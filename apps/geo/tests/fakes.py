"""Test helpers: a fake upstream that serves files with ETag/Last-Modified."""
import hashlib
import http.server
import os
import sys
import threading
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "..", "the-intersect")
COMMON = os.path.join(HERE, "..", "..", "library", "common")
REGISTER = os.path.join(HERE, "..", "..", "library", "register.json")
for path in (APP, COMMON):
    if path not in sys.path:
        sys.path.insert(0, path)

try:
    from osgeo import gdal  # noqa: F401

    HAVE_GDAL = True
except ImportError:
    HAVE_GDAL = False


class FakeUpstream:
    """Serves self.files ({path: bytes}); ETag is the content hash."""

    def __init__(self):
        self.files = {}
        self.handlers = {}  # path -> fn(query dict) -> bytes
        self.requests = []  # (method, path, user-agent)
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _head(self, data):
                self.send_header("ETag", '"' + hashlib.sha256(data).hexdigest()[:16] + '"')
                self.send_header("Last-Modified", "Wed, 21 Oct 2026 00:00:00 GMT")
                self.send_header("Content-Length", str(len(data)))

            def _serve(self, body):
                fake.requests.append((self.command, self.path, self.headers.get("User-Agent", "")))
                data = fake.files.get(self.path)
                if data is None:
                    # Callables answer by path prefix (ArcGIS-style queries).
                    base, _, query = self.path.partition("?")
                    handler = fake.handlers.get(base)
                    data = handler(urllib.parse.parse_qs(query)) if handler else None
                if data is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self._head(data)
                self.end_headers()
                if body:
                    self.wfile.write(data)

            def do_HEAD(self):
                self._serve(False)

            def do_GET(self):
                self._serve(True)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def gets(self, path=None):
        return [p for m, p, _ in self.requests if m == "GET" and (path is None or p == path)]

    def close(self):
        self.server.shutdown()
