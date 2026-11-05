"""The limits appear as literals in prose and in one Python string.

These tests pin those literals to RECENT_FORM_LIMITS so the constant stays the
single source of truth even where a human-readable number is spelled out.
"""

from __future__ import annotations

import unittest

from tests._support import import_fetch_espn, read_text


class DocsIntegrityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fetch_espn = import_fetch_espn()
        cls.limits = cls.fetch_espn.RECENT_FORM_LIMITS
        cls.readme = read_text("README.md")
        cls.summarize = read_text("scripts/summarize.py")

    def test_readme_states_the_limits(self):
        for pos, limit in self.limits.items():
            self.assertIn(f"{limit} {pos}", self.readme, f"{limit} {pos}")
        self.assertIn(str(sum(self.limits.values())), self.readme)

    def test_readme_documents_the_artifact_and_the_tests(self):
        for token in (
            "recent_form_rankings.json",
            "is_jason_x",
            "fantasy_team_id",
            "filterStatsForTopScoringPeriodIds",
            "appliedTotal",
            "python -m unittest discover -s tests",
        ):
            self.assertIn(token, self.readme, token)

    def test_readme_states_the_pdf_limitation(self):
        # The one requirement this repository cannot verify must be stated,
        # not quietly dropped.
        self.assertIn("cannot be verified here", self.readme)

    def test_summarize_files_table_states_the_limits(self):
        for pos, limit in self.limits.items():
            self.assertIn(f"{limit} {pos}", self.summarize, f"{limit} {pos}")


if __name__ == "__main__":
    unittest.main()
