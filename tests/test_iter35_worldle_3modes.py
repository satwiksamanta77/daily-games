"""Fix-loop iteration 35 tests — Worldle 3-mode play fix.

Run 39138524274 (target 2026-10-19, Canada): Worldle 10/13.
1. CAPITAL: picker clicked dot 'wrong-2' (nearest-dot, 5.9px from the
   projected Ottawa point) instead of 'right-124'. PLUS the arbitrate
   line printed pin_ok=True while the mode verdict used pin_ok=False
   (two different computations: landed vs correct).
2. POPULATIONCURRENCY: probe expected a numeric input; the site shows
   four bucket buttons. Root cause: the bucket matcher tested
   t.endsWith('+') but the live top bucket is '35+ million' ('+'
   mid-string), so no top-bucket population ever matched.
3. POPULOUSCITIES: shown-set probe matched [] — it only scanned
   button/[role=option]/li/[draggable=true], but chips/slots are
   plain DIVs. Fix: leaf-text probe + always drag by rank.

The JS probes are tested by running the EXACT runtime JS (captured
from the functions via a recording FakePage) under node with stub
DOMs — not a Python mirror, not source text.
"""
import inspect
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

import runner as R


def _dot(x, y, **kw):
    base = {"x": float(x), "y": float(y), "r": 8.0, "fill": "blue",
            "cls": "", "id": "", "data": {}, "title": "", "aria": "",
            "label": "", "label_dist": None, "parent_text": "",
            "parent_data": {}, "group_title": "", "sibling_text": "",
            "overlay_label": "", "grandparent_text": "",
            "grandparent_title": "", "grandparent_data": {}, "text": "",
            "tag": "circle"}
    base.update(kw)
    return base


class _CapturePage:
    """Records every page.evaluate JS string; returns None/{}."""

    def __init__(self):
        self.js_calls = []

    def evaluate(self, js, arg=None):
        self.js_calls.append(js)
        return None


def _capture_js(fn, *args, **kwargs):
    page = _CapturePage()
    try:
        fn(page, *args, **kwargs)
    except Exception:
        pass
    return page.js_calls


def _run_node(js_src, prelude, call):
    """Run runtime JS under node with a stub DOM prelude; return
    the parsed JSON printed by `call`."""
    script = prelude + "\nconst fn = eval('(' + " + json.dumps(js_src) \
        + " + ')');\nconsole.log(JSON.stringify(" + call + "));\n"
    with tempfile.NamedTemporaryFile("w", suffix=".js",
                                     delete=False) as f:
        f.write(script)
        path = f.name
    out = subprocess.run(["node", path], capture_output=True, text=True,
                         timeout=30)
    assert out.returncode == 0, f"node failed: {out.stderr[:500]}"
    return json.loads(out.stdout.strip().splitlines()[-1])


# ---------------------------------------------------------------- Capital

class TestCapitalDotIdIter35(unittest.TestCase):
    FIT = (1031.3, 651.9)  # run 39138524274 fit point for Ottawa

    def _canada_probe(self):
        dots = [_dot(1044.0, 631.0, id="right-124"),
                _dot(949.0, 618.0, id="wrong-0"),
                _dot(853.0, 621.0, id="wrong-1"),
                _dot(1028.0, 657.0, id="wrong-2")]
        return {"found": True, "dots": dots, "n_paths": 0, "n_dots": 4,
                "in_fill": False, "corrected": None}

    def test_canada_picks_right_dot_not_nearest_wrong(self):
        # The iter-35 regression: old code returned nearest-dot
        # wrong-2 (5.9px from fit); the graded id must win.
        got = R._worldle_capital_pick_pin_target(
            self._canada_probe(), self.FIT, "Ottawa")
        self.assertIsNotNone(got)
        self.assertEqual(got["method"], "dot-id-right")
        self.assertEqual((got["x"], got["y"]), (1044.0, 631.0))
        self.assertEqual(got["dot_index"], 0)
        self.assertEqual(got["dot_tag"], "circle")

    def test_kuwait_picks_right_811(self):
        dots = [_dot(1085.0, 440.0, id="right-811"),
                _dot(1010.0, 440.0, id="wrong-0"),
                _dot(1090.0, 435.0, id="wrong-1")]
        probe = {"found": True, "dots": dots, "n_paths": 0, "n_dots": 3,
                 "in_fill": False, "corrected": None}
        got = R._worldle_capital_pick_pin_target(
            probe, (1072.5, 422.7), "Kuwait City")
        self.assertEqual(got["method"], "dot-id-right")
        self.assertEqual((got["x"], got["y"]), (1085.0, 440.0))

    def test_no_grading_ids_nearest_dot_unchanged(self):
        dots = [_dot(1085.0, 285.0), _dot(1090.0, 435.0),
                _dot(1010.0, 440.0)]
        probe = {"found": True, "dots": dots, "n_paths": 0, "n_dots": 3,
                 "in_fill": False, "corrected": None}
        got = R._worldle_capital_pick_pin_target(
            probe, (1072.5, 422.7), "Kuwait City")
        self.assertEqual(got["method"], "nearest-dot")
        self.assertEqual((got["x"], got["y"]), (1090.0, 435.0))

    def test_two_right_dots_ambiguous_no_click(self):
        dots = [_dot(1044.0, 631.0, id="right-124"),
                _dot(1028.0, 657.0, id="right-125")]
        probe = {"found": True, "dots": dots, "n_paths": 0, "n_dots": 2,
                 "in_fill": False, "corrected": None}
        got = R._worldle_capital_pick_pin_target(
            probe, self.FIT, "Ottawa")
        self.assertIsNone(got)

    def test_dot_id_excluded_from_dot_name_id_source(self):
        # A grading id must never match as a NAME: a dot whose only
        # id is 'wrong-2' must not dot-name-match a capital whose
        # name is a substring of it, and the picker source must
        # carry the exclusion.
        src = inspect.getsource(R._worldle_capital_pick_pin_target)
        self.assertIn("right|wrong", src)
        dots = [_dot(1044.0, 631.0, id="wrong-2")]
        probe = {"found": True, "dots": dots, "n_paths": 0, "n_dots": 1,
                 "in_fill": True, "corrected": None}
        got = R._worldle_capital_pick_pin_target(
            probe, (1044.0, 631.0), "Wrong")
        # Must NOT return dot-name via the id; nearest-dot (the
        # dot is at the fit point) is the only honest method here.
        self.assertNotEqual((got or {}).get("method"), "dot-name")

    def test_caller_routes_dot_id_right_to_element_click(self):
        src = inspect.getsource(R._s_worldle_play_capital)
        self.assertIn('"dot-id-right"', src)

    def test_arbitrate_split_landed_vs_correct(self):
        src = inspect.getsource(R._s_worldle_play_capital)
        # Old single flag is gone; arbitrate logs landed + correct.
        self.assertNotIn("def _pin_ok", src)
        self.assertIn("def _pin_arbitrate", src)
        self.assertIn("landed={_landed} correct={_correct}", src)
        self.assertIn('"landed": _landed', src)
        self.assertIn('"correct": _right', src)


# ------------------------------------------------------- Population bucket

_POP_PRELUDE = """
const clicked = [];
const BUTTONS = %s;
const buttons = BUTTONS.map(t => ({innerText: t, offsetParent: {},
  closest: () => null, click: function() { clicked.push(t); }}));
global.document = { querySelectorAll: (sel) => buttons };
"""


class TestPopulationBucketIter35(unittest.TestCase):
    CANADA = ["< 15 million", "15 - 25 million", "25 - 35 million",
              "35+ million"]
    KUWAIT = ["< 3 million", "3 - 5 million", "5 - 8 million",
              "8+ million"]

    def _run(self, buttons, pop):
        js_calls = _capture_js(R._s_worldle_population_bucket, pop)
        assert js_calls, "no JS captured"
        prelude = _POP_PRELUDE % json.dumps(buttons)
        res = _run_node(js_calls[0], prelude,
                         "({out: fn(%s), clicked: clicked})" % pop)
        return res

    def test_canada_top_bucket_this_bug(self):
        # Run 39138524274: 41,798,407 must click '35+ million'.
        # Old code returned None (endsWith('+') never matched).
        res = self._run(self.CANADA, 41798407)
        self.assertEqual(res["out"], "35+ million")
        self.assertEqual(res["clicked"], ["35+ million"])

    def test_canada_boundaries(self):
        cases = [(35000000, "35+ million"),
                 (34999999, "25 - 35 million"),
                 (25000000, "25 - 35 million"),
                 (15000000, "15 - 25 million"),
                 (14999999, "< 15 million"),
                 (20000000, "15 - 25 million")]
        for pop, want in cases:
            with self.subTest(pop=pop):
                res = self._run(self.CANADA, pop)
                self.assertEqual(res["out"], want)

    def test_old_kuwait_set_still_works(self):
        res = self._run(self.KUWAIT, 5118445)
        self.assertEqual(res["out"], "5 - 8 million")
        res = self._run(self.KUWAIT, 50000000)
        self.assertEqual(res["out"], "8+ million")

    def test_no_endswith_plus_in_bucket_matchers(self):
        # Both bucket matchers (GDP + population) now use
        # includes('+'); the old endsWith pattern must be gone
        # from the matcher code (it survives only in a
        # docstring describing the old bug).
        for fn in (R._s_worldle_population_bucket,
                   R._s_worldle_gdp_bucket):
            src = inspect.getsource(fn)
            self.assertNotIn("t.endsWith('+')", src)
            self.assertIn("t.includes('+')", src)


# ------------------------------------------------------------- Populous

_POPULOUS_PRELUDE = """
function mkEl(text, x, y, w, h, children, inNav) {
  return { innerText: text, offsetParent: {}, children: children || [],
    closest: (sel) => inNav ? {} : null,
    getBoundingClientRect: () => ({x: x, y: y, width: w, height: h}) };
}
const NAV_TORONTO = mkEl('Toronto', 0, 0, 50, 20, [], true);
const CHIP_MONTREAL = mkEl('Montreal', 900, 300, 160, 44, []);
const CHIP_OTTAWA = mkEl('Ottawa', 900, 360, 160, 44, []);
const CHIP_TORONTO = mkEl('Toronto', 900, 420, 160, 44, []);
const CHIP_CALGARY = mkEl('Calgary', 900, 480, 160, 44, []);
const SLOT1 = mkEl('1. 3M', 300, 300, 200, 60, []);
const SLOT2 = mkEl('2. 2M', 300, 380, 200, 60, []);
const SLOT3 = mkEl('3. 1M', 300, 460, 200, 60, []);
const SLOT4 = mkEl('4. 1M', 300, 540, 200, 60, []);
const CONTAINER = mkEl('Toronto\\ncontainer', 100, 100, 300, 200,
  [CHIP_TORONTO]);
const CHROME = mkEl('Guess', 500, 700, 120, 44, []);
const ALL = [NAV_TORONTO, CHIP_MONTREAL, CHIP_OTTAWA, CHIP_TORONTO,
  CHIP_CALGARY, SLOT1, SLOT2, SLOT3, SLOT4, CONTAINER, CHROME];
global.document = { querySelectorAll: (sel) => ALL };
"""


class TestPopulousProbeIter35(unittest.TestCase):
    def _probe(self, want):
        js_calls = _capture_js(R._worldle_populous_probe, want)
        assert js_calls, "no JS captured from populous probe"
        return _run_node(js_calls[0], _POPULOUS_PRELUDE,
                         "fn(" + json.dumps({"want": want, "n": len(want)})
                         + ")")

    def test_probe_finds_div_chips_and_rank_slots(self):
        want = ["Toronto", "Montreal", "Calgary", "Ottawa"]
        res = self._probe(want)
        chips = {c["name"]: c for c in res["chips"]}
        self.assertEqual(set(chips), set(want))
        # Chip centres from the fixture rects.
        self.assertEqual((chips["Toronto"]["x"], chips["Toronto"]["y"]),
                         (980.0, 442.0))
        slots = res["slots"]
        self.assertEqual([s["rank"] for s in slots], [1, 2, 3, 4])
        self.assertEqual([s["label"] for s in slots],
                         ["1. 3M", "2. 2M", "3. 1M", "4. 1M"])
        # Nav-contained 'Toronto' must NOT double-count; the
        # container DIV (has children) is not a leaf.
        self.assertEqual(len(res["chips"]), 4)

    def test_probe_missing_chip(self):
        # No Ottawa chip in the DOM -> chips lack Ottawa.
        prelude = _POPULOUS_PRELUDE.replace(
            "const ALL = [NAV_TORONTO, CHIP_MONTREAL, CHIP_OTTAWA,",
            "const ALL = [NAV_TORONTO, CHIP_MONTREAL,")
        want = ["Toronto", "Montreal", "Calgary", "Ottawa"]
        js_calls = _capture_js(R._worldle_populous_probe, want)
        res = _run_node(js_calls[0], prelude,
                        "fn(" + json.dumps({"want": want, "n": 4}) + ")")
        self.assertNotIn("Ottawa", {c["name"] for c in res["chips"]})


class TestPopulousSlotContainsIter35(unittest.TestCase):
    def _contains(self, leaves, slot, name):
        js_calls = _capture_js(R._worldle_populous_slot_contains,
                               {"x": slot[0], "y": slot[1]}, name)
        assert js_calls, "no JS captured from slot_contains"
        els = []
        for text, x, y in leaves:
            els.append(
                "mkLeaf(%s, %s, %s)" % (json.dumps(text), x, y))
        prelude = """
function mkLeaf(text, x, y) {
  return { innerText: text, offsetParent: {}, children: [],
    closest: () => null,
    getBoundingClientRect: () => ({x: x - 40, y: y - 15,
      width: 80, height: 30}) };
}
const ALL = [%s];
global.document = { querySelectorAll: (sel) => ALL };
""" % ", ".join(els)
        return _run_node(js_calls[0], prelude,
                         "fn(" + json.dumps(
                             {"sx": slot[0], "sy": slot[1], "name": name})
                         + ")")

    def test_exact_name_near_centre(self):
        self.assertTrue(self._contains(
            [("Toronto", 400, 330)], (400, 330), "Toronto"))

    def test_other_name_near_centre_is_not_a_hit(self):
        self.assertFalse(self._contains(
            [("Montreal", 400, 330)], (400, 330), "Toronto"))

    def test_same_name_far_away_is_not_a_hit(self):
        self.assertFalse(self._contains(
            [("Toronto", 900, 420)], (400, 330), "Toronto"))


class _FakeMouse:
    def __init__(self):
        self.calls = []

    def move(self, x, y, steps=1):
        self.calls.append(("move", x, y, steps))

    def down(self):
        self.calls.append(("down",))

    def up(self):
        self.calls.append(("up",))

    def click(self, x, y):
        self.calls.append(("click", x, y))


class _FakePage:
    """Dispatches evaluate() by JS content for populous tests."""

    def __init__(self, probe=None, slot_seq=None, guess_disabled=False,
                 body="Can you organise the 4 most populated cities?"):
        self.probe = probe
        self.slot_seq = list(slot_seq) if slot_seq is not None else []
        self.guess_disabled = guess_disabled
        self.body = body
        self.mouse = _FakeMouse()
        self.drag_events = 0
        self.evaluated = []

    def evaluate(self, js, arg=None):
        self.evaluated.append(js)
        if js.strip() == "() => document.body.innerText":
            return self.body
        if "DataTransfer" in js:
            self.drag_events += 1
            return True
        if "Math.hypot" in js:
            if self.slot_seq:
                return self.slot_seq.pop(0)
            return True
        if "toLowerCase() === 'guess'" in js:
            return self.guess_disabled
        if "args.want" in js or "parseInt(m[1]" in js:
            return self.probe
        return {}

    def wait_for_timeout(self, ms):
        pass


class TestPopulousDragIter35(unittest.TestCase):
    CHIP = {"name": "Toronto", "x": 980.0, "y": 442.0}
    SLOT = {"rank": 1, "label": "1. 3M", "x": 400.0, "y": 330.0}

    def test_attempt1_success_returns_true(self):
        page = _FakePage(slot_seq=[True])
        self.assertTrue(R._worldle_populous_drag(
            page, "worldle", self.CHIP, self.SLOT, "Toronto"))
        self.assertEqual(page.drag_events, 0)
        self.assertIn(("down",), page.mouse.calls)

    def test_attempt2_after_attempt1_miss(self):
        page = _FakePage(slot_seq=[False, True])
        self.assertTrue(R._worldle_populous_drag(
            page, "worldle", self.CHIP, self.SLOT, "Toronto"))
        self.assertEqual(page.drag_events, 0)
        self.assertEqual(page.mouse.calls.count(("down",)), 2)

    def test_all_three_attempts_on_repeated_miss(self):
        page = _FakePage(slot_seq=[False, False, False])
        self.assertFalse(R._worldle_populous_drag(
            page, "worldle", self.CHIP, self.SLOT, "Toronto"))
        self.assertEqual(page.drag_events, 1)
        self.assertEqual(page.mouse.calls.count(("down",)), 2)


class TestPopulousPlayIter35(unittest.TestCase):
    WANT = ["Toronto", "Montreal", "Calgary", "Ottawa"]
    PROBE = {
        "chips": [{"name": "Montreal", "x": 980.0, "y": 322.0},
                  {"name": "Ottawa", "x": 980.0, "y": 382.0},
                  {"name": "Toronto", "x": 980.0, "y": 442.0},
                  {"name": "Calgary", "x": 980.0, "y": 502.0}],
        "slots": [{"rank": 1, "label": "1. 3M", "x": 400.0, "y": 330.0},
                  {"rank": 2, "label": "2. 2M", "x": 400.0, "y": 410.0},
                  {"rank": 3, "label": "3. 1M", "x": 400.0, "y": 490.0},
                  {"rank": 4, "label": "4. 1M", "x": 400.0, "y": 570.0}],
    }
    DAY = {"date": "2026-10-19",
           "populous": [{"name": n} for n in WANT]}

    def _page(self, **kw):
        return _FakePage(probe=self.PROBE, **kw)

    def test_full_play_drags_by_rank(self):
        page = self._page()
        with mock.patch.object(
                R, "_s_worldle_submit_guarded",
                return_value="submitted") as submit:
            status, played, detail = R._s_worldle_play_populous(
                page, "worldle", self.DAY)
        self.assertEqual(status, "attempted")
        self.assertEqual(played, self.WANT)
        submit.assert_called_once()
        # Drop targets = slot centres in rank order: the drag
        # ladder's final move of each attempt-1 drag lands on the
        # slot centre for that rank. Calgary -> slot 3, Ottawa ->
        # slot 4 (both '1M' labels — rank pairing, not bucket).
        moves = [c for c in page.mouse.calls if c[0] == "move"]
        slot_targets = [(400.0, 330.0), (400.0, 410.0),
                        (400.0, 490.0), (400.0, 570.0)]
        for target in slot_targets:
            self.assertTrue(
                any(abs(m[1] - target[0]) < 1 and
                    abs(m[2] - target[1]) < 1 for m in moves),
                f"no drag landed on slot {target}")

    def test_missing_chip_skips(self):
        probe = {"chips": [c for c in self.PROBE["chips"]
                           if c["name"] != "Ottawa"],
                 "slots": self.PROBE["slots"]}
        page = _FakePage(probe=probe)
        status, _, _ = R._s_worldle_play_populous(
            page, "worldle", self.DAY)
        self.assertEqual(status, "skipped")

    def test_bad_rank_slots_fail(self):
        probe = {"chips": self.PROBE["chips"],
                 "slots": [dict(s, rank=r) for s, r in
                           zip(self.PROBE["slots"], [1, 2, 4, 5])]}
        page = _FakePage(probe=probe)
        status, _, _ = R._s_worldle_play_populous(
            page, "worldle", self.DAY)
        self.assertEqual(status, "failed")

    def test_guess_still_disabled_fails(self):
        page = self._page(guess_disabled=True)
        with mock.patch.object(
                R, "_s_worldle_submit_guarded",
                return_value="submitted") as submit:
            status, _, _ = R._s_worldle_play_populous(
                page, "worldle", self.DAY)
        self.assertEqual(status, "failed")
        submit.assert_not_called()

    def test_click_path_deleted_from_populous(self):
        src = inspect.getsource(R._s_worldle_play_populous)
        self.assertNotIn("button, [role=option], li, [draggable=true]",
                         src)
        self.assertNotIn("click_option", src)
        self.assertNotIn("dom_kind", src)


if __name__ == "__main__":
    unittest.main()
