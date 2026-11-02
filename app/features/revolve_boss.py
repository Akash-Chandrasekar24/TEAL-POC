"""General feature: revolve BOSS of a half-section around a vertical axis (a pin, a shaft, a stepped turned part, a cone, a dome).

Measured live in SolidWorks: the checklist route works: one sketch on the FRONT datum with the closed half-section (its edge
ALONG the axis included) plus that same segment again as a construction line, then {"type": "revolve", "axis": {...}, "angle": radians}.
The FRONT datum is not mirrored: sketch x = world X, the plane offset = world Z = minus the plan y. The tool IGNORES an `operation`
on a revolve node (a "cut" silently builds a boss), so a revolve CUT is refused here, never sent.

Request model (the drawing's unit, positions as printed): the axis stands at plan position `axis_at`, parallel to the TOP datum's normal
(world Y); `profile` is the half-section in (r, h): r = distance from the axis (>= 0), h = height above `base_height`
(`base_height` = where h = 0 lies along the datum normal). Lines and arcs; arcs carry dir (+1 counter-clockwise in the (r, h) frame).
Exactly one line lies ON the axis (r = 0); the profile never crosses the axis.

Expected volume added (exact, Pappus / Green): V = pi x |closed integral of r^2 dh| around the profile. Cylinder, cone, sphere and torus are
exact by construction. `base_on_face`: the lowest horizontal edge lies ON an existing face, so it merges into it and makes no new face.
Refused (needs_human) because their behaviour has not been measured: a partial angle (which way it sweeps), an axis that is not vertical, the `right`
datum, a profile that does not touch the axis along an edge (a ring / washer), any operation but 'boss'.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from app.geometry import _arc_sweep
from app.verify import Expect

from .base import (FeatureContext, FeatureRequestError, FeatureResult, PlaneFrame, Position, Probe, check_closed, factor, graph_of,
                   probe_cylinders, probe_planar_face, read_geometry, sketch_node)
from .outline_helpers import check_simple_polygon

MM3_PER_UNIT3 = {"mm": 1.0, "inch": 25.4 ** 3}
EPS = 1e-9


# ------------------------------------------------------------------ pure geometry of an (r, h) profile
def revolve_volume(profile: list[dict]) -> float:
    """Volume of the solid made by revolving the closed (r, h) profile 360 degrees about r = 0, in unit^3: pi |closed integral r^2 dh|."""
    total = 0.0
    for s in profile:
        if s["kind"] == "line":
            r1, r2, h1, h2 = s["x1"], s["x2"], s["y1"], s["y2"]
            total += (h2 - h1) * (r1 * r1 + r1 * r2 + r2 * r2) / 3
        elif s["kind"] == "arc":
            cx, cy = s["cx"], s["cy"]
            radius = math.hypot(s["x1"] - cx, s["y1"] - cy)
            a1, sweep = _arc_sweep(s)
            a2 = a1 + sweep
            c1 = math.sin(a2) - math.sin(a1)                                        # integral of cos
            c2 = (a2 - a1) / 2 + (math.sin(2 * a2) - math.sin(2 * a1)) / 4          # integral of cos^2
            c3 = (math.sin(a2) - math.sin(a2) ** 3 / 3) - (math.sin(a1) - math.sin(a1) ** 3 / 3)   # integral of cos^3
            total += radius * (cx * cx * c1 + 2 * cx * radius * c2 + radius * radius * c3)
        else:
            raise FeatureRequestError(f"profile segment kind {s.get('kind')!r} is not supported")
    return math.pi * abs(total)


def _sample(profile: list[dict], per_arc: int = 24) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for s in profile:
        if s["kind"] == "line":
            pts.append((s["x1"], s["y1"]))
        else:
            a1, sweep = _arc_sweep(s)
            radius = math.hypot(s["x1"] - s["cx"], s["y1"] - s["cy"])
            pts += [(s["cx"] + radius * math.cos(a1 + sweep * k / per_arc), s["cy"] + radius * math.sin(a1 + sweep * k / per_arc)) for k in range(per_arc)]
    return pts


def _r_extent(profile: list[dict]) -> float:
    """Smallest r reached by the profile, arcs included (an arc may bulge past its end points): refuses a profile that crosses the axis."""
    lo = min(v for s in profile for v in (s["x1"], s["x2"]))
    for s in profile:
        if s["kind"] != "arc":
            continue
        a1, sweep = _arc_sweep(s)
        radius = math.hypot(s["x1"] - s["cx"], s["y1"] - s["cy"])
        d = (math.pi - a1) % (2 * math.pi) if sweep > 0 else -((a1 - math.pi) % (2 * math.pi))      # is the point (cx - radius, cy) on the arc?
        if abs(d) < abs(sweep) - 1e-12:
            lo = min(lo, s["cx"] - radius)
    return lo


@dataclass(frozen=True)
class RevolveBoss:
    unit: str
    plane: str                                   # 'top': the axis is parallel to the top datum's normal (vertical), positions in top-plan coordinates
    axis_at: Position                            # plan position of the axis, as printed
    base_height: float                           # height (along the datum normal) where h = 0 lies
    profile: list[dict]                          # closed half-section in (r, h): segments {"kind": "line", x1, y1, x2, y2} / {"kind": "arc", cx, cy, x1, y1, x2, y2, dir}
    angle_deg: float = 360.0
    operation: str = "boss"
    base_on_face: bool = True                    # the lowest horizontal edge lies on an existing face (merges, no new face)
    expect_bodies: int | None = 1
    label: str = "revolve boss"
    regression_delta_mm3: float | None = None

    NAME = "revolve_boss"

    # ---------------------------------------------------------------- pure parts
    def validate(self) -> PlaneFrame:
        frame = PlaneFrame("top")
        factor(self.unit)
        if self.plane != "top":
            raise FeatureRequestError(f"only axes parallel to the top datum's normal are probed (plane='top'), got {self.plane!r}: needs_human")
        if self.operation != "boss":
            raise FeatureRequestError(f"operation {self.operation!r}: the revolve tool ignores the operation and builds a BOSS (measured); "
                                      "a revolve cut is not available: needs_human")
        if not math.isclose(self.angle_deg, 360.0, abs_tol=1e-9):
            raise FeatureRequestError(f"angle {self.angle_deg}: which way a partial revolve sweeps has not been probed; only 360 is built: needs_human")
        if not self.profile:
            raise FeatureRequestError("empty profile")
        for s in self.profile:
            if s.get("kind") == "arc" and s.get("dir") not in (1, -1):
                raise FeatureRequestError("every arc needs dir = 1 (counter-clockwise) or -1 (clockwise)")
            if s.get("kind") not in ("line", "arc"):
                raise FeatureRequestError(f"profile segment kind {s.get('kind')!r} is not supported")
        check_closed(self.profile)
        if min(v for s in self.profile for v in (s["x1"], s["x2"])) < -EPS or _r_extent(self.profile) < -EPS:
            raise FeatureRequestError("the profile crosses the axis (r < 0): the revolved solid would overlap itself")
        axis_edges = [s for s in self.profile if s["kind"] == "line" and abs(s["x1"]) < EPS and abs(s["x2"]) < EPS and abs(s["y1"] - s["y2"]) > EPS]
        if len(axis_edges) != 1:
            raise FeatureRequestError(f"the profile must have exactly one line ON the axis (r = 0), found {len(axis_edges)}: a profile that does not touch "
                                      "the axis along an edge (ring, washer) has not been probed: needs_human")
        try:
            check_simple_polygon(_sample(self.profile))
        except FeatureRequestError as ex:
            raise FeatureRequestError(f"the profile crosses or touches itself ({ex})") from ex
        if revolve_volume(self.profile) <= 0:
            raise FeatureRequestError("the profile encloses no volume")
        return frame

    def axis_edge(self) -> dict:
        return next(s for s in self.profile if s["kind"] == "line" and abs(s["x1"]) < EPS and abs(s["x2"]) < EPS and abs(s["y1"] - s["y2"]) > EPS)

    def expected_delta_mm3(self) -> float:
        return revolve_volume(self.profile) * MM3_PER_UNIT3[self.unit]

    def base_edge(self) -> dict | None:
        """The lowest horizontal line (it lies on the existing face when base_on_face)."""
        if not self.base_on_face:
            return None
        lo = min(min(s["y1"], s["y2"]) for s in self.profile)
        return next((s for s in self.profile if s["kind"] == "line" and abs(s["y1"] - lo) < EPS and abs(s["y2"] - lo) < EPS and abs(s["x1"] - s["x2"]) > EPS), None)

    def new_face_count(self) -> int:
        """Faces the revolve adds: one per profile edge, except the axis edge and the base edge merged into an existing face."""
        axis, base = self.axis_edge(), self.base_edge()
        return sum(1 for s in self.profile if s is not axis and s is not base)

    def expected(self) -> Expect:
        return Expect(delta_mm3=self.expected_delta_mm3(), regression_delta_mm3=self.regression_delta_mm3, bodies=self.expect_bodies)

    def sketch_profile_m(self) -> list[dict]:
        """The half-section in the FRONT-datum sketch frame, metres: sketch x = axis x + r, sketch y = base_height + h."""
        f = factor(self.unit)
        ax, _ = self.axis_at.to_sketch_m(self.unit)
        base = self.base_height * f
        out = []
        for s in self.profile:
            if s["kind"] == "line":
                out.append({"kind": "line", "x1": ax + s["x1"] * f, "y1": base + s["y1"] * f, "x2": ax + s["x2"] * f, "y2": base + s["y2"] * f})
            else:
                out.append({"kind": "arc", "cx": ax + s["cx"] * f, "cy": base + s["cy"] * f, "x1": ax + s["x1"] * f, "y1": base + s["y1"] * f,
                            "x2": ax + s["x2"] * f, "y2": base + s["y2"] * f, "dir": s["dir"]})
        return out

    def graph(self) -> dict:
        self.validate()
        f = factor(self.unit)
        _, ay = self.axis_at.to_sketch_m(self.unit)
        seg = self.sketch_profile_m()
        edge = self.axis_edge()
        ax_line = next(m for m, s in zip(seg, self.profile) if s is edge)
        lo, hi = sorted(((ax_line["x1"], ax_line["y1"]), (ax_line["x2"], ax_line["y2"])), key=lambda p: p[1])
        axis = {"x1": lo[0], "y1": lo[1], "x2": hi[0], "y2": hi[1]}          # base to top: the direction that worked in the 4a probe
        construction = {"kind": "line", **axis, "construction": True}
        return graph_of([sketch_node("n1", "front", -ay, seg + [construction]),
                         {"id": "n2", "type": "revolve", "sketch": "n1", "axis": axis, "angle": 2 * math.pi}])

    def probes(self, faces: list[dict], faces_before: int, faces_after: int) -> list[Probe]:
        f = factor(self.unit)
        frame = PlaneFrame("top")
        ax, ay = self.axis_at.to_sketch_m(self.unit)
        a, b = frame.in_plane_world(ax, ay)
        base = self.base_edge()
        cyl, rings = [], []
        for s in self.profile:
            if s["kind"] != "line" or s is self.axis_edge() or s is base:
                continue
            if abs(s["x1"] - s["x2"]) < EPS and s["x1"] > EPS:                       # constant r: a cylinder wall
                cyl.append((s["x1"] * f, a, b))
            elif abs(s["y1"] - s["y2"]) < EPS:                                       # constant h: a flat ring or disc
                rings.append(((self.base_height + s["y1"]) * f, math.pi * abs(s["x1"] ** 2 - s["x2"] ** 2) * f * f))
        out = []
        if cyl:
            out.append(probe_cylinders(faces, cyl, frame, "revolved cylinder walls: radius and position of the axis"))
        for at, area in rings:
            out.append(probe_planar_face(faces, 1, at, area, f"flat face of the revolve at {at * 1000:.3f} mm"))
        added = faces_after - faces_before
        out.append(Probe("one new face per profile edge", added == self.new_face_count(), f"{added} face(s) added, {self.new_face_count()} expected"))
        return out

    # ---------------------------------------------------------------- run
    async def apply(self, ctx: FeatureContext) -> FeatureResult:
        self.validate()
        graph = self.graph()
        res = FeatureResult(self.NAME)

        async def action():
            return await ctx.sw.submit_feature_graph(graph, fresh_document=False)

        v = await ctx.verifier.run_step(self.label, action, self.expected())
        res.verdicts.append(v)
        if not v.ok:
            return res
        res.notes.append(f"measured volume change {v.measured_delta_mm3:.2f} mm3 (Pappus {self.expected_delta_mm3():.2f})")
        faces, _ = await read_geometry(ctx.sw)
        res.probes += self.probes(faces, v.before.faces, v.after.faces)
        return res
