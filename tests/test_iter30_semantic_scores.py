"""Fix-loop iteration 30 tests — Semantle/Contexto score probe (ANALYSIS28 BUG 2).

The confirmation-run failure, frame- + log-proven: the probe claimed
scores "not exposed in DOM" (score=None for every guess) while frames
showed every guess's Similarity/rank visibly rendered. Root cause,
confirmed from the sites' frontend source: the shared probe used one
generic selector — 'table tr, li, [class*=guess i], [class*=row i],
[class*=entry i]' capped at the first 80 matches — which matches
NEITHER site's real guess rows. Semantle is an Angular app: guesses
render as a <div> grid inside <app-guesses>, each row a
div.dark:border-neutral-300 (no table, no li, no guess/row/entry
class). Contexto renders each guess as div.row-wrapper > div.row >
span.row-word + rank span inside div.guess-history.

Fix: the probe scopes to each site's real guess list (Contexto:
.guess-history .row-wrapper with .row-word + rank span; Semantle:
app-guesses div[class*="border-neutral-300"] minus the rounded-md
recent-guess card) and _semantic_parse_dom_rows extracts the score
from the scoped row records.
"""
import inspect
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import runner as R

# Confirmation-run row shapes (from ANALYSIS28 frames + frontend source).
SEMT_ROWS = [
    {"src": "semantle", "text": "1\nnutrient\n-0.67\n9999+"},
    {"src": "semantle", "text": "2\ncombat\n6.38\n9999+"},
    {"src": "semantle", "text": "3\nago\n12.63\nGetting close"},
    {"src": "semantle", "text": "4\nlog\n2.29\n9999+"},
    {"src": "semantle", "text": "5\ncry\n100.00\n1"},
]

CTX_ROWS = [
    {"src": "contexto", "word": "outstanding", "score": "1344"},
    {"src": "contexto", "word": "church", "score": "1513"},
    {"src": "contexto", "word": "equal", "score": "434"},
    {"src": "contexto", "word": "provider", "score": "2469"},
    {"src": "contexto", "word": "section", "score": "1"},
]


class TestSemanticParseDomRows(unittest.TestCase):
    def test_semantle_similarities(self):
        # Exact confirmation-run values from ANALYSIS28 frames.
        for word, want in [("nutrient", "-0.67"), ("combat", "6.38"),
                           ("ago", "12.63"), ("log", "2.29"),
                           ("cry", "100.00")]:
            got, probe = R._semantic_parse_dom_rows(SEMT_ROWS, word)
            self.assertEqual(got, want, f"{word}: {probe}")

    def test_semantle_case_insensitive(self):
        got, _ = R._semantic_parse_dom_rows(SEMT_ROWS, "NUTRIENT")
        self.assertEqual(got, "-0.67")

    def test_semantle_word_boundary(self):
        # "log" must not match a row whose word merely contains it.
        rows = [{"src": "semantle", "text": "1\ncatalog\n3.14\n9999+"},
                {"src": "semantle", "text": "2\nlog\n2.29\n9999+"}]
        got, _ = R._semantic_parse_dom_rows(rows, "log")
        self.assertEqual(got, "2.29")
        got, _ = R._semantic_parse_dom_rows(rows, "catalog")
        self.assertEqual(got, "3.14")

    def test_semantle_missing_word_none(self):
        got, probe = R._semantic_parse_dom_rows(SEMT_ROWS, "zebra")
        self.assertIsNone(got)
        self.assertEqual(probe["rows_scanned"], len(SEMT_ROWS))

    def test_semantle_no_decimal_none(self):
        rows = [{"src": "semantle", "text": "1\nnutrient\n9999+"}]
        got, _ = R._semantic_parse_dom_rows(rows, "nutrient")
        self.assertIsNone(got)

    def test_contexto_ranks(self):
        # Exact confirmation-run values from ANALYSIS28 frames.
        for word, want in [("outstanding", "1344"), ("church", "1513"),
                           ("equal", "434"), ("provider", "2469"),
                           ("section", "1")]:
            got, probe = R._semantic_parse_dom_rows(CTX_ROWS, word)
            self.assertEqual(got, want, f"{word}: {probe}")

    def test_contexto_exact_word_match(self):
        # "equal" must not match a row-word of "unequal".
        rows = [{"src": "contexto", "word": "unequal", "score": "999"},
                {"src": "contexto", "word": "equal", "score": "434"}]
        got, _ = R._semantic_parse_dom_rows(rows, "equal")
        self.assertEqual(got, "434")

    def test_contexto_missing_word_none(self):
        got, _ = R._semantic_parse_dom_rows(CTX_ROWS, "zebra")
        self.assertIsNone(got)

    def test_empty_rows_none(self):
        got, _ = R._semantic_parse_dom_rows([], "cry")
        self.assertIsNone(got)


class TestProbeJsSelectors(unittest.TestCase):
    """The evaluate payload must use the scoped per-site selectors —
    the old generic 'table tr, li, [class*=guess...]' selector is the
    bug; a regression to it must fail loudly here."""

    def test_scoped_selectors_present(self):
        src = inspect.getsource(R._semantic_dom_scores)
        self.assertIn(".guess-history .row-wrapper", src)
        self.assertIn(".row-word", src)
        self.assertIn('app-guesses div[class*="border-neutral-300"]', src)
        self.assertIn("rounded-md", src)

    def test_old_generic_selector_gone(self):
        # Check the evaluate PAYLOAD only (the docstring quotes the old
        # selector for the record, which is fine).
        src = inspect.getsource(R._semantic_dom_scores)
        start = src.index('page.evaluate("""')
        js = src[start:].split('"""', 2)[1]
        self.assertNotIn("table tr, li,", js)
        self.assertNotIn("[class*=guess", js)
        self.assertNotIn("slice(0, 80)", js)

    def test_evaluate_js_parses(self):
        # The JS payload inside _semantic_dom_scores must be valid JS.
        import subprocess
        import tempfile
        src = inspect.getsource(R._semantic_dom_scores)
        start = src.index("page.evaluate(\"\"\"")
        js = src[start:].split('"""', 2)[1]
        # Strip the leading "() => {" wrapper is not valid standalone;
        # wrap it as an expression statement for node --check.
        wrapped = "const _probe = " + js.strip().rstrip(";") + ";"
        with tempfile.NamedTemporaryFile("w", suffix=".js",
                                         delete=False) as f:
            f.write(wrapped)
            name = f.name
        r = subprocess.run(["node", "--check", name],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)


# --- Mock DOM (JS): runs the REAL probe JS against faithful trees ---
# Contexto tree mirrors the bundle: .guess-history > .row-wrapper >
# [.outer-bar, .row > [span.row-word, span rank]], plus a decoy
# .row-wrapper OUTSIDE .guess-history (must not be picked).
# Semantle tree mirrors the bundle: app-guesses > div.row
# (class dark:border-neutral-300) plus a recent-guess card sharing
# the class but adding rounded-md (must be excluded).
_MOCK_DOM_JS = r"""
class El {
  constructor(tag, cls, text) {
    this.tagName = tag.toUpperCase();
    this.className = cls || '';
    this.textContent = text || '';
    this.innerText = text || '';
    this.children = [];
    this.parent = null;
  }
  append(c) { c.parent = this; this.children.push(c); return c; }
  _matchSimple(sel) {
    sel = sel.trim();
    // tag + [attr*="val"]
    let m = sel.match(/^([a-zA-Z-]+)\[([a-zA-Z-]+)\*="([^"]*)"\]$/);
    if (m) {
      if (this.tagName.toLowerCase() !== m[1].toLowerCase()) return false;
      const attr = m[2] === 'class' ? 'className' : m[2];
      const v = (this[attr] || '').toString();
      return v.includes(m[3]);
    }
    if (sel.startsWith('.')) return (' ' + this.className + ' ').includes(' ' + sel.slice(1) + ' ') || this.className.split(' ').includes(sel.slice(1));
    if (sel === 'span') return this.tagName === 'SPAN';
    return this.tagName.toLowerCase() === sel.toLowerCase();
  }
  _matchChain(parts, el) {
    // parts: right-to-left selector tokens, '>' = child combinator
    let cur = el, i = parts.length - 1;
    if (!cur._matchSimple(parts[i])) return false;
    i--;
    while (i >= 0) {
      if (parts[i] === '>') {
        i--;
        cur = cur.parent;
        if (!cur || !cur._matchSimple(parts[i])) return false;
        i--;
      } else {
        let found = false, anc = cur.parent;
        while (anc) { if (anc._matchSimple(parts[i])) { found = true; cur = anc; break; } anc = anc.parent; }
        if (!found) return false;
        i--;
      }
    }
    return true;
  }
  querySelectorAll(sel) {
    const parts = sel.replace(/\s*>\s*/g, ' > ').split(/\s+/).filter(Boolean);
    const out = [];
    const walk = (el) => { for (const c of el.children) { if (this._matchChain(parts, c)) out.push(c); walk(c); } };
    walk(this);
    return out;
  }
  querySelector(sel) {
    const all = this.querySelectorAll(sel);
    return all.length ? all[0] : null;
  }
}
const doc = new El('html', '', '');
const body = doc.append(new El('body', '', ''));
const gh = body.append(new El('div', 'guess-history', ''));
function ctxRow(word, rank) {
  const w = gh.append(new El('div', 'row-wrapper', ''));
  w.append(new El('div', 'outer-bar', ''));
  const row = w.append(new El('div', 'row', ''));
  row.append(new El('span', 'row-word', word));
  row.append(new El('span', '', String(rank)));
  return w;
}
ctxRow('outstanding', 1344);
ctxRow('church', 1513);
// Decoy: a row-wrapper OUTSIDE .guess-history must not be picked.
const decoy = body.append(new El('div', 'row-wrapper', ''));
const decoyRow = decoy.append(new El('div', 'row', ''));
decoyRow.append(new El('span', 'row-word', 'decoyword'));
decoyRow.append(new El('span', '', '9999'));
// Semantle: app-guesses with one ngFor row + one rounded-md card.
const app = body.append(new El('app-guesses', '', ''));
const semRow = app.append(new El('div', 'dark:border-neutral-300', '1\nnutrient\n-0.67\n9999+'));
const semCard = app.append(new El('div', 'dark:border-neutral-300 rounded-md', '1\nnutrient\n-0.67\n9999+'));
const document = doc;
"""


class TestProbeJsEndToEnd(unittest.TestCase):
    """Execute the REAL probe JS (extracted from _semantic_dom_scores)
    under node against the mock DOMs, then parse with the real
    Python parser. This is the end-to-end proof the scoped selectors
    return the sites' real rows."""

    def _run_probe(self):
        import json
        import subprocess
        import tempfile
        src = inspect.getsource(R._semantic_dom_scores)
        start = src.index('page.evaluate("""')
        js = src[start:].split('"""', 2)[1]
        body = (_MOCK_DOM_JS + "\nconst rows = (" + js.strip() + ")();\n"
                "console.log(JSON.stringify(rows));\n")
        with tempfile.NamedTemporaryFile("w", suffix=".js",
                                         delete=False) as f:
            f.write(body)
            name = f.name
        r = subprocess.run(["node", name], capture_output=True, text=True,
                           timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout.strip())

    def test_probe_returns_scoped_rows(self):
        rows = self._run_probe()
        by_src = {}
        for r in rows:
            by_src.setdefault(r["src"], []).append(r)
        # Contexto: exactly the 2 in-history rows (decoy excluded).
        self.assertEqual(len(by_src.get("contexto", [])), 2)
        self.assertEqual([r["word"] for r in by_src["contexto"]],
                         ["outstanding", "church"])
        self.assertEqual([r["score"] for r in by_src["contexto"]],
                         ["1344", "1513"])
        # Semantle: exactly the 1 ngFor row (rounded-md card excluded).
        self.assertEqual(len(by_src.get("semantle", [])), 1)
        self.assertIn("nutrient", by_src["semantle"][0]["text"])

    def test_probe_rows_parse_to_scores(self):
        rows = self._run_probe()
        got, _ = R._semantic_parse_dom_rows(rows, "outstanding")
        self.assertEqual(got, "1344")
        got, _ = R._semantic_parse_dom_rows(rows, "nutrient")
        self.assertEqual(got, "-0.67")
        got, _ = R._semantic_parse_dom_rows(rows, "decoyword")
        self.assertIsNone(got)


if __name__ == "__main__":
    unittest.main()
