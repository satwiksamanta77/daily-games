"""Fix-loop iteration 7 regression tests (globle / worldle / framed).

Run:  python3 -m unittest tests.test_iter7_fixes -v
All tests are synthetic/local except TestGlobleLive, which probes the
live Globle endpoint for the CURRENT day (read-only GETs, no browser,
no secrets) to prove a 404-timezone falls through to a 200-timezone
for the SAME day.
"""
import sys
import unittest
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent.parent
sys.path.insert(0, str(HERE))

import answers as A  # noqa: E402
import runner  # noqa: E402


class TestGlobleTzOrder(unittest.TestCase):
    """Globle: BROWSER_TZ first, then zones whose local date == the
    target day, then the rest — deduped, day param never changed."""

    def test_ordering(self):
        d = date(2026, 10, 17)
        order = A._globle_tz_order(d, "Pacific/Kiritimati")
        self.assertEqual(order[0], "Pacific/Kiritimati")
        self.assertEqual(len(order), len(set(order)))
        self.assertEqual(set(order), set(A._GLOBLE_TZ_CANDIDATES))
        rest = order[1:]
        local_d = [t for t in rest
                   if datetime.now(ZoneInfo(t)).date() == d]
        other = [t for t in rest
                 if datetime.now(ZoneInfo(t)).date() != d]
        self.assertEqual(rest, local_d + other)

    def test_browser_tz_default(self):
        order = A._globle_tz_order(date(2026, 10, 17), None)
        self.assertEqual(order[0], "Pacific/Kiritimati")


class TestGlobleLive(unittest.TestCase):
    """Live endpoint: a 404-timezone must fall through to a 200
    timezone for the SAME day (no day change, no previous-day fallback)."""

    def test_fallback_same_day(self):
        import requests
        d = date(2026, 10, 17)
        n = 200  # list length sent by the solver; any int works for gating
        kirit = requests.get(
            "https://globle-game.com/answer?day=2026-10-17",
            params={"list": n, "timeZone": "Pacific/Kiritimati"},
            headers={"User-Agent": "WordSolverX Video"}, timeout=20)
        utc = requests.get(
            "https://globle-game.com/answer?day=2026-10-17",
            params={"list": n, "timeZone": "UTC"},
            headers={"User-Agent": "WordSolverX Video"}, timeout=20)
        # Kiritimati has rolled past midnight (local Oct 18), so the
        # endpoint 404s for Oct 17 there; UTC still serves Oct 17.
        self.assertEqual(kirit.status_code, 404)
        self.assertEqual(utc.status_code, 200)
        try:
            got = A.globle(d)
        except SystemExit as e:
            # This VM lacks pycryptodome (CI installs it via
            # requirements.txt), so decoding fails here. The
            # fall-through is still proven when the error shows a
            # non-Kiritimati tz was reached after the 404.
            msg = str(e)
            self.assertIn("No module named 'Crypto'", msg)
            # A later timezone (Sydney, whose local date is still the
            # target day) was reached and its 200 response got as far
            # as decoding — proving fall-through past the Kiritimati 404.
            self.assertIn("Australia/Sydney", msg)
            return
        self.assertEqual(got["date"], "2026-10-17")
        self.assertEqual(got["answer"], "Ghana")
        self.assertTrue(got["via"].startswith("api:"))
        self.assertNotEqual(got["via"], "api:Pacific/Kiritimati")


class TestTriviaBlankStatements(unittest.TestCase):
    """Worldle trivia: GENERAL/HISTORY fill-in-the-blank facts end in
    '.', not '?' — the matcher must still match them against the DB
    question, however the blank renders."""

    def test_general_nickname_blank(self):
        db = ("The national football team of Togo is popularly known "
              "by the nickname the _______.")
        for shown in (
                "The national football team of Togo is popularly known "
                "by the nickname the _______.",
                "The national football team of Togo is popularly known "
                "by the nickname the .",
                "The national football team of Togo is popularly known "
                "by the nickname the",
        ):
            self.assertTrue(runner._s_worldle_trivia_match(db, shown),
                            f"no match for shown={shown!r}")

    def test_history_blank(self):
        db = ("After World War I, the former German colony of Togoland "
              "was divided into British and _______ mandate territories.")
        shown = ("After World War I, the former German colony of "
                 "Togoland was divided into British and _______ mandate "
                 "territories.")
        self.assertTrue(runner._s_worldle_trivia_match(db, shown))

    def test_no_false_match(self):
        db = ("The national football team of Togo is popularly known "
              "by the nickname the _______.")
        self.assertFalse(runner._s_worldle_trivia_match(
            db, "Togo's most important mineral export is _______."))


class TestAreaDisplayParse(unittest.TestCase):
    """Worldle area: the display may be a textContent-only number
    (SVG <text> / CSS-hidden), a small start value, or an aria value."""

    def test_textcontent_unit(self):
        val, src = runner._worldle_area_parse_display(
            {"commonText": "Area\n56,785 km²\nGive up"})
        self.assertEqual((val, src), (56785, "commonText:single"))

    def test_pure_small_start(self):
        # A widget whose whole display element reads "0" (below the
        # old bare >=100 filter) must still be found.
        val, src = runner._worldle_area_parse_display({"pure": "0"})
        self.assertEqual((val, src), (0, "pure:single"))

    def test_pure_widget_number(self):
        # Iter 11: multiple distinct pure candidates = ambiguous,
        # never max() — the reader returns None, not the largest.
        val, src = runner._worldle_area_parse_display(
            {"pure": "1\n56785"})
        self.assertIsNone(val)
        self.assertIsNone(src)

    def test_aria_value(self):
        val, src = runner._worldle_area_parse_display(
            {"aria": "56785"})
        self.assertEqual((val, src), (56785, "aria:single"))

    def test_tab_labels_not_display(self):
        val, src = runner._worldle_area_parse_display(
            {"common": "1. FACTS 2. GENERAL"})
        self.assertIsNone(val)
        self.assertIsNone(src)

    def test_old_bare_still_works(self):
        val, src = runner._worldle_area_parse_display(
            {"common": "something 56785"})
        self.assertEqual((val, src), (56785, "common:single"))


class TestPhrasePick(unittest.TestCase):
    """Worldle language phrase: only a game-fetched mapping that
    pairs the prompt with the shown options may be clicked."""

    def test_match(self):
        pj = {"phrases": [
            {"phrase": "My name is", "ewe": "Nye ŋkɔe nye Kofi",
             "kbp": "Mɩn hɩɖɛ wɩɖɛ Ama"},
            {"phrase": "Hello", "ewe": "Ŋdi", "kbp": "Alɩfɩya"},
        ]}
        shown = ["Nye ŋkɔe nye Kofi", "Mɩn hɩɖɛ wɩɖɛ Ama", "Ŋdi"]
        got = runner._worldle_phrase_pick(
            pj, 'How do you say "My name is" in Ewe and Kabiye?',
            ["ewe", "kbp"], ["Ewe", "Kabiye"], shown)
        self.assertEqual(got, {"ewe": "Nye ŋkɔe nye Kofi",
                               "kbp": "Mɩn hɩɖɛ wɩɖɛ Ama"})

    def test_no_mapping_is_none(self):
        pj = {"phrases": [{"phrase": "Hello", "ewe": "Ŋdi",
                           "kbp": "Alɩfɩya"}]}
        got = runner._worldle_phrase_pick(
            pj, 'How do you say "My name is" in Ewe and Kabiye?',
            ["ewe", "kbp"], ["Ewe", "Kabiye"], ["Ŋdi", "Alɩfɩya"])
        self.assertIsNone(got)

    def test_partial_mapping_is_none(self):
        # Only one language's translation among the shown options:
        # never click a half-mapping.
        pj = {"phrases": [{"phrase": "My name is",
                           "ewe": "Nye ŋkɔe nye Kofi",
                           "kbp": "not shown"}]}
        got = runner._worldle_phrase_pick(
            pj, 'How do you say "My name is" in Ewe and Kabiye?',
            ["ewe", "kbp"], ["Ewe", "Kabiye"], ["Nye ŋkɔe nye Kofi"])
        self.assertIsNone(got)


class TestIter7SourceShape(unittest.TestCase):
    """The iter-7 fixes are present in source (guards against a
    revert silently dropping them)."""

    def test_globle_uses_tz_order(self):
        src = (HERE / "answers.py").read_text(encoding="utf-8")
        self.assertIn("_globle_tz_order", src)
        self.assertIn("for tz in _globle_tz_order", src)

    def test_trivia_blank_scan(self):
        src = (HERE / "runner.py").read_text(encoding="utf-8")
        self.assertIn("_{3,}", src)
        self.assertIn("db_opts <= shown_opts", src)

    def test_capital_pin_probe(self):
        src = (HERE / "runner.py").read_text(encoding="utf-8")
        self.assertIn("_worldle_pin_probe", src)
        self.assertIn("elementFromPoint", src)
        self.assertIn("svg circle", src)

    def test_area_textcontent(self):
        src = (HERE / "runner.py").read_text(encoding="utf-8")
        self.assertIn("commonText", src)
        self.assertIn('src + ":single"', src)

    def test_framed_ad_state(self):
        src = (HERE / "runner.py").read_text(encoding="utf-8")
        self.assertIn("_framed_reward_ad_state", src)
        self.assertIn("ad-state", src)


if __name__ == "__main__":
    unittest.main()
