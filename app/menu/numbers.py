"""The numbers inside a printed dimension text (used by rule 12: every number an item uses must appear in `seen_dimensions`)."""
from __future__ import annotations

import re
from fractions import Fraction

_NUM = re.compile(r"\d+\s+\d+/\d+|\d+/\d+|\d*\.\d+|\d+")


def numbers_in(text: str) -> set[float]:
    """All numbers in a dimension string: 'Ø8 THRU' -> {8}, '4X Ø8' -> {4, 8}, '1 1/2' -> {1.5, 1, 0.5...}."""
    out: set[float] = set()
    for tok in _NUM.findall(str(text)):
        tok = tok.strip()
        if " " in tok and "/" in tok:          # mixed number: 1 1/2
            whole, frac = tok.split()
            out.add(float(int(whole) + Fraction(frac)))
        elif "/" in tok:
            out.add(float(Fraction(tok)))
        else:
            out.add(float(tok))
    return out
