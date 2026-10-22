"""The geo_sync command line (python3 -m geo_sync sync|fire|inbox)."""
import glob
import json
import os
import shutil
import tempfile
import unittest

from fakes import APP, REGISTER


class MainTest(unittest.TestCase):
    def test_sync_runs_from_environment(self):
        from geo_sync import __main__ as cli
        tmp = tempfile.mkdtemp()
        try:
            for d in ("library", "personal", "inbox", "scratch"):
                os.makedirs(os.path.join(tmp, d))
            sources = os.path.join(tmp, "sources.json")
            json.dump([{"id": "usgs_topo", "cls": "live", "folder": "03_Geo/08_Online_Sources", "kind": "none",
                        "url": "", "version_from": "field", "convert": "live-xml", "area": "region",
                        "license": "public domain", "options": {"name": "USGS Topo", "max_zoom": 16,
                                                                "template": "https://example.test/{$z}/{$y}/{$x}"}}],
                      open(sources, "w"))
            env = {"GEO_LIBRARY_ROOT": f"{tmp}/library", "GEO_PERSONAL_ROOT": f"{tmp}/personal",
                   "GEO_INBOX_ROOT": f"{tmp}/inbox", "GEO_SCRATCH": f"{tmp}/scratch", "GEO_SOURCES": sources,
                   "LIBRARY_REGISTER": REGISTER, "GEO_BUDGET_SECONDS": "60"}
            self.assertEqual(cli.main(["sync"], env), 0)
            self.assertTrue(os.path.exists(f"{tmp}/library/03_Geo/08_Online_Sources/usgs_topo.xml"))
            self.assertTrue(os.path.exists(f"{tmp}/personal/00_Meta/README.md"))
        finally:
            shutil.rmtree(tmp)

    def test_unknown_command_is_usage_error(self):
        from geo_sync import __main__ as cli
        self.assertEqual(cli.main(["nope"], {}), 2)

    def test_no_flux_substitution_syntax(self):
        # Flux postBuild would rewrite a dollar-brace sequence in the ConfigMap.
        for path in glob.glob(os.path.join(APP, "geo_sync", "*.py")) + [os.path.join(APP, "sources.json")]:
            self.assertNotIn("$" + "{", open(path).read(), path)


if __name__ == "__main__":
    unittest.main()
