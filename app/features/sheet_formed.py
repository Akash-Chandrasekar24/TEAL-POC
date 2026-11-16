"""General feature: a SHEET-METAL part from its FORMED shape (what most sheet-metal drawings dimension): a base face with flanges on its edges,
flanges on flanges (walls, tabs, feet), holes on any face. It is unfolded here into its flat pattern and built as one (app.features.sheet_metal).

Unfolding, the standard sheet-metal way: every face is laid flat next to its parent along the bend line. A flange whose OUTSIDE length is L
(from the parent's outer surface to its tip, as the views dimension it) is L - BD long on the flat pattern beyond the bend line, with the bend
deduction  BD = 2 (R + t) tan(angle / 2) - angle (R + K t)  (outside setback twice, less the bend allowance). The bend line is the parent's
outer surface: the sketched bend keeps the flange's outer face on it (measured live: a 90 degree bend, R 2, t 2 gave 3.288 mm, this BD).

Frames: the base face's outline in the top view (outside dimensions). A flange's frame: x along its edge from edge point 1, y = the distance
from the parent's outer surface (0 at the bend, its length at the tip); a child flange's edge is given in its parent flange's frame.
"""
from __future__ import annotations

import math

from .base import FeatureRequestError

TOL = 1e-6


def bend_deduction(angle_deg: float, radius: float, thickness: float, k: float) -> float:
    a = math.radians(angle_deg)
    return 2 * (radius + thickness) * math.tan(a / 2) - a * (radius + k * thickness)


def _signed_area(segs: list[dict]) -> float:
    return sum(s["x1"] * s["y2"] - s["x2"] * s["y1"] for s in segs if s["kind"] == "line") / 2   # sign only (arcs ignored)


def _on_line(s: dict, p: tuple) -> float | None:
    """The parameter (0..1) of point p on the straight segment s, or None when p is not on it."""
    dx, dy = s["x2"] - s["x1"], s["y2"] - s["y1"]
    L2 = dx * dx + dy * dy
    if L2 < TOL:
        return None
    t = ((p[0] - s["x1"]) * dx + (p[1] - s["y1"]) * dy) / L2
    off = abs((p[0] - s["x1"]) * dy - (p[1] - s["y1"]) * dx) / math.sqrt(L2)
    return t if off < 1e-4 and -1e-6 <= t <= 1 + 1e-6 else None


def _line(a, b) -> dict:
    return {"kind": "line", "x1": a[0], "y1": a[1], "x2": b[0], "y2": b[1]}


class Unfolder:
    """Builds the flat pattern face by face: the outline is grown by splicing each flange's free sides in place of the stretch of its
    parent's edge it stands on (so every bend line lies across the sheet between the parent and its flange)."""

    def __init__(self, base: list[dict], thickness: float, radius: float, k: float):
        if any(s["kind"] not in ("line", "arc") for s in base):
            raise FeatureRequestError("the base face's outline is lines and arcs")
        self.outline = [dict(s) for s in base]
        self.ccw = _signed_area(self.outline) > 0                   # the interior is on the LEFT of the outline's direction
        self.t, self.R, self.k = thickness, radius, k
        self.cutouts: list[list[dict]] = []
        self.bends: list[tuple] = []

    def _splice(self, e1: tuple, e2: tuple, path: list[tuple]) -> tuple:
        """Replace the stretch e1..e2 of the outline's straight edge with path (e1 -> ... -> e2). Returns the outward normal there."""
        for i, s in enumerate(self.outline):
            if s["kind"] != "line":
                continue
            t1, t2 = _on_line(s, e1), _on_line(s, e2)
            if t1 is None or t2 is None:
                continue
            if t1 > t2:                                             # the outline runs the other way along this edge
                e1, e2, path, t1, t2 = e2, e1, path[::-1], t2, t1
            a, b = (s["x1"], s["y1"]), (s["x2"], s["y2"])
            new = ([_line(a, e1)] if t1 > 1e-9 else []) + [_line(p, q) for p, q in zip(path, path[1:])] + ([_line(e2, b)] if t2 < 1 - 1e-9 else [])
            self.outline[i:i + 1] = new
            return
        raise FeatureRequestError(f"no straight edge of its parent face runs through {tuple(round(v, 3) for v in e1)} and "
                                  f"{tuple(round(v, 3) for v in e2)}: a flange's edge must lie on its parent face's outline")

    def _outward(self, e1: tuple, e2: tuple) -> tuple:
        """The flat-pattern direction away from the parent face, across its edge e1-e2 (the outline is not yet spliced there)."""
        for s in self.outline:
            if s["kind"] == "line" and _on_line(s, e1) is not None and _on_line(s, e2) is not None:
                dx, dy = s["x2"] - s["x1"], s["y2"] - s["y1"]
                L = math.hypot(dx, dy)
                return (dy / L, -dx / L) if self.ccw else (-dy / L, dx / L)
        raise FeatureRequestError(f"no straight edge of its parent face runs through {tuple(round(v, 3) for v in e1)} and "
                                  f"{tuple(round(v, 3) for v in e2)}: a flange's edge must lie on its parent face's outline")

    def add(self, fl, to_flat, parent_up: bool | None = None) -> None:
        """One flange (and its own flanges). to_flat maps the PARENT face's frame to the flat pattern; parent_up: the parent's own bend."""
        e1, e2 = to_flat(*fl.edge[0]), to_flat(*fl.edge[1])
        w = math.dist(e1, e2)
        if w < TOL:
            raise FeatureRequestError(f"flange {fl.tag or '?'}: its edge has no length")
        u = ((e2[0] - e1[0]) / w, (e2[1] - e1[1]) / w)
        n = self._outward(e1, e2)
        radius = fl.radius or self.R
        bd = bend_deduction(fl.angle_deg, radius, self.t, self.k)
        # lengths are dimensioned from the parent's OUTER surface; a flange that bends the other way round from its parent has its bend's
        # outside on the parent's inner surface, one thickness further (measured live: a tab bent outward off a wall came out t short)
        if parent_up is not None and (fl.direction == "up") != parent_up:
            bd -= self.t
        if fl.length - bd <= 1e-6:
            raise FeatureRequestError(f"flange {fl.tag or '?'}: its length {fl.length:g} is shorter than its bend ({bd:.3f}): it cannot be formed")

        def mine(x: float, y: float, rigid: bool = True) -> tuple:
            yf = y - bd if (rigid or y > 1e-9) else 0.0
            return (e1[0] + x * u[0] + yf * n[0], e1[1] + x * u[1] + yf * n[1])
        pts = fl.outline or [[0, 0], [0, fl.length], [w, fl.length], [w, 0]]
        if not (any(abs(p[0]) < 1e-6 and abs(p[1]) < 1e-6 for p in pts) and any(abs(p[0] - w) < 1e-6 and abs(p[1]) < 1e-6 for p in pts)):
            raise FeatureRequestError(f"flange {fl.tag or '?'}: its outline must contain [0, 0] and [{w:g}, 0] (the bend)")
        i0 = next(i for i, p in enumerate(pts) if abs(p[0]) < 1e-6 and abs(p[1]) < 1e-6)
        ring = pts[i0:] + pts[:i0]                                  # starts at [0, 0]
        if abs(ring[1][0] - w) < 1e-6 and abs(ring[1][1]) < 1e-6:   # it went straight along the bend: go round the other way
            ring = [ring[0]] + ring[1:][::-1]
        iw = next(i for i, p in enumerate(ring) if abs(p[0] - w) < 1e-6 and abs(p[1]) < 1e-6)
        path = [mine(p[0], p[1], rigid=False) for p in ring[:iw + 1]]
        self._splice(e1, e2, path)
        self.bends.append(((e1[0], e1[1], e2[0], e2[1]), fl.direction == "up", fl.angle_deg, fl.radius, fl.tag))
        flip = (u[0] * n[1] - u[1] * n[0]) < 0                      # a mirrored frame turns arcs the other way
        for c in fl.cutouts:
            self.cutouts.append(_map_cutout(c, mine, flip))
        for child in fl.flanges:
            self.add(child, mine, fl.direction == "up")


def _map_cutout(c, to_flat, flip: bool) -> list[dict]:
    """A cut-out given in a flange's frame -> flat-pattern segments."""
    from app.menu import params as P
    if isinstance(c, P.FlatCircle):
        x, y = to_flat(c.x, c.y)
        return [{"kind": "circle", "cx": x, "cy": y, "diameter": c.diameter}]
    if isinstance(c, P.FlatSlot):
        from app.menu.registry import _slot
        (x1, y1), (x2, y2) = to_flat(c.x1, c.y1), to_flat(c.x2, c.y2)
        return _slot(x1, y1, x2, y2, c.width)
    if isinstance(c, P.Polygon):
        pts = [to_flat(*p) for p in c.points]
        return [_line(a, b) for a, b in zip(pts, pts[1:] + pts[:1])]
    out = []
    for s in (seg.model_dump() for seg in c.segments):
        t = dict(s)
        for kx, ky in (("x1", "y1"), ("x2", "y2"), ("cx", "cy")):
            if kx in t:
                t[kx], t[ky] = to_flat(t[kx], t[ky])
        if t["kind"] == "arc" and flip:
            t["dir"] = -t["dir"]
        out.append(t)
    return out
