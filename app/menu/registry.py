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
from app.features.blades import RadialBlades, TurnedKeyway
from app.features.standalone import Loft, Section, SheetMetalBase, SweptTube
from app.features.ext_features import AngledBoss, Drill, Shell, SideCut, SideProfileCut, SweptPassage
from app.features.turned_ops import MM3_PER_UNIT3 as _MM3
from app.features.turned_ops import CrossHole, TurnedAxial
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
    turned_profile: list | None = None                   # that base's half-section (u, r) and axis: what turned_axial / cross_hole build on
    turned_axis: str | None = None
    base_feature: Any = None                             # the base item's feature (a shell checks it is a plain box or cylinder)
    anchors: dict = field(default_factory=dict)          # name -> Anchor: the flat faces earlier items created ("F6.top", "F5.end"); filled by the runner's prepare()
    height_varies: bool = False                          # a side_profile_cut shaped the base: a through cut from front / back crosses different heights

    def varying_through(self, face: Any, end: str) -> bool:
        """A through cut from the front / back of a base whose height varies: its exact volume is unknown, so it is checked as 'removes material'."""
        return self.height_varies and end == "through" and face in ("front", "back")

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
        if abs(p.angle_deg) > 1e-9:
            raise FeatureRequestError("a turned rectangle is drawn on the front or back face only")
        return rounded_rectangle(cx, cy, p.width, p.height, p.corner_radius)
    if isinstance(p, TangentChain):
        raise FeatureRequestError("a tangent_chain outline is built on the front or back face only (use explicit segments on the other faces)")
    if isinstance(p, P.CURVE_SHAPES):
        raise FeatureRequestError(f"a {p.shape} outline is built on the front or back face only")
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


def curve_outline(p: Any, cx: float = 0.0, cy: float = 0.0) -> list[dict]:
    """A formula-defined outline (gear, trochoid, rotor) as lines and arcs, centred at (cx, cy) in the drawing unit."""
    from app import curves
    try:
        if isinstance(p, P.SpurGear):
            out = curves.spur_gear(p.teeth, p.module, p.pressure_angle_deg, p.internal, tol=p.chord_tolerance, x=cx, y=cy, rotation_deg=p.rotation_deg)
        elif isinstance(p, P.Epitrochoid):
            out = curves.epitrochoid(p.generating_radius, p.eccentricity, clearance=p.clearance, tol=p.chord_tolerance, x=cx, y=cy, rotation_deg=p.rotation_deg)
        else:
            out = curves.rotor_flank(p.generating_radius, p.eccentricity, clearance=p.clearance, tol=p.chord_tolerance, x=cx, y=cy, rotation_deg=p.rotation_deg)
    except curves.CurveError as ex:
        raise FeatureRequestError(str(ex)) from ex
    return out.segments


def _raw_outline(p: Any, ctx: BuildContext) -> tuple[list[dict], bool]:
    """(segments, already in the datum's centred frame?)"""
    if isinstance(p, P.CURVE_SHAPES):
        cx = cy = 0.0
        if p.position is not None:
            f = factor(ctx.unit)
            cx, cy = (v / f for v in _position(p.position).to_sketch_m(ctx.unit, ctx.outline_wh))
        return curve_outline(p, cx, cy), True
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
        return rounded_rectangle(cx, cy, p.width, p.height, p.corner_radius, p.angle_deg), True
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
    if isinstance(p, P.CURVE_SHAPES):
        raise FeatureRequestError(f"a half-section cannot be a {p.shape}: write the half-section outline")
    if isinstance(p, P.Rectangle):
        if p.position is not None:
            raise FeatureRequestError("a half-section rectangle has no position: its corner is at u = 0, r = 0")
        if abs(p.angle_deg) > 1e-9:
            raise FeatureRequestError("a half-section rectangle cannot be turned")
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
    if isinstance(params, P.SweptTubeParams):                             # a standalone part: nothing builds on it; its footprint for reports
        xs, ys = [p[0] for p in params.path], [p[1] for p in params.path]
        wh = (max(xs) - min(xs) + params.outer_diameter, max(ys) - min(ys) + params.outer_diameter)
        return wh, (wh[0] / 2, wh[1] / 2), params.outer_diameter
    if isinstance(params, P.LoftParams):
        size = lambda s: (s.diameter, s.diameter) if s.shape == "circle" else (s.width, s.height)
        (w0, h0), (w1, h1) = size(params.bottom), size(params.top)
        wh = (max(w0, w1 + 2 * abs(params.top_offset_x)), max(h0, h1 + 2 * abs(params.top_offset_y)))
        return wh, (wh[0] / 2, wh[1] / 2), params.height
    if isinstance(params, P.SheetMetalBaseParams):
        return (params.width, params.height), (params.width / 2, params.height / 2), params.thickness
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
        from app.features.outline_helpers import rotated_half_extents
        return rotated_half_extents(s.width, s.height, s.angle_deg)
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
    partial = _corner_zone(w, "a cut", boxes, p.edge_zone_ok) or ctx.varying_through(p.face, end)
    return CutFeature(ctx.unit, w.plane, shapes, end=end, depth=depth, target="face", offset=w.offset, into=w.into, material_length=length,
                      outline_wh=w.outline_wh, bound_only=partial, label=label + (" (partial: upper-bound volume)" if partial else ""))


def build_hole(p: P.HoleParams, ctx: BuildContext, label: str) -> Hole:
    w = where_for(ctx, p.face, p.on, p.offset, "a hole")
    end = "through" if p.depth == "through" else "blind"
    positions = _positions(p.positions, p.pattern, ctx, w, "a hole")
    r = max(p.diameter, p.counterbore_diameter or 0.0, p.countersink_diameter or 0.0) / 2          # the WIDEST part of the hole decides
    boxes = [(q.x - r, q.x + r, q.y - r, q.y + r) for q in positions] if isinstance(w.view, AnchorView) else []
    partial = _corner_zone(w, "a hole", boxes, p.edge_zone_ok) or ctx.varying_through(p.face, end)
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
    partial = _corner_zone(w, "a ring cut" if isinstance(p.profile, P.Ring) else "a profile cut", boxes, p.edge_zone_ok) or ctx.varying_through(p.face, end)
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


def _need_turned(ctx: BuildContext, what: str) -> None:
    if not ctx.turned or ctx.turned_profile is None:
        raise FeatureRequestError(f"{what} is built on a turned part: the first item must be a revolve_base")


def build_turned_axial(p: P.TurnedAxialParams, ctx: BuildContext, label: str) -> TurnedAxial:
    _need_turned(ctx, "a turned_axial item")
    return TurnedAxial(ctx.unit, ctx.turned_axis, ctx.turned_profile, p.operation, p.diameter, p.from_u, p.to_u,
                       p.offset_in_view, p.offset_toward_viewer, label=label)


def build_cross_hole(p: P.CrossHoleParams, ctx: BuildContext, label: str) -> CrossHole:
    _need_turned(ctx, "a cross hole")
    return CrossHole(ctx.unit, ctx.turned_axis, ctx.turned_profile, p.diameter, p.at_u, p.direction, p.thread_note, label=label)


def build_radial_blades(p: P.RadialBladesParams, ctx: BuildContext, label: str) -> RadialBlades:
    _need_turned(ctx, "radial blades")
    return RadialBlades(ctx.unit, ctx.turned_axis, ctx.turned_profile, p.count, p.inner_diameter, p.outer_diameter, p.thickness, p.from_u, p.to_u,
                        p.shape, p.inlet_angle_deg or 0.0, p.outlet_angle_deg or 0.0, 1 if p.hand == "counter_clockwise" else -1, p.start_angle_deg, label=label)


def build_turned_keyway(p: P.TurnedKeywayParams, ctx: BuildContext, label: str) -> TurnedKeyway:
    _need_turned(ctx, "a keyway")
    return TurnedKeyway(ctx.unit, ctx.turned_axis, ctx.turned_profile, p.width, p.depth, p.from_u, p.to_u, p.ends, label=label)


def _standalone(ctx: BuildContext, what: str) -> None:
    if ctx.has_base:
        raise FeatureRequestError(f"{what} makes a whole part: it must be the only item")


def build_swept_tube(p: P.SweptTubeParams, ctx: BuildContext, label: str) -> SweptTube:
    _standalone(ctx, "a swept tube")
    radii = p.bend_radius if isinstance(p.bend_radius, list) else [p.bend_radius]
    return SweptTube(ctx.unit, tuple(tuple(pt) for pt in p.path), tuple(radii), p.outer_diameter, p.inner_diameter, label=label)


def _section(s: P.LoftSectionParams, cx: float = 0.0, cy: float = 0.0) -> Section:
    return Section(s.shape, s.diameter or 0.0, s.width or 0.0, s.height or 0.0, s.corner_radius, cx, cy)


def build_loft(p: P.LoftParams, ctx: BuildContext, label: str) -> Loft:
    _standalone(ctx, "a loft")
    return Loft(ctx.unit, _section(p.bottom), _section(p.top, p.top_offset_x, p.top_offset_y), p.height, label=label)


def build_sheet_metal_base(p: P.SheetMetalBaseParams, ctx: BuildContext, label: str) -> SheetMetalBase:
    _standalone(ctx, "a sheet-metal part")
    return SheetMetalBase(ctx.unit, p.width, p.height, p.thickness, p.bend_radius, tuple((f.edge, f.length, f.angle_deg) for f in p.flanges),
                          p.k_factor, label=label)


def build_swept_passage(p: P.SweptPassageParams, ctx: BuildContext, label: str) -> SweptPassage:
    ctx.need_base("a swept passage")
    size = (*ctx.outline_wh, ctx.thickness)
    if isinstance(p.path, P.SpiralPath):
        f = factor(ctx.unit)
        sx, sy = _position(p.path.centre).to_sketch_m(ctx.unit, ctx.outline_wh)
        spiral = (sx / f, sy / f, p.path.start_radius, p.path.end_radius, p.path.sweep_deg, p.path.start_angle_deg,
                  1 if p.path.hand == "counter_clockwise" else -1)
        return SweptPassage(ctx.unit, p.operation, p.diameter, p.plane_z, "spiral", spiral=spiral, size=size, label=label, end_diameter=p.end_diameter)
    radii = p.path.bend_radius if isinstance(p.path.bend_radius, list) else [p.path.bend_radius]
    return SweptPassage(ctx.unit, p.operation, p.diameter, p.plane_z, "polyline", points=tuple(tuple(q) for q in p.path.points),
                        bend_radii=tuple(radii), size=size, label=label)


def build_shell(p: P.ShellParams, ctx: BuildContext, label: str) -> Shell:
    ctx.need_base("a shell")
    base = ctx.base_feature
    vol = base.expected_delta_mm3() if base is not None and hasattr(base, "expected_delta_mm3") else None
    k = _MM3[ctx.unit]
    if ctx.turned:
        from app.features.revolve_base import turned_extent
        L, R, _ = turned_extent(ctx.turned_profile)
        if vol is None or abs(vol - math.pi * R * R * L * k) > 1e-6 * vol:
            raise FeatureRequestError("a shell is built on a plain cylinder (a rectangular half-section from the axis) only")
        return Shell(ctx.unit, p.thickness, tuple(p.open_faces), "cylinder", (L, R, ctx.turned_axis), label=label)
    W, H = ctx.outline_wh
    if vol is None or abs(vol - W * H * ctx.thickness * k) > 1e-6 * vol:
        raise FeatureRequestError("a shell is built on a plain box (a rectangular outline with sharp corners) only")
    return Shell(ctx.unit, p.thickness, tuple(p.open_faces), "box", (W, H, ctx.thickness), label=label)


def build_side_profile_cut(p: P.SideProfileCutParams, ctx: BuildContext, label: str) -> SideProfileCut:
    ctx.need_base("a side profile cut")
    if ctx.turned:
        raise FeatureRequestError("a side profile cut shapes an extruded base (a turned part already has its side outline)")
    if p.outline.frame != "base_lower_left":
        raise FeatureRequestError("the side outline is written with frame base_lower_left: x from the left edge of the base outline, y = height above its back face")
    W, H = ctx.outline_wh
    segs = round_polygon_corners([tuple(pt) for pt in p.outline.points], p.outline.corner_radius) if isinstance(p.outline, P.Polygon) \
        else [s.model_dump() for s in p.outline.segments]
    return SideProfileCut(ctx.unit, tuple(_shift(segs, W / 2, 0.0)), (W, H, ctx.thickness), _outline_centre(ctx), label=label)


def build_side_cut(p: P.SideCutParams, ctx: BuildContext, label: str) -> SideCut:
    ctx.need_base("a side cut")
    if ctx.turned:
        raise FeatureRequestError("a side cut is for an extruded base (on a turned part use cross_hole)")
    W, H = ctx.outline_wh
    loops = [({"kind": "circle", "cx": h.x - W / 2, "cy": h.y, "diameter": h.diameter},) for h in p.holes]
    for o in p.outlines:
        if o.frame != "base_lower_left":
            raise FeatureRequestError("a side-view outline is written with frame base_lower_left")
        segs = round_polygon_corners([tuple(pt) for pt in o.points], o.corner_radius) if isinstance(o, P.Polygon) else [s.model_dump() for s in o.segments]
        loops.append(tuple(_shift(segs, W / 2, 0.0)))
    if not loops:
        raise FeatureRequestError("give the holes or outlines seen in the side view")
    return SideCut(ctx.unit, tuple(loops), (W, H, ctx.thickness), None if p.depth == "through" else p.depth, p.from_edge, _outline_centre(ctx), label=label)


def _outline_centre(ctx: BuildContext) -> tuple[float, float]:
    """Where the base outline's box centre sits in the datum frame: the outline is drawn where the drawing puts it, NOT centred on the origin
    (a triple clamp's outline runs y -75 .. 35). Side-view features measure from the outline's edges, so they are placed from this."""
    (W, H), (dx, dy) = ctx.outline_wh, ctx.frame_shift
    return (W / 2 - dx, H / 2 - dy)


def build_drill(p: P.DrillParams, ctx: BuildContext, label: str) -> Drill:
    ctx.need_base("a drill")
    f = factor(ctx.unit)
    if p.at_model_mm is not None:                                       # the point the designer selected in SolidWorks: model coordinates, mm
        entry = tuple(v / 1000 / f for v in p.at_model_mm)
    elif p.entry is not None and p.entry_height is not None:
        sx, sy = _position(p.entry).to_sketch_m(ctx.unit, ctx.outline_wh)
        entry = (sx / f, sy / f, p.entry_height)
    else:
        raise FeatureRequestError("a drill needs its entry point: `entry` + `entry_height`, or `at_model_mm`")
    if (p.counterbore_diameter is None) != (p.counterbore_depth is None):
        raise FeatureRequestError("a counterbore needs both its diameter and its depth")
    W, H = ctx.outline_wh
    reach = math.sqrt(W * W + H * H + ctx.thickness ** 2)
    return Drill(ctx.unit, entry, tuple(p.direction), p.diameter, None if p.depth == "through" else p.depth,
                 (p.counterbore_diameter, p.counterbore_depth) if p.counterbore_diameter else None, reach, label=label)


def build_angled_boss(p: P.AngledBossParams, ctx: BuildContext, label: str) -> AngledBoss:
    ctx.need_base("an angled boss")
    if ctx.turned:
        raise FeatureRequestError("an angled boss stands on the front face of a flat (extruded) base, not on a turned part")
    f = factor(ctx.unit)
    sx, sy = _position(p.position).to_sketch_m(ctx.unit, ctx.outline_wh)
    return AngledBoss(ctx.unit, sx / f, sy / f, p.diameter, p.height, p.tilt_deg, p.lean, (*ctx.outline_wh, ctx.thickness),
                      p.hole_diameter, p.hole_depth, label=label)


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
    selector = EdgeSelector(w.plane, w.offset, region=region, kinds=tuple(p.kinds), radius=p.radius, length=p.length, tol=p.tol,
                            along=None if p.along is None else "xyz".index(p.along))
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
                "revolve_base": RevolveBase, "revolve_on_face": RevolveOnFace, "turned_axial": TurnedAxial, "cross_hole": CrossHole,
                "radial_blades": RadialBlades, "turned_keyway": TurnedKeyway,
                "swept_tube": SweptTube, "loft": Loft, "sheet_metal_base": SheetMetalBase,
                "swept_passage": SweptPassage, "shell": Shell, "angled_boss": AngledBoss, "side_profile_cut": SideProfileCut, "side_cut": SideCut, "drill": Drill}[self.name]


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
        ("shapes must not overlap each other", "a counterbore or countersink is a `hole`, not a cut",
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
        ("chamfers are equal on both faces (two distances are not built)",
         "the edges must lie in the plane of one face of the base (front, back, top, bottom, left, right; `offset` for a flat face beyond it); use `along` to keep "
         "only the straight edges running one way, e.g. the corner edges through the thickness"),
        {"kind": "fillet", "size": 2, "face": "front", "kinds": ["line", "arc"], "expect_count": 4},
        build_edges, _COMMON | {"distance2", "max_edges", "selector"}),
    MenuEntry(
        "revolve_base", P.RevolveBaseParams,
        "A turned part (shaft, bushing, pin, flanged sleeve) revolved 360 degrees from its half-section: the FIRST item, instead of an extruded plate.",
        "Use it when the drawing is a lathe part: a half-section with steps, cones, grooves, fillets and perhaps a bore, turned about an axis. Write the half-section as a "
        "closed outline in (u, r): u along the axis, r the radius measured from the axis (r >= 0), frame base_lower_left (u from one end of the part, r from the axis). "
        "A bore is part of the outline (it stays away from the axis). `axis` says whether the axis is horizontal or vertical in the plate view. Exactly the outline's edges "
        "become the faces; at most one outline edge may lie on the axis.",
        ("it can only be the FIRST item; after it only the turned-part items can follow: turned_axial, cross_hole, radial_blades, turned_keyway (flats, end-face patterns and other flat-part items are `unsupported`)",
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
    MenuEntry(
        "turned_axial", P.TurnedAxialParams,
        "After a turned base: a round section along the shaft axis, added (boss) or removed (cut), optionally offset from the axis (an eccentric lobe).",
        "Give the section's diameter and the two stations along the axis where it starts and ends (measured like the half-section, from u = 0). An eccentric "
        "lobe is a boss with `offset_in_view` = the printed eccentricity. An end-face bore or counterbore is a cut. A coaxial step belongs in the half-section, not here.",
        ("only after a revolve_base", "round sections only, parallel to the part axis", "a boss must touch the turned part (one body)"),
        {"operation": "boss", "diameter": 40, "from_u": 10, "to_u": 30, "offset_in_view": 10},
        build_turned_axial, _COMMON | {"axis", "profile"}),
    MenuEntry(
        "cross_hole", P.CrossHoleParams,
        "After a turned base: a through hole across the shaft, perpendicular to its axis and through it (a pin bore, an oil or lock-pin hole).",
        "Give the diameter and the station along the axis of the hole's centre. `toward_viewer` when the hole is drawn as a circle on the part; `in_view` when "
        "it runs across the part in the drawing plane. A thread is a note only.",
        ("only after a revolve_base", "through holes only, crossing the axis at 90 degrees (no offset, no angle, no blind cross hole)",
         "the hole must stay between the ends of the part"),
        {"diameter": 8, "at_u": 20, "direction": "toward_viewer"},
        build_cross_hole, _COMMON | {"axis", "profile"}),
    MenuEntry(
        "radial_blades", P.RadialBladesParams,
        "After a turned base: equal blades or fins round the axis (impeller vanes, fan blades, cooling fins), standing on a hub or between two shrouds.",
        "Give the blade count, the diameters they run between (inner = the hub they stand on, outer = the tip), the thickness and the two stations "
        "along the axis. Straight for radial fins and fan blades; arc for curved impeller vanes, with the printed inlet and outlet blade angles.",
        ("only after a revolve_base", "blades go in EMPTY space: inner diameter = the hub surface, or the blades span between two shrouds",
         "equal blades only, all the same; no twist, no taper, no variable thickness (no airfoil)", "neighbouring blades must not overlap"),
        {"count": 6, "inner_diameter": 50, "outer_diameter": 80, "thickness": 3, "from_u": 10, "to_u": 30, "shape": "straight"},
        build_radial_blades, _COMMON | {"axis", "profile"}),
    MenuEntry(
        "turned_keyway", P.TurnedKeywayParams,
        "After a turned base: a keyway along the top of a round section of the shaft (e.g. 'KEYWAY 10x4.5'), round or square ended.",
        "Give width x depth as printed and the two stations of its ends along the axis. It must lie on one diameter of the shaft.",
        ("only after a revolve_base with a horizontal axis", "on the top of the shaft as drawn; one diameter along its whole length"),
        {"width": 8, "depth": 4, "from_u": 10, "to_u": 30, "ends": "round"},
        build_turned_keyway, _COMMON | {"axis", "profile"}),
    MenuEntry(
        "swept_tube", P.SweptTubeParams,
        "A WHOLE PART: a round bar or tube along a path of straight runs and bends (a handle, a bent rod, a pipe spool), drawn in the front view.",
        "Give the centre line as the points where the straight runs meet, the centreline bend radius, the outside diameter and, for a tube, the "
        "inside diameter. It is the only item of the list.",
        ("the only item: nothing can be added to it", "a flat path in the front view; the first run horizontal or vertical",
         "round sections only, the same all along (no reducer, no flattening)", "every bend radius larger than the outside radius; the tube must not touch itself"),
        {"path": [[0, 0], [100, 0], [100, 80]], "bend_radius": 30, "outer_diameter": 20, "inner_diameter": 16},
        build_swept_tube, _COMMON),
    MenuEntry(
        "loft", P.LoftParams,
        "A WHOLE PART: a solid that blends one section into another on a parallel plane (a reducer, a square-to-round transition, a tapered post).",
        "Give the bottom and top sections (circle, or rectangle with an optional corner radius), the distance between them and any sideways shift of the "
        "top. It is the only item of the list.",
        ("the only item: nothing can be added to it", "two sections on parallel planes; solid (no wall, no hollow)",
         "circle -> circle and sharp rectangle -> rectangle are checked exactly; other pairs as a volume range"),
        {"bottom": {"shape": "rectangle", "width": 60, "height": 60, "corner_radius": 6}, "top": {"shape": "circle", "diameter": 40}, "height": 50},
        build_loft, _COMMON),
    MenuEntry(
        "sheet_metal_base", P.SheetMetalBaseParams,
        "A WHOLE PART in sheet metal: a flat rectangular blank with square or angled flanges on its edges (a bracket, a cover, a tray).",
        "Give the flat face (width x height), the sheet thickness, the inside bend radius and each flange (edge, length, angle). It is the only item of the list.",
        ("the only item: nothing can be added to it", "a rectangular blank; at most one flange per edge, no flange on a flange",
         "no holes, cut-outs or reliefs yet", "the volume is checked as a range (SolidWorks' own flange-length convention)"),
        {"width": 120, "height": 80, "thickness": 2, "bend_radius": 3, "flanges": [{"edge": "top", "length": 30}, {"edge": "bottom", "length": 30}]},
        build_sheet_metal_base, _COMMON),
    MenuEntry(
        "swept_passage", P.SweptPassageParams,
        "After a base: a round passage CUT (or a round rod boss) along a path in a plane parallel to the front face: straight runs with bends, "
        "or a spiral that winds outward (a pump volute, a coolant gallery, a channel).",
        "Give the section diameter, the depth of the path's plane from the back face and the path: points + bend radius, or a spiral (centre, "
        "start and end radius, how far round, where it starts, which way).",
        ("a round section; a spiral may widen from diameter to end_diameter (built one step per quarter turn, so it winds whole quarter turns)", "the path lies in a plane parallel to the front face and starts horizontal or vertical",
         "the passage must stay inside the part (cut) and must not run into itself", "the volume is checked as an upper bound"),
        {"operation": "cut", "diameter": 8, "plane_z": 5, "path": {"kind": "polyline", "points": [[-40, 0], [20, 0], [20, 25]], "bend_radius": 10}},
        build_swept_passage, _COMMON),
    MenuEntry(
        "shell", P.ShellParams,
        "Directly after a plain box or cylinder base: hollow it out to a wall thickness, leaving chosen faces open (a box, a cover, a cup, a tube end).",
        "Give the wall thickness and the faces left open: front / back / top / bottom / left / right of a box; start / end of a turned base.",
        ("only the SECOND item, directly after a base that is a sharp-cornered rectangle outline or a plain cylinder",
         "inward walls of one thickness (no outward shell, no different wall thicknesses)"),
        {"thickness": 3, "open_faces": ["front"]},
        build_shell, _COMMON),
    MenuEntry(
        "angled_boss", P.AngledBossParams,
        "On the front face of a flat base: a round boss standing at an angle to the face (a spark-plug boss, an angled port), optionally with a hole down its axis.",
        "Give where its axis meets the face, the diameter, the height along its axis, the tilt from the face normal and which way it leans; "
        "for a hole, its diameter and depth from the top (or 'through').",
        ("front face of an extruded base only", "tilt up to 60 degrees, leaning up / down / left / right as drawn (not diagonally)",
         "the boss foot must stay on the face and the plate be thick enough to seat it"),
        {"position": {"x": 0, "y": 0}, "diameter": 20, "height": 25, "tilt_deg": 20, "lean": "up", "hole_diameter": 10, "hole_depth": 15},
        build_angled_boss, _COMMON),
    MenuEntry(
        "drill", P.DrillParams,
        "A round hole along ANY direction from an entry point: angled holes, holes in side faces, a bore down a boss that sticks out sideways.",
        "Give the entry point (as a top-view position + its height above the back face, or `at_model_mm` straight from a point the designer "
        "selected in SolidWorks), the direction INTO the material ([x, y, z] in the top view's axes, which are the model's axes), the diameter "
        "and 'through' or a depth from the entry; an optional counterbore. For a bore down a cylinder: enter at the centre of its end face, "
        "direction along its axis, depth = its length (a 'through' hole also cuts whatever lies further along the axis).",
        ("round holes only", "the entry point should lie on the part's surface", "a counterbore needs both its diameter and its depth"),
        {"entry": {"x": 0, "y": 0}, "entry_height": 5, "direction": [1, 0, 0], "diameter": 6, "depth": 20},
        build_drill, _COMMON),
    MenuEntry(
        "side_profile_cut", P.SideProfileCutParams,
        "Right after an extruded base: give the part's outline in the SIDE view (elevation) and everything of the base outside it is cut away, "
        "through the part's whole width. Top outline x side outline = the true shape of a part that rises, steps or slopes in elevation "
        "(a triple clamp raised in the middle, a bracket with sloped arms, a casting thinner at the ends).",
        "Extrude the base from the TOP-view outline to the part's FULL height (the tallest point in the side view), then give the side outline: "
        "x from the left edge of the base outline, y = height above its back face, as a polygon (corner radii allowed) or lines + arcs.",
        ("the base must be an extruded top-view outline extruded to the full height", "the side outline lies within the base's length and height",
         "one side outline (the shape is the same across the width)", "the volume is checked as an upper bound"),
        {"outline": {"shape": "polygon", "frame": "base_lower_left", "points": [[0, 0], [120, 0], [120, 5], [80, 5], [70, 10], [50, 10], [40, 5], [0, 5]],
                     "corner_radius": 0}},
        build_side_profile_cut, _COMMON),
    MenuEntry(
        "side_cut", P.SideCutParams,
        "After an extruded base: holes and openings drawn in the SIDE view, cut sideways across the part's width: pinch-bolt holes and their "
        "counterbores (a hole 10.5 through + a 22 counterbore to depth 28 from one side), the split of a clamp end, a cross hole.",
        "Give round holes by their centre (x from the left edge of the base outline, y = height above the back face) and diameter, or openings "
        "as closed outlines in the same frame; 'through' the width, or a depth from the face at the top or bottom edge of the top-view outline. "
        "A counterbored hole is two side cuts: the small hole through, the counterbore to its depth.",
        ("an extruded base only", "straight across the width (square to the side view)", "the volume is checked as an upper bound"),
        {"holes": [{"x": 20, "y": 5, "diameter": 4}], "depth": "through"},
        build_side_cut, _COMMON),
]}
