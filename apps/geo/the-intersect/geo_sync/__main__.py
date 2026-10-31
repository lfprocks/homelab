"""python3 -m geo_sync sync|fire|inbox

sync:  every source in sources.json (version check, fetch, convert), then
       the inbox sweep. Exit 1 only on real source failures.
fire:  the hourly wildfire snapshot.
inbox: the inbox sweep alone, on demand.
"""
import datetime
import os
import sys

import library_common as lc

from . import charts, core, fcc, fire, inbox, live, osm, vector  # noqa: F401 (register fetchers/converters)

DEFAULTS = {
    "GEO_LIBRARY_ROOT": "/library",
    "GEO_PERSONAL_ROOT": "/library-personal",
    "GEO_INBOX_ROOT": "/library-inbox",
    "GEO_SCRATCH": "/scratch",
    "GEO_SOURCES": "/etc/geo-sync/sources.json",
    "LIBRARY_REGISTER": "/etc/geo-sync/register.json",
    "GEO_BUDGET_SECONDS": "230400",
}


def context(env):
    get = lambda k: env.get(k, DEFAULTS[k])  # noqa: E731
    return core.Ctx(library_root=get("GEO_LIBRARY_ROOT"), personal_root=get("GEO_PERSONAL_ROOT"),
                    inbox_root=get("GEO_INBOX_ROOT"), scratch=get("GEO_SCRATCH"),
                    now=datetime.datetime.now(datetime.timezone.utc), budget_s=float(get("GEO_BUDGET_SECONDS")),
                    user_agent=lc.USER_AGENT_FMT.format(job="geo-sync"))


def main(argv, env):
    if len(argv) != 1 or argv[0] not in ("sync", "fire", "inbox"):
        print(__doc__, file=sys.stderr)
        return 2
    ctx = context(env)
    if argv[0] == "fire":
        return fire.run(ctx)
    register = lc.load_register(env.get("LIBRARY_REGISTER", DEFAULTS["LIBRARY_REGISTER"]))
    if argv[0] == "inbox":
        core.prepare(register, ctx)
        inbox.sweep(ctx)
        core.prepare(register, ctx)  # refresh the personal README's import dates
        return 0
    rc = core.run(core.load_sources(env.get("GEO_SOURCES", DEFAULTS["GEO_SOURCES"])), register, ctx)
    inbox.sweep(ctx)
    core.prepare(register, ctx)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], os.environ))
