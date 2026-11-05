"""Offline checks of the guards: the list hash, the repair diff, and what a model's fix suggestion may change: python tests/test_guards.py"""
import copy

import fakes  # noqa: F401
from fakes import GOOD

from app.agent.guards import apply_repair, check_repair, diff_params, list_hash


def suggestion(params, item_id="F2", **extra):
    return {"status": "suggestion", "item_id": item_id, "params": params, "reason": "bigger hole", **extra}


def changed_hole(**changes):
    p = copy.deepcopy(GOOD["items"][1]["params"])
    p.update(changes)
    return p


def test_list_hash_is_stable_and_sensitive():
    a = copy.deepcopy(GOOD)
    assert list_hash(a) == list_hash(copy.deepcopy(GOOD))
    a["items"][1]["params"]["diameter"] = 9
    assert list_hash(a) != list_hash(GOOD)


def test_diff_params_names_every_leaf():
    d = diff_params({"a": 1, "b": {"c": [1, 2]}}, {"a": 1, "b": {"c": [1, 3]}, "e": 5})
    assert d == [{"path": "b.c[1]", "old": 2, "new": 3}, {"path": "e", "old": None, "new": 5}]


def test_a_good_suggestion_passes_and_applies_only_to_that_item():
    ok, reason, params = check_repair(GOOD, "F2", suggestion(changed_hole(diameter=9)))
    assert ok and params["diameter"] == 9
    new = apply_repair(GOOD, "F2", params)
    assert new["items"][1]["params"]["diameter"] == 9 and new["items"][0] == GOOD["items"][0] and GOOD["items"][1]["params"]["diameter"] == 8


def test_guard_refusals():
    cases = [
        ("not a JSON object", "nope"),
        ("asks for a human", {"status": "needs_human", "reason": "unclear"}),
        ("only the failed item may change", suggestion(changed_hole(diameter=9), item_id="F1")),
        ("not allowed", suggestion(changed_hole(diameter=9), items=[])),
        ("do not fit hole", suggestion(changed_hole(diameter=-1))),
        ("changes nothing", suggestion(changed_hole())),
        ("only a human may change", suggestion(changed_hole(depth=5, floor_open=True))),
    ]
    for expected, reply in cases:
        ok, reason, _ = check_repair(GOOD, "F2", reply)
        assert not ok and expected in reason, (expected, reason)


def test_only_a_human_may_set_human_only_flags():
    blind_open = changed_hole(depth=5, floor_open=True)               # floor_open is allowed on a blind hole only
    ok, reason, _ = check_repair(GOOD, "F2", suggestion(blind_open), human=True)
    assert ok, reason
    ok, reason, _ = check_repair(GOOD, "F2", suggestion(blind_open), human=False)
    assert not ok and "only a human" in reason


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
