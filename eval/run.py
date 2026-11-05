r"""Score the system on the benchmark (eval/benchmark.json). Results go to eval\runs\<time>\ (scores.json + per-set files).

  .venv\Scripts\python eval\run.py --segment                 views: code segmentation (no model, no SolidWorks)       [P2]
  .venv\Scripts\python eval\run.py --read [--llm gemini|domo] dimensions: today's single-image reader (needs a model key) [P0 baseline]
  .venv\Scripts\python eval\run.py --segment --read --sets s02 s11
Model calls go through the cache, so a second run of the same sets costs nothing (--no-cache forces real calls).
Every truth entry is DRAFTED (verified=false in benchmark.json) until an engineer checks it: treat the scores as indicative until then.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval.score import score_dimensions, score_views  # noqa: E402

BENCH_DPI = 150                 # PDF pages of the benchmark are stored at 150 DPI; the truth points are in those pixels


def load_sets(only: list[str] | None) -> list[dict]:
    bench = json.loads((ROOT / "eval" / "benchmark.json").read_text(encoding="utf-8"))["sets"]
    by_id = {s["id"]: s for s in bench}
    for s in bench:
        if isinstance(s["dimensions"], str):                       # "same as <id>"
            s["dimensions"] = by_id[s["dimensions"].removeprefix("same as ").strip()]["dimensions"]
    return [s for s in bench if not only or any(s["id"].startswith(o) for o in only)]


def overlay(png: bytes, boxes, points, path: Path) -> None:
    import cv2
    import numpy as np
    img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    for i, b in enumerate(boxes, 1):
        colour = (0, 160, 0) if b.kind == "view" else (200, 120, 0)
        cv2.rectangle(img, tuple(b.box[:2]), tuple(b.box[2:]), colour, 2)
        cv2.putText(img, f"{i}{'' if b.kind == 'view' else ' table'}", (b.box[0] + 4, b.box[1] + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.6, colour, 2)
    for v in points:                                                   # truth: the view point (filled) and its extent points (rings)
        cv2.circle(img, tuple(int(c) for c in v["point"]), 7, (0, 0, 255), -1)
        for x, y in v.get("extent", []):
            cv2.circle(img, (int(x), int(y)), 6, (0, 0, 255), 2)
    cv2.imwrite(str(path), img)


def run_segment(s: dict, out: Path) -> dict:
    from app.ingest import load_pages
    from app.perception.segment import segment_png
    pages = load_pages([ROOT / p for p in s["pages"]], dpi=BENCH_DPI)
    boxes = {}
    for page in pages:
        found = segment_png(page.png)
        boxes[page.no] = [b.box for b in found if b.kind == "view"]
        overlay(page.png, found, [v for v in s["views"] if v["page"] == page.no], out / f"{s['id']}_p{page.no}.png")
    return score_views(s["views"], boxes)


def run_p3(s: dict, client, out: Path) -> dict:
    """Phase P3 reader (2 calls a page: whole page + packed view crops): dimension recall / precision, view naming, calls, tokens."""
    from dataclasses import asdict
    from app.ingest import load_pages
    from app.perception.extract import read_page
    from app.perception.segment import segment_png
    from eval.score import _inside, score_dimension_set
    found, calls, named, typed, coarse, reads = [], 0, 0, 0, 0, []
    kind = lambda t: "ortho" if t in ("plan", "front", "side") else t   # what the build needs: a normal view, section, detail or 3D picture
    usage = {"prompt": 0, "output": 0}
    wrap = _Counting(client, usage)
    for page in load_pages([ROOT / p for p in s["pages"]], dpi=BENCH_DPI):
        boxes = [b.box for b in segment_png(page.png) if b.kind == "view"]
        r = read_page(wrap, page.no, page.png, boxes)
        reads.append(asdict(r))
        calls += r.calls
        found += [d.text for d in r.dims]
        for v in [t for t in s["views"] if t["page"] == page.no]:              # naming: the crop holding the view's point, its type
            crop = next((c for c in r.views if _inside(v["point"], c.box)), None)
            if crop is not None:
                named += 1
                right = [v["type"], *v.get("also", [])]                          # a hatched front view is also a section
                typed += crop.type in right
                coarse += kind(crop.type) in [kind(t) for t in right]
    (out / f"{s['id']}_p3.json").write_text(json.dumps(reads, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    return {**score_dimension_set(s["dimensions"], found), "calls": calls, "views": len(s["views"]), "typed": typed, "coarse": coarse, "named": named, **usage}


class _Counting:
    """Passes calls through and adds up the tokens they used (a cache hit adds none)."""

    def __init__(self, inner, usage):
        self.inner, self.usage = inner, usage
        self.model = inner.model

    def generate_json(self, system, parts):
        resp = self.inner.generate_json(system, parts)
        self.usage["prompt"] += resp.usage.get("prompt_tokens", 0)
        self.usage["output"] += resp.usage.get("output_tokens", 0)
        return resp


def run_read(s: dict, client, out: Path) -> dict:
    """Today's reader: ONE image per call. A multi-page set cannot be read by it (that is what phases P2-P4 change)."""
    from app.agent.prompts import reader_system
    from app.ingest import file_part
    from app.llm import LLMError
    if len(s["pages"]) != 1 or s["pages"][0].lower().endswith(".pdf"):
        return {"skipped": "multi-page set: today's reader takes one image", **score_dimensions(s["dimensions"], [], s["dimensions_complete"])}
    try:
        resp = client.generate_json(reader_system(), [file_part(ROOT / s["pages"][0])])
    except LLMError as ex:
        return {"error": str(ex)}
    (out / f"{s['id']}_reader.json").write_text(json.dumps(resp.data, indent=1, ensure_ascii=False), encoding="utf-8")
    seen = resp.data.get("seen_dimensions", []) if isinstance(resp.data, dict) else []
    return {**score_dimensions(s["dimensions"], seen, s["dimensions_complete"]), "items": len(resp.data.get("items", [])) if isinstance(resp.data, dict) else 0,
            "cached": bool(resp.usage.get("cached"))}


def fmt(v) -> str:
    return "  -  " if v is None else f"{v * 100:5.1f}%" if isinstance(v, float) else str(v)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--segment", action="store_true", help="score view segmentation")
    ap.add_argument("--read", action="store_true", help="score today's reader (dimension recall/precision); needs a model key")
    ap.add_argument("--p3", action="store_true", help="the phase P3 reader (whole page + packed view crops): dimension recall, naming, calls, tokens")
    ap.add_argument("--llm", choices=("gemini", "domo"))
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--sets", nargs="*", help="only sets whose id starts with these")
    args = ap.parse_args()
    if not (args.segment or args.read or args.p3):
        ap.error("give --segment, --read and/or --p3")
    client = None
    if args.read or args.p3:
        from app.llm import LLMError, make_client
        try:
            client = make_client(args.llm, cache=False if args.no_cache else None)
        except LLMError as ex:
            print(f"--read needs a model: {ex}")
            return 2
    out = ROOT / "eval" / "runs" / datetime.now().strftime("%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    results = {}
    for s in load_sets(args.sets):
        r = {}
        if args.segment:
            r["views"] = run_segment(s, out)
        if args.read:
            r["dimensions"] = run_read(s, client, out)
        if args.p3:
            r["p3"] = run_p3(s, client, out)
            print(f"{s['id']:28} recall {fmt(r['p3']['recall'])} precision {fmt(r['p3']['precision'])} calls {r['p3']['calls']} "
                  f"tokens {r['p3']['prompt']}+{r['p3']['output']} views typed {r['p3']['typed']}|{r['p3']['coarse']}/{r['p3']['views']}", flush=True)
        results[s["id"]] = r
    (out / "scores.json").write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
    if args.p3:
        t = {k: sum(r["p3"][k] for r in results.values()) for k in ("matched", "truth", "predicted", "calls", "prompt", "output", "typed", "coarse", "views")}
        print(f"\nP3 ALL: recall {fmt(t['matched'] / t['truth'])}  precision {fmt(t['matched'] / max(1, t['predicted']))}  calls {t['calls']}  "
              f"tokens {t['prompt']} in + {t['output']} out  view type right {t['typed']}/{t['views']} exact, {t['coarse']}/{t['views']} normal/section/detail/3D")

    print(f"{'set':28} " + ("views whole cover boxes merged  whole%  cover%  precision   " if args.segment else "") + ("dims recall precision" if args.read else ""))
    tot = {"views": 0, "found": 0, "coverable": 0, "merged": 0, "boxes": 0, "good": 0, "truth": 0, "matched": 0}
    for sid, r in results.items():
        line = f"{sid:28} "
        if "views" in r:
            v = r["views"]
            line += (f"{v['views']:>5} {v['found']:>5} {v['coverable']:>5} {v['boxes']:>5} {v['merged']:>6}  {fmt(v['recall'])}  {fmt(v['coverable_recall'])}"
                     f"  {fmt(v['precision'])}   ")
            for k in ("views", "found", "coverable", "merged", "boxes"):
                tot[k] += v[k]
            tot["good"] += round(v["precision"] * v["boxes"])
        if "dimensions" in r:
            d = r["dimensions"]
            line += d.get("error") or f"{fmt(d.get('recall'))}  {fmt(d.get('precision'))}" + (f"  ({d['skipped']})" if d.get("skipped") else "")
            tot["truth"] += d.get("truth", 0)
            tot["matched"] += d.get("matched", 0)
        print(line)
    if args.segment:
        print(f"{'ALL':28} {tot['views']:>5} {tot['found']:>5} {tot['coverable']:>5} {tot['boxes']:>5} {tot['merged']:>6}  {fmt(tot['found'] / tot['views'])}"
              f"  {fmt(tot['coverable'] / tot['views'])}  {fmt(tot['good'] / max(1, tot['boxes']))}")
        print("whole = one box holds the whole view; cover = its fragments together hold it (the naming step merges them); merged = two views in one box")
    if args.read and tot["truth"]:
        print(f"dimension recall over all sets: {fmt(tot['matched'] / tot['truth'])}")
    print(f"\nresults and overlay pictures: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
