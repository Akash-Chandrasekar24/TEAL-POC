"""ANCHORS: the named flat faces that earlier items create, so a later item can say "on F6.top" instead of computing offsets in the base plate's frame.

An anchor is a flat face with an axis-aligned normal (+x, -x, +y, -y, +z, -z), a centre (world, drawing unit) and an extent (U, V). A feature placed ON an anchor
is turned into what the code already builds and has verified live: a datum plane (front for z normals, right for x normals, top for y normals) at the
anchor's height, the direction into the material, and positions converted into that datum's measured sketch frame. Nothing new is asked of SolidWorks.

LOCAL DIRECTIONS (the rule, in plain words). Positions on an anchor are written as seen LOOKING AT THE FACE FROM OUTSIDE THE PART: u is to the right, v is up.
`centre` is the middle of the anchor; edge words (left_edge, right_edge, bottom_edge, top_edge) measure from its extent.
    normal +z  (a face that looks toward you in the front view):    right = world +x (the right of the front view)      up = world +y
    normal -z  (the back, looked at from behind the part):          right = world -x                                     up = world +y
    normal +x  (a face that looks to the right, seen from the right): right = toward the BACK of the part (world -z)     up = world +y
    normal -x  (a face that looks to the left, seen from the left):  right = toward the FRONT of the part (world +z)    up = world +y
    normal +y  (a face that looks up, seen from above):              right = world +x                                     up = toward the BACK (world -z)
    normal -y  (a face that looks down, seen from below):            right = world +x                                     up = toward the FRONT (world +z)
These are the same rules the six base faces already use (see faces.py). The base is the box x in [-W/2, W/2], y in [-H/2, H/2], z in [0, T]; the front face is z = T.

Anchors a feature publishes (named `<item id>.<role>`):
    extrude_profile_boss / circle_boss (a boss on a face):   `top` (`top1`, `top2`, ... for several circles): its free end cap
    extrude_profile_boss, its FLAT SIDES:                    `right` `left` `up` `down` `front` `back`: the flat side that LOOKS that way in the front view
                                                             (right = +x, left = -x, up = +y, down = -y, front = +z toward you, back = -z)
    revolve_on_face, 90 degrees:                             `end`: the open end of the bend (180 degrees comes back to the face plane and has no free end;
                                                             45 and 135 degrees end on a slanted plane: no anchor)
The free end ALSO answers to the direction word it looks in (`top` of a boss that grows toward you is also `front`). `top` is therefore ALWAYS the free end,
whichever way it looks, while `up` is ALWAYS the face that looks +y: they are the same face only for a boss that grows upward.

SIDES, in plain words:
  * Only straight edges that run along an axis of the outline become sides (arcs, sloped edges and round bosses give none: their sides are curved).
  * The size of a side is measured on the FULL OUTER outline: a side of a 360 x 360 boss with R20 corners is 360 wide, not the 320 of its straight part, and its centre
    is the centre of the outline. So "50 from the top edge" means 50 from the outermost edge of the part, as the drawing dimensions it.
  * Several sides that look the same way are numbered `right1`, `right2` ... from the LOWEST POSITION first: the lowest coordinate along the direction they look
    (for right / left sides the smallest x, for up / down sides the smallest y, for front / back sides the smallest z); sides in the same plane are ordered by the
    smallest position along the other in-plane axis. A direction with a single side has no number.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .base import FeatureRequestError, PlaneFrame, Position, factor

_AXIS = {"x": 0, "y": 1, "z": 2}
_DATUM = {"x": "right", "y": "top", "z": "front"}

# normal -> (world direction of local u = "right", world direction of local v = "up"), as seen looking at the face from outside
VIEW_AXES: dict[str, tuple[tuple[int, int, int], tuple[int, int, int]]] = {
    "+z": ((1, 0, 0), (0, 1, 0)),
    "-z": ((-1, 0, 0), (0, 1, 0)),
    "+x": ((0, 0, -1), (0, 1, 0)),
    "-x": ((0, 0, 1), (0, 1, 0)),
    "+y": ((1, 0, 0), (0, 0, -1)),
    "-y": ((1, 0, 0), (0, 0, 1)),
}
NORMALS = tuple(VIEW_AXES)
_WORDS_X = {"centre", "left_edge", "right_edge"}
_WORDS_Y = {"centre", "bottom_edge", "top_edge"}


@dataclass(frozen=True)
class Anchor:
    name: str
    centre: tuple[float, float, float]            # world, drawing unit
    normal: str                                   # '+x' ... '-z'
    extent: tuple[float, float] | None = None     # (U along local u, V along local v), drawing unit; None = unknown (edge words are then refused)
    flat: tuple[float, float] | None = None       # only for a SIDE of an extruded boss: the STRAIGHT part of the side along `flat_axis` (local coordinates from the centre).
    flat_axis: str | None = None                  # 'u' or 'v': the local axis the side's edge runs along. Beyond `flat` the side is rounded or stepped away.

    def __post_init__(self):
        if self.normal not in NORMALS:
            raise FeatureRequestError(f"anchor normal must be one of {NORMALS}, got {self.normal!r}")
        if (self.flat is None) != (self.flat_axis is None) or self.flat_axis not in (None, "u", "v"):
            raise FeatureRequestError("an anchor's flat range needs its axis, 'u' or 'v'")


class AnchorView:
    """The face of an anchor, with the same interface the registry uses for the base's side faces (FaceView)."""

    def __init__(self, anchor: Anchor, lift: float = 0.0):
        if abs(lift) > 1e-12:
            raise FeatureRequestError(f"'on' {anchor.name}: a face reference cannot be lifted (offset must be 0)")
        self.anchor = anchor
        self.axis_letter = anchor.normal[1]
        self.frame = PlaneFrame(_DATUM[self.axis_letter])
        sx, sy = self.frame.signs
        a_ax, b_ax = self.frame.in_plane_axes
        u_world, v_world = VIEW_AXES[anchor.normal]
        # local u / v as directions in the datum's sketch frame (they run along one sketch axis each, with a sign)
        self.du = (sx * u_world[a_ax], sy * u_world[b_ax])
        self.dv = (sx * v_world[a_ax], sy * v_world[b_ax])
        self._centre_sketch = (sx * anchor.centre[a_ax], sy * anchor.centre[b_ax])

    # ---------------------------------------------------------------- where the face is (what `where()` needs)
    @property
    def datum(self) -> str:
        return _DATUM[self.axis_letter]

    @property
    def outward(self) -> int:
        return 1 if self.anchor.normal[0] == "+" else -1

    @property
    def plane_height(self) -> float:
        return self.anchor.centre[_AXIS[self.axis_letter]]

    @property
    def into(self) -> str:
        return "negative" if self.outward > 0 else "positive"

    @property
    def is_datum_itself(self) -> bool:
        return abs(self.plane_height) < 1e-12

    def boss_orientation(self, what: str = "a boss") -> tuple[bool, str]:
        """Same rules as FaceView: a face pointing along the datum normal uses the plane path; a downward face on an offset plane is sketched on the face."""
        if self.outward > 0:
            return False, "plane"
        if self.is_datum_itself:
            return True, "plane"
        return True, "face"

    @property
    def cut_target(self) -> str:
        return "face" if self.outward < 0 and not self.is_datum_itself else "plane"

    @property
    def view_mirrored(self) -> bool:
        """Does the map (u, v) -> datum sketch reverse orientation? (arc senses flip)"""
        return self.du[0] * self.dv[1] - self.du[1] * self.dv[0] < 0

    # ---------------------------------------------------------------- positions
    def centre(self) -> tuple[float, float]:
        """The anchor's centre in the datum sketch frame."""
        return self._centre_sketch

    def view_size(self) -> tuple[float, float]:
        if self.anchor.extent is None:
            raise FeatureRequestError(f"'on' {self.anchor.name}: the size of this face is not known")
        return self.anchor.extent

    def to_local(self, pos: Position) -> tuple[float, float]:
        """A position as written -> local (u, v) from the anchor's centre."""
        if pos.x_from not in _WORDS_X or pos.y_from not in _WORDS_Y:
            raise FeatureRequestError(f"on {self.anchor.name}: x_from must be one of {sorted(_WORDS_X)} and y_from one of {sorted(_WORDS_Y)}, "
                                      f"got {pos.x_from!r} / {pos.y_from!r}")
        if (pos.x_from != "centre" or pos.y_from != "centre") and self.anchor.extent is None:
            raise FeatureRequestError(f"on {self.anchor.name}: edge words need the size of the face, which is not known: measure from the centre")
        U, V = self.anchor.extent or (0.0, 0.0)
        u = {"centre": pos.x, "left_edge": -U / 2 + pos.x, "right_edge": U / 2 - pos.x}[pos.x_from]
        v = {"centre": pos.y, "bottom_edge": -V / 2 + pos.y, "top_edge": V / 2 - pos.y}[pos.y_from]
        return u, v

    def local_to_sketch(self, u: float, v: float) -> tuple[float, float]:
        cx, cy = self._centre_sketch
        return cx + u * self.du[0] + v * self.dv[0], cy + u * self.du[1] + v * self.dv[1]

    def local_to_world(self, u: float, v: float) -> tuple[float, float, float]:
        x, y = self.local_to_sketch(u, v)
        return self.frame.to_world(x, y, self.plane_height)

    def to_sketch(self, pos: Position) -> tuple[float, float]:
        return self.local_to_sketch(*self.to_local(pos))

    def centred(self, pos: Position) -> Position:
        x, y = self.to_sketch(pos)
        return Position(x, y)

    # ---------------------------------------------------------------- outlines (written as seen from outside)
    def map_segments(self, segs: list[dict], frame: str = "base_lower_left") -> list[dict]:
        """Outline segments written in local (u, v) -> the datum sketch frame. frame 'centre' = from the anchor's centre; 'base_lower_left' = from the lower-left
        corner of the face's extent."""
        du_, dv_ = 0.0, 0.0
        if frame == "base_lower_left":
            U, V = self.view_size()
            du_, dv_ = -U / 2, -V / 2
        elif frame != "centre":
            raise FeatureRequestError(f"outline frame must be 'base_lower_left' or 'centre', got {frame!r}")
        out = []
        for s in segs:
            t = dict(s)
            for kx, ky in (("x1", "y1"), ("x2", "y2"), ("cx", "cy")):
                if kx in t:
                    t[kx], t[ky] = self.local_to_sketch(s[kx] + du_, s[ky] + dv_)
            if t["kind"] == "arc" and self.view_mirrored:
                t["dir"] = -t["dir"]
            out.append(t)
        return out

    def to_datum_frame_shift(self) -> tuple[float, float]:
        raise FeatureRequestError(f"'on' {self.anchor.name}: a region on an anchor face is not supported")

    # ---------------------------------------------------------------- the straight part of a side (the corner-zone guard)
    def sketch_box_to_local(self, x0: float, x1: float, y0: float, y1: float) -> tuple[float, float, float, float]:
        """A box in the datum sketch frame -> (u_min, u_max, v_min, v_max) from the anchor's centre (u and v each run along one sketch axis)."""
        cx, cy = self._centre_sketch
        us, vs = [], []
        for x in (x0, x1):
            for y in (y0, y1):
                us.append((x - cx) * self.du[0] + (y - cy) * self.du[1])
                vs.append((x - cx) * self.dv[0] + (y - cy) * self.dv[1])
        return min(us), max(us), min(vs), max(vs)

    def flat_overhang(self, box_local: tuple[float, float, float, float]) -> tuple[float, float] | None:
        """How far a footprint reaches PAST the straight part of this side: (amount on the low side, amount on the high side), each >= 0, or None when the footprint lies
        on the straight part or the anchor has no flat range (a free end, a bend end, a round boss)."""
        if self.anchor.flat is None:
            return None
        lo_f, hi_f = self.anchor.flat
        u0, u1, v0, v1 = box_local
        lo, hi = (u0, u1) if self.anchor.flat_axis == "u" else (v0, v1)
        low, high = max(0.0, lo_f - lo), max(0.0, hi - hi_f)
        return (low, high) if low > 1e-6 or high > 1e-6 else None


# ------------------------------------------------------------------ the anchors a feature publishes
def _frame_of(feature) -> PlaneFrame:
    return PlaneFrame(feature.plane)


def _normal_text(axis_letter: str, sign: int) -> str:
    return ("+" if sign > 0 else "-") + axis_letter


DIR_WORD = {"+x": "right", "-x": "left", "+y": "up", "-y": "down", "+z": "front", "-z": "back"}


def _signed_area(profile: list[dict]) -> float:
    """Positive for a counter-clockwise outline (arcs sampled)."""
    from .revolve_boss import _sample
    pts = _sample(profile)
    return sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts))) / 2


def side_anchors(item_id: str, feature, frame: PlaneFrame, bounds: tuple[float, float, float, float], h0: float, h1: float) -> dict[str, Anchor]:
    """The flat SIDES of an extruded outline: one anchor per straight edge that runs along an axis. Size and centre come from the FULL OUTER outline
    (bounds), not from the straight part of the edge, so rounded corners do not move the edge words."""
    prof = feature.profile
    if any(s["kind"] == "circle" for s in prof):
        return {}
    x0, x1, y0, y1 = bounds
    ccw = _signed_area(prof) > 0
    (a_ax, sx), (b_ax, sy) = (frame.in_plane_axes[0], frame.signs[0]), (frame.in_plane_axes[1], frame.signs[1])
    hmid, depth = (h0 + h1) / 2, h1 - h0
    found = []
    for s in prof:
        if s["kind"] != "line":
            continue
        dx, dy = s["x2"] - s["x1"], s["y2"] - s["y1"]
        length = math.hypot(dx, dy)
        if length < 1e-9:
            continue
        tol = 1e-9 * max(1.0, length)
        turn = 1 if ccw else -1
        if abs(dx) <= tol:                                       # runs along the sketch y axis: faces left / right in the sketch
            n_s = (math.copysign(1.0, dy) * turn, 0.0)
            along, centre_sketch, span = b_ax, (s["x1"], (y0 + y1) / 2), y1 - y0
        elif abs(dy) <= tol:                                     # runs along the sketch x axis: faces up / down in the sketch
            n_s = (0.0, -math.copysign(1.0, dx) * turn)
            along, centre_sketch, span = a_ax, ((x0 + x1) / 2, s["y1"]), x1 - x0
        else:
            continue                                             # a sloped edge: no anchor
        n_world = [0.0, 0.0, 0.0]
        n_world[a_ax], n_world[b_ax] = sx * n_s[0], sy * n_s[1]
        axis = max(range(3), key=lambda i: abs(n_world[i]))
        normal = _normal_text("xyz"[axis], 1 if n_world[axis] > 0 else -1)
        centre = frame.to_world(centre_sketch[0], centre_sketch[1], hmid)
        edge_mid = frame.to_world((s["x1"] + s["x2"]) / 2, (s["y1"] + s["y2"]) / 2, hmid)       # where THIS edge really is: only used to order sides
        u_dir, v_dir = VIEW_AXES[normal]
        span_on_u = u_dir[along] != 0
        extent = (span, depth) if span_on_u else (depth, span)
        # the STRAIGHT part of this side along its edge, in local coordinates from the centre of the (full outer) side: where the real edge starts and ends
        e1 = frame.to_world(s["x1"], s["y1"], hmid)[along]
        e2 = frame.to_world(s["x2"], s["y2"], hmid)[along]
        direction = (u_dir if span_on_u else v_dir)[along]
        lo, hi = sorted(((e1 - centre[along]) * direction, (e2 - centre[along]) * direction))
        found.append((normal, round(centre[axis], 6), round(edge_mid[along], 6), centre, extent, (lo, hi), "u" if span_on_u else "v"))
    out: dict[str, Anchor] = {}
    by_dir: dict[str, list] = {}
    for item in found:
        by_dir.setdefault(item[0], []).append(item)
    for normal, items in by_dir.items():
        items.sort(key=lambda t: (t[1], t[2]))                  # the lowest position first
        word = DIR_WORD[normal]
        for k, (_, _, _, centre, extent, flat, flat_axis) in enumerate(items, start=1):
            name = f"{item_id}.{word}" if len(items) == 1 else f"{item_id}.{word}{k}"
            out[name] = Anchor(name, centre, normal, extent, flat, flat_axis)
    return out


def _with_direction_alias(item_id: str, name: str, anchor: Anchor, out: dict[str, Anchor]) -> None:
    """Publish a free end under its own name AND under the direction word it looks in (`top` stays the free end whichever way it looks)."""
    out[name] = anchor
    alias = f"{item_id}.{DIR_WORD[anchor.normal]}"
    if alias not in out:
        out[alias] = Anchor(alias, anchor.centre, anchor.normal, anchor.extent, anchor.flat, anchor.flat_axis)


def anchors_of(item_id: str, feature) -> dict[str, Anchor]:
    """The anchors an item publishes after it is built (offline, from the request values; nothing is read back from SolidWorks)."""
    from .circle_boss import ExtrudeCircleBoss
    from .extrude_profile_boss import ExtrudeProfileBoss
    from .revolve_on_face import RevolveOnFace
    from app.geometry import profile_bounds

    out: dict[str, Anchor] = {}
    if isinstance(feature, ExtrudeProfileBoss) and not feature.is_base:
        frame = _frame_of(feature)
        axis_letter = "xyz"[frame.normal_axis]
        grow = -1 if feature.reversed else 1
        h0, h1 = sorted((feature.offset, feature.offset + grow * feature.depth))
        top = feature.offset + grow * feature.depth
        bounds = profile_bounds(feature.profile)
        x0, x1, y0, y1 = bounds
        centre = frame.to_world((x0 + x1) / 2, (y0 + y1) / 2, top)
        out.update(side_anchors(item_id, feature, frame, bounds, h0, h1))
        _with_direction_alias(item_id, f"{item_id}.top", Anchor(f"{item_id}.top", centre, _normal_text(axis_letter, grow), (x1 - x0, y1 - y0)), out)
    elif isinstance(feature, ExtrudeCircleBoss):
        frame = _frame_of(feature)
        axis_letter = "xyz"[frame.normal_axis]
        grow = -1 if feature.reversed else 1
        top = feature.offset + grow * feature.depth
        k_unit = factor(feature.unit)
        for k, c in enumerate(feature.circles, start=1):
            sx_m, sy_m = c.position.to_sketch_m(feature.unit, feature.outline_wh)
            cx, cy = sx_m / k_unit, sy_m / k_unit
            name = f"{item_id}.top" if len(feature.circles) == 1 else f"{item_id}.top{k}"
            anchor = Anchor(name, frame.to_world(cx, cy, top), _normal_text(axis_letter, grow), (c.diameter, c.diameter))
            if len(feature.circles) == 1:
                _with_direction_alias(item_id, name, anchor, out)
            else:
                out[name] = anchor
    elif isinstance(feature, RevolveOnFace) and math.isclose(feature.angle_deg, 90.0, abs_tol=1e-9):
        if feature.is_round():
            d, _, cx, cy = feature._round_parts()
            u_extent, v_extent = d, d
        else:
            x0, x1, y0, y1 = feature.x_extent()
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            u_extent, v_extent = x1 - x0, y1 - y0
        gap = feature.axis_x - cx                                     # + : the section is left of the axis: the bend turns toward +x
        centre = (feature.axis_x, cy, feature.plane_height + feature.sweep * abs(gap))
        _with_direction_alias(item_id, f"{item_id}.end", Anchor(f"{item_id}.end", centre, "+x" if gap > 0 else "-x", (u_extent, v_extent)), out)
    return out
