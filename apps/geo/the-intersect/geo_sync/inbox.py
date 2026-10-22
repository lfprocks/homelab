"""The library-inbox share: files the person downloads by hand.

Today that is RepeaterBook CSV exports (a radius around home), which are
personal-class: kept in library-personal only, never served or shared.
A file is recognised by its header row, not its name. Recognised files go
through the core (staging, manifest, state, retention) and are removed
from the inbox; anything else is left in place with a warning.
"""
import csv
import hashlib
import os
import shutil
import subprocess

from . import core, formats

FOLDER = "03_Geo/07_Overlays/01_Ham_Repeaters"
LICENSE = "RepeaterBook export: personal use only, never redistribute"

# Header names seen in RepeaterBook exports, lower-cased.
ALIASES = {
    "output": ("output freq", "output frequency", "frequency", "freq", "output"),
    "input": ("input freq", "input frequency", "input"),
    "offset": ("offset",),
    "tone": ("uplink tone", "pl", "ctcss", "tone", "pl tone", "access tone", "tone up"),
    "tone_down": ("downlink tone", "tsq", "tone down"),
    "call": ("call", "callsign", "call sign"),
    "location": ("location", "nearest city", "city", "landmark"),
    "county": ("county",),
    "state": ("state",),
    "mode": ("mode", "modes"),
    "lat": ("lat", "latitude"),
    "lon": ("long", "lon", "longitude", "lng"),
}
REQUIRED = ("output", "call", "lat", "lon")


def column_map(header):
    """{field: header column} for a RepeaterBook header, or None if it isn't one."""
    lowered = {h.strip().lower(): h for h in header}
    found = {}
    for field, names in ALIASES.items():
        found[field] = next((lowered[n] for n in names if n in lowered), None)
    return found if all(found[f] for f in REQUIRED) else None


def _header(path):
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            return next(csv.reader(f), [])
    except (UnicodeDecodeError, StopIteration):
        return []


def inbox_version(src, ctx):
    with open(src.url, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def inbox_fetch(src, ctx, workdir):
    path = os.path.join(workdir, os.path.basename(src.url))
    shutil.copyfile(src.url, path)
    return [path]


core.FETCHERS["inbox-file"] = (inbox_version, inbox_fetch)


def _float(value):
    return float(str(value).split()[0])


def repeaterbook_csv(src, ctx, inputs, outdir):
    with open(inputs[0], newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        m = column_map(reader.fieldnames or [])
        rows = []
        for r in reader:
            try:
                lat, lon, out_mhz = _float(r[m["lat"]]), _float(r[m["lon"]]), _float(r[m["output"]])
            except (ValueError, TypeError, IndexError):
                continue
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                continue
            g = lambda k: (r.get(m[k]) or "").strip() if m[k] else ""  # noqa: E731
            tones = " / ".join(t for t in (g("tone"), g("tone_down")) if t)
            parts = [f"Output {out_mhz:.4f} MHz"]
            if g("input"):
                parts.append(f"input {g('input')} MHz" + (f" ({g('offset')})" if g("offset") else ""))
            if tones:
                parts.append(f"tone {tones}")
            parts += [p for p in (g("mode"), g("location"), g("county"), g("state")) if p]
            rows.append({"lat": lat, "lon": lon, "name": f"{g('call')} {out_mhz:.3f}", "description": ", ".join(parts)})
    if not rows:
        raise ValueError("no usable rows (bad coordinates or frequencies)")
    work = os.path.join(outdir, ".work")
    os.makedirs(work, exist_ok=True)
    table = os.path.join(work, "repeaters.csv")
    with open(table, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["lat", "lon", "name", "description"])
        writer.writeheader()
        writer.writerows(rows)
    points = os.path.join(work, "repeaters.gpkg")
    subprocess.run(["ogr2ogr", "-overwrite", "-f", "GPKG", points, table, "-oo", "X_POSSIBLE_NAMES=lon",
                    "-oo", "Y_POSSIBLE_NAMES=lat", "-a_srs", "EPSG:4326", "-nln", "ham_repeaters"], check=True)
    name = core.derived_name(src, ctx, "x", area="radius")[: -len(".x")]
    return [formats.write_layer(points, os.path.join(outdir, name), "points", LICENSE,
                                name_field="name", desc_field="description", icon="triangle",
                                fallback_name="Repeater")]


core.CONVERTERS["repeaterbook-csv"] = repeaterbook_csv


def sweep(ctx):
    outcomes = []
    for name in sorted(os.listdir(ctx.inbox_root)):
        path = os.path.join(ctx.inbox_root, name)
        if name.startswith(".") or not os.path.isfile(path):
            continue
        if not (name.lower().endswith(".csv") and column_map(_header(path))):
            core.log(f"inbox: {name}: not a recognised file, left in place")
            outcomes.append(core.Outcome.WARNED)
            continue
        src = core.Source(key="ham_repeaters", cls="personal", folder=FOLDER, kind="inbox-file", url=path,
                          version_from="field", convert="repeaterbook-csv", area="radius", license=LICENSE)
        try:
            outcome = core.process(src, ctx)
        except Exception as error:
            core.log(f"inbox: {name}: {error!r}; left in place")
            outcomes.append(core.Outcome.WARNED)
            continue
        state = core.load_state(ctx.personal_root, "personal")
        state["ham_repeaters"]["last_import"] = ctx.now.strftime("%Y-%m-%d")
        core.save_state(ctx.personal_root, "personal", state)
        os.remove(path)
        core.log(f"inbox: {name}: imported as ham_repeaters ({outcome.value})")
        outcomes.append(outcome)
    return outcomes
