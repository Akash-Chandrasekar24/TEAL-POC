"""Exercise the persistent worker protocol without requiring the native OCR engine."""
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import fakes  # noqa: F401
from app.perception.ocr_worker import OcrWorker


HELPER='''import sys,json,time
for line in sys.stdin:
    item=json.loads(line)
    if item.get("exit"): break
    if item.get("slow"): time.sleep(2)
    print(json.dumps({"echo":item}),flush=True)
'''


def test_worker_reuses_process_and_serializes_concurrent_requests():
    real_popen=subprocess.Popen
    def start(*args,**kwargs):
        return real_popen([sys.executable,"-u","-c",HELPER],**kwargs)
    worker=OcrWorker()
    with patch("app.perception.ocr_worker.subprocess.Popen",side_effect=start) as launches:
        try:
            assert worker.request({"ping":True})=={"echo":{"ping":True}}
            with ThreadPoolExecutor(max_workers=4) as pool:
                replies=list(pool.map(lambda n:worker.request({"n":n}),range(8)))
            assert replies==[{"echo":{"n":n}} for n in range(8)]
            assert launches.call_count==1
        finally: worker.close()
    assert worker._process is None


def test_worker_restarts_after_crash_and_timeout_without_reusing_stale_reply():
    real_popen=subprocess.Popen
    def start(*args,**kwargs):
        return real_popen([sys.executable,"-u","-c",HELPER],**kwargs)
    worker=OcrWorker()
    with patch("app.perception.ocr_worker.subprocess.Popen",side_effect=start) as launches:
        try:
            worker.request({"ping":True})
            try: worker.request({"slow":True},timeout=.05)
            except TimeoutError: pass
            else: raise AssertionError("Slow OCR did not time out")
            assert worker._process is None
            assert worker.request({"new":True})=={"echo":{"new":True}}
            try: worker.request({"exit":True})
            except RuntimeError: pass
            else: raise AssertionError("An exited worker returned success")
            assert worker.request({"recovered":True})=={"echo":{"recovered":True}}
            assert launches.call_count==3
        finally: worker.close()
