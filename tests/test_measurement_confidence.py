import copy
import math

import fakes  # noqa: F401
from drawing_fixtures import drawing_result,png
from test_hybrid_extraction import observed,ocr
from fakes import FakeModel
from app.perception.hybrid import extract,Extraction
from app.perception.extract import read_page
from app.drawing.model import reconcile,Resolutions
from app.drawing.confidence import review_index,rubric_score


def scored(score):
    result=drawing_result();result["measurement_review_policy"]="agent-confidence-v1"
    for dim in result["pages"][0]["read"]["dims"]:
        dim.update(confidence=95,confidence_metrics=[5,5,3,5,5],confidence_reason="Digits and notation legible in source")
    result["pages"][0]["read"]["dims"][0].update(confidence=score,single_source=True)
    return result


def test_strict_above_80_threshold_requires_agent_rubric_ratings():
    for metrics,expected in (([5,5,0,5,5],False),([4,5,3,4,4],True),([5,5,3,5,5],True),(None,False),([True,5,3,5,5],False),(["5",5,3,5,5],False),([6,5,3,5,5],False)):
        result=scored(99);result["pages"][0]["read"]["dims"][0]["confidence_metrics"]=metrics
        model=reconcile(result);review=review_index(result,model)["P1.d1"]
        assert review["ai_approved"] is expected
        assert any("P1.d1" in issue for issue in model.issues) is not expected
        if expected: assert model.dimensions[0].confirmed is False
    result=drawing_result();model=reconcile(result)
    assert review_index(result,model)["P1.d1"]["confidence"] is None


def test_confidence_never_silences_conflict_or_unlocated_source():
    data=observed()
    for c in data["callouts"]: c.update(confidence=99,confidence_metrics=[5,5,3,5,5],confidence_reason="Visible source characters")
    data["callouts"][0]["status"]="conflict"
    read=extract(FakeModel(reads=[data]),1,png(),ocr(),"r1")
    from dataclasses import asdict
    result={"id":"abcdef123456","measurement_review_policy":"agent-confidence-v1","pages":[{"no":1,"source":"test.png","source_page":1,"sha":"source","read":asdict(read)}]}
    model=reconcile(result);review=review_index(result,model)
    assert review["P1.d1"]["confidence"]==92 and not review["P1.d1"]["ai_approved"]
    assert review["P1.d2"]["ai_approved"]
    # A fabricated convenience score must not override the canonical agent record.
    result["pages"][0]["read"]["dims"][0]["confidence"]=100
    assert not review_index(result,reconcile(result))["P1.d1"]["ai_approved"]
    result=scored(99);result["pages"][0]["read"]["dims"][0]["views"]=[]
    assert not review_index(result,reconcile(result))["P1.d1"]["ai_approved"]


def test_human_override_and_review_flag_are_distinct_from_ai_approval():
    result=scored(95)
    flagged=reconcile(result,Resolutions.model_validate({"dimensions":[{"id":"P1.d1","confirmed":False}]}))
    assert review_index(result,flagged)["P1.d1"]["needs_review"]
    corrected=reconcile(result,Resolutions.model_validate({"dimensions":[{"id":"P1.d1","text":"125"}]}))
    state=review_index(result,corrected)["P1.d1"]
    assert state["human_confirmed"] and not state["ai_approved"] and not state["needs_review"]
    assert corrected.dimensions[0].text=="120" and corrected.dimensions[0].effective_text=="125"


def test_hybrid_agent_confidence_is_strict_and_survives_the_handoff():
    data=observed();data["callouts"][0].update(confidence=99,confidence_metrics=[5,5,3,5,5],confidence_reason="Complete callout is legible")
    result=extract(FakeModel(reads=[data]),1,png(),ocr(),"r1")
    assert result.dims[0].confidence==92
    assert result.evidence["extraction"]["callouts"][0]["confidence_reason"]=="Complete callout is legible"
    for invalid in (True,"92",101,-1,math.nan):
        bad=copy.deepcopy(data);bad["callouts"][0]["confidence"]=invalid
        try: Extraction.model_validate(bad)
        except ValueError: pass
        else: raise AssertionError("Invalid agent confidence accepted")
    data=observed();source=ocr()
    for line in source["lines"]:line["confidence"]=99
    result=extract(FakeModel(reads=[data]),1,png(),source,"r1")
    assert all(dim.confidence is None for dim in result.dims),"OCR scores must never become agent confidence"


def test_weighted_metrics_and_critical_evidence_guards_are_independent():
    assert rubric_score([5,5,3,5,5])==92
    assert rubric_score([4,4,4,4,4])==80
    assert rubric_score([4,5,3,4,4])==81
    result=scored(100)
    dimension=result["pages"][0]["read"]["dims"][0]
    dimension["confidence_metrics"]=[5,5,3,5,3]
    state=review_index(result,reconcile(result))["P1.d1"]
    assert state["confidence"]==88 and not state["ai_approved"],"Unclear mechanical meaning cannot be averaged away"
    dimension["confidence_metrics"]=[5,5,5,5,5]
    state=review_index(result,reconcile(result))["P1.d1"]
    assert not state["ai_approved"] and "supporting views" in state["review_reason"]


def test_cross_view_high_scores_require_distinct_assessed_references():
    data=observed()
    second=copy.deepcopy(data["views"][0]);second.update(id="v2",box=[810,0,1000,800]);data["views"].append(second)
    data["callouts"][0].update(confidence_metrics=[4,4,4,4,4],confidence_support_views=["v1","v2"],confidence_reason="Corresponding section confirms the feature")
    from dataclasses import asdict
    def review_for(candidate):
        read=extract(FakeModel(reads=[candidate]),1,png(),ocr(),"r1")
        result={"id":"abcdef123456","pages":[{"no":1,"source":"test.png","source_page":1,"sha":"source","read":asdict(read)}]}
        return review_index(result,reconcile(result))["P1.d1"]
    assert not review_for(data)["ai_approved"],"Exactly 80 requires review"
    data["callouts"][0]["confidence_metrics"]=[4,4,5,4,4]
    assert review_for(data)["ai_approved"]
    data["callouts"][0]["confidence_support_views"]=["v1","v1"]
    assert not review_for(data)["ai_approved"]


def test_legacy_agent_records_preserve_scores_without_inventing_them():
    page={"d":[{"text":"20","confidence":94,"confidence_reason":"Clear printed text"},"10"],"tb":{},"notes":[]}
    crops={"c":{"1":{"t":"front","d":["20","10"]}}}
    result=read_page(FakeModel(reads=[page,crops]),1,png(),[[0,0,800,600]])
    readings={d.text:d for d in result.dims}
    assert readings["20"].confidence==94 and readings["10"].confidence is None
