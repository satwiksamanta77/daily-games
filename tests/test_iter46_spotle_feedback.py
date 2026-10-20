"""Fix-loop iteration 46 tests — Spotle feedback-card selection.

Production defect (ANALYSIS44, run 39382795622): the feedback reader
picked a card POSITIONALLY (last .guess-wrapper in DOM order). The
board renders NEWEST FIRST, so for guess >= 2 the read returned a
PREVIOUS guess's card: guess 2 'Christina Perri' was logged with
card_artist='Elton John' and guess 1's statuses while her real tiles
were Gender GREEN + Genre GREEN + Country GREEN. The filter applied
the wrong artist's constraints, the pool emptied 56 -> 0, and the
solver honestly deferred. The defect was invisible while no second
guess had ever been submitted (one-card board: first == latest).

Fix: _SPOTLE_FEEDBACK_JS returns EVERY card with its artist;
_spotle_read_feedback(page, gid, want_artist) selects the card whose
artist == the guess just submitted and REFUSES (None) when no such
card exists; _spotle_read_feedback_settled waits for that card's
tiles to settle (two consecutive identical artist-matched reads);
_s_spotle filters ONLY on artist-matched feedback.

These fakes model the LIVE board: cards newest-first in DOM order,
each card carrying its own artist header + 6 tiles; the flat
artist/tiles fields carry the LAST DOM card (the OLDEST guess) —
exactly the trap the old positional reader fell into.

Run:  python3 -m unittest tests.test_iter46_spotle_feedback -v
"""

import json
import random
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

import runner as R
import solve_lib as S


def _art(name, year, rank, gender, genre, country, group):
    return {"artist": name, "debut_album_year": year,
            "listener_rank": rank, "gender": gender, "genre": genre,
            "country": country, "group_size": group}


_DOWN = ("M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6 1.41-1.41z")
_UP = ("M7.41 15.41L12 10.83l4.59 4.58L18 14l-6-6-6 6 1.41 1.41z")


def _tiles_for(guess, mystery):
    """The 6 feedback tiles for `guess` vs `mystery`, in game format."""
    st = S.spotle_compare(guess, mystery)
    ar = S.spotle_arrows(guess, mystery)
    labels = ["Debut", "Members", "Popularity", "Gender", "Genre"]
    tiles = []
    for i, lab in enumerate(labels):
        cls = ["item"]
        if st[i] == "correct":
            cls.append("correct")
        elif st[i] == "close":
            cls.append("close")
        arrows = []
        if lab == "Debut" and ar[0]:
            arrows = [_DOWN if ar[0] == "down" else _UP]
        elif lab == "Popularity" and ar[1]:
            arrows = [_DOWN if ar[1] == "down" else _UP]
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
    return tiles


class _FakeRow:
    """Bare <li> dropdown row, name as a text node (live shape)."""

    def __init__(self, page, name):
        self._page = page
        self._name = name

    def query_selector(self, sel):
        return None  # no <h3> on the live row

    def inner_text(self):
        return self._name

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
    def click(self, timeout=None):
        return None


class _LiveBoardPage:
    """Spotle board as the live game renders it: NEWEST card first.

    `submitted` is in play order; the DOM card_list is its reverse.
    The legacy flat artist/tiles fields expose the LAST DOM card —
    the OLDEST guess — the exact stale read of the iter-44 failure.
    """

    def __init__(self, pool, mystery):
        self.pool = pool
        self.mystery = mystery
        self.typed = ""
        self.submitted = []
        self.body_text = ""

    # -- board state -------------------------------------------------
    def _card_list(self):
        cards = []
        for name in reversed(self.submitted):  # newest first
            guess = next(p for p in self.pool if p["artist"] == name)
            cards.append({"artist": name,
                          "tiles": _tiles_for(guess, self.mystery)})
        return cards

    # -- playwright-ish surface --------------------------------------
    def wait_for_timeout(self, ms):
        return None

    def query_selector(self, sel):
        if "PLAY" in sel:
            return _FakeButton()
        if sel.startswith("#search") or "input[name=search]" in sel:
            return _FakeInput(self)
        return None

    def query_selector_all(self, sel):
        if sel == "ul.results li":
            return [_FakeRow(self, self.typed)] if self.typed else []
        return []

    def evaluate(self, js):
        if "_app/immutable" in js:
            return json.dumps(self.pool)
        if "querySelectorAll('.guess-wrapper').length" in js:
            return len(self.submitted)
        if "guess-wrapper" in js and "tiles" in js:
            cards = self._card_list()
            flat = cards[-1] if cards else {"artist": "", "tiles": []}
            won = bool(self.submitted) and \
                self.submitted[-1] == self.mystery["artist"]
            return {"cards": len(cards), "artist": flat["artist"],
                    "tiles": flat["tiles"], "card_list": cards,
                    "has_congrats": won}
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


class TestFeedbackCardSelection(unittest.TestCase):
    def setUp(self):
        self.pool = _make_pool()
        self.mystery = _art("Mystery Artist", 1998, 42, "f", "Rock",
                            "gb", 2)
        self.pool.append(self.mystery)
        self.page = _LiveBoardPage(self.pool, self.mystery)
        self.g1 = next(p for p in self.pool
                       if p["artist"] == "Artist 000")
        self.g2 = next(p for p in self.pool
                       if p["artist"] == "Artist 001")
        # The two guesses' real feedback differs (else the test is moot).
        self.assertNotEqual(S.spotle_compare(self.g1, self.mystery),
                            S.spotle_compare(self.g2, self.mystery))
        self.page.submitted.extend(["Artist 000", "Artist 001"])

    def test_board_is_newest_first(self):
        cards = self.page._card_list()
        self.assertEqual([c["artist"] for c in cards],
                         ["Artist 001", "Artist 000"])

    def test_reader_returns_second_guesss_own_card(self):
        # The production failure: guess 2 read guess 1's card.
        fb = R._spotle_read_feedback(self.page, "spotle", "Artist 001")
        self.assertIsNotNone(fb)
        statuses, arrows, artist, cards, congrats = fb
        self.assertEqual(artist, "Artist 001")
        self.assertEqual(statuses,
                         S.spotle_compare(self.g2, self.mystery))
        self.assertEqual(arrows, S.spotle_arrows(self.g2, self.mystery))
        self.assertEqual(cards, 2)

    def test_reader_can_still_read_first_guesss_card(self):
        fb = R._spotle_read_feedback(self.page, "spotle", "Artist 000")
        self.assertIsNotNone(fb)
        self.assertEqual(fb[2], "Artist 000")
        self.assertEqual(fb[0], S.spotle_compare(self.g1, self.mystery))

    def test_reader_refuses_unplayed_artist(self):
        # Never fall back to another guess's card.
        self.assertIsNone(R._spotle_read_feedback(
            self.page, "spotle", "Artist 002"))
        self.assertIsNone(R._spotle_read_feedback(
            self.page, "spotle", "Mystery Artist"))

    def test_settled_reader_matches_artist(self):
        fb = R._spotle_read_feedback_settled(self.page, "spotle",
                                             "Artist 001")
        self.assertIsNotNone(fb)
        self.assertEqual(fb[2], "Artist 001")
        self.assertEqual(fb[0], S.spotle_compare(self.g2, self.mystery))

    def test_settled_reader_none_when_card_absent(self):
        self.assertIsNone(R._spotle_read_feedback_settled(
            self.page, "spotle", "Artist 002", tries=2))


class TestFullLoopOnLiveBoard(unittest.TestCase):
    def setUp(self):
        self.pool = _make_pool()
        self.mystery = _art("Mystery Artist", 1998, 42, "f", "Rock",
                            "gb", 2)
        self.pool.append(self.mystery)
        self.page = _LiveBoardPage(self.pool, self.mystery)

    def test_loop_filters_only_on_each_guesss_own_feedback(self):
        seen = []
        orig = S.spotle_filter_pool

        def recording_filter(pool_arg, history):
            for h in history:
                seen.append((h["guess"]["artist"],
                             tuple(h["statuses"])))
            return orig(pool_arg, history)

        with mock.patch.object(S, "spotle_filter_pool",
                               recording_filter):
            ok, ev = R._s_spotle(self.page, "Mystery Artist", "spotle")
        self.assertTrue(ok, ev)
        played = R._PLAYED.get("spotle") or []
        self.assertEqual(played[-1], "Mystery Artist")
        self.assertGreaterEqual(len(played), 2)
        self.assertLessEqual(len(played), 10)
        self.assertEqual(len(set(played)), len(played))  # no repeats
        self.assertNotEqual(played[0], "Mystery Artist")
        # Every filter call must have used each guess's OWN feedback:
        # the recorded statuses equal the game's true compare for the
        # guess they are attributed to. (On the positional reader,
        # guess >= 2 carried guess 1's statuses and this breaks.)
        self.assertTrue(seen, "filter never ran — loop did not play")
        by_name = {p["artist"]: p for p in self.pool}
        for name, statuses in seen:
            self.assertEqual(
                statuses,
                S.spotle_compare(by_name[name], self.mystery),
                f"feedback attributed to {name!r} is not its own")


if __name__ == "__main__":
    unittest.main()
