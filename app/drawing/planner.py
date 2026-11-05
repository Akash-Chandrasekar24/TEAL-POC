"""Vision interpretation -> cross-view plan -> deterministic, provenance-bound list.

This first compiler supports rectangular plates, single plain holes, and round
bosses on front/back or a preceding boss's free end. Everything else stays an
explicit unsupported item. Neither model stage receives SolidWorks tools.
"""
from __future__ import annotations

import ast
import json
import math
import operator
import re
from typing import Literal

from pydantic import Field

from app import trace
from app.llm.base import FilePart, LLMClient
from app.menu import parse_reply
from app.perception.extract import mark_boxes
from app.runner import validate_list

from .model import DrawingModel, Strict

Kind = Literal["plate", "hole", "boss", "unsupported"]


class FeatureObservation(Strict):
    id: str
    kind: Kind
    description: str = Field(min_length=1)
    views: list[str] = Field(default_factory=list)
    dimensions: dict[str, list[str]] = Field(default_factory=dict)


class PageInterpretation(Strict):
    features: list[FeatureObservation]
    questions: list[str] = Field(default_factory=list)


class Binding(Strict):
    expression: str = Field(min_length=1, max_length=200)
    sources: dict[str, str] = Field(default_factory=dict)
    reason: str = Field("", max_length=1000)


class PlannedFeature(Strict):
    id: str = Field(pattern=r"^F[1-9][0-9]*$")
    kind: Kind
    description: str = Field(min_length=1)
    observations: list[str] = Field(min_length=1)
    parameters: dict[str, Binding] = Field(default_factory=dict)
    support: str = "base"
    face: Literal["front", "back"] = "front"
    x_from: Literal["centre", "left_edge", "right_edge"] = "centre"
    y_from: Literal["centre", "bottom_edge", "top_edge"] = "centre"
    through: bool = False


class Relationship(Strict):
    kind: Literal["concentric", "symmetric", "tangent", "same_axis", "same_feature", "perpendicular"]
    features: list[str] = Field(min_length=1)
    views: list[str] = Field(min_length=1)
    evidence: str = Field(min_length=1)


class Proposal(Strict):
    features: list[PlannedFeature]
    relationships: list[Relationship] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)


_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}


def evaluate(binding: Binding, model: DrawingModel) -> tuple[float, dict]:
    """Small arithmetic language; no eval(), calls, attributes, powers or hidden constants.

    Dimension variables are lengths. Addition/subtraction require equal units;
    multiplication/division track length powers. Final parameters must be lengths.
    Only 0, 1, and 2 are allowed as scalar literals. Zero alone denotes a stated
    centreline/origin and requires a rationale for human review.
    """
    dims = {d.id: d for d in model.dimensions}
    if any(not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]*", name) for name in binding.sources):
        raise ValueError("dimension aliases must be simple identifiers")
    try:
        tree = ast.parse(binding.expression, mode="eval")
    except SyntaxError as ex:
        raise ValueError("invalid dimension expression") from ex
    if len(list(ast.walk(tree))) > 60:
        raise ValueError("dimension expression is too complex")
    used = set()

    def walk(node):
        if isinstance(node, ast.Name):
            if node.id not in binding.sources or binding.sources[node.id] not in dims:
                raise ValueError(f"unknown dimension source for {node.id}")
            used.add(node.id)
            return dims[binding.sources[node.id]].nominal(), 1
        if isinstance(node, ast.Constant) and type(node.value) in (int, float) and node.value in (0, 1, 2):
            return float(node.value), 0
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            value, power = walk(node.operand)
            return (-value if isinstance(node.op, ast.USub) else value), power
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            a, pa = walk(node.left)
            b, pb = walk(node.right)
            if isinstance(node.op, (ast.Add, ast.Sub)) and pa != pb:
                raise ValueError("cannot add lengths and scalars")
            power = pa + pb if isinstance(node.op, ast.Mult) else pa - pb if isinstance(node.op, ast.Div) else pa
            try:
                value = _OPS[type(node.op)](a, b)
            except (ZeroDivisionError, OverflowError) as ex:
                raise ValueError("dimension expression cannot be evaluated") from ex
            if not math.isfinite(value):
                raise ValueError("dimension expression must produce a finite value")
            return value, power
        raise ValueError("only dimension names, +, -, *, / and scalar literals 0/1/2 are allowed")

    value, power = walk(tree.body)
    if used != set(binding.sources):
        raise ValueError("every cited dimension must actually be used")
    if not used:
        if not isinstance(tree.body, ast.Constant) or tree.body.value != 0:
            raise ValueError("a length needs dimension sources; only a stated origin may be literal zero")
    elif power != 1:
        raise ValueError("expression must have length units")
    derived = not isinstance(tree.body, ast.Name)
    if derived and not binding.reason.strip():
        raise ValueError("derived dimensions and origin positions need an explanation")
    return value, {"value": value, "unit": model.unit, "expression": binding.expression,
                   "sources": {alias: {"id": ref, "page": dims[ref].page, "views": dims[ref].views,
                                       "printed": dims[ref].text, "effective": dims[ref].effective_text,
                                       "confirmed": dims[ref].confirmed} for alias, ref in binding.sources.items()},
                   "derived": derived, "reason": binding.reason}


VISION_PROMPT = """Interpret ONE engineering drawing page using its numbered dimension/view evidence supplied as text.
Return physical feature observations, not a build list. Identify every geometric feature, including repeated holes,
edge treatments and geometry the initial compiler cannot build. Allowed kinds: plate (rectangular base only),
hole (one plain circular hole), boss (one circular boss), unsupported (everything else).
Use ids P<page>.f1, P<page>.f2, ... . Each feature has description, views (P<page>.vN),
and dimensions: mapping from geometric role (width, height, thickness, diameter, x, y, depth, etc.) to dimension IDs.
Use only supplied IDs; include the view that supplies depth as well as location. Do not equate equal numbers with
the same dimension or feature. Empty roles or missing information require a precise question. Never measure pixels.
A repeated-hole group with incomplete individual positions is unsupported; never silently build only one hole.
Use section views and their cutting-plane references to identify bores, cavities, shoulders, blind depths and wall
thicknesses; cite the section view and its measurement IDs. Hatching denotes cut material, not grooves or ribs.
Cross-check sections with external views. Missing cutting-plane identity, unclear internal shape or conflicting
section dimensions require precise questions. Retain section questions until human clarification resolves them.
Notes, threads, countersinks, fillets, sloping bases, rings, slots and other excluded details must be unsupported.
Human corrections override conflicting image digits. JSON only, matching this schema:
"""

PLAN_PROMPT = """Reconcile feature observations from every page into ONE CAD plan. JSON only.
This compiler supports only: one rectangular plate base; plain holes; circular bosses. Other geometry MUST remain
an unsupported feature. Do not approximate a different outline, thread, countersink, bend, fillet or slot.
Plate parameters: width,height,thickness. Hole: diameter,x,y and depth (blind) OR material_length (through=true).
Boss: diameter,x,y,height. Give every required parameter, including positions; never default an unspecified position.
Each parameter is {expression: "d", sources: {d: "P1.d3"}, reason: ""}. Arithmetic +,-,*,/ with scalar 0,1,2 only;
all named dimensions must be used. Derived values need a reason. Literal 0 is ONLY a visibly stated centreline/origin,
with a reason citing its view; never use zero to hide a missing dimension. No pixel measurements or invented lengths.
support=base means face front/back of the plate. support=F2 means the free end of a preceding boss F2 (face must be front).
Positions use x_from/y_from centre or named edges. On a boss, edge words mean its own outer extent.
Feature IDs are F1,F2,... . Every feature must cite one or more observation IDs; account for ALL observations,
exactly once each. Merge observations across views only when geometry establishes the SAME physical feature;
equal numbers or similar names alone are not enough. Conflicting readings or identities require targeted questions.
Order the base first, plate holes before bosses, and holes in a boss after that boss.
Through cuts remove ALL material along the line; use a cited blind depth when a hole must stop before other material.
Model relationships are proposals for human review, with feature IDs, source view IDs and evidence. Carry ALL unresolved questions; never override
source units/projection, confirm dimensions yourself, or drop unsupported observations. Human note is clarification,
not permission to invent values. Output matching this schema:
"""


def interpret(llm: LLMClient, model: DrawingModel, pictures: dict[int, bytes]) -> list[PageInterpretation]:
    from app.parallel import ordered_map
    by_dim = {d.id: d for d in model.dimensions}
    def read_one(page):
        if page.no not in pictures:
            raise ValueError(f"Page {page.no}: source picture is unavailable; read the drawing again")
        context = {"page": page.model_dump(), "unit": model.unit, "projection": model.projection,
                   "box_legend": {str(i): view.id for i, view in enumerate(page.views, 1)},
                   "dimensions": [d.model_dump() for d in model.dimensions if d.page == page.no],
                   "human_note": model.resolutions.note}
        system = VISION_PROMPT + json.dumps(PageInterpretation.model_json_schema())
        marked = mark_boxes(pictures[page.no], [v.box for v in page.views])
        parts = [FilePart(marked, "image/png"), json.dumps(context, ensure_ascii=False)]
        response = llm.generate_json(system, parts)
        try:
            parsed = PageInterpretation.model_validate(response.data)
            views = {v.id for v in page.views}
            seen = set()
            for feature in parsed.features:
                if not re.fullmatch(rf"P{page.no}\.f[1-9][0-9]*", feature.id) or feature.id in seen:
                    raise ValueError(f"Page {page.no}: invalid or duplicate feature observation {feature.id}")
                seen.add(feature.id)
                if not set(feature.views) <= views:
                    raise ValueError(f"{feature.id}: unknown source view")
                for refs in feature.dimensions.values():
                    if any(ref not in by_dim or by_dim[ref].page != page.no for ref in refs):
                        raise ValueError(f"{feature.id}: unknown source dimension or wrong page")
        except ValueError:
            getattr(llm, "discard", lambda *a: None)(system, parts)
            raise
        trace.current().event("interpret", "page features", page=page.no, features=len(parsed.features), questions=parsed.questions)
        return parsed
    return ordered_map(read_one, model.pages)


def compile_plan(model: DrawingModel, interpretations: list[PageInterpretation], proposal: Proposal) -> dict:
    """Compile only the supported subset; reasons are copied into needs_human."""
    reasons = list(model.issues) + [q for page in interpretations for q in page.questions] + list(proposal.questions)
    observed = {f.id: f for page in interpretations for f in page.features}
    views = {v.id for page in model.pages for v in page.views}
    planned = {f.id: f for f in proposal.features}
    if len(planned) != len(proposal.features):
        reasons.append("Plan has duplicate feature IDs")
    covered = set()
    for feature in proposal.features:
        pages = [ref.split(".", 1)[0] for ref in feature.observations if ref in observed]
        if feature.kind != "unsupported" and len(pages) != len(set(pages)):
            reasons.append(f"{feature.id}: distinct physical observations on one page cannot be collapsed into one feature")
        for ref in feature.observations:
            if ref not in observed or ref in covered:
                reasons.append(f"{feature.id}: unknown or multiply assigned observation {ref}")
            elif feature.kind != "unsupported" and observed[ref].kind != feature.kind:
                reasons.append(f"{feature.id}: plan changes observation kind {ref}")
            covered.add(ref)
    if observed.keys() - covered:
        reasons.append(f"Unaccounted geometry observations: {sorted(observed.keys() - covered)}")
    if not observed:
        reasons.append("No physical features identified; confirm the drawing contents")
    for relation in proposal.relationships:
        if not set(relation.features) <= planned.keys() or not set(relation.views) <= views:
            reasons.append("A proposed relationship references unknown features or views")

    items, unsupported, provenance, built = [], [], {}, set()
    for feature in proposal.features:
        if feature.kind == "unsupported":
            unsupported.append({"id": f"U{len(unsupported) + 1}", "text": f"{feature.id}: {feature.description}"})
            continue
        required = {"plate": {"width", "height", "thickness"}, "boss": {"diameter", "x", "y", "height"},
                    "hole": {"diameter", "x", "y", "material_length" if feature.through else "depth"}}[feature.kind]
        if set(feature.parameters) != required:
            reasons.append(f"{feature.id}: required parameters {sorted(required)}; received {sorted(feature.parameters)}")
            continue
        if feature.kind != "hole" and feature.through:
            reasons.append(f"{feature.id}: through applies only to a hole")
            continue
        source_refs = {ref for obs in feature.observations if obs in observed for ids in observed[obs].dimensions.values() for ref in ids}
        try:
            values, evidence = {}, {}
            for key, binding in feature.parameters.items():
                # A planner cannot attach arbitrary unrelated readings to a feature.
                if not set(binding.sources.values()) <= source_refs:
                    raise ValueError(f"{key}: source dimension was not attributed to this feature")
                values[key], evidence[key] = evaluate(binding, model)
            if feature.kind == "plate":
                if items or feature.support != "base" or feature.face != "front":
                    raise ValueError("the rectangular plate must be the first and only base")
                params = {"profile": {"shape": "rectangle", "width": values["width"], "height": values["height"]}, "depth": values["thickness"]}
                menu_name = "extrude_profile_boss"
            else:
                if not items:
                    raise ValueError("a rectangular plate base is required first")
                if feature.kind == "hole" and feature.support == "base" and any(planned[i].kind == "boss" for i in built):
                    raise ValueError("build plate holes before bosses to avoid split-face selection")
                placement = {"face": feature.face}
                if feature.support != "base":
                    parent = planned.get(feature.support)
                    if feature.support not in built or parent is None or parent.kind != "boss" or feature.face != "front":
                        raise ValueError("support must be a preceding boss's free end, or base front/back")
                    placement = {"on": f"{feature.support}.top"}
                position = {"x": values["x"], "y": values["y"], "x_from": feature.x_from, "y_from": feature.y_from}
                if feature.kind == "hole":
                    params = {**placement, "positions": [position], "diameter": values["diameter"], "depth": "through" if feature.through else values["depth"]}
                    if feature.through:
                        params["material_length"] = values["material_length"]
                    menu_name = "hole"
                else:
                    params = {**placement, "circles": [{"position": position, "diameter": values["diameter"]}], "height": values["height"]}
                    menu_name = "circle_boss"
            derived = any(e["derived"] for e in evidence.values())
            cited = sorted({ref for b in feature.parameters.values() for ref in b.sources.values()})
            by_id = {d.id: d for d in model.dimensions}
            items.append({"id": feature.id, "feature": menu_name, "params": params,
                          "printed": "; ".join(f"{ref}: {by_id[ref].effective_text}" for ref in cited),
                          "derived": derived, "derived_from": json.dumps(evidence, ensure_ascii=False) if derived else "",
                          "note": feature.description})
            provenance[feature.id] = {"observations": feature.observations, "parameters": evidence}
            built.add(feature.id)
        except ValueError as ex:
            reasons.append(f"{feature.id}: {ex}")
    if unsupported:
        reasons.append("Unsupported geometry remains; this initial planner requires a complete supported part")
    reasons = list(dict.fromkeys(reasons))
    draft = {"list_version": "1", "part_name": model.part_name, "unit": model.unit if model.unit != "unknown" else "mm",
             "projection": model.projection, "seen_dimensions": [d.effective_text for d in model.dimensions] + [d.token for d in model.dimensions if d.token],
             "items": items, "unsupported": unsupported, "needs_human": reasons, "accepted_gaps": []}
    # Unknown units use a schema-compatible placeholder only inside a blocked
    # draft; needs_human prevents approval and no downloadable list is offered.
    offline_reasons = validate_list(parse_reply(draft))
    if not reasons and offline_reasons:
        reasons.extend(offline_reasons)
        draft["needs_human"] = reasons
    return {"version": "1", "model_hash": model.digest(), "status": "needs_information" if reasons else "ready_for_approval",
            "questions": reasons, "draft": draft, "provenance": provenance,
            "interpretations": [p.model_dump() for p in interpretations], "proposal": proposal.model_dump(),
            "validation": offline_reasons, "approved": False}


def plan(llm: LLMClient, model: DrawingModel, pictures: dict[int, bytes]) -> dict:
    if model.issues:
        return {"version": "1", "model_hash": model.digest(), "status": "needs_review", "questions": model.issues,
                "draft": None, "provenance": {}, "approved": False, "calls": 0}
    interpreted = interpret(llm, model, pictures)
    return plan_interpreted(llm, model, interpreted)


def plan_interpreted(llm: LLMClient, model: DrawingModel, interpreted: list[PageInterpretation]) -> dict:
    """Existing text planning/compilation stage, after extraction has finished."""
    prompt = {"drawing": model.model_dump(), "interpretations": [p.model_dump() for p in interpreted]}
    system, parts = PLAN_PROMPT + json.dumps(Proposal.model_json_schema()), [json.dumps(prompt, ensure_ascii=False)]
    response = llm.generate_json(system, parts)
    try:
        proposal = Proposal.model_validate(response.data)
    except ValueError:
        getattr(llm, "discard", lambda *a: None)(system, parts)
        raise
    report = compile_plan(model, interpreted, proposal)
    report["calls"] = len(model.pages) + 1
    trace.current().event("plan", "draft validation", status=report["status"], questions=report["questions"], items=len(report["draft"]["items"]))
    return report
