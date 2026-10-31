"""Hourly wildfire-perimeter snapshot (WFIGS current perimeters) for offline
use, clipped to the region. The last 24 snapshots are kept. No fires is a
success and writes an empty layer. Snapshots are not recorded in the
manifest: 24 a day would bury it.
"""
import os
import shutil
import tempfile

from . import core, formats, vector

WFIGS_URL = ("https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/"
             "WFIGS_Interagency_Perimeters_Current/FeatureServer/0")
FOLDER = "03_Geo/07_Overlays/05_Hazards/02_ATAK"
PREFIX = "wildfire_region_"
KEEP = 24


def run(ctx):
    src = core.Source(key="wildfire", cls="keep", folder=FOLDER, kind="arcgis", url=WFIGS_URL,
                      version_from="field", convert="none", area="region", license="Public domain (NIFC WFIGS)",
                      options={"geometry": "polygons"})
    work = tempfile.mkdtemp(dir=ctx.scratch)
    try:
        geojson = vector.arcgis_fetch(src, ctx, work)[0]
        clipped = os.path.join(work, "wildfire.gpkg")
        vector.clip(src, ctx, geojson, clipped)
        name = f"{PREFIX}{ctx.now.strftime('%Y-%m-%dT%H')}"
        out = formats.write_layer(clipped, os.path.join(work, name), "polygons", src.license,
                                  name_field="poly_IncidentName", desc_field="", allow_empty=True,
                                  colour="B71C1C")
        # geo-fire may run before geo-sync ever has built the skeleton.
        folder = os.path.join(ctx.library_root, FOLDER)
        incoming = os.path.join(ctx.library_root, core.INCOMING)
        os.makedirs(folder, exist_ok=True)
        os.makedirs(incoming, exist_ok=True)
        staged = os.path.join(incoming, os.path.basename(out))
        shutil.copyfile(out, staged)
        os.replace(staged, os.path.join(folder, os.path.basename(out)))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    snapshots = sorted(n for n in os.listdir(folder) if n.startswith(PREFIX))
    for old in snapshots[:-KEEP]:
        os.remove(os.path.join(folder, old))
    core.log(f"wildfire: {os.path.basename(out)}")
    return 0
