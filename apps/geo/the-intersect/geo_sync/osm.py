"""OpenStreetMap-derived overlays from the Geofabrik WA and ID extracts.

The extracts are their own store-only sources (osm_wa, osm_id) kept in
06_Basemap/01_Source. Derived overlays read those stored files rather than
downloading again, and their version is the extracts' versions. Filters use
GDAL's OSM driver with the SQLite dialect (hstore_get_value on other_tags).
Never query the public Overpass API from a scheduled job.
"""
import os
import subprocess

from . import areas, core, formats

ATTRIBUTION = "© OpenStreetMap contributors"
LINK = "https://www.openstreetmap.org/copyright"
EXTRACTS = ("osm_wa", "osm_id")
EXTRACT_FOLDER = "03_Geo/06_Basemap"


def tag(key):
    return f"hstore_get_value(other_tags, '{key}')"


# The OSM driver's default osmconf puts amenity in its own column on the
# multipolygons layer but only in other_tags on the points layer.
EMERGENCY_POINTS = (f"{tag('amenity')} IN ('hospital', 'fire_station', 'police') "
                    f"OR {tag('emergency')} = 'ambulance_station'")
EMERGENCY_POLYGONS = ("amenity IN ('hospital', 'fire_station', 'police') "
                      f"OR {tag('emergency')} = 'ambulance_station'")

# Each preset: geometry type, areas to produce, and (layer, where, label)
# queries; multipolygon queries are reduced to a point on the surface.
PRESETS = {
    "alpr": {
        "store_upstream": False, "geometry": "points", "areas": ["region"],
        "queries": [("points", f"{tag('surveillance:type')} = 'ALPR'",
                     f"COALESCE(name, 'ALPR ' || COALESCE({tag('manufacturer')}, 'unknown'))")],
    },
    "surveillance": {
        "store_upstream": False, "geometry": "points", "areas": ["region"],
        "queries": [("points", f"man_made = 'surveillance' AND COALESCE({tag('surveillance:type')}, '') <> 'ALPR'",
                     "COALESCE(name, 'Camera')")],
    },
    "emergency_services": {
        "store_upstream": False, "geometry": "points", "areas": ["region"],
        "queries": [("points", EMERGENCY_POINTS, f"COALESCE(name, {tag('amenity')})"),
                    ("multipolygons", EMERGENCY_POLYGONS, "COALESCE(name, amenity)")],
    },
    "osm_hiking_routes": {
        "store_upstream": False, "geometry": "lines", "areas": ["region"],
        "queries": [("multilinestrings", f"{tag('route')} = 'hiking'", "COALESCE(name, 'Hiking route')")],
    },
    "osm_paths": {
        "store_upstream": False, "geometry": "lines", "areas": ["king", "methow", "sandpoint"],
        "queries": [("lines", "highway IN ('path', 'track')", "COALESCE(name, highway)")],
    },
}


def _stored_extracts(ctx):
    state = core.load_state(ctx.library_root, "keep")
    folder = os.path.join(ctx.library_root, EXTRACT_FOLDER, "01_Source")
    paths, versions = [], []
    for key in EXTRACTS:
        entry = state.get(key)
        if not entry or not entry.get("history"):
            raise RuntimeError(f"{key} has not been fetched yet")
        versions.append(f"{key}:{entry['version']}")
        paths += [os.path.join(folder, name) for name in entry["history"][-1]["source"]]
    return paths, "|".join(versions)


def derived_version(src, ctx):
    return _stored_extracts(ctx)[1]


def derived_fetch(src, ctx, workdir):
    return _stored_extracts(ctx)[0]


core.FETCHERS["osm-derived"] = (derived_version, derived_fetch)


def _extract(inputs, queries, out):
    """Run each query over each extract into one GPKG layer 'features'."""
    first = True
    for pbf in inputs:
        for layer, where, label in queries:
            geom = "ST_PointOnSurface(GEOMETRY)" if layer == "multipolygons" else "GEOMETRY"
            sql = f"SELECT {geom} AS geometry, {label} AS name, other_tags FROM {layer} WHERE {where}"
            args = ["ogr2ogr", "-f", "GPKG", out, pbf, "-dialect", "SQLite", "-sql", sql, "-nln", "features"]
            args += ["-overwrite"] if first else ["-append", "-update"]
            subprocess.run(args, check=True)
            first = False


def osm_filter(src, ctx, inputs, outdir):
    preset = PRESETS[src.options["preset"]] if "preset" in src.options else src.options
    work = os.path.join(outdir, ".work")
    os.makedirs(work, exist_ok=True)
    raw = os.path.join(work, "raw.gpkg")
    _extract(inputs, preset["queries"], raw)
    outs = []
    for area in preset["areas"]:
        clipped = os.path.join(work, f"{area}.gpkg")
        subprocess.run(["ogr2ogr", "-overwrite", "-f", "GPKG", clipped, raw, "features",
                        "-clipsrc", areas.cutline(ctx, area), "-nln", src.key], check=True)
        name = core.derived_name(src, ctx, "x", area=area)[: -len(".x")]
        outs.append(formats.write_layer(clipped, os.path.join(outdir, name), preset["geometry"], ATTRIBUTION, LINK,
                                        icon=src.options.get("icon"), fallback_name=src.options.get("fallback_name")))
    return outs


core.CONVERTERS["osm-filter"] = osm_filter
