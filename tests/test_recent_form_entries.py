"""Per-entry invariants: three games, correct averages, correct ordering."""

from __future__ import annotations

import math
import unittest

from tests._support import import_fetch_espn, is_degraded, load_artifact_or_fail


class RecentFormEntriesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fetch_espn = import_fetch_espn()
        cls.artifact = load_artifact_or_fail(cls)
        cls.limits = cls.fetch_espn.RECENT_FORM_LIMITS
        cls.rankings = cls.artifact["rankings"]
        cls.counts = cls.artifact["counts"]
        cls.weeks = cls.artifact["weeks"]

    def each_entry(self):
        for pos in self.limits:
            for entry in self.rankings[pos]:
                yield pos, entry

    def test_every_entry_has_exactly_three_games(self):
        required = self.artifact["weeks"]["games_required"]
        for pos, entry in self.each_entry():
            self.assertEqual(len(entry["games"]), required, f"{pos} {entry['name']}")

    def test_scoring_periods_are_distinct_and_in_range(self):
        considered = set(self.weeks["considered"])
        for pos, entry in self.each_entry():
            periods = [g["scoring_period_id"] for g in entry["games"]]
            self.assertEqual(len(set(periods)), len(periods), entry["name"])
            for period in periods:
                self.assertIsInstance(period, int)
                self.assertGreaterEqual(period, 1)
                self.assertIn(period, considered, entry["name"])
            # Oldest first, which is the PDF's left-to-right order.
            self.assertEqual(periods, sorted(periods), entry["name"])

    def test_every_game_has_a_finite_numeric_score(self):
        for pos, entry in self.each_entry():
            for game in entry["games"]:
                points = game["points"]
                self.assertIsInstance(points, (int, float), entry["name"])
                self.assertFalse(isinstance(points, bool), entry["name"])
                self.assertTrue(math.isfinite(points), entry["name"])

    def test_average_equals_mean_of_its_games(self):
        for pos, entry in self.each_entry():
            points = [g["points"] for g in entry["games"]]
            expected = sum(points) / len(points)
            self.assertLessEqual(
                abs(entry["avg_3_game_exact"] - expected), 1e-9, entry["name"]
            )
            self.assertLessEqual(
                abs(entry["avg_3_game"] - entry["avg_3_game_exact"]), 5e-7,
                entry["name"],
            )

    def test_display_average_matches_the_numeric_average(self):
        for pos, entry in self.each_entry():
            self.assertEqual(
                entry["avg_3_game_display"],
                f"{entry['avg_3_game_exact']:.2f}",
                entry["name"],
            )
            self.assertEqual(len(entry["avg_3_game_display"].split(".")[1]), 2)

    def test_lists_are_sorted_by_unrounded_average_descending(self):
        # Non-increasing, not strictly decreasing: ties are real in this data,
        # so a strict assertion would fail on correct output.
        for pos in self.limits:
            rows = self.rankings[pos]
            for prev, nxt in zip(rows, rows[1:]):
                self.assertGreaterEqual(
                    prev["avg_3_game_exact"], nxt["avg_3_game_exact"],
                    f"{pos}: {prev['name']} then {nxt['name']}",
                )

    def test_ties_are_broken_by_ascending_player_id(self):
        for pos in self.limits:
            rows = self.rankings[pos]
            for prev, nxt in zip(rows, rows[1:]):
                if prev["avg_3_game_exact"] == nxt["avg_3_game_exact"]:
                    self.assertLess(
                        prev["player_id"], nxt["player_id"],
                        f"{pos}: {prev['name']} then {nxt['name']}",
                    )

    def test_ranks_are_one_based_and_contiguous(self):
        for pos in self.limits:
            rows = self.rankings[pos]
            self.assertEqual(
                [e["rank"] for e in rows], list(range(1, len(rows) + 1)), pos
            )

    def test_player_ids_are_unique_within_and_across_positions(self):
        seen: dict[int, str] = {}
        for pos in self.limits:
            ids = [e["player_id"] for e in self.rankings[pos]]
            self.assertEqual(len(set(ids)), len(ids), f"duplicate id within {pos}")
            for pid in ids:
                self.assertNotIn(pid, seen, f"{pid} in both {seen.get(pid)} and {pos}")
                seen[pid] = pos

    def test_entry_position_matches_its_list(self):
        for pos, entry in self.each_entry():
            self.assertEqual(entry["position"], pos, entry["name"])

    def test_names_are_non_empty_strings(self):
        for pos, entry in self.each_entry():
            self.assertIsInstance(entry["name"], str)
            self.assertNotEqual(entry["name"].strip(), "")

    def test_weeks_used_covers_the_entries(self):
        considered = set(self.weeks["considered"])
        for pos in self.limits:
            union = sorted(
                {g["scoring_period_id"] for e in self.rankings[pos] for g in e["games"]}
            )
            self.assertEqual(self.counts[pos]["weeks_used"], union, pos)
            self.assertTrue(set(union) <= considered, pos)

    def test_scoring_crosscheck_within_tolerance(self):
        check = self.artifact["scoring_crosscheck"]
        if check["values_checked"] == 0:
            # The scoring-table-missing state must be internally consistent.
            self.assertIsNone(check["max_abs_delta"])
            self.assertIsNone(check["ok"])
            self.assertIsNone(check["worst"])
            self.skipTest("cross-check disabled: no scoring table was available")
        if is_degraded(self.artifact):
            self.skipTest("degraded artifact: weeks.failed is not empty")
        self.assertEqual(check["values_checked"], 3 * self.artifact["ranked_total"])
        self.assertLessEqual(check["max_abs_delta"], check["tolerance"])
        self.assertIs(check["ok"], True)

    def test_weeks_block_is_coherent(self):
        # Structural only: weeks.failed being non-empty is a legitimate degraded
        # state the pipeline is designed to produce and the Action commits, so
        # asserting its emptiness would turn one ESPN 5xx into a red suite.
        considered = self.weeks["considered"]
        self.assertEqual(considered, sorted(considered))
        self.assertEqual(considered[0], 1)
        with_data = set(self.weeks["with_data"])
        failed = set(self.weeks["failed"])
        self.assertTrue(with_data <= set(considered))
        self.assertTrue(failed <= set(considered))
        self.assertEqual(with_data & failed, set())

    def test_zero_point_games_are_real_appearances(self):
        # A 0.00 game is only legitimate when the player actually played. The
        # fetch drops did-not-play rows, so any 0.00 that survives into a ranked
        # entry must be an appearance -- and the count of kept zero-point games
        # must be able to account for every 0.00 in the tables.
        zeros = sum(
            1 for _pos, entry in self.each_entry()
            for game in entry["games"] if game["points"] == 0.0
        )
        kept = self.artifact["diagnostics"]["zero_point_games_kept"]
        self.assertLessEqual(
            zeros, kept,
            f"{zeros} zero-point games are ranked but only {kept} were kept as "
            "real appearances",
        )

    def test_not_played_rows_were_excluded(self):
        # The corrected rule must actually be doing work. ESPN emits a 0.0 row
        # for every rostered player who did not play, so on a healthy multi-week
        # snapshot this count is necessarily large; if it were 0 the filter
        # would have silently stopped running.
        diag = self.artifact["diagnostics"]
        if is_degraded(self.artifact) or len(self.weeks["with_data"]) < 2:
            self.skipTest("too few weeks fetched to expect did-not-play rows")
        self.assertGreater(
            diag["not_played_excluded"], 0,
            "no did-not-play rows were excluded; the participation filter looks "
            "inert",
        )
        # Every collected value is a real appearance, so the collected total and
        # the kept-zero count cannot exceed each other's bounds.
        self.assertLessEqual(
            diag["zero_point_games_kept"], diag["weekly_values_collected"]
        )

    def test_diagnostics_are_present_and_plausible(self):
        diag = self.artifact["diagnostics"]
        for key in (
            "players_seen", "weekly_values_collected", "invalid_values",
            "not_played_excluded", "zero_point_games_kept", "espn_requests",
            "page_size",
        ):
            self.assertIn(key, diag)
            self.assertIsInstance(diag[key], int, key)
            self.assertGreaterEqual(diag[key], 0, key)
        self.assertEqual(diag["page_size"], self.fetch_espn.RECENT_FORM_PAGE_SIZE)
        # Bounds, not just types: a players_seen sourced from the wrong dict or a
        # weekly_values_collected that counted only ranked values fails these.
        total = self.artifact["ranked_total"]
        self.assertGreaterEqual(diag["players_seen"], total)
        self.assertGreaterEqual(diag["weekly_values_collected"], 3 * total)


if __name__ == "__main__":
    unittest.main()
