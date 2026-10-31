"""OpenStreetMap-derived overlays."""
import datetime
import os
import shutil
import tempfile
import unittest
import zipfile

from fakes import HAVE_GDAL, HERE

NOW = datetime.datetime(2026, 10, 22, 12, 0, tzinfo=datetime.timezone.utc)
FIXTURE = os.path.join(HERE, "fixtures", "tiny.osm")


@unittest.skipUnless(HAVE_GDAL, "needs GDAL")
class OsmTest(unittest.TestCase):
    def setUp(self):
        from geo_sync import areas, core, formats, osm  # noqa: F401
        self.core, self.osm, self.formats, self.areas = core, osm, formats, areas
        self.tmp = tempfile.mkdtemp()
        # A region cutline covering western WA and Sandpoint, not 40N/100W.
        self.cutline = os.path.join(self.tmp, "region.geojson")
        open(self.cutline, "w").write(
            '{"type":"FeatureCollection","features":[{"type":"Feature","properties":{"NAME":"r"},'
            '"geometry":{"type":"Polygon","coordinates":[[[-125,45],[-116,45],[-116,49.1],[-125,49.1],[-125,45]]]}}]}')
        self._cutline = areas.cutline
        areas.cutline = lambda ctx, area: self.cutline
        self.ctx = core.Ctx(library_root=self.tmp, personal_root=self.tmp, inbox_root=self.tmp, scratch=self.tmp,
                            now=NOW, budget_s=60, user_agent="t")

    def tearDown(self):
        self.areas.cutline = self._cutline
        shutil.rmtree(self.tmp)

    def convert(self, key, options):
        src = self.core.Source(key=key, cls="keep", folder="x", kind="osm-derived", url="", version_from="field",
                               convert="osm-filter", area="region", license="ODbL", options=options)
        out = os.path.join(self.tmp, "out-" + key)
        os.makedirs(out)
        return src, self.core.CONVERTERS["osm-filter"](src, self.ctx, [FIXTURE], out)

    def features(self, path):
        from osgeo import ogr
        ds = ogr.Open(path if not path.endswith(".kmz") else "/vsizip/" + path + "/doc.kml")
        return [f for layer in ds for f in layer]

    def kml_text(self, path):
        with zipfile.ZipFile(path) as z:
            return z.read("doc.kml").decode()

    def test_alpr_filter_and_tags(self):
        _, outs = self.convert("alpr", self.osm.PRESETS["alpr"])
        self.assertEqual(len(outs), 1)
        self.assertEqual(len(self.features(outs[0])), 3)  # node 5 is outside the region, node 4 is not ALPR
        self.assertIn("Flock Safety", self.kml_text(outs[0]))

    def test_emergency_services_filter(self):
        _, outs = self.convert("emergency_services", self.osm.PRESETS["emergency_services"])
        names = sorted(f.GetField("Name") for f in self.features(outs[0]))
        # Polygon features (most hospitals) arrive as points on their surface.
        self.assertEqual(names, ["Medic 1", "Polygon Hospital", "Precinct", "Station 1", "Test Hospital"])

    def test_derived_names_follow_scheme(self):
        _, outs = self.convert("alpr", self.osm.PRESETS["alpr"])
        self.assertEqual(os.path.basename(outs[0]), "alpr_region_2026-10-22.kmz")

    def test_attribution_present(self):
        _, outs = self.convert("alpr", self.osm.PRESETS["alpr"])
        self.assertIn("© OpenStreetMap contributors", self.kml_text(outs[0]))

    def test_large_layer_stays_kmz(self):
        # iTAK reads no GeoPackage overlays, and a flat 18 MB / 15,110-line KMZ
        # imported fine (owner's test), so size never switches the format.
        old = self.formats.KMZ_MAX_FEATURES if hasattr(self.formats, "KMZ_MAX_FEATURES") else None
        _, outs = self.convert("alpr", self.osm.PRESETS["alpr"])
        self.assertEqual(os.path.basename(outs[0]), "alpr_region_2026-10-22.kmz")
        self.assertIsNone(old)

    def test_derived_source_does_not_copy_the_extract(self):
        self.assertFalse(self.core.Source(key="alpr", cls="keep", folder="x", kind="osm-derived", url="",
                                          version_from="field", convert="osm-filter", area="region", license="ODbL",
                                          options=self.osm.PRESETS["alpr"]).options.get("store_upstream", True))


if __name__ == "__main__":
    unittest.main()


class SourcesFileTest(unittest.TestCase):
    def test_sources_json_loads_and_osm_derived_do_not_copy_extract(self):
        import json
        from fakes import APP
        entries = json.load(open(os.path.join(APP, "sources.json")))
        keys = [e["id"] for e in entries]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertLess(keys.index("osm_wa"), keys.index("alpr"))  # extracts first
        for e in entries:
            if e["kind"] == "osm-derived":
                self.assertFalse(e["options"]["store_upstream"])
