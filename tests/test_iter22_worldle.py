"""Fix-loop iteration 22 tests — WORLDLE + WAFFLE CARD (evidence-grounded
in ANALYSIS19 §§2-6).

- Capital (P0): nearest-dot-to-projected-coordinates targeting (the
  dots are unlabelled; the widget only accepts a click ON a dot);
  NEVER retarget onto a path-piece centre when the fit point is
  near a dot; per-tag candidate counts + rejection reasons; the
  marker-counter elements dumped + diffed; <use> href fill resolved.
- Area (P0): visible-round verification on the NORMAL switch path;
  gate on viewport-intersection + opacity + active-panel membership
  (not rect > 0), gate failure returns partial WITH a logged reason;
  comparison click restricted to the verified-visible Area container
  with tag/class/rect/coordinates logged; re-verify after the click
  and BEFORE submit — no submit when the view flipped.
- Teuteuf (P1): hollow-box diagnostic + coordinate X click via
  elementFromPoint host, with re-verification; never lie.
- G4 (P1): scan anchored to the toggle (aria-controls / sibling /
  elementsFromPoint) + custom-element containers enumerated.
- Waffle (P2): breakdown card fits ALL N rows (auto-shrink).

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
FIT = (1072.5, 422.7)  # ANALYSIS19 Capital fit point
CONTAINER = {"tag": "SECTION", "cls": "area-panel", "id": "area",
             "rect": {"x": 400, "y": 200, "w": 1120, "h": 600}}


# ---------------------------------------------------------------- Capital

class TestCapitalNearestDotIter22(unittest.TestCase):
    def test_four_unlabelled_dots_nearest_wins(self):
        # ANALYSIS19 frame coords: exactly 4 unlabelled blue dots;
        # the projected point is ~21px from the mid-right dot
        # (1085,440 — the Kuwait City position).
        dots = [_dot(1085.0, 285.0), _dot(1010.0, 440.0),
                _dot(1085.0, 440.0), _dot(1105.0, 640.0)]
        probe = {"found": True, "dots": dots, "n_paths": 29,
                 "n_dots": 4, "in_fill": False, "corrected": None}
        got = R._worldle_capital_pick_pin_target(
            probe, FIT, "Kuwait City")
        self.assertIsNotNone(got)
        self.assertEqual(got["method"], "nearest-dot")
        self.assertEqual((got["x"], got["y"]), (1085.0, 440.0))
        self.assertEqual(got["dot_index"], 2)

    def test_nearest_dot_tie_is_ambiguous(self):
        dots = [_dot(1080.0, 430.0), _dot(1065.0, 415.0)]
        probe = {"found": True, "dots": dots, "n_paths": 0,
                 "n_dots": 2, "in_fill": False, "corrected": None}
        self.assertIsNone(R._worldle_capital_pick_pin_target(
            probe, FIT, "Kuwait City"))

    def test_dot_140px_away_never_picked(self):
        dots = [_dot(1300.0, 700.0)]
        probe = {"found": True, "dots": dots, "n_paths": 0,
                 "n_dots": 1, "in_fill": True, "corrected": None}
        got = R._worldle_capital_pick_pin_target(
            probe, FIT, "Kuwait City")
        self.assertEqual(got["method"], "bbox-fit-in-fill")
        self.assertEqual((got["x"], got["y"]), FIT)

    def test_nearest_dot_helper_retarget_case(self):
        # The ANALYSIS19 retarget case: fit (1072.5,422.7) vs
        # piece centre (1154.6,415.1) 82.4px away. The picker
        # must stay on the dot (21px), never the path centre.
        dots = [_dot(1085.0, 285.0), _dot(1010.0, 440.0),
                _dot(1085.0, 440.0), _dot(1105.0, 640.0)]
        near = R._worldle_capital_nearest_dot(dots, FIT)
        self.assertIsNotNone(near)
        self.assertAlmostEqual(near[1], 21.4, places=0)
        self.assertEqual((near[0]["x"], near[0]["y"]),
                         (1085.0, 440.0))

    def test_caller_probes_before_retarget_and_refuses_it(self):
        src = inspect.getsource(R._s_worldle_play_capital)
        probe_i = src.index(
            "_worldle_capital_pinpoint_probe(page, px, py)")
        piece_i = src.index(
            "_worldle_capital_piece_path(page, px, py)")
        self.assertLess(probe_i, piece_i,
                        "probe/picker must run on the projected "
                        "point BEFORE any piece retarget")
        self.assertIn("piece retarget SKIPPED", src)
        # The REFUSED log line is split across two string
        # literals in source ("piece retarget " + "REFUSED ...").
        self.assertIn("REFUSED (fit point near", src)
        self.assertIn('"nearest-dot"', src)

    def test_element_click_covers_nearest_dot(self):
        src = inspect.getsource(R._s_worldle_play_capital)
        self.assertIn('"dot-name", "nearest-dot"', src)
        self.assertIn("-dot-centre", src)


class TestCapitalProbeDiagnosticsIter22(unittest.TestCase):
    MARKERS = [
        {"tag": "use", "cls": "", "id": "", "fill": "",
         "computed_fill": "rgb(0, 0, 0)",
         "resolved_fill": "rgb(37, 99, 235)",
         "rect": {"x": 1080, "y": 435, "w": 10, "h": 10},
         "parent_tag": "g", "parent_cls": "dots"},
        {"tag": "use", "cls": "", "id": "", "fill": "",
         "computed_fill": "rgb(0, 0, 0)",
         "resolved_fill": "rgb(37, 99, 235)",
         "rect": {"x": 1080, "y": 280, "w": 10, "h": 10},
         "parent_tag": "g", "parent_cls": "dots"},
    ]

    def test_diagnostics_wrapper_returns_per_tag_and_markers(self):
        def _ev(js, arg=None):
            if "worldle-capital-probe-diagnostics" in js:
                return {"per_tag": {"use": 4, "g": 1},
                        "rejected": {"not-named-not-blue": 0,
                                     "accepted-as-dot-candidate": 4},
                        "marker_elements": list(self.MARKERS),
                        "marker_count": 2, "svg_found": True}
            return None

        got = R._worldle_capital_probe_diagnostics(_FakePage(_ev))
        self.assertEqual(got["diag"]["per_tag"], {"use": 4, "g": 1})
        self.assertEqual(len(got["marker_elements"]), 2)
        self.assertEqual(got["marker_elements"][0]["resolved_fill"],
                         "rgb(37, 99, 235)")

    def test_marker_dot_diff(self):
        dots = [_dot(1085.0, 440.0), _dot(1085.0, 285.0)]
        diff = R._worldle_capital_marker_dot_diff(self.MARKERS, dots)
        self.assertIn("markers_without_dot=0", diff)
        far = [{"tag": "circle", "rect": {"x": 100, "y": 100,
                                          "w": 10, "h": 10}}]
        diff2 = R._worldle_capital_marker_dot_diff(far, dots)
        self.assertIn("markers_without_dot=1", diff2)
        self.assertIn("dots_without_marker=2", diff2)

    def test_summary_carries_diag_and_diff(self):
        dots = [_dot(1085.0, 440.0), _dot(1085.0, 285.0)]
        probe = {"found": True, "dots": dots,
                 "diag": {"per_tag": {"use": 4},
                          "rejected": {"not-named-not-blue": 1}},
                 "marker_elements": list(self.MARKERS)}
        s = R._worldle_capital_dot_attempt_summary(
            probe, "Kuwait City", None)
        self.assertIn("per_tag={'use': 4}", s)
        self.assertIn("marker_elements(2)", s)
        self.assertIn("diff=markers=2 dots=2", s)

    def test_probe_source_resolves_use_href_fill(self):
        src = inspect.getsource(R._worldle_capital_pinpoint_probe)
        self.assertIn("resolved2", src)
        self.assertIn("getElementById", src)
        self.assertIn("resolved_fill", src)


# ------------------------------------------------------------------- Area

class _FakeSwitchDropdownPage(_FakePage):
    def __init__(self, store_mode="area", visible=False, reason=""):
        super().__init__()
        self.store_mode = store_mode
        self.visible = visible
        self.reason = reason

    def evaluate(self, js, arg=None):
        self.calls.append(js)
        if "worldle-visible-area" in js:
            return {"visible": self.visible, "signals": [],
                    "prompt_visible": self.visible, "selected_tab": "",
                    "prompt_text": "", "next_round_visible": False,
                    "container": CONTAINER if self.visible else None,
                    "reason": self.reason, "rejected": 2,
                    "rejection_reasons": {"off-viewport": 2}}
        if "localStorage" in js:
            if arg == "game_round":
                return {"gameMode": self.store_mode}
            return {}
        return None


class TestAreaSwitchNormalPathIter22(unittest.TestCase):
    def test_store_area_but_visible_gate_fails_returns_false(self):
        logs = []
        orig = R._dbg
        R._dbg = _dbg_recorder(logs)
        try:
            page = _FakeSwitchDropdownPage(
                store_mode="area", visible=False,
                reason="no truly-visible Area prompt (rejected=2)")
            got = R._s_worldle_switch_dropdown_only(
                page, "worldle", "area", DS)
        finally:
            R._dbg = orig
        # The iter-21 shape: store says area, SPA still shows
        # PopulationCurrency — the NORMAL switch path must NOT
        # claim the switch, so the caller falls through to the
        # full switch (Next-round recovery).
        self.assertFalse(got)
        self.assertTrue(any("visible-round gate failed" in m
                            for m in logs))
        self.assertTrue(any("no truly-visible Area prompt" in m
                            for m in logs))

    def test_store_area_and_visible_returns_true(self):
        page = _FakeSwitchDropdownPage(store_mode="area", visible=True)
        self.assertTrue(R._s_worldle_switch_dropdown_only(
            page, "worldle", "area", DS))

    def test_switch_dropdown_source_verifies_visible(self):
        src = inspect.getsource(R._s_worldle_switch_dropdown_only)
        self.assertIn("_s_worldle_visible_area_ui(page)", src)
        self.assertIn("visible-round gate", src)


class TestAreaGateIter22(unittest.TestCase):
    def test_gate_source_requires_truly_visible(self):
        src = inspect.getsource(R._s_worldle_visible_area_ui)
        self.assertIn("off-viewport", src)
        self.assertIn("opacity", src)
        self.assertIn("aria-hidden", src)
        self.assertIn("rejection_reasons", src)
        self.assertIn("container", src)
        self.assertIn("promptVisible && panelActive", src)
        self.assertNotIn("visible: promptVisible ||", src)

    def test_gate_failure_returns_partial_with_reason(self):
        logs = []
        orig = R._dbg
        R._dbg = _dbg_recorder(logs)
        try:
            page = _FakeAreaPageIter22(visible=False, record=False,
                                       flip=False)
            st, pick, det = R._s_worldle_play_comparison(
                page, "worldle", {}, "area", DS,
                ("larger surface area",),
                {"kuwait": 17818, "qatar": 11000},
                "size", "area", verify_store=True)
        finally:
            R._dbg = orig
        self.assertEqual(st, "partial")
        self.assertIsNone(pick)
        self.assertIn("NOT clicked", det)
        self.assertIn("no truly-visible Area prompt", det)
        self.assertTrue(any("gate FAILED" in m for m in logs))
        self.assertTrue(any("no truly-visible Area prompt" in m
                            for m in logs))
        self.assertFalse(any("worldle-comparison-click" in c
                             and "card-click" not in c
                             for c in page.calls))


class _FakeAreaPageIter22(_FakePage):
    """Fake for _s_worldle_play_comparison: visible-area gate ->
    probe -> click -> selected-state -> re-verify -> submit."""

    def __init__(self, visible=True, record=False, flip=False):
        super().__init__()
        self.visible = visible
        self.record = record
        self.flip = flip
        self.store = []
        self.clicked = []
        self.visible_calls = 0

    def evaluate(self, js, arg=None):
        self.calls.append(js)
        if "worldle-visible-area" in js:
            self.visible_calls += 1
            vis = self.visible
            if self.flip and self.clicked:
                vis = False
            return {"visible": vis, "signals": [],
                    "prompt_visible": vis, "selected_tab": "",
                    "prompt_text": "", "next_round_visible": False,
                    "container": CONTAINER if vis else None,
                    "reason": ("no truly-visible Area prompt "
                               "(rejected=2)") if not vis else "",
                    "rejected": 0 if vis else 2,
                    "rejection_reasons": {}}
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
            return [True, "Kuwait", "button", "BUTTON", "cls",
                    {"x": 500, "y": 300, "w": 200, "h": 80},
                    600, 340]
        if "worldle-comparison-selected-state" in js:
            sel = bool(self.record)
            return {"selected": ([{"name": "Kuwait"}] if sel else []),
                    "selected_name": "Kuwait" if sel else "",
                    "options": [{"name": "Kuwait", "selected": sel}],
                    "wanted_selected": sel,
                    "any_selected": sel}
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


class TestAreaClickContainerIter22(unittest.TestCase):
    def test_click_passes_container_and_logs_target(self):
        seen = {}

        def _ev(js, arg=None):
            if "worldle-comparison-click" in js:
                seen["arg"] = arg
                return [True, "Kuwait", "button", "BUTTON", "opt-cls",
                        {"x": 500, "y": 300, "w": 200, "h": 80},
                        600, 340]
            return None

        page = _FakePage(_ev)
        ok, detail, via = R._s_worldle_click_comparison_option(
            page, "Kuwait", container=CONTAINER)
        self.assertTrue(ok)
        self.assertEqual(via, "button")
        self.assertEqual(seen["arg"]["container"], CONTAINER)
        self.assertIn("tag=BUTTON", detail)
        self.assertIn("cls=opt-cls", detail)
        self.assertIn("rect={'x': 500, 'y': 300", detail)
        self.assertIn("centre=(600, 340)", detail)

    def test_click_source_restricts_to_container(self):
        src = inspect.getsource(R._s_worldle_click_comparison_option)
        self.assertIn("inContainer", src)
        self.assertIn("container.rect", src)
        self.assertIn("no candidate inside verified Area container",
                      src)
        src2 = inspect.getsource(R._s_worldle_click_comparison_card)
        self.assertIn("inContainer", src2)

    def test_verified_round_click_proceeds_and_records_pick(self):
        logs = []
        orig = R._dbg
        R._dbg = _dbg_recorder(logs)
        try:
            page = _FakeAreaPageIter22(visible=True, record=True,
                                       flip=False)
            st, pick, det = R._s_worldle_play_comparison(
                page, "worldle", {}, "area", DS,
                ("larger surface area",),
                {"kuwait": 17818, "qatar": 11000},
                "size", "area", verify_store=True)
        finally:
            R._dbg = orig
        self.assertEqual(st, "attempted")
        self.assertEqual(pick, "Kuwait")

    def test_post_click_flip_never_submits(self):
        logs = []
        orig = R._dbg
        R._dbg = _dbg_recorder(logs)
        try:
            page = _FakeAreaPageIter22(visible=True, record=True,
                                       flip=True)
            st, pick, det = R._s_worldle_play_comparison(
                page, "worldle", {}, "area", DS,
                ("larger surface area",),
                {"kuwait": 17818, "qatar": 11000},
                "size", "area", verify_store=True)
        finally:
            R._dbg = orig
        # The click flipped the view (attempt-2 shape): no
        # submit into the wrong round.
        self.assertEqual(st, "partial")
        self.assertIsNone(pick)
        self.assertIn("NOT submitted", det)
        self.assertTrue(any("view FLIPPED" in m for m in logs))
        self.assertFalse(any("Guess pressed once" in m for m in logs))


# ----------------------------------------------------------------- Banner

class TestTeuteufHollowBoxIter22(unittest.TestCase):
    def _unverified(self, hollow):
        import json as _json
        return ("unverified-no-element-match:"
                + _json.dumps({"roots": 1, "shadow_hosts": 0,
                               "hollow_boxes": hollow}))

    def test_hollow_box_x_click_and_box_gone_means_closed(self):
        logs = []
        orig = R._dbg
        R._dbg = _dbg_recorder(logs)
        try:
            state = {"n": 0}
            box = {"tag": "DIV", "cls": "banner-host", "id": "",
                   "rect": {"x": 0, "y": 0, "w": 1920, "h": 90},
                   "children": 0,
                   "host_at_centre": {"tag": "DIV", "cls": "",
                                      "id": ""}}

            def _ev(js, arg=None):
                state["n"] += 1
                if state["n"] == 1:
                    return self._unverified([box])
                return self._unverified([])

            page = _FakePage(_ev)
            R._dismiss_teuteuf_banner(page)
        finally:
            R._dbg = orig
        # X at (x + w - 16, y + 16) = (1904, 16).
        self.assertIn((1904.0, 16.0), page.mouse.clicks)
        self.assertTrue(any("closed-hollow-box-via-coordinate-X" in m
                            for m in logs))

    def test_hollow_box_still_present_never_claimed_dismissed(self):
        logs = []
        orig = R._dbg
        R._dbg = _dbg_recorder(logs)
        try:
            box = {"tag": "DIV", "cls": "banner-host", "id": "",
                   "rect": {"x": 0, "y": 0, "w": 1920, "h": 90},
                   "children": 0, "host_at_centre": None}

            def _ev(js, arg=None):
                return self._unverified([box])

            page = _FakePage(_ev)
            R._dismiss_teuteuf_banner(page)
        finally:
            R._dbg = orig
        self.assertIn((1904.0, 16.0), page.mouse.clicks)
        self.assertTrue(any("hollow-box-X-click-did-not-dismiss" in m
                            for m in logs))
        self.assertFalse(any("closed-hollow-box" in m for m in logs))

    def test_banner_source_has_hollow_box_diagnostic(self):
        src = inspect.getsource(R._dismiss_teuteuf_banner)
        self.assertIn("hollow_boxes", src)
        self.assertIn("host_at_centre", src)
        self.assertIn("elementFromPoint", src)
        self.assertIn("closed-hollow-box-via-coordinate-X", src)


# --------------------------------------------------------------------- G4

class TestLiveRoundsReaderIter22(unittest.TestCase):
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

    def test_anchored_scan_with_custom_element_returns_13(self):
        # The menu container is a CUSTOM element (<x-menu>)
        # with no role/class the old passes could match; the
        # scan anchored to the toggle returns all 13 entries.
        page = self._page([{"entries": list(self.ENTRIES_13),
                            "container": True,
                            "anchored": {"tag": "X-MENU", "cls": "",
                                         "role": "",
                                         "via": "aria-controls",
                                         "score": 13, "leaves": 13}}])
        got = R._s_worldle_live_round_modes(page, "worldle")
        self.assertIsNotNone(got)
        self.assertEqual(len(got["mapped"]), 13)
        self.assertEqual(got["raw"], self.ENTRIES_13)

    def test_anchored_container_logged_on_failure(self):
        logs = []
        orig = R._dbg
        R._dbg = _dbg_recorder(logs)
        try:
            page = self._page([{"entries": [], "container": False,
                                "diag": [],
                                "anchored": {"tag": "X-MENU",
                                             "cls": "", "role": "",
                                             "via": "elementsFromPoint-ancestor",
                                             "score": 0, "leaves": 0}}])
            self.assertIsNone(
                R._s_worldle_live_round_modes(page, "worldle"))
        finally:
            R._dbg = orig
        self.assertTrue(any("anchored container" in m for m in logs))
        self.assertTrue(any("X-MENU" in m for m in logs))

    def test_scan_source_anchored_and_custom_elements(self):
        src = inspect.getsource(R._s_worldle_live_round_modes)
        self.assertIn("anchored", src)
        self.assertIn("aria-controls", src)
        self.assertIn("elementsFromPoint", src)
        self.assertIn("includes('-')", src)


# ----------------------------------------------------------------- Waffle

class TestWaffleCardIter22(unittest.TestCase):
    def test_facts_row_height_fits_six_rows(self):
        card_h, gap = DP._facts_row_height(6)
        self.assertEqual(card_h, 65)
        # 520 + 6 rows + 5 gaps ends exactly at the footer line.
        self.assertEqual(520 + 6 * card_h + 5 * gap, DP.H - 110)

    def test_facts_row_height_single_row_unchanged(self):
        card_h, _gap = DP._facts_row_height(1)
        self.assertEqual(card_h, 88)

    def test_generate_facts_draws_all_six_rows(self):
        src = inspect.getsource(DP.generate_facts)
        self.assertNotIn("for s in rows[:5]:", src)
        self.assertIn("for s in rows:", src)
        self.assertIn("_facts_row_height", src)
        steps = [{"turn": i + 1, "guess": w, "pattern": ""}
                 for i, w in enumerate(
                     ["GIANT", "GRAPE", "ABBEY", "AMBER", "EGRET",
                      "TRYST"])]
        with tempfile.TemporaryDirectory() as td:
            out = str(Path(td) / "facts.png")
            stats = DP.generate_facts(
                out, "Waffle", "October 18, 2026", "GIANT", steps,
                scoring=False, extra_stat=("11", "swaps used"))
            self.assertTrue(Path(out).exists())
            labels = [lab for _val, lab in stats]
            self.assertIn("swaps used", labels)


if __name__ == "__main__":
    unittest.main()
