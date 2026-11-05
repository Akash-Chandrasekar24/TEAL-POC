"""Local positioned OCR. Scores unavailable from this engine remain unknown."""
from __future__ import annotations

import io
import os
from pathlib import Path
import tempfile
import time

from PIL import Image
from .ocr_worker import worker


def prewarm_ocr():
    """Load recognition once on a blank tile while idle; never cache a drawing result."""
    if os.name == "nt":
        tile=io.BytesIO()
        Image.new("RGB",(100,100),"white").save(tile,"PNG")
        read_ocr(tile.getvalue(),timeout=15)


def read_ocr(png: bytes, *, timeout: float = 30, language: str = "en-US") -> dict:
    if os.name != "nt":
        raise RuntimeError("Windows OCR is unavailable on this platform; configure an OCR adapter")
    started = time.monotonic()
    with Image.open(io.BytesIO(png)) as original:
        width, height = original.size
        direct=original.format=="PNG" and original.mode=="RGB" and max(width,height)<=10000
        if direct:
            sent_width,sent_height=width,height
        else:
            image = original.convert("RGB")
            image.thumbnail((10000, 10000))
            sent_width, sent_height = image.size
        with tempfile.TemporaryDirectory(prefix="drawing-ocr-") as folder:
            path = Path(folder) / "page.png"
            if direct: path.write_bytes(png)
            else: image.save(path)
            raw = worker.request({"path":str(path),"language":language},timeout)
    # Microsoft defines nonzero-angle word boxes in a rotated coordinate frame.
    # Keep that fact explicit; the visual agent must locate source occurrences.
    angle = raw.get("angle")
    frame = "source" if angle is None or abs(angle) < .01 else "ocr_rotated"
    lines = []
    for line in raw["lines"]:
        words = [{"text": word["text"], "box": [round(v * (width/sent_width if i%2==0 else height/sent_height), 2)
                  for i, v in enumerate(word["box"])]} for word in line["words"]]
        if not words:
            continue
        box = [min(w["box"][0] for w in words), min(w["box"][1] for w in words),
               max(w["box"][2] for w in words), max(w["box"][3] for w in words)]
        lines.append({"id": line["id"], "text": line["text"], "box": box, "words": words, "confidence": None})
    return {"engine": raw["engine"], "language": raw["language"], "angle": angle, "coordinate_frame": frame,
            "width": width, "height": height, "lines": lines, "seconds": round(time.monotonic()-started, 3)}
