"""The menu: feature name -> list-item parameters (params.py) + the ADAPTER that turns an item into the feature's own request.

The adapter only does what is generic and fixed by the pipeline's conventions:
- the unit comes from the list, the plane is the FRONT datum for every item (the reader's frame);
- outlines written in the BASE frame (origin = lower-left corner of the base outline) are moved to the datum's centred frame;
- `face` ("front" / "back" of the base plate) becomes a plane height and a direction: front = z at the base thickness, going into the
  material (-z) for a cut / hole, outward (+z) for a boss; back = z 0, going into the material (+z) or outward (-z);
- positions written with edge words get the base outline size.
It adds no geometry of its own and never guesses a number: anything missing is refused by the feature's own validation.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel

from app.features.base import FeatureRequestError, Position, factor
from app.features.circle_boss import BossCircle, ExtrudeCircleBoss
from app.features.cut_feature import CutFeature, CutShape
from app.features.cut_profile import ExtrudeProfileCut
from app.features.edge_feature import EdgeSelector, FilletChamferEdges
from app.features.extrude_profile_boss import ExtrudeProfileBoss
from app.features.anchors import AnchorView
from app.features.faces import FaceView
from app.features.hole import Hole
from app.features.rib import Rib
from app.features.revolve_base import RevolveBase, turned_base_info
from app.features.revolve_on_face import RevolveOnFace
from app.features.outline_helpers import round_polygon_corners, rounded_rectangle
from app.geometry import profile_bounds

from . import params as P
from . import patterns
from .tangent_chain import TangentChain, solve_chain

PLANE = "front"                     # the pipeline's convention: the reader's front view = the FRONT datum
TURNED_END_FACES_ENABLED = False    # items on the end faces (left / right) of a turned base: switched on only after a live axial-hole test passes


@dataclass
class BuildContext:
    """What later items need to know about the base plate. Filled in by the runner after the base item succeeds."""
    unit: str
    thickness: float | None = None                       # base thickness along z
    outline_wh: tuple[float, float] | None = None        # base outline size (W, H)
    frame_shift: tuple[float, float] | None = None       # base-frame point that becomes the datum origin: (x0 + W/2, y0 + H/2)
    turned: bool = False                                 # the base is a revolve_base: its front / back / top / bottom are not flat faces
    anchors: dict = field(default_factory=dict)          # name -> Anchor: the flat faces earlier items created ("F6.top", "F5.end"); filled by the runner's prepare()

    @property
    def has_base(self) -> bool:
        return self.thickness is not None

    def need_base(self, what: str) -> None:
        if not self.has_base:
            raise FeatureRequestError(f"{what} needs a base plate first: the first item must be an extrude_profile_boss without a face")


# ------------------------------------------------------------------ small helpers
def _position(p: P.Pos) -> Position:
    return Position(p.x, p.y, p.x_from, p.y_from)


def _face(ctx: BuildContext, face: str, what: str) -> tuple[float, str, bool]:
    """(plane height, direction INTO the material for a cut, a boss must be reversed?) for a face of the base plate."""
    ctx.need_base(what)
    if face == "front":
        return ctx.thickness, "negative", False
    return 0.0, "positive", True


@dataclass(frozen=True)
class Where:
    """Where an item sits: the datum plane, the face height along its normal, the way INTO the material, the boss flag, and how a position
    printed on that face reaches the feature. front / back: the proven path (positions from the base outline, `outline_wh`). top / bottom:
    a FaceView converts every position to the datum's sketch frame, so the features get plain centre-based positions."""
    plane: str
    offset: float
    into: str
    reversed: bool
    target: str
    cut_target: str
    outline_wh: tuple[float, float] | None
    pos: Callable[[Any], Position]
    view: FaceView | None = None


def where(ctx: BuildContext, face: str, what: str, boss: bool = False, lift: float = 0.0) -> Where:
    """`boss=True` for items that grow OUT of the face: only then is the boss direction worked out. `lift`: the plane sits this far beyond the face
    (the flat top of a boss). A downward face that is not on the datum (bottom, left, or the back face lifted) is sketched ON the face."""
    if ctx.turned and not (face in ("left", "right") and TURNED_END_FACES_ENABLED):
        raise FeatureRequestError(f"{what}: after a turned base (revolve_base) no other item can be built yet: its front, back, top and bottom are curved, and its end "
                                  f"faces have not been tested live (a cross hole needs work that is not built): needs_human")
    if face in ("front", "back"):
        offset, into, reversed_ = _face(ctx, face, what)
        outward = 1 if face == "front" else -1
        offset += outward * lift
        downward_offset = outward < 0 and abs(offset) > 1e-12
        return Where(PLANE, offset, into, reversed_, "face" if (boss and downward_offset) else "plane", "face" if downward_offset else "plane",
                     ctx.outline_wh, _position)
    ctx.need_base(what)
    if ctx.outline_wh is None:
        raise FeatureRequestError(f"{what} needs the base outline size")
    fv = FaceView(face, ctx.outline_wh[0], ctx.outline_wh[1], ctx.thickness, lift)
    reversed_, target = fv.boss_orientation(what) if boss else (False, "plane")
    return Where(fv.datum, fv.plane_height, fv.into, reversed_, target, fv.cut_target, None, lambda q: fv.centred(_position(q)), fv)


def where_on(ctx: BuildContext, name: str, what: str, boss: bool = False, lift: float = 0.0) -> Where:
    """An item placed ON a face an earlier item created (an anchor): the anchor becomes a datum plane + height + direction + position conversion, exactly like a
    side face of the base (see features/anchors.py for the local directions)."""
    if ctx.turned:
        raise FeatureRequestError(f"{what}: after a turned base (revolve_base) no other item can be built yet: needs_human")
    anchor = ctx.anchors.get(name)
    if anchor is None:
        known = sorted(ctx.anchors)
        raise FeatureRequestError(f"{what}: 'on' {name!r} is not a face made by an EARLIER item (known: {known or 'none yet'}); anchors are named <item id>.top for the free end of a boss, "
                                  f"<item id>.up / .down / .left / .right / .front / .back for a flat side of an extruded boss (several: right1, right2 ...) "
                                  f"and <item id>.end for the open end of a 90-degree bend")
    view = AnchorView(anchor, lift)
    reversed_, target = view.boss_orientation(what) if boss else (False, "plane")
    return Where(view.datum, view.plane_height, view.into, reversed_, target, view.cut_target, None, lambda q: view.centred(_position(q)), view)


def where_for(ctx: BuildContext, face: str | None, on: str | None, offset: float, what: str, boss: bool = False) -> Where:
    """The place of an item: a face of the base (`face`, with an optional `offset` lift) or a face an earlier item made (`on`)."""
    if on is not None:
        if face is not None:
            raise FeatureRequestError(f"{what}: give `face` OR `on`, not both")
        return where_on(ctx, on, what, boss, offset)
    if face is None:
        raise FeatureRequestError(f"{what}: give `face` (a face of the base) or `on` (a face an earlier item made)")
    return where(ctx, face, what, boss, offset)


def _face_outline(p: Any, w: Where) -> list[dict]:
    """An outline on a top / bottom / left / right face, in the datum's sketch frame. A rectangle is placed with the edge words (its centre);
    a polygon or explicit segments are written as seen looking AT the face from outside, from its lower-left corner (see FaceView.view_to_sketch)."""
    if isinstance(p, P.Ring):
        cx, cy = w.view.centre() if p.position is None else w.view.to_sketch(_position(p.position))
        return _ring_circles(cx, cy, p)
    if isinstance(p, P.Rectangle):
        cx, cy = w.view.centre() if p.position is None else w.view.to_sketch(_position(p.position))
        return rounded_rectangle(cx, cy, p.width, p.height, p.corner_radius)
    if isinstance(p, TangentChain):
        raise FeatureRequestError("a tangent_chain outline is built on the front or back face only (use explicit segments on the other faces)")
    segs = round_polygon_corners([tuple(pt) for pt in p.points], p.corner_radius) if isinstance(p, P.Polygon) else [s.model_dump() for s in p.segments]
    return w.view.map_segments(segs, p.frame)


def _shift(segs: list[dict], dx: float, dy: float) -> list[dict]:
    out = []
    for s in segs:
        t = dict(s)
        for kx, ky in (("x1", "y1"), ("x2", "y2"), ("cx", "cy")):
            if kx in t:
                t[kx], t[ky] = t[kx] - dx, t[ky] - dy
        out.append(t)
    return out


def _ring_circles(cx: float, cy: float, p: Any) -> list[dict]:
    """A ring as two concentric circle entries (outer first), in the datum's centred frame, drawing unit."""
    return [{"kind": "circle", "diameter": p.outer_diameter, "cx": cx, "cy": cy}, {"kind": "circle", "diameter": p.inner_diameter, "cx": cx, "cy": cy}]


def _raw_outline(p: Any, ctx: BuildContext) -> tuple[list[dict], bool]:
    """(segments, already in the datum's centred frame?)"""
    if isinstance(p, P.Ring):
        cx = cy = 0.0
        if p.position is not None:
            f = factor(ctx.unit)
            cx, cy = (v / f for v in _position(p.position).to_sketch_m(ctx.unit, ctx.outline_wh))
        return _ring_circles(cx, cy, p), True
    if isinstance(p, P.Rectangle):
        cx, cy = 0.0, 0.0
        if p.position is not None:
            f = factor(ctx.unit)
            cx, cy = (v / f for v in _position(p.position).to_sketch_m(ctx.unit, ctx.outline_wh))
        return rounded_rectangle(cx, cy, p.width, p.height, p.corner_radius), True
    if isinstance(p, P.Polygon):
        return round_polygon_corners([tuple(pt) for pt in p.points], p.corner_radius), p.frame == "centre"
    if isinstance(p, TangentChain):
        return solve_chain(p), p.frame == "centre"
    return [s.model_dump() for s in p.segments], p.frame == "centre"


def resolve_outline(p: Any, ctx: BuildContext) -> list[dict]:
    """The outline in the datum's centred frame (drawing unit). A base outline is centred on its own bounding box."""
    segs, centred = _raw_outline(p, ctx)
    if centred:
        return segs
    dx, dy = ctx.frame_shift if ctx.frame_shift is not None else _own_shift(segs)
    return _shift(segs, dx, dy)


def _own_shift(segs: list[dict]) -> tuple[float, float]:
    x0, x1, y0, y1 = profile_bounds(segs)
    return x0 + (x1 - x0) / 2, y0 + (y1 - y0) / 2


def _half_section(p: Any) -> list[dict]:
    """The (u, r) half-section of a revolve_base: x = u along the axis, y = r from the axis (frame base_lower_left only: the axis is y = 0)."""
    if isinstance(p, P.Ring):
        raise FeatureRequestError("a half-section cannot be a ring: write the half-section outline (a bore is part of it)")
    if isinstance(p, P.Rectangle):
        if p.position is not None:
            raise FeatureRequestError("a half-section rectangle has no position: its corner is at u = 0, r = 0")
        return rounded_rectangle(p.width / 2, p.height / 2, p.width, p.height, p.corner_radius)
    if p.frame != "base_lower_left":
        raise FeatureRequestError("a half-section is written with frame base_lower_left: u from the end of the part, r from the axis")
    if isinstance(p, P.Polygon):
        return round_polygon_corners([tuple(pt) for pt in p.points], p.corner_radius)
    if isinstance(p, TangentChain):
        return solve_chain(p)
    return [s.model_dump() for s in p.segments]


def base_info(params: Any, unit: str) -> tuple[tuple[float, float], tuple[float, float], float]:
    """((W, H), frame_shift, thickness) of a base item (a plate outline, or a turned part)."""
    if isinstance(params, P.RevolveBaseParams):
        return turned_base_info(_half_section(params.profile), params.axis)
    ctx = BuildContext(unit)
    segs, centred = _raw_outline(params.profile, ctx)
    x0, x1, y0, y1 = profile_bounds(segs)
    # base frame (origin = lower-left corner of the outline) -> datum: a centred outline has its corner at (x0, y0) of the datum frame
    shift = (-x0, -y0) if centred else _own_shift(segs)
    return (x1 - x0, y1 - y0), shift, params.depth


def _all_inside(pts: list[tuple[float, float]], size: tuple[float, float], what: str, where: str, unit: str) -> None:
    """Every position (centred frame, drawing unit) must lie on the face it is printed on: a centre outside it builds nothing (found offline,
    before SolidWorks is touched). Side faces are checked by their own corner-zone guard."""
    half_w, half_h = size[0] / 2 + 1e-6, size[1] / 2 + 1e-6
    outside = [(round(x, 3), round(y, 3)) for x, y in pts if abs(x) > half_w or abs(y) > half_h]
    if outside:
        raise FeatureRequestError(f"{what}: {len(outside)} position(s) lie outside {where}, e.g. {outside[0]} (from its centre, {unit}): "
                                  f"check the numbers and which edge they are measured from")


def _positions(seeds: list[P.Pos], pattern: Any, ctx: BuildContext, w: Where, what: str) -> list[Position]:
    """The feature positions for explicit seed positions plus an optional pattern. No pattern: the proven path (`w.pos`). With a pattern (front / back faces
    only): the pattern is expanded in the datum's centred frame and the feature gets plain centre-based positions; each must lie inside the base's bounding box."""
    if pattern is None:                                     # the proven path, with the same "inside the face" check a pattern gets
        if isinstance(w.view, AnchorView) and w.view.anchor.extent is not None:
            _all_inside([w.view.to_local(_position(q)) for q in seeds], w.view.anchor.extent, what, f"the face {w.view.anchor.name}", ctx.unit)
        elif w.view is None and ctx.outline_wh is not None:
            f = factor(ctx.unit)
            _all_inside([tuple(v / f for v in _position(q).to_sketch_m(ctx.unit, ctx.outline_wh)) for q in seeds], ctx.outline_wh, what,
                        "the base outline", ctx.unit)
        return [w.pos(q) for q in seeds]
    if isinstance(w.view, AnchorView):                      # a pattern on an anchor face: expanded in the face's own (u, v), as seen from outside
        view = w.view

        def uv(q: P.Pos) -> tuple[float, float]:
            return view.to_local(_position(q))

        pts = patterns.expand([uv(q) for q in seeds], pattern, uv(pattern.centre) if pattern.kind == "circular" else None)
        if view.anchor.extent is not None:
            half_u, half_v = view.anchor.extent[0] / 2 + 1e-6, view.anchor.extent[1] / 2 + 1e-6
            outside = [(u, v) for u, v in pts if abs(u) > half_u or abs(v) > half_v]
            if outside:
                raise FeatureRequestError(f"{what}: the pattern puts {len(outside)} position(s) outside the face {view.anchor.name}, e.g. {outside[0]} "
                                          f"(from its centre, {ctx.unit}): check count, spacing, direction and start angle")
        return [Position(*view.local_to_sketch(u, v)) for u, v in pts]
    if w.view is not None:
        raise FeatureRequestError(f"{what}: a pattern is built on the front or back face or on an anchor face only (list the positions one by one on other faces)")
    f = factor(ctx.unit)

    def xy(q: P.Pos) -> tuple[float, float]:
        sx, sy = _position(q).to_sketch_m(ctx.unit, ctx.outline_wh)
        return sx / f, sy / f

    pts = patterns.expand([xy(q) for q in seeds], pattern, xy(pattern.centre) if pattern.kind == "circular" else None)
    if ctx.outline_wh is not None:
        half_w, half_h = ctx.outline_wh[0] / 2 + 1e-6, ctx.outline_wh[1] / 2 + 1e-6
        outside = [(x, y) for x, y in pts if abs(x) > half_w or abs(y) > half_h]
        if outside:
            raise FeatureRequestError(f"{what}: the pattern puts {len(outside)} position(s) outside the base outline, e.g. {outside[0]} (centred frame, {ctx.unit}): "
                                      f"check count, spacing, direction and start angle")
    return [Position(x, y) for x, y in pts]


def _corner_zone(w: Where, what: str, boxes: list[tuple[float, float, float, float]], override: bool) -> bool:
    """THE CORNER-ZONE GUARD. A hole, cut or ring cut placed on a SIDE of an extruded boss must lie on the straight part of that side. Where the side is rounded (R corners)
    or stepped away, the shape would cut less material than its full size and the exact volume check would fail. `boxes`: the footprints in the datum sketch frame.
    Returns False when everything lies on the straight part. Otherwise: a human override (`edge_zone_ok`) returns True (the item is then checked as an upper bound);
    without it the item is refused with a plain message (needs_human)."""
    view = w.view
    if not isinstance(view, AnchorView) or view.anchor.flat is None:
        return False                                            # a free end, a bend end, a round boss, a face of the base: nothing to guard
    worst, hit = 0.0, None
    for b in boxes:
        over = view.flat_overhang(view.sketch_box_to_local(*b))
        if over and max(over) > worst:
            worst, hit = max(over), over
    if hit is None:
        return False
    if override:
        return True
    lo, hi = view.anchor.flat
    where_ = "right (+) / left (-) of" if view.anchor.flat_axis == "u" else "above (+) / below (-)"
    side = "low (left / bottom) end" if hit[0] >= hit[1] else "high (right / top) end"
    raise FeatureRequestError(
        f"{what} on {view.anchor.name}: it reaches {worst:.2f} mm past the {side} of the STRAIGHT part of that side. The straight part runs from {lo:g} to "
        f"{hi:g} mm, {where_} the centre of the side; beyond that the side is rounded or stepped away, so the shape would cut less material than its full size. Move it at least "
        f"{worst:.2f} mm further in. A human can accept a partial cut by setting `edge_zone_ok: true` on this item (its volume is then only checked as an upper bound): needs_human")


def _shape_half_extents(s: CutShape) -> tuple[float, float]:
    """Half the size of a cut shape's footprint along the sketch x and y axes (drawing unit)."""
    if s.kind == "circle":
        return s.diameter / 2, s.diameter / 2
    if s.kind == "rectangle":
        return s.width / 2, s.height / 2
    a = math.radians(s.angle_deg)                                # a slot (stadium) along `angle_deg`
    return s.centre_distance / 2 * abs(math.cos(a)) + s.width / 2, s.centre_distance / 2 * abs(math.sin(a)) + s.width / 2


def _end_and_length(depth: Any, material_length: float | None) -> tuple[str, float | None, float | None]:
    """(end, depth, material_length) for a cut: 'through' needs the thickness it crosses, a blind cut crosses its own depth."""
    if depth == "through":
        if material_length is None:
            raise FeatureRequestError("a through cut needs material_length (the thickness it crosses): the volume check is exact only then, and the "
                                      "pipeline does not guess it: needs_human")
        return "through", None, material_length
    return "blind", float(depth), material_length if material_length is not None else float(depth)


# ------------------------------------------------------------------ adapters: params -> the feature's request
def build_extrude_profile_boss(p: P.ExtrudeProfileBossParams, ctx: BuildContext, label: str) -> ExtrudeProfileBoss:
    if p.face is None and p.on is None:
        if ctx.has_base:
            raise FeatureRequestError("a second base: only the first item may be an extrude_profile_boss without a face")
        return ExtrudeProfileBoss(unit=ctx.unit, plane=PLANE, profile=resolve_outline(p.profile, ctx), depth=p.depth, offset=0.0,
                                  reversed=False, origin="centre", label=label)
    w = where_for(ctx, p.face, p.on, p.offset, "a boss on a face", boss=True)
    profile = resolve_outline(p.profile, ctx) if w.view is None else _face_outline(p.profile, w)
    return ExtrudeProfileBoss(unit=ctx.unit, plane=w.plane, profile=profile, depth=p.depth, offset=w.offset, reversed=w.reversed, target=w.target,
                              is_base=False, origin="centre", label=label)


def build_cut(p: P.CutParams, ctx: BuildContext, label: str) -> CutFeature:
    w = where_for(ctx, p.face, p.on, p.offset, "a cut")
    end, depth, length = _end_and_length(p.depth, p.material_length)
    shapes = [CutShape(s.kind, pos, diameter=s.diameter, centre_distance=s.centre_distance, width=s.width, height=s.height, angle_deg=s.angle_deg)
              for s in p.shapes for pos in _positions([] if s.position is None else [s.position], s.pattern, ctx, w, "a cut")]
    boxes = [(c.position.x - hx, c.position.x + hx, c.position.y - hy, c.position.y + hy) for c in shapes for hx, hy in [_shape_half_extents(c)]] if isinstance(w.view, AnchorView) else []
    partial = _corner_zone(w, "a cut", boxes, p.edge_zone_ok)
    return CutFeature(ctx.unit, w.plane, shapes, end=end, depth=depth, target="face", offset=w.offset, into=w.into, material_length=length,
                      outline_wh=w.outline_wh, bound_only=partial, label=label + (" (partial: upper-bound volume)" if partial else ""))


def build_hole(p: P.HoleParams, ctx: BuildContext, label: str) -> Hole:
    w = where_for(ctx, p.face, p.on, p.offset, "a hole")
    end = "through" if p.depth == "through" else "blind"
    positions = _positions(p.positions, p.pattern, ctx, w, "a hole")
    r = max(p.diameter, p.counterbore_diameter or 0.0, p.countersink_diameter or 0.0) / 2          # the WIDEST part of the hole decides
    boxes = [(q.x - r, q.x + r, q.y - r, q.y + r) for q in positions] if isinstance(w.view, AnchorView) else []
    partial = _corner_zone(w, "a hole", boxes, p.edge_zone_ok)
    return Hole(unit=ctx.unit, plane=w.plane, positions=positions, diameter=p.diameter, style=p.style, end=end,
                depth=None if end == "through" else float(p.depth), material_length=p.material_length, target="face", offset=w.offset, into=w.into,
                counterbore_diameter=p.counterbore_diameter, counterbore_depth=p.counterbore_depth,
                countersink_diameter=p.countersink_diameter, countersink_angle_deg=p.countersink_angle_deg, thread_note=p.thread_note,
                outline_wh=w.outline_wh, floor_open=p.floor_open, bound_only=partial, label=label + (" (partial: upper-bound volume)" if partial else ""))


def build_circle_boss(p: P.CircleBossParams, ctx: BuildContext, label: str) -> ExtrudeCircleBoss:
    w = where_for(ctx, p.face, p.on, p.offset, "a circle boss", boss=True)
    return ExtrudeCircleBoss(unit=ctx.unit, plane=w.plane, circles=[BossCircle(c.diameter, pos) for c in p.circles
                                      for pos in _positions([] if c.position is None else [c.position], c.pattern, ctx, w, "a circle boss")], depth=p.height,
                             offset=w.offset, reversed=w.reversed, target=w.target, outline_wh=w.outline_wh, label=label)


def build_profile_cut(p: P.ProfileCutParams, ctx: BuildContext, label: str) -> ExtrudeProfileCut:
    w = where_for(ctx, p.face, p.on, p.offset, "a profile cut")
    end, depth, length = _end_and_length(p.depth, p.material_length)
    profile = resolve_outline(p.profile, ctx) if w.view is None else _face_outline(p.profile, w)
    boxes = [profile_bounds(profile)] if isinstance(w.view, AnchorView) else []
    partial = _corner_zone(w, "a ring cut" if isinstance(p.profile, P.Ring) else "a profile cut", boxes, p.edge_zone_ok)
    return ExtrudeProfileCut(unit=ctx.unit, plane=w.plane, profile=profile, end=end, depth=depth, offset=w.offset, into=w.into,
                             target=w.cut_target, origin="centre", material_length=length, bound_only=partial,
                             label=label + (" (partial: upper-bound volume)" if partial else ""))


def build_revolve_base(p: P.RevolveBaseParams, ctx: BuildContext, label: str) -> RevolveBase:
    if ctx.has_base:
        raise FeatureRequestError("a second base: only the first item may be a base")
    return RevolveBase(ctx.unit, p.axis, _half_section(p.profile), label=label)


def build_revolve_on_face(p: P.RevolveOnFaceParams, ctx: BuildContext, label: str) -> RevolveOnFace:
    w = where(ctx, p.face, "a revolve on a face", lift=p.offset)
    f = factor(ctx.unit)
    if isinstance(p.section, P.CircleSection):
        sx, sy = _position(p.section.position).to_sketch_m(ctx.unit, ctx.outline_wh)
        cx, cy = sx / f, sy / f
        section = [{"kind": "circle", "diameter": p.section.diameter, "cx": cx, "cy": cy}]
        if p.section.bore_diameter is not None:
            section.append({"kind": "circle", "diameter": p.section.bore_diameter, "cx": cx, "cy": cy})
    else:
        section = resolve_outline(p.section, ctx)
    axis_x = Position(p.axis.x, 0.0, p.axis.x_from, "centre").to_sketch_m(ctx.unit, ctx.outline_wh)[0] / f
    return RevolveOnFace(ctx.unit, w.offset, 1 if p.face == "front" else -1, section, axis_x, p.angle_deg, ctx.outline_wh, label=label)


def build_rib(p: P.RibParams, ctx: BuildContext, label: str) -> Rib:
    if ctx.turned:
        raise FeatureRequestError("a rib after a turned base (revolve_base) is not built: needs_human")
    ctx.need_base("a rib")
    return Rib(ctx.unit, _position(p.axis), p.boss_diameter, p.boss_height, p.thickness, p.foot_distance, p.height_at_boss, ctx.thickness,
               sides=p.sides, foot_fillet=p.foot_fillet, outline_wh=ctx.outline_wh, label=label)


def build_edges(p: P.EdgeParams, ctx: BuildContext, label: str) -> FilletChamferEdges:
    w = where(ctx, p.face, "an edge feature", lift=p.offset)
    region = None
    if p.region is not None:
        if w.view is None:
            dx, dy = ctx.frame_shift
        else:                                    # measured from the face's lower-left corner in its datum sketch frame
            dx, dy = (-v for v in w.view.to_datum_frame_shift())
        region = (p.region[0] - dx, p.region[1] - dx, p.region[2] - dy, p.region[3] - dy)
    selector = EdgeSelector(w.plane, w.offset, region=region, kinds=tuple(p.kinds), radius=p.radius, length=p.length, tol=p.tol)
    return FilletChamferEdges(ctx.unit, p.kind, p.size, selector, expect_count=p.expect_count, label=label)


# ------------------------------------------------------------------ the menu
@dataclass(frozen=True)
class MenuEntry:
    name: str
    params_model: type[BaseModel]
    summary: str
    description: str
    limits: tuple[str, ...]
    example: dict
    build: Callable[[Any, BuildContext, str], Any]
    internal: frozenset[str] = field(default_factory=frozenset)    # request fields the adapter sets itself (unit, plane, label, ...)

    @property
    def feature_class(self) -> type:
        return {"extrude_profile_boss": ExtrudeProfileBoss, "cut": CutFeature, "hole": Hole, "circle_boss": ExtrudeCircleBoss,
                "profile_cut": ExtrudeProfileCut, "rib": Rib, "fillet_chamfer_edges": FilletChamferEdges,
                "revolve_base": RevolveBase, "revolve_on_face": RevolveOnFace}[self.name]


_COMMON = frozenset({"unit", "plane", "label", "regression_delta_mm3", "outline_wh"})

MENU: dict[str, MenuEntry] = {e.name: e for e in [
    MenuEntry(
        "extrude_profile_boss", P.ExtrudeProfileBossParams,
        "Extrude a flat outline into a solid: the base plate, or a boss on its front/back face or on a face placed with `on`.",
        "The FIRST item of every list is the base: an outline (rectangle, polygon with optional rounded corners, explicit lines and arcs, or a "
        "`tangent_chain` of circles) extruded by `depth` (the thickness) from the back face toward the viewer. With `face` set it adds a boss that grows outward from that "
        "face of the base; with `on` it grows outward from a flat face made by an earlier item.",
        ("the first item must be the base (no face); only one base",
         "a curved outline whose arc end points are NOT printed (only radii, centres, 'tangent') is a `tangent_chain`; explicit `segments` need every end point printed or derived",
         "a `tangent_chain` outline works on the front and back faces only", "no draft, no taper"),
        {"profile": {"shape": "rectangle", "width": 120, "height": 80, "corner_radius": 10}, "depth": 10},
        build_extrude_profile_boss, _COMMON | {"mode", "reversed", "origin", "target", "is_base"}),
    MenuEntry(
        "cut", P.CutParams,
        "Cut circles, slots or rectangles into a face: through or blind pockets.",
        "Removes material under each shape, starting from the front or back face of the base, or from a face placed with `on` (a flat face made by an earlier item). `depth` is a number (blind) or 'through'. "
        "A through cut needs `material_length`, the thickness it crosses.",
        ("shapes must not overlap each other", "rotated rectangles are not supported", "a counterbore or countersink is a `hole`, not a cut",
         "a shape's `pattern` works on the front and back faces and on a face placed with `on`; on the other faces of the base list every position; a circular pattern takes no `position`"),
        {"shapes": [{"kind": "rectangle", "position": {"x": 0, "y": 0}, "width": 30, "height": 20}], "face": "front", "depth": 5},
        build_cut, _COMMON | {"end", "target", "into", "face_index", "floor_open", "bound_only"}),
    MenuEntry(
        "hole", P.HoleParams,
        "Holes of one spec: plain, counterbore or 90-degree countersink; a threaded hole is a plain hole at the nominal size plus a note.",
        "Drills every position in `positions` from the front or back face of the BASE PLATE, or from a face placed with `on` (a flat face made by an earlier item). `depth` is a number (blind, flat bottom) or 'through'; a through hole "
        "needs `material_length`. A bore that goes through the plate AND a boss standing on its front face is drilled from the BACK face (`face` back, "
        "`depth` through, `material_length` = plate thickness + boss height). A counterbore needs counterbore_diameter and counterbore_depth, a countersink needs countersink_diameter.",
        ("countersink angle other than 90 is refused", "no real threads: the thread note is only carried along", "blind holes are flat bottomed",
         "`through` cuts through ALL the material along the line: if solid lies beyond the thickness meant (for example the bore must stop at the open end of a bend), use a "
         "blind `depth` equal to that length instead of 'through'",
         "`pattern` (linear, grid, circular, mirror) works on the front and back faces and on a face placed with `on`; on the other faces of the base list every position; a circular pattern takes no `positions`"),
        {"positions": [{"x": 15, "x_from": "left_edge", "y": 15, "y_from": "bottom_edge"}], "diameter": 8, "face": "front", "depth": "through", "material_length": 10},
        build_hole, _COMMON | {"target", "into", "end", "bound_only"}),
    MenuEntry(
        "circle_boss", P.CircleBossParams,
        "Round bosses (pins, hubs) standing on the front or back face or on a face placed with `on`.",
        "Pushes each circle out of the chosen face of the base, or of a flat face made by an earlier item (placed with `on`), by `height`.",
        ("round bosses only: other boss shapes use extrude_profile_boss with a face",
         "a circle's `pattern` works on the front and back faces and on a face placed with `on`; on the other faces of the base list every position; a circular pattern takes no `position`"),
        {"circles": [{"diameter": 60, "position": {"x": 0, "y": 0}}], "height": 35, "face": "front"},
        build_circle_boss, _COMMON | {"depth", "mode", "reversed", "target", "new_material_length", "expect_bodies"}),
    MenuEntry(
        "profile_cut", P.ProfileCutParams,
        "Cut any closed outline (windows with rounded corners, notches, profile pockets) into a face of the base or a face placed with `on`.",
        "Like `cut`, but the shape is a rectangle with optional rounded corners, a polygon, explicit lines and arcs, a ring (an annular groove) or a `tangent_chain` of circles "
        "(a curved slot, an arc-shaped window). It starts from a face of the base, or from a flat face made by an earlier item (placed with `on`).",
        ("the outline must be closed and must not cross itself", "a `tangent_chain` outline works on the front and back faces only"),
        {"profile": {"shape": "rectangle", "width": 20, "height": 12, "corner_radius": 3, "position": {"x": 40, "y": 30}}, "face": "front", "depth": 4},
        build_profile_cut, _COMMON | {"end", "into", "origin", "target", "bound_only"}),
    MenuEntry(
        "rib", P.RibParams,
        "Triangular ribs (gussets) between the front face of the plate and a round boss standing on it, along x.",
        "One or two ribs on the left / right of a round boss. Each is a triangle seen from the side: its foot lies on the plate face at "
        "`foot_distance` from the boss axis, its sloped edge meets the boss wall at `height_at_boss`. `thickness` is measured across the rib "
        "(along y). Put it AFTER the boss and BEFORE any bore through the boss. The rib is centred on the boss axis. A fillet printed at the rib feet "
        "is `foot_fillet` of this item, never a separate fillet_chamfer_edges item.",
        ("round boss only; ribs run along x (a rib along y, at an angle or with a curved or stepped profile is `unsupported`)",
         "straight sloped edge only, no fillet on the rib itself (a fillet at its foot is a fillet_chamfer_edges item)",
         "the rib must fit inside the plate outline and below the boss top"),
        {"axis": {"x": 0, "y": 0}, "boss_diameter": 60, "boss_height": 35, "thickness": 20, "foot_distance": 45, "height_at_boss": 15, "sides": "both"},
        build_rib, _COMMON | {"plate_thickness"}),
    MenuEntry(
        "fillet_chamfer_edges", P.EdgeParams,
        "Round (fillet) or bevel (45-degree chamfer) edges chosen by their location on a face of the base.",
        "Selects the edges that lie in the plane of the front or back face, optionally only inside `region` or only lines / arcs / circles of a given "
        "radius or length. SolidWorks rounds a whole chain of tangent edges together, so the whole chain is included. Give `expect_count` so a "
        "wrong selection stops the build.",
        ("chamfers are equal on both faces (two distances are not built)", "the edges must lie in the plane of the base's front or back face"),
        {"kind": "fillet", "size": 2, "face": "front", "kinds": ["line", "arc"], "expect_count": 4},
        build_edges, _COMMON | {"distance2", "max_edges", "selector"}),
    MenuEntry(
        "revolve_base", P.RevolveBaseParams,
        "A turned part (shaft, bushing, pin, flanged sleeve) revolved 360 degrees from its half-section: the FIRST item, instead of an extruded plate.",
        "Use it when the drawing is a lathe part: a half-section with steps, cones, grooves, fillets and perhaps a bore, turned about an axis. Write the half-section as a "
        "closed outline in (u, r): u along the axis, r the radius measured from the axis (r >= 0), frame base_lower_left (u from one end of the part, r from the axis). "
        "A bore is part of the outline (it stays away from the axis). `axis` says whether the axis is horizontal or vertical in the plate view. Exactly the outline's edges "
        "become the faces; at most one outline edge may lie on the axis.",
        ("it can only be the FIRST item; after it NO other item can be built yet (cross holes, flats and end-face features are `unsupported`)",
         "full 360 degrees only; a groove or bore is drawn into the half-section, never cut afterwards",
         "the part spans z = 0 .. 2 x the largest radius (the pipeline's back face is z = 0)"),
        {"profile": {"shape": "polygon", "points": [[0, 0], [40, 0], [40, 25], [0, 25]]}, "axis": "horizontal"},
        build_revolve_base, _COMMON | set()),
    MenuEntry(
        "revolve_on_face", P.RevolveOnFaceParams,
        "Bend or ring: a section drawn on the front or back face revolved about a vertical axis in that face's plane (pipe elbow, ring, washer; hollow with a bore).",
        "Draw the section ON the face: a circle (`bore_diameter` makes it a hollow pipe wall) or any closed outline, lying clear of a VERTICAL axis (`axis`, seen in the plate "
        "view), and revolve it by `angle_deg` (45, 90, 135 or 180). The solid grows away from the plate: toward the viewer from the front face, away from it from the back face. The "
        "section must lie on the base's face (it starts there and merges into the plate).",
        ("the axis is vertical and lies in the plane of the face; an axis perpendicular to the face, a horizontal axis and a revolve CUT are `unsupported`",
         "only 45, 90, 135 and 180 degrees are built; the section must lie clear of the axis (not touching it) and inside the base outline",
         "the far end of a bend (for example a second flange) is a separate feature on another face and is not built by this one"),
        {"section": {"shape": "circle", "diameter": 20, "bore_diameter": 12, "position": {"x": -20, "y": 0}}, "axis": {"x": 10}, "face": "front", "angle_deg": 90},
        build_revolve_on_face, _COMMON | {"plane_height", "sweep", "axis_x", "expect_bodies"}),
]}
