"""Stress: whatever the model sends (garbage JSON, unknown tools, wrong types, bad numbers for every feature on the menu), the agent's tools
answer with a plain status and the build never crashes."""
import asyncio
import json
import random
from types import SimpleNamespace

import fakes  # noqa: F401
from app.agent.sw_agent import SolidWorksAgent
from app.llm.base import LLMResponse
from app.menu import MENU, parse_reply
from app.runner.runner import RequestProblem, Runner, prepare

STATUSES = {"ok", "refused", "check_failed", "removed", "failed"}


def _bl():
    return parse_reply({"list_version": "1", "unit": "mm", "projection": "third", "items": [
        {"id": "F1", "feature": "extrude_profile_boss", "params": {"profile": {"shape": "rectangle", "width": 120, "height": 80}, "depth": 20}}]})


def _runner():
    r = Runner.__new__(Runner)

    async def call(tool, args=None, **kw):
        return SimpleNamespace(ok=True, text="ok", json=lambda: {"count": 3})
    r.sw, r.log, r.expected_docs = SimpleNamespace(call=call, tool_names=["ext_feature_count", "ext_delete_after"]), None, ()

    async def measure(ctx):
        return {"bodies": 1}

    async def look(ctx):
        return {"status": "ok", "message": "picture"}

    async def try_feature(bl, ctx, built, item, mark):                       # the real offline check, no SolidWorks
        try:
            prepare(bl.model_copy(update={"items": [*built.values(), item]}))
        except RequestProblem as ex:
            return {"status": "refused", "feature_call": True, "message": ex.message}
        return {"status": "ok", "feature_call": True, "message": "built"}
    r._measure, r._look, r._try_feature = measure, look, try_feature
    return r


def _junk(rng):
    pick = rng.choice
    return pick([None, 0, -1, 1e9, "", "abc", [], {}, [1, 2], {"x": "y"}, True, float("nan")])


def test_every_tool_survives_random_and_broken_arguments():
    rng = random.Random(7)
    bl = _bl()
    r = _runner()
    built = {"F1": bl.items[0]}
    tools = [*MENU, "describe", "measure", "look", "", "rm -rf", "hole ", "HOLE"]
    for phase in ("fix", "review"):
        run = r._tools(bl, None, dict(built), phase, "F2", [3])
        for k in range(600):
            tool = rng.choice(tools)
            ex = MENU[tool].example if tool in MENU else {}
            args = dict(ex)
            for key in rng.sample(list(args) or ["x"], k=min(len(args) or 1, rng.randint(0, 3))):
                args[key] = _junk(rng)                                          # break some fields of a valid example
            if rng.random() < 0.2:
                args = {"feature": rng.choice([*MENU, "nope", 5])}
            out = asyncio.run(run(tool, args))
            assert out["status"] in STATUSES, (tool, args, out)
            assert isinstance(out.get("message", ""), str)


def test_the_agent_survives_a_model_that_answers_nonsense():
    class Junk:
        def __init__(self):
            self.n = 0

        def generate_json(self, system, parts):
            self.n += 1
            data = [None, [], "text", {"tool": 5}, {"tool": "hole", "args": "not a dict"}, {"args": {}}, {"tool": "keep"}][self.n % 7]
            return LLMResponse(json.dumps(data), data, "fake", 0, {})

    async def run(tool, args):
        assert isinstance(tool, str) and isinstance(args, dict)
        return {"status": "refused", "message": "no"}
    for turns in (1, 3, 7, 12):
        end = asyncio.run(SolidWorksAgent(Junk(), None, "", fix_turns=turns, review_turns=turns).fix({"item": {"id": "F2"}}, run))
        assert end["tool"] in ("skip", "keep")
        end = asyncio.run(SolidWorksAgent(Junk(), None, "", fix_turns=turns, review_turns=turns).review({}, run))
        assert end["tool"] in ("skip", "done")
