"""Fix-loop iteration 21 tests — Nerdle workstream.

Evidence-grounded in ANALYSIS18 (run 39081533416):

1. ² second entry (P0, §2d/§6.2/§7.1): guess 4 (official
   '1²+67+8=76') failed ALL methods while frame gp_0106 showed
   the X² button visibly rendered. Root cause: the power-key JS
   took `Array.from(...).find(isPowerBtn)` = the FIRST DOM-order
   match, and returned false when THAT one had a zero rect, never
   trying the other matches. Now the JS iterates ALL matches and
   clicks/boxes the first VISIBLE + ENABLED one, and a failure
   logs EVERY candidate (tag/rect/text/disabled/usable).
2. Speed verdict-model (P0, §2c/§6.3/§7.2): the video showed the
   game granting 'You won in 0m 7s' while the bot marked speed
   unwon. Causes: (a) the 2-board model demanded
   `len(state) >= 2` grids while the site runs a single-grid flow
   (2 fully-green rows: pre-filled submitted starter + played
   answer); (b) the flush only handled a PENDING starter row, not
   one that arrived SUBMITTED; (c) the win popup was never a
   verdict signal. Now: speed load dumps FULL storage + grid
   states; `_check_win_verified` verifies speed's single-grid
   flow (green rows >= boards + coverage + every grid solved,
   or the game's win popup + every grid solved + coverage);
   the pre-filled SUBMITTED starter counts toward the played
   solutions via the seeded history; and a win popup's Next
   button is tried (advance) before the win is claimed — the
   sweep never X-dismisses the popup, and this code never
   clicks X/close/share/play-again either.
3. ² retry-ladder cap (P2, §7.8): 4 x ~27s = ~108s re-running an
   identical, deterministically failing ladder. Now: after the
   FIRST full-method failure the equation is recorded and every
   later attempt (pre-Enter retype, type_guess retry2, outer
   site-solution / entry retries) fails fast instead of
   re-entering the ladder.

All tests are synthetic/local: no network, no CI run. The
power-key JS tests execute the SHIPPED JS strings in Node with
a mocked DOM (not a re-implementation of the matcher).
"""
import asyncio
import contextlib
import io
import itertools
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import nerdle_solver as NS

SRC_NERDLE = (Path(__file__).parent.parent
              / "nerdle_solver.py").read_text(encoding="utf-8")


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _fn_src(src, start_marker, end_marker):
    start = src.index(start_marker)
    end = src.index(end_marker, start)
    return src[start:end]


# ---------------------------------------------------------------- fix 1
# Power-key picker iterates ALL matches (real JS in Node).

NODE_HARNESS = r"""
// Harness: mock DOM buttons, run the shipped picker JS.
const mkBtn = (o) => ({
  tagName: 'BUTTON',
  textContent: o.text || '',
  dataset: o.dataset || {},
  disabled: !!o.disabled,
  _aria: (o.aria === undefined ? null : o.aria),
  _ariaDisabled: (o.ariaDisabled === undefined ? null : o.ariaDisabled),
  _rect: o.rect,
  _display: o._display,
  _visibility: o._visibility,
  clicked: false,
  getAttribute: function (k) {
    if (k === 'aria-label') return this._aria;
    if (k === 'aria-disabled') return this._ariaDisabled;
    return null;
  },
  getBoundingClientRect: function () { return this._rect; },
  scrollIntoView: function () {},
  click: function () { this.clicked = true; },
});
const BTNS = JSON.parse(process.env.BTNS_JSON);
const buttons = BTNS.map(mkBtn);
global.document = { querySelectorAll: (sel) => buttons };
global.getComputedStyle = (b) => ({ display: b._display || 'block',
  visibility: b._visibility || 'visible' });
const fn = eval('(' + process.env.JS_SRC + ')');
const out = fn(process.env.CH || '²');
if (process.env.MODE === 'candidates') {
  console.log(JSON.stringify(out));
} else if (process.env.MODE === 'box') {
  console.log(JSON.stringify(out));
} else {
  console.log(JSON.stringify({clicked: out,
    clickedIdx: buttons.findIndex((b) => b.clicked)}));
}
"""


def _run_picker_js(js_src, buttons, mode="click", ch="²"):
    """Execute the shipped picker JS in Node against mock buttons.

    Returns the parsed stdout JSON. Skips (returns None) when
    Node is unavailable — the source-structure tests below still
    pin the contract in that case.
    """
    if shutil.which("node") is None:
        return None
    env = dict(os.environ)
    env["JS_SRC"] = js_src
    env["BTNS_JSON"] = __import__("json").dumps(buttons)
    env["MODE"] = mode
    env["CH"] = ch
    with tempfile.NamedTemporaryFile("w", suffix=".js",
                                     delete=False) as f:
        f.write(NODE_HARNESS)
        path = f.name
    try:
        res = subprocess.run(["node", path], env=env,
                             capture_output=True, text=True, timeout=30)
    finally:
        os.unlink(path)
    if res.returncode != 0:
        raise AssertionError(f"node harness failed: {res.stderr}")
    return __import__("json").loads(res.stdout.strip())


def _power_btns():
    # First DOM-order match: a hidden/zero-rect duplicate.
    # Second match: the visible X² button.
    return [
        {"text": "X²", "aria": "X²",
         "rect": {"x": 0, "y": 0, "width": 0, "height": 0}},
        {"text": "X²", "aria": "X²",
         "rect": {"x": 100, "y": 200, "width": 40, "height": 24}},
    ]


class TestPowerPickerIteratesAllMatches(unittest.TestCase):
    def test_click_js_iterates_all_matches(self):
        # The first-match trap is gone from the shipped JS: it
        # iterates _cands with a usability gate, never .find().
        self.assertIn("for (const b of _cands)", NS.CLICK_POWER_JS)
        self.assertIn("_candUsable(b)", NS.CLICK_POWER_JS)
        self.assertNotIn(".find(isPowerBtn)", NS.CLICK_POWER_JS)
        self.assertIn("document.querySelectorAll('button')",
                      NS.CLICK_POWER_JS)
        self.assertIn("b.click()", NS.CLICK_POWER_JS)

    def test_box_js_iterates_all_matches(self):
        self.assertIn("for (const b of _cands)", NS.POWER_BTN_BOX_JS)
        self.assertIn("_candUsable(b)", NS.POWER_BTN_BOX_JS)
        self.assertNotIn(".find(isPowerBtn)", NS.POWER_BTN_BOX_JS)
        self.assertIn("return {x:", NS.POWER_BTN_BOX_JS)

    def test_click_picks_visible_second_match(self):
        # Iter-20 shape, executed in Node against the REAL JS:
        # first match zero-rect -> the visible second match is
        # clicked (iter-20's code returned clicked=False here).
        out = _run_picker_js(NS.CLICK_POWER_JS, _power_btns())
        if out is None:
            self.skipTest("node unavailable")
        self.assertTrue(out["clicked"])
        self.assertEqual(out["clickedIdx"], 1)

    def test_click_skips_disabled_first_match(self):
        btns = [
            {"text": "X²", "aria": "X²", "disabled": True,
             "rect": {"x": 10, "y": 20, "width": 40, "height": 24}},
            {"text": "X²", "aria": "X²",
             "rect": {"x": 100, "y": 200, "width": 40, "height": 24}},
        ]
        out = _run_picker_js(NS.CLICK_POWER_JS, btns)
        if out is None:
            self.skipTest("node unavailable")
        self.assertTrue(out["clicked"])
        self.assertEqual(out["clickedIdx"], 1)

    def test_click_all_hidden_returns_false(self):
        btns = [
            {"text": "X²", "aria": "X²",
             "rect": {"x": 0, "y": 0, "width": 0, "height": 0}},
            {"text": "X²", "aria": "X²", "_display": "none",
             "rect": {"x": 5, "y": 5, "width": 40, "height": 24}},
        ]
        out = _run_picker_js(NS.CLICK_POWER_JS, btns)
        if out is None:
            self.skipTest("node unavailable")
        self.assertFalse(out["clicked"])
        self.assertEqual(out["clickedIdx"], -1)

    def test_box_returns_visible_second_match_centre(self):
        out = _run_picker_js(NS.POWER_BTN_BOX_JS, _power_btns(),
                             mode="box")
        if out is None:
            self.skipTest("node unavailable")
        self.assertEqual(out, {"x": 120.0, "y": 212.0,
                               "w": 40, "h": 24})

    def test_candidates_dump_lists_all_matches(self):
        out = _run_picker_js(NS.POWER_BTN_CANDIDATES_JS, _power_btns(),
                             mode="candidates")
        if out is None:
            self.skipTest("node unavailable")
        self.assertEqual(len(out), 2)
        self.assertFalse(out[0]["usable"])
        self.assertEqual(out[0]["rect"],
                         {"x": 0, "y": 0, "w": 0, "h": 0})
        self.assertTrue(out[1]["usable"])
        self.assertEqual(out[1]["text"], "X²")
        self.assertEqual(out[1]["aria"], "X²")
        for c in out:
            self.assertIn("tag", c)
            self.assertIn("disabled", c)


# ---------------------------------------------------------------- fix 1+3
# Failure path: candidate dump logged, ladder cap engaged.

class _FakeFailKeyboard:
    def __init__(self, page):
        self._page = page
        self.typed = []
        self.pressed = []
        self.inserted = []

    async def type(self, text, delay=0):
        self.typed.append((text, delay))

    async def press(self, key):
        self.pressed.append(key)

    async def insert_text(self, text):
        self.inserted.append(text)


class _FakeFailMouse:
    def __init__(self):
        self.clicked = []

    async def click(self, x, y):
        self.clicked.append((x, y))


class _FakeFailPage:
    """Board where EVERY ² entry method fails (the iter-20 guess-4
    shape): js-click False, box None, clipboard raises,
    insert_text no-change. Candidates: 2 matches (hidden + visible
    per the old first-match story — here both report unusable to
    the Python side because the fake's click path is dead)."""

    def __init__(self):
        self.frames = []
        self.keyboard = _FakeFailKeyboard(self)
        self.mouse = _FakeFailMouse()
        self.click_calls = 0
        self.box_calls = 0
        self.candidate_calls = 0
        self.candidates = [
            {"tag": "BUTTON", "text": "X²", "aria": "X²",
             "rect": {"x": 0, "y": 0, "w": 0, "h": 0},
             "disabled": False, "aria_disabled": None,
             "display": "block", "visibility": "visible",
             "usable": False},
            {"tag": "BUTTON", "text": "X²", "aria": "X²",
             "rect": {"x": 100, "y": 200, "w": 40, "h": 24},
             "disabled": False, "aria_disabled": None,
             "display": "block", "visibility": "visible",
             "usable": True},
        ]

    async def wait_for_timeout(self, _ms):
        return None

    async def query_selector(self, sel):
        return None

    async def evaluate(self, js, *args):
        s = js if isinstance(js, str) else ""
        if "POWER_BTN_CANDIDATES" in s:
            self.candidate_calls += 1
            return [dict(c) for c in self.candidates]
        if "sup_cells" in s:
            return {"tiles": 9, "sup_cells": 0, "grids": []}
        if "return {x:" in s:
            self.box_calls += 1
            return None
        if "b.click()" in s:
            self.click_calls += 1
            return False
        if "navigator.clipboard" in s:
            raise Exception("NotAllowedError: Failed to execute "
                            "'writeText' on 'Clipboard': Write "
                            "permission denied")
        if "getComputedStyle" in s:
            return [{"rows": [[-1] * 10], "texts": ["1+67+8=76"]}]
        if "div.pb-grid *" in s:
            return 0
        return None


class TestPowerFailureDumpAndLadderCap(unittest.TestCase):
    EQ = "1²+67+8=76"

    def test_failure_logs_all_candidates(self):
        page = _FakeFailPage()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _run(NS.type_equation(page, self.EQ))
        out = buf.getvalue()
        self.assertIn("FAILED after all entry methods", out)
        self.assertIn("candidates (2):", out)
        self.assertIn("X²", out)
        self.assertIn("usable", out)
        self.assertEqual(page.candidate_calls, 1)
        self.assertIn(self.EQ, NS._ladder_failed_set(page))

    def test_second_entry_fails_fast_without_reclick(self):
        # The cap: the second type_equation for the SAME equation
        # skips the whole ladder (no new js-click / box calls, no
        # second candidate dump) — the iter-20 burn was exactly
        # these repeats (~27s per type_guess repeat).
        page = _FakeFailPage()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _run(NS.type_equation(page, self.EQ))
            clicks_after_first = page.click_calls
            boxes_after_first = page.box_calls
            dumps_after_first = page.candidate_calls
            _run(NS.type_equation(page, self.EQ))
        out = buf.getvalue()
        self.assertEqual(page.click_calls, clicks_after_first)
        self.assertEqual(page.box_calls, boxes_after_first)
        self.assertEqual(page.candidate_calls, dumps_after_first)
        self.assertIn("ladder cap", out)
        self.assertIn("skipping re-entry", out)

    def test_cap_is_per_equation(self):
        # A DIFFERENT ² equation is not capped by the first
        # equation's failure — the cap is per-equation, not global.
        page = _FakeFailPage()
        with contextlib.redirect_stdout(io.StringIO()):
            _run(NS.type_equation(page, self.EQ))
        self.assertNotIn("1²+60+7=68", NS._ladder_failed_set(page))
        clicks_before = page.click_calls
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _run(NS.type_equation(page, "1²+60+7=68"))
        self.assertGreater(page.click_calls, clicks_before)
        self.assertIn("1²+60+7=68", NS._ladder_failed_set(page))

    def test_cap_is_per_page(self):
        # The cap is scoped to the PAGE: the same equation
        # failing on one page must not cap a fresh page (this is
        # what a process-global set got wrong — a failing test
        # for '1²+67+8=76' capped every later caller of it).
        page_a = _FakeFailPage()
        with contextlib.redirect_stdout(io.StringIO()):
            _run(NS.type_equation(page_a, self.EQ))
        self.assertIn(self.EQ, NS._ladder_failed_set(page_a))
        page_b = _FakeFailPage()
        self.assertNotIn(self.EQ, NS._ladder_failed_set(page_b))
        clicks_before = page_b.click_calls
        with contextlib.redirect_stdout(io.StringIO()):
            _run(NS.type_equation(page_b, self.EQ))
        self.assertGreater(page_b.click_calls, clicks_before)

    def test_type_guess_retry2_skipped_under_cap(self):
        # type_guess for a capped ² equation returns False
        # WITHOUT a retry2 retype (no second ladder). The fake's
        # Enter never evaluates (row stays incomplete), so
        # attempt1 fails, retry1 (Enter-only) is skipped as an
        # entry failure, and retry2 hits the cap.
        page = _FakeFailPage()
        NS._ladder_failed_set(page).add(self.EQ)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            state, counts, ok = _run(
                NS.type_guess(page, self.EQ, 10, [0]))
        out = buf.getvalue()
        self.assertFalse(ok)
        self.assertIn("retry2 SKIPPED (ladder cap)", out)
        self.assertEqual(page.click_calls, 0)

    def test_outer_retries_gated_on_cap(self):
        pm = _fn_src(SRC_NERDLE, "async def play_mode", "async def main")
        self.assertIn("nxt not in _ladder_failed_set(page)", pm)
        self.assertIn("SKIPPED (ladder cap)", pm)
        self.assertIn("site-solution retry", pm)
        self.assertIn("entry retries SKIPPED ", pm)
        # Per-mode reset (one page serves all 9 modes).
        self.assertIn("_POWER_LADDER_FAILED_BY_PAGE[page] = set()", pm)


# ---------------------------------------------------------------- fix 2
# Speed single-grid win: pure helpers + verified check.

class TestSpeedGreenRows(unittest.TestCase):
    def _grid(self, rows):
        return {"rows": rows, "texts": [""] * len(rows)}

    def test_green_row_count(self):
        state = [self._grid([[2] * 8, [1, 0, 1, 0, 1, 0, 1, 0]]),
                 self._grid([[2] * 8])]
        self.assertEqual(NS._green_row_count(state), 2)
        self.assertEqual(NS._green_row_count([]), 0)
        self.assertEqual(NS._green_row_count([self._grid([[-1] * 8])]), 0)

    def test_mode_won_by_state_speed_single_grid(self):
        # One grid, 2 green rows = won for speed (the iter-20
        # shape: starter + played answer in ONE grid).
        state = [self._grid([[2] * 8, [2] * 8])]
        self.assertTrue(NS._mode_won_by_state(state, 2, "speed"))
        self.assertFalse(NS._mode_won_by_state(state, 2, "quad"))
        self.assertFalse(NS._mode_won_by_state(
            [self._grid([[2] * 8])], 2, "speed"))
        self.assertTrue(NS._mode_won_by_state(
            [self._grid([[2] * 8]), self._grid([[2] * 8])], 2, "quad"))


class _FakeWinPage:
    """Fake for _check_win_verified: WIN_JS -> win_js, popup ->
    popup flag."""

    def __init__(self, win_js=True, popup=False):
        self.win_js = win_js
        self.popup = popup
        self.popup_calls = 0

    async def wait_for_timeout(self, _ms):
        return None

    async def evaluate(self, js, *args):
        s = js if isinstance(js, str) else ""
        if "SPEED_WIN_POPUP" in s:
            self.popup_calls += 1
            return self.popup
        if "you won" in s:
            return self.win_js
        return None


def _speed_state_two_green():
    return [{"rows": [[2] * 8, [2] * 8], "texts": ["2+10-6=6",
                                                  "50-6*7=8"]}]


class TestSpeedWinVerified(unittest.TestCase):
    POOL = ["50-6*7=8", "2+10-6=6"]
    PLAYED = ["2+10-6=6", "50-6*7=8"]

    def test_single_grid_flow_verified(self):
        # Iter-20 shape: 1 grid, starter + played answer green,
        # WIN_JS true -> verified (iter-20 returned False on
        # len(state) >= boards alone).
        page = _FakeWinPage(win_js=True, popup=False)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ok = _run(NS._check_win_verified(
                page, 8, 2, _speed_state_two_green(),
                list(self.PLAYED), list(self.POOL), mode_id="speed"))
        self.assertTrue(ok)
        self.assertIn("single-grid flow", buf.getvalue())

    def test_popup_path_verified_on_read_divergence(self):
        # WIN_JS true but READ_JS sees only 1 green row (WIN_JS
        # scans all divs, READ_JS only div.pb-grid): the game's
        # popup + solved grid + coverage still verify the win.
        page = _FakeWinPage(win_js=True, popup=True)
        state = [{"rows": [[2] * 8], "texts": ["2+10-6=6"]}]
        ok = _run(NS._check_win_verified(
            page, 8, 2, state, list(self.PLAYED),
            list(self.POOL), mode_id="speed"))
        self.assertTrue(ok)
        self.assertEqual(page.popup_calls, 1)

    def test_popup_alone_never_wins_without_coverage(self):
        # Popup present but a site solution unplayed -> NOT won.
        # (The iter-19 lesson: wonText/popup alone never wins.)
        page = _FakeWinPage(win_js=True, popup=True)
        ok = _run(NS._check_win_verified(
            page, 8, 2, _speed_state_two_green(),
            ["2+10-6=6"], list(self.POOL), mode_id="speed"))
        self.assertFalse(ok)

    def test_popup_alone_never_wins_without_solved_grids(self):
        page = _FakeWinPage(win_js=True, popup=True)
        state = [{"rows": [[-1] * 8, [-1] * 8], "texts": ["", ""]}]
        ok = _run(NS._check_win_verified(
            page, 8, 2, state, list(self.PLAYED),
            list(self.POOL), mode_id="speed"))
        self.assertFalse(ok)

    def test_new_board_unsolved_blocks_reverification(self):
        # After an advance reveals board 2, the board-1 win must
        # NOT re-verify while board 2 is unsolved.
        page = _FakeWinPage(win_js=True, popup=False)
        state = [{"rows": [[2] * 8, [2] * 8],
                  "texts": ["2+10-6=6", "50-6*7=8"]},
                 {"rows": [[-1] * 8], "texts": [""]}]
        ok = _run(NS._check_win_verified(
            page, 8, 2, state, list(self.PLAYED),
            list(self.POOL), mode_id="speed"))
        self.assertFalse(ok)

    def test_quad_keeps_strict_every_grid_rule(self):
        page = _FakeWinPage(win_js=True, popup=True)
        ok = _run(NS._check_win_verified(
            page, 8, 4, _speed_state_two_green(),
            list(self.PLAYED), list(self.POOL), mode_id="quad"))
        self.assertFalse(ok)

    def test_win_js_false_with_popup_still_verifies(self):
        # The popup is an INDEPENDENT verdict signal: WIN_JS can
        # miss the win (green-row counting divergence) while the
        # game itself granted it — popup + solved + coverage
        # still verify.
        page = _FakeWinPage(win_js=False, popup=True)
        ok = _run(NS._check_win_verified(
            page, 8, 2, _speed_state_two_green(),
            list(self.PLAYED), list(self.POOL), mode_id="speed"))
        self.assertTrue(ok)
        self.assertEqual(page.popup_calls, 1)

    def test_win_js_false_without_popup_never_wins(self):
        page = _FakeWinPage(win_js=False, popup=False)
        ok = _run(NS._check_win_verified(
            page, 8, 2, _speed_state_two_green(),
            list(self.PLAYED), list(self.POOL), mode_id="speed"))
        self.assertFalse(ok)


# ---------------------------------------------------------------- fix 2
# Speed end-to-end: pre-filled SUBMITTED starter + advance.

def _shift13(eq):
    return "".join(chr(ord(c) + 13) for c in eq)


class _FakeSpeedMouse:
    async def click(self, x, y):
        return None


class _FakeSpeedLocator:
    def __init__(self, page):
        self._page = page
        self.first = self

    async def bounding_box(self, timeout=None):
        return {"x": 0.0, "y": 0.0, "width": 100.0, "height": 100.0}


class _FakeSpeedKeyboard:
    def __init__(self, page):
        self._page = page
        self.typed = []
        self.pressed = []

    async def type(self, text, delay=0):
        self.typed.append((text, delay))
        g = self._page._active_grid()
        if g is None:
            return
        for i, fb in enumerate(g["rows"]):
            if not NS._done(fb):
                g["texts"][i] = ((g["texts"][i] or "") + text)[:8]
                return

    async def press(self, key):
        self.pressed.append(key)
        if key == "Enter":
            self._page._submit()
        elif key == "Backspace":
            g = self._page._active_grid()
            if g is None:
                return
            for i, fb in enumerate(g["rows"]):
                if not NS._done(fb) and g["texts"][i]:
                    g["texts"][i] = g["texts"][i][:-1]
                    return

    async def insert_text(self, text):
        await self.type(text)


class _FakeSpeedPage:
    """Playwright stand-in for the speed page in the iter-20
    shape: ONE grid whose row 1 already holds the SUBMITTED
    starter '2+10-6=6' (green) at load; grid solution A.
    `advance_grid` (optional) is appended when the win popup's
    Next button is clicked — board 2 behind Next.
    `popup_next`: the win popup's Next label, or None (no Next
    button -> single-grid flow)."""

    A = "50-6*7=8"
    B = "2+10-6=6"

    def __init__(self, advance_grid=None, popup_next=None,
                 pool=None):
        self.url = "https://speed.nerdlegame.com/"
        self.frames = []
        self.keyboard = _FakeSpeedKeyboard(self)
        self.mouse = _FakeSpeedMouse()
        self._active = 0
        self.advance_grid = advance_grid
        self.popup_next = popup_next
        self.pool = pool if pool is not None else [self.A, self.B]
        self.grids = [self._grid_with_starter(self.A)]
        self.screenshots = []
        self.advance_clicks = 0

    @staticmethod
    def _grid_with_starter(solution):
        # Row 1 holds the SUBMITTED starter '2+10-6=6' — evaluated
        # (colored tiles in the iter-20 video) but NOT green: it
        # is a pre-filled guess, not the grid's solution. The
        # grid is therefore NOT solved at load (the iter-20 bot
        # played 2 guesses instead of breaking at iteration 0),
        # and the win verifies via the game's popup once the
        # played answer turns a row green.
        rows = [[1, 0, 1, 0, 1, 0, 1, 0]] + [[-1] * 8 for _ in range(5)]
        texts = ["2+10-6=6"] + [""] * 5
        return {"solution": solution, "rows": rows, "texts": texts}

    @staticmethod
    def _fresh_grid(solution):
        return {"solution": solution,
                "rows": [[-1] * 8 for _ in range(6)],
                "texts": [""] * 6}

    def _active_grid(self):
        if 0 <= self._active < len(self.grids):
            return self.grids[self._active]
        return None

    def _popup_present(self):
        # The game shows its win popup once every grid holds a
        # green row (a solved board each).
        if not self.grids:
            return False
        return all(any(all(v == 2 for v in r) for r in g["rows"])
                   for g in self.grids)

    def _submit(self):
        g = self._active_grid()
        if g is None:
            return
        for i, fb in enumerate(g["rows"]):
            if not NS._done(fb) and (g["texts"][i] or "").strip():
                typed = g["texts"][i].strip()
                if typed == g["solution"]:
                    g["rows"][i] = [2] * 8
                else:
                    g["rows"][i] = [1, 0, 1, 0, 1, 0, 1, 0]
                return

    async def wait_for_timeout(self, _ms):
        return None

    async def goto(self, url, **kw):
        return None

    async def screenshot(self, path=None, **kw):
        self.screenshots.append(path)
        return b""

    async def query_selector(self, sel):
        return None

    def on(self, *a):
        return None

    def remove_listener(self, *a):
        return None

    def locator(self, sel):
        return _FakeSpeedLocator(self)

    async def evaluate(self, js, *args):
        s = js if isinstance(js, str) else ""
        if "SPEED_LOAD_DUMP" in s:
            return {"local": [{"key": "gameState", "len": 0,
                               "preview": ""}],
                    "session": [],
                    "grids": [{"rows": list(g["texts"])}
                              for g in self.grids],
                    "win_popup_present": self._popup_present(),
                    "body_len": 100}
        if "SPEED_ADVANCE" in s:
            if args:
                # The Next-button click.
                self.advance_clicks += 1
                if self.advance_grid is not None:
                    self.grids.append(self.advance_grid)
                    self._active = len(self.grids) - 1
                return args[0]
            return {"popup": self._popup_present(),
                    "next_label": (self.popup_next
                                   if self._popup_present() else None)}
        if "SPEED_WIN_POPUP" in s:
            return self._popup_present()
        if "out.keys" in s:  # restore probe: NO keys, 0 guesses —
            # the iter-20 discrepancy (filled row, empty gate).
            return {"keys": [], "guesses": 0,
                    "hasSolution": False, "wonText": False}
        if "localStorage.removeItem" in s:
            return []
        if "header_probe" in s:  # SERVED_JS
            return {"page_local": "x", "page_iso": "x",
                    "tz_offset_min": 0,
                    "header_probe": {"header_text": "", "own_headers": [],
                                     "hash_texts": [], "data_attrs": [],
                                     "probe_diag": {}, "url": self.url,
                                     "body_text": ""},
                    "useLocalTime": "true", "gameState_keys": [],
                    "dateish": {}}
        if "performance.getEntriesByType" in s:  # FETCH_JS
            return [_shift13(e) for e in self.pool]
        if "you won" in s:  # WIN_JS: green rows across grids
            green = sum(1 for g in self.grids for r in g["rows"]
                        if r and all(v == 2 for v in r))
            return green >= 2
        if "play now" in s:  # SPLASH_JS
            return False
        if "localStorage.getItem('gameState')" in s:
            return None
        if "document.body.style.zoom" in s:
            return None
        if "pb-grid" in s:  # READ_JS
            return [{"rows": [list(r) for r in g["rows"]],
                     "texts": list(g["texts"])} for g in self.grids]
        return None


class TestSpeedPrefilledStarterEndToEnd(unittest.TestCase):
    JUNK = ["11+22=33", "12+34=46", "10+20=30",
            "13+25=38", "14+26=40", "15+27=42"]
    MODE = {"id": "speed", "name": "Speed",
            "url": "https://speed.nerdlegame.com/", "len": 8,
            "boards": 2}
    OFFICIAL = {"speed": ["50-6*7=8", "2+10-6=6"]}

    def setUp(self):
        self._old_seed = os.environ.get("NERDLE_SEED")
        # NERDLE_SEED=100 -> min_probes = 2 + (100 % 2) + 1 = 3.
        os.environ["NERDLE_SEED"] = "100"
        self._old_solver = NS.solver_suggest
        self._junk = itertools.cycle(self.JUNK)
        NS.solver_suggest = lambda *a, **k: [next(self._junk)]
        self._old_played = dict(NS.PLAYED)
        self._old_served = dict(NS.SERVED)
        NS.PLAYED.clear()
        NS.SERVED.clear()
        self.addCleanup(self._restore)

    def _restore(self):
        if self._old_seed is None:
            os.environ.pop("NERDLE_SEED", None)
        else:
            os.environ["NERDLE_SEED"] = self._old_seed
        NS.solver_suggest = self._old_solver
        NS.PLAYED.clear()
        NS.PLAYED.update(self._old_played)
        NS.SERVED.clear()
        NS.SERVED.update(self._old_served)

    def test_speed_won_with_prefilled_starter_counted(self):
        # Iter-20 shape, end to end: starter '2+10-6=6' already
        # submitted at load (restore gate reads keys=[]), 2 junk
        # probes, then the site-solution override plays A. The
        # win verifies via the single-grid flow (starter + A =
        # 2 green rows) and the popup has no Next button.
        page = _FakeSpeedPage(popup_next=None)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            typed, expected, signal = _run(
                NS.play_mode(page, self.MODE, ".",
                             official=dict(self.OFFICIAL)))
        out = buf.getvalue()
        self.assertEqual(signal, "won")
        self.assertEqual(typed, 3)
        self.assertEqual(expected, 2)
        played = NS.PLAYED.get("speed") or []
        # PLAYED is ours only: the starter is NOT in PLAYED, but
        # the win still verified because the seeded history
        # counted it toward the played solutions.
        self.assertNotIn("2+10-6=6", played)
        self.assertIn("50-6*7=8", played)
        # The starter row arrived submitted-but-not-green, so
        # WIN_JS (green rows >= 2) is False and the win verifies
        # via the game's own popup + solved grid + coverage.
        self.assertIn("speed win VERIFIED (game win popup)", out)
        self.assertIn("load dump: localStorage keys=", out)
        self.assertIn("gameState", out)
        self.assertIn("advance: win popup present, no Next button",
                      out)

    def test_speed_advance_reveals_board2_then_wins(self):
        # Board 2 sits behind the win popup's Next button: after
        # board 1 wins (A played, starter B green), the bot
        # advances instead of claiming won; board 2 (solution C)
        # must be solved before the win verifies — the old win
        # must NOT re-verify while board 2 is unsolved.
        page = _FakeSpeedPage(
            advance_grid=_FakeSpeedPage._fresh_grid("63-7*9=0"),
            popup_next="Next",
            pool=["50-6*7=8", "2+10-6=6", "63-7*9=0"])
        official = {"speed": ["50-6*7=8", "2+10-6=6", "63-7*9=0"]}
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            typed, expected, signal = _run(
                NS.play_mode(page, self.MODE, ".",
                             official=dict(official)))
        out = buf.getvalue()
        self.assertEqual(signal, "won")
        self.assertEqual(page.advance_clicks, 1)
        played = NS.PLAYED.get("speed") or []
        self.assertIn("50-6*7=8", played)
        self.assertIn("63-7*9=0", played)
        self.assertIn("advance: NEW board appeared", out)
        self.assertIn("speed win UNVERIFIED", out)

    def test_speed_dump_enumerates_all_storage_keys(self):
        # The dump is the iter-21 (a) fix: it must enumerate
        # EVERY localStorage key (not just the 5 gate keys) so
        # the artifact names the starter row's true store.
        dump_src = _fn_src(SRC_NERDLE, "SPEED_LOAD_DUMP_JS = ",
                           "async def _nerdle_speed_load_dump")
        self.assertIn("store.key(i)", dump_src)
        self.assertIn("dumpStore(localStorage)", dump_src)
        self.assertIn("dumpStore(sessionStorage)", dump_src)
        self.assertIn("div.pb-grid", dump_src)

    def test_advance_never_clicks_close_or_share(self):
        # Advancing is not dismissing: the advance probe matches
        # Next/Continue only, and the click only ever clicks the
        # button whose text EQUALS the probed Next label (never
        # an X/close/share button the probe did not name).
        adv = _fn_src(SRC_NERDLE, "SPEED_ADVANCE_JS = ",
                      "SPEED_ADVANCE_CLICK_JS = ")
        self.assertIn("next|continue|next board|play next|next puzzle",
                      adv)
        click = _fn_src(SRC_NERDLE, "SPEED_ADVANCE_CLICK_JS = ",
                        "SPEED_LOAD_DUMP_JS = ")
        self.assertIn("(b.textContent || '').trim() === label", click)


if __name__ == "__main__":
    unittest.main()
