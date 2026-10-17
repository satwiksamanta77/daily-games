"""Fix-loop iteration 13 tests.

Nerdle wrong-day (P0, REVISED): the target date is the WEBSITE
effective date (wordsolverx puzzle-window rule for nerdle: boundary
16:30 UTC, offset +1 — after 16:30 UTC the effective date is UTC+1,
before it is UTC; 16:30 UTC = 22:00 IST = the site's 10pm IST post).
Answers + puzzle numbers come from the site's own published API
(nerdle-answers worker, dated URL only, never /today). HARD GATE:
the served puzzle number must equal the API's number per mode, or
no final ships. Betweenle (P0): stale buffer re-submits, banner
classifier misattribution, WORD phantom, retry-chain reachability.
"""
import os
import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

import runner as R
import nerdle_solver as NS


class TestNerdleEffectiveDate(unittest.TestCase):
    def test_boundary_constant(self):
        self.assertEqual(NS.NERDLE_SITE_BOUNDARY_UTC, (16, 30))

    def test_before_boundary_is_utc_date(self):
        # 16:29 UTC Oct-17 -> Oct-17 (site has not posted Oct-18).
        self.assertEqual(
            NS._nerdle_effective_date(datetime(2026, 10, 17, 16, 29)),
            datetime(2026, 10, 17))

    def test_inside_grace_still_pre_boundary(self):
        # 16:30:30 UTC is inside the 60 s rollover grace: the site
        # still serves the pre-boundary date until 16:30:60.
        self.assertEqual(
            NS._nerdle_effective_date(datetime(2026, 10, 17, 16, 30, 30)),
            datetime(2026, 10, 17))

    def test_after_boundary_is_utc_plus_one(self):
        # 16:31 UTC Oct-17 -> Oct-18 (the 10pm IST post has flipped).
        self.assertEqual(
            NS._nerdle_effective_date(datetime(2026, 10, 17, 16, 31)),
            datetime(2026, 10, 18))

    def test_late_night_is_next_day(self):
        self.assertEqual(
            NS._nerdle_effective_date(datetime(2026, 10, 17, 23, 0)),
            datetime(2026, 10, 18))

    def test_morning_is_same_day(self):
        self.assertEqual(
            NS._nerdle_effective_date(datetime(2026, 10, 18, 9, 0)),
            datetime(2026, 10, 18))

    def test_runner_mirror_agrees(self):
        for h in (0, 9, 15, 16, 17, 23):
            now = datetime(2026, 10, 17, h, 0)
            self.assertEqual(R._nerdle_effective_date(now),
                             NS._nerdle_effective_date(now))

    def test_target_date_uses_effective_date_not_fake(self):
        # FAKE_DATE_ISO (pipeline pin) must NOT move Nerdle: the
        # website effective date rules. Env override carries the
        # runner-computed effective date.
        with mock.patch.dict(os.environ, {
                "FAKE_DATE_ISO": "2026-10-20T00:05:00",
                "NERDLE_EFFECTIVE_DATE": "2026-10-18"}):
            self.assertEqual(NS._target_date(), datetime(2026, 10, 18))


class TestNerdleNumberGate(unittest.TestCase):
    API_1018 = {
        "classic": [1733], "micro": [26], "mini": [497],
        "midi": [1733], "maxi": [1368], "minibi": [603, 6],
        "quad": [3289, 3309, 3329, 3349],
        "speed": [1732, 1725], "instant": [1718],
        "classicPuzzleNumber": 1733}

    def _served(self, **headers):
        return {m: {"header_number": h, "matches_target_official": True}
                for m, h in headers.items()}

    def test_all_match_passes(self):
        served = self._served(classic=1733, micro=26, mini=497,
                              midi=1733, maxi=1368, minibi=603,
                              quad=3309, speed=1725, instant=1718)
        blocked, _, _ = NS._nerdle_number_gate(served, self.API_1018)
        self.assertFalse(blocked)

    def test_classic_header_mismatch_blocks(self):
        # Iter-12 production case: header #1732 for an Oct-18 target.
        served = self._served(classic=1732)
        blocked, detail, _ = NS._nerdle_number_gate(served, self.API_1018)
        self.assertTrue(blocked)
        self.assertIn("1732", detail)

    def test_classic_header_missing_blocks(self):
        served = {"classic": {"matches_target_official": True}}
        blocked, _, _ = NS._nerdle_number_gate(served, self.API_1018)
        self.assertTrue(blocked)

    def test_classic_answer_mismatch_blocks(self):
        served = {"classic": {"header_number": 1733,
                              "matches_target_official": False}}
        blocked, _, _ = NS._nerdle_number_gate(served, self.API_1018)
        self.assertTrue(blocked)

    def test_other_mode_exposed_mismatch_blocks(self):
        served = self._served(classic=1733, micro=99)
        blocked, detail, _ = NS._nerdle_number_gate(served, self.API_1018)
        self.assertTrue(blocked)
        self.assertIn("micro", detail)

    def test_other_mode_not_exposed_is_warning_only(self):
        # The site may not expose a per-mode number to check; that is
        # a warning, not a block (classic remains the day proof).
        served = self._served(classic=1733)
        blocked, _, per = NS._nerdle_number_gate(served, self.API_1018)
        self.assertFalse(blocked)
        self.assertEqual(per["micro"]["status"], "warning: not exposed")

    def test_other_mode_answer_mismatch_does_not_block(self):
        # API answers disagree with the site for micro/minibi/quad
        # (proven iter-12: site micro 8-7=1 vs API 8/2=4), so
        # answer-equality cannot prove a wrong day there.
        served = {"classic": {"header_number": 1733,
                              "matches_target_official": True},
                  "micro": {"header_number": 26,
                            "matches_target_official": False}}
        blocked, _, _ = NS._nerdle_number_gate(served, self.API_1018)
        self.assertFalse(blocked)

    def test_no_api_numbers_blocks(self):
        blocked, _, _ = NS._nerdle_number_gate({}, None)
        self.assertTrue(blocked)

    def test_runner_gate_mirrors_solver(self):
        served = self._served(classic=1732)
        blocked, _, _ = R._nerdle_number_gate(served, self.API_1018)
        self.assertTrue(blocked)
        served_ok = self._served(classic=1733, micro=26)
        blocked, _, _ = R._nerdle_number_gate(served_ok, self.API_1018)
        self.assertFalse(blocked)


class TestNerdleApiFetch(unittest.TestCase):
    API_PAYLOAD = {"success": True, "data": {
        "date": "2026-10-18", "classicPuzzleNumber": 1733,
        "modes": [
            {"id": "classic", "puzzleNumbers": [1733],
             "answers": [{"puzzleNumber": 1733, "answer": "9*83=747",
                          "type": "equation"}]},
            {"id": "micro", "puzzleNumbers": [26],
             "answers": [{"puzzleNumber": 26, "answer": "5+4=9",
                          "type": "equation"}]}]}}

    def _resp(self, ok=True, status=200, payload=None):
        r = mock.Mock()
        r.ok = ok
        r.status_code = status
        r.json.return_value = payload if payload is not None else {}
        return r

    def test_dated_url_only_never_today(self):
        with mock.patch.object(NS.requests, "get",
                               return_value=self._resp(payload=self.API_PAYLOAD)) as g:
            _, eqs, nums = NS.fetch_nerdle_answers(datetime(2026, 10, 18))
        self.assertEqual(g.call_count, 1)
        called_url = g.call_args[0][0]
        self.assertIn("/2026-10-18", called_url)
        self.assertNotIn("/today", called_url)
        self.assertEqual(eqs["classic"], ["9*83=747"])
        self.assertEqual(nums["classic"], [1733])
        self.assertEqual(nums["classicPuzzleNumber"], 1733)

    def test_404_defers_no_fallback(self):
        with mock.patch.object(NS.requests, "get",
                               return_value=self._resp(ok=False, status=404)) as g:
            _, eqs, nums = NS.fetch_nerdle_answers(datetime(2026, 10, 18))
        self.assertEqual(g.call_count, 1)  # no /today fallback
        self.assertEqual(eqs, {})
        self.assertIsNone(nums)


class TestBetweenleBannerSemantic(unittest.TestCase):
    def test_parse_bounds(self):
        tuples, invalid = R._betweenle_banner_semantic(
            "enter word placed in dictionary before diner")
        self.assertEqual(tuples, frozenset({("before", "DINER")}))
        self.assertFalse(invalid)

    def test_parse_invalid(self):
        tuples, invalid = R._betweenle_banner_semantic("not a word")
        self.assertEqual(tuples, frozenset())
        self.assertTrue(invalid)

    def test_same_tuple_not_new_despite_excerpt_change(self):
        # Surrounding-text change must not fake a fresh rejection.
        before = R._betweenle_extract_banner(
            "Score 5. Enter word placed in dictionary before PARTY")
        after = R._betweenle_extract_banner(
            "Score 6! Enter word placed in dictionary before PARTY")
        self.assertFalse(R._betweenle_new_rejection(before, after))

    def test_consistency_stale_viper_vs_biker(self):
        # Iter-12 killer: banner (before, DINER) belongs to stale VIPER,
        # not BIKER (BIKER < DINER cannot violate "before DINER").
        self.assertTrue(R._betweenle_banner_consistent(
            {("before", "DINER")}, "VIPER"))
        self.assertFalse(R._betweenle_banner_consistent(
            {("before", "DINER")}, "BIKER"))

    def test_consistency_after(self):
        self.assertTrue(R._betweenle_banner_consistent(
            {("after", "DINER")}, "BIKER"))
        self.assertFalse(R._betweenle_banner_consistent(
            {("after", "DINER")}, "VIPER"))

    def test_inconsistent_banner_is_buffer_failure(self):
        verdict = R._betweenle_classify_submit(
            "", R._betweenle_extract_banner(
                "Enter word placed in dictionary before DINER"),
            3, {"guess_n": 3, "stats_word": ""}, "BIKER")
        self.assertEqual(verdict, "buffer_failure")

    def test_consistent_banner_is_rejected(self):
        verdict = R._betweenle_classify_submit(
            "", R._betweenle_extract_banner(
                "Enter word placed in dictionary before DINER"),
            3, {"guess_n": 3, "stats_word": ""}, "VIPER")
        self.assertEqual(verdict, "rejected")

    def test_placeholder_word_is_not_submitted(self):
        # Stats panel carries the 'WORD' placeholder during play —
        # it must never count as a submitted guess.
        verdict = R._betweenle_classify_submit(
            "", "", 1, {"guess_n": 1, "stats_word": "WORD"}, "WORD")
        self.assertEqual(verdict, "no_change")

    def test_real_stats_word_is_submitted(self):
        verdict = R._betweenle_classify_submit(
            "", "", 2, {"guess_n": 2, "stats_word": "BIKER"}, "BIKER")
        self.assertEqual(verdict, "submitted")


class _FakeBetweenlePage:
    """Minimal page stub for the restore-seed (counters only)."""

    def __init__(self, guess_n=1, stats_word="WORD"):
        self._counters = {"guess_n": guess_n, "guess_total": 14,
                          "stats_word": stats_word, "win_popup": False,
                          "tail": ""}

    def evaluate(self, js):
        return dict(self._counters)


class TestBetweenleRestoreSeed(unittest.TestCase):
    def test_word_phantom_not_seeded(self):
        # Fresh board: GUESS 1/14, stats panel placeholder WORD.
        # Iter-12 seeded WORD as submitted; the counter arithmetic
        # (1/14 -> 3/14 after 2 real submits) proves it never was.
        with mock.patch.dict(R._PLAYED, {}, clear=True), \
                mock.patch.dict(R._REJECTED, {}, clear=True):
            used, submitted, rejected = R._betweenle_seed_restored_state(
                _FakeBetweenlePage(guess_n=1, stats_word="WORD"),
                "betweenle")
        self.assertEqual(submitted, [])
        self.assertNotIn("WORD", used)
        self.assertEqual(rejected, [])

    def test_guess_counter_is_current_number(self):
        # GUESS 3/14 means 2 submitted before this run, not 3.
        with mock.patch.dict(R._PLAYED, {}, clear=True), \
                mock.patch.dict(R._REJECTED, {}, clear=True):
            used, submitted, rejected = R._betweenle_seed_restored_state(
                _FakeBetweenlePage(guess_n=3, stats_word="WORD"),
                "betweenle")
        self.assertEqual(submitted, [])


if __name__ == "__main__":
    unittest.main()


class TestWorldleComparisonSkipped(unittest.TestCase):
    class _Page:
        def __init__(self, body):
            self._body = body

        def evaluate(self, js):
            return self._body

    SKIP = ("The GDP comparison round has been skipped, as Kuwait "
            "does not have enough neighbours to compare.")

    def test_skip_message_detected(self):
        ok, det = R._s_worldle_comparison_skipped(
            self._Page(self.SKIP + " Some other text."))
        self.assertTrue(ok)
        self.assertIn("has been skipped", det)

    def test_no_skip_message(self):
        ok, det = R._s_worldle_comparison_skipped(
            self._Page("Which country has a larger surface area?"))
        self.assertFalse(ok)
        self.assertEqual(det, "")

    def test_evaluate_exception_is_not_skipped(self):
        class _Bad:
            def evaluate(self, js):
                raise RuntimeError("boom")
        ok, det = R._s_worldle_comparison_skipped(_Bad())
        self.assertFalse(ok)


class TestFramedSwitchBounds(unittest.TestCase):
    def _per_mode(self):
        # t_switch = when the bot navigates to the mode; t_start =
        # when the board is ready. Poster gate dead time is 300->340.
        return [
            {"mode": "classic", "t_switch": 10, "t_start": 30},
            {"mode": "one_frame", "t_switch": 100, "t_start": 120},
            {"mode": "titleshot", "t_switch": 200, "t_start": 210},
            {"mode": "poster", "t_switch": 300, "t_start": 340},
        ]

    def test_bounds_anchor_at_mode_switch(self):
        bounds, kept = R._framed_segment_bounds(self._per_mode(), 400)
        self.assertEqual(bounds, [(10, 100), (100, 200), (200, 300),
                                  (300, 400)])
        self.assertEqual([d["mode"] for d in kept],
                         ["classic", "one_frame", "titleshot", "poster"])

    def test_poster_gate_inside_poster_chapter(self):
        # The 300->340 poster-gate dead time must NOT air inside the
        # Titleshot chapter (iter-12: it did, ~30s of gate footage).
        bounds, _ = R._framed_segment_bounds(self._per_mode(), 400)
        titleshot = bounds[2]
        self.assertLessEqual(titleshot[1], 300)
        poster = bounds[3]
        self.assertEqual(poster, (300, 400))
        self.assertTrue(poster[0] <= 300 < 340 <= poster[1])

    def test_legacy_t_start_fallback(self):
        legacy = [{"mode": "classic", "t_start": 10},
                  {"mode": "poster", "t_start": 300}]
        bounds, _ = R._framed_segment_bounds(legacy, 400)
        self.assertEqual(bounds, [(10, 300), (300, 400)])

    def test_anchor_prefers_switch(self):
        self.assertEqual(R._framed_anchor({"t_switch": 5, "t_start": 9}), 5)
        self.assertEqual(R._framed_anchor({"t_start": 9}), 9)


class TestFramedBreakdownCounts(unittest.TestCase):
    def test_framed_counts_all_guesses_and_mode_answers(self):
        import daily_parity as DP
        import tempfile
        steps = [{"turn": i + 1, "guess": f"FILM {i}", "pattern": "",
                  "mode": m} for i, m in enumerate(
                      ["Classic", "One Frame", "Titleshot", "Poster"])]
        with tempfile.TemporaryDirectory() as td:
            out = str(Path(td) / "facts.png")
            stats = DP.generate_facts(out, "Framed", "Oct 18, 2026",
                                      "4/4 modes won", steps,
                                      scoring=False,
                                      guesses_played=8, final_answers=4)
        self.assertEqual(stats, [("8", "guesses played"),
                                 ("4", "final answers")])

    def test_single_answer_game_unchanged(self):
        import daily_parity as DP
        import tempfile
        steps = [{"turn": i + 1, "guess": f"GUESS{i}", "pattern": "G...."}
                 for i in range(3)]
        with tempfile.TemporaryDirectory() as td:
            out = str(Path(td) / "facts.png")
            stats = DP.generate_facts(out, "Wordle", "Oct 18, 2026",
                                      "ANSWER", steps, scoring=True)
        self.assertEqual(stats[0], ("3", "guesses played"))
        self.assertEqual(stats[-1], ("1", "final answer"))


if __name__ == "__main__":
    unittest.main()


class TestWorldleLiveBrowserHelpers(unittest.TestCase):
    """Best-evidenced fixes for Capital / Trivia / Language Q1 (items
    6-8): these need live-browser confirmation of the widget DOM —
    the tests cover the Python wrappers + the pure matchers, and
    ANALYSIS10 must flag exactly what still needs live confirmation."""

    def test_shadow_click_dispatch_wrapper(self):
        class _Page:
            def evaluate(self, js, arg):
                self.js = js
                self.arg = arg
                return {"dispatched": True, "tag": "svg",
                        "cls": "", "depth": 1}
        pg = _Page()
        ok, det = R._s_worldle_dispatch_shadow_click(pg, 100.0, 200.0)
        self.assertTrue(ok)
        self.assertIn("svg", det)
        self.assertEqual(pg.arg, {"x": 100.0, "y": 200.0})
        # The JS must pierce shadow roots and dispatch pointer events.
        self.assertIn("shadowRoot", pg.js)
        self.assertIn("pointerdown", pg.js)

    def test_shadow_click_exception_is_false(self):
        class _Bad:
            def evaluate(self, js, arg):
                raise RuntimeError("boom")
        ok, _ = R._s_worldle_dispatch_shadow_click(_Bad(), 1, 2)
        self.assertFalse(ok)

    def test_phrase_slot_contains(self):
        class _Page:
            def __init__(self, val):
                self.val = val

            def evaluate(self, js, arg):
                self.arg = arg
                return self.val
        self.assertTrue(R._worldle_phrase_slot_contains(_Page(True),
                                                        "Nɔɔyɛ la me."))
        self.assertFalse(R._worldle_phrase_slot_contains(_Page(False),
                                                          "Nɔɔyɛ la me."))

    def test_trivia_exact_db_text_match(self):
        # The DB boolean text IS the game's statement: a normalized
        # match must hit (iter-13 Trivia fix — exact match before the
        # token heuristic).
        db = "Has had a female head of state/government"
        shown = "Has had a female head of state / government"
        self.assertTrue(R._s_worldle_trivia_match(db, shown))
        self.assertFalse(R._s_worldle_trivia_match(
            "Is a member of NATO", shown))
