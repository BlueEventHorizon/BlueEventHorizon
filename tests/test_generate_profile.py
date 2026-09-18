import importlib.util
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ElementTree
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "generate_profile.py"
SPEC = importlib.util.spec_from_file_location("generate_profile", MODULE_PATH)
assert SPEC and SPEC.loader
generate_profile = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = generate_profile
SPEC.loader.exec_module(generate_profile)


class ProfileGeneratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data = generate_profile.load_fixture(
            ROOT / "tests" / "fixtures" / "github-profile.json"
        )

    def test_calculates_stats_without_counting_forks_as_original_work(self) -> None:
        stats = generate_profile.calculate_stats(self.data, date(2026, 9, 18))

        self.assertEqual(stats.public_repos, 5)
        self.assertEqual(stats.original_projects, 4)
        self.assertEqual(stats.stars_earned, 55)
        self.assertEqual(stats.forks_earned, 7)
        self.assertEqual(stats.followers, 42)
        self.assertEqual(stats.years_on_github, 10)
        self.assertEqual(
            stats.repository_languages,
            (("Swift", 2), ("Dart", 1), ("Python", 1)),
        )
        self.assertEqual(
            stats.code_languages,
            (("Swift", 900000), ("Dart", 200000), ("Python", 100000)),
        )
        self.assertEqual(stats.contributions_last_year, 780)
        self.assertEqual(stats.lifetime_contributions, 12345)
        self.assertEqual(stats.total_commits, 2345)
        self.assertEqual(stats.total_pull_requests, 123)
        self.assertEqual(stats.total_issues, 45)
        self.assertEqual(stats.contributed_repositories, 24)
        self.assertEqual(stats.contribution_months[0], ("2025-10", 10))
        self.assertEqual(stats.contribution_months[-1], ("2026-09", 120))
        self.assertEqual(stats.recent_commit_count, 4)
        self.assertEqual(stats.recent_commit_hours[5], 1)
        self.assertEqual(stats.recent_commit_hours[9], 2)
        self.assertEqual(stats.recent_commit_hours[23], 1)

    def test_renders_deterministic_valid_svg_and_escapes_user_content(self) -> None:
        self.data["user"]["name"] = "K2 <Terada> & Co"
        stats = generate_profile.calculate_stats(self.data, date(2026, 9, 18))

        first = generate_profile.render_dashboard(self.data, stats)
        second = generate_profile.render_dashboard(self.data, stats)

        self.assertEqual(first, second)
        self.assertIn("K2 &lt;Terada&gt; &amp; Co", first)
        self.assertNotIn("generated at", first.casefold())
        self.assertIn("Languages by Code Size", first)
        self.assertIn("Recent Public Commits", first)
        self.assertIn("12.3K", first)
        self.assertNotIn("<image", first)
        self.assertNotIn("<script", first)
        self.assertNotIn('href="http', first)
        ElementTree.fromstring(first)

    def test_groups_small_language_segments_as_other(self) -> None:
        grouped = generate_profile._top_with_other(
            (("Swift", 10), ("Python", 8), ("Go", 6), ("Dart", 4), ("Ruby", 2))
        )

        self.assertEqual(
            grouped,
            (("Swift", 10), ("Python", 8), ("Go", 6), ("Dart", 4), ("Other", 2)),
        )

    def test_chooses_compact_axis_maximum_around_one_thousand(self) -> None:
        cases = {
            0: 4,
            800: 800,
            801: 1000,
            904: 1000,
            1000: 1000,
            1001: 2000,
        }

        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(generate_profile._nice_axis_max(value), expected)

    def test_write_if_changed_skips_identical_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "dashboard.svg"

            self.assertTrue(generate_profile.write_if_changed(output, "<svg/>\n"))
            self.assertFalse(generate_profile.write_if_changed(output, "<svg/>\n"))
            self.assertEqual(output.read_text(encoding="utf-8"), "<svg/>\n")


if __name__ == "__main__":
    unittest.main()
