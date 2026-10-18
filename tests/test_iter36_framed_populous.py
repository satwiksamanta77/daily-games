"""Fix-loop iteration 36 tests — Framed classic label + PopulousCities slot shape.

ITEM 1 (Framed classic): the iter-34 gate's label map used "CLASSIC"
for the classic board, but the site labels the classic board header
"FRAMED #N" (frame-proven in run 39151052266: "FRAMED #1683"; the
only "CLASSIC" text is the menu/nav tab, which carries no number).
The gate therefore deferred classic every run (safe, but no classic
video). Fix: label map "classic" -> "FRAMED" in _framed_live_answer.
These tests simulate the real page.evaluate (apply the label regex
to the body text, uppercased) so a revert to "CLASSIC" fails them:
with body "FRAMED #1683" + a bare "CLASSIC" tab, the CLASSIC regex
finds no number -> unreadable -> stale, while FRAMED matches 1683.

ITEM 2 (PopulousCities): the probe rewrite lives in
tests/test_iter35_worldle_3modes.py (fixture updated to the real
bare-label + pill shape). No populous tests here.
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import answers as A
import runner as R


class _FramedTextPage:
    """Simulates the real page.evaluate for the header script: applies
    the label regex to the body text (uppercased), exactly like the
    production JS (new RegExp(lbl + '\\s*#\\s*(\\d+)'))."""

    def __init__(self, body):
        self._body = body

    def evaluate(self, script, *args):
        if "new RegExp" in script:
            label = args[0] if args else ""
            m = re.search(re.escape(label) + r"\s*#\s*(\d+)",
                          self._body.upper())
            return int(m.group(1)) if m else None
        return ""

    def goto(self, url, **kw):
        pass

    def reload(self, **kw):
        pass

    def wait_for_timeout(self, ms):
        pass


class TestFramedClassicLabelIter36(unittest.TestCase):
    def setUp(self):
        self._orig_framed = A.framed
        self._orig_dbg = R._dbg
        R._dbg = lambda gid, msg: None

    def tearDown(self):
        A.framed = self._orig_framed
        R._dbg = self._orig_dbg

    def _engine(self, puzzle):
        A.framed = lambda d, mode=None: {"puzzle": puzzle, "answer": "X"}

    def test_classic_board_labeled_framed_passes(self):
        # Production classic board: header "FRAMED #1683", bare
        # "CLASSIC" tab with no number. Gate must PASS (1683 == 1683).
        self._engine(1683)
        body = ("FRAMED #1683\nGuess the movie\nCLASSIC\nONE FRAME\n"
                "TITLESHOT\nPOSTER\n")
        got = R._framed_live_answer(_FramedTextPage(body), "classic",
                                    "fallback")
        self.assertEqual(got, "fallback")

    def test_classic_stale_number_defers(self):
        self._engine(1683)
        body = "FRAMED #1682\nCLASSIC\n"
        with self.assertRaises(R._FramedStaleBoard) as cm:
            R._framed_live_answer(_FramedTextPage(body), "classic",
                                  "fallback")
        self.assertIn("1682", str(cm.exception))
        self.assertIn("1683", str(cm.exception))

    def test_classic_no_framed_header_is_unreadable(self):
        # No "FRAMED #N" anywhere: unreadable -> defer, never solve.
        self._engine(1683)
        body = "CLASSIC\nONE FRAME #686\n"
        with self.assertRaises(R._FramedStaleBoard) as cm:
            R._framed_live_answer(_FramedTextPage(body), "classic",
                                  "fallback")
        self.assertIn("unreadable", str(cm.exception))

    def test_one_frame_label_unchanged(self):
        # Other modes' labels must keep working after the map change.
        self._engine(686)
        body = "FRAMED #1683\nONE FRAME #686\n"
        got = R._framed_live_answer(_FramedTextPage(body), "one-frame",
                                    "fallback")
        self.assertEqual(got, "fallback")


if __name__ == "__main__":
    unittest.main()
