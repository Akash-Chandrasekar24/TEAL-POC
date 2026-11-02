"""The ORCHESTRATOR: a LangGraph graph that runs one job from a drawing to a finished part. Every step of the job goes through it.

    read ──► validate ──► approve ──► build ──► review ──► done
     ▲          ▲   ▲        │          │
     │          │   └────────┤          └─ build failed ──► repair ──► approve_fix ──► build (again, from a fresh part)
     └──────────┴─ correct ───┘                              ▲  │ reject / invalid suggestion
                                                             └──┘
  read        the LLM reads the drawing and writes the build LIST (LLM call)
  validate    code: the gate + "can every item be built?" (no LLM)
  approve     HUMAN: approve / correct / edit / abort (a pause: the job waits, saved on disk)
  build       code: the runner builds the approved list, then save + export + preview (no LLM)
  repair      the LLM suggests a corrected `params` for the ONE failed item (LLM call, small package)
  approve_fix HUMAN: approve / reject / edit the item / abort
  review      HUMAN: look at the finished model beside the drawing: accept / correct / abort
  llm_pause   HUMAN: the model could not be reached (a temporary error is retried once automatically first; other errors are not retried): retry / abort

Rules kept here: the approved list is fixed (its hash is checked before every build); only the failed item's params can change, and only after a
human approves the exact diff; an environment or internal failure never goes to the LLM; every loop has a limit; a failed step is repaired at most
`max_repairs` times. The nodes of the human pauses are pure (LangGraph runs them again from the top when the job resumes).
"""
from __future__ import annotations

import json
import mimetypes
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import ValidationError

from app.llm.base import FilePart, LLMClient, LLMError, LLMJsonError
from app.menu import MENU, BuildContext, base_info, feature_card, parse_reply
from app.menu.buildlist import BASE_FEATURES, Stopped
from app.runner import validate_list

from .guards import HUMAN_ONLY_FLAGS, apply_repair, check_repair, diff_params, find_item, list_hash
from .prompts import reader_system, repair_system

STATUSES = ("reading", "awaiting_approval", "building", "repairing", "awaiting_fix_approval", "awaiting_review", "llm_failed", "done", "needs_human", "aborted", "stopped")
_TRANSIENT = re.compile(r"\b(503|429|UNAVAILABLE|RESOURCE_EXHAUSTED|overloaded|timed out|timeout|temporarily)\b", re.I)


class JobState(TypedDict, total=False):
    job_id: str
    job_dir: str
    image: str
    reads: int
    note: str | None                  # the human's correction for the next read
    draft: dict                       # the list as the LLM wrote it (or as the human edited it)
    reasons: list[str]                # why the draft cannot be approved yet
    dropped_flags: list[str]          # human-set flags the last re-read replaced (shown at the approval)
    approved: dict                    # the approved list: set ONLY by a human decision
    approved_hash: str
    versions: int                     # how many approved versions exist (1 = the original approval)
    report: dict
    finish: dict | None
    failure: dict | None
    repairs: dict                     # item id -> number of repair calls
    proposal: dict | None
    fix_feedback: str | None
    retry_node: str                   # which LLM step to run again after a provider failure: read | repair
    retried: bool                     # True only when the one automatic retry of a temporary error really ran (and failed too)
    status: str
    detail: str
    events: list[str]
    tokens: dict


@dataclass
class Deps:
    llm: LLMClient
    build: Callable[[dict, Path], dict]          # (approved list, job dir) -> {"report": dict, "finish": dict | None}
    max_reads: int = 3                           # reader calls per job (the first read + corrections)
    max_repairs: int = 2                         # repair calls per failed item
    max_format_retries: int = 1                  # automatic re-asks when the reader's reply does not fit the list format
    llm_retry_delay: float = 3.0                 # seconds before the ONE automatic retry of a temporary provider error (503, quota, timeout)


def new_job_state(job_id: str, job_dir: str, image: str, draft: dict | None = None) -> JobState:
    """`draft`: a saved build list; the job then starts at validate -> approve and makes no model call."""
    state = JobState(job_id=job_id, job_dir=job_dir, image=image, reads=0, note=None, reasons=[], versions=0, repairs={}, events=[], status="reading",
                     tokens={"calls": 0, "prompt": 0, "output": 0, "thought": 0}, finish=None, failure=None, proposal=None, fix_feedback=None)
    if draft is not None:
        state.update(draft=draft, status="awaiting_approval", events=["started from a saved list (no model call)"])
    return state


# ------------------------------------------------------------------ helpers
def _events(state: JobState, *lines: str) -> list[str]:
    return list(state.get("events", [])) + list(lines)


def _add_tokens(state: JobState, usage: dict) -> dict:
    t = dict(state.get("tokens") or {"calls": 0, "prompt": 0, "output": 0, "thought": 0})
    t["calls"] += 1
    t["prompt"] += usage.get("prompt_tokens", 0)
    t["output"] += usage.get("output_tokens", 0)
    t["thought"] += usage.get("thought_tokens", 0)
    return t


def human_flags(lst: dict | None) -> list[str]:
    """The switches a human set in a list, as short labels: 'F5 floor_open', 'F7 edge_zone_ok', 'expected_final'."""
    lst = lst or {}
    out = [f"{it.get('id')} {flag}" for it in lst.get("items", []) for flag in HUMAN_ONLY_FLAGS if (it.get("params") or {}).get(flag)]
    return out + (["expected_final"] if lst.get("expected_final") else [])


def _save(job_dir: str, name: str, data: Any) -> None:
    path = Path(job_dir)
    path.mkdir(parents=True, exist_ok=True)
    (path / name).write_text(json.dumps(data, indent=1, ensure_ascii=False, default=str), encoding="utf-8")


def _image_part(path: str) -> FilePart:
    mime = mimetypes.guess_type(path)[0] or "image/png"
    return FilePart(Path(path).read_bytes(), mime)


def _reasons_for(draft: dict) -> list[str]:
    try:
        parsed = parse_reply(draft)
    except ValidationError as ex:
        first = ex.errors()[0]
        return [f"the list does not fit the list format: {'.'.join(str(p) for p in first['loc'])}: {first['msg']}"]
    if isinstance(parsed, Stopped):
        return [f"the reader stopped: {parsed.reason}"]
    return validate_list(parsed)


def repair_package(state: JobState) -> dict:
    approved, report, failure = state["approved"], state["report"], state["failure"]
    item = find_item(approved, failure["item_id"])
    context: dict[str, Any] = {"unit": approved["unit"]}
    first = approved["items"][0]
    if first["feature"] in BASE_FEATURES:
        try:
            (w, h), _, thickness = base_info(MENU[first["feature"]].params_model.model_validate(first["params"]), approved["unit"])
            context.update(base_thickness=thickness, base_outline_wh=[w, h])
        except (ValidationError, KeyError):
            pass
    rep = next((i for i in report["items"] if i["id"] == failure["item_id"]), {})
    keep = ("name", "ok", "verdict", "message", "expected_delta_mm3", "measured_delta_mm3")
    probes = rep.get("probes", [])
    ids = [i["id"] for i in approved["items"]]
    return {"context": context, "approved_item": item, "feature_description": feature_card(item["feature"]), "failure": failure,
            "item_report": {"verdicts": [{k: v.get(k) for k in keep} for v in rep.get("verdicts", [])],
                            "failed_probes": [p for p in probes if not p["ok"]], "passed_probes": sum(1 for p in probes if p["ok"]),
                            "notes": rep.get("notes", [])[:6]},
            "built_before": [{"id": i["id"], "feature": i["feature"], "status": i["status"]} for i in report["items"] if ids.index(i["id"]) < ids.index(failure["item_id"])],
            "human_feedback_on_the_last_suggestion": state.get("fix_feedback")}


# ------------------------------------------------------------------ the graph
def build_graph(deps: Deps, checkpointer=None):
    def generate(system: str, parts: list):
        """One model call; a TEMPORARY provider error (503, quota, timeout) is retried once after a short pause. Anything else, or a second failure, is raised."""
        try:
            return deps.llm.generate_json(system, parts)
        except LLMError as ex:
            if isinstance(ex, LLMJsonError) or not _TRANSIENT.search(str(ex)):
                ex.retried = False                                              # no automatic retry ran: this kind of error is not retried
                raise
            time.sleep(deps.llm_retry_delay)
            try:
                return deps.llm.generate_json(system, parts)
            except LLMError as second:
                second.retried = True                                           # the one automatic retry ran and failed too
                raise

    def read(state: JobState) -> dict:
        reads = state.get("reads", 0)
        if reads >= deps.max_reads:
            return {"status": "needs_human", "detail": f"the reader was asked {reads} times; a human has to resolve this", "events": _events(state, "read limit reached")}
        if not state.get("image"):
            return {"status": "needs_human", "detail": "this job started from a saved list, so there is no drawing to read again: edit the list or start a new job with the drawing",
                    "events": _events(state, "no drawing to read (job started from a saved list)")}
        parts: list[Any] = [_image_part(state["image"])]
        if state.get("note"):
            parts.append(f"The human corrected your previous list. Correction: {state['note']}\nYour previous list:\n{json.dumps(state.get('draft', {}), ensure_ascii=False)}\n"
                         "Write the corrected list.")
        tokens, data, parsed, error = state.get("tokens"), None, None, ""
        probe_state = dict(state)
        for attempt in range(1 + deps.max_format_retries):
            try:
                resp = generate(reader_system(), parts)
            except LLMError as ex:
                return {"status": "llm_failed", "retry_node": "read", "retried": getattr(ex, "retried", False), "detail": f"the reader call failed: {ex}", "tokens": tokens,
                        "events": _events(state, f"reader call failed: {ex}" + (" (after one automatic retry)" if getattr(ex, "retried", False) else " (no automatic retry for this error)"))}
            probe_state["tokens"] = tokens = _add_tokens(probe_state, resp.usage)
            data = resp.data
            try:
                parsed = parse_reply(data)
                error = ""
                break
            except ValidationError as ex:
                first = ex.errors()[0]
                error = f"{'.'.join(str(p) for p in first['loc'])}: {first['msg']}"
                parts = parts + [f"Your reply does not fit the list format ({error}). Reply again with the corrected JSON only."]
        out: dict[str, Any] = {"reads": reads + 1, "note": None, "tokens": tokens}
        if isinstance(parsed, Stopped):
            _save(state["job_dir"], f"reader_stop_{reads + 1}.json", data)
            return {**out, "status": "stopped", "detail": parsed.reason, "events": _events(state, f"the reader stopped: {parsed.reason}")}
        if parsed is None:                                                        # still not in the format: the human sees it and can only correct or abort
            draft = data if isinstance(data, dict) else {"raw": data}
            _save(state["job_dir"], f"draft_{reads + 1}.json", draft)
            return {**out, "draft": draft, "status": "awaiting_approval",                  # `validate` reports the format problem to the human
                    "events": _events(state, f"the reader's reply is not in the list format ({error})")}
        draft = parsed.model_dump()
        events = _events(state, f"read {reads + 1}: {len(draft['items'])} items")
        if draft["accepted_gaps"]:
            events.append(f"the reader wrote accepted_gaps {draft['accepted_gaps']}: cleared (only a human may accept a gap)")
            draft["accepted_gaps"] = []
        for flag in HUMAN_ONLY_FLAGS:                                              # switches that turn a check off: set by a human at approval, never by a model
            hit = [it.get("id") for it in draft["items"] if it.get("params", {}).get(flag)]
            if hit:
                for it in draft["items"]:
                    it["params"].pop(flag, None)
                events.append(f"the reader wrote {flag} on {hit}: cleared (only a human may set it)")
        if draft.get("expected_final") is not None:
            events.append(f"the reader wrote expected_final {draft['expected_final']}: cleared (only a human may set it)")
            draft["expected_final"] = None
        dropped = human_flags(state.get("approved") or state.get("draft"))            # what a human had set in the list this re-read replaces
        if dropped:
            events.append(f"the new read replaced the list: human-set {dropped} dropped, add them again if still needed")
        _save(state["job_dir"], f"draft_{reads + 1}.json", draft)
        return {**out, "draft": draft, "dropped_flags": dropped, "status": "awaiting_approval", "events": events}

    def validate(state: JobState) -> dict:
        reasons = _reasons_for(state["draft"])
        return {"reasons": reasons, "status": "awaiting_approval", "events": _events(state, f"validate: {len(reasons)} reason(s) against approval")}

    def approve(state: JobState) -> dict:
        decision = interrupt({"kind": "approve_list", "job_id": state["job_id"], "list": state["draft"], "reasons": state.get("reasons", []),
                              "reads": state.get("reads", 0), "tokens": state.get("tokens"), "dropped_flags": state.get("dropped_flags", [])})
        action = (decision or {}).get("action")
        if action == "abort":
            return {"status": "aborted", "detail": "aborted by the human at the list approval", "events": _events(state, "aborted at the list approval")}
        if action == "correct":
            note = str(decision.get("message", "")).strip()
            if not note:
                return {"status": "awaiting_approval", "events": _events(state, "a correction needs a message")}
            return {"note": note, "status": "reading", "events": _events(state, f"human correction: {note}")}
        if action == "edit" and isinstance(decision.get("list"), dict):
            return {"draft": decision["list"], "status": "awaiting_approval", "events": _events(state, "the human edited the list")}
        if action == "approve":
            candidate = json.loads(json.dumps(state["draft"]))
            gaps = [g for g in decision.get("accepted_gaps", []) if g in {u.get("id") for u in candidate.get("unsupported", [])}]
            candidate["accepted_gaps"] = gaps
            if _reasons_for(candidate):                                          # not approvable yet: back to the human with the reasons
                return {"draft": candidate, "status": "awaiting_approval", "events": _events(state, "approve refused: the list still has reasons")}
            _save(state["job_dir"], "approved_v1.json", candidate)
            return {"approved": candidate, "approved_hash": list_hash(candidate), "versions": 1, "reasons": [], "status": "building", "repairs": {},
                    "events": _events(state, f"approved by the human ({len(gaps)} gap(s) accepted), hash {list_hash(candidate)}")}
        return {"status": "awaiting_approval", "events": _events(state, f"unknown decision {action!r}")}

    def build(state: JobState) -> dict:
        approved = state["approved"]
        if list_hash(approved) != state["approved_hash"]:
            return {"status": "needs_human", "detail": "the approved list changed after approval: refusing to build", "events": _events(state, "hash mismatch: build refused")}
        out = deps.build(approved, Path(state["job_dir"]))
        report, finish = out["report"], out.get("finish")
        _save(state["job_dir"], f"report_{state.get('versions', 1)}_{len(state.get('events', []))}.json", {"report": report, "finish": finish})
        base = {"report": report, "finish": finish, "failure": report.get("failure"), "proposal": None}
        if report["ok"] and finish and finish["ok"]:
            return {**base, "status": "awaiting_review", "events": _events(state, "build OK, files written")}
        if report["ok"]:
            bad = [c["name"] for c in (finish or {}).get("checks", []) if not c["ok"]]
            return {**base, "status": "needs_human", "detail": f"the part was built but the finish failed: {bad}", "events": _events(state, f"finish failed: {bad}")}
        f = report["failure"]
        repairable = f["kind"] in ("request", "check", "tool") and state.get("repairs", {}).get(f["item_id"], 0) < deps.max_repairs
        if repairable:
            return {**base, "status": "repairing", "events": _events(state, f"build failed at {f['item_id']} ({f['kind']}): repair")}
        why = "environment problem" if f["kind"] in ("environment", "internal") else "no repair attempts left"
        return {**base, "status": "needs_human", "detail": f"{f['item_id']} ({f['feature']}) {f['kind']}: {f['message']} ({why})",
                "events": _events(state, f"build failed at {f['item_id']}: needs a human ({why})")}

    def repair(state: JobState) -> dict:
        failure = state["failure"]
        item_id = failure["item_id"]
        repairs = dict(state.get("repairs", {}))
        if repairs.get(item_id, 0) >= deps.max_repairs:
            return {"status": "needs_human", "detail": f"{item_id}: no repair attempts left", "events": _events(state, f"{item_id}: repair attempts used up")}
        try:
            resp = generate(repair_system(), [json.dumps(repair_package(state), ensure_ascii=False)])
        except LLMError as ex:                                                    # a provider failure is not a repair attempt: it is not counted
            return {"status": "llm_failed", "retry_node": "repair", "retried": getattr(ex, "retried", False), "detail": f"the repair call failed: {ex}",
                    "events": _events(state, f"repair call failed: {ex}" + (" (after one automatic retry)" if getattr(ex, "retried", False) else " (no automatic retry for this error)"))}
        repairs[item_id] = repairs.get(item_id, 0) + 1
        tokens = _add_tokens(state, resp.usage)
        ok, reason, params = check_repair(state["approved"], item_id, resp.data)
        if not ok:
            if isinstance(resp.data, dict) and resp.data.get("status") == "needs_human":
                return {"status": "needs_human", "detail": reason, "repairs": repairs, "tokens": tokens, "events": _events(state, f"repair: {reason}")}
            return {"status": "repairing", "repairs": repairs, "tokens": tokens, "fix_feedback": f"your last suggestion was rejected: {reason}",
                    "events": _events(state, f"repair suggestion rejected by the guard: {reason}")}
        old = find_item(state["approved"], item_id)["params"]
        proposal = {"item_id": item_id, "params": params, "reason": reason, "diff": diff_params(old, params)}
        _save(state["job_dir"], f"proposal_{item_id}_{repairs[item_id]}.json", proposal)
        return {"proposal": proposal, "status": "awaiting_fix_approval", "repairs": repairs, "tokens": tokens, "fix_feedback": None,
                "events": _events(state, f"repair suggestion for {item_id}: {len(proposal['diff'])} change(s)")}

    def approve_fix(state: JobState) -> dict:
        p = state["proposal"]
        decision = interrupt({"kind": "approve_fix", "job_id": state["job_id"], "item_id": p["item_id"], "reason": p["reason"], "diff": p["diff"],
                              "failure": state["failure"]})
        action = (decision or {}).get("action")
        if action == "abort":
            return {"status": "aborted", "detail": "aborted by the human at the fix approval", "events": _events(state, "aborted at the fix approval")}
        if action == "reject":
            return {"status": "repairing", "fix_feedback": str(decision.get("message", "")).strip() or "the human rejected the suggestion", "proposal": None,
                    "events": _events(state, "the human rejected the suggestion")}
        params = p["params"] if action == "approve" else decision.get("params") if action == "edit_item" else None
        if params is None:
            return {"status": "awaiting_fix_approval", "events": _events(state, f"unknown decision {action!r}")}
        ok, why, checked = check_repair(state["approved"], p["item_id"], {"status": "suggestion", "item_id": p["item_id"], "params": params, "reason": "human"},
                                        human=action == "edit_item")
        if not ok:
            return {"status": "awaiting_fix_approval", "events": _events(state, f"the item cannot be accepted: {why}")}
        new = apply_repair(state["approved"], p["item_id"], checked)
        versions = state.get("versions", 1) + 1
        _save(state["job_dir"], f"approved_v{versions}.json", new)
        return {"approved": new, "approved_hash": list_hash(new), "versions": versions, "status": "building", "proposal": None, "fix_feedback": None,
                "events": _events(state, f"fix for {p['item_id']} approved by the human (version {versions}), hash {list_hash(new)}")}

    def review(state: JobState) -> dict:
        decision = interrupt({"kind": "review", "job_id": state["job_id"], "files": state["finish"]["files"], "final": state["report"].get("final"),
                              "gaps": state["report"].get("gaps", [])})
        action = (decision or {}).get("action")
        if action == "accept":
            return {"status": "done", "detail": "accepted by the human", "events": _events(state, "accepted by the human")}
        if action == "correct" and str(decision.get("message", "")).strip():
            return {"note": str(decision["message"]).strip(), "status": "reading", "events": _events(state, f"human correction after the build: {decision['message']}")}
        if action == "abort":
            return {"status": "aborted", "detail": "aborted by the human at the review", "events": _events(state, "aborted at the review")}
        return {"status": "awaiting_review", "events": _events(state, f"unknown decision {action!r}")}

    def llm_pause(state: JobState) -> dict:
        decision = interrupt({"kind": "llm_failed", "job_id": state["job_id"], "detail": state.get("detail", ""), "retry_node": state.get("retry_node"),
                              "retried": state.get("retried", False)})
        action = (decision or {}).get("action")
        if action == "retry":
            return {"status": "reading" if state.get("retry_node") == "read" else "repairing", "detail": "", "events": _events(state, "the human asked to retry the model call")}
        if action == "abort":
            return {"status": "aborted", "detail": "aborted by the human after a model failure", "events": _events(state, "aborted after a model failure")}
        return {"status": "llm_failed", "events": _events(state, f"unknown decision {action!r}")}

    g = StateGraph(JobState)
    for name, fn in (("read", read), ("llm_pause", llm_pause), ("validate", validate), ("approve", approve), ("build", build), ("repair", repair), ("approve_fix", approve_fix), ("review", review)):
        g.add_node(name, fn)
    g.add_conditional_edges(START, lambda s: "validate" if s.get("draft") else "read", {"validate": "validate", "read": "read"})   # a saved list skips the model
    g.add_conditional_edges("read", lambda s: "llm_pause" if s["status"] == "llm_failed" else END if s["status"] in ("stopped", "needs_human") else "validate",
                            {"validate": "validate", "llm_pause": "llm_pause", END: END})
    g.add_conditional_edges("llm_pause", lambda s: {"reading": "read", "repairing": "repair", "llm_failed": "llm_pause"}.get(s["status"], END),
                            {"read": "read", "repair": "repair", "llm_pause": "llm_pause", END: END})
    g.add_edge("validate", "approve")
    g.add_conditional_edges("approve", lambda s: {"reading": "read", "awaiting_approval": "validate", "building": "build"}.get(s["status"], END),
                            {"read": "read", "validate": "validate", "build": "build", END: END})
    g.add_conditional_edges("build", lambda s: {"awaiting_review": "review", "repairing": "repair"}.get(s["status"], END), {"review": "review", "repair": "repair", END: END})
    g.add_conditional_edges("repair", lambda s: {"awaiting_fix_approval": "approve_fix", "repairing": "repair", "llm_failed": "llm_pause"}.get(s["status"], END),
                            {"approve_fix": "approve_fix", "repair": "repair", "llm_pause": "llm_pause", END: END})
    g.add_conditional_edges("approve_fix", lambda s: {"building": "build", "repairing": "repair", "awaiting_fix_approval": "approve_fix"}.get(s["status"], END),
                            {"build": "build", "repair": "repair", "approve_fix": "approve_fix", END: END})
    g.add_conditional_edges("review", lambda s: {"reading": "read", "awaiting_review": "review"}.get(s["status"], END), {"read": "read", "review": "review", END: END})
    return g.compile(checkpointer=checkpointer)
