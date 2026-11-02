"""Feature: revolve a section drawn ON a face plane about a vertical axis IN that plane (pipe bends, rings, tori, washers).

Measured live in SolidWorks (probes 2026-10-21, archive knowledge/revolve_probe3_notes.md):
- the section is sketched on the FRONT datum at an offset (the face plane) together with a separate CONSTRUCTION line on the axis; the revolve node names that line;
- a round section, a bored (two concentric circles) section and a ring that does not touch the axis all revolve correctly (hollow bends included);
- the sweep direction follows the RIGHT-HAND rule about the drawn axis direction, for a section that does not touch the axis:
  sweep_z = -(axis y direction) x (side of the section), side = +1 right of the axis, -1 left. Measured: section left, axis up -> +z (P3a, F2); left, axis down -> -z (P3b);
  right, axis down -> +z (the first back-face U-bend sank into the plate: 4,726 mm3 instead of 7,106, reproduced by calculation). So the axis is drawn upward when
  sweep x side = -1 and downward otherwise. A section with an edge ON the axis gave +z on the right side with the axis up in an earlier probe, which contradicts the rule:
  that case is refused (not understood);
- the tool finds the axis only for a FRONT-datum sketch: an axis along the face normal (top / right datum) fails (AXIS_NOT_FOUND), so it is never built here;
- a revolve CUT does not exist (the tool silently builds a boss).
So: front face -> the bend grows toward the viewer (+z), back face -> away from the plate (-z); the axis direction is chosen to make it so.

Volume added (exact): V = angle x first moment of the section about the axis (Pappus), checked against SolidWorks. The start cap lies on the face
(it merges into the plate: one body); a sweep of at most 180 degrees stays on the free side of the face plane, so the volume is purely additive.
Only angles in ALLOWED_ANGLES have been measured; anything else is refused (needs_human) until a live test widens the set.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from app.geometry import profile_area, profile_bounds
from app.verify import Expect

from .base import (AREA_TOL_M2, FeatureContext, FeatureRequestError, FeatureResult, Probe, circle_node, factor, graph_of, profile_to_graph, read_geometry,
                   sketch_node)
from .revolve_section import (check_section_loop, circle_section_volume, side_of_vertical_axis, volume_about_vertical)

MM3_PER_UNIT3 = {"mm": 1.0, "inch": 25.4 ** 3}
ALLOWED_ANGLES: set[float] = {45.0, 90.0, 135.0, 180.0}   # measured live 2026-10-21: 90 (P3a, P4, F2), 180 (F3), 45 (F4), 135 (F5); more than 180 would sweep into the plate
EPS = 1e-9


@dataclass(frozen=True)
class RevolveOnFace:
    """All numbers in the drawing's unit, in the datum's CENTRED frame (origin = centre of the base outline).
    `section`: either one closed outline of lines / arcs, or 1-2 concentric circles {"kind": "circle", diameter, cx, cy} (outer first, then the bore)."""
    unit: str
    plane_height: float                      # z of the face plane = the sketch plane offset
    sweep: int                               # +1 front face (axis drawn upward, sweeps +z), -1 back face (axis drawn downward, sweeps -z)
    section: list[dict]
    axis_x: float                            # the vertical axis x in the datum frame
    angle_deg: float
    outline_wh: tuple[float, float] | None = None       # the base outline size: the section must lie within it
    expect_bodies: int | None = 1
    label: str = "revolve on face"
    regression_delta_mm3: float | None = None

    NAME = "revolve_on_face"

    # ---------------------------------------------------------------- pure parts
    def is_round(self) -> bool:
        return bool(self.section) and all(s.get("kind") == "circle" for s in self.section)

    def _round_parts(self) -> tuple[float, float | None, float, float]:
        """(outer diameter, bore diameter or None, cx, cy) of a round section."""
        cs = self.section
        if len(cs) > 2:
            raise FeatureRequestError("a round section is one circle or a circle with a bore (two concentric circles)")
        outer = cs[0]
        bore = cs[1] if len(cs) == 2 else None
        if bore is not None and (abs(bore["cx"] - outer["cx"]) > EPS or abs(bore["cy"] - outer["cy"]) > EPS):
            raise FeatureRequestError("the bore must be concentric with the outer circle")
        if bore is not None and bore["diameter"] >= outer["diameter"]:
            raise FeatureRequestError("the bore must be smaller than the outer circle")
        if outer["diameter"] <= 0 or (bore is not None and bore["diameter"] <= 0):
            raise FeatureRequestError("diameters must be positive")
        return outer["diameter"], (bore["diameter"] if bore else None), outer["cx"], outer["cy"]

    def x_extent(self) -> tuple[float, float, float, float]:
        if self.is_round():
            d, _, cx, cy = self._round_parts()
            return cx - d / 2, cx + d / 2, cy - d / 2, cy + d / 2
        return profile_bounds(self.section)

    def validate(self) -> None:
        factor(self.unit)
        if self.sweep not in (1, -1):
            raise FeatureRequestError("sweep must be +1 (front face) or -1 (back face)")
        if not any(math.isclose(self.angle_deg, a, abs_tol=1e-9) for a in ALLOWED_ANGLES):
            raise FeatureRequestError(f"angle {self.angle_deg:g} degrees has not been measured in SolidWorks: only {sorted(ALLOWED_ANGLES)} are built "
                                      f"(more than 180 would also sweep into the plate): needs_human")
        if not self.section:
            raise FeatureRequestError("no section")
        if self.is_round():
            self._round_parts()
        else:
            if any(s.get("kind") == "circle" for s in self.section):
                raise FeatureRequestError("a section is either circles or one closed outline, not both")
            check_section_loop(self.section, "the section")
        x0, x1, y0, y1 = self.x_extent()
        if self.is_round():
            if x0 < self.axis_x + 1e-6 and x1 > self.axis_x - 1e-6:
                raise FeatureRequestError("a round section must lie clear of the axis (a circle touching or crossing it makes no valid solid)")
        else:
            side = side_of_vertical_axis(self.section, self.axis_x)
            distance = x0 - self.axis_x if side > 0 else self.axis_x - x1
            if distance < 1e-6:
                raise FeatureRequestError("the section touches the axis: only a section that lies CLEAR of the axis has a measured sweep direction (a section with an edge "
                                          "on the axis behaved differently in an earlier probe): needs_human")
        if self.outline_wh is not None:
            W, H = self.outline_wh
            if x0 < -W / 2 - 1e-6 or x1 > W / 2 + 1e-6 or y0 < -H / 2 - 1e-6 or y1 > H / 2 + 1e-6:
                raise FeatureRequestError(f"the section ({x0:g}..{x1:g}, {y0:g}..{y1:g}) leaves the base outline ({W:g} x {H:g}): the start cap must lie on the face")

    def section_area(self) -> float:
        if self.is_round():
            d, b, _, _ = self._round_parts()
            return math.pi / 4 * (d * d - (b or 0.0) ** 2)
        return profile_area(self.section)

    def expected_delta_mm3(self) -> float:
        k = MM3_PER_UNIT3[self.unit]
        if self.is_round():
            d, b, cx, _ = self._round_parts()
            return circle_section_volume(d, b, cx - self.axis_x, self.angle_deg) * k
        return volume_about_vertical(self.section, self.axis_x, self.angle_deg) * k

    def expected(self) -> Expect:
        return Expect(delta_mm3=self.expected_delta_mm3(), regression_delta_mm3=self.regression_delta_mm3, bodies=self.expect_bodies)

    def side(self) -> int:
        """+1 / -1: the section lies right / left of the axis."""
        if self.is_round():
            return 1 if self._round_parts()[2] > self.axis_x else -1
        return side_of_vertical_axis(self.section, self.axis_x)

    def axis_up(self) -> bool:
        """Right-hand rule (measured): sweep_z = -(axis direction) x side, so the axis is drawn upward exactly when sweep x side = -1."""
        return self.sweep * self.side() < 0

    def axis_segment(self) -> tuple[tuple[float, float], tuple[float, float]]:
        """The construction axis line (drawing unit), drawn in the direction that makes the bend grow away from the plate."""
        _, _, y0, y1 = self.x_extent()
        margin = max(1.0, 0.1 * (y1 - y0))
        lo, hi = (self.axis_x, y0 - margin), (self.axis_x, y1 + margin)
        return (lo, hi) if self.axis_up() else (hi, lo)

    def graph(self) -> dict:
        self.validate()
        f = factor(self.unit)
        if self.is_round():
            nodes = [circle_node(c["diameter"], c["cx"] * f, c["cy"] * f, self.unit) for c in self.section]
        else:
            nodes = profile_to_graph(self.section, self.unit, "centre")
        a, b = self.axis_segment()
        axis = {"x1": a[0] * f, "y1": a[1] * f, "x2": b[0] * f, "y2": b[1] * f}
        construction = {"kind": "line", **axis, "construction": True}
        return graph_of([sketch_node("n1", "front", self.plane_height * f, nodes + [construction]),
                         {"id": "n2", "type": "revolve", "sketch": "n1", "axis": axis, "angle": math.radians(self.angle_deg)}])

    def probes(self, faces: list[dict]) -> tuple[list[Probe], list[str]]:
        f = factor(self.unit)
        notes: list[str] = []
        out: list[Probe] = []
        if self.angle_deg < 180 - 1e-9:
            z0 = self.plane_height * f
            area = self.section_area() * f * f
            hit = [p for p in faces if p.get("planar") and abs(p["area"] - area) <= AREA_TOL_M2 and abs(p["normal"][1]) < 1e-3
                   and (p["point"][2] - z0) * self.sweep > 1e-4]
            near = [(round(p["point"][2] * 1000, 3), round(p["area"] * 1e6, 1)) for p in faces if p.get("planar") and abs(p["normal"][1]) < 1e-3]
            out.append(Probe("free end cap of the sweep: a planar face with the section's area on the free side of the face plane", bool(hit),
                             f"expected area {area * 1e6:.1f} mm2 on the {'+z' if self.sweep > 0 else '-z'} side of z = {z0 * 1000:.3f} mm; planar faces seen "
                             f"(z mm, area mm2): {near}"))
        else:
            notes.append("180 degrees: the end cap comes back to the face plane and may merge into the plate, so no end-cap probe; volume and body count decide")
        return out, notes

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
        probes, notes = self.probes(faces)
        res.probes += probes
        res.notes += notes
        return res
