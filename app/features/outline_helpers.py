"""Outline helpers (pure geometry, no SolidWorks): round the corners of a polygon, tangent lines to circles.

Plain numbers in the DRAWING's unit (mm or inch); nothing is converted here. Segments are
 lines {"kind": "line", x1, y1, x2, y2} and arcs {"kind": "arc", cx, cy, x1, y1, x2, y2, dir}
(dir +1 = counter-clockwise, -1 = clockwise, always from (x1, y1) to (x2, y2)). Consecutive segments share their end points
EXACTLY (the same float objects), so a result is a closed contour for the checklist (submit_feature_graph).
A request that cannot be built (radius too big, collinear corner, circle inside the other...) raises FeatureRequestError.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from .base import FeatureRequestError

Segment = dict


@dataclass(frozen=True)
class Circle:
    cx: float
    cy: float
    r: float

EPS = 1e-9
Point = tuple[float, float]


def _sub(a: Point, b: Point) -> Point:
    return a[0] - b[0], a[1] - b[1]


def _cross(a: Point, b: Point) -> float:
    return a[0] * b[1] - a[1] * b[0]


def _unit(v: Point) -> Point:
    n = math.hypot(*v)
    return v[0] / n, v[1] / n


def _line(a: Point, b: Point) -> Segment:
    return {"kind": "line", "x1": a[0], "y1": a[1], "x2": b[0], "y2": b[1]}


# ------------------------------------------------------------------ rounding the corners of a polygon

def _orient(a: Point, b: Point, c: Point) -> float:
    return _cross(_sub(b, a), _sub(c, a))


def _on_segment(a: Point, b: Point, c: Point) -> bool:
    """c lies on segment ab, given that a, b, c are collinear."""
    return min(a[0], b[0]) - EPS <= c[0] <= max(a[0], b[0]) + EPS and min(a[1], b[1]) - EPS <= c[1] <= max(a[1], b[1]) + EPS


def _segments_touch(a: Point, b: Point, c: Point, d: Point) -> bool:
    """True when segment ab and segment cd cross or touch (shared end points and overlaps included)."""
    o1, o2, o3, o4 = _orient(a, b, c), _orient(a, b, d), _orient(c, d, a), _orient(c, d, b)
    if ((o1 > EPS and o2 < -EPS) or (o1 < -EPS and o2 > EPS)) and ((o3 > EPS and o4 < -EPS) or (o3 < -EPS and o4 > EPS)):
        return True
    return ((abs(o1) <= EPS and _on_segment(a, b, c)) or (abs(o2) <= EPS and _on_segment(a, b, d))
            or (abs(o3) <= EPS and _on_segment(c, d, a)) or (abs(o4) <= EPS and _on_segment(c, d, b)))


def check_simple_polygon(pts: Sequence[Point]) -> None:
    """Raise FeatureRequestError when the polygon crosses or touches itself (bow-tie, figure 8, a vertex on another edge,
    an edge folded back over its neighbour). Neighbouring edges may only share their common corner."""
    n = len(pts)
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        for j in range(i + 1, n):
            c, d = pts[j], pts[(j + 1) % n]
            if j == i + 1 or (i == 0 and j == n - 1):               # neighbours: they share one corner, that is allowed
                shared = b if j == i + 1 else a
                other_i, other_j = (a, d) if j == i + 1 else (b, c)  # the far end points
                if abs(_orient(shared, other_i, other_j)) <= EPS and (other_i[0] - shared[0]) * (other_j[0] - shared[0]) + (other_i[1] - shared[1]) * (other_j[1] - shared[1]) > 0:
                    raise FeatureRequestError(f"edges {i} and {j} fold back over each other (a spike at point {j if j == i + 1 else 0})")
                continue
            if _segments_touch(a, b, c, d):
                raise FeatureRequestError(f"the polygon crosses or touches itself: edge {i} (point {i} to {(i + 1) % n}) and edge {j} "
                                          f"(point {j} to {(j + 1) % n}) meet")

def round_polygon_corners(points: Sequence[Point], radius: float | Sequence[float | None] = 0.0) -> list[Segment]:
    """Closed outline of the polygon `points` with its corners rounded.

    `radius`: one number for every corner, or one entry per corner (None or 0 = leave that corner sharp).
    Works for both orientations and for concave corners: the arc turns the way the polygon turns there
    (left turn = dir +1, right turn = dir -1). The result starts with the arc (or the first sharp corner) of the FIRST
    point and keeps the polygon's order. Two arcs may touch on an edge (their tangent lengths add up to the edge length);
    more than that is refused. A polygon that crosses or touches itself is refused.
    """
    n = len(points)
    if n < 3:
        raise FeatureRequestError("a polygon needs at least 3 points")
    radii = [radius] * n if not isinstance(radius, (list, tuple)) else list(radius)
    if len(radii) != n:
        raise FeatureRequestError(f"radius list has {len(radii)} entries for {n} corners")
    radii = [0.0 if r is None else float(r) for r in radii]
    if any(r < 0 for r in radii):
        raise FeatureRequestError("a corner radius cannot be negative")
    pts = [(float(x), float(y)) for x, y in points]
    for i in range(n):
        if math.hypot(*_sub(pts[(i + 1) % n], pts[i])) < EPS:
            raise FeatureRequestError(f"points {i} and {(i + 1) % n} are the same point (zero-length edge)")
    check_simple_polygon(pts)                       # first: a bow-tie can have area 0, and "it crosses itself" is the useful message
    if abs(sum(_cross(pts[i], pts[(i + 1) % n]) for i in range(n))) < EPS:
        raise FeatureRequestError("the polygon encloses no area")

    # per corner: tangent point on the incoming edge, tangent point on the outgoing edge, centre, direction, tangent length
    corners: list[tuple[Point, Point, Point | None, int, float]] = []
    for i in range(n):
        p, a, b = pts[i], pts[i - 1], pts[(i + 1) % n]
        r = radii[i]
        if r == 0:
            corners.append((p, p, None, 0, 0.0))
            continue
        u, v = _unit(_sub(a, p)), _unit(_sub(b, p))
        turn = _cross(_sub(p, a), _sub(b, p))
        cos_t = max(-1.0, min(1.0, u[0] * v[0] + u[1] * v[1]))
        theta = math.acos(cos_t)                       # angle between the two edges at the corner
        if abs(turn) < EPS * max(1.0, math.hypot(*_sub(p, a)) * math.hypot(*_sub(b, p))) or theta > math.pi - 1e-9:
            raise FeatureRequestError(f"corner {i} is straight (the edges are collinear): there is nothing to round with radius {r}")
        t = r / math.tan(theta / 2)                    # distance from the corner to each tangent point
        bis = _unit((u[0] + v[0], u[1] + v[1]))
        d = r / math.sin(theta / 2)
        corners.append(((p[0] + u[0] * t, p[1] + u[1] * t), (p[0] + v[0] * t, p[1] + v[1] * t),
                        (p[0] + bis[0] * d, p[1] + bis[1] * d), 1 if turn > 0 else -1, t))
    for i in range(n):
        length = math.hypot(*_sub(pts[(i + 1) % n], pts[i]))
        used = corners[i][4] + corners[(i + 1) % n][4]
        if used > length + 1e-9:
            raise FeatureRequestError(f"the corner radii at points {i} and {(i + 1) % n} need {used:.4f} of the edge between them, "
                                      f"which is only {length:.4f} long: use smaller radii")

    out: list[Segment] = []
    for i in range(n):
        t_in, t_out, centre, direction, _ = corners[i]
        if centre is not None:
            out.append({"kind": "arc", "cx": centre[0], "cy": centre[1], "x1": t_in[0], "y1": t_in[1],
                        "x2": t_out[0], "y2": t_out[1], "dir": direction})
        nxt_in = corners[(i + 1) % n][0]
        if math.hypot(*_sub(nxt_in, t_out)) > EPS:      # a zero-length line (two arcs meeting) is left out
            out.append(_line(t_out, nxt_in))
        elif nxt_in != t_out:                           # same point up to rounding: share the exact end point
            if out and out[-1]["kind"] == "arc":
                out[-1]["x2"], out[-1]["y2"] = nxt_in
    return out


def rounded_rectangle(cx: float, cy: float, width: float, height: float, radius: float, angle_deg: float = 0.0) -> list[Segment]:
    """Rectangle centred on (cx, cy), counter-clockwise, all four corners rounded with `radius`, turned `angle_deg` counter-clockwise about its centre
    (`width` runs along the turned x direction)."""
    if width <= 0 or height <= 0:
        raise FeatureRequestError("width and height must be positive")
    a = math.radians(angle_deg)
    c, s = math.cos(a), math.sin(a)
    corners = [(-width / 2, -height / 2), (width / 2, -height / 2), (width / 2, height / 2), (-width / 2, height / 2)]
    return round_polygon_corners([(cx + x * c - y * s, cy + x * s + y * c) for x, y in corners], radius)


def rotated_half_extents(width: float, height: float, angle_deg: float) -> tuple[float, float]:
    """Half the axis-aligned box of a width x height rectangle turned by angle_deg."""
    a = math.radians(angle_deg)
    return (abs(math.cos(a)) * width + abs(math.sin(a)) * height) / 2, (abs(math.sin(a)) * width + abs(math.cos(a)) * height) / 2


# ------------------------------------------------------------------ tangent lines
def tangent_point_to_circle(point: Point, circle: Circle, side: str) -> Point:
    """Point where the tangent line from `point` touches `circle`. side 'left' / 'right': the touching point lies to the
    left (counter-clockwise side) or right of the ray from `point` through the circle's centre."""
    if side not in ("left", "right"):
        raise FeatureRequestError(f"side must be 'left' or 'right', got {side!r}")
    dx, dy = circle.cx - point[0], circle.cy - point[1]
    d = math.hypot(dx, dy)
    if d <= circle.r + EPS:
        raise FeatureRequestError("the point is on or inside the circle: there is no tangent line from it")
    alpha = math.asin(circle.r / d) * (1 if side == "left" else -1)
    length = math.sqrt(d * d - circle.r * circle.r)
    ang = math.atan2(dy, dx) + alpha
    return point[0] + length * math.cos(ang), point[1] + length * math.sin(ang)


def tangent_between_circles(c1: Circle, c2: Circle, kind: str, side: str) -> tuple[Point, Point]:
    """Tangent line between two circles: (touching point on c1, touching point on c2).

    kind 'outer' (both circles on the same side of the line) or 'inner' (the line crosses between them).
    side 'left' / 'right': which of the two lines, looking from c1 towards c2.
    """
    if kind not in ("outer", "inner") or side not in ("left", "right"):
        raise FeatureRequestError(f"kind must be outer/inner and side left/right, got {kind!r}/{side!r}")
    dx, dy = c2.cx - c1.cx, c2.cy - c1.cy
    d = math.hypot(dx, dy)
    need = abs(c1.r - c2.r) if kind == "outer" else c1.r + c2.r
    if d <= need + EPS:
        raise FeatureRequestError(f"no {kind} tangent: the circles are too close (centre distance {d:.4f}, needs more than {need:.4f})")
    cos_t = (c1.r - c2.r) / d if kind == "outer" else (c1.r + c2.r) / d
    theta = math.acos(cos_t) * (1 if side == "left" else -1)
    base = math.atan2(dy, dx)
    nx, ny = math.cos(base + theta), math.sin(base + theta)         # unit normal shared by the touching points
    sign = 1 if kind == "outer" else -1
    return (c1.cx + c1.r * nx, c1.cy + c1.r * ny), (c2.cx + sign * c2.r * nx, c2.cy + sign * c2.r * ny)
