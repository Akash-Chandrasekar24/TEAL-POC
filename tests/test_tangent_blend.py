"""Blend arcs in a tangent chain: an arc of a printed radius whose centre follows from touching the circles before and after it (the
standard drafting construction: R150 / R350 joining the clamp ends of a triple clamp, fillets between a boss and an arm)."""
import math

from app.menu.tangent_chain import TangentChain, solve_chain


def chain(blend_r, turn, ends=30):
    return TangentChain.model_validate({"shape": "tangent_chain", "frame": "centre", "circles": [
        {"id": "A", "centre": {"x": -100, "y": 0}, "radius": ends, "turn": "ccw"},
        {"id": "B", "centre": {"x": 100, "y": 0}, "radius": ends, "turn": "ccw"},
        {"id": "T", "centre": {"blend": True}, "radius": blend_r, "turn": turn}]})


def blend_arc(segs, r):
    return next(s for s in segs if s["kind"] == "arc" and abs(math.hypot(s["x1"] - s["cx"], s["y1"] - s["cy"]) - r) < 1e-6)


def test_a_concave_blend_touches_both_ends_from_outside_and_makes_a_waist():
    segs = solve_chain(chain(150, "cw"))
    b = blend_arc(segs, 150)
    assert abs(math.dist((b["cx"], b["cy"]), (-100, 0)) - 180) < 1e-9 and abs(math.dist((b["cx"], b["cy"]), (100, 0)) - 180) < 1e-9
    assert b["cy"] - 150 < 30                                                    # dips below the straight tangent line (y = 30)
    for x, y in ((b["x1"], b["y1"]), (b["x2"], b["y2"])):                        # its ends lie on the end circles: the joins are smooth
        assert min(abs(math.dist((x, y), (-100, 0)) - 30), abs(math.dist((x, y), (100, 0)) - 30)) < 1e-9


def test_a_convex_blend_bulges_out_and_a_too_small_radius_is_refused():
    b = blend_arc(solve_chain(chain(150, "ccw")), 150)
    assert abs(math.dist((b["cx"], b["cy"]), (-100, 0)) - 120) < 1e-9 and b["cy"] + 150 > 30     # internal touch, above the tangent line
    try:
        chain(40, "ccw"); assert False                                            # refused when the outline is read
    except ValueError as ex:
        assert "cannot touch both" in str(ex)


def test_a_blend_arc_that_meets_its_neighbours_gives_no_zero_length_line():
    """User's triple clamp: an R350 blend between R35 and R33 circles left two zero-length lines; SolidWorks refused the base sketch."""
    import math
    from app.menu.tangent_chain import TangentChain, solve_chain
    chain = TangentChain.model_validate({"shape": "tangent_chain", "frame": "base_lower_left", "circles": [
        {"id": "A", "centre": {"x": 233, "y": 75}, "radius": 35, "turn": "ccw"}, {"id": "B", "centre": {"blend": True}, "radius": 350, "turn": "cw"},
        {"id": "C", "centre": {"x": 33, "y": 75}, "radius": 33, "turn": "ccw"}, {"id": "D", "centre": {"x": 133, "y": 35}, "radius": 35, "turn": "ccw"}]})
    segs = solve_chain(chain)
    assert all(math.hypot(s["x2"] - s["x1"], s["y2"] - s["y1"]) > 1e-4 for s in segs if s["kind"] == "line")
    for a, b in zip(segs, segs[1:] + segs[:1]):
        assert (a["x2"], a["y2"]) == (b["x1"], b["y1"])                                  # still closed, end points shared exactly
