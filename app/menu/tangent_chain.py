"""`tangent_chain`: an outline given as a chain of CIRCLES, the way lever plates, link plates and arc slots are dimensioned (radii, centre positions,
"tangent"), instead of arc end points that the drawing never prints. The code works the end points out exactly and hands plain line / arc segments to the
existing builders, so nothing else changes.

Walk the outline once, in order. Every circle has a `turn`: `ccw` when the outline wraps round it counter-clockwise (the material or window is on the LEFT of
the direction of travel: rounded ends, bosses), `cw` when it wraps the other way (a concave scoop). Walk the whole outline counter-clockwise and the convex
circles are `ccw`, the scoops `cw`. A circle of radius 0 is a sharp corner. Between two circles the travel runs along their common tangent line: outer
tangent when both turn the same way, inner (crossing) tangent when they differ; the tangent is the one that is travelled from the first circle to the next
with both centres on the correct side, so nothing is guessed. Circles that touch (internally or externally) are joined directly by an arc, no line.

Numbers in the drawing's unit, in the frame given by `frame` (like polygon points). Result: a closed list of segments
{"kind": "line", x1, y1, x2, y2} and {"kind": "arc", cx, cy, x1, y1, x2, y2, dir}; consecutive segments share their end points EXACTLY.
"""
from __future__ import annotations

import math
from typing import Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.features.base import FeatureRequestError
from app.features.outline_helpers import check_simple_polygon

TOL = 1e-7                  # lengths (drawing unit): shorter than this a line is a touch and an arc is a point
ANGLE_TOL = 1e-9
SAMPLE_STEP = math.radians(3)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class XY(_Strict):
    x: float
    y: float


class Polar(BaseModel):
    """A centre placed from an EARLIER circle of the chain: `distance` and `angle_deg` (counter-clockwise from +x) measured from that circle's centre."""
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    origin: str = Field(alias="from", description="id of an earlier circle in this list")
    distance: float = Field(gt=0, description="distance between the two centres, as printed")
    angle_deg: float = Field(description="direction from that centre to this one, counter-clockwise from +x as seen in the plate view")


class ChainCircle(_Strict):
    id: str = Field(min_length=1, description="a short name, e.g. A, B, C")
    centre: Union[XY, Polar] = Field(description="x, y in the outline's frame, or placed from an earlier circle with from / distance / angle_deg")
    radius: float = Field(ge=0, description="0 = a sharp corner")
    turn: Literal["ccw", "cw"] = Field(description="ccw: the outline wraps round this circle counter-clockwise (convex); cw: clockwise (a concave scoop)")


class TangentChain(_Strict):
    shape: Literal["tangent_chain"]
    circles: list[ChainCircle] = Field(min_length=2, description="the circles in order along the outline, walked counter-clockwise; the last connects back to the first")
    frame: Literal["base_lower_left", "centre"] = Field("base_lower_left", description="the origin of the centres: the base outline's lower-left corner, or its centre")

    @model_validator(mode="after")
    def _check(self):
        ids = [c.id for c in self.circles]
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            raise ValueError(f"duplicate circle ids: {dup}")
        for k, c in enumerate(self.circles):
            if isinstance(c.centre, Polar) and c.centre.origin not in ids[:k]:
                raise ValueError(f"circle {c.id}: 'from' must be the id of an EARLIER circle in the list (got {c.centre.origin!r})")
        solve_chain(self)                                   # geometry problems show up when the list is read, not at build time
        return self


# ------------------------------------------------------------------ the solver
Point = tuple[float, float]


def _centres(chain: TangentChain) -> list[Point]:
    out: dict[str, Point] = {}
    for c in chain.circles:
        if isinstance(c.centre, XY):
            out[c.id] = (c.centre.x, c.centre.y)
        else:
            bx, by = out[c.centre.origin]
            a = math.radians(c.centre.angle_deg)
            out[c.id] = (bx + c.centre.distance * math.cos(a), by + c.centre.distance * math.sin(a))
    return [out[c.id] for c in chain.circles]


def _join(ci: Point, ri: float, si: int, cj: Point, rj: float, sj: int, names: str) -> tuple[Point, Point, float]:
    """(leaving point on circle i, arriving point on circle j, tangent length) for the tangent line travelled from i to j.
    si / sj = +1 (ccw) or -1 (cw): the centre lies on the left (+1) or right (-1) of the direction of travel."""
    dx, dy = cj[0] - ci[0], cj[1] - ci[1]
    d = math.hypot(dx, dy)
    if d < TOL:
        raise FeatureRequestError(f"{names}: the two circles have the same centre: there is no tangent line between them")
    k = sj * rj - si * ri                                       # n . (cj - ci) for the left normal n of the travel direction
    ratio = k / d
    if abs(ratio) > 1 + 1e-9:
        raise FeatureRequestError(f"{names}: no tangent line exists (centres {d:.6g} apart, radii {ri:g} and {rj:g}): one circle is inside the other, "
                                  f"or the turns (ccw / cw) are wrong")
    ratio = max(-1.0, min(1.0, ratio))
    phi, delta = math.atan2(dy, dx), math.acos(ratio)
    best = None
    for sign in (1, -1):
        a = phi + sign * delta
        nx, ny = math.cos(a), math.sin(a)                       # left normal of the travel direction
        ux, uy = ny, -nx                                        # the travel direction
        p = (ci[0] - si * ri * nx, ci[1] - si * ri * ny)
        q = (cj[0] - sj * rj * nx, cj[1] - sj * rj * ny)
        length = (q[0] - p[0]) * ux + (q[1] - p[1]) * uy
        if length >= -TOL and (best is None or length > best[2]):
            best = (p, q, length)
    if best is None:
        raise FeatureRequestError(f"{names}: no tangent line can be travelled from the first circle to the second with these turns (ccw / cw): check the order and the turns")
    return best


def solve_chain(chain: TangentChain) -> list[dict]:
    """The closed outline (lines and arcs) the chain stands for. Raises FeatureRequestError when it cannot be built exactly."""
    circles = chain.circles
    n = len(circles)
    cen = _centres(chain)
    sgn = [1 if c.turn == "ccw" else -1 for c in circles]
    pout: list[Point] = [(0.0, 0.0)] * n
    qin: list[Point] = [(0.0, 0.0)] * n
    touching = [False] * n                                      # the join from circle i to i+1 has no line
    for i in range(n):
        j = (i + 1) % n
        p, q, length = _join(cen[i], circles[i].radius, sgn[i], cen[j], circles[j].radius, sgn[j], f"circles {circles[i].id} -> {circles[j].id}")
        pout[i] = p
        touching[i] = length <= TOL
        qin[j] = p if touching[i] else q
    segs: list[dict] = []
    arcs = []
    for i, c in enumerate(circles):
        sweep = 0.0
        if c.radius > TOL:
            a_in = math.atan2(qin[i][1] - cen[i][1], qin[i][0] - cen[i][0])
            a_out = math.atan2(pout[i][1] - cen[i][1], pout[i][0] - cen[i][0])
            sweep = ((a_out - a_in) * sgn[i]) % (2 * math.pi)
            if sweep < ANGLE_TOL or sweep > 2 * math.pi - ANGLE_TOL:
                sweep = 0.0
        if sweep == 0.0:
            pout[i] = qin[i]                                    # no arc: the line leaves exactly where the previous one arrived
        arcs.append(sweep)
    for i, c in enumerate(circles):
        j = (i + 1) % n
        if arcs[i] > 0.0:
            segs.append({"kind": "arc", "cx": cen[i][0], "cy": cen[i][1], "x1": qin[i][0], "y1": qin[i][1], "x2": pout[i][0], "y2": pout[i][1], "dir": sgn[i]})
        if not touching[i]:
            segs.append({"kind": "line", "x1": pout[i][0], "y1": pout[i][1], "x2": qin[j][0], "y2": qin[j][1]})
    if len(segs) < 3:
        raise FeatureRequestError("the chain gives fewer than 3 segments: it does not enclose an area")
    _check_simple(segs)
    return segs


def _check_simple(segs: list[dict]) -> None:
    """The outline must not cross or touch itself (checked on a polyline that follows every arc in 3-degree steps)."""
    pts: list[Point] = []
    for s in segs:
        pts.append((s["x1"], s["y1"]))
        if s["kind"] == "arc":
            a1 = math.atan2(s["y1"] - s["cy"], s["x1"] - s["cx"])
            a2 = math.atan2(s["y2"] - s["cy"], s["x2"] - s["cx"])
            r = math.hypot(s["x1"] - s["cx"], s["y1"] - s["cy"])
            sweep = ((a2 - a1) * s["dir"]) % (2 * math.pi) or 2 * math.pi
            steps = max(int(sweep / SAMPLE_STEP), 1)
            for k in range(1, steps):
                a = a1 + s["dir"] * sweep * k / steps
                pts.append((s["cx"] + r * math.cos(a), s["cy"] + r * math.sin(a)))
    try:
        check_simple_polygon(pts)
    except FeatureRequestError as ex:
        raise FeatureRequestError(f"the outline crosses or touches itself ({ex}): check the order of the circles and their turns") from ex
