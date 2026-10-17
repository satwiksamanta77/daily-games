"""Fix-loop iteration 14 tests (evidence-grounded in ANALYSIS10).

1. Nerdle header scrape (P0): multi-strategy capture — the iter-13
   regex matched nothing, so header_number was null on all 9 modes
   and the gate blocked a correct solve.
2. Nerdle per-mode API trust (P0): the API is authoritative for
   classic/mini/midi/maxi ONLY; micro/minibi/quad/instant/speed are
   site-rules (API mismatch never blocks, API never cited as proof).
3. Worldle Trivia stopword scoring (P1): isMonarchy/isTerritory/
   hasMetro capped at 0.5 because stopwords were stripped from the
   statement tokens but NOT from the key words.
4. Worldle Area icon filter (P1): 'Zoom out icon' / 'Previous icon' /
   'Next icon' polluted the comparison probe and blocked the pick.
5. Worldle Capital tab activation (P1): PINPOINT active_after=False
   and the code proceeded anyway; the "proceed anyway" path is gone.
6. Worldle Language slot probe (P1): primary probe returned
   slots=[] and the click-verify fallback was never reached.
7. Betweenle post-win stop (P3): one extra guess (HIKER) played on
   the already-won board.
8. Nerdle speed matches=True weakness (P3): 1/2 equations matched
   counted as matches=True — both must match.

All tests are synthetic/local: no network, no browser, no CI run.
"""
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import runner as R
import nerdle_solver as NS

SRC_RUNNER = (Path(__file__).parent.parent / "runner.py").read_text(
    encoding="utf-8")


class TestNerdleHeaderCapture(unittest.TestCase):
    def test_header_element_strategy(self):
        n, strat, raw = NS._nerdle_header_number_from_probe(
            {"header_text": "Nerdle #1733", "hash_texts": [],
             "data_attrs": [], "url": "", "body_text": ""})
        self.assertEqual((n, strat), (1733, "header-element"))
        self.assertEqual(raw, "Nerdle #1733")

    def test_hash_element_strategy(self):
        n, strat, _ = NS._nerdle_header_number_from_probe(
            {"header_text": "Nerdle", "hash_texts": ["Puzzle #1733"],
             "data_attrs": [], "url": "", "body_text": ""})
        self.assertEqual((n, strat), (1733, "hash-element"))

    def test_data_attribute_strategy(self):
        n, strat, _ = NS._nerdle_header_number_from_probe(
            {"header_text": "", "hash_texts": [],
             "data_attrs": ["puzzleNumber=1733"], "url": "",
             "body_text": ""})
        self.assertEqual((n, strat), (1733, "data-attribute"))

    def test_url_strategy(self):
        n, strat, _ = NS._nerdle_header_number_from_probe(
            {"header_text": "", "hash_texts": [], "data_attrs": [],
             "url": "https://www.nerdlegame.com/game#1733",
             "body_text": ""})
        self.assertEqual((n, strat), (1733, "url"))

    def test_json_blob_strategy(self):
        n, strat, _ = NS._nerdle_header_number_from_probe(
            {"header_text": "", "hash_texts": [], "data_attrs": [],
             "url": "https://www.nerdlegame.com/game",
             "body_text": '{"puzzleNumber": 1733, "x": 1}'})
        self.assertEqual((n, strat), (1733, "json-blob-or-body"))

    def test_iter13_pattern_covered_by_body_step(self):
        # The iter-13 pattern (nerdle. #1733) is covered by the
        # json-blob-or-body step's # pattern — no separate step.
        n, strat, _ = NS._nerdle_header_number_from_probe(
            {"header_text": "", "hash_texts": [], "data_attrs": [],
             "url": "https://www.nerdlegame.com/game",
             "body_text": "welcome to nerdle. #1733 daily"})
        self.assertEqual((n, strat), (1733, "json-blob-or-body"))

    def test_none_when_no_evidence(self):
        n, strat, _ = NS._nerdle_header_number_from_probe(
            {"header_text": "", "hash_texts": [], "data_attrs": [],
             "url": "https://www.nerdlegame.com/game", "body_text": ""})
        self.assertIsNone(n)
        self.assertEqual(strat, "none")

    def test_first_plausible_wins(self):
        # Header beats a conflicting hash-element number.
        n, strat, _ = NS._nerdle_header_number_from_probe(
            {"header_text": "Nerdle #1733",
             "hash_texts": ["Puzzle #9999"], "data_attrs": [],
             "url": "", "body_text": ""})
        self.assertEqual((n, strat), (1733, "header-element"))

    def test_served_js_returns_raw_probe_not_a_number(self):
        # The browser probe returns RAW evidence; the Python parser
        # applies the strategies (unit-testable). The old single
        # regex must not be the capture anymore.
        self.assertIn("header_probe", NS.SERVED_JS)
        self.assertIn("hash_texts", NS.SERVED_JS)
        self.assertIn("data_attrs", NS.SERVED_JS)
        # The probe must not COMPUTE header_number (no key
        # assignment); the Python parser does that from the raw
        # evidence. (A comment may still mention the field name.)
        self.assertNotIn("header_number:", NS.SERVED_JS)


class TestNerdlePerModeTrust(unittest.TestCase):
    API_1018 = {
        "classic": [1733], "micro": [26], "mini": [497],
        "midi": [1733], "maxi": [1368], "minibi": [603, 6],
        "quad": [3289, 3309, 3329, 3349],
        "speed": [1732, 1725], "instant": [1718],
        "classicPuzzleNumber": 1733}

    def _served(self, **kw):
        # kw: mode -> (header_number, matches_target_official)
        return {m: {"header_number": h, "matches_target_official": mt}
                for m, (h, mt) in kw.items()}

    def test_trust_table_constants(self):
        self.assertEqual(NS.NERDLE_API_PROVEN_MODES,
                         ("classic", "mini", "midi", "maxi"))
        self.assertEqual(NS.NERDLE_API_UNPROVEN_MODES,
                         ("micro", "minibi", "quad", "instant", "speed"))

    def test_boundary_constant_unchanged(self):
        # The site has NOT moved its post to 9pm IST: (16, 30) stays.
        # A move would make it (15, 30) — a pending user decision,
        # NOT this iteration.
        self.assertEqual(NS.NERDLE_SITE_BOUNDARY_UTC, (16, 30))

    def test_proven_answer_mismatch_blocks(self):
        # Mini is API-proven: served != API blocks.
        served = self._served(classic=(1733, True), mini=(497, False))
        blocked, detail, _ = NS._nerdle_number_gate(served, self.API_1018)
        self.assertTrue(blocked)
        self.assertIn("mini", detail)

    def test_proven_header_mismatch_blocks(self):
        served = self._served(classic=(1733, True), midi=(9999, True))
        blocked, detail, _ = NS._nerdle_number_gate(served, self.API_1018)
        self.assertTrue(blocked)
        self.assertIn("midi", detail)

    def test_unproven_answer_mismatch_never_blocks(self):
        # ANALYSIS10: site answers DIFFER from the API for these
        # modes, so the SITE rules — an API mismatch is a warning.
        for mode in ("micro", "minibi", "quad", "instant", "speed"):
            served = self._served(classic=(1733, True))
            served[mode] = {"header_number": self.API_1018[mode][0],
                            "matches_target_official": False}
            blocked, _, per = NS._nerdle_number_gate(served, self.API_1018)
            self.assertFalse(blocked, mode)
            self.assertEqual(per[mode]["trust"], "site-rules")

    def test_unproven_header_mismatch_never_blocks(self):
        # Iter-13 site micro 5+3=8 vs API 5+4=9: the API cannot
        # veto the site's own board for unproven modes.
        served = self._served(classic=(1733, True), micro=(99, True))
        blocked, _, per = NS._nerdle_number_gate(served, self.API_1018)
        self.assertFalse(blocked)
        self.assertIn("warning", per["micro"]["status"])
        self.assertEqual(per["micro"]["trust"], "site-rules")

    def test_classic_still_fail_closed(self):
        served = self._served(classic=(None, True))
        blocked, _, _ = NS._nerdle_number_gate(served, self.API_1018)
        self.assertTrue(blocked)
        served = self._served(classic=(1733, False))
        blocked, _, _ = NS._nerdle_number_gate(served, self.API_1018)
        self.assertTrue(blocked)

    def test_all_proven_match_passes(self):
        served = self._served(classic=(1733, True), mini=(497, True),
                              midi=(1733, True), maxi=(1368, True),
                              micro=(99, False))
        blocked, _, _ = NS._nerdle_number_gate(served, self.API_1018)
        self.assertFalse(blocked)

    def test_runner_gate_mirrors_solver(self):
        served = self._served(classic=(1733, True), mini=(497, False))
        self.assertTrue(R._nerdle_number_gate(served, self.API_1018)[0])
        served2 = self._served(classic=(1733, True), micro=(99, False))
        blocked, _, per = R._nerdle_number_gate(served2, self.API_1018)
        self.assertFalse(blocked)
        self.assertEqual(per["micro"]["trust"], "site-rules")


class TestNerdleSpeedMatches(unittest.TestCase):
    API_PAIR = ["711/9=79", "2+10-6=6"]

    def test_both_match_is_true(self):
        ok, detail = NS._nerdle_speed_matches(
            ["711/9=79", "2+10-6=6"], self.API_PAIR, boards_n=2)
        self.assertTrue(ok)
        self.assertIn("2/2", detail)

    def test_iter13_partial_is_not_a_match(self):
        # Iter-13 production case: played 50-6*7=8, 2+10-6=6 —
        # only 1/2 matched the API pair, yet matches=True.
        ok, detail = NS._nerdle_speed_matches(
            ["50-6*7=8", "2+10-6=6"], self.API_PAIR, boards_n=2)
        self.assertFalse(ok)
        self.assertIn("partial", detail)
        self.assertIn("1/2", detail)

    def test_none_match_is_false(self):
        ok, _ = NS._nerdle_speed_matches(
            ["1+2=3", "4+5=9"], self.API_PAIR, boards_n=2)
        self.assertFalse(ok)

    def test_empty_is_false(self):
        ok, _ = NS._nerdle_speed_matches([], self.API_PAIR, boards_n=2)
        self.assertFalse(ok)


class TestWorldleTriviaStopwords(unittest.TestCase):
    # ANALYSIS10 exact Kuwait FACTS statements + values.
    FACTS = {"top20WorldHeritageSites": False, "isMonarchy": True,
             "isTerritory": False, "hasMetro": False}
    STATEMENTS = [
        ("Top 20 in number of World Heritage sites",
         "top20WorldHeritageSites"),
        ("Is a monarchy", "isMonarchy"),
        ("Is a dependency/territory", "isTerritory"),
        ("Has a metro system", "hasMetro"),
    ]

    def test_each_kuwait_statement_maps(self):
        for text, want in self.STATEMENTS:
            self.assertEqual(
                R._s_worldle_bool_key_for_statement(text, self.FACTS),
                want, text)

    def test_exactly_one_want_true_candidate(self):
        # With stopwords stripped from the KEY side, isMonarchy
        # scores {monarchy} = 1.0 (was 1/2 = 0.5 < 0.6 -> None).
        # Kuwait IS a monarchy, so exactly one statement is TRUE.
        trues = [text for text, _ in self.STATEMENTS
                 if self.FACTS[R._s_worldle_bool_key_for_statement(
                     text, self.FACTS)] is True]
        self.assertEqual(trues, ["Is a monarchy"])


class TestWorldleAreaIconFilter(unittest.TestCase):
    def test_pick_drops_three_chrome_icons(self):
        # ANALYSIS10 Area fixture: 3 map-control buttons + 2 real
        # country cards -> the pick resolves to a real option card.
        opts = [{"name": "Zoom out icon", "aria": "Zoom out icon"},
                {"name": "Previous icon"},
                {"name": "Next icon", "title": "Next icon"},
                {"name": "Kuwait"},
                {"name": "Qatar"}]
        vals = {"kuwait": 17818, "qatar": 11610}
        best, detail = R._s_worldle_comparison_pick(opts, vals)
        self.assertEqual(best, "Kuwait")
        self.assertIn("17818", detail)

    def test_pick_drops_icon_via_alt(self):
        opts = [{"name": "Kuwait"},
                {"name": "Qatar", "alt": "Previous icon"}]
        vals = {"kuwait": 17818, "qatar": 11610}
        # Qatar's card carries an icon alt -> dropped; only Kuwait
        # remains -> pick refuses (need >=2 real options).
        best, _ = R._s_worldle_comparison_pick(opts, vals)
        self.assertIsNone(best)

    def test_probe_js_has_icon_filter(self):
        import inspect
        src = inspect.getsource(R._s_worldle_comparison_probe)
        self.assertIn("icon", src)
        self.assertIn("_iconish", src)


class _FakeLocator:
    def __init__(self, count=0):
        self._count = count

    @property
    def first(self):
        return self

    def count(self):
        return self._count

    def is_visible(self):
        return self._count > 0

    def click(self, timeout=None):
        return None


class _FakeCapitalPage:
    """State machine for PINPOINT activation: JS click activates only
    when told to; the retry click activates only when told to."""

    def __init__(self, *, active=False, click_activates=True,
                 retry_activates=True, found=True):
        self.active = active
        self._click_activates = click_activates
        self._retry_activates = retry_activates
        self._found = found
        self.clicks = 0

    def locator(self, sel):
        return _FakeLocator(count=0)

    def wait_for_timeout(self, ms):
        pass

    def evaluate(self, js, *args):
        if "CAPITAL_TAB_ACTIVE_CHECK" in js:
            return self.active
        if "CAPITAL_TAB_DUMP" in js:
            return [{"text": "1. NAME", "aria_selected": "true",
                     "cls": "active", "data_state": "active",
                     "data_value": "name", "role": "tab", "id": "",
                     "disabled": False, "outer": "<button>1. NAME</button>"},
                    {"text": "2. PINPOINT", "aria_selected": None,
                     "cls": "", "data_state": "inactive",
                     "data_value": "pinpoint", "role": "tab", "id": "",
                     "disabled": False,
                     "outer": "<button>2. PINPOINT</button>"}]
        if "CAPITAL_TAB_CLICK_JS" in js:
            self.clicks += 1
            if not self._found:
                return {"found": False}
            if self._click_activates:
                self.active = True
            return {"found": True, "via": "js-click"}
        if "CAPITAL_TAB_RETRY_CLICK" in js:
            self.clicks += 1
            if self._retry_activates:
                self.active = True
            return {"found": True, "via": "retry-alt-selector"}
        return None


class TestWorldleCapitalActivation(unittest.TestCase):
    def test_already_active_no_click(self):
        pg = _FakeCapitalPage(active=True)
        ok, detail = R._worldle_capital_activate_pinpoint(pg, "worldle")
        self.assertTrue(ok)
        self.assertIn("already active", detail)
        self.assertEqual(pg.clicks, 0)

    def test_first_click_activates(self):
        pg = _FakeCapitalPage()
        ok, _ = R._worldle_capital_activate_pinpoint(pg, "worldle")
        self.assertTrue(ok)
        self.assertTrue(pg.active)

    def test_retry_activates_after_first_click_fails(self):
        pg = _FakeCapitalPage(click_activates=False,
                              retry_activates=True)
        ok, detail = R._worldle_capital_activate_pinpoint(pg, "worldle")
        self.assertTrue(ok)
        self.assertIn("retry", detail)
        self.assertTrue(pg.active)

    def test_never_activates_fails_honestly_with_dump(self):
        # Iter-13 behaviour (proceed anyway) must be gone: the
        # helper refuses and carries the tab DOM dump as evidence.
        pg = _FakeCapitalPage(click_activates=False,
                              retry_activates=False)
        ok, detail = R._worldle_capital_activate_pinpoint(pg, "worldle")
        self.assertFalse(ok)
        self.assertIn("NEVER activated", detail)
        self.assertIn("PINPOINT", detail)  # dump evidence
        self.assertIn("pin NOT clicked", detail)

    def test_tab_not_found(self):
        pg = _FakeCapitalPage(found=False)
        ok, detail = R._worldle_capital_activate_pinpoint(pg, "worldle")
        self.assertFalse(ok)
        self.assertIn("not found", detail)

    def test_proceed_anyway_path_is_gone(self):
        self.assertNotIn("proceeding to pin click",
                         SRC_RUNNER)
        self.assertIn("_worldle_capital_activate_pinpoint(page, gid)",
                      SRC_RUNNER)


class _FakeMouse:
    def __init__(self):
        self.moves = 0

    def move(self, x, y, steps=1):
        self.moves += 1

    def down(self):
        pass

    def up(self):
        pass


class _FakePhrasePage:
    """Primary probe returns chips + slots=[] (iter-13); the broad
    probe returns whatever broad_slots holds."""

    def __init__(self, broad_slots):
        self.broad_slots = broad_slots
        self.mouse = _FakeMouse()

    def wait_for_timeout(self, ms):
        pass

    def evaluate(self, js, *args):
        if "WORLDLE-PHRASE-BROAD-SLOT-PROBE" in js:
            return {"slots": list(self.broad_slots),
                    "empty_boxes": list(self.broad_slots)}
        if "args.sel" in js:
            return False  # narrow slot-verify: nothing placed
        return {"chips": [
            {"code": "ara", "phrase": "اسمي", "draggable": False,
             "x": 100.0, "y": 100.0},
            {"code": "eng", "phrase": "My name is", "draggable": False,
             "x": 200.0, "y": 200.0}],
            "slots": []}


class TestWorldleLanguageSlotProbe(unittest.TestCase):
    PICKED = {"ara": "اسمي", "eng": "My name is"}
    CODES = ["ara", "eng"]
    NAMES = ["Arabic", "English"]
    PROMPTS = ["My name is"]

    def test_broad_probe_returns_slots(self):
        slots = [{"label": "", "x": 500.0, "y": 300.0, "text": "",
                  "kind": "bordered-box"}]
        pg = _FakePhrasePage(slots)
        got = R._worldle_phrase_broad_slot_probe(pg)
        self.assertEqual(got["slots"], slots)

    def test_no_slots_anywhere_reaches_click_fallback(self):
        # Iter-13: drag returned "partial" on slots=[] and the
        # click-verify fallback was never reached. Now it must
        # return "click-fallback" so the caller reaches the click path.
        pg = _FakePhrasePage([])
        status, detail = R._worldle_phrase_drag(
            pg, "worldle", self.PICKED, self.CODES, self.NAMES,
            self.PROMPTS, "2026-10-18")
        self.assertEqual(status, "click-fallback")
        self.assertIn("broad probe", detail)

    def test_broad_slots_are_adopted_for_drag(self):
        # Broad probe finds 2 unlabelled slots -> drag is attempted
        # (mouse moves happen); it does not fall back to click.
        pg = _FakePhrasePage([
            {"label": "", "x": 500.0, "y": 300.0, "text": "",
             "kind": "bordered-box"},
            {"label": "", "x": 700.0, "y": 300.0, "text": "",
             "kind": "bordered-box"}])
        status, detail = R._worldle_phrase_drag(
            pg, "worldle", self.PICKED, self.CODES, self.NAMES,
            self.PROMPTS, "2026-10-18")
        # Drag attempted but nothing placed (fake never places) ->
        # honest partial naming the drag failure, NOT a slot-less
        # partial and NOT a click-fallback.
        self.assertEqual(status, "partial")
        self.assertIn("phrase drag failed", detail)
        self.assertGreater(pg.mouse.moves, 0)


class _FakeWinPage:
    def __init__(self, won):
        self._won = won

    def evaluate(self, js, *args):
        if "flt-semantics" in js:
            return ""
        return "YOU WON" if self._won else "guess the word"


class TestBetweenlePostWinStop(unittest.TestCase):
    def test_won_after_guess_true_on_win_signal(self):
        won, ev = R._betweenle_won_after_guess(_FakeWinPage(True),
                                               "BIKER")
        self.assertTrue(won)
        self.assertIn("win signal", ev)

    def test_won_after_guess_false_without_signal(self):
        won, _ = R._betweenle_won_after_guess(_FakeWinPage(False),
                                              "BIKER")
        self.assertFalse(won)

    def test_loop_checks_win_after_each_guess(self):
        # The _s_betweenle loop must call the per-guess win check
        # and break (no further guesses on a won board) — the
        # iter-13 HIKER-on-a-won-board path.
        import inspect
        src = inspect.getsource(R._s_betweenle)
        self.assertIn("_betweenle_won_after_guess(page, ans)", src)
        self.assertIn("no further guesses on a won board", src)


if __name__ == "__main__":
    unittest.main()
