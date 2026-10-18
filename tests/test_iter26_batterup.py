"""Fix-loop iteration 26 tests — Workstream B: Batterup real mouse click
(ANALYSIS23 RC-new-2).

The iter-25 failure, frame- + log-proven: zero log lines showed a
suggestion-row click; the only selection action was `re-selected
(selected via keyboard)`; all 9 submits were `via Enter key` with
`dropdown_open=True, populated_rows=0`; frames showed the suggestion
row open and unselected with no CMP dialog. The iter-25 fix shipped
as keyboard re-selection, not a mouse click.

Root cause (iter 26): Batterup's suggestion rows are `.result-box`
elements (the run-2 fix added that selector to `_type_country_guess`'s
pick), but the click + selection-state code never addressed them:
`_attr_click_suggestion_once`'s Playwright / mouse-events selector sets
and `_attr_selection_state`'s dropdown-open selectors covered only
React-autosuggest / `[role=option]` shapes. So the Playwright click
never found the row (returned False, silently — no log line), the
keyboard fallback "succeeded", and `_attr_selection_state` reported
selected=True VACUOUSLY (its open probe never saw the `.result-box`
row, so open=False while the row sat open and unselected).

Fix (runner.py, Batterup `_attr_*` + `solve_attr_game` only):
- `.result-box` / `[class*=result-box i]` / `#player-search .result-box`
  selectors added to the Playwright-click, mouse-coords, and
  mouse-events paths, to `_attr_selection_state`'s open detection,
  and to `_attr_post_submit_probe`'s dropdown detector.
- New `mouse-coords` method: a REAL `page.mouse.click` at the
  exact-match row's centre coordinates.
- Every real mouse click logs `suggestion row mouse-click:
  clicked=True method=... row=... rect=...` (iter 25 had zero such
  lines); `_attr_reselect` logs each method's attempt + verified
  selection state. Keyboard stays as a last fallback, but only
  counts when `_attr_selection_state` confirms the selection changed.
- Submit prefers the site's submit control (`#player-search
  button[type=submit]`) and uses Enter ONLY after a confirmed
  selection; `pick ok` still means SELECTED.
- Pitcher probes replaced by batter probes: the three no-suggestion
  names (Tinoco, Wade Miley, Jhoan Duran) are all pitchers (SP/RP)
  while both suggestion-producing names are batters (1B). Pitchers
  are excluded from the Batterup probe pool by position, and
  Miley/Duran are denylisted by name like Tinoco. The "suggestion
  index covers batters only" hypothesis is supported (5/5 names +
  all 8 vendored answers are position players) but not proven from
  site code; the fix does not depend on it (probes are disposable
  guesses; the answer always comes from the CDN, never the pool).
  The CMP re-check stays as a fallback only.

All tests are synthetic/local: fake pages/rows, monkeypatched helpers
— no browser, no network, no live play.

Run:  python3 -m unittest tests.test_iter26_batterup -v
"""

import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

import runner as R


class _FakeRow:
    """Suggestion-row stand-in (a `.result-box` element)."""

    def __init__(self, text, rect=None, click_raises=False):
        self._text = text
        self._rect = rect
        self._click_raises = click_raises
        self.clicked = False

    def inner_text(self):
        return self._text

    def scroll_into_view_if_needed(self, timeout=None):
        return None

    def bounding_box(self):
        return dict(self._rect) if self._rect else None

    def click(self, timeout=None):
        if self._click_raises:
            raise RuntimeError("click timeout")
        self.clicked = True


class _FakeButton:
    def __init__(self):
        self.clicked = False

    def is_visible(self):
        return True

    def click(self, timeout=None):
        self.clicked = True


class _FakeMouse:
    def __init__(self):
        self.clicks = []

    def click(self, x, y):
        self.clicks.append((x, y))


class _FakePage:
    """Minimal page stand-in recording presses / queries / mouse."""

    def __init__(self, rows=None, evaluate=None, keyboard_selects=False):
        self._rows = rows or []
        self._evaluate = evaluate  # fn(script, *args) -> value
        self._keyboard_selects = keyboard_selects
        self.selected_flag = False
        self.pressed = []
        self.queries = []
        self.evaluate_scripts = []
        self.keyboard = self
        self.mouse = _FakeMouse()
        self._qp_gid = "batterup"

    def wait_for_timeout(self, ms):
        return None

    def press(self, key):
        self.pressed.append(key)
        if key == "Enter" and self._keyboard_selects:
            self.selected_flag = True

    def query_selector(self, sel):
        self.queries.append(sel)
        return None

    def query_selector_all(self, sel):
        self.queries.append(sel)
        return list(self._rows)

    def evaluate(self, script, *args):
        self.evaluate_scripts.append(script)
        if self._evaluate is not None:
            return self._evaluate(script, *args)
        return ""


class _PatchHelpersMixin:
    def setUp(self):
        self._msgs = []
        self._dbg_patch = mock.patch.object(
            R, "_dbg", side_effect=lambda gid, msg: self._msgs.append(msg))
        self._dbg_patch.start()
        self.addCleanup(self._dbg_patch.stop)
        self._qp_patch = mock.patch.object(
            R, "QP",
            types.SimpleNamespace(ev_push=lambda *a, **k: None),
            create=True)
        self._qp_patch.start()
        self.addCleanup(self._qp_patch.stop)

    def _click_logs(self):
        return [m for m in self._msgs
                if "suggestion row mouse-click" in m]


class TestRealMouseClick(_PatchHelpersMixin, unittest.TestCase):
    _RECT = {"x": 100, "y": 200, "width": 200, "height": 40}

    def test_playwright_click_logs_a_real_click(self):
        # The iter-25 gap: a click with zero log lines is
        # indistinguishable from a click that never happened.
        row = _FakeRow("Alec Burleson", rect=self._RECT)
        page = _FakePage(rows=[row])
        did = R._attr_click_suggestion_once(
            page, "Alec Burleson", "playwright-click")
        self.assertTrue(did)
        self.assertTrue(row.clicked)
        logs = self._click_logs()
        self.assertEqual(len(logs), 1)
        self.assertIn("clicked=True", logs[0])
        self.assertIn("method=playwright-click", logs[0])
        self.assertIn("Alec Burleson", logs[0])

    def test_click_queries_result_box_selectors(self):
        # Regression: the iter-25 selector set never mentioned
        # .result-box, so the Playwright click never found the row.
        page = _FakePage(rows=[])
        did = R._attr_click_suggestion_once(
            page, "Alec Burleson", "playwright-click")
        self.assertFalse(did)
        self.assertTrue(any("result-box" in q for q in page.queries),
                        f"no .result-box selector queried: {page.queries}")

    def test_mouse_coords_clicks_row_centre(self):
        # Playwright element click fails (timeout) -> the coords
        # fallback performs a REAL mouse click at the row centre.
        row = _FakeRow("Alec Burleson", rect=self._RECT,
                       click_raises=True)
        page = _FakePage(rows=[row])
        did = R._attr_click_suggestion_once(
            page, "Alec Burleson", "mouse-coords")
        self.assertTrue(did)
        self.assertFalse(row.clicked)  # element click never used
        self.assertEqual(page.mouse.clicks, [(200, 220)])
        logs = self._click_logs()
        self.assertEqual(len(logs), 1)
        self.assertIn("clicked=True", logs[0])
        self.assertIn("method=mouse-coords", logs[0])

    def test_no_row_logs_clicked_false(self):
        page = _FakePage(rows=[])
        did = R._attr_click_suggestion_once(
            page, "Alec Burleson", "playwright-click")
        self.assertFalse(did)
        logs = self._click_logs()
        self.assertEqual(len(logs), 1)
        self.assertIn("clicked=False", logs[0])
        self.assertIn("no exact-match row", logs[0])


class TestSelectionStateSelectors(_PatchHelpersMixin, unittest.TestCase):
    def test_selection_probe_queries_result_box(self):
        # The vacuous-True hole: the old open-detection JS never saw
        # a .result-box row, so it reported open=False while the row
        # sat open and unselected.
        seen = {}

        def _ev(script, *args):
            seen["script"] = script
            return {"input_value": "Alec Burleson",
                    "suggestion_open": False}

        page = _FakePage(evaluate=_ev)
        st = R._attr_selection_state(page, "Alec Burleson")
        self.assertIs(st["selected"], True)
        self.assertIn("result-box", seen["script"])

    def test_mouse_events_js_queries_result_box(self):
        page = _FakePage(evaluate=lambda s, *a: False)
        did = R._attr_click_suggestion_once(
            page, "Alec Burleson", "mouse-events")
        self.assertFalse(did)
        self.assertTrue(any("result-box" in s
                            for s in page.evaluate_scripts))


class TestReselect(_PatchHelpersMixin, unittest.TestCase):
    _RECT = {"x": 100, "y": 200, "width": 200, "height": 40}

    def _selection_evaluate(self, page):
        def _ev(script, *args):
            if "MouseEvent" in script:
                return False
            return {"input_value": "Alec Burleson",
                    "suggestion_open": not page.selected_flag}
        return _ev

    def test_reselect_uses_real_click_and_verifies(self):
        row = _FakeRow("Alec Burleson", rect=self._RECT)
        page = _FakePage(rows=[row])
        page.selected_flag = True
        page._evaluate = self._selection_evaluate(page)
        ok, detail = R._attr_reselect(page, "Alec Burleson")
        self.assertTrue(ok)
        self.assertIn("playwright-click", detail)
        self.assertTrue(row.clicked)
        self.assertTrue(any("clicked=True" in m
                            for m in self._click_logs()))

    def test_keyboard_fallback_counts_only_when_selection_changes(self):
        # Keyboard is the last method: it counts ONLY if the
        # selection state actually flips (iter-25's vacuous-True).
        page = _FakePage(rows=[])
        page._evaluate = self._selection_evaluate(page)
        page._keyboard_selects = True
        ok, detail = R._attr_reselect(page, "Alec Burleson")
        self.assertTrue(ok)
        self.assertIn("selected via keyboard", detail)
        self.assertIn("ArrowDown", page.pressed)
        self.assertIn("Enter", page.pressed)

    def test_keyboard_fallback_rejected_when_selection_never_changes(self):
        page = _FakePage(rows=[])
        page._evaluate = self._selection_evaluate(page)
        page._keyboard_selects = False  # Enter changes nothing
        ok, detail = R._attr_reselect(page, "Alec Burleson")
        self.assertFalse(ok)
        self.assertIn("never landed", detail)


class TestSolveAttrGameGates(_PatchHelpersMixin, unittest.TestCase):
    _BOARD_PROBE = {"rows": ["Alec Burleson | ..."], "dropdown_open": False,
                    "populated": True, "populated_rows": 1,
                    "feedback_tiles": 3, "visible_exact_cells": 0}

    def setUp(self):
        super().setUp()
        R._PLAYED.pop("batterup", None)
        R._REJECTED.pop("batterup", None)
        self.addCleanup(lambda: (R._PLAYED.pop("batterup", None),
                                  R._REJECTED.pop("batterup", None)))

    def _base_patches(self, page, pick, selection, reselect, verify,
                      query_selector=None):
        if query_selector is not None:
            page.query_selector = query_selector
        return [
            mock.patch.object(R, "_close_modals"),
            mock.patch.object(R, "_idle_drift"),
            mock.patch.object(R, "_attr_pool", return_value=[]),
            mock.patch.object(R, "_attr_consent_recheck",
                              return_value=None),
            mock.patch.object(R, "_attr_dismiss_dropdown_soft"),
            mock.patch.object(R, "_type_country_guess", side_effect=pick),
            mock.patch.object(R, "_attr_selection_state",
                              side_effect=selection),
            mock.patch.object(R, "_attr_reselect", side_effect=reselect),
            mock.patch.object(R, "_settle"),
            mock.patch.object(R, "_attr_verify_submitted",
                              side_effect=verify),
            mock.patch.object(R, "_attr_post_submit_probe",
                              return_value=dict(self._BOARD_PROBE)),
            mock.patch.object(R, "_strict_win",
                              return_value=(True, "you won")),
        ]

    def _run(self, page, patches):
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return R.solve_attr_game(page, "Alec Burleson", "batterup")

    def test_pick_ok_requires_selected_state(self):
        # (a) Pick reports ok but no selection landed and re-select
        # fails -> the pick does NOT count; nothing is submitted.
        page = _FakePage()
        verify = mock.Mock()
        patches = self._base_patches(
            page,
            pick=lambda p1, p2, name: (True, name),
            selection=lambda p, name: {"selected": False,
                                       "suggestion_open": True,
                                       "input_value": name},
            reselect=lambda p, name: (False, "selection never landed"),
            verify=verify)
        solved, evidence = self._run(page, patches)
        self.assertFalse(solved)
        self.assertIn("pick failed", evidence)
        verify.assert_not_called()
        self.assertEqual(page.pressed, [])
        self.assertNotIn("Alec Burleson", R._PLAYED.get("batterup", []))

    def test_submit_never_fires_on_unverified_selection(self):
        # (b) Pick confirms, but by submit time the selection is gone
        # and re-select fails -> Enter is NEVER pressed, the submit
        # button is never clicked, verify is never called.
        page = _FakePage()
        verify = mock.Mock()
        selection_calls = [
            {"selected": True, "suggestion_open": False,
             "input_value": "Alec Burleson"},   # pick confirm
            {"selected": False, "suggestion_open": True,
             "input_value": "Alec Burleson"},   # ensure before submit
            {"selected": False, "suggestion_open": True,
             "input_value": "Alec Burleson"},   # re-pick confirm
        ]
        patches = self._base_patches(
            page,
            pick=lambda p1, p2, name: (True, name),
            selection=selection_calls,
            reselect=lambda p, name: (False, "selection never landed"),
            verify=verify)
        solved, _ev = self._run(page, patches)
        self.assertFalse(solved)
        verify.assert_not_called()
        self.assertEqual(page.pressed, [])
        self.assertTrue(any("NOT submitting" in m for m in self._msgs))
        self.assertNotIn("Alec Burleson", R._PLAYED.get("batterup", []))

    def test_submit_prefers_site_submit_control_over_enter(self):
        # (c) With a confirmed selection, the site's own submit
        # control is clicked; Enter is not pressed.
        page = _FakePage()
        button = _FakeButton()

        def _query(sel):
            return button if "player-search" in sel else None

        verify = mock.Mock(
            return_value=(True, dict(self._BOARD_PROBE)))
        patches = self._base_patches(
            page,
            pick=lambda p1, p2, name: (True, name),
            selection=lambda p, name: {"selected": True,
                                       "suggestion_open": False,
                                       "input_value": name},
            reselect=lambda p, name: (True, "selected via playwright-click"),
            verify=verify,
            query_selector=_query)
        solved, _ev = self._run(page, patches)
        self.assertTrue(solved)
        self.assertTrue(button.clicked)
        self.assertEqual(page.pressed, [])
        verify.assert_called_once()


class TestPitcherProbes(_PatchHelpersMixin, unittest.TestCase):
    def test_pitcher_probes_excluded_from_pool(self):
        pool = R._attr_pool("batterup")
        self.assertNotIn("Wade Miley", pool)
        self.assertNotIn("Jhoan Duran", pool)
        self.assertNotIn("Jesus Tinoco", pool)
        self.assertIn("Vladimir Guerrero Jr", pool)
        self.assertIn("Alec Burleson", pool)

    def test_no_pool_member_is_a_pitcher(self):
        players = {p["player_name"]: p for p in json.loads(
            (Path(R.__file__).parent / "frontend_data" / "src" / "lib" /
             "data" / "batterup-players.json").read_text(encoding="utf-8"))}
        pool = R._attr_pool("batterup")
        self.assertGreater(len(pool), 100)  # batters remain plentiful
        for name in pool:
            pos = {str(x).upper()
                   for x in players[name].get("position") or []}
            self.assertFalse(
                pos & R._BATTERUP_PITCHER_POSITIONS,
                f"pitcher {name} ({pos}) still in probe pool")

    def test_pitcher_api_probe_filtered_outright(self):
        # Miley passed in as an api_probe is dropped by the denylist
        # before play; he is never typed, never submitted.
        page = _FakePage()
        R._PLAYED.pop("batterup", None)
        R._REJECTED.pop("batterup", None)
        self.addCleanup(lambda: (R._PLAYED.pop("batterup", None),
                                  R._REJECTED.pop("batterup", None)))
        picked = []

        def _pick(p1, p2, name):
            picked.append(name)
            return True, name

        patches = [
            mock.patch.object(R, "_close_modals"),
            mock.patch.object(R, "_idle_drift"),
            mock.patch.object(R, "_attr_pool",
                              return_value=["Aaron Judge"]),
            mock.patch.object(R, "_attr_consent_recheck",
                              return_value=None),
            mock.patch.object(R, "_attr_dismiss_dropdown_soft"),
            mock.patch.object(R, "_type_country_guess",
                              side_effect=_pick),
            mock.patch.object(
                R, "_attr_selection_state",
                return_value={"selected": None, "suggestion_open": None,
                              "input_value": ""}),
            mock.patch.object(R, "_attr_reselect"),
            mock.patch.object(R, "_settle"),
            mock.patch.object(
                R, "_attr_verify_submitted",
                return_value=(True, {"populated_rows": 1,
                                     "dropdown_open": False,
                                     "feedback_tiles": 3,
                                     "failure_kind": "verified"})),
            mock.patch.object(
                R, "_attr_post_submit_probe",
                return_value={"rows": ["x"], "dropdown_open": False,
                              "populated": True, "populated_rows": 1,
                              "feedback_tiles": 3,
                              "visible_exact_cells": 0}),
            mock.patch.object(R, "_strict_win",
                              return_value=(True, "you won")),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        solved, _ev = R.solve_attr_game(
            page, "Alec Burleson", "batterup",
            api_probes=("Wade Miley", "Aaron Judge"))
        self.assertTrue(solved)
        self.assertNotIn("Wade Miley", picked)
        self.assertNotIn("Wade Miley", R._PLAYED.get("batterup", []))


if __name__ == "__main__":
    unittest.main()
