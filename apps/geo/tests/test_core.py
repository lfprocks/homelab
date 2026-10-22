"""geo_sync core: versions, classes, budget, retention, failure semantics."""
import datetime
import os
import shutil
import tempfile
import unittest

from fakes import REGISTER, FakeUpstream

import library_common as lc
from geo_sync import core

NOW = datetime.datetime(2026, 10, 22, 12, 0, tzinfo=datetime.timezone.utc)


def upper(src, ctx, inputs, outdir):
    """Test converter: one output per input, contents upper-cased."""
    outs = []
    for path in inputs:
        out = os.path.join(outdir, core.derived_name(src, ctx, "txt"))
        with open(path, "rb") as f, open(out, "wb") as g:
            g.write(f.read().upper())
        outs.append(out)
    return outs


def broken(src, ctx, inputs, outdir):
    raise RuntimeError("upstream schema changed")


class CoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.up = FakeUpstream()
        self.up.files["/a/alpha.txt"] = b"alpha v1"
        self.up.files["/b/beta.txt"] = b"beta v1"
        core.CONVERTERS["test-upper"] = upper
        core.CONVERTERS["test-broken"] = broken
        self.register = lc.load_register(REGISTER)
        self.ctx = self.make_ctx()

    def tearDown(self):
        self.up.close()
        shutil.rmtree(self.tmp)

    def make_ctx(self, budget_s=3600, now=NOW):
        roots = {k: os.path.join(self.tmp, k) for k in ("library", "personal", "inbox", "scratch")}
        for path in roots.values():
            os.makedirs(path, exist_ok=True)
        return core.Ctx(
            library_root=roots["library"], personal_root=roots["personal"], inbox_root=roots["inbox"],
            scratch=roots["scratch"], now=now, budget_s=budget_s, user_agent="geo-sync-test/1.0",
        )

    def src(self, key, path, cls="keep", convert="test-upper", folder="03_Geo/07_Overlays/01_Surveillance", **kw):
        return core.Source(key=key, cls=cls, folder=folder, kind="http-file", url=self.up.base_url + path,
                           version_from="etag", convert=convert, area="region", license="test", **kw)

    def atak(self, src_folder="03_Geo/07_Overlays/01_Surveillance", root=None):
        return sorted(os.listdir(os.path.join(root or self.ctx.library_root, src_folder, "02_ATAK")))

    def test_unchanged_upstream_is_current(self):
        sources = [self.src("alpha", "/a/alpha.txt")]
        self.assertEqual(core.run(sources, self.register, self.ctx), 0)
        self.up.requests.clear()
        self.assertEqual(core.run(sources, self.register, self.ctx), 0)
        self.assertEqual(self.up.gets("/a/alpha.txt"), [])
        self.assertEqual(core.last_outcomes["alpha"], core.Outcome.CURRENT)

    def test_changed_upstream_updates_and_keeps_previous(self):
        sources = [self.src("alpha", "/a/alpha.txt")]
        core.run(sources, self.register, self.ctx)
        for i, day in enumerate((23, 24), start=2):
            self.up.files["/a/alpha.txt"] = f"alpha v{i}".encode()
            self.ctx = self.make_ctx(now=NOW.replace(day=day))
            self.assertEqual(core.run(sources, self.register, self.ctx), 0)
        # current and previous only
        self.assertEqual(self.atak(), ["alpha_region_2026-10-23.txt", "alpha_region_2026-10-24.txt"])
        src_dir = os.path.join(self.ctx.library_root, "03_Geo/07_Overlays/01_Surveillance/01_Source")
        self.assertEqual(len(os.listdir(src_dir)), 2)

    def test_conversion_error_keeps_previous_output(self):
        good = [self.src("alpha", "/a/alpha.txt")]
        core.run(good, self.register, self.ctx)
        before = self.atak()
        self.up.files["/a/alpha.txt"] = b"alpha v2"
        bad = [self.src("alpha", "/a/alpha.txt", convert="test-broken")]
        self.ctx = self.make_ctx(now=NOW.replace(day=23))
        self.assertEqual(core.run(bad, self.register, self.ctx), 1)
        self.assertEqual(self.atak(), before)
        self.assertEqual(core.last_outcomes["alpha"], core.Outcome.FAILED)

    def test_live_source_never_fetches(self):
        written = []

        def live_xml(src, ctx, inputs, outdir):
            self.assertEqual(inputs, [])
            out = os.path.join(outdir, "kingco.xml")
            open(out, "w").write("<customMapSource/>")
            written.append(out)
            return [out]

        core.CONVERTERS["test-live"] = live_xml
        src = core.Source(key="kingco", cls="live", folder="03_Geo/08_Online_Sources", kind="http-file",
                          url=self.up.base_url + "/a/alpha.txt", version_from="etag", convert="test-live",
                          area="region", license="restricted")
        self.assertEqual(core.run([src], self.register, self.ctx), 0)
        self.assertEqual(self.up.requests, [])
        self.assertEqual(os.listdir(os.path.join(self.ctx.library_root, "03_Geo/08_Online_Sources")), ["kingco.xml"])

    def test_personal_output_confined_to_personal_root(self):
        ok = self.src("reps", "/a/alpha.txt", cls="personal", folder="03_Geo/07_Overlays/01_Ham_Repeaters")
        self.assertTrue(core.output_root(ok, self.ctx).startswith(self.ctx.personal_root))
        escape = self.src("reps", "/a/alpha.txt", cls="personal", folder="../library/03_Geo")
        with self.assertRaises(ValueError):
            core.output_root(escape, self.ctx)
        keep_in_personal_folder = self.src("x", "/a/alpha.txt", folder="03_Geo/07_Overlays/01_Ham_Repeaters")
        with self.assertRaises(ValueError):
            core.output_root(keep_in_personal_folder, self.ctx)

    def test_time_budget_defers_remaining_sources(self):
        sources = [self.src("alpha", "/a/alpha.txt"), self.src("beta", "/b/beta.txt")]
        self.ctx.budget_s = 0  # spent as soon as the first source finishes
        self.assertEqual(core.run(sources, self.register, self.ctx), 0)
        self.assertEqual(core.last_outcomes["alpha"], core.Outcome.UPDATED)
        self.assertEqual(core.last_outcomes["beta"], core.Outcome.DEFERRED)
        self.assertEqual(self.up.gets("/b/beta.txt"), [])

    def test_warn_only_failure_does_not_fail_run(self):
        src = self.src("avy", "/missing.json", warn_only=True)
        self.assertEqual(core.run([src], self.register, self.ctx), 0)
        self.assertEqual(core.last_outcomes["avy"], core.Outcome.WARNED)

    def test_real_failure_fails_run(self):
        src = self.src("gone", "/missing.json")
        self.assertEqual(core.run([src, self.src("alpha", "/a/alpha.txt")], self.register, self.ctx), 1)
        self.assertEqual(core.last_outcomes["gone"], core.Outcome.FAILED)
        self.assertEqual(core.last_outcomes["alpha"], core.Outcome.UPDATED)

    def test_manifest_has_class_and_derived_from(self):
        core.run([self.src("alpha", "/a/alpha.txt")], self.register, self.ctx)
        rows = open(os.path.join(self.ctx.library_root, "00_Meta/03_Geo.manifest.tsv")).read().splitlines()
        self.assertEqual(rows[0].split("\t"), core.MANIFEST_COLUMNS)
        by_file = {r.split("\t")[0]: dict(zip(core.MANIFEST_COLUMNS, r.split("\t"))) for r in rows[1:]}
        derived = by_file["alpha_region_2026-10-22.txt"]
        self.assertEqual(derived["class"], "keep")
        # Upstream names without a date get the fetch date (folder rule 5).
        self.assertEqual(derived["derived_from"], "alpha_2026-10-22.txt")
        self.assertEqual(by_file["alpha_2026-10-22.txt"]["derived_from"], "")

    def test_nothing_outside_incoming_on_failure(self):
        bad = [self.src("alpha", "/a/alpha.txt", convert="test-broken")]
        core.run(bad, self.register, self.ctx)
        folder = os.path.join(self.ctx.library_root, "03_Geo/07_Overlays/01_Surveillance")
        self.assertEqual(os.listdir(os.path.join(folder, "01_Source")), [])
        self.assertEqual(os.listdir(os.path.join(folder, "02_ATAK")), [])
        self.assertEqual(os.listdir(os.path.join(self.ctx.library_root, "01_Incoming")), [])

    def test_readmes_written_for_both_shares(self):
        core.run([], self.register, self.ctx)
        lib = open(os.path.join(self.ctx.library_root, "00_Meta/README.md")).read()
        per = open(os.path.join(self.ctx.personal_root, "00_Meta/README.md")).read()
        self.assertEqual(lib, lc.render_readme(self.register, "library"))
        self.assertIn("Ham_Repeaters", per)
        self.assertNotIn("Ham_Repeaters", lib)


if __name__ == "__main__":
    unittest.main()
