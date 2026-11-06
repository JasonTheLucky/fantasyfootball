"""FAAB plan, projections, matchup posture, snapshot delta and review day."""

from __future__ import annotations

import unittest

from tests._support import DATA_DIR, import_fetch_espn, load_json


def have(name: str) -> bool:
    return (DATA_DIR / name).exists()


class FaabPlanTest(unittest.TestCase):
    """Tiers must be costed against remaining, not the original budget."""

    @classmethod
    def setUpClass(cls):
        cls.fe = import_fetch_espn()

    def test_tiers_scale_with_remaining(self):
        plan = self.fe.faab_plan({"budget": 200, "spent": 149, "remaining": 51})
        self.assertEqual(plan["remaining"], 51)
        for tier in plan["tiers"]:
            self.assertLessEqual(tier["max_bid"], 51, tier["tier"])
            self.assertIs(tier["affordable"], True, tier["tier"])
            self.assertGreaterEqual(tier["suggested_bid"], 1)
            self.assertLessEqual(tier["suggested_bid"], tier["max_bid"])

    def test_the_old_original_budget_ladder_would_have_been_unaffordable(self):
        # Regression on the bug this fixes: 25-40% of the ORIGINAL 200 is
        # $50-80, which a $51 balance cannot cover.
        plan = self.fe.faab_plan({"budget": 200, "spent": 149, "remaining": 51})
        top = plan["tiers"][0]
        self.assertLess(top["max_bid"], 50)

    def test_discretionary_is_remaining_minus_reserve(self):
        plan = self.fe.faab_plan({"budget": 200, "spent": 0, "remaining": 200})
        self.assertEqual(plan["reserve_floor"], 40)
        self.assertEqual(plan["discretionary"], 160)

    def test_tiny_balance_still_yields_a_whole_dollar_bid(self):
        plan = self.fe.faab_plan({"budget": 200, "spent": 199, "remaining": 1})
        for tier in plan["tiers"]:
            self.assertGreaterEqual(tier["suggested_bid"], 1, tier["tier"])

    def test_zero_balance_marks_everything_unaffordable(self):
        plan = self.fe.faab_plan({"budget": 200, "spent": 200, "remaining": 0})
        self.assertEqual(plan["discretionary"], 0)
        self.assertTrue(all(not t["affordable"] for t in plan["tiers"]))

    def test_missing_faab_returns_none(self):
        self.assertIsNone(self.fe.faab_plan(None))
        self.assertIsNone(self.fe.faab_plan({}))


class MatchupPostureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fe = import_fetch_espn()

    def game(self, mine, theirs, team_id=1):
        return [{
            "home": {"team_id": team_id, "team_name": "Jason X",
                     "projected_points_live": mine, "points": 0,
                     "win_probability": 0.5},
            "away": {"team_id": 99, "team_name": "Them",
                     "projected_points_live": theirs, "points": 0},
        }]

    def test_postures_across_the_range(self):
        cases = [
            (150.0, 100.0, "heavy favourite"),
            (150.0, 142.0, "favourite"),
            (150.0, 149.0, "coin flip"),
            (140.0, 148.0, "underdog"),
            (100.0, 150.0, "heavy underdog"),
        ]
        for mine, theirs, expected in cases:
            out = self.fe.build_current_matchup(self.game(mine, theirs))
            self.assertEqual(out["posture"], expected, f"{mine} vs {theirs}")

    def test_favourite_is_told_to_protect_the_floor(self):
        out = self.fe.build_current_matchup(self.game(150.0, 100.0))
        self.assertIn("floor", out["guidance"].lower())

    def test_underdog_is_told_to_chase_ceiling(self):
        out = self.fe.build_current_matchup(self.game(100.0, 150.0))
        self.assertIn("ceiling", out["guidance"].lower())

    def test_margin_and_opponent_are_reported(self):
        out = self.fe.build_current_matchup(self.game(150.0, 130.0))
        self.assertEqual(out["opponent"], "Them")
        self.assertAlmostEqual(out["projected_margin"], 20.0)

    def test_missing_projections_are_unknown_not_guessed(self):
        games = [{
            "home": {"team_id": 1, "team_name": "Jason X"},
            "away": {"team_id": 99, "team_name": "Them"},
        }]
        out = self.fe.build_current_matchup(games)
        self.assertEqual(out["posture"], "unknown")
        self.assertIsNone(out["projected_margin"])

    def test_no_matchup_returns_none(self):
        self.assertIsNone(self.fe.build_current_matchup([]))
        self.assertIsNone(self.fe.build_current_matchup(None))


class ProjectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fe = import_fetch_espn()

    def test_picks_the_weekly_projection_not_the_season_or_the_actual(self):
        player = {"stats": [
            {"statSourceId": 0, "statSplitTypeId": 1, "scoringPeriodId": 9,
             "seasonId": 2026, "appliedTotal": 5.0},        # actual
            {"statSourceId": 1, "statSplitTypeId": 0, "scoringPeriodId": 0,
             "seasonId": 2026, "appliedTotal": 258.0},      # season projection
            {"statSourceId": 1, "statSplitTypeId": 1, "scoringPeriodId": 9,
             "seasonId": 2026, "appliedTotal": 27.71},      # the one we want
        ]}
        self.assertAlmostEqual(
            self.fe.extract_weekly_projection(player, 2026, 9), 27.71
        )

    def test_wrong_week_or_season_is_none(self):
        player = {"stats": [
            {"statSourceId": 1, "statSplitTypeId": 1, "scoringPeriodId": 9,
             "seasonId": 2025, "appliedTotal": 20.0},
        ]}
        self.assertIsNone(self.fe.extract_weekly_projection(player, 2026, 9))
        self.assertIsNone(self.fe.extract_weekly_projection(player, 2025, 8))

    def test_zero_is_a_real_projection_not_a_missing_one(self):
        # A bye-week player is projected 0.0, which must survive as 0.0 rather
        # than becoming None, because the report treats them differently.
        player = {"stats": [
            {"statSourceId": 1, "statSplitTypeId": 1, "scoringPeriodId": 9,
             "seasonId": 2026, "appliedTotal": 0.0},
        ]}
        self.assertEqual(self.fe.extract_weekly_projection(player, 2026, 9), 0.0)

    def test_junk_values_are_none(self):
        for bad in (None, "x", float("nan"), float("inf"), True):
            player = {"stats": [
                {"statSourceId": 1, "statSplitTypeId": 1, "scoringPeriodId": 9,
                 "seasonId": 2026, "appliedTotal": bad},
            ]}
            self.assertIsNone(
                self.fe.extract_weekly_projection(player, 2026, 9), repr(bad)
            )


class ChangesSinceLastTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fe = import_fetch_espn()

    def index(self, players):
        return {"players_by_id": players}

    def rec(self, pid, **over):
        base = {
            "player_id": pid, "name": f"P{pid}", "position": "WR",
            "pro_team": "KC", "fantasy_status": "ROSTERED",
            "fantasy_team": "Rival", "fantasy_team_id": 7,
        }
        base.update(over)
        return base

    def test_first_run_is_not_comparable(self):
        out = self.fe.build_changes_since_last(
            None, self.index({"1": self.rec(1)}), None
        )
        self.assertIs(out["comparable"], False)
        self.assertTrue(all(v == 0 for v in out["counts"].values()))

    def test_injury_designation_change_is_detected(self):
        before = self.index({"1": self.rec(1, injury={"designation": "QUESTIONABLE"})})
        after = self.index({"1": self.rec(1, injury={"designation": "OUT"})})
        out = self.fe.build_changes_since_last(before, after, {})
        self.assertEqual(out["counts"]["injury_changes"], 1)
        change = out["injury_changes"][0]
        self.assertEqual((change["from"], change["to"]), ("QUESTIONABLE", "OUT"))
        self.assertEqual(change["direction"], "changed")

    def test_new_and_cleared_designations_are_distinguished(self):
        before = self.index({"1": self.rec(1), "2": self.rec(2, injury={"designation": "OUT"})})
        after = self.index({"1": self.rec(1, injury={"designation": "OUT"}), "2": self.rec(2)})
        out = self.fe.build_changes_since_last(before, after, {})
        directions = {c["player_id"]: c["direction"] for c in out["injury_changes"]}
        self.assertEqual(directions[1], "new")
        self.assertEqual(directions[2], "cleared")

    def test_jason_x_players_sort_first_and_are_subsetted(self):
        before = self.index({
            "1": self.rec(1, fantasy_team="Jason X", fantasy_team_id=1),
            "2": self.rec(2),
        })
        after = self.index({
            "1": self.rec(1, fantasy_team="Jason X", fantasy_team_id=1,
                          injury={"designation": "OUT"}),
            "2": self.rec(2, injury={"designation": "OUT"}),
        })
        out = self.fe.build_changes_since_last(before, after, {})
        self.assertEqual(out["injury_changes"][0]["player_id"], 1)
        self.assertEqual(out["counts"]["jason_x_injury_changes"], 1)
        self.assertEqual(len(out["jason_x_injury_changes"]), 1)

    def test_dropped_player_becomes_available(self):
        before = self.index({"1": self.rec(1)})
        after = self.index({"1": self.rec(
            1, fantasy_status="AVAILABLE", availability_status="FREEAGENT",
            fantasy_team=None, fantasy_team_id=None)})
        out = self.fe.build_changes_since_last(before, after, {})
        self.assertEqual(out["counts"]["became_available"], 1)
        self.assertEqual(out["became_available"][0]["dropped_by"], "Rival")

    def test_claimed_player_is_flagged_so_a_stale_target_is_caught(self):
        before = self.index({"1": self.rec(
            1, fantasy_status="AVAILABLE", availability_status="FREEAGENT",
            fantasy_team=None, fantasy_team_id=None)})
        after = self.index({"1": self.rec(1)})
        out = self.fe.build_changes_since_last(before, after, {})
        self.assertEqual(out["counts"]["became_rostered"], 1)
        self.assertEqual(out["became_rostered"][0]["added_by"], "Rival")

    def test_trade_between_teams_is_detected(self):
        before = self.index({"1": self.rec(1, fantasy_team_id=7, fantasy_team="A")})
        after = self.index({"1": self.rec(1, fantasy_team_id=9, fantasy_team="B")})
        out = self.fe.build_changes_since_last(before, after, {})
        self.assertEqual(out["counts"]["changed_team"], 1)
        row = out["changed_team"][0]
        self.assertEqual((row["from_team"], row["to_team"]), ("A", "B"))

    def test_unchanged_snapshot_reports_nothing(self):
        same = self.index({"1": self.rec(1)})
        out = self.fe.build_changes_since_last(same, same, {})
        self.assertIs(out["comparable"], True)
        self.assertEqual(out["counts"]["injury_changes"], 0)
        self.assertEqual(out["counts"]["became_available"], 0)

    def test_previous_snapshot_time_is_carried(self):
        out = self.fe.build_changes_since_last(
            self.index({}), self.index({}),
            {"fetched_at": "T", "fetched_at_ct_label": "L", "scoringPeriodId": 9},
        )
        self.assertEqual(out["previous_snapshot_at"], "T")
        self.assertEqual(out["previous_snapshot_at_ct"], "L")
        self.assertEqual(out["previous_scoring_period_id"], 9)


@unittest.skipUnless(have("changes_since_last.json"), "artifacts not committed")
class EffectivenessArtifactTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fe = import_fetch_espn()
        cls.jason = load_json("jason_x.json")
        cls.top = load_json("top_available.json")
        cls.meta = load_json("metadata.json")
        cls.changes = load_json("changes_since_last.json")

    def test_faab_plan_is_published_and_affordable(self):
        plan = self.jason["faab_plan"]
        remaining = self.jason["faab"]["remaining"]
        self.assertEqual(plan["remaining"], remaining)
        for tier in plan["tiers"]:
            self.assertIs(tier["affordable"], tier["max_bid"] <= remaining)

    def test_current_matchup_is_published(self):
        matchup = self.jason.get("current_matchup")
        if not matchup:
            self.skipTest("no matchup for this period")
        self.assertIn(matchup["posture"], {
            "heavy favourite", "favourite", "coin flip", "underdog",
            "heavy underdog", "unknown",
        })
        self.assertTrue(matchup["guidance"])

    def test_projections_are_present_and_dated(self):
        self.assertEqual(
            self.top["projection_scoring_period_id"], self.meta["scoringPeriodId"]
        )
        self.assertEqual(
            self.jason["projection_scoring_period_id"], self.meta["scoringPeriodId"]
        )
        with_projection = [
            p for p in self.jason["starters"] if "projected_points" in p
        ]
        self.assertGreater(len(with_projection), 0)

    def test_review_day_flag_matches_the_weekday(self):
        expected = self.meta["weekday_ct"] == "Tuesday"
        self.assertIs(self.meta["full_league_review_day"], expected)
        self.assertEqual(
            self.fe.FULL_LEAGUE_REVIEW_WEEKDAY, 1, "Tuesday is weekday 1"
        )

    def test_changes_counts_match_their_lists(self):
        counts = self.changes["counts"]
        for key in ("injury_changes", "became_available", "became_rostered",
                    "changed_team"):
            self.assertEqual(counts[key], len(self.changes[key]), key)
        self.assertEqual(
            counts["jason_x_injury_changes"],
            len(self.changes["jason_x_injury_changes"]),
        )


if __name__ == "__main__":
    unittest.main()
