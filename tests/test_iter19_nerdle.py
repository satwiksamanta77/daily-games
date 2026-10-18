"""Fix-loop iteration 19 tests — Nerdle workstream.

Evidence-grounded in ANALYSIS16 (run 39075479441):

1. ² entry repeatability (P0, §2b/§6.2/§7): maxi guess 3 js-click
   clicked=True, guess 4 clicked=False with the X² button visibly
   present (frame gp_0190); 3x ~21s stalls. The click path was a
   SINGLE attempt with a single selector against a keyboard that
   re-renders between guesses. Now _click_power_key /
   _power_button_box re-query the button FRESH before every ² entry
   (up to 4 attempts, short settle between; never a reused handle /
   selector across guesses) and CLICK_POWER_JS / POWER_BTN_BOX_JS
   match by several selectors (aria-label exact / 'x²', button text,
   data-key). Verification unchanged: tile/sup delta, never U+00B2
   text count; max one entry per ².
2. Number-gate mini false-block (P0, §2a/§6.1/§7): gate blocked on
   `mini header #1733 not in API [497]`, but mini's own nav reads
   'mini nerdle . #1733' (the site's global daily number) and mini's
   site_solutions == official, mini won. Mini now gates on
   site_solutions == official answers when its header IS the global
   number (== classic's header), never on header-vs-API. Any other
   mini header still gates on header-vs-API (pre-iter-19 behaviour).
   Midi stays header-gated: the API's midi numbers ARE the global
   sequence ([1733]), so header-vs-API is apples-to-apples for midi.
   The gate stays FAIL-CLOSED — only the comparison is fixed.
3. Minibi header scope (P2, §6.14/§7): probe read popup/about text
   ('About mini-bi nerdle … | You won!'), header_number=None. The
   site spells the mode "mini-bi", so the prefix matcher rejected
   minibi's own header (prefix "bi"), and the loose header/title
   selectors matched the About/win popup. SERVED_JS now excludes
   dialog/modal/popup/about/overlay elements from the header
   probe, and the Python matcher normalizes "mini-bi"/"mini bi"
   to "minibi".
4. Speed win semantics (P2, §6.13/§7): speed typed only 1 of 2 site
   solutions yet signal='won' rested on the page wonText. WIN_JS's
   wonText branch now applies to single-board modes only, and
   _check_win_verified additionally requires EVERY grid solved AND
   every site solution played before a multi-board mode claims a
   win. The win bar is NOT weakened — it is per-board now.
5. Nerdle leading-wait trim (P2, §2c/§7): first-typing 19.8s
   (cookies 5.9s + sweep 2.9s + solver 1.7s + first type 5.9s +
   load ~5s). Trimmed leading IDLE only: the redundant standalone
   pre-loop sweep (the loop sweeps every iteration anyway), the
   unconditional second accept_cookies scan (now only when a
   dismissal happened), and the fixed 1500ms post-reload wait (now
   wait-for-grid with the same 1500ms cap). Typing speed / per-guess
   pacing UNCHANGED.

All tests are synthetic/local: no network, no browser, no CI run.
Fake page/board objects stand in for Playwright.
"""
import asyncio
import contextlib
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import nerdle_solver as NS
import runner as R

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
# ² entry repeatability: fresh re-query + retry + multi-selector


class _FakeTransientPowerPage:
    """Playwright stand-in for the ² ladder with a TRANSIENTLY absent
    power button: the FIRST js-click evaluate in the session returns
    False (keyboard re-rendering between guesses — the iter-18
    guess-4 shape), the second succeeds. A single-attempt click
    fails this page; the iter-19 retry helper must succeed via
    js-click with no fallthrough to coordinate/clipboard.
    """

    def __init__(self):
        self.keyboard = _FakePowerKeyboard(self)
        self.mouse = _FakePowerMouse()
        self.entered = False
        self.js_click_calls = 0
        self.clipboard_calls = 0

    async def wait_for_timeout(self, _ms):
        return None

    async def evaluate(self, js, *args):
        if "sup_cells" in js:
            tiles = 10 if self.entered else 9
            sup = 1 if self.entered else 0
            text = "12+67+8=76" if self.entered else "1+67+8=76"
            return {"tiles": tiles, "sup_cells": sup,
                    "grids": [[{"text": text, "tiles": tiles,
                                "sup_cells": sup}]]}
        if "return {x:" in js:
            return {"x": 100.0, "y": 200.0, "w": 40.0, "h": 24.0}
        if "b.click()" in js:
            self.js_click_calls += 1
            if self.js_click_calls == 1:
                return False  # transient: button not yet re-rendered
            self.entered = True
            return True
        if "navigator.clipboard" in js:
            self.clipboard_calls += 1
            self.entered = True
            return None
        if "getComputedStyle" in js:
            text = "12+67+8=76" if self.entered else "1+67+8=76"
            return [{"rows": [[-1] * 10], "texts": [text]}]
        if "div.pb-grid *" in js:
            return 0
        return None


class _FakePowerKeyboard:
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
        self._page.entered = True


class _FakePowerMouse:
    def __init__(self):
        self.clicked = []

    async def click(self, x, y):
        self.clicked.append((x, y))


class TestPowerKeyFreshRequery(unittest.TestCase):
    def test_transient_first_click_retried_and_succeeds(self):
        # Iter-18 guess-4 shape: first js-click False (button not
        # yet re-rendered), second True. Must succeed via js-click
        # with NO fallthrough to coordinate/clipboard/insert_text.
        page = _FakeTransientPowerPage()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _run(NS.type_equation(page, "1²+67+8=76"))
        out = buf.getvalue()
        self.assertGreaterEqual(page.js_click_calls, 2)
        self.assertEqual(page.mouse.clicked, [])
        self.assertEqual(page.clipboard_calls, 0)
        self.assertEqual(page.keyboard.inserted, [])
        self.assertIn("verified in row text via js-click", out)
        self.assertNotIn("FAILED after all entry methods", out)

    def test_second_superscript_in_session_works_like_first(self):
        # The iter-19 target: a second ² in the same session works
        # exactly like the first — both via js-click, single entry.
        page = _FakeTransientPowerPage()
        with contextlib.redirect_stdout(io.StringIO()):
            _run(NS.type_equation(page, "1²+67+8=76"))
        # New row / new guess: the row is fresh again.
        page.entered = False
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _run(NS.type_equation(page, "1²+60+7=68"))
        out = buf.getvalue()
        self.assertEqual(page.mouse.clicked, [])
        self.assertEqual(page.clipboard_calls, 0)
        self.assertEqual(page.keyboard.inserted, [])
        self.assertIn("verified in row text via js-click", out)
        self.assertNotIn("FAILED after all entry methods", out)

    def test_all_clicks_go_through_fresh_requery_helpers(self):
        te = _fn_src(SRC_NERDLE, "async def type_equation",
                     "async def _verify_row_or_retype")
        self.assertIn("_click_power_key(page, part)", te)
        self.assertIn("_power_button_box(page, part)", te)
        # No direct single-attempt evaluate of the click JS inside
        # type_equation — every ² click goes through the retry helper.
        self.assertNotIn("page.evaluate(CLICK_POWER_JS", te)
        self.assertNotIn("page.evaluate(POWER_BTN_BOX_JS", te)

    def test_click_js_matches_by_several_selectors(self):
        # The iter-18 exact-aria-label-only match is gone: the
        # matcher must also accept 'x²' labels, button text, and
        # data-key/data-char.
        self.assertIn("isPowerBtn", NS.CLICK_POWER_JS)
        self.assertIn("'x' + ch", NS.CLICK_POWER_JS)
        self.assertIn("textContent", NS.CLICK_POWER_JS)
        self.assertIn("dataset.key", NS.CLICK_POWER_JS)
        self.assertIn("isPowerBtn", NS.POWER_BTN_BOX_JS)

    def test_helpers_requery_fresh_per_attempt(self):
        helper = _fn_src(SRC_NERDLE, "async def _click_power_key",
                         "async def _power_button_box")
        self.assertIn("page.evaluate(CLICK_POWER_JS, ch)", helper)
        self.assertIn("attempts", helper)
        self.assertIn("wait_for_timeout(wait_ms)", helper)
        box = _fn_src(SRC_NERDLE, "async def _power_button_box",
                      "def _last_evaluated_text")
        self.assertIn("page.evaluate(POWER_BTN_BOX_JS, ch)", box)


# ---------------------------------------------------------------- fix 2
# Number-gate mini false-block


class TestMiniGate(unittest.TestCase):
    API_1018 = {
        "classic": [1733], "micro": [26], "mini": [497],
        "midi": [1733], "maxi": [1368], "minibi": [603, 6],
        "quad": [3289, 3309, 3329, 3349],
        "speed": [1732, 1725], "instant": [1718],
        "classicPuzzleNumber": 1733}
    OFFICIAL_1018 = {
        "classic": ["9*83=747"], "mini": ["30/6=5"],
        "midi": ["4*13=52"], "maxi": ["1²+60+7=68"]}

    def _served(self, classic_hn=1733, mini_hn=1733, mini_site=None,
                mini_matches=True, midi_hn=None, midi_matches=True):
        served = {
            "classic": {"header_number": classic_hn,
                        "matches_target_official": True,
                        "site_solutions": ["9*83=747"]},
            "mini": {"header_number": mini_hn,
                     "matches_target_official": mini_matches,
                     "site_solutions": mini_site},
        }
        if midi_hn is not None:
            served["midi"] = {"header_number": midi_hn,
                              "matches_target_official": midi_matches,
                              "site_solutions": ["4*13=52"]}
        return served

    def test_iter18_production_case_passes(self):
        # ANALYSIS16 §2a, verbatim: mini header #1733 (global),
        # site_solutions ['30/6=5'] == official, mini won. The
        # iter-18 gate blocked on this; iter-19 must not.
        served = self._served(mini_site=["30/6=5"])
        blocked, _, per = NS._nerdle_number_gate(
            served, self.API_1018, official=self.OFFICIAL_1018)
        self.assertFalse(blocked)
        self.assertIn("site-solutions", per["mini"]["status"])

    def test_mini_global_header_solution_mismatch_blocks(self):
        # Global header + site solutions != official: fail-closed.
        served = self._served(mini_site=["1+2=3"])
        blocked, detail, _ = NS._nerdle_number_gate(
            served, self.API_1018, official=self.OFFICIAL_1018)
        self.assertTrue(blocked)
        self.assertIn("mini", detail)

    def test_mini_global_header_no_solutions_blocks(self):
        # Global header + NO site-solutions proof: fail-closed.
        served = self._served(mini_site=[], mini_matches=None)
        blocked, detail, _ = NS._nerdle_number_gate(
            served, self.API_1018, official=self.OFFICIAL_1018)
        self.assertTrue(blocked)
        self.assertIn("mini", detail)

    def test_mini_modespecific_header_still_api_gated(self):
        # A mini header that is NOT the global number is a
        # mode-specific claim: header-vs-API still applies
        # (pre-iter-19 behaviour, pinned by test_iter13).
        served = self._served(mini_hn=99, mini_site=["30/6=5"])
        blocked, detail, _ = NS._nerdle_number_gate(
            served, self.API_1018, official=self.OFFICIAL_1018)
        self.assertTrue(blocked)
        self.assertIn("mini", detail)
        served = self._served(mini_hn=497, mini_site=["30/6=5"])
        blocked, _, _ = NS._nerdle_number_gate(
            served, self.API_1018, official=self.OFFICIAL_1018)
        self.assertFalse(blocked)

    def test_mini_answer_mismatch_still_blocks(self):
        # test_iter14 pin: mini=(497, matches=False) blocks.
        served = self._served(mini_hn=497, mini_site=["30/6=5"],
                              mini_matches=False)
        blocked, detail, _ = NS._nerdle_number_gate(
            served, self.API_1018, official=self.OFFICIAL_1018)
        self.assertTrue(blocked)
        self.assertIn("mini", detail)

    def test_midi_stays_header_gated(self):
        # Midi is NOT demoted: the API's midi numbers ARE the global
        # sequence, so header-vs-API is apples-to-apples for midi.
        # (mini is given valid site solutions so only midi is under
        # test here.)
        served = self._served(mini_site=["30/6=5"], midi_hn=9999)
        blocked, detail, _ = NS._nerdle_number_gate(
            served, self.API_1018, official=self.OFFICIAL_1018)
        self.assertTrue(blocked)
        self.assertIn("midi", detail)
        served = self._served(mini_site=["30/6=5"], midi_hn=1733)
        blocked, _, _ = NS._nerdle_number_gate(
            served, self.API_1018, official=self.OFFICIAL_1018)
        self.assertFalse(blocked)

    def test_legacy_no_official_fallback(self):
        # Legacy callers (no official list): the matches flag is the
        # only day-proof — True passes, False blocks.
        served = self._served(mini_site=None, mini_matches=True)
        blocked, _, _ = NS._nerdle_number_gate(served, self.API_1018)
        self.assertFalse(blocked)
        served = self._served(mini_site=None, mini_matches=False)
        blocked, detail, _ = NS._nerdle_number_gate(served, self.API_1018)
        self.assertTrue(blocked)
        self.assertIn("mini", detail)

    def test_site_solutions_match_is_exact(self):
        self.assertTrue(NS._nerdle_site_solutions_match(
            ["30/6=5"], ["30/6=5"]))
        self.assertFalse(NS._nerdle_site_solutions_match(
            ["30/6=5"], ["30/6=5", "1+2=3"]))
        self.assertFalse(NS._nerdle_site_solutions_match([], ["30/6=5"]))
        self.assertFalse(NS._nerdle_site_solutions_match(
            ["30/6=5"], []))

    def test_runner_gate_mirrors_solver_mini(self):
        served = self._served(mini_site=["30/6=5"])
        blocked, _, per = R._nerdle_number_gate(
            served, self.API_1018, official=self.OFFICIAL_1018)
        self.assertFalse(blocked)
        self.assertIn("site-solutions", per["mini"]["status"])
        served = self._served(mini_site=["1+2=3"])
        blocked, detail, _ = R._nerdle_number_gate(
            served, self.API_1018, official=self.OFFICIAL_1018)
        self.assertTrue(blocked)
        self.assertIn("mini", detail)

    def test_gate_call_sites_pass_official(self):
        self.assertIn("_nerdle_number_gate(\n        SERVED, api_numbers, official=official)",
                      SRC_NERDLE)
        runner_src = (Path(__file__).parent.parent
                      / "runner.py").read_text(encoding="utf-8")
        self.assertIn("official=rec.get(\"equations_official\")",
                      runner_src)


# ---------------------------------------------------------------- fix 3
# Minibi header scope


class TestMinibiHeaderScope(unittest.TestCase):
    def test_hyphenated_minibi_header_parses(self):
        # The site spells it "mini-bi": 'mini-bi nerdle . #1234'
        # is minibi's OWN header, not chrome.
        probe = {"own_headers": [{"tag": "DIV", "cls": "main-nav",
                                  "text": "mini-bi nerdle . #1234"}]}
        n, ev = NS._nerdle_own_header_from_probe(probe, "minibi")
        self.assertEqual(n, 1234)
        self.assertIsNotNone(ev)
        self.assertEqual(ev["prefix"], "minibi")

    def test_spaced_minibi_header_parses(self):
        probe = {"own_headers": [{"tag": "DIV", "cls": "main-nav",
                                  "text": "mini bi nerdle . #1234"}]}
        n, _ = NS._nerdle_own_header_from_probe(probe, "minibi")
        self.assertEqual(n, 1234)

    def test_about_popup_text_yields_no_header(self):
        # The iter-18 minibi header_text ('About mini-bi nerdle …
        # | You won!') carries no puzzle number and must never
        # yield a header number.
        probe = {"own_headers": [
            {"tag": "DIV", "cls": "about",
             "text": "About mini-bi nerdle - the fun math game | You won!"}]}
        n, ev = NS._nerdle_own_header_from_probe(probe, "minibi")
        self.assertIsNone(n)
        self.assertIsNone(ev)

    def test_served_js_scopes_header_to_game_header(self):
        # SERVED_JS must exclude dialog/modal/popup/about/overlay
        # elements from the LOOSE header probe and include
        # .main-nav. Iter-20 (ANALYSIS17 §2b/§6.1): the iter-19
        # blanket `_inPopup` skip (which also excluded
        # aria-hidden wrappers — i.e. the game header itself while
        # a win popup is open, 8/9 -> 0/9) is REPLACED by
        # `_popupTrip` + the strict filter: the own-header
        # collection applies NO popup skip (each entry records
        # its popup_trip), and the loose selectors keep only the
        # strict filter (dialog/modal/popup/about/overlay,
        # WITHOUT aria-hidden).
        self.assertIn("_popupTrip", NS.SERVED_JS)
        self.assertIn("_inPopupStrict", NS.SERVED_JS)
        self.assertIn('[role="dialog"]', NS.SERVED_JS)
        self.assertIn("main-nav", NS.SERVED_JS)
        self.assertIn("popup_trip: _trip", NS.SERVED_JS)
        self.assertNotIn("if (_inPopup(e)) continue;", NS.SERVED_JS)
        self.assertNotIn("!_inPopup(e)", NS.SERVED_JS)


# ---------------------------------------------------------------- fix 4
# Speed win semantics


class _FakeWinPage:
    def __init__(self, win_text=True):
        self.win_text = win_text

    async def evaluate(self, js, *args):
        if "you won" in js:
            return self.win_text
        return False


def _grid(solved, text="12+34=46"):
    fb = [2] * 8 if solved else [-1] * 8
    return {"rows": [fb], "texts": [text]}


class TestSpeedWinSemantics(unittest.TestCase):
    SPEED_SOLUTIONS = ["50-6*7=8", "2+10-6=6"]

    def test_win_js_wontext_single_board_only(self):
        self.assertIn("boards <= 1", NS.WIN_JS)

    def test_single_board_wontext_still_wins(self):
        page = _FakeWinPage(win_text=True)
        ok = _run(NS._check_win_verified(
            page, 8, 1, [_grid(True)], ["12+34=46"], ["12+34=46"]))
        self.assertTrue(ok)

    def test_speed_one_board_solved_does_not_win(self):
        # Iter-18 shape: wonText present, only 1 of 2 boards solved.
        page = _FakeWinPage(win_text=True)
        ok = _run(NS._check_win_verified(
            page, 8, 2, [_grid(True, "50-6*7=8"), _grid(False)],
            ["50-6*7=8"], self.SPEED_SOLUTIONS))
        self.assertFalse(ok)

    def test_speed_both_solved_one_solution_played_does_not_win(self):
        page = _FakeWinPage(win_text=True)
        ok = _run(NS._check_win_verified(
            page, 8, 2, [_grid(True, "50-6*7=8"),
                         _grid(True, "2+10-6=6")],
            ["50-6*7=8"], self.SPEED_SOLUTIONS))
        self.assertFalse(ok)

    def test_speed_both_solved_both_played_wins(self):
        page = _FakeWinPage(win_text=True)
        ok = _run(NS._check_win_verified(
            page, 8, 2, [_grid(True, "50-6*7=8"),
                         _grid(True, "2+10-6=6")],
            ["50-6*7=8", "2+10-6=6"], self.SPEED_SOLUTIONS))
        self.assertTrue(ok)

    def test_no_page_win_means_no_win(self):
        page = _FakeWinPage(win_text=False)
        ok = _run(NS._check_win_verified(
            page, 8, 2, [_grid(True), _grid(True)],
            self.SPEED_SOLUTIONS, self.SPEED_SOLUTIONS))
        self.assertFalse(ok)

    def test_solutions_cover_exact_and_dom_normalized(self):
        self.assertTrue(NS._solutions_played_cover(
            ["50-6*7=8", "2+10-6=6"], self.SPEED_SOLUTIONS))
        # DOM-normalized plain-2 form of a superscript equation counts.
        self.assertTrue(NS._solutions_played_cover(
            ["12+67+8=76"], ["1²+67+8=76"]))
        self.assertFalse(NS._solutions_played_cover(
            ["50-6*7=8"], self.SPEED_SOLUTIONS))
        # Unknown solutions (empty) fall back to the grid check alone.
        self.assertTrue(NS._solutions_played_cover(["x"], []))

    def test_play_mode_uses_verified_win(self):
        pm = _fn_src(SRC_NERDLE, "async def play_mode", "async def main")
        # The loop / post-loop / final win checks go through the
        # verified helper; the only bare check_win() left is the
        # single-board instant branch.
        self.assertIn("_check_win_verified(", pm)
        self.assertEqual(pm.count("await check_win()"), 1)
        self.assertNotIn("if await check_win():", pm)


# ---------------------------------------------------------------- fix 5
# Nerdle leading-wait trim (leading IDLE only; typing speed unchanged)


class TestLeadingWaitTrim(unittest.TestCase):
    def test_pre_loop_sweep_removed(self):
        # The standalone pre-loop sweep ran back-to-back with the
        # loop's own first-iteration sweep; the sweep call + its
        # stage label are gone (comments may still name it).
        self.assertNotIn('_stage("pre-loop sweep")', SRC_NERDLE)

    def test_second_cookies_scan_conditional(self):
        pm = _fn_src(SRC_NERDLE, "async def play_mode", "async def main")
        self.assertIn("if _cookies_acted or _modals_acted:", pm)
        dm = _fn_src(SRC_NERDLE, "async def dismiss_modals",
                     "SPLASH_JS = ")
        self.assertIn("return True", dm)
        self.assertIn("return False", dm)

    def test_fresh_board_waits_for_grid_not_fixed_sleep(self):
        # Iter-22 (P0): the hydration wait moved into the
        # _nerdle_wait_hydrated helper (grid render + 400 ms
        # settle) so BOTH restore-gate probes read post-hydration
        # state; _nerdle_fresh_board calls the helper. The
        # contract is unchanged: wait for the grid, never a
        # fixed 1500 ms sleep.
        fb = _fn_src(SRC_NERDLE, "async def _nerdle_fresh_board",
                     "async def play_mode")
        self.assertIn("_nerdle_wait_hydrated(page)", fb)
        self.assertNotIn("wait_for_timeout(1500)", fb)
        helper = _fn_src(SRC_NERDLE, "async def _nerdle_wait_hydrated",
                         "async def _nerdle_fresh_board")
        self.assertIn('wait_for_selector("div.pb-grid"', helper)
        self.assertNotIn("wait_for_timeout(1500)", helper)

    def test_branches_without_loop_still_sweep(self):
        # The legacy + instant-no-state branches never enter the
        # loop, so they keep their own sweep before typing.
        pm = _fn_src(SRC_NERDLE, "async def play_mode", "async def main")
        self.assertIn("legacy direct typing", pm)
        self.assertGreater(pm.count("await sweep_popups(page"), 1)

    def test_typing_speed_unchanged(self):
        te = _fn_src(SRC_NERDLE, "async def type_equation",
                     "async def _verify_row_or_retype")
        self.assertIn("async def type_equation(page, eq, delay=200)", te)
        self.assertIn("page.keyboard.type(eq, delay=delay)", te)
        self.assertIn("page.keyboard.type(part, delay=delay)", te)
        tg = _fn_src(SRC_NERDLE, "async def type_guess", "FETCH_JS = ")
        self.assertIn("timeout=3", tg)
        self.assertIn("timeout=5", tg)


if __name__ == "__main__":
    unittest.main()
