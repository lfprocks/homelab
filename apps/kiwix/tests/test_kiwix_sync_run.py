"""End-to-end tests for kiwix_sync.run against a fake download.kiwix.org."""
import datetime
import hashlib
import http.server
import os
import shutil
import stat
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "..", "the-intersect")
sys.path.insert(0, APP)

import kiwix_sync  # noqa: E402

NOW = datetime.datetime(2026, 10, 22, 5, 0, 0, tzinfo=datetime.timezone.utc)

WIKI = "wikipedia_en_all_maxi_2026-08.zim"
WIKT = "wiktionary_en_all_nopic_2026-08.zim"
SF_OLD = "serverfault.com_en_all_2026-02.zim"
SF_NEW = "serverfault.com_en_all_2026-10.zim"


class FakeKiwix:
    """Serves zim/<dir>/ listings, files and .sha256 files from a dict."""

    def __init__(self):
        self.files = {}  # url path -> bytes
        self.requests = []  # (path, range header)
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                fake.requests.append((self.path, self.headers.get("Range")))
                if self.path.endswith("/"):
                    names = sorted(p[len(self.path):] for p in fake.files if p.startswith(self.path))
                    body = "".join(f'<a href="{n}">{n}</a>\n' for n in names).encode()
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(body)
                    return
                data = fake.files.get(self.path)
                if data is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                rng = self.headers.get("Range")
                if rng:
                    start = int(rng.split("=")[1].split("-")[0])
                    if start >= len(data):
                        # What download.kiwix.org's mirrors answer for a
                        # range at or past the end of the file.
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{len(data)}")
                        self.end_headers()
                        return
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
                    data = data[start:]
                else:
                    self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}/"

    def publish(self, upstream_dir, name, data, sha=None):
        self.files[f"/{upstream_dir}/{name}"] = data
        digest = sha or hashlib.sha256(data).hexdigest()
        self.files[f"/{upstream_dir}/{name}.sha256"] = f"{digest}  {name}\n".encode()

    def zim_gets(self):
        return [p for p, _ in self.requests if p.endswith(".zim")]

    def close(self):
        self.server.shutdown()


class RunTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.tools = tempfile.mkdtemp()
        self.fake = FakeKiwix()
        self.fake.publish("zim/wikipedia", WIKI, b"W" * 5000)
        self.fake.publish("zim/wiktionary", WIKT, b"T" * 3000)
        self.fake.publish("zim/stack_exchange", SF_OLD, b"S" * 2000)
        self.config = kiwix_sync.load_config(os.path.join(APP, "library.json"))
        self.manage = self.fake_manage(exit_code=0)

    def tearDown(self):
        self.fake.close()
        shutil.rmtree(self.root)
        shutil.rmtree(self.tools)

    def fake_manage(self, exit_code):
        # kiwix-manage <library.xml> add <zim>...: record the zims in the library file.
        path = os.path.join(self.tools, f"kiwix-manage-{exit_code}")
        with open(path, "w") as f:
            f.write(f'#!/bin/sh\nlib="$1"; shift; shift\nfor z in "$@"; do echo "$z" >> "$lib"; done\nexit {exit_code}\n')
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        return path

    def run_sync(self, manage=None):
        return kiwix_sync.run(self.config, self.root, self.fake.base_url, manage or self.manage, NOW)

    def path(self, *parts):
        return os.path.join(self.root, *parts)

    def read(self, *parts):
        with open(self.path(*parts), "rb") as f:
            return f.read()

    def test_first_run_downloads_and_catalogues(self):
        self.assertEqual(self.run_sync(), 0)
        self.assertEqual(self.read("02_Kiwix", "01_Wikipedia", WIKI), b"W" * 5000)
        self.assertTrue(os.path.exists(self.path("02_Kiwix", "02_Wiktionary", WIKT)))
        self.assertTrue(os.path.exists(self.path("02_Kiwix", "03_Stack_Exchange", SF_OLD)))
        library = self.read("02_Kiwix", "library.xml").decode()
        for name in (WIKI, WIKT, SF_OLD):
            self.assertIn(name, library)
        manifest = self.read("00_Meta", "02_Kiwix.manifest.tsv").decode().splitlines()
        self.assertEqual(manifest[0] + "\n", kiwix_sync.MANIFEST_HEADER)
        self.assertEqual(len(manifest), 4)
        self.assertEqual(os.listdir(self.path("01_Incoming")), [])
        self.assertTrue(os.path.exists(self.path("00_Meta", "README.md")))
        self.assertFalse(os.path.exists(self.path("03_Geo")))

    def test_second_run_is_noop(self):
        self.assertEqual(self.run_sync(), 0)
        self.fake.requests.clear()
        self.assertEqual(self.run_sync(), 0)
        self.assertEqual(self.fake.zim_gets(), [])
        manifest = self.read("00_Meta", "02_Kiwix.manifest.tsv").decode().splitlines()
        self.assertEqual(len(manifest), 4)

    def test_resume_continues_partial_download(self):
        os.makedirs(self.path("01_Incoming"))
        with open(self.path("01_Incoming", WIKI + ".part"), "wb") as f:
            f.write(b"W" * 1234)
        self.assertEqual(self.run_sync(), 0)
        self.assertIn((f"/zim/wikipedia/{WIKI}", "bytes=1234-"), self.fake.requests)
        self.assertEqual(self.read("02_Kiwix", "01_Wikipedia", WIKI), b"W" * 5000)

    def test_complete_part_is_verified_not_wedged(self):
        # The pod died after the last byte but before the move: the next run
        # gets 416 for its range and must verify the part, not fail forever.
        os.makedirs(self.path("01_Incoming"))
        with open(self.path("01_Incoming", WIKI + ".part"), "wb") as f:
            f.write(b"W" * 5000)
        self.assertEqual(self.run_sync(), 0)
        self.assertEqual(self.read("02_Kiwix", "01_Wikipedia", WIKI), b"W" * 5000)
        self.assertEqual(os.listdir(self.path("01_Incoming")), [])

    def test_complete_but_corrupt_part_is_discarded(self):
        os.makedirs(self.path("01_Incoming"))
        with open(self.path("01_Incoming", WIKI + ".part"), "wb") as f:
            f.write(b"Z" * 5000)
        self.assertEqual(self.run_sync(), 1)
        self.assertFalse(os.path.exists(self.path("02_Kiwix", "01_Wikipedia", WIKI)))
        self.assertEqual(os.listdir(self.path("01_Incoming")), [])

    def test_checksum_mismatch_leaves_served_copy(self):
        self.assertEqual(self.run_sync(), 0)
        old_sf = self.read("02_Kiwix", "03_Stack_Exchange", SF_OLD)
        old_library = self.read("02_Kiwix", "library.xml")
        self.fake.publish("zim/stack_exchange", SF_NEW, b"N" * 2100, sha="0" * 64)
        wiki_new = "wikipedia_en_all_maxi_2026-10.zim"
        self.fake.publish("zim/wikipedia", wiki_new, b"X" * 5100)
        self.assertEqual(self.run_sync(), 1)
        self.assertEqual(self.read("02_Kiwix", "03_Stack_Exchange", SF_OLD), old_sf)
        self.assertFalse(os.path.exists(self.path("02_Kiwix", "03_Stack_Exchange", SF_NEW)))
        self.assertEqual(os.listdir(self.path("01_Incoming")), [])
        # The other book still updated, and the catalogue serves it while
        # keeping the old serverfault build.
        self.assertTrue(os.path.exists(self.path("02_Kiwix", "01_Wikipedia", wiki_new)))
        library = self.read("02_Kiwix", "library.xml").decode()
        self.assertIn(wiki_new, library)
        self.assertIn(SF_OLD, library)
        self.assertNotEqual(library.encode(), old_library)

    def test_retention_waits_for_catalogue(self):
        folder = self.path("02_Kiwix", "03_Stack_Exchange")
        os.makedirs(folder)
        for old in ("serverfault.com_en_all_2025-06.zim", "serverfault.com_en_all_2025-12.zim"):
            with open(os.path.join(folder, old), "wb") as f:
                f.write(b"old")
        self.assertEqual(self.run_sync(manage=self.fake_manage(exit_code=1)), 1)
        self.assertEqual(
            sorted(os.listdir(folder)),
            ["serverfault.com_en_all_2025-06.zim", "serverfault.com_en_all_2025-12.zim", SF_OLD],
        )
        self.assertEqual(self.run_sync(), 0)
        self.assertEqual(sorted(os.listdir(folder)), ["serverfault.com_en_all_2025-12.zim", SF_OLD])

    def test_empty_catalogue_written_before_downloads(self):
        # kiwix-serve exits without a library.xml, so one must exist before the
        # first (hours-long) download starts.
        seen = []
        original = self.fake.server.RequestHandlerClass.do_GET
        root = self.root

        def spy(handler):
            if handler.path.endswith(".zim"):
                seen.append(os.path.exists(os.path.join(root, "02_Kiwix", "library.xml")))
            original(handler)

        self.fake.server.RequestHandlerClass.do_GET = spy
        self.assertEqual(self.run_sync(), 0)
        self.assertTrue(seen)
        self.assertTrue(all(seen))

    def test_empty_catalogue_when_no_books(self):
        self.fake.files.clear()
        self.assertEqual(self.run_sync(), 1)
        library = self.read("02_Kiwix", "library.xml").decode()
        self.assertIn("<library", library)
        self.assertNotIn(".zim", library)

    def test_invalid_config_fails_before_io(self):
        self.config["wanted"][0]["folder"] = "02_Kiwix/09_Nope"
        self.assertEqual(self.run_sync(), 1)
        self.assertEqual(os.listdir(self.root), [])
        self.assertEqual(self.fake.requests, [])


if __name__ == "__main__":
    unittest.main()
