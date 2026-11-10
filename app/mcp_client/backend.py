"""Pinned upstream dependency, schema compatibility and native/custom catalog.

No SolidWorks process is started by the source checks or catalog functions.
Upstream files are read only; our integration and custom features live separately.
"""
from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import subprocess
from functools import lru_cache
from pathlib import Path

from app import config
from .generated_tools import TOOL_KIND, ToolsMixin


class BackendUnavailable(RuntimeError):
    pass


APPLICATION_TOOL_PARAMETERS = {"inspect_assembly": {"inspection_type"}, "inspect_drawing": set()}


def extension_hashes() -> dict[str, str]:
    """Bind owned native extensions to the executable, as well as upstream."""
    return {str(p.relative_to(config.PROJECT_ROOT)).replace("\\", "/"): source_hash(p)
            for p in sorted((config.PROJECT_ROOT / "extensions/solidworks").glob("*.cs"))}


def source_hash(path: Path) -> str:
    # Git may check out LF or CRLF on Windows; neither changes the source contract.
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def source_check(repo: Path | None = None, lock: dict | None = None) -> dict:
    repo = Path(repo or config.SOLIDWORKS_REPO_DIR).resolve()
    lock = lock if lock is not None else json.loads(config.SOLIDWORKS_LOCK_PATH.read_text(encoding="utf-8"))
    reasons = []
    if lock.get("version") != 1:
        reasons.append("unsupported SolidWorks dependency lock version")
    for name, digest in lock.get("files", {}).items():
        path = (repo / name).resolve()
        if not path.is_relative_to(repo) or not path.is_file():
            reasons.append(f"missing upstream source: {name}")
        elif source_hash(path) != digest:
            reasons.append(f"upstream source differs from the locked revision: {name}")
    if not lock.get("files"):
        reasons.append("dependency lock has no source checksums")
    revision = None
    if (repo / ".git").exists():
        try:
            revision = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
            if revision != lock["revision"]:
                reasons.append(f"upstream checkout is {revision}, expected {lock['revision']}")
            dirty = subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=no"], text=True)
            if dirty.strip():
                reasons.append("upstream checkout has modified tracked files; keep customizations in our application")
        except (OSError, subprocess.CalledProcessError) as ex:
            reasons.append(f"cannot verify upstream checkout: {ex}")
    return {"ok": not reasons, "repo": str(repo), "revision": revision or lock.get("revision"),
            "url": lock.get("url"), "license": lock.get("license"), "reasons": reasons}


def require_source() -> dict:
    report = source_check()
    if not report["ok"]:
        raise BackendUnavailable("; ".join(report["reasons"]) + ". Run scripts/setup_solidworks_backend.py --fetch.")
    return report


def build_check(exe: Path | None = None) -> dict:
    exe = Path(exe or config.EXECUTION_EXE_PATH)
    manifest = exe.parent / "build-manifest.json"
    reasons = []
    revision = None
    if not exe.is_file():
        reasons.append(f"execution executable is missing: {exe}")
    if not manifest.is_file():
        reasons.append("execution build-manifest.json is missing; build with scripts/build_execution_layer.py")
    else:
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            lock = json.loads(config.SOLIDWORKS_LOCK_PATH.read_text(encoding="utf-8"))
            revision = data.get("source_revision")
            if data.get("version") != 1 or revision != lock["revision"]:
                reasons.append("execution build does not match the pinned upstream revision")
            if data.get("extensions", {}) != extension_hashes():
                reasons.append("execution build does not match application extensions; rebuild it")
            if exe.is_file() and hashlib.sha256(exe.read_bytes()).hexdigest() != data.get("executable_sha256"):
                reasons.append("execution executable differs from its build manifest")
        except (ValueError, OSError) as ex:
            reasons.append(f"cannot verify execution build manifest: {ex}")
    return {"ok": not reasons, "executable": str(exe), "revision": revision, "reasons": reasons}


@lru_cache(maxsize=1)
def _validator():
    require_source()
    path = config.SOLIDWORKS_REPO_DIR / "compiler/solidworks/pycompiler/ir_schema.py"
    spec = importlib.util.spec_from_file_location("_solidpilot_ir_schema", path)
    module = importlib.util.module_from_spec(spec)
    # Compile source directly: don't create __pycache__ in the upstream checkout.
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), module.__dict__)
    return module.validate


def validate_graph(graph: dict) -> list[str]:
    """Use upstream's real plan-time IR validator, with zero execution calls."""
    return _validator()(graph)


def check_tools(tools: list) -> dict:
    """Compare generated typed wrappers with real MCP input schemas."""
    actual = {t.name: t for t in tools}
    expected_tools = set(TOOL_KIND) | set(APPLICATION_TOOL_PARAMETERS)
    missing = sorted(expected_tools - actual.keys())
    drift = {}
    for name in set(TOOL_KIND) & actual.keys():
        expected = set(inspect.signature(getattr(ToolsMixin, name)).parameters) - {"self", "timeout"}
        schema = actual[name].input_schema
        received = set(schema.get("properties", {}))
        if expected != received:
            drift[name] = {"wrapper_only": sorted(expected - received), "upstream_only": sorted(received - expected)}
    for name, parameters in APPLICATION_TOOL_PARAMETERS.items():
        if name in actual:
            received = set(actual[name].input_schema.get("properties", {}))
            if received != parameters:
                drift[name] = {"expected": sorted(parameters), "actual": sorted(received)}
    return {"ok": not missing and not drift, "count": len(actual), "missing": missing,
            "additional": sorted(actual.keys() - expected_tools), "parameter_drift": drift}


def catalog(tools: list | None = None) -> dict:
    from app.menu import MENU
    report = source_check()
    native = [{"name": name, "kind": kind, "layer": "upstream_mcp"} for name, kind in TOOL_KIND.items()]
    if tools is not None:
        native = [{"name": t.name, "kind": TOOL_KIND[t.name], "layer": "upstream_mcp",
                   "input_schema": t.input_schema} for t in tools if t.name in TOOL_KIND]
    return {"version": "1", "source": report, "native_tools": native,
            "application_tools": [{"name": "inspect_assembly", "kind": "read", "layer": "application_mcp",
                                   "purpose": "Workspace, rebuild, bounds, constraints and interference verification"},
                                  {"name": "inspect_drawing", "kind": "read", "layer": "application_mcp",
                                   "purpose": "Native sheet, model references, view layout/scale and dimension readback"}],
            "custom_features": [{"name": e.name, "summary": e.summary, "limits": list(e.limits),
                                 "layer": "application", "execution": "upstream_mcp"} for e in MENU.values()],
            "note": "Native tool availability is separate from the validated custom build-list menu. Human approval and feature checks remain required."}


def custom_graph_checks() -> dict:
    """Check our menu examples against upstream IR; low-level routes stay explicit."""
    import copy
    from app.features.base import FeatureRequestError
    from app.menu import MENU, parse_reply
    from app.menu.buildlist import BASE_FEATURES
    from app.runner import prepare
    base = {"id": "F1", "feature": "extrude_profile_boss", "params": copy.deepcopy(MENU["extrude_profile_boss"].example)}
    turned_base = {"id": "F1", "feature": "revolve_base", "params": copy.deepcopy(MENU["revolve_base"].example)}
    cases = []
    for name, entry in MENU.items():
        is_base = name in BASE_FEATURES
        item = {"id": "F1" if is_base else "F2", "feature": name, "params": copy.deepcopy(entry.example)}
        first = turned_base if name in ("turned_axial", "cross_hole", "radial_blades", "turned_keyway") else base
        data = {"list_version": "1", "unit": "mm", "projection": "third", "items": [item] if is_base else [first, item]}
        feature = prepare(parse_reply(data))[-1].feature
        method = getattr(feature, "graph", None)
        if not callable(method):
            cases.append({"feature": name, "route": "composed_or_low_level", "errors": []})
            continue
        try:
            graphs = [method(-1), method(1)] if name == "rib" else [method()]
        except FeatureRequestError:
            cases.append({"feature": name, "route": "low_level_face", "errors": []})
            continue
        errors = [error for graph in graphs for error in validate_graph(graph)]
        cases.append({"feature": name, "route": "feature_graph", "graphs": len(graphs), "errors": errors})
    return {"ok": all(not c["errors"] for c in cases), "graphs_checked": sum(c.get("graphs", 0) for c in cases), "cases": cases}
