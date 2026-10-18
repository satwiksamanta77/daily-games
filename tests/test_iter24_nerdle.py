"""Fix-loop iteration 24 tests — Nerdle workstream.

Evidence-grounded in ANALYSIS21 (run 39091631879):

FIX 1 — Maxi ² matcher + identical-retry burn (RC3, P0).
On the official equation `1²+67+8=76` the power-key lookup
returned `candidates (0): []` on ALL 16 ladder runs while the
X² key was visibly present/enabled (frame f0141), burning
~121s. Two distinct root causes, both fixed here:
  (a) The DIAGNOSTIC probe was broken, not the DOM: 
      POWER_KEYBOARD_STATE_JS contained two adjacent string
      literals (`"...power "` newline `"matcher ..."`) — a JS
      SyntaxError (Unexpected string) — so page.evaluate always
      threw and _power_keyboard_state always returned {}.
      Every iter-23 `keyboard_state={}` line was this probe
      bug, not a DOM fact. The strings are now joined with `+`.
  (b) The MATCHER could not see the key in its rendered forms:
      the app renders superscripts as a plain 2 in a
      <sup>/sup-styled cell (iter-18 grid finding), so the key
      can read back as textContent 'x2' with no U+00B2 anywhere;
      after a ² guess is evaluated the key is re-rendered with
      feedback; the key can be a [role="button"], a plain
      element inside a keyboard container, or inside a shadow
      root — none of which `document.querySelectorAll('button')`
      + the old matcher could see. The matcher now covers all
      of those, matched by rendered label/class regardless of
      keyboard_state.
  (c) IDENTICAL RETRIES: the iter-23 probe-failure path
      deliberately did not engage the ladder cap, so every
      retry re-ran the full ~30s ladder on the identical
      matcher result / board state (16 FAILED blocks). Now a
      retry whose probe fingerprint (candidate signature +
      in-progress row text) matches a recorded probe failure
      fails fast with the diagnostic dump only. A retry whose
      fingerprint DIFFERS (keyboard re-rendered, button
      reappeared, row changed) is not identical and proceeds —
      the fingerprint probe itself is that retry's re-probe.
      The recovery re-entry now runs AFTER focus_grid (the
      ladder's clicks may all have run unfocused).

FIX 2 — Nerdle Speed zero Next-clicks (P1, carry-forward).
Iter-23: win VERIFIED via the game grant, then the loop-top
branch called _speed_try_advance anyway, which clicked Next
once (`advance: clicked Next button 'Next up ...'` -> hub
nav). Now the grant path claims the win and STOPS: when the
grant already holds, _speed_try_advance is never called.
Advancing stays only for wins verified WITHOUT the grant.

All tests are synthetic/local: no network, no CI run.
"""
import asyncio
import contextlib
import io
import re
import sys
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


_USABLE_CAND = {"tag": "BUTTON", "text": "X²", "aria": "X²",
                "rect": {"x": 100, "y": 200, "w": 40, "h": 24},
                "disabled": False, "aria_disabled": None,
                "display": "block", "visibility": "visible",
                "usable": True}


class _FakeKeyboard:
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


class _FakeMouse:
    def __init__(self):
        self.clicked = []

    async def click(self, x, y):
        self.clicked.append((x, y))


class _FakeIter24Page:
    """Board page for the iter-24 ² tests.

    candidate_seq: candidate lists returned by successive
    POWER_BTN_CANDIDATES evaluations (last value repeats).
    click_mode: "always-false" or "succeed-after-focus"
    (the click only delivers once focus_grid has clicked the
    mouse — models a board that needed focus first).
    row_text: the in-progress row text returned by read_state.
    """

    def __init__(self, candidate_seq, click_mode="always-false",
                 row_text="1"):
        self.keyboard = _FakeKeyboard()
        self.mouse = _FakeMouse()
        self._candidate_seq = [list(c) for c in candidate_seq]
        self._candidate_calls = 0
        self.candidate_calls = 0
        self.click_calls = 0
        self.click_mode = click_mode
        self.entered = False
        self.keyboard_state_calls = 0
        self.row_text = row_text

    async def wait_for_timeout(self, _ms):
        return None

    async def evaluate(self, js, *args):
        s = js if isinstance(js, str) else ""
        if "POWER_KEYBOARD_STATE" in s:
            self.keyboard_state_calls += 1
            return {"selector": "power matcher",
                    "buttons_total": 31, "keyboard_containers": 1,
                    "grids": 1, "body_text_len": 123}
        if "POWER_BTN_CANDIDATES" in s:
            self.candidate_calls += 1
            idx = min(self._candidate_calls,
                      len(self._candidate_seq) - 1)
            self._candidate_calls += 1
            return [dict(c) for c in self._candidate_seq[idx]]
        if "sup_cells" in s:
            if self.entered:
                return {"tiles": 10, "sup_cells": 1, "grids": []}
            return {"tiles": 9, "sup_cells": 0, "grids": []}
        if "return {x:" in s:
            return None
        if "b.click()" in s:
            self.click_calls += 1
            if self.click_mode == "succeed-after-focus" \
                    and self.mouse.clicked:
                self.entered = True
                return True
            return False
        if "navigator.clipboard" in s:
            raise Exception("NotAllowedError: Failed to execute "
                            "'writeText' on 'Clipboard': Write "
                            "permission denied")
        if "getComputedStyle" in s:
            return [{"rows": [[-1] * 10],
                     "texts": [self.row_text]}]
        if "div.pb-grid *" in s:
            return 0
        return None


# ---------------------------------------------------------------- fix 1a
# Matcher covers the rendered forms; state probe actually runs.

class TestIter24MatcherSource(unittest.TestCase):
    def test_matcher_covers_flattened_label(self):
        # The DOM-flattened key reads back as 'x2' (plain 2 in a
        # <sup> cell) with no U+00B2 anywhere — the old matcher
        # required ² in aria/text/dataset and missed it.
        self.assertIn("x' + flat", NS._POWER_BTN_MATCH_JS)
        self.assertIn("startsWith('x' + flat",
                      NS._POWER_BTN_MATCH_JS)

    def test_matcher_covers_sup_child(self):
        self.assertIn("SUP", NS._POWER_BTN_MATCH_JS)
        self.assertIn("_hasSupFlat", NS._POWER_BTN_MATCH_JS)

    def test_matcher_covers_feedback_suffix_and_class(self):
        # A feedback-suffixed label ('x², present') and a
        # class-named power/exponent key.
        self.assertIn("startsWith('x' + ch", NS._POWER_BTN_MATCH_JS)
        self.assertIn("power", NS._POWER_BTN_MATCH_JS)
        self.assertIn("exponent", NS._POWER_BTN_MATCH_JS)

    def test_matcher_scope_beyond_plain_buttons(self):
        # [role="button"], keyboard-container scope, shadow roots.
        self.assertIn('[role="button"]', NS._POWER_BTN_MATCH_JS)
        self.assertIn('keyboard', NS._POWER_BTN_MATCH_JS)
        self.assertIn("shadowRoot", NS._POWER_BTN_MATCH_JS)

    def test_matcher_still_clicks_first_visible_enabled(self):
        # The iter-21 usable rule is unchanged.
        self.assertIn("_candUsable", NS.CLICK_POWER_JS)
        self.assertIn("b.click()", NS.CLICK_POWER_JS)

    def test_candidate_dump_shows_class(self):
        self.assertIn("cls:", NS.POWER_BTN_CANDIDATES_JS)

    def test_state_probe_has_no_adjacent_string_literals(self):
        # The iter-23 SyntaxError class: `"a"` newline `"b"` with
        # no `+` between them. The fixed probe joins with `+`.
        self.assertIsNone(
            re.search(r'"\s*\n\s*"', NS.POWER_KEYBOARD_STATE_JS))
        self.assertIn('\n          + "', NS.POWER_KEYBOARD_STATE_JS)

    def test_state_probe_returns_real_state(self):
        # With a working probe, keyboard_state is populated, not
        # {} — the iter-23 `keyboard_state={}` was the probe bug.
        page = _FakeIter24Page([[]])
        st = _run(NS._power_keyboard_state(page))
        self.assertEqual(st["buttons_total"], 31)
        self.assertEqual(st["keyboard_containers"], 1)

    def test_matcher_probe_independent_of_keyboard_state(self):
        # (a) in the task: the matcher finds the button from its
        # rendered label/class regardless of keyboard_state —
        # the candidate probe takes no state input at all.
        page = _FakeIter24Page([[_USABLE_CAND]])
        cands = _run(NS._power_button_candidates(page, "²"))
        self.assertEqual(len(cands), 1)
        self.assertTrue(cands[0]["usable"])


# ---------------------------------------------------------------- fix 1c
# Identical-retry fail-fast.

class TestIdenticalRetryFailFast(unittest.TestCase):
    EQ = "1²+67+8=76"

    def test_second_identical_attempt_fails_fast(self):
        # Iter-23 production shape: every retry got 0 candidates
        # in the same row state. The second identical attempt
        # must NOT re-run the ladder.
        page = _FakeIter24Page([[], [], []])
        with contextlib.redirect_stdout(io.StringIO()):
            _run(NS.type_equation(page, self.EQ))
        clicks_after_first = page.click_calls
        self.assertGreater(clicks_after_first, 0)
        cand_after_first = page.candidate_calls
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _run(NS.type_equation(page, self.EQ))
        out = buf.getvalue()
        self.assertIn("identical-retry fail-fast", out)
        self.assertIn("fail-fast confirmed", out)
        # No further clicks: the ladder was skipped.
        self.assertEqual(page.click_calls, clicks_after_first)
        # ...but the retry still re-probed (cheaply) — the
        # fingerprint probe is the re-probe.
        self.assertGreater(page.candidate_calls, cand_after_first)

    def test_third_identical_attempt_still_fails_fast(self):
        page = _FakeIter24Page([[], [], []])
        with contextlib.redirect_stdout(io.StringIO()):
            for _ in range(3):
                _run(NS.type_equation(page, self.EQ))
        # First attempt ran the ladder (4 js-clicks); attempts
        # 2–3 added none.
        self.assertEqual(page.click_calls, 4)

    def test_different_equation_is_not_identical(self):
        # The fingerprint is keyed by (equation, char): a
        # different equation is a new attempt, not a retry.
        page = _FakeIter24Page([[], [], []])
        with contextlib.redirect_stdout(io.StringIO()):
            _run(NS.type_equation(page, self.EQ))
        clicks_after_first = page.click_calls
        with contextlib.redirect_stdout(io.StringIO()):
            _run(NS.type_equation(page, "2²+11+8=76"))
        self.assertGreater(page.click_calls, clicks_after_first)

    def test_changed_candidates_proceed(self):
        # Button reappears between attempts -> fingerprint
        # differs -> the retry is NOT identical and proceeds.
        page = _FakeIter24Page([[]])
        _run(NS._record_probe_failure(page, self.EQ, "²", 10, []))
        page._candidate_seq = [[_USABLE_CAND]]
        page._candidate_calls = 0
        self.assertFalse(_run(NS._probe_failure_is_identical(
            page, self.EQ, "²", 10)))

    def test_changed_row_proceeds(self):
        # Row changed between attempts -> not identical.
        page = _FakeIter24Page([[]], row_text="1")
        _run(NS._record_probe_failure(page, self.EQ, "²", 10, []))
        page.row_text = "12"
        self.assertFalse(_run(NS._probe_failure_is_identical(
            page, self.EQ, "²", 10)))

    def test_same_fingerprint_is_identical(self):
        page = _FakeIter24Page([[]], row_text="1")
        _run(NS._record_probe_failure(page, self.EQ, "²", 10, []))
        self.assertTrue(_run(NS._probe_failure_is_identical(
            page, self.EQ, "²", 10)))

    def test_no_record_means_not_identical(self):
        page = _FakeIter24Page([[]])
        self.assertFalse(_run(NS._probe_failure_is_identical(
            page, self.EQ, "²", 10)))

    def test_fingerprint_pure(self):
        fp1 = NS._probe_fingerprint([_USABLE_CAND], "1")
        fp2 = NS._probe_fingerprint([dict(_USABLE_CAND)], "1")
        self.assertEqual(fp1, fp2)
        self.assertNotEqual(
            fp1, NS._probe_fingerprint([], "1"))
        self.assertNotEqual(
            fp1, NS._probe_fingerprint([_USABLE_CAND], "12"))

    def test_recovery_runs_after_focus(self):
        # The recovery re-entry now runs AFTER focus_grid: the
        # ladder's clicks all fail unfocused, the dump finds
        # the button, focus runs, the re-probed click delivers.
        page = _FakeIter24Page([[_USABLE_CAND]],
                               click_mode="succeed-after-focus")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _run(NS.type_equation(page, self.EQ))
        out = buf.getvalue()
        self.assertIn("RECOVERED via re-probed js-click", out)
        self.assertTrue(page.entered)
        # Focus actually happened before the recovery click.
        self.assertTrue(page.mouse.clicked)


# ---------------------------------------------------------------- fix 2
# Speed grant path: zero Next-clicks.

class _FakeSpeedPage:
    """Page for the Speed grant guard: advance present/absent
    scripted; records every advance-CLICK evaluation."""

    def __init__(self, advance_popup=True):
        self.advance_popup = advance_popup
        self.click_js_calls = 0
        self.advance_probe_calls = 0

    async def wait_for_timeout(self, _ms):
        return None

    async def evaluate(self, js, *args):
        s = js if isinstance(js, str) else ""
        if "SPEED_ADVANCE_CLICK" in s:
            self.click_js_calls += 1
            return "Next up ..."
        if "SPEED_ADVANCE" in s:
            self.advance_probe_calls += 1
            return {"popup": self.advance_popup,
                    "next_label": "Next up ..." if self.advance_popup
                    else None}
        return None


def _grant_state():
    # One grid, fully-green row: solved + 1 green row.
    return [{"rows": [[2] * 8], "texts": ["2+10-6=6"]}]


class TestSpeedZeroNextClicks(unittest.TestCase):
    def test_grant_guard_true_when_grant_holds(self):
        page = _FakeSpeedPage(advance_popup=True)
        ok = _run(NS._speed_grant_already_verified(
            page, 8, _grant_state(), ["2+10-6=6"], ["2+10-6=6"]))
        self.assertTrue(ok)
        # The guard is a read-only probe: zero advance clicks.
        self.assertEqual(page.click_js_calls, 0)

    def test_grant_path_performs_zero_next_clicks(self):
        # The exact iter-23 regression shape: win verified via
        # the grant, then the old code called _speed_try_advance
        # (one Next click -> hub). The guarded path claims the
        # win and never calls the advance.
        page = _FakeSpeedPage(advance_popup=True)
        if _run(NS._speed_grant_already_verified(
                page, 8, _grant_state(), ["2+10-6=6"],
                ["2+10-6=6"])):
            signal = "won"  # claim + STOP, no advance call
        else:
            _run(NS._speed_try_advance(page, 8, _grant_state()))
            signal = "advanced?"
        self.assertEqual(signal, "won")
        self.assertEqual(page.click_js_calls, 0)

    def test_grant_guard_false_without_advance(self):
        # Advance not offered yet -> not the grant path; the
        # caller may still advance (rescue path, unchanged).
        page = _FakeSpeedPage(advance_popup=False)
        ok = _run(NS._speed_grant_already_verified(
            page, 8, _grant_state(), ["2+10-6=6"], ["2+10-6=6"]))
        self.assertFalse(ok)
        self.assertEqual(page.click_js_calls, 0)

    def test_grant_guard_false_without_coverage(self):
        page = _FakeSpeedPage(advance_popup=True)
        ok = _run(NS._speed_grant_already_verified(
            page, 8, _grant_state(), [], ["2+10-6=6"]))
        self.assertFalse(ok)

    def test_play_mode_grant_guard_precedes_advance(self):
        # Wiring: in play_mode's loop-top verified branch, the
        # grant guard (claim + break) sits BEFORE the
        # _speed_try_advance call.
        start = SRC_NERDLE.index("async def play_mode")
        pm = SRC_NERDLE[start:]
        g = pm.index("_speed_grant_already_verified(")
        a = pm.index("_speed_try_advance(", g)
        between = pm[g:a]
        self.assertIn('signal = "won"', between)
        self.assertIn("break", between)
        self.assertIn("WITHOUT clicking Next", between)

    def test_advance_still_available_off_grant_path(self):
        # _speed_try_advance is kept for wins verified WITHOUT
        # the grant (a 2nd board may hide behind Next there).
        self.assertIn("async def _speed_try_advance", SRC_NERDLE)
        page = _FakeSpeedPage(advance_popup=False)
        outcome, _st = _run(NS._speed_try_advance(
            page, 8, _grant_state()))
        self.assertEqual(outcome, "no-popup")
        self.assertEqual(page.click_js_calls, 0)


# ---------------------------------------------------------------- guards
# Things iter-24 must NOT change.

class TestIter24Guards(unittest.TestCase):
    def test_typing_speed_unchanged(self):
        self.assertIn("async def type_equation(page, eq, delay=200)",
                      SRC_NERDLE)

    def test_kept_constants_and_gates_untouched(self):
        self.assertEqual(NS.NERDLE_SITE_BOUNDARY_UTC, (16, 30))
        self.assertIn("_popupTrip", SRC_NERDLE)
        self.assertIn("site-solutions == official", SRC_NERDLE)
        self.assertIn("def _speed_grant_ready", SRC_NERDLE)

    def test_prepurge_before_uselocaltime_setter(self):
        self.assertLess(SRC_NERDLE.index("prepurge_js()"),
                        SRC_NERDLE.index(
                            "setItem('useLocalTime','true')"))

    def test_speed_grant_predicate_unchanged(self):
        self.assertTrue(NS._speed_grant_ready(1, True, True, True))
        self.assertFalse(NS._speed_grant_ready(0, True, True, True))
        self.assertFalse(NS._speed_grant_ready(1, False, True, True))
        self.assertFalse(NS._speed_grant_ready(1, True, True, False))

    def test_single_entry_and_row_verify_kept(self):
        # ² single-entry rule + pre-Enter full-row verification
        # are untouched by the matcher/fail-fast changes.
        self.assertIn("single-entry", SRC_NERDLE)
        self.assertIn("_verify_row_or_retype", SRC_NERDLE)
        self.assertIn("pre-Enter row check", SRC_NERDLE)


if __name__ == "__main__":
    unittest.main()
