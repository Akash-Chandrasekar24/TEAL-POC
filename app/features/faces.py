"""FACE VIEWS: where a feature goes on the BASE, whatever face it is on.

The base is an outline sketched on the FRONT datum and extruded by T toward the viewer, so in the datum frame it is the box
x in [-W/2, W/2], y in [-H/2, H/2], z in [0, T] (front face at z = T, back face at z = 0).

A face view turns "this face of the base" into what the features already understand: a datum plane, the height of the face along
that datum's normal, the direction INTO the material, and the conversion of a position printed on the face into the sketch frame of
that datum. Positions are always measured from NAMED EDGES of the part itself (never from the page orientation of a drawing view,
which differs between first and third angle projection):

    front / back   x: left_edge | right_edge | centre      y: bottom_edge | top_edge | centre      (the front-view frame, as before)
    top / bottom   x: left_edge | right_edge | centre      y: front_edge | back_edge | centre      (depth measured along z)
    left / right   x: front_edge | back_edge | centre      y: bottom_edge | top_edge | centre      (depth measured along z)

Sketch frames used here are measured ones: FRONT datum x = world X, y = world Y (normal +Z); TOP datum x = world X, y = world -Z
(normal +Y); RIGHT datum x = world -Z, y = world Y (normal +X; measured 2026-10-13). The left face is the right datum's downward face.
A boss on a downward-facing face that is not on the datum itself (the bottom and left faces) is refused: measured, `reversed` on an offset
plane silently adds nothing.
"""
from __future__ import annotations

from dataclasses import dataclass

from .base import FeatureRequestError, Position

SUPPORTED = ("front", "back", "top", "bottom", "left", "right")
_WORDS = {
    "front": ({"left_edge", "right_edge", "centre"}, {"bottom_edge", "top_edge", "centre"}),
    "back": ({"left_edge", "right_edge", "centre"}, {"bottom_edge", "top_edge", "centre"}),
    "top": ({"left_edge", "right_edge", "centre"}, {"front_edge", "back_edge", "centre"}),
    "bottom": ({"left_edge", "right_edge", "centre"}, {"front_edge", "back_edge", "centre"}),
    "left": ({"front_edge", "back_edge", "centre"}, {"bottom_edge", "top_edge", "centre"}),
    "right": ({"front_edge", "back_edge", "centre"}, {"bottom_edge", "top_edge", "centre"}),
}


@dataclass(frozen=True)
class FaceView:
    side: str
    width: float            # W: base extent along x (drawing unit)
    height: float           # H: base extent along y
    depth: float            # T: base extent along z (the extrusion depth)
    lift: float = 0.0       # the plane sits this far BEYOND the face, outward (the flat top of a boss, an offset plane)

    def __post_init__(self):
        if self.side not in SUPPORTED:
            raise FeatureRequestError(f"face must be one of {SUPPORTED}, got {self.side!r}")

    # ---------------------------------------------------------------- where the face is
    @property
    def datum(self) -> str:
        return {"front": "front", "back": "front", "top": "top", "bottom": "top", "left": "right", "right": "right"}[self.side]

    @property
    def outward(self) -> int:
        """Sign of the outward normal along the datum normal."""
        return 1 if self.side in ("front", "top", "right") else -1

    @property
    def plane_height(self) -> float:
        """Height of the face along the datum normal (drawing unit): front z = T, back z = 0, top y = H/2, bottom y = -H/2."""
        base = {"front": self.depth, "back": 0.0, "top": self.height / 2, "bottom": -self.height / 2,
                "right": self.width / 2, "left": -self.width / 2}[self.side]
        return base + self.outward * self.lift

    @property
    def into(self) -> str:
        """Direction along the datum normal that goes INTO the material (the way a cut goes)."""
        return "negative" if self.outward > 0 else "positive"

    @property
    def is_datum_itself(self) -> bool:
        return abs(self.plane_height) < 1e-12

    def boss_orientation(self, what: str = "a boss") -> tuple[bool, str]:
        """(`reversed`, target) for a boss growing OUT of this face. Faces that point along the datum normal: the checklist plane path. The back
        face (on the datum itself): reversed plane path (proven). A downward face on an OFFSET plane (bottom, left): the compiler would sketch on
        the face and `reversed` would send the boss back into the body (adds nothing), so the boss is sketched on the face directly."""
        if self.outward > 0:
            return False, "plane"
        if self.is_datum_itself:
            return True, "plane"
        return True, "face"

    @property
    def cut_target(self) -> str:
        """A profile cut on a downward face that is not on the datum: sketch ON the face (the plane path would be refused, mirrored frame)."""
        return "face" if self.outward < 0 and not self.is_datum_itself else "plane"

    # ---------------------------------------------------------------- the face rectangle in its sketch frame
    def rect(self) -> tuple[float, float, float, float]:
        """(x0, x1, y0, y1) of the face in the sketch frame of its datum (drawing unit)."""
        if self.datum == "front":
            return -self.width / 2, self.width / 2, -self.height / 2, self.height / 2
        if self.datum == "right":                                          # right datum: sketch x = -z (z in [0, T]), sketch y = world Y
            return -self.depth, 0.0, -self.height / 2, self.height / 2
        return -self.width / 2, self.width / 2, -self.depth, 0.0          # top datum: sketch y = -z, z in [0, T]

    def size(self) -> tuple[float, float]:
        x0, x1, y0, y1 = self.rect()
        return x1 - x0, y1 - y0

    def centre(self) -> tuple[float, float]:
        x0, x1, y0, y1 = self.rect()
        return (x0 + x1) / 2, (y0 + y1) / 2

    def check_words(self, pos: Position) -> None:
        xs, ys = _WORDS[self.side]
        if pos.x_from not in xs or pos.y_from not in ys:
            raise FeatureRequestError(f"on the {self.side} face x_from must be one of {sorted(xs)} and y_from one of {sorted(ys)}, "
                                      f"got {pos.x_from!r} / {pos.y_from!r}")

    def to_sketch(self, pos: Position) -> tuple[float, float]:
        """A position as printed on this face -> sketch-frame coordinates (drawing unit), measured from the datum origin."""
        self.check_words(pos)
        x0, x1, y0, y1 = self.rect()
        cx, cy = self.centre()                   # "centre" = the centre of the face (the datum origin on the front / back faces)
        # sketch coordinate that runs along -z (top: y, right: x): the front edge (z = T) is its minimum, the back edge (z = 0) its maximum
        if self.datum == "right":
            x = {"front_edge": x0 + pos.x, "back_edge": x1 - pos.x, "centre": cx + pos.x}[pos.x_from]
            y = {"bottom_edge": y0 + pos.y, "top_edge": y1 - pos.y, "centre": cy + pos.y}[pos.y_from]
            return x, y
        x = {"left_edge": x0 + pos.x, "right_edge": x1 - pos.x, "centre": cx + pos.x}[pos.x_from]
        if self.datum == "front":
            y = {"bottom_edge": y0 + pos.y, "top_edge": y1 - pos.y, "centre": cy + pos.y}[pos.y_from]
        else:
            y = {"front_edge": y0 + pos.y, "back_edge": y1 - pos.y, "centre": cy + pos.y}[pos.y_from]
        return x, y

    def centred(self, pos: Position) -> Position:
        """The same position written from the sketch origin (what every feature accepts without an outline size)."""
        x, y = self.to_sketch(pos)
        return Position(x, y)

    # ---------------------------------------------------------------- outlines: the OUTSIDE VIEW of the face
    def view_size(self) -> tuple[float, float]:
        """(u extent, v extent) of the face seen from outside: top / bottom W x T, left / right T x H."""
        return {"front": (self.width, self.height), "back": (self.width, self.height), "top": (self.width, self.depth),
                "bottom": (self.width, self.depth), "left": (self.depth, self.height), "right": (self.depth, self.height)}[self.side]

    @property
    def view_mirrored(self) -> bool:
        """Is the map from the outside view (u, v) to the datum sketch orientation-reversing? (arc senses flip)"""
        return self.side in ("bottom", "left")

    def view_to_sketch(self, u: float, v: float) -> tuple[float, float]:
        """A point of the face seen from OUTSIDE the part -> the datum sketch frame. (u, v) is measured from the lower-left corner of that view,
        which is fixed by the part, not by any drawing view:
            top     looking down at the face:  u to the right (+x), v toward the BACK; origin at the left / front corner
            bottom  looking up at the face:    u to the right (+x), v toward the FRONT; origin at the left / back corner
            right   looking at the face:       u from the front edge to the back edge, v up; origin at the front / bottom corner
            left    looking at the face:       u from the back edge to the front edge, v up; origin at the back / bottom corner"""
        x0, x1, y0, y1 = self.rect()
        if self.side == "top":
            return x0 + u, y0 + v
        if self.side == "bottom":
            return x0 + u, y1 - v
        if self.side == "right":
            return x0 + u, y0 + v
        if self.side == "left":
            return x1 - u, y0 + v
        raise FeatureRequestError("outlines on the front / back faces use the base frame (the proven path)")

    def map_segments(self, segs: list[dict], frame: str = "base_lower_left") -> list[dict]:
        """Outline segments (lines, arcs) written in the outside view of this face -> the datum sketch frame, drawing unit. frame 'centre' =
        measured from the centre of the view instead of its lower-left corner."""
        du, dv = (0.0, 0.0)
        if frame == "centre":
            su, sv = self.view_size()
            du, dv = su / 2, sv / 2
        elif frame != "base_lower_left":
            raise FeatureRequestError(f"outline frame must be 'base_lower_left' or 'centre', got {frame!r}")
        out = []
        for s in segs:
            t = dict(s)
            for kx, ky in (("x1", "y1"), ("x2", "y2"), ("cx", "cy")):
                if kx in t:
                    t[kx], t[ky] = self.view_to_sketch(t[kx] + du, t[ky] + dv)
            if t["kind"] == "arc" and self.view_mirrored:
                t["dir"] = -t["dir"]
            out.append(t)
        return out

    def to_datum_frame_shift(self) -> tuple[float, float]:
        """Shift that turns a point measured from the face's lower-left corner (sketch frame) into the datum sketch frame, as `corner + shift`."""
        x0, _, y0, _ = self.rect()
        return x0, y0
