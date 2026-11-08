"""Fillets / chamfers on edges picked by face AND direction, e.g. the corner edges through a plate's thickness."""
import itertools
import math

import fakes  # noqa: F401
from app.features.base import FeatureRequestError
from app.menu import parse_reply
from app.runner import prepare, validate_list

W, H, T = 0.100, 0.060, 0.020          # the plate in metres: x -50..50, y -30..30, z 0..20 (as SolidWorks reports it)


def box():
    x, y, z = (-W / 2, W / 2), (-H / 2, H / 2), (0.0, T)
    corners = list(itertools.product(x, y, z))
    edges = []
    for p, q in itertools.combinations(corners, 2):
        if sum(a != b for a, b in zip(p, q)) == 1:                               # 12 edges: differ in exactly one coordinate
            edges.append({"i": len(edges), "start": list(p), "end": list(q), "mid": [(a + b) / 2 for a, b in zip(p, q)]})
    faces = [{"planar": True, "normal": n, "point": pt, "area": ar} for n, pt, ar in (
        ([0, 0, 1], [0, 0, T], W * H), ([0, 0, -1], [0, 0, 0], W * H),
        ([0, 1, 0], [0, H / 2, T / 2], W * T), ([0, -1, 0], [0, -H / 2, T / 2], W * T),
        ([1, 0, 0], [W / 2, 0, T / 2], H * T), ([-1, 0, 0], [-W / 2, 0, T / 2], H * T))]
    return faces, edges


def edge_item(**params):
    return {"list_version": "1", "unit": "mm", "projection": "third", "seen_dimensions": ["100", "60", "20", "3", "2"], "items": [
        {"id": "F1", "feature": "extrude_profile_boss", "params": {"profile": {"shape": "rectangle", "width": 100, "height": 60}, "depth": 20}},
        {"id": "F2", "feature": "fillet_chamfer_edges", "params": params}]}


def feature(**params):
    bl = parse_reply(edge_item(**params))
    assert validate_list(bl) == [], validate_list(bl)
    return prepare(bl)[1].feature


def direction(e):
    return [round(abs(b - a) / max(1e-12, math.dist(e.start, e.end)), 6) for a, b in zip(e.start, e.end)]


def test_face_top_along_z_picks_only_the_two_corner_edges_through_the_thickness():
    faces, edges = box()
    sel = feature(kind="fillet", size=3, face="top", kinds=["line"], along="z", expect_count=2).select(faces, edges)
    assert len(sel.chosen) == 2 and all(direction(e) == [0, 0, 1] for e in sel.chosen)
    assert all(e.convexity == "convex" for e in sel.chosen)


def test_without_along_the_top_face_gives_all_four_of_its_edges():
    faces, edges = box()
    sel = feature(kind="chamfer", size=2, face="top", kinds=["line"]).select(faces, edges)
    assert len(sel.chosen) == 4


def test_four_vertical_corners_take_two_items_and_get_an_exact_volume():
    faces, edges = box()
    total = 0.0
    for face in ("top", "bottom"):
        f = feature(kind="fillet", size=3, face=face, kinds=["line"], along="z", expect_count=2)
        sel = f.select(faces, edges)
        (lo, hi), why = f.delta_range_mm3(sel)
        assert lo == hi, why
        total += lo
    assert math.isclose(total, -4 * (1 - math.pi / 4) * 3 ** 2 * 20, rel_tol=1e-9)          # (1 - pi/4) r^2 x L per convex straight edge


def test_a_wrong_count_stops_instead_of_rounding_other_edges():
    faces, edges = box()
    try:
        feature(kind="fillet", size=3, face="front", kinds=["line"], along="z", expect_count=1).select(faces, edges)
        assert False, "edges along z do not lie in the front face"
    except FeatureRequestError as ex:
        assert "no edge" in str(ex)
