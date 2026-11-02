"""General feature: one or more round bosses (cylinders, pins, hubs, lugs' round ends) pushed out from a datum plane.

Feature 1 takes an outline of lines and arcs; this one takes plain circles, which is what most round bosses on a drawing
are. Same checklist route as features 1 and 3 (submit_feature_graph: one sketch node of circles + one extrude boss node).

Expected volume added: sum(circle areas) x new_material_length, where new_material_length defaults to the full depth
(the boss starts on a free face). Pass a smaller value when part of the boss runs inside existing material.
The step also expects ONE body afterwards: a boss that only floats near the part would silently become a second body.
A boss whose sketch plane sits on a downward-facing face is refused (see hazards.py).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from app.verify import Expect
from .base import (FeatureContext, FeatureRequestError, FeatureResult, PlaneFrame, Position, Probe, circle_node, factor,
                   graph_of, probe_cylinders, probe_planar_face, read_geometry, sketch_node)
from .face_sketch import sketch_profile_on_face
from .hazards import refuse_mirrored_frame

MM3_PER_UNIT3 = {"mm": 1.0, "inch": 25.4 ** 3}


@dataclass(frozen=True)
class BossCircle:
    diameter: float
    position: Position                  # the circle's CENTRE as printed

    def validate(self) -> None:
        if not self.diameter or self.diameter <= 0:
            raise FeatureRequestError("a boss circle needs a positive diameter")

    def area(self) -> float:
        self.validate()
        return math.pi * self.diameter ** 2 / 4


@dataclass(frozen=True)
class ExtrudeCircleBoss:
    """All numbers in the drawing's unit. `plane` is the view the positions are printed in (datum frame)."""
    unit: str
    plane: str                          # 'top' | 'front'
    circles: list[BossCircle]
    depth: float                        # blind: distance from the sketch plane; mid_plane: TOTAL height
    offset: float = 0.0                 # sketch plane offset along the datum normal (drawing unit)
    mode: str = "blind"                 # 'blind' | 'mid_plane'
    reversed: bool = False              # blind only; on an offset plane only with target='face' (see below)
    target: str = "plane"               # 'plane': checklist graph. 'face': sketch ON the planar face at `offset` and grow outward from it
                                        # (needed for a face that points against the datum normal: bottom / left faces, a reversed boss)
    new_material_length: float | None = None    # how much of the depth is NEW material (default: all of it)
    expect_bodies: int | None = 1       # None to switch the single-body check off
    outline_wh: tuple[float, float] | None = None   # only for positions printed from an edge
    label: str = "circle boss"
    regression_delta_mm3: float | None = None

    NAME = "extrude_circle_boss"

    # ---------------------------------------------------------------- pure parts (offline-testable)
    def validate(self) -> PlaneFrame:
        frame = PlaneFrame(self.plane)
        f = factor(self.unit)
        if not self.circles:
            raise FeatureRequestError("no circles")
        for c in self.circles:
            c.validate()
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
        if self.new_material_length is not None and not (0 < self.new_material_length <= self.depth):
            raise FeatureRequestError("new_material_length must be positive and at most the depth")
        centres = [c.position.to_sketch_m(self.unit, self.outline_wh) for c in self.circles]
        for i in range(len(self.circles)):
            for j in range(i + 1, len(self.circles)):
                gap = math.hypot(centres[i][0] - centres[j][0], centres[i][1] - centres[j][1])
                if gap < (self.circles[i].diameter + self.circles[j].diameter) / 2 * f:
                    raise FeatureRequestError(f"circles {i} and {j} overlap: their volumes would be counted twice")
        return frame

    def total_area(self) -> float:
        return sum(c.area() for c in self.circles)

    def expected_delta_mm3(self) -> float:
        length = self.new_material_length if self.new_material_length is not None else self.depth
        return self.total_area() * length * MM3_PER_UNIT3[self.unit]

    def expected(self) -> Expect:
        return Expect(delta_mm3=self.expected_delta_mm3(), regression_delta_mm3=self.regression_delta_mm3, bodies=self.expect_bodies)

    def profile_m(self) -> list[dict]:
        prof = []
        for c in self.circles:
            cx, cy = c.position.to_sketch_m(self.unit, self.outline_wh)
            prof.append(circle_node(c.diameter, cx, cy, self.unit))
        return prof

    def graph(self) -> dict:
        self.validate()
        f = factor(self.unit)
        prof = self.profile_m()
        ext = {"id": "n2", "type": "extrude", "sketch": "n1", "operation": "boss", "end": self.mode, "depth": self.depth * f}
        if self.reversed:
            ext["reversed"] = True
        return graph_of([sketch_node("n1", self.plane, self.offset * f, prof), ext])

    def end_heights_m(self) -> list[float]:
        """Heights (along the datum normal) of the boss's free end cap(s)."""
        f = factor(self.unit)
        off, d = self.offset * f, self.depth * f
        if self.mode == "mid_plane":
            return [off - d / 2, off + d / 2]
        return [off - d] if self.reversed else [off + d]

    def probes(self, faces: list[dict], frame: PlaneFrame) -> list[Probe]:
        f = factor(self.unit)
        cyl = []
        for c in self.circles:
            cx, cy = c.position.to_sketch_m(self.unit, self.outline_wh)
            a, b = frame.in_plane_world(cx, cy)
            cyl.append((c.diameter * f / 2, a, b))
        out = [probe_cylinders(faces, cyl, frame, "round walls: radius and in-plane position of every boss")]
        # a free end cap has exactly the circle's area only when the whole depth is new material
        if self.new_material_length is None or math.isclose(self.new_material_length, self.depth, rel_tol=1e-9):
            for h in self.end_heights_m():
                for i, c in enumerate(self.circles):
                    out.append(probe_planar_face(faces, frame.normal_axis, h, c.area() * f * f, f"end cap of boss {i} at {h * 1000:.3f} mm"))
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
                r, idx = await sketch_profile_on_face(ctx, frame, self.offset * f, -1 if self.reversed else 1, self.profile_m())
                res.notes.append(f"sketched on face {idx}")
                return r if not r.ok else await ctx.sw.extrude_feature(feature_type="boss", depth=self.depth * f)
            return await ctx.sw.submit_feature_graph(graph, fresh_document=False)

        v = await ctx.verifier.run_step(self.label, action, self.expected())
        res.verdicts.append(v)
        if not v.ok:
            return res
        res.notes.append(f"measured volume change {v.measured_delta_mm3:.2f} mm3")
        faces, _ = await read_geometry(ctx.sw)
        res.probes += self.probes(faces, frame)
        return res
