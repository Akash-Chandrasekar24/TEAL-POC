"""The build LIST: what the reader LLM writes and the human approves (one JSON document), plus the code-side checks on it.

    {"list_version": "1", "part_name": "...", "unit": "mm", "projection": "first", "seen_dimensions": ["120", "80", "R10", ...],
     "items": [{"id": "F1", "feature": "extrude_profile_boss", "printed": "120 x 80 x 10", "params": {...}}, ...],
     "unsupported": [{"id": "U1", "text": "ribs"}], "needs_human": [], "accepted_gaps": []}

`feature` is a name from the menu (registry.MENU), `params` are that feature's list-item parameters (params.py). The reader's rules: never guess a number, keep the drawing's unit, mark calculated numbers `derived`, copy every dimension text into
`seen_dimensions` first (rule 12: code checks that every number used is in it or marked derived). `accepted_gaps` is filled by the app after a
human decision, never by the reader. {"status": "stopped", "reason": "..."} is the reader's stop answer (an assembly or several parts).
"""
from __future__ import annotations

from typing import Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .numbers import numbers_in

from .registry import MENU

BASE_FEATURE = "extrude_profile_boss"
STANDALONE_FEATURES = ("swept_tube", "loft", "sheet_metal_base")       # one item that is the WHOLE part: nothing may follow
BASE_FEATURES = (BASE_FEATURE, "revolve_base", *STANDALONE_FEATURES)   # the items that may be the FIRST one (the base)
_UNCHECKED = frozenset({"expect_count", "dir", "tol", "countersink_angle_deg", "angle_deg",
                        "count", "count_x", "count_y", "start_angle_deg", "sweep_deg"})     # counts, enums and fixed angles are not dimensions


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Item(Strict):
    id: str
    feature: str
    printed: str = Field("", description="the dimension text as printed on the drawing, so the human can find it")
    uncertain: bool = False
    derived: bool = Field(False, description="true when a number is a sum of printed numbers (never measured from the picture)")
    derived_from: str = ""
    note: str = ""
    params: dict[str, Any]


class Unsupported(Strict):
    id: str
    text: str


class ExpectedFinal(Strict):
    """Numbers a HUMAN adds at approval to check the FINISHED part (the reader never writes this): its exact bounding box in mm (world axes: x right, y up,
    z toward the viewer, back face z = 0) and the number of solid bodies. The runner measures both after the last item; a difference stops the job at review."""
    bbox_mm: list[list[float]] | None = Field(None, description="[[x_min, y_min, z_min], [x_max, y_max, z_max]] in mm")
    bodies: int | None = Field(None, ge=1)
    tol_mm: float = Field(0.05, gt=0, description="allowed difference per bounding-box number, mm")

    @model_validator(mode="after")
    def _shape(self):
        if self.bbox_mm is not None:
            if len(self.bbox_mm) != 2 or any(len(c) != 3 for c in self.bbox_mm):
                raise ValueError("bbox_mm must be [[x_min, y_min, z_min], [x_max, y_max, z_max]]")
            if any(lo >= hi for lo, hi in zip(*self.bbox_mm)):
                raise ValueError("every bbox_mm minimum must be smaller than its maximum")
        if self.bbox_mm is None and self.bodies is None:
            raise ValueError("expected_final needs bbox_mm and / or bodies")
        return self

    def check(self, final: dict) -> list[dict]:
        """[{name, ok, detail}] comparing `final` (the runner's final measurements: bbox_mm, bodies) with the expectation."""
        out: list[dict] = []
        if self.bbox_mm is not None:
            got = final.get("bbox_mm")
            if not got:
                out.append({"name": "expected bounding box", "ok": False, "detail": "the part's bounding box could not be measured"})
            else:
                bad = [f"{'xyz'[i]} {kind}: {got[k][i]:.3f}, expected {self.bbox_mm[k][i]:.3f}"
                       for k, kind in ((0, "min"), (1, "max")) for i in range(3) if abs(got[k][i] - self.bbox_mm[k][i]) > self.tol_mm]
                size = [h - l for l, h in zip(*got)]
                out.append({"name": "expected bounding box", "ok": not bad,
                            "detail": ("matches: " if not bad else "; ".join(bad) + " | measured ") + f"size {size[0]:.3f} x {size[1]:.3f} x {size[2]:.3f} mm "
                                      f"(x {got[0][0]:.3f}..{got[1][0]:.3f}, y {got[0][1]:.3f}..{got[1][1]:.3f}, z {got[0][2]:.3f}..{got[1][2]:.3f})"})
        if self.bodies is not None:
            n = final.get("bodies")
            out.append({"name": "expected body count", "ok": n == self.bodies, "detail": f"{n} body(ies), expected {self.bodies}"})
        return out


class BuildList(Strict):
    list_version: Literal["1"]
    part_name: str = ""
    unit: Literal["mm", "inch"]
    projection: Literal["first", "third", "unknown"]
    seen_dimensions: list[str] = []
    items: list[Item]
    unsupported: list[Unsupported] = []
    needs_human: list[str] = []
    accepted_gaps: list[str] = []
    expected_final: ExpectedFinal | None = None                      # added by a human at approval, never by the reader


class Stopped(Strict):
    status: Literal["stopped"]
    reason: str


def parse_reply(data: dict) -> Union[BuildList, Stopped]:
    """Validate the reader's JSON. Raises pydantic.ValidationError when it does not fit the list format."""
    if isinstance(data, dict) and data.get("status") == "stopped":
        return Stopped.model_validate(data)
    return BuildList.model_validate(data)


def _numbers(value: Any, path: str = "") -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    if isinstance(value, bool):
        return out
    if isinstance(value, (int, float)):
        return [(path, float(value))]
    if isinstance(value, dict):
        for k, v in value.items():
            if k not in _UNCHECKED:
                out += _numbers(v, f"{path}.{k}" if path else k)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            out += _numbers(v, f"{path}[{i}]")
    return out


def item_params(item: Item):
    """The item's params as the feature's pydantic model (raises pydantic.ValidationError)."""
    return MENU[item.feature].params_model.model_validate(item.params)


def seen_violations(bl: BuildList) -> list[str]:
    """Rule 12: every number an item uses must be in seen_dimensions, or the item must be marked derived."""
    seen: set[float] = set()
    for s in bl.seen_dimensions:
        seen |= numbers_in(s)
    problems = []
    for it in bl.items:
        if it.derived or it.feature not in MENU:
            continue
        try:
            written = item_params(it).model_dump(exclude_unset=True)
        except ValidationError:
            continue
        for path, v in _numbers(written):
            if abs(v) > 1e-12 and not any(abs(abs(v) - s) < 1e-9 for s in seen):
                problems.append(f"{it.id}.{path}={v:g} is not in seen_dimensions and the item is not marked derived")
    return problems


def list_gate(bl: BuildList) -> list[str]:
    """Reasons the build must NOT start (an empty list = go). Mirrors the old spec gate, for the new format."""
    reasons: list[str] = []
    if bl.needs_human:
        reasons.append(f"needs_human is not empty ({len(bl.needs_human)} items)")
    if bl.projection == "unknown":
        reasons.append("projection is unknown")
    unc = [i.id for i in bl.items if i.uncertain]
    if unc:
        reasons.append(f"uncertain items: {unc}")
    open_gaps = [u.id for u in bl.unsupported if u.id not in set(bl.accepted_gaps)]
    if open_gaps:
        reasons.append(f"unsupported items not accepted by a human: {open_gaps}")
    if not bl.items:
        reasons.append("the list has no items")
        return reasons
    ids = [i.id for i in bl.items]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        reasons.append(f"duplicate item ids: {dup}")
    for it in bl.items:
        if it.feature not in MENU:
            reasons.append(f"{it.id}: '{it.feature}' is not on the menu {sorted(MENU)}; write it as unsupported")
            continue
        try:
            item_params(it)
        except ValidationError as ex:
            first = ex.errors()[0]
            reasons.append(f"{it.id} ({it.feature}) params: {'.'.join(str(p) for p in first['loc'])}: {first['msg']}")
    first = bl.items[0]
    def placed(params: Any) -> bool:                                  # on a face of the base (`face`) or on a face an earlier item made (`on`)
        return isinstance(params, dict) and (params.get("face") is not None or params.get("on") is not None)

    if first.feature not in BASE_FEATURES or (first.feature == BASE_FEATURE and placed(first.params)):
        reasons.append(f"the first item must be the base: {BASE_FEATURE} without a face, revolve_base or a whole-part item {STANDALONE_FEATURES} (got {first.id}: {first.feature})")
    if first.feature in STANDALONE_FEATURES and len(bl.items) > 1:
        reasons.append(f"{first.feature} makes the whole part: it must be the only item")
    if any((it.feature == BASE_FEATURE and not placed(it.params)) or it.feature in ("revolve_base", *STANDALONE_FEATURES) for it in bl.items[1:]):
        reasons.append("only the first item may be a base (an extrude_profile_boss without a face, or a revolve_base)")
    reasons += seen_violations(bl)
    return reasons
