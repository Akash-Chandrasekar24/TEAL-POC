"""Simulated native sheet acceptance checks; no live CAD fidelity is implied."""
import asyncio
import copy
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import fakes  # noqa: F401
from assembly_fixtures import FakeAssembly, ready_plan

from app.assembly.jobs import AssemblyJob
from app.assembly.runner import run_assembly
from app.drawing.sheets import SheetPlan, assembly_references, check_sheet, run_sheet, sha256, validate_sheet
from app.mcp_client.solidworks import ToolResult


def plan_at(root):
    source = root / "source.SLDPRT"
    source.write_bytes(b"SIMULATED CAD ONLY")
    return SheetPlan.model_validate({"name": "plate_sheet", "model_path": str(source), "model_sha256": sha256(source),
        "configuration": "Default", "model_reviewed": True, "source_evidence": "TEST fixture, not reference CAD",
        "projection": "third", "views": [{"id": "V1", "orientation": "front", "x_mm": 90, "y_mm": 90, "scale": 1},
        {"id": "V2", "orientation": "top", "x_mm": 90, "y_mm": 200, "scale": 1}],
        "dimensions": [{"view": "V1", "native_name": "D1@Sketch1", "expected_si": .08, "tolerance_si": .00001,
                        "source_evidence": "TEST printed 80 mm"}]})


class FakeSheet:
    def __init__(self, plan, *, bad=None):
        self.plan, self.bad, self.views, self.calls = plan, bad, [], []
        self.path, self.dimensioned = "", False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def ensure_ready(self):
        return SimpleNamespace(ok=True)

    def __getattr__(self, name):
        async def call(*args, **kwargs):
            self.calls.append(name)
            data = {}
            if name == "inspect_assembly":
                data = {"documents": [{}] if self.bad == "existing_document" else []}
            elif name == "add_drawing_view":
                x, y = kwargs["pos_x"], kwargs["pos_y"]
                refs = [{"path": p, "configuration": c} for p, c in assembly_references(self.plan)] if Path(self.plan.model_path).suffix.lower() == ".sldasm" else []
                self.views.append({"name": f"View{len(self.views)+1}", "model_path": self.plan.model_path,
                    "configuration": "Wrong" if self.bad == "configuration" else "Default", "orientation": args[0],
                    "scale": kwargs["scale"], "position_m": [x, y], "outline_m": [x-.025, y-.025, x+.025, y+.025],
                    "dimensions": [], "component_references": refs})
            elif name == "inspect_drawing":
                views = copy.deepcopy(self.views)
                if self.dimensioned and views:
                    views[0]["dimensions"] = [{"name": "D1@Sketch1", "value_si": .09 if self.bad == "dimension" else .08}]
                if self.bad == "overlap" and len(views) == 2:
                    views[1]["outline_m"] = views[0]["outline_m"]
                if self.bad == "references":
                    for view in views:
                        view["component_references"] = []
                data = {"version": 1, "title": "Sheet1", "path": self.path, "sheet_count": 1,
                        "sheet_size_m": [.420, .297], "first_angle": self.bad == "projection", "rebuild_ok": True,
                        "view_count": len(views), "views": views}
            elif name == "auto_dimension_drawing":
                self.dimensioned = True
            elif name == "save_document":
                self.path = str(args[0]); Path(self.path).write_bytes(b"SIMULATED DRAWING")
            elif name == "export_document":
                Path(args[1]).write_bytes(b"%PDF-1.7 SIMULATED")
            elif name not in {"create_drawing", "auto_center_marks"}:
                raise AssertionError(name)
            return ToolResult(name, json.dumps(data), False, 0)
        return call


def test_sheet_creates_linked_native_and_pdf_only_after_numeric_readback_checks():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory); plan = plan_at(root); sw = FakeSheet(plan)
        report = asyncio.run(run_sheet(sw, plan, root / "out", plan.digest()))
        assert report["status"] == "awaiting_review", report
        assert not report["accuracy_verified"]
        assert set(report["files"]) == {"drawing", "pdf"} and all(Path(p).is_file() for p in report["files"].values())
        assert sw.calls.index("auto_dimension_drawing") < sw.calls.index("save_document") < sw.calls.index("export_document")


def test_sheet_refuses_changed_model_unapproved_plan_and_unsupported_requirements():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory); plan = plan_at(root); sw = FakeSheet(plan)
        try:
            asyncio.run(run_sheet(sw, plan, root / "out", "changed"))
        except ValueError:
            assert not sw.calls
        else:
            raise AssertionError("Changed approval accepted")
        plan.unsupported_requirements = ["Section A-A and BOM balloons are required"]
        assert any("Section A-A" in b for b in validate_sheet(plan))
        plan.unsupported_requirements = []; plan.model_reviewed = False
        assert any("Review the 3D model" in b for b in validate_sheet(plan))
        plan.model_reviewed = True; Path(plan.model_path).write_bytes(b"changed")
        assert any("reviewed hash" in b for b in validate_sheet(plan))


def test_wrong_projection_configuration_dimensions_or_overlap_prevent_export():
    for bad in ("projection", "configuration", "dimension", "overlap", "existing_document"):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); plan = plan_at(root); sw = FakeSheet(plan, bad=bad)
            report = asyncio.run(run_sheet(sw, plan, root / "out", plan.digest()))
            assert report["status"] == "failed" and report["errors"], (bad, report)
            assert "export_document" not in sw.calls and not report["files"]


def test_layout_unknown_dimension_and_invalid_projection_placement_are_blocked():
    with tempfile.TemporaryDirectory() as directory:
        plan = plan_at(Path(directory))
        plan.views[1].y_mm = 50
        assert any("projection" in b for b in validate_sheet(plan))
        plan.views[1].x_mm = 120
        assert any("align" in b for b in validate_sheet(plan))
        plan.dimensions[0].view = "V99"
        assert any("unknown view" in b for b in validate_sheet(plan))


def accepted_assembly(root):
    assets = root / "models"
    plan = ready_plan(assets)
    job = AssemblyJob(root / "assembly_job", assets)
    job.create(plan); job.approve()
    output = job.begin()
    report = asyncio.run(run_assembly(FakeAssembly(), plan, assets, output, plan.digest()))
    job.finish(report); job.accept()
    return job, report


def test_assembly_sheet_requires_accepted_job_and_pinned_dependency_readback():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory); plan = plan_at(root)
        job, report = accepted_assembly(root)
        plan.model_path = report["files"]["assembly"]; plan.model_sha256 = sha256(plan.model_path)
        assert any("accepted assembly job" in b for b in validate_sheet(plan))
        plan.accepted_assembly_job = str(job.folder)
        assert not validate_sheet(plan)
        result = asyncio.run(run_sheet(FakeSheet(plan), plan, root / "sheet", plan.digest()))
        assert result["status"] == "awaiting_review", result
        bad = asyncio.run(run_sheet(FakeSheet(plan, bad="references"), plan, root / "bad_sheet", plan.digest()))
        assert bad["status"] == "failed" and not bad["files"]
        component = next(Path(report["files"]["components"]).glob("*.SLDPRT")); component.write_bytes(b"changed")
        assert any("dependency changed" in b for b in validate_sheet(plan))


def test_sheet_cli_approval_execution_and_acceptance_detect_output_changes():
    from scripts import run_sheet as cli
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory); plan = plan_at(root); file = root / "input.json"
        file.write_text(plan.model_dump_json(), encoding="utf-8")
        with patch.object(cli, "JOBS", root / "jobs"), patch("app.agent.execution.config.PROJECT_ROOT", root):
            assert cli.main(["start", str(file)]) == 0
            jid = next(cli.JOBS.iterdir()).name
            assert cli.main(["execute", jid]) == 2  # no native runtime opt-in
            assert cli.main(["approve", jid]) == 0
            with patch("app.mcp_client.solidworks.SolidWorksClient", return_value=FakeSheet(plan)):
                state = asyncio.run(cli.execute(cli.JOBS / jid))
            assert state["status"] == "awaiting_review", state
            Path(state["report"]["files"]["pdf"]).write_bytes(b"changed")
            assert cli.main(["accept", jid]) == 2
