"""Central time, bye weeks, the Jason X block, trimmed pool and gates."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone

from tests._support import DATA_DIR, import_fetch_espn, load_json


def have(name: str) -> bool:
    return (DATA_DIR / name).exists()


class CentralTimeTest(unittest.TestCase):
    """The DST rule is implemented by hand, so it is pinned by tests.

    zoneinfo raises ZoneInfoNotFoundError on a stock Windows install, so the
    fallback path is what runs locally while the Linux runner uses zoneinfo.
    Both must agree, which is what these fixed expectations enforce.
    """

    @classmethod
    def setUpClass(cls):
        cls.fe = import_fetch_espn()

    def utc(self, *args):
        return datetime(*args, tzinfo=timezone.utc)

    def test_offset_is_cst_in_winter(self):
        self.assertEqual(self.fe._central_offset_hours(self.utc(2026, 1, 15, 12)), -6)
        self.assertEqual(self.fe._central_offset_hours(self.utc(2026, 12, 15, 12)), -6)

    def test_offset_is_cdt_in_summer(self):
        self.assertEqual(self.fe._central_offset_hours(self.utc(2026, 7, 4, 12)), -5)

    def test_dst_starts_second_sunday_of_march(self):
        # 2026: 2nd Sunday of March is the 8th; switch at 02:00 local.
        self.assertEqual(self.fe._central_offset_hours(self.utc(2026, 3, 8, 7)), -6)
        self.assertEqual(self.fe._central_offset_hours(self.utc(2026, 3, 8, 9)), -5)

    def test_dst_ends_first_sunday_of_november(self):
        # 2026: 1st Sunday of November is the 1st.
        self.assertEqual(self.fe._central_offset_hours(self.utc(2026, 10, 31, 12)), -5)
        self.assertEqual(self.fe._central_offset_hours(self.utc(2026, 11, 3, 12)), -6)

    def test_nth_weekday(self):
        self.assertEqual(self.fe._nth_weekday(2026, 3, 6, 2).day, 8)
        self.assertEqual(self.fe._nth_weekday(2026, 11, 6, 1).day, 1)

    def test_label_uses_a_timezone_abbreviation(self):
        label = self.fe.to_central_label(self.utc(2026, 11, 5, 23, 15))
        self.assertIn("CST", label)
        self.assertNotIn("UTC-", label)
        self.assertIn("Nov 5", label)

    def test_label_in_daylight_time(self):
        label = self.fe.to_central_label(self.utc(2026, 7, 4, 17, 0))
        self.assertIn("CDT", label)

    def test_conversion_shifts_the_clock(self):
        central = self.fe.to_central(self.utc(2026, 11, 6, 0, 15))
        self.assertEqual((central.month, central.day, central.hour), (11, 5, 18))

    def test_none_and_naive_inputs(self):
        self.assertIsNone(self.fe.to_central(None))
        self.assertIsNone(self.fe.to_central_iso(None))
        self.assertIsNone(self.fe.to_central_label(None))
        naive = datetime(2026, 11, 6, 0, 15)
        self.assertIsNotNone(self.fe.to_central(naive))

    def test_iso_to_central_label(self):
        self.assertIn("CST", self.fe.iso_to_central_label("2026-11-06T00:15:00+00:00"))
        self.assertIn("CST", self.fe.iso_to_central_label("2026-11-06T00:15:00Z"))
        self.assertIsNone(self.fe.iso_to_central_label("not a date"))
        self.assertIsNone(self.fe.iso_to_central_label(None))


class ScheduleHelperTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fe = import_fetch_espn()

    def test_bye_week_map_skips_invalid(self):
        schedule = {
            12: {"abbrev": "KC", "bye_week": 5},
            0: {"abbrev": "FA", "bye_week": 0},
            99: {"abbrev": "XX", "bye_week": None},
        }
        self.assertEqual(self.fe.bye_week_map(schedule), {12: 5})

    def test_upcoming_games_marks_byes(self):
        schedule = {
            12: {
                "abbrev": "KC",
                "bye_week": 5,
                "games_by_scoring_period": {
                    "4": {"opponent": "LV", "at_home": False},
                    "6": {"opponent": "LAC", "at_home": True},
                },
            }
        }
        out = self.fe.upcoming_games(schedule, 12, [4, 5, 6])
        self.assertEqual([g["scoring_period_id"] for g in out], [4, 5, 6])
        self.assertFalse(out[0]["bye"])
        self.assertTrue(out[1]["bye"])          # absent period == bye
        self.assertEqual(out[2]["opponent"], "LAC")

    def test_upcoming_games_unknown_team(self):
        self.assertEqual(self.fe.upcoming_games({}, 12, [1]), [])


class GatesHelperTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fe = import_fetch_espn()

    def base_metadata(self, **over):
        meta = {
            "league_fetch_success": True, "roster_fetch_success": True,
            "team_count_complete": True, "transactions_fetch_success": True,
            "player_pool_fetch_success": True, "matchup_fetch_success": True,
            "ownership_reconciled": True, "player_index_built": True,
            "recent_form_success": True, "injury_report_success": True,
            "team_count": 12, "expected_team_count": 12,
            "recent_form_ranked_total": 212, "errors": [],
        }
        meta.update(over)
        return meta

    def test_all_pass(self):
        gates = self.fe.build_gates(
            self.base_metadata(),
            {"schedule_ok": True, "schedule_teams": 33, "faab_ok": True,
             "faab_detail": "ok"},
        )
        self.assertEqual(gates["data_status"], "PASS")
        self.assertEqual(gates["failed_critical"], [])

    def test_critical_failure_fails_the_status(self):
        gates = self.fe.build_gates(
            self.base_metadata(roster_fetch_success=False),
            {"schedule_ok": True, "schedule_teams": 33, "faab_ok": True,
             "faab_detail": "ok"},
        )
        self.assertEqual(gates["data_status"], "FAIL")
        self.assertIn("all_rosters_retrieved", gates["failed_critical"])

    def test_supplementary_failure_does_not_fail_the_status(self):
        # The injury feed, the NFL schedule and recent form are all degradable.
        gates = self.fe.build_gates(
            self.base_metadata(injury_report_success=False, recent_form_success=False),
            {"schedule_ok": False, "schedule_teams": 0, "faab_ok": True,
             "faab_detail": "ok"},
        )
        self.assertEqual(gates["data_status"], "PASS")
        for gate in ("injury_report_retrieved", "nfl_schedule_retrieved",
                     "recent_form_built"):
            self.assertIn(gate, gates["failed_non_critical"])

    def test_faab_arithmetic_is_critical(self):
        gates = self.fe.build_gates(
            self.base_metadata(),
            {"schedule_ok": True, "schedule_teams": 33, "faab_ok": False,
             "faab_detail": "mismatched"},
        )
        self.assertEqual(gates["data_status"], "FAIL")
        self.assertIn("faab_arithmetic", gates["failed_critical"])


@unittest.skipUnless(have("gates.json"), "derived artifacts not committed")
class DerivedArtifactTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fe = import_fetch_espn()
        cls.gates = load_json("gates.json")
        cls.meta = load_json("metadata.json")
        cls.jason = load_json("jason_x.json")
        cls.schedule = load_json("nfl_schedule.json")
        cls.top = load_json("top_available.json")
        cls.rosters = load_json("rosters.json")

    def test_metadata_has_both_timestamps(self):
        self.assertIn("fetched_at_ct", self.meta)
        self.assertIn("fetched_at_ct_label", self.meta)
        self.assertEqual(
            self.meta["fetched_at_ct"],
            self.fe.to_central_iso(
                datetime.fromisoformat(self.meta["fetched_at"])
            ),
        )

    def test_gates_data_status_agrees_with_its_gates(self):
        failed = [g["gate"] for g in self.gates["gates"]
                  if g["critical"] and not g["passed"]]
        self.assertEqual(self.gates["failed_critical"], failed)
        self.assertEqual(
            self.gates["data_status"], "PASS" if not failed else "FAIL"
        )

    def test_gates_cover_every_critical_name(self):
        named = {g["gate"] for g in self.gates["gates"]}
        self.assertTrue(self.fe.CRITICAL_GATES <= named)

    def test_jason_x_block_matches_the_roster(self):
        team = next(
            t for t in self.rosters["teams"]
            if t["team_id"] == self.fe.JASON_X_TEAM_ID
        )
        self.assertEqual(self.jason["team_name"], team["team_name"])
        self.assertEqual(len(self.jason["starters"]), team["counts"]["starters"])
        self.assertEqual(len(self.jason["bench"]), team["counts"]["bench"])
        self.assertEqual(
            len(self.jason["injured_reserve"]), team["counts"]["injured_reserve"]
        )
        self.assertEqual(self.jason["capacity"], team["capacity"])
        self.assertIs(
            self.jason["has_open_bench_spot"], team["capacity"]["bench_open"] > 0
        )

    def test_jason_x_bye_pressure_is_derived_from_byes(self):
        for block in self.jason["bye_pressure"]:
            week = block["scoring_period_id"]
            for player in block["starters_on_bye"]:
                match = next(
                    p for p in self.jason["starters"] if p["name"] == player["name"]
                )
                self.assertEqual(match["bye_week"], week)

    def test_bye_weeks_agree_with_the_schedule_gaps(self):
        # A team's bye period must be absent from its game map.
        for team in self.schedule["teams"].values():
            bye = team.get("bye_week")
            if not isinstance(bye, int) or bye <= 0:
                continue
            self.assertNotIn(
                str(bye), team["games_by_scoring_period"], team.get("abbrev")
            )

    def test_byes_by_period_matches_the_abbrev_map(self):
        rebuilt: dict[str, list[str]] = {}
        for abbrev, week in self.schedule["bye_weeks_by_abbrev"].items():
            rebuilt.setdefault(str(week), []).append(abbrev)
        self.assertEqual(
            {k: sorted(v) for k, v in rebuilt.items()},
            {k: sorted(v) for k, v in self.schedule["byes_by_scoring_period"].items()},
        )

    def test_top_available_is_a_subset_of_the_pool(self):
        pool = {p["player_id"] for p in load_json("available_players.json")["players"]}
        for position, rows in self.top["players_by_position"].items():
            self.assertLessEqual(len(rows), self.top["per_position"], position)
            for row in rows:
                self.assertIn(row["player_id"], pool, row["name"])
                self.assertEqual(row["position"], position)

    def test_top_available_is_ranked_and_contiguous(self):
        for position, rows in self.top["players_by_position"].items():
            self.assertEqual(
                [r["rank"] for r in rows], list(range(1, len(rows) + 1)), position
            )
            owned = [r.get("percent_owned") or 0 for r in rows]
            self.assertEqual(owned, sorted(owned, reverse=True), position)

    def test_top_available_acquisition_matches_status(self):
        for rows in self.top["players_by_position"].values():
            for row in rows:
                expected = (
                    "FREE AGENT"
                    if row.get("availability_status") == "FREEAGENT"
                    else "WAIVERS"
                )
                self.assertEqual(row["acquisition"], expected, row["name"])

    def test_bye_week_is_inline_on_players(self):
        index = load_json("player_index.json")["players_by_id"]
        byes = self.schedule["bye_weeks_by_abbrev"]
        checked = 0
        for record in index.values():
            abbrev = record.get("pro_team")
            if abbrev in byes and record.get("bye_week") is not None:
                self.assertEqual(record["bye_week"], byes[abbrev], record.get("name"))
                checked += 1
        self.assertGreater(checked, 0)

    def test_artifacts_round_trip(self):
        for artifact in (self.gates, self.jason, self.schedule, self.top):
            json.dumps(artifact)
            self.assertIsInstance(artifact["usage"], str)


if __name__ == "__main__":
    unittest.main()
