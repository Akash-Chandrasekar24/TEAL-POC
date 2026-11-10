"""The parameters of every menu feature, as the LIST ITEM writes them (pydantic: validation + JSON schema + the text the reader sees).

Words and frames are the drawing reader's, so a human can compare each number with the drawing:
- the part is read in the FRONT view: x to the right, y up, thickness measured from the BACK face toward the viewer (the FRONT datum);
- a position is a feature's CENTRE: {"x": 15, "x_from": "left_edge", "y": 15, "y_from": "bottom_edge"} (`centre` = the centre of the BASE outline);
- outlines (polygon points, segments, regions) are written in the BASE frame: the origin is the lower-left corner of the base outline;
- `face` is "front" (the face you see) or "back" (the opposite face) of the BASE plate.
Numbers are in the list's unit (mm or inch), exactly as printed; nothing is converted here.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .patterns import Pattern, seeds_problem
from .position import Pos
from .tangent_chain import TangentChain


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


Face = Literal["front", "back", "top", "bottom", "left", "right"]


EDGE_ZONE_DOC = ("written by a HUMAN, never by a model: accept that this hole / cut / ring cut, placed on a SIDE of a boss, reaches past the straight part of that side into "
                 "the rounded or stepped zone, so it cuts less material than its full size. Without it such an item is refused (needs_human). With it the volume is only checked "
                 "as an upper bound")

ON_DOC = ("a flat face made by an EARLIER item, written `<item id>.<role>`, instead of `face`: `F3.top` = the FREE END of a boss (whichever way it looks), `F6.up` / `.down` / `.left` / "
          "`.right` / `.front` / `.back` = the flat side of an extruded boss that looks that way in the front view (several the same way: `right1`, `right2`, lowest position first), "
          "`F5.end` = the open end of a 90-degree bend. Positions on it are written from that face's own centre (or the FULL OUTER edges of the outline), as seen LOOKING AT THE FACE "
          "FROM OUTSIDE: u to the right, v up. Do not compute offsets: `offset` must stay 0")


def face_or_on_problem(face: object, on: object, base_without_face: bool = False) -> str | None:
    """None when exactly one of `face` / `on` is given (an extrude_profile_boss may give neither: it is then the base)."""
    if face is not None and on is not None:
        return "give `face` OR `on`, not both"
    if face is None and on is None and not base_without_face:
        return "give `face` (a face of the base) or `on` (a face an earlier item made)"
    return None


PATTERN_DOC = ("optional pattern (front / back faces, or a face placed with `on`; on the other faces of the base list every position). linear / grid / mirror repeat the seed position(s); circular needs NO seed position: it gives every "
               "position from `centre`, `pitch_diameter` (the printed bolt circle), `count` and `start_angle_deg`. Use it whenever the drawing says 4X, "
               "'equally spaced' or gives a bolt circle; write explicit positions only for irregular layouts")


# ------------------------------------------------------------------ outlines
class LineSeg(Strict):
    kind: Literal["line"]
    x1: float
    y1: float
    x2: float
    y2: float


class ArcSeg(Strict):
    kind: Literal["arc"]
    cx: float
    cy: float
    x1: float
    y1: float
    x2: float
    y2: float
    dir: Literal[1, -1] = Field(description="1 = counter-clockwise from (x1, y1) to (x2, y2), -1 = clockwise")


Segment = Annotated[Union[LineSeg, ArcSeg], Field(discriminator="kind")]


class Rectangle(Strict):
    shape: Literal["rectangle"]
    width: float = Field(gt=0, description="along x")
    height: float = Field(gt=0, description="along y")
    corner_radius: float = Field(0, ge=0, description="all four corners; 0 = sharp")
    position: Pos | None = Field(None, description="the rectangle's centre; omit for the base outline itself (centred)")
    angle_deg: float = Field(0, description="turns the rectangle about its centre, counter-clockwise from +x (width runs along this direction); 0 = axis-aligned")


class Polygon(Strict):
    shape: Literal["polygon"]
    points: list[Annotated[list[float], Field(min_length=2, max_length=2)]] = Field(min_length=3, description="corner points in order, [x, y]")
    corner_radius: Union[float, list[float | None]] = Field(0, description="one radius for every corner, or one entry per point (null/0 = sharp)")
    frame: Literal["base_lower_left", "centre"] = Field("base_lower_left", description="the origin of the points")


class Segments(Strict):
    shape: Literal["segments"]
    segments: list[Segment] = Field(min_length=3, description="a closed outline, in order, sharing end points exactly")
    frame: Literal["base_lower_left", "centre"] = "base_lower_left"


class Ring(Strict):
    shape: Literal["ring"]
    outer_diameter: float = Field(gt=0, description="outer diameter, as printed")
    inner_diameter: float = Field(gt=0, description="inner diameter (the bore of a tube, the inside of a groove): must be SMALLER than the outer diameter")
    position: Pos | None = Field(None, description="the centre of the ring, placed like any position; omit for the centre of the base outline / of the face it is placed on")

    @model_validator(mode="after")
    def _smaller(self):
        if self.inner_diameter >= self.outer_diameter:
            raise ValueError(f"the inner diameter of a ring ({self.inner_diameter:g}) must be smaller than its outer diameter ({self.outer_diameter:g})")
        return self


CURVE_TOL_DOC = "largest chord deviation from the exact curve, in the drawing unit (default 0.005 = 5 micrometres in mm)"


class SpurGear(Strict):
    """An involute spur gear outline from its printed gear data (z, m, alpha). External = the gear body; internal = the toothed hole of a ring gear."""
    shape: Literal["spur_gear"]
    teeth: int = Field(ge=6, le=400, description="number of teeth, as printed (z)")
    module: float = Field(gt=0, description="module, as printed (m)")
    pressure_angle_deg: float = Field(20, ge=10, le=35, description="pressure angle, as printed (alpha)")
    internal: bool = Field(False, description="true for an internal (ring) gear: the outline is the toothed HOLE, teeth pointing inward; cut it from a disc")
    position: Pos | None = Field(None, description="the gear centre; omit for the centre of the base outline / face")
    rotation_deg: float = Field(0, description="turns the teeth about the centre (0 = a tooth, or for an internal gear a space, on +x)")
    chord_tolerance: float = Field(0.005, gt=0, le=0.1, description=CURVE_TOL_DOC)


class Epitrochoid(Strict):
    """The bore of a Wankel rotor housing: the two-lobe epitrochoid x = R cos t + e cos 3t, y = R sin t + e sin 3t."""
    shape: Literal["epitrochoid"]
    generating_radius: float = Field(gt=0, description="R, the rotor's generating radius as printed (e.g. 'R65.000 (TROCHOID)')")
    eccentricity: float = Field(gt=0, description="e, the eccentric shaft offset as printed (e.g. 'e = 15.00')")
    clearance: float = Field(0, ge=0, description="enlarge the bore by this apex clearance, only if printed")
    position: Pos | None = None
    rotation_deg: float = Field(0, description="0 = major axis along x; 90 = major axis vertical")
    chord_tolerance: float = Field(0.005, gt=0, le=0.1, description=CURVE_TOL_DOC)


class RotorFlank(Strict):
    """The three-flank Wankel rotor: the inner envelope of the housing epitrochoid with the same R and e."""
    shape: Literal["rotor_flank"]
    generating_radius: float = Field(gt=0, description="R of the housing trochoid the rotor runs in")
    eccentricity: float = Field(gt=0, description="e, the eccentric shaft offset")
    clearance: float = Field(0, ge=0, description="reduce the rotor by this running clearance, only if printed")
    position: Pos | None = None
    rotation_deg: float = Field(0, description="0 = one apex on +x")
    chord_tolerance: float = Field(0.005, gt=0, le=0.1, description=CURVE_TOL_DOC)


CURVE_SHAPES = (SpurGear, Epitrochoid, RotorFlank)
Profile = Annotated[Union[Rectangle, Polygon, Segments, TangentChain, Ring, SpurGear, Epitrochoid, RotorFlank], Field(discriminator="shape")]


# ------------------------------------------------------------------ features
class ExtrudeProfileBossParams(Strict):
    profile: Profile
    depth: float = Field(gt=0, description="thickness (the base) or height of the boss")
    face: Face | None = Field(None, description="omit (and omit `on`) for the BASE (sketched on the back face, grows toward the viewer); a face name = a boss that grows outward "
                                                "from that face of the base")
    on: str | None = Field(None, description=ON_DOC)
    offset: float = Field(0, ge=0, description="start the feature on a plane this far BEYOND the base face, outward (e.g. the flat top of a boss standing 35 high: face front, "
                                              "offset 35). 0 = the base face itself. There must be a flat face at that height. Positions are still measured from the BASE outline")

    @model_validator(mode="after")
    def _where(self):
        problem = face_or_on_problem(self.face, self.on, base_without_face=True)
        if problem:
            raise ValueError(problem)
        return self


class CutShapeParams(Strict):
    kind: Literal["circle", "slot", "rectangle"]
    position: Pos | None = Field(None, description="the shape's centre (the seed when a pattern repeats it); omit only with a circular pattern")
    diameter: float | None = Field(None, gt=0, description="circle")
    centre_distance: float | None = Field(None, gt=0, description="slot: distance between the two end-arc centres")
    width: float | None = Field(None, gt=0, description="slot: width; rectangle: along x")
    height: float | None = Field(None, gt=0, description="rectangle: along y")
    angle_deg: float = Field(0, description="slot: direction of its centre line; rectangle: turns it about its centre (width runs along this direction); "
                                            "counter-clockwise from +x in the view, e.g. a groove drawn at an apex 120 degrees round")
    pattern: Pattern | None = Field(None, description=PATTERN_DOC)

    @model_validator(mode="after")
    def _seed(self):
        problem = seeds_problem(self.pattern, 0 if self.position is None else 1)
        if problem:
            raise ValueError(problem)
        return self


class CutParams(Strict):
    shapes: list[CutShapeParams] = Field(min_length=1)
    face: Face | None = Field(None, description="a face of the base; or give `on` instead")
    on: str | None = Field(None, description=ON_DOC)
    offset: float = Field(0, ge=0, description="start the feature on a plane this far BEYOND the base face, outward (e.g. the flat top of a boss standing 35 high: face front, "
                                              "offset 35). 0 = the base face itself. There must be a flat face at that height. Positions are still measured from the BASE outline")
    depth: Union[float, Literal["through"]] = Field(description="a number (blind) or 'through'")
    material_length: float | None = Field(None, gt=0, description="the thickness the cut crosses. REQUIRED for 'through'; a blind cut defaults to its depth")
    edge_zone_ok: bool = Field(False, description=EDGE_ZONE_DOC)

    @model_validator(mode="after")
    def _where(self):
        problem = face_or_on_problem(self.face, self.on)
        if problem:
            raise ValueError(problem)
        return self


class HoleParams(Strict):
    positions: list[Pos] = Field([], description="every hole of this spec; with a pattern: the seed position(s); with a circular pattern: leave empty")
    pattern: Pattern | None = Field(None, description=PATTERN_DOC)
    diameter: float = Field(gt=0, description="nominal diameter (a threaded hole: the nominal size, e.g. 6 for M6)")
    face: Face | None = Field(None, description="a face of the base; or give `on` instead")
    on: str | None = Field(None, description=ON_DOC)
    offset: float = Field(0, ge=0, description="start the feature on a plane this far BEYOND the base face, outward (e.g. the flat top of a boss standing 35 high: face front, "
                                              "offset 35). 0 = the base face itself. There must be a flat face at that height. Positions are still measured from the BASE outline")
    depth: Union[float, Literal["through"]] = Field(description="a number (blind, flat bottom) or 'through'")
    material_length: float | None = Field(None, gt=0, description="the thickness the hole crosses. REQUIRED for 'through'")
    style: Literal["plain", "counterbore", "countersink"] = "plain"
    counterbore_diameter: float | None = Field(None, gt=0)
    counterbore_depth: float | None = Field(None, gt=0)
    countersink_diameter: float | None = Field(None, gt=0, description="diameter at the surface")
    countersink_angle_deg: float = Field(90, description="included angle: only 90 is built")
    thread_note: str | None = Field(None, description="e.g. 'M6'. Informational: no thread is modelled")
    floor_open: bool = Field(False, description="written by a HUMAN at approval, never by a model: a BLIND hole whose bottom is expected to open into empty space or an existing "
                                                "hollow (no solid floor). The check then asserts there is no floor face. A counterbore step is not affected")
    edge_zone_ok: bool = Field(False, description=EDGE_ZONE_DOC)

    @model_validator(mode="after")
    def _seeds(self):
        problem = seeds_problem(self.pattern, len(self.positions)) or face_or_on_problem(self.face, self.on)
        if problem:
            raise ValueError(problem)
        if self.floor_open and self.depth == "through":
            raise ValueError("floor_open applies to a blind hole (a number depth) only")
        return self


class CircleParams(Strict):
    diameter: float = Field(gt=0)
    position: Pos | None = Field(None, description="the circle's centre (the seed when a pattern repeats it); omit only with a circular pattern")
    pattern: Pattern | None = Field(None, description=PATTERN_DOC)

    @model_validator(mode="after")
    def _seed(self):
        problem = seeds_problem(self.pattern, 0 if self.position is None else 1)
        if problem:
            raise ValueError(problem)
        return self


class CircleBossParams(Strict):
    circles: list[CircleParams] = Field(min_length=1)
    height: float = Field(gt=0, description="how far the bosses stick out")
    face: Face | None = Field(None, description="a face of the base; or give `on` instead")
    on: str | None = Field(None, description=ON_DOC)
    offset: float = Field(0, ge=0, description="start the feature on a plane this far BEYOND the base face, outward (e.g. the flat top of a boss standing 35 high: face front, "
                                              "offset 35). 0 = the base face itself. There must be a flat face at that height. Positions are still measured from the BASE outline")

    @model_validator(mode="after")
    def _where(self):
        problem = face_or_on_problem(self.face, self.on)
        if problem:
            raise ValueError(problem)
        return self


class ProfileCutParams(Strict):
    profile: Profile
    face: Face | None = Field(None, description="a face of the base; or give `on` instead")
    on: str | None = Field(None, description=ON_DOC)
    offset: float = Field(0, ge=0, description="start the feature on a plane this far BEYOND the base face, outward (e.g. the flat top of a boss standing 35 high: face front, "
                                              "offset 35). 0 = the base face itself. There must be a flat face at that height. Positions are still measured from the BASE outline")
    depth: Union[float, Literal["through"]]
    material_length: float | None = Field(None, gt=0, description="REQUIRED for 'through'; a blind cut defaults to its depth. A RING outline is always cut blind: give its depth as a number")
    edge_zone_ok: bool = Field(False, description=EDGE_ZONE_DOC)

    @model_validator(mode="after")
    def _where(self):
        problem = face_or_on_problem(self.face, self.on)
        if problem:
            raise ValueError(problem)
        if isinstance(self.profile, Ring) and self.depth == "through":
            raise ValueError("a ring cut needs an explicit blind depth (a number), not 'through'")
        return self


class CircleSection(Strict):
    shape: Literal["circle"]
    diameter: float = Field(gt=0, description="outer diameter of the round section")
    bore_diameter: float | None = Field(None, gt=0, description="diameter of the bore of a hollow section (a pipe wall); omit for a solid round section")
    position: Pos = Field(description="the centre of the circle, placed like any position")


Section = Annotated[Union[CircleSection, Rectangle, Polygon, Segments, TangentChain], Field(discriminator="shape")]


class AxisX(Strict):
    x: float
    x_from: Literal["centre", "left_edge", "right_edge"] = Field("centre", description="what x is measured from (centre = centre of the base outline)")


class RevolveOnFaceParams(Strict):
    section: Section = Field(description="the section drawn ON the face: a circle (with an optional bore) or any closed outline, placed like other outlines")
    axis: AxisX = Field(description="the VERTICAL axis the section is revolved about, in the plane of the face, seen in the plate view. The section must lie clear of it, on one side")
    face: Literal["front", "back"] = Field(description="the face of the base the section is drawn on; the revolved solid grows away from the plate")
    offset: float = Field(0, ge=0, description="start on a plane this far BEYOND the base face, outward (e.g. the flat top of a boss). 0 = the base face itself")
    angle_deg: float = Field(description="the sweep in degrees: 45, 90, 135 or 180 only")


class RevolveBaseParams(Strict):
    profile: Profile = Field(description="the HALF-SECTION of the turned part as in a lathe drawing: x = u along the axis, y = r the radius (>= 0) measured from the axis, "
                                         "frame base_lower_left, a closed outline. Steps, cones, grooves, fillets and a bore are all part of this outline")
    axis: Literal["horizontal", "vertical"] = Field(description="the axis of the part as drawn in the plate view (u runs along it)")


STATION_DOC = ("a station along the turned part's axis, measured like its half-section: from the end where u = 0 (the left end for a "
               "horizontal axis, the bottom end for a vertical one), in the drawing unit")


class TurnedAxialParams(Strict):
    operation: Literal["boss", "cut"] = Field(description="boss = add a round section (e.g. an eccentric lobe or a collar); cut = remove one (e.g. an end-face bore)")
    diameter: float = Field(gt=0, description="diameter of the round section, as printed")
    from_u: float = Field(description=STATION_DOC + ": where the section starts")
    to_u: float = Field(description=STATION_DOC + ": where it ends")
    offset_in_view: float = Field(0, description="distance of its centre from the part axis, in the drawing plane: + up for a horizontal axis, + right for a vertical one (e.g. 'e = 15.00' drawn upward)")
    offset_toward_viewer: float = Field(0, description="distance of its centre from the axis toward the viewer; 0 unless printed")


class CrossHoleParams(Strict):
    diameter: float = Field(gt=0, description="hole diameter (a threaded hole: the nominal size)")
    at_u: float = Field(description=STATION_DOC + ": the hole's centre")
    direction: Literal["toward_viewer", "in_view"] = Field("toward_viewer", description="toward_viewer = the hole is seen as a circle in the view; in_view = it runs across the "
                                                                                      "part within the drawing plane (seen as two parallel hidden lines)")
    thread_note: str | None = Field(None, description="e.g. 'M8'. Informational: no thread is modelled")


class RadialBladesParams(Strict):
    count: int = Field(ge=1, description="number of equal blades / fins round the axis, as printed (e.g. '7 BLADES', '12x')")
    inner_diameter: float = Field(gt=0, description="where the blades start: the hub (or body) diameter they stand on, as printed")
    outer_diameter: float = Field(gt=0, description="where the blades end: the tip diameter, as printed")
    thickness: float = Field(gt=0, description="blade / fin thickness, as printed")
    from_u: float = Field(description=STATION_DOC + ": where the blades start along the axis (e.g. the inside face of a shroud)")
    to_u: float = Field(description=STATION_DOC + ": where they end")
    shape: Literal["straight", "arc"] = Field("straight", description="straight = radial blades or fins; arc = curved impeller vanes (textbook single-arc blade)")
    inlet_angle_deg: float | None = Field(None, gt=0, lt=90, description="arc only: blade angle at the inner diameter, from the tangent (beta 1), as printed")
    outlet_angle_deg: float | None = Field(None, gt=0, lt=90, description="arc only: blade angle at the outer diameter, from the tangent (beta 2), as printed")
    hand: Literal["counter_clockwise", "clockwise"] = Field("counter_clockwise", description="arc only: which way the blade leans going outward, seen from the u = 0 end")
    start_angle_deg: float = Field(0, description="where the first blade starts on the hub, seen from the u = 0 end, counter-clockwise from 'toward the viewer' (horizontal axis) / from +x (vertical axis)")

    @model_validator(mode="after")
    def _arc(self):
        if self.shape == "arc" and (self.inlet_angle_deg is None or self.outlet_angle_deg is None):
            raise ValueError("an arc blade needs inlet_angle_deg and outlet_angle_deg (the printed blade angles)")
        if self.inner_diameter >= self.outer_diameter:
            raise ValueError("inner_diameter must be smaller than outer_diameter")
        return self


class TurnedKeywayParams(Strict):
    width: float = Field(gt=0, description="keyway width, as printed (e.g. 10 in 'KEYWAY 10x4.5')")
    depth: float = Field(gt=0, description="keyway depth into the shaft, as printed (e.g. 4.5)")
    from_u: float = Field(description=STATION_DOC + ": where the keyway starts")
    to_u: float = Field(description=STATION_DOC + ": where it ends (its full length including round ends)")
    ends: Literal["round", "square"] = Field("round", description="round = end-milled keyway (rounded ends), square = straight ends")


class SweptTubeParams(Strict):
    path: list[Annotated[list[float], Field(min_length=2, max_length=2)]] = Field(min_length=2, description="the tube's CENTRE LINE in the front view: "
                                                                                     "the points where its straight runs meet ([x, y], in order); the first run must be horizontal or vertical")
    bend_radius: Union[float, list[float]] = Field(description="centreline bend radius at the corners: one for all, or one per corner (as printed, e.g. 'R40 CL')")
    outer_diameter: float = Field(gt=0, description="outside diameter of the bar or tube")
    inner_diameter: float | None = Field(None, gt=0, description="inside diameter of a tube (outside minus twice the wall); omit for a solid bar")


class LoftSectionParams(Strict):
    shape: Literal["circle", "rectangle"]
    diameter: float | None = Field(None, gt=0, description="circle")
    width: float | None = Field(None, gt=0, description="rectangle: along x")
    height: float | None = Field(None, gt=0, description="rectangle: along y")
    corner_radius: float = Field(0, ge=0, description="rectangle: all four corners; 0 = sharp")

    @model_validator(mode="after")
    def _size(self):
        if (self.shape == "circle" and self.diameter is None) or (self.shape == "rectangle" and (self.width is None or self.height is None)):
            raise ValueError("a circle needs diameter; a rectangle needs width and height")
        return self


class LoftParams(Strict):
    bottom: LoftSectionParams = Field(description="the section on the back plane (z = 0), centred on the origin")
    top: LoftSectionParams = Field(description="the section on the parallel plane `height` in front")
    height: float = Field(gt=0, description="distance between the two section planes (the part's length / height), as printed")
    top_offset_x: float = Field(0, description="how far the top section's centre is shifted along x from the bottom's (0 = coaxial)")
    top_offset_y: float = Field(0, description="the same along y")


class FlangeParams(Strict):
    edge: Literal["top", "bottom", "left", "right"] = Field(description="the edge of the blank, as seen in the front view")
    length: float = Field(gt=0, description="flange length, as printed")
    angle_deg: float = Field(90, gt=0, le=135, description="bend angle (90 = square)")


class SheetMetalBaseParams(Strict):
    width: float = Field(gt=0, description="blank width along x (the flat face before flanges)")
    height: float = Field(gt=0, description="blank height along y")
    thickness: float = Field(gt=0, description="sheet thickness, as printed (e.g. 't = 2')")
    bend_radius: float = Field(gt=0, description="inside bend radius, as printed")
    k_factor: float | None = Field(None, gt=0, lt=1, description="only if printed")
    flanges: list[FlangeParams] = Field(default_factory=list, description="flanges on the blank's edges, at most one per edge")


class PolylinePath(Strict):
    kind: Literal["polyline"]
    points: list[Annotated[list[float], Field(min_length=2, max_length=2)]] = Field(min_length=2, description="the passage's centre line: the points where "
                                                                                     "its straight runs meet, [x, y] from the centre of the base outline; the first run horizontal or vertical")
    bend_radius: Union[float, list[float]] = Field(description="centreline bend radius at the corners: one for all or one per corner")


class SpiralPath(Strict):
    kind: Literal["spiral"]
    centre: Pos = Field(description="the centre the spiral winds round (e.g. the impeller axis), placed like any position")
    start_radius: float = Field(gt=0, description="radius of the passage centre line where the spiral starts (e.g. at the cutwater)")
    end_radius: float = Field(gt=0, description="radius where it ends (e.g. at the discharge throat)")
    sweep_deg: float = Field(ge=30, le=720, description="how far round it winds, in degrees (a volute: about 360)")
    start_angle_deg: Literal[0, 90, 180, 270] = Field(0, description="where it starts round the centre, counter-clockwise from +x (0 = to the right)")
    hand: Literal["counter_clockwise", "clockwise"] = Field("counter_clockwise", description="which way it winds, as seen in the front view")


class SweptPassageParams(Strict):
    operation: Literal["cut", "boss"] = Field("cut", description="cut = a passage / gallery / channel; boss = a round rod or rib along the path")
    diameter: float = Field(gt=0, description="diameter of the round passage section")
    plane_z: float = Field(description="depth of the path's plane, measured from the BACK face toward the viewer (e.g. half the thickness)")
    path: Union[PolylinePath, SpiralPath] = Field(discriminator="kind")


class ShellParams(Strict):
    thickness: float = Field(gt=0, description="wall thickness, as printed")
    open_faces: list[Literal["front", "back", "top", "bottom", "left", "right", "start", "end"]] = Field(
        min_length=1, description="faces left open: front / back / top / bottom / left / right of a box base; start / end (u = 0 / far end) of a turned base")


class AngledBossParams(Strict):
    position: Pos = Field(description="where the boss axis meets the FRONT face")
    diameter: float = Field(gt=0, description="boss diameter")
    height: float = Field(gt=0, description="length of the boss along its own axis, from the face to its flat top")
    tilt_deg: float = Field(gt=0, le=60, description="angle between the boss axis and the face normal, as printed")
    lean: Literal["up", "down", "left", "right"] = Field(description="which way the top of the boss leans, as seen in the front view")
    hole_diameter: float | None = Field(None, gt=0, description="a hole down the boss axis (e.g. a spark-plug or port bore): its diameter")
    hole_depth: Union[float, Literal["through"], None] = Field(None, description="the hole depth from the top of the boss, or 'through'")


class RibParams(Strict):
    axis: Pos = Field(description="the axis of the round boss the rib leans on, as a position on the plate")
    boss_diameter: float = Field(gt=0, description="outer diameter of that boss")
    boss_height: float = Field(gt=0, description="height of that boss above the front face")
    thickness: float = Field(gt=0, description="rib thickness (measured along y, across the rib)")
    foot_distance: float = Field(gt=0, description="distance from the boss axis (along x) to the foot of the rib on the plate face")
    height_at_boss: float = Field(gt=0, description="height above the plate face where the sloped edge meets the boss wall")
    sides: Literal["left", "right", "both"] = Field("both", description="which side(s) of the boss along x")
    foot_fillet: float | None = Field(None, gt=0, description="fillet radius at the foot of the rib where it meets the plate (e.g. a printed 'R5 (TYP)' at the rib feet); omit if sharp")


class EdgeParams(Strict):
    kind: Literal["fillet", "chamfer"]
    size: float = Field(gt=0, description="fillet radius, or the chamfer setback (45 degrees, equal on both faces)")
    face: Face = Field(description="the edges that lie in the plane of this face of the base")
    offset: float = Field(0, ge=0, description="select the edges of the flat face on a plane this far BEYOND the base face, outward (e.g. the flat top of a boss standing 35 high: face front, "
                                              "offset 35). 0 = the base face itself. There must be a flat face at that height. Positions are still measured from the BASE outline")
    region: list[float] | None = Field(None, min_length=4, max_length=4, description="[x_min, x_max, y_min, y_max] in the base frame; only edges inside it")
    kinds: list[Literal["line", "arc", "circle"]] = Field(["line", "arc", "circle"], min_length=1)
    radius: float | None = Field(None, gt=0, description="only arcs / circles of this radius")
    length: float | None = Field(None, gt=0, description="only lines of this length")
    tol: float = Field(0.01, gt=0, description="tolerance of radius / length / region")
    along: Literal["x", "y", "z"] | None = Field(None, description="only straight edges running along this direction (x right, y up as drawn in the front view, "
                                                                   "z through the thickness toward the viewer): e.g. face top + along z = the two top corner edges "
                                                                   "through the plate; face front + along x = the top and bottom edges of the front face")
    expect_count: int | None = Field(None, ge=1, description="the number of edges this must select; a different count stops the build")
