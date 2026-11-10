"""Features built through the application geometry tools (extensions/solidworks/GeometryExtensions.cs):

  swept_passage  a round passage CUT (or a round rod / rib boss) swept along a path in a plane parallel to the front face: straight runs with
                 bends, or a SPIRAL of tangent arcs whose radius grows (the volute of a pump casing, a coolant gallery, a cable channel).
                 Expected change: at most section area x centreline length (a bound: the passage may run partly outside material).
  shell          hollow the base out to a wall thickness, opening chosen faces (a box, a cover, a cup). Exact on a plain box or cylinder base,
                 directly after the base.
  angled_boss    a round boss standing on the front face at an angle (a spark-plug boss, an angled port), optionally with a hole down its axis.
                 Exact: the boss above the face is pi r^2 h (the wedges either side of the tilted start plane cancel), the hole pi r_h^2 depth.

Coordinates: the front view of the base, centred (x right, y up from the centre of the base outline, or of a turned part's length and axis),
z from the back face (0) toward the viewer; sent to SolidWorks in model metres. Every SolidWorks step is volume-checked by the step verifier.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from app.verify import Expect
from .base import FeatureContext, FeatureRequestError, FeatureResult, factor
from .standalone import _path_samples, tube_path
from .turned_ops import MM3_PER_UNIT3

_EPS = 1e-9


def spiral_path(cx: float, cy: float, r0: float, r1: float, sweep_deg: float, start_angle_deg: float, hand: int) -> tuple[list[dict], float]:
    """Tangent arcs whose radii grow evenly from r0 to r1 over `sweep_deg` (pieces of at most 90 degrees), starting at start_angle_deg round
    (cx, cy): the draughtsman's volute. Returns (segments, centreline length)."""
    k = max(2, math.ceil(sweep_deg / 90 - 1e-9))
    step = math.radians(sweep_deg) / k * hand
    radii = [r0 + (r1 - r0) * i / (k - 1) for i in range(k)]
    c = (cx, cy)
    theta = math.radians(start_angle_deg)
    segs, length = [], 0.0
    u = lambda a: (math.cos(a), math.sin(a))
    for i, rho in enumerate(radii):
        a, b = theta, theta + step
        p1 = (c[0] + rho * u(a)[0], c[1] + rho * u(a)[1])
        p2 = (c[0] + rho * u(b)[0], c[1] + rho * u(b)[1])
        segs.append({"kind": "arc", "cx": c[0], "cy": c[1], "x1": p1[0], "y1": p1[1], "x2": p2[0], "y2": p2[1], "dir": hand})
        length += rho * abs(step)
        if i + 1 < k:                                                        # keep the next arc tangent: centres on the shared normal
            c = (c[0] + (rho - radii[i + 1]) * u(b)[0], c[1] + (rho - radii[i + 1]) * u(b)[1])
        theta = b
    return segs, length


def _mid(s: dict) -> tuple[float, float]:
    a1 = math.atan2(s["y1"] - s["cy"], s["x1"] - s["cx"])
    a2 = math.atan2(s["y2"] - s["cy"], s["x2"] - s["cx"])
    sw = ((a2 - a1) % (2 * math.pi)) if s["dir"] > 0 else -((a1 - a2) % (2 * math.pi))
    r = math.hypot(s["x1"] - s["cx"], s["y1"] - s["cy"])
    return s["cx"] + r * math.cos(a1 + sw / 2), s["cy"] + r * math.sin(a1 + sw / 2)


def _start_tangent(s: dict) -> tuple[float, float]:
    if s["kind"] == "line":
        d = (s["x2"] - s["x1"], s["y2"] - s["y1"])
    else:
        rx, ry = s["x1"] - s["cx"], s["y1"] - s["cy"]
        d = (-ry * s["dir"], rx * s["dir"])
    n = math.hypot(*d)
    return d[0] / n, d[1] / n


def entities_3d(segs: list[dict], z: float, f: float) -> list[dict]:
    """Front-view segments at depth z -> model-space entities in metres (what the geometry tools take)."""
    P = lambda x, y: [x * f, y * f, z * f]
    out = []
    for s in segs:
        if s["kind"] == "line":
            out.append({"kind": "line", "p1": P(s["x1"], s["y1"]), "p2": P(s["x2"], s["y2"])})
        else:
            mx, my = _mid(s)
            out.append({"kind": "arc", "center": P(s["cx"], s["cy"]), "p1": P(s["x1"], s["y1"]), "p2": P(s["x2"], s["y2"]), "mid": P(mx, my)})
    return out


def _ok(result, what: str) -> dict:
    if not result.ok:
        raise FeatureRequestError(f"{what}: {result.text[:300]}")
    return result.json()


@dataclass(frozen=True)
class SweptPassage:
    unit: str
    operation: str                       # 'cut' | 'boss'
    diameter: float
    plane_z: float
    path_kind: str                       # 'polyline' | 'spiral'
    points: tuple = ()
    bend_radii: tuple = ()
    spiral: tuple = ()                   # (cx, cy, r0, r1, sweep_deg, start_angle_deg, hand)
    size: tuple = (0.0, 0.0, 0.0)        # the base's (W, H, T) for the inside-the-part check
    label: str = "swept passage"
    end_diameter: float | None = None    # spiral only: a volute that widens, swept a quarter turn at a time, each with its own diameter

    NAME = "swept_passage"

    def path(self) -> tuple[list[dict], float]:
        if self.path_kind == "spiral":
            return spiral_path(*self.spiral)
        return tube_path([tuple(p) for p in self.points], list(self.bend_radii))

    def pieces(self) -> list[tuple[list[dict], float]]:
        """(segments, diameter) per sweep: one sweep, or for a widening volute one per quarter-turn arc with the diameter at the arc's
        middle (a stepped volute)."""
        segs, _ = self.path()
        if self.end_diameter is None or self.end_diameter == self.diameter:
            return [(segs, self.diameter)]
        n = len(segs)
        return [([s], self.diameter + (self.end_diameter - self.diameter) * (i + 0.5) / n) for i, s in enumerate(segs)]

    def validate(self) -> None:
        factor(self.unit)
        if self.operation not in ("cut", "boss") or not self.diameter > 0:
            raise FeatureRequestError("a swept passage is a cut or a boss with a positive diameter")
        if self.end_diameter is not None:
            if self.path_kind != "spiral" or not self.end_diameter > 0:
                raise FeatureRequestError("an end diameter (a widening passage) is for a spiral path, and must be positive")
            if self.spiral[4] % 90 or self.spiral[4] < 180:              # every step must start square to x or y
                raise FeatureRequestError("a widening volute winds a whole number of quarter turns, at least half a turn (180, 270, 360 ... degrees)")
        r = max(self.diameter, self.end_diameter or 0) / 2
        if self.path_kind == "spiral":
            cx, cy, r0, r1, sweep, start, hand = self.spiral
            if not (0 < r0 and 0 < r1 and 30 <= sweep <= 720) or start % 90 or hand not in (1, -1):
                raise FeatureRequestError("a spiral needs positive radii, a sweep of 30-720 degrees, a start angle of 0 / 90 / 180 / 270 and a hand")
            if min(r0, r1) <= r:
                raise FeatureRequestError("the spiral's radius must stay larger than the passage radius")
        elif self.path_kind == "polyline":
            if any(b <= r for b in self.bend_radii if len(self.points) > 2):
                raise FeatureRequestError("every bend radius must be larger than the passage radius")
        else:
            raise FeatureRequestError("path kind must be polyline or spiral")
        segs, length = self.path()
        t = _start_tangent(segs[0])
        if not (abs(abs(t[0]) - 1) < 1e-9 or abs(abs(t[1]) - 1) < 1e-9):
            raise FeatureRequestError("the path must start horizontal or vertical in the front view (the section is drawn on a datum plane square to it)")
        pts = _path_samples(segs, max(r / 4, length / 2000))
        reach = math.pi * max(r, *(math.hypot(s["x1"] - s["cx"], s["y1"] - s["cy"]) for s in segs if s["kind"] == "arc"), *(self.bend_radii or (0,)))
        for i in range(len(pts)):
            for j in range(i + 1, len(pts)):
                if pts[j][2] - pts[i][2] > reach and math.hypot(pts[j][0] - pts[i][0], pts[j][1] - pts[i][1]) < 2 * r - 1e-6:
                    raise FeatureRequestError("the passage runs into itself")
        W, H, T = self.size
        if self.operation == "cut" and W:
            xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
            if min(xs) - r < -W / 2 - _EPS or max(xs) + r > W / 2 + _EPS or min(ys) - r < -H / 2 - _EPS or max(ys) + r > H / 2 + _EPS \
                    or self.plane_z - r < -_EPS or self.plane_z + r > T + _EPS:
                raise FeatureRequestError("the passage leaves the part's outline or thickness: check the path, the depth and the diameter")

    def _one_spec(self, segs: list[dict], diameter: float) -> dict:
        f = factor(self.unit)
        x0, y0 = segs[0]["x1"], segs[0]["y1"]
        t = _start_tangent(segs[0])
        plane = {"datum": "right", "offset": x0 * f} if abs(t[0]) > 0.5 else {"datum": "top", "offset": y0 * f}
        circle = {"kind": "circle", "center": [x0 * f, y0 * f, self.plane_z * f], "radius": diameter / 2 * f}
        return {"operation": self.operation, "path": {"plane": {"datum": "front", "offset": self.plane_z * f}, "entities": entities_3d(segs, self.plane_z, f)},
                "profile": {"plane": plane, "entities": [circle]}}

    def specs(self) -> list[dict]:
        self.validate()
        return [self._one_spec(segs, d) for segs, d in self.pieces()]

    def spec(self) -> dict:
        return self.specs()[0]

    def piece_bounds_mm3(self) -> list[float]:
        """Each piece: section area x its arc length (one quarter-turn arc of the spiral: radius x pi/2)."""
        k = MM3_PER_UNIT3[self.unit]
        ps = self.pieces()
        if len(ps) == 1:
            return [math.pi / 4 * self.diameter ** 2 * self.path()[1] * k]
        step = math.radians(self.spiral[4]) / len(ps)
        return [math.pi / 4 * d ** 2 * math.hypot(s[0]["x1"] - s[0]["cx"], s[0]["y1"] - s[0]["cy"]) * step * k for s, d in ps]

    def bound_mm3(self) -> float:
        return sum(self.piece_bounds_mm3())

    def expected(self) -> Expect:
        return Expect(bound_mm3=self.expected_delta_mm3(), bodies=1)       # signed: a cut removes

    def expected_delta_mm3(self) -> float:
        return (-1 if self.operation == "cut" else 1) * self.bound_mm3()

    async def apply(self, ctx: FeatureContext) -> FeatureResult:
        specs, bounds = self.specs(), self.piece_bounds_mm3()
        res = FeatureResult(self.NAME)
        for i, (spec, b) in enumerate(zip(specs, bounds)):
            label = self.label if len(specs) == 1 else f"{self.label} {i + 1}/{len(specs)}"
            v = await ctx.verifier.run_step(label, lambda spec=spec: ctx.sw.geometry("ext_sweep", spec),
                                            Expect(bound_mm3=-b if self.operation == "cut" else b, bodies=1))
            res.verdicts.append(v)
            if not v.ok:
                break
        return res


@dataclass(frozen=True)
class Shell:
    unit: str
    thickness: float
    open_faces: tuple
    base: str                            # 'box' | 'cylinder'
    size: tuple                          # box (W, H, T) | cylinder (L, R, axis)
    label: str = "shell"

    NAME = "shell"
    BOX_FACES = ("front", "back", "top", "bottom", "left", "right")
    END_FACES = ("start", "end")

    def validate(self) -> None:
        factor(self.unit)
        t = self.thickness
        if not t > 0 or not self.open_faces or len(set(self.open_faces)) != len(self.open_faces):
            raise FeatureRequestError("a shell needs a positive wall thickness and at least one face to open (each once)")
        names = self.BOX_FACES if self.base == "box" else self.END_FACES
        bad = [f for f in self.open_faces if f not in names]
        if bad:
            raise FeatureRequestError(f"open faces of a {self.base} base are {names}, got {bad}")
        if self.base == "box":
            W, H, T = self.size
            if min(self.cavity()) <= 0:
                raise FeatureRequestError("the wall is too thick for this box: nothing would be left inside")
        else:
            L, R, _ = self.size
            if t >= R or self.cavity()[1] <= 0:
                raise FeatureRequestError("the wall is too thick for this cylinder")

    def cavity(self):
        t, o = self.thickness, set(self.open_faces)
        if self.base == "box":
            W, H, T = self.size
            walls = lambda a, b: (a not in o) + (b not in o)
            return (W - t * walls("left", "right"), H - t * walls("top", "bottom"), T - t * walls("front", "back"))
        L, R, _ = self.size
        return (R - t, L - t * ((("start" not in o)) + ("end" not in o)))

    def face_points(self) -> list[list[float]]:
        f = factor(self.unit)
        if self.base == "box":
            W, H, T = self.size
            pts = {"front": (0, 0, T), "back": (0, 0, 0), "top": (0, H / 2, T / 2), "bottom": (0, -H / 2, T / 2),
                   "left": (-W / 2, 0, T / 2), "right": (W / 2, 0, T / 2)}
        else:
            L, R, axis = self.size
            pts = {"start": (-L / 2, 0, R), "end": (L / 2, 0, R)} if axis == "horizontal" else {"start": (0, -L / 2, R), "end": (0, L / 2, R)}
        return [[v * f for v in pts[name]] for name in self.open_faces]

    def spec(self) -> dict:
        self.validate()
        return {"thickness": self.thickness * factor(self.unit), "faces": self.face_points(), "outward": False}

    def expected_delta_mm3(self) -> float:
        c = self.cavity()
        v = c[0] * c[1] * c[2] if self.base == "box" else math.pi * c[0] ** 2 * c[1]
        return -v * MM3_PER_UNIT3[self.unit]

    def expected(self) -> Expect:
        return Expect(delta_mm3=self.expected_delta_mm3(), bodies=1)

    async def apply(self, ctx: FeatureContext) -> FeatureResult:
        spec = self.spec()
        res = FeatureResult(self.NAME)
        res.verdicts.append(await ctx.verifier.run_step(self.label, lambda: ctx.sw.geometry("ext_shell", spec), self.expected()))
        return res


LEAN = {"up": ((0, 1), "x"), "down": ((0, -1), "x"), "right": ((1, 0), "y"), "left": ((-1, 0), "y")}


@dataclass(frozen=True)
class AngledBoss:
    unit: str
    x: float
    y: float
    diameter: float
    height: float
    tilt_deg: float
    lean: str
    size: tuple                          # the base's (W, H, T)
    hole_diameter: float | None = None
    hole_depth: float | str | None = None
    label: str = "angled boss"

    NAME = "angled_boss"

    def axis(self) -> tuple[float, float, float]:
        (dx, dy), _ = LEAN[self.lean]
        th = math.radians(self.tilt_deg)
        return (dx * math.sin(th), dy * math.sin(th), math.cos(th))

    def back_depth(self) -> float:
        """How far the boss also grows the other way from its tilted start plane: just enough to close the wedge on the uphill side."""
        return self.diameter / 2 * math.tan(math.radians(self.tilt_deg)) * 1.02

    def validate(self) -> None:
        factor(self.unit)
        if self.lean not in LEAN:
            raise FeatureRequestError(f"lean must be one of {tuple(LEAN)}")
        r, th = self.diameter / 2, math.radians(self.tilt_deg)
        W, H, T = self.size
        if not (0 < self.tilt_deg <= 60 and r > 0 and self.height > 0):
            raise FeatureRequestError("an angled boss needs a tilt of 0-60 degrees, a positive diameter and height")
        if self.height <= r * math.tan(th):
            raise FeatureRequestError("the boss is too short for its tilt: its top would dip into the face")
        if abs(self.x) + r / math.cos(th) > W / 2 or abs(self.y) + r / math.cos(th) > H / 2:
            raise FeatureRequestError("the boss foot runs past the edge of the face")
        if T < 2.1 * r * math.sin(th):
            raise FeatureRequestError("the plate is too thin to seat the boss at this tilt")
        if self.hole_diameter is not None:
            if not 0 < self.hole_diameter < self.diameter:
                raise FeatureRequestError("the hole must be narrower than the boss")
            if self.hole_depth != "through" and not (isinstance(self.hole_depth, (int, float)) and self.hole_depth > 0):
                raise FeatureRequestError("give the hole depth (from the top of the boss) or 'through'")
            if isinstance(self.hole_depth, (int, float)):
                bottom = (self.hole_depth - self.height) * math.cos(th) + self.hole_diameter / 2 * math.sin(th)
                if bottom > T - 1e-6:
                    raise FeatureRequestError("a blind hole this deep would come out of the back face: use 'through'")

    def plane_spec(self) -> dict:
        f = factor(self.unit)
        T = self.size[2]
        _, along = LEAN[self.lean]
        return {"reference": {"datum": "front", "offset": T * f}, "point": [self.x * f, self.y * f, T * f], "along": along,
                "angle_deg": self.tilt_deg, "normal": list(self.axis())}

    def boss_spec(self, plane: str) -> dict:
        f = factor(self.unit)
        T = self.size[2]
        return {"operation": "boss", "plane": {"name": plane},
                "entities": [{"kind": "circle", "center": [self.x * f, self.y * f, T * f], "radius": self.diameter / 2 * f}],
                "direction": list(self.axis()), "end": "blind", "depth": self.height * f, "depth_back": self.back_depth() * f}

    def hole_spec(self, plane: str, normal_sign: int = 1) -> dict:
        """The hole starts on the boss top: the tilted plane offset by the height, along its +normal (which SolidWorks may have pointed
        either way along the axis: normal_sign from the plane tool)."""
        f = factor(self.unit)
        a, T, h = self.axis(), self.size[2], self.height
        top = [(self.x + a[0] * h) * f, (self.y + a[1] * h) * f, (T + a[2] * h) * f]
        through = self.hole_depth == "through"
        return {"operation": "cut", "plane": {"name": plane, "offset": normal_sign * h * f},
                "entities": [{"kind": "circle", "center": top, "radius": self.hole_diameter / 2 * f}],
                "direction": [-v for v in a], "end": "through_all" if through else "blind", "depth": 0.0 if through else self.hole_depth * f}

    def expected_delta_mm3(self) -> float:
        return math.pi / 4 * self.diameter ** 2 * self.height * MM3_PER_UNIT3[self.unit]

    def hole_expected(self) -> Expect:
        k = MM3_PER_UNIT3[self.unit]
        a = math.pi / 4 * self.hole_diameter ** 2
        if self.hole_depth == "through":
            th = math.radians(self.tilt_deg)
            return Expect(bound_mm3=a * (self.height + 2 * self.size[2] / math.cos(th)) * k, bodies=1)
        return Expect(delta_mm3=-a * self.hole_depth * k, bodies=1)

    async def apply(self, ctx: FeatureContext) -> FeatureResult:
        self.validate()
        res = FeatureResult(self.NAME)
        made = _ok(await ctx.sw.geometry("ext_angled_plane", self.plane_spec()), "the tilted plane")
        plane, sign = made["plane"], int(made.get("normal_sign", 1))
        v = await ctx.verifier.run_step(self.label, lambda: ctx.sw.geometry("ext_sketch_extrude", self.boss_spec(plane)),
                                        Expect(delta_mm3=self.expected_delta_mm3(), bodies=1))
        res.verdicts.append(v)
        if v.ok and self.hole_diameter is not None:
            res.verdicts.append(await ctx.verifier.run_step(self.label + " hole", lambda: ctx.sw.geometry("ext_sketch_extrude", self.hole_spec(plane, sign)),
                                                            self.hole_expected()))
        return res


@dataclass(frozen=True)
class SideProfileCut:
    """The part's outline in the SIDE view (x along the part as in the top view, z = height above the back face): everything of the
    base outside it is cut away, right through the part's width. Top outline x side outline = the real shape of a part that rises,
    steps or slopes (a triple clamp raised in the middle, a bracket with sloped arms). One ext_sketch_extrude cut on the Top datum:
    the base's side box and the outline as two loops, so SolidWorks removes the region between them."""
    unit: str
    outline: tuple                       # closed lines / arcs, drawing unit, x centred on the base outline, z from the back face
    size: tuple                          # the base's (W, H, T)
    label: str = "side profile cut"

    NAME = "side_profile_cut"
    MARGIN = 5.0

    def validate(self) -> None:
        from app.geometry import profile_bounds
        factor(self.unit)
        W, H, T = self.size
        if not self.outline:
            raise FeatureRequestError("give the side outline")
        x0, x1, z0, z1 = profile_bounds(list(self.outline))
        tol = 1e-6 * max(W, T)
        if x0 < -W / 2 - tol or x1 > W / 2 + tol:
            raise FeatureRequestError(f"the side outline runs {x0:g}..{x1:g} along x, outside the base outline (-{W / 2:g}..{W / 2:g})")
        if z0 < -tol or z1 > T + tol:
            raise FeatureRequestError(f"the side outline reaches height {z1:g}, the base is only {T:g} tall: extrude the base to the part's FULL height first")

    def removed_bound_mm3(self) -> float:
        from app.geometry import profile_area
        W, H, T = self.size
        return max(0.0, W * T - profile_area(list(self.outline))) * H * MM3_PER_UNIT3[self.unit]

    def spec(self) -> dict:
        self.validate()
        from .extrude_profile_boss import datum_normal, ext_entities
        from .base import PlaneFrame
        f = factor(self.unit)
        W, H, T = self.size
        m = self.MARGIN
        y0 = 0.0                                                                # the Top datum through the part, cut both ways (measured: a plane BELOW
                                                                                # the part with through_all makes SolidWorks refuse the cut)
        box = [(-W / 2 - m, -m), (W / 2 + m, -m), (W / 2 + m, T + m), (-W / 2 - m, T + m)]
        loop = [{"kind": "line", "x1": a[0], "y1": a[1], "x2": b[0], "y2": b[1]} for a, b in zip(box, box[1:] + box[:1])]
        # the Top datum's sketch y runs along world -Z: heights z go in as -z, and an arc's turning sense flips with that mirror
        flip = lambda seg: {**seg, **{k: seg[k] * f for k in ("x1", "x2", "cx") if k in seg}, **{k: -seg[k] * f for k in ("y1", "y2", "cy") if k in seg},
                            **({"dir": -seg["dir"]} if "dir" in seg else {})}
        frame = PlaneFrame("top")
        ents = ext_entities([flip(seg) for seg in loop + list(self.outline)], frame, y0)
        half = (H / 2 + m) * f
        return {"operation": "cut", "plane": {"datum": "top", "offset": y0}, "entities": ents, "direction": datum_normal(frame),
                "end": "blind", "depth": half}

    def specs(self) -> list[dict]:
        """Two cuts from the mid plane, one each way: a cut's depth_back is not applied (measured: only half the width was cut)."""
        one = self.spec()
        return [one, {**one, "direction": [-v for v in one["direction"]]}]

    def expected(self) -> Expect:
        return Expect(bound_mm3=-self.removed_bound_mm3(), bodies=1)       # negative: material removed

    def expected_delta_mm3(self) -> float:
        return -self.removed_bound_mm3()

    async def apply(self, ctx: FeatureContext) -> FeatureResult:
        first, second = self.specs()
        res = FeatureResult(self.NAME)

        async def both():
            r = await ctx.sw.geometry("ext_sketch_extrude", first)
            return r if not r.ok else await ctx.sw.geometry("ext_sketch_extrude", second)

        res.verdicts.append(await ctx.verifier.run_step(self.label, both, self.expected()))
        return res
