"""Page progress must arrive before the job finishes, including late errors."""
import http.client
import json
import threading
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import fakes  # noqa: F401
from app.web import server


def test_extraction_stream_flushes_before_completion_and_reports_errors():
    release = threading.Event()
    fail = False

    def run(rid, files, llm, read, **kwargs):
        emit = kwargs["on_progress"]
        emit({"event": "started", "id": rid, "total": 2})
        emit({"event": "page", "id": rid, "total": 2, "page": {"no": 2, "text": "Ø38 ±0.1"}})
        assert release.wait(timeout=5), "Client must receive progress before extraction finishes"
        if fail:
            raise ValueError("Page 1 could not be read")
        return {"id": rid, "pages": [{"no": 1}, {"no": 2}]}

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        with patch.object(server, "run", run):
            for fail in (False, True):
                release.clear()
                connection = http.client.HTTPConnection(*httpd.server_address, timeout=3)
                try:
                    connection.request("POST", "/api/read", json.dumps({"stream": True, "read": True}), {"Content-Type": "application/json"})
                    response = connection.getresponse()
                    assert response.status == 200
                    assert response.getheader("Content-Type").startswith("application/x-ndjson")
                    assert json.loads(response.readline())["event"] == "started"
                    page = json.loads(response.readline())
                    assert page["page"] == {"no": 2, "text": "Ø38 ±0.1"}
                    release.set()
                    final = json.loads(response.readline())
                    assert final["event"] == ("error" if fail else "result")
                    if fail:
                        assert "Page 1" in final["error"] and final["id"]
                    else:
                        assert [p["no"] for p in final["result"]["pages"]] == [1, 2]
                    assert response.read() == b""
                finally:
                    release.set()
                    connection.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
