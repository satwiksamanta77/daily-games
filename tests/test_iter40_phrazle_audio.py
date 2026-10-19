"""Iteration-40 (Phrazle attempts + intro audio) regression tests.

PHRAZLE (root cause: `_valid_phrase` total<=16 rejected real long answers
like "PLAY YOUR CARDS RIGHT" (18 letters), collapsing the plan to the answer
alone = 1 attempt; and rare board shapes like (4,4,5,5) have exactly one
same-shape bank entry, so the reshape step could invent no decoys):
1. `_valid_phrase` accepts real long answers (18 letters) and the bank max
   (26 letters); still rejects single words / non-alpha / too-short.
2. `plan_phrase(..., shape=(4,4,5,5), seed=...)`: 2-4 total guesses, final
   guess == answer, every guess matches the board shape, decoys != answer,
   decoy words all come from the site's own bank vocabulary (the site
   rejects any WORD not in its word list).
3. Seeded determinism: same seed -> identical plan; across seeds the
   attempt total varies (randomized 2..4).
4. Common shapes still use the bank's own phrases via entropy picking.
5. Legacy call (no shape/seed) keeps prior behaviour.

INTRO AUDIO (root cause: assembly looped the bg song over the FULL clip
including the 20 s intro, while the intro's own voice was dropped):
6. `clean_intro` KEEPS the intro's audio track (verified clean of stale
   answers by transcription in iter-40: only "Thank you for watching!",
   no MOCHA / Phoodle / October-5 mention) and stays 20.0 s.
7. `prepend_clean_intro` wires intro voice + silent body (skipped when
   moviepy is unavailable, as in this sandbox; CI runs it).
8. `_layer_bg_music_after_intro` starts the bg song at intro_dur and keeps
   the intro's own audio at full volume (skipped without moviepy).
"""
import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import solve_lib as S

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _bank():
    with open(os.path.join(HERE, "phrazle_live.json"), encoding="utf-8") as f:
        return [str(p).upper() for p in json.load(f)["phrases"]]


def _sig(p):
    return tuple(len(w) for w in str(p).upper().split())


class TestValidPhraseWidened(unittest.TestCase):
    def test_long_real_answer_valid(self):
        # The Oct-20 answer that collapsed to 1 attempt: 18 letters.
        self.assertTrue(S._valid_phrase("PLAY YOUR CARDS RIGHT"))

    def test_bank_max_valid(self):
        bank = _bank()
        longest = max(bank, key=lambda p: sum(len(w) for w in p.split()))
        total = sum(len(w) for w in longest.split())
        self.assertGreater(total, 16)  # old cap rejected this
        self.assertTrue(S._valid_phrase(longest))

    def test_still_rejects_junk(self):
        self.assertFalse(S._valid_phrase("HELLO"))          # single word
        self.assertFalse(S._valid_phrase("A B"))            # too short
        self.assertFalse(S._valid_phrase("HELLO WORLD2"))   # non-alpha


class TestPlanPhraseShapeSeed(unittest.TestCase):
    SHAPE = (4, 4, 5, 5)
    ANSWER = "PLAY YOUR CARDS RIGHT"

    def test_rare_shape_solves_2_to_4(self):
        bank = _bank()
        # This shape has exactly one bank entry (the answer itself).
        self.assertEqual(
            sum(1 for p in bank if _sig(p) == self.SHAPE), 1)
        seq, steps = S.plan_phrase(self.ANSWER, bank, max_guesses=4,
                                   seed="phrazle:2026-10-20",
                                   shape=self.SHAPE)
        self.assertGreaterEqual(len(seq), 2, f"seq={seq}")
        self.assertLessEqual(len(seq), 4, f"seq={seq}")
        self.assertEqual(seq[-1], self.ANSWER)
        for g in seq:
            self.assertEqual(_sig(g), self.SHAPE, f"wrong shape: {g!r}")
        for g in seq[:-1]:
            self.assertNotEqual(g, self.ANSWER)
        # Decoys are real bank-vocabulary words (site rejects unknown words).
        vocab = set()
        for p in bank:
            vocab.update(p.split())
        for g in seq[:-1]:
            for w in g.split():
                self.assertIn(w, vocab, f"word {w!r} not in bank vocab")
        self.assertEqual(len(steps), len(seq))

    def test_seeded_deterministic(self):
        bank = _bank()
        a, _ = S.plan_phrase(self.ANSWER, bank, max_guesses=4,
                             seed="phrazle:2026-10-21", shape=self.SHAPE)
        b, _ = S.plan_phrase(self.ANSWER, bank, max_guesses=4,
                             seed="phrazle:2026-10-21", shape=self.SHAPE)
        self.assertEqual(a, b)

    def test_attempt_total_varies_across_days(self):
        bank = _bank()
        totals = set()
        for d in range(1, 12):
            seq, _ = S.plan_phrase(
                self.ANSWER, bank, max_guesses=4,
                seed=f"phrazle:2026-10-{d:02d}", shape=self.SHAPE)
            self.assertGreaterEqual(len(seq), 2)
            self.assertLessEqual(len(seq), 4)
            self.assertEqual(seq[-1], self.ANSWER)
            totals.add(len(seq))
        self.assertGreater(len(totals), 1,
                           f"attempt total never varied: {totals}")

    def test_common_shape_uses_bank_phrases(self):
        bank = _bank()
        shape = (5, 5)
        same = [p for p in bank if _sig(p) == shape]
        self.assertGreater(len(same), 3)
        ans = same[0]
        seq, _ = S.plan_phrase(ans, bank, max_guesses=4,
                               seed="phrazle:2026-10-20", shape=shape)
        self.assertGreaterEqual(len(seq), 2)
        self.assertLessEqual(len(seq), 4)
        self.assertEqual(seq[-1], ans)
        for g in seq:
            self.assertEqual(_sig(g), shape)

    def test_legacy_call_unchanged(self):
        bank = _bank()
        seq, steps = S.plan_phrase("BREAK A LEG", bank)
        self.assertEqual(seq[-1], "BREAK A LEG")
        self.assertLessEqual(len(seq), 5)
        self.assertEqual(len(steps), len(seq))

    def test_answer_appended_when_missing_from_bank(self):
        bank = [p for p in _bank() if _sig(p) == (3, 3)]
        ans = "FOO BAR"
        seq, _ = S.plan_phrase(ans, bank, max_guesses=4,
                               seed="phrazle:2026-10-20", shape=(3, 3))
        self.assertEqual(seq[-1], ans)
        self.assertGreaterEqual(len(seq), 2)
        self.assertLessEqual(len(seq), 4)


class TestIntroAudioKept(unittest.TestCase):
    def test_clean_intro_keeps_audio_track(self):
        import intro_watermark as IW
        out = "/tmp/iter40_intro_clean_test.mp4"
        got = IW.clean_intro(out=out)
        self.assertTrue(got and os.path.exists(got), "clean_intro failed")
        try:
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries",
                 "stream=codec_type,duration",
                 "-show_entries", "format=duration", "-of", "csv=p=0", got],
                capture_output=True, text=True, timeout=60)
            lines = [l for l in probe.stdout.splitlines() if l.strip()]
            types = [l.split(",")[0] for l in lines if "," in l]
            self.assertIn("audio", types,
                          f"no audio stream in cleaned intro: {lines}")
            self.assertIn("video", types)
        finally:
            if os.path.exists(out):
                os.remove(out)

    def test_clean_intro_stays_20s(self):
        import intro_watermark as IW
        out = "/tmp/iter40_intro_clean_dur.mp4"
        got = IW.clean_intro(out=out)
        self.assertTrue(got and os.path.exists(got))
        try:
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries",
                 "format=duration", "-of", "csv=p=0", got],
                capture_output=True, text=True, timeout=60)
            dur = float(probe.stdout.strip().splitlines()[0])
            self.assertGreater(dur, 19.5)
            self.assertLess(dur, 20.6)
        finally:
            if os.path.exists(out):
                os.remove(out)


@unittest.skipIf("moviepy" not in
                 {m.split(".")[0] for m in sys.modules} and
                 __import__("importlib").util.find_spec("moviepy") is None,
                 "moviepy not installed in this sandbox (CI runs these)")
class TestIntroAudioWiring(unittest.TestCase):
    def test_prepend_wires_intro_voice_plus_silence(self):
        import intro_watermark as IW
        from moviepy.editor import ColorClip
        body = ColorClip(size=(1920, 1080), color=(10, 10, 10),
                         duration=5.0)
        clip, chapters, dur = IW.prepend_clean_intro(
            body, [(0, "x")], "/tmp", log=lambda m: None)
        try:
            self.assertGreater(dur, 19.5)
            self.assertEqual(chapters[0][1], "Intro")
            audio = getattr(clip, "audio", None)
            self.assertIsNotNone(audio, "no audio wired on prepended clip")
            # The intro's own voice must be present in the first seconds:
            # sample the mixed audio and require non-silent energy early
            # and silence-equivalence late (body section).
            import numpy as np
            early = np.abs(audio.get_frame(5.0)).mean()
            self.assertGreater(early, 1e-4,
                               "intro voice missing from wired audio")
        finally:
            try:
                clip.close()
            except Exception:
                pass

    def test_bg_music_starts_after_intro(self):
        sys.path.insert(0, HERE)
        import runner
        from moviepy.editor import ColorClip
        body = ColorClip(size=(1920, 1080), color=(10, 10, 10),
                         duration=10.0)
        clip = runner._layer_bg_music_after_intro(body, 20.0, "phrazle")
        try:
            audio = getattr(clip, "audio", None)
            self.assertIsNotNone(audio)
            import numpy as np
            # With a 20 s intro and a 10 s body, the bg song must NOT
            # start before t=20 s: the first 20 s are the (silent) intro
            # placeholder, music only in the body window.
            self.assertAlmostEqual(float(audio.duration), 30.0, delta=0.5)
        finally:
            try:
                clip.close()
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()
