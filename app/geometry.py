"""Plane geometry of closed outlines made of lines and arcs (pure maths, in whatever unit the numbers are in).

A segment is {"kind": "line", x1, y1, x2, y2} or {"kind": "arc", cx, cy, x1, y1, x2, y2, dir} (dir +1 = counter-clockwise, -1 = clockwise).
"""
from __future__ import annotations

import math


class CannotCompute(Exception):
    pass


# ------------------------------------------------------------------ profile geometry (extruded_profile)
def _arc_sweep(seg: dict) -> tuple[float, float]:
    """(start angle, signed sweep) of an arc segment. dir +1 = counter-clockwise."""
    a1 = math.atan2(seg["y1"] - seg["cy"], seg["x1"] - seg["cx"])
    a2 = math.atan2(seg["y2"] - seg["cy"], seg["x2"] - seg["cx"])
    if seg.get("dir", 1) >= 0:
        sweep = (a2 - a1) % (2 * math.pi)
        sweep = sweep or 2 * math.pi
    else:
        sweep = -((a1 - a2) % (2 * math.pi) or 2 * math.pi)
    return a1, sweep


def circles_area(profile: list[dict]) -> float:
    """Area of a profile that is made of circles: ONE circle, or a RING (two concentric circles: outer minus inner)."""
    d = sorted((s["diameter"] for s in profile), reverse=True)
    return math.pi / 4 * (d[0] ** 2 - (d[1] ** 2 if len(d) > 1 else 0.0))


def profile_area(profile: list[dict]) -> float:
    """Area enclosed by a closed outline of lines and arcs (Green's theorem, exact for arcs), or by a circle / ring (circle entries)."""
    if profile and all(s.get("kind") == "circle" for s in profile):
        return circles_area(profile)
    twice = 0.0
    for s in profile:
        if s["kind"] == "line":
            twice += s["x1"] * s["y2"] - s["x2"] * s["y1"]
        elif s["kind"] == "arc":
            r = math.hypot(s["x1"] - s["cx"], s["y1"] - s["cy"])
            a1, sweep = _arc_sweep(s)
            a2 = a1 + sweep
            twice += (r * r * sweep + s["cx"] * r * (math.sin(a2) - math.sin(a1))
                      - s["cy"] * r * (math.cos(a2) - math.cos(a1)))
        else:
            raise CannotCompute(f"profile segment kind {s['kind']!r}")
    return abs(twice) / 2.0


def _circle_bounds(profile: list[dict]) -> tuple[float, float, float, float]:
    """Outer bounds of circle entries (the OUTER circle of a ring decides)."""
    x0 = min(s["cx"] - s["diameter"] / 2 for s in profile)
    x1 = max(s["cx"] + s["diameter"] / 2 for s in profile)
    y0 = min(s["cy"] - s["diameter"] / 2 for s in profile)
    y1 = max(s["cy"] + s["diameter"] / 2 for s in profile)
    return x0, x1, y0, y1


def profile_vertex_bounds(profile: list[dict]) -> tuple[float, float, float, float]:
    """Bounds from segment END points only (what analyze_model(edges) can see: it lists no partial-arc edges)."""
    if profile and all(s.get("kind") == "circle" for s in profile):
        return _circle_bounds(profile)
    xs = [v for s in profile for v in (s["x1"], s["x2"])]
    ys = [v for s in profile for v in (s["y1"], s["y2"])]
    return min(xs), max(xs), min(ys), max(ys)


def profile_bounds(profile: list[dict]) -> tuple[float, float, float, float]:
    if profile and all(s.get("kind") == "circle" for s in profile):
        return _circle_bounds(profile)
    xs, ys = [], []
    for s in profile:
        xs += [s["x1"], s["x2"]]
        ys += [s["y1"], s["y2"]]
        if s["kind"] == "arc":
            r = math.hypot(s["x1"] - s["cx"], s["y1"] - s["cy"])
            a1, sweep = _arc_sweep(s)
            for k in range(4):                       # cardinal points inside the sweep
                ang = k * math.pi / 2
                d = (ang - a1) % (2 * math.pi) if sweep > 0 else -((a1 - ang) % (2 * math.pi))
                if abs(d) < abs(sweep) - 1e-12:
                    xs.append(s["cx"] + r * math.cos(ang))
                    ys.append(s["cy"] + r * math.sin(ang))
    return min(xs), max(xs), min(ys), max(ys)


def outline_crossing(profile: list[dict], pieces: int = 32):
    """(i, j, (x, y)): the first place where segment i and a later segment j of a closed outline of lines and arcs CROSS (meeting at a
    shared end point is not a crossing), or None. Arcs are followed as `pieces` chords. SolidWorks refuses a crossing outline with no
    reason ("Feature creation returned null"): a connecting rod's tangent line ran on through the big end's shoulder."""
    paths = []
    for s in profile:
        if s["kind"] == "arc":
            r = math.hypot(s["x1"] - s["cx"], s["y1"] - s["cy"])
            a1, sweep = _arc_sweep(s)
            pts = [(s["cx"] + r * math.cos(a1 + sweep * k / pieces), s["cy"] + r * math.sin(a1 + sweep * k / pieces)) for k in range(pieces + 1)]
            pts[0], pts[-1] = (s["x1"], s["y1"]), (s["x2"], s["y2"])
        else:
            pts = [(s["x1"], s["y1"]), (s["x2"], s["y2"])]
        paths.append(pts)
    eps = 1e-6
    for i in range(len(paths)):
        for j in range(i + 1, len(paths)):
            for (ax, ay), (bx, by) in zip(paths[i], paths[i][1:]):
                for (cx, cy), (dx, dy) in zip(paths[j], paths[j][1:]):
                    den = (bx - ax) * (dy - cy) - (by - ay) * (dx - cx)
                    if abs(den) < 1e-12:
                        continue
                    t = ((cx - ax) * (dy - cy) - (cy - ay) * (dx - cx)) / den
                    u = ((cx - ax) * (by - ay) - (cy - ay) * (bx - ax)) / den
                    if eps < t < 1 - eps and eps < u < 1 - eps:
                        return i, j, (ax + t * (bx - ax), ay + t * (by - ay))
    return None
