"""Injury flags: the badge travels, and IR means what it says."""

from __future__ import annotations

import json
import unittest

from tests._support import DATA_DIR, all_entries, import_fetch_espn, load_json


def have(name: str) -> bool:
    return (DATA_DIR / name).exists()


class InjuryHelperTest(unittest.TestCase):
    """Pure-function tests: no files, no network."""

    @classmethod
    def setUpClass(cls):
        cls.fe = import_fetch_espn()

    def test_athlete_id_recovered_from_links(self):
        athlete = {"links": [
            {"href": "https://www.espn.com/nfl/player/stats/_/id/4360761/michael-wilson"},
        ]}
        self.assertEqual(self.fe._athlete_id_from_links(athlete), "4360761")

    def test_athlete_id_absent_is_none(self):
        self.assertIsNone(self.fe._athlete_id_from_links({}))
        self.assertIsNone(self.fe._athlete_id_from_links({"links": [{"href": "/nfl/"}]}))

    def test_season_ending_return_date_is_labelled_not_dated(self):
        # ESPN encodes a season-ending injury as the Super Bowl date.
        self.assertEqual(
            self.fe._return_date_label("2027-02-15"), "out for the season"
        )

    def test_in_season_return_date_is_a_readable_date(self):
        self.assertEqual(self.fe._return_date_label("2026-12-06"), "Dec 6")
        self.assertEqual(self.fe._return_date_label("2026-11-05"), "Nov 5")

    def test_missing_or_junk_return_date(self):
        self.assertIsNone(self.fe._return_date_label(None))
        self.assertIsNone(self.fe._return_date_label(""))
        self.assertEqual(self.fe._return_date_label("not a date"), "not a date")

    def test_injury_badge_is_none_without_a_record(self):
        self.assertIsNone(self.fe.injury_badge(123, {}))
        self.assertIsNone(self.fe.injury_badge(123, {"456": {"designation": "IR"}}))

    def test_injury_badge_matches_by_string_id(self):
        report = {"-16026": {"designation": "IR", "on_nfl_ir": True}}
        badge = self.fe.injury_badge(-16026, report)
        self.assertIsNotNone(badge)
        self.assertIs(badge["on_nfl_ir"], True)

    def test_injury_badge_drops_empty_fields(self):
        report = {"1": {"designation": "OUT", "on_nfl_ir": False,
                        "expected_return": None, "injury_type": None}}
        badge = self.fe.injury_badge(1, report)
        self.assertNotIn("expected_return", badge)
        self.assertNotIn("injury_type", badge)
        self.assertEqual(badge["designation"], "OUT")


@unittest.skipUnless(have("injuries.json"), "data/injuries.json not committed")
class InjuryArtifactTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fe = import_fetch_espn()
        cls.injuries = load_json("injuries.json")
        cls.players = cls.injuries["players"]
        cls.index = load_json("player_index.json")["players_by_id"]
        cls.rosters = load_json("rosters.json")

    def test_every_injured_player_is_in_the_league_pool(self):
        for pid in self.players:
            self.assertIn(pid, self.index, pid)

    def test_counts_match_the_lists(self):
        counts = self.injuries["counts"]
        self.assertEqual(counts["in_league_pool"], len(self.players))
        self.assertEqual(
            counts["on_nfl_ir"],
            sum(1 for v in self.players.values() if v.get("on_nfl_ir")),
        )
        self.assertEqual(
            counts["jason_x_affected"],
            sum(1 for v in self.players.values() if v.get("is_jason_x")),
        )

    def test_is_jason_x_uses_the_team_id(self):
        for v in self.players.values():
            expected = v.get("fantasy_team_id") == self.fe.JASON_X_TEAM_ID
            self.assertIs(bool(v.get("is_jason_x")), expected, v.get("name"))

    def test_season_ending_dates_are_never_shown_as_a_date(self):
        for v in self.players.values():
            ret = v.get("expected_return") or ""
            if ret.startswith("2027-02"):
                self.assertEqual(
                    v.get("expected_return_label"), "out for the season", v.get("name")
                )

    def test_return_labels_agree_with_their_dates(self):
        for v in self.players.values():
            self.assertEqual(
                v.get("expected_return_label"),
                self.fe._return_date_label(v.get("expected_return")),
                v.get("name"),
            )

    def test_ir_cross_checks_are_accurate(self):
        # Players on NFL IR who are NOT in a fantasy IR slot.
        expected = {
            v["name"] for v in self.players.values()
            if v.get("on_nfl_ir") and v.get("fantasy_status") == "ROSTERED"
            and not v.get("in_fantasy_ir_slot")
        }
        reported = {r["name"] for r in self.injuries["ir_on_nfl_but_active_slot"]}
        self.assertEqual(reported, expected)

        # Fantasy IR slots holding someone without an NFL IR designation.
        slot_without = set()
        for team in self.rosters["teams"]:
            for p in team["injured_reserve"]:
                if not p.get("on_nfl_ir"):
                    slot_without.add(p["name"])
        self.assertEqual(
            {r["name"] for r in self.injuries["ir_slot_but_not_on_nfl_ir"]},
            slot_without,
        )

    def test_badge_is_inline_and_identical_everywhere(self):
        """The same injury must not differ between files."""
        rankings = load_json("recent_form_rankings.json")
        ranked = {str(e["player_id"]): e for e in all_entries(rankings)}

        roster_players = {}
        for team in self.rosters["teams"]:
            for bucket in ("starters", "bench", "injured_reserve"):
                for p in team[bucket]:
                    roster_players[str(p["player_id"])] = p

        compared = 0
        for pid, record in self.players.items():
            expected = self.fe.injury_badge(pid, {pid: record})
            for source, blob in (
                ("player_index", self.index.get(pid)),
                ("rosters", roster_players.get(pid)),
                ("recent_form", ranked.get(pid)),
            ):
                if not blob:
                    continue
                badge = blob.get("injury")
                if badge is None:
                    continue
                self.assertEqual(badge, expected, f"{source}: {record.get('name')}")
                compared += 1
        self.assertGreater(compared, 0, "no inline badge was found to compare")

    def test_roster_ir_flags_are_booleans_and_consistent(self):
        for team in self.rosters["teams"]:
            for bucket in ("starters", "bench", "injured_reserve"):
                for p in team[bucket]:
                    self.assertIsInstance(p["in_fantasy_ir_slot"], bool)
                    self.assertIsInstance(p["on_nfl_ir"], bool)
                    self.assertIs(
                        p["in_fantasy_ir_slot"],
                        p["lineup_slot"] == "IR",
                        p["name"],
                    )
                    if bucket == "injured_reserve":
                        self.assertTrue(p["in_fantasy_ir_slot"], p["name"])

    def test_metadata_reports_the_step(self):
        meta = load_json("metadata.json")
        if "injury_report_success" not in meta:
            self.skipTest("metadata.json predates the injury step")
        self.assertIsInstance(meta["injury_report_success"], bool)

    def test_artifact_round_trips(self):
        json.dumps(self.injuries)
        self.assertIsInstance(self.injuries["usage"], str)
        self.assertIn("on_nfl_ir", self.injuries["usage"])


if __name__ == "__main__":
    unittest.main()
