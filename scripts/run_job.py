r"""Run a JOB: one drawing -> a build list (the LLM reads the drawing) -> your approval -> the part in SolidWorks -> your review.

  python scripts\run_job.py start  --image <drawing.png|jpg|pdf> [--job-id ID]
  python scripts\run_job.py start  --list <approved_list.json> [--job-id ID]      (no model call: validate, then your approval, then build)
  python scripts\run_job.py resume --job ID --decision "{\"action\": \"approve\"}"          (or --decision-file decision.json)
  python scripts\run_job.py status --job ID
  add --llm gemini|domo to choose the model (default WORKER_LLM_PROVIDER in .env, else domo); a resumed job keeps the model it was started with
  add --no-repair (on every command of the job) to turn the model repair step off: a failed check then stops the job for a human and no Gemini call is made
  add --no-cache to make real model calls even when the same call was answered before (the cache is on by default; LLM_CACHE=off in .env turns it off)
  python scripts\run_job.py cache [--purge] [--layer llm] [--tenant NAME]       (show the cache, or delete entries; --purge alone deletes everything)

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


def job_llm(args, jid: str) -> str | None:
    """--llm if given, else the model the job was started with (meta.json), else the worker default."""
    if args.llm:
        return args.llm
    meta = job_dir(jid) / "meta.json"
    return json.loads(meta.read_text(encoding="utf-8")).get("llm") if meta.is_file() else None


def make_graph(no_repair: bool = False, llm: str | None = None, no_cache: bool = False):
    """`no_repair`: no model repair step: a failed check stops the job at "needs a human" and makes NO model call (pass --no-repair on EVERY command of that job).
    `llm`: gemini | domo (None = WORKER_LLM_PROVIDER). `no_cache`: real model calls even for a call answered before."""
    from app.agent.orchestrator import Orchestrator
    STORE.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(STORE / "jobs.sqlite", check_same_thread=False)
    workers = Orchestrator.with_provider(llm, cache=False if no_cache else None, build=solidworks_build)
    client = workers.extraction.llm
    deps = Deps(client, solidworks_build, workers=workers,
                max_repairs=0 if (no_repair or os.getenv("JOB_NO_REPAIR")) else 2)
    return build_graph(deps, SqliteSaver(conn))


def show(out: dict, state: dict, jid: str) -> None:
    """Print what the human has to look at (or the final status)."""
    tokens = state.get("tokens", {})
    print(f"\n=== job {jid} | status: {state.get('status')} | LLM calls {tokens.get('calls', 0)} (+{tokens.get('cached', 0)} from cache), tokens in {tokens.get('prompt', 0)} / out {tokens.get('output', 0)} / thought {tokens.get('thought', 0)}")
    for role, task in state.get("worker_status", {}).items():
        print(f"  {role}: {task['task']} -> {task['status']}")
    if state.get("harness"):
        budget = state["harness"]
        print(f"  harness: {budget['model_calls']}/{budget['policy']['max_model_calls']} model attempts, "
              f"{budget['tokens']}/{budget['policy']['max_tokens']} reported tokens, {budget['actions']} actions")
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


def cache_cmd(args) -> int:
    """Show the cache (entries and size per tenant and layer) or delete entries."""
    from app.cache import default_cache
    cache = default_cache()
    if args.purge:
        print(f"deleted {cache.purge(args.tenant, args.layer)} cache entries")
    for s in cache.stats() or [{"tenant": "-", "layer": "-", "entries": 0, "bytes": 0}]:
        print(f"  tenant {s['tenant']} | layer {s['layer']} | {s['entries']} entries | {s['bytes'] / 1e6:.2f} MB")
    return 0


def run(args) -> int:
    from app import config
    from app.llm import LLMError
    if args.cmd == "cache":
        return cache_cmd(args)
    JOBS.mkdir(parents=True, exist_ok=True)
    jid = (args.job_id or datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:4]) if args.cmd == "start" else args.job
    llm = job_llm(args, jid) or config.WORKER_LLM_PROVIDER
    try:
        graph = make_graph(args.no_repair, llm, args.no_cache)
    except LLMError as ex:
        print(f"the {llm} model cannot be used: {ex}")
        return 2
    print(f"model: {llm}")
    from app import trace
    folder = job_dir(jid) / "trace" / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{args.cmd}"
    try:
        with trace.run(jid, folder, save_files=config.TRACE_FILES, command=args.cmd, llm=llm):
            out, cfg = _run_graph(args, graph, jid, llm)
    except UsageError as ex:
        print(ex)
        return 2
    show(out, graph.get_state(cfg).values, jid)
    print(f"process log: {folder / 'run.log'}")
    return 0


class UsageError(ValueError):
    """A wrong command line: printed, exit code 2."""


def _run_graph(args, graph, jid: str, llm: str):
    """start / resume / status: one graph invocation (traced by the caller)."""
    if args.cmd == "start":
        jd = job_dir(jid)
        jd.mkdir(parents=True, exist_ok=True)
        if bool(args.image) == bool(args.list):
            raise UsageError("give exactly one of --image (read a drawing with the model) or --list (start from a saved build list)")
        draft = json.loads(Path(args.list).read_text(encoding="utf-8")) if args.list else None
        image = str(Path(args.image).resolve()) if args.image else ""
        (jd / "meta.json").write_text(json.dumps({"image": image, "list": str(Path(args.list).resolve()) if args.list else "", "llm": llm}, indent=1),
                                      encoding="utf-8")
        cfg = {"configurable": {"thread_id": jid}, "recursion_limit": 80}
        out = graph.invoke(new_job_state(jid, str(jd), image, draft), cfg)
    else:
        cfg = {"configurable": {"thread_id": jid}, "recursion_limit": 80}
        if args.cmd == "resume":
            text = args.decision if args.decision else Path(args.decision_file).read_text(encoding="utf-8")
            decision = json.loads(text)
            if not isinstance(decision, dict):
                raise UsageError("the decision must be a JSON object, for example {\"action\": \"approve\"}")
            out = graph.invoke(Command(resume=decision), cfg)
        else:
            snap = graph.get_state(cfg)
            out = {"__interrupt__": [i for t in snap.tasks for i in t.interrupts]} if snap.next else {}
    return out, cfg


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
    for p in (s, r, t):
        p.add_argument("--llm", choices=("gemini", "domo"), help="both worker models (default: WORKER_LLM_PROVIDER in .env; a resumed job keeps its model)")
        p.add_argument("--no-cache", action="store_true", help="make real model calls even when the same call was answered before")
    c = sub.add_parser("cache", help="show the cache, or delete entries with --purge")
    c.add_argument("--purge", action="store_true", help="delete entries (all, or only --tenant / --layer)")
    c.add_argument("--tenant")
    c.add_argument("--layer", help="e.g. llm")
    sys.exit(run(ap.parse_args()))
