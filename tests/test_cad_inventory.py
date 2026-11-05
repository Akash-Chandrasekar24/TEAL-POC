"""Coverage must not confuse unreviewed claims or a listed tool with CAD support."""
import copy

import fakes  # noqa: F401
from fakes import GOOD

from eval.cad import inventory, markdown


def bench(**changes):
    return {"sets": [{"id": "part", "verified": False, "build_list": None, **changes}]}


def requirements(*capabilities, reviewed=False):
    return {"version": 1, "cases": [{"id": "part", "reviewed": reviewed,
            "required_capabilities": list(capabilities), "evidence": "section A-A reviewed"}]}


def test_unreviewed_requirements_do_not_rank_gaps_or_imply_coverage():
    report = inventory(bench(), requirements("sweep"))
    assert report["reviewed_gap_counts"] == {}
    assert report["cases"][0]["missing_capabilities"] == ["sweep"]
    assert not report["cases"][0]["offline_eligible"]
    assert "pending review" in markdown(report)


def test_reviewed_gaps_rank_once_per_drawing_and_block_eligibility():
    report = inventory(bench(verified=True, build_list=GOOD), requirements("sweep", "sweep", reviewed=True))
    assert report["reviewed_gap_counts"] == {"sweep": 1}
    assert not report["cases"][0]["offline_eligible"]
    assert report["cases"][0]["reference_status"] == "offline_valid"
    synthetic = inventory(bench(synthetic=True), requirements("sweep", reviewed=True))
    assert synthetic["reviewed_gap_counts"] == {}


def test_offline_eligibility_needs_reviewed_truth_requirements_and_reference():
    req = requirements("extrude_profile_boss", "hole", reviewed=True)
    for b in (bench(), bench(verified=True), bench(build_list=GOOD)):
        assert not inventory(b, req)["cases"][0]["offline_eligible"]
    report = inventory(bench(verified=True, build_list=GOOD), req)
    assert report["cases"][0]["offline_eligible"]
    assert report["cases"][0]["live_status"] == "not_measured"
    assert "sweep" not in {e["name"] for e in report["menu"]}


def test_invalid_or_missing_reference_is_not_counted_as_buildable():
    for reference in ({"status": "stopped", "reason": "assembly"}, {**GOOD, "items": []}, "does-not-exist.json"):
        report = inventory(bench(verified=True, build_list=reference), requirements("hole", reviewed=True))
        case = report["cases"][0]
        assert case["reference_status"] == "invalid" and case["reference_reasons"]
        assert not case["offline_eligible"]


def test_bad_annotations_are_refused_instead_of_silently_ignored():
    cases = [requirements("nonexistent"), requirements(reviewed=True),
             {"version": 9}, {"version": 1, "cases": [{"id": "unknown"}]}]
    duplicate = requirements("hole")
    duplicate["cases"] *= 2
    cases.append(duplicate)
    no_evidence = requirements("hole", reviewed=True)
    no_evidence["cases"][0]["evidence"] = ""
    cases.append(no_evidence)
    for bad in cases:
        try:
            inventory(bench(), copy.deepcopy(bad))
        except ValueError:
            continue
        raise AssertionError(f"accepted invalid annotations: {bad}")
