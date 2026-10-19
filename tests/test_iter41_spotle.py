"""Fix-loop iteration 41 tests — Spotle (19th game, new build).

Covers: the SSR date gate (game's own number, NOT the answers page's),
the feedback constraint model (ports of the game's own ee() + arrows),
the maximin pick, pool capture/parse, the feedback probe mapping, the
exact-match submit, the full feedback-driven solver loop on a simulated
board, registry completeness (18 -> 19), SEO caps, and the no-plan-
fallback guard. All tests are synthetic/local: fake pages, monkeypatched
fetch — no browser, no network, no live play.

Run:  python3 -m unittest tests.test_iter41_spotle -v
"""

import json
import random
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

import answers as A
import runner as R
import solve_lib as S
import seo as SEO


# ---------------------------------------------------------------- gate
class TestSpotleNumberGate(unittest.TestCase):
    def test_number_anchor(self):
        self.assertEqual(A.spotle_number_for(date(2026, 10, 19)), 1634)
        self.assertEqual(A.spotle_number_for(date(2026, 10, 20)), 1635)
        self.assertEqual(A.spotle_number_for(date(2026, 10, 18)), 1633)

    def test_ssr_parse_real_shape(self):
        html = ('...todaysDate:"10/19/2026",spotleNumber:1634,'
                'todaysEntry:{date:"10/19/2026",artist:"Evanescence",'
                'itunesId:"1440666111",image_uri:"https://x/y.jpg"},'
                'yesterdaysEntry:{date:"10/18/2026",artist:"Beastie Boys"}...')
        self.assertEqual(A._spotle_parse_ssr(html),
                         (date(2026, 10, 19), 1634, "Evanescence"))

    def test_ssr_parse_missing_fields(self):
        self.assertEqual(A._spotle_parse_ssr("<html>nothing</html>"),
                         (None, None, None))
        self.assertEqual(A._spotle_parse_ssr(
            'todaysDate:"10/19/2026",spotleNumber:1634'), (None, None, None))

    def _run_gate(self, html, tgt):
        class FakeResp:
            text = html

            def raise_for_status(self):
                pass

        import requests as _requests
        with mock.patch.object(_requests, "get",
                               return_value=FakeResp()):
            return A.spotle(tgt)

    def test_gate_passes_for_served_day(self):
        html = ('todaysDate:"10/20/2026",spotleNumber:1635,'
                'todaysEntry:{date:"10/20/2026",artist:"Some Artist"}')
        out = self._run_gate(html, date(2026, 10, 20))
        self.assertEqual(out["answer"], "Some Artist")
        self.assertEqual(out["number"], 1635)

    def test_gate_defers_stale_board(self):
        # Served 10/19 while the target is 10/20: yesterday's board.
        html = ('todaysDate:"10/19/2026",spotleNumber:1634,'
                'todaysEntry:{date:"10/19/2026",artist:"Evanescence"}')
        with self.assertRaises(SystemExit):
            self._run_gate(html, date(2026, 10, 20))

    def test_gate_defers_number_mismatch(self):
        # Right date, wrong number: a renumbered / stale board.
        html = ('todaysDate:"10/20/2026",spotleNumber:9999,'
                'todaysEntry:{date:"10/20/2026",artist:"Some Artist"}')
        with self.assertRaises(SystemExit):
            self._run_gate(html, date(2026, 10, 20))

    def test_gate_defers_on_fetch_failure(self):
        import requests as _requests
        with mock.patch.object(_requests, "get",
                               side_effect=Exception("boom")):
            with self.assertRaises(SystemExit):
                A.spotle(date(2026, 10, 20))

    def test_gate_defers_on_unparseable_ssr(self):
        with self.assertRaises(SystemExit):
            self._run_gate("<html>garbage</html>", date(2026, 10, 20))

    def test_answers_page_number_not_used(self):
        # The answers page says #1677 for 2026-10-20. The GAME says 1635.
        # A board serving 1677 for 10/20 must FAIL the gate (it is not
        # the game's number for that date).
        html = ('todaysDate:"10/20/2026",spotleNumber:1677,'
                'todaysEntry:{date:"10/20/2026",artist:"Some Artist"}')
        with self.assertRaises(SystemExit):
            self._run_gate(html, date(2026, 10, 20))


# ---------------------------------------------------------------- compare / filter
def _art(name, year, rank, gender, genre, country, group):
    return {"artist": name, "debut_album_year": year, "listener_rank": rank,
            "gender": gender, "genre": genre, "country": country,
            "group_size": group}


class TestSpotleCompare(unittest.TestCase):
    def test_all_correct(self):
        m = _art("M", 2000, 100, "f", "Pop", "us", 1)
        self.assertEqual(S.spotle_compare(m, m),
                         ("correct",) * 6)
        self.assertEqual(S.spotle_arrows(m, m), (None, None))

    def test_thresholds(self):
        m = _art("M", 2000, 100, "f", "Pop", "us", 1)
        # debut: |diff| 5 -> close, 6 -> miss
        self.assertEqual(S.spotle_compare(_art("G", 2005, 100, "f", "Pop",
                                               "us", 1), m)[0], "close")
        self.assertEqual(S.spotle_compare(_art("G", 2006, 100, "f", "Pop",
                                               "us", 1), m)[0], "miss")
        # rank: |diff| 50 -> close, 51 -> miss
        self.assertEqual(S.spotle_compare(_art("G", 2000, 150, "f", "Pop",
                                               "us", 1), m)[2], "close")
        self.assertEqual(S.spotle_compare(_art("G", 2000, 151, "f", "Pop",
                                               "us", 1), m)[2], "miss")
        # group / gender / genre have NO close state
        self.assertEqual(S.spotle_compare(_art("G", 2000, 100, "m", "Pop",
                                               "us", 1), m)[3], "miss")
        self.assertEqual(S.spotle_compare(_art("G", 2000, 100, "f", "Rock",
                                               "us", 1), m)[4], "miss")
        self.assertEqual(S.spotle_compare(_art("G", 2000, 100, "f", "Pop",
                                               "us", 2), m)[1], "miss")

    def test_country_close_is_same_continent(self):
        m = _art("M", 2000, 100, "f", "Pop", "us", 1)
        # Canada: same continent (north_america), different country -> close
        self.assertEqual(S.spotle_compare(_art("G", 2000, 100, "f", "Pop",
                                               "ca", 1), m)[5], "close")
        # France: different continent -> miss
        self.assertEqual(S.spotle_compare(_art("G", 2000, 100, "f", "Pop",
                                               "fr", 1), m)[5], "miss")

    def test_arrow_directions(self):
        m = _art("M", 2000, 100, "f", "Pop", "us", 1)
        # debut guess later than mystery -> down (mystery earlier)
        self.assertEqual(S.spotle_arrows(_art("G", 2003, 100, "f", "Pop",
                                              "us", 1), m), ("down", None))
        # debut guess earlier -> up
        self.assertEqual(S.spotle_arrows(_art("G", 1997, 100, "f", "Pop",
                                              "us", 1), m), ("up", None))
        # rank guess LESS popular (higher number) -> up (mystery more pop)
        self.assertEqual(S.spotle_arrows(_art("G", 2000, 150, "f", "Pop",
                                              "us", 1), m), (None, "up"))
        # rank guess MORE popular (lower number) -> down
        self.assertEqual(S.spotle_arrows(_art("G", 2000, 50, "f", "Pop",
                                              "us", 1), m), (None, "down"))

    def test_filter_keeps_mystery_drops_others(self):
        m = _art("Mystery", 2000, 100, "f", "Pop", "us", 1)
        g = _art("G1", 2003, 150, "m", "Pop", "ca", 1)
        st, ar = S.spotle_compare(g, m), S.spotle_arrows(g, m)
        pool = [m, g,
                _art("B", 2001, 120, "f", "Pop", "mx", 1),   # in all windows
                _art("C", 2010, 120, "f", "Pop", "mx", 1)]   # year outside
        kept = S.spotle_filter_pool(
            pool, [{"guess": g, "statuses": st, "arrows": ar}])
        self.assertEqual([a["artist"] for a in kept], ["Mystery", "B"])

    def test_filter_empty_when_feedback_impossible(self):
        m = _art("M", 2000, 100, "f", "Pop", "us", 1)
        # A candidate can never be consistent with a "correct" debut
        # against a different year.
        g = _art("G", 2001, 100, "f", "Pop", "us", 1)
        st = ("correct", "correct", "correct", "correct", "correct",
              "correct")
        self.assertEqual(
            S.spotle_filter_pool([m],
                                 [{"guess": g, "statuses": st,
                                   "arrows": (None, None)}]), [])

    def test_pick_next_splits_and_never_repeats(self):
        pool = [_art(f"A{i}", 1990 + i, 10 * i, "m" if i % 2 else "f",
                     "Pop", "us", 1) for i in range(10)]
        pick = S.spotle_pick_next(pool, pool, guessed_names=[])
        self.assertIsNotNone(pick)
        pick2 = S.spotle_pick_next(pool, pool,
                                   guessed_names=[pick["artist"]])
        self.assertNotEqual(pick2["artist"], pick["artist"])
        self.assertIsNone(S.spotle_pick_next(
            [], [], guessed_names=[]))
        self.assertIsNone(S.spotle_pick_next(
            pool, pool, guessed_names=[p["artist"] for p in pool]))


# ---------------------------------------------------------------- pool parse
class TestSpotlePoolParse(unittest.TestCase):
    def test_parse_valid(self):
        raw = json.dumps([_art("Drake", 2009, 0, "m", "Hip Hop", "ca", 1),
                          _art("X", 2000, 1, "f", "Pop", "us", 1)]
                         + [_art(f"F{i}", 2000 + i % 30, 2 + i, "m", "Pop",
                                 "us", 1) for i in range(120)])
        pool = R._spotle_parse_pool(raw, "spotle")
        self.assertEqual(len(pool), 122)
        self.assertEqual(pool[0]["artist"], "Drake")

    def test_parse_rejects_small_and_garbage(self):
        self.assertEqual(R._spotle_parse_pool("not json", "spotle"), [])
        self.assertEqual(R._spotle_parse_pool(
            json.dumps([_art("A", 2000, 0, "m", "Pop", "us", 1)]),
            "spotle"), [])


# ---------------------------------------------------------------- probe + submit (fake page)
class _FakeH3:
    def __init__(self, text):
        self._text = text

    def inner_text(self):
        return self._text


class _FakeRow:
    def __init__(self, page, name):
        self._page = page
        self._name = name

    def query_selector(self, sel):
        if sel == "h3":
            return _FakeH3(self._name)
        return None

    def scroll_into_view_if_needed(self, timeout=None):
        return None

    def click(self, timeout=None):
        self._page.submitted.append(self._name)


class _FakeInput:
    def __init__(self, page):
        self._page = page

    def fill(self, text):
        self._page.typed = text


class _FakeButton:
    def __init__(self, page):
        self._page = page

    def click(self, timeout=None):
        self._page.play_clicked = True


class _FakePage:
    """Simulates the Spotle board: pool capture, dropdown, feedback."""

    def __init__(self, pool, mystery):
        self.pool = pool
        self.mystery = mystery
        self.typed = ""
        self.submitted = []       # artist names clicked in the dropdown
        self.play_clicked = False
        self.body_text = ""

    def wait_for_timeout(self, ms):
        return None

    def query_selector(self, sel):
        if "PLAY" in sel:
            return _FakeButton(self)
        if sel.startswith("#search") or "input[name=search]" in sel:
            return _FakeInput(self)
        return None

    def query_selector_all(self, sel):
        if sel == "ul.results li":
            # The dropdown shows the typed name as its exact match.
            return [_FakeRow(self, self.typed)] if self.typed else []
        return []

    def evaluate(self, js):
        if "_app/immutable" in js:
            return json.dumps(self.pool)
        if "guess-wrapper" in js and "tiles" in js:
            # Feedback for the latest submitted guess, in game format.
            if not self.submitted:
                return {"cards": 0, "artist": "", "tiles": [],
                        "has_congrats": False}
            guess = next(p for p in self.pool
                         if p["artist"] == self.submitted[-1])
            st = S.spotle_compare(guess, self.mystery)
            ar = S.spotle_arrows(guess, self.mystery)
            labels = ["Debut", "Members", "Popularity", "Gender", "Genre"]
            tiles = []
            for i, lab in enumerate(labels):
                cls = ["item"]
                if st[i] == "correct":
                    cls.append("correct")
                elif st[i] == "close":
                    cls.append("close")
                arrows = []
                if lab == "Debut" and ar[0] == "down":
                    arrows = ["M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6 "
                              "1.41-1.41z"]
                elif lab == "Debut" and ar[0] == "up":
                    arrows = ["M7.41 15.41L12 10.83l4.59 4.58L18 14l-6-6-6 6 "
                              "1.41 1.41z"]
                elif lab == "Popularity" and ar[1] == "down":
                    arrows = ["M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6 "
                              "1.41-1.41z"]
                elif lab == "Popularity" and ar[1] == "up":
                    arrows = ["M7.41 15.41L12 10.83l4.59 4.58L18 14l-6-6-6 6 "
                              "1.41 1.41z"]
                tiles.append({"label": lab, "classes": cls, "value": "x",
                              "has_flag": False, "arrows": arrows})
            cls = ["item"]
            if st[5] == "correct":
                cls.append("correct")
            elif st[5] == "close":
                cls.append("close")
            tiles.append({"label": guess["country"].upper(), "classes": cls,
                          "value": guess["country"], "has_flag": True,
                          "arrows": []})
            return {"cards": len(self.submitted),
                    "artist": guess["artist"], "tiles": tiles,
                    "has_congrats": all(s == "correct" for s in st)}
        if "querySelectorAll('.guess-wrapper').length" in js:
            return len(self.submitted)
        if "body.innerText" in js:
            return self.body_text
        if "localStorage.clear" in js:
            return None
        return None

    def reload(self, wait_until=None):
        return None


def _make_pool():
    rng = random.Random(7)
    pool = []
    genres = ["Pop", "Rock", "Hip Hop", "R&B", "Electronic"]
    countries = ["us", "gb", "ca", "fr", "de", "br", "jp", "au"]
    for i in range(130):
        pool.append(_art(f"Artist {i:03d}", 1960 + (i * 7) % 60,
                         i, "m" if i % 2 else "f",
                         genres[i % len(genres)],
                         countries[i % len(countries)], 1 + i % 4))
    for name in S.SPOTLE_OPENERS:
        pool.append(_art(name, 1970 + (len(name) % 40), 500 + len(name),
                         "f" if len(name) % 2 else "m", "Pop", "us", 1))
    return pool


class TestSpotleProbeAndSubmit(unittest.TestCase):
    def setUp(self):
        self.pool = _make_pool()
        self.mystery = _art("Mystery Artist", 1998, 42, "f", "Rock", "gb", 2)
        self.pool.append(self.mystery)
        self.page = _FakePage(self.pool, self.mystery)

    def test_feedback_probe_maps_tiles(self):
        self.page.submitted.append("Artist 000")
        fb = R._spotle_read_feedback(self.page, "spotle")
        self.assertIsNotNone(fb)
        statuses, arrows, artist, cards, congrats = fb
        guess = self.pool[0]
        self.assertEqual(statuses, S.spotle_compare(guess, self.mystery))
        self.assertEqual(arrows, S.spotle_arrows(guess, self.mystery))
        self.assertEqual(artist, "Artist 000")
        self.assertEqual(cards, 1)

    def test_feedback_probe_fails_on_wrong_tile_count(self):
        class _BadPage(_FakePage):
            def evaluate(self, js):
                if "tiles" in js:
                    return {"cards": 1, "artist": "X",
                            "tiles": [{"label": "Debut"}],
                            "has_congrats": False}
                return super().evaluate(js)

        self.assertIsNone(R._spotle_read_feedback(_BadPage(self.pool,
                                                           self.mystery),
                                                  "spotle"))

    def test_submit_clicks_exact_match(self):
        ok, detail = R._spotle_submit(self.page, "spotle", "Artist 005")
        self.assertTrue(ok, detail)
        self.assertEqual(self.page.submitted, ["Artist 005"])

    def test_submit_never_submits_without_exact_match(self):
        # Empty dropdown (no rows): no click, no submission.
        class _NoRows(_FakePage):
            def query_selector_all(self, sel):
                return []

        page = _NoRows(self.pool, self.mystery)
        ok, detail = R._spotle_submit(page, "spotle", "Artist 005")
        self.assertFalse(ok)
        self.assertEqual(page.submitted, [])

    def test_full_loop_converges_from_feedback(self):
        # The gate artist is the mystery, but the solver must NOT play
        # it first: it converges from feedback alone. The mystery is
        # not in SPOTLE_OPENERS, so guess 1 is always a real opener.
        self.assertNotIn(self.mystery["artist"], S.SPOTLE_OPENERS)
        ok, ev = R._s_spotle(self.page, "Mystery Artist", "spotle")
        self.assertTrue(ok, ev)
        played = R._PLAYED.get("spotle") or []
        self.assertEqual(played[-1], "Mystery Artist")
        self.assertLessEqual(len(played), 10)
        self.assertGreaterEqual(len(played), 2)
        self.assertEqual(len(set(played)), len(played))  # no repeats
        self.assertNotEqual(played[0], "Mystery Artist")
        self.assertIn("solved in", ev)

    def test_full_loop_defers_when_pool_capture_fails(self):
        class _NoPool(_FakePage):
            def evaluate(self, js):
                if "_app/immutable" in js:
                    return None
                return super().evaluate(js)

        page = _NoPool(self.pool, self.mystery)
        ok, ev = R._s_spotle(page, "Mystery Artist", "spotle")
        self.assertFalse(ok)
        self.assertIn("pool", ev)


# ---------------------------------------------------------------- registry / SEO / guard
class TestSpotleRegistry(unittest.TestCase):
    def test_games_registry(self):
        g = R.GAMES.get("spotle")
        self.assertIsNotNone(g)
        self.assertEqual(g["url"], "https://spotle.io")
        self.assertEqual(g["slug"], "spotle-answer-today")
        self.assertTrue(g.get("no_fake"))
        self.assertEqual(len(R.GAMES), 19)

    def test_solver_registry(self):
        self.assertIn("spotle", R.SOLVERS)
        self.assertIn("spotle", A.ANSWER_FNS)
        self.assertEqual(R._puzzle_number_for("spotle", "2026-10-19"),
                         1634)

    def test_no_letter_scoring_and_attribute_win(self):
        self.assertIn("spotle", R._NO_LETTER_SCORING)
        import daily_parity as DP
        self.assertIn("spotle", DP._ATTRIBUTE_WIN_GAMES)

    def test_games_json(self):
        root = Path(__file__).parent.parent
        games = json.loads((root / "games.json").read_text())["games"]
        self.assertEqual(len(games), 19)
        sp = [g for g in games if g["id"] == "spotle"]
        self.assertEqual(len(sp), 1)
        self.assertEqual(sp[0]["seo_slug"], "spotle-answer-today")

    def test_cross_links_include_spotle(self):
        urls = [u for _, u in SEO.DEFAULT_CROSS_LINKS]
        self.assertIn("https://wordsolverx.com/spotle-answer-today", urls)
        self.assertEqual(len(SEO.DEFAULT_CROSS_LINKS), 19)
        self.assertEqual(len(set(urls)), 19)

    def test_workflows(self):
        root = Path(__file__).parent.parent / ".github" / "workflows"
        allwf = (root / "daily-all-videos.yml").read_text()
        dawn = (root / "daily-batch-a-dawn.yml").read_text()
        self.assertIn("batch-b-spotle", allwf)
        self.assertIn("generate-spotle", dawn)
        # Dawn-only: Spotle must NOT appear in the 9pm, marveldle or
        # Batch-C workflows (its NY-midnight flip is after 9pm IST).
        for f in ("daily-batch-a-9pm.yml", "daily-batch-b-marveldle.yml",
                  "daily-batch-c-1030pm.yml"):
            text = (root / f).read_text()
            self.assertNotIn("spotle", text, f)

    def test_result_steps_no_fabricated_plan(self):
        R._PLAYED.pop("spotle", None)
        self.assertEqual(R._result_steps("spotle", "Mystery Artist"), [])

    def test_seo_caps(self):
        title = SEO.build_title("Spotle", "Oct 20, 2026",
                                puzzle_number=1635)
        self.assertLessEqual(len(title), 100)
        self.assertIn("Spotle", title)
        tags = SEO.build_tags("Spotle", "Oct 20, 2026",
                              puzzle_number=1635)
        self.assertTrue(tags)
        self.assertLessEqual(sum(len(t) for t in tags), 2000)


if __name__ == "__main__":
    unittest.main()
