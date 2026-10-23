"""The map push's allowlist: only finished ATAK outputs leave the cluster."""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "..", "the-intersect")
if APP not in sys.path:
    sys.path.insert(0, APP)

import push  # noqa: E402

KEPT = [
    "03_Geo/01_Basemaps/02_ATAK/washington_topo.mbtiles",
    "03_Geo/07_Overlays/03_Fire/02_ATAK/perimeters.kmz",
    "03_Geo/02_Charts/01_Sectional/02_ATAK/seattle.xml",
    "03_Geo/a/b/c/02_ATAK/deep.KMZ",
]

REFUSED = [
    # sources, metadata and drops are never pushed
    "03_Geo/07_Overlays/03_Fire/01_Source/perimeters.geojson",
    "03_Geo/07_Overlays/03_Fire/01_Source/02_ATAK/perimeters.kmz",
    "03_Geo/01_Basemaps/00_Meta/02_ATAK/manifest.xml",
    "03_Geo/01_Incoming/02_ATAK/drop.kmz",
    # GeoPackages are sources, whatever folder they are in
    "03_Geo/01_Basemaps/02_ATAK/washington.gpkg",
    "03_Geo/01_Basemaps/02_ATAK/washington.gpkg.kmz",
    # ham repeaters are personal-class, in any spelling
    "03_Geo/07_Overlays/01_Ham_Repeaters/02_ATAK/repeaters.kmz",
    "03_Geo/07_Overlays/04_Radio/02_ATAK/ham-repeaters_wa.kmz",
    "03_Geo/07_Overlays/04_Radio/02_ATAK/HAM_REPEATERS.mbtiles",
    # wrong extension, wrong folder, wrong depth, outside 03_Geo
    "03_Geo/01_Basemaps/02_ATAK/readme.txt",
    "03_Geo/01_Basemaps/02_ATAK/sub/nested.kmz",
    "03_Geo/01_Basemaps/washington.mbtiles",
    "03_Geo/02_ATAK.kmz",
    "02_ATAK/top.kmz",
    "04_Kiwix/02_ATAK/wiki.xml",
    # hidden files and anything that is not a plain relative path
    "03_Geo/01_Basemaps/02_ATAK/.partial.mbtiles",
    "/03_Geo/01_Basemaps/02_ATAK/abs.kmz",
    "03_Geo/../03_Geo/01_Basemaps/02_ATAK/up.kmz",
    "03_Geo/01_Basemaps/02_ATAK/../02_ATAK/up.kmz",
    "03_Geo//01_Basemaps/02_ATAK/double.kmz",
    "03_Geo/01_Basemaps/02_ATAK/new\nline.kmz",
    "",
]


class AllowlistTest(unittest.TestCase):
    def test_allowlist_keeps_atak_outputs(self):
        self.assertEqual(push.allowlist(KEPT), sorted(KEPT))
        # sorted and de-duplicated
        self.assertEqual(push.allowlist(list(reversed(KEPT)) + KEPT), sorted(KEPT))

    def test_push_refuses_non_allowlisted(self):
        for path in REFUSED:
            with self.subTest(path=path):
                self.assertEqual(push.allowlist([path]), [])
        self.assertEqual(push.allowlist(REFUSED + KEPT), sorted(KEPT))


class StageTest(unittest.TestCase):
    """main() mirrors the allowlisted files as symlinks into a staging tree."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.library = os.path.join(self.tmp, "library")
        self.stage = os.path.join(self.tmp, "stage")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def write(self, rel):
        path = os.path.join(self.library, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(rel)

    def staged(self):
        out = []
        for root, _dirs, files in os.walk(self.stage):
            for name in files:
                out.append(os.path.relpath(os.path.join(root, name), self.stage))
        return sorted(out)

    def test_stage_holds_only_allowlisted_files(self):
        for rel in KEPT + [p for p in REFUSED if p and not p.startswith("/") and ".." not in p
                           and "//" not in p and "\n" not in p]:
            self.write(rel)
        # a symlink inside the library is never followed, even when its name fits
        os.symlink("/etc/passwd", os.path.join(self.library, "03_Geo/01_Basemaps/02_ATAK/link.kmz"))
        self.assertEqual(push.main([self.library, self.stage]), 0)
        self.assertEqual(self.staged(), sorted(KEPT))
        for rel in KEPT:
            link = os.path.join(self.stage, rel)
            self.assertTrue(os.path.islink(link))
            self.assertEqual(os.readlink(link), os.path.join(os.path.abspath(self.library), rel))

    def test_empty_library_is_refused(self):
        # An unmounted or empty share must never reach rsync --delete-after.
        os.makedirs(os.path.join(self.library, "03_Geo"))
        self.assertNotEqual(push.main([self.library, self.stage]), 0)
        self.assertNotEqual(push.main([os.path.join(self.tmp, "missing"), self.stage]), 0)

    def test_existing_stage_is_refused(self):
        self.write(KEPT[0])
        os.makedirs(self.stage)
        self.assertNotEqual(push.main([self.library, self.stage]), 0)


if __name__ == "__main__":
    unittest.main()
