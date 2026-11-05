"""One reusable native OCR subprocess, with serialized requests and crash recovery."""
import atexit
import json
from pathlib import Path
import queue
import subprocess
import threading
import time


class OcrWorker:
    def __init__(self):
        self._lock=threading.Lock()
        self._process=None
        self._replies=None

    def _start(self):
        helper=Path(__file__).resolve().parents[2]/"scripts/windows_ocr.ps1"
        self._process=subprocess.Popen(["powershell.exe","-NoProfile","-NonInteractive","-ExecutionPolicy","Bypass",
                                        "-File",str(helper),"-Server"],
                                       stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                                       creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        self._replies=queue.Queue()
        stream,replies=self._process.stdout,self._replies
        def receive():
            try:
                for line in iter(stream.readline,b""): replies.put(line)
            finally:
                replies.put(None)
                stream.close()
        threading.Thread(target=receive,daemon=True,name="native-ocr-output").start()

    def _stop(self):
        process,self._process=self._process,None
        if process is not None:
            if process.poll() is None: process.kill()
            process.wait(timeout=5)
            try: process.stdin.close()
            except OSError: pass

    def close(self):
        with self._lock: self._stop()

    def request(self,payload,timeout=30):
        started=time.monotonic()
        if not self._lock.acquire(timeout=timeout): raise TimeoutError("OCR worker queue timed out")
        try:
            try:
                if self._process is None or self._process.poll() is not None:
                    self._stop();self._start()
                self._process.stdin.write((json.dumps(payload)+"\n").encode("utf-8"))
                self._process.stdin.flush()
                line=self._replies.get(timeout=max(.001,timeout-(time.monotonic()-started)))
                if line is None: raise RuntimeError("Native OCR worker exited")
                reply=json.loads(line.decode("utf-8-sig"))
                if "error" in reply: raise RuntimeError("Windows OCR failed: "+str(reply["error"])[:1000])
                return reply
            except queue.Empty:
                self._stop()
                raise TimeoutError("Windows OCR timed out") from None
            except (OSError,ValueError,RuntimeError):
                self._stop()
                raise
        finally: self._lock.release()


worker=OcrWorker()
atexit.register(worker.close)
