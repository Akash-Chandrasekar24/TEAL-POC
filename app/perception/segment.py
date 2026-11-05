"""SEGMENT (architecture 4.3 #5, cache layer C2): a drawing page -> the boxes of its views. Plain code (OpenCV), no model.

  1. ink = dark pixels (Otsu threshold on the grey page)
  2. the sheet frame, zone strips and long border lines are removed (lines longer than FRAME_FRACTION of the page side)
  3. ink is smeared by a gap proportional to the page size, so a view, its dimensions and its label become one blob
  4. blobs -> boxes; tiny boxes (stray text, zone letters) are dropped; boxes that overlap are merged
  5. tables and title blocks are recognised (mostly long straight lines in a grid) and returned with kind "table"

The model never draws boxes: it only names the views these boxes contain (extract.py).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import cv2
import numpy as np

from app.cache import Cache, key

CONTAIN_SHARE = 0.6              # a box with this share of its area inside a bigger box is part of it
SEGMENT_V = 13                 # table columns must span the grid, not only a section's narrow web
LAYER = "views"
BG_FRACTION = 0.025              # background is estimated over this share of the page diagonal (wider than any line)
INK_CONTRAST = 40                # grey levels darker than the local background that count as ink
FRAME_SPAN = 0.6                 # an ink piece wider AND taller than this share of the page is the sheet frame (+ title-block grid)
FRAME_FRACTION = 0.3             # a straight line longer than this share of the page side is frame / border, not part of a view
TEXT_HEIGHT_FRACTION = 0.035     # a box flatter than this share of the page height is a line of text (a view label), not a view
GAP_FRACTION = 0.004             # smear distance as a share of the page diagonal: views closer than ~2x this are merged
MIN_AREA_FRACTION = 0.004        # boxes smaller than this share of the page are text, not views
TABLE_LINE_SHARE = 0.45          # a table's ink is mostly long horizontal / vertical rules ...
TABLE_ROW_SPAN = 0.6             # ... rows run across this share of its width ...
TABLE_COLUMN_SPAN = 0.6          # ... columns also span the grid; short web edges are not table columns
TABLE_RULES = 3                  # ... at least this many each way ...
TABLE_MAX_FILL = 0.2             # ... and it is sparse (a shaded 3D picture is dense)
CORE_CLOSE = 0.015               # view cores: close gaps up to this share of the page diagonal (0 = no splitting) ...
CORE_OPEN = 0.015               # ... then remove anything thinner than this (the lines that join two views)
CORE_MIN_AREA = 0.008            # a core smaller than this share of the page is a detail of a view, not a view
FRAME_EDGE = 0.05               # a frame's ink lies within this share of its size from its own outline ...
FRAME_EDGE_SHARE = 0.5           # ... for at least this share of its ink


@dataclass
class ViewBox:
    box: list[int]               # [x0, y0, x1, y1] in page pixels
    kind: str                    # view | table
    ink: int                     # ink pixels inside (after frame removal)


def _ink(gray: np.ndarray) -> np.ndarray:
    """Pixels clearly darker than their LOCAL background: a grey gradient or a shaded 3D picture's fill is background, its edges are ink."""
    k = max(15, int(np.hypot(*gray.shape) * BG_FRACTION)) | 1
    background = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    return np.where(cv2.subtract(background, gray) > INK_CONTRAST, 255, 0).astype(np.uint8)


def _hollow(mask: np.ndarray) -> bool:
    """True when most of the piece's ink lies near its own bounding box edge: a frame. Views joined by a long dimension line are not hollow."""
    h, w = mask.shape
    m = max(2, int(min(h, w) * FRAME_EDGE))
    inner = int(cv2.countNonZero(mask[m:h - m, m:w - m])) if h > 2 * m and w > 2 * m else 0
    total = int(cv2.countNonZero(mask))
    return total > 0 and (total - inner) / total >= FRAME_EDGE_SHARE


def _drop_frame(ink: np.ndarray) -> np.ndarray:
    """Remove every HOLLOW ink piece that spans most of the page both ways: the sheet frame, with its zone ticks and the title-block grid drawn onto it."""
    hgt, wid = ink.shape
    n, labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    frame = []
    for i in range(1, n):
        x, y, w, h = stats[i, :4]
        if w > wid * FRAME_SPAN and h > hgt * FRAME_SPAN and _hollow((labels[y:y + h, x:x + w] == i).astype(np.uint8)):
            frame.append(i)
    if frame:
        ink = ink.copy()
        ink[np.isin(labels, frame)] = 0
    return ink


def _long_lines(ink: np.ndarray, length_h: int, length_v: int) -> np.ndarray:
    h = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, length_h), 1)))
    v = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(3, length_v))))
    return cv2.bitwise_or(h, v)


def _is_table(part: np.ndarray, total: int, dense_only: bool = False) -> bool:
    """A table or title block: sparse ink, mostly straight rules, at least TABLE_RULES rows running across most of its width, and columns. A
    shaded 3D picture is dense; a view's outline edges, or two rectangular views side by side, do not give rows across the whole box."""
    h, w = part.shape
    rows = cv2.morphologyEx(part, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(8, int(w * TABLE_ROW_SPAN)), 1)))
    cols = cv2.morphologyEx(part, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(8, int(h * TABLE_COLUMN_SPAN)))))
    n_rows = cv2.connectedComponents(rows)[0] - 1
    n_cols = cv2.connectedComponents(cols)[0] - 1
    share = (cv2.countNonZero(rows) + cv2.countNonZero(cols)) / total
    if n_rows >= 8 and n_cols >= 3 and share >= TABLE_LINE_SHARE:
        return True  # dense low-resolution BOMs have substantial text ink
    if dense_only:
        return False
    if total / max(1, h * w) > TABLE_MAX_FILL:
        return False
    return n_rows >= TABLE_RULES and n_cols >= TABLE_RULES and share >= TABLE_LINE_SHARE


def screenshot_canvas(png: bytes) -> list[int] | None:
    """Conservative beige CAD canvas detection; keep all coordinates in the source.

    Ordinary white sheets and colored geometry do not trigger this. This masks
    chrome only for segmentation; the untouched whole page remains model evidence.
    """
    rgb = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    if rgb is None:
        return None
    h, w = rgb.shape[:2]
    quantized = (rgb // 8).astype(np.int32)
    keys = quantized[:, :, 0]*1024 + quantized[:, :, 1]*32 + quantized[:, :, 2]
    mode = int(np.bincount(keys.ravel(), minlength=32768).argmax())
    color = np.array([mode//1024, (mode//32)%32, mode%32])*8 + 4
    if color.min() < 180 or not 8 <= int(color.max()-color.min()) <= 48:
        return None
    mask = cv2.inRange(rgb, np.maximum(0, color-8).astype(np.uint8), np.minimum(255, color+8).astype(np.uint8))
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if n < 2:
        return None
    x, y, bw, bh, area = max(stats[1:], key=lambda s: s[4])
    if area < w*h*.25 or bw < w*.6 or bh < h*.6 or x < w*.05 or y < h*.05:
        return None
    return [int(x), int(y), int(x+bw), int(y+bh)]


def _area(b: list[int]) -> int:
    return max(0, b[2] - b[0]) * max(0, b[3] - b[1])


def _merge(boxes: list[list[int]]) -> list[list[int]]:
    """Merge a box into another when it lies mostly INSIDE it (a label or a dimension inside a view's box). Boxes that only touch at a corner
    stay apart: the box of an L-shaped view often covers a corner of its neighbour, and merging those would chain every view into one."""
    boxes = sorted((b[:] for b in boxes), key=_area, reverse=True)
    changed = True
    while changed:
        changed = False
        out: list[list[int]] = []
        for b in boxes:
            for o in out:
                inter = [max(o[0], b[0]), max(o[1], b[1]), min(o[2], b[2]), min(o[3], b[3])]
                if _area(inter) >= CONTAIN_SHARE * min(_area(b), _area(o)):
                    o[:] = [min(o[0], b[0]), min(o[1], b[1]), max(o[2], b[2]), max(o[3], b[3])]
                    changed = True
                    break
            else:
                out.append(b)
        boxes = out
    return boxes


def _split_joined(ink: np.ndarray, boxes: list[list[int]]) -> list[list[int]]:
    """Views that thin ink joins (a dimension, extension or leader line running into the next view) form one blob. Each view has a dense CORE:
    closing fills the space between its lines, opening then removes everything thinner than a view (the joining lines). A box with 2+ cores is
    split: every ink pixel in it goes to its NEAREST core, so the parts together still hold all of the box's ink (a view split by mistake is
    still whole as the union of its parts, which the naming step merges back). A box with 0-1 cores is kept as it is."""
    if not CORE_CLOSE:
        return boxes
    hgt, wid = ink.shape
    diag = np.hypot(wid, hgt)
    k_close, k_open = max(3, int(diag * CORE_CLOSE)), max(3, int(diag * CORE_OPEN))
    solid = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k_close, k_close)))
    cores = cv2.morphologyEx(solid, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k_open, k_open)))
    n, labels, stats, cent = cv2.connectedComponentsWithStats(cores, connectivity=8)
    min_core = wid * hgt * CORE_MIN_AREA
    big = [i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] >= min_core]
    out = []
    for b in boxes:
        x0, y0, x1, y1 = b
        inside = [i for i in big if x0 <= cent[i][0] <= x1 and y0 <= cent[i][1] <= y1]
        if len(inside) < 2:
            out.append(b)
            continue
        ys, xs = np.nonzero(ink[y0:y1, x0:x1])
        # ponytail: one distance transform per core over the box; fine for the few cores a page has
        dist = np.stack([cv2.distanceTransform(np.where(labels[y0:y1, x0:x1] == i, 0, 255).astype(np.uint8), cv2.DIST_L2, 3)[ys, xs]
                         for i in inside])
        owner = dist.argmin(axis=0)
        for k in range(len(inside)):
            px, py = xs[owner == k], ys[owner == k]
            if len(px):
                out.append([x0 + int(px.min()), y0 + int(py.min()), x0 + int(px.max()) + 1, y0 + int(py.max()) + 1])
    return out


def segment_png(png: bytes) -> list[ViewBox]:
    gray = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise ValueError("the page is not a readable picture")
    hgt, wid = gray.shape
    original_ink = _ink(gray)
    canvas = screenshot_canvas(png)
    if canvas is not None:
        x0, y0, x1, y1 = canvas
        masked = np.zeros_like(original_ink)
        masked[y0:y1, x0:x1] = original_ink[y0:y1, x0:x1]
        original_ink = masked
    ink = _drop_frame(original_ink)
    long_line = int(max(wid, hgt) * FRAME_FRACTION)                   # the LONGER side: a narrow page must not lose a view's edges
    ink = cv2.subtract(ink, _long_lines(ink, long_line, long_line))
    gap = max(3, int(np.hypot(wid, hgt) * GAP_FRACTION))
    blob = cv2.dilate(ink, cv2.getStructuringElement(cv2.MORPH_RECT, (gap, gap)))
    n, _, stats, _ = cv2.connectedComponentsWithStats(blob, connectivity=8)
    min_area = wid * hgt * MIN_AREA_FRACTION
    raw = [[int(x), int(y), int(x + w), int(y + h)] for x, y, w, h, area in stats[1:]
           if w * h >= min_area and min(w, h) >= hgt * TEXT_HEIGHT_FRACTION + gap]          # a line of text or a strip of frame is not a view
    raw = _split_joined(ink, raw)
    out = []
    for b in _merge(raw):
        x0, y0, x1, y1 = b
        part = ink[y0:y1, x0:x1]
        total = int(cv2.countNonZero(part))
        if total == 0:
            continue
        source_part = original_ink[y0:y1, x0:x1]
        source_total = int(cv2.countNonZero(source_part))
        is_table = _is_table(part, total) or _is_table(source_part, max(1, source_total), dense_only=True)
        out.append(ViewBox(b, "table" if is_table else "view", total))
    out.sort(key=lambda v: (v.box[1] // max(1, hgt // 6), v.box[0]))      # reading order: rows top to bottom, then left to right
    return out


def segment_page(png: bytes, sha: str, cache: Cache | None = None, tenant: str = "default") -> list[ViewBox]:
    """segment_png with cache layer C2 (key: page sha + SEGMENT_V). Traced: boxes found, tables left out, cache hit, time."""
    from app import trace
    with trace.current().step("segment", "page", page_sha=sha[:12], version=SEGMENT_V) as out:
        k = key(SEGMENT_V, sha)
        boxes, cached = None, False
        if cache is not None:
            hit = cache.get_json(tenant, LAYER, k)
            if hit is not None:
                boxes, cached = [ViewBox(**v) for v in hit], True
        if boxes is None:
            boxes = segment_png(png)
            if cache is not None:
                cache.put_json(tenant, LAYER, k, [asdict(v) for v in boxes], versions={"segment": SEGMENT_V})
        views = [b for b in boxes if b.kind == "view"]
        out.update(cached=cached, views=len(views), tables=len(boxes) - len(views), boxes=[b.box for b in views],
                   table_boxes=[b.box for b in boxes if b.kind != "view"])
    return boxes
