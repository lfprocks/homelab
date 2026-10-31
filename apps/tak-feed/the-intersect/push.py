"""Choose what the weekly map push sends to the TAK server.

Only finished ATAK outputs of registered keep-class datasets leave the
cluster: files directly inside <dataset>/02_ATAK, where <dataset> is a
register entry (apps/library/register.json) on the "library" share with
kind "dataset" under 03_Geo, ending in .kmz, .mbtiles or .xml. Refused even
there:

  - ham repeaters in any spelling (personal-class data, never served): the
    path is NFKC-normalised, casefolded and stripped to a-z, and anything
    containing "hamrepeater" is dropped;
  - any .gpkg (sources), 01_Source, 00_Meta and 01_Incoming;
  - hidden files (partial writes) and anything that is not a plain
    relative path.

    python3 push.py [--register FILE] LIBRARY STAGE
        walks LIBRARY/03_Geo (symlinks are never followed), applies
        allowlist() and builds STAGE, a new directory holding one symlink
        per allowed file at its library-relative path, then verifies it.
    python3 push.py --verify [--register FILE] LIBRARY STAGE
        checks STAGE again, immediately before rsync reads it.

rsync then sends STAGE with --copy-links and --delete-after, so the
server's tree becomes exactly the allowlisted set. verify() is what makes
--copy-links safe: every staged link must point at exactly its own path
under LIBRARY, resolve without passing through any symlink, and end in a
regular file; one that does not fails the whole run.

Stdlib only. The ConfigMap carrying this file is excluded from Flux
substitution; it holds no dollar-brace tokens anyway, so it stays correct
if that annotation is ever lost.
"""
import argparse
import json
import os
import stat
import sys
import unicodedata

ROOT = "03_Geo"
ATAK = "02_ATAK"
EXTENSIONS = (".kmz", ".mbtiles", ".xml")
REFUSED_PARTS = ("01_source", "00_meta", "01_incoming")
REGISTER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "register.json")


def load_datasets(register_path=REGISTER):
    """Keep-class dataset folders under 03_Geo, from the library register."""
    with open(register_path, encoding="utf-8") as f:
        register = json.load(f)
    return frozenset(
        e["path"] for e in register
        if e.get("share", "library") == "library"
        and e.get("kind") == "dataset"
        and str(e.get("path", "")).startswith(ROOT + "/")
    )


def _letters(text):
    folded = unicodedata.normalize("NFKC", text).casefold()
    return "".join(c for c in folded if "a" <= c <= "z")


def _allowed(path, datasets):
    if not isinstance(path, str) or not path or any(c in path for c in "\n\r\0"):
        return False
    parts = path.split("/")
    # Plain relative paths only: no leading slash, no empty, "." or ".." parts.
    if any(part in ("", ".", "..") for part in parts):
        return False
    if len(parts) < 4 or parts[-2] != ATAK or "/".join(parts[:-2]) not in datasets:
        return False
    if parts[-1].startswith("."):
        return False
    lowered = path.lower()
    if not lowered.endswith(EXTENSIONS) or ".gpkg" in lowered:
        return False
    if "hamrepeater" in _letters(path):
        return False
    if any(part.lower() in REFUSED_PARTS for part in parts):
        return False
    return True


def allowlist(paths, datasets):
    """The library-relative paths that may be pushed, sorted and unique."""
    return sorted({p for p in paths if _allowed(p, datasets)})


def walk(library):
    """Every regular file under LIBRARY/03_Geo, relative to LIBRARY."""
    top = os.path.join(library, ROOT)
    out = []
    for root, dirs, files in os.walk(top, followlinks=False):
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
        for name in files:
            full = os.path.join(root, name)
            if os.path.islink(full) or not os.path.isfile(full):
                continue
            out.append(os.path.relpath(full, library).replace(os.sep, "/"))
    return out


def verify(library, stage, datasets):
    """Problems with STAGE, or [] if every link is safe to copy."""
    library_real = os.path.realpath(library)
    problems, links = [], 0
    for root, dirs, files in os.walk(stage, followlinks=False):
        for name in dirs + files:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, stage).replace(os.sep, "/")
            st = os.lstat(full)
            if stat.S_ISDIR(st.st_mode):
                continue
            if not stat.S_ISLNK(st.st_mode):
                problems.append("%s: not a link" % rel)
                continue
            links += 1
            if not allowlist([rel], datasets):
                problems.append("%s: not allowlisted" % rel)
                continue
            if os.readlink(full) != os.path.join(library, rel):
                problems.append("%s: points somewhere else" % rel)
                continue
            # realpath resolves every component: equal to the plain path only
            # if nothing between the library root and the file is a symlink.
            real = os.path.realpath(full)
            if real != os.path.join(library_real, rel):
                problems.append("%s: resolves through a symlink" % rel)
                continue
            if not stat.S_ISREG(os.lstat(real).st_mode):
                problems.append("%s: not a regular file" % rel)
    if links == 0:
        problems.append("nothing staged")
    return problems


def main(argv):
    parser = argparse.ArgumentParser(prog="push.py")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--register", default=REGISTER)
    parser.add_argument("library")
    parser.add_argument("stage")
    args = parser.parse_args(argv)
    library, stage = os.path.abspath(args.library), os.path.abspath(args.stage)
    datasets = load_datasets(args.register)

    if args.verify:
        problems = verify(library, stage, datasets)
        for p in problems:
            print("refusing: " + p, file=sys.stderr)
        return 1 if problems else 0

    if not os.path.isdir(os.path.join(library, ROOT)):
        print("refusing: %s/%s is not a directory (share not mounted?)" % (library, ROOT), file=sys.stderr)
        return 1
    if os.path.lexists(stage):
        print("refusing: %s already exists" % stage, file=sys.stderr)
        return 1
    found = walk(library)
    keep = allowlist(found, datasets)
    if not keep:
        # An empty list would make rsync --delete-after empty the server.
        print("refusing: nothing to push out of %d files (share empty?)" % len(found), file=sys.stderr)
        return 1
    os.makedirs(stage)
    for rel in keep:
        link = os.path.join(stage, rel)
        os.makedirs(os.path.dirname(link), exist_ok=True)
        os.symlink(os.path.join(library, rel), link)
    problems = verify(library, stage, datasets)
    for p in problems:
        print("refusing: " + p, file=sys.stderr)
    if problems:
        return 1
    print("allowlisted %d of %d files under %s" % (len(keep), len(found), ROOT))
    for rel in keep:
        print("  " + rel)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
