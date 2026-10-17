"""Fix-loop iteration 12 regression tests — Worldle slice.

Covers: G4 live-round scoping (open-dropdown-only, emoji-strip,
expanded chrome, BLOCK only on in-scope unmapped), deliberate
Area/GDP comparison branch (probe / pick / CDN resolve / click,
never click unresolved), trivia token-overlap + country-name strip
+ boolean-choice trigger, TOTAL=13 mode set unchanged.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

import runner as R


class _FakePage:
    """Minimal Playwright-page stand-in: evaluate returns scripted data."""

    def __init__(self, scripted=None, body=""):
        self._scripted = scripted or {}
        self._body = body
        self._calls = []

    def evaluate(self, script, arg=None):
        self._calls.append(script)
        for key, val in self._scripted.items():
            if key in script:
                return val() if callable(val) else val
        if "document.body.innerText" in script:
            return self._body
        return None

    def wait_for_timeout(self, ms):
        pass


class TestEmojiStrip(unittest.TestCase):
    def test_prefix(self):
        self.assertEqual(R._s_worldle_strip_emoji("📍 Capital"), "Capital")
        self.assertEqual(R._s_worldle_strip_emoji("🌍 Country"), "Country")

    def test_suffix(self):
        self.assertEqual(R._s_worldle_strip_emoji("Country ✅"), "Country")

    def test_plain_unchanged(self):
        self.assertEqual(R._s_worldle_strip_emoji("GDP Per Capita"),
                         "GDP Per Capita")
        self.assertEqual(R._s_worldle_strip_emoji("Population & Currency"),
                         "Population & Currency")

    def test_label_key_matches(self):
        self.assertEqual(R._s_worldle_label_key("📍 Capital"),
                         R._s_worldle_label_key("Capital"))
        self.assertEqual(R._s_worldle_label_key("🌍 Country"), "country")


class TestDropdownChrome(unittest.TestCase):
    def test_expanded_chrome(self):
        for raw in ("Give up", "Next round", "Remove ads", "Share",
                    "Guess", "Submit", "Try again", "How to play"):
            self.assertIn(R._s_worldle_label_key(raw),
                          R._WORLDLE_DROPDOWN_CHROME,
                          f"{raw!r} must be chrome, never unmapped")


class TestLiveRoundsScoping(unittest.TestCase):
    """G4: dropdown-scope only, emoji-strip, raw logged, scope flag."""

    def test_emoji_entries_map_and_chrome_filtered(self):
        raw = ["🌍 Country", "📍 Capital", "Mystery Round", "Give up"]
        page = _FakePage({"labels": True, "const out": raw})
        got = R._s_worldle_live_round_modes(page, "worldle")
        self.assertIsNotNone(got)
        self.assertEqual(got["scope"], "dropdown")
        self.assertIn("country", got["mapped"])
        self.assertIn("capital", got["mapped"])
        self.assertEqual(got["raw"], raw, "raw list survives verbatim")
        self.assertEqual(got["unmapped"], ["Mystery Round"])
        self.assertNotIn("Give up", got["unmapped"])

    def test_no_container_is_unreadable_not_blocked(self):
        page = _FakePage({"labels": True,
                          "const out": {"entries": [], "container": False}})
        self.assertIsNone(R._s_worldle_live_round_modes(page, "worldle"),
                          "no open dropdown container => None, never a "
                          "whole-page scan that could BLOCK on chrome")

    def test_dropdown_unreadable(self):
        page = _FakePage({"labels": False})
        self.assertIsNone(R._s_worldle_live_round_modes(page, "worldle"))


class TestComparisonPick(unittest.TestCase):
    """Pure pick: max wins, tie keeps first shown, unresolved refuses."""

    def test_max_wins_kuwait_vs_qatar(self):
        opts = [{"name": "Kuwait"}, {"name": "Qatar"}]
        vals = {"kuwait": 17818, "qatar": 11610}
        best, detail = R._s_worldle_comparison_pick(opts, vals)
        self.assertEqual(best, "Kuwait")
        self.assertIn("17818", detail)

    def test_tie_keeps_first_shown(self):
        opts = [{"name": "Aland"}, {"name": "Bland"}]
        best, detail = R._s_worldle_comparison_pick(
            opts, {"aland": 100, "bland": 100})
        self.assertEqual(best, "Aland")
        self.assertIn("tie", detail)

    def test_unresolved_refuses(self):
        opts = [{"name": "Kuwait"}, {"name": "Mysteryland"}]
        best, detail = R._s_worldle_comparison_pick(opts, {"kuwait": 17818})
        self.assertIsNone(best)
        self.assertIn("Mysteryland", detail)

    def test_too_few_options(self):
        best, _ = R._s_worldle_comparison_pick([{"name": "Kuwait"}], {})
        self.assertIsNone(best)


class TestComparisonBranch(unittest.TestCase):
    """Deliberate branch end-to-end on a FakePage: probe -> pick ->
    click -> submit; unresolvable -> partial with NO click."""

    def _page(self, probe_opts, click_ok=True):
        scripted = {
            "worldle-comparison-probe": {
                "prompt_found": True, "options": probe_opts},
            "worldle-comparison-click": [click_ok, "Kuwait"],
            "localStorage": None,
        }
        return _FakePage(scripted)

    def test_area_clicks_larger(self):
        opts = [{"name": "Kuwait", "has_img": True},
                {"name": "Qatar", "has_img": True}]
        page = self._page(opts)
        day = {"date": "2026-10-18",
               "size_lookup": {"kuwait": 17818, "qatar": 11610}}
        st, pick, det = R._s_worldle_play_comparison(
            page, "worldle", day, "area", "2026-10-18",
            ("larger surface area",), day["size_lookup"], "size", "area")
        self.assertEqual(st, "attempted")
        self.assertEqual(pick, "Kuwait")
        self.assertTrue(any("worldle-comparison-click" in c
                            for c in page._calls))

    def test_gdp_image_card_option_found(self):
        # The iter-runs failure: 'Saudi Arabia' shown as an image
        # card, invisible to the old button-innerText scan. The probe
        # reports it by name; the branch clicks the max-GDP option.
        opts = [{"name": "Kuwait", "has_img": True},
                {"name": "Iraq", "has_img": True},
                {"name": "Saudi Arabia", "has_img": True}]
        page = self._page(opts)
        day = {"date": "2026-10-18",
               "gdp_lookup": {"kuwait": 29950, "iraq": 5000,
                              "saudi arabia": 32000}}
        st, pick, det = R._s_worldle_play_comparison(
            page, "worldle", day, "gdp", "2026-10-18",
            ("highest", "per capita gdp"), day["gdp_lookup"],
            "gdpPerCapita", "gdp")
        self.assertEqual(st, "attempted")
        self.assertEqual(pick, "Saudi Arabia")

    def test_unresolvable_never_clicks(self):
        opts = [{"name": "Kuwait"}, {"name": "Mysteryland"}]
        page = self._page(opts)
        day = {"date": "2026-10-18", "size_lookup": {"kuwait": 17818}}
        # CDN fetch returns nothing for Mysteryland.
        page._scripted["worldle-country-stat-fetch"] = []
        st, pick, det = R._s_worldle_play_comparison(
            page, "worldle", day, "area", "2026-10-18",
            ("larger surface area",), day["size_lookup"], "size", "area")
        self.assertEqual(st, "partial")
        self.assertIsNone(pick)
        self.assertFalse(any("worldle-comparison-click" in c
                             for c in page._calls),
                         "unresolved option must never be clicked")

    def test_prompt_absent_is_not_present(self):
        page = _FakePage({"worldle-comparison-probe":
                          {"prompt_found": False, "options": []}})
        st, pick, det = R._s_worldle_play_comparison(
            page, "worldle", {}, "area", "2026-10-18",
            ("larger surface area",), {}, "size", "area")
        self.assertEqual(st, "not-present")
        self.assertIsNone(pick)


class TestTriviaMatchIter12(unittest.TestCase):
    def test_token_overlap_paraphrase(self):
        db = ("The country has a coastline on the Red Sea and a desert "
              "climate with hot summers")
        shown = ("Desert climate with hot summers and a coastline on "
                 "the Red Sea")
        self.assertTrue(R._s_worldle_trivia_match(db, shown))

    def test_unrelated_still_false(self):
        self.assertFalse(R._s_worldle_trivia_match(
            "The country has a coastline on the Red Sea",
            "The capital city is located in the northern region"))

    def test_country_name_stripped(self):
        db = "Which fact is true?"
        shown = "Which fact about Kuwait is true?"
        self.assertFalse(R._s_worldle_trivia_match(db, shown),
                         "without stripping, the inserted name breaks it")
        self.assertTrue(R._s_worldle_trivia_match(
            db, shown, extra_stop={"kuwait"}))

    def test_exact_still_true(self):
        self.assertTrue(R._s_worldle_trivia_match("Hello world",
                                                   "Hello world"))


class TestWorldleTotal(unittest.TestCase):
    """TOTAL = 13 reconciled: 11 offered + 2 removed
    (populationCurrency, populousCities). The mode set is unchanged."""

    def test_play_order_is_13(self):
        self.assertEqual(len(R._WORLDLE_PLAY_ORDER), 13)
        self.assertIn("area", R._WORLDLE_PLAY_ORDER)
        self.assertIn("gdp", R._WORLDLE_PLAY_ORDER)
        self.assertIn("populationCurrency", R._WORLDLE_PLAY_ORDER)
        self.assertIn("populousCities", R._WORLDLE_PLAY_ORDER)

    def test_mode_labels_cover_play_order(self):
        for mode in R._WORLDLE_PLAY_ORDER:
            self.assertIn(mode, R._WORLDLE_MODE_LABELS)


if __name__ == "__main__":
    unittest.main()
