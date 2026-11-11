"""Python wrapper around the SolidWorks MCP adapter (third_party/.../adapters/claude/server.py).

Rules:
- EXECUTION_EXE_PATH is ALWAYS passed to the adapter, and must point at our own build, never inside
  third_party/ and never a copy of the shipped exe (the adapter auto-starts that exe when the
  execution server is down, so an unset path would silently run the shipped one).
- Every call is logged (build/logs/mcp_calls.jsonl) and has a timeout.
- A tool-level failure (SolidWorks said no) is RETURNED as a ToolResult with ok=False, so the
  orchestrator can hand the exact text to the fixer. Transport problems and timeouts are RAISED.
- Secrets (anything with KEY / TOKEN / SECRET / PASSWORD in its name) are NOT passed to the adapter.
"""
from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import os
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app import config
from .generated_tools import TOOL_KIND, ToolsMixin
from .backend import require_source, check_tools, validate_graph, build_check

# Seconds. Anything not listed gets DEFAULT_TIMEOUT.
DEFAULT_TIMEOUT = 60.0
TOOL_TIMEOUTS: dict[str, float] = {
    "ensure_ready": 240.0,       # may cold-start SolidWorks
    "open_new_part": 90.0,
    "submit_feature_graph": 240.0,
    "analyze_model": 120.0,
    "export_document": 180.0,
    "save_document": 120.0,
    "get_recipe": 30.0,
    "inspect_assembly": 200.0,
    "inspect_drawing": 150.0,
    "ext_sweep": 250.0,
    "ext_clear_selection": 60.0,
    "ext_feature_count": 60.0,
    "ext_delete_after": 120.0,
    "ext_drill": 250.0,
    "ext_bbox": 120.0,
    "ext_command_in_progress": 60.0,
    "ext_shell": 250.0,
    "ext_angled_plane": 250.0,
    "ext_sketch_extrude": 250.0,
    "ext_revolve": 250.0,
    "ext_annotate": 250.0,
}
_LOG_ARG_CHARS = 1500
_LOG_RESULT_CHARS = 600


class UnsafePathError(RuntimeError):
    """A path we refuse to use (inside third_party/, missing, or the shipped exe)."""


class McpToolTimeout(TimeoutError):
    """A tool call did not answer in time. The adapter/SolidWorks may be stuck."""


@dataclass
class ToolResult:
    tool: str
    text: str
    is_error: bool          # MCP-level error flag (the adapter raised)
    seconds: float
    args: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """True unless the MCP call errored or the text says the operation FAILED."""
        if self.is_error:
            return False
        head = self.text.lstrip()[:40].upper()
        return not (head.startswith("FAILED") or head.startswith("CAD OPERATION FAILED"))

    def json(self) -> Any:
        """Parse the whole text as JSON. Raises ValueError if it is not (most tools return a
        'COMPLETED | ... | features=[...]' line instead: use features / analysis_json / numbers)."""
        return json.loads(self.text)

    @property
    def features(self) -> list[str]:
        """The entries of the 'features=[...]' part of a COMPLETED line ([] if there is none)."""
        idx = self.text.find("features=")
        if idx < 0:
            return []
        rest = self.text[idx + len("features="):]
        cut = rest.find(" | result_geometry=")
        if cut >= 0:
            rest = rest[:cut]
        try:
            value = ast.literal_eval(rest.strip())
        except (ValueError, SyntaxError):
            return []
        return [str(v) for v in value] if isinstance(value, list) else []

    def analysis_json(self) -> Any:
        """For analyze_model faces/edges/features/...: the JSON object inside features=['{...}']."""
        for entry in self.features:
            if entry.lstrip().startswith(("{", "[")):
                return json.loads(entry)
        raise ValueError("no JSON entry in the result")

    def numbers(self) -> dict[str, float]:
        """For analyze_model mass_properties / geometry: {'volume': 5.9e-05, 'faces': 11.0, ...}."""
        out: dict[str, float] = {}
        for entry in self.features:
            key, sep, val = entry.partition("=")
            if sep:
                try:
                    out[key.strip()] = float(val)
                except ValueError:
                    pass
        return out


# --------------------------------------------------------------------------- path safety
def _is_inside(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
        return True
    except ValueError:
        return False


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_executable_path(exe: Path | str) -> Path:
    """Return the resolved exe path, or raise UnsafePathError. Never starts anything."""
    p = Path(exe)
    if not str(exe).strip():
        raise UnsafePathError("EXECUTION_EXE_PATH is empty")
    rp = p.resolve()
    if _is_inside(rp, config.THIRD_PARTY_DIR) or "third_party" in {part.lower() for part in rp.parts}:
        raise UnsafePathError(f"refusing exe path inside third_party/: {rp}")
    if rp.suffix.lower() != ".exe":
        raise UnsafePathError(f"exe path does not end in .exe: {rp}")
    if not rp.is_file():
        raise UnsafePathError(f"exe not found (build it first: scripts\build_execution_layer.ps1): {rp}")
    shipped = config.SHIPPED_EXE_PATH
    if shipped.is_file() and _sha256(rp) == _sha256(shipped):
        raise UnsafePathError(f"this is a copy of the shipped exe; refusing: {rp}")
    return rp


def check_output_path(path: Path | str) -> Path:
    """Output files must not go inside third_party/. Creates the parent folder."""
    rp = Path(path).resolve()
    if _is_inside(rp, config.THIRD_PARTY_DIR):
        raise UnsafePathError(f"refusing to write inside third_party/: {rp}")
    rp.parent.mkdir(parents=True, exist_ok=True)
    return rp


def _clean_env(extra: dict[str, str]) -> dict[str, str]:
    """Copy of os.environ without secrets, plus our variables."""
    bad = ("KEY", "TOKEN", "SECRET", "PASSWORD")
    env = {k: v for k, v in os.environ.items() if not any(b in k.upper() for b in bad)}
    env.update(extra)
    return env


# --------------------------------------------------------------------------- the client
BRIDGE_HEALTH = "http://localhost:5000/health"


def keep_bridge_up(exe: Path, wait_s: float = 60.0) -> None:
    """Start OUR execution bridge outside the MCP session's Windows job object, so it outlives the session and the next part reuses it.
    The MCP client puts the adapter in a kill-on-close job: the bridge the adapter spawned died with every part (~15 s each to restart)."""
    import subprocess
    import httpx
    try:
        if httpx.get(BRIDGE_HEALTH, timeout=2).status_code == 200:
            return
    except httpx.HTTPError:
        pass
    flags = 0x00000008 | 0x08000000                                         # DETACHED_PROCESS | CREATE_NO_WINDOW
    try:
        subprocess.Popen([str(exe)], cwd=str(exe.parent), creationflags=flags | 0x01000000, close_fds=True)   # | CREATE_BREAKAWAY_FROM_JOB
    except OSError:                                                         # our own process is in a job that forbids breakaway
        subprocess.Popen([str(exe)], cwd=str(exe.parent), creationflags=flags, close_fds=True)
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        try:
            if httpx.get(BRIDGE_HEALTH, timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)


class SolidWorksClient(ToolsMixin):
    """Async context manager. All 47 adapter tools are typed methods (generated: see ToolsMixin); the hand-written
    methods below override a few of them with friendlier arguments. Usage:

        async with SolidWorksClient() as sw:
            await sw.ensure_ready()
            res = await sw.submit_feature_graph(graph_dict)
            if not res.ok: ...
    """

    def __init__(self, exe_path: Path | str | None = None, log_path: Path | None = None):
        # Validate NOW so a bad path fails before anything is launched.
        self.exe_path = check_executable_path(exe_path if exe_path is not None else config.EXECUTION_EXE_PATH)
        require_source()
        build = build_check(self.exe_path)
        if not build["ok"]:
            raise UnsafePathError("; ".join(build["reasons"]))
        self.server_path = config.MCP_SERVER_PATH.resolve()
        if not self.server_path.is_file():
            raise UnsafePathError(f"MCP adapter not found: {self.server_path}")
        self.log_path = Path(log_path) if log_path else config.LOG_DIR / "mcp_calls.jsonl"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self.tool_names: list[str] = []

    # ---- lifecycle
    async def __aenter__(self) -> "SolidWorksClient":
        params = StdioServerParameters(
            command=str(config.PYTHON_EXE),
            args=["-B", str(config.MCP_HOST_PATH)],
            env=_clean_env({
                "EXECUTION_EXE_PATH": str(self.exe_path),   # ALWAYS set (see module docstring)
                "PYTHONDONTWRITEBYTECODE": "1",
                "SOLIDWORKS_MCP_REPO": str(config.SOLIDWORKS_REPO_DIR.resolve()),
                "SOLIDWORKS_ADAPTER_LOG": str(self.log_path.with_name("adapter.log").resolve()),
                "HTTP_TIMEOUT": str(config.SW_HTTP_TIMEOUT),
                "ENSURE_TIMEOUT": str(config.SW_ENSURE_TIMEOUT),
            }),
            cwd=str(config.BUILD_DIR),  # not the repo folder
        )
        config.BUILD_DIR.mkdir(parents=True, exist_ok=True)
        keep_bridge_up(self.exe_path)                                           # best effort: the adapter starts it itself if this did not
        stderr_path = self.log_path.with_name("adapter_stderr.log")
        stack = AsyncExitStack()
        try:
            errlog = stack.enter_context(open(stderr_path, "a", encoding="utf-8", errors="replace"))
            read, write = await stack.enter_async_context(stdio_client(params, errlog=errlog))
            session = await stack.enter_async_context(ClientSession(read, write))
            await asyncio.wait_for(session.initialize(), timeout=60)
            listed = (await asyncio.wait_for(session.list_tools(), timeout=60)).tools
            compatibility = check_tools(listed)
            if not compatibility["ok"]:
                raise RuntimeError(f"SolidWorks MCP tool contract mismatch: {compatibility}")
            self.tool_names = [t.name for t in listed]
        except BaseException:
            await stack.aclose()
            raise
        self._stack, self._session = stack, session
        self._log({"event": "connected", "exe": str(self.exe_path), "tools": len(self.tool_names)})
        return self

    async def __aexit__(self, *exc) -> None:
        if self.__dict__.get("_fast") and self._session is not None:            # never leave the SolidWorks window frozen
            try:
                await self.call("ext_command_in_progress", {"on": False})
            except Exception:                                                  # noqa: BLE001 - closing anyway
                pass
            self._fast = False
        if self._stack is not None:
            await self._stack.aclose()
        self._stack = self._session = None
        self._log({"event": "closed"})

    # ---- core call
    async def call(self, tool: str, args: dict[str, Any] | None = None,
                   timeout: float | None = None) -> ToolResult:
        if self._session is None:
            raise RuntimeError("SolidWorksClient is not connected (use 'async with')")
        if self.tool_names and tool not in self.tool_names:
            raise ValueError(f"unknown tool: {tool}")
        args = args or {}
        # Face / edge listings take 10-60 s on a real part and were asked twice at the same model state. Cached until any
        # other tool is called (any other tool may change the model); a listing that failed is never cached.
        listing = tool == "analyze_model" and set(args) == {"analysis_type"} and args["analysis_type"] in ("faces", "edges")
        cache = self.__dict__.setdefault("_listing_cache", {})
        if listing and args["analysis_type"] in cache:
            return cache[args["analysis_type"]]
        if tool not in ("analyze_model", "verify_state"):
            cache.clear()
        limit = timeout if timeout is not None else TOOL_TIMEOUTS.get(tool, DEFAULT_TIMEOUT)
        if listing and timeout is None:
            limit = max(limit, 420.0)        # a whole-part face / edge list grows with the part: a 42-tooth gear (~850 faces) took 37-120+ s
        started = time.monotonic()
        try:
            raw = await asyncio.wait_for(
                self._session.call_tool(tool, args, read_timeout_seconds=limit),
                timeout=limit + 5,
            )
        except (asyncio.TimeoutError, TimeoutError) as ex:
            self._log({"event": "call", "tool": tool, "args": _short(args), "seconds": round(time.monotonic() - started, 3),
                       "outcome": "TIMEOUT", "limit": limit})
            raise McpToolTimeout(f"{tool} did not answer within {limit:.0f}s") from ex
        except Exception as ex:  # transport / protocol failure
            self._log({"event": "call", "tool": tool, "args": _short(args), "seconds": round(time.monotonic() - started, 3),
                       "outcome": "TRANSPORT_ERROR", "error": repr(ex)[:500]})
            raise
        text = "\n".join(getattr(c, "text", str(c)) for c in raw.content)
        result = ToolResult(tool=tool, text=text, is_error=bool(getattr(raw, "is_error", False)),
                            seconds=time.monotonic() - started, args=args)
        self._log({"event": "call", "tool": tool, "args": _short(args), "seconds": round(result.seconds, 3),
                   "outcome": "OK" if result.ok else "FAILED", "is_error": result.is_error,
                   "result_chars": len(text), "result_head": text[:_LOG_RESULT_CHARS]})
        if listing and result.ok:
            cache[args["analysis_type"]] = result
        if tool == "ensure_ready" and result.ok and not self.__dict__.get("_fast") and "ext_command_in_progress" in self.tool_names:
            on = await self.call("ext_command_in_progress", {"on": True})        # COM ~30x faster while this session drives SolidWorks
            self._fast = on.ok
        return result

    # ---- typed helpers (names/params follow the adapter's server.py)
    async def ensure_ready(self) -> ToolResult:
        return await self.call("ensure_ready")

    async def inspect_assembly(self, inspection_type: str = "verification") -> ToolResult:
        if inspection_type not in ("workspace", "verification"):
            raise ValueError("unknown assembly inspection type")
        return await self.call("inspect_assembly", {"inspection_type": inspection_type})

    async def geometry(self, tool: str, spec: dict) -> ToolResult:
        """One of the application geometry tools (ext_sweep, ext_shell, ext_angled_plane, ext_sketch_extrude); spec in model metres."""
        if tool not in ("ext_sweep", "ext_shell", "ext_angled_plane", "ext_sketch_extrude", "ext_drill", "ext_revolve", "ext_annotate"):
            raise ValueError(f"not a geometry tool: {tool}")
        return await self.call(tool, {"spec": spec})

    async def open_new_part(self) -> ToolResult:
        return await self.call("open_new_part")

    async def close_document(self, save: bool = False, close_all: bool = False) -> ToolResult:
        return await self.call("close_document", {"save": save, "close_all": close_all})

    async def verify_state(self) -> ToolResult:
        return await self.call("verify_state")

    async def get_recipe(self, section: str = "index", profile: str = "all") -> ToolResult:
        return await self.call("get_recipe", {"section": section, "profile": profile})

    async def submit_feature_graph(self, graph: dict | str, fresh_document: bool = True) -> ToolResult:
        parsed = json.loads(graph) if isinstance(graph, str) else graph
        errors = validate_graph(parsed)
        if errors:
            raise ValueError("SolidWorks feature graph is invalid: " + "; ".join(errors))
        text = graph if isinstance(graph, str) else json.dumps(graph)
        return await self.call("submit_feature_graph", {"graph": text, "fresh_document": fresh_document})

    async def analyze_model(self, analysis_type: str, *, near: list[float] | None = None,
                            k: int = 0, axis: list[float] | None = None, name: str = "") -> ToolResult:
        args: dict[str, Any] = {"analysis_type": analysis_type}
        if name:
            args["name"] = name
        if near is not None:
            args["near"] = json.dumps(near)
        if k:
            args["k"] = k
        if axis is not None:
            args["axis"] = json.dumps(axis)
        return await self.call("analyze_model", args)

    async def save_document(self, file_path: Path | str) -> ToolResult:
        return await self.call("save_document", {"file_path": str(check_output_path(file_path))})

    async def inspect_drawing(self) -> ToolResult:
        return await self.call("inspect_drawing")

    async def export_document(self, fmt: str, file_path: Path | str) -> ToolResult:
        if fmt not in ("STEP", "IGES", "STL", "PDF", "DWG", "DXF"):
            raise ValueError(f"bad export format: {fmt}")
        return await self.call("export_document", {"format": fmt, "file_path": str(check_output_path(file_path))})

    async def set_part_material(self, material_name: str, library: str = "SolidWorks Materials") -> ToolResult:
        return await self.call("set_part_material", {"material_name": material_name, "library": library})

    # ---- logging
    def _log(self, entry: dict[str, Any]) -> None:
        entry = {"ts": datetime.now().isoformat(timespec="milliseconds"), **entry}
        try:
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass  # logging must never break a call


def _short(args: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in args.items():
        s = v if isinstance(v, str) else json.dumps(v)
        out[k] = s if len(s) <= _LOG_ARG_CHARS else s[:_LOG_ARG_CHARS] + f"...[+{len(s) - _LOG_ARG_CHARS} chars]"
    return out
