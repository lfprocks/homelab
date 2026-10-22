"""Pure-logic tests for the Kiwix library sync (apps/kiwix/the-intersect/kiwix_sync.py)."""
import copy
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "..", "the-intersect")
sys.path.insert(0, APP)

import kiwix_sync  # noqa: E402

CONFIG_PATH = os.path.join(APP, "library.json")


def listing(*names):
    rows = "\n".join(f'<a href="{n}">{n}</a>  2026-01-01 00:00  1G' for n in names)
    return f"<html><body><pre>\n{rows}\n</pre></body></html>"


class NewestBuildTest(unittest.TestCase):
    def test_newest_build_picks_latest_date(self):
        html = listing("wikipedia_en_all_maxi_2026-02.zim", "wikipedia_en_all_maxi_2026-08.zim")
        self.assertEqual(kiwix_sync.newest_build(html, "wikipedia_en_all_maxi"), "wikipedia_en_all_maxi_2026-08.zim")

    def test_newest_build_ignores_longer_book_names(self):
        html = listing(
            "wikipedia_en_all_maxi_2026-02.zim",
            "wikipedia_en_all_maxi_2026-08.zim",
            "wikipedia_en_all_maxi_medicine_2026-10.zim",
        )
        self.assertEqual(kiwix_sync.newest_build(html, "wikipedia_en_all_maxi"), "wikipedia_en_all_maxi_2026-08.zim")

    def test_newest_build_none_when_absent(self):
        html = listing("wiktionary_en_all_nopic_2026-08.zim")
        self.assertIsNone(kiwix_sync.newest_build(html, "wikipedia_en_all_maxi"))


class RetentionTest(unittest.TestCase):
    def test_builds_to_delete_keeps_two_newest(self):
        files = [
            "serverfault.com_en_all_2026-02.zim",
            "serverfault.com_en_all_2025-12.zim",
            "serverfault.com_en_all_2026-08.zim",
        ]
        self.assertEqual(kiwix_sync.builds_to_delete(files, "serverfault.com_en_all"), ["serverfault.com_en_all_2025-12.zim"])

    def test_builds_to_delete_ignores_other_books_and_parts(self):
        files = [
            "serverfault.com_en_all_2025-01.zim.part",
            "superuser.com_en_all_2024-01.zim",
            "serverfault.com_en_all_2026-02.zim",
            "serverfault.com_en_all_2026-08.zim",
            "library.xml",
        ]
        self.assertEqual(kiwix_sync.builds_to_delete(files, "serverfault.com_en_all"), [])


class ValidateTest(unittest.TestCase):
    def setUp(self):
        self.config = kiwix_sync.load_config(CONFIG_PATH)

    def test_validate_accepts_repo_config(self):
        self.assertEqual(kiwix_sync.validate(self.config), [])

    def test_validate_rejects_bad_folder_name(self):
        for bad in ("02_kiwix", "2_Kiwix"):
            config = copy.deepcopy(self.config)
            config["register"].append({"path": bad, "state": "reserved", "purpose": "x"})
            self.assertTrue(kiwix_sync.validate(config), bad)

    def test_validate_rejects_duplicate_number_under_parent(self):
        config = copy.deepcopy(self.config)
        config["register"].append({"path": "02_Other", "state": "reserved", "purpose": "x"})
        self.assertTrue(kiwix_sync.validate(config))

    def test_validate_rejects_unregistered_target(self):
        config = copy.deepcopy(self.config)
        config["wanted"][0]["folder"] = "02_Kiwix/09_Nope"
        self.assertTrue(kiwix_sync.validate(config))

    def test_validate_rejects_reserved_target(self):
        config = copy.deepcopy(self.config)
        config["wanted"][0]["folder"] = "04_Datasets"
        self.assertTrue(kiwix_sync.validate(config))


class SkeletonAndReadmeTest(unittest.TestCase):
    def setUp(self):
        self.config = kiwix_sync.load_config(CONFIG_PATH)

    def test_skeleton_skips_reserved(self):
        dirs = kiwix_sync.skeleton_dirs(self.config)
        self.assertNotIn("03_Geo/01_Imagery", dirs)
        self.assertNotIn("04_Datasets", dirs)
        self.assertIn("02_Kiwix/03_Stack_Exchange", dirs)
        self.assertEqual(dirs, sorted(dirs))

    def test_readme_lists_every_register_entry_and_rules(self):
        readme = kiwix_sync.render_readme(self.config)
        for entry in self.config["register"]:
            if entry.get("share", "library") == "library":
                self.assertIn(entry["path"], readme)
        geo_line = next(line for line in readme.splitlines() if "04_Datasets" in line)
        self.assertIn("reserved", geo_line)
        self.assertIn("Numbers are never reused, renumbered or renamed.", readme)


class ManifestAndSourceTest(unittest.TestCase):
    def test_manifest_row_is_tab_separated(self):
        row = kiwix_sync.manifest_row("a.zim", "https://x/a.zim", "ab" * 32, 42, "2026-10-22T05:00:00Z", "CC BY-SA 4.0")
        self.assertTrue(row.endswith("\n"))
        self.assertEqual(len(row.rstrip("\n").split("\t")), 6)
        self.assertEqual(len(kiwix_sync.MANIFEST_HEADER.rstrip("\n").split("\t")), 6)

    def test_script_has_no_flux_substitution_syntax(self):
        with open(os.path.join(APP, "kiwix_sync.py")) as f:
            self.assertNotIn("${", f.read())


if __name__ == "__main__":
    unittest.main()
