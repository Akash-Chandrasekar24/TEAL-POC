"""The position model shared by params.py and patterns.py (kept apart so the two can import it without a cycle)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Pos(BaseModel):
    """A position as printed: the CENTRE of the feature."""
    model_config = ConfigDict(extra="forbid")

    x: float
    y: float
    x_from: Literal["centre", "left_edge", "right_edge", "front_edge", "back_edge"] = Field(
        "centre", description="what x is measured from (centre = centre of the base outline / of the face). Front, back, top, bottom faces: left_edge | right_edge. "
                              "Left and right faces: front_edge | back_edge (the depth direction)")
    y_from: Literal["centre", "bottom_edge", "top_edge", "front_edge", "back_edge"] = Field(
        "centre", description="what y is measured from (centre = centre of the base outline / of the face). Front, back, left and right faces: bottom_edge | top_edge. "
                              "Top and bottom faces: front_edge | back_edge (the depth direction)")
