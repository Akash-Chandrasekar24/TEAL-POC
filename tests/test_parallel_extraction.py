import base64
import io
import threading
import time
import tempfile
from contextvars import ContextVar
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

import fakes  # noqa: F401
from PIL import Image
from app.parallel import ordered_map
from app import trace
from app.agent.harness import current_harness
from app.agent.workers import ExtractionAgent
from app.perception.extract import PageRead
from app.web import server


def test_bounded_parallel_order_and_context():
    context = ContextVar("test_parallel_context",default=None)
    token=context.set("drawing-job")
    lock=threading.Lock(); active=0; peak=0
    barrier=threading.Barrier(2)
    def task(index):
        nonlocal active,peak
        assert context.get()=="drawing-job"
        with lock:
            active+=1; peak=max(peak,active)
        barrier.wait(timeout=3)
        time.sleep(.01 if index%2 else .03)
        with lock: active-=1
        return index
    try:
        assert ordered_map(task,range(4))==[0,1,2,3]
        assert peak==2
    finally: context.reset(token)


def test_four_page_reads_overlap_with_isolated_ids_and_progress():
    buffer=io.BytesIO(); Image.new("RGB",(120,100),"white").save(buffer,"PNG")
    upload=base64.b64encode(buffer.getvalue()).decode()
    barrier=threading.Barrier(2)
    completed=[]
    def read_page(worker,page_no,png,boxes):
        assert trace.current().run_id=="abcdef123456"
        assert current_harness() is not None
        barrier.wait(timeout=3)
        time.sleep(.01 if page_no%2==0 else .03)
        return PageRead(page_no,[],[],{},[],1)
    with tempfile.TemporaryDirectory() as folder:
        root=Path(folder)
        with patch.multiple(server,RESULTS=root/"results",UPLOADS=root/"uploads",RUNS=root/"runs"), \
                patch("app.llm.make_client",return_value=SimpleNamespace(model="fake",provider="fake")), \
                patch.object(server,"segment_page",return_value=[]), \
                patch.object(ExtractionAgent,"read_page",read_page):
            result=server.run("abcdef123456",[{"name":f"page{i}.png","data":upload} for i in range(4)],"domo",True,on_progress=completed.append)
            assert [p["no"] for p in result["pages"]]==[1,2,3,4]
            assert [p["read"]["page"] for p in result["pages"]]==[1,2,3,4]
            assert completed[0]["event"]=="started" and completed[0]["total"]==4
            ready=[event["page"]["no"] for event in completed if event["event"]=="page"]
            assert set(ready)=={1,2,3,4} and ready[0]==2
            assert result["harness"]["actions"]==4


def test_parallel_failure_is_not_a_partial_success():
    def task(index):
        if index==1: raise ValueError("bad drawing")
        return index
    try:
        ordered_map(task,range(4))
    except ValueError as error:
        assert str(error)=="bad drawing"
    else:
        raise AssertionError("A failed page must not produce a successful drawing set")
