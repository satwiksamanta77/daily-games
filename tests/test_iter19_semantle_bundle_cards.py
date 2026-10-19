"""Fix-loop iteration 19 tests — Semantle + Bundle + Cards workstream.

Covers ANALYSIS16 §2(i), §5, §6 items 9, 11, 12, 15, §7 P1/P2/P3:
1. Semantle real live-day signal (P1): the URL probe captures nothing
   on semantle.com, but the site's own displayed header "Game #N" IS a
   live-day signal from the game host (N -> 2022-01-30 + N days). When
   the URL probe is empty, _align_answer_to_live_day now consults the
   displayed number under the same +/-1-day bound. When neither source
   yields a date it says so honestly — no faked signal.
2. Bundle excludes unsolved gameplay (P1): the workflow filter now
   drops EVERY mp4 in a solved=False dir from the staging copy, and a
   fail-closed verification step fails the job if any survives.
3. Answer-card truncation (P2): no fixed-char slices in card renderers;
   full guess/hint/answer text is drawn (auto-shrink / wrap).
4. Metadata warts (P3): no placeholder chapters for unsolved games;
   the Teuteuf banner probe never runs/labels for a known non-Worldle
   game (the stray "teuteuf edu banner" line in the waffle log).
"""
import json
import shutil
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

import daily_parity as DP
import runner as R

HERE = Path(__file__).parent.parent
WF = (HERE / ".github/workflows/daily-all-videos.yml").read_text()
SRC_RUNNER = (HERE / "runner.py").read_text()

TARGET = date(2026, 10, 18)


class _FakeAnswers:
    """answers-module stand-in: target date + Semantle epoch maths."""

    def __init__(self, target=TARGET):
        self._target = target

    def target_date(self):
        return self._target

    def semantle(self, d):
        idx = (d - date(2022, 1, 30)).days
        return {"answer": f"word-{idx}", "puzzle": idx, "date": str(d)}


class _FakePage:
    """Minimal Playwright-page stand-in."""

    def __init__(self, body="", gid=None, url=None):
        self._body = body
        if gid is not None:
            self._qp_gid = gid
        if url is not None:
            self.url = url
        self.calls = []
        self.waits = []

    def evaluate(self, js, *a):
        self.calls.append(js)
        return self._body

    def wait_for_timeout(self, ms):
        self.waits.append(ms)


def _dbg_recorder(logs):
    def _rec(gid, msg):
        logs.append(f"[{gid}] {msg}")
    return _rec


# ------------------------------------------------------- Semantle signal


class TestSemantleDisplayedSignal(unittest.TestCase):
    def setUp(self):
        self._orig_a = R.A
        self._orig_live = dict(R._LIVE_DAY)
        self._orig_dbg = R._dbg
        R.A = _FakeAnswers(TARGET)
        R._LIVE_DAY.clear()
        self.logs = []
        R._dbg = _dbg_recorder(self.logs)

    def tearDown(self):
        R.A = self._orig_a
        R._LIVE_DAY.clear()
        R._LIVE_DAY.update(self._orig_live)
        R._dbg = self._orig_dbg

    def _ans(self):
        return {"answer": "cry", "puzzle": 1722, "date": "2026-10-18"}

    def test_puzzle_number_maps_to_date(self):
        self.assertEqual(R._semantle_date_from_puzzle_number(1722),
                         date(2026, 10, 18))
        self.assertEqual(R._semantle_date_from_puzzle_number(0),
                         date(2022, 1, 30))
        self.assertIsNone(R._semantle_date_from_puzzle_number(-1))
        self.assertIsNone(R._semantle_date_from_puzzle_number("abc"))

    def test_displayed_target_day_keeps_target(self):
        ans = self._ans()
        page = _FakePage("Semantle\nGame #1722\n")
        out, served = R._align_answer_to_live_day(
            "semantle", ans, page)
        self.assertIs(out, ans)
        self.assertIsNone(served)
        self.assertTrue(any("Game #1722" in m for m in self.logs))

    def test_displayed_plus_one_day_rekeys(self):
        page = _FakePage("Semantle\nGame #1723\n")
        out, served = R._align_answer_to_live_day(
            "semantle", self._ans(), page)
        self.assertEqual(out["answer"], "word-1723")
        self.assertEqual(str(served), "2026-10-19")
        self.assertTrue(any("displayed Game #1723" in m
                            for m in self.logs))
        self.assertTrue(any("semantle.com" in m for m in self.logs))

    def test_displayed_minus_one_day_rekeys_for_defer(self):
        page = _FakePage("Semantle\nGame #1721\n")
        out, served = R._align_answer_to_live_day(
            "semantle", self._ans(), page)
        self.assertEqual(out["answer"], "word-1721")
        self.assertEqual(str(served), "2026-10-17")

    def test_displayed_far_jump_refused(self):
        ans = self._ans()
        page = _FakePage("Semantle\nGame #1700\n")
        out, served = R._align_answer_to_live_day(
            "semantle", ans, page)
        self.assertIs(out, ans)
        self.assertIsNone(served)
        self.assertTrue(any("RE-KEY REFUSED" in m for m in self.logs))

    def test_no_signal_when_neither_source(self):
        ans = self._ans()
        page = _FakePage("Semantle\nno number here\n")
        out, served = R._align_answer_to_live_day(
            "semantle", ans, page)
        self.assertIs(out, ans)
        self.assertIsNone(served)
        self.assertTrue(any("no live-day signal" in m
                            for m in self.logs))
        self.assertTrue(any("no displayed Game #" in m
                            for m in self.logs))

    def test_no_signal_without_page_unchanged(self):
        # Old behaviour preserved: no page, no probe -> honest log.
        ans = self._ans()
        out, served = R._align_answer_to_live_day("semantle", ans)
        self.assertIs(out, ans)
        self.assertIsNone(served)
        self.assertTrue(any("no live-day signal" in m
                            for m in self.logs))

    def test_doubleverify_url_never_becomes_signal(self):
        # The iter-17 pollution vector, seeded directly: the ad host
        # must be dropped and the displayed number used instead.
        dv = ("https://tpsc-video-ue.doubleverify.com/visit.js"
              "?dvtagver=dvot_2026-10-14_x")
        R._LIVE_DAY["hosts"] = {
            "tpsc-video-ue.doubleverify.com": "2026-10-14"}
        R._LIVE_DAY["urls"] = {
            "tpsc-video-ue.doubleverify.com": dv}
        ans = self._ans()
        page = _FakePage("Semantle\nGame #1722\n")
        out, served = R._align_answer_to_live_day(
            "semantle", ans, page)
        self.assertIs(out, ans)
        self.assertIsNone(served)
        self.assertFalse(any("doubleverify" in m for m in self.logs),
                         "ad URL must never appear in the decision")

    def test_displayed_signal_not_used_for_other_games(self):
        # The Game # fallback is Semantle-only: another game with a
        # "Game #..." page must not re-key off it.
        page = _FakePage("Game #1723\n")
        out, served = R._align_answer_to_live_day(
            "contexto", {"answer": "section"}, page)
        self.assertEqual(out, {"answer": "section"})
        self.assertIsNone(served)
        self.assertFalse(any("displayed Game #" in m
                             for m in self.logs))


# ------------------------------------------------------- Bundle


class TestBundleExcludesUnsolvedGameplay(unittest.TestCase):
    def test_workflow_drops_every_mp4_in_unsolved_dirs(self):
        self.assertIn("Exclude unsolved games' finals", WF)
        self.assertIn("*.mp4", WF)
        self.assertIn("Verify no unsolved video survived", WF)
        self.assertIn("sys.exit(1)", WF)
        self.assertIn("gameplay_1080.mp4", WF)

    def test_filter_logic_on_fixture_tree(self):
        # Replicate the workflow's filter + verification on a fixture
        # tree shaped like the iter-18 bundle (unsolved nerdle +
        # worldle with gameplay, solved waffle with final + gameplay,
        # unsolved dir also holding a quarantined file).
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "all-artifacts"
            cases = (("nerdle", False), ("worldle", False),
                     ("waffle", True))
            for game, solved in cases:
                d = root / f"video-{game}"
                d.mkdir(parents=True)
                (d / "result.json").write_text(
                    json.dumps({"solved": solved}))
                (d / "gameplay_1080.mp4").write_text("fake")
                if not solved:
                    (d / f"{game}_final_2026-10-18.mp4").write_text("fake")
                    (d / f"{game}_WRONGDAY_BLOCKED_2026-10-18.mp4"
                     ).write_text("fake")
                else:
                    (d / f"{game}_final_2026-10-18.mp4").write_text("fake")
            # Filter: every mp4 in a solved=False dir goes.
            for res in sorted(root.glob("video-*/result.json")):
                if json.loads(res.read_text()).get("solved") is False:
                    for mp4 in sorted(res.parent.glob("*.mp4")):
                        mp4.unlink()
            # Verification: fail-closed if anything survived.
            leaked = []
            for res in sorted(root.glob("video-*/result.json")):
                if json.loads(res.read_text()).get("solved") is False:
                    leaked.extend(sorted(res.parent.glob("*.mp4")))
            self.assertEqual(leaked, [])
            self.assertFalse(
                (root / "video-nerdle/gameplay_1080.mp4").exists())
            self.assertFalse(
                (root / "video-worldle/gameplay_1080.mp4").exists())
            self.assertFalse(
                (root / "video-nerdle/nerdle_WRONGDAY_BLOCKED_2026-10-18.mp4"
                 ).exists())
            # Solved games keep finals AND solved gameplay.
            self.assertTrue(
                (root / "video-waffle/waffle_final_2026-10-18.mp4"
                 ).exists())
            self.assertTrue(
                (root / "video-waffle/gameplay_1080.mp4").exists())

    def test_verification_catches_a_leak(self):
        # The verification predicate must flag an unsolved dir that
        # still holds an mp4 (i.e. the filter stopped deleting).
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "all-artifacts"
            d = root / "video-nerdle"
            d.mkdir(parents=True)
            (d / "result.json").write_text(json.dumps({"solved": False}))
            (d / "gameplay_1080.mp4").write_text("fake")
            leaked = []
            for res in sorted(root.glob("video-*/result.json")):
                if json.loads(res.read_text()).get("solved") is False:
                    leaked.extend(sorted(res.parent.glob("*.mp4")))
            self.assertEqual(len(leaked), 1)


# ------------------------------------------------------- Cards


class _RecDraw:
    """ImageDraw stand-in recording every text() call."""

    def __init__(self, img, texts, real_draw_cls):
        self._d = real_draw_cls(img)
        self._texts = texts

    def text(self, xy, s, **k):
        self._texts.append(str(s))
        return self._d.text(xy, s, **k)

    def __getattr__(self, name):
        return getattr(self._d, name)


class TestCardTruncation(unittest.TestCase):
    def _render_dp(self, fn, *args, **kw):
        tmp = tempfile.mkdtemp(prefix="iter19-card-")
        try:
            texts = []
            real_draw = DP.ImageDraw.Draw
            out = Path(tmp) / "card.png"
            with mock.patch.object(
                    DP.ImageDraw, "Draw",
                    side_effect=lambda img: _RecDraw(img, texts,
                                                     real_draw)):
                fn(str(out), *args, **kw)
            return out.exists(), texts
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_reveal_draws_full_long_guesses(self):
        steps = [
            {"guess": "SAINT KITTS AND NEVIS", "turn": 2,
             "pattern": "G" * 21},
            {"guess": "LIVING ON BORROWED TIME", "turn": 1,
             "pattern": "G" * 22},
        ]
        exists, texts = self._render_dp(
            DP.generate_reveal, "Countryle", "Oct 18, 2026",
            "BOTSWANA", steps, scoring=True)
        self.assertTrue(exists)
        joined = " ".join(texts)
        self.assertIn("SAINT KITTS AND NEVIS", joined)
        self.assertIn("LIVING ON BORROWED TIME", joined)
        # No drawn text may BE the truncated form (the full string
        # contains it as a substring, so check whole-call equality).
        self.assertNotIn("SAINT KITTS AND NEVI", texts)
        self.assertNotIn("LIVING ON BORROWED T", texts)

    def test_facts_draws_full_long_guesses(self):
        steps = [{"guess": "SAINT KITTS AND NEVIS", "turn": 2,
                  "pattern": "G" * 21}]
        exists, texts = self._render_dp(
            DP.generate_facts, "Countryle", "Oct 18, 2026",
            "BOTSWANA", steps, scoring=True)
        self.assertTrue(exists)
        self.assertIn("SAINT KITTS AND NEVIS", " ".join(texts))
        self.assertNotIn("SAINT KITTS AND NEVI", texts)

    def test_hints_draw_full_long_hint(self):
        hint = ("This is a very long hint that certainly exceeds "
                "sixty characters in length for sure")
        exists, texts = self._render_dp(
            DP.generate_hints, "Countryle", "Oct 18, 2026",
            [hint], "BOTSWANA")
        self.assertTrue(exists)
        self.assertIn(hint, " ".join(texts))

    def test_reveal_long_answer_tiles_do_not_raise(self):
        steps = [{"guess": "LIVING ON BORROWED TIME", "turn": 1,
                  "pattern": "G" * 22}]
        exists, _texts = self._render_dp(
            DP.generate_reveal, "Phrazle", "Oct 18, 2026",
            "LIVING ON BORROWED TIME", steps, scoring=True)
        self.assertTrue(exists)

    def test_no_fixed_char_slices_in_card_renderers(self):
        # Source-level guard: the code-form slices that truncated
        # card text must be gone from the guess/clue drawing paths
        # (comments may still name the old slices).
        dp_src = Path(DP.__file__).read_text()
        for bad in ("upper()[:20]", "upper()[:24]", "str(h)[:60]",
                    "kind)[:64]", "kind=kind)[:70]"):
            self.assertNotIn(bad, dp_src)

    def test_runner_card_wraps_full_line(self):
        tmp = tempfile.mkdtemp(prefix="iter19-runner-card-")
        try:
            texts = []
            real_draw = R.ImageDraw.Draw
            out = Path(tmp) / "card.png"
            long_line = ("SAINT KITTS AND NEVIS is a long line that "
                         "exceeds eighty characters in total length here")
            with mock.patch.object(
                    R.ImageDraw, "Draw",
                    side_effect=lambda img: _RecDraw(img, texts,
                                                     real_draw)):
                R.card(str(out), "Countryle", "Title",
                       [long_line], "Oct 18, 2026")
            self.assertTrue(out.exists())
            self.assertIn(long_line, " ".join(texts))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_runner_thumb_draws_partial_answer_only(self):
        # Iteration 37 REPLACED the old contract (full answer drawn on
        # the thumbnail) with a curiosity gap: first 2 letters
        # revealed as single-letter tiles, the rest '?'. The full
        # answer lives in the video + description, NOT the thumbnail.
        tmp = tempfile.mkdtemp(prefix="iter19-thumb-")
        try:
            texts = []
            real_draw = R.ImageDraw.Draw
            out = Path(tmp) / "thumb.png"
            ans = ("LIVING ON BORROWED TIME AND MORE WORDS HERE "
                   "TO EXCEED THIRTY FOUR CHARS")
            with mock.patch.object(
                    R.ImageDraw, "Draw",
                    side_effect=lambda img: _RecDraw(img, texts,
                                                     real_draw)):
                R.thumb(str(out), "Phrazle", "Oct 18, 2026", ans,
                        puzzle_number=123)
            self.assertTrue(out.exists())
            joined = " ".join(texts)
            self.assertNotIn(ans, joined)
            self.assertNotIn(ans[:34], texts)
            # Curiosity-gap tiles: first 2 letters as single draws,
            # '?' tiles for the rest.
            self.assertIn("L", texts)
            self.assertIn("I", texts)
            self.assertIn("?", texts)
            self.assertIn("#123", joined)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------- Metadata


class TestMetadataPlaceholders(unittest.TestCase):
    def test_no_invented_nerdle_chapters(self):
        # The all-[0, mode] fallback is gone: no chapters recorded
        # means no chapters emitted.
        self.assertNotIn("[(0, str(m.get(\"id\")))", SRC_RUNNER)

    def test_unsolved_chapters_omitted(self):
        self.assertIn("chapters = []", SRC_RUNNER)
        self.assertIn("chapters = [] if not solved", SRC_RUNNER)

    def test_embed_chapters_tolerates_empty(self):
        with tempfile.TemporaryDirectory() as td:
            out = R._embed_chapters(str(Path(td) / "x.mp4"), [])
            self.assertFalse(out)


class TestTeuteufProbeScopedToWorldle(unittest.TestCase):
    def setUp(self):
        self._orig_dbg = R._dbg
        self.logs = []
        R._dbg = _dbg_recorder(self.logs)

    def tearDown(self):
        R._dbg = self._orig_dbg

    def test_waffle_page_never_probed_or_logged(self):
        page = _FakePage("absent", gid="waffle",
                         url="https://waffle.example.com/")
        R._dismiss_teuteuf_banner(page)
        self.assertEqual(page.calls, [])
        self.assertFalse(any("teuteuf edu banner" in m
                             for m in self.logs))

    def test_worldle_page_still_probed(self):
        page = _FakePage("absent", gid="worldle",
                         url="https://worldle.teuteuf.fr/")
        R._dismiss_teuteuf_banner(page)
        self.assertEqual(len(page.calls), 2)  # initial + re-scan
        self.assertTrue(any("teuteuf edu banner: absent" in m
                            for m in self.logs))

    def test_non_teuteuf_url_skips_probe(self):
        page = _FakePage("absent", gid="test-iter19",
                         url="https://example.com/")
        R._dismiss_teuteuf_banner(page)
        self.assertEqual(page.calls, [])

    def test_unknown_game_still_probed(self):
        # Direct calls / test doubles with no known game id keep the
        # old behaviour (preserves the iter-16/17/18 banner tests).
        page = _FakePage("absent", gid="test-iter19")
        R._dismiss_teuteuf_banner(page)
        self.assertEqual(len(page.calls), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
