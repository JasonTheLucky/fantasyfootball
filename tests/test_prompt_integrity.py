"""The prompt is a copy-paste artifact: a broken fence silently corrupts it."""

from __future__ import annotations

import unittest

from tests._support import import_fetch_espn, read_text

PROMPT = "docs/daily-report-prompt.md"
LEGEND = "Yellow highlighted rows = Players currently on Jason X"
CELL_FORM = "W8 \u00b7 24.60"      # W8 <middle dot> 24.60


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
        # Section 10 text: from its heading to the SOURCES heading.
        head = cls.block.find("10. RECENT FORM RANKINGS")
        tail = cls.block.find("11. SOURCES")
        cls.section_ten = cls.block[head:tail] if head != -1 and tail != -1 else ""

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

    def test_section_ten_and_eleven_headings_present(self):
        self.assertIn("10. RECENT FORM RANKINGS \u2014 LAST 3 GAMES", self.block)
        self.assertIn("11. SOURCES", self.block)
        self.assertNotIn("\n10. SOURCES", self.block)
        self.assertNotEqual(self.section_ten, "", "Section 10 slice came back empty")

    def test_six_limits_present(self):
        for pos, limit in self.fetch_espn.RECENT_FORM_LIMITS.items():
            self.assertIn(f"{limit} {pos}", self.section_ten, f"{limit} {pos}")
        total = sum(self.fetch_espn.RECENT_FORM_LIMITS.values())
        self.assertIn(str(total), self.section_ten)

    def test_legend_line_present(self):
        self.assertIn(LEGEND, self.section_ten)

    def test_game_cell_format_is_specified(self):
        self.assertIn(CELL_FORM, self.section_ten)
        gate_t = self.block[self.block.find("\nT Section 10"):]
        self.assertIn(CELL_FORM, gate_t, "gate T must pin the cell form too")

    def test_new_gates_present(self):
        for letter in "ABCDEFGHIJKLMNOPQRSTU":
            self.assertRegex(
                self.block, rf"(?m)^{letter} ", f"gate {letter} missing"
            )

    def test_cross_references_updated(self):
        self.assertIn("Sections 1-11", self.block)
        self.assertNotIn("Sections 1-10", self.block)
        self.assertIn("4/5/full7/8/10/Sources = deep read", self.block)
        self.assertIn("recent_form_rankings.json", self.block)

    def test_superseded_limits_absent(self):
        for stale in ("12 QB", "36 RB", "36 WR", "12 TE", "12 K", "12 D/ST",
                      "12/36/36/12/12/12"):
            self.assertNotIn(stale, self.text, f"superseded limit {stale!r} present")

    def test_column_order_is_specified(self):
        for column in ("GAME 1 (OLDEST)", "GAME 2", "GAME 3 (LATEST)",
                       "3-GAME AVG", "OWNER"):
            self.assertIn(column, self.section_ten, column)


if __name__ == "__main__":
    unittest.main()
