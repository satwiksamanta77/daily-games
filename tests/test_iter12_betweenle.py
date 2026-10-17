"""Fix-loop iteration 12 Betweenle tests.

Covers the ANALYSIS8 §1/§6 regression diagnosis (run 39043936480):
(a) multi-mechanism submit retry chain (dismiss+resend -> canvas sweep
    -> synthetic key dispatch) for the no-banner-change case;
(b) banner BEFORE/AFTER classification — a standing/stale banner alone
    never classifies a word rejected;
(c) pick excludes used ∪ rejected;
(d) seed replay from restored state (used/submitted from restored
    counters/rows before replaying);
(e) true cards/counts: steps + result.json reflect ONLY actually
    submitted guesses; a solved record carries the verified answer,
    never answer=None;
(f) stale 20s MOCHA intro prepend stays removed; typing speed unchanged.
"""
import json
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

import runner as R


class _FakeMouse:
    def __init__(self, page):
        self.page = page
        self.clicks = []

    def click(self, x, y):
        self.clicks.append((x, y))


class _FakeKeyboard:
    def __init__(self, page):
        self.page = page
        self.presses = []

    def press(self, key):
        self.presses.append(key)
        if key == "Enter":
            self.page.enters += 1
            if self.page.enters >= self.page.submit_after:
                self.page.counters["guess_n"] = self.page.counters_before + 1
                self.page.counters["stats_word"] = self.page.word


class _FakePage:
    """Playwright-page stand-in for the Betweenle retry chain / seed."""

    def __init__(self, body="", counters=None, submit_after=99, word="BIKER"):
        self._body = body
        self.counters = dict(counters or {"guess_n": 0, "guess_total": 14,
                                           "stats_word": ""})
        self.counters_before = self.counters.get("guess_n") or 0
        self.submit_after = submit_after
        self.word = word
        self.enters = 0
        self.synthetic_dispatches = 0
        self.mouse = _FakeMouse(self)
        self.keyboard = _FakeKeyboard(self)
        self.evaluated = []

    def evaluate(self, script, arg=None):
        self.evaluated.append(script)
        if "KeyboardEvent" in script:
            # Synthetic Enter delivery: counts as an Enter for the fake.
            self.synthetic_dispatches += 1
            self.enters += 1
            if self.enters >= self.submit_after:
                self.counters["guess_n"] = self.counters_before + 1
                self.counters["stats_word"] = self.word
            return True
        if "statisticspanel-word" in script:
            return dict(self.counters)
        if "getElementById(p)" in script:
            return "missing"
        if "document.body.innerText" in script:
            return self._body
        return None

    def wait_for_timeout(self, ms):
        pass

    def query_selector(self, sel):
        return None


class TestBannerBeforeAfter(unittest.TestCase):
    """(b) Standing banner alone never classifies a word rejected."""

    def test_extract_banner(self):
        got = R._betweenle_extract_banner(
            "Good board. Enter word placed in dictionary before PARTY")
        self.assertIn("party", got)
        self.assertEqual(R._betweenle_extract_banner("Good guess!"), "")

    def test_stale_banner_is_no_change(self):
        banner = R._betweenle_extract_banner(
            "Enter word placed in dictionary before PARTY")
        verdict = R._betweenle_classify_submit(
            banner, banner, 2, {"guess_n": 2, "stats_word": ""}, "BIKER")
        self.assertEqual(verdict, "no_change")

    def test_fresh_banner_is_rejected(self):
        verdict = R._betweenle_classify_submit(
            "", R._betweenle_extract_banner(
                "Enter word placed in dictionary before PARTY"),
            2, {"guess_n": 2, "stats_word": ""}, "VIPER")
        self.assertEqual(verdict, "rejected")

    def test_changed_banner_is_rejected(self):
        before = R._betweenle_extract_banner(
            "Enter word placed in dictionary before PARTY")
        after = R._betweenle_extract_banner(
            "Enter word placed in dictionary before QUART")
        self.assertTrue(R._betweenle_new_rejection(before, after))
        verdict = R._betweenle_classify_submit(
            before, after, 2, {"guess_n": 2, "stats_word": ""}, "VIPER")
        self.assertEqual(verdict, "rejected")

    def test_counter_advance_is_submitted_despite_banner(self):
        banner = R._betweenle_extract_banner(
            "Enter word placed in dictionary before PARTY")
        verdict = R._betweenle_classify_submit(
            banner, banner, 2, {"guess_n": 3, "stats_word": ""}, "DINER")
        self.assertEqual(verdict, "submitted")

    def test_stats_word_is_submitted_winning_guess(self):
        # Winning guess never advances the GUESS counter (ANALYSIS8 §1):
        # the stats panel naming the word is the submit confirmation.
        verdict = R._betweenle_classify_submit(
            "", "", 2, {"guess_n": 2, "stats_word": "BIKER"}, "BIKER")
        self.assertEqual(verdict, "submitted")

    def test_no_banner_no_move_is_no_change(self):
        verdict = R._betweenle_classify_submit(
            "", "", 2, {"guess_n": 2, "stats_word": ""}, "BIKER")
        self.assertEqual(verdict, "no_change")


class TestPickExcludesUsedUnionRejected(unittest.TestCase):
    """(c) Pick excludes used ∪ rejected, not used alone."""

    def test_rejected_word_never_repicked(self):
        got = R._betweenle_pick_excluding(
            ["BIKER", "FIXER"], ["APPLE", "ZEBRA"], (None, None),
            {"BIKER"}, "BIKER")
        self.assertEqual(got, "FIXER")

    def test_used_word_never_repicked(self):
        got = R._betweenle_pick_excluding(
            ["SLATE", "DINER"], ["APPLE"], (None, None),
            {"SLATE"}, "DINER")
        self.assertEqual(got, "DINER")

    def test_all_excluded_returns_none(self):
        got = R._betweenle_pick_excluding(
            ["BIKER"], ["APPLE"], (None, None), {"BIKER", "APPLE"}, "BIKER")
        self.assertIsNone(got)


class TestRetryChain(unittest.TestCase):
    """(a) dismiss+resend -> canvas sweep -> synthetic dispatch, in order."""

    def test_sweep_delivers_after_resend_fails(self):
        page = _FakePage(submit_after=2, word="BIKER")
        submitted, cnt, mech = R._betweenle_submit_retry_chain(
            page, "betweenle", "BIKER", 2)
        self.assertTrue(submitted)
        self.assertEqual(mech, "canvas-sweep")
        self.assertEqual(cnt.get("guess_n"), 1)

    def test_first_resend_delivers(self):
        page = _FakePage(submit_after=1, word="BIKER")
        submitted, _cnt, mech = R._betweenle_submit_retry_chain(
            page, "betweenle", "BIKER", 2)
        self.assertTrue(submitted)
        self.assertEqual(mech, "dismiss+resend")

    def test_exhausted_chain_reaches_synthetic_and_fails(self):
        page = _FakePage(submit_after=99, word="BIKER")
        submitted, _cnt, mech = R._betweenle_submit_retry_chain(
            page, "betweenle", "BIKER", 2)
        self.assertFalse(submitted)
        self.assertIsNone(mech)
        self.assertGreaterEqual(page.synthetic_dispatches, 1,
                                "chain must reach synthetic dispatch")
        self.assertGreaterEqual(len(page.mouse.clicks), 6,
                                "chain must attempt a canvas sweep")

    def test_synthetic_dispatch_script(self):
        js = R._BETWEENLE_SYNTHETIC_ENTER_JS
        self.assertIn("KeyboardEvent", js)
        self.assertIn("Enter", js)
        self.assertIn("keydown", js)


class TestRestoreSeed(unittest.TestCase):
    """(d) Seed replay from restored state before replaying."""

    def setUp(self):
        self._played = dict(R._PLAYED)
        self._rejected = dict(R._REJECTED)

    def tearDown(self):
        R._PLAYED.clear()
        R._PLAYED.update(self._played)
        R._REJECTED.clear()
        R._REJECTED.update(self._rejected)

    def test_seed_from_ledgers_and_stats_panel(self):
        R._PLAYED["betweenle"] = ["SLATE"]
        # Legacy iter-11 suffix must be cleaned to the plain word.
        R._REJECTED["betweenle"] = ["VIPER (not submitted)"]
        page = _FakePage(counters={"guess_n": 3, "guess_total": 14,
                                    "stats_word": "DINER"})
        used, submitted, rejected = R._betweenle_seed_restored_state(
            page, "betweenle")
        self.assertEqual(submitted, ["SLATE", "DINER"])
        self.assertEqual(rejected, ["VIPER"])
        self.assertEqual(used, {"SLATE", "DINER", "VIPER"})
        self.assertEqual(R._REJECTED["betweenle"], ["VIPER"])

    def test_seed_empty_on_fresh_board(self):
        R._PLAYED.pop("betweenle", None)
        R._REJECTED.pop("betweenle", None)
        page = _FakePage(counters={"guess_n": 0, "guess_total": 14,
                                    "stats_word": ""})
        used, submitted, rejected = R._betweenle_seed_restored_state(
            page, "betweenle")
        self.assertEqual((used, submitted, rejected), (set(), [], []))


class TestSubmittedOnlyStepsAndAnswer(unittest.TestCase):
    """(e) Cards/counts use submitted-only; solved carries the answer."""

    def setUp(self):
        self._played = dict(R._PLAYED)
        self._rejected = dict(R._REJECTED)

    def tearDown(self):
        R._PLAYED.clear()
        R._PLAYED.update(self._played)
        R._REJECTED.clear()
        R._REJECTED.update(self._rejected)

    def test_steps_use_submitted_only(self):
        R._PLAYED["betweenle"] = ["SLATE", "DINER", "BIKER"]
        steps = R._result_steps("betweenle", "BIKER")
        self.assertEqual([s["guess"] for s in steps],
                         ["SLATE", "DINER", "BIKER"])
        self.assertTrue(all(s.get("pattern") == "" for s in steps),
                        "no fabricated letter scoring on Betweenle steps")

    def test_steps_never_fall_back_to_plan(self):
        R._PLAYED.pop("betweenle", None)
        # Planned words (SLATE/DINER/VIPER/FIXER/HIKER/BIKER) must never
        # appear as steps when nothing was confirmed submitted.
        self.assertEqual(R._result_steps("betweenle", "BIKER"), [])

    def test_preliminary_result_carries_verified_answer(self):
        gid = "test-iter12-betweenle-prelim"
        try:
            R._PLAYED[gid] = ["SLATE"]
            R._write_preliminary_result(gid, "2026-10-17", True,
                                        "solved evidence", answer="BIKER")
            doc = json.loads((R.HERE / "videos" / gid
                              / "result.json").read_text())
            self.assertEqual(doc["answer"], "BIKER")
            self.assertEqual(doc["guesses"], ["SLATE"])
            R._write_preliminary_result(gid, "2026-10-17", False,
                                        "unsolved", answer="BIKER")
            doc = json.loads((R.HERE / "videos" / gid
                              / "result.json").read_text())
            self.assertIsNone(doc["answer"],
                              "unsolved record must not claim an answer")
        finally:
            shutil.rmtree(R.HERE / "videos" / gid, ignore_errors=True)


class TestIntroRemovedAndTypingSpeed(unittest.TestCase):
    """(f) Stale MOCHA intro stays removed; typing speed unchanged."""

    def test_no_intro_clip_in_runner(self):
        src = (Path(__file__).parent.parent / "runner.py").read_text()
        self.assertIn("_intro_dur = 0.0", src)
        for line in src.splitlines():
            stripped = line.strip()
            if "wordsolverx-intro" in line:
                self.assertTrue(stripped.startswith("#"),
                                f"intro file referenced in code: {line!r}")
            self.assertFalse(
                ("wordsolverx-intro" in line
                 and ("VideoFileClip" in line or "AudioFileClip" in line)),
                f"intro clip loaded: {line!r}")

    def test_betweenle_solver_wires_new_paths(self):
        src = (Path(__file__).parent.parent / "runner.py").read_text()
        self.assertIn("_betweenle_submit_retry_chain(page, gid, g, _cnt_before)",
                      src)
        self.assertIn("_betweenle_pick_excluding(gs[gi:], _bank, _bd,",
                      src)
        self.assertIn("_betweenle_seed_restored_state(page, gid)", src)
        self.assertIn("_betweenle_rejection_banner(page)", src)
        self.assertNotIn(
            '_rejected_bl.append(str(g).upper() + " (not submitted)")', src,
            "rejected ledger must hold plain words, not suffixed labels")

    def test_typing_speed_unchanged(self):
        src = (Path(__file__).parent.parent / "runner.py").read_text()
        self.assertIn("_type_like_a_person(page, str(g), base_delay=170)",
                      src)


if __name__ == "__main__":
    unittest.main()
