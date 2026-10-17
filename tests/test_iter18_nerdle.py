"""Fix-loop iteration 18 tests — Nerdle workstream (worker A).

Evidence-grounded in ANALYSIS15 (run 39072577360):

1. Header JOINED text (P0, §1): SERVED_JS tested `nerdle…#digits`
   PER-LINE of innerText, but the logo spans split `nerdle .` and
   `#1733` onto separate lines, so per-line can never match (the
   artifact carries the lone `#1733` line + the joined
   `nerdle . #1733` sample, diagnostics-only). SERVED_JS now tests
   the JOINED text (lines joined with a space) in addition to
   per-line; the Python own-header prefix discipline is unchanged.
2. ² entry (P0, §2): the iter-17 ladder double-inserted ² (frame
   row 1²²+66+8=5) because `_grid_superscript_count` counts U+00B2
   in row text while the DOM stores a plain 2 in a
   superscript-styled cell. Entry is now verified by TILE-COUNT
   delta and/or superscript-styled-cell detection; at most one
   entry method runs once a tile appears; the FULL row is verified
   against the intended equation BEFORE Enter, with clear + retype
   on mismatch. The legacy U+00B2 count is diagnostic-only.
3. 22s gap (P1, §5): attempts 1–2 failed with counts=[0] (board did
   not react) — attempt1 typed into an unfocused board with the
   cookie banner still visible; only retry2 (the first path that
   focuses) succeeded. type_guess now dismisses popups/consent and
   focuses BEFORE the first typing attempt (once per mode), consent
   clicks fall back to a JS click when an overlay intercepts
   pointer events, and the wait_change poll on a failed attempt is
   capped (3s attempts 1–2, 5s final retype; was 8s). Typing speed
   / pacing UNCHANGED.
4. Midi typed-vs-board divergence (P1, ANALYSIS15 root-cause §5):
   log typed `1-1+5=5`, board row `5+1-1=5`. The mechanism is NOT
   provable from code — recorded UNRESOLVED in the worker report
   (no mechanism invented). What IS new: the pre-Enter row check
   logs the board row BEFORE Enter, so the next artifact
   attributes a divergence to ENTRY (row already wrong before
   Enter) vs SUBMIT (row right before Enter, different at
   read-back). Tested here by simulating a board that delivers the
   permuted row.

All tests are synthetic/local: no network, no browser, no CI run.
Fake page/board objects stand in for Playwright. Imports resolve
against the repo root (the test file lives in tests/).
"""
import asyncio
import contextlib
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import nerdle_solver as NS

SRC_NERDLE = (Path(__file__).parent.parent
              / "nerdle_solver.py").read_text(encoding="utf-8")


def _fn_src(src, start_marker, end_marker):
    start = src.index(start_marker)
    end = src.index(end_marker, start)
    return src[start:end]


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------- fix 1


class TestHeaderJoinedText(unittest.TestCase):
    def _probe(self, text):
        return {"own_headers": [{"tag": "DIV", "cls": "", "text": text}]}

    @staticmethod
    def _js_join(split_span_text):
        # Mirrors the SERVED_JS join: element text with lines joined
        # by a single space (the fix-1 change).
        return " ".join(split_span_text.split("\n"))

    def test_joined_split_span_matches(self):
        # The logo spans split `nerdle .` and `#1733` onto separate
        # innerText lines; joined with a space they must match.
        cases = [
            ("nerdle .\n#1733", "classic", 1733),
            ("maxi nerdle .\n#1368", "maxi", 1368),
            ("micro nerdle .\n#1506", "micro", 1506),
            ("Home\nmaxi nerdle .\n#1368\nPlay", "maxi", 1368),
        ]
        for text, mode, want in cases:
            with self.subTest(text=text, mode=mode):
                joined = self._js_join(text)
                n, ev = NS._nerdle_own_header_from_probe(
                    self._probe(joined), mode)
                self.assertEqual(n, want)
                self.assertIsNotNone(ev)

    def test_joined_own_header_discipline_kept(self):
        # Bare joined header on a variant page is NOT its header.
        n, _ = NS._nerdle_own_header_from_probe(
            self._probe(self._js_join("nerdle .\n#1733")), "mini")
        self.assertIsNone(n)
        # Another mode's joined header on the classic page is chrome.
        n, _ = NS._nerdle_own_header_from_probe(
            self._probe(self._js_join("maxi nerdle .\n#1368")), "classic")
        self.assertIsNone(n)

    def test_served_js_tests_joined_text(self):
        # SERVED_JS is the runtime JS string: it must compute the
        # joined text (lines joined with a space) and test it, in
        # addition to the per-line filter.
        self.assertIn("join(' ')", NS.SERVED_JS)
        self.assertIn("joinedHit", NS.SERVED_JS)
        self.assertIn("(lines.length || joinedHit)", NS.SERVED_JS)
        self.assertIn("joinedHit ? joined : lines.join", NS.SERVED_JS)


# ---------------------------------------------------------------- fix 2


class _FakeLadderKeyboard:
    def __init__(self):
        self.typed = []
        self.pressed = []
        self.inserted = []

    async def type(self, text, delay=0):
        self.typed.append((text, delay))

    async def press(self, key):
        self.pressed.append(key)

    async def insert_text(self, text):
        self.inserted.append(text)


class _FakeLadderMouse:
    def __init__(self):
        self.clicked = []

    async def click(self, x, y):
        self.clicked.append((x, y))


class _FakeLadderPage:
    """Playwright stand-in for the superscript ladder.

    Simulates the REAL DOM shape (ANALYSIS15 §2): the exponent is a
    PLAIN 2 in a superscript-styled cell — row text after entry is
    "12+67+8=76" (no U+00B2), tiles 9->10, sup_cells 0->1. The
    legacy U+00B2 count therefore stays 0 across a successful entry;
    only the tile/sup-cell probe can verify it.
    """

    def __init__(self, js_click_ok=True):
        self.keyboard = _FakeLadderKeyboard()
        self.mouse = _FakeLadderMouse()
        self.entered = False
        self.js_click_ok = js_click_ok
        self.clipboard_calls = 0

    async def wait_for_timeout(self, _ms):
        return None

    async def evaluate(self, js, *args):
        if "sup_cells" in js:
            # SUPERSCRIPT_STATE_JS.
            text = "12+67+8=76" if self.entered else "1+67+8=76"
            tiles = 10 if self.entered else 9
            sup = 1 if self.entered else 0
            return {"tiles": tiles, "sup_cells": sup,
                    "grids": [[{"text": text, "tiles": tiles,
                                "sup_cells": sup}]]}
        if "navigator.clipboard" in js:
            self.clipboard_calls += 1
            self.entered = True
            return None
        if "b.click()" in js:
            if self.js_click_ok:
                self.entered = True
                return True
            return False
        if "return {x:" in js:
            return {"x": 100.0, "y": 200.0, "w": 40.0, "h": 24.0}
        if "getComputedStyle" in js:
            text = "12+67+8=76" if self.entered else "1+67+8=76"
            return [{"rows": [[-1] * 10], "texts": [text]}]
        if "div.pb-grid *" in js:
            return 0
        return None


class TestSuperscriptEntryVerifier(unittest.TestCase):
    def test_tile_delta_verifies_plain2_entry(self):
        # The real DOM case: plain 2 in a sup-styled cell. Tiles
        # 9->10, sup 0->1, U+00B2 count 0->0 — verified by delta.
        before = {"tiles": 9, "sup_cells": 0, "grids": []}
        after = {"tiles": 10, "sup_cells": 1, "grids": []}
        self.assertTrue(NS._superscript_entry_verified(before, after))

    def test_tile_delta_alone_verifies(self):
        # A tile appeared but sup detection missed it: still entry.
        before = {"tiles": 9, "sup_cells": 0, "grids": []}
        after = {"tiles": 10, "sup_cells": 0, "grids": []}
        self.assertTrue(NS._superscript_entry_verified(before, after))

    def test_no_delta_not_verified(self):
        before = {"tiles": 9, "sup_cells": 0, "grids": []}
        after = {"tiles": 9, "sup_cells": 0, "grids": []}
        self.assertFalse(NS._superscript_entry_verified(before, after))

    def test_missing_snapshot_not_verified(self):
        self.assertFalse(NS._superscript_entry_verified(
            None, {"tiles": 10, "sup_cells": 1, "grids": []}))
        self.assertFalse(NS._superscript_entry_verified(
            {"tiles": 9, "sup_cells": 0, "grids": []}, None))

    def test_current_row_sup_cells_aligned(self):
        # read_state: one in-progress row; probe grids aligned 1:1.
        state = [{"rows": [[-1] * 10], "texts": ["12+67+8=76"]}]
        probe = {"tiles": 10, "sup_cells": 1,
                 "grids": [[{"text": "12+67+8=76", "tiles": 10,
                             "sup_cells": 1}]]}
        self.assertEqual(NS._current_row_sup_cells(state, probe), 1)

    def test_current_row_sup_cells_none_when_probe_missing(self):
        state = [{"rows": [[-1] * 10], "texts": ["12+67+8=76"]}]
        self.assertIsNone(NS._current_row_sup_cells(state, None))

    def test_current_row_sup_cells_none_when_no_in_progress(self):
        state = [{"rows": [[2] * 10], "texts": ["12+67+8=76"]}]
        probe = {"tiles": 10, "sup_cells": 1,
                 "grids": [[{"text": "12+67+8=76", "tiles": 10,
                             "sup_cells": 1}]]}
        self.assertIsNone(NS._current_row_sup_cells(state, probe))


class TestRowMatchesEquation(unittest.TestCase):
    def test_exact_match(self):
        self.assertTrue(NS._row_matches_equation(
            "1²+66+8=75", "1²+66+8=75"))

    def test_plain2_dom_form_matches(self):
        # DOM stores the exponent as plain 2: row "12+66+8=75" IS
        # the intended "1²+66+8=75".
        self.assertTrue(NS._row_matches_equation(
            "12+66+8=75", "1²+66+8=75"))
        self.assertTrue(NS._row_matches_equation(
            "12+34=46", "1²+34=46"))
        self.assertTrue(NS._row_matches_equation(
            "13+34=46", "1³+34=46"))

    def test_double_superscript_rejected(self):
        # The iter-17 failure row matches NEITHER form.
        self.assertFalse(NS._row_matches_equation(
            "1²²+66+8=75", "1²+66+8=75"))
        self.assertFalse(NS._row_matches_equation(
            "12²+66+8=75", "1²+66+8=75"))

    def test_wrong_digits_rejected(self):
        self.assertFalse(NS._row_matches_equation(
            "12+66+8=75", "1²+67+8=76"))
        self.assertFalse(NS._row_matches_equation(
            "12+66+8=7", "1²+66+8=75"))  # lost keystroke

    def test_whitespace_ignored_and_none(self):
        self.assertTrue(NS._row_matches_equation(
            "1² + 66 + 8 = 75", "1²+66+8=75"))
        self.assertFalse(NS._row_matches_equation(None, "1²+66+8=75"))
        self.assertFalse(NS._row_matches_equation("12+66+8=75", None))


class TestInProgressRowText(unittest.TestCase):
    def test_picks_first_in_progress_row(self):
        state = [{"rows": [[2] * 5, [-1] * 5],
                  "texts": ["5+1-1=5", "1-1+5="]}]
        self.assertEqual(NS._in_progress_row_text(state), "1-1+5=")

    def test_none_when_all_evaluated_or_empty(self):
        state = [{"rows": [[2] * 5], "texts": ["5+1-1=5"]}]
        self.assertIsNone(NS._in_progress_row_text(state))
        state = [{"rows": [[-1] * 5], "texts": [""]}]
        self.assertIsNone(NS._in_progress_row_text(state))


class TestTypeEquationSingleEntry(unittest.TestCase):
    def test_js_click_delivery_stops_ladder(self):
        # js-click delivers the tile (plain-2 DOM: tiles 9->10,
        # sup 0->1, U+00B2 count 0->0). NO further method may run —
        # the old ladder ran the coordinate click next and
        # double-inserted ².
        page = _FakeLadderPage(js_click_ok=True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _run(NS.type_equation(page, "1²+67+8=76"))
        out = buf.getvalue()
        self.assertEqual(page.mouse.clicked, [])
        self.assertEqual(page.clipboard_calls, 0)
        self.assertEqual(page.keyboard.inserted, [])
        self.assertIn("verified in row text via js-click", out)
        self.assertNotIn("FAILED after all entry methods", out)

    def test_fallthrough_when_js_click_fails(self):
        # js-click reports not-found (no tile); the coordinate click
        # delivers and verifies. Clipboard/insert_text never run.
        page = _FakeLadderPage(js_click_ok=False)
        orig_click = page.mouse.click

        async def _click(x, y):
            page.entered = True
            await orig_click(x, y)

        page.mouse.click = _click
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _run(NS.type_equation(page, "1²+67+8=76"))
        out = buf.getvalue()
        self.assertEqual(page.mouse.clicked, [(100.0, 200.0)])
        self.assertEqual(page.clipboard_calls, 0)
        self.assertEqual(page.keyboard.inserted, [])
        self.assertIn("verified in row text via coordinate click", out)
        self.assertNotIn("FAILED after all entry methods", out)

    def test_old_dead_paths_never_called(self):
        page = _FakeLadderPage(js_click_ok=True)
        with contextlib.redirect_stdout(io.StringIO()):
            _run(NS.type_equation(page, "1²+67+8=76"))
        self.assertNotIn("²", [t for t, _d in page.keyboard.typed])
        self.assertNotIn("²", page.keyboard.pressed)

    def test_ladder_wired_to_new_verifier(self):
        src = _fn_src(SRC_NERDLE, "async def type_equation",
                      "async def _verify_row_or_retype")
        self.assertIn("_grid_superscript_state(page, cols)", src)
        self.assertIn("_superscript_entry_verified(", src)
        # Legacy U+00B2 count still taken, diagnostic-only.
        self.assertIn("_grid_superscript_count(page, part, cols=cols)", src)
        self.assertIn("diagnostic-only", src)
        self.assertIn("single-entry", src)
        self.assertIn("insert_text(part)", src)
        self.assertNotIn("keyboard.press(part)", src)

    def test_performed_but_unplaced_click_falls_through(self):
        # Both-probes-blind rule: the state probe is unavailable
        # (non-dict) but the legacy count CAN still see the row, so
        # a coordinate click that was performed yet placed no tile
        # (legacy count unchanged) must fall through to clipboard
        # paste — it must NOT trigger the single-entry early stop,
        # which fires only when BOTH probes are blind.
        page = _FakeLegacyOnlyPage()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _run(NS.type_equation(page, "1²+67+8=76"))
        out = buf.getvalue()
        self.assertEqual(page.mouse.clicked, [(100.0, 200.0)])
        self.assertTrue(page.entered)
        self.assertIn("verified in row text via clipboard paste", out)
        self.assertNotIn("BOTH probes blind", out)


class _FakeLegacyOnlyPage:
    """Ladder fake where the NEW state probe is unavailable (returns
    a non-dict) but the LEGACY U+00B2 count still tracks delivery —
    the shape of the iter-15 fake. The coordinate click is performed
    but places nothing; only the clipboard paste delivers.
    """

    def __init__(self):
        self.keyboard = _FakeLadderKeyboard()
        self.mouse = _FakeLadderMouse()
        self.entered = False

    async def wait_for_timeout(self, _ms):
        return None

    async def evaluate(self, js, *args):
        if "sup_cells" in js:
            return 0  # non-dict -> state probe unavailable
        if "navigator.clipboard" in js:
            self.entered = True
            return None
        if "b.click()" in js:
            return False  # js-click fails
        if "return {x:" in js:
            return {"x": 100.0, "y": 200.0, "w": 40.0, "h": 24.0}
        if "getComputedStyle" in js:
            text = "1²+67+8=76" if self.entered else "1+67+8=76"
            return [{"rows": [[-1] * 10], "texts": [text]}]
        if "div.pb-grid *" in js:
            return 1 if self.entered else 0
        return None


# ------------------------------------------------------- fix 2 + fix 4


class _FakeBoardKeyboard:
    def __init__(self, board):
        self._board = board
        self.typed = []
        self.pressed = []
        self.inserted = []

    async def type(self, text, delay=0):
        self.typed.append((text, delay))
        self._board._type_text(text)

    async def press(self, key):
        self.pressed.append(key)
        self._board._press_key(key)

    async def insert_text(self, text):
        self.inserted.append(text)
        self._board._type_text(text)


class _FakeBoardMouse:
    def __init__(self):
        self.clicked = []

    async def click(self, x, y):
        self.clicked.append((x, y))


class _FakeBoardLocator:
    async def bounding_box(self, timeout=None):
        return None


class _FakeBoard:
    """Minimal board simulator for type_guess + pre-Enter verification.

    One grid, one in-progress row. keyboard.type appends to the row
    (with configurable delivery faults); Enter evaluates the row iff
    it is exactly `cols` long; Backspace pops a char. read_state
    (READ_JS) returns the row with [-1]*cols while in progress and
    [2]*cols once evaluated.
    """

    def __init__(self, cols, *, drop_first_type=False, permute_type=False):
        self.cols = cols
        self.keyboard = _FakeBoardKeyboard(self)
        self.mouse = _FakeBoardMouse()
        self.frames = []
        self._row = ""
        self._done = False
        self._drop_first_type = drop_first_type
        self._permute_type = permute_type
        self._typed_n = 0

    def _type_text(self, text):
        self._typed_n += 1
        if self._done:
            return
        if self._permute_type and text == "1-1+5=5":
            # Simulate the observed midi divergence: the board row
            # comes up permuted vs the typed string.
            self._row += "5+1-1=5"
            return
        if self._drop_first_type and self._typed_n == 1:
            text = text[:-1]  # one keystroke lost
        self._row += text

    def _press_key(self, key):
        if key == "Enter":
            if not self._done and len(self._row) == self.cols:
                self._done = True
        elif key == "Backspace":
            if not self._done:
                self._row = self._row[:-1]

    def _state(self):
        fb = [2] * self.cols if self._done else [-1] * self.cols
        return [{"rows": [fb], "texts": [self._row]}]

    async def wait_for_timeout(self, _ms):
        return None

    def locator(self, _sel):
        class _L:
            first = _FakeBoardLocator()

        return _L()

    async def query_selector(self, _sel):
        return None

    async def evaluate(self, js, *args):
        if "sup_cells" in js:
            return {"tiles": len(self._row), "sup_cells": 0,
                    "grids": [[{"text": self._row,
                                "tiles": len(self._row), "sup_cells": 0}]]}
        if "getComputedStyle" in js:
            return self._state()
        return False


class TestTypeGuessFullRowVerification(unittest.TestCase):
    def setUp(self):
        NS._TYPE_PRIMED = False

    def test_matching_row_submitted_without_retype(self):
        board = _FakeBoard(7)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            state, counts, ok = _run(
                NS.type_guess(board, "1-1+5=5", 7, [0]))
        self.assertTrue(ok)
        self.assertEqual(counts, [1])
        self.assertEqual([t for t, _d in board.keyboard.typed],
                         ["1-1+5=5"])
        self.assertIn("matches '1-1+5=5'", buf.getvalue())

    def test_mismatch_clears_and_retypes_whole_row(self):
        # First typing loses a keystroke (row "12+34=4"); the
        # pre-Enter check must catch it, clear + retype, and only
        # then submit — never Enter a short row.
        board = _FakeBoard(8, drop_first_type=True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            state, counts, ok = _run(
                NS.type_guess(board, "12+34=46", 8, [0]))
        out = buf.getvalue()
        self.assertTrue(ok)
        self.assertEqual(counts, [1])
        self.assertEqual([t for t, _d in board.keyboard.typed],
                         ["12+34=46", "12+34=46"])
        self.assertIn("pre-Enter row MISMATCH", out)
        self.assertIn("matches '12+34=46'", out)

    def test_permuted_row_logged_as_entry_divergence(self):
        # Midi case (ANALYSIS15 §5, UNRESOLVED mechanism): the board
        # row comes up "5+1-1=5" for typed "1-1+5=5", on BOTH the
        # first typing and the retype. The artifact must show the
        # row was ALREADY wrong before Enter (ENTRY divergence),
        # and the board row is still submitted as-is (read-back
        # records it) — never an infinite retype loop.
        board = _FakeBoard(7, permute_type=True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            state, counts, ok = _run(
                NS.type_guess(board, "1-1+5=5", 7, [0]))
        out = buf.getvalue()
        self.assertTrue(ok)
        self.assertEqual(counts, [1])
        # Exactly one retype, then submit as-is.
        self.assertEqual([t for t, _d in board.keyboard.typed],
                         ["1-1+5=5", "1-1+5=5"])
        self.assertIn("pre-Enter row MISMATCH", out)
        self.assertIn("STILL mismatched", out)
        self.assertIn("5+1-1=5", out)


# ---------------------------------------------------------------- fix 3


class TestTypeGuessPrimingAndFailFast(unittest.TestCase):
    def setUp(self):
        NS._TYPE_PRIMED = False

    def test_priming_runs_once_per_mode(self):
        # First type_guess on a fresh page primes (focus click);
        # the second call on another page with the flag already set
        # skips priming (play_mode resets the flag per mode).
        b1 = _FakeBoard(7)
        with contextlib.redirect_stdout(io.StringIO()):
            _run(NS.type_guess(b1, "1-1+5=5", 7, [0]))
        self.assertTrue(NS._TYPE_PRIMED)
        self.assertIn((640, 360), b1.mouse.clicked)
        b2 = _FakeBoard(7)
        with contextlib.redirect_stdout(io.StringIO()):
            _run(NS.type_guess(b2, "1-1+5=5", 7, [0]))
        # No focus click from priming on the second call.
        self.assertEqual(b2.mouse.clicked, [])

    def test_focus_before_first_type_in_source(self):
        tg = _fn_src(SRC_NERDLE, "async def type_guess", "FETCH_JS = ")
        self.assertIn("focus_grid(page)", tg)
        self.assertIn("sweep_popups(page", tg)
        self.assertIn("accept_cookies(page", tg)
        self.assertIn("dismiss_modals(page", tg)
        self.assertLess(tg.index("focus_grid(page)"),
                        tg.index("type_equation(page, eq)"))

    def test_no_8s_poll_in_type_path(self):
        tg = _fn_src(SRC_NERDLE, "async def type_guess", "FETCH_JS = ")
        self.assertNotIn("timeout=8", tg)
        self.assertIn("timeout=3", tg)
        self.assertIn("timeout=5", tg)
        wc = _fn_src(SRC_NERDLE, "async def wait_change",
                     "async def focus_grid")
        self.assertIn("timeout=3.0", wc)

    def test_play_mode_resets_priming(self):
        pm = _fn_src(SRC_NERDLE, "async def play_mode", "async def main")
        self.assertIn("_TYPE_PRIMED = False", pm)

    def test_consent_js_click_fallback(self):
        ac = _fn_src(SRC_NERDLE, "async def accept_cookies",
                     "async def dismiss_modals")
        self.assertIn('el.evaluate("(e) => e.click()")', ac)
        dm = _fn_src(SRC_NERDLE, "async def dismiss_modals",
                     "SPLASH_JS = ")
        self.assertIn('el.evaluate("(e) => e.click()")', dm)

    def test_typing_speed_unchanged(self):
        te = _fn_src(SRC_NERDLE, "async def type_equation",
                     "async def _verify_row_or_retype")
        self.assertIn("async def type_equation(page, eq, delay=200)", te)
        self.assertIn("page.keyboard.type(eq, delay=delay)", te)
        self.assertIn("page.keyboard.type(part, delay=delay)", te)


if __name__ == "__main__":
    unittest.main()
