"""Choose what the weekly map push sends to the TAK server.

Only finished ATAK outputs leave the cluster: files directly inside a
02_ATAK folder somewhere under 03_Geo, ending in .kmz, .mbtiles or .xml.
Everything else stays on the library share, and some things are refused
even inside a 02_ATAK folder:

  - Ham_Repeaters in any spelling (personal-class data, never served);
  - 01_Source, 00_Meta and 01_Incoming folders, and any .gpkg (sources);
  - hidden files (partial writes) and anything that is not a plain
    relative path.

    python3 push.py LIBRARY STAGE

walks LIBRARY/03_Geo (symlinks are never followed), applies allowlist()
and builds STAGE, a new directory holding one symlink per allowed file at
its library-relative path. rsync then sends STAGE with --copy-links and
--delete-after, so the server's tree becomes exactly the allowlisted set:
nothing outside it is ever read by rsync, and nothing outside it survives
on the server.

Stdlib only. Flux substitutes dollar-brace tokens in this ConfigMap, so
none may appear here.
"""
import os
import sys

ROOT = "03_Geo"
ATAK = "02_ATAK"
EXTENSIONS = (".kmz", ".mbtiles", ".xml")
REFUSED_PARTS = ("01_source", "00_meta", "01_incoming")


def _allowed(path):
    if not isinstance(path, str) or not path or "\n" in path or "\r" in path or "\0" in path:
        return False
    parts = path.split("/")
    # Plain relative paths only: no leading slash, no empty, "." or ".." parts.
    if any(part in ("", ".", "..") for part in parts):
        return False
    if len(parts) < 3 or parts[0] != ROOT or parts[-2] != ATAK:
        return False
    name = parts[-1]
    if name.startswith("."):
        return False
    lowered = path.lower()
    if not lowered.endswith(EXTENSIONS):
        return False
    if ".gpkg" in lowered:
        return False
    if "ham_repeaters" in lowered.replace("-", "_").replace(" ", "_"):
        return False
    if any(part.lower() in REFUSED_PARTS for part in parts):
        return False
    return True


def allowlist(paths):
    """The library-relative paths that may be pushed, sorted and unique."""
    return sorted({p for p in paths if _allowed(p)})


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


def main(argv):
    if len(argv) != 2:
        print("usage: push.py LIBRARY STAGE", file=sys.stderr)
        return 2
    library, stage = (os.path.abspath(a) for a in argv)
    if not os.path.isdir(os.path.join(library, ROOT)):
        print("refusing: %s/%s is not a directory (share not mounted?)" % (library, ROOT), file=sys.stderr)
        return 1
    if os.path.lexists(stage):
        print("refusing: %s already exists" % stage, file=sys.stderr)
        return 1
    found = walk(library)
    keep = allowlist(found)
    if not keep:
        # An empty list would make rsync --delete-after empty the server.
        print("refusing: nothing to push out of %d files (share empty?)" % len(found), file=sys.stderr)
        return 1
    os.makedirs(stage)
    for rel in keep:
        link = os.path.join(stage, rel)
        os.makedirs(os.path.dirname(link), exist_ok=True)
        os.symlink(os.path.join(library, rel), link)
    print("allowlisted %d of %d files under %s" % (len(keep), len(found), ROOT))
    for rel in keep:
        print("  " + rel)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
