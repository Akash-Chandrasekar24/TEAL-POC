"""Feature: a TURNED BASE, the first solid of a part: a shaft, bushing, pin or flanged sleeve revolved 360 degrees from its half-section.

The half-section is written as in a lathe drawing: x = u (along the axis), y = r (the radius, >= 0, measured from the axis). The axis is `horizontal`
(along x in the front view) or `vertical` (along y). Grooves, cones, fillets and bores are part of the profile: a bore is just a profile that stays away from
the axis (measured: a ring profile revolves correctly, hollow in the middle), because a revolve CUT does not exist in the tool.

Frame (so every other convention holds): the part is centred on the datum origin like any base; the sketch plane is the FRONT datum at z = R_max, so the solid spans
z = 0 .. 2 R_max (back face z = 0). Bounding box: (L, 2 R_max, 2 R_max) for a horizontal axis, (2 R_max, L, 2 R_max) for a vertical one.
A full 360-degree revolve does not depend on the sweep direction. Exactly one profile edge may lie on the axis (it is also drawn as the construction axis line,
the configuration measured live); a profile with NO edge on the axis (a through bore) gets a separate construction line along the axis.

Expected volume (exact, Pappus / Green): V = pi |closed integral of r^2 du|. Probes: every constant-radius wall is a cylinder of that radius on the axis line;
every flat step is a planar face of the right area at the right place; one new face per profile edge (the axis edge adds none).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from app.geometry import profile_bounds
from app.verify import Expect

from .base import (FeatureContext, FeatureRequestError, FeatureResult, Probe, factor, graph_of, probe_bbox, probe_planar_face, profile_to_graph, read_geometry,
                   sketch_node)
from .revolve_boss import EPS, revolve_volume
from .revolve_section import check_section_loop, lowest_r, probe_revolved_walls, swapped

MM3_PER_UNIT3 = {"mm": 1.0, "inch": 25.4 ** 3}


def turned_extent(profile_ur: list[dict]) -> tuple[float, float, float]:
    """(L, R_max, u_centre) of a (u, r) half-section: length along the axis, largest radius, middle of the length."""
    u0, u1, _, r1 = profile_bounds(profile_ur)
    return u1 - u0, r1, (u0 + u1) / 2


def turned_base_info(profile_ur: list[dict], axis: str) -> tuple[tuple[float, float], tuple[float, float], float]:
    """((W, H), frame_shift, thickness) of the turned base in the front view: horizontal axis (L, 2R), vertical axis (2R, L); z thickness 2R."""
    length, rmax, _ = turned_extent(profile_ur)
    wh = (length, 2 * rmax) if axis == "horizontal" else (2 * rmax, length)
    return wh, (wh[0] / 2, wh[1] / 2), 2 * rmax


@dataclass(frozen=True)
class RevolveBase:
    unit: str
    axis: str                                # 'horizontal' | 'vertical' (as drawn in the front view)
    profile: list[dict]                      # the half-section in (u, r), the drawing's unit: closed lines / arcs, r >= 0
    label: str = "revolve base"
    regression_delta_mm3: float | None = None

    NAME = "revolve_base"

    # ---------------------------------------------------------------- pure parts
    def axis_edges(self) -> list[dict]:
        return [s for s in self.profile if s["kind"] == "line" and abs(s["y1"]) < EPS and abs(s["y2"]) < EPS and abs(s["x1"] - s["x2"]) > EPS]

    def validate(self) -> None:
        factor(self.unit)
        if self.axis not in ("horizontal", "vertical"):
            raise FeatureRequestError(f"axis must be 'horizontal' or 'vertical', got {self.axis!r}")
        check_section_loop(self.profile, "the half-section")
        if lowest_r(swapped(self.profile)) < -EPS or profile_bounds(self.profile)[2] < -EPS:
            raise FeatureRequestError("the half-section reaches below the axis (r < 0): write the radius as a positive number measured from the axis")
        if len(self.axis_edges()) > 1:
            raise FeatureRequestError("more than one profile edge lies on the axis: not measured, needs_human")
        if turned_extent(self.profile)[1] <= EPS:
            raise FeatureRequestError("the half-section has no radius")
        if revolve_volume(swapped(self.profile)) <= 0:
            raise FeatureRequestError("the half-section encloses no volume")

    def expected_delta_mm3(self) -> float:
        return revolve_volume(swapped(self.profile)) * MM3_PER_UNIT3[self.unit]

    def expected(self) -> Expect:
        return Expect(delta_mm3=self.expected_delta_mm3(), regression_delta_mm3=self.regression_delta_mm3, bodies=1)

    def _sketch_profile(self) -> list[dict]:
        """The half-section in the FRONT datum sketch frame (drawing unit): centred along the axis."""
        length, rmax, uc = turned_extent(self.profile)
        out = []
        for s in self.profile:
            t = dict(s)
            for kx, ky in (("x1", "y1"), ("x2", "y2"), ("cx", "cy")):
                if kx in s:
                    u, r = s[kx] - uc, s[ky]
                    t[kx], t[ky] = (u, r) if self.axis == "horizontal" else (r, u)
            if self.axis == "vertical" and s["kind"] == "arc":
                t["dir"] = -s["dir"]                  # swapping x and y is a reflection
            out.append(t)
        return out

    def axis_segment(self) -> tuple[tuple[float, float], tuple[float, float]]:
        length, _, _ = turned_extent(self.profile)
        edges = self.axis_edges()
        if edges:
            _, _, uc = turned_extent(self.profile)
            e = edges[0]
            a, b = sorted((e["x1"] - uc, e["x2"] - uc))
        else:
            a, b = -length / 2, length / 2
        return ((a, 0.0), (b, 0.0)) if self.axis == "horizontal" else ((0.0, a), (0.0, b))

    def graph(self) -> dict:
        self.validate()
        f = factor(self.unit)
        _, rmax, _ = turned_extent(self.profile)
        seg = profile_to_graph(self._sketch_profile(), self.unit, "centre")
        a, b = self.axis_segment()
        axis = {"x1": a[0] * f, "y1": a[1] * f, "x2": b[0] * f, "y2": b[1] * f}
        construction = {"kind": "line", **axis, "construction": True}
        return graph_of([sketch_node("n1", "front", rmax * f, seg + [construction]),
                         {"id": "n2", "type": "revolve", "sketch": "n1", "axis": axis, "angle": 2 * math.pi}])

    def expected_new_faces(self) -> int:
        return len(self.profile) - len(self.axis_edges())

    def bbox_m(self) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        f = factor(self.unit)
        length, rmax, _ = turned_extent(self.profile)
        half = (length / 2, rmax, 0.0) if self.axis == "horizontal" else (rmax, length / 2, 0.0)
        lo = (-half[0] * f, -half[1] * f, 0.0)
        hi = (half[0] * f, half[1] * f, 2 * rmax * f)
        return lo, hi

    def probes(self, faces: list[dict], faces_before: int, faces_after: int) -> tuple[list[Probe], list[str]]:
        f = factor(self.unit)
        length, rmax, uc = turned_extent(self.profile)
        ax_idx = 0 if self.axis == "horizontal" else 1                  # world axis the revolve axis runs along
        perp = ((1, 0.0), (2, rmax * f)) if self.axis == "horizontal" else ((0, 0.0), (2, rmax * f))
        radii, flats = [], []
        for s in self.profile:
            if s["kind"] != "line" or s in self.axis_edges():
                continue
            if abs(s["y1"] - s["y2"]) < EPS and s["y1"] > EPS:                             # constant r: a cylinder wall
                radii.append(s["y1"] * f)
            elif abs(s["x1"] - s["x2"]) < EPS:                                              # constant u: a flat ring or disc
                flats.append(((s["x1"] - uc) * f, math.pi * abs(s["y1"] ** 2 - s["y2"] ** 2) * f * f))
        out: list[Probe] = []
        notes: list[str] = []
        if radii:
            out.append(probe_revolved_walls(faces, sorted(set(round(r, 12) for r in radii)), ax_idx, perp, "turned walls: radius and position of the axis"))
        for at, area in flats:
            out.append(probe_planar_face(faces, ax_idx, at, area, f"flat step face at {at * 1000:.3f} mm along the axis"))
        added = faces_after - faces_before
        out.append(Probe("one new face per profile edge", added == self.expected_new_faces(), f"{added} face(s) added, {self.expected_new_faces()} expected"))
        return out, notes

    def box_profile(self) -> bool:
        """Only lines parallel or perpendicular to the axis (cylinders and flat steps): the exact bounding box can be read from the faces and edges."""
        return all(s["kind"] == "line" and (abs(s["y1"] - s["y2"]) < EPS or abs(s["x1"] - s["x2"]) < EPS) for s in self.profile)

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
        faces, edges = await read_geometry(ctx.sw)
        probes, notes = self.probes(faces, v.before.faces, v.after.faces)
        res.probes += probes
        res.notes += notes
        if self.box_profile():
            lo, hi = self.bbox_m()
            res.probes.append(probe_bbox(faces, edges, lo, hi, "exact bounding box"))
        return res
