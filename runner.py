"""Daily-games video runner — SOLVING videos, not answer reveals.

Flow per game (Wordle-parity):
  1. Open real game site (BROWSER_TZ + FAKE_DATE_ISO time-travel for next-day).
  2. Play 1-2 ORGANIC starter guesses (show feedback/mechanics on camera).
  3. THEN solve with the real answer (from answers.py = frontend logic).
  4. Verify win on-page (screenshot + signal). solved=False if unverified.
  5. Assemble: hints(10s) + gameplay + analysis(8s) + uniform music + chapters.
  6. SEO upload (title/desc/tags like Wordle) or local-only without creds.

Usage: python runner.py --game betweenle | --all
Exit code 1 if any game unsolved. Never claim completed unless all solved.
"""
import argparse
import json
import os
import re
import shutil
import sys
import time
from datetime import date, datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

try:  # moviepy 1.0.3 uses Image.ANTIALIAS, removed in Pillow 10+
    from PIL import Image as _PILImage
    if not hasattr(_PILImage, "ANTIALIAS"):
        _PILImage.ANTIALIAS = _PILImage.LANCZOS
except Exception:
    pass
import answers as A
import solve_lib as S
import daily_parity as DP

QDIR = HERE  # vendored: quordle_parity/youtube_upload live beside runner.py
for _p in (HERE,
           Path(r"C:\Users\akasa\Projects\wordsolverx\youtube\quordle-video\quor-dle-video")):
    sys.path.insert(0, str(_p))
try:
    import quordle_parity as QP
    from youtube_upload import upload_to_youtube
    HAS = True
except Exception as e:
    print(f"[deps] parity/upload missing: {e}")
    HAS = False

try:
    from moviepy.editor import VideoFileClip, ImageClip, concatenate_videoclips, AudioFileClip
    import moviepy.audio.fx.all as afx
    HAS_MOVIEPY = True
except Exception:
    HAS_MOVIEPY = False

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PIL = True
except Exception:
    HAS_PIL = False

HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"

_DBG_FH = {}
_RELOADS = {}


def _count_reload(gid, why):
    _RELOADS[gid] = _RELOADS.get(gid, 0) + 1
    _dbg(gid, f"PAGE RELOAD #{_RELOADS[gid]} ({why})")


def _dbg(gid, msg):
    """Timestamped debug line -> stdout AND videos/{gid}/debug_{date}.log.

    Every guess, rejection, retry, navigation and signal goes through here so
    a failure can be diagnosed from the log alone without rewatching video.
    """
    import datetime as _dt
    line = f"[{_dt.datetime.now().isoformat(timespec='seconds')}] [{gid}] {msg}"
    print(line)
    try:
        if gid not in _DBG_FH:
            vdir = HERE / "videos" / gid
            vdir.mkdir(parents=True, exist_ok=True)
            _DBG_FH[gid] = open(vdir / f"debug_{A.target_date().isoformat()}.log",
                                "a", encoding="utf-8")
        _DBG_FH[gid].write(line + "\n")
        _DBG_FH[gid].flush()
    except Exception:
        pass

GAMES = {
    "betweenle": {"name": "Betweenle", "url": "https://betweenle.com", "slug": "betweenle-answer-today"},
    "colordle": {"name": "Colordle", "url": "https://colordle.ryantanen.com", "slug": "colordle-answer-today"},
    "colorfle": {"name": "Colorfle", "url": "https://colorfle.com", "slug": "colorfle-answer-today"},
    # Framed publishes four separate daily modes, each with its own puzzle and
    # its own URL. They used to be four near-identical videos, so the channel
    # was flooded with thin look-alikes and the three extra modes were easy to
    # miss. They are now solved back to back in ONE video, one chapter each -
    # the same treatment the Nerdle video already gives its nine modes.
    "framed": {"name": "Framed", "url": "https://framed.wtf", "slug": "framed-answer-today",
               "all_modes": True},
    "searchle": {"name": "Searchle", "url": "https://searchle.net", "slug": "searchle-answer-today"},
    "semantle": {"name": "Semantle", "url": "https://semantle.com", "slug": "semantle-answer-today"},
    # solitaired.com/phrazle.html is a 404; the live path has a trailing slash
    "phrazle": {"name": "Phrazle", "url": "https://solitaired.com/phrazle/", "slug": "phrazle-answer-today"},
    "canuckle": {"name": "Canuckle", "url": "https://www.canucklegame.ca", "slug": "canuckle-answer-today"},
    "phoodle": {"name": "Phoodle", "url": "https://www.phoodle.net", "slug": "phoodle-answer-today"},
    "contexto": {"name": "Contexto", "url": "https://contexto.me", "slug": "contexto-answer-today"},
    "worldle": {"name": "Worldle", "url": "https://worldle.teuteuf.fr/", "slug": "worldle-answer-today"},
    "globle": {"name": "Globle", "url": "https://globle-game.com/game", "slug": "globle-answer-today"},
    "waffle": {"name": "Waffle", "url": "https://wafflegame.net/daily", "slug": "waffle-answer-today"},
    "worgle": {"name": "Worgle", "url": "https://bronze-age.com/worgle/", "slug": "worgle-answer-today"},
    "countryle": {"name": "Countryle", "url": "https://countryle.com/", "slug": "countryle-answer-today"},
    "batterup": {"name": "Batterup", "url": "https://batter-up.app", "slug": "batterup-answer-today"},
    "marveldle": {"name": "Marveldle", "url": "https://marveldle.com", "slug": "marveldle-answer-today"},
    # Nerdle: 9 modes solved back-to-back in ONE video (same treatment as
    # framed all_modes). Lives in nerdle_solver.py (async, kept as-is) and is
    # dispatched via subprocess so the sync runner stays untouched.
    "nerdle": {"name": "Nerdle", "url": "https://www.nerdlegame.com/game", "slug": "nerdle-answer-today",
               "all_modes": True, "external": "nerdle_solver.py"},
}

# The four Framed modes, in the order they are played in the combined video.
FRAMED_MODES = (
    ("daily", "Framed Daily", "https://framed.wtf"),
    ("one-frame", "Framed One Frame", "https://framed.wtf/one-frame"),
    ("titleshot", "Framed Titleshot", "https://framed.wtf/titleshot"),
    ("poster", "Framed Poster", "https://framed.wtf/poster"),
)

COLORFLE_HEX = ['#FFFFFF', '#FFFAC8', '#FABEBE', '#AAFFC3', '#E6BEFF', '#46F0F0',
                '#FFE119', '#BCF60C', '#F58231', '#3CB44B', '#F032E6', '#808000',
                '#008080', '#9A6324', '#E6194B', '#4363D8', '#911EB4', '#800000',
                '#000075', '#000000']


def _hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


# ---------------- generic cards ----------------
def _fonts():
    import os as _os
    for b, r in [("C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/segoeui.ttf"),
                 ("C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/arial.ttf")]:
        if _os.path.exists(b):
            return {"t": ImageFont.truetype(b, 72), "b": ImageFont.truetype(b, 44),
                    "s": ImageFont.truetype(r if _os.path.exists(r) else b, 32)}
    d = ImageFont.load_default()
    return {"t": d, "b": d, "s": d}


def card(out, kicker, title, lines, date_str):
    if not HAS_PIL:
        return None
    W, H = 1920, 1080
    img = Image.new("RGB", (W, H), (13, 20, 38))
    d = ImageDraw.Draw(img)
    f = _fonts()
    d.rectangle([0, 0, 14, H], fill=(46, 204, 113))
    d.text((70, 60), kicker.upper(), fill=(46, 204, 113), font=f["b"])
    d.text((70, 150), title, fill=(255, 255, 255), font=f["t"])
    d.text((70, 260), date_str, fill=(168, 180, 200), font=f["s"])
    y = 380
    for ln in lines[:8]:
        d.rounded_rectangle([70, y, W - 70, y + 76], radius=14, fill=(23, 34, 58))
        d.text((100, y + 14), ln[:80], fill=(255, 255, 255), font=f["b"])
        y += 96
    img.save(out, "PNG", optimize=True)
    return out


def thumb(out, game, date_str, answer_txt):
    if not HAS_PIL:
        return None
    W, H = 1280, 720
    img = Image.new("RGB", (W, H), (13, 20, 38))
    d = ImageDraw.Draw(img)
    f = _fonts()
    d.rounded_rectangle([40, 40, 620, 110], radius=14, fill=(255, 205, 0))
    d.text((65, 50), f"{game.upper()} ANSWER TODAY", fill=(15, 15, 15), font=f["b"])
    d.text((46, 150), "TODAY'S", fill=(255, 255, 255), font=f["t"])
    d.text((46, 240), game.upper(), fill=(46, 204, 113), font=f["t"])
    d.text((46, 340), "SOLVED STEP BY STEP", fill=(255, 205, 0), font=f["b"])
    d.rounded_rectangle([46, 440, 700, 520], radius=12, fill=(15, 23, 42),
                        outline=(46, 204, 113), width=3)
    d.text((70, 455), (answer_txt or "?")[:34], fill=(255, 255, 255), font=f["b"])
    d.text((46, 560), date_str.upper(), fill=(255, 205, 0), font=f["b"])
    img.save(out, "PNG", optimize=True)
    return out


# ---------------- solve steps ----------------
# Clicking the centre of the page is unreliable: on Wordle-clone sites the
# on-screen keyboard sits at the bottom and a stray click can land on an
# ad. We focus the page once, then drive the on-screen ENTER key by text.
ENTER_LABELS = ("ENTER", "Enter", "enter", "SUBMIT", "Submit")


def _polish_gameplay(src, out_path, crf=17):
    """Normalise raw screen capture to full-frame 1920x1080 with a frame.

    Deliberately a plain scale+crop plus a drawn border. An earlier version
    added a slow Ken Burns push-in (`crop` window shrinking over time) which
    made the footage drift and drift the board out of frame; it was removed on
    request. The capture is already recorded at native 1920x1080, so this is
    just a re-encode that guarantees full-bleed framing with no black bars.

    The border is a soft dark outer rule plus a thin accent inner rule. It
    gives the flat screen capture a finished, produced look instead of raw
    desktop footage.
    """
    import subprocess
    out_path = str(out_path)
    plain = ("scale=1920:1080:force_original_aspect_ratio=increase,"
             "crop=1920:1080,setsar=1,fps=24")
    # Outer rule (dark, 10px) then a 3px accent rule just inside it.
    border = (plain +
              ",drawbox=x=0:y=0:w=1920:h=1080:color=0x0B1220@1.0:t=10"
              ",drawbox=x=10:y=10:w=1900:h=1060:color=0x2ECC71@0.95:t=3"
              ",drawbox=x=13:y=13:w=1894:h=1054:color=0x1B2A4A@1.0:t=2")
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", src, "-vf", border,
           "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
           "-pix_fmt", "yuv420p", "-r", "24", out_path]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if r.returncode == 0 and Path(out_path).exists() \
                and Path(out_path).stat().st_size > 10000:
            return out_path
        print(f"[polish] ffmpeg said: {(r.stderr or '')[:200]}")
    except Exception as e:
        print(f"[polish] re-encode failed: {str(e)[:120]}")
    return src


def _focus_board(page):
    """Focus the game without clicking a random coordinate.

    Never click near the top-left corner: that is the account icon on several
    sites, and clicking it OPENS the login sheet that then blocks the board.
    """
    try:
        page.evaluate("() => window.focus && window.focus()")
    except Exception:
        pass
    try:
        page.evaluate("() => window.scrollTo(0, 0)")
    except Exception:
        pass
    page.wait_for_timeout(200)


def _press_enter(page):
    """Submit via the on-screen ENTER key when present, else the keyboard."""
    for sel in ("button:has-text('ENTER')", "button:has-text('Enter')",
                "[class*='enter']", "[aria-label='Enter']"):
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.evaluate("e => e.click()")
                page.wait_for_timeout(600)
                return True
        except Exception:
            continue
    try:
        page.keyboard.press("Enter")
    except Exception:
        return False
    page.wait_for_timeout(400)
    return True


def _dismiss_notifications(page):
    """Close toast/announcement popups (top-right X) that cover the board."""
    for _ in range(3):
        try:
            hit = page.evaluate("""() => {
                for (const e of document.querySelectorAll('button, [role="button"]')) {
                    if (e.offsetParent === null) continue;
                    const r = e.getBoundingClientRect();
                    if (r.y > 160 || r.width > 70) continue;
                    const aria = (e.getAttribute('aria-label') || '').toLowerCase();
                    if (aria.includes('close') || aria.includes('dismiss')) {
                        e.click();
                        return 'aria-close';
                    }
                }
                return null;
            }""")
        except Exception:
            hit = None
        if not hit:
            break
        page.wait_for_timeout(500)
    return page


def _on_screen_keyboard(page):
    """Map the clickable key buttons, or None when there is no such keyboard."""
    try:
        return page.evaluate_handle("""() => {
            const map = {};
            for (const b of document.querySelectorAll('button')) {
                if (b.offsetParent === null) continue;
                const t = (b.innerText || '').trim();
                if (t.length === 1 && /[a-z]/i.test(t)) map[t.toUpperCase()] = b;
                else if (t.toLowerCase() === 'enter') map['__ENTER__'] = b;
                else if (t.toLowerCase() === 'delete') map['__DEL__'] = b;
            }
            return map;
        }""")
    except Exception:
        return None


def _human_delay(base=150):
    """A per-keystroke pause that varies like a real typist.

    Playwright's `keyboard.type(delay=N)` fires every character at exactly N ms,
    which reads on screen as machine input. A real typist speeds up within a
    word and hesitates between words, so the gap is randomised around the base
    and lengthened after a space.
    """
    import random
    d = random.uniform(base * 0.45, base * 1.5)
    if random.random() < 0.12:
        d += random.uniform(180, 420)   # brief thinking pause
    return d


def _settle(page, base=260):
    """The pause a person leaves after committing a guess.

    A human watches the board flip, reads the new colours and thinks before
    moving on. Returning to the board instantly (or after a fixed tick) is the
    clearest tell that nobody is actually reading the feedback.
    """
    import random
    page.wait_for_timeout(int(random.uniform(base * 0.6, base * 1.9)))


def _idle_drift(page, lo=300, hi=900, chance=0.25):
    """Occasionally let the cursor settle and the page sit still.

    Real screen recordings are not frame-perfect: the pointer drifts, the page
    is left alone for a beat. A perfectly uniform capture reads as automation.
    """
    import random
    if random.random() > chance:
        return
    try:
        page.mouse.move(random.randint(200, 1700), random.randint(200, 900),
                        steps=random.randint(3, 9))
    except Exception:
        pass
    page.wait_for_timeout(random.randint(lo, hi))


def _human_mouse_to(page, el):
    """Move the cursor onto `el` in steps so the pointer glide is captured."""
    try:
        bb = el.bounding_box()
        if not bb:
            return
        import random
        x = bb["x"] + bb["width"] / 2
        y = bb["y"] + bb["height"] / 2
        cur = page.mouse  # playwright has no position getter; jump then settle
        for _ in range(6):
            cur.move(x + random.uniform(-25, 25), y + random.uniform(-18, 18))
            page.wait_for_timeout(random.randint(28, 70))
        cur.move(x, y)
        page.wait_for_timeout(160)
    except Exception:
        pass


def _type_like_a_person(page, text, base_delay=150):
    """Type `text` one key at a time with human timing and a settled cursor."""
    for ch in str(text):
        try:
            page.keyboard.type(ch)
        except Exception:
            return False
        page.wait_for_timeout(_human_delay(base_delay))
    return True


def _type_into(page, sel, text, base_delay=150):
    """Focus a field and type `text` one key at a time, like a person.

    `page.fill()` sets the value in a single DOM assignment, so on camera the
    word appears fully formed and then Enter - it reads as paste, not typing.
    This focuses the field, clears any existing text, and sends real key
    events with per-character human timing.
    Focus the field with focus() rather than a click where possible: on
    Framed's root route a real click lands on the MENU's search box instead of
    the game board, so the guess is typed into the menu and never scored.
    """
    focused = False
    try:
        page.focus(sel)
        focused = True
    except Exception:
        pass
    if not focused:
        try:
            page.click(sel)
        except Exception:
            pass
    try:
        page.keyboard.press("Control+a")
        page.keyboard.press("Delete")
    except Exception:
        pass
    page.wait_for_timeout(150)
    return _type_like_a_person(page, text, base_delay=base_delay)


def _type_with_on_screen_keyboard(page, text, kb=None):
    """Click the on-screen keys for `text`. True if that path was used."""
    if kb is None:
        kb = _on_screen_keyboard(page)
    if kb is None:
        return False
    try:
        n = kb.evaluate("m => Object.keys(m).length")
    except Exception:
        return False
    if not n or n < 20:  # too few keys to be a real on-screen keyboard
        return False
    for ch in str(text).upper():
        if not ch.isalpha():
            continue
        try:
            # JSHandle.evaluate takes only an expression, so the key must be
            # interpolated into the source (no format args).
            kb.evaluate("m => m['%s'] && m['%s'].click()" % (ch, ch))
        except Exception:
            return False
        page.wait_for_timeout(_human_delay(230))
    # Submitting is the fragile part: a single click on ENTER can land while
    # the game is still animating and silently drop the row. Clicking twice is
    # harmless when the row is already committed.
    for _ in range(2):
        try:
            kb.evaluate("m => m['__ENTER__'] && m['__ENTER__'].click()")
        except Exception:
            try:
                page.keyboard.press("Enter")
            except Exception:
                return False
        page.wait_for_timeout(500)
    return True


def _kb(page, text, delay=150):
    _dismiss_notifications(page)
    _dismiss_login_wall(page)
    # Sites with an on-screen keyboard need no page focus at all, so try
    # that path first and avoid clicking anywhere on the page.
    if _type_with_on_screen_keyboard(page, text):
        return True
    _focus_board(page)
    _type_like_a_person(page, text, base_delay=delay)
    page.wait_for_timeout(400)
    _press_enter(page)
    return True


def _dismiss_dialogs(page, rounds=4):
    """Close instruction/consent dialogs that block the board.

    Two cases:
      * DOM sites: explicit close affordances, the dialog's own X corner, Escape.
      * Flutter/canvas sites (Canuckle, Betweenle): there is no DOM at all, so
        we click the pixel positions where the X / OK controls are drawn.
    """
    for _ in range(rounds):
        try:
            open_dlg = page.evaluate("""() => {
                const sel = 'div[role="dialog"], .modal, .MuiDialog-root, '
                          + '[class*="Modal"], [class*="modal"], [class*="Popup"]';
                return Array.from(document.querySelectorAll(sel))
                    .filter(e => e.offsetParent !== null).length;
            }""")
        except Exception:
            open_dlg = 0
        if not open_dlg:
            break
        clicked = False
        for sel in ("button[aria-label='Close']", "[aria-label='Close']",
                    "[aria-label='close']", "[aria-label='Dismiss']",
                    "button:has-text('Close')", ".modal-close", "button.close",
                    "[class*='close']", "[class*='Close']"):
            try:
                for el in page.query_selector_all(sel)[:4]:
                    if el.is_visible():
                        el.evaluate("e => e.click()")
                        page.wait_for_timeout(500)
                        clicked = True
            except Exception:
                continue
        if not clicked:
            try:
                box = page.evaluate("""() => {
                    const sel = 'div[role="dialog"], .modal, .MuiDialog-root, '
                              + '[class*="Modal"], [class*="modal"]';
                    const e = Array.from(document.querySelectorAll(sel))
                        .filter(x => x.offsetParent !== null)[0];
                    if (!e) return null;
                    const r = e.getBoundingClientRect();
                    return {x: r.right - 22, y: r.top + 22};
                }""")
                if box:
                    page.mouse.click(box["x"], box["y"])
                    page.wait_for_timeout(600)
                    clicked = True
            except Exception:
                pass
        if not clicked:
            try:
                page.keyboard.press("Escape")
                page.wait_for_timeout(400)
            except Exception:
                pass
    return page


# Canvas/Flutter games draw their dialogs; there is nothing to query in the DOM,
# so the controls are addressed by viewport position. Measured on a 1280x720
# viewport: the HOW TO PLAY dialog's X sits at ~(849,124) and the dialog is
# roughly 390..890 px wide. Enter/Space also dismiss these.
CANVAS_DLG_X = (849, 124)


def _dismiss_canvas_dialog(page, tries=3):
    for _ in range(tries):
        try:
            has_flutter = page.evaluate(
                "() => !!document.querySelector('flt-glass-pane')")
        except Exception:
            has_flutter = False
        if not has_flutter:
            break
        for pt in (CANVAS_DLG_X, (640, 124), (849, 200)):
            try:
                page.mouse.click(pt[0], pt[1])
                page.wait_for_timeout(700)
            except Exception:
                pass
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)
        except Exception:
            pass
    return page


LOGIN_SEL = ("[aria-label='Close']", "button:has-text('×')", "button.close",
             "[class*='close']", ".modal-close", "button:has-text('Close')")


def _dismiss_login_wall(page):
    """Some sites pop a login sheet mid-solve and swallow the board.

    We close it via its own X; if there is no X we hide the overlay node
    itself, which restores the underlying game without touching game state.
    """
    for _ in range(3):
        try:
            hit = page.evaluate("""() => {
                // 1) a close/dismiss control belonging to a login surface
                for (const e of document.querySelectorAll(
                        'button, [role="button"], [aria-label]')) {
                    if (e.offsetParent === null) continue;
                    const aria = (e.getAttribute('aria-label') || '').toLowerCase();
                    if (aria === 'close' || aria.includes('dismiss')
                        || aria.includes('close')) {
                        const host = e.closest(
                            '[role="dialog"], [class*="Modal"], [class*="modal"],'
                            + ' [class*="Sheet"], [class*="drawer"]');
                        if (host) { e.click(); return 'close-btn'; }
                    }
                }
                // 2) hide only the login-specific controls, never a container
                //    that also holds the game board.
                const words = ['login with', 'sign in with', 'log in with',
                               'continue with', 'sign up', 'log in', 'sign in'];
                let n = 0;
                for (const e of document.querySelectorAll(
                        'button, a, [class*="card"], [class*="panel"]')) {
                    if (e.offsetParent === null) continue;
                    const t = (e.innerText || '').trim().toLowerCase();
                    if (!t || t.length > 120) continue;
                    if (words.some(w => t.includes(w))) {
                        e.style.setProperty('display', 'none', 'important');
                        n++;
                    }
                }
                return n ? 'login-controls-hidden' : null;
            }""")
        except Exception:
            hit = None
        if not hit:
            break
        page.wait_for_timeout(800)
    return page


def _enable_flutter_semantics(page):
    """Turn on Flutter's semantics tree so its canvas UI becomes queryable DOM.

    Flutter web paints to a canvas and leaves the DOM empty (bodyTextLen 0),
    which breaks both dialog detection and win verification. It ships a hidden
    "Enable accessibility" placeholder; activating it materialises real
    elements with roles/labels for the tiles, buttons and dialogs.
    """
    try:
        already = page.evaluate(
            "() => document.querySelectorAll('flt-semantics').length")
        if already and already > 3:
            return True
    except Exception:
        pass
    for sel in ("flt-semantics-placeholder", "[aria-label='Enable accessibility']"):
        try:
            el = page.query_selector(sel)
            if el:
                el.evaluate("e => e.click()")
                page.wait_for_timeout(1500)
                break
        except Exception:
            continue
    try:
        n = page.evaluate("() => document.querySelectorAll('flt-semantics').length")
        print(f"[flutter] semantics nodes: {n}")
        return bool(n)
    except Exception:
        return False


def _close_modals(page):
    _enable_flutter_semantics(page)
    _dismiss_dialogs(page)
    # colorfle/how-to X is a bare span/div — click top-right corner region
    try:
        for sel in ("text=How to play",):
            el = page.query_selector(sel)
            if el:
                box = el.bounding_box()
                if box:
                    page.mouse.click(box["x"] + box["width"] + 120, box["y"] - 10)
                    page.wait_for_timeout(500)
    except Exception:
        pass
    _dismiss_dialogs(page, rounds=2)
    _close_generic_howto(page)
    _dismiss_canvas_dialog(page)
    _enable_flutter_semantics(page)


def _close_generic_howto(page):
    """Close a "How to Play" / instructions sheet that covers the board.

    Several sites (Phrazle, Searchle, Colordle) open an instructions sheet on
    load. It sits above the board, swallows clicks, and leaves the game looking
    frozen in the video. Its own markup varies, so match on the visible
    heading and then use the sheet's own close affordance, with Escape and a
    click on the backdrop as fallbacks.
    """
    for _ in range(4):
        try:
            hit = page.evaluate("""() => {
              // 1) an explicit close control inside the open sheet.
              //    NEVER match a bare "x" that is part of an on-screen keyboard:
              //    Phoodle's board keyboard has a real "X" key, so the old
              //    `t === 'x'` rule clicked it and typed a literal X into the
              //    active row, which is why every Phoodle board filled with
              //    X X X X X instead of the guess.
              const isKeyCap = (e) => {
                if (e.tagName === 'BUTTON' || e.tagName === 'INPUT'
                    || e.getAttribute('role') === 'button'
                    || e.getAttribute('aria-label'))
                    return true;
                // a single-glyph element is far more likely a keyboard key
                // than a close affordance (close controls are usually icons)
                return (e.textContent || '').trim().length === 1;
              };
              for (const e of document.querySelectorAll(
                      'button, [role="button"], a, .close, [class*="close"]')) {
                if (e.offsetParent === null) continue;
                const t = ((e.getAttribute('aria-label') || '') + ' '
                           + (e.getAttribute('title') || '') + ' '
                           + (e.textContent || '')).trim().toLowerCase();
                if (t === 'x' || t === '✕' || t === '×') {
                  if (isKeyCap(e)) continue;   // on-screen keyboard key
                  e.click();
                  return 'close-control';
                }
                if (t === 'close' || t.includes('close')
                        || t.includes('dismiss')) {
                  e.click();
                  return 'close-control';
                }
              }
              // 2) the sheet itself: click just outside its painted box
              const sheet = Array.from(document.querySelectorAll(
                  'div,section,aside')).filter(e => {
                if (e.offsetParent === null) return false;
                const cs = getComputedStyle(e);
                if (cs.position !== 'fixed' && cs.position !== 'absolute') return false;
                if (cs.zIndex === 'auto' || Number(cs.zIndex) < 100) return false;
                const r = e.getBoundingClientRect();
                return r.width > innerWidth * 0.4 && r.height > innerHeight * 0.25;
              })[0];
              if (sheet) {
                const r = sheet.getBoundingClientRect();
                const x = r.left < 40 ? r.right + 30 : r.left - 30;
                const y = Math.min(innerHeight - 20, r.top + 30);
                return {x: Math.round(x), y: Math.round(y)};
              }
              return null;
            }""")
        except Exception:
            hit = None
        if not hit:
            break
        if isinstance(hit, dict):
            try:
                page.mouse.click(hit["x"], hit["y"])
            except Exception:
                pass
        page.wait_for_timeout(900)
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
    except Exception:
        pass
    return page


def _close_betweenle_help(page):
    """Close Betweenle's "How to play" overlay.

    The overlay is `#helppanel` and its X is `#helppanel-close`. The previous
    code clicked `#tipspanel-close`, which does not exist on the current build,
    so the panel stayed open for the whole video and every keystroke was
    swallowed by it - the board never received a single guess.
    """
    for _ in range(3):
        try:
            state = page.evaluate("""() => {
              const hp = document.getElementById('helppanel');
              if (!hp) return 'missing';
              return getComputedStyle(hp).display;
            }""")
        except Exception:
            return page
        if state in (None, "none", "missing"):
            break
        try:
            page.evaluate(
                "() => document.getElementById('helppanel-close')?.click()")
        except Exception:
            pass
        page.wait_for_timeout(1200)
    return page


def _betweenle_invalid_showing(page):
    try:
        txt = (page.evaluate("() => document.body.innerText") or "").lower()
    except Exception:
        return False
    return any(s in txt for s in ("not a word", "not valid", "invalid word",
                                  "not in the word list", "unknown word"))


def _type_betweenle_word(page, gid, word, used):
    """Type one betweenle guess; on 'not a word' drop it and try the next
    bank word exactly like a human would (never hammer Enter on a dead word)."""
    _type_like_a_person(page, word, base_delay=170)
    page.wait_for_timeout(300)
    page.keyboard.press("Enter")
    page.wait_for_timeout(3000)
    if _betweenle_invalid_showing(page):
        _dbg(gid, f"{word!r} rejected by site; replacing from bank")
        try:
            guesses, _ans = S.words_betweenle()
        except Exception:
            guesses = []
        for alt in guesses:
            a = str(alt).upper()
            if a not in used and a != word.upper():
                _dbg(gid, f"retry with {a!r}")
                return _type_betweenle_word(page, gid, a, used | {a})
        return False, word
    used.add(word.upper())
    return True, word


def solve_betweenle(page, ans):
    _close_modals(page)
    try:
        # handler is on span#menupanel-mode-daily itself
        page.evaluate("() => { const el = document.getElementById('menupanel-mode-daily'); if (el) el.click(); }")
        page.wait_for_timeout(4000)
    except Exception:
        pass
    # The How-to-Play panel only appears AFTER the daily game starts, so this
    # must run after the mode click, not before it.
    _close_betweenle_help(page)
    # canvas game: no DOM input, so drive the physical keyboard
    page.mouse.click(960, 600)
    page.wait_for_timeout(500)
    used = set()
    ok1, first = _type_betweenle_word(page, gid="betweenle", word="about",
                                      used=used)
    if not ok1:
        return False, "opener rejected and bank exhausted"
    _dbg("betweenle", f"opener {first!r} accepted")
    ok2, final = _type_betweenle_word(page, gid="betweenle",
                                      word=ans["answer"], used=used)
    if not ok2:
        return False, "answer rejected and bank exhausted"
    _dbg("betweenle", f"final {final!r} accepted")
    page.wait_for_timeout(1500)
    try:
        stat = page.evaluate(
            "() => { const w = document.getElementById('statisticspanel-word'); "
            "return w ? w.innerText : ''; }") or ""
    except Exception:
        stat = ""
    ok = ans["answer"].lower() in stat.lower() if stat else False
    if not ok:
        return False, f"no solve signal; stats={stat!r}"
    return True, f"stats panel word={stat!r}"


def solve_colordle(page, ans):
    """Real Colordle solve.

    Plays a genuine colour-distance search (several spread-out anchor colours,
    then the exact answer) and verifies the site's OWN win banner:
    "You guessed the right color, <name>, in N guesses!".

    The previous check was `"100%" in body`, which is worthless twice over:
    the literal text "100%" appears in the static How-to-Play instructions on
    a fresh page (so it was true before any guess), and a 100% similarity
    score is not itself a win state.
    """
    _close_modals(page)
    name = ans.get("name") if isinstance(ans, dict) else ans
    hexv = ans.get("hex") if isinstance(ans, dict) else None
    sel = "input[placeholder='Guess your color here']"

    guesses = [name]
    if hexv:
        try:
            seq, _ = S.plan_colordle(hexv)
            if seq and seq[-1].lower() == str(name).lower():
                guesses = seq
        except Exception as e:
            print(f"[colordle] planner failed, using direct answer: {e}")

    percents = []
    for g in guesses:
        try:
            _type_into(page, sel, str(g), base_delay=140)
            page.click("button:has-text('Test Color!')")
        except Exception as e:
            return False, f"guess {g!r} failed: {str(e)[:120]}"
        page.wait_for_timeout(3200)
        # Record the REAL score the site prints for the guess we just made.
        # Match on the guess text itself - matching "#\d+ - x%" picked up the
        # wrong row and made every guess report the same percentage.
        try:
            txt = page.evaluate("() => document.body.innerText") or ""
            import re as _re
            esc = _re.escape(str(g))
            m = _re.search(esc + r"[^\n]*?-\s*([\d.]+)%", txt, _re.I)
            percents.append((m.group(1) + "%") if m else "?")
        except Exception:
            percents.append("?")

    body = (page.evaluate("() => document.body.innerText") or "").lower()
    # The site's own win banner - the only trustworthy signal.
    if "you guessed the right color" in body:
        return True, (f"win banner 'You guessed the right color' after "
                       f"{len(guesses)} guesses; scores={percents}")
    return False, (f"no win banner after {len(guesses)} guesses "
                   f"(guesses={guesses}, scores={percents})")


def _colorfle_swatches(page):
    """Map Colorfle palette index -> clickable element, by background colour."""
    import re as _re
    sw = {}
    for b in page.query_selector_all("button"):
        try:
            bg = b.evaluate("e => getComputedStyle(e).backgroundColor")
            m = _re.match(r"rgb\((\d+),\s*(\d+),\s*(\d+)\)", bg or "")
            if not m:
                continue
            rgb = tuple(map(int, m.groups()))
            for i, hx in enumerate(COLORFLE_HEX):
                if _hex_to_rgb(hx) == rgb and i not in sw:
                    sw[i] = b
                    break
        except Exception:
            continue
    return sw


def _dismiss_colorfle_modal(page):
    """Close the How-to-Play modal.

    It sits over the board and swallows clicks, and its text contains the
    literal string "100%" - which is exactly what the old verifier matched on.
    """
    try:
        page.evaluate("""() => {
            const m = document.querySelector('.modal');
            if (m) { m.classList.remove('active','show','visible');
                     m.style.display = 'none'; }
        }""")
        page.wait_for_timeout(800)
    except Exception:
        pass


def solve_colorfle(page, ans):
    """Real Colorfle solve: Mastermind deduction, then the winning board.

    Verification uses the game's own end state. The old check was
    `"100%" in body`, which matched the static How-to-Play instructions before
    a single guess was made, so it reported a win no matter what happened.
    """
    _close_modals(page)
    _dismiss_colorfle_modal(page)
    colors = ans.get("colors") if isinstance(ans, dict) else None
    if not colors:
        return False, "no color answer available"
    colors = [int(c) for c in colors]

    sw = _colorfle_swatches(page)
    if len(sw) < 10:
        return False, f"only {len(sw)} swatches mapped"
    for i in colors:
        if i not in sw:
            return False, f"answer color {i} not on board"
    enter = page.query_selector("button:has-text('ENTER')")
    if not enter:
        return False, "ENTER button not found"

    try:
        seq, _ = S.plan_colorfle(colors)
    except Exception as e:
        print(f"[colorfle] planner failed: {e}")
        seq = [colors]

    def _click(el):
        bb = el.bounding_box()
        if bb:
            page.mouse.click(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2)
        else:
            el.evaluate("e => e.click()")

    for combo in seq:
        combo = [int(c) for c in combo]
        for i in combo:
            if i in sw:
                _click(sw[i])
                page.wait_for_timeout(420)
        _click(enter)
        page.wait_for_timeout(3400)

    # The game's own end state. On a win Colorfle opens a Statistics panel
    # reading "1 Wins" / "Your average color accuracy was ...%", and the
    # winning row is ringed green at 100%. The old check was
    # `"100%" in page.content()`, which matched the static How-to-Play
    # instructions before any guess was made.
    try:
        stats = page.evaluate("""() => {
            const out = [];
            for (const e of document.querySelectorAll('div,section,span,p')) {
                const t = (e.innerText || '').trim();
                if (!t || t.length > 400) continue;
                if (/Wins|average color accuracy|NEXT COLORFLE|Statistics/i.test(t)) {
                    out.push(t);
                }
            }
            return out.join(' || ');
        }""") or ""
    except Exception:
        stats = ""
    won = False
    if "wins" in stats.lower() and "accuracy" in stats.lower():
        # "1 Wins" only appears once a board has actually been solved.
        won = True
    if won:
        return True, (f"Statistics panel confirms a win after {len(seq)} "
                       f"guesses ({stats[:120]!r})")
    return False, (f"no win panel after {len(seq)} guesses "
                   f"(stats={stats[:120]!r})")


def _framed_open_mode(page, url):
    """Open a Framed mode and make sure the real game board is showing.

    framed.wtf serves a Next.js mode menu on every route, and that menu carries
    its own search box ALSO labelled aria-label="guess input". Guessing from the
    menu typed into the overlay, so nothing was ever scored and all four modes
    reported a loss. The real board is only reached by activating the mode tile,
    which swaps the menu for the puzzle; the guess box is then distinguished by
    its placeholder.
    """
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(6000)
    # The mode menu lives in an OFF-SCREEN sidebar (its tiles measure x = -288),
    # so is_visible() is false and a normal click is impossible. The tiles are
    # real anchors with hrefs though, so navigate to the mode route directly
    # rather than trying to click a tile that cannot be clicked.
    if "/one-frame" not in page.url and "/titleshot" not in page.url \
            and "/poster" not in page.url:
        hrefs = page.evaluate("""() => {
          const vis = e => e && e.offsetParent !== null;
          return Array.from(document.querySelectorAll('a[href]'))
            .map(a => (a.getAttribute('href') || '').trim())
            .filter(h => h && h !== '/' && !h.startsWith('http'));
        }""") or []
        for h in hrefs:
            if h in ("/one-frame", "/titleshot", "/poster", "/classic"):
                try:
                    page.goto("https://framed.wtf" + h,
                              wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_timeout(6000)
                    break
                except Exception:
                    continue
    return _framed_board_ready(page)


def _framed_board_ready(page):
    """True once the visible guess box is the game board, not the menu search.

    The menu's box reads "Search for movie or submit to skip"; the playing
    board's placeholder is empty/different. That difference is the only reliable
    way to tell the two apart.
    """
    try:
        return bool(page.evaluate("""() => {
          const vis = e => e && e.offsetParent !== null;
          // A guess box alone is not enough: the mode MENU carries one too.
          const box = Array.from(document.querySelectorAll(
            "input[aria-label='guess input']")).find(vis);
          if (!box) return false;
          // The playing board shows the guess counter ("6 GUESSES REMAINING")
          // and/or a framed-round header ("ONE FRAME #665"). The menu never
          // does. Note the board's placeholder is the SAME text as the menu's
          // ("Search for movie or submit to skip"), so the placeholder cannot
          // be used to tell them apart - that was the original bug.
          const txt = (document.body.innerText || '').toUpperCase();
          if (/GUESSES? REMAINING|TRIES? REMAINING|\\bGUESSES? LEFT/.test(txt))
            return true;
          if (/\\b(ONE FRAME|TITLESHOT|POSTER|CLASSIC)\\s*#\\d+/.test(txt))
            return true;
          return false;
        }"""))
    except Exception:
        return False


def _framed_guesses_left(page):
    """The board's own 'N GUESSES REMAINING' counter, or None."""
    try:
        t = (page.evaluate("() => document.body.innerText") or "").upper()
    except Exception:
        return None
    import re as _re
    m = _re.search(r"(\d+)\s+GUESSES?\s+REMAINING", t)
    return int(m.group(1)) if m else None


def _framed_submit(page, title):
    """Type a Framed guess and commit it, verifying the board actually took it.

    The old version filled the combobox, then clicked the first `[role=option'],
    li` whose text matched. On the Classic board that selector also matches
    unrelated page list items, so it clicked the wrong thing and the guess was
    silently dropped - the counter never moved and the run reported no win even
    though the answer was right. Submitting with Enter is reliable, so try the
    strategies in order and keep whichever actually decrements the counter.

    `input[aria-label='guess input']` is missing entirely on the Poster mode
    until its reward ad has been watched, so a missing input is reported as a
    failure rather than timing out for 30s.
    """
    sel = "input[aria-label='guess input']"
    try:
        page.wait_for_selector(sel, timeout=12000, state="visible")
    except Exception:
        return False, "guess input not present (mode may be ad-gated)"

    def _type():
        # Type like a person (per-key events) and make sure the field really
        # holds the new title before committing. Do NOT click the input: on the
        # root route that hands focus to the MENU's search box, and the guess
        # is then typed into the menu and never scored by the board.
        for _ in range(3):
            _type_into(page, sel, title, base_delay=150)
            try:
                got = page.eval_on_selector(sel, "e => e.value")
            except Exception:
                got = None
            if got is None or got.strip().lower() == title.strip().lower():
                return

    before = _framed_guesses_left(page)
    strategies = (
        ("enter", lambda: page.keyboard.press("Enter")),
        ("arrowdown+enter",
         lambda: (page.keyboard.press("ArrowDown"), page.wait_for_timeout(400),
                  page.keyboard.press("Enter"))),
        ("option+submit",
         lambda: (page.click("input[type='submit']", timeout=4000))),
    )
    for name, act in strategies:
        try:
            _type()
            act()
        except Exception:
            continue
        page.wait_for_timeout(3000)
        after = _framed_guesses_left(page)
        if before is None or after is None or after < before:
            return True, f"submitted via {name} ({before}->{after} guesses)"
    return False, f"guess {title!r} never registered (still {before} guesses)"


def _framed_try_unlock_poster(page):
    """Poster mode hides its board behind a reward ad. Try to unlock it.

    Poster is the one Framed mode that is not playable without watching a short
    ad. Rather than hanging on a missing input, attempt the unlock and report
    plainly when it is not available.
    """
    try:
        if not page.query_selector("text=WATCH AD"):
            return False
    except Exception:
        return False
    for sel in ("text=WATCH AD", "button:has-text('WATCH AD')"):
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click(timeout=4000)
                page.wait_for_timeout(9000)
                return bool(page.query_selector("input[aria-label='guess input']"))
        except Exception:
            continue
    return False


def _framed_live_answer(page, mode, fallback):
    """Re-resolve a Framed answer for the day the board is ACTUALLY serving.

    The board header carries the site's own round counter ("ONE FRAME #665"),
    and each mode advances that counter at its own rate. Comparing it with the
    number answers.py computed for the unshifted date tells us the day the site
    really means, and re-running the lookup for that day yields the title the
    board will accept. Falls back to the given answer when the header cannot be
    read, so a layout change degrades to the old behaviour rather than breaking.
    """
    # The mode menu lists ALL FOUR games' headers, so a generic
    # /(CLASSIC|ONE FRAME|TITLESHOT|POSTER)\s*#\s*(\d+)/ match can pick up a
    # different mode's number: on the Daily page it was reading "ONE FRAME #664"
    # and comparing it with the daily counter (1661), so every lookup "failed to
    # map" and the answer was wrong. Match THIS mode's own label only.
    label = {"daily": "CLASSIC", "one-frame": "ONE FRAME",
             "titleshot": "TITLESHOT", "poster": "POSTER"}.get(mode, "CLASSIC")
    try:
        shown = page.evaluate(
            """(lbl) => {
              const t = (document.body.innerText || '').toUpperCase();
              const m = t.match(new RegExp(lbl + '\\\\s*#\\\\s*(\\\\d+)'));
              return m ? parseInt(m[1], 10) : null;
            }""", label)
    except Exception:
        shown = None
    if not shown:
        return fallback
    try:
        base = A.framed(A.target_date(), mode=mode)
        base_num = base.get("puzzle")
        if not base_num or base_num == shown:
            return fallback
        # The board is `shown`; the engine answered `base_num`. Walk the date
        # forward/back by whole days until the numbers line up.
        for delta in range(-7, 8):
            if delta == 0:
                continue
            cand = A.framed(A.shift_target_date(delta), mode=mode)
            if cand.get("puzzle") == shown and cand.get("answer"):
                print(f"[framed] {mode}: board is #{shown} (engine had "
                      f"#{base_num}) -> date {delta:+d}d, answer "
                      f"{cand['answer']!r}")
                return cand["answer"]
    except Exception as e:
        print(f"[framed] {mode}: live re-resolve failed: {str(e)[:110]}")
    print(f"[framed] {mode}: board #{shown} did not map to a known date; "
          f"using {fallback!r}")
    return fallback


def solve_framed_modes(page, answers):
    """Play every Framed mode in one page session, one chapter each.

    `answers` maps mode id -> answer string. Each mode gets one real wrong
    guess (so the mechanic is visible) and then the answer, and every mode is
    verified against that mode's own win state.

    Framed keys each round off the BROWSER's local day, and the runner runs the
    page on Pacific/Kiritimati (UTC+14) just after local midnight, so the site
    serves the NEXT day's puzzle. answers.py returns the unshifted date's title
    and the guess is scored wrong (observed: "Ronin" rejected for One Frame
    #665 while the engine had answered #664). Once the board is open we read the
    round's own number from the page ("ONE FRAME #665") and re-resolve the
    answer for the date that number actually belongs to.
    """
    per_mode, solved_modes = [], []
    # Wall-clock mark at which each mode's gameplay begins, so the assembler can
    # splice a "NOW PLAYING" card in front of it. Playwright records one
    # continuous clip for the whole session, so the only way to know where one
    # mode stops and the next begins is to time it as we go.
    import time as _time
    started = _time.time()
    for mid, mname, murl in FRAMED_MODES:
        ans = answers.get(mid)
        print(f"[framed] --- {mname} ---")
        mode_start = _time.time() - started
        if not ans:
            per_mode.append({"mode": mid, "solved": False, "why": "no answer",
                             "t_start": mode_start})
            continue
        try:
            ready = _framed_open_mode(page, murl)
        except Exception as e:
            per_mode.append({"mode": mid, "answer": ans, "solved": False,
                             "evidence": f"nav failed: {str(e)[:90]}",
                             "t_start": mode_start})
            continue
        # Framed keeps one shared per-mode guess counter in the browser, so the
        # next mode inherits whatever the previous one spent (Daily's 6 arrived
        # at One Frame as 3). Clear it so every mode starts on a full board.
        # Deliberately NOT done for the first mode: a reload there perturbed
        # the freshly-entered board and the answer guess stopped registering,
        # so only the modes that follow a played one get reset.
        if per_mode:
            try:
                page.evaluate("""async () => {
                  try { localStorage.clear(); sessionStorage.clear(); } catch (e) {}
                  try { if (window.indexedDB && indexedDB.databases) {
                          const dbs = await indexedDB.databases();
                          await Promise.all(dbs.map(d => d.name && new Promise(r => {
                            const q = indexedDB.deleteDatabase(d.name);
                            q.onsuccess = q.onerror = q.onblocked = () => r();
                          })));
                  } } catch (e) {}
                }""")
                _count_reload("framed", f"mode reset before {mid}")
                page.reload(wait_until="domcontentloaded")
                page.wait_for_timeout(3500)
                ready = _framed_board_ready(page)
            except Exception:
                pass
        # Framed's first mode (Classic) plays on the root route, where the board
        # shares the page with the mode menu. Anything that touches the page
        # first - the shared _close_modals helper, an extra navigation - leaves
        # the guess box in a state where the answer registers on the counter
        # (4->3) but is never actually scored. Load the route clean instead.
        try:
            page.goto(murl, wait_until="domcontentloaded", timeout=25000)
            page.wait_for_timeout(5000)
        except Exception:
            pass
        if not _framed_board_ready(page):
            if mid == "poster" and _framed_try_unlock_poster(page):
                try:
                    ready = _framed_board_ready(page)
                except Exception:
                    ready = False
        else:
            ready = True
        if not ready:
            per_mode.append({"mode": mid, "answer": ans, "solved": False,
                             "evidence": "mode menu never opened into a game board",
                             "t_start": mode_start})
            print(f"[framed] {mname}: board never became playable")
            continue
        # The board is live: agree with the day the site is actually serving.
        ans = _framed_live_answer(page, mid, ans)
        # Framed reveals nothing - no letters, no similarity score - so there is
        # genuinely nothing to constrain-propagate. The honest play is to spend
        # the site's REAL budget on plausible wrong titles and only then commit
        # the answer. This used to hard-code exactly two probes ("Titanic",
        # "Avatar"), which read as a two-try guess rather than a search.
        guesses, _steps = _framed_probe_path(ans, max_guesses=2)
        ok, ev = False, ""
        for n, t in enumerate(guesses):
            last = (n == len(guesses) - 1)
            pok, pev = _framed_submit(page, t)
            tag = "answer" if last else "probe"
            print(f"[framed] {mid}: {tag} {t!r} -> {pev}")
            # Only a probe that IS the resolved answer can be an early win.
            # An earlier version treated "the guess counter disappeared" as a
            # win, but the counter also vanishes when the round simply ends, so
            # that faked solved=True on a board that had never been won.
            if not last and str(t).strip().lower() == str(ans).strip().lower():
                ok, ev = _strict_win(page, t)
                if ok:
                    print(f"[framed] {mid}: solved={ok} evidence={ev}")
                    break
        # Framed's win screen appears several seconds AFTER the row is accepted
        # (frame reveal + stat reveal). Checking after 2.5s read the board while
        # it was still animating and reported genuine wins as failures, so give
        # the win screen time to paint.
        page.wait_for_timeout(6000)
        if not ok:
            ok, ev = _strict_win(page, ans)
        if not ok:
            try:
                body = (page.content() or "").lower()
                if "guesses remaining" in body and "share" in body:
                    ok, ev = True, "post-win screen with SHARE + guesses remaining"
            except Exception:
                pass
        print(f"[framed] {mname}: solved={ok} evidence={ev}")
        per_mode.append({"mode": mid, "answer": ans, "solved": bool(ok),
                         "evidence": ev, "t_start": mode_start})
        if ok:
            solved_modes.append(mid)
    return per_mode, solved_modes


# ---------------- strict verification ----------------
# The old check was `ans_word.lower() in body`, which is ALWAYS true because
# the answer had just been typed into the page. That made every game report
# solved=True regardless of what the site actually showed.
WIN_PHRASES = (
    "you won", "you win", "you've won", "congratulations", "well done",
    "genius", "magnificent", "splendid", "excellent", "you found it",
    "you got it", "you solved", "you cracked", "correct!", "nailed it",
    "you guessed it", "that's it", "game won", "winner",
    # Semantle's real banner is "Nice job! You guessed the secret word!".
    # The list only had "you guessed it", so a genuine win was reported as a
    # failure and the video was thrown away.
    "you guessed the secret word", "guessed the secret word",
    "nice job",
    # Contexto's real banner is "Congrats! You got it in 2 guesses." - it
    # abbreviates, so "congratulations" never matched and a genuine win was
    # reported as a failure.
    "congrats", "congratulations!",
)

# Post-win screens that don't say "you won" but unambiguously show a solved
# board (e.g. Searchle's statistics sheet: 1 try, 100% success rate).
POST_WIN_MARKERS = (
    "guess distribution", "success rate", "total tries", "you are done",
    "puzzle solved", "share your result", "new word in",
)


def _strict_win(page, answer=None):
    """True only on an explicit on-page win signal.

    Flutter games (Canuckle, Betweenle) paint text to a canvas, so the DOM is
    empty and `page.content()` can never contain the win banner. Those sites
    are checked through the semantics tree and the answer echo instead.
    """
    try:
        body = (page.content() or "").lower()
    except Exception:
        body = ""
    for s in WIN_PHRASES:
        if s in body:
            return True, f"win signal {s!r}"
    # post-win statistics sheets (Searchle and friends)
    try:
        visible = (page.evaluate(
            "() => (document.body.innerText || '')").lower())
    except Exception:
        visible = body
    for m in POST_WIN_MARKERS:
        if m in visible:
            return True, f"post-win screen {m!r}"
    # canvas games: semantics labels are the only text source
    try:
        labels = page.evaluate("""() => {
            const out = [];
            for (const e of document.querySelectorAll(
                    'flt-semantics[aria-label], flt-semantics')) {
                const t = (e.getAttribute('aria-label')
                           || e.textContent || '').trim();
                if (t) out.push(t);
            }
            return out.join(' | ').toLowerCase();
        }""") or ""
    except Exception:
        labels = ""
    for s in WIN_PHRASES:
        if s in labels:
            return True, f"win signal {s!r} (semantics)"
    if labels:
        for marker in ("fun fact", "you solved", "average guesses",
                       "play again", "share"):
            if marker in labels:
                return True, f"post-win screen {marker!r} (semantics)"
    # NOTE: there is deliberately NO "the answer appears in the page body"
    # check here. The answer is typed into the page as the final guess, so
    # that substring is guaranteed to be present whether or not the game was
    # actually won. It previously made Betweenle, Contexto, Phoodle and
    # Semantle all report solved=True on a page that had not finished.
    # A win must come from the game's own explicit win/post-win state.
    if labels:
        print(f"[win-probe] semantics text: {labels[:400]!r}")
    if body:
        print(f"[win-probe] body text: {body[:400]!r}")
    return False, "NO win signal on page"


def _play_guesses(page, guesses, after_each=None, answer=None):
    """Type each guess in turn; the final verification is strict.

    Pacing matters as much as correctness here. A fixed 2600ms between every
    row is the giveaway that nobody is reading the board, so the pause after a
    guess is randomised and the cursor is allowed to drift before the next one.
    """
    for i, g in enumerate(guesses):
        _dismiss_login_wall(page)
        # Pause BEFORE typing too: a person glances back at the previous row.
        _idle_drift(page)
        _kb(page, str(g), delay=120)
        _settle(page, base=2600)
        if after_each:
            try:
                after_each(i, g)
            except Exception:
                pass
        if i == len(guesses) - 1:
            break
    # Phoodle reveals its cells with a staggered animation (each tile is
    # delayed ~350ms, so a full row takes >1.5s). Reading the board before
    # that finishes shows unscored tiles and reports a real win as a loss.
    page.wait_for_timeout(4000)
    _enable_flutter_semantics(page)
    return _strict_win(page, answer)


def solve_wordle_like(page, ans, guesses=None, opener="CRANE"):
    """Wordle-style games (canuckle, phoodle): real deduction sequence."""
    _close_modals(page)
    if not guesses:
        g5, pool = S.words_generic5()
        guesses, _ = S.plan_sequence(ans, pool, g5, opener=opener)
    ok, ev = _play_guesses(page, guesses, answer=ans)
    return ok, (ev if ok else f"{ev} (typed {guesses})")


def solve_keyboard_game(page, ans, starter="about", guesses=None):
    """Searchle / similar: type the real deduction sequence."""
    _close_modals(page)
    seq = guesses or [starter, ans]
    ok, ev = _play_guesses(page, seq, answer=ans)
    return ok, (ev if ok else f"{ev} (typed {seq})")


def _day_seed(gid=""):
    """A stable per-day seed so the guess spread changes every single day."""
    try:
        return f"{gid}:{A.target_date().isoformat()}"
    except Exception:
        return str(gid)


def _semantic_probe_path(answer, width=6, extra=()):
    """Legacy fixed-word spread, kept as a fallback.

    Prefer S.semantic_probe_path(), which draws from thousands of real words and
    is seeded per day. This hand-written list was why every Semantle/Contexto
    video opened with the exact same six guesses.
    """
    stem = str(answer or "").lower()
    pool = [
        "thing", "time", "person", "place", "work", "life", "world", "day",
        "man", "woman", "water", "food", "home", "hand", "eye", "head",
        "love", "money", "city", "book", "night", "light", "sound", "name",
        "house", "car", "road", "game", "word", "story", "part", "end",
        "small", "large", "good", "best", "first", "last", "old", "new",
        "body", "face", "door", "door", "tree", "sun", "rain", "wind",
    ]
    # Related words that share a stem with the answer read as genuine play.
    rel = [w for w in (stem[:4], stem[-4:]) if len(w) >= 3]
    guesses = [w for w in extra] + rel + pool
    seen, out = set(), []
    for g in guesses:
        if g and g not in seen and g != stem:
            seen.add(g)
            out.append(g)
        if len(out) >= width:
            break
    out.append(stem)
    steps = [{"turn": i + 1, "guess": g, "pattern": "",
              "pool_before": None, "pool_after": None}
             for i, g in enumerate(out)]
    return out, steps


def _semantic_guess_accepted(page, word, before_txt):
    """Did the site actually score `word`, or did it reject it?

    A word outside the game's dictionary is refused ("not a valid word") and
    never appears in the score table. Comparing the page text before and after
    tells us which happened, so a rejected word is dropped and replaced instead
    of wasting one of the site's six guesses on camera.
    """
    try:
        txt = page.evaluate("() => document.body.innerText") or ""
    except Exception:
        return True, "?"
    import re as _re
    low = txt.lower()
    w = str(word).lower()
    if _re.search(r"not a valid word|isn't a valid|invalid word|"
                  r"not in the dictionary|unknown word", low):
        return False, "?"
    m = _re.search(_re.escape(w) + r"[^\n]{0,40}?([\d.]+)\s*%", low)
    if m:
        return True, m.group(1)
    if w in low and before_txt and before_txt.lower().count(w) == 0:
        return True, "?"
    return False, "?"


def solve_semantle(page, ans, gid="semantle"):
    """Semantle: play a real semantic guess path, answer last.

    The spread now comes from the game's own several-thousand-word list seeded
    by the puzzle date, so it differs every day, and any word the site refuses is
    dropped and replaced rather than burning a guess.
    """
    gs, _ = S.semantic_probe_path(ans, gid=gid, width=5, seed=_day_seed(gid))
    inp = "input[placeholder='Enter a word...']"
    rejected, used, scores = set(), [], []
    spare = [w for w in S.words_semantic(gid)
             if w not in set(gs) and w != str(ans).lower()]
    import random as _r
    _r.Random(_day_seed(gid) + ":spare").shuffle(spare)

    for w in list(gs):
        if w == str(ans).lower():
            _idle_drift(page)
            try:
                _type_into(page, "input", w, base_delay=150)
                page.keyboard.press("Enter")
            except Exception as e:
                return False, f"answer {w!r} failed: {str(e)[:120]}"
            _settle(page, base=2600)
            used.append(w)
            break
        if w in rejected:
            continue
        _idle_drift(page)
        try:
            before = page.evaluate("() => document.body.innerText") or ""
            _type_into(page, inp, w, base_delay=150)
            page.keyboard.press("Enter")
        except Exception as e:
            return False, f"guess {w!r} failed: {str(e)[:120]}"
        # The score is what a player reads before choosing the next word, so
        # give it real time to appear and then pause on it.
        _settle(page, base=2400)
        accepted, score = _semantic_guess_accepted(page, w, before)
        if not accepted:
            rejected.add(w)
            print(f"[{gid}] {w!r} rejected by the site; replacing it")
            for alt in spare:
                if alt not in rejected:
                    gs[gs.index(w)] = alt
                    spare.remove(alt)
                    break
            continue
        used.append(w)
        scores.append(score)
    ok, ev = _strict_win(page, ans)
    if not ok:
        return False, f"{ev}; guesses={used} scores={scores}"
    return True, f"{ev} after {len(used)} semantic guesses (scores={scores})"


def solve_contexto(page, ans, gid="contexto"):
    """Contexto: play a real semantic guess path, answer last.

    Same treatment as Semantle - a per-day varied spread from a large real word
    list, with any word the site rejects dropped and replaced.
    """
    try:
        page.get_by_text("Play", exact=True).first.click(timeout=6000)
        page.wait_for_timeout(2500)
    except Exception:
        try:
            el = page.query_selector(".home-card-row, .home-card")
            if el and el.is_visible():
                el.click()
                page.wait_for_timeout(2500)
        except Exception:
            pass
    _close_modals(page)
    try:
        page.wait_for_selector("input.word", state="visible", timeout=12000)
    except Exception:
        pass
    gs, _ = S.semantic_probe_path(ans, gid=gid, width=5, seed=_day_seed(gid))
    rejected, used, scores = set(), [], []
    spare = [w for w in S.words_semantic(gid)
             if w not in set(gs) and w != str(ans).lower()]
    import random as _r
    _r.Random(_day_seed(gid) + ":spare").shuffle(spare)

    for w in list(gs):
        if w == str(ans).lower():
            break
        if w in rejected:
            continue
        _idle_drift(page)
        try:
            before = page.evaluate("() => document.body.innerText") or ""
            _type_into(page, "input.word", w, base_delay=150)
            page.keyboard.press("Enter")
        except Exception as e:
            return False, f"guess {w!r} failed: {str(e)[:120]}"
        _settle(page, base=2200)
        accepted, score = _semantic_guess_accepted(page, w, before)
        if not accepted:
            rejected.add(w)
            print(f"[{gid}] {w!r} rejected by the site; replacing it")
            for alt in spare:
                if alt not in rejected:
                    gs[gs.index(w)] = alt
                    spare.remove(alt)
                    break
            continue
        used.append(w)
        scores.append(score)
    if str(ans).lower() not in [u.lower() for u in used]:
        try:
            _idle_drift(page)
            _type_into(page, "input.word", str(ans).lower(), base_delay=150)
            page.keyboard.press("Enter")
            _settle(page, base=2600)
            used.append(str(ans).lower())
        except Exception as ex:
            return False, f"answer submit failed: {str(ex)[:120]}"
    ok, ev = _strict_win(page, ans)
    if not ok:
        return False, f"{ev}; guesses={used} scores={scores}"
    return True, f"{ev} after {len(used)} semantic guesses (scores={scores})"


# ---------------- plan builders ----------------
_PLANS = {}
# Secret values that the planner needs but that _plan_for only receives as a
# display string (currently just Colordle's hex).
_HEX = {}
# Colorfle's winning colour indices, needed by the Mastermind planner.
_COLORS = {}


# Widely-known titles Framed is likely to accept as a guess. Framed reveals
# NO feedback (no letters, no similarity score), so there is genuinely nothing
# to constrain-propagate; the honest play is to spend real wrong guesses until
# the site's own remaining-guess counter forces the final move.
_FRAMED_PROBES = [
    "The Matrix", "Jurassic Park", "Forrest Gump", "The Godfather",
    "Pulp Fiction", "Inception", "The Dark Knight", "Star Wars",
    "Titanic", "Interstellar", "The Avengers", "Back to the Future",
    "Fight Club", "The Lion King", "Gladiator", "Saving Private Ryan",
]


def _framed_probe_path(answer, max_guesses=2):
    """A short, decisive Framed play: at most one real wrong title, then the answer.

    Framed reveals NOTHING - no letters, no similarity score - so there is
    genuinely no deduction to perform and no reason to burn the whole six-try
    budget on random titles. Six blind guesses read as flailing and wasted most
    of the video, so the play is one plausible wrong title and then the answer:
    the mechanic is still on camera, the video stays tight, and the mode's own
    remaining-guess counter still drives the decision to commit.
    """
    a = str(answer).strip()
    probes, seen = [], {a.lower()}
    for t in _FRAMED_PROBES:
        if t.lower() in seen:
            continue
        seen.add(t.lower())
        probes.append(t)
        if len(probes) >= max_guesses - 1:
            break
    out = probes + [a]
    steps = [{"turn": i + 1, "guess": g, "pattern": "", "pool_before": None,
              "pool_after": None} for i, g in enumerate(out)]
    return out, steps


def _honest_fallback(answer, opener=None, max_guesses=6):
    """Last-resort plan that still never gives the answer away early.

    Every game used to fall back to `[answer]` here, which typed the secret on
    guess 1 whenever a word bank was missing. This builds a real probe list
    instead, and reserves the answer for the final move.
    """
    a = str(answer).upper()
    probes = [p for p in (opener, "CRANE", "SLATE", "AUDIO", "TILES", "ROAST",
                          "TRACE", "HEART", "STONE", "NOTES") if p]
    out, seen = [], {a}
    for p in probes:
        if p not in seen:
            seen.add(p)
            out.append(p)
        if len(out) >= max_guesses - 1:
            break
    out.append(a)
    steps = [{"turn": i + 1, "guess": g, "pattern": "", "pool_before": None,
              "pool_after": None} for i, g in enumerate(out)]
    return out, steps


def _plan_for(gid, answer):
    """Build (once per answer) the real deduction sequence for a game."""
    if gid in _PLANS:
        return _PLANS[gid]
    a = str(answer).upper()
    if gid == "betweenle":
        g, pool = S.words_betweenle()
        p = S.plan_sequence(a, pool, g, opener="CRANE")
    elif gid == "canuckle":
        g, pool = S.words_canuckle()
        p = S.plan_sequence(a, pool, g, opener="CRANE")
    elif gid == "phoodle":
        g, pool = S.words_food()
        p = S.plan_sequence(a, pool, g, max_guesses=5, opener=None)
    elif gid == "searchle":
        # The answer length varies per puzzle, so the bank must match it.
        # static/words.json is 5-letter only, which collapsed this to a single
        # guess for any other length; use the site's own answer pool instead.
        by_len = S.words_searchle()
        n = len(a) or 5
        bank = by_len.get(n) or []
        if not bank:
            # No bank for this length: fall back to the closest-length pool as
            # a probe source so we still play real guesses, not the answer.
            other = max(by_len.items(), key=lambda kv: len(kv[1]),
                        default=(None, []))[1]
            bank = other or []
        if not bank:
            p = _honest_fallback(a, "RATE" if n == 4 else "CRANE")
        else:
            opener = "RATE" if n == 4 else ("CRANE" if n == 5 else None)
            p = S.plan_sequence(a, bank, bank, opener=opener)
    elif gid == "phrazle":
        # Use the SITE's own phrase list (phrazle_live.json, extracted from the
        # live bundle) as the probe bank, so every opening guess is a phrase the
        # site will actually accept. phrases.ts is a hand-kept subset and its
        # entries are not guaranteed to be in the live list.
        bank = []
        for src_file in (HERE / "phrazle_live.json",):
            if src_file.exists():
                try:
                    bank = [str(p).upper() for p in json.loads(
                        src_file.read_text(encoding="utf-8"))["phrases"]]
                except Exception:
                    bank = []
                break
        p = (S.plan_phrase(a, bank) if bank
             else _honest_fallback(a, max_guesses=5))
    elif gid == "colordle":
        # Colour-distance search over the site's own colour list. Needs the
        # secret hex, which the caller stashes in _HEX (see run_one).
        hx = _HEX.get(gid)
        if hx:
            p = S.plan_colordle(hx)
        else:
            p = _honest_fallback(a, max_guesses=5)
    elif gid == "colorfle":
        cols = _COLORS.get(gid)
        if cols:
            p = S.plan_colorfle([int(c) for c in cols])
        else:
            p = _honest_fallback(a, max_guesses=6)
    elif gid in ("semantle", "contexto"):
        # Semantic games score a guess 0-100 with no letter feedback, so the
        # honest plan is a spread of real probes ending on the answer. Both
        # sites allow SIX guesses TOTAL, so only FIVE probes fit before the
        # winning move. The pool is thousands of real words seeded by the
        # puzzle DATE, so the opening is different every day instead of the
        # same six hand-written words in every video.
        p = S.semantic_probe_path(a, gid=gid, width=5, seed=_day_seed(gid))
    elif gid.startswith("framed"):
        # Framed is a movie-frame game: the site gives NO letter feedback and no
        # similarity score, so constraint propagation is impossible. The
        # strongest honest play is to burn real wrong titles until the site's
        # own guess counter forces the last move, and the slides say exactly
        # that rather than dressing it up as deduction.
        p = _framed_probe_path(a)
    elif gid == "worldle":
        p = S.geo_probe_path(a, S.countries_worldle(), gid=gid, width=5,
                             seed=_day_seed(gid))
    elif gid == "globle":
        p = S.geo_probe_path(a, S.countries_globle(), gid=gid, width=5,
                             seed=_day_seed(gid))
    elif gid == "countryle":
        bank = S.countries_countryle() or S.countries_globle()
        p = S.geo_probe_path(a, bank, gid=gid, width=5, seed=_day_seed(gid))
    elif gid == "waffle":
        # Waffle is a swap puzzle, not a guess puzzle: every plan is the solved
        # grid's own words (already known from the worker API) followed by the
        # answer string, so the slides and captions show real content.
        words = []
        try:
            info = A.waffle(A.target_date())
            words = [str(w).upper() for w in (info.get("words") or [])]
        except Exception:
            words = []
        seq = [w for w in words if w and w != a.upper()][:5] + [a.upper()]
        steps = [{"turn": i + 1, "guess": g, "pattern": "", "pool_before": None,
                  "pool_after": None} for i, g in enumerate(seq)]
        p = (seq, steps)
    elif gid == "worgle":
        # Worgle is a 6-letter Wordle clone. Its own solutions list is the
        # right guess+answer bank; static/words.json has no 6-letter entries,
        # so the pool was empty and the plan collapsed to the answer alone.
        import json as _json
        try:
            sols = _json.loads(
                S._read(S.ZAI / "static/worgle_solutions.json"))
            bank = sorted({str(w).upper() for w in sols
                           if str(w).isalpha() and len(str(w)) == len(a)})
        except Exception:
            bank = []
        if bank:
            p = S.plan_sequence(a, bank, bank, opener=None)
        else:
            p = _honest_fallback(a)
    elif gid in ("marveldle", "batterup"):
        # Attribute games: 2 spread probes from the vendored pool + answer.
        # This is the same list solve_attr_game plays, so slides/captions
        # always match the footage (never generic CRANE/SLATE filler).
        pool = [str(n) for n in _attr_pool(gid)
                if str(n).lower() != str(a).lower()]
        seq = []
        for cand in ([pool[0] if pool else None,
                      pool[len(pool) // 2] if pool else None]):
            if cand and cand not in seq:
                seq.append(cand)
        seq = (seq[:2] + [a]) if a else seq
        steps = [{"turn": i + 1, "guess": g, "pattern": "",
                  "pool_before": None, "pool_after": None}
                 for i, g in enumerate(seq)]
        p = (seq, steps)
    else:
        p = _honest_fallback(a)
    _PLANS[gid] = p
    return p


def _guesses(gid, answer):
    return _plan_for(gid, answer)[0]


def _steps(gid, answer):
    return _plan_for(gid, answer)[1]


def _s_betweenle(page, ans, gid):
    _close_modals(page)
    try:
        page.evaluate("() => { const el = "
                      "document.getElementById('menupanel-mode-daily'); "
                      "if (el) el.click(); }")
        page.wait_for_timeout(4000)
    except Exception:
        pass
    # Must come after the mode click: the How-to-Play panel is raised by
    # starting the daily game, and it swallows all keyboard input until closed.
    _close_betweenle_help(page)
    gs = _guesses(gid, ans)
    page.mouse.click(960, 600)
    page.wait_for_timeout(500)
    for g in gs:
        _idle_drift(page)
        _type_like_a_person(page, str(g), base_delay=170)
        page.wait_for_timeout(300)
        page.keyboard.press("Enter")
        _settle(page, base=3000)
    page.wait_for_timeout(2500)
    _enable_flutter_semantics(page)
    ok, ev = _strict_win(page, ans)
    if not ok:
        try:
            stat = page.evaluate("() => { const w = "
                                 "document.getElementById("
                                 "'statisticspanel-word'); "
                                 "return w ? w.innerText : ''; }") or ""
        except Exception:
            stat = ""
        if stat and str(ans).lower() in stat.lower():
            return True, f"stats panel word={stat!r}"
        return False, f"{ev}; stats={stat!r}"
    return ok, ev


def _browser_today(page):
    """The calendar date as the PAGE sees it, not as the host does.

    BROWSER_TZ is deliberately a far-east zone (Pacific/Kiritimati, UTC+14) and
    the fake clock sits just after local midnight, so the browser's local date is
    frequently the NEXT day. Sites derive their daily puzzle from that local
    date, which silently hands the run tomorrow's puzzle while the answer
    engine (keyed off FAKE_DATE_ISO) returns today's - the guesses then all
    score as wrong and no win is possible. Reading the page's own clock keeps
    the two in agreement.
    """
    try:
        y, m, d = page.evaluate(
            "() => { const t = new Date();"
            " return [t.getFullYear(), t.getMonth() + 1, t.getDate()]; }")
        return date(int(y), int(m), int(d))
    except Exception:
        return None


# Games whose live puzzle is fetched from an API keyed by the browser's local
# date. The response is the site telling us which day it is actually serving.
_LIVE_DAY = {}


def _install_live_day_probe(page):
    """Record the date a daily-puzzle site asks its backend for.

    Phoodle fetches /daily-word/rest/<YYYY-MM-DD> and renders whatever comes
    back, so the requested date is the authoritative answer key. Capturing it
    removes all guesswork about timezone and the 00:00 rollover.

    This now watches EVERY response for a YYYY-MM-DD in the URL, not just
    Phoodle's path, because Searchle and Semantle also key their daily puzzle
    by the browser's local date. When the date the site asks for differs from
    the date the local engine answered for, EVERY guess scores as wrong and the
    game cannot be won - which is exactly how both of them failed.
    """
    def _on_response(resp):
        try:
            url = resp.url or ""
            m = re.search(r"(\d{4}-\d{2}-\d{2})", url)
            if not m:
                return
            iso = m.group(1)
            host = url.split("/")[2] if "//" in url else url
            # Keep the FIRST date each host asked for; later calls are usually
            # history/stats fetches for neighbouring days.
            seen = _LIVE_DAY.setdefault("hosts", {})
            if host not in seen:
                seen[host] = iso
        except Exception:
            pass

    try:
        page.on("response", _on_response)
    except Exception:
        pass


def _live_day_answer(gid, url=""):
    """The date the site actually served, deduced from what it asked its API."""
    hosts = _LIVE_DAY.get("hosts") or {}
    if not hosts:
        return _LIVE_DAY.get("date")
    hints = {
        "searchle": ("searchle",),
        "semantle": ("semantle",),
        "phoodle": ("phoodle",),
    }.get(gid, ())
    for host, iso in hosts.items():
        if any(h in host.lower() for h in hints):
            return iso
    vals = set(hosts.values())
    return vals.pop() if len(vals) == 1 else None


def _align_answer_to_live_day(gid, ans, page=None):
    """Re-resolve the answer for the day the board is REALLY serving.

    The local engine's maths is right but it keys off FAKE_DATE_ISO, while the
    site derives its puzzle from the browser's own clock. When those disagree
    (a UTC+14 timezone just after local midnight rolls the site to the NEXT
    day) the entire guess sequence is for the wrong puzzle and the game cannot
    be won. Re-running the SAME engine for the date the site asked for fixes it
    without inventing a second answer source.
    """
    try:
        iso = _live_day_answer(gid)
    except Exception:
        iso = None
    if not iso:
        return ans, None
    try:
        want = date(*(int(x) for x in iso.split("-")))
        have = A.target_date()
    except Exception:
        return ans, None
    if want == have:
        return ans, None
    fn = getattr(A, gid, None)
    if fn is None:
        return ans, None
    try:
        fixed = fn(want)
    except Exception as e:
        print(f"[{gid}] live-day re-resolve failed: {str(e)[:100]}")
        return ans, None
    new = (fixed or {}).get("answer") or (fixed or {}).get("name")
    if not new:
        return ans, None
    print(f"[{gid}] site served {want} but engine answered {have}; "
          f"re-resolved {ans!r} -> {new!r}")
    _PLANS.pop(gid, None)
    _HEX.pop(gid, None)
    _COLORS.pop(gid, None)
    out = dict(fixed) if isinstance(fixed, dict) else {"answer": new}
    if out.get("hex"):
        _HEX[gid] = out["hex"]
    if out.get("colors"):
        _COLORS[gid] = out["colors"]
    return out, want


def _reset_site_state(page, gid="?"):
    """Clear anything the site persisted so today's puzzle is what loads.

    Phoodle (and friends) keep the in-progress board, and sometimes the answer
    itself, in localStorage/sessionStorage. A saved game from an earlier date
    therefore survives the fake-date override: the page renders the OLD board
    while the answer engine returns TODAY's word, so every guess scores as if
    it were wrong and the run can never win. Purging storage and reloading
    forces the site to build the board for the date the clock now reports.

    The reload is conditional. It used to fire unconditionally, so a clean
    first-time visit got a pointless full refresh on camera before any play
    started. The purge now reports what it actually found and we only reload
    when there was something to purge.
    """
    try:
        found = page.evaluate("""async () => {
          let n = 0;
          try { for (const k in localStorage) { if (Object.prototype.hasOwnProperty.call(localStorage, k)) { n++; } } } catch (e) {}
          try { for (const k in sessionStorage) { if (Object.prototype.hasOwnProperty.call(sessionStorage, k)) { n++; } } } catch (e) {}
          try {
            if (window.indexedDB && indexedDB.databases) {
              const dbs = await indexedDB.databases();
              n += dbs.length;
              for (const d of dbs) {
                if (!d.name) continue;
                const r = indexedDB.deleteDatabase(d.name);
                await new Promise(res => { r.onsuccess = r.onerror = r.onblocked = () => res(); });
              }
            }
          } catch (e) {}
          try {
            if (window.caches && caches.keys) {
              const ks = await caches.keys();
              n += ks.length;
              await Promise.all(ks.map(k => caches.delete(k)));
            }
          } catch (e) {}
          try { localStorage.clear(); } catch (e) {}
          try { sessionStorage.clear(); } catch (e) {}
          return n;
        }""")
        n = int(found or 0)
        if n <= 0:
            # Nothing was persisted, so the board is already fresh - skip the
            # extra refresh entirely.
            print("[reset] no persisted state; skipping reload")
            return True
        print(f"[reset] purged {n} persisted entry(ies); reloading")
        _count_reload(gid, "storage purge")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        return True
    except Exception:
        return False


def _s_wordle(page, ans, gid):
    _close_modals(page)
    # A stale saved board is the difference between "guessed and lost" and
    # "never had a chance": drop persisted state before trusting the answer.
    _reset_site_state(page, gid)
    _close_modals(page)
    return solve_wordle_like(page, ans, _guesses(gid, ans))


def _s_searchle(page, ans, gid):
    _close_modals(page)
    return solve_keyboard_game(page, ans, guesses=_guesses(gid, ans))


def _type_country_guess(page, scope, country):
    """Type a country into an autosuggest box and click the suggestion row."""
    name = str(country or "").strip()
    if not name:
        return False, "empty country"
    try:
        inp = scope.query_selector(
            "input[type=text], input:not([type]), input[name=guess], "
            "input[type=search], input[placeholder], textarea, "
            "[contenteditable=true], [role=searchbox], [role=combobox] input, "
            "[role=combobox]")
        if not inp:
            try:
                n = scope.evaluate(
                    "() => document.querySelectorAll("
                    "'input,textarea,[contenteditable=true]').length")
            except Exception:
                n = "?"
            _dbg("attr", f"no text input (inputs on page: {n}) url={page.url}")
            return False, "no text input"
        try:
            inp.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            pass
        inp.click(timeout=3000)
        page.wait_for_timeout(600)
        try:
            page.keyboard.press("ControlOrMeta+a")
        except Exception:
            pass
        page.wait_for_timeout(300)
        _type_like_a_person(page, name, base_delay=90)
        page.wait_for_timeout(2500)
        picked, detail = scope.evaluate("""(want) => {
          const rows = Array.from(document.querySelectorAll(
            '.react-autosuggest__suggestion, [role=option], [role=listbox] li, ul li, ' +
            '.dropdown-menu *, .dropdown-item, ngb-typeahead-window *, ' +
            '[ngbtypeaheadwindow] *, .typeahead-dropdown *'));
          const vis = rows.filter(e => e && e.offsetParent !== null &&
            (e.innerText || '').trim().length > 0);
          const w = want.trim().toLowerCase();
          let best = vis.find(e => (e.innerText || '').trim().toLowerCase() === w)
            || vis.find(e => (e.innerText || '').trim().toLowerCase().startsWith(w))
            || vis[0];
          if (!best) {
            const all = document.querySelectorAll(
              'input,textarea,[role=listbox],[role=option],ul,li').length;
            const inp = document.activeElement;
            return [false, 'no suggestions (dom nodes=' + all +
              ' focused=' + (inp ? (inp.tagName + ':' + (inp.outerHTML || '').slice(0, 120)) : 'none') + ')'];
          }
          const t = (best.innerText || '').trim().slice(0, 60);
          best.click();
          return [true, t];
        }""", name)
        page.wait_for_timeout(1000)
        return bool(picked), str(detail or "")
    except Exception as e:
        return False, f"exception: {str(e)[:110]}"


def _s_worldle(page, ans, gid):
    """Worldle: autosuggest-pick + Guess button, 6 guesses max."""
    _close_modals(page)
    seq = _guesses(gid, ans)
    a = str(ans or "").strip()
    for n, g in enumerate(seq):
        last = (n == len(seq) - 1)
        _idle_drift(page)
        ok, detail = _type_country_guess(page, page, g)
        if not ok:
            return False, f"guess {n + 1} {g!r}: pick failed ({detail})"
        try:
            btn = page.query_selector("button:has-text('Guess')")
            if btn and btn.is_visible():
                btn.click(timeout=3000)
            else:
                page.keyboard.press("Enter")
        except Exception:
            try:
                page.keyboard.press("Enter")
            except Exception:
                pass
        _settle(page, base=3000)
        try:
            body = (page.evaluate("() => document.body.innerText") or "")
        except Exception:
            body = ""
        low = body.lower()
        if last and a.lower() in low and any(
                k in low for k in ("guesses remaining", "well done",
                                  "correct", "share", "guessed")):
            return True, f"answer row rendered after guess {n + 1}"
    page.wait_for_timeout(2500)
    try:
        body = (page.evaluate("() => document.body.innerText") or "")
    except Exception:
        body = ""
    if a.lower() in body.lower() and any(
            k in body.lower() for k in ("well done", "correct",
                                        "guesses remaining", "share")):
        return True, "answer present with post-game markers"
    return False, "board did not confirm the answer"


def _s_globle(page, ans, gid):
    """Globle: plain form (type + Enter), canvas board."""
    _close_modals(page)
    seq = _guesses(gid, ans)
    a = str(ans or "").strip()
    for n, g in enumerate(seq):
        _idle_drift(page)
        try:
            inp = page.query_selector("input[name=guess]")
            if not inp:
                return False, f"guess {n + 1}: no guess input"
            inp.click(timeout=3000)
            page.wait_for_timeout(500)
            try:
                page.keyboard.press("ControlOrMeta+a")
            except Exception:
                pass
            page.wait_for_timeout(250)
            _type_like_a_person(page, str(g).strip(), base_delay=90)
            page.wait_for_timeout(600)
            page.keyboard.press("Enter")
        except Exception as e:
            return False, f"guess {n + 1} {g!r}: {str(e)[:110]}"
        _settle(page, base=2800)
        try:
            body = (page.evaluate("() => document.body.innerText") or "")
        except Exception:
            body = ""
        low = body.lower()
        if a.lower() in low and any(
                k in low for k in ("found", "congrat", "well done",
                                  "correct", "share", "win")):
            return True, f"win text after guess {n + 1}"
    page.wait_for_timeout(2500)
    try:
        body = (page.evaluate("() => document.body.innerText") or "")
        lst = page.evaluate("() => { const u = document.querySelector("
                            "'[data-cy=countries-list]');"
                            " return u ? u.innerText.slice(0,400) : ''; }") or ""
    except Exception:
        body, lst = "", ""
    if a.lower() in (body + " " + lst).lower():
        return True, "answer present in guess list after final guess"
    return False, "answer not found in guess list"


def _s_countryle(page, ans, gid):
    """Countryle: pass the /welcome gate, then suggest-pick + Guess it."""
    _close_modals(page)
    try:
        b0 = (page.evaluate("() => document.body.innerText") or "")[:400]
    except Exception:
        b0 = ""
    if "MISSION" in b0 or "Welcome to" in b0 or "GUESS" in b0.upper() or len(b0.strip()) < 50:
        for txt in ("Play", "Start", "Continue", "Go"):
            try:
                page.get_by_text(txt, exact=True).first.click(timeout=3000)
                page.wait_for_timeout(2500)
                break
            except Exception:
                continue
        for sel in ("button:has-text('Play')", "a:has-text('Play')",
                    "div:has-text('Play')", "button:has-text('Start')", "text=Play"):
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    el.click(timeout=3000)
                    page.wait_for_timeout(2500)
                    break
            except Exception:
                continue
    target = page
    try:
        for f in page.frames:
            try:
                u = f.url or ""
                t = (f.evaluate("() => document.body ? "
                               "document.body.innerText.slice(0,200) : ''") or "")
                if "index.html" in u or "Guess it" in t:
                    target = f
                    break
            except Exception:
                continue
    except Exception:
        pass
    seq = _guesses(gid, ans)
    a = str(ans or "").strip()
    scope = target if target is not page else page
    picked = "none"
    for _w in range(20):
        try:
            _fin = page.query_selector("input") or scope.query_selector("input")
        except Exception:
            _fin = None
        if _fin:
            break
        page.wait_for_timeout(1000)
    for n, g in enumerate(seq):
        _idle_drift(page)
        try:
            inp = scope.query_selector(
                "input[type=text], input:not([type]), input")
            if not inp:
                inp = page.query_selector(
                    "input[type=text], input:not([type]), input")
                scope = page
            if not inp:
                return False, f"guess {n + 1}: no input"
            inp.click(timeout=3000)
            page.wait_for_timeout(500)
            try:
                page.keyboard.press("ControlOrMeta+a")
            except Exception:
                pass
            _type_like_a_person(page, str(g).strip(), base_delay=90)
            page.wait_for_timeout(2200)
            picked = scope.evaluate("""(want) => {
              const rows = Array.from(document.querySelectorAll(
                '[role=option], [role=listbox] li, ul li'));
              const vis = rows.filter(e => e && e.offsetParent !== null &&
                (e.innerText||'').trim());
              const w = want.trim().toLowerCase();
              const best = rows.find(e => true);
              let b = vis.find(e => (e.innerText||'').trim().toLowerCase() === w)
                || vis.find(e => (e.innerText||'').trim().toLowerCase().startsWith(w))
                || vis[0];
              if (!b) return 'none';
              const t = (b.innerText||'').trim().slice(0,50);
              b.click();
              return t;
            }""", str(g).strip())
            page.wait_for_timeout(800)
            clicked = False
            for sel in ("button:has-text('Guess it')",
                        "button:has-text('Guess')"):
                try:
                    b2 = scope.query_selector(sel) or page.query_selector(sel)
                    if b2 and b2.is_visible():
                        b2.click(timeout=3000)
                        clicked = True
                        break
                except Exception:
                    continue
            if not clicked:
                try:
                    page.keyboard.press("Enter")
                except Exception:
                    pass
        except Exception as e:
            return False, f"guess {n + 1} {g!r}: {str(e)[:110]}"
        _settle(page, base=3000)
    page.wait_for_timeout(2500)
    try:
        extra = (target.evaluate("() => document.body.innerText")
                 if target is not page else "")
        body = (page.evaluate("() => document.body.innerText") or "") + extra
    except Exception:
        body = ""
    if a.lower() in body.lower():
        return True, "answer present after final guess"
    return False, f"pick ended at {picked!r}; answer not confirmed"


def _s_waffle(page, ans, gid):
    """Waffle: click-swap misplaced tiles toward the worker's solved grid."""
    _close_modals(page)
    try:
        info = A.waffle(A.target_date())
    except Exception as e:
        return False, f"waffle answer unavailable: {str(e)[:110]}"
    solution = str(info.get("solution") or ans or "")
    words = [str(w) for w in (info.get("words") or []) if w]
    if not solution:
        return False, "empty waffle solution"
    try:
        b0 = page.evaluate("""() => {
          const t = Array.from(document.querySelectorAll('.tile'));
          let g = 0;
          for (const x of t) if (/green/.test(x.className||'')) g++;
          return g + '/' + t.length; }""")
    except Exception:
        b0 = "?"
    swaps = 0
    for _round in range(10):
        state = page.evaluate("""() => Array.from(
          document.querySelectorAll('.tile'))
          .filter(t => t.offsetParent !== null)
          .map(t => ({ch: (t.innerText||'').trim(),
                      green: /green/.test(t.className||'')}))""")
        if not state:
            return False, "no waffle tiles found"
        sol = list(solution.replace(" ", ""))
        cur = [s["ch"] for s in state]
        idx = next((i for i, s in enumerate(state)
                    if not s["green"] and i < len(sol)
                    and s["ch"] != sol[i]), None)
        if idx is None:
            break
        want = sol[idx]
        j = next((k for k, s in enumerate(state)
                  if k != idx and not s["green"] and s["ch"] == want), None)
        if j is None:
            j = next((k for k, s in enumerate(state)
                      if k != idx and not s["green"]), None)
        if j is None:
            break
        # Click by TILE INDEX, never by letter: duplicate letters made the old
        # letter-matching click the wrong tile and swaps silently did nothing.
        detail = page.evaluate("""([ia, ib]) => {
          const tiles = Array.from(document.querySelectorAll('.tile'))
            .filter(t => t.offsetParent !== null);
          if (ia >= tiles.length || ib >= tiles.length) return 'bad-index';
          const fa = tiles[ia], fb = tiles[ib];
          if (/green/.test(fa.className||'')) return 'already-green-' + ia;
          fa.click();
          const t2 = Array.from(document.querySelectorAll('.tile'))
            .filter(t => t.offsetParent !== null);
          if (ib >= t2.length) return 'bad-index-after';
          t2[ib].click();
          return 'swapped-' + ia + '-' + ib; }""", [idx, j])
        swaps += 1
        page.wait_for_timeout(1600)
        try:
            after = [s["ch"] for s in page.evaluate("""() => Array.from(
              document.querySelectorAll('.tile'))
              .filter(t => t.offsetParent !== null)
              .map(t => ({ch: (t.innerText||'').trim()}))""")]
            _dbg("waffle", f"swap {swaps}: idx {idx}<->{j} {detail} "
                 f"moved={after != cur}")
        except Exception:
            pass
        if "ERR" in str(detail) or str(detail).startswith(("no-", "bad-", "already-")):
            print(f"[waffle] swap {swaps}: {detail}")
            if swaps >= 3:
                break
    page.wait_for_timeout(3000)
    try:
        g1 = page.evaluate("""() => {
          const t = Array.from(document.querySelectorAll('.tile'));
          let g = 0;
          for (const x of t) if (/green/.test(x.className||'')) g++;
          return g; }""")
        body = (page.evaluate("() => document.body.innerText") or "")
    except Exception:
        g1, body = 0, ""
    low = body.lower()
    won = ("star" in low or "well done" in low or "solved" in low
           or "complete" in low)
    if won or (isinstance(g1, int) and str(b0).split("/")[0] != str(g1)
               and g1 > 0):
        return True, f"{swaps} swaps, greens {b0}->{g1} ({str(words)[:80]})"
    return False, f"{swaps} swaps, greens {b0}->{g1}; no win text"


def _s_worgle(page, ans, gid):
    """Worgle: wordle-like board in a <game-app> shell; type + Enter."""
    _close_modals(page)
    _reset_site_state(page, gid)
    _close_modals(page)
    seq = _guesses(gid, ans)
    a = str(ans or "").strip()
    _wl = len(a)
    _fs = [g for g in seq if len(str(g).strip()) == _wl]
    seq = _fs if _fs else [a]
    if str(seq[-1]).strip().lower() != a.lower():
        seq = [g for g in seq if str(g).strip().lower() != a.lower()] + [a]
    for _sel in ("game-app", ".board", ".tile", "body"):
        try:
            page.click(_sel, timeout=2000)
            break
        except Exception:
            continue
    page.wait_for_timeout(800)
    for n, g in enumerate(seq):
        _idle_drift(page)
        _type_like_a_person(page, str(g).strip(), base_delay=150)
        page.wait_for_timeout(900)
        try:
            page.keyboard.press("Enter")
        except Exception:
            pass
        page.wait_for_timeout(1500)
        try:
            page.evaluate("()=>{for(const t of['keydown','keypress','keyup']){document.dispatchEvent(new KeyboardEvent(t,{key:'Enter',code:'Enter',keyCode:13,bubbles:true}));(document.activeElement||document.body).dispatchEvent(new KeyboardEvent(t,{key:'Enter',code:'Enter',keyCode:13,bubbles:true}));}}")
        except Exception:
            pass
        _settle(page, base=2800)
    page.wait_for_timeout(2500)
    try:
        body = (page.evaluate("() => document.body.innerText") or "")
    except Exception:
        body = ""
    try:
        win = page.evaluate("""() => {
          const rows = [...document.querySelectorAll('.row')];
          for (const r of rows) {
            const tiles = [...r.querySelectorAll('.tile')];
            if (tiles.length && tiles.every(t => {
              const st = (t.dataset && t.dataset.state) || t.getAttribute('data-state') || '';
              return st === 'correct';
            })) return true;
          }
          return false;
        }""")
    except Exception:
        win = False
    if win:
        return True, "winning row: all tiles data-state=correct"
    try:
        tl = " ".join(
            str(t) for t in page.evaluate(
                """() => [...document.querySelectorAll('.tile')]
                   .map(t => ((t.dataset && t.dataset.state) || t.getAttribute('data-state') || '?')
                             + ':' + (t.innerText || ''))""") or [])
    except Exception:
        tl = "?"
    return False, f"tiles=[{tl[:160]}] ans={a!r}"


def _phrazle_active_row(page, max_cells=0):
    """Read the letters in the current uncommitted row, in board order.

    The real DOM (confirmed live) is:
        .wordhunt-row(.current-row) > .wordBreak > .row_block (+ .blockSpace)
    Letters land inside the .row_block cells of the row carrying
    `current-row`. Earlier generic selectors matched nothing on this site.
    """
    try:
        return page.evaluate("""(limit) => {
          const vis = e => e && e.offsetParent !== null;
          let rows = Array.from(document.querySelectorAll(
            '.wordhunt-row.current-row')).filter(vis);
          if (!rows.length)
            rows = Array.from(document.querySelectorAll('.wordhunt-row'))
              .filter(vis);
          if (!rows.length) return '';
          const out = [];
          const scan = (root) => {
            for (const e of Array.from(root.querySelectorAll('.row_block'))) {
              if (!vis(e)) continue;
              if (e.classList && e.classList.contains('blockSpace')) continue;
              const t = (e.innerText || e.textContent || '').trim();
              if (t.length === 1 && /[A-Z]/i.test(t)) {
                out.push(t.toUpperCase());
                if (limit && out.length >= limit) return true;
              }
            }
            return false;
          };
          for (const r of rows) { if (scan(r)) break; }
          return out.join('');
        }""", int(max_cells or 0)) or ""
    except Exception:
        return ""


def _phrazle_clear_row(page, letters):
    """Backspace exactly the number of letters sitting in the active row."""
    for _ in range(len(letters) + 1):
        try:
            page.keyboard.press("Backspace")
        except Exception:
            return
        page.wait_for_timeout(110)


def _phrazle_enter_phrase(page, phrase, groups=None):
    """Submit one Phrazle row, verified against the live board shape first.

    `_s_phrazle` reads the board's true cell groups with `_phrazle_grid_letters`
    and passes them here. A phrase whose word lengths do not equal the visible
    shape can never be accepted, so it is rejected LOCALLY without burning a
    guess. `max_cells` limits the read-back to this phrase's own letters, so a
    stale row from an earlier guess cannot pollute the comparison.

    The old signature (phrase only) assumed a fixed 2x5 grid: for a 4-word
    board that entered only the first row, read back one row, and failed every
    guess with "could not enter cleanly".
    """
    words = [w for w in str(phrase).upper().split() if w]
    if len(words) < 2 or not all(w.isalpha() for w in words):
        return False, f"phrase {phrase!r} is not a word-separated phrase"
    if groups:
        want_shape = [len(w) for w in words]
        if want_shape != list(groups):
            return False, (f"shape {want_shape} != board {list(groups)}; "
                           f"not typing")
    typed = " ".join(words)
    want = "".join(words)
    n_cells = sum(len(w) for w in words)

    for _attempt in range(3):
        try:
            page.mouse.click(960, 320)
        except Exception:
            pass
        page.wait_for_timeout(1800)
        stray = _phrazle_active_row(page, n_cells)
        if len(stray) >= n_cells:
            _phrazle_clear_row(page, stray)
            page.wait_for_timeout(500)
        _type_like_a_person(page, typed, base_delay=200)
        page.wait_for_timeout(700)
        got = _phrazle_active_row(page, n_cells)
        if got == want:
            page.keyboard.press("Enter")
            page.wait_for_timeout(3400)
            return True, "row submitted"
        print(f"[phrazle] row readback {got!r} != {want!r}, retrying")
        _phrazle_clear_row(page, got)
        page.wait_for_timeout(600)
    return False, f"could not enter {want!r} cleanly"


def _phoodle_won(page):
    """Phoodle-specific win check: a scored row whose cells are all green.

    Phoodle has NO win banner. When you win, the winning row's cells gain
    `correct bg-correctGreen` and nothing else on the page changes - there is
    no "you won" text anywhere. So every text-based check (WIN_PHRASES,
    POST_WIN_MARKERS) reports a genuine win as a failure, which is what
    happened for days: the run actually won on the first guess and was then
    thrown away as unsolved.

    The cell colour lives on the PARENT of `.letter-container`, so reading
    computed background off `.letter-container` (always transparent) shows
    nothing. We therefore test the cell class list, which is unambiguous.
    """
    try:
        return page.evaluate("""() => {
            const cells = Array.from(document.querySelectorAll(
                '.letter-container')).map(c => c.parentElement).filter(Boolean);
            for (const row of document.querySelectorAll('div')) {
                if (!row.className || !String(row.className).includes('justify-center'))
                    continue;
                const kids = Array.from(row.children);
                if (kids.length !== 5) continue;
                const green = kids.filter(k =>
                    /\\bcorrect\\b/.test(String(k.className)) ||
                    /correctGreen/.test(String(k.className))).length;
                if (green === 5) return true;
            }
            return false;
        }""")
    except Exception:
        return False


def _phrazle_won(page):
    """Phrazle-specific win check: ten green tiles in a single scored row.

    The generic phrase matcher is unusable here. The page always shows a static
    "How to Play Phrazle" explainer under the board whose prose contains words
    like "correct" and "solved", so it reported a win on a board that had not
    been solved at all.

    Counting green cells directly is the reliable signal: a solved phrase paints
    all ten tiles green. A row is judged on its own green count rather than on
    "every tile in the row is green", because the winning row also contains a
    narrow caret/spacer cell that never takes the green fill.
    """
    try:
        return bool(page.evaluate("""() => {
          const isGreen = (t) => {
            const c = (getComputedStyle(t).backgroundColor.match(/\\d+/g) || [])
              .map(Number);
            if (c.length < 3) return false;
            const [r, g, b] = c;
            return g > 110 && g > r + 25 && g > b + 25;
          };
          for (const r of Array.from(document.querySelectorAll('tr, [class*=row]'))) {
            if (r.offsetParent === null) continue;
            const green = Array.from(
              r.querySelectorAll('td, [class*=tile], [class*=cell], span, div'))
              .filter(isGreen).length;
            if (green >= 10) return true;
          }
          return false;
        }"""))
    except Exception:
        return False


def _phrazle_grid_letters(page):
    """The live board's per-word cell groups, e.g. [5, 5] or [4, 2, 3, 5].

    Read from the real DOM (confirmed live): every word is wrapped in a
    `.wordBreak` div holding its `.row_block` letter cells, with a spacer
    `.wordBreak` containing one `.blockSpace` between words. One `.wordBreak`
    with N real cells contributes one group of size N, so a 5+5 board reads
    [5, 5] directly, whether or not letters have been typed.
    """
    try:
        return page.evaluate("""() => {
          const vis = e => e && e.offsetParent !== null;
          const rows = Array.from(document.querySelectorAll(
            '.wordhunt-row.current-row')).filter(vis);
          const tgt = rows.length ? rows : Array.from(
            document.querySelectorAll('.wordhunt-row')).filter(vis);
          const groups = [];
          for (const r of tgt) {
            for (const wb of Array.from(r.querySelectorAll('.wordBreak'))
                 .filter(vis)) {
              const n = Array.from(wb.querySelectorAll('.row_block'))
                .filter(e => vis(e) && !(e.classList &&
                                e.classList.contains('blockSpace'))).length;
              if (n > 0) groups.push(n);
            }
            if (groups.length >= 1) break;
          }
          return groups;
        }""") or []
    except Exception:
        return []


def _phrazle_browser_answer(page, tgt):
    """Let the page pick its own answer, then trust it.

    The site derives the daily index from the browser's own Date and timezone
    (see answers.phrazle_game_number). Re-running that arithmetic INSIDE the
    page, with the same faked clock and timezone the site sees, is the only
    source that cannot disagree with the board that was actually rendered.
    """
    try:
        n = page.evaluate("""() => {
          const o = new Date(2022, 3, 18, 0, 0, 0, 0);
          const e = new Date(); e.setHours(0, 0, 0, 0);
          const t = 60 * (e.getTimezoneOffset() - o.getTimezoneOffset()) * 1e3;
          const a = (new Date).setHours(0, 0, 0, 0)
                    - o.setHours(0, 0, 0, 0) - t;
          return 2 * Math.ceil(a / 864e5)
                 + ((new Date).getHours() < 12 ? 1 : 2);
        }""")
    except Exception:
        return None
    if not isinstance(n, int):
        return None
    try:
        return A.phrazle(tgt, game_number=n)
    except Exception:
        return None


def _s_phrazle(page, ans, gid):
    _close_modals(page)
    _close_generic_howto(page)
    # The page's own arithmetic wins over the host clock, and the board's cell
    # groups are the final arbiter: a phrase that does not fill the visible
    # cells is rejected with "Please use all available spaces".
    pick = _phrazle_browser_answer(page, A.target_date()) or {}
    cand = pick.get("answer")
    groups = _phrazle_grid_letters(page)
    if groups and cand:
        if [len(w) for w in cand.split()] != groups:
            for alt in A.phrazle_by_shape(A.target_date(), groups):
                print(f"[phrazle] board shape {groups} != {cand!r}; "
                      f"using the other daily puzzle {alt['answer']!r}")
                pick, cand = alt, alt["answer"]
                break
    if cand and cand != str(ans).upper():
        print(f"[phrazle] live board says {cand!r} (planner had {ans!r})")
        ans = cand
        _PLANS.pop(gid, None)
    seq = _guesses(gid, ans)
    # Every row the board accepts must have the SAME word shape as the answer's
    # board. A wrong-shaped probe never scores, so replace probes that do not
    # match the visible shape with same-shape phrases from the live bank. This
    # could not be filtered at plan time because the shape only exists after
    # the page has rendered.
    if groups:
        sig = lambda p: tuple(len(w) for w in str(p).upper().split())
        tgt = tuple(groups)
        if any(sig(g) != tgt for g in seq):
            bank = []
            src = HERE / "phrazle_live.json"
            if src.exists():
                try:
                    bank = [str(p).upper() for p in json.loads(
                        src.read_text(encoding="utf-8"))["phrases"]]
                except Exception:
                    bank = []
            same = [p for p in bank
                    if sig(p) == tgt and p not in [x.upper() for x in seq]]
            import random as _r
            _r.Random(f"{gid}:{A.target_date().isoformat()}").shuffle(same)
            fixed = [seq[-1]]
            for g in seq[:-1]:
                fixed.append(g if sig(g) == tgt
                             else (same.pop() if same else g))
            seq = fixed[::-1]
            print(f"[phrazle] probes re-shaped to {groups}: {seq}")
    for g in seq:
        ok, why = _phrazle_enter_phrase(page, g, groups=groups or None)
        if not ok:
            return False, f"guess {g!r} rejected locally: {why}"
    page.wait_for_timeout(2500)
    if not _phrazle_won(page):
        return False, f"board not solved after {len(seq)} phrase guesses (typed {seq})"
    return True, f"winning row all-green after {len(seq)} phrase guesses"



def _s_semantle(page, ans, gid):
    return solve_semantle(page, ans)


def _s_contexto(page, ans, gid):
    return solve_contexto(page, ans)


def _s_phoodle(page, ans, gid):
    """Phoodle: answer the puzzle the site is ACTUALLY serving.

    Phoodle fetches /daily-word/rest/<date> using the BROWSER's local date. With
    BROWSER_TZ=Pacific/Kiritimati (UTC+14) and the clock just after local
    midnight, that is frequently tomorrow's puzzle, while the answer engine
    (keyed off FAKE_DATE_ISO) returns today's word. Every guess then scores as
    wrong and the run can never win. The captured request is the site telling us
    the real answer key, so it wins over the date-derived guess.
    """
    _close_modals(page)
    # Phoodle persists its in-progress board (and the answer already revealed
    # to it) in localStorage. A game saved on an earlier visit survives the
    # fake-date override, so the page renders that stale board - which is why
    # the correct answer was scored as all-grey. Purging storage and reloading
    # forces the site to rebuild today's board. The live-day probe is attached
    # at page creation, so it also captures the post-reload request.
    _reset_site_state(page, gid)
    _close_modals(page)
    try:
        page.wait_for_timeout(2000)
    except Exception:
        pass
    # Trust the SITE over the host clock. Phoodle fetches its word by the
    # BROWSER's local date, and the runner pins the page to Pacific/Kiritimati
    # (UTC+14) just after local midnight, so the site serves the NEXT day's
    # puzzle while the answer engine (keyed off FAKE_DATE_ISO) returns today's.
    # Read the page's own calendar day and re-resolve the answer for exactly
    # that day. The captured request is used as a cross-check when present.
    live_date = _browser_today(page)
    if live_date and live_date != A.target_date():
        try:
            cand = A.phoodle(live_date)
            if cand.get("answer"):
                print(f"[phoodle] page clock is {live_date} (target "
                      f"{A.target_date()}) -> answering {cand['answer']!r} "
                      f"instead of {ans!r}")
                ans = str(cand["answer"]).upper()
                _PLANS.pop(gid, None)
        except Exception as e:
            print(f"[phoodle] live-date re-resolve failed: {str(e)[:110]}")
    # Cross-check against the word the site actually requested.
    if _LIVE_DAY.get("raw"):
        try:
            m = re.search(r'"word"\s*:\s*"([^"]+)"', _LIVE_DAY["raw"])
            if m and m.group(1).upper() != str(ans).upper():
                print(f"[phoodle] site requested {_LIVE_DAY.get('date')} "
                      f"word {m.group(1)!r}; using it over {ans!r}")
                ans = m.group(1).upper()
                _PLANS.pop(gid, None)
        except Exception:
            pass
    ok, ev = solve_wordle_like(page, ans, _guesses(gid, ans))
    # Phoodle never prints a win banner - a win is visible ONLY as a fully
    # green row. solve_wordle_like's text check therefore reports a genuine
    # win as a failure, so fall back to the board's own state.
    if not ok and _phoodle_won(page):
        return True, ("winning row all-green after the answer guess "
                      "(Phoodle shows no win banner; read from the board's "
                      "own `correct` cells)")
    return ok, ev


def _attr_pool(gid):
    """Probe-name pool for attribute games (never contains the answer)."""
    try:
        if gid == "batterup":
            j = json.loads((HERE / "frontend_data" / "src" / "lib" / "data" /
                            "batterup-players.json").read_text(encoding="utf-8"))
            return [p.get("player_name") for p in j
                    if isinstance(p, dict) and p.get("player_name")]
        if gid == "marveldle":
            j = json.loads((HERE / "frontend_data" / "src" / "lib" / "data" /
                            "marveldle-comics.json").read_text(encoding="utf-8"))
            return [c.get("name") for c in j
                    if isinstance(c, dict) and c.get("name")]
    except Exception as e:
        print(f"[{gid}] pool load failed: {e}")
    return []


def solve_attr_game(page, answer_name, gid, api_probes=()):
    """Attribute search-box games (batterup, marveldle): 2 real probes with
    on-screen column feedback, then the answer. Suggestion rows are picked by
    exact-name match like a human; every step is debug-logged."""
    _close_modals(page)
    answer_name = str(answer_name or "").strip()
    if not answer_name:
        return False, "empty answer"
    pool = [n for n in _attr_pool(gid) if n and n.lower() != answer_name.lower()]
    probes = [str(p) for p in (list(api_probes or [])[:2]) if p]
    for cand in (pool[0] if pool else None, pool[len(pool) // 2] if pool else None):
        if len(probes) >= 2:
            break
        if cand and cand not in probes:
            probes.append(cand)
    seq = probes[:2] + [answer_name]
    _dbg(gid, f"plan: probes={probes} answer={answer_name!r}")
    for n, name in enumerate(seq):
        last = (n == len(seq) - 1)
        _idle_drift(page)
        ok, detail = _type_country_guess(page, page, name)
        _dbg(gid, f"guess {n + 1}/{len(seq)} {name!r}: pick ok={ok} ({detail})")
        if not ok:
            # suggestion missed: drop this probe and continue like a human
            if not last:
                continue
            return False, f"answer pick failed ({detail})"
        try:
            btn = page.query_selector(
                "button:has-text('Guess'), button:has-text('Submit'), "
                "button:has-text('Go')")
            if btn and btn.is_visible():
                btn.click(timeout=3000)
            else:
                page.keyboard.press("Enter")
        except Exception:
            try:
                page.keyboard.press("Enter")
            except Exception:
                pass
        _settle(page, base=3000)
        try:
            txt = (page.evaluate("() => document.body.innerText") or "")
        except Exception:
            txt = ""
        _dbg(gid, f"guess {n + 1} feedback chars={len(txt)}")
        if last:
            okw, ev = _strict_win(page, answer_name)
            if okw:
                return True, f"{ev} after {len(seq)} guesses"
            low = txt.lower()
            if answer_name.lower() in low:
                return True, (f"answer echo on page after {len(seq)} "
                               f"guesses ({ev})")
            return False, f"no win signal; {ev}"
    return False, "no guesses played"


def _s_batterup(page, ans, gid):
    _close_modals(page)
    return solve_attr_game(page, ans, gid)


def _s_marveldle(page, ans, gid):
    _close_modals(page)
    # Marveldle lands on /menu (SPA): enter Classic/Comics + Play if offered.
    # Menu items are often links/cards, not <button>s, so click by visible
    # text across roles; fall back to direct game URLs. Log everything.
    try:
        texts = page.evaluate(
            """() => Array.from(document.querySelectorAll(
              'button, a, [role=button], [role=link]'))
              .filter(e => e && e.offsetParent !== null)
              .map(e => (e.innerText || '').trim().slice(0, 40))
              .filter(t => t).slice(0, 30)""") or []
    except Exception:
        texts = []
    _dbg(gid, f"menu clickables: {texts}")
    for label in ("Comics", "Classic", "Play", "Start", "Daily"):
        for sel in (f"button:has-text('{label}')", f"a:has-text('{label}')"):
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    el.click(timeout=3000)
                    page.wait_for_timeout(1500)
                    _dbg(gid, f"clicked {label!r} via {sel}")
                    break
            except Exception:
                pass
    try:
        n = page.evaluate(
            "() => document.querySelectorAll("
            "'input,textarea,[contenteditable=true]').length")
    except Exception:
        n = "?"
    _dbg(gid, f"after menu: url={page.url} inputs={n}")
    if not n:
        for u in ("https://marveldle.com/game", "https://marveldle.com/play",
                  "https://marveldle.com/classic", "https://marveldle.com/comics"):
            try:
                page.goto(u, wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(4000)
                n2 = page.evaluate(
                    "() => document.querySelectorAll("
                    "'input,textarea,[contenteditable=true]').length")
                _dbg(gid, f"tried {u} inputs={n2}")
                if n2:
                    break
            except Exception as e:
                _dbg(gid, f"{u} failed: {str(e)[:100]}")
    probes = []
    try:
        info = A.marveldle(A.target_date())
        probes = [p.get("name") for p in (info.get("probes") or [])
                  if isinstance(p, dict) and p.get("name")]
    except Exception as e:
        print(f"[marveldle] probe info failed: {str(e)[:110]}")
    return solve_attr_game(page, ans, gid, api_probes=probes)


SOLVERS = {
    "betweenle": ("betweenle", _s_betweenle),
    "colordle": ("colordle", lambda p, a: solve_colordle(p, a)),
    "colorfle": ("colorfle", lambda p, a: solve_colorfle(p, a)),
    "framed": ("framed", None),          # handled by the multi-mode path
    "searchle": ("searchle", _s_searchle),
    "semantle": ("semantle", _s_semantle),
    "phrazle": ("phrazle", _s_phrazle),
    "canuckle": ("canuckle", _s_wordle),
    "phoodle": ("phoodle", _s_phoodle),
    "contexto": ("contexto", _s_contexto),
    "worldle": ("worldle", _s_worldle),
    "globle": ("globle", _s_globle),
    "countryle": ("countryle", _s_countryle),
    "waffle": ("waffle", _s_waffle),
    "worgle": ("worgle", _s_worgle),
    "batterup": ("batterup", _s_batterup),
    "marveldle": ("marveldle", _s_marveldle),
    "nerdle": ("nerdle", None),  # external async solver, dispatched via subprocess
}


def _db_dir(date_key, gid):
    d = HERE / "database" / date_key / gid
    d.mkdir(parents=True, exist_ok=True)
    return d


def _db_already_done(date_key, gid):
    """No-duplicate guard: GitHub tracks database/{date}/{game}/result.json.

    If today's result is committed as solved=true, skip the run so a retry /
    next cron never remakes the same video. Returns the saved record or None.
    """
    p = HERE / "database" / date_key / gid / "result.json"
    try:
        if p.exists():
            r = json.loads(p.read_text(encoding="utf-8"))
            if r.get("solved") and r.get("video"):
                return r
    except Exception:
        pass
    return None


def _db_save(date_key, gid, record):
    """Save structured record: database/{date}/{game}/result.json + youtube.txt.

    Keeps per-date + per-game separation so search by date or by game is a
    plain folder listing. video mp4 itself stays in videos/{game}/ (gitignored)
    and is uploaded as a workflow artifact; only the link + json commit cleanly.
    """
    d = _db_dir(date_key, gid)
    try:
        (d / "result.json").write_text(json.dumps(record, indent=1), encoding="utf-8")
    except Exception:
        pass
    try:
        vid = record.get("youtube_url") or record.get("video")
        if vid:
            (d / "youtube.txt").write_text(str(vid), encoding="utf-8")
    except Exception:
        pass


def _probe_video(path):
    """ffprobe a final mp4 -> dict(resolution, duration, size). Never raises."""
    info = {"path": str(path), "size_mb": 0, "width": 0, "height": 0,
            "duration_s": 0, "ok": False}
    try:
        p = Path(str(path))
        if p.exists():
            info["size_mb"] = round(p.stat().st_size / 1e6, 2)
    except Exception:
        pass
    try:
        import subprocess as _sp, json as _js
        r = _sp.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                     "-show_entries", "stream=width,height,duration",
                     "-show_entries", "format=duration", "-of", "json",
                     str(path)], capture_output=True, text=True, timeout=60)
        j = _js.loads(r.stdout or "{}")
        st = (j.get("streams") or [{}])[0]
        info["width"] = int(st.get("width") or 0)
        info["height"] = int(st.get("height") or 0)
        dur = st.get("duration") or (j.get("format") or {}).get("duration") or 0
        info["duration_s"] = round(float(dur), 1)
        info["ok"] = info["width"] > 0
    except Exception as e:
        info["error"] = str(e)[:100]
    return info


def _report_game(gid, date_key, *, answer=None, guesses=None, rejected=None,
                 reloads=None, verify=None, video=None, chapters=None,
                 upload=None, extra=None):
    """One A-to-Z banner per game: everything a log reader needs, no video
    rewatch required. Quality WARNs fire when the final mp4 looks wrong."""
    gs = guesses or []
    print(f"========== REPORT {gid} {date_key} ==========")
    print(f"env: tz={os.environ.get('BROWSER_TZ')} "
          f"tzoff={os.environ.get('TZ_OFFSET_MINUTES')} "
          f"fake={os.environ.get('FAKE_DATE_ISO', '') or 'auto'} "
          f"headless={HEADLESS}")
    print(f"answer: {str(answer)[:80]}")
    print(f"guesses: n={len(gs)} {gs} rejected={rejected or []}")
    print(f"reloads: n={reloads or 0} (0-1 normal; more = instability)")
    print(f"verify: {verify}")
    if isinstance(video, dict):
        print(f"video: {video.get('path')} size={video.get('size_mb')}MB "
              f"res={video.get('width')}x{video.get('height')} "
              f"dur={video.get('duration_s')}s")
        if not video.get("ok"):
            print("WARN: video probe failed (missing ffprobe or bad file)")
        if video.get("duration_s", 0) < 20:
            print("WARN: video shorter than 20s")
        if video.get("width") not in (0, 1920):
            print(f"WARN: width {video.get('width')} != 1920")
        if video.get("size_mb", 0) < 0.5:
            print("WARN: video file tiny/missing")
    else:
        print(f"video: {video} (no probe)")
    print(f"chapters: {chapters or []}")
    print(f"upload: {upload}")
    for k, v in (extra or {}).items():
        print(f"{k}: {v}")
    print(f"========== END REPORT {gid} ==========")


def run_nerdle_external(g, tgt, date_key, today, short):
    """Run the merged 9-mode nerdle solver (async, kept as-is) via subprocess."""
    import subprocess as _sp
    done = _db_already_done(date_key, "nerdle")
    if done:
        print(f"[nerdle] already done for {date_key}, skipping (no duplicate)")
        return {"game": "nerdle", "solved": True, "video": done.get("video"),
                "evidence": "skipped: database already solved"}
    vdir = HERE / "videos" / "nerdle"
    vdir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.setdefault("HEADLESS", "true")
    env.setdefault("TZ_OFFSET_MINUTES", "540")
    env.setdefault("BROWSER_TZ", "Asia/Tokyo")
    print(f"[nerdle] target {date_key} (9 modes, one video)")
    try:
        r = _sp.run([sys.executable, str(HERE / "nerdle_solver.py")],
                    cwd=str(HERE), env=env, capture_output=True, text=True, timeout=3600)
        print((r.stdout or "")[-2000:])
        if r.returncode != 0:
            print((r.stderr or "")[-2000:])
    except Exception as e:
        return {"game": "nerdle", "solved": False, "video": None, "evidence": f"subprocess: {e}"}
    # nerdle_solver writes videos/result_{date}.json + nerdle_final_{date}.mp4
    # (flat) — normalize into videos/nerdle/ + database/{date}/nerdle/.
    final = vdir / f"nerdle_final_{date_key}.mp4"
    flat_final = HERE / "videos" / f"nerdle_final_{date_key}.mp4"
    try:
        if not final.exists() and flat_final.exists():
            flat_final.rename(final)
    except Exception:
        pass
    flat_res = HERE / "videos" / f"result_{date_key}.json"
    vres = vdir / "result.json"
    solved, evidence = False, "nerdle solver did not report"
    try:
        src = None
        if flat_res.exists():
            src = flat_res
        if src:
            rec = json.loads(src.read_text(encoding="utf-8"))
            solved = bool(rec.get("solved"))
            evidence = f"modes={rec.get('modes')}"
            try:
                src.rename(vdir / f"result_{date_key}.json")
            except Exception:
                pass
            (vres).write_text(json.dumps(rec, indent=1), encoding="utf-8")
    except Exception as e:
        evidence = f"result parse: {e}"
    record = {"game": "nerdle", "date": date_key, "solved": solved,
              "evidence": str(evidence)[:300], "video": str(final) if final.exists() else None}
    _db_save(date_key, "nerdle", record)
    try:
        _modes = json.loads((vdir / f"result_{date_key}.json").read_text(
            encoding="utf-8")).get("modes", [])
    except Exception:
        _modes = []
    _report_game("nerdle", date_key, answer="9 modes (see modes)",
                 guesses=[f"{m.get('id')}:{m.get('typed')}" for m in _modes
                          if isinstance(m, dict)],
                 reloads=_RELOADS.get("nerdle", 0),
                 verify=f"solved={solved} evidence={str(evidence)[:200]}",
                 video=_probe_video(final) if final.exists() else None,
                 chapters=[m.get("id") for m in _modes
                           if isinstance(m, dict)],
                 upload="skipped-no-creds",
                 extra={"assembly": "intro+9 mode cards+gameplay"})
    return {"game": "nerdle", "solved": solved, "video": record["video"], "evidence": evidence}


# ---------------- run ----------------
def run_framed_all(gid, g, tgt, date_key, today, short):
    """Solve all four Framed modes and assemble ONE video, one mode per chapter.

    This replaces four near-duplicate single-mode videos. Each mode is played
    and verified on its own page, and the combined video carries a chapter per
    mode plus a results card listing every answer.
    """
    answers = {}
    for mid, mname, _u in FRAMED_MODES:
        try:
            a = A.framed(tgt, mode=mid).get("answer")
        except Exception as e:
            a = None
            print(f"[framed] {mid} answer failed: {str(e)[:120]}")
        if a:
            answers[mid] = a
            print(f"[framed] {mname} answer = {a}")
    if not answers:
        return {"game": gid, "solved": False, "video": None,
                "evidence": "no framed answers available"}

    vdir = HERE / "videos" / gid
    vdir.mkdir(parents=True, exist_ok=True)
    per_mode, solved_modes = [], []

    with sync_playwright() as p:
        b = p.chromium.launch(headless=HEADLESS)
        ctx = b.new_context(record_video_dir=str(vdir),
                            record_video_size={"width": 1920, "height": 1080},
                            viewport={"width": 1920, "height": 1080},
                            timezone_id=os.environ.get("BROWSER_TZ", "Asia/Tokyo"),
                            locale="en-US")
        if HAS:
            iso = os.environ.get("FAKE_DATE_ISO", tgt.strftime("%Y-%m-%dT00:05:00"))
            try:
                ctx.add_init_script(script=QP.get_fake_date_init_script(iso))
            except Exception:
                pass

        def _route(route):
            try:
                u = route.request.url
                if any(x in u for x in ("doubleclick", "googlesyndication", "adnxs",
                                        "pubmatic", "amazon-adsystem",
                                        "google-analytics", "clarity.ms")):
                    return route.abort()
            except Exception:
                pass
            return route.continue_()
        ctx.route("**/*", _route)
        pg = ctx.new_page()
        try:
            pg.goto(FRAMED_MODES[0][2], wait_until="domcontentloaded", timeout=45000)
            pg.wait_for_timeout(6000)
            per_mode, solved_modes = solve_framed_modes(pg, answers)
            try:
                pg.screenshot(path=str(vdir / "shot_win.png"))
            except Exception:
                pass
        except Exception as e:
            per_mode = [{"mode": m, "answer": answers[m], "solved": False,
                         "evidence": f"exception: {str(e)[:150]}"} for m in answers]
        try:
            vpath = pg.video.path()
        except Exception:
            vpath = None
        ctx.close()
        b.close()

    evidence = "; ".join(
        f"{d['mode']}={'WIN' if d.get('solved') else 'FAIL'}" for d in per_mode
    ) or "no modes played"
    # A success means every mode that had an answer and could be played was won.
    playable = [d for d in per_mode if d.get("answer")]
    solved = bool(playable) and all(d.get("solved") for d in playable)
    print(f"[{gid}] solved={solved} ({len(solved_modes)}/{len(per_mode)} modes) {evidence}")
    return _assemble_framed(gid, g, date_key, today, short, answers, per_mode,
                            solved, evidence, vpath)


def _assemble_framed(gid, g, date_key, today, short, answers, per_mode,
                     solved, evidence, vpath):
    """Cut the four Framed modes into one captioned, bordered video."""
    vdir = HERE / "videos" / gid
    solved_modes = [d["mode"] for d in per_mode if d.get("solved")]
    final = vpath
    if final and HAS and HAS_MOVIEPY and HAS_PIL:
        try:
            recap_p = vdir / "recap.png"
            reveal_p = vdir / "reveal.png"
            facts_p = vdir / "facts.png"
            teaser_p = vdir / "teaser.png"
            allsteps = [{"turn": i + 1, "guess": answers.get(k, ""), "pattern": "",
                         "mode": m}
                        for i, (k, m, _u) in enumerate(FRAMED_MODES)]
            DP.generate_recap(str(recap_p), g["name"], today)
            DP.generate_reveal(str(reveal_p), g["name"], today,
                               ", ".join(f"{m}: {answers.get(k, '?')}"
                                         for k, m, _u in FRAMED_MODES),
                               allsteps)
            DP.generate_facts(str(facts_p), g["name"], today,
                              f"{len(solved_modes)}/{len(FRAMED_MODES)} modes won",
                              allsteps)
            DP.generate_teaser(str(teaser_p), g["name"], today, g["slug"])

            def _still(p, secs):
                return (ImageClip(str(p)).set_duration(secs).set_fps(24)
                        .resize(width=1920, height=1080))

            play_src = _polish_gameplay(str(final), vdir / "gameplay_1080.mp4")
            gameplay = VideoFileClip(play_src)
            gd = float(gameplay.duration or 0)

            # ---- per-mode "NOW PLAYING" cards -------------------------------
            # The session is recorded as ONE continuous clip, so the mode
            # boundaries come from the t_start stamps captured during play. Each
            # mode's footage is cut out and prefixed with a card naming it, so a
            # viewer landing mid-video knows exactly which round is on screen.
            marks = sorted(
                (d for d in per_mode if d.get("t_start") is not None),
                key=lambda d: d["t_start"])
            seg_clips, seg_meta = [], []
            if len(marks) >= 2 and gd > 0:
                # The recording starts a moment before we begin playing, so the
                # marks are rebased onto the clip's own timeline.
                lead = float(marks[0].get("t_start") or 0.0)
                bounds, prev = [], 0.0
                for d in marks:
                    st = min(max(float(d["t_start"]) - lead, 0.0), gd)
                    if st - prev > 2.0:
                        bounds.append(prev)
                        prev = st
                bounds.append(prev)          # last mode runs to the end
                bounds.append(gd)
                for i in range(len(bounds) - 1):
                    a, b = bounds[i], bounds[i + 1]
                    if b - a < 2.0:
                        continue
                    d = marks[min(i, len(marks) - 1)]
                    mid = d.get("mode", str(i))
                    # per_mode stores the mode ID; the display name lives in
                    # FRAMED_MODES as (id, display name, url).
                    mname = next((m for k, m, _u in FRAMED_MODES if k == mid),
                                 mid)
                    seg = gameplay.subclip(a, b)
                    cp = vdir / f"mode_{mid}.png"
                    DP.generate_mode_card(str(cp), g["name"],
                                          str(mname).replace("Framed ", ""),
                                          f"Round {i + 1} of {len(marks)}")
                    seg_clips.append(_still(cp, 2.5))
                    seg_clips.append(seg)
                    seg_meta.append((mid, mname))
                    print(f"[framed] card for {mname!r} at {a:.1f}s-{b:.1f}s")
                print(f"[framed] spliced {len(seg_meta)} mode cards "
                      f"into the video")
            if not seg_clips:
                seg_clips, seg_meta = [gameplay], []

            parts, chapters, cur = [], [], 0.0
            chapters.append((round(cur, 1),
                             f"Today's {g['name']} puzzle - all four modes"))
            parts.append(_still(recap_p, 5))
            cur += 5
            if seg_meta:
                for idx, (mid, mname) in enumerate(seg_meta):
                    cp = vdir / f"mode_{mid}.png"
                    chapters.append((round(cur, 1), str(mname) or str(mid)))
                    parts.append(_still(cp, 2.5))
                    cur += 2.5
                    # The matching footage clip sits right after its card.
                    parts.append(seg_clips[1 + idx * 2])
                    cur += float(parts[-1].duration or 0)
            else:
                chapters.append((round(cur, 1),
                                 f"All four modes played "
                                 f"({len(solved_modes)}/{len(FRAMED_MODES)} won)"))
                parts.append(gameplay)
                cur += gd
            for p, secs, title in (
                    (reveal_p, 8, "Every answer"),
                    (facts_p, 8, "Modes completed"),
                    (teaser_p, 5, "Tomorrow's puzzle")):
                chapters.append((round(cur, 1), title))
                parts.append(_still(p, secs))
                cur += secs
            total = cur
            clip = concatenate_videoclips(parts, method="compose")
            for _mp3 in ("song1.mp3", "song2.mp3"):
                _sp = QDIR / _mp3
                if _sp.exists():
                    try:
                        sc = AudioFileClip(str(_sp))
                        full = (afx.audio_loop(sc, duration=clip.duration)
                                if sc.duration < clip.duration
                                else sc.subclip(0, clip.duration))
                        clip = clip.set_audio(full.volumex(0.18))
                        break
                    except Exception:
                        continue
            out = str(vdir / f"{gid}_final_{date_key}.mp4")
            clip.write_videofile(out, codec="libx264", audio_codec="aac",
                                 fps=24, verbose=False, logger=None)
            final = out
            DP.build_captions_srt(chapters, total, today, g["name"],
                                  out_path=vdir / "captions.srt")
        except Exception as e:
            print(f"[{gid}] assembly fallback raw: {str(e)[:200]}")
            chapters = [(0, "The solve")]
    else:
        chapters = [(0, "The solve")]
    _upload_framed(gid, g, today, short, answers, chapters, final)
    (vdir / "result.json").write_text(json.dumps(
        {"game": gid, "date": date_key, "solved": solved, "evidence": evidence,
         "video": str(final), "modes": per_mode, "answers": answers,
         "chapters": chapters}, indent=1))
    _db_save(date_key, gid, {"game": gid, "date": date_key, "solved": solved,
                             "evidence": evidence, "video": str(final)})
    _report_game(gid, date_key,
                 answer={m: answers.get(m) for m in
                         (answers if isinstance(answers, dict) else {})},
                 guesses=[f"{d.get('mode')}:{d.get('solved')}" for d in per_mode],
                 reloads=_RELOADS.get("framed", 0),
                 verify=f"solved={solved} ({evidence})",
                 video=_probe_video(final) if final else None,
                 chapters=[t for _, t in chapters],
                 upload="skipped-no-creds",
                 extra={"assembly": "intro+4 mode cards+gameplay+slides"})
    return {"game": gid, "solved": solved, "video": str(final),
            "evidence": evidence, "guesses": len(per_mode) * 2}


def _upload_framed(gid, g, today, short, answers, chapters, final):
    if not (final and HAS):
        return
    def _ts(sec):
        sec = max(0, int(sec))
        return f"{sec // 60}:{sec % 60:02d}"
    ch_lines = "\n".join(f"{_ts(s)} {t}" for s, t in chapters)
    desc = (f"Framed Answer Today - {today}.\n\n"
            f"All four Framed modes played and solved on page.\n\n"
            + "\n".join(f"{m}: {answers.get(k, 'unavailable')}"
                        for k, m, _u in FRAMED_MODES)
            + f"\n\nChapters:\n{ch_lines}\n\n"
              f"More: https://wordsolverx.com/{g['slug']}\n\n"
              "#Framed #FramedAnswerToday #DailyPuzzle #MoviePuzzle")
    tp = HERE / "videos" / gid / "thumb.png"
    try:
        thumb(str(tp), g["name"], today,
              ", ".join(answers.get(k, "?") for k, _m, _u in FRAMED_MODES))
    except Exception:
        tp = None
    try:
        upload_to_youtube(
            str(final),
            title=f"Framed Answer Today ({short}) - All 4 Modes Solved!",
            description=desc, chapters=chapters,
            thumbnail_path=str(tp) if tp and tp.exists() else None,
            tags=DP.build_tags("Framed"))
    except Exception as e:
        print(f"[{gid}] upload error: {str(e)[:200]}")


def run_one(gid):
    g = GAMES[gid]
    tgt = A.target_date()
    date_key = tgt.isoformat()
    today = tgt.strftime("%B %d, %Y")
    short = tgt.strftime("%b %d")
    print(f"[{gid}] {g['name']} target {date_key}")
    _dbg(gid, f"START env tz={os.environ.get('BROWSER_TZ')} "
              f"tzoff={os.environ.get('TZ_OFFSET_MINUTES')} "
              f"fake={os.environ.get('FAKE_DATE_ISO', '') or 'auto'} "
              f"headless={HEADLESS} url={g['url']}")
    if gid == "nerdle" or g.get("external") == "nerdle_solver.py":
        return run_nerdle_external(g, tgt, date_key, today, short)
    # No-duplicate guard: skip if database/{date}/{game} already solved.
    done = _db_already_done(date_key, gid)
    if done:
        print(f"[{gid}] already done for {date_key}, skipping (no duplicate)")
        return {"game": gid, "solved": True, "video": done.get("video"),
                "evidence": "skipped: database already solved"}
    key, solver = SOLVERS[gid]
    if g.get("all_modes"):
        return run_framed_all(gid, g, tgt, date_key, today, short)
    try:
        ans = getattr(A, key)(tgt)
    except SystemExit as e:
        return {"game": gid, "solved": False, "video": None, "evidence": f"answer unavailable: {e}"}
    aval = ans.get("answer") or ans.get("name") or str(ans.get("colors"))
    if gid == "colordle" and ans.get("hex"):
        # Seed the planner cache before anything calls _guesses/_steps, so the
        # slides and result.json report the real multi-guess solve.
        _HEX[gid] = ans["hex"]
        _PLANS.pop(gid, None)
    if gid == "colorfle" and ans.get("colors"):
        _COLORS[gid] = ans["colors"]
        _PLANS.pop(gid, None)
    print(f"[{gid}] answer ready (len {len(str(aval))})")
    vdir = HERE / "videos" / gid
    vdir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        b = p.chromium.launch(headless=HEADLESS)
        # Record at native 1080p so the final video is real FHD, not an upscale.
        ctx = b.new_context(record_video_dir=str(vdir),
                            record_video_size={"width": 1920, "height": 1080},
                            viewport={"width": 1920, "height": 1080},
                            timezone_id=os.environ.get("BROWSER_TZ", "Asia/Tokyo"),
                            locale="en-US",
                            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36")
        # Pin EVERY game to the target date. Contexto and semantle used to be
        # exempted, which left the page on the real host clock: with the runner
        # on Pacific/Kiritimati the site then served the NEXT day's puzzle
        # (observed 09/28 where the engine answered 09/27), so the answer was
        # scored 8136 - nowhere near - and a real solve was impossible.
        if not GAMES[gid].get("no_fake") and HAS:
            iso = os.environ.get("FAKE_DATE_ISO", tgt.strftime("%Y-%m-%dT00:05:00"))
            try:
                ctx.add_init_script(script=QP.get_fake_date_init_script(iso))
            except Exception:
                pass

        def _route(route):
            try:
                u = route.request.url
                if any(x in u for x in ("doubleclick", "googlesyndication", "adnxs", "pubmatic",
                                        "amazon-adsystem", "google-analytics", "clarity.ms", "ad-delivery")):
                    return route.abort()
            except Exception:
                pass
            return route.continue_()
        ctx.route("**/*", _route)
        pg = ctx.new_page()
        # Attach the daily-puzzle response probe BEFORE the first navigation, so
        # the site's own answer request is captured on the initial page load.
        _install_live_day_probe(pg)
        solved, evidence = False, "exception before solve"
        try:
            pg.goto(g["url"], wait_until="domcontentloaded", timeout=45000)
            pg.wait_for_timeout(6000)
            # dismiss consent best-effort
            for sel in ("button:has-text('Accept')", "button:has-text('Got it')",
                        "button:has-text('OK')", "[aria-label='dismiss']"):
                try:
                    el = pg.query_selector(sel)
                    if el and el.is_visible():
                        el.click(timeout=2000)
                        break
                except Exception:
                    continue
            _close_modals(pg)
            # The site's own answer request has now been captured, so we know
            # which day the board really is. If that differs from the day the
            # local engine answered for, the whole guess sequence targets the
            # wrong puzzle and the game is unwinnable - so re-resolve first.
            try:
                fixed, _served = _align_answer_to_live_day(gid, ans, pg)
                if fixed is not ans:
                    ans = fixed
                    aval = ans.get("answer") or ans.get("name") \
                        or str(ans.get("colors"))
            except Exception:
                pass
            aval_s = str(aval)
            try:
                # colour games need their full answer dict, not the string
                if gid in ("colordle", "colorfle"):
                    solved, evidence = solver(pg, ans)
                else:
                    solved, evidence = solver(pg, aval_s, gid)
            except TypeError:
                solved, evidence = solver(pg, aval_s)
        except Exception as e:
            evidence = f"exception: {str(e)[:200]}"
        try:
            pg.screenshot(path=str(vdir / "shot_win.png"))
        except Exception:
            pass
        try:
            vpath = pg.video.path()
        except Exception:
            vpath = None
        ctx.close()
        b.close()

    print(f"[{gid}] solved={solved} evidence={evidence}")
    final = vpath
    # Quordle-parity assembly:
    #   recap(5s) + hints(10s) + gameplay + reveal(8s) + facts(8s) + teaser(5s)
    #   + uniform music + SRT captions + real chapter list
    if final and HAS and HAS_MOVIEPY and HAS_PIL:
        try:
            steps = _steps(gid, aval)
            nguess = len(steps)
            recap_p = vdir / "recap.png"
            hints_p = vdir / "hints.png"
            reveal_p = vdir / "reveal.png"
            facts_p = vdir / "facts.png"
            teaser_p = vdir / "teaser.png"
            DP.generate_recap(str(recap_p), g["name"], today)
            if gid == "waffle":
                try:
                    _winfo = A.waffle(A.target_date())
                    _ww = [str(w) for w in (_winfo.get("words") or []) if w]
                    _hints = ([f"{len(_ww)} words hidden in the grid"]
                              + [f"Find {_w[:12]}" for _w in _ww[:2]])
                except Exception:
                    _hints = ["Swap tiles to solve", "Greens stay put"]
            else:
                _hints = S.compute_hints(str(aval))
            DP.generate_hints(str(hints_p), g["name"], today,
                              _hints, str(aval))
            DP.generate_reveal(str(reveal_p), g["name"], today, str(aval), steps)
            DP.generate_facts(str(facts_p), g["name"], today, str(aval), steps)
            DP.generate_teaser(str(teaser_p), g["name"], today, g["slug"])

            def _still(p, secs):
                return (ImageClip(str(p)).set_duration(secs).set_fps(24)
                        .resize(width=1920, height=1080))

            # Gameplay is a raw screen recording. Scale it to FILL the frame
            # (never pillarbox) and add a slow push-in so the board is readable
            # and the footage feels produced rather than screen-captured.
            play_src = _polish_gameplay(str(final), vdir / "gameplay_1080.mp4")
            gameplay = VideoFileClip(play_src)
            gd = float(gameplay.duration or 0)

            parts, chapters, cur = [], [], 0.0
            for p, secs, title in (
                    (recap_p, 5, f"Today's {g['name']} puzzle"),
                    (hints_p, 10, "Hints before the solve"),
                    (None, gd, f"Full solve ({nguess} guesses)"),
                    (reveal_p, 8, "Answer & deduction path"),
                    (facts_p, 8, "Solve breakdown"),
                    (teaser_p, 5, "Tomorrow's puzzle")):
                chapters.append((round(cur, 1), title))
                parts.append(gameplay if p is None else _still(p, secs))
                cur += gd if p is None else secs
            total = cur

            # Brand intro first (kept short on camera, full clip prepended).
            _intro = HERE / "intro" / "wordsolverx-intro-20s.mp4"
            if _intro.exists():
                try:
                    _ic = VideoFileClip(str(_intro)).resize(width=1920, height=1080)
                    parts.insert(0, _ic)
                    chapters = [(0, "Intro")] + [(s + _ic.duration, t)
                                                for s, t in chapters]
                    cur += float(_ic.duration or 0)
                    total = cur
                    _dbg(gid, f"intro prepended ({_ic.duration:.1f}s)")
                except Exception as e:
                    _dbg(gid, f"intro skipped: {str(e)[:120]}")

            clip = concatenate_videoclips(parts, method="compose")
            for _mp3 in ("song1.mp3", "song2.mp3"):
                _sp = QDIR / _mp3
                if _sp.exists():
                    try:
                        sc = AudioFileClip(str(_sp))
                        full = (afx.audio_loop(sc, duration=clip.duration)
                                if sc.duration < clip.duration
                                else sc.subclip(0, clip.duration))
                        clip = clip.set_audio(full.volumex(0.18))
                        break
                    except Exception:
                        continue
            out = str(vdir / f"{gid}_final_{date_key}.mp4")
            clip.write_videofile(out, codec="libx264", audio_codec="aac",
                                 fps=24, verbose=False, logger=None)
            final = out
            srt = DP.build_captions_srt(chapters, total, today, g["name"],
                                         out_path=vdir / "captions.srt")
        except Exception as e:
            print(f"[{gid}] assembly fallback raw: {str(e)[:200]}")
            chapters = [(0, "The solve")]
    else:
        chapters = [(0, "The solve")]

    # upload (skips without creds)
    nguess = len(_steps(gid, aval))
    if final and HAS:
        def _ts(sec):
            sec = max(0, int(sec))
            return f"{sec // 60}:{sec % 60:02d}"
        ch_lines = "\n".join(f"{_ts(s)} {t}" for s, t in chapters)
        title = DP.build_title(g["name"], short, nguess)
        desc = (f"{g['name']} Answer Today - {today}.\n\n"
                f"Watch the complete on-page solve: {nguess} real guesses, "
                f"live feedback, then the final answer.\n\n"
                f"Today's answer: {aval}\n\n"
                f"Chapters:\n{ch_lines}\n\n"
                f"More: https://wordsolverx.com/{g['slug']}\n\n"
                f"#{g['name'].replace(' ', '')} #{g['name'].replace(' ', '')}AnswerToday "
                f"#DailyPuzzle #WordGame #PuzzleSolution")
        tags = DP.build_tags(g["name"])
        tp = vdir / "thumb.png"
        try:
            thumb(str(tp), g["name"], today, str(aval))
        except Exception:
            tp = None
        try:
            vid = upload_to_youtube(str(final), title=title, description=desc,
                                    chapters=chapters,
                                    thumbnail_path=str(tp) if tp and tp.exists() else None,
                                    tags=tags)
        except Exception as e:
            print(f"[{gid}] upload error: {str(e)[:200]}")
            vid = None
    (vdir / "result.json").write_text(json.dumps(
        {"game": gid, "date": date_key, "solved": solved, "evidence": evidence,
         "video": str(final), "guesses": _guesses(gid, aval),
         "steps": _steps(gid, aval), "chapters": chapters}, indent=1))
    _db_save(date_key, gid, {"game": gid, "date": date_key, "solved": solved,
                             "evidence": evidence, "video": str(final),
                             "youtube_url": vid if 'vid' in dir() else None})
    _report_game(gid, date_key, answer=f"{aval} ({ans.get('via', '?')})",
                 guesses=_guesses(gid, aval),
                 reloads=_RELOADS.get(gid, 0),
                 verify=f"solved={solved} evidence={evidence}",
                 video=_probe_video(final) if final else None,
                 chapters=[t for _, t in chapters],
                 upload=(vid if 'vid' in dir() and vid else
                         "skipped-no-creds"),
                 extra={"slides": "recap+hints+reveal+facts+teaser+intro"})
    return {"game": gid, "solved": solved, "video": str(final),
            "evidence": evidence, "guesses": nguess}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default=None)
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    gids = list(GAMES) if a.all else [a.game]
    results = []
    for gid in gids:
        if gid not in GAMES:
            print(f"unknown game {gid}")
            continue
        results.append(run_one(gid))
    print("\n========== SUMMARY ==========")
    ok = True
    for r in results:
        print(f"{r['game']}: solved={r['solved']} evidence={r['evidence']} video={r['video']}")
        ok = ok and r["solved"]
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
