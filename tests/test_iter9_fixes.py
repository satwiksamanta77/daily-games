"""Fix-loop iteration 9 regression tests.

Rerun #8 (run 39029968074, sha e29aef3) still failed on globle /
framed / worldle after the iter7 fixes. Diagnosis from the #8
artifacts (job logs + debug logs + result.json + shot_win.png):

- globle: the PAGE's own answer request 404'd (day=2026-10-17 &
  timeZone=Kiritimati at 11:47 UTC = 01:47 Oct 18 real Kiritimati).
  Iter7 fixed only OUR direct fetch, not the page's request.
- worldle: iter7 DELETED `def _s_worldle_play_language` (its body
  became dead code inside _worldle_phrase_pick) -> NameError crash;
  capital still never clicked the 2. PINPOINT tab (markers 4->4);
  trivia still broke on empty `shown` before the tab-try logic.
- framed: rewarded ad never loaded (overlays=[] for 45s) — defer
  honestly instead of FAIL.
- batterup: shot_win STILL pre-submit (dropdown open, cells empty);
  the wait gated on dropdown state, which never clears.

Run:  python3 -m unittest tests.test_iter9_fixes -v
All tests are synthetic/local (fake pages / node fixture DOM / no
browser, no network except TestGloblePageTzLive which does read-only
GETs against the Globle endpoint).
"""
import inspect
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent.parent
sys.path.insert(0, str(HERE))

import answers as A  # noqa: E402
import runner  # noqa: E402


class _FakeKeyboard:
    def __init__(self):
        self.pressed = []

    def press(self, key):
        self.pressed.append(key)


class _FakePage:
    """Minimal Playwright-page stand-in: evaluate() returns canned
    data, keyboard records presses, wait_for_timeout is a no-op."""

    def __init__(self, evaluate_result=None, frames=None):
        self._ev = evaluate_result if evaluate_result is not None else {}
        self.frames = frames if frames is not None else []
        self.keyboard = _FakeKeyboard()
        self.evaluate_calls = 0

    def evaluate(self, _js, *args):
        self.evaluate_calls += 1
        return dict(self._ev)

    def wait_for_timeout(self, _ms):
        pass


class TestGloblePageTz(unittest.TestCase):
    """The Globle PAGE tz must be a zone whose REAL local date ==
    the target date (the server only serves day == real-today-in-
    timeZone)."""

    def test_kiritimati_rolled_is_not_used(self):
        # On 2026-10-17 real Kiritimati has already rolled to 10-18,
        # so the page tz must NOT be Kiritimati for that target day.
        tz = A._globle_page_tz(date(2026, 10, 17), "Pacific/Kiritimati")
        self.assertNotEqual(tz, "Pacific/Kiritimati")
        self.assertEqual(datetime.now(ZoneInfo(tz)).date(),
                         date(2026, 10, 17))

    def test_prefers_browser_tz_when_it_matches(self):
        tokyo_today = datetime.now(ZoneInfo("Asia/Tokyo")).date()
        self.assertEqual(
            A._globle_page_tz(tokyo_today, "Asia/Tokyo"), "Asia/Tokyo")

    def test_fallback_when_no_zone_matches(self):
        self.assertEqual(
            A._globle_page_tz(date(2030, 1, 1), "Pacific/Kiritimati"),
            "Pacific/Kiritimati")

    def test_runner_uses_page_tz_for_globle(self):
        src = inspect.getsource(runner.run_one)
        self.assertIn("_globle_page_tz", src)
        self.assertIn('if gid == "globle"', src)
        self.assertIn("timezone_id=_page_tz", src)


class TestGloblePageTzLive(unittest.TestCase):
    """Live endpoint: the page-tz for the CURRENT target day must
    200 for that day (the exact request the page itself will make)."""

    def test_page_tz_request_200s(self):
        import requests
        d = A.target_date()
        tz = A._globle_page_tz(d, "Pacific/Kiritimati")
        r = requests.get(
            f"https://globle-game.com/answer?day={d.isoformat()}",
            params={"list": 197, "timeZone": tz},
            headers={"User-Agent": "WordSolverX Video"}, timeout=20)
        self.assertEqual(r.status_code, 200, f"page tz {tz} for {d}")


class TestWorldleLanguageRestored(unittest.TestCase):
    """Iter7 deleted `def _s_worldle_play_language` (NameError crash
    in rerun #8). It must exist, with PHRASE and ADVANCED split."""

    def test_function_defined(self):
        self.assertTrue(callable(runner._s_worldle_play_language))

    def test_phrase_advanced_split(self):
        src = inspect.getsource(runner._s_worldle_play_language)
        # The conflated code branch is gone (the string survives only
        # inside a comment explaining the old code).
        self.assertNotIn("\n        if _phrase or _advanced:\n", src)
        self.assertIn("\n        if _phrase:\n", src)
        self.assertIn("\n        if _advanced:\n", src)
        self.assertIn("ADVANCED:", src)
        self.assertIn("PHRASE:", src)

    def test_day_phrases_used(self):
        src = inspect.getsource(runner._s_worldle_play_language)
        self.assertIn('day.get("phrases")', src)
        self.assertIn("common/languages.json", src)
        self.assertIn("draggable", src)


class TestWorldlePhraseMap(unittest.TestCase):
    """Phrase data source: the game's own common/languages.json
    [{languageCode, phrases:{myNameIs}}]."""

    _DOC = [
        {"languageCode": "ewe",
         "phrases": {"myNameIs": "N\u0254\u0254y\u025b la me"}},
        {"languageCode": "kbp",
         "phrases": {"myNameIs": "\u00c9w\u00e1 m\u00ed k\u1eb9\u0301"}},
        {"languageCode": "fra", "phrases": {"myNameIs": "Je m'appelle"}},
        {"languageCode": "deu", "phrases": {}},
        {"languageCode": "zzz"},
    ]

    def test_maps_day_languages(self):
        got = A._worldle_phrase_map(self._DOC, ["ewe", "kbp"])
        self.assertEqual(got["ewe"], "N\u0254\u0254y\u025b la me")
        self.assertEqual(got["kbp"], "\u00c9w\u00e1 m\u00ed k\u1eb9\u0301")
        self.assertNotIn("fra", got)  # not a day language
        self.assertNotIn("deu", got)  # empty phrases
        self.assertNotIn("zzz", got)  # no phrases key

    def test_case_insensitive_codes(self):
        got = A._worldle_phrase_map(self._DOC, ["EWE"])
        self.assertEqual(got["ewe"], "N\u0254\u0254y\u025b la me")


class TestCapitalPinpointTab(unittest.TestCase):
    """Capital must click the 2. PINPOINT tab before pinning
    (ANALYSIS6 §2a; rerun #8: markers 4->4, no PINPOINT log line)."""

    def test_tab_click_in_capital_path(self):
        # Iter-14 refactor (ANALYSIS10): the tab click + activation
        # verification moved into _worldle_capital_activate_pinpoint;
        # the capital path must call it BEFORE the pin probe/click,
        # and must refuse to pin when activation fails (the iter-13
        # "proceed anyway" path is gone).
        src = inspect.getsource(runner._s_worldle_play_capital)
        self.assertIn("PINPOINT", src)
        self.assertIn("_worldle_capital_activate_pinpoint(page, gid)", src)
        self.assertLess(src.index("_worldle_capital_activate_pinpoint"),
                        src.index("_worldle_pin_probe"))
        helper = inspect.getsource(runner._worldle_capital_activate_pinpoint)
        self.assertIn("PINPOINT", helper)
        self.assertIn("CAPITAL_TAB_CLICK_JS", helper)
        self.assertIn("NEVER activated", helper)


class TestTriviaTabTryOnEmpty(unittest.TestCase):
    """Tab-try logic must run when `shown` is empty instead of
    breaking (ANALYSIS6 §2b defect 2)."""

    def test_try_next_tab_defined_and_used_on_empty(self):
        src = inspect.getsource(runner._s_worldle_play_trivia)
        self.assertIn("def _try_next_tab", src)
        i = src.index("if not shown:\n            # Iter 9")
        self.assertIn("_try_next_tab", src[i:i + 400])

    def test_general_prompt_matches_blank_rule_not_question_rule(self):
        # The GENERAL prompt has no '?' — the old ?-only scanner
        # missed it; the blank-run rule (_{3,}) catches it.
        line = ("The national football team of Togo is popularly "
                "known by the nickname the _______")
        self.assertIsNone(re.search(r"\?", line))
        self.assertIsNotNone(re.search(r"_{3,}", line))


class TestAreaStepperSelection(unittest.TestCase):
    """Area stepper selection (node simulation on a fixture DOM):
    nav arrows excluded by data-testid/aria-label, +/- steppers
    picked, tightest widget pair wins."""

    _NODE_FIXTURE = r"""
class El {
  constructor(tag, attrs, text, parent) {
    this.tagName = tag; this._attrs = attrs || {};
    this.innerText = text || ''; this.parentElement = parent || null;
    this.offsetParent = {}; this.children = [];
    if (parent) parent.children.push(this);
  }
  getAttribute(k) { return this._attrs[k] || ''; }
  closest(sel) {
    const wants = sel.split(',').map(s => s.trim().toLowerCase());
    let n = this;
    while (n) {
      if (wants.includes(n.tagName.toLowerCase())) return n;
      n = n.parentElement;
    }
    return null;
  }
}
const root = new El('BODY', {}, '', null);
const main = new El('MAIN', {}, '', root);
const nav = new El('NAV', {}, '', root);
// Standalone nav arrows OUTSIDE <nav> (the rerun #6 failure mode):
// icon-only buttons with arrow testids.
const prevArrow = new El('BUTTON', {'data-testid': 'previous-arrow-button'}, '', main);
const nextArrow = new El('BUTTON', {'data-testid': 'next-arrow-button'}, '', main);
// Stray icon-only button elsewhere on the page (settings gear).
const gear = new El('BUTTON', {'aria-label': 'Settings'}, '', main);
// The area widget: +/- steppers + display, deep in a container.
const widget = new El('DIV', {}, '', main);
const inner = new El('DIV', {}, '', widget);
const plus = new El('BUTTON', {'aria-label': 'Increase'}, '+', inner);
const disp = new El('SPAN', {}, '56785', inner);
const minus = new El('BUTTON', {'aria-label': 'Decrease'}, '\u2212', inner);
const docEls = [prevArrow, nextArrow, gear, plus, minus];
const document = { querySelectorAll: (sel) => docEls.filter(e =>
  sel.split(',').map(s => s.trim()).includes(
    e.tagName.toLowerCase() === 'div' ? 'div[role=button]' : 'button')) };
FRAGMENT
console.log(JSON.stringify(st.map(e => (e.innerText || '').trim())));
"""

    def _run_fragment(self, fixture_js):
        if not shutil.which("node"):
            self.skipTest("node not available")
        frag = runner._AREA_STEPPER_SELECT_JS
        js = fixture_js.replace("FRAGMENT", frag)
        with tempfile.NamedTemporaryFile(
                "w", suffix=".js", delete=False) as f:
            f.write(js)
            path = f.name
        try:
            out = subprocess.run(["node", path], capture_output=True,
                                 text=True, timeout=30)
        finally:
            Path(path).unlink(missing_ok=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout.strip())

    def test_plus_minus_pair_beats_nav_arrows(self):
        got = self._run_fragment(self._NODE_FIXTURE)
        self.assertEqual(sorted(got), sorted(["+", "\u2212"]))

    def test_icon_only_steppers_beat_nav_arrows(self):
        fixture = self._NODE_FIXTURE.replace(
            "const plus = new El('BUTTON', {'aria-label': 'Increase'}, '+', inner);",
            "const plus = new El('BUTTON', {}, '', inner);").replace(
            "const minus = new El('BUTTON', {'aria-label': 'Decrease'}, '\\u2212', inner);",
            "const minus = new El('BUTTON', {}, '', inner);")
        got = self._run_fragment(fixture)
        # Both chosen steppers are the icon-only widget buttons
        # (empty text), not the nav arrows or the gear.
        self.assertEqual(got, ["", ""])


class TestAreaParseInputs(unittest.TestCase):
    """Display read from live <input> values (iter 9)."""

    def test_inputs_pure(self):
        self.assertEqual(
            runner._worldle_area_parse_display({"inputs": "56785"}),
            (56785, "inputs:single"))

    def test_inputs_small_number_allowed(self):
        self.assertEqual(
            runner._worldle_area_parse_display({"inputs": "12"}),
            (12, "inputs:single"))

    def test_inputs_win_over_common(self):
        # Iter 11 priority: a live <input> value inside the widget
        # beats container text, which may hold the TARGET/prompt
        # number rather than the current display value.
        self.assertEqual(
            runner._worldle_area_parse_display(
                {"common": "1,000 km2", "inputs": "5"}),
            (5, "inputs:single"))


class TestFramedDeferred(unittest.TestCase):
    """Poster: ad-iframe-load probe + honest DEFERRED (not FAIL)."""

    def test_ad_state_reports_ad_load(self):
        page = _FakePage({
            "overlays": [], "close": [], "googletag": None,
            "ad_iframes": [{"src": "https://googleads.g.doubleclick.net/x",
                            "vis": True, "adLike": True}],
            "videos": [{"src": "", "dur": 15, "paused": False,
                        "vis": True}],
            "ima": True, "ad_loaded": True,
        }, frames=[type("F", (), {"url": "https://framed.wtf/poster"})()])
        got = runner._framed_reward_ad_state(page)
        self.assertTrue(got["ad_loaded"])
        self.assertTrue(got["ima"])
        self.assertEqual(len(got["videos"]), 1)
        self.assertEqual(got["frame_urls"],
                         ["https://framed.wtf/poster"])

    def test_deferred_in_code_paths(self):
        src_modes = inspect.getsource(runner.solve_framed_modes)
        self.assertIn('"deferred": True', src_modes)
        self.assertIn("DEFERRED", src_modes)
        self.assertIn("ad_loaded", src_modes)
        src_all = inspect.getsource(runner.run_framed_all)
        self.assertIn("DEFERRED", src_all)
        src_asm = inspect.getsource(runner._assemble_framed)
        self.assertIn("skipping final assembly", src_asm)
        self.assertIn("DEFERRED", src_asm)
        # The assembly gate still requires every mode solved.
        self.assertIn('all(d.get("solved")', src_all)


class TestBatterupPopulatedWait(unittest.TestCase):
    """Post-submit wait keys on a POPULATED guess row, never on
    dropdown state (reruns #5-#8: dropdown never cleared)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_here = runner.HERE
        runner.HERE = Path(self._tmp.name)

    def tearDown(self):
        runner.HERE = self._old_here
        self._tmp.cleanup()

    def test_populated_breaks_despite_open_dropdown(self):
        page = _FakePage({"rows": ["Wyatt Langford"], "dropdown_open": True,
                          "populated": True, "populated_rows": 1,
                          "feedback_tiles": 6})
        import time as _t
        t0 = _t.time()
        rows = runner._attr_wait_post_submit(page, "batterup-t9a",
                                             timeout_s=8.0)
        self.assertLess(_t.time() - t0, 1.0)
        self.assertEqual(rows, ["Wyatt Langford"])

    def test_dropdown_closed_alone_is_not_enough(self):
        page = _FakePage({"rows": [], "dropdown_open": False,
                          "populated": False, "populated_rows": 0,
                          "feedback_tiles": 0})
        rows = runner._attr_wait_post_submit(page, "batterup-t9b",
                                             timeout_s=0.2)
        self.assertEqual(rows, [])

    def test_dismiss_blurs_and_clears(self):
        page = _FakePage({"ok": True})
        runner._attr_dismiss_suggestion(page)
        self.assertIn("ControlOrMeta+a", page.keyboard.pressed)
        self.assertIn("Backspace", page.keyboard.pressed)
        self.assertIn("Escape", page.keyboard.pressed)
        self.assertGreater(page.evaluate_calls, 0)

    def test_probe_narrowed_to_guess_rows(self):
        src = inspect.getsource(runner._attr_post_submit_probe)
        self.assertIn("populated", src)
        self.assertIn("[class*=guess i]", src)
        # The old junk-pulling selector is gone.
        self.assertNotIn("[class*=row i], [class*=tile i], li", src)


class TestSkipGuardPreserved(unittest.TestCase):
    """Iter8 skip-guard fix must survive: every skipped game writes
    result.json skipped:true."""

    def test_skip_writer_still_wired(self):
        self.assertTrue(callable(runner._write_skip_result))
        self.assertIn("_write_skip_result",
                      inspect.getsource(runner.run_one))


if __name__ == "__main__":
    unittest.main()
