"""General feature: a whole SHEET-METAL part from its flat pattern (brackets, enclosures, chassis, clips, covers, trays, panels).

What a sheet-metal drawing gives is what this takes: the flat pattern's outline, the holes / slots / cut-outs in it, the thickness, the
inside bend radius (K-factor if printed), every bend line with its direction (up / down), angle and radius (the bend table), and the face
that stays put. It is built the way the part is made: the flat blank with its cut-outs is one sketch -> a sheet-metal base flange, then each
bend is a sketched bend along its line.

Any number of bends, flanges on flanges, tabs and feet: bend lines form a tree around the fixed face, and the bends are made from the
OUTERMOST in, so every bend line still lies on the flat sheet (at its flat-pattern coordinates) when it is bent, and the side toward the fixed
face stays put. The bridge checks each bend's direction on the part's box and flips it when SolidWorks bent it the other way.

Check: bending does not change the volume: flat area x thickness (measured live: 9042.9 mm3 flat and bent). Holes and cut-outs are part of it.
Not here (each is a gap for the designer): forming tools (louvers, lances, embosses, extruded holes), hems, jogs, rolled / conical parts.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from app.geometry import profile_area
from app.verify import Expect
from .base import FeatureContext, FeatureRequestError, FeatureResult, Probe, check_closed, factor
from .ext_features import _mid
from .turned_ops import MM3_PER_UNIT3


def _cross(ax, ay, bx, by) -> float:
    return ax * by - ay * bx


def _segments_cross(p, q, a, b) -> bool:
    """The open segments p-q and a-b cross (touching at an end does not count)."""
    d1 = _cross(b[0] - a[0], b[1] - a[1], p[0] - a[0], p[1] - a[1])
    d2 = _cross(b[0] - a[0], b[1] - a[1], q[0] - a[0], q[1] - a[1])
    d3 = _cross(q[0] - p[0], q[1] - p[1], a[0] - p[0], a[1] - p[1])
    d4 = _cross(q[0] - p[0], q[1] - p[1], b[0] - p[0], b[1] - p[1])
    return d1 * d2 < -1e-12 and d3 * d4 < -1e-12


def _in_cutout(x: float, y: float, cut: list[dict], margin: float) -> bool:
    """Is the point inside (or within `margin` of) one cut-out? Circles exactly, a polygon / segments outline by its corner points."""
    if len(cut) == 1 and cut[0]["kind"] == "circle":
        c = cut[0]
        return math.hypot(x - c["cx"], y - c["cy"]) < c["diameter"] / 2 + margin
    pts = [(s["x1"], s["y1"]) for s in cut if s["kind"] in ("line", "arc")]
    if not pts:
        return False
    x0, x1 = min(p[0] for p in pts) - margin, max(p[0] for p in pts) + margin
    y0, y1 = min(p[1] for p in pts) - margin, max(p[1] for p in pts) + margin
    return x0 <= x <= x1 and y0 <= y <= y1


@dataclass(frozen=True)
class SheetMetalPart:
    """Everything in the drawing's unit, in the flat pattern's CENTRED frame (the outline's box centred on the origin)."""
    unit: str
    outline: tuple                     # closed segments (line / arc dicts)
    cutouts: tuple                     # each a closed list of segments (or one circle)
    thickness: float
    bend_radius: float
    bends: tuple                       # ((x1, y1, x2, y2), up, angle_deg, radius | None, tag)
    fixed: tuple                       # (x, y) on the face that stays put
    k_factor: float | None = None
    label: str = "sheet metal part"

    NAME = "sheet_metal_part"

    def validate(self) -> None:
        factor(self.unit)
        if not (self.thickness > 0 and self.bend_radius > 0):
            raise FeatureRequestError("sheet metal needs a positive thickness and inside bend radius")
        check_closed(list(self.outline))
        for cut in self.cutouts:
            if not (len(cut) == 1 and cut[0]["kind"] == "circle"):
                check_closed(list(cut))
        for (x1, y1, x2, y2), _, angle, radius, tag in self.bends:
            if math.hypot(x2 - x1, y2 - y1) < 1e-9:
                raise FeatureRequestError(f"bend {tag or '?'}: its line has no length")
            if not 0 < angle <= 180:
                raise FeatureRequestError(f"bend {tag or '?'}: the angle must be between 0 and 180 degrees")

    def order(self) -> list[tuple]:
        """The bends from the outermost in, each with a point just on its FIXED side (toward the fixed face, on the sheet, clear of holes)."""
        fx, fy = self.fixed
        lines = [b[0] for b in self.bends]
        out = []
        for k, ((x1, y1, x2, y2), up, angle, radius, tag) in enumerate(self.bends):
            mid = ((x1 + x2) / 2, (y1 + y2) / 2)
            depth = sum(_segments_cross((fx, fy), mid, (l[0], l[1]), (l[2], l[3])) for j, l in enumerate(lines) if j != k)
            length = math.hypot(x2 - x1, y2 - y1)
            nx, ny = -(y2 - y1) / length, (x2 - x1) / length
            side = 1.0 if (fx - x1) * nx + (fy - y1) * ny >= 0 else -1.0     # the fixed point's side of this line
            eps = max(2 * self.thickness, 1.0)
            point = None
            for frac in (0.5, 0.35, 0.65, 0.2, 0.8):                          # a spot beside the line that is not inside a hole
                px, py = x1 + (x2 - x1) * frac + side * nx * eps, y1 + (y2 - y1) * frac + side * ny * eps
                if not any(_in_cutout(px, py, list(c), self.thickness) for c in self.cutouts):
                    point = (px, py)
                    break
            if point is None:
                raise FeatureRequestError(f"bend {tag or k + 1}: no spot of sheet beside its line on the fixed side (holes everywhere)")
            out.append((depth, k, (x1, y1, x2, y2), up, angle, radius, point, tag))
        return sorted(out, key=lambda o: (-o[0], o[1]))

    def flat_area(self) -> float:
        area = abs(profile_area(list(self.outline)))
        for cut in self.cutouts:
            area -= abs(profile_area(list(cut)))
        if area <= 0:
            raise FeatureRequestError("the cut-outs take away the whole flat pattern")
        return area

    def spec(self) -> dict:
        self.validate()
        f = factor(self.unit)
        P = lambda x, y: [x * f, y * f, 0.0]

        def ents(segs):
            out = []
            for s in segs:
                if s["kind"] == "line":
                    out.append({"kind": "line", "p1": P(s["x1"], s["y1"]), "p2": P(s["x2"], s["y2"])})
                elif s["kind"] == "arc":
                    mx, my = _mid(s)
                    out.append({"kind": "arc", "center": P(s["cx"], s["cy"]), "p1": P(s["x1"], s["y1"]), "p2": P(s["x2"], s["y2"]), "mid": P(mx, my)})
                else:
                    out.append({"kind": "circle", "center": P(s["cx"], s["cy"]), "radius": s["diameter"] / 2 * f})
            return out
        entities = ents(self.outline) + [e for cut in self.cutouts for e in ents(cut)]
        bends = [{"p1": P(l[0], l[1]), "p2": P(l[2], l[3]), "fixed": [pt[0] * f, pt[1] * f], "angle": math.radians(angle),
                  "radius": (radius or self.bend_radius) * f, "up": bool(up), "tag": tag or ""} for _, _, l, up, angle, radius, pt, tag in self.order()]
        spec = {"thickness": self.thickness * f, "radius": self.bend_radius * f, "entities": entities, "bends": bends}
        if self.k_factor is not None:
            spec["k_factor"] = self.k_factor
        return spec

    def expected(self) -> Expect:
        v = self.flat_area() * self.thickness * MM3_PER_UNIT3[self.unit]
        # bending keeps the volume (K = 0.5 exactly; another K-factor shifts it a little): +-2 %
        return Expect(range_mm3=(v * 0.98, v * 1.02), bodies=1)

    def expected_delta_mm3(self) -> float:
        return self.flat_area() * self.thickness * MM3_PER_UNIT3[self.unit]

    async def apply(self, ctx: FeatureContext) -> FeatureResult:
        spec = self.spec()
        res = FeatureResult(self.NAME)
        got = {}

        async def action():
            got["r"] = await ctx.sw.geometry("ext_sheet_metal", spec)
            return got["r"]
        res.verdicts.append(await ctx.verifier.run_step(self.label, action, self.expected()))
        try:                                                      # a bend SolidWorks refused is left out (the rest is built): flagged here
            skipped = got["r"].json().get("skipped", []) if got.get("r") is not None and got["r"].ok else []
        except Exception:                                         # noqa: BLE001 - only the report
            skipped = []
        for b in skipped:
            res.probes.append(Probe(f"bend {b.get('tag') or b.get('n')}", False, f"not bent: {str(b.get('why'))[:300]}"))
        return res
