"""FCC Antenna Structure Registration towers in Washington and Idaho.

r_tower.zip holds pipe-delimited CO.dat (coordinates) and RA.dat
(registration: location, heights, structure type), joined on the unique
system identifier (field 4 in both).
"""
import csv
import io
import os
import subprocess
import zipfile

from . import core, formats, vector

STATES = ("WA", "ID")


def dms(degrees, minutes, seconds, hemisphere):
    value = float(degrees) + float(minutes) / 60 + float(seconds) / 3600
    return -value if hemisphere in ("S", "W") else value


def _rows(z, name):
    text = io.TextIOWrapper(z.open(name), encoding="latin-1", newline="")
    return csv.reader(text, delimiter="|")


def fcc_asr(src, ctx, inputs, outdir):
    coords = {}
    with zipfile.ZipFile(inputs[0]) as z:
        for r in _rows(z, "CO.dat"):
            if len(r) > 15 and r[5] == "T" and r[6] and r[11]:
                coords[r[4]] = (dms(r[6], r[7], r[8] or 0, r[9]), dms(r[11], r[12], r[13] or 0, r[14]))
        towers = []
        for r in _rows(z, "RA.dat"):
            if len(r) > 32 and r[25] in STATES and r[4] in coords:
                lat, lon = coords[r[4]]
                height = r[30] or r[28]
                towers.append({"lat": f"{lat:.6f}", "lon": f"{lon:.6f}",
                               "name": f"ASR {r[3]} {height} m",
                               "description": f"{r[32]}; {height} m above ground, {r[31]} m AMSL; "
                                              f"{r[23]}, {r[24]} {r[25]}; registration {r[3]}"})
    work = os.path.join(outdir, ".work")
    os.makedirs(work, exist_ok=True)
    table = os.path.join(work, "towers.csv")
    with open(table, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["lat", "lon", "name", "description"])
        writer.writeheader()
        writer.writerows(towers)
    points = os.path.join(work, "towers.gpkg")
    subprocess.run(["ogr2ogr", "-overwrite", "-f", "GPKG", points, table, "-oo", "X_POSSIBLE_NAMES=lon",
                    "-oo", "Y_POSSIBLE_NAMES=lat", "-a_srs", "EPSG:4326", "-nln", "towers"], check=True)
    src.options.setdefault("geometry", "points")
    clipped = os.path.join(work, "clipped.gpkg")
    vector.clip(src, ctx, points, clipped)
    name = core.derived_name(src, ctx, "x")[: -len(".x")]
    return [formats.write_layer(clipped, os.path.join(outdir, name), "points", src.license,
                                name_field="name", desc_field="description", icon="target", fallback_name="Tower")]


core.CONVERTERS["fcc-asr"] = fcc_asr
