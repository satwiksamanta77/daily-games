"""Fix-loop iteration 24 tests — Workstream C: Batterup CMP + Marveldle
QC/card (ANALYSIS21 RC4 + RC6).

RC4 (Batterup, P0): end-of-run frame f0045 shows the "We value your
privacy" CMP dialog covering the board, the suggestion dropdown still
open on "Vladimir Guerrero Jr", zero populated rows. Log: guess 1
picked OK but `submit verify: populated_rows=0, dropdown_open=True,
verified=False after 9.0s`; re-pick and guesses 2-3 all
`ElementHandle.click: Timeout 3000ms exceeded`; `submitted=0`.
Root cause: the one-shot _close_modals sweep at solve start never
re-runs (dedup via _qp_modals_closed), so a CMP that re-appears
(ad-iframe navigation) is never dismissed, and submit verification
just blindly re-picked into the covered board. Fix: re-check consent
before every pick, after every submit, and on the RC4 signature
(unverified submit + dropdown open + no row growth); close the
dropdown explicitly before re-picking.

RC6 (Marveldle, P2): `solved=true` with a good final, but QC
`ok=false`: `gameplay < 50% of solve span (53.3s < 50% of 106.7s)`.
Root cause: the coverage denominator was the whole START->shot_win
span, which includes pre-play load/menu time no gameplay clip can
cover. Fix: compare gameplay against the gameplay-phase window
(span minus time to first player action), falling back to the whole
span when the phase is unknown. And the breakdown card showed
"0 green rows" for Marveldle/Countryle on won cards: their wins are
attribute/banner wins, not green-row wins, so the stat is dropped
for attribute/banner games.

All tests are synthetic/local: fake pages, monkeypatched helpers,
mocked subprocess for the QC coverage branch — no browser, no
network, no live play.

Run:  python3 -m unittest tests.test_iter24_batterup_marveldle -v
"""

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

import daily_parity as DP
import runner as R


class _DummyPage:
    """Minimal page stand-in: only the waits/clicks the patched code paths
    still touch (all probe/dismiss/consent helpers are monkeypatched)."""

    def __init__(self):
        self.pressed = []
        self.keyboard = self

    def wait_for_timeout(self, ms):
        return None

    def press(self, key):
        self.pressed.append(key)

    def query_selector(self, sel):
        return None

    def evaluate(self, script, *args):
        return ""


class _PatchDbgMixin:
    def setUp(self):
        self._dbg_patch = mock.patch.object(R, "_dbg", lambda gid, msg: None)
        self._dbg_patch.start()
        self.addCleanup(self._dbg_patch.stop)


class TestConsentRecheck(_PatchDbgMixin, unittest.TestCase):
    def test_recheck_clicks_cmp_and_returns_hit(self):
        calls = []
        with mock.patch.object(R, "_dismiss_consent",
                               side_effect=lambda p: calls.append("dismiss") or "consent-accept:accept all"), \
                mock.patch.object(R, "_close_modals") as close_modals:
            hit = R._attr_consent_recheck(_DummyPage(), "batterup", "test")
        self.assertEqual(hit, "consent-accept:accept all")
        self.assertEqual(calls, ["dismiss"])
        close_modals.assert_not_called()

    def test_recheck_full_also_forces_modal_sweep(self):
        with mock.patch.object(R, "_dismiss_consent", return_value=None), \
                mock.patch.object(R, "_close_modals") as close_modals:
            hit = R._attr_consent_recheck(_DummyPage(), "batterup", "test",
                                          full=True)
        self.assertIsNone(hit)
        close_modals.assert_called_once_with(mock.ANY, _force=True)

    def test_recheck_never_raises(self):
        with mock.patch.object(R, "_dismiss_consent",
                               side_effect=RuntimeError("boom")), \
                mock.patch.object(R, "_close_modals",
                                  side_effect=RuntimeError("boom")):
            hit = R._attr_consent_recheck(_DummyPage(), "batterup", "test",
                                          full=True)
        self.assertIsNone(hit)

    def test_consent_js_recognises_privacy_containers(self):
        # The iter-23 dialog is a "We value your privacy" CMP: the
        # accept-button finder must treat privacy containers as CMP
        # containers, otherwise _dismiss_consent can never click it.
        self.assertIn("privacy", R._CONSENT_ACCEPT_JS)


class TestSubmitVerifyConsentRecheck(_PatchDbgMixin, unittest.TestCase):
    _RC4_PROBE = {"rows": [], "dropdown_open": True, "populated": False,
                  "populated_rows": 0, "feedback_tiles": 0,
                  "visible_exact_cells": 0}
    _GROWN_PROBE = {"rows": ["Vladimir Guerrero Jr | ..."],
                    "dropdown_open": True, "populated": True,
                    "populated_rows": 1, "feedback_tiles": 3,
                    "visible_exact_cells": 0}
    _CLOSED_PROBE = {"rows": [], "dropdown_open": False, "populated": False,
                     "populated_rows": 0, "feedback_tiles": 0,
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
                _DummyPage(), "batterup", baseline, timeout_s=0.05)
        return verified, pr, rechecks

    def test_rc4_signature_triggers_consent_recheck(self):
        # ANALYSIS21 RC4 exact shape: unverified submit, dropdown open,
        # zero populated rows -> consent re-check (full sweep), not a
        # blind re-pick.
        verified, _pr, rechecks = self._run_verify(self._RC4_PROBE)
        self.assertFalse(verified)
        self.assertEqual(len(rechecks), 1)
        why, full = rechecks[0]
        self.assertIn("dropdown open", why)
        self.assertTrue(full)

    def test_verified_submit_does_not_recheck(self):
        verified, _pr, rechecks = self._run_verify(self._GROWN_PROBE)
        self.assertTrue(verified)
        self.assertEqual(rechecks, [])

    def test_no_growth_dropdown_closed_does_not_recheck(self):
        # Different signature (dropdown already closed): the CMP
        # re-check is not triggered — the failure is not the RC4 shape.
        verified, _pr, rechecks = self._run_verify(self._CLOSED_PROBE)
        self.assertFalse(verified)
        self.assertEqual(rechecks, [])


class TestSolveAttrGameWiring(_PatchDbgMixin, unittest.TestCase):
    _BOARD_PROBE = {"rows": ["Alec Burleson | ..."], "dropdown_open": False,
                    "populated": True, "populated_rows": 1,
                    "feedback_tiles": 3, "visible_exact_cells": 0}

    def setUp(self):
        super().setUp()
        R._PLAYED.pop("batterup", None)
        R._REJECTED.pop("batterup", None)
        self.addCleanup(lambda: (R._PLAYED.pop("batterup", None),
                                  R._REJECTED.pop("batterup", None)))

    def _run_solve(self, pick_side_effect, verify_side_effect):
        rechecks = []
        soft_closes = []
        with mock.patch.object(R, "_close_modals"), \
                mock.patch.object(R, "_idle_drift"), \
                mock.patch.object(R, "_attr_pool", return_value=[]), \
                mock.patch.object(
                    R, "_attr_consent_recheck",
                    side_effect=lambda p, g, w, full=False: rechecks.append(
                        (w, full)) or None), \
                mock.patch.object(
                    R, "_attr_dismiss_dropdown_soft",
                    side_effect=lambda p: soft_closes.append(True)), \
                mock.patch.object(R, "_type_country_guess",
                                  side_effect=pick_side_effect), \
                mock.patch.object(R, "_settle"), \
                mock.patch.object(R, "_attr_verify_submitted",
                                  side_effect=verify_side_effect), \
                mock.patch.object(R, "_attr_post_submit_probe",
                                  return_value=dict(self._BOARD_PROBE)), \
                mock.patch.object(R, "_strict_win",
                                  return_value=(True, "you won")):
            solved, evidence = R.solve_attr_game(
                _DummyPage(), "Alec Burleson", "batterup")
        return solved, evidence, rechecks, soft_closes

    def test_pick_failure_retries_after_consent_recheck(self):
        # Pick 1 fails as if a CMP covered the board (click timeout);
        # the loop re-checks consent, closes the dropdown, and the
        # re-pick succeeds -> solved.
        solved, _ev, rechecks, soft_closes = self._run_solve(
            [(False, "exception: ElementHandle.click: Timeout 3000ms"),
             (True, "Alec Burleson")],
            [(True, dict(self._BOARD_PROBE))])
        self.assertTrue(solved)
        whys = [w for w, _f in rechecks]
        self.assertTrue(any("pick failed" in w for w in whys),
                        f"no pick-failure re-check in {whys}")
        self.assertGreaterEqual(len(soft_closes), 1)

    def test_submit_retry_rechecks_consent_and_closes_dropdown(self):
        # First submit unverified (RC4 shape); the retry re-checks
        # consent and closes the dropdown before re-picking, and the
        # second submit verifies -> solved.
        solved, _ev, rechecks, soft_closes = self._run_solve(
            [(True, "Alec Burleson"), (True, "Alec Burleson")],
            [(False, {"populated_rows": 0, "dropdown_open": True,
                      "feedback_tiles": 0}),
             (True, dict(self._BOARD_PROBE))])
        self.assertTrue(solved)
        whys = [w for w, _f in rechecks]
        self.assertTrue(any("before re-pick" in w for w in whys),
                        f"no pre-re-pick re-check in {whys}")
        self.assertGreaterEqual(len(soft_closes), 1)


class TestGameplayPhase(unittest.TestCase):
    def test_marveldle_shape(self):
        # ANALYSIS21 numbers: span 106.7s, first action at 70.5s ->
        # gameplay phase 36.2s, which the 53.3s clip covers.
        self.assertEqual(R._gameplay_phase_s(106.7, 70.5), 36.2)

    def test_missing_inputs_fall_back(self):
        self.assertIsNone(R._gameplay_phase_s(None, 70.5))
        self.assertIsNone(R._gameplay_phase_s(106.7, None))
        self.assertIsNone(R._gameplay_phase_s(None, None))

    def test_nonpositive_phase_falls_back(self):
        self.assertIsNone(R._gameplay_phase_s(50.0, 60.0))
        self.assertIsNone(R._gameplay_phase_s(50.0, 50.0))

    def test_string_inputs_coerced(self):
        self.assertEqual(R._gameplay_phase_s("106.7", "70.5"), 36.2)
        self.assertIsNone(R._gameplay_phase_s("bad", 1))


class TestQCCoveragePhase(_PatchDbgMixin, unittest.TestCase):
    def _run_qc(self, ffprobe_dur, **kwargs):
        def _fake_run(args, **kw):
            if args and args[0] == "ffprobe":
                return types.SimpleNamespace(returncode=0,
                                             stdout=f"{ffprobe_dur}\n",
                                             stderr="")
            return types.SimpleNamespace(returncode=0, stdout="",
                                          stderr="")

        with tempfile.TemporaryDirectory() as td:
            gp = Path(td) / "gameplay_1080.mp4"
            gp.write_bytes(b"fake")
            with mock.patch("subprocess.run", side_effect=_fake_run):
                ok, detail = R._qc_visible_content(
                    "marveldle", Path(td), gameplay_path=gp, **kwargs)
        return ok, detail

    def test_marveldle_final_passes_with_phase(self):
        # ANALYSIS21 exact numbers: 53.3s gameplay, 106.7s span,
        # 36.2s gameplay phase -> coverage 1.472 vs the phase, PASS.
        ok, detail = self._run_qc(
            53.3, solve_span_s=106.7, gameplay_phase_s=36.2)
        self.assertTrue(ok, detail)
        self.assertEqual(detail["gameplay"]["gameplay_phase_s"], 36.2)
        self.assertEqual(detail["gameplay"]["coverage"], 1.472)
        self.assertNotIn("fail", detail["gameplay"])

    def test_same_clip_fails_on_whole_span_without_phase(self):
        # The iter-23 misfire, reproduced: without a phase the same
        # 53.3s clip fails against the 106.7s whole span (53.3 < 53.35).
        ok, detail = self._run_qc(53.3, solve_span_s=106.7)
        self.assertFalse(ok)
        self.assertEqual(
            detail["gameplay"]["fail"],
            "gameplay < 50% of solve span (53.3s < 50% of 106.7s)")

    def test_truly_truncated_clip_still_fails_with_phase(self):
        # The phase denominator must not rubber-stamp a clip that
        # misses most of the solve: 10s of 36.2s phase still fails.
        ok, detail = self._run_qc(
            10.0, solve_span_s=106.7, gameplay_phase_s=36.2)
        self.assertFalse(ok)
        self.assertEqual(
            detail["gameplay"]["fail"],
            "gameplay < 50% of gameplay phase (10.0s < 50% of 36.2s)")


class TestFactsCardAttributeGames(unittest.TestCase):
    _STEPS = [{"turn": 1, "guess": "8-Ball", "pattern": ""},
              {"turn": 2, "guess": "Living Tribunal", "pattern": ""},
              {"turn": 3, "guess": "Glob", "pattern": ""}]

    def _render(self, game, steps=None):
        with tempfile.TemporaryDirectory() as td:
            out = str(Path(td) / "facts.png")
            stats = DP.generate_facts(out, game, "October 18, 2026", "Glob",
                                      steps if steps is not None else self._STEPS,
                                      scoring=True)
            return stats

    def test_marveldle_card_has_no_green_rows(self):
        stats = self._render("Marveldle")
        self.assertEqual(stats, [("3", "guesses played"),
                                 ("1", "final answer")])

    def test_countryle_card_has_no_green_rows(self):
        steps = [{"turn": 1, "guess": "Oman", "pattern": ""},
                 {"turn": 2, "guess": "Botswana", "pattern": ""}]
        stats = self._render("Countryle", steps)
        self.assertEqual(stats, [("2", "guesses played"),
                                 ("1", "final answer")])

    def test_batterup_card_has_no_green_rows(self):
        stats = self._render("Batterup")
        self.assertEqual(stats, [("3", "guesses played"),
                                 ("1", "final answer")])

    def test_letter_game_still_shows_green_rows(self):
        # Control: a per-letter game with a whole-green row keeps the
        # stat — the drop is attribute/banner games only.
        steps = [{"turn": 1, "guess": "SAUCE", "pattern": "XXYXX"},
                 {"turn": 2, "guess": "LUNGO", "pattern": "GGGGG"}]
        stats = self._render("Phoodle", steps)
        self.assertIn(("1", "green rows"), stats)


if __name__ == "__main__":
    unittest.main()
