"""Fix-loop iteration 5 regression tests (countryle / framed / worldle).

Run:  python3 -m unittest tests.test_iter5_fixes -v
All tests are synthetic/local: no network, no browser, no CI run needed.
"""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).parent.parent
sys.path.insert(0, str(HERE))

import runner  # noqa: E402

SRC = (HERE / "runner.py").read_text(encoding="utf-8")


class TestTriviaBoolStatementMatcher(unittest.TestCase):
    """Worldle FACTS tab: 4 statements, one TRUE — the matcher maps each
    statement to its geogrid boolean key (CI run 39018667929 options)."""

    BF = {"bottom20PopulationDensity": True, "femaleLeader": True,
          "observesDST": False, "top20WorldHeritageSites": False,
          "top20PopulationDensity": False, "antarcticTreaty": False,
          "drivesLeft": False, "isMonarchy": False, "nato": True}

    def test_ci_options_map_to_keys(self):
        m = runner._s_worldle_bool_key_for_statement
        self.assertEqual(m("Bottom 20 in population density", self.BF),
                         "bottom20PopulationDensity")
        self.assertEqual(
            m("Has had a female head of state/government", self.BF),
            "femaleLeader")
        self.assertEqual(m("Observes Daylight Saving Time", self.BF),
                         "observesDST")
        self.assertEqual(
            m("Top 20 in number of World Heritage sites", self.BF),
            "top20WorldHeritageSites")

    def test_non_statements_map_to_none(self):
        m = runner._s_worldle_bool_key_for_statement
        for text in ("Remove Ads", "Give up", "Trivia", "1. FACTS", ""):
            self.assertIsNone(m(text, self.BF), text)

    def test_ambiguous_maps_to_none(self):
        # A statement matching two keys equally must never guess a key.
        m = runner._s_worldle_bool_key_for_statement
        self.assertIsNone(m("Top 20", {"top20PopulationDensity": True,
                                       "top20WorldHeritageSites": False}))


class TestIter5SourceGuards(unittest.TestCase):
    """Source-level guards for the iteration-5 root-cause fixes."""

    def test_countryle_no_title_mangling(self):
        self.assertNotIn("text-is('{box['t'].title()}')", SRC)

    def test_countryle_skip_fallback_and_no_break(self):
        self.assertIn("onboarding: SKIP advanced", SRC)
        self.assertIn("NEVER break to the PLAY-only wait", SRC)

    def test_countryle_play_loop_logs_missing_play(self):
        self.assertIn("PLAY loop: no PLAY/START button on this slide", SRC)

    def test_reboot_probe_reads_frames(self):
        self.assertIn("Take the max over the page + all frames", SRC)

    def test_framed_assembly_gated(self):
        self.assertIn("skipping final assembly: not solved", SRC)
        # No "None" string leaks into framed result.json / return value.
        self.assertNotIn('"video": str(final), "modes"', SRC)

    def test_framed_poster_unlock_polls(self):
        self.assertIn("up to ~45 s for the rewarded ad to complete", SRC)
        self.assertIn("_framed_open_board_with_unlock", SRC)

    def test_worldle_capital_no_first_svg(self):
        self.assertIn("which is a 24x24 MUI PersonRoundedIcon", SRC)
        self.assertIn("full-world equirectangular fallback", SRC)

    def test_worldle_gdp_buckets(self):
        self.assertIn("def _s_worldle_gdp_bucket", SRC)
        self.assertIn("bucket buttons, not a number input", SRC)

    def test_worldle_area_stepper(self):
        self.assertIn("def _s_worldle_play_area_stepper", SRC)

    def test_worldle_language_fallback(self):
        self.assertIn('"kbp": "Kabiye"', SRC)
        # Iter 6 replaced the "auto-submitted after first click"
        # heuristic (it conflated selection with submission): submission
        # is now the language-guesses array growing, with BOTH names +
        # Guess per question. See tests/test_iter6_fixes.py.
        self.assertIn("submitted={submitted}", SRC)
        # Per-question pick: Q1 must try the second language first.
        self.assertIn("names[qidx], names[1 - qidx]", SRC)


if __name__ == "__main__":
    unittest.main(verbosity=2)
