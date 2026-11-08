"""Turned rectangles: in cuts (seal grooves, keyways) and as outlines. Area, corners and extents against plain geometry."""
import math

import fakes  # noqa: F401
from app.features.cut_feature import CutShape, rectangle_segments
from app.features.base import Position
from app.features.outline_helpers import rotated_half_extents, rounded_rectangle
from app.geometry import profile_area, profile_bounds
from app.menu import parse_reply
from app.runner import prepare, validate_list


def test_turned_rectangle_keeps_its_size_and_turns_about_its_centre():
    segs = rectangle_segments((10, 5), 3, 8, 120)
    assert math.isclose(profile_area(segs), 24, rel_tol=1e-12)
    xs = [s["x1"] for s in segs]
    ys = [s["y1"] for s in segs]
    assert math.isclose(sum(xs) / 4, 10) and math.isclose(sum(ys) / 4, 5)
    first = segs[0]                                                       # the width edge runs along 120 degrees
    assert math.isclose(math.degrees(math.atan2(first["y2"] - first["y1"], first["x2"] - first["x1"])) % 180, 120, abs_tol=1e-9)
    hx, hy = rotated_half_extents(3, 8, 90)
    assert math.isclose(hx, 4) and math.isclose(hy, 1.5)
    x0, x1, y0, y1 = profile_bounds(rounded_rectangle(0, 0, 3, 8, 0, 90))
    assert math.isclose(x1 - x0, 8) and math.isclose(y1 - y0, 3)


def test_rounded_turned_rectangle_area_is_unchanged_by_turning():
    a = profile_area(rounded_rectangle(0, 0, 22, 64, 6))                  # Wankel intake port 22 x 64, R6 corners
    b = profile_area(rounded_rectangle(5, -3, 22, 64, 6, 37))
    assert math.isclose(a, b, rel_tol=1e-12) and math.isclose(a, 22 * 64 - (4 - math.pi) * 36, rel_tol=1e-12)


def test_a_cut_with_three_grooves_at_120_degrees_validates_and_compiles():
    grooves = [{"kind": "rectangle", "width": 3, "height": 8, "angle_deg": a,
                "position": {"x": round(30 * math.cos(math.radians(a)), 6), "y": round(30 * math.sin(math.radians(a)), 6), "x_from": "centre", "y_from": "centre"}}
               for a in (0, 120, 240)]
    lst = {"list_version": "1", "unit": "mm", "projection": "third", "items": [
        {"id": "F1", "feature": "extrude_profile_boss", "params": {"profile": {"shape": "rectangle", "width": 100, "height": 100}, "depth": 20}},
        {"id": "F2", "feature": "cut", "derived": True, "derived_from": "groove centres on radius 30 at 0 / 120 / 240 degrees",
         "params": {"shapes": grooves, "face": "front", "depth": 5}}],
        "seen_dimensions": ["100", "20", "3", "8", "30", "5", "120", "240"]}
    bl = parse_reply(lst)
    assert validate_list(bl) == []
    cut = prepare(bl)[1].feature
    outlines = cut.shape_segments_m()                                     # what is sketched on the face, in metres
    assert len(outlines) == 3 and all(math.isclose(profile_area(o), 24e-6, rel_tol=1e-9) for o in outlines)
    centres = [(sum(s["x1"] for s in o) / 4, sum(s["y1"] for s in o) / 4) for o in outlines]
    assert all(math.isclose(math.hypot(*c), 0.030, rel_tol=1e-6) for c in centres)
    assert math.isclose(sum(s.area() for s in cut.shapes), 3 * 24)
    s = CutShape("rectangle", Position(0, 0), width=3, height=8, angle_deg=90)
    assert s.half_extent() == rotated_half_extents(3, 8, 90)
