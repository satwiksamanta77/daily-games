"""Fix-loop iteration 16 tests — WORLDLE workstream (evidence-grounded
in ANALYSIS12 §§5-7 + §9).

1. Capital pin-drop (P0): iter-15 clicked the bbox-fit point, whose
   pointerdown landed on bare SVG (top=svg, markers 4->4) — the
   teuteuf-geo-map component fires onRegionClick ONLY when
   pointerdown lands ON a region piece (pointerdown ->
   pendingClickRegion; d3-zoom end fires only when !didZoomMove;
   no click listener exists on pieces). Now the click is retargeted
   onto the nearest hit-test-verified country piece path
   (path.country-solid / g.geo-map-label in the component's shadow
   tree), with a 250px guard so a far-off bbox point never clicks a
   neighbouring country.
2. Language placement (P0): covered by the composed tree — the drag
   helper publishes its LABEL pairs with pre-drag chip coords, and
   click-verify reuses them verbatim (never re-pairs, never
   re-resolves the chip by text), refuses geometry-x re-pairing, and
   rejects a chip centre coinciding with the slot centre (the
   iter-15 swap symptom). Tested here on the iter-15 fixture
   coordinates (slots y≈303, chips y≈668).
3. Area re-switch (P1): a failed store-verify + a view move used to
   return partial immediately (17818 never typed). Now it
   re-switches to Area, re-verifies the visible round, and runs the
   number path. Ad-divs are excluded from the input + stepper
   probes.
4. Teuteuf banner (P1): the old offsetParent visibility check saw
   a fixed-position banner as absent; visibility is now rect-based,
   the close click is verified by re-querying, and EVERY outcome
   (including 'absent') is logged; the dismiss re-runs on every
   mode switch.

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

    def click(self, x, y):
        self.clicks.append((float(x), float(y)))

    def move(self, *a, **k):
        pass

    def down(self):
        pass

    def up(self):
        pass


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
        self._qp_gid = "test-iter16"

    def evaluate(self, js, arg=None):
        return self._evaluate(js, arg)

    def wait_for_timeout(self, ms):
        pass


def _dbg_recorder(logs):
    def _rec(gid, msg):
        logs.append(f"[{gid}] {msg}")
    return _rec


class TestCapitalPickPiece(unittest.TestCase):
    # Fixture: a capital-map SVG's piece candidates. The bbox-fit
    # point (1072.5, 422.7) is the iter-15 Kuwait point; the
    # graticule/sphere path is UNVERIFIED at the same point (a bare
    # SVG hit), the Kuwait piece is verified, a neighbour is
    # verified but farther away.
    FIXTURE = [
        {"id": "sphere", "cls": "stroke", "region": "",
         "cx": 1072.5, "cy": 422.7, "verified": False},
        {"id": "piece-KW", "cls": "country-solid", "region": "KW",
         "cx": 1070.0, "cy": 420.0, "verified": True},
        {"id": "piece-SA", "cls": "country-solid", "region": "SA",
         "cx": 900.0, "cy": 300.0, "verified": True},
    ]

    def test_picks_verified_country_piece_not_sphere(self):
        got = R._worldle_capital_pick_piece(
            self.FIXTURE, 1072.5, 422.7)
        self.assertIsNotNone(got)
        self.assertEqual(got["id"], "piece-KW")
        self.assertAlmostEqual(got["dist"], 3.7, places=1)

    def test_unverified_only_returns_none(self):
        cands = [dict(p, verified=False) for p in self.FIXTURE]
        self.assertIsNone(
            R._worldle_capital_pick_piece(cands, 1072.5, 422.7))

    def test_far_verified_piece_beyond_guard_returns_none(self):
        # Nearest verified piece ~493px away: a far-off bbox point
        # must NOT click a probably-neighbour country.
        cands = [{"id": "piece-SA", "cls": "country-solid",
                  "region": "SA", "cx": 700.0, "cy": 100.0,
                  "verified": True}]
        self.assertIsNone(
            R._worldle_capital_pick_piece(cands, 1072.5, 422.7))

    def test_boundary_250px_is_picked(self):
        cands = [{"id": "piece-X", "cls": "country-solid",
                  "region": "X", "cx": 150.0, "cy": 200.0,
                  "verified": True}]
        got = R._worldle_capital_pick_piece(cands, 0.0, 0.0)
        self.assertIsNotNone(got)
        self.assertAlmostEqual(got["dist"], 250.0)

    def test_malformed_entries_skipped(self):
        cands = [None, "nope", {"id": "bad", "cx": "x", "cy": 1,
                                "verified": True},
                 {"id": "good", "cx": 10.0, "cy": 10.0,
                  "verified": True}]
        got = R._worldle_capital_pick_piece(cands, 0.0, 0.0)
        self.assertIsNotNone(got)
        self.assertEqual(got["id"], "good")


class TestCapitalPiecePathProbe(unittest.TestCase):
    def test_probe_picks_from_page_candidates(self):
        page = _FakePage(lambda js, arg=None: {
            "pieces": [
                {"id": "sphere", "cls": "stroke", "region": "",
                 "cx": 1072.5, "cy": 422.7, "verified": False},
                {"id": "piece-KW", "cls": "country-solid",
                 "region": "KW", "cx": 1070.0, "cy": 420.0,
                 "verified": True},
            ]})
        got = R._worldle_capital_piece_path(page, 1072.5, 422.7)
        self.assertIsNotNone(got)
        self.assertEqual(got["id"], "piece-KW")

    def test_probe_exception_returns_none(self):
        def _boom(js, arg=None):
            raise RuntimeError("boom")
        page = _FakePage(_boom)
        self.assertIsNone(R._worldle_capital_piece_path(page, 0, 0))

    def test_retarget_wired_into_capital_player(self):
        src = inspect.getsource(R._s_worldle_play_capital)
        self.assertIn("_worldle_capital_piece_path(page, px, py)", src)
        self.assertIn("px, py = float(_piece[\"cx\"])", src)


class TestClickVerifyReusesLabelPairs(unittest.TestCase):
    # Iter-15 fixture coordinates (ANALYSIS12 §6): label slots at
    # y≈303, chip boxes at y≈668. The iter-15 bug clicked the slot
    # coords (850, 304) as the "chip" and the chip box (841, 668.5)
    # as the "slot".
    PAIRS_IN = [
        ("ara", {"x": 841.0, "y": 668.5, "phrase": "my name is ahmed"},
         {"x": 850.0, "y": 303.3, "label": "Arabic"}),
        ("eng", {"x": 1079.0, "y": 668.5, "phrase": "my name is john"},
         {"x": 1070.0, "y": 303.3, "label": "English"}),
    ]
    PICKED = {"ara": "my name is ahmed", "eng": "my name is john"}

    def _page(self):
        def _ev(js, arg=None):
            if "WORLDLE-PHRASE-BROAD-SLOT-PROBE" in js:
                # The broad probe sees ONLY the chip row (as in
                # iter-15, chips qualify as bordered boxes): if
                # click-verify re-paired, it would pair these.
                return {"slots": [
                    {"label": "", "x": 841.0, "y": 668.5,
                     "text": "my name is ahmed", "kind": "bordered-box"},
                    {"label": "", "x": 1079.0, "y": 668.5,
                     "text": "my name is john", "kind": "bordered-box"},
                ], "empty_boxes": []}
            if "localStorage.getItem" in js:
                if arg == "language-guesses":
                    return {"2026-10-18": [
                        {"question": 1, "isCorrect": True},
                        {"question": 1, "isCorrect": True}]}
                return {}
            if "[data-slot]" in js:
                # Narrow slot-contains probe: the fixture slots
                # hold their phrases after the clicks.
                return True
            if "querySelectorAll('button')" in js:
                return "guess"
            return None
        return _FakePage(_ev)

    def test_clicks_stashed_chip_then_slot_coords(self):
        logs = []
        orig = R._dbg
        R._dbg = _dbg_recorder(logs)
        try:
            st, det = R._worldle_phrase_click_verify(
                self._page(), "gid-test", self.PICKED,
                ["ara", "eng"], ["Arabic", "English"], [], "2026-10-18",
                _pairs_in=self.PAIRS_IN)
        finally:
            R._dbg = orig
        self.assertEqual(st, "attempted")
        # Exact click order: stashed chip centre, then stashed slot
        # centre, per pair — never the iter-15 swap.
        page = self._page()
        R._dbg = _dbg_recorder(logs)
        try:
            R._worldle_phrase_click_verify(
                page, "gid-test", self.PICKED,
                ["ara", "eng"], ["Arabic", "English"], [], "2026-10-18",
                _pairs_in=self.PAIRS_IN)
        finally:
            R._dbg = orig
        self.assertEqual(page.mouse.clicks, [
            (841.0, 668.5), (850.0, 303.3),
            (1079.0, 668.5), (1070.0, 303.3)])
        self.assertTrue(any("reusing drag LABEL pairs" in m
                            for m in logs))

    def test_geometry_repairing_refused_without_pairs_in(self):
        # No _pairs_in: the only candidates are the chip-row boxes,
        # which pair by geometry-x only -> refused outright.
        logs = []
        orig = R._dbg
        R._dbg = _dbg_recorder(logs)
        try:
            st, det = R._worldle_phrase_click_verify(
                self._page(), "gid-test", self.PICKED,
                ["ara", "eng"], ["Arabic", "English"], [], "2026-10-18")
        finally:
            R._dbg = orig
        self.assertEqual(st, "partial")
        self.assertIn("geometry", det.lower())


class TestAreaReswitchOnPartialViewMove(unittest.TestCase):
    def test_store_verify_partial_plus_view_move_runs_number(self):
        logs = []
        calls = {"switch": []}
        comp_calls = {"n": 0}

        def _comp(page, gid, day, mode, ds, subs, lookup, stat,
                  label, verify_store=False):
            comp_calls["n"] += 1
            if comp_calls["n"] == 1:
                return ("partial", None,
                        "comparison option 'Kuwait' clicked + submitted "
                        "but the store did NOT change (size-guesses 0 "
                        "guesses, identical list); 'answered' NOT "
                        "claimed — the comparison answer was never "
                        "recorded")
            return ("not-present", None, "comparison prompt not showing")

        still = iter([False, True])

        def _still(page):
            return next(still)

        def _switch(page, gid, mode, ds):
            calls["switch"].append(mode)
            return True

        orig = {}
        for name, fn in [
                ("_s_worldle_play_comparison", _comp),
                ("_worldle_area_still_on_area", _still),
                ("_s_worldle_switch_mode", _switch),
                ("_worldle_area_log_state",
                 lambda page, gid, step: None),
                ("_s_worldle_pick_input", lambda *a, **k: False),
                ("_s_worldle_play_area_stepper",
                 lambda page, gid, size: (
                     True, f"stepper display reached {size}")),
                ("_s_worldle_submit_guarded",
                 lambda *a, **k: "submitted"),
                ("_dbg", _dbg_recorder(logs))]:
            orig[name] = getattr(R, name)
            setattr(R, name, fn)
        try:
            st, guesses, det = R._s_worldle_play_area(
                _FakePage(), "gid-test",
                {"date": "2026-10-18", "size": 17818,
                 "size_lookup": {}})
        finally:
            for name, fn in orig.items():
                setattr(R, name, fn)
        self.assertEqual(st, "attempted")
        self.assertIn("17818", guesses)
        self.assertEqual(calls["switch"], ["area"])
        self.assertTrue(any("re-switching" in m for m in logs))

    def test_still_on_area_partial_returns_without_number(self):
        def _comp(page, gid, day, mode, ds, subs, lookup, stat,
                  label, verify_store=False):
            return ("partial", None, "comparison option not clickable")

        orig = {}
        for name, fn in [
                ("_s_worldle_play_comparison", _comp),
                ("_worldle_area_still_on_area", lambda page: True),
                ("_worldle_area_log_state",
                 lambda page, gid, step: None),
                ("_dbg", _dbg_recorder([]))]:
            orig[name] = getattr(R, name)
            setattr(R, name, fn)
        try:
            st, guesses, det = R._s_worldle_play_area(
                _FakePage(), "gid-test",
                {"date": "2026-10-18", "size": 17818,
                 "size_lookup": {}})
        finally:
            for name, fn in orig.items():
                setattr(R, name, fn)
        self.assertEqual(st, "partial")
        self.assertIn("not clickable", det)


class TestBannerDismiss(unittest.TestCase):
    def test_absent_case_is_logged(self):
        logs = []
        orig = R._dbg
        R._dbg = _dbg_recorder(logs)
        try:
            R._dismiss_teuteuf_banner(
                _FakePage(lambda js, arg=None: "absent"))
        finally:
            R._dbg = orig
        self.assertTrue(any("teuteuf edu banner: absent" in m
                            for m in logs))

    def test_visibility_is_rect_based_not_offset_parent(self):
        src = inspect.getsource(R._dismiss_teuteuf_banner)
        self.assertIn("getBoundingClientRect", src)
        # The old check `e && e.offsetParent !== null` must be gone
        # (the word still appears in comments explaining why).
        self.assertNotIn("e.offsetParent", src)

    def test_dismiss_reruns_on_mode_switch(self):
        src = inspect.getsource(R._s_worldle_switch_mode)
        self.assertIn("_dismiss_teuteuf_banner(page)", src)


class TestAdExclusion(unittest.TestCase):
    def test_input_probe_excludes_ad_containers(self):
        src = inspect.getsource(R._s_worldle_pick_input)
        for token in ("adngin", "adsbygoogle", "snigel", "_1SS2i7sB"):
            self.assertIn(token, src)

    def test_stepper_probe_excludes_ad_containers(self):
        for token in ("adngin", "adsbygoogle", "snigel", "_1SS2i7sB"):
            self.assertIn(token, R._AREA_STEPPER_SELECT_JS)


if __name__ == "__main__":
    unittest.main()
