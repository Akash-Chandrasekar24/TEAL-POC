"""Patterns: a way to write many positions of one feature without listing each one. The code expands the pattern into plain positions BEFORE the
feature is built, so every feature, check and probe still sees an explicit list (no SolidWorks pattern tool is used).

All geometry here is in the datum's CENTRED frame (origin = centre of the base outline's bounding box, x to the right, y up, as seen in the plate view),
in the drawing's unit. Four kinds:
- linear: the seed repeated `count` times, `spacing` apart, going right / left / up / down;
- grid: the seed is the first of `count_x` x `count_y` copies (going right and up unless told otherwise);
- circular: `count` copies spread on a circle (the bolt circle `pitch_diameter`) round `centre`; NO seed is written, the circle gives every position;
- mirror: every seed and its mirror image across the base's vertical or horizontal centreline (a seed on the line is not doubled).
Angles run counter-clockwise from +x, as seen in the plate view.
"""
from __future__ import annotations

import math
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.features.base import FeatureRequestError

from .position import Pos

_EPS = 1e-6


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


Direction = Literal["right", "left", "up", "down"]
_STEP = {"right": (1, 0), "left": (-1, 0), "up": (0, 1), "down": (0, -1)}


class LinearPattern(_Strict):
    kind: Literal["linear"]
    count: int = Field(ge=2, description="how many copies in total, the seed included")
    spacing: float = Field(gt=0, description="distance between neighbouring copies, as printed")
    direction: Direction = Field(description="the way the copies go from the seed, as seen in the plate view")


class GridPattern(_Strict):
    kind: Literal["grid"]
    count_x: int = Field(1, ge=1, description="copies along x (the seed is the first)")
    spacing_x: float | None = Field(None, gt=0, description="required when count_x > 1")
    count_y: int = Field(1, ge=1, description="copies along y (the seed is the first)")
    spacing_y: float | None = Field(None, gt=0, description="required when count_y > 1")
    x_direction: Literal["right", "left"] = "right"
    y_direction: Literal["up", "down"] = "up"

    @model_validator(mode="after")
    def _check(self):
        if self.count_x * self.count_y < 2:
            raise ValueError("a grid needs at least 2 copies (count_x * count_y >= 2)")
        if self.count_x > 1 and self.spacing_x is None:
            raise ValueError("spacing_x is required when count_x > 1")
        if self.count_y > 1 and self.spacing_y is None:
            raise ValueError("spacing_y is required when count_y > 1")
        return self


class CircularPattern(_Strict):
    kind: Literal["circular"]
    centre: Pos = Field(description="the centre of the circle, placed like any position")
    pitch_diameter: float = Field(gt=0, description="diameter of the circle the centres lie on (the printed bolt circle / PCD)")
    count: int = Field(ge=2, description="number of positions on the circle")
    start_angle_deg: float = Field(0, description="angle of the first position, counter-clockwise from +x as seen in the plate view (0 = to the right, 90 = up)")
    sweep_deg: float = Field(360, gt=0, le=360, description="360 = a full circle with equal steps; less = an arc from the first to the last position")


class MirrorPattern(_Strict):
    kind: Literal["mirror"]
    axis: Literal["vertical", "horizontal"] = Field(description="vertical = the base's vertical centreline (left <-> right); horizontal = its horizontal centreline (top <-> bottom)")


Pattern = Annotated[Union[LinearPattern, GridPattern, CircularPattern, MirrorPattern], Field(discriminator="kind")]


# ------------------------------------------------------------------ seeds rule (used by the params models)
def seeds_problem(pattern: object | None, n_seeds: int) -> str | None:
    """None when the number of written seed positions fits the pattern; else the reason."""
    if pattern is None:
        return None if n_seeds >= 1 else "write at least one position"
    if getattr(pattern, "kind", None) == "circular":
        return None if n_seeds == 0 else "with a circular pattern write NO position: the circle gives every position"
    return None if n_seeds >= 1 else "write the seed position (the first copy) as well as the pattern"


# ------------------------------------------------------------------ expansion
def _clean(v: float) -> float:
    return round(v, 9) + 0.0


def expand(seeds: list[tuple[float, float]], pattern: object, centre: tuple[float, float] | None = None) -> list[tuple[float, float]]:
    """The positions the pattern stands for, in order, in the datum's centred frame. `centre` is the circular pattern's centre already converted to
    that frame. Raises FeatureRequestError when two positions coincide."""
    kind = pattern.kind
    out: list[tuple[float, float]] = []
    if kind == "linear":
        dx, dy = _STEP[pattern.direction]
        for sx, sy in seeds:
            out += [(sx + dx * pattern.spacing * k, sy + dy * pattern.spacing * k) for k in range(pattern.count)]
    elif kind == "grid":
        ax = 1 if pattern.x_direction == "right" else -1
        ay = 1 if pattern.y_direction == "up" else -1
        for sx, sy in seeds:
            for j in range(pattern.count_y):
                for i in range(pattern.count_x):
                    out.append((sx + ax * (pattern.spacing_x or 0) * i, sy + ay * (pattern.spacing_y or 0) * j))
    elif kind == "circular":
        if centre is None:
            raise FeatureRequestError("a circular pattern needs its centre")
        r = pattern.pitch_diameter / 2
        step = 360.0 / pattern.count if pattern.sweep_deg >= 360 else pattern.sweep_deg / (pattern.count - 1)
        for k in range(pattern.count):
            a = math.radians(pattern.start_angle_deg + step * k)
            out.append((centre[0] + r * math.cos(a), centre[1] + r * math.sin(a)))
    elif kind == "mirror":
        for sx, sy in seeds:
            out.append((sx, sy))
            mx, my = (-sx, sy) if pattern.axis == "vertical" else (sx, -sy)
            on_axis = abs(sx) < _EPS if pattern.axis == "vertical" else abs(sy) < _EPS
            if not on_axis:
                out.append((mx, my))
    else:
        raise FeatureRequestError(f"unknown pattern kind {kind!r}")
    out = [(_clean(x), _clean(y)) for x, y in out]
    for i in range(len(out)):
        for j in range(i + 1, len(out)):
            if math.hypot(out[i][0] - out[j][0], out[i][1] - out[j][1]) < _EPS:
                raise FeatureRequestError(f"the pattern puts two positions on the same point ({out[i][0]:g}, {out[i][1]:g}): check count, spacing and direction")
    return out
