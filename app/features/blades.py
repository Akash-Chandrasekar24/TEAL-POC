"""Custom features after a turned base (revolve_base): blades / fins around the axis, and a keyway in a shaft.

Frame (set by revolve_base, as in turned_ops): the axis runs along world X ('horizontal') or world Y ('vertical'), at the other in-plane coordinate 0
and at z = R_max; stations `u` are measured like the half-section.

  radial_blades  N equal blades (impeller vanes, fan blades, cooling fins) in the gap between radii r_in and r_out and between two stations.
                 Seen from the u = 0 end ("end view": screen right = toward the viewer of the main view for a horizontal axis, up = up),
                 each blade is the strip of `thickness` around its centre line, cut off exactly by the circles r_in and r_out, so it sits ON the
                 hub (r_in) without overlapping it. Centre line: 'straight' = radial; 'arc' = the textbook single-arc blade through r_in and r_out with
                 the inlet / outlet blade angles (from the tangent), leaning counter-clockwise (hand +1) or clockwise (-1) going outward. All N outlines go in ONE sketch, extruded forward
                 from the smaller station (no circular pattern: the part axis is not a datum axis in this frame).
  turned_keyway  a keyway of `width` x `depth` along the top of a round section (as drawn), from_u..to_u, square or round ends: a slot sketched
                 on the datum plane parallel to the axis at height (R - depth), cut forward (upward) through the top.

Every outline is made of exact lines and arcs; the expected volume is the outline's exact area x the length (blades must lie in empty space
between the stations, which is checked against the half-section), and for the keyway the exact material above the cutting plane
(Gauss quadrature over the slot, closed form across the width), so the SolidWorks volume check catches a wrong size or position.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from app.geometry import profile_area, profile_bounds
from app.verify import Expect
from .base import FeatureContext, FeatureRequestError, FeatureResult, factor, graph_of, sketch_node
from .revolve_base import turned_extent
from .turned_ops import MM3_PER_UNIT3, section_intervals

_EPS = 1e-6


def _ang(p):
    return math.atan2(p[1], p[0])


def _norm(a):
    """Angle wrapped into (-pi, pi]."""
    return (a + math.pi) % (2 * math.pi) - math.pi


def _circle_cross(c, rho, r, near):
    """The intersection of circle (centre c, radius rho) with the axis circle of radius r closest to `near`, or None."""
    d = math.hypot(*c)
    if d < _EPS or d > rho + r or d < abs(rho - r):
        return None
    a = (r * r - rho * rho + d * d) / (2 * d)
    h = math.sqrt(max(0.0, r * r - a * a))
    ux, uy = c[0] / d, c[1] / d
    cands = [(a * ux - h * uy, a * uy + h * ux), (a * ux + h * uy, a * uy - h * ux)]
    return min(cands, key=lambda p: math.hypot(p[0] - near[0], p[1] - near[1]))


def single_arc(r1: float, r2: float, beta1_deg: float, beta2_deg: float, hand: int):
    """The textbook single-arc impeller blade through r1 (inlet blade angle beta1) and r2 (outlet angle beta2), both measured from the tangent.
    Returns (centre, radius, p1, p2, turn) with p1 = (r1, 0) and turn = +1 / -1 the sense round the centre from p1 to p2; the blade runs
    counter-clockwise round the axis (hand +1) or clockwise (hand -1) as it goes outward."""
    b1, b2 = math.radians(beta1_deg), math.radians(beta2_deg)
    den = 2 * (r2 * math.cos(b2) - r1 * math.cos(b1))
    if not (0 < b1 < math.pi / 2 and 0 < b2 < math.pi / 2) or abs(den) < _EPS:
        raise FeatureRequestError("blade angles must be between 0 and 90 degrees and give a finite blade radius")
    R = abs((r2 * r2 - r1 * r1) / den)
    p1 = (r1, 0.0)
    t1 = (math.sin(b1), hand * math.cos(b1))                                     # outward, leaning round the axis by the hand
    for n in ((-t1[1], t1[0]), (t1[1], -t1[0])):
        cen = (p1[0] + R * n[0], p1[1] + R * n[1])
        if abs(cen[0] ** 2 + cen[1] ** 2 - (r2 * r2 + R * R - 2 * r2 * R * math.cos(b2))) > 1e-6 * max(1.0, r2 * r2):
            continue
        turn = 1 if (p1[0] - cen[0]) * t1[1] - (p1[1] - cen[1]) * t1[0] > 0 else -1
        d = math.hypot(*cen)
        if d > R + r2 or d < abs(R - r2):
            continue
        a = (r2 * r2 - R * R + d * d) / (2 * d)
        h = math.sqrt(max(0.0, r2 * r2 - a * a))
        ux, uy = cen[0] / d, cen[1] / d
        a1 = math.atan2(p1[1] - cen[1], p1[0] - cen[0])
        travel = lambda q: ((math.atan2(q[1] - cen[1], q[0] - cen[0]) - a1) * turn) % (2 * math.pi)
        p2 = min([(a * ux - h * uy, a * uy + h * ux), (a * ux + h * uy, a * uy - h * ux)], key=travel)
        return cen, R, p1, p2, turn
    raise FeatureRequestError("no single-arc blade fits these diameters and angles")


def blade_outline(r_in: float, r_out: float, thickness: float, shape: str, inlet_angle_deg: float, outlet_angle_deg: float, hand: int,
                  angle_deg: float) -> list[dict]:
    """One blade as a closed loop of exact lines / arcs (end-view coordinates, centred on the axis)."""
    t = thickness / 2
    rot = math.radians(angle_deg)
    if shape == "straight":
        if t >= r_in:
            raise FeatureRequestError("a blade thicker than the inner diameter cannot sit on the hub")
        si, so = math.sqrt(r_in ** 2 - t * t), math.sqrt(r_out ** 2 - t * t)
        a_in, b_in, a_out, b_out = (si, -t), (si, t), (so, -t), (so, t)
        side_a = {"kind": "line", "x1": a_in[0], "y1": a_in[1], "x2": a_out[0], "y2": a_out[1]}
        side_b = {"kind": "line", "x1": b_out[0], "y1": b_out[1], "x2": b_in[0], "y2": b_in[1]}
    elif shape == "arc":
        cen, R, p1, p2, sweep_dir = single_arc(r_in, r_out, inlet_angle_deg, outlet_angle_deg, hand)
        if t >= R:
            raise FeatureRequestError("the blade is thicker than its curvature allows")
        pts = {}
        for side, rho in (("a", R + t), ("b", R - t)):
            for which, r, near in (("in", r_in, p1), ("out", r_out, p2)):
                q = _circle_cross(cen, rho, r, near)
                if q is None:
                    raise FeatureRequestError("the blade's sides do not reach both diameters: check the blade angles and thickness")
                pts[side + which] = q
        # walk side a from inner to outer, outer circle to side b, side b back, inner circle back
        side_a = {"kind": "arc", "cx": cen[0], "cy": cen[1], "x1": pts["ain"][0], "y1": pts["ain"][1], "x2": pts["aout"][0], "y2": pts["aout"][1],
                  "dir": sweep_dir}
        side_b = {"kind": "arc", "cx": cen[0], "cy": cen[1], "x1": pts["bout"][0], "y1": pts["bout"][1], "x2": pts["bin"][0], "y2": pts["bin"][1],
                  "dir": -sweep_dir}
        a_in, a_out, b_out, b_in = pts["ain"], pts["aout"], pts["bout"], pts["bin"]
    else:
        raise FeatureRequestError(f"blade shape must be 'straight' or 'arc', got {shape!r}")

    def rim(p, q, r):
        turn = _norm(_ang(q) - _ang(p))
        return {"kind": "arc", "cx": 0.0, "cy": 0.0, "x1": p[0], "y1": p[1], "x2": q[0], "y2": q[1], "dir": 1 if turn > 0 else -1}
    loop = [side_a, rim(a_out, b_out, r_out), side_b, rim(b_in, a_in, r_in)]
    return [_rotate(s, rot) for s in loop]


def _rotate(s, a):
    ca, sa = math.cos(a), math.sin(a)
    rot = lambda x, y: (x * ca - y * sa, x * sa + y * ca)
    out = dict(s)
    out["x1"], out["y1"] = rot(s["x1"], s["y1"])
    out["x2"], out["y2"] = rot(s["x2"], s["y2"])
    if s["kind"] == "arc":
        out["cx"], out["cy"] = rot(s["cx"], s["cy"])
    return out


def _segments_cross(a: list[dict], b: list[dict]) -> bool:
    """Do two closed outlines overlap? (sampled boundaries: any point of one strictly inside the other, both ways)."""
    def samples(loop):
        out = []
        for s in loop:
            if s["kind"] == "line":
                out += [(s["x1"] + (s["x2"] - s["x1"]) * k / 16, s["y1"] + (s["y2"] - s["y1"]) * k / 16) for k in range(16)]
            else:
                a1 = math.atan2(s["y1"] - s["cy"], s["x1"] - s["cx"])
                a2 = math.atan2(s["y2"] - s["cy"], s["x2"] - s["cx"])
                sw = (a2 - a1) % (2 * math.pi) if s["dir"] > 0 else -((a1 - a2) % (2 * math.pi))
                rho = math.hypot(s["x1"] - s["cx"], s["y1"] - s["cy"])
                out += [(s["cx"] + rho * math.cos(a1 + sw * k / 32), s["cy"] + rho * math.sin(a1 + sw * k / 32)) for k in range(32)]
        return out

    def inside(p, loop_pts):
        x, y, n, hit = p[0], p[1], len(loop_pts), False
        for i in range(n):
            (x1, y1), (x2, y2) = loop_pts[i], loop_pts[(i + 1) % n]
            if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
                hit = not hit
        return hit
    pa, pb = samples(a), samples(b)
    return any(inside(p, pb) for p in pa) or any(inside(p, pa) for p in pb)


def _end_view_to_sketch(axis: str, rmax: float):
    """End-view point (screen x, screen y) relative to the axis -> sketch coordinates on the datum perpendicular to the axis (see turned_ops)."""
    if axis == "horizontal":        # datum 'right': sketch x = world -Z, y = world Y; screen right = +Z (toward the main view's viewer), up = +Y
        return lambda x, y: (-(rmax + x), y)
    return lambda x, y: (x, -(rmax + y))   # datum 'top': sketch x = world X, y = world -Z; screen right = +X, up = +Z


def _map_segment(s, fn, f):
    out = {"kind": s["kind"]}
    out["x1"], out["y1"] = (v * f for v in fn(s["x1"], s["y1"]))
    out["x2"], out["y2"] = (v * f for v in fn(s["x2"], s["y2"]))
    if s["kind"] == "arc":
        out["cx"], out["cy"] = (v * f for v in fn(s["cx"], s["cy"]))
        # both mappings are a reflection (x -> -x, or y -> -y): the turning sense flips
        out["dir"] = -s["dir"]
    return out


@dataclass(frozen=True)
class RadialBlades:
    unit: str
    axis: str
    profile: list[dict]
    count: int
    inner_diameter: float
    outer_diameter: float
    thickness: float
    from_u: float
    to_u: float
    shape: str = "straight"
    inlet_angle_deg: float = 0.0
    outlet_angle_deg: float = 0.0
    hand: int = 1
    start_angle_deg: float = 0.0
    label: str = "radial blades"

    NAME = "radial_blades"

    def _span(self):
        return min(self.from_u, self.to_u), max(self.from_u, self.to_u)

    def outlines(self) -> list[list[dict]]:
        r_in, r_out = self.inner_diameter / 2, self.outer_diameter / 2
        return [blade_outline(r_in, r_out, self.thickness, self.shape, self.inlet_angle_deg, self.outlet_angle_deg, self.hand, self.start_angle_deg + 360 * k / self.count) for k in range(self.count)]

    def validate(self) -> None:
        factor(self.unit)
        lo, hi = self._span()
        r_in, r_out = self.inner_diameter / 2, self.outer_diameter / 2
        if not (isinstance(self.count, int) and self.count >= 1) or not (0 < r_in < r_out) or not self.thickness > 0 or hi - lo <= 0:
            raise FeatureRequestError("radial_blades needs count >= 1, 0 < inner < outer diameter, a positive thickness and two different stations")
        u0, u1, _, _ = profile_bounds(self.profile)
        if lo < u0 - _EPS or hi > u1 + _EPS:
            raise FeatureRequestError("the blades run past the ends of the turned part: check from_u / to_u")
        loops = self.outlines()
        if self.count > 1 and (_segments_cross(loops[0], loops[1]) or _segments_cross(loops[0], loops[-1])):
            raise FeatureRequestError("neighbouring blades overlap: fewer blades, thinner blades or less wrap")
        touching = False
        for k in range(41):                                                      # the blades must lie in empty space between the stations
            u = lo + (hi - lo) * (k + 0.4142) / 41                                 # off round fractions: never exactly on a step
            for a, b in section_intervals(self.profile, u):
                if b > r_in + _EPS and a < r_out - _EPS:
                    raise FeatureRequestError(f"the blades would cut into existing material at u = {u:g} (radii {a:g}..{b:g}): "
                                              "blades go in the empty gap, inner diameter = the hub")
                if abs(b - r_in) < 1e-3:
                    touching = True
        for u in (lo - 1e-3, hi + 1e-3):                                          # or they span between two shrouds
            if any(a <= r_in + _EPS and b >= r_out - _EPS for a, b in section_intervals(self.profile, u)):
                touching = True
        if not touching:
            raise FeatureRequestError("the blades touch neither the hub (inner diameter) nor a shroud at either station: they would be a separate body")

    def graph(self) -> dict:
        self.validate()
        f = factor(self.unit)
        _, rmax, uc = turned_extent(self.profile)
        fn = _end_view_to_sketch(self.axis, rmax)
        lo, hi = self._span()
        datum = "right" if self.axis == "horizontal" else "top"
        prims = [_map_segment(s, fn, f) for loop in self.outlines() for s in loop]
        ext = {"id": "n2", "type": "extrude", "sketch": "n1", "operation": "boss", "end": "blind", "depth": (hi - lo) * f}
        return graph_of([sketch_node("n1", datum, (lo - uc) * f, prims), ext])

    def expected_delta_mm3(self) -> float:
        lo, hi = self._span()
        return sum(profile_area(loop) for loop in self.outlines()) * (hi - lo) * MM3_PER_UNIT3[self.unit]

    async def apply(self, ctx: FeatureContext) -> FeatureResult:
        graph = self.graph()
        res = FeatureResult(self.NAME)
        v = await ctx.verifier.run_step(self.label, lambda: ctx.sw.submit_feature_graph(graph, fresh_document=False),
                                        Expect(delta_mm3=self.expected_delta_mm3(), bodies=1))
        res.verdicts.append(v)
        return res


@dataclass(frozen=True)
class TurnedKeyway:
    unit: str
    axis: str
    profile: list[dict]
    width: float
    depth: float
    from_u: float
    to_u: float
    ends: str = "round"            # 'round' (end-milled) | 'square'
    label: str = "keyway"

    NAME = "turned_keyway"

    def _span(self):
        return min(self.from_u, self.to_u), max(self.from_u, self.to_u)

    def radius(self) -> float:
        lo, hi = self._span()
        rs = set()
        for k in range(21):
            iv = section_intervals(self.profile, lo + (hi - lo) * (k + 0.4142) / 21)   # off round fractions: never exactly on a step
            if not iv:
                raise FeatureRequestError("the keyway runs off the shaft: check from_u / to_u")
            rs.add(round(iv[-1][1], 6))
        if len(rs) != 1:
            raise FeatureRequestError("a keyway must lie on ONE round section (the diameter changes between from_u and to_u)")
        return rs.pop()

    def validate(self) -> None:
        factor(self.unit)
        if self.axis != "horizontal":
            raise FeatureRequestError("turned_keyway is built on a horizontal shaft only (as drawn in the plate view)")
        if self.ends not in ("round", "square"):
            raise FeatureRequestError(f"ends must be 'round' or 'square', got {self.ends!r}")
        lo, hi = self._span()
        R = self.radius()
        if not (0 < self.width < 2 * R and 0 < self.depth < R):
            raise FeatureRequestError("the keyway must be narrower than the shaft and shallower than its radius")
        if self.ends == "round" and hi - lo <= self.width:
            raise FeatureRequestError("a round-ended keyway must be longer than it is wide")

    def outline(self) -> list[dict]:
        """The slot in the Top datum's sketch frame (x = world X, y = world -Z), drawing unit."""
        lo, hi = self._span()
        _, rmax, uc = turned_extent(self.profile)
        x0, x1, w, yc = lo - uc, hi - uc, self.width / 2, -rmax
        if self.ends == "square":
            pts = [(x0, yc - w), (x1, yc - w), (x1, yc + w), (x0, yc + w)]
            return [{"kind": "line", "x1": a[0], "y1": a[1], "x2": b[0], "y2": b[1]} for a, b in zip(pts, pts[1:] + pts[:1])]
        a, b = x0 + w, x1 - w
        return [{"kind": "line", "x1": a, "y1": yc - w, "x2": b, "y2": yc - w},
                {"kind": "arc", "cx": b, "cy": yc, "x1": b, "y1": yc - w, "x2": b, "y2": yc + w, "dir": 1},
                {"kind": "line", "x1": b, "y1": yc + w, "x2": a, "y2": yc + w},
                {"kind": "arc", "cx": a, "cy": yc, "x1": a, "y1": yc + w, "x2": a, "y2": yc - w, "dir": 1}]

    def graph(self) -> dict:
        self.validate()
        f = factor(self.unit)
        R = self.radius()
        prims = [{k: (v if k in ("kind", "dir") else v * f) for k, v in s.items()} for s in self.outline()]
        cut = {"id": "n2", "type": "extrude", "sketch": "n1", "operation": "cut", "end": "blind", "depth": (self.depth + R) * f}
        return graph_of([sketch_node("n1", "top", (R - self.depth) * f, prims), cut])

    def expected_delta_mm3(self) -> float:
        """Minus the shaft material above the plane y = R - depth inside the slot (height h(z) = sqrt(R^2 - z^2) - (R - depth) across the width)."""
        lo, hi = self._span()
        R, w, c = self.radius(), self.width / 2, self.radius() - self.depth
        h = lambda z: max(0.0, math.sqrt(max(0.0, R * R - z * z)) - c)
        xs, ws = np.polynomial.legendre.leggauss(64)
        across = sum(wk * h(w * xk) for xk, wk in zip(xs, ws)) * w                 # integral of h over the width
        if self.ends == "square":
            v = across * (hi - lo)
        else:
            discs = sum(wk * h(w * xk) * 2 * math.sqrt(max(0.0, 1 - xk * xk)) * w for xk, wk in zip(xs, ws)) * w   # the two half-disc ends = one disc
            v = across * (hi - lo - 2 * w) + discs
        return -v * MM3_PER_UNIT3[self.unit]

    async def apply(self, ctx: FeatureContext) -> FeatureResult:
        graph = self.graph()
        res = FeatureResult(self.NAME)
        v = await ctx.verifier.run_step(self.label, lambda: ctx.sw.submit_feature_graph(graph, fresh_document=False),
                                        Expect(delta_mm3=self.expected_delta_mm3(), bodies=1))
        res.verdicts.append(v)
        return res
