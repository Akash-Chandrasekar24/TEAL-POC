"""Feature 2: cut circles, slots and rectangles, blind or through, on a datum plane or on an existing planar face.

Plane target  -> checklist (submit_feature_graph): one sketch with every shape, one cut.
Face target   -> low-level: analyze_model(faces) -> create_sketch(on_face) -> add_sketch_entities -> extrude_feature
                 Shape positions use the SAME datum-frame words as everywhere else; the face's own
                 sketch frame is read from create_sketch and the points are projected into it with dot products, so
                 a mirrored or rotated face frame cannot flip a position.
Expected volume removed: sum(shape areas) x material_length, areas exact (circle, rectangle, slot = L*w + pi*w^2/4).
When material_length is not given the cut is only bounded: |removed| <= area x depth (blind / mid_plane), and the MEASURED
value is logged (and compared with `regression_delta_mm3`, a previously measured value, when given).
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field, replace

from app.verify import Expect
from .base import (AREA_TOL_M2, FeatureContext, FeatureRequestError, FeatureResult, PlaneFrame, POS_TOL_M, Position, Probe,
                   factor, graph_of, probe_cylinders, probe_planar_face, probe_segments, read_geometry, sketch_node)

MM3_PER_UNIT3 = {"mm": 1.0, "inch": 25.4 ** 3}
_FRAME = re.compile(r"frame origin \[([^\]]*)\] xdir \[([^\]]*)\] ydir \[([^\]]*)\]")


@dataclass(frozen=True)
class CutShape:
    """One shape, in the drawing's unit. `position` is the shape's CENTRE as printed.
    circle: diameter. slot: centre_distance (distance between the two end-arc centres, as printed e.g. '32') + width + angle_deg.
    rectangle: width (along x) + height (along y)."""
    kind: str
    position: Position
    diameter: float | None = None
    centre_distance: float | None = None
    width: float | None = None
    height: float | None = None
    angle_deg: float = 0.0

    def validate(self) -> None:
        need = {"circle": ("diameter",), "slot": ("centre_distance", "width"), "rectangle": ("width", "height")}
        if self.kind not in need:
            raise FeatureRequestError(f"shape kind must be circle, slot or rectangle, got {self.kind!r}")
        for n in need[self.kind]:
            if not getattr(self, n) or getattr(self, n) <= 0:
                raise FeatureRequestError(f"{self.kind} needs a positive {n}")

    def area(self) -> float:
        self.validate()
        if self.kind == "circle":
            return math.pi * self.diameter ** 2 / 4
        if self.kind == "rectangle":
            return self.width * self.height
        return self.centre_distance * self.width + math.pi * self.width ** 2 / 4

    def half_extent(self) -> tuple[float, float]:
        """Half size of the shape's axis-aligned box (for the overlap check)."""
        if self.kind == "circle":
            return self.diameter / 2, self.diameter / 2
        if self.kind == "rectangle":
            from .outline_helpers import rotated_half_extents
            return rotated_half_extents(self.width, self.height, self.angle_deg)
        a = math.radians(self.angle_deg)
        # exact box of a stadium: half the centre line's projection + the end radius, in both axes
        hx = abs(math.cos(a)) * self.centre_distance / 2 + self.width / 2
        hy = abs(math.sin(a)) * self.centre_distance / 2 + self.width / 2
        return hx, hy


def slot_segments(c: tuple[float, float], length: float, width: float, angle_deg: float) -> list[dict]:
    """Closed stadium outline (sketch units, whatever the caller's unit is): two lines + two semicircles, clockwise arcs
    (arcs dir -1). Shared end points are exactly equal."""
    a = math.radians(angle_deg)
    dx, dy = math.cos(a), math.sin(a)
    nx, ny = -dy, dx
    h, w2 = length / 2, width / 2
    c1 = (c[0] - dx * h, c[1] - dy * h)
    c2 = (c[0] + dx * h, c[1] + dy * h)
    p1l, p1r = (c1[0] + nx * w2, c1[1] + ny * w2), (c1[0] - nx * w2, c1[1] - ny * w2)
    p2l, p2r = (c2[0] + nx * w2, c2[1] + ny * w2), (c2[0] - nx * w2, c2[1] - ny * w2)
    return [
        {"kind": "line", "x1": p1l[0], "y1": p1l[1], "x2": p2l[0], "y2": p2l[1]},
        {"kind": "arc", "cx": c2[0], "cy": c2[1], "x1": p2l[0], "y1": p2l[1], "x2": p2r[0], "y2": p2r[1], "dir": -1},
        {"kind": "line", "x1": p2r[0], "y1": p2r[1], "x2": p1r[0], "y2": p1r[1]},
        {"kind": "arc", "cx": c1[0], "cy": c1[1], "x1": p1r[0], "y1": p1r[1], "x2": p1l[0], "y2": p1l[1], "dir": -1},
    ]


def rectangle_segments(c: tuple[float, float], w: float, h: float, angle_deg: float = 0.0) -> list[dict]:
    """Rectangle centred on c, `w` along the direction angle_deg (counter-clockwise from +x), `h` across it."""
    a = math.radians(angle_deg)
    co, si = math.cos(a), math.sin(a)
    corners = [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]
    pts = [(c[0] + x * co - y * si, c[1] + x * si + y * co) for x, y in corners]
    pts.append(pts[0])
    return [{"kind": "line", "x1": pts[i][0], "y1": pts[i][1], "x2": pts[i + 1][0], "y2": pts[i + 1][1]} for i in range(4)]


@dataclass(frozen=True)
class CutFeature:
    unit: str
    plane: str                          # 'top' | 'front': the view the positions are printed in (datum frame)
    shapes: list[CutShape]
    end: str = "blind"                  # 'blind' | 'through' | 'mid_plane' (mid_plane: plane target only, depth = TOTAL)
    depth: float | None = None          # blind / mid_plane
    target: str = "plane"               # 'plane' | 'face'
    offset: float = 0.0                 # plane target: plane offset; face target: height of the planar face (both along the datum normal)
    into: str | None = None             # 'positive' | 'negative' normal direction the cut goes. default: plane +, face -
    material_length: float | None = None  # how much material the cut crosses (drawing unit). Makes the expected volume exact
    outline_wh: tuple[float, float] | None = None   # only for positions printed from an edge
    face_index: int | None = None       # face target: force a face (else the only planar face at `offset`)
    floor_open: bool = False            # blind only: the floor is EXPECTED to open into empty space or an existing hollow, so no solid floor face must exist there
    bound_only: bool = False            # a human accepted that the shape reaches past the straight part of a rounded / stepped side: it cuts LESS than its full size, so
                                        # the volume is only checked as an upper bound and the full-size floor probe is skipped
    label: str = "cut"
    regression_delta_mm3: float | None = None       # a previously measured volume change for the same step (optional regression check)

    NAME = "cut_feature"

    # ---------------------------------------------------------------- validation and pure maths
    def validate(self) -> PlaneFrame:
        frame = PlaneFrame(self.plane)
        factor(self.unit)
        if not self.shapes:
            raise FeatureRequestError("no shapes")
        for s in self.shapes:
            s.validate()
        if self.end not in ("blind", "through", "mid_plane"):
            raise FeatureRequestError(f"end must be blind, through or mid_plane, got {self.end!r}")
        if self.target not in ("plane", "face"):
            raise FeatureRequestError(f"target must be plane or face, got {self.target!r}")
        if self.end in ("blind", "mid_plane") and (self.depth is None or self.depth <= 0):
            raise FeatureRequestError(f"end={self.end!r} needs a positive depth")
        if self.end == "mid_plane" and self.target == "face":
            raise FeatureRequestError("mid_plane cuts need a plane target")
        if self.into not in (None, "positive", "negative"):
            raise FeatureRequestError("into must be positive or negative")
        if self.material_length is not None and self.material_length <= 0:
            raise FeatureRequestError("material_length must be positive")
        if self.floor_open and self.end != "blind":
            raise FeatureRequestError("floor_open applies to a blind cut only: a through cut has no floor")
        centres = [s.position.to_sketch_m(self.unit, self.outline_wh) for s in self.shapes]
        f = factor(self.unit)
        for i in range(len(self.shapes)):
            for j in range(i + 1, len(self.shapes)):
                hi, hj = self.shapes[i].half_extent(), self.shapes[j].half_extent()
                if abs(centres[i][0] - centres[j][0]) < (hi[0] + hj[0]) * f and abs(centres[i][1] - centres[j][1]) < (hi[1] + hj[1]) * f:
                    raise FeatureRequestError(f"shapes {i} and {j} overlap: their areas would be counted twice")
        return frame

    def direction(self) -> str:
        return self.into or ("negative" if self.target == "face" else "positive")

    def total_area(self) -> float:
        return sum(s.area() for s in self.shapes)

    def expected(self) -> Expect:
        """Signed volume change in mm3: exact when material_length is known, otherwise an upper bound."""
        k = MM3_PER_UNIT3[self.unit]
        area = self.total_area()
        if self.material_length is not None and not self.bound_only:
            return Expect(delta_mm3=-area * self.material_length * k, regression_delta_mm3=self.regression_delta_mm3)
        if self.end in ("blind", "mid_plane"):
            return Expect(bound_mm3=-area * self.depth * k, regression_delta_mm3=self.regression_delta_mm3)
        return Expect(regression_delta_mm3=self.regression_delta_mm3)       # through, length unknown: only 'must change'

    def shape_segments_m(self) -> list[list[dict]]:
        """Per shape: sketch-frame segments in METRES (circle -> one 'circle' record)."""
        f = factor(self.unit)
        out = []
        for s in self.shapes:
            cx, cy = s.position.to_sketch_m(self.unit, self.outline_wh)
            if s.kind == "circle":
                out.append([{"kind": "circle", "diameter": s.diameter * f, "cx": cx, "cy": cy}])
            elif s.kind == "rectangle":
                out.append(rectangle_segments((cx, cy), s.width * f, s.height * f, s.angle_deg))
            else:
                out.append(slot_segments((cx, cy), s.centre_distance * f, s.width * f, s.angle_deg))
        return out

    def graph(self) -> dict:
        """Checklist graph for a PLANE target."""
        self.validate()
        if self.target != "plane":
            raise FeatureRequestError("graph() is the plane-target path")
        f = factor(self.unit)
        profile = [seg for shape in self.shape_segments_m() for seg in shape]
        cut = {"id": "n2", "type": "extrude", "sketch": "n1", "operation": "cut",
               "end": {"blind": "blind", "through": "through_all", "mid_plane": "mid_plane"}[self.end]}
        if self.end != "through":
            cut["depth"] = self.depth * f
        if self.direction() == "negative":
            cut["reversed"] = True
        return graph_of([sketch_node("n1", self.plane, self.offset * f, profile), cut])

    def floor_m(self) -> float | None:
        """Height of the cut's floor along the datum normal (blind cuts only)."""
        if self.end != "blind":
            return None
        f = factor(self.unit)
        sign = 1 if self.direction() == "positive" else -1
        return (self.offset + sign * self.depth) * f

    # ---------------------------------------------------------------- face path helpers
    @staticmethod
    def parse_frame(text: str) -> tuple[list[float], list[float], list[float]]:
        """The face sketch's frame from create_sketch. Measured format: `... | result_geometry={"frame": {"origin": [..],
        "xdir": [..], "ydir": [..]}, "ref": ...}`; the older text form `frame origin [..] xdir [..] ydir [..]` is accepted too."""
        i = text.find("result_geometry=")
        if i >= 0:
            try:
                obj, _ = json.JSONDecoder().raw_decode(text[i + len("result_geometry="):])
                fr = obj["frame"]
                return [float(v) for v in fr["origin"]], [float(v) for v in fr["xdir"]], [float(v) for v in fr["ydir"]]
            except (ValueError, KeyError, TypeError):
                pass
        m = _FRAME.search(text)
        if not m:
            raise FeatureRequestError(f"create_sketch returned no frame: {text[:300]}")
        return tuple([float(v) for v in g.split(",")] for g in m.groups())  # type: ignore[return-value]

    def entities_in_face_frame(self, origin: list[float], xdir: list[float], ydir: list[float], frame: PlaneFrame) -> list[dict]:
        """Use the same validated frame mapping as profile cuts and face bosses."""
        from .face_sketch import FaceFrame
        mapping = FaceFrame(origin, xdir, ydir, frame, self.offset * factor(self.unit))
        return mapping.records([segment for shape in self.shape_segments_m() for segment in shape])

    async def _pick_face(self, ctx: FeatureContext, frame: PlaneFrame) -> dict:
        """The create_sketch arguments that select the face to sketch on: its index.
        Several planar faces at the height whose outward normal points the way the cut starts (an earlier boss or rib split the face into
        pieces): ANY of them gives the same sketch plane and frame, and the cut follows the sketch contours, not the piece (measured
        2026-10-13: a through cut sketched on one piece cut a circle lying on another). A coordinate pick (face_x/y/z) is NOT used: the server
        selects it with SolidWorks' view ray, which hit a side face. None found: refused."""
        if self.face_index is not None:
            return {"face_index": self.face_index}
        faces, _ = await read_geometry(ctx.sw)
        at = self.offset * factor(self.unit)
        ax = frame.normal_axis
        cand = [p for p in faces if p.get("planar") and abs(abs(p["normal"][ax]) - 1) < 1e-3 and abs(p["point"][ax] - at) <= POS_TOL_M]
        outward = 1 if self.direction() == "negative" else -1          # a cut going INTO the material starts on a face facing the other way
        facing = [p for p in cand if p["normal"][ax] * outward > 0]
        cand = facing or cand
        if not cand:
            raise FeatureRequestError(f"no planar face at {self.offset} {self.unit} along the {self.plane} normal: needs_human")
        return {"face_index": cand[0]["i"]}

    # ---------------------------------------------------------------- run
    def routed_to_face(self, faces: list[dict], frame: PlaneFrame) -> "CutFeature | None":
        """A PLANE target whose offset plane coincides with a DOWNWARD-facing face (outward normal opposite to the datum
        normal) makes the compiler sketch ON that face, whose frame is mirrored in sketch y relative to the datum
        (found 2026-10-12: a cut on the plane at the plate's underside missed its lobe bores). Such a cut is routed through
        the face path, which reads the real frame back. Returns the face-path request, or None when there is no hazard."""
        if self.target != "plane" or abs(self.offset) < 1e-12:
            return None
        at = self.offset * factor(self.unit)
        ax = frame.normal_axis
        down = [p for p in faces if p.get("planar") and abs(abs(p["normal"][ax]) - 1) < 1e-3
                and p["normal"][ax] < 0 and abs(p["point"][ax] - at) <= POS_TOL_M]
        if not down:
            return None
        if self.end == "mid_plane" and self.material_length is None:
            raise FeatureRequestError("a mid_plane cut on a plane that coincides with a downward face needs material_length "
                                      "(it is routed as a through cut from that face)")
        return replace(self, target="face", end="through" if self.end == "mid_plane" else self.end,
                       into=self.into or "positive", face_index=down[0]["i"] if len(down) == 1 else self.face_index)

    async def apply(self, ctx: FeatureContext) -> FeatureResult:
        frame = self.validate()
        if self.target == "plane" and abs(self.offset) > 1e-12:
            faces, _ = await read_geometry(ctx.sw)
            routed = self.routed_to_face(faces, frame)
            if routed is not None:
                res = await routed.apply(ctx)
                res.notes.insert(0, "routed to the FACE path: the offset plane coincides with a downward-facing face, whose sketch frame is "
                                    "mirrored relative to the datum (the compiler would sketch on that face)")
                return res
        res = FeatureResult(self.NAME)
        f = factor(self.unit)

        if self.target == "plane":
            graph = self.graph()

            async def action():
                return await ctx.sw.submit_feature_graph(graph, fresh_document=False)
        else:
            pick = await self._pick_face(ctx, frame)
            res.notes.append(f"face index {pick['face_index']}")

            async def action():
                sk = await ctx.sw.create_sketch(on_face=True, **pick)
                if not sk.ok:
                    return sk
                origin, xdir, ydir = self.parse_frame(sk.text)
                ents = self.entities_in_face_frame(origin, xdir, ydir, frame)
                en = await ctx.sw.add_sketch_entities(json.dumps(ents))
                if not en.ok:
                    return en
                kw = {"feature_type": "cut"}
                if self.end == "through":
                    kw["through"] = True
                else:
                    kw["depth"] = self.depth * f
                return await ctx.sw.extrude_feature(**kw)

        v = await ctx.verifier.run_step(self.label, action, self.expected())
        res.verdicts.append(v)
        if not v.ok:
            return res
        res.notes.append(f"measured volume change {v.measured_delta_mm3:.2f} mm3"
                         + (f" (upper bound {-v.bound_mm3:.2f})" if v.bound_mm3 is not None else ""))
        faces, edges = await read_geometry(ctx.sw)
        res.probes += self.probes(faces, edges, frame)
        return res

    def probes(self, faces: list[dict], edges: list[dict], frame: PlaneFrame) -> list[Probe]:
        f = factor(self.unit)
        shapes_m = self.shape_segments_m()
        cyl, lines = [], []
        for shape, segs in zip(self.shapes, shapes_m):
            for s in segs:
                if s["kind"] == "circle":
                    a, b = frame.in_plane_world(s["cx"], s["cy"])
                    cyl.append((s["diameter"] / 2, a, b))
                elif s["kind"] == "arc":
                    a, b = frame.in_plane_world(s["cx"], s["cy"])
                    cyl.append((shape.width * f / 2, a, b))
                elif s["kind"] == "line":
                    lines.append(s)
        out = [probe_cylinders(faces, cyl, frame, "round walls: radius and in-plane position of every circle / slot end")] if cyl else []
        if lines:
            out.append(probe_segments(edges, lines, frame, "straight walls: every slot / rectangle line exists as an edge at the printed position"))
        floor = self.floor_m()
        if floor is not None and not self.bound_only:
            for i, sh in enumerate(self.shapes):
                if self.floor_open:
                    out.append(self._open_floor_probe(faces, frame, floor, sh.area() * f * f, i, sh.kind))
                else:
                    out.append(probe_planar_face(faces, frame.normal_axis, floor, sh.area() * f * f, f"floor of shape {i} ({sh.kind}) at {floor * 1000:.3f} mm"))
        return out

    @staticmethod
    def _open_floor_probe(faces: list[dict], frame: PlaneFrame, floor_m: float, area_m2: float, i: int, kind: str) -> Probe:
        """The cut was declared to break through at its floor: NO planar face of the shape's area may exist at the floor height. (The volume check still fixes the
        depth: a cut that ran on past the floor removes more volume and fails there.)"""
        axis = frame.normal_axis
        hit = [(round(p["point"][axis] * 1000, 3), round(p["area"] * 1e6, 1)) for p in faces if p.get("planar") and abs(abs(p["normal"][axis]) - 1) < 1e-3
               and abs(p["point"][axis] - floor_m) <= POS_TOL_M and abs(p["area"] - area_m2) <= AREA_TOL_M2]
        return Probe(f"open floor of shape {i} ({kind}) at {floor_m * 1000:.3f} mm: no solid floor, the cut opens into empty space", not hit,
                     "no floor face there (open, as declared)" if not hit else f"a solid floor face exists (z mm, area mm2): {hit}: the cut does NOT open there")
