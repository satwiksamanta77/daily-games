"""Iteration-49 (Colorfle ONLY) regression tests.

Two defects from the user report of 2026-10-22 (evidence: ANALYSIS49):

1. ONE MODE ONLY: Colorfle has TWO daily boards — Normal (3 colours)
   and Hard (4 colours), switched by the site's NORMAL/HARD toggle,
   each with its own seed and win state — and the answers site shows
   both answers. The old solver played only the default Normal board,
   so the video never showed the Hard answer.

2. LABEL/SEED OFFSET: the game's board is a pure function of the
   page's LOCAL clock (seed = local date, 17:00 local flip). wordsJi
   labels seed date S as S-1 (verified verbatim against their worker
   rows for 2026-10-20..23), so the site's "Oct-22" answers are the
   game's seed-Oct-23 answers. The old pipeline recorded seed D under
   date D, so the video on the site's D page showed the answers the
   site files under D-1 — the "video shows yesterday's answer"
   mismatch. The fix: record date D solves seed D+1 (engine date and
   page pin both shift), and the record carries both modes' answers.

Every test here fails on the pre-iter-49 code.
"""
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import solve_lib as S


class TestSeedConvention(unittest.TestCase):
    def test_seed_date_is_record_plus_one(self):
        import runner as R
        self.assertEqual(R._colorfle_seed_date(date(2026, 10, 22)),
                         date(2026, 10, 23))
        self.assertEqual(R._colorfle_seed_date(date(2026, 12, 31)),
                         date(2027, 1, 1))

    def test_colorfle_page_pin_is_next_day_midnight(self):
        # The game seeds from the page's LOCAL date (flip 17:00
        # local): pinning the record date itself serves seed == record
        # (the old off-by-one); the pin must be record + 1, 00:05.
        import runner as R
        os.environ.pop("FAKE_DATE_ISO", None)
        self.assertEqual(R._fake_iso_for("colorfle", date(2026, 10, 22)),
                         "2026-10-23T00:05:00")

    def test_other_games_pin_unchanged(self):
        import runner as R
        os.environ.pop("FAKE_DATE_ISO", None)
        self.assertEqual(R._fake_iso_for("betweenle", date(2026, 10, 22)),
                         "2026-10-22T00:05:00")


class TestAnswersCombine(unittest.TestCase):
    def _mode(self, colors, names, hexes, number=1641):
        return {"date": "2026-10-23", "mode": 0, "puzzleNumber": number,
                "colors": colors, "colorNames": names, "colorHexes": hexes}

    def test_combine_keeps_normal_shape_and_adds_hard(self):
        import answers as A
        normal = self._mode([19, 2, 6], ["Black", "Pink", "Yellow"],
                            ["#000000", "#FABEBE", "#FFE119"])
        hard = self._mode([14, 19, 2, 6],
                          ["Red", "Black", "Pink", "Yellow"],
                          ["#E6194B", "#000000", "#FABEBE", "#FFE119"])
        hard["mode"] = 1
        out = A._colorfle_combine(normal, hard)
        # Backward compatible top level (existing consumers).
        self.assertEqual(out["colors"], [19, 2, 6])
        self.assertEqual(out["colorHexes"],
                         ["#000000", "#FABEBE", "#FFE119"])
        self.assertEqual(out["puzzleNumber"], 1641)
        # Both modes ride along.
        self.assertEqual(out["colors_hard"], [14, 19, 2, 6])
        self.assertEqual(out["modes"]["normal"]["colors"], [19, 2, 6])
        self.assertEqual(out["modes"]["hard"]["colors"], [14, 19, 2, 6])


class _FakeEl:
    def __init__(self, page):
        self._page = page

    def bounding_box(self):
        return None  # force the evaluate-click path

    def evaluate(self, js):
        if "click" in js:
            self._page._mode = "1"
        return None


class _FakeSwitchPage:
    """Minimal page double for _colorfle_switch_to_hard: the toggle
    click flips localStorage 'mode' to '1' (or never, when stuck)."""

    def __init__(self, flips=True):
        self._mode = "0"
        self._flips = flips

    def query_selector(self, sel):
        assert "switch-button-case" in sel
        return _FakeEl(self) if self._flips else None

    def evaluate(self, js):
        if "switch-button-case" in js and "click" in js:
            if self._flips:
                self._mode = "1"
            return None
        if "localStorage.getItem('mode')" in js:
            return self._mode
        raise AssertionError(f"unexpected evaluate JS: {js[:80]}")

    def wait_for_timeout(self, ms):
        pass


class TestSwitchToHard(unittest.TestCase):
    def test_switch_verified_by_site_mode_flag(self):
        import runner as R
        page = _FakeSwitchPage(flips=True)
        self.assertTrue(R._colorfle_switch_to_hard(page))
        self.assertEqual(page._mode, "1")

    def test_switch_fail_closed_when_site_never_flips(self):
        import runner as R
        page = _FakeSwitchPage(flips=False)
        self.assertFalse(R._colorfle_switch_to_hard(page))


class _FakeWinPage:
    """Page double for _colorfle_verify_win: evaluate() returns the
    canned localStorage state dict (mirrors iter-39's fake)."""

    def __init__(self, state):
        self._state = state

    def evaluate(self, js):
        if "localStorage.getItem('mode')" in js:
            return dict(self._state)
        raise AssertionError(f"unexpected evaluate JS: {js[:80]}")


class TestVerifyHardMode(unittest.TestCase):
    """The win verifier keys off the site's mode flag: in mode 1 it
    reads gameOverHard/guessBoardHard."""

    def _page(self, state):
        return _FakeWinPage(state)

    def _state(self, game_over, board, mode):
        import json as _json
        return {"mode": mode, "gameOver": game_over,
                "board": _json.dumps(board), "modal": "block",
                "winStat": "1", "hardWinStat": "1"}

    def test_hard_win_verified_in_mode_1(self):
        import runner as R
        board = [[0, 11, 6, 12], [14, 19, 2, 6], [-1, -1, -1, -1]]
        page = self._page(self._state("2", board, mode=1))
        won, detail = R._colorfle_verify_win(page, [14, 19, 2, 6])
        self.assertTrue(won, detail)

    def test_normal_answer_not_a_hard_win(self):
        import runner as R
        board = [[0, 11, 6, 12], [14, 19, 2, 6], [-1, -1, -1, -1]]
        page = self._page(self._state("2", board, mode=1))
        won, _ = R._colorfle_verify_win(page, [19, 2, 6])
        self.assertFalse(won)


class TestPlanHardMode(unittest.TestCase):
    def test_seed_oct23_hard_solves_within_4(self):
        # The site's Oct-22 Hard answer (seed Oct-23): Red/Black/
        # Pink/Yellow. Planner must keep the standing rule for the
        # 4-colour board too: 2-4 attempts, answer last, true chain.
        seq, steps = S.plan_colorfle([14, 19, 2, 6], max_guesses=4)
        self.assertGreaterEqual(len(seq), 2)
        self.assertLessEqual(len(seq), 4)
        self.assertEqual(seq[-1], [14, 19, 2, 6])
        self.assertEqual(len({tuple(g) for g in seq}), len(seq))
        for st in steps:
            self.assertEqual(
                "".join(S.mastermind_feedback(st["guess"],
                                              [14, 19, 2, 6])),
                st["pattern"])
        self.assertEqual(steps[0]["pool_before"], 116280)
        self.assertEqual(steps[-1]["pool_after"], 1)


class TestStepsTwoModes(unittest.TestCase):
    def tearDown(self):
        import runner as R
        R._COLORS.pop("colorfle", None)
        R._COLORFLE_HARD.pop("colorfle", None)
        R._PLANS.pop("colorfle", None)

    def test_steps_cover_both_modes_in_play_order(self):
        import runner as R
        R._COLORS["colorfle"] = [19, 2, 6]
        R._COLORFLE_HARD["colorfle"] = [14, 19, 2, 6]
        R._PLANS.pop("colorfle", None)
        steps = R._steps("colorfle", "[19, 2, 6]")
        modes = [s.get("mode") for s in steps]
        self.assertIn("normal", modes)
        self.assertIn("hard", modes)
        # Normal steps all precede Hard steps; turns are continuous.
        self.assertEqual(modes, sorted(modes, key=lambda m: m != "normal"))
        self.assertEqual([s["turn"] for s in steps],
                         list(range(1, len(steps) + 1)))
        # First mode ends on the Normal answer, plan ends on Hard's.
        n_norm = modes.count("normal")
        self.assertEqual(steps[n_norm - 1]["guess"], [19, 2, 6])
        self.assertEqual(steps[-1]["guess"], [14, 19, 2, 6])
        self.assertLessEqual(n_norm, 4)
        self.assertLessEqual(modes.count("hard"), 4)


class TestSplitSeconds(unittest.TestCase):
    def test_split_maps_through_autocrop(self):
        import runner as R
        # 10s leading cut: raw 30.0 -> polished 20.0.
        self.assertEqual(
            R._colorfle_split_seconds(50.0, 30.0, [[10.0, 60.0]]), 20.0)

    def test_split_rejects_unusable_marks(self):
        import runner as R
        self.assertIsNone(R._colorfle_split_seconds(50.0, None, None))
        self.assertIsNone(R._colorfle_split_seconds(50.0, 0.2, None))
        self.assertIsNone(R._colorfle_split_seconds(50.0, 49.9, None))


class TestAnswersByModeShape(unittest.TestCase):
    def test_db_shape(self):
        import runner as R
        out = R._colorfle_answers_by_mode([
            {"mode": "normal", "answer": [19, 2, 6]},
            {"mode": "hard", "answer": [14, 19, 2, 6]}])
        self.assertEqual(out, {"normal": [19, 2, 6],
                               "hard": [14, 19, 2, 6]})

    def test_legacy_empty_is_none(self):
        import runner as R
        self.assertIsNone(R._colorfle_answers_by_mode([]))
        self.assertIsNone(R._colorfle_answers_by_mode(None))


if __name__ == "__main__":
    unittest.main()
