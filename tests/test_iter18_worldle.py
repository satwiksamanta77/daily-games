"""Fix-loop iteration 18 tests — WORLDLE workstream (evidence-grounded
in ANALYSIS15 §§6, 8, 9, 11).

1. Worldle Capital pin (P0, ANALYSIS15 §6): iter-17's
   bbox-fit-in-fill method ran (hit-test-verified in-fill point
   1154.6, 415.1) but produced no marker, and dot-name NEVER ran
   (zero dot-name lines). Root cause: the pinpoint probe only
   read name-ish attrs ON the circle (data-*/title/aria/id) —
   the iter-17 probe found none — while a dot's NAME lives
   beside the dot (a nearby <text> label) or on its parent <g>.
   The probe now records each dot's nearest <text> label (<=60px)
   + parent-<g> text/data, and the picker matches dot-name against
   those sources (logging WHICH source matched). The pin ladder is
   reordered: real press FIRST, confirm second, synthetic
   path-dispatch demoted to LAST RESORT (it was the primary path
   in iters 13-17 and never produced a marker). Nearest-dot-to-a-
   projection stays FORBIDDEN (iter-16 guard, still tested).
2. Teuteuf banner probe (P2, ANALYSIS15 §8): the log said
   `absent` while the banner sat in every Worldle frame. Root
   cause: the iter-17 probe's body-level pre-check returned
   'absent' from `document.body.textContent`, which does NOT
   include shadow-DOM or iframe content — so the shadow/iframe
   search it claimed to run was dead code in exactly the case it
   covered. The pre-check gate is gone: 'absent' only after ALL
   roots are searched. Plus a late-render re-scan: an 'absent'
   result is re-scanned once after 500ms before it is logged.
3. Live-rounds reader / G4 (P2, ANALYSIS15 §§9, 11): the reader
   still returned None ("no open dropdown container found
   (leaves < 3)"), so the 12-offered count was reconstructed, not
   read live. Root cause: the leaf filter
   `children.length === 0` excluded EVERY real entry — each
   entry is a wrapper (button/li) holding an emoji span + a label
   span, so no entry is a bare leaf. Leaves are now the INNERMOST
   text-bearing elements; container selectors also cover
   popover/popper portal wrappers; and the toggle click is
   retried once when the first scan finds no container (an
   already-open dropdown was CLOSED by the first click). A
   genuinely unreadable list still returns None — never a faked
   live read.

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
        self._qp_gid = "test-iter18"
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


class TestCapitalDotNameViaLabel(unittest.TestCase):
    # Iter-17 Kuwait fixture: bbox-fit point (1072.5, 422.7), 4
    # dots with NO identifying attrs on the circles themselves —
    # the state in which dot-name could never fire.
    DOTS = [
        {"x": 1085.0, "y": 285.0, "r": 8.0, "fill": "blue",
         "cls": "", "id": "", "data": {}, "title": "", "aria": "",
         "label": "", "label_dist": None, "parent_text": "",
         "parent_data": {}},
        {"x": 1010.0, "y": 440.0, "r": 8.0, "fill": "blue",
         "cls": "", "id": "", "data": {}, "title": "", "aria": "",
         "label": "", "label_dist": None, "parent_text": "",
         "parent_data": {}},
        {"x": 1090.0, "y": 435.0, "r": 8.0, "fill": "blue",
         "cls": "", "id": "", "data": {}, "title": "", "aria": "",
         "label": "", "label_dist": None, "parent_text": "",
         "parent_data": {}},
        {"x": 1110.0, "y": 645.0, "r": 8.0, "fill": "blue",
         "cls": "", "id": "", "data": {}, "title": "", "aria": "",
         "label": "", "label_dist": None, "parent_text": "",
         "parent_data": {}},
    ]
    FIT = (1072.5, 422.7)

    def _probe(self, **kw):
        base = {"found": True, "dots": [dict(d) for d in self.DOTS],
                "n_paths": 29, "n_dots": 4, "in_fill": False,
                "corrected": None}
        base.update(kw)
        return base

    def test_dot_name_via_nearby_text_label(self):
        # The NAME lives in the <text> label beside the dot, not
        # on the circle: dot-name must now fire (iter-17: never).
        dots = [dict(d) for d in self.DOTS]
        dots[2] = dict(dots[2], label="Kuwait City", label_dist=18.0)
        got = R._worldle_capital_pick_pin_target(
            self._probe(dots=dots), self.FIT, "Kuwait City")
        self.assertIsNotNone(got)
        self.assertEqual(got["method"], "dot-name")
        self.assertEqual((got["x"], got["y"]), (1090.0, 435.0))
        self.assertIn("label", got["detail"])

    def test_dot_name_via_parent_group_text(self):
        # The <g> wrapping dot + label carries the name.
        dots = [dict(d) for d in self.DOTS]
        dots[2] = dict(dots[2], parent_text="Kuwait City")
        got = R._worldle_capital_pick_pin_target(
            self._probe(dots=dots), self.FIT, "Kuwait City")
        self.assertIsNotNone(got)
        self.assertEqual(got["method"], "dot-name")
        self.assertEqual((got["x"], got["y"]), (1090.0, 435.0))
        self.assertIn("parent_text", got["detail"])

    def test_dot_name_via_parent_group_data(self):
        dots = [dict(d) for d in self.DOTS]
        dots[1] = dict(dots[1], parent_data={"city": "Kuwait City"})
        got = R._worldle_capital_pick_pin_target(
            self._probe(dots=dots), self.FIT, "Kuwait City")
        self.assertIsNotNone(got)
        self.assertEqual(got["method"], "dot-name")
        self.assertEqual((got["x"], got["y"]), (1010.0, 440.0))

    def test_dot_name_beats_in_fill(self):
        # A named dot wins over the in-fill land point: the click
        # lands on the game's own candidate, not on land.
        dots = [dict(d) for d in self.DOTS]
        dots[2] = dict(dots[2], label="Kuwait City", label_dist=18.0)
        got = R._worldle_capital_pick_pin_target(
            self._probe(dots=dots, in_fill=True), self.FIT,
            "Kuwait City")
        self.assertEqual(got["method"], "dot-name")

    def test_ambiguous_labels_return_none(self):
        # Two dots labelled with the capital: ambiguous -> None,
        # never a coin-flip click on a possibly-wrong candidate.
        dots = [dict(d) for d in self.DOTS]
        dots[0] = dict(dots[0], label="Kuwait City", label_dist=12.0)
        dots[2] = dict(dots[2], label="Kuwait City", label_dist=18.0)
        self.assertIsNone(R._worldle_capital_pick_pin_target(
            self._probe(dots=dots), self.FIT, "Kuwait City"))

    def test_one_group_wrapping_all_dots_is_ambiguous(self):
        # One <g> wrapping ALL dots gives every dot the same
        # parent_text: the ambiguity rule must refuse, not pick
        # the first dot.
        dots = [dict(d, parent_text="Kuwait City Riyadh Doha")
                for d in self.DOTS]
        self.assertIsNone(R._worldle_capital_pick_pin_target(
            self._probe(dots=dots), self.FIT, "Kuwait City"))

    def test_unnamed_dots_still_fall_back_to_in_fill(self):
        # No dot names the capital: behaviour is UNCHANGED —
        # in-fill land point, NOT nearest-dot (iter-16 guard).
        got = R._worldle_capital_pick_pin_target(
            self._probe(in_fill=True), self.FIT, "Kuwait City")
        self.assertEqual(got["method"], "bbox-fit-in-fill")
        self.assertEqual((got["x"], got["y"]), self.FIT)

    def test_in_fill_search_still_beats_nearest_dot(self):
        # Iter-17 guard, unchanged: fit point in a hole, nearest
        # dot ~21px away must NOT be picked.
        got = R._worldle_capital_pick_pin_target(
            self._probe(corrected={"x": 1060.0, "y": 410.0,
                                   "dist": 17.9}),
            self.FIT, "Kuwait City")
        self.assertEqual(got["method"], "in-fill-search")
        self.assertEqual((got["x"], got["y"]), (1060.0, 410.0))

    def test_no_verified_target_returns_none(self):
        self.assertIsNone(R._worldle_capital_pick_pin_target(
            self._probe(), self.FIT, "Kuwait City"))


class TestPinLadderOrder(unittest.TestCase):
    def test_real_press_runs_before_synthetic_dispatch(self):
        src = inspect.getsource(R._s_worldle_capital_pin_ladder)
        self.assertIn("real-press=ok", src)
        self.assertIn("path-dispatch-synthetic-last-resort", src)
        self.assertLess(src.index("real-press=ok"),
                        src.index("path-dispatch-synthetic-last-resort"),
                        "synthetic path-dispatch must no longer be the "
                        "primary ladder step")
        self.assertIn("LAST RESORT", src)
        self.assertNotIn("path-dispatch={res}", src)

    def _ladder_page(self, probe_markers):
        """FakePage: pin probe returns markers from probe_markers
        in call order; confirm returns False; path dispatch ok."""
        state = {"n": 0}

        def _ev(js, arg=None):
            if "elementFromPoint" in js:
                m = probe_markers[min(state["n"],
                                      len(probe_markers) - 1)]
                state["n"] += 1
                return {"markers": m, "sig": f"sig{m}",
                        "topCls": "", "topTag": "svg", "inMap": True}
            if "confirm pin" in js:
                return False
            if "svg path" in js:
                return {"dispatched": True, "n_paths": 5}
            return None

        return _FakePage(_ev)

    def test_marker_after_real_press_stops_before_synthetic(self):
        # Real press produces the marker: the ladder must STOP —
        # the synthetic path-dispatch must never run. The ladder's
        # FIRST in-ladder probe is the post-real-press one, so the
        # scripted markers start at the post-press value (5).
        page = self._ladder_page([5])
        before = {"markers": 4, "sig": "sig4",
                  "topCls": "", "topTag": "svg"}
        after, detail = R._s_worldle_capital_pin_ladder(
            page, "worldle", 1090.0, 435.0, before,
            lambda b, a: int(a.get("markers") or 0)
            > int(b.get("markers") or 0))
        self.assertIn("MARKER", detail)
        self.assertIn("real-press=ok", detail)
        self.assertNotIn("path-dispatch", detail)
        self.assertFalse(any("svg path" in c for c in page.calls),
                         "synthetic path-dispatch ran despite the "
                         "real press producing a marker")
        self.assertEqual(page.mouse.downs, 1)
        self.assertEqual(page.mouse.ups, 1)

    def test_no_marker_runs_all_steps_in_order(self):
        page = self._ladder_page([4])
        before = {"markers": 4, "sig": "sig4",
                  "topCls": "", "topTag": "svg"}
        _after, detail = R._s_worldle_capital_pin_ladder(
            page, "worldle", 1090.0, 435.0, before,
            lambda b, a: False)
        self.assertIn("no marker", detail)
        self.assertLess(detail.index("real-press=ok"),
                        detail.index("confirm-click="))
        self.assertLess(detail.index("confirm-click="),
                        detail.index("path-dispatch-synthetic-"
                                     "last-resort"))


class TestBannerProbeTruthful(unittest.TestCase):
    def test_absent_is_rescanned_before_logging(self):
        # Iter-18 late-render re-scan: a first 'absent' is NOT
        # logged until a second scan 500ms later also says absent.
        logs = []
        orig = R._dbg
        R._dbg = _dbg_recorder(logs)
        try:
            page = _FakePage(lambda js, arg=None: "absent")
            R._dismiss_teuteuf_banner(page)
        finally:
            R._dbg = orig
        self.assertEqual(len(page.calls), 2,
                         "absent must trigger exactly one re-scan")
        self.assertIn(500, page.waits)
        self.assertTrue(any("teuteuf edu banner: absent" in m
                            for m in logs))

    def test_found_result_is_not_rescanned(self):
        logs = []
        orig = R._dbg
        R._dbg = _dbg_recorder(logs)
        try:
            page = _FakePage(lambda js, arg=None: "closed-via-button")
            R._dismiss_teuteuf_banner(page)
        finally:
            R._dbg = orig
        self.assertEqual(len(page.calls), 1,
                         "a found/closed result must not be re-scanned")
        self.assertTrue(any("closed-via-button" in m for m in logs))

    def test_late_render_found_on_rescan_is_logged(self):
        # First scan misses (banner not rendered yet), re-scan
        # finds it: the logged result is the re-scan's, not a
        # lying 'absent'.
        logs = []
        orig = R._dbg
        R._dbg = _dbg_recorder(logs)
        try:
            page = _FakePage(lambda js, arg=None:
                             "absent" if len(page.calls) == 1
                             else "closed-via-button")
            R._dismiss_teuteuf_banner(page)
        finally:
            R._dbg = orig
        self.assertTrue(any("closed-via-button" in m for m in logs))
        self.assertFalse(any("banner: absent" in m for m in logs))

    def test_no_body_precheck_gate_in_source(self):
        src = inspect.getsource(R._dismiss_teuteuf_banner)
        # The iter-17 gate: a single-line early 'absent' from
        # body textContent (which excludes shadow/iframe content).
        self.assertNotIn(
            "if (!bodyText.includes('teuteuf edu')) return 'absent'",
            src)
        # The probe still searches every root and logs HOW found.
        self.assertIn("shadowRoot", src)
        self.assertIn("contentDocument", src)
        self.assertIn("textContent", src)
        # The JS is evaluated twice (initial + re-scan on absent).
        self.assertEqual(src.count("page.evaluate(_JS)"), 2)
        # Rect-based visibility (iter-16 constraint, kept).
        self.assertIn("getBoundingClientRect", src)
        self.assertNotIn("e.offsetParent", src)


class TestLiveRoundsReaderLeaves(unittest.TestCase):
    def _page(self, scan_results):
        """FakePage: toggle opens; scan returns scan_results in
        call order (list form = iter-11 back-compat, or dict)."""
        state = {"n": 0}

        def _ev(js, arg=None):
            if "G4-LIVE-ROUNDS-SCAN" in js:
                out = scan_results[min(state["n"],
                                       len(scan_results) - 1)]
                state["n"] += 1
                return out
            if "(labels)" in js:
                return True
            return None

        page = _FakePage(_ev)
        page._scan_calls = lambda: sum(
            1 for c in page.calls if "G4-LIVE-ROUNDS-SCAN" in c)
        return page

    def test_wrapped_entries_map(self):
        # Scan returns the RAW list (the JS leaf fix is what lets
        # wrapped entries reach this point in a real browser);
        # mapping + chrome filtering are unchanged.
        raw = ["🌍 Country", "📍 Capital", "Mystery Round", "Give up"]
        page = self._page([raw])
        got = R._s_worldle_live_round_modes(page, "worldle")
        self.assertIsNotNone(got)
        self.assertEqual(got["scope"], "dropdown")
        self.assertIn("country", got["mapped"])
        self.assertIn("capital", got["mapped"])
        self.assertEqual(got["raw"], raw)
        self.assertEqual(got["unmapped"], ["Mystery Round"])

    def test_no_container_retries_toggle_then_returns_none(self):
        # First scan: no container (dropdown was already open, so
        # the first click CLOSED it). Second scan after the re-open
        # click: still no container -> None, never a faked read.
        page = self._page([{"entries": [], "container": False},
                           {"entries": [], "container": False}])
        self.assertIsNone(R._s_worldle_live_round_modes(page, "worldle"))
        self.assertEqual(page._scan_calls(), 2,
                         "the reader must re-scan after the re-open "
                         "click before concluding unreadable")

    def test_second_scan_success_is_used(self):
        # Already-open dropdown: first scan misses (closed by the
        # first click), second scan after re-open reads the list.
        raw = ["🌍 Country", "📍 Capital"]
        page = self._page([{"entries": [], "container": False}, raw])
        got = R._s_worldle_live_round_modes(page, "worldle")
        self.assertIsNotNone(got)
        self.assertIn("country", got["mapped"])

    def test_toggle_never_opens_is_none(self):
        def _ev(js, arg=None):
            if "(labels)" in js:
                return False
            return None
        page = _FakePage(_ev)
        self.assertIsNone(R._s_worldle_live_round_modes(page, "worldle"))

    def test_old_leaf_filter_is_gone(self):
        src = inspect.getsource(R._s_worldle_live_round_modes)
        # The old filter CODE is gone (the string survives only in
        # the explanatory comment naming what was removed).
        self.assertNotIn(".filter(e => e.children.length === 0)", src,
                         "the old leaf filter excluded every wrapped "
                         "entry and always returned 'leaves < 3'")
        self.assertIn("hasTextChild", src)
        self.assertIn("popover", src)
        self.assertIn("Second chance", src)


class TestCapitalPlayerWiring(unittest.TestCase):
    def test_probe_picker_and_dot_log_wired(self):
        src = inspect.getsource(R._s_worldle_play_capital)
        self.assertIn("_worldle_capital_pinpoint_probe(page, px, py)", src)
        self.assertIn("_worldle_capital_pick_pin_target(", src)
        self.assertIn("pin dots:", src)
        self.assertIn("pin target:", src)
        # Zero-movement press: move, then down + up with no move
        # between (d3-zoom didZoomMove stays false).
        self.assertIn("page.mouse.down()", src)
        self.assertIn("page.mouse.up()", src)
        self.assertIn("didZoomMove", src)


if __name__ == "__main__":
    unittest.main()
