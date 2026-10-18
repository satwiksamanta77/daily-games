"""Iteration-33 tests: cleaned 20 s brand intro + wordsolverx watermark.

Intro contract (root cause: the raw intro's middle shows a stale
Wordle answer - "Wordle Hints and Answer for Today #1934 - OCTOBER 5,
2026" + MOCHA tiles + "MOCHA - BEST GUESS" card + a Phoodle "BRE..."
fragment, frame-audited 1 fps, dirty ~2-12 s):
  * assembly NEVER prepends the raw file - only clean_intro(), which
    holds the dirty window [1.8, 12.5) on the clean logo frame and
    drops the audio track (unverified intro audio could speak MOCHA);
  * cleaned intro stays 20 s so chapters shift by a plain +20 s;
  * watermark "wordsolverx.com" is a small bottom-right badge over
    the whole final clip (gameplay + cards), never covering boards.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import intro_watermark as IW

try:
    from moviepy.editor import (ColorClip, CompositeVideoClip, ImageClip,
                                VideoFileClip)
    HAS_MOVIEPY = True
except Exception:
    HAS_MOVIEPY = False


def _ffmpeg_info(path):
    """`ffmpeg -i` stderr (ffprobe is not shipped by imageio-ffmpeg,
    and CI may not have it on PATH) - used for stream/duration reads."""
    out = subprocess.run(
        [IW._ffmpeg_exe(), "-i", str(path)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        timeout=60, check=False)
    return out.stderr.decode("utf-8", "replace")


def _ffprobe_streams(path):
    import re
    info = _ffmpeg_info(path)
    kinds = re.findall(r"Stream #\d+:\d+.*?: (Video|Audio)", info)
    return [k.lower() for k in kinds]


def _ffprobe_duration(path):
    import re
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", _ffmpeg_info(path))
    if not m:
        return -1.0
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def _grab_frame(video, t, out):
    subprocess.run(
        [IW._ffmpeg_exe(), "-y", "-v", "error", "-ss", str(t),
         "-i", str(video), "-frames:v", "1", str(out)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=60, check=False)
    return out


def _mean_abs_diff(p1, p2):
    from PIL import Image, ImageChops, ImageStat
    a = Image.open(str(p1)).convert("RGB")
    b = Image.open(str(p2)).convert("RGB")
    if a.size != b.size:
        b = b.resize(a.size)
    return sum(ImageStat.Stat(ImageChops.difference(a, b)).mean)


class TestChapterShift(unittest.TestCase):
    def test_shift_adds_intro_chapter(self):
        ch = [(0, "Recap"), (5, "Hints"), (15, "Solve")]
        out = IW.shift_chapters_for_intro(ch, 20.0)
        self.assertEqual(out[0], (0, "Intro"))
        self.assertEqual(out[1:], [(20.0, "Recap"), (25.0, "Hints"),
                                   (35.0, "Solve")])

    def test_zero_duration_unchanged(self):
        ch = [(0, "Recap"), (5, "Hints")]
        self.assertEqual(IW.shift_chapters_for_intro(ch, 0.0), ch)
        self.assertEqual(IW.shift_chapters_for_intro(ch, None), ch)

    def test_empty_chapters(self):
        self.assertEqual(IW.shift_chapters_for_intro([], 20.0),
                         [(0, "Intro")])


class TestWatermarkBadge(unittest.TestCase):
    def test_position_bottom_right(self):
        x, y = IW.watermark_position(371, 54)
        self.assertEqual((x, y), (1920 - 371 - 28, 1080 - 54 - 30))
        self.assertTrue(0 <= x < 1920 and 0 <= y < 1080)

    def test_png_small_discreet_badge(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "wm.png")
            w, h = IW.make_watermark_png(p)
            self.assertLess(w, 600, "watermark must stay small")
            self.assertLess(h, 100, "watermark must stay small")
            from PIL import Image
            img = Image.open(p)
            self.assertEqual(img.mode, "RGBA")
            alpha = img.getchannel("A")
            self.assertGreater(max(alpha.getdata()), 0,
                               "badge must have visible pixels")
            self.assertLess(max(alpha.getdata()), 220,
                            "badge must stay translucent, not opaque")


class TestCleanIntro(unittest.TestCase):
    """End-to-end: real ffmpeg build from the repo intro file."""

    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.TemporaryDirectory()
        cls.out = os.path.join(cls.td.name, "clean.mp4")
        cls.built = IW.clean_intro(IW.INTRO_SRC, cls.out,
                                   log=lambda m: None)
        if cls.built:
            cls.src = str(IW.INTRO_SRC)

    @classmethod
    def tearDownClass(cls):
        cls.td.cleanup()

    def test_builds(self):
        self.assertTrue(self.built, "clean_intro must build from repo file")
        self.assertTrue(os.path.exists(self.out))

    def test_duration_stays_20s(self):
        self.assertAlmostEqual(_ffprobe_duration(self.out), 20.0,
                               delta=0.6)

    def test_no_audio_track(self):
        # Intro audio is unverified (could speak MOCHA) - dropped.
        self.assertEqual(_ffprobe_streams(self.out), ["video"])

    def test_dirty_window_held_on_logo(self):
        # t=5 (was MOCHA answer page) and t=11 (was Phoodle BRE) must
        # equal the clean logo hold frame (original t=0.3), not the
        # original dirty frames.
        hold = _grab_frame(self.src, 0.3,
                           os.path.join(self.td.name, "hold.jpg"))
        for t in (5, 9, 11):
            got = _grab_frame(self.out, t,
                              os.path.join(self.td.name, f"c{t}.jpg"))
            self.assertLess(_mean_abs_diff(got, hold), 10.0,
                            f"t={t} must be the logo hold, not an answer")
        # And the dirty-window frames must NOT match the originals.
        for t in (5, 11):
            orig = _grab_frame(self.src, t,
                               os.path.join(self.td.name, f"o{t}.jpg"))
            got = _grab_frame(self.out, t,
                              os.path.join(self.td.name, f"c{t}b.jpg"))
            self.assertGreater(_mean_abs_diff(got, orig), 25.0,
                               f"t={t} must differ from the raw answer")

    def test_clean_windows_preserved(self):
        # t=15 (DAILY VIDEOS) and t=19 (outro) survive the rebuild.
        for t in (15, 19):
            orig = _grab_frame(self.src, t,
                               os.path.join(self.td.name, f"p{t}.jpg"))
            got = _grab_frame(self.out, t,
                              os.path.join(self.td.name, f"q{t}.jpg"))
            self.assertLess(_mean_abs_diff(got, orig), 14.0,
                            f"t={t} clean scene must survive rebuild")


@unittest.skipUnless(HAS_MOVIEPY, "moviepy not installed")
class TestWatermarkComposite(unittest.TestCase):
    def test_badge_visible_bottom_right_only(self):
        with tempfile.TemporaryDirectory() as td:
            png = os.path.join(td, "wm.png")
            IW.make_watermark_png(png)
            base = ColorClip(size=(1920, 1080), color=(20, 30, 50),
                             duration=1.0)
            wm = (ImageClip(png).set_duration(1.0)
                  .set_position(IW.watermark_position(371, 54)))
            out = CompositeVideoClip([base, wm])
            frame = out.get_frame(0.5)
            # Badge region (bottom-right) differs from the flat base...
            import numpy as np
            badge = frame[1000:1060, 1600:1900]
            self.assertGreater(float(abs(badge.astype(int) - 20).mean()),
                               1.0, "watermark must be visible bottom-right")
            # ...while the centre (gameplay area) is untouched.
            centre = frame[400:600, 800:1100]
            self.assertLess(float(abs(centre.astype(int) - 20).mean()),
                            0.5, "watermark must not touch gameplay centre")


class TestPrependSoftFail(unittest.TestCase):
    def test_missing_source_returns_none(self):
        # clean_intro must fail SOFT (None), never raise, so a
        # helper failure can never fail a game job.
        self.assertIsNone(
            IW.clean_intro("/nonexistent/intro.mp4",
                           os.path.join(tempfile.gettempdir(),
                                        "nope.mp4"),
                           log=lambda m: None))


if __name__ == "__main__":
    unittest.main()
