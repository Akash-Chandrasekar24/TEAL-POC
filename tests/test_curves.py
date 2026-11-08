"""Formula-defined outlines: involute gears, the Wankel housing trochoid and the rotor. Checked against the maths, not against themselves."""
import json
import math
from pathlib import Path

import numpy as np

import fakes  # noqa: F401
from app import curves
from app.geometry import profile_area, profile_bounds

ROOT = Path(__file__).resolve().parents[1]


def closed(segs):
    return all(math.isclose(a["x2"], b["x1"], abs_tol=1e-12) and math.isclose(a["y2"], b["y1"], abs_tol=1e-12) for a, b in zip(segs, segs[1:] + segs[:1]))


def involute_angle(r, teeth, module, alpha_deg=20.0):
    """Half angular thickness of an external tooth at radius r (standard, no shift)."""
    a = math.radians(alpha_deg)
    rb = module * teeth / 2 * math.cos(a)
    return math.pi / (2 * teeth) + curves.inv(a) - (curves.inv(math.acos(rb / r)) if r > rb else 0.0)


def test_external_gear_matches_standard_sizes_and_tooth_thickness():
    o = curves.spur_gear(42, 2, 20)                                      # the Wankel stationary gear
    segs = o.segments
    assert closed(segs) and o.tolerance == 0.005
    x0, x1, y0, y1 = profile_bounds(segs)
    assert math.isclose(x1 - x0, 88, abs_tol=1e-9)                        # tip Ø = m (z + 2), printed Ø88.00
    tips = [s for s in segs if s["kind"] == "arc" and math.isclose(math.hypot(s["x1"], s["y1"]), 44, abs_tol=1e-9)]
    roots = [s for s in segs if s["kind"] == "arc" and math.isclose(math.hypot(s["x1"], s["y1"]), 39.5, abs_tol=1e-9)]
    assert len(tips) == 42 and len(roots) == 42                           # one tip and one root arc per tooth; root Ø = m (z - 2.5) = 79
    # every flank chord stays within the stated tolerance of the true involute
    worst = 0.0
    for s in segs:
        if s["kind"] != "line":
            continue
        for f in np.linspace(0, 1, 7):
            px, py = s["x1"] + f * (s["x2"] - s["x1"]), s["y1"] + f * (s["y2"] - s["y1"])
            r = math.hypot(px, py)
            ang = math.atan2(py, px)
            k = round(ang / (2 * math.pi / 42))
            off = abs(ang - k * 2 * math.pi / 42)
            worst = max(worst, r * abs(off - involute_angle(r, 42, 2)))
    assert worst <= 0.0055, worst
    # tooth thickness on the pitch circle = pi m / 2
    assert math.isclose(2 * involute_angle(42.0, 42, 2) * 42.0, math.pi, rel_tol=1e-12)


def test_internal_gear_is_a_toothed_hole_with_the_printed_pitch():
    o = curves.spur_gear(84, 2, 20, internal=True)                        # the Wankel ring gear, printed pitch Ø168.00
    size = curves.gear_size(84, 2, 20, internal=True)
    assert (size.pitch, size.tip, size.root) == (168, 164, 173)
    x0, x1, y0, y1 = profile_bounds(o.segments)
    assert closed(o.segments) and math.isclose(x1 - x0, 173, abs_tol=1e-9)
    area = profile_area(o.segments)
    assert math.pi * 82 ** 2 < area < math.pi * 86.5 ** 2                 # between the tip and root circles
    assert math.isclose(area, math.pi * 84 ** 2, rel_tol=0.01)            # tooth = space on the pitch circle: about the pitch-circle area


def test_bad_gear_data_is_refused_not_guessed():
    for args in ((5, 2), (42, 0), (42, 2, 50)):
        try:
            curves.spur_gear(*args)
            assert False, args
        except curves.CurveError:
            pass


def test_epitrochoid_is_the_curve_and_has_the_right_axes():
    o = curves.epitrochoid(65, 15)
    segs = o.segments
    x0, x1, y0, y1 = profile_bounds(segs)
    assert closed(segs) and math.isclose(x1 - x0, 160, abs_tol=1e-9)    # major axis 2 (R + e)
    a = curves.epitrochoid_points(65, 15, n=200000)                       # the true curve, as very short segments
    b = np.roll(a, -1, axis=0)
    ab = b - a
    def to_curve(p):
        t = np.clip(((p - a) * ab).sum(1) / (ab * ab).sum(1), 0, 1)
        return np.min(np.hypot(*(a + ab * t[:, None] - p).T))
    for s in segs[::5]:                                                   # chord midpoints are within tolerance of the true curve
        assert to_curve(np.array([(s["x1"] + s["x2"]) / 2, (s["y1"] + s["y2"]) / 2])) <= 0.0051
    turned = curves.epitrochoid(65, 15, rotation_deg=90)
    tx0, tx1, ty0, ty1 = profile_bounds(turned.segments)
    assert math.isclose(ty1 - ty0, 160, abs_tol=1e-6)                     # major axis vertical
    try:
        curves.epitrochoid(30, 15)
        assert False
    except curves.CurveError:
        pass


def _inside(points, polygon):
    """Even-odd point-in-polygon for many points."""
    x, y = points[:, 0][:, None], points[:, 1][:, None]
    ax, ay = polygon[:, 0][None, :], polygon[:, 1][None, :]
    bx, by = np.roll(polygon[:, 0], -1)[None, :], np.roll(polygon[:, 1], -1)[None, :]
    crosses = ((ay > y) != (by > y)) & (x < (bx - ax) * (y - ay) / (by - ay + 1e-300) + ax)
    return crosses.sum(axis=1) % 2 == 1


def test_rotor_has_three_apexes_on_R_and_fits_the_housing_in_every_position():
    R, e = 65, 15
    o = curves.rotor_flank(R, e)
    pts = np.array([[s["x1"], s["y1"]] for s in o.segments])
    rad = np.hypot(*pts.T)
    assert closed(o.segments) and abs(rad.max() - R) < 0.01               # the apexes sit on the generating radius
    ang = np.degrees(np.arctan2(pts[:, 1], pts[:, 0]))
    apexes = sorted(round(a) % 360 for a, r in zip(ang, rad) if r > R - 0.01)
    assert {0, 120, 240} <= set(apexes)
    housing = curves.epitrochoid_points(R, e, n=20000)
    for phi in np.linspace(0, 2 * np.pi, 37):                             # rotor turns phi, eccentric turns 3 phi
        c, s = math.cos(phi), math.sin(phi)
        world = np.stack([c * pts[:, 0] - s * pts[:, 1] + e * math.cos(3 * phi), s * pts[:, 0] + c * pts[:, 1] + e * math.sin(3 * phi)], axis=1)
        shrunk = world * (1 - 0.0002)                                     # 13 micrometres of numerical margin at the apexes
        assert _inside(shrunk, housing).all(), phi                        # never cuts into the housing
        gap = min(np.min(np.hypot(*(housing - p).T)) for p in world[rad > R - 0.5])
        assert gap < 0.02, (phi, gap)                                     # and the apexes stay on it (seal contact)


def test_drawing_consistency_finds_the_wankel_sheet_conflict():
    issues = curves.wankel_consistency(65, 15, {"rotor_apex_distance": 120.0, "housing_major_axis": 160.0})
    assert len(issues) == 1 and "112.58" in issues[0] and "120" in issues[0]


def test_wankel_gear_build_lists_compile_against_the_upstream_ir_schema():
    from app.menu import parse_reply
    from app.mcp_client.backend import validate_graph
    from app.runner import prepare, validate_list
    for name in ("wankel_stationary_gear", "wankel_internal_gear"):
        data = json.loads((ROOT / "eval/build_lists" / f"{name}.json").read_text(encoding="utf-8"))
        assert validate_list(parse_reply(data)) == ["unsupported items not accepted by a human: ['U1']"]   # the root-fillet gap needs a human
        data["accepted_gaps"] = ["U1"]
        bl = parse_reply(data)
        assert validate_list(bl) == []
        graphs = [p.feature.graph() for p in prepare(bl) if callable(getattr(p.feature, "graph", None))]
        assert graphs and all(validate_graph(g) == [] for g in graphs)
