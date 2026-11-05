"""Expose completed local extraction test receipts in the existing browser UI.

No model calls, CAD execution or approval. Original files/hashes and recorded
readings are checked before creating immutable browser results.
"""
from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.assembly.jobs import write_json
from app.assembly.model import digest
from app.cache import default_cache
from app.drawing.model import reconcile
from app.drawing.sheets import sha256
from app.ingest import load_pages
from app.web import server


def import_case(case, existing=None):
    if case.get("error") or not case.get("read_pages"):
        return None
    source = Path(case["source"])
    if sha256(source) != case["sha256"]:
        raise ValueError(f"Source changed since extraction: {source.name}")
    key = digest({"source": case["sha256"], "read_pages": case["read_pages"]})
    if existing and existing.get("key") == key and (server.RESULTS / f"{existing['id']}.json").is_file():
        return existing
    pages = load_pages([source])
    if [p.sha for p in pages] != [p["sha"] for p in case["read_pages"]]:
        raise ValueError(f"Rendered pages changed since extraction: {source.name}")
    for page in pages:
        default_cache().put_blob(page.png)
    original_trace = Path(case["trace"])
    events = [json.loads(line) for line in (original_trace / "trace.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    if not events or events[-1].get("event") != "run done":
        raise ValueError("Only a completed trace can be imported")
    replies = [e for e in events if e.get("stage") == "llm" and e.get("event") == "reply"]
    provider = next((e.get("provider") for e in events if e.get("stage") == "llm" and e.get("event") == "call"), None)
    if provider not in {"domo", "gemini"}:
        raise ValueError("The recorded model provider is unavailable or unsupported")
    rid, assessment_id = uuid.uuid4().hex[:12], uuid.uuid4().hex[:12]
    result = {"id": rid, "llm": provider, "pages": copy.deepcopy(case["read_pages"]),
              "seconds": round(events[-1]["t"], 1), "calls": sum(p["read"]["calls"] for p in case["read_pages"]),
              "tokens": {"prompt": sum(e.get("tokens_in", 0) for e in replies), "output": sum(e.get("tokens_out", 0) for e in replies),
                         "cached_calls": sum(bool(e.get("cached")) for e in replies)},
              "source_quality": case["pages"], "harness": case["harness"],
              "imported_from": {"source_sha256": case["sha256"], "trace": str(original_trace)}, "accuracy_verified": False}
    result["drawing_model"] = reconcile(result).model_dump()
    assessment = {"id": assessment_id, "result_id": rid, "source_hash": digest({"pages": result["pages"]}),
                  "assessments": case["assessments"], "report": case["readiness"], "harness": case["harness"],
                  "imported_from": str(original_trace)}
    server.RESULTS.mkdir(parents=True, exist_ok=True)
    server.RUNS.mkdir(parents=True, exist_ok=True)
    shutil.copytree(original_trace, server.RUNS / rid)
    # Reuse the same recorded trace for the independently linked assessment card.
    shutil.copytree(original_trace, server.RUNS / assessment_id)
    write_json(server.RESULTS / f"{rid}.json", result)
    write_json(server.READINESS / rid / f"{assessment_id}.json", assessment)
    write_json(server.READINESS / rid / "latest.json", assessment)
    return {"key": key, "id": rid, "source": source.name, "url": f"http://127.0.0.1:8765/#{rid}"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    index = args.directory / "browser-results.json"
    existing = json.loads(index.read_text(encoding="utf-8")) if index.is_file() else []
    by_source = {e["source"]: e for e in existing}
    for path in sorted(args.directory.glob("*/assessment.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        result = import_case(case, by_source.get(Path(case["source"]).name))
        if result:
            by_source[result["source"]] = result
    entries = sorted(by_source.values(), key=lambda e: e["source"])
    write_json(index, entries)
    lines = ["# Completed drawing extraction results", "", "These are source-bound readings and scope assessments, not verified CAD reconstructions.", ""]
    lines.extend(f"- [{e['source']}]({e['url']})" for e in entries)
    (args.directory / "browser-results.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    print(json.dumps(entries, indent=2))


if __name__ == "__main__":
    main()
