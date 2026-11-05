"""Behavioral assembly tests with simulated CAD. No live geometric accuracy claim."""
import asyncio
import copy
import json
import tempfile
from pathlib import Path

import fakes  # noqa: F401
from fakes import FakeModel
from assembly_fixtures import FakeAssembly, ready_plan
from app.assembly.assets import import_model
from app.assembly.bom import read_bom
from app.assembly.jobs import AssemblyJob
from app.assembly.model import AssemblyPlan, Placement, Surface, validate_plan
from app.assembly.runner import AssemblyError, check_inspection, check_mates, run_assembly, select_face


def refuses(fn, exception=ValueError):
    try:
        fn()
    except exception as ex:
        return str(ex)
    raise AssertionError("Invalid operation was accepted")


def test_bom_repeated_tables_preserve_evidence_and_conflicts_without_multiplying_quantity():
    row = {"item": "1", "part_number": "P-10", "description": "Motor", "quantity": 2, "kind": "part", "text": "1 P-10 Motor 2"}
    llm = FakeModel(reads=[{"rows": [row], "unit": "mm"}, {"rows": [row], "unit": "mm"}])
    source = {"pages": [{"no": 1}, {"no": 2}]}
    plan = read_bom(llm, source, {1: b"test", 2: b"test"})
    assert len(plan.bom) == 1 and plan.bom[0].quantity == 2 and len(plan.bom[0].evidence) == 2
    assert not plan.bom[0].confirmed and not plan.occurrences and len(llm.calls) == 2
    llm = FakeModel(reads=[{"rows": [row], "unit": "mm"}, {"rows": [{**row, "quantity": 3}], "unit": "inch"}])
    plan = read_bom(llm, source, {1: b"test", 2: b"test"})
    assert plan.bom[0].issues and plan.unit is None and plan.issues


def test_missing_bom_page_and_nested_assembly_remain_blockers():
    plan = read_bom(FakeModel(reads=[{"rows": [], "unit": None}]), {"pages": [{"no": 1}, {"no": 2}]}, {1: b"test"})
    assert any("Page 2" in b for b in validate_plan(plan))
    with tempfile.TemporaryDirectory() as directory:
        plan = ready_plan(Path(directory))
        plan.bom[0].kind = "assembly"
        assert any("flat leaf" in b for b in validate_plan(plan))


def test_validator_blocks_counts_unknown_models_placements_disconnected_and_duplicates():
    with tempfile.TemporaryDirectory() as directory:
        assets = Path(directory)
        plan = ready_plan(assets)
        assert not validate_plan(plan, assets)
        plan.bom[0].quantity = 3
        plan.occurrences[1].placement = None
        plan.mates = []
        plan.models[0].confirmed = False
        errors = validate_plan(plan, assets)
        assert any("quantity" in e for e in errors) and any("placement" in e for e in errors)
        assert any("connection" in e for e in errors) and any("confirm model" in e for e in errors)
        plan.occurrences.append(plan.occurrences[0])
        assert any("Duplicate occurrence" in e for e in validate_plan(plan, assets))


def test_rigid_transforms_reject_reflection_shear_scale_and_nonfinite_coordinates():
    kwargs = {"translation": [1, 2, 3], "evidence": "drawing", "confirmed": True}
    assert Placement(**kwargs).transform(.001)[9:13] == [.001, .002, .003, 1]
    for rotation in ([2, 0, 0, 0, 1, 0, 0, 0, 1], [-1, 0, 0, 0, 1, 0, 0, 0, 1], [1, .1, 0, 0, 1, 0, 0, 0, 1]):
        refuses(lambda: Placement(**kwargs, rotation=rotation))
    refuses(lambda: Placement(**{**kwargs, "translation": [float("nan"), 0, 0]}))


def test_model_assets_reject_wrong_formats_and_detect_file_changes():
    with tempfile.TemporaryDirectory() as directory:
        assets = Path(directory)
        refuses(lambda: import_model("model.step", b"data", assets))
        refuses(lambda: import_model("model.SLDPRT", b"not a native file", assets))
        plan = ready_plan(assets)
        plan.models[0].path(assets).write_bytes(b"changed")
        assert any("SHA-256" in e for e in validate_plan(plan, assets))


def test_semantic_face_selection_refuses_ambiguity_and_selects_cylinder_by_axis_and_radius():
    surface = Surface(occurrence="A", kind="plane", point=[0, 0, 5], direction=[0, 0, 1])
    face = {"i": 7, "planar": True, "normal": [0, 0, -1], "point": [.08, .04, .005]}
    assert select_face(surface, [face], .001, .002) == 7
    assert "2 faces" in refuses(lambda: select_face(surface, [face, {**face, "i": 8}], .001, .002), AssemblyError)
    cylinder = Surface(occurrence="B", kind="cylinder", point=[0, 0, 10], direction=[0, 0, 1], radius=5)
    faces = [{"i": 3, "kind": "cylinder", "origin": [0, 0, 0], "axis": [0, 0, -1], "radius": .005},
             {"i": 4, "kind": "cylinder", "origin": [.02, 0, 0], "axis": [0, 0, 1], "radius": .005}]
    assert select_face(cylinder, faces, .001, .002) == 3
    assert "0 faces" in refuses(lambda: select_face(surface, [], .001, .002), AssemblyError)


def test_assembly_execution_bundles_pinned_models_and_exports_only_after_checks():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory); assets = root / "assets"
        plan, sw = ready_plan(assets), FakeAssembly()
        report = asyncio.run(run_assembly(sw, plan, assets, root / "run", plan.digest()))
        assert report["status"] == "awaiting_review", report
        assert not report["errors"] and all(c["ok"] for c in report["checks"])
        assert len(report["names"]) == 2 and len(list((root / "run/components").glob("*.SLDPRT"))) == 1
        assert Path(report["files"]["assembly"]).is_file() and Path(report["files"]["step"]).is_file()
        assert [c[0] for c in sw.calls].index("save_document") > max(i for i, c in enumerate(sw.calls) if c[0] == "add_mate")
        stored = json.loads((root / "run/report.json").read_text())
        assert stored == report


def test_approval_hash_and_input_validation_precede_all_cad_calls():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory); plan, sw = ready_plan(root / "assets"), FakeAssembly()
        refuses(lambda: asyncio.run(run_assembly(sw, plan, root / "assets", root / "run", "stale")), AssemblyError)
        assert not sw.calls
        plan.bom[0].confirmed = False
        refuses(lambda: asyncio.run(run_assembly(sw, plan, root / "assets", root / "run", plan.digest())), AssemblyError)
        assert not sw.calls


def test_interference_underconstraint_config_mismatch_and_mate_drift_prevent_export():
    cases = [{"interference": True}, {"constraints": 2}, {"bad_config": True}, {"drift": True}, {"ambiguous": True}, {"extra_docs": True}]
    for case in cases:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); plan, sw = ready_plan(root / "assets"), FakeAssembly(**case)
            report = asyncio.run(run_assembly(sw, plan, root / "assets", root / "run", plan.digest()))
            assert report["status"] == "failed" and report["errors"], (case, report)
            assert not any(c[0] in ("export_document", "save_document") for c in sw.calls), case
            if case.get("extra_docs"):
                assert not any(c[0] == "open_new_assembly" for c in sw.calls)


def test_incomplete_verification_and_missing_mate_readbacks_are_failures():
    with tempfile.TemporaryDirectory() as directory:
        plan = ready_plan(Path(directory)); names = {"base": "A", "block": "B"}
        assert len(check_inspection(plan, {}, names)) >= 3
        assert check_mates(plan, {}, names, {"M1": "M1"})
        data = {"mate_count": 1, "mates": [{"name": "M1", "type": "distance", "alignment": "closest", "entities": []}]}
        assert len(check_mates(plan, data, names, {"M1": "M1"})) >= 2


def test_persistent_approval_rejects_edits_and_acceptance_requires_verified_unchanged_outputs():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory); assets = root / "assets"; plan = ready_plan(assets)
        job = AssemblyJob(root / "job", assets)
        assert job.create(plan)["status"] == "awaiting_approval"
        refuses(job.begin)
        assert job.approve()["status"] == "approved"
        changed = plan.model_copy(deep=True); changed.occurrences[1].placement.translation = (20, 0, 0)
        (job.folder / "plan.json").write_text(changed.model_dump_json())
        refuses(job.approved_plan)
        (job.folder / "plan.json").write_text(plan.model_dump_json())
        run = job.begin(); report = asyncio.run(run_assembly(FakeAssembly(), plan, assets, run, plan.digest()))
        assert job.finish(report)["status"] == "awaiting_review"
        native = Path(report["files"]["assembly"]); before = native.read_bytes(); native.write_bytes(b"modified")
        refuses(job.accept)
        native.write_bytes(before)
        assert job.accept()["status"] == "accepted"
