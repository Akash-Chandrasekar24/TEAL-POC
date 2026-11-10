"""AI-drafted answers to the extraction's open clarification questions. PROPOSALS ONLY.

Nothing here saves anything: the browser shows each proposal and the reviewer accepts, edits or skips it, and only then is it saved through the
existing correction path (the chat reply), labelled as AI-suggested and confirmed by the human. The model may say it cannot tell; it must not
invent a value, and a proposed measurement is kept only if it is one printed callout and names a real view of that page.
"""
from __future__ import annotations

import json
import re

from app.llm.base import FilePart
from app.perception.dims import dim_tokens

QUESTIONS_PER_CALL = 20          # questions with no spot on the page are asked in batches with the whole page
CROP_MAX = 1600                  # longest side of a close-up sent with a question (px)
MAX_ANSWER = 600

PROMPT = '''You help a human reviewer resolve open questions about ONE engineering drawing page. Look at the picture and the questions below.
For each question give a short, factual answer that is grounded ONLY in what is visibly printed on this page, or say you cannot tell.
Never guess a dimension, never calculate one, never use knowledge of typical parts. If the picture is too small or unclear to be sure, set can_answer false.
Be conservative: when a question says a small, rotated or low-resolution reading is uncertain or has two plausible values, answer only if EVERY digit is unmistakable at full size;
otherwise set can_answer false and say what is hard to read. A wrong confident answer is worse than "cannot tell".
A "measurement" is only a dimension callout (numbers, optionally with a symbol such as Ø, R or °). A title, scale label, part name or note is never a measurement.
When the answer is a corrected printed callout, put EXACTLY the printed text in "measurement" (one callout, e.g. "22" or "Ø10 - 8 hole") and the id of the view it
belongs to in "view" (from the views list); otherwise leave both null. "basis" says in one sentence what you saw.
The picture is either the whole page or, when the context says "picture": "close-up", a full-resolution close-up of the part of the page the question is about.
Return JSON only: {"suggestions":[{"key":"P1.u1","can_answer":true,"answer":"...","measurement":null,"view":null,"basis":"..."}]}
Answer every key once; do not add keys that were not asked.'''


def open_questions(result: dict, model) -> list[dict]:
    """The unresolved clarification questions: {key, page, question}: exactly the ones the review page lists (same filter as the drawing model),
    so the AI is never asked about raw OCR flags or plain statements. Questions the reviewer already answered are not asked again."""
    from .model import is_question
    answered = {c.id for c in model.resolutions.clarifications}
    out = []
    for page in result["pages"]:
        read = page.get("read") or {}
        categories = {c.get("id"): c.get("category") for c in ((read.get("evidence") or {}).get("extraction") or {}).get("callouts", []) if isinstance(c, dict)}
        for i, issue in enumerate(read.get("issues", []), 1):
            key = f"P{page['no']}.u{i}"
            if isinstance(issue, str) and key not in answered and is_question(issue, categories):
                out.append({"key": key, "page": page["no"], "question": issue, "box": question_box(page, issue)})
    return out


_STOP = {"the", "and", "this", "that", "with", "from", "callout", "view", "drawing", "page", "exact", "should", "verified", "visually", "crowded",
         "legible", "against", "source", "attachment", "referenced", "surface", "cylindrical", "dimension", "dimensions", "shown", "clear"}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", text.lower()) if w not in _STOP}


def question_box(page: dict, text: str) -> list[float] | None:
    """Where on the page a question points (page pixels), the same way the review page finds it: a callout id (o12), a view id (v3), a feature id
    (f8: its views), a SECTION / DETAIL letter, else the view whose title shares the most words with the question. None = the whole page."""
    read = page.get("read") or {}
    ex = (read.get("evidence") or {}).get("extraction") or {}
    boxes, W, H = page.get("boxes") or [], page.get("width") or 0, page.get("height") or 0
    view_box = lambda n: boxes[n - 1] if 0 < n <= len(boxes) else None

    def union(bs):
        bs = [b for b in bs if b]
        return [min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs)] if bs else None

    m = re.search(r"\bo(\d+)\b", text)
    if m:
        c = next((c for c in ex.get("callouts", []) if isinstance(c, dict) and c.get("id") == f"o{m.group(1)}" and c.get("box")), None)
        if c and W and H:
            b = c["box"]
            return [b[0] * W / 1000, b[1] * H / 1000, b[2] * W / 1000, b[3] * H / 1000]
    m = re.search(r"\bv(\d+)\b", text)
    if m and view_box(int(m.group(1))):
        return view_box(int(m.group(1)))
    m = re.search(r"\bf(\d+)\b", text)
    if m:
        f = next((f for f in ex.get("features", []) if isinstance(f, dict) and f.get("id") == f"f{m.group(1)}"), None)
        got = union([view_box(int(v[1:])) for v in (f or {}).get("views", []) if re.fullmatch(r"v\d+", str(v))])
        if got:
            return got
    views = read.get("views") or []
    m = re.search(r"\b(section|detail)\s+([a-z])(?:\s*-\s*\2)?\b", text, re.I)
    if m:
        want = f"{m.group(1)} {m.group(2)}".lower()
        for i, v in enumerate(views):
            if want in re.sub(r"\s*-\s*[a-z]\b", "", str(v.get("label", "")).lower()):
                return view_box(v.get("no") or i + 1)
    words = _words(text)
    scored = sorted(((len(words & _words(str(v.get("label", "")))), i, v) for i, v in enumerate(views)), key=lambda t: (-t[0], t[1]))
    if scored and scored[0][0] >= 2 and (len(scored) == 1 or scored[1][0] < scored[0][0]):
        return view_box(scored[0][2].get("no") or scored[0][1] + 1)
    return None


def crop_png(png: bytes, box, pad: int = 120) -> bytes | None:
    """The question's spot at full resolution (shrunk only if longer than CROP_MAX)."""
    if not box:
        return None
    import io
    from PIL import Image
    im = Image.open(io.BytesIO(png))
    pad = max(pad, int(0.2 * max(box[2] - box[0], box[3] - box[1])))   # dimensions often sit outside the detected view box
    x0, y0 = max(0, int(box[0]) - pad), max(0, int(box[1]) - pad)
    x1, y1 = min(im.width, int(box[2]) + pad), min(im.height, int(box[3]) + pad)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    im = im.crop((x0, y0, x1, y1))
    if max(im.size) > CROP_MAX:
        k = CROP_MAX / max(im.size)
        im = im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))))
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


_ALLOWED_WORDS = {"hole", "holes", "thru", "through", "deep", "typ", "mm", "inch"}


def looks_like_dimension(text: str) -> bool:
    """A callout with digits and no ordinary words ("05. CONNECTING ROD CAP" is a title, "Ø10 - 8 hole" is a dimension)."""
    return bool(re.search(r"\d", text)) and all(w.lower() in _ALLOWED_WORDS for w in re.findall(r"[A-Za-z]{3,}", text))


def _clean(raw, asked: dict[str, str], views: set[str]) -> dict | None:
    if not isinstance(raw, dict) or raw.get("key") not in asked:
        return None
    can = raw.get("can_answer") is True
    answer = raw.get("answer") if isinstance(raw.get("answer"), str) else ""
    answer = re.sub(r"\s+", " ", answer).strip()[:MAX_ANSWER]
    item = {"key": raw["key"], "question": asked[raw["key"]], "can_answer": bool(can and answer),
            "answer": answer if can else "", "measurement": None, "view": None,
            "basis": re.sub(r"\s+", " ", raw["basis"]).strip()[:300] if isinstance(raw.get("basis"), str) else ""}
    measurement, view = raw.get("measurement"), raw.get("view")
    if item["can_answer"] and isinstance(measurement, str) and measurement.strip() and isinstance(view, str) and view in views:
        text = measurement.strip()[:200]
        if len(dim_tokens(text)) == 1 and looks_like_dimension(text):   # one printed dimension callout, in a view that exists on this page
            item["measurement"], item["view"] = text, view
    return item


def suggest(llm, page_no: int, png: bytes, questions: list[dict], views: list[dict], dimensions: list[str]) -> list[dict]:
    """Proposals for every question on one page (one model call per batch of 20). Each asked key appears once."""
    out, rest = [], []
    for q in questions:                       # a question that points at a spot is asked with its close-up (one picture per call)
        crop = crop_png(png, q.get("box"))
        if crop:
            out.extend(_suggest_batch(llm, page_no, crop, [q], views, dimensions, closeup=True))
        else:
            rest.append(q)
    for start in range(0, len(rest), QUESTIONS_PER_CALL):
        out.extend(_suggest_batch(llm, page_no, png, rest[start:start + QUESTIONS_PER_CALL], views, dimensions))
    order = {q["key"]: i for i, q in enumerate(questions)}
    return sorted(out, key=lambda x: order.get(x["key"], 0))


def _suggest_batch(llm, page_no: int, png: bytes, questions: list[dict], views: list[dict], dimensions: list[str], closeup: bool = False) -> list[dict]:
    asked = {q["key"]: q["question"] for q in questions}
    context = {"page": page_no, "picture": "close-up" if closeup else "whole page", "views": [{"id": v["id"], "label": v.get("label", ""), "type": v.get("type", "")} for v in views],
               "extracted_dimension_texts": dimensions[:200],
               "questions": [{"key": k, "question": q} for k, q in asked.items()]}
    system, parts = PROMPT, [FilePart(png, "image/png"), json.dumps(context, ensure_ascii=False)]
    response = llm.generate_json(system, parts)
    data = response.data
    raw = data.get("suggestions") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        getattr(llm, "discard", lambda *a: None)(system, parts)
        raise ValueError("The suggestion reply did not contain a suggestions list")
    view_ids = {v["id"] for v in views}
    out, seen = [], set()
    for entry in raw:
        item = _clean(entry, asked, view_ids)
        if item and item["key"] not in seen:
            seen.add(item["key"])
            out.append(item)
    for key, question in asked.items():                # a question the model skipped is shown as "could not tell", never silently dropped
        if key not in seen:
            out.append({"key": key, "question": question, "can_answer": False, "answer": "", "measurement": None, "view": None,
                        "basis": "The AI did not answer this question."})
    return out
