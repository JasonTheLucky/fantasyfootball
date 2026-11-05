"""Builder and helper unit tests on synthetic input.

These exercise the rules without depending on what the live league looks like
today, so they stay meaningful when the scheduled Action rewrites data/.
"""

from __future__ import annotations

import unittest

from tests._support import import_fetch_espn


def row(points, recomputed=None, applied_raw=None):
    return {
        "points": points,
        "applied_raw": applied_raw if applied_raw is not None else points,
        "recomputed": recomputed,
    }


def meta_entry(position, name="Player", pro_team="KC", position_id=1):
    return {
        "name": name,
        "position": position,
        "pro_team": pro_team,
        "position_id": position_id,
    }


class BuildRecentFormTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fe = import_fetch_espn()

    def build(self, weekly, meta, index=None, limits=None):
        return self.fe.build_recent_form(
            weekly, meta, index or {}, [1, 2, 3, 4, 5, 6, 7, 8],
            limits=limits or {"QB": 24, "RB": 60, "WR": 60, "TE": 24, "K": 24, "D/ST": 20},
        )

    def test_fewer_than_three_games_is_excluded(self):
        out = self.build({1: {7: row(10.0), 8: row(12.0)}}, {1: meta_entry("QB")})
        self.assertEqual(out["rankings"]["QB"], [])
        self.assertEqual(out["counts"]["QB"]["qualifying"], 0)

    def test_exactly_three_games_is_included(self):
        out = self.build(
            {1: {6: row(10.0), 7: row(12.0), 8: row(14.0)}}, {1: meta_entry("QB")}
        )
        self.assertEqual(len(out["rankings"]["QB"]), 1)
        self.assertAlmostEqual(out["rankings"]["QB"][0]["avg_3_game_exact"], 12.0)

    def test_more_than_three_takes_the_latest_weeks(self):
        # Weeks 1-3 score higher, but the window is the three highest weeks.
        weekly = {1: {w: row(float(100 - w)) for w in range(1, 9)}}
        out = self.build(weekly, {1: meta_entry("QB")})
        periods = [g["scoring_period_id"] for g in out["rankings"]["QB"][0]["games"]]
        self.assertEqual(periods, [6, 7, 8])

    def test_zero_point_game_counts(self):
        out = self.build(
            {1: {6: row(0.0), 7: row(12.0), 8: row(12.0)}}, {1: meta_entry("QB")}
        )
        self.assertEqual(len(out["rankings"]["QB"]), 1)
        self.assertAlmostEqual(out["rankings"]["QB"][0]["avg_3_game_exact"], 8.0)

    def test_bye_week_gap_is_skipped(self):
        out = self.build(
            {1: {5: row(10.0), 7: row(10.0), 8: row(10.0)}}, {1: meta_entry("QB")}
        )
        periods = [g["scoring_period_id"] for g in out["rankings"]["QB"][0]["games"]]
        self.assertEqual(periods, [5, 7, 8])

    def test_limit_truncates_and_counts_report_the_pool(self):
        weekly = {i: {6: row(float(i)), 7: row(float(i)), 8: row(float(i))}
                  for i in range(1, 31)}
        meta = {i: meta_entry("QB", name=f"QB{i}") for i in range(1, 31)}
        out = self.build(weekly, meta)
        self.assertEqual(out["counts"]["QB"]["ranked"], 24)
        self.assertEqual(out["counts"]["QB"]["qualifying"], 30)
        self.assertIs(out["counts"]["QB"]["sufficient"], True)

    def test_shortfall_reports_actual_count(self):
        weekly = {i: {6: row(1.0), 7: row(1.0), 8: row(1.0)} for i in range(1, 6)}
        meta = {i: meta_entry("K", name=f"K{i}", position_id=5) for i in range(1, 6)}
        out = self.build(weekly, meta)
        self.assertEqual(out["counts"]["K"]["ranked"], 5)
        self.assertEqual(out["counts"]["K"]["qualifying"], 5)
        self.assertIs(out["counts"]["K"]["sufficient"], False)
        self.assertEqual(len(out["rankings"]["K"]), 5)
        self.assertIs(out["all_positions_sufficient"], False)

    def test_tie_order_is_player_id_ascending(self):
        weekly = {
            900: {6: row(10.0), 7: row(10.0), 8: row(10.0)},
            100: {6: row(10.0), 7: row(10.0), 8: row(10.0)},
        }
        meta = {900: meta_entry("QB", name="High"), 100: meta_entry("QB", name="Low")}
        out = self.build(weekly, meta)
        self.assertEqual([e["player_id"] for e in out["rankings"]["QB"]], [100, 900])

    def test_sort_uses_the_unrounded_average(self):
        # Differ only below the sixth decimal: rounding would tie them.
        weekly = {
            1: {6: row(10.0), 7: row(10.0), 8: row(10.0000001)},
            2: {6: row(10.0), 7: row(10.0), 8: row(10.0)},
        }
        meta = {1: meta_entry("QB", name="A"), 2: meta_entry("QB", name="B")}
        out = self.build(weekly, meta)
        self.assertEqual([e["player_id"] for e in out["rankings"]["QB"]], [1, 2])

    def test_weeks_used_is_the_union(self):
        weekly = {
            1: {6: row(1.0), 7: row(1.0), 8: row(1.0)},
            2: {5: row(1.0), 7: row(1.0), 8: row(1.0)},
        }
        meta = {1: meta_entry("QB", name="A"), 2: meta_entry("QB", name="B")}
        out = self.build(weekly, meta)
        self.assertEqual(out["counts"]["QB"]["weeks_used"], [5, 6, 7, 8])

    def test_diagnostics_formulas(self):
        # meta has 3 players, weekly has 5 values, ranked_total is 1.
        weekly = {1: {6: row(1.0), 7: row(1.0), 8: row(1.0)}, 2: {7: row(1.0), 8: row(1.0)}}
        meta = {
            1: meta_entry("QB", name="A"),
            2: meta_entry("QB", name="B"),
            3: meta_entry("QB", name="C"),
        }
        out = self.build(weekly, meta)
        self.assertEqual(out["diagnostics"]["players_seen"], 3)
        self.assertEqual(out["diagnostics"]["weekly_values_collected"], 5)
        self.assertEqual(out["ranked_total"], 1)

    def test_unranked_positions_are_ignored(self):
        weekly = {
            1: {6: row(50.0), 7: row(50.0), 8: row(50.0)},
            2: {6: row(50.0), 7: row(50.0), 8: row(50.0)},
        }
        meta = {
            1: meta_entry("P", name="Punter", position_id=6),
            2: meta_entry("UNKNOWN", name="Mystery", position_id=99),
        }
        out = self.build(weekly, meta)
        self.assertEqual(out["ranked_total"], 0)
        for block in out["counts"].values():
            self.assertEqual(block["qualifying"], 0)

    def test_name_falls_back_to_the_index_then_to_the_id(self):
        weekly = {
            1: {6: row(1.0), 7: row(1.0), 8: row(1.0)},
            2: {6: row(1.0), 7: row(1.0), 8: row(1.0)},
        }
        meta = {1: meta_entry("QB", name=None), 2: meta_entry("QB", name=None)}
        index = {"1": {"name": "From Index", "fantasy_status": "AVAILABLE"}}
        out = self.build(weekly, meta, index=index)
        names = {e["player_id"]: e["name"] for e in out["rankings"]["QB"]}
        self.assertEqual(names[1], "From Index")
        self.assertEqual(names[2], "Player 2")

    def test_ownership_block_entries_and_resolved(self):
        weekly = {1: {6: row(1.0), 7: row(1.0), 8: row(1.0)}}
        out = self.build(weekly, {1: meta_entry("QB")}, index={})
        own = out["ownership"]
        self.assertEqual(own["unknown"], 1)
        self.assertEqual(own["entries"], out["ranked_total"])
        self.assertEqual(own["resolved"], own["entries"] - 1)

    def test_name_id_disagreement_is_recorded(self):
        weekly = {1: {6: row(1.0), 7: row(1.0), 8: row(1.0)}}
        index = {
            "1": {
                "fantasy_status": "ROSTERED",
                "fantasy_team": "Jason X Renamed",
                "fantasy_team_id": 1,
            }
        }
        out = self.build(weekly, {1: meta_entry("QB")}, index=index)
        own = out["ownership"]
        self.assertIs(own["jason_x_name_id_agree"], False)
        self.assertEqual(own["jason_x_by_id_count"], 1)
        self.assertEqual(own["jason_x_by_name_count"], 0)
        self.assertEqual(own["jason_x_team_name_observed"], "Jason X Renamed")
        self.assertIs(out["rankings"]["QB"][0]["is_jason_x"], True)

    def test_name_id_agreement_when_they_match(self):
        weekly = {1: {6: row(1.0), 7: row(1.0), 8: row(1.0)}}
        index = {
            "1": {
                "fantasy_status": "ROSTERED",
                "fantasy_team": "Jason X",
                "fantasy_team_id": 1,
            }
        }
        out = self.build(weekly, {1: meta_entry("QB")}, index=index)
        own = out["ownership"]
        self.assertIs(own["jason_x_name_id_agree"], True)
        self.assertEqual(own["jason_x_by_id_count"], own["jason_x_by_name_count"])

    def test_crosscheck_skips_none_recomputed(self):
        weekly = {1: {6: row(1.0), 7: row(1.0), 8: row(1.0)}}
        out = self.build(weekly, {1: meta_entry("QB")})
        check = out["scoring_crosscheck"]
        self.assertEqual(check["values_checked"], 0)
        self.assertIsNone(check["max_abs_delta"])
        self.assertIsNone(check["ok"])
        self.assertIsNone(check["worst"])
        self.assertIn("note", check)

    def test_crosscheck_uses_the_raw_applied_total(self):
        # points is rounded, applied_raw is not; the delta must use applied_raw.
        weekly = {
            1: {
                6: row(12.34, recomputed=12.344, applied_raw=12.344),
                7: row(12.34, recomputed=12.344, applied_raw=12.344),
                8: row(12.34, recomputed=12.344, applied_raw=12.344),
            }
        }
        out = self.build(weekly, {1: meta_entry("QB")})
        check = out["scoring_crosscheck"]
        self.assertEqual(check["values_checked"], 3)
        self.assertEqual(check["max_abs_delta"], 0.0)
        self.assertIs(check["ok"], True)

    def test_crosscheck_reports_the_worst_offender(self):
        weekly = {
            1: {
                6: row(10.0, recomputed=10.0),
                7: row(10.0, recomputed=10.0),
                8: row(10.0, recomputed=3.0),
            }
        }
        out = self.build(weekly, {1: meta_entry("QB", name="Wrong")})
        check = out["scoring_crosscheck"]
        self.assertIs(check["ok"], False)
        self.assertEqual(check["worst"]["name"], "Wrong")
        self.assertEqual(check["worst"]["scoring_period_id"], 8)
        self.assertAlmostEqual(check["worst"]["abs_delta"], 7.0)


class HelperTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fe = import_fetch_espn()

    def test_extract_weekly_entry_selects_the_right_row(self):
        player = {
            "stats": [
                {"statSourceId": 0, "scoringPeriodId": 0, "seasonId": 2026,
                 "appliedTotal": 300.0, "stats": {"1": 1}},
                {"statSourceId": 1, "scoringPeriodId": 8, "seasonId": 2026,
                 "appliedTotal": 20.0, "stats": {"2": 2}},
                {"statSourceId": 0, "scoringPeriodId": 8, "seasonId": 2025,
                 "appliedTotal": 99.0, "stats": {"3": 3}},
                {"statSourceId": 0, "scoringPeriodId": 8, "seasonId": 2026,
                 "appliedTotal": 26.8, "stats": {"42": 155.0}},
            ]
        }
        found = self.fe.extract_weekly_entry(player, 2026, 8)
        self.assertEqual(found[0], 26.8)
        self.assertEqual(found[1], {"42": 155.0})
        self.assertIsNone(self.fe.extract_weekly_entry(player, 2026, 7))

    def test_extract_weekly_entry_rejects_unusable_totals(self):
        for bad in (None, "x", float("nan"), float("inf")):
            player = {"stats": [{"statSourceId": 0, "scoringPeriodId": 8,
                                 "seasonId": 2026, "appliedTotal": bad}]}
            self.assertIsNone(self.fe.extract_weekly_entry(player, 2026, 8), bad)

    def test_recompute_coerces_string_stat_keys(self):
        items = {53: {"statId": 53, "points": 1.0}}
        self.assertAlmostEqual(
            self.fe.recompute_applied_total({"53": 2}, items, 1), 2.0
        )

    def test_recompute_applied_total_honours_points_overrides(self):
        items = {123: {"statId": 123, "points": 0.0, "pointsOverrides": {"16": -1.0}}}
        self.assertAlmostEqual(
            self.fe.recompute_applied_total({"123": 3}, items, 16), -3.0
        )
        self.assertAlmostEqual(
            self.fe.recompute_applied_total({"123": 3}, items, 1), 0.0
        )

    def test_recompute_ignores_unknown_and_junk(self):
        items = {1: {"statId": 1, "points": 2.0}}
        self.assertAlmostEqual(
            self.fe.recompute_applied_total(
                {"1": 1, "999": 5, "bad": 1, "2": "x"}, items, 1
            ),
            2.0,
        )
        self.assertEqual(self.fe.recompute_applied_total(None, items, 1), 0.0)

    def test_scoring_items_by_stat_id_tolerates_junk(self):
        settings = {"scoringItems": [
            {"statId": 3, "points": 1.0},
            "not a dict",
            {"points": 2.0},
            {"statId": "nope", "points": 3.0},
            {"statId": "7", "points": 4.0},
        ]}
        items = self.fe.scoring_items_by_stat_id(settings)
        self.assertEqual(set(items), {3, 7})
        self.assertEqual(self.fe.scoring_items_by_stat_id(None), {})
        self.assertEqual(self.fe.scoring_items_by_stat_id({}), {})

    def test_recent_form_weeks_range(self):
        weeks = self.fe.recent_form_weeks
        self.assertEqual(
            weeks({"latestScoringPeriod": 9, "finalScoringPeriod": 17}, 9),
            list(range(1, 10)),
        )
        self.assertEqual(
            weeks({"latestScoringPeriod": 20, "finalScoringPeriod": 17}, 20),
            list(range(1, 18)),
        )
        self.assertEqual(weeks({}, None), [])
        self.assertEqual(weeks({"latestScoringPeriod": "x"}, None), [])
        # Falls back to the passed scoring period when status lacks the key.
        self.assertEqual(weeks({}, 3), [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
