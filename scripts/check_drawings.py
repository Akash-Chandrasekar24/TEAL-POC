"""Assess independent drawing files, deduplicate exact sources and record real evidence.

  python scripts/check_drawings.py --inputs inputs --out storage/acceptance
  python scripts/check_drawings.py --inputs inputs --files image.png --vision --out storage/acceptance

--vision uses the configured extraction worker (Domo/high by default). It does
not start SolidWorks or invent reference CAD, and never awards an accuracy score.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import config, trace
from app.agent.orchestrator import Orchestrator
from app.drawing.readiness import readiness, source_quality
from app.drawing.model import reconcile
from app.ingest import load_pages
from app.perception.segment import segment_png


def inspect_file(path: Path, output: Path, vision: bool, provider: str | None, read: bool = False):
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    folder = output / sha[:16]
    folder.mkdir(parents=True, exist_ok=True)
    report = {"source": str(path.resolve()), "sha256": sha, "accuracy_verified": False, "live_cad": "not_executed"}
    run_id = uuid.uuid4().hex[:12]
    report["trace"] = str(folder / "traces" / run_id)
    with trace.run(run_id, folder / "traces" / run_id, save_files=config.TRACE_FILES, source=path.name):
        pages = load_pages([path])
        qualities = [source_quality(p.png) for p in pages]
        report["pages"] = [{"page": p.no, **quality, "boxes": [{"box": b.box, "kind": b.kind} for b in segment_png(p.png)]}
                           for p, quality in zip(pages, qualities)]
        assessed = []
        if vision or read:
            supervisor = Orchestrator.with_provider(provider or config.WORKER_LLM_PROVIDER)
            try:
                for page in pages:
                    if vision:
                        assessed.append(supervisor.assess_page(page.png))
                if read:
                    readings = []
                    for page in pages:
                        boxes = [b.box for b in segment_png(page.png) if b.kind == "view"]
                        readings.append({"no": page.no, "source": path.name, "source_page": page.source_page,
                                         "sha": page.sha, "width": page.width, "height": page.height, "boxes": boxes,
                                         "read": asdict(supervisor.read_page(page.no, page.png, boxes))})
                    report["read_pages"] = readings
                    report["drawing_model"] = reconcile({"id": sha[:12], "pages": readings}).model_dump()
                report["harness"] = supervisor.harness.snapshot()
            except Exception as ex:
                report["error"] = f"{type(ex).__name__}: {ex}"
                report["harness"] = supervisor.harness.snapshot()
        report["assessments"] = [a.model_dump() for a in assessed]
        report["readiness"] = readiness(assessed, qualities)
        if len(assessed) != len(pages):
            report["readiness"]["blockers"].insert(0, "Some pages have not been visually assessed")
    (folder / "assessment.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def markdown(summary):
    lines = ["# Supplied drawing acceptance assessment", "",
             f"{summary['files']} files; {summary['unique_sources']} unique sources; {summary['duplicates']} exact duplicate(s).", "",
             "**High-accuracy reconstruction has not been established for any case.**",
             "Live Domo scope/extraction calls are distinct from native SolidWorks builds and geometric acceptance tests.", "",
             "| Source | Native pixels | Scope | Extracted dimensions | Unresolved reading checks |",
             "|---|---|---|---|---|"]
    for report in summary["cases"]:
        dimensions = sum(len(p["read"]["dims"]) for p in report.get("read_pages", [])) if "read_pages" in report else "not run"
        unresolved = len(report.get("drawing_model", {}).get("issues", [])) if "drawing_model" in report else "not run"
        size = ", ".join(f"{p['width']}×{p['height']}" for p in report["pages"])
        kind = ", ".join(a["document_kind"] for a in report["assessments"]) or "not assessed"
        lines.append(f"| {Path(report['source']).name} | {size} | {kind} | {dimensions} | {unresolved} |")
    for report in summary["cases"]:
        lines.extend(["", f"## {Path(report['source']).name}", ""])
        if len(report.get("aliases", [])) > 1:
            lines.append("Identical sources: " + ", ".join(report["aliases"]) + ".")
        for assessment in report["assessments"]:
            lines.extend([assessment["summary"], ""])
        if report.get("error"):
            lines.extend(["Run error: " + report["error"], ""])
        lines.extend("- " + b for b in report["readiness"]["blockers"])
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--inputs", type=Path)
    parser.add_argument("--files", nargs="*", type=Path, default=[])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--vision", action="store_true")
    parser.add_argument("--read", action="store_true", help="also run the actual whole-page/crop dimension reader")
    parser.add_argument("--llm", choices=("domo", "gemini"))
    parser.add_argument("--workers", type=int, choices=(1, 2), default=2)
    args = parser.parse_args()
    files = list(args.files)
    if args.inputs:
        files.extend(sorted(p for p in args.inputs.iterdir() if p.suffix.lower() in {".pdf", ".png", ".jpg", ".jpeg", ".webp"}))
    if not files:
        parser.error("Give --inputs or --files")
    unique, aliases = {}, {}
    for path in files:
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        unique.setdefault(sha, path)
        aliases.setdefault(sha, []).append(str(path))
    args.out.mkdir(parents=True, exist_ok=True)
    reports = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        pending = {pool.submit(inspect_file, p, args.out, args.vision, args.llm, args.read): sha for sha, p in unique.items()}
        for future in as_completed(pending):
            result = future.result()
            result["aliases"] = aliases[pending[future]]
            reports.append(result)
            print(f"{Path(result['source']).name}: {'ERROR ' + result['error'] if 'error' in result else result['readiness']['status']}", flush=True)
    summary = {"files": len(files), "unique_sources": len(unique), "duplicates": len(files)-len(unique),
               "vision_requested": args.vision, "dimension_read_requested": args.read,
               "accuracy_verified": False, "cases": sorted(reports, key=lambda r: r["source"])}
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    (args.out / "report.md").write_text(markdown(summary), encoding="utf-8")
    print(f"Report: {args.out / 'summary.json'}", flush=True)
    return 2 if any("error" in r for r in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())
