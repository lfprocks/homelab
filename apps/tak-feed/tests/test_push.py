"""The map push's allowlist: only finished ATAK outputs leave the cluster."""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "..", "the-intersect")
REGISTER = os.path.join(HERE, "..", "..", "library", "register.json")
if APP not in sys.path:
    sys.path.insert(0, APP)

import push  # noqa: E402

KEPT = [
    "03_Geo/06_Basemap/02_ATAK/washington_topo.mbtiles",
    "03_Geo/07_Overlays/05_Hazards/02_ATAK/perimeters.kmz",
    "03_Geo/05_Charts/02_ATAK/seattle.xml",
    "03_Geo/07_Overlays/08_Towers/02_ATAK/towers.KMZ",
]

REFUSED = [
    # sources, metadata and drops are never pushed
    "03_Geo/07_Overlays/05_Hazards/01_Source/perimeters.geojson",
    "03_Geo/07_Overlays/05_Hazards/01_Source/02_ATAK/perimeters.kmz",
    "03_Geo/06_Basemap/00_Meta/02_ATAK/manifest.xml",
    "03_Geo/01_Incoming/02_ATAK/drop.kmz",
    # GeoPackages are sources, whatever folder they are in
    "03_Geo/06_Basemap/02_ATAK/washington.gpkg",
    "03_Geo/06_Basemap/02_ATAK/washington.gpkg.kmz",
    # ham repeaters are personal-class, in any spelling
    "03_Geo/07_Overlays/01_Ham_Repeaters/02_ATAK/repeaters.kmz",
    "03_Geo/07_Overlays/04_Emergency_Services/02_ATAK/ham-repeaters_wa.kmz",
    "03_Geo/07_Overlays/04_Emergency_Services/02_ATAK/HAM_REPEATERS.mbtiles",
    "03_Geo/07_Overlays/08_Towers/02_ATAK/HamRepeaters.kmz",
    "03_Geo/07_Overlays/08_Towers/02_ATAK/ham_repeater.kmz",
    "03_Geo/07_Overlays/08_Towers/02_ATAK/ham.repeaters.kmz",
    "03_Geo/07_Overlays/08_Towers/02_ATAK/ham–repeaters.kmz",   # en dash
    "03_Geo/07_Overlays/08_Towers/02_ATAK/ham＿repeaters.kmz",   # fullwidth underscore
    "03_Geo/07_Overlays/08_Towers/02_ATAK/ＨａｍRepeaters.kmz",  # fullwidth letters
    # only registered keep-class datasets: no personal or made-up folders
    "03_Geo/99_Personal/02_ATAK/x.kmz",
    "03_Geo/07_Overlays/99_Anything/02_ATAK/x.kmz",
    "03_Geo/a/b/c/02_ATAK/deep.kmz",
    "03_Geo/07_Overlays/02_ATAK/overlays.kmz",
    "03_Geo/02_ATAK/top.kmz",
    "03_Geo/07_Overlays/05_Hazards/x/02_ATAK/nested.kmz",
    # wrong extension, wrong folder, wrong depth, outside 03_Geo
    "03_Geo/06_Basemap/02_ATAK/readme.txt",
    "03_Geo/06_Basemap/02_ATAK/sub/nested.kmz",
    "03_Geo/06_Basemap/washington.mbtiles",
    "03_Geo/02_ATAK.kmz",
    "02_ATAK/top.kmz",
    "04_Kiwix/02_ATAK/wiki.xml",
    # hidden files and anything that is not a plain relative path
    "03_Geo/06_Basemap/02_ATAK/.partial.mbtiles",
    "/03_Geo/06_Basemap/02_ATAK/abs.kmz",
    "03_Geo/../03_Geo/06_Basemap/02_ATAK/up.kmz",
    "03_Geo/06_Basemap/02_ATAK/../02_ATAK/up.kmz",
    "03_Geo//06_Basemap/02_ATAK/double.kmz",
    "03_Geo/06_Basemap/02_ATAK/new\nline.kmz",
    "",
]


class RegisterTest(unittest.TestCase):
    def test_datasets_come_from_the_register(self):
        datasets = push.load_datasets(REGISTER)
        self.assertIn("03_Geo/06_Basemap", datasets)
        self.assertIn("03_Geo/07_Overlays/05_Hazards", datasets)
        # personal-share datasets and plain folders are not datasets to push
        self.assertNotIn("03_Geo/07_Overlays/01_Ham_Repeaters", datasets)
        self.assertNotIn("03_Geo/07_Overlays", datasets)
        self.assertTrue(all(d.startswith("03_Geo/") for d in datasets))


class AllowlistTest(unittest.TestCase):
    def setUp(self):
        self.datasets = push.load_datasets(REGISTER)

    def test_allowlist_keeps_atak_outputs(self):
        self.assertEqual(push.allowlist(KEPT, self.datasets), sorted(KEPT))
        # sorted and de-duplicated
        self.assertEqual(push.allowlist(list(reversed(KEPT)) + KEPT, self.datasets), sorted(KEPT))

    def test_push_refuses_non_allowlisted(self):
        for path in REFUSED:
            with self.subTest(path=path):
                self.assertEqual(push.allowlist([path], self.datasets), [])
        self.assertEqual(push.allowlist(REFUSED + KEPT, self.datasets), sorted(KEPT))

    def test_ham_repeater_is_refused_even_in_a_registered_dataset(self):
        # a register entry for it must not make it pushable
        datasets = self.datasets | {"03_Geo/07_Overlays/01_Ham_Repeaters"}
        self.assertEqual(push.allowlist(["03_Geo/07_Overlays/01_Ham_Repeaters/02_ATAK/r.kmz"], datasets), [])


class StageTest(unittest.TestCase):
    """main() mirrors the allowlisted files as symlinks into a staging tree."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.library = os.path.join(self.tmp, "library")
        self.stage = os.path.join(self.tmp, "stage")
        self.outside = os.path.join(self.tmp, "secret")
        with open(self.outside, "w") as f:
            f.write("private key")

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

    def run_main(self, *extra):
        return push.main(list(extra) + ["--register", REGISTER, self.library, self.stage])

    def test_stage_holds_only_allowlisted_files(self):
        for rel in KEPT + [p for p in REFUSED if p and not p.startswith("/") and ".." not in p
                           and "//" not in p and "\n" not in p]:
            self.write(rel)
        # a symlink inside the library is never followed, even when its name fits
        os.symlink(self.outside, os.path.join(self.library, "03_Geo/06_Basemap/02_ATAK/link.kmz"))
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(self.staged(), sorted(KEPT))
        for rel in KEPT:
            link = os.path.join(self.stage, rel)
            self.assertTrue(os.path.islink(link))
            self.assertEqual(os.readlink(link), os.path.join(os.path.abspath(self.library), rel))
        self.assertEqual(self.run_main("--verify"), 0)

    def test_verify_refuses_a_file_swapped_for_a_symlink(self):
        for rel in KEPT:
            self.write(rel)
        self.assertEqual(self.run_main(), 0)
        # between staging and rsync, someone swaps a file for a link to a secret
        victim = os.path.join(self.library, KEPT[0])
        os.remove(victim)
        os.symlink(self.outside, victim)
        self.assertNotEqual(self.run_main("--verify"), 0)

    def test_verify_refuses_a_directory_swapped_for_a_symlink(self):
        for rel in KEPT:
            self.write(rel)
        self.assertEqual(self.run_main(), 0)
        atak = os.path.dirname(os.path.join(self.library, KEPT[0]))
        shutil.rmtree(atak)
        decoy = os.path.join(self.tmp, "decoy")
        os.makedirs(decoy)
        os.symlink(self.outside, os.path.join(decoy, os.path.basename(KEPT[0])))
        os.symlink(decoy, atak)
        self.assertNotEqual(self.run_main("--verify"), 0)

    def test_verify_refuses_a_stage_link_pointing_outside(self):
        for rel in KEPT:
            self.write(rel)
        self.assertEqual(self.run_main(), 0)
        link = os.path.join(self.stage, KEPT[1])
        os.remove(link)
        os.symlink(self.outside, link)
        self.assertNotEqual(self.run_main("--verify"), 0)

    def test_empty_library_is_refused(self):
        # An unmounted or empty share must never reach rsync --delete-after.
        os.makedirs(os.path.join(self.library, "03_Geo"))
        self.assertNotEqual(self.run_main(), 0)
        self.library = os.path.join(self.tmp, "missing")
        self.assertNotEqual(self.run_main(), 0)

    def test_existing_stage_is_refused(self):
        self.write(KEPT[0])
        os.makedirs(self.stage)
        self.assertNotEqual(self.run_main(), 0)


if __name__ == "__main__":
    unittest.main()
