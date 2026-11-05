r"""Read a drawing set (phases P2 + P3): pages -> view boxes (code) -> dimensions per view (model, 2 calls a page).

  .venv\Scripts\python scripts\views.py drawing.pdf sheet2.png --out out_dir                boxes only (no model call)
  .venv\Scripts\python scripts\views.py drawing.pdf --out out_dir --read [--llm gemini|domo]  + dimensions, view names, title block

Writes to out_dir: page_<n>.png (the page with its numbered view boxes) and, with --read, read.json: every dimension with the crop(s) it was
read in (`single_source` = only one of the two reads saw it: worth a human look), the crops' view names, the title block, the call count.
Pages, boxes and model replies are cached (storage\cache), so running it again on the same files is instant and costs nothing.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import config, trace  # noqa: E402
from app.cache import default_cache  # noqa: E402
from app.ingest import load_pages  # noqa: E402
from app.perception.extract import mark_boxes  # noqa: E402
from app.agent.orchestrator import Orchestrator  # noqa: E402
from app.agent.workers import ExtractionAgent  # noqa: E402
from app.perception.segment import segment_page  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", help="PDF and / or picture files of ONE drawing set")
    ap.add_argument("--out", required=True)
    ap.add_argument("--dpi", type=int, default=300, help="PDF render resolution (default 300)")
    ap.add_argument("--read", action="store_true", help="read the dimensions and name the views (model: 2 calls a page)")
    ap.add_argument("--llm", choices=("gemini", "domo"))
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cache = None if args.no_cache else default_cache()
    llm = None
    if args.read:
        from app.llm import LLMError, make_client
        try:
            llm = make_client(args.llm or config.WORKER_LLM_PROVIDER, cache=not args.no_cache, reasoning_effort=config.EXTRACTION_REASONING_EFFORT)
            supervisor = Orchestrator(extraction=ExtractionAgent(llm))
        except LLMError as ex:
            print(f"--read needs a model: {ex}")
            return 2
    rid = uuid.uuid4().hex[:12]
    with trace.run(rid, out / "trace", save_files=config.TRACE_FILES, files=[Path(f).name for f in args.files], read=args.read):
        pages = load_pages(args.files, dpi=args.dpi, cache=cache, tenant=config.TENANT)
        reads = []
        for page in pages:
            found = segment_page(page.png, page.sha, cache=cache, tenant=config.TENANT)
            boxes = [b for b in found if b.kind == "view"]                       # crop numbers on the picture = crop numbers in read.json
            (out / f"page_{page.no}.png").write_bytes(mark_boxes(page.png, [b.box for b in boxes]))
            print(f"page {page.no} ({Path(page.source).name} p{page.source_page}, {page.width}x{page.height}): {len(boxes)} view boxes "
                  f"({len(found) - len(boxes)} table / title block left out)")
            if llm is not None:
                r = supervisor.read_page(page.no, page.png, [b.box for b in boxes])
                reads.append(asdict(r))
                for v in r.views:
                    print(f"  crop {v.no}: {v.type:8} {v.label!r} scale {v.scale}" + (f" (same view as crop {v.same_as})" if v.same_as else "")
                          + f" | {', '.join(v.dims) or '-'}")
                print(f"  {len(r.dims)} dimensions ({sum(d.single_source for d in r.dims)} seen by one read only, "
                      f"{sum(not d.views for d in r.dims)} not placed in a view), {r.calls} model calls")
        if reads:
            (out / "read.json").write_text(json.dumps(reads, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"written to {out} (process log: {out / 'trace' / 'run.log'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
