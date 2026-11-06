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


class ParticipationTest(unittest.TestCase):
    """The did-not-play bug: a 0.0 row is only a game if the player appeared.

    Shapes below are the ones ESPN actually emits, taken from live week 6-8
    responses for this league.
    """

    @classmethod
    def setUpClass(cls):
        cls.fe = import_fetch_espn()

    def played(self, raw):
        return self.fe.played_in_game(raw)

    def test_empty_stats_is_not_a_game(self):
        # Josh Jacobs week 6 and Mike Evans week 7 both looked exactly like this.
        self.assertFalse(self.played({}))
        self.assertFalse(self.played(None))

    def test_games_played_marker_means_played(self):
        # Tre Tucker week 8: appeared, scored 0.00.
        self.assertTrue(self.played({"58": 1.0, "155": 1.0, "210": 1.0}))
        # Zay Flowers week 8: five targets, no catch.
        self.assertTrue(self.played({"58": 5.0, "156": 1.0, "210": 1.0}))
        # Omar Cooper Jr. week 8: the minimum real appearance.
        self.assertTrue(self.played({"156": 1.0, "210": 1.0}))

    def test_stats_without_the_marker_is_not_a_game(self):
        # Defensive: other stats present but no games-played marker.
        self.assertFalse(self.played({"58": 1.0, "155": 1.0}))

    def test_marker_must_be_at_least_one(self):
        self.assertFalse(self.played({"210": 0}))
        self.assertFalse(self.played({"210": 0.0}))
        self.assertTrue(self.played({"210": 1}))
        self.assertTrue(self.played({"210": 2.0}))

    def test_marker_must_be_numeric(self):
        for junk in (None, "1", True, [], {}):
            self.assertFalse(self.played({"210": junk}), repr(junk))

    def test_a_scoring_game_is_always_a_game(self):
        self.assertTrue(
            self.played({"42": 118.0, "43": 1.0, "53": 6.0, "210": 1.0})
        )

    def test_the_users_scenario_end_to_end(self):
        """18.4 / did-not-play / 16.5 must not average in the zero."""
        weekly = {
            1: {
                5: row(18.4),
                6: row(22.1),
                7: row(0.0),      # did not play; excluded upstream by the fetch
                8: row(16.5),
            }
        }
        # Simulate the fetch having dropped week 7, which is what the corrected
        # played_in_game filter does before the builder ever sees it.
        del weekly[1][7]
        out = self.fe.build_recent_form(
            weekly, {1: meta_entry("RB")}, {}, [5, 6, 7, 8],
        )
        entry = out["rankings"]["RB"][0]
        periods = [g["scoring_period_id"] for g in entry["games"]]
        self.assertEqual(periods, [5, 6, 8])
        self.assertAlmostEqual(entry["avg_3_game_exact"], (18.4 + 22.1 + 16.5) / 3)
        self.assertEqual(entry["avg_3_game_display"], "19.00")

    def test_a_real_zero_still_counts(self):
        """A played-but-scoreless game belongs in the average."""
        weekly = {1: {6: row(18.0), 7: row(0.0), 8: row(12.0)}}
        out = self.fe.build_recent_form(
            weekly, {1: meta_entry("WR")}, {}, [6, 7, 8],
        )
        entry = out["rankings"]["WR"][0]
        self.assertEqual([g["points"] for g in entry["games"]], [18.0, 0.0, 12.0])
        self.assertAlmostEqual(entry["avg_3_game_exact"], 10.0)


class SeasonEndingExclusionTest(unittest.TestCase):
    """Out-for-season players are dropped before the slice, so limits backfill."""

    @classmethod
    def setUpClass(cls):
        cls.fe = import_fetch_espn()

    def build(self, weekly, meta, injury_report, limits=None):
        return self.fe.build_recent_form(
            weekly, meta, {}, [6, 7, 8],
            limits=limits or {"QB": 2, "RB": 60, "WR": 60, "TE": 24,
                              "K": 24, "D/ST": 20},
            injury_report=injury_report,
        )

    def test_out_for_season_is_excluded_and_the_limit_backfills(self):
        # Three QBs, the best of whom is out for the season, limit 2.
        weekly = {
            1: {6: row(40.0), 7: row(40.0), 8: row(40.0)},     # out for season
            2: {6: row(20.0), 7: row(20.0), 8: row(20.0)},
            3: {6: row(10.0), 7: row(10.0), 8: row(10.0)},
        }
        meta = {i: meta_entry("QB", name=f"QB{i}") for i in (1, 2, 3)}
        injuries = {
            "1": {"designation": "IR", "on_nfl_ir": True,
                  "expected_return": "2027-02-15",
                  "expected_return_label": "out for the season"}
        }
        out = self.build(weekly, meta, injuries)
        names = [e["name"] for e in out["rankings"]["QB"]]
        self.assertEqual(names, ["QB2", "QB3"])     # backfilled to the limit
        self.assertEqual(out["counts"]["QB"]["ranked"], 2)
        self.assertIs(out["counts"]["QB"]["sufficient"], True)
        self.assertEqual(out["diagnostics"]["excluded_out_for_season"], 1)
        self.assertEqual(
            [e["name"] for e in out["excluded_out_for_season"]], ["QB1"]
        )

    def test_in_season_return_is_still_ranked(self):
        weekly = {1: {6: row(40.0), 7: row(40.0), 8: row(40.0)}}
        injuries = {
            "1": {"designation": "IR", "on_nfl_ir": True,
                  "expected_return": "2026-12-06", "expected_return_label": "Dec 6"}
        }
        out = self.build(weekly, {1: meta_entry("QB", name="Back Soon")}, injuries)
        self.assertEqual([e["name"] for e in out["rankings"]["QB"]], ["Back Soon"])
        self.assertEqual(out["diagnostics"]["excluded_out_for_season"], 0)

    def test_excluded_player_is_not_in_qualifying_ids(self):
        weekly = {
            1: {6: row(40.0), 7: row(40.0), 8: row(40.0)},
            2: {6: row(20.0), 7: row(20.0), 8: row(20.0)},
        }
        meta = {1: meta_entry("QB", name="Gone"), 2: meta_entry("QB", name="Here")}
        injuries = {"1": {"expected_return_label": "out for the season"}}
        out = self.build(weekly, meta, injuries)
        self.assertEqual(out["qualifying_player_ids"]["QB"], [2])

    def test_is_out_for_season_helper(self):
        self.assertTrue(
            self.fe.is_out_for_season({"expected_return_label": "out for the season"})
        )
        self.assertFalse(self.fe.is_out_for_season({"expected_return_label": "Dec 6"}))
        self.assertFalse(self.fe.is_out_for_season({}))
        self.assertFalse(self.fe.is_out_for_season(None))


class FormLookupTest(unittest.TestCase):
    """The L3 RANK shown in Sections 4 and 6 comes from here."""

    @classmethod
    def setUpClass(cls):
        cls.fe = import_fetch_espn()

    def payload(self):
        return {
            "limits": {"WR": 60},
            "rankings": {"WR": [
                {"player_id": 1, "rank": 1, "avg_3_game_display": "25.00"},
            ]},
            "qualifying_player_ids": {"WR": [1, 2]},
            "excluded_out_for_season": [
                {"player_id": 3, "name": "Done", "position": "WR"}
            ],
        }

    def test_ranked_player_gets_rank_and_average(self):
        out = self.fe.recent_form_lookup(self.payload())
        self.assertEqual(out[1]["recent_form_rank"], 1)
        self.assertEqual(out[1]["recent_form_avg"], "25.00")
        self.assertEqual(out[1]["recent_form_position"], "WR")
        self.assertNotIn("recent_form_note", out[1])

    def test_qualifying_but_unranked_says_outside_the_limit(self):
        out = self.fe.recent_form_lookup(self.payload())
        self.assertEqual(out[2]["recent_form_note"], "outside the ranked top 60")
        self.assertNotIn("recent_form_rank", out[2])

    def test_season_ending_exclusion_has_its_own_reason(self):
        # Must not be reported as "fewer than three games": they may well have
        # three, and the real reason is the season-ending injury.
        out = self.fe.recent_form_lookup(self.payload())
        self.assertEqual(
            out[3]["recent_form_note"],
            "excluded from the rankings: out for the season",
        )

    def test_unknown_player_is_absent_so_callers_can_say_fewer_than_three(self):
        out = self.fe.recent_form_lookup(self.payload())
        self.assertNotIn(99, out)

    def test_empty_payload(self):
        self.assertEqual(self.fe.recent_form_lookup(None), {})
        self.assertEqual(self.fe.recent_form_lookup({}), {})


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
