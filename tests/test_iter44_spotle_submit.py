"""Fix-loop iteration 44 tests — Spotle dropdown submit (row text).

Production defect (ANALYSIS43): `_spotle_submit` read the dropdown
row's name ONLY via a child <h3>. The live search component
(chunks_Cexo3-Sk.js) renders each row as a bare
<li class="svelte-tsg0ih"> whose artist name is a TEXT NODE — no <h3>
exists, so the read was always "" and every guess reported
"no exact dropdown match" (14x in run 39374184013), blocking the
solve even with the dropdown open on an exact first row.

Fix: `_spotle_row_name` falls back to `row.inner_text()` (normalized)
when no <h3> child exists. The match stays EXACT — no substring,
no prefix, no Enter. These fakes model the LIVE DOM shape (bare li,
text node, no h3); the iter-41 fake page was updated to match.

Run:  python3 -m unittest tests.test_iter44_spotle_submit -v
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import runner as R


class _FakeH3:
    def __init__(self, text):
        self._text = text

    def inner_text(self):
        return self._text


class _LiveRow:
    """Bare <li> with the name as a text node — the LIVE shape."""

    def __init__(self, page, text, h3_text=None, boom=False):
        self._page = page
        self._text = text
        self._h3 = _FakeH3(h3_text) if h3_text is not None else None
        self._boom = boom
        self.clicked = False

    def query_selector(self, sel):
        if self._boom:
            raise RuntimeError("selector exploded")
        if sel == "h3":
            return self._h3
        return None

    def inner_text(self):
        return self._text

    def scroll_into_view_if_needed(self, timeout=None):
        return None

    def click(self, timeout=None):
        self.clicked = True
        self._page.submitted.append(self._text)
        self._page.cards += 1


class _FakeInput:
    def __init__(self, page):
        self._page = page

    def fill(self, text):
        self._page.typed = text


class _FakePage:
    def __init__(self, rows):
        self.rows = rows
        self.typed = ""
        self.submitted = []
        self.cards = 0

    def wait_for_timeout(self, ms):
        return None

    def query_selector(self, sel):
        if sel.startswith("#search") or "input[name=search]" in sel:
            return _FakeInput(self)
        return None

    def query_selector_all(self, sel):
        if sel == "ul.results li":
            return list(self.rows)
        return []

    def evaluate(self, js):
        if "guess-wrapper" in js and ".length" in js:
            return self.cards
        return None


class TestRowNameRead(unittest.TestCase):
    def test_live_row_text_node(self):
        page = _FakePage([])
        row = _LiveRow(page, "Elton John")
        self.assertEqual(R._spotle_row_name(row), "Elton John")

    def test_whitespace_normalized(self):
        page = _FakePage([])
        row = _LiveRow(page, "  Elton   John\n")
        self.assertEqual(R._spotle_row_name(row), "Elton John")

    def test_h3_preferred_when_present(self):
        page = _FakePage([])
        row = _LiveRow(page, "Row Text", h3_text="H3 Name")
        self.assertEqual(R._spotle_row_name(row), "H3 Name")

    def test_empty_h3_falls_back_to_row_text(self):
        page = _FakePage([])
        row = _LiveRow(page, "Elton John", h3_text="   ")
        self.assertEqual(R._spotle_row_name(row), "Elton John")


class TestSubmitLiveShape(unittest.TestCase):
    def test_submits_exact_live_row(self):
        page = _FakePage([_LiveRow(None, "Elton John")])
        page.rows[0]._page = page
        ok, detail = R._spotle_submit(page, "spotle", "Elton John")
        self.assertTrue(ok, detail)
        self.assertEqual(page.submitted, ["Elton John"])

    def test_clicks_only_the_exact_row(self):
        rows = [_LiveRow(None, "Elton John Tribute"),
                _LiveRow(None, "Elton John"),
                _LiveRow(None, "Elton")]
        page = _FakePage(rows)
        for r in rows:
            r._page = page
        ok, detail = R._spotle_submit(page, "spotle", "Elton John")
        self.assertTrue(ok, detail)
        self.assertEqual(page.submitted, ["Elton John"])
        self.assertFalse(rows[0].clicked)
        self.assertTrue(rows[1].clicked)
        self.assertFalse(rows[2].clicked)

    def test_no_exact_match_never_clicks(self):
        rows = [_LiveRow(None, "Elton John Tribute"),
                _LiveRow(None, "Elton John!")]
        page = _FakePage(rows)
        for r in rows:
            r._page = page
        ok, detail = R._spotle_submit(page, "spotle", "Elton John")
        self.assertFalse(ok)
        self.assertIn("no exact dropdown match", detail)
        self.assertEqual(page.submitted, [])
        self.assertFalse(any(r.clicked for r in rows))

    def test_broken_row_skipped_others_still_match(self):
        rows = [_LiveRow(None, "Boom", boom=True),
                _LiveRow(None, "Elton John")]
        page = _FakePage(rows)
        for r in rows:
            r._page = page
        ok, detail = R._spotle_submit(page, "spotle", "Elton John")
        self.assertTrue(ok, detail)
        self.assertEqual(page.submitted, ["Elton John"])

    def test_whitespace_variant_still_exact(self):
        page = _FakePage([_LiveRow(None, "\n  Elton   John  ")])
        page.rows[0]._page = page
        ok, detail = R._spotle_submit(page, "spotle", "Elton John")
        self.assertTrue(ok, detail)


if __name__ == "__main__":
    unittest.main()
