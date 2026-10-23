"""Fix-loop iteration 50 tests — Batterup explicit-date fake clock.

Proven defect (run 40031686330, a DATE-TARGETED production recovery
daily-all-videos {game: batterup, date: 2026-10-23, publish: true}):
the CDN answer for 2026-10-23 was ready (#978 'Maikel Garcia'), but
the site served #977 (10/22/26) and the run ended "no win" in ~3s.

Root cause: the batch workflows export
FAKE_DATE_ISO="<inputs.date>T00:05:00" whenever a date input is
given, and runner._fake_iso_for returned that env value BEFORE the
per-game fake_iso_mode branch — so Batterup's "et_noon" conversion
never ran in explicit-date runs. 00:05 naive is still D-1 in
America/New_York; the site keyed its board off ET and served D-1.
The dawn/auto path (no date input, env unset) computed ET noon
correctly and solved #978 the same morning.

Fix (runner._fake_iso_for): when the env value IS this function's
own generic pin for the target date and the game is an et_noon
game, fall through to the per-game computation — the exact ET-noon
instant the dawn path produces. Any other env value remains a
genuine operator override and still wins, for every game; every
other game's pin behavior is byte-identical.

Run:  python3 -m unittest tests.test_iter50_batterup_explicit_date -v
"""

import datetime as _dt
import os
import sys
import unittest
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))

import runner as R

_ET = ZoneInfo("America/New_York")
_GENERIC_PIN = "2026-10-23T00:05:00"   # exactly what the workflow exports


def _et_date_of(iso):
    """ET calendar date of the instant _fake_iso_for returned."""
    if iso.endswith("+00:00"):
        return _dt.datetime.fromisoformat(iso).astimezone(_ET).date()
    # naive local instant in the Kiritimati context: its UTC instant is
    # 10:05 on D-1, whose ET date is D-1 — the defect being pinned.
    naive = _dt.datetime.fromisoformat(iso)
    return (naive - _dt.timedelta(hours=14)).astimezone(_ET).date()


class TestExplicitDateEtNoon(unittest.TestCase):
    def test_generic_pin_batterup_gets_et_noon_edt(self):
        with mock.patch.dict(os.environ,
                             {"FAKE_DATE_ISO": _GENERIC_PIN}):
            got = R._fake_iso_for("batterup", _dt.date(2026, 10, 23))
        self.assertEqual(got, "2026-10-23T16:00:00+00:00")
        self.assertEqual(_et_date_of(got), _dt.date(2026, 10, 23))

    def test_generic_pin_batterup_gets_et_noon_est(self):
        with mock.patch.dict(os.environ,
                             {"FAKE_DATE_ISO": "2026-01-15T00:05:00"}):
            got = R._fake_iso_for("batterup", _dt.date(2026, 1, 15))
        self.assertEqual(got, "2026-01-15T17:00:00+00:00")
        self.assertEqual(_et_date_of(got), _dt.date(2026, 1, 15))


class TestUnchangedBehaviorGuards(unittest.TestCase):
    def test_generic_pin_other_game_unchanged(self):
        with mock.patch.dict(os.environ,
                             {"FAKE_DATE_ISO": _GENERIC_PIN}):
            got = R._fake_iso_for("marveldle", _dt.date(2026, 10, 23))
        self.assertEqual(got, _GENERIC_PIN)

    def test_genuine_operator_override_still_wins_for_batterup(self):
        with mock.patch.dict(os.environ,
                             {"FAKE_DATE_ISO": "2026-10-23T09:30:00"}):
            got = R._fake_iso_for("batterup", _dt.date(2026, 10, 23))
        self.assertEqual(got, "2026-10-23T09:30:00")

    def test_no_env_batterup_et_noon_unchanged(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            os.environ.pop("FAKE_DATE_ISO", None)
            got = R._fake_iso_for("batterup", _dt.date(2026, 10, 23))
        self.assertEqual(got, "2026-10-23T16:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
