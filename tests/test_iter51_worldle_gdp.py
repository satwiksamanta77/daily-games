"""Fix-loop iteration 51 tests — WORLDLE GDP comparison (evidence:
ANALYSIS51, runs 40010882824 + 40031682553, target 2026-10-24,
Solomon Islands).

Defect: the GDP mode solved the number (bucket '$1K - $2.5K' for
2205.25, win_check number=True) in BOTH runs but the comparison
never completed (comparison=False), so the mode stayed partial and
the all-modes gate held worldle at 12/13 with no video.

Root cause: the game's GDP comparison renders each option button
as "Name (YYYY)" — the year is that country's GDP-data year. The
live probe in the recovery run read exactly:
  ['New Caledonia (2019)', 'Papua New Guinea (2025)',
   'Solomon Islands (2024)', 'Vanuatu (2023)']
Every name-resolution path keyed on the raw suffixed name:
- _s_worldle_cmp_key did not strip the suffix, so the deliberate
  branch missed every entry of the day gdp_lookup (plain names);
- _s_worldle_fetch_country_stat matched the suffixed names
  against the CDN countries list and resolved nothing
  (log: values={} -> "comparison options unresolvable; NOT
  clicked");
- the visible-options fallback in _s_worldle_play_gdp tested the
  suffixed names against the lookup, found none "shown", and fell
  to the day prediction 'Australia' — the max over ALL candidates,
  which the game does not even show — "not found" -> partial.

Fix: _s_worldle_cmp_key strips a trailing parenthesized 4-digit
year (display chrome, never part of a country name; non-year
parentheses are kept), _s_worldle_fetch_country_stat cleans the
names it sends to the CDN matcher, and the play_gdp fallback
shares the same key. The pick still returns the ORIGINAL display
name, so clicks target the real button text.

Shown-set truth (the game's own data, answers.py day build for
2026-10-24): New Caledonia 34939 > Vanuatu 3188 > Papua New
Guinea 2560 > Solomon Islands 2205.25 — the correct pick among
the shown options is 'New Caledonia (2019)'.

All tests are synthetic/local: no network, no browser, no CI run.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import runner as R

# The exact shown options from the run-40031682553 job log probe
# (names + rects as rendered by the game).
_SHOWN = [
    {"name": "New Caledonia (2019)", "x": 834.5, "y": 300.046875,
     "has_img": False, "rect": {"x": 715, "y": 276, "w": 239, "h": 49},
     "text": "New Caledonia (2019)", "aria": "", "alt": "",
     "title": "", "src": ""},
    {"name": "Papua New Guinea (2025)", "x": 1085.5, "y": 300.046875,
     "has_img": False, "rect": {"x": 966, "y": 276, "w": 239, "h": 49},
     "text": "Papua New Guinea (2025)", "aria": "", "alt": "",
     "title": "", "src": ""},
    {"name": "Solomon Islands (2024)", "x": 834.5, "y": 361.046875,
     "has_img": False, "rect": {"x": 715, "y": 337, "w": 239, "h": 49},
     "text": "Solomon Islands (2024)", "aria": "", "alt": "",
     "title": "", "src": ""},
    {"name": "Vanuatu (2023)", "x": 1085.5, "y": 361.046875,
     "has_img": False, "rect": {"x": 966, "y": 337, "w": 239, "h": 49},
     "text": "Vanuatu (2023)", "aria": "", "alt": "",
     "title": "", "src": ""},
]
# answers.py day build for 2026-10-24 (plain-name keys, game data).
_LOOKUP = {"solomon islands": 2205.25, "vanuatu": 3188,
           "new caledonia": 34939, "papua new guinea": 2560,
           "australia": 75648, "federated states of micronesia": 5291}
_DS = "2026-10-24"


def _dbg_recorder(logs):
    def _rec(gid, msg):
        logs.append(f"[{gid}] {msg}")
    return _rec


class _Patch:
    """Minimal monkeypatch context for module-level runner hooks."""

    def __init__(self, **kw):
        self.kw = kw
        self.orig = {}

    def __enter__(self):
        for k, v in self.kw.items():
            self.orig[k] = getattr(R, k)
            setattr(R, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self.orig.items():
            setattr(R, k, v)
        return False


# ------------------------------------------------------------- cmp key

class TestCmpKeyYearSuffixIter51(unittest.TestCase):
    def test_strips_trailing_data_year(self):
        self.assertEqual(R._s_worldle_cmp_key("New Caledonia (2019)"),
                         "new caledonia")
        self.assertEqual(R._s_worldle_cmp_key("Solomon Islands (2024)"),
                         "solomon islands")

    def test_plain_names_unchanged(self):
        self.assertEqual(R._s_worldle_cmp_key("Papua New Guinea"),
                         "papua new guinea")
        self.assertEqual(R._s_worldle_cmp_key("  Vanuatu "),
                         "vanuatu")

    def test_non_year_parentheses_are_part_of_the_name(self):
        # Only a bare 4-digit year is display chrome; a real name
        # qualifier in parentheses must survive.
        self.assertEqual(R._s_worldle_cmp_key("Congo (Brazzaville)"),
                         "congo (brazzaville)")

    def test_year_not_at_end_is_kept(self):
        self.assertEqual(R._s_worldle_cmp_key("Foo (2019) Bar"),
                         "foo (2019) bar")


# ---------------------------------------------------------------- pick

class TestPickYearSuffixedOptionsIter51(unittest.TestCase):
    def test_pick_resolves_suffixed_options_against_plain_lookup(self):
        best, detail = R._s_worldle_comparison_pick(
            [dict(o) for o in _SHOWN], dict(_LOOKUP))
        self.assertEqual(best, "New Caledonia (2019)")
        self.assertIn("max=34939", detail)


# ------------------------------------------------------- CDN fallback

class _FakeCDNPage:
    """Mimics the production fetch JS: it lowercases/collapses the
    names it is GIVEN and matches them against the CDN countries
    list's plain names — a suffixed name matches nothing, exactly
    as in run 40031682553 (values={})."""

    _COUNTRIES = {"new caledonia": 34939, "papua new guinea": 2560,
                  "solomon islands": 2205.25, "vanuatu": 3188}

    def __init__(self):
        self.seen_names = None

    def evaluate(self, js, arg=None):
        assert "worldle-country-stat-fetch" in js
        self.seen_names = list((arg or {}).get("names") or [])
        out = []
        for n in self.seen_names:
            k = " ".join(str(n).lower().split())
            if k in self._COUNTRIES:
                out.append([n, self._COUNTRIES[k]])
        return out


class TestFetchCountryStatIter51(unittest.TestCase):
    def test_suffixed_names_are_cleaned_before_the_cdn_match(self):
        page = _FakeCDNPage()
        got = R._s_worldle_fetch_country_stat(
            page, ["New Caledonia (2019)", "Vanuatu (2023)"],
            "gdpPerCapita")
        self.assertEqual(got, {"new caledonia": 34939,
                               "vanuatu": 3188})


# -------------------------------------------- deliberate branch (e2e)

class _FakeGDPComparisonPage:
    """Fake page for _s_worldle_play_comparison(mode='gdp') with
    the Oct-24 probe output. The CDN fetch JS is mimicked
    faithfully: suffixed names resolve nothing."""

    def __init__(self):
        self.calls = []
        self.clicked = []

    def evaluate(self, js, arg=None):
        if "worldle-comparison-probe" in js:
            return {"prompt_found": True,
                    "options": [dict(o) for o in _SHOWN]}
        if "worldle-country-stat-fetch" in js:
            # Production behaviour: names are matched as given
            # (lowercase/collapse only) against plain CDN names.
            out = []
            cdn = {"new caledonia": 34939, "papua new guinea": 2560,
                   "solomon islands": 2205.25, "vanuatu": 3188}
            for n in (arg or {}).get("names") or []:
                k = " ".join(str(n).lower().split())
                if k in cdn:
                    out.append([n, cdn[k]])
            return out
        if "worldle-comparison-card-click" in js:
            self.calls.append("card-click")
            return [False, "no card", "card", "", "", None, None, None]
        if "worldle-comparison-click" in js:
            self.calls.append("button-click")
            want = (arg or {}).get("want") if isinstance(arg, dict) \
                else arg
            self.clicked.append(want)
            return [True, want, "button", "BUTTON", "btn",
                    {"x": 715, "y": 276, "w": 239, "h": 49},
                    834.5, 300.0]
        if "worldle-comparison-selected-state" in js:
            # GDP buttons show no probe-visible selection; the code
            # submits anyway and lets the store arbitrate.
            return {"any_selected": False, "selected": [],
                    "selected_name": "", "wanted_selected": False,
                    "options": [{"name": o["name"]} for o in _SHOWN]}
        if "localStorage.getItem" in js:
            if arg == "game_round":
                return {"gameMode": "gdp"}
            return {_DS: []}
        if "document.body.innerText" in js:
            return ("Among Solomon Islands and its neighbours, "
                    "which has the highest per capita GDP?")
        return True

    def wait_for_timeout(self, ms):
        return None


class TestPlayComparisonGDPYearSuffixIter51(unittest.TestCase):
    def test_shown_options_resolve_and_max_is_clicked(self):
        page = _FakeGDPComparisonPage()
        logs, submits = [], []
        with _Patch(_dbg=_dbg_recorder(logs),
                    _s_worldle_submit_guarded=(
                        lambda *a, **k: submits.append(1) or "ok"),
                    _settle=lambda page, base=0: None):
            st, pick, det = R._s_worldle_play_comparison(
                page, "worldle",
                {"date": _DS, "gdp": {"number": 2205.25}},
                "gdp", _DS, ("highest", "per capita gdp"),
                dict(_LOOKUP), "gdpPerCapita", "gdp")
        self.assertEqual(st, "attempted")
        self.assertEqual(pick, "New Caledonia (2019)")
        self.assertEqual(page.clicked, ["New Caledonia (2019)"])
        self.assertEqual(len(submits), 1)


# --------------------------------------- play_gdp visible fallback

class _FakeGDPBucketPage:
    """Only what _s_worldle_play_gdp touches before the fallback:
    the bucket JS and the COMPARISON-tab JS."""

    def evaluate(self, js, arg=None):
        if "startsWith('<')" in js:
            return "$1K - $2.5K"
        if "COMPARISON" in js:
            return True
        return True

    def wait_for_timeout(self, ms):
        return None


class TestPlayGDPVisibleFallbackIter51(unittest.TestCase):
    def test_fallback_uses_shown_max_not_unshown_prediction(self):
        # Deliberate branch reports the prompt not present (the
        # pre-fix cascade shape): the visible-options fallback must
        # still find the shown options (suffixed names), resolve
        # them, and click the shown max — NOT the day prediction
        # 'Australia', which the game does not show.
        clicked = []
        submits = []

        def _click_text(page, name):
            clicked.append(name)
            return (name in [o["name"] for o in _SHOWN],
                    "ok" if name in [o["name"] for o in _SHOWN]
                    else "not found")

        day = {"date": _DS,
               "gdp": {"number": 2205.25,
                       "compare_answer": "Australia"},
               "gdp_lookup": dict(_LOOKUP)}
        with _Patch(
                _dbg=_dbg_recorder([]),
                _s_worldle_submit_guarded=(
                    lambda *a, **k: submits.append(1) or "ok"),
                _settle=lambda page, base=0: None,
                _s_worldle_play_comparison=(
                    lambda *a, **k: ("not-present", None,
                                     "comparison prompt not showing")),
                _s_worldle_visible_options=(
                    lambda page: [o["name"] for o in _SHOWN]),
                _s_worldle_click_option_text=_click_text,
                _s_worldle_click_option_loose=(
                    lambda page, name: (False, "not found"))):
            st, guesses, det = R._s_worldle_play_gdp(
                _FakeGDPBucketPage(), "worldle", day)
        self.assertEqual(st, "attempted")
        self.assertEqual(guesses, ["2205.25", "New Caledonia (2019)"])
        self.assertEqual(clicked, ["New Caledonia (2019)"])
        self.assertEqual(len(submits), 2)  # number + comparison


if __name__ == "__main__":
    unittest.main()
