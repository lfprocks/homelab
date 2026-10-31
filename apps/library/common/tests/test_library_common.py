"""Tests for the library core shared by the kiwix and geo jobs."""
import http.server
import os
import shutil
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

import library_common as lc  # noqa: E402

REGISTER = os.path.join(HERE, "..", "..", "register.json")


class RegisterTest(unittest.TestCase):
    def setUp(self):
        self.register = lc.load_register(REGISTER)

    def test_repo_register_valid(self):
        self.assertEqual(lc.validate_register(self.register), [])

    def test_dataset_children_in_skeleton(self):
        dirs = lc.skeleton_dirs(self.register, "library")
        self.assertIn("03_Geo/05_Charts/01_Source", dirs)
        self.assertIn("03_Geo/05_Charts/02_ATAK", dirs)
        self.assertEqual(dirs, sorted(dirs))

    def test_shares_are_separate(self):
        library = lc.skeleton_dirs(self.register, "library")
        personal = lc.skeleton_dirs(self.register, "personal")
        self.assertNotIn("03_Geo/07_Overlays/01_Ham_Repeaters", library)
        self.assertIn("03_Geo/07_Overlays/01_Ham_Repeaters/02_ATAK", personal)
        self.assertNotIn("02_Kiwix", personal)
        self.assertNotIn("Ham_Repeaters", lc.render_readme(self.register, "library"))
        self.assertNotIn("02_Kiwix", lc.render_readme(self.register, "personal"))

    def test_reserved_not_in_skeleton(self):
        dirs = lc.skeleton_dirs(self.register, "library")
        self.assertNotIn("03_Geo/01_Imagery", dirs)
        self.assertNotIn("04_Datasets", dirs)
        self.assertIn("03_Geo/07_Overlays/01_Surveillance", dirs)

    def test_wanted_folder_must_be_in_use(self):
        self.assertTrue(lc.validate_register(self.register, wanted_folders=["03_Geo/01_Imagery"]))
        self.assertTrue(lc.validate_register(self.register, wanted_folders=["02_Kiwix/99_Nope"]))
        self.assertEqual(lc.validate_register(self.register, wanted_folders=["02_Kiwix/01_Wikipedia"]), [])

    def test_bad_share_or_kind_rejected(self):
        bad = self.register + [{"path": "05_X", "state": "in-use", "purpose": "x", "share": "nope"}]
        self.assertTrue(lc.validate_register(bad))
        bad = self.register + [{"path": "05_X", "state": "in-use", "purpose": "x", "kind": "nope"}]
        self.assertTrue(lc.validate_register(bad))

    def test_readme_identical_for_same_register(self):
        self.assertEqual(lc.render_readme(self.register, "library"), lc.render_readme(list(self.register), "library"))
        self.assertIn("Numbers are never reused, renumbered or renamed.", lc.render_readme(self.register, "library"))


class DownloadTest(unittest.TestCase):
    DATA = b"x" * 5000

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.seen = []
        data, seen = self.DATA, self.seen

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                seen.append((self.headers.get("Range"), self.headers.get("User-Agent")))
                rng = self.headers.get("Range")
                if rng:
                    start = int(rng.split("=")[1].split("-")[0])
                    if start >= len(data):
                        self.send_response(416)
                        self.end_headers()
                        return
                    self.send_response(206)
                    body = data[start:]
                else:
                    self.send_response(200)
                    body = data
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/f"

    def tearDown(self):
        self.server.shutdown()
        shutil.rmtree(self.dir)

    def test_download_resume_and_416(self):
        part = os.path.join(self.dir, "f.part")
        with open(part, "wb") as f:
            f.write(self.DATA[:1000])
        lc.download(self.url, part, "test-agent/1.0")
        self.assertEqual(open(part, "rb").read(), self.DATA)
        self.assertEqual(self.seen[-1], ("bytes=1000-", "test-agent/1.0"))
        lc.download(self.url, part, "test-agent/1.0")  # complete part: 416, no error
        self.assertEqual(open(part, "rb").read(), self.DATA)

    def test_write_atomic(self):
        path = os.path.join(self.dir, "a.txt")
        lc.write_atomic(path, "hello")
        self.assertEqual(open(path).read(), "hello")
        self.assertFalse(os.path.exists(path + ".tmp"))


if __name__ == "__main__":
    unittest.main()
