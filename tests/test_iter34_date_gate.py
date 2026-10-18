"""Fix-loop iteration 34 tests — GENERALIZED puzzle-number <-> date gate.

User directive: the bot must VERIFY the served board is really the
target date's board — using each game's puzzle NUMBER <-> date mapping
where one exists — and REJECT an old-date board (honest defer, no
video, next run picks it up). This generalizes the PROVEN Nerdle hard
gate + Batterup date-keying gate to all 18 games.

Evidence (all three pinned before implementing):
- Run 39119211813 artifacts: only Batterup (#973), Semantle (#1722)
  and Nerdle (per-mode headers) carry a served puzzle NUMBER; 6 games
  carry a served date only; 9 carry NEITHER. Framed #685 / Worldle
  No. 1731 / Canuckle #1618 do NOT appear in that run's artifacts.
- WordSolverX source: ONLY Nerdle has a number<->date formula.
  Batterup's number lives in the per-date CDN file. The other 16
  games have NO number<->date data — their gate is date-only.
- Repo audit: Marveldle pick-id fetch failure PROCEEDED (fail-open);
  Framed re-keyed to the served day + silently fell back to the
  target answer when the header was unreadable; Phrazle/Phoodle
  followed the served board unconditionally; Canuckle could solve
  a stale Firestore fallback; Worldle tried adjacent dates; the
  Semantle displayed-# check only ran when the probe disagreed.

Gates implemented (fail-closed on POSITIVE mismatch evidence;
unreadable signal still proceeds, as before — a read failure is
not evidence of a stale board):
- Framed: mode header #N must equal the target day's number, else
  _FramedStaleBoard -> mode deferred. Unreadable header -> defer.
- Semantle: displayed Game #N overrides a probe that agrees with
  the target (same +/-1 bound as the re-key path).
- Phrazle: page game_number must map to the target date, else defer.
- Phoodle: page calendar day must equal the target date, else defer.
- Canuckle: live Firestore fallback date must equal target, else
  SystemExit (defer). Bundled path was already date-exact.
- Marveldle: pick-id fetch failure now raises SystemExit (defer)
  instead of proceeding to live-solve.
- Worldle: Summary/postcard date must equal the played day, else
  defer. Unreadable => proceed (not evidence of staleness).
- Nerdle / Batterup / Waffle gates untouched (already hard).
- Betweenle / Colordle / Colorfle / Contexto / Searchle / Worgle /
  Countryle / Globle: NO number exists to check — the probe align
  + tgt-1 defer is the gate. No number is invented for them.
"""
import base64
import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

import answers as A
import runner as R


# ---------------------------------------------------------------- Framed

class _FramedPage:
    """Fake page for _framed_live_answer: evaluate() returns the board
    header number for the label script; goto/reload/wait are no-ops."""

    def __init__(self, shown):
        self._shown = shown

    def evaluate(self, script, *args):
        if "new RegExp" in script:
            return self._shown
        return ""

    def goto(self, url, **kw):
        pass

    def reload(self, **kw):
        pass

    def wait_for_timeout(self, ms):
        pass


class TestFramedGate(unittest.TestCase):
    def setUp(self):
        self._orig_framed = A.framed
        self._orig_dbg = R._dbg
        self.logs = []
        R._dbg = lambda gid, msg: self.logs.append(f"[{gid}] {msg}")

    def tearDown(self):
        A.framed = self._orig_framed
        R._dbg = self._orig_dbg

    def _engine(self, puzzle):
        A.framed = lambda d, mode=None: {"puzzle": puzzle, "answer": "X"}

    def test_match_returns_fallback(self):
        self._engine(685)
        got = R._framed_live_answer(_FramedPage(685), "one-frame",
                                    "fallback")
        self.assertEqual(got, "fallback")

    def test_mismatch_raises_stale(self):
        self._engine(685)
        with self.assertRaises(R._FramedStaleBoard) as cm:
            R._framed_live_answer(_FramedPage(684), "one-frame",
                                  "fallback")
        self.assertIn("684", str(cm.exception))
        self.assertIn("685", str(cm.exception))

    def test_unreadable_header_raises_stale(self):
        self._engine(685)
        with self.assertRaises(R._FramedStaleBoard) as cm:
            R._framed_live_answer(_FramedPage(None), "classic",
                                  "fallback")
        self.assertIn("unreadable", str(cm.exception))

    def test_solve_framed_modes_defers_all_stale(self):
        # Every mode's board number mismatches the target's: all four
        # defer, none solved, no assembly input.
        self._engine(685)
        pg = _FramedPage(684)
        answers = {mid: "Some Movie" for mid, _, _ in R.FRAMED_MODES}
        orig_open = R._framed_open_mode
        orig_ready = R._framed_board_ready
        R._framed_open_mode = lambda page, url: True
        R._framed_board_ready = lambda page: True
        try:
            per_mode, solved_modes = R.solve_framed_modes(pg, answers)
        finally:
            R._framed_open_mode = orig_open
            R._framed_board_ready = orig_ready
        self.assertEqual(len(per_mode), 4)
        self.assertTrue(all(d.get("deferred") for d in per_mode))
        self.assertTrue(all(str(d.get("evidence") or "").startswith("defer:")
                            for d in per_mode))
        self.assertEqual(solved_modes, [])


# ------------------------------------------------------- Semantle display

class _SemantlePage:
    def __init__(self, text):
        self._text = text

    def evaluate(self, js, *a):
        return self._text


class TestAlignSemantleDisplayedOverridesProbe(unittest.TestCase):
    """Probe says target, displayed Game # says otherwise: the
    displayed number (page state from the game host) wins."""

    def setUp(self):
        self._orig_prov = R._live_day_provenance
        self._orig_sem = A.semantle
        self._orig_dbg = R._dbg
        self.logs = []
        R._dbg = lambda gid, msg: self.logs.append(f"[{gid}] {msg}")
        self.target = A.target_date()
        self.epoch = R._SEMANTLE_EPOCH
        self.n_target = (self.target - self.epoch).days
        R._live_day_provenance = lambda gid: (
            self.target.isoformat(), "semantle.com",
            "https://semantle.com/x")
        R.A.semantle = lambda d: {
            "answer": f"word-{(d - self.epoch).days}",
            "puzzle": (d - self.epoch).days}

    def tearDown(self):
        R._live_day_provenance = self._orig_prov
        A.semantle = self._orig_sem
        R._dbg = self._orig_dbg

    def _ans(self):
        return {"answer": "cry", "puzzle": self.n_target,
                "date": str(self.target)}

    def _page(self, n):
        return _SemantlePage(f"Semantle\nGame #{n}\n")

    def test_displayed_stale_defers_via_served(self):
        out, served = R._align_answer_to_live_day(
            "semantle", self._ans(), self._page(self.n_target - 1))
        self.assertEqual(str(served), str(self.target - timedelta(days=1)))
        self.assertEqual(out["answer"], f"word-{self.n_target - 1}")

    def test_displayed_ahead_rekeys(self):
        out, served = R._align_answer_to_live_day(
            "semantle", self._ans(), self._page(self.n_target + 1))
        self.assertEqual(str(served), str(self.target + timedelta(days=1)))
        self.assertEqual(out["answer"], f"word-{self.n_target + 1}")

    def test_displayed_target_keeps_target(self):
        ans = self._ans()
        out, served = R._align_answer_to_live_day(
            "semantle", ans, self._page(self.n_target))
        self.assertIs(out, ans)
        self.assertIsNone(served)

    def test_displayed_far_jump_keeps_target(self):
        ans = self._ans()
        out, served = R._align_answer_to_live_day(
            "semantle", ans, self._page(self.n_target - 22))
        self.assertIs(out, ans)
        self.assertIsNone(served)


# ---------------------------------------------------------------- Phrazle

class _PhrazlePage:
    """evaluate() returns the page's own game number for the
    arithmetic script; everything else is inert."""

    def __init__(self, n):
        self._n = n

    def evaluate(self, js, *a):
        if "getTimezoneOffset" in js:
            return self._n
        return ""

    def query_selector(self, *a, **k):
        return None


class TestPhrazleGate(unittest.TestCase):
    def setUp(self):
        self._orig_dbg = R._dbg
        self.logs = []
        R._dbg = lambda gid, msg: self.logs.append(f"[{gid}] {msg}")

    def tearDown(self):
        R._dbg = self._orig_dbg

    def test_date_from_game_number_both_slots(self):
        tgt = A.target_date()
        n1 = A.phrazle_game_number(tgt, hour=9)
        n2 = A.phrazle_game_number(tgt, hour=15)
        self.assertEqual(R._phrazle_date_from_game_number(n1), tgt)
        self.assertEqual(R._phrazle_date_from_game_number(n2), tgt)
        self.assertEqual(
            R._phrazle_date_from_game_number(
                A.phrazle_game_number(tgt - timedelta(days=1), hour=9)),
            tgt - timedelta(days=1))

    def test_date_from_game_number_invalid(self):
        self.assertIsNone(R._phrazle_date_from_game_number(0))
        self.assertIsNone(R._phrazle_date_from_game_number(-5))
        self.assertIsNone(R._phrazle_date_from_game_number("abc"))
        self.assertIsNone(R._phrazle_date_from_game_number(None))

    def test_stale_board_defers(self):
        tgt = A.target_date()
        n_stale = A.phrazle_game_number(tgt - timedelta(days=1), hour=9)
        pg = _PhrazlePage(n_stale)
        orig_close = R._close_modals
        orig_howto = R._close_generic_howto
        R._close_modals = lambda *a, **k: None
        R._close_generic_howto = lambda *a, **k: None
        try:
            ok, ev = R._s_phrazle(pg, "CRY", "phrazle")
        finally:
            R._close_modals = orig_close
            R._close_generic_howto = orig_howto
        self.assertFalse(ok)
        self.assertTrue(ev.startswith("defer:"), ev)
        self.assertIn("game_number", ev)


# ---------------------------------------------------------------- Phoodle

class _PhoodlePage:
    def wait_for_timeout(self, ms):
        pass


class TestPhoodleGate(unittest.TestCase):
    def setUp(self):
        self._orig_dbg = R._dbg
        self._orig_close = R._close_modals
        self._orig_reset = R._reset_site_state
        self._orig_today = R._browser_today
        self._orig_solve = R.solve_wordle_like
        self._orig_guesses = R._guesses
        self._orig_live = dict(R._LIVE_DAY)
        self.logs = []
        R._dbg = lambda gid, msg: self.logs.append(f"[{gid}] {msg}")
        R._close_modals = lambda *a, **k: None
        R._reset_site_state = lambda *a, **k: None
        R._LIVE_DAY.clear()

    def tearDown(self):
        R._dbg = self._orig_dbg
        R._close_modals = self._orig_close
        R._reset_site_state = self._orig_reset
        R._browser_today = self._orig_today
        R.solve_wordle_like = self._orig_solve
        R._guesses = self._orig_guesses
        R._LIVE_DAY.clear()
        R._LIVE_DAY.update(self._orig_live)

    def test_stale_board_defers(self):
        tgt = A.target_date()
        R._browser_today = lambda page: tgt - timedelta(days=1)
        ok, ev = R._s_phoodle(_PhoodlePage(), "LUNGO", "phoodle")
        self.assertFalse(ok)
        self.assertTrue(ev.startswith("defer:"), ev)
        self.assertIn(str(tgt - timedelta(days=1)), ev)

    def test_target_day_proceeds(self):
        tgt = A.target_date()
        R._browser_today = lambda page: tgt
        R.solve_wordle_like = lambda *a, **k: (True, "ok")
        R._guesses = lambda *a, **k: ["LUNGO"]
        ok, ev = R._s_phoodle(_PhoodlePage(), "LUNGO", "phoodle")
        self.assertTrue(ok)


# --------------------------------------------------------------- Canuckle

class _FirestoreResp:
    def __init__(self, docs):
        self.ok = True
        self._docs = docs

    def json(self):
        return {"documents": self._docs}


def _canuckle_doc(idx, answer):
    return {"fields": {
        "index": {"integerValue": str(idx)},
        "answer": {"stringValue": base64.b64encode(
            answer.encode()).decode()}}}


class TestCanuckleGate(unittest.TestCase):
    def setUp(self):
        self._orig_zai = A.ZAI
        self._tmp = tempfile.TemporaryDirectory()
        (Path(self._tmp.name) / "src" / "lib").mkdir(parents=True)
        (Path(self._tmp.name) / "src" / "lib" /
         "wordlebot-wasm").mkdir(exist_ok=True)
        (Path(self._tmp.name) / "src" / "lib" / "wordlebot-wasm" /
         "assets" / "generated").mkdir(parents=True)
        (Path(self._tmp.name) / "src" / "lib" / "wordlebot-wasm" /
         "assets" / "generated" / "canuckle-data.json").write_text(
            json.dumps({"puzzles": []}))
        A.ZAI = Path(self._tmp.name)

    def tearDown(self):
        A.ZAI = self._orig_zai
        self._tmp.cleanup()

    @staticmethod
    def _idx_for(d):
        return 143 + (d - date(2022, 10, 4)).days

    def test_stale_live_fallback_defers(self):
        stale = date(2030, 1, 1) - timedelta(days=1)
        resp = _FirestoreResp(
            [_canuckle_doc(self._idx_for(stale), "TITLE")])
        with mock.patch("requests.get", return_value=resp):
            with self.assertRaises(SystemExit) as cm:
                A.canuckle(d=date(2030, 1, 1))
        self.assertIn("stale", str(cm.exception))

    def test_fresh_live_fallback_ok(self):
        fresh = date(2030, 1, 1)
        resp = _FirestoreResp(
            [_canuckle_doc(self._idx_for(fresh), "TITLE")])
        with mock.patch("requests.get", return_value=resp):
            got = A.canuckle(d=fresh)
        self.assertEqual(got["date"], "2030-01-01")
        self.assertEqual(got["via"], "live")


# -------------------------------------------------------------- Marveldle

class TestMarveldleFailClosed(unittest.TestCase):
    def test_pick_id_fetch_failure_defers(self):
        with mock.patch("requests.get",
                        side_effect=ConnectionError("boom")), \
                mock.patch.object(A, "_marvel_sid",
                                  return_value="sid"):
            with self.assertRaises(SystemExit) as cm:
                A.marveldle(d=date(2026, 10, 18))
        self.assertIn("pick-id check failed", str(cm.exception))


# ------------------------------------------------------------ Worldle sum

class TestWorldleSummaryDate(unittest.TestCase):
    def test_postcard_date(self):
        self.assertEqual(
            R._worldle_summary_date("OCTOBER 18, 2026 · NO. 1731"),
            date(2026, 10, 18))

    def test_lowercase(self):
        self.assertEqual(
            R._worldle_summary_date("october 18, 2026"),
            date(2026, 10, 18))

    def test_no_date(self):
        self.assertIsNone(
            R._worldle_summary_date("BONUS ROUNDS COMPLETED 11/11"))
        self.assertIsNone(R._worldle_summary_date(""))
        self.assertIsNone(R._worldle_summary_date(None))

    def test_impossible_date(self):
        self.assertIsNone(
            R._worldle_summary_date("FEBRUARY 30, 2026"))


if __name__ == "__main__":
    unittest.main()
