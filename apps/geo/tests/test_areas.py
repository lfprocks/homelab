"""Region and focus-area cutlines."""
import json
import os
import shutil
import tempfile
import unittest

from fakes import HAVE_GDAL


def square(x, y, d=1.0):
    return {"type": "Polygon", "coordinates": [[[x, y], [x + d, y], [x + d, y + d], [x, y + d], [x, y]]]}


@unittest.skipUnless(HAVE_GDAL, "needs GDAL")
class AreasTest(unittest.TestCase):
    def setUp(self):
        from geo_sync import areas
        self.areas = areas
        self.tmp = tempfile.mkdtemp()
        states = [{"type": "Feature", "properties": {"STATEFP": fp, "NAME": n}, "geometry": square(x, 45)}
                  for fp, n, x in (("53", "Washington", -124), ("16", "Idaho", -117), ("41", "Oregon", -123))]
        counties = [{"type": "Feature", "properties": {"STATEFP": fp, "NAME": n}, "geometry": square(x, 47, 0.2)}
                    for fp, n, x in (("16", "Bonner", -117), ("16", "Boundary", -116.7), ("16", "Kootenai", -116.4),
                                     ("16", "Shoshone", -116.1), ("16", "Benewah", -115.8), ("16", "Ada", -115.5),
                                     ("53", "King", -122))]
        for name, feats in (("states", states), ("counties", counties)):
            with open(os.path.join(self.tmp, f"{name}.geojson"), "w") as f:
                json.dump({"type": "FeatureCollection", "features": feats}, f)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def names(self, path):
        from osgeo import ogr
        ds = ogr.Open(path)
        return sorted(f.GetField("NAME") for f in ds.GetLayer(0))

    def test_region_is_washington_plus_panhandle(self):
        out = os.path.join(self.tmp, "region.gpkg")
        self.areas.build_region(os.path.join(self.tmp, "states.geojson"), os.path.join(self.tmp, "counties.geojson"), out)
        self.assertEqual(self.names(out), ["Benewah", "Bonner", "Boundary", "Kootenai", "Shoshone", "Washington"])

    def test_focus_counties(self):
        out = os.path.join(self.tmp, "king.gpkg")
        self.areas.build_county(os.path.join(self.tmp, "counties.geojson"), "53", "King", out)
        self.assertEqual(self.names(out), ["King"])


if __name__ == "__main__":
    unittest.main()
