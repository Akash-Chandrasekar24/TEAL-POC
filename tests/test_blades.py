"""radial_blades and turned_keyway: exact geometry against closed forms, refusals, and the water pump parts they unlock (built from the printed
sizes plus the designer's answers for what the sheet does not print)."""
import math

import fakes  # noqa: F401
import numpy as np
from app.features.base import FeatureRequestError
from app.features.blades import RadialBlades, TurnedKeyway, blade_outline, single_arc
from app.features.turned_ops import _strip
from app.geometry import profile_area
from app.mcp_client.backend import validate_graph
from app.menu import parse_reply
from app.runner import prepare, validate_list


def poly(pts):
    return [{"kind": "line", "x1": a[0], "y1": a[1], "x2": b[0], "y2": b[1]} for a, b in zip(pts, pts[1:] + pts[:1])]


def test_single_arc_blade_meets_both_diameters_at_the_printed_angles():
    for hand in (1, -1):
        cen, R, p1, p2, _ = single_arc(25, 70, 20, 25, hand)
        assert math.isclose(math.hypot(*p2), 70, abs_tol=1e-9) and math.isclose(R, (70 ** 2 - 25 ** 2) / (2 * (70 * math.cos(math.radians(25)) - 25 * math.cos(math.radians(20)))))
        for p, beta in ((p1, 20), (p2, 25)):
            radial = np.array(p) / math.hypot(*p)
            normal = (np.array(p) - cen) / R
            assert math.isclose(math.degrees(math.asin(abs(radial @ normal))), 90 - beta, abs_tol=1e-6)   # angle from the tangent = beta
        assert (p2[1] > 0) == (hand > 0)


def test_blade_areas_are_exact():
    assert math.isclose(profile_area(blade_outline(30, 80, 4, "straight", 0, 0, 1, 0)), (_strip(80, 2) - _strip(30, 2)) / 2, rel_tol=1e-12)
    loop = blade_outline(25, 70, 4, "arc", 20, 25, 1, 33)
    pts = []
    for s in loop:
        a1 = math.atan2(s["y1"] - s["cy"], s["x1"] - s["cx"]); a2 = math.atan2(s["y2"] - s["cy"], s["x2"] - s["cx"])
        sw = (a2 - a1) % (2 * math.pi) if s["dir"] > 0 else -((a1 - a2) % (2 * math.pi)); r = math.hypot(s["x1"] - s["cx"], s["y1"] - s["cy"])
        pts += [(s["cx"] + r * math.cos(a1 + sw * k / 4000), s["cy"] + r * math.sin(a1 + sw * k / 4000)) for k in range(4000)]
    P = np.array(pts)
    dense = 0.5 * abs(np.sum(P[:, 0] * np.roll(P[:, 1], -1) - np.roll(P[:, 0], -1) * P[:, 1]))
    assert math.isclose(profile_area(loop), dense, rel_tol=1e-6)
    radii = np.hypot(P[:, 0], P[:, 1])
    assert math.isclose(radii.min(), 25, abs_tol=1e-6) and math.isclose(radii.max(), 70, abs_tol=1e-6)     # sits on the hub, ends on the tip circle


def test_blades_are_refused_when_they_overlap_cut_material_or_float():
    hub = poly([(0, 0), (0, 70), (6, 70), (6, 25), (40, 25), (40, 0)])               # shroud Ø140 x 6, hub Ø50
    ok = dict(unit="mm", axis="horizontal", profile=hub, count=7, inner_diameter=50, outer_diameter=140, thickness=4, from_u=6, to_u=30,
              shape="arc", inlet_angle_deg=20, outlet_angle_deg=25, hand=1)
    assert validate_graph(RadialBlades(**ok).graph()) == []
    for bad, why in ((dict(count=40), "overlap"), (dict(from_u=2), "existing material"), (dict(inner_diameter=60, from_u=7), "separate body")):
        try:
            RadialBlades(**(ok | bad)).graph()
            assert False, bad
        except FeatureRequestError as ex:
            assert why in str(ex), (bad, str(ex))


def test_keyway_volume_matches_closed_form_and_brute_force():
    shaft = poly([(0, 0), (0, 25), (150, 25), (150, 0)])
    R, c = 25, 20.5
    square = TurnedKeyway("mm", "horizontal", shaft, 10, 4.5, 100, 138, "square")
    assert math.isclose(-square.expected_delta_mm3(), 38 * (_strip(R, 5) / 2 - c * 10), rel_tol=1e-9)
    assert validate_graph(square.graph()) == []
    round_ = TurnedKeyway("mm", "horizontal", shaft, 10, 4.5, 100, 138, "round")
    xs = np.linspace(100, 138, 3801); zs = np.linspace(-5, 5, 1001); X, Z = np.meshgrid(xs, zs)
    inslot = ((X >= 105) & (X <= 133)) | ((X - 105) ** 2 + Z ** 2 <= 25) | ((X - 133) ** 2 + Z ** 2 <= 25)
    H = np.clip(np.sqrt(np.clip(R * R - Z * Z, 0, None)) - c, 0, None) * inslot
    assert abs(-round_.expected_delta_mm3() - np.trapezoid(np.trapezoid(H, xs, axis=1), zs)) < 0.1
    try:
        TurnedKeyway("mm", "horizontal", poly([(0, 0), (0, 25), (50, 25), (50, 15), (150, 15), (150, 0)]), 10, 4.5, 40, 60, "round").graph()
        assert False
    except FeatureRequestError as ex:
        assert "ONE round section" in str(ex)


def build(items, unsupported=()):
    data = {"list_version": "1", "unit": "mm", "projection": "third", "items": items, "unsupported": list(unsupported),
            "accepted_gaps": [u["id"] for u in unsupported]}
    bl = parse_reply(data)
    assert validate_list(bl) == []
    return prepare(bl)


def test_pump_cooling_fan_and_shaft_keyway_build_with_the_designers_answers():
    # 08 COOLING FAN (sheet 2): hub Ø60 x 32 with a Ø28 H7 bore, blades out to Ø160 (printed). Count and thickness are NOT printed:
    # DESIGNER'S ANSWER used here: 12 straight blades, 3 thick.
    fan = build([{"id": "I1", "feature": "revolve_base", "printed": "Ø60 hub, Ø28 H7, 32", "derived": True, "derived_from": "radii = half the diameters",
                  "params": {"axis": "horizontal", "profile": {"shape": "polygon", "points": [[0, 14], [0, 30], [32, 30], [32, 14]]}}},
                 {"id": "I2", "feature": "radial_blades", "printed": "Ø160; 12 blades x 3 (designer)", "derived": True, "derived_from": "designer answer",
                  "params": {"count": 12, "inner_diameter": 60, "outer_diameter": 160, "thickness": 3, "from_u": 0, "to_u": 32, "shape": "straight"}}])
    assert validate_graph(fan[1].feature.graph()) == []
    assert math.isclose(fan[1].feature.expected_delta_mm3(), 12 * 32 * (_strip(80, 1.5) - _strip(30, 1.5)) / 2, rel_tol=1e-12)
    # 02 PUMP SHAFT: KEYWAY 10x4.5 on the Ø50 seat (u 100..138). Its length is NOT clear (the key is 40, the seat 38):
    # DESIGNER'S ANSWER used here: 32 long, centred on the seat, round ends.
    shaft = build([{"id": "I1", "feature": "revolve_base", "printed": "pump shaft", "derived": True, "derived_from": "see eval/build_lists/pump_shaft.json",
                    "params": {"axis": "horizontal", "profile": {"shape": "polygon", "points": [
                        [0, 0], [0, 10], [2, 12], [28, 12], [28, 15], [70, 15], [70, 17.5], [100, 17.5], [100, 25], [138, 25], [138, 17.5],
                        [180, 17.5], [180, 15], [210, 15], [210, 12.5], [250, 12.5], [250, 10], [278, 10], [280, 8], [280, 0]]}}},
                   {"id": "I2", "feature": "turned_keyway", "printed": "KEYWAY 10x4.5; 32 long (designer)", "derived": True, "derived_from": "designer answer",
                    "params": {"width": 10, "depth": 4.5, "from_u": 103, "to_u": 135, "ends": "round"}}])
    assert validate_graph(shaft[1].feature.graph()) == [] and shaft[1].feature.expected_delta_mm3() < 0
