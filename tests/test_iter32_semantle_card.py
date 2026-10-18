"""Fix-loop iteration 32 tests — Semantle score probe reads the recent-guess card (ANALYSIS30 §3-§4).

Root cause (pinned, not re-diagnosed): at probe time the CURRENT guess
lives ONLY in the recent-guess card (rounded-md div inside <app-guesses>,
rendering recentGuess.guess + recentGuess.initialSimilarity); the ngFor
list holds only guesses 1..N-1. A list-only probe can NEVER see the
current guess — the bounded poll (iter 31) was disproven for exactly this
reason (10 attempts, kept still lagged by one guess).

Fix: _semantic_dom_scores also captures the card per attempt
{text, loading: card contains app-spinner}; _semantic_parse_dom_rows
parses the card when no list row matches, only if the card's word
matches the target AND loading is false (while app-spinner is present
the card shows the -1.00 placeholder, not the real similarity). A
stale card (wrong word) never matches. The list row is preferred when
both match. The bounded poll is kept: it now covers the brief
card-loading window (a loading card leaves matched_row None).
"""
import inspect
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import runner as R

CARD = {"text": "1\nnutrient\n-0.67\nfar", "loading": False}
CARD_LOADING = {"text": "1\nnutrient\n-1.00\nfar", "loading": True}
CARD_STALE = {"text": "1\ncombat\n6.38\ncold", "loading": False}
LIST_ROW = {"src": "semantle", "text": "1\nnutrient\n-0.67\n9999+"}
CTX_ROW = {"src": "contexto", "word": "outstanding", "score": "1344"}
DIAG = {"semantle_raw": 2, "semantle_kept": 1,
        "contexto_raw": 0, "contexto_kept": 0}


class TestParserCard(unittest.TestCase):
    def test_card_parsed_when_list_empty(self):
        got, probe = R._semantic_parse_dom_rows([], "nutrient", card=CARD)
        self.assertEqual(got, "-0.67")
        self.assertIn("nutrient", probe["matched_row"])
        self.assertEqual(probe["card_loading"], False)
        self.assertIn("nutrient", probe["card_text"])

    def test_loading_card_no_match(self):
        # app-spinner present => -1.00 placeholder, NOT the real score.
        got, probe = R._semantic_parse_dom_rows([], "nutrient",
                                                 card=CARD_LOADING)
        self.assertIsNone(got)
        self.assertIsNone(probe["matched_row"])
        self.assertEqual(probe["card_loading"], True)

    def test_stale_card_no_match(self):
        # Card shows a previous guess: word mismatch => no match.
        got, probe = R._semantic_parse_dom_rows([], "nutrient",
                                                 card=CARD_STALE)
        self.assertIsNone(got)
        self.assertIsNone(probe["matched_row"])
        self.assertEqual(probe["card_loading"], False)

    def test_list_preferred_when_both_match(self):
        # Same value either way in production; the test pins the
        # preference: the list row's score wins over the card's.
        card = {"text": "1\nnutrient\n9.99\nfar", "loading": False}
        got, probe = R._semantic_parse_dom_rows([LIST_ROW], "nutrient",
                                                 card=card)
        self.assertEqual(got, "-0.67")

    def test_card_u2212_minus_normalized(self):
        card = {"text": "1\nnutrient\n\u22120.67\nfar", "loading": False}
        got, _probe = R._semantic_parse_dom_rows([], "nutrient", card=card)
        self.assertEqual(got, "-0.67")
        self.assertEqual(float(got), -0.67)

    def test_card_matched_without_decimal_is_none(self):
        card = {"text": "1\nnutrient\nfar", "loading": False}
        got, probe = R._semantic_parse_dom_rows([], "nutrient", card=card)
        self.assertIsNone(got)
        self.assertIsNotNone(probe["matched_row"])

    def test_contexto_ignores_card(self):
        # The card is a Semantle-only record; a Contexto row still wins
        # on its own path and the card is never consulted for it.
        got, _probe = R._semantic_parse_dom_rows([CTX_ROW], "outstanding",
                                                  card=CARD)
        self.assertEqual(got, "1344")

    def test_no_card_old_shape_unchanged(self):
        got, probe = R._semantic_parse_dom_rows([LIST_ROW], "nutrient")
        self.assertEqual(got, "-0.67")
        self.assertIsNone(probe["card_text"])
        self.assertIsNone(probe["card_loading"])


class FakePage:
    """Distinguishes evaluate calls by JS content: main probe
    (out.push) returns dicts {rows, card} popped per call; diag probe
    (semantle_raw) returns DIAG. Bare lists are also accepted (old
    shape) to pin backward compat."""

    def __init__(self, main_seq, diag=None):
        self.main_seq = list(main_seq)
        self.diag = dict(diag if diag is not None else DIAG)
        self.waits = []
        self.calls = 0

    def wait_for_timeout(self, ms):
        self.waits.append(ms)

    def evaluate(self, js):
        self.calls += 1
        if "semantle_raw" in js:
            return dict(self.diag)
        if "out.push" in js:
            if self.main_seq:
                return self.main_seq.pop(0)
            return {"rows": [], "card": None}
        if "document.body.innerText" in js:
            return ""
        raise AssertionError(f"unexpected evaluate JS: {js[:80]!r}")


class TestDomScoresCard(unittest.TestCase):
    def test_card_score_first_attempt(self):
        pg = FakePage([{"rows": [], "card": CARD}])
        got, probe = R._semantic_dom_scores(pg, "nutrient")
        self.assertEqual(got, "-0.67")
        self.assertEqual(probe["attempts"], 1)
        self.assertEqual(pg.waits, [])
        self.assertIn("nutrient", probe["card_text"])
        self.assertEqual(probe["card_loading"], False)

    def test_loading_card_then_loaded_polls_once(self):
        # First attempt: card loading (-1.00 placeholder) => no match
        # => poll; second attempt: card loaded => score.
        pg = FakePage([{"rows": [], "card": CARD_LOADING},
                       {"rows": [], "card": CARD}])
        got, probe = R._semantic_dom_scores(pg, "nutrient")
        self.assertEqual(got, "-0.67")
        self.assertEqual(probe["attempts"], 2)
        self.assertEqual(pg.waits, [1000])

    def test_stale_card_polls_to_cap(self):
        pg = FakePage([{"rows": [], "card": CARD_STALE}])
        got, probe = R._semantic_dom_scores(pg, "nutrient")
        self.assertIsNone(got)
        self.assertEqual(probe["attempts"], 10)
        self.assertEqual(pg.waits, [1000] * 9)

    def test_bare_list_shape_backward_compat(self):
        # Old-shape evaluate result (bare rows array, no card).
        pg = FakePage([[LIST_ROW]])
        got, probe = R._semantic_dom_scores(pg, "nutrient")
        self.assertEqual(got, "-0.67")
        self.assertIsNone(probe["card_text"])
        self.assertIsNone(probe["card_loading"])


class TestCallerLogsCard(unittest.TestCase):
    def test_card_fields_logged_every_probe(self):
        pg = FakePage([{"rows": [], "card": CARD}], )
        seen = []
        orig_dbg = R._dbg
        R._dbg = lambda gid, msg: seen.append(f"[{gid}] {msg}")
        try:
            ok, score = R._semantic_guess_accepted(
                pg, "nutrient", "before text", gid="semantle")
        finally:
            R._dbg = orig_dbg
        self.assertTrue(ok)
        self.assertEqual(score, "-0.67")
        diag = [m for m in seen if "score probe:" in m]
        self.assertEqual(len(diag), 1, seen)
        self.assertIn("card_text=", diag[0])
        self.assertIn("card_loading=False", diag[0])
        self.assertIn("score='-0.67'", diag[0])


class TestMainJsParses(unittest.TestCase):
    def test_main_js_parses(self):
        # The modified main evaluate payload (now returns {rows, card}
        # and captures the card) must be valid JS.
        src = inspect.getsource(R._semantic_dom_scores)
        payloads = []
        rest = src
        while 'page.evaluate("""' in rest:
            start = rest.index('page.evaluate("""')
            js = rest[start:].split('"""', 2)[1]
            payloads.append(js)
            rest = rest[start + len('page.evaluate("""') + len(js) + 3:]
        main = [j for j in payloads if "out.push" in j]
        self.assertEqual(len(main), 1)
        wrapped = "const _main = " + main[0].strip().rstrip(";") + ";"
        with tempfile.NamedTemporaryFile("w", suffix=".js",
                                         delete=False) as f:
            f.write(wrapped)
            name = f.name
        r = subprocess.run(["node", "--check", name],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
