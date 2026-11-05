"""Chat replies are saved review inputs; they cannot approve CAD or export."""
import http.client
import json
import tempfile
import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import fakes  # noqa: F401
from app.drawing.model import DrawingModel, Resolutions
from app.web import server
from test_drawing_web import saved_result


@contextmanager
def workspace():
    with saved_result() as source, tempfile.TemporaryDirectory() as directory, \
         patch.object(server,"CHATS",Path(directory)), \
         patch("app.llm.make_client",side_effect=AssertionError("Saving a chat correction must not call a model")), \
         patch.object(server.WORKFLOW,"action",side_effect=AssertionError("A chat reply must not start CAD")):
        yield source


def reply(source,target,text,**kwargs):
    current=server.result_with_design(source["id"])
    return server.chat_reply(source["id"],current["drawing_model"]["source_hash"],current["review_hash"],target,text,**kwargs)


def refuses(fn,fragment):
    try:fn()
    except ValueError as ex:assert fragment.lower() in str(ex).lower(),str(ex)
    else:raise AssertionError("Invalid chat correction was accepted")


def test_chat_measurement_updates_review_and_retains_original_evidence_and_history():
    with workspace() as source:
        original=(server.RESULTS/f"{source['id']}.json").read_bytes()
        before=server.result_with_design(source["id"])
        saved=reply(source,"P1.d1","125")
        dim=next(d for d in saved["review"]["model"]["dimensions"] if d["id"]=="P1.d1")
        assert dim["text"]=="120" and dim["corrected_text"]=="125" and dim["confirmed"]
        assert saved["review"]["plan"]["draft"] is None and not saved["review"]["plan"]["approved"]
        assert (server.RESULTS/f"{source['id']}.json").read_bytes()==original
        assert server.chat_history(source["id"])[0]["reply"]=="125"
        assert saved["review_hash"]!=before["review_hash"]
        refuses(lambda:server.chat_reply(source["id"],before["drawing_model"]["source_hash"],before["review_hash"],"P1.d1","130"),"review changed")
        assert len(server.chat_history(source["id"]))==1
        refuses(lambda:server.downloadable_list(source["id"]),"before downloading")


def test_chat_clarification_preserves_other_review_decisions_and_refuses_invalid_values():
    with workspace() as source:
        reply(source,"P1.d1","125")
        saved=reply(source,"note","The top and front views show the same central boss")
        model=DrawingModel.model_validate(saved["review"]["model"])
        assert next(d for d in model.dimensions if d.id=="P1.d1").nominal()==125
        assert "central boss" in model.resolutions.note
        before=server.result_with_design(source["id"])["review_hash"]
        refuses(lambda:reply(source,"P1.d1","not a measurement"),"nominal")
        refuses(lambda:reply(source,"P1.d999","38"),"Choose a current")
        assert server.result_with_design(source["id"])["review_hash"]==before
        assert len(server.chat_history(source["id"]))==2


def test_chat_source_question_requires_a_valid_view_for_supplied_measurement():
    with workspace() as source:
        source["pages"][0]["read"]["issues"]=["Hole depth unreadable"]
        (server.RESULTS/f"{source['id']}.json").write_text(json.dumps(source),encoding="utf-8")
        refuses(lambda:reply(source,"P1.u1","Depth is shown in the section",measurement="12"),"source view")
        saved=reply(source,"P1.u1","Depth is 12 in section A-A",measurement="12",view="P1.v2")
        human=next(d for d in saved["review"]["model"]["dimensions"] if d["id"]=="P1.h1")
        assert human["token"]=="12" and human["views"]==["P1.v2"] and human["confirmed"]
        assert not any("Hole depth unreadable" in i for i in saved["review"]["model"]["issues"])


def test_chat_http_reopens_saved_replies_and_rejects_cross_origin_writes():
    class Quiet(server.Handler):
        def log_message(self,*args):pass
    with workspace() as source:
        httpd=ThreadingHTTPServer(("127.0.0.1",0),Quiet)
        thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
        connection=http.client.HTTPConnection("127.0.0.1",httpd.server_port,timeout=10)
        current=server.result_with_design(source["id"])
        payload={"result_id":source["id"],"source_hash":current["drawing_model"]["source_hash"],"model_hash":current["review_hash"],"target":"P1.d1","text":"125"}
        try:
            connection.request("POST","/api/chat",json.dumps(payload),{"Content-Type":"application/json","Origin":"https://unrelated.example"})
            response=connection.getresponse();response.read();assert response.status==403
            connection.request("POST","/api/chat",json.dumps(payload),{"Content-Type":"application/json"})
            response=connection.getresponse();saved=json.loads(response.read());assert response.status==200,saved
            connection.request("GET",f"/api/chat/{source['id']}")
            response=connection.getresponse();history=json.loads(response.read())
            assert response.status==200 and history==saved["history"]
            connection.request("GET",f"/api/result/{source['id']}")
            response=connection.getresponse();reopened=json.loads(response.read())
            assert reopened["review_hash"]==saved["review_hash"]
        finally:
            connection.close();httpd.shutdown();httpd.server_close();thread.join(timeout=5)
