"""Inbox sweep and the personal RepeaterBook overlay."""
import datetime
import os
import shutil
import tempfile
import unittest

from fakes import HAVE_GDAL, HERE, REGISTER

NOW = datetime.datetime(2026, 10, 22, 12, 0, tzinfo=datetime.timezone.utc)
SAMPLE = os.path.join(HERE, "fixtures", "repeaterbook_sample.csv")


class RecogniseTest(unittest.TestCase):
    def test_header_mapping_tolerates_variants(self):
        from geo_sync import inbox
        m = inbox.column_map(["Frequency", "Input", "PL", "Callsign", "Latitude", "Longitude", "Nearest City"])
        self.assertEqual((m["output"], m["tone"], m["call"], m["lat"], m["lon"]),
                         ("Frequency", "PL", "Callsign", "Latitude", "Longitude"))
        self.assertIsNone(inbox.column_map(["name", "value"]))


@unittest.skipUnless(HAVE_GDAL, "needs GDAL")
class InboxTest(unittest.TestCase):
    def setUp(self):
        import library_common as lc
        from geo_sync import core, inbox
        self.core, self.inbox = core, inbox
        self.tmp = tempfile.mkdtemp()
        self.ctx = core.Ctx(*(os.path.join(self.tmp, d) for d in ("library", "personal", "inbox", "scratch")),
                            now=NOW, budget_s=60, user_agent="t")
        for d in ("library", "personal", "inbox", "scratch"):
            os.makedirs(os.path.join(self.tmp, d))
        self.register = lc.load_register(REGISTER)
        core.prepare(self.register, self.ctx)
        self.atak = os.path.join(self.ctx.personal_root, "03_Geo/07_Overlays/01_Ham_Repeaters/02_ATAK")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def drop(self, name, text=None):
        path = os.path.join(self.ctx.inbox_root, name)
        if text is None:
            shutil.copyfile(SAMPLE, path)
        else:
            open(path, "w").write(text)
        return path

    def test_repeaterbook_csv_to_points(self):
        self.drop("RB_export.csv")
        outcomes = self.inbox.sweep(self.ctx)
        self.assertEqual(outcomes, [self.core.Outcome.UPDATED])
        files = os.listdir(self.atak)
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].startswith("ham-repeaters_radius_2026-10-22."))
        from osgeo import ogr
        path = os.path.join(self.atak, files[0])
        ds = ogr.Open(path if not path.endswith(".kmz") else "/vsizip/" + path + "/doc.kml")
        names = sorted(f.GetField("Name") for layer in ds for f in layer)
        self.assertEqual(names, ["K7TEST 146.820", "N7TEST 147.000", "W7TEST 442.875"])

    def test_inbox_file_removed_after_success(self):
        dropped = self.drop("RB_export.csv")
        self.inbox.sweep(self.ctx)
        self.assertFalse(os.path.exists(dropped))
        src = os.listdir(os.path.join(self.ctx.personal_root, "03_Geo/07_Overlays/01_Ham_Repeaters/01_Source"))
        self.assertEqual(src, ["RB_export_2026-10-22.csv"])

    def test_unrecognised_inbox_file_left_in_place(self):
        dropped = self.drop("notes.csv", "name,value\na,1\n")
        self.assertEqual(self.inbox.sweep(self.ctx), [self.core.Outcome.WARNED])
        self.assertTrue(os.path.exists(dropped))
        self.assertEqual(os.listdir(self.atak), [])

    def test_malformed_repeaterbook_csv_left_in_place(self):
        dropped = self.drop("bad.csv", "Output Freq,Call,Lat,Long\n146.82,K7X,not-a-number,also-bad\n")
        self.assertEqual(self.inbox.sweep(self.ctx), [self.core.Outcome.WARNED])
        self.assertTrue(os.path.exists(dropped))
        self.assertEqual(os.listdir(self.atak), [])

    def test_personal_never_in_library_root(self):
        self.drop("RB_export.csv")
        self.inbox.sweep(self.ctx)
        for dirpath, _, names in os.walk(self.ctx.library_root):
            for name in names:
                self.assertNotIn("ham", name.lower(), os.path.join(dirpath, name))
                self.assertNotIn("RB_export", name)

    def test_personal_readme_shows_last_import(self):
        self.drop("RB_export.csv")
        self.inbox.sweep(self.ctx)
        self.core.prepare(self.register, self.ctx)
        readme = open(os.path.join(self.ctx.personal_root, "00_Meta/README.md")).read()
        self.assertIn("ham_repeaters: last imported 2026-10-22", readme)


if __name__ == "__main__":
    unittest.main()
