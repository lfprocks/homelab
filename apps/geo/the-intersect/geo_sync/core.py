"""geo-sync core: sources, versions, licence classes, budget and retention.

Each source is data (sources.json) naming a fetcher kind and a converter.
A source is re-fetched only when its upstream version changes. Upstream
files land in <dataset>/01_Source, derived files in <dataset>/02_ATAK, both
moved into place from 01_Incoming only once the whole conversion succeeded.
Design: terraform/docs/superpowers/specs/2026-10-22-offline-library-geo-design.md.

Never write a dollar sign followed by a brace in this package: Flux
postBuild substitution would rewrite it.
"""
import dataclasses
import contextlib
import enum
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.request

import library_common as lc

MANIFEST_COLUMNS = ["file", "url", "sha256", "size_bytes", "fetched_at_utc", "license", "class", "derived_from"]
MANIFEST_HEADER = "\t".join(MANIFEST_COLUMNS) + "\n"
INCOMING = "01_Incoming"
KEEP_VERSIONS = 2
# One writer at a time: concurrencyPolicy does not cover manual Jobs. A lock
# older than a run's maximum deadline is from a dead run and is taken over.
LOCK = "geo-sync.lock"
LOCK_STALE_S = 4 * 24 * 3600
# Bump when a converter's output changes for the same inputs and options.
CONVERTER_REV = "4"  # 2: KMZ descriptions HTML-escaped; 3: flat KML for iTAK; 4: polygons as KMZ
DATE_IN_NAME = re.compile(r"(19|20)\d{2}[-_]?\d{2}")

# Output format per data type. iTAK reads vector overlays only as KML, KMZ
# or GeoPDF; GeoPackage only as raster map tiles (owner's checks, 2026-10-22).
# A flat 18 MB / 15,110-line KMZ imports, so size never changes the format.
FORMATS = {"points": "kmz", "lines": "kmz", "polygons": "kmz", "raster": "mbtiles"}

# Registries filled by the source modules (osm, vector, charts, ...).
FETCHERS = {}
CONVERTERS = {}

# Outcomes of the last run, by source key (for logs and tests).
last_outcomes = {}


class Outcome(enum.Enum):
    CURRENT = "current"
    UPDATED = "updated"
    DEFERRED = "deferred"
    WARNED = "warned"
    FAILED = "failed"


@dataclasses.dataclass
class Source:
    key: str
    cls: str  # keep | personal | live
    folder: str  # register path of the dataset folder
    kind: str  # fetcher name
    url: str
    version_from: str  # etag | last-modified | name-date | field
    convert: str  # converter name
    area: str  # region | idaho | <focus key>
    license: str
    warn_only: bool = False
    options: dict = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class Ctx:
    library_root: str
    personal_root: str
    inbox_root: str
    scratch: str
    now: object  # aware datetime
    budget_s: float
    user_agent: str
    started: float = dataclasses.field(default_factory=time.monotonic)

    def budget_spent(self):
        return time.monotonic() - self.started >= self.budget_s


def log(message):
    print(message, flush=True)


def load_sources(path):
    """sources.json names each source "id" (a JSON "key" field with a
    high-entropy value trips gitleaks' generic-api-key rule)."""
    with open(path) as f:
        return [Source(key=entry["id"], **{k: v for k, v in entry.items() if k != "id"}) for entry in json.load(f)]


def output_root(src, ctx):
    """The dataset folder for src, confined to the share its class allows."""
    root = ctx.personal_root if src.cls == "personal" else ctx.library_root
    folder = os.path.normpath(os.path.join(root, src.folder))
    # Check on resolved paths (symlinks, ..), return the path as configured.
    if not os.path.realpath(folder).startswith(os.path.realpath(root) + os.sep):
        raise ValueError(f"{src.key}: folder {src.folder} escapes its share")
    # A keep/live source must never write into a folder registered as personal.
    if src.cls != "personal" and "/01_Ham_Repeaters" in "/" + src.folder:
        raise ValueError(f"{src.key}: {src.cls} source in a personal folder")
    return folder


def derived_name(src, ctx, ext, zoom=None, area=None):
    vintage = ctx.now.strftime("%Y-%m-%d")
    name = f"{src.key.replace('_', '-')}_{area or src.area}_{vintage}"
    if zoom:
        name += f"_z{zoom[0]}-{zoom[1]}"
    return f"{name}.{ext}"


def stored_name(upstream_name, ctx):
    """Upstream file name, with the fetch date added when it carries none."""
    if DATE_IN_NAME.search(upstream_name):
        return upstream_name
    stem, dot, ext = upstream_name.partition(".")
    return f"{stem}_{ctx.now.strftime('%Y-%m-%d')}{dot}{ext}"


# --- state ---------------------------------------------------------------

def _state_path(root, cls):
    name = "personal.state.json" if cls == "personal" else "03_Geo.state.json"
    return os.path.join(root, "00_Meta", name)


def load_state(root, cls):
    path = _state_path(root, cls)
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def save_state(root, cls, state):
    lc.write_atomic(_state_path(root, cls), json.dumps(state, indent=1, sort_keys=True) + "\n")


def _manifest_path(root, cls):
    name = "personal.manifest.tsv" if cls == "personal" else "03_Geo.manifest.tsv"
    return os.path.join(root, "00_Meta", name)


# --- fetchers ------------------------------------------------------------

def _head(url, ctx):
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": ctx.user_agent})
    with urllib.request.urlopen(req, timeout=60) as response:
        return response.headers


def http_file_version(src, ctx):
    headers = _head(src.url, ctx)
    if src.version_from == "etag" and headers.get("ETag"):
        return headers["ETag"]
    return headers.get("Last-Modified") or headers.get("ETag") or ""


def http_file_fetch(src, ctx, workdir):
    name = os.path.basename(src.url.split("?")[0]) or f"{src.key}.dat"
    path = os.path.join(workdir, name)
    lc.download(src.url, path, ctx.user_agent)
    if src.options.get("md5_url"):
        expected = lc.fetch_text(src.options["md5_url"], ctx.user_agent).split()[0].lower()
        digest = hashlib.md5()
        with open(path, "rb") as f:
            while chunk := f.read(lc.CHUNK):
                digest.update(chunk)
        if digest.hexdigest() != expected:
            raise RuntimeError(f"md5 mismatch for {name}: expected {expected}, got {digest.hexdigest()}")
    return [path]


FETCHERS["http-file"] = (http_file_version, http_file_fetch)


# --- the run -------------------------------------------------------------

def _move_in(paths, dest_dir, incoming, rename=None):
    """Stage each path in 01_Incoming, then rename into dest_dir. Returns names."""
    names = []
    for path in paths:
        name = rename(os.path.basename(path)) if rename else os.path.basename(path)
        staged = os.path.join(incoming, name)
        shutil.copyfile(path, staged)
        os.replace(staged, os.path.join(dest_dir, name))
        names.append(name)
    return names


def _retain(entry, dest):
    """Keep the newest KEEP_VERSIONS versions' files; delete older ones."""
    history = entry.get("history", [])
    kept = history[-KEEP_VERSIONS:]
    for sub, field in (("01_Source", "source"), ("02_ATAK", "atak")):
        still_used = {name for version in kept for name in version.get(field, [])}
        for old in history[:-KEEP_VERSIONS]:
            for name in old.get(field, []):
                path = os.path.join(dest, sub, name)
                if name not in still_used and os.path.exists(path):
                    os.remove(path)
    entry["history"] = kept


def process(src, ctx):
    dest = output_root(src, ctx)
    root = ctx.personal_root if src.cls == "personal" else ctx.library_root
    if src.cls == "live":
        # Never fetched: only the converter's map source file is written.
        work = tempfile.mkdtemp(dir=ctx.scratch)
        try:
            outs = CONVERTERS[src.convert](src, ctx, [], work)
            _move_in(outs, dest, os.path.join(root, INCOMING))
        finally:
            shutil.rmtree(work, ignore_errors=True)
        return Outcome.UPDATED
    state = load_state(root, src.cls)
    entry = state.setdefault(src.key, {})
    version_fn, fetch_fn = FETCHERS[src.kind]
    upstream_version = version_fn(src, ctx)
    store_only = src.options.get("store_only", False)
    store_upstream = src.options.get("store_upstream", True)
    current = (entry.get("history", [])[-1:] or [{}])[0]
    check = ("01_Source", current.get("source", ["-"])) if store_only else ("02_ATAK", current.get("atak", ["-"]))
    have = bool(check[1]) and all(os.path.exists(os.path.join(dest, check[0], n)) for n in check[1])
    recipe = _recipe(src)
    version = f"{upstream_version}#{recipe}" if upstream_version else ""
    if version and entry.get("version") == version and have:
        return Outcome.CURRENT
    work = tempfile.mkdtemp(dir=ctx.scratch)
    try:
        upstream = fetch_fn(src, ctx, work)
        if not version:
            # No upstream version (some ArcGIS layers): compare the payload itself.
            version = f"sha256:{_payload_hash(upstream)}#{recipe}"
            if entry.get("version") == version and have:
                return Outcome.CURRENT
        outdir = os.path.join(work, "out")
        os.makedirs(outdir)
        outs = [] if store_only else CONVERTERS[src.convert](src, ctx, upstream, outdir)
        if not outs and not store_only:
            raise RuntimeError("conversion produced no output")
        incoming = os.path.join(root, INCOMING)
        fetched = ctx.now.strftime("%Y-%m-%dT%H:%M:%SZ")
        if store_upstream:
            src_names = _move_in(upstream, os.path.join(dest, "01_Source"), incoming,
                                 rename=lambda n: stored_name(n, ctx))
        else:
            # Derived from files another source keeps (e.g. the OSM extracts).
            src_names = [os.path.basename(p) for p in upstream]
        atak_names = _move_in(outs, os.path.join(dest, "02_ATAK"), incoming)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    manifest = _manifest_path(root, src.cls)
    for name in src_names if store_upstream else []:
        path = os.path.join(dest, "01_Source", name)
        lc.append_manifest(manifest, MANIFEST_HEADER, lc.manifest_row(
            name, src.url, lc.sha256(path), os.path.getsize(path), fetched, src.license, src.cls, ""))
    for name in atak_names:
        path = os.path.join(dest, "02_ATAK", name)
        lc.append_manifest(manifest, MANIFEST_HEADER, lc.manifest_row(
            name, "", lc.sha256(path), os.path.getsize(path), fetched, src.license, src.cls, ",".join(src_names)))
    entry["version"] = version
    entry.setdefault("history", []).append(
        {"version": version, "source": src_names if store_upstream else [], "atak": atak_names})
    _retain(entry, dest)
    save_state(root, src.cls, state)
    return Outcome.UPDATED


def _manual_imports(ctx):
    """README section listing when each hand-imported personal source last arrived."""
    state = load_state(ctx.personal_root, "personal")
    lines = [f"- {key}: last imported {entry['last_import']}\n"
             for key, entry in sorted(state.items()) if entry.get("last_import")]
    return "\n## Manual imports\n\n" + ("".join(lines) or "- none yet\n")


def _recipe(src):
    """What, besides upstream data, decides the outputs: rebuild when it changes."""
    blob = json.dumps([src.convert, src.options, src.area, FORMATS, CONVERTER_REV], sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:12]


def _payload_hash(paths):
    digest = hashlib.sha256()
    for path in sorted(paths):
        with open(path, "rb") as f:
            while chunk := f.read(lc.CHUNK):
                digest.update(chunk)
    return digest.hexdigest()[:16]


@contextlib.contextmanager
def run_lock(ctx):
    """Yield True if this run holds the library lock, False if another live run does."""
    meta = os.path.join(ctx.library_root, "00_Meta")
    os.makedirs(meta, exist_ok=True)
    path = os.path.join(meta, LOCK)
    try:
        if time.time() - os.path.getmtime(path) > LOCK_STALE_S:
            log(f"lock: taking over stale {LOCK}")
            os.remove(path)
    except FileNotFoundError:
        pass
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        log(f"lock: another geo-sync run holds {LOCK}; skipping")
        yield False
        return
    try:
        os.write(fd, f"{os.getpid()} {ctx.now.isoformat()}\n".encode())
        os.close(fd)
        yield True
    finally:
        os.remove(path)


def prepare(register, ctx):
    for share, root in (("library", ctx.library_root), ("personal", ctx.personal_root)):
        for path in lc.skeleton_dirs(register, share):
            os.makedirs(os.path.join(root, path), exist_ok=True)
        readme = lc.render_readme(register, share)
        if share == "personal":
            readme += _manual_imports(ctx)
        lc.write_atomic(os.path.join(root, "00_Meta", "README.md"), readme)


def run(sources, register, ctx):
    last_outcomes.clear()
    with run_lock(ctx) as held:
        return _run(sources, register, ctx) if held else 0


def _run(sources, register, ctx):
    errors = lc.validate_register(register, [s.folder for s in sources if s.cls != "personal"])
    if errors:
        for error in errors:
            log(f"register: {error}")
        return 1
    prepare(register, ctx)
    failed = False
    for src in sources:
        if ctx.budget_spent() and last_outcomes:
            outcome = Outcome.DEFERRED
        else:
            try:
                outcome = process(src, ctx)
            except Exception as error:  # one source's failure must not stop the others
                outcome = Outcome.WARNED if src.warn_only else Outcome.FAILED
                log(f"{src.key}: {outcome.value}: {error!r}")
        last_outcomes[src.key] = outcome
        log(f"{src.key}: {outcome.value}")
        failed = failed or outcome == Outcome.FAILED
    return 1 if failed else 0
