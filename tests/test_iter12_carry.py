"""Fix-loop iteration 12 regression tests (carry-forward verification).

Covers the gaps found re-verifying the iteration-11 batch against the
98a92ff code (not its claims):
- Searchle opener: iter-11 picked the opener from a generic list that is
  NOT in Searchle's own guess bank for any length, so plan_sequence
  silently dropped it and played an entropy pick (STARE) as guess 1.
- Semantic cap: the iter-11 cap counted ACCEPTED words only, so under a
  rejection storm every rejected probe was replaced and retyped without
  bound (reproduced on unmodified code: 4157 Enters on an all-rejected
  page). The cap must count TYPED attempts.
- Semantic dedupe: the iter-11 guard compared the rejected set only
  exact-case, and the feedback swap bypassed the guard entirely.
- Early result.json: a crash in browser/context setup (before the solver
  returned) left no result.json; the nerdle subprocess-exception path
  returned with none either. SIGTERM handler is now a testable
  module-level function.
"""
import json
import signal
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

import answers as A
import runner as R
import solve_lib as S


def _typed_words(page):
    """Words typed into a mock page: chars between Enter presses."""
    words, buf = [], []
    for call in page.keyboard.method_calls:
        name, args, _kw = call
        if name == "type" and args:
            buf.append(str(args[0]))
        elif name == "press" and args and args[0] == "Enter":
            words.append("".join(buf))
            buf = []
    return words


def _mock_semantic_page(mode="reject"):
    """Mock Playwright page for the semantic solvers.

    mode="reject": every probe is refused (body text never contains the
    word) — the rejection-storm case.
    mode="accept": the body text always shows the in-progress word with
    a score, so every probe is accepted but no win signal ever appears.
    """
    page = mock.MagicMock()
    state = {"buf": []}

    def _type(ch):
        state["buf"].append(str(ch))

    def _press(key):
        if key == "Enter":
            state["buf"] = []

    page.keyboard.type.side_effect = _type
    page.keyboard.press.side_effect = _press

    def _eval(script, arg=None):
        if "table tr" in script:
            return []  # no per-guess score rows in the DOM
        if "document.body.innerText" in script:
            if mode == "accept":
                w = "".join(state["buf"])
                return f"{w} 55%" if w else ""
            return ""
        return ""

    page.evaluate.side_effect = _eval
    return page


class TestSearchleOpener(unittest.TestCase):
    def test_opener_is_in_bank_for_every_length(self):
        bank = S.words_searchle()
        for n, words in bank.items():
            op = R._searchle_opener(words, n, "searchle:2026-10-18", "KNEES")
            self.assertIsNotNone(op, f"no opener for length {n}")
            self.assertIn(op, [w.upper() for w in words])
            self.assertNotEqual(op, "KNEES")

    def test_opener_varies_by_date_seed(self):
        bank = S.words_searchle()[5]
        seen = {R._searchle_opener(bank, 5, f"searchle:2026-10-{d:02d}",
                                    "KNEES")
                for d in range(1, 29)}
        self.assertGreater(len(seen), 1,
                           "date-seeded opener must vary across days")

    def test_plan_for_searchle_actually_plays_opener_first(self):
        # _plan_for uses A.target_date via _day_seed; pin it.
        with mock.patch.object(A, "target_date",
                               return_value=date(2026, 10, 18)):
            R._PLANS.pop("searchle", None)
            guesses, _steps = R._plan_for("searchle", "KNEES")
        bank = {w.upper() for w in S.words_searchle()[5]}
        self.assertIn(guesses[0], bank,
                      "first Searchle guess must be a legal bank word")
        self.assertEqual(guesses[-1], "KNEES",
                         "final guess must always be the verified answer")
        self.assertEqual(guesses[0],
                         R._searchle_opener(sorted(bank), 5,
                                            "searchle:2026-10-18", "KNEES"),
                         "first guess must be the date-seeded opener, "
                         "not an entropy pick")


class TestDailyOpenerVariation(unittest.TestCase):
    def test_opener_varies_across_dates(self):
        choices = ["CRANE", "SLATE", "TRACE", "ADIEU", "ROAST", "AUDIO"]
        seen = set()
        for d in range(1, 29):
            with mock.patch.object(A, "target_date",
                                   return_value=date(2026, 10, d)):
                seen.add(R._daily_opener(choices))
        self.assertGreater(len(seen), 1,
                           "date-seeded opener must vary across days")

    def test_canuckle_and_betweenle_openers_are_legal_guesses(self):
        g, _ = S.words_canuckle()
        for o in ["CRANE", "SLATE", "TRACE", "ADIEU", "ROAST", "AUDIO"]:
            self.assertIn(o, g, f"Canuckle opener {o} not a legal guess")
        g2, _ = S.words_betweenle()
        for o in ["ABOUT", "CRANE", "SLATE", "TRACE", "ADIEU", "ROAST"]:
            self.assertIn(o, g2, f"Betweenle opener {o} not a legal guess")


class TestSemanticDedupe(unittest.TestCase):
    def test_dup_is_case_insensitive_on_both_sets(self):
        self.assertTrue(R._semantic_dup("CRY", ["cry"], []))
        self.assertTrue(R._semantic_dup("cry", [], ["CRY"]))
        self.assertTrue(R._semantic_dup("Cry", [], ["cRy"]))
        self.assertFalse(R._semantic_dup("cry", ["log"], ["ago"]))

    def _run(self, fn, gid, mode):
        page = _mock_semantic_page(mode)
        saved_played = dict(R._PLAYED)
        saved_rej = dict(R._REJECTED)
        with mock.patch.object(R, "_dbg", lambda *a: None):
            try:
                ok, _ev = fn(page, "cry" if gid == "semantle"
                             else "section", gid=gid)
            finally:
                R._PLAYED.clear()
                R._PLAYED.update(saved_played)
                R._REJECTED.clear()
                R._REJECTED.update(saved_rej)
        return page, ok

    def test_rejection_storm_types_at_most_five(self):
        # The iter-11 bug: 4157 Enters on an all-rejected page.
        for fn, gid in ((R.solve_semantle, "semantle"),
                         (R.solve_contexto, "contexto")):
            page, _ok = self._run(fn, gid, "reject")
            words = _typed_words(page)
            self.assertLessEqual(len(words), 5,
                                 f"{gid}: {len(words)} typed under storm")
            self.assertEqual(len({w.lower() for w in words}), len(words),
                             f"{gid}: duplicate word typed: {words}")

    def test_accepted_path_types_at_most_five_no_duplicates(self):
        for fn, gid in ((R.solve_semantle, "semantle"),
                         (R.solve_contexto, "contexto")):
            page, _ok = self._run(fn, gid, "accept")
            words = _typed_words(page)
            self.assertLessEqual(len(words), 5,
                                 f"{gid}: {len(words)} typed when accepted")
            self.assertEqual(len({w.lower() for w in words}), len(words),
                             f"{gid}: duplicate word typed: {words}")
            # Answer is the verified correct answer, typed last.
            self.assertEqual(words[-1],
                             "cry" if gid == "semantle" else "section")


class TestEarlyResultAndSigterm(unittest.TestCase):
    def test_preliminary_result_on_failure(self):
        tmp = Path(str(R.HERE)) / "videos" / "test-early-result"
        (R.HERE / "videos").mkdir(exist_ok=True)
        with mock.patch.object(R, "_dbg", lambda *a: None):
            R._write_preliminary_result("test-early-result", "2026-10-18",
                                        False, "boom")
        rec = json.loads((tmp / "result.json").read_text(encoding="utf-8"))
        self.assertFalse(rec["solved"])
        self.assertTrue(rec["qc"]["pending"])
        self.assertEqual(rec["evidence"], "boom")
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    def test_nerdle_subprocess_exception_writes_result(self):
        vdir = R.HERE / "videos" / "nerdle"
        vdir.mkdir(parents=True, exist_ok=True)
        marker = vdir / "result.json"
        if marker.exists():
            marker.unlink()
        with mock.patch.object(R, "_dbg", lambda *a: None), \
                mock.patch.object(R, "_db_already_done",
                                  return_value=None), \
                mock.patch("subprocess.run",
                           side_effect=RuntimeError("launch failed")):
            res = R.run_nerdle_external({}, date(2026, 10, 18),
                                        "2026-10-18", "Oct 18, 2026",
                                        "Oct 18")
        self.assertFalse(res["solved"])
        self.assertTrue(marker.exists(),
                        "nerdle subprocess failure must leave result.json")
        rec = json.loads(marker.read_text(encoding="utf-8"))
        self.assertFalse(rec["solved"])
        self.assertIn("subprocess", rec["evidence"])
        marker.unlink(missing_ok=True)

    def test_sigterm_handler_writes_result_and_exits_143(self):
        vdir = R.HERE / "videos" / "test-sigterm"
        vdir.mkdir(parents=True, exist_ok=True)
        marker = vdir / "result.json"
        if marker.exists():
            marker.unlink()
        saved = dict(R._CURRENT_RUN)
        with mock.patch.object(R, "_dbg", lambda *a: None), \
                mock.patch("subprocess.run", return_value=None):
            R._CURRENT_RUN.update({"gid": "test-sigterm",
                                   "date_key": "2026-10-18",
                                   "solved": False,
                                   "evidence": "mid-solve",
                                   "stage": "polish"})
            try:
                with self.assertRaises(SystemExit) as cm:
                    R._signal_handler(signal.SIGTERM, None)
                self.assertEqual(cm.exception.code, 143)
            finally:
                R._CURRENT_RUN.clear()
                R._CURRENT_RUN.update(saved)
        self.assertTrue(marker.exists(),
                        "SIGTERM handler must write result.json")
        rec = json.loads(marker.read_text(encoding="utf-8"))
        self.assertIn("terminated by SIGTERM during polish",
                      rec["evidence"])
        import shutil
        shutil.rmtree(vdir, ignore_errors=True)

    def test_install_registers_signal_handler(self):
        seen = {}
        real_signal = signal.signal
        try:
            signal.signal = lambda s, h: seen.__setitem__(s, h)
            R._install_signal_handlers()
        finally:
            signal.signal = real_signal
        self.assertIs(seen.get(signal.SIGTERM), R._signal_handler)
        self.assertIs(seen.get(signal.SIGINT), R._signal_handler)


if __name__ == "__main__":
    unittest.main()
