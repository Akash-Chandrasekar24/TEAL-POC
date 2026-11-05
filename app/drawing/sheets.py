"""Reviewed 3D model -> checked standard model views -> native 2D sheet.

This is a limited model-linked sheet workflow, not raster tracing or automatic
recreation of source sections, BOMs, tolerances and annotation placement.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import Field

from .model import Strict, fingerprint


class SheetView(Strict):
    id: str = Field(pattern=r"^V[1-9][0-9]*$")
    orientation: Literal["front", "top", "right", "left", "back", "bottom", "isometric"]
    x_mm: float = Field(gt=10, lt=410, allow_inf_nan=False)
    y_mm: float = Field(gt=10, lt=287, allow_inf_nan=False)
    scale: float = Field(gt=0, le=10, allow_inf_nan=False)


class SheetDimension(Strict):
    view: str
    native_name: str = Field(min_length=1)
    expected_si: float = Field(allow_inf_nan=False)
    tolerance_si: float = Field(gt=0, allow_inf_nan=False)
    source_evidence: str = Field(min_length=1)


class SheetPlan(Strict):
    version: Literal["1"] = "1"
    name: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    model_path: str = Field(min_length=1)
    model_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    configuration: str = Field(min_length=1)
    model_reviewed: bool = False
    accepted_assembly_job: str | None = None
    source_evidence: str = Field(min_length=1)
    projection: Literal["first", "third"]
    views: list[SheetView] = Field(min_length=1, max_length=7)
    dimensions: list[SheetDimension] = Field(min_length=1)
    unsupported_requirements: list[str] = Field(default_factory=list)

    def digest(self):
        return fingerprint(self.model_dump())


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for block in iter(lambda: file.read(1024*1024), b""):
            digest.update(block)
    return digest.hexdigest()


def assembly_references(plan: SheetPlan) -> list[tuple[str, str]]:
    """Require the existing accepted assembly evidence and pinned bundled parts."""
    from app.assembly.model import AssemblyPlan, digest
    if not plan.accepted_assembly_job:
        raise ValueError("An SLDASM sheet requires an accepted assembly job with verified bundled dependencies")
    job = Path(plan.accepted_assembly_job)
    state = json.loads((job / "state.json").read_text(encoding="utf-8"))
    approved = AssemblyPlan.model_validate_json((job / "approved.json").read_text(encoding="utf-8"))
    current = AssemblyPlan.model_validate_json((job / "plan.json").read_text(encoding="utf-8"))
    report = state.get("report") or {}
    if (state.get("status") != "accepted" or approved.digest() != current.digest()
            or approved.digest() != state.get("approved_hash") or digest(report) != state.get("report_hash")
            or report.get("plan_hash") != approved.digest()):
        raise ValueError("Assembly acceptance/plan/report evidence is missing or changed")
    native = Path(report["files"]["assembly"])
    if native.resolve() != Path(plan.model_path).resolve() or sha256(native) != state["output_hashes"]["assembly"]:
        raise ValueError("Accepted assembly output differs from the selected model")
    folder = Path(report["files"]["components"])
    by_bom = {}
    for model in approved.models:
        path = folder / f"{model.sha256}.SLDPRT"
        if sha256(path) != model.sha256:
            raise ValueError("Accepted assembly dependency changed")
        by_bom[model.bom_id] = (str(path.resolve()).casefold(), model.configuration)
    return [by_bom[o.bom_id] for o in approved.occurrences]


def validate_sheet(plan: SheetPlan) -> list[str]:
    issues = []
    model = Path(plan.model_path)
    if model.suffix.lower() not in {".sldprt", ".sldasm"}:
        issues.append("Source must be a native SLDPRT or an accepted SLDASM")
    if model.suffix.lower() == ".sldasm":
        try:
            assembly_references(plan)
        except (ValueError, KeyError, OSError) as ex:
            issues.append(f"Assembly source: {ex}")
    if not model.is_file() or (model.is_file() and sha256(model) != plan.model_sha256):
        issues.append("Source model is missing or differs from its reviewed hash")
    if not plan.model_reviewed:
        issues.append("Review the 3D model against source dimensions before generating its sheet")
    issues.extend(plan.unsupported_requirements)
    ids = [v.id for v in plan.views]
    orientations = [v.orientation for v in plan.views]
    if len(ids) != len(set(ids)) or len(orientations) != len(set(orientations)):
        issues.append("View IDs and orientations must be unique")
    if any(d.view not in ids for d in plan.dimensions):
        issues.append("A dimension references an unknown view")
    if len({(d.view, d.native_name) for d in plan.dimensions}) != len(plan.dimensions):
        issues.append("Expected native dimension bindings must be unique")
    by_type = {v.orientation: v for v in plan.views}
    front = by_type.get("front")
    if front:
        for kind, axis, positive in (("top", "y_mm", plan.projection == "third"), ("right", "x_mm", plan.projection == "third")):
            other = by_type.get(kind)
            if other and ((getattr(other, axis) > getattr(front, axis)) != positive):
                issues.append(f"{kind} view placement conflicts with {plan.projection}-angle projection")
            if other:
                aligned_axis = "x_mm" if axis == "y_mm" else "y_mm"
                if abs(getattr(other, aligned_axis) - getattr(front, aligned_axis)) > 1e-5:
                    issues.append(f"{kind} view must align with the front view")
    return list(dict.fromkeys(issues))


def check_sheet(plan: SheetPlan, data: dict, names: dict[str, str], expected_path: Path | None = None):
    errors = []
    if data.get("version") != 1 or data.get("rebuild_ok") is not True or data.get("sheet_count") != 1:
        errors.append("Drawing inspection is incomplete or rebuild/sheet count is wrong")
    size = data.get("sheet_size_m")
    if not isinstance(size, list) or len(size) != 2 or any(type(v) not in (int, float) or not math.isfinite(v) or abs(v-e) > 1e-7 for v, e in zip(size, [.420, .297])):
        errors.append("Drawing must use the verified A3 landscape sheet size")
    if data.get("first_angle") is not (plan.projection == "first"):
        errors.append("Native sheet projection conflicts with the approved first/third-angle layout; configure the drawing template")
    if expected_path is not None and Path(data.get("path") or "").resolve() != expected_path.resolve():
        errors.append("The active saved drawing is not the expected output")
    views = data.get("views")
    if not isinstance(views, list) or data.get("view_count") != len(views) or any(not isinstance(v, dict) for v in views):
        return errors + ["Incomplete native drawing view enumeration"]
    actual = {v.get("name"): v for v in views}
    if len(actual) != len(views) or set(actual) != set(names.values()):
        errors.append("Native views differ from the reviewed layout")
    boxes = []
    expected_refs = Counter(assembly_references(plan)) if Path(plan.model_path).suffix.lower() == ".sldasm" else None
    for requested in plan.views:
        view = actual.get(names.get(requested.id))
        if view is None:
            continue
        try:
            if Path(view.get("model_path") or "").resolve() != Path(plan.model_path).resolve():
                errors.append(f"{requested.id}: wrong referenced model")
            if view.get("configuration") != plan.configuration:
                errors.append(f"{requested.id}: wrong model configuration")
            if Path(plan.model_path).suffix.lower() == ".sldasm":
                refs = view.get("component_references")
                actual_refs = Counter((str(Path(r["path"]).resolve()).casefold(), r["configuration"]) for r in refs) if isinstance(refs, list) else Counter()
                if actual_refs != expected_refs:
                    errors.append(f"{requested.id}: assembly reference/configuration/quantity differs")
            if str(view.get("orientation", "")).lstrip("*").lower() != requested.orientation:
                errors.append(f"{requested.id}: wrong native orientation")
            scale = view.get("scale")
            position, outline = view.get("position_m"), view.get("outline_m")
            values = [scale] + position + outline
            if len(position) != 2 or len(outline) != 4 or any(type(n) not in (int, float) or not math.isfinite(n) for n in values):
                raise ValueError("Incomplete view geometry")
            if abs(scale - requested.scale) > 1e-6 or any(abs(a-b) > 1e-5 for a, b in zip(position, [requested.x_mm/1000, requested.y_mm/1000])):
                errors.append(f"{requested.id}: native scale/position differs")
            x0, y0, x1, y1 = outline
            if not (0.01 <= x0 < x1 <= .410 and .01 <= y0 < y1 <= .287):
                errors.append(f"{requested.id}: view is empty or outside the sheet margins")
            boxes.append((requested.id, outline))
            dimensions = view.get("dimensions")
            if not isinstance(dimensions, list):
                raise ValueError("Missing native dimension readback")
            for expected in (d for d in plan.dimensions if d.view == requested.id):
                hits = [d for d in dimensions if d.get("name") == expected.native_name]
                if len(hits) != 1 or type(hits[0].get("value_si")) not in (int, float) or not math.isfinite(hits[0]["value_si"]) or abs(hits[0]["value_si"]-expected.expected_si) > expected.tolerance_si:
                    errors.append(f"{requested.id}: dimension {expected.native_name} is missing, ambiguous or wrong")
        except (ValueError, TypeError, KeyError, OSError):
            errors.append(f"{requested.id}: incomplete native view geometry/dimension readback")
    for i, (name, a) in enumerate(boxes):
        for other, b in boxes[i+1:]:
            if min(a[2], b[2])-max(a[0], b[0]) > 1e-5 and min(a[3], b[3])-max(a[1], b[1]) > 1e-5:
                errors.append(f"Views {name} and {other} overlap")
    return errors


async def run_sheet(sw, plan: SheetPlan, output: Path, approved_hash: str):
    if plan.digest() != approved_hash:
        raise ValueError("The drawing plan changed after approval")
    blockers = validate_sheet(plan)
    if blockers:
        raise ValueError("; ".join(blockers))
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError("Drawing output directory must be new and empty")
    output.mkdir(parents=True, exist_ok=True)
    report = {"plan_hash": approved_hash, "status": "failed", "files": {}, "checks": [], "errors": [], "accuracy_verified": False}

    async def call(name, *args, **kwargs):
        response = await getattr(sw, name)(*args, **kwargs)
        if not response.ok:
            raise ValueError(f"{name}: {response.text[:300]}")
        return response

    try:
        workspace = (await call("inspect_assembly", "workspace")).json()
        if workspace.get("documents") != []:
            raise ValueError("Close existing SolidWorks documents before creating a sheet; none are closed automatically")
        await call("create_drawing", model_path=str(Path(plan.model_path).resolve()))
        names = {}
        for view in plan.views:
            before = (await call("inspect_drawing")).json()
            await call("add_drawing_view", view.orientation, pos_x=view.x_mm/1000, pos_y=view.y_mm/1000,
                       scale=view.scale, model_path=str(Path(plan.model_path).resolve()), display_mode="hlr" if view.orientation == "isometric" else "hlv")
            after = (await call("inspect_drawing")).json()
            previous = {v["name"] for v in before["views"]}
            added = {v["name"] for v in after["views"]} - previous
            if len(added) != 1 or len(after["views"]) != len(before["views"])+1:
                raise ValueError("View insertion did not produce exactly one new view")
            names[view.id] = added.pop()
        await call("auto_dimension_drawing", all_views=True, include_unmarked=True, eliminate_duplicates=True)
        await call("auto_center_marks", include_slots=True, extended_lines=True)
        inspection = (await call("inspect_drawing")).json()
        errors = check_sheet(plan, inspection, names)
        report["checks"].append({"name": "Native sheet/view/model/dimension checks", "ok": not errors, "errors": errors, "readback": inspection})
        if errors:
            raise ValueError("; ".join(errors))
        native, pdf = output / f"{plan.name}.SLDDRW", output / f"{plan.name}.pdf"
        await call("save_document", native)
        errors = check_sheet(plan, (await call("inspect_drawing")).json(), names, native)
        if errors:
            raise ValueError("; ".join(errors))
        await call("export_document", "PDF", pdf)
        errors = check_sheet(plan, (await call("inspect_drawing")).json(), names, native)
        if errors:
            raise ValueError("; ".join(errors))
        if not native.is_file() or native.stat().st_size == 0 or not pdf.is_file() or not pdf.read_bytes().startswith(b"%PDF-"):
            raise ValueError("Native drawing/PDF export is missing or invalid")
        if sha256(plan.model_path) != plan.model_sha256:
            raise ValueError("Source 3D model changed during drawing generation")
        if Path(plan.model_path).suffix.lower() == ".sldasm":
            assembly_references(plan)
        report["files"] = {"drawing": str(native.resolve()), "pdf": str(pdf.resolve())}
        report["status"] = "awaiting_review"
    except Exception as ex:
        report["errors"].append(f"{type(ex).__name__}: {ex}")
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
