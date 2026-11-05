"""Set up the pinned SolidPilot backend without replacing our custom features.

  python scripts/setup_solidworks_backend.py --fetch       # exact locked source
  python scripts/setup_solidworks_backend.py --build       # build our own executable
  python scripts/setup_solidworks_backend.py --probe       # MCP handshake, no CAD edits/start
  python scripts/setup_solidworks_backend.py --ready       # attach/start SolidWorks, no document edits
  python scripts/setup_solidworks_backend.py --catalog storage/solidworks-tools.json
Without flags, checks source, compiler and executable presence only.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from contextlib import AsyncExitStack
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import config
from app.mcp_client.backend import catalog, check_tools, require_source, build_check, custom_graph_checks
from app.mcp_client.solidworks import SolidWorksClient, _clean_env


def fetch_source():
    lock = json.loads(config.SOLIDWORKS_LOCK_PATH.read_text(encoding="utf-8"))
    repo = config.SOLIDWORKS_REPO_DIR
    if repo.exists():
        require_source()  # Never reset or pull over an existing checkout.
        print(f"Pinned source already present: {lock['revision']}")
        return
    repo.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "--no-checkout", lock["url"], str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "checkout", "--detach", lock["revision"]], check=True)
    require_source()


async def probe():
    """Discovery only: starting the Python adapter does not start native execution."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    require_source()
    config.BUILD_DIR.mkdir(parents=True, exist_ok=True)
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    params = StdioServerParameters(command=str(config.PYTHON_EXE), args=["-B", str(config.MCP_HOST_PATH)],
                                   cwd=str(config.BUILD_DIR), env=_clean_env({
                                       "EXECUTION_EXE_PATH": str(config.EXECUTION_EXE_PATH.resolve()),
                                       "SOLIDWORKS_MCP_REPO": str(config.SOLIDWORKS_REPO_DIR.resolve()),
                                       "SOLIDWORKS_ADAPTER_LOG": str(config.LOG_DIR / "adapter.log"),
                                       "HTTP_TIMEOUT": str(config.SW_HTTP_TIMEOUT),
                                       "ENSURE_TIMEOUT": str(config.SW_ENSURE_TIMEOUT),
                                       "PYTHONDONTWRITEBYTECODE": "1"}))
    async with AsyncExitStack() as stack:
        err = stack.enter_context(open(config.LOG_DIR / "adapter_probe_stderr.log", "a", encoding="utf-8"))
        reader, writer = await stack.enter_async_context(stdio_client(params, errlog=err))
        session = await stack.enter_async_context(ClientSession(reader, writer))
        await asyncio.wait_for(session.initialize(), 60)
        tools = (await asyncio.wait_for(session.list_tools(), 60)).tools
        report = catalog(tools)
        report["compatibility"] = check_tools(tools)
        resource = await asyncio.wait_for(session.read_resource("schema://feature-graph"), 60)
        report["feature_graph_resource"] = bool(resource.contents)
        report["custom_graphs"] = custom_graph_checks()
        return report


async def ready():
    async with SolidWorksClient() as sw:
        response = await sw.ensure_ready()
        return {"ok": response.ok, "result": response.text}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--ready", action="store_true")
    parser.add_argument("--catalog", type=Path)
    args = parser.parse_args()
    if args.fetch:
        fetch_source()
    if args.build:
        subprocess.run([sys.executable, str(ROOT / "scripts/build_execution_layer.py")], check=True)
    report = asyncio.run(probe()) if args.probe else catalog()
    report["execution_exe_present"] = config.EXECUTION_EXE_PATH.is_file()
    report["build"] = build_check()
    if args.ready:
        report["ready"] = asyncio.run(ready())
    if args.catalog:
        args.catalog.parent.mkdir(parents=True, exist_ok=True)
        args.catalog.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"source": report["source"], "native_tools": len(report["native_tools"]),
                      "application_tools": len(report["application_tools"]),
                      "custom_features": len(report["custom_features"]), "execution_exe_present": report["execution_exe_present"],
                      **{k: report[k] for k in ("build", "compatibility", "custom_graphs", "ready", "feature_graph_resource") if k in report}}, indent=2))
    checks = (report["source"], report.get("compatibility", {"ok": True}), report.get("custom_graphs", {"ok": True}), report.get("ready", {"ok": True}))
    return 0 if all(c["ok"] for c in checks) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as ex:
        print(f"SolidWorks backend setup failed: {ex}", file=sys.stderr)
        raise SystemExit(1)
