"""Adversarial and recovery checks for the bounded ReAct runtime envelope."""
import json
import http.client
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import fakes  # noqa: F401
from fakes import GOOD, FakeBuild, FakeModel, failure, good
from test_agent_workers import fix
from test_graph_flows import Job
from test_drawing_web import saved_result, scripted

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app import trace
from app.agent.execution import cad_execution
from app.agent.graph import Deps, build_graph, new_job_state, repair_package
from app.agent.harness import AgentHarness, HarnessClient, HarnessPolicy, HarnessStopped, capabilities
from app.agent.orchestrator import Orchestrator
from app.agent.workers import ExtractionAgent, SolidWorksAgent
from app.drawing.model import Resolutions, reconcile
from app.llm.base import LLMError
from app.web import server


def refuses(fn):
    try:
        fn()
    except HarnessStopped as ex:
        return str(ex)
    raise AssertionError("Harness boundary was bypassed")


def workers(model=None, execution=None, policy=None, build=None):
    return Orchestrator(ExtractionAgent(model or FakeModel()), SolidWorksAgent(execution or FakeModel(), build=build),
                        harness=AgentHarness(policy))


def test_worker_actions_are_allowlisted_before_dispatch():
    supervisor = workers()
    with patch.object(supervisor.solidworks, "build_part", side_effect=AssertionError("Must not execute")):
        assert "not assigned" in refuses(lambda: supervisor._dispatch("extraction", "build_part", GOOD, Path("unused")))
        assert "not assigned" in refuses(lambda: supervisor._dispatch("unknown", "repair", []))
        assert "not assigned" in refuses(lambda: supervisor._dispatch("solidworks", "__getattribute__", "llm"))
    assert supervisor.harness.snapshot()["actions"] == 0


def test_public_react_trajectory_has_all_phases_without_copying_inputs():
    supervisor = workers()
    folder = Path(tempfile.mkdtemp())
    with trace.run("react", folder):
        supervisor.read_list(["PRIVATE DRAWING PAYLOAD"])
    state = supervisor.harness.snapshot()
    assert [h["phase"] for h in state["history"]] == ["decision", "action", "observation", "evaluation"]
    assert state["model_calls"] == 1 and state["tokens"] == 120
    assert "PRIVATE DRAWING PAYLOAD" not in json.dumps(state)
    assert all(e["worker"] == "extraction" for e in state["history"])


def test_model_budget_survives_approval_and_stops_repair_without_retrying():
    extraction, execution, build = FakeModel(), FakeModel(repairs=[fix()]), FakeBuild(failure())
    supervisor = workers(extraction, execution, HarnessPolicy(max_model_calls=1))
    job = Job(workers=supervisor, build=build)
    assert job.pause == "approve_list" and job.state["harness"]["model_calls"] == 1
    job.decide(action="approve")
    assert job.state["status"] == "needs_human" and job.pause is None
    assert "budget reached" in job.state["detail"] and not execution.calls
    assert job.state["harness"]["model_calls"] == 1 and len(build.lists) == 1


def test_failed_provider_attempts_are_bounded_across_new_graph_resume():
    memory, build = MemorySaver(), FakeBuild()
    model = FakeModel(reads=[LLMError("Fake permanent provider failure")])
    graph = build_graph(Deps(model, build, workers=workers(model, policy=HarnessPolicy(max_model_calls=1))), memory)
    directory = tempfile.mkdtemp()
    from fakes import drawing
    cfg = {"configurable": {"thread_id": "persisted"}, "recursion_limit": 80}
    out = graph.invoke(new_job_state("persisted", directory, drawing()), cfg)
    assert out["__interrupt__"][0].value["kind"] == "llm_failed"
    resumed_model = FakeModel()
    resumed = build_graph(Deps(resumed_model, build, workers=workers(resumed_model)), memory)
    resumed.invoke(Command(resume={"action": "retry"}), cfg)
    state = resumed.get_state(cfg).values
    assert state["status"] == "needs_human" and state["harness"]["model_calls"] == 1
    assert state["harness"]["policy"]["max_model_calls"] == 1 and not resumed_model.calls


def test_reusing_one_compiled_graph_does_not_share_budgets_between_jobs():
    from fakes import drawing
    model, build = FakeModel(), FakeBuild()
    graph = build_graph(Deps(model, build, workers=workers(model, policy=HarnessPolicy(max_model_calls=1))), MemorySaver())
    for jid in ("first", "second"):
        cfg = {"configurable": {"thread_id": jid}, "recursion_limit": 80}
        out = graph.invoke(new_job_state(jid, tempfile.mkdtemp(), drawing()), cfg)
        assert out["__interrupt__"][0].value["kind"] == "approve_list"
        assert graph.get_state(cfg).values["harness"]["model_calls"] == 1
    assert model.count("reader") == 2


def test_token_and_active_time_budgets_stop_before_another_model_call():
    model = FakeModel()
    harness = AgentHarness(HarnessPolicy(max_tokens=100))
    with harness.session():
        client = HarnessClient(model)
        client.generate_json("S", ["x"])
        assert "token budget" in refuses(lambda: client.generate_json("S", ["x"]))
    assert len(model.calls) == 1
    ticks = iter([0.0, 2.0])
    timed = AgentHarness(HarnessPolicy(max_model_seconds=1), clock=lambda: next(ticks))
    with timed.session():
        HarnessClient(model).generate_json("S", ["x"])
        assert "time budget" in refuses(lambda: HarnessClient(model).generate_json("S", ["x"]))
    assert timed.snapshot()["model_seconds"] == 2


def test_action_budget_and_history_are_bounded():
    supervisor = workers(policy=HarnessPolicy(max_actions=2, history_limit=5))
    supervisor.read_list(["first"]); supervisor.read_list(["second"])
    assert "action budget" in refuses(lambda: supervisor.read_list(["third"]))
    state = supervisor.harness.snapshot()
    assert state["actions"] == state["model_calls"] == 2 and len(state["history"]) == 5


def test_repeated_repair_stops_and_retains_original_approval():
    extraction, execution = FakeModel(), FakeModel(repairs=[fix()])
    job = Job(workers=workers(extraction, execution), build=FakeBuild(failure()), max_repairs=5)
    job.decide(action="approve")
    original = job.state["approved_hash"]
    job.decide(action="reject", message="not correct")
    assert job.pause == "approve_fix"
    job.decide(action="reject", message="same answer again")
    assert job.state["status"] == "needs_human" and "same repair" in job.state["detail"]
    assert job.state["approved_hash"] == original and len(job.build.lists) == 1
    assert job.state["repairs"]["F2"] == 3


def test_repair_receives_recent_measured_observations_and_public_decision():
    execution = FakeModel(repairs=[fix()])
    job = Job(workers=workers(execution=execution), build=FakeBuild(failure()))
    job.decide(action="approve")
    package = json.loads(execution.calls[0][1][0])
    assert len(package["recent_observations"]) <= 6
    assert any(h.get("failure", {}).get("item_id") == "F2" for h in package["recent_observations"])
    assert any(h["task"] == "propose_repair" and h["next"] == "human_approval" for h in job.state["harness"]["history"])
    assert job.pause == "approve_fix" and len(job.build.lists) == 1


def test_invalid_preapproved_handoff_and_incomplete_cad_report_are_blocked():
    supervisor = workers()
    with patch.object(supervisor.solidworks, "plan", return_value={"status": "ready_for_approval", "draft": GOOD, "approved": True}):
        assert "preapproved" in refuses(lambda: supervisor._dispatch("solidworks", "plan", None, []))
    with patch.object(supervisor.solidworks, "build_part", return_value={"report": {}}):
        assert "incomplete report" in refuses(lambda: supervisor.build_part(GOOD, Path("unused")))
    with patch.object(supervisor.solidworks, "build_part", return_value={"report": {"ok": False, "items": [], "failure": {}}}):
        assert "incomplete failure" in refuses(lambda: supervisor.build_part(GOOD, Path("unused")))
    with patch.object(supervisor.solidworks, "build_part", return_value={"report": {"ok": True, "items": []}, "finish": {"ok": True, "files": {}}}):
        assert "without export files" in refuses(lambda: supervisor.build_part(GOOD, Path("unused")))
    assert "verified outputs" in refuses(lambda: supervisor.harness.validate_handoff("build_assembly", {"status": "awaiting_review"}))


def test_corrupt_budget_stops_at_a_human_boundary_and_never_builds():
    job = Job(draft=good())
    job.graph.update_state(job.cfg, {"harness": {"version": 999}})
    job.decide(action="approve")
    assert job.state["status"] == "needs_human" and not job.build.lists


def test_invalid_usage_is_not_silently_counted_or_cached():
    class BadUsage(FakeModel):
        def generate_json(self, system, parts):
            result = super().generate_json(system, parts)
            result.usage = {"prompt_tokens": -1}
            return result

        def discard(self, system, parts):
            self.discarded = True

    model = BadUsage()
    supervisor = workers(model)
    assert "usage counters" in refuses(lambda: supervisor.read_list(["x"]))
    assert model.discarded and supervisor.harness.snapshot()["model_calls"] == 1


def test_one_native_writer_and_intent_before_any_side_effect():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        lock = root / "cad.lock"
        with cad_execution(root / "part", "part", "approved", lock_path=lock):
            assert json.loads((root / "part/harness/native-intent.json").read_text())["status"] == "running"
            assert "already reserved" in refuses(lambda: _native_action(root / "assembly", lock))
        assert not lock.exists()
        assert json.loads((root / "part/harness/native-intent.json").read_text())["status"] == "completed"


def _native_action(job, lock):
    with cad_execution(job, "assembly", "approved", lock_path=lock):
        raise AssertionError("Concurrent execution entered")


def test_uncertain_native_action_cannot_be_automatically_replayed():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        lock = root / "cad.lock"
        try:
            with cad_execution(root / "job", "part", "approved", lock_path=lock):
                raise RuntimeError("connection lost after CAD action")
        except RuntimeError:
            pass
        assert not lock.exists()
        assert "reconciliation" in refuses(lambda: _native_action(root / "job", lock))


def test_stale_native_lock_is_not_stolen():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        lock = root / "cad.lock"
        lock.write_text('{"owner":"existing"}')
        assert "already reserved" in refuses(lambda: _native_action(root / "job", lock))
        assert json.loads(lock.read_text())["owner"] == "existing"


def test_browser_saves_harness_budget_with_immutable_plan():
    with saved_result() as result:
        artifact = server.plan_result(result["id"], reconcile(result).source_hash, Resolutions(), client=scripted())
        assert artifact["harness"]["model_calls"] == 2
        assert artifact["harness"]["tokens"] == 240
        assert artifact["harness"]["actions"] == 2
        assert server.result_with_design(result["id"])["design"]["harness"] == artifact["harness"]
    public = capabilities()
    assert public["approval_required"] and "build_part" not in public["workers"]["extraction"]


def test_harness_http_endpoint_exposes_limits_and_model_settings_without_credentials():
    class Quiet(server.Handler):
        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=5)
    try:
        connection.request("GET", "/api/harness")
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 200 and payload == capabilities()
        assert payload["model"]["reasoning_effort"] == "high" and payload["mode"] == "bounded_react"
        assert "token" not in json.dumps(payload).lower().replace("max_tokens", "")
    finally:
        connection.close()
        httpd.shutdown(); httpd.server_close(); thread.join(timeout=5)
