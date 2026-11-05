import base64
import io
import json
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

import fakes  # noqa: F401
from PIL import Image
from app.web import server
from app.agent.orchestrator import Orchestrator
from app.perception.extract import PageRead, agent_boxes, read_page, AGENT_VIEWS_PROMPT
from app.drawing.planner import PageInterpretation
from app.drawing.model import Resolutions
from app.llm.cached import CachedClient
from app.llm.base import LLMResponse
from app.cache import Cache


def test_agent_boundaries_validate_without_cv():
    boxes, issues = agent_boxes([{"box":[100,100,400,400]}, {"box":[100,100,400,400]}, {"box":[600,100,900,400]}, {"box":[-1,0,10,10]}], 2000, 1000)
    assert boxes == [[200,100,800,400],[1200,100,1800,400]]
    assert any("duplicate" in issue for issue in issues)
    assert any("invalid" in issue for issue in issues)
    assert agent_boxes(None, 100, 100)[1]


def test_agent_boxes_drive_crop_read_and_keep_valid_overlap_cached():
    class Client:
        model="fake"
        def generate_json(self, system, parts):
            data = {"d":[],"tb":{},"notes":[],"regions":[{"box":[0,0,700,700]},{"box":[400,0,1000,1000]}]} if system == AGENT_VIEWS_PROMPT else {"c":{"1":{"t":"front","d":[]},"2":{"t":"iso","d":[]}}}
            return LLMResponse(json.dumps(data),data,self.model,0,{})
        def discard(self,*args):
            raise AssertionError("Valid overlap should remain cached and be presented for human review")
    buffer=io.BytesIO(); Image.new("RGB",(200,100),"white").save(buffer,"PNG")
    result=read_page(Client(),1,buffer.getvalue(),None)
    assert [view.box for view in result.views]==[[0,0,140,70],[80,0,200,100]]
    assert result.calls==2 and any("overlap" in issue for issue in result.issues)


def test_progressive_review_returns_before_features_preserves_source_and_edits():
    entered, release = threading.Event(), threading.Event()
    def interpretation(*args):
        entered.set()
        assert release.wait(10)
        return [PageInterpretation(features=[])]
    data = io.BytesIO(); Image.new("RGB", (120,100), "white").save(data,"PNG")
    with tempfile.TemporaryDirectory() as folder, ThreadPoolExecutor(max_workers=1) as pool:
        root = Path(folder)
        paths = {key:root/key for key in ["RESULTS","UPLOADS","RUNS","PLANS","ASSEMBLIES","READINESS","ENRICHMENTS"]}
        with patch.multiple(server, **paths, _ENRICH_POOL=pool), patch("app.llm.make_client",return_value=SimpleNamespace(model="fake",provider="fake")), \
                patch.object(Orchestrator,"read_page",return_value=PageRead(1,[],[],{"document_kind":"part"},[],0)) as reading, \
                patch.object(Orchestrator,"interpret",side_effect=interpretation), \
                patch.object(server,"segment_page",side_effect=AssertionError("Browser extraction must not call CV")):
            try:
                result=server.run("123456abcdef",[{"name":"drawing.png","data":base64.b64encode(data.getvalue()).decode()}],"domo",True,workflow=True,progressive=True)
                assert result["extraction_status"]=="processing" and "extracted_features" not in result
                assert entered.wait(5)
                assert reading.call_args.args[2] is None
                original=(server.RESULTS/"123456abcdef.json").read_bytes()
                server.review_result(result["id"], result["drawing_model"]["source_hash"], Resolutions(note="Human clarification kept"))
            finally:
                release.set()
            pool.shutdown(wait=True)
            latest=server.result_with_design(result["id"])
            assert latest["extraction_status"]=="complete", latest.get("feature_extraction_error")
            assert "extracted_features" in latest
            assert latest["drawing_model"]["resolutions"]["note"]=="Human clarification kept"
            assert (server.RESULTS/"123456abcdef.json").read_bytes()==original


def test_simultaneous_identical_model_calls_reuse_one_response():
    entered, release = threading.Event(), threading.Event()
    class Client:
        provider,model="fake","one"
        calls=0
        def generate_json(self, system, parts):
            self.calls+=1; entered.set()
            assert release.wait(5)
            return LLMResponse('{"ok":true}',{"ok":True},self.model,0,{})
    with tempfile.TemporaryDirectory() as folder, ThreadPoolExecutor(max_workers=2) as pool:
        inner=Client(); client=CachedClient(inner,Cache(folder))
        first=pool.submit(client.generate_json,"same",["drawing"])
        assert entered.wait(2)
        second=pool.submit(client.generate_json,"same",["drawing"])
        release.set()
        assert first.result().data==second.result().data
        assert inner.calls==1
        client.cache.close()
