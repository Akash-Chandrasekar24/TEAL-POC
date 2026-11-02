"""PREDICTED BOUNDING BOX: where the material of a list can possibly be, worked out offline from the requests (nothing is read back from SolidWorks).

Each item that ADDS material reports the world box it can fill; the union is the predicted box. The measured box of the finished part must lie INSIDE it
(plus a small tolerance): cuts only remove material, so the measured box can be smaller, never bigger. A part that sticks out of the predicted box means
something was built where no item put it (a misplaced face reference, a wrong plane, a cut that became a boss).

Items that add material but have no box yet (the rib) are assumed to lie inside the other boxes (a rib stands on the plate and leans on a boss).
Items that remove material (hole, cut, profile_cut, fillet_chamfer_edges) add nothing.

This check is independent of `expected_final` (the human's numbers from the drawing). It starts WARN-ONLY (PREDICTED_BBOX_MODE=warn): it reports but does not
stop the job; set PREDICTED_BBOX_MODE=stop to make a violation stop the job at the review step.
"""
from __future__ import annotations

import math

from app.geometry import profile_bounds

from .base import PlaneFrame, factor

Box = tuple[tuple[float, float, float], tuple[float, float, float]]
SAMPLE_DEG = 0.25                                  # angular step of the bend's swept box (a 300 mm radius is then within 0.001 mm)


def _frame_box(frame: PlaneFrame, x0: float, x1: float, y0: float, y1: float, h0: float, h1: float) -> Box:
    """World box of the sketch rectangle (x0..x1, y0..y1) extruded along the datum normal from height h0 to h1."""
    corners = [frame.to_world(x, y, h) for x in (x0, x1) for y in (y0, y1) for h in (h0, h1)]
    return tuple(min(c[i] for c in corners) for i in range(3)), tuple(max(c[i] for c in corners) for i in range(3))     # type: ignore[return-value]


def _grow_range(feature) -> tuple[float, float]:
    grow = -1 if getattr(feature, "reversed", False) else 1
    a, b = feature.offset, feature.offset + grow * feature.depth
    return min(a, b), max(a, b)


def _bend_points(feature) -> list[tuple[float, float]]:
    """Boundary points of the bend's section (x, y) in the datum frame (drawing unit): the outer circle, or the outline."""
    if feature.is_round():
        d, _, cx, cy = feature._round_parts()
        return [(cx + d / 2 * math.cos(math.radians(a)), cy + d / 2 * math.sin(math.radians(a))) for a in range(360)]
    pts: list[tuple[float, float]] = []
    for s in feature.section:
        pts += [(s["x1"], s["y1"]), (s["x2"], s["y2"])]
        if s["kind"] == "arc":
            r = math.hypot(s["x1"] - s["cx"], s["y1"] - s["cy"])
            a1 = math.atan2(s["y1"] - s["cy"], s["x1"] - s["cx"])
            a2 = math.atan2(s["y2"] - s["cy"], s["x2"] - s["cx"])
            sweep = ((a2 - a1) * s["dir"]) % (2 * math.pi) or 2 * math.pi
            pts += [(s["cx"] + r * math.cos(a1 + s["dir"] * sweep * k / 90), s["cy"] + r * math.sin(a1 + s["dir"] * sweep * k / 90)) for k in range(91)]
    return pts


def feature_box(feature) -> Box | None:
    """The world box (drawing unit) a feature can add material in, or None when it adds none (or has no box yet)."""
    from .circle_boss import ExtrudeCircleBoss
    from .extrude_profile_boss import ExtrudeProfileBoss
    from .revolve_base import RevolveBase
    from .revolve_on_face import RevolveOnFace

    if isinstance(feature, ExtrudeProfileBoss):
        x0, x1, y0, y1 = profile_bounds(feature.profile)
        h0, h1 = _grow_range(feature)
        return _frame_box(PlaneFrame(feature.plane), x0, x1, y0, y1, h0, h1)
    if isinstance(feature, ExtrudeCircleBoss):
        f = factor(feature.unit)
        frame = PlaneFrame(feature.plane)
        h0, h1 = _grow_range(feature)
        boxes = []
        for c in feature.circles:
            sx, sy = (v / f for v in c.position.to_sketch_m(feature.unit, feature.outline_wh))
            r = c.diameter / 2
            boxes.append(_frame_box(frame, sx - r, sx + r, sy - r, sy + r, h0, h1))
        return union(boxes)
    if isinstance(feature, RevolveBase):
        f = factor(feature.unit)
        lo, hi = feature.bbox_m()
        return tuple(v / f for v in lo), tuple(v / f for v in hi)                  # type: ignore[return-value]
    if isinstance(feature, RevolveOnFace):
        ax, z0 = feature.axis_x, feature.plane_height
        pts = _bend_points(feature)
        steps = max(int(feature.angle_deg / SAMPLE_DEG), 1)
        xs, ys, zs = [], [], []
        for k in range(steps + 1):
            th = math.radians(feature.angle_deg * k / steps)
            c, s = math.cos(th), math.sin(th)
            for x, y in pts:
                u = x - ax
                xs.append(ax + u * c)
                ys.append(y)
                zs.append(z0 + feature.sweep * abs(u) * s)
        return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))
    return None


def union(boxes: list[Box | None]) -> Box | None:
    real = [b for b in boxes if b is not None]
    if not real:
        return None
    return tuple(min(b[0][i] for b in real) for i in range(3)), tuple(max(b[1][i] for b in real) for i in range(3))     # type: ignore[return-value]


def predicted_box_mm(features: list, unit: str) -> Box | None:
    """Union of the boxes of all items, in mm."""
    box = union([feature_box(f) for f in features])
    if box is None:
        return None
    k = factor(unit) * 1000
    return tuple(v * k for v in box[0]), tuple(v * k for v in box[1])              # type: ignore[return-value]


def predicted_check(final: dict, features: list, unit: str, tol_mm: float, mode: str) -> dict | None:
    """The measured box must lie inside the predicted box (+ tolerance). Returns {name, ok, detail, mode, predicted_mm} or None when there is nothing to compare."""
    measured = final.get("bbox_mm")
    predicted = predicted_box_mm(features, unit)
    if not measured or predicted is None:
        return None
    bad = []
    for i in range(3):
        if measured[0][i] < predicted[0][i] - tol_mm:
            bad.append(f"{'xyz'[i]} min {measured[0][i]:.3f} is below the predicted {predicted[0][i]:.3f}")
        if measured[1][i] > predicted[1][i] + tol_mm:
            bad.append(f"{'xyz'[i]} max {measured[1][i]:.3f} is above the predicted {predicted[1][i]:.3f}")
    shown = f"predicted x {predicted[0][0]:.3f}..{predicted[1][0]:.3f}, y {predicted[0][1]:.3f}..{predicted[1][1]:.3f}, z {predicted[0][2]:.3f}..{predicted[1][2]:.3f} mm"
    return {"name": "predicted bounding box", "ok": not bad, "mode": mode,
            "detail": ("measured box lies inside the " if not bad else "; ".join(bad) + " | ") + shown,
            "predicted_mm": [list(predicted[0]), list(predicted[1])]}
