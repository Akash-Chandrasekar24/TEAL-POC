"""Saved sheets -> BOM -> immutable reviewed assembly download. Never starts CAD."""
import base64
import copy
import http.client
import json
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import fakes  # noqa: F401
from fakes import FakeModel
from assembly_fixtures import ready_plan
from app.assembly.model import digest
from app.web import server
from test_drawing_web import saved_result


def model():
    return FakeModel(reads=[{"rows": [{"item": "1", "part_number": "BLOCK-10", "description": "Block", "quantity": 2,
                                     "kind": "part", "text": "1 BLOCK-10 Block 2"}], "unit": "mm"}])


def test_saved_assembly_review_retains_raw_extraction_and_downloads_reviewed_snapshot():
    with saved_result() as source, tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        with patch.multiple(server, ASSEMBLIES=root / "drafts", MODELS=root / "models"):
            raw = server.assembly_read_result(source["id"], client=model())
            assert raw["status"] == "needs_information" and not raw["approved"]
            assert raw["plan"]["bom"][0]["quantity"] == 2 and not raw["plan"]["occurrences"]
            plan = ready_plan(server.MODELS); plan.source_hash = digest(source)
            reviewed = server.assembly_review_result(source["id"], raw["id"], plan.model_dump())
            assert reviewed["status"] == "ready_for_approval" and not reviewed["approved"]
            assert reviewed["extracted"] == raw["plan"] and reviewed["id"] != raw["id"]
            assert server.assembly_artifact(source["id"], raw["id"])["plan"] == raw["plan"]
            server.MODELS.joinpath(plan.models[0].sha256 + ".SLDPRT").unlink()
            assert server.assembly_artifact(source["id"], reviewed["id"])["status"] == "needs_information"


def test_assembly_review_refuses_changed_sources_and_cross_drawing_plans():
    with saved_result() as source, tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        with patch.multiple(server, ASSEMBLIES=root / "drafts", MODELS=root / "models"):
            raw = server.assembly_read_result(source["id"], client=model())
            edited = copy.deepcopy(raw["plan"]); edited["source_hash"] = "different source"
            try:
                server.assembly_review_result(source["id"], raw["id"], edited)
            except ValueError:
                pass
            else:
                raise AssertionError("Cross-drawing review accepted")
            source["pages"][0]["source"] = "changed.png"
            (server.RESULTS / f"{source['id']}.json").write_text(json.dumps(source), encoding="utf-8")
            try:
                server.assembly_artifact(source["id"], raw["id"])
            except ValueError:
                pass
            else:
                raise AssertionError("Stale drawing source accepted")


def test_assembly_http_upload_review_and_snapshot_routes():
    with saved_result() as source, tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        with patch.multiple(server, ASSEMBLIES=root / "drafts", MODELS=root / "models"):
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
            connection = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=10)
            def post(path, body):
                connection.request("POST", path, json.dumps(body), {"Content-Type": "application/json"})
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            try:
                with patch("app.llm.make_client", return_value=model()):
                    code, raw = post("/api/assembly/read", {"result_id": source["id"]})
                assert code == 200 and raw["status"] == "needs_information", raw
                binary = bytes.fromhex("d0cf11e0a1b11ae1") + b"TEST ONLY"
                code, uploaded = post("/api/assembly/model", {"name": "../../pump.SLDPRT", "data": base64.b64encode(binary).decode()})
                assert code == 200 and uploaded["source_name"] == "pump.SLDPRT"
                assert len(list(server.MODELS.glob("*.SLDPRT"))) == 1
                code, _ = post("/api/assembly/model", {"name": "model.step", "data": base64.b64encode(binary).decode()})
                assert code == 400
                plan = ready_plan(server.MODELS); plan.source_hash = digest(source)
                code, reviewed = post("/api/assembly/review", {"result_id": source["id"], "attempt": raw["id"], "plan": plan.model_dump()})
                assert code == 200 and reviewed["status"] == "ready_for_approval", reviewed
                connection.request("GET", f"/api/assembly/{source['id']}/{reviewed['id']}/plan")
                response = connection.getresponse()
                assert response.status == 200 and json.loads(response.read()) == plan.model_dump(mode="json")
                connection.request("GET", "/assembly.html")
                response = connection.getresponse()
                assert response.status == 200 and b"Assembly planner" in response.read()
            finally:
                connection.close(); httpd.shutdown(); httpd.server_close(); thread.join(timeout=5)
