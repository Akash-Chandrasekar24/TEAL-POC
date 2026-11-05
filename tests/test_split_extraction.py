import copy
import threading

import fakes  # noqa: F401
from app.llm.base import LLMResponse
from app.perception.hybrid import extract,Extraction
from app.perception.split import Survey,bind_features
from drawing_fixtures import png
from test_hybrid_extraction import observed,ocr


def survey():
    return {"features":[{"kind":"cavity","description":"Sectioned internal cavity","at":[[400,400]],
                         "dimensions":{"depth":[{"text":"20 ±0.1","at":[125,125]}]},
                         "questions":["Missing cavity position"]}],"whole_page_checked":True}


def test_split_runs_both_lanes_concurrently_and_binds_source_locations():
    barrier=threading.Barrier(2)
    class Client:
        model="fake";cache_settings={}
        def generate_json(self,system,parts):
            barrier.wait(timeout=3)
            if "Your task is a concise mechanical feature survey" in system: data=survey()
            else:
                data=observed();data["features"]=[];data["coverage"]["features"]="not_checked"
            return LLMResponse("{}",data,self.model,1,{"output_tokens":20})
    result=extract(Client(),1,png(),ocr(),"r1",transport="split")
    assert result.calls==2
    evidence=result.evidence
    assert evidence["extraction"]["features"][0]["dimensions"]=={"depth":["o1"]}
    assert evidence["extraction"]["coverage"]["features"]=="assessed"
    assert evidence["source_feature_survey"]==Survey.model_validate(survey()).model_dump()
    assert any("Missing cavity position" in q for q in result.issues)
    assert evidence["cad_ready"] is False


def test_split_does_not_bind_equal_values_at_wrong_source_occurrences():
    original=Extraction.model_validate(observed());original.features=[]
    data=survey();data["features"][0]["dimensions"]["depth"][0]["at"]=[225,125]
    result=bind_features(original,Survey.model_validate(data))
    assert result.features[0].dimensions["depth"]==[]
    assert any("0 matches" in q for q in result.features[0].questions)
    assert original.features==[]
    duplicate=copy.deepcopy(original.callouts[0]);duplicate.id="other";original.callouts.append(duplicate)
    result=bind_features(original,Survey.model_validate(survey()))
    assert result.features[0].dimensions["depth"]==[]
    assert any("2 matches" in q for q in result.features[0].questions)


def test_split_unassessed_survey_cannot_mark_feature_coverage_complete():
    data=survey();data["whole_page_checked"]=False
    result=bind_features(Extraction.model_validate(observed()),Survey.model_validate(data))
    assert result.whole_page_checked is False
    assert result.coverage["features"]=="not_checked"


def test_split_retains_located_note_and_section_symbol_feature_references():
    original=observed();original["features"]=[]
    original["callouts"][0].update(text="ALL HOLES THROUGH",category="note")
    data=survey();data["features"][0]["dimensions"]={"termination":[{"text":"ALL HOLES THROUGH","at":[125,125]}]}
    result=bind_features(Extraction.model_validate(original),Survey.model_validate(data))
    assert result.features[0].dimensions=={"termination":["o1"]}
