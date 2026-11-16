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


class SitsOn(Strict):
    part: str                          # the part it sits on / in
    copy: int = Field(1, ge=1)
    my_face: int = Field(ge=0)         # index into THIS part's round_faces_mm
    its_face: int = Field(ge=0)        # index into THAT part's round_faces_mm


class DraftPlacement(Strict):
    part: str
    copy: int = Field(1, ge=1)
    origin: list[float] = Field(min_length=3, max_length=3)
    x_to: str
    y_to: str
    on: SitsOn | None = None           # the app puts the two round faces on one axis, centred: the origin is then computed, not guessed
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


ROUND_FACES = """
Each part also lists its round faces IN ITS OWN FRAME, read from the built SolidWorks part: "round_faces_mm": [{"i": index, "diameter",
"axis": the axis direction, "from", "to": the two end points of that cylinder on its axis}]. A bore, a crankpin, a journal, a pin or a ring
groove is one of them. For EVERY part that sits on or in another one, add "on": {"part": "<that part id>", "copy": n, "my_face": i,
"its_face": j}: i = the round face of THIS part, j = the round face of THAT part it sits on (a rod's big-end bore on ITS crankpin: pick the
crankpin whose axis is at that cylinder's station and throw; a cap's bore on its rod's big-end bore; the piston pin's outside in the rod's
small-end bore; the piston's pin bore on its pin; a ring's inside diameter on its groove's bottom diameter). The app then puts the two faces
on one axis, centred on each other, EXACTLY: your origin only matters for a part with no "on" (the crankshaft). Your x_to / y_to still set
the orientation: choose them so the two axes are parallel (a rod pointing up to its piston, a cap below its rod). Place parts in the order
they hang together: crankshaft, rods, caps, pins, pistons, rings."""

FEEDBACK = """
Your previous placements were built in SolidWorks and these parts OVERLAPPED (component ids, overlap volume in mm3): {overlaps}.
Your previous placements: {previous}. Parts that only touch are fine; anything above about 1 mm3 means a part sits inside another.
Correct the placements so they do not overlap: work from the round faces, keep what was right."""


def round_faces(faces: list[dict], edges: list[dict], min_radius_m: float = 0.004) -> list[dict]:
    """The part's cylinders (own frame, mm): diameter, axis direction and the axis segment it covers, from SolidWorks' face and edge lists
    (an edge whose points all lie on a cylinder marks where it starts / ends). Small roundings (< min radius) are left out."""
    import math
    out: list[dict] = []
    for f in faces:
        if f.get("kind") != "cylinder" or not f.get("radius") or f["radius"] < min_radius_m:
            continue
        o, a, r = f["origin"], f["axis"], f["radius"]
        n = math.sqrt(sum(v * v for v in a)) or 1.0
        a = [v / n for v in a]
        t_of = lambda p: sum((p[i] - o[i]) * a[i] for i in range(3))
        def off_axis(p):
            t = t_of(p)
            return math.sqrt(sum((p[i] - o[i] - t * a[i]) ** 2 for i in range(3)))
        ts = [t_of(p) for e in edges for p in (e["start"], e["end"], e["mid"])
              if all(abs(off_axis(q) - r) < 2e-5 for q in (e["start"], e["end"], e["mid"]))]
        if not ts:
            continue
        lo, hi = min(ts), max(ts)
        seg = [[round((o[i] + t * a[i]) * 1000, 2) for i in range(3)] for t in (lo, hi)]
        key = (round(r * 2000, 2), tuple(round(v, 3) for v in a), tuple(seg[0]), tuple(seg[1]))
        if any((c["diameter"], tuple(c["axis"]), tuple(c["from"]), tuple(c["to"])) == key for c in out):
            continue
        out.append({"diameter": key[0], "axis": list(key[1]), "from": seg[0], "to": seg[1]})
    return sorted(out, key=lambda c: -c["diameter"])[:40]


def draft_placements(llm, picture: bytes, parts: list[dict], feedback: dict | None = None) -> DraftAssembly:
    context = [{"id": p["id"], "name": p["name"], "quantity": p["quantity"], "bbox_mm_own_frame": p["bbox_mm"],
                **({"round_faces_mm": [{"i": i, **c} for i, c in enumerate(p["round_faces"])]} if p.get("round_faces") else {})} for p in parts]
    prompt = PROMPT + (ROUND_FACES if any(p.get("round_faces") for p in parts) else "")
    if feedback and feedback.get("overlaps"):
        prompt += FEEDBACK.format(overlaps=json.dumps(feedback["overlaps"]), previous=json.dumps(feedback.get("previous", []))[:12000])
    reply = llm.generate_json(prompt, [FilePart(picture, "image/png"), json.dumps({"parts": context}, ensure_ascii=False)])
    return DraftAssembly.model_validate(reply.data)


def _world(origin, rot, p):
    """A point of the part's own frame in the assembly: origin + x*row0 + y*row1 + z*row2 (rot = rotation(), mm)."""
    return [origin[k] + p[0] * rot[k] + p[1] * rot[3 + k] + p[2] * rot[6 + k] for k in range(3)]


def _turn(rot, v):
    return [v[0] * rot[k] + v[1] * rot[3 + k] + v[2] * rot[6 + k] for k in range(3)]


def snap(draft: DraftAssembly, built: list[dict]) -> tuple[dict, list[str]]:
    """{(part, copy): origin}: every placement with "on" is moved so its round face sits on the other part's, on one axis and centred
    (what a concentric mate + centring does), in dependency order. The AI chose WHICH faces and the orientation; the numbers are the app's.
    An "on" that cannot be honoured (unknown face, axes not parallel, a loop) keeps the AI's origin and is reported."""
    faces = {p["id"]: p.get("round_faces") or [] for p in built}
    todo = {(d.part, d.copy): d for d in draft.placements}
    origins, rots, problems = {}, {}, []
    for key, d in todo.items():
        try:
            rots[key] = rotation(d.x_to, d.y_to)
        except ValueError:
            pass
    for key, d in todo.items():                                     # the anchors: no "on"
        if d.on is None:
            origins[key] = list(d.origin)
    for _ in range(len(todo)):
        moved = False
        for key, d in todo.items():
            if key in origins or d.on is None or key not in rots:
                continue
            target = (d.on.part, d.on.copy)
            if target not in origins or target not in rots:
                continue
            mine, theirs = faces.get(d.part, []), faces.get(d.on.part, [])
            if d.on.my_face >= len(mine) or d.on.its_face >= len(theirs):
                problems.append(f"{d.part} copy {d.copy}: round face {d.on.my_face} / {d.on.its_face} does not exist: the AI's origin is kept")
                origins[key] = list(d.origin); moved = True
                continue
            a, b = mine[d.on.my_face], theirs[d.on.its_face]
            axis_mine, axis_theirs = _turn(rots[key], a["axis"]), _turn(rots[target], b["axis"])
            if abs(sum(x * y for x, y in zip(axis_mine, axis_theirs))) < 0.999:
                problems.append(f"{d.part} copy {d.copy}: its face {d.on.my_face} is not parallel to {d.on.part} copy {d.on.copy}'s face "
                                f"{d.on.its_face} in the chosen orientation: the AI's origin is kept")
                origins[key] = list(d.origin); moved = True
                continue
            mid = lambda c: [(c["from"][k] + c["to"][k]) / 2 for k in range(3)]
            goal = _world(origins[target], rots[target], mid(b))
            here = _turn(rots[key], mid(a))
            origins[key] = [round(goal[k] - here[k], 4) for k in range(3)]
            moved = True
        if not moved:
            break
    for key, d in todo.items():
        if key not in origins:
            problems.append(f"{d.part} copy {d.copy}: sits on {d.on.part} copy {d.on.copy}, which is not placed: the AI's origin is kept")
            origins[key] = list(d.origin)
    return origins, problems


def plan_from_parts(name: str, source_hash: str, unit: str, built: list[dict], draft: DraftAssembly, bom_page: int = 1) -> tuple[AssemblyPlan, list[str]]:
    """built: [{id, name, item, quantity, sha256, source_name, bbox_mm, job}]. Returns the plan (nothing confirmed) and the problems found
    in the AI's draft (missing copies, bad axes): those are listed for the designer, never silently fixed."""
    origins, problems = snap(draft, built)
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
            at = origins.get((d.part, d.copy), d.origin)
            how = f" | on {d.on.part} {d.on.copy}: round face {d.on.my_face} centred on its face {d.on.its_face}" if d.on else ""
            occurrences.append(Occurrence(id=f"{rid}-{n}"[:60], bom_id=rid, fixed=True,
                                          placement=Placement(translation=at, rotation=rot, evidence=(d.evidence + how)[:2000])))
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
