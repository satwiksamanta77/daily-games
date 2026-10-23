"""Fix-loop iteration 29 tests — Batterup-only (user override).

The iter-28 failure, frame- + log-proven: Batterup CORRECTLY
deferred for the 2nd consecutive run (served #972 10/17/26 vs
target 2026-10-18), because the BOT'S OWN fake clock served the
previous-day board: the generic 00:05-local fake instant, parsed
as Kiritimati-local (UTC+14), is 10:05 UTC on D-1 — the site's
America/New_York date is then D-1, so it serves game D-1.

Fixes (runner.py):
1. Per-game fake instant: GAMES["batterup"]["fake_iso_mode"] ==
   "et_noon" -> _fake_iso_for returns target-date 12:00 noon ET
   as an explicit-offset UTC ISO (16:00 UTC in EDT), whose ET
   date IS the target. Other games keep the generic instant;
   an explicit FAKE_DATE_ISO env value still wins. (Iter-50:
   except when the env value IS the generic pin itself — the
   workflows export exactly that on explicit-date dispatches,
   which silently bypassed this fix; see test_iter50.)
2. Batterup pick = click-submit (RE Q5): the site has NO
   'selected' state and NO Enter handler — clicking a suggestion
   <li> IS the submit. _batterup_click_submit types, real-pointer
   -clicks the matched <li> (name part before " ("), and verifies
   the RE triple (input '' + dropdown hidden + new populated
   row) after waiting out the 250ms colour-application delay.
   The old JS container click never fired the <li>'s onClick,
   and the old 'selected' check could never pass (a genuine
   submit clears the input) — iter-26: 3 rows submitted on
   screen, bot counted 0.
3. _s_batterup reloads the document before reading the header
   (a stale SPA page keeps the previous day's file) and logs
   pathname + page-load timestamp; /archive/<n> URLs go to root.
4. Control-name probe (RE Q2) before any real guess.
5. Pitcher hypothesis REFUTED by RE (filter is substring
   .includes(), no position filter; live players file contains
   Duran/Miley/Tinoco) — the _BATTERUP_NO_SUGGESTION_NAMES
   denylist is removed; the pool's pitcher exclusion stays as a
   probe-priority choice only, not an index fact.

All tests are synthetic/local: fake pages, monkeypatched
helpers — no browser, no network, no live play.

Run:  python3 -m unittest tests.test_iter29_batterup -v
"""

import datetime as _dt
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

import runner as R


# ------------------------------------------------------- fake page

class _StubInput:
    def __init__(self):
        self.clicked = 0

    def scroll_into_view_if_needed(self, timeout=3000):
        return None

    def click(self, timeout=3000):
        self.clicked += 1


class _StubMouse:
    def __init__(self):
        self.clicks = []

    def click(self, x, y):
        self.clicks.append((x, y))


class _StubKeyboard:
    def __init__(self):
        self.pressed = []

    def press(self, key):
        self.pressed.append(key)


class _StubPage:
    """Fake page dispatching evaluate() by script content."""

    def __init__(self, li_rows=(), open_lis=0, input_value=""):
        self.li_rows = list(li_rows)
        self.open_lis = open_lis
        self.input_value = input_value
        self.inp = _StubInput()
        self.mouse = _StubMouse()
        self.keyboard = _StubKeyboard()
        self.waits = []

    def query_selector(self, sel):
        return self.inp

    def wait_for_timeout(self, ms):
        self.waits.append(ms)

    def evaluate(self, script, *args):
        if "getBoundingClientRect" in script:
            return {"x": 500.0, "y": 300.0, "text": "Alec Burleson (2021-2025)"}
        if "#player-search" in script:
            return list(self.li_rows)
        if "h6.tile" in script:
            return ["solid"] * 6
        if "input-box" in script and "value" in script:
            return self.input_value
        if "#result-id li" in script:
            return self.open_lis
        return ""


class TestFakeIsoFor(unittest.TestCase):
    def test_batterup_et_noon_october_edt(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            if "FAKE_DATE_ISO" in os.environ:
                del os.environ["FAKE_DATE_ISO"]
            got = R._fake_iso_for("batterup", _dt.date(2026, 10, 18))
        # Noon EDT (UTC-4) == 16:00 UTC, explicit offset.
        self.assertEqual(got, "2026-10-18T16:00:00+00:00")

    def test_batterup_et_noon_january_est(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            if "FAKE_DATE_ISO" in os.environ:
                del os.environ["FAKE_DATE_ISO"]
            got = R._fake_iso_for("batterup", _dt.date(2026, 1, 15))
        # Noon EST (UTC-5) == 17:00 UTC — DST-safe via zoneinfo.
        self.assertEqual(got, "2026-01-15T17:00:00+00:00")

    def test_batterup_et_noon_instant_has_target_et_date(self):
        from zoneinfo import ZoneInfo
        with mock.patch.dict(os.environ, {}, clear=True):
            if "FAKE_DATE_ISO" in os.environ:
                del os.environ["FAKE_DATE_ISO"]
            got = R._fake_iso_for("batterup", _dt.date(2026, 10, 18))
        instant = _dt.datetime.fromisoformat(got)
        et_date = instant.astimezone(ZoneInfo("America/New_York")).date()
        self.assertEqual(et_date, _dt.date(2026, 10, 18))

    def test_env_override_wins_for_batterup(self):
        # Iter-50 narrowed this contract: an env value EQUAL to the
        # generic pin "<target>T00:05:00" is the workflows' own date
        # pin, not an operator override, and no longer wins for
        # et_noon games (run 40031686330). A GENUINE operator instant
        # still wins — pinned here with a non-generic value; the
        # generic-pin case lives in test_iter50.
        with mock.patch.dict(
                os.environ, {"FAKE_DATE_ISO": "2026-10-18T09:30:00"}):
            got = R._fake_iso_for("batterup", _dt.date(2026, 10, 18))
        self.assertEqual(got, "2026-10-18T09:30:00")

    def test_other_games_keep_generic_instant(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            if "FAKE_DATE_ISO" in os.environ:
                del os.environ["FAKE_DATE_ISO"]
            self.assertEqual(
                R._fake_iso_for("marveldle", _dt.date(2026, 10, 18)),
                "2026-10-18T00:05:00")
            self.assertEqual(
                R._fake_iso_for("worldle", _dt.date(2026, 10, 18)),
                "2026-10-18T00:05:00")

    def test_batterup_games_entry_flags_et_noon(self):
        self.assertEqual(
            R.GAMES["batterup"].get("fake_iso_mode"), "et_noon")
        self.assertNotIn("fake_iso_mode", R.GAMES["marveldle"])


class TestBatterupClickSubmit(unittest.TestCase):
    def setUp(self):
        self._dbg = mock.patch.object(R, "_dbg", lambda *a, **k: None)
        self._dbg.start()
        self._tlp = mock.patch.object(
            R, "_type_like_a_person", lambda *a, **k: None)
        self._tlp.start()
        self._ev = mock.patch.object(R.QP, "ev_push", lambda *a, **k: None)
        self._ev.start()

    def tearDown(self):
        self._ev.stop()
        self._tlp.stop()
        self._dbg.stop()

    def test_triple_held_clicks_matched_li_centre(self):
        page = _StubPage(
            li_rows=["Alec Burleson (2021-2025)",
                     "Vladimir Guerrero Jr (2019-2025)"],
            open_lis=0, input_value="")
        probe = {"populated_rows": 1, "row_details": [
            {"name": "Someone Else", "cells": [
                {"text": "27", "state": "other"}]}]}
        with mock.patch.object(
                R, "_attr_post_submit_probe", return_value=probe):
            ok, detail, pr = R._batterup_click_submit(
                page, "Alec Burleson", 0)
        self.assertTrue(ok, detail)
        self.assertIn("triple", detail)
        # Real pointer click at the matched <li>'s centre.
        self.assertEqual(page.mouse.clicks, [(500.0, 300.0)])
        self.assertEqual(pr.get("populated_rows"), 1)

    def test_duplicate_on_board_never_clicks(self):
        page = _StubPage(li_rows=["Alec Burleson (2021-2025)"])
        probe = {"populated_rows": 1, "row_details": [
            {"name": "Alec Burleson", "cells": []}]}
        with mock.patch.object(
                R, "_attr_post_submit_probe", return_value=probe):
            ok, detail, _ = R._batterup_click_submit(
                page, "Alec Burleson", 0)
        self.assertFalse(ok)
        self.assertIn("duplicate", detail)
        # The site silently ignores a re-click: no click attempted.
        self.assertEqual(page.mouse.clicks, [])
        self.assertEqual(page.inp.clicked, 0)

    def test_no_suggestions_reports_phrase_for_replacement(self):
        page = _StubPage(li_rows=[], open_lis=0, input_value="Tinoco")
        probe = {"populated_rows": 0, "row_details": []}
        with mock.patch.object(
                R, "_attr_post_submit_probe", return_value=probe):
            ok, detail, _ = R._batterup_click_submit(
                page, "Jesus Tinoco", 0)
        self.assertFalse(ok)
        # The replacement path in solve_attr_game keys on this phrase.
        self.assertIn("no suggestions", detail)
        self.assertEqual(page.mouse.clicks, [])


class TestPitcherHypothesisCorrection(unittest.TestCase):
    def test_denylist_constant_removed(self):
        self.assertFalse(hasattr(R, "_BATTERUP_NO_SUGGESTION_NAMES"))

    def test_pool_pitcher_exclusion_still_a_choice(self):
        # The pool still excludes pitchers (probe-priority choice),
        # but nothing in runner.py asserts the index excludes them.
        self.assertEqual(
            R._BATTERUP_PITCHER_POSITIONS, {"SP", "RP", "CP", "P", "CL"})


class TestBatterupControlProbe(unittest.TestCase):
    def test_control_probe_types_and_clears_without_submit(self):
        page = _StubPage(li_rows=[], open_lis=2, input_value="")
        with mock.patch.object(R, "_dbg", lambda *a, **k: None), \
                mock.patch.object(
                    R, "_type_like_a_person", lambda *a, **k: None):
            R._batterup_control_probe(page, "Alec Burleson")
        # Typed (input clicked) and cleared (Ctrl+A + Backspace).
        self.assertEqual(page.inp.clicked, 1)
        self.assertIn("ControlOrMeta+a", page.keyboard.pressed)
        self.assertIn("Backspace", page.keyboard.pressed)
        # No submit click: the control probe never clicks a row.
        self.assertEqual(page.mouse.clicks, [])


if __name__ == "__main__":
    unittest.main()
