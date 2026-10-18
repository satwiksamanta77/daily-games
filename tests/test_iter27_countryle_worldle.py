"""Fix-loop iteration 27 tests — WORKSTREAM B: Countryle onboarding,
Worldle Capital recolour log line, leading-idle trim.

Evidence-grounded in ANALYSIS24 (run 39099364350), with the
iter-25 -> iter-26 comparison that CORRECTS the assignment's
premise:

COUNTRYLE (ANALYSIS24 §3): the iter-25 -> iter-26 code diff
(`git diff 164140f 0a2aad8 -- runner.py`) contains ZERO Countryle
hunks — all hunks are Worldle comparison + Batterup attr code.
Same code, same 5x NEXT + PLAY! tap sequence, same log shape in
both runs (iter-25 solved Botswana, iter-26 failed). The iter-26
failure was NOT taps that never advanced: the scan DID reach the
PLAY! button, which only mounts on the last slide. What failed
was AFTER PLAY: the app re-navigated to /welcome at 05:08:57
(log NAV line, mid-input-wait), the deck was back on slide 1
(gameplay frame f0025: "Welcome to COUNTRYLE", dot 1
highlighted), and the bot never verified the game mounted,
never re-ran onboarding, and burned the input wait. Two latent
defects made that possible: (1) the tap ladder ran js-click
FIRST and trusted the dispatch; (2) the advance verdict
compared the full fingerprint, whose href/btns components can
change without a slide advance (ad churn / navigations).

Fixes covered here:
1. Real-click-first ladder (Playwright element click, then
   coordinate click, then JS click last) with post-tap
   slide-advance verification after EACH method against the
   stable active .owl-item index; retry with the next method
   when a tap did not advance.
2. LOUD deck-stuck failure: when the full ladder + escalation
   cannot advance the slide, onboarding FAILS with an explicit
   error instead of proceeding to an input wait that cannot
   succeed.
3. Deck-reappeared recovery: when the welcome deck comes back
   during the input wait (post-PLAY reload), ONE onboarding
   pass re-runs instead of waiting out a dead clock.
4. The iter-16 slide-sensitive fingerprint is unchanged.

CAPITAL (ANALYSIS24 §4): the explicit `recolour: blue->green`
line fired in iter-24 (dot right-811, #5252ff -> #5CE50D) but
was absent in iter-25/26. Root cause: `_marker_or_recolour`
short-circuited on a marker-count rise (`if _marker_appeared:
return True`), so when markers went 4->5 the recolour probe
never ran. Fix: the recolour probe ALWAYS runs and the line is
always emitted when a recolour is observed; the arbitration
verdict (marker OR recolour) is unchanged.

LEADING IDLE (ANALYSIS24 §5): same-cap condition polls replace
fixed sleeps in the pre-first-typing paths — framed 2x6000ms,
marveldle menu 1500ms / input-wait 2000ms poll / fallback 4000ms,
colorfle modal 800ms. Typing speed / per-guess pacing untouched
(colorfle 420ms/3400ms waits asserted present).

All tests are synthetic/local: no network, no browser, no CI run.
New JS payloads are also executed under node (skipped if node
is missing) on a fixture DOM.
"""

import inspect
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import runner as R


def _dbg_recorder(logs):
    def _rec(gid, msg):
        logs.append(f"[{gid}] {msg}")
    return _rec


# --------------------------------------------------------------- fakes

class _FakeMouse:
    def __init__(self, page):
        self._page = page
        self.clicks = []

    def click(self, x, y):
        self.clicks.append((x, y))
        self._page._on_coords_click()

    def move(self, x, y):
        pass

    def down(self):
        pass

    def up(self):
        pass


class _FakeKeyboard:
    def press(self, *a, **k):
        pass


class _FakeEl:
    def __init__(self, frame, label=None, is_input=False):
        self._frame = frame
        self._label = label
        self._is_input = is_input

    def click(self, timeout=None):
        if self._is_input:
            return
        self._frame._on_element_click(self._label)

    def focus(self):
        pass

    def input_value(self):
        return ""

    def is_visible(self):
        return True

    def bounding_box(self):
        return {"x": 90, "y": 190, "width": 20, "height": 20}


class _FakeCountryleFrame:
    """State machine for the iter-26 Countryle shape.

    Phases: 'deck1' (first onboarding pass) -> 'loading' (PLAY!
    tapped, deck gone, game still loading) -> 'deck2' (post-PLAY
    reload: deck back on slide 1) -> 'game' (input visible).
    The first _countryle_deck_present() call in 'loading' flips
    the phase to 'deck2' (the iter-26 05:08:57 /welcome reload).
    """

    def __init__(self, element_ok=True, coords_ok=True, js_ok=True,
                 stuck=False):
        self.url = "https://countryle.com/welcome"
        self.phase = "deck1"
        self.slide = 0
        self.count = 6
        self.input_visible = False
        self.element_ok = element_ok
        self.coords_ok = coords_ok
        self.js_ok = js_ok
        self.stuck = stuck

    # -- slide machinery ------------------------------------------------
    def _advance(self, label):
        if self.stuck:
            return
        if label == "PLAY!":
            if self.phase == "deck1":
                self.phase = "loading"
                self.slide = 0
            elif self.phase == "deck2":
                self.phase = "game"
                self.input_visible = True
            return
        if label == "NEXT" and self.phase in ("deck1", "deck2"):
            self.slide = min(self.slide + 1, 5)

    def _scan(self):
        if self.input_visible or self.phase == "loading":
            return None
        if self.phase in ("deck1", "deck2"):
            if self.slide < 5:
                return {"t": "NEXT", "x": 100, "y": 200}
            return {"t": "PLAY!", "x": 100, "y": 200}
        return None

    # -- click entry points --------------------------------------------
    def _on_element_click(self, label):
        if not self.element_ok:
            raise Exception("element click intercepted")
        self._advance(label)

    def _on_coords_click(self):
        if not self.coords_ok:
            return
        box = self._scan()
        if box:
            self._advance(box["t"])

    def _on_js_click(self, label):
        if not self.js_ok:
            return False
        if label == "SKIP":
            return False
        box = self._scan()
        if box and box["t"] == label:
            self._advance(label)
            return True
        return False

    # -- Playwright frame API ------------------------------------------
    def evaluate(self, js, arg=None):
        # Order matters: the slide-fingerprint JS contains
        # "findIndex" too, so check "owl-stage" (fingerprint)
        # before "findIndex" (index probe); the stuck-tap probe
        # contains both elementFromPoint patterns, so check the
        # probe's literal first.
        if "owl-stage" in js:  # _COUNTRYLE_SLIDE_FP_JS
            btns = "NEXT|Skip" if self._scan() else ""
            return (f"{self.url}|active={self.slide}/{self.count}"
                    f"|transform=translateX(0)|btns={btns}")
        if "findIndex" in js:  # _COUNTRYLE_SLIDE_IDX_JS
            return {"active": self.slide, "count": self.count}
        if "PLAY|START|CONTINUE|GO|GOT IT" in js:  # _COUNTRYLE_BTN_JS
            return self._scan()
        if "norm" in js:  # _COUNTRYLE_BTN_JS_CLICK_JS
            return self._on_js_click(arg)
        if "loading-mask" in js:
            return None
        if "elementFromPoint:" in js:
            return "elementFromPoint: DIV#.ad text=''"
        if "elementFromPoint(pt.x" in js:
            return None  # no interceptor
        if "document.querySelector('input, button')" in js:
            return True
        if js.strip() == "() => true":
            return True
        if "document.body.innerText" in js:
            return "You found the COUNTRYLE"
        if "querySelectorAll('li, [class*=row i]')" in js:
            return 5
        if "querySelectorAll(" in js and "li" in js:
            return 5
        return None

    def query_selector(self, sel):
        if sel == "input":
            return _FakeEl(self, is_input=True) if self.input_visible else None
        if ":text-is(" in sel:
            if "PLAY!" in sel:
                return _FakeEl(self, label="PLAY!")
            if "NEXT" in sel:
                return _FakeEl(self, label="NEXT")
            return None
        if "has-text('Play')" in sel or "has-text('PLAY')" in sel:
            return None  # PLAY loop finds no button (deck gone)
        return None

    def query_selector_all(self, sel):
        return []


class _FakeCountrylePage:
    def __init__(self, frame):
        self.mouse = _FakeMouse(self)
        self.keyboard = _FakeKeyboard()
        self.url = "https://countryle.com/"
        self.frames = [frame]
        self._frame = frame

    def _on_coords_click(self):
        self._frame._on_coords_click()

    def evaluate(self, js, arg=None):
        if "loading-mask" in js:
            return 0
        if "document.body.innerText" in js:
            return "You found the COUNTRYLE"
        return None

    def wait_for_timeout(self, ms):
        pass


class _FakePage:
    """Minimal fake page for the idle-trim helpers."""

    def __init__(self, evaluate=None):
        self.mouse = _FakeMouse(self)
        self.keyboard = _FakeKeyboard()
        self._evaluate = evaluate or (lambda js, arg=None: None)
        self.url = ""
        self.calls = []

    def _on_coords_click(self):
        pass

    def evaluate(self, js, arg=None):
        self.calls.append(js)
        return self._evaluate(js, arg)

    def wait_for_timeout(self, ms):
        pass

    def query_selector(self, sel):
        return None

    def query_selector_all(self, sel):
        return []


# ------------------------------------------------------- slide advance

class TestSlideAdvanceIter27(unittest.TestCase):
    def test_index_change_is_advance(self):
        self.assertTrue(R._countryle_slide_advanced(
            (0, 6), "fp0", (1, 6), "fp1"))

    def test_same_index_fp_change_is_NOT_advance(self):
        # The iter-26 latent defect: the fingerprint's href/btns
        # components change without a slide advance (ad churn /
        # navigations). Same active index => NOT advanced, even
        # when the fingerprint string differs.
        self.assertFalse(R._countryle_slide_advanced(
            (0, 6), "urlA|active=0|btns=X", (0, 6),
            "urlB|active=0|btns=Y"))

    def test_fingerprint_fallback_when_no_index(self):
        self.assertTrue(R._countryle_slide_advanced(
            None, "fp0", None, "fp1"))
        self.assertFalse(R._countryle_slide_advanced(
            None, "fp0", None, "fp0"))


# ------------------------------------------------------- deck ladder

class TestCountryleDeckLadderIter27(unittest.TestCase):
    def _run(self, frame):
        logs = []
        orig_dbg = R._dbg
        R._dbg = _dbg_recorder(logs)
        page = _FakeCountrylePage(frame)
        try:
            fr, status = R._countryle_run_deck(page, frame, "countryle")
        finally:
            R._dbg = orig_dbg
        return fr, status, logs

    def test_tap_advances_via_element_first_try(self):
        frame = _FakeCountryleFrame()
        _, status, logs = self._run(frame)
        # Deck pass 1: NEXT x5 via element, PLAY! tap leaves the
        # deck dismissed (phase 'loading') — no input yet.
        self.assertEqual(status, "deck-dismissed")
        taps = [l for l in logs if "onboarding tap" in l]
        self.assertEqual(len(taps), 6)
        self.assertTrue(all("via=element" in t for t in taps),
                        taps)
        self.assertTrue(all("advanced=True" in t for t in taps),
                        taps)
        self.assertFalse(any("via=js-click" in t for t in taps))

    def test_element_blocked_retries_with_coords(self):
        # Element click intercepted -> coords advances the slide.
        frame = _FakeCountryleFrame(element_ok=False)
        _, status, logs = self._run(frame)
        self.assertEqual(status, "deck-dismissed")
        taps = [l for l in logs if "onboarding tap" in l]
        self.assertTrue(any("via=coords advanced=True" in t
                            for t in taps), taps)
        self.assertFalse(any("via=js-click" in t for t in taps))

    def test_js_click_last_resort(self):
        # Element intercepted AND coords absorbed -> js-click lands.
        frame = _FakeCountryleFrame(element_ok=False, coords_ok=False)
        _, status, logs = self._run(frame)
        self.assertEqual(status, "deck-dismissed")
        taps = [l for l in logs if "onboarding tap" in l]
        self.assertTrue(any("via=js-click advanced=True" in t
                            for t in taps), taps)

    def test_no_method_advances_loud_fail(self):
        # Nothing advances (element intercepted, coords/js
        # absorbed, keys + SKIP absorbed): LOUD deck-stuck, not
        # a silent fall-through to an input wait.
        frame = _FakeCountryleFrame(element_ok=False, coords_ok=False,
                                    js_ok=False, stuck=True)
        _, status, logs = self._run(frame)
        self.assertEqual(status, "deck-stuck")
        self.assertTrue(any("onboarding FAILED" in l for l in logs),
                        logs)
        self.assertTrue(any("NOT proceeding" in l for l in logs),
                        logs)


# ------------------------------------------------------- deck re-appeared

class TestCountryleDeckReappearedIter27(unittest.TestCase):
    def test_deck_present_detector(self):
        # Deck on screen: NEXT offered + Owl carousel present.
        frame = _FakeCountryleFrame()
        self.assertTrue(R._countryle_deck_present(frame))
        # Game board: no onboarding button, no carousel signal.
        frame.phase = "game"
        frame.input_visible = True
        frame.count = 0
        self.assertFalse(R._countryle_deck_present(frame))

    def test_input_wait_reruns_deck_after_post_play_reload(self):
        # End-to-end at the unit level: the iter-26 shape.
        # Deck pass 1 taps through (deck dismissed, game still
        # loading) -> PLAY loop finds no button -> input wait
        # sees the deck BACK (post-PLAY /welcome reload) ->
        # ONE deck re-run -> input appears -> guesses play ->
        # win banner -> solved.
        logs = []
        orig_dbg = R._dbg
        R._dbg = _dbg_recorder(logs)
        saved = {}
        for _name in ("_close_modals", "_settle", "_idle_drift",
                      "_guesses", "_type_country_guess"):
            saved[_name] = getattr(R, _name)
        frame = _FakeCountryleFrame()
        # Arm the reload: the first deck-present check in the
        # input wait flips phase loading -> deck2.
        _orig_present = R._countryle_deck_present

        def _present_once(fr):
            if fr.phase == "loading":
                fr.phase = "deck2"
                fr.slide = 0
                return True
            return _orig_present(fr)

        try:
            R._close_modals = lambda page, _force=False: page
            R._settle = lambda page, base=0: None
            R._idle_drift = lambda page, **k: None
            R._guesses = lambda gid, ans: ["Oman"]
            R._type_country_guess = lambda p1, p2, name: (True, "picked")
            R._countryle_deck_present = _present_once
            page = _FakeCountrylePage(frame)
            ok, detail = R._s_countryle(page, "Oman", "countryle")
        finally:
            R._dbg = orig_dbg
            for _name, _fn in saved.items():
                setattr(R, _name, _fn)
            R._countryle_deck_present = _orig_present
        self.assertTrue(ok, detail)
        self.assertTrue(any("re-appeared during" in l for l in logs),
                        logs)
        self.assertTrue(any("input appeared after deck re-run" in l
                            for l in logs), logs)


# ------------------------------------------------------- Capital recolour

class TestCapitalRecolourIter27(unittest.TestCase):
    def test_recolour_line_emitted_when_marker_also_rose(self):
        # The iter-25/26 shape: markers 4->5 AND the dot
        # recoloured. The old short-circuit suppressed the line
        # here; the fix must emit it AND still return True.
        logs = []
        ok = R._worldle_capital_marker_or_recolour(
            {"markers": 4}, {"markers": 5},
            True,
            "recolour: dot id='right-811' at (1079.7,437.9) "
            "recoloured blue->green (before=['#5252ff'] "
            "after=['#5CE50D'] near_click=True)",
            log=logs.append)
        self.assertTrue(ok)
        self.assertEqual(len(logs), 1)
        self.assertIn("worldle capital pin recolour signal: ", logs[0])
        self.assertIn("recolour: dot id='right-811'", logs[0])
        self.assertIn("recoloured blue->green", logs[0])

    def test_no_line_when_no_recolour(self):
        logs = []
        ok = R._worldle_capital_marker_or_recolour(
            {"markers": 4}, {"markers": 5}, False, "no recolour",
            log=logs.append)
        self.assertTrue(ok)  # marker alone still counts
        self.assertEqual(logs, [])

    def test_no_marker_no_recolour_is_false(self):
        logs = []
        ok = R._worldle_capital_marker_or_recolour(
            {"markers": 4, "sig": "s"}, {"markers": 4, "sig": "s"},
            False, "no recolour", log=logs.append)
        self.assertFalse(ok)
        self.assertEqual(logs, [])

    def test_dot_recoloured_pure(self):
        before = [{"id": "right-811", "x": 1079.7, "y": 437.9,
                   "fill": "#5252ff", "computed_fill": "rgb(82, 82, 255)",
                   "bg": "", "resolved_fill": ""}]
        after = [{"id": "right-811", "x": 1079.7, "y": 437.9,
                  "fill": "#5CE50D", "computed_fill": "rgb(92, 229, 13)",
                  "bg": "", "resolved_fill": ""}]
        ok, det = R._worldle_capital_dot_recoloured(
            before, after, 1079.7, 437.9)
        self.assertTrue(ok)
        self.assertIn("recoloured blue->green", det)
        # No change -> no recolour.
        ok2, _ = R._worldle_capital_dot_recoloured(before, before)
        self.assertFalse(ok2)

    def test_marker_appeared_pure(self):
        self.assertTrue(R._worldle_capital_marker_appeared(
            {"markers": 4}, {"markers": 5}))
        self.assertFalse(R._worldle_capital_marker_appeared(
            {"markers": 4, "sig": "s"}, {"markers": 4, "sig": "s"}))

    def test_short_circuit_is_gone_from_source(self):
        src = inspect.getsource(R._s_worldle_play_capital)
        self.assertIn("_worldle_capital_marker_or_recolour", src)
        self.assertNotIn("if _marker_appeared(b, a):", src)


# ------------------------------------------------------- idle trims

class TestIdleTrimIter27(unittest.TestCase):
    def test_framed_wait_board_ready_early_exit(self):
        calls = {"n": 0}

        def _ready(page):
            calls["n"] += 1
            return calls["n"] >= 2

        orig = R._framed_board_ready
        R._framed_board_ready = _ready
        try:
            self.assertTrue(R._framed_wait_board_ready(_FakePage(),
                                                       cap_ms=6000))
        finally:
            R._framed_board_ready = orig
        self.assertEqual(calls["n"], 2)

    def test_framed_wait_board_ready_timeout(self):
        orig = R._framed_board_ready
        R._framed_board_ready = lambda page: False
        try:
            self.assertFalse(R._framed_wait_board_ready(_FakePage(),
                                                        cap_ms=50))
        finally:
            R._framed_board_ready = orig

    def test_framed_open_mode_uses_poll_not_fixed_sleep(self):
        src = inspect.getsource(R._framed_open_mode)
        self.assertIn("_framed_wait_board_ready", src)
        self.assertNotIn("wait_for_timeout(6000)", src)

    def test_marveldle_wait_inputs_early_exit(self):
        calls = {"n": 0}

        def _ev(js, arg=None):
            calls["n"] += 1
            return 3 if calls["n"] >= 3 else 0

        page = _FakePage(evaluate=_ev)
        self.assertEqual(R._marveldle_wait_inputs(page), 3)
        self.assertEqual(calls["n"], 3)

    def test_marveldle_wait_inputs_timeout(self):
        page = _FakePage(evaluate=lambda js, arg=None: 0)
        self.assertEqual(
            R._marveldle_wait_inputs(page, cap_ms=50), 0)

    def test_marveldle_wait_menu_settled_url_change(self):
        page = _FakePage(evaluate=lambda js, arg=None: False)
        page.url = "https://marveldle.com/game"
        self.assertTrue(R._marveldle_wait_menu_settled(
            page, "https://marveldle.com/", "Comics", cap_ms=1500))

    def test_marveldle_wait_menu_settled_label_gone(self):
        page = _FakePage(evaluate=lambda js, arg=None: True)
        page.url = "https://marveldle.com/"
        self.assertTrue(R._marveldle_wait_menu_settled(
            page, "https://marveldle.com/", "Comics", cap_ms=1500))

    def test_marveldle_wait_menu_settled_timeout(self):
        page = _FakePage(evaluate=lambda js, arg=None: False)
        page.url = "https://marveldle.com/"
        self.assertFalse(R._marveldle_wait_menu_settled(
            page, "https://marveldle.com/", "Comics", cap_ms=50))

    def test_marveldle_uses_polls_not_fixed_sleeps(self):
        src = inspect.getsource(R._s_marveldle)
        self.assertIn("_marveldle_wait_menu_settled", src)
        self.assertIn("_marveldle_wait_inputs", src)
        self.assertNotIn("wait_for_timeout(1500)", src)
        self.assertNotIn("wait_for_timeout(2000)", src)
        self.assertNotIn("wait_for_timeout(4000)", src)

    def test_colorfle_modal_poll_exits_when_hidden(self):
        calls = {"n": 0}
        waits = []

        class _P(_FakePage):
            def wait_for_timeout(self, ms):
                waits.append(ms)

        def _ev(js, arg=None):
            calls["n"] += 1
            # First evaluate hides the modal; the hidden-check
            # then reports hidden at once.
            return True

        page = _P(evaluate=_ev)
        R._dismiss_colorfle_modal(page)
        self.assertEqual(waits, [])

    def test_colorfle_modal_no_fixed_800_sleep(self):
        src = inspect.getsource(R._dismiss_colorfle_modal)
        self.assertIn("_COLORFLE_MODAL_HIDDEN_JS", src)
        self.assertNotIn("wait_for_timeout(800)", src)

    def test_colorfle_per_guess_pacing_unchanged(self):
        # Typing speed / per-guess pacing is NOT an idle trim:
        # the 420ms click settle + 3400ms post-ENTER wait stay.
        src = inspect.getsource(R.solve_colorfle)
        self.assertIn("wait_for_timeout(420)", src)
        self.assertIn("wait_for_timeout(3400)", src)


# ------------------------------------------------------- new JS payloads

def _run_node(js_body, prelude=""):
    """Run js_body under node with `prelude` defining `document`;
    returns stdout. Skips when node is missing."""
    if not shutil.which("node"):
        raise unittest.SkipTest("node not available")
    with tempfile.NamedTemporaryFile("w", suffix=".js",
                                     delete=False) as fh:
        fh.write(prelude + "\n" + js_body)
        path = fh.name
    try:
        out = subprocess.run(["node", path], capture_output=True,
                             text=True, timeout=15)
    finally:
        Path(path).unlink(missing_ok=True)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


class TestNewJsPayloadsIter27(unittest.TestCase):
    def test_slide_idx_js_syntax(self):
        src = "const fn = " + R._COUNTRYLE_SLIDE_IDX_JS + ";\n"
        out = _run_node(
            src + """
const document = { querySelectorAll: () => [
  { classList: { contains: c => c === 'active' } },
  { classList: { contains: () => false } },
  { classList: { contains: c => c === 'active' } },
  { classList: { contains: () => false } },
  { classList: { contains: () => false } },
  { classList: { contains: () => false } },
] };
console.log(JSON.stringify(fn()));
""")
        self.assertEqual(out, '{"active":0,"count":6}')

    def test_marveldle_menu_settled_js(self):
        src = "const fn = " + R._MARVELDLE_MENU_SETTLED_JS + ";\n"
        out = _run_node(
            src + """
let labelPresent = true;
const document = { querySelectorAll: () => labelPresent
  ? [{ offsetParent: {}, innerText: 'Comics' }] : [] };
console.log(fn('Comics'));
labelPresent = false;
console.log(fn('Comics'));
""")
        self.assertEqual(out, "false\ntrue")

    def test_colorfle_modal_hidden_js(self):
        src = "const fn = " + R._COLORFLE_MODAL_HIDDEN_JS + ";\n"
        out = _run_node(
            src + """
let modal = { offsetParent: {}, style: { display: '' } };
const document = { querySelector: () => modal };
console.log(fn());
modal = { offsetParent: null, style: { display: '' } };
console.log(fn());
modal = null;
console.log(fn());
""")
        self.assertEqual(out, "false\ntrue\ntrue")

    def test_no_adjacent_string_literals_in_new_js(self):
        # The iter-24 2-matcher bug: adjacent string literals in
        # a JS probe are a SyntaxError. node --check on each
        # payload is the guard; this also asserts the payloads
        # are single JS expressions, not stitched fragments.
        for _js in (R._COUNTRYLE_SLIDE_IDX_JS,
                    R._MARVELDLE_MENU_SETTLED_JS,
                    R._COLORFLE_MODAL_HIDDEN_JS):
            _run_node("const fn = " + _js + ";")


if __name__ == "__main__":
    unittest.main()
