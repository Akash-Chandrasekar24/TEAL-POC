"""Offline checks of the build-list format, the gate and "can every item be built?" (no SolidWorks): python tests/test_menu.py"""
import copy
import re

import fakes  # noqa: F401  (sets sys.path and a temporary cache dir)
from fakes import GOOD, PLATE, good

from app.menu import MENU, parse_reply
from app.menu.buildlist import BASE_FEATURES, Stopped
from app.runner import validate_list


def numbers(value) -> list[str]:
    """Every number in a params tree, as text (so a test list's seen_dimensions covers rule 12)."""
    return re.findall(r"-?\d+(?:\.\d+)?", str(value))


def single(item: dict, base: dict | None = PLATE) -> dict:
    items = [item] if base is None else [copy.deepcopy(base), item]
    return {"list_version": "1", "unit": "mm", "projection": "first", "items": items,
            "seen_dimensions": sorted({n for it in items for n in numbers(it["params"])})}


def test_the_good_list_is_buildable():
    assert validate_list(parse_reply(GOOD)) == []


def test_every_menu_example_is_valid_and_buildable():
    """The menu the model reads shows these examples: each one must pass the gate and prepare offline."""
    bad = {}
    for name, entry in MENU.items():
        item = {"id": "F9", "feature": name, "params": copy.deepcopy(entry.example)}
        turned = {"id": "F1", "feature": "revolve_base", "params": copy.deepcopy(MENU["revolve_base"].example)}     # features that build on a turned part
        lst = (single({**item, "id": "F1"}, base=None) if name in BASE_FEATURES
               else single(item, base=turned) if name in ("turned_axial", "cross_hole", "radial_blades", "turned_keyway") else single(item))
        reasons = validate_list(parse_reply(lst))
        if reasons:
            bad[name] = reasons
    assert not bad, bad


def test_gate_reasons():
    cases = {
        "projection is unknown": good(projection="unknown"),
        "uncertain items": good(items=[PLATE, {**GOOD["items"][1], "uncertain": True}]),
        "needs_human is not empty": good(needs_human=["the thread size is not readable"]),
        "unsupported items not accepted by a human": good(unsupported=[{"id": "U1", "text": "knurl"}]),
        "is not in seen_dimensions": good(seen_dimensions=["120", "80", "10"]),
        "the first item must be the base": good(items=[GOOD["items"][1], PLATE]),
        "is not on the menu": good(items=[PLATE, {"id": "F2", "feature": "shell", "params": {}}]),
        "duplicate item ids": good(items=[PLATE, {**GOOD["items"][1], "id": "F1"}]),
    }
    for expected, lst in cases.items():
        reasons = validate_list(parse_reply(lst))
        assert any(expected in r for r in reasons), (expected, reasons)


def test_accepted_gap_and_derived_number_pass():
    assert validate_list(parse_reply(good(unsupported=[{"id": "U1", "text": "knurl"}], accepted_gaps=["U1"]))) == []
    derived = copy.deepcopy(GOOD["items"][1]) | {"derived": True}
    assert validate_list(parse_reply(good(seen_dimensions=["120", "80", "10"], items=[PLATE, derived]))) == []


def test_unbuildable_numbers_are_found_offline():
    hole = copy.deepcopy(GOOD["items"][1])
    hole["params"]["diameter"] = 0                                    # impossible size: refused before SolidWorks
    assert validate_list(parse_reply(good(items=[PLATE, hole])))


def test_a_position_outside_the_part_is_refused_offline():
    """(Was a known gap found in P0: only the SolidWorks volume check caught it.) Every position, not only pattern ones, must lie on its face."""
    hole = copy.deepcopy(GOOD["items"][1])
    hole["params"]["positions"][0]["x"] = 500                         # far outside the 120 x 80 plate
    reasons = validate_list(parse_reply(good(seen_dimensions=GOOD["seen_dimensions"] + ["500"], items=[PLATE, hole])))
    assert any("outside the base outline" in r for r in reasons), reasons
    edge = copy.deepcopy(GOOD["items"][1])
    edge["params"]["positions"][0]["x"] = 120                         # exactly on the right edge: allowed (a notch)
    assert validate_list(parse_reply(good(seen_dimensions=GOOD["seen_dimensions"] + ["120"], items=[PLATE, edge]))) == []


def test_format_errors_and_stop():
    try:
        parse_reply({"list_version": "1", "unit": "mm", "projection": "first", "items": [], "surprise": 1})
        raise AssertionError("an unknown field must be refused")
    except Exception as ex:                                           # pydantic.ValidationError
        assert "surprise" in str(ex)
    assert isinstance(parse_reply({"status": "stopped", "reason": "an assembly"}), Stopped)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
