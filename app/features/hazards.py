"""Shared safety check for features that sketch on an OFFSET datum plane.

Measured in SolidWorks: when an offset plane coincides with a DOWNWARD-facing planar face (for
example the underside of a plate), the compiler sketches ON that face, and that face's sketch frame is mirrored in
sketch y compared with the datum. Volume cannot see this (a mirrored circle on a symmetric part has the same volume),
so features that cannot route around it refuse the request instead of placing geometry in the wrong spot.
"""
from __future__ import annotations

from .base import POS_TOL_M, FeatureRequestError, PlaneFrame


def downward_faces_at(faces: list[dict], frame: PlaneFrame, at_m: float) -> list[dict]:
    """Planar faces normal to the datum normal, lying at `at_m`, whose outward normal points AGAINST the datum normal."""
    ax = frame.normal_axis
    return [p for p in faces
            if p.get("planar") and abs(abs(p["normal"][ax]) - 1) < 1e-3
            and p["normal"][ax] < 0 and abs(p["point"][ax] - at_m) <= POS_TOL_M]


def refuse_mirrored_frame(faces: list[dict], frame: PlaneFrame, at_m: float, what: str) -> None:
    hits = downward_faces_at(faces, frame, at_m)
    if hits:
        raise FeatureRequestError(
            f"{what}: the sketch plane at {at_m * 1000:.3f} mm coincides with a downward-facing face "
            f"{[(h.get('i'), round(h['area'] * 1e6, 1)) for h in hits]}; its sketch frame is mirrored, so positions could "
            f"land on the wrong side. Start from a free plane (a different offset) or use the face-path cut_feature.")
