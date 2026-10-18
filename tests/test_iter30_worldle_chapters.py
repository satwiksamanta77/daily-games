"""Fix-loop iteration 30 tests — Worldle chapter timestamps (ANALYSIS28 BUG 1).

The confirmation-run failure, frame- + log-proven: 7 of 14 Worldle
final-video chapters landed ~10-20s late ("Flag Solve" on Trivia,
"Area Solve" on the postcard). Root cause, confirmed in code:
_s_worldle_segment records RAW-recording offsets (vs _VIDEO_T0),
but _s_worldle_final_chapters mapped chapter = intro + 15 + raw_off
while assembly embeds chapters over the AUTOCROPPED gameplay
(leading idle cut: 13.4s in the confirmation run). Every chapter
landed exactly one leading-cut late.

Fix: _s_worldle_final_chapters takes the autocrop kept segments
(QP.LAST_POLISH['autocrop']['segments'], raw coordinates) and maps
each raw offset to its polished offset via _s_worldle_raw_to_polished
before adding the intro + cards base.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import runner as R


class TestRawToPolished(unittest.TestCase):
    def test_leading_cut_only(self):
        # Confirmation run: segments [[13.4, 334.0]].
        segs = [[13.4, 334.0]]
        self.assertEqual(R._s_worldle_raw_to_polished(0.0, segs), 0.0)
        self.assertEqual(R._s_worldle_raw_to_polished(13.4, segs), 0.0)
        self.assertEqual(R._s_worldle_raw_to_polished(100.0, segs), 86.6)
        # Area evidence 314.4 -> polished 301.0 (content at ~315 final).
        self.assertEqual(R._s_worldle_raw_to_polished(314.4, segs), 301.0)

    def test_no_cut_identity(self):
        segs = [[0.0, 300.0]]
        self.assertEqual(R._s_worldle_raw_to_polished(128.3, segs), 128.3)
        self.assertEqual(R._s_worldle_raw_to_polished(0.0, segs), 0.0)

    def test_none_segments_identity(self):
        self.assertEqual(R._s_worldle_raw_to_polished(128.3, None), 128.3)
        self.assertEqual(R._s_worldle_raw_to_polished(128.3, []), 128.3)

    def test_mid_video_static_cuts(self):
        segs = [[0.0, 100.0], [150.0, 300.0]]
        self.assertEqual(R._s_worldle_raw_to_polished(50.0, segs), 50.0)
        # Raw 120 sits in the cut gap [100, 150]: clamp to the next
        # kept segment's start (polished 100.0).
        self.assertEqual(R._s_worldle_raw_to_polished(120.0, segs), 100.0)
        # Raw 200 -> polished 100 + (200 - 150) = 150.
        self.assertEqual(R._s_worldle_raw_to_polished(200.0, segs), 150.0)
        # Past the last segment: clamp to the polished total (250.0).
        self.assertEqual(R._s_worldle_raw_to_polished(400.0, segs), 250.0)

    def test_negative_and_bad_input(self):
        self.assertEqual(R._s_worldle_raw_to_polished(-5.0, [[13.4, 334.0]]),
                         0.0)
        self.assertEqual(R._s_worldle_raw_to_polished("bogus", [[0, 10]]),
                         0.0)


class TestFinalChapters(unittest.TestCase):
    def setUp(self):
        self._saved = list(R._WORLDLE_SEGMENTS)
        R._WORLDLE_SEGMENTS[:] = [
            (128.3, "Orientation Solve"),
            (314.4, "Area Solve"),
        ]

    def tearDown(self):
        R._WORLDLE_SEGMENTS[:] = self._saved

    def test_leading_cut_subtracted(self):
        # Confirmation run: leading cut 13.4s, gameplay (polished) 320.6s.
        ch = R._s_worldle_final_chapters(0.0, 320.6,
                                         kept_segments=[[13.4, 334.0]])
        by_title = dict((t, s) for s, t in ch)
        # Old (buggy) mapping gave 15 + 314.4 = 329.4 (postcard).
        # Fixed: 15 + (314.4 - 13.4) = 316.0 (Area content).
        self.assertEqual(by_title["Area Solve"], 316.0)
        self.assertEqual(by_title["Orientation Solve"], 129.9)
        # Summary still at base + POLISHED gameplay duration.
        self.assertEqual(by_title["Summary"], 335.6)
        self.assertEqual(ch[0], (0, "Today's Worldle puzzle"))

    def test_no_segments_keeps_old_mapping(self):
        ch = R._s_worldle_final_chapters(0.0, 320.6)
        by_title = dict((t, s) for s, t in ch)
        self.assertEqual(by_title["Area Solve"], 329.4)
        self.assertEqual(by_title["Summary"], 335.6)

    def test_intro_prefix(self):
        ch = R._s_worldle_final_chapters(20.0, 320.6,
                                         kept_segments=[[13.4, 334.0]])
        self.assertEqual(ch[0], (0, "Intro"))
        by_title = dict((t, s) for s, t in ch)
        self.assertEqual(by_title["Area Solve"], 336.0)


if __name__ == "__main__":
    unittest.main()
