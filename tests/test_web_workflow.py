"""Approval/restart/export boundaries, using simulated CAD and real checkpoints."""
import copy
import json
import tempfile
import asyncio
import shutil
import struct
from unittest.mock import patch
from pathlib import Path

from fakes import FakeModel, good, failure
from app.agent.orchestrator import Orchestrator
from app.agent.workers import ExtractionAgent, SolidWorksAgent
from app.drawing.model import Resolutions, reconcile
from app.drawing.sheets import sha256
from app.web.workflow import Workflow
from drawing_fixtures import drawing_result


def raises(fn, part):
    try:
        fn()
    except (ValueError, OSError) as ex:
        assert part.lower() in str(ex).lower(), str(ex)
    else:
        raise AssertionError("Expected a refused action")


class SimulatedBuild:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def __call__(self, approved, folder):
        self.calls.append(copy.deepcopy(approved))
        if self.fail:
            self.fail = False
            return {"report": {"ok": False, "failure": failure(), "items": [], "final": {}}, "finish": None}
        private = folder / "private" / str(len(self.calls)); private.mkdir(parents=True)
        files = {}
        for key, suffix in (("SLDPRT", ".SLDPRT"), ("STL", ".stl"), ("PREVIEW", ".png")):
            path = private / ("SIMULATED" + suffix); path.write_bytes(b"SIMULATED TEST ONLY " + str(approved).encode())
            files[key] = str(path)
        return {"report": {"ok": True, "failure": None, "items": [], "final": {"bbox_mm": [[0, 0, 0], [120, 80, 10]]}, "gaps": []},
                "finish": {"ok": True, "files": files, "checks": [], "configuration": "Default"}}


def service(folder, build=None, model=None):
    build = build or SimulatedBuild()
    artifact = {"id": "123456abcdef", "result_id": "abcdef123456", "llm": "domo"}
    draft = good()
    def plan(rid, attempt):
        assert rid == artifact["result_id"] and attempt == artifact["id"]
        return artifact, draft
    exports = []
    def export(approved, job, receipt):
        exports.append(copy.deepcopy(receipt))
        output = job / "exports"; output.mkdir()
        result = {}
        for key in ("SLDPRT", "STEP", "STL"):
            path = output / ("SIMULATED." + key.lower()); path.write_bytes(b"SIMULATED DELIVERY")
            result[key] = {"path": str(path), "sha256": sha256(path)}
        return result
    llm = model or FakeModel()
    factory = lambda: Orchestrator(ExtractionAgent(llm), SolidWorksAgent(llm, build=build))
    app = Workflow(folder, plan, build=build, exporter=export, workers=factory)
    return app, artifact, build, exports


def test_browser_workflow_requires_exact_review_before_export_and_survives_reload():
    with tempfile.TemporaryDirectory() as directory:
        app, artifact, build, exports = service(Path(directory))
        job = app.create(artifact["result_id"], artifact["id"])
        assert not build.calls and job["phase"] == "awaiting_approval"
        raises(lambda: app.action(job["id"], job["revision"], "export"), "not available")
        job = app.action(job["id"], job["revision"], "build")
        assert job["phase"] == "awaiting_review" and job["preview"] and not job["exports"] and not exports
        assert not list((Path(directory) / "jobs" / job["id"]).rglob("*.step"))
        raises(lambda: app.artifact(job["id"], "SLDPRT"), "before downloading")
        raises(lambda: app.action(job["id"], 0, "accept"), "stale")
        raises(lambda: app.action(job["id"], job["revision"], "accept", review_hash="old"), "preview changed")
        raises(lambda: app.action(job["id"], job["revision"], "accept", review_hash=job["review_hash"]), "Confirm")
        # Reconstruct the controller; SQLite and JSON carry the human pause.
        reopened, _, _, _ = service(Path(directory), build)
        assert reopened.latest(artifact["result_id"])["review_hash"] == job["review_hash"]
        job = app.action(job["id"], job["revision"], "accept", review_hash=job["review_hash"], compared_drawing=True, checked_measurements=True)
        assert job["phase"] == "accepted" and not exports
        job = app.action(job["id"], job["revision"], "export", review_hash=job["review_hash"])
        assert job["phase"] == "exported" and len(exports) == 1 and len(job["exports"]) == 3
        path = app.artifact(job["id"], "STEP"); path.write_bytes(b"changed")
        raises(lambda: app.artifact(job["id"], "STEP"), "changed")


def test_agent_repair_pauses_for_human_then_builds_only_after_the_correction():
    repair = {"status": "suggestion", "item_id": "F2", "params": good()["items"][1]["params"], "reason": "Confirm the supplied depth before retrying"}
    repair["params"]["diameter"] = 9
    with tempfile.TemporaryDirectory() as directory:
        app, artifact, build, exports = service(Path(directory), SimulatedBuild(True), FakeModel(repairs=[repair]))
        job = app.create(artifact["result_id"], artifact["id"])
        job = app.action(job["id"], 0, "build")
        assert job["phase"] == "awaiting_fix_approval" and len(build.calls) == 1 and not exports
        params = copy.deepcopy(job["proposal"]["params"]); params["diameter"] = 8.5
        job = app.action(job["id"], job["revision"], "edit_fix", params=params)
        assert job["phase"] == "awaiting_review" and len(build.calls) == 2
        assert build.calls[-1]["items"][1]["params"]["diameter"] == 8.5 and not exports


def test_changed_source_or_private_model_invalidates_acceptance():
    with tempfile.TemporaryDirectory() as directory:
        app, artifact, _, exports = service(Path(directory))
        job = app.create(artifact["result_id"], artifact["id"])
        job = app.action(job["id"], 0, "build")
        artifact["modified"] = True
        assert app.status(job["id"])["stale"]
        raises(lambda: app.action(job["id"], job["revision"], "accept"), "changed")
        artifact.pop("modified")
        receipt = app.load(job["id"])["receipt"]
        Path(receipt["files"]["SLDPRT"]["path"]).write_bytes(b"modified")
        assert app.status(job["id"])["phase"] == "needs_human"
        raises(lambda: app.action(job["id"], job["revision"], "accept", review_hash=job["review_hash"]), "changed")
        assert not exports


def test_a_correction_is_made_on_the_built_part_not_built_again():
    with tempfile.TemporaryDirectory() as directory:
        app, artifact, build, _ = service(Path(directory))
        edits = []

        def editor(approved, folder, prior, source):
            edits.append((source, prior))
            out = build(approved, folder)
            out["report"]["items"] = [{"id": "A1", "feature": "hole", "status": "ok", "message": "added by the SolidWorks agent: {}"}]
            out["report"]["agent"] = {"added": ["A1"], "review": {"summary": "added the hole"}}
            return out
        app.editor = editor
        job = app.create(artifact["result_id"], artifact["id"])
        job = app.action(job["id"], 0, "build")
        first = app.load(job["id"])["receipt"]["files"]["SLDPRT"]["path"]
        raises(lambda: app.action(job["id"], job["revision"], "correct", review_hash=job["review_hash"], message=" "), "Describe")
        job = app.action(job["id"], job["revision"], "correct", review_hash=job["review_hash"], message="Hole diameter must be 8")
        assert len(build.calls) == 2 and edits[0][0] == first and edits[0][1]["ok"]          # the editor got the BUILT part and its report
        assert job["phase"] == "awaiting_review" and job["corrections"] == ["Hole diameter must be 8"] and job["agent_reply"]["added"] == ["A1"]
        assert json.loads((app.folder(job["id"]) / "corrections.json").read_text(encoding="utf-8")) == ["Hole diameter must be 8"]
        assert app.load(job["id"])["receipt"]["files"]["SLDPRT"]["path"] != first             # the edited part is the one to review now
        raises(lambda: app.action(job["id"], job["revision"], "export", review_hash=job["review_hash"]), "not available")

        app.editor = lambda *a: (_ for _ in ()).throw(RuntimeError("SolidWorks is busy"))
        before = app.load(job["id"])["receipt"]
        job = app.action(job["id"], job["revision"], "correct", review_hash=job["review_hash"], message="and the slot")
        assert job["phase"] == "awaiting_review" and "SolidWorks is busy" in job["detail"] and app.load(job["id"])["receipt"] == before


def test_unreadable_source_clarifications_are_explicit_provenance_not_silent_dismissal():
    source = drawing_result(); source["pages"][0]["read"]["issues"] = ["Hole depth unreadable"]
    assert reconcile(source).issues
    resolution = Resolutions.model_validate({"clarifications": [{"id": "P1.u1", "answer": "The section marks the hole depth as 12", "text": "12", "views": ["P1.v2"]}]})
    reviewed = reconcile(source, resolution)
    assert not reviewed.issues and reviewed.dimensions[0].id == "P1.h1"
    assert reviewed.dimensions[0].confirmed and reviewed.dimensions[0].readings[0].source == "human"
    assert "Human clarification" in reviewed.pages[0].notes[-1]
    assert source["pages"][0]["read"]["issues"] == ["Hole depth unreadable"]
    invalid = resolution.model_copy(deep=True); invalid.clarifications[0].views = ["P1.v99"]
    raises(lambda: reconcile(source, invalid), "valid view")


def binary_mesh(path):
    path.write_bytes(b"SIMULATED TEST MESH".ljust(80, b" ") + struct.pack("<I", 1)
                     + struct.pack("<12fH", 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 2, 3, 0))


def test_private_preview_does_not_prepare_step_deliverables():
    from app.finish import finish_part
    from app.mcp_client.solidworks import ToolResult
    class FakeSW:
        calls = []
        async def save_document(self, path):
            self.calls.append("save"); path.write_bytes(b"SIMULATED PART")
            return ToolResult("save", "COMPLETED", False, 0)
        async def export_document(self, fmt, path):
            self.calls.append(fmt)
            assert fmt == "STL", "Review must not export STEP"
            binary_mesh(path)
            return ToolResult("export", "COMPLETED", False, 0)
    def render(stl, png, title):
        png.write_bytes(b"SIMULATED PREVIEW"); return png
    with tempfile.TemporaryDirectory() as directory, patch("app.finish.render_stl_png", side_effect=render):
        sw = FakeSW()
        report = asyncio.run(finish_part(sw, Path(directory), "test", "mm", review_only=True))
        assert report.ok and sw.calls == ["save", "STL"] and set(report.files) == {"SLDPRT", "STL", "PREVIEW"}


def test_native_export_uses_saved_review_and_refuses_modified_or_wrong_active_model():
    from app.agent.guards import list_hash
    from app.agent.review import _export
    from app.mcp_client.solidworks import ToolResult
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory); private = root / "private"; private.mkdir()
        source = private / "SIMULATED.SLDPRT"; source.write_bytes(b"SIMULATED NATIVE")
        mesh = private / "SIMULATED.stl"; binary_mesh(mesh)
        image = private / "SIMULATED.png"; image.write_bytes(b"SIMULATED IMAGE")
        receipt = {"approved_hash": list_hash(good()), "configuration": "Default",
                   "files": {key: {"path": str(path), "sha256": sha256(path)} for key,path in (("SLDPRT",source),("STL",mesh),("PREVIEW",image))}}
        class FakeSW:
            calls = []
            modified = False
            wrong_model = False
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            def result(self, data): return ToolResult("fake", json.dumps(data), False, 0)
            async def ensure_ready(self): return self.result({})
            async def inspect_assembly(self, kind):
                return self.result({"documents": [{"path": str(source)}], "active_path": str(source) if not self.wrong_model else "unrelated.SLDPRT",
                                    "modified": self.modified, "configuration": "Default"})
            async def export_document(self, fmt, path):
                self.calls.append(fmt)
                if fmt == "STEP": path.write_text("SIMULATED MILLI STEP")
                else: shutil.copyfile(mesh, path)
                return self.result({})
        sw = FakeSW()
        with patch("app.agent.review.SolidWorksClient", return_value=sw):
            sw.modified = True
            raises(lambda: asyncio.run(_export(good(), root, receipt)), "unchanged reviewed model")
            assert not sw.calls
            sw.modified = False; sw.wrong_model = True
            raises(lambda: asyncio.run(_export(good(), root, receipt)), "unchanged reviewed model")
            assert not sw.calls
            sw.wrong_model = False
            outputs = asyncio.run(_export(good(), root, receipt))
            assert set(outputs) == {"SLDPRT", "STEP", "STL"} and sw.calls == ["STEP", "STL"]
            assert outputs["SLDPRT"]["sha256"] == receipt["files"]["SLDPRT"]["sha256"]


def test_workflow_http_denies_early_downloads_and_cross_origin_native_actions():
    import http.client
    import threading
    from http.server import ThreadingHTTPServer
    from app.web import server
    class Quiet(server.Handler):
        def log_message(self, *args): pass
    with tempfile.TemporaryDirectory() as directory:
        app, artifact, _, _ = service(Path(directory))
        with patch.object(server, "WORKFLOW", app):
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
            connection = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=10)
            body = json.dumps({"result_id": artifact["result_id"], "attempt": artifact["id"]})
            try:
                connection.request("POST", "/api/workflow/start", body, {"Content-Type": "application/json", "Origin": "https://unrelated.example"})
                response = connection.getresponse(); response.read(); assert response.status == 403
                connection.request("POST", "/api/workflow/start", body, {"Content-Type": "application/json"})
                response = connection.getresponse(); job = json.loads(response.read()); assert response.status == 200
                connection.request("GET", f"/api/workflow/{job['id']}/download/STEP")
                response = connection.getresponse(); response.read(); assert response.status == 409
                connection.request("GET", f"/api/workflow/result/{artifact['result_id']}")
                response = connection.getresponse(); reopened = json.loads(response.read())
                assert reopened["phase"] == "awaiting_approval" and response.getheader("Cache-Control") == "no-store"
            finally:
                connection.close(); httpd.shutdown(); httpd.server_close(); thread.join(timeout=5)


def test_upload_triggers_extraction_for_both_parts_and_assemblies_without_navigation():
    import base64
    from app.perception.extract import PageRead
    from app.web import server
    from app.drawing.planner import PageInterpretation
    from drawing_fixtures import png
    for kind in ("part", "assembly", "mixed"):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            read = PageRead(1, [], [], {"document_kind": kind, "unit": "mm", "projection": "third"}, [], 0)
            model = FakeModel(reads=[{"rows": [{"item": "1", "part_number": "P-10", "description": "Part", "quantity": 1, "kind": "part", "text": "P-10 QTY 1"}], "unit": "mm"}])
            with patch.multiple(server, RESULTS=root/"web", UPLOADS=root/"uploads", RUNS=root/"runs", ASSEMBLIES=root/"assemblies", MODELS=root/"models"), \
                 patch("app.llm.make_client", return_value=model), \
                 patch.object(Orchestrator, "read_page", return_value=read) as reading, \
                 patch.object(Orchestrator, "interpret", return_value=[PageInterpretation(features=[])]) as interpreting:
                result = server.run("abcdef123456", [{"name":"drawing.png", "data":base64.b64encode(png()).decode()}], "domo", True, workflow=True)
                assert reading.call_count == 1 and interpreting.call_count == 1
                assert "extracted_features" in result
                assert ("assembly" in result) is (kind != "part")
                if kind != "part":
                    assert result["assembly"]["plan"]["bom"][0]["part_number"] == "P-10"
                    assert not result["assembly"]["approved"]


def test_reread_uses_original_upload_and_creates_a_separate_result():
    import base64
    from app.web import server
    from test_drawing_web import saved_result
    with saved_result() as source, tempfile.TemporaryDirectory() as directory:
        uploads = Path(directory)
        original = uploads/source["id"]; original.mkdir()
        (original/"1_original.png").write_bytes(b"ORIGINAL SOURCE TEST ONLY")
        with patch.object(server,"UPLOADS",uploads), patch.object(server,"run",return_value={"id":"111111111111"}) as reading:
            result=server.reread_result(source["id"],"111111111111")
            assert result["id"] != source["id"]
            args,kwargs=reading.call_args
            assert args[0]=="111111111111" and args[2]==source["llm"] and args[3] is True and kwargs["workflow"]
            assert base64.b64decode(args[1][0]["data"])==b"ORIGINAL SOURCE TEST ONLY"


def test_assembly_browser_build_uses_private_review_and_checks_dependency_hashes():
    from assembly_fixtures import FakeAssembly, ready_plan
    from app.assembly.runner import run_assembly
    from app.agent.guards import list_hash
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory); assets=root/"assets"; plan=ready_plan(assets)
        artifact={"id":"123456abcdef", "result_id":"abcdef123456", "plan":plan.model_dump()}
        class ReviewAssembly(FakeAssembly):
            def __getattr__(self,name):
                original=super().__getattr__(name)
                async def call(*args,**kwargs):
                    if name=="export_document" and args[0]=="STL":
                        self.calls.append((name,args,kwargs));binary_mesh(Path(args[1]))
                        from app.mcp_client.solidworks import ToolResult
                        return ToolResult(name,"COMPLETED",False,0)
                    response=await original(*args,**kwargs)
                    if name=="inspect_assembly" and args[0]=="workspace":
                        data=response.json();data["configuration"]="Default"
                        from app.mcp_client.solidworks import ToolResult
                        return ToolResult(name,json.dumps(data),False,0)
                    return response
                return call
        sw=ReviewAssembly()
        def build(approved,folder,assets,workers):
            def render(stl,png,title):png.write_bytes(b"SIMULATED PREVIEW");return png
            with patch("app.preview.render_stl_png",side_effect=render):
                return asyncio.run(run_assembly(sw,plan,assets,folder/"private"/"review",plan.digest(),review_only=True))
        app=Workflow(root/"workflow",lambda rid,attempt:(artifact,plan.model_dump()),assets=assets,assembly_build=build)
        job=app.create(artifact["result_id"],artifact["id"])
        job=app.action(job["id"],0,"build")
        assert job["phase"]=="awaiting_review",job
        assert not any(c[0]=="export_document" and c[1][0]=="STEP" for c in sw.calls)
        receipt=app.load(job["id"])["receipt"]
        assert "SLDASM" in receipt["files"] and receipt["dependencies"]
        assert receipt["approved_hash"]==list_hash(plan.model_dump())
        Path(receipt["dependencies"][0]["path"]).write_bytes(b"MODIFIED COMPONENT")
        raises(lambda:app.action(job["id"],job["revision"],"accept",review_hash=job["review_hash"],compared_drawing=True,checked_measurements=True),"changed")
