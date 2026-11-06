"""The prompt is a copy-paste artifact: a broken fence silently corrupts it."""

from __future__ import annotations

import unittest

from tests._support import import_fetch_espn, read_text

PROMPT = "docs/daily-report-prompt.md"
LEGEND = "Yellow = Jason X roster"
CELL_FORM = "W8 \u00b7 24.60"      # W8 <middle dot> 24.60
SECTION_4A = "4A. RECENT FORM RANKINGS"
GATE_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWX"


class PromptIntegrityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fetch_espn = import_fetch_espn()
        cls.text = read_text(PROMPT)
        cls.lines = cls.text.split("\n")
        cls.fences = [
            i for i, line in enumerate(cls.lines) if line.strip().startswith("````")
        ]
        start, end = cls.fences[0], cls.fences[-1]
        cls.block = "\n".join(cls.lines[start + 1:end])
        # Section 4A text: its heading through the start of Section 5.
        head = cls.block.find(SECTION_4A)
        tail = cls.block.find("5. TWO-WEEK")
        cls.section_4a = cls.block[head:tail] if head != -1 and tail != -1 else ""

    def test_exactly_two_four_backtick_fences(self):
        self.assertEqual(len(self.fences), 2, f"fences at {self.fences}")

    def test_no_stray_three_backtick_fences(self):
        stray = [
            i + 1 for i, line in enumerate(self.lines)
            if line.strip().startswith("```") and not line.strip().startswith("````")
        ]
        self.assertEqual(stray, [], f"three-backtick fences at lines {stray}")

    def test_prompt_block_is_non_empty_and_bounded(self):
        self.assertLess(self.fences[0], self.fences[1])
        self.assertGreater(len(self.block.split("\n")), 100)

    def test_section_headings_present(self):
        self.assertNotEqual(self.section_4a, "", "Section 4A slice came back empty")
        for heading in (
            "1. EXECUTIVE SUMMARY", "2. MY PLAYERS", "3. FRESH FREE-AGENT ACTIVITY",
            "4. POSITION WATCHLIST", SECTION_4A, "5. TWO-WEEK",
            "6. TOP 5 FREE-AGENT ROSTER VALUE UPGRADES", "7. BETTOR IN GREEN",
            "8. LEAGUE POSITION STRENGTH", "9. WHAT I WOULD DO TODAY",
            "10. SOURCES",
        ):
            self.assertIn(heading, self.block, heading)

    def test_six_limits_present_in_section_4a(self):
        for pos, limit in self.fetch_espn.RECENT_FORM_LIMITS.items():
            self.assertIn(f"{limit} {pos}", self.section_4a, f"{limit} {pos}")
        total = sum(self.fetch_espn.RECENT_FORM_LIMITS.values())
        self.assertIn(str(total), self.section_4a)

    def test_legend_line_present(self):
        self.assertIn(LEGEND, self.section_4a)

    def test_game_cell_format_is_specified_in_both_places(self):
        self.assertIn(CELL_FORM, self.section_4a)
        gate_t = self.block[self.block.find("\nT Section 4A"):]
        self.assertIn(CELL_FORM, gate_t, "gate T must pin the cell form too")

    def test_participation_rule_is_stated(self):
        # The did-not-play bug must stay fixed in the prompt's own wording.
        for phrase in ("ACTUALLY PLAYED", "did not play", "excluded entirely"):
            self.assertIn(phrase, self.section_4a, phrase)

    def test_all_gates_present(self):
        for letter in GATE_LETTERS:
            self.assertRegex(
                self.block, rf"(?m)^{letter} ", f"gate {letter} missing"
            )

    def test_new_data_files_are_referenced(self):
        for name in (
            "gates.json", "jason_x.json", "nfl_schedule.json",
            "top_available.json", "injuries.json", "recent_form_rankings.json",
            "player_index.json",
        ):
            self.assertIn(name, self.block, name)

    def test_derivations_are_replaced_by_lookups(self):
        # These are the instructions the pipeline now satisfies directly.
        self.assertIn("fetched_at_ct_label", self.block)
        self.assertIn("has_open_bench_spot", self.block)
        self.assertIn("data_status", self.block)
        self.assertIn("bye_pressure", self.block)
        self.assertNotIn("convert snapshot time to CT", self.block)

    def test_superseded_limits_absent(self):
        for stale in ("12 QB", "36 RB", "36 WR", "12 TE", "12 K", "12 D/ST",
                      "12/36/36/12/12/12"):
            self.assertNotIn(stale, self.text, f"superseded limit {stale!r} present")

    def test_column_order_is_specified(self):
        for column in ("GAME 1 (OLDEST)", "GAME 2", "GAME 3 (LATEST)",
                       "3-GAME AVG", "STATUS", "OWNER"):
            self.assertIn(column, self.section_4a, column)

    def test_ir_rules_are_present(self):
        for phrase in ("on_nfl_ir", "in_fantasy_ir_slot", "out for the season",
                       "IR-R"):
            self.assertIn(phrase, self.block, phrase)

    def test_section_4a_states_the_season_ending_exclusion(self):
        self.assertIn("excluded_out_for_season", self.section_4a)
        self.assertIn("backfill", self.section_4a)

    def test_l3_rank_replaces_the_cost_columns(self):
        section_4 = self.block[
            self.block.find("4. POSITION WATCHLIST"):self.block.find(SECTION_4A)
        ]
        section_6 = self.block[
            self.block.find("6. TOP 5 FREE-AGENT"):self.block.find("7. BETTOR IN GREEN")
        ]
        for name, text in (("Section 4", section_4), ("Section 6", section_6)):
            self.assertIn("L3 RANK", text, name)
            self.assertIn("recent_form_rank", text, name)
            self.assertIn("recent_form_note", text, name)
        # The old columns must be gone from the row/field specs.
        self.assertNotIn("| Bid |", section_4)
        self.assertNotIn("ACQUISITION COST", section_6)
        # But cost must still be stated somewhere in each, for gates P and Q.
        self.assertIn("FREE AGENT $0", section_4)
        self.assertIn("FREE AGENT $0", section_6)

    def test_section_5_slot_order_is_fixed(self):
        section_5 = self.block[
            self.block.find("5. TWO-WEEK"):self.block.find("6. TOP 5 FREE-AGENT")
        ]
        self.assertIn("QB, RB, RB, WR, WR, TE, FLEX, K, D/ST", section_5)
        self.assertIn("starting_slot_order", section_5)

    def test_section_8_uses_the_ten_point_scale(self):
        section_8 = self.block[
            self.block.find("8. LEAGUE POSITION STRENGTH"):
            self.block.find("9. WHAT I WOULD DO TODAY")
        ]
        self.assertIn("1-10 scale", section_8)
        self.assertIn("exactly one team scores 10", section_8)
        self.assertIn("exactly one team scores 1", section_8)
        # The superseded four-point vocabulary must be gone.
        for stale in ("1-Great", "2-Strong", "3-Average", "4-Weak"):
            self.assertNotIn(stale, self.block, f"superseded rating {stale!r}")

    def test_slot_order_matches_the_code_constant(self):
        expected = ", ".join(self.fetch_espn.STARTING_SLOT_ORDER)
        self.assertIn(expected, self.block)


if __name__ == "__main__":
    unittest.main()
