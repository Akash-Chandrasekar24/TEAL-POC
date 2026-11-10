"""Parts on a sheet -> a build list per part -> the existing approval / build / review / export workflow (simulated SolidWorks)."""
import copy
import io
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import fakes  # noqa: F401
from fakes import FakeModel, good
from test_web_workflow import SimulatedBuild
from app.agent.orchestrator import Orchestrator
from app.agent.workers import ExtractionAgent, SolidWorksAgent
from app.drawing.sheets import sha256
from app.llm.base import LLMResponse
from app.web import parts, server
from app.web.workflow import Workflow


def page(no, labels, bom=None):
    views = [{"no": i + 1, "type": "front", "label": label} for i, label in enumerate(labels)]
    ev = {"extraction": {"callouts": [], "bom": bom or []}}
    return {"no": no, "sha": "a" * 64, "width": 400, "height": 300, "boxes": [[10 + 40 * i, 10, 40 + 40 * i, 60] for i in range(len(labels))],
            "read": {"views": views, "evidence": ev}}


def test_parts_come_from_the_bom_and_views_go_to_the_longest_matching_name():
    result = {"pages": [page(1, ["01. ROTOR HOUSING (FRONT VIEW)", "SECTION A-A (ROTOR HOUSING)", "01. ROTOR (FRONT VIEW)", "HOUSING ASSEMBLY (ISOMETRIC)"]),
                        page(2, ["MAJOR PARTS LIST (ASSEMBLY)"], bom=[{"item": "2", "description": "Rotor Housing (Trochoid)", "quantity": 1},
                                                                  {"item": "3", "description": "Rotor", "quantity": 1},
                                                                  {"item": "12", "description": "Hex. Bolt (M10)", "quantity": 12}])]}
    found = {p["id"]: p for p in parts.detect_parts(result)}
    assert set(found) == {"rotor-housing-trochoid", "rotor", "hex-bolt-m10"}
    assert [v["label"] for v in found["rotor-housing-trochoid"]["views"]] == ["01. ROTOR HOUSING (FRONT VIEW)", "SECTION A-A (ROTOR HOUSING)"]
    assert [v["label"] for v in found["rotor"]["views"]] == ["01. ROTOR (FRONT VIEW)"]                 # not the housing views
    assert "kind" not in found["hex-bolt-m10"] and found["hex-bolt-m10"]["quantity"] == 12                 # bolts are built in 3D too


def test_without_a_bom_numbered_labels_name_the_parts_and_a_plain_drawing_is_one_part():
    found = parts.detect_parts({"pages": [page(1, ["01. PISTON", "SECTION A-A", "02. PISTON PIN", "CONNECTING ROD", "04. CONNECTING ROD"])]})
    assert [p["id"] for p in found] == ["piston", "piston-pin", "connecting-rod"]
    assert [v["label"] for v in next(p for p in found if p["id"] == "connecting-rod")["views"]] == ["CONNECTING ROD", "04. CONNECTING ROD"]
    single = parts.detect_parts({"pages": [page(1, ["FRONT", "TOP"])]})
    assert [p["name"] for p in single] == ["This part"] and len(single[0]["views"]) == 2


def test_build_lists_are_checked_and_gaps_are_left_to_the_reviewer():
    with tempfile.TemporaryDirectory() as d:
        root, part = Path(d), {"id": "gear", "name": "Gear", "item": "4", "quantity": 1}
        ready = parts.save_part_list(root, "abcdef123456", part, {**good(), "unsupported": [{"id": "U1", "text": "root fillets"}], "accepted_gaps": ["U1"]}, "file")
        assert ready["check"]["status"] == "ready" and [u["id"] for u in ready["check"]["unsupported"]] == ["U1"]
        assert ready["draft"]["accepted_gaps"] == []                                              # a file cannot pre-accept a gap
        bad = parts.save_part_list(root, "abcdef123456", part, {"items": []}, "ai")
        assert bad["check"]["status"] == "invalid" and bad["check"]["blocking"]
        stopped = parts.save_part_list(root, "abcdef123456", part, {"status": "stopped", "reason": "not a part drawing"}, "ai")
        assert stopped["check"]["status"] == "stopped"
        assert parts.latest_lists(root, "abcdef123456")["gear"]["id"] == stopped["id"]           # the latest list per part
        assert parts.load_part_list(root, "abcdef123456", "../../etc") is None


def test_a_part_build_list_goes_through_approval_build_review_and_export():
    with tempfile.TemporaryDirectory() as d, patch.object(server, "PART_LISTS", Path(d) / "parts"):
        part = {"id": "gear", "name": "Gear", "item": "4", "quantity": 1}
        art = parts.save_part_list(server.PART_LISTS, "abcdef123456", part, {**good(), "unsupported": [{"id": "U1", "text": "root fillets"}]}, "file")
        build = SimulatedBuild()
        def export(approved, job, receipt):
            out = job / "exports"; out.mkdir()
            res = {}
            for key in ("SLDPRT", "STEP", "STL"):
                path = out / ("SIMULATED." + key.lower()); path.write_bytes(b"SIMULATED DELIVERY")
                res[key] = {"path": str(path), "sha256": sha256(path)}
            return res
        llm = FakeModel()
        app = Workflow(Path(d) / "wf", server.workflow_plan, build=build, exporter=export,
                       workers=lambda: Orchestrator(ExtractionAgent(llm), SolidWorksAgent(llm, build=build)))
        job = app.create("abcdef123456", art["id"])
        refused = app.action(job["id"], job["revision"], "build")                                 # gap not accepted: the graph stops before SolidWorks
        assert not build.calls and refused["phase"] == "awaiting_approval" and any("U1" in q for q in refused["questions"])
        job = app.action(refused["id"], refused["revision"], "build", accepted_gaps=["U1"])
        assert len(build.calls) == 1 and build.calls[0]["accepted_gaps"] == ["U1"] and job["phase"] == "awaiting_review"
        job = app.action(job["id"], job["revision"], "accept", review_hash=job["review_hash"], compared_drawing=True, checked_measurements=True)
        job = app.action(job["id"], job["revision"], "export", review_hash=job["review_hash"])
        assert job["phase"] == "exported" and len(job["exports"]) == 3


def test_a_part_list_that_is_not_ready_cannot_start_a_build():
    with tempfile.TemporaryDirectory() as d, patch.object(server, "PART_LISTS", Path(d)):
        art = parts.save_part_list(Path(d), "abcdef123456", {"id": "x", "name": "X", "item": "", "quantity": 1}, {"items": []}, "ai")
        try:
            server.workflow_plan("abcdef123456", art["id"])
            assert False
        except ValueError as ex:
            assert "not buildable" in str(ex)


def test_ai_build_list_sees_only_the_part_and_the_reviewed_values():
    from PIL import Image
    buf = io.BytesIO(); Image.new("RGB", (400, 300), "white").save(buf, "PNG")
    result = {"id": "abcdef123456", "llm": "domo", "pages": [page(1, ["01. GEAR", "02. SHAFT"])],
              "drawing_model": {"unit": "mm", "projection": "third", "issues": [],
                                "dimensions": [{"id": "P1.d1", "text": "Ø88", "views": ["P1.v1"], "confirmed": True},
                                               {"id": "P1.d2", "text": "284", "views": ["P1.v2"], "confirmed": True}]}}
    seen = []
    class Reader:
        model = "fake"; provider = "fake"
        def generate_json(self, system, parts_):
            seen.append(parts_)
            return LLMResponse(json.dumps(good()), good(), "fake", 0, {})
    with tempfile.TemporaryDirectory() as d, patch.object(server, "PART_LISTS", Path(d)), \
         patch.object(server, "result_with_design", lambda rid: copy.deepcopy(result)), \
         patch.object(server, "default_cache", lambda: type("C", (), {"get_blob": lambda self, sha: buf.getvalue()})()):
        art = server.create_part_list("abcdef123456", "gear", client=Reader())
    text = seen[0][1]
    assert "ONE part only: Gear" in text and "Ø88" in text and "284" not in text                # only this part's reviewed values
    assert art["check"]["status"] == "ready" and art["source"] == "ai" and art["part"]["id"] == "gear"
    result["drawing_model"]["issues"] = ["P1.d1 (Ø88): human review required. low"]
    with patch.object(server, "result_with_design", lambda rid: copy.deepcopy(result)):
        try:
            server.create_part_list("abcdef123456", "gear", client=Reader())
            assert False
        except ValueError as ex:
            assert "Approve the measurements" in str(ex)


def test_build_everything_prepares_lists_waits_for_one_approval_then_builds_each_part():
    result = {"id": "abcdef123456", "pages": [page(1, ["01. GEAR", "02. SHAFT", "03. HEX BOLT"])], "drawing_model": {"issues": []}}
    built = []
    class FakeWorkflow:
        def create(self, rid, attempt):
            return {"id": "j" + attempt[:5], "revision": 1}
        def action(self, jid, rev, action, **d):
            built.append((jid, d["accepted_gaps"]))
            return {"id": jid, "phase": "awaiting_review"}
        def status(self, jid):
            return {"id": jid, "phase": "awaiting_review"}
    def fake_create(rid, part_id, llm_name=None, client=None):
        draft = {**good(), "unsupported": [{"id": "U1", "text": "chamfers"}]} if part_id == "gear" else {"items": []}
        return parts.save_part_list(server.PART_LISTS, rid, {"id": part_id, "name": part_id, "item": "", "quantity": 1}, draft, "ai")
    class Now:                                                                     # run the background jobs inline
        def submit(self, fn, *a):
            fn(*a)
    ready = {"ready": True, "detail": "Ready to build."}
    with tempfile.TemporaryDirectory() as d, patch.object(server, "PART_LISTS", Path(d)), patch.object(server, "WORKFLOW", FakeWorkflow()), \
         patch.object(server, "_BATCH_POOL", Now()), patch.object(server, "create_part_list", fake_create), \
         patch.object(server, "result_with_design", lambda rid: copy.deepcopy(result)):
        batch = server.start_batch("abcdef123456")
        batch = server.load_batch("abcdef123456")
        assert batch["phase"] == "awaiting_approval" and not built                   # nothing built before the human approves
        assert {i["part_id"]: i["status"] for i in batch["items"]} == {"gear": "listed", "shaft": "listed", "hex-bolt": "listed"}
        with patch.object(parts, "solidworks_status", lambda: {"ready": False, "detail": "not running"}):
            try:
                server.approve_batch("abcdef123456", batch["id"], {"gear": ["U1"]}); assert False
            except ValueError as ex:
                assert "not ready" in str(ex)
        with patch.object(parts, "solidworks_status", lambda: ready):
            try:
                server.approve_batch("abcdef123456", "000000000000", {}); assert False
            except ValueError as ex:
                assert "changed" in str(ex)
            server.approve_batch("abcdef123456", batch["id"], {"gear": ["U1"]})
        done = server.load_batch("abcdef123456")
        items = {i["part_id"]: i for i in done["items"]}
        assert done["phase"] == "done" and len(built) == 1 and built[0][1] == ["U1"]
        assert items["gear"]["status"] == "awaiting_review" and items["shaft"]["status"] == "not_buildable"
        with patch.object(parts, "solidworks_status", lambda: ready):
            assert server.parts_overview("abcdef123456")["batch"]["items"][0]["job"]["phase"] == "awaiting_review"


def test_approve_all_refuses_a_part_whose_gaps_were_not_accepted():
    result = {"id": "abcdef123456", "pages": [page(1, ["01. GEAR"])], "drawing_model": {"issues": []}}
    with tempfile.TemporaryDirectory() as d, patch.object(server, "PART_LISTS", Path(d)), \
         patch.object(server, "_BATCH_POOL", type("N", (), {"submit": lambda self, fn, *a: None})()), \
         patch.object(server, "result_with_design", lambda rid: copy.deepcopy(result)), \
         patch.object(parts, "solidworks_status", lambda: {"ready": True, "detail": ""}):
        parts.save_part_list(Path(d), "abcdef123456", {"id": "gear", "name": "Gear", "item": "", "quantity": 1},
                             {**good(), "unsupported": [{"id": "U1", "text": "chamfers"}]}, "file")
        batch = server.start_batch("abcdef123456")
        server._save_batch("abcdef123456", {**batch, "phase": "awaiting_approval"})
        out = server.approve_batch("abcdef123456", batch["id"], {})
        assert out["items"][0]["status"] == "not_approved"


def test_a_material_column_read_into_the_name_is_split_off():
    bom = [{"item": "1", "description": "Pump Casing \u2014 Cast Iron (FC250)", "quantity": 1}, {"item": "6", "description": "Mechanical Seal \u2014 Carbon / SiC (FKM)", "quantity": 1}]
    found = {p["id"]: p for p in parts.detect_parts({"pages": [page(1, ["01. PUMP CASING (FRONT VIEW)", "SECTION A-A (PUMP CASING)"], bom=bom)]})}
    assert found["pump-casing"]["material"] == "Cast Iron (FC250)" and len(found["pump-casing"]["views"]) == 2
    assert "kind" not in found["mechanical-seal"]                                                          # built in 3D like every part
