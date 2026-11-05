"""Dimension text -> comparable tokens (used to combine two reads of a page, and by the benchmark to score them)."""
from __future__ import annotations

import re

_NUM = r"\d+(?:[.,]\d+)?"
# a count in front of a diameter / radius / "holes" (6xØ24, 4-R20, 2 holes Ø5): not a dimension
_COUNT = re.compile(rf"\b\d+\s*(?:[xX×]|-|holes?\b)\s*(?=[ØR]|\s*holes?\b|\s*[ØR])")
_TOKEN = re.compile(rf"([ØR]?)\s*({_NUM})\s*(°?)")


def dim_tokens(text: str) -> list[str]:
    """'6xØ24' -> ['Ø24'], '2 x Ø5.50 THRU' -> ['Ø5.5'], '3x45°' -> ['3', '45°'], '80.00 ±0.10' -> ['80'], '(360)' -> ['360']."""
    t = text.replace("⌀", "Ø").replace("ø", "Ø").replace("%%c", "Ø").replace("%%C", "Ø")
    t = re.sub(r"±\s*" + _NUM, "", t)                                  # a tolerance is not a dimension
    t = re.sub(r"[+-]\s*" + _NUM + r"\s*/\s*[+-]?\s*" + _NUM, "", t)
    t = _COUNT.sub("", t)
    out = []
    for prefix, number, deg in _TOKEN.findall(t):
        value = float(number.replace(",", "."))
        if value == 0:
            continue
        out.append(f"{prefix}{value:g}{deg}")
    return out
