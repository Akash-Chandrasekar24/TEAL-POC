"""Features AFTER a turned base (revolve_base): round bosses / cuts along the shaft axis (an eccentric lobe, an end-face bore) and cross holes.

Frame (set by revolve_base): the turned part is centred on the datum origin; its axis runs along world X (axis 'horizontal') or world Y ('vertical')
at the other in-plane coordinate 0 and at z = R_max. Stations along the axis are written like the half-section: `u` from the end of the part where
the half-section starts (u = 0), in the drawing's unit.

  turned_axial  a circle (diameter, optionally offset from the axis) on the datum plane perpendicular to the axis at the smaller station,
                extruded forward to the larger station: boss (adds material) or cut (removes it). Always extruded forward: a reversed boss on an
                offset plane silently adds nothing (measured), so the plane is placed at the start of the span instead.
  cross_hole    a through hole perpendicular to the axis, through the axis, at station `at_u`: into the view (toward the viewer) or in the view.

Expected volume changes are integrated from the half-section (material intervals at each station, exact circle-circle / circle-strip areas,
Gauss quadrature split at every profile vertex), so a wrong position or size is caught by the volume check, not only by the probes.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from app.geometry import profile_bounds
from app.verify import Expect
from .base import FeatureContext, FeatureRequestError, FeatureResult, PlaneFrame, factor, graph_of, probe_cylinders, read_geometry, sketch_node
from .revolve_base import turned_extent

MM3_PER_UNIT3 = {"mm": 1.0, "inch": 25.4 ** 3}
_GX, _GW = np.polynomial.legendre.leggauss(8)


# ------------------------------------------------------------------ the turned part as material intervals
def section_intervals(profile_ur: list[dict], u: float) -> list[tuple[float, float]]:
    """Material radius intervals [(r_lo, r_hi), ...] of the turned part at station u (even-odd crossings of the half-section)."""
    rs = []
    for s in profile_ur:
        if s["kind"] == "line":
            x1, y1, x2, y2 = s["x1"], s["y1"], s["x2"], s["y2"]
            if min(x1, x2) < u < max(x1, x2):
                rs.append(y1 + (y2 - y1) * (u - x1) / (x2 - x1))
        elif s["kind"] == "arc":
            cx, cy = s["cx"], s["cy"]
            rho = math.hypot(s["x1"] - cx, s["y1"] - cy)
            if abs(u - cx) >= rho:
                continue
            a1 = math.atan2(s["y1"] - cy, s["x1"] - cx)
            a2 = math.atan2(s["y2"] - cy, s["x2"] - cx)
            sweep = ((a2 - a1) % (2 * math.pi) or 2 * math.pi) if s.get("dir", 1) >= 0 else -((a1 - a2) % (2 * math.pi) or 2 * math.pi)
            h = math.sqrt(rho * rho - (u - cx) ** 2)
            for r in (cy + h, cy - h):
                ang = math.atan2(r - cy, u - cx)
                rel = (ang - a1) % (2 * math.pi) if sweep > 0 else (a1 - ang) % (2 * math.pi)
                if rel < abs(sweep):
                    rs.append(r)
    rs.sort()
    return [(max(0.0, rs[i]), rs[i + 1]) for i in range(0, len(rs) - 1, 2)]


def lens(a: float, b: float, d: float) -> float:
    """Area shared by circles of radius a and b whose centres are d apart."""
    if a <= 0 or b <= 0 or d >= a + b:
        return 0.0
    if d <= abs(a - b):
        return math.pi * min(a, b) ** 2
    ca = max(-1.0, min(1.0, (d * d + a * a - b * b) / (2 * d * a)))
    cb = max(-1.0, min(1.0, (d * d + b * b - a * a) / (2 * d * b)))
    k = max(0.0, (-d + a + b) * (d + a - b) * (d - a + b) * (d + a + b))
    return a * a * math.acos(ca) + b * b * math.acos(cb) - 0.5 * math.sqrt(k)


def _integrate(fn, lo: float, hi: float, breaks: list[float], pieces: int = 48) -> float:
    cuts = sorted({lo, hi, *[b for b in breaks if lo < b < hi]})
    total = 0.0
    for a, b in zip(cuts, cuts[1:]):
        for k in range(pieces):
            x0 = a + (b - a) * k / pieces
            x1 = a + (b - a) * (k + 1) / pieces
            mid, half = (x0 + x1) / 2, (x1 - x0) / 2
            total += half * sum(w * fn(mid + half * x) for x, w in zip(_GX, _GW))
    return total


def _breaks(profile_ur: list[dict]) -> list[float]:
    out = []
    for s in profile_ur:
        out += [s["x1"], s["x2"]]
        if s["kind"] == "arc":
            rho = math.hypot(s["x1"] - s["cx"], s["y1"] - s["cy"])
            out += [s["cx"] - rho, s["cx"], s["cx"] + rho]
    return out


def axial_overlap(profile_ur: list[dict], radius: float, offset: float, u_lo: float, u_hi: float) -> float:
    """Volume of the turned material inside the cylinder of `radius` whose axis is parallel to the part axis at distance `offset`, between u_lo and u_hi."""
    def area(u):
        return sum(lens(radius, hi, offset) - lens(radius, lo, offset) for lo, hi in section_intervals(profile_ur, u))
    return _integrate(area, u_lo, u_hi, _breaks(profile_ur))


def _strip(R: float, w: float) -> float:
    """Area of a disc of radius R inside the strip |s| <= w (the cross-section a cross hole of half-width w removes from a full disc)."""
    m = min(w, R)
    if m <= 0:
        return 0.0
    return 2 * (m * math.sqrt(max(0.0, R * R - m * m)) + R * R * math.asin(m / R))


def cross_overlap(profile_ur: list[dict], radius: float, at_u: float) -> float:
    """Volume of turned material inside a through cylinder of `radius` crossing the axis perpendicularly at station at_u."""
    def area(u):
        w = math.sqrt(max(0.0, radius * radius - (u - at_u) ** 2))
        return sum(_strip(hi, w) - _strip(lo, w) for lo, hi in section_intervals(profile_ur, u))
    return _integrate(area, at_u - radius, at_u + radius, _breaks(profile_ur) + [at_u])


# ------------------------------------------------------------------ features
@dataclass(frozen=True)
class TurnedAxial:
    unit: str
    axis: str                     # 'horizontal' | 'vertical'
    profile: list[dict]           # the base's half-section (u, r)
    operation: str                # 'boss' | 'cut'
    diameter: float
    u_from: float
    u_to: float
    offset_in_view: float = 0.0   # + = up (horizontal axis) / + = right (vertical axis)
    offset_toward_viewer: float = 0.0
    label: str = "turned axial"

    NAME = "turned_axial"

    def _span(self):
        return min(self.u_from, self.u_to), max(self.u_from, self.u_to)

    def validate(self) -> None:
        factor(self.unit)
        if self.operation not in ("boss", "cut"):
            raise FeatureRequestError(f"operation must be 'boss' or 'cut', got {self.operation!r}")
        lo, hi = self._span()
        if not self.diameter > 0 or hi - lo <= 0:
            raise FeatureRequestError("a turned_axial item needs a positive diameter and two different stations")
        u0, u1, _, _ = profile_bounds(self.profile)
        overlap = axial_overlap(self.profile, self.diameter / 2, self.offset, lo, hi)
        if self.operation == "cut" and overlap <= 1e-9:
            raise FeatureRequestError("the cut removes no material: check the stations and the offset")
        if self.operation == "boss" and overlap <= 1e-9 and not (math.isclose(lo, u1) or math.isclose(hi, u0)):
            raise FeatureRequestError("the boss does not touch the turned part: it would be a separate body")

    @property
    def offset(self) -> float:
        return math.hypot(self.offset_in_view, self.offset_toward_viewer)

    def _datum(self) -> str:
        return "right" if self.axis == "horizontal" else "top"

    def _centre_sketch(self) -> tuple[float, float]:
        """Circle centre in the sketch frame of the perpendicular datum (drawing unit)."""
        _, rmax, _ = turned_extent(self.profile)
        z = rmax + self.offset_toward_viewer
        return (-z, self.offset_in_view) if self.axis == "horizontal" else (self.offset_in_view, -z)

    def graph(self) -> dict:
        self.validate()
        f = factor(self.unit)
        _, _, uc = turned_extent(self.profile)
        lo, hi = self._span()
        cx, cy = self._centre_sketch()
        circle = {"kind": "circle", "diameter": self.diameter * f, "cx": cx * f, "cy": cy * f}
        ext = {"id": "n2", "type": "extrude", "sketch": "n1", "operation": self.operation, "end": "blind", "depth": (hi - lo) * f}
        return graph_of([sketch_node("n1", self._datum(), (lo - uc) * f, [circle]), ext])

    def expected_delta_mm3(self) -> float:
        lo, hi = self._span()
        a = self.diameter / 2
        overlap = axial_overlap(self.profile, a, self.offset, lo, hi)
        v = math.pi * a * a * (hi - lo) - overlap if self.operation == "boss" else -overlap
        return v * MM3_PER_UNIT3[self.unit]

    async def apply(self, ctx: FeatureContext) -> FeatureResult:
        graph = self.graph()
        res = FeatureResult(self.NAME)
        v = await ctx.verifier.run_step(self.label, lambda: ctx.sw.submit_feature_graph(graph, fresh_document=False),
                                        Expect(delta_mm3=self.expected_delta_mm3(), bodies=1))
        res.verdicts.append(v)
        if not v.ok:
            return res
        faces, _ = await read_geometry(ctx.sw)
        f = factor(self.unit)
        frame = PlaneFrame(self._datum())
        cx, cy = self._centre_sketch()
        a, b = frame.in_plane_world(cx * f, cy * f)
        res.probes.append(probe_cylinders(faces, [(self.diameter / 2 * f, a, b)], frame, f"{self.operation} wall: Ø{self.diameter:g} at its offset from the axis"))
        return res


@dataclass(frozen=True)
class CrossHole:
    unit: str
    axis: str
    profile: list[dict]
    diameter: float
    at_u: float
    direction: str = "toward_viewer"     # 'toward_viewer' (into the view) | 'in_view' (perpendicular to the axis, in the drawing plane)
    thread_note: str | None = None
    label: str = "cross hole"

    NAME = "cross_hole"

    def validate(self) -> None:
        factor(self.unit)
        if self.direction not in ("toward_viewer", "in_view"):
            raise FeatureRequestError(f"direction must be 'toward_viewer' or 'in_view', got {self.direction!r}")
        if not self.diameter > 0:
            raise FeatureRequestError("a cross hole needs a positive diameter")
        u0, u1, _, _ = profile_bounds(self.profile)
        a = self.diameter / 2
        if self.at_u - a < u0 or self.at_u + a > u1:
            raise FeatureRequestError("the cross hole runs past the end of the turned part: check at_u")
        if cross_overlap(self.profile, a, self.at_u) <= 1e-9:
            raise FeatureRequestError("the cross hole removes no material at that station")

    def _plane(self) -> tuple[str, float, tuple[float, float]]:
        """(datum, offset, circle centre in that sketch frame), drawing unit."""
        _, rmax, uc = turned_extent(self.profile)
        w = self.at_u - uc
        if self.direction == "toward_viewer":
            return "front", rmax, ((w, 0.0) if self.axis == "horizontal" else (0.0, w))
        return ("top", 0.0, (w, -rmax)) if self.axis == "horizontal" else ("right", 0.0, (-rmax, w))

    def graph(self) -> dict:
        self.validate()
        f = factor(self.unit)
        _, rmax, _ = turned_extent(self.profile)
        datum, off, (cx, cy) = self._plane()
        circle = {"kind": "circle", "diameter": self.diameter * f, "cx": cx * f, "cy": cy * f}
        cut = {"id": "n2", "type": "extrude", "sketch": "n1", "operation": "cut", "end": "mid_plane", "depth": (2 * rmax + 2 * self.diameter) * f}
        return graph_of([sketch_node("n1", datum, off * f, [circle]), cut])

    def expected_delta_mm3(self) -> float:
        return -cross_overlap(self.profile, self.diameter / 2, self.at_u) * MM3_PER_UNIT3[self.unit]

    async def apply(self, ctx: FeatureContext) -> FeatureResult:
        graph = self.graph()
        res = FeatureResult(self.NAME)
        v = await ctx.verifier.run_step(self.label, lambda: ctx.sw.submit_feature_graph(graph, fresh_document=False),
                                        Expect(delta_mm3=self.expected_delta_mm3(), bodies=1))
        res.verdicts.append(v)
        if not v.ok:
            return res
        if self.thread_note:
            res.notes.append(f"thread {self.thread_note}: carried as a note, not modelled")
        faces, _ = await read_geometry(ctx.sw)
        f = factor(self.unit)
        datum, _, (cx, cy) = self._plane()
        frame = PlaneFrame(datum)
        a, b = frame.in_plane_world(cx * f, cy * f)
        res.probes.append(probe_cylinders(faces, [(self.diameter / 2 * f, a, b)], frame, f"cross hole Ø{self.diameter:g} wall at its station"))
        return res
