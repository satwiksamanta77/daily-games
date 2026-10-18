"""Fix-loop iteration 31 tests — Semantle score probe diagnostics (ANALYSIS29 BUG 2).

Iteration-30 run: Semantle returned score=None for all 4 guesses while the
Similarity column was visibly rendered, and the caller DISCARDED the probe
diagnostics, so the run carried zero evidence of WHICH stage failed
(selector matched 0 rows vs rows filtered vs parser found no decimal vs
evaluate threw vs probe ran before rows rendered).

Fix: (a) _semantic_dom_scores now returns per-stage diagnostics —
raw selector counts BEFORE the rounded-md/text filters (raw_semantle /
raw_contexto) vs kept rows, plus attempts and error; (b) one bounded
re-read when the first attempt finds no row for this word at all
(matched_row None — the guess row may render just after the first read
on the Angular POST round-trip); a matched row with no decimal is NOT
retried. (c) _semantic_guess_accepted logs the diagnostics on EVERY
probe (gid-gated _dbg line) instead of dropping them; the selector
itself was verified against the LIVE Semantle bundle template
(main-BDG63Q2B.js): ngFor rows are divs with class
" dark:border-neutral-300" and NO rounded-md; the recent-guess card
carries BOTH classes, so the rounded-md exclusion is correct.
(d) The parser accepts U+2212 MINUS SIGN in the similarity and
normalizes it to ASCII "-" (float() downstream rejects U+2212).
"""
import inspect
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import runner as R

SEM_ROW = {"src": "semantle", "text": "1\nnutrient\n-0.67\n9999+"}
CTX_ROW = {"src": "contexto", "word": "outstanding", "score": "1344"}
DIAG = {"semantle_raw": 2, "semantle_kept": 1,
        "contexto_raw": 0, "contexto_kept": 0}


class FakePage:
    """Distinguishes the three evaluate calls by JS content:
    body innerText, the main probe (out.push), the diag probe
    (semantle_raw). main_rows_seq is popped per main-probe call so
    a retry test can return [] first, then rows."""

    def __init__(self, main_rows_seq, diag=None, raise_on=None,
                 body_text=""):
        self.main_rows_seq = list(main_rows_seq)
        self.diag = dict(diag if diag is not None else DIAG)
        self.raise_on = set(raise_on or ())
        self.body_text = body_text
        self.waits = []
        self.calls = 0

    def wait_for_timeout(self, ms):
        self.waits.append(ms)

    def evaluate(self, js):
        self.calls += 1
        if "semantle_raw" in js:
            if "diag" in self.raise_on:
                raise RuntimeError("diag boom")
            return dict(self.diag)
        if "out.push" in js:
            if "main" in self.raise_on:
                raise RuntimeError("main boom")
            if self.main_rows_seq:
                return self.main_rows_seq.pop(0)
            return []
        if "document.body.innerText" in js:
            return self.body_text
        raise AssertionError(f"unexpected evaluate JS: {js[:80]!r}")


class TestParserUnicodeMinus(unittest.TestCase):
    def test_u2212_minus_normalized(self):
        # Semantle may render the similarity with U+2212 MINUS SIGN;
        # the parser must return ASCII "-0.67" (float() rejects U+2212).
        rows = [{"src": "semantle", "text": "1\nnutrient\n\u22120.67\n9999+"}]
        got, probe = R._semantic_parse_dom_rows(rows, "nutrient")
        self.assertEqual(got, "-0.67")
        self.assertEqual(float(got), -0.67)

    def test_matched_row_no_decimal_is_none(self):
        rows = [{"src": "semantle", "text": "1\nnutrient\n9999+"}]
        got, probe = R._semantic_parse_dom_rows(rows, "nutrient")
        self.assertIsNone(got)
        self.assertIsNotNone(probe["matched_row"])


class TestDomScoresDiagnostics(unittest.TestCase):
    def test_success_returns_diagnostics(self):
        pg = FakePage([[SEM_ROW, CTX_ROW]])
        got, probe = R._semantic_dom_scores(pg, "nutrient")
        self.assertEqual(got, "-0.67")
        self.assertEqual(probe["rows_scanned"], 2)
        self.assertEqual(probe["raw_semantle"], 2)
        self.assertEqual(probe["kept_semantle"], 1)
        self.assertEqual(probe["attempts"], 1)
        self.assertIsNone(probe["error"])
        self.assertEqual(pg.waits, [])

    def test_retry_when_no_row_for_word(self):
        # First attempt: no rows at all (late render). Second: rows.
        pg = FakePage([[], [SEM_ROW]])
        got, probe = R._semantic_dom_scores(pg, "nutrient")
        self.assertEqual(got, "-0.67")
        self.assertEqual(probe["attempts"], 2)
        self.assertEqual(pg.waits, [1200])

    def test_no_retry_when_row_matched_without_decimal(self):
        pg = FakePage([[{"src": "semantle", "text": "1\nnutrient\n9999+"}]])
        got, probe = R._semantic_dom_scores(pg, "nutrient")
        self.assertIsNone(got)
        self.assertEqual(probe["attempts"], 1)
        self.assertEqual(pg.waits, [])

    def test_no_retry_when_word_absent_but_rows_present(self):
        # Rows exist but none for this word: matched_row None, so one
        # bounded re-read (the row may render late), then give up.
        pg = FakePage([[{"src": "semantle", "text": "1\ncombat\n6.38\n9999+"}],
                       [{"src": "semantle", "text": "1\ncombat\n6.38\n9999+"}]])
        got, probe = R._semantic_dom_scores(pg, "nutrient")
        self.assertIsNone(got)
        self.assertEqual(probe["attempts"], 2)
        self.assertEqual(pg.waits, [1200])

    def test_evaluate_throw_sets_error(self):
        pg = FakePage([], raise_on={"main"})
        got, probe = R._semantic_dom_scores(pg, "nutrient")
        self.assertIsNone(got)
        self.assertIn("main boom", probe["error"])
        self.assertEqual(probe["attempts"], 1)

    def test_diag_throw_does_not_break_probe(self):
        pg = FakePage([[SEM_ROW]], raise_on={"diag"})
        got, probe = R._semantic_dom_scores(pg, "nutrient")
        self.assertEqual(got, "-0.67")
        self.assertEqual(probe["raw_semantle"], 0)
        self.assertEqual(probe["attempts"], 1)


class TestCallerLogsDiagnostics(unittest.TestCase):
    def test_diagnostics_logged_every_probe(self):
        pg = FakePage([[SEM_ROW]], body_text="nutrient -0.67 far")
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
        line = diag[0]
        for key in ("rows_scanned=", "raw_semantle=", "kept_semantle=",
                    "raw_contexto=", "kept_contexto=", "matched_row=",
                    "error=", "attempts="):
            self.assertIn(key, line, line)
        self.assertIn("score='-0.67'", line)

    def test_caller_exception_logged_not_swallowed(self):
        pg = FakePage([], body_text="nutrient is here")
        orig_scores = R._semantic_dom_scores
        R._semantic_dom_scores = lambda p, w: (_ for _ in ()).throw(
            RuntimeError("boom"))
        seen = []
        orig_dbg = R._dbg
        R._dbg = lambda gid, msg: seen.append(f"[{gid}] {msg}")
        try:
            ok, score = R._semantic_guess_accepted(
                pg, "nutrient", "before text", gid="semantle")
        finally:
            R._semantic_dom_scores = orig_scores
            R._dbg = orig_dbg
        # Word is in the page text and was not in before_txt: accepted
        # without a readable score — but the caller error is logged.
        self.assertTrue(ok)
        self.assertIsNone(score)
        diag = [m for m in seen if "score probe:" in m]
        self.assertEqual(len(diag), 1, seen)
        self.assertIn("caller: boom", diag[0])

    def test_no_gid_no_log_line(self):
        pg = FakePage([[SEM_ROW]], body_text="nutrient -0.67 far")
        seen = []
        orig_dbg = R._dbg
        R._dbg = lambda gid, msg: seen.append(msg)
        try:
            ok, score = R._semantic_guess_accepted(pg, "nutrient",
                                                   "before text")
        finally:
            R._dbg = orig_dbg
        self.assertTrue(ok)
        self.assertEqual(score, "-0.67")
        self.assertEqual([m for m in seen if "score probe:" in m], [])


class TestDiagJsParses(unittest.TestCase):
    def test_diag_js_parses(self):
        # The diagnostics evaluate payload must be valid JS too.
        import subprocess
        import tempfile
        src = inspect.getsource(R._semantic_dom_scores)
        payloads = []
        rest = src
        while 'page.evaluate("""' in rest:
            start = rest.index('page.evaluate("""')
            js = rest[start:].split('"""', 2)[1]
            payloads.append(js)
            rest = rest[start + len('page.evaluate("""') + len(js) + 3:]
        diag = [j for j in payloads if "semantle_raw" in j]
        self.assertEqual(len(diag), 1)
        wrapped = "const _diag = " + diag[0].strip().rstrip(";") + ";"
        with tempfile.NamedTemporaryFile("w", suffix=".js",
                                         delete=False) as f:
            f.write(wrapped)
            name = f.name
        r = subprocess.run(["node", "--check", name],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
