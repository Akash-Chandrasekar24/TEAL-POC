"""Experiment (not used by the app): read each locally-segmented view in parallel, merge, then re-read the doubtful callouts close up.
  1. OCR + OpenCV view segmentation (local, no model call)
  2. one short read per view crop, in parallel; each callout carries a position and a "rot" flag (rotated text)
  3. cross-check with OCR: a callout OCR also found at the same place is accepted; the others are "doubtful"
  4. verify the doubtful rotated/symbol callouts with a close-up (shown upright and rotated both ways), in parallel; replace the text
  .venv\\Scripts\\python eval\\tiled_experiment.py inputs\\image_11_triple_clamp.png --reference eval\\hybrid_triple_clamp_reference.json
"""
import argparse, io, json, os, re, sys, time, uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
parser = argparse.ArgumentParser()
parser.add_argument("input", type=Path)
parser.add_argument("--reference", type=Path, required=True)
parser.add_argument("--effort", default="low", help="effort of the crop reads")
parser.add_argument("--verify-effort", default="medium", help="effort of the close-up verifications")
parser.add_argument("--workers", type=int, default=3)
parser.add_argument("--scale", type=float, default=2.0, help="upscale each crop (Domo shrinks anything over 2048 px)")
parser.add_argument("--max-verify", type=int, default=14)
parser.add_argument("--no-verify", action="store_true")
parser.add_argument("--out", type=Path)
args = parser.parse_args()
os.environ["DOMO_MAX_IN_FLIGHT"] = str(args.workers)      # must be set before app.llm.domo is imported

from PIL import Image
from app import config, trace
from app.ingest.pages import load_pages
from app.llm import make_client
from app.llm.base import FilePart
from app.perception.ocr import read_ocr
from app.perception.segment import segment_page
from eval.hybrid_score import normalize

PROMPT = '''This is ONE view cropped from an engineering drawing. Transcribe EVERY printed dimension and callout in this crop, exactly as printed:
all digits, diameter/radius/degree symbols, tolerances, quantities, threads. Include rotated and small text. Do not calculate, merge or invent anything.
Return JSON only: {"callouts":[{"text":"exact printed text","category":"measurement|symbol|note","x":0,"y":0,"rot":false}]}
x,y = centre of that text inside this crop, normalized 0..1000. rot = true when the text is rotated, vertical or slanted (not horizontal).
List each printed occurrence separately, even when two read the same.'''

VERIFY = '''Close-up of a small region of an engineering drawing{shown}. A first read of the dimension nearest the centre gave "{text}", which may be wrong
(typical errors: diameter symbol read as degree or radius, a dropped or added digit). Look only at the text nearest the centre and report exactly what is printed.
Return JSON only: {{"text":"exact printed text","sure":true}}'''


def panel(image, cx, cy, half, scale):
    box = (max(0, round(cx - half)), max(0, round(cy - half)), min(image.width, round(cx + half)), min(image.height, round(cy + half)))
    crop = image.crop(box)
    return crop.resize((round(crop.width * scale), round(crop.height * scale)), Image.LANCZOS)


def main():
    started = time.monotonic(); rid = uuid.uuid4().hex[:12]
    out = args.out or ROOT / "eval" / "runs" / "tiled" / f"{args.input.stem}-{args.effort}-v2-{rid}"
    out.mkdir(parents=True, exist_ok=True)
    reference = json.loads(args.reference.read_text(encoding="utf-8"))
    expected = Counter(normalize(t) for t in reference["measurement_texts"])

    def scored(items):
        predicted = Counter(normalize(c["text"]) for c in items if c["category"] == "measurement")
        return {"matched": sum((expected & predicted).values()), "expected": sum(expected.values()), "predicted": sum(predicted.values()),
                "missed": list((expected - predicted).elements()), "extra": list((predicted - expected).elements())}

    with trace.run(rid, out / "trace"):
        page = load_pages([args.input])[0]
        marks = {}
        t0 = time.monotonic(); ocr = read_ocr(page.png); marks["ocr_s"] = round(time.monotonic() - t0, 2)
        t0 = time.monotonic(); boxes = [b for b in segment_page(page.png, page.sha) if b.kind == "view"]; marks["segment_s"] = round(time.monotonic() - t0, 2)
        client = make_client(config.WORKER_LLM_PROVIDER, cache=False, reasoning_effort=args.effort)
        verifier = make_client(config.WORKER_LLM_PROVIDER, cache=False, reasoning_effort=args.verify_effort)
        image = Image.open(io.BytesIO(page.png)).convert("RGB")

        def read(item):
            index, box = item
            left, top, right, bottom = box.box
            crop = image.crop((left, top, right, bottom))
            crop = crop.resize((round(crop.width * args.scale), round(crop.height * args.scale)), Image.LANCZOS)
            buffer = io.BytesIO(); crop.save(buffer, "PNG")
            t = time.monotonic()
            reply = client.generate_json(PROMPT, [FilePart(buffer.getvalue(), "image/png")])
            return index, box.box, reply.data, time.monotonic() - t, reply.usage

        t0 = time.monotonic()
        # longest-first: the biggest crops hold the most text
        order = sorted(enumerate(boxes, 1), key=lambda i: -((i[1].box[2] - i[1].box[0]) * (i[1].box[3] - i[1].box[1])))
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            results = list(pool.map(read, order))
        marks["crops_s"] = round(time.monotonic() - t0, 1)
        (out / "raw.json").write_text(json.dumps([[r[0], r[1], r[2], round(r[3], 1), r[4]] for r in results], ensure_ascii=False), encoding="utf-8")

        merged = []
        for index, (left, top, right, bottom), data, seconds, usage in results:
            for c in (data.get("callouts") or []) if isinstance(data, dict) else []:
                text = str(c.get("text", "")).strip()
                if not text:
                    continue
                x = left + (right - left) * float(c.get("x", 500)) / 1000
                y = top + (bottom - top) * float(c.get("y", 500)) / 1000
                if any(normalize(text) == normalize(m["text"]) and abs(x - m["x"]) < 30 and abs(y - m["y"]) < 30 for m in merged):
                    continue
                merged.append({"text": text, "category": c.get("category", "measurement"), "x": x, "y": y, "rot": bool(c.get("rot")), "crop": index})
        before = scored(merged)

        def agreed(m):   # OCR read the same text close to this position
            key = normalize(m["text"])
            return any(key and key in normalize(line["text"]) and abs((line["box"][0] + line["box"][2]) / 2 - m["x"]) < 90 and abs((line["box"][1] + line["box"][3]) / 2 - m["y"]) < 90
                       for line in ocr["lines"])
        for m in merged:
            m["ocr_agrees"] = agreed(m)
        symbol = re.compile(r"[ØøΦφ⌀°]|^\s*R\s*\d|°")
        doubtful = [m for m in merged if m["category"] == "measurement" and not m["ocr_agrees"] and (m["rot"] or symbol.search(m["text"]))][:args.max_verify]

        changes = []
        if not args.no_verify and doubtful:
            def verify(m):
                half, scale = 80, 4
                base = panel(image, m["x"], m["y"], half, scale)
                if m["rot"]:
                    panels = [base, base.rotate(-90, expand=True), base.rotate(90, expand=True)]   # as-is, clockwise, counter-clockwise
                    sheet = Image.new("RGB", (sum(p.width for p in panels) + 20, max(p.height for p in panels)), "white")
                    x = 0
                    for p in panels:
                        sheet.paste(p, (x, 0)); x += p.width + 10
                    shown = " shown three times: as-is, rotated 90° clockwise, rotated 90° counter-clockwise (read the copy where the text is upright)"
                else:
                    sheet, shown = base, ""
                if sheet.width > 2000:
                    sheet = sheet.resize((2000, round(sheet.height * 2000 / sheet.width)), Image.LANCZOS)
                buffer = io.BytesIO(); sheet.save(buffer, "PNG")
                reply = verifier.generate_json(VERIFY.format(shown=shown, text=m["text"]), [FilePart(buffer.getvalue(), "image/png")])
                return m, reply.data
            t0 = time.monotonic()
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                verdicts = list(pool.map(verify, doubtful))
            marks["verify_s"] = round(time.monotonic() - t0, 1)
            for m, data in verdicts:
                new = str(data.get("text", "")).strip() if isinstance(data, dict) else ""
                if new and normalize(new) != normalize(m["text"]):
                    changes.append({"was": m["text"], "now": new, "sure": data.get("sure"), "at": [round(m["x"]), round(m["y"])]})
                    m["text"] = new
        after = scored(merged)
    total = time.monotonic() - started
    report = {"input": str(args.input), "effort": args.effort, "verify_effort": args.verify_effort, "workers": args.workers, "crops": len(boxes),
              "total_seconds": round(total, 1), **marks, "callouts": len(merged), "ocr_agreed": sum(m["ocr_agrees"] for m in merged), "verified": len(doubtful),
              "changes": changes, "before": before, "after": after}
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


main()
