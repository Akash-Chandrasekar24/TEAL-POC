"""General feature: cut ANY closed outline (lines and arcs) out of the part, blind / through / mid-plane, from a datum plane.

This is the general version of cut_feature's "plane" path: cut_feature only knows circle, slot and rectangle; this one takes
whatever outline the drawing shows (windows with rounded corners, profile cuts, pockets, notches). It uses the same
checklist route that features 1 and 2 already proved live (submit_feature_graph: one sketch node + one extrude cut node).

Expected volume removed: outline area x material_length (exact, when the caller knows how much material the cut crosses),
otherwise only bounded by area x depth (blind / mid_plane); the MEASURED value is logged either way.
A cut whose sketch plane sits on a downward-facing face is refused (see hazards.py), not guessed.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from app.geometry import profile_area
from app.verify import Expect
from .base import (FeatureContext, FeatureRequestError, FeatureResult, PlaneFrame, Probe, check_closed, factor, graph_of,
                   probe_cylinders, probe_planar_face, probe_segments, profile_to_graph, read_geometry, sketch_node)
from .face_sketch import sketch_profile_on_face
from .hazards import refuse_mirrored_frame

MM3_PER_UNIT3 = {"mm": 1.0, "inch": 25.4 ** 3}


@dataclass(frozen=True)
class ExtrudeProfileCut:
    """All numbers in the drawing's unit. `profile`: closed lines/arcs, coordinates as printed (arcs need dir = 1 / -1).
    `origin`: 'centre' = measured from the sketch origin; 'lower_left' = the reader's profile frame (lower-left corner of the outline's box)."""
    unit: str
    plane: str                              # 'top' | 'front'
    profile: list[dict]
    end: str = "blind"                      # 'blind' | 'through' | 'mid_plane' (mid_plane: depth = TOTAL)
    depth: float | None = None
    offset: float = 0.0                     # sketch plane offset along the datum normal (drawing unit)
    into: str = "positive"                  # direction of the cut along the datum normal: 'positive' | 'negative'
    target: str = "plane"                   # 'plane': checklist graph. 'face': sketch ON the planar face at `offset` (a face that points against the
                                            # datum normal: the compiler would sketch on it with a mirrored frame, so the frame is read back instead)
    origin: str = "centre"
    material_length: float | None = None    # how much material the cut crosses (drawing unit): makes the expected volume exact
    bound_only: bool = False                # a human accepted that the outline reaches past the straight part of a rounded / stepped side: it cuts LESS than its full
                                            # size, so the volume is only checked as an upper bound and the full-size floor probe is skipped
    label: str = "profile cut"
    regression_delta_mm3: float | None = None

    NAME = "extrude_profile_cut"

    # ---------------------------------------------------------------- pure parts (offline-testable)
    def validate(self) -> PlaneFrame:
        frame = PlaneFrame(self.plane)
        factor(self.unit)
        if self.end not in ("blind", "through", "mid_plane"):
            raise FeatureRequestError(f"end must be blind, through or mid_plane, got {self.end!r}")
        if self.end in ("blind", "mid_plane") and (self.depth is None or self.depth <= 0):
            raise FeatureRequestError(f"end={self.end!r} needs a positive depth")
        if self.into not in ("positive", "negative"):
            raise FeatureRequestError("into must be positive or negative")
        if self.target not in ("plane", "face"):
            raise FeatureRequestError(f"target must be 'plane' or 'face', got {self.target!r}")
        if self.target == "face" and self.end == "mid_plane":
            raise FeatureRequestError("a cut sketched on a face cannot be mid_plane")
        if self.material_length is not None and self.material_length <= 0:
            raise FeatureRequestError("material_length must be positive")
        if self.origin not in ("centre", "lower_left"):
            raise FeatureRequestError(f"profile origin must be 'centre' or 'lower_left', got {self.origin!r}")
        if not self.profile:
            raise FeatureRequestError("empty profile")
        check_closed(self.profile)
        if profile_area(self.profile) <= 0:
            raise FeatureRequestError("the outline encloses no area")
        if any(s.get("kind") == "circle" for s in self.profile) and self.end != "blind":
            raise FeatureRequestError("a ring (or circle) outline cut needs an explicit blind depth: through / mid-plane are refused")
        return frame

    def area(self) -> float:
        return profile_area(self.profile)

    def expected(self) -> Expect:
        k = MM3_PER_UNIT3[self.unit]
        if self.material_length is not None and not self.bound_only:
            return Expect(delta_mm3=-self.area() * self.material_length * k, regression_delta_mm3=self.regression_delta_mm3)
        if self.end in ("blind", "mid_plane"):
            return Expect(bound_mm3=-self.area() * self.depth * k, regression_delta_mm3=self.regression_delta_mm3)
        return Expect(regression_delta_mm3=self.regression_delta_mm3)      # through, length unknown: only "must change"

    def graph(self) -> dict:
        self.validate()
        f = factor(self.unit)
        seg = profile_to_graph(self.profile, self.unit, self.origin)
        check_closed(seg)
        cut = {"id": "n2", "type": "extrude", "sketch": "n1", "operation": "cut",
               "end": {"blind": "blind", "through": "through_all", "mid_plane": "mid_plane"}[self.end]}
        if self.end != "through":
            cut["depth"] = self.depth * f
        if self.into == "negative":
            cut["reversed"] = True
        return graph_of([sketch_node("n1", self.plane, self.offset * f, seg), cut])

    def floor_m(self) -> float | None:
        """Height of the cut's floor along the datum normal (blind cuts only)."""
        if self.end != "blind":
            return None
        sign = 1 if self.into == "positive" else -1
        return (self.offset + sign * self.depth) * factor(self.unit)

    def probes(self, faces: list[dict], edges: list[dict], frame: PlaneFrame) -> list[Probe]:
        seg = profile_to_graph(self.profile, self.unit, self.origin)
        cyl = []
        for s in seg:
            if s["kind"] == "arc":
                a, b = frame.in_plane_world(s["cx"], s["cy"])
                cyl.append((math.hypot(s["x1"] - s["cx"], s["y1"] - s["cy"]), a, b))
            elif s["kind"] == "circle":                                    # a ring cut: the outer and the inner wall
                a, b = frame.in_plane_world(s["cx"], s["cy"])
                cyl.append((s["diameter"] / 2, a, b))
        lines = [s for s in seg if s["kind"] == "line"]
        out: list[Probe] = []
        if cyl:
            out.append(probe_cylinders(faces, cyl, frame, "round walls: radius and in-plane position of every arc / circle"))
        if lines:
            out.append(probe_segments(edges, lines, frame, "straight walls: every line of the outline exists as an edge at the printed position"))
        floor = self.floor_m()
        f = factor(self.unit)
        # the floor face has exactly the outline's area only when the cut sits fully inside material
        if floor is not None and not self.bound_only and self.material_length is not None and math.isclose(self.material_length, self.depth, rel_tol=1e-9):
            out.append(probe_planar_face(faces, frame.normal_axis, floor, self.area() * f * f, f"floor at {floor * 1000:.3f} mm"))
        return out

    # ---------------------------------------------------------------- run
    async def apply(self, ctx: FeatureContext) -> FeatureResult:
        frame = self.validate()
        graph = self.graph()
        f = factor(self.unit)
        if abs(self.offset) > 1e-12 and self.target == "plane":
            faces, _ = await read_geometry(ctx.sw)
            refuse_mirrored_frame(faces, frame, self.offset * f, self.label)
        res = FeatureResult(self.NAME)

        async def action():
            if self.target == "face":
                seg = profile_to_graph(self.profile, self.unit, self.origin)
                r, idx = await sketch_profile_on_face(ctx, frame, self.offset * f, 1 if self.into == "negative" else -1, seg)
                res.notes.append(f"sketched on face {idx}")
                if not r.ok:
                    return r
                return await ctx.sw.extrude_feature(feature_type="cut", through=True) if self.end == "through" else \
                    await ctx.sw.extrude_feature(feature_type="cut", depth=self.depth * f)
            return await ctx.sw.submit_feature_graph(graph, fresh_document=False)

        v = await ctx.verifier.run_step(self.label, action, self.expected())
        res.verdicts.append(v)
        if not v.ok:
            return res
        res.notes.append(f"measured volume change {v.measured_delta_mm3:.2f} mm3"
                         + (f" (upper bound {-v.bound_mm3:.2f})" if v.bound_mm3 is not None else ""))
        faces, edges = await read_geometry(ctx.sw)
        res.probes += self.probes(faces, edges, frame)
        return res
