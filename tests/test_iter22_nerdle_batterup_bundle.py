"""Fix-loop iteration 22 tests — Nerdle (Speed) + Batterup + Bundle.

Evidence-grounded in ANALYSIS19 (run 39085638866):

1. Nerdle Speed verdict model (P0, §3): `green_rows >= 2` was
   UNREACHABLE (the starter row `2+10-6=6` arrives SUBMITTED as a
   guess with mixed feedback, never all-green — only ONE row can
   ever be green); the site never shows a win popup the verdict
   probe can rely on (iter-21: popup=False at the verdict, then
   the advance probe found popup + 'Next up ...' moments later —
   a settle race); and the bot's own Next-click navigated to the
   hub, so the final re-read ran against the hub page
   (green_rows 1->0, all_solved True->False). Now: the grant
   signal is single-green-row + seeded coverage + advance
   offered (never 2 green rows, never a bare popup demand);
   the verdict is computed BEFORE any Next-click; a
   navigated-away advance keeps the PRE-advance verdict and is
   never re-read as verdict evidence.
2. Restore gate (P0, §3): `restore gate on load: keys=[]
   guesses=0` + `gameState cleared: none present`, yet the load
   dump seconds later showed gameState WITH guesses — the
   restore/clear ran before site hydration. Now both probes are
   post-hydration (grid render + settle), and a re-hydrated
   gameState after a clear+reload is logged as site-provided
   seeded state, never claimed as an empty board.
3. Speed board-1 site-rules (P1, §3): site board-1 `50-6*7=8`
   vs API `711/9=79` (no served board corresponds). Speed
   board-1 is site-rules like micro/minibi/quad/instant; an API
   match is NOT required for it.
4. Batterup publish check (P1, §4): the 4-line defer log had
   ZERO fetch evidence. Now every fetch logs URL + status +
   body fingerprint + server timestamp + carried game_dates,
   and the defer message embeds that evidence. The defer
   decision itself is unchanged (safe direction).
5. Bundle finals-only (P1, §5): the bundle packed raw
   `gameplay_1080.mp4` for all 16 games; Nerdle's raw (275.6 s
   vs 230.3 s final) still contains the cut Speed chapter.
   Now the pack ships `*_final_*.mp4` ONLY — raw gameplay is
   excluded from every per-game dir in the staging copy.
   Per-game artifacts still hold raw + final.
6. Nerdle recording wart (P2, §2): `equations_played` recorded
   the DOM-flattened `12+67+8=76` for the maxi win, and the
   pre-Enter line printed "matches" for textually different
   strings. Now the game-evaluated `1²+67+8=76` form is
   recorded, the flattened form survives only as a footnote,
   and the pre-Enter line says "normalized-match" for a
   normalizing comparison.

All tests are synthetic/local: no network (Batterup fetches
are mocked), no CI run. Typing speed unchanged (no pacing
value touched anywhere in this iteration).
"""
import asyncio
import contextlib
import io
import itertools
import json
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

import nerdle_solver as NS
import answers as ANS

SRC_NERDLE = (Path(__file__).parent.parent
              / "nerdle_solver.py").read_text(encoding="utf-8")
WF = (Path(__file__).parent.parent / ".github" / "workflows"
      / "daily-all-videos.yml").read_text(encoding="utf-8")


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------- fix 1a
# Speed grant predicate (pure) + verified check (fake page).

class TestSpeedGrantPredicate(unittest.TestCase):
    def test_grant_ready_iter21_shape(self):
        # ANALYSIS19 §3 after guess 2: 1 green row, coverage True,
        # all grids solved, advance offered -> GRANTED.
        self.assertTrue(NS._speed_grant_ready(1, True, True, True))

    def test_no_green_row_never_grants(self):
        self.assertFalse(NS._speed_grant_ready(0, True, True, True))

    def test_no_coverage_never_grants(self):
        self.assertFalse(NS._speed_grant_ready(1, False, True, True))

    def test_unsolved_grid_never_grants(self):
        self.assertFalse(NS._speed_grant_ready(1, True, False, True))

    def test_no_advance_never_grants(self):
        # The advance offer is part of the grant: a solved board
        # with no advance signal is NOT a grant (the popup path
        # may still verify it — the grant path must not).
        self.assertFalse(NS._speed_grant_ready(1, True, True, False))

    def test_two_green_rows_also_grant(self):
        # >= 1, not == 1: a board that genuinely shows 2 green
        # rows (legacy path (a) territory) also satisfies the
        # grant predicate.
        self.assertTrue(NS._speed_grant_ready(2, True, True, True))


class _FakeGrantPage:
    """Fake for _check_win_verified / _speed_try_advance.

    win_js: WIN_JS result. popup: WIN_POPUP_JS result.
    advance: dict returned by the SPEED_ADVANCE probe, or a list
    of dicts (one per probe, to simulate the popup animating in
    late). Records every evaluate call for the no-click
    assertion."""

    def __init__(self, win_js=False, popup=False, advance=None,
                 url="https://speed.nerdlegame.com/"):
        self.win_js = win_js
        self.popup = popup
        self._advance = advance
        self.url = url
        self.evals = []
        self.advance_probes = 0

    async def wait_for_timeout(self, _ms):
        return None

    async def evaluate(self, js, *args):
        s = js if isinstance(js, str) else ""
        self.evals.append(s)
        if "=== label" in s:
            # SPEED_ADVANCE_CLICK_JS — the only click evaluation.
            return args[0] if args else None
        if "next_label" in s:
            self.advance_probes += 1
            adv = self._advance
            if isinstance(adv, list):
                idx = min(self.advance_probes - 1, len(adv) - 1)
                return dict(adv[idx])
            if isinstance(adv, dict):
                return dict(adv)
            return {"popup": False, "next_label": None}
        if "SPEED_WIN_POPUP" in s:
            return self.popup
        if "boards <= 1" in s:
            return self.win_js
        return None


def _speed_state_iter21():
    # Iter-21 after guess 2: row 1 = starter (mixed feedback,
    # never green), row 2 = played solution (all green).
    return [{"rows": [[1, 0, 1, 0, 1, 0, 1, 0], [2] * 8],
             "texts": ["2+10-6=6", "50-6*7=8"]}]


class TestSpeedGrantVerified(unittest.TestCase):
    POOL = ["50-6*7=8", "2+10-6=6"]
    PLAYED = ["2+10-6=6", "50-6*7=8"]

    def test_iter21_shape_verifies_via_grant(self):
        # The exact iter-21 UNVERIFIED line (green_rows=1,
        # covered=True, popup=False, all_solved=True) now
        # VERIFIES via the grant path once the advance is
        # offered — no 2-green-row demand, no popup demand.
        page = _FakeGrantPage(
            win_js=False, popup=False,
            advance={"popup": True, "next_label": "Next up ..."})
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ok = _run(NS._check_win_verified(
                page, 8, 2, _speed_state_iter21(),
                list(self.PLAYED), list(self.POOL), mode_id="speed"))
        self.assertTrue(ok)
        self.assertIn("game grant", buf.getvalue())

    def test_verdict_computed_before_any_next_click(self):
        # The verdict path must NEVER evaluate the click JS:
        # _check_win_verified / _speed_grant_verified are
        # read-only (evaluate), never a click.
        page = _FakeGrantPage(
            win_js=False, popup=False,
            advance={"popup": True, "next_label": "Next up ..."})
        with contextlib.redirect_stdout(io.StringIO()):
            ok = _run(NS._check_win_verified(
                page, 8, 2, _speed_state_iter21(),
                list(self.PLAYED), list(self.POOL), mode_id="speed"))
        self.assertTrue(ok)
        self.assertFalse(any("=== label" in e for e in page.evals),
                         "verdict must not evaluate the Next-click JS")

    def test_grant_fails_without_advance(self):
        # Same board, no advance offered -> NOT verified via
        # grant (and popup=False, so path (b) also fails).
        # The UNVERIFIED line must show the advance state.
        page = _FakeGrantPage(
            win_js=False, popup=False,
            advance={"popup": False, "next_label": None})
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ok = _run(NS._check_win_verified(
                page, 8, 2, _speed_state_iter21(),
                list(self.PLAYED), list(self.POOL), mode_id="speed"))
        self.assertFalse(ok)
        self.assertIn("advance_next=None", buf.getvalue())

    def test_grant_fails_without_coverage(self):
        page = _FakeGrantPage(
            win_js=False, popup=False,
            advance={"popup": True, "next_label": "Next up ..."})
        with contextlib.redirect_stdout(io.StringIO()):
            ok = _run(NS._check_win_verified(
                page, 8, 2, _speed_state_iter21(),
                ["2+10-6=6"], list(self.POOL), mode_id="speed"))
        self.assertFalse(ok)

    def test_grant_fails_with_unsolved_second_grid(self):
        # A green row on board 1 must NOT verify while a board 2
        # grid is still unsolved (the advance-rescue case).
        page = _FakeGrantPage(
            win_js=False, popup=False,
            advance={"popup": True, "next_label": "Next up ..."})
        state = _speed_state_iter21() + [
            {"rows": [[-1] * 8], "texts": [""]}]
        with contextlib.redirect_stdout(io.StringIO()):
            ok = _run(NS._check_win_verified(
                page, 8, 2, state,
                list(self.PLAYED), list(self.POOL), mode_id="speed"))
        self.assertFalse(ok)

    def test_settle_reprobe_catches_late_popup(self):
        # The iter-21 race: popup=False at the first probe, then
        # the advance probe finds popup + Next moments later.
        # The grant's settle re-probe catches it.
        page = _FakeGrantPage(
            win_js=False, popup=False,
            advance=[{"popup": False, "next_label": None},
                     {"popup": True, "next_label": "Next up ..."}])
        with contextlib.redirect_stdout(io.StringIO()):
            ok = _run(NS._check_win_verified(
                page, 8, 2, _speed_state_iter21(),
                list(self.PLAYED), list(self.POOL), mode_id="speed"))
        self.assertTrue(ok)
        self.assertGreaterEqual(page.advance_probes, 2)


# ---------------------------------------------------------------- fix 1b
# Advance navigates away -> pre-advance verdict stands.

class TestSpeedAdvanceNavigatedAway(unittest.TestCase):
    def test_advance_reports_navigated_away_on_hub(self):
        # Clicking Next navigates speed.nerdlegame.com -> the
        # hub (www.nerdlegame.com) with no grids: outcome is
        # "navigated-away", NOT "no-new-board".
        page = _FakeGrantPage(
            advance={"popup": True, "next_label": "Next up ..."},
            url="https://speed.nerdlegame.com/")

        async def _read_state_after_click(page, cols, tries=3):
            # read_state on the hub page: no grids.
            return []

        async def _run_advance():
            # Simulate the click's navigation: the click JS
            # evaluation changes the URL + empties the board.
            orig_eval = page.evaluate

            async def _eval(js, *args):
                s = js if isinstance(js, str) else ""
                if "=== label" in s:
                    page.url = ("https://www.nerdlegame.com/"
                                "?gp=2026-10-17,classic,speed")
                    return args[0] if args else None
                return await orig_eval(js, *args)

            page.evaluate = _eval
            with mock.patch.object(NS, "read_state",
                                   side_effect=_read_state_after_click):
                return await NS._speed_try_advance(
                    page, 8, _speed_state_iter21())

        outcome, _ = _run(_run_advance())
        self.assertEqual(outcome, "navigated-away")

    def test_advance_still_on_page_is_no_new_board(self):
        # Clicked Next, URL unchanged, no new board -> the old
        # "no-new-board" outcome (still on the game page).
        page = _FakeGrantPage(
            advance={"popup": True, "next_label": "Next up ..."})

        async def _read_state_same(page, cols, tries=3):
            return _speed_state_iter21()

        with mock.patch.object(NS, "read_state",
                               side_effect=_read_state_same):
            outcome, _ = _run(NS._speed_try_advance(
                page, 8, _speed_state_iter21()))
        self.assertEqual(outcome, "no-new-board")


# ---------------------------------------------------------------- fix 1b
# play_mode end-to-end: pre-advance verdict stands.

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
    """Playwright stand-in for the speed page (iter-21 shape):
    ONE grid, row 1 = SUBMITTED starter '2+10-6=6' (mixed
    feedback, never green). `popup_after`: the win popup +
    Next button appear once this many _check_win_verified
    calls (WIN_JS evaluations) have run — simulating the popup
    animating in during the sweep between the loop-top check
    and the pre-advance re-check; 0 = present from the start,
    None = never. `navigate_on_click`: the Next click
    navigates to the hub (URL change + no grids)."""

    A = "50-6*7=8"
    B = "2+10-6=6"
    HUB = "https://www.nerdlegame.com/?gp=2026-10-17,classic,speed"

    def __init__(self, popup_after=0, navigate_on_click=False,
                 pool=None):
        self.url = "https://speed.nerdlegame.com/"
        self.frames = []
        self.keyboard = _FakeSpeedKeyboard(self)
        self.mouse = _FakeSpeedMouse()
        self._active = 0
        self.popup_after = popup_after
        self.navigate_on_click = navigate_on_click
        self.pool = pool if pool is not None else [self.A, self.B]
        self.grids = [self._grid_with_starter(self.A)]
        self.screenshots = []
        self.advance_clicks = 0
        self.win_js_calls = 0

    @staticmethod
    def _grid_with_starter(solution):
        rows = [[1, 0, 1, 0, 1, 0, 1, 0]] + [[-1] * 8 for _ in range(5)]
        texts = ["2+10-6=6"] + [""] * 5
        return {"solution": solution, "rows": rows, "texts": texts}

    def _active_grid(self):
        if 0 <= self._active < len(self.grids):
            return self.grids[self._active]
        return None

    def _popup_present(self):
        if self.popup_after is None:
            return False
        return self.win_js_calls > self.popup_after

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

    async def wait_for_selector(self, sel, timeout=None):
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
        if "=== label" in s:
            # The Next-button click.
            self.advance_clicks += 1
            if self.navigate_on_click:
                self.url = self.HUB
                self.grids = []
            return args[0] if args else None
        if "next_label" in s:
            if self._popup_present():
                return {"popup": True,
                        "next_label": "Next up ..."}
            return {"popup": False, "next_label": None}
        if "SPEED_WIN_POPUP" in s:
            return self._popup_present()
        if "out.keys" in s:  # restore probe
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
        if "boards <= 1" in s:  # WIN_JS: green rows across grids
            self.win_js_calls += 1
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


class TestSpeedPlayModePreAdvanceVerdict(unittest.TestCase):
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
        self._old_notes = dict(NS.PLAYED_NOTES)
        NS.PLAYED.clear()
        NS.SERVED.clear()
        NS.PLAYED_NOTES.clear()
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
        NS.PLAYED_NOTES.clear()
        NS.PLAYED_NOTES.update(self._old_notes)

    def test_pre_advance_recheck_claims_win_without_click(self):
        # The iter-21 shape with the popup animating in during
        # the sweep: loop-top verdict races it (popup not yet
        # visible), but the pre-advance re-check in the
        # no-unsolved branch sees the grant and claims the win
        # WITHOUT clicking Next (advance_clicks == 0).
        # popup_after=4: the popup appears on the 5th
        # _check_win_verified call — the pre-advance re-check —
        # after 4 loop-top checks saw no popup (iterations 0-3;
        # iteration 3 is the first grant-ready board).
        page = _FakeSpeedPage(popup_after=4, navigate_on_click=True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            typed, expected, signal = _run(
                NS.play_mode(page, self.MODE, ".",
                             official=dict(self.OFFICIAL)))
        out = buf.getvalue()
        self.assertEqual(signal, "won")
        self.assertEqual(page.advance_clicks, 0)
        self.assertIn("game grant", out)
        self.assertEqual(page.url, "https://speed.nerdlegame.com/")

    def test_advance_to_hub_keeps_pre_advance_verdict(self):
        # Iter-24 (ANALYSIS21 carry-forward) SUPERSEDES the old
        # contract: a grant-verified win must be claimed WITHOUT
        # clicking Next (zero-Next-clicks rule). The old test
        # asserted advance_clicks==1 + hub nav; the new guard
        # claims the win and breaks before _speed_try_advance.
        # Popup + Next present from the start: the loop-top
        # verdict verifies via the grant path, the guard claims
        # the win without advancing, and the page never navigates
        # to the hub.
        page = _FakeSpeedPage(popup_after=0, navigate_on_click=True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            typed, expected, signal = _run(
                NS.play_mode(page, self.MODE, ".",
                             official=dict(self.OFFICIAL)))
        out = buf.getvalue()
        self.assertEqual(signal, "won")
        self.assertEqual(page.advance_clicks, 0)
        self.assertNotEqual(page.url, _FakeSpeedPage.HUB)
        self.assertIn("WITHOUT clicking Next", out)


# ---------------------------------------------------------------- fix 1c
# Restore gate reads post-hydration state.

class _FakeRestorePage:
    """Fake for _nerdle_fresh_board: the restore probe reports
    keys=[] BEFORE hydration (grid not yet rendered) and the
    site's seeded gameState AFTER hydration."""

    def __init__(self, seeded=True):
        self.hydrated = False
        self.seeded = seeded
        self.goto_calls = 0

    async def wait_for_selector(self, sel, timeout=None):
        self.hydrated = True
        return None

    async def wait_for_timeout(self, _ms):
        return None

    async def goto(self, url, **kw):
        self.goto_calls += 1
        self.hydrated = False  # reload re-boots
        return None

    async def evaluate(self, js, *args):
        s = js if isinstance(js, str) else ""
        if "out.keys" in s:
            if self.hydrated and self.seeded:
                return {"keys": ["gameState"], "guesses": 1,
                        "hasSolution": True, "wonText": False}
            return {"keys": [], "guesses": 0,
                    "hasSolution": False, "wonText": False}
        if "localStorage.removeItem" in s:
            return ["gameState"] if (self.hydrated and self.seeded) else []
        return None


class TestRestoreGateHydration(unittest.TestCase):
    MODE = {"id": "speed", "url": "https://speed.nerdlegame.com/"}

    def test_gate_reads_post_hydration_state(self):
        # The iter-21 lie was `restore gate on load: keys=[]
        # guesses=0` while the site had not yet hydrated. The
        # gate must now read the POST-hydration state.
        page = _FakeRestorePage(seeded=True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _run(NS._nerdle_fresh_board(page, self.MODE))
        out = buf.getvalue()
        self.assertIn("restore gate on load (post-hydration)", out)
        self.assertIn("keys=['gameState']", out)
        self.assertIn("guesses=1", out)

    def test_rehydration_logged_not_claimed_empty(self):
        # After a clear + reload the site re-seeds gameState:
        # the log must say RE-HYDRATED (site-provided seeded
        # state), never claim an empty board.
        page = _FakeRestorePage(seeded=True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _run(NS._nerdle_fresh_board(page, self.MODE))
        out = buf.getvalue()
        self.assertIn("RE-HYDRATED", out)
        self.assertIn("site-provided seeded state", out)
        self.assertIn("NOT a restore", out)

    def test_genuinely_empty_store_stays_empty(self):
        # No seeded state: the gate honestly reports empty and
        # no re-hydration note appears.
        page = _FakeRestorePage(seeded=False)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _run(NS._nerdle_fresh_board(page, self.MODE))
        out = buf.getvalue()
        self.assertIn("keys=[]", out)
        self.assertIn("gameState cleared: none present", out)
        self.assertNotIn("RE-HYDRATED", out)


# ---------------------------------------------------------------- fix 1d
# Speed board-1 is site-rules.

class TestSpeedBoard1SiteRules(unittest.TestCase):
    def test_iter21_production_shape_is_site_rules(self):
        # ANALYSIS19 §3: site board-1 '50-6*7=8', API first
        # equation '711/9=79' (no served board corresponds).
        detail, is_site_rules = NS._speed_site_rules_detail(
            ["50-6*7=8", "2+10-6=6"],
            ["711/9=79", "2+10-6=6"],
            False, "partial: 1/2 equations match API")
        self.assertTrue(is_site_rules)
        self.assertIn("site-rules board-1", detail)
        self.assertIn("API match NOT required", detail)
        self.assertIn("50-6*7=8", detail)
        self.assertIn("711/9=79", detail)
        self.assertIn("never gating", detail)

    def test_full_match_is_not_site_rules(self):
        detail, is_site_rules = NS._speed_site_rules_detail(
            ["711/9=79", "2+10-6=6"],
            ["711/9=79", "2+10-6=6"],
            True, "2/2 equations match API (both)")
        self.assertFalse(is_site_rules)
        self.assertEqual(detail, "2/2 equations match API (both)")


# ---------------------------------------------------------------- fix 2
# Batterup publish check carries fetch evidence.

class _FakeBatterupResp:
    def __init__(self, status=200, body=b"{}", server_date=None):
        self.status_code = status
        self.content = body
        self.headers = {"Date": server_date or "Sat, 18 Oct 2026 01:13:00 GMT"}
        self.ok = 200 <= status < 300
        self.text = body.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.text)


def _batterup_body(games):
    return json.dumps({"games": games}).encode("utf-8")


class TestBatterupPublishCheckEvidence(unittest.TestCase):
    DAY = date(2026, 10, 18)

    def _entry(self, game_date, name="Some Player"):
        return {"game_date": game_date, "player_name": name,
                "game_number": 1}

    def test_defer_carries_fetch_evidence(self):
        # Both CDN files fetched, neither carries the target
        # day: the defer must embed URL + status + fingerprint
        # + server timestamp + carried game_dates.
        body = _batterup_body([self._entry("2026-10-17")])
        seen_urls = []

        def _get(url, **kw):
            seen_urls.append(url)
            return _FakeBatterupResp(status=200, body=body)

        with mock.patch("requests.get", side_effect=_get):
            with self.assertRaises(SystemExit) as cm:
                ANS.batterup(self.DAY)
        msg = str(cm.exception)
        self.assertIn("not published yet", msg)
        self.assertIn("games_batterup2026-10-18.json", msg)
        self.assertIn("games_batterup2026-10-19.json", msg)
        self.assertIn("status=200", msg)
        self.assertIn("sha256=", msg)
        self.assertIn("bytes=", msg)
        self.assertIn("server_date=Sat, 18 Oct 2026 01:13:00 GMT", msg)
        self.assertIn("games_dates=['2026-10-17']", msg)
        self.assertIn("target_entry=no", msg)
        self.assertEqual(len(seen_urls), 2)

    def test_non_ok_fetch_logged_with_status(self):
        # Iter-23: a uniform non-OK (control + target share the
        # failure fingerprint) is now "unverified (fetch blocked)",
        # NOT "not published yet" — a 404/403 on its own carries
        # zero publication information (ANALYSIS20 §4).
        def _get(url, **kw):
            return _FakeBatterupResp(status=404, body=b"Not Found")

        with mock.patch("requests.get", side_effect=_get):
            with self.assertRaises(SystemExit) as cm:
                ANS.batterup(self.DAY)
        msg = str(cm.exception)
        self.assertIn("status=404", msg)
        self.assertIn("unverified (fetch blocked)", msg)
        self.assertNotIn("not published yet", msg)

    def test_fetch_exception_logged_not_swallowed(self):
        def _get(url, **kw):
            raise ConnectionError("boom")

        with mock.patch("requests.get", side_effect=_get):
            with self.assertRaises(SystemExit) as cm:
                ANS.batterup(self.DAY)
        self.assertIn("fetch_failed=boom", str(cm.exception))

    def test_published_day_still_returns_player(self):
        # The evidence change must not change the defer DECISION:
        # a published day still returns the player.
        body = _batterup_body([self._entry("2026-10-18", "Star Player")])
        with mock.patch("requests.get",
                        return_value=_FakeBatterupResp(body=body)):
            out = ANS.batterup(self.DAY)
        self.assertEqual(out["answer"], "Star Player")
        self.assertEqual(out["via"], "cdn")


# ---------------------------------------------------------------- fix 3
# Bundle is finals-only.

class TestBundleFinalsOnly(unittest.TestCase):
    def test_workflow_excludes_raw_gameplay(self):
        self.assertIn("Exclude raw gameplay from the pack", WF)
        self.assertIn("finals-only", WF)
        self.assertIn('video-*/gameplay_1080.mp4', WF)
        self.assertIn("no raw gameplay_1080.mp4", WF)

    def test_filter_logic_on_fixture_tree(self):
        # Replicate the workflow's two filter steps + verification
        # on a fixture tree shaped like the iter-21 bundle:
        # solved nerdle with final + raw (raw holds the CUT
        # Speed chapter), solved waffle with final + raw,
        # unsolved worldle with gameplay only.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "all-artifacts"
            cases = (("nerdle", True), ("waffle", True),
                     ("worldle", False))
            for game, solved in cases:
                d = root / f"video-{game}"
                d.mkdir(parents=True)
                (d / "result.json").write_text(
                    json.dumps({"solved": solved}))
                (d / "gameplay_1080.mp4").write_text("fake raw")
                if solved:
                    (d / f"{game}_final_2026-10-18.mp4").write_text(
                        "fake final")
            # Step 1 (iter-19): every mp4 in a solved=False dir.
            for res in sorted(root.glob("video-*/result.json")):
                if json.loads(res.read_text()).get("solved") is False:
                    for mp4 in sorted(res.parent.glob("*.mp4")):
                        mp4.unlink()
            # Step 2 (iter-22, finals-only): raw gameplay in EVERY
            # dir, solved or not.
            for raw in sorted(root.glob("video-*/gameplay_1080.mp4")):
                raw.unlink()
            # Verification: no mp4 in unsolved dirs; no raw
            # gameplay anywhere.
            leaked = []
            for res in sorted(root.glob("video-*/result.json")):
                if json.loads(res.read_text()).get("solved") is False:
                    leaked.extend(sorted(res.parent.glob("*.mp4")))
            self.assertEqual(leaked, [])
            self.assertEqual(
                sorted(root.glob("video-*/gameplay_1080.mp4")), [])
            # Finals survive for solved games; unsolved has no mp4.
            self.assertTrue(
                (root / "video-nerdle/nerdle_final_2026-10-18.mp4"
                 ).exists())
            self.assertTrue(
                (root / "video-waffle/waffle_final_2026-10-18.mp4"
                 ).exists())
            self.assertEqual(
                sorted((root / "video-worldle").glob("*.mp4")), [])

    def test_verification_catches_raw_gameplay_leak(self):
        # A surviving gameplay_1080.mp4 in a SOLVED dir must
        # fail the finals-only verification (the iter-21 leak).
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "all-artifacts"
            d = root / "video-nerdle"
            d.mkdir(parents=True)
            (d / "result.json").write_text(json.dumps({"solved": True}))
            (d / "nerdle_final_2026-10-18.mp4").write_text("fake")
            (d / "gameplay_1080.mp4").write_text("fake raw")
            raw = sorted(root.glob("video-*/gameplay_1080.mp4"))
            self.assertEqual(len(raw), 1)


# ---------------------------------------------------------------- fix 4
# Recording: game-evaluated form + normalized-match wording.

class TestGameEvaluatedForm(unittest.TestCase):
    def test_exact_match_records_suggestion(self):
        record, flat = NS._game_evaluated_form("50-6*7=8", "50-6*7=8")
        self.assertEqual(record, "50-6*7=8")
        self.assertIsNone(flat)

    def test_flattened_superscript_records_game_form(self):
        # The iter-21 maxi wart: DOM read-back "12+67+8=76"
        # records as game-evaluated "1²+67+8=76", flattened
        # form kept ONLY as the footnote.
        record, flat = NS._game_evaluated_form("12+67+8=76",
                                                "1²+67+8=76")
        self.assertEqual(record, "1²+67+8=76")
        self.assertEqual(flat, "12+67+8=76")

    def test_flattened_cube_records_game_form(self):
        record, flat = NS._game_evaluated_form("13+67+8=76",
                                                "1³+67+8=76")
        self.assertEqual(record, "1³+67+8=76")
        self.assertEqual(flat, "13+67+8=76")

    def test_true_mismatch_records_board_row(self):
        # A real divergence (midi reorder) still records the
        # board row, not the suggestion.
        record, flat = NS._game_evaluated_form("5+1-1=5", "1-1+5=5")
        self.assertEqual(record, "5+1-1=5")
        self.assertIsNone(flat)

    def test_double_superscript_never_normalizes(self):
        # "1²²+67+8=76" matches NEITHER form — it is a true
        # mismatch, recorded as the board row.
        record, flat = NS._game_evaluated_form("1²²+67+8=76",
                                                "1²+67+8=76")
        self.assertEqual(record, "1²²+67+8=76")
        self.assertIsNone(flat)

    def test_missing_readback_records_suggestion(self):
        record, flat = NS._game_evaluated_form(None, "50-6*7=8")
        self.assertEqual(record, "50-6*7=8")
        self.assertIsNone(flat)


class _FakePreEnterPage:
    """Fake for _verify_row_or_retype: the in-progress row holds
    `row_text`."""

    def __init__(self, row_text):
        self.row_text = row_text

    async def wait_for_timeout(self, _ms):
        return None

    async def evaluate(self, js, *args):
        s = js if isinstance(js, str) else ""
        if "pb-grid" in s:
            return [{"rows": [[-1] * 10], "texts": [self.row_text]}]
        return None


class TestPreEnterNormalizedMatchWording(unittest.TestCase):
    def test_flattened_row_logs_normalized_match(self):
        page = _FakePreEnterPage("12+67+8=76")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ok = _run(NS._verify_row_or_retype(page, "1²+67+8=76", 10))
        out = buf.getvalue()
        self.assertTrue(ok)
        self.assertIn("normalized-match", out)
        self.assertIn("12+67+8=76", out)
        self.assertIn("1²+67+8=76", out)
        # Must NEVER print "matches" for two textually different
        # strings.
        self.assertNotIn("matches '1²+67+8=76'", out)

    def test_exact_row_still_logs_matches(self):
        page = _FakePreEnterPage("50-6*7=8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ok = _run(NS._verify_row_or_retype(page, "50-6*7=8", 8))
        self.assertTrue(ok)
        self.assertIn("matches '50-6*7=8'", buf.getvalue())


# ---------------------------------------------------------------- guards
# Typing speed + kept behavior untouched.

class TestIter22Guards(unittest.TestCase):
    def test_typing_speed_unchanged(self):
        # No delay value in the type path was touched: the
        # keyboard.type delay=200 call sites are unchanged.
        self.assertIn("keyboard.type(eq, delay=200)", SRC_NERDLE)
        self.assertIn("keyboard.type(part, delay=delay)", SRC_NERDLE)

    def test_kept_constants_untouched(self):
        self.assertIn("NERDLE_SITE_BOUNDARY_UTC = (16, 30)", SRC_NERDLE)
        self.assertIn("prepurge BEFORE", SRC_NERDLE)
        self.assertIn("_popupTrip", SRC_NERDLE)
        self.assertIn("iterate ALL matches", SRC_NERDLE)

    def test_no_two_green_row_demand_in_grant_path(self):
        # The grant predicate demands >= 1 green row; the
        # _check_win_verified speed branch must not demand
        # green >= boards as the ONLY speed path — the grant
        # path exists alongside it.
        self.assertIn("_speed_grant_verified", SRC_NERDLE)
        self.assertIn("single green solution row", SRC_NERDLE)


if __name__ == "__main__":
    unittest.main()
