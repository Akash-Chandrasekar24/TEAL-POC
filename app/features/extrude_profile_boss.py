"""Feature 1: extrude a closed outline of lines and arcs as a boss. Checklist path (submit_feature_graph).

Measured in SolidWorks: a boss with `reversed` on an OFFSET plane silently adds nothing, so only blind-forward or mid_plane are allowed there.
Expected volume change: + (outline area) x (depth), area exact for lines and arcs.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from app.geometry import profile_area, profile_bounds
from app.verify import Expect
from .face_sketch import sketch_profile_on_face
from .base import (probe_cylinders, probe_planar_face, probe_segments, Probe, FeatureContext, FeatureRequestError, FeatureResult, PlaneFrame, check_closed, factor, graph_of,
                   probe_bbox, profile_to_graph, read_geometry, sketch_node)

MM3_PER_UNIT3 = {"mm": 1.0, "inch": 25.4 ** 3}


@dataclass(frozen=True)
class ExtrudeProfileBoss:
    """All numbers in the drawing's unit. `profile`: closed lines/arcs, coordinates as printed. `origin`: 'centre' = measured
    from the sketch origin; 'lower_left' = the reader's profile frame (lower-left corner of the outline)."""
    unit: str
    plane: str                          # 'top' | 'front'
    profile: list[dict]
    depth: float                        # blind: distance from the sketch plane; mid_plane: TOTAL thickness
    offset: float = 0.0                 # sketch plane offset along the datum normal (drawing unit)
    mode: str = "blind"                 # 'blind' | 'mid_plane'
    reversed: bool = False              # blind only; on an offset plane only with target='face'
    is_base: bool = True                # the first solid: its own bounding box IS the part. False for a boss on a face (checked by its own walls)
    target: str = "plane"               # 'plane': checklist graph. 'face': sketch ON the planar face at `offset` and grow outward from it
    origin: str = "centre"
    label: str = "boss"
    regression_delta_mm3: float | None = None   # a previously measured volume change for the same step (optional regression check)

    NAME = "extrude_profile_boss"

    def validate(self) -> PlaneFrame:
        frame = PlaneFrame(self.plane)
        factor(self.unit)
        if self.mode not in ("blind", "mid_plane"):
            raise FeatureRequestError(f"mode must be 'blind' or 'mid_plane', got {self.mode!r}")
        if self.depth <= 0:
            raise FeatureRequestError("depth must be positive")
        if self.target not in ("plane", "face"):
            raise FeatureRequestError(f"target must be 'plane' or 'face', got {self.target!r}")
        if self.target == "face" and self.mode != "blind":
            raise FeatureRequestError("a boss sketched on a face is blind (mode='blind')")
        if self.reversed and self.target == "plane" and (self.mode != "blind" or abs(self.offset) > 1e-12):
            raise FeatureRequestError("a reversed boss on an offset plane silently adds nothing: use target='face' (sketch on the face) or offset 0")
        check_closed(self.profile) if self.origin == "centre" else None
        return frame

    # ---- pure parts (offline-testable)
    def graph(self) -> dict:
        self.validate()
        f = factor(self.unit)
        seg = profile_to_graph(self.profile, self.unit, self.origin)
        check_closed(seg)
        ext = {"id": "n2", "type": "extrude", "sketch": "n1", "operation": "boss", "end": self.mode, "depth": self.depth * f}
        if self.reversed:
            ext["reversed"] = True
        return graph_of([sketch_node("n1", self.plane, self.offset * f, seg), ext])

    def expected_delta_mm3(self) -> float:
        return profile_area(self.profile) * self.depth * MM3_PER_UNIT3[self.unit]

    def normal_extent_m(self) -> tuple[float, float]:
        f = factor(self.unit)
        off, d = self.offset * f, self.depth * f
        if self.mode == "mid_plane":
            return off - d / 2, off + d / 2
        return (off - d, off) if self.reversed else (off, off + d)

    def boss_probes(self, faces: list[dict], edges: list[dict], seg: list[dict], frame: PlaneFrame, cap_m: float) -> list[Probe]:
        """A boss on a face: the part is more than the boss, so check the boss's own walls and its free end cap."""
        out: list[Probe] = []
        lines = [s for s in seg if s["kind"] == "line"]
        if lines:
            out.append(probe_segments(edges, lines, frame, "boss side walls: every line of the outline exists as an edge at the printed position"))
        cyl = []
        for s in seg:
            if s["kind"] == "arc":
                a, b = frame.in_plane_world(s["cx"], s["cy"])
                cyl.append((math.hypot(s["x1"] - s["cx"], s["y1"] - s["cy"]), a, b))
            elif s["kind"] == "circle":                                    # a round or ring boss: the outer wall and, for a ring, the inner wall
                a, b = frame.in_plane_world(s["cx"], s["cy"])
                cyl.append((s["diameter"] / 2, a, b))
        if cyl:
            out.append(probe_cylinders(faces, cyl, frame, "boss round walls: radius and in-plane position of every arc / circle"))
        f = factor(self.unit)
        out.append(probe_planar_face(faces, frame.normal_axis, cap_m, profile_area(self.profile) * f * f, f"boss end cap at {cap_m * 1000:.3f} mm"))
        return out

    # ---- run
    async def apply(self, ctx: FeatureContext) -> FeatureResult:
        frame = frame_ = self.validate()
        graph = self.graph()
        res = FeatureResult(self.NAME)

        async def action():
            if self.target == "face":
                seg = profile_to_graph(self.profile, self.unit, self.origin)
                r, idx = await sketch_profile_on_face(ctx, frame_, self.offset * factor(self.unit), -1 if self.reversed else 1, seg)
                res.notes.append(f"sketched on face {idx}")
                return r if not r.ok else await ctx.sw.extrude_feature(feature_type="boss", depth=self.depth * factor(self.unit))
            return await ctx.sw.submit_feature_graph(graph, fresh_document=False)

        v = await ctx.verifier.run_step(self.label, action, Expect(delta_mm3=self.expected_delta_mm3(),
                                                                     regression_delta_mm3=self.regression_delta_mm3))
        res.verdicts.append(v)
        if not v.ok:
            return res
        # position probe: exact world bounding box (arc edges are recovered from the cylinder faces)
        faces, edges = await read_geometry(ctx.sw)
        lo_n, hi_n = self.normal_extent_m()
        seg = profile_to_graph(self.profile, self.unit, self.origin)
        if not self.is_base:
            res.probes += self.boss_probes(faces, edges, seg, frame, lo_n if self.reversed else hi_n)
            return res
        x0, x1, y0, y1 = profile_bounds(seg)
        lo, hi = [0.0] * 3, [0.0] * 3
        a_ax, b_ax = frame.in_plane_axes
        (sx, sy) = frame.signs                      # a sketch axis may run against its world axis (top: world Z = -sketch y)
        lo[a_ax], hi[a_ax] = (x0, x1) if sx > 0 else (-x1, -x0)
        lo[b_ax], hi[b_ax] = (y0, y1) if sy > 0 else (-y1, -y0)
        lo[frame.normal_axis], hi[frame.normal_axis] = lo_n, hi_n
        res.probes.append(probe_bbox(faces, edges, tuple(lo), tuple(hi), "exact bounding box"))
        return res
