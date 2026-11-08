"""General feature: fillet or chamfer edges chosen by LOCATION (never by hard-coded edge numbers).

Selection rule (measured in SolidWorks): an edge is "in the plane" when its start, mid and end all lie
in the plane at `height` along the datum normal. Lines, full circles and partial arcs all do. `mid` of a partial arc is its
CHORD midpoint, so an arc is recognised by both end points lying on one cylinder face's circle (axis along the datum normal);
its true length comes from that face (area / height), which also tells a minor from a major arc.

SolidWorks rounds a whole chain of tangent edges at once (measured: a fillet removed 1,495 mm3 where
a hand estimate said 1,020), so the feature computes that chain ("closure") in code and reports the edges it adds. Its own propagation is NOT
reliable: given ONE side of a slot rim it chamfered the upper side and both end arcs but stopped before the lower side
(measured: 3 faces instead of 4, 43 mm3 instead of 55.6 mm3). So the feature sends EVERY edge of the chain
explicitly, and the probes check one new face per edge.

Expected volume change (mm3): every edge of the closure contributes  sign x A x L x (1 + side x k / R)  with
    A = d^2 / 2 (chamfer) or (1 - pi/4) r^2 (fillet)   the area of the removed (added) corner section,
    k = d / 3 (chamfer) or (10 - 3 pi) / (12 - 3 pi) r = 0.2234 r (fillet)   the distance of that section's centre from the edge,
    L the edge length, R its radius (a straight edge has no R: factor 1). Pappus: on a curved edge the section's centre is NOT
    on the edge (measured 2026-10-12: a 1 mm chamfer on the R7 rim of a boss removed 20.94 mm3, not 21.99 = d^2/2 x 2 pi R).
    sign is -1 for a convex 90-degree edge (material removed) and +1 for a concave one (added); side is -1 when the flat face
    lies INSIDE the circle (a boss rim: the section is closer to the axis) and +1 when it lies outside (a hole rim, the foot
    of a boss). The side is read from the flat face's representative point for a FULL circle; for a partial arc it cannot be
    read, so the feature claims an interval (both sides) and a measured value anywhere inside it passes.
It is only claimed when EVERY closure edge is understood: convexity known from the adjacent faces, length exact, and no
corner where two closure edges meet at an angle (corner blends change the volume). Otherwise no volume is claimed, the step
only has to change the model, and the measured value is logged. analyze_model reports volumes with 6 significant digits
(1 mm3 at 434,000 mm3), so the verifier never demands a tighter match than that.
Unequal chamfers (two distances) are directional and need a flip probe: refused for now.
"""
from __future__ import annotations

import json
import math
from collections import deque
from dataclasses import dataclass, field

from app.verify import Expect

from .base import POS_TOL_M, FeatureContext, FeatureRequestError, FeatureResult, PlaneFrame, Probe, factor, read_geometry

MM3_PER_UNIT3 = {"mm": 1.0, "inch": 25.4 ** 3}
PT_TOL = 5e-6                 # two reported points are the same point (6 decimals of a metre)
PARALLEL_TOL = 1e-3           # |sin| of the angle between two directions that count as tangent
LENGTH_TOL = 0.02             # relative: arc length from the face area must match minor or major candidate


# ------------------------------------------------------------------ classified edges
@dataclass(frozen=True)
class EdgeInfo:
    i: int
    kind: str                                 # 'line' | 'arc' | 'circle'
    start: tuple[float, float, float]
    end: tuple[float, float, float]
    length_m: float
    length_exact: bool
    radius_m: float | None = None
    centre_ab: tuple[float, float] | None = None      # in-plane world coordinates (axes of PlaneFrame.in_plane_axes)
    convexity: str = "unknown"                # 'convex' | 'concave' | 'unknown'
    flat_side: str = "unknown"                # circles/arcs: 'inside' | 'outside' | 'unknown' (where the flat face lies relative to the circle)


def _ab(p, frame: PlaneFrame) -> tuple[float, float]:
    a, b = frame.in_plane_axes
    return p[a], p[b]


def _unit2(v: tuple[float, float]) -> tuple[float, float]:
    n = math.hypot(*v)
    return v[0] / n, v[1] / n


def edge_in_plane(e: dict, axis: int, height_m: float) -> bool:
    return all(abs(p[axis] - height_m) <= POS_TOL_M for p in (e["start"], e["mid"], e["end"]))


def _cylinders(faces: list[dict], axis: int) -> list[dict]:
    return [f for f in faces if f.get("kind") == "cylinder" and abs(abs(f["axis"][axis]) - 1) < 1e-6]


def _on_circle(p, cyl: dict, frame: PlaneFrame) -> bool:
    a, b = frame.in_plane_axes
    return abs(math.hypot(p[a] - cyl["origin"][a], p[b] - cyl["origin"][b]) - cyl["radius"]) <= PT_TOL


def _arc_length(cyl: dict, chord: float, height_m: float, axis: int) -> tuple[float, bool]:
    """(length, exact). Candidates: minor arc and major arc for the chord on that radius; the cylinder wall's area divided by
    its height (twice the distance from the plane to the wall's representative mid-height point) says which one."""
    r = cyl["radius"]
    minor = r * 2 * math.asin(min(1.0, chord / (2 * r)))
    major = 2 * math.pi * r - minor
    if abs(minor - major) <= 1e-9:
        return minor, True                                    # a semicircle: no ambiguity
    wall_h = 2 * abs(height_m - cyl["point"][axis])
    if wall_h > 1e-9 and cyl.get("area"):
        from_face = cyl["area"] / wall_h
        best = min((minor, major), key=lambda c: abs(c - from_face))
        if abs(best - from_face) <= LENGTH_TOL * best:
            return best, True
    return minor, False


def _convexity(info: EdgeInfo, faces: list[dict], frame: PlaneFrame, axis: int, height_m: float, cyls: list[dict]) -> str:
    """'convex' / 'concave' / 'unknown' for an edge in the plane. A flat face normal to the datum axis lies at the plane and
    points up (or down); the wall carrying the edge extends below (above) the plane. Flat face up + wall below = convex
    (a hole rim, an outline rim, a boss top); flat face up + wall above = concave (the foot of a boss). Both are 90 degrees by
    construction: the wall is a plane parallel to the axis or a cylinder around it. Anything else is 'unknown'."""
    flats = [f for f in faces if f.get("planar") and abs(abs(f["normal"][axis]) - 1) < 1e-3 and abs(f["point"][axis] - height_m) <= POS_TOL_M]
    signs = {1 if f["normal"][axis] > 0 else -1 for f in flats}
    if len(signs) != 1:
        return "unknown"
    up = signs.pop() > 0
    walls: list[float] = []                                     # representative heights of the walls that carry this edge
    if info.kind == "line":
        for f in faces:
            if f.get("planar") and abs(f["normal"][axis]) < 1e-3 and all(
                    abs(sum(n * (p - q) for n, p, q in zip(f["normal"], pt, f["point"]))) <= PT_TOL for pt in (info.start, info.end)):
                walls.append(f["point"][axis])
    else:
        for c in cyls:
            if abs(c["radius"] - info.radius_m) <= PT_TOL and _on_circle(info.start, c, frame) and _on_circle(info.end, c, frame):
                walls.append(c["point"][axis])
    below = [w for w in walls if w < height_m - PT_TOL]
    above = [w for w in walls if w > height_m + PT_TOL]
    if len(below) + len(above) != 1:
        return "unknown"
    return "convex" if (up and below) or (not up and above) else "concave"


def _flat_side(info: EdgeInfo, faces: list[dict], frame: PlaneFrame, axis: int, height_m: float) -> str:
    """Full circles only: 'inside' when the flat face(s) at the plane have their representative point inside the circle
    (the cap of a boss), 'outside' when outside (the plate around a hole, the foot of a boss), else 'unknown'."""
    if info.kind != "circle":
        return "unknown"
    flats = [f for f in faces if f.get("planar") and abs(abs(f["normal"][axis]) - 1) < 1e-3 and abs(f["point"][axis] - height_m) <= POS_TOL_M]
    ca, cb = info.centre_ab
    a, b = frame.in_plane_axes
    d = [math.hypot(f["point"][a] - ca, f["point"][b] - cb) for f in flats]
    inside, outside = any(x < info.radius_m - PT_TOL for x in d), any(x > info.radius_m + PT_TOL for x in d)
    return "inside" if inside and not outside else "outside" if outside and not inside else "unknown"


def classify_in_plane(faces: list[dict], edges: list[dict], frame: PlaneFrame, height_m: float) -> list[EdgeInfo]:
    axis = frame.normal_axis
    cyls = _cylinders(faces, axis)
    a, b = frame.in_plane_axes
    out: list[EdgeInfo] = []
    for e in edges:
        if not edge_in_plane(e, axis, height_m):
            continue
        s, t, m = tuple(e["start"]), tuple(e["end"]), tuple(e["mid"])
        chord = math.dist(s, t)
        if chord < PT_TOL:                                       # full circle: `mid` is the opposite point
            r = math.dist(s, m) / 2
            centre = ((s[a] + m[a]) / 2, (s[b] + m[b]) / 2)
            info = EdgeInfo(e["i"], "circle", s, t, 2 * math.pi * r, True, r, centre)
        else:
            cyl = next((c for c in cyls if _on_circle(s, c, frame) and _on_circle(t, c, frame)
                        and abs(math.hypot(m[a] - c["origin"][a], m[b] - c["origin"][b]) - c["radius"]) > PT_TOL), None)
            if cyl is not None:                                  # `mid` is the chord midpoint, NOT on the circle
                length, exact = _arc_length(cyl, chord, height_m, axis)
                info = EdgeInfo(e["i"], "arc", s, t, length, exact, cyl["radius"], (cyl["origin"][a], cyl["origin"][b]))
            else:
                info = EdgeInfo(e["i"], "line", s, t, chord, True)
        out.append(EdgeInfo(**{**info.__dict__, "convexity": _convexity(info, faces, frame, axis, height_m, cyls),
                               "flat_side": _flat_side(info, faces, frame, axis, height_m)}))
    return out


# ------------------------------------------------------------------ tangent chains
def _end_tangent(info: EdgeInfo, point, frame: PlaneFrame) -> tuple[float, float]:
    pa = _ab(point, frame)
    if info.kind == "arc":
        ra, rb = pa[0] - info.centre_ab[0], pa[1] - info.centre_ab[1]
        return _unit2((-rb, ra))
    sa, ea = _ab(info.start, frame), _ab(info.end, frame)
    return _unit2((ea[0] - sa[0], ea[1] - sa[1]))


def _ends(info: EdgeInfo):
    return [] if info.kind == "circle" else [info.start, info.end]


def _neighbours(infos: list[EdgeInfo], frame: PlaneFrame):
    """[(i, j, tangent)] for every pair of in-plane edges that share an end point; tangent = their directions are parallel there."""
    out = []
    for x in range(len(infos)):
        for y in range(x + 1, len(infos)):
            for p in _ends(infos[x]):
                for q in _ends(infos[y]):
                    if math.dist(p, q) <= PT_TOL:
                        d1, d2 = _end_tangent(infos[x], p, frame), _end_tangent(infos[y], q, frame)
                        out.append((x, y, abs(d1[0] * d2[1] - d1[1] * d2[0]) <= PARALLEL_TOL))
    return out


def tangent_closure(chosen: list[EdgeInfo], infos: list[EdgeInfo], frame: PlaneFrame) -> tuple[list[EdgeInfo], bool]:
    """(chosen edges plus every in-plane edge reached through tangent joints, True when two closure edges meet at an angle)."""
    pos = {inf.i: n for n, inf in enumerate(infos)}
    nbrs = _neighbours(infos, frame)
    seen = {pos[c.i] for c in chosen}
    queue = deque(seen)
    while queue:
        x = queue.popleft()
        for p, q, tangent in nbrs:
            if tangent and x in (p, q):
                o = q if x == p else p
                if o not in seen:
                    seen.add(o)
                    queue.append(o)
    corner = any(p in seen and q in seen and not tangent for p, q, tangent in nbrs)
    return [infos[n] for n in sorted(seen)], corner


# ------------------------------------------------------------------ request
@dataclass(frozen=True)
class EdgeSelector:
    """Which edges, by location. Numbers in the drawing's unit; `region` is (x_min, x_max, y_min, y_max) in the plan
    coordinates of the datum plane (the same frame positions are printed in); an edge qualifies when both its end points
    (a circle: its whole extent) lie inside. kinds: any of 'line', 'arc', 'circle'. radius filters arcs and circles, length lines."""
    plane: str                                   # 'top' | 'front': the datum whose normal the plane is perpendicular to
    height: float                                # position of the plane along that normal
    region: tuple[float, float, float, float] | None = None
    kinds: tuple[str, ...] = ("line", "arc", "circle")
    radius: float | None = None
    length: float | None = None
    tol: float = 0.01
    along: int | None = None                     # only straight edges running along this WORLD axis (0 x right, 1 y up, 2 z toward the viewer)


@dataclass
class Selection:
    chosen: list[EdgeInfo]
    closure: list[EdgeInfo]
    corner: bool
    in_plane: int

    @property
    def propagated(self) -> list[EdgeInfo]:
        have = {c.i for c in self.chosen}
        return [c for c in self.closure if c.i not in have]


@dataclass(frozen=True)
class FilletChamferEdges:
    unit: str
    kind: str                                    # 'fillet' | 'chamfer'
    size: float                                  # fillet radius, or the chamfer setback (45 degrees, equal on both faces)
    selector: EdgeSelector
    distance2: float | None = None               # unequal chamfer: refused for now
    expect_count: int | None = None              # number of edges the selector must find, else needs_human
    max_edges: int = 40
    label: str = "fillet/chamfer by location"
    regression_delta_mm3: float | None = None

    NAME = "fillet_chamfer_edges"

    # ---------------------------------------------------------------- pure parts
    def validate(self) -> PlaneFrame:
        factor(self.unit)
        frame = PlaneFrame(self.selector.plane)
        if self.kind not in ("fillet", "chamfer"):
            raise FeatureRequestError(f"kind must be 'fillet' or 'chamfer', got {self.kind!r}")
        if self.size <= 0:
            raise FeatureRequestError("size must be positive")
        if self.distance2 is not None and abs(self.distance2 - self.size) > 1e-12:
            raise FeatureRequestError("unequal chamfers (two distances) are directional and need a flip probe: not built yet")
        if self.kind == "fillet" and self.distance2 is not None:
            raise FeatureRequestError("a fillet has one radius")
        bad = [k for k in self.selector.kinds if k not in ("line", "arc", "circle")]
        if bad or not self.selector.kinds:
            raise FeatureRequestError(f"selector kinds must be some of line / arc / circle, got {self.selector.kinds!r}")
        return frame

    def select(self, faces: list[dict], edges: list[dict]) -> Selection:
        frame = self.validate()
        f, s = factor(self.unit), self.selector
        infos = classify_in_plane(faces, edges, frame, s.height * f)

        def plan(p):
            a, b = _ab(p, frame)
            return frame.signs[0] * a / f, frame.signs[1] * b / f

        def inside(info: EdgeInfo) -> bool:
            if s.region is None:
                return True
            x0, x1, y0, y1 = s.region
            pts = [plan(info.start), plan(info.end)]
            if info.kind == "circle":
                cx, cy = plan_centre(info)
                r = info.radius_m / f
                pts = [(cx - r, cy - r), (cx + r, cy + r)]
            return all(x0 - s.tol <= x <= x1 + s.tol and y0 - s.tol <= y <= y1 + s.tol for x, y in pts)

        def plan_centre(info: EdgeInfo):
            a, b = info.centre_ab
            return frame.signs[0] * a / f, frame.signs[1] * b / f

        def passes(info: EdgeInfo) -> bool:
            if info.kind not in s.kinds or not inside(info):
                return False
            if s.along is not None:
                if info.kind != "line" or info.length_m <= 0:
                    return False
                if abs(abs(info.end[s.along] - info.start[s.along]) / info.length_m - 1) > 1e-6:
                    return False
            if s.radius is not None and (info.radius_m is None or abs(info.radius_m / f - s.radius) > s.tol):
                return False
            return s.length is None or (info.kind == "line" and abs(info.length_m / f - s.length) <= s.tol)

        chosen = [i for i in infos if passes(i)]
        if not chosen:
            raise FeatureRequestError(f"{self.label}: no edge lies in the plane at {s.height} {self.unit} and matches the selector "
                                      f"({len(infos)} edge(s) are in that plane): needs_human")
        if len(chosen) > self.max_edges:
            raise FeatureRequestError(f"{self.label}: {len(chosen)} edges selected, more than max_edges={self.max_edges}: needs_human")
        if self.expect_count is not None and len(chosen) != self.expect_count:
            raise FeatureRequestError(f"{self.label}: the selector found {len(chosen)} edge(s), the request expects {self.expect_count}: needs_human")
        closure, corner = tangent_closure(chosen, infos, frame)
        return Selection(chosen, closure, corner, len(infos))

    def delta_range_mm3(self, sel: Selection) -> tuple[tuple[float, float] | None, str]:
        """((lowest, highest) signed volume change in mm3, or None, why not). lowest == highest when everything is exact."""
        if sel.corner:
            return None, "two edges of the chain meet at an angle: corner blends change the volume, no formula"
        unknown = [e.i for e in sel.closure if e.convexity == "unknown"]
        if unknown:
            return None, f"convexity of edge(s) {unknown} could not be read from the adjacent faces"
        inexact = [e.i for e in sel.closure if not e.length_exact]
        if inexact:
            return None, f"length of arc edge(s) {inexact} is ambiguous (minor or major arc)"
        f = factor(self.unit)
        if self.kind == "fillet":
            area, k = (1 - math.pi / 4) * self.size ** 2, (10 - 3 * math.pi) / (12 - 3 * math.pi) * self.size
        else:
            area, k = self.size ** 2 / 2, self.size / 3
        lo = hi = 0.0
        for e in sel.closure:
            length = e.length_m / f
            sign = 1 if e.convexity == "concave" else -1
            if e.kind == "line":
                factors = (1.0, 1.0)
            else:
                r = e.radius_m / f
                if k >= r:
                    return None, f"the {self.kind} size is too large for the radius {r:.3f} of edge {e.i}"
                side = {"inside": (-1,), "outside": (1,)}.get(e.flat_side, (-1, 1)) if e.kind == "circle" else (-1, 1)
                factors = (1 + min(side) * k / r, 1 + max(side) * k / r)
            vols = sorted(sign * area * length * x * MM3_PER_UNIT3[self.unit] for x in factors)
            lo, hi = lo + vols[0], hi + vols[-1]
        return (lo, hi), ""

    def expected(self, sel: Selection) -> tuple[Expect, str]:
        rng, why = self.delta_range_mm3(sel)
        if rng is None:
            return Expect(regression_delta_mm3=self.regression_delta_mm3), why
        lo, hi = rng
        if hi - lo <= 1e-9:
            return Expect(delta_mm3=lo, regression_delta_mm3=self.regression_delta_mm3), ""
        return Expect(range_mm3=(lo, hi), regression_delta_mm3=self.regression_delta_mm3), ""

    def tool_args(self, sel: Selection) -> dict:
        args = {"feature_type": self.kind, "radius_or_distance": self.size * factor(self.unit),
                "edge_indices": json.dumps(sorted(e.i for e in sel.closure))}
        if self.kind == "chamfer":
            args.update(chamfer_type="distance_angle", angle=45)
        return args

    def probes(self, sel: Selection, faces_before: int, faces_after: int, edges_after: list[dict]) -> list[Probe]:
        def same(x: dict, e: EdgeInfo) -> bool:
            ends = ((x["start"], e.start, x["end"], e.end), (x["start"], e.end, x["end"], e.start))
            return any(math.dist(p1, p2) <= PT_TOL and math.dist(p3, p4) <= PT_TOL for p1, p2, p3, p4 in ends)

        gone_not = [e.i for e in sel.closure if any(same(x, e) for x in edges_after)]
        out = [Probe("edges of the chain are gone", not gone_not,
                     "every edge of the chain was replaced" if not gone_not else f"edge(s) {gone_not} still exist unchanged")]
        added = faces_after - faces_before
        out.append(Probe("one new face per edge of the chain", added == len(sel.closure),
                         f"{added} face(s) added, {len(sel.closure)} edge(s) in the chain"))
        return out

    # ---------------------------------------------------------------- run
    async def apply(self, ctx: FeatureContext) -> FeatureResult:
        self.validate()
        faces, edges = await read_geometry(ctx.sw)
        sel = self.select(faces, edges)
        exp, why = self.expected(sel)
        res = FeatureResult(self.NAME)
        res.notes.append(f"selected edges {sorted(e.i for e in sel.chosen)} of {sel.in_plane} in the plane"
                         + (f"; the tangent chain {sorted(e.i for e in sel.propagated)} is added and sent explicitly "
                            "(SolidWorks's own propagation stopped short in a live test)" if sel.propagated else ""))
        rng, _ = self.delta_range_mm3(sel)
        if rng is not None:
            res.notes.append(f"expected volume change {rng[0]:.2f}" + (f" .. {rng[1]:.2f}" if abs(rng[1] - rng[0]) > 1e-9 else "") + " mm3"
                             + ("" if abs(rng[1] - rng[0]) < 1e-9 else " (an interval: the side of a partial arc is not readable)"))
        if why:
            res.notes.append(f"no exact volume claimed: {why}")
        args = self.tool_args(sel)

        async def action():
            return await ctx.sw.add_edge_feature(**args)

        v = await ctx.verifier.run_step(self.label, action, exp)
        res.verdicts.append(v)
        if not v.ok:
            return res
        res.notes.append(f"measured volume change {v.measured_delta_mm3:.2f} mm3")
        _, edges_after = await read_geometry(ctx.sw)
        res.probes += self.probes(sel, v.before.faces, v.after.faces, edges_after)
        return res
