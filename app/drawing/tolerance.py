"""The tolerance a drawing allows on one dimension, and whether the model is inside it. In this order:
  1. printed on the callout: "50 ±0.1", "50 +0.2/-0.1", "Ø25 H7" / "Ø25 h6" (ISO 286 IT6..IT11 for H and h);
  2. the drawing's general tolerance: "ISO 2768-m" (f / m / c / v) anywhere in its text;
  3. none printed: ISO 2768-m, marked as assumed.
All values in mm."""
from __future__ import annotations

import re

_SIZES = [3, 6, 10, 18, 30, 50, 80, 120, 180, 250, 315, 400, 500]                  # ISO 286 size steps (upper bounds, mm)
_IT = {6: [6, 8, 9, 11, 13, 16, 19, 22, 25, 29, 32, 36, 40], 7: [10, 12, 15, 18, 21, 25, 30, 35, 40, 46, 52, 57, 63],
       8: [14, 18, 22, 27, 33, 39, 46, 54, 63, 72, 81, 89, 97], 9: [25, 30, 36, 43, 52, 62, 74, 87, 100, 115, 130, 140, 155],
       10: [40, 48, 58, 70, 84, 100, 120, 140, 160, 185, 210, 230, 250], 11: [60, 75, 90, 110, 130, 160, 190, 220, 250, 290, 320, 360, 400]}   # µm
_2768_SIZES = [3, 6, 30, 120, 400, 1000, 2000, 4000]                                # ISO 2768-1 linear size steps (upper bounds, mm)
_2768 = {"f": [0.05, 0.05, 0.1, 0.15, 0.2, 0.3, 0.5, None], "m": [0.1, 0.1, 0.2, 0.3, 0.5, 0.8, 1.2, 2.0],
         "c": [0.2, 0.3, 0.5, 0.8, 1.2, 2.0, 3.0, 4.0], "v": [None, 0.5, 1.0, 1.5, 2.5, 4.0, 6.0, 8.0]}
_NUM = r"(\d+(?:\.\d+)?)"


def general_class(text: str) -> str | None:
    """The ISO 2768 class named anywhere in the drawing's text ("ISO 2768-m", "2768 mK", "GENERAL TOLERANCE ISO 2768 f")."""
    m = re.search(r"2768\s*[-–]?\s*([fmcv])", text or "", re.I)
    return m.group(1).lower() if m else None


def _step(size: float, steps: list[float]) -> int | None:
    return next((i for i, top in enumerate(steps) if size <= top), None)


def iso2768(size: float, cls: str) -> float | None:
    i = _step(abs(size), _2768_SIZES)
    return None if i is None or abs(size) < 0.5 else _2768[cls][i]


def printed_band(text: str, nominal: float, mm_per_unit: float = 1.0) -> tuple[float, float, str] | None:
    """(lower, upper, how) in mm from one printed callout, or None when it prints no tolerance."""
    t = (text or "").replace("−", "-").replace("–", "-")
    m = re.search(r"±\s*" + _NUM, t) or re.search(r"\+/-\s*" + _NUM, t)
    if m:
        d = float(m.group(1)) * mm_per_unit
        return -d, d, f"printed ±{m.group(1)}"
    m = re.search(r"\+\s*" + _NUM + r"\s*/?\s*-\s*" + _NUM, t)
    if m:
        return -float(m.group(2)) * mm_per_unit, float(m.group(1)) * mm_per_unit, f"printed +{m.group(1)}/-{m.group(2)}"
    m = re.search(r"\b([Hh])\s*(6|7|8|9|10|11)\b", t)
    if m:
        i = _step(nominal, _SIZES)
        if i is not None:
            it = _IT[int(m.group(2))][i] / 1000
            return (0.0, it, f"fit {m.group(1)}{m.group(2)}") if m.group(1) == "H" else (-it, 0.0, f"fit {m.group(1)}{m.group(2)}")
    return None


def _callout_for(drawing_value: float, printed: str, mm_per_unit: float) -> str | None:
    """The printed callout of an item that carries this value (an item prints several, comma separated)."""
    for part in re.split(r",|;", printed or ""):
        nums = [float(x) for x in re.findall(_NUM, part.replace("±", " ± "))]
        if nums and abs(nums[0] * mm_per_unit - drawing_value) < 1e-6 * max(1.0, drawing_value):
            return part
    return None


def judge(check: dict, printed: str, general: str | None, mm_per_unit: float = 1.0, accuracy: float | None = None) -> dict:
    """The check with its tolerance band (mm), where the band comes from, and whether the model is inside it. Volumes have no drawing tolerance.
    `accuracy` (mm, e.g. 0.01 or 0.001): judge the MODEL against the printed nominal by that band instead (how exactly the CAD matches the
    drawing); the drawing's own band stays in `tol` for reference. The face reader resolves 0.001 mm: that is the finest meaningful value."""
    if accuracy:
        base = judge(check, printed, general, mm_per_unit)
        if check.get("unit") != "mm" or check.get("model") is None:
            return base
        err = check["model"] - check["drawing"]
        return {**base, "within_drawing": base.get("within"), "drawing_tol_source": base.get("tol_source"), "accuracy": accuracy,
                "tol": [-accuracy, accuracy], "tol_source": f"model accuracy ±{accuracy:g} mm (drawing: {base.get('tol_source')})",
                "within": abs(err) <= accuracy + 1e-9}
    if check.get("unit") != "mm" or check.get("model") is None:
        return {**check, "tol": None, "tol_source": "no drawing tolerance for a volume" if check.get("unit") != "mm" else "not measured", "within": None}
    nominal, err = check["drawing"], check["model"] - check["drawing"]
    callout = _callout_for(nominal, printed, mm_per_unit)
    band = printed_band(callout, nominal, mm_per_unit) if callout else None
    if band is None:
        cls = general or "m"
        d = iso2768(nominal, cls)
        if d is None:
            return {**check, "tol": None, "tol_source": f"ISO 2768-{cls} has no value for {nominal:g} mm", "within": None}
        band = (-d, d, f"ISO 2768-{cls}" + (" (drawing)" if general else " (assumed: no tolerance printed)"))
    lo, hi, how = band
    return {**check, "tol": [round(lo, 4), round(hi, 4)], "tol_source": how, "within": lo - 1e-9 <= err <= hi + 1e-9}
