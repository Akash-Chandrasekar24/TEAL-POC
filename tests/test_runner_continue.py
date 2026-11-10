"""A later step whose check fails no longer stops the part: it is flagged and the remaining steps are built (the triple clamp stopped at
step 6 of 16 on a hole that removed 1.7% less material, leaving a flat plate). The base, and SolidWorks failures, still stop."""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import fakes  # noqa: F401
from app.features.base import FeatureResult
from app.runner import runner as runner_mod
from app.runner.runner import Prepared, Runner
from app.verify.step_verifier import StepVerdict


def step(name, ok=True, verdict="ok"):
    class F:
        async def apply(self, ctx):
            return FeatureResult(name, verdicts=[StepVerdict(name, ok, verdict, "volume change -2422.00 mm3, expected -2463.01 mm3")])
    return Prepared(SimpleNamespace(id=name, feature="hole"), None, F(), name)


def run(prepared):
    r = Runner.__new__(Runner)
    r.sw, r.log, r.expected_docs = SimpleNamespace(), None, ()

    async def opened(unit, report):
        return "Part1"

    async def final(verifier, report):
        return {}
    r._open_part, r._final = opened, final
    bl = SimpleNamespace(part_name="p", unit="mm", unsupported=[], accepted_gaps=[], expected_final=None)
    with patch.object(runner_mod, "predicted_check", lambda *a, **k: None):
        return asyncio.run(r._run_once(bl, prepared))


def test_a_failed_check_on_a_later_step_is_flagged_and_the_rest_is_built():
    rep = run([step("F1"), step("F6", ok=False, verdict="wrong_delta"), step("F7")])
    assert rep.ok and rep.failure is None
    assert [i.status for i in rep.items] == ["ok", "check_failed", "ok"]
    assert any("F6" in w and "CHECK FAILED" in w for w in rep.warnings)


def test_the_base_failing_or_solidworks_failing_still_stops():
    assert run([step("F1", ok=False, verdict="wrong_delta"), step("F2")]).failure.item_id == "F1"
    rep = run([step("F1"), step("F2", ok=False, verdict="timeout"), step("F3")])
    assert not rep.ok and rep.failure.kind == "environment" and rep.items[2].status == "not_run"
