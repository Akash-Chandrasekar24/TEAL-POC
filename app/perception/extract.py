"""READ (architecture 4.3 #6-#7, phase P3): a page -> its dimensions, each tied to the view it was read in. TWO model calls per page.

  call 1  the clean whole page  -> every dimension on it + title block + notes (the SAFETY NET: a bad crop can never lose a number)
  call 2  ONE sheet: a small overview of the page with the boxes numbered (captions printed beside a view are in sight) + the page's view
          crops at full size -> per crop: what view it is, its caption / scale, and the dimensions in it (zoomed in, attributed)

The two reads are combined: a dimension counts once (the larger count of the two reads); it gets the view(s) of the crop(s) it was read in;
one seen ONLY on the whole page has no view yet ("unplaced"), one seen by only one of the two reads is `single_source` (shown to the human).
Measured on the benchmark, Domo, 13 sets (eval/run.py --p3), all at 30 calls:
  naming views from the crops alone        recall 89.7 %, view kind right 49/62  (captions are outside the crops)
  naming on the page with boxes drawn      recall 85.7 %, view kind right 51/62  (drawn boxes cost the number reading)
  this layout                              recall 91.0 %, precision 76.8 %, view kind right 57/62, ~2.6k tokens a drawing
A call per crop instead of one packed sheet cost 103 calls for the same reads. The number of calls is what costs money: two a page.
"""
from __future__ import annotations

import io
import math
from collections import Counter
from dataclasses import dataclass, field

from PIL import Image, ImageDraw

from app.llm.base import FilePart, LLMClient
from .dims import dim_tokens
from app.drawing.confidence import RUBRIC_PROMPT,rubric_score

MAX_SIDE = 2048                 # Domo shrinks anything larger: a packed sheet never needs to be shrunk (more sheets instead)
GAP = 24                        # px between packed crops (room for the crop number)

_COMMON = ("List every dimension / size callout exactly as printed: 60, 12.50, Ø50, R35, 16°, 4xR6, 6xØ24, 2x45°, Prof. 28, 80.00 ±0.10. "
           "A callout printed in two places is listed twice. Not: title block text, border zone letters/numbers, view names, scale notes. "
           "Never guess a number you cannot read. JSON only.")
PAGE_PROMPT = ("A page of an engineering drawing. " + _COMMON +
               ' Reply {"d": [{"text":"complete printed callout","confidence_metrics":[5,5,3,5,5],"confidence_reason":"source evidence"}], "tb": {"name": "", "material": "", "unit": "mm|inch|unknown", "projection": "first|third|unknown",'
               ' "projection_symbol": {"status":"present|absent|unreadable","convention":"first|third|unknown","evidence":"visible symbol and relative cone/end-circle positions"},'
               ' "scale": "", "document_kind": "part|assembly|detail_sheet|mixed|unknown"}, "notes": [general notes as printed],'
               ' "issues": [locations of unreadable printed callouts]}. Inspect the projection symbol, including the title block. '
               'Use printed text or a legible symbol for projection; never infer it from country or view layout. '
               'Keep conflicting text/symbol readings separate. Classify the whole sheet. Ignore application chrome. '
               'Confidence ratings illustrate the format, not a default. Assess each reading. In this pass consistency cannot exceed 3 without identified supporting views.\n')
CROPS_PROMPT = ("Top: the whole page of an engineering drawing, small, with numbered blue boxes. Below: those boxes cut out at full size, "
                "marked with the same numbers in red. For every number: the view it shows (plan|front|side|section|detail|iso|other|none; "
                "hatching = section, a 3D picture = iso), its caption as printed NEAR the view on the page (\"SECTION A-A\", \"DETAIL B\", or "
                "\"\"), its own scale or null, \"s\": another number showing the SAME view (one view cut in two) or null; and the dimensions "
                "in the full-size cut-out. For sections add section: {cutting_plane: printed reference, evidence: internal geometry and hatching, questions: [uncertainties]}. "
                "Do not treat hatching as geometry. " + _COMMON + ' Reply {"c": {"1": {"t": "plan", "l": "", "sc": null, "s": null, "d": [dims]}}}')
MIN_CROP_SCALE = 0.6           # crops may be shrunk to fit one sheet down to this (a 300 DPI page still gives ~180 DPI crops)
SHEET_FILL = 0.7               # shelf packing fills about this share of a sheet
OVERVIEW_H = 640               # the page overview on the crop sheet: big enough to read captions and layout, small enough to leave room


@dataclass
class CropView:
    no: int                                  # 1-based crop number on the page (as drawn)
    box: list[int]                           # page pixels
    type: str = "none"
    label: str = ""
    scale: str | None = None
    same_as: int | None = None
    dims: list[str] = field(default_factory=list)
    section: dict = field(default_factory=dict)  # cutting-plane and internal-geometry evidence


@dataclass
class Dimension:
    id: str                                  # P<page>.d<k>
    text: str                                # as printed (first reading)
    views: list[int]                         # crop numbers it was read in ([] = unplaced: only the whole-page read saw it)
    single_source: bool                      # only one of the two reads saw it: worth a human look
    token: str = ""                          # the exact normalized component represented by this row
    confidence: float | None = None          # extraction-agent estimate, never an OCR score
    confidence_reason: str = ""
    confidence_metrics: list[int] | None = None


@dataclass
class PageRead:
    page: int
    dims: list[Dimension]
    views: list[CropView]
    title_block: dict
    notes: list[str]
    calls: int
    page_dims: list[str] = field(default_factory=list)  # preserve the independent whole-page reading for provenance
    issues: list[str] = field(default_factory=list)     # unreadable callouts cannot silently disappear from a plan


def pack(png: bytes, boxes: list[list[int]], pad: int = 8, overview: bytes | None = None) -> list[tuple[bytes, list[int]]]:
    """The crops of one page packed in rows onto as few sheets as fit MAX_SIDE, each crop numbered (1-based, page order), at full size
    (a crop larger than MAX_SIDE is shrunk to fit). `overview`: a picture placed first on the first sheet (the small marked page).
    -> [(sheet png, crop numbers on it)]."""
    page = Image.open(io.BytesIO(png)).convert("RGB")
    crops: list = []
    if overview is not None:
        ov = Image.open(io.BytesIO(overview)).convert("RGB")
        ov.thumbnail((MAX_SIDE - GAP, OVERVIEW_H))
        crops.append(("PAGE", ov))
    cut = [page.crop((max(0, x0 - pad), max(0, y0 - pad), min(page.width, x1 + pad), min(page.height, y1 + pad))) for x0, y0, x1, y1 in boxes]
    # one sheet is one call: shrink all crops evenly until they fit one sheet, but never below MIN_CROP_SCALE (text must stay readable)
    room = SHEET_FILL * MAX_SIDE * (MAX_SIDE - (crops[0][1].height + GAP if crops else 0))
    need = sum((c.width + GAP) * (c.height + GAP) for c in cut)
    scale = max(MIN_CROP_SCALE, min(1.0, (room / need) ** 0.5)) if need else 1.0
    for i, c in enumerate(cut, 1):
        if scale < 1.0:
            c = c.resize((max(1, round(c.width * scale)), max(1, round(c.height * scale))), Image.LANCZOS)
        max_crop_height = MAX_SIDE - GAP - (ov.height + GAP if overview is not None else 0)
        if c.width > MAX_SIDE - GAP or c.height > max_crop_height:
            c.thumbnail((MAX_SIDE - GAP, max_crop_height))
        crops.append((i, c))
    sheets, placed, x, y, row_h, width = [], [], 0, 0, 0, 0

    def flush():
        if placed:
            sheet = Image.new("RGB", (max(1, width), max(1, y + row_h)), "white")
            draw = ImageDraw.Draw(sheet)
            for n, img, px, py in placed:
                sheet.paste(img, (px, py + GAP))
                colour = (30, 80, 255) if n == "PAGE" else (220, 0, 0)
                draw.rectangle([px, py + GAP, px + img.width - 1, py + GAP + img.height - 1], outline=colour, width=2)
                draw.text((px + 3, py + 4), str(n), fill=colour)
            buf = io.BytesIO()
            sheet.save(buf, "PNG", optimize=True)
            sheets.append((buf.getvalue(), [n for n, *_ in placed if n != "PAGE"]))

    # ponytail: simple shelf packing in page order; a smarter packer only saves pixels, not calls
    for n, img in crops:
        w, h = img.width + GAP, img.height + GAP
        if x + w > MAX_SIDE:                                                   # next row
            x, y, row_h = 0, y + row_h, 0
        if y + h > MAX_SIDE:                                                   # next sheet: it starts with the overview again
            flush()
            placed, x, y, row_h, width = [], 0, 0, 0, 0
            if overview is not None:
                placed.append(("PAGE", ov, 0, 0))
                y, width = ov.height + GAP, ov.width + GAP
        placed.append((n, img, x, y))
        x, row_h, width = x + w, max(row_h, h), max(width, x + w)
    flush()
    from app import trace
    tr = trace.current()
    (tr.warn if len(sheets) > 1 else tr.event)("pack", "crops packed", crops=len(boxes), sheets=len(sheets), crop_scale=round(scale, 3),
                                               scale_floor_hit=scale <= MIN_CROP_SCALE and need > room, crops_per_sheet=[n for _, n in sheets],
                                               sheet_sizes=[list(Image.open(io.BytesIO(s)).size) for s, _ in sheets])
    return sheets


def _dims(value) -> list[str]:
    if not isinstance(value,list): return []
    return [str(text) for d in value for text in [d.get("text","") if isinstance(d,dict) else d] if str(text).strip()]


def combine(page_no: int, page_dims: list[str], views: list[CropView]) -> list[Dimension]:
    """One dimension per printed callout. How many: the whole-page read's count (it sees each callout once; overlapping crops would count
    one twice), or, for one only the crops saw, the most any single crop saw. Views: the crops it was read in."""
    page_count = Counter(tok for d in page_dims for tok in dim_tokens(d))
    crop_count: Counter = Counter()
    where: dict[str, list[int]] = {}
    text: dict[str, str] = {}
    for d in page_dims:
        for tok in dim_tokens(d):
            text.setdefault(tok, d)
    for v in views:
        in_crop = Counter(tok for d in v.dims for tok in dim_tokens(d))
        for tok, n in in_crop.items():
            crop_count[tok] = max(crop_count[tok], n)            # crops can OVERLAP: the same printed callout is then in two crops
            where.setdefault(tok, []).append(v.no)
        for d in v.dims:
            for tok in dim_tokens(d):
                text.setdefault(tok, d)
    out = []
    for tok in sorted(set(page_count) | set(crop_count), key=lambda t: (where.get(t, [999])[0], t)):
        # how many times it is printed: the whole-page read decides (it sees each callout once); crops only say WHERE
        for _ in range(page_count[tok] or crop_count[tok]):
            out.append(Dimension(f"P{page_no}.d{len(out) + 1}", text[tok], where.get(tok, []), not (page_count[tok] and crop_count[tok]), tok))
    return out


def mark_boxes(png: bytes, boxes: list[list[int]]) -> bytes:
    """The page with thin numbered boxes, for the whole-page call: it names the views with the WHOLE page in sight (captions such as
    "SECTION A-A" are printed outside the view, so a crop cannot show them). Thin light lines keep the dimension text readable."""
    if not boxes:
        return png
    im = Image.open(io.BytesIO(png)).convert("RGB")
    draw = ImageDraw.Draw(im)
    for n, (x0, y0, x1, y1) in enumerate(boxes, 1):
        draw.rectangle([x0, y0, x1, y1], outline=(60, 120, 255), width=1)
        draw.text((x0 + 2, max(0, y0 - 11)), str(n), fill=(30, 80, 255))
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue()


AGENT_VIEWS_PROMPT = PAGE_PROMPT + """
Also identify every distinct drawing view on the ORIGINAL page. Add a top-level
"regions" array with one {"box": [left, top, right, bottom]} per view.
Coordinates are normalized to 0..1000 relative to the entire supplied image.
Find complete orthographic, section, detail and isometric views, including narrow
side sections. One complete view gets ONE box, never separate boxes for its holes,
edges, hatching or dimension clusters. Include its dimension callouts when space
permits. Keep adjacent views separate. Avoid overlaps and duplicate/contained
boxes for the same view. A caption such as A-A belongs to its associated view.
Ignore existing colored rectangle annotations, app chrome, tables and title blocks.
Do not enclose several unrelated views in a single box. Report uncertainty in issues.
Return an empty regions array only if no drawing views are present.
"""


def agent_boxes(regions, width, height):
    """Validate model coordinates; never substitute CV boundaries for missing evidence."""
    issues, boxes = [], []
    if not isinstance(regions, list) or len(regions) > 80:
        return [], ["The extraction agent did not return a valid drawing-view list. Re-extract or clarify the view boundaries."]
    for index, region in enumerate(regions, 1):
        raw = region.get("box") if isinstance(region, dict) else None
        if (not isinstance(raw, list) or len(raw) != 4 or
                any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1000 for v in raw) or
                raw[0] >= raw[2] or raw[1] >= raw[3]):
            issues.append(f"Agent view {index}: invalid boundary; confirm this region against the original drawing.")
            continue
        box = [round(raw[0]*width/1000), round(raw[1]*height/1000), round(raw[2]*width/1000), round(raw[3]*height/1000)]
        area = (box[2]-box[0])*(box[3]-box[1])
        if area <= 0:
            issues.append(f"Agent view {index}: boundary is too small to read.")
            continue
        duplicate = False
        for previous in boxes:
            intersection = max(0,min(box[2],previous[2])-max(box[0],previous[0]))*max(0,min(box[3],previous[3])-max(box[1],previous[1]))
            other_area = (previous[2]-previous[0])*(previous[3]-previous[1])
            if intersection/(area+other_area-intersection) > .85:
                issues.append(f"Agent view {index}: duplicate boundary removed; verify that no separate view was omitted.")
                duplicate = True
                break
            if intersection/min(area,other_area) > .25:
                issues.append(f"Agent view {index}: its boundary overlaps another view; verify the readings in the shared region.")
        if not duplicate:
            boxes.append(box)
    if not boxes:
        issues.append("No drawing views were identified by the agent. Whole-page readings require review.")
    return boxes, issues


def read_page(llm: LLMClient, page_no: int, png: bytes, boxes: list[list[int]] | None) -> PageRead:
    """Two calls (or one more per extra packed sheet). Raises LLMError when the model fails.
    Call 1, the clean page: every dimension + title block + notes (nothing drawn on it: drawn boxes cost ~4 points of recall, measured).
    Call 2, the crop sheet: a small marked overview of the page on top (so captions printed beside a view are in sight) + the full-size
    crops: the NAME of every crop and its dimensions."""
    from app import trace
    tr = trace.current()
    agent_regions = boxes is None
    prompt = (AGENT_VIEWS_PROMPT if agent_regions else PAGE_PROMPT)+"\n"+RUBRIC_PROMPT
    tr.event("read", "page call (whole page: dimensions + title block)", page=page_no, boxes=len(boxes or []), region_source="agent" if agent_regions else "supplied")
    page_parts = [FilePart(png, "image/png")]
    page = llm.generate_json(prompt, page_parts).data
    if not isinstance(page, dict) or not isinstance(page.get("d"), list) or not isinstance(page.get("tb"), dict) or not isinstance(page.get("notes"), list):
        getattr(llm, "discard", lambda *a: None)(prompt, page_parts)
    if not isinstance(page, dict):
        tr.warn("read", "page reply is not a JSON object: nothing used from it", page=page_no, got=type(page).__name__)
        page = {}
    for key in ("d", "tb", "notes"):
        if key not in page:
            tr.warn("read", f"page reply has no '{key}'", page=page_no)
    if agent_regions:
        with Image.open(io.BytesIO(png)) as image:
            boxes, boundary_issues = agent_boxes(page.get("regions"), *image.size)
        page["issues"] = _dims(page.get("issues")) + boundary_issues
        # Valid overlapping rectangles can be unavoidable on densely arranged
        # sheets. Keep their evidence cached and expose the review issue.
        if not boxes or any("invalid" in issue for issue in boundary_issues):
            getattr(llm, "discard", lambda *a: None)(prompt, page_parts)
        tr.event("read", "agent view boundaries", page=page_no, boxes=boxes, issues=boundary_issues)
    tr.event("read", "page reply", page=page_no, dims=_dims(page.get("d")), title_block=page.get("tb"), notes=_dims(page.get("notes")))
    views = [CropView(i, b) for i, b in enumerate(boxes, 1)]
    calls = 1
    for sheet, numbers in pack(png, boxes, overview=mark_boxes(png, boxes)) if boxes else []:
        tr.event("read", "crop sheet call (names + dimensions per crop)", page=page_no, crops=numbers)
        crop_parts = [FilePart(sheet, "image/png")]
        reply = llm.generate_json(CROPS_PROMPT, crop_parts).data
        calls += 1
        crops = reply.get("c", {}) if isinstance(reply, dict) else {}
        if not isinstance(crops, dict) or not crops:
            tr.warn("read", "crop sheet reply has no 'c' object: these crops get no names and no dimensions", page=page_no, crops=numbers,
                    got=type(reply).__name__)
            crops = {}
        unknown = sorted(set(map(str, crops)) - set(map(str, numbers)))
        if unknown or any(not isinstance(crops.get(str(n)) or crops.get(n), dict) for n in numbers):
            getattr(llm, "discard", lambda *a: None)(CROPS_PROMPT, crop_parts)
        if unknown:
            tr.warn("read", "reply names crops that are not on this sheet (ignored)", page=page_no, crops=unknown)
        for n in numbers:
            c = crops.get(str(n)) or crops.get(n) or {}
            if not isinstance(c, dict) or not c:
                tr.warn("read", "crop missing from the reply (no name, no dimensions)", page=page_no, crop=n, got=c if not isinstance(c, dict) else None)
                continue
            v = views[n - 1]
            v.type, v.label, v.scale = str(c.get("t") or "none"), str(c.get("l") or ""), c.get("sc")
            same = c.get("s")
            v.same_as = same if isinstance(same, int) and 1 <= same <= len(boxes) and same != n else None
            if same is not None and v.same_as is None:
                tr.warn("read", "crop's 'same view as' is not another crop on the page (ignored)", page=page_no, crop=n, same_as=same)
            v.dims = _dims(c.get("d"))
            if isinstance(c.get("section"), dict):
                section = c["section"]
                v.section = {"cutting_plane": str(section.get("cutting_plane") or ""),
                             "evidence": str(section.get("evidence") or ""),
                             "questions": _dims(section.get("questions"))}
            if v.type == "section" and not v.section.get("evidence") and not v.section.get("questions"):
                v.section["questions"] = ["Confirm the cutting plane and internal geometry; section evidence was not returned."]
            tr.event("read", "crop", page=page_no, crop=n, box=v.box, type=v.type, label=v.label, scale=v.scale, same_as=v.same_as, dims=v.dims)
    tb = page.get("tb") if isinstance(page.get("tb"), dict) else {}
    if "projection_symbol" not in tb:
        tb = {**tb, "projection_symbol": {"status": "unreadable", "convention": "unknown",
               "evidence": "The agent did not return a symbol reading. Verify the projection against the original drawing."}}
    section_questions = [f"Section {v.no} ({v.section.get('cutting_plane') or v.label}): {question}"
                         for v in views for question in v.section.get("questions", [])]
    dims = combine(page_no, _dims(page.get("d")), views)
    from app.drawing.confidence import agent_score
    from collections import defaultdict
    scores=defaultdict(list)
    for item in page.get("d",[]) if isinstance(page.get("d"),list) else []:
        if isinstance(item,dict) and isinstance(item.get("text"),str):
            scores[item["text"]].append(item)
    positions=Counter()
    for dimension in dims:
        candidates=scores[dimension.text]
        index=positions[dimension.text];positions[dimension.text]+=1
        if index<len(candidates):
            reading=candidates[index]
            grade=rubric_score(reading.get("confidence_metrics"))
            dimension.confidence_metrics=list(reading["confidence_metrics"]) if grade is not None else None
            dimension.confidence=grade if grade is not None else agent_score(reading.get("confidence"))
            dimension.confidence_reason=reading.get("confidence_reason") if isinstance(reading.get("confidence_reason"),str) else ""
    tr.event("combine", "page dimensions", page=page_no, total=len(dims), agreed=sum(not d.single_source for d in dims),
             check=[d.text for d in dims if d.single_source and d.views], no_view=[d.text for d in dims if not d.views],
             only_on_page=[d.text for d in dims if d.single_source and not d.views], calls=calls)
    return PageRead(page_no, dims, views, tb, _dims(page.get("notes")), calls, _dims(page.get("d")), _dims(page.get("issues")) + section_questions)
