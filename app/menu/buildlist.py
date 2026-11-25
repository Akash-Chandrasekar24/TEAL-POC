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
STANDALONE_FEATURES = ("swept_tube", "loft", "sheet_metal_base", "sheet_metal_part", "sheet_metal_formed")       # one item that is the WHOLE part: nothing may follow
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
    projection_reasoning: str = ""                                   # the symbol, or the layout evidence the AI decided from
    seen_dimensions: list[str] = []
    items: list[Item]
    unsupported: list[Unsupported] = []
    needs_human: list[str] = []
    accepted_gaps: list[str] = []
    expected_final: ExpectedFinal | None = None                      # added by a human at approval, never by the reader
    designer_answers: list[dict[str, str]] = []                      # the designer's answers to needs_human, kept when they confirm the list


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


def off_lobe_bosses(bl: BuildList) -> list[str]:
    """A round boss placed 'on' a profile boss's top at (0, 0) sits at the centre of that outline's BOUNDING BOX (the anchor's centre), which
    is not where a pin goes when the outline has rounded ends: an engine's two middle crankpins came out 22 mm off their webs' pin arcs and
    the rods could not be assembled. Refused when the outline has an arc the circle could sit in but none centred on the box centre."""
    import math
    from app.geometry import profile_bounds
    hosts = {it.id: it for it in bl.items if it.feature == "extrude_profile_boss" and isinstance(it.params, dict)}
    reasons = []
    for it in bl.items:
        p = it.params if isinstance(it.params, dict) else {}
        on = str(p.get("on") or "")
        if it.feature != "circle_boss" or not on.endswith(".top") or on.split(".")[0] not in hosts:
            continue
        profile = hosts[on.split(".")[0]].params.get("profile")
        segs = profile.get("segments") if isinstance(profile, dict) else None
        if not isinstance(segs, list) or not segs or not all(isinstance(s, dict) and s.get("kind") in ("line", "arc") for s in segs):
            continue
        try:
            x0, x1, y0, y1 = profile_bounds(segs)
            arcs = [(s["cx"], s["cy"], math.hypot(s["x1"] - s["cx"], s["y1"] - s["cy"])) for s in segs if s["kind"] == "arc"]
        except (KeyError, TypeError, ValueError):
            continue
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        for c in p.get("circles") or []:
            pos = c.get("position") if isinstance(c, dict) else None
            if not isinstance(c, dict) or c.get("pattern") or not isinstance(c.get("diameter"), (int, float)):
                continue
            if pos is not None and (not isinstance(pos, dict) or abs(float(pos.get("x") or 0)) > 1e-9 or abs(float(pos.get("y") or 0)) > 1e-9
                                    or pos.get("x_from", "centre") != "centre" or pos.get("y_from", "centre") != "centre"):
                continue
            lobes = [a for a in arcs if a[2] >= c["diameter"] / 2 - 1e-6]
            if lobes and not any(math.hypot(a[0] - cx, a[1] - cy) < 0.5 for a in lobes):
                where = ", ".join(f"({a[0]:g}, {a[1]:g}) R{a[2]:g}" for a in lobes[:4])
                reasons.append(f"{it.id}: its diameter-{c['diameter']:g} circle 'on' {on} at (0, 0) lands on the centre of the BOUNDING BOX of "
                               f"{on.split('.')[0]}'s outline ({cx:g}, {cy:g} in that outline's coordinates), not on a rounded end it could sit "
                               f"in ({where}): a pin or boss is concentric with the arc around it. Place {it.id} like the other pins: `face` + "
                               f"`offset` (the station of {on.split('.')[0]}'s top) and the arc centre's position in the base outline's "
                               f"coordinates, or the arc centre's offset from that box centre")
    return reasons


def crossing_outlines(bl: BuildList) -> list[str]:
    """An outline that crosses itself cannot be extruded (SolidWorks only says 'Feature creation returned null'): refused before the build
    with WHERE it crosses, so the list is fixed instead of the part stopping at that step."""
    from app.geometry import outline_crossing
    reasons = []
    for it in bl.items:
        profile = it.params.get("profile") if isinstance(it.params, dict) else None
        if not isinstance(profile, dict):
            continue
        segs, pts = profile.get("segments"), profile.get("points")
        if isinstance(pts, list) and len(pts) >= 3 and segs is None:
            try:
                segs = [{"kind": "line", "x1": a[0], "y1": a[1], "x2": b[0], "y2": b[1]} for a, b in zip(pts, pts[1:] + pts[:1])]
            except (TypeError, IndexError):
                continue
        if not isinstance(segs, list) or not all(isinstance(s, dict) and s.get("kind") in ("line", "arc") for s in segs):
            continue
        try:
            hit = outline_crossing(segs)
        except (KeyError, TypeError, ValueError):
            continue
        if hit:
            i, j, (x, y) = hit
            reasons.append(f"{it.id}: its outline crosses itself: segment {i + 1} ({segs[i]['kind']}) crosses segment {j + 1} "
                           f"({segs[j]['kind']}) at ({x:.3f}, {y:.3f}), and SolidWorks cannot extrude a crossing outline. Go once around "
                           f"the BOUNDARY of the shape: where a line or arc meets another edge (a shoulder, a lug, a flat), end both at "
                           f"that meeting point and continue along the edge that is on the outside; drop any piece that lies inside")
    return reasons


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
    if any(it.feature == "shell" for it in bl.items[2:]) or (bl.items and bl.items[0].feature == "shell"):
        reasons.append("a shell must be the second item, directly after its base (it hollows the plain base)")
    reasons += seen_violations(bl)
    reasons += off_lobe_bosses(bl)
    reasons += crossing_outlines(bl)
    return reasons
