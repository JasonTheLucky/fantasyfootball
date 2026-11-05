"""Ranking limits, counts, and the metadata the step reports."""

from __future__ import annotations

import json
import unittest

from tests._support import (
    import_fetch_espn,
    load_artifact_or_fail,
    load_json,
)

EXPECTED_LIMITS = {"QB": 24, "RB": 60, "WR": 60, "TE": 24, "K": 24, "D/ST": 20}
EXPECTED_TOTAL = 212


class RecentFormLimitsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fetch_espn = import_fetch_espn()
        cls.artifact = load_artifact_or_fail(cls)
        cls.limits = cls.fetch_espn.RECENT_FORM_LIMITS
        cls.counts = cls.artifact["counts"]
        cls.rankings = cls.artifact["rankings"]

    def test_limits_are_the_specified_values(self):
        self.assertEqual(dict(self.limits), EXPECTED_LIMITS)
        self.assertEqual(sum(self.limits.values()), EXPECTED_TOTAL)

    def test_limits_order_is_qb_rb_wr_te_k_dst(self):
        # The artifact, the digest and the PDF all rely on this order.
        self.assertEqual(list(self.limits), ["QB", "RB", "WR", "TE", "K", "D/ST"])

    def test_artifact_limits_match_the_constant(self):
        self.assertEqual(self.artifact["limits"], dict(self.limits))
        self.assertEqual(self.artifact["expected_total"], sum(self.limits.values()))

    def test_counts_block_matches_the_constant(self):
        self.assertEqual(set(self.counts), set(self.limits))
        for pos, limit in self.limits.items():
            self.assertEqual(self.counts[pos]["limit"], limit, pos)

    def test_ranked_counts_equal_list_lengths(self):
        for pos in self.limits:
            self.assertEqual(
                self.counts[pos]["ranked"], len(self.rankings[pos]), pos
            )

    def test_ranked_equals_min_limit_qualifying(self):
        for pos, limit in self.limits.items():
            block = self.counts[pos]
            self.assertEqual(
                block["ranked"], min(limit, block["qualifying"]), pos
            )
            self.assertGreaterEqual(block["qualifying"], block["ranked"], pos)

    def test_sufficient_flag_is_consistent(self):
        for pos, limit in self.limits.items():
            block = self.counts[pos]
            self.assertIs(block["sufficient"], block["ranked"] == limit, pos)
        self.assertIs(
            self.artifact["all_positions_sufficient"],
            all(b["sufficient"] for b in self.counts.values()),
        )

    def test_total_matches_the_lists(self):
        self.assertEqual(
            self.artifact["ranked_total"],
            sum(len(self.rankings[pos]) for pos in self.limits),
        )

    def test_total_is_212_when_every_position_is_sufficient(self):
        if not self.artifact["all_positions_sufficient"]:
            short = [p for p, b in self.counts.items() if not b["sufficient"]]
            self.skipTest(f"positions below their limit: {short}")
        self.assertEqual(self.artifact["ranked_total"], EXPECTED_TOTAL)

    def test_games_required_is_three(self):
        self.assertEqual(self.fetch_espn.RECENT_FORM_GAMES, 3)
        self.assertEqual(self.artifact["weeks"]["games_required"], 3)

    def test_jason_x_constants(self):
        self.assertEqual(self.fetch_espn.JASON_X_TEAM_ID, 1)
        self.assertEqual(self.fetch_espn.JASON_X_TEAM_NAME, "Jason X")
        own = self.artifact["ownership"]
        self.assertEqual(own["jason_x_team_id"], self.fetch_espn.JASON_X_TEAM_ID)
        self.assertEqual(own["jason_x_team_name"], self.fetch_espn.JASON_X_TEAM_NAME)

    def test_metadata_reports_the_step(self):
        metadata = load_json("metadata.json")
        if "recent_form_success" not in metadata:
            self.skipTest("metadata.json predates the recent-form step")
        self.assertIsInstance(metadata["recent_form_success"], bool)
        self.assertIsInstance(metadata["recent_form_ranked_total"], int)
        self.assertGreaterEqual(metadata["recent_form_ranked_total"], 0)

    def test_metadata_ranked_total_matches_the_artifact(self):
        # The artifact stamps the run's started_at, so equality means one run
        # wrote both files. This is the assertion that catches the metadata
        # parameter being defaulted instead of passed at the call site.
        metadata = load_json("metadata.json")
        if "recent_form_ranked_total" not in metadata:
            self.skipTest("metadata.json predates the recent-form step")
        if metadata.get("started_at") != self.artifact.get("fetched_at"):
            self.skipTest("metadata.json and the artifact are from different runs")
        self.assertEqual(
            metadata["recent_form_ranked_total"], self.artifact["ranked_total"]
        )

    def test_critical_success_ignores_the_step(self):
        metadata = load_json("metadata.json")
        self.assertEqual(
            metadata["critical_success"],
            bool(
                metadata["league_fetch_success"] and metadata["roster_fetch_success"]
            ),
        )

    def test_artifact_is_valid_json_with_a_usage_string(self):
        self.assertIsInstance(self.artifact["usage"], str)
        self.assertGreater(len(self.artifact["usage"]), 200)
        # Round-trips, so the committed file cannot be subtly malformed.
        json.dumps(self.artifact)


if __name__ == "__main__":
    unittest.main()
