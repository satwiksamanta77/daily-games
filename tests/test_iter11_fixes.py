"""Fix-loop iteration 11 regression tests.

Covers: no-scoring cards (Betweenle/Framed/Semantle/Contexto),
semantic dedupe helpers + caps, Framed Classic rename, Batterup
visible-exact-cells probe, early result.json, Countryle retry /
validation / defer, Worldle live-round raw mapping, area display
reader (no max-over-container), Betweenle bounds + rejection,
autocrop typing windows, Nerdle chapter starts.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

import answers as A
import daily_parity as DP
import runner as R
import nerdle_solver as NS


class _FakePage:
    """Minimal Playwright-page stand-in: evaluate returns scripted data."""

    def __init__(self, scripted=None, body=""):
        self._scripted = scripted or {}
        self._body = body
        self._calls = []

    def evaluate(self, script, arg=None):
        self._calls.append(script)
        # Iter-19: the G4 scan JS takes (labels) as its arg, so a
        # bare "labels" substring match would return the toggle's
        # bool for the scan too — dispatch the scan marker first.
        if "G4-LIVE-ROUNDS-SCAN" in script and \
                "const out" in self._scripted:
            val = self._scripted["const out"]
            return val() if callable(val) else val
        for key, val in self._scripted.items():
            if key in script:
                return val() if callable(val) else val
        if "document.body.innerText" in script:
            return self._body
        return None

    def wait_for_timeout(self, ms):
        pass


class TestNoScoringCards(unittest.TestCase):
    """A1: Betweenle cards must NEVER render letter scoring."""

    def _render(self, fn, scoring, **kw):
        tmp = tempfile.mkdtemp(prefix="iter11-card-")
        try:
            texts = []
            real_draw = DP.ImageDraw.Draw

            class _Rec:
                def __init__(self, img):
                    self._d = real_draw(img)

                def text(self, xy, s, **k):
                    texts.append(str(s))
                    return self._d.text(xy, s, **k)

                def __getattr__(self, name):
                    return getattr(self._d, name)

            clue_calls = []
            orig_clue = DP._clue_line
            out = Path(tmp) / "card.png"
            with mock.patch.object(DP.ImageDraw, "Draw",
                                   side_effect=lambda img: _Rec(img)):
                with mock.patch.object(
                        DP, "_clue_line",
                        side_effect=lambda *a, **k: clue_calls.append(a)
                        or orig_clue(*a, **k)):
                    steps = [{"guess": "CRANE", "turn": 1, "pattern": "XXYXX"},
                             {"guess": "GATOR", "turn": 2, "pattern": "GGGGG"}]
                    fn(str(out), "Betweenle", "Oct 17", "GATOR",
                       steps, scoring=scoring, **kw)
            exists = out.exists()
            return exists, texts, clue_calls
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_reveal_no_scoring(self):
        exists, texts, clue_calls = self._render(DP.generate_reveal, False)
        self.assertTrue(exists)
        self.assertEqual(clue_calls, [],
                         "scoring=False must never call _clue_line")
        joined = " ".join(texts)
        self.assertIn("GUESSES PLAYED", joined)
        self.assertNotIn("HOW EACH GUESS SCORED", joined)
        self.assertNotIn("in the word", joined)
        self.assertNotIn("wrong spot", joined)

    def test_reveal_scoring_still_works(self):
        exists, texts, clue_calls = self._render(DP.generate_reveal, True)
        self.assertTrue(exists)
        self.assertTrue(clue_calls, "scoring=True still renders clues")

    def test_facts_no_scoring(self):
        exists, texts, clue_calls = self._render(DP.generate_facts, False)
        self.assertTrue(exists)
        self.assertEqual(clue_calls, [])
        joined = " ".join(texts)
        self.assertNotIn("green rows", joined)
        self.assertNotIn("in the word", joined)

    def test_no_scoring_set(self):
        self.assertIn("betweenle", R._NO_LETTER_SCORING)
        self.assertIn("framed", R._NO_LETTER_SCORING)
        self.assertIn("semantle", R._NO_LETTER_SCORING)
        self.assertIn("contexto", R._NO_LETTER_SCORING)


class TestSemanticDedupeAndCap(unittest.TestCase):
    """D: dedupe guard + hard cap helpers."""

    def test_closeness(self):
        self.assertEqual(R._semantic_closeness("semantle", 30), "high")
        self.assertEqual(R._semantic_closeness("semantle", 3), "low")
        self.assertIsNone(R._semantic_closeness("semantle", 15))
        self.assertEqual(R._semantic_closeness("contexto", 100), "high")
        self.assertEqual(R._semantic_closeness("contexto", 9000), "low")
        self.assertIsNone(R._semantic_closeness("contexto", 2000))

    def test_feedback_reorder_high_prefers_shared_stem(self):
        spare = ["DISTANT", "SHARE", "SHARING", "FARAWAY"]
        out = R._semantic_feedback_reorder(spare, "SHARK", "semantle", 30)
        self.assertEqual(out[0], "SHARE")

    def test_feedback_reorder_low_prefers_distant(self):
        spare = ["CLOSE", "CLOSER", "FARAWAY", "REMOTE"]
        out = R._semantic_feedback_reorder(spare, "CLOSE", "semantle", 2)
        self.assertIn(out[0], ("FARAWAY", "REMOTE"))

    def test_caps(self):
        self.assertEqual(R._SEMANTIC_MAX_TYPED, 5)
        self.assertEqual(R._SEMANTIC_MAX_PROBES, 4)


class TestFramedClassicRename(unittest.TestCase):
    """C1: internal id/display is 'classic', never 'daily'."""

    def test_runner_modes(self):
        ids = [m[0] for m in R.FRAMED_MODES]
        self.assertIn("classic", ids)
        self.assertNotIn("daily", ids)
        for _id, display, _url in R.FRAMED_MODES:
            self.assertNotIn("Daily", display)
            self.assertNotIn("daily", display.lower().replace("daily", "")
                             if False else display)

    def test_answers_modes(self):
        self.assertIn("classic", A.FRAMED_MODES)
        self.assertNotIn("daily", A.FRAMED_MODES)
        # API gameType mapping stays "daily" (explicit, documented).
        self.assertEqual(A.FRAMED_MODES["classic"][0], "daily")
        self.assertIn("classic", A.FRAMED_URLS)
        self.assertNotIn("daily", A.FRAMED_URLS)
        self.assertEqual(A.framed(date(2026, 5, 29), mode="classic")["mode"],
                         "classic")


class TestBatterupVisibleExactCells(unittest.TestCase):
    """B: exact_n comes from visible populated rows only."""

    def test_probe_passthrough(self):
        canned = {"rows": ["Wyatt Langford | ..."],
                  "dropdown_open": False, "populated": True,
                  "populated_rows": 1, "feedback_tiles": 3,
                  "visible_exact_cells": 3}
        page = _FakePage({"const vis": canned})
        got = R._attr_post_submit_probe(page)
        self.assertEqual(got.get("visible_exact_cells"), 3)
        self.assertEqual(got.get("populated_rows"), 1)


class TestPreliminaryResult(unittest.TestCase):
    """H1: result.json written immediately after the solver returns."""

    def test_write(self):
        gid = "test-iter11-prelim"
        try:
            R._write_preliminary_result(gid, "2026-10-17", False,
                                        "test evidence")
            p = R.HERE / "videos" / gid / "result.json"
            self.assertTrue(p.exists())
            doc = json.loads(p.read_text())
            self.assertEqual(doc["solved"], False)
            self.assertEqual(doc["qc"], {"pending": True})
            self.assertIsNone(doc["video"])
        finally:
            shutil.rmtree(R.HERE / "videos" / gid, ignore_errors=True)


class TestCountryleRetry(unittest.TestCase):
    """I: retry/backoff, validation, immediate defer on no-country."""

    def _stub(self, responses):
        calls = []

        class Resp:
            def __init__(self, status, payload):
                self.status_code = status
                self._payload = payload
                self.ok = 200 <= status < 300
                self.content = json.dumps(payload).encode()

            def json(self):
                return self._payload

        def _get(*a, **k):
            calls.append(a)
            spec = responses[min(len(calls) - 1, len(responses) - 1)]
            if isinstance(spec, Exception):
                raise spec
            return Resp(*spec)

        return _get, calls

    def test_no_country_defers_immediately(self):
        get, calls = self._stub([(200, {"status": "ok"})])
        with mock.patch("requests.get", get):
            with self.assertRaises(SystemExit):
                A.countryle(date(2099, 1, 1))
        self.assertEqual(len(calls), 1,
                         "200 valid JSON, no country => defer at once")

    def test_retry_then_give_up(self):
        get, calls = self._stub([(500, {})])
        with mock.patch("requests.get", get):
            with mock.patch("time.sleep", lambda *a, **k: None):
                with self.assertRaises(SystemExit):
                    A.countryle(date(2099, 1, 1))
        # 3 sources x up to 3 attempts each.
        self.assertEqual(len(calls), 9)

    def test_invalid_payload_rejected(self):
        get, calls = self._stub([(200, {"country": "garbage-not-crypto"})])
        with mock.patch("requests.get", get):
            with mock.patch("time.sleep", lambda *a, **k: None):
                with self.assertRaises(SystemExit):
                    A.countryle(date(2099, 1, 1))
        self.assertGreaterEqual(len(calls), 2,
                                 "undecryptable payload must not be accepted")


class TestWorldleLiveRounds(unittest.TestCase):
    """G4: raw dropdown entries survive to Python; unmapped split."""

    def test_raw_unmapped(self):
        raw = ["Country", "Capital", "Mystery Round", "Close"]
        page = _FakePage({"labels": True, "const out": raw})
        got = R._s_worldle_live_round_modes(page, "worldle")
        self.assertIsNotNone(got)
        self.assertIn("country", got["mapped"])
        self.assertIn("capital", got["mapped"])
        self.assertIn("Mystery Round", got["unmapped"])
        self.assertIn("Mystery Round", got["raw"])
        self.assertNotIn("Close", got["unmapped"], "chrome is filtered")

    def test_dropdown_unreadable(self):
        page = _FakePage({"labels": False})
        self.assertIsNone(R._s_worldle_live_round_modes(page, "worldle"))


class TestAreaDisplayReader(unittest.TestCase):
    """G3: reader returns None on ambiguity, never max() distractor."""

    def test_between_wins(self):
        v, src = R._worldle_area_parse_display(
            {"between": "111369 km2", "common": "111369 998877"})
        self.assertEqual(v, 111369)
        self.assertTrue(src.startswith("between"))

    def test_ambiguous_common_returns_none(self):
        v, src = R._worldle_area_parse_display(
            {"common": "Area 111369 km2 and 998877 km2"})
        self.assertIsNone(v, "multiple candidates => unknown, not max()")

    def test_single_pure(self):
        v, src = R._worldle_area_parse_display({"pure": "54321"})
        self.assertEqual(v, 54321)

    def test_body_and_store_ignored(self):
        v, src = R._worldle_area_parse_display(
            {"body": "999999", "store": "888888"})
        self.assertIsNone(v)


class TestBetweenleBounds(unittest.TestCase):
    """A2/A3: bounds from live game incl. rejection banner."""

    def test_bound_rows(self):
        page = _FakePage(body="Secret word is placed in dictionary "
                         "between LEVEL and SLATE")
        self.assertEqual(R._betweenle_bounds(page), ("LEVEL", "SLATE"))

    def test_rejection_banner_hi(self):
        page = _FakePage(body="Enter word placed in dictionary "
                         "before PARTY")
        self.assertEqual(R._betweenle_bounds(page), (None, "PARTY"))

    def test_rejection_banner_lo(self):
        page = _FakePage(body="Enter word placed in dictionary "
                         "after CRANE")
        self.assertEqual(R._betweenle_bounds(page), ("CRANE", None))

    def test_rejected_showing(self):
        page = _FakePage(body="Enter word placed in dictionary "
                         "before PARTY")
        self.assertTrue(R._betweenle_rejected_showing(page))
        page2 = _FakePage(body="Good guess!")
        self.assertFalse(R._betweenle_rejected_showing(page2))

    def test_pick_in_bounds_none_side(self):
        got = R._betweenle_pick_in_bounds(["GATOR"], ["APPLE", "ZEBRA"],
                                          (None, "PARTY"), set(), "GATOR")
        self.assertEqual(got, "GATOR")


class TestAutocropTypingWindows(unittest.TestCase):
    """A4: typing windows anchor the leading cut + protect static runs."""

    def test_leading_cut_from_earliest_window(self):
        import subprocess
        import quordle_parity as QP
        tmp = tempfile.mkdtemp(prefix="iter11-autocrop-")
        try:
            vid = str(Path(tmp) / "black.mp4")
            r = subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                 "-i", "color=c=black:s=320x180:d=10",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", vid],
                capture_output=True, text=True, timeout=60)
            if r.returncode != 0 or not Path(vid).exists():
                self.skipTest("ffmpeg black-frame fixture unavailable")
            segs, detail = QP._autocrop_keep_segments(
                vid, typing_windows=[(3.0, 5.0), (7.0, 8.0)])
            self.assertAlmostEqual(detail["leading_cut_s"], 2.0, delta=0.2)
            self.assertEqual(detail["leading_cut_source"],
                             "earliest typing window -1s")
            self.assertTrue(segs and abs(segs[0][0] - 2.0) < 0.2)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestNerdleChapters(unittest.TestCase):
    """F: chapters accumulate over emitted (card_dur, seg_dur)."""

    def test_card_starts(self):
        starts = NS.compute_nerdle_chapter_starts(
            [(2.5, 30.0), (2.5, 40.0), (2.5, 25.0)])
        self.assertEqual(starts, [0.0, 32.5, 75.0])


if __name__ == "__main__":
    unittest.main()
