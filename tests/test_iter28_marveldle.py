"""Fix-loop iteration 28 tests — Marveldle win detector (ANALYSIS25 RC1)
+ Framed first-segment leading trim.

The iter-27 failure, frame- + log-proven: the bot WON Marveldle
on-screen (Glob row all 6 cells green + portrait revealed,
shot_win.png / gameplay f0072.jpg) but reported
visible_exact_cells=0 and NO win signal, so solved=false and no
video shipped. Root cause: the iter-27 probe rewrite changed
exact-cell counting from "any descendant whose class matches
/exact|correct|green/…" (iter-26: exact=2/2/8 -> win) to "leaf
cells only, own computed-bg hue" — but Marveldle's green lives on
the TILE CONTAINER (a non-leaf ancestor), so every leaf read
'other' -> exact=0 -> the all-attribute fallback (exact_n >= 5)
never fired, and Marveldle shows no win text for _strict_win.

Fix (runner.py, _attr_post_submit_probe): stateOf walks from each
leaf cell up to (excluding) its row, returning the FIRST verdict
found at any level (nearest wins). Leaf-coloured boards
(Batterup) still read the leaf; container-coloured boards
(Marveldle) read the tile container. The walk never crosses the
row boundary, so a green row container or page chrome can never
mark a cell green. A fully transparent node is no verdict (walk
continues up); an opaque non-feedback colour is 'other';
exhausting the walk with no verdict is 'unknown' (the continuation
filter skips 'unknown' cells — same as the iter-27 leaf-only
read, so no new constraints are invented).

Also in this iteration: Framed's final showed ~26s of empty board
before the first typing (ANALYSIS25 Fix 5) because Framed assembly
runs with autocrop OFF (autocrop's non-uniform cuts would shift
every mode boundary — the misplaced-cards root cause). Fix:
_trim_first_bound trims ONLY the first segment's start to 1s
before the first recorded action; later bounds are untouched, so
no mode boundary can shift.

All tests are synthetic/local: fake pages, monkeypatched helpers,
and the REAL probe JS executed under node against a mock DOM —
no browser, no network, no live play.

Run:  python3 -m unittest tests.test_iter28_marveldle -v
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

import runner as R


# ------------------------------------------------------- capture helpers

class _CapturePage:
    """Fake page recording evaluate() scripts; returns a canned value."""

    def __init__(self, result=""):
        self.result = result
        self.scripts = []

    def evaluate(self, script, *args):
        self.scripts.append(script)
        return self.result


class _DummyPage:
    """Minimal page stand-in for the mocked solver paths."""

    def __init__(self):
        self.pressed = []
        self.keyboard = self

    def wait_for_timeout(self, ms):
        return None

    def press(self, key):
        self.pressed.append(key)

    def query_selector(self, sel):
        return None

    def evaluate(self, script, *args):
        return ""


def _node_check(js):
    """node --check on one JS payload (parse only). Skips w/o node."""
    if not shutil.which("node"):
        raise unittest.SkipTest("node not available")
    with tempfile.NamedTemporaryFile("w", suffix=".js",
                                     delete=False) as fh:
        fh.write("const fn = " + js + ";\n")
        path = fh.name
    try:
        out = subprocess.run(["node", "--check", path],
                             capture_output=True, text=True, timeout=15)
    finally:
        Path(path).unlink(missing_ok=True)
    assert out.returncode == 0, out.stderr


def _run_node(js_body):
    """Run js_body under node; returns stdout. Skips w/o node."""
    if not shutil.which("node"):
        raise unittest.SkipTest("node not available")
    with tempfile.NamedTemporaryFile("w", suffix=".js",
                                     delete=False) as fh:
        fh.write(js_body)
        path = fh.name
    try:
        out = subprocess.run(["node", path], capture_output=True,
                             text=True, timeout=15)
    finally:
        Path(path).unlink(missing_ok=True)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


# ------------------------------------------------------- mock DOM (JS)
# Same minimal DOM as the iter-27 probe tests, plus a parentElement
# getter (the real DOM property the ancestor walk uses).

_MOCK_DOM_PRELUDE = r"""
class El {
  constructor(o) {
    this.tagName = (o.tag || 'div').toUpperCase();
    this.id = o.id || '';
    this.className = o.cls || '';
    this.role = o.role || '';
    this.attrs = o.attrs || {};
    this.innerText = o.text || '';
    this.value = o.value || '';
    this.children = [];
    this.parent = null;
    this.offsetParent = o.hasOwnProperty('visible')
      ? (o.visible ? {} : null) : {};
    this._style = o.style || {backgroundColor: 'rgba(0, 0, 0, 0)',
      visibility: 'visible', display: 'block', opacity: '1'};
    this._rect = o.rect || {width: 100, height: 20};
    (o.kids || []).forEach(k => this.append(k));
  }
  get parentElement() { return this.parent; }
  append(k) { k.parent = this; this.children.push(k); return k; }
  getBoundingClientRect() { return this._rect; }
  closest(sel) {
    let el = this;
    while (el) { if (matchesList(el, sel)) return el; el = el.parent; }
    return null;
  }
  querySelectorAll(sel) {
    const out = [];
    const walk = (el) => { for (const c of el.children) {
      if (matchesList(c, sel)) out.push(c); walk(c); } };
    walk(this);
    return out;
  }
  querySelector(sel) {
    const a = this.querySelectorAll(sel);
    return a.length ? a[0] : null;
  }
}
function splitTop(s, sep) {
  const out = []; let depth = 0; let cur = '';
  for (const ch of s) {
    if (ch === '[') depth++;
    if (ch === ']') depth--;
    if (ch === sep && depth === 0) { out.push(cur); cur = ''; }
    else cur += ch;
  }
  out.push(cur);
  return out;
}
function matchesOne(el, sel) {
  sel = sel.trim();
  if (!sel) return false;
  const parts = splitTop(sel, ' ');
  if (parts.length > 1) {
    const last = parts[parts.length - 1];
    if (!matchesSimple(el, last)) return false;
    let anc = el.parent; let pi = parts.length - 2;
    while (anc && pi >= 0) {
      if (matchesSimple(anc, parts[pi])) pi--;
      anc = anc.parent;
    }
    return pi < 0;
  }
  return matchesSimple(el, sel);
}
function matchesList(el, sel) {
  return splitTop(sel, ',').some(p => matchesOne(el, p));
}
function matchesSimple(el, sel) {
  sel = sel.trim();
  let notSel = null;
  const notIdx = sel.indexOf(':not(');
  if (notIdx >= 0) {
    notSel = sel.slice(notIdx + 5, sel.length - 1);
    sel = sel.slice(0, notIdx);
  }
  if (sel === '*') return notSel === null || !matchesSimple(el, notSel);
  let rest = sel;
  const tagM = rest.match(/^[a-zA-Z][a-zA-Z0-9]*/);
  if (tagM) {
    if (el.tagName !== tagM[0].toUpperCase()) return false;
    rest = rest.slice(tagM[0].length);
  }
  while (rest) {
    if (rest.startsWith('.')) {
      const m = rest.match(/^\.([A-Za-z0-9_-]+)/);
      if (!m) return false;
      if ((' ' + el.className + ' ').indexOf(' ' + m[1] + ' ') < 0)
        return false;
      rest = rest.slice(m[0].length);
    } else if (rest.startsWith('#')) {
      const m = rest.match(/^#([A-Za-z0-9_-]+)/);
      if (!m || el.id !== m[1]) return false;
      rest = rest.slice(m[0].length);
    } else if (rest.startsWith('[')) {
      const end = rest.indexOf(']');
      if (end < 0) return false;
      const inner = rest.slice(1, end);
      if (inner.indexOf('=') < 0) {
        const attr = inner.trim();
        const actual = attr === 'class' ? el.className
          : attr === 'role' ? el.role : el.attrs[attr];
        if (actual === undefined || actual === null) return false;
        rest = rest.slice(end + 1);
        continue;
      }
      const m = inner.match(/^([A-Za-z-]+)(\*?=)"?([^"]*?)"?\s*(i)?$/);
      if (!m) return false;
      const attr = m[1]; const op = m[2]; let val = m[3];
      const ci = !!m[4];
      let actual;
      if (attr === 'class') actual = el.className;
      else if (attr === 'role') actual = el.role;
      else actual = el.attrs[attr];
      if (actual === undefined || actual === null) return false;
      if (ci) { actual = String(actual).toLowerCase();
        val = val.toLowerCase(); }
      else actual = String(actual);
      if (op === '=') { if (actual !== val) return false; }
      else { if (actual.indexOf(val) < 0) return false; }
      rest = rest.slice(end + 1);
    } else return false;
  }
  if (notSel !== null && matchesSimple(el, notSel)) return false;
  return true;
}
const window = {
  getComputedStyle: (el) => el._style,
};
let ROOT = null;
const document = {
  querySelectorAll(sel) {
    const out = [];
    if (ROOT && matchesList(ROOT, sel)) out.push(ROOT);
    if (ROOT) out.push(...ROOT.querySelectorAll(sel));
    return out;
  },
  querySelector(sel) {
    const a = this.querySelectorAll(sel);
    return a.length ? a[0] : null;
  },
};
const GREEN = 'rgb(34, 197, 94)';
const YELLOW = 'rgb(250, 204, 21)';
const WHITE = 'rgb(255, 255, 255)';
const TRANSPARENT = 'rgba(0, 0, 0, 0)';
function styleOf(bg) {
  return {backgroundColor: bg, visibility: 'visible',
    display: 'block', opacity: '1'};
}
function nameLeaf(text) {
  return new El({tag: 'div', cls: 'name', text: text});
}
// Marveldle shape: colour on the TILE CONTAINER (non-leaf ancestor),
// text leaf inside it transparent/unclassed. cells = [text, bg|cls]
// where bg is the container background and cls the container class.
function containerRow(name, cells) {
  const kids = [nameLeaf(name)];
  cells.forEach(c => {
    const leaf = new El({tag: 'div', cls: 'cell', text: c[0],
      style: styleOf(TRANSPARENT)});
    const tile = new El({tag: 'div', cls: c[2] || 'tile',
      style: styleOf(c[1]), kids: [leaf]});
    kids.push(tile);
  });
  return new El({tag: 'div', cls: 'guess-row', kids: kids,
    text: [name].concat(cells.map(c => c[0])).join('\n')});
}
// Batterup shape: colour on the LEAF cell itself.
function leafRow(name, cells) {
  const kids = [nameLeaf(name)];
  cells.forEach(c => kids.push(new El({tag: 'div', cls: 'cell',
    text: c[0], style: styleOf(c[1])})));
  return new El({tag: 'div', cls: 'guess-row', kids: kids,
    text: [name].concat(cells.map(c => c[0])).join('\n')});
}
function setupBoard(kind) {
  const inp = new El({tag: 'input', attrs: {name: 'guess'}, value: ''});
  const box = new El({tag: 'div', cls: 'result-box', text: ''});
  const form = new El({tag: 'form', id: 'player-search',
    kids: [inp, box]});
  const kids = [form];
  if (kind === 'marveldle_all_green') {
    kids.push(containerRow('Iron Man', [
      ['Male', GREEN, ''], ['Superhero', GREEN, ''],
      ['Human', GREEN, ''], ['Strength', GREEN, ''],
      ['Earth', GREEN, ''], ['2001', GREEN, '']]));
  } else if (kind === 'marveldle_mixed') {
    kids.push(containerRow('Iron Man', [
      ['Male', GREEN, ''], ['Superhero', YELLOW, ''],
      ['Human', WHITE, ''], ['Strength', GREEN, ''],
      ['Earth', TRANSPARENT, ''], ['2001', GREEN, '']]));
  } else if (kind === 'batterup_leaf') {
    kids.push(leafRow('JOSE TREVINO', [
      ['35', YELLOW], ['Reds', WHITE], ['NL Central', WHITE],
      ['USA', GREEN], ['33', WHITE], ['C', WHITE]]));
  } else if (kind === 'green_row_container') {
    // The ROW itself is green; containers + leaves transparent.
    // The walk must NOT cross the row boundary: exact must stay 0
    // and every cell must read 'unknown', not 'green'.
    const row = containerRow('Iron Man', [
      ['Male', TRANSPARENT, ''], ['Superhero', TRANSPARENT, ''],
      ['Human', TRANSPARENT, ''], ['Strength', TRANSPARENT, ''],
      ['Earth', TRANSPARENT, ''], ['2001', TRANSPARENT, '']]);
    row._style = styleOf(GREEN);
    kids.push(row);
  } else if (kind === 'nearest_wins') {
    // Leaf has its OWN opaque white bg inside a green container:
    // the nearest verdict (the leaf's) wins -> 'other'.
    const leaf = new El({tag: 'div', cls: 'cell', text: 'Male',
      style: styleOf(WHITE)});
    const tile = new El({tag: 'div', cls: 'tile',
      style: styleOf(GREEN), kids: [leaf]});
    const row = new El({tag: 'div', cls: 'guess-row',
      kids: [nameLeaf('Iron Man'), tile],
      text: 'Iron Man\nMale'});
    kids.push(row);
  } else if (kind === 'class_on_container') {
    // Container class carries the verdict ('correct'), no bg.
    kids.push(containerRow('Iron Man', [
      ['Male', TRANSPARENT, 'tile correct']]));
  } else if (kind === 'yellow_container') {
    kids.push(containerRow('Iron Man', [
      ['Male', YELLOW, '']]));
  } else if (kind === 'transparent_all') {
    kids.push(containerRow('Iron Man', [
      ['Male', TRANSPARENT, '']]));
  }
  ROOT = new El({tag: 'body', kids: kids});
}
"""


def _probe_js():
    page = _CapturePage({})
    R._attr_post_submit_probe(page)
    assert len(page.scripts) == 1
    return page.scripts[0]


class TestProbeJsSyntax(unittest.TestCase):
    """The rewritten probe payload must parse (node --check);
    adjacent string literals in JS are a SyntaxError (iter-24 RC3)."""

    def test_post_submit_probe_parses(self):
        _node_check(_probe_js())

    def test_stateof_walks_ancestors(self):
        js = _probe_js()
        self.assertIn("parentElement", js)
        self.assertIn("rowEl", js)


class TestProbeAgainstMockDom(unittest.TestCase):
    """The REAL probe JS executed under node against a mock DOM
    shaped like the iter-27 Marveldle/Batterup boards."""

    def _run_probe(self, kind):
        js = _MOCK_DOM_PRELUDE + "\nsetupBoard(%r);\n" % kind
        js += "const fn = " + _probe_js() + ";\n"
        js += "console.log(JSON.stringify(fn()));\n"
        return json.loads(_run_node(js))

    def test_marveldle_all_green_row_counts(self):
        # ANALYSIS25 RC1 exact shape: all 6 cells green on the tile
        # CONTAINERS, leaves transparent. Iter-27 read exact=0 here.
        pr = self._run_probe("marveldle_all_green")
        self.assertEqual(pr["populated_rows"], 1)
        self.assertEqual(pr["feedback_tiles"], 6)
        self.assertEqual(pr["visible_exact_cells"], 6)
        self.assertEqual(len(pr["row_details"]), 1)
        self.assertEqual(
            [c["state"] for c in pr["row_details"][0]["cells"]],
            ["green"] * 6)

    def test_marveldle_mixed_row(self):
        pr = self._run_probe("marveldle_mixed")
        self.assertEqual(pr["visible_exact_cells"], 3)
        self.assertEqual(
            [c["state"] for c in pr["row_details"][0]["cells"]],
            ["green", "yellow", "other", "green", "unknown", "green"])

    def test_batterup_leaf_colours_unchanged(self):
        # Leaf-coloured board: the walk must still read the leaf
        # first — iter-27 behaviour preserved exactly.
        pr = self._run_probe("batterup_leaf")
        self.assertEqual(pr["populated_rows"], 1)
        self.assertEqual(pr["visible_exact_cells"], 1)
        self.assertEqual(
            [c["state"] for c in pr["row_details"][0]["cells"]],
            ["yellow", "other", "other", "green", "other", "other"])

    def test_green_row_container_does_not_leak(self):
        # A green ROW container must not mark its cells green: the
        # walk stops at the row boundary.
        pr = self._run_probe("green_row_container")
        self.assertEqual(pr["visible_exact_cells"], 0)
        self.assertEqual(
            [c["state"] for c in pr["row_details"][0]["cells"]],
            ["unknown"] * 6)

    def test_nearest_verdict_wins(self):
        # Leaf's own opaque white beats the green container: the
        # most specific node's styling wins.
        pr = self._run_probe("nearest_wins")
        self.assertEqual(pr["visible_exact_cells"], 0)
        self.assertEqual(
            [c["state"] for c in pr["row_details"][0]["cells"]],
            ["other"])

    def test_class_keyword_on_container(self):
        # Container class 'tile correct' -> green even with no bg.
        pr = self._run_probe("class_on_container")
        self.assertEqual(pr["visible_exact_cells"], 1)
        self.assertEqual(
            [c["state"] for c in pr["row_details"][0]["cells"]],
            ["green"])

    def test_yellow_container(self):
        pr = self._run_probe("yellow_container")
        self.assertEqual(pr["visible_exact_cells"], 0)
        self.assertEqual(
            [c["state"] for c in pr["row_details"][0]["cells"]],
            ["yellow"])

    def test_transparent_all_is_unknown(self):
        # No colour anywhere up to the row -> 'unknown' (the
        # continuation filter skips these; no invented constraint).
        pr = self._run_probe("transparent_all")
        self.assertEqual(pr["visible_exact_cells"], 0)
        self.assertEqual(
            [c["state"] for c in pr["row_details"][0]["cells"]],
            ["unknown"])


class _PatchDbgMixin:
    def setUp(self):
        self._dbg_patch = mock.patch.object(R, "_dbg", lambda gid, msg: None)
        self._dbg_patch.start()
        self.addCleanup(self._dbg_patch.stop)


class TestSolveAttrGameMarveldleFallback(_PatchDbgMixin, unittest.TestCase):
    """Wiring guard: with NO win text, a board probe reporting 6
    visible exact cells in a populated row must win via the
    all-attribute fallback (exact_n >= 5). The probe behaviour
    itself is proven by the mock-DOM tests above; this guards the
    solver path that consumes it."""

    _BOARD_PROBE = {"rows": ["Iron Man | ..."], "dropdown_open": False,
                    "populated": True, "populated_rows": 1,
                    "feedback_tiles": 6, "visible_exact_cells": 6,
                    "row_details": []}

    def setUp(self):
        super().setUp()
        R._PLAYED.pop("marveldle", None)
        R._REJECTED.pop("marveldle", None)
        self.addCleanup(lambda: (R._PLAYED.pop("marveldle", None),
                                  R._REJECTED.pop("marveldle", None)))

    def test_all_attribute_fallback_wins_without_win_text(self):
        with mock.patch.object(R, "_close_modals"), \
                mock.patch.object(R, "_idle_drift"), \
                mock.patch.object(R, "_attr_pool", return_value=[]), \
                mock.patch.object(R, "_attr_consent_recheck"), \
                mock.patch.object(R, "_attr_dismiss_dropdown_soft"), \
                mock.patch.object(R, "_type_country_guess",
                                  return_value=(True, "picked")), \
                mock.patch.object(R, "_settle"), \
                mock.patch.object(R, "_attr_verify_submitted",
                                  return_value=(True, {})), \
                mock.patch.object(R, "_attr_post_submit_probe",
                                  return_value=dict(self._BOARD_PROBE)), \
                mock.patch.object(R, "_strict_win",
                                  return_value=(False, "no win text")):
            solved, evidence = R.solve_attr_game(
                _DummyPage(), "Glob", "marveldle")
        self.assertTrue(solved)
        self.assertIn("all-attribute win (6 visible exact/green cells",
                      evidence)

    def test_below_threshold_does_not_win(self):
        probe = dict(self._BOARD_PROBE, visible_exact_cells=4,
                     feedback_tiles=4)
        with mock.patch.object(R, "_close_modals"), \
                mock.patch.object(R, "_idle_drift"), \
                mock.patch.object(R, "_attr_pool", return_value=[]), \
                mock.patch.object(R, "_attr_consent_recheck"), \
                mock.patch.object(R, "_attr_dismiss_dropdown_soft"), \
                mock.patch.object(R, "_type_country_guess",
                                  return_value=(True, "picked")), \
                mock.patch.object(R, "_settle"), \
                mock.patch.object(R, "_attr_verify_submitted",
                                  return_value=(True, {})), \
                mock.patch.object(R, "_attr_post_submit_probe",
                                  return_value=probe), \
                mock.patch.object(R, "_strict_win",
                                  return_value=(False, "no win text")):
            solved, _evidence = R.solve_attr_game(
                _DummyPage(), "Glob", "marveldle")
        self.assertFalse(solved)


class TestFramedTrimFirstBound(unittest.TestCase):
    """_framed_trim_first_bound: only the first segment's start
    moves; later bounds are byte-identical, so no mode boundary
    can shift (the misplaced-cards root cause stays closed)."""

    def test_trims_first_segment_to_anchor_minus_1s(self):
        bounds = [(0.0, 60.0), (60.0, 120.0), (120.0, 180.0)]
        got = R._framed_trim_first_bound(bounds, 31.0)
        self.assertEqual(got, [(30.0, 60.0), (60.0, 120.0),
                               (120.0, 180.0)])
        # Input not mutated.
        self.assertEqual(bounds, [(0.0, 60.0), (60.0, 120.0),
                                  (120.0, 180.0)])

    def test_no_trim_without_anchor(self):
        bounds = [(0.0, 60.0), (60.0, 120.0)]
        self.assertEqual(R._framed_trim_first_bound(bounds, None), bounds)

    def test_no_trim_anchor_before_first_segment(self):
        bounds = [(40.0, 90.0), (90.0, 150.0)]
        self.assertEqual(R._framed_trim_first_bound(bounds, 30.0), bounds)

    def test_no_trim_anchor_after_first_segment(self):
        bounds = [(0.0, 60.0), (60.0, 120.0)]
        self.assertEqual(R._framed_trim_first_bound(bounds, 90.0), bounds)

    def test_no_trim_tiny_cut(self):
        # Cut of 0.4s is not worth re-cutting.
        bounds = [(30.0, 90.0), (90.0, 150.0)]
        self.assertEqual(R._framed_trim_first_bound(bounds, 31.4), bounds)

    def test_no_trim_short_remainder(self):
        # Cutting would leave < 2s of first-segment footage.
        bounds = [(0.0, 31.5), (31.5, 90.0)]
        self.assertEqual(R._framed_trim_first_bound(bounds, 31.0), bounds)

    def test_empty_bounds(self):
        self.assertEqual(R._framed_trim_first_bound([], 30.0), [])
