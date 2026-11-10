"""One assembly from the parts "Build everything" built: the built SLDPRT files become the components, the AI drafts where each one goes.

The AI sees the sheet's assembly / section views and, for every built part, its name, quantity and measured bounding box IN ITS OWN FRAME.
It answers, per copy of a part, where that frame's origin goes in the assembly (mm) and which assembly axes the part's own x and y axes
point along (axis-aligned: 24 orientations), with the view and printed dimensions it used. Every placement is fixed (no mates yet) and
NOTHING is confirmed here: the designer approves the whole plan once, and only then is it built (the existing assembly runner checks it).
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import Field

from app.drawing.model import Strict
from app.llm.base import FilePart
from .model import AssemblyPlan, BOMRow, Evidence, Expectations, ModelAsset, Occurrence, Placement

AXES = {"+X": (1.0, 0.0, 0.0), "-X": (-1.0, 0.0, 0.0), "+Y": (0.0, 1.0, 0.0), "-Y": (0.0, -1.0, 0.0), "+Z": (0.0, 0.0, 1.0), "-Z": (0.0, 0.0, -1.0)}

PROMPT = """You place the parts of ONE machine into its assembly, from the assembly and section views of its drawing (the picture).
Each part below was already modelled in its OWN frame; its bounding box in that frame is given in mm (lo = minimum corner, hi = maximum).
Turned parts have their axis along their own x; flat parts lie in their own x-y plane with the thickness along +z.
Choose ONE assembly frame (say which in "frame": e.g. "origin on the main shaft axis at the rear face of the rear housing, X along the shaft").
For EVERY copy of every part (quantity copies) give: where that part's own origin goes, in mm in the assembly frame, and which assembly axis the
part's own x axis and own y axis point along (one of +X -X +Y -Y +Z -Z; they must be perpendicular). Use printed dimensions, the stack-up of
widths shown in the section views, the bolt circles and the shaft axis; never invent a part. "evidence": the view and printed values you used.
Also give the overall assembly size (bounding box extents in mm, X Y Z) with its evidence.
Return JSON only: {"frame": "...", "placements": [{"part": "<part id>", "copy": 1, "origin": [x, y, z], "x_to": "+X", "y_to": "+Y",
"evidence": "..."}], "size": [sx, sy, sz], "size_evidence": "...", "notes": ["anything uncertain"]}"""


class DraftPlacement(Strict):
    part: str
    copy: int = Field(1, ge=1)
    origin: list[float] = Field(min_length=3, max_length=3)
    x_to: str
    y_to: str
    evidence: str = Field(min_length=1)


class DraftAssembly(Strict):
    frame: str = ""
    placements: list[DraftPlacement]
    size: list[float] | None = None
    size_evidence: str = ""
    notes: list[str] = []


def rotation(x_to: str, y_to: str) -> list[float]:
    """Row-vector rotation (the rows are the images of the part's x, y, z axes; see compose.py) from two axis names."""
    if x_to not in AXES or y_to not in AXES:
        raise ValueError(f"axis names are +X -X +Y -Y +Z -Z, got {x_to!r} {y_to!r}")
    x, y = AXES[x_to], AXES[y_to]
    if any(a * b for a, b in zip(x, y)) != 0 or x == y or x == tuple(-v for v in y):
        raise ValueError(f"x_to {x_to} and y_to {y_to} must be perpendicular")
    z = (x[1] * y[2] - x[2] * y[1], x[2] * y[0] - x[0] * y[2], x[0] * y[1] - x[1] * y[0])
    return [*x, *y, *z]


def draft_placements(llm, picture: bytes, parts: list[dict]) -> DraftAssembly:
    context = [{"id": p["id"], "name": p["name"], "quantity": p["quantity"], "bbox_mm_own_frame": p["bbox_mm"]} for p in parts]
    reply = llm.generate_json(PROMPT, [FilePart(picture, "image/png"), json.dumps({"parts": context}, ensure_ascii=False)])
    return DraftAssembly.model_validate(reply.data)


def plan_from_parts(name: str, source_hash: str, unit: str, built: list[dict], draft: DraftAssembly, bom_page: int = 1) -> tuple[AssemblyPlan, list[str]]:
    """built: [{id, name, item, quantity, sha256, source_name, bbox_mm, job}]. Returns the plan (nothing confirmed) and the problems found
    in the AI's draft (missing copies, bad axes): those are listed for the designer, never silently fixed."""
    problems: list[str] = []
    rows, models, occurrences = [], [], []
    by_part: dict[str, list[DraftPlacement]] = {}
    for d in draft.placements:
        by_part.setdefault(d.part, []).append(d)
    for p in built:
        rid = f"item-{p['id']}"[:40]
        rows.append(BOMRow(id=rid, part_number=p["name"], description=p["name"], quantity=p["quantity"], kind="part",
                           evidence=[Evidence(page=bom_page, text=f"parts list: {p['item'] + ' ' if p.get('item') else ''}{p['name']} x{p['quantity']}")]))
        models.append(ModelAsset(bom_id=rid, sha256=p["sha256"], source_name=p["source_name"], configuration="Default",
                                 origin="custom_features", provenance=f"built by this app from the drawing (job {p['job']})"))
        mine = sorted(by_part.get(p["id"], []), key=lambda d: d.copy)[:p["quantity"]]
        if len(mine) < p["quantity"]:
            problems.append(f"{p['name']}: the AI placed {len(mine)} of {p['quantity']} copies")
        for n, d in enumerate(mine, 1):
            try:
                rot = rotation(d.x_to, d.y_to)
            except ValueError as ex:
                problems.append(f"{p['name']} copy {n}: {ex}")
                continue
            occurrences.append(Occurrence(id=f"{rid}-{n}"[:60], bom_id=rid, fixed=True,
                                          placement=Placement(translation=d.origin, rotation=rot, evidence=d.evidence)))
    unknown = sorted(set(by_part) - {p["id"] for p in built})
    if unknown:
        problems.append(f"the AI placed parts that were not built: {', '.join(unknown)} (ignored)")
    expected = Expectations(size=draft.size, size_evidence=draft.size_evidence) if draft.size and all(v > 0 for v in draft.size) else Expectations()
    plan = AssemblyPlan(name=name, source_hash=source_hash, unit=unit, bom=rows, models=models, occurrences=occurrences, expected=expected)
    return plan, problems + [f"AI note: {n}" for n in draft.notes]


def approve(plan: AssemblyPlan) -> AssemblyPlan:
    """The designer approved the whole plan: confirm what they reviewed (unit, parts list, models, every placement)."""
    data = plan.model_dump()
    data.update(unit_confirmed=True, bom_complete=True)
    for key in ("bom", "models"):
        for row in data[key]:
            row["confirmed"] = True
    for occ in data["occurrences"]:
        if occ.get("placement"):
            occ["placement"]["confirmed"] = True
    return AssemblyPlan.model_validate(data)


def built_part(job_root: Path, job_id: str) -> dict | None:
    """The saved SLDPRT and measured bounding box of a part build job that finished (awaiting review, accepted or exported)."""
    state = job_root / job_id / "state.json"
    if not state.is_file():
        return None
    record = json.loads(state.read_text(encoding="utf-8"))
    graph = record.get("graph") or {}
    sldprt = ((graph.get("finish") or {}).get("files") or {}).get("SLDPRT")
    bbox = ((graph.get("report") or {}).get("final") or {}).get("bbox_mm")
    if record.get("phase") not in ("awaiting_review", "accepted", "exported") or not sldprt or not Path(sldprt).is_file() or not bbox:
        return None
    return {"path": Path(sldprt), "bbox_mm": bbox}
