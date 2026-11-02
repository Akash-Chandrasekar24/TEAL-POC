"""General feature: holes (plain, counterbore, countersink; a threaded hole is a plain hole plus a note), composed from existing features.

  plain        one cut_feature: n circles of diameter d, through or blind.
  counterbore  cut 1: the counterbore (D x c); cut 2: the hole d through what remains (length L - c).
  countersink  a plain hole, then one 45-degree chamfer per hole rim (fillet_chamfer_edges). A 90-degree included angle is an equal
               chamfer of (D_cs - d) / 2; other angles need an UNEQUAL chamfer, which is directional and not probed yet: refused.
  thread_note  carried in the result notes only. The hole is cut at the nominal size; no thread is modelled.

All numbers in the drawing's unit, positions as printed (the datum frame, like cut_feature). `material_length` = the thickness the
hole crosses (required for a through hole: the volume check is then exact, and the library never guesses it). Blind holes are flat
bottomed (no drill-point cone). The start is a planar face at `offset` (target='face', the cut goes into the material) or a datum
plane at `offset` (target='plane').

Expected volume removed, per hole (mm3):  plain pi/4 d^2 L;  counterbore pi/4 D^2 c + pi/4 d^2 (L - c) (= pi/4 d^2 L + ring);
countersink pi/4 d^2 L + (c^2 / 2) x 2 pi (d/2 + c/3) with c = (D_cs - d) / 2 (the removed section lies OUTSIDE the hole radius).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .base import (FeatureContext, FeatureRequestError, FeatureResult, PlaneFrame, Position, Probe, factor, probe_planar_face,
                   read_geometry, ring_area)
from .cut_feature import CutFeature, CutShape
from .edge_feature import EdgeSelector, FilletChamferEdges

MM3_PER_UNIT3 = {"mm": 1.0, "inch": 25.4 ** 3}
STYLES = ("plain", "counterbore", "countersink")


@dataclass(frozen=True)
class Hole:
    unit: str
    plane: str                                   # 'top' | 'front': the view the positions are printed in (datum frame)
    positions: list[Position]                    # the hole CENTRES as printed; every hole gets the same spec
    diameter: float
    style: str = "plain"
    end: str = "through"                         # 'through' | 'blind'
    depth: float | None = None                   # blind: hole depth from the start (= material crossed, flat bottom)
    material_length: float | None = None         # through: the thickness the hole crosses (required: never guessed)
    target: str = "face"                         # 'face': a planar face at `offset`, cut into the material; 'plane': a datum plane
    offset: float = 0.0                          # height of the start face / plane along the datum normal
    into: str | None = None                      # 'positive' | 'negative'; default: face -> negative, plane -> positive
    counterbore_diameter: float | None = None
    counterbore_depth: float | None = None
    countersink_diameter: float | None = None    # diameter at the surface
    countersink_angle_deg: float = 90.0          # included angle: only 90 is built
    thread_note: str | None = None               # e.g. "M6": informational, no thread is modelled
    outline_wh: tuple[float, float] | None = None
    floor_open: bool = False                     # blind only: the hole's bottom is EXPECTED to open into empty space / a hollow (no solid floor face); the counterbore step is unaffected
    bound_only: bool = False                     # a human accepted that the hole reaches past the straight part of a rounded / stepped side: it cuts LESS than its full size
    label: str = "hole"

    NAME = "hole"

    # ---------------------------------------------------------------- pure parts
    def length(self) -> float:
        """Material crossed by the hole (L)."""
        return self.material_length if self.end == "through" else self.depth

    def countersink_size(self) -> float:
        """The equal chamfer that opens the hole to the countersink diameter."""
        return (self.countersink_diameter - self.diameter) / 2

    def validate(self) -> PlaneFrame:
        frame = PlaneFrame(self.plane)
        f = factor(self.unit)
        if not self.positions:
            raise FeatureRequestError("no hole positions")
        if not self.diameter or self.diameter <= 0:
            raise FeatureRequestError("the hole needs a positive diameter")
        if self.style not in STYLES:
            raise FeatureRequestError(f"style must be one of {STYLES}, got {self.style!r}")
        if self.end not in ("through", "blind"):
            raise FeatureRequestError(f"end must be 'through' or 'blind', got {self.end!r}")
        if self.target not in ("face", "plane"):
            raise FeatureRequestError(f"target must be 'face' or 'plane', got {self.target!r}")
        if self.end == "through":
            if self.depth is not None:
                raise FeatureRequestError("a through hole takes material_length (the thickness it crosses), not depth")
            if not self.material_length or self.material_length <= 0:
                raise FeatureRequestError("a through hole needs material_length (the thickness it crosses): the volume check is exact "
                                          "only then, and the library does not guess it: needs_human")
        else:
            if not self.depth or self.depth <= 0:
                raise FeatureRequestError("a blind hole needs a positive depth")
            if self.material_length is not None:
                raise FeatureRequestError("a blind hole takes depth, not material_length")
        length = self.length()
        has_cb = self.counterbore_diameter is not None or self.counterbore_depth is not None
        has_cs = self.countersink_diameter is not None
        if self.style != "counterbore" and has_cb:
            raise FeatureRequestError(f"counterbore fields given but the style is {self.style!r}")
        if self.style != "countersink" and has_cs:
            raise FeatureRequestError(f"countersink_diameter given but the style is {self.style!r}")
        widest = self.diameter
        if self.style == "counterbore":
            if not (self.counterbore_diameter and self.counterbore_depth):
                raise FeatureRequestError("a counterbore needs counterbore_diameter and counterbore_depth")
            if self.counterbore_diameter <= self.diameter:
                raise FeatureRequestError("counterbore_diameter must be larger than the hole diameter")
            if not 0 < self.counterbore_depth < length:
                raise FeatureRequestError(f"counterbore_depth must be positive and shallower than the hole ({length})")
            widest = self.counterbore_diameter
        if self.style == "countersink":
            if self.target != "face":
                raise FeatureRequestError("a countersink opens the rim of a hole on a FACE: use target='face'")
            if not has_cs or self.countersink_diameter <= self.diameter:
                raise FeatureRequestError("a countersink needs countersink_diameter larger than the hole diameter")
            if not math.isclose(self.countersink_angle_deg, 90.0, abs_tol=1e-9):
                raise FeatureRequestError(f"countersink angle {self.countersink_angle_deg} degrees needs an unequal chamfer, which is "
                                          "directional and has not been probed yet: only 90 degrees is built: needs_human")
            if self.countersink_size() >= length:
                raise FeatureRequestError("the countersink is deeper than the hole")
            widest = self.countersink_diameter
        if self.thread_note is not None and not str(self.thread_note).strip():
            raise FeatureRequestError("thread_note is empty")
        if self.floor_open and self.end != "blind":
            raise FeatureRequestError("floor_open applies to a blind hole only: a through hole has no floor")
        centres = [p.to_sketch_m(self.unit, self.outline_wh) for p in self.positions]
        for i in range(len(centres)):
            for j in range(i + 1, len(centres)):
                if math.dist(centres[i], centres[j]) < widest * f - 1e-12:
                    raise FeatureRequestError(f"holes {i} and {j} overlap (centres closer than {widest} {self.unit})")
        return frame

    def direction(self) -> str:
        return self.into or ("negative" if self.target == "face" else "positive")

    def floor_height(self, depth: float) -> float:
        """Height (drawing unit, along the datum normal) `depth` away from the start, in the direction of the cut."""
        return self.offset + (-depth if self.direction() == "negative" else depth)

    def _cut(self, label: str, diameter: float, end: str, depth: float | None, material_length: float, floor_open: bool = False) -> CutFeature:
        shapes = [CutShape("circle", p, diameter=diameter) for p in self.positions]
        return CutFeature(self.unit, self.plane, shapes, end=end, depth=depth, target=self.target, offset=self.offset,
                          into=self.into, material_length=material_length, outline_wh=self.outline_wh, floor_open=floor_open, bound_only=self.bound_only,
                          label=f"{self.label}: {label}")

    def steps(self) -> list[CutFeature | FilletChamferEdges]:
        """The features that build this hole, in order (each one is verified by its own volume and probes)."""
        self.validate()
        length = self.length()
        end, depth = ("through", None) if self.end == "through" else ("blind", length)
        if self.style == "counterbore":
            c = self.counterbore_depth
            return [self._cut("counterbore", self.counterbore_diameter, "blind", c, c),                            # the counterbore always has a solid floor ring
                    self._cut("hole below the counterbore", self.diameter, end, depth, length - c, floor_open=self.floor_open)]
        out: list[CutFeature | FilletChamferEdges] = [self._cut("hole", self.diameter, end, depth, length, floor_open=self.floor_open)]
        if self.style == "countersink":
            f = factor(self.unit)
            r = self.diameter / 2
            for k, p in enumerate(self.positions):
                x, y = (v / f for v in p.to_sketch_m(self.unit, self.outline_wh))
                region = (x - r - 0.5, x + r + 0.5, y - r - 0.5, y + r + 0.5)
                out.append(FilletChamferEdges(self.unit, "chamfer", self.countersink_size(),
                                              EdgeSelector(self.plane, self.offset, region=region, kinds=("circle",), radius=r),
                                              expect_count=1, label=f"{self.label}: countersink {k + 1}"))
        return out

    def expected_total_mm3(self) -> float:
        """Signed volume change of the whole feature (negative: material removed)."""
        n, d, length = len(self.positions), self.diameter, self.length()
        total = n * math.pi / 4 * d * d * length
        if self.style == "counterbore":
            total += n * ring_area(self.counterbore_diameter, d) * self.counterbore_depth
        elif self.style == "countersink":
            c = self.countersink_size()
            total += n * (c * c / 2) * 2 * math.pi * (d / 2 + c / 3)
        return -total * MM3_PER_UNIT3[self.unit]

    def floor_probes(self, faces: list[dict], frame: PlaneFrame) -> list[Probe]:
        """The counterbore floors: one ring face per hole at the counterbore depth."""
        if self.style != "counterbore":
            return []
        f = factor(self.unit)
        at = self.floor_height(self.counterbore_depth) * f
        ring = ring_area(self.counterbore_diameter, self.diameter) * f * f
        return [probe_planar_face(faces, frame.normal_axis, at, ring, f"counterbore floor ring (each of {len(self.positions)}) at {at * 1000:.3f} mm")]

    # ---------------------------------------------------------------- run
    async def apply(self, ctx: FeatureContext) -> FeatureResult:
        frame = self.validate()
        res = FeatureResult(self.NAME)
        res.notes.append(f"expected total volume change {self.expected_total_mm3():,.2f} mm3 ({len(self.positions)} hole(s), style {self.style})")
        for step in self.steps():
            sub = await step.apply(ctx)
            res.verdicts += sub.verdicts
            res.probes += sub.probes
            res.notes += [f"{step.label}: {n}" for n in sub.notes]
            if not sub.ok:
                res.notes.append(f"stopped at {step.label}: the later steps were not run")
                return res
        if self.style == "counterbore" and not self.bound_only:
            faces, _ = await read_geometry(ctx.sw)
            res.probes += self.floor_probes(faces, frame)
        if self.thread_note:
            res.notes.append(f"thread note (NOT modelled, the hole is cut at the nominal size): {self.thread_note}")
        return res
