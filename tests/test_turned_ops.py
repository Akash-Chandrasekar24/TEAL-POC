"""Features after a turned base: eccentric / coaxial round sections along the axis, and cross holes. Volumes against closed forms."""
import json
import math
from pathlib import Path

import fakes  # noqa: F401
from app.features.base import FeatureRequestError
from app.features.turned_ops import CrossHole, TurnedAxial, axial_overlap, cross_overlap, section_intervals
from app.menu import parse_reply
from app.mcp_client.backend import validate_graph
from app.runner import prepare, validate_list
from app.runner.runner import RequestProblem

ROOT = Path(__file__).resolve().parents[1]


def rect(length, r, bore=0.0):
    """Half-section of a plain shaft (or a tube with a bore) from u = 0 to `length`."""
    pts = [(0, bore), (length, bore), (length, r), (0, r)]
    return [{"kind": "line", "x1": a[0], "y1": a[1], "x2": b[0], "y2": b[1]} for a, b in zip(pts, pts[1:] + pts[:1])]


def test_material_intervals_see_bores_and_steps():
    assert section_intervals(rect(40, 25), 10) == [(0.0, 25.0)]
    assert section_intervals(rect(40, 25, bore=10), 10) == [(10.0, 25.0)]
    assert section_intervals(rect(40, 25), 50) == []


def test_volumes_match_closed_forms():
    assert math.isclose(axial_overlap(rect(40, 25), 10, 0, 10, 30), math.pi * 100 * 20, rel_tol=1e-9)        # coaxial cylinder inside the shaft
    assert math.isclose(cross_overlap(rect(200, 25), 25, 100), 16 / 3 * 25 ** 3, rel_tol=1e-5)               # Steinmetz bicylinder
    assert math.isclose(cross_overlap(rect(40, 25), 0.5, 20), math.pi * 0.25 * 50, rel_tol=1e-3)             # thin hole: area x diameter
    tube = cross_overlap(rect(200, 25, bore=10), 4, 100)                                                     # a tube removes less than a solid shaft
    assert math.isclose(tube, cross_overlap(rect(200, 25), 4, 100) - cross_overlap(rect(200, 10), 4, 100), rel_tol=1e-9)


def test_eccentric_lobe_adds_lobe_minus_what_was_already_there():
    lobe = TurnedAxial("mm", "horizontal", rect(100, 20), "boss", 64, 30, 70, offset_in_view=15)
    inside = axial_overlap(rect(100, 20), 32, 15, 30, 70)
    assert math.isclose(lobe.expected_delta_mm3(), math.pi * 32 ** 2 * 40 - inside, rel_tol=1e-12) and 0 < inside < math.pi * 20 ** 2 * 40
    cut = TurnedAxial("mm", "horizontal", rect(100, 20), "cut", 10, 0, 15)                                   # an end-face bore 15 deep
    assert math.isclose(cut.expected_delta_mm3(), -math.pi * 25 * 15, rel_tol=1e-9)


def test_graphs_use_the_perpendicular_datum_and_pass_upstream_validation():
    for axis, datum in (("horizontal", "right"), ("vertical", "top")):
        g = TurnedAxial("mm", axis, rect(100, 20), "boss", 64, 30, 70, offset_in_view=15).graph()
        sk, ex = g["nodes"]
        assert sk["ref"]["datum"] == datum and math.isclose(sk["ref"]["offset"], -0.020) and ex["operation"] == "boss" and math.isclose(ex["depth"], 0.040)
        assert validate_graph(g) == []
    for axis, direction, datum in (("horizontal", "toward_viewer", "front"), ("horizontal", "in_view", "top"), ("vertical", "in_view", "right")):
        g = CrossHole("mm", axis, rect(100, 20), 8, 50, direction).graph()
        assert g["nodes"][0]["ref"]["datum"] == datum and g["nodes"][1]["end"] == "mid_plane" and validate_graph(g) == []
    cx = CrossHole("mm", "horizontal", rect(100, 20), 8, 20).graph()["nodes"][0]["profile"][0]
    assert math.isclose(cx["cx"], -0.030) and cx["cy"] == 0                                                   # station 20 on a 100 long shaft centred at 0


def test_impossible_requests_are_refused():
    for bad in (lambda: TurnedAxial("mm", "horizontal", rect(100, 20), "cut", 10, 120, 140).validate(),      # beyond the part: removes nothing
                lambda: TurnedAxial("mm", "horizontal", rect(100, 20), "boss", 10, 30, 40, offset_in_view=60).validate(),   # floating lobe
                lambda: CrossHole("mm", "horizontal", rect(100, 20), 12, 3).validate()):                     # runs past the end
        try:
            bad()
            assert False
        except FeatureRequestError:
            pass
    plate = {"list_version": "1", "unit": "mm", "projection": "third", "items": [
        {"id": "F1", "feature": "extrude_profile_boss", "params": {"profile": {"shape": "rectangle", "width": 40, "height": 20}, "depth": 5}},
        {"id": "F2", "feature": "cross_hole", "params": {"diameter": 4, "at_u": 10}}]}
    try:
        prepare(parse_reply(plate))
        assert False
    except RequestProblem as ex:
        assert "revolve_base" in str(ex)


def test_wankel_eccentric_shaft_list_is_buildable_offline():
    data = json.loads((ROOT / "eval/build_lists/wankel_eccentric_shaft.json").read_text(encoding="utf-8"))
    assert set(validate_list(parse_reply(data))) == {"unsupported items not accepted by a human: ['U1', 'U2']"}
    data["accepted_gaps"] = ["U1", "U2"]
    bl = parse_reply(data)
    assert validate_list(bl) == []
    items = prepare(bl)
    assert [p.item.feature for p in items] == ["revolve_base", "turned_axial"]
    assert math.isclose(items[1].feature.expected_delta_mm3(), math.pi * 40 * (32 ** 2 - 10 ** 2), rel_tol=1e-9)   # the core lies inside the lobe
    assert all(validate_graph(p.feature.graph()) == [] for p in items)
