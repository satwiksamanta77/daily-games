"""Fix-loop iteration 21 tests — WORLDLE workstream (evidence-grounded
in ANALYSIS18 §§2(e)-2(h), 2(j), §6 items 4-7, §7 Worldle items 3-7).

- Area (P0): the round-switch verified the STORE (game_round) while
  the SPA never re-rendered the Area UI, so the comparison click
  landed on the wrong round (frame gp_0342: PopulationCurrency UI).
  The switch now verifies the VISIBLE round, recovers via the
  visible UI's own 'Next round' control, and fails honestly when
  the Area UI never renders; the comparison player itself refuses
  to click on an unverified Area round.
- Capital (P0): dot collection is unified with the markers counter
  (<use>/<g>/<image> shapes + a markers-selector pass over every
  root), dot-name matching runs over that set, and a verified
  point coinciding with a dot snaps to the dot's CENTRE.
- Teuteuf (P1): widened iframe-shape guards, a rendered-text
  (innerText) pass, closed-shadow host enumeration, and an honest
  'unverified-no-element-match:' label — never 'absent' for a
  frame-visible banner.
- G4 (P1): content-based container fallback (no trust in the
  opened menu's markup) + a container dump on failure.
- Waffle (P2): the breakdown card shows "swaps used" (or drops
  the stat) instead of a meaningless "0 green rows".

All tests are synthetic/local: no network, no browser, no CI run.
"""
import inspect
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import runner as R
import daily_parity as DP


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

    def query_selector(self, sel):
        return None

    def query_selector_all(self, sel):
        return []


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


DS = "2026-10-18"


# ------------------------------------------------------------------- Area

class TestVisibleAreaHelperIter21(unittest.TestCase):
    def test_dict_result_passes_through(self):
        page = _FakePage(lambda js, arg=None: {
            "visible": True, "signals": ["area-prompt-visible"],
            "prompt_visible": True, "selected_tab": "",
            "prompt_text": "larger surface area", "next_round_visible": False})
        got = R._s_worldle_visible_area_ui(page)
        self.assertTrue(got["visible"])
        self.assertIn("area-prompt-visible", got["signals"])
        self.assertIn("worldle-visible-area", page.calls[0])

    def test_dict_result_not_visible(self):
        page = _FakePage(lambda js, arg=None: {
            "visible": False, "signals": ["next-round-visible"],
            "prompt_visible": False, "selected_tab": "",
            "prompt_text": "", "next_round_visible": True})
        got = R._s_worldle_visible_area_ui(page)
        self.assertFalse(got["visible"])
        self.assertTrue(got["next_round_visible"])

    def test_bool_true_fake_maps_to_visible(self):
        # Legacy test fakes returning a bare True (bool-compat,
        # mirroring the G4 toggle convention).
        page = _FakePage(lambda js, arg=None: True)
        self.assertTrue(R._s_worldle_visible_area_ui(page)["visible"])

    def test_none_result_reports_probe_not_run(self):
        # None = the probe never executed (legacy fakes that do
        # not script it). Production JS always returns a dict,
        # so this path never weakens a real run's gate.
        page = _FakePage(lambda js, arg=None: None)
        got = R._s_worldle_visible_area_ui(page)
        self.assertFalse(got["visible"])
        self.assertFalse(got["probe_ran"])

    def test_dict_result_reports_probe_ran(self):
        page = _FakePage(lambda js, arg=None: {
            "visible": False, "signals": []})
        got = R._s_worldle_visible_area_ui(page)
        self.assertTrue(got["probe_ran"])
        page = _FakePage(lambda js, arg=None: None)
        self.assertFalse(R._s_worldle_visible_area_ui(page)["visible"])

        def _raise(js, arg=None):
            raise RuntimeError("evaluate boom")

        page2 = _FakePage(_raise)
        got = R._s_worldle_visible_area_ui(page2)
        self.assertFalse(got["visible"])
        self.assertIn("error", got)

    def test_visible_probe_source_checks_rendered_prompt_not_header(self):
        src = inspect.getsource(R._s_worldle_visible_area_ui)
        self.assertIn("larger surface area", src)
        self.assertIn("surface area of", src)
        self.assertIn("aria-selected", src)
        # The site header is always WORLDLE, so a header check is
        # useless and must not be the visible-round signal.
        self.assertNotIn("WORLDLE", src)

    def test_click_next_round_clicks_exact_button(self):
        seen = {}

        def _ev(js, arg=None):
            if "worldle-next-round-click" in js:
                seen["called"] = True
                return True
            return None

        page = _FakePage(_ev)
        self.assertTrue(R._s_worldle_click_next_round(page))
        self.assertTrue(seen.get("called"))
        self.assertIn("'next round'", page.calls[0])


class _FakeSwitchPage(_FakePage):
    """Fake for _s_worldle_switch_mode: store + visible-round +
    dropdown + banner dispatch."""

    def __init__(self, store_mode="area", visible=False,
                 dropdown_open=False, item_clicked=False,
                 next_round=False):
        super().__init__()
        self.store_mode = store_mode
        self.visible = visible  # bool, or "after-next-round"
        self.dropdown_open = dropdown_open
        self.item_clicked = item_clicked
        self.next_round = next_round
        self.next_round_calls = 0
        self.url = "https://worldle.teuteuf.fr/"

    def evaluate(self, js, arg=None):
        self.calls.append(js)
        if "worldle-visible-area" in js:
            if self.visible is None:
                return None
            vis = self.visible
            if vis == "after-next-round":
                vis = self.next_round_calls > 0
            return {"visible": bool(vis), "signals": [],
                    "prompt_visible": bool(vis), "selected_tab": "",
                    "prompt_text": "", "next_round_visible": False}
        if "worldle-next-round-click" in js:
            self.next_round_calls += 1
            return self.next_round
        if "TEUTEUF-BANNER-DISMISS" in js:
            return "absent"
        if "localStorage" in js:
            if arg == "game_round":
                return {"gameMode": self.store_mode}
            return {}
        if isinstance(arg, list):
            return self.dropdown_open
        if isinstance(arg, str) and arg == "Area":
            return self.item_clicked
        return None


class TestSwitchVisibleGateIter21(unittest.TestCase):
    def test_store_says_area_but_visible_never_confirmed_fails(self):
        # The iter-19/20 shape: game_round='area', visible UI is
        # still the previous round, no Next-round control. The
        # switch must NOT return True on the store alone, and
        # must never claim the switch.
        logs = []
        orig = R._dbg
        R._dbg = _dbg_recorder(logs)
        try:
            page = _FakeSwitchPage(store_mode="area", visible=False,
                                   dropdown_open=False,
                                   next_round=False)
            got = R._s_worldle_switch_mode(page, "worldle", "area", DS)
        finally:
            R._dbg = orig
        self.assertFalse(got)
        self.assertTrue(any("worldle-visible-area" in c
                            for c in page.calls))
        self.assertTrue(any("visible-round gate failed" in m
                            for m in logs))

    def test_next_round_advance_recovers_visible_round(self):
        # Dropdown click lands (store already area), visible UI
        # still the old round; one Next-round advance renders
        # the Area UI; the switch then verifies and returns True.
        logs = []
        orig = R._dbg
        R._dbg = _dbg_recorder(logs)
        try:
            page = _FakeSwitchPage(store_mode="area",
                                   visible="after-next-round",
                                   dropdown_open=True,
                                   item_clicked=True,
                                   next_round=True)
            got = R._s_worldle_switch_mode(page, "worldle", "area", DS)
        finally:
            R._dbg = orig
        self.assertTrue(got)
        self.assertGreaterEqual(page.next_round_calls, 1)
        self.assertTrue(any("Next round advance" in m for m in logs))

    def test_switch_falls_back_to_store_when_probe_unscripted(self):
        # Legacy fake: the visible probe returns None (never
        # executed). The switch falls back to the store check
        # rather than blocking on a probe that never ran.
        page = _FakeSwitchPage(store_mode="area", visible=None,
                               dropdown_open=False)
        self.assertTrue(
            R._s_worldle_switch_mode(page, "worldle", "area", DS))
        # Capital (and every other mode) still verifies on the
        # store alone — the visible-round gate is Area-only, so
        # no other mode can regress from a prompt-shaped check.
        page = _FakeSwitchPage(store_mode="capital", visible=False,
                               dropdown_open=False)
        got = R._s_worldle_switch_mode(page, "worldle", "capital", DS)
        self.assertTrue(got)
        self.assertFalse(any("worldle-visible-area" in c
                             for c in page.calls))

    def test_switch_source_wires_visible_gate_and_next_round(self):
        src = inspect.getsource(R._s_worldle_switch_mode)
        self.assertIn("_s_worldle_visible_area_ui(page)", src)
        self.assertIn("_s_worldle_click_next_round(page)", src)
        self.assertIn("Next round advance", src)
        self.assertIn("visible-round gate failed", src)


class _FakeAreaPage(_FakePage):
    """Fake for _s_worldle_play_comparison: visible-area gate ->
    probe -> click -> selected-state -> submit -> store-verify."""

    def __init__(self, visible=True, record=False, mode="area"):
        super().__init__()
        self.visible = visible
        self.record = record
        self.mode = mode
        self.store = []
        self.clicked = []

    def evaluate(self, js, arg=None):
        self.calls.append(js)
        if "worldle-visible-area" in js:
            if self.visible is None:
                return None
            return {"visible": self.visible, "signals": [],
                    "prompt_visible": self.visible, "selected_tab": "",
                    "prompt_text": "", "next_round_visible": False}
        if "worldle-comparison-probe" in js:
            return {"prompt_found": True,
                    "options": [{"name": "Kuwait", "x": 1, "y": 1},
                                {"name": "Qatar", "x": 2, "y": 2}]}
        if "worldle-comparison-card-click" in js:
            return [False, "no card found", "card", "", ""]
        if "worldle-comparison-click" in js:
            self.clicked.append("option")
            if self.record:
                self.store = [{"question": 1, "answer": "Kuwait",
                               "correct": "Kuwait"}]
            return [True, "Kuwait", "button", "BUTTON", "cls"]
        if "worldle-comparison-selected-state" in js:
            sel = bool(self.record)
            return {"selected": ([{"name": "Kuwait"}] if sel else []),
                    "selected_name": "Kuwait" if sel else "",
                    "options": [{"name": "Kuwait", "selected": sel}],
                    "wanted_selected": sel}
        if "document.body.innerText" in js:
            return ("Which country has a larger surface area? "
                    "Kuwait Qatar")
        if "localStorage" in js:
            if arg in ("size-guesses", "gdp-guesses"):
                return {DS: list(self.store)}
            if arg == "rounds-completed":
                return {DS: []}
            return {}
        return None


class TestComparisonVisibleGateIter21(unittest.TestCase):
    def test_unverified_round_blocks_click_entirely(self):
        # The iter-20 failure shape, gated: the probe lists
        # Kuwait/Qatar, but the visible UI is not Area — so
        # NOTHING is clicked (no option click, no card retry)
        # and the comparison returns partial, not attempted.
        page = _FakeAreaPage(visible=False, record=False)
        st, pick, det = R._s_worldle_play_comparison(
            page, "worldle", {}, "area", DS,
            ("larger surface area",),
            {"kuwait": 17818, "qatar": 11000},
            "size", "area", verify_store=True)
        self.assertEqual(st, "partial")
        self.assertIsNone(pick)
        self.assertIn("NOT clicked", det)
        self.assertIn("visible Area UI not verified", det)
        self.assertFalse(any("worldle-comparison-click" in c
                             and "card-click" not in c
                             for c in page.calls))
        self.assertFalse(any("worldle-comparison-card-click" in c
                             for c in page.calls))

    def test_verified_round_click_proceeds_and_records_pick(self):
        page = _FakeAreaPage(visible=True, record=True)
        st, pick, det = R._s_worldle_play_comparison(
            page, "worldle", {}, "area", DS,
            ("larger surface area",),
            {"kuwait": 17818, "qatar": 11000},
            "size", "area", verify_store=True)
        self.assertEqual(st, "attempted")
        self.assertEqual(pick, "Kuwait")
        self.assertTrue(any("worldle-comparison-click" in c
                            for c in page.calls))

    def test_gate_skipped_when_probe_unscripted(self):
        # Legacy fake: the visible probe returns None (never
        # executed). The gate is not enforced on a probe that
        # never ran; the comparison-probe evidence arbitrates
        # (and the skip is logged).
        logs = []
        orig = R._dbg
        R._dbg = _dbg_recorder(logs)
        try:
            page = _FakeAreaPage(visible=None, record=True)
            st, pick, det = R._s_worldle_play_comparison(
                page, "worldle", {}, "area", DS,
                ("larger surface area",),
                {"kuwait": 17818, "qatar": 11000},
                "size", "area", verify_store=True)
        finally:
            R._dbg = orig
        self.assertEqual(st, "attempted")
        self.assertEqual(pick, "Kuwait")
        self.assertTrue(any("gate not enforced" in m for m in logs))

    def test_gate_is_area_only_gdp_not_blocked(self):
        # The visible-Area gate must not leak into GDP: a GDP
        # comparison proceeds even when the Area UI is not
        # showing (GDP has its own round UI).
        page = _FakeAreaPage(visible=False, record=True, mode="gdp")
        st, pick, det = R._s_worldle_play_comparison(
            page, "worldle", {}, "gdp", DS,
            ("highest", "per capita gdp"),
            {"kuwait": 30000, "qatar": 20000},
            "gdpPerCapita", "gdp", verify_store=True)
        self.assertEqual(st, "attempted")
        self.assertEqual(pick, "Kuwait")


# ---------------------------------------------------------------- Capital

class TestCapitalDotsIter21(unittest.TestCase):
    FIT = (1072.5, 422.7)

    def test_dot_name_via_path_shaped_dot(self):
        # Iter-21: the 4 frame-visible dots are path-shaped;
        # dot-name matching must run over path dots, and the
        # target is the dot's centre.
        dots = [_dot(1085.0, 285.0, tag="path"),
                _dot(1010.0, 440.0, tag="path"),
                _dot(1090.0, 435.0, text="Kuwait City", tag="path"),
                _dot(1110.0, 645.0, tag="path")]
        probe = {"found": True, "dots": dots, "n_paths": 29,
                 "n_dots": 4, "in_fill": False, "corrected": None}
        got = R._worldle_capital_pick_pin_target(
            probe, self.FIT, "Kuwait City")
        self.assertIsNotNone(got)
        self.assertEqual(got["method"], "dot-name")
        self.assertEqual((got["x"], got["y"]), (1090.0, 435.0))
        self.assertEqual(got["dot_tag"], "path")

    def test_dot_name_via_use_shaped_dot(self):
        # Iter-21: <use href="#pin"> markers are enumerated now;
        # a name-matched <use> dot is clicked at its centre.
        dots = [_dot(1085.0, 285.0, tag="use"),
                _dot(1090.0, 435.0, text="Kuwait City", tag="use")]
        probe = {"found": True, "dots": dots, "n_paths": 0,
                 "n_dots": 2, "in_fill": False, "corrected": None}
        got = R._worldle_capital_pick_pin_target(
            probe, self.FIT, "Kuwait City")
        self.assertEqual(got["method"], "dot-name")
        self.assertEqual(got["dot_tag"], "use")
        self.assertEqual((got["x"], got["y"]), (1090.0, 435.0))

    def test_circle_dots_still_found(self):
        dots = [_dot(1085.0, 285.0, tag="circle"),
                _dot(1090.0, 435.0, label="Kuwait City", tag="circle")]
        probe = {"found": True, "dots": dots, "n_paths": 0,
                 "n_dots": 2, "in_fill": False, "corrected": None}
        got = R._worldle_capital_pick_pin_target(
            probe, self.FIT, "Kuwait City")
        self.assertEqual(got["method"], "dot-name")
        self.assertEqual((got["x"], got["y"]), (1090.0, 435.0))

    def test_verified_fit_point_snaps_to_dot_centre(self):
        # Iter-21: the bbox-fit point is in-fill AND coincides
        # with a dot (within 12px) — click the dot's centre,
        # not the nearby coordinate.
        dots = [_dot(1095.0, 440.0), _dot(1010.0, 440.0)]
        probe = {"found": True, "dots": dots, "n_paths": 0,
                 "n_dots": 2, "in_fill": True, "corrected": None}
        got = R._worldle_capital_pick_pin_target(
            probe, (1090.0, 435.0), "Kuwait City")
        self.assertEqual(got["method"], "bbox-fit-in-fill-dot-centre")
        self.assertEqual((got["x"], got["y"]), (1095.0, 440.0))
        self.assertEqual(got["dot_index"], 0)

    def test_far_dot_never_snapped(self):
        # The iter-16 anti-pattern, pinned: a dot 140px-ish away
        # from the verified point must NOT be clicked. Snap is
        # coincidence-only (<=12px), never nearest-dot.
        dots = [_dot(1090.0, 435.0), _dot(1010.0, 440.0)]
        probe = {"found": True, "dots": dots, "n_paths": 0,
                 "n_dots": 2, "in_fill": True, "corrected": None}
        got = R._worldle_capital_pick_pin_target(
            probe, self.FIT, "Kuwait City")
        self.assertEqual(got["method"], "bbox-fit-in-fill")
        self.assertEqual((got["x"], got["y"]), self.FIT)

    def test_in_fill_search_snaps_to_dot_centre(self):
        dots = [_dot(1090.0, 435.0), _dot(1010.0, 440.0)]
        probe = {"found": True, "dots": dots, "n_paths": 0,
                 "n_dots": 2, "in_fill": False,
                 "corrected": {"x": 1092.0, "y": 437.0, "dist": 5.0}}
        got = R._worldle_capital_pick_pin_target(
            probe, self.FIT, "Kuwait City")
        self.assertEqual(got["method"], "in-fill-search-dot-centre")
        self.assertEqual((got["x"], got["y"]), (1090.0, 435.0))

    def test_in_fill_search_no_snap_when_far(self):
        dots = [_dot(1090.0, 435.0)]
        probe = {"found": True, "dots": dots, "n_paths": 0,
                 "n_dots": 1, "in_fill": False,
                 "corrected": {"x": 1000.0, "y": 400.0, "dist": 80.0}}
        got = R._worldle_capital_pick_pin_target(
            probe, self.FIT, "Kuwait City")
        self.assertEqual(got["method"], "in-fill-search")
        self.assertEqual((got["x"], got["y"]), (1000.0, 400.0))

    def test_probe_source_unifies_with_markers_counter(self):
        src = inspect.getsource(R._worldle_capital_pinpoint_probe)
        # <use>/<g>/<image> shapes enumerated (iter-21).
        self.assertIn("path, rect, use, g, div, span, button, image",
                      src)
        # Markers-counter selector pass over every root.
        self.assertIn("svg use", src)
        self.assertIn("[class*=dot i]", src)
        # <use> dots name themselves via the referenced symbol.
        self.assertIn("xlink:href", src)

    def test_click_dot_element_recognises_use_and_g(self):
        src = inspect.getsource(
            R._worldle_capital_click_dot_element)
        self.assertIn("'use', 'g'", src)
        self.assertIn("data-marker", src)


# ----------------------------------------------------------------- Banner

class TestBannerProbeIter21(unittest.TestCase):
    def test_unverified_label_rescanned_and_logged_never_absent(self):
        logs = []
        orig = R._dbg
        R._dbg = _dbg_recorder(logs)
        try:
            page = _FakePage(
                lambda js, arg=None: "unverified-no-element-match:"
                '{"roots": 1, "shadow_hosts": 0}')
            R._dismiss_teuteuf_banner(page)
        finally:
            R._dbg = orig
        self.assertEqual(len(page.calls), 2,
                         "unverified no-match must re-scan once")
        self.assertTrue(any("unverified-no-element-match:" in m
                            for m in logs))
        self.assertFalse(any(m.endswith("teuteuf edu banner: absent")
                             for m in logs))

    def test_legacy_absent_still_rescanned(self):
        # Iter-18 behaviour preserved for legacy callers/fakes:
        # a bare 'absent' re-scans once and is logged as-is.
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

    def test_iframe_corner_click_then_unverified_means_closed(self):
        logs = []
        orig = R._dbg
        R._dbg = _dbg_recorder(logs)
        try:
            state = {"n": 0}

            def _ev(js, arg=None):
                if "HIDE-IFRAME" in js:
                    return True
                state["n"] += 1
                if state["n"] == 1:
                    return ("iframe-banner:" +
                            '{"x": 0.0, "y": 0.0, "w": 1920.0, '
                            '"h": 90.0}')
                return "unverified-no-element-match:{}"

            page = _FakePage(_ev)
            R._dismiss_teuteuf_banner(page)
        finally:
            R._dbg = orig
        # Corner click at (x + w - 16, y + 16) = (1904, 16) for a
        # full-width 1920px banner iframe.
        self.assertIn((1904.0, 16.0), page.mouse.clicks)
        self.assertTrue(any("closed-iframe-via-corner-click" in m
                            for m in logs))

    def test_banner_source_new_passes_and_honest_label(self):
        src = inspect.getsource(R._dismiss_teuteuf_banner)
        # Honest no-match label (never 'absent' for a
        # frame-visible banner).
        self.assertIn("unverified-no-element-match:", src)
        # Rendered-text pass (innerText crosses shadow roots).
        self.assertIn("innerText", src)
        self.assertIn("innerCands", src)
        # Widened iframe-shape guards (full-width banner iframe).
        self.assertIn("r.width <= 2000", src)
        self.assertIn("r.height <= 320", src)
        # Closed-shadow host enumeration in the diagnostics.
        self.assertIn("custom_hosts", src)
        self.assertIn("has_shadow", src)
        # Legacy labels still handled (iter-18/20 compat).
        self.assertIn("absent-candidates:", src)


# --------------------------------------------------------------------- G4

class TestLiveRoundsReaderIter21(unittest.TestCase):
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

    def test_open_dropdown_with_13_entries_still_returns_all(self):
        page = self._page(
            [{"entries": list(self.ENTRIES_13), "container": True}])
        got = R._s_worldle_live_round_modes(page, "worldle")
        self.assertIsNotNone(got)
        self.assertEqual(len(got["mapped"]), 13)
        self.assertEqual(got["raw"], self.ENTRIES_13)

    def test_failure_logs_opened_menu_container_dump(self):
        # Toggle opens, scan finds no container — but the scan
        # now returns the top scored containers; the reader logs
        # that dump (the opened menu's ACTUAL markup) instead of
        # a bare "no container".
        logs = []
        orig = R._dbg
        R._dbg = _dbg_recorder(logs)
        try:
            diag = [{"tag": "DIV", "cls": "css-1a2b3c-menu",
                     "role": "", "rect": {"x": 800, "y": 60,
                                          "w": 260, "h": 400},
                     "score": 13, "leaves": 14,
                     "sample": ["Country", "Neighbours", "Capital"]}]
            page = self._page([{"entries": [], "container": False,
                                "diag": diag}])
            self.assertIsNone(
                R._s_worldle_live_round_modes(page, "worldle"))
        finally:
            R._dbg = orig
        self.assertTrue(any("container dump" in m for m in logs))
        self.assertTrue(any("css-1a2b3c-menu" in m for m in logs))

    def test_scan_source_content_fallback_and_no_forbidden_strings(self):
        src = inspect.getsource(R._s_worldle_live_round_modes)
        # Content-based fallback + container dump.
        self.assertIn("diagCands", src)
        self.assertIn("purity", src)
        self.assertIn("diag: diagCands", src)
        # Markup-based pass kept (labelKeys / aria-controls).
        self.assertIn("labelKeys", src)
        self.assertIn("aria-controls", src)
        self.assertIn("hasTextChild", src)
        self.assertIn("popover", src)
        # Iter-19 constraints kept: no offsetParent visibility,
        # no nav/header/footer ancestry exclusion.
        self.assertNotIn("offsetParent !== null", src)
        self.assertNotIn("closest('nav, header, footer')", src)


# ----------------------------------------------------------------- Waffle

class TestWaffleCardIter21(unittest.TestCase):
    def test_waffle_facts_stat_parses_swaps(self):
        self.assertEqual(
            R._waffle_facts_stat(
                "11 swaps, greens 7/21->21/21 (GIANT GRAPE ABBEY)"),
            ("11", "swaps used"))
        self.assertIsNone(R._waffle_facts_stat("solved, no swaps"))
        self.assertIsNone(R._waffle_facts_stat(""))
        self.assertIsNone(R._waffle_facts_stat(None))

    def test_waffle_card_never_shows_green_rows(self):
        with tempfile.TemporaryDirectory() as td:
            out = str(Path(td) / "facts.png")
            stats = DP.generate_facts(
                out, "Waffle", "October 18, 2026", "GIANT",
                [{"turn": 1, "guess": "GIANT", "pattern": ""}])
            self.assertTrue(Path(out).exists())
            labels = [lab for _val, lab in stats]
            self.assertNotIn("green rows", labels)

    def test_waffle_card_with_swaps_stat(self):
        with tempfile.TemporaryDirectory() as td:
            out = str(Path(td) / "facts.png")
            stats = DP.generate_facts(
                out, "Waffle", "October 18, 2026", "GIANT",
                [{"turn": 1, "guess": "GIANT", "pattern": ""}],
                extra_stat=("11", "swaps used"))
            labels = [lab for _val, lab in stats]
            self.assertIn("swaps used", labels)
            self.assertNotIn("green rows", labels)
            self.assertIn(("11", "swaps used"), stats)

    def test_non_waffle_card_keeps_green_rows(self):
        with tempfile.TemporaryDirectory() as td:
            out = str(Path(td) / "facts.png")
            stats = DP.generate_facts(
                out, "Wordle", "October 18, 2026", "CRANE",
                [{"turn": 1, "guess": "CRANE", "pattern": "GGGGG"}])
            labels = [lab for _val, lab in stats]
            self.assertIn("green rows", labels)

    def test_runner_wires_swaps_stat_for_waffle(self):
        src = inspect.getsource(R.run_one)
        self.assertIn("_waffle_facts_stat(evidence)", src)
        self.assertIn("extra_stat=", src)


if __name__ == "__main__":
    unittest.main()
