"""Fix-loop iteration 19 tests — WORLDLE workstream (evidence-grounded
in ANALYSIS16 §§2(e)(f)(g), §6 items 5-7, 10, §7 Worldle items).

1. Worldle Capital dot-name (P0, ANALYSIS16 §2e): the iter-18
   ANALYSIS said "dot-name never attempted: zero log lines" — but
   the iter-18 LOG proves the picker RAN (line 104:
   method=bbox-fit-in-fill, dots=4) and dot-name never MATCHED.
   Two root causes: (a) the probe let EVERY dot independently
   claim its nearest <text>, so two dots near one label both
   claimed it and the ambiguity rule refused a real match
   (Kuwait's dots sit close together, 60px radii overlap);
   (b) a (source, value) pair shared by 2+ dots — e.g. one <g>
   wrapping all dots + labels giving every dot the same
   parent_text — made ALL dots "match" and tripped the same
   ambiguity rule, poisoning a unique per-dot label match.
   Fixes: greedy mutual-nearest label assignment (one label to
   at most one dot), a shared-source exclusion (a value shared
   by 2+ dots is group evidence, not dot evidence), more name
   sources (group <title>, sibling <text>/<title>, grandparent
   <g> data/title/text, HTML overlay labels), and an
   UNCONDITIONAL dot-name attempt log line carrying every dot's
   name evidence. Nearest-dot-to-projection stays FORBIDDEN.
2. Teuteuf banner probe (P1, ANALYSIS16 §2f): the log said
   `absent` ~30x while the banner sat in every frame. Root
   causes: the text match missed a title split across sibling
   spans with no whitespace node ('TeuteufEdu'), a brand mark
   whose text lives in alt/aria-label/title, and a CROSS-ORIGIN
   iframe whose text no same-origin probe can read. Fixes:
   space-stripped signature ('teuteufedu'), descendant
   alt/aria-label/title folded into the signature, and a
   banner-shaped unreadable iframe reported as
   'present-iframe-unreadable' (never a lying 'absent') with a
   real corner-click dismiss + iframe-hide fallback.
3. G4 live-rounds reader (P1, ANALYSIS16 §2g): the reader
   returned None while frame gp_0355 proves the dropdown IS open
   with all 13 entries. Root causes: offsetParent-based
   visibility (a fixed-position dropdown portal has a null
   offsetParent and was filtered out of the toggle, container
   AND leaf searches), a nav/header/footer ancestry exclusion
   (the switcher lives inside the page header/nav), and a
   single attempt during the Country round. Fixes: rect-based
   visibility everywhere, content-scored container picking
   (leaves mapping to known round labels), and a 3-attempt loop
   (scan-first, toggle+scan, Escape+toggle+scan).
4. Area (P2, ANALYSIS16 §6.10): the iter-18 number path ran
   the stepper probe ON the comparison widget (its two image
   buttons were the "stepper candidates", log line 250) and
   reported "stepper failed" — while the comparison probe-click
   had actually answered it. Fix: the number path re-attempts
   the DELIBERATE comparison branch first; the stepper probe
   runs only when no comparison widget is showing. Win-check
   wording now describes the comparison pick, not "first guess
   correct==answer". Outcome logic unchanged.

All tests are synthetic/local: no network, no browser, no CI run.
"""
import inspect
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import runner as R


class _FakeMouse:
    def __init__(self):
        self.clicks = []
        self.moves = []
        self.downs = 0
        self.ups = 0

    def click(self, x, y):
        self.clicks.append((float(x), float(y)))

    def move(self, x, y, steps=None):
        self.moves.append((float(x), float(y)))

    def down(self):
        self.downs += 1

    def up(self):
        self.ups += 1


class _FakeKeyboard:
    def press(self, *a, **k):
        pass


class _FakePage:
    """Minimal Playwright-page stand-in: evaluate dispatch by JS
    marker, a recording mouse, no-op waits."""

    def __init__(self, evaluate=None):
        self.mouse = _FakeMouse()
        self.keyboard = _FakeKeyboard()
        self._evaluate = evaluate or (lambda js, arg=None: None)
        self._qp_gid = "test-iter19"
        self.calls = []
        self.waits = []

    def evaluate(self, js, arg=None):
        self.calls.append(js)
        return self._evaluate(js, arg)

    def wait_for_timeout(self, ms):
        self.waits.append(ms)


def _dbg_recorder(logs):
    def _rec(gid, msg):
        logs.append(f"[{gid}] {msg}")
    return _rec


# ---------------------------------------------------------------- Capital

class TestCapitalDotNameIter19(unittest.TestCase):
    DOTS = [
        {"x": 1085.0, "y": 285.0, "r": 8.0, "fill": "blue",
         "cls": "", "id": "", "data": {}, "title": "", "aria": "",
         "label": "", "label_dist": None, "parent_text": "",
         "parent_data": {}, "group_title": "", "sibling_text": "",
         "overlay_label": "", "grandparent_text": "",
         "grandparent_title": "", "grandparent_data": {}},
        {"x": 1010.0, "y": 440.0, "r": 8.0, "fill": "blue",
         "cls": "", "id": "", "data": {}, "title": "", "aria": "",
         "label": "", "label_dist": None, "parent_text": "",
         "parent_data": {}, "group_title": "", "sibling_text": "",
         "overlay_label": "", "grandparent_text": "",
         "grandparent_title": "", "grandparent_data": {}},
        {"x": 1090.0, "y": 435.0, "r": 8.0, "fill": "blue",
         "cls": "", "id": "", "data": {}, "title": "", "aria": "",
         "label": "", "label_dist": None, "parent_text": "",
         "parent_data": {}, "group_title": "", "sibling_text": "",
         "overlay_label": "", "grandparent_text": "",
         "grandparent_title": "", "grandparent_data": {}},
        {"x": 1110.0, "y": 645.0, "r": 8.0, "fill": "blue",
         "cls": "", "id": "", "data": {}, "title": "", "aria": "",
         "label": "", "label_dist": None, "parent_text": "",
         "parent_data": {}, "group_title": "", "sibling_text": "",
         "overlay_label": "", "grandparent_text": "",
         "grandparent_title": "", "grandparent_data": {}},
    ]
    FIT = (1072.5, 422.7)

    def _probe(self, **kw):
        base = {"found": True, "dots": [dict(d) for d in self.DOTS],
                "n_paths": 29, "n_dots": 4, "in_fill": False,
                "corrected": None}
        base.update(kw)
        return base

    def test_dot_name_via_group_title(self):
        # The name lives in a <title> child of the parent <g>.
        dots = [dict(d) for d in self.DOTS]
        dots[2] = dict(dots[2], group_title="Kuwait City")
        got = R._worldle_capital_pick_pin_target(
            self._probe(dots=dots), self.FIT, "Kuwait City")
        self.assertIsNotNone(got)
        self.assertEqual(got["method"], "dot-name")
        self.assertEqual((got["x"], got["y"]), (1090.0, 435.0))
        self.assertIn("group_title", got["detail"])

    def test_dot_name_via_sibling_text(self):
        # The name lives in a sibling <text> of the same group.
        dots = [dict(d) for d in self.DOTS]
        dots[0] = dict(dots[0], sibling_text="Kuwait City")
        got = R._worldle_capital_pick_pin_target(
            self._probe(dots=dots), self.FIT, "Kuwait City")
        self.assertIsNotNone(got)
        self.assertEqual(got["method"], "dot-name")
        self.assertEqual((got["x"], got["y"]), (1085.0, 285.0))
        self.assertIn("sibling_text", got["detail"])

    def test_dot_name_via_overlay_label(self):
        # The name lives in an HTML overlay label over the map.
        dots = [dict(d) for d in self.DOTS]
        dots[1] = dict(dots[1], overlay_label="Kuwait City")
        got = R._worldle_capital_pick_pin_target(
            self._probe(dots=dots), self.FIT, "Kuwait City")
        self.assertIsNotNone(got)
        self.assertEqual(got["method"], "dot-name")
        self.assertEqual((got["x"], got["y"]), (1010.0, 440.0))
        self.assertIn("overlay_label", got["detail"])

    def test_dot_name_via_grandparent_data(self):
        dots = [dict(d) for d in self.DOTS]
        dots[3] = dict(dots[3],
                       grandparent_data={"city": "Kuwait City"})
        got = R._worldle_capital_pick_pin_target(
            self._probe(dots=dots), self.FIT, "Kuwait City")
        self.assertIsNotNone(got)
        self.assertEqual(got["method"], "dot-name")
        self.assertEqual((got["x"], got["y"]), (1110.0, 645.0))

    def test_shared_parent_text_no_longer_poisons_unique_label(self):
        # THE iter-18 failure shape: one <g> wraps ALL dots, so
        # every dot shares the same parent_text (all city names),
        # while ONE dot has its own unique label naming the
        # capital. Old picker: all 4 dots "matched" via the shared
        # parent_text -> ambiguity -> None (fell through to
        # bbox-fit-in-fill). New picker: the shared parent_text is
        # group evidence, dropped; the unique label match stands.
        dots = [dict(d, parent_text="Kuwait City Al Jahra Hawally")
                for d in self.DOTS]
        dots[2] = dict(dots[2], label="Kuwait City", label_dist=18.0)
        got = R._worldle_capital_pick_pin_target(
            self._probe(dots=dots), self.FIT, "Kuwait City")
        self.assertIsNotNone(got)
        self.assertEqual(got["method"], "dot-name")
        self.assertEqual((got["x"], got["y"]), (1090.0, 435.0))
        self.assertIn("label", got["detail"])

    def test_shared_label_on_two_dots_still_ambiguous(self):
        # Two dots carrying the SAME label value: the value is
        # shared, so neither dot is identified -> None, never a
        # coin-flip click.
        dots = [dict(d) for d in self.DOTS]
        dots[0] = dict(dots[0], label="Kuwait City", label_dist=12.0)
        dots[2] = dict(dots[2], label="Kuwait City", label_dist=18.0)
        self.assertIsNone(R._worldle_capital_pick_pin_target(
            self._probe(dots=dots), self.FIT, "Kuwait City"))

    def test_two_distinct_labels_naming_capital_still_ambiguous(self):
        # Distinct CONTAINED values that BOTH name the capital
        # (e.g. "Greater Kuwait City" vs "Kuwait City metro",
        # neither an exact match) still hit the ambiguity backstop
        # -> None. (An EXACT match beats contained matches — that
        # is the strongest evidence, not ambiguity.)
        dots = [dict(d) for d in self.DOTS]
        dots[0] = dict(dots[0], label="Greater Kuwait City",
                       label_dist=12.0)
        dots[2] = dict(dots[2], label="Kuwait City metro",
                       label_dist=18.0)
        self.assertIsNone(R._worldle_capital_pick_pin_target(
            self._probe(dots=dots), self.FIT, "Kuwait City"))

    def test_nearest_dot_to_projection_still_forbidden(self):
        # Unnamed dots + a nearby in-fill point: the picker must
        # return the in-fill point, NEVER the nearest dot, even
        # when a dot sits closer to the fit point than the
        # in-fill point does.
        got = R._worldle_capital_pick_pin_target(
            self._probe(corrected={"x": 1060.0, "y": 410.0,
                                   "dist": 17.9}),
            self.FIT, "Kuwait City")
        self.assertEqual(got["method"], "in-fill-search")
        self.assertEqual((got["x"], got["y"]), (1060.0, 410.0))

    def test_attempt_summary_lists_every_dots_evidence(self):
        dots = [dict(d) for d in self.DOTS]
        dots[2] = dict(dots[2], label="Kuwait City", label_dist=18.0)
        probe = self._probe(dots=dots)
        target = R._worldle_capital_pick_pin_target(
            probe, self.FIT, "Kuwait City")
        summary = R._worldle_capital_dot_attempt_summary(
            probe, "Kuwait City", target)
        self.assertIn("method=dot-name", summary)
        self.assertIn("dots=4", summary)
        self.assertIn("label='Kuwait City'", summary)
        # Dots with no evidence are listed explicitly, not omitted.
        self.assertIn("no name evidence", summary)

    def test_attempt_summary_with_no_match(self):
        summary = R._worldle_capital_dot_attempt_summary(
            self._probe(), "Kuwait City", None)
        self.assertIn("method=None", summary)
        self.assertIn("dots=4", summary)
        self.assertEqual(summary.count("no name evidence"), 4)

    def test_probe_uses_greedy_label_assignment(self):
        src = inspect.getsource(R._worldle_capital_pinpoint_probe)
        self.assertIn("assignNearest", src)
        self.assertIn("overlay_label", src)
        self.assertIn("group_title", src)
        self.assertIn("sibling_text", src)
        self.assertIn("grandparent", src)

    def test_play_capital_logs_dot_name_attempt_unconditionally(self):
        src = inspect.getsource(R._s_worldle_play_capital)
        self.assertIn("worldle capital dot-name attempt:", src)
        self.assertIn("_worldle_capital_dot_attempt_summary(", src)


# ----------------------------------------------------------------- Banner

class TestBannerProbeIter19(unittest.TestCase):
    def test_iframe_banner_corner_click_closes(self):
        # Cross-origin banner iframe: first probe reports the
        # iframe rect, Python clicks its top-right corner (the X),
        # re-probe says absent -> closed-iframe-via-corner-click.
        logs = []
        orig = R._dbg
        R._dbg = _dbg_recorder(logs)
        try:
            rect = {"x": 700.0, "y": 20.0, "w": 500.0, "h": 80.0}

            def _ev(js, arg=None):
                if "HIDE-IFRAME-BANNER" in js:
                    return True
                if len(page.calls) == 1:
                    return ("iframe-banner:" +
                            '{"x": 700.0, "y": 20.0, "w": 500.0, '
                            '"h": 80.0}')
                return "absent"

            page = _FakePage(_ev)
            R._dismiss_teuteuf_banner(page)
        finally:
            R._dbg = orig
        # Corner click at (x + w - 16, y + 16) = (1184, 36).
        self.assertIn((1184.0, 36.0), page.mouse.clicks)
        self.assertTrue(any("present-iframe-unreadable" in m
                            for m in logs))
        self.assertTrue(any("closed-iframe-via-corner-click" in m
                            for m in logs))

    def test_iframe_banner_hide_fallback(self):
        # Corner click does NOT dismiss: the iframe element itself
        # is hidden and the log says exactly that.
        logs = []
        orig = R._dbg
        R._dbg = _dbg_recorder(logs)
        try:
            def _ev(js, arg=None):
                if "HIDE-IFRAME" in js:
                    return True
                return ("iframe-banner:" +
                        '{"x": 700.0, "y": 20.0, "w": 500.0, '
                        '"h": 80.0}')

            page = _FakePage(_ev)
            R._dismiss_teuteuf_banner(page)
        finally:
            R._dbg = orig
        self.assertTrue(any("hidden-iframe-banner" in m for m in logs))
        self.assertTrue(any("HIDE-IFRAME" in c
                            for c in page.calls))

    def test_absent_still_rescanned(self):
        logs = []
        orig = R._dbg
        R._dbg = _dbg_recorder(logs)
        try:
            page = _FakePage(lambda js, arg=None: "absent")
            R._dismiss_teuteuf_banner(page)
        finally:
            R._dbg = orig
        self.assertEqual(len(page.calls), 2)
        self.assertTrue(any("teuteuf edu banner: absent" in m
                            for m in logs))

    def test_banner_source_covers_new_signatures(self):
        src = inspect.getsource(R._dismiss_teuteuf_banner)
        # Space-stripped signature (split-span 'TeuteufEdu').
        self.assertIn("'teuteufedu'", src)
        # Descendant alt/aria-label/title folded into the signature.
        self.assertIn("getAttribute('alt')", src)
        # Cross-origin iframe detection, never a lying 'absent'.
        self.assertIn("iframe-banner:", src)
        self.assertIn("present-iframe-unreadable", src)
        # Icon-only close control via class/id.
        self.assertIn("clsId", src)
        # Rect-based visibility (iter-16 constraint, kept).
        self.assertIn("getBoundingClientRect", src)
        self.assertNotIn("e.offsetParent", src)


# --------------------------------------------------------------------- G4

class TestLiveRoundsReaderIter19(unittest.TestCase):
    def _page(self, scan_results, toggle=True):
        state = {"n": 0}

        def _ev(js, arg=None):
            if "G4-LIVE-ROUNDS-SCAN" in js:
                out = scan_results[min(state["n"],
                                       len(scan_results) - 1)]
                state["n"] += 1
                return out
            if "(labels)" in js:
                return toggle
            return None

        page = _FakePage(_ev)
        page._scan_calls = lambda: sum(
            1 for c in page.calls if "G4-LIVE-ROUNDS-SCAN" in c)
        page._toggle_calls = lambda: sum(
            1 for c in page.calls if "G4-LIVE-ROUNDS-TOGGLE" in c)
        return page

    def test_scan_first_already_open_dropdown(self):
        # Dropdown ALREADY open: attempt 1 scans it with NO toggle
        # click (a click would have CLOSED it, or clicked a round
        # entry and switched rounds).
        raw = ["🌍 Country", "📍 Capital"]
        page = self._page([{"entries": raw, "container": True}])
        got = R._s_worldle_live_round_modes(page, "worldle")
        self.assertIsNotNone(got)
        self.assertIn("country", got["mapped"])
        self.assertEqual(page._toggle_calls(), 0,
                         "an already-open dropdown must be read "
                         "without any toggle click")
        self.assertEqual(page._scan_calls(), 1)

    def test_three_attempts_before_none(self):
        # No container on any attempt: 3 scans (scan-first,
        # toggle+scan, Escape+toggle+scan) before concluding None.
        page = self._page([{"entries": [], "container": False}])
        self.assertIsNone(R._s_worldle_live_round_modes(page, "worldle"))
        self.assertEqual(page._scan_calls(), 3)

    def test_second_attempt_toggle_then_scan(self):
        # Attempt 1 misses (dropdown closed), attempt 2 opens via
        # toggle and reads the list.
        raw = ["🌍 Country", "📍 Capital", "Area"]
        page = self._page([{"entries": [], "container": False},
                           {"entries": raw, "container": True}])
        got = R._s_worldle_live_round_modes(page, "worldle")
        self.assertIsNotNone(got)
        self.assertIn("area", got["mapped"])
        self.assertEqual(page._toggle_calls(), 1)

    def test_checkmark_entries_map(self):
        # Frame gp_0355: entries carry checkmarks (Country ✓,
        # Capital ✗). The Python mapping must strip them.
        raw = ["Country ✓", "Capital ✗", "Area ✓"]
        page = self._page([{"entries": raw, "container": True}])
        got = R._s_worldle_live_round_modes(page, "worldle")
        self.assertIsNotNone(got)
        self.assertIn("country", got["mapped"])
        self.assertIn("capital", got["mapped"])
        self.assertIn("area", got["mapped"])
        self.assertEqual(got["unmapped"], [])

    def test_toggle_never_opens_but_dropdown_open_is_read(self):
        # Toggle affordance missing AND dropdown already open:
        # scan-first still reads the list (old code returned None
        # on toggle failure without ever scanning).
        raw = ["🌍 Country"]
        page = self._page([{"entries": raw, "container": True}],
                          toggle=False)
        got = R._s_worldle_live_round_modes(page, "worldle")
        self.assertIsNotNone(got)
        self.assertIn("country", got["mapped"])

    def test_reader_source_no_offsetparent_no_nav_exclusion(self):
        src = inspect.getsource(R._s_worldle_live_round_modes)
        # Rect-based visibility everywhere (iter-16 banner fix,
        # finally applied here).
        self.assertNotIn("offsetParent !== null", src)
        # No nav/header/footer ancestry exclusion.
        self.assertNotIn("closest('nav, header, footer')", src)
        # Content-scored container picking + aria-controls target.
        self.assertIn("labelKeys", src)
        self.assertIn("aria-controls", src)
        # Tiered toggle skips entries inside an open menu.
        self.assertIn("inOpenMenu", src)
        self.assertIn("G4-LIVE-ROUNDS-TOGGLE", src)


# ------------------------------------------------------------------- Area

class TestAreaComparisonPathIter19(unittest.TestCase):
    def test_win_check_describes_comparison_pick(self):
        ds = "2026-10-18"

        def _ev(js, arg=None):
            if "localStorage" in js:
                if arg == "size-guesses":
                    return {ds: [{"correct": 17818, "answer": 17818}]}
                if arg == "rounds-completed":
                    return {ds: ["area"]}
                return {}
            return None

        page = _FakePage(_ev)
        day = {"code": "KW", "size": 17818}
        win, _gs, detail = R._s_worldle_win_check(
            page, "area", day, ds)
        self.assertTrue(win)
        self.assertIn("area comparison pick recorded", detail)
        self.assertNotIn("first guess correct==answer", detail)

    def test_number_path_comparison_first_stepper_gated(self):
        src = inspect.getsource(R._s_worldle_play_area)
        # The number path re-attempts the deliberate comparison
        # branch BEFORE any stepper probe.
        self.assertIn('_st_c, _pick_c, _det_c = _comparison()', src)
        # The stepper probe runs only when no comparison widget
        # is showing (or the game declined it).
        self.assertIn('if _st_c in ("not-present", "skipped"):', src)
        # A showing-but-unanswered comparison is never
        # probe-clicked as steppers.
        self.assertIn("after-comparison-fallback", src)
        self.assertIn("no stepper probe on the comparison widget", src)


if __name__ == "__main__":
    unittest.main()
