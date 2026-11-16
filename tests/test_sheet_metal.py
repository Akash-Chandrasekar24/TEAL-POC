"""General sheet metal from a flat pattern: any outline and cut-outs, any number of bends, bent from the OUTERMOST in so every bend line is
still on the flat sheet, the side toward the fixed face kept still. Offline: the order, the fixed-side points, the volume and the slots."""
import math

import fakes  # noqa: F401
from app.menu import parse_reply
from app.runner import validate_list
from app.runner.runner import prepare
from app.geometry import profile_area


def bracket(**over):
    params = {"outline": {"shape": "polygon", "points": [[0, 0], [110, 0], [110, 10], [120, 10], [120, 30], [110, 30], [110, 40], [0, 40]]},
              "cutouts": [{"shape": "circle", "x": 55, "y": 20, "diameter": 10}, {"shape": "slot", "x1": 40, "y1": 8, "x2": 70, "y2": 8, "width": 6}],
              "thickness": 2, "bend_radius": 2,
              "bends": [{"line": [[25, 0], [25, 40]], "direction": "up", "tag": "A"},
                        {"line": [[110, 10], [110, 30]], "direction": "down", "tag": "C"},       # a tab on the right wall: a flange on a flange
                        {"line": [[85, 0], [85, 40]], "direction": "up", "tag": "B"}],
              "fixed_point": [55, 30], **over}
    nums = sorted({str(v) for v in [0, 110, 10, 120, 30, 40, 55, 20, 8, 70, 6, 2, 25, 85]})
    return parse_reply({"list_version": "1", "unit": "mm", "projection": "third", "seen_dimensions": nums,
                        "items": [{"id": "F1", "feature": "sheet_metal_part", "params": params}]})


def test_the_outermost_bend_is_made_first_and_each_keeps_the_fixed_side_still():
    bl = bracket()
    assert validate_list(bl) == []
    spec = prepare(bl)[0].feature.spec()
    xs = [round(b["p1"][0] * 1000, 1) for b in spec["bends"]]
    assert xs[0] == 50.0                                                 # the tab (x = 110 on the flat; the 120 wide pattern centred: -60) first
    assert sorted(xs[1:]) == [-35.0, 25.0]
    for b in spec["bends"]:                                              # the fixed spot lies on the fixed point's side of its line
        fx = b["fixed"][0] * 1000
        line_x = b["p1"][0] * 1000
        assert (fx - line_x) * (-5 - line_x) > 0                         # the fixed point (55, 30) is at x = -5 centred
    assert [b["up"] for b in spec["bends"]] == [False, True, True]


def test_the_expected_volume_is_the_flat_area_less_the_cut_outs_times_the_thickness():
    f = prepare(bracket())[0].feature
    slot = math.pi * 3 ** 2 + 30 * 6
    assert abs(f.expected_delta_mm3() - (110 * 40 + 10 * 20 - math.pi * 25 - slot) * 2) < 0.01
    assert abs(profile_area(list(f.cutouts[1])) - slot) < 1e-6           # the slot is a closed outline of 2 lines and 2 half circles


def test_a_fixed_spot_beside_a_bend_line_avoids_a_hole_on_it():
    bl = bracket(cutouts=[{"shape": "circle", "x": 29, "y": 20, "diameter": 4}])   # a hole right where the spot beside bend A would be
    spec = prepare(bl)[0].feature.spec()
    a = next(b for b in spec["bends"] if round(b["p1"][0] * 1000) == -35)
    hole = (29 - 60, 0)
    assert math.hypot(a["fixed"][0] * 1000 - hole[0], a["fixed"][1] * 1000 - hole[1]) > 2 + 2


def test_a_formed_part_unfolds_with_the_bend_deduction_and_its_outside_lengths():
    """Formed: a 60 x 40 base, two 25 walls up, a 12 tab bent outward off one wall (built live: 72 x 40 x 25 mm, as dimensioned)."""
    from app.features.sheet_formed import bend_deduction
    from app.menu import MENU
    ex = MENU["sheet_metal_formed"].example
    bl = parse_reply({"list_version": "1", "unit": "mm", "projection": "third", "items": [{"id": "F1", "feature": "sheet_metal_formed", "params": ex}],
                      "seen_dimensions": ["0", "60", "40", "30", "20", "10", "2", "25", "12"]})
    assert validate_list(bl) == []
    f = prepare(bl)[0].feature
    bd = bend_deduction(90, 2, 2, 0.5)
    assert abs(bd - 3.2876) < 1e-3
    xs = sorted({round(s["x1"], 3) for s in f.outline})
    width = xs[-1] - xs[0]
    assert abs(width - (60 + 2 * (25 - bd) + (12 + 2 - bd))) < 1e-6        # the tab bends the other way round: one thickness more
    assert [b[1] for b in f.bends] == [True, True, False]
