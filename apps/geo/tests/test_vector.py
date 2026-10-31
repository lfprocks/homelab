"""Federal/state overlays: GDB zips, ArcGIS services, FCC towers."""
import datetime
import io
import json
import os
import shutil
import tempfile
import time
import unittest
import urllib.parse
import zipfile

from fakes import APP, HAVE_GDAL, FakeUpstream

NOW = datetime.datetime(2026, 10, 22, 12, 0, tzinfo=datetime.timezone.utc)
REGION = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"NAME": "r"}, "geometry": {
    "type": "Polygon", "coordinates": [[[-125, 45], [-116, 45], [-116, 49.1], [-125, 49.1], [-125, 45]]]}}]}


def square(x, y, d=0.1):
    return {"type": "Polygon", "coordinates": [[[x, y], [x + d, y], [x + d, y + d], [x, y + d], [x, y]]]}


class FccParseTest(unittest.TestCase):
    def test_dms_to_decimal(self):
        from geo_sync import fcc
        self.assertAlmostEqual(fcc.dms(47, 36, 31.2, "N"), 47.6087, places=4)
        self.assertAlmostEqual(fcc.dms(122, 19, 55.6, "W"), -122.3321, places=4)


class SourcesConfigTest(unittest.TestCase):
    def test_avalanche_is_warn_only_and_faa_uses_arcgis(self):
        entries = {e["id"]: e for e in json.load(open(os.path.join(APP, "sources.json")))}
        self.assertTrue(entries["avalanche_zones"]["warn_only"])
        self.assertEqual(entries["faa_airports"]["kind"], "arcgis")
        self.assertEqual(entries["faa_class_airspace"]["kind"], "arcgis")
        # Server-side SQL (FLOOR) must not reach GDAL's local -where.
        self.assertIn("FLOOR(SRMP)", entries["wsdot_mileposts"]["options"]["query_where"])
        self.assertNotIn("where", entries["wsdot_mileposts"]["options"])
        # MVUM NAME is mostly empty; the road/trail number (ID) is always set.
        for key in ("mvum_roads", "mvum_trails"):
            self.assertEqual(entries[key]["options"]["name_field"], "ID")
            self.assertEqual(entries[key]["options"]["desc_field"], "MVUM_SYMBOL_NAME")


@unittest.skipUnless(HAVE_GDAL, "needs GDAL")
class VectorTest(unittest.TestCase):
    def setUp(self):
        from geo_sync import areas, core, fcc, vector  # noqa: F401
        self.core, self.vector, self.fcc, self.areas = core, vector, fcc, areas
        self.tmp = tempfile.mkdtemp()
        self.cutline = os.path.join(self.tmp, "region.geojson")
        json.dump(REGION, open(self.cutline, "w"))
        self._cutline = areas.cutline
        areas.cutline = lambda ctx, area: self.cutline
        self.ctx = core.Ctx(library_root=self.tmp, personal_root=self.tmp, inbox_root=self.tmp, scratch=self.tmp,
                            now=NOW, budget_s=60, user_agent="t")
        self.up = FakeUpstream()

    def tearDown(self):
        self.areas.cutline = self._cutline
        self.up.close()
        shutil.rmtree(self.tmp)

    def src(self, key, convert="vector-clip", kind="http-file", url="", **options):
        return self.core.Source(key=key, cls="keep", folder="x", kind=kind, url=url, version_from="field",
                                convert=convert, area="region", license="public domain", options=options)

    def count(self, path):
        from osgeo import ogr
        ds = ogr.Open(path if not path.endswith(".kmz") else "/vsizip/" + path + "/doc.kml")
        return sum(layer.GetFeatureCount() for layer in ds)

    def outdir(self, name):
        path = os.path.join(self.tmp, name)
        os.makedirs(path)
        return path

    def test_gdb_zip_clipped_to_region(self):
        from osgeo import ogr, osr
        gdb = os.path.join(self.tmp, "Own.gdb")
        ds = ogr.GetDriverByName("OpenFileGDB").CreateDataSource(gdb)
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(4326)
        layer = ds.CreateLayer("Ownership", srs, ogr.wkbPolygon)
        layer.CreateField(ogr.FieldDefn("OWNER", ogr.OFTString))
        for owner, (x, y) in (("USFS", (-121.5, 47.5)), ("USFS", (-100.0, 40.0))):
            feature = ogr.Feature(layer.GetLayerDefn())
            feature.SetField("OWNER", owner)
            feature.SetGeometry(ogr.CreateGeometryFromJson(json.dumps(square(x, y))))
            layer.CreateFeature(feature)
        ds = None
        zpath = os.path.join(self.tmp, "Own.gdb.zip")
        with zipfile.ZipFile(zpath, "w") as z:
            for name in os.listdir(gdb):
                z.write(os.path.join(gdb, name), f"Own.gdb/{name}")
        src = self.src("usfs_ownership", geometry="polygons", name_field="OWNER")
        outs = self.core.CONVERTERS["vector-clip"](src, self.ctx, [zpath], self.outdir("o1"))
        self.assertEqual(len(outs), 1)
        self.assertEqual(self.count(outs[0]), 1)
        # iTAK reads overlays only as KML/KMZ/GeoPDF (owner's check, 2026-10-22).
        self.assertTrue(outs[0].endswith(".kmz"))

    def test_invalid_polygon_on_the_edge_survives_clip(self):
        # A real WFIGS perimeter was lost: GEOS threw "Ring edge missing"
        # clipping an invalid polygon. A bowtie straddling the -116 edge.
        bowtie = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"NAME": "bowtie"}, "geometry": {"type": "Polygon", "coordinates": [
                [[-116.5, 47.0], [-115.5, 48.0], [-115.5, 47.0], [-116.5, 48.0], [-116.5, 47.0]]]}}]}
        path = os.path.join(self.tmp, "bowtie.geojson")
        json.dump(bowtie, open(path, "w"))
        out = os.path.join(self.tmp, "c.gpkg")
        self.vector.clip(self.src("fires", geometry="polygons"), self.ctx, path, out)
        self.assertEqual(self.count(out), 1)

    def _arcgis(self, total=5, page=2):
        feats = [{"type": "Feature", "properties": {"NAME": f"f{i}"},
                  "geometry": {"type": "Point", "coordinates": [-121 - i * 0.01, 47.5]}} for i in range(total)]
        self.up.handlers["/svc/FeatureServer/0"] = lambda q: json.dumps(
            {"maxRecordCount": page, "editingInfo": {"lastEditDate": 1790000000000}}).encode()

        def query(q):
            offset = int(q.get("resultOffset", ["0"])[0])
            count = int(q.get("resultRecordCount", [str(page)])[0])
            chunk = feats[offset:offset + count]
            return json.dumps({"type": "FeatureCollection", "features": chunk,
                               "properties": {"exceededTransferLimit": offset + count < total}}).encode()

        self.up.handlers["/svc/FeatureServer/0/query"] = query
        return self.up.base_url + "/svc/FeatureServer/0"

    def test_arcgis_paging_collects_all_pages(self):
        url = self._arcgis(total=5, page=2)
        src = self.src("wsdot_mileposts", kind="arcgis", url=url, geometry="points", name_field="NAME")
        version_fn, fetch_fn = self.core.FETCHERS["arcgis"]
        self.assertEqual(version_fn(src, self.ctx), "1790000000000")
        paths = fetch_fn(src, self.ctx, self.outdir("w"))
        data = json.load(open(paths[0]))
        self.assertEqual(len(data["features"]), 5)
        self.assertEqual(len([p for p in self.up.gets() if "/query" in p]), 3)

    def test_arcgis_query_where_sent_to_server(self):
        url = self._arcgis(total=3, page=5)
        src = self.src("x", kind="arcgis", url=url, query_where="SRMP = FLOOR(SRMP)")
        self.core.FETCHERS["arcgis"][1](src, self.ctx, self.outdir("w"))
        query = [p for p in self.up.gets() if "/query" in p][0]
        self.assertIn("FLOOR", urllib.parse.unquote_plus(query))

    def test_arcgis_throttle(self):
        url = self._arcgis(total=6, page=2)
        old = self.vector.THROTTLE_S
        self.vector.THROTTLE_S = 0.2
        try:
            started = time.monotonic()
            self.core.FETCHERS["arcgis"][1](self.src("x", kind="arcgis", url=url), self.ctx, self.outdir("w"))
            self.assertGreaterEqual(time.monotonic() - started, 0.4)  # 3 pages, 2 waits
        finally:
            self.vector.THROTTLE_S = old

    def test_fcc_join_and_filter(self):
        co = ("CO|REG|A1|1000001|11|T|47|36|31.2|N|0|122|19|55.6|W|0||\n"
              "CO|REG|A2|1000002|12|T|43|0|0.0|N|0|116|30|0.0|W|0||\n"
              "CO|REG|A3|1000003|13|T|45|30|0.0|N|0|122|40|0.0|W|0||\n")
        ra = ("RA|REG|A1|1000001|11|OC|AU|I|C|||||||C||||||||1 Main|SEATTLE|WA|53033|98101|30.0|50.0|35.0|85.0|TOWER|\n"
              "RA|REG|A2|1000002|12|OC|AU|I|C|||||||C||||||||2 Main|BOISE|ID|16001|83702|30.0|800.0|35.0|835.0|POLE|\n"
              "RA|REG|A3|1000003|13|OC|AU|I|C|||||||C||||||||3 Main|PORTLAND|OR|41051|97201|30.0|20.0|35.0|55.0|TOWER|\n")
        zpath = os.path.join(self.tmp, "r_tower.zip")
        with zipfile.ZipFile(zpath, "w") as z:
            z.writestr("CO.dat", co)
            z.writestr("RA.dat", ra)
        src = self.src("fcc_asr", convert="fcc-asr")
        outs = self.core.CONVERTERS["fcc-asr"](src, self.ctx, [zpath], self.outdir("o"))
        # WA and ID only; the Boise tower (43N) is outside the test region
        self.assertEqual(self.count(outs[0]), 1)


if __name__ == "__main__":
    unittest.main()


class ScienceBaseUrlTest(unittest.TestCase):
    def test_download_url_uses_catalog_file_get(self):
        # The item JSON's url/downloadUri answer with an HTML app page, not the file.
        from geo_sync import vector
        self.assertEqual(
            vector.sciencebase_download_url("https://www.sciencebase.gov/catalog/item/6759abcfd34edfeb8710a004",
                                            "PADUS4_1_State_WA_GDB_KMZ.zip"),
            "https://www.sciencebase.gov/catalog/file/get/6759abcfd34edfeb8710a004?name=PADUS4_1_State_WA_GDB_KMZ.zip")
