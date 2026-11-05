"""Offline checks of phase P2: ingest (pages from any file) and segmentation (code): python tests/test_perception.py"""
import io
import tempfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

import fakes  # noqa: F401

from app.cache import Cache
from app.ingest import file_part, load_pages
from app.llm.base import LLMError
from app.perception.segment import segment_page, segment_png


def picture(fmt: str, size=(400, 300)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, "white").save(buf, fmt)
    return buf.getvalue()


def saved(data: bytes, name: str) -> Path:
    p = Path(tempfile.mkdtemp(prefix="ingest-")) / name
    p.write_bytes(data)
    return p


def sheet() -> bytes:
    """A1-ish test sheet: a frame with a title-block grid, two separate views, and two views joined by one long thin dimension line."""
    img = np.full((900, 1400), 255, np.uint8)
    cv2.rectangle(img, (20, 20), (1380, 880), 0, 2)                                     # frame
    for y in (760, 800, 840):                                                            # title block grid drawn onto the frame
        cv2.line(img, (1000, y), (1380, y), 0, 1)
    for x in (1100, 1250):
        cv2.line(img, (x, 760), (x, 880), 0, 1)
    views = [(100, 100, 400, 350), (600, 100, 900, 350), (100, 500, 350, 700), (480, 500, 760, 700)]
    for x0, y0, x1, y1 in views:                                                         # each view: an outline, holes, hatching
        cv2.rectangle(img, (x0, y0), (x1, y1), 0, 2)
        for k in range(4):
            cv2.circle(img, (x0 + (k + 1) * (x1 - x0) // 5, (y0 + y1) // 2), 18, 0, 2)
        for d in range(x0 - (y1 - y0) // 2, x1, 12):                                    # hatching over the upper half (a section)
            cv2.line(img, (max(x0, d), y0 + max(0, x0 - d)), (min(x1, d + (y1 - y0) // 2), y0 + min((y1 - y0) // 2, x1 - d)), 0, 1)
    cv2.line(img, (350, 690), (480, 690), 0, 1)                                          # a dimension line joining views 3 and 4
    ok, buf = cv2.imencode(".png", img)
    return buf.tobytes()


def test_file_type_comes_from_the_bytes():
    assert file_part(saved(picture("WEBP"), "drawing.jpg")).mime_type == "image/webp"   # inputs\image_2.jpg was a WebP
    assert file_part(saved(picture("PNG"), "drawing.jpg")).mime_type == "image/png"
    avif = saved(picture("AVIF"), "drawing.png")                                       # inputs\image_9.png was an AVIF: models get a PNG
    part = file_part(avif)
    assert part.mime_type == "image/png" and Image.open(io.BytesIO(part.data)).format == "PNG"
    try:
        file_part(saved(b"not a drawing", "x.png"))
        raise AssertionError("expected LLMError")
    except LLMError:
        pass


def test_pdf_pages_and_the_page_cache():
    pages = [Image.new("RGB", (300, 200), "white"), Image.new("RGB", (200, 300), "white")]
    buf = io.BytesIO()
    pages[0].save(buf, "PDF", save_all=True, append_images=pages[1:], resolution=100)
    pdf = saved(buf.getvalue(), "set.pdf")
    cache = Cache(tempfile.mkdtemp())
    got = load_pages([pdf, saved(picture("JPEG"), "extra.jpg")], dpi=100, cache=cache)
    assert [(p.no, p.source_page, p.width, p.height, p.dpi) for p in got] == [(1, 1, 300, 200, 100), (2, 2, 200, 300, 100), (3, 1, 400, 300, None)]
    assert all(Image.open(io.BytesIO(p.png)).format == "PNG" for p in got)
    again = load_pages([pdf], dpi=100, cache=cache)                                    # the second time comes from the cache
    assert [p.sha for p in again] == [p.sha for p in got[:2]]
    assert cache.stats()[0]["layer"] == "pages"


def test_segmentation_finds_each_view_and_drops_the_frame():
    png = sheet()
    boxes = [b.box for b in segment_png(png) if b.kind == "view"]
    centres = [(250, 225), (750, 225), (225, 600), (620, 600)]
    for cx, cy in centres:
        holders = [b for b in boxes if b[0] <= cx <= b[2] and b[1] <= cy <= b[3]]
        assert holders, f"no box holds the view at {(cx, cy)}: {boxes}"
        for b in holders:                                                               # never two views in one box
            assert sum(b[0] <= x <= b[2] and b[1] <= y <= b[3] for x, y in centres) == 1, (b, boxes)
    assert not any(b[2] - b[0] > 1000 for b in boxes), "the frame must not become a box"
    cache = Cache(tempfile.mkdtemp())
    assert segment_page(png, "sha1", cache) == segment_page(png, "sha1", cache)      # C2 cache round trip


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
