"""Fix-loop iteration 25 tests — Workstream B: Batterup suggestion-click
(ANALYSIS22 RC-new-2).

The iter-24 failure, frame-proven: the bot typed the name, the
suggestion dropdown showed the name, but the suggestion row was NEVER
selected — it pressed Enter, the dropdown stayed open
(dropdown_open=True on all 6 verifies), and zero rows populated.
"Pick ok=True" counted typing + suggestion appearance, not a
selection, because `_type_country_guess` verified only that the
input held the typed name (which is true whether or not the row was
selected). Guess 2 ("Jesus Tinoco") additionally produced no
suggestion at all. The iter-23 CMP theory is refuted: zero CMP lines
and no consent dialog in any gameplay frame.

Fix (runner.py): for batterup, pick ok now means SELECTED — the
dropdown must be closed with the input holding exactly the picked
name (checked by `_attr_selection_state`), otherwise the row is
re-selected via a real Playwright click / full mouse events /
keyboard (`_attr_reselect`). A submit that leaves dropdown_open=True
+ 0 populated rows is classified "selection-never-happened"
(`_attr_submit_failure_kind`) and triggers a re-select, not a blind
re-pick. A no-suggestion probe is replaced in place by a
proven-suggestion name (Guerrero Jr / Burleson — their suggestion
rows are visible in the iter-24 frames). The CMP re-check stays as a
fallback only. "Jesus Tinoco" is denylisted from the batterup pool.

All tests are synthetic/local: fake pages, monkeypatched helpers —
no browser, no network, no live play.

Run:  python3 -m unittest tests.test_iter25_batterup -v
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

import runner as R


class _FakeRow:
    """Suggestion-row stand-in for the Playwright-click path."""

    def __init__(self, text):
        self._text = text
        self.clicked = False

    def inner_text(self):
        return self._text

    def scroll_into_view_if_needed(self, timeout=None):
        return None

    def bounding_box(self):
        return None  # no cursor-event bookkeeping needed in tests

    def click(self, timeout=None):
        self.clicked = True


class _FakePage:
    """Minimal page stand-in recording key presses / button queries."""

    def __init__(self, evaluate_result="", rows=None):
        self._evaluate_result = evaluate_result
        self._rows = rows or []
        self.pressed = []
        self.keyboard = self

    def wait_for_timeout(self, ms):
        return None

    def press(self, key):
        self.pressed.append(key)

    def query_selector(self, sel):
        return None

    def query_selector_all(self, sel):
        return list(self._rows)

    def evaluate(self, script, *args):
        if isinstance(self._evaluate_result, Exception):
            raise self._evaluate_result
        return self._evaluate_result


class _PatchDbgMixin:
    def setUp(self):
        self._dbg_patch = mock.patch.object(R, "_dbg", lambda gid, msg: None)
        self._dbg_patch.start()
        self.addCleanup(self._dbg_patch.stop)


class TestSelectionState(_PatchDbgMixin, unittest.TestCase):
    """(a) pick ok requires a selection event, not mere appearance."""

    def test_open_dropdown_with_name_in_input_is_not_selected(self):
        # The exact iter-24 shape: input holds the typed name, the
        # dropdown is still open -> typed, NOT selected.
        page = _FakePage({"input_value": "Vladimir Guerrero Jr",
                          "suggestion_open": True})
        st = R._attr_selection_state(page, "Vladimir Guerrero Jr")
        self.assertIs(st["selected"], False)
        self.assertTrue(st["suggestion_open"])

    def test_closed_dropdown_with_exact_input_is_selected(self):
        page = _FakePage({"input_value": "Alec Burleson",
                          "suggestion_open": False})
        st = R._attr_selection_state(page, "Alec Burleson")
        self.assertIs(st["selected"], True)

    def test_closed_dropdown_with_partial_input_is_not_selected(self):
        # Mid-typing state (frame f0085: "Alec Burles" typed) is not
        # a selection even with the dropdown closed.
        page = _FakePage({"input_value": "Alec Burles",
                          "suggestion_open": False})
        st = R._attr_selection_state(page, "Alec Burleson")
        self.assertIs(st["selected"], False)

    def test_unavailable_probe_is_none_not_false(self):
        page = _FakePage("")  # fake evaluate returning nothing
        st = R._attr_selection_state(page, "Alec Burleson")
        self.assertIsNone(st["selected"])
        page2 = _FakePage(RuntimeError("boom"))
        st2 = R._attr_selection_state(page2, "Alec Burleson")
        self.assertIsNone(st2["selected"])


class TestSubmitFailureKind(unittest.TestCase):
    def test_iter24_shape_is_selection_never_happened(self):
        probe = {"populated_rows": 0, "dropdown_open": True,
                 "feedback_tiles": 0}
        self.assertEqual(R._attr_submit_failure_kind(probe, 0),
                         "selection-never-happened")

    def test_growth_is_verified(self):
        probe = {"populated_rows": 1, "dropdown_open": True,
                 "feedback_tiles": 3}
        self.assertEqual(R._attr_submit_failure_kind(probe, 0),
                         "verified")

    def test_closed_dropdown_no_growth_is_plain_no_growth(self):
        probe = {"populated_rows": 0, "dropdown_open": False,
                 "feedback_tiles": 0}
        self.assertEqual(R._attr_submit_failure_kind(probe, 0),
                         "no-row-growth")


class TestReselect(_PatchDbgMixin, unittest.TestCase):
    def test_playwright_click_selects(self):
        row = _FakeRow("Alec Burleson")
        page = _FakePage({"input_value": "Alec Burleson",
                          "suggestion_open": False}, rows=[row])
        ok, detail = R._attr_reselect(page, "Alec Burleson")
        self.assertTrue(ok)
        self.assertIn("playwright-click", detail)
        self.assertTrue(row.clicked)

    def test_reselect_fails_when_selection_never_lands(self):
        # Every method runs, selection stays False throughout.
        page = _FakePage({"input_value": "Alec Burleson",
                          "suggestion_open": True}, rows=[])
        ok, detail = R._attr_reselect(page, "Alec Burleson")
        self.assertFalse(ok)
        self.assertIn("never landed", detail)

    def test_reselect_stops_when_probe_unavailable(self):
        page = _FakePage("")
        ok, detail = R._attr_reselect(page, "Alec Burleson")
        self.assertFalse(ok)
        self.assertIn("probe unavailable", detail)


class TestVerifyFailureKind(_PatchDbgMixin, unittest.TestCase):
    _RC4_PROBE = {"rows": [], "dropdown_open": True, "populated": False,
                  "populated_rows": 0, "feedback_tiles": 0,
                  "visible_exact_cells": 0}
    _GROWN_PROBE = {"rows": ["Vladimir Guerrero Jr | ..."],
                    "dropdown_open": True, "populated": True,
                    "populated_rows": 1, "feedback_tiles": 3,
                    "visible_exact_cells": 0}

    def _run_verify(self, probe, baseline=0):
        rechecks = []
        with mock.patch.object(R, "_attr_post_submit_probe",
                               return_value=dict(probe)), \
                mock.patch.object(R, "_attr_dismiss_suggestion"), \
                mock.patch.object(
                    R, "_attr_consent_recheck",
                    side_effect=lambda p, g, w, full=False: rechecks.append(
                        (w, full)) or None):
            verified, pr = R._attr_verify_submitted(
                _FakePage(), "batterup", baseline, timeout_s=0.05)
        return verified, pr, rechecks

    def test_iter24_shape_classified_selection_never_happened(self):
        verified, pr, _rechecks = self._run_verify(self._RC4_PROBE)
        self.assertFalse(verified)
        self.assertEqual(pr["failure_kind"], "selection-never-happened")

    def test_cmp_recheck_kept_as_fallback(self):
        # CMP is no longer the presumed cause, but the re-check still
        # runs on the dropdown-open + no-growth signature.
        _v, _pr, rechecks = self._run_verify(self._RC4_PROBE)
        self.assertEqual(len(rechecks), 1)

    def test_grown_submit_classified_verified(self):
        verified, pr, _rechecks = self._run_verify(self._GROWN_PROBE)
        self.assertTrue(verified)
        self.assertEqual(pr["failure_kind"], "verified")


class TestSolveAttrGameSelection(_PatchDbgMixin, unittest.TestCase):
    _BOARD_PROBE = {"rows": ["Alec Burleson | ..."], "dropdown_open": False,
                    "populated": True, "populated_rows": 1,
                    "feedback_tiles": 3, "visible_exact_cells": 0}

    def setUp(self):
        super().setUp()
        R._PLAYED.pop("batterup", None)
        R._REJECTED.pop("batterup", None)
        self.addCleanup(lambda: (R._PLAYED.pop("batterup", None),
                                  R._REJECTED.pop("batterup", None)))

    def _base_patches(self, page, click_submit):
        # Iter 29: Batterup picks via _batterup_click_submit
        # (click = submit, RE Q5). The old _type_country_guess +
        # selection-state + reselect + verify-submit + settle steps
        # no longer run for Batterup.
        return [
            mock.patch.object(R, "_close_modals"),
            mock.patch.object(R, "_idle_drift"),
            mock.patch.object(R, "_attr_pool", return_value=[]),
            mock.patch.object(R, "_attr_consent_recheck",
                              return_value=None),
            mock.patch.object(R, "_batterup_click_submit",
                              side_effect=click_submit),
            mock.patch.object(R, "_attr_post_submit_probe",
                              return_value=dict(self._BOARD_PROBE)),
            mock.patch.object(R, "_strict_win",
                              return_value=(True, "you won")),
        ]

    def _run(self, page, patches):
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return R.solve_attr_game(page, "Alec Burleson", "batterup")

    def test_click_submit_triple_failure_is_pick_failure(self):
        # Iter 29 supersedes
        # test_pick_ok_without_selection_is_pick_failure: there is
        # no 'selected' state on Batterup (RE Q5) — a pick counts
        # only when the click-submit triple holds. A triple failure
        # -> the pick does NOT count, nothing is submitted, the
        # answer pick fails honestly.
        page = _FakePage()
        patches = self._base_patches(
            page,
            click_submit=lambda p, name, base: (
                False, "click-submit triple not held", {}))
        solved, evidence = self._run(page, patches)
        self.assertFalse(solved)
        self.assertIn("pick failed", evidence)
        self.assertEqual(page.pressed, [])
        self.assertNotIn("Alec Burleson", R._PLAYED.get("batterup", []))

    def test_click_submit_triple_held_counts_as_submitted(self):
        # Iter 29 supersedes
        # test_pick_ok_with_failed_selection_then_reselect_succeeds:
        # the click-submit triple held -> the pick counts as
        # submitted (click = submit, RE Q5) and the solve proceeds.
        page = _FakePage()
        patches = self._base_patches(
            page,
            click_submit=lambda p, name, base: (
                True, "click-submit triple held", {}))
        solved, _ev = self._run(page, patches)
        self.assertTrue(solved)
        self.assertIn("Alec Burleson", R._PLAYED.get("batterup", []))

    def test_failed_click_submit_never_submits(self):
        # Iter 29 supersedes
        # test_no_submit_without_confirmed_selection: there is no
        # separate submit step on Batterup (click = submit, RE Q5)
        # — so a failed click-submit means Enter is NEVER pressed,
        # no button is clicked, nothing is counted as submitted.
        page = _FakePage()
        patches = self._base_patches(
            page,
            click_submit=lambda p, name, base: (
                False, "click-submit triple not held", {}))
        solved, _ev = self._run(page, patches)
        self.assertFalse(solved)
        self.assertEqual(page.pressed, [])
        self.assertNotIn("Alec Burleson", R._PLAYED.get("batterup", []))

    def test_failed_click_submit_retries_once_without_retype(self):
        # Iter 29 supersedes
        # test_unverified_submit_reselects_without_repick: there is
        # no separate submit/reselect step on Batterup (click =
        # submit, RE Q5) — a failed click-submit gets exactly ONE
        # re-click-submit (after a consent re-check); the name is
        # never retyped (_type_country_guess never runs).
        page = _FakePage()
        click_submit = mock.Mock(side_effect=[
            (False, "click-submit triple not held", {}),
            (True, "click-submit triple held", {})])
        consent_calls = []
        patches = self._base_patches(page, click_submit=click_submit)
        # Record consent re-checks: the retry takes the
        # click-submit-failure path.
        patches[3] = mock.patch.object(
            R, "_attr_consent_recheck",
            side_effect=lambda p, g, w, full=False: consent_calls.append(w))
        patches.append(mock.patch.object(
            R, "_type_country_guess",
            side_effect=AssertionError("Batterup must not retype")))
        solved, _ev = self._run(page, patches)
        self.assertTrue(solved)
        self.assertEqual(click_submit.call_count, 2)
        self.assertTrue(any("click-submit failed" in w
                            for w in consent_calls))
        self.assertIn("Alec Burleson", R._PLAYED.get("batterup", []))


class TestTinocoReplacement(_PatchDbgMixin, unittest.TestCase):
    def test_tinoco_never_in_batterup_pool(self):
        pool = R._attr_pool("batterup")
        self.assertNotIn("Jesus Tinoco", pool)
        self.assertIn("Vladimir Guerrero Jr", pool)
        self.assertIn("Alec Burleson", pool)

    def test_tinoco_never_in_plan(self):
        seq, _steps = R._plan_for("batterup", "Aaron Judge")
        self.assertNotIn("Jesus Tinoco", seq)

    def test_no_suggestion_probe_replaced_by_proven_name(self):
        # (d) A probe with no suggestions (the Tinoco shape — here a
        # generic no-suggestion name, since Tinoco himself is
        # denylisted from probes outright) is replaced in place by a
        # proven-suggestion name; the failed probe is never submitted,
        # the answer is never replaced.
        page = _FakePage()
        R._PLAYED.pop("batterup", None)
        R._REJECTED.pop("batterup", None)
        self.addCleanup(lambda: (R._PLAYED.pop("batterup", None),
                                  R._REJECTED.pop("batterup", None)))

        # Iter 29: the pick is a click-submit (RE Q5) — a
        # "no suggestions" click-submit failure triggers the same
        # replacement path as the old pick failure did.
        def _click_submit(p, name, base):
            if name == "No Such Player":
                return False, "no suggestions (dom nodes=41)", {}
            return True, "click-submit triple held", {}

        patches = [
            mock.patch.object(R, "_close_modals"),
            mock.patch.object(R, "_idle_drift"),
            mock.patch.object(R, "_attr_pool", return_value=[]),
            mock.patch.object(R, "_attr_consent_recheck",
                              return_value=None),
            mock.patch.object(R, "_batterup_click_submit",
                              side_effect=_click_submit),
            mock.patch.object(
                R, "_attr_post_submit_probe",
                return_value={"rows": ["x"], "dropdown_open": False,
                              "populated": True, "populated_rows": 1,
                              "feedback_tiles": 3,
                              "visible_exact_cells": 0}),
            mock.patch.object(R, "_strict_win",
                              return_value=(True, "you won")),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        solved, _ev = R.solve_attr_game(
            page, "Alec Burleson", "batterup",
            api_probes=("No Such Player", "Aaron Judge"))
        self.assertTrue(solved)
        played = R._PLAYED.get("batterup", [])
        self.assertNotIn("No Such Player", played)
        # The replacement is the first proven name that is not the
        # answer (Guerrero Jr; Burleson is the answer here).
        self.assertIn("Vladimir Guerrero Jr", played)
        self.assertIn("Aaron Judge", played)
        self.assertIn("Alec Burleson", played)
        self.assertIn("No Such Player", R._REJECTED.get("batterup", []))

    def test_tinoco_api_probe_played_not_filtered(self):
        # Iter 29 supersedes test_tinoco_api_probe_filtered_outright:
        # the denylist is gone (RE Q2 refuted the batter-only
        # hypothesis — Tinoco IS in the suggestion index). A
        # caller-passed Tinoco probe is a valid guess and is now
        # played, not filtered.
        page = _FakePage()
        R._PLAYED.pop("batterup", None)
        R._REJECTED.pop("batterup", None)
        self.addCleanup(lambda: (R._PLAYED.pop("batterup", None),
                                  R._REJECTED.pop("batterup", None)))
        picked = []

        def _click_submit(p, name, base):
            picked.append(name)
            return True, "click-submit triple held", {}

        patches = [
            mock.patch.object(R, "_close_modals"),
            mock.patch.object(R, "_idle_drift"),
            mock.patch.object(R, "_attr_pool",
                              return_value=["Aaron Judge"]),
            mock.patch.object(R, "_attr_consent_recheck",
                              return_value=None),
            mock.patch.object(R, "_batterup_click_submit",
                              side_effect=_click_submit),
            mock.patch.object(
                R, "_attr_post_submit_probe",
                return_value={"rows": ["x"], "dropdown_open": False,
                              "populated": True, "populated_rows": 1,
                              "feedback_tiles": 3,
                              "visible_exact_cells": 0}),
            mock.patch.object(R, "_strict_win",
                              return_value=(True, "you won")),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        solved, _ev = R.solve_attr_game(
            page, "Alec Burleson", "batterup",
            api_probes=("Jesus Tinoco", "Aaron Judge"))
        self.assertTrue(solved)
        self.assertIn("Jesus Tinoco", picked)
        self.assertIn("Jesus Tinoco",
                      R._PLAYED.get("batterup", []))


if __name__ == "__main__":
    unittest.main()
