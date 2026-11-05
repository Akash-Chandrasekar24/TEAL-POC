"""Scope/quality regressions using supplied drawings and controlled model replies."""
import copy
import json
import http.client
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import fakes  # noqa: F401
from fakes import FakeModel
from drawing_fixtures import png
from test_drawing_web import saved_result, scripted

from app.drawing.model import Resolutions, reconcile
from app.drawing.readiness import assess_page, readiness, source_quality, VisualAssessment
from app.perception.segment import segment_png, screenshot_canvas
from app.web import server

ROOT = Path(__file__).resolve().parents[1]


def scope(kind="assembly"):
    return {"document_kind": kind, "summary": "A reviewed drawing scope fixture",
            "requirements": [{"capability": "assembly" if kind != "part" else "rectangular_plate", "evidence": "Multiple component balloons"}],
            "components": ["pump", "frame"] if kind != "part" else ["plate"], "unreadable": [], "missing_information": []}


def test_supplied_relief_sheet_warns_of_native_resolution_without_upsampling_claim():
    quality = source_quality((ROOT / "image copy.png").read_bytes())
    assert (quality["width"], quality["height"]) == (320, 453)
    assert quality["warnings"] and not quality["accuracy_verified"] and not quality["upscaling_restores_missing_detail"]


def test_skid_screenshot_chrome_is_excluded_and_dense_bom_retained_as_table():
    data = (ROOT / "image copy 2.png").read_bytes()
    canvas = screenshot_canvas(data)
    assert canvas is not None and canvas[0] >= 140 and canvas[1] >= 80
    boxes = segment_png(data)
    assert all(b.box[0] >= canvas[0] and b.box[1] >= canvas[1] for b in boxes)
    assert any(b.kind == "table" and b.box[0] > 580 and b.box[1] < 150 for b in boxes)
    assert any(b.kind == "view" and b.box[0] < 200 and b.box[1] < 150 for b in boxes)


def test_source_grid_fallback_does_not_misclassify_support_block_as_table():
    boxes = segment_png((ROOT / "inputs/image_9.png").read_bytes())
    assert any(b.kind == "view" and b.box[0] < 100 and b.box[1] < 50 and b.box[3] > 350 for b in boxes)
    assert screenshot_canvas((ROOT / "inputs/image_9.png").read_bytes()) is None


def test_tall_hatched_connecting_rod_section_is_not_filtered_as_a_table():
    boxes = segment_png((ROOT / "image copy 4.png").read_bytes())
    # Section B-B, including its flange and web measurements. The old classifier
    # mistook the short web edges for table columns and discarded this region.
    target = [750, 45, 814, 317]
    hits = [b for b in boxes if b.box[0] <= target[0] and b.box[1] <= target[1] and b.box[2] >= target[2] and b.box[3] >= target[3]]
    assert hits and all(b.kind == "view" for b in hits)


def test_readiness_never_turns_model_assessment_into_verified_accuracy():
    assessed = VisualAssessment.model_validate(scope("part"))
    report = readiness([assessed], [source_quality(png())])
    assert report["status"] == "not_verified_for_high_accuracy" and not report["accuracy_verified"]
    assert not report["assessments_reviewed"] and report["blockers"]
    assert report["requirements"][0]["support"] == "automatic"


def test_unknown_capabilities_and_missing_information_remain_blockers():
    raw = scope(); raw["requirements"].append({"capability": "invented_geometry_recipe", "evidence": "Not in the application"})
    raw["missing_information"] = ["Gear tooth module missing"]
    report = readiness([VisualAssessment.model_validate(raw)], [])
    assert any("invented_geometry_recipe" in b for b in report["blockers"])
    assert any("Gear tooth module" in b for b in report["blockers"])


def test_invalid_scope_reply_is_discarded():
    class Model(FakeModel):
        def discard(self, *args):
            self.discarded = True
    model = Model(reads=[{"document_kind": "part", "summary": "No geometry", "requirements": []}])
    try:
        assess_page(model, png())
    except ValueError:
        assert model.discarded
    else:
        raise AssertionError("A scope assessment silently dropped all geometry")


def test_saved_assembly_scope_blocks_single_part_planner_before_any_model_call():
    with saved_result() as result, tempfile.TemporaryDirectory() as directory:
        with patch.object(server, "READINESS", Path(directory)):
            artifact = server.assess_result(result["id"], client=FakeModel(reads=[scope()]))
            assert server.result_with_design(result["id"])["readiness"] == artifact
            model = scripted()
            try:
                server.plan_result(result["id"], reconcile(result).source_hash, Resolutions(), client=model)
            except ValueError as ex:
                assert "assembly or multiple detail parts" in str(ex) and not model.calls
            else:
                raise AssertionError("An assembly was silently passed to the single-part compiler")


def test_single_part_scope_keeps_existing_planning_and_stale_scope_is_refused():
    with saved_result() as result, tempfile.TemporaryDirectory() as directory:
        with patch.object(server, "READINESS", Path(directory)):
            artifact = server.assess_result(result["id"], client=FakeModel(reads=[scope("part")]))
            planned = server.plan_result(result["id"], reconcile(result).source_hash, Resolutions(), client=scripted())
            assert planned["plan"]["status"] == "ready_for_approval"
            path = server.READINESS / result["id"] / "latest.json"
            artifact["source_hash"] = "changed"
            path.write_text(json.dumps(artifact), encoding="utf-8")
            try:
                server.plan_result(result["id"], reconcile(result).source_hash, Resolutions(), client=scripted())
            except ValueError as ex:
                assert "stale" in str(ex)
            else:
                raise AssertionError("Stale scope accepted")


def test_unreadable_callouts_do_not_silently_drop_out_of_planning():
    with saved_result() as result:
        result["pages"][0]["read"]["issues"] = ["Unreadable hole depth beside section A-A"]
        model = reconcile(result)
        assert any("Unreadable hole depth" in issue for issue in model.issues)


def test_reader_scope_alone_blocks_assembly_even_without_optional_assessment():
    with saved_result() as result:
        result["pages"][0]["read"]["title_block"]["document_kind"] = "assembly"
        (server.RESULTS / f"{result['id']}.json").write_text(json.dumps(result), encoding="utf-8")
        try:
            server.plan_result(result["id"], reconcile(result).source_hash, Resolutions(), client=scripted())
        except ValueError as ex:
            assert "multiple components" in str(ex)
        else:
            raise AssertionError("Assembly silently treated as one plate")


def test_http_readiness_endpoint_saves_scope_and_reopens_it_with_the_result():
    class Quiet(server.Handler):
        def log_message(self, *args):
            pass
    with saved_result() as result, tempfile.TemporaryDirectory() as directory, \
         patch.object(server, "READINESS", Path(directory)), patch("app.llm.make_client", return_value=FakeModel(reads=[scope()])):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
        connection = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=5)
        try:
            connection.request("POST", "/api/assess", json.dumps({"result_id": result["id"]}), {"Content-Type": "application/json"})
            response = connection.getresponse(); artifact = json.loads(response.read())
            assert response.status == 200 and not artifact["report"]["accuracy_verified"]
            connection.request("GET", f"/api/result/{result['id']}")
            response = connection.getresponse(); reopened = json.loads(response.read())
            assert response.status == 200 and reopened["readiness"] == artifact
        finally:
            connection.close(); httpd.shutdown(); httpd.server_close(); thread.join(timeout=5)


def test_imported_live_receipts_preserve_sources_and_make_no_new_model_calls():
    from scripts.import_drawing_checks import import_case
    from drawing_fixtures import drawing_result
    from app import trace
    from app.agent.harness import AgentHarness
    from app.drawing.sheets import sha256
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory); source = root / "source.png"; source.write_bytes(png())
        pages = drawing_result()["pages"]; pages[0]["sha"] = sha256(source)
        assessed = VisualAssessment.model_validate(scope("part"))
        with trace.run("receipt", root / "trace") as tr:
            tr.event("llm", "call", provider="domo", model="TEST ONLY")
            tr.event("llm", "reply", tokens_in=100, tokens_out=20, cached=False)
        case = {"source": str(source), "sha256": sha256(source), "read_pages": pages, "trace": str(root / "trace"),
                "pages": [source_quality(png())], "harness": AgentHarness().snapshot(),
                "assessments": [assessed.model_dump()], "readiness": readiness([assessed], [source_quality(png())])}
        with patch.multiple(server, RESULTS=root / "web", RUNS=root / "runs", READINESS=root / "readiness"), \
             patch("app.llm.make_client", side_effect=AssertionError("Receipt import must not call a model")):
            imported = import_case(case)
            reopened = server.result_with_design(imported["id"])
            assert reopened["pages"] == pages and not reopened["accuracy_verified"]
            assert not reopened["readiness"]["report"]["accuracy_verified"]
            assert import_case(case, imported) == imported
            source.write_bytes(b"changed")
            try:
                import_case(case, imported)
            except ValueError as ex:
                assert "Source changed" in str(ex)
            else:
                raise AssertionError("Changed input accepted as the original extraction")
