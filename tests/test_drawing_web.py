"""Saved evidence -> planning API -> downloaded list -> existing approval pause."""
import copy
import http.client
import json
import tempfile
import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import fakes  # noqa: F401
from fakes import FakeModel
from drawing_fixtures import drawing_result, interpretations, png, proposal
from test_graph_flows import Job

from app.cache import default_cache
from app.drawing.model import Resolutions, reconcile
from app.web import server


@contextmanager
def saved_result():
    with tempfile.TemporaryDirectory(prefix="drawing-web-") as directory:
        root = Path(directory)
        results = root / "web"
        results.mkdir()
        result = drawing_result()
        result["pages"][0]["sha"] = default_cache().put_blob(png())
        (results / f"{result['id']}.json").write_text(json.dumps(result), encoding="utf-8")
        with patch.multiple(server, RESULTS=results, PLANS=root / "plans", RUNS=root / "runs"):
            yield result


def scripted():
    return FakeModel(reads=[interpretations()[0].model_dump(), proposal().model_dump()])


def test_saved_plan_reopens_downloads_and_still_requires_job_approval():
    with saved_result() as result:
        source = reconcile(result).source_hash
        llm = scripted()
        artifact = server.plan_result(result["id"], source, Resolutions(), client=llm)
        assert artifact["plan"]["status"] == "ready_for_approval", artifact
        assert artifact["plan"]["calls"] == 2
        assert artifact["tokens"]["prompt"] == 200
        reopened = server.result_with_design(result["id"])
        assert reopened["design"] == artifact
        draft = server.downloadable_list(result["id"])
        job = Job(draft=draft)
        assert job.pause == "approve_list" and job.build.lists == [] and job.model.calls == []
        job.decide(action="approve")
        assert job.pause == "review" and len(job.build.lists) == 1
        assert (server.PLANS / result["id"] / f"{artifact['id']}.json").is_file()
        # Raw readings are immutable; decisions live in a separate artifact.
        raw = json.loads((server.RESULTS / f"{result['id']}.json").read_text(encoding="utf-8"))
        assert raw == result


def test_changed_readings_refuse_stale_reviews_and_unresolved_reads_make_no_model_calls():
    with saved_result() as result:
        llm = scripted()
        try:
            server.plan_result(result["id"], "stale", Resolutions(), client=llm)
        except ValueError as ex:
            assert "changed" in str(ex)
        else:
            raise AssertionError("stale correction accepted")
        assert llm.calls == []
        result["pages"][0]["read"]["dims"][0]["single_source"] = True
        (server.RESULTS / f"{result['id']}.json").write_text(json.dumps(result), encoding="utf-8")
        artifact = server.plan_result(result["id"], reconcile(result).source_hash, Resolutions(), client=llm)
        assert artifact["plan"]["status"] == "needs_review" and llm.calls == []
        try:
            server.downloadable_list(result["id"])
        except ValueError:
            pass
        else:
            raise AssertionError("blocked plan downloaded")


def test_invalid_model_response_is_saved_as_a_refusal_not_a_build_list():
    with saved_result() as result:
        artifact = server.plan_result(result["id"], reconcile(result).source_hash, Resolutions(),
                                      client=FakeModel(reads=[{"features": "invalid"}]))
        assert artifact["plan"]["status"] == "needs_information"
        assert artifact["plan"]["draft"] is None and artifact["plan"]["approved"] is False
        assert server.result_with_design(result["id"])["design"]["id"] == artifact["id"]


def test_download_is_pinned_to_the_reviewed_attempt_after_a_later_plan():
    with saved_result() as result:
        source = reconcile(result).source_hash
        first = server.plan_result(result["id"], source, Resolutions(), client=scripted())
        corrected = Resolutions.model_validate({"dimensions": [{"id": "P1.d1", "text": "125"}]})
        second = server.plan_result(result["id"], source, corrected, client=scripted())
        assert first["id"] != second["id"]
        old = server.downloadable_list(result["id"], first["id"])
        new = server.downloadable_list(result["id"], second["id"])
        assert old["items"][0]["params"]["profile"]["width"] == 120
        assert new["items"][0]["params"]["profile"]["width"] == 125


def test_provider_failure_keeps_a_trace_link_and_does_not_create_a_list():
    from app.llm.base import LLMError
    with saved_result() as result:
        try:
            server.plan_result(result["id"], reconcile(result).source_hash, Resolutions(),
                               client=FakeModel(reads=[LLMError("Fake provider failure")]))
        except LLMError as ex:
            assert (server.RUNS / ex.run_id / "trace.jsonl").is_file()
        else:
            raise AssertionError("provider failure swallowed")
        assert not (server.PLANS / result["id"] / "latest.json").exists()


def test_http_plan_and_download_endpoints_with_local_fake_model():
    class QuietHandler(server.Handler):
        def log_message(self, *args):
            pass

    with saved_result() as result, patch("app.llm.make_client", return_value=scripted()):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        connection = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=30)
        try:
            body = json.dumps({"result_id": result["id"], "source_hash": reconcile(result).source_hash, "resolutions": {}})
            connection.request("POST", "/api/plan", body, {"Content-Type": "application/json"})
            response = connection.getresponse()
            artifact = json.loads(response.read())
            assert response.status == 200 and artifact["plan"]["status"] == "ready_for_approval", artifact
            connection.request("GET", f"/api/design/{result['id']}/list")
            response = connection.getresponse()
            draft = json.loads(response.read())
            assert response.status == 200 and len(draft["items"]) == 3
            connection.request("POST", "/api/plan", '[]', {"Content-Type": "application/json"})
            response = connection.getresponse()
            assert response.status == 400
            response.read()
            # A currently running plan must not be overwritten by a second request.
            server._PLAN_LOCK.acquire()
            try:
                connection.request("POST", "/api/plan", body, {"Content-Type": "application/json"})
                response = connection.getresponse()
                assert response.status == 409
                response.read()
            finally:
                server._PLAN_LOCK.release()
        finally:
            connection.close()
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)
