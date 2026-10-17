"""Fix-loop iteration 15 tests (evidence-grounded in ANALYSIS11).

1. Semantle re-key bound (P0): the iter-14 re-key had no plausibility
   bound and no provenance — a racy probe reading re-keyed Oct-18 ->
   Oct-14 (cry -> suit) and played suit on the Oct-18 board (site
   header: Game #1722). Now: re-key bounded to ±1 day, host+URL
   logged on every decision, displayed puzzle number cross-checked.
2. Nerdle mode-specific header capture (P0): hash-element took the
   FIRST #<digits> in DOM order — mini/midi/speed all returned 1733
   (classic's number from shared chrome). Now only the mode's OWN
   header logo line counts.
3. Nerdle classic-day-key (P0): init-script ordering — prepurge
   registered BEFORE the useLocalTime setter so the wipe cannot eat
   the bot's own setting (classic is modes[0], first-document wipe
   victim). Source-checked here; live proof is the SERVED
   useLocalTime log line in the next artifact.
4. Nerdle maxi ² entry (P1): power-key press verified in-cell, with
   keyboard.type / keyboard.press fallbacks. Plus full solver stdout
   forwarded to the debug log (source-checked).
5. Worldle Capital active-marker (P1): dark-background + panel-switch
   evidence accepted; dumps carry FULL class strings.
6. Worldle Language pairing (P1): pair by slot LABEL + slot-box
   geometry, never raw broad-list indices; DragEvent point-based
   (reachable for broad slots); click-verify helper exists and the
   caller reaches it on click-fallback.
7. Worldle Area store-verify (P1): comparison "answered" only when
   the store list changed; visible round re-verified after the
   comparison submit; GetAdmiral ad div excluded from steppers.
8. Betweenle post-win stop (P3): stats_word == answer stops the
   loop without Flutter semantics.
9. Teuteuf Edu banner dismissal (P3): helper exists, wired into
   _close_modals.

All tests are synthetic/local: no network, no browser, no CI run.
"""
import asyncio
import contextlib
import io
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import runner as R
import nerdle_solver as NS

SRC_RUNNER = (Path(__file__).parent.parent / "runner.py").read_text(
    encoding="utf-8")
SRC_NERDLE = (Path(__file__).parent.parent / "nerdle_solver.py").read_text(
    encoding="utf-8")


class _FakeAnswers:
    """Stub for runner.A: target_date + per-game engine."""

    def __init__(self, target):
        self._target = target

    def target_date(self):
        return self._target

    def semantle(self, d):
        idx = (d - date(2022, 1, 30)).days
        return {"answer": f"word-{idx}", "puzzle": idx, "date": str(d)}


class TestSemantleRekeyBound(unittest.TestCase):
    TARGET = date(2026, 10, 18)

    def setUp(self):
        self._orig_a = R.A
        self._orig_live = dict(R._LIVE_DAY)
        R.A = _FakeAnswers(self.TARGET)

    def tearDown(self):
        R.A = self._orig_a
        R._LIVE_DAY.clear()
        R._LIVE_DAY.update(self._orig_live)

    def _set_probe(self, iso, host="semantle.io",
                   url="https://semantle.io/api/word/2026-10-14"):
        R._LIVE_DAY.clear()
        R._LIVE_DAY["hosts"] = {host: iso}
        R._LIVE_DAY["urls"] = {host: url}

    def test_provenance_returns_host_and_url(self):
        self._set_probe("2026-10-14", "semantle.io",
                        "https://semantle.io/api/word/2026-10-14")
        iso, host, url = R._live_day_provenance("semantle")
        self.assertEqual((iso, host, url),
                         ("2026-10-14", "semantle.io",
                          "https://semantle.io/api/word/2026-10-14"))

    def test_four_day_jump_refused(self):
        # Iter-14 REGRESSION: Oct-18 -> Oct-14 re-keyed and played
        # suit on the Oct-18 board. Now refused: keep target.
        self._set_probe("2026-10-14")
        ans = {"answer": "cry", "puzzle": 1722, "date": "2026-10-18"}
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            out, served = R._align_answer_to_live_day("semantle", ans)
        self.assertIs(out, ans)
        self.assertIsNone(served)
        log = buf.getvalue()
        self.assertIn("RE-KEY REFUSED", log)
        self.assertIn("semantle.io", log)
        self.assertIn("https://semantle.io/api/word/2026-10-14", log)

    def test_plus_one_day_allowed(self):
        self._set_probe("2026-10-19", url="https://semantle.io/api/x")
        ans = {"answer": "cry", "puzzle": 1722, "date": "2026-10-18"}
        out, served = R._align_answer_to_live_day("semantle", ans)
        self.assertEqual(out["answer"], "word-1723")
        self.assertEqual(str(served), "2026-10-19")

    def test_minus_one_day_allowed_preserves_defer(self):
        # Served == target-1 still re-keys so the caller can defer
        # (site has not rolled yet) — the bound must not break that.
        self._set_probe("2026-10-17", url="https://semantle.io/api/x")
        ans = {"answer": "cry", "puzzle": 1722, "date": "2026-10-18"}
        out, served = R._align_answer_to_live_day("semantle", ans)
        self.assertEqual(out["answer"], "word-1721")
        self.assertEqual(str(served), "2026-10-17")

    def test_displayed_number_contradiction_refuses(self):
        # Site displays Game #1722 (target-day puzzle) but the probe
        # claims 2026-10-19: the reading is false, keep target.
        self._set_probe("2026-10-19", url="https://semantle.io/api/x")

        class _Page:
            def evaluate(self, js, *a):
                return "Semantle\nGame #1722\nSimilarity table"

        ans = {"answer": "cry", "puzzle": 1722, "date": "2026-10-18"}
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            out, served = R._align_answer_to_live_day(
                "semantle", ans, _Page())
        self.assertIs(out, ans)
        self.assertIsNone(served)
        self.assertIn("Game #1722", buf.getvalue())

    def test_displayed_number_consistent_allows(self):
        self._set_probe("2026-10-19", url="https://semantle.io/api/x")

        class _Page:
            def evaluate(self, js, *a):
                return "Semantle\nGame #1723\n"

        ans = {"answer": "cry", "puzzle": 1722, "date": "2026-10-18"}
        out, served = R._align_answer_to_live_day(
            "semantle", ans, _Page())
        self.assertEqual(out["answer"], "word-1723")
        self.assertEqual(str(served), "2026-10-19")

    def test_displayed_number_none_when_absent(self):
        class _Page:
            def evaluate(self, js, *a):
                return "Semantle\nno number here\n"

        self.assertIsNone(R._live_day_displayed_puzzle_number(_Page()))


class TestNerdleOwnHeader(unittest.TestCase):
    def test_mini_own_header_wins_over_classic_chrome(self):
        # Iter-14: mini captured 1733 (classic's number) from shared
        # chrome. The probe now carries both; mini's own header wins.
        probe = {"header_text": "nerdle. #1733",
                 "own_headers": [
                     {"tag": "DIV", "cls": "shared chrome",
                      "text": "nerdle. #1733"},
                     {"tag": "H1", "cls": "logo mini",
                      "text": "mini nerdle. #497"}],
                 "hash_texts": ["nerdle. #1733"], "data_attrs": [],
                 "url": "", "body_text": ""}
        n, strat, raw = NS._nerdle_header_number_from_probe(
            probe, mode_id="mini")
        self.assertEqual((n, strat), (497, "own-header"))
        self.assertIn("H1", raw)
        self.assertIn("logo mini", raw)
        self.assertIn("mini nerdle. #497", raw)

    def test_mini_rejects_classic_only_header(self):
        probe = {"header_text": "nerdle. #1733",
                 "own_headers": [
                     {"tag": "DIV", "cls": "shared chrome",
                      "text": "nerdle. #1733"}],
                 "hash_texts": ["nerdle. #1733"], "data_attrs": [],
                 "url": "", "body_text": ""}
        n, strat, _ = NS._nerdle_header_number_from_probe(
            probe, mode_id="mini")
        self.assertEqual((n, strat), (None, "own-header-not-found"))

    def test_classic_bare_header_is_its_own(self):
        probe = {"header_text": "nerdle. #1732",
                 "own_headers": [
                     {"tag": "H1", "cls": "logo",
                      "text": "nerdle. #1732"}],
                 "hash_texts": [], "data_attrs": [], "url": "",
                 "body_text": ""}
        n, strat, _ = NS._nerdle_header_number_from_probe(
            probe, mode_id="classic")
        self.assertEqual((n, strat), (1732, "own-header"))

    def test_maxi_own_header(self):
        probe = {"header_text": "maxi nerdle. #1368",
                 "own_headers": [
                     {"tag": "H1", "cls": "logo",
                      "text": "maxi nerdle. #1368"}],
                 "hash_texts": [], "data_attrs": [], "url": "",
                 "body_text": ""}
        n, strat, _ = NS._nerdle_header_number_from_probe(
            probe, mode_id="maxi")
        self.assertEqual((n, strat), (1368, "own-header"))

    def test_legacy_no_mode_keeps_iter14_strategies(self):
        # Callers without a mode keep the iter-14 fallback chain
        # (existing iter-14 tests cover these paths).
        n, strat, _ = NS._nerdle_header_number_from_probe(
            {"header_text": "Nerdle", "hash_texts": ["Puzzle #1733"],
             "data_attrs": [], "url": "", "body_text": ""})
        self.assertEqual((n, strat), (1733, "hash-element"))

    def test_day_key_init_order_and_logging(self):
        # prepurge must be registered BEFORE the useLocalTime setter
        # in nerdle_solver.main (see the iter-15 finding comment):
        # otherwise the first-document wipe eats the bot's own
        # setting and classic serves the UTC-day (yesterday) board.
        pre = SRC_NERDLE.index("prepurge_js()")
        use = SRC_NERDLE.index("useLocalTime','true'")
        self.assertLess(pre, use)
        self.assertIn("useLocalTime", SRC_NERDLE)
        # SERVED evidence logs the live useLocalTime value.
        self.assertIn('useLocalTime', SRC_NERDLE)
        self.assertIn("mode_id=mode[\"id\"]", SRC_NERDLE)


class _FakeNerdlePage:
    """Async fake for type_equation: grid ²-count + power-key click."""

    def __init__(self, click_works=True, type_works=True,
                 press_works=True):
        self.count = 0
        self.click_works = click_works
        self.type_works = type_works
        self.press_works = press_works
        self.typed = []
        self.keyboard = self._Keyboard(self)

    class _Keyboard:
        def __init__(self, page):
            self._page = page

        async def type(self, text, delay=0):
            self._page.typed.append(text)
            if self._page.type_works and text in ("²", "³"):
                self._page.count += 1

        async def press(self, key):
            if self._page.press_works and key in ("²", "³"):
                self._page.count += 1

    async def evaluate(self, js, arg=None):
        if "div.pb-grid" in js:
            return self.count
        if "window.__QP_HOLD" in js:
            return None
        # CLICK_POWER_JS: button found; the cell appears only when
        # click_works (iter-14: click "succeeded" but no cell).
        if self.click_works:
            self.count += 1
        return True

    async def wait_for_timeout(self, ms):
        return None


class TestNerdleSuperscriptEntry(unittest.TestCase):
    def test_power_click_verified_in_cell(self):
        page = _FakeNerdlePage(click_works=True)
        asyncio.run(NS.type_equation(page, "1²+67+8=76"))
        self.assertEqual(page.count, 1)

    def test_type_fallback_when_click_produces_no_cell(self):
        # Iter-14 maxi stall: the click produced no cell. The
        # keyboard.type fallback must place the ².
        page = _FakeNerdlePage(click_works=False, type_works=True)
        asyncio.run(NS.type_equation(page, "1²+67+8=76"))
        self.assertEqual(page.count, 1)

    def test_press_fallback_when_type_also_fails(self):
        page = _FakeNerdlePage(click_works=False, type_works=False,
                                press_works=True)
        asyncio.run(NS.type_equation(page, "1²+67+8=76"))
        self.assertEqual(page.count, 1)

    def test_all_methods_failing_does_not_raise(self):
        page = _FakeNerdlePage(click_works=False, type_works=False,
                                press_works=False)
        asyncio.run(NS.type_equation(page, "1²+67+8=76"))
        self.assertEqual(page.count, 0)

    def test_full_stdout_forwarded_to_debug_log(self):
        self.assertIn("solver subprocess stdout (full", SRC_RUNNER)
        self.assertIn("debug_{date_key}.log", SRC_RUNNER)


class TestWorldleCapitalActive(unittest.TestCase):
    def test_aria_selected(self):
        self.assertTrue(R._worldle_capital_tab_active(
            {"aria_selected": True}))

    def test_class_active(self):
        self.assertTrue(R._worldle_capital_tab_active(
            {"cls": "tab tab-active"}))

    def test_panel_switch_evidence(self):
        # Frame g_120: dark tab + PINPOINT prompt + map, no
        # aria/class/data-state marker — the iter-14 checker said
        # False. Panel-switch evidence alone suffices.
        self.assertTrue(R._worldle_capital_tab_active(
            {"aria_selected": False, "cls": "tab xyz",
             "data_state": "", "prompt_visible": True,
             "map_visible": True}))

    def test_dark_background_marker(self):
        self.assertTrue(R._worldle_capital_tab_active(
            {"aria_selected": False, "cls": "tab xyz",
             "data_state": "", "bg": "rgb(30, 30, 30)",
             "sibling_bg": "rgb(240, 240, 240)",
             "prompt_visible": False, "map_visible": False}))

    def test_globally_dark_theme_not_active(self):
        # Both tabs dark: no relative marker, do NOT claim active.
        self.assertFalse(R._worldle_capital_tab_active(
            {"aria_selected": False, "cls": "tab xyz",
             "data_state": "", "bg": "rgb(30, 30, 30)",
             "sibling_bg": "rgb(35, 35, 35)",
             "prompt_visible": False, "map_visible": False}))

    def test_inactive_light_tab(self):
        self.assertFalse(R._worldle_capital_tab_active(
            {"aria_selected": False, "cls": "tab xyz",
             "data_state": "", "bg": "rgb(240, 240, 240)",
             "sibling_bg": "rgb(30, 30, 30)",
             "prompt_visible": False, "map_visible": False}))

    def test_luminance(self):
        self.assertEqual(R._worldle_color_luminance("rgb(0, 0, 0)"), 0.0)
        self.assertAlmostEqual(
            R._worldle_color_luminance("rgb(255, 255, 255)"), 1.0)
        self.assertIsNone(R._worldle_color_luminance("transparent"))
        self.assertIsNone(R._worldle_color_luminance(""))

    def test_dump_uses_full_class_strings(self):
        self.assertNotIn("slice(0, 80)", SRC_RUNNER[
            SRC_RUNNER.index("CAPITAL_TAB_DUMP"):
            SRC_RUNNER.index("CAPITAL_TAB_DUMP") + 1200])


class TestWorldleLanguagePairing(unittest.TestCase):
    def test_iter14_fixture_pairs_by_label(self):
        # Iter-14 broad list: header-row elements at y≈82 plus the
        # two true labeled slots at y≈303 (frame g_255). Raw
        # indices 1, 3 were header elements; label pairing picks
        # the y≈303 boxes.
        slots = [
            {"label": "", "x": 400.0, "y": 82.0, "text": "", "kind": "x"},
            {"label": "", "x": 900.0, "y": 82.0, "text": "", "kind": "x"},
            {"label": "Arabic", "x": 850.0, "y": 303.3,
             "text": "", "kind": "bordered-box"},
            {"label": "English", "x": 1070.0, "y": 303.3,
             "text": "", "kind": "bordered-box"},
        ]
        pairs, how = R._worldle_phrase_pair_slots(
            slots, ["ara", "eng"], ["Arabic", "English"])
        self.assertEqual(how, "label")
        self.assertEqual(
            [(c, s["x"], s["y"]) for c, s in pairs],
            [("ara", 850.0, 303.3), ("eng", 1070.0, 303.3)])

    def test_label_in_text_field_counts(self):
        slots = [
            {"label": "", "x": 850.0, "y": 303.3, "text": "Arabic"},
            {"label": "", "x": 1070.0, "y": 303.3, "text": "English"},
        ]
        pairs, how = R._worldle_phrase_pair_slots(
            slots, ["ara", "eng"], ["Arabic", "English"])
        self.assertEqual(how, "label")
        self.assertEqual([c for c, _ in pairs], ["ara", "eng"])

    def test_unlabelled_geometry_fallback_picks_slot_row(self):
        slots = [
            {"label": "", "x": 100.0, "y": 82.0, "text": ""},
            {"label": "", "x": 200.0, "y": 82.0, "text": ""},
            {"label": "", "x": 850.0, "y": 303.3, "text": ""},
            {"label": "", "x": 1070.0, "y": 303.3, "text": ""},
        ]
        pairs, how = R._worldle_phrase_pair_slots(
            slots, ["ara", "eng"], ["Arabic", "English"])
        self.assertEqual(how, "geometry-x")
        self.assertEqual(
            [(c, s["x"]) for c, s in pairs],
            [("ara", 850.0), ("eng", 1070.0)])

    def test_no_exact_row_refuses(self):
        slots = [
            {"label": "", "x": 100.0, "y": 82.0, "text": ""},
            {"label": "", "x": 200.0, "y": 82.0, "text": ""},
            {"label": "", "x": 300.0, "y": 82.0, "text": ""},
        ]
        pairs, _reason = R._worldle_phrase_pair_slots(
            slots, ["ara", "eng"], ["Arabic", "English"])
        self.assertIsNone(pairs)

    def test_dragevent_reachable_and_click_verify_wired(self):
        # DragEvent must be point-based (elementFromPoint), not
        # narrow-index — the iter-14 narrow version was skipped for
        # broad slots. The caller must reach click-verify on
        # click-fallback (iter-14: zero log lines).
        self.assertIn("elementFromPoint", SRC_RUNNER)
        self.assertIn("def _worldle_phrase_click_verify", SRC_RUNNER)
        # Caller passes page, gid, _picked (whitespace-normalised:
        # the iter-16 call spans lines + passes _pairs_in; the
        # normalised text has a space after "(").
        self.assertIn("_worldle_phrase_click_verify( page, gid, _picked",
                      " ".join(SRC_RUNNER.split()))
        self.assertIn('return ("click-fallback"', SRC_RUNNER)


class _FakeAreaPage:
    """Sync fake for _s_worldle_play_comparison store-verify."""

    def __init__(self, record):
        self.record = record  # False = iter-14 behaviour: store never changes
        self.store = []

    def evaluate(self, js, arg=None):
        if "worldle-comparison-probe" in js:
            return {"prompt_found": True,
                    "options": [{"name": "Kuwait", "x": 1, "y": 1},
                                {"name": "Qatar", "x": 2, "y": 2}]}
        if "worldle-comparison-click" in js:
            if self.record:
                self.store = [{"question": 1, "answer": "Kuwait",
                               "correct": "Kuwait"}]
            return [True, "Kuwait"]
        if "localStorage.getItem" in js:
            if arg == "size-guesses":
                return {"2026-10-18": list(self.store)}
            return {}
        if "document.body.innerText" in js:
            return "Which country has a larger surface area? Kuwait Qatar"
        return True

    def wait_for_timeout(self, ms):
        return None


class TestWorldleAreaStoreVerify(unittest.TestCase):
    def test_comparison_answered_when_store_changes(self):
        page = _FakeAreaPage(record=True)
        st, pick, det = R._s_worldle_play_comparison(
            page, "worldle", {}, "area", "2026-10-18",
            ("larger surface area",), {"kuwait": 17818, "qatar": 11000},
            "size", "area", verify_store=True)
        self.assertEqual(st, "attempted")
        self.assertEqual(pick, "Kuwait")

    def test_comparison_not_answered_when_store_unchanged(self):
        # Iter-14: click + Enter, store changed=False twice, yet the
        # code claimed "answered" and ran the number path on the
        # wrong view. Now: partial, no "answered" claim.
        page = _FakeAreaPage(record=False)
        st, pick, det = R._s_worldle_play_comparison(
            page, "worldle", {}, "area", "2026-10-18",
            ("larger surface area",), {"kuwait": 17818, "qatar": 11000},
            "size", "area", verify_store=True)
        self.assertEqual(st, "partial")
        self.assertIsNone(pick)
        self.assertIn("did NOT change", det)

    def test_ad_div_excluded_from_steppers(self):
        self.assertIn("_1SS2i7sB", R._AREA_STEPPER_SELECT_JS)
        self.assertIn("GetAdmiral", R._AREA_STEPPER_SELECT_JS)

    def test_area_uses_store_verify_and_reswitch(self):
        self.assertIn("verify_store=True", SRC_RUNNER)
        self.assertIn("_worldle_area_log_state(page, gid", SRC_RUNNER)
        self.assertIn("_worldle_area_still_on_area(page)", SRC_RUNNER)
        self.assertIn('_s_worldle_switch_mode(page, gid, "area", ds)',
                      SRC_RUNNER)


class _FakeRoundPage:
    def __init__(self, game_mode, header):
        self._mode = game_mode
        self._header = header

    def evaluate(self, js, arg=None):
        if "localStorage.getItem" in js:
            if arg == "game_round":
                return {"gameMode": self._mode} if self._mode else None
            return None
        if "aria-selected" in js:
            return self._header
        return ""


class TestWorldleAreaStillOnArea(unittest.TestCase):
    def test_on_area(self):
        page = _FakeRoundPage("area", "2. AREA")
        self.assertTrue(R._worldle_area_still_on_area(page))

    def test_moved_via_game_round(self):
        page = _FakeRoundPage("populationCurrency", "2. AREA")
        self.assertFalse(R._worldle_area_still_on_area(page))

    def test_moved_via_header(self):
        page = _FakeRoundPage("area", "1. POPULATION ✓")
        self.assertFalse(R._worldle_area_still_on_area(page))

    def test_missing_evidence_counts_as_on_area(self):
        page = _FakeRoundPage(None, "")
        self.assertTrue(R._worldle_area_still_on_area(page))


class TestBetweenlePostWinStop(unittest.TestCase):
    def test_stats_word_equals_answer_is_win(self):
        self.assertTrue(R._betweenle_stats_won(
            {"stats_word": "BIKER"}, "biker"))
        self.assertTrue(R._betweenle_stats_won(
            {"stats_word": "biker"}, "BIKER"))

    def test_stats_word_mismatch_not_win(self):
        self.assertFalse(R._betweenle_stats_won(
            {"stats_word": "HIKER"}, "BIKER"))
        self.assertFalse(R._betweenle_stats_won({"stats_word": ""}, "BIKER"))
        self.assertFalse(R._betweenle_stats_won({}, "BIKER"))

    def test_won_after_guess_uses_counters_without_semantics(self):
        # Iter-14: the in-loop _strict_win could not see the win
        # (semantics enabled only after the loop), so HIKER was
        # typed after BIKER although counters showed stats='BIKER'.
        class _Page:
            def evaluate(self, js, *a):
                return {"guess_n": 3, "guess_total": 14,
                        "stats_word": "BIKER", "win_popup": True,
                        "tail": ""}

        won, ev = R._betweenle_won_after_guess(_Page(), "BIKER")
        self.assertTrue(won)
        self.assertIn("stats_word", ev)


class TestTeuteufBanner(unittest.TestCase):
    def test_banner_dismiss_wired(self):
        self.assertIn("def _dismiss_teuteuf_banner", SRC_RUNNER)
        self.assertIn("Teuteuf Edu", SRC_RUNNER)
        self.assertIn("_dismiss_teuteuf_banner(page)", SRC_RUNNER)


if __name__ == "__main__":
    unittest.main()
