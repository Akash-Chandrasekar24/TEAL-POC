"""Explicit approval workflow for model-linked native SolidWorks drawing sheets.

  python scripts/run_sheet.py schema
  python scripts/run_sheet.py check sheet-plan.json
  python scripts/run_sheet.py start sheet-plan.json
  python scripts/run_sheet.py approve JOB_ID
  python scripts/run_sheet.py execute JOB_ID --solidworks
  python scripts/run_sheet.py accept JOB_ID
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import config, trace
from app.assembly.jobs import write_json
from app.drawing.sheets import SheetPlan, sha256, validate_sheet

JOBS = ROOT / "storage/sheets/jobs"


def approved_plan(folder):
    state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
    approved = SheetPlan.model_validate_json((folder / "approved.json").read_text(encoding="utf-8"))
    current = SheetPlan.model_validate_json((folder / "plan.json").read_text(encoding="utf-8"))
    if approved.digest() != current.digest() or current.digest() != state.get("approved_hash"):
        raise ValueError("Sheet plan changed after approval; create a new reviewed job")
    blockers = validate_sheet(approved)
    if blockers:
        raise ValueError("; ".join(blockers))
    return approved


async def execute(folder):
    from app.agent.execution import cad_execution
    from app.agent.orchestrator import Orchestrator
    from app.mcp_client.solidworks import SolidWorksClient
    plan = approved_plan(folder)
    state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
    if state["status"] not in {"approved", "failed", "runtime_unavailable"}:
        raise ValueError("Approve the drawing plan before execution")
    attempt = uuid.uuid4().hex[:12]
    output = folder / "runs" / attempt
    state.update(status="building", attempts=[*state.get("attempts", []), attempt])
    write_json(folder / "state.json", state)
    with trace.run(attempt, folder / "traces" / attempt, save_files=config.TRACE_FILES, workflow="drawing_sheet"):
        try:
            with cad_execution(folder, "drawing_sheet", plan.digest()):
                async with SolidWorksClient() as sw:
                    ready = await sw.ensure_ready()
                    if not ready.ok:
                        raise ValueError(ready.text)
                    report = await Orchestrator().build_sheet(sw, plan, output, plan.digest())
        except Exception as ex:
            report = {"plan_hash": plan.digest(), "status": "runtime_unavailable", "files": {}, "checks": [],
                      "errors": [f"{type(ex).__name__}: {ex}"], "accuracy_verified": False}
            write_json(output / "report.json", report)
    state.update(status=report["status"], report=report, report_hash=sha_json(report),
                 output_hashes={k: sha256(p) for k, p in report["files"].items()})
    write_json(folder / "state.json", state)
    return state


def sha_json(data):
    from app.drawing.model import fingerprint
    return fingerprint(data)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("schema")
    for command in ("check", "start"):
        sub.add_parser(command).add_argument("file", type=Path)
    for command in ("approve", "execute", "status", "accept"):
        child = sub.add_parser(command)
        child.add_argument("job")
        if command == "execute":
            child.add_argument("--solidworks", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "schema":
            result = SheetPlan.model_json_schema()
        elif args.cmd in {"check", "start"}:
            plan = SheetPlan.model_validate_json(args.file.read_text(encoding="utf-8"))
            blockers = validate_sheet(plan)
            result = {"status": "needs_information" if blockers else "awaiting_approval", "blockers": blockers,
                      "plan_hash": plan.digest(), "accuracy_verified": False, "attempts": []}
            if args.cmd == "start":
                jid = uuid.uuid4().hex[:12]
                folder = JOBS / jid
                folder.mkdir(parents=True, exist_ok=False)
                write_json(folder / "plan.json", plan.model_dump())
                write_json(folder / "state.json", result)
                result = {"job": jid, **result}
        else:
            if not re.fullmatch(r"[0-9a-f]{12}", args.job):
                raise ValueError("Invalid sheet job ID")
            folder = JOBS / args.job
            result = json.loads((folder / "state.json").read_text(encoding="utf-8"))
            if args.cmd == "approve":
                if result["status"] not in {"awaiting_approval", "needs_information"}:
                    raise ValueError("Only an unapproved drawing plan can be approved")
                plan = SheetPlan.model_validate_json((folder / "plan.json").read_text(encoding="utf-8"))
                blockers = validate_sheet(plan)
                if blockers:
                    raise ValueError("; ".join(blockers))
                write_json(folder / "approved.json", plan.model_dump())
                result.update(status="approved", approved_hash=plan.digest(), blockers=[])
                write_json(folder / "state.json", result)
            elif args.cmd == "execute":
                if not args.solidworks:
                    raise ValueError("Native sheet execution requires licensed SolidWorks and --solidworks")
                lock = JOBS / "execution.lock"
                with lock.open("x", encoding="utf-8") as file:
                    file.write(args.job)
                try:
                    result = asyncio.run(execute(folder))
                finally:
                    lock.unlink()
            elif args.cmd == "accept":
                approved_plan(folder)
                if result["status"] != "awaiting_review" or sha_json(result["report"]) != result["report_hash"]:
                    raise ValueError("Only an unchanged checked drawing can be accepted after source/visual review")
                if any(sha256(result["report"]["files"][k]) != digest for k, digest in result["output_hashes"].items()):
                    raise ValueError("A drawing output changed after verification")
                result["status"] = "accepted"
                write_json(folder / "state.json", result)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 2 if result.get("status") in {"needs_information", "failed", "runtime_unavailable"} else 0
    except (ValueError, KeyError, OSError) as ex:
        print(f"Drawing sheet: {ex}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
