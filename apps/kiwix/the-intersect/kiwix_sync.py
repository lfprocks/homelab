"""Keep the offline Kiwix library on vault current.

Reads library.json (the folder-number register and the wanted-list), keeps
each wanted ZIM at its newest upstream build, verified against upstream's
sha256, and maintains the share's layout and record. Design:
terraform/docs/superpowers/specs/2026-10-21-offline-library-kiwix-design.md.

Python stdlib only. Never write a dollar sign followed by a brace in this
file: Flux postBuild substitution would rewrite it.
"""
import datetime
import json
import os
import re
import subprocess
import sys
try:
    import library_common as lc
except ImportError:  # running from the repo (tests): apps/library/common
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "library", "common"))
    import library_common as lc

MANIFEST_HEADER = "file\turl\tsha256\tsize_bytes\tfetched_at_utc\tlicense\n"


def load_config(path):
    """The wanted-list from path, plus the shared library register."""
    with open(path) as f:
        config = json.load(f)
    config["register"] = lc.load_register(_register_path(path))
    return config


def _register_path(config_path):
    if os.environ.get("LIBRARY_REGISTER"):
        return os.environ["LIBRARY_REGISTER"]
    beside = os.path.join(os.path.dirname(config_path), "register.json")
    if os.path.exists(beside):
        return beside
    # Running from the repo (tests): apps/library/register.json.
    return os.path.join(os.path.dirname(os.path.abspath(config_path)), "..", "..", "library", "register.json")


def _build_pattern(book):
    return re.compile(re.escape(book) + r"_(\d{4}-\d{2})\.zim$")


def newest_build(listing_html, book):
    """The newest <book>_YYYY-MM.zim linked from a directory listing, or None."""
    pattern = re.compile(r'href="(' + re.escape(book) + r'_(\d{4}-\d{2})\.zim)"')
    matches = pattern.findall(listing_html)
    if not matches:
        return None
    return max(matches, key=lambda m: m[1])[0]


def book_builds(filenames, book):
    """This book's finished builds among filenames, oldest first."""
    pattern = _build_pattern(book)
    builds = [(m.group(1), name) for name in filenames if (m := pattern.match(name))]
    return [name for _, name in sorted(builds)]


def builds_to_delete(filenames, book, keep=2):
    builds = book_builds(filenames, book)
    return builds[:-keep] if len(builds) > keep else []


def validate(config):
    return lc.validate_register(config.get("register", []), [w.get("folder") for w in config.get("wanted", [])])


def skeleton_dirs(config):
    return lc.skeleton_dirs(config["register"], "library")


def render_readme(config):
    return lc.render_readme(config["register"], "library")


def manifest_row(file, url, sha256, size, fetched_at, license):
    return lc.manifest_row(file, url, sha256, size, fetched_at, license)


# --- Orchestration ----------------------------------------------------------

CHUNK = 8 * 1024 * 1024
# Flush the part to the share every this many bytes, so unwritten SMB cache
# (charged to the pod's memory limit) stays bounded on a fast stream.
FSYNC_EVERY = 64 * 1024 * 1024
# Wikimedia's mirror (dumps.wikimedia.org) answers 403 to generic clients
# such as Python-urllib; its User-Agent policy asks for a descriptive one.
USER_AGENT = "kiwix-sync/1.0 (offline library; https://github.com/lfprocks/homelab)"
LIBRARY_XML = "02_Kiwix/library.xml"
MANIFEST = "00_Meta/02_Kiwix.manifest.tsv"
INCOMING = "01_Incoming"
# kiwix-serve exits when its library file is missing, so an empty one is
# written before the first download.
EMPTY_LIBRARY = '<?xml version="1.0" encoding="UTF-8" ?>\n<library version="20110515"/>\n'


def log(message):
    print(message, flush=True)


def _write_atomic(path, text):
    lc.write_atomic(path, text)


def _fetch_text(url):
    return lc.fetch_text(url, USER_AGENT)


def _download(url, part):
    """Stream url into part, resuming from its current size (see library_common)."""
    lc.download(url, part, USER_AGENT, chunk=CHUNK, fsync_every=FSYNC_EVERY)


def _sha256(path):
    return lc.sha256(path)


def _manifest_files(root):
    path = os.path.join(root, MANIFEST)
    if not os.path.exists(path):
        return set()
    with open(path) as f:
        return {line.split("\t", 1)[0] for line in f.readlines()[1:]}


def _append_manifest(root, row):
    path = os.path.join(root, MANIFEST)
    new = not os.path.exists(path)
    with open(path, "a") as f:
        if new:
            f.write(MANIFEST_HEADER)
        f.write(row)


def sync_book(want, root, base_url, now):
    """Bring one book to its newest upstream build. True when it ended clean."""
    book, folder = want["book"], os.path.join(root, want["folder"])
    listing_url = f"{base_url}{want['upstream_dir']}/"
    name = newest_build(_fetch_text(listing_url), book)
    if name is None:
        log(f"{book}: no build found at {listing_url}")
        return False
    target = os.path.join(folder, name)
    if os.path.exists(target) and name in _manifest_files(root):
        log(f"{book}: {name} is current")
        return True
    url = f"{listing_url}{name}"
    part = os.path.join(root, INCOMING, name + ".part")
    log(f"{book}: downloading {url}")
    _download(url, part)
    expected = _fetch_text(url + ".sha256").split()[0].lower()
    actual = _sha256(part)
    if actual != expected:
        log(f"{book}: CHECKSUM MISMATCH for {name}: expected {expected}, got {actual}; discarded")
        os.remove(part)
        return False
    size = os.path.getsize(part)
    os.replace(part, target)
    fetched_at = now.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _append_manifest(root, manifest_row(name, url, actual, size, fetched_at, want["license"]))
    log(f"{book}: {name} verified and in place ({size} bytes)")
    return True


def rebuild_catalogue(config, root, kiwix_manage):
    """Rewrite library.xml with the newest build of each book. True on success."""
    zims = []
    for want in config["wanted"]:
        builds = book_builds(os.listdir(os.path.join(root, want["folder"])), want["book"])
        if builds:
            zims.append(os.path.join(root, want["folder"], builds[-1]))
    library = os.path.join(root, LIBRARY_XML)
    tmp = library + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    if not zims:
        log("catalogue: no books yet")
        return True
    result = subprocess.run([kiwix_manage, tmp, "add", *zims])
    if result.returncode != 0:
        log(f"catalogue: kiwix-manage exited {result.returncode}; library.xml left as it was")
        return False
    os.replace(tmp, library)
    log(f"catalogue: library.xml lists {len(zims)} books")
    return True


def run(config, root, base_url, kiwix_manage, now):
    errors = validate(config)
    if errors:
        for error in errors:
            log(f"config: {error}")
        return 1
    for path in skeleton_dirs(config):
        os.makedirs(os.path.join(root, path), exist_ok=True)
    _write_atomic(os.path.join(root, "00_Meta", "README.md"), render_readme(config))
    library = os.path.join(root, LIBRARY_XML)
    if not os.path.exists(library):
        _write_atomic(library, EMPTY_LIBRARY)

    clean = True
    for want in config["wanted"]:
        try:
            clean = sync_book(want, root, base_url, now) and clean
        except Exception as error:  # one book's failure must not stop the others
            log(f"{want['book']}: failed: {error!r}")
            clean = False

    if not rebuild_catalogue(config, root, kiwix_manage):
        return 1
    for want in config["wanted"]:
        folder = os.path.join(root, want["folder"])
        for old in builds_to_delete(os.listdir(folder), want["book"]):
            os.remove(os.path.join(folder, old))
            log(f"{want['book']}: deleted old build {old}")
    return 0 if clean else 1


def main():
    config = load_config(os.environ.get("LIBRARY_CONFIG", "/etc/kiwix-sync/library.json"))
    sys.exit(run(
        config,
        os.environ.get("LIBRARY_ROOT", "/library"),
        os.environ.get("KIWIX_BASE_URL", "https://download.kiwix.org/"),
        os.environ.get("KIWIX_MANAGE", "/opt/kiwix/kiwix-manage"),
        datetime.datetime.now(datetime.timezone.utc),
    ))


if __name__ == "__main__":
    main()
