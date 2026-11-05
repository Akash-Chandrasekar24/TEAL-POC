"""Offline assembly planning/approval; execution requires explicit --solidworks.

  python scripts/run_assembly.py schema
  python scripts/run_assembly.py import-model pump.SLDPRT
  python scripts/run_assembly.py check assembly.json
  python scripts/run_assembly.py start assembly.json
  python scripts/run_assembly.py approve JOB_ID
  python scripts/run_assembly.py execute JOB_ID --solidworks  # licensed runtime
  python scripts/run_assembly.py status JOB_ID
  python scripts/run_assembly.py accept JOB_ID               # human review
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.assembly.assets import import_model
from app.assembly.jobs import AssemblyJob, write_json
from app.assembly.model import AssemblyPlan, validate_plan

ASSETS = ROOT / "storage/assembly/models"
JOBS = ROOT / "storage/assembly/jobs"


async def execute(job):
    # Import only after explicit runtime opt-in. No CLI planning command attaches
    # to COM, spawns the native server or launches SolidWorks.
    from app import config, trace
    from app.agent.orchestrator import Orchestrator
    from app.agent.execution import cad_execution
    from app.mcp_client.solidworks import SolidWorksClient
    plan = job.approved_plan()
    output = job.begin()
    # Keep traces outside the output directory: the unchanged runner requires
    # that directory to be empty before pinning inputs and creating CAD files.
    with trace.run(job.folder.name, job.folder / "traces" / output.name,
                   save_files=config.TRACE_FILES, workflow="assembly_execution"):
        try:
            with cad_execution(job.folder, "assembly", plan.digest()):
                async with SolidWorksClient() as sw:
                    ready = await sw.ensure_ready()
                    if not ready.ok:
                        raise RuntimeError(ready.text)
                    report = await Orchestrator().build_assembly(sw, plan, ASSETS, output, plan.digest())
        except Exception as ex:
            report = {"plan_hash": plan.digest(), "status": "runtime_unavailable", "files": {}, "checks": [],
                      "errors": [f"{type(ex).__name__}: {ex}"]}
            write_json(output / "report.json", report)
    return job.finish(report)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("schema")
    sub.add_parser("import-model").add_argument("file", type=Path)
    for cmd in ("check", "start"):
        sub.add_parser(cmd).add_argument("file", type=Path)
    for cmd in ("approve", "execute", "status", "accept"):
        child = sub.add_parser(cmd)
        child.add_argument("job", help="12-character job ID")
        if cmd == "execute":
            child.add_argument("--solidworks", action="store_true", help="Explicitly start/use licensed SolidWorks")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "schema":
            result = AssemblyPlan.model_json_schema()
        elif args.cmd == "import-model":
            result = import_model(args.file.name, args.file.read_bytes(), ASSETS)
        elif args.cmd in ("check", "start"):
            plan = AssemblyPlan.model_validate_json(args.file.read_text(encoding="utf-8"))
            if args.cmd == "check":
                blockers = validate_plan(plan, ASSETS)
                result = {"status": "needs_information" if blockers else "ready_for_approval", "blockers": blockers, "plan_hash": plan.digest()}
            else:
                jid = uuid.uuid4().hex[:12]
                result = {"job": jid, **AssemblyJob(JOBS / jid, ASSETS).create(plan)}
        else:
            import re
            if not re.fullmatch(r"[0-9a-f]{12}", args.job):
                raise ValueError("Invalid assembly job ID")
            job = AssemblyJob(JOBS / args.job, ASSETS)
            if args.cmd == "execute":
                if not args.solidworks:
                    raise ValueError("Execution needs licensed SolidWorks and --solidworks. Offline approval remains saved.")
                # One assembly CLI writer. Existing documents are checked again
                # by the runtime; this lock never causes them to be closed.
                lock = JOBS / "execution.lock"
                with lock.open("x", encoding="utf-8") as file:
                    file.write(args.job)
                try:
                    result = asyncio.run(execute(job))
                finally:
                    lock.unlink()
            else:
                result = job.state if args.cmd == "status" else getattr(job, args.cmd)()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 2 if result.get("status") in ("needs_information", "failed", "runtime_unavailable") else 0
    except (ValueError, OSError) as ex:
        print(f"Assembly: {ex}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
