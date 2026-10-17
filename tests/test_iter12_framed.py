"""Fix-loop iteration 12 regression tests — slice FRAMED + SEARCHLE + COUNTRYLE.

Covers:
- Framed titleshot root cause: the titles top-ten API counts GUESSES, not
  correct answers, so its last-frame leader is wrong for sequel/sibling
  answers (titleshot #501: leader "Despicable Me", real answer
  "Despicable Me 2" from the core API the site itself scores against).
  answers.framed() must use the core API first for titleshot/poster, and
  runner._framed_live_answer's last resort must do the same.
- Framed C2: segment bounds are RAW recording-relative t_starts (no lead
  rebase — the polished clip starts at recording 0 with autocrop OFF).
- Framed unsolved assembly: the diagnosis fast-copy runs (vdir bug fixed)
  but an unsolved run never yields a final / upload.
- Framed Classic rename: no "daily" label in modes/chapters/cards/evidence.
- Framed play gating: no guess before _framed_board_ready.
- Searchle: the opener must come from the guess-accepted (allowed) list
  and survive into the plan, date-seeded.
- Countryle: retry path exercised — upstream failure then success.
"""
import json
import shutil
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

import answers as A
import daily_parity as DP
import runner as R
import solve_lib as S


class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.ok = 200 <= status < 300
        self.content = json.dumps(payload).encode()

    def json(self):
        return self._payload


class TestFramedCoreApi(unittest.TestCase):
    """Titleshot/poster answers come from the core API, not top-ten."""

    _CORE_501 = {
        "id": 502, "day": 501, "asset_id": 1147,
        "asset": {"id": 1147, "external_id": 93456,
                  "imdb_id": "tt1690953", "title": "Despicable Me 2",
                  "framed_title": "", "year": 2013,
                  "titleshot_day": 501},
    }
    # Titles top-ten for #501: frame-6 leader is the WRONG sibling.
    _TOP_TEN_501 = {
        "game_number": "501",
        "frames": [
            {"frame_number": 6,
             "items": json.dumps([
                 {"title": "Despicable Me", "count": 5},
                 {"title": "Despicable Me 4", "count": 1}])},
        ],
    }

    def _get(self, url, **kw):
        if "core.framed.wtf" in url and "/501" in url:
            return _Resp(200, self._CORE_501)
        if "titles.framed.wtf" in url and "/501" in url:
            return _Resp(200, self._TOP_TEN_501)
        raise AssertionError(f"unexpected fetch: {url}")

    def test_core_title_prefers_framed_title(self):
        payload = {"asset": {"title": "Base Title",
                             "framed_title": "Framed Title"}}
        self.assertEqual(A._framed_core_title(payload), "Framed Title")

    def test_core_title_falls_back_to_title(self):
        # The real #501 payload has framed_title "".
        self.assertEqual(A._framed_core_title(self._CORE_501),
                         "Despicable Me 2")

    def test_framed_titleshot_uses_core_not_top_ten(self):
        with mock.patch("requests.get", self._get):
            got = A.framed(date(2026, 10, 18), mode="titleshot")
        self.assertEqual(got["answer"], "Despicable Me 2")
        self.assertEqual(got["via"], "core")
        self.assertEqual(got["puzzle"], 501)

    def test_framed_titleshot_falls_back_to_top_ten(self):
        def _get(url, **kw):
            if "core.framed.wtf" in url:
                return _Resp(404, {})
            if "titles.framed.wtf" in url:
                return _Resp(200, self._TOP_TEN_501)
            raise AssertionError(f"unexpected fetch: {url}")

        with mock.patch("requests.get", _get):
            got = A.framed(date(2026, 10, 18), mode="titleshot")
        # Core down: titles API is the documented fallback (known to be
        # guess-count data, so this result is explicitly second-best).
        self.assertEqual(got["answer"], "Despicable Me")
        self.assertEqual(got["via"], "live")

    def test_framed_core_rejects_other_modes(self):
        # Classic/one-frame have no core challenge path (404-verified);
        # framed_core must not be called for them.
        self.assertIsNone(A.framed_core("classic", 1682))
        self.assertIsNone(A.framed_core("one-frame", 685))


class _FramedFakePage:
    """Fake page for _framed_live_answer / _framed_board_ready.

    evaluate() returns the board header number for the label script,
    body text for the innerText probe, and a canned bool for the
    board-ready script.
    """

    def __init__(self, shown=None, body="", board_ready=True):
        self._shown = shown
        self._body = body
        self._board_ready = board_ready
        self.gotos = []

    def evaluate(self, script, arg=None):
        if "new RegExp" in script:
            return self._shown
        if "GUESSES? REMAINING" in script:
            return self._board_ready
        if "document.body.innerText" in script:
            return self._body
        return None

    def goto(self, url, **kw):
        self.gotos.append(url)

    def wait_for_timeout(self, ms):
        pass


class TestFramedLiveAnswerCore(unittest.TestCase):
    """_framed_live_answer re-resolves via the core API for titleshot."""

    def test_date_walk_uses_core(self):
        page = _FramedFakePage(shown=501, body="TITLESHOT #501")
        core_500 = {"asset": {"title": "Innerspace", "framed_title": ""}}
        core_501 = {"asset": {"title": "Despicable Me 2",
                              "framed_title": ""}}

        def _get(url, **kw):
            if "core.framed.wtf" in url and "/500" in url:
                return _Resp(200, core_500)
            if "core.framed.wtf" in url and "/501" in url:
                return _Resp(200, core_501)
            if "titles.framed.wtf" in url:
                return _Resp(404, {})
            raise AssertionError(f"unexpected fetch: {url}")

        with mock.patch("requests.get", _get):
            with mock.patch.object(A, "target_date",
                                   return_value=date(2026, 10, 17)):
                with mock.patch.object(
                        A, "shift_target_date",
                        side_effect=lambda dlt: date(2026, 10, 17)
                        + timedelta(days=dlt)):
                    got = R._framed_live_answer(page, "titleshot",
                                                "Despicable Me")
        self.assertEqual(got, "Despicable Me 2")


class TestFramedSegmentBounds(unittest.TestCase):
    """C2: bounds are raw t_starts — no lead rebase."""

    def test_no_lead_rebase(self):
        per_mode = [
            {"mode": "classic", "t_start": 19.9},
            {"mode": "one-frame", "t_start": 68.7},
            {"mode": "titleshot", "t_start": 101.4},
            {"mode": "poster", "t_start": 182.1},
        ]
        bounds, kept = R._framed_segment_bounds(per_mode, 240.0)
        # Old (buggy) code subtracted lead=19.9 from every bound:
        # [(0, 48.8), (48.8, 81.5), (81.5, 162.2), (162.2, 220.1)].
        self.assertEqual(bounds, [(19.9, 68.7), (68.7, 101.4),
                                  (101.4, 182.1), (182.1, 240.0)])
        self.assertEqual([d["mode"] for d in kept],
                         ["classic", "one-frame", "titleshot", "poster"])

    def test_first_bound_is_first_mark(self):
        bounds, _ = R._framed_segment_bounds(
            [{"mode": "classic", "t_start": 33.0},
             {"mode": "poster", "t_start": 90.0}], 120.0)
        self.assertEqual(bounds[0][0], 33.0)
        self.assertEqual(bounds[-1][1], 120.0)

    def test_close_marks_deduped(self):
        bounds, kept = R._framed_segment_bounds(
            [{"mode": "classic", "t_start": 10.0},
             {"mode": "one-frame", "t_start": 11.0},
             {"mode": "poster", "t_start": 50.0}], 100.0)
        self.assertEqual(len(bounds), 2)
        self.assertEqual([d["mode"] for d in kept],
                         ["classic", "poster"])


class TestFramedUnsolvedAssembly(unittest.TestCase):
    """Unsolved: diagnosis fast-copy runs, but never a final/upload."""

    def test_unsolved_no_final_no_upload(self):
        tmp = Path(tempfile.mkdtemp(prefix="iter12-framed-"))
        calls = {"fast_copy": [], "upload": []}
        orig_here = R.HERE
        try:
            R.HERE = tmp
            per_mode = [
                {"mode": "classic", "answer": "Warm Bodies",
                 "solved": True, "evidence": "win signal",
                 "t_start": 19.9},
                {"mode": "titleshot", "answer": "Despicable Me 2",
                 "solved": False, "evidence": "NO win signal",
                 "t_start": 101.4},
            ]
            answers = {"classic": "Warm Bodies",
                       "titleshot": "Despicable Me 2"}
            with mock.patch.object(
                    R, "_fast_copy_gameplay",
                    side_effect=lambda s, o: calls["fast_copy"].append(
                        (s, str(o))) or str(o)):
                with mock.patch.object(
                        R, "_upload_framed",
                        side_effect=lambda *a, **k: calls["upload"].append(
                            a)):
                    with mock.patch.object(
                            R, "_qc_visible_content",
                            return_value=(False, "mocked")):
                        with mock.patch.object(R, "_report_game"):
                            with mock.patch.object(R, "_db_save"):
                                out = R._assemble_framed(
                                    "framed", {"name": "Framed",
                                               "slug": "framed-answer-today"},
                                    "2026-10-18", "Oct 18",
                                    "short", answers, per_mode, False,
                                    "classic=WIN; titleshot=FAIL",
                                    "/nonexistent/raw.mp4")
            # Diagnosis fast-copy DID run (vdir bug fixed).
            self.assertEqual(len(calls["fast_copy"]), 1)
            # ... but no final, no upload of any kind: _upload_framed is
            # invoked only with final=None, and the real function
            # returns immediately on final=None (no upload).
            self.assertIsNone(out["video"])
            self.assertFalse(out["solved"])
            self.assertTrue(calls["upload"])
            self.assertTrue(all(a[-1] is None for a in calls["upload"]),
                            "upload must never receive a final on unsolved")
            doc = json.loads((tmp / "videos" / "framed"
                              / "result.json").read_text())
            self.assertIsNone(doc["video"])
            self.assertFalse(doc["solved"])
            self.assertEqual([m["mode"] for m in doc["modes"]],
                             ["classic", "titleshot"])
            self.assertNotIn("daily", json.dumps(doc).lower())
        finally:
            R.HERE = orig_here
            shutil.rmtree(tmp, ignore_errors=True)


class TestFramedClassicRenameOutputs(unittest.TestCase):
    """No 'daily' label in Framed chapters/cards/evidence."""

    def test_mode_names(self):
        for mid, display, _url in R.FRAMED_MODES:
            self.assertNotIn("daily", mid.lower())
            self.assertNotIn("daily", display.lower())
        # Chapters/cards are built from FRAMED_MODES display names.
        chapters = [m for _k, m, _u in R.FRAMED_MODES]
        self.assertIn("Framed Classic", chapters)
        self.assertTrue(all("daily" not in c.lower() for c in chapters))

    def test_mode_card_says_classic(self):
        tmp = Path(tempfile.mkdtemp(prefix="iter12-card-"))
        try:
            out = tmp / "mode_classic.png"
            DP.generate_mode_card(str(out), "Framed", "Classic",
                                  "Round 1 of 4")
            self.assertTrue(out.exists())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_answers_modes_classic(self):
        self.assertIn("classic", A.FRAMED_MODES)
        self.assertNotIn("daily", A.FRAMED_MODES)


class TestFramedBoardGate(unittest.TestCase):
    """Play starts only once the mode board is showing."""

    def test_ready_with_counter(self):
        page = _FramedFakePage(body="6 GUESSES REMAINING",
                               board_ready=True)
        self.assertTrue(R._framed_board_ready(page))

    def test_not_ready_menu_only(self):
        page = _FramedFakePage(body="MENU CLASSIC ONE FRAME",
                               board_ready=False)
        self.assertFalse(R._framed_board_ready(page))

    def test_not_ready_poster_gate(self):
        page = _FramedFakePage(body="WATCH AD to unlock POSTER",
                               board_ready=True)
        self.assertFalse(R._framed_board_ready(page))


class TestSearchleOpener(unittest.TestCase):
    """Opener comes from the guess-accepted (allowed) list."""

    def test_opener_in_bank_and_survives(self):
        bank = S.words_searchle().get(5) or []
        self.assertTrue(bank, "searchle 5-letter bank must exist")
        g1 = R._guesses("searchle", "KNEES")
        g2 = R._guesses("searchle", "KNEES")
        self.assertEqual(g1, g2, "opener must be date-seeded deterministic")
        self.assertTrue(g1, "plan must not be empty")
        self.assertIn(g1[0], bank,
                      "opener must be a member of the allowed bank")
        self.assertNotEqual(g1[0], "KNEES", "opener must not be the answer")
        self.assertEqual(g1[-1], "KNEES", "plan must end on the answer")

    def test_opener_not_entropy_fallback(self):
        # The old bug: the fixed opener was dropped and plan_sequence
        # fell back to an entropy pick. The opener must now be the
        # date-seeded bank pick, not whatever pick_guess prefers.
        g = R._guesses("searchle", "KNEES")
        bank = S.words_searchle().get(5) or []
        import random as _r
        rng = _r.Random(R._day_seed("searchle"))
        cands = [w for w in bank if w != "KNEES"
                 and len(set(w)) == len(w)]
        if not cands:
            cands = [w for w in bank if w != "KNEES"]
        self.assertEqual(g[0], rng.choice(cands))


class TestCountryleRetryThenSuccess(unittest.TestCase):
    """Retry path exercised: upstream failure THEN success."""

    def test_failure_then_success(self):
        calls = []

        def _get(*a, **k):
            calls.append(a)
            if len(calls) == 1:
                return _Resp(500, {})
            # Valid payload: plain int country id (Botswana = 26).
            return _Resp(200, {"country": 26})

        with mock.patch("requests.get", _get):
            with mock.patch("time.sleep", lambda *a, **k: None):
                got = A.countryle(date(2026, 10, 18))
        self.assertEqual(got["answer"], "Botswana")
        self.assertEqual(got["via"], "live")
        self.assertEqual(len(calls), 2,
                         "one failure + one success, no stale fallback")


if __name__ == "__main__":
    unittest.main()
