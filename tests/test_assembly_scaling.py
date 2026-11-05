"""Large flat assemblies: batched full checks and cached face lists cut the number of whole-assembly reads; safety checks are unchanged."""
import asyncio
import copy
import tempfile
from pathlib import Path

import fakes  # noqa: F401
from assembly_fixtures import FakeAssembly, ready_plan
from app.assembly.model import AssemblyPlan
from app.assembly.runner import run_assembly


def big_plan(assets, blocks):
    data = ready_plan(assets).model_dump()
    base, block = data["occurrences"]
    data["bom"][0]["quantity"] = blocks + 1
    data["occurrences"] = [base] + [{**copy.deepcopy(block), "id": f"b{k}"} for k in range(blocks)]
    mate = data["mates"][0]
    data["mates"] = [{**copy.deepcopy(mate), "id": f"M{k}", "b": {**mate["b"], "occurrence": f"b{k}"}} for k in range(blocks)]
    return AssemblyPlan.model_validate(data)


def reads(sw, kind, what):
    return sum(1 for name, args, _ in sw.calls if name == kind and args and args[0] == what)


def run(blocks, **kwargs):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory); plan, sw = big_plan(root / "assets", blocks), kwargs.pop("sw", None) or FakeAssembly()
        return asyncio.run(run_assembly(sw, plan, root / "assets", root / "run", plan.digest(), **kwargs)), sw


def test_small_assemblies_keep_the_check_after_every_step():
    report, sw = run(2)                                         # 3 inserts + 2 mates: every step plus the final reads
    assert report["status"] == "awaiting_review", report
    assert reads(sw, "analyze_assembly", "components") >= 3 + 2


def test_large_assembly_reads_all_components_far_less_often_and_still_succeeds():
    every, sw_every = run(30, check_every=1)
    batched, sw_batched = run(30)                               # default: every 10 steps above 20 steps
    assert every["status"] == batched["status"] == "awaiting_review", (every["errors"], batched["errors"])
    a, b = reads(sw_every, "analyze_assembly", "components"), reads(sw_batched, "analyze_assembly", "components")
    assert a >= 61 and b <= 12 and b * 4 < a, (a, b)           # 31 inserts + 30 mates vs batches of 10 + the fixed final checks
    # the base's face list is read once and reused by all 30 mates (60 reads before the cache), in both modes
    assert reads(sw_every, "analyze_assembly", "faces") == reads(sw_batched, "analyze_assembly", "faces") == 31


def test_batching_never_lets_drift_interference_or_a_foreign_document_reach_export():
    for case in ({"drift": True}, {"interference": True}, {"constraints": 2}, {"extra_docs": True}):
        report, sw = run(30, sw=FakeAssembly(**case))
        assert report["status"] == "failed" and report["errors"], case
        assert not any(c[0] in ("export_document", "save_document") for c in sw.calls), case


def test_a_failed_batch_names_the_steps_it_covers():
    report, _ = run(30, sw=FakeAssembly(drift=True))
    assert "since the last full check" in " ".join(c["name"] for c in report["checks"]) or "moved outside tolerance" in report["errors"][0]
