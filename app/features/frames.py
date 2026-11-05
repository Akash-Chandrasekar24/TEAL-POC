"""Rigid sketch frames, independent of the SolidWorks execution route.

Lengths stay in the caller's unit. Direction vectors are dimensionless. Frames
read back from SolidWorks may have small rounding errors, but cannot contain
scale, shear, or non-finite coordinates.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

from .base import FeatureRequestError

Vector = tuple[float, float, float]
AXIS_TOL = 1e-5


def _vector(value: Sequence[float], name: str) -> Vector:
    try:
        out = tuple(float(v) for v in value)
    except (TypeError, ValueError, OverflowError) as ex:
        raise FeatureRequestError(f"{name} must contain three finite numbers") from ex
    if len(out) != 3 or not all(math.isfinite(v) for v in out):
        raise FeatureRequestError(f"{name} must contain three finite numbers")
    return out


def dot(a: Vector, b: Vector) -> float:
    return sum(x * y for x, y in zip(a, b))


def cross(a: Vector, b: Vector) -> Vector:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


@dataclass(frozen=True)
class SketchFrame:
    """Right-handed frame: origin + x*xdir + y*ydir + z*normal.

    Small rounding errors within AXIS_TOL are orthonormalized so projection and
    reconstruction remain inverses. Invalid frames are refused before sketching.
    """
    origin: Vector
    xdir: Vector
    ydir: Vector
    normal: Vector = field(init=False)

    def __post_init__(self):
        origin = _vector(self.origin, "frame origin")
        x, y = _vector(self.xdir, "frame xdir"), _vector(self.ydir, "frame ydir")
        nx, ny = math.sqrt(dot(x, x)), math.sqrt(dot(y, y))
        if abs(nx - 1) > AXIS_TOL or abs(ny - 1) > AXIS_TOL:
            raise FeatureRequestError("sketch frame directions must be unit vectors (no scaling)")
        x, y = tuple(v / nx for v in x), tuple(v / ny for v in y)
        overlap = dot(x, y)
        if abs(overlap) > AXIS_TOL:
            raise FeatureRequestError("sketch frame directions must be perpendicular (no shear)")
        y = tuple(y[i] - overlap * x[i] for i in range(3))
        ny = math.sqrt(dot(y, y))
        y = tuple(v / ny for v in y)
        for name, value in (("origin", origin), ("xdir", x), ("ydir", y), ("normal", cross(x, y))):
            object.__setattr__(self, name, value)

    def to_world(self, x: float, y: float, z: float = 0.0) -> Vector:
        x, y, z = _vector((x, y, z), "local point")
        return tuple(self.origin[i] + x * self.xdir[i] + y * self.ydir[i] + z * self.normal[i] for i in range(3))

    def to_local(self, point: Sequence[float]) -> Vector:
        p = _vector(point, "world point")
        d = tuple(p[i] - self.origin[i] for i in range(3))
        return dot(d, self.xdir), dot(d, self.ydir), dot(d, self.normal)

    def project(self, point: Sequence[float], tolerance: float) -> tuple[float, float]:
        """Project a point only if it actually lies on this sketch plane."""
        if not math.isfinite(tolerance) or tolerance < 0:
            raise FeatureRequestError("plane tolerance must be finite and non-negative")
        u, v, distance = self.to_local(point)
        if abs(distance) > tolerance:
            raise FeatureRequestError(f"point is off the sketch plane by {distance:g}: needs_human")
        return u, v

    def orientation_to(self, other: SketchFrame, tolerance: float) -> int:
        """Sense of a planar mapping; reject non-coplanar frames, never flatten them."""
        self.project(other.origin, tolerance)
        alignment = dot(self.normal, other.normal)
        if math.sqrt(dot(cross(self.normal, other.normal), cross(self.normal, other.normal))) > AXIS_TOL:
            raise FeatureRequestError("source and target sketch planes are not parallel: needs_human")
        return 1 if alignment > 0 else -1
