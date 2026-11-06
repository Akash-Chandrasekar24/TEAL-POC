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

MAX_QUESTIONS_PER_CALL = 20
MAX_ANSWER = 600

PROMPT = '''You help a human reviewer resolve open questions about ONE engineering drawing page. Look at the picture and the questions below.
For each question give a short, factual answer that is grounded ONLY in what is visibly printed on this page, or say you cannot tell.
Never guess a dimension, never calculate one, never use knowledge of typical parts. If the picture is too small or unclear to be sure, set can_answer false.
Be conservative: when a question says a small, rotated or low-resolution reading is uncertain or has two plausible values, answer only if EVERY digit is unmistakable at full size;
otherwise set can_answer false and say what is hard to read. A wrong confident answer is worse than "cannot tell".
A "measurement" is only a dimension callout (numbers, optionally with a symbol such as Ø, R or °). A title, scale label, part name or note is never a measurement.
When the answer is a corrected printed callout, put EXACTLY the printed text in "measurement" (one callout, e.g. "22" or "Ø10 - 8 hole") and the id of the view it
belongs to in "view" (from the views list); otherwise leave both null. "basis" says in one sentence what you saw.
Return JSON only: {"suggestions":[{"key":"P1.u1","can_answer":true,"answer":"...","measurement":null,"view":null,"basis":"..."}]}
Answer every key once; do not add keys that were not asked.'''


def open_questions(result: dict, model) -> list[dict]:
    """The unresolved clarification questions: {key, page, question}. Questions the reviewer already answered are not asked again."""
    answered = {c.id for c in model.resolutions.clarifications}
    out = []
    for page in result["pages"]:
        for i, issue in enumerate((page.get("read") or {}).get("issues", []), 1):
            key = f"P{page['no']}.u{i}"
            if isinstance(issue, str) and key not in answered:
                out.append({"key": key, "page": page["no"], "question": issue})
    return out


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
    """One model call for one page. Returns cleaned proposals for the asked keys only (each key once)."""
    asked = {q["key"]: q["question"] for q in questions[:MAX_QUESTIONS_PER_CALL]}
    context = {"page": page_no, "views": [{"id": v["id"], "label": v.get("label", ""), "type": v.get("type", "")} for v in views],
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
