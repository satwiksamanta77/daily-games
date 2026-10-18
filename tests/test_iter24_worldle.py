"""Fix-loop iteration 24 tests — WORLDLE (evidence-grounded in
ANALYSIS21, run 39091631879).

- Capital RC1 (P0): `_s_worldle_play_capital` had NO return on the
  pin_ok=True path (fell off the end -> None -> dispatcher
  TypeError, aborting 9 bonus modes), and `run_one`'s bare
  `except TypeError` caught that INNER unpack error and re-raised
  the misleading "missing 'gid'" error. Now: an explicit
  `return "attempted", [name], "pin landed; ..."` on the pin-ok
  path, and the fallback retries without gid ONLY for a
  signature mismatch naming this solver call.
- Capital RC2 (P1): marker detection was recolour-blind — the game
  recolours the correct dot blue #5252ff -> green (+ confetti +
  "Next round") instead of adding a marker element, so 4->4 was
  misread as "no marker". Now: a blue->green dot recolour, the
  "Next round" signal, or confetti counts as the marker signal.
- G4 RC5 (P1): the live-rounds reader returned None on all
  attempts; toggle opened but every markup/anchored/content
  pass scored 0. Now: leaf passes pierce shadow roots, and a
  before/after-toggle DOM diff names what the toggle actually
  opened (or closed) without guessing the menu's markup.

All tests are synthetic/local: no network, no browser, no CI run.
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


# ------------------------------------------------------- RC1: missing return

class TestCapitalMissingReturnIter24(unittest.TestCase):
    def test_pin_ok_path_has_explicit_return(self):
        # Structural proof: the function's pin_ok=True path must
        # end in an explicit return — no fall-through to None.
        src = inspect.getsource(R._s_worldle_play_capital)
        self.assertIn('return "attempted", [name]', src)
        self.assertIn("pin landed; arbitrate=", src)

    def test_capital_success_path_returns_tuple_not_none(self):
        # Behavioural: drive the pin_ok=True path with faked
        # browser-bound probes (the recolour + return logic is
        # real). The iter-23 shape: dots blue before, the
        # clicked dot GREEN after, store delta + right entry.
        logs = []
        orig_dbg = R._dbg
        R._dbg = _dbg_recorder(logs)
        saved = {}
        for _name in ("_idle_drift", "_settle",
                      "_type_country_guess",
                      "_s_worldle_submit_guarded",
                      "_s_worldle_day_guesses",
                      "_worldle_capital_activate_pinpoint",
                      "_worldle_capital_pinpoint_probe",
                      "_worldle_capital_probe_diagnostics",
                      "_worldle_capital_click_dot_element",
                      "_worldle_pin_probe",
                      "_worldle_capital_dot_attempt_summary"):
            saved[_name] = getattr(R, _name)
        try:
            R._idle_drift = lambda page: None
            R._settle = lambda page, base=0: None
            R._type_country_guess = lambda p1, p2, name: (True,
                                                           "picked")
            R._s_worldle_submit_guarded = lambda *a, **k: None
            _guess_calls = {"n": 0}

            def _guesses(page, key, ds):
                _guess_calls["n"] += 1
                if _guess_calls["n"] <= 2:
                    return [{"question": 0, "name": "Kuwait City"}]
                return [{"question": 0, "name": "Kuwait City"},
                        {"question": 1, "name": "right-Kuwait City"}]

            R._s_worldle_day_guesses = _guesses
            R._worldle_capital_activate_pinpoint = \
                lambda page, gid: (True, "active")
            R._worldle_capital_probe_diagnostics = lambda page: {}
            R._worldle_capital_dot_attempt_summary = \
                lambda *a: "summary"

            def _blue_dot(x, y, id_):
                return {"x": float(x), "y": float(y), "r": 9.0,
                        "fill": "#5252ff",
                        "computed_fill": "rgb(82,82,255)", "bg": "",
                        "resolved_fill": "", "cls": "", "id": id_,
                        "data": {}, "title": "", "aria": "",
                        "label": "", "parent_text": "",
                        "parent_data": {}, "group_title": "",
                        "sibling_text": "", "overlay_label": "",
                        "grandparent_text": "",
                        "grandparent_title": "",
                        "grandparent_data": {}, "text": "",
                        "tag": "circle"}

            _probe_calls = {"n": 0}

            def _probe(page, px, py):
                _probe_calls["n"] += 1
                dots = [_blue_dot(1100, 380, "right-811"),
                        _blue_dot(1000, 300, "wrong-0"),
                        _blue_dot(1150, 450, "wrong-1"),
                        _blue_dot(1050, 250, "wrong-2")]
                if _probe_calls["n"] >= 2:
                    # After the click: the clicked dot recolours
                    # blue -> green (the iter-23 frame shape).
                    dots[0]["fill"] = "#22c55e"
                    dots[0]["computed_fill"] = "rgb(34,197,94)"
                return {"found": True, "dots": dots, "n_paths": 29,
                        "n_dots": 4, "in_fill": False,
                        "corrected": None}

            R._worldle_capital_pinpoint_probe = _probe
            R._worldle_capital_click_dot_element = \
                lambda page, px, py, name: {"clicked": True,
                                            "tag": "circle",
                                            "id": "right-811"}
            R._worldle_pin_probe = lambda page, px, py: {
                "markers": 4, "sig": "same-sig",
                "topTag": "CIRCLE", "topCls": "",
                "inMap": True}
            page = _FakePage(
                lambda js, arg=None:
                "can you guess where is Kuwait City?"
                if "document.body.innerText" in js else
                {"tag": "TEUTEUF-GEO-MAP", "cls": "", "viewBox": "",
                 "bbox": {"x": 900, "y": 200, "w": 400, "h": 300},
                 "dataset": {"minLon": 40.0, "maxLon": 55.0,
                             "minLat": 25.0, "maxLat": 35.0},
                 "aspect": 1.33, "outerHTML": ""})
            day = {"date": "2026-10-18",
                   "capital": {"name": "Kuwait City",
                               "lat": 29.3759, "lon": 47.9774},
                   "capitals": [{"name": "Kuwait City",
                                 "lat": 29.3759, "lon": 47.9774}],
                   "bounds": {"minLon": 40.0, "maxLon": 55.0,
                              "minLat": 25.0, "maxLat": 35.0}}
            got = R._s_worldle_play_capital(page, "worldle", day)
        finally:
            R._dbg = orig_dbg
            for _name, _fn in saved.items():
                setattr(R, _name, _fn)
        # THE RC1 regression shape was None here (implicit
        # fall-through). Now a proper 3-tuple.
        self.assertIsNotNone(got)
        self.assertEqual(len(got), 3)
        self.assertEqual(got[0], "attempted")
        self.assertEqual(got[1], ["Kuwait City"])
        self.assertIn("pin landed", got[2])
        # The RC2 recolour signal fired (no marker-count delta,
        # yet the pin was recognised as landed).
        self.assertTrue(any("recolour signal" in m for m in logs))

    def test_bonus_dispatcher_unpacks_capital_tuple(self):
        src = inspect.getsource(R._s_worldle_play_bonus)
        self.assertIn("return _s_worldle_play_capital(page, gid, day)",
                      src)


# ------------------------------------------- RC1: TypeError fallback narrowing

class _FakeSolver:
    pass


class TestTypeErrorFallbackIter24(unittest.TestCase):
    def test_inner_unpack_typeerror_is_not_signature_mismatch(self):
        # The exact iter-23 crash shape: the dispatcher unpack
        # TypeError raised INSIDE the solver.
        def _s_worldle(*a):
            raise TypeError(
                "cannot unpack non-iterable NoneType object")

        try:
            _s_worldle()
        except TypeError as e:
            self.assertFalse(
                R._solver_typeerror_is_signature_mismatch(e,
                                                          _s_worldle))

    def test_missing_gid_is_signature_mismatch(self):
        def _s_worldle(pg, aval, gid):
            return True, "ok"

        try:
            _s_worldle(None, "x")
        except TypeError as e:
            self.assertTrue(
                R._solver_typeerror_is_signature_mismatch(e,
                                                          _s_worldle))

    def test_too_many_args_is_signature_mismatch(self):
        def _two_arg_solver(pg, aval):
            return True, "ok"

        try:
            _two_arg_solver(None, "x", "worldle")
        except TypeError as e:
            self.assertTrue(
                R._solver_typeerror_is_signature_mismatch(
                    e, _two_arg_solver))

    def test_inner_call_mismatch_naming_other_fn_is_not(self):
        # An inner call's mismatch must NOT trigger the retry:
        # the message names the inner function, not the solver.
        def _s_worldle(pg, aval, gid):
            raise TypeError(
                "_helper() takes 1 positional argument but 2 "
                "were given")

        try:
            _s_worldle(None, "x", "worldle")
        except TypeError as e:
            self.assertFalse(
                R._solver_typeerror_is_signature_mismatch(e,
                                                          _s_worldle))

    def test_plain_typeerror_is_not_signature_mismatch(self):
        def _s_worldle(pg, aval, gid):
            raise TypeError("unsupported operand")

        try:
            _s_worldle(None, "x", "worldle")
        except TypeError as e:
            self.assertFalse(
                R._solver_typeerror_is_signature_mismatch(e,
                                                          _s_worldle))

    def test_run_one_uses_classifier(self):
        src = inspect.getsource(R.run_one)
        self.assertIn("_solver_typeerror_is_signature_mismatch", src)
        # The old bare `except TypeError:` with an unconditional
        # retry is gone.
        self.assertNotIn("except TypeError:\n", src)


# ------------------------------------------------------- RC2: recolour

class TestRecolourMarkerIter24(unittest.TestCase):
    def _dot(self, x, y, fill, computed=""):
        return {"x": float(x), "y": float(y), "r": 9.0,
                "fill": fill, "computed_fill": computed,
                "bg": "", "resolved_fill": "", "id": "d1"}

    def test_greenish_rule(self):
        self.assertTrue(R._worldle_capital_is_greenish("#22c55e"))
        self.assertTrue(R._worldle_capital_is_greenish("#00ff00"))
        self.assertTrue(R._worldle_capital_is_greenish("rgb(34,197,94)"))
        self.assertTrue(R._worldle_capital_is_greenish("green"))
        self.assertFalse(R._worldle_capital_is_greenish("#5252ff"))
        self.assertFalse(R._worldle_capital_is_greenish("#ff0000"))
        self.assertFalse(R._worldle_capital_is_greenish("#eab308"))
        self.assertFalse(R._worldle_capital_is_greenish(""))
        self.assertFalse(R._worldle_capital_is_greenish(None))

    def test_blue_to_green_counts(self):
        # The iter-23 frame shape: attribute fill #5252ff ->
        # #22c55e on the clicked dot.
        before = [self._dot(1100, 380, "#5252ff", "rgb(82,82,255)")]
        after = [self._dot(1100, 380, "#22c55e", "rgb(34,197,94)")]
        ok, det = R._worldle_capital_dot_recoloured(
            before, after, 1100, 380)
        self.assertTrue(ok)
        self.assertIn("blue->green", det)
        self.assertIn("near_click=True", det)

    def test_computed_fill_green_counts_when_attr_stays_blue(self):
        # The game's attribute fill may stay #5252ff while the
        # rendered (computed) fill turns green — no single
        # field is trusted alone.
        before = [self._dot(1100, 380, "#5252ff", "rgb(82,82,255)")]
        after = [self._dot(1100, 380, "#5252ff", "rgb(34,197,94)")]
        ok, _ = R._worldle_capital_dot_recoloured(before, after)
        self.assertTrue(ok)

    def test_no_change_does_not_count(self):
        before = [self._dot(1100, 380, "#5252ff", "rgb(82,82,255)")]
        after = [self._dot(1100, 380, "#5252ff", "rgb(82,82,255)")]
        ok, _ = R._worldle_capital_dot_recoloured(before, after)
        self.assertFalse(ok)

    def test_blue_to_red_does_not_count(self):
        # A wrong pin recolours red, not green — must not be
        # mistaken for a landed/correct pin.
        before = [self._dot(1100, 380, "#5252ff", "rgb(82,82,255)")]
        after = [self._dot(1100, 380, "#ef4444", "rgb(239,68,68)")]
        ok, _ = R._worldle_capital_dot_recoloured(before, after)
        self.assertFalse(ok)

    def test_already_green_does_not_count(self):
        before = [self._dot(1100, 380, "#22c55e", "rgb(34,197,94)")]
        after = [self._dot(1100, 380, "#22c55e", "rgb(34,197,94)")]
        ok, _ = R._worldle_capital_dot_recoloured(before, after)
        self.assertFalse(ok)

    def test_moved_dots_do_not_match(self):
        before = [self._dot(1100, 380, "#5252ff")]
        after = [self._dot(1200, 480, "#22c55e")]
        ok, _ = R._worldle_capital_dot_recoloured(before, after)
        self.assertFalse(ok)

    def test_recolour_wired_into_capital(self):
        src = inspect.getsource(R._s_worldle_play_capital)
        self.assertIn("_worldle_capital_dot_recoloured", src)
        self.assertIn("_marker_or_recolour", src)
        self.assertIn("Next round", src)
        self.assertIn("confetti", src)


# ------------------------------------------------------------- RC5: G4 diff

def _snap_el(text, x, y, chain):
    return {"text": text, "x": x, "y": y, "w": 100, "h": 24,
            "chain": chain}


def _menu_chain():
    return [{"tag": "DIV", "cls": "round-menu", "id": "menu1"},
            {"tag": "DIV", "cls": "app", "id": ""}]


def _menu_snapshot(texts, y0=100):
    return [_snap_el(t, 10, y0 + 30 * i, _menu_chain())
            for i, t in enumerate(texts)]


_MENU_LABELS = ["Country", "Neighbours", "Capital", "Orientation",
               "Flag", "Trivia", "Landmark", "Emblem",
               "Spoken languages", "GDP Per Capita",
               "Population & Currency", "Populous cities", "Area"]


class TestG4ToggleDiffIter24(unittest.TestCase):
    def test_appeared_menu_found_by_diff(self):
        before = [_snap_el("Worldle", 10, 10,
                           [{"tag": "HEADER", "cls": "", "id": ""}])]
        after = before + _menu_snapshot(_MENU_LABELS)
        got = R._worldle_g4_diff_entries(before, after,
                                         list(R._WORLDLE_MODE_LABELS
                                              .values()))
        self.assertTrue(got["container_new"])
        self.assertEqual(len(got["entries_new"]), 13)
        self.assertEqual(got["anchored_new"]["cls"], "round-menu")
        self.assertEqual(got["anchored_new"]["via"], "toggle-diff")
        self.assertEqual(got["new_label_matches"], 13)

    def test_fewer_than_three_label_matches_is_no_container(self):
        before = []
        after = _menu_snapshot(["Country", "Flag"])
        got = R._worldle_g4_diff_entries(before, after,
                                         list(R._WORLDLE_MODE_LABELS
                                              .values()))
        self.assertFalse(got["container_new"])
        self.assertEqual(got["entries_new"], [])

    def test_unrelated_new_elements_excluded(self):
        # An ad banner appearing at the same time must not
        # enter the menu entries: only the shared-container
        # elements count.
        before = []
        after = _menu_snapshot(_MENU_LABELS) + [
            _snap_el("Teuteuf Edu is here!", 10, 900,
                     [{"tag": "DIV", "cls": "banner", "id": ""}]),
            _snap_el("Remove ads", 10, 930,
                     [{"tag": "DIV", "cls": "banner", "id": ""}])]
        got = R._worldle_g4_diff_entries(before, after,
                                         list(R._WORLDLE_MODE_LABELS
                                              .values()))
        self.assertTrue(got["container_new"])
        self.assertEqual(len(got["entries_new"]), 13)
        self.assertNotIn("Teuteuf Edu is here!",
                         got["entries_new"])

    def test_disappeared_menu_is_removed_container(self):
        # The toggle CLOSED an already-open menu: the removed
        # diff names the was-open menu's entries.
        before = _menu_snapshot(_MENU_LABELS)
        after = []
        got = R._worldle_g4_diff_entries(before, after,
                                         list(R._WORLDLE_MODE_LABELS
                                              .values()))
        self.assertFalse(got["container_new"])
        self.assertTrue(got["container_removed"])
        self.assertEqual(len(got["entries_removed"]), 13)
        self.assertEqual(got["anchored_removed"]["via"],
                         "toggle-diff-removed")

    def test_no_change_is_no_container(self):
        snap = _menu_snapshot(_MENU_LABELS)
        got = R._worldle_g4_diff_entries(
            snap, list(snap),
            list(R._WORLDLE_MODE_LABELS.values()))
        self.assertFalse(got["container_new"])
        self.assertFalse(got["container_removed"])

    def test_reader_returns_diff_entries(self):
        # End-to-end through the reader: the scan finds no
        # container, the toggle opens, and the toggle-diff
        # supplies the 13 entries.
        logs = []
        orig = R._dbg
        R._dbg = _dbg_recorder(logs)
        try:
            base = [_snap_el("Worldle", 10, 10,
                             [{"tag": "HEADER", "cls": "",
                               "id": ""}])]
            state = {"n": 0}

            def _ev(js, arg=None):
                if "G4-DOM-SNAPSHOT" in js:
                    state["n"] += 1
                    if state["n"] == 1:
                        return list(base)
                    return list(base) + _menu_snapshot(_MENU_LABELS)
                if "G4-LIVE-ROUNDS-SCAN" in js:
                    return {"entries": [], "container": False,
                            "diag": [], "anchored": None}
                if "G4-LIVE-ROUNDS-TOGGLE" in js:
                    return {"opened": True, "via": "label-button"}
                return None

            page = _FakePage(_ev)
            got = R._s_worldle_live_round_modes(page, "worldle")
        finally:
            R._dbg = orig
        self.assertIsNotNone(got)
        self.assertEqual(len(got["mapped"]), 13)
        self.assertTrue(any("toggle-diff" in m for m in logs))

    def test_scan_js_pierces_shadow_roots(self):
        src = inspect.getsource(R._s_worldle_live_round_modes)
        self.assertIn("leavesIn", src)
        self.assertIn("shadowRoot", src)
        self.assertIn("toggle-diff", src)

    def test_anchored_logged_on_every_attempt_still(self):
        # Iter-23 behaviour must hold: anchored= on every attempt.
        logs = []
        orig = R._dbg
        R._dbg = _dbg_recorder(logs)
        try:
            def _ev(js, arg=None):
                if "G4-DOM-SNAPSHOT" in js:
                    return []
                if "G4-LIVE-ROUNDS-SCAN" in js:
                    return {"entries": [], "container": False,
                            "diag": [], "anchored": None}
                if "G4-LIVE-ROUNDS-TOGGLE" in js:
                    return {"opened": True, "via": "aria-toggle"}
                return None

            page = _FakePage(_ev)
            self.assertIsNone(
                R._s_worldle_live_round_modes(page, "worldle"))
        finally:
            R._dbg = orig
        import re as _re
        attempt_lines = [m for m in logs
                         if _re.search(r"attempt [123] anchored=", m)]
        self.assertEqual(len(attempt_lines), 3)


if __name__ == "__main__":
    unittest.main()
