"""Fix-loop iteration 10 regression tests.

Rerun #9 (run 39035133842, sha b78ccc7) finished 18/19 jobs success:
globle + framed cleared by the iter9 fixes. Only worldle still failed,
on country guess 1 with ZERO guesses submitted:

  "pick failed (exception: ElementHandle.click: Timeout 3000ms exceeded)"

Primary evidence (video-worldle/gameplay_1080.mp4, frames at t=2s/11s/13s):
Worldle's Didomi "We value your privacy / Accept all" consent notice
covered the whole page from ~1s in and never went away. The guess-input
click timed out because the notice intercepts the click point.

Root cause: the notice lives inside #didomi-host's shadow DOM.
Neither _dismiss_dialogs (light-DOM dialog selector) nor the consent
watcher (plain querySelectorAll, no shadow piercing) could ever see its
Accept button, and Didomi has no close/X affordance for the old
close-only sweep to click. Fix: pierce open shadow roots in both the
watcher (quordle_parity.consent_watch_js) and a new synchronous
_consent_accept pass (runner._dismiss_consent, called from
_dismiss_dialogs), gated on a CMP container so game buttons are never
clicked.

Run:  python3 -m unittest tests.test_iter10_fixes -v
All tests are synthetic/local (node fixture DOM / source inspection /
no browser, no network).
"""
import inspect
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent.parent
sys.path.insert(0, str(HERE))

import quordle_parity as QP  # noqa: E402
import runner  # noqa: E402


class TestConsentAcceptJs(unittest.TestCase):
    """runner._CONSENT_ACCEPT_JS on a fixture DOM (node simulation):
    shadow-DOM Didomi accept is clicked; game buttons are never clicked.
    """

    _HEADER = r"""
class El {
  constructor(tag, attrs, text, parent) {
    this.tagName = tag; this._attrs = attrs || {};
    this.innerText = text || ''; this.parentElement = parent || null;
    this.offsetParent = {}; this.children = [];
    this.shadowRoot = null; this.clicked = false; this._root = null;
    if (parent) parent.children.push(this);
  }
  get id() { return this._attrs['id'] || ''; }
  get className() { return this._attrs['class'] || ''; }
  getAttribute(k) { return this._attrs[k] || ''; }
  getBoundingClientRect() { return {width: 120, height: 40}; }
  click() { this.clicked = true; }
  getRootNode() { return this._root; }
  closest(sel) {
    const parts = sel.split(',').map(s => s.trim());
    let n = this;
    while (n) {
      for (const p of parts) { if (matchPart(n, p)) return n; }
      n = n.parentElement;
    }
    return null;
  }
}
function matchPart(el, p) {
  let m = p.match(/^\[class\*=(.+?)( i)?\]$/);
  if (m) {
    const want = m[1].replace(/^['"]|['"]$/g, '');
    const cls = el.className || '';
    return m[2] ? cls.toLowerCase().includes(want.toLowerCase())
                : cls.includes(want);
  }
  m = p.match(/^\[id\*=(.+?)( i)?\]$/);
  if (m) {
    const want = m[1].replace(/^['"]|['"]$/g, '');
    const id = el.id || '';
    return m[2] ? id.toLowerCase().includes(want.toLowerCase())
                : id.includes(want);
  }
  m = p.match(/^\[role=(.+?)\]$/);
  if (m) return (el.getAttribute('role') || '') === m[1].replace(/^['"]|['"]$/g, '');
  return false;
}
function qsa(list, sel) {
  if (sel === '*') return list.slice();
  if (sel === 'iframe') return [];
  return list.filter(e =>
    e.tagName === 'BUTTON' || (e.getAttribute('role') || '') === 'button' ||
    e.tagName === 'A' || e.id === 'didomi-notice-agree-button');
}
class ShadowRootFake {
  constructor(host, children) { this.host = host; this.children = children; }
  querySelectorAll(sel) { return qsa(this.children, sel); }
}
"""

    _FOOTER = r"""
const __res = FRAGMENT;
console.log(JSON.stringify({res: __res, flags: __flagsFn()}));
"""

    def _run(self, body_js):
        if not shutil.which("node"):
            self.skipTest("node not available")
        js = self._HEADER + body_js + self._FOOTER.replace(
            "FRAGMENT", runner._CONSENT_ACCEPT_JS)
        with tempfile.NamedTemporaryFile(
                "w", suffix=".js", delete=False) as f:
            f.write(js)
            path = f.name
        try:
            out = subprocess.run(["node", path], capture_output=True,
                                 text=True, timeout=30)
        finally:
            Path(path).unlink(missing_ok=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout.strip())

    def test_didomi_shadow_accept_clicked_game_button_untouched(self):
        body = r"""
const lightEls = [];
const bodyEl = new El('BODY', {}, '', null); lightEls.push(bodyEl);
const host = new El('DIV', {'id': 'didomi-host'}, '', bodyEl); lightEls.push(host);
const main = new El('MAIN', {}, '', bodyEl); lightEls.push(main);
const gameBtn = new El('BUTTON', {}, 'Continue', main); lightEls.push(gameBtn);
const shadowKids = [];
const acceptBtn = new El('BUTTON', {'id': 'didomi-notice-agree-button'},
  'Accept all', null); shadowKids.push(acceptBtn);
const note = new El('P', {}, 'We value your privacy', null); shadowKids.push(note);
const shadow = new ShadowRootFake(host, shadowKids);
host.shadowRoot = shadow;
const document = { querySelectorAll: (sel) => qsa(lightEls, sel) };
for (const e of lightEls) e._root = document;
for (const e of shadowKids) e._root = shadow;
const __flagsFn = () => ({accept: acceptBtn.clicked, game: gameBtn.clicked});
"""
        got = self._run(body)
        self.assertEqual(got["res"], "consent-accept:didomi-notice-agree-button")
        self.assertTrue(got["flags"]["accept"])
        self.assertFalse(got["flags"]["game"])

    def test_shadow_accept_via_host_fallback(self):
        # Plain 'Accept all' button in a shadow root whose OWN id/class
        # match nothing CMP: only the shadow host (#didomi-host) proves
        # it is a CMP button. closest() finds nothing; getRootNode().host
        # must carry the gate.
        body = r"""
const lightEls = [];
const bodyEl = new El('BODY', {}, '', null); lightEls.push(bodyEl);
const host = new El('DIV', {'id': 'didomi-host'}, '', bodyEl); lightEls.push(host);
const shadowKids = [];
const wrap = new El('DIV', {'class': 'popup'}, '', null); shadowKids.push(wrap);
const acceptBtn = new El('BUTTON', {}, 'Accept all', wrap); shadowKids.push(acceptBtn);
const shadow = new ShadowRootFake(host, shadowKids);
host.shadowRoot = shadow;
const document = { querySelectorAll: (sel) => qsa(lightEls, sel) };
for (const e of lightEls) e._root = document;
for (const e of shadowKids) e._root = shadow;
const __flagsFn = () => ({accept: acceptBtn.clicked});
"""
        got = self._run(body)
        self.assertEqual(got["res"], "consent-accept:accept all")
        self.assertTrue(got["flags"]["accept"])

    def test_game_continue_without_cmp_not_clicked(self):
        body = r"""
const lightEls = [];
const bodyEl = new El('BODY', {}, '', null); lightEls.push(bodyEl);
const main = new El('MAIN', {}, '', bodyEl); lightEls.push(main);
const gameBtn = new El('BUTTON', {}, 'Continue', main); lightEls.push(gameBtn);
const document = { querySelectorAll: (sel) => qsa(lightEls, sel) };
for (const e of lightEls) e._root = document;
const __flagsFn = () => ({game: gameBtn.clicked});
"""
        got = self._run(body)
        self.assertIsNone(got["res"])
        self.assertFalse(got["flags"]["game"])

    def test_light_dom_cookie_banner_accept_clicked(self):
        body = r"""
const lightEls = [];
const bodyEl = new El('BODY', {}, '', null); lightEls.push(bodyEl);
const banner = new El('DIV', {'class': 'cookie-banner'}, '', bodyEl); lightEls.push(banner);
const acceptBtn = new El('BUTTON', {}, 'Accept all', banner); lightEls.push(acceptBtn);
const document = { querySelectorAll: (sel) => qsa(lightEls, sel) };
for (const e of lightEls) e._root = document;
const __flagsFn = () => ({accept: acceptBtn.clicked});
"""
        got = self._run(body)
        self.assertEqual(got["res"], "consent-accept:accept all")
        self.assertTrue(got["flags"]["accept"])

    def test_hidden_cmp_button_skipped(self):
        body = r"""
const lightEls = [];
const bodyEl = new El('BODY', {}, '', null); lightEls.push(bodyEl);
const banner = new El('DIV', {'class': 'cookie-banner'}, '', bodyEl); lightEls.push(banner);
const acceptBtn = new El('BUTTON', {}, 'Accept all', banner); lightEls.push(acceptBtn);
acceptBtn.offsetParent = null;
const document = { querySelectorAll: (sel) => qsa(lightEls, sel) };
for (const e of lightEls) e._root = document;
const __flagsFn = () => ({accept: acceptBtn.clicked});
"""
        got = self._run(body)
        self.assertIsNone(got["res"])
        self.assertFalse(got["flags"]["accept"])


class TestConsentWatcherShadowDom(unittest.TestCase):
    """The continuous watcher must pierce shadow roots and know Didomi,
    while keeping its text + overlay gates (never click game buttons)."""

    def test_watcher_pierces_shadow_roots(self):
        js = QP.consent_watch_js()
        self.assertIn("shadowRoot", js)
        self.assertIn("didomi-notice-agree-button", js)
        self.assertIn("[id*=didomi]", js)
        self.assertIn("getRootNode", js)

    def test_watcher_keeps_gates(self):
        js = QP.consent_watch_js()
        self.assertIn("TXT.has(t)", js)
        self.assertIn("inOverlay(el)", js)
        self.assertIn("done.has(el)", js)


class TestDismissDialogsCallsConsent(unittest.TestCase):
    """_dismiss_dialogs must run the CMP pass each round, before the
    dialog-selector break that a shadow-DOM notice never satisfies."""

    def test_dismiss_dialogs_calls_consent_pass(self):
        src = inspect.getsource(runner._dismiss_dialogs)
        self.assertIn("_dismiss_consent", src)

    def test_dismiss_consent_uses_accept_js(self):
        src = inspect.getsource(runner._dismiss_consent)
        self.assertIn("_CONSENT_ACCEPT_JS", src)
        self.assertIn("wait_for_timeout", src)

    def test_accept_js_constant_covers_shadow_and_didomi(self):
        js = runner._CONSENT_ACCEPT_JS
        self.assertIn("shadowRoot", js)
        self.assertIn("didomi-notice-agree-button", js)
        self.assertIn("getRootNode", js)


if __name__ == "__main__":
    unittest.main()
