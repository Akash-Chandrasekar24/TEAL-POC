"""Checked flat-assembly execution through upstream MCP plus owned verification.

No inferred placements or automatic repair. Failed/incomplete readbacks stop the
run. Export only follows geometric and solver checks; acceptance is a separate
human review, and a passing report is not a manufacturing certification.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from .model import AssemblyPlan, Surface, cross, dot, validate_plan

CONTACT_M3 = 1e-9           # up to 1 mm3 of "interference" between two parts is an exact fit touching (rounding), not overlap


def _join_stl(parts: list[Path], out: Path) -> None:
    """Several STL files -> one binary STL (no files: nothing is written, the caller's read reports it)."""
    import struct
    from app.preview import read_stl
    if not parts:
        return
    tris = [t for p in parts for t in read_stl(p).reshape(-1, 3, 3)]
    with open(out, "wb") as f:
        f.write(b"joined component meshes".ljust(80, b" ") + struct.pack("<I", len(tris)))
        for a, b, c in tris:
            n = cross([b[i] - a[i] for i in range(3)], [c[i] - a[i] for i in range(3)])
            f.write(struct.pack("<12fH", *n, *a, *b, *c, 0))


class AssemblyError(RuntimeError):
    pass


def _finite(values, count):
    if not isinstance(values, (list, tuple)) or len(values) != count:
        raise AssemblyError(f"Expected {count} numeric values in a runtime readback")
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in values):
        raise AssemblyError("Non-finite or non-numeric runtime geometry")
    return values


def select_face(surface: Surface, faces: list[dict], scale: float, tolerance: float) -> int:
    """Find exactly one analytic surface. Never fall back to the nearest face.

Plane points identify a plane by signed offset; cylinder points identify an
axis by perpendicular distance. An optional area separates repeated surfaces.
All runtime positions/areas are SI, selectors are in the plan's units.
"""
    point = [x * scale for x in surface.point]
    tol = tolerance * scale
    matches = []
    for face in faces:
        planar = surface.kind == "plane"
        if (planar and face.get("planar") is not True) or (not planar and face.get("kind") != "cylinder"):
            continue
        direction = _finite(face.get("normal" if planar else "axis"), 3)
        origin = _finite(face.get("point" if planar else "origin"), 3)
        if abs(dot(direction, direction) - 1) > 1e-5:
            raise AssemblyError("Runtime surface direction is not a unit vector")
        if math.sqrt(dot(cross(direction, surface.direction), cross(direction, surface.direction))) > 1e-5:
            continue
        delta = [point[i] - origin[i] for i in range(3)]
        distance = abs(dot(delta, direction)) if planar else math.sqrt(dot(cross(delta, direction), cross(delta, direction)))
        if distance > tol:
            continue
        if not planar:
            radius = _finite([face.get("radius")], 1)[0]
            if abs(radius - surface.radius * scale) > tol:
                continue
        if surface.area is not None:
            area = _finite([face.get("area")], 1)[0]
            # Upstream face areas are rounded to 1e-6 square metres.
            if abs(area - surface.area * scale**2) > max(1e-6, abs(area) * 1e-6):
                continue
        index = face.get("i")
        if type(index) is not int or index < 0:
            raise AssemblyError("Runtime face has no valid index")
        matches.append(index)
    if len(matches) != 1:
        raise AssemblyError(f"{surface.occurrence}: {surface.kind} selector matched {len(matches)} faces; refine the model/selector")
    return matches[0]


def check_components(plan: AssemblyPlan, data: dict, names: dict[str, str], models: dict[str, Path]) -> list[str]:
    errors = []
    entries = data.get("components")
    if not isinstance(entries, list) or data.get("component_count") != len(entries):
        return ["Incomplete component enumeration"]
    actual = {c.get("name"): c for c in entries}
    if len(actual) != len(entries) or set(actual) != set(names.values()):
        errors.append("Runtime component identities/count differ from inserted occurrences")
    occurrences = {o.id: o for o in plan.occurrences}
    configurations = {m.bom_id: m.configuration for m in plan.models}
    for oid, name in names.items():
        entry = actual.get(name)
        if entry is None:
            continue
        occurrence = occurrences[oid]
        if Path(entry.get("path", "")).resolve() != models[occurrence.bom_id].resolve():
            errors.append(f"{oid}: runtime model path differs from pinned component")
        if entry.get("config") != configurations[occurrence.bom_id]:
            errors.append(f"{oid}: runtime configuration differs from the reviewed model")
        if entry.get("fixed") is not occurrence.fixed or entry.get("suppressed", False) or entry.get("children", 0):
            errors.append(f"{oid}: fixed/suppressed/nesting state differs")
        try:
            actual_transform = _finite(entry.get("transform"), 13)
            expected = occurrence.placement.transform(plan.scale)
            if any(abs(a-b) > plan.expected.rotation_tolerance for a, b in zip(actual_transform[:9], expected[:9])):
                errors.append(f"{oid}: component rotation moved outside tolerance")
            if any(abs(a-b) > plan.expected.length_tolerance * plan.scale for a, b in zip(actual_transform[9:12], expected[9:12])):
                errors.append(f"{oid}: component position moved outside tolerance")
            if abs(actual_transform[12] - 1) > 1e-9:
                errors.append(f"{oid}: component has an unexpected scale")
        except AssemblyError as ex:
            errors.append(f"{oid}: {ex}")
    return errors


def check_mates(plan: AssemblyPlan, data: dict, names: dict[str, str], mate_names: dict[str, str]) -> list[str]:
    entries = data.get("mates")
    if not isinstance(entries, list) or data.get("mate_count") != len(entries):
        return ["Incomplete mate enumeration"]
    actual = {m.get("name"): m for m in entries}
    errors = []
    if len(actual) != len(entries) or set(actual) != set(mate_names.values()):
        errors.append("Runtime mate identities/count differ from approved mates")
    for mate in plan.mates:
        entry = actual.get(mate_names[mate.id])
        if entry is None:
            continue
        if entry.get("type") != mate.type or entry.get("alignment") != mate.alignment:
            errors.append(f"{mate.id}: mate type/alignment differs")
        entities = entry.get("entities", [])
        if len(entities) != 2 or {e.get("component") for e in entities} != {names[mate.a.occurrence], names[mate.b.occurrence]}:
            errors.append(f"{mate.id}: mate connects different components")
        if mate.value is not None:
            expected = mate.value * plan.scale if mate.type == "distance" else math.radians(mate.value)
            value = entry.get("value")
            tolerance = plan.expected.length_tolerance * plan.scale if mate.type == "distance" else plan.expected.rotation_tolerance
            if type(value) not in (int, float) or not math.isfinite(value) or abs(value - expected) > tolerance:
                errors.append(f"{mate.id}: mate driving dimension differs")
    return errors


def check_inspection(plan: AssemblyPlan, data: dict, names: dict[str, str]) -> list[str]:
    errors = []
    if data.get("rebuild_ok") is not True or data.get("feature_errors") != []:
        errors.append("Assembly rebuild failed or feature/mate errors were reported")
    constraints = data.get("constraints")
    if not isinstance(constraints, list) or {c.get("name") for c in constraints} != set(names.values()) or len(constraints) != len(names):
        errors.append("Incomplete component constraint status")
    elif plan.expected.fully_constrained:
        for c in constraints:
            if not c.get("fixed") and c.get("constrained_status") != 3:  # swFullyConstrained
                errors.append(f"{c['name']}: component is not fully constrained")
    else:
        for c in constraints:
            if not c.get("fixed") and c.get("constrained_status") not in (2, 3):
                errors.append(f"{c['name']}: invalid/unknown mate solver state")
    # an exact fit touches: a Ø33.8 pin in its Ø33.8 bore reports 0.94 mm3 (0.2 um of rounding over the pin's surface). Up to 1 mm3 is
    # contact; anything more, and every "possible" intersection, is a part inside another.
    real = [i for i in (data.get("interferences") or []) if i.get("possible") or not isinstance(i.get("volume_m3"), (int, float))
            or i["volume_m3"] > CONTACT_M3]
    if data.get("interference_complete") is not True or not isinstance(data.get("interferences"), list) or real:
        errors.append("Interference check is incomplete or reports intersections (including possible intersections)")
    try:
        bounds = _finite(data.get("bounds_m"), 6)
        size = [(bounds[i+3] - bounds[i]) / plan.scale for i in range(3)]
        if any(s <= 0 or abs(s-e) > plan.expected.length_tolerance for s, e in zip(size, plan.expected.size)):
            errors.append(f"Overall size differs: measured {size}, expected {plan.expected.size} {plan.unit}")
    except AssemblyError as ex:
        errors.append(str(ex))
    return errors


async def run_assembly(sw, plan: AssemblyPlan, assets: Path, output: Path, approved_hash: str, *, review_only=False, check_every: int | None = None) -> dict:
    """Call only after an explicit approved snapshot AND runtime opt-in.

    The caller owns runtime startup. This function requires an empty workspace,
    creates one assembly and never closes user documents. Outputs bundle the
    exact component files to keep native references local to the new run.

    `check_every`: re-read ALL components after every N insert/mate steps (the read has no per-component filter, so each one costs O(parts)).
    None = every step for up to 20 steps, else every 10. The final and post-save checks always run on everything, so a drift cannot reach export;
    a larger N only localizes a failure less precisely (it names the batch, not the step).
    """
    report = {"plan_hash": plan.digest(), "status": "failed", "checks": [], "errors": [], "files": {}, "names": {}}
    if approved_hash != plan.digest():
        raise AssemblyError("The approved assembly snapshot changed; review and approve it again")
    blockers = validate_plan(plan, assets)
    if blockers:
        raise AssemblyError("; ".join(blockers))
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise AssemblyError("Assembly output directory must be new and empty")
    names, mate_names, paths = report["names"], {}, {}
    every = max(1, check_every or (1 if len(plan.occurrences) + len(plan.mates) <= 20 else 10))
    pending, faces_cache = 0, {}

    async def call(name, *args, **kwargs):
        result = await getattr(sw, name)(*args, **kwargs)
        if not result.ok:
            raise AssemblyError(f"{name}: {result.text[:500]}")
        return result

    def check(label, errors, data):
        report["checks"].append({"name": label, "ok": not errors, "errors": errors, "readback": data})
        if errors:
            raise AssemblyError("; ".join(errors))

    async def workspace(expected_title=None):
        data = (await call("inspect_assembly", "workspace")).json()
        if expected_title is None:
            if data.get("documents") != []:
                raise AssemblyError("Close open SolidWorks documents before an assembly run; none are closed automatically")
        elif data.get("title") != expected_title:
            raise AssemblyError("The active document changed during the assembly run")
        return data

    async def components(label):
        nonlocal pending
        pending = 0
        await workspace(title)
        data = (await call("analyze_assembly", "components")).analysis_json()
        check(label, check_components(plan, data, names, paths), data)

    async def step_check(label):
        """Full component check after this step, or once enough steps are pending (see check_every)."""
        nonlocal pending
        pending += 1
        if pending >= every:
            await components(label if every == 1 else f"{label} (and the {pending - 1} step(s) since the last full check)")

    async def faces_of(name):
        if name not in faces_cache:     # a component's face list is in its own coordinates: mates do not change it
            data = (await call("analyze_assembly", "faces", component=name)).analysis_json()
            if data.get("component") != name or not isinstance(data.get("faces"), list) or data.get("face_count") != len(data["faces"]):
                raise AssemblyError("Incomplete component face enumeration")
            faces_cache[name] = data["faces"]
        return faces_cache[name]

    try:
        for model in plan.models:
            data = model.path(assets).read_bytes()
            if hashlib.sha256(data).hexdigest() != model.sha256:
                raise AssemblyError("Component changed while pinning build inputs")
            target = output / "components" / f"{model.sha256}.SLDPRT"
            target.parent.mkdir(exist_ok=True)
            if not target.exists():
                target.write_bytes(data)
            paths[model.bom_id] = target.resolve()
        (output / "plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
        await workspace()
        await call("open_new_assembly")
        opened = (await call("inspect_assembly", "workspace")).json()
        title = opened.get("title")
        if not title or len(opened.get("documents", [])) != 1:
            raise AssemblyError("New assembly is not the sole active document")
        models = {m.bom_id: m for m in plan.models}
        for occurrence in plan.occurrences:
            if every == 1:
                await workspace(title)
            result = await call("insert_component", file_path=str(paths[occurrence.bom_id]),
                                config=models[occurrence.bom_id].configuration, fixed=occurrence.fixed,
                                transform_json=json.dumps(occurrence.placement.transform(plan.scale)))
            if not result.features or not result.features[0] or result.features[0] in names.values():
                raise AssemblyError(f"{occurrence.id}: missing or duplicate inserted component name")
            names[occurrence.id] = result.features[0]
            await step_check(f"Placement {occurrence.id}")
        for mate in plan.mates:
            if every == 1:
                await workspace(title)
            indices = []
            for surface in (mate.a, mate.b):
                try:
                    faces = await faces_of(names[surface.occurrence])
                except AssemblyError as ex:
                    raise AssemblyError(f"{mate.id}: {ex}")
                indices.append(select_face(surface, faces, plan.scale, plan.expected.selector_tolerance))
            value = mate.value or 0.0
            if mate.type == "distance":
                value *= plan.scale
            result = await call("add_mate", mate_type=mate.type, a_component=names[mate.a.occurrence], a_index=indices[0],
                                b_component=names[mate.b.occurrence], b_index=indices[1], a_kind="face", b_kind="face",
                                alignment=mate.alignment, flip=mate.flip, value=value)
            if not result.features or not result.features[0] or result.features[0] in mate_names.values():
                raise AssemblyError(f"{mate.id}: missing or duplicate mate name")
            mate_names[mate.id] = result.features[0]
            await step_check(f"After mate {mate.id}")
        await components("Final component count, configuration and placements")
        data = (await call("analyze_assembly", "mates")).analysis_json()
        check("Mate identities and dimensions", check_mates(plan, data, names, mate_names), data)
        await workspace(title)
        data = (await call("inspect_assembly", "verification")).json()
        if data.get("title") != title:
            raise AssemblyError("Active assembly changed during verification")
        check("Rebuild, constraints, exact bounds and interference", check_inspection(plan, data, names), data)
        # Rebuilding/solving during inspection may move parts. Verify again.
        await components("Placements after final rebuild")
        native, step = output / f"{plan.name}.SLDASM", output / f"{plan.name}.step"
        await call("save_document", native)
        # Save changes the document title to the file stem/title.
        saved = (await call("inspect_assembly", "workspace")).json()
        active = [d for d in saved.get("documents", []) if d.get("title") == saved.get("title")]
        if len(active) != 1 or Path(active[0].get("path", "")).resolve() != native.resolve():
            raise AssemblyError("Save did not leave the expected assembly active")
        title = saved["title"]
        await components("Placements after native save")
        preview_files = {}
        if review_only:
            from app.preview import render_stl_png
            mesh, image = output / f"{plan.name}.stl", output / f"{plan.name}.png"
            await call("export_document", "STL", mesh)
            if not mesh.is_file():
                # SolidWorks' "save all components in one file" option is off: it wrote "<name> - <component>.STL" per component (in one
                # frame: measured, together they span the assembly's size). One mesh is made of them.
                _join_stl(sorted(output.glob(f"{plan.name} - *.STL")), mesh)
            render_stl_png(mesh, image, plan.name)
            preview_files = {"mesh": str(mesh.resolve()), "preview": str(image.resolve()), "configuration": saved["configuration"]}
        else:
            await call("export_document", "STEP", step)
        for path in (native,) if review_only else (native, step):
            if not path.is_file() or path.stat().st_size == 0:
                raise AssemblyError(f"Expected output was not written: {path.name}")
        for model in plan.models:
            if hashlib.sha256(paths[model.bom_id].read_bytes()).hexdigest() != model.sha256:
                raise AssemblyError(f"{model.bom_id}: component file changed during assembly execution")
        report["files"] = {"assembly": str(native.resolve()), "components": str((output / "components").resolve()), **preview_files}
        if not review_only:
            report["files"]["step"] = str(step.resolve())
        report["status"] = "awaiting_review"
    except Exception as ex:
        report["errors"].append(f"{type(ex).__name__}: {ex}")
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
