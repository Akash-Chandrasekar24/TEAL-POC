"""The REAL build step the orchestrator calls: the runner and the finish in ONE SolidWorks session (so the part that was built is the part that is saved)."""
from __future__ import annotations

import asyncio
import os
from dataclasses import asdict
from pathlib import Path

from app.finish import finish_part, safe_name
from app.mcp_client import SolidWorksClient
from app.menu import parse_reply
from app.runner import Runner
from app.verify import RunLog


def keep_open_docs() -> tuple[str, ...]:
    """Documents that may stay open besides the scratch part (comma separated in SW_KEEP_OPEN_DOCS; default none)."""
    return tuple(x.strip() for x in os.getenv("SW_KEEP_OPEN_DOCS", "").split(",") if x.strip())


def apply_expected_final(finish, final: dict, expected) -> None:
    """Add the human's expected-final checks (bounding box, body count) to the finish checks: a mismatch makes the finish fail, so the job stops for a human."""
    if finish is None:
        return
    checks = []
    if expected is not None:
        checks += final.get("expected_checks") or expected.check(final)
    pc = final.get("predicted_check")
    if pc is not None and pc.get("mode") == "stop":                      # warn mode only reports (a warning in the report); stop mode blocks the finish
        checks.append({"name": pc["name"], "ok": pc["ok"], "detail": pc["detail"]})
    finish.checks.extend(checks)
    finish.ok = bool(finish.ok) and all(c["ok"] for c in checks)


async def _build(approved: dict, job_dir: Path) -> dict:
    bl = parse_reply(approved)
    log = RunLog(f"job_{job_dir.name}")
    async with SolidWorksClient() as sw:
        report = await Runner(sw, expected_docs=keep_open_docs(), log=log).run(bl)
        finish = None
        if report.ok:
            finish = await finish_part(sw, job_dir / "output", safe_name(bl.part_name or job_dir.name), bl.unit, expected_bbox_mm=report.final.get("bbox_mm"))
            apply_expected_final(finish, report.final, bl.expected_final)
    log.write_summary(f"job {job_dir.name}", [f"ok={report.ok}", f"part left open in SolidWorks: {report.document}"])
    return {"report": asdict(report), "finish": asdict(finish) if finish else None, "log_dir": str(log.dir)}


def solidworks_build(approved: dict, job_dir: Path) -> dict:
    """(approved list, job dir) -> {"report", "finish"} as plain dicts (what the orchestrator stores)."""
    return asyncio.run(_build(approved, Path(job_dir)))
