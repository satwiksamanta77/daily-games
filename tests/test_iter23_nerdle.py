"""Fix-loop iteration 23 tests — Nerdle workstream.

Evidence-grounded in ANALYSIS20 (run 39089028607):

FIX 1 — Maxi ² REGRESSION (won iter-21 -> unwon iter-22, ANALYSIS20 §3).
The iter-21 -> iter-22 diff for nerdle_solver.py contains ZERO
changes to the ² lookup / ladder-cap code (verified by git diff:
no POWER / ladder / CLICK_POWER hunks), so the regression is
inherent flakiness of the old path, not a changed selector:
  (a) guess 4's ²-button lookup returned 0 candidates after the
      same lookup found the button on guess 3; the exact selector
      + DOM/keyboard state were never logged on the 0-candidate
      failure (`power key '²' candidates (0): []` only);
  (b) the iter-21 ladder cap then blocked retype, site-solution
      retry and entry retries, turning one transient failure into
      permanent mode loss;
  (c) the solver reported `remaining: 0` although the true
      solution was still playable.
Now: the lookup itself is re-probed before 0 candidates is
declared; a persistent 0-candidate result logs the selector +
DOM/keyboard state and is treated as a PROBE failure (cap NOT
engaged, so retries may re-probe); the cap engages only when
the re-probed lookup finds the button but all entry methods
fail; a solver-empty result falls back to the official equation
and then to an unplayed site solution (pool-drain extended to
single-board modes).

FIX 2 — solved=true with Maxi unwon (ANALYSIS20 §1, honesty).
Iter-22 shipped solved=true / QC ok=true with Maxi unwon
(signal '', unproven_modes=[]), cut the Maxi chapter with NO
on-video disclosure. Now the verdict (``_nerdle_verdict``)
requires ALL modes won: any signal != "won" (including ''
and a missing mode) makes solved False, lands in
unproven_modes, and is named in the result.json evidence.
The old "ship with one cut mode" rule is gone.

All tests are synthetic/local: no network, no CI run.
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


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------- fix 1
# Fake board page with a scripted power-key lookup / click / state.

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


class _FakeProbePage:
    """Board where the ² lookup / click behave per script.

    candidate_seq: candidate lists returned by successive
    POWER_BTN_CANDIDATES evaluations (last value repeats).
    click_mode: "always-false" (entry genuinely fails) or
    "succeed-after-ladder" (the first 4 js-click attempts —
    type_equation's ladder — fail, a later re-probed click
    succeeds, modelling a keyboard that renders late).
    """

    def __init__(self, candidate_seq, click_mode="always-false"):
        self.keyboard = _FakeKeyboard()
        self.mouse = _FakeMouse()
        self._candidate_seq = [list(c) for c in candidate_seq]
        self._candidate_calls = 0
        self.candidate_calls = 0
        self.click_calls = 0
        self.click_mode = click_mode
        self.entered = False
        self.keyboard_state_calls = 0

    async def wait_for_timeout(self, _ms):
        return None

    async def evaluate(self, js, *args):
        s = js if isinstance(js, str) else ""
        if "POWER_KEYBOARD_STATE" in s:
            self.keyboard_state_calls += 1
            return {"selector": "document.querySelectorAll('button')",
                    "buttons_total": 0, "keyboard_containers": 0,
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
            if self.click_mode == "succeed-after-ladder" \
                    and self.click_calls > 4:
                self.entered = True
                return True
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


class TestProbeFailureDoesNotCap(unittest.TestCase):
    EQ = "1²+67+8=76"

    def test_zero_candidates_logs_selector_and_state_and_no_cap(self):
        # Iter-22 shape: lookup finds nothing on every re-probe.
        page = _FakeProbePage([[], [], []])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _run(NS.type_equation(page, self.EQ))
        out = buf.getvalue()
        self.assertIn("FAILED after all entry methods", out)
        self.assertIn("candidates (0):", out)
        # The selector + DOM/keyboard state are now logged.
        self.assertIn("selector=", out)
        self.assertIn("keyboard_state=", out)
        self.assertEqual(page.keyboard_state_calls, 1)
        # Lookup was re-probed (more than one candidate query).
        self.assertGreaterEqual(page.candidate_calls, 2)
        # PROBE failure: the cap must NOT engage.
        self.assertIn("PROBE failure", out)
        self.assertIn("ladder cap NOT engaged", out)
        self.assertNotIn(self.EQ, NS._ladder_failed_set(page))

    def test_second_entry_retries_ladder_after_probe_failure(self):
        # Because the cap did not engage, a second entry attempt
        # re-runs the ladder (re-probes the button) instead of
        # failing fast — the iter-22 retries were all SKIPPED.
        page = _FakeProbePage([[], [], []])
        with contextlib.redirect_stdout(io.StringIO()):
            _run(NS.type_equation(page, self.EQ))
        clicks_after_first = page.click_calls
        self.assertGreater(clicks_after_first, 0)
        with contextlib.redirect_stdout(io.StringIO()):
            _run(NS.type_equation(page, self.EQ))
        self.assertGreater(page.click_calls, clicks_after_first)


class TestLookupReprobe(unittest.TestCase):
    EQ = "1²+67+8=76"

    def test_reprobe_finds_button_after_empty_first_lookup(self):
        # The lookup itself is retried: first read empty (keyboard
        # mid-render), second read finds the usable button.
        page = _FakeProbePage([[], [_USABLE_CAND]])
        cands = _run(NS._dump_power_candidates(page, "²"))
        self.assertEqual(len(cands), 1)
        self.assertTrue(cands[0]["usable"])
        self.assertEqual(page.candidate_calls, 2)

    def test_reprobe_click_recovers_entry(self):
        # Full type_equation, iter-22 guess-4 shape with a late
        # keyboard: the ladder's methods all fail while the button
        # is absent, the re-probed lookup finds it, and one
        # bounded re-probed js-click delivers the ².
        page = _FakeProbePage([[], [_USABLE_CAND]],
                              click_mode="succeed-after-ladder")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _run(NS.type_equation(page, self.EQ))
        out = buf.getvalue()
        self.assertIn("RECOVERED via re-probed js-click", out)
        self.assertTrue(page.entered)
        self.assertNotIn(self.EQ, NS._ladder_failed_set(page))

    def test_entry_failure_with_button_present_still_caps(self):
        # The iter-21 cap behaviour is preserved for GENUINE
        # entry failures: button present from the start, all
        # methods fail to deliver -> cap engages.
        page = _FakeProbePage([[_USABLE_CAND]])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _run(NS.type_equation(page, self.EQ))
        out = buf.getvalue()
        self.assertIn("FAILED after all entry methods", out)
        self.assertIn("ladder cap engaged", out)
        self.assertIn(self.EQ, NS._ladder_failed_set(page))


# ---------------------------------------------------------------- fix 1
# Solver-empty fallback (pure helper + wiring).

class TestSolverEmptyFallback(unittest.TestCase):
    def test_official_fallback_when_solver_empty(self):
        nxt, i = NS._solver_empty_fallback(
            ["1²+67+8=76"], 0, ["71-2+14=83"], ["1²+67+8=76"],
            ["71-2+14=83"])
        self.assertEqual(nxt, "1²+67+8=76")
        self.assertEqual(i, 1)

    def test_site_solution_fallback_when_official_exhausted(self):
        # Solver remaining: 0 AND official already played -> the
        # unplayed site solution is still attempted, not a break.
        nxt, i = NS._solver_empty_fallback(
            ["1²+67+8=76"], 1, ["1²+67+8=76", "71-2+14=83"],
            ["1²+67+8=76", "19+7²+8=76"],
            ["1²+67+8=76", "71-2+14=83"])
        self.assertEqual(nxt, "19+7²+8=76")

    def test_single_board_pool_drain(self):
        # Maxi is single-board: the old boards_n > 1 pool-drain
        # would have abandoned it here. The helper has no
        # boards_n condition — pool fallback fires regardless.
        nxt, _i = NS._solver_empty_fallback(
            [], 0, ["71-2+14=83"], ["1²+67+8=76"],
            ["71-2+14=83"])
        self.assertEqual(nxt, "1²+67+8=76")

    def test_none_when_everything_played(self):
        nxt, _i = NS._solver_empty_fallback(
            ["1²+67+8=76"], 1, ["1²+67+8=76"], ["1²+67+8=76"],
            ["1²+67+8=76"])
        self.assertIsNone(nxt)

    def test_play_mode_wires_fallback_and_pool_drain(self):
        start = SRC_NERDLE.index("async def play_mode")
        end = SRC_NERDLE.index("async def main", start)
        pm = SRC_NERDLE[start:end]
        self.assertIn("_solver_empty_fallback(", pm)
        # The pool-drain backstop no longer requires boards_n > 1.
        self.assertNotIn("boards_n > 1 and _unplayed", pm)


# ---------------------------------------------------------------- fix 2
# Verdict honesty (pure helper + main wiring).

def _results_for(won_ids, empty_signal_ids=()):
    out = []
    for m in NS.NERDLE_MODES:
        mid = m["id"]
        if mid in won_ids:
            out.append((mid, 1, 1, "won"))
        elif mid in empty_signal_ids:
            out.append((mid, 3, 1, ""))
        else:
            out.append((mid, 3, 1, ""))
    return out


class TestNerdleVerdictHonesty(unittest.TestCase):
    def test_8_of_9_maxi_unwon_is_not_solved(self):
        # The exact iter-22 shape: 8 won, maxi signal ''.
        won = [m["id"] for m in NS.NERDLE_MODES if m["id"] != "maxi"]
        all_won, unproven, evidence = NS._nerdle_verdict(
            _results_for(won, empty_signal_ids=("maxi",)))
        self.assertFalse(all_won)
        self.assertEqual(unproven, ["maxi"])
        self.assertIn("maxi", evidence)
        self.assertIn("NOT solved", evidence)

    def test_9_of_9_is_solved(self):
        won = [m["id"] for m in NS.NERDLE_MODES]
        all_won, unproven, evidence = NS._nerdle_verdict(
            _results_for(won))
        self.assertTrue(all_won)
        self.assertEqual(unproven, [])
        self.assertIn("9/9", evidence)

    def test_empty_signal_counts_unwon(self):
        won = [m["id"] for m in NS.NERDLE_MODES if m["id"] != "speed"]
        all_won, unproven, _e = NS._nerdle_verdict(
            _results_for(won, empty_signal_ids=("speed",)))
        self.assertFalse(all_won)
        self.assertIn("speed", unproven)

    def test_missing_mode_counts_unwon(self):
        # A mode absent from results entirely (never ran) is
        # unwon, not silently absent.
        results = [(m["id"], 1, 1, "won") for m in NS.NERDLE_MODES
                   if m["id"] != "maxi"]
        all_won, unproven, _e = NS._nerdle_verdict(results)
        self.assertFalse(all_won)
        self.assertEqual(unproven, ["maxi"])

    def test_unproven_signal_counts_unwon(self):
        results = _results_for(
            [m["id"] for m in NS.NERDLE_MODES if m["id"] != "maxi"])
        results = [(mid, t, e, ("unproven" if mid == "maxi" else s))
                   for mid, t, e, s in results]
        all_won, unproven, _e = NS._nerdle_verdict(results)
        self.assertFalse(all_won)
        self.assertIn("maxi", unproven)

    def test_main_no_longer_ships_with_cut_mode(self):
        start = SRC_NERDLE.index("async def main")
        main_src = SRC_NERDLE[start:]
        self.assertNotIn("shipping with cut modes", main_src)
        self.assertNotIn(">= len(NERDLE_MODES) - 1", main_src)
        self.assertIn("_nerdle_verdict(", main_src)
        self.assertIn('"evidence": _verdict_evidence', main_src)
        # The verdict feeds solved: all_solved requires _all_won.
        self.assertIn("all_solved = _all_won and not _wd_blocked",
                      main_src)


# ---------------------------------------------------------------- guards
# Things iter-23 must NOT change (ANALYSIS20 carry-forwards).

class TestIter23Guards(unittest.TestCase):
    def test_typing_speed_unchanged(self):
        # type_equation's plain-typing delay is still 200ms;
        # no pacing value in the ² path changed.
        self.assertIn("async def type_equation(page, eq, delay=200)",
                      SRC_NERDLE)

    def test_kept_constants_and_gates_untouched(self):
        self.assertEqual(NS.NERDLE_SITE_BOUNDARY_UTC, (16, 30))
        # _popupTrip header probe, mini site-solutions gate,
        # Speed grant predicate all still present.
        self.assertIn("_popupTrip", SRC_NERDLE)
        self.assertIn("site-solutions == official", SRC_NERDLE)
        self.assertIn("def _speed_grant_ready", SRC_NERDLE)

    def test_speed_grant_still_works(self):
        # The iter-22 PROVEN Speed verdict model is untouched:
        # single green row + coverage + solved + advance grants.
        self.assertTrue(NS._speed_grant_ready(1, True, True, True))
        self.assertFalse(NS._speed_grant_ready(0, True, True, True))
        self.assertFalse(NS._speed_grant_ready(1, False, True, True))
        self.assertFalse(NS._speed_grant_ready(1, True, True, False))


if __name__ == "__main__":
    unittest.main()
