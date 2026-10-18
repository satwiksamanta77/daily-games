"""Fix-loop iteration 20 tests — Nerdle workstream.

Evidence-grounded in ANALYSIS17 (run 39078605979):

1. Header probe `_inPopup` REGRESSION (P0, §2b/§6.1/§7): iter-19's
   blanket popup filter took the probe 8/9 -> 0/9 non-null
   (own_headers=[] everywhere) while the unfiltered diagnostics
   still carried 'nerdle . #1733' and the frame showed the header
   rendered. Root cause: while a win popup is open the game
   wrapper itself carries aria-hidden="true" (standard modal
   pattern), so the filter excluded the GAME HEADER with the
   popup — and it never logged which selector tripped. Now
   SERVED_JS records the tripping selector per element
   (`_popupTrip`), the own-header collection applies NO popup
   filter (each entry records its popup_trip), the loose
   header/title selectors keep only the strict filter
   (dialog/modal/popup/about/overlay, WITHOUT aria-hidden), and
   the Python side prefers untripped entries while still
   accepting a tripped game header as the only match. The gate
   stays FAIL-CLOSED.
2. Mini gate, real test (P0, §2b/§7): iter-19 never executed the
   mini comparison (no header to compare). End-to-end: with
   headers present, mini passes on site_solutions == official
   when its header is the global number, and still blocks on a
   real site-solutions mismatch or a missing classic header.
3. Speed: play BOTH site solutions (P0, §2e/§6.3/§7): iter-19
   speed exited after 2 guesses with '2+10-6=6' unplayed and
   rows remaining — the loop abandoned the mode through a
   SILENT break (no exit-cause log). Now both silent exits
   (no-unsolved-grid / rows_left=0) re-read to rule out a
   transient misread and log the exit with the unplayed pool,
   the no-new-board path retries the fresh read 3x, and a
   pool-drain backstop plays the next unplayed site solution
   instead of breaking when solver + official are exhausted.
4. ² repeatability, two ² in ONE equation/session (P0, §2a/§7):
   iter-19 had exactly one ² guess, so fresh re-query was
   unproven. A single type_equation call for '1²+2²=5' must
   issue exactly one fresh js-click query per ² (two total),
   verify each via the tile/sup delta, and never fall through
   to coordinate/clipboard/insert_text.
5. Second pass (same iteration): genuine-popup DROP (fix 1
   tightening) — a popup copy of a logo line is DROPPED, not
   merely deprioritized; popup-only entries yield no header
   (aria-hidden trips still count: the wrapper itself carries
   aria-hidden while a win popup is open). Mini gate driven
   probe -> header -> gate (fix 2). Speed site-solution
   OVERRIDE + bounded RETRY (fix 3): an unplayed site solution
   beats another solver probe once min_probes probes are typed
   or a board is solved, and a failed site solution gets one
   sweep + re-entry. ² repeatability across THREE separate
   guesses (fix 4, verify-only: no gap found, no fix
   invented). Leading-wait trim (fix 5, P2): the pre-guess
   sweep loop breaks after ONE idle sweep and the main loop's
   first-iteration sweep is skipped (priming still re-sweeps
   before the first keystroke); typing speed unchanged.

All tests are synthetic/local: no network, no browser, no CI run.
"""
import asyncio
import contextlib
import io
import itertools
import os
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


def _fn_src(src, start_marker, end_marker):
    start = src.index(start_marker)
    end = src.index(end_marker, start)
    return src[start:end]


# ---------------------------------------------------------------- fix 1
# Header probe: no blanket popup filter on own-headers; trip logged;
# Python prefers untripped, accepts tripped game header.

class TestHeaderProbePopupTrip(unittest.TestCase):
    def test_tripped_game_header_accepted(self):
        # Iter-19 shape: the only own-header entry trips
        # 'aria-hidden' (win popup open, wrapper hidden) — the
        # iter-19 filter dropped it (0/9). Iter-20 accepts it.
        probe = {"own_headers": [
            {"tag": "DIV", "cls": "nav",
             "text": "maxi nerdle . #1368",
             "popup_trip": "aria-hidden"}]}
        n, ev = NS._nerdle_own_header_from_probe(probe, "maxi")
        self.assertEqual(n, 1368)
        self.assertEqual(ev["trip"], "aria-hidden")

    def test_untripped_preferred_over_tripped(self):
        # A popup entry carrying a DIFFERENT number must not beat
        # the untripped game header, even when it sorts first.
        probe = {"own_headers": [
            {"tag": "DIV", "cls": "share-popup",
             "text": "nerdle . #9999", "popup_trip": "dialog"},
            {"tag": "DIV", "cls": "nav",
             "text": "classic nerdle . #1733", "popup_trip": None}]}
        n, ev = NS._nerdle_own_header_from_probe(probe, "classic")
        self.assertEqual(n, 1733)
        self.assertEqual(ev["trip"], "")

    def test_tripped_minibi_header_accepted(self):
        # Minibi's own header reads 'mini-bi nerdle . #<n>' and
        # trips the filter while the About/win popup is open.
        probe = {"own_headers": [
            {"tag": "DIV", "cls": "nav",
             "text": "mini-bi nerdle . #1501",
             "popup_trip": "aria-hidden"}]}
        n, ev = NS._nerdle_own_header_from_probe(probe, "minibi")
        self.assertEqual(n, 1501)

    def test_about_text_without_logo_line_is_not_a_header(self):
        # The About/win-popup title carries no #<digits> logo line,
        # so it can never become an own-header entry — the strict
        # pattern, not a popup filter, is the guard.
        probe = {"own_headers": [
            {"tag": "DIV", "cls": "about",
             "text": "About mini-bi nerdle - the fun math game",
             "popup_trip": "about"}]}
        n, ev = NS._nerdle_own_header_from_probe(probe, "minibi")
        self.assertIsNone(n)
        self.assertIsNone(ev)

    def test_header_number_raw_carries_popup_trip(self):
        probe = {"own_headers": [
            {"tag": "DIV", "cls": "nav",
             "text": "nerdle . #1733", "popup_trip": "aria-hidden"}]}
        n, strategy, raw = NS._nerdle_header_number_from_probe(
            probe, mode_id="classic")
        self.assertEqual((n, strategy), (1733, "own-header"))
        self.assertIn("popup_trip=aria-hidden", raw)

    def test_served_js_records_trip_no_blanket_skip(self):
        served = _fn_src(SRC_NERDLE, "SERVED_JS = ", "def _solutions_played_cover")
        # Trip-recording helper exists; own-headers carry popup_trip;
        # probe_diag counts trips per selector.
        self.assertIn("_popupTrip", served)
        self.assertIn("popup_trip: _trip", served)
        self.assertIn("popup_trips", served)
        self.assertIn("popup_tripped_own_headers", served)
        self.assertIn("header_els_aria_hidden_skipped", served)
        # The blanket skip is gone from the own-header collection
        # (it still applies to the hash_texts fallback, where
        # _inPopup is the strict filter — hence the region-scoped
        # check, not a whole-file NotIn).
        _own_loop = _fn_src(served, "const _all = ",
                            "own_headers.sort")
        self.assertNotIn("if (_inPopup(e)) continue", _own_loop)
        self.assertNotIn("_inPopupStrict(e)", _own_loop)
        # …and the loose selectors use the STRICT filter (no
        # aria-hidden): the old combined selector list is gone.
        self.assertNotIn("[aria-hidden=\"true\"]'); } catch", served)
        self.assertIn("_inPopupStrict", served)


# ---------------------------------------------------------------- fix 2
# Mini gate, real end-to-end test with headers present.

def _served_with_headers(classic_hn, mini_hn, mini_site,
                         mini_matches=True):
    return {
        "classic": {"header_number": classic_hn,
                    "matches_target_official": True,
                    "site_solutions": ["9*83=747"]},
        "mini": {"header_number": mini_hn,
                 "matches_target_official": mini_matches,
                 "site_solutions": mini_site},
        "midi": {"header_number": 1733,
                 "matches_target_official": True},
        "maxi": {"header_number": 1368,
                 "matches_target_official": True},
    }


class TestMiniGateWithHeaders(unittest.TestCase):
    API = {"classic": [1733], "mini": [497], "midi": [1733],
           "maxi": [1368]}
    OFFICIAL = {"mini": ["5+4=9"]}

    def test_mini_passes_on_site_solutions_with_global_header(self):
        # Iter-19 never executed this: mini header == classic
        # header (global number) + site_solutions == official.
        served = _served_with_headers(1733, 1733, ["5+4=9"])
        blocked, _detail, per = NS._nerdle_number_gate(
            served, self.API, official=self.OFFICIAL)
        self.assertFalse(blocked)
        self.assertIn("site-solutions", per["mini"]["status"])

    def test_mini_blocks_on_real_site_solutions_mismatch(self):
        served = _served_with_headers(1733, 1733, ["5+3=8"])
        blocked, _detail, per = NS._nerdle_number_gate(
            served, self.API, official=self.OFFICIAL)
        self.assertTrue(blocked)
        self.assertIn("blocked", per["mini"]["status"])

    def test_classic_missing_header_still_blocks(self):
        # Fail-closed is NOT weakened: classic unexposed blocks.
        served = _served_with_headers(None, 1733, ["5+4=9"])
        blocked, _detail, per = NS._nerdle_number_gate(
            served, self.API, official=self.OFFICIAL)
        self.assertTrue(blocked)
        self.assertIn("blocked", per["classic"]["status"])

    def test_mini_mode_specific_header_still_header_gated(self):
        # A mini header that is NOT the global number is a
        # mode-specific claim: header-vs-API applies, mismatch
        # blocks (pre-iter-19 behaviour preserved).
        served = _served_with_headers(1733, 999, ["5+4=9"])
        blocked, _detail, per = NS._nerdle_number_gate(
            served, self.API, official=self.OFFICIAL)
        self.assertTrue(blocked)
        self.assertIn("blocked", per["mini"]["status"])


# ---------------------------------------------------------------- fix 3
# Speed: pool-drain backstop + verified loop exits.

class TestSpeedPoolDrain(unittest.TestCase):
    def test_unplayed_pool(self):
        pool = ["50-6*7=8", "2+10-6=6"]
        self.assertEqual(
            NS._unplayed_pool(pool, ["63-7*9=0", "50-6*7=8"]),
            ["2+10-6=6"])
        self.assertEqual(NS._unplayed_pool(pool, pool), [])
        self.assertEqual(NS._unplayed_pool(pool, []), pool)
        self.assertEqual(NS._unplayed_pool([], ["1+1=2"]), [])

    def test_unplayed_pool_dom_normalized_superscript_counts(self):
        # A DOM-normalized plain-2 read-back counts as the ²
        # solution played — no replay of an already-played board.
        self.assertEqual(
            NS._unplayed_pool(["1²+67+8=76"], ["12+67+8=76"]), [])

    def test_loop_exits_are_logged_and_pool_drain_present(self):
        loop = _fn_src(SRC_NERDLE, "for _it in range(40):",
                       "PLAYED[mode[\"id\"]] = list(played_here)")
        # No silent exits: both abandonment paths log the cause
        # with the unplayed pool.
        self.assertIn("no unsolved grid and no ", loop)
        self.assertIn("new board after 3 re-reads", loop)
        self.assertIn("rows_left=0 on re-read", loop)
        self.assertIn("_unplayed_pool(pool", loop)
        # The no-new-board path retries the fresh read 3x.
        self.assertIn("for _retry in range(3):", loop)
        # Pool-drain backstop: solver + official exhausted with a
        # site solution unplayed -> play it, don't break.
        self.assertIn("pool-drain backstop", loop)
        self.assertIn("nxt = _unplayed[0]", loop)


# ---------------------------------------------------------------- fix 4
# ² repeatability: TWO ² entries in ONE equation / session.

class _FakeTwoPowerPage:
    """Playwright stand-in for a board needing TWO ² entries in a
    single equation ('1²+2²=5'): every js-click evaluate is a FRESH
    re-query that lands one entry (tile/sup delta rises)."""

    def __init__(self):
        self.keyboard = _FakeTwoPowerKeyboard(self)
        self.mouse = _FakeTwoPowerMouse()
        self.entries = 0
        self.js_click_calls = 0
        self.box_calls = 0
        self.clipboard_calls = 0

    async def wait_for_timeout(self, _ms):
        return None

    async def evaluate(self, js, *args):
        if "sup_cells" in js:
            return {"tiles": 9 + self.entries,
                    "sup_cells": self.entries, "grids": []}
        if "return {x:" in js:
            self.box_calls += 1
            return {"x": 100.0, "y": 200.0, "w": 40.0, "h": 24.0}
        if "b.click()" in js:
            self.js_click_calls += 1
            self.entries += 1
            return True
        if "navigator.clipboard" in js:
            self.clipboard_calls += 1
            self.entries += 1
            return None
        if "getComputedStyle" in js:
            # READ_JS: no evaluated rows, no ² in row text.
            return [{"rows": [[-1] * 6], "texts": [""]}]
        if "div.pb-grid *" in js:
            return 0
        return None


class _FakeTwoPowerKeyboard:
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
        self._page.entries += 1


class _FakeTwoPowerMouse:
    def __init__(self):
        self.clicked = []

    async def click(self, x, y):
        self.clicked.append((x, y))


class TestTwoSuperscriptsOneSession(unittest.TestCase):
    def test_two_superscripts_each_fresh_js_click(self):
        page = _FakeTwoPowerPage()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _run(NS.type_equation(page, "1²+2²=5"))
        out = buf.getvalue()
        # Exactly ONE fresh js-click query per ² — no double entry,
        # no stale-handle reuse, no fallthrough.
        self.assertEqual(page.js_click_calls, 2)
        self.assertEqual(page.box_calls, 0)
        self.assertEqual(page.mouse.clicked, [])
        self.assertEqual(page.clipboard_calls, 0)
        self.assertEqual(page.keyboard.inserted, [])
        self.assertEqual([t for t, _ in page.keyboard.typed],
                         ["1", "+2", "=5"])
        self.assertEqual(out.count("verified in row text via js-click"), 2)
        self.assertNotIn("FAILED after all entry methods", out)
        self.assertNotIn("unverified after", out)


# ----------------------------------------------------------------
# Fix 1 (second pass): genuine-popup trips are DROPPED, not merely
# deprioritized. A popup copy of a logo line must never become the
# header, even when it is the only match; aria-hidden trips still
# count (the wrapper itself carries aria-hidden while a win popup
# is open).

class TestGenuinePopupDrop(unittest.TestCase):
    def test_popup_only_logo_line_yields_no_header(self):
        # Popup text must not BE the header: with only a popup
        # copy present, there is no header (gate warns / fails
        # closed downstream), never the popup's number.
        probe = {"own_headers": [
            {"tag": "DIV", "cls": "win-popup",
             "text": "maxi nerdle . #9999", "popup_trip": "dialog"}]}
        n, ev = NS._nerdle_own_header_from_probe(probe, "maxi")
        self.assertIsNone(n)
        self.assertIsNone(ev)

    def test_all_genuine_popup_trips_dropped(self):
        for trip in ("dialog", "modal", "popup", "about", "overlay"):
            probe = {"own_headers": [
                {"tag": "DIV", "cls": "x",
                 "text": "mini nerdle . #1733", "popup_trip": trip}]}
            n, _ = NS._nerdle_own_header_from_probe(probe, "mini")
            self.assertIsNone(n, f"trip={trip} must be dropped")

    def test_untripped_preferred_over_aria_hidden_tripped(self):
        # Two maxi candidates with DIFFERENT numbers: the
        # untripped game header wins over an aria-hidden-tripped
        # copy, regardless of list order.
        probe = {"own_headers": [
            {"tag": "DIV", "cls": "main-nav",
             "text": "maxi nerdle . #9999", "popup_trip": "aria-hidden"},
            {"tag": "DIV", "cls": "main-nav",
             "text": "maxi nerdle . #1368", "popup_trip": None}]}
        n, _ = NS._nerdle_own_header_from_probe(probe, "maxi")
        self.assertEqual(n, 1368)

    def test_legacy_entries_without_trip_collected(self):
        # Probes predating the annotation carry no popup_trip —
        # collection fails OPEN (the gate still fails closed on a
        # missing header).
        probe = {"own_headers": [
            {"tag": "DIV", "cls": "main-nav",
             "text": "mini nerdle . #1733"}]}
        n, _ = NS._nerdle_own_header_from_probe(probe, "mini")
        self.assertEqual(n, 1733)

    def test_strict_filter_applies_to_loose_selectors_only(self):
        # The strict filter (_inPopupStrict, no aria-hidden)
        # applies to the loose header/title + hash fallbacks —
        # the own-header collection has NO popup skip. The old
        # `_inPopup` blanket name is gone entirely.
        self.assertIn("_inPopupStrict", NS.SERVED_JS)
        self.assertIn("if (_inPopupStrict(e)) continue;", NS.SERVED_JS)
        self.assertNotIn("if (_inPopup(e)) continue;", NS.SERVED_JS)
        self.assertNotIn("!_inPopup(e)", NS.SERVED_JS)


# ----------------------------------------------------------------
# Fix 2 (second pass): mini gate driven probe -> header -> gate.

class TestMiniGateProbeToGate(unittest.TestCase):
    API = {"classic": [1733], "mini": [497], "midi": [1733],
           "maxi": [1368], "classicPuzzleNumber": 1733}
    OFFICIAL = {"classic": ["9*83=747"], "mini": ["30/6=5"]}

    def _probe(self, text, trip="aria-hidden"):
        return {"own_headers": [
            {"tag": "DIV", "cls": "main-nav", "text": text,
             "popup_trip": trip}]}

    def _served(self, mini_site, classic_hn=1733):
        classic_probe = ({"own_headers": []} if classic_hn is None
                         else self._probe("nerdle . #1733"))
        classic_n, _, _ = NS._nerdle_header_number_from_probe(
            classic_probe, mode_id="classic")
        mini_n, _, _ = NS._nerdle_header_number_from_probe(
            self._probe("mini nerdle . #1733"), mode_id="mini")
        return {
            "classic": {"header_number": classic_n,
                        "matches_target_official": True,
                        "site_solutions": ["9*83=747"]},
            "mini": {"header_number": mini_n,
                     "matches_target_official": True,
                     "site_solutions": mini_site},
        }

    def test_mini_gate_executes_and_passes(self):
        # Iter-19 never executed this: headers were null. With
        # headers present, mini header == classic global header +
        # site_solutions == official -> the gate PASSES (and the
        # mini comparison actually ran — status names it).
        served = self._served(mini_site=["30/6=5"])
        self.assertEqual(served["mini"]["header_number"], 1733)
        self.assertEqual(served["classic"]["header_number"], 1733)
        blocked, _, per = NS._nerdle_number_gate(
            served, self.API, official=self.OFFICIAL)
        self.assertFalse(blocked)
        self.assertIn("site-solutions", per["mini"]["status"])

    def test_mini_gate_blocks_on_solution_mismatch(self):
        served = self._served(mini_site=["1+2=3"])
        blocked, detail, _ = NS._nerdle_number_gate(
            served, self.API, official=self.OFFICIAL)
        self.assertTrue(blocked)
        self.assertIn("mini", detail)

    def test_mini_gate_blocks_when_classic_header_missing(self):
        # No classic header -> the global-header comparison
        # cannot run -> fail-closed ('classic header number not
        # exposed'), now only on a REAL missing header rather
        # than a broken probe.
        served = self._served(mini_site=["30/6=5"], classic_hn=None)
        self.assertIsNone(served["classic"]["header_number"])
        blocked, detail, _ = NS._nerdle_number_gate(
            served, self.API, official=self.OFFICIAL)
        self.assertTrue(blocked)
        self.assertIn("classic header number not exposed", detail)


# ----------------------------------------------------------------
# Fix 3 (second pass): speed site-solution OVERRIDE + bounded RETRY.


def _shift13(eq):
    """Encode like the site's answer files (ASCII +13)."""
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
        g = self._page._first_unsolved()
        if g is None:
            return
        for i, fb in enumerate(g["rows"]):
            if not NS._done(fb):
                # Real grid rows hold at most `cols` (8) cells —
                # keystrokes into a full row are dropped, never
                # appended past the row width.
                g["texts"][i] = ((g["texts"][i] or "") + text)[:8]
                return

    async def press(self, key):
        self.pressed.append(key)
        if key == "Enter":
            self._page._submit()
        elif key == "Backspace":
            g = self._page._first_unsolved()
            if g is None:
                return
            for i, fb in enumerate(g["rows"]):
                if not NS._done(fb) and g["texts"][i]:
                    g["texts"][i] = g["texts"][i][:-1]
                    return
        # Escape: no-op

    async def insert_text(self, text):
        await self.type(text)


class _FakeSpeedPage:
    """Playwright stand-in for the Nerdle speed page: 2 grids x 6
    rows, site solutions A/B served via the FETCH_JS path (answer
    files, +13 shifted), solver patched to return fresh junk.
    Keyboard.type fills the in-progress row of the first unsolved
    grid; Enter evaluates it against that grid's solution.
    `drop_enters_for` maps an equation to a count of swallowed
    Enters (entry-failure simulation for the retry test).
    """

    A = "50-6*7=8"
    B = "2+10-6=6"

    def __init__(self, drop_enters_for=None):
        self.url = "https://speed.nerdlegame.com/"
        self.frames = []
        self.keyboard = _FakeSpeedKeyboard(self)
        self.mouse = _FakeSpeedMouse()
        self.grids = [self._fresh_grid(self.A), self._fresh_grid(self.B)]
        self._drop = dict(drop_enters_for or {})
        self.screenshots = []

    @staticmethod
    def _fresh_grid(solution):
        return {"solution": solution,
                "rows": [[-1] * 8 for _ in range(6)],
                "texts": [""] * 6}

    def _first_unsolved(self):
        for g in self.grids:
            if not any(all(v == 2 for v in r) for r in g["rows"]):
                return g
        return None

    def _submit(self):
        g = self._first_unsolved()
        if g is None:
            return
        for i, fb in enumerate(g["rows"]):
            if not NS._done(fb) and (g["texts"][i] or "").strip():
                typed = g["texts"][i].strip()
                if self._drop.get(typed, 0) > 0:
                    self._drop[typed] -= 1
                    return  # Enter swallowed: row stays in-progress
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
        if "out.keys" in s:  # restore probe
            return {"keys": [], "guesses": 0,
                    "hasSolution": False, "wonText": False}
        if "localStorage.removeItem" in s:  # clear
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
            return [_shift13(self.A), _shift13(self.B)]
        if "you won" in s:  # WIN_JS
            return False
        if "play now" in s:  # SPLASH_JS
            return False
        if "localStorage.getItem('gameState')" in s:  # read_game_state
            return None
        if "document.body.style.zoom" in s:
            return None
        if "pb-grid" in s:  # READ_JS
            return [{"rows": [list(r) for r in g["rows"]],
                     "texts": list(g["texts"])} for g in self.grids]
        return None


class TestSpeedOverrideAndRetry(unittest.TestCase):
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

    def test_speed_plays_both_site_solutions(self):
        # Iter-19 shape, solver NEVER empty: it keeps returning
        # fresh junk probes. Without the iter-20 override the loop
        # would burn rows on probes (the backstop alone cannot
        # fire while the solver answers). With it: 3 probes, then
        # site solution A (override), then site solution B
        # (override on solved board) -> both played, mode won.
        page = _FakeSpeedPage()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            typed, expected, signal = _run(
                NS.play_mode(page, self.MODE, ".",
                             official=dict(self.OFFICIAL)))
        out = buf.getvalue()
        self.assertEqual(signal, "won")
        self.assertEqual(typed, 5)
        self.assertEqual(expected, 2)
        played = NS.PLAYED.get("speed") or []
        self.assertIn("50-6*7=8", played)
        self.assertIn("2+10-6=6", played)
        self.assertEqual(played[-2:], ["50-6*7=8", "2+10-6=6"])
        self.assertIn("site-solution override", out)

    def test_failed_site_solution_retried_once(self):
        # The 1st site solution's entry fails 3x (Enter swallowed:
        # attempt1 + Enter-only + already-in-grid retry inside
        # type_guess) — without the iter-20 outer retry the loop
        # would break with BOTH site solutions unplayed. With it:
        # one sweep + re-entry rescues A, then B plays, mode won.
        page = _FakeSpeedPage(drop_enters_for={"50-6*7=8": 3})
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            typed, _, signal = _run(
                NS.play_mode(page, self.MODE, ".",
                             official=dict(self.OFFICIAL)))
        out = buf.getvalue()
        self.assertEqual(signal, "won")
        played = NS.PLAYED.get("speed") or []
        self.assertIn("50-6*7=8", played)
        self.assertIn("2+10-6=6", played)
        self.assertIn("site-solution retry", out)
        self.assertNotIn("board did not react", out)

    def test_override_only_replaces_solver_probes(self):
        # The override lives in play_mode's selection: it must be
        # present, it must key on the pool + min_probes/solved, and
        # it must never fire when the pool is drained (the
        # _unplayed2 guard).
        pm = _fn_src(SRC_NERDLE, "async def play_mode", "async def main")
        self.assertIn("site-solution override", pm)
        self.assertIn("nxt not in pool", pm)
        self.assertIn("len(typed_guesses) >= min_probes", pm)
        self.assertIn("any(grid_solved(g)", pm)
        self.assertIn("if (_unplayed2 and", pm)


# ----------------------------------------------------------------
# Fix 4 (second pass): ² repeatability across THREE separate
# guesses — VERIFY-ONLY. The code trace found NO gap (see the
# class docstring), so NO fix was invented; these tests pin the
# trace so a future regression is caught.

class _FakePowerKeyboard3:
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


class _FakePowerMouse3:
    def __init__(self):
        self.clicked = []

    async def click(self, x, y):
        self.clicked.append((x, y))


class _FakePowerPage3:
    """Fake page for THREE consecutive ² entries, each with a
    transiently-absent power button on the first js-click attempt
    (keyboard re-rendering between guesses — the iter-18 guess-4
    shape, repeated per guess)."""

    def __init__(self):
        self.keyboard = _FakePowerKeyboard3(self)
        self.mouse = _FakePowerMouse3()
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
            if self.js_click_calls % 2 == 1:
                return False  # transient: not yet re-rendered
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


class TestSuperscriptThreeGuessesNoGap(unittest.TestCase):
    """Iter-20 verify-only finding: the code trace shows NO
    repeatability gap, so NO fix was invented:

    - CLICK_POWER_JS / POWER_BTN_BOX_JS re-query
      document.querySelectorAll('button') FRESH on every call —
      no handle is ever cached across guesses.
    - _click_power_key / _power_button_box re-evaluate per
      attempt (up to 4, short settle between).
    - type_equation's entry snapshots (before / _before_state /
      after / _after_state) are PER-PART LOCALS — no sup counter
      or entry state leaks across guesses.
    - type_equation holds no module-level / global state at all.
    """

    def test_three_consecutive_superscripts_each_retried_fresh(self):
        # Three ² guesses in one session; each entry's FIRST
        # js-click fails (re-render), the retry succeeds. Every
        # entry lands via js-click, single entry each, no
        # fallthrough to coordinate / clipboard / insert_text.
        page = _FakePowerPage3()
        for eq in ("1²+67+8=76", "1²+60+7=68", "1²+61+8=70"):
            page.entered = False
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                _run(NS.type_equation(page, eq))
            out = buf.getvalue()
            self.assertIn("verified in row text via js-click", out)
            self.assertNotIn("FAILED after all entry methods", out)
        self.assertEqual(page.js_click_calls, 6)  # 2 per entry
        self.assertEqual(page.mouse.clicked, [])
        self.assertEqual(page.clipboard_calls, 0)
        self.assertEqual(page.keyboard.inserted, [])

    def test_type_equation_holds_no_global_entry_state(self):
        te = _fn_src(SRC_NERDLE, "async def type_equation",
                     "async def _verify_row_or_retype")
        self.assertNotIn("global ", te)

    def test_entry_snapshots_are_per_part_locals(self):
        te = _fn_src(SRC_NERDLE, "async def type_equation",
                     "async def _verify_row_or_retype")
        self.assertIn("_before_state = await _grid_superscript_state",
                      te)
        self.assertIn("before = await _grid_superscript_count", te)

    def test_click_js_requeries_fresh_every_call(self):
        self.assertIn("document.querySelectorAll('button')",
                      NS.CLICK_POWER_JS)
        self.assertIn("document.querySelectorAll('button')",
                      NS.POWER_BTN_BOX_JS)
        helper = _fn_src(SRC_NERDLE, "async def _click_power_key",
                         "async def _power_button_box")
        self.assertIn("page.evaluate(CLICK_POWER_JS, ch)", helper)
        self.assertIn("attempts", helper)


# ----------------------------------------------------------------
# Fix 5: Nerdle leading-wait trim (leading IDLE only; typing
# speed unchanged; no improvement claimed without a final).

class TestLeadingWaitTrim(unittest.TestCase):
    def test_sweep_loop_breaks_after_one_idle_sweep(self):
        # The two-consecutive-idle-sweeps rule is gone: the
        # second idle sweep was pure re-scan. A delayed splash /
        # banner is still caught by the escape loop, the
        # cookies/modals stage, and type_guess's priming sweep
        # immediately before the first keystroke.
        pm = _fn_src(SRC_NERDLE, "async def play_mode", "async def main")
        self.assertIn("_idle_sweeps >= 1", pm)
        _sweep_loop = _fn_src(
            pm, "Iter-20 (P2, leading-wait trim): ONE idle sweep",
            "_stage(\"cookies/modals\")")
        self.assertNotIn("_idle_sweeps >= 2", _sweep_loop)

    def test_loop_iter0_sweep_skipped_with_stage_log(self):
        # The loop's first-iteration sweep ran back-to-back with
        # the pre-guess dismissal; priming re-sweeps after the
        # solver POST, so the iter-0 sweep is the stale one. The
        # skip is stage-logged so the next run proves the trim.
        pm = _fn_src(SRC_NERDLE, "async def play_mode", "async def main")
        self.assertIn("if _it == 0 and _PRE_GUESS_DISMISSED:", pm)
        self.assertIn("loop iter0 sweep skipped", pm)
        self.assertIn("_PRE_GUESS_DISMISSED = True", pm)
        # Later iterations still sweep (popups between guesses).
        self.assertIn("else:\n                    await sweep_popups(page",
                      pm)

    def test_priming_still_sweeps_and_focuses_before_first_typing(self):
        # The FRESH pre-typing sweep is kept: it runs after the
        # solver POST, when a delayed banner can actually appear.
        # Focus always runs (iter-18: unfocused first guess drops
        # keystrokes).
        tg = _fn_src(SRC_NERDLE, "async def type_guess", "FETCH_JS = ")
        self.assertIn('await sweep_popups(page, tag=":type_guess")', tg)
        self.assertIn("await focus_grid(page)", tg)

    def test_typing_speed_unchanged(self):
        te = _fn_src(SRC_NERDLE, "async def type_equation",
                     "async def _verify_row_or_retype")
        self.assertIn("async def type_equation(page, eq, delay=200)", te)
        self.assertIn("page.keyboard.type(eq, delay=delay)", te)
        self.assertIn("page.keyboard.type(part, delay=delay)", te)


if __name__ == "__main__":
    unittest.main()

