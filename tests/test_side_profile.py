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
    f = prepare(build(60, CLAMP))[0].feature.side
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


def test_the_volume_bound_is_negative_for_material_removed():
    from app.verify.step_verifier import Snapshot, judge
    f = prepare(build(60, CLAMP))[0].feature.side
    exp = f.expected()
    assert exp.bound_mm3 < 0
    before = Snapshot(doc="P", volume=1e-3, area=0, cog=(0, 0, 0), bodies=1, faces=6, edges=12, vertices=8)
    after = Snapshot(doc="P", volume=1e-3 - 300000e-9, area=0, cog=(0, 0, 0), bodies=1, faces=30, edges=84, vertices=56)
    assert judge("side", before, after, exp).ok                                      # a removal within the bound passes (it failed as wrong_sign)


def side_cut(params):
    return prepare(parse_reply({"list_version": "1", "unit": "mm", "projection": "third", "items": [
        {"id": "F1", "feature": "extrude_profile_boss", "params": {"profile": {"shape": "rectangle", "width": 268, "height": 70}, "depth": 60}},
        {"id": "F2", "feature": "side_cut", "params": params}]}))[1].feature


def test_side_cut_through_is_two_cuts_and_a_counterbore_starts_at_its_floor():
    through = side_cut({"holes": [{"x": 30, "y": 16, "diameter": 10.5}], "depth": "through"}).specs()
    assert [s["direction"] for s in through] == [[0.0, 1.0, 0.0], [0.0, -1.0, 0.0]] and through[0]["plane"]["offset"] == 0.0
    c = through[0]["entities"][0]
    assert c["kind"] == "circle" and abs(c["center"][0] + 0.104) < 1e-9 and abs(c["center"][2] - 0.016) < 1e-9 and abs(c["radius"] - 0.00525) < 1e-12
    top = side_cut({"holes": [{"x": 30, "y": 16, "diameter": 22}], "depth": 28, "from_edge": "top_edge"}).specs()[0]
    bottom = side_cut({"holes": [{"x": 30, "y": 16, "diameter": 22}], "depth": 28, "from_edge": "bottom_edge"}).specs()[0]
    assert abs(top["plane"]["offset"] - 0.007) < 1e-12 and top["direction"] == [0.0, 1.0, 0.0]          # floor at 35 - 28, cut out to the face
    assert abs(bottom["plane"]["offset"] + 0.007) < 1e-12 and bottom["direction"] == [0.0, -1.0, 0.0]
    assert side_cut({"holes": [{"x": 30, "y": 16, "diameter": 22}], "depth": 28}).expected().bound_mm3 < 0



def test_through_cuts_after_side_profile_are_bound_only():
    """Ends 32, middle 60: a front hole crosses 32 or 60, not the base depth, so it is checked only as 'removes material'."""
    hole = {"id": "F3", "feature": "hole", "params": {"face": "front", "diameter": 20, "depth": "through", "material_length": 16,
                                                     "positions": [{"x": 0, "x_from": "centre", "y": 0, "y_from": "centre"}]}}
    shaped = build(60, CLAMP); shaped.items.append(type(shaped.items[0]).model_validate(hole))
    plain = build(60, CLAMP); plain.items[1] = type(plain.items[0]).model_validate(hole)
    assert prepare(shaped)[1].feature.bound_only and not prepare(plain)[1].feature.bound_only


def test_the_base_is_built_as_the_real_shape_never_a_full_height_block():
    """Base (full height) + side_profile_cut -> ONE step: grow the side outline across the width, then trim to the top outline."""
    steps = prepare(build(60, CLAMP))
    f = steps[0].feature
    grow, trim, trim_back = f.specs()
    assert len(steps) == 1 and f.NAME == "shaped_base"
    assert grow["operation"] == "boss" and grow["plane"]["datum"] == "top" and abs(grow["depth"] - 0.04) < 1e-12 and abs(grow["depth_back"] - 0.04) < 1e-12
    assert trim["operation"] == trim_back["operation"] == "cut" and trim["plane"] == {"datum": "front", "offset": 0.03}
    assert trim["direction"] == [-v for v in trim_back["direction"]]
    side_area = 200 * 32 + (60 + 20) / 2 * 28
    assert abs(f.expected().bound_mm3 - side_area * 80) < 1e-6 and f.expected().bodies == 1
