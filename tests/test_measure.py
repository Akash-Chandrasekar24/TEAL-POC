"""Drawing vs model per step: the faces a step added give its real diameters and place; each drawing number gets its deviation."""
from types import SimpleNamespace

import fakes  # noqa: F401
from app.runner.measure import signature, step_measurement


def test_a_hole_is_compared_with_the_cylinder_it_made_and_placed_at_its_axis():
    item = SimpleNamespace(params={"diameter": 50, "depth": "through", "positions": [{"x": -100, "y": 0}]})
    faces = [{"kind": "cylinder", "origin": [-0.1, 0.0, 0.032], "axis": [0, 0, -1], "radius": 0.02502, "point": [-0.075, 0, 0.01], "area": 0.005},
             {"planar": True, "normal": [0, 0, 1], "point": [0, 0, 0.032], "area": 0.02}]
    m = step_measurement(item, "mm", faces, [{"name": "F2 hole", "expected_delta_mm3": -62831.85, "measured_delta_mm3": -62900.0}])
    assert m["anchor_mm"] == [-100.0, 0.0, 32.0]
    hole = next(c for c in m["checks"] if c["what"].startswith("Ø"))
    assert hole["drawing"] == 50 and hole["model"] == 50.04 and abs(hole["deviation_pct"] - 0.08) < 1e-9
    vol = next(c for c in m["checks"] if c["what"].startswith("volume removed"))
    assert vol["unit"] == "mm³" and abs(vol["deviation_pct"] - (62900 - 62831.85) / 62831.85 * 100) < 1e-3


def test_the_base_is_checked_against_the_parts_real_size_in_inches_too():
    item = SimpleNamespace(params={"depth": 0.5})
    m = step_measurement(item, "inch", [], [], bbox_mm=[[0, 0, 0], [101.6, 50.8, 12.7]], base_size=(4.0, 2.0, 0.5))
    assert [c["deviation_pct"] for c in m["checks"]] == [0.0, 0.0, 0.0] and m["anchor_mm"] == [50.8, 25.4, 6.35]


def test_a_face_is_recognised_again_after_later_steps():
    f = {"kind": "cylinder", "origin": [0.1, 0.0, 0.0], "axis": [0, 0, 1], "radius": 0.005, "point": [0.105, 0, 0], "area": 1e-4}
    assert signature(f) == signature(dict(f)) and signature(f) != signature({**f, "radius": 0.006})
