"""Immutable source readings with explicit human corrections layered on top.

View attribution from the current reader is value-level: equal numbers in
different views are not automatically the same physical dimension or feature.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.perception.dims import dim_tokens
from .confidence import review_index


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Reading(Strict):
    source: Literal["page", "crop", "human"]
    text: str
    view: str | None = None


class Dimension(Strict):
    id: str
    page: int
    text: str
    token: str
    views: list[str]
    single_source: bool
    readings: list[Reading] = Field(default_factory=list)
    corrected_text: str | None = None
    confirmed: bool = False

    @property
    def effective_text(self) -> str:
        return self.corrected_text if self.corrected_text is not None else self.text

    def nominal(self) -> float:
        # The current reader does not parse fractional dimensions reliably. A
        # human can explicitly replace one with its decimal nominal value.
        if "/" in self.effective_text:
            raise ValueError(f"{self.id}: confirm a decimal nominal value for this fractional callout")
        tokens = dim_tokens(self.corrected_text) if self.corrected_text is not None else [self.token]
        if len(tokens) != 1:
            raise ValueError(f"{self.id}: specify exactly one nominal dimension")
        match = re.fullmatch(r"[ØR]?(-?\d+(?:\.\d+)?(?:e[+-]?\d+)?)", tokens[0], re.I)
        if not match:
            raise ValueError(f"{self.id}: a usable length is required (angles are not lengths)")
        value = float(match.group(1))
        if not math.isfinite(value):
            raise ValueError(f"{self.id}: nominal dimension must be finite")
        return -abs(value) if re.match(r"^\s*[ØR]?\s*-", self.effective_text, re.I) else value


class View(Strict):
    id: str
    type: str
    label: str = ""
    box: list[int]
    same_as: str | None = None
    section: dict = Field(default_factory=dict)


class Page(Strict):
    no: int
    source: str
    source_page: int
    sha: str
    views: list[View]
    title_block: dict = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class DimensionDecision(Strict):
    id: str
    confirmed: bool = True
    text: str | None = Field(None, min_length=1, max_length=200)


class SourceClarification(Strict):
    id: str = Field(pattern=r"^P[1-9][0-9]*\.u[1-9][0-9]*$")
    answer: str = Field(min_length=1, max_length=1000)
    text: str | None = Field(None, min_length=1, max_length=200)
    views: list[str] = Field(default_factory=list)


class Resolutions(Strict):
    unit: Literal["mm", "inch"] | None = None
    projection: Literal["first", "third"] | None = None
    dimensions: list[DimensionDecision] = Field(default_factory=list)
    note: str = Field("", max_length=4000)
    clarifications: list[SourceClarification] = Field(default_factory=list)


class DrawingModel(Strict):
    version: Literal["1"] = "1"
    result_id: str
    source_hash: str
    part_name: str = ""
    unit: Literal["mm", "inch", "unknown"] = "unknown"
    projection: Literal["first", "third", "unknown"] = "unknown"
    pages: list[Page]
    dimensions: list[Dimension]
    resolutions: Resolutions
    issues: list[str] = Field(default_factory=list)

    def digest(self) -> str:
        return fingerprint(self.model_dump())


def fingerprint(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def projection_reading(title_block: dict) -> str:
    """Keep symbol evidence separate from printed text; ambiguity needs review."""
    text = str(title_block.get("projection", "unknown")).strip().lower()
    if "projection_symbol" not in title_block:
        return text  # Saved readings from before symbol extraction remain usable.
    symbol = title_block["projection_symbol"]
    if not isinstance(symbol, dict):
        return "unknown"
    status, convention = symbol.get("status"), symbol.get("convention")
    if status == "absent" and convention == "unknown":
        return text
    if (status != "present" or not isinstance(convention, str) or convention not in {"first", "third"}
            or not isinstance(symbol.get("evidence"), str) or not symbol["evidence"].strip()):
        return "unknown"
    if text not in {"", "unknown", convention}:
        return "unknown"
    return convention


def reconcile(result: dict, resolutions: Resolutions | None = None) -> DrawingModel:
    """No LLM and no inference of feature identity from matching numeric values."""
    resolutions = resolutions or Resolutions()
    pages, dimensions, issues = [], [], []
    clarifications = {c.id: c for c in resolutions.clarifications}
    if len(clarifications) != len(resolutions.clarifications):
        raise ValueError("Duplicate source clarification")
    known_questions = set()
    seen_pages, seen_dims = set(), set()
    for raw in result["pages"]:
        no, read = raw["no"], raw.get("read")
        if no in seen_pages:
            raise ValueError(f"duplicate page: {no}")
        seen_pages.add(no)
        if not read:
            issues.append(f"Page {no}: read dimensions with a model before planning")
            read = {}
        page_questions = [(f"P{no}.u{i}", issue) for i, issue in enumerate(read.get("issues", []), 1) if isinstance(issue, str)]
        known_questions.update(key for key, _ in page_questions)
        views = [View(id=f"P{no}.v{v['no']}", type=v["type"], label=v.get("label", ""), box=v["box"],
                      same_as=f"P{no}.v{v['same_as']}" if v.get("same_as") else None,
                      section=v.get("section") or {}) for v in read.get("views", [])]
        view_ids = {v.id for v in views}
        if len(view_ids) != len(views):
            raise ValueError(f"Page {no}: duplicate view ids")
        for view in views:
            if view.same_as and view.same_as not in view_ids:
                raise ValueError(f"{view.id}: unknown same_as view")
        pages.append(Page(no=no, source=raw["source"], source_page=raw["source_page"], sha=raw["sha"], views=views,
                          title_block=read.get("title_block") or {}, notes=read.get("notes") or []))
        for key, question in page_questions:
            clarification = clarifications.get(key)
            if clarification is None:
                issues.append(f"Page {no}: unreadable source callout: {question}")
                continue
            if not clarification.answer.strip() or not set(clarification.views) <= view_ids:
                raise ValueError(f"{key}: give a source correction and valid view references")
            pages[-1].notes.append(f"Human clarification {key} ({question}): {clarification.answer}")
            if clarification.text is not None:
                tokens = dim_tokens(clarification.text)
                if len(tokens) != 1 or not clarification.views:
                    raise ValueError(f"{key}: a corrected measurement needs one nominal value and its source view")
                dim = Dimension(id=key.replace(".u", ".h"), page=no, text=clarification.text, token=tokens[0],
                                views=clarification.views, single_source=True, confirmed=True,
                                readings=[Reading(source="human", text=clarification.text)])
                dim.nominal()
                dimensions.append(dim)
                seen_dims.add(dim.id)
        for d in read.get("dims", []):
            if d["id"] in seen_dims:
                raise ValueError(f"duplicate dimension: {d['id']}")
            seen_dims.add(d["id"])
            tokens = dim_tokens(d["text"])
            token = d.get("token") or (tokens[0] if len(tokens) == 1 else "")
            refs = [f"P{no}.v{v}" for v in d["views"]]
            if not set(refs) <= view_ids:
                raise ValueError(f"{d['id']}: unknown view")
            readings = [Reading(source="page", text=t) for t in read.get("page_dims", []) if token in dim_tokens(t)]
            for view in read.get("views", []):
                readings += [Reading(source="crop", text=t, view=f"P{no}.v{view['no']}") for t in view.get("dims", []) if token in dim_tokens(t)]
            dimensions.append(Dimension(id=d["id"], page=no, text=d["text"], token=token, views=refs,
                                        single_source=d["single_source"], readings=readings))

    if set(clarifications) - known_questions:
        raise ValueError("A source clarification refers to an unknown question")
    by_id = {d.id: d for d in dimensions}
    decisions = set()
    for decision in resolutions.dimensions:
        if decision.id not in by_id or decision.id in decisions:
            raise ValueError(f"unknown or duplicate dimension correction: {decision.id}")
        if decision.text is not None and not decision.confirmed:
            raise ValueError(f"{decision.id}: a correction must be explicitly confirmed")
        decisions.add(decision.id)
        by_id[decision.id].confirmed = decision.confirmed
        by_id[decision.id].corrected_text = decision.text
    metadata = {}
    for key, allowed in (("unit", {"mm", "inch"}), ("projection", {"first", "third"})):
        values = {(projection_reading(p.title_block) if key == "projection" else
                   str(p.title_block.get(key, "unknown")).strip().lower()) for p in pages}
        # Do not let a known page conceal another page's ambiguous symbol.
        ambiguous_symbol = key == "projection" and any(
            "projection_symbol" in p.title_block and projection_reading(p.title_block) == "unknown" for p in pages)
        values.discard("unknown")
        values.discard("")
        override = getattr(resolutions, key)
        metadata[key] = override or (next(iter(values)) if len(values) == 1 and values <= allowed and not ambiguous_symbol else "unknown")
        if metadata[key] == "unknown":
            issues.append(f"Confirm {key}: " + ("unreadable, conflicting or invalid projection-symbol evidence" if ambiguous_symbol else
                          f"conflicting or unsupported readings {sorted(values)}" if values else "not identified in the drawing"))
    if not dimensions:
        issues.append("No dimension observations are available")
    review_model=DrawingModel(result_id=result["id"],source_hash="",pages=pages,dimensions=dimensions,resolutions=resolutions)
    approvals=review_index(result,review_model)
    for d in dimensions:
        ai_approved=approvals[d.id]["ai_approved"]
        if result.get("measurement_review_policy")=="agent-confidence-v1" and approvals[d.id]["needs_review"]:
            issues.append(f"{d.id} ({d.text}): human review required. {approvals[d.id]['review_reason']}")
        elif (d.single_source or not d.views or not d.token) and not d.confirmed and not ai_approved:
            issues.append(f"{d.id} ({d.text}): confirm this {'unplaced' if not d.views else 'single-source or ambiguous'} dimension against page {d.page}")
        # An invalid/ambiguous legacy row cannot be made usable just by ticking a box.
        # An angle (6°, 33°, 2X45°) is not a length and needs no nominal here; it can only block if a feature tries to use it as one.
        if (d.corrected_text is not None or not d.token or "/" in d.text) and "°" not in d.effective_text:
            try:
                d.nominal()
            except ValueError as ex:
                issues.append(str(ex))
    return DrawingModel(result_id=result["id"], source_hash=fingerprint({"pages": result["pages"]}),
                        part_name=next((str(p.title_block["name"]) for p in pages if p.title_block.get("name")), ""),
                        pages=pages, dimensions=dimensions, resolutions=resolutions, issues=issues, **metadata)
