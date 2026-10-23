"""Generic vector overlays: files (GDB/shapefile zips, GeoJSON), ArcGIS
feature services and ScienceBase items, clipped to an area and written in
the TAK-ready format for their geometry.
"""
import json
import os
import subprocess
import time
import urllib.parse
import zipfile

import library_common as lc

from . import areas, core, formats

# Seconds between ArcGIS requests: be polite to public services.
THROTTLE_S = 0.5
PAGE_MAX = 2000


# --- ArcGIS feature/map services -----------------------------------------

def _arcgis_json(url, ctx, params):
    return json.loads(lc.fetch_text(f"{url}?{urllib.parse.urlencode(params)}", ctx.user_agent))


def arcgis_version(src, ctx):
    info = _arcgis_json(src.url, ctx, {"f": "json"})
    # Layers without editing info re-fetch every run; they are small.
    return str((info.get("editingInfo") or {}).get("lastEditDate") or "")


def arcgis_fetch(src, ctx, workdir):
    info = _arcgis_json(src.url, ctx, {"f": "json"})
    page = min(int(info.get("maxRecordCount") or 1000), PAGE_MAX)
    # query_where runs server-side (ArcGIS SQL, e.g. FLOOR); "where" is GDAL's, applied locally in clip.
    params = {"where": src.options.get("query_where", "1=1"), "outFields": src.options.get("out_fields", "*"),
              "outSR": "4326", "f": "geojson", "resultRecordCount": str(page)}
    if src.options.get("bbox_filter", True):
        minx, miny, maxx, maxy = areas.bbox(areas.cutline(ctx, src.area))
        params.update({"geometry": f"{minx},{miny},{maxx},{maxy}", "geometryType": "esriGeometryEnvelope",
                       "inSR": "4326", "spatialRel": "esriSpatialRelIntersects"})
    features, offset = [], 0
    while True:
        if offset:
            time.sleep(THROTTLE_S)
        params["resultOffset"] = str(offset)
        data = _arcgis_json(src.url + "/query", ctx, params)
        if "error" in data:
            raise RuntimeError(f"ArcGIS error: {data['error']}")
        chunk = data.get("features", [])
        features += chunk
        more = (data.get("properties") or {}).get("exceededTransferLimit") or data.get("exceededTransferLimit")
        if not chunk or (not more and len(chunk) < page):
            break
        offset += len(chunk)
    path = os.path.join(workdir, f"{src.key}.geojson")
    with open(path, "w") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f)
    return [path]


core.FETCHERS["arcgis"] = (arcgis_version, arcgis_fetch)


# --- ScienceBase items (PAD-US) -------------------------------------------

def _sciencebase_file(src, ctx):
    item = json.loads(lc.fetch_text(src.url + ("&" if "?" in src.url else "?") + "format=json", ctx.user_agent))
    for entry in item.get("files", []):
        if entry["name"] == src.options["file"]:
            return entry
    raise RuntimeError(f"{src.options['file']} not in ScienceBase item")


def sciencebase_version(src, ctx):
    entry = _sciencebase_file(src, ctx)
    return f"{entry.get('dateUploaded', '')}:{entry.get('size', '')}"


def sciencebase_download_url(item_url, name):
    """The item JSON's url/downloadUri serve an HTML app page; this serves the file."""
    item_id = item_url.rstrip("/").split("/")[-1].split("?")[0]
    return f"https://www.sciencebase.gov/catalog/file/get/{item_id}?{urllib.parse.urlencode({'name': name})}"


def sciencebase_fetch(src, ctx, workdir):
    entry = _sciencebase_file(src, ctx)
    path = os.path.join(workdir, entry["name"])
    lc.download(sciencebase_download_url(src.url, entry["name"]), path, ctx.user_agent)
    return [path]


core.FETCHERS["sciencebase"] = (sciencebase_version, sciencebase_fetch)


# --- clip and convert ------------------------------------------------------

def open_path(path, wanted=None):
    """A GDAL-openable path for path; inside a zip, the .gdb (or wanted) entry."""
    if not path.endswith(".zip"):
        return path
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
    if wanted:
        return f"/vsizip/{path}/{wanted}"
    gdbs = sorted({n.split(".gdb/")[0] + ".gdb" for n in names if ".gdb/" in n})
    if gdbs:
        return f"/vsizip/{path}/{gdbs[0]}"
    shps = [n for n in names if n.endswith(".shp")]
    return f"/vsizip/{path}/{shps[0]}" if shps else f"/vsizip/{path}"


NLT = {"points": "POINT", "lines": "PROMOTE_TO_MULTI", "polygons": "PROMOTE_TO_MULTI"}


def clip(src, ctx, dataset, out, layer=None):
    """Reproject, filter and clip dataset (layer) to src.area into out (GPKG).

    Lines and polygons are made valid in a first pass and clipped in a
    second: with -makevalid and -clipsrc in one ogr2ogr call, GEOS can still
    throw on invalid input ("Ring edge missing"), losing the feature.
    """
    opts = src.options
    geometry = opts.get("geometry", "polygons")
    cut = areas.cutline(ctx, src.area)
    minx, miny, maxx, maxy = areas.bbox(cut)
    first = ["ogr2ogr", "-overwrite", "-f", "GPKG"]
    spat = ["-t_srs", "EPSG:4326", "-spat", str(minx), str(miny), str(maxx), str(maxy), "-spat_srs", "EPSG:4326",
            "-nln", src.key, "-nlt", NLT[geometry]]
    filters = (["-where", opts["where"]] if opts.get("where") else []) + \
              (["-select", opts["select"]] if opts.get("select") else [])
    if geometry == "points":
        subprocess.run(first + [out, dataset] + ([layer] if layer else []) + spat + ["-clipsrc", cut] + filters,
                       check=True)
        return
    valid = out + ".valid.gpkg"
    subprocess.run(first + [valid, dataset] + ([layer] if layer else []) + spat + ["-makevalid"] + filters,
                   check=True)
    subprocess.run(first + [out, valid, src.key, "-clipsrc", cut, "-nln", src.key, "-nlt", NLT[geometry]],
                   check=True)
    os.remove(valid)


def vector_clip(src, ctx, inputs, outdir):
    opts = src.options
    work = os.path.join(outdir, ".work")
    os.makedirs(work, exist_ok=True)
    clipped = os.path.join(work, f"{src.key}.gpkg")
    clip(src, ctx, open_path(inputs[0], opts.get("zip_entry")), clipped, opts.get("layer"))
    name = core.derived_name(src, ctx, "x")[: -len(".x")]
    return [formats.write_layer(clipped, os.path.join(outdir, name), opts.get("geometry", "polygons"),
                                src.license, name_field=opts.get("name_field"),
                                desc_field=opts.get("desc_field", ""), allow_empty=opts.get("allow_empty", False),
                                icon=opts.get("icon"), fallback_name=opts.get("fallback_name"),
                                colour=opts.get("colour"))]


core.CONVERTERS["vector-clip"] = vector_clip
