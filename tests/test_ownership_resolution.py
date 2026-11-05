"""resolve_ownership as a pure function: no files, no network.

The headline cases are the two regressions the user asked for: a roster change
must move the highlighting with no code edit, and renaming the ESPN team must
not move it at all.
"""

from __future__ import annotations

import unittest

from tests._support import all_entries, import_fetch_espn, load_json


def rostered(team_id, team_name, **extra):
    record = {
        "fantasy_status": "ROSTERED",
        "fantasy_team": team_name,
        "fantasy_team_id": team_id,
    }
    record.update(extra)
    return record


def available(status):
    record = {"fantasy_status": "AVAILABLE"}
    if status is not None:
        record["availability_status"] = status
    return record


class ResolveOwnershipTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fetch_espn = import_fetch_espn()

    def resolve(self, *args, **kwargs):
        # Forwarding method, not a class attribute: assigning the bare function
        # to the class would bind it and pass self as the first argument.
        return self.fetch_espn.resolve_ownership(*args, **kwargs)

    def test_rostered_by_jason_x_is_flagged(self):
        index = {"111": rostered(1, "Jason X")}
        out = self.resolve(111, index)
        self.assertEqual(out["owner_label"], "Jason X")
        self.assertIs(out["is_jason_x"], True)
        self.assertEqual(out["owner_team_id"], 1)

    def test_rostered_by_another_team_is_not_flagged(self):
        index = {"222": rostered(6, "From Puka with Love")}
        out = self.resolve(222, index)
        self.assertEqual(out["owner_label"], "From Puka with Love")
        self.assertIs(out["is_jason_x"], False)

    def test_is_jason_x_follows_the_team_id_not_the_name(self):
        # Right id, wrong name -> flagged.
        out = self.resolve(1, {"1": rostered(1, "Someone Else")})
        self.assertIs(out["is_jason_x"], True)
        self.assertEqual(out["owner_label"], "Someone Else")
        # Wrong id, right name -> NOT flagged.
        out = self.resolve(2, {"2": rostered(7, "Jason X")})
        self.assertIs(out["is_jason_x"], False)

    def test_rename_resilience_keeps_the_same_ids_flagged(self):
        """Decision 2's headline test: a rename must not move a highlight."""
        artifact = load_json("recent_form_rankings.json")
        index = load_json("player_index.json")["players_by_id"]
        team_id = self.fetch_espn.JASON_X_TEAM_ID

        renamed = {}
        for pid, record in index.items():
            copy = dict(record)
            if copy.get("fantasy_team_id") == team_id:
                copy["fantasy_team"] = "Jason X Renamed"
            renamed[pid] = copy

        shipped = {e["player_id"] for e in all_entries(artifact) if e["is_jason_x"]}
        after = {
            e["player_id"] for e in all_entries(artifact)
            if self.resolve(e["player_id"], renamed)["is_jason_x"]
        }
        self.assertEqual(after, shipped)
        # And the displayed owner follows the new name.
        if shipped:
            one = sorted(shipped)[0]
            self.assertEqual(
                self.resolve(one, renamed)["owner_label"], "Jason X Renamed"
            )

    def test_highlight_set_follows_a_swapped_index(self):
        """Roster changes move the highlighting with no code change."""
        artifact = load_json("recent_form_rankings.json")
        index = load_json("player_index.json")["players_by_id"]
        team_id = self.fetch_espn.JASON_X_TEAM_ID
        entries = all_entries(artifact)

        shipped = {e["player_id"] for e in entries if e["is_jason_x"]}
        # Promote some currently-available ranked players onto team 1, and move
        # the real Jason X players to another team.
        promote = [
            e["player_id"] for e in entries
            if e["owner_label"] in ("FREE AGENT", "WAIVERS")
        ][:5]
        if not promote:
            self.skipTest("no available player is inside the ranking limits")

        swapped = {}
        for pid, record in index.items():
            copy = dict(record)
            if copy.get("fantasy_team_id") == team_id:
                copy["fantasy_team_id"] = 6
                copy["fantasy_team"] = "From Puka with Love"
            swapped[pid] = copy
        for pid in promote:
            swapped[str(pid)] = rostered(team_id, "Jason X")

        after = {
            e["player_id"] for e in entries
            if self.resolve(e["player_id"], swapped)["is_jason_x"]
        }
        self.assertEqual(after, set(promote))
        self.assertNotEqual(after, shipped)

    def test_free_agent_and_waivers_labels(self):
        out = self.resolve(1, {"1": available("FREEAGENT")})
        self.assertEqual(out["owner_label"], "FREE AGENT")
        self.assertIs(out["is_jason_x"], False)
        out = self.resolve(2, {"2": available("WAIVERS")})
        self.assertEqual(out["owner_label"], "WAIVERS")
        self.assertIs(out["is_jason_x"], False)

    def test_absent_id_is_unknown(self):
        out = self.resolve(999, {"1": rostered(1, "Jason X")})
        self.assertEqual(out["owner_label"], "UNKNOWN")
        self.assertIs(out["is_jason_x"], False)

    def test_unrecognised_status_is_unknown(self):
        out = self.resolve(1, {"1": available(None)})
        self.assertEqual(out["owner_label"], "UNKNOWN")
        self.assertIs(out["is_jason_x"], False)

    def test_rostered_without_a_team_name_still_follows_the_id(self):
        index = {"1": {"fantasy_status": "ROSTERED", "fantasy_team_id": 1}}
        out = self.resolve(1, index)
        self.assertEqual(out["owner_label"], "UNKNOWN")
        self.assertIs(out["is_jason_x"], True)

    def test_negative_dst_id_resolves(self):
        index = {"-16026": rostered(1, "Jason X")}
        out = self.resolve(-16026, index)
        self.assertIs(out["is_jason_x"], True)

    def test_bench_and_ir_slots_are_still_flagged(self):
        for slot in ("BENCH", "IR"):
            index = {"1": rostered(1, "Jason X", lineup_slot=slot)}
            self.assertIs(self.resolve(1, index)["is_jason_x"], True, slot)

    def test_name_is_never_used_for_matching(self):
        index = {
            "1": rostered(1, "Jason X", name="Josh Johnson"),
            "2": rostered(6, "From Puka with Love", name="Josh Johnson"),
        }
        self.assertIs(self.resolve(1, index)["is_jason_x"], True)
        self.assertIs(self.resolve(2, index)["is_jason_x"], False)

    def test_custom_team_id_is_honoured(self):
        index = {"1": rostered(1, "Jason X"), "7": rostered(7, "Connor's Team")}
        self.assertIs(self.resolve(7, index, jason_x_team_id=7)["is_jason_x"], True)
        self.assertIs(self.resolve(1, index, jason_x_team_id=7)["is_jason_x"], False)

    def test_custom_team_name_affects_only_the_crosscheck_flag(self):
        index = {"1": rostered(1, "Jason X")}
        out = self.resolve(1, index, jason_x_team_name="Someone Else")
        self.assertIs(out["is_jason_x"], True)
        self.assertIs(out["owner_name_is_jason_x"], False)
        out = self.resolve(1, index)
        self.assertIs(out["owner_name_is_jason_x"], True)

    def test_non_dict_record_is_unknown(self):
        out = self.resolve(1, {"1": "not a dict"})
        self.assertEqual(out["owner_label"], "UNKNOWN")
        self.assertIs(out["is_jason_x"], False)


if __name__ == "__main__":
    unittest.main()
