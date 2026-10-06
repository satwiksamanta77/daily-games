"""Nerdle daily video — all modes solved in one run (Quordle-style).

Modes (9, from all-wordsolverx-workers/nerdle-answers):
  classic(8), micro(5), mini(6), midi(7), maxi(10),
  minibi(2x6), quad(4x8), speed(2x8), instant(8-hint)
Answers: nerdle-answers worker (D1) -> direct nerdlegame md5 URLs fallback.
Video: Playwright record 1280x720, BROWSER_TZ (default Asia/Tokyo for next-day),
  FAKE_DATE_ISO time-travel init script, Wordle-parity assembly
  (recap 5s + hints 10s + gameplay + 3x analysis 8s + teaser 5s),
  uniform music, SEO like Wordle, 9pm IST trigger via Cloudflare worker.
Skip waffle (updates much later, separate timing).
"""
import asyncio
import json
import os
import re
import sys
import random
import time
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from playwright.async_api import async_playwright
import requests

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "youtube" / "quordle-video" / "quor-dle-video"))
try:
    import quordle_parity as QP
    from quordle_thumbnail import generate_quordle_thumbnail as _gen_thumb
    QP_AVAILABLE = True
except Exception as e:
    print(f"[parity] import failed, continuing raw: {e}")
    QP_AVAILABLE = False

try:
    from youtube_upload import upload_to_youtube, fetch_word_analysis
    YOUTUBE_AVAILABLE = True
except ImportError:
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent.parent / "youtube" / "quordle-video" / "quor-dle-video"))
        from youtube_upload import upload_to_youtube, fetch_word_analysis
        YOUTUBE_AVAILABLE = True
    except ImportError:
        YOUTUBE_AVAILABLE = False
        print("YouTube upload module not available.")

try:
    from moviepy.editor import VideoFileClip, ImageClip, concatenate_videoclips, AudioFileClip
    import moviepy.audio.fx.all as afx
    MOVIEPY_AVAILABLE = True
except Exception:
    MOVIEPY_AVAILABLE = False

HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"

# All 9 nerdle modes — every equation solved in the SAME run
NERDLE_MODES = [
    {"id": "classic", "name": "Classic", "url": "https://www.nerdlegame.com/game", "len": 8},
    {"id": "micro", "name": "Micro", "url": "https://micro.nerdlegame.com/", "len": 5},
    {"id": "mini", "name": "Mini", "url": "https://mini.nerdlegame.com/", "len": 6},
    {"id": "midi", "name": "Midi", "url": "https://midi.nerdlegame.com/", "len": 7},
    {"id": "maxi", "name": "Maxi", "url": "https://maxi.nerdlegame.com/", "len": 10},
    {"id": "minibi", "name": "MiniBi", "url": "https://mini.bi.nerdlegame.com/", "len": 6, "boards": 2},
    {"id": "quad", "name": "Quad", "url": "https://quad.nerdlegame.com/", "len": 8, "boards": 4},
    {"id": "speed", "name": "Speed", "url": "https://speed.nerdlegame.com/", "len": 8, "boards": 2},
    {"id": "instant", "name": "Instant", "url": "https://instant.nerdlegame.com/", "len": 8},
]

NERDLE_START = datetime(2022, 1, 20)


def _visible_now():
    """UTC 'now' honoring FAKE_DATE_ISO (wall time in TZ_OFFSET_MINUTES zone)."""
    fake = os.environ.get("FAKE_DATE_ISO", "").strip()
    if fake:
        try:
            dt = datetime.fromisoformat(fake)
            try:
                off = int(os.environ.get("TZ_OFFSET_MINUTES", "540"))
            except ValueError:
                off = 540
            return dt - timedelta(minutes=off)
        except ValueError:
            pass
    return datetime.utcnow()


def _target_date():
    """Site-visible puzzle day = worker's getVisibleDateKey rule:
    rolls over 15:00 UTC: before -> UTC date, after -> UTC date + 1."""
    now_utc = _visible_now()
    vis = now_utc.date() + timedelta(days=1 if now_utc.hour >= 15 else 0)
    return datetime(vis.year, vis.month, vis.day)


def classic_puzzle_number(d):
    return (d.date() - NERDLE_START.date()).days + 1


_OPS = set('+-*/')
_LETTER_MAP = (('a', '+'), ('m', '-'), ('d', '/'), ('t', '*'), ('e', '='))


def _js_num_str(x):
    if x != x:
        return 'NaN'
    if float(x).is_integer():
        return str(int(x))
    return repr(float(x))


def _eval_simple(expr):
    """JS-float arithmetic eval for + - * / ( ) (left-assoc, same as site's parser).

    Maxi's answer files use superscript exponents (`2²-1+6-8=1`), so ²/³ are
    treated as postfix square/cube. Without this the site's own ground-truth
    equation failed validation and the run fell back to a wrong worker guess.
    """
    if not re.fullmatch(r'[0-9+\-*/().²³]+', expr) or expr in ('',):
        return None
    tokens = re.findall(r'\d+\.?\d*|[+\-*/()²³]', expr)
    if ''.join(tokens) != expr:
        return None
    pos = 0

    def parse_expr():
        nonlocal pos
        v = parse_term()
        while pos < len(tokens) and tokens[pos] in '+-':
            op = tokens[pos]; pos += 1
            r = parse_term()
            v = v + r if op == '+' else v - r
        return v

    def parse_term():
        nonlocal pos
        v = parse_atom()
        while pos < len(tokens) and tokens[pos] in '*/':
            op = tokens[pos]; pos += 1
            r = parse_atom()
            v = v * r if op == '*' else v / r
        return v

    def parse_atom():
        nonlocal pos
        if tokens[pos] == '(':
            pos += 1
            v = parse_expr()
            if pos < len(tokens) and tokens[pos] == ')':
                pos += 1
        else:
            v = float(tokens[pos]); pos += 1
        # postfix superscript exponents: 2² -> 4, 2³ -> 8
        while pos < len(tokens) and tokens[pos] in '²³':
            v = v * v if tokens[pos] == '²' else v * v * v
            pos += 1
        return v

    try:
        v = parse_expr()
        return v if pos == len(tokens) else None
    except Exception:
        return None


def _unique_perms(s):
    if len(s) < 2:
        return [s]
    out = []
    for i, c in enumerate(s):
        if s.index(c) != i:
            continue
        for p in _unique_perms(s[:i] + s[i + 1:]):
            out.append(c + p)
    return out


def decode_instant_hint(hint):
    """Port of the site's Tt(): '<8-char row>_<1-based green pos>' -> equation.

    Permutations of the row, green position fixed, must be a valid equation
    (one '=', numeric RHS, no adjacent operators, LHS starts/ends with a digit),
    eval matches. Returns '' if no solution."""
    try:
        pre, post = hint.split('_', 1)
        row = pre
        for a, b in _LETTER_MAP:
            row = row.replace(a, b)
        gi = int(post) - 1
    except Exception:
        return ''
    if not (0 <= gi < len(row)):
        return ''
    pick = ''
    for s in _unique_perms(row):
        if s[gi] != row[gi] or s[0] == '=' or s[-1] == '=':
            continue
        parts = s.split('=')
        if len(parts) != 2:
            continue
        lhs, rhs = parts
        if not re.fullmatch(r'\d+(\.\d+)?', rhs or ''):
            continue
        if any(s[i] in _OPS and s[i + 1] in _OPS for i in range(len(s) - 1)):
            continue
        if not lhs or not lhs[0].isdigit() or not lhs[-1].isdigit():
            continue
        v = _eval_simple(lhs)
        if v is None:
            continue
        if _js_num_str(v) == rhs or _js_num_str(v) == _js_num_str(float(rhs)):
            pick = s  # site keeps the LAST match
    return pick


def fetch_nerdle_answers(target=None):
    """All-mode answers from the nerdle-answers worker (same source as site)."""
    from datetime import timedelta as _td
    target = target or _target_date()
    date_key = target.strftime("%Y-%m-%d")
    for u in (f"https://nerdle-answers.nerdleapi.workers.dev/{date_key}?v=2",
              "https://nerdle-answers.nerdleapi.workers.dev/today?v=2"):
        try:
            r = requests.get(u, timeout=20, headers={"User-Agent": "WordSolverX Video"})
            if not r.ok:
                continue
            j = r.json()
            data = (j.get("data") or j) if isinstance(j, dict) else {}
            if isinstance(data, dict) and data.get("modes"):
                m = {}
                for md in data["modes"]:
                    eqs = []
                    for a in (md.get("answers") or []):
                        if not a.get("answer"):
                            continue
                        if a.get("type") == "equation":
                            eqs.append(a["answer"])
                        elif a.get("type") == "hint":
                            dec = decode_instant_hint(a["answer"])
                            print(f"[nerdle] instant hint {a['answer']} -> {dec!r}")
                            if dec:
                                eqs.append(dec)
                    if eqs:
                        m[md["id"]] = eqs
                print(f"[nerdle] answers via {u}: " +
                      ", ".join(f"{k}={len(v)}eq" for k, v in m.items()))
                return date_key, m
        except Exception:
            continue
    print("[nerdle] worker unavailable, blind-play fallback (video still records)")
    return date_key, {}


async def block_ads(route):
    try:
        u = route.request.url
        if any(x in u for x in ("doubleclick", "googlesyndication", "adnxs", "pubmatic", "criteo", "amazon-adsystem", "googletagmanager", "google-analytics", "clarity.ms", "amxrtb")):
            return await route.abort()
    except Exception:
        pass
    return await route.continue_()


FILLERS = {5: "1+2=3", 6: "12/4=3", 7: "1+23=24", 8: "12+34=46", 10: "1+23+45=69"}

WIN_JS = """
({cols, boards}) => {
  const t = document.body.innerText || '';
  if (/you won|won in \\d|great job/i.test(t)) return true;
  const green = (c) => (c.className || '').includes('398874')
                        || (c.className || '').includes('66FF66');
  let full = 0;
  for (const r of document.querySelectorAll('div')) {
    if (r.children.length === cols &&
        Array.from(r.children).every(green)) full++;
  }
  return full >= boards;
}
"""

SOLVER_API = "https://solver.nerdle.workers.dev"
SOLVER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Content-Type": "application/json",
    "Origin": "https://wordsolverx.com",
    "Referer": "https://wordsolverx.com/nerdle-solver",
}
# solver-worker mode ids (length/charset presets) per nerdle mode
SOLVER_MODE = {"classic": "classic", "micro": "micro", "mini": "mini", "midi": "midi",
               "maxi": "maxi", "quad": "classic", "speed": "classic",
               "minibi": "mini", "instant": "classic"}


def solver_suggest(mode_id, history, tries=3):
    """POST to the frontend solver worker. history=[(eq, fb[0|1|2...])].
    Returns ranked equation list ('eq') or [] on failure (retries with backoff)."""
    payload = {"mode": SOLVER_MODE.get(mode_id, "classic"),
               "guesses": [{"equation": eq, "feedback": fb} for eq, fb in history]}
    for i in range(tries):
        try:
            r = requests.post(f"{SOLVER_API}/solve", json=payload,
                              headers=SOLVER_HEADERS, timeout=30)
            if r.ok:
                j = r.json()
                if j.get("success"):
                    sug = [s["eq"] for s in (j.get("suggestions") or []) if s.get("eq")]
                    if sug:
                        return sug
                    print(f"[solver] {mode_id} empty suggestions: {str(j)[:140]}")
                else:
                    print(f"[solver] {mode_id} success=false: {str(j)[:140]}")
            else:
                print(f"[solver] {mode_id} http {r.status_code}: {r.text[:140]}")
        except Exception as e:
            print(f"[solver] {mode_id} worker failed: {e}")
        time.sleep(1.0 + i)
    return []


READ_JS = """
(cols) => {
  const C = {'rgb(57, 136, 116)': 2, 'rgb(130, 4, 88)': 1, 'rgb(22, 24, 3)': 0};
  const M = {multiply: '*', multipy: '*', plus: '+', minus: '-',
             divide: '/', equals: '='};
  const sym = (c) => {
    let t = (c.innerText || '').trim();
    if (!t) t = (c.textContent || '').trim();
    return (t in M) ? M[t] : t;
  };
  const grids = [];
  for (const g of document.querySelectorAll('div.pb-grid')) {
    const rows = [], texts = [];
    for (const r of g.children) {
      const cells = [];
      for (const c of r.children) {
        if ((c.className || '').toString().includes('keyboard-cell')) cells.push(c);
      }
      if (cells.length !== cols) continue;
      const fb = [];
      for (const c of cells) {
        const b = getComputedStyle(c).backgroundColor;
        fb.push((b in C) ? C[b] : -1);
      }
      rows.push(fb);
      texts.push(cells.map(sym).join(''));
    }
    grids.push({rows: rows, texts: texts});
  }
  return grids;
}
"""


async def read_state(page, cols, tries=4):
    for _ in range(tries):
        try:
            g = await page.evaluate(READ_JS, cols)
            if g:
                return g
        except Exception:
            pass
        await page.wait_for_timeout(400)
    return []


def _done(r):
    return all(v >= 0 for v in r)


def eval_count(g):
    return sum(1 for r in g["rows"] if _done(r))


def grid_solved(g):
    return any(all(v == 2 for v in r) for r in g["rows"])


def is_valid_eq(s, cols):
    if not s or len(s) != cols or s.count('=') != 1:
        return False
    lhs, rhs = s.split('=')
    if not re.fullmatch(r'\d+(\.\d+)?', rhs or '') or not lhs:
        return False
    if any(lhs[i] in _OPS and lhs[i + 1] in _OPS for i in range(len(lhs) - 1)):
        return False
    first = next((c for c in lhs if c not in '()'), '')
    last = next((c for c in reversed(lhs) if c not in '()'), '')
    if not first.isdigit() or not last.isdigit():
        return False
    v = _eval_simple(lhs)
    if v is None:
        return False
    return _js_num_str(v) == _js_num_str(float(rhs))


def decode_site_file(body):
    """Site answer files are ASCII shifted +13, but non-ASCII chars are NOT.

    Maxi's file is UTF-8 `?\\xc2\\xb2:>8C:EJ>`: the ASCII bytes carry the +13
    shift while `²` (0xC2 0xB2) is stored verbatim. Shifting every char turned
    `²` into `¥`, so the site's own ground-truth equation failed validation and
    the run fell back to a wrong worker guess. Shift ASCII only, and take
    bytes as UTF-8 so the multi-byte exponent survives.
    """
    if isinstance(body, (bytes, bytearray)):
        try:
            body = body.decode("utf-8")
        except UnicodeDecodeError:
            body = body.decode("latin-1")
    try:
        return ''.join(chr(ord(c) - 13) if ord(c) < 128 else c
                       for c in (body or '').strip())
    except Exception:
        return ''


async def read_game_state(page, cols):
    """The site keeps today's solution in localStorage.gameState (ground truth,
    authoritative even where the worker's hint/answer list is wrong)."""
    try:
        raw = await page.evaluate(
            "() => { try { return localStorage.getItem('gameState'); } "
            "catch (e) { return null; } }")
        if not raw:
            return []
        j = json.loads(raw)
        if not isinstance(j, dict):
            return []
    except Exception:
        return []
    vals = []
    for key in ("solution", "solutions", "answers"):
        v = j.get(key)
        if isinstance(v, str):
            vals.append(v)
        elif isinstance(v, list):
            vals.extend(str(x) for x in v)
    out = []
    for s in vals:
        s = (s or '').strip()
        if s and is_valid_eq(s, cols) and s not in out:
            out.append(s)
    if out:
        print(f"[gameState] solution(s): {out}")
    return out


async def wait_change(page, cols, prev, timeout=8.0):
    """Poll until evaluated-row counts change (empty reads never count).
    Then settle: one extra re-read after 600ms."""
    state = []
    t0 = time.time()
    while time.time() - t0 < timeout:
        state = await read_state(page, cols, tries=1)
        if state:
            counts = [eval_count(g) for g in state]
            n = min(len(counts), len(prev)) if prev else 0
            if (len(counts) != len(prev) and prev) or any(
                    counts[i] != prev[i] for i in range(n)):
                await page.wait_for_timeout(600)
                settled = await read_state(page, cols, tries=3)
                if settled:
                    return settled
                return state
        await page.wait_for_timeout(400)
    return state


async def focus_grid(page):
    try:
        g = page.locator("div.pb-grid").first
        box = await g.bounding_box(timeout=3000)
        if box:
            await page.mouse.click(box["x"] + 10, box["y"] + box["height"] - 10)
            await page.wait_for_timeout(400)
            return
    except Exception:
        pass
    await page.mouse.click(640, 360)
    await page.wait_for_timeout(400)


CONSENT_SELS = [
    "button:has-text('Accept All')", "button:has-text('Reject All')",
    "button:has-text('Accept all')", "button:has-text('Reject all')",
    "button:has-text('Accept')", "button:has-text('Reject')",
    "button:has-text('Agree')", "button:has-text('Allow all')",
    "button:has-text('Got it')", "#truste-consent-button",
]


async def accept_cookies(page, quiet=False):
    """Dismiss CMP consent dialogs (may live in iframes). Returns True if clicked."""
    for fr in list(page.frames):
        for sel in CONSENT_SELS:
            try:
                el = await fr.query_selector(sel)
                if not el:
                    continue
                if not await el.is_visible():
                    continue
                await el.click(timeout=1500)
                print(f"[consent] clicked {sel!r} in {fr.url[:60]}")
                await page.wait_for_timeout(700)
                return True
            except Exception as e:
                if not quiet:
                    print(f"[consent] {sel!r} on {fr.url[:40]} failed: {e}")
                continue
    if not quiet:
        print(f"[consent] none ({len(page.frames)} frames)")
    return False


async def dismiss_modals(page, has_grid):
    sels = ["button:has-text('play now')", "button:has-text('Play Now')",
            "button:has-text('Accept')", "button:has-text('Got it')",
            "button:has-text('X Disable Hints')", "button:has-text('Disable Hints')",
            "[aria-label='dismiss']", ".fc-cta-consent"]
    for sel in sels:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                await el.click(timeout=2000)
                await page.wait_for_timeout(600)
                return
        except Exception:
            continue


SPLASH_JS = """
() => {
  const vis = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const want = ['play now', 'how to play', 'disable hints'];
  for (const b of document.querySelectorAll('button, a')) {
    const t = (b.textContent || '').trim().toLowerCase();
    if (want.includes(t) && vis(b)) return true;
  }
  return false;
}
"""


async def sweep_popups(page, tag=""):
    """Cheap popup sweep between guesses / before screenshots.
    Closes the delayed intro splash + hint tooltip + CMP consent without
    touching an open win modal (no bare Escape unless splash persists)."""
    acted = False
    if len(page.frames) > 1:
        acted = await accept_cookies(page, quiet=True) or acted
    try:
        need = bool(await page.evaluate(SPLASH_JS))
    except Exception:
        need = False
    if need:
        await dismiss_modals(page, has_grid=True)
        acted = True
        try:
            still = bool(await page.evaluate(SPLASH_JS))
        except Exception:
            still = False
        if still:
            try:
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(300)
            except Exception:
                pass
    if acted:
        print(f"[sweep{tag}] popups dismissed")
    return acted


CLICK_POWER_JS = """
(ch) => {
  const vis = e => e && e.offsetParent !== null;
  const b = Array.from(document.querySelectorAll('button'))
    .filter(vis)
    .find(x => (x.getAttribute('aria-label') || '').trim() === ch);
  if (!b) return false;
  b.click();
  return true;
}
"""


async def type_equation(page, eq, delay=90):
    """Type `eq`, entering ²/³ via the board's own power keys.

    `keyboard.type` silently DROPS superscripts (Maxi's row then holds one
    fewer tile and the guess can never score - the exact failure that lost
    the mode). The site exposes them as buttons whose aria-label is the
    superscript, so split the equation and click those in place.
    """
    if not any(c in eq for c in "²³"):
        await page.keyboard.type(eq, delay=delay)
        return
    for part in re.split(r"([²³])", eq):
        if not part:
            continue
        if part in "²³":
            ok = await page.evaluate(CLICK_POWER_JS, part)
            if not ok:
                print(f"[type] power key {part!r} not found on board")
            await page.wait_for_timeout(120)
        else:
            await page.keyboard.type(part, delay=delay)


async def type_guess(page, eq, cols, prev):
    """Type one equation + Enter with a retry ladder.
    Returns (state, counts, ok); ok only when evaluated-row counts changed."""

    async def done(state):
        counts = [eval_count(g) for g in state]
        ok = bool(state) and (
            len(counts) != len(prev)
            or any(counts[i] != prev[i] for i in range(len(counts))))
        return state, counts, ok

    try:
        await type_equation(page, eq)
        await page.keyboard.press("Enter")
    except Exception as e:
        print(f"[type] {eq!r} failed: {e}")
        return [], [], False
    state, counts, ok = await done(await wait_change(page, cols, prev))
    print(f"[type] {eq} attempt1 ok={ok} counts={counts}")
    if ok:
        return state, counts, True
    # retry 1: dismiss popups that appeared, flush with Enter alone
    await accept_cookies(page)
    try:
        await page.keyboard.press("Enter")
    except Exception:
        pass
    state, counts, ok = await done(await wait_change(page, cols, prev, timeout=5))
    print(f"[type] {eq} attempt2 ok={ok} counts={counts}")
    if ok:
        return state, counts, True
    # retry 2: clear popups, refocus, clear row, retype
    await accept_cookies(page)
    try:
        await page.keyboard.press("Escape")
    except Exception:
        pass
    await focus_grid(page)
    try:
        for _ in range(cols + 2):
            await page.keyboard.press("Backspace")
        await type_equation(page, eq)
        await page.keyboard.press("Enter")
    except Exception as e:
        print(f"[type] retry failed: {e}")
        return state, counts, False
    state, counts, ok = await done(await wait_change(page, cols, prev, timeout=8))
    print(f"[type] {eq} attempt3 ok={ok} counts={counts}")
    return state, counts, ok


FETCH_JS = """
async () => {
  const out = [];
  for (const e of performance.getEntriesByType('resource')) {
    const u = e.name || '';
    if (/nerdlegame\\.com/i.test(u) && /(words|answer)/i.test(u)) {
      try { out.push(await (await fetch(u)).text()); } catch (err) {}
    }
  }
  return out;
}
"""


def _merge_site_eqs(site_eqs, worker_eqs, boards_n):
    """Site answer files are ground truth; keep worker eqs only to pad."""
    if not site_eqs:
        return list(worker_eqs)
    merged = list(site_eqs)
    for e in worker_eqs:
        if len(merged) >= max(boards_n, len(site_eqs)):
            break
        if e not in merged:
            merged.append(e)
    return merged


def _seed_history(state):
    """All evaluated rows at load (starters / resumes) -> per-grid hists."""
    hists, typed = {}, []
    for b, g in enumerate(state):
        rows_h = []
        for r, fb in enumerate(g["rows"]):
            if _done(fb):
                t = (g["texts"][r] or "").strip()
                if t:
                    rows_h.append((t, list(fb)))
                    if t not in typed:
                        typed.append(t)
        hists[b] = rows_h
    return hists, typed


async def play_mode(page, mode, script_dir, official=None):
    """Solve ONE mode: algorithm first (solver-worker entropy guesses);
    official day's answer only when rows_left <= 2 (or solver is empty).
    Returns (typed_count, expected_count, signal)."""
    print(f"\n=== {mode['name']} ({mode['url']}) ===")
    eqs = list((official or {}).get(mode["id"]) or [])
    cols = mode["len"]
    boards_n = mode.get("boards", 1)
    typed = 0
    signal = ""
    site_eqs = []
    try:
        async def on_resp(r):
            try:
                u = r.url.lower()
                if "nerdlegame.com" in u and ("words" in u or "answer" in u):
                    dec = decode_site_file(await r.text())
                    if dec and is_valid_eq(dec, cols) and dec not in site_eqs:
                        site_eqs.append(dec)
                        print(f"[{mode['id']}] site answer file -> {dec}")
            except Exception:
                pass

        page.on("response", on_resp)
        for att in range(3):
            try:
                await page.goto(mode["url"], wait_until="domcontentloaded", timeout=45000)
                break
            except Exception as e:
                print(f"[{mode['id']}] goto {att + 1}/3 failed: {e}")
                await page.wait_for_timeout(2500)
        for _ in range(4):
            await page.wait_for_timeout(1000)
            await sweep_popups(page, tag=f":{mode['id']}")
        # popups block input: intro/about splash (Escape), CMP consent (frames)
        for _ in range(2):
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass
            await page.wait_for_timeout(400)
        await accept_cookies(page)
        await dismiss_modals(page, has_grid=True)
        await accept_cookies(page)
        state = await read_state(page, cols, tries=6)
        if not site_eqs:
            try:
                for body in (await page.evaluate(FETCH_JS) or []):
                    dec = decode_site_file(body)
                    if dec and is_valid_eq(dec, cols) and dec not in site_eqs:
                        site_eqs.append(dec)
                        print(f"[{mode['id']}] site answer via fetch -> {dec}")
            except Exception as e:
                print(f"[{mode['id']}] fetch fallback failed: {e}")
        gs_eqs = await read_game_state(page, cols)
        try:
            page.remove_listener("response", on_resp)
        except Exception:
            pass
        pool = list(site_eqs)
        for e in gs_eqs:
            if e not in pool:
                pool.append(e)
        if pool:
            eqs = _merge_site_eqs(pool, eqs, boards_n)
            print(f"[{mode['id']}] official eqs: {eqs}")

        async def check_win():
            try:
                return bool(await page.evaluate(
                    WIN_JS, {"cols": cols, "boards": boards_n}))
            except Exception:
                return False

        await sweep_popups(page, tag=f":{mode['id']}")

        if mode["id"] == "instant":
            # hint row is the pre-given starter; exactly one guess allowed
            await focus_grid(page)
            if eqs:
                if state:
                    prev = [eval_count(g) for g in state]
                    await type_guess(page, eqs[0], cols, prev)
                else:
                    await page.keyboard.type(eqs[0], delay=90)
                    await page.keyboard.press("Enter")
                    await page.wait_for_timeout(3000)
                typed = 1
        elif not state:
            print(f"[{mode['id']}] no pb-grid found, legacy direct typing")
            await focus_grid(page)
            for eq in eqs:
                await page.keyboard.type(eq, delay=90)
                await page.keyboard.press("Enter")
                await page.wait_for_timeout(2500)
                typed += 1
        else:
            # flush pre-filled starter row (quad/minibi/speed): typing before
            # Enter truncates/discards the first guess
            pending = any(
                g["rows"] and not _done(g["rows"][0]) and (g["texts"][0] or "").strip()
                for g in state)
            if pending:
                print(f"[{mode['id']}] flushing pre-filled row")
                await focus_grid(page)
                prev0 = [eval_count(g) for g in state]
                try:
                    await page.keyboard.press("Enter")
                except Exception:
                    pass
                new_state = await wait_change(page, cols, prev0, timeout=6)
                if new_state:
                    state = new_state
                else:
                    try:
                        for _ in range(cols + 2):
                            await page.keyboard.press("Backspace")
                    except Exception:
                        pass
                    state = await read_state(page, cols) or state
            hists, typed_guesses = _seed_history(state)
            usable0 = {i: len(g["rows"]) for i, g in enumerate(state)}
            prev_counts = [eval_count(g) for g in state]
            official_i = 0

            for _it in range(40):
                for b, g in enumerate(state):
                    c = eval_count(g)
                    before = prev_counts[b] if b < len(prev_counts) else -1
                    if b not in usable0:
                        usable0[b] = len(g["rows"])
                        hists[b] = []
                    elif c < before:
                        print(f"[{mode['id']}] grid {b} reset, clearing hist")
                        hists[b] = []
                        usable0[b] = len(g["rows"])
                if len(state) >= boards_n and all(grid_solved(g) for g in state):
                    signal = "won"
                    break
                if await check_win():
                    signal = "won"
                    break
                await sweep_popups(page, tag=f":{mode['id']}")
                unsolved = [i for i, g in enumerate(state) if not grid_solved(g)]
                if not unsolved:
                    if len(state) >= boards_n:
                        signal = "won"
                        break
                    # more boards may still appear (speed board2)
                    await page.wait_for_timeout(2500)
                    fresh = await read_state(page, cols, tries=3)
                    if fresh and len(fresh) > len(state):
                        for b in range(len(state), len(fresh)):
                            usable0[b] = len(fresh[b]["rows"])
                            hists[b] = []
                        state = fresh
                        prev_counts = [eval_count(g) for g in state]
                        continue
                    break
                first_open = unsolved[0]
                rows_left = min(
                    usable0.get(i, len(state[i]["rows"])) - eval_count(state[i])
                    for i in unsolved)
                if rows_left <= 0:
                    break
                need = len([e for e in eqs if e not in typed_guesses])
                nxt = None
                if rows_left <= 2 and need:
                    # user rule: official answer only in the last 2 attempts
                    while official_i < len(eqs) and eqs[official_i] in typed_guesses:
                        official_i += 1
                    if official_i < len(eqs):
                        nxt = eqs[official_i]
                        official_i += 1
                else:
                    raw = solver_suggest(mode["id"], hists.get(first_open, []))
                    # ²/³ suggestions used to be dropped because keyboard.type
                    # could not enter them; type_equation clicks the board's
                    # own power keys now, so keep them.
                    sug = [s for s in raw if s not in typed_guesses
                           and len(s) == cols]
                    if raw and not sug:
                        print(f"[{mode['id']}] suggestions all filtered: "
                              f"raw={raw[:6]} typed={typed_guesses}")
                    if not sug:
                        for j in unsolved[1:]:
                            alt = [s for s in solver_suggest(
                                       mode["id"], hists.get(j, []), tries=1)
                                   if s not in typed_guesses and len(s) == cols]
                            if alt:
                                sug = alt
                                break
                    nxt = sug[0] if sug else None
                    if nxt is None:
                        # solver empty/down: emergency official
                        while official_i < len(eqs) and eqs[official_i] in typed_guesses:
                            official_i += 1
                        if official_i < len(eqs):
                            nxt = eqs[official_i]
                            official_i += 1
                if nxt is None:
                    if not typed_guesses:
                        nxt = FILLERS.get(cols, "12+34=46")
                    else:
                        print(f"[{mode['id']}] no next guess available, stopping")
                        break
                prev = list(prev_counts)
                state, counts, ok = await type_guess(page, nxt, cols, prev)
                if not ok:
                    print(f"[{mode['id']}] board did not react to {nxt!r}, stopping")
                    break
                typed += 1
                typed_guesses.append(nxt)
                for b, g in enumerate(state):
                    before = prev[b] if b < len(prev) else -1
                    c = counts[b] if b < len(counts) else before
                    if c < before:
                        hists[b] = []
                        usable0[b] = len(g["rows"])
                    elif c > before:
                        last = next((r for r in reversed(g["rows"]) if _done(r)), None)
                        if last is not None:
                            hists.setdefault(b, []).append((nxt, list(last)))
                prev_counts = counts
                print(f"[{mode['id']}] guess {typed}: {nxt} "
                      f"rows_left~{rows_left - 1} hist={len(hists.get(first_open, []))}")
            if not signal:
                fresh = await read_state(page, cols, tries=3)
                if fresh and len(fresh) >= boards_n and all(
                        grid_solved(g) for g in fresh):
                    signal = "won"
                elif not signal and await check_win():
                    signal = "won"

        if not signal:
            fresh = await read_state(page, cols, tries=3)
            if fresh and len(fresh) >= boards_n and all(
                    grid_solved(g) for g in fresh):
                signal = "won"
        if signal == "won":
            await sweep_popups(page, tag=f":{mode['id']}")
        for _ in range(3):
            await page.wait_for_timeout(700)
            await sweep_popups(page, tag=f":{mode['id']}")
        try:
            url_now = page.url
            splash_now = bool(await page.evaluate(SPLASH_JS))
        except Exception:
            url_now, splash_now = "?", "?"
        print(f"[{mode['id']}] shot@ {url_now} splash={splash_now} won={bool(signal)}")
        try:
            await page.screenshot(path=str(script_dir / f"shot_{mode['id']}.png"))
        except Exception:
            pass
        if not signal:
            try:
                won = await page.evaluate(WIN_JS, {"cols": cols, "boards": boards_n})
                if won:
                    signal = "won"
            except Exception as e:
                print(f"[{mode['id']}] win check failed: {e}")
        print(f"[{mode['id']}] typed {typed}/{len(eqs)} won={bool(signal)}")
        return typed, len(eqs), signal
    except Exception as e:
        print(f"[{mode['id']}] play failed: {e}")
        import traceback
        traceback.print_exc()
        return 0, len(eqs), ""


async def main():
    script_dir = Path(__file__).parent
    video_dir = script_dir / "videos"
    video_dir.mkdir(exist_ok=True)
    target = _target_date()
    date_key = target.strftime("%Y-%m-%d")
    today = target.strftime("%B %d, %Y")
    date_short = target.strftime("%b %d")
    day_num = target.timetuple().tm_yday
    print(f"[nerdle] target {date_key} puzzle #{classic_puzzle_number(target)}")

    _, official = await asyncio.to_thread(fetch_nerdle_answers, target)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        tz = os.environ.get("BROWSER_TZ", "Asia/Tokyo")
        print(f"[tz] {tz}")
        context = await browser.new_context(
            record_video_dir=str(video_dir),
            record_video_size={"width": 1280, "height": 720},
            viewport={"width": 1280, "height": 720},
            timezone_id=tz, locale="en-US",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        )
        await context.route("**/*", block_ads)
        try:
            fake_iso = os.environ.get("FAKE_DATE_ISO", "").strip()
            if fake_iso and QP_AVAILABLE:
                await context.add_init_script(script=QP.get_fake_date_init_script(fake_iso))
                print(f"[fakedate] {fake_iso}")
        except Exception as e:
            print(f"[fakedate] skipped: {e}")
        page = await context.new_page()
        t0 = time.time()
        starts = []
        results = []
        for i, m in enumerate(NERDLE_MODES):
            starts.append(time.time() - t0)
            r = await play_mode(page, m, script_dir, official if isinstance(official, dict) else None)
            results.append((m["id"],) + r)
        total = time.time() - t0
        vpath = await page.video.path()
        await context.close()
        await browser.close()

    # verify gate: every mode must show a REAL on-page win (WIN_JS)
    won_modes = [mid for mid, t, e, s in results if s == "won"]
    weak = [(mid, t, e) for mid, t, e, s in results if s != "won"]
    failed = weak
    all_solved = len(won_modes) == len(NERDLE_MODES)
    print(f"[verify] won {len(won_modes)}/{len(NERDLE_MODES)} no-win={weak}")

    final = vpath
    chapters = []
    # Wordle-parity assembly (moviepy) + uniform music
    if final and MOVIEPY_AVAILABLE and QP_AVAILABLE:
        try:
            gameplay = VideoFileClip(str(final))
            gd = float(gameplay.duration or 0)
            parts, cursor = [], 0.0
            def _img(p, d):
                return ImageClip(p).set_duration(d).set_fps(24).resize(width=1920, height=1080)
            classic = []
            if isinstance(official, dict):
                classic = [e for e in (official.get("classic") or [])][:4]
            # hints slide reuses quordle hints card (4 words); pad/truncate
            hp = script_dir / f"hints_{date_key}.png"
            if QP.generate_quordle_hints_image(str(hp), today, (classic[:4] if classic else ["12+34=46"]*1)):
                chapters.append((cursor, "Hints before the solve"))
                parts.append(_img(str(hp), 10)); cursor += 10
            # Gameplay split into 9 mode chapters, each prefixed with a NOW PLAYING
            # card so a viewer landing mid-video can tell which mode is up.
            # The recording is continuous, so the boundaries come from the
            # per-mode `starts` timestamps captured while playing.
            weights = []
            for i in range(len(NERDLE_MODES)):
                s = starts[i] if i < len(starts) else 0
                e = starts[i+1] if i+1 < len(starts) else total
                weights.append(max(1.0, e - s))
            tw = sum(weights) or 1.0
            cuts, acc = [], 0.0
            for w in weights:
                cuts.append((acc * gd / tw, (acc + w) * gd / tw))
                acc += w

            mode_parts, card_sec = [], 0.0
            for i, m in enumerate(NERDLE_MODES):
                a, b = cuts[i]
                if b - a < 0.5:
                    continue
                chapters.append((cursor + card_sec, f"{m['name']} Solve"))
                cp = script_dir / f"mode_{i}.png"
                try:
                    QP.generate_mode_card(str(cp), "Nerdle",
                                          m.get("name", f"Mode {i + 1}"),
                                          f"Round {i + 1} of {len(NERDLE_MODES)}")
                    mode_parts.append(_img(str(cp), 2.5))
                    card_sec += 2.5
                except Exception as _e:
                    print(f"[nerdle] mode card failed: {str(_e)[:90]}")
                mode_parts.append(gameplay.subclip(a, b))
            if mode_parts:
                parts.extend(mode_parts)
                cursor += gd + card_sec
            else:
                parts.append(gameplay)
                cursor += gd
            # analysis slides (reuse quordle def/freq/facts with nerdle equations as words)
            ap = script_dir / f"analysis_{date_key}.png"
            if QP.generate_quordle_definition_slide(str(ap), today, classic[:4] if classic else ["EQUATION"], {}):
                chapters.append((cursor, "Answer analysis"))
                parts.append(_img(str(ap), 8)); cursor += 8
            fp = script_dir / f"facts_{date_key}.png"
            if QP.generate_quordle_facts_slide(str(fp), today, classic[:4] if classic else ["EQUATION"]):
                chapters.append((cursor, "Facts & solve path"))
                parts.append(_img(str(fp), 8)); cursor += 8
            final_clip = concatenate_videoclips(parts, method="compose")
            for _mp3 in ("song1.mp3", "song2.mp3"):
                _sp = script_dir / _mp3
                if _sp.exists():
                    try:
                        sc = AudioFileClip(str(_sp))
                        full = afx.audio_loop(sc, duration=final_clip.duration) if sc.duration < final_clip.duration else sc.subclip(0, final_clip.duration)
                        final_clip = final_clip.set_audio(full)
                        break
                    except Exception:
                        continue
            out = str(video_dir / f"nerdle_final_{date_key}.mp4")
            final_clip.write_videofile(out, codec='libx264', audio_codec='aac', fps=24)
            final = out
        except Exception as e:
            print(f"[assembly] fallback raw: {e}")

    if YOUTUBE_AVAILABLE and final:
        title_v = [
            f"Nerdle Answer Today ({date_short}) - All 9 Modes Solved!",
            f"Nerdle Answer Today - {today} Classic/Mini/Maxi + More",
            f"Nerdle Answers Today - {today} Full Solve + Hints",
        ]
        title = title_v[day_num % len(title_v)]
        if not chapters:
            chapters = [(0, f"{m['name']} Solve") for m in NERDLE_MODES]
        mode_lines = []
        if isinstance(official, dict):
            for m in NERDLE_MODES:
                eqs = official.get(m["id"]) or []
                if eqs:
                    mode_lines.append(f"{m['name']}: {' | '.join(eqs)}")
        chap_str = "\n".join(f"{int(s//60)}:{int(s%60):02d} {l}" for s, l in chapters)
        desc = (f"Nerdle Answer Today — {today} | All 9 Modes Solved\n\n"
                + ("\n".join(mode_lines) + "\n\n" if mode_lines else "")
                + f"Watch every mode solved step by step with hints.\n\nCHAPTERS:\n{chap_str}\n\n"
                f"Nerdle solver: https://wordsolverx.com/nerdle-solver\n\n"
                f"#Nerdle #NerdleAnswerToday #DailyPuzzle #MathPuzzle #WordGame")
        tags = ["nerdle answer today", "Nerdle", "Nerdle Answer", "Daily Puzzle",
                "Math Puzzle", "Nerdle Classic", "Nerdle Mini", "Nerdle Maxi",
                "Nerdle Hints", "Nerdle Solver"]
        vid = upload_to_youtube(str(final), title=title, description=desc,
                                chapters=chapters, tags=tags, ytd_info={})
        print(f"[upload] {vid}")
    import json as _json
    (video_dir / f"result_{date_key}.json").write_text(_json.dumps(
        {"game": "nerdle", "date": date_key, "solved": all_solved,
         "modes": [{"id": mid, "typed": t, "expected": e, "signal": s}
                   for mid, t, e, s in results]}, indent=1))
    print("Done!" if all_solved else "NOT SOLVED — see result json")
    import sys as _sys
    _sys.exit(0 if all_solved else 1)


if __name__ == "__main__":
    asyncio.run(main())
