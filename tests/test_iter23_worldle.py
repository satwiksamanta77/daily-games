"""Fix-loop iteration 23 tests — WORLDLE + FRAMED (evidence-grounded
in ANALYSIS20, run 39089028607).

- Capital (P0, §2): the game's exact dot fill #5252ff was missing
  from the isBlueish hex allowlist, the computed-fill fallback was
  skipped whenever an attribute fill existed, and the main probe's
  JS had an unclosed `if (!fill) {` so it never parsed (dots=[]
  always). Now: 5252ff + the 6-digit-hex b-dominant rule in BOTH
  probes (main + diagnostics), computed fill ALWAYS read and
  tested alongside the attribute fill, and the brace fixed.
- Area (P0, §3): pick returned None (options 'Image Quiz Item' x2
  unresolvable) -> early return before the gate/click/re-verify,
  and the solve came from the banner probe's hollow-box misfire
  clicking the Kuwait card. Now: generic option names resolve via
  the option image src (country-shapes/kw.svg -> Kuwait) from the
  game's own CDN code list, so the pick + gate + click path
  execute; a store pick with no Area-path click is explicitly
  NOT credited as an Area-path solve.
- Teuteuf (P1, §4): the hollow-box heuristic fired on game UI
  (11 did-not-dismiss; 1 success closed an app popup). Now: a
  hollow box is a coordinate-X candidate ONLY with a banner
  signature (non-interactive container, no light-DOM game
  descendants, non-interactive centre hit, banner-shaped,
  top-of-page); boxes without it are diagnosed, never clicked.
- G4 (P1, §5): the anchored scan left no `anchored` line at all.
  Now: the anchor attempt is logged on EVERY attempt (success
  and failure) and unconditionally on the failure path,
  including anchored=None.
- Framed (P2, §6): the breakdown counts lived only as pixels in
  the facts card, never as a log string. Now: the assembly logs
  "N guesses played / M final answers".

All tests are synthetic/local: no network, no browser, no CI run.
"""
import inspect
import json
import sys
import tempfile
import unittest
import ast
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import runner as R
import daily_parity as DP


class _FakeMouse:
    def __init__(self):
        self.clicks = []

    def click(self, x, y):
        self.clicks.append((float(x), float(y)))

    def move(self, x, y, steps=None):
        pass

    def down(self):
        pass

    def up(self):
        pass


class _FakeKeyboard:
    def press(self, *a, **k):
        pass


class _FakePage:
    def __init__(self, evaluate=None):
        self.mouse = _FakeMouse()
        self.keyboard = _FakeKeyboard()
        self._evaluate = evaluate or (lambda js, arg=None: None)
        self._qp_gid = "worldle"
        self.url = ""
        self.calls = []

    def evaluate(self, js, arg=None):
        self.calls.append(js)
        return self._evaluate(js, arg)

    def wait_for_timeout(self, ms):
        pass

    def query_selector(self, sel):
        return None

    def query_selector_all(self, sel):
        return []


def _dbg_recorder(logs):
    def _rec(gid, msg):
        logs.append(f"[{gid}] {msg}")
    return _rec


# ---------------------------------------------------------------- Capital

class TestCapitalIsBlueishIter23(unittest.TestCase):
    def test_game_dot_fill_is_blue(self):
        # ANALYSIS20 §2: the game's exact dot fill.
        self.assertTrue(R._worldle_capital_is_blueish("#5252ff"))
        self.assertTrue(R._worldle_capital_is_blueish("rgb(82,82,255)"))
        self.assertTrue(R._worldle_capital_is_blueish("RGB(82, 82, 255)"))

    def test_existing_blues_still_blue(self):
        self.assertTrue(R._worldle_capital_is_blueish("#2563eb"))
        self.assertTrue(R._worldle_capital_is_blueish("#3b82f6"))
        self.assertTrue(R._worldle_capital_is_blueish("blue"))

    def test_non_blues_still_not_blue(self):
        self.assertFalse(R._worldle_capital_is_blueish("#ff0000"))
        self.assertFalse(R._worldle_capital_is_blueish("#525252"))
        self.assertFalse(R._worldle_capital_is_blueish("red"))
        self.assertFalse(R._worldle_capital_is_blueish(""))
        self.assertFalse(R._worldle_capital_is_blueish(None))

    def test_main_probe_js_has_fix(self):
        src = inspect.getsource(R._worldle_capital_pinpoint_probe)
        self.assertIn("5252ff", src)
        self.assertIn("computedFill", src)
        self.assertIn("computed2", src)
        self.assertIn("!isBlueish(computed2)", src)
        # The iter-22 parse-killer: the old gated assignment
        # `if (!fill) { fill = getComputedStyle(c).fill ... }`
        # (unclosed brace) is gone; computed fill is always read.
        self.assertNotIn("fill = getComputedStyle(c).fill || ''", src)

    def test_diagnostics_probe_js_has_fix(self):
        src = inspect.getsource(R._worldle_capital_probe_diagnostics)
        self.assertIn("5252ff", src)
        self.assertIn("computedFill", src)
        self.assertIn("!isBlueish(computedFill)", src)


# ------------------------------------------------------------------- Area

def _src_options():
    return [
        {"name": "Image Quiz Item", "x": 833, "y": 300,
         "src": "https://cdn-assets.teuteuf.fr/country-shapes/kw.svg"},
        {"name": "Image Quiz Item", "x": 1100, "y": 300,
         "src": "https://cdn-assets.teuteuf.fr/country-shapes/qa.svg"},
    ]


class TestAreaSrcResolutionIter23(unittest.TestCase):
    def test_generic_names_resolve_via_src(self):
        def _ev(js, arg=None):
            if "worldle-country-code-map" in js:
                return {"kw": "Kuwait", "qa": "Qatar"}
            return None

        opts, detail = R._s_worldle_resolve_option_names_by_src(
            _FakePage(_ev), _src_options())
        self.assertEqual([o["name"] for o in opts],
                         ["Kuwait", "Qatar"])
        self.assertTrue(all(o.get("name_via_src") for o in opts))
        self.assertIn("src-resolved", detail)
        self.assertIn("Kuwait", detail)

    def test_resolved_names_pick(self):
        # The iter-22 shape: after resolution the pick must NOT
        # return None — the gate/click/re-verify path executes.
        opts = [{"name": "Kuwait"}, {"name": "Qatar"}]
        best, _det = R._s_worldle_comparison_pick(
            opts, {"kuwait": 17818, "qatar": 11000})
        self.assertEqual(best, "Kuwait")

    def test_non_generic_names_need_no_fetch(self):
        seen = []

        def _ev(js, arg=None):
            seen.append(js)
            return {}

        opts = [{"name": "Kuwait", "src": ".../kw.svg"},
                {"name": "Qatar", "src": ".../qa.svg"}]
        got, detail = R._s_worldle_resolve_option_names_by_src(
            _FakePage(_ev), opts)
        self.assertEqual([o["name"] for o in got], ["Kuwait", "Qatar"])
        self.assertEqual(seen, [])
        self.assertEqual(detail, "")

    def test_fetch_failure_leaves_names_unchanged(self):
        def _ev(js, arg=None):
            raise Exception("CDN down")

        got, detail = R._s_worldle_resolve_option_names_by_src(
            _FakePage(_ev), _src_options())
        self.assertEqual([o["name"] for o in got],
                         ["Image Quiz Item", "Image Quiz Item"])
        self.assertIn("unavailable", detail)

    def test_comparison_calls_src_resolution(self):
        src = inspect.getsource(R._s_worldle_play_comparison)
        self.assertIn("_s_worldle_resolve_option_names_by_src(", src)

    def test_store_pick_without_area_click_not_credited(self):
        src = inspect.getsource(R._s_worldle_play_area)
        self.assertIn("STORE ALREADY RECORDS A PICK", src)
        # (String literal is split across source lines.)
        self.assertIn("NOT credited", src)
        self.assertIn("attributable to the Area path", src)


# ---------------------------------------------------------------- Teuteuf

def _unverified(hollow):
    return ("unverified-no-element-match:"
            + json.dumps({"roots": 1, "shadow_hosts": 0,
                          "hollow_boxes": hollow}))


class TestTeuteufBannerSignatureIter23(unittest.TestCase):
    def _run(self, hollow, second=None):
        logs = []
        orig = R._dbg
        R._dbg = _dbg_recorder(logs)
        try:
            state = {"n": 0}

            def _ev(js, arg=None):
                state["n"] += 1
                if state["n"] == 1:
                    return _unverified(hollow)
                return _unverified(second or [])

            page = _FakePage(_ev)
            R._dismiss_teuteuf_banner(page)
        finally:
            R._dbg = orig
        return page, logs

    def test_game_ui_box_without_signature_never_clicked(self):
        # The iter-22 Kuwait Area card shape: a BUTTON host at
        # the centre, signature explicitly False.
        box = {"tag": "DIV", "cls": "", "id": "",
               "rect": {"x": 709, "y": 176, "w": 248, "h": 248},
               "children": 1,
               "host_at_centre": {"tag": "BUTTON",
                                  "cls": "flex justify-center", "id": ""},
               "signature": False,
               "signature_reasons": ["centre-hit-interactive:BUTTON",
                                     "not-banner-shaped:248x248"]}
        page, logs = self._run([box])
        self.assertEqual(page.mouse.clicks, [])
        self.assertTrue(any("lack banner signature" in m for m in logs))

    def test_signed_banner_box_still_clicked(self):
        box = {"tag": "DIV", "cls": "banner-host", "id": "",
               "rect": {"x": 0, "y": 0, "w": 1920, "h": 90},
               "children": 0,
               "host_at_centre": {"tag": "DIV", "cls": "", "id": ""},
               "signature": True, "signature_reasons": []}
        page, logs = self._run([box])
        self.assertIn((1904.0, 16.0), page.mouse.clicks)
        self.assertTrue(any("closed-hollow-box-via-coordinate-X" in m
                            for m in logs))

    def test_source_has_signature_gate(self):
        src = inspect.getsource(R._dismiss_teuteuf_banner)
        self.assertIn("signature", src)
        self.assertIn("signature_reasons", src)
        self.assertIn("light-dom-game-descendant", src)
        self.assertIn("centre-hit-interactive", src)


# --------------------------------------------------------------------- G4

class TestG4AnchoredLoggingIter23(unittest.TestCase):
    def _page(self, scan_results):
        state = {"n": 0}

        def _ev(js, arg=None):
            if "G4-LIVE-ROUNDS-SCAN" in js:
                out = scan_results[min(state["n"],
                                       len(scan_results) - 1)]
                state["n"] += 1
                return out
            if "G4-LIVE-ROUNDS-TOGGLE" in js:
                return {"opened": True, "via": "aria-toggle"}
            return None

        return _FakePage(_ev)

    def test_null_anchor_logged_on_every_attempt(self):
        logs = []
        orig = R._dbg
        R._dbg = _dbg_recorder(logs)
        try:
            page = self._page([{"entries": [], "container": False,
                                "diag": [], "anchored": None}])
            self.assertIsNone(
                R._s_worldle_live_round_modes(page, "worldle"))
        finally:
            R._dbg = orig
        # Per-attempt lines (exactly 3 attempts). Note the
        # failure-path line also contains "anchored=" (as
        # "anchored=None"), so match the per-attempt shape.
        import re as _re
        attempt_lines = [m for m in logs
                         if _re.search(r"attempt [123] anchored=", m)]
        self.assertEqual(len(attempt_lines), 3)
        self.assertTrue(any("anchored=None" in m for m in logs))


# ----------------------------------------------------------------- Framed

class TestFramedBreakdownIter23(unittest.TestCase):
    def test_breakdown_counts_logged(self):
        src = inspect.getsource(R._assemble_framed)
        self.assertIn("framed breakdown:", src)
        self.assertIn("guesses played /", src)
        self.assertIn("final answers", src)

    def test_facts_card_renders_framed_stats(self):
        steps = [{"turn": i + 1, "guess": a, "pattern": ""}
                 for i, a in enumerate(
                     ["Warm Bodies", "The Blob",
                      "Despicable Me 2", "The Deer Hunter"])]
        with tempfile.TemporaryDirectory() as td:
            out = str(Path(td) / "facts.png")
            stats = DP.generate_facts(
                out, "Framed", "October 18, 2026", "4/4 modes won",
                steps, scoring=False, guesses_played=8,
                final_answers=4)
            self.assertTrue(Path(out).exists())
            self.assertIn(("8", "guesses played"), stats)
            self.assertIn(("4", "final answers"), stats)


class TestCapitalPickerIter23Additions(unittest.TestCase):
    """Additions: the ANALYSIS20 §2 exact dot geometry must drive
    nearest-dot, and the repaired probe JS strings must be
    brace-balanced (the iter-20..22 main probe never parsed)."""

    def test_synthetic_four_dots_nearest_dot(self):
        # ANALYSIS20 §2 per_tag evidence: 4 circles with
        # fill='#5252ff', rects (1071,429 18x18), (1076,275
        # 18x18), (1001,433 18x18), (1098,630 18x18) -> centres
        # below. Nearest to the fit point (1072.5,422.7) must be
        # the (1071,429) circle (centre (1080,438), ~17px).
        def _dot(x, y, id_):
            return {"x": float(x), "y": float(y), "r": 9.0,
                    "fill": "#5252ff", "cls": "", "id": id_,
                    "data": {}, "title": "", "aria": "",
                    "label": "", "parent_text": "",
                    "parent_data": {}, "group_title": "",
                    "sibling_text": "", "overlay_label": "",
                    "grandparent_text": "", "grandparent_title": "",
                    "grandparent_data": {}, "text": "",
                    "tag": "circle"}

        dots = [_dot(1080, 438, "right-811"),
                _dot(1085, 284, "wrong-0"),
                _dot(1010, 442, "wrong-1"),
                _dot(1107, 639, "wrong-2")]
        for d in dots:
            self.assertTrue(
                R._worldle_capital_is_blueish(d["fill"])
                or R._worldle_capital_is_blueish("rgb(82,82,255)"))
        near = R._worldle_capital_nearest_dot(dots, (1072.5, 422.7))
        self.assertIsNotNone(near)
        self.assertEqual((near[0]["x"], near[0]["y"]),
                         (1080.0, 438.0))
        self.assertEqual(near[0]["id"], "right-811")
        probe = {"found": True, "dots": dots, "n_paths": 29,
                 "n_dots": 4, "in_fill": False, "corrected": None}
        got = R._worldle_capital_pick_pin_target(
            probe, (1072.5, 422.7), "Kuwait City")
        self.assertIsNotNone(got)
        self.assertEqual(got["method"], "nearest-dot")
        self.assertEqual((got["x"], got["y"]), (1080.0, 438.0))
        self.assertEqual(got["dot_index"], 0)

    def test_probe_js_brace_balanced(self):
        # Single-pass scanner: track string / template / line-
        # comment / block-comment state together, so an
        # apostrophe in a // comment (e.g. "game's") cannot open
        # a phantom string and swallow braces, and a // inside a
        # string literal cannot start a phantom comment.
        def _balance(js):
            depth = min_depth = 0
            in_str = None
            in_tpl = False
            in_block = False
            for line in js.split("\n"):
                i = 0
                while i < len(line):
                    ch = line[i]
                    if in_block:
                        if (ch == "*" and i + 1 < len(line)
                                and line[i + 1] == "/"):
                            in_block = False
                            i += 2
                            continue
                        i += 1
                        continue
                    if in_str is not None:
                        if ch == "\\":
                            i += 2
                            continue
                        if ch == in_str:
                            in_str = None
                        i += 1
                        continue
                    if in_tpl:
                        if ch == "\\":
                            i += 2
                            continue
                        if ch == "`":
                            in_tpl = False
                        i += 1
                        continue
                    if ch == "/" and i + 1 < len(line) \
                            and line[i + 1] == "/":
                        break
                    if ch == "/" and i + 1 < len(line) \
                            and line[i + 1] == "*":
                        in_block = True
                        i += 2
                        continue
                    if ch in ("'", '"'):
                        in_str = ch
                        i += 1
                        continue
                    if ch == "`":
                        in_tpl = True
                        i += 1
                        continue
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        min_depth = min(min_depth, depth)
                    i += 1
            return depth, min_depth

        def _extract_js(func_name, needle):
            path = Path(__file__).parent.parent / "runner.py"
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            for node in ast.walk(tree):
                if (isinstance(node, ast.FunctionDef)
                        and node.name == func_name):
                    for sub in ast.walk(node):
                        if (isinstance(sub, ast.Constant)
                                and isinstance(sub.value, str)
                                and needle in sub.value):
                            return sub.value
            raise AssertionError(
                f"JS with {needle!r} not found in {func_name}")

        for fn, needle in (
                ("_worldle_capital_pinpoint_probe",
                 "pin-capital-svg"),
                ("_worldle_capital_probe_diagnostics",
                 "worldle-capital-probe-diagnostics"),
                ("_s_worldle_visible_area_ui",
                 "worldle-visible-area")):
            depth, min_depth = _balance(_extract_js(fn, needle))
            self.assertEqual(depth, 0, fn)
            self.assertGreaterEqual(min_depth, 0, fn)


DS23 = "2026-10-18"


class _FakeAreaSrcPage23(_FakePage):
    """Fake for _s_worldle_play_comparison with Image-Quiz-Item
    options carrying country-shape srcs."""

    def __init__(self, visible=True, record=False):
        super().__init__()
        self.visible = visible
        self.record = record
        self.store = []
        self.clicked = []

    def evaluate(self, js, arg=None):
        self.calls.append(js)
        if "worldle-visible-area" in js:
            return {"visible": self.visible, "signals": [],
                    "prompt_visible": self.visible, "selected_tab": "",
                    "prompt_text": "", "next_round_visible": False,
                    "container": ({"tag": "SECTION", "cls": "area",
                                   "id": "area",
                                   "rect": {"x": 400, "y": 200,
                                            "w": 1120, "h": 600}}
                                  if self.visible else None),
                    "reason": "" if self.visible else "not visible",
                    "rejected": 0, "rejection_reasons": {},
                    "probe_ran": True}
        if "worldle-comparison-probe" in js:
            return {"prompt_found": True,
                    "options": [
                        {"name": "Image Quiz Item", "x": 833, "y": 300,
                         "has_img": True, "text": "", "aria": "",
                         "alt": "", "title": "",
                         "src": ("https://cdn-assets.teuteuf.fr/"
                                 "country-shapes/kw.svg")},
                        {"name": "Image Quiz Item", "x": 1200, "y": 300,
                         "has_img": True, "text": "", "aria": "",
                         "alt": "", "title": "",
                         "src": ("https://cdn-assets.teuteuf.fr/"
                                 "country-shapes/qa.svg")}]}
        if "worldle-country-code-map" in js:
            return {"kw": "Kuwait", "qa": "Qatar"}
        if "worldle-country-stat-fetch" in js:
            return []
        if "worldle-comparison-card-click" in js:
            return [False, "no card found", "card", "", ""]
        if "worldle-comparison-click" in js:
            self.clicked.append("option")
            if self.record:
                self.store = [{"question": 1, "answer": "Kuwait",
                               "correct": "Kuwait"}]
            return [True, "Kuwait", "button", "BUTTON", "cls",
                    {"x": 500, "y": 300, "w": 200, "h": 80},
                    600, 340]
        if "worldle-comparison-selected-state" in js:
            sel = bool(self.record)
            return {"selected": ([{"name": "Kuwait"}] if sel else []),
                    "selected_name": "Kuwait" if sel else "",
                    "options": [{"name": "Kuwait", "selected": sel}],
                    "wanted_selected": sel,
                    "any_selected": sel}
        if "document.body.innerText" in js:
            return ("Which country has a larger surface area? "
                    "Kuwait Qatar")
        if "localStorage" in js:
            if arg == "size-guesses":
                return {DS23: list(self.store)}
            if arg == "rounds-completed":
                return {DS23: []}
            if arg == "game_round":
                return {"gameMode": "area"}
            return {}
        return None


class TestAreaEndToEndIter23Additions(unittest.TestCase):
    """Additions: the full comparison flow with src-resolved
    options (gate + click must actually execute), the gate on
    the pick-None path, and the unattributed-pick verdict."""

    def test_src_resolved_options_gate_runs_click_runs(self):
        logs = []
        orig = R._dbg
        R._dbg = _dbg_recorder(logs)
        try:
            page = _FakeAreaSrcPage23(visible=True, record=True)
            st, pick, det = R._s_worldle_play_comparison(
                page, "worldle", {}, "area", DS23,
                ("larger surface area",),
                {"kuwait": 17818, "qatar": 11000},
                "size", "area", verify_store=True)
        finally:
            R._dbg = orig
        # No early return: the gate ran, the Area click path
        # ran, the pick was recorded.
        self.assertEqual(st, "attempted")
        self.assertEqual(pick, "Kuwait")
        self.assertNotIn("NOT clicked", det)
        self.assertTrue(any("worldle-visible-area" in c
                            for c in page.calls))
        self.assertTrue(any("worldle-comparison-click" in c
                            for c in page.calls))
        self.assertTrue(any("src-resolved" in m for m in logs))

    def test_pick_none_still_logs_gate(self):
        logs = []
        orig = R._dbg
        R._dbg = _dbg_recorder(logs)
        try:
            def _ev(js, arg=None):
                if "worldle-visible-area" in js:
                    return {"visible": False, "signals": [],
                            "prompt_visible": False, "selected_tab": "",
                            "prompt_text": "",
                            "next_round_visible": False,
                            "container": None,
                            "reason": "no truly-visible Area prompt",
                            "rejected": 1, "rejection_reasons": {},
                            "probe_ran": True}
                if "worldle-comparison-probe" in js:
                    return {"prompt_found": True,
                            "options": [
                                {"name": "Image Quiz Item", "x": 1,
                                 "y": 1, "src": ""},
                                {"name": "Image Quiz Item", "x": 2,
                                 "y": 2, "src": ""}]}
                if "document.body.innerText" in js:
                    return "Which country has a larger surface area?"
                return None

            page = _FakePage(_ev)
            st, pick, det = R._s_worldle_play_comparison(
                page, "worldle", {}, "area", DS23,
                ("larger surface area",), {}, "size", "area",
                verify_store=True)
        finally:
            R._dbg = orig
        self.assertEqual(st, "partial")
        self.assertIsNone(pick)
        self.assertIn("NOT clicked", det)
        self.assertIn("gate evaluated on unresolvable path", det)
        self.assertTrue(any("gate evaluated on unresolvable path" in m
                            for m in logs))
        self.assertFalse(any("worldle-comparison-click" in c
                             for c in page.calls))

    def test_empty_code_map_keeps_options(self):
        page = _FakePage(lambda js, arg=None: {})
        opts = [{"name": "Image Quiz Item",
                 "src": "https://cdn-assets.teuteuf.fr/"
                        "country-shapes/kw.svg"}]
        got, det = R._s_worldle_resolve_option_names_by_src(page, opts)
        self.assertEqual(got[0]["name"], "Image Quiz Item")
        self.assertIn("unavailable", det)

    def test_unattributed_pick_flagged_in_area_verdict(self):
        logs = []
        orig = R._dbg
        R._dbg = _dbg_recorder(logs)
        try:
            # Comparison returns partial (nothing clicked via
            # the Area path) but the store ALREADY records a
            # correct pick — the iter-22 banner-misfire shape.
            page = _FakeAreaSrcPage23(visible=True, record=False)
            page.store = [{"question": 1, "answer": "Kuwait",
                           "correct": "Kuwait"}]
            _orig_probe = page.evaluate

            def _ev(js, arg=None):
                if "worldle-comparison-probe" in js:
                    return {"prompt_found": True,
                            "options": [
                                {"name": "Image Quiz Item", "x": 1,
                                 "y": 1, "src": ""},
                                {"name": "Image Quiz Item", "x": 2,
                                 "y": 2, "src": ""}]}
                return _orig_probe(js, arg)

            page.evaluate = _ev
            day = {"date": DS23, "size": 17818,
                   "size_lookup": {"kuwait": 17818,
                                   "qatar": 11000}}
            st, guesses, det = R._s_worldle_play_area(
                page, "worldle", day)
        finally:
            R._dbg = orig
        self.assertEqual(st, "partial")
        self.assertIn("STORE ALREADY RECORDS A PICK", det)
        self.assertIn("NOT attributable to the Area path", det)
        self.assertFalse(page.clicked)


class TestTeuteufSelectionIter23Additions(unittest.TestCase):
    """Additions: with mixed candidates, the click must go to
    the signature-True box, never to a game-UI box."""

    def test_signature_false_skipped_for_signature_true(self):
        logs = []
        orig = R._dbg
        R._dbg = _dbg_recorder(logs)
        try:
            card = {"tag": "BUTTON", "cls": "", "id": "",
                    "rect": {"x": 709, "y": 176, "w": 248, "h": 248},
                    "children": 1,
                    "host_at_centre": {"tag": "BUTTON",
                                       "cls": "flex justify-center",
                                       "id": ""},
                    "signature": False,
                    "signature_reasons": ["interactive-tag:BUTTON"]}
            banner = {"tag": "DIV", "cls": "banner-host", "id": "",
                      "rect": {"x": 0, "y": 0, "w": 1920, "h": 90},
                      "children": 0,
                      "host_at_centre": {"tag": "DIV", "cls": "",
                                         "id": ""},
                      "signature": True, "signature_reasons": []}
            state = {"n": 0}

            def _ev(js, arg=None):
                state["n"] += 1
                if state["n"] == 1:
                    return _unverified([card, banner])
                return _unverified([])

            page = _FakePage(_ev)
            R._dismiss_teuteuf_banner(page)
        finally:
            R._dbg = orig
        # The card's X (941,192) must never be clicked; the
        # banner's X (1904,16) is the only click.
        self.assertNotIn((941.0, 192.0), page.mouse.clicks)
        self.assertIn((1904.0, 16.0), page.mouse.clicks)


class TestG4SuccessAnchorIter23Additions(unittest.TestCase):
    """Additions: the anchored line must also appear on the
    success path (the old code only logged it on failure, and
    only when non-null)."""

    def test_anchored_logged_on_success(self):
        logs = []
        orig = R._dbg
        R._dbg = _dbg_recorder(logs)
        try:
            entries = ["🌍 Country ✓", "Neighbours", "📍 Capital ✓",
                       "Orientation", "Flag", "Trivia", "Landmark",
                       "Emblem", "Spoken languages", "GDP Per Capita",
                       "Population & Currency", "Populous cities",
                       "Area"]
            state = {"n": 0}

            def _ev(js, arg=None):
                if "G4-LIVE-ROUNDS-SCAN" in js:
                    state["n"] += 1
                    return {"entries": list(entries),
                            "container": True,
                            "anchored": {"tag": "X-MENU", "cls": "",
                                         "role": "",
                                         "via": "aria-controls",
                                         "score": 13, "leaves": 13}}
                if "G4-LIVE-ROUNDS-TOGGLE" in js:
                    return {"opened": True, "via": "aria-toggle"}
                return None

            page = _FakePage(_ev)
            got = R._s_worldle_live_round_modes(page, "worldle")
        finally:
            R._dbg = orig
        self.assertIsNotNone(got)
        self.assertEqual(len(got["mapped"]), 13)
        self.assertTrue(any("anchored=" in m and "X-MENU" in m
                            for m in logs))


if __name__ == "__main__":
    unittest.main()
