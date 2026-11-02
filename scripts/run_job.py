r"""Run a JOB: one drawing -> a build list (the LLM reads the drawing) -> your approval -> the part in SolidWorks -> your review.

  python scripts\run_job.py start  --image <drawing.png|jpg|pdf> [--job-id ID]
  python scripts\run_job.py start  --list <approved_list.json> [--job-id ID]      (no model call: validate, then your approval, then build)
  python scripts\run_job.py resume --job ID --decision "{\"action\": \"approve\"}"          (or --decision-file decision.json)
  python scripts\run_job.py status --job ID
  add --no-repair (on every command of the job) to turn the model repair step off: a failed check then stops the job for a human and no Gemini call is made

The job pauses at every human step and prints what you have to look at. Decisions are JSON objects:
  at the list              {"action": "approve", "accepted_gaps": ["U1"]} | {"action": "correct", "message": "..."} | {"action": "edit", "list": {...}} | {"action": "abort"}
  at a suggested fix       {"action": "approve"} | {"action": "reject", "message": "..."} | {"action": "edit_item", "params": {...}} | {"action": "abort"}
  at the review            {"action": "accept"} | {"action": "correct", "message": "..."} | {"action": "abort"}
  after a model failure    {"action": "retry"} | {"action": "abort"}

A job lives in storage\jobs\<id>\ (the list drafts, the approved versions, the reports and the output files: SLDPRT, STEP, STL, preview).
Its state is saved in storage\jobs.sqlite, so a paused job can be resumed by a later run of this script.
The model reads GEMINI_API_KEY / GEMINI_MODEL from .env (see .env.example). The build step needs SolidWorks 2026 installed and port 5000 free.
"""
import argparse
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from langgraph.checkpoint.sqlite import SqliteSaver  # noqa: E402
from langgraph.types import Command  # noqa: E402

from app.agent.graph import Deps, build_graph, new_job_state  # noqa: E402
from app.agent.live import solidworks_build  # noqa: E402

STORE = ROOT / "storage"
JOBS = STORE / "jobs"


def job_dir(job_id: str) -> Path:
    return JOBS / job_id


def make_graph(no_repair: bool = False):
    """`no_repair`: no model repair step: a failed check stops the job at "needs a human" and makes NO Gemini call (pass --no-repair on EVERY command of that job)."""
    from app.llm.gemini import GeminiClient
    STORE.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(STORE / "jobs.sqlite", check_same_thread=False)
    client = GeminiClient()
    deps = Deps(client, solidworks_build, max_repairs=0) if (no_repair or os.getenv("JOB_NO_REPAIR")) else Deps(client, solidworks_build)
    return build_graph(deps, SqliteSaver(conn))


def show(out: dict, state: dict, jid: str) -> None:
    """Print what the human has to look at (or the final status)."""
    tokens = state.get("tokens", {})
    print(f"\n=== job {jid} | status: {state.get('status')} | LLM calls {tokens.get('calls', 0)}, tokens in {tokens.get('prompt', 0)} / out {tokens.get('output', 0)} / thought {tokens.get('thought', 0)}")
    pending = out.get("__interrupt__")
    if not pending:
        print(f"detail: {state.get('detail', '')}")
        if state.get("finish"):
            for k, v in state["finish"]["files"].items():
                print(f"  {k}: {v}")
        print("events:", *state.get("events", [])[-8:], sep="\n  ")
        return
    ask = pending[0].value
    if ask["kind"] == "approve_list":
        lst = ask["list"]
        print(f"WAITING FOR YOUR APPROVAL OF THE LIST (read {ask['reads']} time(s))")
        print(f"part: {lst.get('part_name')} | unit: {lst.get('unit')} | projection: {lst.get('projection')}")
        print("seen dimensions:", ", ".join(lst.get("seen_dimensions", [])))
        if ask.get("dropped_flags"):
            print(f"NOTE: this re-read replaced the list, so these human-set flags were DROPPED: {', '.join(ask['dropped_flags'])}. Add them again if you still need them.")
        if lst.get("expected_final"):
            print("expected for the FINISHED part (set by a human):", json.dumps(lst["expected_final"]))
        for it in lst.get("items", []):
            flag = (" [uncertain]" if it.get("uncertain") else "") + (f" [derived: {it.get('derived_from')}]" if it.get("derived") else "")
            print(f"  {it['id']} {it['feature']}  printed: {it.get('printed')!r}{flag}")
            print(f"      params: {json.dumps(it['params'], ensure_ascii=False)}")
        for u in lst.get("unsupported", []):
            print(f"  UNSUPPORTED {u['id']}: {u['text']}")
        for n in lst.get("needs_human", []):
            print(f"  NEEDS HUMAN: {n}")
        if ask["reasons"]:
            print("CANNOT BE APPROVED YET:")
            for r in ask["reasons"]:
                print("  -", r)
        print(f"(the list is saved in {job_dir(jid)})")
    elif ask["kind"] == "approve_fix":
        print(f"WAITING FOR YOUR APPROVAL OF A FIX for {ask['item_id']}")
        print(f"failure: {ask['failure']['kind']} {ask['failure'].get('code', '')}: {ask['failure']['message']}")
        print(f"suggestion: {ask['reason']}")
        for d in ask["diff"]:
            print(f"  {d['path']}: {d['old']}  ->  {d['new']}")
    elif ask["kind"] == "llm_failed":
        print(f"THE MODEL COULD NOT BE REACHED ({ask['retry_node']} step)" + (" after one automatic retry" if ask.get("retried") else ": no automatic retry ran (this kind of error is not retried)"))
        print(f"  {ask['detail']}")
        print('  decide: {"action": "retry"} or {"action": "abort"}')
    else:
        print("WAITING FOR YOUR REVIEW of the finished part (compare it with the drawing)")
        for k, v in ask["files"].items():
            print(f"  {k}: {v}")
        print("  final:", {k: v for k, v in (ask.get("final") or {}).items() if k not in ("expected_checks", "predicted_check")})
        for c in (ask.get("final") or {}).get("expected_checks", []):
            print(f"  [{'PASS' if c['ok'] else 'FAIL'}] {c['name']}: {c['detail']}")
        pc = (ask.get("final") or {}).get("predicted_check")
        if pc:
            print(f"  [{'PASS' if pc['ok'] else 'FAIL'}] {pc['name']} ({pc['mode']} mode{'' if pc['mode'] == 'stop' else ': reported, does not stop'}): {pc['detail']}")
        for g in ask.get("gaps", []):
            print(f"  NOT BUILT (accepted gap) {g['id']}: {g['text']}")


def run(args) -> int:
    JOBS.mkdir(parents=True, exist_ok=True)
    graph = make_graph(args.no_repair)
    if args.cmd == "start":
        jid = args.job_id or datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:4]
        jd = job_dir(jid)
        jd.mkdir(parents=True, exist_ok=True)
        if bool(args.image) == bool(args.list):
            print("give exactly one of --image (read a drawing with the model) or --list (start from a saved build list)")
            return 2
        draft = json.loads(Path(args.list).read_text(encoding="utf-8")) if args.list else None
        image = str(Path(args.image).resolve()) if args.image else ""
        (jd / "meta.json").write_text(json.dumps({"image": image, "list": str(Path(args.list).resolve()) if args.list else ""}, indent=1), encoding="utf-8")
        cfg = {"configurable": {"thread_id": jid}, "recursion_limit": 80}
        out = graph.invoke(new_job_state(jid, str(jd), image, draft), cfg)
    else:
        jid = args.job
        cfg = {"configurable": {"thread_id": jid}, "recursion_limit": 80}
        if args.cmd == "resume":
            text = args.decision if args.decision else Path(args.decision_file).read_text(encoding="utf-8")
            decision = json.loads(text)
            if not isinstance(decision, dict):
                print("the decision must be a JSON object, for example {\"action\": \"approve\"}")
                return 2
            out = graph.invoke(Command(resume=decision), cfg)
        else:
            snap = graph.get_state(cfg)
            out = {"__interrupt__": [i for t in snap.tasks for i in t.interrupts]} if snap.next else {}
    show(out, graph.get_state(cfg).values, jid)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Run a drawing-to-SolidWorks job (see the module docstring).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start", help="read a drawing and start a job")
    s.add_argument("--image", help="the drawing to read with the model")
    s.add_argument("--list", help="a saved build list (JSON): skips the model, goes to validate -> your approval")
    s.add_argument("--job-id")
    s.add_argument("--no-repair", action="store_true", help="no model repair step: a failed check stops the job and waits for a human (no Gemini call)")
    r = sub.add_parser("resume", help="give a decision to a paused job")
    r.add_argument("--job", required=True)
    r.add_argument("--decision")
    r.add_argument("--decision-file")
    r.add_argument("--no-repair", action="store_true", help="same switch as on start: give it on every command of a job that must not call the model for repairs")
    t = sub.add_parser("status", help="show where a job is")
    t.add_argument("--job", required=True)
    t.add_argument("--no-repair", action="store_true")
    sys.exit(run(ap.parse_args()))
