"""Fixes from the whole-branch review of geo plan 1."""
import datetime
import hashlib
import json
import os
import shutil
import tempfile
import unittest
import zipfile

from fakes import APP, HAVE_GDAL, REGISTER, FakeUpstream

import library_common as lc
from geo_sync import core

NOW = datetime.datetime(2026, 10, 22, 12, 0, tzinfo=datetime.timezone.utc)


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.entries = {e["id"]: e for e in json.load(open(os.path.join(APP, "sources.json")))}

    def test_avalanche_keeps_zones_not_stale_danger(self):
        # A days-old danger rating shown offline could mislead a backcountry user.
        select = self.entries["avalanche_zones"]["options"]["select"]
        for stale in ("danger", "travel_advice", "color", "warning", "start_date"):
            self.assertNotIn(stale, select.split(","))
        self.assertTrue(self.entries["avalanche_zones"]["options"]["allow_empty"])

    def test_geofabrik_extracts_are_checksummed(self):
        for key in ("osm_wa", "osm_id"):
            self.assertEqual(self.entries[key]["options"]["md5_url"], self.entries[key]["url"] + ".md5")

    def test_cronjobs_put_gdal_temp_files_in_scratch(self):
        # The OSM driver writes its node index to the cwd (a read-only ConfigMap
        # mount in the pod) unless CPL_TMPDIR is set, and then returns 0 features.
        for name in ("cronjob-sync.yaml", "cronjob-fire.yaml"):
            text = open(os.path.join(APP, name)).read()
            for var in ("CPL_TMPDIR", "TMPDIR", "SQLITE_TMPDIR"):
                self.assertIn(f"{{name: {var}, value: /scratch}}", text, f"{name}: {var}")


def upper(src, ctx, inputs, outdir):
    out = os.path.join(outdir, core.derived_name(src, ctx, "txt"))
    with open(inputs[0], "rb") as f, open(out, "wb") as g:
        g.write(f.read().upper() + json.dumps(src.options, sort_keys=True).encode())
    return [out]


class CoreHardeningTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.up = FakeUpstream()
        self.up.files["/a/alpha.txt"] = b"alpha"
        core.CONVERTERS["test-upper"] = upper
        self.register = lc.load_register(REGISTER)
        self.ctx = self.make_ctx()

    def tearDown(self):
        self.up.close()
        shutil.rmtree(self.tmp)

    def make_ctx(self, now=NOW):
        roots = [os.path.join(self.tmp, d) for d in ("library", "personal", "inbox", "scratch")]
        for r in roots:
            os.makedirs(r, exist_ok=True)
        return core.Ctx(*roots, now=now, budget_s=3600, user_agent="t")

    def src(self, version_from="etag", **options):
        return core.Source(key="alpha", cls="keep", folder="03_Geo/07_Overlays/01_Surveillance", kind="http-file",
                           url=self.up.base_url + "/a/alpha.txt", version_from=version_from, convert="test-upper",
                           area="region", license="t", options=options)

    def test_changed_options_rebuild_even_if_upstream_unchanged(self):
        core.run([self.src(name_field="A")], self.register, self.ctx)
        self.ctx = self.make_ctx(NOW.replace(day=23))
        core.run([self.src(name_field="B")], self.register, self.ctx)
        self.assertEqual(core.last_outcomes["alpha"], core.Outcome.UPDATED)

    def test_unversioned_upstream_with_same_payload_is_current(self):
        # ArcGIS layers without editingInfo report version "".
        core.FETCHERS["test-noversion"] = (lambda s, c: "", core.FETCHERS["http-file"][1])
        src = self.src()
        src.kind = "test-noversion"
        core.run([src], self.register, self.ctx)
        self.ctx = self.make_ctx(NOW.replace(day=23))
        core.run([src], self.register, self.ctx)
        self.assertEqual(core.last_outcomes["alpha"], core.Outcome.CURRENT)
        atak = os.listdir(os.path.join(self.ctx.library_root, "03_Geo/07_Overlays/01_Surveillance/02_ATAK"))
        self.assertEqual(len(atak), 1)

    def test_md5_mismatch_fails_and_stores_nothing(self):
        self.up.files["/a/alpha.txt.md5"] = b"0" * 32 + b"  alpha.txt\n"
        src = self.src(md5_url=self.up.base_url + "/a/alpha.txt.md5")
        self.assertEqual(core.run([src], self.register, self.ctx), 1)
        folder = os.path.join(self.ctx.library_root, "03_Geo/07_Overlays/01_Surveillance")
        self.assertEqual(os.listdir(os.path.join(folder, "01_Source")), [])

    def test_md5_match_passes(self):
        self.up.files["/a/alpha.txt.md5"] = hashlib.md5(b"alpha").hexdigest().encode() + b"  alpha.txt\n"
        self.assertEqual(core.run([self.src(md5_url=self.up.base_url + "/a/alpha.txt.md5")], self.register, self.ctx), 0)

    def test_held_lock_skips_the_run(self):
        os.makedirs(os.path.join(self.ctx.library_root, "00_Meta"), exist_ok=True)
        open(os.path.join(self.ctx.library_root, "00_Meta", core.LOCK), "w").write("other run")
        self.assertEqual(core.run([self.src()], self.register, self.ctx), 0)
        self.assertEqual(core.last_outcomes, {})
        self.assertEqual(self.up.gets(), [])

    def test_stale_lock_is_taken_over(self):
        os.makedirs(os.path.join(self.ctx.library_root, "00_Meta"), exist_ok=True)
        lock = os.path.join(self.ctx.library_root, "00_Meta", core.LOCK)
        open(lock, "w").write("dead run")
        old = (NOW - datetime.timedelta(days=5)).timestamp()
        os.utime(lock, (old, old))
        self.assertEqual(core.run([self.src()], self.register, self.ctx), 0)
        self.assertEqual(core.last_outcomes["alpha"], core.Outcome.UPDATED)
        self.assertFalse(os.path.exists(lock))

    def test_write_atomic_ignores_another_writers_tmp(self):
        path = os.path.join(self.tmp, "README.md")
        os.makedirs(path + ".tmp")  # another writer's fixed-name tmp would collide
        lc.write_atomic(path, "ok")
        self.assertEqual(open(path).read(), "ok")


@unittest.skipUnless(HAVE_GDAL, "needs GDAL")
class GuardTest(unittest.TestCase):
    def setUp(self):
        from geo_sync import formats
        self.formats = formats
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def gpkg(self, features, field="name"):
        from osgeo import ogr, osr
        path = os.path.join(self.tmp, f"in{len(features)}{field}.gpkg")
        ds = ogr.GetDriverByName("GPKG").CreateDataSource(path)
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(4326)
        layer = ds.CreateLayer("l", srs, ogr.wkbPoint)
        layer.CreateField(ogr.FieldDefn(field, ogr.OFTString))
        for i in features:
            f = ogr.Feature(layer.GetLayerDefn())
            f.SetField(field, f"p{i}")
            f.SetGeometry(ogr.CreateGeometryFromWkt(f"POINT(-122 {47 + i * 0.01})"))
            layer.CreateFeature(f)
        ds = None
        return path

    def test_empty_layer_fails_unless_allowed(self):
        empty = self.gpkg([])
        with self.assertRaises(RuntimeError):
            self.formats.write_layer(empty, os.path.join(self.tmp, "o1"), "points", "t")
        out = self.formats.write_layer(empty, os.path.join(self.tmp, "o2"), "points", "t", allow_empty=True)
        self.assertTrue(os.path.exists(out))

    def test_missing_label_field_fails(self):
        with self.assertRaises(RuntimeError):
            self.formats.write_layer(self.gpkg([1, 2], field="title"), os.path.join(self.tmp, "o3"), "points", "t",
                                     name_field="name")


@unittest.skipUnless(HAVE_GDAL, "needs GDAL")
class TacZipTest(unittest.TestCase):
    def test_every_chart_in_a_zip_is_converted_and_named_by_its_tif(self):
        from osgeo import gdal, osr
        from geo_sync import charts
        tmp = tempfile.mkdtemp()
        try:
            zpath = os.path.join(tmp, "Seattle_TAC.zip")
            with zipfile.ZipFile(zpath, "w") as z:
                for name, x in (("Portland TAC", -123.5), ("Seattle TAC", -123.0)):
                    tif = os.path.join(tmp, name + ".tif")
                    ds = gdal.GetDriverByName("GTiff").Create(tif, 200, 200, 1, gdal.GDT_Byte)
                    ds.SetGeoTransform((x, 0.01, 0, 48.5, 0, -0.01))
                    srs = osr.SpatialReference()
                    srs.ImportFromEPSG(4326)
                    ds.SetProjection(srs.ExportToWkt())
                    ct = gdal.ColorTable()
                    ct.SetColorEntry(0, (1, 2, 3, 255))
                    ds.GetRasterBand(1).SetRasterColorTable(ct)
                    ds = None
                    z.write(tif, name + ".tif")
            ctx = core.Ctx(tmp, tmp, tmp, tmp, NOW, 60, "t")
            src = core.Source(key="faa_charts", cls="keep", folder="x", kind="faa-charts", url="", version_from="field",
                              convert="faa-charts", area="region", license="pd",
                              options={"edition": "09-03-2026", "min_zoom": 9, "max_zoom": 10})
            out = os.path.join(tmp, "out")
            os.makedirs(out)
            names = sorted(os.path.basename(p).split("_")[0] for p in charts.faa_charts(src, ctx, [zpath], out))
            self.assertEqual(names, ["faa-portland-tac", "faa-seattle-tac"])
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(HAVE_GDAL, "needs GDAL")
class KmzStyleTest(unittest.TestCase):
    """iTAK showed pins but no labels until every Placemark had a <name> and a
    styleUrl to an explicit IconStyle/LabelStyle (owner's test, 2026-10-22)."""

    def test_kmz_placemarks_are_named_and_styled(self):
        from osgeo import ogr, osr
        from geo_sync import formats
        tmp = tempfile.mkdtemp()
        try:
            src = os.path.join(tmp, "in.gpkg")
            ds = ogr.GetDriverByName("GPKG").CreateDataSource(src)
            srs = osr.SpatialReference()
            srs.ImportFromEPSG(4326)
            layer = ds.CreateLayer("alpr", srs, ogr.wkbPoint)
            for field in ("name", "other_tags"):
                layer.CreateField(ogr.FieldDefn(field, ogr.OFTString))
            for i, name in enumerate(("ALPR Flock Safety", None)):
                f = ogr.Feature(layer.GetLayerDefn())
                if name:
                    f.SetField("name", name)
                f.SetField("other_tags", '"manufacturer"=>"Flock Safety","direction"=>"90"')
                f.SetGeometry(ogr.CreateGeometryFromWkt(f"POINT(-122 {47 + i * 0.01})"))
                layer.CreateFeature(f)
            ds = None
            out = formats.write_layer(src, os.path.join(tmp, "alpr_region_x"), "points", "t", icon="camera",
                                      fallback_name="ALPR")
            kml = zipfile.ZipFile(out).read("doc.kml").decode()
            import re
            placemarks = re.findall(r"<Placemark.*?</Placemark>", kml, re.S)
            self.assertEqual(len(placemarks), 2)
            for pm in placemarks:
                self.assertRegex(pm, r"<name>[^<]+</name>")
                self.assertIn("<styleUrl>#style</styleUrl>", pm)
                self.assertNotIn("=>", pm)  # no raw OSM tag dump
            self.assertIn("ALPR", placemarks[1])
            self.assertIn("<IconStyle>", kml)
            self.assertIn("camera.png", kml)
            self.assertIn("<LabelStyle>", kml)
        finally:
            shutil.rmtree(tmp)


class StyleConfigTest(unittest.TestCase):
    def test_point_layers_have_icons_and_fallback_names(self):
        from geo_sync import formats
        entries = {e["id"]: e for e in json.load(open(os.path.join(APP, "sources.json")))}
        for key in ("alpr", "surveillance", "emergency_services", "usfs_recsites", "faa_airports", "wsdot_mileposts"):
            self.assertIn(entries[key]["options"]["icon"], formats.ICONS, key)
            self.assertTrue(entries[key]["options"]["fallback_name"], key)


class TakStyleSafetyTest(unittest.TestCase):
    def test_markup_in_osm_values_cannot_spoof_or_break_labels(self):
        # OSM tag values are crowdsourced; one containing <name> must not
        # become (or remove) a pin's label.
        from geo_sync import formats
        kml = ('<?xml version="1.0" encoding="utf-8" ?><kml xmlns="http://www.opengis.net/kml/2.2"><Document id="d">'
               '<Placemark id="p1"><description><![CDATA[Tower at 1 Main <name>Spoofed</name> St]]></description>'
               '<Point><coordinates>-122,47,0</coordinates></Point></Placemark>'
               '<Placemark id="p2"><name>Real &amp; Named</name><Point><coordinates>-122,47.1,0</coordinates></Point>'
               '</Placemark></Document></kml>')
        out = formats.tak_style(kml, "points", "camera", "ALPR")
        import xml.etree.ElementTree as ET
        ns = {"k": "http://www.opengis.net/kml/2.2"}
        root = ET.fromstring(out.encode())
        names = [pm.find("k:name", ns).text for pm in root.iter("{http://www.opengis.net/kml/2.2}Placemark")]
        self.assertEqual(names, ["ALPR", "Real & Named"])
        description = root.find(".//k:Placemark/k:description", ns)
        self.assertEqual(len(description), 0)  # stays text, never becomes elements
        # HTML-escaped for TAK's HTML rendering (and XML-escaped again on write).
        self.assertIn("&lt;name&gt;Spoofed&lt;/name&gt;", description.text)
        self.assertEqual(len(root.findall(".//k:Style", ns)), 1)


class DescriptionEscapeTest(unittest.TestCase):
    def test_plain_descriptions_are_html_escaped(self):
        # TAK clients render descriptions as HTML; FCC addresses and
        # RepeaterBook locations are third-party text.
        from geo_sync import formats
        kml = ('<?xml version="1.0" encoding="utf-8" ?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
               '<Placemark><name>T</name><description><![CDATA[1 Main <img src=x onerror=alert(1)> St]]>'
               '</description><Point><coordinates>-122,47,0</coordinates></Point></Placemark></Document></kml>')
        out = formats.tak_style(kml, "points", None, "T")
        import xml.etree.ElementTree as ET
        root = ET.fromstring(out.encode())
        text = root.find(".//{http://www.opengis.net/kml/2.2}description").text
        self.assertNotIn("<img", text)
        self.assertIn("&lt;img", text)

    def test_converter_revision_bumped_for_escaping(self):
        from geo_sync import core
        self.assertGreaterEqual(int(core.CONVERTER_REV), 3)


class FlatKmlTest(unittest.TestCase):
    """iTAK rejects KML with a <Document> nested in a <Document> (LIBKML's
    layout) as "too large, parsing aborted", even at 50 pins; a flat file of
    983 pins imports (owner's tests, 2026-10-22)."""

    def test_output_is_one_flat_document(self):
        from geo_sync import formats
        kml = ('<?xml version="1.0" encoding="utf-8" ?><kml xmlns="http://www.opengis.net/kml/2.2" '
               'xmlns:atom="http://www.w3.org/2005/Atom"><Document id="root_doc"><atom:author><atom:name>OSM'
               '</atom:name></atom:author><Document id="alpr"><name>alpr</name><Schema name="alpr" id="alpr.schema">'
               '<SimpleField name="osm_id" type="string"></SimpleField></Schema><Folder><Placemark id="p1"><name>A'
               '</name><Point><coordinates>-122,47,0</coordinates></Point></Placemark></Folder>'
               '<Placemark id="p2"><name>B</name><Point><coordinates>-122,47.1,0</coordinates></Point></Placemark>'
               '</Document></Document></kml>')
        out = formats.tak_style(kml, "points", "camera", "ALPR", title="ALPR cameras")
        import xml.etree.ElementTree as ET
        k = "{http://www.opengis.net/kml/2.2}"
        root = ET.fromstring(out.encode())
        documents = list(root.iter(k + "Document"))
        self.assertEqual(len(documents), 1)
        self.assertEqual([c.tag for c in documents[0]][:2], [k + "name", k + "Style"])
        self.assertEqual(documents[0].find(k + "name").text, "ALPR cameras")
        self.assertEqual([p.find(k + "name").text for p in documents[0].findall(k + "Placemark")], ["A", "B"])
        self.assertEqual(list(root.iter(k + "Folder")), [])
        self.assertEqual(list(root.iter(k + "Schema")), [])
        self.assertNotIn("Atom", out)
