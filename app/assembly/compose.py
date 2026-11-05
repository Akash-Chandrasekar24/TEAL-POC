"""Compose complex assemblies (sub-assemblies, bolt-circle / row patterns) into the FLAT occurrences and mates that the runner executes.

Pure plan logic: no model calls, no COM. The result is merged into an AssemblyPlan and goes through the same validate_plan, approval and
checked execution as a hand-written flat plan. Nothing here guesses a position: every generated placement is derived from stated inputs.

TRANSFORM CONVENTION: SolidWorks' IMathTransform.ArrayData rotation block holds the images of the x, y, z axes as its ROWS (ROW-vector convention:
p' = p·M + t). Evidence: the upstream execution layer records, from a licensed machine, that a sketch frame's x-axis is d[0..2], y is d[3..5]
(SolidWorksService.Analyze.cs, "proven empirically"), and the API help lays the rotation rows out as the x, y, z axes. It was proven for a sketch
transform, not yet for a component's Transform2, so the live acceptance checklist must confirm it once with an asymmetric rotated insert.
Composition therefore is M = M_local·M_instance and t = t_local·M_instance + t_instance.
"""
from __future__ import annotations

import math
from typing import Literal

from pydantic import Field, model_validator

from app.drawing.model import Strict
from .model import IDENTITY, Mate, Occurrence, Placement, Surface, Vec3, cross, dot

_ID = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$"


class SubAssembly(Strict):
    """A reusable group. Placements and mates are in the group's own frame; ids are local."""
    name: str = Field(pattern=_ID)
    occurrences: list[Occurrence]
    mates: list[Mate] = Field(default_factory=list)


class Instance(Strict):
    id: str = Field(pattern=_ID)
    of: str
    placement: Placement
    fixed: bool = False   # False: nothing inside is fixed, so external mates must connect it


class Pattern(Strict):
    """N copies of one component. `mates` are templates: use the occurrence id "@" for the copy; the other end is an existing flat id."""
    id: str = Field(pattern=_ID)
    kind: Literal["linear", "circular"]
    bom_id: str
    base: Placement
    count: int = Field(ge=2, le=500, strict=True)
    step: Vec3 | None = None                       # linear: translation between neighbours (plan units)
    center: Vec3 | None = None                     # circular: a point on the axis (plan units)
    axis: Vec3 | None = None                       # circular: axis direction
    angle: float | None = Field(default=None, gt=0, le=360)   # circular: degrees between neighbours
    fixed: bool = False
    mates: list[Mate] = Field(default_factory=list)

    @model_validator(mode="after")
    def shape(self):
        if self.kind == "linear" and (self.step is None or not any(self.step)):
            raise ValueError("a linear pattern needs a non-zero step")
        if self.kind == "circular":
            if self.center is None or self.axis is None or self.angle is None or dot(self.axis, self.axis) < 1e-12:
                raise ValueError("a circular pattern needs center, a non-zero axis and an angle")
        return self


def _mul(a, b):
    """3x3 row-major product a·b."""
    return tuple(sum(a[3*i+k] * b[3*k+j] for k in range(3)) for i in range(3) for j in range(3))


def _row_apply(v, m):
    """Row vector times matrix: v·m."""
    return tuple(sum(v[k] * m[3*k+j] for k in range(3)) for j in range(3))


def _axis_angle(axis, degrees):
    """Active rotation about `axis` as a ROW-convention matrix (the transpose of the usual column-vector Rodrigues matrix)."""
    n = math.sqrt(dot(axis, axis))
    x, y, z = (c / n for c in axis)
    c, s, t = math.cos(math.radians(degrees)), math.sin(math.radians(degrees)), 1 - math.cos(math.radians(degrees))
    column = (t*x*x + c, t*x*y - s*z, t*x*z + s*y,
              t*x*y + s*z, t*y*y + c, t*y*z - s*x,
              t*x*z - s*y, t*y*z + s*x, t*z*z + c)
    return tuple(column[3*j+i] for i in range(3) for j in range(3))


def _surface(surface: Surface, occurrence: str) -> Surface:
    return surface.model_copy(update={"occurrence": occurrence})   # point/direction are component-local: unchanged


def compose(subassemblies: list[SubAssembly], instances: list[Instance], patterns: list[Pattern] = ()) -> tuple[list[Occurrence], list[Mate], list[str]]:
    """Flat occurrences and mates plus blockers. Flat ids: "<instance>.<local id>" and "<pattern>.<k>" (k from 1)."""
    blockers, occurrences, mates = [], [], []
    groups = {s.name: s for s in subassemblies}
    if len(groups) != len(subassemblies):
        blockers.append("Duplicate sub-assembly name")
    for inst in instances:
        group = groups.get(inst.of)
        if group is None:
            blockers.append(f"{inst.id}: unknown sub-assembly {inst.of}")
            continue
        for local in group.occurrences:
            if local.placement is None:
                blockers.append(f"{inst.id}.{local.id}: sub-assembly occurrence has no placement")
                continue
            r_i, t_i, r_l, t_l = inst.placement.rotation, inst.placement.translation, local.placement.rotation, local.placement.translation
            rotation = _mul(r_l, r_i)
            translation = tuple(a + b for a, b in zip(_row_apply(t_l, r_i), t_i))
            occurrences.append(Occurrence(
                id=f"{inst.id}.{local.id}", bom_id=local.bom_id, fixed=local.fixed and inst.fixed,
                placement=Placement(translation=translation, rotation=rotation,
                                    evidence=f"{local.placement.evidence} (in {inst.of}, placed: {inst.placement.evidence})",
                                    confirmed=local.placement.confirmed and inst.placement.confirmed)))
        for mate in group.mates:
            mates.append(mate.model_copy(update={"id": f"{inst.id}.{mate.id}", "a": _surface(mate.a, f"{inst.id}.{mate.a.occurrence}"),
                                                 "b": _surface(mate.b, f"{inst.id}.{mate.b.occurrence}")}))
    for pattern in patterns:
        for k in range(1, pattern.count + 1):
            oid, i = f"{pattern.id}.{k}", k - 1
            base = pattern.base
            if pattern.kind == "linear":
                translation, rotation = tuple(t + i * s for t, s in zip(base.translation, pattern.step)), base.rotation
            else:
                q = _axis_angle(pattern.axis, i * pattern.angle)
                translation = tuple(c + d for c, d in zip(pattern.center, _row_apply(tuple(t - c for t, c in zip(base.translation, pattern.center)), q)))
                rotation = _mul(base.rotation, q)
            occurrences.append(Occurrence(id=oid, bom_id=pattern.bom_id, fixed=pattern.fixed,
                                          placement=Placement(translation=translation, rotation=rotation,
                                                              evidence=f"{base.evidence} (pattern {pattern.id} copy {k} of {pattern.count})",
                                                              confirmed=base.confirmed)))
            for mate in pattern.mates:
                ends = [mate.a.occurrence, mate.b.occurrence]
                if ends.count("@") != 1:
                    blockers.append(f"{pattern.id}.{mate.id}: a pattern mate needs exactly one end named '@'")
                    continue
                swap = lambda s: _surface(s, oid if s.occurrence == "@" else s.occurrence)
                mates.append(mate.model_copy(update={"id": f"{oid}.{mate.id}", "a": swap(mate.a), "b": swap(mate.b)}))
    for label, values in (("occurrence", [o.id for o in occurrences]), ("mate", [m.id for m in mates])):
        seen = set()
        for v in values:
            if v in seen:
                blockers.append(f"Duplicate generated {label} id: {v}")
            seen.add(v)
    return occurrences, mates, list(dict.fromkeys(blockers))
