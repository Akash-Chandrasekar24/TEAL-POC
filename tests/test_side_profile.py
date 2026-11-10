"""side_profile_cut: top outline x side outline. One cut on the Top datum: the base's side box and the side outline as two loops."""
import fakes  # noqa: F401
from app.features.base import FeatureRequestError
from app.menu import parse_reply
from app.runner import prepare

CLAMP = [[0, 0], [200, 0], [200, 32], [130, 32], [110, 60], [90, 60], [70, 32], [0, 32]]


def build(depth, points):
    return parse_reply({"list_version": "1", "unit": "mm", "projection": "third", "items": [
        {"id": "F1", "feature": "extrude_profile_boss", "params": {"profile": {"shape": "rectangle", "width": 200, "height": 80}, "depth": depth}},
        {"id": "F2", "feature": "side_profile_cut", "params": {"outline": {"shape": "polygon", "frame": "base_lower_left", "points": points}}}]})


def test_the_side_outline_becomes_heights_above_the_back_face_and_the_volume_bound_is_exact():
    f = prepare(build(60, CLAMP))[1].feature
    spec = f.spec()
    zs = [e[k][2] for e in spec["entities"] for k in ("p1", "p2") if k in e]
    assert spec["plane"] == {"datum": "top", "offset": 0.0} and spec["end"] == "blind" and spec["direction"] == [0.0, 1.0, 0.0]
    assert abs(spec["depth"] - 0.045) < 1e-12 and [s["direction"] for s in f.specs()] == [[0.0, 1.0, 0.0], [-0.0, -1.0, -0.0]]   # one cut each way
    assert abs(min(zs) + 0.005) < 1e-12 and abs(max(zs) - 0.065) < 1e-12                       # the box: 5 mm round the 0..60 side box
    side_area = 200 * 32 + (60 + 20) / 2 * 28                                                     # the 32 band + the raised middle (trapezoid)
    assert abs(f.removed_bound_mm3() - (200 * 60 - side_area) * 80) < 1e-6


def test_a_side_outline_taller_than_the_base_is_refused():
    try:
        prepare(build(40, CLAMP)); assert False
    except Exception as ex:
        assert "FULL height" in str(ex)
