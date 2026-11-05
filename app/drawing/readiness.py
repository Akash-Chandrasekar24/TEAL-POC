"""Evidence-based reconstruction triage; never a geometric accuracy certificate."""
from __future__ import annotations

import io
import json
from typing import Literal

from PIL import Image
from pydantic import Field

from app.llm.base import FilePart
from .model import Strict


# Application planning/execution coverage, rather than the larger upstream tool catalog.
COVERAGE = {
    "rectangular_plate": ("automatic", "Existing provenance-bound plate compiler"),
    "plain_hole": ("automatic", "Existing compiler; complete individual positions and depths required"),
    "circular_boss": ("automatic", "Existing compiler on supported planar references"),
    "profile_extrusion": ("manual_plan", "Legacy menu supports reviewed explicit/tangent-chain outlines"),
    "turned_part": ("manual_plan", "Legacy revolve base only; no later features"),
    "turned_followups": ("unsupported", "Cross holes, flats and keyways after a turned base are not integrated"),
    "general_edge_treatments": ("partial", "Current edge recipes cover selected base-plane edges only"),
    "angled_features": ("unsupported", "General inclined-plane placement is not exposed to the application planner"),
    "internal_passages": ("partial", "Only reviewed supported cuts/terminations; hidden geometry needs detail sheets"),
    "sheet_metal": ("unsupported", "No validated application bend/flat-pattern recipe"),
    "gears": ("unsupported", "No validated tooth/profile and gear-train reconstruction recipe"),
    "springs": ("unsupported", "No validated helical spring construction recipe"),
    "assembly": ("manual_plan", "Reviewed native models, quantities, placements and mates are required"),
    "supplier_components": ("manual_plan", "Supplier SLDPRT models and configurations must be supplied"),
    "piping": ("unsupported", "No integrated routing and connectivity planner"),
    "section_detail_views": ("unsupported", "Section/detail drawing layout is not reconstructed from raster sheets"),
    "bom_balloons": ("unsupported", "No validated drawing BOM/balloon recreation workflow"),
    "drawing_annotations": ("partial", "Model dimensions cannot recover every source note, tolerance or annotation"),
}


class Requirement(Strict):
    capability: str = Field(min_length=1)
    evidence: str = Field(min_length=1, max_length=1000)


class VisualAssessment(Strict):
    document_kind: Literal["part", "assembly", "detail_sheet", "mixed", "unknown"]
    summary: str = Field(min_length=1, max_length=1500)
    components: list[str] = Field(default_factory=list)
    requirements: list[Requirement] = Field(default_factory=list)
    unreadable: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)


PROMPT = """Assess this engineering drawing for reconstructing BOTH editable 3D SolidWorks models
and native 2D drawing sheets. This is capability triage, not geometry generation.
Identify whether it describes a single part, an assembly, several detail parts, or is unknown.
List required capabilities with visible view/callout evidence. Include sections/details, notes,
BOM balloons, gears, springs, turned parts and later cross holes, angled features, internal passages,
supplier components and piping when shown. Do not silently reduce a complex part to a simple plate.
Mention application UI chrome when the input is a screenshot. Identify unreadable digits/tables,
missing continuation sheets and geometric information needed from component detail drawings/models.
Never invent dimensions, tooth counts, tolerances, materials, hidden geometry or placements.
Do not equate multiple pictured components with one part. Do not claim any reconstruction is accurate.
Use these capability names where applicable: """ + ", ".join(COVERAGE) + ". JSON matching this schema:\n"


def source_quality(png: bytes) -> dict:
    with Image.open(io.BytesIO(png)) as image:
        width, height = image.size
    warnings = []
    if min(width, height) < 500:
        warnings.append("Small native image: dense dimension text may be unrecoverable. Use the original PDF or a higher-resolution scan.")
    elif max(width, height) < 1400:
        warnings.append("Limited native resolution: verify fine dimensions, symbols and BOM text against original detail sheets.")
    return {"width": width, "height": height, "warnings": warnings,
            "accuracy_verified": False, "upscaling_restores_missing_detail": False}


def assess_page(llm, png: bytes) -> VisualAssessment:
    system = PROMPT + json.dumps(VisualAssessment.model_json_schema())
    parts = [FilePart(png, "image/png")]
    response = llm.generate_json(system, parts)
    try:
        result = VisualAssessment.model_validate(response.data)
        if not result.requirements and result.document_kind != "unknown":
            raise ValueError("The assessment must account for the visible geometry or explicitly remain unknown")
    except ValueError:
        getattr(llm, "discard", lambda *a: None)(system, parts)
        raise
    return result


def readiness(assessments: list[VisualAssessment], qualities: list[dict]) -> dict:
    requirements, blockers = [], []
    for page, assessment in enumerate(assessments, 1):
        for item in assessment.requirements:
            level, limit = COVERAGE.get(item.capability, ("unsupported", "Unrecognized capability; engineering review required"))
            requirements.append({"page": page, **item.model_dump(), "support": level, "limit": limit})
            if level != "automatic":
                blockers.append(f"Page {page}: {item.capability}: {limit}")
        blockers.extend(f"Page {page}: {q}" for q in assessment.unreadable + assessment.missing_information)
        if assessment.document_kind in ("assembly", "mixed", "detail_sheet"):
            blockers.append(f"Page {page}: identify/review each physical component and its detail geometry before assembling")
    if not assessments:
        blockers.append("No visual scope assessment has been performed")
    blockers.append("Engineer-reviewed source dimensions and reference-CAD acceptance measurements are not supplied by this assessment")
    blockers.append("Native 2D sheet fidelity (views, sections, dimensions, tolerances, BOM and notes) needs independent verification")
    return {"status": "not_verified_for_high_accuracy", "accuracy_verified": False,
            "assessments_reviewed": False, "requirements": requirements,
            "source_warnings": [q for p in qualities for q in p["warnings"]],
            "blockers": list(dict.fromkeys(blockers)), "goals": ["3d_models", "native_2d_sheets"]}
