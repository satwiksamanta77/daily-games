"""Fix-loop iteration 27 tests — Workstream A: Batterup probes +
date-keying + feedback continuation (ANALYSIS24 RC2).

The iter-26 failure, frame- + log-proven: the real mouse-click
lines fired 3/3, but the bot counted every pick as failed
(`selected=False`, `submitted=0`) while frames f0013/f0020/f0030
prove all 3 guesses were submitted with feedback rows. Worse, the
CDN "answer" Alec Burleson was NOT the mystery player on the served
board (his row shows only USA green in f0030), and the log's "same
puzzle, display rollover" claim is REFUTED by the CDN archive
itself: game_date 2026-10-18 = game #973 = Burleson, game_date
2026-10-17 = game #972 = Wyatt Langford — so the served board
(#972 10/17/26) was the previous day's board for target 2026-10-18.

Fix (runner.py, Batterup-scoped only):
- Probe rewrite: dropdown_open is now conjunctive (a visible
  suggestion row with text WHILE the input holds text — the
  `.result-box` container staying mounted empty no longer reads
  open); feedback_tiles counts leaf cells per populated row (3
  rows x 6 = 18); green cells are read from computed background
  colour; row_details carries per-row cell states for the
  continuation filter. The same detector feeds
  _attr_selection_state, so the two probes cannot disagree again.
- Date gate: _s_batterup compares the served board's own header
  against the target date BEFORE playing anything; a served board
  that is not the target day's defers with no video. The CDN answer
  is a seed only — if its row is not all-green, the solver
  continues from feedback.
- Continuation: past the planned 3 guesses, the solver picks
  feedback-driven guesses (green = equal, yellow/other = not
  equal per column) up to the board's 8 rows, until an all-green
  row or rows/pool exhaustion. Summed greens across rows never
  count as a win.
- Pitcher hypothesis ("suggestion index covers batters only"):
  SUPPORTED, not proven — no production guess is burned on a
  pitcher probe; only the exclusion logic is unit-tested.

All tests are synthetic/local: fake pages, monkeypatched helpers,
and the REAL probe JS executed under node against a mock DOM —
no browser, no network, no live play.

Run:  python3 -m unittest tests.test_iter27_batterup -v
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
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
        self.pressed = []
        self.keyboard = self

    def wait_for_timeout(self, ms):
        return None

    def press(self, key):
        self.pressed.append(key)

    def evaluate(self, script, *args):
        self.scripts.append(script)
        return self.result

    def query_selector(self, sel):
        return None

    def query_selector_all(self, sel):
        return []


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
# A minimal DOM sufficient to execute the REAL probe JS: elements
# with className/role/attrs/innerText/value/children/parent,
# closest(), querySelector(All), getBoundingClientRect(), and a
# window.getComputedStyle returning each element's stored style.

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
function leaf(text, bg) {
  return new El({tag: 'div', cls: 'cell', text: text,
    style: {backgroundColor: bg, visibility: 'visible',
      display: 'block', opacity: '1'}});
}
function guessRow(name, cells) {
  const kids = [new El({tag: 'div', cls: 'name', text: name})];
  cells.forEach(c => kids.push(leaf(c[0], c[1])));
  return new El({tag: 'div', cls: 'guess-row', kids: kids,
    text: [name].concat(cells.map(c => c[0])).join('\n')});
}
const GREEN = 'rgb(34, 197, 94)';
const YELLOW = 'rgb(250, 204, 21)';
const WHITE = 'rgb(255, 255, 255)';
// Iter-26 board states, from frames f0011 (pre-submit) / f0030
// (3 submitted rows, input cleared, no dropdown).
function setupBoard(kind) {
  const kids = [];
  if (kind === 'presubmit' || kind === 'selected') {
    const inp = new El({tag: 'input', attrs: {name: 'guess'},
      value: 'Jose Trevino'});
    let box;
    if (kind === 'presubmit') {
      // Suggestion row visible with text (f0011 shape).
      box = new El({tag: 'div', cls: 'result-box',
        text: 'Jose Trevino'});
    } else {
      // Container still mounted but EMPTY after selection.
      box = new El({tag: 'div', cls: 'result-box', text: ''});
    }
    const form = new El({tag: 'form', id: 'player-search',
      kids: [inp, box]});
    kids.push(form);
    if (kind === 'presubmit') {
      // One already-submitted row + an open dropdown.
      kids.push(guessRow('JOSE TREVINO', [
        ['35', YELLOW], ['Reds', WHITE], ['NL Central', WHITE],
        ['USA', GREEN], ['33', WHITE], ['C', WHITE]]));
    }
  } else if (kind === 'submitted3') {
    const inp = new El({tag: 'input', attrs: {name: 'guess'},
      value: ''});
    const box = new El({tag: 'div', cls: 'result-box', text: ''});
    const form = new El({tag: 'form', id: 'player-search',
      kids: [inp, box]});
    kids.push(form);
    kids.push(guessRow('JOSE TREVINO', [
      ['35', YELLOW], ['Reds', WHITE], ['NL Central', WHITE],
      ['USA', GREEN], ['33', WHITE], ['C', WHITE]]));
    kids.push(guessRow('DJ LEMAHIEU', [
      ['26', WHITE], ['Yankees', WHITE], ['AL East', YELLOW],
      ['USA', GREEN], ['38', WHITE], ['2B', WHITE]]));
    kids.push(guessRow('ALEC BURLESON', [
      ['41', WHITE], ['Cardinals', WHITE], ['NL Central', WHITE],
      ['USA', GREEN], ['27', WHITE], ['1B', WHITE]]));
  } else if (kind === 'empty') {
    const inp = new El({tag: 'input', attrs: {name: 'guess'},
      value: ''});
    const box = new El({tag: 'div', cls: 'result-box', text: ''});
    kids.push(new El({tag: 'form', id: 'player-search',
      kids: [inp, box]}));
  }
  ROOT = new El({tag: 'body', kids: kids});
}
"""


def _probe_js():
    page = _CapturePage({})
    R._attr_post_submit_probe(page)
    assert len(page.scripts) == 1
    return page.scripts[0]


def _selection_js():
    page = _CapturePage({"input_value": "", "suggestion_open": False})
    R._attr_selection_state(page, "Jose Trevino")
    assert len(page.scripts) == 1
    return page.scripts[0]


class TestProbeJsSyntax(unittest.TestCase):
    """Every changed page.evaluate payload must parse (node --check);
    adjacent string literals in JS are a SyntaxError (iter-24 RC3)."""

    def test_post_submit_probe_parses(self):
        _node_check(_probe_js())

    def test_selection_state_parses(self):
        _node_check(_selection_js())

    def test_shared_detector_in_both_payloads(self):
        # The two probes must use the SAME conjunctive detector, so
        # they cannot disagree again.
        for js in (_probe_js(), _selection_js()):
            self.assertIn("_attrOpen", js)
            self.assertIn("result-box", js)
            self.assertIn("dropdown_open", _probe_js())

    def test_no_bare_result_box_open_check(self):
        # The iter-26 hole: a bare `.result-box` visibility check
        # with no text / input condition reads open forever.
        js = _probe_js()
        self.assertNotIn("filter(vis).length > 0", js)


class TestProbeAgainstMockDom(unittest.TestCase):
    """The REAL probe JS executed under node against a mock DOM
    shaped like the iter-26 frames."""

    def _run_probe(self, kind):
        js = _MOCK_DOM_PRELUDE + "\nsetupBoard(%r);\n" % kind
        js += "const fn = " + _probe_js() + ";\n"
        js += "console.log(JSON.stringify(fn()));\n"
        return json.loads(_run_node(js))

    def test_submitted3_rows_tiles_dropdown(self):
        # f0030: 3 submitted rows, 18 tiles, input cleared, no
        # dropdown — the exact state iter-26 misread as
        # submitted=0, feedback_tiles=0, dropdown_open=True.
        pr = self._run_probe("submitted3")
        self.assertEqual(pr["populated_rows"], 3)
        self.assertEqual(pr["feedback_tiles"], 18)
        self.assertIs(pr["dropdown_open"], False)
        self.assertTrue(pr["populated"])
        self.assertEqual(len(pr["row_details"]), 3)
        names = [r["name"] for r in pr["row_details"]]
        self.assertEqual(names, ["JOSE TREVINO", "DJ LEMAHIEU",
                                 "ALEC BURLESON"])
        for r in pr["row_details"]:
            self.assertEqual(len(r["cells"]), 6)
        # Green cells: one USA per row = 3.
        self.assertEqual(pr["visible_exact_cells"], 3)
        trevino = pr["row_details"][0]
        self.assertEqual(
            [c["state"] for c in trevino["cells"]],
            ["yellow", "other", "other", "green", "other", "other"])

    def test_empty_board_zero_rows(self):
        pr = self._run_probe("empty")
        self.assertEqual(pr["populated_rows"], 0)
        self.assertEqual(pr["feedback_tiles"], 0)
        self.assertIs(pr["dropdown_open"], False)
        self.assertIs(pr["populated"], False)

    def test_dropdown_visible_with_rows_is_open(self):
        # Presubmit: one submitted row + an open suggestion row
        # with input text -> dropdown_open=True.
        pr = self._run_probe("presubmit")
        self.assertEqual(pr["populated_rows"], 1)
        self.assertIs(pr["dropdown_open"], True)

    def _run_selection(self, kind, name="Jose Trevino"):
        js = _MOCK_DOM_PRELUDE + "\nsetupBoard(%r);\n" % kind
        js += "const fn = " + _selection_js() + ";\n"
        js += "console.log(JSON.stringify(fn(%r)));\n" % name
        return json.loads(_run_node(js))

    def test_selection_open_dropdown_is_not_selected(self):
        # f0011: input holds the name, suggestion row open -> typed,
        # NOT selected.
        st = self._run_selection("presubmit")
        self.assertIs(st["suggestion_open"], True)

    def test_selection_empty_mounted_container_is_selected(self):
        # Post-click: input holds the name, .result-box container
        # still mounted but EMPTY -> closed -> selected. This is
        # the exact state iter-26 misread as selected=False.
        out = self._run_selection("selected")
        self.assertIs(out["suggestion_open"], False)
        self.assertEqual(out["input_value"], "Jose Trevino")
        # And the Python wrapper turns closed + exact input into
        # selected=True.
        page = _CapturePage({"input_value": "Jose Trevino",
                             "suggestion_open": False})
        st = R._attr_selection_state(page, "Jose Trevino")
        self.assertIs(st["selected"], True)


class TestAllGreenRow(unittest.TestCase):
    def test_all_green_row_wins(self):
        pr = {"row_details": [
            {"name": "A", "cells": [{"state": "green"}] * 6}]}
        ok, name = R._batterup_all_green_row(pr)
        self.assertTrue(ok)
        self.assertEqual(name, "A")

    def test_five_green_is_not_a_win(self):
        # Summed-green trap: 5 green + 1 white in one row, and
        # greens spread across rows, must never count.
        pr = {"row_details": [
            {"name": "A", "cells": [{"state": "green"}] * 5 +
             [{"state": "other"}]},
            {"name": "B", "cells": [{"state": "green"}] * 3 +
             [{"state": "other"}] * 3}]}
        ok, _name = R._batterup_all_green_row(pr)
        self.assertFalse(ok)

    def test_no_row_details_is_not_a_win(self):
        self.assertFalse(R._batterup_all_green_row({})[0])
        self.assertFalse(R._batterup_all_green_row(
            {"row_details": []})[0])


# Iter-26 rows (frames f0013/f0020/f0030): states per column
# [#, Team, Div., Born, Age, Pos.].
_ITER26_ROWS = [
    {"name": "Jose Trevino",
     "cells": [{"state": s} for s in
               ["yellow", "other", "other", "green", "other",
                "other"]]},
    {"name": "DJ LeMahieu",
     "cells": [{"state": s} for s in
               ["other", "other", "yellow", "green", "other",
                "other"]]},
    {"name": "Alec Burleson",
     "cells": [{"state": s} for s in
               ["other", "other", "other", "green", "other",
                "other"]]},
]


def _iter26_probe():
    return {"rows": ["JOSE TREVINO", "DJ LEMAHIEU", "ALEC BURLESON"],
            "dropdown_open": False, "populated": True,
            "populated_rows": 3, "feedback_tiles": 18,
            "visible_exact_cells": 3,
            "row_details": [dict(r) for r in _ITER26_ROWS]}


class TestFeedbackContinuation(unittest.TestCase):
    def test_next_candidate_satisfies_feedback(self):
        # Independent re-derivation of the constraints (not via the
        # helper's own filter): green = equal, yellow/other = not
        # equal; Pos. green = overlap, else disjoint.
        page = _CapturePage({})
        players = R._batterup_players_by_name()
        tried = {R._attr_norm_name(n) for n in
                 ("Jose Trevino", "DJ LeMahieu", "Alec Burleson")}
        with mock.patch.object(
                R, "_attr_post_submit_probe",
                return_value=_iter26_probe()):
            nxt = R._batterup_next_from_feedback(page, tried)
        self.assertIsNotNone(nxt)
        self.assertNotIn(R._attr_norm_name(nxt), tried)
        cand = players[R._attr_norm_name(nxt)]
        cand_pos = {str(x).upper()
                    for x in (cand.get("position") or [])}
        self.assertFalse(cand_pos & R._BATTERUP_PITCHER_POSITIONS,
                         f"continuation returned pitcher {nxt}")
        for row in _ITER26_ROWS:
            guessed = players[R._attr_norm_name(row["name"])]
            for idx, cell in enumerate(row["cells"]):
                gv = R._batterup_cell_value(guessed, idx)
                cv = R._batterup_cell_value(cand, idx)
                if gv is None or cv is None:
                    continue
                if idx == 5:
                    if cell["state"] == "green":
                        self.assertTrue(set(cv) & set(gv))
                    else:
                        self.assertFalse(set(cv) & set(gv))
                elif cell["state"] == "green":
                    self.assertEqual(cv, gv)
                else:
                    self.assertNotEqual(cv, gv)

    def test_no_row_details_returns_none(self):
        page = _CapturePage({})
        with mock.patch.object(
                R, "_attr_post_submit_probe",
                return_value={"row_details": []}):
            self.assertIsNone(
                R._batterup_next_from_feedback(page, set()))

    def test_cdn_answer_not_all_green_continues(self):
        # CDN answer played (Burleson, 1-green row) with no win ->
        # the solver continues with a feedback-driven 4th guess,
        # which wins. _strict_win: False for the answer guess,
        # True for the continuation guess.
        page = _CapturePage({})
        R._PLAYED.pop("batterup", None)
        R._REJECTED.pop("batterup", None)
        self.addCleanup(lambda: (R._PLAYED.pop("batterup", None),
                                  R._REJECTED.pop("batterup", None)))
        win_calls = []

        def _win(p, answer=None):
            win_calls.append(answer)
            # Answer guess: initial False + 5s-recheck False, then
            # the continuation guess wins.
            return (len(win_calls) > 2, "you won" if len(win_calls) > 2
                    else "no win signal")

        patches = [
            mock.patch.object(R, "_close_modals"),
            mock.patch.object(R, "_idle_drift"),
            mock.patch.object(R, "_attr_consent_recheck",
                              return_value=None),
            mock.patch.object(R, "_attr_dismiss_dropdown_soft"),
            # Iter 29: Batterup picks via _batterup_click_submit
            # (click = submit, RE Q5) — the old _type_country_guess +
            # selection-state + reselect + verify-submit steps no
            # longer run for Batterup.
            mock.patch.object(
                R, "_batterup_click_submit",
                return_value=(True, "click-submit triple held", {})),
            mock.patch.object(R, "_attr_post_submit_probe",
                              return_value=_iter26_probe()),
            mock.patch.object(R, "_strict_win", side_effect=_win),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        solved, _ev = R.solve_attr_game(
            page, "Alec Burleson", "batterup",
            api_probes=("Jose Trevino", "DJ LeMahieu"))
        self.assertTrue(solved)
        played = R._PLAYED.get("batterup", [])
        self.assertEqual(len(played), 4)
        self.assertEqual(played[:3], ["Jose Trevino", "DJ LeMahieu",
                                      "Alec Burleson"])
        # The 4th guess is feedback-driven, not a repeat.
        self.assertNotIn(played[3], played[:3])

    def test_all_green_row_wins_without_banner(self):
        # _strict_win False throughout, but the board shows an
        # all-green row -> win via the board's own feedback.
        page = _CapturePage({})
        R._PLAYED.pop("batterup", None)
        R._REJECTED.pop("batterup", None)
        self.addCleanup(lambda: (R._PLAYED.pop("batterup", None),
                                  R._REJECTED.pop("batterup", None)))
        all_green = {"rows": ["X"], "dropdown_open": False,
                     "populated": True, "populated_rows": 1,
                     "feedback_tiles": 6, "visible_exact_cells": 6,
                     "row_details": [
                         {"name": "X",
                          "cells": [{"state": "green"}] * 6}]}
        patches = [
            mock.patch.object(R, "_close_modals"),
            mock.patch.object(R, "_idle_drift"),
            mock.patch.object(R, "_attr_consent_recheck",
                              return_value=None),
            mock.patch.object(R, "_attr_dismiss_dropdown_soft"),
            # Iter 29: Batterup picks via _batterup_click_submit
            # (click = submit, RE Q5) — the old _type_country_guess +
            # selection-state + reselect + verify-submit steps no
            # longer run for Batterup.
            mock.patch.object(
                R, "_batterup_click_submit",
                return_value=(True, "click-submit triple held", {})),
            mock.patch.object(R, "_attr_post_submit_probe",
                              return_value=dict(all_green)),
            mock.patch.object(R, "_strict_win",
                              return_value=(False, "no win signal")),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        solved, ev = R.solve_attr_game(
            page, "Alec Burleson", "batterup",
            api_probes=("Jose Trevino", "DJ LeMahieu"))
        self.assertTrue(solved)
        self.assertIn("all-green", ev)

    def test_continuation_caps_at_eight_rows(self):
        # No win ever: the solver plays the planned 3 + 5
        # feedback-driven guesses, then stops honestly at 8 rows.
        page = _CapturePage({})
        R._PLAYED.pop("batterup", None)
        R._REJECTED.pop("batterup", None)
        self.addCleanup(lambda: (R._PLAYED.pop("batterup", None),
                                  R._REJECTED.pop("batterup", None)))
        patches = [
            mock.patch.object(R, "_close_modals"),
            mock.patch.object(R, "_idle_drift"),
            mock.patch.object(R, "_attr_consent_recheck",
                              return_value=None),
            mock.patch.object(R, "_attr_dismiss_dropdown_soft"),
            # Iter 29: Batterup picks via _batterup_click_submit
            # (click = submit, RE Q5) — the old _type_country_guess +
            # selection-state + reselect + verify-submit steps no
            # longer run for Batterup.
            mock.patch.object(
                R, "_batterup_click_submit",
                return_value=(True, "click-submit triple held", {})),
            mock.patch.object(R, "_attr_post_submit_probe",
                              return_value=_iter26_probe()),
            mock.patch.object(R, "_strict_win",
                              return_value=(False, "no win signal")),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        solved, ev = R.solve_attr_game(
            page, "Alec Burleson", "batterup",
            api_probes=("Jose Trevino", "DJ LeMahieu"))
        self.assertFalse(solved)
        played = R._PLAYED.get("batterup", [])
        self.assertEqual(len(played), 8)
        self.assertEqual(len(set(played)), 8)  # no repeats
        self.assertIn("submitted=8", ev)


class TestDateGate(unittest.TestCase):
    def _header_page(self, text):
        class _P(_CapturePage):
            def evaluate(self, script, *args):
                if "document.body.innerText" in script:
                    return text
                return ""
        return _P()

    def _cdn(self):
        return {"game_date": R.A.target_date().isoformat(),
                "game_number": 973,
                "player_name": "Alec Burleson"}

    def test_previous_day_board_defers_without_playing(self):
        # Iter-26 shape: served #972 10/17/26, target = CDN date.
        tgt = R.A.target_date()
        prev = tgt - timedelta(days=1)
        hdr = (f"#972 {prev.month}/{prev.day}/"
               f"{str(prev.year)[2:]}")
        page = self._header_page(hdr)
        R._BATTERUP_CDN["batterup"] = dict(self._cdn())
        self.addCleanup(lambda: R._BATTERUP_CDN.pop("batterup", None))
        msgs = []
        with mock.patch.object(
                R, "_dbg",
                side_effect=lambda gid, m: msgs.append(m)), \
                mock.patch.object(R, "_close_modals"), \
                mock.patch.object(R, "solve_attr_game") as _solve:
            solved, ev = R._s_batterup(page, "Alec Burleson",
                                       "batterup")
        self.assertFalse(solved)
        self.assertIn("defer", ev)
        self.assertIn("#972", ev)
        _solve.assert_not_called()
        # Date-keying evidence logged with all fields.
        gate = [m for m in msgs if "date-keying" in m]
        self.assertTrue(gate)
        self.assertIn("cdn_game_number=973", gate[0])
        self.assertIn("cdn_answer='Alec Burleson'", gate[0])
        self.assertIn("target=", gate[0])

    def test_target_day_board_plays(self):
        tgt = R.A.target_date()
        hdr = (f"#973 {tgt.month}/{tgt.day}/"
               f"{str(tgt.year)[2:]}")
        page = self._header_page(hdr)
        R._BATTERUP_CDN["batterup"] = dict(self._cdn())
        self.addCleanup(lambda: R._BATTERUP_CDN.pop("batterup", None))
        with mock.patch.object(R, "_close_modals"), \
                mock.patch.object(
                    R, "solve_attr_game",
                    return_value=(True, "won")) as _solve, \
                mock.patch.object(R, "_attr_post_submit_probe",
                                  return_value={"row_details": []}):
            solved, ev = R._s_batterup(page, "Alec Burleson",
                                       "batterup")
        self.assertTrue(solved)
        _solve.assert_called_once()
        self.assertIn("batterup date-keying", ev)
        self.assertIn("cdn_game_number=973", ev)

    def test_missing_header_defers(self):
        page = self._header_page("Batter Up! Guess the player")
        R._BATTERUP_CDN["batterup"] = dict(self._cdn())
        self.addCleanup(lambda: R._BATTERUP_CDN.pop("batterup", None))
        with mock.patch.object(R, "_close_modals"), \
                mock.patch.object(R, "solve_attr_game") as _solve:
            solved, ev = R._s_batterup(page, "Alec Burleson",
                                       "batterup")
        self.assertFalse(solved)
        self.assertIn("defer", ev)
        _solve.assert_not_called()


class TestPitcherHypothesis(unittest.TestCase):
    def test_pitcher_positions_excluded_from_pool(self):
        # Hypothesis status: SUPPORTED, not proven — covered by
        # unit tests on the exclusion logic only; no production
        # guess is ever burned on a pitcher probe.
        self.assertIn("SP", R._BATTERUP_PITCHER_POSITIONS)
        self.assertIn("RP", R._BATTERUP_PITCHER_POSITIONS)
        pool = R._attr_pool("batterup")
        players = R._batterup_players_by_name()
        self.assertGreater(len(pool), 100)
        for name in pool:
            pos = {str(x).upper()
                   for x in players[R._attr_norm_name(name)].get(
                       "position") or []}
            self.assertFalse(pos & R._BATTERUP_PITCHER_POSITIONS,
                             f"pitcher {name} ({pos}) in pool")

    def test_continuation_never_returns_pitcher(self):
        page = _CapturePage({})
        tried = set()
        with mock.patch.object(
                R, "_attr_post_submit_probe",
                return_value=_iter26_probe()):
            nxt = R._batterup_next_from_feedback(page, tried)
        self.assertIsNotNone(nxt)
        players = R._batterup_players_by_name()
        pos = {str(x).upper()
               for x in players[R._attr_norm_name(nxt)].get(
                   "position") or []}
        self.assertFalse(pos & R._BATTERUP_PITCHER_POSITIONS)


if __name__ == "__main__":
    unittest.main()
