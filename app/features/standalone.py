"""Standalone parts: one feature makes the whole part (the only item of its build list).

  swept_tube        a round bar or tube (outer / optional inner diameter) swept along a path of straight runs joined by bends of a given
                    centreline radius, drawn in the front view (handles, bent rods, pipe spools). Expected volume: section area x centreline
                    length (Pappus; exact while every bend radius exceeds the outer radius and the tube does not touch itself, both checked).
  loft              a solid blending one section (circle or rectangle, optional corner radius) on the back plane into another on a parallel
                    plane `height` in front, optionally shifted (reducers, transitions, tapered posts). Expected volume: exact prismatoid for
                    circle -> circle and sharp rectangle -> rectangle (straight-line blend); a checked range otherwise.
  sheet_metal_base  a flat sheet-metal blank (rectangle, thickness, bend radius) with optional edge flanges on its four edges (brackets, covers,
                    trays). Expected volume: blank + flanges as a range, because SolidWorks measures a flange length by its own convention
                    and trims the blank at the bend; the range still catches a missing flange or a wrong size.

Frames: everything is drawn in the FRONT view (world X right, Y up, Z toward the viewer). Sketches go on the datum planes and their offsets.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.verify import Expect
from .base import FeatureContext, FeatureRequestError, FeatureResult, factor, graph_of, sketch_node
from .turned_ops import MM3_PER_UNIT3

_EPS = 1e-9


def _unit(v):
    n = math.hypot(*v)
    if n < _EPS:
        raise FeatureRequestError("two path points coincide")
    return (v[0] / n, v[1] / n)


def _scaled(prims: list[dict], f: float) -> list[dict]:
    return [{k: (v if k in ("kind", "dir", "construction") else v * f) for k, v in s.items()} for s in prims]


# ------------------------------------------------------------------ swept tube
def tube_path(points: list[tuple[float, float]], radii: list[float]) -> tuple[list[dict], float]:
    """(path segments, centreline length): straight runs joined by tangent arcs of the given radius at every corner."""
    if len(points) < 2:
        raise FeatureRequestError("a path needs at least two points")
    n_corner = len(points) - 2
    if len(radii) == 1 and n_corner > 1:
        radii = radii * n_corner
    if n_corner and len(radii) != n_corner:
        raise FeatureRequestError(f"give one bend radius, or one per corner ({n_corner})")
    dirs = [_unit((b[0] - a[0], b[1] - a[1])) for a, b in zip(points, points[1:])]
    lengths = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:])]
    trims, bends = [0.0] * (len(points) + 1), []
    for i in range(1, len(points) - 1):
        d1, d2, R = dirs[i - 1], dirs[i], radii[i - 1]
        cross = d1[0] * d2[1] - d1[1] * d2[0]
        theta = math.acos(max(-1.0, min(1.0, d1[0] * d2[0] + d1[1] * d2[1])))
        if theta < 1e-9:
            bends.append(None)
            continue
        if theta > math.radians(179):
            raise FeatureRequestError(f"the path turns back on itself at point {i + 1}")
        T = R * math.tan(theta / 2)
        trims[i] = T
        bends.append((i, R, theta, 1 if cross > 0 else -1, T))
    for k, L in enumerate(lengths):
        if trims[k] + trims[k + 1] > L + 1e-9:
            raise FeatureRequestError(f"the bends at both ends of run {k + 1} need {trims[k] + trims[k + 1]:g} but the run is only {L:g} long: smaller bend radii")
    segs, total = [], 0.0
    start = points[0]
    for k, L in enumerate(lengths):
        d = dirs[k]
        end = (points[k + 1][0] - d[0] * trims[k + 1], points[k + 1][1] - d[1] * trims[k + 1])
        if math.hypot(end[0] - start[0], end[1] - start[1]) > 1e-9:
            segs.append({"kind": "line", "x1": start[0], "y1": start[1], "x2": end[0], "y2": end[1]})
            total += math.hypot(end[0] - start[0], end[1] - start[1])
        if k + 1 < len(points) - 1 and bends[k] is not None:
            i, R, theta, s, T = bends[k]
            d2 = dirs[k + 1]
            a = end
            b = (points[i][0] + d2[0] * T, points[i][1] + d2[1] * T)
            c = (a[0] - s * d[1] * R, a[1] + s * d[0] * R)                      # centre: to the left of the run for a left turn
            segs.append({"kind": "arc", "cx": c[0], "cy": c[1], "x1": a[0], "y1": a[1], "x2": b[0], "y2": b[1], "dir": s})
            total += R * theta
            start = b
        else:
            start = end
    return segs, total


def _path_samples(segs: list[dict], step: float) -> list[tuple[float, float, float]]:
    """(x, y, arc length) samples along the path."""
    out, s0 = [], 0.0
    for g in segs:
        if g["kind"] == "line":
            L = math.hypot(g["x2"] - g["x1"], g["y2"] - g["y1"])
            n = max(1, int(L / step))
            out += [(g["x1"] + (g["x2"] - g["x1"]) * k / n, g["y1"] + (g["y2"] - g["y1"]) * k / n, s0 + L * k / n) for k in range(n)]
        else:
            R = math.hypot(g["x1"] - g["cx"], g["y1"] - g["cy"])
            a1 = math.atan2(g["y1"] - g["cy"], g["x1"] - g["cx"])
            a2 = math.atan2(g["y2"] - g["cy"], g["x2"] - g["cx"])
            sw = ((a2 - a1) % (2 * math.pi)) if g["dir"] > 0 else -((a1 - a2) % (2 * math.pi))
            L = R * abs(sw)
            n = max(2, int(L / step))
            out += [(g["cx"] + R * math.cos(a1 + sw * k / n), g["cy"] + R * math.sin(a1 + sw * k / n), s0 + L * k / n) for k in range(n)]
        s0 += L
    last = segs[-1]
    out.append((last["x2"], last["y2"], s0))
    return out


@dataclass(frozen=True)
class SweptTube:
    unit: str
    points: tuple
    bend_radii: tuple
    outer_diameter: float
    inner_diameter: float | None = None
    label: str = "swept tube"

    NAME = "swept_tube"

    def path(self):
        return tube_path([tuple(p) for p in self.points], list(self.bend_radii))

    def validate(self) -> None:
        factor(self.unit)
        ro = self.outer_diameter / 2
        if not ro > 0 or (self.inner_diameter is not None and not 0 < self.inner_diameter < self.outer_diameter):
            raise FeatureRequestError("a tube needs 0 < inner diameter < outer diameter (omit the inner diameter for a solid bar)")
        d0 = _unit((self.points[1][0] - self.points[0][0], self.points[1][1] - self.points[0][1]))
        if not (abs(d0[0]) > 1 - 1e-9 or abs(d0[1]) > 1 - 1e-9):
            raise FeatureRequestError("the first run must be horizontal or vertical in the front view (the section is drawn on a datum plane square to it)")
        if any(r <= ro for r in self.bend_radii if len(self.points) > 2):
            raise FeatureRequestError(f"every bend radius (centreline) must be larger than the outer radius {ro:g}, or the tube folds into itself")
        segs, length = self.path()
        reach = math.pi * max([ro, *self.bend_radii])
        pts = _path_samples(segs, max(ro / 4, length / 2000))
        for i in range(len(pts)):                                                 # the tube must not touch itself
            for j in range(i + 1, len(pts)):
                if pts[j][2] - pts[i][2] > reach and math.hypot(pts[j][0] - pts[i][0], pts[j][1] - pts[i][1]) < 2 * ro - 1e-6:
                    raise FeatureRequestError("the tube runs into itself: spread the path or reduce the diameter")

    def section_area(self) -> float:
        a = math.pi / 4 * self.outer_diameter ** 2
        return a - (math.pi / 4 * self.inner_diameter ** 2 if self.inner_diameter else 0.0)

    def graph(self) -> dict:
        self.validate()
        f = factor(self.unit)
        segs, _ = self.path()
        x0, y0 = self.points[0]
        d0 = _unit((self.points[1][0] - x0, self.points[1][1] - y0))
        datum, off, centre = ("right", x0, (0.0, y0)) if abs(d0[0]) > 0.5 else ("top", y0, (x0, 0.0))   # right: sketch (-Z, Y); top: (X, -Z)
        rings = [{"kind": "circle", "diameter": self.outer_diameter, "cx": centre[0], "cy": centre[1]}]
        if self.inner_diameter:
            rings.append({"kind": "circle", "diameter": self.inner_diameter, "cx": centre[0], "cy": centre[1]})
        return graph_of([sketch_node("n1", "front", 0.0, _scaled(segs, f)), sketch_node("n2", datum, off * f, _scaled(rings, f)),
                         {"id": "n3", "type": "sweep", "sketch": "n2", "path": "n1"}])

    def expected_delta_mm3(self) -> float:
        return self.section_area() * self.path()[1] * MM3_PER_UNIT3[self.unit]

    def expected(self) -> Expect:
        return Expect(delta_mm3=self.expected_delta_mm3(), bodies=1)

    async def apply(self, ctx: FeatureContext) -> FeatureResult:
        graph = self.graph()
        res = FeatureResult(self.NAME)
        res.verdicts.append(await ctx.verifier.run_step(self.label, lambda: ctx.sw.submit_feature_graph(graph, fresh_document=False), self.expected()))
        return res


# ------------------------------------------------------------------ loft
@dataclass(frozen=True)
class Section:
    shape: str                 # 'circle' | 'rectangle'
    diameter: float = 0.0
    width: float = 0.0
    height: float = 0.0
    corner_radius: float = 0.0
    cx: float = 0.0
    cy: float = 0.0

    def area(self) -> float:
        if self.shape == "circle":
            return math.pi / 4 * self.diameter ** 2
        return self.width * self.height - (4 - math.pi) * self.corner_radius ** 2

    def prims(self) -> list[dict]:
        if self.shape == "circle":
            return [{"kind": "circle", "diameter": self.diameter, "cx": self.cx, "cy": self.cy}]
        w, h, r, cx, cy = self.width / 2, self.height / 2, self.corner_radius, self.cx, self.cy
        if r <= 0:
            pts = [(cx - w, cy - h), (cx + w, cy - h), (cx + w, cy + h), (cx - w, cy + h)]
            return [{"kind": "line", "x1": a[0], "y1": a[1], "x2": b[0], "y2": b[1]} for a, b in zip(pts, pts[1:] + pts[:1])]
        out = []
        corners = [(cx + w - r, cy - h + r, -90), (cx + w - r, cy + h - r, 0), (cx - w + r, cy + h - r, 90), (cx - w + r, cy - h + r, 180)]
        for k, (ox, oy, a0) in enumerate(corners):
            a, b = math.radians(a0), math.radians(a0 + 90)
            out.append({"kind": "arc", "cx": ox, "cy": oy, "x1": ox + r * math.cos(a), "y1": oy + r * math.sin(a),
                        "x2": ox + r * math.cos(b), "y2": oy + r * math.sin(b), "dir": 1})
            nx, ny, na = corners[(k + 1) % 4]
            out.append({"kind": "line", "x1": ox + r * math.cos(b), "y1": oy + r * math.sin(b),
                        "x2": nx + r * math.cos(math.radians(na)), "y2": ny + r * math.sin(math.radians(na))})
        return out

    def check(self, what: str) -> None:
        if self.shape == "circle":
            if not self.diameter > 0:
                raise FeatureRequestError(f"the {what} circle needs a positive diameter")
        elif not (self.width > 0 and self.height > 0 and 0 <= self.corner_radius <= min(self.width, self.height) / 2):
            raise FeatureRequestError(f"the {what} rectangle needs a positive size and a corner radius up to half its smaller side")


@dataclass(frozen=True)
class Loft:
    unit: str
    bottom: Section
    top: Section
    height: float
    label: str = "loft"

    NAME = "loft"

    def validate(self) -> None:
        factor(self.unit)
        if not self.height > 0:
            raise FeatureRequestError("a loft needs a positive height between its two sections")
        self.bottom.check("bottom")
        self.top.check("top")

    def exact(self) -> float | None:
        """The prismatoid volume h/6 (A0 + 4 Am + A1) where the straight-line blend has a known middle section; None otherwise."""
        b, t, h = self.bottom, self.top, self.height
        if b.shape == t.shape == "circle":
            mid = math.pi / 4 * ((b.diameter + t.diameter) / 2) ** 2
        elif b.shape == t.shape == "rectangle" and b.corner_radius == t.corner_radius == 0:
            mid = (b.width + t.width) / 2 * (b.height + t.height) / 2
        else:
            return None
        return h / 6 * (b.area() + 4 * mid + t.area())

    def graph(self) -> dict:
        self.validate()
        f = factor(self.unit)
        return graph_of([sketch_node("n1", "front", 0.0, _scaled(self.bottom.prims(), f)),
                         sketch_node("n2", "front", self.height * f, _scaled(self.top.prims(), f)),
                         {"id": "n3", "type": "loft", "profiles": ["n1", "n2"]}])

    def expected(self) -> Expect:
        k = MM3_PER_UNIT3[self.unit]
        v = self.exact()
        if v is not None:
            return Expect(delta_mm3=v * k, bodies=1)
        lo, hi = sorted((self.bottom.area(), self.top.area()))
        return Expect(range_mm3=(0.85 * lo * self.height * k, 1.05 * hi * self.height * k), bodies=1,
                      expect_doc="mixed sections: the blend is SolidWorks' own, so the volume is checked as a range")

    def expected_delta_mm3(self) -> float:
        v = self.exact()
        return (v if v is not None else (self.bottom.area() + self.top.area()) / 2 * self.height) * MM3_PER_UNIT3[self.unit]

    async def apply(self, ctx: FeatureContext) -> FeatureResult:
        graph = self.graph()
        res = FeatureResult(self.NAME)
        res.verdicts.append(await ctx.verifier.run_step(self.label, lambda: ctx.sw.submit_feature_graph(graph, fresh_document=False), self.expected()))
        return res


# ------------------------------------------------------------------ sheet metal
EDGES = ("top", "bottom", "left", "right")


@dataclass(frozen=True)
class SheetMetalBase:
    unit: str
    width: float
    height: float
    thickness: float
    bend_radius: float
    flanges: tuple = field(default_factory=tuple)      # ((edge, length, angle_deg), ...)
    k_factor: float | None = None
    label: str = "sheet metal base"

    NAME = "sheet_metal_base"

    def validate(self) -> None:
        factor(self.unit)
        if not (self.width > 0 and self.height > 0 and self.thickness > 0 and self.bend_radius > 0):
            raise FeatureRequestError("a sheet-metal blank needs a positive width, height, thickness and bend radius")
        if self.k_factor is not None and not 0 < self.k_factor < 1:
            raise FeatureRequestError("the K-factor must be between 0 and 1")
        seen = set()
        for edge, length, angle in self.flanges:
            if edge not in EDGES or edge in seen:
                raise FeatureRequestError(f"flanges go on the blank's edges {EDGES}, at most one per edge (got {edge!r})")
            seen.add(edge)
            if not length > self.bend_radius + self.thickness:
                raise FeatureRequestError(f"the {edge} flange ({length:g}) must be longer than the bend radius plus the thickness")
            if not 0 < angle <= 135:
                raise FeatureRequestError("a flange angle must be between 0 and 135 degrees")

    def _edge(self, edge: str) -> tuple[tuple[float, float, float], float]:
        """(a point at the middle of that edge of the blank's sketch-plane face, the edge length), drawing unit."""
        w, h = self.width / 2, self.height / 2
        return {"top": ((0.0, h, 0.0), self.width), "bottom": ((0.0, -h, 0.0), self.width),
                "left": ((-w, 0.0, 0.0), self.height), "right": ((w, 0.0, 0.0), self.height)}[edge]

    def graph(self) -> dict:
        self.validate()
        f = factor(self.unit)
        blank = Section("rectangle", width=self.width, height=self.height).prims()
        base = {"id": "n2", "type": "sheet_metal", "sketch": "n1", "thickness": self.thickness * f, "bend_radius": self.bend_radius * f}
        if self.k_factor is not None:
            base["k_factor"] = self.k_factor
        nodes = [sketch_node("n1", "front", 0.0, _scaled(blank, f)), base]
        for k, (edge, length, angle) in enumerate(self.flanges):
            near, _ = self._edge(edge)
            nodes.append({"id": f"n{3 + k}", "type": "edge_flange", "edge": {"near": [v * f for v in near]}, "length": length * f,
                          "angle": math.radians(angle)})
        return graph_of(nodes)

    def volume_range(self) -> tuple[float, float]:
        t, R = self.thickness, self.bend_radius
        lo = hi = self.width * self.height * t
        for edge, length, angle in self.flanges:
            _, w = self._edge(edge)
            bend = math.radians(angle) / 2 * ((R + t) ** 2 - R * R) * w
            lo += w * t * (length - 2 * (R + t))                                  # the blank trimmed at the bend and the length measured outside
            hi += w * t * length + bend
        return lo, hi

    def expected(self) -> Expect:
        k = MM3_PER_UNIT3[self.unit]
        lo, hi = self.volume_range()
        if not self.flanges:
            return Expect(delta_mm3=lo * k, bodies=1)
        return Expect(range_mm3=(lo * k, hi * k), bodies=1,
                      expect_doc="flange length and bend trimming follow SolidWorks' own convention: checked as a range")

    def expected_delta_mm3(self) -> float:
        lo, hi = self.volume_range()
        return (lo + hi) / 2 * MM3_PER_UNIT3[self.unit]

    async def apply(self, ctx: FeatureContext) -> FeatureResult:
        graph = self.graph()
        res = FeatureResult(self.NAME)
        res.verdicts.append(await ctx.verifier.run_step(self.label, lambda: ctx.sw.submit_feature_graph(graph, fresh_document=False), self.expected()))
        return res
