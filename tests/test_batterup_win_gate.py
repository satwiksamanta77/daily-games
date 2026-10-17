"""Regression tests: Batterup hollow-win gate (ANALYSIS7 §3, run 39039561115).

result.json claimed solved=True on evidence "win signal 'you won' after
1 submitted guesses" while the video showed NO solve: input holding the
answer, suggestion dropdown open, guess rows empty. Root causes fixed:
1. A pick + sent keystroke was counted as "submitted" without verifying
   submission happened (Enter did not submit; dropdown stayed open).
2. The populated-row wait ran but its return value gated nothing.
3. _strict_win matched WIN_PHRASES against raw page HTML — hidden
   templates/scripts contain 'you won'.
4. The answer-echo fallback fired because the typed answer sat in the
   input + open dropdown.

Run:  python3 -m unittest tests.test_batterup_win_gate -v
All tests are synthetic/local (fake pages, monkeypatched helpers —
no browser, no network, no live play).
"""
import inspect
import unittest
from unittest import mock
from pathlib import Path
import sys

HERE = Path(__file__).parent.parent
sys.path.insert(0, str(HERE))

import runner  # noqa: E402

ANSWER = "Wyatt Langford"


class _FakeKeyboard:
    def __init__(self):
        self.pressed = []

    def press(self, key):
        self.pressed.append(key)


class _FakePage:
    """Fake Playwright page: evaluate() dispatches on script markers;
    content() returns the hollow-win page HTML (hidden template +
    script + display:none div all containing 'you won', visible main
    without it) — the win gate must ignore all of it."""

    HOLLOW_HTML = (
        "<html><head><script>var x = 'you won';</script></head>"
        "<body><template><div>you won</div></template>"
        "<div style='display:none'>you won</div>"
        "<main>Guess the player</main></body></html>")

    def __init__(self, visible_text="", semantics_labels="", inner="",
                 cols=None):
        self.visible_text = visible_text
        self.semantics_labels = semantics_labels
        self.inner = inner
        self.cols = cols if cols is not None else {"n": 0, "sample": ""}
        self.keyboard = _FakeKeyboard()

    def content(self):
        return self.HOLLOW_HTML

    def evaluate(self, js, *args):
        if "_visible_page_text" in js:
            return self.visible_text
        if "flt-semantics" in js:
            return self.semantics_labels
        if "document.body.innerText" in js:
            return self.inner
        if "className.baseVal" in js:
            return dict(self.cols)
        if "input[name=guess]" in js:
            return True
        return ""

    def query_selector(self, _sel):
        return None

    def wait_for_timeout(self, _ms):
        pass


class TestStrictWinVisibleOnly(unittest.TestCase):
    """(3) Hidden-template 'you won' must NOT count as a win."""

    def test_hidden_template_you_won_does_not_count(self):
        page = _FakePage(visible_text="Batter Up Guess the player")
        ok, _ev = runner._strict_win(page, ANSWER)
        self.assertFalse(ok)

    def test_answer_in_input_or_dropdown_does_not_count(self):
        # Visible text holds only the answer (input value / open
        # suggestion dropdown text) — no win phrase. Must be False.
        page = _FakePage(
            visible_text="Wyatt Langford Wyatt Langford Guess")
        ok, _ev = runner._strict_win(page, ANSWER)
        self.assertFalse(ok)

    def test_visible_win_text_counts(self):
        page = _FakePage(visible_text="You won! Congratulations")
        ok, ev = runner._strict_win(page, ANSWER)
        self.assertTrue(ok)
        self.assertIn("you won", ev)

    def test_post_win_markers_still_work_on_visible_text(self):
        page = _FakePage(visible_text="Guess Distribution 1 2 3")
        ok, ev = runner._strict_win(page, ANSWER)
        self.assertTrue(ok)
        self.assertIn("post-win screen", ev)

    def test_visible_text_js_excludes_hidden_nodes(self):
        src = runner._VISIBLE_TEXT_JS
        for token in ("SCRIPT", "STYLE", "TEMPLATE", "NOSCRIPT",
                      "hidden", "display", "visibility"):
            self.assertIn(token, src)
        # The win gate must never consult raw HTML.
        self.assertNotIn("page.content",
                         inspect.getsource(runner._strict_win))


class _ProbePage(_FakePage):
    """Fake page serving a scripted sequence of post-submit probe dicts
    (probe JS is identified by 'dropdown_open')."""

    def __init__(self, probes):
        super().__init__()
        self._probes = list(probes)

    def evaluate(self, js, *args):
        if "dropdown_open" in js:
            if self._probes:
                return dict(self._probes.pop(0))
            return {"rows": [], "dropdown_open": False, "populated": False,
                    "populated_rows": 0, "feedback_tiles": 0}
        return super().evaluate(js, *args)


def _probe(rows, drop, pop, fb, exact=None):
    return {"rows": rows, "dropdown_open": drop, "populated": pop,
            "populated_rows": pop, "feedback_tiles": fb,
            # Iter 11: exact_n comes from visible exact cells inside
            # populated rows, never a page-wide class count.
            "visible_exact_cells": fb if exact is None else exact}


class TestAttrVerifySubmitted(unittest.TestCase):
    """(1) Submission = populated-row GROWTH past the baseline."""

    def setUp(self):
        self._dbg_msgs = []
        self._p = mock.patch.object(
            runner, "_dbg",
            lambda gid, msg: self._dbg_msgs.append(msg))
        self._p.start()

    def tearDown(self):
        self._p.stop()

    def test_growth_verifies_submission(self):
        page = _ProbePage([_probe(["Wyatt Langford"], True, 1, 6),
                           _probe(["Wyatt Langford"], False, 1, 6)])
        ok, pr = runner._attr_verify_submitted(page, "batterup", 0)
        self.assertTrue(ok)
        self.assertEqual(pr["populated_rows"], 1)
        self.assertNotIn("WARN", "\n".join(self._dbg_msgs))

    def test_no_growth_is_not_submission(self):
        page = _ProbePage([_probe([], False, 0, 0)])
        ok, _pr = runner._attr_verify_submitted(page, "batterup", 0,
                                                timeout_s=0.05)
        self.assertFalse(ok)
        # The dismiss ran (input cleared) for the retry's fresh pick.
        self.assertIn("Escape", page.keyboard.pressed)

    def test_stale_rows_do_not_verify(self):
        # One row from an earlier guess; this guess added nothing.
        page = _ProbePage([_probe(["Probe One"], False, 1, 6)])
        ok, _pr = runner._attr_verify_submitted(page, "batterup", 1,
                                                timeout_s=0.05)
        self.assertFalse(ok)

    def test_growth_with_sticky_dropdown_still_accepts(self):
        # Dropdown detector stays open after dismiss (known iter9
        # flakiness) — row growth is the decisive proof of submission.
        page = _ProbePage([_probe(["Wyatt Langford"], True, 1, 6),
                           _probe(["Wyatt Langford"], True, 1, 6)])
        ok, _pr = runner._attr_verify_submitted(page, "batterup", 0)
        self.assertTrue(ok)
        self.assertTrue(any("WARN" in m for m in self._dbg_msgs))


class TestSolveAttrGameWinGate(unittest.TestCase):
    """(2) + (4): an unverified guess never reaches the win check;
    answer echo is never win evidence; visible win + populated rows +
    feedback tiles counts."""

    def setUp(self):
        self._patches = []

        def patch(name, new):
            p = mock.patch.object(runner, name, new)
            p.start()
            self._patches.append(p)

        self._dbg_msgs = []
        patch("_dbg", lambda gid, msg: self._dbg_msgs.append(msg))
        patch("_close_modals", lambda page, _force=False: None)
        patch("_idle_drift", lambda page, *a, **k: None)
        patch("_settle", lambda page, *a, **k: None)
        patch("_attr_pool", lambda gid: ["Probe One", "Probe Two"])
        self._pick_calls = []
        patch("_type_country_guess", self._fake_pick)
        self._verify_calls = 0
        self._verify_fn = lambda page, gid, base: (True, _probe([], False, 1, 6))
        patch("_attr_verify_submitted", self._fake_verify)
        self._win_calls = 0
        self._win_result = (False, "NO win signal on page")
        patch("_strict_win", self._fake_win)
        self._probe_result = _probe([ANSWER], False, 1, 6)
        patch("_attr_post_submit_probe",
              lambda page: dict(self._probe_result))
        self._old_played = dict(runner._PLAYED)
        self._old_rejected = dict(runner._REJECTED)
        runner._PLAYED.clear()
        runner._REJECTED.clear()
        self.page = _FakePage()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        runner._PLAYED.clear()
        runner._PLAYED.update(self._old_played)
        runner._REJECTED.clear()
        runner._REJECTED.update(self._old_rejected)

    def _fake_pick(self, page, scope, name):
        self._pick_calls.append(name)
        return True, name

    def _fake_verify(self, page, gid, baseline):
        self._verify_calls += 1
        return self._verify_fn(page, gid, baseline)

    def _fake_win(self, page, answer=None):
        self._win_calls += 1
        return self._win_result

    def test_unsubmitted_answer_never_win_checked(self):
        # Even a positive win signal must not be consulted: the answer
        # never submitted, so there is nothing to win-check.
        self._verify_fn = lambda page, gid, base: (
            False, _probe([], False, 0, 0))
        self._win_result = (True, "win signal 'you won'")
        ok, ev = runner.solve_attr_game(self.page, ANSWER, "batterup")
        self.assertFalse(ok)
        self.assertIn("NOT submitted", ev)
        self.assertEqual(self._win_calls, 0)
        self.assertEqual(runner._PLAYED.get("batterup"), None)
        # Bounded retries: 3 attempts (1 pick + 2 re-picks) per guess.
        self.assertEqual(len(self._pick_calls), 9)

    def test_answer_echo_is_not_a_win(self):
        self.page.inner = f"{ANSWER}\nGuess the player"
        # Echo only: the board shows a populated row but ZERO scored
        # cells (nothing actually matched) - the all-attribute path
        # must not fire on an unscored board.
        self._probe_result = _probe([ANSWER], False, 1, 0)
        ok, ev = runner.solve_attr_game(self.page, ANSWER, "batterup")
        self.assertFalse(ok)
        self.assertIn("no win signal", ev)
        self.assertNotIn("answer echo", ev)
        self.assertEqual(self._win_calls, 2)  # initial + 5s recheck
        self.assertIn("(submitted=3", ev)

    def test_visible_win_plus_board_counts(self):
        self._win_result = (True, "win signal 'you won'")
        ok, ev = runner.solve_attr_game(self.page, ANSWER, "batterup")
        self.assertTrue(ok)
        self.assertIn("win signal 'you won'", ev)
        self.assertIn("board rows=1", ev)
        self.assertEqual(runner._PLAYED.get("batterup"),
                         ["Probe One", "Probe Two", ANSWER])

    def test_win_signal_without_populated_rows_ignored(self):
        self._win_result = (True, "win signal 'you won'")
        self._probe_result = _probe([], False, 0, 0)
        ok, ev = runner.solve_attr_game(self.page, ANSWER, "batterup")
        self.assertFalse(ok)
        self.assertIn("without populated guess rows", ev)
        self.assertEqual(self._win_calls, 2)

    def test_unsubmitted_probes_do_not_count(self):
        # Probes fail to submit (6 verify calls: 3 attempts x 2 probes);
        # the answer submits on its first attempt (7th call) and wins.
        def verify(page, gid, base):
            return (self._verify_calls > 6,
                    _probe([ANSWER], False, 1, 6))
        self._verify_fn = verify
        self._win_result = (True, "win signal 'you won'")
        ok, ev = runner.solve_attr_game(self.page, ANSWER, "batterup")
        self.assertTrue(ok)
        self.assertIn("after 1 submitted guesses", ev)
        self.assertIn(f"submitted=['{ANSWER}']", ev)
        self.assertEqual(runner._PLAYED.get("batterup"), [ANSWER])

    def test_all_attribute_tiles_need_board(self):
        self.page.cols = {"n": 6, "sample": "exact"}
        ok, ev = runner.solve_attr_game(self.page, ANSWER, "batterup")
        self.assertTrue(ok)
        self.assertIn("all-attribute win", ev)
        self.assertIn("board rows=1", ev)

    def test_all_attribute_tiles_ignored_without_board(self):
        self.page.cols = {"n": 6, "sample": "exact"}
        self._probe_result = _probe([], False, 0, 0)
        ok, ev = runner.solve_attr_game(self.page, ANSWER, "batterup")
        self.assertFalse(ok)
        self.assertIn("no win signal", ev)


if __name__ == "__main__":
    unittest.main()
