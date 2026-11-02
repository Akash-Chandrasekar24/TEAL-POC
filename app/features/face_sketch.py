"""Sketch ON a planar face of the part (low-level path): pick the face, read the real sketch frame back, and map a profile into it.

Why: a boss on a face that points AGAINST its datum normal (the bottom and left faces of the base, or any offset plane that coincides with a
face) cannot go through the checklist: the compiler turns an offset plane that lies on a face into a sketch ON that face, whose default
direction is already OUTWARD, so the `reversed` flag sends the boss back into the body and it adds nothing (measured 2026-10-13). A sketch made
directly on the face grows outward by default (measured: boss on the bottom face reached y = -40, on the left face x = -60) and its frame is
read back (the face frame is mirrored relative to the datum), so positions and arc senses are mapped with dot products, never assumed.

Face choice: any planar face at the height whose outward normal faces the way the boss grows. Pieces of a split face share one plane and frame.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass

from .base import POS_TOL_M, FeatureContext, FeatureRequestError, PlaneFrame, read_geometry
from .cut_feature import CutFeature


def pick_face_index(faces: list[dict], frame: PlaneFrame, at_m: float, outward: int) -> int:
    """Index of a planar face at `at_m` along the datum normal whose outward normal points along `outward` (+1 / -1) of that normal."""
    ax = frame.normal_axis
    cand = [p for p in faces if p.get("planar") and abs(abs(p["normal"][ax]) - 1) < 1e-3 and abs(p["point"][ax] - at_m) <= POS_TOL_M]
    facing = [p for p in cand if p["normal"][ax] * outward > 0]
    if not (facing or cand):
        raise FeatureRequestError(f"no planar face at {at_m * 1000:.3f} mm along the {frame.datum} normal: needs_human")
    return (facing or cand)[0]["i"]


@dataclass(frozen=True)
class FaceFrame:
    """The sketch frame of a face as SolidWorks reports it, plus the datum frame the request is written in."""
    origin: list[float]
    xdir: list[float]
    ydir: list[float]
    datum: PlaneFrame
    height_m: float

    def uv(self, x: float, y: float) -> tuple[float, float]:
        """Datum sketch point (m) lying on the face plane -> the face sketch's own (u, v)."""
        p = self.datum.to_world(x, y, self.height_m)
        d = [p[i] - self.origin[i] for i in range(3)]
        return sum(d[i] * self.xdir[i] for i in range(3)), sum(d[i] * self.ydir[i] for i in range(3))

    def records(self, segments: list[dict]) -> list[dict]:
        """add_sketch_entities records for graph-style segments (metres, datum frame): circle, line, arc (centre + ends + sense)."""
        out: list[dict] = []
        for s in segments:
            if s["kind"] == "circle":
                u, v = self.uv(s["cx"], s["cy"])
                out.append({"entity_type": "circle", "cx": u, "cy": v, "radius": s["diameter"] / 2})
            elif s["kind"] == "line":
                (u1, v1), (u2, v2) = self.uv(s["x1"], s["y1"]), self.uv(s["x2"], s["y2"])
                out.append({"entity_type": "line", "x1": u1, "y1": v1, "x2": u2, "y2": v2})
            elif s["kind"] == "arc":
                (cu, cv), (u1, v1), (u2, v2) = self.uv(s["cx"], s["cy"]), self.uv(s["x1"], s["y1"]), self.uv(s["x2"], s["y2"])
                out.append({"entity_type": "arc_center", "cx": cu, "cy": cv, "x1": u1, "y1": v1, "x2": u2, "y2": v2, "direction": self._sense(s)})
            else:
                raise FeatureRequestError(f"segment kind {s['kind']!r} cannot be sketched on a face")
        return out

    def _sense(self, s: dict) -> int:
        """+1 / -1 sweep sense of an arc AFTER mapping into the face frame (a mirrored frame flips it): the sign of the cross product of the
        start radius and the mid-arc radius."""
        cx, cy = s["cx"], s["cy"]
        a1, a2 = math.atan2(s["y1"] - cy, s["x1"] - cx), math.atan2(s["y2"] - cy, s["x2"] - cx)
        sweep = (a2 - a1) % (2 * math.pi) if s["dir"] == 1 else -((a1 - a2) % (2 * math.pi))
        if abs(sweep) < 1e-12:
            sweep = 2 * math.pi * s["dir"]
        r = math.hypot(s["x1"] - cx, s["y1"] - cy)
        mid = (cx + r * math.cos(a1 + sweep / 2), cy + r * math.sin(a1 + sweep / 2))
        (cu, cv), (u1, v1), (um, vm) = self.uv(cx, cy), self.uv(s["x1"], s["y1"]), self.uv(*mid)
        return 1 if (u1 - cu) * (vm - cv) - (v1 - cv) * (um - cu) > 0 else -1


async def sketch_profile_on_face(ctx: FeatureContext, frame: PlaneFrame, at_m: float, outward: int, segments: list[dict]):
    """create_sketch on the face + add the profile. Returns (tool result of the last call, face index). The caller extrudes."""
    faces, _ = await read_geometry(ctx.sw)
    idx = pick_face_index(faces, frame, at_m, outward)
    sk = await ctx.sw.create_sketch(on_face=True, face_index=idx)
    if not sk.ok:
        return sk, idx
    origin, xdir, ydir = CutFeature.parse_frame(sk.text)
    ff = FaceFrame(origin, xdir, ydir, frame, at_m)
    return await ctx.sw.add_sketch_entities(json.dumps(ff.records(segments))), idx
