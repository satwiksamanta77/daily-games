"""Fix-loop iteration 16 tests (evidence-grounded in ANALYSIS12).

1. Nerdle own-header probe (P0): iter-15's full-line-anchored matcher
   captured NOTHING on all 9 modes while frames showed the headers
   (#1733 classic, #1368 maxi). Now: SEARCH (not full-line anchor)
   on the whole element text; JS collects via bounding-rect
   visibility (not offsetParent — fixed headers have offsetParent
   null) + textContent fallback + probe_diag diagnostics; a prefix
   counts as a mode prefix only when it IS a known mode name.
2. Worldle Capital pin (P0): the teuteuf-geo-map component fires
   onRegionClick only when pointerdown lands ON a country piece
   path (pointerdown -> pendingClickRegion -> d3-zoom end); a
   bare-SVG pointerdown sets nothing. The click is retargeted onto
   the nearest hit-test-verified piece path, and a pin ladder
   (path dispatch / real press / confirm step) runs whenever no
   marker appeared — inMap alone is NOT placement.
3. Worldle Language placement (P0): click-verify REUSES the drag
   helper's LABEL pairs (pre-drag chip coords) — never re-probes +
   re-pairs (iter-15 re-paired chip boxes as slots by geometry-x
   and clicked slot coords as the chip); a geometry-x re-pairing
   is refused; chip/slot centre coincidence is refused; the broad
   probe no longer returns chips as slots; drag gains a hover-drop
   attempt + a DataTransfer payload.
4. Worldle Area re-switch (P1): iter-15 re-switched only when the
   comparison returned "attempted" — the iter-15 run returned
   "partial" (store-verify failed) + view moved, and the number
   path never ran. Now a partial + view move re-switches, re-
   verifies, and falls through to the number path.
5. Nerdle 2 verifier (P1): count cells CONTAINING 2 (site renders
   it inside the `1` cell as `12`), not cells whose entire text is 2.
6. Teuteuf banner (P1): rect-based visibility, close verified by
   re-query (falls back to hiding the container), ALWAYS logged
   (including 'absent'), re-run on every mode switch.
7. Semantle live-day silence (P1): the no-signal decision is logged
   ("no live-day signal, keeping target") instead of silent.
8. Nerdle 37s pre-typing gap (P2): stage timing logs + early-exit
   sweep / conditional Escape (typing speed unchanged).
9. Carry-forwards (P3): speed solutions deduped at the reader
   (same equation in answer file AND gameState double-counted);
   autocrop segments=[] sentinel translated to the full source
   kept (kept_s=duration, not 0.0); Countryle onboarding root
   cause — Owl Carousel renders ALL slides at once so the body-
   text fingerprint was slide-invariant; PLAY! text; SKIP is a
   <div class="skip-text">, not a button.

All tests are synthetic/local: no network, no browser, no CI run.
"""
import contextlib
import io
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import runner as R
import nerdle_solver as NS

SRC_RUNNER = (Path(__file__).parent.parent / "runner.py").read_text(
    encoding="utf-8")
SRC_NERDLE = (Path(__file__).parent.parent / "nerdle_solver.py").read_text(
    encoding="utf-8")


def _nerdle_fn_src(name, end_marker):
    start = SRC_NERDLE.index(f"async def {name}")
    end = SRC_NERDLE.index(end_marker, start)
    return SRC_NERDLE[start:end]


class _FakeAnswers:
    """Stub for runner.A: target_date + per-game engine."""

    def __init__(self, target):
        self._target = target

    def target_date(self):
        return self._target

    def semantle(self, d):
        idx = (d - date(2022, 1, 30)).days
        return {"answer": f"word-{idx}", "puzzle": idx, "date": str(d)}


class TestNerdleOwnHeaderRelaxed(unittest.TestCase):
    def _probe(self, text):
        return {"own_headers": [{"tag": "DIV", "cls": "", "text": text}]}

    def test_substring_in_line_matches(self):
        # Logo shares its line with nav text: iter-15 ^...$ missed it.
        n, ev = NS._nerdle_own_header_from_probe(
            self._probe("header nav nerdle. #1733 Home"), "classic")
        self.assertEqual(n, 1733)
        self.assertEqual(ev["prefix"], "")

    def test_split_lines_match(self):
        # "nerdle." and "#1733" on separate lines (separate spans).
        n, _ = NS._nerdle_own_header_from_probe(
            self._probe("nerdle.\n#1733"), "classic")
        self.assertEqual(n, 1733)

    def test_chrome_word_prefix_counts_as_no_prefix(self):
        # "the" is chrome wording, not a mode claim -> classic's header.
        n, ev = NS._nerdle_own_header_from_probe(
            self._probe("the nerdle. #1733"), "classic")
        self.assertEqual((n, ev["prefix"]), (1733, ""))

    def test_maxi_own_header(self):
        n, ev = NS._nerdle_own_header_from_probe(
            self._probe("maxi nerdle. #1368"), "maxi")
        self.assertEqual((n, ev["prefix"]), (1368, "maxi"))

    def test_other_mode_prefix_rejected(self):
        # Maxi's header on the classic page is shared chrome, not
        # classic's header.
        n, _ = NS._nerdle_own_header_from_probe(
            self._probe("maxi nerdle. #1368"), "classic")
        self.assertIsNone(n)
        # Classic's header on the mini page is NOT mini's header.
        n, _ = NS._nerdle_own_header_from_probe(
            self._probe("classic nerdle. #1733"), "mini")
        self.assertIsNone(n)

    def test_mini_own_header(self):
        n, ev = NS._nerdle_own_header_from_probe(
            self._probe("mini nerdle. #497"), "mini")
        self.assertEqual((n, ev["prefix"]), (497, "mini"))

    def test_served_js_has_diagnostics(self):
        self.assertIn("probe_diag", NS.SERVED_JS)
        self.assertIn("elements_visible_rect", NS.SERVED_JS)
        self.assertIn("nerdle_text_samples", NS.SERVED_JS)
        # Rect visibility, not offsetParent, for the own-header scan.
        self.assertIn("getBoundingClientRect", NS.SERVED_JS)
        # textContent fallback for nodes with empty innerText.
        self.assertIn("textContent", NS.SERVED_JS)
        # Search within the line, not a full-line anchor.
        self.assertIn("/([a-z]+\\s+)?nerdle", NS.SERVED_JS)


class TestNerdleSuperscriptContains(unittest.TestCase):
    def test_count_uses_contains_not_exact(self):
        src = _nerdle_fn_src("_grid_superscript_count",
                              "async def type_equation")
        self.assertIn("includes(ch)", src)
        # The live predicate (not the docstring's historical note).
        self.assertNotIn("trim() === ch", src)


class TestSemantleNoSignalLogged(unittest.TestCase):
    def setUp(self):
        self._orig_a = R.A
        self._orig_live = dict(R._LIVE_DAY)
        R.A = _FakeAnswers(date(2026, 10, 18))
        R._LIVE_DAY.clear()

    def tearDown(self):
        R.A = self._orig_a
        R._LIVE_DAY.clear()
        R._LIVE_DAY.update(self._orig_live)

    def test_no_signal_logs_decision_and_keeps_target(self):
        ans = {"answer": "cry", "puzzle": 1722, "date": "2026-10-18"}
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            out, served = R._align_answer_to_live_day("semantle", ans)
        self.assertIs(out, ans)
        self.assertIsNone(served)
        self.assertIn("no live-day signal", buf.getvalue())
        self.assertIn("keeping target", buf.getvalue())


class TestCapitalPickPiece(unittest.TestCase):
    def test_nearest_verified_piece_wins(self):
        pieces = [
            {"id": "far", "cx": 900.0, "cy": 900.0, "verified": True},
            {"id": "near", "cx": 510.0, "cy": 410.0, "verified": True},
            {"id": "unverified-near", "cx": 501.0, "cy": 401.0,
             "verified": False},
        ]
        got = R._worldle_capital_pick_piece(pieces, 500.0, 400.0)
        self.assertEqual(got["id"], "near")
        self.assertAlmostEqual(got["dist"], (10 ** 2 + 10 ** 2) ** 0.5)

    def test_beyond_max_dist_returns_none(self):
        pieces = [{"id": "far", "cx": 900.0, "cy": 900.0,
                   "verified": True}]
        self.assertIsNone(
            R._worldle_capital_pick_piece(pieces, 500.0, 400.0))
        # A neighbour country 400px away must NOT be clicked.
        self.assertIsNone(
            R._worldle_capital_pick_piece(pieces, 500.0, 400.0,
                                          max_dist=250.0))

    def test_unverified_only_returns_none(self):
        pieces = [{"id": "x", "cx": 501.0, "cy": 401.0,
                   "verified": False}]
        self.assertIsNone(
            R._worldle_capital_pick_piece(pieces, 500.0, 400.0))

    def test_ladder_wired_into_capital_flow(self):
        self.assertIn("_s_worldle_capital_pin_ladder", SRC_RUNNER)
        self.assertIn("_worldle_capital_piece_path", SRC_RUNNER)
        self.assertIn("retargeted onto country", SRC_RUNNER)


class _FakeMouse:
    def __init__(self):
        self.clicks = []

    def click(self, x, y):
        self.clicks.append((x, y))

    def move(self, *a, **k):
        pass

    def down(self):
        pass

    def up(self):
        pass


class _FakePage:
    def __init__(self):
        self.mouse = _FakeMouse()

    def evaluate(self, *a, **k):
        return False

    def wait_for_timeout(self, *a, **k):
        pass


class TestClickVerifyReuse(unittest.TestCase):
    def setUp(self):
        self._orig_dbg = R._dbg
        R._dbg = lambda gid, msg: None

    def tearDown(self):
        R._dbg = self._orig_dbg

    def test_reuses_drag_pairs_verbatim(self):
        # Chip coords come from the drag pairs, NOT a fresh text
        # lookup (the fake page's evaluate returns False, so a text
        # lookup would report "not found" with zero clicks).
        pairs = [
            ("ara", {"code": "ara", "phrase": "x", "x": 850, "y": 668},
             {"label": "Arabic", "x": 850, "y": 303}),
            ("eng", {"code": "eng", "phrase": "y", "x": 1070, "y": 668},
             {"label": "English", "x": 1070, "y": 303}),
        ]
        picked = {"ara": "phrase-ara", "eng": "phrase-eng"}
        page = _FakePage()
        st, det = R._worldle_phrase_click_verify(
            page, "worldle", picked, ["ara", "eng"],
            ["Arabic", "English"], ["my name is"], "2026-10-18",
            _pairs_in=pairs)
        self.assertEqual(st, "partial")
        # First pair clicked chip-then-slot, then stopped: the slot
        # did not fill (fake evaluate), so no submit, no 2nd pair.
        self.assertEqual(page.mouse.clicks, [(850, 668), (850, 303)])
        self.assertIn("did not fill", det)

    def test_swap_guard_refuses_coincident_centres(self):
        # Iter-15 symptom: chip coords == slot coords (850, 304).
        pairs = [
            ("ara", {"code": "ara", "phrase": "x", "x": 850, "y": 304},
             {"label": "Arabic", "x": 850, "y": 304}),
        ]
        page = _FakePage()
        st, det = R._worldle_phrase_click_verify(
            page, "worldle", {"ara": "phrase-ara"}, ["ara"],
            ["Arabic"], ["my name is"], "2026-10-18",
            _pairs_in=pairs)
        self.assertEqual(st, "partial")
        self.assertIn("coincide", det)
        self.assertEqual(page.mouse.clicks, [])

    def test_geometry_repairing_refused(self):
        orig_probe = R._worldle_phrase_broad_slot_probe
        orig_pair = R._worldle_phrase_pair_slots
        try:
            R._worldle_phrase_broad_slot_probe = (
                lambda page, names=None: {"slots": [
                    {"label": "", "x": 1, "y": 668}]})
            R._worldle_phrase_pair_slots = (
                lambda slots, codes, names: (
                    [("ara", {"label": "", "x": 1, "y": 668})],
                    "geometry-x"))
            page = _FakePage()
            st, det = R._worldle_phrase_click_verify(
                page, "worldle", {"ara": "phrase-ara"}, ["ara"],
                ["Arabic"], ["my name is"], "2026-10-18")
            self.assertEqual(st, "partial")
            self.assertIn("geometry", det)
            self.assertEqual(page.mouse.clicks, [])
        finally:
            R._worldle_phrase_broad_slot_probe = orig_probe
            R._worldle_phrase_pair_slots = orig_pair

    def test_drag_has_hover_drop_and_payload(self):
        self.assertIn("hover-drop", SRC_RUNNER)
        self.assertIn("effectAllowed", SRC_RUNNER)
        self.assertIn("setData('text/plain'", SRC_RUNNER)


class TestAreaReswitch(unittest.TestCase):
    def setUp(self):
        self._orig = {}
        for name in ("_s_worldle_play_comparison", "_worldle_area_log_state",
                     "_worldle_area_still_on_area", "_s_worldle_switch_mode",
                     "_s_worldle_pick_input", "_s_worldle_set_picked_value",
                     "_s_worldle_submit_guarded", "_settle",
                     "_s_worldle_win_check", "_dbg"):
            self._orig[name] = getattr(R, name)
        R._dbg = lambda gid, msg: None
        R._worldle_area_log_state = lambda page, gid, step: None
        R._s_worldle_submit_guarded = lambda *a, **k: None
        R._settle = lambda page, base=0: None
        self.calls = []

    def tearDown(self):
        for name, fn in self._orig.items():
            setattr(R, name, fn)

    def _day(self):
        return {"date": "2026-10-18", "size": 17818,
                "size_lookup": {"kuwait": 17818, "qatar": 11000}}

    def test_partial_plus_view_move_reswitches_and_plays_number(self):
        comp = iter([("partial", None, "store did NOT change"),
                     ("attempted", "Kuwait", "comparison")])
        R._s_worldle_play_comparison = lambda *a, **k: next(comp)
        still = iter([False, True])
        R._worldle_area_still_on_area = lambda page: next(still)

        def _switch(page, gid, mode, ds):
            self.calls.append(mode)
            return True
        R._s_worldle_switch_mode = _switch
        R._s_worldle_pick_input = lambda *a, **k: True
        R._s_worldle_set_picked_value = lambda *a, **k: (True, "set")
        R._s_worldle_win_check = lambda *a, **k: (False, None, "no")
        st, guesses, det = R._s_worldle_play_area(
            object(), "worldle", self._day())
        self.assertEqual(self.calls, ["area"])
        self.assertEqual(st, "attempted")
        self.assertEqual(guesses, ["17818", "Kuwait"])

    def test_reswitch_failure_fails_honestly(self):
        R._s_worldle_play_comparison = lambda *a, **k: (
            "partial", None, "store did NOT change")
        R._worldle_area_still_on_area = lambda page: False
        R._s_worldle_switch_mode = lambda *a, **k: False
        st, guesses, det = R._s_worldle_play_area(
            object(), "worldle", self._day())
        self.assertEqual(st, "partial")
        self.assertIn("re-switch", det)

    def test_partial_while_still_on_area_returns_partial(self):
        R._s_worldle_play_comparison = lambda *a, **k: (
            "partial", None, "options unresolvable")
        R._worldle_area_still_on_area = lambda page: True
        switched = []
        R._s_worldle_switch_mode = lambda *a, **k: switched.append(1)
        st, guesses, det = R._s_worldle_play_area(
            object(), "worldle", self._day())
        self.assertEqual(st, "partial")
        self.assertEqual(switched, [])
        self.assertIn("options unresolvable", det)


class TestBannerAndMiscSource(unittest.TestCase):
    def test_banner_always_logged_rect_verified(self):
        start = SRC_RUNNER.index("def _dismiss_teuteuf_banner")
        end = SRC_RUNNER.index("def _close_modals", start)
        src = SRC_RUNNER[start:end]
        self.assertIn("ALWAYS log", src)
        self.assertIn("getBoundingClientRect", src)
        self.assertIn("closed-via-button+hidden-container", src)
        # Re-run on every mode switch.
        self.assertIn("_dismiss_teuteuf_banner(page)", SRC_RUNNER)

    def test_countryle_root_cause(self):
        self.assertIn("owl-item", SRC_RUNNER)
        self.assertIn("div.skip-text", SRC_RUNNER)
        self.assertIn("replace(/!+$/, '')", SRC_RUNNER)

    def test_nerdle_stage_logging(self):
        self.assertIn('pre-guess stage', SRC_NERDLE)
        self.assertIn("solver_suggest start", SRC_NERDLE)
        # Typing speed untouched: normal typing still passes delay
        # through unchanged.
        self.assertIn("keyboard.type(part, delay=delay)", SRC_NERDLE)

    def test_speed_dedup_at_reader(self):
        self.assertIn("drop exact duplicates", SRC_NERDLE)


class TestAutocropSentinel(unittest.TestCase):
    def test_no_cut_reports_full_source_kept(self):
        import shutil
        import subprocess
        import tempfile
        import quordle_parity as QP
        tmp = tempfile.mkdtemp(prefix="iter16-autocrop-")
        try:
            vid = str(Path(tmp) / "black.mp4")
            r = subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                 "-i", "color=c=black:s=320x180:d=10",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", vid],
                capture_output=True, text=True, timeout=60)
            if r.returncode != 0 or not Path(vid).exists():
                self.skipTest("ffmpeg black-frame fixture unavailable")
            out = str(Path(tmp) / "out.mp4")
            got = QP._apply_autocrop(vid, out, first_typing_s=0.5,
                                     solve_span_s=10.0)
            self.assertEqual(got, vid)
            ac = (QP.LAST_POLISH or {}).get("autocrop") or {}
            # The old log read segments=[] kept=0.0s coverage=None
            # for a video that kept the FULL source.
            self.assertEqual(ac.get("segments"), [[0.0, 10.0]])
            self.assertAlmostEqual(float(ac.get("kept_s") or 0), 10.0,
                                   delta=0.2)
            self.assertAlmostEqual(float(ac.get("coverage_ratio") or 0),
                                   1.0, delta=0.05)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
