"""Exact world bounding box of the active part, from analyze_model(faces) + analyze_model(edges).

Why: no analyze_model mode returns a bounding box (bodies/geometry/mass_properties/features/feature_map/faces/edges
were checked), and analyze_model(edges) reports an arc's `mid` as its chord midpoint, so edge points alone under-estimate
any side set by an arc. The `dist` that faces/edges queries return is the distance to a face's representative point,
not to the trimmed face, so it cannot test "is this point on the face".

Method (exact for prismatic arcs, i.e. cylinders whose axis is a world axis, which is everything built by extrusions):
- start from all edge end/mid points;
- for each cylindrical face, its arc end points are the edge end points that lie on its circle (the arc's two vertical
  edges are listed); the face's representative point says which side of the circle the arc is on; a cardinal extreme
  (centre +- r along an in-plane axis) is added ONLY if it lies inside that arc;
- a closed circle edge on the same circle means a full cylinder: all four extremes;
- a cylinder that does not fit this pattern is reported in `uncertain` and contributes nothing (never guessed).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

TOL = 5e-6          # metres: SolidWorks reports 6 decimals


@dataclass
class BBox:
    lo: tuple[float, float, float]
    hi: tuple[float, float, float]
    uncertain: list[str] = field(default_factory=list)

    def size(self) -> tuple[float, float, float]:
        return tuple(h - l for l, h in zip(self.lo, self.hi))  # type: ignore[return-value]


def _ang(a: float, b: float) -> float:
    return math.atan2(b, a)


def _ccw(frm: float, to: float) -> float:
    return (to - frm) % (2 * math.pi)


def exact_bbox(faces: list[dict], edges: list[dict]) -> BBox:
    pts = [p for e in edges for p in (e["start"], e["end"], e["mid"])]
    if not pts:
        raise ValueError("no edges: the part is empty")
    extra: list[tuple[float, float, float]] = []
    uncertain: list[str] = []
    for f in faces:
        if f.get("kind") != "cylinder":
            continue
        axis = f["axis"]
        k = max(range(3), key=lambda i: abs(axis[i]))
        if abs(abs(axis[k]) - 1) > 1e-6:
            uncertain.append(f"face {f['i']}: cylinder axis {axis} is not a world axis")
            continue
        a, b = [i for i in range(3) if i != k]
        cx, cy, r = f["origin"][a], f["origin"][b], f["radius"]
        on_circle = [p for e in edges for p in (e["start"], e["end"]) if abs(math.hypot(p[a] - cx, p[b] - cy) - r) <= TOL]
        full = any("length" in e and abs(e["length"] - 2 * math.pi * r) <= 1e-5 and abs(math.hypot(e["start"][a] - cx, e["start"][b] - cy) - r) <= TOL
                   for e in edges)
        cardinals = [0.0, math.pi / 2, math.pi, 3 * math.pi / 2]
        if full:
            wanted = cardinals
        else:
            angs = []
            for p in on_circle:
                t = _ang(p[a] - cx, p[b] - cy)
                if all(abs(_ccw(t, u)) > 1e-7 and abs(_ccw(u, t)) > 1e-7 for u in angs):
                    angs.append(t)
            if len(angs) < 2:
                uncertain.append(f"face {f['i']}: fewer than 2 edge end points on its circle (r={r * 1000:.3f} mm)")
                continue
            mid = _ang(f["point"][a] - cx, f["point"][b] - cy)
            before = min(angs, key=lambda t: _ccw(t, mid) if _ccw(t, mid) > 1e-9 else 10.0)    # nearest end point clockwise of mid
            after = min(angs, key=lambda t: _ccw(mid, t) if _ccw(mid, t) > 1e-9 else 10.0)     # nearest end point counter-clockwise
            span = _ccw(before, after)
            wanted = [c for c in cardinals if _ccw(before, c) < span - 1e-9]
        for t in wanted:
            p = [0.0, 0.0, 0.0]
            # `origin` is a point on the cylinder's AXIS and can lie far outside the part (measured: y=100 mm for a 60 mm part);
            # the representative `point` lies ON the face, so its axis coordinate is inside the part
            p[a], p[b], p[k] = cx + r * math.cos(t), cy + r * math.sin(t), f["point"][k]
            extra.append((p[0], p[1], p[2]))
    allp = pts + extra
    lo = tuple(min(p[i] for p in allp) for i in range(3))
    hi = tuple(max(p[i] for p in allp) for i in range(3))
    return BBox(lo, hi, uncertain)   # type: ignore[arg-type]
