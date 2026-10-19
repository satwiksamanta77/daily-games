"""Iteration-39 (Colorfle ONLY) regression tests.

Covers the four user requirements + the two root causes found in the
frame-by-frame analysis of the 9pm run 39286970529 (Oct-20) Colorfle video:

1. NO HINTS in Colorfle videos (no hints segment, no hints language in
   SEO/thumbnail/teaser, no "Free hints" chip on the teaser).
2. Colorfle solves in 2-4 attempts MAX, final guess = verified answer.
3. Frame-by-frame issues: white swatch (index 0) never registering
   (swatch map picked a non-palette white button), and the win check
   false-positive (hidden "0 Wins" Statistics DOM text).
4. REMOVE the "answer & deduction path" + "solve breakdown" segments
   from Colorfle ONLY (other games keep all six segments).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import solve_lib as S


class TestPlanColorfleMax4(unittest.TestCase):
    def test_oct20_answer_solves_in_4(self):
        seq, steps = S.plan_colorfle([0, 19, 3], max_guesses=4)
        self.assertLessEqual(len(seq), 4)
        self.assertGreaterEqual(len(seq), 2)
        self.assertEqual(seq[-1], [0, 19, 3])
        # Every step's pattern is the true feedback vs the secret, and
        # the pool never grows.
        prev = None
        for st in steps:
            self.assertEqual(
                "".join(S.mastermind_feedback(st["guess"], [0, 19, 3])),
                st["pattern"])
            if prev is not None:
                self.assertLessEqual(st["pool_before"], prev)
            prev = st["pool_after"]
        self.assertEqual(steps[-1]["pool_after"], 1)

    def test_other_secrets_stay_within_4(self):
        for secret in ([19, 0, 3], [5, 12, 7], [18, 2, 11]):
            seq, _ = S.plan_colorfle(secret, max_guesses=4)
            self.assertLessEqual(len(seq), 4, secret)
            self.assertGreaterEqual(len(seq), 2, secret)
            self.assertEqual(seq[-1], list(secret), secret)
            self.assertEqual(len({tuple(g) for g in seq}), len(seq),
                             f"duplicate guess for {secret}")

    def test_no_probe_after_pool_collapses(self):
        # Once a probe leaves exactly one candidate, the plan must play
        # the answer next — never burn another probe on a 1-candidate
        # pool. Property over several secrets (each plan is ~seconds).
        for secret in ([0, 19, 3], [19, 0, 3], [5, 12, 7], [18, 2, 11]):
            seq, steps = S.plan_colorfle(secret, max_guesses=4)
            for k, st in enumerate(steps[:-1]):
                if st["pool_after"] == 1:
                    # The very next step must be the final answer.
                    self.assertEqual(k + 1, len(steps) - 1, secret)
                    self.assertEqual(steps[k + 1]["guess"], list(secret),
                                     secret)


class _FakeButton:
    def __init__(self, label):
        self.label = label


class _FakePage:
    """Minimal Playwright page double for _colorfle_swatches /
    _colorfle_verify_win: evaluate() returns canned button/localStorage
    data, query_selector_all() returns handles in DOM order."""

    def __init__(self, buttons, win_state=None):
        # buttons: list of dicts {bg, vis, dc} in DOM order.
        self._buttons = buttons
        self._win_state = win_state or {}

    def evaluate(self, js):
        if "querySelectorAll('button')" in js:
            out = []
            for i, b in enumerate(self._buttons):
                out.append({"bg": b["bg"], "vis": b["vis"], "i": i,
                            "dc": b.get("dc")})
            return out
        if "localStorage.getItem('mode')" in js:
            return dict(self._win_state)
        raise AssertionError(f"unexpected evaluate JS: {js[:80]}")

    def query_selector_all(self, sel):
        assert sel == "button"
        return [_FakeButton(f"btn{i}") for i in range(len(self._buttons))]


class TestColorfleSwatches(unittest.TestCase):
    def _palette_buttons(self, decoy_white=True):
        # A non-palette WHITE button (no data-color) BEFORE the palette,
        # like a header/modal close button: the old first-match-wins
        # mapper picked it for index 0 and white never registered.
        btns = []
        if decoy_white:
            btns.append({"bg": "rgb(255, 255, 255)", "vis": True,
                         "dc": None})
        import runner as R
        for i, hx in enumerate(R.COLORFLE_HEX):
            r = int(hx[1:3], 16)
            g = int(hx[3:5], 16)
            b = int(hx[5:7], 16)
            btns.append({"bg": f"rgb({r}, {g}, {b})", "vis": True,
                         "dc": str(i)})
        return btns

    def test_white_maps_to_palette_button_not_decoy(self):
        import runner as R
        page = _FakePage(self._palette_buttons(decoy_white=True))
        sw = R._colorfle_swatches(page)
        self.assertEqual(len(sw), 20)
        # sw[0] must be the PALETTE white button (DOM index 1, after the
        # decoy), not the decoy (DOM index 0).
        self.assertEqual(sw[0].label, "btn1")
        # Every index maps to the button whose data-color claims it.
        for i in range(20):
            self.assertEqual(sw[i].label, f"btn{i + 1}")

    def test_no_decoy_still_maps_all(self):
        import runner as R
        page = _FakePage(self._palette_buttons(decoy_white=False))
        sw = R._colorfle_swatches(page)
        self.assertEqual(len(sw), 20)
        for i in range(20):
            self.assertEqual(sw[i].label, f"btn{i}")

    def test_hidden_buttons_ignored(self):
        import runner as R
        btns = self._palette_buttons(decoy_white=False)
        btns[0]["vis"] = False  # palette white button hidden
        page = _FakePage(btns)
        sw = R._colorfle_swatches(page)
        self.assertNotIn(0, sw)


class TestColorfleVerifyWin(unittest.TestCase):
    def _win_state(self, game_over, board, modal="none", win="0",
                    hard="0", mode=0):
        import json as _json
        return {"mode": mode, "gameOver": game_over,
                "board": _json.dumps(board), "modal": modal,
                "winStat": win, "hardWinStat": hard}

    def test_real_win_verified(self):
        import runner as R
        board = [[9, 11, 10], [0, 1, 2], [3, 4, 5], [0, 19, 3],
                 [-1, -1, -1], [-1, -1, -1]]
        page = _FakePage([], self._win_state("2", board, modal="block",
                                             win="1"))
        won, detail = R._colorfle_verify_win(page, [0, 19, 3])
        self.assertTrue(won, detail)

    def test_hidden_zero_wins_is_not_a_win(self):
        # REGRESSION (9pm run 39286970529): the old check matched the
        # always-present Statistics DOM ("0 Wins") and reported
        # solved=True while the answer was never submitted.
        import runner as R
        board = [[9, 11, 10], [1, 2, 3], [6, 3, 7], [8, 12, 13],
                 [19, 3, -1], [-1, -1, -1]]
        page = _FakePage([], self._win_state("0", board, modal="none",
                                             win="0"))
        won, detail = R._colorfle_verify_win(page, [0, 19, 3])
        self.assertFalse(won, detail)
        self.assertIn("gameOver=0", detail)

    def test_gameover_without_answer_row_is_not_a_win(self):
        import runner as R
        board = [[9, 11, 10], [1, 2, 3], [6, 3, 7], [8, 12, 13],
                 [19, 3, -1], [-1, -1, -1]]
        page = _FakePage([], self._win_state("2", board, modal="block",
                                             win="1"))
        won, _ = R._colorfle_verify_win(page, [0, 19, 3])
        self.assertFalse(won)

    def test_unsubmitted_answer_row_is_not_a_win(self):
        # The answer's colours placed but ENTER never registered:
        # gameOver stays 0, so this is NOT a win.
        import runner as R
        board = [[9, 11, 10], [0, 1, 2], [3, 4, 5], [0, 19, 3],
                 [-1, -1, -1], [-1, -1, -1]]
        page = _FakePage([], self._win_state("0", board, modal="none",
                                             win="0"))
        won, _ = R._colorfle_verify_win(page, [0, 19, 3])
        self.assertFalse(won)


class TestAssemblySegments(unittest.TestCase):
    def test_colorfle_drops_three_segments(self):
        import runner as R
        segs = R._assembly_segments("colorfle", "Colorfle", 4, 30.0,
                                    "recap.png", "hints.png", "reveal.png",
                                    "facts.png", "teaser.png")
        titles = [t for _, _, t in segs]
        self.assertEqual(titles, ["Today's Colorfle puzzle",
                                  "Full solve (4 guesses)",
                                  "Tomorrow's puzzle"])
        self.assertNotIn("hints.png", [p for p, _, _ in segs])
        self.assertNotIn("reveal.png", [p for p, _, _ in segs])
        self.assertNotIn("facts.png", [p for p, _, _ in segs])

    def test_other_games_keep_all_six_segments(self):
        import runner as R
        segs = R._assembly_segments("betweenle", "Betweenle", 4, 30.0,
                                    "recap.png", "hints.png", "reveal.png",
                                    "facts.png", "teaser.png")
        titles = [t for _, _, t in segs]
        self.assertEqual(titles, ["Today's Betweenle puzzle",
                                  "Hints before the solve",
                                  "Full solve (4 guesses)",
                                  "Answer & deduction path",
                                  "Solve breakdown",
                                  "Tomorrow's puzzle"])


class TestColorfleSeoNoHints(unittest.TestCase):
    def test_description_has_no_hints_and_no_letter_faq(self):
        import seo as SEO
        desc = SEO.build_description(
            "Colorfle", "colorfle-answer-today", "October 20, 2026",
            "Oct 20", answer="[0, 19, 3]", guesses=4,
            chapters=[(0, "Intro")], no_hints=True, skip_letter_faq=True)
        self.assertNotIn("hint", desc.lower())
        self.assertNotIn("What letter", desc)

    def test_description_default_still_mentions_hints(self):
        import seo as SEO
        desc = SEO.build_description(
            "Betweenle", "betweenle-answer-today", "October 20, 2026",
            "Oct 20", answer="STORK", guesses=4,
            chapters=[(0, "Intro")])
        self.assertIn("hints", desc)
        self.assertIn("What letter", desc)

    def test_tags_drop_hints_for_colorfle(self):
        import seo as SEO
        tags = SEO.build_tags("Colorfle", "Oct 20", no_hints=True)
        self.assertNotIn("Colorfle Hints", tags)
        self.assertNotIn("Colorfle Hint Today", tags)
        self.assertIn("colorfle answer today", tags)

    def test_tags_keep_hints_for_other_games(self):
        import seo as SEO
        tags = SEO.build_tags("Betweenle", "Oct 20")
        self.assertIn("Betweenle Hints", tags)

    def test_title_no_hints_fallback(self):
        import seo as SEO
        title = SEO.build_title("Colorfle", "Oct 20", guesses=None,
                                puzzle_number=None, seed=0, no_hints=True)
        self.assertNotIn("Hint", title)
        title2 = SEO.build_title("Colorfle", "Oct 20", guesses=None,
                                 puzzle_number=None, seed=0)
        self.assertIn("Hints", title2)


class TestColorfleTeaserAndThumb(unittest.TestCase):
    def test_teaser_hides_hints_chip(self):
        # The teaser renders without crashing with the chip hidden;
        # the chip list itself is asserted via the function's source
        # contract (the frame check in ANALYSIS39 proves the pixels).
        import tempfile
        import daily_parity as DP
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "teaser.png")
            DP.generate_teaser(out, "Colorfle", "October 20, 2026",
                               "colorfle-answer-today",
                               hide_hints_chip=True)
            self.assertTrue(os.path.exists(out))
            self.assertGreater(os.path.getsize(out), 0)
            out2 = os.path.join(td, "teaser2.png")
            DP.generate_teaser(out2, "Betweenle", "October 20, 2026",
                               "betweenle-answer-today")
            self.assertTrue(os.path.exists(out2))

    def test_thumb_no_hints_cta(self):
        import tempfile
        import runner as R
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "thumb.png")
            R.thumb(out, "Colorfle", "October 20, 2026", "[0, 19, 3]",
                    no_hints=True)
            self.assertTrue(os.path.exists(out))
            self.assertGreater(os.path.getsize(out), 0)


if __name__ == "__main__":
    unittest.main()
