"""Whole-part features: swept_tube, loft, sheet_metal_base. Exact volumes against hand calculations, refusals, and the only-item rule."""
import math

import fakes  # noqa: F401
from app.features.base import FeatureRequestError
from app.features.standalone import Loft, Section, SheetMetalBase, SweptTube, tube_path
from app.mcp_client.backend import validate_graph
from app.menu import parse_reply
from app.runner import prepare, validate_list


def refused(feature, words):
    try:
        feature.graph()
    except FeatureRequestError as ex:
        assert words in str(ex), str(ex)
        return
    assert False, "not refused"


def test_tube_path_lengths_and_volume_are_exact():
    assert math.isclose(tube_path([(0, 0), (100, 0), (100, 80)], [20])[1], 80 + math.pi / 2 * 20 + 60, rel_tol=1e-12)
    assert math.isclose(tube_path([(0, 0), (100, 0), (100, 60), (0, 60)], [25])[1], 75 + math.pi * 25 + 10 + 75, rel_tol=1e-12)
    a = math.radians(30)                                                     # a 60-degree turn: tangent length R tan(30)
    assert math.isclose(tube_path([(0, 0), (50, 0), (50 + 40 * math.cos(2 * a), 40 * math.sin(2 * a))], [10])[1],
                        50 + 40 - 2 * 10 * math.tan(a) + 10 * 2 * a, rel_tol=1e-12)
    t = SweptTube("mm", ((0, 0), (100, 0), (100, 80)), (20,), 16, 12)
    assert validate_graph(t.graph()) == []
    assert math.isclose(t.expected_delta_mm3(), math.pi / 4 * (16 ** 2 - 12 ** 2) * (140 + math.pi * 10), rel_tol=1e-12)
    assert SweptTube("mm", ((0, 0), (0, 50), (60, 50)), (15,), 10).graph()["nodes"][1]["ref"]["datum"] == "top"     # vertical first run


def test_tubes_that_cannot_be_made_are_refused():
    refused(SweptTube("mm", ((0, 0), (100, 0), (100, 80)), (7,), 16), "larger than the outer radius")
    refused(SweptTube("mm", ((0, 0), (30, 30), (60, 0)), (20,), 10), "horizontal or vertical")
    refused(SweptTube("mm", ((0, 0), (10, 0), (10, 80)), (20,), 10), "only 10 long")
    refused(SweptTube("mm", ((0, 0), (100, 0), (100, 50), (50, 50), (50, -30)), (6,), 10), "runs into itself")
    SweptTube("mm", ((0, 0), (100, 0), (100, 14), (0, 14)), (6,), 10).graph()                 # 4 mm clear between the runs: fine


def test_loft_volumes():
    lc = Loft("mm", Section("circle", diameter=60), Section("circle", diameter=30, cx=10), 50)       # oblique frustum: Cavalieri, same volume
    assert validate_graph(lc.graph()) == [] and math.isclose(lc.expected().delta_mm3, math.pi * 50 / 3 * (30 ** 2 + 30 * 15 + 15 ** 2), rel_tol=1e-12)
    lr = Loft("mm", Section("rectangle", width=80, height=60), Section("rectangle", width=40, height=30), 40)   # similar: pyramid frustum
    assert math.isclose(lr.expected().delta_mm3, 40 / 3 * (4800 + 1200 + math.sqrt(4800 * 1200)), rel_tol=1e-12)
    mixed = Loft("mm", Section("rectangle", width=60, height=60, corner_radius=8), Section("circle", diameter=40), 40)
    lo, hi = mixed.expected().range_mm3
    assert validate_graph(mixed.graph()) == [] and lo < math.pi * 400 * 40 and hi > (3600 - (4 - math.pi) * 64) * 40


def test_sheet_metal_blank_is_exact_and_flanges_are_a_range():
    flat = SheetMetalBase("mm", 120, 80, 2, 3)
    assert validate_graph(flat.graph()) == [] and flat.expected().delta_mm3 == 120 * 80 * 2
    tray = SheetMetalBase("mm", 120, 80, 2, 3, (("top", 30, 90), ("bottom", 30, 90)))
    lo, hi = tray.expected().range_mm3
    assert validate_graph(tray.graph()) == [] and lo > 120 * 80 * 2 and hi < 120 * 80 * 2 + 2 * 120 * 2 * 30 + 2 * 120 * 30
    nodes = tray.graph()["nodes"]
    assert [n["type"] for n in nodes] == ["sketch", "sheet_metal", "edge_flange", "edge_flange"]
    assert nodes[2]["edge"]["near"] == [0.0, 0.04, 0.0]                         # the middle of the top edge, in metres
    try:
        SheetMetalBase("mm", 120, 80, 2, 3, (("top", 4, 90),)).graph(); assert False
    except FeatureRequestError as ex:
        assert "longer than the bend radius" in str(ex)


def test_a_whole_part_item_must_be_the_only_item():
    tube = {"id": "I1", "feature": "swept_tube", "printed": "Ø20 x 2, R30", "derived": True, "derived_from": "inner 16 = 20 - 2 x 2",
            "params": {"path": [[0, 0], [100, 0], [100, 80]], "bend_radius": 30, "outer_diameter": 20, "inner_diameter": 16}}
    alone = {"list_version": "1", "unit": "mm", "projection": "third", "seen_dimensions": ["100", "80", "R30", "Ø20", "0"], "items": [tube]}
    assert validate_list(parse_reply(alone)) == [] and prepare(parse_reply(alone))[0].feature.NAME == "swept_tube"
    more = {**alone, "items": [tube, {"id": "I2", "feature": "hole", "params": {"positions": [{"x": 0, "y": 0}], "diameter": 5, "face": "front", "depth": 2}}]}
    assert any("only item" in r for r in validate_list(parse_reply(more)))
