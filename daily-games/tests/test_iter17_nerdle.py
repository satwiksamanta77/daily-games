"""Fix-loop iteration 17 tests — Nerdle + Semantle workstream.

Evidence-grounded in ANALYSIS13 (run 39067252873):

1. Nerdle header regex (P0): both matchers used `nerdle\\.?\\s*#` but the
   real DOM text is `nerdle . #1733` (SPACE before the dot), so the
   own-header capture was null on 8/9 modes. Fixed to
   `nerdle\\s*\\.?\\s*#` in the Python matcher
   (_nerdle_own_header_from_probe), the legacy _nums patterns, and the
   browser-side SERVED_JS line matcher. Own-header-only discipline
   kept (bare `nerdle. #n` counts only for classic).
2. Nerdle ² entry robustness (P0): the old ladder could not deliver ²
   at all — js-click "not found", keyboard.type drops ², keyboard.press
   raises Unknown key. New ladder: re-found JS click -> coordinate
   click on the button's bounding-box centre -> clipboard paste ->
   keyboard.insert_text, each verified via the read_state-based
   superscript counter. _grid_superscript_count now counts via
   read_state row text (the old leaf-cell probe returned 0 on a row
   frame-proven to contain ²). type_guess skips the Enter-only retry
   when the in-progress row lacks the needed superscript (entry
   failure, not submit failure); play_mode retries the ENTRY up to
   2 extra times instead of abandoning the mode while rows remain.
3. Nerdle read-back row logging (P1): equations_played records the
   row read back from the board, not the solver suggestion (midi
   logged 1-1+5=5 while the settled board showed 5+1-1=5).
4. Semantle unconditional decision log (P1): ROOT CAUSE — iter-16
   used bare print(), which goes to CI console stdout ONLY, while
   the artifact debug log is written ONLY by _dbg(). The iter-16
   test asserted on stdout, so it passed while the artifact stayed
   silent. Every decision line now goes through _dbg() (which also
   prints), and every previously-silent early return logs. Tests
   assert on the _dbg channel (a captured file handle), not stdout.
5. Nerdle 37s gap stage logging (P1): stage logs inside the
   type-guess path (solver_suggest done -> first keystroke window).
   Logging only — typing speed / pacing unchanged; no wait removed.

All tests are synthetic/local: no network, no browser, no CI run.
Fake page/keyboard objects stand in for Playwright. Imports resolve
against the repo root (the test file lives in tests/).
"""
import asyncio
import contextlib
import io
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import runner as R
import nerdle_solver as NS

SRC_RUNNER = (Path(__file__).parent.parent
              / "runner.py").read_text(encoding="utf-8")
SRC_NERDLE = (Path(__file__).parent.parent
              / "nerdle_solver.py").read_text(encoding="utf-8")


def _fn_src(src, start_marker, end_marker):
    start = src.index(start_marker)
    end = src.index(end_marker, start)
    return src[start:end]


# ---------------------------------------------------------------- fix 1


class TestHeaderRegexSpaceBeforeDot(unittest.TestCase):
    def _probe(self, text):
        return {"own_headers": [{"tag": "DIV", "cls": "", "text": text}]}

    def test_all_five_observed_formats(self):
        # (probe text, mode_id) -> expected number. These are the five
        # formats quoted in ANALYSIS13 §1 from the iter-16 artifact.
        cases = [
            ("nerdle . #1733", "classic", 1733),
            ("micro nerdle . #1506", "micro", 1506),
            ("maxi nerdle . #1368", "maxi", 1368),
            ("quad nerdle #1620", "quad", 1620),
            ("nerdle. #1733", "classic", 1733),
        ]
        for text, mode, want in cases:
            with self.subTest(text=text, mode=mode):
                n, ev = NS._nerdle_own_header_from_probe(
                    self._probe(text), mode)
                self.assertEqual(n, want)
                self.assertIsNotNone(ev)

    def test_own_header_only_discipline_kept(self):
        # Maxi's header on the classic page is shared chrome.
        n, _ = NS._nerdle_own_header_from_probe(
            self._probe("maxi nerdle . #1368"), "classic")
        self.assertIsNone(n)
        # Bare header on a variant page is NOT that variant's header.
        n, _ = NS._nerdle_own_header_from_probe(
            self._probe("nerdle . #1733"), "mini")
        self.assertIsNone(n)
        # Chrome wording before "nerdle" counts as no prefix (classic).
        n, ev = NS._nerdle_own_header_from_probe(
            self._probe("the nerdle . #1733"), "classic")
        self.assertEqual((n, ev["prefix"]), (1733, ""))
        # Mode prefix still recognised with the space-before-dot form.
        n, ev = NS._nerdle_own_header_from_probe(
            self._probe("midi nerdle . #1733"), "midi")
        self.assertEqual((n, ev["prefix"]), (1733, "midi"))

    def test_header_inside_longer_line(self):
        n, _ = NS._nerdle_own_header_from_probe(
            self._probe("header nav nerdle . #1733 Home"), "classic")
        self.assertEqual(n, 1733)

    def test_legacy_no_mode_path_space_before_dot(self):
        # _nerdle_header_number_from_probe without mode_id falls back
        # to body-text strategies; the space-before-dot form must match.
        probe = {"header_text": "", "hash_texts": [],
                 "data_attrs": [], "url": "",
                 "body_text": "welcome nerdle . #1733 play now"}
        n, strategy, _raw = NS._nerdle_header_number_from_probe(probe)
        self.assertEqual(n, 1733)

    def test_served_js_line_matcher_fixed(self):
        # SERVED_JS is the runtime JS string: the fixed pattern reads
        # `nerdle\s*\.?\s*#` once Python unescapes the source.
        self.assertIn(r"nerdle\s*\.?\s*#", NS.SERVED_JS)

    def test_python_matcher_source_fixed(self):
        src = _fn_src(SRC_NERDLE, "def _nerdle_own_header_from_probe",
                      "def _nerdle_header_number_from_probe")
        self.assertIn(r"nerdle\s*\.?\s*#", src)


# ---------------------------------------------------------------- fix 2


class _FakeKeyboard:
    def __init__(self):
        self.typed = []      # (text, delay) passed to type()
        self.pressed = []    # keys passed to press()
        self.inserted = []   # text passed to insert_text()

    async def type(self, text, delay=0):
        self.typed.append((text, delay))

    async def press(self, key):
        self.pressed.append(key)

    async def insert_text(self, text):
        self.inserted.append(text)


class _FakeMouse:
    def __init__(self):
        self.clicked = []

    async def click(self, x, y):
        self.clicked.append((x, y))


class _FakePage:
    """Playwright stand-in for _grid_superscript_count / type_equation.

    evaluate() dispatches on JS content:
    - READ_JS (contains getComputedStyle) -> current board grids;
    - CLICK_POWER_JS (contains b.click()) -> False (button not found);
    - POWER_BTN_BOX_JS (contains 'return {x:') -> button centre;
    - clipboard writeText -> marks ² as pasted;
    - DOM leaf probe (contains 'div.pb-grid *') -> fallback count.
    mouse.click() marks ² as entered (the coordinate click delivers it).
    """

    def __init__(self, dom_fallback=None):
        self.keyboard = _FakeKeyboard()
        self.mouse = _FakeMouse()
        self.entered = False
        self.dom_fallback = dom_fallback
        self.evaluate_calls = []

    async def wait_for_timeout(self, _ms):
        return None

    async def evaluate(self, js, *args):
        self.evaluate_calls.append(js)
        if "navigator.clipboard" in js:
            self.entered = True  # paste delivered ²
            return None
        if "b.click()" in js:
            return False  # method 1: power key "not found on board"
        if "return {x:" in js:
            return {"x": 100.0, "y": 200.0, "w": 40.0, "h": 24.0}
        if "getComputedStyle" in js:
            text = "1²+67+8=76" if self.entered else "1+67+8=76"
            return [{"rows": [[-1] * 10], "texts": [text]}]
        if "div.pb-grid *" in js:
            return self.dom_fallback
        return None

    def mark_entered_via_mouse(self):
        self.entered = True


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestSuperscriptCountViaReadState(unittest.TestCase):
    def test_counts_occurrences_in_row_text(self):
        page = _FakePage()
        page.entered = True  # row text holds 1²+67+8=76
        got = _run(NS._grid_superscript_count(page, "²", cols=10))
        self.assertEqual(got, 1)

    def test_zero_when_no_superscript_in_row(self):
        page = _FakePage()
        page.entered = False  # row text holds 1+67+8=76 (no ²)
        got = _run(NS._grid_superscript_count(page, "²", cols=10))
        self.assertEqual(got, 0)

    def test_dom_fallback_when_read_state_empty(self):
        class _EmptyReads(_FakePage):
            async def evaluate(self, js, *args):
                self.evaluate_calls.append(js)
                if "getComputedStyle" in js:
                    return []  # read_state yields nothing
                if "div.pb-grid *" in js:
                    return 3
                return None

        got = _run(NS._grid_superscript_count(_EmptyReads(), "²", cols=10))
        self.assertEqual(got, 3)

    def test_count_uses_read_state_not_leaf_probe(self):
        src = _fn_src(SRC_NERDLE, "async def _grid_superscript_count",
                      "async def type_equation")
        self.assertIn("read_state(page, cols", src)


class TestTypeEquationLadder(unittest.TestCase):
    def test_coordinate_click_delivers_superscript(self):
        # Method 1 (js-click) fails; method 2 (coordinate click) must
        # deliver ² and verify via read_state row text. The fake's
        # mouse.click marks entry; type_equation itself calls
        # page.mouse.click directly, so wrap it to flip the flag.
        page = _FakePage()
        orig_click = page.mouse.click

        async def _click(x, y):
            page.entered = True
            await orig_click(x, y)

        page.mouse.click = _click
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _run(NS.type_equation(page, "1²+67+8=76"))
        out = buf.getvalue()
        self.assertIn((100.0, 200.0), page.mouse.clicked)
        self.assertIn("verified in row text via coordinate click", out)
        self.assertNotIn("FAILED after all entry methods", out)

    def test_old_dead_paths_never_called_for_superscript(self):
        # keyboard.type(²) drops the char and keyboard.press(²) raises
        # Unknown key — both are proven dead ends and must not be
        # attempted for a superscript part. Plain parts still type.
        page = _FakePage()
        orig_click = page.mouse.click

        async def _click(x, y):
            page.entered = True
            await orig_click(x, y)

        page.mouse.click = _click
        with contextlib.redirect_stdout(io.StringIO()):
            _run(NS.type_equation(page, "1²+67+8=76"))
        self.assertNotIn("²", [t for t, _d in page.keyboard.typed])
        self.assertNotIn("²", page.keyboard.pressed)
        self.assertIn("1", [t for t, _d in page.keyboard.typed])
        self.assertIn("+67+8=76", [t for t, _d in page.keyboard.typed])

    def test_ladder_methods_present_in_source(self):
        src = _fn_src(SRC_NERDLE, "async def type_equation",
                      "async def type_guess")
        self.assertIn("POWER_BTN_BOX_JS", src)
        self.assertIn("mouse.click", src)
        self.assertIn("clipboard.writeText", src)
        self.assertIn("insert_text(part)", src)
        self.assertIn("_grid_superscript_count(page, part, cols=cols)", src)
        self.assertNotIn('keyboard.press(part)', src)
        # CLICK_POWER_JS no longer filters on offsetParent (fixed
        # keyboard bars have offsetParent null in Chrome).
        click_src = _fn_src(SRC_NERDLE, "CLICK_POWER_JS = ",
                            "POWER_BTN_BOX_JS = ")
        self.assertNotIn("offsetParent !== null", click_src)
        self.assertNotIn(".filter(vis)", click_src)
        self.assertIn("scrollIntoView", click_src)

    def test_type_guess_skips_enter_only_on_entry_failure(self):
        src = _fn_src(SRC_NERDLE, "async def type_guess",
                      "FETCH_JS = ")
        self.assertIn("_entry_missing_superscript", src)
        self.assertIn("skipping Enter-only retry", src)

    def test_play_mode_retries_entry_instead_of_abandoning(self):
        src = _fn_src(SRC_NERDLE, "async def play_mode",
                      "async def main")
        self.assertIn("entry retry", src)
        self.assertIn("rows remain", src)


class TestEntryMissingSuperscript(unittest.TestCase):
    def _state(self, texts, rows):
        return [{"rows": rows, "texts": texts}]

    def test_in_progress_row_lacks_superscript(self):
        st = self._state(["1+67+8=76"], [[-1] * 10])
        self.assertTrue(NS._entry_missing_superscript(st, "1²+67+8=76"))

    def test_in_progress_row_has_superscript(self):
        st = self._state(["1²+67+8=76"], [[-1] * 10])
        self.assertFalse(NS._entry_missing_superscript(st, "1²+67+8=76"))

    def test_no_superscript_needed(self):
        st = self._state(["1+67+8=76"], [[-1] * 10])
        self.assertFalse(NS._entry_missing_superscript(st, "1+67+8=76"))

    def test_no_in_progress_row(self):
        st = self._state(["1²+67+8=76"], [[2] * 10])
        self.assertFalse(NS._entry_missing_superscript(st, "1²+67+8=76"))


# ---------------------------------------------------------------- fix 3


class TestReadbackPlayed(unittest.TestCase):
    def test_midi_case_returns_board_row_not_suggestion(self):
        # Midi: suggestion 1-1+5=5, settled board row 5+1-1=5.
        state = [{"rows": [[2] * 5], "texts": ["5+1-1=5"]}]
        got = NS._readback_played(state, [0])
        self.assertEqual(got, "5+1-1=5")

    def test_none_when_no_new_evaluated_row(self):
        state = [{"rows": [[-1] * 5], "texts": ["1-1+5="]}]
        self.assertIsNone(NS._readback_played(state, [0]))

    def test_last_evaluated_text(self):
        state = [{"rows": [[2] * 5, [-1] * 5],
                  "texts": ["5+1-1=5", "1-1+5="]}]
        self.assertEqual(NS._last_evaluated_text(state), "5+1-1=5")

    def test_play_mode_records_readback(self):
        src = _fn_src(SRC_NERDLE, "async def play_mode",
                      "async def main")
        self.assertIn("_readback_played(state, prev)", src)
        self.assertIn("READ-BACK MISMATCH", src)
        self.assertIn("read-back=", src)
        # Suggestion stays in typed_guesses; played_here takes read-back.
        self.assertIn("played_here.append(_readback)", src)


# ---------------------------------------------------------------- fix 4


class _FakeAnswers:
    def __init__(self, target):
        self._target = target

    def target_date(self):
        return self._target

    def semantle(self, d):
        idx = (d - date(2022, 1, 30)).days
        return {"answer": f"word-{idx}", "puzzle": idx, "date": str(d)}


class TestSemantleDecisionViaDbg(unittest.TestCase):
    """The artifact channel is _dbg's file handle, NOT stdout.

    Each test seeds R._DBG_FH['semantle'] with a StringIO (the
    artifact stand-in) and asserts the decision line lands THERE —
    the exact check that failed on the iter-16 artifact.
    """

    def setUp(self):
        self._orig_a = R.A
        self._orig_live = dict(R._LIVE_DAY)
        self._orig_fh = R._DBG_FH.pop("semantle", None)
        R.A = _FakeAnswers(date(2026, 10, 18))
        R._LIVE_DAY.clear()
        self._sink = io.StringIO()
        R._DBG_FH["semantle"] = self._sink

    def tearDown(self):
        R.A = self._orig_a
        R._LIVE_DAY.clear()
        R._LIVE_DAY.update(self._orig_live)
        R._DBG_FH.pop("semantle", None)
        if self._orig_fh is not None:
            R._DBG_FH["semantle"] = self._orig_fh

    def test_no_signal_reaches_artifact_channel(self):
        ans = {"answer": "cry", "puzzle": 1722, "date": "2026-10-18"}
        with contextlib.redirect_stdout(io.StringIO()):
            out, served = R._align_answer_to_live_day("semantle", ans)
        self.assertIs(out, ans)
        self.assertIsNone(served)
        self.assertIn("no live-day signal", self._sink.getvalue())
        self.assertIn("keeping target", self._sink.getvalue())

    def test_served_equals_target_logs_decision(self):
        R._LIVE_DAY["hosts"] = {"semantle.example.com": "2026-10-18"}
        R._LIVE_DAY["urls"] = {}
        ans = {"answer": "cry", "puzzle": 1722, "date": "2026-10-18"}
        with contextlib.redirect_stdout(io.StringIO()):
            out, served = R._align_answer_to_live_day("semantle", ans)
        self.assertIs(out, ans)
        self.assertIsNone(served)
        self.assertIn("live-day decision: probe served=2026-10-18",
                      self._sink.getvalue())

    def test_far_jump_refusal_logs_via_dbg(self):
        R._LIVE_DAY["hosts"] = {"semantle.example.com": "2026-10-14"}
        R._LIVE_DAY["urls"] = {"semantle.example.com":
                               "https://semantle.example.com/x"}
        ans = {"answer": "cry", "puzzle": 1722, "date": "2026-10-18"}
        with contextlib.redirect_stdout(io.StringIO()):
            out, served = R._align_answer_to_live_day("semantle", ans)
        self.assertIs(out, ans)
        self.assertIsNone(served)
        self.assertIn("RE-KEY REFUSED", self._sink.getvalue())

    def test_align_uses_dbg_not_print(self):
        src = _fn_src(SRC_RUNNER, "def _align_answer_to_live_day",
                      "def _reset_site_state")
        self.assertIn("_dbg(gid", src)
        self.assertNotIn('print(f"[{gid}] live-day', src)
        self.assertNotIn('print(f"[{gid}] site served', src)


# ---------------------------------------------------------------- fix 5


class TestTypePathStageLogging(unittest.TestCase):
    def test_stage_logs_inside_type_path(self):
        tg = _fn_src(SRC_NERDLE, "async def type_guess", "FETCH_JS = ")
        self.assertIn("stage type_guess start", tg)
        self.assertIn("stage type_equation call done", tg)
        self.assertIn("stage wait_change attempt1 done", tg)
        self.assertIn("stage retry2 clear+retype start", tg)
        self.assertIn("stage type_guess done", tg)
        te = _fn_src(SRC_NERDLE, "async def type_equation",
                      "async def type_guess")
        self.assertIn("stage type_equation start", te)
        self.assertIn("stage superscript", te)
        self.assertIn("stage type_equation done", te)
        pm = _fn_src(SRC_NERDLE, "async def play_mode", "async def main")
        self.assertIn("type_guess start", pm)
        self.assertIn("type_guess done", pm)

    def test_no_pacing_change(self):
        # Typing speed / pacing unchanged: the plain-text path still
        # types with the caller's delay, and no wait was removed from
        # the type path (settles + wait_change still present).
        te = _fn_src(SRC_NERDLE, "async def type_equation",
                      "async def type_guess")
        self.assertIn("page.keyboard.type(eq, delay=delay)", te)
        self.assertIn("wait_for_timeout(150)", te)
        self.assertIn("wait_for_timeout(120)", te)
        tg = _fn_src(SRC_NERDLE, "async def type_guess", "FETCH_JS = ")
        self.assertIn("wait_change(page, cols, prev", tg)


if __name__ == "__main__":
    unittest.main()
