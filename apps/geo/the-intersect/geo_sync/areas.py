"""Region and focus-area cutlines, cached under 03_Geo/00_Areas.

Region: Washington plus the Idaho panhandle's five counties. Focus areas:
King County, the Methow watershed (HUC8 17020008), Bonner County. Boundaries
come from the Census cartographic boundary files and the USGS WBD.
"""
import json
import os
import subprocess
import urllib.parse

import library_common as lc

AREAS = "03_Geo/00_Areas"
STATES_URL = "https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_state_500k.zip"
COUNTIES_URL = "https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_county_500k.zip"
WBD_HUC8_QUERY = "https://hydro.nationalmap.gov/arcgis/rest/services/wbd/MapServer/4/query"
PANHANDLE = ("Boundary", "Bonner", "Kootenai", "Shoshone", "Benewah")
WA, ID = "53", "16"
FOCUS = {"king": ("county", WA, "King"), "sandpoint": ("county", ID, "Bonner"), "methow": ("huc8", "17020008")}


def _ogr(*args):
    mode = [] if "-append" in args else ["-overwrite"]
    subprocess.run(["ogr2ogr", *mode, *args], check=True)


def build_region(states, counties, out):
    """WA state polygon plus the five panhandle counties, in EPSG:4326."""
    _ogr("-f", "GPKG", "-t_srs", "EPSG:4326", "-nln", "region", "-nlt", "PROMOTE_TO_MULTI", "-select", "NAME",
         out, states, "-where", f"STATEFP = '{WA}'")
    names = ", ".join(f"'{n}'" for n in PANHANDLE)
    # -append ignores source fields the target lacks (and forbids -select).
    _ogr("-f", "GPKG", "-append", "-update", "-t_srs", "EPSG:4326", "-nln", "region", "-nlt", "PROMOTE_TO_MULTI",
         out, counties, "-where", f"STATEFP = '{ID}' AND NAME IN ({names})")


def build_county(counties, statefp, name, out):
    _ogr("-f", "GPKG", "-t_srs", "EPSG:4326", "-nln", "area", "-nlt", "PROMOTE_TO_MULTI", "-select", "NAME",
         out, counties, "-where", f"STATEFP = '{statefp}' AND NAME = '{name}'")


def build_state(states, statefp, out):
    _ogr("-f", "GPKG", "-t_srs", "EPSG:4326", "-nln", "area", "-nlt", "PROMOTE_TO_MULTI", "-select", "NAME",
         out, states, "-where", f"STATEFP = '{statefp}'")


def _cached(ctx, name):
    folder = os.path.join(ctx.library_root, AREAS)
    os.makedirs(folder, exist_ok=True)  # geo-fire may run before geo-sync
    return os.path.join(folder, name)


def _boundary_zip(ctx, url):
    path = _cached(ctx, os.path.basename(url))
    if not os.path.exists(path):
        # pid-unique: geo-fire and geo-sync may build the cache at the same time.
        part = f"{path}.{os.getpid()}.part"
        lc.download(url, part, ctx.user_agent)
        os.replace(part, path)
    return "/vsizip/" + path


def cutline(ctx, area):
    """Path to a cached GPKG for area: region, idaho, or a FOCUS key."""
    out = _cached(ctx, f"{area}.gpkg")
    if os.path.exists(out):
        return out
    tmp = f"{out}.{os.getpid()}.tmp.gpkg"
    if area == "region":
        build_region(_boundary_zip(ctx, STATES_URL), _boundary_zip(ctx, COUNTIES_URL), tmp)
    elif area == "idaho":
        build_state(_boundary_zip(ctx, STATES_URL), ID, tmp)
    elif FOCUS[area][0] == "county":
        build_county(_boundary_zip(ctx, COUNTIES_URL), FOCUS[area][1], FOCUS[area][2], tmp)
    else:
        params = urllib.parse.urlencode({"where": f"huc8='{FOCUS[area][1]}'", "outFields": "huc8,name",
                                         "outSR": "4326", "f": "geojson"})
        geojson = out + ".geojson"
        lc.write_atomic(geojson, lc.fetch_text(f"{WBD_HUC8_QUERY}?{params}", ctx.user_agent))
        if not json.load(open(geojson)).get("features"):
            raise RuntimeError(f"no WBD boundary for {area}")
        _ogr("-f", "GPKG", "-nln", "area", "-nlt", "PROMOTE_TO_MULTI", tmp, geojson)
    os.replace(tmp, out)
    return out


def bbox(path):
    """(minx, miny, maxx, maxy) of a cutline, for -spat prefilters."""
    from osgeo import ogr
    ds = ogr.Open(path)  # keep the dataset alive while its layer is used
    minx, maxx, miny, maxy = ds.GetLayer(0).GetExtent()
    return minx, miny, maxx, maxy
