"""Fix-loop iteration 20 tests — WORLDLE workstream (evidence-grounded
in ANALYSIS17 §§2(f)-2(i), §6 items 5-8, §7 Worldle items).

NOTE on provenance: this file's tests were written against the
MERGED runner.py, which contains iter-20 Worldle work from two
concurrent editors (Capital probe work + Area/G4/Teuteuf work).
The tests pin the merged behaviour, not one author's draft:
- Capital: shadow-piercing host search + second-pass dot
  enumeration (path/rect/div/span/button) + `text` name source.
- Area: button-first click returning (ok, detail, via), card-only
  fallback click, post-click selected-state probe (non-dict
  returns normalise to {}), win-check never claims "pick
  recorded" on an empty / not-correct store.
- Teuteuf: body-sentence signatures, bare span/SVG/div close
  search, no bare 'absent' (absent-candidates diagnostics).
- G4: per-attempt toggle-vs-scan logging, summary-time re-read.

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

    def move(self, x, y, steps=None):
        pass

    def down(self):
        pass

    def up(self):
        pass


class _FakeKeyboard:
    def press(self, *a, **k):
        pass


class _FakePage:
    """Minimal Playwright-page stand-in."""

    def __init__(self, evaluate=None):
        self.mouse = _FakeMouse()
        self.keyboard = _FakeKeyboard()
        self._evaluate = evaluate or (lambda js, arg=None: None)
        self._qp_gid = "worldle"
        self.url = ""
        self.calls = []

    def evaluate(self, js, arg=None):
        self.calls.append(js)
        return self._evaluate(js, arg)

    def wait_for_timeout(self, ms):
        pass


def _dbg_recorder(logs):
    def _rec(gid, msg):
        logs.append(f"[{gid}] {msg}")
    return _rec


def _dot(x, y, **kw):
    base = {"x": float(x), "y": float(y), "r": 8.0, "fill": "blue",
            "cls": "", "id": "", "data": {}, "title": "", "aria": "",
            "label": "", "label_dist": None, "parent_text": "",
            "parent_data": {}, "group_title": "", "sibling_text": "",
            "overlay_label": "", "grandparent_text": "",
            "grandparent_title": "", "grandparent_data": {},
            "text": "", "tag": "circle"}
    base.update(kw)
    return base


# ---------------------------------------------------------------- Capital

class TestCapitalDotsIter20(unittest.TestCase):
    FIT = (1072.5, 422.7)

    def test_dot_name_via_own_text_html_pin(self):
        # Iter-20: an HTML <div> pin dot carries the city name as
        # its own short text (not a sibling label). The picker must
        # match it via the `text` name source.
        dots = [_dot(1085.0, 285.0), _dot(1010.0, 440.0),
                _dot(1090.0, 435.0, text="Kuwait City", tag="div"),
                _dot(1110.0, 645.0)]
        probe = {"found": True, "dots": dots, "n_paths": 29,
                 "n_dots": 4, "in_fill": False, "corrected": None}
        got = R._worldle_capital_pick_pin_target(
            probe, self.FIT, "Kuwait City")
        self.assertIsNotNone(got)
        self.assertEqual(got["method"], "dot-name")
        self.assertEqual((got["x"], got["y"]), (1090.0, 435.0))
        self.assertIn("text", got["detail"])

    def test_dot_name_carries_index_and_tag_for_element_click(self):
        # The picker return carries dot_index + dot_tag so the
        # caller can click the matched dot ELEMENT itself
        # (_worldle_capital_click_dot_element), not a coordinate.
        dots = [_dot(1085.0, 285.0),
                _dot(1090.0, 435.0, text="Kuwait City", tag="div")]
        probe = {"found": True, "dots": dots, "n_paths": 0,
                 "n_dots": 2, "in_fill": False, "corrected": None}
        got = R._worldle_capital_pick_pin_target(
            probe, self.FIT, "Kuwait City")
        self.assertEqual(got["method"], "dot-name")
        self.assertEqual(got["dot_index"], 1)
        self.assertEqual(got["dot_tag"], "div")

    def test_click_dot_element_returns_probe_dict(self):
        page = _FakePage(
            lambda js, arg=None: {"clicked": True, "tag": "circle",
                                  "cls": "dot", "id": "",
                                  "text": "", "top_tag": "CIRCLE"})
        got = R._worldle_capital_click_dot_element(
            page, 1090.0, 435.0, "Kuwait City")
        self.assertTrue(got["clicked"])
        self.assertEqual(got["tag"], "circle")
        self.assertIn("worldle-capital-click-dot-element",
                      page.calls[0])

    def test_click_dot_element_uses_dot_centre_coordinates(self):
        seen = {}

        def _ev(js, arg=None):
            seen["arg"] = arg
            return {"clicked": False, "reason": "no dot-like"}

        page = _FakePage(_ev)
        R._worldle_capital_click_dot_element(
            page, 1090.0, 435.0, "Kuwait City")
        self.assertEqual(seen["arg"], {"x": 1090.0, "y": 435.0})

    def test_summary_lists_text_and_tag(self):
        dots = [_dot(1085.0, 285.0),
                _dot(1090.0, 435.0, text="Kuwait City", tag="div")]
        probe = {"found": True, "dots": dots, "n_paths": 0,
                 "n_dots": 2, "in_fill": False, "corrected": None}
        summary = R._worldle_capital_dot_attempt_summary(
            probe, "Kuwait City", None)
        self.assertIn("text='Kuwait City'", summary)
        self.assertIn("tag='div'", summary)

    def test_probe_source_pierces_shadow_and_enumerates_all_dot_shapes(self):
        src = inspect.getsource(R._worldle_capital_pinpoint_probe)
        # Shadow-root piercing: the iter-19 dots=0 case (host
        # inside the teuteuf-geo-map shadow tree).
        self.assertIn("shadowRoot", src)
        self.assertIn("#pin-capital-svg", src)
        # Second pass enumerates path/rect/div/span/button dots,
        # not just circle/ellipse.
        self.assertIn("path, rect, div, span, button", src)
        self.assertIn("isBlueish", src)
        # Dot-shaped filter: small bounding box, centre on map.
        self.assertIn("r2.width < 3 || r2.width > 48", src)


# ------------------------------------------------------------------- Area

class TestAreaWinCheckWordingIter20(unittest.TestCase):
    DS = "2026-10-18"

    def _page(self):
        def _ev(js, arg=None):
            if "localStorage" in js:
                if arg == "size-guesses":
                    return {self.DS: list(self.store)}
                if arg == "rounds-completed":
                    return {self.DS: []}
                return {}
            return None

        page = _FakePage(_ev)
        return page

    def test_empty_store_never_claims_pick_recorded(self):
        self.store = []
        page = self._page()
        win, _gs, detail = R._s_worldle_win_check(
            page, "area", {"code": "KW", "size": 17818}, self.DS)
        self.assertFalse(win)
        # The AFFIRMATIVE claim must never appear; "no pick
        # recorded" is the honest negation, not the claim.
        self.assertNotIn("area comparison pick recorded", detail)
        self.assertIn("no pick recorded", detail)
        self.assertIn("size-guesses empty", detail)

    def test_present_but_wrong_entry_not_called_pick_recorded(self):
        self.store = [{"correct": 17818, "answer": 11000}]
        page = self._page()
        win, _gs, detail = R._s_worldle_win_check(
            page, "area", {"code": "KW", "size": 17818}, self.DS)
        self.assertFalse(win)
        self.assertNotIn("pick recorded", detail)
        self.assertIn("entry present but not correct", detail)

    def test_correct_entry_still_called_pick_recorded(self):
        self.store = [{"correct": 17818, "answer": 17818}]
        page = self._page()
        win, _gs, detail = R._s_worldle_win_check(
            page, "area", {"code": "KW", "size": 17818}, self.DS)
        self.assertTrue(win)
        self.assertIn("pick recorded", detail)


class TestAreaClickTargetsIter20(unittest.TestCase):
    def test_click_returns_via_button(self):
        page = _FakePage(
            lambda js, arg=None: [True, "Kuwait", "button",
                                  "BUTTON", "some-cls"])
        ok, detail, via = R._s_worldle_click_comparison_option(
            page, "Kuwait")
        self.assertTrue(ok)
        self.assertEqual(via, "button")
        # Iter-22: detail names the clicked element's
        # tag/class (rect/coords when the 8-item return is
        # given), not just its innerText.
        self.assertTrue(detail.startswith("Kuwait"))
        self.assertIn("tag=BUTTON", detail)
        self.assertIn("cls=some-cls", detail)

    def test_click_legacy_two_item_fake_maps_via_unknown(self):
        # Pre-iter-20 fakes return [ok, detail] only.
        page = _FakePage(lambda js, arg=None: [True, "Kuwait"])
        ok, detail, via = R._s_worldle_click_comparison_option(
            page, "Kuwait")
        self.assertTrue(ok)
        self.assertEqual(via, "unknown")

    def test_card_click_returns_via_card(self):
        page = _FakePage(
            lambda js, arg=None: [True, "Kuwait", "card", "DIV", ""])
        ok, detail, via = R._s_worldle_click_comparison_card(
            page, "Kuwait")
        self.assertTrue(ok)
        self.assertEqual(via, "card")
        self.assertTrue(detail.startswith("Kuwait"))
        self.assertIn("tag=DIV", detail)

    def test_selected_state_normalises_non_dict(self):
        # A fake returning True (or None) must NOT crash the
        # caller with AttributeError: 'bool' object has no
        # attribute 'get' (the merged-code bug this guards).
        for ret in (True, None, "garbage", 42):
            page = _FakePage(lambda js, arg=None, _r=ret: _r)
            got = R._s_worldle_comparison_selected_state(page, "Kuwait")
            self.assertEqual(got, {})

    def test_selected_state_passes_dict_through(self):
        want = {"selected": [{"name": "Kuwait"}],
                "selected_name": "Kuwait",
                "options": [{"name": "Kuwait", "selected": True}],
                "wanted_selected": True}
        page = _FakePage(lambda js, arg=None: dict(want))
        self.assertEqual(
            R._s_worldle_comparison_selected_state(page, "Kuwait"),
            want)


class _FakeAreaPage(_FakePage):
    """Fake for _s_worldle_play_comparison: probe -> click ->
    selected-state -> submit -> store-verify."""

    def __init__(self, record, selected):
        super().__init__()
        self.record = record
        self.selected = selected  # dict returned by selected-state
        self.store = []

    def evaluate(self, js, arg=None):
        self.calls.append(js)
        if "worldle-comparison-probe" in js:
            return {"prompt_found": True,
                    "options": [{"name": "Kuwait", "x": 1, "y": 1},
                                {"name": "Qatar", "x": 2, "y": 2}]}
        if "worldle-comparison-card-click" in js:
            return [False, "no card found", "card", "", ""]
        if "worldle-comparison-click" in js:
            if self.record:
                self.store = [{"question": 1, "answer": "Kuwait",
                               "correct": "Kuwait"}]
            return [True, "Kuwait", "button", "BUTTON", "cls"]
        if "worldle-comparison-selected-state" in js:
            return dict(self.selected)
        if "localStorage" in js:
            if arg == "size-guesses":
                return {"2026-10-18": list(self.store)}
            return {}
        if "document.body.innerText" in js:
            return ("Which country has a larger surface area? "
                    "Kuwait Qatar")
        return True


class TestAreaPostClickEvidenceIter20(unittest.TestCase):
    def test_post_click_selected_state_logged_and_card_retry_attempted(self):
        logs = []
        orig = R._dbg
        R._dbg = _dbg_recorder(logs)
        try:
            page = _FakeAreaPage(
                record=False,
                selected={"selected": [], "selected_name": "",
                          "options": [{"name": "Kuwait",
                                       "selected": False}],
                          "wanted_selected": False})
            st, pick, det = R._s_worldle_play_comparison(
                page, "worldle", {}, "area", "2026-10-18",
                ("larger surface area",),
                {"kuwait": 17818, "qatar": 11000},
                "size", "area", verify_store=True)
        finally:
            R._dbg = orig
        self.assertEqual(st, "partial")
        self.assertIsNone(pick)
        # The post-click evidence line exists (button click,
        # no selection) and the card retry ran.
        self.assertTrue(any("post-click selected-state" in m
                            for m in logs))
        self.assertTrue(any("worldle-comparison-card-click" in c
                            for c in page.calls))
        self.assertTrue(any("no selection after click" in m
                            for m in logs))

    def test_selected_click_still_store_verified(self):
        logs = []
        orig = R._dbg
        R._dbg = _dbg_recorder(logs)
        try:
            page = _FakeAreaPage(
                record=True,
                selected={"selected": [{"name": "Kuwait"}],
                          "selected_name": "Kuwait",
                          "options": [{"name": "Kuwait",
                                       "selected": True}],
                          "wanted_selected": True})
            st, pick, det = R._s_worldle_play_comparison(
                page, "worldle", {}, "area", "2026-10-18",
                ("larger surface area",),
                {"kuwait": 17818, "qatar": 11000},
                "size", "area", verify_store=True)
        finally:
            R._dbg = orig
        self.assertEqual(st, "attempted")
        self.assertEqual(pick, "Kuwait")


# ----------------------------------------------------------------- Banner

class TestBannerProbeIter20(unittest.TestCase):
    def test_body_sentence_signatures_in_source(self):
        src = inspect.getsource(R._dismiss_teuteuf_banner)
        # Banner identified by its BODY sentence too, not only
        # the brand mark (brand may render as an image/SVG logo).
        self.assertIn("you asked, we listened", src)
        self.assertIn("educational resource", src)
        self.assertIn("built for classrooms", src)
        self.assertIn("youaskedwelistened", src)
        # Close search covers bare span/SVG/div controls.
        self.assertIn("span, ", src)
        self.assertIn("[class*=close i]", src)
        # No bare 'absent' lie: diagnostics instead.
        self.assertIn("absent-candidates:", src)

    def test_absent_candidates_logged_never_bare_absent(self):
        logs = []
        orig = R._dbg
        R._dbg = _dbg_recorder(logs)
        try:
            page = _FakePage(
                lambda js, arg=None: "absent-candidates:"
                '{"roots": 1, "shadow_hosts": 0}')
            R._dismiss_teuteuf_banner(page)
        finally:
            R._dbg = orig
        self.assertTrue(any("absent-candidates:" in m for m in logs))
        self.assertFalse(any(m.endswith("teuteuf edu banner: absent")
                             for m in logs))
        # absent-candidates re-scans once (late-render), like
        # bare 'absent' did — exactly 2 probe calls, then logged.
        self.assertEqual(len(page.calls), 2)


# --------------------------------------------------------------------- G4

class TestLiveRoundsReaderIter20(unittest.TestCase):
    ENTRIES_13 = ["🌍 Country ✓", "Neighbours", "📍 Capital ✓",
                  "Orientation", "Flag", "Trivia", "Landmark",
                  "Emblem", "Spoken languages", "GDP Per Capita",
                  "Population & Currency", "Populous cities",
                  "Area"]

    def _page(self, scan_results):
        state = {"n": 0}

        def _ev(js, arg=None):
            if "G4-LIVE-ROUNDS-SCAN" in js:
                out = scan_results[min(state["n"],
                                       len(scan_results) - 1)]
                state["n"] += 1
                return out
            if "G4-LIVE-ROUNDS-TOGGLE" in js:
                return {"opened": True, "via": "aria-toggle"}
            return None

        return _FakePage(_ev)

    def test_open_dropdown_with_13_entries_returns_all(self):
        page = self._page(
            [{"entries": list(self.ENTRIES_13), "container": True}])
        got = R._s_worldle_live_round_modes(page, "worldle")
        self.assertIsNotNone(got)
        self.assertEqual(len(got["mapped"]), 13)
        for mode in ("country", "neighbours", "capital",
                     "orientation", "flag", "trivia", "landmark",
                     "emblem", "spokenLanguages", "gdp",
                     "populationCurrency", "populousCities", "area"):
            self.assertIn(mode, got["mapped"])
        self.assertEqual(got["raw"], self.ENTRIES_13)

    def test_toggle_vs_scan_logged_separately(self):
        logs = []
        orig = R._dbg
        R._dbg = _dbg_recorder(logs)
        try:
            page = self._page([{"entries": [], "container": False}])
            self.assertIsNone(
                R._s_worldle_live_round_modes(page, "worldle"))
        finally:
            R._dbg = orig
        attempt_lines = [m for m in logs
                         if "attempt 1 toggle opened=" in m
                         or "attempt 2 toggle opened=" in m
                         or "attempt 3 toggle opened=" in m]
        self.assertEqual(len(attempt_lines), 3)
        self.assertTrue(all("scan container=" in m
                            for m in attempt_lines))
        # Final None line carries both outcome lists.
        self.assertTrue(any("toggle outcomes=" in m and
                            "scan outcomes=" in m for m in logs))

    def test_summary_time_reread_wired_in_worldle_flow(self):
        src = inspect.getsource(R._s_worldle)
        self.assertIn("re-read before summary", src)
        self.assertIn("_s_worldle_live_round_modes(page, gid)", src)


if __name__ == "__main__":
    unittest.main()
