"""FAA VFR charts (Seattle and Great Falls sectionals, Seattle TAC) as
MBTiles, one file per chart. Editions run on a 56-day cycle; the current
one is read from the FAA VFR page. Collars are not clipped: each chart is
its own layer.
"""
import datetime
import os
import re
import subprocess
import zipfile

import library_common as lc

from . import core

FAA_VFR_PAGE = "https://www.faa.gov/air_traffic/flight_info/aeronav/digital_products/vfr/"
AERONAV = "https://aeronav.faa.gov/visual"
EDITION = re.compile(r"aeronav\.faa\.gov/visual/(\d{2}-\d{2}-\d{4})/")
WEB_MERCATOR_Z0 = 156543.03392804097  # metres per pixel at zoom 0


def current_edition(page_html, today):
    """The newest edition date (MM-DD-YYYY) on or before today."""
    def as_date(s):
        return datetime.datetime.strptime(s, "%m-%d-%Y").date()
    editions = sorted({e for e in EDITION.findall(page_html) if as_date(e) <= today}, key=as_date)
    if not editions:
        raise RuntimeError("no current FAA chart edition found")
    return editions[-1]


def charts_version(src, ctx):
    edition = current_edition(lc.fetch_text(FAA_VFR_PAGE, ctx.user_agent), ctx.now.date())
    src.options["edition"] = edition
    return edition


def charts_fetch(src, ctx, workdir):
    paths = []
    for chart in src.options["charts"]:
        path = os.path.join(workdir, os.path.basename(chart))
        lc.download(f"{AERONAV}/{src.options['edition']}/{chart}", path, ctx.user_agent)
        paths.append(path)
    return paths


core.FETCHERS["faa-charts"] = (charts_version, charts_fetch)


def _run(*args):
    subprocess.run(list(args), check=True)


def faa_charts(src, ctx, inputs, outdir):
    lo, hi = src.options.get("min_zoom", 6), src.options.get("max_zoom", 12)
    edition = datetime.datetime.strptime(src.options["edition"], "%m-%d-%Y").strftime("%Y-%m-%d")
    work = os.path.join(outdir, ".work")
    os.makedirs(work, exist_ok=True)
    outs = []
    # A zip can hold several charts (Seattle_TAC.zip: Portland TAC, Seattle FLY,
    # Seattle TAC): convert each, named by its own file.
    jobs = []
    for zpath in inputs:
        with zipfile.ZipFile(zpath) as z:
            jobs += [(zpath, n) for n in z.namelist() if n.lower().endswith(".tif")]
    for zpath, tif in jobs:
        chart = re.sub(r"[^a-z0-9]+", "-", os.path.splitext(os.path.basename(tif))[0].lower()).strip("-")
        rgb = os.path.join(work, f"{chart}.vrt")
        merc = os.path.join(work, f"{chart}.tif")
        _run("gdal_translate", "-q", "-of", "VRT", "-expand", "rgb", f"/vsizip/{zpath}/{tif}", rgb)
        res = WEB_MERCATOR_Z0 / 2 ** hi
        _run("gdalwarp", "-q", "-overwrite", "-t_srs", "EPSG:3857", "-tr", str(res), str(res), "-r", "bilinear",
             "-co", "TILED=YES", rgb, merc)
        tmp = os.path.join(work, f"{chart}.mbtiles")
        _run("gdal_translate", "-q", "-of", "MBTILES", "-co", "TILE_FORMAT=JPEG", "-co", "ZOOM_LEVEL_STRATEGY=AUTO",
             merc, tmp)
        _run("gdaladdo", "-q", "-r", "average", tmp, *[str(2 ** i) for i in range(1, hi - lo + 1)])
        # GDAL stops overviews below one tile, so name the file by what it holds.
        zmin, zmax = _zoom_range(tmp)
        out = os.path.join(outdir, f"faa-{chart}_region_{edition}_z{zmin}-{zmax}.mbtiles")
        os.replace(tmp, out)
        outs.append(out)
    return outs


def _zoom_range(mbtiles):
    import sqlite3
    with sqlite3.connect(mbtiles) as db:
        meta = dict(db.execute("SELECT name, value FROM metadata").fetchall())
    return meta["minzoom"], meta["maxzoom"]


core.CONVERTERS["faa-charts"] = faa_charts
