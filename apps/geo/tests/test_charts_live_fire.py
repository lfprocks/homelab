"""FAA charts, live map sources and the wildfire snapshot."""
import datetime
import json
import os
import shutil
import tempfile
import unittest

from fakes import HAVE_GDAL, FakeUpstream

NOW = datetime.datetime(2026, 10, 22, 12, 0, tzinfo=datetime.timezone.utc)
FAA_PAGE = """<a href="https://aeronav.faa.gov/visual/09-03-2026/sectional-files/Seattle.zip">Seattle</a>
<a href="https://aeronav.faa.gov/visual/10-29-2026/sectional-files/Seattle.zip">Seattle (next)</a>
<a href="https://aeronav.faa.gov/visual/07-10-2026/sectional-files/Seattle.zip">old</a>"""


class EditionTest(unittest.TestCase):
    def test_chart_edition_detection(self):
        from geo_sync import charts
        self.assertEqual(charts.current_edition(FAA_PAGE, NOW.date()), "09-03-2026")
        self.assertEqual(charts.current_edition(FAA_PAGE, datetime.date(2026, 10, 29)), "10-29-2026")


class LiveTest(unittest.TestCase):
    def test_live_xml_written_without_fetch(self):
        from geo_sync import core, live  # noqa: F401
        tmp = tempfile.mkdtemp()
        try:
            src = core.Source(key="aerials", cls="live", folder="03_Geo/08_Online_Sources", kind="none",
                              url="", version_from="field", convert="live-xml", area="king", license="restricted",
                              options={"name": "KingCo Aerial 2025", "min_zoom": 0, "max_zoom": 21, "tile_type": "jpg",
                                       "template": "https://example.test/tile/{$z}/{$y}/{$x}"})
            outs = core.CONVERTERS["live-xml"](src, None, [], tmp)
            text = open(outs[0]).read()
            self.assertEqual(os.path.basename(outs[0]), "aerials.xml")
            self.assertIn("{$z}/{$y}/{$x}", text)
            self.assertIn("<maxZoom>21</maxZoom>", text)
        finally:
            shutil.rmtree(tmp)


@unittest.skipUnless(HAVE_GDAL, "needs GDAL")
class ChartsTest(unittest.TestCase):
    def test_chart_to_mbtiles_zoom_range(self):
        import zipfile
        from osgeo import gdal, osr
        from geo_sync import charts, core
        tmp = tempfile.mkdtemp()
        try:
            tif = os.path.join(tmp, "Seattle SEC.tif")
            ds = gdal.GetDriverByName("GTiff").Create(tif, 200, 200, 1, gdal.GDT_Byte)
            # About the size of a real sectional (8.5 x 4.5 degrees).
            ds.SetGeoTransform((-125.5, 0.0425, 0, 49.0, 0, -0.0225))
            srs = osr.SpatialReference()
            srs.ImportFromEPSG(4326)
            ds.SetProjection(srs.ExportToWkt())
            ct = gdal.ColorTable()
            for i in range(256):
                ct.SetColorEntry(i, (i, 255 - i, 128, 255))
            ds.GetRasterBand(1).SetRasterColorTable(ct)
            ds.GetRasterBand(1).Fill(7)
            ds = None
            zpath = os.path.join(tmp, "Seattle.zip")
            with zipfile.ZipFile(zpath, "w") as z:
                z.write(tif, "Seattle SEC.tif")
            ctx = core.Ctx(tmp, tmp, tmp, tmp, NOW, 60, "t")
            src = core.Source(key="faa_charts", cls="keep", folder="x", kind="faa-charts", url="", version_from="field",
                              convert="faa-charts", area="region", license="public domain",
                              options={"edition": "09-03-2026", "min_zoom": 6, "max_zoom": 12})
            out = os.path.join(tmp, "out")
            os.makedirs(out)
            outs = core.CONVERTERS["faa-charts"](src, ctx, [zpath], out)
            self.assertEqual([os.path.basename(p) for p in outs], ["faa-seattle-sec_region_2026-09-03_z6-12.mbtiles"])
            import sqlite3
            meta = dict(sqlite3.connect(outs[0]).execute("select name, value from metadata").fetchall())
            self.assertEqual((meta["minzoom"], meta["maxzoom"]), ("6", "12"))
        finally:
            shutil.rmtree(tmp)


@unittest.skipUnless(HAVE_GDAL, "needs GDAL")
class FireTest(unittest.TestCase):
    def setUp(self):
        from geo_sync import areas, core, fire
        self.core, self.fire, self.areas = core, fire, areas
        self.tmp = tempfile.mkdtemp()
        cut = os.path.join(self.tmp, "region.geojson")
        json.dump({"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": {
            "type": "Polygon", "coordinates": [[[-125, 45], [-116, 45], [-116, 49.1], [-125, 49.1], [-125, 45]]]}}]},
            open(cut, "w"))
        self._cutline = areas.cutline
        areas.cutline = lambda ctx, area: cut
        self.up = FakeUpstream()
        self.features = []
        self.up.handlers["/wfigs/FeatureServer/0"] = lambda q: b'{"maxRecordCount": 2000}'
        self.up.handlers["/wfigs/FeatureServer/0/query"] = lambda q: json.dumps(
            {"type": "FeatureCollection", "features": self.features}).encode()
        self.fire.WFIGS_URL = self.up.base_url + "/wfigs/FeatureServer/0"
        self.folder = os.path.join(self.tmp, "03_Geo/07_Overlays/05_Hazards/02_ATAK")
        os.makedirs(self.folder)

    def tearDown(self):
        self.areas.cutline = self._cutline
        self.up.close()
        shutil.rmtree(self.tmp)

    def ctx(self, hour):
        return self.core.Ctx(self.tmp, self.tmp, self.tmp, self.tmp, NOW.replace(hour=0) + datetime.timedelta(hours=hour),
                             60, "t")

    def test_fire_on_fresh_library_creates_its_folders(self):
        # geo-fire can run before geo-sync ever has: nothing exists yet.
        shutil.rmtree(os.path.join(self.tmp, "03_Geo"))
        self.assertEqual(self.fire.run(self.ctx(12)), 0)
        self.assertEqual(len(os.listdir(self.folder)), 1)

    def test_fire_no_fires_is_success(self):
        self.assertEqual(self.fire.run(self.ctx(12)), 0)
        self.assertEqual(len(os.listdir(self.folder)), 1)

    def test_fire_snapshot_and_retention(self):
        self.features = [{"type": "Feature", "properties": {"poly_IncidentName": "Test Fire"}, "geometry": {
            "type": "Polygon", "coordinates": [[[-121, 47], [-120.9, 47], [-120.9, 47.1], [-121, 47.1], [-121, 47]]]}}]
        for hour in range(25):
            self.assertEqual(self.fire.run(self.ctx(hour)), 0)
        names = sorted(os.listdir(self.folder))
        self.assertEqual(len(names), 24)
        self.assertNotIn("wildfire_region_2026-10-22T00", " ".join(names))
        self.assertTrue(names[-1].startswith("wildfire_region_2026-10-23T00"))


if __name__ == "__main__":
    unittest.main()
