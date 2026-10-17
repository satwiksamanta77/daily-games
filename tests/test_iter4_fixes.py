"""Fix-loop iteration 4 regression tests (non-Worldle fixes).

Run:  python3 tests/test_iter4_fixes.py
All tests are synthetic/local: no network, no browser, no CI run needed.
"""
import os
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent.parent
sys.path.insert(0, str(HERE))

import quordle_parity as QP  # noqa: E402
import runner  # noqa: E402
import nerdle_solver as NS  # noqa: E402


def _ffmpeg(*args, timeout=300):
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", *args],
                       capture_output=True, text=True, timeout=timeout)
    assert r.returncode == 0, f"ffmpeg failed: {r.stderr[:300]}"
    return r


def _make_video(path, seconds, kind):
    """Synthetic gameplay clip. kind: 'blank' (static white) or 'live'
    (changing test pattern)."""
    if kind == "blank":
        src = ["-f", "lavfi", "-i",
               f"color=white:size=320x180:rate=24:duration={seconds}"]
    else:
        src = ["-f", "lavfi", "-i",
               f"testsrc2=size=320x180:rate=24:duration={seconds}"]
    _ffmpeg(*src, "-c:v", "libx264", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", "-an", str(path))


def _make_shot(path, kind):
    from PIL import Image
    if kind == "blank":
        Image.new("L", (320, 180), 255).save(path)
    else:
        rnd = random.Random(7)
        img = Image.new("L", (320, 180))
        img.putdata([rnd.randrange(256) for _ in range(320 * 180)])
        img.save(path)


class TestQcBlankFail(unittest.TestCase):
    """Task 8: blank gameplay input MUST fail visible-content QC,
    real gameplay MUST pass."""

    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="iter4_qc_"))

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def _qc(self, shot_kind, gp_kind, gp_seconds, solve_span):
        (self.td / "shot_win.png")
        _make_shot(self.td / "shot_win.png", shot_kind)
        gp = self.td / "gameplay.mp4"
        _make_video(gp, gp_seconds, gp_kind)
        return runner._qc_visible_content("test-gid", self.td,
                                          gameplay_path=gp,
                                          solve_span_s=solve_span)

    def test_blank_fails(self):
        ok, detail = self._qc("blank", "blank", 5, solve_span=30)
        self.assertFalse(ok, f"blank video must FAIL QC: {detail}")
        self.assertIn("fail", str(detail))

    def test_real_passes(self):
        ok, detail = self._qc("live", "live", 60, solve_span=59)
        self.assertTrue(ok, f"real gameplay must PASS QC: {detail}")

    def test_short_clip_fails_coverage(self):
        # Waffle rerun #3: 3.75 s clip for a 59 s solve must FAIL, even
        # though its frames are colourful (content check alone passes it).
        ok, detail = self._qc("live", "live", 3.75, solve_span=59)
        self.assertFalse(ok, f"3.75s/59s clip must FAIL QC: {detail}")
        self.assertIn("50%", str(detail.get("gameplay", {}).get("fail", "")))

    def test_full_clip_passes_coverage(self):
        ok, detail = self._qc("live", "live", 60, solve_span=59)
        self.assertTrue(ok, f"full clip must PASS QC: {detail}")
        self.assertGreaterEqual(detail["gameplay"]["coverage"], 0.5)


class TestAutocropCoverage(unittest.TestCase):
    """Task 2: autocrop must never cut gameplay below 50% of solve span."""

    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="iter4_crop_"))
        self.src = self.td / "src.mp4"
        _make_video(self.src, 60, "blank")

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_wrong_anchor_clamped(self):
        # Anchor at 55 s would keep 6 s of a 59 s solve -> guard clamps.
        segs, detail = QP._autocrop_keep_segments(
            str(self.src), first_typing_s=55.0, solve_span_s=59.0)
        kept = sum(b - a for a, b in segs)
        self.assertGreaterEqual(kept, 0.5 * 59.0, f"guard failed: {detail}")
        self.assertTrue(detail["guard_clamped"], f"guard did not fire: {detail}")

    def test_good_anchor_untouched(self):
        segs, detail = QP._autocrop_keep_segments(
            str(self.src), first_typing_s=15.0, solve_span_s=59.0)
        kept = sum(b - a for a, b in segs)
        self.assertGreaterEqual(kept, 0.5 * 59.0)
        self.assertFalse(detail["guard_clamped"])
        self.assertAlmostEqual(detail["leading_cut_s"], 14.0, delta=0.6)


class TestNerdleEquations(unittest.TestCase):
    """Task 3: equations = actually played, official recorded alongside."""

    def test_played_wins_over_official(self):
        rep = NS.equations_report(
            {"micro": ["8-7=1"], "classic": ["1+2+3=6"]},
            {"micro": ["8/2=4"], "classic": ["1+2+3=6"]})
        self.assertEqual(rep["equations"]["micro"], ["8-7=1"])
        self.assertEqual(rep["equations_official"]["micro"], ["8/2=4"])
        self.assertEqual(rep["equations"]["classic"], ["1+2+3=6"])
        self.assertIn("ACTUALLY PLAYED", rep["equations_note"])

    def test_unplayed_mode_reports_empty_not_official(self):
        rep = NS.equations_report({}, {"instant": ["1+9*5=46"]})
        self.assertEqual(rep["equations"]["instant"], [])
        self.assertEqual(rep["equations_official"]["instant"], ["1+9*5=46"])


class TestResultStepsPoolMerge(unittest.TestCase):
    """Task 7: played steps keep the planner's pool chain."""

    def test_pool_merged_on_match(self):
        orig_steps = runner._steps
        runner._steps = lambda gid, aval: [
            {"turn": 1, "guess": "[1, 2, 3]", "pattern": "XXG",
             "pool_before": 6840, "pool_after": 544},
            {"turn": 2, "guess": "[4, 5, 6]", "pattern": "GGG",
             "pool_before": 544, "pool_after": 1}]
        try:
            runner._PLAYED["colorfle"] = [[1, 2, 3], [4, 5, 6]]
            steps = runner._result_steps("colorfle", "[1, 2, 3]")
        finally:
            runner._steps = orig_steps
            runner._PLAYED.pop("colorfle", None)
        self.assertEqual(steps[0]["pool_before"], 6840)
        self.assertEqual(steps[0]["pool_after"], 544)
        self.assertEqual(steps[1]["pool_after"], 1)

    def test_no_merge_on_mismatch(self):
        orig_steps = runner._steps
        runner._steps = lambda gid, aval: [
            {"turn": 1, "guess": "[9, 9, 9]", "pattern": "",
             "pool_before": 6840, "pool_after": 544}]
        try:
            runner._PLAYED["colorfle"] = [[1, 2, 3]]
            steps = runner._result_steps("colorfle", "[1, 2, 3]")
        finally:
            runner._steps = orig_steps
            runner._PLAYED.pop("colorfle", None)
        self.assertIsNone(steps[0]["pool_before"])


class TestFirstActionAnchor(unittest.TestCase):
    """Task 9/10: first-action anchor is recording-relative + logged."""

    def test_record_first_action(self):
        import time as _time
        gid = "test-anchor-gid"
        runner._RUN_T0[gid] = _time.time() - 20.0
        runner._REC_T0[gid] = _time.time() - 17.0
        runner._FIRST_TYPE.pop(gid, None)
        runner._FIRST_TYPE_REC.pop(gid, None)
        runner._FIRST_ACTION_LABEL.pop(gid, None)
        try:
            runner._record_first_action(gid, "swap")
            self.assertIn(gid, runner._FIRST_TYPE)
            # Recording started 3 s after run START: the recording anchor
            # must be ~3 s SMALLER than the START-relative value, or the
            # autocrop cut lands inside the solve (marveldle/worgle).
            self.assertAlmostEqual(
                runner._FIRST_TYPE[gid] - runner._FIRST_TYPE_REC[gid],
                3.0, delta=0.6)
            self.assertEqual(runner._FIRST_ACTION_LABEL[gid], "swap")
            # Second call is a no-op (first action wins).
            runner._record_first_action(gid, "typing")
            self.assertEqual(runner._FIRST_ACTION_LABEL[gid], "swap")
        finally:
            for d in (runner._RUN_T0, runner._REC_T0, runner._FIRST_TYPE,
                      runner._FIRST_TYPE_REC, runner._FIRST_ACTION_LABEL):
                d.pop(gid, None)
            shutil.rmtree(HERE / "videos" / gid, ignore_errors=True)


class TestRemovedRetypeAndCountryle(unittest.TestCase):
    """Tasks 6 + 11: source-level regression guards for removed code."""

    def test_betweenle_no_retype_after_confirmation(self):
        src = (HERE / "runner.py").read_text(encoding="utf-8")
        marker = "confirmed submitted by game counters"
        i = src.index(marker)
        window = src[i:i + 900]
        self.assertNotIn("_type_betweenle_via_keys(page, g)", window)
        self.assertNotIn("_submit_betweenle(page)", window)
        # Iter 11: the confirmed-submitted path never retypes (wording
        # "never retyping"); the stall breaker is ONE Enter resend,
        # not a letter retype.
        self.assertIn("never retyping", window)
        # Iter 11: NO letter-retype call remains anywhere — the stall
        # path resends Enter once, never retypes the word.
        self.assertEqual(src.count("_type_betweenle_via_keys(page, g)"), 0)

    def test_countryle_no_next_fallback(self):
        src = (HERE / "runner.py").read_text(encoding="utf-8")
        self.assertNotIn("button.next, .next, [class*=next i]", src)
        self.assertNotIn("_COUNTRYLE_BTN_EXCEPT_JS", src)
        self.assertIn("_COUNTRYLE_SLIDE_FP_JS", src)
        self.assertIn("PLAY|START|CONTINUE|GO|GOT IT", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
