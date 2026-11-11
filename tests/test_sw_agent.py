"""The SolidWorks agent: back and forth while building. A step that goes wrong is handed to the agent with what SolidWorks made; the agent
calls tools (features, describe, measure, look), reads each result, and the step is reworked, kept or taken out. No model / SolidWorks here."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

import fakes  # noqa: F401
from app.agent.sw_agent import SolidWorksAgent
from app.features.base import FeatureResult
from app.llm.base import LLMError, LLMResponse
from app.menu import parse_reply
from app.runner import runner as runner_mod
from app.runner.runner import Prepared, Runner
from app.verify.step_verifier import StepVerdict

GOOD_HOLE = {"face": "front", "diameter": 8, "depth": "through", "material_length": 10,
             "positions": [{"x": 0, "x_from": "centre", "y": 0, "y_from": "centre"}]}


class Says:
    """A model that answers with the next scripted tool call and remembers what it was shown."""

    def __init__(self, *replies):
        self.replies, self.seen = list(replies), []

    def generate_json(self, system, parts):
        self.seen.append(parts[-1])
        if not self.replies:
            raise LLMError("no more replies")
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return LLMResponse(json.dumps(r), r, "fake", 0, {})


def test_the_agent_reads_each_result_and_stops_when_a_feature_call_comes_back_ok():
    llm = Says({"tool": "describe", "args": {"feature": "hole"}, "why": "read the card"},
               {"tool": "hole", "args": {**GOOD_HOLE, "diameter": -1}, "why": "first try"},
               {"tool": "hole", "args": GOOD_HOLE, "why": "fixed the diameter"})
    calls = []

    async def run(tool, args):
        calls.append(tool)
        if tool == "describe":
            return {"status": "ok", "card": "### hole"}
        return {"status": "ok" if args["diameter"] > 0 else "refused", "feature_call": True, "message": "diameter must be positive"}

    end = asyncio.run(SolidWorksAgent(llm, b"png", "- hole: a hole").fix({"item": {"id": "F2"}}, run))
    assert end["result"]["status"] == "ok" and calls == ["describe", "hole", "hole"]
    assert "diameter must be positive" in llm.seen[2]                         # the SolidWorks answer went back to the model


def test_a_model_failure_or_no_answer_gives_up_without_stopping_the_build():
    async def run(tool, args):
        return {"status": "refused", "feature_call": True, "message": "no"}
    end = asyncio.run(SolidWorksAgent(Says(LLMError("503")), None, "").fix({"item": {"id": "F2"}}, run))
    assert end["tool"] == "skip" and "model call failed" in end["args"]["reason"]
    end = asyncio.run(SolidWorksAgent(Says(*[{"tool": "hole", "args": {}}] * 3), None, "", fix_turns=3).fix({"item": {"id": "F2"}}, run))
    assert end["tool"] == "skip" and "3 tool calls" in end["args"]["reason"]


def _runner(agent, try_feature):
    calls = []

    async def call(tool, args=None, **kw):
        calls.append((tool, args))
        return SimpleNamespace(ok=True, text="ok", json=lambda: {"count": 9})

    r = Runner.__new__(Runner)
    r.sw, r.log, r.expected_docs, r.agent, r.look_dir = SimpleNamespace(call=call, tool_names=["ext_feature_count", "ext_delete_after"]), None, (), agent, None

    async def opened(unit, report):
        return "Part1"

    async def final(verifier, report):
        return {}

    async def measure(ctx):
        return {"bodies": 1}
    async def usable(ctx, mark):
        return True
    r._open_part, r._final, r._measure, r._try_feature, r._usable = opened, final, measure, try_feature, usable
    return r, calls


def _list():
    return parse_reply({"list_version": "1", "unit": "mm", "projection": "third", "items": [
        {"id": "F1", "feature": "extrude_profile_boss", "params": {"profile": {"shape": "rectangle", "width": 40, "height": 30}, "depth": 10}},
        {"id": "F2", "feature": "hole", "params": GOOD_HOLE}]})


def _steps(bl, second_ok):
    def step(item, ok):
        class F:
            async def apply(self, ctx):
                return FeatureResult(item.id, verdicts=[StepVerdict(item.id, ok, "ok" if ok else "wrong_delta", "volume change -100 mm3, expected -502 mm3")])
        return Prepared(item, None, F(), item.id)
    return [step(bl.items[0], True), step(bl.items[1], second_ok)]


def test_a_step_whose_check_failed_is_reworked_by_the_agent_and_replaces_the_old_one():
    bl = _list()
    llm = Says({"tool": "hole", "args": {**GOOD_HOLE, "material_length": 30}, "why": "the plate is 30 thick here"})
    tried = []

    async def try_feature(bl_, ctx, built, item, mark):
        tried.append((item.id, item.params["material_length"], sorted(built)))
        return {"status": "ok", "feature_call": True, "message": "built and checked"}
    r, calls = _runner(SolidWorksAgent(llm, b"png", "", review_turns=0), try_feature)
    with patch.object(runner_mod, "predicted_check", lambda *a, **k: None):
        rep = asyncio.run(r._run_once(bl, _steps(bl, False)))
    assert [i.status for i in rep.items] == ["ok", "ok"] and "reworked by the SolidWorks agent" in rep.items[1].message
    assert tried == [("F2", 30, ["F1"])]                                       # built on the part as it is, without the old F2
    assert any("REWORKED" in w for w in rep.warnings) and not any("CHECK FAILED" in w for w in rep.warnings)
    assert rep.agent["calls"][0]["tool"] == "hole"


def test_when_the_agent_gives_up_a_failed_step_is_taken_out_and_the_build_still_finishes():
    bl = _list()
    llm = Says({"tool": "skip", "args": {"reason": "the drawing does not show where"}})

    async def try_feature(*a):
        raise AssertionError("no feature call was made")
    r, calls = _runner(SolidWorksAgent(llm, b"png", "", review_turns=0), try_feature)
    counts = iter([5, 6, 6])

    async def count():
        return next(counts, 6)
    r._feature_count = count
    with patch.object(runner_mod, "predicted_check", lambda *a, **k: None):
        rep = asyncio.run(r._run_once(bl, _steps(bl, False)))
    assert rep.ok and rep.items[1].status == "removed" and "could not fix it: the drawing does not show where" in rep.items[1].message
    assert ("ext_delete_after", {"count": 5}) in calls                         # the half-right step came out again (back to the count before it)


def test_keep_after_the_agent_rebuilt_a_step_keeps_the_agents_version():
    """Live bug (triple clamp slit): the original step was never built; the agent's cut WAS built, its end-wall check failed because the slit
    runs into a bore, and the agent said keep. The agent's cut was taken out again. It must stay, flagged."""
    bl = _list()
    llm = Says({"tool": "cut", "args": {"face": "back", "depth": "through", "material_length": 10,
                                        "shapes": [{"kind": "slot", "centre_distance": 20, "width": 3, "position": {"x": 0, "x_from": "centre", "y": 0, "y_from": "centre"}}]},
                "why": "start on the back face"},
               {"tool": "keep", "args": {}, "why": "the slit runs into the bore: that end wall cannot exist"})

    async def try_feature(bl_, ctx, built, item, mark):
        return {"status": "check_failed", "feature_call": True, "message": "probe 'round walls' failed"}
    r, calls = _runner(SolidWorksAgent(llm, b"png", "", review_turns=0), try_feature)

    class Refused:
        async def apply(self, ctx):
            from app.features.base import FeatureRequestError
            raise FeatureRequestError("no planar face at -8.0 mm along the front normal")
    steps = _steps(bl, True)
    steps[1] = Prepared(bl.items[1], None, Refused(), "F2")
    with patch.object(runner_mod, "predicted_check", lambda *a, **k: None):
        rep = asyncio.run(r._run_once(bl, steps))
    f2 = rep.items[1]
    assert f2.status == "check_failed" and "reworked by the SolidWorks agent as cut" in f2.message and "kept by the SolidWorks agent" in f2.message
    assert [t for t, _ in calls].count("ext_delete_after") == 1                # only the clean-up before the agent's attempt; nothing after the keep
    assert any("kept by the SolidWorks agent" in w for w in rep.warnings)


def test_the_drawing_and_the_part_go_to_the_model_as_one_picture():
    import io
    from PIL import Image
    from app.agent.sw_agent import side_by_side
    png = lambda w, h: (lambda b: (Image.new("RGB", (w, h), "red").save(b, "PNG"), b.getvalue())[1])(io.BytesIO())
    out = Image.open(io.BytesIO(side_by_side(png(400, 300), png(800, 200))))
    assert out.size == (1600, 1200 + 400 + 12)
    seen = []

    class One:
        def generate_json(self, system, parts):
            seen.append([p for p in parts if not isinstance(p, str)])
            return LLMResponse("{}", {"tool": "done", "args": {}}, "fake", 0, {})

    async def run(tool, args):
        return {"status": "ok", "image": None}
    agent = SolidWorksAgent(One(), png(400, 300), "")
    asyncio.run(agent._ask("s", {}, png(100, 100)))
    assert len(seen[0]) == 1                                                   # one picture per call



def test_solidworks_failing_during_the_agents_work_stops_the_run_with_a_report_not_a_crash():
    """Live bug (pipe elbow): a failed bend left the part unreadable; measuring it for the agent raised and the whole build crashed with no
    report. Now the run stops like any SolidWorks failure: a report with an environment failure."""
    from app.verify import VerifyError
    bl = _list()
    r, calls = _runner(SolidWorksAgent(Says({"tool": "skip", "args": {}}), b"png", "", review_turns=0), None)

    async def unusable(ctx, mark):
        raise VerifyError("analyze_model failed twice: Error executing tool analyze_model")
    r._usable = unusable
    with patch.object(runner_mod, "predicted_check", lambda *a, **k: None):
        rep = asyncio.run(r._run_once(bl, _steps(bl, False)))
    assert not rep.ok and rep.failure.kind == "environment" and "analyze_model" in rep.failure.message


def test_one_budget_of_model_calls_covers_the_whole_part():
    """Stress run: a part with many failing steps took 71 model calls (22 minutes). The agent shares one budget across every step and the review."""
    async def run(tool, args):
        return {"status": "refused", "feature_call": True, "message": "no"}
    agent = SolidWorksAgent(Says(*[{"tool": "hole", "args": {}}] * 50), None, "", fix_turns=5, review_turns=5, total_turns=7)
    ends = [asyncio.run(agent.fix({"item": {"id": f"F{k}"}}, run)) for k in range(4)]
    assert agent.log.turns == 7 and all(e["tool"] == "skip" for e in ends) and "budget" in ends[-1]["args"]["reason"]


def test_a_feature_solidworks_refused_is_taken_out_before_the_part_is_measured():
    """Live bug (user's build): the agent's added boss was refused mid-sketch, the open sketch made the part unmeasurable and the finished
    part was thrown away. The refused attempt is undone first (the bridge also closes an open sketch), then measured."""
    from app.runner.runner import Runner as R
    bl = _list()
    r, calls = _runner(None, None)
    order = []

    async def call(tool, args=None, **kw):
        order.append(tool)
        return SimpleNamespace(ok=True, text="ok", json=lambda: {"count": 4})
    r.sw.call = call

    class Ver:
        async def current(self):
            order.append("measure")

    class Refused:
        async def apply(self, ctx):
            return FeatureResult("A1", verdicts=[StepVerdict("A1", False, "tool_failed", "ENTITY_CREATION_FAILED add_sketch_entity")])
    with patch.object(runner_mod, "prepare", lambda b: [Prepared(bl.items[0], None, Refused(), "A1")]):
        out = asyncio.run(R._try_feature(r, bl, SimpleNamespace(verifier=Ver()), {}, bl.items[1], 4))
    assert out["status"] == "failed" and "refused" in out["message"] and order == ["ext_delete_after", "measure"]


def test_a_failing_review_keeps_the_finished_part():
    from app.verify import VerifyError
    bl = _list()
    r, calls = _runner(SolidWorksAgent(Says(), b"png", "", review_turns=3), None)

    async def boom(*a):
        raise VerifyError("analyze_model failed twice")
    r._agent_review_part = boom

    class Ver:
        async def current(self):
            return None
    report = runner_mod.RunReport("p", "mm")
    stop = asyncio.run(r._agent_review(bl, SimpleNamespace(verifier=Ver()), {}, report))
    assert stop is False and report.failure is None and any("part is kept" in w for w in report.warnings)
