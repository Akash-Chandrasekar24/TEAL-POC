"""Projection and section evidence must survive extraction and CAD handoff."""
import copy
import json

import fakes  # noqa: F401
from fakes import FakeModel
from drawing_fixtures import drawing_result, interpretations, png, proposal
from app.agent.orchestrator import Orchestrator
from app.agent.workers import ExtractionAgent, SolidWorksAgent
from app.drawing.model import Resolutions, reconcile
from app.perception.extract import read_page


def symbol(convention="third", status="present"):
    return {"status": status, "convention": convention, "evidence": "Truncated cone and end-circle symbol in title block"}


def test_projection_symbol_sets_convention_and_conflicts_pause_for_human():
    for convention in ("first", "third"):
        result = drawing_result()
        tb = result["pages"][0]["read"]["title_block"]
        tb.update(projection="unknown", projection_symbol=symbol(convention))
        model = reconcile(result)
        assert model.projection == convention and not model.issues
        assert model.pages[0].title_block["projection_symbol"] == symbol(convention)
        tb["projection"] = "first" if convention == "third" else "third"
        ambiguous = reconcile(result)
        assert ambiguous.projection == "unknown"
        assert any("projection-symbol" in issue for issue in ambiguous.issues)
        assert not reconcile(result, Resolutions(projection=convention)).issues
    for bad in (None, {}, symbol(["first", "third"]), symbol("unknown", "unreadable"), symbol("third", "absent"),
                {"status": "present", "convention": "third", "evidence": ""}):
        result = drawing_result()
        result["pages"][0]["read"]["title_block"]["projection_symbol"] = bad
        assert reconcile(result).projection == "unknown"
    result["pages"][0]["read"]["title_block"]["projection_symbol"] = symbol("unknown", "absent")
    assert reconcile(result).projection == "third"  # Explicit printed text remains usable.


def test_known_page_cannot_hide_an_unreadable_symbol_on_another_page():
    result = drawing_result()
    second = copy.deepcopy(result["pages"][0])
    second.update(no=2)
    second["read"].update(dims=[], views=[])
    second["read"]["title_block"]["projection_symbol"] = symbol("unknown", "unreadable")
    result["pages"].append(second)
    assert reconcile(result).projection == "unknown"


def test_symbol_and_section_are_extracted_and_uncertainty_enters_source_review():
    section = {"cutting_plane": "A-A", "evidence": "Hatched wall around a blind bore", "questions": ["Confirm blind bore depth"]}
    client = FakeModel(reads=[
        {"d": ["10"], "tb": {"projection": "third", "projection_symbol": symbol()}, "notes": []},
        {"c": {"1": {"t": "section", "l": "SECTION A-A", "d": ["10"], "section": section}}}])
    read = read_page(client, 1, png(), [[0, 0, 700, 500]])
    assert read.title_block["projection_symbol"] == symbol()
    assert read.views[0].section == section
    assert any("Confirm blind bore depth" in issue for issue in read.issues)


def test_features_projection_and_sections_reach_cad_worker_together():
    result = drawing_result()
    tb = result["pages"][0]["read"]["title_block"]
    tb["projection_symbol"] = symbol()
    section = {"cutting_plane": "A-A", "evidence": "Plate thickness and bore are visible", "questions": []}
    result["pages"][0]["read"]["views"][1]["section"] = section
    observed = interpretations()[0].model_dump()
    extraction = FakeModel(reads=[observed])
    execution = FakeModel(reads=[proposal().model_dump()])
    report = Orchestrator(ExtractionAgent(extraction), SolidWorksAgent(execution)).plan(reconcile(result), {1: png()})
    assert report["status"] == "ready_for_approval"
    payload = json.loads(execution.calls[0][1][0])
    assert payload["interpretations"] == [observed]
    page = payload["drawing"]["pages"][0]
    assert payload["drawing"]["projection"] == "third"
    assert page["title_block"]["projection_symbol"] == symbol()
    assert page["views"][1]["section"] == section


def test_missing_symbol_and_section_evidence_require_review():
    client = FakeModel(reads=[
        {"d": ["10"], "tb": {"projection": "third"}, "notes": []},
        {"c": {"1": {"t": "section", "l": "A-A", "d": ["10"]}}}])
    read = read_page(client, 1, png(), [[0, 0, 700, 500]])
    assert read.title_block["projection_symbol"]["status"] == "unreadable"
    assert any("internal geometry" in issue for issue in read.issues)
