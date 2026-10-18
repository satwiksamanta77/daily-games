"""Fix-loop iteration 25 tests — WORLDLE AREA (evidence-grounded in
ANALYSIS22, run 39095140394, RC-new-1).

Frame-proven defect: the Area comparison picker clicked a 40x40
chrome BUTTON (page rect x=772, y=62, centre (792, 82) — a
prev/next nav arrow in the header row) instead of either country
card, which navigated away (view flipped to PopulationCurrency).
Frame f0331 shows the real widget: "Which country has a larger
surface area?" with TWO SILHOUETTE CARDS (Kuwait left, Qatar
right), each with a light-blue LABEL BAR ("Kuwait" / "Qatar")
beneath it — image + label-bar elements, NOT <button>s.

Root causes fixed here:
1. The click matcher's sibling-label helper climbed 3 ancestors
   and scanned whole subtrees, so ANY element sharing a high
   ancestor with the cards (e.g. the header nav button) "matched"
   every country name. Names now come only from the element
   itself, a DIRECT sibling label, or a descendant label inside
   the candidate — never an ancestor scan.
2. Buttons were tried before cards, so chrome won. For Area the
   CARD is now attempted first, the button only as fallback.
   (GDP keeps the proven buttons-first order — its options ARE
   buttons.)
3. Small (~40x40) controls in the top header band are excluded
   as chrome in every comparison JS path (probe / click / card /
   loose / selected-state) and in the Python pick.
4. The visible-area container now counts silhouette IMAGES as
   option-like descendants, so the container actually bounds the
   card widget (the old button-only count never matched it).
5. Selected-state verification GATES submit for Area: when the
   probe read the widget's options and none is selected after
   card + button attempts, the pick is pick-failed and nothing
   is submitted. (When the probe itself read nothing, the old
   submit-anyway + store-verify arbitration still applies.)
6. A view-flip after the click is pick-FAILED, never pick-ok:
   the flip check now consults the visible-round probe AND the
   store/header evidence (game_round / visible header naming
   another round), and a flip returns partial with no submit.

All tests are synthetic/local: no network, no browser, no CI run.
"""
import inspect
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import runner as R


def _dbg_recorder(logs):
    def _rec(gid, msg):
        logs.append(f"[{gid}] {msg}")
    return _rec


_CARD_KUWAIT = {"name": "Kuwait", "x": 650, "y": 500,
                "has_img": True,
                "rect": {"x": 500, "y": 300, "w": 300, "h": 400},
                "text": "Image Quiz Item", "aria": "", "alt": "",
                "title": "",
                "src": "https://cdn-assets.teuteuf.fr/country-shapes/kw.svg"}
_CARD_QATAR = {"name": "Qatar", "x": 1050, "y": 500,
               "has_img": True,
               "rect": {"x": 900, "y": 300, "w": 300, "h": 400},
               "text": "Image Quiz Item", "aria": "", "alt": "",
               "title": "",
               "src": "https://cdn-assets.teuteuf.fr/country-shapes/qa.svg"}
# The iter-24 wrong target: a 40x40 header nav button at (772, 62).
_CHROME_BUTTON = {"name": "Kuwait", "x": 792, "y": 82,
                  "has_img": False,
                  "rect": {"x": 772, "y": 62, "w": 40, "h": 40},
                  "text": "", "aria": "", "alt": "", "title": "",
                  "src": ""}


# ------------------------------------------- chrome-rect helper + pick

class TestChromeRectIter25(unittest.TestCase):
    def test_iter24_header_button_is_chrome(self):
        # The exact iter-24 wrong target.
        self.assertTrue(R._worldle_area_is_chrome_rect(
            {"x": 772, "y": 62, "w": 40, "h": 40}))

    def test_card_rect_is_not_chrome(self):
        self.assertFalse(R._worldle_area_is_chrome_rect(
            {"x": 500, "y": 300, "w": 300, "h": 400}))

    def test_wide_top_banner_is_not_chrome(self):
        # Top band alone is not enough: a wide banner is not a
        # ~40x40 chrome button.
        self.assertFalse(R._worldle_area_is_chrome_rect(
            {"x": 0, "y": 0, "w": 1200, "h": 90}))

    def test_small_button_below_band_is_not_chrome(self):
        # A small control below the header band could be a real
        # option (e.g. a stepper); only top-band small = chrome.
        self.assertFalse(R._worldle_area_is_chrome_rect(
            {"x": 600, "y": 500, "w": 40, "h": 40}))

    def test_missing_or_degenerate_rect_is_not_chrome(self):
        self.assertFalse(R._worldle_area_is_chrome_rect(None))
        self.assertFalse(R._worldle_area_is_chrome_rect({}))
        self.assertFalse(R._worldle_area_is_chrome_rect(
            {"x": 772, "y": 62}))
        self.assertFalse(R._worldle_area_is_chrome_rect(
            {"x": 772, "y": 62, "w": 0, "h": 0}))
        # width/height key spellings work too.
        self.assertTrue(R._worldle_area_is_chrome_rect(
            {"x": 772, "y": 62, "width": 40, "height": 40}))


class TestPickDropsChromeIter25(unittest.TestCase):
    def test_chrome_candidate_never_shadows_the_real_card(self):
        # The chrome "Kuwait" is listed FIRST (document order in
        # iter-24). If it is not dropped before the name dedup,
        # it shadows the real Kuwait card; dropping it leaves
        # only Qatar distinct, so the pick must refuse.
        best, detail = R._s_worldle_comparison_pick(
            [_CHROME_BUTTON, _CARD_QATAR],
            {"kuwait": 17818, "qatar": 11000})
        self.assertIsNone(best)
        self.assertIn("need >=2", detail)

    def test_cards_pick_the_max(self):
        best, _ = R._s_worldle_comparison_pick(
            [_CARD_KUWAIT, _CARD_QATAR],
            {"kuwait": 17818, "qatar": 11000})
        self.assertEqual(best, "Kuwait")

    def test_chrome_card_with_same_name_as_card_is_ignored(self):
        # Chrome + both real cards: the chrome entry must not
        # change the outcome.
        best, _ = R._s_worldle_comparison_pick(
            [_CHROME_BUTTON, _CARD_KUWAIT, _CARD_QATAR],
            {"kuwait": 17818, "qatar": 11000})
        self.assertEqual(best, "Kuwait")


# ------------------------------------------- JS source guards

class TestComparisonJsGuardsIter25(unittest.TestCase):
    def test_chrome_guard_in_every_comparison_js_path(self):
        for fn in (R._s_worldle_comparison_probe,
                   R._s_worldle_click_comparison_option,
                   R._s_worldle_click_comparison_card):
            src = inspect.getsource(fn)
            self.assertIn("isChromeCandidate", src)
        # Loose + selected-state carry the same rule inline.
        for fn in (R._s_worldle_click_option_loose,
                   R._s_worldle_comparison_selected_state):
            src = inspect.getsource(fn)
            self.assertIn("r.y < 160 && r.width <= 56", src)

    def test_no_ancestor_climb_in_probe_or_click(self):
        # The ancestor-climbing sibling-label scan is gone: it
        # is what let a header button "match" every country.
        for fn in (R._s_worldle_comparison_probe,
                   R._s_worldle_click_comparison_option):
            self.assertNotIn("for (let depth", inspect.getsource(fn))

    def test_descendant_label_in_card_paths(self):
        # The Area label bar lives INSIDE the card div, so the
        # card matcher / selected-state namer must read
        # descendant labels.
        for fn in (R._s_worldle_comparison_probe,
                   R._s_worldle_click_comparison_option,
                   R._s_worldle_click_comparison_card,
                   R._s_worldle_comparison_selected_state):
            self.assertIn("descLabel", inspect.getsource(fn))

    def test_visible_container_counts_images(self):
        # The container probe must count silhouette images as
        # option-like descendants, or the Area widget (no
        # buttons) never yields a bounding container.
        src = inspect.getsource(R._s_worldle_visible_area_ui)
        self.assertIn("[data-value], img", src)


# ------------------------------------------- fake-page harness

class _FakeCompPage:
    """Fake for _s_worldle_play_comparison (Area / GDP)."""

    def __init__(self, selected="selected", flip="never",
                 card_ok=True, button_ok=True, probe_options=None):
        self.selected = selected  # selected | none | empty
        self.flip = flip  # never | probe | store
        self.card_ok = card_ok
        self.button_ok = button_ok
        self.probe_options = (probe_options if probe_options is not None
                              else [dict(_CARD_KUWAIT),
                                    dict(_CARD_QATAR)])
        self.calls = []
        self.clicked = False
        self.visible_calls = 0

    def evaluate(self, js, arg=None):
        if "worldle-visible-area" in js:
            self.visible_calls += 1
            visible = True
            if self.flip == "probe" and self.visible_calls >= 2:
                visible = False
            return {"visible": visible, "signals": ["area-prompt-visible"],
                    "prompt_visible": visible, "selected_tab": "area",
                    "prompt_text": "which country has a larger surface area?",
                    "next_round_visible": False, "probe_ran": True,
                    "container": {"tag": "DIV", "cls": "widget",
                                  "rect": {"x": 400, "y": 150,
                                           "w": 800, "h": 600}},
                    "reason": "" if visible else "no truly-visible Area prompt",
                    "rejected": 0, "rejection_reasons": {}}
        if "worldle-comparison-card-click" in js:
            self.calls.append("card-click")
            if self.card_ok:
                self.clicked = True
                return [True, "Kuwait", "card", "DIV", "card-cls",
                        {"x": 500, "y": 300, "w": 300, "h": 400},
                        650, 500]
            return [False, "no card inside verified Area container",
                    "card", "", "", None, None, None]
        if "worldle-comparison-click" in js:
            self.calls.append("button-click")
            if self.button_ok:
                self.clicked = True
                return [True, "Kuwait", "button", "BUTTON", "btn-cls",
                        {"x": 500, "y": 300, "w": 300, "h": 400},
                        650, 500]
            return [False, "not found", "button", "", "", None, None, None]
        if "worldle-comparison-selected-state" in js:
            self.calls.append("selected-state")
            if self.selected == "empty":
                return {}
            any_sel = self.selected == "selected"
            return {"any_selected": any_sel,
                    "selected_name": "Kuwait" if any_sel else "",
                    "selected_count": 1 if any_sel else 0,
                    "selected": [{"name": "Kuwait", "tag": "DIV",
                                  "cls": "selected",
                                  "signals": ["class:selected"]}]
                    if any_sel else [],
                    "options": [
                        {"name": "Kuwait", "tag": "DIV", "cls": "",
                         "selected": any_sel,
                         "signals": ["class:selected"] if any_sel else []},
                        {"name": "Qatar", "tag": "DIV", "cls": "",
                         "selected": False, "signals": []}],
                    "wanted": "Kuwait", "wanted_selected": any_sel}
        if "worldle-comparison-probe" in js:
            return {"prompt_found": True,
                    "options": [dict(o) for o in self.probe_options]}
        if "role=menuitem" in js:
            self.calls.append("loose-click")
            return [False, "not found"]
        if "localStorage.getItem" in js:
            if arg == "game_round":
                if self.flip == "store" and self.clicked:
                    return {"gameMode": "populationCurrency"}
                return {"gameMode": "area"}
            if arg == "size-guesses":
                return {"2026-10-18": []}
            return {}
        if "aria-selected" in js:
            return "2. AREA"
        if "document.body.innerText" in js:
            return "Which country has a larger surface area? Kuwait Qatar"
        return True

    def wait_for_timeout(self, ms):
        return None


def _run_comparison(page, mode="area", prompt_substrs=("larger surface area",),
                    lookup=None, stat="size"):
    logs = []
    submits = []
    orig_dbg = R._dbg
    orig_submit = R._s_worldle_submit_guarded
    orig_settle = R._settle
    R._dbg = _dbg_recorder(logs)
    R._s_worldle_submit_guarded = lambda *a, **k: submits.append(1) or "submitted"
    R._settle = lambda page, base=0: None
    try:
        got = R._s_worldle_play_comparison(
            page, "worldle", {}, mode, "2026-10-18", prompt_substrs,
            lookup if lookup is not None else {"kuwait": 17818,
                                               "qatar": 11000},
            stat, mode, verify_store=False)
    finally:
        R._dbg = orig_dbg
        R._s_worldle_submit_guarded = orig_submit
        R._settle = orig_settle
    return got, logs, submits


# ------------------------------------------- (a) card preferred over header button

class TestAreaCardFirstIter25(unittest.TestCase):
    def test_card_attempted_first_and_button_never_called(self):
        page = _FakeCompPage(selected="selected")
        (st, pick, det), logs, submits = _run_comparison(page)
        self.assertEqual(st, "attempted")
        self.assertEqual(pick, "Kuwait")
        self.assertEqual(page.calls.count("card-click"), 1)
        self.assertNotIn("button-click", page.calls)
        self.assertEqual(len(submits), 1)

    def test_card_fails_then_button_fallback(self):
        page = _FakeCompPage(selected="selected", card_ok=False)
        (st, pick, det), logs, submits = _run_comparison(page)
        self.assertEqual(st, "attempted")
        self.assertEqual(pick, "Kuwait")
        self.assertEqual(page.calls.count("card-click"), 1)
        self.assertEqual(page.calls.count("button-click"), 1)
        self.assertEqual(len(submits), 1)


# ------------------------------------------- (c) view-flip is pick-failed

class TestAreaViewFlipIter25(unittest.TestCase):
    def test_flip_via_visible_probe_is_pick_failed_no_submit(self):
        page = _FakeCompPage(selected="selected", flip="probe")
        (st, pick, det), logs, submits = _run_comparison(page)
        self.assertEqual(st, "partial")
        self.assertIsNone(pick)
        self.assertIn("pick-failed", det)
        self.assertEqual(submits, [])

    def test_flip_via_store_is_pick_failed_no_submit(self):
        # Visible probe still says Area, but game_round flipped
        # to PopulationCurrency after the click (the iter-24
        # shape) — the store/header evidence alone must block.
        page = _FakeCompPage(selected="selected", flip="store")
        (st, pick, det), logs, submits = _run_comparison(page)
        self.assertEqual(st, "partial")
        self.assertIsNone(pick)
        self.assertIn("pick-failed", det)
        self.assertEqual(submits, [])


# ------------------------------------------- (d) selected state gates submit

class TestAreaSelectedGateIter25(unittest.TestCase):
    def test_no_selection_after_card_and_button_gates_submit(self):
        page = _FakeCompPage(selected="none")
        (st, pick, det), logs, submits = _run_comparison(page)
        self.assertEqual(st, "partial")
        self.assertIsNone(pick)
        self.assertIn("no selected state", det)
        self.assertIn("NOT submitted", det)
        self.assertEqual(submits, [])
        # Both attempts were tried before gating.
        self.assertIn("card-click", page.calls)
        self.assertIn("button-click", page.calls)

    def test_empty_selected_probe_falls_back_to_store_arbitration(self):
        # The probe itself read nothing (options empty) — the old
        # submit-anyway + store-verify path still applies, so a
        # probe that never ran cannot newly fail Area.
        page = _FakeCompPage(selected="empty")
        (st, pick, det), logs, submits = _run_comparison(page)
        self.assertEqual(st, "attempted")
        self.assertEqual(pick, "Kuwait")
        self.assertEqual(len(submits), 1)

    def test_gdp_still_button_first_and_submits_without_selection(self):
        # The Area gate must not leak into GDP: GDP keeps
        # buttons-first and submits with store-verify
        # arbitrating, as proven in iter-24.
        page = _FakeCompPage(selected="none")
        (st, pick, det), logs, submits = _run_comparison(
            page, mode="gdp",
            prompt_substrs=("highest", "per capita gdp"),
            lookup={"kuwait": 80000, "qatar": 71000},
            stat="gdpPerCapita")
        self.assertEqual(st, "attempted")
        self.assertEqual(pick, "Kuwait")
        # Button-first for GDP: the button click comes before any
        # card retry (the card retry after a no-selection button
        # click is pre-existing iter-20 behaviour, unchanged).
        self.assertEqual(page.calls[0], "button-click")
        self.assertEqual(len(submits), 1)


if __name__ == "__main__":
    unittest.main()
