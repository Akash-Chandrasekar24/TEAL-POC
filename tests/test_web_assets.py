"""Static compression must preserve bytes, invalidate edits and respect negotiation."""
import gzip
import http.client
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import fakes  # noqa: F401
from app.web import server


def test_static_compression_revalidation_and_updates():
    with tempfile.TemporaryDirectory() as folder, patch.object(server, "HERE", Path(folder)):
        path = Path(folder) / "ui.js"
        original = b"const drawing = 'dimensions';\n" * 100
        path.write_bytes(original)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        def get(headers):
            connection = http.client.HTTPConnection(*httpd.server_address, timeout=5)
            try:
                connection.request("GET", "/ui.js?v=1", headers=headers)
                response = connection.getresponse()
                return response.status, dict(response.getheaders()), response.read()
            finally:
                connection.close()
        try:
            status, headers, body = get({"Accept-Encoding": "gzip;q=0.5"})
            assert status == 200 and gzip.decompress(body) == original
            assert headers["Content-Encoding"] == "gzip" and len(body) < len(original)
            assert headers["Cache-Control"] == "no-cache" and headers["Vary"] == "Accept-Encoding"
            tag = headers["ETag"]
            status, _, body = get({"Accept-Encoding": "gzip", "If-None-Match": tag})
            assert status == 304 and not body
            status, plain, body = get({"Accept-Encoding": "gzip;q=0", "If-None-Match": tag})
            assert status == 200 and body == original and "Content-Encoding" not in plain
            assert plain["ETag"] != tag
            path.write_bytes(original + b"// updated\n")
            status, updated, body = get({"Accept-Encoding": "gzip", "If-None-Match": tag})
            assert status == 200 and updated["ETag"] != tag
            assert gzip.decompress(body) == path.read_bytes()
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)
