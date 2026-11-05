import base64
import copy
import json
import http.client
from http.server import ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from types import SimpleNamespace

import fakes  # noqa: F401
from fakes import FakeModel
from drawing_fixtures import png
from app.perception.hybrid import extract, Extraction
from app.perception.evidence import EvidenceStore
from app.drawing.model import Resolutions
from app.web import server
from eval.hybrid_score import score
from app.perception import compact


def anchored():
    data=observed()
    data["format"]=compact.VERSION
    data["views"][0].pop("id")
    data["callouts"]=[{"ocr_row":1,"views":[1],"quantity":"4","tolerance":"±0.1"},
                      {"text":"20 ±0.2","box":[200,100,250,150],"views":[1],"ocr_rows":[2],
                       "category":"measurement","status":"conflict","alternatives":["20 ±0.3"]}]
    data["features"][0].update(views=[1],dimensions={"depth":[2]})
    data["features"][0].pop("id")
    return data


def test_anchored_transport_preserves_complete_evidence_and_noncontiguous_ocr_ids():
    source=ocr();source["lines"][0]["id"]="ocr17"
    data=anchored();before=copy.deepcopy(data)
    result=extract(FakeModel(reads=[data]),1,png(),source,"r1",transport="anchored")
    assert data==before
    evidence=result.evidence["extraction"]
    assert evidence["callouts"][0]["text"]=="20 ±0.1"
    assert evidence["callouts"][0]["ocr_ids"]==["ocr17"]
    assert evidence["callouts"][0]["quantity"]==4
    assert evidence["callouts"][0]["tolerance"]=="±0.1"
    assert evidence["callouts"][1]["alternatives"]==["20 ±0.3"]
    assert evidence["features"][0]["dimensions"]=={"depth":["o2"]}
    assert result.evidence["attempts"][0]["wire_format"]==compact.VERSION
    assert result.views[0].section["cutting_plane"]=="A-A"


def test_anchors_reject_invented_references_rotated_boxes_and_field_overrides():
    for change in (lambda d:d["callouts"][0].update(ocr_row=0),
                   lambda d:d["callouts"][0].update(ocr_row=True),
                   lambda d:d["callouts"][0].update(ocr_row=99),
                   lambda d:d["callouts"][0].update(text="different"),
                   lambda d:d["callouts"][0].update(views=[99]),
                   lambda d:d["features"][0].update(dimensions={"depth":[99]})):
        data=anchored();change(data)
        try: compact.expand(data,ocr())
        except ValueError: pass
        else: raise AssertionError("Invalid source anchor accepted")
    source=ocr();source["coordinate_frame"]="ocr_rotated"
    try: compact.expand(anchored(),source)
    except ValueError: pass
    else: raise AssertionError("A rotated OCR box was used as a source box")


def test_anchor_provider_can_omit_format_marker_without_a_second_model_call():
    data=anchored();data.pop("format")
    result=extract(FakeModel(reads=[data]),1,png(),ocr(),"r1",transport="anchored")
    assert result.calls==1 and result.evidence["extraction"]["callouts"][0]["text"]=="20 ±0.1"


def test_anchor_shortcut_does_not_approve_coverage_or_infer_quantities():
    data=anchored();data["coverage"].pop("sections");data["views"][0]["assessed"]=False
    data["callouts"][0].pop("quantity")
    result=extract(FakeModel(reads=[data]),1,png(),ocr(),"r1",transport="anchored")
    assert result.evidence["extraction"]["callouts"][0]["quantity"] is None
    assert result.evidence["cad_ready"] is False
    assert any("sections" in q for q in result.issues)
    assert any("not been assessed" in q for q in result.issues)
    for value in (True,"2.5","2-4",0):
        data=anchored();data["callouts"][0]["quantity"]=value
        try: Extraction.model_validate(compact.expand(data,ocr()))
        except ValueError: pass
        else: raise AssertionError("Noninteger quantity accepted")


def observed():
    return {"name":"test","unit":"mm","projection":"third",
            "projection_symbol":{"status":"absent","convention":"unknown","evidence":"Printed THIRD ANGLE text"},
            "document_kind":"part",
            "views":[{"id":"v1","box":[0,0,800,800],"type":"section","label":"A-A","cutting_plane":"A-A",
                      "section_evidence":"Two distinct callouts","assessed":True}],
            "callouts":[{"id":f"o{i}","box":[i*100,100,i*100+50,150],"text":text,"category":"measurement",
                         "views":["v1"],"ocr_ids":[f"ocr{i}"],"status":"matched","tolerance":tolerance}
                        for i,text,tolerance in [(1,"20 ±0.1","±0.1"),(2,"20 ±0.2","±0.2")]],
            "features":[{"id":"f1","kind":"cavity","description":"Internal cavity","views":["v1"],"dimensions":{"depth":["o1"]}}],
            "coverage":{k:"assessed" for k in ("dimensions","symbols","projection","sections","features","notes","bom")},
            "whole_page_checked":True}


def ocr():
    return {"engine":"fake","width":800,"height":600,"coordinate_frame":"source","angle":0,"seconds":0,
            "lines":[{"id":f"ocr{i}","text":c["text"],"box":[0,0,200,100]} for i,c in enumerate(observed()["callouts"],1)]}


def test_hybrid_preserves_distinct_tolerances_occurrences_and_section():
    result=extract(FakeModel(reads=[observed()]),1,png(),ocr(),"r1")
    assert [d.text for d in result.dims]==["20 ±0.1","20 ±0.2"]
    assert result.dims[0].id!=result.dims[1].id
    assert result.views[0].section["cutting_plane"]=="A-A"
    assert result.evidence["features"]["features"][0]["kind"]=="unsupported"
    assert result.evidence["human_review"]=="pending" and result.evidence["cad_ready"] is False


def test_verifier_cannot_silently_drop_candidate_occurrences():
    revised=observed();revised["callouts"].pop()
    result=extract(FakeModel(reads=[observed(),revised]),1,png(),ocr(),"r2")
    assert result.calls==2 and len(result.dims)==2
    assert any("Verification omitted callouts o2" in q for q in result.issues)
    assert result.evidence["coverage_status"]=="needs_review"


def test_unknown_references_and_invalid_source_boxes_are_rejected():
    for change in (lambda r:r["callouts"][0].update(views=["missing"]),
                   lambda r:r["views"][0].update(box=[0,0,0,0]),
                   lambda r:r["callouts"].append(copy.deepcopy(r["callouts"][0]))):
        data=observed();change(data)
        try: Extraction.model_validate(data)
        except ValueError: pass
        else: raise AssertionError("Invalid source evidence accepted")
    data=observed();data["callouts"][0]["ocr_ids"]=["missing"]
    try: extract(FakeModel(reads=[data]),1,png(),ocr())
    except ValueError: pass
    else: raise AssertionError("Unknown OCR evidence accepted")


def test_missing_coverage_and_false_ocr_agreement_remain_questions():
    data=observed();data["coverage"].pop("symbols");data["views"][0]["assessed"]=False
    data["callouts"][0]["text"]="20 ±0.9"
    result=extract(FakeModel(reads=[data]),1,png(),ocr())
    assert any("symbols" in q for q in result.issues)
    assert any("not been assessed" in q for q in result.issues)
    assert result.evidence["extraction"]["callouts"][0]["status"]=="conflict"


def test_null_units_mean_unknown_and_verification_cannot_erase_questions():
    data=observed();data["callouts"][0]["unit"]=None
    data["questions"]=["Missing locating dimensions"]
    revised=observed()
    result=extract(FakeModel(reads=[data,revised]),1,png(),ocr(),"r2")
    assert "Missing locating dimensions" in result.issues
    assert Extraction.model_validate(data).callouts[0].unit=="unknown"


def test_section_reference_object_preserves_label_and_both_evidence_texts():
    data=observed();data["views"][0]["cutting_plane"]={"label":"A-A","evidence":"Arrows at both ends"}
    parsed=Extraction.model_validate(data)
    assert parsed.views[0].cutting_plane=="A-A"
    assert parsed.views[0].section_evidence=="Two distinct callouts\nArrows at both ends"
    data["views"][0]["cutting_plane"]["unexpected"]="unrecognized evidence"
    try: Extraction.model_validate(data)
    except ValueError: pass
    else: raise AssertionError("Unknown section evidence was discarded")


def test_overlapping_views_are_retained_but_require_review():
    data=observed();other=copy.deepcopy(data["views"][0]);other["id"]="v2";data["views"].append(other)
    result=extract(FakeModel(reads=[data]),1,png(),ocr())
    assert len(result.views)==2
    assert any("overlaps" in issue for issue in result.issues)


def test_s2_source_binding_revision_conflicts_and_image_integrity():
    with tempfile.TemporaryDirectory() as folder:
        source={"id":"abcdef123456","pages":[]};store=EvidenceStore(folder)
        first=store.publish(source,{"original":True},{"source":png()})
        assert store.load(source,first)["payload"]["original"]
        second=store.publish(source,{"corrected":True},{},expected_parent=first["revision"])
        assert store.load(source)["revision"]==second["revision"]
        for operation in (lambda:store.publish(source,{},{}), lambda:store.load({**source,"changed":True},first),
                          lambda:store.load(source,{**first,"revision":"../../bad"})):
            try: operation()
            except ValueError: pass
            else: raise AssertionError("Stale or invalid evidence accepted")
        original=store.load(source,first)
        (Path(folder)/"blobs"/original["assets"]["source"]).write_bytes(b"corrupt")
        try: store.load(source,first)
        except ValueError: pass
        else: raise AssertionError("Altered source image accepted")


def test_s2_competing_updates_publish_only_one_child():
    with tempfile.TemporaryDirectory() as folder, ThreadPoolExecutor(max_workers=2) as pool:
        source={"id":"abcdef123456"};store=EvidenceStore(folder)
        first=store.publish(source,{},{}); barrier=threading.Barrier(2)
        def update(number):
            barrier.wait(timeout=3)
            try: return store.publish(source,{"n":number},{},expected_parent=first["revision"])
            except ValueError: return None
        results=list(pool.map(update,[1,2]))
        assert sum(r is not None for r in results)==1


def test_exact_score_does_not_discard_tolerance_or_quantity():
    reference={"verification":"fixture","scope":"measurements","measurement_texts":["4xØ10 ±0.1","20 ±0.2"]}
    extraction={"callouts":[{"category":"measurement","text":"Ø10 ±0.1"},{"category":"measurement","text":"20 ±0.1"}],
                "views":[],"unit":"unknown","projection":"unknown"}
    assert score(reference,extraction)["matched"]==0


def test_web_hybrid_saves_sidecar_and_never_auto_promotes_to_cad():
    with tempfile.TemporaryDirectory() as folder:
        root=Path(folder)
        paths={k:root/k for k in ("RESULTS","UPLOADS","RUNS","PLANS","ASSEMBLIES","READINESS","CHATS","ENRICHMENTS","EVIDENCE")}
        with patch.multiple(server,**paths),patch("app.llm.make_client",return_value=FakeModel(reads=[observed()])), \
             patch("app.perception.ocr.read_ocr",return_value=ocr()), \
             patch.object(server,"segment_page",side_effect=AssertionError("CV must not identify hybrid views")):
            result=server.run("abcdef123456",[{"name":"test.png","data":base64.b64encode(png()).decode()}],"domo",True,
                              workflow=True,progressive=True,strategy="r1")
            original=(server.RESULTS/"abcdef123456.json").read_bytes()
            assert result["evidence_binding"] and result["extracted_features"]
            assert server.result_with_design(result["id"])["evidence_binding"]==result["evidence_binding"]
            assert (server.RESULTS/"abcdef123456.json").read_bytes()==original
            try: server.plan_result(result["id"],result["drawing_model"]["source_hash"],Resolutions())
            except ValueError as error: assert "evaluation modes" in str(error)
            else: raise AssertionError("Trial evidence was promoted to CAD")
            try: server.workflow_plan(result["id"],"irrelevant")
            except ValueError as error: assert "evaluation evidence" in str(error)
            else: raise AssertionError("Trial evidence bypassed the workflow gate")


def test_http_retained_source_survives_cache_miss_and_corruption_is_actionable():
    with tempfile.TemporaryDirectory() as folder:
        root=Path(folder)
        paths={k:root/k for k in ("RESULTS","UPLOADS","RUNS","PLANS","ASSEMBLIES","READINESS","CHATS","ENRICHMENTS","EVIDENCE")}
        with patch.multiple(server,**paths),patch("app.llm.make_client",return_value=FakeModel(reads=[observed()])), \
             patch("app.perception.ocr.read_ocr",return_value=ocr()):
            result=server.run("abcdef123456",[{"name":"test.png","data":base64.b64encode(png()).decode()}],"domo",True,
                              workflow=True,progressive=True,strategy="r1")
            sha=result["pages"][0]["sha"]
            httpd=ThreadingHTTPServer(("127.0.0.1",0),server.Handler)
            thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
            def get(path):
                connection=http.client.HTTPConnection(*httpd.server_address,timeout=5)
                try:
                    connection.request("GET",path);response=connection.getresponse()
                    return response.status,response.read()
                finally: connection.close()
            try:
                with patch.object(server,"default_cache",return_value=SimpleNamespace(get_blob=lambda sha:None)):
                    status,body=get(f"/api/page/{sha}")
                    assert status==200 and body==png()
                    status,body=get("/api/result/abcdef123456")
                    assert status==200 and json.loads(body)["evidence_binding"]
                    (server.EVIDENCE/"blobs"/sha).write_bytes(b"changed")
                    status,body=get("/api/result/abcdef123456")
                    assert status==409 and "evidence" in json.loads(body)["error"]
                    assert get(f"/api/page/{sha}")[0]==404
            finally:
                httpd.shutdown();httpd.server_close();thread.join(timeout=5)
