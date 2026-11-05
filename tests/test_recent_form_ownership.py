"""Ownership and highlighting, checked against the committed player_index.json.

Covers both directions: every flagged player really is Jason X's, and every
Jason X player in the tables really is flagged. The second direction is what
catches a missed highlight.
"""

from __future__ import annotations

import unittest

from tests._support import (
    all_entries,
    import_fetch_espn,
    load_artifact_or_fail,
    load_json,
)


class RecentFormOwnershipTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fetch_espn = import_fetch_espn()
        cls.artifact = load_artifact_or_fail(cls)
        cls.index = load_json("player_index.json")["players_by_id"]
        cls.entries = all_entries(cls.artifact)
        cls.jason_x_id = cls.fetch_espn.JASON_X_TEAM_ID

    def record_for(self, entry: dict) -> dict | None:
        return self.index.get(str(entry["player_id"]))

    def jason_x_ids_in_index(self) -> set[str]:
        return {
            pid for pid, rec in self.index.items()
            if isinstance(rec, dict)
            and rec.get("fantasy_status") == "ROSTERED"
            and rec.get("fantasy_team_id") == self.jason_x_id
        }

    def test_jason_x_entries_are_rostered_by_jason_x(self):
        for entry in self.entries:
            if not entry["is_jason_x"]:
                continue
            record = self.record_for(entry)
            self.assertIsNotNone(record, entry["name"])
            self.assertEqual(record["fantasy_status"], "ROSTERED", entry["name"])
            self.assertEqual(
                record.get("fantasy_team_id"), self.jason_x_id, entry["name"]
            )

    def test_jason_x_entries_carry_the_current_team_name(self):
        names = set()
        for entry in self.entries:
            if not entry["is_jason_x"]:
                continue
            record = self.record_for(entry)
            self.assertEqual(entry["owner_label"], record["fantasy_team"])
            names.add(entry["owner_label"])
        if names:
            self.assertEqual(len(names), 1, f"one team id, several names: {names}")
            self.assertEqual(
                names.pop(), self.artifact["ownership"]["jason_x_team_name_observed"]
            )

    def test_every_ranked_jason_x_player_is_flagged(self):
        ranked = {str(e["player_id"]): e for e in self.entries}
        should_be_flagged = self.jason_x_ids_in_index() & set(ranked)
        missed = [
            ranked[pid]["name"] for pid in should_be_flagged
            if not ranked[pid]["is_jason_x"]
        ]
        self.assertEqual(missed, [], f"Jason X players not highlighted: {missed}")

    def test_other_teams_are_never_flagged(self):
        for entry in self.entries:
            record = self.record_for(entry)
            if not isinstance(record, dict):
                continue
            if record.get("fantasy_status") != "ROSTERED":
                continue
            if record.get("fantasy_team_id") != self.jason_x_id:
                self.assertFalse(entry["is_jason_x"], entry["name"])

    def test_free_agents_and_waivers_are_never_flagged(self):
        for entry in self.entries:
            if entry["owner_label"] in ("FREE AGENT", "WAIVERS"):
                self.assertFalse(entry["is_jason_x"], entry["name"])
            record = self.record_for(entry)
            if isinstance(record, dict) and record.get("fantasy_status") == "AVAILABLE":
                self.assertFalse(entry["is_jason_x"], entry["name"])

    def test_unknown_owner_is_never_flagged(self):
        for entry in self.entries:
            if entry["owner_label"] == "UNKNOWN":
                self.assertFalse(entry["is_jason_x"], entry["name"])

    def test_owner_label_is_never_a_placeholder(self):
        banned = {"Owned", "OWNED", "Other team", "N/A", ""}
        for entry in self.entries:
            label = entry["owner_label"]
            self.assertIsInstance(label, str, entry["name"])
            self.assertNotIn(label, banned, entry["name"])
            self.assertNotEqual(label.strip(), "", entry["name"])

    def test_owner_label_equals_the_index_team_name(self):
        for entry in self.entries:
            record = self.record_for(entry)
            if not isinstance(record, dict):
                continue
            if record.get("fantasy_status") != "ROSTERED":
                continue
            self.assertEqual(entry["owner_label"], record["fantasy_team"])
            self.assertEqual(
                entry["owner_team_id"], record.get("fantasy_team_id")
            )

    def test_owner_label_matches_availability_for_available_players(self):
        labels = self.fetch_espn.AVAILABILITY_LABELS
        for entry in self.entries:
            record = self.record_for(entry)
            if not isinstance(record, dict):
                continue
            if record.get("fantasy_status") == "ROSTERED":
                continue
            expected = labels.get(record.get("availability_status"), "UNKNOWN")
            self.assertEqual(entry["owner_label"], expected, entry["name"])

    def test_jason_x_flag_is_not_filtered_by_slot(self):
        # The flagged set equals exactly the Jason X rostered ids present in the
        # rankings, so no slot-based exclusion (bench, IR, D/ST) can be hiding.
        flagged = {str(e["player_id"]) for e in self.entries if e["is_jason_x"]}
        ranked_ids = {str(e["player_id"]) for e in self.entries}
        self.assertEqual(flagged, self.jason_x_ids_in_index() & ranked_ids)

    def test_ownership_block_counts_are_accurate(self):
        own = self.artifact["ownership"]
        self.assertEqual(
            own["is_jason_x_count"], sum(1 for e in self.entries if e["is_jason_x"])
        )
        self.assertEqual(
            own["unknown"],
            sum(1 for e in self.entries if e["owner_label"] == "UNKNOWN"),
        )
        self.assertEqual(own["entries"], self.artifact["ranked_total"])
        self.assertEqual(own["resolved"] + own["unknown"], own["entries"])

        by_id = self.jason_x_ids_in_index()
        by_name = {
            pid for pid, rec in self.index.items()
            if isinstance(rec, dict)
            and rec.get("fantasy_status") == "ROSTERED"
            and rec.get("fantasy_team") == self.fetch_espn.JASON_X_TEAM_NAME
        }
        self.assertEqual(own["jason_x_by_id_count"], len(by_id))
        self.assertEqual(own["jason_x_by_name_count"], len(by_name))
        self.assertIs(own["jason_x_name_id_agree"], by_id == by_name)

    def test_highlighting_is_not_wholly_broken(self):
        # Fails rather than skips: "the index has Jason X players in the table
        # and none is flagged" is never a legitimate state.
        if self.artifact["ranked_total"] == 0:
            self.skipTest("nothing ranked")
        ranked_ids = {str(e["player_id"]) for e in self.entries}
        if not (self.jason_x_ids_in_index() & ranked_ids):
            self.skipTest("no Jason X player is inside the ranking limits")
        self.assertGreater(self.artifact["ownership"]["is_jason_x_count"], 0)


if __name__ == "__main__":
    unittest.main()
