"""Fix-loop iteration 17 tests — WORLDLE workstream (evidence-grounded
in ANALYSIS13 §§5-9, 12).

1. Worldle Capital pin (P0): the iter-16 bbox-fit point (1072.5, 422.7)
   probed top=svg — bare SVG, NOT on the country outline (it sits in
   Kuwait Bay, a hole in the outline) — so pointerdown there sets no
   pendingClickRegion and no pin registers, while 4 blue dots sit on
   the map. New: a pinpoint-map probe (dots with data-*/title/aria
   attrs, isPointInFill on-land test, nearest in-fill grid search)
   + a pure target picker (dot-name > bbox-fit-in-fill >
   in-fill-search; NEVER nearest-dot-to-a-wrong-projection) +
   wiring in the capital player (explicit move/down/up with zero
   movement between down and up, post-click dot-delta log).
2. Worldle Language placement (P0): (a) the slot verifier's norm
   used [^a-z0-9], which DELETED the Arabic phrase (norm('اسمي') ==
   ''), so the Arabic slot could never verify even when
   frame-proven filled; the norm is now Unicode-preserving.
   (b) The drag AND click-verify loops returned on the FIRST
   unplaced pair, so eng was NEVER attempted; both loops now
   attempt ALL pairs, record per-pair placement, and click-verify
   skips pairs the drag already placed (their chips now live
   inside the slots).
3. Teuteuf banner probe (P2): the old probe matched the exact
   substring 'Teuteuf Edu' against per-element innerText in the
   light DOM only, and reported 'absent' while the banner sat in
   every frame. The probe now normalises whitespace, matches
   textContent (covers SVG text), and searches light DOM + shadow
   roots + same-origin iframes, logging HOW the banner was found.
4. G4 (P2): "G4" is the fix-loop name for the live-rounds
   cross-check gate (NOT Google Analytics — no GA check exists).
   The cross-check already ran and logged, but without the "G4"
   token, so a grep for G4 found zero lines. Its log lines now
   carry the G4 tag, including the None case.

P3 carry-forwards (live-rounds reader None, frozen_frac, Semantle
scores) are deliberately NOT changed here: none is clean/low-risk
from the saved source alone. All tests are synthetic/local: no
network, no browser, no CI run.
"""
import inspect
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import runner as R


class _FakeMouse:
    def __init__(self):
        self.clicks = []
        self.moves = []

    def click(self, x, y):
        self.clicks.append((float(x), float(y)))

    def move(self, x, y, steps=None):
        self.moves.append((float(x), float(y)))

    def down(self):
        pass

    def up(self):
        pass


class _FakeKeyboard:
    def press(self, *a, **k):
        pass


class _FakePage:
    """Minimal Playwright-page stand-in: evaluate dispatch by JS
    marker, a recording mouse, no-op waits."""

    def __init__(self, evaluate=None):
        self.mouse = _FakeMouse()
        self.keyboard = _FakeKeyboard()
        self._evaluate = evaluate or (lambda js, arg=None: None)
        self._qp_gid = "test-iter17"

    def evaluate(self, js, arg=None):
        return self._evaluate(js, arg)

    def wait_for_timeout(self, ms):
        pass


def _dbg_recorder(logs):
    def _rec(gid, msg):
        logs.append(f"[{gid}] {msg}")
    return _rec


class TestPhraseNormUnicode(unittest.TestCase):
    def test_arabic_preserved(self):
        # The iter-16 bug: [^a-z0-9] deleted the Arabic phrase.
        self.assertEqual(R._worldle_phrase_norm_text("اسمي"), "اسمي")
        self.assertEqual(R._worldle_phrase_norm_text("اسمي."), "اسمي")

    def test_english_and_punctuation(self):
        self.assertEqual(R._worldle_phrase_norm_text("My name is!"),
                         "my name is")
        self.assertEqual(R._worldle_phrase_norm_text("  Nɔɔyɛ, la me. "),
                         "nɔɔyɛ la me")


class TestSlotAtContainsArabic(unittest.TestCase):
    # Broad probe sees the Arabic slot FILLED with اسمي at the slot
    # centre (the iter-16 frame-proven state) and the English slot
    # empty. The old norm returned False for ara here.
    SLOT_AR = {"label": "Arabic", "x": 850.0, "y": 303.3,
               "text": "اسمي", "kind": "bordered-box"}
    SLOT_EN = {"label": "English", "x": 1070.0, "y": 303.3,
               "text": "", "kind": "bordered-box"}

    def _page(self, slots):
        def _ev(js, arg=None):
            if "WORLDLE-PHRASE-BROAD-SLOT-PROBE" in js:
                return {"slots": list(slots), "empty_boxes": list(slots)}
            return None
        return _FakePage(_ev)

    def test_filled_arabic_slot_verifies(self):
        page = self._page([self.SLOT_AR, self.SLOT_EN])
        self.assertTrue(R._worldle_phrase_slot_at_contains(
            page, {"x": 850.0, "y": 303.3}, "اسمي"))

    def test_empty_english_slot_does_not_verify(self):
        page = self._page([self.SLOT_AR, self.SLOT_EN])
        self.assertFalse(R._worldle_phrase_slot_at_contains(
            page, {"x": 1070.0, "y": 303.3}, "My name is"))

    def test_wrong_centre_does_not_verify(self):
        page = self._page([self.SLOT_AR, self.SLOT_EN])
        self.assertFalse(R._worldle_phrase_slot_at_contains(
            page, {"x": 100.0, "y": 100.0}, "اسمي"))


class _FakePhrasePage(_FakePage):
    """Phrase drag/click-verify fake: chips from the probe, broad
    slots from `broad_slots`, narrow slot check from `narrow`."""

    def __init__(self, broad_slots, narrow=False, guesses_q1=None):
        super().__init__()
        self.broad_slots = broad_slots
        self.narrow = narrow
        self.guesses_q1 = guesses_q1

    def evaluate(self, js, arg=None):
        if "WORLDLE-PHRASE-BROAD-SLOT-PROBE" in js:
            return {"slots": list(self.broad_slots),
                    "empty_boxes": list(self.broad_slots)}
        if "localStorage.getItem" in js:
            if arg == "language-guesses" and self.guesses_q1 is not None:
                return {"2026-10-18": list(self.guesses_q1)}
            return {}
        if js.strip().startswith("(phrase)"):
            # Narrow any-slot check ONLY: the drag probe JS also
            # contains the literal "[data-slot]" selector string,
            # so dispatch on the arrow-function head, not the
            # selector substring.
            return bool(self.narrow)
        if "querySelectorAll('button')" in js:
            return "guess"
        return {"chips": [
            {"code": "ara", "phrase": "اسمي", "draggable": False,
             "x": 1079.0, "y": 668.5},
            {"code": "eng", "phrase": "My name is", "draggable": False,
             "x": 841.0, "y": 492.5}],
            "slots": []}


class TestDragAttemptsAllPairs(unittest.TestCase):
    PICKED = {"ara": "اسمي", "eng": "My name is"}
    CODES = ["ara", "eng"]
    NAMES = ["Arabic", "English"]
    PROMPTS = ["My name is"]
    SLOTS = [{"label": "Arabic", "x": 850.0, "y": 303.3, "text": "",
              "kind": "bordered-box"},
             {"label": "English", "x": 1070.0, "y": 303.3, "text": "",
              "kind": "bordered-box"}]

    def _run(self, page):
        logs = []
        orig = R._dbg
        R._dbg = _dbg_recorder(logs)
        try:
            out = {}
            res = R._worldle_phrase_drag(
                page, "worldle", self.PICKED, self.CODES, self.NAMES,
                self.PROMPTS, "2026-10-18", _out=out)
        finally:
            R._dbg = orig
        return res, out, logs

    def test_both_pairs_attempted_when_none_place(self):
        # Fake never places anything: BOTH pairs must be attempted
        # (iter-16: only ara was attempted, eng never tried).
        page = _FakePhrasePage(self.SLOTS, narrow=False)
        (st, det), out, _logs = self._run(page)
        self.assertEqual(st, "click-fallback")
        self.assertIn("phrase drag failed for ara", det)
        self.assertIn("phrase drag failed for eng", det)
        self.assertEqual(out.get("placed"), {"ara": False, "eng": False})
        # eng's chip (841, 492.5) was actually moved to: eng attempted.
        self.assertTrue(any(abs(x - 841.0) < 5 and abs(y - 492.5) < 5
                            for x, y in page.mouse.moves))

    def test_eng_attempted_when_ara_already_placed(self):
        # Broad probe sees the Arabic slot FILLED: ara verifies
        # placed on drag, yet eng must STILL be attempted (iter-16:
        # the loop stopped after ara).
        filled = [{"label": "Arabic", "x": 850.0, "y": 303.3,
                   "text": "اسمي", "kind": "bordered-box"},
                  {"label": "English", "x": 1070.0, "y": 303.3,
                   "text": "", "kind": "bordered-box"}]
        page = _FakePhrasePage(filled, narrow=False)
        (st, det), out, _logs = self._run(page)
        self.assertEqual(st, "click-fallback")
        self.assertEqual(out.get("placed"), {"ara": True, "eng": False})
        self.assertIn("phrase drag failed for eng", det)
        self.assertNotIn("phrase drag failed for ara", det)
        self.assertTrue(any(abs(x - 841.0) < 5 and abs(y - 492.5) < 5
                            for x, y in page.mouse.moves))


class TestClickVerifyAllPairs(unittest.TestCase):
    PICKED = {"ara": "my name is ahmed", "eng": "my name is john"}
    PAIRS_IN = [
        ("ara", {"x": 841.0, "y": 668.5, "phrase": "my name is ahmed"},
         {"x": 850.0, "y": 303.3, "label": "Arabic"}),
        ("eng", {"x": 1079.0, "y": 668.5, "phrase": "my name is john"},
         {"x": 1070.0, "y": 303.3, "label": "English"}),
    ]
    Q1_CORRECT = [{"question": 1, "isCorrect": True},
                  {"question": 1, "isCorrect": True}]

    def _page(self, narrow):
        return _FakePhrasePage([], narrow=narrow,
                               guesses_q1=self.Q1_CORRECT)

    def _run(self, page, placed_in):
        logs = []
        orig = R._dbg
        R._dbg = _dbg_recorder(logs)
        try:
            res = R._worldle_phrase_click_verify(
                page, "gid-test", self.PICKED, ["ara", "eng"],
                ["Arabic", "English"], [], "2026-10-18",
                _pairs_in=self.PAIRS_IN, _placed_in=placed_in)
        finally:
            R._dbg = orig
        return res, logs

    def test_skips_drag_placed_pair(self):
        # ara already placed by drag: click-verify must NOT click
        # ara's stashed chip coords (the chip now lives in the slot)
        # — only eng is clicked.
        page = self._page(narrow=True)
        (st, _det), logs = self._run(page, {"ara": True, "eng": False})
        self.assertEqual(st, "attempted")
        self.assertEqual(page.mouse.clicks,
                         [(1079.0, 668.5), (1070.0, 303.3)])
        self.assertTrue(any("already placed by drag; skipping" in m
                            for m in logs))

    def test_attempts_all_pairs_on_failure(self):
        # Nothing ever places: BOTH pairs must be clicked (iter-16:
        # the first failure stopped the path, eng never attempted).
        page = self._page(narrow=False)
        (st, det), _logs = self._run(page, None)
        self.assertEqual(st, "partial")
        self.assertIn("unplaced", det)
        self.assertIn("ara", det)
        self.assertIn("eng", det)
        self.assertEqual(page.mouse.clicks,
                         [(841.0, 668.5), (850.0, 303.3),
                          (1079.0, 668.5), (1070.0, 303.3)])


class TestCapitalPickPinTarget(unittest.TestCase):
    # Iter-16 Kuwait fixture: bbox-fit point (1072.5, 422.7), dots
    # at the ANALYSIS13 frame positions, no identifying attrs.
    DOTS = [
        {"x": 1085.0, "y": 285.0, "r": 8.0, "fill": "blue",
         "cls": "", "id": "", "data": {}, "title": "", "aria": ""},
        {"x": 1010.0, "y": 440.0, "r": 8.0, "fill": "blue",
         "cls": "", "id": "", "data": {}, "title": "", "aria": ""},
        {"x": 1090.0, "y": 435.0, "r": 8.0, "fill": "blue",
         "cls": "", "id": "", "data": {}, "title": "", "aria": ""},
        {"x": 1110.0, "y": 645.0, "r": 8.0, "fill": "blue",
         "cls": "", "id": "", "data": {}, "title": "", "aria": ""},
    ]
    FIT = (1072.5, 422.7)

    def _probe(self, **kw):
        base = {"found": True, "dots": list(self.DOTS), "n_paths": 29,
                "n_dots": 4, "in_fill": False, "corrected": None}
        base.update(kw)
        return base

    def test_dot_name_match_wins(self):
        dots = [dict(d) for d in self.DOTS]
        dots[0] = dict(dots[0], data={"city": "Kuwait City"})
        got = R._worldle_capital_pick_pin_target(
            self._probe(dots=dots), self.FIT, "Kuwait City")
        self.assertIsNotNone(got)
        self.assertEqual(got["method"], "dot-name")
        self.assertEqual((got["x"], got["y"]), (1085.0, 285.0))

    def test_ambiguous_dot_names_returns_none(self):
        # Iter-22: ambiguity is DISTINCT values naming the
        # capital (two contained matches -> None). Identical
        # values on two dots are GROUP evidence, dropped by the
        # shared-source rule, and nearest-dot then picks.
        dots = [dict(d) for d in self.DOTS]
        dots[0] = dict(dots[0],
                       data={"city": "Greater Kuwait City"})
        dots[1] = dict(dots[1],
                       data={"city": "Kuwait City metro"})
        self.assertIsNone(R._worldle_capital_pick_pin_target(
            self._probe(dots=dots), self.FIT, "Kuwait City"))

    def test_bbox_fit_in_fill_keeps_fit_point(self):
        # Iter-22: with NO dot near the fit point, the in-fill
        # branch still returns the fit point (dots moved far
        # away so nearest-dot does not fire).
        far = [dict(d, x=d["x"] + 500.0, y=d["y"] + 500.0)
               for d in self.DOTS]
        got = R._worldle_capital_pick_pin_target(
            self._probe(dots=far, in_fill=True), self.FIT,
            "Kuwait City")
        self.assertEqual(got["method"], "bbox-fit-in-fill")
        self.assertEqual((got["x"], got["y"]), self.FIT)

    def test_nearest_dot_beats_in_fill_search(self):
        # Iter-22 SUPERSEDES the iter-16/17 "never nearest-dot"
        # rule (ANALYSIS19 §2): the nearest dot (~21px away) IS
        # the target — an in-fill point not on a dot does not
        # register on this widget.
        got = R._worldle_capital_pick_pin_target(
            self._probe(corrected={"x": 1060.0, "y": 410.0,
                                   "dist": 17.9}),
            self.FIT, "Kuwait City")
        self.assertEqual(got["method"], "nearest-dot")
        self.assertEqual((got["x"], got["y"]), (1090.0, 435.0))

    def test_no_verified_target_returns_none(self):
        # Iter-22: no dot near the fit point + no in-fill /
        # corrected point -> None.
        far = [dict(d, x=d["x"] + 500.0, y=d["y"] + 500.0)
               for d in self.DOTS]
        self.assertIsNone(R._worldle_capital_pick_pin_target(
            self._probe(dots=far), self.FIT, "Kuwait City"))
        self.assertIsNone(R._worldle_capital_pick_pin_target(
            {}, self.FIT, "Kuwait City"))
        self.assertIsNone(R._worldle_capital_pick_pin_target(
            {"found": False}, self.FIT, "Kuwait City"))
        self.assertIsNone(R._worldle_capital_pick_pin_target(
            {}, self.FIT, "Kuwait City"))
        self.assertIsNone(R._worldle_capital_pick_pin_target(
            {"found": False}, self.FIT, "Kuwait City"))

    def test_probe_wired_into_capital_player(self):
        src = inspect.getsource(R._s_worldle_play_capital)
        self.assertIn("_worldle_capital_pinpoint_probe(page, px, py)", src)
        self.assertIn("_worldle_capital_pick_pin_target(", src)
        self.assertIn("pin dots:", src)
        # Zero-movement press: move, then down + up with no move
        # between (d3-zoom didZoomMove stays false).
        self.assertIn("page.mouse.down()", src)
        self.assertIn("page.mouse.up()", src)
        self.assertIn("didZoomMove", src)

    def test_pinpoint_svg_paths_are_piece_candidates(self):
        src = inspect.getsource(R._worldle_capital_piece_path)
        self.assertIn("#pin-capital-svg path", src)


class TestBannerProbe(unittest.TestCase):
    def test_absent_case_is_logged(self):
        logs = []
        orig = R._dbg
        R._dbg = _dbg_recorder(logs)
        try:
            R._dismiss_teuteuf_banner(
                _FakePage(lambda js, arg=None: "absent"))
        finally:
            R._dbg = orig
        self.assertTrue(any("teuteuf edu banner: absent" in m
                            for m in logs))

    def test_present_result_logged_truthfully_not_rewritten(self):
        logs = []
        orig = R._dbg
        R._dbg = _dbg_recorder(logs)
        try:
            R._dismiss_teuteuf_banner(
                _FakePage(lambda js, arg=None:
                          "present-body-text-no-element"))
        finally:
            R._dbg = orig
        self.assertTrue(any("present-body-text-no-element" in m
                            for m in logs))

    def test_probe_matches_normalised_textcontent_shadow_iframe(self):
        src = inspect.getsource(R._dismiss_teuteuf_banner)
        # Whitespace-normalised match on textContent (covers SVG
        # text + innerText line breaks inside "Teuteuf Edu").
        self.assertIn("textContent", src)
        self.assertIn("teuteuf edu", src)
        # Shadow roots + same-origin iframes are searched.
        self.assertIn("shadowRoot", src)
        self.assertIn("contentDocument", src)
        # The old exact-substring innerText light-DOM-only match
        # must be gone.
        self.assertNotIn("includes('Teuteuf Edu')", src)
        # Rect-based visibility (iter-16 test constraint).
        self.assertIn("getBoundingClientRect", src)
        self.assertNotIn("e.offsetParent", src)


class TestG4Tagged(unittest.TestCase):
    def test_cross_check_line_carries_g4_tag(self):
        src = inspect.getsource(R._s_worldle)
        self.assertIn("worldle G4 live rounds cross-check", src)

    def test_reader_logs_carry_g4_tag(self):
        src = inspect.getsource(R._s_worldle_live_round_modes)
        self.assertIn("worldle G4 live rounds reader", src)

    def test_reader_none_return_logs_g4(self):
        # Toggle never opens: the reader returns None AND logs the
        # G4 check result (iter-17: including the None case).
        logs = []
        orig = R._dbg
        R._dbg = _dbg_recorder(logs)
        try:
            got = R._s_worldle_live_round_modes(
                _FakePage(lambda js, arg=None: False), "worldle")
        finally:
            R._dbg = orig
        self.assertIsNone(got)
        self.assertTrue(any("G4" in m and "None" in m for m in logs))

    def test_language_caller_passes_drag_placed_map(self):
        src = inspect.getsource(R._s_worldle_play_language)
        self.assertIn('_placed_in=_drag_out.get("placed")', src)


if __name__ == "__main__":
    unittest.main()
