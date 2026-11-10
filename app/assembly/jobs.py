"""Persistent assembly approval/review, independent of LLM and runtime access."""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path

from .model import AssemblyPlan, digest, validate_plan


# Windows refuses to replace a file while another thread has it open (and refuses the open during the replace): the server reads
# and writes these files from several threads, so the replace and read_json take one lock. Measured: a build ended with
# "PermissionError: Access is denied" on its batch file while the page was polling it.
_FILE_LOCK = threading.Lock()


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    for attempt in range(40):                       # another PROCESS (antivirus, indexer) may still hold it a moment
        try:
            with _FILE_LOCK:
                os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 39:
                raise
            time.sleep(0.05)


def read_json(path: Path):
    """Read a file written by write_json without racing its replace."""
    with _FILE_LOCK:
        return json.loads(path.read_text(encoding="utf-8"))


class AssemblyJob:
    def __init__(self, folder: Path, assets: Path):
        self.folder, self.assets = folder, assets

    @property
    def state(self):
        return json.loads((self.folder / "state.json").read_text(encoding="utf-8"))

    @property
    def plan(self):
        return AssemblyPlan.model_validate_json((self.folder / "plan.json").read_text(encoding="utf-8"))

    def save(self, state):
        write_json(self.folder / "state.json", state)
        return state

    def create(self, plan: AssemblyPlan):
        self.folder.mkdir(parents=True, exist_ok=False)
        write_json(self.folder / "plan.json", plan.model_dump())
        blockers = validate_plan(plan, self.assets)
        return self.save({"status": "needs_information" if blockers else "awaiting_approval", "plan_hash": plan.digest(),
                          "blockers": blockers, "approved_hash": None, "attempts": [], "events": ["Assembly draft saved"]})

    def approve(self):
        state, plan = self.state, self.plan
        if state["status"] not in ("needs_information", "awaiting_approval"):
            raise ValueError("Only an unapproved draft can be approved; create a new job to change an approved plan")
        blockers = validate_plan(plan, self.assets)
        if blockers:
            self.save({**state, "status": "needs_information", "blockers": blockers})
            raise ValueError("Resolve assembly blockers before approval: " + "; ".join(blockers))
        write_json(self.folder / "approved.json", plan.model_dump())
        return self.save({**state, "status": "approved", "blockers": [], "plan_hash": plan.digest(),
                          "approved_hash": plan.digest(), "events": [*state["events"], "Assembly snapshot explicitly approved"]})

    def approved_plan(self):
        state, plan = self.state, self.plan
        approved = AssemblyPlan.model_validate_json((self.folder / "approved.json").read_text(encoding="utf-8"))
        if not state.get("approved_hash") or state["approved_hash"] != plan.digest() or approved.digest() != plan.digest():
            raise ValueError("Assembly changed after approval; create and approve a new job")
        blockers = validate_plan(approved, self.assets)
        if blockers:
            raise ValueError("; ".join(blockers))
        return approved

    def begin(self):
        state = self.state
        if state["status"] not in ("approved", "failed", "runtime_unavailable"):
            raise ValueError("Approve the plan before execution; review a completed run before starting another")
        self.approved_plan()
        attempt = uuid.uuid4().hex[:12]
        self.save({**state, "status": "building", "attempts": [*state["attempts"], attempt],
                   "events": [*state["events"], f"Execution explicitly requested: {attempt}"]})
        return self.folder / "runs" / attempt

    def finish(self, report):
        state = self.state
        if state["status"] != "building" or report.get("plan_hash") != state["approved_hash"]:
            raise ValueError("Unexpected assembly execution result")
        status = report.get("status")
        if status not in ("awaiting_review", "failed", "runtime_unavailable"):
            raise ValueError("Invalid assembly report status")
        hashes = {}
        if status == "awaiting_review":
            import hashlib
            for label in ("assembly", "step"):
                path = Path(report["files"][label])
                hashes[label] = hashlib.sha256(path.read_bytes()).hexdigest()
        return self.save({**state, "status": status, "report": report, "report_hash": digest(report), "output_hashes": hashes,
                          "events": [*state["events"], f"Build result: {status}"]})

    def accept(self):
        import hashlib
        state = self.state
        self.approved_plan()
        if state["status"] != "awaiting_review" or digest(state["report"]) != state["report_hash"]:
            raise ValueError("Only an unchanged successful build can be accepted after visual/drawing review")
        for label, sha in state["output_hashes"].items():
            path = Path(state["report"]["files"][label])
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != sha:
                raise ValueError("Assembly output changed after verification")
        for model in self.plan.models:
            path = Path(state["report"]["files"]["components"]) / f"{model.sha256}.SLDPRT"
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != model.sha256:
                raise ValueError("Bundled component changed after verification")
        return self.save({**state, "status": "accepted", "events": [*state["events"], "Human accepted assembly after drawing/visual review"]})
