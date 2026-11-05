"""Offline checks of the JOB GRAPH: every human pause, decision and guard, with a fake model and a fake SolidWorks build.

  python tests/test_graph_flows.py   (needs the project's requirements: langgraph)
"""
import copy
import tempfile
from pathlib import Path

import fakes  # noqa: F401
from fakes import GOOD, FakeBuild, FakeModel, drawing, failure, good, transient

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.agent.graph import Deps, build_graph, new_job_state


class Job:
    """One job on a fresh graph: start, decide, look."""

    def __init__(self, model=None, build=None, draft=None, **deps):
        self.model, self.build = model or FakeModel(), build or FakeBuild()
        self.graph = build_graph(Deps(self.model, self.build, llm_retry_delay=0, **deps), MemorySaver())
        self.dir = Path(tempfile.mkdtemp(prefix="job-"))
        self.cfg = {"configurable": {"thread_id": "t"}, "recursion_limit": 80}
        self.out = self.graph.invoke(new_job_state("t", str(self.dir), "" if draft else drawing(), draft), self.cfg)

    def decide(self, **decision):
        self.out = self.graph.invoke(Command(resume=decision), self.cfg)
        return self

    @property
    def pause(self) -> str | None:
        pending = self.out.get("__interrupt__")
        return pending[0].value["kind"] if pending else None

    @property
    def payload(self) -> dict:
        return self.out["__interrupt__"][0].value

    @property
    def state(self) -> dict:
        return self.graph.get_state(self.cfg).values


def test_happy_path_read_approve_build_review_accept():
    job = Job()
    assert job.pause == "approve_list" and job.payload["reasons"] == []
    job.decide(action="approve")
    assert job.pause == "review" and len(job.build.lists) == 1
    assert (job.dir / "approved_v1.json").is_file()
    job.decide(action="accept")
    assert job.pause is None and job.state["status"] == "done"


def test_correct_rereads_with_the_note_and_edit_replaces_the_list():
    job = Job(model=FakeModel(reads=[GOOD, good(part_name="fixed")]))
    job.decide(action="correct", message="the hole is Ø9")
    assert job.model.count("reader") == 2 and "the hole is Ø9" in job.model.calls[-1][1][-1]
    assert job.pause == "approve_list" and job.payload["list"]["part_name"] == "fixed"
    job.decide(action="edit", list=good(part_name="edited by hand"))
    assert job.pause == "approve_list" and job.payload["list"]["part_name"] == "edited by hand"
    job.decide(action="approve")
    assert job.build.lists[0]["part_name"] == "edited by hand"


def test_approve_is_refused_while_reasons_remain():
    job = Job(model=FakeModel(reads=[good(projection="unknown")]))
    assert job.payload["reasons"]
    job.decide(action="approve")
    assert job.pause == "approve_list" and not job.build.lists      # nothing was built


def test_accepted_gap_is_set_only_by_the_human():
    job = Job(model=FakeModel(reads=[good(unsupported=[{"id": "U1", "text": "knurl"}], accepted_gaps=["U1"])]))
    assert job.payload["list"]["accepted_gaps"] == []                  # the reader's own acceptance was cleared
    job.decide(action="approve", accepted_gaps=["U1"])
    assert job.pause == "review" and job.build.lists[0]["accepted_gaps"] == ["U1"]


def test_reader_cannot_set_human_only_flags_or_expected_final():
    hole = copy.deepcopy(GOOD["items"][1])
    hole["params"].update(depth=5, floor_open=True)
    job = Job(model=FakeModel(reads=[good(items=[GOOD["items"][0], hole], expected_final={"bbox_mm": [[0, 0, 0], [120, 80, 10]]})]))
    lst = job.payload["list"]
    assert "floor_open" not in lst["items"][1]["params"] and lst["expected_final"] is None


def test_failed_build_repair_approve_fix_rebuild():
    fix = {"status": "suggestion", "item_id": "F2", "params": {**GOOD["items"][1]["params"], "diameter": 9}, "reason": "bigger"}
    job = Job(model=FakeModel(repairs=[fix]), build=FakeBuild(failure(), "ok"))
    job.decide(action="approve")
    assert job.pause == "approve_fix" and job.payload["diff"] == [{"path": "diameter", "old": 8, "new": 9}]
    job.decide(action="approve")
    assert job.pause == "review" and job.build.lists[1]["items"][1]["params"]["diameter"] == 9
    assert job.state["versions"] == 2 and (job.dir / "approved_v2.json").is_file()


def test_reject_fix_asks_again_with_the_feedback_then_limit():
    fix = {"status": "suggestion", "item_id": "F2", "params": {**GOOD["items"][1]["params"], "diameter": 9}, "reason": "bigger"}
    job = Job(model=FakeModel(repairs=[fix]), build=FakeBuild(failure()))
    job.decide(action="approve").decide(action="reject", message="the drawing says Ø8")
    assert job.pause == "approve_fix" and "the drawing says Ø8" in job.model.calls[-1][1][0]
    job.decide(action="reject", message="still no")
    assert job.pause is None and job.state["status"] == "needs_human"  # max_repairs = 2 per item


def test_guard_rejects_a_suggestion_for_another_item():
    bad = {"status": "suggestion", "item_id": "F1", "params": GOOD["items"][0]["params"], "reason": "x"}
    job = Job(model=FakeModel(repairs=[bad]), build=FakeBuild(failure()))
    job.decide(action="approve")
    assert job.pause is None and job.state["status"] == "needs_human" and job.model.count("repair") == 2


def test_environment_failure_never_goes_to_the_model():
    job = Job(build=FakeBuild(failure(kind="environment", message="SolidWorks is not running")))
    job.decide(action="approve")
    assert job.state["status"] == "needs_human" and job.model.count("repair") == 0


def test_temporary_model_error_is_retried_once_then_the_human_decides():
    job = Job(model=FakeModel(reads=[transient(), GOOD]))
    assert job.pause == "approve_list" and job.model.count("reader") == 2   # the one automatic retry worked
    job = Job(model=FakeModel(reads=[transient(), transient(), GOOD]))
    assert job.pause == "llm_failed" and job.payload["retried"] is True
    job.decide(action="retry")
    assert job.pause == "approve_list"


def test_read_limit_and_saved_list_start():
    job = Job(model=FakeModel(reads=[GOOD]))
    for note in ("one", "two", "three"):
        job.decide(action="correct", message=note)
    assert job.state["status"] == "needs_human" and job.model.count("reader") == 3   # max_reads = 3
    job = Job(draft=GOOD)
    assert job.pause == "approve_list" and job.model.calls == []      # a saved list makes no model call


def test_unreadable_drawing_goes_to_a_human_without_a_model_call():
    model = FakeModel()
    graph = build_graph(Deps(model, FakeBuild(), llm_retry_delay=0), MemorySaver())
    bad = Path(tempfile.mkdtemp()) / "drawing.png"
    bad.write_bytes(b"this is not a picture")
    cfg = {"configurable": {"thread_id": "t"}, "recursion_limit": 80}
    graph.invoke(new_job_state("t", str(bad.parent), str(bad)), cfg)
    state = graph.get_state(cfg).values
    assert state["status"] == "needs_human" and "cannot be read" in state["detail"] and model.calls == []


def test_abort_at_every_pause():
    assert Job().decide(action="abort").state["status"] == "aborted"
    assert Job().decide(action="approve").decide(action="abort").state["status"] == "aborted"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
