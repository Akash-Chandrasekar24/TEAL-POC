"""swept_passage, shell and angled_boss: geometry and requests checked offline with a recording SolidWorks stand-in.

The C# side (extensions/solidworks/GeometryExtensions.cs) is compiled by scripts/build_execution_layer.py; these tests check that what we SEND
is right: model-space coordinates, planes square to the path, the tilted plane's normal along the boss axis, and the exact expected volumes."""
import asyncio
import json
import math

import fakes  # noqa: F401
import numpy as np
from app.features.base import FeatureRequestError
from app.features.ext_features import AngledBoss, Shell, SweptPassage, spiral_path
from app.menu import parse_reply
from app.runner import prepare, validate_list


def arc_end_tangent(s, at_end):
    x, y = (s["x2"], s["y2"]) if at_end else (s["x1"], s["y1"])
    rx, ry = x - s["cx"], y - s["cy"]
    n = math.hypot(rx, ry)
    return (-ry * s["dir"] / n, rx * s["dir"] / n)


def test_spiral_is_a_smooth_chain_of_arcs_growing_outward():
    segs, length = spiral_path(0, 0, 60, 90, 360, 0, 1)
    assert len(segs) == 4 and math.isclose(math.hypot(segs[0]["x1"], segs[0]["y1"]), 60)
    radii = [math.hypot(s["x1"] - s["cx"], s["y1"] - s["cy"]) for s in segs]
    assert radii == sorted(radii) and math.isclose(radii[0], 60) and math.isclose(radii[-1], 90)
    for a, b in zip(segs, segs[1:]):                                        # each arc ends where the next starts, with the same tangent
        assert math.isclose(a["x2"], b["x1"], abs_tol=1e-9) and math.isclose(a["y2"], b["y1"], abs_tol=1e-9)
        ta, tb = arc_end_tangent(a, True), arc_end_tangent(b, False)
        assert math.isclose(ta[0], tb[0], abs_tol=1e-9) and math.isclose(ta[1], tb[1], abs_tol=1e-9)
    assert math.isclose(length, sum(r * math.pi / 2 for r in radii))


def test_swept_passage_request_and_bound():
    vol = SweptPassage("mm", "cut", 24, 40, "spiral", spiral=(0, 0, 60, 90, 360, 0, 1), size=(260, 260, 80))
    spec = vol.spec()
    start = spec["path"]["entities"][0]["p1"]
    assert spec["operation"] == "cut" and spec["path"]["plane"] == {"datum": "front", "offset": 0.04}
    assert start == [0.06, 0.0, 0.04] and spec["profile"]["plane"] == {"datum": "top", "offset": 0.0}   # starts going up: section on the Top datum
    assert spec["profile"]["entities"][0] == {"kind": "circle", "center": [0.06, 0.0, 0.04], "radius": 0.012}
    for e in spec["path"]["entities"]:                                      # the mid point really lies on the arc
        c, m, p = (np.array(e[k]) for k in ("center", "mid", "p1"))
        assert math.isclose(np.linalg.norm(m - c), np.linalg.norm(p - c), rel_tol=1e-9)
    assert math.isclose(vol.bound_mm3(), math.pi * 144 * spiral_path(0, 0, 60, 90, 360, 0, 1)[1])
    gallery = SweptPassage("mm", "cut", 8, 5, "polyline", points=((-40, 0), (20, 0), (20, 25)), bend_radii=(10,), size=(120, 80, 10))
    assert gallery.spec()["profile"]["plane"] == {"datum": "right", "offset": -0.04}                     # starts along x: Right datum
    for bad, words in ((SweptPassage("mm", "cut", 30, 40, "spiral", spiral=(0, 0, 60, 90, 360, 0, 1), size=(120, 120, 80)), "leaves the part"),
                       (SweptPassage("mm", "cut", 24, 40, "spiral", spiral=(0, 0, 60, 90, 360, 45, 1), size=(260, 260, 80)), "start angle"),
                       (SweptPassage("mm", "cut", 24, 40, "spiral", spiral=(0, 0, 30, 34, 720, 0, 1), size=(260, 260, 80)), "runs into itself")):
        try:
            bad.spec(); assert False, words
        except FeatureRequestError as ex:
            assert words in str(ex), str(ex)


def test_widening_volute_is_one_sweep_per_quarter_turn():
    vol = SweptPassage("mm", "cut", 16, 40, "spiral", spiral=(0, 0, 60, 90, 360, 0, 1), size=(260, 260, 80), end_diameter=32)
    specs = vol.specs()
    assert np.allclose([s["profile"]["entities"][0]["radius"] for s in specs], [0.009, 0.011, 0.013, 0.015])   # diameters 18 22 26 30 mid-step
    for s in specs:                                                          # each step starts square to x or y: its section on a datum
        (sx, sy, _), plane = s["profile"]["entities"][0]["center"], s["profile"]["plane"]
        assert plane in ({"datum": "top", "offset": sy}, {"datum": "right", "offset": sx}) and len(s["path"]["entities"]) == 1
    radii = [60, 70, 80, 90]
    assert math.isclose(vol.bound_mm3(), sum(math.pi / 4 * d * d * r * math.pi / 2 for d, r in zip((18, 22, 26, 30), radii)))
    sent = []

    class SW:
        async def geometry(self, tool, spec):
            sent.append(tool)
            return type("R", (), {"ok": True})()

    class Verifier:
        async def run_step(self, label, action, expect):
            await action()
            return type("V", (), {"ok": True})()

    res = asyncio.run(vol.apply(type("C", (), {"sw": SW(), "verifier": Verifier()})()))
    assert sent == ["ext_sweep"] * 4 and len(res.verdicts) == 4
    for bad in (dict(end_diameter=32, spiral=(0, 0, 60, 90, 90, 0, 1)), dict(end_diameter=32, spiral=(0, 0, 60, 90, 315, 0, 1))):
        try:
            SweptPassage(**(dict(unit="mm", operation="cut", diameter=16, plane_z=40, path_kind="spiral", size=(260, 260, 80)) | bad)).validate()
            assert False, bad
        except FeatureRequestError as ex:
            assert "quarter turns" in str(ex)


def test_shell_cavities_are_exact():
    box = Shell("mm", 3, ("front",), "box", (120, 80, 30))
    assert box.expected_delta_mm3() == -(114 * 74 * 27)
    assert box.spec()["faces"] == [[0.0, 0.0, 0.03]] and box.spec()["thickness"] == 0.003
    tube = Shell("mm", 3, ("front", "back"), "box", (120, 80, 30))
    assert tube.expected_delta_mm3() == -(114 * 74 * 30)
    cup = Shell("mm", 2, ("end",), "cylinder", (40, 25, "horizontal"))
    assert math.isclose(cup.expected_delta_mm3(), -math.pi * 23 ** 2 * 38) and cup.spec()["faces"] == [[0.02, 0.0, 0.025]]


def build(items):
    return prepare(parse_reply({"list_version": "1", "unit": "mm", "projection": "third", "items": items}))


PLAIN = {"id": "F1", "feature": "extrude_profile_boss", "params": {"profile": {"shape": "rectangle", "width": 120, "height": 80}, "depth": 30}}
ROUND = {"id": "F1", "feature": "extrude_profile_boss", "params": {"profile": {"shape": "rectangle", "width": 120, "height": 80, "corner_radius": 10}, "depth": 30}}


def test_shell_only_on_a_plain_base_and_only_second():
    assert build([PLAIN, {"id": "F2", "feature": "shell", "params": {"thickness": 3, "open_faces": ["front"]}}])[1].feature.base == "box"
    try:
        build([ROUND, {"id": "F2", "feature": "shell", "params": {"thickness": 3, "open_faces": ["front"]}}]); assert False
    except Exception as ex:
        assert "plain box" in str(ex)
    hole = {"id": "F2", "feature": "hole", "params": {"positions": [{"x": 0, "y": 0}], "diameter": 5, "face": "front", "depth": 2}}
    late = {"list_version": "1", "unit": "mm", "projection": "third", "seen_dimensions": ["120", "80", "30", "5", "2", "3", "0"],
            "items": [PLAIN, hole, {"id": "F3", "feature": "shell", "params": {"thickness": 3, "open_faces": ["front"]}}]}
    assert any("second item" in r for r in validate_list(parse_reply(late)))


def test_angled_boss_volume_above_the_face_is_pi_r2_h():
    """The tilted cylinder runs from depth_back below its start plane to h above it; above the face it must hold exactly pi r^2 h."""
    b = AngledBoss("mm", 10, -5, 20, 25, 30, "up", (120, 80, 30), 10, 15)
    a = np.array(b.axis()); r, h, g, T = 10, 25, b.back_depth(), 30
    e1 = np.array([1.0, 0, 0]); e2 = np.cross(a, e1)
    s = np.linspace(-g, h, 801); rho = np.sqrt((np.arange(200) + 0.5) / 200) * r; phi = np.linspace(0, 2 * math.pi, 361)[:-1]
    S, RHO, PHI = np.meshgrid(s, rho, phi, indexing="ij")
    z = T + S * a[2] + RHO * (np.cos(PHI) * e1[2] + np.sin(PHI) * e2[2])
    above = (z > T).mean() * math.pi * r * r * (h + g)
    assert abs(above - math.pi * r * r * h) / (math.pi * r * r * h) < 2e-3, above
    assert math.isclose(b.expected_delta_mm3(), math.pi * 100 * 25)
    assert b.hole_expected().delta_mm3 == -math.pi * 25 * 15
    ps = b.plane_spec()
    assert ps["normal"] == list(b.axis()) and ps["along"] == "x" and ps["point"] == [0.01, -0.005, 0.03]


def test_angled_boss_sends_plane_then_boss_then_hole_on_the_plane_s_real_side():
    sent = []

    class SW:
        async def geometry(self, tool, spec):
            sent.append((tool, spec))
            data = {"ok": True, "plane": "Plane7", "normal_sign": -1} if tool == "ext_angled_plane" else {"ok": True}
            return type("R", (), {"ok": True, "text": json.dumps(data), "json": lambda self: data})()

    class Verifier:
        async def run_step(self, label, action, expect):
            await action()
            return type("V", (), {"ok": True})()

    b = AngledBoss("mm", 0, 0, 20, 25, 20, "left", (120, 80, 30), 10, "through")
    res = asyncio.run(b.apply(type("C", (), {"sw": SW(), "verifier": Verifier()})()))
    assert [t for t, _ in sent] == ["ext_angled_plane", "ext_sketch_extrude", "ext_sketch_extrude"] and len(res.verdicts) == 2
    boss, hole = sent[1][1], sent[2][1]
    assert boss["plane"] == {"name": "Plane7"} and boss["direction"] == list(b.axis()) and boss["depth_back"] > 0
    assert hole["plane"] == {"name": "Plane7", "offset": -0.025} and hole["end"] == "through_all"      # the plane's normal came out reversed
    assert hole["direction"] == [-v for v in b.axis()]


def test_angled_boss_refusals():
    for kw, words in ((dict(height=2), "too short"), (dict(x=55), "past the edge"), (dict(size=(120, 80, 4)), "too thin"),
                      (dict(hole_diameter=25, hole_depth=5), "narrower")):
        args = dict(unit="mm", x=0, y=0, diameter=20, height=25, tilt_deg=30, lean="up", size=(120, 80, 30)) | kw
        try:
            AngledBoss(**args).validate(); assert False, kw
        except FeatureRequestError as ex:
            assert words in str(ex), (kw, str(ex))
