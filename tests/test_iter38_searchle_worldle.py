"""Fix-loop iteration 38 tests — Searchle rejection recovery + Worldle
4-mode regressions (run 39279697490, target 2026-10-20, Saint Lucia).

SEARCHLE (DEFECT 1 — NOT the win detector): the bot typed MOCKTAIL,
the game REJECTED it (not in its valid-word list `ec`: jiggle +
"Word not found", guess not submitted, row stays stuck), and the bot
typed ACCURATE + DOLPHINS into the still-full row, so nothing ever
submitted. Frames prove: row 1 stuck on uncoloured MOCKTAIL, rows
2-6 empty, all 32 frames identical. The result.json step pattern
GGGGGGGG was planner-computed, NOT page-observed. Fixes:
(a) bank filtered against the game's valid list (MOCKTAIL + 72 more
excluded); (b) the play loop verifies each guess landed on the board
and recovers from a rejection (clear row, record rejected, play next).

WORLDLE (DEFECT 2 — Saint Lucia 9/13):
- CAPITAL: NE 110m GeoJSON has NO feature for small island nations
  (no LC/VC/BB/...), so bounds was None and the pin skipped with
  'projection NOT determinable'. Fix: 50m fallback (242 features,
  includes LC).
- SPOKENLANGUAGES: Intl.DisplayNames.of('acf') returns 'acf' (ICU has
  no name) and the fallback table had no 'acf' -> whole mode skipped
  although both options were on screen. Fix: the game's own
  languages.json names.en (exact button text).
- POPULATIONCURRENCY: the picker clicked '1+ million' for pop
  184,100 (correct: '50 - 500 thousand'). Two bugs: the /million/
  filter skipped thousand-only buckets, and the open-ended branch
  parsed only the pre-'+' part (parse('1') = 1, unit lost).
- POPULOUSCITIES: slots [] — the bucket regex matched M only, but
  small-city boards label buckets in K ('1. 20K' ... '4. 5K').

JS probes are tested by running the EXACT runtime JS (captured from
the functions via a recording FakePage) under node with stub DOMs —
not a Python mirror, not source text (iter-35 pattern).
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

import answers as A
import runner as R
import solve_lib as S


class _CapturePage:
    """Records every page.evaluate JS string; returns None/{ }."""

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
    """Run runtime JS under node with a stub DOM prelude; return the
    parsed JSON printed by `call`."""
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


# ---------------------------------------------------------------- Searchle bank

class TestSearchleBankExcludesGameRejectedIter38(unittest.TestCase):
    def test_mocktail_and_friends_excluded(self):
        bank = S.words_searchle()
        all_words = set()
        for words in bank.values():
            all_words.update(w.upper() for w in words)
        for w in ("MOCKTAIL", "BLOGGING", "FACEBOOK", "SNAPCHAT",
                  "WHATSAPP", "TWERKING", "BOTOX", "YOUTUBER",
                  "USERNAME", "TIKTOK", "WARIO", "POTATOS", "FAMILYS",
                  "BOREDS", "EXPATS", "SPAMMER", "SPOTIFY"):
            self.assertNotIn(w, all_words,
                             f"{w} is rejected by the game (not in ec) "
                             f"and must not be a probe candidate")

    def test_exclusion_set_matches_bank_filter(self):
        # Every word in the exclusion set must actually be gone from
        # the bank (the filter is the enforcement, not the comment).
        bank = S.words_searchle()
        all_words = set()
        for words in bank.values():
            all_words.update(w.upper() for w in words)
        self.assertFalse(all_words & set(S._SEARCHLE_NOT_IN_GAME_DICT))

    def test_bank_still_usable_for_every_length(self):
        bank = S.words_searchle()
        self.assertIn(8, bank)
        self.assertGreater(len(bank[8]), 50)
        for n, words in bank.items():
            self.assertTrue(words, f"empty bank for length {n}")

    def test_plan_still_ends_on_answer(self):
        with mock.patch.object(A, "target_date") as _td:
            import datetime as _dt
            _td.return_value = _dt.date(2026, 10, 20)
            R._PLANS.pop("searchle", None)
            guesses, _steps = R._plan_for("searchle", "DOLPHINS")
        self.assertEqual(guesses[-1], "DOLPHINS")
        self.assertNotIn("MOCKTAIL", guesses)


# ------------------------------------------------------- Searchle board probe

_SEARCHLE_GRID_PRELUDE = """
function mkCell(letter, cls) {
  return { textContent: letter,
    parentElement: { className: cls } };
}
function mkGrid(rows) {
  // rows: [[letters, scored], ...] over 6 rows x 8 cols.
  const cells = [];
  for (const [letters, scored] of rows) {
    for (let i = 0; i < 8; i++) {
      const letter = letters[i] || '';
      const cls = scored
        ? 'correct shadowed bg-green-500 text-white'
        : (letter ? 'bg-white border-slate-200' : 'bg-white border-slate-200');
      cells.push(mkCell(letter, cls));
    }
  }
  return cells;
}
const CELLS = mkGrid(%s);
global.document = { querySelectorAll: (sel) => CELLS };
"""


class TestSearchleBoardProbeIter38(unittest.TestCase):
    def _rows(self, rows):
        js_calls = _capture_js(R._searchle_board_rows, 8)
        assert js_calls, "no JS captured from board probe"
        prelude = _SEARCHLE_GRID_PRELUDE % json.dumps(rows)
        return _run_node(js_calls[0], prelude, "fn(8)")

    def test_scored_row_read(self):
        rows = [["MOCKTAIL", True], ["", False], ["", False],
                ["", False], ["", False], ["", False]]
        got = self._rows(rows)
        self.assertEqual(got[0], {"letters": "MOCKTAIL", "scored": True})

    def test_unscored_row_read(self):
        rows = [["MOCKTAIL", False], ["", False], ["", False],
                ["", False], ["", False], ["", False]]
        got = self._rows(rows)
        self.assertEqual(got[0], {"letters": "MOCKTAIL", "scored": False})

    def test_mixed_rows(self):
        rows = [["ACCURATE", True], ["DOLPHINS", False],
                ["", False], ["", False], ["", False], ["", False]]
        got = self._rows(rows)
        self.assertEqual(got[0]["scored"], True)
        self.assertEqual(got[1], {"letters": "DOLPHINS",
                                  "scored": False})


class TestSearchleGuessAcceptedIter38(unittest.TestCase):
    """_searchle_guess_accepted polls the board probe: scored -> True,
    stuck unscored after polls -> False, no rows -> None."""

    class _ProbePage:
        def __init__(self, script):
            self.script = list(script)
            self.waits = 0

        def evaluate(self, js, arg=None):
            if not self.script:
                return []
            return self.script.pop(0)

        def wait_for_timeout(self, ms):
            self.waits += 1

    def _rows(self, letters, scored):
        base = [{"letters": "", "scored": False} for _ in range(6)]
        base[0] = {"letters": letters, "scored": scored}
        return base

    def test_scored_first_poll_accepts(self):
        page = self._ProbePage([self._rows("MOCKTAIL", True)])
        self.assertTrue(R._searchle_guess_accepted(page, "MOCKTAIL", 8))
        self.assertEqual(page.waits, 0)

    def test_stuck_row_rejected_after_polls(self):
        stuck = [self._rows("MOCKTAIL", False)] * 3
        page = self._ProbePage(stuck)
        self.assertFalse(R._searchle_guess_accepted(page, "MOCKTAIL", 8))
        self.assertEqual(page.waits, 2)

    def test_reveal_then_scored_accepts(self):
        # First poll mid-reveal (unscored), second poll scored.
        page = self._ProbePage([self._rows("MOCKTAIL", False),
                                self._rows("MOCKTAIL", True)])
        self.assertTrue(R._searchle_guess_accepted(page, "MOCKTAIL", 8))
        self.assertEqual(page.waits, 1)

    def test_never_landed_is_false_at_once(self):
        # Board shows a DIFFERENT stuck word: this guess never landed.
        page = self._ProbePage([self._rows("MOCKTAIL", False)])
        self.assertFalse(R._searchle_guess_accepted(page, "ACCURATE", 8))
        self.assertEqual(page.waits, 0)

    def test_no_rows_is_none(self):
        page = self._ProbePage([[]])
        self.assertIsNone(R._searchle_guess_accepted(page, "MOCKTAIL", 8))


class TestSearchlePlayLoopRecoveryIter38(unittest.TestCase):
    """Full _s_searchle loop with a scripted board: MOCKTAIL stuck ->
    cleared -> ACCURATE accepted -> DOLPHINS accepted -> win.
    _PLAYED holds only accepted guesses; _REJECTED holds MOCKTAIL."""

    class _Handle:
        def __init__(self):
            self.clicks = []

        def evaluate(self, js):
            if "Object.keys" in js:
                return 30
            self.clicks.append(js)
            return True

    class _FakePage:
        def __init__(self):
            self.calls = 0
            self.handle = TestSearchlePlayLoopRecoveryIter38._Handle()

        def evaluate(self, js, arg=None):
            if "letter-container" in js:
                self.calls += 1
                # Calls 1-4: MOCKTAIL stuck (debug probe + 3 polls).
                # Call 5-6: ACCURATE (debug unscored, poll scored).
                # Call 7-8: DOLPHINS (debug unscored, poll scored).
                if self.calls <= 4:
                    rows = [{"letters": "MOCKTAIL", "scored": False}]
                elif self.calls <= 6:
                    rows = [{"letters": "ACCURATE", "scored": True}]
                else:
                    rows = [{"letters": "DOLPHINS", "scored": True}]
                rows += [{"letters": "", "scored": False}] * 5
                return rows
            if "flt-semantics" in js:
                return ""
            return "You won!"

        def evaluate_handle(self, js):
            return self.handle

        def wait_for_timeout(self, ms):
            return None

    def test_recovery_loop(self):
        page = self._FakePage()
        with mock.patch.object(R, "_close_modals", lambda p: None), \
                mock.patch.object(R, "_dismiss_login_wall",
                                  lambda p: None), \
                mock.patch.object(R, "_idle_drift", lambda p, **k: None), \
                mock.patch.object(R, "_kb", lambda p, t, **k: True), \
                mock.patch.object(R, "_settle", lambda p, **k: None), \
                mock.patch.object(R, "_enable_flutter_semantics",
                                  lambda p: None), \
                mock.patch.object(R, "_guesses",
                                  return_value=["MOCKTAIL", "ACCURATE",
                                                "DOLPHINS"]), \
                mock.patch.object(R, "_searchle_clear_row",
                                  wraps=R._searchle_clear_row) as _clr:
            R._PLAYED.pop("searchle", None)
            R._REJECTED.pop("searchle", None)
            try:
                ok, ev = R._s_searchle(page, "DOLPHINS", "searchle")
            finally:
                played = R._PLAYED.pop("searchle", None)
                rejected = R._REJECTED.pop("searchle", None)
        self.assertTrue(ok, f"win check failed: {ev}")
        self.assertEqual(played, ["ACCURATE", "DOLPHINS"])
        self.assertEqual(rejected, ["MOCKTAIL"])
        # The stuck row was cleared (8 Delete clicks) before guess 2.
        self.assertEqual(len(page.handle.clicks), 8)


# ------------------------------------------------------------------- Capital bounds

class TestWorldleCountryBoundsIter38(unittest.TestCase):
    def _doc(self, features):
        return {"type": "FeatureCollection", "features": features}

    def _feat(self, code, lon_min, lon_max, lat_min, lat_max):
        return {"properties": {"ISO_A2": code},
                "geometry": {"type": "Polygon",
                             "coordinates": [[[lon_min, lat_min],
                                              [lon_max, lat_min],
                                              [lon_max, lat_max],
                                              [lon_min, lat_max],
                                              [lon_min, lat_min]]]}}

    def test_small_island_uses_50m_when_110m_lacks_it(self):
        # 110m doc: 177 features, no LC. 50m doc: has LC.
        calls = []

        def _get(url, required=True):
            calls.append(url)
            if "110m" in url:
                return self._doc([self._feat("CA", -140, -50, 40, 70)])
            if "50m" in url:
                return self._doc([self._feat(
                    "LC", -61.073145, -60.886768,
                    13.717578, 14.093359)])
            return None

        got = A._worldle_country_bounds("LC", _get)
        self.assertIsNotNone(got)
        self.assertAlmostEqual(got["minLon"], -61.073145)
        self.assertAlmostEqual(got["maxLon"], -60.886768)
        self.assertAlmostEqual(got["minLat"], 13.717578)
        self.assertAlmostEqual(got["maxLat"], 14.093359)
        self.assertTrue(any("50m" in u for u in calls),
                        "50m fallback must be attempted for LC")

    def test_big_country_uses_110m_without_50m_fetch(self):
        calls = []

        def _get(url, required=True):
            calls.append(url)
            if "110m" in url:
                return self._doc([self._feat("CA", -140, -50, 40, 70)])
            return self._doc([])

        got = A._worldle_country_bounds("CA", _get)
        self.assertEqual(got, {"minLon": -140, "maxLon": -50,
                               "minLat": 40, "maxLat": 70})
        self.assertFalse(any("50m" in u for u in calls),
                         "50m must not be fetched when 110m has the code")

    def test_all_fetch_failures_give_none(self):
        got = A._worldle_country_bounds("LC", lambda u, required=True: None)
        self.assertIsNone(got)

    def test_bounds_used_by_capital_fit(self):
        # The 50m LC bounds map Castries (14.0, -60.99) inside the
        # Saint Lucia outline bbox (NW area), not outside it.
        bounds = {"minLon": -61.073145, "maxLon": -60.886768,
                  "minLat": 13.717578, "maxLat": 14.093359}
        path = {"x": 735.0, "y": 220.5, "w": 450.0, "h": 450.0}
        px = R._worldle_capital_fit_px(path, bounds, 14.0, -60.99)
        self.assertIsNotNone(px)
        # Castries is on the NW coast: left half, upper third.
        self.assertLess(px[0], 735.0 + 225.0)
        self.assertLess(px[1], 220.5 + 150.0)


# ------------------------------------------------------------- Language names

class TestWorldleLanguageNamesIter38(unittest.TestCase):
    DOC = [{"languageCode": "acf",
            "names": {"en": "Saint Lucian Creole"},
            "phrases": {"myNameIs": "Mwen sé"}},
           {"languageCode": "eng",
            "names": {"en": "English"},
            "phrases": {"myNameIs": "My name is"}},
           {"languageCode": "kbp",
            "names": {"en": "Kabiye"},
            "phrases": {"myNameIs": "Éwá mí kɛ́"}}]

    def test_game_names_preferred_over_icu(self):
        got = A._worldle_language_names_map(self.DOC, ["acf", "eng"])
        self.assertEqual(got, {"acf": "Saint Lucian Creole",
                               "eng": "English"})

    def test_missing_code_omitted(self):
        got = A._worldle_language_names_map(self.DOC, ["acf", "zzz"])
        self.assertEqual(got, {"acf": "Saint Lucian Creole"})

    def test_empty_names_en_omitted(self):
        doc = [{"languageCode": "acf", "names": {},
                "phrases": {"myNameIs": "x"}}]
        self.assertEqual(A._worldle_language_names_map(doc, ["acf"]), {})

    def test_play_language_prefers_game_names(self):
        # The solver must use day["language_names"] and never call
        # Intl for codes the game already names. A page whose
        # evaluate raises proves Intl was not consulted.
        class _NoIntlPage:
            def evaluate(self, js, arg=None):
                raise AssertionError("Intl must not be consulted")

        day = {"date": "2026-10-20",
               "language_names": {"acf": "Saint Lucian Creole",
                                  "eng": "English"}}
        # Only the name-resolution head of _s_worldle_play_language is
        # exercised here: with a page that cannot click, the function
        # must get PAST the name gate (it will fail later on clicking,
        # which proves the gate passed).
        with mock.patch.object(
                R, "_s_worldle_visible_options", return_value=[]):
            status, _played, detail = R._s_worldle_play_language(
                _NoIntlPage(), "worldle", day)
        self.assertNotIn("not resolvable", detail,
                         "game names must satisfy the name gate")


# ------------------------------------------------------------ Population bucket

_POP_PRELUDE = """
const clicked = [];
const BUTTONS = %s;
const buttons = BUTTONS.map(t => ({innerText: t, offsetParent: {},
  closest: () => null, click: function() { clicked.push(t); }}));
global.document = { querySelectorAll: (sel) => buttons };
"""


class TestPopulationBucketIter38(unittest.TestCase):
    SAINT_LUCIA = ["< 50 thousand", "50 - 500 thousand",
                   "500k - 1 million", "1+ million"]
    CANADA = ["< 15 million", "15 - 25 million", "25 - 35 million",
              "35+ million"]
    KUWAIT = ["< 3 million", "3 - 5 million", "5 - 8 million",
              "8+ million"]

    def _run(self, buttons, pop):
        js_calls = _capture_js(R._s_worldle_population_bucket, pop)
        assert js_calls, "no JS captured"
        prelude = _POP_PRELUDE % json.dumps(buttons)
        return _run_node(js_calls[0], prelude,
                         "({out: fn(%s), clicked: clicked})" % pop)

    def test_saint_lucia_correct_bucket(self):
        # Run 39279697490: 184,100 must click '50 - 500 thousand',
        # NOT '1+ million'.
        res = self._run(self.SAINT_LUCIA, 184100)
        self.assertEqual(res["out"], "50 - 500 thousand")
        self.assertEqual(res["clicked"], ["50 - 500 thousand"])

    def test_saint_lucia_boundaries(self):
        cases = [(49999, "< 50 thousand"),
                 (50000, "50 - 500 thousand"),
                 (499999, "50 - 500 thousand"),
                 (500000, "500k - 1 million"),
                 (999999, "500k - 1 million"),
                 (1000000, "1+ million"),
                 (5000000, "1+ million")]
        for pop, want in cases:
            with self.subTest(pop=pop):
                res = self._run(self.SAINT_LUCIA, pop)
                self.assertEqual(res["out"], want)

    def test_open_ended_unit_not_lost(self):
        # The iter-38 bug: parse('1') = 1 matched ANY pop >= 1.
        # With ONLY '1+ million' shown, pop 100 must match nothing.
        res = self._run(["1+ million"], 100)
        self.assertIsNone(res["out"])
        self.assertEqual(res["clicked"], [])

    def test_canada_still_works(self):
        cases = [(41798407, "35+ million"),
                 (35000000, "35+ million"),
                 (34999999, "25 - 35 million"),
                 (25000000, "25 - 35 million"),
                 (15000000, "15 - 25 million"),
                 (14999999, "< 15 million"),
                 (20000000, "15 - 25 million")]
        for pop, want in cases:
            with self.subTest(pop=pop):
                res = self._run(self.CANADA, pop)
                self.assertEqual(res["out"], want)

    def test_kuwait_still_works(self):
        res = self._run(self.KUWAIT, 5118445)
        self.assertEqual(res["out"], "5 - 8 million")
        res = self._run(self.KUWAIT, 50000000)
        self.assertEqual(res["out"], "8+ million")

    def test_non_bucket_buttons_ignored(self):
        buttons = ["Remove Ads", "Give up", "Next round",
                   "1. POPULATION", "2. CURRENCY", "Peso", "Dollar",
                   "< 50 thousand", "50 - 500 thousand",
                   "500k - 1 million", "1+ million"]
        res = self._run(buttons, 184100)
        self.assertEqual(res["out"], "50 - 500 thousand")


# ------------------------------------------------------------- Populous K buckets

_POPULOUS_K_PRELUDE = """
function mkEl(text, x, y, w, h, children, inNav) {
  return { innerText: text, offsetParent: {}, children: children || [],
    closest: (sel) => inNav ? {} : null,
    getBoundingClientRect: () => ({x: x, y: y, width: w, height: h}) };
}
const CHIP_CASTRIES = mkEl('Castries', 900, 300, 160, 44, []);
const CHIP_VIEUX = mkEl('Vieux Fort', 900, 360, 160, 44, []);
const CHIP_BISEE = mkEl('Bisee', 900, 420, 160, 44, []);
const CHIP_SOUF = mkEl('Soufrière', 900, 480, 160, 44, []);
const RANK1 = mkEl('1.', 300, 300, 20, 24, []);
const RANK2 = mkEl('2.', 300, 390, 20, 24, []);
const RANK3 = mkEl('3.', 300, 480, 20, 24, []);
const RANK4 = mkEl('4.', 300, 570, 20, 24, []);
const BUCKET1 = mkEl('20K', 325, 300, 40, 24, []);
const BUCKET2 = mkEl('13K', 325, 390, 40, 24, []);
const BUCKET3 = mkEl('8K', 325, 480, 40, 24, []);
const BUCKET4 = mkEl('5K', 325, 570, 40, 24, []);
const ICON1 = mkEl('', 368, 355, 24, 24, []);
const ICON2 = mkEl('', 368, 445, 24, 24, []);
const ICON3 = mkEl('', 368, 535, 24, 24, []);
const ICON4 = mkEl('', 368, 625, 24, 24, []);
const PILL1 = mkEl('', 280, 340, 200, 60, [ICON1]);
const PILL2 = mkEl('', 280, 430, 200, 60, [ICON2]);
const PILL3 = mkEl('', 280, 520, 200, 60, [ICON3]);
const PILL4 = mkEl('', 280, 610, 200, 60, [ICON4]);
const CHROME = mkEl('Guess', 500, 700, 120, 44, []);
const ALL = [CHIP_CASTRIES, CHIP_VIEUX, CHIP_BISEE, CHIP_SOUF,
  RANK1, RANK2, RANK3, RANK4,
  BUCKET1, BUCKET2, BUCKET3, BUCKET4,
  ICON1, ICON2, ICON3, ICON4, PILL1, PILL2, PILL3, PILL4, CHROME];
global.document = { querySelectorAll: (sel) => ALL };
"""


class TestPopulousKBucketsIter38(unittest.TestCase):
    WANT = ["Castries", "Bisee", "Soufrière", "Vieux Fort"]

    def _probe(self, prelude):
        js_calls = _capture_js(R._worldle_populous_probe, self.WANT)
        assert js_calls, "no JS captured from populous probe"
        return _run_node(js_calls[0], prelude,
                         "fn(" + json.dumps({"want": self.WANT,
                                             "n": 4}) + ")")

    def test_k_buckets_form_slots(self):
        # Run 39279697490: slots [] with K buckets; now 4 slots with
        # K labels and pill drop targets.
        res = self._probe(_POPULOUS_K_PRELUDE)
        chips = {c["name"] for c in res["chips"]}
        self.assertEqual(chips, set(self.WANT))
        slots = res["slots"]
        self.assertEqual([s["rank"] for s in slots], [1, 2, 3, 4])
        self.assertEqual([s["label"] for s in slots],
                         ["1. 20K", "2. 13K", "3. 8K", "4. 5K"])
        self.assertTrue(all(s["pill"] for s in slots))
        self.assertEqual([(s["x"], s["y"]) for s in slots],
                         [(380.0, 370.0), (380.0, 460.0),
                          (380.0, 550.0), (380.0, 640.0)])

    def test_m_buckets_still_work(self):
        # Iter-36 Canada shape must not regress: M labels unchanged.
        prelude = _POPULOUS_K_PRELUDE.replace(
            "'20K'", "'3M'").replace("'13K'", "'2M'").replace(
            "'8K'", "'1M'").replace("'5K'", "'1M'")
        res = self._probe(prelude)
        self.assertEqual([s["label"] for s in res["slots"]],
                         ["1. 3M", "2. 2M", "3. 1M", "4. 1M"])


if __name__ == "__main__":
    unittest.main()
