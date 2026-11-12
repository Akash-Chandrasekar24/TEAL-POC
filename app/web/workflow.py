"""Durable browser bridge to the existing approval/repair graph.

Only server-owned plans and artifact paths enter execution. Revision and review
hashes bind each click to the evidence the user actually saw.
"""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from contextlib import closing, contextmanager
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from app.agent.graph import Deps, build_graph, new_job_state
from app.agent.orchestrator import Orchestrator
from app.agent.review import edit_preview, export_reviewed, preview_assembly, preview_build, verify_receipt
from app.agent.guards import list_hash
from app.assembly.jobs import write_json
from app.drawing.model import fingerprint
from app.drawing.sheets import sha256

ID = re.compile(r"^[0-9a-f]{12}$")
BOOT = uuid.uuid4().hex


class Workflow:
    def __init__(self, root, plan, *, build=preview_build, exporter=export_reviewed, workers=None, assets=None, assembly_build=preview_assembly,
                 editor=edit_preview):
        self.root, self.plan = Path(root), plan
        self.build, self.exporter, self.workers, self.editor = build, exporter, workers, editor
        self.assets, self.assembly_build = assets, assembly_build

    def folder(self, jid):
        if not isinstance(jid, str) or not ID.fullmatch(jid):
            raise ValueError("Invalid workflow job")
        return self.root / "jobs" / jid

    def load(self, jid):
        return json.loads((self.folder(jid) / "state.json").read_text(encoding="utf-8"))

    def save(self, record):
        write_json(self.folder(record["id"]) / "state.json", record)

    def current(self, record):
        artifact, draft = self.plan(record["result_id"], record["attempt"])
        if fingerprint(artifact) != record["artifact_hash"]:
            raise ValueError("The source or measurement review changed. Review a new plan before continuing.")
        return draft

    def create(self, rid, attempt):
        artifact, draft = self.plan(rid, attempt)
        jid = uuid.uuid4().hex[:12]
        record = {"id": jid, "result_id": rid, "attempt": attempt, "artifact_hash": fingerprint(artifact),
                  "llm": artifact.get("llm") or "domo", "draft": draft, "revision": 0, "kind": "assembly" if "assembly_version" in draft else "part",
                  "phase": "awaiting_approval", "questions": [], "events": [], "boot": BOOT}
        self.save(record)
        write_json(self.root / "results" / f"{rid}.json", {"id": jid})
        return self.public(record)

    def latest(self, rid):
        if not ID.fullmatch(rid):
            raise ValueError("Invalid drawing result")
        index = self.root / "results" / f"{rid}.json"
        return self.status(json.loads(index.read_text())["id"]) if index.is_file() else None

    def status(self, jid):
        return self.public(self.load(jid))

    def public(self, record):
        stale = False
        try:
            self.current(record)
        except (ValueError, OSError):
            stale = True
        phase = record["phase"]
        if phase in {"building", "exporting", "resuming"} and record.get("boot") != BOOT:
            phase = "needs_human"
        result = {key: record.get(key) for key in ("id", "result_id", "attempt", "revision", "questions", "events", "review_hash", "proposal", "detail", "corrections")}
        report = (record.get("graph") or {}).get("report") or {}
        review = (report.get("agent") or {}).get("review") or {}
        added = (report.get("agent") or {}).get("added", [])                  # what the LAST review / edit added (not earlier ones)
        result.update(phase=phase, stale=stale, exports=[], preview=None, final=report.get("final"),
                      agent_reply={"says": review.get("summary") or review.get("reason") or "", "added": added} if record.get("corrections") else None)
        if record.get("receipt") and not stale:
            try:
                verify_receipt(self.folder(record["id"]), record["receipt"])
                result["preview"] = {"image": f"/api/workflow/{record['id']}/preview", "mesh": f"/api/workflow/{record['id']}/mesh"}
            except (ValueError, OSError):
                result.update(phase="needs_human", detail="The review artifacts changed. Rebuild and verify the model again.")
        if phase == "exported" and not stale and result["preview"]:
            result["exports"] = [{"format": key, "url": f"/api/workflow/{record['id']}/download/{key}"} for key in record.get("exports", {})]
        return result

    @contextmanager
    def locked(self, jid, revision):
        folder = self.folder(jid)
        lock = folder / "request.lock"
        try:
            with lock.open("x") as file:
                file.write(BOOT)
        except FileExistsError:
            raise ValueError("This job has an operation in progress; refresh its status before continuing") from None
        try:
            record = self.load(jid)
            if type(revision) is not int or revision != record["revision"]:
                raise ValueError("This screen is stale. Refresh the job before applying a decision.")
            if record["phase"] in {"building", "resuming", "exporting"}:
                raise ValueError("An interrupted operation needs inspection before another native action")
            yield record
        finally:
            lock.unlink(missing_ok=True)

    def action(self, jid, revision, action, **decision):
        with self.locked(jid, revision) as record:
            if action != "abort":
                self.current(record)
            phase = record["phase"]
            allowed = {"build": {"awaiting_approval"}, "approve_fix": {"awaiting_fix_approval"},
                       "edit_fix": {"awaiting_fix_approval"}, "reject_fix": {"awaiting_fix_approval"},
                       "retry_llm": {"llm_failed"}, "accept": {"awaiting_review"},
                       "correct": {"awaiting_review"}, "export": {"accepted"},
                       "abort": {"awaiting_approval", "awaiting_fix_approval", "awaiting_review", "llm_failed", "needs_human", "changes_requested", "accepted"}}
            if phase not in allowed.get(action, set()):
                raise ValueError(f"Decision {action!r} is not available while {phase}")
            if action in {"accept", "correct", "export"}:
                if decision.get("review_hash") != record.get("review_hash"):
                    raise ValueError("The preview changed. Reopen it before confirming or exporting.")
                verify_receipt(self.folder(jid), record["receipt"])
            if action == "accept" and (decision.get("compared_drawing") is not True or decision.get("checked_measurements") is not True):
                raise ValueError("Confirm the drawing comparison and measurement review before accepting")
            if action == "correct" and not str(decision.get("message", "")).strip():
                raise ValueError("Describe the measurement or geometry that needs correction")
            record["revision"] += 1
            record.update(phase="exporting" if action == "export" else "building" if action == "build" else "resuming", boot=BOOT)
            self.save(record)
            try:
                if action == "export":
                    record["exports"] = self.exporter(record["graph"]["approved"], self.folder(jid), record["receipt"])
                    expected = {"SLDASM", "STEP", "STL", "BUNDLE"} if record["kind"] == "assembly" else {"SLDPRT", "STEP", "STL"}
                    if set(record["exports"]) != expected:
                        raise ValueError("The export set is incomplete")
                    record["phase"] = "exported"
                elif action == "correct" and record["kind"] == "assembly":
                    record.update(phase="changes_requested", questions=[str(decision["message"]).strip()])
                elif action == "correct":
                    # the designer's chat message goes to the SolidWorks agent (corrections.json, read by agent_for), which edits the part
                    # already built (its saved SLDPRT): nothing is built again from the start
                    record["corrections"] = [*record.get("corrections", []), str(decision["message"]).strip()[:2000]]
                    (self.folder(jid) / "corrections.json").write_text(json.dumps(record["corrections"]), encoding="utf-8")
                    graph = record["graph"]
                    try:
                        out = self.editor(graph["approved"], self.folder(jid), graph.get("report") or {}, record["receipt"]["files"]["SLDPRT"]["path"])
                    except Exception as ex:                                     # noqa: BLE001 - shown in the chat, the part stays reviewable
                        out = {"report": {"ok": False, "failure": {"message": f"{type(ex).__name__}: {ex}"}}, "finish": None}
                    record["graph"] = {**graph, "report": out["report"], "finish": out["finish"]}
                    finish = out["finish"]
                    if not out["report"]["ok"] or not finish or set(finish["files"]) != {"SLDPRT", "STL", "PREVIEW"} or not finish.get("configuration"):
                        # the saved part on disk is untouched (an edit is only saved when it finished): it stays the one to review
                        failure = (out["report"].get("failure") or {}).get("message") or "the edited part could not be saved for review"
                        record.update(graph=graph, phase="awaiting_review", detail=f"The SolidWorks agent's edit stopped, the part is as before: {failure}"[:600])
                    else:
                        self._receipt(record, finish["files"], finish["configuration"])
                        record.update(phase="awaiting_review", questions=[], detail="")
                elif action == "abort":
                    record["phase"] = "aborted"
                else:
                    self._advance(record, action, decision)
            except Exception as ex:
                record.update(phase="needs_human", detail=f"{type(ex).__name__}: {ex}", questions=[str(ex)])
            record["events"] = [*record.get("events", []), f"Human action: {action}; state: {record['phase']}"]
            self.save(record)
            return self.public(record)

    def _advance(self, record, action, decision):
        folder, jid = self.folder(record["id"]), record["id"]
        workers = self.workers() if self.workers else Orchestrator.with_provider(record["llm"], build=self.build)
        if record["kind"] == "assembly":
            if action == "accept":
                record["phase"] = "accepted"
                return
            if action != "build":
                raise ValueError("Resolve assembly measurements and component questions in the assembly review before rebuilding")
            report = self.assembly_build(record["draft"], folder, self.assets, workers)
            record["graph"] = {"approved": record["draft"], "approved_hash": list_hash(record["draft"]), "report": report}
            record["phase"] = "awaiting_review" if report["status"] == "awaiting_review" else "needs_human"
            record["questions"] = report.get("errors", [])
            if report["status"] == "awaiting_review":
                native = report["files"]
                files = {"SLDASM": native["assembly"], "STL": native["mesh"], "PREVIEW": native["preview"]}
                dependencies = [{"path": str(path.resolve()), "sha256": sha256(path)} for path in Path(native["components"]).glob("*.SLDPRT")]
                self._receipt(record, files, native["configuration"], dependencies)
            return
        deps = Deps(workers.extraction.llm, self.build, workers=workers)
        cfg = {"configurable": {"thread_id": jid}, "recursion_limit": 80}
        with closing(sqlite3.connect(folder / "graph.sqlite", check_same_thread=False)) as conn:
            graph = build_graph(deps, SqliteSaver(conn))
            if action == "build":
                graph.invoke(new_job_state(jid, str(folder), "", record["draft"]), cfg)
            mapping = {"build": "approve", "approve_fix": "approve", "edit_fix": "edit_item", "reject_fix": "reject",
                       "retry_llm": "retry", "accept": "accept", "abort": "abort"}
            if graph.get_state(cfg).next:
                graph.invoke(Command(resume={**decision, "action": mapping[action]}), cfg)
            state = graph.get_state(cfg).values
            record["graph"] = state
            record.update(phase="accepted" if state["status"] == "done" else state["status"],
                          questions=state.get("reasons", []) if state["status"] == "awaiting_approval" else [state["detail"]] if state.get("detail") else [],
                          proposal=state.get("proposal"), detail=state.get("detail", ""))
            if state["status"] == "awaiting_review":
                finish = state["finish"]
                if set(finish["files"]) != {"SLDPRT", "STL", "PREVIEW"} or not finish.get("configuration"):
                    raise ValueError("Private model review artifacts/configuration are incomplete")
                self._receipt(record, finish["files"], finish["configuration"])

    def _receipt(self, record, paths, configuration, dependencies=None):
        files = {key: {"path": str(Path(path).resolve()), "sha256": sha256(path)} for key, path in paths.items()}
        record["receipt"] = {"files": files, "configuration": configuration, "approved_hash": record["graph"]["approved_hash"], "dependencies": dependencies or []}
        verify_receipt(self.folder(record["id"]), record["receipt"])
        record["review_hash"] = fingerprint({"receipt": record["receipt"], "artifact": record["artifact_hash"]})

    def artifact(self, jid, kind):
        record = self.load(jid)
        self.current(record)
        verify_receipt(self.folder(jid), record["receipt"])
        if kind == "preview":
            return Path(record["receipt"]["files"]["PREVIEW"]["path"])
        if kind == "mesh":
            return Path(record["receipt"]["files"]["STL"]["path"])
        if record["phase"] != "exported" or kind not in {"SLDPRT", "SLDASM", "STEP", "STL", "BUNDLE"}:
            raise ValueError("Confirm the model and export it before downloading deliverables")
        item = record["exports"][kind]
        path = Path(item["path"]).resolve()
        if not path.is_relative_to(self.folder(jid).resolve() / "exports") or sha256(path) != item["sha256"]:
            raise ValueError("The exported file changed; download refused")
        return path
