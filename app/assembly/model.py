"""Assembly contract. Missing manufacturing data stays missing, never inferred.

All lengths in a plan use its unit. Surface selectors use component-local
coordinates. Rotations use SolidWorks MathTransform's nine row-major values;
translation is converted to metres only at the MCP boundary.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.drawing.model import Strict

Vec3 = tuple[float, float, float]
Rotation = tuple[float, float, float, float, float, float, float, float, float]
IDENTITY: Rotation = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)


def digest(data) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


class Evidence(Strict):
    page: int = Field(ge=1)
    text: str = Field(min_length=1)


class BOMRow(Strict):
    id: str = Field(min_length=1)
    part_number: str = ""
    description: str = ""
    quantity: int | None = Field(default=None, ge=1, strict=True)
    kind: Literal["part", "assembly", "unknown"] = "unknown"
    scope: str = ""  # empty = root BOM; nested BOM is deliberately blocked
    evidence: list[Evidence] = Field(default_factory=list)
    confirmed: bool = False
    issues: list[str] = Field(default_factory=list)


class ModelAsset(Strict):
    bom_id: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_name: str = Field(min_length=1)
    configuration: str = Field(min_length=1)
    origin: Literal["supplied", "custom_features"] = "supplied"
    provenance: str = Field(min_length=1)
    confirmed: bool = False

    def path(self, assets: Path) -> Path:
        return assets / f"{self.sha256}.SLDPRT"


class Placement(Strict):
    translation: Vec3
    rotation: Rotation = IDENTITY
    evidence: str = Field(min_length=1)
    confirmed: bool = False

    @field_validator("rotation")
    @classmethod
    def rigid(cls, value):
        rows = [value[i:i+3] for i in (0, 3, 6)]
        if any(abs(dot(a, b) - (i == j)) > 1e-7 for i, a in enumerate(rows) for j, b in enumerate(rows)):
            raise ValueError("rotation must be orthonormal; scale and shear are forbidden")
        if dot(cross(rows[0], rows[1]), rows[2]) < 1 - 1e-7:
            raise ValueError("rotation must be right-handed; reflections are forbidden")
        return value

    def transform(self, scale: float) -> list[float]:
        return [*self.rotation, *(x * scale for x in self.translation), 1.0]


class Occurrence(Strict):
    id: str = Field(min_length=1)
    bom_id: str
    placement: Placement | None = None
    fixed: bool = False


class Surface(Strict):
    occurrence: str
    kind: Literal["plane", "cylinder"]
    point: Vec3
    direction: Vec3
    radius: float | None = Field(default=None, gt=0)
    # Optional extra discriminator for repeated coplanar/cylindrical faces.
    area: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def geometry(self):
        if abs(dot(self.direction, self.direction) - 1) > 1e-7:
            raise ValueError("surface direction must be a unit vector")
        if (self.kind == "cylinder") != (self.radius is not None):
            raise ValueError("only a cylinder requires a radius")
        return self


class Mate(Strict):
    id: str = Field(min_length=1)
    type: Literal["coincident", "concentric", "perpendicular", "parallel", "tangent", "distance", "angle", "lock"]
    a: Surface
    b: Surface
    alignment: Literal["aligned", "anti_aligned", "closest"] = "closest"
    flip: bool = False
    value: float | None = None  # distance: plan units; angle: degrees
    evidence: str = Field(min_length=1)
    confirmed: bool = False

    @model_validator(mode="after")
    def compatible(self):
        if self.a.occurrence == self.b.occurrence:
            raise ValueError("a mate must connect different occurrences")
        if (self.type in ("distance", "angle")) != (self.value is not None):
            raise ValueError("only distance and angle mates require a value")
        if self.type == "distance" and self.value < 0:
            raise ValueError("distance cannot be negative; use flip for direction")
        if self.type == "angle" and not 0 <= self.value <= 180:
            raise ValueError("mate angle must be between 0 and 180 degrees")
        if self.type == "concentric" and (self.a.kind != "cylinder" or self.b.kind != "cylinder"):
            raise ValueError("concentric mates require two cylindrical surfaces")
        return self


class Expectations(Strict):
    # Bounds from reviewed drawings, not predictions from the built result.
    size: Vec3 | None = None
    size_evidence: str = ""
    length_tolerance: float = Field(default=0.02, gt=0, le=1)
    rotation_tolerance: float = Field(default=1e-6, gt=0, le=1e-4)
    # Face reader rounds to 1e-6 m: do not advertise sub-micron selection.
    selector_tolerance: float = Field(default=0.002, gt=0, le=0.1)
    fully_constrained: bool = True

    @field_validator("size")
    @classmethod
    def positive_size(cls, value):
        if value is not None and any(x <= 0 for x in value):
            raise ValueError("expected sizes must be positive")
        return value


class AssemblyPlan(Strict):
    assembly_version: Literal["1"] = "1"
    name: str = Field(default="assembly", pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
    source_hash: str = Field(min_length=1)
    unit: Literal["mm", "inch"] | None = None
    unit_confirmed: bool = False
    bom_complete: bool = False
    bom: list[BOMRow] = Field(default_factory=list)
    models: list[ModelAsset] = Field(default_factory=list)
    occurrences: list[Occurrence] = Field(default_factory=list)
    mates: list[Mate] = Field(default_factory=list)
    expected: Expectations = Field(default_factory=Expectations)
    issues: list[str] = Field(default_factory=list)

    @property
    def scale(self):
        if self.unit is None:
            raise ValueError("confirm assembly units")
        return 0.001 if self.unit == "mm" else 0.0254

    def digest(self):
        return digest(self.model_dump())


def validate_plan(plan: AssemblyPlan, assets: Path | None = None) -> list[str]:
    """Collect actionable blockers. No model calls, COM or guessed geometry."""
    errors = list(plan.issues)
    if plan.unit is None or not plan.unit_confirmed:
        errors.append("Confirm the assembly length unit")
    if not plan.bom_complete:
        errors.append("Confirm the BOM is complete across all supplied sheets, including all leaf parts")
    if not plan.bom:
        errors.append("Supply and review the complete BOM")
    for label, values in (("BOM", [r.id for r in plan.bom]), ("occurrence", [o.id for o in plan.occurrences]),
                          ("mate", [m.id for m in plan.mates]), ("model BOM mapping", [m.bom_id for m in plan.models])):
        errors.extend(f"Duplicate {label}: {key}" for key, n in Counter(values).items() if n > 1)
    rows = {r.id: r for r in plan.bom}
    models = {m.bom_id: m for m in plan.models}
    counts = Counter(o.bom_id for o in plan.occurrences)
    for row in plan.bom:
        if not row.confirmed or row.issues:
            errors.append(f"{row.id}: review BOM identity, quantity and conflicts: {'; '.join(row.issues)}")
        if not row.part_number.strip() or not row.evidence:
            errors.append(f"{row.id}: supply part identity and drawing evidence")
        if row.kind != "part" or row.scope:
            errors.append(f"{row.id}: nested/unknown assembly entries require a reviewed flat leaf BOM; not supported directly")
        if row.quantity is None or counts[row.id] != row.quantity:
            errors.append(f"{row.id}: BOM quantity {row.quantity} differs from {counts[row.id]} explicit occurrences")
        if row.id not in models:
            errors.append(f"{row.id}: supply a dimensionally verified SLDPRT model or build it with the custom part workflow")
    for model in plan.models:
        if model.bom_id not in rows:
            errors.append(f"{model.bom_id}: model is not in the BOM")
        if not model.confirmed:
            errors.append(f"{model.bom_id}: confirm model identity, configuration and dimensions")
        if assets is not None:
            path = model.path(assets)
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != model.sha256:
                errors.append(f"{model.bom_id}: component file is missing or its SHA-256 changed")
    occurrences = {o.id: o for o in plan.occurrences}
    for occurrence in plan.occurrences:
        if occurrence.bom_id not in rows:
            errors.append(f"{occurrence.id}: unknown BOM row {occurrence.bom_id}")
        if occurrence.placement is None or not occurrence.placement.confirmed:
            errors.append(f"{occurrence.id}: supply and confirm an explicit placement and its dimensional evidence")
    connected = {o.id for o in plan.occurrences if o.fixed}
    if not connected:
        errors.append("Fix at least one reference component")
    for mate in plan.mates:
        if not mate.confirmed:
            errors.append(f"{mate.id}: review mate references, value and alignment")
        for ref in (mate.a, mate.b):
            if ref.occurrence not in occurrences:
                errors.append(f"{mate.id}: unknown occurrence {ref.occurrence}")
    for _ in plan.occurrences:
        for mate in plan.mates:
            ends = {mate.a.occurrence, mate.b.occurrence}
            if connected & ends:
                connected |= ends
    for occurrence in plan.occurrences:
        if occurrence.id not in connected:
            errors.append(f"{occurrence.id}: no mate connection to a fixed reference")
    if plan.expected.size is None or not plan.expected.size_evidence.strip():
        errors.append("Supply reviewed overall assembly dimensions for the final check")
    if plan.unit == "inch" and plan.expected.selector_tolerance * 0.0254 < 1e-6:
        errors.append("Selector tolerance is below the upstream face reader resolution (1 micrometre)")
    if plan.unit == "mm" and plan.expected.selector_tolerance < 0.001:
        errors.append("Selector tolerance is below the upstream face reader resolution (0.001 mm)")
    return errors
