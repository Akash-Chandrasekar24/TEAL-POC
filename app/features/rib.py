"""General feature: triangular ribs (gussets) that lean on a round boss and stand on the front face of the plate.

A rib is a right triangle seen in the plane perpendicular to the plate that passes through the boss axis (the TOP datum, offset to the
boss axis). Its foot lies on the plate face at `foot_distance` from the axis, its slope climbs toward the boss and meets the boss wall
(radius R) at `height_at_boss`; the slope goes on into the boss, where the inner end (a flat face) is buried, so the rib is attached by
overlap and leaves no gap. The triangle is extruded `mid_plane` by `thickness` along y. One step per side (left / right along x).

Build the ribs BEFORE any bore through the boss: a rib end buried in the wall would otherwise reach into the hole.

Expected volume added per side (exact, numeric): the integral over x of  height(x) x width(x)  where height(x) is the slope and
width(x) = thickness minus the chord of the boss cylinder at that x (the part of the rib that lies inside the boss adds nothing).
Refused (needs_human, never approximated): ribs along y or at an angle, curved or stepped profiles, ribs on a non-round boss,
a rib that would stick out of the plate, meet the wall above the boss top, or be wider than the boss. A steep slope whose buried
part would rise past the boss top stops flat at the boss top inside the boss.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from app.verify import Expect

from .base import (FeatureContext, FeatureRequestError, FeatureResult, Position, Probe, POS_TOL_M, factor, graph_of, read_geometry,
                   sketch_node)

MM3_PER_UNIT3 = {"mm": 1.0, "inch": 25.4 ** 3}
SIDES = {"left": (-1,), "right": (1,), "both": (-1, 1)}
BURY = 0.9                  # the inner end sits at BURY x (distance of the boss wall at the rib's side face), so its corners stay inside the boss


@dataclass(frozen=True)
class Rib:
    """All numbers in the drawing's unit. `axis` = the boss axis as printed (needs `outline_wh` for edge words). The plate's front face is
    at `plate_thickness` along z."""
    unit: str
    axis: Position
    boss_diameter: float
    boss_height: float                  # height of the boss above the plate face
    thickness: float                    # along y
    foot_distance: float                # from the axis along x to the foot on the plate face
    height_at_boss: float               # height above the face where the slope meets the boss wall
    plate_thickness: float
    sides: str = "both"                 # 'left' | 'right' | 'both'
    foot_fillet: float | None = None    # fillet radius where the sloped face meets the plate (the foot edge), None = sharp
    outline_wh: tuple[float, float] | None = None
    label: str = "rib"

    NAME = "rib"

    # ---------------------------------------------------------------- pure parts (offline-testable)
    @property
    def radius(self) -> float:
        return self.boss_diameter / 2

    def inner_distance(self) -> float:
        return BURY * math.sqrt(self.radius ** 2 - (self.thickness / 2) ** 2)

    def inner_height(self) -> float:
        """Height of the slope at the buried inner end (the slope continues into the boss)."""
        return self.height_at_boss * (self.foot_distance - self.inner_distance()) / (self.foot_distance - self.radius)

    def validate(self) -> None:
        factor(self.unit)
        if self.sides not in SIDES:
            raise FeatureRequestError(f"sides must be 'left', 'right' or 'both', got {self.sides!r}")
        for name in ("boss_diameter", "boss_height", "thickness", "foot_distance", "height_at_boss", "plate_thickness"):
            if not getattr(self, name) or getattr(self, name) <= 0:
                raise FeatureRequestError(f"rib: {name} must be positive")
        if self.thickness >= self.boss_diameter:
            raise FeatureRequestError("rib: thickness must be smaller than the boss diameter (a wider rib would stick out past the boss)")
        if self.foot_distance <= self.radius:
            raise FeatureRequestError("rib: foot_distance must be larger than the boss radius (the foot lies outside the boss)")
        if self.foot_fillet is not None and not (0 < self.foot_fillet < self.thickness):
            raise FeatureRequestError("rib: foot_fillet must be positive and smaller than the rib thickness")
        if self.height_at_boss > self.boss_height:
            raise FeatureRequestError("rib: height_at_boss is higher than the boss")
        if self.outline_wh is not None:
            ax, ay = (v / factor(self.unit) for v in self.axis.to_sketch_m(self.unit, self.outline_wh))
            W, H = self.outline_wh
            for s in SIDES[self.sides]:
                if abs(ax + s * self.foot_distance) > W / 2 + 1e-9:
                    raise FeatureRequestError("rib: the foot lies outside the plate outline")
            if abs(ay) + self.thickness / 2 > H / 2 + 1e-9:
                raise FeatureRequestError("rib: the rib is wider than the plate at the boss axis")
        elif self.axis.x_from != "centre" or self.axis.y_from != "centre":
            raise FeatureRequestError("a boss axis measured from an edge needs the base outline size (outline_wh)")

    def side_volume_mm3(self) -> float:
        """Volume one rib adds (drawing unit^3 -> mm3): the triangle prism minus its overlap with the boss cylinder."""
        R, t, F, d0, H = self.radius, self.thickness, self.foot_distance, self.inner_distance(), self.inner_height()
        h = lambda x: min(H * (F - x) / (F - d0), self.boss_height)    # flat at the boss top inside the boss (graph)
        xw = math.sqrt(R * R - t * t / 4)                       # up to here the rib is completely inside the boss
        n = 20000
        dx = (R - xw) / n
        part = sum(h(xw + (i + 0.5) * dx) * (t - 2 * math.sqrt(max(R * R - (xw + (i + 0.5) * dx) ** 2, 0.0))) for i in range(n)) * dx
        outside = t * H / (F - d0) * (F - R) ** 2 / 2           # integral of h over [R, F] times t (h is linear, zero at F)
        return (part + outside) * MM3_PER_UNIT3[self.unit]

    def expected_delta_mm3(self) -> float:
        return self.side_volume_mm3()

    def axis_world_m(self) -> tuple[float, float]:
        return self.axis.to_sketch_m(self.unit, self.outline_wh)

    def graph(self, side: int) -> dict:
        """Sketch on the TOP datum offset to the boss axis (sketch x = world X, sketch y = world -Z), extrude mid_plane by the thickness."""
        self.validate()
        f = factor(self.unit)
        ax, ay = self.axis_world_m()
        z0, z1 = self.plate_thickness * f, (self.plate_thickness + self.inner_height()) * f
        xi, xf = ax + side * self.inner_distance() * f, ax + side * self.foot_distance * f
        pts = [(xi, -z0), (xf, -z0), (xi, -z1)]
        if self.inner_height() > self.boss_height:
            # a steep slope (meets the wall at or near the boss top): inside the boss the rib stops flat at the boss top, still buried
            zt = (self.plate_thickness + self.boss_height) * f
            xc = ax + side * (self.foot_distance - self.boss_height * (self.foot_distance - self.radius) / self.height_at_boss) * f
            pts = [(xi, -z0), (xf, -z0), (xc, -zt), (xi, -zt)]
        seg = [{"kind": "line", "x1": a[0], "y1": a[1], "x2": b[0], "y2": b[1]} for a, b in zip(pts, pts[1:] + pts[:1])]
        ext = {"id": "n2", "type": "extrude", "sketch": "n1", "operation": "boss", "end": "mid_plane", "depth": self.thickness * f}
        return graph_of([sketch_node("n1", "top", ay, seg), ext])

    def foot_probe(self, edges: list[dict], side: int) -> Probe:
        """The foot is a straight line along y on the plate face from axis y - thickness/2 to axis y + thickness/2. Another feature (a hole
        under the rib) may split it, so the edges lying on that line must together cover the whole foot."""
        f = factor(self.unit)
        ax, ay = self.axis_world_m()
        x, z = ax + side * self.foot_distance * f, self.plate_thickness * f
        lo, hi = ay - self.thickness * f / 2, ay + self.thickness * f / 2
        on_line = [sorted((e["start"][1], e["end"][1])) for e in edges
                   if all(abs(e[k][0] - x) <= POS_TOL_M and abs(e[k][2] - z) <= POS_TOL_M for k in ("start", "end"))
                   and abs(e["start"][1] - e["end"][1]) > POS_TOL_M]
        reach = lo
        for a, b in sorted(on_line):
            if a <= reach + POS_TOL_M:
                reach = max(reach, b)
        ok = reach >= hi - POS_TOL_M
        return Probe(f"rib foot edge ({'right' if side > 0 else 'left'}) at x {x * 1000:.3f} mm", ok,
                     "found" if ok else f"the straight edges on x={x * 1000:.3f}, z={z * 1000:.3f} mm cover y {lo * 1000:.3f} up to {reach * 1000:.3f} mm of {lo * 1000:.3f} .. {hi * 1000:.3f}")

    def footprint_collisions(self, faces: list[dict]) -> list[str]:
        """Round holes / bosses (cylinders along z) other than the boss itself whose circle touches a rib's footprint on the plate face.
        A rib standing over a hole would cap it, and the volume check cannot see that, so it is refused."""
        f = factor(self.unit)
        ax, ay = self.axis_world_m()
        found = []
        for c in faces:
            if c.get("kind") != "cylinder" or abs(abs(c["axis"][2]) - 1) > 1e-6:
                continue
            cx, cy, r = c["origin"][0], c["origin"][1], c["radius"]
            if math.hypot(cx - ax, cy - ay) <= POS_TOL_M:
                continue                                           # the boss (or its bore) itself
            for s in SIDES[self.sides]:
                x0, x1 = sorted((ax + s * self.inner_distance() * f, ax + s * self.foot_distance * f))
                dx, dy = max(x0 - cx, 0.0, cx - x1), max(ay - self.thickness * f / 2 - cy, 0.0, cy - ay - self.thickness * f / 2)
                if math.hypot(dx, dy) < r + POS_TOL_M:
                    found.append(f"a round feature of radius {r * 1000:.2f} mm at ({cx * 1000:.2f}, {cy * 1000:.2f}) mm touches the {'right' if s > 0 else 'left'} rib")
                    break
        return found

    # ---------------------------------------------------------------- run
    async def apply(self, ctx: FeatureContext) -> FeatureResult:
        self.validate()
        res = FeatureResult(self.NAME)
        faces, _ = await read_geometry(ctx.sw)
        clash = self.footprint_collisions(faces)
        if clash:
            raise FeatureRequestError("rib: " + "; ".join(clash) + " (a rib over a hole would cap it): needs_human, change the rib size / position or the hole")
        for side in SIDES[self.sides]:
            graph = self.graph(side)
            name = f"{self.label} ({'right' if side > 0 else 'left'})"

            async def action(g=graph):
                return await ctx.sw.submit_feature_graph(g, fresh_document=False)

            v = await ctx.verifier.run_step(name, action, Expect(delta_mm3=self.expected_delta_mm3(), bodies=1))
            res.verdicts.append(v)
            if not v.ok:
                return res
            res.notes.append(f"{name}: measured volume change {v.measured_delta_mm3:.2f} mm3")
            _, edges = await read_geometry(ctx.sw)
            res.probes.append(self.foot_probe(edges, side))
        if self.foot_fillet is not None:
            res = await self._fillet_feet(ctx, res)
        return res

    async def _fillet_feet(self, ctx: FeatureContext, res: FeatureResult) -> FeatureResult:
        """Fillet the foot edges (straight, as long as the thickness, on the plate face at the feet) with the edge-feature machinery."""
        from .edge_feature import EdgeSelector, FilletChamferEdges
        f = factor(self.unit)
        ax, ay = (v / f for v in self.axis_world_m())
        sides = SIDES[self.sides]
        xs = [ax + s * self.foot_distance for s in sides]
        region = (min(xs) - 0.5, max(xs) + 0.5, ay - self.thickness / 2 - 0.5, ay + self.thickness / 2 + 0.5)
        sel = EdgeSelector("front", self.plate_thickness, region=region, kinds=("line",), length=self.thickness)
        sub = await FilletChamferEdges(self.unit, "fillet", self.foot_fillet, sel, expect_count=len(sides), label=f"{self.label} foot fillet").apply(ctx)
        res.verdicts += sub.verdicts
        res.probes += sub.probes
        res.notes += sub.notes
        return res
