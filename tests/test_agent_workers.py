"""Worker assignment regressions: existing prompts, approvals and CAD behavior."""
import asyncio
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import fakes  # noqa: F401
from fakes import GOOD, FakeBuild, FakeModel, failure, good
from drawing_fixtures import interpretations, model, png, proposal
from assembly_fixtures import FakeAssembly, ready_plan
from test_drawing_web import saved_result
from test_graph_flows import Job

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app import config, trace
from app.agent.graph import Deps, build_graph, new_job_state
from app.agent.orchestrator import Orchestrator
from app.agent.prompts import reader_system, repair_system
from app.agent.workers import ExtractionAgent, SolidWorksAgent
from app.assembly.jobs import AssemblyJob
from app.drawing.model import Resolutions, reconcile
from app.drawing.planner import plan
from app.llm.base import LLMError
from app.llm.domo import DomoClient
from app.web import server


def fix():
    return {"status": "suggestion", "item_id": "F2", "params": {**GOOD["items"][1]["params"], "diameter": 9}, "reason": "bigger"}


def supervisor(extraction, execution, build=None):
    return Orchestrator(ExtractionAgent(extraction), SolidWorksAgent(execution, build=build))


def test_split_planning_matches_existing_output_and_model_call_count():
    observed, proposed = interpretations()[0].model_dump(), proposal().model_dump()
    expected = plan(FakeModel(reads=[observed, proposed]), model(), {1: png()})
    extraction, execution = FakeModel(reads=[observed]), FakeModel(reads=[proposed])
    folder = Path(tempfile.mkdtemp())
    with trace.run("workers", folder):
        actual = supervisor(extraction, execution).plan(model(), {1: png()})
    assert actual == expected and actual["approved"] is False
    assert len(extraction.calls) == len(execution.calls) == 1
    # Only the extraction worker receives drawing pixels; planning receives text.
    assert any(not isinstance(p, str) for p in extraction.calls[0][1])
    assert all(isinstance(p, str) for p in execution.calls[0][1])
    events = [json.loads(line) for line in (folder / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    tasks = [(e["worker"], e["task"]) for e in events if e["event"] == "worker task done"]
    assert tasks == [("extraction", "interpret"), ("solidworks", "plan")]
    assert not hasattr(ExtractionAgent, "build_part") and not hasattr(ExtractionAgent, "build_assembly")


def test_graph_uses_distinct_workers_and_keeps_both_approval_pauses():
    extraction, execution, unused = FakeModel(), FakeModel(repairs=[fix()]), FakeModel()
    build = FakeBuild(failure(), "ok")
    job = Job(model=unused, build=build, workers=supervisor(extraction, execution))
    assert job.pause == "approve_list" and not build.lists and not execution.calls
    assert job.state["worker_status"]["extraction"] == {"task": "read", "status": "awaiting_approval"}
    job.decide(action="approve")
    assert job.pause == "approve_fix" and len(build.lists) == 1
    assert extraction.count("reader") == 1 and extraction.count("repair") == 0
    assert execution.count("reader") == 0 and execution.count("repair") == 1
    job.decide(action="approve")
    assert job.pause == "review" and len(build.lists) == 2 and job.state["versions"] == 2
    assert build.lists[1]["items"][1]["params"]["diameter"] == 9
    assert not unused.calls
    job.decide(action="accept")
    assert job.state["status"] == "done"


def test_invalid_replies_are_discarded_from_the_responsible_worker_only():
    class Recording(FakeModel):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.discarded = []

        def discard(self, system, parts):
            self.discarded.append(system)

    extraction = Recording(reads=[{"items": "invalid"}, GOOD])
    execution = Recording(repairs=[{**fix(), "item_id": "F1"}, fix()])
    build = FakeBuild(failure(), "ok")
    job = Job(workers=supervisor(extraction, execution, build), build=build)
    assert extraction.discarded == [reader_system()] and not execution.discarded
    job.decide(action="approve")
    assert job.pause == "approve_fix"
    assert execution.discarded == [repair_system()] and extraction.discarded == [reader_system()]
    assert job.state["repairs"]["F2"] == 2


def test_domo_workers_use_separate_clients_and_the_same_configured_model():
    with patch.multiple(config, LLM_PROVIDER="domo", DOMO_INSTANCE="test-instance"), \
         patch.dict(os.environ, {"DOMO_DEVELOPER_TOKEN": "test-token"}):
        workers = Orchestrator.with_provider(cache=False)
        with patch.object(DomoClient, "generate_json", autospec=True) as generate:
            generate.return_value = SimpleNamespace(text="{}", data={}, model=config.DOMO_MODEL, seconds=0, usage={})
            workers.read_list(["drawing input"])
            workers.repair(["failure package"])
        extraction, execution = workers.extraction.llm.inner, workers.solidworks.llm.inner
        assert isinstance(extraction, DomoClient) and isinstance(execution, DomoClient)
        assert extraction is not execution and extraction.model == execution.model == config.DOMO_MODEL
        assert extraction.reasoning_effort == "medium" and execution.reasoning_effort == "high"
        assert workers.extraction.llm.cache_settings == {"reasoning_effort": "medium"}
        assert workers.solidworks.llm.cache_settings == {"reasoning_effort": "high"}
        assert [c.args[1] for c in generate.call_args_list] == [reader_system(), repair_system()]


def test_unresolved_evidence_still_stops_before_any_client_is_created():
    unresolved = model().model_copy(update={"issues": ["Confirm the missing dimension"]})
    with patch("app.llm.make_client", side_effect=AssertionError("Unexpected model client")):
        report = Orchestrator().plan(unresolved, {})
    assert report["status"] == "needs_review" and report["calls"] == 0 and report["draft"] is None


def test_worker_default_ignores_inherited_legacy_provider_but_keeps_explicit_override():
    with patch.multiple(config, LLM_PROVIDER="gemini", WORKER_LLM_PROVIDER="domo"):
        workers = Orchestrator.with_provider()
        assert workers.extraction.identity()["provider"] == workers.solidworks.identity()["provider"] == "domo"
        explicit = Orchestrator.with_provider("gemini")
        assert explicit.extraction.identity()["provider"] == explicit.solidworks.identity()["provider"] == "gemini"


def test_saved_checkpoint_without_worker_metadata_resumes_with_existing_nodes():
    memory, build = MemorySaver(), FakeBuild()
    initial = build_graph(Deps(FakeModel(), build), memory)
    folder = tempfile.mkdtemp()
    cfg = {"configurable": {"thread_id": "existing"}, "recursion_limit": 80}
    paused = initial.invoke(new_job_state("existing", folder, "", good()), cfg)
    assert paused["__interrupt__"][0].value["kind"] == "approve_list"
    assert "worker_status" not in initial.get_state(cfg).values
    extraction, execution = FakeModel(), FakeModel()
    resumed = build_graph(Deps(FakeModel(), build, workers=supervisor(extraction, execution, build)), memory)
    out = resumed.invoke(Command(resume={"action": "approve"}), cfg)
    assert out["__interrupt__"][0].value["kind"] == "review" and len(build.lists) == 1
    assert not extraction.calls and not execution.calls
    assert resumed.get_state(cfg).values["worker_status"]["solidworks"]["status"] == "awaiting_review"
    resumed.invoke(Command(resume={"action": "accept"}), cfg)
    assert resumed.get_state(cfg).values["status"] == "done"


def test_browser_planning_uses_two_clients_and_preserves_combined_token_totals():
    extraction = FakeModel(reads=[interpretations()[0].model_dump()])
    execution = FakeModel(reads=[proposal().model_dump()])
    with saved_result() as result, patch("app.llm.make_client", side_effect=[extraction, execution]) as factory:
        artifact = server.plan_result(result["id"], reconcile(result).source_hash, Resolutions(), llm_name="domo")
    assert [c.args for c in factory.call_args_list] == [("domo",), ("domo",)]
    assert len(extraction.calls) == len(execution.calls) == 1
    assert artifact["plan"]["calls"] == 2 and artifact["tokens"]["prompt"] == 200
    assert artifact["plan"]["status"] == "ready_for_approval"


def test_worker_failures_are_logged_and_follow_existing_retry_pause():
    folder = Path(tempfile.mkdtemp())
    extraction, execution = FakeModel(reads=[LLMError("provider unavailable")]), FakeModel()
    with trace.run("failure", folder):
        job = Job(workers=supervisor(extraction, execution))
    assert job.pause == "llm_failed" and not execution.calls and not job.build.lists
    assert job.state["worker_status"]["extraction"]["status"] == "llm_failed"
    events = [json.loads(line) for line in (folder / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(e["event"] == "worker task failed" and e["worker"] == "extraction" for e in events)


def test_assembly_cli_routes_execution_without_creating_model_clients_or_changing_outputs():
    from scripts import run_assembly as cli

    class Session(FakeAssembly):
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def ensure_ready(self):
            return SimpleNamespace(ok=True)

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        assets, session = root / "models", Session()
        job = AssemblyJob(root / "job", assets)
        job.create(ready_plan(assets)); job.approve()
        with patch.object(cli, "ASSETS", assets), \
             patch("app.mcp_client.solidworks.SolidWorksClient", return_value=session), \
             patch("app.llm.make_client", side_effect=AssertionError("Approved execution needs no LLM")):
            result = asyncio.run(cli.execute(job))
        assert result["status"] == "awaiting_review", result
        assert Path(result["report"]["files"]["assembly"]).is_file()
        attempt = result["attempts"][-1]
        assert (job.folder / "traces" / attempt / "trace.jsonl").is_file()
        assert job.accept()["status"] == "accepted"
