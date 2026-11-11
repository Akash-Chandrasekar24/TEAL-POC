"""The tolerance each dimension is judged by: printed first, then the drawing's general tolerance, then ISO 2768-m (assumed)."""
from app.drawing.tolerance import general_class, iso2768, judge, printed_band


def chk(drawing, model):
    return {"what": "x", "drawing": drawing, "model": model, "unit": "mm", "deviation_pct": None}


def test_printed_plus_minus_and_asymmetric_and_fits():
    assert printed_band("Ø50 ±0.1", 50) == (-0.1, 0.1, "printed ±0.1")
    assert printed_band("30 +0.2/-0.1", 30) == (-0.1, 0.2, "printed +0.2/-0.1")
    assert printed_band("Ø25 H7", 25) == (0.0, 0.021, "fit H7")            # ISO 286: 18..30 mm, IT7 = 21 µm
    assert printed_band("Ø25 h6", 25) == (-0.013, 0.0, "fit h6")
    assert printed_band("Ø25", 25) is None


def test_the_general_tolerance_comes_from_the_drawing_or_is_assumed():
    assert general_class("GENERAL TOLERANCES: ISO 2768-f") == "f" and general_class("no note") is None
    assert iso2768(50, "m") == 0.3 and iso2768(268, "m") == 0.5 and iso2768(8, "c") == 0.5
    j = judge(chk(50, 50.2), "Ø50, Ø50, 200", None)
    assert j["tol"] == [-0.3, 0.3] and j["within"] and "assumed" in j["tol_source"]
    j = judge(chk(50, 50.2), "Ø50 ±0.1", "m")
    assert j["tol"] == [-0.1, 0.1] and j["within"] is False and j["tol_source"] == "printed ±0.1"
    j = judge(chk(24, 24.0), "Ø24, 40", "f")
    assert j["tol"] == [-0.1, 0.1] and j["within"] and j["tol_source"] == "ISO 2768-f (drawing)"


def test_a_volume_has_no_drawing_tolerance_and_inches_are_converted():
    assert judge({"what": "volume", "drawing": 100, "model": 101, "unit": "mm³"}, "", None)["within"] is None
    j = judge(chk(25.4, 25.5), "1.000 ±0.005", None, mm_per_unit=25.4)
    assert j["tol"] == [-0.127, 0.127] and j["within"] is True
