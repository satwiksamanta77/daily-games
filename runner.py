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
from datetime import date, datetime, timedelta
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
    from moviepy.editor import VideoFileClip, ImageClip, concatenate_videoclips, AudioFileClip, CompositeVideoClip
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
# Played (actually typed/accepted) guesses per game, set by solvers as they
# play. run_one builds chapters / result.json `guesses` / report from this
# when present, so a rejected-and-replaced guess is never reported as played
# (contexto's 5-vs-6: plan said 6, only 5 were ever accepted).
_PLAYED = {}
# Words the site rejected per game, surfaced in evidence/report (was silently
# dropped: _report_game printed rejected=[] even when a rejection happened).
_REJECTED = {}
# run_one START wall-clock per game (for START->first-typing measurement).
_RUN_T0 = {}
# gid -> seconds from START to first typed character (logged once per run).
_FIRST_TYPE = {}
# gid -> wall-clock when the page's video RECORDING started (page creation).
# The autocrop anchor must be recording-relative: the old code measured the
# first-typing anchor from run START (which includes browser launch + setup)
# but applied it to the recording timeline (which starts at page creation),
# so the cut landed ~2-4 s INTO the solve — marveldle gameplay started at
# guess-1 feedback, worgle at guess 2.
_REC_T0 = {}
# gid -> seconds from RECORDING start to first player action (autocrop anchor).
_FIRST_TYPE_REC = {}
# gid -> what the first action was ("typing", "swap", "color-pick", ...).
_FIRST_ACTION_LABEL = {}
# gid -> seconds from START to shot_win (solve span, for autocrop/QC coverage).
_SOLVE_SPAN = {}
# gid -> batterup CDN archive entry for the target day (game_date, game_number).
_BATTERUP_CDN = {}
# gid -> list of (start, end) typing windows, RECORDING-relative
# (fix-loop iter 11): EVERY guess's typing interval, so autocrop can
# keep every guess's typing (frame-diff reads small typing areas as
# frozen; first-typing alone let Betweenle's cut drop CRANE/PARTY).
_TYPE_WINDOWS = {}


def _record_type_window(gid, start_s, end_s):
    """Append one typing window (recording-relative) for autocrop."""
    try:
        if gid:
            _TYPE_WINDOWS.setdefault(gid, []).append(
                (round(float(start_s), 2), round(float(end_s), 2)))
    except Exception:
        pass


def _record_first_action(gid, what="typing"):
    """Record the run's first player action ONCE (all games, all input paths).

    Logs the `START->first-typing` line (START-relative, the startup-delay
    metric) and stores the RECORDING-relative anchor for autocrop. `what`
    names the action: "typing" for keyboard games, "swap" for waffle's
    drag-swaps, "color-pick" for colorfle's clicks. No-op after the first
    call. Typing speed / pacing are UNCHANGED — this only records timing.
    """
    try:
        if not gid or gid in _FIRST_TYPE:
            return
        now = time.time()
        if gid in _RUN_T0:
            _el = now - _RUN_T0[gid]
            _FIRST_TYPE[gid] = round(_el, 1)
            _dbg(gid, f"START->first-typing {_el:.1f}s (first {what})")
        if gid in _REC_T0:
            _FIRST_TYPE_REC[gid] = round(now - _REC_T0[gid], 1)
        elif gid in _FIRST_TYPE:
            _FIRST_TYPE_REC[gid] = _FIRST_TYPE[gid]
        _FIRST_ACTION_LABEL[gid] = what
    except Exception:
        pass


def _count_reload(gid, why):
    _RELOADS[gid] = _RELOADS.get(gid, 0) + 1
    _dbg(gid, f"PAGE RELOAD #{_RELOADS[gid]} ({why})")


_VIDEO_T0 = None
_CLICKS = []
_FOUND_ANSWER = {}
_LAST_BOARD_SHOT = None


def _push_click_at(x, y):
    global _VIDEO_T0
    if _VIDEO_T0 is None:
        _VIDEO_T0 = time.time()
    try:
        t = max(0.0, time.time() - _VIDEO_T0)
    except Exception:
        t = 0.0
    _CLICKS.append((round(t, 2), int(x), int(y)))
    try:
        QP.ev_push(x, y, "click")
    except Exception:
        pass


def _push_click(page):
    """Record the canvas point where the user focuses (input/button), so the
    polished gameplay can show were clicks landed."""
    try:
        pt = page.evaluate("""() => {
          const el = document.activeElement;
          if (el && el.tagName && el.tagName.toLowerCase() !== 'body') {
            const r = el.getBoundingClientRect();
            if (r.width > 4 && r.height > 4) return [r.x + r.width / 2, r.y + r.height / 2];
          }
          const vis = Array.from(document.querySelectorAll('input')).find(e => e && e.offsetParent !== null);
          if (vis) { const r = vis.getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; }
          return [innerWidth / 2, innerHeight / 2];
        }""") or []
        if pt:
            _push_click_at(pt[0], pt[1])
    except Exception:
        pass


def _auto_dismiss_dialog(gid):
    def _h(d):
        try:
            print(f"[{gid}] dialog auto-dismiss {d.type}")
            d.dismiss()
        except Exception:
            pass
    return _h


def _log_nav(gid):
    """Top-level browser log: every navigation with its URL."""
    global _VIDEO_T0
    if _VIDEO_T0 is None:
        _VIDEO_T0 = time.time()
    def _h(frame):
        try:
            _dbg(gid, f"NAV -> {frame.url}")
        except Exception:
            pass
    return _h


def _dbg(gid, msg):
    """Timestamped debug line -> stdout AND videos/{gid}/debug_{date}.log.
    try:
        _DBG_FH[gid].flush()
    except Exception:
        pass

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

# In-flight run state for the SIGTERM handler + preliminary result.json:
# the handler must be able to record what was in flight when the runner
# was killed, without trusting any later stage to run.
_CURRENT_RUN = {"gid": None, "date_key": "", "solved": False,
                "evidence": "", "stage": "start"}


def _close_dbg_handles():
    """Flush + close every open debug-log handle (fix-loop iter 11:
    SIGTERM during the tail left the last ~108s of log unflushed and
    the artifact without result.json). Never raises."""
    for _fh in list(_DBG_FH.values()):
        try:
            _fh.flush()
            _fh.close()
        except Exception:
            pass
    _DBG_FH.clear()


def _write_preliminary_result(gid, date_key, solved, evidence, video=None,
                              answer=None):
    """Write result.json IMMEDIATELY (fix-loop iter 11): the old code
    wrote result.json ONLY after polish + QC, so a SIGTERM during the
    tail (exit 143, ~108s after the final log line, runs 39038172088 /
    39039561115) left no result.json and no Upload. The preliminary
    record (qc: pending) is overwritten by the final record after
    polish/QC. Never raises. `answer` is the verified winning word
    when solved, else None — a solved record must never carry
    answer=None (run 39043936480 Betweenle: solved=True, answer None)."""
    try:
        vdir = HERE / "videos" / gid
        vdir.mkdir(parents=True, exist_ok=True)
        (vdir / "result.json").write_text(json.dumps(
            {"game": gid, "date": date_key, "solved": bool(solved),
             "answer": (str(answer) if solved and answer else None),
             "evidence": str(evidence)[:500],
             "video": str(video) if video else None,
             "guesses": ([str(x) for x in _PLAYED[gid]]
                         if _PLAYED.get(gid) else []),
             "qc": {"pending": True}}, indent=1), encoding="utf-8")
        _dbg(gid, f"preliminary result.json written "
                  f"(solved={bool(solved)}, qc=pending)")
    except Exception as e:
        print(f"[{gid}] preliminary result.json failed: {str(e)[:100]}")


def _fast_copy_gameplay(src, out_path):
    """Unsolved-game gameplay artifact: fast remux/copy of the raw
    recording, NO re-encode, AUTOCROP off (fix-loop iter 11). The old
    path ran the full _polish_gameplay re-encode (incl. the
    _card_composite full re-encode) for unsolved games — the most
    likely place for the SIGTERM tail stall — only to discard the
    result. QC samples frames from the raw clip the same way."""
    try:
        import subprocess as _sp
        r = _sp.run(["ffmpeg", "-y", "-v", "error", "-i", str(src),
                     "-c", "copy", str(out_path)],
                    capture_output=True, text=True, timeout=300)
        if r.returncode == 0 and Path(str(out_path)).exists():
            return str(out_path)
    except Exception:
        pass
    try:
        import shutil as _sh
        _sh.copy2(str(src), str(out_path))
        return str(out_path)
    except Exception:
        return str(src)


def _signal_handler(signum, _frame):
    """SIGTERM/SIGINT handler (fix-loop iter 11, extracted iter 12 so
    tests can invoke it directly): on runner shutdown, flush + write
    the in-flight result.json (evidence += " [terminated by SIGTERM
    during <stage>]"), flush/close debug handles, kill ffmpeg children
    (and the nerdle solver subprocess, which _sp.run would otherwise
    leave orphaned), and exit promptly (~5s grace, never block on
    ffmpeg). The handler never waits on a child process."""
    _gid = _CURRENT_RUN.get("gid")
    if _gid:
        try:
            _write_preliminary_result(
                _gid, _CURRENT_RUN.get("date_key") or "",
                solved=bool(_CURRENT_RUN.get("solved")),
                evidence=(str(_CURRENT_RUN.get("evidence") or "")
                          + " [terminated by SIGTERM during "
                          + str(_CURRENT_RUN.get("stage") or "run")
                          + "]"))
        except Exception:
            pass
        try:
            _close_dbg_handles()
        except Exception:
            pass
    try:
        import subprocess as _sp
        _sp.run(["pkill", "-f", "ffmpeg"], timeout=3,
                capture_output=True)
        # The nerdle path blocks in subprocess.run; SIGTERM to the
        # runner must not orphan the solver + its Chromium.
        _sp.run(["pkill", "-f", "nerdle_solver.py"], timeout=3,
                capture_output=True)
    except Exception:
        pass
    raise SystemExit(143)


def _install_signal_handlers():
    """Register _signal_handler for SIGTERM/SIGINT."""
    import signal as _signal
    try:
        _signal.signal(_signal.SIGTERM, _signal_handler)
        _signal.signal(_signal.SIGINT, _signal_handler)
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
    "batterup": {"name": "Batterup", "url": "https://batter-up.app",
                 "slug": "batterup-answer-today", "no_align": True},
    "marveldle": {"name": "Marveldle", "url": "https://marveldle.com", "slug": "marveldle-answer-today"},
    # Nerdle: 9 modes solved back-to-back in ONE video (same treatment as
    # framed all_modes). Lives in nerdle_solver.py (async, kept as-is) and is
    # dispatched via subprocess so the sync runner stays untouched.
    "nerdle": {"name": "Nerdle", "url": "https://www.nerdlegame.com/game", "slug": "nerdle-answer-today",
               "all_modes": True, "external": "nerdle_solver.py"},
}

# The four Framed modes, in the order they are played in the combined video.
# Mode id "classic" (the site's menu label is CLASSIC; the old internal id
# "daily" leaked into chapters/cards/evidence as "Framed Daily"). The
# titles API still wants gameType "daily" for this mode — see
# answers.FRAMED_MODES[mode][0] / _FRAMED_API_GAMETYPE, which maps
# classic -> "daily" explicitly.
FRAMED_MODES = (
    ("classic", "Framed Classic", "https://framed.wtf"),
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


def _wrap_text_r(d, txt, font, max_w):
    """Word-wrap `txt` to lines fitting `max_w` — never drops text.

    Iter-19: same truncation class as daily_parity's [:20] slices.
    Over-long single words are hard-split so they cannot overflow.
    """
    txt = str(txt)
    if not txt:
        return [txt]
    try:
        if d.textlength(txt, font=font) <= max_w:
            return [txt]
    except Exception:
        return [txt]
    lines, cur = [], ""
    for word in txt.split(" "):
        cand = (cur + " " + word).strip()
        try:
            fits = d.textlength(cand, font=font) <= max_w
        except Exception:
            fits = len(cand) < 40
        if fits:
            cur = cand
            continue
        if cur:
            lines.append(cur)
            cur = ""
        rest = word
        while rest:
            try:
                if d.textlength(rest, font=font) <= max_w:
                    break
            except Exception:
                break
            cut = len(rest)
            while cut > 1:
                try:
                    if d.textlength(rest[:cut], font=font) <= max_w:
                        break
                except Exception:
                    break
                cut -= 1
            if cut <= 1:
                break
            lines.append(rest[:cut])
            rest = rest[cut:]
        cur = rest
    if cur:
        lines.append(cur)
    return lines or [txt]


def _shrink_font(font, size):
    """Return `font` at `size` when it has a path, else `font`."""
    try:
        path = getattr(font, "path", None)
        if path:
            return ImageFont.truetype(path, size)
    except Exception:
        pass
    return font


def _fit_font_r(d, txt, font, max_w, min_size=14):
    """Shrink `font` until `txt` fits `max_w`; never truncates text."""
    size = getattr(font, "size", 32)
    best = font
    try:
        if d.textlength(str(txt), font=font) <= max_w:
            return font
    except Exception:
        return font
    while size > min_size:
        size = max(min_size, int(size * 0.92))
        nf = _shrink_font(font, size)
        if nf is font:
            return best
        best = nf
        try:
            if d.textlength(str(txt), font=nf) <= max_w:
                return nf
        except Exception:
            return best
    return best


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
    # Iter-19: no ln[:80] slice — wrap each row (row grows for a
    # second line) so the full text is drawn.
    for ln in lines[:8]:
        parts = _wrap_text_r(d, ln, f["b"], W - 70 - 100)
        row_h = 76 + 34 * (len(parts) - 1)
        if y + row_h > H - 20:
            break
        d.rounded_rectangle([70, y, W - 70, y + row_h], radius=14, fill=(23, 34, 58))
        for j, part in enumerate(parts):
            d.text((100, y + 14 + j * 34), part, fill=(255, 255, 255), font=f["b"])
        y += row_h + 20
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
    # Iter-19: no [:34] slice — shrink the font so the full answer
    # fits the thumbnail box instead of truncating it.
    _ans = str(answer_txt or "?")
    _af = _fit_font_r(d, _ans, f["b"], 700 - 70 - 24)
    d.text((70, 455), _ans, fill=(255, 255, 255), font=_af)
    d.text((46, 560), date_str.upper(), fill=(255, 205, 0), font=f["b"])
    img.save(out, "PNG", optimize=True)
    return out


# ---------------- solve steps ----------------
# Clicking the centre of the page is unreliable: on Wordle-clone sites the
# on-screen keyboard sits at the bottom and a stray click can land on an
# ad. We focus the page once, then drive the on-screen ENTER key by text.
ENTER_LABELS = ("ENTER", "Enter", "enter", "SUBMIT", "Submit")


def _polish_gameplay(src, out_path, crf=17, gid=None, autocrop=True):
    """Delegate to the shared quordle_parity polish + Recordly-grade cursor
    overlay (native-size spring-smoothed arrow fed by the input-telemetry
    bus). Kept as a thin wrapper so every existing call site - including the
    framed assembler - gets the new cursor for free. Passes the run's
    RECORDING-relative first-action time through for autocrop (leading-idle
    cut), the solve span for the autocrop coverage guard, and the action
    label — and copies the autocrop cut detail into the artifact debug log
    (the rerun-#3 waffle truncation was silent in the artifact because only
    stdout carried the cut)."""
    try:
        _ft = _FIRST_TYPE_REC.get(gid) if gid else None
        if _ft is None:
            _ft = _FIRST_TYPE.get(gid) if gid else None
        _label = _FIRST_ACTION_LABEL.get(gid) if gid else None
        _span = _SOLVE_SPAN.get(gid) if gid else None
        _tw = _TYPE_WINDOWS.get(gid) if gid else None
        if gid:
            _dbg(gid, "polish start")
        out = QP.polish_gameplay(str(src), str(out_path), crf=crf,
                                 first_typing_s=_ft, solve_span_s=_span,
                                 first_action_label=_label,
                                 typing_windows=_tw,
                                 autocrop=autocrop)
        _lp = QP.LAST_POLISH or {}
        print(f"[polish] done mode={_lp.get('mode')} clips={_lp.get('clips')} "
              f"events={_lp.get('events')} out={out}")
        _ac = _lp.get("autocrop") or {}
        if gid and _ac:
            _dbg(gid, f"autocrop: cut={_ac.get('cut_s')}s "
                      f"leading_cut={_ac.get('leading_cut_s')}s "
                      f"source={_ac.get('leading_cut_source')} "
                      f"static_cuts={_ac.get('static_cuts')} "
                      f"segments={_ac.get('segments')} "
                      f"kept={_ac.get('kept_s')}s "
                      f"coverage={_ac.get('coverage_ratio')} "
                      f"of solve_span={_ac.get('solve_span_s')}s "
                      f"guard_clamped={_ac.get('guard_clamped')}")
        if gid:
            _dbg(gid, "polish done")
    except Exception as e:
        print(f"[polish] shared polish failed: {str(e)[:140]}")
        return str(src)
    # Guard: whatever comes back MUST be 1920x1080 or the moviepy concat
    # pillarboxes it on black (the marveldle 720p incident).
    try:
        import subprocess as _sp3
        _r3 = _sp3.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height",
                        "-of", "csv=p=0", str(out)],
                       capture_output=True, text=True, timeout=60)
        _w3, _h3 = [int(v) for v in _r3.stdout.strip().split(",")[:2]]
        if (_w3, _h3) != (1920, 1080):
            _fx = str(out) + ".fx.mp4"
            _r4 = _sp3.run(["ffmpeg", "-y", "-v", "error", "-i", str(out),
                            "-vf", "scale=1920:1080:force_original_aspect_ratio="
                            "increase,crop=1920:1080", "-c:v", "libx264",
                            "-preset", "medium", "-crf", "18", "-c:a", "aac",
                            _fx], capture_output=True, text=True, timeout=1800)
            if _r4.returncode == 0 and Path(_fx).exists():
                print(f"[polish] normalised {_w3}x{_h3} -> 1920x1080")
                return _fx
    except Exception as _e3:
        print(f"[polish] resolution guard skipped: {str(_e3)[:80]}")
    return out


def _instrument_mouse(page):
    """Wrap page.mouse.* so EVERY synthetic mouse call lands in the cursor
    telemetry track - per-site call sites were too sparse to look alive."""
    try:
        m = page.mouse
        for name in ("move", "down", "up", "click", "dblclick"):
            orig = getattr(m, name)

            def _wrap(*a, _o=orig, _n=name, **kw):
                res = _o(*a, **kw)
                try:
                    x = a[0] if len(a) > 0 else kw.get("x")
                    y = a[1] if len(a) > 1 else kw.get("y")
                    if x is not None and y is not None:
                        QP.ev_push(x, y,
                                   "click" if _n in ("click", "dblclick", "down")
                                   else "move")
                except Exception:
                    pass
                return res
            setattr(m, name, _wrap)
    except Exception:
        pass


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
    try:
        QP.drain_consent_clicks(page)
    except Exception:
        pass
    """The pause a person leaves after committing a guess.

    A human watches the board flip, reads the new colours and thinks before
    moving on. Returning to the board instantly (or after a fixed tick) is the
    clearest tell that nobody is actually reading the feedback.
    """
    import random
    page.wait_for_timeout(int(random.uniform(base * 0.6, base * 1.9)))


def _daily_opener(choices):
    """Pick a deterministic-per-day opener so videos never all start the
    same way (bot tell). Same date -> same opener (reproducible)."""
    try:
        seed = int(str(A.target_date()).replace("-", ""))
    except Exception:
        seed = 0
    return list(choices)[seed % len(list(choices))]


def _idle_drift(page, lo=300, hi=900, chance=0.25):
    """Occasionally let the cursor settle and the page sit still.

    Real screen recordings are not frame-perfect: the pointer drifts, the page
    is left alone for a beat. A perfectly uniform capture reads as automation.
    """
    import random
    if random.random() > chance:
        return
    try:
        _dx, _dy = random.randint(200, 1700), random.randint(200, 900)
        page.mouse.move(_dx, _dy, steps=random.randint(3, 9))
        try:
            QP.ev_push(_dx, _dy, "move")
        except Exception:
            pass
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
            jx, jy = x + random.uniform(-25, 25), y + random.uniform(-18, 18)
            cur.move(jx, jy)
            try:
                QP.ev_push(jx, jy, "move")
            except Exception:
                pass
            page.wait_for_timeout(random.randint(28, 70))
        cur.move(x, y)
        try:
            QP.ev_push(x, y, "move")
        except Exception:
            pass
        page.wait_for_timeout(160)
    except Exception:
        pass


def _type_like_a_person(page, text, base_delay=150):
    """Type `text` one key at a time with human timing and a settled cursor.

    Typing speed / delays are UNCHANGED (user-approved). This only records
    the first action once per run, for the startup-delay measurement and
    the autocrop anchor (see _record_first_action).
    """
    _record_first_action(getattr(page, "_qp_gid", None), "typing")
    _gid = getattr(page, "_qp_gid", None)
    _w_start = None
    try:
        if _gid and _gid in _REC_T0:
            _w_start = time.time() - _REC_T0[_gid]
    except Exception:
        _w_start = None
    try:
        page.evaluate("() => { window.__QP_HOLD = 1; window.__QP_HOLD_T = Date.now(); }")
    except Exception:
        pass
    try:
        for ch in str(text):
            try:
                page.keyboard.type(ch)
            except Exception:
                return False
            page.wait_for_timeout(_human_delay(base_delay))
        return True
    finally:
        try:
            page.evaluate("() => { window.__QP_HOLD = 0; }")
        except Exception:
            pass
        # Record the typing window for autocrop (fix-loop iter 11):
        # typing speed / delays are UNCHANGED — this only records
        # timing so autocrop never cuts a guess's typing.
        try:
            if _gid and _w_start is not None and _gid in _REC_T0:
                _record_type_window(
                    _gid, _w_start, time.time() - _REC_T0[_gid])
        except Exception:
            pass


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
    _push_click(page)
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
    # Never append to a stale row: clear whatever the field holds first
    # (batter-up kept the previous guess on camera before this). BUT only when
    # a real DOM input is focused: on Flutter/canvas boards (canuckle) the
    # Ctrl+A keydown is TYPED as a stray 'A' and corrupts every row
    # (debug showed "Current Guess: AAUDI" for input AUDIO).
    try:
        _dom_inp = page.evaluate(
            "() => { const e = document.activeElement; return !!(e && "
            "(e.tagName === 'INPUT' || e.tagName === 'TEXTAREA' "
            "|| e.isContentEditable)); }")
    except Exception:
        _dom_inp = False
    if _dom_inp:
        try:
            page.keyboard.press("Control+a")
            page.keyboard.press("Delete")
        except Exception:
            pass
    # Sites with an on-screen keyboard need no page focus at all, so try
    # that path first and avoid clicking anywhere on the page.
    # (This path bypasses _type_like_a_person, so record the first action
    # here — phoodle/searchle never logged START->first-typing because
    # their guesses go through the on-screen keyboard.)
    if _type_with_on_screen_keyboard(page, text):
        _record_first_action(getattr(page, "_qp_gid", None),
                             "typing (on-screen keyboard)")
        _push_click(page)
        return True
    _focus_board(page)
    _n0 = len(QP.ev_all())
    _push_click(page)
    if len(QP.ev_all()) == _n0:
        try:
            _ib = page.query_selector("input, textarea, [contenteditable=true]")
            _bb = (_ib.bounding_box() if _ib else None) or \
                {"x": 660, "y": 340, "width": 600, "height": 400}
            QP.ev_push(_bb["x"] + _bb["width"] / 2,
                       _bb["y"] + _bb["height"] / 2, "focus")
        except Exception:
            pass
    _type_like_a_person(page, text, base_delay=delay)
    page.wait_for_timeout(400)
    _press_enter(page)
    return True


# Synchronous CMP/consent accept pass (fix-loop iter 10). Worldle's
# Didomi "We value your privacy / Accept all" notice lives inside
# #didomi-host's shadow DOM, so the light-DOM dialog sweep below never
# sees it and the old consent watcher (plain querySelectorAll) never
# clicked it. The notice covered the whole page, the first guess-input
# click timed out (ElementHandle.click 3000ms), and the worldle job in
# run 39035133842 failed on country guess 1 with zero guesses
# submitted. This fragment pierces open shadow roots and clicks the
# accept button inside a CMP container. Returns 'consent-accept:<id|text>'
# or null. The CMP-container gate is what keeps it from ever clicking a
# game button: a "Continue"/"OK" button outside a consent/cookie/CMP
# container is left alone.
_CONSENT_ACCEPT_JS = """(() => {
  const ACCEPT = new Set(['accept all', 'accept all cookies', 'accept',
    'agree', 'i agree', 'agree and close', 'allow all', 'got it', 'ok',
    'okay', 'continue']);
  const CMP = '[class*=consent i],[class*=cookie i],[class*=cmp i],' +
    '[class*=didomi i],[class*=truste i],[class*=fc-],[class*=modal i],' +
    '[class*=dialog i],[class*=notice i],[class*=privacy i],' +
    '[id*=consent i],[id*=cookie i],[id*=privacy i],' +
    '[id*=didomi],[id*=truste],[role=dialog]';
  const inCmp = (el) => {
    try { if (el.closest(CMP)) return true; } catch (e) {}
    try {
      const rn = el.getRootNode && el.getRootNode();
      if (rn && rn.host) {
        const h = (rn.host.id || '') + ' ' +
          (typeof rn.host.className === 'string' ? rn.host.className : '');
        if (/didomi|consent|cookie|cmp|truste/i.test(h)) return true;
      }
    } catch (e) {}
    return false;
  };
  const roots = [document];
  const walk = (root) => {
    let all = [];
    try { all = Array.from(root.querySelectorAll('*')); }
    catch (e) { return; }
    for (const e of all) {
      try {
        if (e.shadowRoot) { roots.push(e.shadowRoot); walk(e.shadowRoot); }
      } catch (err) {}
    }
  };
  try { walk(document); } catch (e) {}
  try {
    for (const f of Array.from(document.querySelectorAll('iframe'))) {
      try { const d = f.contentDocument; if (d) { roots.push(d); walk(d); } }
      catch (e) {}
    }
  } catch (e) {}
  for (const root of roots) {
    let els = [];
    try {
      els = Array.from(root.querySelectorAll(
        'button, [role=button], a, #didomi-notice-agree-button'));
    } catch (e) { continue; }
    for (const el of els) {
      try {
        if (!el || el.offsetParent === null) continue;
        const r = el.getBoundingClientRect();
        if (!r || r.width < 8 || r.height < 8) continue;
        const t = ((el.innerText || el.getAttribute('aria-label') || ''))
          .trim().toLowerCase();
        const isDidomiAgree = el.id === 'didomi-notice-agree-button';
        if (!isDidomiAgree && (!t || t.length > 30 || !ACCEPT.has(t))) continue;
        if (!inCmp(el)) continue;
        el.click();
        return 'consent-accept:' + (el.id || t);
      } catch (e) {}
    }
  }
  return null;
})()"""


def _dismiss_consent(page):
    """Click a CMP/consent accept button if one is covering the page.

    Synchronous counterpart to the consent watcher: _close_modals runs
    this before any guess input is touched, so a Didomi/TrustArc-style
    notice can never again sit over the board while the solver clicks
    at it. Returns the clicked button's id/text, or None.
    """
    try:
        hit = page.evaluate(_CONSENT_ACCEPT_JS)
    except Exception:
        return None
    if hit:
        try:
            page.wait_for_timeout(900)
        except Exception:
            pass
    return hit


def _dismiss_dialogs(page, rounds=4):
    """Close instruction/consent dialogs that block the board.

    Two cases:
      * DOM sites: explicit close affordances, the dialog's own X corner, Escape.
      * Flutter/canvas sites (Canuckle, Betweenle): there is no DOM at all, so
        we click the pixel positions where the X / OK controls are drawn.
    """
    for _ in range(rounds):
        # CMP/consent first: a Didomi-style notice is NOT matched by the
        # dialog selector below (shadow-DOM host), so without this the
        # loop breaks immediately with the notice still covering the page.
        try:
            _dismiss_consent(page)
        except Exception:
            pass
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
                        page.wait_for_timeout(250)
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
                    page.wait_for_timeout(300)
                    clicked = True
            except Exception:
                pass
        if not clicked:
            try:
                page.keyboard.press("Escape")
                page.wait_for_timeout(250)
            except Exception:
                pass
    return page


# Canvas/Flutter games draw their dialogs; there is nothing to query in the DOM,
# so the controls are addressed by viewport position. Measured on a 1280x720
# viewport: the HOW TO PLAY dialog's X sits at ~(849,124) and the dialog is
# roughly 390..890 px wide. Enter/Space also dismiss these.
_SHADOW_TILE_COUNT = """() => {
  const count = (root) => {
    let n = root.querySelectorAll('.tile').length;
    for (const e of root.querySelectorAll('*'))
      if (e.shadowRoot) n += count(e.shadowRoot);
    return n;
  };
  return count(document);
}"""

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


def _dismiss_teuteuf_banner(page):
    """Dismiss the "Teuteuf Edu is here!" banner (iter-15 P3).

    The banner overlaps the page header in every Worldle frame and
    was never dismissed by _close_modals. Best-effort: click the
    banner's own close/dismiss control; if it has none, hide the
    smallest banner container (height-capped, so a page-level
    container is never hidden). Never raises; a no-op when the
    banner is absent.

    Iter-16: visibility is rect-based (a fixed-position banner has
    a null offset parent, so the old check saw it as absent), and
    a close click is verified by re-querying — a click that does
    not remove the banner falls back to hiding its container.

    Iter-17 (ANALYSIS13 §8): the probe STILL reported 'absent'
    while the banner sat in every Worldle frame. Root cause: the
    old probe matched the EXACT substring 'Teuteuf Edu' against
    per-element innerText in the light DOM only. That misses the
    banner when (a) its text is split across block boundaries so
    innerText inserts a line break inside "Teuteuf Edu", (b) the
    text lives in SVG <text> (no innerText), (c) it lives inside
    a shadow root, or (d) it lives inside an iframe. The new
    probe normalises whitespace (collapse all runs to one space,
    case-insensitive), matches against textContent (which covers
    SVG text), and searches the light DOM + every shadow root +
    every same-origin iframe document. (The iter-17 body-level
    textContent pre-check was REMOVED in iter-18 — see below —
    because it short-circuited the shadow/iframe search.) The log now
    names HOW the banner was found (light-dom / shadow-dom /
    iframe / body-text), so a future 'absent' vs frame mismatch is
    diagnosable instead of a bare claim.

    Iter-18 (ANALYSIS15 §8): the iter-17 probe STILL lied. Root
    cause: its body-level pre-check — `if (!bodyText.includes
    ('teuteuf edu')) return 'absent'` — reads
    `document.body.textContent`, which does NOT include shadow-DOM
    or iframe content. The shadow/iframe search below was dead
    code in exactly the case it claimed to cover: a banner living
    only in a shadow root or iframe returned 'absent' without any
    root ever being searched. The pre-check gate is GONE: every
    root's text is searched, and 'absent' is returned only after
    all roots fail to match. A second root cause: the probe can
    run before the banner renders (late-render), so a single
    instant 'absent' is weak — an 'absent' result is now re-scanned
    once after 500ms before it is logged. 'absent' therefore means
    "not found at call time after a re-scan", never "proven not
    on the page". The banner is non-blocking; this never blocks
    anything — a lying 'absent' is worse than no claim, so the
    probe would rather return 'present-*-no-element' than lie.

    Iter-19 (ANALYSIS16 §6 item 15): this is a WORLDLE-only probe
    (the banner lives on teuteuf.fr), but _close_modals calls it for
    EVERY game, so every game's log got a "teuteuf edu banner:
    absent" line — e.g. the stray line in the waffle log, a
    wrong-game probe label. The probe now runs ONLY when the page
    is Worldle (or its game is unknown, e.g. a direct call / test):
    when page._qp_gid names a KNOWN non-Worldle game, or the page
    URL names a non-Teuteuf host, return silently — no probe, no
    log line, no label.

    Iter-19 (ANALYSIS16 §2f): the iter-18 probe STILL returned
    'absent' ~30x while the banner sat in every frame. Its text
    match (norm(textContent) vs 'teuteuf edu') is blind to three
    real banner renderings: (a) a title split across sibling
    spans with no whitespace text node between them
    (textContent 'TeuteufEdu', no space), (b) a brand mark whose
    text lives in alt/aria-label/title, not textContent, and
    (c) a CROSS-ORIGIN iframe, whose text no same-origin probe
    can ever read. The probe now matches the space-stripped
    signature ('teuteufedu') too, folds descendant
    alt/aria-label/title into the signature, and — when no
    element matches — reports a banner-shaped unreadable iframe
    as 'present-iframe-unreadable' (with its rect) instead of a
    lying 'absent'; Python dismisses it with a real click on the
    frame's top-right corner (the X), and hides the iframe
    element itself only if the click did not dismiss it. The
    close-button search also matches icon-only controls whose
    class/id names them close/dismiss.

    Iter-20 (ANALYSIS17 §2g): the probe STILL said 'absent' 30x
    while the banner sat in every frame (gp_0135/0360/0380). Two
    more gaps closed: (a) the brand signature is not required —
    the banner's BODY SENTENCE ('You asked, we listened! ...
    educational resource, built for classrooms.') now matches
    too, covering a brand mark rendered as an image/SVG logo
    with no text; (b) the X close search now covers bare
    span/SVG/div close controls (not just button/[role=button]/
    [aria-label]/a). And a bare 'absent' is GONE: when nothing
    matches, the probe returns 'absent-candidates:' + the
    candidate evidence (roots/shadow hosts, every iframe with
    readability + rect, top-of-page edu/classroom mentions,
    top custom elements = possible closed-shadow hosts,
    teuteuf alt/aria/title hits) so the next artifact is
    diagnosable from the log alone. Worldle-only scoping is
    unchanged (waffle log stays clean).

    Iter-21 (ANALYSIS18 §6.6): the probe STILL said 'absent'
    30x while the banner sat in every frame. Three more gaps
    closed: (a) the iframe-shape guards capped width at 1300 /
    height at 220, excluding a FULL-WIDTH (1920px) banner
    iframe and a wrapped two-line banner — caps are now 2000 /
    320 (still <50% viewport height); (b) textContent misses
    rendered-only text (a shadow-DOM banner surfaced through
    its host's innerText — textContent never crosses a shadow
    boundary, innerText does), so a rendered-text pass now
    runs when body innerText matches a banner signature but
    no per-element textContent did, with re-verification in
    the same signal; (c) the no-match label is finally
    honest — 'unverified-no-element-match:' ("no matching
    element found by any pass"), NEVER 'absent'. The
    diagnostics also name every custom host + whether it
    has an open shadow root, so a banner in a CLOSED shadow
    root is attributable to a host element instead of
    invisible. The banner stays non-blocking; a no-match is
    logged as unverified, never as a dismissal.

    Iter-22 (ANALYSIS19 §4): the probe STILL said
    'unverified-no-element-match' ~20x with the banner
    frame-visible. The remaining blind spot is a CLOSED
    shadow root on a PLAIN (non-custom) host: closed content
    is invisible to textContent/innerText/shadowRoot
    enumeration, and a plain div/section host never appears
    in custom_top. The diagnostics now add a HOLLOW-BOX
    list (top-of-page elements with non-zero rect but EMPTY
    textContent = shadow hosts, with elementFromPoint at
    the box centre = the host), and Python dismisses via
    coordinates — a real mouse click on the banner's
    top-right X — with re-verification: believed only when
    the hollow box is gone on re-probe. Fix or drop, never
    lie: a still-present hollow box is logged as NOT
    dismissed."""
    try:
        _gid = getattr(page, "_qp_gid", None)
        if _gid in GAMES and _gid != "worldle":
            return
        _url = str(getattr(page, "url", "") or "")
        if _url and "teuteuf" not in _url:
            return
    except Exception:
        pass
    _JS = """/* TEUTEUF-BANNER-DISMISS */
          () => {
          const norm = s => (s || '').toLowerCase()
            .replace(/\\s+/g, ' ').trim();
          // Iter-19: the iter-18 probe STILL lied ('absent' x30
          // while the banner sat in every frame). Its hasSig
          // matched ONLY norm(textContent) against 'teuteuf edu' —
          // which misses the banner when (a) its title is split
          // across sibling spans with NO whitespace text node
          // between them, so textContent reads 'TeuteufEdu'
          // (no space), (b) the brand mark is an <img> whose
          // text lives in alt/aria-label/title, not textContent,
          // or (c) the banner lives in a CROSS-ORIGIN iframe
          // (contentDocument null/inaccessible), so its text is
          // invisible to every same-origin probe. hasSig now
          // matches the space-stripped form too ('teuteufedu')
          // and folds descendant alt/aria-label/title into the
          // signature; the no-element fallback below reports a
          // banner-shaped unreadable iframe instead of 'absent'.
          const nospace = s => (s || '').toLowerCase()
            .replace(/\\s+/g, '');
          const sigOf = el => {
            const parts = [el.textContent || ''];
            try {
              for (const d of el.querySelectorAll(
                  '[alt], [aria-label], [title]')) {
                parts.push(d.getAttribute('alt') || '');
                parts.push(d.getAttribute('aria-label') || '');
                parts.push(d.getAttribute('title') || '');
              }
            } catch (err) {}
            return parts.join(' ');
          };
          // Iter-20 (ANALYSIS17 §2g): the banner STILL read
          // 'absent' x30 while sitting in every frame. The brand
          // signature alone is not enough: match the banner's
          // BODY SENTENCE too ('You asked, we listened! Teuteuf
          // Edu is our new educational resource, built for
          // classrooms.') — if the brand mark renders as an
          // image/SVG logo with no text, or the heading is
          // split across nodes the brand match cannot see, the
          // sentence still identifies the banner. The sentence
          // is banner-specific on Worldle (this probe is
          // Worldle-only), so it cannot match game content.
          const hasSig = el => { try {
            const s = sigOf(el);
            const n = norm(s), ns = nospace(s);
            return n.includes('teuteuf edu') ||
              ns.includes('teuteufedu') ||
              n.includes('you asked, we listened') ||
              n.includes('educational resource') ||
              n.includes('built for classrooms') ||
              ns.includes('youaskedwelistened') ||
              ns.includes('educationalresource') ||
              ns.includes('builtforclassrooms');
            } catch (err) { return false; } };
          // Rect-based visibility (iter-16): a FIXED-position
          // banner has a null offset parent, so the old
          // visibility check reported it 'absent' while it was
          // visible in every frame.
          const vis = el => { if (!el) return false;
            try { const r = el.getBoundingClientRect();
              return r.width > 0 && r.height > 0; }
            catch (err) { return false; } };
          // Iter-18: NO body-level pre-check gate. The iter-17
          // probe returned 'absent' here whenever the banner
          // lived in a shadow root or an iframe, because
          // document.body.textContent does NOT include shadow-DOM
          // or iframe content — so the shadow/iframe search
          // below was dead code in exactly the case it claimed
          // to cover. Every root's text is searched below, and
          // 'absent' is returned only after all roots fail.
          // Roots to search: the main document, every shadow root
          // (recursively), and every same-origin iframe document
          // (cross-origin frames throw on access and are skipped).
          const roots = [{root: document, method: 'light-dom'}];
          const seenShadow = new Set();
          const collectShadow = rt => {
            for (const el of rt.querySelectorAll('*')) {
              if (el.shadowRoot && !seenShadow.has(el.shadowRoot)) {
                seenShadow.add(el.shadowRoot);
                roots.push({root: el.shadowRoot,
                            method: 'shadow-dom'});
                collectShadow(el.shadowRoot);
              }
            }
          };
          try { collectShadow(document); } catch (err) {}
          for (const fr of Array.from(
              document.querySelectorAll('iframe'))) {
            try { const d = fr.contentDocument;
              if (d) roots.push({root: d, method: 'iframe'});
            } catch (err) {}
          }
          const find = () => {
            const out = [];
            for (const {root, method} of roots) {
              let els = [];
              try { els = Array.from(root.querySelectorAll(
                'body *')).filter(vis).filter(hasSig); }
              catch (err) {}
              if (!els.length) {
                try { els = Array.from(root.querySelectorAll('*'))
                  .filter(vis).filter(hasSig); }
                catch (err) {}
              }
              for (const el of els) out.push({el, method});
            }
            return out;
          };
          const cands = find();
          if (!cands.length) {
            // Signature in some root's text but no matching
            // element: the text is rendered where per-element
            // textContent cannot see it (e.g. canvas). Truthful,
            // not 'absent'. Every root's text is checked — the
            // light DOM, each shadow root, each same-origin
            // iframe document — never just document.body.
            let sigInText = false;
            for (const {root} of roots) {
              try {
                const t = root === document
                  ? (document.body ? document.body.textContent : '')
                  : (root.body ? root.body.textContent
                               : (root.textContent || ''));
                // Iter-20: body-sentence signatures too (see
                // hasSig) — the brand mark may be an image.
                if (norm(t).includes('teuteuf edu is here') ||
                    nospace(t).includes('teuteufeduishere') ||
                    norm(t).includes('you asked, we listened') ||
                    norm(t).includes('educational resource') ||
                    norm(t).includes('built for classrooms')) {
                  sigInText = true; break;
                }
              } catch (err) {}
            }
            if (sigInText) return 'present-body-text-no-element';
            // Iter-19: CROSS-ORIGIN iframes. A same-origin probe
            // can never read their text (contentDocument is null
            // or throws), so a banner living in one is invisible
            // to every check above — yet it is frame-visible.
            // A banner-shaped UNREADABLE iframe at the top of the
            // page is the banner until proven otherwise: report
            // it (with its rect, so Python can click its X) and
            // NEVER return a lying 'absent' for it. Shape guards
            // (short, top-of-page, <50% viewport height) keep the
            // game itself / a tall embed from ever matching.
            for (const fr of Array.from(
                document.querySelectorAll('iframe'))) {
              let readable = false;
              try { readable = !!fr.contentDocument; }
              catch (err) { readable = false; }
              if (readable) continue;
              let r;
              try { r = fr.getBoundingClientRect(); }
              catch (err) { continue; }
              if (!r || r.width <= 0 || r.height <= 0) continue;
              const vh = window.innerHeight || 0;
              // Iter-21: the width cap (1300) excluded a
              // FULL-WIDTH banner iframe (1920px viewport) and
              // the height cap (220) excluded a wrapped
              // two-line banner — both are exactly the
              // banner's shape on a wide viewport. Caps are
              // now viewport-relative (width <= 2000, height
              // <= 320, still <50% of viewport height), so a
              // full-width top banner iframe is reported
              // instead of a lying 'absent'.
              if (r.height >= 20 && r.height <= 320 &&
                  r.width >= 200 && r.width <= 2000 &&
                  r.top >= -20 && r.top <= 260 &&
                  r.height < vh * 0.5) {
                return 'iframe-banner:' + JSON.stringify(
                  {x: r.x, y: r.y, w: r.width, h: r.height});
              }
            }
            // Iter-21: RENDERED-TEXT pass. textContent misses
            // text that only exists as rendered text (e.g. a
            // shadow-DOM banner surfaced through its host's
            // innerText — textContent never crosses a shadow
            // boundary, innerText does). If the whole body's
            // RENDERED text contains a banner signature while
            // no per-element textContent matched, search
            // per-element innerText for the smallest matching
            // element and run the same close/hide flow on it.
            // This pass runs ONLY when body innerText matches
            // (one layout read), never as a blanket scan.
            try {
              const bodyInner = norm(document.body ?
                document.body.innerText : '');
              if (bodyInner.includes('teuteuf edu') ||
                  bodyInner.includes('you asked, we listened') ||
                  bodyInner.includes('educational resource') ||
                  bodyInner.includes('built for classrooms')) {
                const innerCands = [];
                for (const el of document.querySelectorAll('body *')) {
                  if (!vis(el)) continue;
                  const it = norm(el.innerText || '');
                  if (it.includes('teuteuf edu') ||
                      it.includes('you asked, we listened') ||
                      it.includes('educational resource') ||
                      it.includes('built for classrooms'))
                    innerCands.push({el, method: 'innerText'});
                }
                if (innerCands.length) {
                  innerCands.sort((a, b) =>
                    norm(a.el.textContent).length -
                    norm(b.el.textContent).length);
                  // Reuse the close/hide flow below by
                  // treating the innerText match as the
                  // found candidate set.
                  cands.push(...innerCands);
                }
              }
            } catch (err) {}
            // Iter-20 (ANALYSIS17 §2g): NEVER a bare 'absent'
            // again — a bare 'absent' vs frame-visible banner is
            // exactly the lie this probe has told for 3
            // iterations. Return the CANDIDATE evidence instead:
            // roots searched, open shadow hosts, every iframe
            // with its readability + rect (the banner may live
            // in an unreadable iframe whose rect misses the
            // banner-shape guards), top-of-page elements
            // mentioning edu/classroom/asked/is-here, custom
            // elements near the top (possible CLOSED shadow
            // hosts, which no same-origin probe can pierce),
            // and any element whose alt/aria-label/title
            // mentions teuteuf. The next artifact is then
            // diagnosable from the log alone.
            // Iter-21: the diagnostics return below runs ONLY
            // when the rendered-text pass ALSO found nothing —
            // an innerText match above falls through to the
            // close/hide flow instead of reporting no-match.
            // And the label is honest: 'unverified-...' means
            // "no matching element found by any pass", NEVER
            // "the banner is absent" (frame-visible banners
            // have disproven that claim for 4 iterations).
            // 'absent-candidates:' survives in the Python
            // handler below for legacy callers/fakes.
            if (!cands.length) {
            const diag = {roots: roots.length, shadow_hosts: 0,
              iframes: [], top_cands: [], custom_top: [],
              attr_teuteuf: [], custom_hosts: [],
              hollow_boxes: []};
            try { diag.shadow_hosts = seenShadow.size; }
            catch (err) {}
            for (const fr of Array.from(
                document.querySelectorAll('iframe'))) {
              let readable = false;
              try { readable = !!fr.contentDocument; }
              catch (err) { readable = false; }
              let r = null;
              try { const rr = fr.getBoundingClientRect();
                r = {x: Math.round(rr.x), y: Math.round(rr.y),
                     w: Math.round(rr.width),
                     h: Math.round(rr.height)}; }
              catch (err) {}
              diag.iframes.push({readable: readable, rect: r,
                src: String(fr.getAttribute('src') || '')
                  .slice(0, 80)});
            }
            for (const el of document.querySelectorAll('body *')) {
              let r;
              try { r = el.getBoundingClientRect(); }
              catch (err) { continue; }
              if (!r || r.width <= 0 || r.height <= 0) continue;
              if (r.top < -20 || r.top > 300) continue;
              const t = norm(el.textContent || '');
              const cls = String(el.getAttribute('class') || '')
                .slice(0, 60);
              if (t.includes('edu') || t.includes('classroom') ||
                  t.includes('you asked') || t.includes('is here')) {
                if (diag.top_cands.length < 12)
                  diag.top_cands.push({tag: el.tagName, cls: cls,
                    text: t.slice(0, 90)});
              }
              if ((el.tagName || '').includes('-')) {
                if (diag.custom_top.length < 12)
                  diag.custom_top.push({tag: el.tagName, cls: cls,
                    rect: {x: Math.round(r.x), y: Math.round(r.y),
                           w: Math.round(r.width),
                           h: Math.round(r.height)}});
                // Iter-21: CLOSED-shadow hosts are the one
                // remaining blind spot (el.shadowRoot null
                // covers both closed-shadow and no-shadow, so
                // has_shadow=False is "unsearchable or plain",
                // not "proven no shadow"). Name every custom
                // host + its shadow state in the log so a
                // banner hiding in a closed root is at least
                // attributable to a host element.
                if (diag.custom_hosts.length < 12)
                  diag.custom_hosts.push({tag: el.tagName, cls: cls,
                    has_shadow: !!el.shadowRoot,
                    rect: {x: Math.round(r.x), y: Math.round(r.y),
                           w: Math.round(r.width),
                           h: Math.round(r.height)}});
              }
              for (const a of ['alt', 'aria-label', 'title']) {
                const v = el.getAttribute(a) || '';
                if (norm(v).includes('teuteuf') &&
                    diag.attr_teuteuf.length < 8) {
                  diag.attr_teuteuf.push({tag: el.tagName, cls: cls,
                    attr: a, val: v.slice(0, 60)});
                }
              }
            }
            // Iter-22 (ANALYSIS19 §4): HOLLOW-BOX diagnostic.
            // A banner in a CLOSED shadow root on a PLAIN
            // (non-custom) host is invisible to every pass
            // above: closed content is invisible to
            // textContent/innerText/shadowRoot enumeration, and
            // a plain div/section host never appears in
            // custom_top. Its signature is a HOLLOW BOX: a
            // top-of-page element with a non-zero rect but
            // EMPTY textContent (shadow content does not count
            // toward the host's textContent) — a rendered box
            // with nothing in the light DOM to render. The
            // widest hollow boxes are the banner candidates;
            // elementFromPoint at the box centre returns the
            // host for closed shadow (hit-testing pierces
            // shadow trees even when the DOM API cannot), so
            // the host at the centre is recorded too.
            try {
              const hollow = [];
              for (const el of document.querySelectorAll('body *')) {
                const tag = (el.tagName || '').toUpperCase();
                if (['SCRIPT', 'STYLE', 'LINK', 'META', 'TITLE',
                     'HEAD', 'NOSCRIPT', 'TEMPLATE',
                     'SLOT'].includes(tag)) continue;
                let r;
                try { r = el.getBoundingClientRect(); }
                catch (err) { continue; }
                if (!r || r.width < 200 || r.height < 20 ||
                    r.height > 320) continue;
                if (r.top < -20 || r.top > 300) continue;
                if ((el.textContent || '').trim() !== '') continue;
                const cx = r.x + r.width / 2, cy = r.y + r.height / 2;
                let hostAtCentre = null;
                try {
                  const hit = document.elementFromPoint(cx, cy);
                  if (hit) hostAtCentre = {tag: hit.tagName,
                    cls: String(hit.getAttribute('class') || '')
                      .slice(0, 60),
                    id: hit.id || ''};
                } catch (err) {}
                // Iter-23 (ANALYSIS20 §4): BANNER SIGNATURE.
                // The hollow-box heuristic fired on game UI —
                // it mistook the Kuwait Area card (a BUTTON
                // with a country-shape <img>) for a banner box
                // and clicked it, answering a puzzle round by
                // luck. A hollow box is a coordinate-X
                // candidate ONLY with a banner signature: a
                // NON-interactive container (not a button /
                // link / input / img), with NO light-DOM game
                // content inside it (no img / button / link —
                // a closed-shadow banner host has no light-DOM
                // descendants at all, while a game card's
                // img / button live in the light DOM), whose
                // centre hit is not interactive, and which is
                // banner-shaped (wide, short, top-of-page).
                // Boxes WITHOUT the signature are still
                // DIAGNOSED (rejection reasons below) but are
                // never clicked.
                const sigReasons = [];
                const interactiveTags = ['BUTTON', 'A', 'INPUT',
                  'SELECT', 'TEXTAREA', 'LABEL', 'IMG', 'VIDEO',
                  'AUDIO', 'CANVAS'];
                if (interactiveTags.includes(tag))
                  sigReasons.push('interactive-tag:' + tag);
                const roleL = String(
                  el.getAttribute('role') || '').toLowerCase();
                if (['button', 'option', 'radio', 'tab', 'link',
                     'menuitem', 'checkbox', 'switch']
                    .includes(roleL))
                  sigReasons.push('interactive-role:' + roleL);
                let hasGameDesc = false;
                try { hasGameDesc = !!el.querySelector(
                  'img, button, a, input, select, ' +
                  '[role=button], [role=option]');
                } catch (err) {}
                if (hasGameDesc)
                  sigReasons.push('light-dom-game-descendant');
                const hostTagU = hostAtCentre ?
                  String(hostAtCentre.tag || '')
                    .toUpperCase() : '';
                if (interactiveTags.includes(hostTagU))
                  sigReasons.push(
                    'centre-hit-interactive:' + hostTagU);
                if (!(r.width >= 320 && r.height >= 20 &&
                      r.height <= 200))
                  sigReasons.push('not-banner-shaped:' +
                    Math.round(r.width) + 'x' +
                    Math.round(r.height));
                if (!(r.top >= -20 && r.top <= 200))
                  sigReasons.push('not-top-of-page:' +
                    Math.round(r.top));
                hollow.push({tag: el.tagName,
                  cls: String(el.getAttribute('class') || '')
                    .slice(0, 60),
                  id: el.id || '',
                  rect: {x: Math.round(r.x), y: Math.round(r.y),
                         w: Math.round(r.width),
                         h: Math.round(r.height)},
                  children: el.childElementCount || 0,
                  host_at_centre: hostAtCentre,
                  signature: sigReasons.length === 0,
                  signature_reasons: sigReasons});
              }
              hollow.sort((a, b) =>
                ((b.signature ? 1 : 0) - (a.signature ? 1 : 0)) ||
                ((b.rect.w * b.rect.h) - (a.rect.w * a.rect.h)));
              diag.hollow_boxes = hollow.slice(0, 8);
            } catch (err) {}
            return 'unverified-no-element-match:' +
              JSON.stringify(diag);
            }
          }
          cands.sort((a, b) =>
            norm(a.el.textContent).length -
            norm(b.el.textContent).length);
          const leaf = cands[0].el;
          const foundMethod = cands[0].method;
          const suffix = foundMethod === 'light-dom' ? ''
            : ' (' + foundMethod + ')';
          let node = leaf;
          for (let d = 0; d < 6 && node; d++) {
            // Iter-20: the banner's X is not necessarily a
            // <button> — it may be a bare <span>/SVG/div with an
            // onClick, a close-named class, or a close
            // data-testid. Search those too, or the X is never
            // found and the probe falls through to hiding the
            // container (which still works, but only after the
            // close search fails).
            const btns = node.querySelectorAll
              ? Array.from(node.querySelectorAll(
                  'button, [role=button], [aria-label], a, span, ' +
                  'svg, [class*=close i], [class*=dismiss i], ' +
                  '[data-testid*=close i], [data-testid*=dismiss i]'))
              : [];
            const close = btns.find(b => {
              const t = (((b.getAttribute('aria-label') || '') + ' ' +
                (b.getAttribute('title') || '') + ' ' +
                (b.textContent || '')).trim().toLowerCase());
              // Iter-19: also match an icon-only close control
              // (empty text, SVG child) whose class/id names it
              // close/dismiss — the banner's X may carry no text.
              const clsId = (((b.getAttribute('class') || '') + ' ' +
                (b.getAttribute('id') || '') + ' ' +
                (b.getAttribute('data-testid') || '')).toLowerCase());
              return t === 'x' || t === '✕' || t === '×' ||
                t === 'close' || t.includes('dismiss') ||
                t.includes('close') || clsId.includes('close') ||
                clsId.includes('dismiss');
            });
            if (close && vis(close)) {
              close.click();
              // Verify the click actually removed it (a React
              // re-render can restore a DOM-clicked banner) —
              // otherwise hide the container instead of reporting
              // a success that is still on screen.
              // Iter-21: when the banner was found via the
              // rendered-text pass, find() (textContent-only)
              // can never see it — re-verify with the SAME
              // innerText signal, or a no-op click would be
              // reported as 'closed'.
              if (foundMethod === 'innerText') {
                let gone = false;
                try {
                  const bi = norm(document.body ?
                    document.body.innerText : '');
                  gone = !(bi.includes('teuteuf edu') ||
                    bi.includes('you asked, we listened') ||
                    bi.includes('educational resource') ||
                    bi.includes('built for classrooms'));
                } catch (err) { gone = false; }
                if (gone)
                  return 'closed-via-button' + suffix;
                let box = leaf;
                for (let d2 = 0; d2 < 4 && box && box.parentElement;
                     d2++) {
                  const r = box.getBoundingClientRect();
                  if (r.height > 0 && r.height <= 300) {
                    box.style.setProperty('display', 'none',
                      'important');
                    return 'closed-via-button+hidden-container' +
                      suffix;
                  }
                  box = box.parentElement;
                }
                return 'close-clicked-still-present' + suffix;
              }
              const still = find();
              if (!still.length)
                return 'closed-via-button' + suffix;
              still.sort((a, b) =>
                norm(a.el.textContent).length -
                norm(b.el.textContent).length);
              let box = still[0].el;
              for (let d2 = 0; d2 < 4 && box && box.parentElement; d2++) {
                const r = box.getBoundingClientRect();
                if (r.height > 0 && r.height <= 300) {
                  box.style.setProperty('display', 'none', 'important');
                  return 'closed-via-button+hidden-container' + suffix;
                }
                box = box.parentElement;
              }
              return 'close-clicked-still-present' + suffix;
            }
            node = node.parentElement;
          }
          let box = leaf;
          for (let d = 0; d < 4 && box.parentElement; d++) {
            const r = box.getBoundingClientRect();
            if (r.height > 0 && r.height <= 300) {
              box.style.setProperty('display', 'none', 'important');
              return 'hidden-container' + suffix;
            }
            box = box.parentElement;
          }
          return 'found-no-close' + suffix;
        }"""
    _HIDE_IFRAME_JS = """/* TEUTEUF-BANNER-HIDE-IFRAME */
          (rect) => {
          for (const fr of document.querySelectorAll('iframe')) {
            const r = fr.getBoundingClientRect();
            if (Math.abs(r.x - rect.x) < 2 &&
                Math.abs(r.y - rect.y) < 2 &&
                Math.abs(r.width - rect.w) < 2 &&
                Math.abs(r.height - rect.h) < 2) {
              fr.style.setProperty('display', 'none', 'important');
              return true;
            }
          }
          return false;
        }"""
    try:
        res = page.evaluate(_JS)
    except Exception:
        return
    if isinstance(res, str) and res.startswith("iframe-banner:"):
        # Iter-19: cross-origin banner iframe — the probe can see
        # the frame but not its text. Dismiss via a REAL click on
        # the frame's top-right corner (the X position), then
        # re-probe: gone -> closed; still there -> hide the iframe
        # element itself (banner-shaped by the JS guards, so
        # hiding it cannot hide the game) and log exactly that.
        import json as _json
        try:
            _rect = _json.loads(res[len("iframe-banner:"):])
        except Exception:
            _rect = {}
        try:
            _dbg(getattr(page, "_qp_gid", "banner"),
                 "teuteuf edu banner: present-iframe-unreadable "
                 f"rect={_rect}")
        except Exception:
            pass
        try:
            page.mouse.click(
                float(_rect.get("x", 0)) + float(_rect.get("w", 0)) - 16,
                float(_rect.get("y", 0)) + 16)
            page.wait_for_timeout(500)
            res = page.evaluate(_JS)
        except Exception:
            res = "iframe-corner-click-failed"
        if res == "absent":
            res = "closed-iframe-via-corner-click"
        elif isinstance(res, str) and (
                res.startswith("unverified-no-element-match:")
                or res.startswith("absent-candidates:")):
            # Iter-21: the JS probe no longer returns a bare
            # 'absent' — a no-match diagnostics result after a
            # corner click means the banner iframe is gone from
            # the probe's view (absence itself is unverified,
            # but the iframe-banner signal is cleared).
            res = ("closed-iframe-via-corner-click "
                   "(unverified-absence)")
        elif isinstance(res, str) and res.startswith("iframe-banner:"):
            try:
                _hidden = page.evaluate(_HIDE_IFRAME_JS, _rect)
            except Exception:
                _hidden = False
            res = ("hidden-iframe-banner" if _hidden
                   else "iframe-corner-click-did-not-dismiss")
    # Iter-22 (ANALYSIS19 §4): HOLLOW-BOX coordinate X.
    # When the diagnostics name a hollow box (top-of-page,
    # non-zero rect, EMPTY textContent = closed-shadow host),
    # dismiss via coordinates: a real mouse click on the
    # banner's top-right X (elementFromPoint at the banner
    # centre returns the host for closed shadow, and real
    # hit-testing pierces the shadow tree). Re-verify
    # afterwards: the click is only BELIEVED when the
    # hollow box is gone on re-probe (or the probe now
    # finds + closes the banner); a still-present hollow
    # box is logged as NOT dismissed, never claimed.
    _hollow_handled = False
    if isinstance(res, str) and res.startswith(
            "unverified-no-element-match:"):
        _hollow = []
        try:
            import json as _json
            _diag = _json.loads(
                res[len("unverified-no-element-match:"):])
            _hollow = _diag.get("hollow_boxes") or []
        except Exception:
            _hollow = []
        if _hollow:
            # Iter-23 (ANALYSIS20 §4): BANNER SIGNATURE gate.
            # Only a hollow box the JS annotated signature=True
            # may be coordinate-X clicked. Production JS always
            # emits `signature`; a MISSING field means a legacy
            # diagnostic (tests) and passes through. An explicit
            # False blocks the click — the Kuwait Area card and
            # every other game-UI box fail the signature, so the
            # coordinate-X path can no longer click game UI.
            _sig_boxes = [b for b in _hollow
                          if isinstance(b, dict)
                          and b.get("signature") is not False]
            _box = _sig_boxes[0] if _sig_boxes else None
            if _box is None:
                try:
                    _dbg(getattr(page, "_qp_gid", "banner"),
                         "teuteuf edu banner: hollow-box candidates "
                         "lack banner signature — NOT clicking "
                         "game UI (coordinate-X path requires "
                         "banner signature) "
                         f"candidates={_hollow}")
                except Exception:
                    pass
                _hollow_handled = True
            if _box is not None:
                _rect = _box.get("rect") or {}
                try:
                    _hx = float(_rect.get("x", 0)) \
                        + float(_rect.get("w", 0)) - 16
                    _hy = float(_rect.get("y", 0)) + 16
                except (TypeError, ValueError):
                    _hx, _hy = 0.0, 0.0
                try:
                    _dbg(getattr(page, "_qp_gid", "banner"),
                         "teuteuf edu banner: hollow-box candidate "
                         f"{_box} — clicking X at ({_hx:.0f}, {_hy:.0f})")
                except Exception:
                    pass
                try:
                    page.mouse.click(_hx, _hy)
                    page.wait_for_timeout(500)
                    _res2 = page.evaluate(_JS)
                except Exception:
                    _res2 = "hollow-box-click-failed"
                if isinstance(_res2, str) and not _res2.startswith(
                        ("unverified-no-element-match:",
                         "absent-candidates:")):
                    res = (str(_res2) + " (via hollow-box coordinate X)")
                elif isinstance(_res2, str):
                    _still = []
                    try:
                        import json as _json
                        _diag2 = _json.loads(
                            _res2[len("unverified-no-element-match:"):]
                            if _res2.startswith(
                                "unverified-no-element-match:")
                            else "{}")
                        _still = _diag2.get("hollow_boxes") or []
                    except Exception:
                        _still = []
                    _gone = True
                    for _b in _still:
                        _r2 = _b.get("rect") or {}
                        try:
                            if abs(float(_r2.get("x", -999))
                                   - float(_rect.get("x", 0))) < 4 \
                                    and abs(float(_r2.get("y", -999))
                                            - float(_rect.get("y", 0))) < 4:
                                _gone = False
                        except (TypeError, ValueError):
                            pass
                    if _gone:
                        res = ("closed-hollow-box-via-coordinate-X "
                               "(hollow box gone on re-probe; banner "
                               "dismissal unverified by text probe)")
                    else:
                        res = ("hollow-box-X-click-did-not-dismiss "
                               "(hollow box still present; banner NOT "
                               "claimed dismissed)")
                else:
                    res = ("hollow-box-X-click-result-unreadable "
                           "(banner NOT claimed dismissed)")
                _hollow_handled = True
    if not _hollow_handled and (res == "absent" or (isinstance(res, str)
                           and (res.startswith("absent-candidates:")
                                or res.startswith(
                                    "unverified-no-element-match:")))):
        # Iter-18 late-render re-scan: the probe can run before
        # the banner renders, so a single instant 'absent' is
        # weak. Re-scan once after 500ms before logging 'absent'.
        # Safe to re-run: an 'absent' first pass performed no
        # close/hide side effect. Bounded (+500ms, one retry).
        # Iter-20: 'absent-candidates:' (candidate diagnostics,
        # never a bare 'absent') re-scans the same way.
        # Iter-21: 'unverified-no-element-match:' (the honest
        # replacement label — "no matching element found",
        # never "the banner is absent") re-scans the same way.
        try:
            page.wait_for_timeout(500)
            res = page.evaluate(_JS)
        except Exception:
            pass
    # Iter-16 (ANALYSIS12 §11): ALWAYS log the result, including
    # 'absent' — iter-15 logged only on found, and _close_modals'
    # dedup meant the dismiss ran once before the banner rendered
    # (found absent, silent) and never re-ran, so the artifact had
    # ZERO banner lines while the banner sat in every Worldle frame.
    # An absent-at-call-time result is a real observation, not noise.
    try:
        _dbg(getattr(page, "_qp_gid", "banner"),
             f"teuteuf edu banner: {res}")
    except Exception:
        pass


def _close_modals(page, _force=False):
    """One modal sweep per page state (DEDUP, fix-loop iter 3).

    run_one calls this once after the ready-wait; every solver also calls it
    first, so without dedup every game paid for TWO full sweeps (the double
    modal sweep in the startup-delay analysis). Later calls on the same page
    are no-ops unless _force=True (after a reload, when modals can return).
    Faster too: fewer rounds, shorter per-click waits, break-early when no
    dialog is found, canvas-dialog sweep cut to 1 try, and the second
    semantics activation skipped when the first succeeded. No ad waits:
    nothing here waits for ads to load.
    """
    try:
        if getattr(page, "_qp_modals_closed", False) and not _force:
            return page
    except Exception:
        pass
    _sem = _enable_flutter_semantics(page)
    _dismiss_teuteuf_banner(page)
    _dismiss_dialogs(page, rounds=2)
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
    _dismiss_dialogs(page, rounds=1)
    _close_generic_howto(page)
    _dismiss_canvas_dialog(page, tries=1)
    if not _sem:
        _enable_flutter_semantics(page)
    try:
        page._qp_modals_closed = True
    except Exception:
        pass
    return page


def _close_generic_howto(page):
    """Close a "How to Play" / instructions sheet that covers the board.

    Several sites (Phrazle, Searchle, Colordle) open an instructions sheet on
    load. It sits above the board, swallows clicks, and leaves the game looking
    frozen in the video. Its own markup varies, so match on the visible
    heading and then use the sheet's own close affordance, with Escape and a
    click on the backdrop as fallbacks.
    """
    for _ in range(2):
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
    """Close Betweenle's "How to play" overlay - whichever panel id the
    current build uses (#tipspanel on the canvas-migrated site, #helppanel
    on older ones). Untrusted el.click() does NOT dismiss it (the listener
    ignores synthetic clicks), so use a real mouse click on the X button and
    verify the panel is actually hidden before typing anything."""
    for _round in range(4):
        # Current build dismisses the how-to via the panel's PLAY button
        # (#helppanel-pagination-play); the old -close X no longer exists, so
        # without this the overlay swallowed every keystroke (Oct-12/13 fails).
        for _play_sel in ("#helppanel-pagination-play",
                          "button:has-text('PLAY')"):
            try:
                _p = page.query_selector(_play_sel)
                if _p and _p.is_visible():
                    _pb = _p.bounding_box()
                    _p.click(timeout=2000)
                    if _pb:
                        try:
                            QP.ev_push(_pb["x"] + _pb["width"] / 2,
                                       _pb["y"] + _pb["height"] / 2, "click")
                        except Exception:
                            pass
                    page.wait_for_timeout(900)
            except Exception:
                continue
        hidden = True
        for pid in ("tipspanel", "helppanel"):
            try:
                st = page.evaluate(
                    "(p) => { const e = document.getElementById(p);"
                    " if (!e) return 'missing';"
                    " const cs = getComputedStyle(e);"
                    " return cs.display + '/' + cs.visibility; }", pid)
            except Exception:
                st = "missing"
            if st in ("missing", "none/hidden", "none/visible"):
                continue
            hidden = False
            box = None
            try:
                box = page.evaluate("""(p) => {
                  const c = document.getElementById(p + '-close') ||
                    (document.getElementById(p) || {})
                      .querySelector?.('button, .close, [class*=close i]');
                  if (!c) return null;
                  const r = c.getBoundingClientRect();
                  return r.width > 2 ? {x: r.x + r.width / 2,
                                        y: r.y + r.height / 2} : null;
                }""", pid)
            except Exception:
                box = None
            if box:
                page.mouse.click(box["x"], box["y"])
                try:
                    QP.ev_push(box["x"], box["y"], "click")
                except Exception:
                    pass
            else:
                page.mouse.click(960, 120)   # X sits top-right of the panel
            page.wait_for_timeout(900)
        if hidden:
            break
    return page



# ---------------- fix-loop iter 3 helpers ----------------
# Ready selectors: the game's own input/board element. run_one waits for
# one of these instead of a fixed 6 s sleep (fast games proceed as soon as
# the board is there; slow boots get a bounded wait, then the solver's own
# waits take over — never a blind sleep + blind modal sweep).
_READY_SELECTORS = {
    "betweenle": ["button:has-text('DAILY WORD')", "canvas"],
    "colordle": ["input[placeholder='Guess your color here']"],
    "colorfle": ["button:has-text('ENTER')"],
    "searchle": ["input"],
    "semantle": ["input[placeholder='Enter a word...']"],
    "phrazle": [".wordhunt-row"],
    "canuckle": ["flt-glass-pane", "canvas"],
    "phoodle": [".letter-container", "input"],
    "contexto": ["input.word"],
    "worldle": ["input"],
    "globle": ["input[name=guess]"],
    "waffle": [".tile"],
    "worgle": ["game-app"],
    "countryle": ["#rm-app-frame", "iframe"],
    "batterup": ["input"],
    "marveldle": ["input"],
}
_READY_TIMEOUTS = {"canuckle": 30000, "countryle": 30000, "waffle": 20000}


def _wait_ready(page, gid):
    """Wait for the game's input/board selector (bounded, fail-fast log).

    Returns True when a ready selector attached. On timeout it logs and
    returns False — the solver's own waits still handle a slow boot, so a
    timeout here never aborts a run; it just stops the fixed-sleep tax.
    """
    import time as _t
    sels = _READY_SELECTORS.get(gid) or ["input", "button", "canvas"]
    budget = _READY_TIMEOUTS.get(gid, 15000) / 1000.0
    t0 = _t.time()
    for sel in sels:
        left = budget - (_t.time() - t0)
        if left <= 0:
            break
        try:
            page.wait_for_selector(sel, state="attached",
                                   timeout=max(500, min(4000, left * 1000)))
            _dbg(gid, f"ready selector {sel!r} after {_t.time() - t0:.1f}s")
            return True
        except Exception:
            continue
    _dbg(gid, f"ready selector not seen after {_t.time() - t0:.1f}s "
              f"(budget {budget:.0f}s); proceeding to solver")
    return False


# Games whose sites give NO per-letter feedback. Their cards/steps must
# never carry a Wordle-style pattern: Betweenle gives only bound rows,
# Framed gives no feedback at all, Semantle/Contexto give a similarity
# score. A pattern on any of these would render as fabricated letter
# scoring ("2 in the word, wrong spot").
_NO_LETTER_SCORING = {"betweenle", "framed", "semantle", "contexto"}


def _result_steps(gid, aval):
    """Steps for slides/chapters/result.json: PLAYED guesses when the solver
    recorded them, else the plan. A rejected-and-replaced guess must never
    be reported as played (contexto 5-vs-6).

    NO-LETTER-SCORING games (Betweenle, Framed, Semantle, Contexto) never
    get a pattern merged into their steps: their sites give no per-letter
    feedback, so a planner-computed Wordle pattern would render as
    fabricated letter scoring on the answer/breakdown cards.

    Pool evidence: when the planner's steps carry pool_before/pool_after for
    the SAME guesses (colorfle's Mastermind chain 6840->...->1), merge those
    values into the played steps. The played-steps rebuild used to discard
    them (rerun #3: all pool=None) because it rebuilt steps from _PLAYED
    alone. Only merge a step whose guess matches the planner's step, and
    only non-None pool values — never invent a chain."""
    played = _PLAYED.get(gid)
    if played:
        steps = [{"turn": i + 1, "guess": (str(g) if not isinstance(g, str) else g),
                  "pattern": "", "pool_before": None, "pool_after": None}
                 for i, g in enumerate(played)]
        try:
            planned = _steps(gid, aval) or []
            if len(planned) == len(steps):
                for s, p in zip(steps, planned):
                    if not isinstance(p, dict):
                        continue
                    if str(p.get("guess")) != str(s.get("guess")):
                        continue
                    if p.get("pool_before") is not None:
                        s["pool_before"] = p.get("pool_before")
                    if p.get("pool_after") is not None:
                        s["pool_after"] = p.get("pool_after")
                    if p.get("pattern") and gid not in _NO_LETTER_SCORING:
                        s["pattern"] = p.get("pattern")
        except Exception:
            pass
        return steps
    # Betweenle (iter-12 (e)): NO plan fallback. Cards/chapters/counts
    # must reflect ONLY actually-submitted guesses — falling back to the
    # plan fabricated "HOW EACH GUESS SCORED" for 6 planned words when
    # real submitted = 3 (SLATE, DINER, BIKER, run 39043936480). With no
    # confirmed-submitted ledger there are zero true steps, not 6.
    if gid == "betweenle":
        return []
    # Plan fallback: strip planner patterns for no-letter-scoring games
    # (same fabricated-scoring rule as the played-steps merge above).
    _planned = _steps(gid, aval) or []
    if gid in _NO_LETTER_SCORING:
        _planned = [dict(s, pattern="") if isinstance(s, dict) else s
                    for s in _planned]
    return _planned


# Phoodle defensive un-hide (fix-loop iter 3). The live page ships
# <html style="display: none !important">, so nothing paints (blank
# screenshot + blank recording) while DOM text stays readable. This clears
# the inline display:none on <html> and re-clears it if anything re-sets
# it (MutationObserver). It does NOT identify the setter — that still
# needs a live-browser DOM breakpoint on <html>'s style attribute
# (still-open). Applied via add_init_script (before recording starts)
# + explicit evaluate after load, phoodle only.
_PHOODLE_UNHIDE_JS = """(() => {
  const clear = () => {
    try {
      const h = document.documentElement;
      if (!h || !h.style) return 0;
      if (h.style.getPropertyValue('display') === 'none') {
        h.style.removeProperty('display');
        return 1;
      }
      return 0;
    } catch (e) { return 0; }
  };
  const n = clear();
  try {
    const mo = new MutationObserver(() => { clear(); });
    mo.observe(document.documentElement,
               {attributes: true, attributeFilter: ['style']});
    window.__QP_UNHIDE_MO = mo;
  } catch (e) {}
  return n;
})()"""


def _phoodle_unhide(page, gid="phoodle"):
    """Explicitly clear <html> display:none after load / before screenshot."""
    try:
        n = page.evaluate(_PHOODLE_UNHIDE_JS)
        _dbg(gid, f"un-hide <html>: cleared={n} "
                  f"(display:none removed if present; observer armed)")
        return n
    except Exception as e:
        _dbg(gid, f"un-hide <html> failed: {str(e)[:80]}")
        return None


def _gameplay_phase_s(solve_span_s, first_action_s):
    """Pure: gameplay-phase window = solve span minus pre-play time.

    Fix-loop iter 24 (ANALYSIS21 RC6): _SOLVE_SPAN runs START->shot_win
    and therefore includes browser launch, page load, consent/menu
    navigation and idle waits — time during which no gameplay exists.
    The QC coverage check compared the gameplay clip against that whole
    span, so a good Marveldle final (53.3s of gameplay in a 106.7s
    span, most of the span being pre-first-typing load/menu time)
    failed as "gameplay < 50% of solve span". The gameplay phase starts
    at the first recorded player action (_FIRST_TYPE, START-relative,
    same clock as the span), so span - first_action is the window the
    clip must cover. Returns None when either input is missing /
    non-numeric or the phase is not positive — the caller then falls
    back to the whole-span comparison (old behaviour)."""
    try:
        span = float(solve_span_s)
        first = float(first_action_s)
    except (TypeError, ValueError):
        return None
    phase = span - first
    return round(phase, 1) if phase > 0 else None


def _qc_visible_content(gid, vdir, gameplay_path=None, solve_span_s=None,
                        gameplay_phase_s=None):
    """Visible-content QC (shared helper, fix-loop iter 3).

    Metadata QC (solved + 1920x1080 + dur>=40) passed a BLANK phoodle video.
    Fail when gameplay frozen_frac > 0.98 AND mean brightness > 240, OR
    shot_win std < 12. Fix-loop iter 4: also FAIL when the gameplay clip
    covers < 50% of the solve span (START->shot_win) — the rerun-#3 waffle
    clip was 3.75 s for a 59 s solve and passed the frozen/brightness check
    because it only checks content, not coverage. Never raises; returns
    (ok, detail). A missing screenshot/gameplay is reported as skipped, not
    failed — no evidence, no failure — but the detail records the gap.
    """
    import subprocess as _sp
    import tempfile
    detail = {}
    ok = True
    try:
        shot = Path(vdir) / "shot_win.png"
        if shot.exists():
            try:
                from PIL import Image as _PIL
                _im = _PIL.open(shot).convert("L")
                _hist = _im.histogram()
                _n = sum(_hist) or 1
                _mean = sum(i * c for i, c in enumerate(_hist)) / _n
                _var = sum(c * (i - _mean) ** 2 for i, c in enumerate(_hist)) / _n
                _std = _var ** 0.5
                detail["shot_win"] = {"mean": round(_mean, 1),
                                      "std": round(_std, 1)}
                if _std < 12:
                    ok = False
                    detail["shot_win"]["fail"] = "std<12 (near-blank)"
            except Exception as e:
                detail["shot_win"] = {"error": str(e)[:80]}
        else:
            detail["shot_win"] = {"skipped": "no shot_win.png"}
        gp = Path(str(gameplay_path)) if gameplay_path else None
        if gp is not None and gp.exists():
            try:
                from PIL import Image as _PIL2, ImageChops as _Chops
                with tempfile.TemporaryDirectory() as _td:
                    _r = _sp.run(["ffmpeg", "-y", "-v", "error", "-i", str(gp),
                                  "-vf", "fps=1,scale=160:-1,format=gray",
                                  "-frames:v", "120",
                                  str(Path(_td) / "f%04d.png")],
                                 capture_output=True, text=True, timeout=300)
                    _frames = sorted(Path(_td).glob("f*.png"))
                    _means, _diffs = [], []
                    _prev = None
                    for _fp in _frames:
                        _f = _PIL2.open(_fp).convert("L")
                        _h = _f.histogram()
                        _nn = sum(_h) or 1
                        _means.append(sum(i * c for i, c in enumerate(_h)) / _nn)
                        if _prev is not None:
                            _d = _Chops.difference(_prev, _f).histogram()
                            _diffs.append(sum(i * c for i, c in enumerate(_d)) / _nn)
                        _prev = _f
                    if _diffs:
                        _frozen = sum(1 for d in _diffs if d < 1.0) / len(_diffs)
                        _bmean = sum(_means) / len(_means) if _means else 0.0
                        detail["gameplay"] = {"frames": len(_frames),
                                              "frozen_frac": round(_frozen, 3),
                                              "mean_brightness": round(_bmean, 1)}
                        if _frozen > 0.98 and _bmean > 240:
                            ok = False
                            detail["gameplay"]["fail"] = (
                                "frozen_frac>0.98 AND brightness>240 (blank)")
                    else:
                        detail["gameplay"] = {"skipped": "no frames sampled"}
                    # Coverage: the gameplay clip must show the solve, not
                    # just non-blank frames. A 3.75 s clip for a 59 s solve
                    # fails even when its 4 frames are colourful.
                    try:
                        _r_dur = _sp.run(["ffprobe", "-v", "error",
                                          "-show_entries", "format=duration",
                                          "-of", "csv=p=0", str(gp)],
                                         capture_output=True, text=True,
                                         timeout=60)
                        _gp_dur = float((_r_dur.stdout or "0").strip() or 0)
                    except Exception:
                        _gp_dur = 0.0
                    if _gp_dur > 0:
                        detail["gameplay"]["duration_s"] = round(_gp_dur, 1)
                    if solve_span_s and _gp_dur > 0:
                        detail["gameplay"]["solve_span_s"] = round(float(solve_span_s), 1)
                        # Iter 24 (RC6): coverage denominator is the
                        # gameplay-phase window when known (gameplay
                        # clip vs first-action->shot_win), NOT the
                        # whole START->shot_win span — the span
                        # includes pre-play load/menu time that no
                        # gameplay clip can or should cover. Falls
                        # back to the whole span when no phase is
                        # known (old behaviour).
                        _denom = None
                        _denom_label = "solve span"
                        try:
                            if gameplay_phase_s and float(gameplay_phase_s) > 0:
                                _denom = float(gameplay_phase_s)
                                _denom_label = "gameplay phase"
                                detail["gameplay"]["gameplay_phase_s"] = round(_denom, 1)
                        except (TypeError, ValueError):
                            _denom = None
                        if _denom is None:
                            _denom = float(solve_span_s)
                        detail["gameplay"]["coverage"] = round(
                            _gp_dur / _denom, 3)
                        if _gp_dur < 0.5 * _denom:
                            ok = False
                            detail["gameplay"]["fail"] = (
                                f"gameplay < 50% of {_denom_label} "
                                f"({_gp_dur:.1f}s < 50% of "
                                f"{_denom:.1f}s)")
            except Exception as e:
                detail["gameplay"] = {"error": str(e)[:100]}
        else:
            detail["gameplay"] = {"skipped": "no gameplay file"}
    except Exception as e:
        detail["error"] = str(e)[:120]
    return ok, detail


def _semantic_dom_scores(page, word):
    """Score for `word` from the sites' real score DOM (guess list/table).

    Semantle renders each guess as a table row (word + similarity);
    Contexto renders each guess as a list row (word + rank number). Read
    the row for this word and take its number: a trailing '%' number first,
    else the single decimal, else the single integer when the row holds
    only the word + one number. Returns (score_or_None, probe) — None with
    the probe (rows scanned, matched row text) means the DOM exposes no
    per-guess score; the caller must say so honestly, never fake one.
    """
    import re as _re
    probe = {"rows_scanned": 0, "matched_row": None, "numbers": []}
    try:
        rows = page.evaluate("""() => {
          const out = [];
          for (const r of document.querySelectorAll(
              'table tr, li, [class*=guess i], [class*=row i], [class*=entry i]')) {
            const t = (r.innerText || r.textContent || '').trim();
            if (t) out.push(t);
          }
          return out.slice(0, 80);
        }""") or []
    except Exception as e:
        probe["error"] = str(e)[:80]
        return None, probe
    probe["rows_scanned"] = len(rows)
    w = str(word).lower()
    matched = None
    for t in rows:
        low = t.lower()
        if _re.search(r"(?<![a-z])" + _re.escape(w) + r"(?![a-z])", low):
            matched = t
    if not matched:
        return None, probe
    probe["matched_row"] = matched[:160]
    nums = _re.findall(r"[-+]?\d+(?:\.\d+)?%?", matched)
    probe["numbers"] = nums
    m_pct = _re.search(r"([-+]?\d+(?:\.\d+)?)\s*%", matched)
    if m_pct:
        return m_pct.group(1), probe
    decs = [n for n in nums if "." in n]
    if len(decs) == 1:
        return decs[0], probe
    rest = _re.sub(r"(?i)" + _re.escape(w), "", matched)
    rest_nums = _re.findall(r"[-+]?\d+(?:\.\d+)?", rest)
    if len(rest_nums) == 1:
        return rest_nums[0], probe
    return None, probe


def _betweenle_counters(page):
    """The game's own submit counters: GUESS n/14, stats-panel word, WIN popup."""
    try:
        return page.evaluate("""() => {
          const t = document.body.innerText || '';
          const gm = t.match(/GUESS\\s*(\\d+)\\s*\\/\\s*(\\d+)/i) || [];
          const st = document.getElementById('statisticspanel-word');
          return {guess_n: Number(gm[1] || 0),
                  guess_total: Number(gm[2] || 0),
                  stats_word: st ? st.innerText : '',
                  win_popup: /WIN/i.test(t.slice(-600)),
                  tail: t.slice(-200)};
        }""") or {}
    except Exception:
        return {}


def _betweenle_submitted(page, before_n, word):
    """True only if the game's own counters confirm submission.

    Settle-wait, read twice (a single read can lag the canvas app), and
    accept only a GUESS-counter advance or the stats panel showing this
    word. A stale bounds/board read must never produce a 'did not submit'.
    Returns (submitted, counters)."""
    page.wait_for_timeout(1200)
    c1 = _betweenle_counters(page)
    page.wait_for_timeout(800)
    c2 = _betweenle_counters(page)
    if (c2.get("guess_n") or 0) > (before_n or 0):
        return True, c2
    # Stats-panel clause: only a REAL panel word counts. During play
    # the panel carries the 'WORD' placeholder — iter-12 treated that
    # as a submitted guess (the WORD phantom).
    _stats = str(c2.get("stats_word") or "").strip()
    if (_stats and _stats.upper() != "WORD" and word
            and word.lower() in _stats.lower()):
        return True, c2
    if (c2.get("guess_n") or 0) > (c1.get("guess_n") or 0):
        return True, c2
    return False, c2


def _waffle_board_letters(page):
    """Initial board letters in row-major (data-pos) order, or ''.

    The game's logical state lives in data-pos (DOM order never changes on
    a swap), so sorting by data-pos is the only honest board read."""
    import json as _js
    board = _waffle_board(page)
    if not board:
        return ""
    def _pos(t):
        try:
            p = _js.loads(t.get("pos") or "{}")
            return (int(p.get("y", 0)), int(p.get("x", 0)))
        except Exception:
            return (99, 99)
    cells = sorted([i for i, t in enumerate(board) if not t.get("empty")],
                   key=lambda i: _pos(board[i]))
    return "".join(board[i].get("ch") or "" for i in cells)


def _waffle_served_day(board_letters, puzzle_today, puzzle_prev):
    """Pure served-board classifier (unit-testable, no browser).

    Returns (verdict, detail): 'proceed' when the board is today's puzzle,
    'defer' when it is yesterday's (site has not rolled — never solve it),
    'fail' otherwise (wrong length, or matches neither — fail loudly).
    """
    b = str(board_letters or "").upper().replace(" ", "")
    t = str(puzzle_today or "").upper().replace(" ", "")
    p = str(puzzle_prev or "").upper().replace(" ", "")
    if len(b) != 21:
        return "fail", f"board has {len(b)} letters, want 21: {b!r}"
    if t and b == t:
        return "proceed", "board matches today's puzzle"
    if p and b == p:
        return "defer", "board matches previous-day puzzle"
    return "fail", (f"board {b!r} matches neither today's puzzle "
                    f"{t!r} nor previous-day puzzle {p!r}")


def _betweenle_invalid_showing(page):
    try:
        txt = (page.evaluate("() => document.body.innerText") or "").lower()
    except Exception:
        return False
    return any(s in txt for s in ("not a word", "not valid", "invalid word",
                                  "not in the word list", "unknown word"))


def _betweenle_state(page):
    """Snapshot the board: bounds + typed rows + toasts, for submit verify."""
    try:
        return page.evaluate("""() => {
          const t = (document.body.innerText || '');
          const gm = t.match(/GUESS\s*(\d+)\s*\/\s*(\d+)/i) || [];
          const rows = Number(gm[1] || 0);
          const enter = Array.from(document.querySelectorAll('button'))
            .some(b => (b.innerText || '').trim().toUpperCase() === 'ENTER'
                       && b.offsetParent !== null);
          return {len: t.length, rows: rows, enter: enter,
                  tail: t.slice(-160),
                  invalid: /not a word|not valid|invalid word|not in the word list|unknown word|before|after/i.test(t)};
        }""") or {}
    except Exception:
        return {}


def _click_on_screen_key(page, label):
    """Click a visible on-screen keycap by its text (betweenle's migrated
    build renders keys as divs, so button-only maps and physical keys both
    miss once an ad steals focus)."""
    try:
        box = page.evaluate("""(want) => {
          const els = Array.from(document.querySelectorAll(
            'button, div, span, [role=button]'));
          for (const e of els) {
            if (e.offsetParent === null) continue;
            const t = (e.innerText || '').trim();
            if (t.toLowerCase() !== want.toLowerCase()) continue;
            const r = e.getBoundingClientRect();
            if (r.width < 16 || r.width > 150 || r.height < 16 || r.height > 95)
              continue;
            if (e.children.length > 2) continue;
            return {x: r.x + r.width / 2, y: r.y + r.height / 2};
          }
          return null;
        }""", label)
        if box:
            page.mouse.click(box["x"], box["y"])
            try:
                QP.ev_push(box["x"], box["y"], "click")
            except Exception:
                pass
            page.wait_for_timeout(90)
            return True
    except Exception:
        pass
    return False


def _type_betweenle_via_keys(page, word):
    """Clear leftovers with the backspace keycap, then click letter keycaps;
    falls back to physical typing for any key the DOM map misses."""
    # CANVAS game: the internal letter buffer keeps a rejected word
    # ("Word not in list" leaves garbage which garbles the next guess -
    # the WEIRW stall). The game's own key handler clears on Backspace.
    for _ in range(14):
        try:
            page.keyboard.press("Backspace")
        except Exception:
            break
        page.wait_for_timeout(35)
    clicked = 0
    for ch in str(word).upper():
        if _click_on_screen_key(page, ch):
            clicked += 1
        else:
            page.keyboard.type(ch, delay=120)
    return clicked


def _submit_betweenle(page):
    if _click_on_screen_key(page, "enter"):
        return "keycap-enter"
    page.keyboard.press("Enter")
    return "physical-enter"


def _evict_interceptors(page, gid, pts=((960, 540), (960, 880), (960, 320))):
    """Neuter transparent full-page overlay divs that swallow every click
    (the countryle NEXT-interceptor pattern, now seen on betweenle too:
    physical keys work twice, then a see-through ad layer eats all input).
    Only touches BIG transparent elements - never the canvas or keycaps."""
    try:
        hits = page.evaluate("""(pts) => {
          const out = [];
          for (const p of pts) {
            const el = document.elementFromPoint(p[0], p[1]);
            if (!el) continue;
            const tag = el.tagName.toLowerCase();
            if (tag in {canvas:1, body:1, html:1, input:1, button:1}) continue;
            const r = el.getBoundingClientRect();
            if (r.width < innerWidth * 0.6 || r.height < innerHeight * 0.6)
              continue;
            const cs = getComputedStyle(el);
            const bg = cs.backgroundColor || '';
            const m = bg.match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)(?:,\\s*([0-9.]+))?\\)/);
            const alpha = m ? (m[4] === undefined ? 1 : parseFloat(m[4])) : 1;
            if (alpha > 0.05 && cs.opacity !== '0') continue;
            out.push((el.id || el.className || tag).toString().slice(0, 60));
            el.style.setProperty('pointer-events', 'none', 'important');
          }
          return out;
        }""", [list(p) for p in pts])
        for h in (hits or []):
            _dbg(gid, f"transparent interceptor neutered: {h!r}")
    except Exception:
        pass


def _type_betweenle_word(page, gid, word, used):
    """Type one betweenle guess; on 'not a word' drop it and try the next
    bank word exactly like a human would (never hammer Enter on a dead word).
    Verifies the Enter actually submitted (state changed); otherwise refocuses
    the board and resends once instead of typing into the void."""
    # Ad iframes steal window focus after the first couple of guesses and
    # every later keystroke dies inside them: reclaim focus explicitly.
    try:
        page.evaluate(
            "() => { const a = document.activeElement; "
            "if (a && a !== document.body) a.blur(); " "if (a && a.tagName === 'IFRAME') { try { a.contentWindow.blur(); } " "catch (e) {} } try { window.top.focus(); } catch (e) {} " "window.focus(); }")
        page.mouse.click(960, 320)     # neutral canvas spot above the board
        QP.ev_push(960, 320, "click")
        page.wait_for_timeout(250)
    except Exception:
        pass
    _push_click_at(960, 620)
    try:
        page.bring_to_front()
    except Exception:
        pass
    _evict_interceptors(page, gid)
    # CANVAS game: the internal letter buffer keeps any rejected word
    # ("Word not in list" leaves garbage that garbles the next guess - the
    # WEIRW stall). The game's own key handler clears on Backspace keydowns.
    for _bs in range(14):
        try:
            page.keyboard.press("Backspace")
        except Exception:
            break
        page.wait_for_timeout(30)
    before = _betweenle_state(page)
    # The migrated build draws a DOM on-screen keyboard: click the key
    # elements themselves (focus-proof, human-looking, feeds the cursor
    # track). Physical typing stays as fallback.
    _key_js = """(want) => {
      const els = Array.from(document.querySelectorAll('button, div, span'))
        .filter(e => e.offsetParent !== null && e.children.length === 0 &&
          (e.innerText || '').trim().toUpperCase() === want);
      for (const e of els) {
        const r = e.getBoundingClientRect();
        if (r.width > 18 && r.width < 150 && r.height > 18 && r.y > 480)
          return {x: r.x + r.width / 2, y: r.y + r.height / 2};
      }
      return null;
    }"""
    _typed_keys = True
    for _ch in str(word).upper():
        try:
            _kp = page.evaluate(_key_js, _ch)
        except Exception:
            _kp = None
        if not _kp:
            _typed_keys = False
            break
        page.mouse.click(_kp["x"], _kp["y"])
        QP.ev_push(_kp["x"], _kp["y"], "click")
        page.wait_for_timeout(140)
    if not _typed_keys:
        _type_like_a_person(page, word, base_delay=170)
    page.wait_for_timeout(300)
    # Migrated build draws a DOM on-screen keyboard: click its ENTER button
    # with a real mouse event (physical Enter also works, but the button
    # click is what a human does and it feeds the cursor track).
    try:
        _ep = page.evaluate(_key_js, "ENTER")
        if _ep:
            page.mouse.click(_ep["x"], _ep["y"])
            QP.ev_push(_ep["x"], _ep["y"], "click")
        else:
            page.keyboard.press("Enter")
    except Exception:
        page.keyboard.press("Enter")
    page.wait_for_timeout(2500)
    after = _betweenle_state(page)
    if after.get("rows", 0) <= before.get("rows", 0):
        page.keyboard.press("Enter")
        page.wait_for_timeout(2000)
        after = _betweenle_state(page)
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
    if after.get("len") == before.get("len") and \
            after.get("rows") == before.get("rows"):
        # Enter went nowhere (focus on tooltip, not the board): refocus the
        # board centre and resend Enter once.
        _dbg(gid, f"{word!r} no board change; canvas-centre + ENTER retry")
        try:
            page.mouse.click(960, 620)
            page.wait_for_timeout(400)
            _ep = page.evaluate(_key_js, "ENTER") if False else None
            try:
                _ep = page.evaluate("""(want) => {
                  const els = Array.from(document.querySelectorAll(
                    'button, div, span')).filter(e => e.offsetParent !== null &&
                    e.children.length === 0 &&
                    (e.innerText || '').trim().toUpperCase() === want);
                  for (const e of els) {
                    const r = e.getBoundingClientRect();
                    if (r.width > 18 && r.width < 150 && r.height > 18 &&
                        r.y > 480) return {x: r.x + r.width / 2,
                                           y: r.y + r.height / 2};
                  }
                  return null;
                }""", "ENTER")
            except Exception:
                _ep = None
            if _ep:
                page.mouse.click(_ep["x"], _ep["y"])
            else:
                page.keyboard.press("Enter")
            page.wait_for_timeout(2500)
        except Exception:
            pass
        after2 = _betweenle_state(page)
        if after2.get("len") == before.get("len") and \
                _betweenle_invalid_showing(page):
            _dbg(gid, f"{word!r} still rejected after resend; replacing")
            return _type_betweenle_word(page, gid, word + "", used)
    used.add(word.upper())
    _dbg(gid, f"{word!r} submitted (board {before.get('len')}->{after.get('len')} chars)")
    return True, word


def solve_betweenle(page, ans):
    # DEAD CODE (fix-loop iter 12 note): no caller — SOLVERS routes
    # betweenle to _s_betweenle, which uses _plan_for + _daily_opener.
    # Left in place only so history stays readable; the fixed "about"
    # opener below is NOT the opener any run plays.
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
    # The helper tooltip bubble sits over the board centre and swallows the
    # focus click; dismiss it (X, then Escape) before touching the board.
    for _t in range(3):
        try:
            x = page.query_selector(
                "[class*=tooltip] [class*=close], [class*=help] [class*=close],"
                " [class*=popover] [aria-label=Close], button:has-text('×')")
            if x and x.is_visible():
                x.click(timeout=2000)
                page.wait_for_timeout(600)
        except Exception:
            pass
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
        except Exception:
            pass
    # Verify the daily board actually opened (ENTER key + bounds visible);
    # otherwise click DAILY WORD by text and wait again.
    def _board_on():
        try:
            t = (page.evaluate("() => document.body.innerText") or "")
            return ("ENTER" in t.upper() and "DAILY" not in t.upper()[:500]) or \
                bool(page.query_selector("button:has-text('ENTER')"))
        except Exception:
            return False
    if not _board_on():
        _dbg("betweenle", "board not open; clicking DAILY WORD by text")
        for sel in ("button:has-text('DAILY WORD')", "text=DAILY WORD"):
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    el.click(timeout=3000)
                    page.wait_for_timeout(4000)
                    break
            except Exception:
                continue
        _close_betweenle_help(page)
    _dbg("betweenle", f"board open={_board_on()}")
    # canvas game: no DOM input, so drive the physical keyboard
    page.mouse.click(960, 620)
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
        try:
            _tail = (page.evaluate("() => document.body.innerText") or "")[-400:]
            ok = ("WIN" in _tail.upper()
                  and ans["answer"].lower() in _tail.lower())
        except Exception:
            ok = False
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
            seq, _ = S.plan_colordle(hexv, date_str=A.target_date().isoformat())
            if seq and seq[-1].lower() == str(name).lower():
                guesses = seq
        except Exception as e:
            print(f"[colordle] planner failed, using direct answer: {e}")

    percents = []
    _played_cd = []
    for _gi, g in enumerate(guesses):
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
        # Per-guess line (fix-loop iter 4): colordle was the only game with
        # just one end-of-solve summary line. One timestamped _dbg line per
        # played guess, same pattern as the other games.
        _played_cd.append(g)
        _PLAYED["colordle"] = list(_played_cd)
        _dbg("colordle", f"guess {_gi + 1}/{len(guesses)} {g!r}: "
                          f"score={percents[-1]}")

    body = (page.evaluate("() => document.body.innerText") or "").lower()
    # The site's own win banner - the only trustworthy signal.
    _dbg("colordle", f"per-guess scores={percents} guesses={guesses}")
    if "you guessed the right color" in body:
        return True, (f"win banner 'You guessed the right color' after "
                       f"{len(guesses)} guesses; scores={percents}")
    return False, (f"no win banner after {len(guesses)} guesses "
                   f"(guesses={guesses}, scores={percents})")


def _colorfle_swatches(page):
    """Map Colorfle palette index -> clickable element, by background colour.

    One evaluate call returns every button's colour at once; the old version
    did a getComputedStyle round-trip per button and took forever on camera.
    """
    import re as _re
    sw = {}
    try:
        items = page.evaluate("""() => {
          const all = Array.from(document.querySelectorAll('button'));
          return all.map(e => ({bg: getComputedStyle(e).backgroundColor,
            vis: !!e.offsetParent, i: all.indexOf(e)})); }""") or []
    except Exception:
        items = []
    btns = page.query_selector_all("button") or []
    for it in items:
        try:
            if not it.get("vis"):
                continue
            m = _re.match(r"rgb\((\d+),\s*(\d+),\s*(\d+)\)", it.get("bg") or "")
            if not m:
                continue
            rgb = tuple(map(int, m.groups()))
            for i, hx in enumerate(COLORFLE_HEX):
                if _hex_to_rgb(hx) == rgb and i not in sw \
                        and it["i"] < len(btns):
                    sw[i] = btns[it["i"]]
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
        seq, _cf_steps = S.plan_colorfle(colors)
    except Exception as e:
        print(f"[colorfle] planner failed: {e}")
        seq, _cf_steps = [colors], []

    def _click(el):
        bb = el.bounding_box()
        if bb:
            page.mouse.click(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2)
        else:
            el.evaluate("e => e.click()")

    _played_cf = []
    for _n, combo in enumerate(seq):
        combo = [int(c) for c in combo]
        # First action anchor (fix-loop iter 4): colorfle uses clicks, never
        # typing, so _type_like_a_person never fires and the game had no
        # START->first-typing line / autocrop anchor.
        _record_first_action(getattr(page, "_qp_gid", "colorfle"),
                             "color-pick")
        # Pool chain (fix-loop iter 4): the planner's per-step
        # pool_before/pool_after (6840->...->1) must stay visible in the
        # debug log, not only in result.json steps.
        _pool_txt = ""
        try:
            _st = (_cf_steps or [])[_n] if _n < len(_cf_steps or []) else {}
            if _st.get("pool_before") is not None:
                _pool_txt = (f" pool_before={_st.get('pool_before')} "
                             f"pool_after={_st.get('pool_after')} "
                             f"pattern={_st.get('pattern')!r}")
        except Exception:
            pass
        _dbg(getattr(page, "_qp_gid", "colorfle"),
             f"guess {_n + 1}/{len(seq)} combo={combo}{_pool_txt}")
        for i in combo:
            if i in sw:
                _click(sw[i])
                page.wait_for_timeout(420)
        _click(enter)
        page.wait_for_timeout(3400)
        _played_cf.append(list(combo))
        _PLAYED["colorfle"] = [list(c) for c in _played_cf]
        try:
            _tail = (page.evaluate(
                "() => (document.body.innerText || '').slice(-140)") or "")
            _dbg(getattr(page, "_qp_gid", "colorfle"),
                 f"board after guess {_n + 1}: "
                 f"{_tail.replace(chr(10), '|')[:120]!r}")
        except Exception:
            pass

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
    if _framed_board_ready(page):
        return True
    # The mode menu lives in an OFF-SCREEN sidebar (its tiles measure x = -288),
    # so is_visible() is false and a normal click is impossible. Go to the
    # REQUESTED mode route directly - never the first mode link found (that
    # hopped daily->one-frame->daily on camera).
    from urllib.parse import urlparse as _up
    want = (_up(url).path or "/").rstrip("/") or "/"
    norm = (page.url.rstrip("/")) if isinstance(page.url, str) else ""
    if want != "/" and not norm.endswith(want):
        try:
            page.goto("https://framed.wtf" + want,
                      wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(6000)
        except Exception:
            pass
    return _framed_board_ready(page)


def _framed_poster_gate_up(page):
    """True when the poster reward gate is showing ("WATCH AD" / the
    "watching an ad will unlock" text). Never raises. Used both to keep
    _framed_board_ready honest (a locked poster page carries a POSTER #N
    header, so header-only readiness is a false positive) and to report
    the correct failure evidence (unlock never completed, not "menu
    never opened").
    """
    try:
        txt = (page.evaluate(
            "() => document.body.innerText") or "").lower()
    except Exception:
        return False
    return ("watch ad" in txt
            or "watching an ad will unlock" in txt
            or "support us by watching" in txt)


def _framed_board_ready(page):
    """True once the visible guess box is the game board, not the menu search.

    The menu's box reads "Search for movie or submit to skip"; the playing
    board's placeholder is empty/different. That difference is the only reliable
    way to tell the two apart.
    """
    try:
        # Fix-loop iter 6: the locked poster page renders "POSTER #450"
        # AND a menu search box, so header-only readiness called a
        # locked board ready. The reward gate up means NOT ready.
        if _framed_poster_gate_up(page):
            return False
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


def _framed_reward_ad_state(page):
    """Snapshot the rewarded-ad UI state (iter 7): ad containers /
    overlays, close/skip/claim controls in the main frame AND child
    frames, and the googletag rewarded-slot state if exposed. Pure
    probe (no clicks) — the unlock poll uses it to see WHAT the ad is
    doing instead of polling board_ready blindly for 45s.

    Fix-loop iter 9: also probe the AD-IFRAME LOAD itself — every
    iframe (src + visibility, ad-like = googleads / doubleclick /
    ima / ads), every <video> element (the rewarded creative is a
    video), the IMA SDK presence (window.google.ima), and every frame
    URL. ad_loaded = an ad-like iframe OR a video OR the IMA SDK is
    present. In rerun #8 the poll showed overlays=[] for 45s, but the
    probe never said whether an ad iframe/video had EVER loaded — the
    distinction between "ad never filled" (defer honestly) and "ad
    loaded but the gate never dropped" (real failure).

    Fix-loop iter 18: also record whether the rewarded flow STARTED
    (reward_click_registered = the page URL gained #goog_rewarded
    and/or a googletag slot with "reward" in its path exists).
    The unlock timeout used to conflate two different failures:
    (a) the WATCH AD click never registered with the ad SDK
    (bot-path failure), and (b) the click registered but no
    creative ever loaded (site / ad-server no-fill — the honest
    defer case). Only (b) may defer; (a) is a failure. The
    distinction is what proves the iter-17 poster defer is a site
    gate, not a bot-path regression: the identical tile-click +
    unlock path unlocked poster in ~0s in every earlier run, and
    in the iter-17 run the click DID register (URL #goog_rewarded,
    videoreward slot present) while ad_loaded stayed False for
    the full 45s."""
    out = {"overlays": [], "close": [], "googletag": None, "frames": 0,
           "ad_iframes": [], "videos": [], "ima": False, "frame_urls": [],
           "ad_loaded": False, "reward_click_registered": False}
    try:
        out["frames"] = len(page.frames or [])
    except Exception:
        pass
    try:
        out["frame_urls"] = [str(getattr(_fr, "url", "") or "")[:160]
                             for _fr in (page.frames or [])]
    except Exception:
        pass
    try:
        out.update(page.evaluate("""() => {
          const vis = e => e && e.offsetParent !== null;
          const ov = Array.from(document.querySelectorAll(
            'iframe, [id*=reward i], [class*=reward i], ' +
            '[id*=goog_rewarded i], [class*=goog_rewarded i], ' +
            '[id*=ad-container i], [class*=ad-container i]'))
            .filter(vis).map(e => ({tag: e.tagName,
              id: (e.id || '').slice(0, 60),
              cls: String(e.className || '').slice(0, 80),
              src: (e.getAttribute('src') || '').slice(0, 100),
              w: Math.round(e.getBoundingClientRect().width),
              h: Math.round(e.getBoundingClientRect().height)}));
          const close = Array.from(document.querySelectorAll(
            'button, a, [role=button]')).filter(vis)
            .map(e => (e.innerText || e.getAttribute('aria-label') || ''))
            .map(t => t.trim().toLowerCase())
            .filter(t => /close|skip|claim|reward|continue|done|got it/.test(t));
          let gt = null;
          try {
            const g = window.googletag;
            if (g && g.pubads) {
              const slots = (g.pubads().getSlots() || []).map(s => ({
                path: (s.getAdUnitPath && s.getAdUnitPath()) || '',
                sizes: String((s.getSizes && s.getSizes()) || ''),
              }));
              gt = {slots: slots};
            }
          } catch (e) {}
          // Iter 9: ad-iframe load probe — every iframe (not just
          // visible ones), every video, IMA SDK presence.
          const adRe = /googleads|doubleclick|ima|\\/ads\\/|adservice|goog_rewarded/i;
          const adIframes = Array.from(document.querySelectorAll('iframe'))
            .map(f => ({src: (f.getAttribute('src') || '').slice(0, 160),
                        vis: vis(f),
                        adLike: adRe.test(f.getAttribute('src') || '')}));
          const videos = Array.from(document.querySelectorAll('video'))
            .map(v => ({src: ((v.currentSrc || v.getAttribute('src')) || '')
              .slice(0, 160),
              dur: (typeof v.duration === 'number' && isFinite(v.duration))
                ? Math.round(v.duration) : -1,
              paused: !!v.paused, vis: vis(v)}));
          let ima = false;
          try { ima = !!(window.google && window.google.ima); } catch (e) {}
          const adLoaded = adIframes.some(f => f.adLike) ||
            videos.length > 0 || ima;
          return {overlays: ov.slice(0, 8),
                  close: Array.from(new Set(close)).slice(0, 8),
                  googletag: gt,
                  ad_iframes: adIframes.slice(0, 12),
                  videos: videos.slice(0, 6),
                  ima: ima, ad_loaded: !!adLoaded};
        }""") or {})
    except Exception:
        pass
    # Iter-18: did the rewarded flow START? The WATCH AD click
    # registers with the ad SDK when the URL gains #goog_rewarded
    # and/or a googletag slot with "reward" in its path exists
    # (the iter-17 run showed BOTH while no creative ever loaded).
    # A click that merely found a button proves nothing about the
    # SDK flow — this is the signal that separates a site/ad
    # no-fill (honest defer) from a bot-path failure.
    try:
        _url = str(getattr(page, "url", "") or "")
        _slots = (((out.get("googletag") or {}).get("slots")) or [])
        _reward_slot = any(
            "reward" in str((s or {}).get("path") or "").lower()
            for s in _slots)
        out["reward_click_registered"] = (
            "goog_rewarded" in _url) or bool(_reward_slot)
    except Exception:
        pass
    return out


def _framed_try_unlock_poster(page):
    """Poster mode hides its board behind a reward ad. Try to unlock it.

    Poster is the one Framed mode that is not playable without watching a short
    ad. Click the unlock button (WATCH AD / UNLOCK wording) and then POLL for
    the board: the rewarded ad takes seconds to complete, and the old code
    checked once immediately after the click, read the still-locked menu,
    and reported failure. Returns True iff the playable board is showing.

    Fix-loop iter 6: every step is logged (the rerun-#5 unlock ran in
    total silence, so the exact sub-cause was unprovable), and the click
    is a TRUSTED Playwright element click first — a JS .click() dispatches
    an untrusted event, which a rewarded-ad SDK may ignore, so the old
    JS-only click may never have started a real rewarded-ad session.

    Fix-loop iter 18 (VERDICT — site / ad-server change, NOT a path
    regression): the tile-click path and this unlock function are
    byte-identical to the code that unlocked poster in ~0s in every
    earlier run (same sequence every time: tile element-click times
    out at 5s -> tile JS-click True -> board NOT ready -> unlock
    start gate_up=True -> WATCH AD element-click -> board ready
    ~0s, gate_up=False). In the iter-17 run the same sequence ran,
    the WATCH AD click registered (URL gained #goog_rewarded, a
    videoreward googletag slot existed), but NO creative ever loaded
    (ad_loaded=False for the full 45s: no ad-like iframe, no video,
    no IMA SDK). What changed is upstream: the site / ad server
    stopped granting on click / stopped filling the rewarded slot
    in headless CI. There is NO legitimate non-ad path around it —
    bypassing the client-side gate or faking the reward callback
    would be faking an ad view, so the honest outcome stays DEFER
    with no video for a 3/4 game. The timeout below now records
    click_registered explicitly so the next run's artifact proves
    which side failed instead of leaving it to inference.
    """
    _gate_before = _framed_poster_gate_up(page)
    _dbg("framed", f"unlock: start gate_up={_gate_before} url={page.url[:70]}")
    # Trusted element click first; JS click only as a fallback.
    _clicked, _via = False, None
    try:
        _btn = None
        for _sel in ("button:has-text('WATCH AD')",
                     "button:has-text('UNLOCK')",
                     "a:has-text('WATCH AD')",
                     "[role=button]:has-text('WATCH AD')"):
            try:
                _cand = page.query_selector(_sel)
            except Exception:
                _cand = None
            if _cand is not None:
                try:
                    if _cand.is_visible():
                        _btn = _cand
                        break
                except Exception:
                    pass
        if _btn is not None:
            _btn.click(timeout=5000)
            _clicked, _via = True, "element-click"
            _dbg("framed", "unlock: WATCH AD button clicked via element-click")
    except Exception as _e:
        _dbg("framed", f"unlock: element-click failed: {str(_e)[:100]}")
    if not _clicked:
        try:
            _clicked = bool(page.evaluate("""() => {
              const vis = e => e && e.offsetParent !== null;
              const els = Array.from(document.querySelectorAll(
                'button, a, [role=button]')).filter(vis);
              const norm = s => (s||'').toUpperCase().replace(/\\s+/g,' ').trim();
              const hit = els.find(e => {
                const t = norm(e.innerText);
                return (t.includes('WATCH') && t.includes('AD')) ||
                       t.includes('UNLOCK') || t === 'WATCH AD TO PLAY';
              });
              if (!hit) return false;
              hit.click(); return true;
            }"""))
            _via = "js-click" if _clicked else None
            _dbg("framed", f"unlock: WATCH AD JS-click fallback clicked={_clicked}")
        except Exception as _e:
            _dbg("framed", f"unlock: JS-click fallback failed: {str(_e)[:100]}")
            return False
    if not _clicked:
        _dbg("framed", "unlock: no WATCH AD / UNLOCK button found on page")
        return False
    for _i in range(90):  # up to ~45 s for the rewarded ad to complete
        page.wait_for_timeout(500)
        if _framed_board_ready(page):
            _dbg("framed", f"unlock: board ready after ~{_i * 0.5:.0f}s "
                            f"(via={_via}); gate_up={_framed_poster_gate_up(page)}")
            return True
        # Iter 7: the old poll only watched board_ready, so a stuck /
        # never-filled rewarded ad ran out the clock in silence. Every
        # ~5s, inspect the ad UI: if the ad offers a close/skip/claim
        # control (no-fill close, countdown finished), click it the
        # way a real viewer would, then keep polling for the board.
        # The board itself is still the only unlock proof.
        if _i % 10 == 9:
            try:
                _ad = _framed_reward_ad_state(page)
            except Exception:
                _ad = {}
            _dbg("framed", f"unlock: ad-state @{(_i + 1) * 0.5:.0f}s "
                            f"frames={_ad.get('frames')} "
                            f"overlays={str(_ad.get('overlays'))[:300]} "
                            f"close={_ad.get('close')} "
                            f"googletag={str(_ad.get('googletag'))[:200]}")
            _close_hit = False
            for _fr in (page.frames or []):
                try:
                    _hit = _fr.evaluate("""() => {
                      const vis = e => e && e.offsetParent !== null;
                      const els = Array.from(document.querySelectorAll(
                        'button, a, [role=button]')).filter(vis);
                      const hit = els.find(e => /^(close|skip|skip ad|claim|continue|done|got it|x)$/i
                        .test((e.innerText || e.getAttribute('aria-label') || '').trim()));
                      if (!hit || hit.disabled) return false;
                      hit.click(); return true;
                    }""")
                except Exception:
                    _hit = False
                if _hit:
                    _close_hit = True
                    _dbg("framed", "unlock: ad close/skip control "
                                    "clicked; re-polling for board")
                    break
            if _close_hit:
                page.wait_for_timeout(1500)
                if _framed_board_ready(page):
                    _dbg("framed", "unlock: board ready after ad close")
                    return True
        if _i % 20 == 19:
            try:
                _tail = (page.evaluate(
                    "() => (document.body.innerText || '').slice(-160)")
                    or "").replace("\n", " | ")
            except Exception:
                _tail = "?"
            _dbg("framed", f"unlock: poll {_i + 1}/90 (~{(_i + 1) * 0.5:.0f}s) "
                            f"board_ready=False gate_up={_framed_poster_gate_up(page)} "
                            f"url={page.url[:60]} tail={_tail[:140]!r}")
    try:
        _ad_final = _framed_reward_ad_state(page)
    except Exception:
        _ad_final = {}
    _click_registered = bool(_ad_final.get("reward_click_registered"))
    _dbg("framed", "unlock: TIMEOUT after ~45s; board never ready; "
                    f"gate_up={_framed_poster_gate_up(page)} url={page.url[:70]} "
                    f"click_registered={_click_registered} "
                    f"ad_state={str(_ad_final)[:400]}")
    if not _ad_final.get("ad_loaded"):
        if _click_registered:
            # Iter 9 + 18: no ad iframe / video / IMA SDK ever
            # appeared in 45s of polling, but the WATCH AD click DID
            # register with the ad SDK — a site / ad-server no-fill,
            # not a bot-path failure. The caller records this as
            # DEFERRED (environment-blocked), not a solve failure.
            _dbg("framed", "unlock: ad never loaded in 45s (no ad-like "
                            "iframe, no video, no IMA SDK) but the "
                            "WATCH AD click registered — site/ad "
                            "no-fill; poster deferred, not failed")
        else:
            # Iter-18: the rewarded flow never started — the click
            # did not register with the ad SDK. That is a bot-path
            # failure, NOT a site no-fill, and must not be deferred
            # as one (the caller only defers on click_registered).
            _dbg("framed", "unlock: rewarded flow never started "
                            "(click did not register) — bot-path "
                            "failure, not a site no-fill")
    return False


def _framed_open_board_with_unlock(page, mid):
    """Open a Framed mode board from the mode menu + reward unlock.

    Returns True iff the playable board is showing afterwards.
    Steps: (1) JS-click the mode's menu tile (label POSTER / ONE FRAME /
    TITLESHOT / CLASSIC) and poll for the board — the old path never
    clicked the tile, so a mode whose route serves the menu stayed on
    the menu ("mode menu never opened into a game board"); (2) run the
    reward unlock (which polls for the board itself).
    """
    label = {"classic": "CLASSIC", "one-frame": "ONE FRAME",
             "titleshot": "TITLESHOT", "poster": "POSTER"}.get(mid, "")
    _dbg("framed", f"open-board: mode={mid} label={label!r} url={page.url[:60]}")
    # Trusted element click first (same untrusted-event reason as the
    # unlock click); JS click only as a fallback.
    clicked, _via = False, None
    try:
        _tile = page.query_selector(
            f"button:has-text('{label}'), a:has-text('{label}'), "
            f"[role=menuitem]:has-text('{label}')")
        if _tile is not None and _tile.is_visible():
            _tile.click(timeout=5000)
            clicked, _via = True, "element-click"
            _dbg("framed", f"open-board: tile {label!r} clicked via element-click")
    except Exception as _e:
        _dbg("framed", f"open-board: tile element-click failed: {str(_e)[:100]}")
    if not clicked:
        try:
            clicked = bool(page.evaluate("""(lbl) => {
              const vis = e => e && e.offsetParent !== null;
              const norm = s => (s||'').toUpperCase().replace(/\\s+/g,' ').trim();
              const els = Array.from(document.querySelectorAll(
                'button, a, [role=button], [role=menuitem]'));
              const hit = els.find(e => norm(e.innerText) === norm(lbl)) ||
                els.find(e => norm(e.innerText).includes(norm(lbl)));
              if (!hit) return false;
              hit.click(); return true;
            }""", label))
            _via = "js-click" if clicked else None
            _dbg("framed", f"open-board: tile JS-click fallback clicked={clicked}")
        except Exception as _e:
            clicked = False
            _dbg("framed", f"open-board: tile JS-click failed: {str(_e)[:100]}")
    if clicked:
        for _ in range(20):
            page.wait_for_timeout(500)
            if _framed_board_ready(page):
                _dbg("framed", f"open-board: {mid} board ready after tile click "
                                f"(via={_via})")
                return True
        _dbg("framed", f"open-board: {mid} board NOT ready after tile click "
                        f"(via={_via}); trying reward unlock")
    else:
        _dbg("framed", f"open-board: {mid} tile {label!r} not found/clicked")
    if _framed_try_unlock_poster(page):
        return True
    _ready = _framed_board_ready(page)
    _dbg("framed", f"open-board: {mid} final board_ready={_ready} "
                    f"gate_up={_framed_poster_gate_up(page)}")
    return _ready


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
    label = {"classic": "CLASSIC", "one-frame": "ONE FRAME",
             "titleshot": "TITLESHOT", "poster": "POSTER"}.get(mode, "CLASSIC")
    shown = None
    for _try in range(5):          # header number renders async after board
        try:
            shown = page.evaluate(
                """(lbl) => {
                  const t = (document.body.innerText || '').toUpperCase();
                  const m = t.match(new RegExp(lbl + '\\\\s*#\\\\s*(\\\\d+)'));
                  return m ? parseInt(m[1], 10) : null;
                }""", label)
        except Exception:
            shown = None
        if shown:
            break
        page.wait_for_timeout(1500)
    if not shown:
        try:  # the root menu lists every mode's header - read it there
            page.goto("https://framed.wtf", wait_until="domcontentloaded",
                      timeout=25000)
            page.wait_for_timeout(4000)
            shown = page.evaluate(
                "(lbl) => { const t = (document.body.innerText || '')"
                ".toUpperCase(); const m = t.match(new RegExp(lbl + "
                "'\\\\s*#\\\\s*(\\\\d+)')); return m ? parseInt(m[1], 10) : null; }",
                label)
            page.goto(A.FRAMED_URLS[mode], wait_until="domcontentloaded",
                      timeout=25000)
            page.wait_for_timeout(3000)
        except Exception as _me:
            print(f"[framed] {mode}: menu number read failed: "
                  f"{str(_me)[:80]}")
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
    # Last resort: ask the site's own source for the board's own number
    # directly. Titleshot/poster: the CORE API (asset.framed_title ||
    # asset.title) — the titles top-ten API counts guesses, not correct
    # answers, so its last-frame leader is the WRONG answer when the
    # puzzle is a sequel/sibling (titleshot #501: leader "Despicable Me",
    # real answer "Despicable Me 2"). Classic/one-frame keep the titles
    # API (their core challenge paths 404).
    if mode in ("titleshot", "poster"):
        try:
            _core = A.framed_core(mode, shown)
        except Exception:
            _core = None
        if _core:
            print(f"[framed] {mode}: direct core API for #{shown} "
                  f"-> {_core!r}")
            return _core
        print(f"[framed] {mode}: core API for #{shown} gave nothing; "
              f"falling back to titles API")
    try:
        import requests as _rq
        # Explicit API mapping: the titles API's gameType for the
        # Classic mode is "daily" (answers.FRAMED_MODES keeps that as
        # value[0]); the internal id is "classic" everywhere else.
        _gt = A.FRAMED_MODES[mode][0]
        _r = _rq.get(f"https://titles.framed.wtf/v1/titles/top-ten/{shown}",
                     params={"gameType": _gt}, timeout=20,
                     headers={"User-Agent": "WordSolverX Video"})
        if _r.ok:
            _fr = (_r.json().get("frames") or [])
            if _fr:
                _raw = _fr[-1].get("items")
                _it = json.loads(_raw) if isinstance(_raw, str) else (_raw or [])
                if _it and _it[0].get("title"):
                    print(f"[framed] {mode}: direct titles API for #{shown} "
                          f"-> {_it[0]['title']!r}")
                    return _it[0]["title"]
    except Exception as e:
        print(f"[framed] {mode}: direct titles fetch failed: {str(e)[:90]}")
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
        # Recording-relative MODE-SWITCH time: when the bot starts
        # navigating to this mode. This is the segment anchor
        # (t_switch, iter 13 C2) — it is NOT overwritten below.
        # `mode_start` (the card-anchored, board-ready time) is set
        # separately after the board is ready.
        mode_switch = _time.time() - _REC_T0.get("framed", started)
        mode_start = mode_switch
        _mode_retry = 0
        if not ans:
            per_mode.append({"mode": mid, "solved": False, "why": "no answer",
                             "t_start": mode_start, "t_switch": mode_switch})
            continue
        try:
            ready = _framed_open_mode(page, murl)
        except Exception as e:
            per_mode.append({"mode": mid, "answer": ans, "solved": False,
                             "evidence": f"nav failed: {str(e)[:90]}",
                             "t_start": mode_start, "t_switch": mode_switch})
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
            # Poster (rewarded) and any mode whose menu never opened into
            # a board: click the mode's menu tile, then run the reward
            # unlock with a poll for the board (the rewarded ad takes
            # seconds to complete; checking once misses it).
            if _framed_open_board_with_unlock(page, mid):
                try:
                    ready = _framed_board_ready(page)
                except Exception:
                    ready = False
        else:
            ready = True
        if not ready:
            # Fix-loop iter 6: the old evidence string blamed the menu
            # even when the menu HAD opened (POSTER #450 rendered) and
            # the failure was the reward unlock. Name the unlock when
            # the poster page rendered but the gate stayed up; reserve
            # "menu never opened" for when the menu truly never opened.
            try:
                _on_poster = ("/poster" in (page.url or "") or "POSTER" in
                              (page.evaluate(
                                  "() => document.body.innerText") or "").upper())
            except Exception:
                _on_poster = "/poster" in (page.url or "")
            if mid == "poster" and (_on_poster
                                    or _framed_poster_gate_up(page)):
                # Iter 7: name the exact gate + the rewarded-ad state
                # the unlock poll observed (ad containers / close
                # controls / googletag slots), so a still-blocked
                # poster is blocked-with-evidence, not a vague fail.
                try:
                    _ad_ev = _framed_reward_ad_state(page)
                except Exception:
                    _ad_ev = {}
                if (not _ad_ev.get("ad_loaded")
                        and _ad_ev.get("reward_click_registered")):
                    # Iter 9: DEFER the poster mode honestly. The
                    # rewarded ad never loaded in headless CI (no
                    # ad-like iframe, no video, no IMA SDK in 45s of
                    # polling — reruns #6 and #8 both), so the board
                    # was never playable through no fault of the solve.
                    # solved stays False, no final is assembled (the
                    # assembly gate below is unchanged), and the
                    # summary says DEFERRED instead of FAIL so the next
                    # loop knows this is an environment block, not a
                    # solve failure. Never claim the mode as played.
                    # Iter 18: the defer is reserved for the PROVEN
                    # site/ad no-fill — the WATCH AD click registered
                    # with the ad SDK (reward_click_registered) but
                    # no creative ever loaded. A click that never
                    # registered falls through to the failure branch
                    # below instead of being deferred as a site gate.
                    _ev = ("poster DEFERRED: rewarded ad never loads "
                           "in headless CI (WATCH AD click registered "
                           "with the ad SDK but no creative ever "
                           "loaded — site/ad no-fill; "
                           f"reward_click_registered="
                           f"{_ad_ev.get('reward_click_registered')}, "
                           f"ad_loaded={_ad_ev.get('ad_loaded')}, "
                           f"{page.url[:60]}; ad_state="
                           f"{str(_ad_ev)[:300]})")
                    per_mode.append({"mode": mid, "answer": ans,
                                     "solved": False, "deferred": True,
                                     "evidence": _ev,
                                     "t_start": mode_start,
                                     "t_switch": mode_switch})
                    print(f"[framed] {mname}: board never became "
                          "playable (poster DEFERRED: rewarded ad "
                          "never loads in CI)")
                    continue
                _ev = ("poster reward unlock never completed "
                       "(WATCH AD gate still up); Google rewarded-ad "
                       f"flow at {page.url[:60]}; ad_state="
                       f"{str(_ad_ev)[:300]}")
            else:
                _ev = "mode menu never opened into a game board"
            per_mode.append({"mode": mid, "answer": ans, "solved": False,
                             "evidence": _ev,
                             "t_start": mode_start,
                             "t_switch": mode_switch})
            print(f"[framed] {mname}: board never became playable")
            continue
        # CARD-ANCHORED t_start (fix-loop iter 11): the assembly
        # inserts the mode's NOW PLAYING card at t_start, so t_start
        # must be the moment play actually begins — AFTER the board is
        # ready and the ghost-frame guard below, just before the first
        # guess — NOT the loop-top time (the old value was a nav/menu
        # timestamp, so cards landed after play / on win screens).
        # Recording-relative: the recording starts at page creation
        # (_REC_T0), while solve_framed_modes starts after goto+6s.
        mode_start = _time.time() - _REC_T0.get(
            "framed", started)
        # C4 ghost-frame guard: the Classic board briefly shows an
        # off-site page ("GHOST TOWNS OF AMERICA") inside the
        # movie-frame area while the solver is already typing. Do not
        # type the first guess while that text is on the page; wait
        # for it to clear (bounded) and log the frame-area evidence.
        # Root cause still unproven from frames (site ad behaviour vs
        # solver timing) — this guard + log is the honest partial fix.
        for _gw in range(20):
            try:
                _gtxt = (page.evaluate(
                    "() => document.body.innerText") or "")
            except Exception:
                _gtxt = ""
            if "ghost towns" not in _gtxt.lower():
                break
            _dbg("framed", f"{mid}: ghost-frame text still showing "
                           f"(wait {_gw + 1}/20); holding first guess")
            page.wait_for_timeout(500)
        else:
            _dbg("framed", f"{mid}: ghost-frame text never cleared; "
                           f"proceeding (logged for diagnosis)")
        # The board is live: agree with the day the site is actually serving.
        ans = _framed_live_answer(page, mid, ans)
        # Framed reveals nothing - no letters, no similarity score - so there is
        # genuinely nothing to constrain-propagate. The honest play is to spend
        # the site's REAL budget on plausible wrong titles and only then commit
        # the answer. This used to hard-code exactly two probes ("Titanic",
        # "Avatar"), which read as a two-try guess rather than a search.
        guesses, _steps = _framed_probe_path(ans, max_guesses=2)
        ok, ev = False, ""
        n_sub = 0  # submitted guesses this mode (probes + answers)
        for n, t in enumerate(guesses):
            last = (n == len(guesses) - 1)
            pok, pev = _framed_submit(page, t)
            if pok:
                n_sub += 1
            if not pok and "ad-gated" in str(pev):
                _dbg("framed", f"{mid}: input ad-gated; trying reward unlock")
                try:
                    if _framed_try_unlock_poster(page):
                        pok, pev = _framed_submit(page, t)
                except Exception as _ue:
                    _dbg("framed", f"{mid}: unlock failed: {str(_ue)[:70]}")
            tag = "answer" if last else "probe"
            print(f"[framed] {mid}: {tag} {t!r} -> {pev}")
            # Per-guess lines must ALSO reach the artifact debug log
            # (stdout-only lines never land in the artifact).
            _dbg("framed", f"guess {n + 1}/{len(guesses)} {mid} {tag} "
                           f"{t!r} -> {pev}")
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
                body = _visible_page_text(page).lower()
                if "guesses remaining" in body and "share" in body:
                    ok, ev = True, "post-win screen with SHARE + guesses remaining"
            except Exception:
                pass
        print(f"[framed] {mname}: solved={ok} evidence={ev}")
        if not ok and _mode_retry < 1:
            _mode_retry += 1
            _dbg("framed", f"{mid}: not won; retrying mode once "
                           f"(clean reload + full replay)")
            try:
                page.reload(wait_until="domcontentloaded")
                page.wait_for_timeout(4000)
                page.goto(murl, wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(5000)
                if _framed_board_ready(page):
                    _retry_gs = _framed_probe_path(ans, max_guesses=2)[0]
                    for _n2, _t2 in enumerate(_retry_gs):
                        _pok2, _pev2 = _framed_submit(page, _t2)
                        if _pok2:
                            n_sub += 1
                        _dbg("framed", f"retry guess {_n2 + 1}/{len(_retry_gs)} "
                                      f"{mid} {_t2!r} -> {_pev2}")
                    page.wait_for_timeout(6000)
                    ok, ev = _strict_win(page, ans)
                    if not ok:
                        try:
                            _b2 = _visible_page_text(page).lower()
                            if "guesses remaining" in _b2 and "share" in _b2:
                                ok, ev = True, "retry: post-win SHARE screen"
                        except Exception:
                            pass
                    _dbg("framed", f"{mid}: retry solved={ok} ({ev})")
            except Exception as _re:
                _dbg("framed", f"{mid}: retry failed: {str(_re)[:80]}")
        per_mode.append({"mode": mid, "answer": ans, "solved": bool(ok),
                         "evidence": ev, "t_start": mode_start,
                         "t_switch": mode_switch, "guesses": n_sub})
        print(f"[framed] {mname}: FINAL solved={ok}")
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


# JS for _visible_page_text: the page's RENDERED text only. Top-down
# walk that never descends into script/style/template/noscript (hidden
# templates and inline scripts are not visible text) and skips any
# subtree whose own computed style is display:none, visibility:hidden /
# collapse, or whose element carries the hidden attribute. Input values
# are not text nodes, so a typed answer sitting in the input can never
# supply a win phrase.
_VISIBLE_TEXT_JS = """/* _visible_page_text */
() => {
  const skip = new Set(["SCRIPT", "STYLE", "TEMPLATE", "NOSCRIPT",
                        "HEAD", "TITLE", "META", "LINK", "OPTION"]);
  const out = [];
  const body = document.body;
  if (!body) return "";
  const stack = [body];
  while (stack.length) {
    const node = stack.pop();
    if (!node) continue;
    if (node.nodeType === 3) {
      const t = (node.nodeValue || "").trim();
      if (t) out.push(t);
      continue;
    }
    if (node.nodeType !== 1) continue;
    if (skip.has(String(node.tagName || "").toUpperCase())) continue;
    if (node.hasAttribute && node.hasAttribute("hidden")) continue;
    const cs = getComputedStyle(node);
    if (cs.display === "none" || cs.visibility === "hidden" ||
        cs.visibility === "collapse") continue;
    const kids = node.childNodes;
    for (let i = kids.length - 1; i >= 0; i--) stack.push(kids[i]);
  }
  return out.join(" ");
}"""


def _visible_page_text(page):
    """Rendered page text only (see _VISIBLE_TEXT_JS). Returns "" on
    any probe failure — a win claim must be provable from visible text,
    never from a fallback to raw HTML. Never raises."""
    try:
        txt = page.evaluate(_VISIBLE_TEXT_JS)
    except Exception:
        return ""
    return txt if isinstance(txt, str) else ""


def _strict_win(page, answer=None):
    """True only on an explicit VISIBLE on-page win signal.

    The win phrase must appear in the page's RENDERED text
    (_visible_page_text). Raw page HTML is never consulted: hidden
    templates / inline scripts can contain "you won" on a board that
    was never won (batterup rerun #9: hidden "you won" + empty guess
    rows reported solved=True). Flutter games (Canuckle, Betweenle)
    paint text to a canvas, so the DOM is empty and the win banner is
    checked through the semantics tree instead.
    """
    visible = _visible_page_text(page).lower()
    for s in WIN_PHRASES:
        if s in visible:
            return True, f"win signal {s!r}"
    # post-win statistics sheets (Searchle and friends)
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
    if visible:
        print(f"[win-probe] visible text: {visible[:400]!r}")
    return False, "NO win signal on page"


def _play_guesses(page, guesses, after_each=None, answer=None):
    """Type each guess in turn; the final verification is strict.

    Pacing matters as much as correctness here. A fixed 2600ms between every
    row is the giveaway that nobody is reading the board, so the pause after a
    guess is randomised and the cursor is allowed to drift before the next one.
    """
    for i, g in enumerate(guesses):
        _dismiss_login_wall(page)
        _dbg(getattr(page, "_qp_gid", "wordle-like"),
             f"guess {i + 1}/{len(guesses)}: {g!r}")
        # Pause BEFORE typing too: a person glances back at the previous row.
        _idle_drift(page)
        _kb(page, str(g), delay=120)
        _settle(page, base=2600)
        try:
            _tail = (page.evaluate(
                "() => (document.body.innerText || '').slice(-140)") or "")
            _dbg(getattr(page, "_qp_gid", "wordle-like"),
                 f"board after guess {i + 1}: "
                 f"{_tail.replace(chr(10), '|')[:120]!r}")
        except Exception:
            pass
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


def solve_wordle_like(page, ans, guesses=None, opener=None):
    """Wordle-style games (canuckle, phoodle): real deduction sequence."""
    _close_modals(page)
    if not guesses:
        g5, pool = S.words_generic5()
        opener = opener or _daily_opener(["CRANE", "SLATE", "ADIEU", "TRACE",
                                          "ROAST", "AUDIO"])
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
        return True, None
    import re as _re
    low = txt.lower()
    w = str(word).lower()
    if _re.search(r"not a valid word|isn't a valid|invalid word|"
                  r"not in the dictionary|unknown word", low):
        return False, None
    # Root-cause fix (iter 3): the old code extracted a score ONLY via a
    # `<word> ... <number>%` regex on body text, which never matches either
    # site's score rendering — so scores were always []. Read the score
    # from the sites' real score DOM (guess list/table rows) instead.
    # The caller already settled after Enter before calling this.
    try:
        dom_score, _probe = _semantic_dom_scores(page, word)
    except Exception:
        dom_score, _probe = None, {}
    if dom_score is not None:
        return True, dom_score
    m = _re.search(_re.escape(w) + r"[^\n]{0,40}?([\d.]+)\s*%", low)
    if m:
        return True, m.group(1)
    if w in low and before_txt and before_txt.lower().count(w) == 0:
        # FIX-LOOP: accepted but score not readable -> None, not "?", so evidence never shows ['?'] placeholders.
        return True, None
    return False, None


def _semantic_closeness(gid, score):
    """Closeness bucket for one accepted guess: "high" (next word
    should share stem/letters with this guess), "low" (next word
    should be distant/broad), or None (score unusable — keep plan
    order). Semantle scores are similarity (higher = closer);
    Contexto scores are ranks (lower = closer)."""
    try:
        v = float(str(score).strip().rstrip("%"))
    except Exception:
        return None
    if gid == "contexto":
        if v <= 500:
            return "high"
        if v > 5000:
            return "low"
        return None
    if v > 25:
        return "high"
    if v < 5:
        return "low"
    return None


def _semantic_feedback_reorder(spare, guess, gid, score):
    """Reorder the spare pool by the last accepted guess's feedback:
    high closeness -> stem/letter-sharing words first; low closeness
    -> distant broad words first. Pure list operation (unit-tested)."""
    close = _semantic_closeness(gid, score)
    if close is None or not spare:
        return list(spare)
    g = str(guess or "").lower()

    def _shares(w):
        w = str(w).lower()
        if len(w) >= 3 and len(g) >= 3 and (
                w[:3] == g[:3] or w[-3:] == g[-3:]):
            return True
        for n in range(4, min(len(w), len(g)) + 1):
            if any(w[i:i + n] in g for i in range(len(w) - n + 1)):
                return True
        return False

    def _dist(w):
        return len(set(str(w).lower()) & set(g))

    if close == "high":
        return sorted(spare, key=lambda w: (not _shares(w), _dist(w)))
    return sorted(spare, key=lambda w: (_shares(w), -_dist(w)))


# HARD CAP (fix-loop iter 11): total TYPED attempts <= 5 including the
# answer (<= 4 probes + answer). Replacements count against the cap;
# when the cap is hit without a win, the solver returns False and
# never types a 6th word.
_SEMANTIC_MAX_TYPED = 5
_SEMANTIC_MAX_PROBES = _SEMANTIC_MAX_TYPED - 1


def _semantic_dup(word, used, rejected):
    """True if `word` is already in played[] ∪ rejected[] (case-insensitive).

    Fix-loop iter 12: the iter-11 guard compared the rejected set only
    exact-case, so a rejected "CRY" did not block a later "cry". Both
    sets are compared lower-cased here, and every pick path (queue
    guard, replacement pick, feedback swap) uses this one helper.
    """
    w = str(word).lower()
    return (w in {str(u).lower() for u in used}
            or w in {str(r).lower() for r in rejected})


def _searchle_opener(bank, n, seed, answer):
    """Date-seeded Searchle opener that the site will actually accept.

    Fix-loop iter 12 (carry-forward item 1): the iter-11 opener list
    (CRANE/SLATE/TRACE/...) is NOT in Searchle's own guess bank for ANY
    length (verified: zero of the 4/5/6-letter candidates are in
    words_searchle()), so plan_sequence silently dropped the opener and
    played an entropy pick (STARE) instead. The opener is now chosen
    FROM the bank: a generic candidate when one is legal, else a
    date-seeded high-information bank word (all distinct letters).
    The answer is never the opener. Returns None only when the bank
    is empty.
    """
    import random as _r2
    a = str(answer or "").upper()
    pool = [str(w).upper() for w in (bank or []) if str(w).isalpha()]
    if not pool:
        return None
    _generic = {4: ["RATE", "CARE", "LATE", "TARE", "TEAR"],
                5: ["CRANE", "SLATE", "TRACE", "ADIEU", "ROAST", "AUDIO"],
                6: ["CRANES", "SLATES", "TRACES", "ROASTS"]}.get(n) or []
    _bank_set = set(pool)
    _legal = [o for o in _generic if o in _bank_set and o != a]
    if _legal:
        return _r2.Random(seed).choice(_legal)
    _hi = sorted(w for w in pool if len(set(w)) == len(w) and w != a)
    _choices = _hi or sorted(w for w in pool if w != a) or pool
    return _r2.Random(seed).choice(_choices)


def solve_semantle(page, ans, gid="semantle"):
    """Semantle: play a real semantic guess path, answer last.

    The spread now comes from the game's own several-thousand-word list seeded
    by the puzzle date, so it differs every day, and any word the site refuses is
    dropped and replaced rather than burning a guess.
    """
    gs, _ = S.semantic_probe_path(ans, gid=gid, width=_SEMANTIC_MAX_PROBES,
                            seed=_day_seed(gid))
    inp = "input[placeholder='Enter a word...']"
    rejected, used, scores = set(), [], []
    spare = [w for w in S.words_semantic(gid)
             if w not in set(gs) and w != str(ans).lower()]
    import random as _r
    _r.Random(_day_seed(gid) + ":spare").shuffle(spare)
    # Probes only in the queue; the answer is typed AFTER the probe queue
    # drains, so a replacement appended for a rejected probe is actually
    # reached (old code iterated a snapshot `list(gs)` while mutating `gs`,
    # so the replacement was never typed — dead code).
    _ans_l = str(ans).lower()
    gs = [w for w in gs if w != _ans_l]
    _qi, _attempted = 0, 0
    while _qi < len(gs):
        w = gs[_qi]
        _qi += 1
        # Dedupe guard (fix-loop iter 11, hardened iter 12): NEVER type
        # a word already in played[] ∪ rejected[] (case-insensitive) —
        # Contexto typed the same word twice. A duplicate is skipped,
        # not retyped.
        if _semantic_dup(w, used, rejected):
            _dbg(gid, f"duplicate {w!r} skipped (already played/rejected)")
            continue
        # HARD CAP: probes stop at 4 TYPED attempts so the answer stays
        # the 5th typed word; a 6th typed word is never allowed.
        # Fix-loop iter 12: the old check counted only ACCEPTED words
        # (len(used)), so under a rejection storm every rejected probe
        # was replaced and retyped without bound (reproduced: 4157
        # Enters on an all-rejected page). _attempted counts every
        # probe actually typed, accepted or not.
        if _attempted >= _SEMANTIC_MAX_PROBES:
            _dbg(gid, f"probe cap hit ({_SEMANTIC_MAX_PROBES} typed); "
                      f"stopping probes for the answer")
            break
        if len(used) >= _SEMANTIC_MAX_PROBES:
            _dbg(gid, f"probe cap hit ({_SEMANTIC_MAX_PROBES}); "
                      f"stopping probes for the answer")
            break
        _attempted += 1
        _dbg(gid, f"guess {_attempted} {w!r} (queue {_qi}/{len(gs)})")
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
            _REJECTED[gid] = sorted(rejected)
            _dbg(gid, f"{w!r} rejected by the site; replacing it "
                      f"(rejected={sorted(rejected)})")
            # Replacement counts against the cap and must differ
            # from ALL previously typed words (used + rejected + queue).
            # Fix-loop iter 12: the old formula counted ACCEPTED words
            # (len(used)), so a rejected probe always left "room" for a
            # replacement and the queue grew without bound. Count
            # TYPED probes (_attempted) instead: the invariant is
            # typed + queued <= 4, so at most 4 probes are ever typed.
            _can_replace = (_attempted + (len(gs) - _qi)
                            < _SEMANTIC_MAX_PROBES)
            for alt in list(spare):
                if (not _can_replace):
                    break
                if (not _semantic_dup(alt, used, rejected)
                        and str(alt).lower() not in
                        {str(q).lower() for q in gs}):
                    gs.append(alt)
                    spare.remove(alt)
                    _dbg(gid, f"replacement queued: {alt!r} "
                              f"(queue now {len(gs)})")
                    break
            else:
                _dbg(gid, f"no spare replacement left for {w!r}; guess lost")
            continue
        used.append(w)
        _PLAYED[gid] = list(used)
        if score is not None:
            scores.append((w, score))
        # Feedback-driven next choice: reorder the spare pool by this
        # guess's score/rank (high -> stem-sharing words first; low ->
        # distant broad words first) so the next word actually follows
        # from the feedback just read, and swap the next queued probe
        # for the best feedback-fitting spare when the queued word
        # does not fit the feedback direction.
        try:
            spare = _semantic_feedback_reorder(spare, w, gid, score)
            if spare and _qi < len(gs) and score is not None:
                _next_q = gs[_qi]
                _best = spare[0]
                _close = _semantic_closeness(gid, score)
                # Fix-loop iter 12: never swap in a word already in
                # played[] ∪ rejected[] or already queued — the swap
                # bypassed the dedupe guard (it writes straight into gs).
                if (_best != _next_q and _close in ("high", "low")
                        and not _semantic_dup(_best, used, rejected)
                        and str(_best).lower() not in
                        {str(q).lower() for q in gs}):
                    gs[_qi] = _best
                    spare[0] = _next_q
                    _dbg(gid, f"feedback-driven next: queue "
                              f"{_next_q!r} -> {_best!r} "
                              f"(score={score!r} closeness={_close})")
        except Exception as _fe:
            _dbg(gid, f"feedback reorder skipped: {str(_fe)[:60]}")
        _dbg(gid, f"guess {_attempted} accepted: {w!r} score={score!r} "
                  f"(played={used})")
    # Answer last, after every probe/replacement has actually been played.
    # HARD CAP + dedupe: if the answer was already typed (duplicate),
    # do not type it again; if 5 words are already typed, return False
    # — never type a 6th word.
    if _ans_l in [str(u).lower() for u in used]:
        _dbg(gid, f"answer {_ans_l!r} already typed; not retyping")
    elif len(used) >= _SEMANTIC_MAX_TYPED:
        return False, (f"cap hit: {len(used)} typed without win; "
                       f"never typing a 6th word")
    else:
        try:
            _idle_drift(page)
            _type_into(page, "input", _ans_l, base_delay=150)
            page.keyboard.press("Enter")
        except Exception as e:
            return False, f"answer {_ans_l!r} failed: {str(e)[:120]}"
        _settle(page, base=2600)
        used.append(_ans_l)
        _PLAYED[gid] = list(used)
    _REJECTED[gid] = sorted(rejected)
    _dbg(gid, f"guess {len(used)} answer {_ans_l!r} played "
              f"(played={used} rejected={sorted(rejected)})")
    ok, ev = _strict_win(page, ans)
    _score_txt = (f"scores={scores}" if scores else
                  "scores not exposed in DOM (no per-guess score row "
                  "found in the guess list/table for any played guess)")
    if not ok:
        return False, (f"{ev}; guesses={used} {_score_txt} "
                       f"rejected={sorted(rejected)}")
    return True, (f"{ev} after {len(used)} typed guesses "
                   f"({_score_txt}; rejected={sorted(rejected)})")


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
    gs, _ = S.semantic_probe_path(ans, gid=gid, width=_SEMANTIC_MAX_PROBES,
                            seed=_day_seed(gid))
    rejected, used, scores = set(), [], []
    spare = [w for w in S.words_semantic(gid)
             if w not in set(gs) and w != str(ans).lower()]
    import random as _r
    _r.Random(_day_seed(gid) + ":spare").shuffle(spare)
    # Probes only in the queue; the answer is typed AFTER the probe queue
    # drains, so a replacement appended for a rejected probe is actually
    # reached (old code iterated a snapshot `list(gs)` while mutating `gs`,
    # so the replacement was never typed — dead code, and the video showed
    # 5 guesses while the plan/chapters claimed 6).
    _ans_l = str(ans).lower()
    gs = [w for w in gs if w != _ans_l]
    _qi, _attempted = 0, 0
    while _qi < len(gs):
        w = gs[_qi]
        _qi += 1
        # Dedupe guard (fix-loop iter 11, hardened iter 12): NEVER type
        # a word already in played[] ∪ rejected[] (case-insensitive) —
        # Contexto typed the same word twice. A duplicate is skipped,
        # not retyped.
        if _semantic_dup(w, used, rejected):
            _dbg(gid, f"duplicate {w!r} skipped (already played/rejected)")
            continue
        # HARD CAP: probes stop at 4 TYPED attempts so the answer stays
        # the 5th typed word; a 6th typed word is never allowed.
        # Fix-loop iter 12: count TYPED probes (_attempted), not just
        # accepted ones — see solve_semantle (rejection-storm repro:
        # 4157 Enters on an all-rejected page under the old formula).
        if _attempted >= _SEMANTIC_MAX_PROBES:
            _dbg(gid, f"probe cap hit ({_SEMANTIC_MAX_PROBES} typed); "
                      f"stopping probes for the answer")
            break
        if len(used) >= _SEMANTIC_MAX_PROBES:
            _dbg(gid, f"probe cap hit ({_SEMANTIC_MAX_PROBES}); "
                      f"stopping probes for the answer")
            break
        _attempted += 1
        _dbg(gid, f"guess {_attempted} {w!r} (queue {_qi}/{len(gs)})")
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
            _REJECTED[gid] = sorted(rejected)
            _dbg(gid, f"{w!r} rejected by the site; replacing it "
                      f"(rejected={sorted(rejected)})")
            # Replacement counts against the cap and must differ
            # from ALL previously typed words (used + rejected + queue).
            # Fix-loop iter 12: count TYPED probes (_attempted) — the
            # invariant is typed + queued <= 4, so at most 4 probes
            # are ever typed even under a rejection storm.
            _can_replace = (_attempted + (len(gs) - _qi)
                            < _SEMANTIC_MAX_PROBES)
            for alt in list(spare):
                if (not _can_replace):
                    break
                if (not _semantic_dup(alt, used, rejected)
                        and str(alt).lower() not in
                        {str(q).lower() for q in gs}):
                    gs.append(alt)
                    spare.remove(alt)
                    _dbg(gid, f"replacement queued: {alt!r} "
                              f"(queue now {len(gs)})")
                    break
            else:
                _dbg(gid, f"no spare replacement left for {w!r}; guess lost")
            continue
        used.append(w)
        _PLAYED[gid] = list(used)
        if score is not None:
            scores.append((w, score))
        # Feedback-driven next choice: reorder the spare pool by this
        # guess's score/rank (high -> stem-sharing words first; low ->
        # distant broad words first) so the next word actually follows
        # from the feedback just read, and swap the next queued probe
        # for the best feedback-fitting spare when the queued word
        # does not fit the feedback direction.
        try:
            spare = _semantic_feedback_reorder(spare, w, gid, score)
            if spare and _qi < len(gs) and score is not None:
                _next_q = gs[_qi]
                _best = spare[0]
                _close = _semantic_closeness(gid, score)
                # Fix-loop iter 12: never swap in a word already in
                # played[] ∪ rejected[] or already queued — the swap
                # bypassed the dedupe guard (it writes straight into gs).
                if (_best != _next_q and _close in ("high", "low")
                        and not _semantic_dup(_best, used, rejected)
                        and str(_best).lower() not in
                        {str(q).lower() for q in gs}):
                    gs[_qi] = _best
                    spare[0] = _next_q
                    _dbg(gid, f"feedback-driven next: queue "
                              f"{_next_q!r} -> {_best!r} "
                              f"(score={score!r} closeness={_close})")
        except Exception as _fe:
            _dbg(gid, f"feedback reorder skipped: {str(_fe)[:60]}")
        _dbg(gid, f"guess {_attempted} accepted: {w!r} score={score!r} "
                  f"(played={used})")
    if len(used) >= _SEMANTIC_MAX_TYPED and \
            _ans_l not in [str(u).lower() for u in used]:
        return False, (f"cap hit: {len(used)} typed without win; "
                       f"never typing a 6th word")
    if _ans_l not in [u.lower() for u in used]:
        try:
            _idle_drift(page)
            _type_into(page, "input.word", _ans_l, base_delay=150)
            page.keyboard.press("Enter")
            _settle(page, base=2600)
            used.append(_ans_l)
            _PLAYED[gid] = list(used)
        except Exception as ex:
            return False, f"answer submit failed: {str(ex)[:120]}"
    _REJECTED[gid] = sorted(rejected)
    _dbg(gid, f"answer {_ans_l!r} played "
              f"(played={used} rejected={sorted(rejected)})")
    ok, ev = _strict_win(page, ans)
    _score_txt = (f"scores={scores}" if scores else
                  "scores not exposed in DOM (no per-guess score row "
                  "found in the guess list for any played guess)")
    if not ok:
        return False, (f"{ev}; guesses={used} {_score_txt} "
                       f"rejected={sorted(rejected)}")
    return True, (f"{ev} after {len(used)} typed guesses "
                   f"({_score_txt}; rejected={sorted(rejected)})")


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
    import random as _r
    pool = [t for t in _FRAMED_PROBES if t.lower() != a.lower()]
    try:  # archived real titles rotate the opener every single day
        _fd = json.loads((HERE / "frontend_data" / "static" /
                          "framed_data.json").read_text(encoding="utf-8"))
        for _m in (_fd.get("modes") or {}).values():
            for _e in (_m.get("entries") or []):
                _t = str(_e.get("answer") or "").strip()
                if _t and _t.lower() != a.lower() and _t not in pool:
                    pool.append(_t)
    except Exception:
        pass
    rnd = _r.Random(f"framed:{A.target_date().isoformat()}")
    probes = rnd.sample(pool, min(max_guesses - 1, len(pool))) if pool else []
    out = probes + [a]
    steps = [{"turn": i + 1, "guess": g, "pattern": "", "pool_before": None,
              "pool_after": None} for i, g in enumerate(out)]
    return out, steps


def _honest_fallback(answer, opener=None, max_guesses=6, seed=None):
    """Last-resort plan that still never gives the answer away early.

    Every game used to fall back to `[answer]` here, which typed the secret on
    guess 1 whenever a word bank was missing. This builds a real probe list
    instead, and reserves the answer for the final move. The probe order
    is DATE-SEEDED shuffled (fix-loop iter 11): the old fixed order
    CRANE, SLATE, AUDIO, ... opened every fallback video identically.
    """
    a = str(answer).upper()
    probes = [p for p in (opener, "CRANE", "SLATE", "AUDIO", "TILES", "ROAST",
                          "TRACE", "HEART", "STONE", "NOTES") if p]
    import random as _r
    _r.Random(seed if seed is not None else a).shuffle(probes)
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
        p = S.plan_sequence(a, pool, g,
                            opener=_daily_opener(["ABOUT", "CRANE", "SLATE",
                                                  "TRACE", "ADIEU", "ROAST"]))
    elif gid == "canuckle":
        g, pool = S.words_canuckle()
        p = S.plan_sequence(a, pool, g,
                            opener=_daily_opener(["CRANE", "SLATE", "TRACE",
                                                  "ADIEU", "ROAST", "AUDIO"]))
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
            p = _honest_fallback(a, "RATE" if n == 4 else "CRANE",
                              seed=_day_seed(gid))
        else:
            # Date-seeded opener chosen FROM the bank (fix-loop iter 12):
            # the old generic openers (CRANE/SLATE/TRACE/...) are not in
            # Searchle's own guess bank for any length, so plan_sequence
            # silently dropped the opener and played an entropy pick
            # (STARE) as guess 1. _searchle_opener guarantees the opener
            # is a legal guess, so it is actually played first.
            opener = _searchle_opener(bank, n, _day_seed(gid), a)
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
             else _honest_fallback(a, max_guesses=5, seed=_day_seed(gid)))
    elif gid == "colordle":
        # Colour-distance search over the site's own colour list. Needs the
        # secret hex, which the caller stashes in _HEX (see run_one).
        hx = _HEX.get(gid)
        if hx:
            p = S.plan_colordle(hx, date_str=A.target_date().isoformat())
        else:
            p = _honest_fallback(a, max_guesses=5, seed=_day_seed(gid))
    elif gid == "colorfle":
        cols = _COLORS.get(gid)
        if cols:
            p = S.plan_colorfle([int(c) for c in cols])
        else:
            p = _honest_fallback(a, max_guesses=6, seed=_day_seed(gid))
    elif gid in ("semantle", "contexto"):
        # Semantic games score a guess 0-100 with no letter feedback, so the
        # honest plan is a spread of real probes ending on the answer. Both
        # sites allow SIX guesses TOTAL, so only FIVE probes fit before the
        # winning move. The pool is thousands of real words seeded by the
        # puzzle DATE, so the opening is different every day instead of the
        # same six hand-written words in every video.
        p = S.semantic_probe_path(a, gid=gid, width=_SEMANTIC_MAX_PROBES,
                            seed=_day_seed(gid))
    elif gid.startswith("framed"):
        # Framed is a movie-frame game: the site gives NO letter feedback and no
        # similarity score, so constraint propagation is impossible. The
        # strongest honest play is to burn real wrong titles until the site's
        # own guess counter forces the last move, and the slides say exactly
        # that rather than dressing it up as deduction.
        p = _framed_probe_path(a)
    elif gid == "worldle":
        # 3 probes + answer = 4 total: tight, human, always 3-5 steps.
        p = S.geo_probe_path(a, S.countries_worldle(), gid=gid, width=3,
                             seed=_day_seed(gid))
    elif gid == "globle":
        p = S.geo_probe_path(a, S.countries_globle(), gid=gid, width=3,
                             seed=_day_seed(gid))
    elif gid == "countryle":
        bank = S.countries_countryle() or S.countries_globle()
        p = S.geo_probe_path(a, bank, gid=gid, width=3, seed=_day_seed(gid))
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
        # FIX-LOOP: waffle answer is space-joined words (e.g. 'SWEPT SHOUT ...').
        # The old code appended a.upper() which was the CONCATENATED
        # 30-letter string ('EXPELEOARESETIECEARTH') as a fake 6th guess.
        # Plan is now just the real words.
        _aw = [w for w in str(a).upper().split() if w] if " " in str(a) else []
        seq = _aw if _aw else [w for w in words if w][:6]
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
            p = _honest_fallback(a, seed=_day_seed(gid))
    elif gid in ("marveldle", "batterup"):
        # Attribute games: 2 spread probes from the vendored pool + answer.
        # This is the same list solve_attr_game plays, so slides/captions
        # always match the footage (never generic CRANE/SLATE filler).
        pool = [str(n) for n in _attr_pool(gid)
                if str(n).lower() != str(a).lower()]
        # Date-seeded sample (fix-loop iter 11): the old pool[0] /
        # pool[mid] picks opened every Batterup/Marveldle video with
        # the same two probes. Same seed as solve_attr_game so slides
        # match footage; the answer is excluded from the pool above.
        import random as _r3
        _sample = (_r3.Random(_day_seed(gid)).sample(
            pool, min(2, len(pool))) if pool else [])
        seq = []
        for cand in _sample:
            if cand and cand not in seq:
                seq.append(cand)
        seq = (seq[:2] + [a]) if a else seq
        steps = [{"turn": i + 1, "guess": g, "pattern": "",
                  "pool_before": None, "pool_after": None}
                 for i, g in enumerate(seq)]
        p = (seq, steps)
    else:
        p = _honest_fallback(a, seed=_day_seed(gid))
    _PLANS[gid] = p
    return p


def _guesses(gid, answer):
    return _plan_for(gid, answer)[0]


def _steps(gid, answer):
    return _plan_for(gid, answer)[1]


def _betweenle_bounds(page):
    """Current (lo, hi) bounds from the live game, either side None when
    unknown. Sources, in order: (1) the bound rows' own text — "Secret
    word is placed in dictionary between LEVEL and SLATE"; (2) the
    rejection banner — "Enter word placed in dictionary before PARTY"
    (hi=PARTY) / "... after CRANE" (lo=CRANE). A bounds read that
    returns None for every guess (old code: only source (1), and the
    migrated build no longer prints that sentence) silently disabled
    the in-bounds guard, so out-of-bounds words were typed and then
    replayed in a ~38s stuck loop. Returns (lo, hi) or (None, None)."""
    try:
        t = (page.evaluate("() => document.body.innerText") or "")
    except Exception:
        return (None, None)
    import re as _re
    m = _re.search(r"between\s+([A-Za-z]{3,15})\s+and\s+([A-Za-z]{3,15})", t)
    if m:
        return m.group(1).upper(), m.group(2).upper()
    lo = hi = None
    m2 = _re.search(r"placed in dictionary after\s+([A-Za-z]{3,15})", t,
                    _re.IGNORECASE)
    if m2:
        lo = m2.group(1).upper()
    m3 = _re.search(r"placed in dictionary before\s+([A-Za-z]{3,15})", t,
                    _re.IGNORECASE)
    if m3:
        hi = m3.group(1).upper()
    return (lo, hi)


def _betweenle_rejected_showing(page):
    """True when the game is showing a rejection for the last word:
    dictionary rejection ("not a word" family) or an out-of-bounds
    banner ("Enter word placed in dictionary before/after X"). A
    rejected word must NEVER be retyped — the solver moves to the
    next candidate."""
    try:
        txt = (page.evaluate("() => document.body.innerText") or "").lower()
    except Exception:
        return False
    return any(s in txt for s in (
        "not a word", "not valid", "invalid word", "not in the word list",
        "unknown word", "placed in dictionary before",
        "placed in dictionary after"))


def _betweenle_pick_in_bounds(gs, bank, bounds, used, answer):
    """Back-compat wrapper: pick excluding `used` only.

    The solver itself must pass used ∪ rejected (see
    _betweenle_pick_excluding) — iter-11 passed only `used`, so a word
    misclassified as rejected (BIKER, run 39043936480) was re-picked
    and the loop never reached a new word."""
    return _betweenle_pick_excluding(gs, bank, bounds, used, answer)


def _betweenle_pick_excluding(gs, bank, bounds, excluded, answer):
    """Next guess strictly inside bounds, excluding `excluded`
    (= used ∪ rejected): prefer the planned word, else the bank word
    closest to the alphabetical middle of (lo, hi). Either bound may
    be None (unknown side = unbounded)."""
    lo, hi = bounds if bounds else (None, None)
    lo = lo or "AAAAA"
    hi = hi or "ZZZZZ"
    excl = {str(w).upper() for w in (excluded or [])}
    def inside(w):
        w = str(w).upper()
        return lo < w < hi and w not in excl
    for g in gs:
        if inside(g):
            return str(g).upper()
    mid = [w for w in bank if inside(w)]
    if mid:
        return mid[len(mid) // 2]
    return str(answer).upper() if inside(answer) else None


# Rejection-banner markers for Betweenle: the game's per-guess refusal
# notices ("not a word" family + out-of-bounds "placed in dictionary
# before/after X"). A banner that was ALREADY showing before this guess
# was submitted is a STANDING banner, not a notice about this guess.
_BETWEENLE_REJECTION_MARKERS = (
    "not a word", "not valid", "invalid word", "not in the word list",
    "unknown word", "word not in list",
    "placed in dictionary before", "placed in dictionary after")


def _betweenle_extract_banner(text):
    """Pure: the rejection-banner excerpt(s) inside raw page text.

    Returns the normalized text around each rejection marker (or ""
    when no marker is present). Comparing the BEFORE-submit and
    AFTER-submit excerpts is what distinguishes a fresh rejection of
    this guess from a stale standing banner left by an earlier guess.
    """
    import re as _re
    t = _re.sub(r"\s+", " ", str(text or "")).strip()
    if not t:
        return ""
    low = t.lower()
    parts = []
    for m in _BETWEENLE_REJECTION_MARKERS:
        i = low.find(m)
        while i != -1:
            parts.append(t[max(0, i - 40):i + 90].strip().lower())
            i = low.find(m, i + 1)
    return " | ".join(sorted(set(parts)))


def _betweenle_rejection_banner(page):
    """Current rejection-banner excerpt from the live page ("" if none)."""
    try:
        return _betweenle_extract_banner(
            page.evaluate("() => document.body.innerText") or "")
    except Exception:
        return ""


def _betweenle_banner_semantic(banner_text):
    """Pure: semantic content of a rejection-banner excerpt.

    Returns (bounds_tuples, has_invalid) where bounds_tuples is a
    frozenset of (direction, bound) from "placed in dictionary
    before/after <BOUND>", and has_invalid is True when an
    invalid-word marker is present. Excerpt comparison (iter-12) was
    fragile to surrounding-text changes; the (bound, direction) tuple
    is what the game actually means. The banner excerpt is already
    lowercased by _betweenle_extract_banner.
    """
    import re as _re
    t = str(banner_text or "").lower()
    tuples = set()
    for m in _re.finditer(r"placed in dictionary (before|after) ([a-z]+)",
                          t):
        tuples.add((m.group(1), m.group(2).upper()))
    invalid = any(s in t for s in (
        "not a word", "not valid", "invalid word", "not in the word list",
        "unknown word", "word not in list"))
    return frozenset(tuples), invalid


def _betweenle_new_rejection(before_banner, after_banner):
    """Pure: True only when the AFTER snapshot carries rejection content
    the BEFORE snapshot did not. An unchanged (standing) banner is NOT
    a fresh rejection — iter-11 classified BIKER rejected from a stale
    standing banner alone, then re-picked it forever. Compared on
    SEMANTIC tuples (iter 13), not excerpts: surrounding-text changes
    can no longer fake a fresh rejection."""
    after_t, after_inv = _betweenle_banner_semantic(after_banner)
    if not after_t and not after_inv:
        return False
    before_t, before_inv = _betweenle_banner_semantic(before_banner)
    if after_t - before_t:
        return True
    return bool(after_inv and not before_inv)


def _betweenle_banner_consistent(new_tuples, word):
    """Pure: True when a fresh bounds banner could be ABOUT this word.

    A (before, B) banner means the entered word violated "must be
    placed before B" — i.e. the entered word is >= B. An (after, B)
    banner means it violated "must be placed after B" — i.e. the
    entered word is <= B. Iter-12's killer: the banner belonged to
    STALE VIPER ("before DINER") while the bot believed it had typed
    BIKER (BIKER < DINER, cannot violate "before DINER") — the
    rejection was misattributed to a word that was never typed.
    Invalid-word banners carry no bound, so they cannot be attributed
    this way (treated as consistent — see classifier note).
    """
    w = str(word or "").upper()
    for direction, bound in (new_tuples or ()):
        if direction == "before" and w < bound:
            return False
        if direction == "after" and w > bound:
            return False
    return True


def _betweenle_classify_submit(before_banner, after_banner,
                               before_n, after_counters, word):
    """Pure submit classifier (banner BEFORE/AFTER + game counters).

    Returns "submitted" when the game's own counters confirm this word
    (GUESS counter advanced — the stats-panel clause only counts when
    the panel shows a REAL word, never the 'WORD' placeholder the
    panel carries during play, which iter-12 mistook for a submitted
    guess); "rejected" when a NEW rejection banner appeared AND its
    bounds content is consistent with this word; "buffer_failure"
    when a new bounds banner appeared but is INCONSISTENT with this
    word — the banner belongs to a stale buffer word, not this guess,
    so this word was never judged (never mark it rejected);
    "no_change" otherwise (no counter move, no fresh banner: the Enter
    was never delivered — focus/enter-delivery failure, NOT a refusal).
    A standing banner alone can never yield "rejected".
    """
    after = after_counters or {}
    _stats = str(after.get("stats_word") or "").strip()
    _stats_real = _stats and _stats.upper() != "WORD"
    if ((after.get("guess_n") or 0) > (before_n or 0) or
            (_stats_real and word and word.lower() in _stats.lower())):
        return "submitted"
    if _betweenle_new_rejection(before_banner, after_banner):
        _after_t, _ = _betweenle_banner_semantic(after_banner)
        _before_t, _ = _betweenle_banner_semantic(before_banner)
        _new_t = _after_t - _before_t
        if _new_t and not _betweenle_banner_consistent(_new_t, word):
            return "buffer_failure"
        return "rejected"
    return "no_change"


# Synthetic Enter delivery: dispatch keydown/keypress/keyup Enter on
# the document AND on the focused element (same primitive the worgle
# solver uses). Last link of the submit retry chain — it proves the
# Enter reached the game even when clicks/physical keys are swallowed.
_BETWEENLE_SYNTHETIC_ENTER_JS = (
    "() => { for (const t of ['keydown', 'keypress', 'keyup']) {"
    " document.dispatchEvent(new KeyboardEvent(t, {key: 'Enter',"
    " code: 'Enter', keyCode: 13, bubbles: true}));"
    " (document.activeElement || document.body).dispatchEvent("
    " new KeyboardEvent(t, {key: 'Enter', code: 'Enter', keyCode: 13,"
    " bubbles: true})); } return true; }")


def _betweenle_synthetic_enter(page):
    """Deliver Enter via synthetic KeyboardEvents. Returns True when
    the dispatch ran (delivery to the game is NOT guaranteed by the
    return value — counters arbitrate, as everywhere else)."""
    try:
        return bool(page.evaluate(_BETWEENLE_SYNTHETIC_ENTER_JS))
    except Exception:
        return False


def _betweenle_submit_retry_chain(page, gid, word, before_n):
    """Multi-mechanism submit retry for the no-banner-change case.

    Restores the chain that DELIVERED BIKER in run 39043936480 and
    that iter-11 removed ("ONE Enter resend only"): dismiss+resend →
    canvas sweep → synthetic key dispatch. Every mechanism resends
    ENTER ONLY — the typed letters are never retyped, so if the word
    was already accepted (counters lagging) the extra Enters land on
    an empty buffer and are no-ops; if it was never delivered, one of
    the mechanisms delivers it. Counters arbitrate after each step.
    Returns (submitted, counters, mechanism-or-None)."""
    # 1. dismiss+resend: clear help/tooltip overlays, reclaim focus from
    #    ad iframes, click the canvas centre, resend Enter (keycap first).
    try:
        _close_betweenle_help(page)
    except Exception:
        pass
    try:
        page.evaluate(
            "() => { const a = document.activeElement; "
            "if (a && a !== document.body) a.blur(); "
            "if (a && a.tagName === 'IFRAME') { try { a.contentWindow.blur(); } "
            "catch (e) {} } try { window.top.focus(); } catch (e) {} "
            "window.focus(); }")
    except Exception:
        pass
    try:
        page.mouse.click(960, 600)
        QP.ev_push(960, 600, "click")
        page.wait_for_timeout(300)
    except Exception:
        pass
    try:
        if not _click_on_screen_key(page, "enter"):
            page.keyboard.press("Enter")
    except Exception:
        pass
    submitted, cnt = _betweenle_submitted(page, before_n, word)
    _dbg(gid, f"retry chain {word!r}: dismiss+resend -> submitted={submitted}")
    if submitted:
        return True, cnt, "dismiss+resend"
    # 2. canvas sweep: click a sweep of points across the canvas/board
    #    (a single centre click can land on a dead overlay pixel), then
    #    resend Enter via the on-screen keycap / physical key.
    try:
        for _x, _y in ((960, 500), (760, 500), (1160, 500),
                       (960, 620), (960, 420), (960, 500)):
            try:
                page.mouse.click(_x, _y)
                QP.ev_push(_x, _y, "click")
                page.wait_for_timeout(120)
            except Exception:
                continue
        if not _click_on_screen_key(page, "enter"):
            page.keyboard.press("Enter")
    except Exception:
        pass
    submitted, cnt = _betweenle_submitted(page, before_n, word)
    _dbg(gid, f"retry chain {word!r}: canvas sweep -> submitted={submitted}")
    if submitted:
        return True, cnt, "canvas-sweep"
    # 3. synthetic key dispatch: JS KeyboardEvents on document + focus.
    _betweenle_synthetic_enter(page)
    submitted, cnt = _betweenle_submitted(page, before_n, word)
    _dbg(gid, f"retry chain {word!r}: synthetic dispatch -> "
              f"submitted={submitted}")
    if submitted:
        return True, cnt, "synthetic-dispatch"
    return False, cnt, None


def _betweenle_seed_restored_state(page, gid):
    """Seed (used, submitted, rejected) from restored state BEFORE
    replaying after a reboot/restore.

    Sources: this run's in-process ledgers (_PLAYED/_REJECTED, kept
    across run_one's reboot retry) + the live game's own counters
    (GUESS n/14, stats-panel word). Legacy rejected entries suffixed
    " (not submitted)" are cleaned to the plain word. When the live
    counter shows MORE submitted guesses than the ledger names, the
    unknown ones are logged, never invented — the bounds read will
    still constrain the remaining picks, and already-submitted plan
    words are skipped, never retyped. Returns (used set, submitted
    list, rejected list); also syncs _PLAYED/_REJECTED."""
    import re as _re
    def _clean(words):
        out = []
        for w in (words or []):
            c = str(w).split(" (")[0].strip().upper()
            if c and _re.fullmatch(r"[A-Z]+", c) and c not in out:
                out.append(c)
        return out
    submitted = _clean(_PLAYED.get(gid))
    rejected = _clean(_REJECTED.get(gid))
    try:
        cnt = _betweenle_counters(page) or {}
    except Exception:
        cnt = {}
    guess_n = cnt.get("guess_n") or 0
    # GUESS n/14 is the CURRENT-guess number (1-based index of the
    # guess being typed), NOT a submitted count: a fresh board reads
    # 1/14 with zero submitted. Submitted-before-this-run = n - 1.
    # The stats panel carries the 'WORD' placeholder during play —
    # iter-12 seeded it as a submitted word (the WORD phantom:
    # 1/14 -> 3/14 after 2 real submits proved WORD was never
    # submitted). NEVER seed a submitted word from the stats panel.
    _submitted_before = max(0, guess_n - 1)
    if _submitted_before > len(submitted):
        _dbg(gid, f"restore seed: live GUESS counter={guess_n} means "
                  f"{_submitted_before} submitted before this run but "
                  f"ledger names {len(submitted)}; "
                  f"{_submitted_before - len(submitted)} submitted word(s) "
                  f"unknown — not inventing them; remaining picks stay "
                  f"bounds-bound")
    _PLAYED[gid] = list(submitted)
    _REJECTED[gid] = list(rejected)
    used = set(submitted) | set(rejected)
    _dbg(gid, f"restore seed: used={sorted(used)} submitted={submitted} "
              f"rejected={rejected}")
    return used, submitted, rejected


def _betweenle_clear_buffer(page, gid):
    """Clear any stale typing-row word (iter 13).

    After a rejection the game does NOT clear the typing buffer, so
    the next word's keystrokes land in a full buffer and are dropped
    (iter-12: VIPER stayed in the row while the bot "played" BIKER
    then BRANS; each Enter re-submitted stale VIPER). The game's own
    key handler clears on Backspace (see _type_betweenle_via_keys),
    so send Backspaces unconditionally before every word — no
    buffer-content read exists for this canvas app, so the clear is
    unconditional, never conditional on a read."""
    try:
        for _ in range(10):
            page.keyboard.press("Backspace")
            page.wait_for_timeout(35)
        _dbg(gid, "buffer cleared (10 backspaces)")
    except Exception as _e:
        _dbg(gid, f"buffer clear failed: {str(_e)[:60]}")


def _betweenle_attempt_submit(page, gid, g, cnt_before):
    """Type one word + Enter + classify. Returns (verdict, counters).

    Verdicts: submitted / rejected / buffer_failure / no_change (see
    _betweenle_classify_submit). Letters are typed exactly once here;
    a buffer_failure retry is the CALLER's decision (clear + call again).
    """
    _type_like_a_person(page, str(g), base_delay=170)
    page.wait_for_timeout(300)
    # Banner BEFORE submit (iter-12 (b)): a rejection banner already
    # showing here belongs to an EARLIER guess. Only a banner that is
    # NEW after this submit may classify this word rejected.
    _banner_before = _betweenle_rejection_banner(page)
    page.keyboard.press("Enter")
    _settle(page, base=3000)
    _cnt1 = _betweenle_counters(page) or {}
    _banner_after = _betweenle_rejection_banner(page)
    _verdict = _betweenle_classify_submit(
        _banner_before, _banner_after, cnt_before, _cnt1, g)
    _dbg(gid, f"submit check {g!r}: counters GUESS "
              f"{_cnt1.get('guess_n')}/{_cnt1.get('guess_total')} "
              f"(was {cnt_before}) stats={_cnt1.get('stats_word')!r} "
              f"banner new={_betweenle_new_rejection(_banner_before, _banner_after)} "
              f"verdict={_verdict}")
    return _verdict, _cnt1


def _betweenle_stats_won(counters, ans):
    """Pure win check from the game's own counters (iter-15 P3):
    the stats panel word equals the answer. The counters are readable
    in-loop without Flutter semantics; the stats panel shows the
    winning word the moment the winning guess lands."""
    try:
        sw = str((counters or {}).get("stats_word") or "").strip()
    except Exception:
        return False
    return bool(sw) and bool(ans) and sw.upper() == str(ans).strip().upper()


def _betweenle_won_after_guess(page, ans):
    """Win check after one submitted guess (iter-14 P3, iter-15 fix).

    Iter-13 played one extra guess (HIKER) on the already-won board:
    the retry chain fired on a post-win no_change because nothing
    checked the win between guesses. Iter-14 checked _strict_win
    after each guess, but _strict_win reads Flutter semantics and
    _enable_flutter_semantics only runs AFTER the loop — in-loop the
    win was invisible, so HIKER was still typed even though the
    bot's own counters already showed stats='BIKER' at the win.
    Iter-15: check stats_word == answer FIRST (readable in-loop, no
    semantics needed), then _strict_win. Read-only — never raises.
    Returns (won_bool, evidence)."""
    try:
        cnt = _betweenle_counters(page) or {}
    except Exception:
        cnt = {}
    if _betweenle_stats_won(cnt, ans):
        return True, (f"stats_word={cnt.get('stats_word')!r} == answer "
                      "(game counters; no semantics needed)")
    try:
        won, ev = _strict_win(page, ans)
    except Exception:
        return False, ""
    return bool(won), str(ev or "")


def _s_betweenle(page, ans, gid):
    _close_modals(page)
    # Post-migration the daily game starts from a DOM menu button labelled
    # DAILY WORD (the old #menupanel-mode-daily id no longer exists, so the
    # game never left the menu and every guess typed into the void).
    _started = False
    for _sel in ("button:has-text('DAILY WORD')", "text=DAILY WORD",
                 "a:has-text('DAILY WORD')", "#menupanel-mode-daily"):
        try:
            el = page.query_selector(_sel)
            if el and el.is_visible():
                _bb = el.bounding_box()
                el.click(timeout=3000)
                if _bb:
                    QP.ev_push(_bb["x"] + _bb["width"] / 2,
                               _bb["y"] + _bb["height"] / 2, "click")
                _started = True
                _dbg(gid, f"daily game started via {_sel}")
                break
        except Exception:
            continue
    if not _started:
        _dbg(gid, "WARN: no DAILY WORD button found on menu")
    page.wait_for_timeout(4000)
    # Must come after the mode click: the How-to-Play panel is raised by
    # starting the daily game, and it swallows all keyboard input until closed.
    _close_betweenle_help(page)
    # Submitted-guess ledger: chapters/cards/evidence count SUBMITTED
    # guesses only (game counters), never planned/rejected words (run
    # 39039561115: RATIO was counted as guess 3 though the game
    # rejected it — "Full solve (4 guesses)" was false; real submitted
    # = 3). _PLAYED is updated as each guess is confirmed submitted.
    # Seeded from restored state BEFORE replaying (iter-12 (d)): after a
    # reboot/restore, already-submitted plan words are skipped, never
    # retyped, and rejected words stay excluded.
    _used_bl, _submitted_bl, _rejected_bl = \
        _betweenle_seed_restored_state(page, gid)
    gs = _guesses(gid, ans)
    try:
        _bank, _ = S.words_betweenle()
        _bank = sorted({str(w).upper() for w in (_bank or [])})
    except Exception:
        _bank = []
    # Readiness loop: the daily-mode click + how-to dismiss must actually land
    # before typing, or every guess goes into the void (rows stayed 0 on CI).
    for _rd in range(6):
        # NO Escape: Flutter treats it as BACK out of the daily board into
        # the menu (that is why every guess typed into the void). Close the
        # overlay panels by id, click the canvas centre, then force focus on
        # Flutter's hidden WORD input so the keyboard reaches the board.
        try:
            page.evaluate(
                "() => { for (const id of ['tipspanel-close', "
                "'statisticspanel-close']) { const e = "
                "document.getElementById(id); if (e) e.click(); } }")
            try:
                _p = page.query_selector("#helppanel-pagination-play")
                if _p and _p.is_visible():
                    _p.click(timeout=1500)
            except Exception:
                pass
            page.wait_for_timeout(300)
            _cv = page.evaluate(
                "() => { const e = document.querySelector("
                "'flt-glass-pane, canvas, flt-scene-host'); if (!e) return null;"
                "const r = e.getBoundingClientRect();"
                "return {x: r.x + r.width / 2, y: r.y + r.height / 2}; }")
            if _cv:
                page.mouse.click(_cv["x"], _cv["y"])
            else:
                page.mouse.click(960, 600)
            try:
                page.focus("input[placeholder='WORD' i], input[placeholder=WORD]")
            except Exception:
                pass
        except Exception:
            page.mouse.click(960, 600)
        page.wait_for_timeout(600)
        try:
            _st0 = _betweenle_state(page)
            _help = _betweenle_invalid_showing(page)
        except Exception:
            _st0, _help = {}, False
        if _st0.get("enter"):
            _dbg(gid, "on-screen keyboard visible; board ready")
            break
        _dbg(gid, f"readiness {_rd}: rows={_st0.get('rows')} len={_st0.get('len')} "
                  f"help_showing={_help}")
        if _help:
            _close_betweenle_help(page)
            continue
        if (_st0.get("len") or 0) > 0:
            break
        try:  # re-arm the daily mode panel and wait for the board
            page.evaluate("() => { const el = "
                          "document.getElementById('menupanel-mode-daily'); "
                          "if (el) el.click(); }")
            page.wait_for_timeout(3000)
            _close_betweenle_help(page)
        except Exception:
            pass
    _rows_before = 0
    # Words that failed on BUFFER grounds (stale buffer, never judged
    # by the game) are excluded from picks this run but must NEVER
    # poison the rejected set — a never-judged word is not rejected.
    _buffer_failed_bl = set()
    for gi, g in enumerate(gs):
        # Pick excludes used ∪ rejected (iter-12 (c)): a word already
        # attempted or rejected is never re-picked — iter-11 excluded
        # only `used`, so BIKER was re-picked after its stale-banner
        # misclassification and the loop never reached a new word.
        _excluded_bl = set(_used_bl) | set(_rejected_bl) | set(_buffer_failed_bl)
        if str(g).upper() in _excluded_bl:
            _dbg(gid, f"skipping {g!r}: already used/rejected "
                      f"(used ∪ rejected)")
            continue
        _rows_before = (_betweenle_state(page) or {}).get("rows") or 0
        # The game's own GUESS n/14 counter, read fresh before this guess.
        # Submission is verified against THIS counter, never against a
        # stale bounds/board snapshot (rerun #2 logged GATOR as
        # "did not submit" while the WIN popup proved it had).
        _cnt_before = (_betweenle_counters(page) or {}).get("guess_n") or 0
        _bd = _betweenle_bounds(page)
        _dbg(gid, f"bounds before guess {gi + 1}: {_bd}")
        _blo, _bhi = _bd if _bd else (None, None)
        if (_blo or _bhi) and not (
                (_blo or "AAAAA") < str(g).upper() < (_bhi or "ZZZZZ")):
            _alt = _betweenle_pick_excluding(gs[gi:], _bank, _bd,
                                             _excluded_bl, ans)
            _dbg(gid, f"{g!r} outside bounds {_bd}; playing {_alt!r}")
            g = _alt or g
        _used_bl.add(str(g).upper())
        _idle_drift(page)
        # ad iframes/overlays steal window focus (and intercept clicks) on the
        # US ad stack: evict whatever sits on top of the board's typing row,
        # then click the row itself.
        try:
            _top = page.evaluate(
                "() => { const e = (document.elementsFromPoint(960, 500) "
                "|| [])[0]; if (!e) return null; "
                "return {tag: e.tagName, id: e.id || '', "
                "cls: (e.className || '').toString().slice(0, 60)}; }")
            if _top and (_top["tag"] == "IFRAME" or "ad" in _top["id"].lower()
                         or "ad" in _top["cls"].lower()
                         or "slot" in _top["id"].lower()):
                page.evaluate(
                    "() => { const e = (document.elementsFromPoint(960, 500) "
                    "|| [])[0]; if (e && e.id !== 'canvas') "
                    "e.style.setProperty('display', 'none', 'important'); }")
                _dbg(gid, f"evicted interceptor over typing row: {_top}")
        except Exception:
            pass
        try:
            page.evaluate(
                "() => { const a = document.activeElement; "
                "if (a && a !== document.body) a.blur(); " "if (a && a.tagName === 'IFRAME') { try { a.contentWindow.blur(); } " "catch (e) {} } try { window.top.focus(); } catch (e) {} " "window.focus(); }")
        except Exception:
            pass
        page.mouse.click(960, 500)     # the orange typing row on the canvas
        QP.ev_push(960, 500, "click")
        page.wait_for_timeout(300)
        # Clear any stale buffer word BEFORE typing (iter 13): after a
        # rejection the game leaves the word in the row; typing into a
        # full buffer drops the keystrokes and Enter re-submits the
        # stale word (iter-12 VIPER).
        _betweenle_clear_buffer(page, gid)
        _verdict, _cnt1 = _betweenle_attempt_submit(
            page, gid, g, _cnt_before)
        if _verdict == "buffer_failure":
            # The fresh banner belongs to a STALE buffer word, not this
            # guess — this guess was never judged. Clear and type it
            # once more; only if the banner is STILL inconsistent do we
            # give up on this word (as buffer-failed, NOT rejected —
            # a never-judged word must never poison the rejected set,
            # or the answer can be excluded forever as in iter-12).
            _dbg(gid, f"{g!r} buffer_failure: fresh banner inconsistent "
                      f"with this word — clearing and retyping once")
            _betweenle_clear_buffer(page, gid)
            _verdict, _cnt1 = _betweenle_attempt_submit(
                page, gid, g, _cnt_before)
        if _verdict == "buffer_failure":
            _dbg(gid, f"{g!r} still buffer_failure after clear+retype; "
                      f"skipping as buffer-failed (NOT rejected)")
            _buffer_failed_bl.add(str(g).upper())
            continue
        if _verdict == "no_change":
            # No counter move + no FRESH banner: before assuming the
            # Enter was never delivered, check whether this Enter WON
            # the game — the winning guess does not advance the
            # counter (ANALYSIS8 §1), so a bare no_change would mark
            # the ANSWER rejected and lose it from the ledger.
            try:
                _won_now, _won_ev = _strict_win(page, ans)
            except Exception:
                _won_now, _won_ev = False, ""
            if _won_now:
                _dbg(gid, f"{g!r} no counter move but win signal present "
                          f"({_won_ev}) — this Enter won; counting submitted")
                _verdict = "submitted"
        if _verdict == "submitted":
            _submitted_bl.append(str(g).upper())
            _PLAYED[gid] = list(_submitted_bl)
            _dbg(gid, f"{g!r} submitted (game counters GUESS "
                      f"{_cnt1.get('guess_n')}/{_cnt1.get('guess_total')})")
        elif _verdict == "rejected":
            # Fresh rejection banner for THIS guess — never retype it,
            # move to the next candidate at once.
            _dbg(gid, f"{g!r} REJECTED by the game (new banner); "
                      f"never retyping (moving to next candidate)")
            if str(g).upper() not in _rejected_bl:
                _rejected_bl.append(str(g).upper())
            _REJECTED[gid] = list(_rejected_bl)
            continue
        else:
            # No counter move + no FRESH banner: the Enter was never
            # delivered (focus/enter-delivery failure), NOT a refusal.
            # Run the restored multi-mechanism retry chain (iter-12
            # (a)): dismiss+resend → canvas sweep → synthetic key
            # dispatch. Enter-only retries — letters are never retyped.
            # The old code replayed rejected words through resend +
            # key-retype (~38s stuck loop on RATIO, run 39039561115);
            # this chain is Enter-only, so that loop cannot recur.
            _dbg(gid, f"{g!r} no banner change + counters unmoved; "
                      f"submit retry chain (Enter-only, never retype)")
            _submitted_f, _cntf, _mech = \
                _betweenle_submit_retry_chain(page, gid, g, _cnt_before)
            if _submitted_f:
                _submitted_bl.append(str(g).upper())
                _PLAYED[gid] = list(_submitted_bl)
                _dbg(gid, f"{g!r} confirmed submitted by game counters "
                          f"via {_mech} (GUESS {_cntf.get('guess_n')}/"
                          f"{_cntf.get('guess_total')})")
            else:
                # Terminal: Enter was delivered three ways (incl.
                # synthetic dispatch) and the counters still never
                # moved — the game received it and refused it. Treat
                # as rejected, never retype, move on.
                _dbg(gid, f"{g!r} still not submitted after full retry "
                          f"chain (game counters: GUESS "
                          f"{_cntf.get('guess_n')}/{_cntf.get('guess_total')}, "
                          f"was {_cnt_before}); classifying rejected, "
                          f"never retyping")
                if str(g).upper() not in _rejected_bl:
                    _rejected_bl.append(str(g).upper())
                _REJECTED[gid] = list(_rejected_bl)
                continue
        try:
            _st = _betweenle_state(page)
            _cntf2 = _betweenle_counters(page) or {}
            _dbg(gid, f"guess {gi + 1}/{len(gs)} {g!r}: rows={_st.get('rows')} "
                      f"GUESS={_cntf2.get('guess_n')}/{_cntf2.get('guess_total')} "
                      f"stats={_cntf2.get('stats_word')!r} "
                      f"len={_st.get('len')} invalid={_betweenle_invalid_showing(page)}")
            if not _st.get("enter"):
                # on-screen keyboard not visible yet: one canvas-centre click
                _dbg(gid, "on-screen ENTER missing; canvas-centre click")
                try:
                    _cv2 = page.evaluate(
                        "() => { const e = document.querySelector("
                        "'#canvas, canvas'); if (!e) return null;"
                        "const r = e.getBoundingClientRect();"
                        "return {x: r.x + r.width/2, y: r.y + r.height/2}; }")
                    if _cv2:
                        page.mouse.click(_cv2["x"], _cv2["y"])
                        QP.ev_push(_cv2["x"], _cv2["y"], "click")
                except Exception:
                    pass
                page.wait_for_timeout(800)
        except Exception as _e:
            _dbg(gid, f"guess {gi + 1} state read failed: {str(_e)[:60]}")
        # Iter-14 P3: check win after EACH submitted guess and STOP.
        # Iter-13 played one extra guess (HIKER) on the already-won
        # board because the retry chain fired on a post-win no_change
        # and nothing checked the win between guesses. The final
        # _strict_win after the loop still runs (it is the verdict);
        # this break only prevents typing into a won board.
        _won_after, _won_ev = _betweenle_won_after_guess(page, ans)
        if _won_after:
            _dbg(gid, f"board won after {g!r} ({_won_ev}); stopping — "
                      "no further guesses on a won board")
            break
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
        # FIX-LOOP: stats-panel fallback previously returned True even
        # when RATIO/GATOR were logged "rejected by bounds". Require that
        # the answer was actually in the submitted set and rows advanced.
        _final_st = {}
        try:
            _final_st = _betweenle_state(page) or {}
        except Exception:
            _final_st = {}
        if stat and str(ans).lower() in stat.lower() and str(ans).upper() in _submitted_bl and (_final_st.get("rows") or 0) > 0:
            return True, f"stats panel word={stat!r} (submitted, rows={_final_st.get('rows')})"
        if stat and str(ans).lower() in stat.lower():
            _dbg(gid, f"stats panel shows {stat!r} but answer not in submitted set {_submitted_bl} / rows={_final_st.get('rows')}; NOT claiming solved")
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


# Live-day probe: hosts / URL markers that can NEVER carry a game's
# served date. Iter-18 (P1, ANALYSIS15 §4 / ROOT CAUSE 4): the probe
# accepted a date from ANY URL, so a DoubleVerify ad URL
# (tpsc-video-ue.doubleverify.com/visit.js?…
# dvtagver=dvot_2026-10-14_…) became Semantle's "served" date
# (2026-10-14) — a tag VERSION date, not Semantle's puzzle date.
# Semantle's own responses carry NO date-bearing URL, so the ad URL
# was the probe's ONLY reading and the single-value fallback in
# _live_day_provenance returned it. Ad / tracker / third-party URLs
# are ignored ENTIRELY here: they are never recorded, so a dvtagver
# date can never reach a re-key decision again.
_LIVE_DAY_AD_HOST_MARKERS = (
    "doubleverify",  # iter-17 vector: dvtagver tag-version date
    "googlesyndication", "doubleclick", "googleadservices",
    "googletagmanager", "google-analytics", "googletagservices",
    "amazon-adsystem", "criteo", "taboola", "outbrain", "moatads",
    "integralads", "scorecardresearch", "quantserve", "adnxs",
    "rubiconproject", "pubmatic", "openx", "smartadserver", "adsrvr",
    "advertising", "analytics", "hotjar", "mixpanel", "segment",
    "fullstory", "newrelic", "cloudflareinsights", "facebook",
    "twitter", "tiktok", "pinterest",
)
_LIVE_DAY_AD_URL_MARKERS = (
    "dvtagver=",  # DoubleVerify tag version date, NOT a puzzle date
    "goog_rewarded",  # GPT rewarded-ad flow marker
    "/ads/", "adservice", "pagead", "gampad", "safeframe",
    "partnerpixels",
)


def _is_live_day_ad_host(host):
    """True when `host` is an ad / tracker / third-party host."""
    try:
        low = (host or "").lower()
        return bool(low) and any(m in low for m in _LIVE_DAY_AD_HOST_MARKERS)
    except Exception:
        return False


def _is_live_day_ad_url(url):
    """True when `url` is an ad / tracker / third-party URL.

    Pure + unit-testable. A game API is never hosted on an ad host
    and never carries an ad-serving marker, so dropping these URLs
    cannot lose a real served-date signal.
    """
    try:
        low = (url or "").lower()
        if not low:
            return False
        host = low.split("/")[2] if "//" in low else low
        if _is_live_day_ad_host(host):
            return True
        return any(m in low for m in _LIVE_DAY_AD_URL_MARKERS)
    except Exception:
        return False


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
            # Iter-18: ad / tracker URLs are ignored ENTIRELY, before
            # any date is extracted — a dvtagver date must never be
            # recorded as a served date (see _LIVE_DAY_AD_* above).
            if _is_live_day_ad_url(url):
                return
            m = re.search(r"(\d{4}-\d{2}-\d{2})", url)
            if not m:
                # countryle's hidden-api keys the board by DD/MM/YYYY (server
                # date, not the faked client clock) - read that too or the
                # served-day align can never see a mismatch for this game.
                m2 = re.search(r"date=(\d{2})%2F(\d{2})%2F(\d{4})", url)
                if not m2:
                    m2 = re.search(r"date=(\d{2})/(\d{2})/(\d{4})", url)
                if m2:
                    iso = f"{m2.group(3)}-{m2.group(2)}-{m2.group(1)}"
                    host = url.split("/")[2] if "//" in url else ""
                    seen = _LIVE_DAY.setdefault("hosts", {})
                    if host not in seen:
                        seen[host] = iso
                        _LIVE_DAY.setdefault("urls", {})[host] = url
                return
            iso = m.group(1)
            host = url.split("/")[2] if "//" in url else url
            try:
                if resp.status == 404 and "globle-game.com/answer" in url:
                    _LIVE_DAY["globle_404"] = True
            except Exception:
                pass
            # Keep the FIRST date each host asked for; later calls are usually
            # history/stats fetches for neighbouring days.
            # Iter-15: also keep the FIRST URL per host — the provenance
            # behind every live-day reading (ANALYSIS11: the Semantle
            # 10-14 reading logged neither host nor URL, so the racy
            # probe reading could not be traced to a request).
            seen = _LIVE_DAY.setdefault("hosts", {})
            if host not in seen:
                seen[host] = iso
                _LIVE_DAY.setdefault("urls", {})[host] = url
        except Exception:
            pass

    try:
        page.on("response", _on_response)
    except Exception:
        pass


def _live_day_provenance(gid):
    """(iso, host, url) behind the live-day reading for `gid`.

    Iter-15: the probe keeps the FIRST date per host (later calls are
    usually history/stats fetches), but until now the reading logged
    neither host nor URL — when Semantle's probe read 2026-10-14 on a
    page serving Game #1722 (Oct-18), nothing in the artifact said
    WHICH request produced the reading. Every re-key decision now
    logs this triple. Returns (None, None, None) when no reading.
    """
    hosts = _LIVE_DAY.get("hosts") or {}
    urls = _LIVE_DAY.get("urls") or {}
    if not hosts:
        iso = _LIVE_DAY.get("date")
        return (iso, None, None) if iso else (None, None, None)
    # Iter-18 (defense in depth): drop any ad / tracker host that
    # slipped in (e.g. recorded before the capture filter, or seeded
    # directly) — it must never be returned as a served-date reading.
    hosts = {h: iso for h, iso in hosts.items()
             if not _is_live_day_ad_host(h)}
    if not hosts:
        return None, None, None
    hints = {
        "searchle": ("searchle",),
        "semantle": ("semantle",),
        "phoodle": ("phoodle",),
        "countryle": ("countryle",),
    }.get(gid, ())
    for host, iso in hosts.items():
        if any(h in host.lower() for h in hints):
            return iso, host, urls.get(host)
    if hints:
        # Iter-18: a game with a known first-party host hint gets NO
        # reading when no hint host was recorded. The old single-value
        # fallback returned whatever lone host existed — which is how
        # a lone DoubleVerify URL became Semantle's "served" date
        # (Semantle's own responses carry no date-bearing URL). No
        # hint host => "probe captured no date", keep the target.
        return None, None, None
    vals = set(hosts.values())
    if len(vals) == 1:
        iso = vals.pop()
        for host, v in hosts.items():
            if v == iso:
                return iso, host, urls.get(host)
    return None, None, None


def _live_day_answer(gid, url=""):
    """The date the site actually served, deduced from what it asked its API."""
    iso, _host, _url = _live_day_provenance(gid)
    return iso


def _live_day_displayed_puzzle_number(page):
    """The site's own displayed puzzle number, e.g. Semantle's header
    "Game #1722", or None when the page shows none / unreadable.

    Iter-15 cross-check: the displayed number is the site's own day
    claim. A probe reading that contradicts it is a probe failure,
    not a re-key. Pure probe (no clicks)."""
    try:
        txt = page.evaluate("() => document.body.innerText") or ""
    except Exception:
        return None
    m = re.search(r"Game\s*#\s*(\d{1,6})", str(txt))
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


# Semantle's day key: puzzle #N is served on 2022-01-30 + N days.
# answers.semantle() uses the same epoch (idx = days since
# 2022-01-30, puzzle == idx), and the site's own header prints
# "Game #1722" for 2026-10-18 — verified: 2022-01-30 + 1722 days
# == 2026-10-18. The displayed number is therefore a REAL live-day
# signal from the game host's own page state (not a URL, not an ad).
_SEMANTLE_EPOCH = date(2022, 1, 30)


def _semantle_date_from_puzzle_number(n):
    """Game #N -> the date Semantle serves it, or None if unreadable.

    Pure + unit-testable. The mapping is the site's own: puzzle #N
    is the (2022-01-30 + N)-th day's puzzle.
    """
    try:
        n = int(n)
    except (TypeError, ValueError):
        return None
    if n < 0:
        return None
    return _SEMANTLE_EPOCH + timedelta(days=n)


def _align_answer_to_live_day(gid, ans, page=None):
    """Re-resolve the answer for the day the board is REALLY serving.

    The local engine's maths is right but it keys off FAKE_DATE_ISO, while the
    site derives its puzzle from the browser's own clock. When those disagree
    (a UTC+14 timezone just after local midnight rolls the site to the NEXT
    day) the entire guess sequence is for the wrong puzzle and the game cannot
    be won. Re-running the SAME engine for the date the site asked for fixes it
    without inventing a second answer source.

    Iter-15 (P0, ANALYSIS11 Semantle REGRESSION): the re-key had NO
    plausibility bound and NO provenance — a racy probe reading
    (first-date-per-host, where a history/archive fetch for a
    neighbouring day can arrive first) re-keyed Oct-18 -> Oct-14
    (cry -> suit) and played suit on the Oct-18 board (site header:
    Game #1722), losing a won game. Now: (1) the live-day host + URL
    is logged on EVERY re-key decision; (2) the re-key is BOUNDED to
    ±1 day of the target — a larger jump is a probe failure: keep
    the target, flag, do NOT swap the answer; (3) the site's own
    displayed puzzle number, when present, cross-checks the reading
    (Game #1722 = target-day puzzle contradicts a 10-14 reading).
    The ±1 bound preserves the legitimate tgt-1 defer (site has not
    rolled yet): a served==target-1 reading still re-keys, and the
    caller defers on it.

    Iter-17 (P1, ANALYSIS13 §5): ROOT CAUSE of the missing line —
    the iter-16 "log the decision even when the probe captures
    nothing" fix used bare print(), which goes to the CI console
    stdout ONLY. The artifact debug log
    (videos/{gid}/debug_<date>.log, the file ANALYSIS13 grepped
    for "live-day") is written ONLY by _dbg(); print() never lands
    there. Worse, the iter-16 test asserted on captured stdout, so
    it PASSED while the artifact stayed silent — the test verified
    the wrong channel. Every decision line below now goes through
    _dbg() (which ALSO prints to stdout, so the old stdout test
    still passes), and every previously-silent early return logs
    its reason. The caller must no longer swallow this function's
    exceptions with a bare `except: pass` either (see run_game).
    """
    try:
        iso, _host, _url = _live_day_provenance(gid)
    except Exception:
        iso, _host, _url = None, None, None
    if not iso:
        # Iter-19 (P1, ANALYSIS16 §6 item 9 / §2(i)): the URL probe
        # captures NOTHING on semantle.com — Semantle's own responses
        # carry no date-bearing URL — so this branch logged "no
        # live-day signal" and the solve rested on the target answer
        # alone. But Semantle DOES carry a real live-day signal: the
        # site's own displayed header "Game #N", which maps to a date
        # via the game's own epoch (see _semantle_date_from_puzzle_
        # number). It is page state from the game host, never an ad
        # URL, so the ad-host filter cannot pollute it. When the URL
        # probe is empty, consult the displayed number and let the
        # SAME ±1 bound + re-key logic below judge it. When neither
        # source yields a date, say so honestly — do NOT fake one.
        if gid == "semantle" and page is not None:
            _shown = None
            try:
                _shown = _live_day_displayed_puzzle_number(page)
            except Exception:
                _shown = None
            if _shown is not None:
                _served = _semantle_date_from_puzzle_number(_shown)
                if _served is not None:
                    _dbg(gid, f"live-day decision: no URL date; using "
                          f"displayed Game #{_shown} -> served={_served} "
                          f"(page state, game host=semantle.com)")
                    iso = _served.isoformat()
                    _host, _url = "semantle.com", None
                else:
                    _dbg(gid, f"live-day decision: probe captured no "
                          f"date (no live-day signal: no URL date and "
                          f"displayed Game #{_shown} maps to no date), "
                          f"keeping target answer; host={_host} url={_url}")
                    return ans, None
            else:
                _dbg(gid, f"live-day decision: probe captured no date "
                      f"(no live-day signal: no URL date and no "
                      f"displayed Game #), keeping target answer; "
                      f"host={_host} url={_url}")
                return ans, None
        else:
            # Iter-16 (P1, ANALYSIS12 §1): log the decision EVEN when
            # the probe captures nothing — iter-15 returned silently
            # here, so the artifact had zero live-day lines and the ±1
            # bound was never exercised on paper. The bound behaviour
            # is unchanged. Iter-17: via _dbg, not print — print()
            # never reaches the artifact debug log (see docstring).
            _dbg(gid, f"live-day decision: probe captured no date "
                  f"(no live-day signal), keeping target answer; "
                  f"host={_host} url={_url}")
            return ans, None
    try:
        want = date(*(int(x) for x in iso.split("-")))
        have = A.target_date()
    except Exception as _e:
        _dbg(gid, f"live-day decision: probe date {iso!r} unparseable "
              f"({str(_e)[:80]}), keeping target answer; "
              f"host={_host} url={_url}")
        return ans, None
    _dbg(gid, f"live-day decision: probe served={want} target={have} "
          f"host={_host} url={_url}")
    if want == have:
        return ans, None
    if abs((want - have).days) > 1:
        # Probe failure, not a re-key: a real day-key disagreement is
        # at most one day (clock/timezone rollover). A 4-day jump is
        # the probe reading a history/archive/stats fetch. Keep the
        # target answer, flag, do NOT swap.
        _dbg(gid, f"live-day RE-KEY REFUSED: probe served={want} is "
              f"{(want - have).days} days from target {have} (>1 day) — "
              f"probe failure, keeping target answer; host={_host} "
              f"url={_url}")
        return ans, None
    fn = getattr(A, gid, None)
    if fn is None:
        _dbg(gid, f"live-day decision: no engine for {gid!r}, "
              f"keeping target answer (probe served={want}); "
              f"host={_host} url={_url}")
        return ans, None
    try:
        fixed = fn(want)
    except Exception as e:
        _dbg(gid, f"live-day re-resolve failed: {str(e)[:100]}")
        return ans, None
    new = (fixed or {}).get("answer") or (fixed or {}).get("name")
    if not new:
        _dbg(gid, f"live-day decision: re-resolve for served={want} "
              f"returned no answer, keeping target answer; "
              f"host={_host} url={_url}")
        return ans, None
    # Displayed-puzzle cross-check: the site's own header number is
    # its day claim. If it agrees with the TARGET day's puzzle (or
    # disagrees with the served day's puzzle), the probe reading is
    # false — keep the target, do NOT swap.
    if page is not None:
        try:
            _shown = _live_day_displayed_puzzle_number(page)
        except Exception:
            _shown = None
        if _shown is not None:
            _served_puzzle = (fixed or {}).get("puzzle")
            _target_puzzle = None
            try:
                _target_puzzle = (ans or {}).get("puzzle")
            except Exception:
                _target_puzzle = None
            if (_served_puzzle is not None and _shown != _served_puzzle) \
                    or (_target_puzzle is not None
                        and _shown == _target_puzzle):
                _dbg(gid, f"live-day RE-KEY REFUSED: site displays "
                      f"Game #{_shown} but probe served={want} "
                      f"(puzzle={_served_puzzle}) and target {have} "
                      f"puzzle={_target_puzzle} — displayed number "
                      f"contradicts the probe reading; keeping target "
                      f"answer; host={_host} url={_url}")
                return ans, None
            _dbg(gid, f"live-day cross-check: site displays "
                  f"Game #{_shown}, consistent with probe served={want} "
                  f"(puzzle={_served_puzzle})")
    _dbg(gid, f"site served {want} but engine answered {have}; "
          f"re-resolved {ans!r} -> {new!r} (host={_host} url={_url})")
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
    """Fresh-state guarantee WITHOUT an on-camera reload.

    The document-start prepurge init script (added to every context) already
    wipes localStorage/sessionStorage/indexedDB/caches BEFORE any site script
    runs, so the board always boots clean. Re-purging in-page here always found
    storage the site re-created during its own boot and therefore reloaded on
    camera every single run (the "opens, then refreshes again" opening you
    kept seeing in canuckle/worgle/phoodle videos). Keep only a diagnostic.
    """
    try:
        n = page.evaluate(
            "() => { let n = 0; try { n += Object.keys(localStorage).length; }"
            " catch (e) {} try { n += Object.keys(sessionStorage).length; }"
            " catch (e) {} return n; }")
        _dbg(gid, f"site state after pre-nav purge: {n} fresh entries "
                  f"(no reload - prepurge already guaranteed a clean boot)")
    except Exception:
        pass
    return True


def _s_wordle(page, ans, gid):
    _close_modals(page)
    try:
        page._qp_gid = gid
    except Exception:
        pass
    if gid == "canuckle":
        try:
            _dismiss_canvas_dialog(page, tries=2)   # HOW TO PLAY, pre-solve
        except Exception:
            pass
        # Flutter only takes keys when the canvas holds focus: one real click
        # (which also feeds the cursor track) + semantics tree up front.
        try:
            page.mouse.click(960, 620)
            QP.ev_push(960, 620, "click")
            page.wait_for_timeout(600)
            _enable_flutter_semantics(page)
        except Exception:
            pass
        # The focus click can (re)open HOW TO PLAY: never let a dialog ride
        # along on camera for the first minute of the video.
        for _dg in range(3):
            try:
                _how = page.query_selector(
                    "text=HOW TO PLAY") or page.query_selector(
                    "[class*=modal i]:has-text('HOW TO PLAY')")
                if not _how or not _how.is_visible():
                    break
                _x = (page.query_selector("[aria-label*=close i]")
                      or page.query_selector("button:has-text('✕')")
                      or page.query_selector("button:has-text('×')")
                      or page.query_selector(
                          "[class*=modal i] button[class*=close i]"))
                if _x and _x.is_visible():
                    _x.click(timeout=2000)
                    QP.ev_push(960, 240, "click")
                else:
                    page.keyboard.press("Escape")
                page.wait_for_timeout(700)
            except Exception:
                break
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
    import time as _time
    _t0 = _time.time()
    _gid = getattr(page, "_qp_gid", "attr")
    name = str(country or "").strip()
    if not name:
        return False, "empty country"
    _dbg(_gid, f"country-guess start {name!r}")
    try:
        inp = None
        for _wq in range(6):      # input can detach during SPA transitions
            inp = scope.query_selector(
                "input[type=text], input:not([type]), input[name=guess], "
                "input[type=search], input[placeholder], textarea, "
                "[contenteditable=true], [role=searchbox], "
                "[role=combobox] input, [role=combobox]")
            if inp:
                break
            page.wait_for_timeout(1500)
        if not inp:
            try:
                n = scope.evaluate(
                    "() => document.querySelectorAll("
                    "'input,textarea,[contenteditable=true]').length")
            except Exception:
                n = "?"
            _dbg(getattr(page, "_qp_gid", "attr"),
                 f"no text input (inputs on page: {n}) url={page.url}")
            return False, "no text input"
        try:
            inp.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            pass
        inp.click(timeout=3000)
        try:
            _bb = inp.bounding_box()
            if _bb:
                QP.ev_push(_bb["x"] + _bb["width"] / 2,
                           _bb["y"] + _bb["height"] / 2, "click")
        except Exception:
            pass
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
            '.react-autosuggest__suggestion, [role=option], [role=listbox] li, ' +
            '.dropdown-menu li, .dropdown-item, ngb-typeahead-window li, ' +
            '.typeahead-dropdown li, ul[class*=suggest i] li, ul[class*=menu i] li, ' +
            '.result-box, #result-id, #player-search .result-box'));
          const vis = rows.filter(e => e && e.offsetParent !== null &&
            !e.closest('nav, footer, header') &&
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
        if picked:
            # The row must actually have landed in the box: batter-up once
            # reported ok=True after clicking the nav's "Select Game".
            try:
                _v = (inp.input_value() or "").strip().lower()
                if _v and name.lower() not in _v:
                    _dbg(_gid, f"country-guess {name!r} FAILED after "
                               f"{_time.time() - _t0:.1f}s (input holds {_v!r})")
                    return False, (f"picked row {detail!r} but input "
                                   f"holds {_v!r}")
            except Exception:
                pass
        _dbg(_gid, f"country-guess {name!r} -> picked={bool(picked)} "
                   f"in {_time.time() - _t0:.1f}s")
        return bool(picked), str(detail or "")
    except Exception as e:
        return False, f"exception: {str(e)[:110]}"


# ---------------- worldle all-modes (fix-loop iter 4) ----------------
# One video, one chapter per played mode: Country first (bonus rounds are
# DISABLED until Country completes — bundle getRoundDisabled:
# `!completed.includes(country) && mode !== country`), then bonus rounds in
# dropdown order, then a Summary end card (not a puzzle).
#
# Static-source grounding (bundled main.js, NOT live-UI observation):
# - Mode ids + dropdown order: rounds array `f.u` (module 72558) and locale
#   commonPhrases.<mode> labels (Country, Neighbours, Capital, Orientation,
#   Flag, Trivia, Landmark, Emblem, Spoken languages, GDP Per Capita,
#   Population & Currency, Populous cities, Area, Summary).
# - Round removal: rounds hook (module 22314) — capital/orientation without
#   a capital city or for micro-states VA/MC/MO/SG/HK/GI; populousCities with
#   <4 populated cities; language unless exactly 2 languages; currency
#   without population; borders+orientation without borders; gdp without
#   gdpPerCapita; emblem/landmark by live image content-type.
# - Win checks: the game's own share-emoji function (module 32445 `_`),
#   recomputed here from the SAME localStorage guess stores the game
#   writes. Never claim a mode solved unless its win check passes.
# - Guess stores (daily, non-archive): country "guesses", neighbours
#   "bonus-guesses", capital "city-guesses", orientation "orientation-round",
#   flag "flag-guesses", trivia "trivia-guesses", landmark
#   "landmark-guesses", emblem "emblem-guesses", language
#   "language-guesses", gdp "gdp-guesses", currency "currency-guesses",
#   populousCities "populous-cities-guesses", size "size-guesses";
#   completion record "rounds-completed" {dayString: [modes]};
#   mode state "game_round" {gameMode, dayString}.
# - Data URLs (module 83512): day games/<y>/<date>.json, countries,
#   cities, facts/<code>.json, landmarks.json, flags/<code>.svg,
#   shields/<code>.svg.
#
# What static source CANNOT prove (round components are lazy chunks):
# the exact per-round input widgets. Every bonus interaction below is
# therefore attempted only when its controls are identifiable at runtime
# (labelled input / option whose text or image URL matches the derived
# answer); otherwise the mode is SKIPPED WITH REASON, never guessed.
# In particular: the Capital pinpoint sub-question (map click) is NEVER
# faked — its map projection is not in static source — and Trivia answers
# come only from the facts DB matched by question text, never guessed.
_WORLDLE_MODE_LABELS = {
    "country": "Country", "neighbours": "Neighbours", "capital": "Capital",
    "orientation": "Orientation", "flag": "Flag", "trivia": "Trivia",
    "landmark": "Landmark", "emblem": "Emblem",
    "spokenLanguages": "Spoken languages", "gdp": "GDP Per Capita",
    "populationCurrency": "Population & Currency",
    "populousCities": "Populous cities", "area": "Area",
    "summary": "Summary",
}
_WORLDLE_PLAY_ORDER = ["country", "neighbours", "capital", "orientation",
                       "flag", "trivia", "landmark", "emblem",
                       "spokenLanguages", "gdp", "populationCurrency",
                       "populousCities", "area"]
_WORLDLE_GUESS_KEYS = ["guesses", "bonus-guesses", "city-guesses",
                       "currency-guesses", "emblem-guesses", "flag-guesses",
                       "gdp-guesses", "landmark-guesses", "language-guesses",
                       "populous-cities-guesses", "size-guesses",
                       "trivia-guesses", "orientation-round"]
_WORLDLE_MODES = []      # per-mode evidence records (see _s_worldle)
_WORLDLE_SEGMENTS = []   # [(gameplay_offset_s, chapter_title)]
_WORLDLE_T0 = None       # solver start (fallback when _VIDEO_T0 unset)


def _s_worldle_norm(s):
    """Bundle normalizer (module 21068 `r`): NFD, strip diacritics, drop
    [- '()], lowercase. Used for name matching exactly as the game does."""
    import unicodedata as _ud
    t = _ud.normalize("NFD", str(s or ""))
    t = "".join(c for c in t if _ud.category(c) != "Mn")
    for ch in ("-", " ", "'", "(", ")"):
        t = t.replace(ch, "")
    return t.lower()


def _s_worldle_strip_emoji(s):
    """Strip leading/trailing emoji/symbol runs (e.g. the round-emoji
    prefix the game renders before a mode label: '📍 Capital',
    '🌍 Country', 'Country ✅'). Letters, digits, '&' and inner spaces
    survive; a bare-emoji string becomes ''. Pure function."""
    import re as _re
    t = str(s or "")
    t = _re.sub(r"^[^\w& ]+", "", t)
    t = _re.sub(r"[^\w& ]+$", "", t)
    return t.strip()


def _s_worldle_label_key(s):
    """Dropdown-label key: emoji-strip, then the bundle normalizer.
    '📍 Capital' and 'Capital' map to the same key. Pure function."""
    return _s_worldle_norm(_s_worldle_strip_emoji(s))


def _s_worldle_cmp_key(s):
    """Comparison-option key: lowercase, whitespace-collapsed (the same
    key form answers.py uses for gdp_lookup / size_lookup). Pure."""
    return " ".join(str(s or "").lower().split())


# Normalized (_s_worldle_label_key form) chrome strings that may appear
# as leaves inside the open round dropdown but are NOT rounds: tab /
# action / banner text. Fix-loop iter 12: expanded from the iter-11
# set (which missed 'give up', 'next round', 'remove ads', 'share',
# 'guess', 'submit', ...) — any of those leaking into the raw list
# used to BLOCK the run as 'unmapped'. The dropdown-scope fix (below)
# is the primary protection; this set is the second line.
_WORLDLE_DROPDOWN_CHROME = {
    "close", "done", "cancel", "back", "menu", "rounds",
    "selectround", "choosearound", "allrounds", "selectaround",
    "chooseround", "round", "giveup", "nextround", "previousround",
    "prevround", "removeads", "play", "playnextround", "share",
    "guess", "submit", "check", "confirm", "continue", "next",
    "save", "skip", "tryagain", "howtoplay", "settings", "sound",
    "language", "new", "gotit", "ok", "learnmore", "teuteufeduishere",
    "teuteufedu", "all",
}


def _s_worldle_ls(page, key):
    """Read one localStorage key as parsed JSON ({} / None on any error)."""
    try:
        v = page.evaluate("(k) => { try { const v = localStorage.getItem(k);"
                          " return v === null ? null : JSON.parse(v); }"
                          " catch (e) { return null; } }", key)
        return v
    except Exception:
        return None


def _s_worldle_day_guesses(page, key, day_string):
    """Guess list for one mode on one dayString ({} -> [] safe)."""
    try:
        d = _s_worldle_ls(page, key) or {}
        v = d.get(day_string)
        return v if isinstance(v, list) else []
    except Exception:
        return []


def _s_worldle_completed(page, day_string):
    try:
        d = _s_worldle_ls(page, "rounds-completed") or {}
        v = d.get(day_string)
        return list(v) if isinstance(v, list) else []
    except Exception:
        return []


def _s_worldle_current_mode(page):
    try:
        d = _s_worldle_ls(page, "game_round") or {}
        return d.get("gameMode")
    except Exception:
        return None


def _s_worldle_clear_storage(page, gid, day_string):
    """Fresh-state clear BEFORE play: game_round, every per-mode guess
    store, removedRounds, and ONLY today's entry of rounds-completed
    (other days' history is left alone). The runner's pre-nav purge
    already clears storage; this is the explicit, verifiable clear the
    all-modes design requires, so stale rounds/guesses cannot leak in."""
    try:
        cleared = page.evaluate("""(ds) => {
          const keys = ["game_round", "removedRounds", "bonus-guesses",
            "city-guesses", "currency-guesses", "emblem-guesses",
            "flag-guesses", "gdp-guesses", "landmark-guesses",
            "language-guesses", "populous-cities-guesses", "size-guesses",
            "trivia-guesses", "orientation-round", "guesses"];
          const out = [];
          for (const k of keys) {
            if (localStorage.getItem(k) !== null) {
              localStorage.removeItem(k); out.push(k);
            }
          }
          try {
            const rc = JSON.parse(
              localStorage.getItem("rounds-completed") || "{}");
            if (Object.prototype.hasOwnProperty.call(rc, ds)) {
              delete rc[ds];
              localStorage.setItem("rounds-completed", JSON.stringify(rc));
              out.push("rounds-completed[" + ds + "]");
            }
          } catch (e) {}
          return out;
        }""", day_string)
        _dbg(gid, f"worldle storage cleared: {cleared}")
        return cleared
    except Exception as e:
        _dbg(gid, f"worldle storage clear failed: {str(e)[:90]}")
        return []


def _s_worldle_segment(mode, label):
    """Record a chapter segment: gameplay offset of this mode's start.
    Recording starts at page creation, ~when _VIDEO_T0 fires on first
    navigation, so offset vs _VIDEO_T0 is accurate to ~1 s."""
    try:
        base = _VIDEO_T0 or _WORLDLE_T0 or time.time()
        off = max(0.0, time.time() - base)
    except Exception:
        off = 0.0
    _WORLDLE_SEGMENTS.append((round(off, 1), f"{label} Solve"))
    return off


def _s_worldle_chapter_plan():
    """Gameplay-relative chapter plan: [(gameplay_offset_s, title)] for
    every played mode. The assembly step shifts these by intro + card
    durations and appends the Summary chapter at gameplay end; see
    _s_worldle_final_chapters."""
    return list(_WORLDLE_SEGMENTS)


def _s_worldle_final_chapters(intro_s, gameplay_s, cards_s=15.0):
    """Full final-video chapter list from the gameplay-relative plan.

    Assumed assembly layout (the generic one): intro, then 5 s recap +
    10 s hints cards (cards_s=15) before gameplay, then the end cards.
    One chapter per played mode, plus Summary at gameplay end. Callers
    with a different card layout pass their own cards_s."""
    out = [(0, "Intro")] if intro_s else [(0, "Today's Worldle puzzle")]
    base = float(intro_s or 0) + float(cards_s)
    for off, title in _WORLDLE_SEGMENTS:
        out.append((round(base + float(off), 1), title))
    out.append((round(base + float(gameplay_s or 0), 1), "Summary"))
    return out


def _s_worldle_win_check(page, mode, day, day_string, extra=None):
    """Recompute the game's OWN completion check for one mode (share-emoji
    function, module 32445) from the game's localStorage guess stores.

    Returns (correct_bool, guesses_summary_list, detail_string).
    `extra` carries play-time facts the store alone cannot show
    (e.g. populousCities expected count parsed from the live prompt)."""
    extra = extra or {}
    code = (day.get("code") or "").upper()
    comp = _s_worldle_completed(page, day_string)
    done = mode in comp
    if mode == "country":
        gs = _s_worldle_day_guesses(page, "guesses", day_string)
        win = any((g or {}).get("distance") == 0 for g in gs)
        return win, [str((g or {}).get("code") or "?") for g in gs], \
            f"distance==0 win={win}; completed={done}"
    if mode == "neighbours":
        gs = _s_worldle_day_guesses(page, "bonus-guesses", day_string)
        borders = {str(b.get("code") or "").upper()
                   for b in (day.get("borders") or [])}
        hit = [str((g or {}).get("code") or "?") for g in gs]
        n_ok = sum(1 for h in hit if h.upper() in borders)
        return n_ok >= len(borders), hit, \
            f"{n_ok}/{len(borders)} border codes guessed; completed={done}"
    if mode == "capital":
        gs = _s_worldle_day_guesses(page, "city-guesses", day_string)
        caps = day.get("capitals") or ([day.get("capital")] if day.get("capital") else [])
        cap_names = {_s_worldle_norm((c or {}).get("name")) for c in caps}
        q0 = [g for g in gs if (g or {}).get("question") == 0]
        name_ok = bool(q0) and _s_worldle_norm(
            (q0[-1] or {}).get("name")) in cap_names
        n_pin = sum(1 for g in gs
                    if "right" in str((g or {}).get("name") or "").lower())
        pin_ok = bool(caps) and n_pin == len(caps)
        win = bool(name_ok and pin_ok)
        return (win, [str((g or {}).get("name") or "?") for g in gs],
                f"name_ok={name_ok} pin_ok={pin_ok} "
                f"(pin {n_pin}/{len(caps)} 'right'); completed={done}")
    if mode == "orientation":
        st = (_s_worldle_ls(page, "orientation-round") or {}).get(day_string) or {}
        win = st.get("directionCorrect") is True \
            and st.get("distanceCorrect") is True
        return (win, [f"dir={st.get('directionCorrect')} "
                      f"dist={st.get('distanceCorrect')}"],
                f"directionCorrect={st.get('directionCorrect')} "
                f"distanceCorrect={st.get('distanceCorrect')}; "
                f"completed={done}")
    if mode in ("flag", "emblem"):
        key = "flag-guesses" if mode == "flag" else "emblem-guesses"
        gs = _s_worldle_day_guesses(page, key, day_string)
        hit = [str((g or {}).get("code") or "?") for g in gs]
        win = any(h.upper() == code for h in hit)
        return win, hit, f"code=={code}: {win}; completed={done}"
    if mode == "trivia":
        gs = _s_worldle_day_guesses(page, "trivia-guesses", day_string)
        per = {}
        for g in gs:
            q = (g or {}).get("question")
            if q in (1, 2, 3):
                per[q] = (g or {}).get("answer") == (g or {}).get("correct")
        n_ok = sum(1 for v in per.values() if v)
        # Game share-emoji rule: >=2 of q1/q2/q3 correct. Solved
        # additionally requires completed to contain trivia (the
        # caller re-checks after a settle when completed lags).
        win = len(per) >= 2 and n_ok >= 2 and done
        return win, [f"q{q}={'ok' if per.get(q) else 'X'}"
                     for q in sorted(per)], \
            f"{n_ok}/{len(per)} questions correct (game rule needs >=2); " \
            f"completed contains trivia={done}"
    if mode == "landmark":
        gs = _s_worldle_day_guesses(page, "landmark-guesses", day_string)
        want = (day.get("landmark") or {}).get("landmarkCode")
        q0 = next((g for g in gs if (g or {}).get("question") == 0), None)
        q1 = next((g for g in gs if (g or {}).get("question") == 1), None)
        ok0 = bool(q0) and (q0 or {}).get("landmarkCode") == want
        ok1 = bool(q1) and (q1 or {}).get("landmarkCode") == want
        return bool(ok0 and ok1), \
            [f"q{q}={((g or {}).get('landmarkCode'))}" for q, g in
             ((0, q0), (1, q1))], \
            f"landmarkCode=={want}: image={ok0} name={ok1}; completed={done}"
    if mode == "spokenLanguages":
        gs = _s_worldle_day_guesses(page, "language-guesses", day_string)
        n_lang = len(day.get("languages") or [])
        c0 = sum(1 for g in gs if (g or {}).get("question") == 0
                 and (g or {}).get("isCorrect"))
        c1 = sum(1 for g in gs if (g or {}).get("question") == 1
                 and (g or {}).get("isCorrect"))
        win = n_lang > 0 and c0 == n_lang and c1 == n_lang
        return win, [f"q0:{c0}/{n_lang} q1:{c1}/{n_lang}"], \
            f"correct counts per question vs {n_lang} languages; " \
            f"completed={done}"
    if mode == "gdp":
        gs = _s_worldle_day_guesses(page, "gdp-guesses", day_string)
        ok0 = any((g or {}).get("question") == 0
                  and (g or {}).get("answer") == (g or {}).get("correct")
                  for g in gs)
        ok1 = any((g or {}).get("question") == 1
                  and (g or {}).get("answer") == (g or {}).get("correct")
                  for g in gs)
        # Game-skipped comparison (iter-13 verdict fix): when the game
        # itself declines the comparison round (not enough neighbours),
        # there is NO q1 entry to satisfy — the round counts as solved
        # if the number is correct. The old check demanded ok1 even
        # when the game had skipped the comparison (iter-12: both tabs
        # both tabs checked, win_check False, GDP undercounted by 1).
        _sk, _skdet = _s_worldle_comparison_skipped(page)
        win = bool(ok0 and (ok1 or _sk))
        return win, \
            [f"q{q}={'ok' if (ok0 if q == 0 else ok1) else 'X'}"
             for q in (0, 1)], \
            f"number={ok0} comparison={ok1} game_skipped={_sk} ({_skdet}); completed={done}"
    if mode == "populationCurrency":
        gs = _s_worldle_day_guesses(page, "currency-guesses", day_string)
        ok1 = any((g or {}).get("question") == 1
                  and (g or {}).get("answer") == (g or {}).get("correct")
                  for g in gs)
        ok2 = any((g or {}).get("question") == 2
                  and (g or {}).get("answer") == (g or {}).get("correct")
                  for g in gs)
        win = bool(ok1 and ok2 and done)
        return win, \
            [f"q{q}={'ok' if (ok1 if q == 1 else ok2) else 'X'}"
             for q in (1, 2)], \
            f"q1={ok1} q2={ok2}; completed contains " \
            f"populationCurrency={done} (solved needs q1+q2+completed)"
    if mode == "populousCities":
        gs = _s_worldle_day_guesses(page, "populous-cities-guesses",
                                    day_string)
        n_exp = extra.get("expected_n")
        if n_exp is None:
            return False, [str((g or {}).get("name") or "?") for g in gs], \
                "expected city count unknown (prompt unreadable); completed=" \
                f"{done}"
        win = len(gs) == n_exp and bool(gs) and all(
            (g or {}).get("isCorrect") for g in gs)
        return win, [str((g or {}).get("name") or "?") for g in gs], \
            f"{len(gs)}/{n_exp} guesses, all isCorrect={win}; " \
            f"completed={done}"
    if mode == "area":
        gs = _s_worldle_day_guesses(page, "size-guesses", day_string)
        # Iter-20 (ANALYSIS17 §2i): NEVER claim "pick recorded"
        # when size-guesses is EMPTY — iter-19 logged
        # "area comparison pick recorded: size-guesses[0]
        # correct==answer: False" while the log itself said
        # nothing was recorded (there is no size-guesses[0]).
        # Empty store -> "no pick recorded (size-guesses empty)".
        # A present-but-wrong entry is worded as an entry present,
        # not as a recorded pick.
        if not gs:
            return False, [], \
                "area comparison: no pick recorded " \
                f"(size-guesses empty); completed={done}"
        win = bool(gs) and (gs[0] or {}).get("correct") == (gs[0] or {}).get("answer")
        # Iter-19 (ANALYSIS16 §6.10): the old wording ("first guess
        # correct==answer") was arbitrate-logic phrasing, not a real
        # guess count — the Area widget is a shape COMPARISON
        # (Kuwait vs Qatar cards), and the recorded store entry is
        # the comparison pick. Word the check as the pick it is.
        if win:
            return win, [str((g or {}).get("answer") or "?") for g in gs], \
                f"area comparison pick recorded: size-guesses[0] " \
                f"correct==answer: {win}; completed={done}"
        return win, [str((g or {}).get("answer") or "?") for g in gs], \
            "area comparison entry present but not correct: " \
            f"size-guesses[0] correct={(gs[0] or {}).get('correct')} " \
            f"answer={(gs[0] or {}).get('answer')}; completed={done}"
    return False, [], f"unknown mode {mode}"



_WORLDLE_STORE_KEYS = {
    "country": "guesses", "neighbours": "bonus-guesses",
    "capital": "city-guesses", "orientation": "orientation-round",
    "flag": "flag-guesses", "trivia": "trivia-guesses",
    "landmark": "landmark-guesses", "emblem": "emblem-guesses",
    "spokenLanguages": "language-guesses", "gdp": "gdp-guesses",
    "populationCurrency": "currency-guesses",
    "populousCities": "populous-cities-guesses", "area": "size-guesses",
}


def _s_worldle_trivia_norm(s):
    """Trivia matching normalizer: fold curly quotes/apostrophes/dashes,
    collapse whitespace, strip blank _______ runs, lowercase."""
    import re as _re
    t = str(s or "")
    for a, b in (("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'),
                 ("–", "-"), ("—", "-"), ("\u00a0", " ")):
        t = t.replace(a, b)
    t = _re.sub(r"_{2,}", " ", t)
    t = _re.sub(r"\s+", " ", t).strip().lower()
    return t


def _s_worldle_trivia_match(db_q, shown_q, extra_stop=()):
    """True when a facts-DB question matches a displayed question:
    normalized equality / containment, or the distinctive substring
    (first 40 + last 20 chars of the DB question) present in the
    displayed text. Blank runs are stripped by _s_worldle_trivia_norm.

    Fix-loop iter 12: token-overlap fallback for paraphrased displayed
    questions (country name inserted, word order changed): >=0.75
    Jaccard over content tokens, with >=2 shared content tokens or
    one shared distinctive token (len>=6). `extra_stop` strips
    caller-known tokens (e.g. the day country's name words) from both
    sides before the overlap, so a displayed question with the
    country name inserted still matches.
    The ANSWER still comes from the DB — matching never invents one."""
    a = _s_worldle_trivia_norm(db_q)
    b = _s_worldle_trivia_norm(shown_q)
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    if len(a) > 60 and a[:40] in b and a[-20:] in b:
        return True
    import re as _re2
    stop = {"the", "a", "an", "is", "are", "was", "were", "has", "have",
            "had", "of", "in", "on", "to", "for", "with", "by", "which",
            "what", "when", "where", "does", "do", "did", "its", "it",
            "and", "or", "about", "known", "country", "following"}
    stop |= {str(x).lower() for x in (extra_stop or ())}
    ta = {t for t in _re2.findall(r"[a-z]+", a) if t not in stop}
    tb = {t for t in _re2.findall(r"[a-z]+", b) if t not in stop}
    if ta and tb:
        inter = ta & tb
        union = ta | tb
        # >=2 shared content tokens, or one long distinctive token:
        # a single shared short token ('true') is not evidence, but
        # two shared tokens at >=0.75 Jaccard is.
        if union and len(inter) / len(union) >= 0.75 \
                and (len(inter) >= 2
                     or any(len(t) >= 6 for t in inter)):
            return True
    return False


def _s_worldle_bool_key_for_statement(text, boolean_facts):
    """Map a displayed trivia statement ('Has had a female head of
    state/government') to its geogrid boolean key ('femaleLeader').

    The FACTS tab shows 4 statements, one TRUE — not True/False buttons,
    so the True/False branch never fires and every FACTS round
    NO-MATCHed. Matching is token-based: each key is split from camelCase
    (+ digit boundaries: top20 -> top, 20); a key word matches when the
    word itself — or one of its synonyms (dst -> daylight/saving/time,
    leader -> head/state/government, ...) — appears in the statement.
    Requires >=0.6 of the key's words to match and a >0.15 margin over
    the runner-up. Returns None when ambiguous — never guess a key."""
    import re as _re
    stop = {"has", "had", "have", "is", "are", "was", "were", "the", "a",
            "an", "in", "of", "or", "and", "with", "its", "it", "does",
            "do", "did", "been", "being", "by", "for", "on", "to", "from",
            "one", "these", "facts", "fact", "about", "which", "number"}
    toks = set(_re.findall(r"[a-z]+|\d+", str(text or "").lower())) - stop
    if not toks:
        return None
    syn = {
        "dst": {"daylight", "saving", "time"},
        "leader": {"head", "state", "government", "female", "woman",
                   "women", "president", "prime", "minister"},
        "heritage": {"world", "heritage", "sites", "unesco"},
        "density": {"population", "density"},
        "population": {"population", "density"},
        "unesco": {"world", "heritage", "sites"},
        "space": {"citizen", "space"},
        "obesity": {"obesity", "rate"},
        "alcohol": {"alcohol", "ban", "consumption"},
        "nuclear": {"nuclear", "weapons"},
        "monarchy": {"monarchy", "king", "queen", "royal"},
        "territory": {"territory"},
        "commonwealth": {"commonwealth"},
        "eurovision": {"eurovision"},
        "nato": {"nato"},
        "apec": {"apec"},
        "eu": {"european", "union"},
        "ussr": {"soviet", "ussr"},
        "empire": {"empire", "roman", "ottoman", "mongol"},
        "metro": {"metro", "subway", "underground"},
        "skyscrapers": {"skyscrapers", "tall", "buildings"},
        "chocolate": {"chocolate", "consumption"},
        "tourism": {"tourism", "rate"},
        "rail": {"rail", "size"},
        "military": {"military", "mandatory"},
        "marriage": {"marriage", "same", "sex", "legal"},
        "activities": {"activities", "same", "sex", "illegal"},
        "agency": {"space", "agency"},
        "streetview": {"google", "street", "view"},
    }
    scored = []
    for key in (boolean_facts or {}):
        words = set()
        for part in _re.findall(r"[A-Z]?[a-z]+|\d+", key):
            words.add(part.lower())
        for w in list(words):
            m = _re.match(r"([a-z]+)(\d+)$", w)
            if m:
                words.discard(w)
                words.update((m.group(1), m.group(2)))
        # Iter-14 P1 (ANALYSIS10 Trivia root cause): the SAME stopword
        # set stripped from the statement tokens must be stripped from
        # the KEY words too. Before this, isMonarchy -> {is, monarchy}
        # scored 1/2 = 0.5 < 0.6 against statement tokens {monarchy}
        # ("is" stripped), so isMonarchy/isTerritory/hasMetro all
        # NO-MATCHed. After stripping: {monarchy} = 1.0.
        words -= stop
        if not words:
            continue
        hits = 0
        for w in words:
            if w in toks or (set(syn.get(w, ())) & toks):
                hits += 1
        scored.append((hits / len(words), key))
    scored.sort(reverse=True)
    if not scored or scored[0][0] < 0.6:
        return None
    if len(scored) > 1 and scored[0][0] - scored[1][0] <= 0.15:
        return None
    return scored[0][1]


def _s_worldle_dom_dump(page, gid, mode, limit=60):
    """Full DOM evidence dump for a skipped/partial/failed mode: every
    input/button/[role=option]/select in the round container (body minus
    nav/header/footer) with tag/type/placeholder/aria-label/name/id/
    text/min/max/step/value/disabled. Logged + returned (truncated)."""
    try:
        rows = page.evaluate("""() => {
          const vis = e => e && e.offsetParent !== null;
          const els = Array.from(document.querySelectorAll(
            'input, button, [role=option], select, textarea, ' +
            '[role=slider], [role=tab], [role=button]'))
            .filter(vis).filter(e => !e.closest('nav, header, footer'));
          return els.slice(0, 80).map(e => ({
            tag: e.tagName, type: e.getAttribute('type') || '',
            placeholder: e.getAttribute('placeholder') || '',
            aria: e.getAttribute('aria-label') || '',
            name: e.getAttribute('name') || '', id: e.id || '',
            text: (e.innerText || e.value || '').slice(0, 90),
            min: e.getAttribute('min') || '',
            max: e.getAttribute('max') || '',
            step: e.getAttribute('step') || '',
            value: (e.value !== undefined ? String(e.value) : ''),
            disabled: !!e.disabled,
            role: e.getAttribute('role') || '',
            cls: (e.className && String(e.className).slice(0, 60)) || ''
          }));
        }""")
    except Exception as e:
        return "dom dump failed: " + str(e)
    parts = []
    for r in (rows or [])[:limit]:
        parts.append(
            str(r.get("tag")) + " type=" + str(r.get("type"))
            + " ph=" + repr(r.get("placeholder"))
            + " aria=" + repr(r.get("aria"))
            + " name=" + str(r.get("name")) + " id=" + str(r.get("id"))
            + " text=" + repr(r.get("text"))
            + " min=" + str(r.get("min")) + " max=" + str(r.get("max"))
            + " step=" + str(r.get("step")) + " value=" + repr(r.get("value"))
            + " disabled=" + str(r.get("disabled"))
            + " role=" + str(r.get("role"))
            + " class=" + str(r.get("cls")))
    out = "DOM dump [" + str(mode) + "] n=" + str(len(rows or [])) + ":\n" \
        + "\n".join(parts)
    _dbg(gid, out[:3000])
    return out


def _s_worldle_store_snapshot(page, mode, day_string):
    """JSON snapshot of one mode's guess store for one day ('' on error).
    Used by the store-change guard: a click/value-set that already
    submitted shows up as a changed snapshot, so Guess is NOT pressed."""
    key = _WORLDLE_STORE_KEYS.get(mode)
    if not key:
        return ""
    try:
        v = _s_worldle_ls(page, key)
        if isinstance(v, dict) and day_string in v:
            v = v.get(day_string)
        import json as _json
        return _json.dumps(v, sort_keys=True, default=str)[:4000]
    except Exception:
        return ""


def _s_worldle_submit_guarded(page, gid, mode, day_string):
    """Universal submit pattern: wait 800ms -> re-read store -> if the
    store grew (the click/value-set already submitted), do NOT press
    Guess; else press the visible enabled Guess/Submit once. Never
    presses Guess twice for the same sub-question. Returns
    'store-grew' | 'submitted' | 'submit-no-change'."""
    before = _s_worldle_store_snapshot(page, mode, day_string or "")
    try:
        page.wait_for_timeout(800)
    except Exception:
        pass
    after = _s_worldle_store_snapshot(page, mode, day_string or "")
    if before != after:
        _dbg(gid, "worldle " + str(mode)
             + ": store grew after interaction; Guess NOT pressed")
        return "store-grew"
    hit = _s_worldle_click_submit(page)
    _settle(page, base=1500)
    after2 = _s_worldle_store_snapshot(page, mode, day_string or "")
    _dbg(gid, "worldle " + str(mode) + ": Guess pressed once (" + str(hit)
         + "); store changed=" + str(after2 != after))
    return "submitted" if after2 != after else "submit-no-change"


def _s_worldle_picked_kind(page):
    """Describe the element marked by _s_worldle_pick_input."""
    try:
        return page.evaluate("""() => {
          const e = document.querySelector('[data-qp-pick="1"]');
          if (!e) return null;
          return {tag: e.tagName, type: e.getAttribute('type') || '',
                  role: e.getAttribute('role') || '',
                  min: e.getAttribute('min') || '',
                  max: e.getAttribute('max') || '',
                  step: e.getAttribute('step') || '',
                  value: String(e.value !== undefined ? e.value : ''),
                  ariaNow: e.getAttribute('aria-valuenow') || ''};
        }""") or {}
    except Exception:
        return {}


def _s_worldle_drive_range(page, gid, target, label):
    """Drive the picked range/slider to `target` (numeric). React-safe:
    native setter + bubbled input/change + verify; else keyboard
    (focus + Home + Arrow keys in batches, verifying every ~50 presses).
    Logs quantization when the step cannot represent the exact value.
    Returns (ok, detail)."""
    try:
        info = page.evaluate("""() => {
          const e = document.querySelector('[data-qp-pick="1"]');
          if (!e) return null;
          return {min: e.getAttribute('min'), max: e.getAttribute('max'),
                  step: e.getAttribute('step'), value: e.value,
                  ariaNow: e.getAttribute('aria-valuenow'),
                  ariaMin: e.getAttribute('aria-valuemin'),
                  ariaMax: e.getAttribute('aria-valuemax')};
        }""") or {}
    except Exception as e:
        return False, "range read failed: " + str(e)[:80]

    def _f(x, default=None):
        try:
            return float(x) if x not in (None, "") else default
        except Exception:
            return default

    lo = _f(info.get("min"), _f(info.get("ariaMin"), 0.0))
    hi = _f(info.get("max"), _f(info.get("ariaMax"), 360.0))
    step = _f(info.get("step"), 1.0) or 1.0
    if lo is None or hi is None:
        return False, "range bounds unreadable: " + str(info)[:120]
    if not (lo - 1e-9 <= target <= hi + 1e-9):
        return False, "target " + str(target) + " outside range [" \
            + str(lo) + "," + str(hi) + "]"
    quant = ""
    if step > 0:
        rem = (target - lo) % step
        if rem > 1e-9 and step - rem > 1e-9:
            quant = (" QUANTIZATION: step " + str(step)
                     + " cannot represent exact " + str(target)
                     + "; judging by win check")
            _dbg(gid, "worldle " + str(label) + ":" + quant)
    tgt = min(hi, max(lo, float(target)))

    def _cur():
        try:
            v = page.evaluate("""() => {
              const e = document.querySelector('[data-qp-pick="1"]');
              if (!e) return null;
              // Prefer the real value: aria-valuenow is written by the
              // native-setter path itself, so reading it first would
              // mask a failed set. Fall back to it only for
              // role=slider divs with no value property.
              const raw = parseFloat(e.value);
              if (!isNaN(raw)) return raw;
              const av = e.getAttribute('aria-valuenow');
              return av !== null && av !== '' ? parseFloat(av) : null;
            }""")
            return None if v is None else float(v)
        except Exception:
            return None

    # 1) native setter + bubbled events + verify.
    try:
        ok_set = page.evaluate("""(v) => {
          const e = document.querySelector('[data-qp-pick="1"]');
          if (!e) return false;
          try {
            const proto = window.HTMLInputElement.prototype;
            const desc = Object.getOwnPropertyDescriptor(proto, 'value');
            if (desc && desc.set) desc.set.call(e, String(v));
            else e.value = String(v);
          } catch (err) { e.value = String(v); }
          e.setAttribute('aria-valuenow', String(v));
          e.dispatchEvent(new Event('input', {bubbles: true}));
          e.dispatchEvent(new Event('change', {bubbles: true}));
          return true;
        }""", tgt)
        page.wait_for_timeout(500)
        cur = _cur()
        if ok_set and cur is not None \
                and abs(cur - tgt) <= max(step, 1e-9):
            return True, "range set via native setter to " + str(cur) + quant
    except Exception as e:
        _dbg(gid, "worldle " + str(label)
             + ": native setter failed: " + str(e)[:80])
    # 2) keyboard path.
    try:
        el = page.query_selector("[data-qp-pick='1']")
        if el:
            try:
                el.scroll_into_view_if_needed(timeout=3000)
            except Exception:
                pass
            el.click(timeout=3000)
        page.wait_for_timeout(300)
        page.keyboard.press("Home")
        page.wait_for_timeout(300)
        presses = int(round((tgt - lo) / step)) if step else 0
        if presses > 2000:
            return False, "keyboard path needs " + str(presses) \
                + " Arrow presses (>2000); native setter unverified" + quant
        done = 0
        while done < presses:
            batch = min(50, presses - done)
            for _ in range(batch):
                page.keyboard.press("ArrowRight")
            done += batch
            page.wait_for_timeout(150)
            cur = _cur()
            if cur is not None and abs(cur - tgt) <= max(step, 1e-9):
                break
        cur = _cur()
        if cur is not None and abs(cur - tgt) <= max(step, 1e-9):
            return True, "range set via keyboard to " + str(cur) + " (" \
                + str(done) + " arrows)" + quant
        return False, "keyboard range ended at " + str(cur) \
            + ", want " + str(tgt) + quant
    except Exception as e:
        return False, "keyboard range failed: " + str(e)[:90] + quant


def _s_worldle_set_picked_value(page, gid, text, label):
    """Set the picked input to `text`: range/slider -> _s_worldle_drive_range,
    anything else -> typed (typing speed UNCHANGED, base_delay=90).
    Returns (ok, detail). Clears the pick marker either way."""
    kind = _s_worldle_picked_kind(page)
    try:
        target = float(str(text).replace(",", ""))
    except Exception:
        target = None
    if (kind.get("type") == "range" or kind.get("role") == "slider") \
            and target is not None:
        ok, detail = _s_worldle_drive_range(page, gid, target, label)
        try:
            page.evaluate("() => { const e = document.querySelector("
                          "'[data-qp-pick=\"1\"]'); if (e) e.removeAttribute("
                          "'data-qp-pick'); }")
        except Exception:
            pass
        return ok, detail
    ok = _s_worldle_type_picked(page, str(text), gid)
    return ok, "typed into text input" if ok else "typing failed"


def _s_worldle_derived_answer(day, mode):
    """Derived answer for one mode (for skipped/partial/failed records)."""
    try:
        if mode == "country":
            return str(day.get("name") or "")
        if mode == "neighbours":
            return ", ".join((b.get("name") or b.get("code") or "")
                             for b in (day.get("borders") or []))
        if mode == "capital":
            return str((day.get("capital") or {}).get("name") or "")
        if mode == "orientation":
            return "bearing+distance from capitals (see orientation log)"
        if mode in ("flag", "emblem"):
            return str(day.get("code") or "")
        if mode == "trivia":
            return "; ".join(str(c) + ": " + str((f or {}).get("answer"))
                             for c, f in (day.get("facts") or {}).items())
        if mode == "landmark":
            lm = day.get("landmark") or {}
            return str(lm.get("name") or lm.get("landmarkCode") or "")
        if mode == "spokenLanguages":
            return ", ".join(str(x.get("languageCode"))
                             for x in (day.get("languages") or []))
        if mode == "gdp":
            g = day.get("gdp") or {}
            return "number=" + str(g.get("number")) + " compare=" \
                + str(g.get("compare_answer"))
        if mode == "populationCurrency":
            cur = day.get("currency") or {}
            return "population=" + str(day.get("population")) \
                + " currency=" + str(cur.get("name")) + " (" \
                + str(cur.get("code")) + ")"
        if mode == "populousCities":
            return ", ".join(str(c.get("name"))
                             for c in (day.get("populous") or []))
        if mode == "area":
            return str(day.get("size") or "")
    except Exception:
        return ""
    return ""


def _s_worldle_visible_options(page):
    """Visible option-ish button texts (body minus nav/header/footer)."""
    try:
        return page.evaluate("""() => {
          const vis = e => e && e.offsetParent !== null;
          return Array.from(document.querySelectorAll(
            'button, [role=option], [role=radio], label'))
            .filter(vis).filter(e => !e.closest('nav, header, footer'))
            .map(e => (e.innerText || '').trim())
            .filter(t => t && t.length < 120);
        }""") or []
    except Exception:
        return []


def _s_worldle_prompt_texts(page):
    """Body lines carrying a '?' (sub-question prompts), in order."""
    try:
        body = (page.evaluate("() => document.body.innerText") or "")
    except Exception:
        body = ""
    import re as _re
    return [s.strip() for s in _re.split(r"\n+", body)
            if "?" in s][:20]


def _s_worldle_click_option_loose(page, want, container=None):
    """Click a visible option whose text equals `want` (normalized) or
    contains it — fallback when exact-match click fails (e.g. option
    text carries a suffix). Returns (ok, detail)."""
    try:
        hit = page.evaluate("""(args) => {
          const want = (args && args.want) || args;
          const container = (args && args.container) || null;
          const inContainer = e => {
            if (!container || !container.rect) return true;
            try {
              const r = e.getBoundingClientRect();
              const cx = r.x + r.width / 2, cy = r.y + r.height / 2;
              const cr = container.rect;
              return cx >= cr.x - 8 && cx <= cr.x + cr.w + 8 &&
                     cy >= cr.y - 8 && cy <= cr.y + cr.h + 8;
            } catch (err) { return false; }
          };
          const vis = e => e && e.offsetParent !== null;
          const norm = s => (s||'').toLowerCase().replace(/\\s+/g,' ').trim();
          const els = Array.from(document.querySelectorAll(
            'button, [role=option], [role=button], [role=menuitem], ' +
            'a, label, li')).filter(vis).filter(
            e => !e.closest('nav, header, footer'))
            .filter(e => { try { const r = e.getBoundingClientRect();
              return !(r.y < 160 && r.width <= 56 && r.height <= 56);
              } catch (err) { return true; } })
            .filter(inContainer);
          const w = norm(want);
          const hit = els.find(e => norm(e.innerText) === w) ||
            els.find(e => norm(e.innerText).includes(w));
          if (!hit) return [false, 'not found'];
          hit.click();
          return [true, (hit.innerText || '').trim().slice(0, 80)];
        }""", {"want": str(want), "container": container})
        return bool(hit and hit[0]), str((hit or [None, ""])[1] or "")
    except Exception as e:
        return False, "exception: " + str(e)[:80]


def _s_worldle_switch_dropdown_only(page, gid, mode, day_string):
    """Dropdown-only switch attempt (NO game_round+reload fallback).
    Returns True iff game_round == mode after the dropdown click.
    Used by the offered-set rule: a mode whose dropdown switch fails
    AND which is absent from the live round list is VERIFIED ABSENT
    and must not be rescued by the reload fallback.

    Iter-22 (ANALYSIS19 §3a): for Area, the store alone is NOT
    enough — the iter-21 visible-round check lived ONLY in
    _s_worldle_switch_mode, which the main loop never calls when
    this function returns True, so attempt 1 "switched" to Area
    while the SPA still showed PopulationCurrency (ZERO
    `worldle switch area` lines). This function now runs the
    visible-round verification on the NORMAL switch path too:
    store == area + visible Area UI NOT verified -> False
    (with a logged reason), so the main loop falls through to
    _s_worldle_switch_mode (Next-round recovery + reload
    fallback). probe_ran=False (probe never executed, legacy
    fakes) falls back to the store check, never blocks."""
    label = _WORLDLE_MODE_LABELS.get(mode, mode)

    def _area_visible_ok():
        # Returns True / False / None (probe never ran).
        _vis = _s_worldle_visible_area_ui(page)
        if not _vis.get("probe_ran"):
            return None
        if not _vis.get("visible"):
            _dbg(gid, "worldle switch area (dropdown-only): "
                      "store says area but visible-round gate "
                      "failed; NOT claimed "
                      f"(reason={_vis.get('reason')})")
            return False
        return True

    if mode != "summary" and _s_worldle_current_mode(page) == mode:
        if mode == "area":
            _ok = _area_visible_ok()
            # None = probe never ran -> store-only (legacy).
            return True if _ok is not False else False
        return True
    try:
        labels = list(_WORLDLE_MODE_LABELS.values())
        opened = page.evaluate("""(labels) => {
          const vis = e => e && e.offsetParent !== null;
          const norm = s => (s||'').toLowerCase().replace(/\\s+/g,' ').trim();
          const cands = Array.from(document.querySelectorAll(
            'button, [role=button], [aria-expanded], [aria-haspopup]'))
            .filter(vis);
          for (const el of cands) {
            const t = norm(el.innerText);
            if (labels.some(l => t.includes(norm(l))) &&
                (el.querySelector('svg') ||
                 el.getAttribute('aria-expanded') !== null ||
                 el.getAttribute('aria-haspopup'))) {
              el.click(); return true;
            }
          }
          return false;
        }""", labels)
        if opened:
            page.wait_for_timeout(700)
            clicked = page.evaluate("""(label) => {
              const vis = e => e && e.offsetParent !== null;
              const norm = s => (s||'').toLowerCase().replace(/\\s+/g,' ').trim();
              const els = Array.from(document.querySelectorAll(
                'button, [role=option], [role=menuitem], ' +
                '[role=menuitemradio], a, li')).filter(vis)
                .filter(e => norm(e.innerText) === norm(label));
              const tier = [
                e => (e.getAttribute('role') || '').includes('option') ||
                     (e.getAttribute('role') || '').includes('menuitem'),
                e => !!e.closest('[role=listbox], [role=menu], ' +
                                 '[aria-expanded=true]'),
                e => e.tagName === 'BUTTON'
              ];
              for (const t of tier) {
                const hit = els.find(t);
                if (hit) { hit.click(); return true; }
              }
              return false;
            }""", label)
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
            if clicked:
                _settle(page, base=1200)
                _close_modals(page, _force=True)
                _settle(page, base=1200)
                if _s_worldle_current_mode(page) == mode:
                    if mode == "area" and \
                            _area_visible_ok() is False:
                        return False
                    return True
                _settle(page, base=1500)
                if _s_worldle_current_mode(page) == mode:
                    if mode == "area" and \
                            _area_visible_ok() is False:
                        return False
                    return True
    except Exception as e:
        _dbg(gid, "worldle switch " + str(mode)
             + ": dropdown-only failed: " + str(e)[:90])
    if _s_worldle_current_mode(page) == mode:
        if mode == "area":
            _ok = _area_visible_ok()
            return True if _ok is not False else False
        return True
    return False


# ---- runtime interaction helpers (all defensive: no identifiable control
# ---- -> return False so the caller skips with reason; never click blind) ----
def _s_worldle_click_submit(page, scope_text=None):
    """Click the round's submit control (Guess/Submit/Check/Confirm), or
    press Enter when no submit button exists. Returns the clicked label
    or 'enter' / None."""
    try:
        if scope_text:
            hit = page.evaluate("""(t) => {
              const vis = e => e && e.offsetParent !== null;
              const norm = s => (s||'').toLowerCase().replace(/\\s+/g,' ').trim();
              const holder = Array.from(document.querySelectorAll(
                'section, div, form, fieldset')).filter(vis).find(
                e => norm(e.innerText).includes(norm(t)) &&
                     e.querySelector('button'));
              if (!holder) return null;
              const btns = Array.from(holder.querySelectorAll('button'))
                .filter(vis);
              const want = ['submit','check','guess','confirm','continue',
                            'next','save'];
              for (const w of want) {
                const b = btns.find(x => norm(x.innerText) === w);
                if (b) { b.click(); return w; }
              }
              return null;
            }""", scope_text)
            if hit:
                return hit
        hit = page.evaluate("""() => {
          const vis = e => e && e.offsetParent !== null;
          const norm = s => (s||'').toLowerCase().replace(/\\s+/g,' ').trim();
          const btns = Array.from(document.querySelectorAll('button'))
            .filter(vis).filter(b => !b.closest('nav, header, footer'));
          const want = ['submit','check','guess','confirm','continue',
                        'next','save'];
          for (const w of want) {
            const b = btns.find(x => norm(x.innerText) === w);
            if (b) { b.click(); return w; }
          }
          return null;
        }""")
        if hit:
            return hit
        try:
            page.keyboard.press("Enter")
        except Exception:
            pass
        return "enter"
    except Exception:
        return None


def _s_worldle_pick_input(page, keywords, include_range=False):
    """Mark the best-matching visible text/number input for `keywords`
    (placeholder / aria-label / name / id / nearby label text) with a
    temp attribute. Returns True when one was marked.
    include_range=True also matches input[type=range] sliders (GDP /
    Area / Population number widgets may be sliders, not text boxes);
    default False preserves the old text-only behaviour."""
    try:
        return bool(page.evaluate("""(args) => {
          const kws = args[0], includeRange = args[1];
          const vis = e => e && e.offsetParent !== null;
          const norm = s => (s||'').toLowerCase();
          const inputs = Array.from(document.querySelectorAll(
            'input, textarea')).filter(vis).filter(
            e => !e.closest('nav, header, footer'))
            // Iter-16 (Area): exclude ad containers — an input
            // inside an ad unit (GetAdmiral / adngin / Snigel /
            // google ads) must never be picked as the game input
            // (the Area number path would type into an ad).
            .filter(e => !e.closest('#_1SS2i7sB, '
              + '[id*=GetAdmiral i], [class*=admiral i], '
              + '[id*=getadmiral i], [id*=adngin i], '
              + '[class*=adngin i], [id*=advertisement i], '
              + '[class*=advertisement i], [id*=google_ads i], '
              + '[class*=adsbygoogle i], [id*=snigel i], '
              + '[class*=snigel i]'));
          let best = null, bestScore = 0;
          for (const inp of inputs) {
            if (inp.type === 'hidden' || inp.type === 'checkbox' ||
                inp.type === 'radio' || inp.type === 'submit' ||
                inp.type === 'button') continue;
            if (inp.type === 'range' && !includeRange) continue;
            let txt = [inp.placeholder, inp.getAttribute('aria-label'),
                       inp.name, inp.id].map(x => norm(x || '')).join(' ');
            const lab = inp.labels && inp.labels[0] ?
              norm(inp.labels[0].innerText || '') : '';
            txt += ' ' + lab;
            const host = inp.closest('label, div, section, form');
            if (host) txt += ' ' + norm(
              (host.innerText || '').slice(0, 300));
            let score = 0;
            for (const k of kws) if (txt.includes(norm(k))) score += 1;
            if (score > bestScore) { bestScore = score; best = inp; }
          }
          if (best && bestScore > 0) {
            best.setAttribute('data-qp-pick', '1');
            return true;
          }
          return false;
        }""", [[str(k).lower() for k in keywords], bool(include_range)]))
    except Exception:
        return False


def _s_worldle_type_picked(page, text, gid):
    """Type into the input marked by _s_worldle_pick_input (same typing
    speed as everywhere: _type_like_a_person base_delay=90 — UNCHANGED).
    Returns True when text landed in the box."""
    try:
        inp = page.query_selector("[data-qp-pick='1']")
        if not inp:
            return False
        try:
            inp.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            pass
        inp.click(timeout=3000)
        page.wait_for_timeout(400)
        try:
            page.keyboard.press("ControlOrMeta+a")
        except Exception:
            pass
        page.wait_for_timeout(200)
        _type_like_a_person(page, str(text), base_delay=90)
        page.wait_for_timeout(400)
        try:
            v = (inp.input_value() or "").strip()
        except Exception:
            v = ""
        try:
            page.evaluate("() => { const e = document.querySelector("
                          "'[data-qp-pick=\"1\"]'); if (e) e.removeAttribute("
                          "'data-qp-pick'); }")
        except Exception:
            pass
        return bool(v) and str(text).strip()[:12].lower() in v.lower()
    except Exception:
        return False


def _s_worldle_click_option_text(page, want):
    """Click a visible option button whose own text exactly equals `want`
    (normalized). Never clicks a near-match: a wrong option is a wrong
    guess, so exact-match-or-nothing."""
    try:
        hit = page.evaluate("""(want) => {
          const vis = e => e && e.offsetParent !== null;
          const norm = s => (s||'').toLowerCase().replace(/\\s+/g,' ').trim();
          const els = Array.from(document.querySelectorAll(
            'button, [role=option], [role=button], [role=menuitem], ' +
            'a, label, li')).filter(vis).filter(
            e => !e.closest('nav, header, footer'));
          const hit = els.find(e => norm(e.innerText) === norm(want) ||
            norm(e.getAttribute('aria-label') || '') === norm(want) ||
            norm(e.getAttribute('data-value') || '') === norm(want));
          if (!hit) return [false, 'not found'];
          hit.click();
          return [true, (hit.innerText || '').trim().slice(0, 80)];
        }""", str(want))
        return bool(hit and hit[0]), str((hit or [None, ""])[1] or "")
    except Exception as e:
        return False, f"exception: {str(e)[:80]}"


def _s_worldle_click_image(page, kind, code):
    """Click the flag/emblem/landmark image for `code`.

    kind 'flags' -> src contains /flags/<code>.svg (bundle Sn builder);
    kind 'shields' -> src contains /shields/<code>.svg (bundle jn builder);
    kind 'landmarks' -> src contains /landmarks/<code>/ (bundle Lr builder).
    Clicks the closest clickable ancestor, else the image itself."""
    try:
        hit = page.evaluate("""(args) => {
          const vis = e => e && e.offsetParent !== null;
          const kind = args[0], code = String(args[1]).toLowerCase();
          const imgs = Array.from(document.querySelectorAll('img'))
            .filter(vis);
          let img = null;
          if (kind === 'landmarks') {
            img = imgs.find(i => (i.src || '').toLowerCase().includes(
              '/landmarks/' + code + '/'));
          } else {
            const sub = '/' + kind + '/' + code + '.svg';
            img = imgs.find(i => (i.src || '').toLowerCase().includes(sub));
          }
          if (!img) return [false, 'no matching image'];
          const t = img.closest(
            'button, [role=button], [role=option], a, label') || img;
          t.click();
          return [true, (img.src || '').slice(-70)];
        }""", [kind, str(code)])
        return bool(hit and hit[0]), str((hit or [None, ""])[1] or "")
    except Exception as e:
        return False, f"exception: {str(e)[:80]}"


def _s_worldle_visible_area_ui(page):
    """Visible-round evidence for the Area round (fix-loop iter 21,
    ANALYSIS18 §6.5): the store (`game_round`) is NOT the visible
    round — the iter-19/20 Area switch "succeeded" with
    game_round='area' while the SPA never re-rendered the Area UI
    (frame gp_0342: PopulationCurrency UI, Dinar + Next round), so
    the comparison click landed on the wrong round's DOM and the
    store-verify went 0->0. This probe checks the VISIBLE round:
    an Area prompt actually rendered (comparison "larger surface
    area" / number "surface area of ...", TRULY visible: rect
    intersecting the viewport + opacity > 0 + no hidden ancestor
    + active-panel membership, outside nav/header/footer chrome).
    Iter-22 (ANALYSIS19 §3b): rect > 0 alone passed on a
    PopulationCurrency-visible page because the SPA keeps the
    inactive Area panel mounted with a non-zero rect. The
    selected tab alone NEVER makes the round visible. Returns
    {visible, signals, prompt_visible, selected_tab, prompt_text,
    next_round_visible, container, reason, rejected,
    rejection_reasons, probe_ran}; `container` is the
    verified-visible Area container for the comparison click
    restriction, and `reason` names why the gate failed.
    Returns {visible, signals, prompt_visible, selected_tab,
    prompt_text, next_round_visible, probe_ran}. A non-dict probe
    result (legacy test fakes returning True) maps to visible via
    bool-compat, mirroring the G4 toggle convention; a None
    result means the probe never ran (legacy fakes that do not
    script the new probe — production JS always returns a dict,
    so the gate is enforced on every real run) and is reported
    as probe_ran=False so callers can fall back to the
    comparison-probe evidence instead of blocking on a probe
    that never executed. A failed evaluate is visible=False
    (fail closed — never click a comparison option on an
    unverified round)."""
    try:
        res = page.evaluate("""/* worldle-visible-area */ () => {
          const norm = s => (s || '').toLowerCase()
            .replace(/\\s+/g, ' ').trim();
          const vw = window.innerWidth || 0;
          const vh = window.innerHeight || 0;
          // Iter-22 (ANALYSIS19 §3b): rect > 0 is NOT visible.
          // The SPA keeps the inactive Area panel mounted with a
          // non-zero rect, so the iter-21 gate passed on a
          // PopulationCurrency-visible page. An element is
          // TRULY VISIBLE only when: its rect intersects the
          // viewport, its computed opacity > 0, it is not
          // display:none / visibility:hidden, and NO ancestor
          // is aria-hidden / hidden / display:none / opacity 0.
          // A rejected prompt candidate is COUNTED with its
          // reason (rejected / rejection_reasons) instead of
          // silently passing or silently failing.
          const trulyVisible = el => {
            try {
              const r = el.getBoundingClientRect();
              if (!(r.width > 0 && r.height > 0))
                return {ok: false, why: 'zero-rect'};
              if (r.bottom <= 0 || r.top >= vh ||
                  r.right <= 0 || r.left >= vw)
                return {ok: false, why: 'off-viewport'};
              const cs = getComputedStyle(el);
              if (cs.display === 'none')
                return {ok: false, why: 'display-none'};
              if (cs.visibility === 'hidden' ||
                  cs.visibility === 'collapse')
                return {ok: false, why: 'visibility-hidden'};
              if (parseFloat(cs.opacity || '1') <= 0)
                return {ok: false, why: 'opacity-0'};
              let node = el.parentElement;
              while (node && node.tagName !== 'BODY') {
                if (node.getAttribute &&
                    node.getAttribute('aria-hidden') === 'true')
                  return {ok: false, why: 'aria-hidden-ancestor'};
                if (node.hasAttribute && node.hasAttribute('hidden'))
                  return {ok: false, why: 'hidden-ancestor'};
                try {
                  const ncs = getComputedStyle(node);
                  if (ncs.display === 'none')
                    return {ok: false, why: 'hidden-ancestor'};
                  if (ncs.visibility === 'hidden')
                    return {ok: false, why: 'hidden-ancestor'};
                  if (parseFloat(ncs.opacity || '1') <= 0)
                    return {ok: false, why: 'opacity-0-ancestor'};
                } catch (e) {}
                node = node.parentElement;
              }
              return {ok: true, why: ''};
            } catch (e) {
              return {ok: false, why: 'exception'};
            }
          };
          const signals = [];
          let promptVisible = false, promptText = '', promptEl = null;
          let rejected = 0;
          const rejectionReasons = {};
          for (const el of document.querySelectorAll('body *')) {
            if (el.closest && el.closest('nav, header, footer'))
              continue;
            const t = norm(el.textContent || '');
            if (!(t.includes('larger surface area') ||
                  t.includes('surface area of'))) continue;
            const v = trulyVisible(el);
            if (!v.ok) {
              rejected++;
              rejectionReasons[v.why] =
                (rejectionReasons[v.why] || 0) + 1;
              continue;
            }
            promptVisible = true;
            promptText = t.slice(0, 140);
            promptEl = el;
            signals.push('area-prompt-visible');
            break;
          }
          // Active-panel membership: when the prompt lives in a
          // tabpanel whose id is NOT the selected tab's
          // aria-controls target, the panel is inactive even
          // if every ancestor passes trulyVisible (the SPA can
          // stack panels without hiding attributes).
          let panelActive = true;
          if (promptEl) {
            const panel = promptEl.closest('[role=tabpanel]');
            if (panel && panel.id) {
              const selTab = document.querySelector(
                '[aria-selected="true"]');
              if (selTab) {
                const ctl = selTab.getAttribute('aria-controls');
                if (ctl && ctl !== panel.id) {
                  panelActive = false;
                  signals.push('prompt-in-inactive-panel');
                }
              }
            }
          }
          // Verified-visible Area CONTAINER: the smallest truly
          // visible holder containing THIS prompt element plus
          // >=2 option-like descendants. The comparison click
          // is restricted to this container (iter-22 §3c) — a
          // body-wide or prompt-only container would let the
          // click land on the stale hidden panel again.
          let container = null;
          if (promptEl && panelActive) {
            const holders = Array.from(document.querySelectorAll(
              'section, form, div'))
              .filter(e => trulyVisible(e).ok)
              .filter(e => e.contains(promptEl))
              .filter(e => (norm(e.innerText || '')
                .includes('larger surface area') ||
                norm(e.innerText || '')
                .includes('surface area of')))
              // Iter-25: count silhouette IMAGES as option-like
              // descendants too. The Area widget's cards are image
              // + label-bar, NOT buttons — the old button-only
              // count (>=2) never matched the widget, so the
              // container fell back to a prompt-only wrapper (or
              // None) and the click restriction could not keep
              // the click inside the widget.
              .filter(e => e.querySelectorAll(
                'button, [role=option], [role=radio], ' +
                '[role=button], a, label, [data-value], img').length >= 2);
            holders.sort((a, b) =>
              (a.innerText || '').length - (b.innerText || '').length);
            const picked = holders[0] ||
              promptEl.closest('section, form, div');
            if (picked && picked !== document.body) {
              const r = picked.getBoundingClientRect();
              container = {tag: picked.tagName,
                cls: String(picked.getAttribute('class') || '')
                  .slice(0, 80),
                id: picked.id || '',
                rect: {x: Math.round(r.x), y: Math.round(r.y),
                       w: Math.round(r.width),
                       h: Math.round(r.height)}};
            }
          }
          let selectedTab = '';
          for (const el of document.querySelectorAll(
              '[aria-selected=true], [aria-current=true]')) {
            if (!trulyVisible(el).ok) continue;
            const t = norm(el.textContent || '');
            if (t) { selectedTab = t; break; }
          }
          if (selectedTab.includes('area'))
            signals.push('selected-tab-area');
          let nextRoundVisible = false;
          for (const el of document.querySelectorAll(
              'button, [role=button], a')) {
            if (!trulyVisible(el).ok) continue;
            if (norm(el.textContent || '') === 'next round') {
              nextRoundVisible = true; break;
            }
          }
          if (nextRoundVisible) signals.push('next-round-visible');
          // The selected tab alone NEVER makes the round
          // visible: a selected 'Area' tab with no truly-visible
          // Area prompt is exactly the iter-21 failure shape
          // (tab selected, panel not rendered).
          const visible = promptVisible && panelActive;
          let reason = '';
          if (!visible) {
            reason = 'no truly-visible Area prompt ' +
              '(rejected=' + rejected + ' reasons=' +
              JSON.stringify(rejectionReasons) +
              (panelActive ? '' : '; prompt in inactive panel') +
              "; selected_tab='" + selectedTab + "')";
          }
          return {visible: visible,
                  signals: signals,
                  prompt_visible: promptVisible,
                  prompt_text: promptText,
                  selected_tab: selectedTab,
                  next_round_visible: nextRoundVisible,
                  container: container,
                  reason: reason,
                  rejected: rejected,
                  rejection_reasons: rejectionReasons};
        }""")
    except Exception:
        return {"visible": False, "signals": [],
                "prompt_visible": False, "selected_tab": "",
                "prompt_text": "", "next_round_visible": False,
                "probe_ran": False, "container": None,
                "reason": "evaluate failed",
                "rejected": 0, "rejection_reasons": {},
                "error": "evaluate failed"}
    if isinstance(res, dict):
        return {"visible": bool(res.get("visible")),
                "signals": list(res.get("signals") or []),
                "prompt_visible": bool(res.get("prompt_visible")),
                "selected_tab": str(res.get("selected_tab") or ""),
                "prompt_text": str(res.get("prompt_text") or ""),
                "next_round_visible":
                    bool(res.get("next_round_visible")),
                "probe_ran": True,
                "container": (res.get("container")
                              if isinstance(res.get("container"), dict)
                              else None),
                "reason": str(res.get("reason") or ""),
                "rejected": res.get("rejected") or 0,
                "rejection_reasons": (res.get("rejection_reasons")
                                      or {})}
    if res is True:
        return {"visible": True, "signals": ["bool-compat"],
                "prompt_visible": False, "selected_tab": "",
                "prompt_text": "", "next_round_visible": False,
                "probe_ran": True, "container": None,
                "reason": "bool-compat", "rejected": 0,
                "rejection_reasons": {}}
    return {"visible": False, "signals": ["non-dict-probe-result"],
            "prompt_visible": False, "selected_tab": "",
            "prompt_text": "", "next_round_visible": False,
            "probe_ran": False, "container": None,
            "reason": "non-dict-probe-result", "rejected": 0,
            "rejection_reasons": {}}


def _s_worldle_click_next_round(page):
    """Click the visible 'Next round' advance control (iter-21
    Area visible-round recovery): when the store says Area but
    the visible UI is still a completed earlier round, its
    'Next round' button advances the SPA to the next round's UI
    (the frame gp_0342 UI shows exactly this control). Returns
    True iff such a button was clicked. Pure click — the caller
    re-verifies the visible round afterwards."""
    try:
        return bool(page.evaluate("""/* worldle-next-round-click */
          () => {
          const vis = e => { try { const r = e.getBoundingClientRect();
            return r.width > 0 && r.height > 0; }
            catch (err) { return false; } };
          const norm = s => (s || '').toLowerCase()
            .replace(/\\s+/g, ' ').trim();
          for (const el of document.querySelectorAll(
              'button, [role=button], a')) {
            if (!vis(el)) continue;
            if (norm(el.textContent || '') === 'next round') {
              el.click(); return true;
            }
          }
          return false;
        }"""))
    except Exception:
        return False


def _s_worldle_switch_mode(page, gid, mode, day_string):
    """Switch rounds via the round-nav dropdown click by visible label
    (the dropdown's goToRound calls setGameMode(mode, true); Summary
    navigates to /summary). Fallback: write the same state transition
    the app persists (game_round {gameMode, dayString}) + reload.
    Verified by reading game_round back. Returns True iff the mode
    actually changed."""
    # Iter-16 (ANALYSIS12 §11): re-run the banner dismiss on EVERY
    # mode switch — the SPA re-mounts the banner on navigation, and
    # the old once-per-page _close_modals dedup never re-ran it after
    # the banner rendered. This also covers the already-on-mode
    # early return below, which previously skipped the dismiss.
    _dismiss_teuteuf_banner(page)
    label = _WORLDLE_MODE_LABELS.get(mode, mode)

    def _verify():
        # Summary navigates to /summary instead of setting a gameMode
        # (dropdown goToRound: summary -> navigate("/summary")), so it is
        # verified by URL, not by game_round.
        if mode == "summary":
            try:
                return "summary" in (page.url or "")
            except Exception:
                return False
        if _s_worldle_current_mode(page) != mode:
            return False
        if mode == "area":
            # Iter-21 (ANALYSIS18 §6.5): the store is NOT the
            # visible round. A switch that only reads game_round
            # back "succeeds" while the SPA still shows the
            # previous (completed) round's UI, and the comparison
            # click then lands on the wrong round. The Area switch
            # verifies the VISIBLE round too — an Area prompt
            # rendered, or the selected tab naming Area. The
            # site header is always WORLDLE, so a
            # visible_header='WORLDLE' check is useless and is
            # deliberately NOT used here. probe_ran=False means
            # the probe never executed (legacy fakes that do
            # not script it — production JS always returns a
            # dict): fall back to the store check rather than
            # blocking on a probe that never ran.
            _vis = _s_worldle_visible_area_ui(page)
            if not _vis.get("probe_ran"):
                return True
            return bool(_vis.get("visible"))
        return True

    if _verify():
        return True
    # 1) dropdown click by visible label.
    try:
        labels = list(_WORLDLE_MODE_LABELS.values())
        opened = page.evaluate("""(labels) => {
          const vis = e => e && e.offsetParent !== null;
          const norm = s => (s||'').toLowerCase().replace(/\\s+/g,' ').trim();
          const cands = Array.from(document.querySelectorAll(
            'button, [role=button], [aria-expanded], [aria-haspopup]'))
            .filter(vis);
          for (const el of cands) {
            const t = norm(el.innerText);
            if (labels.some(l => t.includes(norm(l))) &&
                (el.querySelector('svg') ||
                 el.getAttribute('aria-expanded') !== null ||
                 el.getAttribute('aria-haspopup'))) {
              el.click(); return true;
            }
          }
          return false;
        }""", labels)
        if opened:
            page.wait_for_timeout(700)
            # Tiered item match: role option/menuitem first, then items
            # inside a listbox/menu container, then any button with the
            # exact label. (The toggle shows the CURRENT label, never the
            # target's, so an exact-label click cannot hit the toggle.)
            clicked = page.evaluate("""(label) => {
              const vis = e => e && e.offsetParent !== null;
              const norm = s => (s||'').toLowerCase().replace(/\\s+/g,' ').trim();
              const els = Array.from(document.querySelectorAll(
                'button, [role=option], [role=menuitem], ' +
                '[role=menuitemradio], a, li')).filter(vis)
                .filter(e => norm(e.innerText) === norm(label));
              const tier = [
                e => (e.getAttribute('role') || '').includes('option') ||
                     (e.getAttribute('role') || '').includes('menuitem'),
                e => !!e.closest('[role=listbox], [role=menu], ' +
                                 '[aria-expanded=true]'),
                e => e.tagName === 'BUTTON'
              ];
              for (const t of tier) {
                const hit = els.find(t);
                if (hit) { hit.click(); return true; }
              }
              return false;
            }""", label)
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
            if clicked:
                _settle(page, base=1200)
                _close_modals(page, _force=True)
                _settle(page, base=1200)
                if _verify():
                    return True
                if (mode == "area"
                        and _s_worldle_current_mode(page) == "area"):
                    # Iter-21: store says Area but the visible UI
                    # is still the previous (completed) round —
                    # the SPA never re-rendered. Advance with its
                    # own 'Next round' control (up to 2x) and
                    # re-verify the VISIBLE round after each
                    # advance; never return True on the store
                    # alone. If the store itself moves off Area,
                    # stop — the fallback below re-drives the
                    # switch instead of chasing the wrong round.
                    _dbg(gid, "worldle switch area: store says area "
                              "but visible Area UI not showing; "
                              "trying Next round advance")
                    for _nr in (1, 2):
                        if not _s_worldle_click_next_round(page):
                            _dbg(gid, "worldle switch area: no "
                                      "Next round control to click")
                            break
                        _settle(page, base=1500)
                        if _verify():
                            return True
                        if _s_worldle_current_mode(page) != "area":
                            break
                    _dbg(gid, "worldle switch area: Next round "
                              "advance did not render the Area UI")
                # Lifesaver modal can hold the switch as pendingGameMode
                # until it closes; one more settle + re-check.
                _settle(page, base=1500)
                if _verify():
                    return True
    except Exception as e:
        _dbg(gid, f"worldle switch {mode}: dropdown path failed: "
                  f"{str(e)[:90]}")
    # 2) fallback: the persisted state transition + reload (the Ke/Ye
    # hooks restore gameMode from game_round when dayString matches).
    # Summary fallback: direct navigation to <origin>/summary.
    try:
        if mode == "summary":
            try:
                origin = "/".join((page.url or "").split("/")[:3])
                page.goto(origin + "/summary",
                          wait_until="domcontentloaded", timeout=45000)
            except Exception:
                pass
        else:
            page.evaluate("""(args) => { localStorage.setItem('game_round',
              JSON.stringify({gameMode: args[0], dayString: args[1]})); }""",
              [mode, day_string])
            page.reload(wait_until="domcontentloaded")
        try:
            page._qp_modals_closed = False
        except Exception:
            pass
        _close_modals(page, _force=True)
        _settle(page, base=1500)
        if _verify():
            _dbg(gid, f"worldle switch {mode}: via fallback "
                      f"({'nav' if mode == 'summary' else 'game_round+reload'})")
            return True
    except Exception as e:
        _dbg(gid, f"worldle switch {mode}: fallback failed: {str(e)[:90]}")
    _ok = _verify()
    if not _ok and mode == "area":
        # Iter-21: fail honestly — the switch never rendered the
        # Area UI, so the caller must NOT play the comparison
        # into whatever round is actually visible.
        _dbg(gid, "worldle switch area: visible-round gate failed "
                  f"(visible={_s_worldle_visible_area_ui(page)}); "
                  "switch NOT claimed")
    return _ok


def _worldle_g4_snapshot(page):
    """Snapshot the visible innermost-text elements (iter-24,
    ANALYSIS21 RC5): one evaluate returning [{text, x, y, w, h,
    chain}] where chain is the ancestor tag/cls/id list
    (nearest-first, up to 6 levels, jumping shadow-root hosts).
    Light DOM + every shadow root. Used for the toggle-diff:
    the snapshot before a toggle click vs after it names the
    elements the toggle actually opened, whatever the menu's
    markup is. Returns [] on any failure."""
    try:
        return page.evaluate("""/* G4-DOM-SNAPSHOT */ () => {
          const roots = [document];
          const collect = rt => {
            try {
              for (const el of rt.querySelectorAll('*')) {
                if (el.shadowRoot) {
                  roots.push(el.shadowRoot);
                  collect(el.shadowRoot);
                }
              }
            } catch (e) {}
          };
          try { collect(document); } catch (e) {}
          const norm = s => (s || '').replace(/\\s+/g, ' ').trim();
          const vis = e => { try { const r = e.getBoundingClientRect();
            return r.width > 0 && r.height > 0; }
            catch (err) { return false; } };
          const desc = el => { try { return {tag: el.tagName || '',
            cls: String(el.getAttribute('class') || '')
              .split(/\\s+/)[0].slice(0, 60) || '',
            id: el.id || ''}; } catch (e) {
              return {tag: '', cls: '', id: ''}; } };
          const out = [];
          const seen = new Set();
          for (const rt of roots) {
            let els = [];
            try { els = Array.from(rt.querySelectorAll('*')); }
            catch (e) { continue; }
            for (const el of els) {
              if (seen.has(el)) continue;
              seen.add(el);
              if (!vis(el)) continue;
              const txt = norm(
                el.innerText || el.textContent || '');
              if (!txt || txt.length > 80) continue;
              const hasTextChild = Array.from(el.children || [])
                .some(c => norm(
                  c.innerText || c.textContent || ''));
              if (hasTextChild) continue;
              const chain = [];
              let cur = el, guard = 0;
              while (cur && guard < 7) {
                const p = cur.parentElement;
                if (p) { chain.push(desc(p)); cur = p; }
                else {
                  const r = cur.getRootNode ?
                    cur.getRootNode() : null;
                  if (r && r.host) {
                    chain.push(desc(r.host)); cur = r.host;
                  } else break;
                }
                guard++;
              }
              let r;
              try { r = el.getBoundingClientRect(); }
              catch (e) { continue; }
              out.push({text: txt,
                x: Math.round(r.x), y: Math.round(r.y),
                w: Math.round(r.width), h: Math.round(r.height),
                chain: chain.slice(0, 6)});
              if (out.length >= 3000) return out;
            }
          }
          return out;
        }""") or []
    except Exception:
        return []


def _worldle_g4_diff_entries(before, after, labels):
    """Pure: diff two G4 DOM snapshots (iter-24, ANALYSIS21 RC5).

    Returns {entries_new, entries_removed, container_new,
    container_removed, anchored_new, anchored_removed,
    n_new, n_removed, new_label_matches, removed_label_matches}.
    `entries_*` are the raw texts of the newly appeared /
    disappeared elements that share the container of the
    label-matching elements (DOM order, deduped) — the opened
    menu's leaves, including any unmapped/renamed label. A
    container counts only when >=3 label-matching elements
    share one ancestor descriptor (tag/cls/id at any depth);
    the anchored dict names that descriptor + via='toggle-diff'
    (or 'toggle-diff-removed'). No container -> entries empty.
    """
    def _key(e):
        try:
            return (str(e.get("text") or "").strip().lower(),
                    int(e.get("x") or 0), int(e.get("y") or 0),
                    int(e.get("w") or 0), int(e.get("h") or 0))
        except (TypeError, ValueError):
            return None

    def _label_key(text):
        try:
            return _s_worldle_label_key(text)
        except Exception:
            return str(text or "").strip().lower()

    label_keys = {_label_key(l) for l in (labels or [])}
    before = [e for e in (before or []) if isinstance(e, dict)]
    after = [e for e in (after or []) if isinstance(e, dict)]
    before_keys = {_key(e) for e in before}
    after_keys = {_key(e) for e in after}
    new = [e for e in after if _key(e) not in before_keys]
    removed = [e for e in before if _key(e) not in after_keys]

    def _container(elements):
        matches = [e for e in elements
                   if _label_key(e.get("text") or "")
                   in label_keys]
        if len(matches) < 3:
            return [], None, matches
        counts = {}
        order = []
        anc_of = {}
        for e in matches:
            for depth, anc in enumerate(e.get("chain") or []):
                if not isinstance(anc, dict):
                    continue
                k = (depth, str(anc.get("tag") or ""),
                     str(anc.get("cls") or ""),
                     str(anc.get("id") or ""))
                if k not in counts:
                    counts[k] = 0
                    order.append(k)
                    anc_of[k] = anc
                counts[k] += 1
        best = None
        best_count = 0
        for k in order:
            if counts[k] > best_count:
                best, best_count = k, counts[k]
        if best is None or best_count < 3:
            return [], None, matches
        depth, tag, cls, aid = best
        entries = []
        seen = set()
        for e in elements:
            chain = e.get("chain") or []
            hit = any(isinstance(a, dict)
                      and str(a.get("tag") or "") == tag
                      and str(a.get("cls") or "") == cls
                      and str(a.get("id") or "") == aid
                      for a in chain)
            if not hit:
                continue
            t = str(e.get("text") or "").strip()
            if t and t.lower() not in seen:
                seen.add(t.lower())
                entries.append(t)
        anchored = {"tag": tag, "cls": cls, "id": aid,
                    "role": "", "via": "toggle-diff",
                    "score": best_count, "leaves": len(entries)}
        return entries, anchored, matches

    entries_new, anch_new, matches_new = _container(new)
    entries_rem, anch_rem, matches_rem = _container(removed)
    if anch_rem is not None:
        anch_rem = dict(anch_rem, via="toggle-diff-removed")
    return {"entries_new": entries_new,
            "entries_removed": entries_rem,
            "container_new": bool(anch_new),
            "container_removed": bool(anch_rem),
            "anchored_new": anch_new,
            "anchored_removed": anch_rem,
            "n_new": len(new), "n_removed": len(removed),
            "new_label_matches": len(matches_new),
            "removed_label_matches": len(matches_rem)}


def _s_worldle_live_round_modes(page, gid):
    """Read the live round list from the OPEN dropdown.

    Returns {"mapped": set of mode ids, "raw": [all visible dropdown
    entry texts], "unmapped": [raw entries matching no known label and
    no chrome string], "scope": "dropdown"}, or None when the dropdown
    cannot be read. Rounds the game removed for missing data are ABSENT
    here — that is the authoritative availability signal. The RAW list
    is returned too: the old code filtered inside page JS and dropped
    unknown labels before Python ever saw them, so a renamed round
    would be silently lost (fix-loop iter 11).

    Fix-loop iter 12 (G4 BLOCKED false positive): the iter-11 reader
    fell back to a WHOLE-PAGE scan when no menu container was found,
    so page chrome / banner / widget text ('Give up', 'Teuteuf Edu is
    here!', ...) entered the raw list as 'unmapped' and BLOCKED the
    run. The probe is now scoped to the OPEN dropdown container ONLY:
    no identifiable open container => None (unreadable, no block),
    never a page-wide scan. Mapping strips emoji prefixes ('📍
    Capital' == 'Capital') and the chrome set is expanded. The reader
    itself logs the RAW entry texts (iter-11 carry-forward: the
    TOTAL=13 reconciliation must be auditable from the log), so the
    caller does not have to re-log them.

    Fix-loop iter 18 (ANALYSIS15 §11): the reader STILL returns
    None ("no open dropdown container found (leaves < 3)"), so
    the 12-offered count is reconstructed, not read live. Root
    cause: the leaf filter `e.children.length === 0` excludes
    EVERY real entry — the switcher renders each entry as a
    wrapper (button/li) holding an emoji span + a label span, so
    no entry is a bare leaf and `leaves.length >= 3` can never
    hold. The filter now takes the INNERMOST text-bearing
    elements (no element child carrying non-empty text): the
    wrapper is excluded, its label span qualifies. Container
    selectors also cover popover/popper portal wrappers. And
    the toggle click is retried once when the first scan finds
    no container: if the dropdown was ALREADY open, the first
    click CLOSED it, so the scan must run after a re-open, not
    after a close. If the live list genuinely cannot be read,
    the reader still returns None (unreadable) — never a faked
    live read; the caller keeps the reconstruction fallback.

    Fix-loop iter 19 (ANALYSIS16 §2g): the reader STILL returns
    None, and frame gp_0355 proves the dropdown IS open with all
    13 entries + checkmarks. Three root causes, all in this
    function: (1) visibility is offsetParent-based — a
    position:fixed dropdown portal (or a toggle inside a fixed
    header) has offsetParent null and is filtered out of the
    toggle search, the container search AND the leaf search
    (the banner probe fixed exactly this in iter-16 by switching
    to rect-based visibility; this reader never got the fix).
    Visibility is now rect-based everywhere. (2) the container
    search EXCLUDED anything inside nav/header/footer — but the
    round switcher lives inside the page header/nav, so the real
    dropdown was discarded. Containers are now identified by
    CONTENT (how many leaves map to known round labels, >=3
    required), not by ancestry; a leaf-count cap (<=60) keeps a
    page-wide wrapper from winning. (3) the reader ran ONCE
    (during the Country round) and its only retry assumed the
    first click had closed an open dropdown. The reader now
    scans FIRST (already-open dropdown, no click, no risk of
    clicking a round entry), then toggle-click + scan, then
    Escape-reset + toggle-click + scan — three attempts, each
    logged. The toggle search is tiered (explicit aria toggle
    first) and skips elements inside an already-open menu, which
    are round ENTRIES — clicking one would SWITCH rounds.

    Fix-loop iter 21 (ANALYSIS18 §6.7): the reader STILL
    returns None — toggle opened=True, scan container=False.
    The container selector (role/class/aria) does not match
    the opened menu's actual markup, and no log ever showed
    that markup. The scan now falls back to CONTENT-based
    container discovery (every visible container-shaped
    element in the light DOM + shadow roots, scored by how
    many innermost leaves map to known round labels, purity
    >=60% so a page-wide wrapper cannot win) and, on
    failure, returns a dump of the top scored containers
    (tag/class/role/rect/score/leaves/sample) that Python
    logs — the opened menu's actual markup, so the next
    artifact shows what to align the selector to.

    Fix-loop iter 24 (ANALYSIS21 RC5): the anchored pass STILL
    scores 0 everywhere — the menu's container is never among
    the candidates, or its leaves live in a shadow tree the
    passes never pierced. Two changes: (1) every leaf pass
    now pierces shadow roots (container + descendant hosts);
    (2) TOGGLE-DIFF — on attempts 2-3 the reader snapshots the
    visible-text DOM before the toggle click and diffs it
    against the snapshot after: appeared elements are what
    the toggle opened, disappeared elements mean the toggle
    closed an already-open menu (re-open + rescan, else use
    the removed entries). The diff, not markup guessing,
    names the real container."""
    try:
        labels = list(_WORLDLE_MODE_LABELS.values())
        # Toggle click (tiered). Returns True iff a toggle was
        # clicked. Tier 1: an explicit toggle affordance
        # (aria-expanded / aria-haspopup) whose text holds a mode
        # label. Tier 2: a visible button-like element whose text
        # holds a mode label — SKIPPING elements inside an
        # already-open menu, which are round ENTRIES: clicking one
        # would SWITCH rounds, not open the menu. The JS returns a
        # dict (opened/via) so the log can name which tier fired;
        # a plain bool is accepted for back-compat with
        # unit-test fakes.
        _TOGGLE_JS = """/* G4-LIVE-ROUNDS-TOGGLE */ (labels) => {
          const vis = e => { try { const r = e.getBoundingClientRect();
            return r.width > 0 && r.height > 0; }
            catch (err) { return false; } };
          const norm = s => (s||'').toLowerCase().replace(/\\s+/g,' ').trim();
          const inOpenMenu = el => !!el.closest(
            '[role=menu], [role=listbox]');
          const hasLabel = el => labels.some(l =>
            norm(el.innerText || el.textContent || '')
              .includes(norm(l)));
          const cands = Array.from(document.querySelectorAll(
            'button, [role=button], [aria-expanded], [aria-haspopup]'))
            .filter(vis);
          const tier1 = cands.find(el => hasLabel(el) &&
            (el.getAttribute('aria-expanded') !== null ||
             el.getAttribute('aria-haspopup') !== null));
          const hit = tier1 || cands.find(el => hasLabel(el) &&
            !inOpenMenu(el) && (el.querySelector('svg') ||
            el.tagName === 'BUTTON' ||
            el.getAttribute('role') === 'button'));
          if (!hit) return {opened: false, via: 'none'};
          try { hit.click(); } catch (err) {
            return {opened: false, via: 'click-failed'}; }
          return {opened: true, via: tier1 ? 'aria-toggle' : 'label-button',
                  tag: hit.tagName,
                  cls: String(hit.getAttribute('class') || '').slice(0, 60)};
        }"""

        def _toggle():
            # Iter-20 (ANALYSIS17 §2h): return the FULL toggle
            # outcome (opened + via), not a bare bool — the caller
            # logs toggle-vs-scan failure SEPARATELY, so the next
            # artifact shows whether the toggle itself failed or
            # the scan found no container after a successful
            # toggle. A bare bool from a test fake maps to
            # via='bool-compat'.
            try:
                _res = page.evaluate(_TOGGLE_JS, labels)
            except Exception:
                return {"opened": False, "via": "evaluate-failed"}
            if isinstance(_res, dict):
                return {"opened": bool(_res.get("opened")),
                        "via": str(_res.get("via") or "unknown"),
                        "tag": str(_res.get("tag") or ""),
                        "cls": str(_res.get("cls") or "")}
            return {"opened": bool(_res), "via": "bool-compat"}
        # RAW, OPEN-DROPDOWN-ONLY: every visible leaf entry inside an
        # identified open menu/listbox/dropdown container — NO label
        # filter in JS, NO whole-page fallback. Mapping to known labels
        # happens in Python so an unknown/renamed label survives as
        # `unmapped`. A list return is accepted for back-compat with
        # the iter-11 unit-test FakePage (which scripts a plain list).
        # Iter-19: visibility is RECT-based (a fixed-position
        # dropdown portal has a null offsetParent, so the old
        # offsetParent check filtered the real dropdown out), and
        # the container is picked by CONTENT SCORE (leaves mapping
        # to known round labels), not by ancestry or first-match —
        # the old nav/header/footer exclusion discarded the real
        # dropdown, which lives inside the page header/nav. The
        # leaf-count cap (<=60) keeps a page-wide wrapper from
        # winning; the precise mapping still happens in Python.
        _SCAN_JS = """/* G4-LIVE-ROUNDS-SCAN */ (labels) => {
          const vis = e => { try { const r = e.getBoundingClientRect();
            return r.width > 0 && r.height > 0; }
            catch (err) { return false; } };
          const norm = s => (s||'').replace(/\\s+/g,' ').trim();
          // Leaf-normalized label form for CONTENT scoring only:
          // lowercase, collapse spaces, strip leading/trailing
          // emoji/symbols ('📍 Capital' -> 'capital',
          // 'Country ✓' -> 'country').
          const keyOf = s => norm(s).toLowerCase()
            .replace(/^[^a-z0-9&]+/, '')
            .replace(/[^a-z0-9&]+$/, '');
          const labelKeys = new Set(labels.map(l => keyOf(l)));
          const out = [];
          const seen = new Set();
          const push = t => { const n = norm(t);
            if (n && n.length < 80 && !seen.has(n.toLowerCase())) {
              seen.add(n.toLowerCase()); out.push(n); } };
          // OPEN-dropdown containers only: role menu/listbox, a
          // dropdown/menu/popover/popper class wrapper, or the
          // aria-controls target / inner menu of an expanded
          // toggle. Never BODY/HTML. No ancestry exclusion: the
          // round switcher lives inside the page header/nav.
          const cands = [];
          const addCand = e => {
            if (e && !cands.includes(e) &&
                !/^(BODY|HTML)$/.test(e.tagName)) cands.push(e); };
          for (const e of document.querySelectorAll(
              '[role=menu], [role=listbox], [class*=dropdown i], ' +
              '[class*=menu i], [class*=popover i], ' +
              '[class*=popper i]')) if (vis(e)) addCand(e);
          for (const t of document.querySelectorAll(
              '[aria-expanded="true"]')) {
            if (!vis(t)) continue;
            const id = t.getAttribute('aria-controls');
            if (id) { const el = document.getElementById(id);
              if (el) addCand(el); }
            const inner = t.querySelector('[role=menu], [role=listbox]');
            if (inner) addCand(inner);
          }
          const hasTextChild = e => Array.from(e.children || [])
            .some(c => norm(c.innerText || c.textContent));
          // Iter-24 (ANALYSIS21 RC5): leaves pierce shadow
          // roots — a menu whose entries live inside a web
          // component's shadow tree is invisible to a plain
          // querySelectorAll on the container, so every pass
          // below collects leaves from the container, its
          // shadow root, and every descendant shadow host.
          const leavesIn = m => {
            const sel = 'button, [role=option], [role=menuitem], ' +
              '[role=menuitemradio], a, li, span, div, p';
            let arr = Array.from(m.querySelectorAll(sel));
            const addShadow = rt => {
              for (const el of rt.querySelectorAll('*')) {
                if (el.shadowRoot) {
                  arr = arr.concat(Array.from(
                    el.shadowRoot.querySelectorAll(sel)));
                  addShadow(el.shadowRoot);
                }
              }
            };
            try {
              if (m.shadowRoot) arr = arr.concat(Array.from(
                m.shadowRoot.querySelectorAll(sel)));
              addShadow(m);
            } catch (err) {}
            return arr.filter(vis)
              .filter(e => !hasTextChild(e));
          };
          let best = null, bestScore = -1, bestLeaves = [];
          for (const m of cands) {
            // Iter-18 leaf rule, kept: entries are NOT bare
            // leaves — each entry is a wrapper (button/li)
            // holding an emoji span + a label span. A leaf is the
            // INNERMOST text-bearing element: no element child
            // carrying non-empty text.
            const leaves = leavesIn(m);
            if (leaves.length < 3 || leaves.length > 60) continue;
            let score = 0;
            for (const lf of leaves)
              if (labelKeys.has(keyOf(
                  lf.innerText || lf.textContent || ''))) score++;
            if (score > bestScore) {
              bestScore = score; best = m; bestLeaves = leaves;
            }
          }
          // Iter-22 (ANALYSIS19 §5): ANCHORED scan. The
          // toggle OPENS (opened=True) but every markup /
          // content pass still finds no container, because the
          // menu's container is a CUSTOM element with no role
          // and no dropdown/menu/popover/popper class. Instead
          // of guessing the container's markup, ANCHOR to the
          // toggle that just opened the menu: its
          // aria-controls target, its nearest sibling menu,
          // or the ancestor at elementsFromPoint just below
          // the toggle (the open menu's on-screen position).
          // The anchored container is scored by CONTENT
          // (leaves mapping to round labels) like every other
          // pass, and its tag/class/role + leaves are returned
          // as `anchored` on success AND failure, so the next
          // artifact names the menu's actual container.
          let anchored = null;
          try {
            const leavesOf = m => leavesIn(m);
            const scoreOf = leaves => leaves.filter(lf =>
              labelKeys.has(keyOf(
                lf.innerText || lf.textContent || ''))).length;
            const describeEl = (el, via) => {
              try {
                const r = el.getBoundingClientRect();
                return {tag: el.tagName,
                  cls: String(el.getAttribute('class') || '')
                    .slice(0, 60),
                  role: el.getAttribute('role') || '',
                  rect: {x: Math.round(r.x), y: Math.round(r.y),
                         w: Math.round(r.width),
                         h: Math.round(r.height)},
                  via: via};
              } catch (err) { return {tag: el.tagName, via: via}; }
            };
            const toggles = Array.from(document.querySelectorAll(
              '[aria-expanded="true"], [aria-haspopup="true"], ' +
              '[aria-controls]'))
              .filter(vis);
            for (const t of toggles) {
              const candsA = [];
              const aid = t.getAttribute('aria-controls');
              if (aid) {
                const el = document.getElementById(aid);
                if (el) candsA.push([el, 'aria-controls']);
              }
              const inner = t.querySelector(
                '[role=menu], [role=listbox]');
              if (inner) candsA.push([inner, 'toggle-descendant']);
              // Iter-23 (ANALYSIS20 §5): push EVERY sibling
              // as an anchor candidate, not only menu-like /
              // hyphen-tagged ones — the menu container may
              // be a plain div sibling, and the CONTENT score
              // (leaves mapping to round labels, score >= 3)
              // is the filter, not the tag. Same for the
              // elementsFromPoint ancestors below.
              if (t.parentElement) {
                for (const sib of t.parentElement.children) {
                  if (sib === t) continue;
                  candsA.push([sib, 'sibling']);
                }
              }
              if (t.nextElementSibling)
                candsA.push([t.nextElementSibling, 'next-sibling']);
              try {
                const tr = t.getBoundingClientRect();
                const hit = document.elementFromPoint(
                  tr.x + tr.width / 2, tr.y + tr.height + 12);
                if (hit) {
                  // Iter-23: push EVERY ancestor of the hit,
                  // nearest-first — the open menu's container
                  // is one of them, whatever its tag/class.
                  // Content scoring (score >= 3) filters; the
                  // loop below breaks at the first candidate
                  // that scores, so a near menu container wins
                  // before a page-wide wrapper.
                  let node = hit;
                  for (let d = 0; d < 6 && node; d++) {
                    if (node !== t)
                      candsA.push([node,
                        'elementsFromPoint-ancestor']);
                    node = node.parentElement;
                  }
                }
              } catch (err) {}
              for (const pair of candsA) {
                const el = pair[0], via = pair[1];
                if (!el || /^(BODY|HTML)$/.test(el.tagName)) continue;
                if (!vis(el)) continue;
                const leaves = leavesOf(el);
                const score = scoreOf(leaves);
                const info = describeEl(el, via);
                info.leaves = leaves.length; info.score = score;
                info.sample = leaves.slice(0, 3).map(lf =>
                  norm(lf.innerText || lf.textContent || '')
                    .slice(0, 40));
                if (!anchored || score > anchored.score)
                  anchored = info;
                if (score >= 3 && leaves.length >= 3 &&
                    leaves.length <= 60) {
                  best = el; bestScore = score; bestLeaves = leaves;
                }
                if (bestScore >= 3) break;
              }
              if (bestScore >= 3) break;
            }
          } catch (err) {}
          // Iter-21 (ANALYSIS18 §6.7): the toggle OPENS
          // (opened=True) but the scan finds no container —
          // the container selector (role/class/aria) does not
          // match the opened menu's actual markup. The menu's
          // MARKUP is the unknown, so the fallback stops
          // trusting markup: EVERY visible container-shaped
          // element (div/ul/section/nav/menu/aside/form + any
          // role-bearing element, light DOM + every shadow
          // root) is scored by CONTENT (how many innermost
          // leaves map to known round labels). A real menu
          // scores high AND pure (>=60% of its leaves are
          // round labels — a page-wide wrapper scores high
          // but impure, and is rejected). The fallback runs
          // only when the markup-based pass scored <3.
          // Diag: the top scored containers (tag/class/role/
          // rect/score/leaves/sample) are returned on failure
          // so the next artifact shows the opened menu's
          // ACTUAL container markup instead of "no container".
          const diagCands = [];
          const noteCand = (el, score, leaves) => {
            try {
              const r = el.getBoundingClientRect();
              diagCands.push({tag: el.tagName,
                cls: String(el.getAttribute('class') || '')
                  .slice(0, 60),
                role: el.getAttribute('role') || '',
                rect: {x: Math.round(r.x), y: Math.round(r.y),
                       w: Math.round(r.width),
                       h: Math.round(r.height)},
                score: score, leaves: leaves.length,
                sample: leaves.slice(0, 3).map(lf =>
                  norm(lf.innerText || lf.textContent || '')
                    .slice(0, 40))});
            } catch (err) {}
          };
          if (!best || bestScore < 3) {
            const fbRoots = [document];
            let fbBest = null, fbScore = -1, fbLeaves = [];
            const collectFb = rt => {
              try {
                for (const el of rt.querySelectorAll('*')) {
                  if (el.shadowRoot) {
                    fbRoots.push(el.shadowRoot);
                    collectFb(el.shadowRoot);
                  }
                }
              } catch (err) {}
            };
            try { collectFb(document); } catch (err) {}
            const fbSeen = new Set();
            for (const rt of fbRoots) {
              let fbEls = [];
              try { fbEls = Array.from(rt.querySelectorAll(
                'div, ul, section, nav, menu, aside, form, ' +
                '[role=menu], [role=listbox], [role=dialog]'))
                // Iter-22: CUSTOM-ELEMENT containers. The
                // fallback tag list EXCLUDED custom elements,
                // so a <…-menu> host with no role/class never
                // enumerated. Hyphen-tagged elements are
                // scored by CONTENT the same way (dedup via
                // fbSeen below).
                .concat(Array.from(rt.querySelectorAll('*'))
                  .filter(e => (e.tagName || '').includes('-'))); }
              catch (err) {}
              for (const m of fbEls) {
                if (fbSeen.has(m)) continue;
                fbSeen.add(m);
                if (/^(BODY|HTML)$/.test(m.tagName)) continue;
                if (!vis(m)) continue;
                const leaves = leavesIn(m);
                if (leaves.length < 3 || leaves.length > 60) continue;
                let score = 0;
                for (const lf of leaves)
                  if (labelKeys.has(keyOf(
                      lf.innerText || lf.textContent || ''))) score++;
                noteCand(m, score, leaves);
                const purity = leaves.length ?
                  score / leaves.length : 0;
                if (score >= 3 && purity >= 0.6 && score > fbScore) {
                  fbScore = score; fbBest = m; fbLeaves = leaves;
                }
              }
            }
            diagCands.sort((a, b) => b.score - a.score);
            if (fbBest) {
              best = fbBest; bestScore = fbScore; bestLeaves = fbLeaves;
            }
          }
          if (!best || bestScore < 3)
            return {entries: [], container: false,
                    diag: diagCands.slice(0, 5),
                    anchored: anchored};
          for (const e of bestLeaves) push(e.innerText);
          return {entries: out, container: true,
                  anchored: anchored};
        }"""

        def _scan():
            try:
                return page.evaluate(_SCAN_JS, labels) or {}
            except Exception:
                return {}
        # Iter-19 retry loop: attempt 1 scans FIRST (the dropdown
        # may ALREADY be open — no click, so no risk of clicking a
        # round entry and switching rounds). Attempt 2: toggle
        # click -> scan. Attempt 3: Escape-reset -> toggle click
        # -> scan. (The iter-18 Second chance re-open is subsumed
        # by attempts 2-3.) Each attempt is logged; a genuinely
        # unreadable list still returns None below — never a faked
        # live read.
        raw_ev = {}
        # Iter-24 RC5: pre-toggle DOM snapshot for the
        # toggle-diff (filled on attempts 2-3, before the
        # toggle click).
        _snap_before = []
        # Iter-20 (ANALYSIS17 §2h): TOGGLE-vs-SCAN failure split,
        # logged per attempt. The iter-19 log only said "attempt N
        # found no dropdown container", which conflates two
        # different failures: (a) the toggle click itself failed
        # (no toggle control found / click failed), vs (b) the
        # toggle succeeded but the scan still found no container
        # (wrong container selector / dropdown not open after the
        # click). Each attempt now logs toggle=... scan=...
        # separately; the final None line carries both outcome
        # lists so the split survives in one grep.
        _toggle_outcomes = []
        _scan_outcomes = []
        for _attempt in (1, 2, 3):
            _tog = {"opened": None, "via": "skipped-scan-first"}
            if _attempt == 1:
                raw_ev = _scan()
            elif _attempt == 2:
                _snap_before = _worldle_g4_snapshot(page)
                _tog = _toggle()
                _toggle_outcomes.append(
                    f"attempt2 opened={_tog.get('opened')} "
                    f"via={_tog.get('via')}")
                page.wait_for_timeout(700)
                raw_ev = _scan()
            else:
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass
                page.wait_for_timeout(300)
                _snap_before = _worldle_g4_snapshot(page)
                _tog = _toggle()
                _toggle_outcomes.append(
                    f"attempt3 opened={_tog.get('opened')} "
                    f"via={_tog.get('via')}")
                page.wait_for_timeout(700)
                raw_ev = _scan()
            _n_entries = 0
            _has_container = False
            if isinstance(raw_ev, dict):
                _has_container = bool(raw_ev.get("container"))
                _n_entries = len(raw_ev.get("entries") or [])
            elif isinstance(raw_ev, list):
                _has_container = True
                _n_entries = len(raw_ev)
            _scan_outcomes.append(
                f"attempt{_attempt} container={_has_container} "
                f"entries={_n_entries}")
            _dbg(gid, "worldle G4 live rounds reader: attempt "
                      f"{_attempt} toggle opened={_tog.get('opened')} "
                      f"via={_tog.get('via')} scan container="
                      f"{_has_container} entries={_n_entries}")
            # Iter-23 (ANALYSIS20 §5): the anchored scan MUST
            # log its anchor attempt on EVERY attempt — success
            # AND failure — so the next analysis can tell
            # whether it ran. The old code logged `anchored`
            # only when a non-null anchor existed on the
            # failure path, so a run with no anchor at all left
            # zero `anchored` lines.
            _anch_attempt = raw_ev.get("anchored") \
                if isinstance(raw_ev, dict) else None
            _dbg(gid, "worldle G4 live rounds reader: attempt "
                      f"{_attempt} anchored="
                      f"{str(_anch_attempt)[:400]}")
            if not _has_container and _attempt in (2, 3):
                # Iter-24 RC5: TOGGLE-DIFF. The markup /
                # anchored / content passes all score 0, so
                # stop guessing the menu's markup: diff the
                # DOM snapshot before the toggle click against
                # the snapshot after it. Elements that APPEARED
                # are what the toggle opened; elements that
                # DISAPPEARED mean the toggle CLOSED an
                # already-open menu the scan-first pass had
                # missed (then re-open + rescan, else use the
                # removed entries — they WERE the open menu).
                try:
                    _snap_after = _worldle_g4_snapshot(page)
                    _diff = _worldle_g4_diff_entries(
                        _snap_before, _snap_after, labels)
                except Exception as _e_diff:
                    _diff = {}
                    _dbg(gid, "worldle G4 toggle-diff: diff "
                              f"failed ({_e_diff})")
                if isinstance(_diff, dict) and _diff:
                    _dbg(gid, "worldle G4 toggle-diff: attempt "
                              f"{_attempt} new={_diff.get('n_new')} "
                              f"removed={_diff.get('n_removed')} "
                              "new_label_matches="
                              f"{_diff.get('new_label_matches')} "
                              "removed_label_matches="
                              f"{_diff.get('removed_label_matches')} "
                              "container_new="
                              f"{_diff.get('container_new')} "
                              "container_removed="
                              f"{_diff.get('container_removed')} "
                              "anchored_new="
                              f"{str(_diff.get('anchored_new'))[:300]} "
                              "anchored_removed="
                              f"{str(_diff.get('anchored_removed'))[:300]}")
                    if _diff.get("container_new"):
                        raw_ev = {"entries":
                                  _diff.get("entries_new") or [],
                                  "container": True,
                                  "anchored":
                                      _diff.get("anchored_new"),
                                  "diag": []}
                        _has_container = True
                        _n_entries = len(raw_ev["entries"])
                        _dbg(gid, "worldle G4 toggle-diff: opened "
                                  "menu found by DOM diff; "
                                  f"anchored="
                                  f"{str(_diff.get('anchored_new'))[:300]} "
                                  f"entries={_n_entries}")
                    elif _diff.get("container_removed"):
                        _dbg(gid, "worldle G4 toggle-diff: toggle "
                                  "CLOSED an already-open menu; "
                                  "re-opening + rescanning")
                        _tog2 = _toggle()
                        page.wait_for_timeout(700)
                        raw_ev = _scan()
                        if isinstance(raw_ev, dict):
                            _has_container = bool(
                                raw_ev.get("container"))
                            _n_entries = len(
                                raw_ev.get("entries") or [])
                        elif isinstance(raw_ev, list):
                            _has_container = True
                            _n_entries = len(raw_ev)
                        if not _has_container:
                            raw_ev = {"entries":
                                      _diff.get("entries_removed")
                                      or [],
                                      "container": True,
                                      "anchored": _diff.get(
                                          "anchored_removed"),
                                      "diag": []}
                            _has_container = True
                            _n_entries = len(raw_ev["entries"])
                            _dbg(gid, "worldle G4 toggle-diff: "
                                      "re-open scan still empty; "
                                      "using removed (was-open) "
                                      "menu entries; anchored="
                                      f"{str(_diff.get('anchored_removed'))[:300]} "
                                      f"entries={_n_entries}")
            if _has_container:
                break
            _dbg(gid, "worldle G4 live rounds reader: attempt "
                      f"{_attempt} found no dropdown container; "
                      "retrying")
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        if isinstance(raw_ev, dict):
            if not raw_ev.get("container"):
                _dbg(gid, "worldle G4 live rounds reader: no open dropdown "
                          "container found after 3 attempts -> None "
                          "(unreadable) | toggle outcomes="
                          f"{_toggle_outcomes} | scan outcomes="
                          f"{_scan_outcomes}")
                # Iter-21: the scan now dumps the top scored
                # containers (tag/class/role/rect/score/leaves/
                # sample) — the opened menu's ACTUAL markup —
                # so the next artifact shows what to align the
                # selector to instead of a bare "no container".
                _diag = raw_ev.get("diag")
                if _diag:
                    _dbg(gid, "worldle G4 live rounds reader: "
                              "opened-menu container dump (top "
                              f"scored): {str(_diag)[:1500]}")
                # Iter-22: the ANCHORED container (from the
                # toggle) is logged even on failure — its
                # tag/class/role + leaves name the menu's
                # actual container when every other pass
                # found nothing. Iter-23: ALWAYS logged, even
                # when the anchor is None (no anchor candidate
                # evaluated) — a missing `anchored` line must
                # never again be ambiguous with a scan that
                # never ran.
                _anch = raw_ev.get("anchored")
                if _anch:
                    _dbg(gid, "worldle G4 live rounds reader: "
                              "anchored container (from toggle): "
                              f"{str(_anch)[:800]}")
                else:
                    _dbg(gid, "worldle G4 live rounds reader: "
                              "anchored=None (no anchor candidate "
                              "evaluated on this attempt)")
                return None
            raw = list(raw_ev.get("entries") or [])
        elif isinstance(raw_ev, list):
            raw = list(raw_ev)
        else:
            _dbg(gid, "worldle G4 live rounds reader: unexpected return "
                      f"type {type(raw_ev).__name__} -> None (unreadable)")
            return None
        inv = {_s_worldle_label_key(v): k
               for k, v in _WORLDLE_MODE_LABELS.items()}
        mapped = {inv[_s_worldle_label_key(f)] for f in (raw or [])
                  if _s_worldle_label_key(f) in inv}
        unmapped = [f for f in (raw or [])
                    if _s_worldle_label_key(f) not in inv
                    and _s_worldle_label_key(f) not in _WORLDLE_DROPDOWN_CHROME
                    and _s_worldle_label_key(f)]
        _dbg(gid, f"worldle G4 live rounds raw (open dropdown only): {raw}")
        _dbg(gid, f"worldle G4 live rounds mapped: {sorted(mapped)}")
        _dbg(gid, f"worldle G4 live rounds unmapped (in-scope): {unmapped}")
        return {"mapped": mapped, "raw": list(raw or []),
                "unmapped": unmapped, "scope": "dropdown"}
    except Exception as e:
        _dbg(gid, f"worldle G4 live round list unreadable: {str(e)[:90]}")
        return None


def _s_worldle_play_country(page, ans, gid, day):
    """Country round: the pre-existing solver, unchanged — autosuggest
    picks for the 3-probe plan + answer, Guess button, answer-row win
    check. Returns (ok, evidence, played_guesses)."""
    _close_modals(page)
    seq = _guesses(gid, ans)
    a = str(ans or "").strip()
    played = []
    for n, g in enumerate(seq):
        last = (n == len(seq) - 1)
        _idle_drift(page)
        ok, detail = _type_country_guess(page, page, g)
        _dbg(gid, f"guess {n + 1}/{len(seq)} {g!r}: pick ok={ok} ({detail[:70]})")
        if not ok:
            return False, f"guess {n + 1} {g!r}: pick failed ({detail})", played
        played.append(g)
        _s_worldle_submit_guarded(page, gid, "country",
                                  day.get("date") or "")
        _settle(page, base=3000)
        try:
            body = (page.evaluate("() => document.body.innerText") or "")
        except Exception:
            body = ""
        low = body.lower()
        if last and a.lower() in low and any(
                k in low for k in ("guesses remaining", "well done",
                                  "correct", "share", "guessed")):
            return True, f"answer row rendered after guess {n + 1}", played
    page.wait_for_timeout(2500)
    try:
        body = (page.evaluate("() => document.body.innerText") or "")
    except Exception:
        body = ""
    _dbg(gid, f"final board tail: {body[-220:]!r}")
    if a.lower() in body.lower() and any(
            k in body.lower() for k in ("well done", "correct",
                                        "guesses remaining", "share")):
        return True, "answer present with post-game markers", played
    return False, "board did not confirm the answer", played


def _s_worldle_play_neighbours(page, gid, day):
    """Neighbours: autosuggest-pick every border country, submit each.
    Border names come from the day data (neighbour records), never from
    the played country's own name."""
    picked = []
    for b in (day.get("borders") or []):
        name = b.get("name") or b.get("code")
        _idle_drift(page)
        ok, detail = _type_country_guess(page, page, name)
        _dbg(gid, f"worldle neighbours pick {name!r}: ok={ok} ({detail[:70]})")
        if not ok:
            return "failed", [name], \
                f"autosuggest pick failed for border {name!r} ({detail})"
        picked.append(name)
        _s_worldle_submit_guarded(page, gid, "neighbours",
                                  day.get("date") or "")
        _settle(page, base=2200)
    return "attempted", picked, f"picked {len(picked)} border countries"


def _worldle_capital_fit_px(path_bbox, bounds, lat, lon):
    """Map (lat, lon) to screen px via the country-bbox -> outline-path
    fit: the country's lat/lon bbox maps linearly onto the rendered
    outline path's screen bbox (longitude linear; latitude in Mercator
    space, as web maps project it). Returns (x, y) screen px, or None
    when the bbox is degenerate / the point is outside it. Pure
    function (unit-tested on the Togo fixture).
    """
    import math as _m
    try:
        min_lon = float(bounds["minLon"])
        max_lon = float(bounds["maxLon"])
        min_lat = float(bounds["minLat"])
        max_lat = float(bounds["maxLat"])
        w = float(path_bbox.get("w") or 0)
        h = float(path_bbox.get("h") or 0)
        if w <= 0 or h <= 0 or max_lon <= min_lon or max_lat <= min_lat:
            return None

        def _merc(la):
            return _m.log(_m.tan(_m.pi / 4 + _m.radians(la) / 2))

        fx = (float(lon) - min_lon) / (max_lon - min_lon)
        fy = (_merc(max_lat) - _merc(float(lat))) / (
            _merc(max_lat) - _merc(min_lat))
        if not (-0.25 <= fx <= 1.25 and -0.25 <= fy <= 1.25):
            return None
        return (float(path_bbox.get("x") or 0) + fx * w,
                float(path_bbox.get("y") or 0) + fy * h)
    except Exception:
        return None


def _worldle_pin_probe(page, px, py):
    """Probe the pin state at (px, py): which element is topmost at the
    point (and whether it is inside the map), plus a BROAD marker count.

    Fix-loop iter 7: the old check counted only [class*=marker/pin] /
    [data-marker] — if the game renders its pin as an SVG circle/g (or
    a Leaflet/MapLibre marker), a real pin was invisible to it and the
    solver bailed before ever submitting. The broad count covers SVG
    circles/ellipses/marker classes + Leaflet/MapLibre markers, and the
    map-host HTML signature catches a newly added pin node. The topmost
    element answers the other failure mode: an overlay/ad covering the
    point swallows the real mouse click. Pure probe (no clicks).

    Probe v2 (fix-loop iter 11): also log the FULL elementFromPoint
    ancestor chain (tag/class/id, 6 levels), whether the topmost
    element is the picked map element or its descendant (picked vs
    topmost identity), and canvas-vs-SVG/DOM — so a "markers 4->4"
    failure can be diagnosed from the log instead of guessed."""
    try:
        return page.evaluate("""(pt) => {
          const top = document.elementFromPoint(pt.x, pt.y);
          const inMap = top ? !!top.closest(
            'teuteuf-geo-map, [data-projection], svg, canvas, ' +
            '[class*=map i]') : false;
          const q = s => document.querySelectorAll(s).length;
          const markers = q('svg circle, svg ellipse, svg use, ' +
            'svg [class*=marker i], svg [class*=pin i], ' +
            '.leaflet-marker-icon, .maplibregl-marker, [data-marker], ' +
            '[class*=marker i], [class*=pin i]');
          // Fix-loop iter 12: the map is the `teuteuf-geo-map` WEB
          // COMPONENT (bundled main.js modules 91041/87375: registered
          // via customElements from web-components.teuteuf.fr), so
          // its SVG + pin live in SHADOW DOM — light-DOM queries see
          // neither. Count markers inside every shadow root too, and
          // report the shadow-host count, or a real pin renders as
          // 'markers 4->4' and the solver bails on a landed pin.
          let markersShadow = 0, shadowHosts = 0;
          const countShadow = root => {
            for (const el of root.querySelectorAll('*')) {
              if (el.shadowRoot) {
                shadowHosts += 1;
                markersShadow += el.shadowRoot.querySelectorAll(
                  'svg circle, svg ellipse, svg use, ' +
                  'svg [class*=marker i], ' +
                  'svg [class*=pin i], [data-marker], ' +
                  '[class*=marker i], [class*=pin i]').length;
                countShadow(el.shadowRoot);
              }
            }
          };
          try { countShadow(document); } catch (e) {}
          let sig = '';
          const host = top ? (top.closest(
            'svg, teuteuf-geo-map, [data-projection]') || top) : null;
          if (host) sig = String((host.outerHTML || '').length) + ':' +
            String(host.childElementCount || 0);
          // v2: ancestor chain of the topmost element + picked-map identity.
          const chain = [];
          let n = top;
          for (let i = 0; i < 6 && n; i++) {
            chain.push((n.tagName || '') + '#' + (n.id || '') + '.' +
              String(n.getAttribute && n.getAttribute('class') || '')
              .slice(0, 50));
            n = n.parentElement;
          }
          const vis = e => e && e.offsetParent !== null;
          const mapEls = Array.from(document.querySelectorAll(
            'teuteuf-geo-map, [data-projection], svg, canvas'))
            .filter(vis);
          let picked = null, pickedArea = -1;
          for (const el of mapEls) {
            const r = el.getBoundingClientRect();
            if (r.width * r.height > pickedArea) {
              pickedArea = r.width * r.height; picked = el;
            }
          }
          const topInPicked = !!(picked && top &&
            (top === picked || picked.contains(top)));
          return {topTag: top ? top.tagName : null,
                  topCls: top ? (top.getAttribute('class') || '')
                    .slice(0, 100) : '',
                  topId: top ? (top.id || '') : '',
                  topTestId: top ? (top.getAttribute('data-testid') || '')
                    : '',
                  inMap: inMap, sig: sig,
                  chain: chain,
                  pickedTag: picked ? picked.tagName : null,
                  pickedCls: picked ?
                    String(picked.getAttribute('class') || '')
                    .slice(0, 100) : '',
                  pickedId: picked ? (picked.id || '') : '',
                  topInPicked: topInPicked,
                  topIsCanvas: !!(top && top.tagName === 'CANVAS'),
                  topInSvg: !!(top && top.closest('svg')),
                  markers: markers + markersShadow,
                  markers_light: markers,
                  markers_shadow: markersShadow,
                  shadow_hosts: shadowHosts};
        }""", {"x": float(px), "y": float(py)}) or {}
    except Exception:
        return {}


def _worldle_color_luminance(css):
    """Relative luminance 0..1 for an rgb()/rgba() color string, or
    None when unparsable. Pure (unit-tested)."""
    m = re.match(r"rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})",
                 str(css or ""))
    if not m:
        return None
    try:
        r, g, b = (int(x) / 255.0 for x in m.groups())
    except ValueError:
        return None

    def _lin(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def _worldle_capital_tab_active(ev):
    """Decide whether the PINPOINT tab is active from an evidence dict
    {aria_selected, cls, data_state, bg, sibling_bg, prompt_visible,
    map_visible} (iter-15 P1, ANALYSIS11 Capital root cause).

    Iter-14 accepted only aria-selected / /active|selected/ class /
    data-state='active' — but the site marks the active tab with a
    DARK BACKGROUND (Tailwind bg class containing neither "active"
    nor "selected"), no aria-selected, no data-state. Frame g_120
    proved the PINPOINT tab WAS visually active (dark tab + prompt +
    map) while the checker reported False, so the pin was never
    clicked. Now also accepts: (a) panel-switch evidence — the
    PINPOINT prompt text + a visible map means the view switched;
    (b) the dark-background marker — the PINPOINT tab's computed
    background is dark AND darker than the NAME tab's (the relative
    comparison guards against a globally dark theme). Pure
    (unit-tested). The pin is still NEVER clicked unless this
    returns True.
    """
    ev = ev if isinstance(ev, dict) else {}
    if ev.get("aria_selected") is True:
        return True
    if re.search(r"active|selected", str(ev.get("cls") or ""),
                 re.IGNORECASE):
        return True
    if str(ev.get("data_state") or "") == "active":
        return True
    if ev.get("prompt_visible") and ev.get("map_visible"):
        return True
    lum = _worldle_color_luminance(ev.get("bg") or "")
    sib = _worldle_color_luminance(ev.get("sibling_bg") or "")
    if lum is not None and lum < 0.45:
        if sib is None or lum < sib - 0.08:
            return True
    return False


def _worldle_capital_activate_pinpoint(page, gid):
    """Activate the `2. PINPOINT` tab; NEVER proceed with the tab
    unconfirmed-active (iter-14 P1, ANALYSIS10 Capital root cause).

    Iter-13 clicked the tab, read active_after=False, and proceeded
    anyway ("active state not confirmable; proceeding") — the pin
    click then landed with the NAME tab still active and registered
    nothing (markers 4->4). Flow: (1) if already active, return True;
    (2) click via Playwright locator, else JS click; (3) re-check
    active; (4) if still inactive, dump the tab DOM (aria-selected /
    class / data-state / actual tab element), retry with alternate
    selectors (contains-PINPOINT, data-state/data-value, second tab
    in the tablist); (5) if STILL inactive, return False with the
    activation evidence — the caller FAILS the mode honestly and
    the pin is NEVER clicked. Returns (active_bool, detail)."""
    def _active():
        # Iter-15: collect EVIDENCE in-page, decide in Python
        # (_worldle_capital_tab_active) so the dark-background and
        # panel-switch markers are testable without a browser.
        try:
            ev = page.evaluate("""/* CAPITAL_TAB_ACTIVE_CHECK */
              () => {
              const vis = e => e && e.offsetParent !== null;
              const hit = Array.from(document.querySelectorAll(
                'button, [role=tab]')).filter(vis)
                .find(e => /^\\s*2\\.\\s*PINPOINT\\s*$/i
                  .test(e.innerText || ''));
              if (!hit) return {found: false};
              const nameTab = Array.from(document.querySelectorAll(
                'button, [role=tab]')).filter(vis)
                .find(e => /^\\s*1\\.\\s*NAME\\s*$/i
                  .test(e.innerText || ''));
              const bgOf = el => { try {
                return getComputedStyle(el).backgroundColor || ''; }
                catch (e) { return ''; } };
              const bodyText = (document.body.innerText || '')
                .toLowerCase();
              const mapVisible = (() => {
                const cands = Array.from(document.querySelectorAll(
                  'teuteuf-geo-map, [data-projection], svg, canvas'))
                  .filter(vis);
                return cands.some(el => {
                  const r = el.getBoundingClientRect();
                  return r.width * r.height > 10000;
                });
              })();
              return {found: true,
                aria_selected:
                  hit.getAttribute('aria-selected') === 'true',
                cls: String(hit.className || ''),
                data_state: hit.getAttribute('data-state'),
                bg: bgOf(hit),
                sibling_bg: nameTab ? bgOf(nameTab) : '',
                prompt_visible:
                  bodyText.includes('can you guess where'),
                map_visible: mapVisible};
            }""")
        except Exception:
            return False
        if isinstance(ev, bool):
            # Legacy shape: the check returned a bare bool.
            return ev
        if not isinstance(ev, dict) or not ev.get("found"):
            return False
        return bool(_worldle_capital_tab_active(ev))

    def _dump():
        try:
            return str(page.evaluate("""/* CAPITAL_TAB_DUMP */
              () => {
              const vis = e => e && e.offsetParent !== null;
              const tabs = Array.from(document.querySelectorAll(
                'button, [role=tab]')).filter(vis);
              return tabs.map(e => ({
                text: (e.innerText || '').trim().slice(0, 40),
                aria_selected: e.getAttribute('aria-selected'),
                // Iter-15: FULL class strings — the iter-14 dump
                // truncated at ~60-80 chars, hiding the suffix that
                // distinguishes the dark-background active tab.
                cls: String(e.className || ''),
                data_state: e.getAttribute('data-state'),
                data_value: e.getAttribute('data-value'),
                role: e.getAttribute('role'),
                id: e.id || '',
                disabled: !!e.disabled,
                computed_bg: (() => { try {
                  return getComputedStyle(e).backgroundColor || ''; }
                  catch (err) { return ''; } })(),
                outer: (e.outerHTML || '').slice(0, 2000)
              }));
            }""") or "")
        except Exception as e:
            return f"tab DOM dump failed: {e}"

    if _active():
        return True, "PINPOINT tab already active"
    _found = False
    _via = ""
    try:
        for _sel in ("[role=tab]:has-text('PINPOINT')",
                     "button:has-text('PINPOINT')"):
            try:
                _cand = page.locator(_sel).first
                if _cand.count() > 0 and _cand.is_visible():
                    _cand.click(timeout=5000)
                    _found, _via = True, f"locator {_sel}"
                    break
            except Exception:
                continue
    except Exception:
        pass
    if not _found:
        try:
            _res = page.evaluate("""/* CAPITAL_TAB_CLICK_JS */
              () => {
              const vis = e => e && e.offsetParent !== null;
              const hit = Array.from(document.querySelectorAll(
                'button, [role=tab]')).filter(vis)
                .find(e => /^\\s*2\\.\\s*PINPOINT\\s*$/i
                  .test(e.innerText || ''));
              if (!hit) return {found: false};
              hit.click();
              return {found: true, via: 'js-click'};
            }""") or {}
            _found = bool(_res.get("found"))
            _via = str(_res.get("via") or "js-click")
        except Exception:
            _found = False
    if not _found:
        return False, ("2. PINPOINT tab not found (pinpoint sub-round "
                       "not entered); pin NOT clicked")
    try:
        page.wait_for_timeout(1200)
    except Exception:
        pass
    if _active():
        return True, f"PINPOINT tab activated via {_via}"
    # Still inactive: dump the tab DOM, then retry with alternate
    # selectors (contains-PINPOINT without the `2.` anchor,
    # data-state/data-value match, second tab in the tablist).
    _dump_text = _dump()
    _dbg(gid, "worldle capital PINPOINT tab still inactive after "
              f"{_via}; tab DOM dump: {_dump_text[:6000]}")
    try:
        page.evaluate("""/* CAPITAL_TAB_RETRY_CLICK */
          () => {
          const vis = e => e && e.offsetParent !== null;
          const tabs = Array.from(document.querySelectorAll(
            'button, [role=tab]')).filter(vis);
          let hit = tabs.find(e => /PINPOINT/i.test(e.innerText || ''));
          if (!hit) hit = tabs.find(e =>
            /pinpoint/i.test((e.getAttribute('data-value') || '') + ' ' +
                             (e.getAttribute('data-state') || '')));
          if (!hit) {
            const list = document.querySelector('[role=tablist]');
            const in_list = list ? Array.from(list.querySelectorAll(
              'button, [role=tab]')).filter(vis) : [];
            hit = in_list[1] || null;
          }
          if (!hit) return {found: false};
          hit.click();
          return {found: true, via: 'retry-alt-selector'};
        }""")
    except Exception:
        pass
    try:
        page.wait_for_timeout(1200)
    except Exception:
        pass
    if _active():
        return True, ("PINPOINT tab activated on retry (alternate "
                      f"selector); first-attempt dump: {_dump_text[:3000]}")
    return False, ("PINPOINT tab NEVER activated (tab click did not "
                   f"switch the view); tab DOM dump: {_dump_text[:6000]}; "
                   "pin NOT clicked — failing the mode honestly")


def _s_worldle_capital_pin_ladder(page, gid, px, py, before,
                                 marker_appeared):
    """Pin-placement ladder (iter-16, ANALYSIS12 §7; reordered
    iter-18, ANALYSIS15 §6).

    The iter-15 run proved a real mouse click lands ON the map
    (inMap=True, top=svg) yet registers NO pin (markers 4->4):
    the `teuteuf-geo-map` component does not treat a click on the
    SVG root as a pin. Attempts, each followed by a re-probe,
    stopping as soon as a marker appears:
      (A) a real press — move, down, hold 250ms, up — which
          generates TRUE pointer events (a synthetic dispatch
          never generates pointer events, and a pointerdown-only
          listener ignores a mouse-only click). Zero mouse
          movement between down and up, so d3-zoom's didZoomMove
          stays false and its `end` handler fires
          onRegionClick(pendingClickRegion).
      (B) a confirm step — click a confirm/place-pin control if
          one appeared after the click (some map widgets place a
          tentative pin that needs confirming).
      (C) LAST RESORT, synthetic: dispatch the full pointer+mouse
          sequence on the country outline PATH element whose bbox
          contains the point (the listener may live on the path,
          not the SVG root). Iter-18 demoted this from first to
          last: synthetic dispatches were the primary path in
          iters 13–17 and NEVER produced a marker, so they no
          longer run before the real press.
    Returns (after_probe_or_None, detail). Placement is still only
    believed when the caller sees a marker/store change.
    """
    attempts = []
    after = None
    # (A) real press first: move, down, hold, up, zero movement
    # between down and up.
    try:
        page.mouse.move(px, py)
        page.mouse.down()
        page.wait_for_timeout(250)
        page.mouse.up()
        attempts.append("real-press=ok")
    except Exception as e:
        attempts.append(f"real-press failed: {str(e)[:60]}")
    _settle(page, base=1200)
    after = _worldle_pin_probe(page, px, py)
    if marker_appeared(before, after):
        return after, "; ".join(attempts) + " -> MARKER"
    # (B) confirm step: a confirm/place-pin control that appeared
    # after the click (tentative-pin widgets need confirming).
    try:
        clicked = page.evaluate("""() => {
          const vis = e => e && e.offsetParent !== null;
          const norm = s => (s || '').toLowerCase()
            .replace(/\\s+/g, ' ').trim();
          const cands = Array.from(document.querySelectorAll(
            'button, [role=button], a')).filter(vis);
          const hit = cands.find(e => {
            const t = norm(e.innerText || e.textContent || '');
            return t === 'confirm' || t === 'confirm location' ||
              t === 'confirm pin' || t === 'place pin' ||
              t === 'use this location' || t === 'submit location' ||
              t.includes('confirm pin') || t.includes('place pin') ||
              t.includes('use this location');
          });
          if (!hit) return false;
          hit.click();
          return true;
        }""")
    except Exception:
        clicked = False
    attempts.append(f"confirm-click={bool(clicked)}")
    if clicked:
        _settle(page, base=1200)
        after = _worldle_pin_probe(page, px, py)
        if marker_appeared(before, after):
            return after, "; ".join(attempts) + " -> MARKER"
    # (C) LAST-RESORT synthetic path dispatch (see docstring).
    try:
        res = page.evaluate("""(pt) => {
          const paths = [];
          const collect = (root) => {
            for (const p of root.querySelectorAll('svg path'))
              paths.push(p);
            for (const el of root.querySelectorAll('*'))
              if (el.shadowRoot) collect(el.shadowRoot);
          };
          collect(document);
          let best = null, bestArea = 1e18, n = 0;
          for (const p of paths) {
            const r = p.getBoundingClientRect();
            if (!(r.width > 0 && r.height > 0)) continue;
            n++;
            if (pt.x >= r.x && pt.x <= r.x + r.width &&
                pt.y >= r.y && pt.y <= r.y + r.height) {
              const a = r.width * r.height;
              if (a < bestArea) { bestArea = a; best = p; }
            }
          }
          if (!best) return {dispatched: false, n_paths: n};
          const init = {bubbles: true, cancelable: true, composed: true,
                        clientX: pt.x, clientY: pt.y, view: window,
                        button: 0, buttons: 1};
          for (const ty of ['pointerdown', 'mousedown', 'pointerup',
                            'mouseup', 'click']) {
            const Ctor = ty.startsWith('pointer') ? PointerEvent : MouseEvent;
            best.dispatchEvent(new Ctor(ty, init));
          }
          return {dispatched: true, n_paths: n, area: bestArea};
        }""", {"x": float(px), "y": float(py)}) or {}
    except Exception as e:
        res = {"dispatched": False, "error": str(e)[:60]}
    attempts.append(f"path-dispatch-synthetic-last-resort={res}")
    _settle(page, base=1200)
    after = _worldle_pin_probe(page, px, py)
    if marker_appeared(before, after):
        return after, "; ".join(attempts) + " -> MARKER"
    return after, "; ".join(attempts) + " -> no marker"


def _worldle_capital_pick_piece(pieces, px, py, max_dist=250.0):
    """Pick the country piece path to click from a probe list
    (iter-16, ANALYSIS12 §7).

    `pieces` = [{id, cls, region, cx, cy, verified}] where
    `verified` means a shadow-piercing elementFromPoint at the
    piece centre resolves to the piece itself. Returns the nearest
    VERIFIED piece within `max_dist` px of (px, py) (with its
    distance attached as "dist"), or None.

    The distance guard matters: the bbox-fit point can be far off,
    and the nearest verified piece to a bad point may be a
    NEIGHBOUR country — clicking it would burn a guess on the wrong
    country. Beyond max_dist the caller keeps the bbox-fit point
    (and logs the miss) instead of clicking a probably-wrong piece.
    Pure (unit-tested on a fixture candidate list)."""
    import math as _m
    best = None
    for p in (pieces or []):
        if not isinstance(p, dict) or not p.get("verified"):
            continue
        try:
            cx, cy = float(p.get("cx")), float(p.get("cy"))
        except (TypeError, ValueError):
            continue
        d = _m.hypot(cx - float(px), cy - float(py))
        if d <= float(max_dist) and (best is None or d < best[0]):
            best = (d, p)
    if best is None:
        return None
    out = dict(best[1])
    out["dist"] = best[0]
    return out


def _worldle_capital_piece_path(page, px, py, max_dist=250.0):
    """Resolve the capital map's COUNTRY PIECE PATH for the pin click
    (iter-16, ANALYSIS12 §7). Pure probe (no clicks).

    Root cause, from the component source itself
    (web-components.teuteuf.fr/teuteuf-geo-map.js, Svelte/D3): the
    map has NO `click` listener on its regions. Region pieces
    (`path.country-solid`, plus label `g.geo-map-label`) bind
    `pointerdown` -> `pendingClickRegion`, and the d3-zoom `end`
    handler fires `onRegionClick(pendingClickRegion)` ONLY when the
    gesture did not move (`!didZoomMove`). A click whose pointerdown
    lands on bare SVG (iter-15: top=svg, markers 4->4) never sets
    `pendingClickRegion`, so NOTHING happens — no matter how many
    `click` events are dispatched afterwards. The iter-15 bbox-fit
    point missed the actual outline path (the "largest path" it fit
    against is the sphere/graticule, not the country), so the
    pointerdown hit empty SVG. This probes (shadow-piercing — the
    pieces live inside the web component's OPEN shadow tree) every
    country piece + label, hit-tests each piece centre, and picks
    the nearest verified piece via _worldle_capital_pick_piece.
    Returns the picked piece dict (cx/cy/dist/id/region) or None.

    Still needs live confirmation: the exact piece-path id /
    region value for the played country (the pick is geometric +
    hit-test, not id-matched).

    Iter-17: the selector also covers `#pin-capital-svg path` —
    the PINPOINT map's own SVG, whose paths carry NO
    `country-solid` class, so the iter-16 selector matched nothing
    there and retargeting never happened for the map the pin is
    actually clicked on."""
    try:
        res = page.evaluate("""(pt) => {
          const roots = [document];
          const collect = root => {
            for (const el of root.querySelectorAll('*'))
              if (el.shadowRoot) {
                roots.push(el.shadowRoot); collect(el.shadowRoot);
              }
          };
          collect(document);
          const hitAt = (x, y) => {
            let el = document.elementFromPoint(x, y);
            let depth = 0;
            while (el && el.shadowRoot && depth < 5) {
              const inner = el.shadowRoot.elementFromPoint(x, y);
              if (!inner || inner === el) break;
              el = inner; depth++;
            }
            return el;
          };
          const out = [];
          for (const root of roots) {
            for (const p of root.querySelectorAll(
                'path.country-solid, g.geo-map-label, ' +
                '#pin-capital-svg path, #pin-capital-svg g')) {
              let r;
              try { r = p.getBoundingClientRect(); }
              catch (e) { continue; }
              if (!r || r.width <= 0 || r.height <= 0) continue;
              const cx = r.x + r.width / 2, cy = r.y + r.height / 2;
              const top = hitAt(cx, cy);
              const verified = !!(top && (top === p ||
                (p.contains && p.contains(top))));
              out.push({id: p.id || '',
                cls: String(p.getAttribute('class') || '').slice(0, 60),
                region: p.getAttribute('data-region-id') || '',
                cx: cx, cy: cy, verified: verified});
            }
          }
          return {pieces: out};
        }""", {"x": float(px), "y": float(py)}) or {}
    except Exception:
        return None
    return _worldle_capital_pick_piece(res.get("pieces") or [],
                                       px, py, max_dist=max_dist)


def _worldle_capital_pinpoint_probe(page, px, py):
    """Probe the PINPOINT map itself (iter-17, ANALYSIS13 §5).

    The iter-16 run clicked the bbox-fit point (1072.5, 422.7) and
    got top=svg (bare SVG, no path on top) — the point is NOT on
    the country outline (it sits in Kuwait Bay, a hole in the
    outline), so pointerdown there sets no pendingClickRegion and
    no pin registers. The same frame shows 4 blue dots on the
    map. This probe returns, in one evaluate: the pinpoint SVG's
    viewBox + screen rect; every dot (SVG circle/ellipse: centre,
    radius, fill, class, id, all data-* attrs, title/aria-label,
    and what elementFromPoint sees at its centre); the path
    count; whether the given point is inside any outline path's
    FILL (isPointInFill on the screen point mapped into SVG user
    space — the definitive on-land test, unlike bbox containment,
    which counts the bay hole as "inside"); and, when the point
    is NOT in-fill, the nearest in-fill point found by a
    distance-sorted grid search over the SVG rect (dot centres
    included as candidates). Pure probe (no clicks). Returns {}
    on any failure."""
    try:
        return page.evaluate("""(pt) => {
          // Iter-20: the iter-19 run probed dots=0 while the frame
          // showed 4 blue dots. The old probe read ONLY
          // document.querySelector('#pin-capital-svg') in the LIGHT
          // DOM and only <circle>/<ellipse> inside it: a pinpoint
          // SVG living inside the teuteuf-geo-map web component's
          // shadow tree (as the country outline does), or dots
          // rendered as <path> markers / HTML <div> pins over the
          // map, were invisible -> {found:false} / dots=[]. The
          // search below pierces every shadow root + same-origin
          // iframe document for the host, and the dot collection
          // below enumerates ALL dot-shaped elements under the pin
          // SVG / map container (circle, ellipse, path, rect, div,
          // span, button) with geometry + classes.
          const roots = [document];
          const collectRoots = root => {
            try {
              for (const el of root.querySelectorAll('*')) {
                if (el.shadowRoot) {
                  roots.push(el.shadowRoot);
                  collectRoots(el.shadowRoot);
                }
              }
            } catch (e) {}
          };
          try { collectRoots(document); } catch (e) {}
          for (const fr of Array.from(
              document.querySelectorAll('iframe'))) {
            try { const d = fr.contentDocument;
              if (d) roots.push(d); } catch (e) {}
          }
          let hostEl = null, container = null, svg = null;
          for (const rt of roots) {
            try {
              const h = rt.querySelector('#pin-capital-svg');
              if (h) { hostEl = h; container = h; break; }
            } catch (e) {}
          }
          if (hostEl) {
            if ((hostEl.tagName || '').toLowerCase() === 'svg') {
              svg = hostEl;
            } else {
              try { svg = hostEl.querySelector('svg'); } catch (e) {}
            }
            if (!svg) {
              try {
                const par = hostEl.parentElement;
                if (par) svg = par.querySelector('svg');
              } catch (e) {}
            }
          }
          if (!svg) {
            // No #pin-capital-svg anywhere (renamed /
            // restructured): fall back to an SVG whose id/class
            // names the pin or capital map.
            for (const rt of roots) {
              try {
                for (const s of rt.querySelectorAll('svg')) {
                  const idc = ((s.id || '') + ' ' +
                    (s.getAttribute('class') || '')).toLowerCase();
                  if (idc.includes('pin') || idc.includes('capital')) {
                    svg = s; container = s; break;
                  }
                }
              } catch (e) {}
              if (svg) break;
            }
          }
          if (!svg && container) svg = container;
          if (!svg) return {found: false, dots: [], n_dots: 0,
            n_paths: 0};
          const rect = svg.getBoundingClientRect();
          const dots = [];
          // Iter-18: collect the SVG <text> labels ONCE — a dot's
          // NAME usually lives NOT on the circle (the iter-17
          // probe found no data-*/title/aria on any dot, so
          // dot-name could never match) but in a nearby <text>
          // label or the parent <g>. Each dot below records its
          // nearest label text + its parent group's text/attrs.
          //
          // Iter-19: TWO probe gaps closed, both from the iter-18
          // evidence (picker ran, dots=4, method=bbox-fit-in-fill —
          // dot-name never MATCHED, not never ran):
          // (1) the old code let EVERY dot independently claim its
          // nearest <text>, so two dots near one label both claimed
          // it and the picker's ambiguity rule refused a real match
          // (Kuwait's dots sit close together on a 450px map, so
          // 60px radii overlap). Labels are now assigned GREEDILY
          // by distance — one label to at most one dot, the closest
          // one — so a shared-radius overlap no longer poisons a
          // unique match.
          // (2) more name sources: the parent <g>'s <title> child
          // (group_title), the grandparent <g>'s data/title/text,
          // sibling <text>/<title> elements sharing the parent
          // group (sibling_text), and HTML overlay labels
          // positioned over the map (overlay_label — a leaf element
          // whose rect sits INSIDE the pinpoint SVG's rect with
          // short text; the prompt heading above the map is OUTSIDE
          // the SVG rect, so it can never match).
          const labelTexts = [];
          for (const t of svg.querySelectorAll('text')) {
            let tr;
            try { tr = t.getBoundingClientRect(); }
            catch (e) { continue; }
            if (!tr || tr.width <= 0 || tr.height <= 0) continue;
            const txt = (t.textContent || '').trim();
            if (txt) labelTexts.push(
              {x: tr.x + tr.width / 2, y: tr.y + tr.height / 2,
               t: txt});
          }
          const overlayTexts = [];
          for (const el of document.querySelectorAll('body *')) {
            // SVG-subtree elements are covered by labelTexts, not
            // here; only HTML overlays count.
            if (el.closest('svg')) continue;
            let r;
            try { r = el.getBoundingClientRect(); }
            catch (e) { continue; }
            if (!r || r.width <= 0 || r.height <= 0) continue;
            if (r.height > 40) continue;
            if (r.x < rect.x - 1 || r.y < rect.y - 1 ||
                r.x + r.width > rect.x + rect.width + 1 ||
                r.y + r.height > rect.y + rect.height + 1) continue;
            const txt = (el.textContent || '')
              .replace(/\s+/g, ' ').trim();
            if (!txt || txt.length > 40) continue;
            let hasTextChild = false;
            for (const ch of el.children || []) {
              if ((ch.textContent || '').trim()) {
                hasTextChild = true; break;
              }
            }
            if (hasTextChild) continue;
            overlayTexts.push({x: r.x + r.width / 2,
                               y: r.y + r.height / 2, t: txt});
          }
          for (const c of svg.querySelectorAll('circle, ellipse')) {
            let r;
            try { r = c.getBoundingClientRect(); }
            catch (e) { continue; }
            if (!r || r.width <= 0 || r.height <= 0) continue;
            const cx = r.x + r.width / 2, cy = r.y + r.height / 2;
            const data = {};
            try { for (const k of Object.keys(c.dataset || {}))
              data[k] = c.dataset[k]; } catch (e) {}
            let title = '';
            try { const t = c.querySelector('title');
              title = t ? (t.textContent || '') : ''; } catch (e) {}
            let top = null;
            try { top = document.elementFromPoint(cx, cy); }
            catch (e) {}
            let fill = '';
            try { fill = c.getAttribute('fill') || ''; } catch (e) {}
            // Iter-23 (ANALYSIS20 §2): ALWAYS consult the
            // computed fill, even when an attribute fill exists
            // — the attribute fill must not veto the computed
            // colour (computed rgb(82,82,255) passes while the
            // attribute '#5252ff' used to fail the allowlist).
            // The attribute fill stays in `fill`; the computed
            // fill is kept separately and tested alongside it
            // in the second pass. This also repairs a missing
            // closing brace: the old `if (!fill) {` block was
            // never closed, so this whole probe string failed
            // to parse and every call threw -> dots=[].
            let computedFill = '';
            try { computedFill = getComputedStyle(c).fill || ''; }
            catch (e) {}
            // Parent <g> text/attrs: the group often wraps the
            // dot + its label together (title / data-* / text).
            // When one <g> wraps ALL dots, every dot gets the same
            // parent_text and the picker's shared-source rule
            // (iter-19) drops it as group evidence, not dot
            // evidence — it can no longer poison a unique match.
            let parentText = '', parentData = {};
            let groupTitle = '', siblingText = '';
            let grandparentData = {}, grandparentText = '';
            let grandparentTitle = '';
            try {
              const par = c.parentElement;
              if (par && par.tagName &&
                  par.tagName.toLowerCase() === 'g') {
                parentText = (par.textContent || '')
                  .replace(/\s+/g, ' ').trim().slice(0, 120);
                for (const k of Object.keys(par.dataset || {}))
                  parentData[k] = par.dataset[k];
                // <title> child of the parent group (group_title)
                // + sibling <text>/<title> elements sharing the
                // group (sibling_text).
                const sibParts = [];
                for (const sib of par.children || []) {
                  if (sib === c) continue;
                  const stag = (sib.tagName || '').toLowerCase();
                  if (stag === 'title') {
                    const tt = (sib.textContent || '')
                      .replace(/\s+/g, ' ').trim();
                    if (tt) { groupTitle = tt; sibParts.push(tt); }
                  } else if (stag === 'text') {
                    const tt = (sib.textContent || '')
                      .replace(/\s+/g, ' ').trim();
                    if (tt) sibParts.push(tt);
                  }
                }
                siblingText = sibParts.join(' ').slice(0, 120);
                // Grandparent <g> (the group above the dot's own
                // group): its data/title/text.
                const gp = par.parentElement;
                if (gp && gp.tagName &&
                    gp.tagName.toLowerCase() === 'g') {
                  grandparentText = (gp.textContent || '')
                    .replace(/\s+/g, ' ').trim().slice(0, 120);
                  for (const k of Object.keys(gp.dataset || {}))
                    grandparentData[k] = gp.dataset[k];
                  for (const sib of gp.children || []) {
                    if ((sib.tagName || '').toLowerCase() === 'title') {
                      grandparentTitle = (sib.textContent || '')
                        .replace(/\s+/g, ' ').trim();
                      break;
                    }
                  }
                }
              }
            } catch (e) {}
            dots.push({x: cx, y: cy, r: r.width / 2,
                       fill: fill, computed_fill: computedFill,
                       cls: String(
                         c.getAttribute('class') || '').slice(0, 80),
                       id: c.id || '', data: data, title: title,
                       aria: c.getAttribute('aria-label') || '',
                       top_tag: top ? top.tagName : null,
                       top_cls: top ? String(
                         top.getAttribute('class') || '')
                         .slice(0, 60) : '',
                       label: '', label_dist: null,
                       overlay_label: '', overlay_label_dist: null,
                       group_title: groupTitle,
                       sibling_text: siblingText,
                       grandparent_title: grandparentTitle,
                       grandparent_text: grandparentText,
                       grandparent_data: grandparentData,
                       parent_text: parentText,
                       parent_data: parentData,
                       // Iter-20: tag (+ empty text) so the
                       // attempt summary logs tag/class/name for
                       // EVERY dot, first-pass dots included (the
                       // second pass already records both).
                       tag: (c.tagName || '').toLowerCase(),
                       text: ''});
          }
          // Iter-20 second pass: dots rendered as <path> markers
          // or HTML <div>/<span>/<button> pins over the map (the
          // iter-19 dots=0 case: the frame's 4 blue dots were not
          // <circle>/<ellipse> under the light-DOM host). Only
          // dot-shaped elements qualify: bounding box 3..48px,
          // centre on the map rect. <path>/<rect> must also be
          // dot-named (class/id dot/marker/pin/point) or blue-ish;
          // HTML pins must be dot-named, blue-ish, or round, with
          // at most a short label as text (a long-text div is a
          // label, not a dot). Centres already collected above
          // are skipped (same dot, two representations).
          const isBlueish = s => {
            const v = String(s || '').toLowerCase()
              .replace(/\s+/g, '');
            if (/^#(0{0,2}5[0-9a-f]{3}|00[0-9a-f]|2563eb|3b82f6|1d4ed8|5252ff)$/
                .test(v)) return true;
            if (/^(blue|dodgerblue|royalblue|mediumblue)$/.test(v))
              return true;
            // Iter-23 (ANALYSIS20 §2): #5252ff is the game's
            // exact dot fill — the old allowlist omitted it, so
            // all 4 dots were rejected as not-named-not-blue. A
            // 6-digit hex is now judged by the SAME b-dominant
            // rule as rgb() (b > 100, b > r + 30, b > g - 20),
            // so any rgb(82,82,255)-equivalent fill passes, not
            // just the one literal.
            const hx = v.match(/^#([0-9a-f]{6})$/);
            if (hx) { const n = parseInt(hx[1], 16);
              const r = (n >> 16) & 255, g = (n >> 8) & 255,
                b = n & 255;
              return b > 100 && b > r + 30 && b > g - 20; }
            const m = v.match(/rgba?\((\d+),(\d+),(\d+)/);
            if (m) { const r = +m[1], g = +m[2], b = +m[3];
              return b > 100 && b > r + 30 && b > g - 20; }
            return false;
          };
          // Iter-21 (ANALYSIS18 §6.4): UNIFY this collection
          // with the markers counter (_worldle_pin_probe),
          // which DID see 4 while this probe saw 0. Two gaps
          // closed: (a) the iter-20 tag list
          // 'path, rect, div, span, button' is extended with
          // <use> / <g> / <image> dot shapes (a <use href="#pin">
          // marker or a <g class="marker"> wrapper is invisible
          // to the old tag list); (b) the search was scoped to the pin SVG
          // + its parent, so dots living anywhere else (an
          // overlay layer, a different shadow subtree) were
          // missed. A third pass below scans EVERY root (light
          // + shadow + iframe docs) with the markers-counter
          // selector set (+ dot-named classes), so any element
          // the markers counter can see is also a dot
          // candidate here. The dot-shape / on-map filters
          // still apply, so a large marker container can
          // never qualify as a dot.
          try {
            const scope = svg || container;
            const extra = [];
            const seenEl = new Set();
            const pushEl = el => {
              if (el && !seenEl.has(el)) {
                seenEl.add(el); extra.push(el);
              }
            };
            for (const el of scope.querySelectorAll(
                'path, rect, use, g, div, span, button, image'))
              pushEl(el);
            try {
              const par = container ? container.parentElement : null;
              if (par) for (const el of par.querySelectorAll(
                  'path, rect, use, g, div, span, button, image'))
                pushEl(el);
            } catch (e) {}
            for (const rt of roots) {
              try {
                for (const el of rt.querySelectorAll(
                    'svg circle, svg ellipse, svg use, ' +
                    'svg [class*=marker i], svg [class*=pin i], ' +
                    'svg [class*=dot i], .leaflet-marker-icon, ' +
                    '.maplibregl-marker, [data-marker], ' +
                    '[class*=marker i], [class*=pin i], ' +
                    '[class*=dot i]')) pushEl(el);
              } catch (e) {}
            }
            for (const c of extra) {
              let r2;
              try { r2 = c.getBoundingClientRect(); }
              catch (e) { continue; }
              if (!r2 || r2.width <= 0 || r2.height <= 0) continue;
              if (r2.width < 3 || r2.width > 48 ||
                  r2.height < 3 || r2.height > 48) continue;
              const cx2 = r2.x + r2.width / 2;
              const cy2 = r2.y + r2.height / 2;
              if (cx2 < rect.x - 4 ||
                  cx2 > rect.x + rect.width + 4 ||
                  cy2 < rect.y - 4 ||
                  cy2 > rect.y + rect.height + 4) continue;
              if (dots.some(d => Math.abs(d.x - cx2) < 3 &&
                  Math.abs(d.y - cy2) < 3)) continue;
              const tag2 = (c.tagName || '').toLowerCase();
              const clsId2 = ((c.getAttribute('class') || '') +
                ' ' + (c.id || '')).toLowerCase();
              // Iter-21: a <use> dot names itself via its
              // referenced symbol (href="#pin" / "#dot"), not
              // via class/id — fold href into the name check.
              const href2 = ((c.getAttribute('href') || '') + ' ' +
                (c.getAttribute('xlink:href') || '')).toLowerCase();
              const named2 = /(dot|marker|pin|point|capital)/
                .test(clsId2) || /(dot|marker|pin|point|capital)/
                .test(href2);
              let fill2 = '';
              try { fill2 = c.getAttribute('fill') || ''; }
              catch (e) {}
              // Iter-22 (ANALYSIS19 §2): resolve <use> href
              // targets' fill + <g> descendants' fill. A
              // <use>'s OWN computed fill is its own (usually
              // black/default) while the blue lives on the
              // referenced symbol/child — the old code tested
              // the element's own fill only, so a blue <use>
              // dot failed BOTH the name and the blue-ish
              // filters. The resolved fill OVERRIDES the own
              // fill for <use>/<g> (their own fill is not the
              // rendered colour); other tags keep their own.
              let resolved2 = '';
              try {
                if (tag2 === 'use') {
                  const hrefU = c.getAttribute('href') ||
                    c.getAttribute('xlink:href') || '';
                  const idU = hrefU.replace(/^#/, '');
                  const tgtU = idU ? document.getElementById(idU)
                    : null;
                  if (tgtU) {
                    resolved2 = tgtU.getAttribute('fill') || '';
                    if (!resolved2) {
                      try { const cfU = getComputedStyle(tgtU).fill;
                        if (cfU && cfU !== 'none') resolved2 = cfU;
                      } catch (e2) {}
                    }
                    if (!resolved2) {
                      const chU = tgtU.querySelector &&
                        tgtU.querySelector('[fill]');
                      if (chU) resolved2 = chU.getAttribute('fill') || '';
                    }
                  }
                } else if (tag2 === 'g') {
                  const chG = c.querySelector &&
                    c.querySelector('[fill]');
                  if (chG) resolved2 = chG.getAttribute('fill') || '';
                }
              } catch (e) {}
              if (resolved2) fill2 = resolved2;
              let bg2 = '', computed2 = '';
              try { const cs2 = getComputedStyle(c);
                bg2 = cs2.backgroundColor || '';
                // Iter-23 (ANALYSIS20 §2): ALWAYS read the
                // computed fill, even when an attribute fill
                // exists — attribute fill must not veto the
                // computed colour. Both are tested below.
                computed2 = cs2.fill || '';
              } catch (e) {}
              const text2 = (tag2 === 'div' || tag2 === 'span' ||
                tag2 === 'button')
                ? (c.textContent || '').replace(/\s+/g, ' ')
                    .trim().slice(0, 60) : '';
              if (tag2 === 'div' || tag2 === 'span' ||
                  tag2 === 'button') {
                if (text2.length > 40) continue;
                let round2 = false;
                try { const cs3 = getComputedStyle(c);
                  const br = parseFloat(cs3.borderRadius || '0');
                  round2 = br >= Math.min(r2.width, r2.height) / 2 - 1;
                } catch (e) {}
                if (!named2 && !isBlueish(bg2) &&
                    !isBlueish(fill2) && !isBlueish(computed2) &&
                    !round2) continue;
              } else {
                if (!named2 && !isBlueish(fill2) &&
                    !isBlueish(computed2) &&
                    !isBlueish(bg2)) continue;
              }
              let data2 = {};
              try { for (const k of Object.keys(c.dataset || {}))
                data2[k] = c.dataset[k]; } catch (e) {}
              dots.push({x: cx2, y: cy2, r: r2.width / 2,
                         fill: fill2, bg: bg2,
                         computed_fill: computed2,
                         resolved_fill: resolved2,
                         cls: String(c.getAttribute('class') || '')
                           .slice(0, 80),
                         id: c.id || '', data: data2,
                         title: '', aria: c.getAttribute('aria-label') || '',
                         top_tag: null, top_cls: '',
                         label: '', label_dist: null,
                         overlay_label: '', overlay_label_dist: null,
                         group_title: '', sibling_text: '',
                         grandparent_title: '', grandparent_text: '',
                         grandparent_data: {}, parent_text: '',
                         parent_data: {}, tag: tag2, text: text2});
            }
          } catch (e) {}
          // Greedy mutual-nearest assignment (iter-19): sort every
          // (dot, label) pair by distance and assign each label to
          // at most one dot (the closest one still unlabelled).
          // A label BETWEEN two dots no longer makes both dots
          // claim it (old behaviour) and trip the ambiguity rule.
          const assignNearest = (items, maxDist, key, distKey) => {
            const pairs = [];
            dots.forEach((d, di) => {
              items.forEach((it, ii) => {
                const dist = Math.hypot(it.x - d.x, it.y - d.y);
                if (dist <= maxDist) pairs.push({dist, di, ii});
              });
            });
            pairs.sort((a, b) => a.dist - b.dist);
            const used = new Set();
            for (const p of pairs) {
              const d = dots[p.di];
              if (d[key]) continue;
              if (used.has(p.ii)) continue;
              used.add(p.ii);
              d[key] = items[p.ii].t;
              if (distKey) d[distKey] = p.dist;
            }
          };
          assignNearest(labelTexts, 60, 'label', 'label_dist');
          assignNearest(overlayTexts, 80, 'overlay_label',
                        'overlay_label_dist');
          const paths = Array.from(svg.querySelectorAll('path'));
          let inFill = false;
          const toUser = (x, y) => {
            try {
              const ctm = svg.getScreenCTM();
              if (!ctm) return null;
              return new DOMPoint(x, y)
                .matrixTransform(ctm.inverse());
            } catch (e) { return null; }
          };
          const hitsFill = (x, y) => {
            const p = toUser(x, y);
            if (!p) return false;
            for (const path of paths) {
              try { if (path.isPointInFill(p)) return true; }
              catch (e) {}
            }
            return false;
          };
          inFill = hitsFill(pt.x, pt.y);
          let corrected = null;
          if (!inFill) {
            // Distance-sorted grid search for the nearest point
            // that IS inside a path's fill. Step 16px over the
            // SVG rect; dot centres are candidates too (they sit
            // on the map). Capped so the search stays cheap.
            const cands = [];
            const seen = new Set();
            const push = (x, y) => {
              const k = Math.round(x) + ',' + Math.round(y);
              if (seen.has(k)) return;
              seen.add(k);
              cands.push({x, y, d: Math.hypot(x - pt.x, y - pt.y)});
            };
            for (const d of dots) push(d.x, d.y);
            for (let gx = rect.x + 8; gx <= rect.x + rect.width;
                 gx += 16) {
              for (let gy = rect.y + 8; gy <= rect.y + rect.height;
                   gy += 16) push(gx, gy);
            }
            cands.sort((a, b) => a.d - b.d);
            let tested = 0;
            for (const c of cands) {
              if (tested++ > 800) break;
              if (hitsFill(c.x, c.y)) { corrected = {x: c.x, y: c.y,
                dist: c.d}; break; }
            }
          }
          return {found: true,
                  svg: {viewBox: svg.getAttribute('viewBox') || '',
                        rect: {x: rect.x, y: rect.y,
                               w: rect.width, h: rect.height}},
                  dots: dots, n_paths: paths.length,
                  n_dots: dots.length,
                  in_fill: inFill, corrected: corrected};
        }""", {"x": float(px), "y": float(py)}) or {}
    except Exception:
        return {}


def _worldle_capital_probe_diagnostics(page):
    """Diagnostics for the pinpoint probe (iter-22, ANALYSIS19
    §2): per-tag candidate counts + rejection reasons + the
    marker-counter elements (tag/class/id/fill/resolved-fill/
    rect/parent) so a future `dots=0` is diagnosable instead
    of an aggregate. Runs as a SECOND evaluate over the same
    DOM (the main probe's own enumeration is unchanged); the
    caller merges its result into the probe dict before the
    picker/summary log, and diffs marker elements against
    the probe's dots. Pure probe (no clicks). Returns {} on
    any failure; a non-dict fake result maps to {}."""
    try:
        res = page.evaluate("""/* worldle-capital-probe-diagnostics */
          () => {
          const roots = [document];
          const collectRoots = root => {
            try { for (const el of root.querySelectorAll('*')) {
              if (el.shadowRoot) {
                roots.push(el.shadowRoot);
                collectRoots(el.shadowRoot);
              }
            } } catch (e) {}
          };
          try { collectRoots(document); } catch (e) {}
          for (const fr of Array.from(
              document.querySelectorAll('iframe'))) {
            try { const d = fr.contentDocument;
              if (d) roots.push(d); } catch (e) {}
          }
          let svg = null;
          for (const rt of roots) {
            try {
              const h = rt.querySelector('#pin-capital-svg');
              if (h) {
                svg = (h.tagName || '').toLowerCase() === 'svg' ? h
                  : (h.querySelector('svg') || h);
                break;
              }
            } catch (e) {}
          }
          if (!svg) {
            for (const rt of roots) {
              try { for (const s of rt.querySelectorAll('svg')) {
                const idc = ((s.id || '') + ' ' +
                  (s.getAttribute('class') || '')).toLowerCase();
                if (idc.includes('pin') || idc.includes('capital')) {
                  svg = s; break;
                }
              } } catch (e) {}
              if (svg) break;
            }
          }
          const rect = svg ? svg.getBoundingClientRect() : null;
          const perTag = {}, rejected = {};
          const bump = (obj, k) => { obj[k] = (obj[k] || 0) + 1; };
          const isBlueish = s => {
            const v = String(s || '').toLowerCase()
              .replace(/\\s+/g, '');
            if (/^#(0{0,2}5[0-9a-f]{3}|00[0-9a-f]|2563eb|3b82f6|1d4ed8|5252ff)$/
                .test(v)) return true;
            if (/^(blue|dodgerblue|royalblue|mediumblue)$/.test(v))
              return true;
            // Iter-23 (ANALYSIS20 §2): same fix as the main
            // probe — #5252ff (the game's exact dot fill) plus
            // the 6-digit-hex b-dominant rule, so any
            // rgb(82,82,255)-equivalent fill passes.
            const hx = v.match(/^#([0-9a-f]{6})$/);
            if (hx) { const n = parseInt(hx[1], 16);
              const r = (n >> 16) & 255, g = (n >> 8) & 255,
                b = n & 255;
              return b > 100 && b > r + 30 && b > g - 20; }
            const m = v.match(/rgba?\\((\\d+),(\\d+),(\\d+)/);
            if (m) { const r = +m[1], g = +m[2], b = +m[3];
              return b > 100 && b > r + 30 && b > g - 20; }
            return false;
          };
          // Resolved fill: a <use>'s OWN computed fill is its
          // own (usually black/default) while the blue lives on
          // the referenced symbol/child; a <g>'s fill lives on
          // its descendants. Same resolution as the main probe.
          const resolvedFill = el => {
            try {
              const tag = (el.tagName || '').toLowerCase();
              if (tag === 'use') {
                const href = el.getAttribute('href') ||
                  el.getAttribute('xlink:href') || '';
                const id = href.replace(/^#/, '');
                const tgt = id ? document.getElementById(id) : null;
                if (tgt) {
                  if (tgt.getAttribute('fill')) return tgt.getAttribute('fill');
                  try { const cf = getComputedStyle(tgt).fill;
                    if (cf && cf !== 'none') return cf; } catch (e) {}
                  const ch = tgt.querySelector && tgt.querySelector('[fill]');
                  if (ch) return ch.getAttribute('fill') || '';
                }
                return '';
              }
              if (tag === 'g') {
                const ch = el.querySelector && el.querySelector('[fill]');
                if (ch) return ch.getAttribute('fill') || '';
              }
            } catch (e) {}
            return '';
          };
          const seen = new Set();
          const dotTags = 'circle, ellipse, path, rect, use, g, ' +
            'div, span, button, image';
          for (const rt of roots) {
            let els = [];
            try { els = Array.from(rt.querySelectorAll(dotTags)); }
            catch (e) {}
            for (const el of els) {
              if (seen.has(el)) continue;
              seen.add(el);
              const tag = (el.tagName || '').toLowerCase();
              bump(perTag, tag);
              let r;
              try { r = el.getBoundingClientRect(); }
              catch (e) { bump(rejected, 'rect-exception'); continue; }
              if (!r || r.width <= 0 || r.height <= 0) {
                bump(rejected, 'zero-rect'); continue; }
              if (r.width < 3 || r.width > 48 ||
                  r.height < 3 || r.height > 48) {
                bump(rejected, 'size-out-of-range'); continue; }
              if (rect) {
                const cx = r.x + r.width / 2, cy = r.y + r.height / 2;
                if (cx < rect.x - 4 || cx > rect.x + rect.width + 4 ||
                    cy < rect.y - 4 || cy > rect.y + rect.height + 4) {
                  bump(rejected, 'off-map'); continue; }
              }
              const clsId = ((el.getAttribute('class') || '') + ' ' +
                (el.id || '')).toLowerCase();
              const href = ((el.getAttribute('href') || '') + ' ' +
                (el.getAttribute('xlink:href') || '')).toLowerCase();
              const named = /(dot|marker|pin|point|capital)/
                .test(clsId) || /(dot|marker|pin|point|capital)/
                .test(href);
              let fill = '';
              try { fill = el.getAttribute('fill') || ''; } catch (e) {}
              let bg = '', computedFill = '';
              try { const cs = getComputedStyle(el);
                bg = cs.backgroundColor || '';
                // Iter-23 (ANALYSIS20 §2): ALWAYS read the
                // computed fill, even when an attribute fill
                // exists — attribute fill must not veto the
                // computed colour. Both are tested below.
                computedFill = cs.fill || '';
              } catch (e) {}
              const res = resolvedFill(el);
              if (res) fill = res;
              const text = (tag === 'div' || tag === 'span' ||
                tag === 'button')
                ? (el.textContent || '').replace(/\\s+/g, ' ')
                    .trim().slice(0, 60) : '';
              if ((tag === 'div' || tag === 'span' ||
                   tag === 'button') && text.length > 40) {
                bump(rejected, 'long-text'); continue; }
              if (!named && !isBlueish(fill) &&
                  !isBlueish(computedFill) && !isBlueish(bg)) {
                bump(rejected, 'not-named-not-blue'); continue; }
              bump(rejected, 'accepted-as-dot-candidate');
            }
          }
          // The markers counter's own elements, dumped raw:
          // the 4 frame-visible dots ARE in this set when the
          // probe says dots=0, so the diff names the filter
          // that rejected them.
          const markerSel = 'svg circle, svg ellipse, svg use, ' +
            'svg [class*=marker i], svg [class*=pin i], ' +
            '.leaflet-marker-icon, .maplibregl-marker, ' +
            '[data-marker], [class*=marker i], [class*=pin i]';
          const markerEls = [];
          const mSeen = new Set();
          for (const rt of roots) {
            let els = [];
            try { els = Array.from(rt.querySelectorAll(markerSel)); }
            catch (e) {}
            for (const el of els) {
              if (mSeen.has(el)) continue;
              mSeen.add(el);
              if (markerEls.length >= 12) break;
              let r = null;
              try { const rr = el.getBoundingClientRect();
                r = {x: Math.round(rr.x), y: Math.round(rr.y),
                     w: Math.round(rr.width),
                     h: Math.round(rr.height)}; } catch (e) {}
              let fill = '', computed = '';
              try { fill = el.getAttribute('fill') || ''; } catch (e) {}
              try { computed = getComputedStyle(el).fill || ''; }
              catch (e) {}
              const par = el.parentElement;
              markerEls.push({tag: (el.tagName || '').toLowerCase(),
                cls: String(el.getAttribute('class') || '')
                  .slice(0, 80),
                id: el.id || '', fill: fill,
                computed_fill: computed,
                resolved_fill: resolvedFill(el),
                rect: r,
                parent_tag: par ? (par.tagName || '').toLowerCase() : '',
                parent_cls: par ? String(
                  par.getAttribute('class') || '').slice(0, 60) : ''});
            }
          }
          return {per_tag: perTag, rejected: rejected,
                  marker_elements: markerEls,
                  marker_count: markerEls.length,
                  svg_found: !!svg};
        }""") or {}
    except Exception:
        return {}
    if not isinstance(res, dict):
        return {}
    return {"diag": {"per_tag": res.get("per_tag") or {},
                     "rejected": res.get("rejected") or {}},
            "marker_elements": list(res.get("marker_elements") or []),
            "marker_count": res.get("marker_count"),
            "svg_found": bool(res.get("svg_found"))}


def _worldle_capital_is_blueish(s):
    """Pure Python mirror of the pinpoint probes' JS `isBlueish`
    (iter-23, ANALYSIS20 §2). The production test lives in the
    JS evaluate strings (main probe + diagnostics); this mirror
    exists so the rule is unit-testable without a browser and
    must stay in sync with the JS: named blues, the explicit
    hex allowlist, the 6-digit-hex b-dominant rule (which admits
    #5252ff = rgb(82,82,255), the game's exact dot fill), and
    the rgb() b-dominant rule."""
    import re as _re
    v = _re.sub(r"\s+", "", str(s or "").lower())
    if _re.match(r"^#(0{0,2}5[0-9a-f]{3}|00[0-9a-f]|2563eb|3b82f6|1d4ed8|5252ff)$", v):
        return True
    if v in ("blue", "dodgerblue", "royalblue", "mediumblue"):
        return True
    m = _re.match(r"^#([0-9a-f]{6})$", v)
    if m:
        n = int(m.group(1), 16)
        r, g, b = (n >> 16) & 255, (n >> 8) & 255, n & 255
        return b > 100 and b > r + 30 and b > g - 20
    m = _re.match(r"rgba?\((\d+),(\d+),(\d+)", v)
    if m:
        r, g, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return b > 100 and b > r + 30 and b > g - 20
    return False


def _worldle_capital_is_greenish(s):
    """Pure: the feedback-green rule for a Capital dot (iter-24,
    ANALYSIS21 RC2). The game recolours the CORRECT dot blue
    (#5252ff) -> green instead of adding a marker element, so a
    green fill on a dot that was blue is the game's own grading
    signal. Named greens + the 6-digit-hex / rgb() g-dominant
    rule (g > 100, g > r + 30, g >= b - 20) — the mirror of the
    b-dominant blue rule, so yellow/orange/teal-adjacent fills
    that fail the g>r+30 test do not count."""
    import re as _re
    v = _re.sub(r"\s+", "", str(s or "").lower())
    if v in ("green", "limegreen", "lime", "forestgreen", "seagreen",
             "mediumseagreen", "darkgreen", "springgreen"):
        return True
    m = _re.match(r"^#([0-9a-f]{6})$", v)
    if m:
        n = int(m.group(1), 16)
        r, g, b = (n >> 16) & 255, (n >> 8) & 255, n & 255
        return g > 100 and g > r + 30 and g >= b - 20
    m = _re.match(r"rgba?\((\d+),(\d+),(\d+)", v)
    if m:
        r, g, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return g > 100 and g > r + 30 and g >= b - 20
    return False


def _worldle_capital_dot_recoloured(before_dots, after_dots,
                                    px=None, py=None):
    """Pure: did a Capital dot change blue -> green between two
    pinpoint-probe dot lists (iter-24, ANALYSIS21 RC2)?

    Dots are matched by position (within 3px — the same dot
    before/after). A dot counts as recoloured when NONE of its
    fill fields was greenish before, ANY fill field (fill /
    computed_fill / bg / resolved_fill) was blueish before, and
    ANY fill field is greenish after — the attribute fill may
    stay #5252ff while the computed fill turns green, so no
    single field is trusted alone. Returns (bool, detail);
    detail names the dot + before->after fills, and flags
    whether the recoloured dot is at/near the clicked point
    (within 40px) when px/py are given."""
    import math as _m
    before = [d for d in (before_dots or []) if isinstance(d, dict)]
    after = [d for d in (after_dots or []) if isinstance(d, dict)]
    _fields = ("fill", "computed_fill", "bg", "resolved_fill")

    def _fills(d):
        return [str(d.get(f) or "") for f in _fields]

    def _any(fn, d):
        return any(fn(v) for v in _fills(d))

    found = []
    for a in after:
        try:
            ax, ay = float(a.get("x")), float(a.get("y"))
        except (TypeError, ValueError):
            continue
        match = None
        for b in before:
            try:
                bx, by = float(b.get("x")), float(b.get("y"))
            except (TypeError, ValueError):
                continue
            if _m.hypot(ax - bx, ay - by) <= 3.0:
                match = b
                break
        if match is None:
            continue
        if _any(_worldle_capital_is_greenish, match):
            continue
        if not _any(_worldle_capital_is_blueish, match):
            continue
        if not _any(_worldle_capital_is_greenish, a):
            continue
        near = None
        if px is not None and py is not None:
            try:
                near = _m.hypot(ax - float(px), ay - float(py)) <= 40.0
            except (TypeError, ValueError):
                near = None
        found.append((a, match, near))
    if not found:
        return False, "no blue->green dot recolour"
    # Prefer a recoloured dot at/near the clicked point; else
    # the first recoloured dot (a correct pin recolours exactly
    # one dot — the clicked one).
    pick = next((f for f in found if f[2] is True), found[0])
    a, b, near = pick
    return True, (f"dot id={a.get('id')!r} at ({a.get('x')},"
                  f"{a.get('y')}) recoloured blue->green "
                  f"(before={_fills(b)} after={_fills(a)} "
                  f"near_click={near})")


def _worldle_capital_nearest_dot(dots, fit_px, max_dist=60.0):
    """Pure: the dot nearest `fit_px` (the projected city
    coordinates), or None (iter-22, ANALYSIS19 §2).

    Returns (dot, distance) when exactly one dot is nearest
    within `max_dist` px. Two dots tied within 5px at the
    minimum distance are AMBIGUOUS -> None (no coin-flip
    click). A dot beyond `max_dist` is never returned: the
    iter-16 worry (a dot ~140px from a bad fit point, a
    different city) is outside the radius by construction.
    The caller uses this to decide whether a path-piece
    retarget is even allowed: a dot within `max_dist` means
    the click stays on the dot, never on a path centre."""
    import math as _m
    try:
        fx, fy = float(fit_px[0]), float(fit_px[1])
    except (TypeError, ValueError, IndexError):
        return None
    scored = []
    for d in (dots or []):
        if not isinstance(d, dict):
            continue
        try:
            dx, dy = float(d.get("x")), float(d.get("y"))
        except (TypeError, ValueError):
            continue
        scored.append((_m.hypot(dx - fx, dy - fy), d))
    if not scored:
        return None
    scored.sort(key=lambda t: t[0])
    best_dist, best = scored[0]
    if best_dist > float(max_dist):
        return None
    if len(scored) > 1 and abs(scored[1][0] - best_dist) <= 5.0:
        return None
    return (best, best_dist)


def _worldle_capital_probe_diag_summary(probe):
    """Pure: one-line diagnostics summary for a pinpoint probe
    (iter-22, ANALYSIS19 §2): per-tag candidate counts +
    rejection reasons + the marker-counter elements + the
    marker-vs-dot diff. An aggregate `dots=0` line is never
    again the whole story."""
    probe = probe if isinstance(probe, dict) else {}
    diag = probe.get("diag") or {}
    per_tag = diag.get("per_tag") or {}
    rejected = diag.get("rejected") or {}
    markers = probe.get("marker_elements") or []
    dots = [d for d in (probe.get("dots") or [])
            if isinstance(d, dict)]
    marker_bits = []
    for m in markers[:6]:
        try:
            r = m.get("rect") or {}
            xy = (f"({float(r.get('x') or 0):.0f},"
                  f"{float(r.get('y') or 0):.0f} "
                  f"{float(r.get('w') or 0):.0f}x"
                  f"{float(r.get('h') or 0):.0f})")
        except (TypeError, ValueError):
            xy = "(?,?)"
        marker_bits.append(
            f"{m.get('tag') or '?'}#{m.get('id') or ''}."
            f"{str(m.get('cls') or '')[:30]} fill={m.get('fill')!r} "
            f"resolved={m.get('resolved_fill')!r} rect={xy} "
            f"parent={m.get('parent_tag') or ''}."
            f"{str(m.get('parent_cls') or '')[:30]}")
    diff = _worldle_capital_marker_dot_diff(markers, dots)
    return (f"per_tag={per_tag} rejected={rejected} "
            f"marker_elements({len(markers)})=["
            + "; ".join(marker_bits) + f"] diff={diff}")


def _worldle_capital_marker_dot_diff(marker_elements, dots):
    """Pure: diff the markers-counter elements against the
    probe's own dot candidates (iter-22). Returns a short
    string naming marker elements with no dot within 3px
    (the probe rejected or never enumerated them) and dots
    with no marker element nearby."""
    import math as _m
    markers = [m for m in (marker_elements or [])
               if isinstance(m, dict)]
    dots = [d for d in (dots or []) if isinstance(d, dict)]

    def _centre(m):
        try:
            r = m.get("rect") or {}
            return (float(r.get("x") or 0)
                    + float(r.get("w") or 0) / 2,
                    float(r.get("y") or 0)
                    + float(r.get("h") or 0) / 2)
        except (TypeError, ValueError):
            return None

    def _dot_xy(d):
        try:
            return (float(d.get("x")), float(d.get("y")))
        except (TypeError, ValueError):
            return None

    unmatched_markers = 0
    for m in markers:
        c = _centre(m)
        if c is None:
            continue
        if not any(_dot_xy(d) is not None
                   and _m.hypot(_dot_xy(d)[0] - c[0],
                                _dot_xy(d)[1] - c[1]) <= 3.0
                   for d in dots):
            unmatched_markers += 1
    unmatched_dots = 0
    for d in dots:
        xy = _dot_xy(d)
        if xy is None:
            continue
        if not any(_centre(m) is not None
                   and _m.hypot(_centre(m)[0] - xy[0],
                                _centre(m)[1] - xy[1]) <= 3.0
                   for m in markers):
            unmatched_dots += 1
    return (f"markers={len(markers)} dots={len(dots)} "
            f"markers_without_dot={unmatched_markers} "
            f"dots_without_marker={unmatched_dots}")


def _worldle_capital_pick_pin_target(probe, fit_px, cap_name):
    """Pure: choose the pin-click target from a pinpoint probe
    (iter-17, ANALYSIS13 §5). Returns {x, y, method, detail} or
    None (caller keeps the bbox-fit point and logs it unverified).

    Decision order — each step is logged by the caller:
    1. dot-name: EXACTLY ONE dot carries identifying text
       (data-* / title / aria-label / id — iter-18 also the
       nearest <text> label within 60px, the parent <g>'s text /
       data-*, and the dot's class; iter-19 also the parent
       <g>'s <title> child, sibling <text>/<title> in the same
       group, grandparent <g> data/title/text, and HTML overlay
       labels). Iter-19 shared-source rule: a (source, value)
       pair appearing on 2+ dots is GROUP evidence, not dot
       evidence, and is dropped before matching — one <g>
       wrapping all dots + labels used to make every dot
       "match" via the shared parent_text and trip the ambiguity
       rule, poisoning a unique per-dot label match (the likely
       iter-18 dot-name failure: the picker ran, dots=4, but
       fell through to bbox-fit-in-fill). The probe also assigns
       each <text> label to at most one dot (greedy by distance),
       so a label between two dots no longer makes both claim it.
       Two or more DISTINCT values naming the capital still mean
       ambiguous -> None.
    2. bbox-fit-in-fill: the bbox-fit point is inside a country
       path's fill, so pointerdown there lands on land and can
       set pendingClickRegion. Click the fit point.
    3. in-fill-search: the fit point is in a hole (Kuwait Bay);
       click the nearest in-fill point the grid search found.

    Iter-22 (ANALYSIS19 §2): the iter-16/21 "never nearest-dot"
    rule is SUPERSEDED. The frame proves the 4 dots are
    UNLABELLED candidate locations and the widget only accepts
    a click ON a dot (markers 4->4 on every off-dot click), so
    a fit point that is NOT on a dot can never register — the
    bbox-fit / in-fill points are not clickable targets at all
    on this widget. Decision order is now:
    1. dot-name (as before; ambiguous -> None).
    2. nearest-dot (NEW): the dot NEAREST the projected
       (fit) point, when it lies within 60px. The 60px radius
       is the label-assignment radius the probe already uses
       for "nearby" on this map, and the ANALYSIS19 case is
       21px (fit (1072.5,422.7) -> mid-right dot (1085,440),
       the Kuwait City position). Two dots within 5px of each
       other at the minimum distance are ambiguous -> None
       (no coin-flip click). A dot 140px away (the iter-16
       worry) is far outside the radius and is never picked.
    3. bbox-fit-in-fill / in-fill-search (as before, kept for
       completeness when NO dot is near the projected point).

    Iter-21: when a verified point (fit point in-fill, or the
    in-fill-search corrected point) already COINCIDES with an
    enumerated dot (within 12px), the target snaps to that
    dot's CENTRE (method *-dot-centre, with dot_index/dot_tag
    so the caller clicks the dot ELEMENT). This is not a
    nearest-dot guess: the point was verified independently,
    and the snap only replaces a near-dot coordinate with
    the dot element itself — the clickable target — instead
    of a bbox-fit coordinate that can miss the element.
    (Iter-22: the nearest-dot step above now fires first, so
    the 12px snap survives only for points whose nearest dot
    is >60px away yet coincides within 12px — unreachable in
    practice, kept as defense in depth.)"""
    probe = probe if isinstance(probe, dict) else {}
    if not probe.get("found"):
        return None
    try:
        fx, fy = float(fit_px[0]), float(fit_px[1])
    except (TypeError, ValueError, IndexError):
        return None
    want = _s_worldle_norm(cap_name or "")
    dots = [d for d in (probe.get("dots") or [])
            if isinstance(d, dict)]

    def _snap_to_dot(x, y, method, detail):
        # Iter-21, see docstring: snap ONLY on coincidence
        # (<=12px) with an already-verified point, never a
        # nearest-dot guess across the map.
        for d in dots:
            try:
                dx, dy = float(d.get("x")), float(d.get("y"))
            except (TypeError, ValueError):
                continue
            if abs(dx - float(x)) <= 12 and abs(dy - float(y)) <= 12:
                return {"x": dx, "y": dy,
                        "method": method + "-dot-centre",
                        "dot_index": dots.index(d),
                        "dot_tag": str(d.get("tag") or ""),
                        "detail": detail + f"; snapped to dot "
                                  f"centre ({dx:.1f}, {dy:.1f})"}
        return None
    if want:
        exact, contained = [], []

        def _pairs_for(d):
            # (source, text) pairs so the log names WHERE the name
            # came from (data-attr vs label vs parent group).
            pairs = []
            for k, v in (d.get("data") or {}).items():
                pairs.append((f"data-{k}", str(v or "")))
            for k, v in (d.get("parent_data") or {}).items():
                pairs.append((f"parent-data-{k}", str(v or "")))
            for k, v in (d.get("grandparent_data") or {}).items():
                pairs.append((f"grandparent-data-{k}", str(v or "")))
            pairs += [("title", str(d.get("title") or "")),
                      ("aria", str(d.get("aria") or "")),
                      ("id", str(d.get("id") or "")),
                      ("label", str(d.get("label") or "")),
                      ("group_title", str(d.get("group_title") or "")),
                      ("sibling_text",
                       str(d.get("sibling_text") or "")),
                      ("overlay_label",
                       str(d.get("overlay_label") or "")),
                      ("grandparent_title",
                       str(d.get("grandparent_title") or "")),
                      ("grandparent_text",
                       str(d.get("grandparent_text") or "")),
                      ("parent_text", str(d.get("parent_text") or "")),
                      # Iter-20: HTML pin dots carry the city name
                      # as their own short text (e.g. a <div> pin
                      # labelled "Kuwait City"), not in a sibling
                      # label — match it as a name source.
                      ("text", str(d.get("text") or "")),
                      ("cls", str(d.get("cls") or ""))]
            return [(src, txt) for src, txt in pairs
                    if str(txt or "").strip()]
        # Iter-19: a name source whose normalized value is shared
        # by 2+ dots identifies the GROUP, not the dot — e.g. one
        # <g> wrapping all dots + labels gives every dot the same
        # parent_text, which used to make ALL dots "match" and trip
        # the ambiguity rule, poisoning a unique per-dot label
        # match (the likely iter-18 dot-name failure: the picker
        # ran, dots=4, but returned bbox-fit-in-fill). Shared
        # (source, value) pairs are dropped BEFORE matching, so
        # only a value unique to one dot can name that dot.
        # Distinct values that both name the capital still hit the
        # ambiguity rule below (-> None), never a coin-flip click.
        _all_pairs = [_pairs_for(d) for d in dots]
        _counts = {}
        for _pairs in _all_pairs:
            for _src, _txt in _pairs:
                _key = (_src, _s_worldle_norm(_txt))
                _counts[_key] = _counts.get(_key, 0) + 1
        _kept = [[(src, txt) for src, txt in pairs
                   if _counts.get((src, _s_worldle_norm(txt)), 0) == 1]
                  for pairs in _all_pairs]
        for d, pairs in zip(dots, _kept):
            norms = [(_s_worldle_norm(t), src) for src, t in pairs]
            if any(n == want for n, _src in norms):
                exact.append(d)
            elif any(want in n for n, _src in norms if n):
                contained.append(d)
        named = exact if exact else contained
        if len(named) == 1:
            d = named[0]
            # Recompute the winning source for the log line from
            # the same shared-source-filtered pairs.
            _via = "?"
            _kept_d = _kept[dots.index(d)] if d in dots else []
            for _src, _t in _kept_d:
                _n = _s_worldle_norm(_t)
                if _n == want or (want and want in _n):
                    _via = _src
                    break
            # Iter-20: carry the matched dot's INDEX + TAG so
            # the caller can click the matched dot ELEMENT itself
            # (_worldle_capital_click_dot_element) instead of an
            # arbitrary coordinate.
            return {"x": float(d.get("x")), "y": float(d.get("y")),
                    "method": "dot-name",
                    "dot_index": dots.index(d),
                    "dot_tag": str(d.get("tag") or ""),
                    "detail": ("dot at "
                               f"({float(d.get('x')):.1f}, "
                               f"{float(d.get('y')):.1f}) names the "
                               f"capital {cap_name!r} via {_via} "
                               f"(fill={d.get('fill')!r})")}
        if len(named) > 1:
            return None
    # Iter-22 (ANALYSIS19 §2): nearest-dot to the PROJECTED
    # point — the dots are unlabelled, so dot-name cannot match,
    # and the widget only accepts a click ON a dot. This runs
    # BEFORE the bbox-fit / in-fill branches: an in-fill point
    # that is not on a dot does not register either.
    _near = _worldle_capital_nearest_dot(dots, (fx, fy))
    if _near is not None:
        _d, _dist = _near
        return {"x": float(_d.get("x")), "y": float(_d.get("y")),
                "method": "nearest-dot",
                "dot_index": dots.index(_d),
                "dot_tag": str(_d.get("tag") or ""),
                "detail": ("nearest dot "
                           f"({float(_d.get('x')):.1f}, "
                           f"{float(_d.get('y')):.1f}) is "
                           f"{_dist:.1f}px from the projected point "
                           f"({fx:.1f}, {fy:.1f}); dots are unlabelled, "
                           "widget accepts only a dot click")}
    if probe.get("in_fill"):
        _snapped = _snap_to_dot(
            fx, fy, "bbox-fit-in-fill",
            "bbox-fit point is inside a country path's fill (on land)")
        if _snapped is not None:
            return _snapped
        return {"x": fx, "y": fy, "method": "bbox-fit-in-fill",
                "detail": "bbox-fit point is inside a country "
                          "path's fill (on land)"}
    corr = probe.get("corrected") or {}
    if corr.get("x") is not None and corr.get("y") is not None:
        _snapped = _snap_to_dot(
            float(corr["x"]), float(corr["y"]), "in-fill-search",
            "bbox-fit point is NOT in-fill (hole); nearest "
            f"in-fill point {float(corr.get('dist') or 0):.1f}px away")
        if _snapped is not None:
            return _snapped
        return {"x": float(corr["x"]), "y": float(corr["y"]),
                "method": "in-fill-search",
                "detail": ("bbox-fit point is NOT in-fill (hole); "
                           f"nearest in-fill point "
                           f"{float(corr.get('dist') or 0):.1f}px away")}
    return None


def _worldle_capital_dot_attempt_summary(probe, cap_name, target):
    """Pure: one-line evidence summary of the dot-name attempt
    (fix-loop iter-19). Lists EVERY dot's name evidence, so a
    future run shows WHY dot-name matched or not instead of
    another "never attempted / zero log lines" mystery. The
    iter-18 run proved the picker RAN (log: method=bbox-fit-in-fill,
    dots=4) but dot-name never MATCHED — this line makes that
    distinction visible on every run."""
    probe = probe if isinstance(probe, dict) else {}
    dots = [d for d in (probe.get("dots") or [])
            if isinstance(d, dict)]
    method = (target or {}).get("method") \
        if isinstance(target, dict) else None
    parts = []
    for i, d in enumerate(dots):
        bits = []
        for key in ("label", "group_title", "sibling_text",
                    "overlay_label", "title", "aria", "id", "cls",
                    "text", "tag"):
            v = d.get(key)
            if str(v or "").strip():
                bits.append(f"{key}={str(v)[:40]!r}")
        for k, v in (d.get("data") or {}).items():
            if str(v or "").strip():
                bits.append(f"data-{k}={str(v)[:40]!r}")
        for k, v in (d.get("parent_data") or {}).items():
            if str(v or "").strip():
                bits.append(f"parent-data-{k}={str(v)[:40]!r}")
        for k, v in (d.get("grandparent_data") or {}).items():
            if str(v or "").strip():
                bits.append(f"grandparent-data-{k}={str(v)[:40]!r}")
        for key in ("parent_text", "grandparent_text",
                    "grandparent_title"):
            v = d.get(key)
            if str(v or "").strip():
                bits.append(f"{key}={str(v)[:40]!r}")
        try:
            xy = f"({float(d.get('x')):.0f},{float(d.get('y')):.0f})"
        except (TypeError, ValueError):
            xy = "(?,?)"
        parts.append(f"dot{i}@{xy}: "
                     + ("; ".join(bits) if bits else "no name evidence"))
    return (f"capital={cap_name!r} dots={len(dots)} "
            f"method={method} | " + " | ".join(parts)
            + " || diag: "
            + _worldle_capital_probe_diag_summary(probe))


def _worldle_capital_click_dot_element(page, px, py, cap_name):
    """Click the NAME-MATCHED dot ELEMENT itself (iter-20,
    ANALYSIS17 §2f) — not an arbitrary coordinate. The iter-19
    failure shape was: dot probe found 0 dots, so the pin fell
    back to coordinate clicks that never registered (markers
    4->4, "You have 2 guesses remaining"). The 4 dots are the
    likely clickable targets, so when the picker matches a dot
    by name, this clicks THAT element: shadow-piercing
    elementFromPoint at the dot centre (the pinpoint map may
    live in a shadow tree), walk up to the nearest dot-like
    element (circle/ellipse/path/rect/use/g, or a small element whose
    class/id names dot/marker/pin/point/capital, or data-marker), then dispatch
    the full pointer+mouse sequence ON that element with the
    dot's client coordinates (the map component listens for
    pointerdown, not click alone) plus a native element.click().
    Returns {clicked, tag, cls, id, text, top_tag, reason}.
    Pure click helper — placement is still only believed when
    the caller sees a marker/store change afterwards."""
    try:
        return page.evaluate("""/* worldle-capital-click-dot-element */
          (pt) => {
          const hitAt = (x, y) => {
            let el = document.elementFromPoint(x, y);
            let depth = 0;
            while (el && el.shadowRoot && depth < 5) {
              const inner = el.shadowRoot.elementFromPoint(x, y);
              if (!inner || inner === el) break;
              el = inner; depth++;
            }
            return el;
          };
          const top = hitAt(pt.x, pt.y);
          if (!top) return {clicked: false, reason: 'no element at point'};
          const isDotLike = e => {
            if (!e) return false;
            const tag = (e.tagName || '').toLowerCase();
            if (['circle', 'ellipse'].includes(tag)) return true;
            // Iter-21: <use> markers and <g> marker wrappers
            // are dots too (the pinpoint probe now enumerates
            // them); a data-marker element is a dot by
            // definition. Small-rect gate still applies so a
            // large wrapper can never qualify.
            if (['path', 'rect', 'use', 'g'].includes(tag)) {
              try { const r = e.getBoundingClientRect();
                if (r.width > 0 && r.width <= 48 &&
                    r.height > 0 && r.height <= 48) return true; }
              catch (err) {}
              return false;
            }
            if (e.hasAttribute && e.hasAttribute('data-marker')) {
              try { const r = e.getBoundingClientRect();
                if (r.width > 0 && r.width <= 48 &&
                    r.height > 0 && r.height <= 48) return true; }
              catch (err) {}
            }
            const clsId = ((e.getAttribute('class') || '') + ' ' +
              (e.id || '')).toLowerCase();
            if (/(dot|marker|pin|point|capital)/.test(clsId)) {
              try { const r = e.getBoundingClientRect();
                if (r.width > 0 && r.width <= 48 &&
                    r.height > 0 && r.height <= 48) return true; }
              catch (err) {}
            }
            return false;
          };
          // The topmost element may be a label/child inside the
          // dot: walk up (light DOM, then shadow host) for the
          // nearest dot-like element.
          let el = top, hops = 0;
          while (el && !isDotLike(el) && hops < 5) {
            const par = el.parentElement;
            if (par) { el = par; hops++; continue; }
            const root = el.getRootNode ? el.getRootNode() : null;
            if (root && root.host) { el = root.host; hops++; continue; }
            break;
          }
          if (!el || !isDotLike(el)) return {clicked: false,
            reason: 'no dot-like element at point',
            top_tag: top.tagName || '',
            top_cls: String(top.getAttribute('class') || '')
              .slice(0, 60)};
          const init = {bubbles: true, cancelable: true, composed: true,
            clientX: pt.x, clientY: pt.y, view: window,
            button: 0, buttons: 1};
          try { el.click(); } catch (err) {}
          for (const ty of ['pointerdown', 'mousedown', 'pointerup',
                            'mouseup', 'click']) {
            try {
              const Ctor = ty.startsWith('pointer') ? PointerEvent
                : MouseEvent;
              el.dispatchEvent(new Ctor(ty, init));
            } catch (err) {}
          }
          return {clicked: true, tag: (el.tagName || '').toLowerCase(),
            cls: String(el.getAttribute('class') || '').slice(0, 60),
            id: el.id || '',
            text: (el.textContent || '').replace(/\\s+/g, ' ')
              .trim().slice(0, 60),
            top_tag: top.tagName || ''};
        }""", {"x": float(px), "y": float(py)}) or {}
    except Exception as e:
        return {"clicked": False, "reason": f"evaluate failed: {str(e)[:80]}"}


def _s_worldle_play_capital(page, gid, day):
    """Capital: Q0 text/autosuggest name + Q1 map pinpoint.

    Q0 is verified against city-guesses. The pinpoint click is NEVER
    faked: the map projection must be exposed by the live DOM
    (data-* bounds/projection); otherwise the pin sub-question is
    SKIPPED with a full DOM dump + map outerHTML + bbox while the
    played name stays played (status partial). One pin per capital,
    prompt order; one retry allowed if the game offers "try again"
    after a wrong pin, logged as retry #2; never re-click blindly."""
    ds = day.get("date") or ""
    cap = day.get("capital") or {}
    name = cap.get("name") or ""
    if not name:
        return "skipped", [], "no capital city in day data"
    _idle_drift(page)
    ok, detail = _type_country_guess(page, page, name)
    if not ok:
        # Fall back to plain typing when the round is a bare text input
        # rather than an autosuggest (no suggestion row to pick).
        if _s_worldle_pick_input(page, ["capital", "city"]):
            ok = _s_worldle_type_picked(page, name, gid)
            detail = "plain-text fallback"
    _dbg(gid, f"worldle capital name {name!r}: ok={ok} ({detail[:70]})")
    if not ok:
        return "failed", [], f"capital name input failed ({detail})"
    _s_worldle_submit_guarded(page, gid, "capital", ds)
    _settle(page, base=2500)
    try:
        body = (page.evaluate("() => document.body.innerText") or "")
    except Exception:
        body = ""
    gs = _s_worldle_day_guesses(page, "city-guesses", ds)
    caps = day.get("capitals") or ([cap] if cap else [])
    cap_names = {_s_worldle_norm((c or {}).get("name")) for c in caps}
    q0 = [g for g in gs if (g or {}).get("question") == 0]
    name_ok = bool(q0) and _s_worldle_norm(
        (q0[-1] or {}).get("name")) in cap_names
    if not name_ok:
        return "failed", [name], \
            f"capital name not verified in city-guesses: {gs}"
    pin_present = "can you guess where" in body.lower()
    if not pin_present:
        return "attempted", [name], "name played; no pinpoint prompt shown"
    # ---- PINPOINT tab activation (fix-loop iter 14, ANALYSIS10) ----
    # The pinpoint click MUST happen with the `2. PINPOINT` tab ACTIVE.
    # Iter-13 clicked the tab, read active_after=False, and proceeded
    # anyway — with the NAME tab still active the map click registered
    # nothing (markers 4->4). The "proceed anyway" path is GONE: if
    # the tab never activates, the mode FAILS honestly with the tab
    # DOM dump as evidence, and the pin is NEVER clicked.
    _pin_active, _pin_detail = _worldle_capital_activate_pinpoint(page, gid)
    _dbg(gid, "worldle capital PINPOINT tab activation: "
              f"active={_pin_active} ({_pin_detail[:200]})")
    if not _pin_active:
        return "partial", [name], \
            f"name played; {_pin_detail}"
    # ---- Q1 pinpoint, one pin per capital, prompt order ----
    for ci, c in enumerate(caps):
        lat, lon = c.get("lat"), c.get("lon")
        if lat is None or lon is None:
            return "partial", [name], \
                f"pin skipped: capital {c.get('name')} has no coordinates"
        try:
            minfo = page.evaluate("""() => {
              const vis = e => e && e.offsetParent !== null;
              // The old code took cands[0] in DOCUMENT order — the first
              // <svg> on the page, which is a 24x24 MUI PersonRoundedIcon,
              // not the map. Filter to plausible map elements: exclude
              // 24x24 icon viewBoxes / *-Icon testids and anything too
              // small to be a map, then prefer the game's own map element
              // (teuteuf-geo-map / data-projection), else the largest.
              const all = Array.from(document.querySelectorAll(
                'teuteuf-geo-map, [data-projection], [class*=map i], ' +
                '[class*=geo-map i], svg, canvas, div[role=button]'))
                .filter(vis);
              const scored = all.map(el => {
                const r = el.getBoundingClientRect();
                return {el, area: r.width * r.height,
                        w: r.width, h: r.height};
              }).filter(c => c.area > 10000)
                .filter(c => (c.el.getAttribute('viewBox') || '') !== '0 0 24 24')
                .filter(c => !(c.el.getAttribute('data-testid') || '').endsWith('Icon'));
              scored.sort((a, b) => {
                const score = x => (x.el.tagName === 'TEUTEUF-GEO-MAP' ? 1000 : 0) +
                  (x.el.hasAttribute('data-projection') ? 500 : 0);
                return (score(b) + Math.log(b.area)) -
                  (score(a) + Math.log(a.area));
              });
              const pick = (scored[0] || {}).el || null;
              if (!pick) return null;
              const r = pick.getBoundingClientRect();
              const ds = {};
              for (const k of Object.keys(pick.dataset || {})) ds[k] = pick.dataset[k];
              return {tag: pick.tagName, cls: String(pick.className || '').slice(0, 120),
                      viewBox: pick.getAttribute('viewBox') || '',
                      bbox: {x: r.x, y: r.y, w: r.width, h: r.height},
                      dataset: ds, aspect: r.height ? r.width / r.height : 0,
                      outerHTML: (pick.outerHTML || '').slice(0, 2000)};
            }""") or {}
        except Exception:
            minfo = {}
        ds_map = (minfo or {}).get("dataset") or {}
        proj = ds_map.get("projection") or ds_map.get("proj") or ""
        bounds = None
        _bbox_override = None  # country-bbox fit uses the path bbox
        for keys in (("minLon", "maxLon", "minLat", "maxLat"),
                     ("west", "east", "south", "north"),
                     ("minLng", "maxLng", "minLat", "maxLat")):
            if all(k in ds_map for k in keys):
                try:
                    bounds = {k: float(ds_map[k]) for k in keys}
                except Exception:
                    bounds = None
                break
        if not bounds:
            # No bounds exposed by the live DOM: if the chosen element is
            # shaped like a world map (aspect ~2:1), treat its bbox as the
            # full world in equirectangular projection. Logged explicitly;
            # the pin click is still verified by marker appearance, so a
            # wrong assumption degrades to partial, not a fake pin.
            _aspect = float((minfo or {}).get("aspect") or 0)
            if 1.5 <= _aspect <= 2.6:
                bounds = {"minLon": -180.0, "maxLon": 180.0,
                          "minLat": -85.0, "maxLat": 85.0}
                proj = (f"equirect(full-world fallback; aspect={_aspect:.2f})")
                _dbg(gid, "worldle capital pin: no bounds exposed; using "
                          f"full-world equirectangular fallback (aspect={_aspect:.2f})")
        if not bounds:
            # Country-bbox -> outline-path fit (fix-loop iter 6): the
            # picked SVG IS the country outline (viewBox 0 0 1000 1000,
            # no dataset bounds), so map the country's lat/lon bbox
            # (day data, Natural Earth GeoJSON) onto the rendered
            # outline path's screen bbox. The generic px/py computation
            # below then runs with the path bbox + Mercator latitude.
            _cb = day.get("bounds") or {}
            if _cb and all(k in _cb for k in
                           ("minLon", "maxLon", "minLat", "maxLat")):
                try:
                    _path = page.evaluate("""() => {
                      const vis = e => e && e.offsetParent !== null;
                      // Shadow-piercing (iter 12): the country outline
                      // lives inside the teuteuf-geo-map web
                      // component's shadow DOM, not light DOM.
                      const allPaths = [];
                      const collect = root => {
                        for (const p of root.querySelectorAll(
                            'svg path')) allPaths.push(p);
                        for (const el of root.querySelectorAll('*'))
                          if (el.shadowRoot) collect(el.shadowRoot);
                      };
                      collect(document);
                      const paths = allPaths.filter(vis);
                      let best = null, bestArea = 0;
                      for (const pth of paths) {
                        const r = pth.getBoundingClientRect();
                        const a = r.width * r.height;
                        if (a > bestArea) { bestArea = a; best = r; }
                      }
                      if (!best) return null;
                      return {x: best.x, y: best.y, w: best.width,
                              h: best.height, n: paths.length};
                    }""") or {}
                except Exception:
                    _path = {}
                _fit = _worldle_capital_fit_px(
                    _path, _cb, lat, lon) if _path.get("w") else None
                if _fit is not None:
                    bounds = {"minLon": _cb["minLon"],
                              "maxLon": _cb["maxLon"],
                              "minLat": _cb["minLat"],
                              "maxLat": _cb["maxLat"]}
                    proj = "mercator(country-bbox fit; outline path bbox)"
                    _bbox_override = dict(_path)
                    _dbg(gid, "worldle capital pin: country-bbox fit "
                              f"path_bbox={_path} bounds={_cb} "
                              f"fit_px={_fit}")
                else:
                    _dbg(gid, "worldle capital pin: country-bbox fit "
                              f"failed (path={_path} bounds={_cb})")
        if not bounds:
            dump = _s_worldle_dom_dump(page, gid, "capital")
            _dbg(gid, "worldle capital pin: projection NOT determinable; "
                      f"map={minfo} DOM dump logged")
            return "partial", [name], \
                "name played; pin skipped (projection not determinable " \
                f"from live DOM); map={minfo}; DOM dump logged"
        # Convert (lat, lon) -> element px per the exposed projection.
        import math as _math
        bbox = _bbox_override or (minfo or {}).get("bbox") or {}
        w, h = float(bbox.get("w") or 0), float(bbox.get("h") or 0)
        west = bounds.get("minLon", bounds.get("west", bounds.get("minLng")))
        east = bounds.get("maxLon", bounds.get("east", bounds.get("maxLng")))
        south = bounds.get("minLat", bounds.get("south"))
        north = bounds.get("maxLat", bounds.get("north"))
        px = float(bbox.get("x") or 0) + (lon - west) / (east - west) * w
        if "mercator" in str(proj).lower():
            def _my(la):
                return _math.log(_math.tan(_math.pi / 4
                                           + _math.radians(la) / 2))
            yn, ys = _my(north), _my(south)
            py = float(bbox.get("y") or 0) \
                + (yn - _my(lat)) / (yn - ys) * h
        else:
            py = float(bbox.get("y") or 0) \
                + (north - lat) / (north - south) * h
        _dbg(gid, f"worldle capital pin {c.get('name')} ({lat},{lon}) "
                  f"-> px=({px:.1f},{py:.1f}) projection={proj or 'equirect(bounds)'}")
        # Iter-22 (ANALYSIS19 §2): PROBE FIRST, retarget LAST
        # (or never). The iter-16/17 order retargeted onto the
        # country-piece path centre BEFORE the probe/picker ran,
        # so the picker saw the RETARGETED point, not the
        # projected point — and the retarget moved the click
        # 82.4px east (fit (1072.5,422.7) -> piece centre
        # (1154.6,415.1)) onto the outline edge, not onto any
        # dot, hence markers 4->4. The widget only accepts a
        # click ON a dot, so: run the probe/picker on the
        # PROJECTED (bbox-fit) point; when the picker returns
        # ANY target (dot-name / nearest-dot / in-fill), use
        # it and NEVER retarget; retarget onto a piece centre
        # ONLY when the picker returns None AND no dot is
        # near the fit point (nearest-dot already covers the
        # near-dot case, so this fallback cannot steal a dot
        # click). The pre-click dot list is also the baseline
        # for the post-click dot-delta check below.
        _fit_px, _fit_py = float(px), float(py)
        _pin_probe = _worldle_capital_pinpoint_probe(page, px, py)
        # Iter-22 diagnostics: per-tag counts + rejection
        # reasons + the marker-counter elements, merged into
        # the probe before the picker/summary log, so the
        # dot-name attempt line carries the diff.
        try:
            _diag = _worldle_capital_probe_diagnostics(page)
        except Exception:
            _diag = {}
        if isinstance(_pin_probe, dict) and isinstance(_diag, dict):
            if _diag.get("diag"):
                _pin_probe["diag"] = _diag.get("diag")
            if _diag.get("marker_elements") is not None:
                _pin_probe["marker_elements"] = _diag.get(
                    "marker_elements")
        # Iter-24 RC2 baseline for the recolour check: the
        # pinpoint probe's dots (fill/computed_fill/bg) BEFORE
        # the click, so a blue->green change is measurable.
        _dots_before_recolour = list(
            (_pin_probe or {}).get("dots") or [])
        _dots_before = len((_pin_probe or {}).get("dots") or [])
        _target = _worldle_capital_pick_pin_target(
            _pin_probe, (px, py), c.get("name") or "")
        # Iter-19: the dot-name attempt is logged UNCONDITIONALLY
        # with every dot's name evidence — attempted / dot found /
        # method chosen — so a future "dot-name never attempted"
        # claim is checkable in one grep. (Iter-18: the picker ran
        # but dot-name never matched; the old log showed only
        # "pinpoint dots=4", not WHY no dot matched.)
        _dbg(gid, "worldle capital dot-name attempt: "
                  f"{_worldle_capital_dot_attempt_summary(_pin_probe, c.get('name') or '', _target)}")
        if _target is not None:
            _dbg(gid, "worldle capital pin target: "
                      f"method={_target.get('method')} "
                      f"target=({_target.get('x'):.1f}, "
                      f"{_target.get('y'):.1f}) "
                      f"fit-point was ({px:.1f},{py:.1f}); "
                      f"{_target.get('detail')} | pinpoint dots="
                      f"{_dots_before} "
                      f"(svg={(_pin_probe or {}).get('svg')}); "
                      "piece retarget SKIPPED (picker returned a "
                      "target; the click stays on the picker "
                      "target, never on a path-piece centre)")
            px, py = float(_target["x"]), float(_target["y"])
        else:
            # Picker returned None: piece retarget is allowed
            # ONLY when no dot is near the fit point (else the
            # picker would have returned nearest-dot). This is
            # the ANALYSIS19 retarget case, enforced here, not
            # in a comment: the retarget must not move the click
            # off a nearby dot.
            _near_dot = _worldle_capital_nearest_dot(
                (_pin_probe or {}).get("dots") or [], (px, py))
            if _near_dot is not None:
                _dbg(gid, "worldle capital pin target: NO picker "
                          "target but a dot is near the fit point "
                          f"({_near_dot[1]:.1f}px); keeping fit point "
                          f"({px:.1f},{py:.1f}) — piece retarget "
                          "REFUSED (fit point near a dot candidate)")
            else:
                _piece = _worldle_capital_piece_path(page, px, py)
                if _piece is not None:
                    _dbg(gid, "worldle capital pin: retargeted onto country "
                              f"piece path id={_piece.get('id')!r} "
                              f"region={_piece.get('region')!r} "
                              f"centre=({_piece.get('cx'):.1f}, "
                              f"{_piece.get('cy'):.1f}) dist="
                              f"{_piece.get('dist'):.1f} (hit-test verified); "
                              f"bbox-fit point was ({px:.1f},{py:.1f}); "
                              "no dot near the fit point, so the "
                              "piece fallback cannot steal a dot click")
                    px, py = float(_piece["cx"]), float(_piece["cy"])
                else:
                    _dbg(gid, "worldle capital pin: no hit-test-verified "
                              "country piece path near the bbox-fit point; "
                              f"keeping bbox-fit point ({px:.1f},{py:.1f}) — "
                              "live confirmation still needed: the exact "
                              "piece-path id / region for this country")
            _dbg(gid, "worldle capital pin target: NO verified "
                      "target (no name-matched dot, fit point not "
                      "in-fill, no in-fill point found); keeping "
                      f"fit/piece point ({px:.1f},{py:.1f}) — "
                      f"pinpoint dots={_dots_before} "
                      f"probe={_pin_probe}")
        before = _worldle_pin_probe(page, px, py)
        before_markers = int(before.get("markers") or 0)
        gs_before = _s_worldle_day_guesses(page, "city-guesses", ds)
        _dbg(gid, "worldle capital pin probe-v2 before: top="
                  f"{before.get('topTag')} cls={str(before.get('topCls'))[:60]!r} "
                  f"inMap={before.get('inMap')} topInPicked={before.get('topInPicked')} "
                  f"picked={before.get('pickedTag')}#{before.get('pickedId')} "
                  f"canvas={before.get('topIsCanvas')} svg={before.get('topInSvg')} "
                  f"markers={before_markers} "
                  f"(light={before.get('markers_light')} "
                  f"shadow={before.get('markers_shadow')} "
                  f"shadow_hosts={before.get('shadow_hosts')}) "
                  f"chain={before.get('chain')}")
        # Iter-20 (ANALYSIS17 §2f): when the picker matched a
        # dot BY NAME, click the matched dot ELEMENT itself
        # (_worldle_capital_click_dot_element) — the 4 dots are
        # the likely clickable targets, and coordinate clicks
        # never registered (markers 4->4). Iter-22: the same
        # element-click applies to nearest-dot and *-dot-centre
        # targets (they are dot targets too — a coordinate
        # press at a dot centre is the shape that never
        # registered). A successful element click REPLACES the
        # coordinate press below (a second press at the same
        # point could move/double-place the pin); a failed
        # element click falls back to the coordinate press.
        # Placement is still only believed when a marker/store
        # change is seen afterwards.
        _landed = bool(before.get("inMap"))
        _dot_el_clicked = False
        if (_target is not None and (
                _target.get("method") in ("dot-name", "nearest-dot")
                or str(_target.get("method") or "").endswith(
                    "-dot-centre"))):
            _el_click = _worldle_capital_click_dot_element(
                page, px, py, c.get("name") or "")
            _dot_el_clicked = bool((_el_click or {}).get("clicked"))
            _dbg(gid, "worldle capital dot element-click: "
                      f"clicked={_dot_el_clicked} "
                      f"tag={(_el_click or {}).get('tag')!r} "
                      f"cls={str((_el_click or {}).get('cls') or '')!r} "
                      f"id={(_el_click or {}).get('id')!r} "
                      f"text={str((_el_click or {}).get('text') or '')!r} "
                      f"top_tag={(_el_click or {}).get('top_tag')!r} "
                      f"reason={str((_el_click or {}).get('reason') or '')!r} "
                      f"dot_index={_target.get('dot_index')} "
                      f"dot_tag={_target.get('dot_tag')!r}")
        if not _dot_el_clicked:
            # Click the VERIFIED target with a real press: move to the
            # point, then down + up with NO mouse movement in between,
            # so d3-zoom's didZoomMove stays false and its `end`
            # handler fires onRegionClick(pendingClickRegion). A
            # synthetic dispatch never generates pointer events, and a
            # press that drags (however slightly) is treated as a zoom
            # gesture, not a click.
            try:
                page.mouse.move(px, py)
                page.mouse.down()
                page.mouse.up()
            except Exception as e:
                return "partial", [name], \
                    f"pin click failed ({str(e)[:80]}); name played"
        _settle(page, base=1200)
        after = _worldle_pin_probe(page, px, py)
        after_markers = int(after.get("markers") or 0)
        # Post-click dot-delta check: a newly placed pin renders
        # as an extra dot/circle. Unchanged dots + no marker = no pin.
        _dots_after = len((_worldle_capital_pinpoint_probe(
            page, px, py) or {}).get("dots") or [])
        _dbg(gid, "worldle capital pin dots: "
                  f"before={_dots_before} after={_dots_after} "
                  f"markers={before_markers}->{after_markers}")

        def _marker_appeared(b, a):
            if int(a.get("markers") or 0) > int(b.get("markers") or 0):
                return True
            _sig_changed = bool(a.get("sig") and b.get("sig")
                                 and a.get("sig") != b.get("sig"))
            _top_marker = ("marker" in str(a.get("topCls") or "").lower()
                           or "pin" in str(a.get("topCls") or "").lower()
                           or a.get("topTag") in ("CIRCLE", "ELLIPSE"))
            return bool(_sig_changed and _top_marker)

        def _recolour_landed():
            # Iter-24 RC2: recolour / Next-round / confetti are
            # marker-equivalent signals. Re-probes the pinpoint
            # dots + body text + confetti DOM; returns (bool,
            # detail). Only a blue->green dot change counts as
            # recolour (a wrong pin recolours red, not green).
            try:
                _after_probe = _worldle_capital_pinpoint_probe(
                    page, px, py) or {}
                _ok_r, _det_r = _worldle_capital_dot_recoloured(
                    _dots_before_recolour,
                    _after_probe.get("dots") or [], px, py)
            except Exception as _e_r:
                return False, f"recolour re-probe failed ({_e_r})"
            if _ok_r:
                return True, f"recolour: {_det_r}"
            try:
                _body_r = (page.evaluate(
                    "() => document.body.innerText") or "").lower()
            except Exception:
                _body_r = ""
            if "next round" in _body_r:
                return True, ("game shows 'Next round' after the "
                              "pin (completion signal)")
            try:
                _conf = bool(page.evaluate(
                    "() => !!document.querySelector("
                    "'[class*=confetti i], [id*=confetti i]')"))
            except Exception:
                _conf = False
            if _conf:
                return True, "confetti element present after the pin"
            return False, f"no recolour ({_det_r})"

        def _marker_or_recolour(b, a):
            # Iter-24 RC2: element-count marker OR recolour /
            # Next-round / confetti. Used everywhere the old
            # _marker_appeared gate ran, so a landed pin that
            # recolours its dot never falls through to the
            # ladder / NOT-LANDED paths.
            if _marker_appeared(b, a):
                return True
            _ok_r, _det_r = _recolour_landed()
            if _ok_r:
                _dbg(gid, "worldle capital pin recolour signal: "
                          f"{_det_r}")
            return _ok_r

        # Iter-16 (ANALYSIS12 §7): inMap is NOT placement — the
        # iter-15 click landed on the map (inMap=True, top=svg) yet
        # registered NO pin, and the fallbacks below never ran
        # because they are gated on `not _landed`. When no marker
        # appeared, run the pin ladder (path dispatch / real press /
        # confirm step) regardless of _landed.
        if not _marker_or_recolour(before, after):
            _lad_after, _lad_detail = _s_worldle_capital_pin_ladder(
                page, gid, px, py, before, _marker_or_recolour)
            _dbg(gid, f"worldle capital pin ladder: {_lad_detail}")
            if _lad_after is not None:
                after = _lad_after
                after_markers = int(after.get("markers") or 0)
                _landed = bool(after.get("inMap")) or _marker_or_recolour(
                    before, after)
        if not _landed and not _marker_or_recolour(before, after):
            # The click point is covered by a NON-map element: ONE
            # coordinate-carrying dispatch on the picked map element,
            # then re-probe. Still only believed if it lands.
            _dbg(gid, "worldle capital pin: point covered by non-map "
                      f"top={before.get('topTag')} cls="
                      f"{str(before.get('topCls'))[:60]!r}; dispatching "
                      "coordinate-carrying click on the map element")
            try:
                _dispatched = bool(page.evaluate("""(pt) => {
                  const vis = e => e && e.offsetParent !== null;
                  const els = Array.from(document.querySelectorAll(
                    'teuteuf-geo-map, [data-projection], svg, canvas'))
                    .filter(vis);
                  if (!els.length) return false;
                  let best = els[0], bestArea = -1;
                  for (const el of els) {
                    const r = el.getBoundingClientRect();
                    const a = r.width * r.height;
                    if (a > bestArea) { bestArea = a; best = el; }
                  }
                  // Pointer events first (iter 13): a web-component
                  // map may listen for pointerdown/pointerup only — a
                  // JS-dispatched mouse event never generates pointer
                  // events, so the old mousedown/mouseup/click-only
                  // dispatch could reach nothing.
                  for (const t of ['pointerdown', 'mousedown', 'pointerup',
                                    'mouseup', 'click']) {
                    const Ctor = t.startsWith('pointer') ? PointerEvent : MouseEvent;
                    best.dispatchEvent(new Ctor(t, {bubbles: true,
                      cancelable: true, composed: true,
                      clientX: pt.x, clientY: pt.y, view: window}));
                  }
                  return true;
                }""", {"x": float(px), "y": float(py)}))
            except Exception:
                _dispatched = False
            _dbg(gid, "worldle capital pin: fallback dispatch="
                      f"{_dispatched}")
            _settle(page, base=1200)
            after = _worldle_pin_probe(page, px, py)
            after_markers = int(after.get("markers") or 0)
            _landed = bool(after.get("inMap")) or _marker_or_recolour(
                before, after)
        if not _landed and not _marker_or_recolour(before, after):
            # Second fallback (iter 13): the handler may live inside
            # the map component's shadow tree, where a light-DOM
            # dispatch never reaches it. Dispatch on the deepest
            # element at the point, piercing shadow roots.
            _sh_ok, _sh_det = _s_worldle_dispatch_shadow_click(
                page, px, py)
            _dbg(gid, "worldle capital pin: shadow dispatch="
                      f"{_sh_ok} ({_sh_det})")
            _settle(page, base=1200)
            after = _worldle_pin_probe(page, px, py)
            after_markers = int(after.get("markers") or 0)
            _landed = bool(after.get("inMap")) or _marker_or_recolour(
                before, after)
        if not _landed and not _marker_or_recolour(before, after):
            _dbg(gid, f"worldle capital pin NOT LANDED probe-v2: {after}")
            return "partial", [name], \
                "pin click did not land on the map (markers " \
                f"{before_markers}->{after_markers}); name played; " \
                f"probe-v2={after}"
        # ALWAYS submit after a plausibly landed click, then arbitrate
        # with the win check. Placement verification is DECOUPLED from
        # DOM markers: a pin can land without a countable marker, and
        # the store (city-guesses delta / pin_right) is the authority.
        _s_worldle_submit_guarded(page, gid, "capital", ds)
        _settle(page, base=2000)

        def _pin_ok():
            _gs = _s_worldle_day_guesses(page, "city-guesses", ds)
            _delta = len(_gs) > len(gs_before)
            _nonq0 = [g for g in _gs
                      if (g or {}).get("question") not in (0,)]
            _right = any("right" in str((g or {}).get("name") or "")
                         .lower() for g in _nonq0)
            _mk = _marker_or_recolour(
                before, _worldle_pin_probe(page, px, py))
            return (_mk or _delta or _right), \
                {"delta": _delta, "right": _right, "marker": _mk}

        _ok, _info = _pin_ok()
        _dbg(gid, f"worldle capital pin arbitrate: pin_ok={_ok} {_info}")
        if not _ok:
            try:
                _body2 = (page.evaluate(
                    "() => document.body.innerText") or "")
            except Exception:
                _body2 = ""
            if "try again" in _body2.lower():
                # One retry, logged as retry #2.
                _dbg(gid, "worldle capital pin: game offers 'try again'; "
                          "retry #2 (one re-click + submit, same coords)")
                try:
                    page.mouse.click(px, py)
                except Exception as e:
                    return "partial", [name], \
                        f"pin retry click failed ({str(e)[:80]}); name played"
                _settle(page, base=1200)
                _s_worldle_submit_guarded(page, gid, "capital", ds)
                _settle(page, base=2000)
                _ok, _info = _pin_ok()
                _dbg(gid, "worldle capital pin retry arbitrate: "
                          f"pin_ok={_ok} {_info}")
        if not _ok:
            _after2 = _worldle_pin_probe(page, px, py)
            _dbg(gid, "worldle capital pin FAILED probe-v2: "
                      f"{_after2}")
            return "partial", [name], \
                "pin click produced no marker and no city-guesses " \
                f"delta (markers {before_markers}->{after_markers}); " \
                f"name played; probe-v2={_after2}"
        # Iter-24 RC1: EXPLICIT return on the pin_ok=True path.
        # The function previously fell off the end here ->
        # implicit None -> dispatcher TypeError, aborting the
        # 9 bonus modes after Capital. pin_ok=True means the
        # store delta / right entry / marker-or-recolour signal
        # confirmed the pin; the dispatcher win check (store
        # name_ok + pin_ok) still decides solved vs failed, so
        # this return can never upgrade a failed pin to solved.
        return "attempted", [name], \
            f"pin landed; arbitrate={_info} markers " \
            f"{before_markers}->{after_markers}"



def _s_worldle_dispatch_shadow_click(page, px, py):
    """Dispatch a full pointer+mouse click sequence on the DEEPEST
    element at (px, py), piercing shadow roots (fix-loop iter 13).

    The capital map is a teuteuf-geo-map web component: its click
    handler may live on an element INSIDE the shadow tree. A
    light-DOM dispatch on the host never reaches a shadow-internal
    listener (the event targets the host, not the inner element),
    and a real mouse click can miss when the picked coordinates hit
    an overlay. This dispatches pointerdown/mousedown/pointerup/
    mouseup/click (composed) on the deepest element at the point —
    the element the game actually listens on. Returns (bool, detail).
    Still only believed if the marker/store arbitration lands."""
    try:
        res = page.evaluate("""(pt) => {
          let el = document.elementFromPoint(pt.x, pt.y);
          let depth = 0;
          while (el && el.shadowRoot && depth < 5) {
            const inner = el.shadowRoot.elementFromPoint(pt.x, pt.y);
            if (!inner || inner === el) break;
            el = inner; depth++;
          }
          if (!el) return {dispatched: false, tag: null, depth: depth};
          const init = {bubbles: true, cancelable: true, composed: true,
                        clientX: pt.x, clientY: pt.y, view: window,
                        button: 0, buttons: 1};
          for (const ty of ['pointerdown', 'mousedown', 'pointerup',
                            'mouseup', 'click']) {
            const Ctor = ty.startsWith('pointer') ? PointerEvent : MouseEvent;
            el.dispatchEvent(new Ctor(ty, init));
          }
          return {dispatched: true, tag: el.tagName,
                  cls: String(el.className || '').slice(0, 80),
                  depth: depth};
        }""", {"x": float(px), "y": float(py)}) or {}
    except Exception as e:
        return False, f"exception: {str(e)[:80]}"
    return bool(res.get("dispatched")), str(res)


def _s_worldle_play_orientation(page, gid, day):
    """Orientation: two tabs, NOT two range inputs.

    1. DIRECTION tab: compass dial (drag along the arc in ~10 steps,
    verified against the live degree readout ±1°; nudge buttons
    "Rotate left/right" fallback) then that tab's Guess once.
    2. DISTANCE tab: slider (Home + Arrow keys in batches, verifying
    the displayed value + unit; mi->km conversion when the widget
    shows miles; nudge fallback) then Guess once.
    Bearing = great-circle initial bearing (rhumb-line bearing also
    computed + logged). Distance = haversine km. The OTHER endpoint
    must be exactly one neighbour capital from the live prompt."""
    ds = day.get("date") or ""
    try:
        body = (page.evaluate("() => document.body.innerText") or "")
    except Exception:
        body = ""
    ours_cap = ((day.get("capital") or {}).get("name") or "")
    ours_country = day.get("name") or ""
    others = []
    for b in (day.get("borders") or []):
        for cand in ((b.get("capital") or {}).get("name") or "",
                     b.get("name") or ""):
            if cand and _s_worldle_norm(cand) in _s_worldle_norm(body) \
                    and _s_worldle_norm(cand) != _s_worldle_norm(ours_cap) \
                    and _s_worldle_norm(cand) != _s_worldle_norm(ours_country):
                others.append((b, cand))
    seen = {}
    for b, cand in others:
        seen[b.get("code")] = (b, cand)
    if len(seen) != 1:
        return "skipped", [], \
            f"orientation reference ambiguous: {len(seen)} other " \
            f"endpoint(s) named in prompt (need exactly 1 neighbour)"
    (b, _cand) = next(iter(seen.values()))
    other_cap = b.get("capital") or {}
    lat1, lon1 = (day.get("capital") or {}).get("lat"), \
        (day.get("capital") or {}).get("lon")
    lat2, lon2 = other_cap.get("lat"), other_cap.get("lon")
    if None in (lat1, lon1, lat2, lon2):
        return "skipped", [], "orientation: missing capital coordinates"
    import math as _math
    p1, p2 = _math.radians(lat1), _math.radians(lat2)
    dl = _math.radians(lon2 - lon1)
    dl_w = (dl + _math.pi) % (2 * _math.pi) - _math.pi
    # Great-circle initial bearing (the game's direction target).
    gc_bearing = (_math.degrees(_math.atan2(
        _math.sin(dl) * _math.cos(p2),
        _math.cos(p1) * _math.sin(p2)
        - _math.sin(p1) * _math.cos(p2) * _math.cos(dl))) + 360) % 360
    # Rhumb-line bearing, logged for comparison (not the target).
    dpsi = _math.log(_math.tan(_math.pi / 4 + p2 / 2) /
                     _math.tan(_math.pi / 4 + p1 / 2)) if p1 != p2 else 0.0
    rhumb = (_math.degrees(_math.atan2(dl_w, dpsi)) + 360) % 360 \
        if dpsi != 0 else (90.0 if dl_w > 0 else 270.0)
    a_h = _math.sin((p2 - p1) / 2) ** 2 + _math.cos(p1) * _math.cos(p2) \
        * _math.sin(dl / 2) ** 2
    dist_km = 2 * 6371.0 * _math.asin(min(1.0, _math.sqrt(a_h)))
    _dbg(gid, f"worldle orientation: {ours_cap} -> "
              f"{other_cap.get('name')} great-circle bearing="
              f"{gc_bearing:.1f}deg rhumb bearing={rhumb:.1f}deg "
              f"dist={dist_km:.0f}km (haversine)")

    def _click_tab(kind):
        try:
            hit = page.evaluate("""(kind) => {
              const vis = e => e && e.offsetParent !== null;
              const norm = s => (s||'').toLowerCase();
              const els = Array.from(document.querySelectorAll(
                'button, [role=tab], [role=button]')).filter(vis)
                .filter(e => !e.closest('nav, header, footer'));
              const el = els.find(e => norm(e.innerText).includes(kind)) ||
                els.find(e => norm(e.getAttribute('aria-label') || '').includes(kind));
              if (!el) return false;
              el.click(); return true;
            }""", kind)
            return bool(hit)
        except Exception:
            return False

    def _readout():
        """Live degree readout near the dial (None when unreadable)."""
        try:
            v = page.evaluate("""() => {
              const vis = e => e && e.offsetParent !== null;
              const els = Array.from(document.querySelectorAll('*'))
                .filter(vis);
              for (const e of els) {
                const t = (e.innerText || '').trim();
                if (t.length > 24) continue;
                const m = t.match(/^(-?\\d+(?:\\.\\d+)?)\\s*°/);
                if (m) return parseFloat(m[1]);
              }
              const m2 = (document.body.innerText || '').match(
                /(\\d+(?:\\.\\d+)?)\\s*°/);
              return m2 ? parseFloat(m2[1]) : null;
            }""")
            return None if v is None else float(v)
        except Exception:
            return None

    def _ang_diff(a, tgt):
        return (float(tgt) - float(a) + 540.0) % 360.0 - 180.0

    # ---- 1. DIRECTION tab ----
    if not _click_tab("direction"):
        _dbg(gid, "worldle orientation: DIRECTION tab not found")
    _settle(page, base=800)
    try:
        dial = page.evaluate("""() => {
          const vis = e => e && e.offsetParent !== null;
          const els = Array.from(document.querySelectorAll('*')).filter(vis);
          let best = null, bestScore = -1;
          for (const e of els) {
            const r = e.getBoundingClientRect();
            if (r.width < 80 || r.height < 80) continue;
            const cls = ((e.className && String(e.className)) || '') +
              ' ' + (e.id || '') + ' ' +
              (e.getAttribute('aria-label') || '');
            let s = 0;
            if (/dial/i.test(cls)) s += 3;
            if (/compass/i.test(cls)) s += 3;
            if (e.getAttribute('role') === 'slider') s += 2;
            if (/°/.test(e.innerText || '')) s += 1;
            if (s > bestScore) { bestScore = s; best = e; }
          }
          if (!best) return null;
          const r = best.getBoundingClientRect();
          return {x: r.x, y: r.y, w: r.width, h: r.height,
                  text: (best.innerText || '').slice(0, 120)};
        }""") or {}
    except Exception:
        dial = {}
    if not dial:
        dump = _s_worldle_dom_dump(page, gid, "orientation")
        return "skipped", [], \
            f"orientation dial not found (DOM dump logged); derived " \
            f"bearing={gc_bearing:.1f}deg dist={dist_km:.0f}km"
    cur = _readout()
    if cur is None:
        cur = 0.0
        _dbg(gid, "worldle orientation: no degree readout found; "
                  "assuming dial starts at 0deg")
    cx, cy = float(dial["x"]) + float(dial["w"]) / 2.0, \
        float(dial["y"]) + float(dial["h"]) / 2.0
    rad = min(float(dial["w"]), float(dial["h"])) / 2.0 * 0.85

    def _pt(ang):
        a = _math.radians(ang)
        return (cx + rad * _math.sin(a), cy - rad * _math.cos(a))

    # Drag along the arc in ~10 steps (shortest arc).
    delta = _ang_diff(cur, gc_bearing)
    try:
        sx, sy = _pt(cur)
        page.mouse.move(sx, sy)
        page.wait_for_timeout(200)
        page.mouse.down()
        for i in range(1, 11):
            nx, ny = _pt(cur + delta * i / 10.0)
            page.mouse.move(nx, ny)
            page.wait_for_timeout(60)
        page.mouse.up()
    except Exception as e:
        return "failed", [], \
            f"orientation dial drag failed: {str(e)[:80]}"
    _settle(page, base=800)
    after_drag = _readout()
    if after_drag is None or abs(_ang_diff(after_drag, gc_bearing)) > 1.0:
        # Drag inert: nudge buttons fallback.
        _dbg(gid, f"worldle orientation: drag ended readout={after_drag} "
                  f"(want {gc_bearing:.1f}); trying nudge buttons")
        try:
            nudges = page.evaluate("""() => {
              const vis = e => e && e.offsetParent !== null;
              const norm = s => (s||'').toLowerCase();
              return Array.from(document.querySelectorAll('button'))
                .filter(vis).filter(e => !e.closest('nav, header, footer'))
                .filter(e => /rotate|turn|left|right|\\u2190|\\u2192|\\u27f2|\\u27f3/
                  .test(norm(e.innerText) + ' ' +
                    norm(e.getAttribute('aria-label') || '')))
                .map(e => (e.innerText || e.getAttribute('aria-label') || '').trim());
            }""") or []
        except Exception:
            nudges = []
        if not nudges:
            dump = _s_worldle_dom_dump(page, gid, "orientation")
            return "skipped", [], \
                "orientation dial drag inert (readout " \
                f"{after_drag} vs target {gc_bearing:.1f}) and no " \
                "nudge buttons found (DOM dump logged); derived " \
                f"bearing={gc_bearing:.1f}deg dist={dist_km:.0f}km"
        # Read one nudge step, then tap toward the target, re-reading
        # every ~10 taps.
        cur2 = _readout()
        step = None
        for _try in range(60):
            if cur2 is None:
                break
            rem = _ang_diff(cur2, gc_bearing)
            if abs(rem) <= 1.0:
                break
            want_right = rem > 0  # bearing increases clockwise
            try:
                page.evaluate("""(right) => {
                  const vis = e => e && e.offsetParent !== null;
                  const norm = s => (s||'').toLowerCase();
                  const btns = Array.from(document.querySelectorAll('button'))
                    .filter(vis).filter(e => !e.closest('nav, header, footer'));
                  const txt = e => norm(e.innerText) + ' ' +
                    norm(e.getAttribute('aria-label') || '');
                  const hit = btns.find(e => right ?
                    /rotate right|turn right|right|\\u2192/.test(txt(e)) :
                    /rotate left|turn left|left|\\u2190/.test(txt(e)));
                  if (hit) hit.click();
                }""", want_right)
            except Exception:
                break
            page.wait_for_timeout(150)
            nxt = _readout()
            if nxt is not None and step is None:
                step = _ang_diff(cur2, nxt)
                if step == 0:
                    break
            cur2 = nxt if nxt is not None else cur2
            if _try % 10 == 9:
                cur2 = _readout()
        if cur2 is None or abs(_ang_diff(cur2, gc_bearing)) > 1.0:
            dump = _s_worldle_dom_dump(page, gid, "orientation")
            return "skipped", [], \
                "orientation nudge buttons did not reach target " \
                f"(readout={cur2} want {gc_bearing:.1f}) (DOM dump " \
                f"logged); derived bearing={gc_bearing:.1f}deg " \
                f"dist={dist_km:.0f}km"
    _s_worldle_submit_guarded(page, gid, "orientation", ds)
    _settle(page, base=1500)
    st = (_s_worldle_ls(page, "orientation-round") or {}).get(ds) or {}
    _dbg(gid, f"worldle orientation after DIRECTION: store={st}")

    # ---- 2. DISTANCE tab ----
    if not _click_tab("distance"):
        _dbg(gid, "worldle orientation: DISTANCE tab not found")
    _settle(page, base=800)

    def _dist_display():
        try:
            return page.evaluate("""() => {
              const vis = e => e && e.offsetParent !== null;
              const slider = document.querySelector(
                'input[type=range], [role=slider]');
              const host = slider ? slider.closest('div, section, form')
                : document.body;
              const t = (host.innerText || '') + '\\n' +
                (document.body.innerText || '');
              const m = t.match(/(\\d[\\d,]*\\.?\\d*)\\s*(km|kilometers|kilometres|mi|miles)\\b/i);
              if (!m) return null;
              return {value: parseFloat(m[1].replace(/,/g, '')),
                      unit: m[2].toLowerCase()};
            }""")
        except Exception:
            return None

    disp = _dist_display()
    unit = (disp or {}).get("unit") or "km"
    target_disp = dist_km if unit.startswith("km") \
        else dist_km / 1.60934
    if unit.startswith("mi"):
        _dbg(gid, "worldle orientation: distance widget shows miles; "
                  f"target {dist_km:.0f}km = {target_disp:.1f}mi")
    dist_ok = False
    if _s_worldle_pick_input(page, ["distance", "slider", "km", "miles"],
                             include_range=True):
        ok_r, det_r = _s_worldle_drive_range(page, gid, target_disp,
                                             "orientation-distance")
        _dbg(gid, f"worldle orientation distance slider: ok={ok_r} ({det_r})")
        if ok_r:
            disp2 = _dist_display()
            if disp2:
                v_km = disp2["value"] if disp2["unit"].startswith("km") \
                    else disp2["value"] * 1.60934
                # Within one slider step (or 10% when step unknown).
                dist_ok = abs(v_km - dist_km) <= max(0.10 * dist_km, 1.0)
                _dbg(gid, f"worldle orientation distance verify: "
                          f"display={disp2} -> {v_km:.0f}km vs {dist_km:.0f}km ok={dist_ok}")
            else:
                dist_ok = True  # drive_range verified its own value
    if not dist_ok:
        # Nudge-button fallback: +/- / increase / decrease near slider.
        _dbg(gid, "worldle orientation: slider path did not verify; "
                  "trying distance nudge buttons")
        for _try in range(200):
            disp3 = _dist_display()
            if not disp3:
                break
            v_km = disp3["value"] if disp3["unit"].startswith("km") \
                else disp3["value"] * 1.60934
            if abs(v_km - dist_km) <= max(0.10 * dist_km, 1.0):
                dist_ok = True
                break
            want_up = v_km < dist_km
            try:
                page.evaluate("""(up) => {
                  const vis = e => e && e.offsetParent !== null;
                  const norm = s => (s||'').toLowerCase();
                  const btns = Array.from(document.querySelectorAll('button'))
                    .filter(vis).filter(e => !e.closest('nav, header, footer'));
                  const txt = e => norm(e.innerText) + ' ' +
                    norm(e.getAttribute('aria-label') || '');
                  const hit = btns.find(e => up ?
                    /increase|\\+|up|more/.test(txt(e)) :
                    /decrease|-|down|less/.test(txt(e)));
                  if (hit) hit.click();
                }""", want_up)
            except Exception:
                break
            page.wait_for_timeout(120)
    if not dist_ok:
        dump = _s_worldle_dom_dump(page, gid, "orientation")
        return "partial", [], \
            "orientation DIRECTION played; DISTANCE slider/nudge " \
            "undrivable (DOM dump logged); derived " \
            f"bearing={gc_bearing:.1f}deg dist={dist_km:.0f}km"
    _s_worldle_submit_guarded(page, gid, "orientation", ds)
    _settle(page, base=1500)
    st2 = (_s_worldle_ls(page, "orientation-round") or {}).get(ds) or {}
    _dbg(gid, f"worldle orientation after DISTANCE: store={st2}")
    return "attempted", [f"bearing {gc_bearing:.1f}deg",
                         f"distance {dist_km:.0f}km"], \
        f"direction+distance played {ours_cap} -> {other_cap.get('name')}"


def _s_worldle_play_flag_emblem(page, gid, day, mode):
    """Flag / Emblem: click the image whose src is the game's own URL for
    this country (flags/<code>.svg / shields/<code>.svg)."""
    kind = "flags" if mode == "flag" else "shields"
    code = (day.get("code") or "").lower()
    ok, detail = _s_worldle_click_image(page, kind, code)
    _dbg(gid, f"worldle {mode} click {code}: ok={ok} ({detail[:70]})")
    if not ok:
        return "failed", [], f"{mode} image not found ({detail})"
    _settle(page, base=2000)
    _s_worldle_submit_guarded(page, gid, mode, day.get("date") or "")
    _settle(page, base=2200)
    return "attempted", [day.get("name") or code], f"clicked {kind}/{code}"


def _click_worldle_trivia_tab(page, label):
    """Click a trivia tab button ("1. FACTS" / "2. GENERAL" / "3. HISTORY").

    Returns a dict {found, disabled, clicked}. The tab button is matched
    by its numbered label, so the GENERAL/HISTORY question sources are
    the day's factData generalFactCode/historyFactCode fillFacts (the
    facts DB), never a guess. HISTORY starts disabled and enables once
    GENERAL is answered — a disabled tab is reported, not forced.
    """
    try:
        return page.evaluate("""(lbl) => {
          const vis = e => e && e.offsetParent !== null;
          const btns = Array.from(document.querySelectorAll(
            'button, [role=tab]')).filter(vis);
          const hit = btns.find(e => new RegExp(
            '^\\\\s*\\\\d+\\\\.\\\\s*' + lbl + '\\\\s*$', 'i')
            .test((e.innerText || ''))) ||
            btns.find(e => (e.innerText || '').trim().toUpperCase() === lbl);
          if (!hit) return {found: false, disabled: false, clicked: false};
          const dis = !!hit.disabled ||
            hit.getAttribute('aria-disabled') === 'true';
          if (dis) return {found: true, disabled: true, clicked: false};
          hit.click();
          return {found: true, disabled: false, clicked: true};
        }""", label) or {}
    except Exception:
        return {"found": False, "disabled": False, "clicked": False}


def _s_worldle_play_trivia(page, gid, day):
    """Trivia: 3 questions. Game win rule is >=2 of q1/q2/q3 correct
    (runner win check relaxed from 3/3), but all 3 are still attempted.
    Two fillFacts (day JSON factData -> facts/<code>.json) + one
    BOOLEAN True/False question from the geogrid booleans. Matching:
    normalized question with blank removed, or distinctive substring
    (first 40 + last 20 chars). Displayed options are verified against
    the DB answers[] (fillFacts) or exactly {True, False} (boolean)
    before clicking. Re-scans for any unanswered displayed question
    until all answered or a full pass answers nothing with no new
    question. Answers in displayed order. After each answer: settle +
    re-read trivia-guesses, record per-question
    {index, type, answer, correct}."""
    ds = day.get("date") or ""
    facts = day.get("facts") or {}
    boolean_facts = day.get("boolean_facts") or {}
    boolean_texts = day.get("boolean_texts") or {}
    # Tokens of the day country's name, stripped before trivia
    # question matching (iter 12): the displayed question may insert
    # the country name ("Which fact about Kuwait is true?") while the
    # DB question does not, which used to break every match.
    _strip = set(_s_worldle_trivia_norm(day.get("name") or "").split())
    per_q = []
    answered_norm, skipped_norm = set(), set()
    # Fix-loop iter 6: the round is TAB-structured (FACTS / GENERAL /
    # HISTORY). The old code only ever saw the visible FACTS tab, so
    # GENERAL/HISTORY were never attempted. GENERAL = the fillFact
    # keyed by factData.generalFactCode, HISTORY = historyFactCode —
    # both already in the facts DB; the tab loop below opens each tab
    # in turn and the existing matcher answers its question.
    _fact_data = (day.get("day") or {}).get("factData") or {}
    _dbg(gid, "worldle trivia tabs: factData=" + str(_fact_data)
         + " factCodes=" + str(sorted(facts)))
    _tabs_tried = {"FACTS"}
    _tabs_deferred = set()

    def _displayed():
        # Fix-loop iter 7: the old scanner only saw questions ending in
        # "?" — but the GENERAL/HISTORY fill-in-the-blank facts are
        # STATEMENTS ending in "." ("...known by the nickname the
        # _______."), so after the FACTS tab was answered the scan
        # returned [] and the loop broke with 1/3 questions. Also match
        # a line carrying a blank run (_{3,}) and return the question
        # LINE (not the first "?" in the whole container text).
        try:
            return page.evaluate("""() => {
              const vis = e => e && e.offsetParent !== null;
              const out = [], seen = new Set();
              for (const e of Array.from(document.querySelectorAll(
                  'section, div, fieldset, form, li')).filter(vis)) {
                const t = (e.innerText || '');
                const lines = t.split('\\n').map(s => s.trim())
                  .filter(s => s.length >= 10);
                const q = lines.find(s => s.includes('?')) ||
                  lines.find(s => /_{3,}/.test(s));
                if (!q) continue;
                if (seen.has(q)) continue;
                const btns = Array.from(e.querySelectorAll(
                  'button, [role=option], [role=radio], label'))
                  .filter(vis).map(b => (b.innerText || '').trim())
                  .filter(x => x && x.length < 120);
                if (btns.length === 0) continue;
                seen.add(q);
                out.push({question: q,
                          options: Array.from(new Set(btns))});
              }
              return out;
            }""") or []
        except Exception:
            return []

    def _store_after(before_len):
        gs = _s_worldle_day_guesses(page, "trivia-guesses", ds)
        new = gs[before_len:] if len(gs) > before_len else []
        return gs, (new[-1] if new else None)

    def _try_next_tab():
        """Try the next unvisited trivia tab (GENERAL, then HISTORY).

        HISTORY starts disabled and enables once GENERAL is answered, so
        a disabled tab is deferred to a later pass, not abandoned.
        Returns True when a tab was clicked or deferred (progress made).
        Fix-loop iter 9: this used to live only at the bottom of the
        pass loop, AFTER the `if not shown: break` — so when the scan
        saw nothing (game sitting on an unvisited GENERAL tab the
        scanner missed), GENERAL/HISTORY were never attempted. It now
        also runs when `shown` is empty."""
        _advanced = False
        for _tab in ("GENERAL", "HISTORY"):
            if _tab in _tabs_tried and _tab not in _tabs_deferred:
                continue
            _res = _click_worldle_trivia_tab(page, _tab)
            _dbg(gid, f"worldle trivia tab {_tab}: {_res}")
            if _res.get("clicked"):
                _tabs_tried.add(_tab)
                _tabs_deferred.discard(_tab)
                _advanced = True
                _settle(page, base=1500)
                break
            if _res.get("disabled"):
                # HISTORY enables once GENERAL is answered; retry
                # it on a later pass instead of abandoning it.
                _tabs_deferred.add(_tab)
                _advanced = True
                continue
            _tabs_tried.add(_tab)
        return _advanced

    for _pass in range(6):
        shown = _displayed()
        # The game auto-advances tabs after an answer; the scan can
        # hit a transient empty state mid-transition. Retry a few
        # times before concluding there is nothing to answer (iter 6
        # broke immediately here, abandoning GENERAL/HISTORY).
        _empty_retries = 0
        while not shown and _empty_retries < 3:
            _settle(page, base=1200)
            shown = _displayed()
            _empty_retries += 1
        if not shown:
            # Iter 9 (ANALYSIS6 §2b): the old code broke here, before
            # the tab-try logic — so an empty scan abandoned the
            # unvisited tabs. Try the next tab; break only when no
            # tab advanced.
            if not _try_next_tab():
                break
            continue
        handled_new = False
        for _dq in shown:
            q_text = _dq.get("question") or ""
            qn = _s_worldle_trivia_norm(q_text)
            if not qn or qn in answered_norm or qn in skipped_norm:
                continue
            opts = _dq.get("options") or []
            # ---- fillFacts ----
            hit = None
            for _code, f in sorted(facts.items()):
                if _s_worldle_trivia_match(f.get("question") or "", q_text,
                                           extra_stop=_strip):
                    hit = (_code, f)
                    break
            if hit is not None:
                _code, f = hit
                ans = f.get("answer") or ""
                db_opts = {_s_worldle_trivia_norm(x)
                           for x in (f.get("answers") or [])}
                shown_opts = {_s_worldle_trivia_norm(x) for x in opts}
                # Iter 7: the old check demanded EXACT set equality,
                # but the scanned container also carries chrome
                # buttons (tabs, Give up, Next round), so a real
                # GENERAL/HISTORY question would always "NOT click".
                # The correct check is SUBSET: every DB answer must be
                # among the shown options; extra chrome is ignored.
                # The click still targets the DB answer's exact text.
                if db_opts and not db_opts <= shown_opts:
                    _dbg(gid, "worldle trivia: displayed options "
                              f"{opts} lack DB answers[] "
                              f"{f.get('answers')} for factCode={_code}; "
                              "NOT clicked")
                    skipped_norm.add(qn)
                    handled_new = True
                    continue
                before = len(_s_worldle_day_guesses(
                    page, "trivia-guesses", ds))
                ok, detail = _s_worldle_click_option_text(page, ans)
                if not ok:
                    ok, detail = _s_worldle_click_option_loose(page, ans)
                _dbg(gid, f"worldle trivia factCode={_code}: option "
                          f"{ans!r} ok={ok} ({detail[:60]})")
                if not ok:
                    skipped_norm.add(qn)
                    handled_new = True
                    continue
                _s_worldle_submit_guarded(page, gid, "trivia", ds)
                _settle(page, base=1500)
                _gs, new_g = _store_after(before)
                corr = bool(new_g) and new_g.get("answer") \
                    == new_g.get("correct")
                per_q.append({"index": len(per_q) + 1, "type": "fillFact",
                              "answer": ans, "correct": corr})
                _dbg(gid, "worldle trivia per-question: "
                          f"{per_q[-1]} (store guess={new_g})")
                answered_norm.add(qn)
                handled_new = True
                continue
            # ---- boolean ----
            opt_norm = {_s_worldle_trivia_norm(o) for o in opts}
            if opt_norm == {"true", "false"}:
                bkey, bval = None, None
                for _k, _v in boolean_facts.items():
                    _txt = boolean_texts.get(_k)
                    if _txt and _s_worldle_trivia_match(_txt, q_text,
                                                       extra_stop=_strip):
                        bkey, bval = _k, _v
                        break
                if bkey is None:
                    _dbg(gid, "worldle trivia boolean: no key->text "
                              f"mapping for displayed question {q_text!r}; "
                              f"options={opts}; boolean skipped (2/3 "
                              "fillFacts may still meet the emoji rule)")
                    skipped_norm.add(qn)
                    handled_new = True
                    continue
                ans = "True" if bval else "False"
                before = len(_s_worldle_day_guesses(
                    page, "trivia-guesses", ds))
                ok, detail = _s_worldle_click_option_text(page, ans)
                if not ok:
                    ok, detail = _s_worldle_click_option_loose(page, ans)
                _dbg(gid, f"worldle trivia boolean {bkey}={bval}: option "
                          f"{ans!r} ok={ok} ({detail[:60]})")
                if not ok:
                    skipped_norm.add(qn)
                    handled_new = True
                    continue
                _s_worldle_submit_guarded(page, gid, "trivia", ds)
                _settle(page, base=1500)
                _gs, new_g = _store_after(before)
                corr = bool(new_g) and new_g.get("answer") \
                    == new_g.get("correct")
                per_q.append({"index": len(per_q) + 1, "type": "boolean",
                              "answer": ans, "correct": corr})
                _dbg(gid, "worldle trivia per-question: "
                          f"{per_q[-1]} (store guess={new_g})")
                answered_norm.add(qn)
                handled_new = True
                continue
            # ---- boolean-choice (FACTS tab) ----
            # The FACTS tab shows 4 statements, one of which is TRUE —
            # not True/False buttons — so the branch above never fires.
            # Map each statement to its boolean key, then click the one
            # whose truth value matches the question polarity.
            # Fix-loop iter 12: the old trigger required the words
            # 'true'/'false' in the question, but the FACTS prompt can
            # ask for the 'correct' statement / 'which fact' / 'select'
            # without either word — every such question NO-MATCHed.
            # The branch still only fires AFTER fillFact + boolean
            # matching failed, and only clicks on exactly one mapped
            # candidate, so a fill-in question with word options
            # (no statement maps) still falls through to NO-MATCH.
            _qlow = _s_worldle_trivia_norm(q_text)
            if "true" in _qlow or "false" in _qlow \
                    or "correct" in _qlow or "statement" in _qlow \
                    or ("which" in _qlow and "fact" in _qlow) \
                    or "select" in _qlow:
                _want_true = not ("not true" in _qlow
                                   or "false" in _qlow
                                   or "incorrect" in _qlow)
                _mapped = []
                for _o in opts:
                    # Exact DB-text match FIRST (iter 13): the DB
                    # boolean texts ARE the game's statements, so a
                    # normalized match is certain — the token
                    # heuristic below (0.6 threshold + margin) can
                    # NO-MATCH a statement whose wording the heuristic
                    # scores below threshold (iter-12: 0 want_true
                    # candidates). Fall back to the heuristic only
                    # when no DB text matches.
                    _k = None
                    for _kk, _txt in (boolean_texts or {}).items():
                        if _txt and _s_worldle_trivia_match(
                                _txt, _o, extra_stop=_strip):
                            _k = _kk
                            break
                    if _k is None:
                        _k = _s_worldle_bool_key_for_statement(
                            _o, boolean_facts)
                    if _k is not None:
                        _mapped.append((_o, _k, bool(boolean_facts[_k])))
                _cands = [m for m in _mapped if m[2] == _want_true]
                if len(_cands) == 1:
                    _o, _k, _v = _cands[0]
                    before = len(_s_worldle_day_guesses(
                        page, "trivia-guesses", ds))
                    ok, detail = _s_worldle_click_option_text(page, _o)
                    if not ok:
                        ok, detail = _s_worldle_click_option_loose(page, _o)
                    _dbg(gid, "worldle trivia boolean-choice "
                              f"{_k}={_v} (want_true={_want_true}): option "
                              f"{_o!r} ok={ok} ({detail[:60]})")
                    if ok:
                        _s_worldle_submit_guarded(page, gid, "trivia", ds)
                        _settle(page, base=1500)
                        _gs, new_g = _store_after(before)
                        corr = bool(new_g) and new_g.get("answer") \
                            == new_g.get("correct")
                        per_q.append({"index": len(per_q) + 1,
                                      "type": "boolean-choice",
                                      "answer": _o, "correct": corr})
                        _dbg(gid, "worldle trivia per-question: "
                                  f"{per_q[-1]} (store guess={new_g})")
                        answered_norm.add(qn)
                        handled_new = True
                        continue
                    _dbg(gid, "worldle trivia boolean-choice: click "
                              f"failed for {_o!r}; skipping question")
                    skipped_norm.add(qn)
                    handled_new = True
                    continue
                if not _mapped:
                    # Iter 13: this fall-through used to be SILENT
                    # (straight to NO-MATCH) — the iter-12 "0 want_true
                    # candidates" skip left no evidence of WHY no
                    # option mapped. Log the options + keys tried.
                    _dbg(gid, "worldle trivia boolean-choice: NO option "
                              f"mapped to any boolean key (options={opts}; "
                              f"keys={sorted(boolean_facts)}); NOT clicked")
                elif not _cands:
                    # Mapped, but every mapped statement has the
                    # OPPOSITE truth value to the question polarity —
                    # not "ambiguous": the DB truth values disagree
                    # with the displayed set (or the polarity read is
                    # inverted). Never click a known-wrong statement.
                    _dbg(gid, "worldle trivia boolean-choice: 0 candidates "
                              f"want_true={_want_true} mapped={_mapped}; "
                              "all mapped statements have the opposite "
                              "truth value; NOT clicked")
                else:
                    _dbg(gid, "worldle trivia boolean-choice: ambiguous "
                              f"({len(_cands)} candidates want_true="
                              f"{_want_true} mapped={_mapped}); NOT clicked")
                    skipped_norm.add(qn)
                    handled_new = True
                    continue
            # ---- no match ----
            _dbg(gid, "worldle trivia NO-MATCH: displayed question "
                      f"{q_text!r} options={opts}; candidate DB "
                      "questions=" + "; ".join(
                          str(c) + ": " + str((f or {}).get("question"))
                          for c, f in sorted(facts.items()))
                      + "; boolean keys=" + str(sorted(boolean_facts)))
            skipped_norm.add(qn)
            handled_new = True
        if not handled_new:
            # Full pass answered nothing with no new question: try the
            # next unvisited tab (GENERAL, then HISTORY).
            if not _try_next_tab():
                break
    answered = [p["answer"] for p in per_q]
    if len(per_q) >= 2:
        return "attempted", answered, \
            f"trivia per-question={per_q}; skipped=" \
            f"{sorted(skipped_norm)}"
    if len(per_q) == 1:
        return "partial", answered, \
            f"trivia per-question={per_q}; skipped=" \
            f"{sorted(skipped_norm)}"
    return "skipped", [], \
        "trivia: no displayed question matched the facts DB or " \
        "boolean mapping (full displayed questions + candidates " \
        "logged above)"


def _s_worldle_play_landmark(page, gid, day):
    """Landmark: Q0 pick the image, Q1 name it — both keyed by the day
    JSON landmarkCode (938 for TG 2026-10-17, computed, never hardcoded).
    Image matched by the game's own landmark URL (/landmarks/<code>/);
    name matched by the landmarks-DB name (names.en)."""
    lm = day.get("landmark") or {}
    want_code = lm.get("landmarkCode")
    name = lm.get("name") or ""
    if want_code is None:
        return "skipped", [], "no landmarkData in day JSON"
    ok, detail = _s_worldle_click_image(page, "landmarks", want_code)
    _dbg(gid, f"worldle landmark image {want_code}: ok={ok} ({detail[:70]})")
    if not ok:
        return "failed", [], f"landmark image not found ({detail})"
    _settle(page, base=1500)
    _s_worldle_submit_guarded(page, gid, "landmark", day.get("date") or "")
    _settle(page, base=2000)
    if name:
        ok2, detail2 = _s_worldle_click_option_text(page, name)
        if not ok2 and _s_worldle_pick_input(page, ["landmark", "name"]):
            ok2 = _s_worldle_type_picked(page, name, gid)
            detail2 = "typed name into input"
        _dbg(gid, f"worldle landmark name {name!r}: ok={ok2} ({detail2[:60]})")
        if not ok2:
            return "partial", [f"image {want_code}"], \
                f"image picked; name option/input not found ({detail2})"
        _s_worldle_submit_guarded(page, gid, "landmark",
                                  day.get("date") or "")
        _settle(page, base=2200)
        return "attempted", [f"image {want_code}", name], \
            "image + name played for landmarkCode " + str(want_code)
    return "partial", [f"image {want_code}"], \
        "image picked; landmark name missing from landmarks DB"


def _s_worldle_language_counts(gs, qidx):
    """(total, correct) for one language sub-question from its store
    guesses: total = entries with question == qidx, correct = those
    with isCorrect. Pure helper (unit-tested)."""
    tot = sum(1 for g in (gs or {}) if (g or {}).get("question") == qidx)
    cor = sum(1 for g in (gs or {})
              if (g or {}).get("question") == qidx and (g or {}).get("isCorrect"))
    return tot, cor


def _worldle_language_phrase_check(page):
    """Detect the PHRASE sub-round: True when the `2. PHRASE` tab is the
    active tab, or the live prompt is a phrase question ("how do you
    say" / "my name is" / "translate ... say").

    Returns (is_phrase, has_phrase_tab, prompts, options). A phrase
    round is logged (prompt + options) and recorded partial without
    clicking — no phrases source exists.
    """
    try:
        _tab = page.evaluate("""() => {
          const vis = e => e && e.offsetParent !== null;
          const tabs = Array.from(document.querySelectorAll(
            'button, [role=tab]')).filter(vis);
          const hit = tabs.find(e => /^\\s*\\d+\\.\\s*PHRASE\\s*$/i
            .test(e.innerText || ''));
          if (!hit) return {has: false, active: false};
          const active = hit.getAttribute('aria-selected') === 'true' ||
            /active|selected/i.test(hit.className || '');
          return {has: true, active: !!active};
        }""") or {}
    except Exception:
        _tab = {}
    prompts = _s_worldle_prompt_texts(page)
    _low = " ".join(prompts).lower()
    _phrase_prompt = ("how do you say" in _low or "my name is" in _low
                      or ("translate" in _low and "say" in _low))
    return (bool(_tab.get("active") or _phrase_prompt),
            bool(_tab.get("has")), prompts, _s_worldle_visible_options(page))


def _worldle_language_phrase_evidence(page):
    """Full-DOM evidence for the PHRASE sub-round (iter 7).

    The old code only looked at BUTTON texts — but the phrase tab
    shows NO buttons, so its options were invisible and the round was
    abandoned with "no phrases source findable". The options may be
    div/span cards, audio buttons, or aria-labelled controls. This
    probe returns: (a) every visible text-bearing / aria-labelled /
    media element outside nav/header/footer (tag, class, role,
    aria-label, text, data-*), (b) the performance resource URLs the
    game ITSELF fetched containing 'phrase' or 'language' (the
    endpoint the game used — never a guessed URL), and (c) the parsed
    JSON of a fetched phrase-data resource, if one exists. Pure probe
    (no clicks).

    Fix-loop iter 9: also report the DRAGGABLE phrase cards
    ([draggable=true] elements with their text) — the game's own
    string for this round is "Simply drag and drop each phrase into
    the correct position", so the options may be drag cards rather
    than clickable options, and the caller must not click them blind.
    """
    try:
        return page.evaluate("""() => {
          const vis = e => e && e.offsetParent !== null;
          const els = Array.from(document.querySelectorAll(
            'body *:not(nav *):not(header *):not(footer *)'))
            .filter(vis).filter(e => !e.closest('nav, header, footer'));
          const opts = [];
          for (const e of els) {
            const t = (e.innerText || '').trim();
            const aria = e.getAttribute('aria-label') || '';
            const ds = {};
            for (const k of Object.keys(e.dataset || {}))
              ds[k] = e.dataset[k];
            const isLeaf = e.children.length === 0;
            if ((isLeaf && t && t.length < 200) || aria ||
                /^(AUDIO|VIDEO)$/.test(e.tagName) ||
                (e.tagName === 'BUTTON' && !t)) {
              opts.push({tag: e.tagName,
                         cls: String(e.className || '').slice(0, 80),
                         role: e.getAttribute('role') || '',
                         aria: aria.slice(0, 120), text: t.slice(0, 200),
                         testid: e.getAttribute('data-testid') || '',
                         data: ds});
            }
            if (opts.length >= 120) break;
          }
          const drag = Array.from(document.querySelectorAll(
            '[draggable="true"]'))
            .filter(vis).filter(e => !e.closest('nav, header, footer'))
            .map(e => ({tag: e.tagName,
                        cls: String(e.className || '').slice(0, 80),
                        text: (e.innerText || '').trim().slice(0, 200)}));
          let fetched = [];
          try {
            fetched = performance.getEntriesByType('resource')
              .map(r => r.name || '')
              .filter(u => /phrase|language/i.test(u));
          } catch (e) {}
          return {options: opts, fetched: Array.from(new Set(fetched)),
                  draggable: drag.slice(0, 20)};
        }""") or {}
    except Exception:
        return {}


def _worldle_phrase_pick(phrase_json, prompt, lang_codes, lang_names,
                         shown_texts):
    """Match the displayed phrase options against phrase data the game
    itself fetched (see _worldle_language_phrase_evidence).

    Generic, never hardcoded: recursively search the JSON for an entry
    that pairs the English prompt phrase (e.g. "My name is") with a
    translation per target language (keyed by language code or English
    name), where at least one translation equals a displayed option
    text. Returns {lang_code: expected_option_text} or None when no
    such entry exists — never a guess. Pure function (unit-tested).
    """
    import re as _re

    def _norm(s):
        return _re.sub(r"\s+", " ", str(s or "")).strip().lower()

    _prompt_norm = _norm(prompt)
    _shown = {_norm(t): t for t in (shown_texts or [])}
    _found = {}

    def _walk(node):
        if isinstance(node, dict):
            _vals = {str(k).lower(): v for k, v in node.items()}
            _en = None
            for _k in ("phrase", "english", "en", "text", "question"):
                if isinstance(_vals.get(_k), str) and _vals[_k]:
                    _en = _vals[_k]
                    break
            if _en and (_norm(_en) == _prompt_norm
                        or _prompt_norm in _norm(_en)
                        or _norm(_en) in _prompt_norm):
                for _code, _name in zip(lang_codes or [],
                                        lang_names or []):
                    for _k in (str(_code).lower(), str(_name).lower()):
                        _v = _vals.get(_k)
                        if isinstance(_v, str) and _norm(_v) in _shown:
                            _found[str(_code).lower()] = _shown[_norm(_v)]
            for _v in node.values():
                _walk(_v)
        elif isinstance(node, list):
            for _v in node:
                _walk(_v)

    _walk(phrase_json or {})
    if _found and len(_found) == len(lang_codes or []):
        return _found
    return None


def _worldle_phrase_slot_contains(page, phrase):
    """True when any drop-slot element's text contains `phrase`
    (punctuation-insensitive). Used to verify a click actually placed
    a phrase chip (iter 13): clicks do not place phrase chips, so an
    unplaced click must stop the path, not be followed by a second
    click + a submit of a no-op."""
    try:
        return bool(page.evaluate("""(phrase) => {
          const vis = e => e && e.offsetParent !== null;
          const norm = s => (s||'').toLowerCase()
            .replace(/[.,!?;:'"«»“”‘’()\-]/g, '')
            .replace(/\s+/g,' ').trim();
          const sel = '[data-slot], [class*=slot i], ' +
            '[class*=drop-zone i], [class*=dropzone i], ' +
            '[class*=drop-target i], [class*=drop--target i], ' +
            '[class*=position i], [class*=placeholder i]';
          const slots = Array.from(document.querySelectorAll(sel))
            .filter(vis)
            .filter(e => !e.closest('nav, header, footer'));
          return slots.some(s => norm(s.innerText).includes(norm(phrase)));
        }""", phrase))
    except Exception:
        return False


def _worldle_phrase_broad_slot_probe(page, names=None):
    """Broad drop-slot probe for the Worldle Q1 PHRASE round (iter 14,
    P1). The iter-13 primary probe (data-slot / slot / drop-zone /
    drop-target / position / placeholder classes) returned slots=[]
    — the slot DOM is still unknown. This probe looks for: (a) empty
    bordered boxes (computed border > 0, near-empty text); (b)
    elements with a language-name label + empty content; (c) empty
    siblings of the chip containers. Returns {slots: [{label, x, y,
    text, kind}], empty_boxes: [...]} — slots are candidates for
    drag pairing (labelled by nearby label text, else ordered);
    empty_boxes is the full diagnostic list logged on failure.
    The drop-slot DOM shape STILL needs live-browser confirmation;
    this probe is the best-evidenced widening, not a proven shape.

    Iter-16 (ANALYSIS12 §9): rule (a) used to accept ANY bordered box
    with <=40 chars of text — the phrase CHIPS themselves are bordered
    boxes with short phrase text, so the probe returned the CHIP row
    as candidate slots; the pairing then picked the lowest
    exactly-sized cluster (the chips at y≈668) and click-verify
    clicked chips as slots. When `names` (the language display
    names) is given, a bordered box with NON-EMPTY text counts as a
    slot only when its text IS a language name (a labelled empty
    slot) — a chip's phrase text never matches. Rule (c) no longer
    labels an empty sibling with the neighbouring CHIP's text
    (which made chip boxes look like labelled slots); it uses
    findLabel, which reads aria/data/sibling labels."""
    try:
        return page.evaluate("""/* WORLDLE-PHRASE-BROAD-SLOT-PROBE */
          (names) => {
          const vis = e => e && e.offsetParent !== null;
          const norm = s => (s||'').toLowerCase()
            .replace(/\\s+/g,' ').trim();
          const nameList = Array.isArray(names) ? names.map(norm) : [];
          const isName = t => {
            if (!nameList.length) return true;
            const nt = norm(t);
            if (!nt) return true;
            return nameList.some(n => n && (nt === n || nt.includes(n)));
          };
          const out = []; const seen = new Set();
          const push = (e, kind, label) => {
            const r = e.getBoundingClientRect();
            if (r.width < 20 || r.height < 12) return;
            const key = Math.round(r.x) + ',' + Math.round(r.y);
            if (seen.has(key)) return;
            seen.add(key);
            out.push({label: (label || '').trim().slice(0, 80),
                      x: r.x + r.width / 2, y: r.y + r.height / 2,
                      text: (e.innerText || '').trim().slice(0, 120),
                      kind: kind});
          };
          const all = Array.from(document.querySelectorAll('body *'))
            .filter(vis)
            .filter(e => !e.closest('nav, header, footer'));
          // Iter-15: label recovery for an empty slot box. The
          // iter-14 probe labelled a box with its OWN text ('' for
          // an empty box), so the two true slots (frame g_255:
          // dashed boxes LABELED "Arabic" / "English") came back
          // unlabelled and the pairing fell back to raw indices.
          // A slot's label lives in aria/data attributes, a
          // preceding-sibling label, a sibling label inside the
          // parent, or a short text element just above the box.
          const findLabel = (e) => {
            const attr = (e.getAttribute('aria-label') || '') ||
              (e.getAttribute('data-label') || '') ||
              (e.getAttribute('data-language') || '') ||
              (e.getAttribute('data-slot') || '');
            if (attr.trim()) return attr.trim();
            const short = s => { const t = (s || '').trim();
              return t && t.length <= 40 && t.split(/\s+/).length <= 4
                ? t : ''; };
            const sib = e.previousElementSibling;
            if (sib) { const t = short(sib.innerText);
              if (t) return t; }
            const parent = e.parentElement;
            if (parent) {
              for (const c of Array.from(parent.children || [])) {
                if (c === e) continue;
                const t = short(c.innerText);
                if (t) return t;
              }
            }
            const r = e.getBoundingClientRect();
            let best = '', bestDy = 1e9;
            for (const o of all) {
              if (o === e) continue;
              const t = short(o.innerText);
              if (!t) continue;
              const or = o.getBoundingClientRect();
              const dy = r.top - or.bottom;
              const overlap = Math.min(r.right, or.right) -
                Math.max(r.left, or.left);
              if (dy >= 0 && dy <= 80 && overlap > 0 && dy < bestDy) {
                best = t; bestDy = dy;
              }
            }
            return best;
          };
          // (a) empty bordered boxes: computed border on any side,
          // near-empty text (a drop slot waiting for a phrase).
          for (const e of all) {
            const t = (e.innerText || '').trim();
            if (t.length > 40) continue;
            let bordered = false;
            try {
              const cs = getComputedStyle(e);
              bordered = ['Top', 'Right', 'Bottom', 'Left'].some(
                s => parseFloat(cs['border' + s + 'Width'] || '0') > 0);
            } catch (err) {}
            // Iter-16: a bordered box with non-empty text counts
            // as a slot only when the text IS a language name —
            // phrase chips are bordered boxes too, and their phrase
            // text must never qualify them as slots.
            if (bordered && t.length <= 40 && isName(t))
              push(e, 'bordered-box', findLabel(e) || t);
          }
          // (b) language-name label + empty content: an element whose
          // own text is a short label and whose next sibling / child
          // is an empty box (labelled slot pair).
          for (const e of all) {
            const t = (e.innerText || '').trim();
            if (!t || t.length > 40 || t.split(/\\s+/).length > 4) continue;
            const sib = e.nextElementSibling;
            if (sib && !(sib.innerText || '').trim())
              push(sib, 'labelled-sibling', t);
            for (const ch of Array.from(e.children || [])) {
              if (!(ch.innerText || '').trim())
                push(ch, 'labelled-child', t);
            }
          }
          // (c) chip containers' siblings: siblings of elements that
          // hold phrase-like text, when the sibling itself is empty.
          for (const e of all) {
            const t = (e.innerText || '').trim();
            if (!t || t.length > 200) continue;
            const parent = e.parentElement;
            if (!parent) continue;
            for (const sib of Array.from(parent.children || [])) {
              if (sib === e) continue;
              if (!(sib.innerText || '').trim())
                // Iter-16: label from the sibling's own labels,
                // NOT the neighbouring chip's phrase text (which
                // made chip boxes look like labelled slots).
                push(sib, 'chip-sibling', findLabel(sib) || '');
            }
          }
          return {slots: out, empty_boxes: out};
        }""", list(names or [])) or {}
    except Exception as e:
        return {"slots": [], "empty_boxes": [], "error": str(e)[:100]}


def _worldle_phrase_pair_slots(slots, codes, names):
    """Pair drop slots to language codes by LABEL + GEOMETRY
    (iter-15 P1, ANALYSIS11 Language root cause).

    Iter-14 paired by RAW broad-list indices (1, 3) — header-row
    elements at y≈82 — while the true slots were the two labeled
    dashed boxes at y≈303 (frame g_255). Pairing is now:
    (1) cluster candidate slots by centre-y (tolerance 30px) and
    pick the cluster with exactly len(codes) members, preferring a
    NON-topmost cluster (the topmost row is page header chrome);
    when labels exist, a cluster whose labels cover all language
    names wins outright; (2) within the cluster, pair each code to
    the slot whose label contains its language name (Arabic→ara,
    English→eng); (3) with no usable labels, pair left-to-right
    (x-sorted) slots to codes in day.languages order.
    Returns [(code, slot_dict), ...] in codes order, or (None,
    reason) when no deterministic pairing exists. Pure (unit-tested).
    """
    codes = [str(c).lower() for c in (codes or [])]
    names = [str(n) for n in (names or [])]
    slots = [s for s in (slots or []) if isinstance(s, dict)]
    if not codes or not slots:
        return None, "no slots/codes to pair"
    if len(slots) < len(codes):
        return None, (f"only {len(slots)} slot candidates for "
                      f"{len(codes)} languages")
    name_of = {c: (names[i] if i < len(names) else c)
               for i, c in enumerate(codes)}

    def _label(s):
        return str(s.get("label") or s.get("text") or "").strip().lower()

    # (1) y-clustering (tolerance 30px), sorted top-to-bottom.
    ordered = sorted(slots, key=lambda s: (float(s.get("y") or 0),
                                           float(s.get("x") or 0)))
    clusters = []
    for s in ordered:
        y = float(s.get("y") or 0)
        if clusters and abs(y - clusters[-1][0]) <= 30:
            clusters[-1][1].append(s)
        else:
            clusters.append([y, [s]])
    # Prefer a cluster whose labels cover all language names.
    def _label_cover(cluster):
        labs = " ".join(_label(s) for s in cluster)
        return all((name_of[c].lower() in labs) for c in codes)
    chosen = None
    for _y, members in clusters:
        if len(members) == len(codes) and _label_cover(members):
            chosen = members
            break
    if chosen is None:
        # Exactly-sized clusters, non-topmost first (header row is
        # the topmost cluster); among equals prefer the LOWEST row
        # (the slot boxes sit under the prompt, below header chrome).
        exact = [m for _y, m in clusters if len(m) == len(codes)]
        if exact:
            chosen = exact[-1] if len(exact) > 1 else exact[0]
    if chosen is None:
        return None, (f"cannot pair chips to slots deterministically "
                      f"(slots={len(slots)} codes={len(codes)}; "
                      f"y-clusters={[len(m) for _y, m in clusters]}); "
                      "no exactly-sized slot row)")
    # (2) label pairing inside the chosen row. An EMPTY label
    # matches nothing ("" is a substring of every string — without
    # this guard, unlabelled slots "paired by label" in list order
    # and reported method "label").
    pairs = []
    used = set()
    labelled_all = True
    for code in codes:
        want = name_of.get(code, "").lower()
        hit = None
        for i, s in enumerate(chosen):
            if i in used:
                continue
            lab = _label(s)
            if want and lab and (want in lab or lab in want):
                hit = i
                break
        if hit is None:
            labelled_all = False
            break
        used.add(hit)
        pairs.append((code, chosen[hit]))
    if labelled_all:
        return pairs, "label"
    # (3) no usable labels: left-to-right slots to codes order.
    by_x = sorted(chosen, key=lambda s: float(s.get("x") or 0))
    return [(code, s) for code, s in zip(codes, by_x)], "geometry-x"


def _worldle_phrase_norm_text(s):
    """Unicode-preserving phrase normalisation (iter-17).

    Strip ONLY the punctuation set the narrow slot check strips,
    collapse whitespace, lowercase. Unlike ``[^a-z0-9 ]`` this
    KEEPS non-Latin scripts: norm('اسمي') == 'اسمي', so a filled
    Arabic slot verifies instead of normalising to '' (the
    iter-16 false negative: every ara placement read placed=False
    while the frame showed the Arabic slot filled)."""
    t = str(s or "").lower()
    t = re.sub(r"[.,!?;:'\"«»“”‘’()\-]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _worldle_phrase_slot_at_contains(page, slot, phrase):
    """Position-anchored slot-fill check (iter-15): the broad-probe
    slot at the SAME centre as `slot` must now contain `phrase`
    (punctuation-insensitive). Unlike _worldle_phrase_slot_contains
    (any slot) and the old narrow-index verify (wrong list for broad
    slots), this cannot false-positive on the chip's own card and
    cannot address the wrong element.

    Iter-17: the normaliser is _worldle_phrase_norm_text
    (Unicode-preserving). The old inline norm used
    ``[^a-z0-9 ]`` which DELETED the Arabic phrase entirely
    (norm('اسمي') == ''), so the Arabic slot could never verify
    even when frame-proven filled.

    The broad probe is called WITHOUT `names` on purpose: a FILLED
    slot's text is the phrase ('اسمي'), not a language name, so the
    names filter would exclude exactly the filled slots this check
    must see."""
    try:
        tx, ty = slot.get("x"), slot.get("y")
        if tx is None or ty is None:
            return False
        broad_now = _worldle_phrase_broad_slot_probe(page) or {}
        norm = _worldle_phrase_norm_text
        for cand in (broad_now.get("slots") or []):
            if (abs(float(cand.get("x") or -9999) - float(tx)) < 10
                    and abs(float(cand.get("y") or -9999) - float(ty)) < 10
                    and norm(phrase)
                    and norm(phrase) in norm(cand.get("text"))):
                return True
    except Exception:
        pass
    return False


def _worldle_phrase_drag(page, gid, picked, codes, names, prompts, ds,
                         _out=None):
    """Drag-and-drop for the Worldle Q1 PHRASE round (fix-loop iter 11).

    Iter-16: when `_out` (a dict) is given, the deterministically
    paired [(code, chip, slot)] list is stored as `_out["pairs"]`
    before any drag attempt, so the click-verify fallback can REUSE
    the LABEL pairs (with chip coords from the pre-drag probe)
    instead of re-probing + re-pairing — the iter-15 re-pairing
    paired the chip boxes as slots (geometry-x) and clicked the
    slot coords as the chip (coordinate swap).

    Clicks do NOT register on phrase options (store changed=False):
    the game's own string says "Simply drag and drop each phrase into
    the correct position". Flow: (1) verify the prompt is myNameIs
    before using day phrases; (2) ONE evaluate probing draggable chips
    (leaf text -> walk ancestors to the draggable card) + drop slots
    ([data-slot], slot/drop/target/position/placeholder classes,
    labelled boxes); (3) pair deterministically — labelled slots by
    language label, unlabelled ordered slots by prompt/day.languages
    order, else partial with the full probe (no drag); (4) drag per
    pair: attempt 1 = Playwright mouse drag in 10 steps + verify;
    attempt 2 = smooth mouse drag (25 intermediate mousemove events +
    hover) + verify; attempt 3 = in-page DragEvent sequence with a
    real DataTransfer (dragstart/dragover/drop/dragend) + verify;
    (5) submit via _s_worldle_submit_guarded, arbitrate with the store
    win check.
    Returns (status, detail); status "click-fallback" when no
    draggable chips exist (caller falls back to the click path).
    Needs live-browser verification for the slot DOM + which drag
    primitive the widget accepts — the probe is logged on failure."""
    import time as _t
    _low_prompts = " ".join(prompts or []).lower()
    if "my name is" not in _low_prompts:
        return ("partial", "phrase prompt is not myNameIs "
                f"(prompts={prompts}); day phrases not used")
    try:
        _probe = page.evaluate("""(picked) => {
          const vis = e => e && e.offsetParent !== null;
          // Punctuation-insensitive (iter 12): a chip reading
          // 'Nɔɔyɛ la me.' must match the day phrase without its
          // trailing period, and vice versa.
          const norm = s => (s||'').toLowerCase()
            .replace(/[.,!?;:"'«»“”‘’()\\-]/g, '')
            .replace(/\\s+/g,' ').trim();
          const chips = [];
          for (const [code, phrase] of Object.entries(picked || {})) {
            const leaves = Array.from(document.querySelectorAll('body *'))
              .filter(vis)
              .filter(e => !e.closest('nav, header, footer'))
              .filter(e => e.children.length === 0)
              .filter(e => norm(e.innerText) === norm(phrase));
            let card = leaves[0] || null;
            let draggable = false;
            if (card) {
              let n = card;
              while (n && n !== document.body) {
                if (n.getAttribute && n.getAttribute('draggable') === 'true') {
                  card = n; draggable = true; break;
                }
                n = n.parentElement;
              }
              if (card && card.getAttribute &&
                  card.getAttribute('draggable') === 'true')
                draggable = true;
            }
            if (card) {
              const r = card.getBoundingClientRect();
              chips.push({code: code, phrase: phrase, draggable: draggable,
                          x: r.x + r.width / 2, y: r.y + r.height / 2});
            } else {
              chips.push({code: code, phrase: phrase, draggable: false,
                          x: null, y: null});
            }
          }
          const slotSel = '[data-slot], [class*=slot i], ' +
            '[class*=drop-zone i], [class*=dropzone i], ' +
            '[class*=drop-target i], [class*=drop--target i], ' +
            '[class*=position i], [class*=placeholder i]';
          const slots = [];
          const seen = new Set();
          for (const e of Array.from(document.querySelectorAll(slotSel))
               .filter(vis)
               .filter(e => !e.closest('nav, header, footer'))) {
            const r = e.getBoundingClientRect();
            if (r.width < 10 || r.height < 10) continue;
            const key = Math.round(r.x) + ',' + Math.round(r.y);
            if (seen.has(key)) continue;
            seen.add(key);
            const label = ((e.getAttribute('aria-label') || '') ||
                           (e.getAttribute('data-label') || '') ||
                           (e.getAttribute('data-language') || '') ||
                           (e.getAttribute('data-slot') || '') ||
                           (e.innerText || '').split('\\n')[0] || '')
              .trim().slice(0, 80);
            slots.push({label: label, x: r.x + r.width / 2,
                        y: r.y + r.height / 2,
                        text: (e.innerText || '').trim().slice(0, 120)});
          }
          return {chips: chips, slots: slots};
        }""", picked) or {}
    except Exception as e:
        return ("partial", f"phrase probe failed: {str(e)[:100]}")
    _chips = _probe.get("chips") or []
    _slots = _probe.get("slots") or []
    _dbg(gid, f"worldle phrase probe: chips={_chips} slots={_slots}")
    # Iter 13: a chip need NOT carry draggable=true to be dragged —
    # the widget may implement drag via pointer events on a plain
    # element (iter-12: chips were found by text, none was marked
    # draggable, so the drag path was skipped and the click path
    # clicked into slots that stayed empty). Text-found chips are
    # drag candidates; attempts 1-2 use real mouse events, which
    # work for pointer-based drag too. Only when NO chip is found
    # by text does the click fallback run.
    if not any(c.get("x") is not None for c in _chips):
        return ("click-fallback", "no phrase chips found by text")
    _dbg(gid, "worldle phrase chips: draggable-marked="
              f"{sum(1 for c in _chips if c.get('draggable'))}/"
              f"{len(_chips)} (text-found chips are dragged anyway)")
    _using_broad_slots = False
    if not _slots:
        # Iter-14 P1: the primary slot probe returned slots=[] in
        # iter-13 and the drag path returned "partial", so the
        # click-verify fallback was NEVER reached. Run the broad
        # probe (empty bordered boxes / labelled empty content /
        # chip-container siblings); if it finds candidate slots,
        # pair + drag against them. If it also finds nothing,
        # return "click-fallback" (NOT "partial") so the caller
        # reaches the click path, which is still guarded by
        # click-verify (an unplaced click stops the path). The
        # drop-slot DOM shape still needs live confirmation.
        _broad = _worldle_phrase_broad_slot_probe(page, names)
        _broad_slots = _broad.get("slots") or []
        _dbg(gid, "worldle phrase broad slot probe: "
                  f"slots={_broad_slots} "
                  f"empty_boxes={len(_broad.get('empty_boxes') or [])} "
                  f"error={_broad.get('error') or ''}")
        if _broad_slots:
            _slots = _broad_slots
            _using_broad_slots = True
            _dbg(gid, "worldle phrase: using broad-probe slots for "
                      f"drag pairing (primary probe found none)")
        else:
            return ("click-fallback",
                    "draggable phrase chips found but no drop slots "
                    f"in DOM (primary probe + broad probe both empty; "
                    f"broad empty_boxes={_broad.get('empty_boxes') or []}); "
                    "falling back to click path (click-verify guards)")
    # ---- Deterministic pairing (iter-15): LABEL + GEOMETRY, never
    # raw broad-list indices (iter-14 paired indices 1, 3 — header-row
    # elements — while the true slots were the labeled boxes at
    # y≈303). See _worldle_phrase_pair_slots.
    _pair_res, _pair_how = _worldle_phrase_pair_slots(_slots, codes, names)
    if _pair_res is None:
        return ("partial", f"cannot pair chips to slots: {_pair_how}; "
                           f"probe={_probe}")
    _chip_by_code = {str(c.get("code") or "").lower(): c for c in _chips}
    _pairs = []  # (code, chip, slot_dict)
    for _code, _slot in _pair_res:
        _chip = _chip_by_code.get(str(_code).lower())
        if _chip is None:
            return ("partial", "pairing found a slot for "
                               f"{_code} but no chip by that code; "
                               f"probe={_probe}")
        _pairs.append((_code, _chip, _slot))
    _pair_desc = [(c, {"label": s.get("label"), "x": s.get("x"),
                       "y": s.get("y")}) for c, _, s in _pairs]
    _dbg(gid, f"worldle phrase pairs ({_pair_how}): {_pair_desc}")
    # Iter-16: publish the pairs (with PRE-DRAG chip coords) for
    # the click-verify fallback — it reuses them verbatim instead
    # of re-probing + re-pairing (the iter-15 re-pairing paired
    # chip boxes as slots and swapped chip/slot coords).
    if isinstance(_out, dict):
        _out["pairs"] = list(_pairs)
        _out["pair_how"] = _pair_how

    def _verify(_slot, _phrase):
        # Position-anchored broad-probe check first (works for narrow
        # and broad slots alike — the slot at the SAME centre must now
        # contain the phrase), then the narrow any-slot check.
        if _worldle_phrase_slot_at_contains(page, _slot, _phrase):
            return True
        return bool(_worldle_phrase_slot_contains(page, _phrase))

    # Iter-17 (ANALYSIS13 §6): attempt ALL pairs. The old loop
    # returned "click-fallback" on the FIRST unplaced pair, so when
    # the (then broken) verifier lied about ara, eng was NEVER
    # attempted. Now every pair is attempted, per-pair placement is
    # recorded, and only pairs that actually failed go to the
    # click-verify fallback (via _out["placed"], which the caller
    # passes through as _placed_in).
    _placed_by_code = {}
    _drag_failed = []
    for _code, _chip, _slot in _pairs:
        _phrase = _chip.get("phrase") or ""
        _placed = False
        # Attempt 1: Playwright mouse drag, 10 steps + verify.
        try:
            page.mouse.move(_chip["x"], _chip["y"])
            page.mouse.down()
            for _step in range(1, 11):
                page.mouse.move(
                    _chip["x"] + (_slot["x"] - _chip["x"]) * _step / 10.0,
                    _chip["y"] + (_slot["y"] - _chip["y"]) * _step / 10.0,
                    steps=1)
                page.wait_for_timeout(60)
            page.mouse.up()
            page.wait_for_timeout(800)
        except Exception as e:
            _dbg(gid, f"worldle phrase mouse drag failed for {_code}: "
                      f"{str(e)[:80]}")
        _placed = _verify(_slot, _phrase)
        _dbg(gid, f"worldle phrase drag mouse {_code} -> slot "
                  f"label={_slot.get('label')!r} ({_slot.get('x')}, "
                  f"{_slot.get('y')}) placed={_placed}")
        if not _placed:
            # Attempt 2: smooth mouse drag — one move call with 25
            # intermediate mousemove events + a hover pause over the
            # slot before release. Some DnD widgets only fire dragover
            # on a continuous move stream, which the 10 discrete
            # moves of attempt 1 do not produce.
            try:
                page.mouse.move(_chip["x"], _chip["y"])
                page.mouse.down()
                page.mouse.move(_slot["x"], _slot["y"], steps=25)
                page.wait_for_timeout(400)
                page.mouse.up()
                page.wait_for_timeout(800)
            except Exception as e:
                _dbg(gid, "worldle phrase smooth drag failed for "
                          f"{_code}: {str(e)[:80]}")
            _placed = _verify(_slot, _phrase)
            _dbg(gid, f"worldle phrase drag smooth {_code} -> slot "
                      f"label={_slot.get('label')!r} ({_slot.get('x')}, "
                      f"{_slot.get('y')}) placed={_placed}")
        if not _placed:
            # Attempt 3 (iter-15): in-page DragEvent sequence with a
            # real DataTransfer — REACHABLE for broad-probe slots now.
            # Iter-14 SKIPPED DragEvent for broad slots (it addressed
            # slots by index into the NARROW selector list, which does
            # not contain the broad slots) and the click-verify
            # fallback was never reached (zero log lines). This
            # version resolves chip + slot via elementFromPoint at
            # their CENTRES, so it dispatches onto the actual elements
            # under the paired coordinates for narrow AND broad slots.
            try:
                page.evaluate("""(args) => {
                  const chip = document.elementFromPoint(
                    args.cx, args.cy);
                  const slot = document.elementFromPoint(
                    args.sx, args.sy);
                  if (!chip || !slot) return false;
                  const dt = new DataTransfer();
                  // Iter-16: a drop handler that reads the payload
                  // (or requires a move effect) saw an EMPTY
                  // DataTransfer from the iter-15 sequence — carry
                  // the phrase + declare a move effect.
                  try { dt.setData('text/plain', args.phrase); } catch (e) {}
                  try { dt.effectAllowed = 'all'; } catch (e) {}
                  const mk = (t) => new DragEvent(t, {bubbles: true,
                    cancelable: true, dataTransfer: dt});
                  chip.dispatchEvent(mk('dragstart'));
                  slot.dispatchEvent(mk('dragenter'));
                  // Repeat dragover: some widgets only arm the drop
                  // zone after several dragover events.
                  slot.dispatchEvent(mk('dragover'));
                  slot.dispatchEvent(mk('dragover'));
                  slot.dispatchEvent(mk('dragover'));
                  slot.dispatchEvent(mk('drop'));
                  chip.dispatchEvent(mk('dragend'));
                  return true;
                }""", {"cx": _chip["x"], "cy": _chip["y"],
                       "sx": _slot["x"], "sy": _slot["y"],
                       "phrase": _phrase})
                page.wait_for_timeout(800)
            except Exception as e:
                _dbg(gid, "worldle phrase DragEvent failed for "
                          f"{_code}: {str(e)[:80]}")
            _placed = _verify(_slot, _phrase)
            _dbg(gid, f"worldle phrase drag DragEvent {_code} -> slot "
                      f"label={_slot.get('label')!r} ({_slot.get('x')}, "
                      f"{_slot.get('y')}) placed={_placed}")
        if not _placed:
            # Attempt 4 (iter-16): hover-drop. The iter-15 frame
            # (g_263) shows the Arabic chip VISIBLY grabbed and
            # floating mid-drag while the slots stay empty — the
            # widget grabs on pointerdown but the drop never arms:
            # it may need a continuous move stream + a settle over
            # the drop zone (dragover) before release, which the
            # 10 discrete moves of attempt 1 do not produce. Drag
            # in 25 steps, wiggle ±10px over the slot centre (to
            # keep dragover firing on the drop zone), hover 1000ms,
            # then release.
            try:
                page.mouse.move(_chip["x"], _chip["y"])
                page.mouse.down()
                page.mouse.move(_slot["x"], _slot["y"], steps=25)
                for _dx, _dy in ((10, 0), (-10, 0), (0, 10), (0, -10),
                                 (0, 0)):
                    page.mouse.move(_slot["x"] + _dx,
                                    _slot["y"] + _dy, steps=2)
                    page.wait_for_timeout(120)
                page.wait_for_timeout(1000)
                page.mouse.up()
                page.wait_for_timeout(800)
            except Exception as e:
                _dbg(gid, "worldle phrase hover-drop failed for "
                          f"{_code}: {str(e)[:80]}")
            _placed = _verify(_slot, _phrase)
            _dbg(gid, f"worldle phrase drag hover-drop {_code} -> slot "
                      f"label={_slot.get('label')!r} ({_slot.get('x')}, "
                      f"{_slot.get('y')}) placed={_placed}")
        _placed_by_code[str(_code).lower()] = bool(_placed)
        if not _placed:
            # Iter-15: drag failure is NOT terminal — record it and
            # CONTINUE to the next pair (iter-17: the old code
            # returned "click-fallback" here, so a single pair's
            # failure — e.g. the verifier lying about ara — meant
            # the remaining pairs were never attempted). The
            # caller reaches the click-verify path for the failed
            # pairs after this loop.
            _drag_failed.append((_code, _phrase, _slot))
            _dbg(gid, f"worldle phrase drag failed for {_code} "
                      f"({_phrase!r} -> slot label={_slot.get('label')!r} "
                      f"({_slot.get('x')}, {_slot.get('y')})); all drag "
                      "primitives failed for this pair; continuing "
                      "to the next pair")
    if isinstance(_out, dict):
        _out["placed"] = dict(_placed_by_code)
    if _drag_failed:
        return ("click-fallback",
                "; ".join(
                    f"phrase drag failed for {_c} "
                    f"({_p!r} -> slot label={_s.get('label')!r} "
                    f"({_s.get('x')}, {_s.get('y')})); all drag "
                    "primitives failed"
                    for _c, _p, _s in _drag_failed)
                + f"; probe={_probe}; falling back to click-verify "
                "(click chip -> click slot) for the failed pairs; "
                f"placed={_placed_by_code}")
    _s_worldle_submit_guarded(page, gid, "spokenLanguages", ds)
    _settle(page, base=1500)
    _gs = _s_worldle_day_guesses(page, "language-guesses", ds)
    _t1, _c1 = _s_worldle_language_counts(_gs, 1)
    if _c1 == len(names):
        return ("attempted", "Q0 language-pick + Q1 phrase DRAGGED "
                f"and submitted (store correct q1={_c1}/{len(names)})")
    return ("partial", "Q1 phrase drags placed but store shows "
            f"q1 correct={_c1}/{len(names)}")


def _worldle_phrase_click_verify(page, gid, picked, codes, names, prompts,
                               ds, _pairs_in=None, _placed_in=None):
    """Click-verify fallback for the Worldle Q1 PHRASE round (iter-15 P1).

    Iter-14: the click path was never reached (zero log lines) — the
    drag helper returned "partial" on drag failure and the caller
    returned partial too. Now the drag helper returns "click-fallback"
    and the caller runs this: for each pair, click the chip centre,
    then click the slot centre, then VERIFY the slot filled
    (position-anchored). Returns (status, detail); "attempted" only
    when the store proves q1 correct == #languages.

    Iter-16 (ANALYSIS12 §9): `_pairs_in` (the drag helper's
    [(code, chip, slot)] list, paired by LABEL with PRE-DRAG chip
    coords) is REUSED verbatim when given — click-verify NEVER
    re-probes + re-pairs. The iter-15 re-pairing re-probed with the
    broad probe, whose chip boxes qualified as slots, paired them
    by geometry-x (chip row y≈668), and then the fresh chip-text
    lookup returned the SLOT coords (850, 304) as the "chip" — so
    the path clicked the slot as the chip and the chip as the slot.
    Without `_pairs_in` (legacy callers), re-pairing is allowed
    ONLY on a LABEL pairing — a geometry-x re-pairing is refused
    outright, and a text-lookup chip result within 12px of the slot
    centre is rejected as the same swap symptom.

    Iter-17 (ANALYSIS13 §6): (a) `_placed_in` (the drag helper's
    per-code placement map) lets click-verify SKIP pairs the drag
    already placed — re-clicking a placed chip would click the
    FILLED slot (the chip now lives there), not the chip bank.
    (b) A single pair's failure NO LONGER stops the path: every
    pair is attempted, failures are recorded, and the submit runs
    only when ALL pairs are placed. The old early-return meant a
    (falsely) failed ara left eng never attempted.
    """
    _cv_pairs = []  # (code, chip_dict, slot_dict)
    if _pairs_in:
        _dbg(gid, "worldle phrase click-verify: reusing drag LABEL "
                  f"pairs (no re-probe, no re-pairing)")
        for _entry in _pairs_in:
            try:
                _c, _ch, _sl = _entry
            except (TypeError, ValueError):
                continue
            _cv_pairs.append((_c, dict(_ch or {}), dict(_sl or {})))
    else:
        _broad = _worldle_phrase_broad_slot_probe(page, names)
        _slots = _broad.get("slots") or []
        if not _slots:
            _dbg(gid, "worldle phrase click-verify: broad slot probe "
                      "found no slots")
            return ("partial", "click-verify: broad slot probe found no "
                               "slots; NOT submitted")
        _pair_res, _pair_how = _worldle_phrase_pair_slots(
            _slots, codes, names)
        if _pair_res is None:
            _dbg(gid, "worldle phrase click-verify: cannot pair: "
                      f"{_pair_how}")
            return ("partial", "click-verify: cannot pair chips to "
                               f"slots: {_pair_how}; NOT submitted")
        if _pair_how != "label":
            # Never re-pair by geometry-x in click-verify: the
            # geometry fallback picks the lowest exactly-sized
            # cluster, which is the CHIP row when chips qualify as
            # slots (iter-15: y≈668 chip boxes paired as slots).
            _dbg(gid, "worldle phrase click-verify: refusing "
                      f"non-label re-pairing ({_pair_how}); NOT submitted")
            return ("partial", "click-verify: pairing is "
                               f"{_pair_how}, not label — re-pairing by "
                               "geometry refused (chip-row-as-slots "
                               "risk); NOT submitted")
        for _code, _slot in _pair_res:
            _cv_pairs.append((_code, None, dict(_slot or {})))
    _cv_desc = [(c, {"label": s.get("label"), "x": s.get("x"),
                   "y": s.get("y")}) for c, _, s in _cv_pairs]
    _dbg(gid, f"worldle phrase click-verify pairs: {_cv_desc}")
    # Iter-17: attempt ALL pairs; record per-pair placement; never
    # stop at the first failure.
    _cv_placed = {}
    _cv_unplaced = []
    _cv_reasons = {}
    for _code, _chip_in, _slot in _cv_pairs:
        _key = str(_code).lower()
        _phrase = (picked or {}).get(_key)
        if not _phrase:
            _dbg(gid, f"worldle phrase click-verify {_code}: no picked "
                      "phrase; recording unplaced, continuing")
            _cv_placed[_key] = False
            _cv_unplaced.append(_code)
            _cv_reasons[_key] = "no picked phrase"
            continue
        if isinstance(_placed_in, dict) and _placed_in.get(_key):
            # Drag already placed this pair: skip it. Its chip now
            # lives INSIDE the slot, so clicking the stashed chip
            # coords would click the filled slot, not the chip bank.
            _dbg(gid, f"worldle phrase click-verify {_code}: already "
                      "placed by drag; skipping (NOT re-clicked)")
            _cv_placed[_key] = True
            continue
        _chip = None
        if _chip_in is not None and _chip_in.get("x") is not None:
            # Pre-drag probe coords: the chip was in its home
            # position when probed; never re-looked-up by text, so
            # the text lookup cannot return the slot coords.
            _chip = {"x": _chip_in.get("x"), "y": _chip_in.get("y")}
        else:
            try:
                _chip = page.evaluate("""(phrase) => {
                  const vis = e => e && e.offsetParent !== null;
                  const norm = s => (s||'').toLowerCase()
                    .replace(/[.,!?;:"'«»“”‘’()\\-]/g, '')
                    .replace(/\\s+/g,' ').trim();
                  const leaves = Array.from(
                    document.querySelectorAll('body *')).filter(vis)
                    .filter(e => !e.closest('nav, header, footer'))
                    .filter(e => e.children.length === 0)
                    .filter(e => norm(e.innerText) === norm(phrase));
                  const el = leaves[0] || null;
                  if (!el) return null;
                  const r = el.getBoundingClientRect();
                  return {x: r.x + r.width / 2, y: r.y + r.height / 2};
                }""", _phrase)
            except Exception:
                _chip = None
        if not _chip:
            _dbg(gid, f"worldle phrase click-verify {_code}: chip for "
                      f"{_phrase!r} not found by text; recording "
                      "unplaced, continuing")
            _cv_placed[_key] = False
            _cv_unplaced.append(_code)
            _cv_reasons[_key] = "chip not found by text"
            continue
        # Swap guard: chip and slot centres must be distinct
        # elements. Iter-15 clicked (850, 304) — the SLOT centre —
        # as the "chip", then the chip box as the "slot".
        try:
            _dist = ((float(_chip["x"]) - float(_slot["x"])) ** 2
                     + (float(_chip["y"]) - float(_slot["y"])) ** 2) ** 0.5
        except Exception:
            _dist = 999.0
        if _dist < 12:
            _dbg(gid, f"worldle phrase click-verify {_code}: chip/slot "
                      f"centres coincide (dist={_dist:.1f}px) — the "
                      "iter-15 coordinate-swap symptom; refusing "
                      "this pair, continuing")
            _cv_placed[_key] = False
            _cv_unplaced.append(_code)
            _cv_reasons[_key] = ("chip/slot centres coincide "
                                 "(swap-guard refusal)")
            continue
        try:
            page.mouse.click(_chip["x"], _chip["y"])
            page.wait_for_timeout(400)
            page.mouse.click(_slot["x"], _slot["y"])
            page.wait_for_timeout(800)
        except Exception as e:
            _dbg(gid, f"worldle phrase click-verify {_code}: click "
                      f"failed: {str(e)[:80]}; recording unplaced, "
                      "continuing")
            _cv_placed[_key] = False
            _cv_unplaced.append(_code)
            _cv_reasons[_key] = f"click failed ({str(e)[:60]})"
            continue
        _placed = (_worldle_phrase_slot_at_contains(page, _slot, _phrase)
                   or _worldle_phrase_slot_contains(page, _phrase))
        _dbg(gid, f"worldle phrase click-verify {_code}: chip "
                  f"({_chip['x']:.0f}, {_chip['y']:.0f}) -> slot "
                  f"label={_slot.get('label')!r} ({_slot.get('x')}, "
                  f"{_slot.get('y')}) placed={_placed}")
        _cv_placed[_key] = bool(_placed)
        if not _placed:
            _cv_unplaced.append(_code)
            _cv_reasons[_key] = "slot did not fill"
    if _cv_unplaced:
        return ("partial", "click-verify: attempted all pairs; "
                           f"unplaced={_cv_unplaced} "
                           f"(placed={_cv_placed}; "
                           f"reasons={_cv_reasons}); NOT submitted")
    _s_worldle_submit_guarded(page, gid, "spokenLanguages", ds)
    _settle(page, base=1500)
    _gs = _s_worldle_day_guesses(page, "language-guesses", ds)
    _t1, _c1 = _s_worldle_language_counts(_gs, 1)
    if _c1 == len(names):
        return ("attempted", "Q0 language-pick + Q1 phrase "
                "CLICK-VERIFIED and submitted "
                f"(store correct q1={_c1}/{len(names)})")
    return ("partial", "click-verify placements filled but store shows "
                       f"q1 correct={_c1}/{len(names)}")


def _s_worldle_play_language(page, gid, day):
    """Spoken languages: round exists iff exactly 2 languages. Q0/Q1
    each need isCorrect count == #languages. Option clicks use the
    DISPLAY names from Intl.DisplayNames(['en'], {type:'language'})
    evaluated in-page (never a guessed code->name mapping). Q1 may be
    a second language-pick OR a phrase question — detected from the
    `2. PHRASE` tab / live prompt, logged (prompt+options) and
    recorded partial without clicking (no phrases source exists).

    Fix-loop iter 6 (submission model): the old code treated ANY
    store-snapshot change after the first click as "auto-submitted"
    and moved on with 1 name per question, while the game needs 2
    correct per question. Now submission = the language-guesses array
    GREW; each language question clicks BOTH display names, then
    presses Guess once (guarded), and the per-question correct count
    is verified from the store. A first click that lands a guess AND
    advances the round (new guess with a different question id, or
    the PHRASE tab/prompt appearing) stops the second click so a
    language name is never clicked into the next question.
    """
    ds = day.get("date") or ""
    langs = day.get("languages") or []
    codes = [str(x.get("languageCode") or "").lower() for x in langs]
    if len(codes) != 2:
        return "skipped", [], \
            f"language round needs exactly 2 languages, have {len(codes)}"
    try:
        names = page.evaluate("""(codes) => codes.map(c => {
          try { return new Intl.DisplayNames(['en'], {type: 'language'})
            .of(c); } catch (e) { return null; }
        })""", codes) or []
    except Exception:
        names = []
    # Intl.DisplayNames returns the raw code for languages missing from
    # the runtime's ICU data (observed: 'kbp' stayed 'kbp', while the live
    # option list shows 'Kabiye' — so the Q1 click matched nothing). Fall
    # back to a curated ISO 639 code -> English name table when the
    # DisplayNames result IS the code itself.
    _LANG_FALLBACK = {"kbp": "Kabiye", "fon": "Fon", "ewe": "Ewe",
                      "hau": "Hausa", "yor": "Yoruba", "ibo": "Igbo",
                      "amh": "Amharic", "orm": "Oromo", "tir": "Tigrinya",
                      "som": "Somali", "kin": "Kinyarwanda", "lug": "Ganda",
                      "nya": "Nyanja", "sot": "Sotho", "tso": "Tsonga",
                      "ven": "Venda", "zul": "Zulu", "xho": "Xhosa",
                      "afr": "Afrikaans", "nso": "Northern Sotho",
                      "tsn": "Tswana", "ssw": "Swati",
                      "nbl": "Southern Ndebele", "wol": "Wolof",
                      "bam": "Bambara", "ful": "Fula", "aka": "Akan",
                      "twi": "Twi", "lin": "Lingala", "kon": "Kongo",
                      "run": "Rundi", "sag": "Sango", "kal": "Kalaallisut"}
    names = [(_LANG_FALLBACK.get(c) if (not n or str(n).lower() == c)
              else n) for c, n in zip(codes, names)]
    if len(names) != 2 or not all(names):
        dump = _s_worldle_dom_dump(page, gid, "spokenLanguages")
        return "skipped", [], \
            "language display names not resolvable via Intl.DisplayNames " \
            f"in-page for {codes} (DOM dump logged)"
    opts = _s_worldle_visible_options(page)
    _dbg(gid, "worldle language: codes=" + str(codes)
         + " displayNames=" + str(names)
         + " live options sample=" + str(opts[:25]))

    def _pick_question(qidx):
        """Click BOTH display names for one language sub-question, then
        Guess once. Submission = the language-guesses array grew (not
        any snapshot change). Returns (ok, detail); a detail starting
        with "PHRASE:" means the round advanced to the phrase sub-round
        after the first click — the caller records it partial.
        """
        order = ([names[qidx], names[1 - qidx]] if 0 <= qidx < 2
                 else list(names))
        before_gs = _s_worldle_day_guesses(page, "language-guesses", ds)
        before_n = len(before_gs)
        ok1, d1 = _s_worldle_click_option_text(page, order[0])
        if not ok1:
            ok1, d1 = _s_worldle_click_option_loose(page, order[0])
        if not ok1:
            return False, f"Q{qidx} option {order[0]!r} not found ({d1})"
        _settle(page, base=800)
        mid_gs = _s_worldle_day_guesses(page, "language-guesses", ds)
        _new = mid_gs[before_n:] if len(mid_gs) > before_n else []
        _advanced = any((g or {}).get("question") not in (qidx, None)
                        for g in _new)
        _phrase, _has_tab, _prompts, _opts = \
            _worldle_language_phrase_check(page)
        if _phrase:
            # PHRASE only when the phrase tab/prompt is showing.
            _tot, _cor = _s_worldle_language_counts(mid_gs, qidx)
            return True, ("PHRASE: Q%d first click landed=%s advanced=%s "
                           "correct=%d/%d; phrase round showing "
                           "(prompts=%s options=%s); NOT clicked"
                           % (qidx, bool(_new), _advanced, _cor,
                              len(names), _prompts, _opts))
        if _advanced:
            # Iter 9 (ANALYSIS6 §2c code-review note): the old code
            # conflated this with PHRASE (`if _phrase or _advanced:`),
            # so a first click that advanced the round to ANOTHER
            # LANGUAGE question was mislabelled PHRASE and recorded
            # partial. ADVANCED-to-another-language-question is NOT
            # the phrase round: the first click already landed as a
            # guess for the next question, so the second click of THIS
            # question must not fire into the next question. Tell the
            # caller to proceed to the next question instead.
            _tot, _cor = _s_worldle_language_counts(mid_gs, qidx)
            return True, ("ADVANCED: Q%d first click landed=%s and round "
                           "advanced to another language question "
                           "(correct=%d/%d); second click skipped, "
                           "proceed to next question"
                           % (qidx, bool(_new), _cor, len(names)))
        ok2, d2 = _s_worldle_click_option_text(page, order[1])
        if not ok2:
            ok2, d2 = _s_worldle_click_option_loose(page, order[1])
        if not ok2:
            _tot, _cor = _s_worldle_language_counts(mid_gs, qidx)
            return bool(_new), (f"Q{qidx} second option {order[1]!r} "
                                f"not found ({d2}); first landed={bool(_new)} "
                                f"correct={_cor}/{len(names)}")
        sub = _s_worldle_submit_guarded(page, gid, "spokenLanguages", ds)
        after_gs = _s_worldle_day_guesses(page, "language-guesses", ds)
        tot, cor = _s_worldle_language_counts(after_gs, qidx)
        submitted = len(after_gs) > before_n
        return submitted, (f"Q{qidx} submitted={submitted} "
                           f"correct={cor}/{len(names)} guesses={tot} "
                           f"submit={sub}")

    ok0, det0 = _pick_question(0)
    _dbg(gid, f"worldle language Q0: ok={ok0} ({det0})")
    if not ok0:
        return "failed", [], f"Q0 language pick failed ({det0})"
    if str(det0).startswith("PHRASE:"):
        return "partial", list(names), \
            f"Q0 language-pick played; {det0}"
    if str(det0).startswith("ADVANCED:"):
        # Round advanced to the next LANGUAGE question: fall through
        # to the post-Q0 flow (phrase check, then Q1 pick). The
        # second Q0 click was skipped so no name is clicked into Q1.
        _dbg(gid, "worldle language Q0 advanced to next language "
                  "question; proceeding to Q1 pick")
    _settle(page, base=1500)
    _phrase, _has_tab, _prompts, _opts = \
        _worldle_language_phrase_check(page)
    if _phrase:
        # Iter 7: the phrase tab shows no BUTTON options, so the old
        # code gave up here. Gather full-DOM evidence (div/span cards,
        # audio buttons, aria labels) + the phrase/language resources
        # the game itself fetched, and only click when the game's own
        # fetched phrase data maps the prompt to the shown options.
        # No mapping -> partial with the full evidence, never a guess.
        # Iter 9: PRIMARY phrase source = the game's own
        # common/languages.json (day["phrases"]: {code.lower():
        # myNameIs}), wired in answers.worldle_all_modes. The day /
        # country JSON has NO phrase data (languageData carries only
        # codes + percentages); this endpoint is the one the bundled
        # main.js references, verified live (all 344 entries carry
        # phrases.myNameIs). The iter-7 game-fetched-resource mapping
        # stays as a fallback when day phrases are absent.
        _ev = _worldle_language_phrase_evidence(page)
        _ev_opts = _ev.get("options") or []
        _ev_fetched = _ev.get("fetched") or []
        _ev_drag = _ev.get("draggable") or []
        _day_phrases = day.get("phrases") or {}
        _dbg(gid, "worldle language Q1 PHRASE branch: prompts="
                  f"{_prompts} dom_options={str(_ev_opts)[:1200]} "
                  f"draggable={str(_ev_drag)[:600]} "
                  f"game_fetched={_ev_fetched} "
                  f"day_phrases={_day_phrases}")
        _picked = None
        _picked_src = ""
        if _day_phrases and all(
                str(_day_phrases.get(str(c).lower()) or "")
                for c in codes):
            _picked = {str(c).lower(): str(_day_phrases[str(c).lower()])
                       for c in codes}
            _picked_src = "day phrases (common/languages.json)"
            _dbg(gid, "worldle language Q1 phrase: game-data phrases "
                       f"{_picked_src} -> {_picked}")
        if not _picked:
            for _url in _ev_fetched:
                if "phrase" not in _url.lower():
                    continue
                try:
                    _pj = page.evaluate(
                        "(u) => fetch(u).then(r => r.ok ? r.json() : null)"
                        ".catch(() => null)", _url)
                except Exception:
                    _pj = None
                if not isinstance(_pj, (dict, list)):
                    continue
                _picked = _worldle_phrase_pick(
                    _pj, _prompts[0] if _prompts else "", codes, names,
                    [str(o.get("text") or "") for o in _ev_opts])
                if _picked:
                    _picked_src = f"game-fetched {_url[:80]}"
                    _dbg(gid, "worldle language Q1 phrase: game-fetched "
                              f"data {_url[:80]} maps prompt to {_picked}")
                    break
        if _picked:
            # Drag first (fix-loop iter 11): clicks do not register on
            # phrase options (store changed=False). The helper probes
            # chips + slots, pairs deterministically, drags per pair,
            # submits, and arbitrates with the store. Only when no
            # draggable chips exist does the click path below run.
            # Iter-16: the drag helper publishes its LABEL pairs
            # (with pre-drag chip coords) into _drag_out; click-verify
            # reuses them verbatim — it never re-probes + re-pairs
            # (the iter-15 re-pairing paired chip boxes as slots and
            # swapped chip/slot coords).
            _drag_out = {}
            _drag_res = _worldle_phrase_drag(page, gid, _picked, codes,
                                             names, _prompts, ds,
                                             _out=_drag_out)
            if _drag_res[0] == "attempted":
                return "attempted", list(names), _drag_res[1]
            if _drag_res[0] == "partial":
                return "partial", list(names), _drag_res[1]
            # click-fallback (iter-15): drags failed or no draggable
            # chips — run the click-verify path (click chip -> click
            # slot, verify slot fills). This path was unreachable in
            # iter-14 (zero log lines).
            _dbg(gid, "worldle phrase: drag returned click-fallback; "
                      "running click-verify path")
            _cv_res = _worldle_phrase_click_verify(
                page, gid, _picked, codes, names, _prompts, ds,
                _pairs_in=_drag_out.get("pairs"),
                # Iter-17: skip pairs the drag already placed —
                # their chips now live inside the slots.
                _placed_in=_drag_out.get("placed"))
            if _cv_res[0] == "attempted":
                return "attempted", list(names), _cv_res[1]
            return "partial", list(names), _cv_res[1]
        return "partial", list(names), \
            "Q0 language-pick played; Q1 is a phrase question " \
            f"(prompts={_prompts}); full-DOM options={str(_ev_opts)[:800]}; " \
            f"game-fetched phrase/language resources={_ev_fetched}; " \
            f"day phrases (common/languages.json)={_day_phrases}; " \
            "no phrase mapping matched the shown options — recorded " \
            "partial without clicking (phrase translations " \
            "verified-or-absent as shown; nothing guessed)"
    ok1, det1 = _pick_question(1)
    _dbg(gid, f"worldle language Q1: ok={ok1} ({det1})")
    if not ok1:
        return "partial", list(names), \
            f"Q0 played; Q1 language pick failed ({det1})"
    if str(det1).startswith("PHRASE:"):
        return "partial", list(names), \
            f"Q0+Q1 language-picks played; {det1}"
    if str(det1).startswith("ADVANCED:"):
        # Round advanced past Q1 (to phrase or summary): fall through
        # to the store-count verification below, which arbitrates.
        _dbg(gid, f"worldle language Q1 advanced: {det1}")
    _settle(page, base=1500)
    # The game's own win rule is 2 correct per question: if the store
    # proves that, the language round is complete whatever tabs remain.
    _gs = _s_worldle_day_guesses(page, "language-guesses", ds)
    _t0, _c0 = _s_worldle_language_counts(_gs, 0)
    _t1, _c1 = _s_worldle_language_counts(_gs, 1)
    if _c0 == len(names) and _c1 == len(names):
        return "attempted", list(names), \
            f"picked both display names per question: {names} " \
            f"(store correct q0={_c0}/{len(names)} q1={_c1}/{len(names)})"
    _phrase2, _has_tab2, _prompts2, _opts2 = \
        _worldle_language_phrase_check(page)
    if _phrase2 or _has_tab2:
        return "partial", list(names), \
            "language picks played but store incomplete " \
            f"(correct q0={_c0}/{len(names)} q1={_c1}/{len(names)}); " \
            f"PHRASE sub-round pending (prompts={_prompts2} " \
            f"options={_opts2}); recorded partial without clicking"
    return "attempted", list(names), \
        "picked display names per question: " + str(names) + \
        f" (store correct q0={_c0}/{len(names)} q1={_c1}/{len(names)})"


def _s_worldle_gdp_bucket(page, number):
    """Click the GDP bucket button containing `number`.

    The NUMBER tab has NO number input — it offers bucket buttons
    ('< $1K', '$1K - $2.5K', ..., '$90K+'). Parse each button's bounds
    (K = thousands) and click the bucket with low <= number < high
    ('< $X' -> high=X; '$X+' -> low=X, no high). Returns the clicked
    button text or None."""
    try:
        hit = page.evaluate("""(num) => {
          const vis = e => e && e.offsetParent !== null;
          const parse = s => {
            const m = (s||'').replace(/[$,\s]/g,'').match(/([\d.]+)K?/);
            if (!m) return null;
            let v = parseFloat(m[1]);
            if ((s||'').includes('K')) v *= 1000;
            return v;
          };
          const els = Array.from(document.querySelectorAll('button'))
            .filter(vis).filter(e => !e.closest('nav, header, footer'));
          for (const e of els) {
            const t = (e.innerText || '').trim();
            if (t.startsWith('<')) {
              const hi = parse(t.slice(1));
              if (hi !== null && num < hi) { e.click(); return t; }
            } else if (t.endsWith('+')) {
              const lo = parse(t.slice(0, -1));
              if (lo !== null && num >= lo) { e.click(); return t; }
            } else if (t.includes('-')) {
              const parts = t.split('-');
              const lo = parse(parts[0]), hi = parse(parts[1]);
              if (lo !== null && hi !== null && num >= lo && num < hi) {
                e.click(); return t;
              }
            }
          }
          return null;
        }""", float(number))
    except Exception:
        return None
    return hit or None


def _s_worldle_play_gdp(page, gid, day):
    """GDP: Q0 number = country.gdpPerCapita EXACTLY (integer as-is,
    no commas/$). Q1 comparison = max gdpPerCapita among the OPTIONS
    ACTUALLY SHOWN (re-derived from the shown set via the CDN/borders
    lookup; the day-prediction is only a fallback when no shown set
    is readable, and any disagreement is logged). Range widgets are
    driven via the keyboard/native-setter path (include_range=True)."""
    ds = day.get("date") or ""
    gdp = day.get("gdp") or {}
    number = gdp.get("number")
    if number is None:
        return "skipped", [], "no gdpPerCapita in day data"
    # The NUMBER tab has bucket buttons, not a number input (the old code
    # searched for an input that does not exist and failed every GDP
    # round). Click the containing bucket first; the input path below is
    # only a fallback for a layout without buckets.
    bucket = _s_worldle_gdp_bucket(page, number)
    if bucket:
        _dbg(gid, f"worldle gdp bucket {bucket!r} clicked for {number}")
        _s_worldle_submit_guarded(page, gid, "gdp", ds)
        _settle(page, base=1500)
        # Answering NUMBER enables the COMPARISON tab; open it so the
        # comparison options below are the live shown set.
        try:
            _cmp_tab = page.evaluate("""() => {
              const vis = e => e && e.offsetParent !== null;
              const hit = Array.from(document.querySelectorAll('button'))
                .filter(vis).find(e => (e.innerText || '').toUpperCase()
                  .includes('COMPARISON'));
              if (hit && !hit.disabled) { hit.click(); return true; }
              return false;
            }""")
        except Exception:
            _cmp_tab = False
        _dbg(gid, f"worldle gdp comparison tab opened={_cmp_tab}")
        _settle(page, base=1500)
    else:
        if not _s_worldle_pick_input(page, ["gdp", "per capita", "dollar",
                                            "usd", "$"], include_range=True):
            dump = _s_worldle_dom_dump(page, gid, "gdp")
            try:
                _bt = (page.evaluate("() => document.body.innerText") or "")
            except Exception:
                _bt = ""
            scan = {k: (k in _bt.lower()) for k in
                    ("gdp", "per capita", "dollar", "usd", "$")}
            return "failed", [], \
                f"GDP number input not found; keyword scan={scan}; DOM dump logged"
        ok, detail = _s_worldle_set_picked_value(page, gid, str(int(number)),
                                                 "gdp")
        if not ok:
            dump = _s_worldle_dom_dump(page, gid, "gdp")
            return "failed", [], \
                f"GDP number set failed ({detail}); DOM dump logged"
        _s_worldle_submit_guarded(page, gid, "gdp", ds)
        _settle(page, base=2000)
    # Q1 comparison: DELIBERATE branch first (fix-loop iter 12) —
    # probe the live comparison widget (image cards + text buttons),
    # resolve every shown country's GDP from the day lookup (+ the
    # game's own CDN), click the max. The old code only read button
    # innerText, so an image-card option ('Saudi Arabia' in the iter
    # runs) was 'not found' even though the lookup held its GDP.
    # When the deliberate branch does not click (prompt absent /
    # options unresolvable), fall through to the legacy
    # visible-options path and then the day-prediction fallback —
    # both of which still refuse to click an unresolved option.
    lookup = day.get("gdp_lookup") or {}
    cst, cpick, cdet = _s_worldle_play_comparison(
        page, gid, day, "gdp", ds,
        ("highest", "per capita gdp"), lookup, "gdpPerCapita", "gdp")
    if cst == "attempted":
        return "attempted", [str(number), cpick], cdet
    if cst == "skipped":
        # The GAME declined the comparison round (not enough
        # neighbours) — the comparison is N/A; the number alone
        # decides. Do NOT fall through to clicking a visible option.
        _dbg(gid, f"worldle gdp comparison game-skipped: {cdet}")
        return "attempted", [str(number)], cdet
    _dbg(gid, "worldle gdp comparison deliberate branch "
              f"{cst}: {cdet}; falling back to visible-options path")

    # Q1: re-derive from the shown options.
    lookup = day.get("gdp_lookup") or {}

    def _nm(s):
        return " ".join(str(s or "").lower().split())

    opts = _s_worldle_visible_options(page)
    shown = []
    for o in opts:
        if _nm(o) in lookup and o not in shown:
            shown.append(o)
    if shown:
        vals = [(o, lookup[_nm(o)]) for o in shown]
        if any(v is None for _, v in vals):
            return "partial", [str(number)], \
                "number played; comparison shown options unresolvable: " \
                f"{vals}"
        best = max(vals, key=lambda kv: kv[1])[0]
        pred = gdp.get("compare_answer")
        if pred and _nm(pred) != _nm(best):
            _dbg(gid, "worldle gdp comparison DISAGREEMENT: shown-options "
                      f"max={best!r} (from {vals}) vs day prediction "
                      f"{pred!r}; using shown-options max")
        else:
            _dbg(gid, "worldle gdp comparison: shown options=" + str(vals)
                 + " max=" + str(best))
        ok2, detail2 = _s_worldle_click_option_text(page, best)
        if not ok2:
            ok2, detail2 = _s_worldle_click_option_loose(page, best)
        _dbg(gid, f"worldle gdp comparison {best!r}: ok={ok2} ({detail2[:60]})")
        if not ok2:
            return "partial", [str(number)], \
                f"number played; comparison option {best!r} not clickable"
        _s_worldle_submit_guarded(page, gid, "gdp", ds)
        _settle(page, base=2200)
        return "attempted", [str(number), best], \
            "GDP number + highest-GDP comparison (shown options) played"
    compare = gdp.get("compare_answer")
    if not compare:
        return "partial", [str(number)], \
            "number played; no comparison options shown and no prediction"
    _dbg(gid, "worldle gdp: no shown comparison options readable; "
              f"falling back to day prediction {compare!r}")
    ok2, detail2 = _s_worldle_click_option_text(page, compare)
    if not ok2:
        ok2, detail2 = _s_worldle_click_option_loose(page, compare)
    _dbg(gid, f"worldle gdp comparison fallback {compare!r}: ok={ok2} "
              f"({detail2[:60]})")
    if not ok2:
        return "partial", [str(number)], \
            f"number played; comparison option {compare!r} not found"
    _s_worldle_submit_guarded(page, gid, "gdp", ds)
    _settle(page, base=2200)
    return "attempted", [str(number), compare], \
        "GDP number + comparison (day-prediction fallback) played"


def _s_worldle_population_bucket(page, pop):
    """Click the population bucket button containing `pop`.

    The Population & Currency q0 widget is BUCKET BUTTONS, not a
    numeric input ('< 3 million', '3 - 5 million', '5 - 8 million',
    '8+ million'). The old code searched for a numeric input that
    does not exist and failed every round (iter-12). Parse each
    button's bounds (million = 1_000_000) and click the bucket with
    low <= pop < high ('< X' -> high=X; 'X+' -> low=X, no high).
    Returns the clicked button text or None."""
    try:
        hit = page.evaluate("""(num) => {
          const vis = e => e && e.offsetParent !== null;
          const parse = s => {
            const m = (s||'').replace(/[,\\s]/g,'').match(/([\\d.]+)/);
            if (!m) return null;
            let v = parseFloat(m[1]);
            if (/million/i.test(s)) v *= 1000000;
            else if (/[kK]/.test(s)) v *= 1000;
            return v;
          };
          const els = Array.from(document.querySelectorAll('button'))
            .filter(vis).filter(e => !e.closest('nav, header, footer'));
          for (const e of els) {
            const t = (e.innerText || '').trim();
            if (!/million/i.test(t)) continue;
            if (t.startsWith('<')) {
              const hi = parse(t.slice(1));
              if (hi !== null && num < hi) { e.click(); return t; }
            } else if (t.endsWith('+')) {
              const lo = parse(t.slice(0, -1));
              if (lo !== null && num >= lo) { e.click(); return t; }
            } else if (t.includes('-')) {
              const parts = t.split('-');
              const lo = parse(parts[0]), hi = parse(parts[1]);
              if (lo !== null && hi !== null && num >= lo && num < hi) {
                e.click(); return t;
              }
            }
          }
          return null;
        }""", float(pop))
    except Exception:
        return None
    return hit or None


def _s_worldle_play_currency(page, gid, day):
    """Population & Currency: q0 = population number (integer as-is).
    q1/q2 meanings are UNCONFIRMED, so every sub-question's prompts +
    option sets are read from the live DOM in order and each answer
    is derived from day data + the shown options. A sub-question whose
    meaning is unknown is dumped and NOT clicked (partial)."""
    ds = day.get("date") or ""
    pop = day.get("population")
    cur = day.get("currency") or {}
    if pop is None:
        return "skipped", [], "no population in day data"
    # The q0 widget is BUCKET BUTTONS ('< 3 million', '3 - 5 million',
    # '5 - 8 million', '8+ million'), not a numeric input (iter-12:
    # 'population input not found' every run). Try buckets first; the
    # input path below is only a fallback for a layout without buckets.
    _bucket = _s_worldle_population_bucket(page, pop)
    if _bucket:
        _dbg(gid, f"worldle population bucket {_bucket!r} clicked for {pop}")
        _s_worldle_submit_guarded(page, gid, "populationCurrency", ds)
        _settle(page, base=2000)
    elif not _s_worldle_pick_input(page, ["population"], include_range=True):
        dump = _s_worldle_dom_dump(page, gid, "populationCurrency")
        return "failed", [], \
            "population input not found; DOM dump logged"
    else:
        ok, detail = _s_worldle_set_picked_value(page, gid, str(int(pop)),
                                                 "population")
        if not ok:
            dump = _s_worldle_dom_dump(page, gid, "populationCurrency")
            return "failed", [], \
                f"population set failed ({detail}); DOM dump logged"
        _s_worldle_submit_guarded(page, gid, "populationCurrency", ds)
        _settle(page, base=2000)
    played = [str(pop)]
    cname, ccode = cur.get("name") or "", cur.get("code") or ""

    def _nm(s):
        return " ".join(str(s or "").lower().split())

    for qi in (1, 2):
        prompts = _s_worldle_prompt_texts(page)
        opts = _s_worldle_visible_options(page)
        _dbg(gid, f"worldle currency q{qi}: prompts={prompts[:5]} "
                  f"options={opts[:30]}")
        prompt_text = " ".join(prompts).lower()
        target = None
        # Prompt-aware derivation from day data + shown options.
        if "code" in prompt_text and ccode:
            cands = [ccode, cname]
        elif "currency" in prompt_text or "money" in prompt_text:
            cands = [cname, ccode]
        else:
            cands = [cname, ccode]
        for cand in cands:
            if cand and any(_nm(o) == _nm(cand) for o in opts):
                target = cand
                break
        if target is None:
            dump = _s_worldle_dom_dump(page, gid, "populationCurrency")
            return "partial", played, \
                f"q{qi} meaning unknown: prompts={prompts} " \
                f"options={opts}; DOM dump logged; NOT clicked"
        okc, dc = _s_worldle_click_option_text(page, target)
        if not okc:
            okc, dc = _s_worldle_click_option_loose(page, target)
        _dbg(gid, f"worldle currency q{qi} {target!r}: ok={okc} ({dc[:60]})")
        if not okc:
            return "partial", played, \
                f"q{qi} option {target!r} not clickable"
        played.append(target)
        _s_worldle_submit_guarded(page, gid, "populationCurrency", ds)
        _settle(page, base=2000)
    return "attempted", played, \
        "population + currency sub-questions played"


def _s_worldle_play_populous(page, gid, day):
    """Populous cities: order the N most populous cities by population
    desc. N is read from the live prompt ("organise the N most
    populated cities"); the order comes from cities.json. The SHOWN
    set must equal the top-N names or both sets are logged + the mode
    is skipped. Interaction is detected from the DOM: click-to-order
    (verify selected state per click) or drag-and-drop (drag each chip
    to its target slot, verifying slots), then Guess once."""
    ds = day.get("date") or ""
    try:
        body = (page.evaluate("() => document.body.innerText") or "")
    except Exception:
        body = ""
    import re as _re
    m = _re.search(r"(\d+)\s+most populated", body) \
        or _re.search(r"(\d+)\s+most populous", body) \
        or _re.search(r"organise the (\d+)", body, _re.IGNORECASE)
    n_exp = int(m.group(1)) if m else None
    if n_exp is None:
        dump = _s_worldle_dom_dump(page, gid, "populousCities")
        return "skipped", [], \
            "populous cities: expected count not in live prompt; " \
            f"prompt tail={body[-400:]!r}; DOM dump logged"
    want = [c.get("name") for c in (day.get("populous") or [])[:n_exp]]
    if len(want) < n_exp or not all(want):
        return "skipped", [], \
            f"populous cities: only {len([w for w in want if w])} named " \
            f"cities for N={n_exp}"

    def _nm(s):
        return " ".join(str(s or "").lower().split())

    try:
        cands = page.evaluate("""() => {
          const vis = e => e && e.offsetParent !== null;
          return Array.from(document.querySelectorAll(
            'button, [role=option], li, [draggable=true]'))
            .filter(vis).filter(e => !e.closest('nav, header, footer'))
            .map(e => (e.innerText || '').trim())
            .filter(t => t && t.length < 120);
        }""") or []
    except Exception:
        cands = []
    want_norm = {_nm(w) for w in want}
    shown_cities = sorted({_nm(c) for c in cands} & want_norm)
    if set(shown_cities) != want_norm:
        _dbg(gid, "worldle populous shown-set MISMATCH: shown candidates="
                  f"{cands} (matched={shown_cities}) vs top-N={want}")
        return "skipped", [], \
            f"populous cities: shown set {shown_cities} != top-N " \
            f"{sorted(want_norm)} (both sets logged)"
    # Detect drag-and-drop (numbered slots + draggable chips) vs
    # click-to-order.
    try:
        dom_kind = page.evaluate("""() => {
          const vis = e => e && e.offsetParent !== null;
          const slots = Array.from(document.querySelectorAll(
            '[data-slot], [class*=slot i], [class*=position i]'))
            .filter(vis).length;
          const drags = Array.from(document.querySelectorAll(
            '[draggable=true]')).filter(vis).length;
          const numbered = Array.from(document.querySelectorAll(
            '[class*=order i], [class*=rank i]')).filter(vis).length;
          return {slots, drags, numbered};
        }""") or {}
    except Exception:
        dom_kind = {}
    if (dom_kind.get("drags") or 0) >= n_exp and \
            ((dom_kind.get("slots") or 0) >= n_exp
             or (dom_kind.get("numbered") or 0) >= n_exp):
        # Drag each chip to its target slot, verifying slots.
        for i, name in enumerate(want):
            try:
                boxes = page.evaluate("""(args) => {
                  const vis = e => e && e.offsetParent !== null;
                  const norm = s => (s||'').toLowerCase().replace(/\\s+/g,' ').trim();
                  const chip = Array.from(document.querySelectorAll(
                    '[draggable=true], button, [role=option], li'))
                    .filter(vis).find(e => norm(e.innerText) === norm(args[0]));
                  const slots = Array.from(document.querySelectorAll(
                    '[data-slot], [class*=slot i], [class*=position i], ' +
                    '[class*=order i]')).filter(vis);
                  const slot = slots[args[1]] || null;
                  if (!chip || !slot) return null;
                  const cr = chip.getBoundingClientRect();
                  const sr = slot.getBoundingClientRect();
                  return {cx: cr.x + cr.width / 2, cy: cr.y + cr.height / 2,
                          sx: sr.x + sr.width / 2, sy: sr.y + sr.height / 2};
                }""", [name, i])
            except Exception:
                boxes = None
            if not boxes:
                return "failed", [], \
                    f"populous drag: chip/slot not found for {name!r} slot {i}"
            try:
                page.mouse.move(boxes["cx"], boxes["cy"])
                page.mouse.down()
                page.wait_for_timeout(150)
                page.mouse.move(boxes["sx"], boxes["sy"], steps=10)
                page.wait_for_timeout(150)
                page.mouse.up()
            except Exception as e:
                return "failed", [], \
                    f"populous drag failed for {name!r}: {str(e)[:80]}"
            _settle(page, base=700)
            try:
                slot_txt = page.evaluate("""(args) => {
                  const vis = e => e && e.offsetParent !== null;
                  const slots = Array.from(document.querySelectorAll(
                    '[data-slot], [class*=slot i], [class*=position i], ' +
                    '[class*=order i]')).filter(vis);
                  const s = slots[args] || null;
                  return s ? (s.innerText || '').trim() : '';
                }""", i) or ""
            except Exception:
                slot_txt = ""
            _dbg(gid, f"worldle populous drag slot {i}: want {name!r} "
                      f"slot shows {slot_txt!r}")
            if _nm(slot_txt) != _nm(name):
                return "failed", [], \
                    f"populous drag slot {i} shows {slot_txt!r}, want {name!r}"
    else:
        # Click-to-order: click in pop-desc order, verify selected
        # state per click.
        for name in want:
            ok, detail = _s_worldle_click_option_text(page, name)
            if not ok:
                ok, detail = _s_worldle_click_option_loose(page, name)
            _dbg(gid, f"worldle populous click {name!r}: ok={ok} ({detail[:50]})")
            if not ok:
                return "failed", [], \
                    f"populous cities click failed: {name!r}"
            _settle(page, base=700)
            try:
                sel = page.evaluate("""(want) => {
                  const vis = e => e && e.offsetParent !== null;
                  const norm = s => (s||'').toLowerCase().replace(/\\s+/g,' ').trim();
                  const el = Array.from(document.querySelectorAll(
                    'button, [role=option], li, [draggable=true]'))
                    .filter(vis).find(e => norm(e.innerText) === norm(want));
                  if (!el) return null;
                  return {selected: el.getAttribute('aria-selected'),
                          cls: String(el.className || '').slice(0, 80),
                          pressed: el.getAttribute('aria-pressed')};
                }""", name)
            except Exception:
                sel = None
            _dbg(gid, f"worldle populous selected-state {name!r}: {sel}")
    _s_worldle_submit_guarded(page, gid, "populousCities", ds)
    _settle(page, base=2200)
    return "attempted", want, f"ordered top-{n_exp} cities by population"


def _worldle_area_parse_display(texts):
    """Parse the area stepper's displayed value from the candidate
    texts {common, between, roleBtn, store, body} plus the iter-7
    keys {commonText, betweenText, pure, aria} plus the iter-9 key
    {inputs} (live <input> values inside the widget).

    Fix-loop iter 11 — reader FIRST, no distractor numbers: the old
    code took max() over the widget-container text, so a distractor
    number elsewhere in the container (start=111369) became the
    "display" and the steppers "never moved" it. Rules now:
    (1) the element BETWEEN the steppers is the display — its number
    wins; (2) a SINGLE pure-number leaf / aria / input value inside
    the widget wins; (3) the common-container text wins ONLY when it
    holds EXACTLY ONE number candidate (unit-qualified or bare) —
    multiple candidates = ambiguous = None, never max(); (4) body /
    store / roleBtn sources are dropped (not widget-local). Return
    None (unknown) rather than a distractor. Pure function
    (unit-tested on fixture DOM texts).
    """
    import re as _re

    def _nums(txt):
        out = []
        for _m in _re.finditer(
                r"(\d[\d,]*)\s*(km2|km²|sq\.?\s*km|square km|km)",
                txt, _re.IGNORECASE):
            try:
                out.append(int(_m.group(1).replace(",", "")))
            except Exception:
                pass
        return out

    def _bare(txt):
        out = []
        for _x in _re.findall(r"\b\d[\d,]{2,}\b", txt):
            try:
                _v = int(_x.replace(",", ""))
            except Exception:
                continue
            if _v >= 100:
                out.append(_v)
        return out

    def _single(vals):
        uniq = sorted(set(vals))
        return uniq[0] if len(uniq) == 1 else None

    # (1) between the steppers — the display element.
    for src in ("between", "betweenText"):
        txt = str((texts or {}).get(src) or "")
        if not txt:
            continue
        v = _single(_nums(txt)) if _nums(txt) else _single(_bare(txt))
        if v is not None:
            return v, src + ":display"
    # (2) single pure-number leaf / aria / live input inside the widget.
    for src in ("pure", "aria", "inputs"):
        txt = str((texts or {}).get(src) or "")
        if not txt:
            continue
        vals = []
        for _line in txt.split("\n"):
            _line = _line.strip()
            if _re.fullmatch(r"\d[\d,]*", _line):
                try:
                    vals.append(int(_line.replace(",", "")))
                except Exception:
                    pass
        v = _single(vals)
        if v is not None:
            return v, src + ":single"
    # (3) common container: exactly ONE candidate or unknown.
    for src in ("common", "commonText"):
        txt = str((texts or {}).get(src) or "")
        if not txt:
            continue
        cands = _nums(txt) or _bare(txt)
        v = _single(cands)
        if v is not None:
            return v, src + ":single"
    return None, None


# Shared stepper-candidate selection for the Worldle area widget
# (fix-loop iter 9, ANALYSIS6 §2d). Used by BOTH the probe and the
# display reader so the two can never disagree about which controls
# are the steppers. Defines `st` = the chosen stepper pair (JS).
#
# Iter 7's filter matched EMPTY-text buttons only, so it caught the
# previous/next-round NAVIGATION arrows (icon-only buttons with
# data-testid="previous-arrow-button" / "next-arrow-button" — the
# rerun #6 failure) while excluding the real +/- steppers (which carry
# `+`/`-` text, increment/decrement aria-labels, or are
# div[role=button]). This filter: EXCLUDES nav arrows by
# data-testid / aria-label / text (never by position), INCLUDES
# +/`-` text, increment/decrement aria-labels, div[role=button], and
# icon-only controls, and when >2 candidates remain picks the pair
# with the DEEPEST common ancestor (the tight widget container, not
# two far-apart page controls). Pure JS fragment (unit-tested in node
# against a fixture DOM containing nav arrows).
_AREA_STEPPER_SELECT_JS = """
  const _areaIsNav = e => {
    const tid = (e.getAttribute('data-testid') || '');
    const aria = (e.getAttribute('aria-label') || '');
    const t = (e.innerText || '').trim();
    if (/arrow|chevron|previous|prev|next|back|forward/i.test(tid)) return true;
    if (/arrow|chevron|previous|next round|back|forward|go to/i.test(aria)) return true;
    if (/give up|next round|remove ads/i.test(t)) return true;
    return false;
  };
  const _areaIsStepper = e => {
    if (_areaIsNav(e)) return false;
    const t = (e.innerText || '').trim();
    const aria = (e.getAttribute('aria-label') || '');
    if (/^[+\\-\\u2212]$/.test(t) || /^[+\\-\\u2212]\\d+$/.test(t)) return true;
    if (/increment|decrement|increase|decrease|step up|step down|\\bplus\\b|\\bminus\\b/i.test(aria)) return true;
    if (/^(increase|decrease|increment|decrement|plus|minus)$/i.test(t)) return true;
    // Fix-loop iter 11: a bare icon-only button (no text, no aria)
    // counts ONLY as part of a tight pair — the LCA check below
    // rejects pairs whose common ancestor is BODY/HTML (outside any
    // widget container). Icon-only widget buttons (iter-9 fixture:
    // empty text, tight widget LCA) still qualify; far-apart nav
    // arrows / close buttons do not.
    if (!t && !aria) return true;
    return false;
  };
  const _areaCands = Array.from(document.querySelectorAll(
    'button, div[role=button]'))
    .filter(e => e && e.offsetParent !== null)
    .filter(e => !e.closest('nav, header, footer'))
    // Iter-15: exclude the GetAdmiral ad container (id _1SS2i7sB,
    // present on every round's page) — in iter-14 the stepper probe
    // found ONLY that ad div as a stepper candidate.
    // Iter-16: same exclusion for the other ad units on the page
    // (adngin / Snigel / google ads) — an ad-div button must never
    // be driven as a stepper.
    .filter(e => !e.closest('#_1SS2i7sB, [id*=GetAdmiral i], '
      + '[class*=admiral i], [id*=getadmiral i], [id*=adngin i], '
      + '[class*=adngin i], [id*=advertisement i], '
      + '[class*=advertisement i], [id*=google_ads i], '
      + '[class*=adsbygoogle i], [id*=snigel i], [class*=snigel i]'))
    .filter(_areaIsStepper);
  const _areaAnc = el => { const a = []; let n = el;
    while (n) { a.push(n); n = n.parentElement; } return a; };
  const _areaLca = (a, b) => { const aa = _areaAnc(a);
    for (const n of _areaAnc(b)) if (aa.includes(n)) return n;
    return null; };
  let st = _areaCands.slice(0, 2);
  if (_areaCands.length > 2) {
    let best = null, bestDepth = -1;
    for (let i = 0; i < _areaCands.length; i++)
      for (let j = i + 1; j < _areaCands.length; j++) {
        const l = _areaLca(_areaCands[i], _areaCands[j]);
        const d = l ? _areaAnc(l).length : -1;
        if (d > bestDepth) { bestDepth = d; best = [_areaCands[i], _areaCands[j]]; }
      }
    if (best) st = best;
  }
  // Fix-loop iter 11: the chosen pair must share a NON-BODY/HTML
  // common ancestor (a tight widget container). Two far-apart page
  // controls whose LCA is BODY/HTML are not a stepper pair — treat
  // as no steppers, never blind-click.
  if (st.length >= 2) {
    const l = _areaLca(st[0], st[1]);
    if (!l || /^(BODY|HTML)$/.test(l.tagName)) st = [];
  }
"""


def _area_stepper_boxes(page):
    """Re-resolve the stepper pair's LIVE boxes (fix-loop iter 11):
    clicks can re-render the widget, so cached boxes go stale — the
    drive loop re-resolves every batch. scrollIntoView first so the
    rects are viewport-relative and clickable. Returns [{x,y,w,h}]."""
    try:
        return page.evaluate("""() => {
          const vis = e => e && e.offsetParent !== null;
""" + _AREA_STEPPER_SELECT_JS + """
          for (const e of st) {
            try { e.scrollIntoView({block: 'center'}); } catch (err) {}
          }
          return st.map(e => { const r = e.getBoundingClientRect();
            return {x: r.x, y: r.y, w: r.width, h: r.height}; });
        }""") or []
    except Exception:
        return []


def _s_worldle_comparison_probe(page, prompt_substrs):
    """Probe the live comparison widget (Area / GDP): returns
    {prompt_found, options: [{name, x, y, has_img, text, aria, alt}]}.

    The prompt must match ALL of `prompt_substrs` (Area: 'larger
    surface area'; GDP: 'highest' + 'per capita gdp') — a single
    substring would also match the round's TAB label ('Size
    Comparison'), which is present even when the comparison
    sub-question is not showing. Options are visible clickable
    candidates in the prompt's container: image cards (silhouette /
    flag with a country-name label) AND text buttons — the old code
    only read button innerText, so image-card options ('Saudi Arabia'
    in the GDP comparison) were 'not found'. Numbered tab labels
    ('2. Size Comparison'), tab elements, and submit/give-up chrome
    are excluded, so tabs can never be mistaken for options."""
    try:
        return page.evaluate("""/* worldle-comparison-probe */
          (subs) => {
          const vis = e => e && e.offsetParent !== null;
          const norm = s => (s||'').toLowerCase().replace(/\\s+/g,' ').trim();
          const bodyText = norm(document.body.innerText || '');
          const promptFound = subs.every(s => bodyText.includes(norm(s)));
          if (!promptFound) return {prompt_found: false, options: []};
          let container = document.body;
          const holders = Array.from(document.querySelectorAll(
            'section, form, div, fieldset'))
            .filter(vis).filter(e => !e.closest('nav, header, footer'))
            .filter(e => subs.every(s => norm(e.innerText || '')
              .includes(norm(s))));
          holders.sort((a, b) => (a.innerText || '').length -
            (b.innerText || '').length);
          for (const h of holders) {
            const n = h.querySelectorAll(
              'button, [role=option], [role=radio], [role=button], a, ' +
              'label, [data-value]').length;
            if (n >= 2) { container = h; break; }
          }
          const chrome = new Set(['guess', 'submit', 'check', 'confirm',
            'continue', 'next', 'save', 'give up', 'next round',
            'remove ads', 'try again', 'skip']);
          // Mode labels are never country options: when the prompt
          // and the options live in sibling containers, the probe
          // falls back to document.body, and the round-nav toggle
          // (showing the current mode label, e.g. 'Area') would
          // otherwise be collected as a bogus option and force a
          // false partial. Toggles (aria-expanded/haspopup) are
          // excluded for the same reason.
          const modeLabels = new Set(['country', 'neighbours', 'capital',
            'orientation', 'flag', 'trivia', 'landmark', 'emblem',
            'spoken languages', 'gdp per capita',
            'population & currency', 'populous cities', 'area',
            'summary']);
          // Generic card titles carry NO country identity: two image
          // cards both titled 'Image Quiz Item' are two distinct
          // options (iter-12: the name dedup collapsed them to 1 and
          // nothing was clicked). Generic names never dedup, and the
          // sibling label (the name pill below the card) is the
          // real name source for image cards.
          const genericNames = new Set(['image quiz item', 'quiz item',
            'image', 'option', 'choice', 'card']);
          const isLabelText = (t) => {
            const s = (t || '').trim();
            if (!s || s.length < 3 || s.length > 40) return false;
            if (/^\s*\d+\s*\./.test(s)) return false;
            if (/(comparison|guess the|which country|surface area|gdp|per capita|vs|versus)/i.test(s)) return false;
            if (chrome.has(norm(s)) || modeLabels.has(norm(s))) return false;
            if (s.split(/\s+/).length > 4) return false;
            return /^[A-Za-zÀ-ÿ'’.\- ]+$/.test(s);
          };
          const sibLabel = (e) => {
            // Iter-25 (ANALYSIS22 RC-new-1): DIRECT siblings only
            // (the label pill below the card). The old version
            // climbed 3 ancestors and scanned whole subtrees, so
            // ANY element sharing a high ancestor with the cards
            // (e.g. a header nav button) "matched" every country
            // name — that is how the Area click landed on a 40x40
            // chrome button. Ancestor scans never name an option.
            const sibs = [];
            if (e.nextElementSibling) sibs.push(e.nextElementSibling);
            if (e.previousElementSibling) sibs.push(e.previousElementSibling);
            for (const c of sibs) {
              const t = (c.innerText || '').trim();
              if (c.querySelector('button, img, input, select')) continue;
              if (isLabelText(t)) return t;
            }
            return '';
          };
          const descLabel = (e) => {
            // Label pill INSIDE the card itself (a descendant leaf
            // carrying the country name). Scoped to the element —
            // unlike an ancestor scan it cannot leak a name from
            // another part of the page onto this candidate.
            for (const cand of e.querySelectorAll('div, span, p, label')) {
              if (cand.querySelector('button, img, input, select')) continue;
              const t = (cand.innerText || '').trim();
              if (isLabelText(t)) return t;
            }
            return '';
          };
          const isChromeCandidate = (e) => {
            // Iter-25: a small (~40x40) control in the top header
            // band (prev/next arrows, banner X, menu) is chrome,
            // never a comparison option — even if its own text
            // or a nearby label looks country-ish.
            try {
              const r = e.getBoundingClientRect();
              return r.y < 160 && r.width <= 56 && r.height <= 56;
            } catch (err) { return false; }
          };
          const out = []; const seen = new Set();
          const cands = Array.from(container.querySelectorAll(
            'button, [role=option], [role=radio], [role=button], a, ' +
            'label, [data-value]'))
            .filter(vis).filter(e => !e.closest('nav, header, footer'))
            .filter(e => !isChromeCandidate(e))
            .filter(e => e.getAttribute('role') !== 'tab')
            .filter(e => !e.closest('[role=tablist]'))
            .filter(e => e.getAttribute('aria-selected') === null)
            .filter(e => e.getAttribute('aria-expanded') === null)
            .filter(e => e.getAttribute('aria-haspopup') === null);
          // Plain-div pills as candidates too: in some layouts the
          // option card IS a div (pill with the country name, no
          // button). A pill div qualifies only when it carries a
          // leaf country-ish label and sits near an image card; the
          // merge step below drops a pill that is just the label of
          // an already-collected button card (same name, nearby).
          const divCands = Array.from(container.querySelectorAll('div'))
            .filter(vis).filter(e => !e.closest('nav, header, footer'))
            .filter(e => !isChromeCandidate(e))
            .filter(e => !e.querySelector('button, img, input, select'))
            .filter(e => isLabelText(e.innerText || ''));
          const allCands = cands.concat(divCands);
          for (const e of allCands) {
            const img = e.querySelector('img');
            const firstLine = (e.innerText || '').split('\\n')
              .map(s => s.trim()).filter(Boolean)[0] || '';
            const aria = e.getAttribute('aria-label') || '';
            const dval = e.getAttribute('data-value') || '';
            const title = e.getAttribute('title') || '';
            const alt = img ? (img.getAttribute('alt') || '') : '';
            const src = img ? (img.getAttribute('src') || '') : '';
            // Name resolution: a generic first-line title ('Image
            // Quiz Item') is NOT a name — the sibling label pill is.
            let name = firstLine || '';
            if (!name || genericNames.has(norm(name))) {
              name = sibLabel(e) || descLabel(e) || aria || dval || title || alt || name;
            }
            if (!name || name.length > 80) continue;
            if (chrome.has(norm(name))) continue;
            if (modeLabels.has(norm(name))) continue;
            // Numbered tab labels ('2. Size Comparison') and any
            // label mentioning the comparison/guess prompt are tabs
            // or prompts, never country options.
            if (/^\\s*\\d+\\s*\\./.test(name)) continue;
            if (/(comparison|guess the)/i.test(name)) continue;
            if (name.split(/\\s+/).length > 5) continue;
            // Iter-14 P1 (ANALYSIS10 Area root cause): map-control
            // buttons ('Zoom out icon', 'Previous icon', 'Next icon')
            // polluted the probe and blocked the pick. Exclude icon/
            // control elements: resolved name ending in 'icon', any
            // of aria/title/alt containing 'icon', or a small
            // icon-only button (<=48x48, no image, no country text).
            const _iconish = (s) => /icon/i.test(s || '');
            if (/icon\\s*$/.test(name) || _iconish(aria) ||
                _iconish(title) || _iconish(alt)) continue;
            const r = e.getBoundingClientRect();
            if (isChromeCandidate(e)) continue;
            if (r.width <= 48 && r.height <= 48 && !img &&
                !isLabelText(firstLine)) continue;
            const cx = r.x + r.width / 2, cy = r.y + r.height / 2;
            // Dedup is POSITION-aware (iter-12 fix): two distinct
            // cards sharing a generic title must NOT collapse.
            // Nested duplicates (button + role=button wrapper) share
            // a centre and still collapse. A pill div whose name
            // matches a nearby button card is that card's label, not
            // a second option — merge it away.
            let duplicate = false;
            for (const o of out) {
              if (norm(o.name) === norm(name) &&
                  Math.abs(o.x - cx) < 200 && Math.abs(o.y - cy) < 200) {
                duplicate = true; break;
              }
              if (norm(o.name) === norm(name) &&
                  Math.abs(o.x - cx) < 20 && Math.abs(o.y - cy) < 20) {
                duplicate = true; break;
              }
            }
            if (duplicate) continue;
            const key = norm(name) + '|' + Math.round(cx / 20) + ',' +
              Math.round(cy / 20);
            if (seen.has(key)) continue;
            seen.add(key);
            out.push({name: name, x: cx, y: cy, has_img: !!img,
                      rect: {x: Math.round(r.x), y: Math.round(r.y),
                             w: Math.round(r.width),
                             h: Math.round(r.height)},
                      text: (e.innerText || '').slice(0, 120),
                      aria: aria.slice(0, 80), alt: alt.slice(0, 80),
                      title: title.slice(0, 80),
                      src: src.slice(0, 160)});
          }
          return {prompt_found: true, options: out};
        }""", list(prompt_substrs)) or {}
    except Exception:
        return {}


def _s_worldle_click_comparison_option(page, want, _exclude=(),
                                      container=None):
    """Click a comparison option by country name, matching first-line
    innerText, aria-label, data-value, title, a descendant img alt,
    or the sibling label pill (image-card options carry the generic
    title 'Image Quiz Item' in button text and the country name in a
    label pill below the card). Plain-div pills are a fallback only:
    interactive elements are tried first so a click never lands on a
    non-interactive label when a button with the same name exists.
    Returns (ok, detail).

    Iter-20 (ANALYSIS17 §2i): the iter-19 click evidently never
    selected the option (both buttons unselected in frame gp_0360,
    store 0->0 twice) but the old code clicked the FIRST matching
    element and reported success with no evidence of which target
    it hit (card vs button) or whether a selection resulted. This
    version (a) uses rect-based visibility (offsetParent-based
    visibility has lied before on this page), (b) enumerates ALL
    matching candidates with kind (button/card/div), tag, class
    and centre, (c) clicks ONE candidate — buttons first, then
    cards, then div pills — and (d) names that target + its
    centre in the detail, so the caller can log the post-click
    selected state and, when nothing is selected, retry with
    the NEXT candidate via `_exclude` (card-vs-button fallback)
    instead of re-clicking the same dead target. `ok` means a
    click was dispatched, NOT that a selection resulted — the
    caller verifies selection with
    _s_worldle_comparison_selected_state and the store.

    Iter-25 (ANALYSIS22 RC-new-1): the JS matcher no longer
    climbs ancestors for a sibling label (that climb let a 40x40
    header nav button "match" every country name); names come
    from the element itself, a DIRECT sibling label, or a
    descendant label inside the candidate. Small (~40x40)
    controls in the top header band are excluded as chrome.
    Area container (from _s_worldle_visible_area_ui). When given,
    ONLY candidates whose centre lies inside that container are
    clickable — the iter-21 click picked the FIRST document-order
    name match anywhere (a stale hidden-panel button) and flipped
    the view Area -> PopulationCurrency. The returned `detail`
    now names the clicked element's tag/class/rect/coordinates
    (not just its innerText), so the click target is PROVEN in
    the log instead of `clicked=''`."""
    try:
        hit = page.evaluate("""/* worldle-comparison-click */
          (args) => {
          const want = (args && args.want) || args;
          const exclude = (args && args.exclude) || [];
          // Iter-22: container restriction — only candidates
          // whose CENTRE lies inside the verified-visible Area
          // container (rect from the visible-round probe) may be
          // clicked. A stale hidden-panel button with the same
          // name sits OUTSIDE that container and is skipped.
          const container = (args && args.container) || null;
          const inContainer = e => {
            if (!container || !container.rect) return true;
            try {
              const r = e.getBoundingClientRect();
              const cx = r.x + r.width / 2, cy = r.y + r.height / 2;
              const cr = container.rect;
              return cx >= cr.x - 8 && cx <= cr.x + cr.w + 8 &&
                     cy >= cr.y - 8 && cy <= cr.y + cr.h + 8;
            } catch (err) { return false; }
          };
          const describe = e => {
            try {
              const r = e.getBoundingClientRect();
              return [r.x, r.y, r.width, r.height,
                      r.x + r.width / 2, r.y + r.height / 2];
            } catch (err) { return [0, 0, 0, 0, 0, 0]; }
          };
          // Iter-20: rect-based visibility (offsetParent lies for
          // fixed/portal subtrees on this page — see G4/banner).
          const vis = e => { try { const r = e.getBoundingClientRect();
            return r.width > 0 && r.height > 0; }
            catch (err) { return false; } };
          const norm = s => (s||'').toLowerCase().replace(/\\s+/g,' ').trim();
          const normDiac = s => norm(s).normalize('NFD')
            .replace(/[\\u0300-\\u036f]/g, '');
          const w = normDiac(want);
          const isLabelText = (t) => {
            const s = (t || '').trim();
            if (!s || s.length < 3 || s.length > 40) return false;
            if (/^\\s*\\d+\\s*\\./.test(s)) return false;
            if (/(comparison|guess the|which country|surface area|gdp|per capita|vs|versus)/i.test(s)) return false;
            if (s.split(/\\s+/).length > 4) return false;
            return /^[A-Za-zÀ-ÿ'’.\\- ]+$/.test(s);
          };
          const sibLabel = (e) => {
            // Iter-25: DIRECT siblings only — see the probe for
            // why the ancestor climb had to go (it let a header
            // nav button "match" every country name).
            const sibs = [];
            if (e.nextElementSibling) sibs.push(e.nextElementSibling);
            if (e.previousElementSibling) sibs.push(e.previousElementSibling);
            for (const c of sibs) {
              const t = (c.innerText || '').trim();
              if (c.querySelector('button, img, input, select')) continue;
              if (isLabelText(t)) return t;
            }
            return '';
          };
          const descLabel = (e) => {
            // Label pill INSIDE the candidate itself (descendant
            // leaf). Scoped to the element, so it cannot leak a
            // name from elsewhere on the page onto this candidate.
            for (const cand of e.querySelectorAll('div, span, p, label')) {
              if (cand.querySelector('button, img, input, select')) continue;
              const t = (cand.innerText || '').trim();
              if (isLabelText(t)) return t;
            }
            return '';
          };
          const isChromeCandidate = (e) => {
            // Iter-25: a small (~40x40) control in the top header
            // band is chrome, never a comparison option.
            try {
              const r = e.getBoundingClientRect();
              return r.y < 160 && r.width <= 56 && r.height <= 56;
            } catch (err) { return false; }
          };
          const genericNames = new Set(['image quiz item', 'quiz item', 'image', 'option', 'choice', 'card']);
          const match = e => {
            const firstLine = (e.innerText || '').split('\\n')
              .map(s => s.trim()).filter(Boolean)[0] || '';
            const img = e.querySelector('img');
            let primary = firstLine || '';
            if (!primary || genericNames.has(normDiac(primary))) {
              primary = sibLabel(e) || descLabel(e) || primary;
            }
            const vals = [primary, firstLine, sibLabel(e), descLabel(e),
              e.getAttribute('aria-label') || '',
              e.getAttribute('data-value') || '',
              e.getAttribute('title') || '',
              img ? (img.getAttribute('alt') || '') : ''];
            return vals.some(v => normDiac(v) === w);
          };
          const interactive = Array.from(document.querySelectorAll(
            'button, [role=option], [role=radio], [role=button], a, ' +
            'label, [data-value]')).filter(vis)
            .filter(e => !e.closest('nav, header, footer'))
            .filter(e => !isChromeCandidate(e))
            .filter(inContainer);
          const divs = Array.from(document.querySelectorAll('div'))
            .filter(vis).filter(e => !e.closest('nav, header, footer'))
            .filter(e => !isChromeCandidate(e))
            .filter(inContainer)
            .filter(e => !e.querySelector('button, img, input, select'))
            .filter(e => isLabelText(e.innerText || ''));
          // Iter-20 (ANALYSIS17 §2i): BUTTON first, CARD second —
          // the caller needs to know WHICH target was clicked so
          // the post-click selected-state log can tell a
          // button-click that never selected from a card-click
          // that did. The old code fell through to divs silently.
          const hitBtn = interactive.find(match);
          if (hitBtn) { hitBtn.click();
            const d = describe(hitBtn);
            return [true, (hitBtn.innerText || '').trim().slice(0, 80),
                    'button', hitBtn.tagName,
                    String(hitBtn.getAttribute('class') || '')
                      .slice(0, 60),
                    {x: Math.round(d[0]), y: Math.round(d[1]),
                     w: Math.round(d[2]), h: Math.round(d[3])},
                    Math.round(d[4]), Math.round(d[5])]; }
          const hitCard = divs.find(match);
          if (hitCard) { hitCard.click();
            const d = describe(hitCard);
            return [true, (hitCard.innerText || '').trim().slice(0, 80),
                    'card', hitCard.tagName,
                    String(hitCard.getAttribute('class') || '')
                      .slice(0, 60),
                    {x: Math.round(d[0]), y: Math.round(d[1]),
                     w: Math.round(d[2]), h: Math.round(d[3])},
                    Math.round(d[4]), Math.round(d[5])]; }
          return [false, container ?
            'no candidate inside verified Area container' :
            'not found', 'none', '', '', null, null, null];
        }""", {"want": str(want),
               "exclude": list(_exclude or []),
               "container": container})
        if isinstance(hit, (list, tuple)) and len(hit) >= 8:
            _rect = hit[5] if isinstance(hit[5], dict) else None
            _detail = (str(hit[1] or "")
                       + f" | tag={hit[3] or ''} "
                       f"cls={str(hit[4] or '')[:60]} "
                       f"rect={_rect} "
                       f"centre=({hit[6]}, {hit[7]})")
            return (bool(hit[0]), _detail, str(hit[2] or "none"))
        if isinstance(hit, (list, tuple)) and len(hit) >= 3:
            _detail = str(hit[1] or "")
            if len(hit) >= 5 and (hit[3] or hit[4]):
                _detail += (f" | tag={hit[3] or ''} "
                            f"cls={str(hit[4] or '')[:60]}")
            return (bool(hit[0]), _detail, str(hit[2] or "none"))
        return bool(hit and hit[0]), str((hit or [None, ""])[1] or ""), \
            "unknown"
    except Exception as e:
        return False, "exception: " + str(e)[:80], "error"


def _s_worldle_click_comparison_card(page, want, container=None):
    """Click ONLY the comparison option CARD (non-interactive div
    card / label pill) for `want` — the iter-20 second attempt when
    a BUTTON click produced no selected state (ANALYSIS17 §2i:
    frame gp_0360 shows Kuwait/Qatar shape CARDS + Kuwait/Qatar
    BUTTONS; the button click evidently never selected, so the card
    itself is the next target). Returns (ok, detail, via='card').
    Never clicks a button: cards only.

    Iter-22 (ANALYSIS19 §3c): same `container` restriction and
    tag/class/rect/coordinates detail as
    _s_worldle_click_comparison_option."""
    try:
        hit = page.evaluate("""/* worldle-comparison-card-click */
          (args) => {
          const want = (args && args.want) || args;
          const container = (args && args.container) || null;
          const inContainer = e => {
            if (!container || !container.rect) return true;
            try {
              const r = e.getBoundingClientRect();
              const cx = r.x + r.width / 2, cy = r.y + r.height / 2;
              const cr = container.rect;
              return cx >= cr.x - 8 && cx <= cr.x + cr.w + 8 &&
                     cy >= cr.y - 8 && cy <= cr.y + cr.h + 8;
            } catch (err) { return false; }
          };
          const describe = e => {
            try {
              const r = e.getBoundingClientRect();
              return [r.x, r.y, r.width, r.height,
                      r.x + r.width / 2, r.y + r.height / 2];
            } catch (err) { return [0, 0, 0, 0, 0, 0]; }
          };
          const vis = e => e && e.offsetParent !== null;
          const norm = s => (s||'').toLowerCase().replace(/\\s+/g,' ').trim();
          const normDiac = s => norm(s).normalize('NFD')
            .replace(/[\\u0300-\\u036f]/g, '');
          const w = normDiac(want);
          const isLabelText = (t) => {
            const s = (t || '').trim();
            if (!s || s.length < 3 || s.length > 40) return false;
            if (/^\\s*\\d+\\s*\\./.test(s)) return false;
            if (/(comparison|guess the|which country|surface area|gdp|per capita|vs|versus)/i.test(s)) return false;
            if (s.split(/\\s+/).length > 4) return false;
            return /^[A-Za-zÀ-ÿ'’.\\- ]+$/.test(s);
          };
          const sibLabel = (e) => {
            const sibs = [];
            if (e.nextElementSibling) sibs.push(e.nextElementSibling);
            if (e.previousElementSibling) sibs.push(e.previousElementSibling);
            for (const c of sibs) {
              const t = (c.innerText || '').trim();
              if (c.querySelector('button, img, input, select')) continue;
              if (isLabelText(t)) return t;
            }
            return '';
          };
          const descLabel = (e) => {
            // Iter-25: the Area label bar ("Kuwait" / "Qatar") is
            // a DESCENDANT of the card div, not a sibling — without
            // this the card itself never matches its own label.
            for (const cand of e.querySelectorAll('div, span, p, label')) {
              if (cand.querySelector('button, img, input, select')) continue;
              const t = (cand.innerText || '').trim();
              if (isLabelText(t)) return t;
            }
            return '';
          };
          const isChromeCandidate = (e) => {
            // Iter-25: a small (~40x40) control in the top header
            // band is chrome, never a comparison card.
            try {
              const r = e.getBoundingClientRect();
              return r.y < 160 && r.width <= 56 && r.height <= 56;
            } catch (err) { return false; }
          };
          const genericNames = new Set(['image quiz item', 'quiz item', 'image', 'option', 'choice', 'card']);
          const match = e => {
            const firstLine = (e.innerText || '').split('\\n')
              .map(s => s.trim()).filter(Boolean)[0] || '';
            const img = e.querySelector('img');
            let primary = firstLine || '';
            if (!primary || genericNames.has(normDiac(primary))) {
              primary = sibLabel(e) || descLabel(e) || primary;
            }
            const vals = [primary, firstLine, sibLabel(e), descLabel(e),
              e.getAttribute('aria-label') || '',
              e.getAttribute('data-value') || '',
              e.getAttribute('title') || '',
              img ? (img.getAttribute('alt') || '') : ''];
            return vals.some(v => normDiac(v) === w);
          };
          // CARDS ONLY: divs with no interactive descendant, plus
          // elements carrying an image (shape card) that are not
          // buttons. Buttons / [role=button] / links are excluded
          // here — this is the second attempt after a button click
          // produced no selection.
          const cards = Array.from(document.querySelectorAll(
            'div, section, figure, li'))
            .filter(vis).filter(e => !e.closest('nav, header, footer'))
            .filter(e => !isChromeCandidate(e))
            .filter(inContainer)
            .filter(e => !e.closest('button, a, [role=button]'))
            .filter(e => e.tagName !== 'BUTTON')
            .filter(e => (e.querySelector('img') ||
                          isLabelText(e.innerText || '')));
          const hit = cards.find(match);
          if (!hit) return [false, container ?
            'no card inside verified Area container' :
            'no card found', 'card', '', '', null, null, null];
          hit.click();
          const d = describe(hit);
          return [true, (hit.innerText || '').trim().slice(0, 80),
                  'card', hit.tagName,
                  String(hit.getAttribute('class') || '').slice(0, 60),
                  {x: Math.round(d[0]), y: Math.round(d[1]),
                   w: Math.round(d[2]), h: Math.round(d[3])},
                  Math.round(d[4]), Math.round(d[5])];
        }""", {"want": str(want), "container": container})
        if isinstance(hit, (list, tuple)) and len(hit) >= 8:
            _rect = hit[5] if isinstance(hit[5], dict) else None
            _detail = (str(hit[1] or "")
                       + f" | tag={hit[3] or ''} "
                       f"cls={str(hit[4] or '')[:60]} "
                       f"rect={_rect} "
                       f"centre=({hit[6]}, {hit[7]})")
            return (bool(hit[0]), _detail, str(hit[2] or "card"))
        if isinstance(hit, (list, tuple)) and len(hit) >= 3:
            _detail = str(hit[1] or "")
            if len(hit) >= 5 and (hit[3] or hit[4]):
                _detail += (f" | tag={hit[3] or ''} "
                            f"cls={str(hit[4] or '')[:60]}")
            return (bool(hit[0]), _detail, str(hit[2] or "card"))
        return bool(hit and hit[0]), str((hit or [None, ""])[1] or ""), \
            "card"
    except Exception as e:
        return False, "exception: " + str(e)[:80], "error"


def _s_worldle_comparison_selected_state(page, want):
    """Post-click selected-state probe for a comparison widget
    (iter-20, ANALYSIS17 §2i). Returns {selected, selected_name,
    selected: [{name, tag, cls, signals}], options: [{name, tag,
    cls, selected, signals}]}.

    An option counts as SELECTED when any of: aria-selected=true,
    aria-checked=true, aria-pressed=true, .checked, dataset.selected
    set, or it (or an ancestor within 2 levels) carries a
    selected/active/checked/chosen class. The probe is pure (no
    clicks) and runs IMMEDIATELY after the comparison click, before
    any submit — so the next artifact shows whether the click
    selected anything at all, instead of only the store-verify
    0->0 after the fact. The continuous gameplay recording is the
    post-click frame; this log line (timestamped) is its marker."""
    try:
        _res = page.evaluate("""/* worldle-comparison-selected-state */
          (want) => {
          const vis = e => e && e.offsetParent !== null;
          const norm = s => (s||'').toLowerCase().replace(/\\s+/g,' ').trim();
          const normDiac = s => norm(s).normalize('NFD')
            .replace(/[\\u0300-\\u036f]/g, '');
          const w = normDiac(want);
          const isLabelText = (t) => {
            const s = (t || '').trim();
            if (!s || s.length < 3 || s.length > 40) return false;
            if (/^\\s*\\d+\\s*\\./.test(s)) return false;
            if (/(comparison|guess the|which country|surface area|gdp|per capita|vs|versus)/i.test(s)) return false;
            if (s.split(/\\s+/).length > 4) return false;
            return /^[A-Za-zÀ-ÿ'’.\\- ]+$/.test(s);
          };
          const sibLabel = (e) => {
            const sibs = [];
            if (e.nextElementSibling) sibs.push(e.nextElementSibling);
            if (e.previousElementSibling) sibs.push(e.previousElementSibling);
            for (const c of sibs) {
              const t = (c.innerText || '').trim();
              if (c.querySelector('button, img, input, select')) continue;
              if (isLabelText(t)) return t;
            }
            return '';
          };
          const descLabel = (e) => {
            // Iter-25: card label bars are descendants of the card.
            for (const cand of e.querySelectorAll('div, span, p, label')) {
              if (cand.querySelector('button, img, input, select')) continue;
              const t = (cand.innerText || '').trim();
              if (isLabelText(t)) return t;
            }
            return '';
          };
          const genericNames = new Set(['image quiz item', 'quiz item', 'image', 'option', 'choice', 'card']);
          const nameOf = e => {
            const firstLine = (e.innerText || '').split('\\n')
              .map(s => s.trim()).filter(Boolean)[0] || '';
            const img = e.querySelector('img');
            let primary = firstLine || '';
            if (!primary || genericNames.has(normDiac(primary))) {
              primary = sibLabel(e) || descLabel(e) || primary;
            }
            for (const v of [primary, firstLine, sibLabel(e), descLabel(e),
                e.getAttribute('aria-label') || '',
                e.getAttribute('data-value') || '',
                e.getAttribute('title') || '',
                img ? (img.getAttribute('alt') || '') : '']) {
              if (v && v.trim()) return v.trim();
            }
            return '';
          };
          const SEL_RE = /selected|active|checked|chosen|is-selected|is-active|is-checked/;
          const signalsOf = e => {
            const sig = [];
            if (e.getAttribute('aria-selected') === 'true') sig.push('aria-selected');
            if (e.getAttribute('aria-checked') === 'true') sig.push('aria-checked');
            if (e.getAttribute('aria-pressed') === 'true') sig.push('aria-pressed');
            if (e.checked === true) sig.push('checked');
            if ((e.dataset || {}).selected) sig.push('data-selected=' + e.dataset.selected);
            let node = e;
            for (let d = 0; d < 3 && node; d++) {
              const cls = String(node.getAttribute('class') || '');
              if (SEL_RE.test(cls)) { sig.push('class:' + cls.slice(0, 50)); break; }
              node = node.parentElement;
            }
            return sig;
          };
          const cands = Array.from(document.querySelectorAll(
            'button, [role=option], [role=radio], [role=button], a, ' +
            'label, [data-value], div'))
            .filter(vis).filter(e => !e.closest('nav, header, footer'))
            .filter(e => { try { const r = e.getBoundingClientRect();
              return !(r.y < 160 && r.width <= 56 && r.height <= 56);
              } catch (err) { return true; } })
            .filter(e => !e.closest('[role=tablist]'))
            .filter(e => e.getAttribute('role') !== 'tab');
          const seen = new Set();
          const options = [];
          for (const e of cands) {
            const name = nameOf(e);
            if (!name || name.length > 80) continue;
            if (/(comparison|guess the|which country)/i.test(name)) continue;
            if (/^\\s*\\d+\\s*\\./.test(name)) continue;
            const key = normDiac(name) + '|' + e.tagName;
            if (seen.has(key)) continue;
            seen.add(key);
            const sig = signalsOf(e);
            options.push({name: name, tag: e.tagName,
              cls: String(e.getAttribute('class') || '').slice(0, 60),
              selected: sig.length > 0, signals: sig});
          }
          const sel = options.filter(o => o.selected);
          // NOTE: `selected` (the LIST) is the field the caller
          // reads; `any_selected` is the bool form. A duplicate
          // `selected` key here previously made the bool
          // unreachable — kept as one key each.
          return {any_selected: sel.length > 0,
            selected_name: sel.length ? sel[0].name : '',
            selected_count: sel.length,
            selected: sel,
            options: options,
            wanted: want,
            wanted_selected: options.some(o =>
              normDiac(o.name) === w && o.selected)};
        }""", str(want))
        # A non-dict return (e.g. a test fake returning True, or
        # a null) must normalise to {} — the caller does
        # `(_sel or {}).get(...)`, which raises AttributeError on
        # a bool. The old `or {}` only covered falsy returns.
        return _res if isinstance(_res, dict) else {}
    except Exception:
        return {}


def _s_worldle_resolve_option_names_by_src(page, options):
    """Resolve generic comparison-option names via the option
    image src (iter-23, ANALYSIS20 §3). The Area widget renders
    two silhouette cards whose button text is the generic title
    'Image Quiz Item' and whose country name lives in a sibling
    label pill the probe could not read — so the pick saw
    'Image Quiz Item' x2, resolved neither, returned None, and
    the gate/click/re-verify fix NEVER EXECUTED. The probe
    already records each option's `src` ('…/country-shapes/kw.svg'
    / '…/country-shapes/qa.svg'): the 2-letter basename IS the
    country code, resolved to a name via the game's own CDN
    countries list (the same endpoint
    _s_worldle_fetch_country_stat uses). Returns
    (options_with_names, detail); on any fetch failure the
    options come back unchanged and the caller stays honest
    (partial, unresolvable) instead of guessing a name. Pure
    except for the single CDN code-map fetch."""
    _GENERIC = {"image quiz item", "quiz item", "image", "option",
                "choice", "card"}
    opts = [dict(o) if isinstance(o, dict) else o
            for o in (options or [])]
    need = {}
    for i, o in enumerate(opts):
        if not isinstance(o, dict):
            continue
        name = _s_worldle_cmp_key(o.get("name") or "")
        src = str(o.get("src") or "")
        if name in _GENERIC or not name:
            m = re.search(r"(?:^|/)([A-Za-z]{2})\."
                          r"(?:svg|png|jpg|jpeg|webp)(?:[?#]|$)", src)
            if m:
                need[i] = m.group(1).lower()
    if not need:
        return opts, ""
    try:
        code_map = page.evaluate("""/* worldle-country-code-map */
          () => fetch('https://cdn-assets.teuteuf.fr/data/common/countries.json')
            .then(r => r.ok ? r.json() : null)
            .then(list => {
              if (!Array.isArray(list)) return {};
              const out = {};
              for (const c of list) {
                if (c && c.code && c.name)
                  out[String(c.code).toLowerCase()] = c.name;
              }
              return out;
            }).catch(() => ({}))""") or {}
    except Exception:
        return opts, ("src resolution unavailable (CDN code-map "
                      "fetch failed)")
    if not isinstance(code_map, dict) or not code_map:
        return opts, ("src resolution unavailable (CDN code-map "
                      "empty)")
    resolved, unmapped = [], []
    for i, code in need.items():
        name = code_map.get(code)
        if name:
            old = opts[i].get("name") or ""
            opts[i]["name"] = name
            opts[i]["name_via_src"] = True
            resolved.append(f"{old!r} ({code}.svg) -> {name!r}")
        else:
            unmapped.append(code)
    detail = ""
    if resolved:
        detail = "src-resolved " + "; ".join(resolved)
    if unmapped:
        detail += ("; src codes unmapped: " + ", ".join(unmapped)) \
            if detail else ("src codes unmapped: "
                             + ", ".join(unmapped))
    return opts, detail


_WORLDLE_AREA_HEADER_BAND_Y = 160
_WORLDLE_AREA_CHROME_MAX = 56


def _worldle_area_is_chrome_rect(rect):
    """True iff `rect` is a small (~40x40) control in the top header
    band — prev/next nav arrows, banner X, menu — never a comparison
    option. `rect` is a {x, y, w, h} (or {x, y, width, height}) dict
    as recorded by the comparison probe. Pure; the JS probes carry
    the same rule (isChromeCandidate), this is the Python-side
    enforcement in _s_worldle_comparison_pick."""
    try:
        if not isinstance(rect, dict):
            return False
        _y = rect.get("y")
        _w = rect.get("w", rect.get("width"))
        _h = rect.get("h", rect.get("height"))
        if _y is None or _w is None or _h is None:
            return False
        return (float(_y) < _WORLDLE_AREA_HEADER_BAND_Y
                and 0 < float(_w) <= _WORLDLE_AREA_CHROME_MAX
                and 0 < float(_h) <= _WORLDLE_AREA_CHROME_MAX)
    except Exception:
        return False


def _s_worldle_comparison_pick(options, values):
    """Pick the max-stat comparison option. `options`: [{name, ...}]
    in display order; `values`: {_s_worldle_cmp_key(name): number}.
    Returns (best_display_name, detail) or (None, reason). Pure —
    never clicks, never guesses: EVERY option must resolve to a
    number, or the pick refuses (a tie keeps the first shown, logged).
    """
    # Iter-14 P1 (ANALYSIS10 Area root cause): defense in depth
    # alongside the probe-side JS filter — drop icon/control
    # options ('Zoom out icon', 'Previous icon', 'Next icon') that
    # reach the pick: resolved name ending in 'icon', or
    # aria/title/alt containing 'icon'. Real country names never
    # match this rule.
    _real_opts = []
    # Iter-25 (ANALYSIS22 RC-new-1): drop header-band chrome
    # candidates (a 40x40 nav button at rect y=62 that the old
    # click matched as "Kuwait") BEFORE the name dedup below —
    # a chrome candidate listed first must not shadow the real
    # card with the same name.
    for o in (options or []):
        if _worldle_area_is_chrome_rect((o or {}).get("rect")):
            continue  # header/nav chrome, not a country option
        _blob = " ".join(str(o.get(f) or "") for f in
                         ("aria", "title", "alt"))
        if re.search(r"icon\s*$", str(o.get("name") or ""),
                     re.IGNORECASE) or "icon" in _blob.lower():
            continue  # icon/control option, not a country
        _real_opts.append(o)
    # (Pure function: no _dbg here — the caller logs the probe +
    # the pick result, so unit tests stay hermetic.)
    normed = []
    for o in (_real_opts or []):
        k = _s_worldle_cmp_key(o.get("name") or "")
        if k and all(k != x[0] for x in normed):
            normed.append((k, o.get("name")))
    if len(normed) < 2:
        return None, (f"need >=2 distinct options, have {len(normed)}")
    missing = [name for k, name in normed
               if values.get(k) is None]
    if missing:
        return None, f"unresolved options: {missing}"
    best = max(normed, key=lambda kv: values[kv[0]])
    mx = values[best[0]]
    tie = sum(1 for k, _ in normed if values[k] == mx) > 1
    return best[1], ("tie, first shown kept" if tie
                     else f"max={values[best[0]]}")


def _s_worldle_fetch_country_stat(page, names, stat):
    """Resolve `stat` ('size' | 'gdpPerCapita') for country `names`
    from the game's own CDN: the countries list (name -> code), then
    each per-country record — the same endpoints answers.py uses.
    Returns {_s_worldle_cmp_key(name): value} for names that resolved.
    A fetch failure returns {} (caller records partial), never a
    guessed value. The Area comparison can pair the day country with
    a NON-neighbour (frame-proven: Kuwait vs Qatar), so the day
    lookup (own + borders) cannot cover every shown name."""
    try:
        pairs = page.evaluate("""/* worldle-country-stat-fetch */
          (args) => fetch('https://cdn-assets.teuteuf.fr/data/common/countries.json')
            .then(r => r.ok ? r.json() : null)
            .then(list => {
              if (!Array.isArray(list)) return [];
              const norm = s => (s||'').toLowerCase().normalize('NFD')
                .replace(/[\\u0300-\\u036f]/g, '')
                .replace(/\\s+/g, ' ').trim();
              const want = {};
              for (const n of args.names) want[norm(n)] = n;
              const codes = {};
              for (const c of list) {
                const k = norm(c.name);
                if (k in want) codes[k] = c.code;
              }
              return Promise.all(Object.entries(codes).map(([k, code]) =>
                fetch('https://cdn-assets.teuteuf.fr/data/common/countries/' +
                  String(code).toLowerCase() + '.json')
                  .then(r => r.ok ? r.json() : null)
                  .then(rec => [want[k], rec ? rec[args.stat] : null])));
            }).catch(() => [])""",
            {"names": list(names or []), "stat": str(stat)}) or []
    except Exception:
        return {}
    out = {}
    for pair in (pairs or []):
        try:
            name, val = pair[0], pair[1]
        except Exception:
            continue
        if val is not None:
            out[_s_worldle_cmp_key(name)] = val
    return out


def _s_worldle_comparison_skipped(page):
    """Game-skipped comparison (iter-13): the game itself declines a
    comparison round when the country lacks enough neighbours, e.g.
    'The GDP comparison round has been skipped, as Kuwait does not
    have enough neighbours…'. Returns (bool, matched_sentence).
    The comparison is N/A in that case — NOT a bot failure — and the
    round counts as solved if the number is correct."""
    try:
        bt = page.evaluate("() => document.body.innerText") or ""
    except Exception:
        return False, ""
    import re as _re
    m = _re.search(r"[Tt]he [^.\n]{0,80}comparison round has been skipped[^.\n]*\.?",
                   bt)
    if m:
        return True, m.group(0).strip()
    return False, ""


def _worldle_area_visible_header(page):
    """Visible round header: the selected tab's text, else the page
    h1, else the document title. Used to re-verify the visible round
    is still Area after a comparison submit (iter-14: the view moved
    to completed-PopulationCurrency via an unlogged SPA change)."""
    try:
        return str(page.evaluate("""() => {
          const vis = e => e && e.offsetParent !== null;
          const sel = Array.from(document.querySelectorAll(
            '[aria-selected=true], [aria-current=true]')).filter(vis);
          for (const e of sel) {
            const t = (e.innerText || '').trim();
            if (t) return t;
          }
          const h = document.querySelector('h1');
          if (h && (h.innerText || '').trim())
            return h.innerText.trim();
          return (document.title || '');
        }""") or "")
    except Exception:
        return ""


def _worldle_area_log_state(page, gid, step):
    """Log game_round + visible round header after an area sub-step
    (iter-15 P1): the iter-14 artifact showed the view had moved
    off Area with NO logged navigation, so every sub-step now
    leaves this breadcrumb."""
    try:
        _mode = _s_worldle_current_mode(page)
    except Exception:
        _mode = "?"
    _dbg(gid, f"worldle area {step}: game_round={_mode!r} "
              f"visible_header={_worldle_area_visible_header(page)!r}")


def _worldle_area_still_on_area(page):
    """True unless there is POSITIVE evidence the view moved off
    Area: game_round naming another mode, or the visible header
    naming another round. Missing evidence counts as still-on-Area
    (never force a switch on an unreadable header)."""
    try:
        _mode = _s_worldle_current_mode(page)
    except Exception:
        _mode = None
    if _mode is not None and _mode != "area":
        return False
    _hdr = _worldle_area_visible_header(page).lower()
    if _hdr:
        for _m, _label in (_WORLDLE_MODE_LABELS or {}).items():
            if _m == "area":
                continue
            if str(_label).lower() and str(_label).lower() in _hdr:
                return False
            # Tab labels are often abbreviated ("1. POPULATION" for
            # the "Population & Currency" round): match distinctive
            # label words (len>=4) too, not just the full label.
            for _tok in re.split(r"[^a-z]+", str(_label).lower()):
                if len(_tok) >= 4 and _tok in _hdr:
                    return False
    return True


def _s_worldle_play_comparison(page, gid, day, mode, ds, prompt_substrs,
                               lookup, stat, label, verify_store=False):
    """Deliberate comparison branch (Area / GDP): probe the live
    comparison widget (image cards + text buttons), resolve EVERY
    shown country's stat from `lookup` (+ the game's own CDN for any
    shown name the day lookup does not cover), click the max, submit
    once. Returns (status, picked_name_or_None, detail) with status in
    {attempted, partial, not-present, skipped}. 'not-present' = the comparison
    prompt is not showing (caller falls through to the number path);
    'partial' = prompt showing but options unresolvable / unclickable
    — NOTHING was clicked in that case. 'skipped' = the GAME itself
    declined the comparison round (not enough neighbours) — the
    comparison is N/A, the caller counts the number alone. The caller
    arbitrates with the game's win check; this helper never claims a
    win itself. verify_store=True (Area, iter-15): after click +
    submit, the mode's store list must have CHANGED (append or
    in-place update) or the comparison is NOT "answered" — a
    click + Enter that records nothing returns 'partial'."""
    probe = _s_worldle_comparison_probe(page, prompt_substrs)
    # Game-skipped comparison first (iter-13): if the game declined
    # the comparison round, the probe finds no prompt and the caller
    # must NOT fall through to clicking a stale/visible option — the
    # comparison is N/A. Check again after opening the tab, because
    # the skip message may only appear once the tab loads.
    _sk, _skdet = _s_worldle_comparison_skipped(page)
    if _sk:
        _dbg(gid, f"worldle {mode} comparison game-skipped: {_skdet}")
        return ("skipped", None,
                f"game skipped the comparison: {_skdet}")
    if not probe.get("prompt_found"):
        # The comparison may live on a COMPARISON tab that needs
        # opening (GDP after the number; Area after the number).
        # A disabled tab is skipped, never forced.
        try:
            opened = page.evaluate("""() => {
              const vis = e => e && e.offsetParent !== null;
              const hit = Array.from(document.querySelectorAll(
                'button, [role=tab]')).filter(vis)
                .find(e => (e.innerText || '').toUpperCase()
                  .includes('COMPARISON'));
              if (hit && !hit.disabled &&
                  hit.getAttribute('aria-disabled') !== 'true') {
                hit.click(); return true;
              }
              return false;
            }""")
        except Exception:
            opened = False
        if opened:
            _settle(page, base=1200)
            probe = _s_worldle_comparison_probe(page, prompt_substrs)
            # The skip message may only appear once the tab loads.
            _sk, _skdet = _s_worldle_comparison_skipped(page)
            if _sk:
                _dbg(gid, f"worldle {mode} comparison game-skipped: {_skdet}")
                return ("skipped", None,
                        f"game skipped the comparison: {_skdet}")
    if not probe.get("prompt_found"):
        return ("not-present", None,
                "comparison prompt not showing (tabs tried)")
    options = probe.get("options") or []
    if len(options) < 2:
        return ("partial", None,
                f"comparison prompt showing but only {len(options)} "
                f"option(s) probed: {options}; NOT clicked")
    # Iter-23 (ANALYSIS20 §3): resolve generic option names
    # ('Image Quiz Item') via the option image src BEFORE the
    # pick — without this the pick returns None and the
    # gate/click/re-verify below NEVER EXECUTE.
    options, _src_det = _s_worldle_resolve_option_names_by_src(
        page, options)
    if _src_det:
        _dbg(gid, f"worldle {mode} comparison: src name resolution: "
                  f"{_src_det}")
    values = {}
    missing = []
    for o in options:
        k = _s_worldle_cmp_key(o.get("name") or "")
        v = (lookup or {}).get(k)
        if v is not None:
            values[k] = v
        else:
            missing.append(o.get("name") or "")
    if missing:
        _dbg(gid, f"worldle {mode} comparison: {missing} not in day "
                  "lookup; resolving via the game's own CDN")
        values.update(_s_worldle_fetch_country_stat(page, missing, stat))
    best, pick_detail = _s_worldle_comparison_pick(options, values)
    if best is None:
        # Iter-23 (ANALYSIS20 §3): the gate must be EVALUATED
        # and logged even on the unresolvable path — the iter-22
        # artifact had ZERO gate lines because the pick's early
        # return ran before the gate. No click happens here;
        # the gate outcome is evidence, not a bypass.
        _gate_note = ""
        if mode == "area":
            try:
                _vis0 = _s_worldle_visible_area_ui(page)
                _gate_note = (f" gate evaluated on unresolvable "
                              f"path: visible={_vis0.get('visible')} "
                              f"reason={_vis0.get('reason')}")
                _dbg(gid, "worldle area comparison: pick "
                          f"unresolvable ({pick_detail});"
                          f"{_gate_note}; NOT clicked")
            except Exception:
                _gate_note = " gate evaluation failed"
        return ("partial", None,
                f"comparison options unresolvable: {pick_detail}; "
                f"options={options}; values={values};{_gate_note} "
                f"NOT clicked")
    _dbg(gid, f"worldle {mode} comparison: options="
              + str([(o.get("name"), values.get(_s_worldle_cmp_key(
                  o.get("name") or ""))) for o in options])
              + f" larger={best!r} ({pick_detail})")
    if mode == "area":
        # Iter-21 (ANALYSIS18 §6.5): NEVER click a comparison
        # option on an unverified round. The probe above reads
        # body text, which can list the Area prompt + options
        # from a not-currently-visible round while the visible UI
        # is still a completed earlier round (frame gp_0342).
        # The visible-round gate is the last check before any
        # click: no verified Area UI -> partial, nothing clicked.
        # probe_ran=False means the visible probe never executed
        # (legacy fakes that do not script it — production JS
        # always returns a dict): the gate is not enforced on
        # a probe that never ran, and the comparison-probe
        # evidence below arbitrates instead. The skip is logged,
        # never silent.
        _vis = _s_worldle_visible_area_ui(page)
        if not _vis.get("visible"):
            if not _vis.get("probe_ran"):
                _dbg(gid, "worldle area comparison: visible-round "
                          "probe unavailable (no dict result); "
                          "gate not enforced — proceeding on "
                          "comparison-probe evidence")
            else:
                # Iter-22: a gate failure returns partial WITH a
                # logged reason (which candidates were rejected
                # and why), never a bare "not verified".
                _dbg(gid, "worldle area comparison: visible-round "
                          "gate FAILED — "
                          f"reason={_vis.get('reason')}")
                return ("partial", None,
                        "visible Area UI not verified "
                        f"(signals={_vis.get('signals')}; "
                        f"reason={_vis.get('reason')}); comparison "
                        f"option {best!r} NOT clicked on an unverified "
                        "round")
    # Iter-22 (ANALYSIS19 §3c): the verified-visible Area
    # container from the SAME probe that passed the gate above
    # (never a second probe — the UI could move between
    # probes). The comparison click is restricted to it; when
    # the probe never identified one (legacy fakes /
    # probe_ran=False), container is None and the click runs
    # unrestricted — but that is LOGGED, never silent, and
    # production JS always returns a container when the gate
    # passes.
    _area_container = None
    if mode == "area":
        try:
            _area_container = (_vis or {}).get("container")
        except Exception:
            _area_container = None
        if _area_container is None:
            _dbg(gid, "worldle area comparison: visible Area "
                      "container NOT identified; click runs "
                      "unrestricted (probe gave no container)")
    # Iter-15 (Area P1): the FULL store list BEFORE the pick, so
    # the "answered" claim can be VERIFIED (iter-14 assumed answered
    # from click+Enter while the store never changed — twice).
    # Full-list comparison (not just length): the game may record
    # the comparison by UPDATING an existing entry in place (number
    # + comparison merged into one guess) rather than appending.
    _store_key = (_WORLDLE_STORE_KEYS or {}).get(mode)
    _before = None
    if _store_key:
        try:
            _before = _s_worldle_day_guesses(page, _store_key, ds)
        except Exception:
            _before = None
    # Iter-25 (ANALYSIS22 RC-new-1): Area is CARDS first. The
    # frame-proven widget is two silhouette cards + label bars,
    # not buttons; the old buttons-first click matched a 40x40
    # header nav button as "Kuwait" and navigated away. For Area
    # the card is attempted first, the button only as fallback.
    # GDP keeps the proven buttons-first order (its options ARE
    # buttons).
    if mode == "area":
        ok, detail, via = _s_worldle_click_comparison_card(
            page, best, container=_area_container)
        if not ok:
            ok, detail, via = _s_worldle_click_comparison_option(
                page, best, container=_area_container)
    else:
        ok, detail, via = _s_worldle_click_comparison_option(
            page, best, container=_area_container)
    if not ok:
        ok, detail = _s_worldle_click_option_loose(
            page, best, container=_area_container)
        via = "loose" if ok else "none"
    if not ok:
        return ("partial", None,
                f"comparison option {best!r} not clickable ({detail})")
    # Iter-20 (ANALYSIS17 §2i): POST-CLICK selected-state check,
    # immediately after the click and BEFORE any submit — the
    # iter-19 artifact had no such evidence (both attempts ended
    # store-verify 0->0 with no post-click frame/state at all).
    # The continuous gameplay recording is the post-click frame;
    # this timestamped log line is its marker. BUTTON first (the
    # click above); if no selection results, try the option CARD.
    def _any_selected(s):
        # Real probe JS returns any_selected (bool) + selected
        # (list); older fakes return only selected (bool or
        # list). Prefer the explicit bool when present, else
        # the truthiness of selected.
        s = s or {}
        if "any_selected" in s:
            return bool(s.get("any_selected"))
        return bool(s.get("selected"))

    _sel = _s_worldle_comparison_selected_state(page, best)
    _dbg(gid, f"worldle {mode} comparison post-click selected-state: "
              f"target={best!r} via={via} clicked={detail!r} "
              f"selected={_any_selected(_sel)} "
              f"wanted_selected={bool((_sel or {}).get('wanted_selected'))} "
              f"selected_name={str((_sel or {}).get('selected_name') or '')!r} "
              f"selected={(_sel or {}).get('selected') or []} "
              f"options={(_sel or {}).get('options') or []}")
    if ok and via == "card" and not _any_selected(_sel):
        # Iter-25 (Area card-first): the card click produced NO
        # selection — try the button with the same name before
        # giving up (mirror of the button-first card retry below).
        _ok2, _det2, _via2 = _s_worldle_click_comparison_option(
            page, best, container=_area_container)
        _sel2 = _s_worldle_comparison_selected_state(page, best)
        _dbg(gid, f"worldle {mode} comparison post-click selected-state "
                  f"(retry): target={best!r} via={_via2} "
                  f"clicked={_det2!r} ok={bool(_ok2)} "
                  f"selected={_any_selected(_sel2)} "
                  f"wanted_selected={bool((_sel2 or {}).get('wanted_selected'))} "
                  f"selected_name={str((_sel2 or {}).get('selected_name') or '')!r} "
                  f"selected={(_sel2 or {}).get('selected') or []} "
                  f"options={(_sel2 or {}).get('options') or []}")
        if _ok2:
            via = f"card(none-selected)+{_via2}"
            _sel = _sel2
    if ok and via == "button" and not _any_selected(_sel):
        # Button click produced NO selection (the iter-19 shape:
        # frame gp_0360 shows both buttons unselected after the
        # click). Try the option CARD itself before submitting.
        _ok2, _det2, _via2 = _s_worldle_click_comparison_card(
            page, best, container=_area_container)
        _sel2 = _s_worldle_comparison_selected_state(page, best)
        _dbg(gid, f"worldle {mode} comparison post-click selected-state "
                  f"(retry): target={best!r} via={_via2} "
                  f"clicked={_det2!r} ok={bool(_ok2)} "
                  f"selected={_any_selected(_sel2)} "
                  f"wanted_selected={bool((_sel2 or {}).get('wanted_selected'))} "
                  f"selected_name={str((_sel2 or {}).get('selected_name') or '')!r} "
                  f"selected={(_sel2 or {}).get('selected') or []} "
                  f"options={(_sel2 or {}).get('options') or []}")
        if _ok2:
            via = f"button(none-selected)+{_via2}"
            _sel = _sel2
    if ok and not _any_selected(_sel):
        if mode == "area" and (_sel or {}).get("options"):
            # Iter-25: selected-state verification GATES submit
            # for Area. The probe read the widget's options and
            # NONE is selected after card + button attempts —
            # submitting now would answer whatever the view
            # happens to show (iter-24: the flipped
            # PopulationCurrency round). Pick-failed, nothing
            # submitted. When the probe itself read nothing
            # (options empty — probe never ran), fall through to
            # the old submit-anyway + store-verify arbitration.
            _dbg(gid, f"worldle {mode} comparison: no selection "
                      f"after click (via={via}); selected-state "
                      "gate blocks submit for Area (pick-failed) — "
                      "NOT submitting")
            return ("partial", None,
                    f"comparison option {best!r} clicked "
                    f"(via={via}) but no selected state on the card "
                    "after card + button attempts (pick-failed); "
                    "NOT submitted — selected-state verification "
                    "gates submit")
        # No selection after button (+card) attempts. Do NOT fail
        # here: some widgets register the pick only on submit /
        # Enter. Submit once and let the store-verify below (and
        # the win check) arbitrate — but the no-selection evidence
        # is now logged, not silent.
        _dbg(gid, f"worldle {mode} comparison: no selection after "
                  f"click (via={via}); submitting anyway — "
                  "store-verify arbitrates")
    if mode == "area" and ok:
        # Iter-22 (ANALYSIS19 §3c): re-verify the visible round
        # AFTER the click and BEFORE submit. Attempt 2 proved
        # the click itself can flip the view Area ->
        # PopulationCurrency; submitting into the flipped view
        # answers the WRONG round. If the view flipped, the
        # click did not register: DO NOT submit — return
        # partial with the reason. probe_ran=False (probe never
        # executed) does not block: the click + store-verify
        # below arbitrate, as before.
        _vis2 = _s_worldle_visible_area_ui(page)
        # Iter-25: a view-flip is pick-FAILED, never pick-ok —
        # check the visible round AND the store/header evidence
        # (_worldle_area_still_on_area: game_round or the visible
        # header naming another round). Either signal alone
        # blocks the submit.
        _still = _worldle_area_still_on_area(page)
        if (_vis2.get("probe_ran") and not _vis2.get("visible")) or not _still:
            _dbg(gid, "worldle area comparison: view FLIPPED after "
                      "click (no longer visible Area) — pick-failed, "
                      "NOT submitting into the wrong round "
                      f"(reason={_vis2.get('reason')}; "
                      f"still_on_area={_still})")
            return ("partial", None,
                    "comparison click flipped the view off Area "
                    f"(pick-failed; reason={_vis2.get('reason')}; "
                    f"still_on_area={_still}); click did NOT "
                    "register — NOT submitted into the wrong round")
    _s_worldle_submit_guarded(page, gid, mode, ds)
    _settle(page, base=2000)
    if verify_store and _store_key is not None and _before is not None:
        try:
            _after = _s_worldle_day_guesses(page, _store_key, ds)
        except Exception:
            _after = None
        _dbg(gid, f"worldle {mode} comparison store-verify: "
                  f"{_store_key} {ds} guesses {len(_before)} -> "
                  f"{len(_after) if _after is not None else '?'} "
                  f"changed={_after is not None and _after != _before} "
                  f"(picked {best!r})")
        if _after is None or _after == _before:
            # NOT "answered": the click + Enter recorded nothing.
            # Never claim the comparison is done on this evidence.
            # (A "partial" here is never upgraded by the caller's win
            # check, so this fires ONLY on a true no-change — the
            # full-list comparison above covers in-place updates.)
            return ("partial", None,
                    f"comparison option {best!r} clicked + submitted "
                    f"but the store did NOT change ({_store_key} "
                    f"{len(_before)} guesses, identical list); "
                    "'answered' NOT claimed — the comparison answer "
                    "was never recorded")
    return ("attempted", best,
            f"{label} comparison: clicked larger {best!r} ({pick_detail})")


def _s_worldle_play_area_stepper(page, gid, size):
    """Area fallback: stepper buttons + displayed value, no input.

    The area widget has NO input element (CI DOM dump: 8 elements, two
    empty buttons, no input/tabs). The widget's steppers are identified
    by the shared _AREA_STEPPER_SELECT_JS (nav arrows excluded by
    data-testid/aria-label; +/- text, increment/decrement aria-labels,
    div[role=button], icon-only controls included). Identify the +
    stepper by clicking once and watching the displayed number move,
    then drive the display to the target in batches, verifying after
    each batch. Bounded (max 600 clicks); if the display never moves,
    fail with the full probe evidence instead of clicking blindly.
    Returns (ok, detail)."""
    import re as _re
    try:
        probe = page.evaluate("""() => {
          const vis = e => e && e.offsetParent !== null;
""" + _AREA_STEPPER_SELECT_JS + """
          const steppers = st;
          const parent = steppers.length ?
            (steppers[0].closest('div, section, form') || null) : null;
          // Iter 9: log the FULL untruncated probe — the stepper
          // container's whole outerHTML + every candidate's text —
          // so the next artifact proves where the number lives. The
          // old code sliced widgetHtml to 3000 chars and each button
          // to 300, and the failure detail truncated further.
          // Guard: if the common ancestor is BODY/HTML (steppers not
          // in a tight widget), use the steppers' parent instead so
          // the probe is the widget, not the whole page.
          let widgetHtml = '';
          if (steppers.length >= 2) {
            const anc = el => { const a = []; let n = el;
              while (n) { a.push(n); n = n.parentElement; } return a; };
            const a0 = anc(steppers[0]);
            let lca = null;
            for (const n of anc(steppers[1]))
              if (a0.includes(n)) { lca = n; break; }
            if (lca && !/^(BODY|HTML)$/.test(lca.tagName))
              widgetHtml = lca.outerHTML || '';
            else if (steppers[0].parentElement)
              widgetHtml = steppers[0].parentElement.outerHTML || '';
          }
          const allCands = _areaCands.map(e => ({
            tag: e.tagName,
            testid: e.getAttribute('data-testid') || '',
            aria: e.getAttribute('aria-label') || '',
            text: (e.innerText || '').trim().slice(0, 80)}));
          return {n: steppers.length,
                  html: steppers.map(e => (e.outerHTML || '')),
                  boxes: steppers.map(e => { const r = e.getBoundingClientRect();
                    return {x: r.x, y: r.y, w: r.width, h: r.height}; }),
                  parentText: parent ? (parent.innerText || '') : '',
                  widgetHtml: widgetHtml,
                  allCands: allCands,
                  body: (document.body.innerText || '')};
        }""") or {}
    except Exception:
        return False, "area stepper probe evaluate failed"
    steppers = probe.get("boxes") or []
    if len(steppers) < 2:
        return False, ("area stepper probe: only "
                       f"{len(steppers)} stepper candidates; html="
                       f"{probe.get('html')}; allCands="
                       f"{probe.get('allCands')}; parentText="
                       f"{(probe.get('parentText') or '')!r}")

    _display_logged = []

    def _read_display():
        # Fix-loop iter 6: the old read took the FIRST stepper's parent
        # div, which contains no number — so start was None and ZERO
        # clicks ever happened. Read from the COMMON ancestor of BOTH
        # steppers (the widget container), the element between them in
        # DOM order, the DIV role=button, the game store, then the body.
        # Re-evaluated fresh on every call (the old bare fallback read
        # a STALE probe snapshot).
        try:
            texts = page.evaluate("""() => {
              const vis = e => e && e.offsetParent !== null;
""" + _AREA_STEPPER_SELECT_JS + """
              let common = '', between = '';
              if (st.length >= 2) {
                const anc = el => { const a = []; let n = el;
                  while (n) { a.push(n); n = n.parentElement; } return a; };
                const a0 = anc(st[0]);
                let lca = null;
                for (const n of anc(st[1])) {
                  if (a0.includes(n)) { lca = n; break; }
                }
                common = lca ? (lca.innerText || '') : '';
                const p = st[0].parentElement;
                if (p && p === st[1].parentElement) {
                  const kids = Array.from(p.children);
                  const i0 = kids.indexOf(st[0]), i1 = kids.indexOf(st[1]);
                  if (i0 >= 0 && i1 >= 0)
                    between = kids.slice(Math.min(i0, i1) + 1,
                      Math.max(i0, i1)).map(e => e.innerText || '')
                      .join('\\n');
                }
              }
              const rb = document.querySelector('div[role=button]');
              let store = '';
              try {
                for (let i = 0; i < localStorage.length; i++) {
                  const k = localStorage.key(i);
                  if (/size|area/i.test(k))
                    store += k + ':' + localStorage.getItem(k) + ';';
                }
              } catch (e) {}
              // Iter 7: innerText misses numbers rendered as SVG
              // <text> or in CSS-hidden nodes, and a display that
              // starts at 0 / below 100 never passed the bare >=100
              // filter. Also collect textContent variants, the
              // pure-number LEAF elements inside the widget, and
              // aria/data values on widget elements.
              // Iter 9: also read INPUT values (el.value, not the
              // value attribute — the attribute holds the initial
              // value, not the live one) for widgets whose display
              // is a number input.
              let commonText = '', betweenText = '', pure = [],
                aria = [], inputs = [];
              const lca2 = (() => {
                if (st.length < 2) return null;
                const anc = el => { const a = []; let n = el;
                  while (n) { a.push(n); n = n.parentElement; } return a; };
                const a0 = anc(st[0]);
                for (const n of anc(st[1])) if (a0.includes(n)) return n;
                return null;
              })();
              if (lca2) {
                commonText = lca2.textContent || '';
                const p = st[0].parentElement;
                if (p && p === st[1].parentElement) {
                  const kids = Array.from(p.children);
                  const i0 = kids.indexOf(st[0]), i1 = kids.indexOf(st[1]);
                  if (i0 >= 0 && i1 >= 0)
                    betweenText = kids.slice(Math.min(i0, i1) + 1,
                      Math.max(i0, i1)).map(e => e.textContent || '')
                      .join('\\n');
                }
                for (const el of lca2.querySelectorAll('*')) {
                  const t = (el.textContent || '').trim();
                  if (el.children.length === 0 &&
                      /^[\\d,]+(\\.\\d+)?\\s*(km²|km2|sq\\.?\\s*km|square km|km)?$/.test(t) &&
                      t.replace(/[^\\d]/g, '').length)
                    pure.push(t);
                  for (const a of ['aria-valuenow', 'aria-valuetext',
                                   'data-value', 'value']) {
                    const v = el.getAttribute(a);
                    if (v && /^[\\d,]+$/.test(v.trim())) aria.push(v.trim());
                  }
                  if (el.tagName === 'INPUT') {
                    const v = (el.value || '').trim();
                    if (v && /^[\\d,]+$/.test(v)) inputs.push(v);
                  }
                }
              }
              return {common: common, between: between,
                      roleBtn: rb ? (rb.innerText || rb.textContent || '') : '',
                      store: store,
                      body: (document.body.innerText || ''),
                      commonText: commonText, betweenText: betweenText,
                      pure: Array.from(new Set(pure)).join('\\n'),
                      aria: Array.from(new Set(aria)).join('\\n'),
                      inputs: Array.from(new Set(inputs)).join('\\n')};
            }""") or {}
        except Exception:
            texts = {}
        val, src = _worldle_area_parse_display(texts)
        if val is not None and not _display_logged:
            _display_logged.append(1)
            _dbg(gid, f"worldle area display read: source={src} value={val}")
        return val

    start = _read_display()
    if start is None:
        # The display may only render after the first interaction
        # (lazy widget): click each stepper once and re-read before
        # giving up. The drive logic below re-derives direction from
        # the (new) start, so the moved value is still handled.
        for _bi in (0, 1):
            try:
                _live0 = _area_stepper_boxes(page)
                _b = _live0[_bi] if len(_live0) > _bi else steppers[_bi]
                page.mouse.click(_b["x"] + _b["w"] / 2,
                                 _b["y"] + _b["h"] / 2)
                page.wait_for_timeout(600)
            except Exception:
                pass
            start = _read_display()
            if start is not None:
                _dbg(gid, "worldle area display appeared after "
                          f"probe-click {_bi}: {start}")
                break
    if start is None:
        return False, ("area stepper: no numeric display found; "
                       f"html={probe.get('html')}; allCands="
                       f"{probe.get('allCands')}; parentText="
                       f"{(probe.get('parentText') or '')!r}; widgetHtml="
                       f"{(probe.get('widgetHtml') or '')!r}; body="
                       f"{(probe.get('body') or '')[:400]!r} (body "
                       "truncated: page text, not probe evidence)")
    # Probe-click candidate 0: watch which way the display moves.
    plus_idx = None
    try:
        _live1 = _area_stepper_boxes(page)
        b0 = _live1[0] if _live1 else steppers[0]
        page.mouse.click(b0["x"] + b0["w"] / 2, b0["y"] + b0["h"] / 2)
        page.wait_for_timeout(600)
        after0 = _read_display()
        if after0 is not None and after0 > start:
            plus_idx = 0
        elif after0 is not None and after0 < start:
            plus_idx = 1
    except Exception:
        pass
    if plus_idx is None:
        # Candidate 0 did not move the display: try candidate 1.
        try:
            _live2 = _area_stepper_boxes(page)
            b1 = _live2[1] if len(_live2) > 1 else steppers[1]
            page.mouse.click(b1["x"] + b1["w"] / 2, b1["y"] + b1["h"] / 2)
            page.wait_for_timeout(600)
            after1 = _read_display()
            if after1 is not None and after1 > (start or 0):
                plus_idx = 1
            elif after1 is not None and after1 < (start or 0):
                plus_idx = 0
        except Exception:
            pass
    if plus_idx is None:
        return False, ("area stepper: neither stepper candidate moved "
                       f"the display (start={start}); html={probe.get('html')}; "
                       f"allCands={probe.get('allCands')}")
    _dbg(gid, f"worldle area stepper: start={start} target={int(size)} "
              f"plus_idx={plus_idx}")
    clicks = 0
    cur = _read_display()
    while cur is not None and cur < int(size) and clicks < 600:
        batch = 10
        # Per-batch locator re-resolve (fix-loop iter 11): never click
        # a stale cached box; scroll_into_view + fresh rect each batch.
        _live = _area_stepper_boxes(page)
        if len(_live) <= plus_idx:
            return False, ("area stepper: stepper elements vanished "
                           f"mid-drive (re-resolved {len(_live)}); "
                           f"display={cur} target={int(size)} "
                           f"clicks={clicks}")
        b = _live[plus_idx]
        for _ in range(batch):
            page.mouse.click(b["x"] + b["w"] / 2, b["y"] + b["h"] / 2)
            clicks += 1
        page.wait_for_timeout(500)
        new = _read_display()
        if new is None or new == cur:
            return False, ("area stepper stuck: display stopped moving at "
                           f"{cur} (target {int(size)}, clicks {clicks})")
        cur = new
    if cur is None:
        return False, "area stepper: display unreadable after driving"
    if cur > int(size):
        return False, (f"area stepper overshot: display {cur} > target "
                       f"{int(size)} (clicks {clicks})")
    if cur != int(size):
        return False, (f"area stepper: display {cur} != target {int(size)} "
                       f"after {clicks} clicks (cap 600)")
    return True, f"stepper display reached {cur} in {clicks} clicks"


def _s_worldle_play_area(page, gid, day):
    """Area: number = country.size EXACTLY (integer as-is) + the
    size-comparison sub-question ('Which country has a larger surface
    area?').

    Fix-loop iter 12 (ANALYSIS8 §4, frame-proven): the comparison
    widget is TWO silhouette image buttons labeled with country names
    + 'Give up' — NOT a stepper. The old code ran the stepper probe
    FIRST, whose probe-click accidentally answered the comparison,
    then reported FAILED because no stepper display moved (even
    though correct=True). Order now: (1) DELIBERATE comparison branch
    first — detect the comparison prompt + two labeled image buttons,
    look up BOTH sizes (day lookup + the game's own CDN for a
    non-neighbour pair, never a guess), click the larger, submit +
    arbitrate; (2) if the win check still fails, the number
    sub-question is still open — play it (input / stepper), then (3)
    run the comparison branch again. The stepper probe NEVER runs
    while the comparison widget is showing. Win check:
    guesses[0].correct == guesses[0].answer (caller arbitrates).

    Fix-loop iter 19 (ANALYSIS16 §6.10): the iter-18 run proved the
    iter-12 ordering did NOT hold in the number path — after the
    comparison's store-verify failed and the view re-switched to
    Area, the number path ran the stepper probe ON the comparison
    widget (its two image buttons were the "stepper candidates",
    log line 250) and reported "stepper failed". The number path
    now re-attempts the DELIBERATE comparison branch first; the
    stepper probe runs only when no comparison widget is showing.
    The win-check wording is the comparison pick, not "first
    guess correct==answer". Outcome logic unchanged: the
    comparison is still answered deliberately, the stepper still
    serves a genuine numeric widget, submit-and-arbitrate still
    arbitrates when a path cannot verify itself."""
    ds = day.get("date") or ""
    size = day.get("size")
    if size is None:
        return "skipped", [], "no size in day data"
    lookup = day.get("size_lookup") or {}

    def _comparison():
        # Iter-15: verify_store=True — the comparison is only
        # "answered" when the store records it (iter-14 assumed
        # answered from click+Enter while store changed=False twice).
        return _s_worldle_play_comparison(
            page, gid, day, "area", ds,
            ("larger surface area",), lookup, "size", "area",
            verify_store=True)

    # (1) Comparison FIRST — deliberate, never probe-click rescue.
    st, pick, det = _comparison()
    _worldle_area_log_state(page, gid, "after-comparison-1")
    if st == "attempted" and not _worldle_area_still_on_area(page):
        # Iter-15: the view moved off Area after the comparison
        # submit in iter-14 (completed-PopulationCurrency, frame
        # g_325) via an unlogged SPA change, and the number path then
        # ran on the wrong view. Re-switch to Area before the number
        # path; if the re-switch fails, fail honestly instead of
        # playing the number into another round.
        _dbg(gid, "worldle area: view moved off Area after comparison "
                  "submit; re-switching to Area before the number path")
        if not _s_worldle_switch_mode(page, gid, "area", ds):
            return "partial", [str(size), pick], \
                "comparison recorded but view moved off Area and " \
                "re-switch to Area failed; number path NOT run on " \
                "the wrong view"
        _worldle_area_log_state(page, gid, "after-reswitch-to-area")
    if st == "partial" and not _worldle_area_still_on_area(page):
        # Iter-16 (ANALYSIS12 §10): the iter-15 re-switch above only
        # ran when the comparison returned "attempted" — but the
        # iter-15 run's comparison returned "partial" (store-verify
        # failed: click + submit recorded nothing) AND the view had
        # already moved to PopulationCurrency, so this function
        # returned partial immediately and the re-switch + number
        # path NEVER ran (frame g_327: completed-PopulationCurrency;
        # 17818 never typed). A failed store-verify + a view move
        # means the comparison submit navigated away before the
        # store recorded it — the Area round itself is still open.
        # RE-SWITCH to Area, re-verify the visible round, then FALL
        # THROUGH to the number path (which ends with a second
        # comparison attempt) instead of giving up. Re-switch
        # failure still fails honestly.
        _dbg(gid, "worldle area: comparison partial (store-verify "
                  "failed) + view moved off Area; re-switching to "
                  "Area before the number path")
        if not _s_worldle_switch_mode(page, gid, "area", ds):
            return "partial", [str(size)], \
                det + "; view moved off Area and re-switch to Area " \
                "failed; number path NOT run on the wrong view"
        _worldle_area_log_state(page, gid, "after-reswitch-to-area")
        if not _worldle_area_still_on_area(page):
            return "partial", [str(size)], \
                det + "; re-switch claimed success but the visible " \
                "round is still not Area; number path NOT run"
    elif st == "partial":
        # Iter-23 (ANALYSIS20 §3): the verdict must match the
        # store — if the store ALREADY records a pick although
        # the Area click path clicked nothing (comparison
        # returned partial), say so explicitly: the pick is NOT
        # attributable to the Area path and is NOT credited as
        # an Area-path solve. (Iter-22 logged "NOT clicked"
        # while the store counted Area complete — a banner
        # probe had clicked the Kuwait card. The banner probe
        # no longer clicks game UI (Fix 3), so this branch is
        # a safety net, not the expected path.)
        try:
            _win_p = _s_worldle_win_check(page, "area", day, ds)
        except Exception:
            _win_p = (False, [], "")
        if _win_p[0]:
            det = (det + "; STORE ALREADY RECORDS A PICK "
                   f"(win_check={_win_p[2]}) although the Area "
                   "click path clicked nothing — pick NOT "
                   "attributable to the Area path; NOT credited "
                   "as an Area-path solve")
        return "partial", [str(size)], det
    picked = []
    if st == "attempted":
        picked.append(pick)
        _win_now = _s_worldle_win_check(page, "area", day, ds)
        if _win_now[0]:
            return "attempted", [str(size), pick], \
                det + f"; win_check={_win_now[2]}"
        _dbg(gid, "worldle area: comparison answered but win check not "
                  "yet passing; playing the number sub-question too")
    # (2) Number path: input/slider, else (comparison re-attempt
    # first when the comparison widget is showing — iter-19, never
    # a stepper probe on it — else stepper), else
    # submit-and-arbitrate (iter 11) when the display is unreadable.
    if not _s_worldle_pick_input(page, ["size", "area", "km", "surface"],
                                 include_range=True):
        # The widget may render late: one settle, then re-check, before
        # concluding there is no input at all.
        _settle(page, base=2500)
    if not _s_worldle_pick_input(page, ["size", "area", "km", "surface"],
                                 include_range=True):
        # No text/range input exists. Iter-19 (ANALYSIS16 §6.10):
        # the widget showing here is the COMPARISON widget (two
        # silhouette image buttons), NOT a stepper — the old code
        # ran the stepper probe on the comparison buttons, whose
        # probe-click accidentally answered the comparison, then
        # reported "stepper failed". Answer the comparison
        # DELIBERATELY via the comparison branch; the stepper probe
        # runs ONLY when no comparison widget is showing (a genuine
        # numeric stepper widget, or the game declined the
        # comparison so the number is the whole round).
        _st_c, _pick_c, _det_c = _comparison()
        if _st_c == "attempted":
            return "attempted", [str(size)] + picked + [_pick_c], \
                f"area {size} km2 + {_det_c}"
        if _st_c in ("not-present", "skipped"):
            # No comparison widget: genuine numeric widget —
            # stepper probe (DOM dump n=8, no input). Drive the
            # steppers toward the target, verifying the display.
            ok, detail = _s_worldle_play_area_stepper(page, gid, size)
            if not ok:
                # Submit-and-arbitrate fallback (fix-loop iter 11):
                # the display may be unreadable while the widget
                # still holds a value — submit once and let the
                # game's own win check arbitrate, instead of
                # failing on the unreadable display.
                _dbg(gid, f"worldle area stepper failed ({detail}); "
                          f"submit-and-arbitrate fallback")
                _s_worldle_submit_guarded(page, gid, "area", ds)
                _settle(page, base=2000)
                _worldle_area_log_state(page, gid,
                                        "after-stepper-fallback")
                _win = _s_worldle_win_check(page, "area", day, ds)
                if _win[0]:
                    return "attempted", [str(size)] + picked, \
                        f"area {size} km2 via submit-arbitrate " \
                        f"({_win[2]}; stepper path failed: {detail})"
                dump = _s_worldle_dom_dump(page, gid, "area")
                return "failed", [], \
                    f"area stepper: {detail}; submit-arbitrate " \
                    f"win_check={_win[2]}; DOM dump logged"
            _dbg(gid, f"worldle area stepper played: {detail}")
        else:
            # Comparison showing but unanswered (partial): do NOT
            # probe-click its buttons as steppers. Submit-and-
            # arbitrate once and let the win check arbitrate.
            _dbg(gid, "worldle area comparison unanswered "
                      f"({_det_c}); submit-and-arbitrate fallback "
                      "(no stepper probe on the comparison widget)")
            _s_worldle_submit_guarded(page, gid, "area", ds)
            _settle(page, base=2000)
            _worldle_area_log_state(page, gid,
                                    "after-comparison-fallback")
            _win = _s_worldle_win_check(page, "area", day, ds)
            if _win[0]:
                return "attempted", [str(size)] + picked, \
                    f"area {size} km2 via submit-arbitrate " \
                    f"({_win[2]}; comparison path: {_det_c})"
            dump = _s_worldle_dom_dump(page, gid, "area")
            return "failed", [], \
                f"area comparison unanswered ({_det_c}); " \
                f"submit-arbitrate win_check={_win[2]}; DOM dump logged"
    else:
        ok, detail = _s_worldle_set_picked_value(page, gid, str(int(size)),
                                                 "area")
        if not ok:
            # Same submit-and-arbitrate fallback for the typed/slider
            # path: a failed set does not prove the game has nothing.
            _dbg(gid, f"worldle area set failed ({detail}); "
                      f"submit-and-arbitrate fallback")
            _s_worldle_submit_guarded(page, gid, "area", ds)
            _settle(page, base=2000)
            _worldle_area_log_state(page, gid, "after-set-fallback")
            _win = _s_worldle_win_check(page, "area", day, ds)
            if _win[0]:
                return "attempted", [str(size)] + picked, \
                    f"area {size} km2 via submit-arbitrate ({_win[2]}; " \
                    f"set path failed: {detail})"
            dump = _s_worldle_dom_dump(page, gid, "area")
            return "failed", [], \
                f"area number set failed ({detail}); submit-arbitrate " \
                f"win_check={_win[2]}; DOM dump logged"
    _s_worldle_submit_guarded(page, gid, "area", ds)
    _settle(page, base=2000)
    _worldle_area_log_state(page, gid, "after-number-path")
    # (3) Comparison again after the number (the normal order when the
    # round is number-first: the comparison appears once the number is
    # answered).
    st2, pick2, det2 = _comparison()
    _worldle_area_log_state(page, gid, "after-comparison-2")
    if st2 == "partial":
        return "partial", [str(size)] + picked, det2
    if st2 == "attempted":
        return "attempted", [str(size)] + picked + [pick2], \
            f"area {size} km2 + {det2}"
    return "attempted", [str(size)] + picked, f"area {size} km2 played"


def _s_worldle_play_bonus(page, gid, day, mode):
    """Dispatch one bonus mode to its player. Returns
    (status, guesses, detail) with status in {attempted, partial,
    skipped, failed}; the caller applies the win check and finalises."""
    if mode == "neighbours":
        return _s_worldle_play_neighbours(page, gid, day)
    if mode == "capital":
        return _s_worldle_play_capital(page, gid, day)
    if mode == "orientation":
        return _s_worldle_play_orientation(page, gid, day)
    if mode in ("flag", "emblem"):
        return _s_worldle_play_flag_emblem(page, gid, day, mode)
    if mode == "trivia":
        return _s_worldle_play_trivia(page, gid, day)
    if mode == "landmark":
        return _s_worldle_play_landmark(page, gid, day)
    if mode == "spokenLanguages":
        return _s_worldle_play_language(page, gid, day)
    if mode == "gdp":
        return _s_worldle_play_gdp(page, gid, day)
    if mode == "populationCurrency":
        return _s_worldle_play_currency(page, gid, day)
    if mode == "populousCities":
        return _s_worldle_play_populous(page, gid, day)
    if mode == "area":
        return _s_worldle_play_area(page, gid, day)
    return "skipped", [], f"no player for mode {mode}"


def _s_worldle(page, ans, gid):
    """Worldle ALL daily rounds in ONE video, one chapter per played
    mode: Country first, then bonus rounds in dropdown order, then the
    Summary end card.

    Solved (overall) requires: Country won AND every bonus round the
    game offers (not removed for missing data) fully passes the game's
    own win check. A mode skipped-with-reason (input not determinable
    from static source / facts not readable / reference ambiguous) or
    left partial (e.g. Capital pinpoint not attempted) makes the run
    NOT solved — the evidence names the mode + reason + the live
    confirmation still needed — so no publishable final is assembled
    for a partial all-modes run (QC honesty: same rule as unsolved)."""
    global _WORLDLE_T0
    _WORLDLE_T0 = time.time()
    _WORLDLE_MODES.clear()
    _WORLDLE_SEGMENTS.clear()
    _PLAYED.pop(gid, None)
    _close_modals(page)
    tgt = A.target_date()
    day_string = tgt.isoformat()
    # Day data: prefer the target date; run_one may have re-keyed to the
    # served day, in which case the solver's answer (ans) names the
    # served day's country — fall back to adjacent dates matching ans.
    day = None
    try:
        cand = A.worldle_all_modes(tgt)
        if _s_worldle_norm(cand.get("name")) == _s_worldle_norm(ans):
            day = cand
        else:
            _dbg(gid, "worldle day-data name "
                      f"{cand.get('name')!r} != solver answer {ans!r}; "
                      "trying adjacent dates (served-day re-key)")
    except SystemExit as e:
        _dbg(gid, f"worldle day data unavailable for {day_string}: {e}")
    except Exception as e:
        _dbg(gid, f"worldle day data error for {day_string}: {str(e)[:120]}")
    if day is None:
        from datetime import timedelta as _td
        for _off in (1, -1, 2, -2):
            try:
                cand = A.worldle_all_modes(tgt + _td(days=_off))
            except Exception:
                continue
            if _s_worldle_norm(cand.get("name")) == _s_worldle_norm(ans):
                day = cand
                _dbg(gid, f"worldle day data matched on "
                          f"{cand.get('date')} (re-keyed run)")
                break
    if day is None:
        return False, ("worldle all-modes: no day data matching solver "
                       f"answer {ans!r} (target {day_string} + adjacents)")
    _dbg(gid, f"worldle all-modes: {day.get('name')} ({day.get('code')}) "
              f"day {day.get('date')} capital="
              f"{(day.get('capital') or {}).get('name')} "
              f"borders={[b.get('code') for b in day.get('borders') or []]} "
              f"landmark={(day.get('landmark') or {}).get('landmarkCode')}")

    # Fresh state, then a reload so the app boots clean.
    _s_worldle_clear_storage(page, gid, day_string)
    try:
        page.reload(wait_until="domcontentloaded")
    except Exception:
        pass
    try:
        page._qp_modals_closed = False
    except Exception:
        pass
    _close_modals(page, _force=True)
    _settle(page, base=1500)

    played_labels = []

    def _record(mode, status, guesses, detail, win_check):
        label = _WORLDLE_MODE_LABELS.get(mode, mode)
        comp_now = _s_worldle_completed(page, day_string)
        rec = {"mode": mode, "label": label, "status": status,
               "guesses": list(guesses or []),
               "correct": bool(win_check[0]) if win_check else False,
               "win_check": (win_check[2] if win_check else ""),
               "detail": detail,
               "completed": comp_now}
        _WORLDLE_MODES.append(rec)
        for g in (guesses or []):
            played_labels.append(f"{label}: {g}")
        _PLAYED[gid] = list(played_labels)
        _dbg(gid, f"worldle mode {mode}: status={status} "
                  f"correct={rec['correct']} guesses={rec['guesses']} "
                  f"win_check={rec['win_check']} completed={comp_now} "
                  f"detail={detail[:110]}")
        return rec

    # ---- Country first (bonus rounds are locked until it completes).
    _s_worldle_segment("country", "Country")
    ok_c, ev_c, played_c = _s_worldle_play_country(page, ans, gid, day)
    win_c = _s_worldle_win_check(page, "country", day, day_string)
    _record("country", "solved" if (ok_c and win_c[0]) else "failed",
            played_c, ev_c, win_c)
    if not (ok_c and win_c[0]):
        return False, (f"worldle all-modes: country not solved "
                       f"({ev_c}; win_check={win_c[2]})")

    # ---- Live round list: the game removes rounds with missing data.
    # Raw evidence (fix-loop iter 11): log raw / mapped / unmapped on
    # three lines so a renamed round can never be silently dropped.
    live_ev = _s_worldle_live_round_modes(page, gid)
    live = (live_ev or {}).get("mapped") if live_ev else None
    # G4 proof (iter 13): the cross-check outcome is logged on ONE
    # line here even when the reader returns None, so the log always
    # shows the cross-check ran (iter-12: zero lines, unverified).
    # Iter-17: "G4" is the fix-loop name for this live-rounds
    # cross-check gate (NOT Google Analytics — no GA check exists);
    # the line carries the G4 tag so a grep for G4 finds the
    # check result, including the None case.
    _dbg(gid, f"worldle G4 live rounds cross-check: reader returned "
              f"{'mapped ' + str(sorted(live)) if live is not None else 'None (unreadable)'}")
    avail = day.get("available") or {}
    for mode in _WORLDLE_PLAY_ORDER[1:]:
        label = _WORLDLE_MODE_LABELS.get(mode, mode)
        # ---- Offered-set rule (iter 4): attempt the dropdown switch
        # and verify via game_round == mode. A successful switch means
        # the mode is OFFERED regardless of the live-list read or
        # `available`. A failed switch + absence from the live list
        # means VERIFIED ABSENT (no game_round+reload fallback for
        # those). When the live list is unreadable (None), `available`
        # is a fallback only, and the switch is still attempted once
        # before marking removed. Summary is an end card, never a
        # puzzle, and is handled after this loop.
        switched = _s_worldle_switch_dropdown_only(page, gid, mode,
                                                   day_string)
        if not switched:
            if live is not None and mode not in live:
                _record(mode, "removed", [],
                        "absent from live round list + dropdown switch "
                        "failed (verified absent)",
                        (False, [], "round not offered by game"))
                continue
            if live is None and avail.get(mode) is not True:
                _record(mode, "removed", [],
                        "live round list unreadable; available="
                        f"{avail.get(mode)}; dropdown switch failed "
                        "(switch attempted once before marking removed)",
                        (False, [], "available-fallback removed"))
                continue
            # Live list contains the mode, or live unreadable +
            # available: one full switch attempt (dropdown retry +
            # game_round+reload fallback).
            if not _s_worldle_switch_mode(page, gid, mode, day_string):
                _record(mode, "skipped", [],
                        "mode switch failed (dropdown + game_round "
                        "fallback)",
                        (False, [], "switch failed"))
                continue
        _s_worldle_segment(mode, label)
        _close_modals(page, _force=True)
        status, guesses, detail = _s_worldle_play_bonus(page, gid, day, mode)
        # Populous win check needs the N parsed from the live prompt;
        # re-parse here from the same prompt text the player read.
        extra = {}
        if mode == "populousCities":
            try:
                _bt = (page.evaluate(
                    "() => document.body.innerText") or "")
                import re as _re2
                _m2 = _re2.search(r"(\d+)\s+most populated", _bt)
                extra["expected_n"] = int(_m2.group(1)) if _m2 else None
            except Exception:
                extra["expected_n"] = None
        win = _s_worldle_win_check(page, mode, day, day_string,
                                   extra=extra)
        if not win[0] and mode in ("trivia", "populationCurrency"):
            # completed can lag the last guess: one settle + re-check.
            _settle(page, base=1500)
            win = _s_worldle_win_check(page, mode, day, day_string,
                                       extra=extra)
        if status == "attempted":
            final = "solved" if win[0] else "failed"
        elif status == "partial":
            # Partial = some sub-question correct, rest skipped with
            # reason (e.g. Capital pinpoint). Win check cannot pass
            # without the missing part, so never upgrade to solved.
            final = "partial"
        elif status == "skipped":
            final = "skipped"
        else:  # failed
            final = "failed"
        if final in ("skipped", "partial", "failed"):
            # Every skipped/partial/failed record carries mode + DOM
            # evidence dump + derived answer + win-check result.
            dump = _s_worldle_dom_dump(page, gid, mode)
            detail = (detail + " | derived="
                      + _s_worldle_derived_answer(day, mode)
                      + " | win_check=" + str(win[2])
                      + " | DOM dump: " + dump[:2000])
        _record(mode, final, guesses, detail, win)

    # Iter-20 (ANALYSIS17 §2h): G4 SUMMARY-TIME re-read. The
    # reader ran once during the Country round and returned None;
    # the dropdown may be open (or openable) by the end of the
    # bonus rounds, and the reader is scan-first (no round switch
    # risk) + ends with Escape, so a second read here is cleanly
    # separable from the main flow. It runs ONLY when the first
    # read failed (live is None) — a successful first read is
    # never re-run. A successful re-read replaces live/live_ev
    # so the classification cross-check below uses it.
    if live is None:
        _live_retry = _s_worldle_live_round_modes(page, gid)
        _dbg(gid, "worldle G4 live rounds re-read before summary: "
                  f"reader returned "
                  f"{'mapped ' + str(sorted((_live_retry or {}).get('mapped') or [])) if _live_retry else 'None (unreadable)'}")
        if _live_retry is not None:
            live_ev = _live_retry
            live = (live_ev or {}).get("mapped")
            _unmapped = (live_ev or {}).get("unmapped") or []

    # ---- Summary end card (not a puzzle). No chapter segment is
    # recorded for it here: _s_worldle_final_chapters appends the
    # Summary chapter once, at gameplay end.
    if _s_worldle_switch_mode(page, gid, "summary", day_string):
        _record("summary", "end-card", [],
                "summary end card (not a puzzle)", (True, [], "n/a"))
    else:
        _record("summary", "skipped", [],
                "summary navigation failed (end card only)",
                (False, [], "summary switch failed"))

    solved_modes = [r for r in _WORLDLE_MODES
                    if r["mode"] != "summary" and r["status"] == "solved"]
    offered = [r for r in _WORLDLE_MODES
               if r["mode"] != "summary"
               and r["status"] != "removed"]
    blocked = [r for r in _WORLDLE_MODES
               if r["mode"] != "summary"
               and r["status"] in ("partial", "skipped", "failed")]
    n_offered = len(offered)
    # ---- Classification cross-check (fix-loop iter 11): derive from
    # the LIVE list + switch verification every run, never hardcode.
    # offered + verified-removed must equal the user-stated TOTAL 13,
    # and unmapped must hold no non-summary entry. Either failure =>
    # classification BLOCKED with the raw list in evidence, solved=False
    # (honest failure, not a silently dropped round).
    n_removed = len([r for r in _WORLDLE_MODES
                     if r["mode"] != "summary"
                     and r["status"] == "removed"])
    _unmapped = (live_ev or {}).get("unmapped") or []
    _xcheck_fail = None
    if live_ev is not None:
        if n_offered + n_removed != 13:
            _xcheck_fail = (f"offered({n_offered}) + removed({n_removed}) "
                            f"!= 13 total")
        # Fix-loop iter 12: BLOCK only on IN-SCOPE unmapped entries
        # (scope == "dropdown"). The reader returns None when no open
        # dropdown container is identifiable, so an out-of-scope read
        # can never block the run (iter-11 false positive).
        elif live_ev.get("scope") == "dropdown" and _unmapped:
            _xcheck_fail = f"unmapped live entries: {_unmapped}"
    if _xcheck_fail:
        _blocked_ev = (f"worldle classification BLOCKED: {_xcheck_fail}; "
                       f"live raw={live_ev.get('raw')}")
        _dbg(gid, _blocked_ev)
        return False, _blocked_ev
    evidence = (f"worldle all-modes: {day.get('name')} ({day.get('code')}); "
                f"{len(solved_modes)}/{n_offered} offered modes solved; "
                f"chapters={_s_worldle_chapter_plan()}")
    if blocked:
        evidence += "; blocked: " + "; ".join(
            f"{r['mode']}={r['status']} ({r['detail'][:80]})"
            for r in blocked)
    solved = (len(offered) == len(solved_modes)) and bool(offered)
    if not solved:
        return False, evidence
    return True, evidence


def _s_globle(page, ans, gid):
    """Globle: plain form (type + Enter), canvas board."""
    _close_modals(page)
    try:
        page.wait_for_selector("input[name=guess]", timeout=30000)
    except Exception:
        _dbg(gid, "guesser input never mounted")
        if _LIVE_DAY.get("globle_404"):
            return False, ("defer: board-unpublished (globle answer endpoint "
                           "404: server requires day == real-today-in-"
                           "timeZone, so the page's (day, timeZone) was "
                           "rejected and the guesser never mounted)")
    seq = _guesses(gid, ans)
    a = str(ans or "").strip()
    _played_gl = []
    for n, g in enumerate(seq):
        _dbg(gid, f"guess {n + 1}/{len(seq)} {g!r}")
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
        _played_gl.append(g)
        _PLAYED[gid] = list(_played_gl)
        try:
            body = (page.evaluate("() => document.body.innerText") or "")
        except Exception:
            body = ""
        low = body.lower()
        _dbg(gid, f"board after guess {n + 1}: "
                  f"{body[-140:].replace(chr(10), '|')[:120]!r}")
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


# Countryle onboarding buttons, PLAY/START-first: the old scan returned the
# FIRST matching button in DOM order (NEXT before PLAY), so PLAY was starved
# and the deck burned 5 no-op NEXT taps. PLAY/START/CONTINUE win whenever
# present; NEXT/SKIP only when no PLAY-style button exists.
_COUNTRYLE_BTN_JS = """() => {
  const vis = e => e && e.offsetParent !== null;
  const pick = (re) => {
    for (const b of Array.from(
        document.querySelectorAll('button, a')).filter(vis)) {
      const t = (b.innerText || '').trim().toUpperCase();
      // Iter-16: strip trailing "!" before matching — the site's
      // play button text is "PLAY!" (en.json playButton), so a bare
      // ^PLAY$ test never matched it. The returned t keeps the
      // original text ("PLAY!"); the click helper normalises too.
      if (re.test(t.replace(/!+$/, ''))) {
        const r = b.getBoundingClientRect();
        if (r.width > 4 && r.height > 4)
          return {t: t, x: r.x + r.width / 2, y: r.y + r.height / 2};
      }
    }
    return null;
  };
  // Iter-16: the site's play button text is "PLAY!" (en.json
  // playButton), NOT "PLAY" — the old ^PLAY$ never matched it, so the
  // tap loop broke silently on the last slide and only the PLAY loop's
  // substring :has-text('Play') + coordinate fallback ever tapped it.
  return pick(/^(PLAY|START|CONTINUE|GO|GOT IT)$/) || pick(/^(NEXT|SKIP)$/);
}"""
# Slide fingerprint: SLIDE-SENSITIVE state, not body text.
# ROOT CAUSE (iter-16, ANALYSIS12 §10 carry-forward, proven from the
# saved frontend source): the welcome deck is an Owl Carousel
# (ngx-owl-carousel-o) whose stage renders ALL 6 slides at once
# (owl-stage template: ngFor over slidesData; inactive .owl-item divs
# stay in the DOM, translated off-stage, never display:none). So
# document.body.innerText contains every slide's text on EVERY slide —
# the old full-body fingerprint was slide-INVARIANT: a NEXT tap that
# DID advance the slide still read "unchanged" (652 chars, identical),
# the bot misdiagnosed working taps as stuck, fired escalation taps
# (which advanced MORE slides — that is why the deck "eventually
# recovered": tap + escalation + tap + escalation + tap = 5 advances,
# landing exactly on slide 6, where NEXT unmounts and PLAY! mounts and
# the fingerprint finally changes). The fix reads what actually
# changes per slide: the active .owl-item index, the stage transform,
# and the mounted footer buttons (NEXT/SKIP vs PLAY!).
_COUNTRYLE_SLIDE_FP_JS = """() => {
  const items = Array.from(document.querySelectorAll('.owl-item'));
  const active = items.findIndex(
      el => el.classList && el.classList.contains('active'));
  const stage = document.querySelector('.owl-stage');
  const btns = Array.from(
      document.querySelectorAll('button, a, div.skip-text'))
      .map(e => (e.innerText || '').trim()).filter(Boolean).join('|');
  return location.href + '|active=' + active + '/' + items.length +
      '|transform=' + (stage ? (stage.style.transform || '') : 'no-stage') +
      '|btns=' + btns;
}"""
# Click the button whose text EXACTLY equals `want`, via a real JS click
# (escalation when a coordinate/element tap did not advance the slide).
# Iter-16: two proven misses fixed — (a) SKIP is a <div
# class="skip-text"> (welcome template Qu1), NOT a button/a, so the old
# button/a-only search NEVER clicked it and "SKIP also did not advance"
# was logged for a click that never fired; (b) the PLAY button text is
# "PLAY!" (en.json playButton), so an exact "PLAY" comparison never
# matched — trailing "!" is stripped on both sides before comparing.
_COUNTRYLE_BTN_JS_CLICK_JS = """(want) => {
  const vis = e => e && e.offsetParent !== null;
  const norm = t => (t || '').trim().toUpperCase().replace(/!+$/, '');
  const w = norm(want);
  for (const b of Array.from(
      document.querySelectorAll('button, a, div.skip-text')).filter(vis)) {
    if (norm(b.innerText) === w) {
      b.click();
      return true;
    }
  }
  return false;
}"""


def _countryle_frame(page, max_rounds=6, wait_ms=500):
    """The playable app lives in a full-page iframe (rm-app-frame ->
    /index.html); the outer document is an ad shell with an empty body - which
    is why every mount check (all versions) reported 'UI still empty'.

    CAPPED + NON-BLOCKING (fix-loop iter 3): at most max_rounds quick
    scans (~3 s total), each probe a single immediate evaluate
    (query_selector would block up to its timeout per frame). The old
    version looped 40x3 s (~120 s) and callers then polled a STALE handle
    for another 131 s. Callers re-acquire on EVERY wait iteration instead.
    """
    welcome = None
    for _ in range(max_rounds):
        for fr in page.frames:
            try:
                u = (fr.url or "")
                if u == page.url or not u.startswith("http"):
                    continue
                if fr.evaluate("() => !!document.querySelector('input, button')"):
                    return fr
                if ("welcome" in u or "index.html" in u) and welcome is None:
                    welcome = fr
            except Exception:
                continue
        if welcome is not None and _ > 2:
            return welcome
        page.wait_for_timeout(wait_ms)
    return welcome


def _countryle_live_frame(page, fr):
    """Return a live frame handle: `fr` if still attached, else a fresh
    re-acquire. The app iframe navigates (/welcome -> /index.html)
    mid-onboarding, which detaches the old handle — every poll on it
    then misses forever (the 131 s stale-frame wait)."""
    try:
        if fr is not None and fr.evaluate("() => true"):
            return fr
    except Exception:
        pass
    try:
        fresh = _countryle_frame(page)
    except Exception:
        fresh = None
    return fresh if fresh is not None else fr


def _countryle_wins_counter(body):
    """Wins counter on the /statistics page ("1 Wins" / "Wins 1").

    Returns the int count, or None when no Wins counter is readable.
    Never raises.
    """
    import re as _re
    try:
        m = _re.search(r"(\d+)\s+Wins\b", str(body or ""), _re.IGNORECASE)
        if m:
            return int(m.group(1))
        m = _re.search(r"\bWins\b\s*[:\n]?\s*(\d+)", str(body or ""),
                       _re.IGNORECASE)
        return int(m.group(1)) if m else None
    except Exception:
        return None


def _countryle_evaluate_win(body, picked, seq, url=""):
    """Decide the Countryle win from one settled body read.

    Returns (won, evidence). Win signals, in order: the statistics-page
    banner "You found the COUNTRYLE"; the answer + a win keyword; the
    Wins counter (>=1) on the /statistics page after a full guess
    sequence was submitted. The li/row fallback applies ONLY on the
    game-board frame — on /statistics the banner/counter is the
    signal, and a row count of 0 there must never discard a win
    (rerun #5: the stats page uses divs, so rows read 0/4 and a real
    TIMOR-LESTE win was discarded).
    """
    low = (body or "").lower()
    on_stats = ("statistics" in str(url or "").lower()
                or "statistics" in low)
    if "you found the" in low and "countryle" in low:
        return True, f"win banner You found the COUNTRYLE (picked {picked!r})"
    if picked and str(picked).lower() in low and any(
            k in low for k in ("guessed", "correct", "well done",
                               "statistics", "share", "play again",
                               "tomorrow", "result")):
        return True, f"answer row rendered (picked {picked!r})"
    wins = _countryle_wins_counter(body)
    if on_stats and wins is not None and wins >= 1:
        return True, (f"statistics page Wins={wins} after "
                      f"{len(seq)} submitted guesses (picked {picked!r})")
    if on_stats:
        return False, (f"statistics page without banner/counter "
                       f"(Wins={wins}; picked {picked!r}) — rows fallback "
                       f"not applied on the statistics page")
    return False, f"no win signal in board body (picked {picked!r})"


def _s_countryle(page, ans, gid):
    """Countryle: pass the /welcome gate INSIDE the app iframe, then guess."""
    import time as _time_c
    _close_modals(page)
    # The outer ad shell keeps its #loading-mask ON TOP of the app iframe, so
    # every click lands on the mask (Playwright: 'element click intercepted').
    # Remove it and let the iframe take pointer events.
    try:
        page.evaluate("""() => {
          for (const sel of ['#loading-mask', '.mask']) {
            const m = document.querySelector(sel);
            if (m) m.remove();
          }
          const f = document.querySelector('#rm-app-frame');
          if (f) { f.style.zIndex = '999'; f.style.position = 'absolute'; }
          document.body.style.pointerEvents = 'auto';
        }""")
    except Exception as e:
        _dbg(gid, f"mask eviction failed: {str(e)[:80]}")
    # Boot wait = repeated cheap re-acquires (each ~3 s max), NOT one
    # 120 s blocking scan. The frame handle is fresh on every attempt.
    fr = None
    _t_boot0 = _time_c.time()
    for _boot in range(12):
        fr = _countryle_frame(page)
        if fr is not None:
            break
        page.wait_for_timeout(2000)
    if fr is None:
        _dbg(gid, "app iframe never produced UI (bot-gate?)")
        return False, "app iframe empty after 60 s"
    _dbg(gid, f"app frame acquired after {_time_c.time() - _t_boot0:.1f}s: "
              f"{fr.url[:60]}")
    # The pageos shell keeps a full-screen #loading-mask spinner OVER the app
    # iframe until ad boot completes; on CI it never completes, so the mask
    # intercepts every click (element AND coordinate) on the onboarding deck.
    try:
        _n_mask = page.evaluate(
            "() => { let n = 0; for (const m of document.querySelectorAll("
            "'#loading-mask, .mask')) { m.remove(); n++; } return n; }")
        _dbg(gid, f"loading-mask removed from shell: {_n_mask}")
    except Exception as _me:
        _dbg(gid, f"mask removal failed: {str(_me)[:80]}")
    try:
        b0 = (fr.evaluate("() => document.body.innerText") or "")[:400]
    except Exception:
        b0 = ""
    # (Was conditional on the welcome text — when the condition was False,
    # _last_tap was never defined and the tap loop raised NameError.)
    _last_tap = None
    page._qp_unch = 0
    for _tap in range(16):
            # Re-acquire EVERY iteration: the iframe navigates mid-deck and
            # the old handle goes stale (every poll then misses).
            _fr_new = _countryle_live_frame(page, fr)
            if _fr_new is not fr:
                _dbg(gid, "onboarding: frame re-acquired "
                          f"(was {(fr.url if fr else '')[:40]})")
                fr = _fr_new
            box = None
            try:
                # PLAY/START-first scan (see _COUNTRYLE_BTN_JS): tap PLAY as
                # soon as it exists instead of starving it behind NEXT.
                box = fr.evaluate(_COUNTRYLE_BTN_JS)
            except Exception:
                box = None
            if not box:
                break
            # A leftover loading mask (ad shell) absorbs every click when the
            # blocked ad scripts never fire their callback: strip it first.
            try:
                fr.evaluate("""() => {
                    for (const m of document.querySelectorAll(
                            '#loading-mask, .mask, [class*=loading i]'))
                        m.remove();
                }""")
            except Exception:
                pass
            try:
                _before = (fr.evaluate(_COUNTRYLE_SLIDE_FP_JS) or "")
            except Exception:
                _before = ""
            # (Removed: the old "rotate the label" step re-scanned for a
            # different button when the same label repeated. When NEXT was
            # the deck's only button the re-scan returned None and the loop
            # tapped NEXT again anyway — the 5x "slide unchanged" repeat.
            # A genuinely-stuck tap now escalates below, then breaks out.)
            # An invisible overlay (re-rendered ad/mask layer) was absorbing
            # every tap: probe the point and evict whatever is not the button.
            try:
                _int = fr.evaluate("""(pt) => {
                    const el = document.elementFromPoint(pt.x, pt.y);
                    if (!el) return null;
                    if (el.closest('button, a')) return null;
                    const desc = el.tagName + '#' + (el.id || '') + '.' +
                        ((el.className || '').toString().slice(0, 60));
                    el.remove();
                    return desc;
                }""", box)
                if _int:
                    _dbg(gid, f"evicted interceptor {_int!r} over {box['t']}")
            except Exception:
                pass
            # Delivery ladder, JS click FIRST: it compares the exact
            # uppercased label, so it cannot miss on case. The old order
            # (element click first) used box['t'].title() in a :text-is
            # selector — 'NEXT'.title() is 'Next', which :text-is never
            # matches against an all-caps button, so the element path
            # silently never fired and every tap fell through to raw
            # viewport coords (which miss when a shell overlay absorbs
            # the click). Coords remain the last resort, not the default.
            _via = None
            try:
                if fr.evaluate(_COUNTRYLE_BTN_JS_CLICK_JS, box["t"]):
                    _via = "js-click"
            except Exception:
                pass
            if _via is None:
                try:
                    _el = fr.query_selector(
                        f"button:text-is('{box['t']}'), "
                        f"a:text-is('{box['t']}')")
                    if _el:
                        _el.click(timeout=2500)
                        _via = "element"
                except Exception as _ce:
                    _dbg(gid, f"element click intercepted: {str(_ce)[:110]}")
            if _via is None:
                page.mouse.click(box["x"], box["y"])  # iframe full-page: 0,0
                _via = "coords"
            _push_click_at(box["x"], box["y"])
            _dbg(gid, f"onboarding trusted tap {box['t']!r} via={_via}")
            page.wait_for_timeout(1400)
            try:
                if fr.query_selector("input"):
                    _dbg(gid, "input visible, onboarding done")
                    break
                _after = (fr.evaluate(_COUNTRYLE_SLIDE_FP_JS) or "")
                if _after == _before:
                    _u = getattr(page, "_qp_unch", 0) + 1
                    page._qp_unch = _u
                    _last_tap = box["t"]
                    _dbg(gid, f"slide unchanged after trusted tap ({_u}x, "
                              f"last={_last_tap})")
                    # Fix-loop iter 6: the ladder still fails to advance
                    # slide 1 on first attempts for an UNPROVEN reason —
                    # log the slide fingerprints + elementFromPoint at
                    # the tap point so the next artifact shows whether
                    # taps are absorbed (overlay) or the fingerprint
                    # lags the real slide.
                    try:
                        _hit = fr.evaluate("""(pt) => {
                          const el = document.elementFromPoint(pt.x, pt.y);
                          if (!el) return 'elementFromPoint: null';
                          return 'elementFromPoint: ' + el.tagName + '#'
                            + (el.id || '') + '.'
                            + ((el.className || '').toString().slice(0, 60))
                            + ' text=' + JSON.stringify(
                              (el.innerText || '').trim().slice(0, 40));
                        }""", {"x": box["x"], "y": box["y"]})
                    except Exception as _he:
                        _hit = f"elementFromPoint probe failed: {str(_he)[:60]}"
                    _dbg(gid, f"stuck-tap probe: fp_before_len={len(_before)} "
                              f"fp_after_len={len(_after)} "
                              f"fp_before_head={_before[:120]!r} "
                              f"fp_after_head={_after[:120]!r} {_hit}")
                    # Escalate ONCE instead of re-tapping the same button:
                    # a tap that did not advance the slide is being absorbed
                    # (overlay) or the button needs a real JS click /
                    # keyboard activation. If the escalation also leaves the
                    # slide unchanged, stop tapping this deck — the explicit
                    # PLAY loop below handles the final slide — instead of
                    # burning 5 identical taps.
                    try:
                        _escalated = fr.evaluate(
                            _COUNTRYLE_BTN_JS_CLICK_JS, box["t"])
                    except Exception:
                        _escalated = False
                    try:
                        for _k in ("Enter", "Space", "ArrowRight"):
                            page.keyboard.press(_k)
                            page.wait_for_timeout(600)
                    except Exception:
                        pass
                    page.wait_for_timeout(800)
                    try:
                        _after2 = (fr.evaluate(_COUNTRYLE_SLIDE_FP_JS) or "")
                    except Exception:
                        _after2 = _after
                    if _after2 == _before:
                        _dbg(gid, "onboarding: slide still unchanged after "
                                  f"escalation (JS click={_escalated})")
                        if box["t"] == "NEXT":
                            # SKIP jumps straight to the game: when NEXT
                            # taps are being absorbed, SKIP is the
                            # progression that still exists on this slide.
                            try:
                                _sk = fr.evaluate(
                                    _COUNTRYLE_BTN_JS_CLICK_JS, "SKIP")
                            except Exception:
                                _sk = False
                            page.wait_for_timeout(1200)
                            try:
                                _after3 = (fr.evaluate(
                                    _COUNTRYLE_SLIDE_FP_JS) or "")
                            except Exception:
                                _after3 = _after2
                            if _sk and (_after3 != _before
                                        or fr.query_selector("input")):
                                _dbg(gid, "onboarding: SKIP advanced "
                                          "past the stuck NEXT slide")
                                page._qp_unch = 0
                                page._qp_stuck = 0
                                _last_tap = None
                                continue
                            _dbg(gid, "onboarding: SKIP also did not "
                                      "advance the slide")
                        # NEVER break to the PLAY-only wait from a slide
                        # with no PLAY button (iter4 regression): the deck
                        # can only advance via NEXT/SKIP, so keep
                        # re-scanning until the tap cap; the PLAY loop
                        # below runs when the deck actually reaches a PLAY
                        # slide or the cap is hit.
                        _stuck = getattr(page, "_qp_stuck", 0) + 1
                        page._qp_stuck = _stuck
                        if _stuck >= 3:
                            _dbg(gid, "onboarding: same slide stuck "
                                      f"({_stuck}x full ladder); giving up "
                                      "deck taps, PLAY/input wait follows")
                            break
                        continue
                    _dbg(gid, "onboarding: escalation advanced the slide")
                    page._qp_unch = 0
                    page._qp_stuck = 0
                    _last_tap = None
                else:
                    page._qp_unch = 0
                    page._qp_stuck = 0
                    _last_tap = None
            except Exception:
                pass
    # final explicit PLAY/START push: the deck's last slide button is the
    # one that actually mounts the game (runs ended one click short of it)
    # Iteration 2: 4 -> 2 taps. Rerun #1 spent ~146s in the PLAY/input-wait
    # phase with no log lines; fewer taps + timing logs below expose where
    # the wait actually goes instead of burning recording time.
    import time as _time2
    _t_play0 = _time2.time()
    for _play in range(2):
        try:
            fr = _countryle_live_frame(page, fr)
            if fr.query_selector("input"):
                _dbg(gid, f"PLAY loop: input already visible after "
                          f"{_time2.time() - _t_play0:.1f}s")
                break
            pb = fr.query_selector("button:has-text('Play')") or \
                fr.query_selector("button:has-text('PLAY')") or \
                fr.query_selector("button:has-text('Start')")
            if not pb:
                # The old code broke here SILENTLY: no log line, so the
                # PLAY loop looked like it ran when it never tapped.
                _dbg(gid, "PLAY loop: no PLAY/START button on this slide")
                break
            _played = False
            try:
                for _lbl in ("PLAY", "START", "CONTINUE", "GO"):
                    if fr.evaluate(_COUNTRYLE_BTN_JS_CLICK_JS, _lbl):
                        _played = True
                        break
            except Exception:
                pass
            if _played:
                _dbg(gid, "explicit PLAY tap via JS click")
            else:
                try:
                    bb = pb.bounding_box()
                    if bb:
                        page.mouse.click(bb["x"] + bb["width"] / 2,
                                         bb["y"] + bb["height"] / 2)
                        _push_click_at(bb["x"] + bb["width"] / 2,
                                       bb["y"] + bb["height"] / 2)
                        _dbg(gid, "explicit PLAY tap")
                except Exception:
                    pb.click(timeout=2500)
            page.wait_for_timeout(2500)
        except Exception:
            break
    seq = _guesses(gid, ans)
    a = str(ans or "").strip()
    picked = "none"
    _fin = None
    _t_wait0 = _time2.time()
    for _w in range(10):
        # Re-acquire EVERY wait iteration: the old code polled a handle
        # captured before onboarding, which went stale on navigation and
        # missed the input for 131 s while it was on screen.
        _fr_w = _countryle_live_frame(page, fr)
        if _fr_w is not fr:
            _dbg(gid, f"input-wait iter {_w}: frame re-acquired")
            fr = _fr_w
        try:
            _fin = fr.query_selector("input")
        except Exception:
            _fin = None
        if _fin:
            _dbg(gid, f"input appeared after "
                      f"{_time2.time() - _t_wait0:.1f}s (wait iter {_w})")
            break
        if _w == 5:     # keyboard fallback: some decks advance on Enter/Space
            for _k in ("Enter", "Space", "ArrowRight"):
                try:
                    page.keyboard.press(_k)
                    page.wait_for_timeout(700)
                except Exception:
                    pass
        if _w == 8:   # last resort: jump the frame straight to the app route
            try:
                fr.evaluate("""() => {
                    if (!document.querySelector('input'))
                        location.assign('/index.html');
                }""")
            except Exception:
                pass
            fr = _countryle_frame(page) or fr
        page.wait_for_timeout(1000)
    if not _fin:
        _dbg(gid, f"input wait exhausted after "
                  f"{_time2.time() - _t_wait0:.1f}s")
        return False, "no input inside app iframe"
    for n, g in enumerate(seq):
        last = (n == len(seq) - 1)
        _idle_drift(page)
        fr = _countryle_live_frame(page, fr)
        ok, detail = _type_country_guess(page, fr, g)
        _dbg(gid, f"guess {n + 1}/{len(seq)} {g!r}: ok={ok} ({detail[:80]})")
        if not ok and "no text input" in str(detail):
            # The ad shell refreshes the game iframe mid-play: state resets
            # to the /welcome deck. Re-acquire, tap through the deck again,
            # then replay this guess on the fresh board.
            _dbg(gid, "input vanished; re-acquiring app frame")
            fr2 = _countryle_frame(page)
            if fr2 is not None:
                fr = fr2
                if "welcome" in (fr.url or ""):
                    _dbg(gid, "deck re-appeared after iframe refresh; re-tapping")
                    for _tap2 in range(10):
                        try:
                            # PLAY-first scan (see _COUNTRYLE_BTN_JS).
                            _b2 = fr.evaluate(_COUNTRYLE_BTN_JS)
                        except Exception:
                            _b2 = None
                        if not _b2:
                            break
                        page.mouse.click(_b2["x"], _b2["y"])
                        _push_click_at(_b2["x"], _b2["y"])
                        page.wait_for_timeout(1200)
                        try:
                            if fr.query_selector("input"):
                                break
                        except Exception:
                            pass
                ok, detail = _type_country_guess(page, fr, g)
                _dbg(gid, f"retry guess {n + 1}: ok={ok} ({detail[:80]})")
        if not ok:
            if last:
                return False, f"guess {n + 1} pick failed ({detail[:120]})"
            continue
        picked = g
        _sub = "none"
        try:
            btn = fr.query_selector("button:has-text('Guess')") or \
                fr.query_selector("button:has-text('GUESS')")
            if btn and btn.is_visible():
                btn.click(timeout=3000)
                _sub = "btn-click"
        except Exception:
            btn = None
        if _sub == "none":
            try:
                _fin2 = fr.query_selector("input")
                if _fin2:
                    _fin2.focus()
                    page.keyboard.press("Enter")
                    _sub = "input-enter"
            except Exception:
                pass
        if _sub == "none":
            page.keyboard.press("Enter")
            _sub = "page-enter"
        _settle(page, base=2000)
        # Countryle's real submit control is the round arrow button beside the
        # input (no DOM submit button with text). If no row landed, click it
        # by geometry.
        try:
            _n0 = fr.evaluate(
                "() => document.querySelectorAll('li, [class*=row i]').length")
            if _n0 == 0:
                _sb = fr.evaluate("""() => {
                  const inp = document.querySelector('input');
                  if (!inp) return null;
                  const wrap = inp.closest('form') ||
                      (inp.parentElement && inp.parentElement.parentElement);
                  if (!wrap) return null;
                  const btns = Array.from(wrap.querySelectorAll('button'))
                    .filter(b => b.offsetParent !== null);
                  const b = btns[btns.length - 1];
                  if (!b) return null;
                  const r = b.getBoundingClientRect();
                  return {x: r.x + r.width / 2, y: r.y + r.height / 2};
                }""")
                if _sb:
                    page.mouse.click(_sb["x"], _sb["y"])
                    QP.ev_push(_sb["x"], _sb["y"], "click")
                    _sub += "+round-btn"
                    _settle(page, base=2000)
        except Exception:
            pass
        _settle(page, base=1000)
        try:
            _rows = fr.evaluate(
                "() => document.querySelectorAll("
                "'[class*=guess i] li, [class*=row i], li').length")
            _ival = (fr.query_selector("input").input_value()
                     if fr.query_selector("input") else "?")
            _dbg(gid, f"submit via {_sub}; rows={_rows} input_now={_ival!r}")
        except Exception:
            pass
    page.wait_for_timeout(2500)
    # Win-check poll (fix-loop iter 6): rerun #5 read the post-win
    # /statistics page ONCE, mid-render (467 chars, banner not yet
    # injected), because the old loop only re-read while the body was
    # <=40 chars — and then discarded a real win. Poll every ~1.5 s
    # up to ~10 s; stop early on a decisive signal (banner / answer +
    # keyword / Wins counter), otherwise stop when two consecutive
    # reads are byte-identical (settled) and only then evaluate.
    body, _prev, _settled, _url = "", None, False, ""
    for _rd in range(7):
        try:
            # re-look-up by URL every attempt: wrappers/detaches make
            # identity checks unreliable after a mid-game remount
            _fresh = next((f for f in page.frames
                           if "index.html" in (f.url or "")), None)
            fr = _fresh or _countryle_frame(page) or fr
            try:
                _url = fr.url or ""
            except Exception:
                _url = ""
            body = ((fr.evaluate("() => document.body.innerText") or "")
                    + (page.evaluate("() => document.body.innerText") or ""))
        except Exception:
            body = ""
        _decisive = ("you found the" in body.lower()
                     and "countryle" in body.lower())
        _dbg(gid, f"win-check read {_rd}: len={len(body)} "
                  f"settled={body == _prev} decisive={_decisive} "
                  f"tail={body[-120:]!r}")
        if _decisive:
            break
        if _prev is not None and body == _prev:
            _settled = True
            break
        _prev = body
        page.wait_for_timeout(1500)
    _dbg(gid, f"win-check settled={_settled} reads={_rd + 1} "
              f"final len={len(body)} url={_url[:60]}")
    _won, _ev = _countryle_evaluate_win(body, picked, seq, _url)
    if _won:
        return True, _ev
    # Board-frame row fallback ONLY: countryle lists every submitted
    # guess as a row with the country name and its similarity % — an
    # answer row + submitted rows == a solved board even when the site
    # shows no banner text at all. Never applied on /statistics (the
    # stats page uses divs, so the count reads 0 there).
    if "statistics" not in _url.lower():
        try:
            _nrows = fr.evaluate(
                "() => Array.from(document.querySelectorAll('li, [class*=row i]'))"
                ".filter(e => e.offsetParent !== null).length") or 0
        except Exception:
            _nrows = 0
        if a.lower() in body.lower() and _nrows >= len(seq):
            return True, (f"answer row + {_nrows} submitted rows "
                          f"(picked {picked!r})")
        return False, (f"board did not confirm {a!r} (picked {picked!r}, "
                       f"rows={_nrows}/{len(seq)})")
    return False, _ev


def _waffle_board(page):
    """Board tiles only: the page has 100+ stray .tile nodes (ads/hidden
    clones); the real grid is the container holding the most visible ones."""
    try:
        res = page.evaluate("""() => {
          const all = Array.from(document.querySelectorAll('.tile'))
            .filter(t => t && t.offsetParent !== null);
          const byParent = new Map();
          for (const t of all) {
            const p = t.parentElement;
            if (!byParent.has(p)) byParent.set(p, []);
            byParent.get(p).push(t);
          }
          let best = [];
          let bestScore = -1;
          for (const grp of byParent.values()) {
            if (grp.length > 30) continue;
            // The playable grid's tiles are draggable and carry letters;
            // pale/menu grids are empty shells. Score accordingly.
            let drag = 0, txt = 0;
            for (const t of grp) {
              if (/draggable/.test(t.className || '')) drag++;
              if ((t.textContent || '').trim()) txt++;
            }
            const score = drag * 100 + txt * 10 + grp.length;
            if (score > bestScore) { bestScore = score; best = grp; }
          }
          if (!best.length) best = all.slice(0, 30);
          const par = best.length && best[0].parentElement;
          const diag = par ? (par.tagName + '.' +
            (par.className || '').slice(0, 60) + ' kids=' +
            par.children.length) : 'noparent';
          const sample = best.slice(0, 2).map(t =>
            (t.outerHTML || '').slice(0, 220)).join(' ||| ');
          return {tiles: best.map(t => {
            const r = t.getBoundingClientRect();
            // textContent, not innerText: letters sit in nested spans that
            // report empty innerText in headless yet have real text content.
            const ch = ((t.textContent || '').trim()[0] || '');
            let pos = '';
            try { pos = t.getAttribute('data-pos') || ''; } catch (e) {}
            return {x: r.x + r.width / 2, y: r.y + r.height / 2,
                    ch: ch, empty: !ch, pos: pos,
                    green: /green/.test(t.className || ''),
                    cls: (t.className || '').slice(0, 60),
                    n: best.length};
          }), diag: diag, sample: sample};
        }""") or {}
    except Exception:
        res = {}
    if isinstance(res, dict):
        _diag = str(res.get("diag", "?")) + " sample=" + str(res.get("sample", "?"))
        _tiles = res.get("tiles") or []
    else:
        _diag, _tiles = "legacy-list", (res or [])
    try:
        _dbg("waffle", f"container {_diag}")
    except Exception:
        pass
    return _tiles


def _s_waffle(page, ans, gid):
    """Waffle: click-swap misplaced tiles toward the worker's solved grid."""
    _close_modals(page)
    try:
        info = A.waffle(A.target_date())
    except Exception as e:
        return False, f"waffle answer unavailable: {str(e)[:110]}"
    # A.waffle() returns "answer" (21-letter grid string), NOT "solution".
    # The old `info.get("solution")` was always None and only worked via the
    # `or ans` fallback. Read the real key explicitly.
    _sol = info.get("answer") or info.get("solution") or ans
    solution = str(_sol.get("answer") if isinstance(_sol, dict) else _sol or "")
    words = [str(w) for w in (info.get("words") or []) if w]
    if not solution:
        return False, "empty waffle solution"
    # Served-board verification BEFORE the first swap (fix-loop iter 3).
    # The site serves by its own calendar (UTC-day math), so at the PM run
    # it may still serve yesterday's puzzle (#1729) while the engine
    # answers for today (#1730). Applying today's solution to yesterday's
    # board can never converge — the swap logic was never the defect.
    try:
        _tgt = A.target_date()
        _prev_entry = A.waffle_puzzle_for(_tgt - timedelta(days=1)) or {}
        _puzzle_today = info.get("puzzle")
        _puzzle_prev = _prev_entry.get("puzzle")
        _board0 = _waffle_board_letters(page)
        _verdict, _detail = _waffle_served_day(
            _board0, _puzzle_today, _puzzle_prev)
        _dbg("waffle", f"served-board check: board={_board0!r} "
                        f"puzzle[{_tgt}]={str(_puzzle_today)!r} "
                        f"puzzle[{_tgt - timedelta(days=1)}]"
                        f"={str(_puzzle_prev)!r} -> {_verdict} ({_detail})")
        if _verdict == "defer":
            return False, (f"defer: board matches puzzle["
                            f"{_tgt - timedelta(days=1)}] — defer, "
                            f"site has not rolled")
        if _verdict == "proceed":
            _dbg("waffle", f"board matches puzzle[{_tgt}]")
        else:
            return False, (f"served-board mismatch: {_detail}; "
                            f"refusing to solve (fail loudly)")
    except Exception as e:
        return False, f"served-board check failed: {str(e)[:120]}"
    try:
        board = _waffle_board(page)
        _dbg("waffle", f"board tiles={len(board)}")
        b0 = (f"{sum(1 for t in board if t['green'])}/{len(board)}"
              if board else "?")
    except Exception:
        b0 = "?"
    swaps = 0
    bad_pairs = set()
    _seen_pairs = {}
    _best_greens = None
    _blocked = set()
    for _round in range(15):
        board = _waffle_board(page)
        if not board:
            return False, "no waffle tiles found"
        # The 5x5 grid holds 4 empty gap cells; the worker solution is the
        # 21 letter cells in row-major order. DOM order is NOT row-major
        # (React key order), so sort by the tiles' own data-pos grid coords.
        import json as _js
        def _pos(t):
            try:
                p = _js.loads(t.get("pos") or "{}")
                return (int(p.get("y", 0)), int(p.get("x", 0)))
            except Exception:
                return (99, 99)
        cells = sorted([i for i, t in enumerate(board) if not t.get("empty")],
                       key=lambda i: _pos(board[i]))
        state = [{"ch": board[i]["ch"], "green": board[i]["green"],
                  "bi": i} for i in cells]
        _dbg("waffle", f"round {_round + 1}: {len(board)} tiles, "
                        f"{len(cells)} letters")
        # The game's logical state lives in data-pos (DOM order never
        # changes on a swap), so moved = position assignment changed.
        sol = list(solution.replace(" ", ""))
        cur = [(s["ch"], board[s["bi"]].get("pos")) for s in state]
        idx = next((k for k, s in enumerate(state)
                    if not s["green"] and k < len(sol)
                    and s["ch"] != sol[k] and k not in _blocked), None)
        if idx is None:
            break
        want = sol[idx]
        bi, bj = state[idx]["bi"], None
        # Skip pairs that already failed twice: rotate to the next candidate
        # instead of hammering a dead swap for 10 rounds on camera.
        cands = [k for k, s in enumerate(state)
                 if k != idx and not s["green"] and (idx, k) not in bad_pairs]
        j = next((k for k in cands if state[k]["ch"] == want), None)
        if j is None:
            # CYCLE fallback (iteration 2): swap with the cell that wants
            # THIS tile's letter, i.e. resolve the 2-cycle. This is the only
            # principled fallback: it moves both tiles toward their homes.
            j = next((k for k in cands if sol[k] == state[idx]["ch"]), None)
            if j is not None:
                _dbg("waffle", f"cycle fallback: cell {idx}({state[idx]['ch']}) "
                                f"<-> cell {j} (wants {state[idx]['ch']})")
        if j is None:
            # NO arbitrary fallback swap (iteration 2): the old
            # `j = next(iter(cands))` swapped a random tile when the wanted
            # letter was not movable, which undid the previous swap and
            # oscillated the same pair (2026-10-17: V<->E toggled 5x).
            # Block this index for this run and re-scan for the next
            # mismatch; if none remains, stop.
            _dbg("waffle", f"no movable tile holds {want!r} for cell {idx}; "
                            f"blocking index (no arbitrary swap)")
            _blocked.add(idx)
            continue
        bj = state[j]["bi"]
        # Tiles are draggable: drag idx onto j like a finger. Click-click
        # selects but never swaps on this board.
        # First-action anchor (fix-loop iter 4): waffle has no typing, so
        # the first drag-swap is the autocrop anchor + START->first-typing
        # line — the typing-only trigger is what let autocrop cut a 59 s
        # solve to 3.75 s.
        _record_first_action(gid, "swap")
        detail = "drag-swap"
        try:
            ax, ay = board[bi]["x"], board[bi]["y"]
            bx, by = board[bj]["x"], board[bj]["y"]
            page.mouse.move(ax, ay)
            page.wait_for_timeout(300)
            _push_click_at(ax, ay)
            page.mouse.down()
            # Human-speed drag: 14 interpolated steps at ~24 ms (the old
            # steps=12 fired with zero inter-step delay, so tiles flew across
            # the board in a couple of frames) and every step lands in the
            # telemetry track so the cursor overlay traces the whole drag.
            _n_st = 14
            for _si in range(1, _n_st + 1):
                _ix = ax + (bx - ax) * _si / _n_st
                _iy = ay + (by - ay) * _si / _n_st
                page.mouse.move(_ix, _iy)
                try:
                    QP.ev_push(_ix, _iy, "drag")
                except Exception:
                    pass
                page.wait_for_timeout(24)
            page.wait_for_timeout(300)
            page.mouse.up()
            try:
                QP.ev_push(bx, by, "click")
            except Exception:
                pass
            page.wait_for_timeout(900)
        except Exception as e:
            detail = f"ERR drag {str(e)[:80]}"
        swaps += 1
        page.wait_for_timeout(1600)
        try:
            _nb = _waffle_board(page)
            _nc = [(t["ch"], t.get("pos")) for t in _nb
                   if not t.get("empty")]
            _moved = (sorted(_nc) != sorted(cur))
            _dbg("waffle", f"swap {swaps}: idx {idx}({cur[idx][0]})"
                            f"<->{j}({cur[j][0]}) {detail} moved={_moved}")
            if not _moved:
                bad_pairs.add((idx, j))
                if len(bad_pairs) >= 3:
                    _dbg("waffle", "3 dead pairs; stopping swaps early")
                    break
            # Post-swap verify (iteration 2): the two cells must have
            # exchanged letters. moved=True alone only says the
            # (letter,pos) multiset changed, which a snap-back with stale
            # data-pos can also produce.
            try:
                _cells2 = sorted(
                    [i for i, t in enumerate(_nb) if not t.get("empty")],
                    key=lambda i: _pos(_nb[i]))
                _state2 = [{"ch": _nb[i]["ch"]} for i in _cells2]
                _took = (len(_state2) > max(idx, j)
                         and _state2[idx]["ch"] == want
                         and _state2[j]["ch"] == cur[idx][0])
                if not _took:
                    _dbg("waffle", f"swap {swaps} did not take effect: "
                                   f"cell {idx} now "
                                   f"{_state2[idx]['ch'] if len(_state2) > idx else '?'} "
                                   f"(want {want}); marking pair dead")
                    bad_pairs.add((idx, j))
                    if len(bad_pairs) >= 3:
                        _dbg("waffle", "3 dead pairs; stopping swaps early")
                        break
            except Exception:
                pass
            # FIX-LOOP: moved=True but no progress is also a loop. The
            # 2026-10-17 run toggled the same O<->V pair 15x with moved=True
            # and greens stuck at 6-7/21. Stop after 3 repeats of the same
            # unordered pair or 3 swaps with no green increase.
            _pair = tuple(sorted((idx, j)))
            _seen_pairs[_pair] = _seen_pairs.get(_pair, 0) + 1
            try:
                _cur_greens = sum(1 for t in _waffle_board(page) if not t.get("empty") and t.get("green"))
            except Exception:
                _cur_greens = None
            if _best_greens is None:
                _best_greens = _cur_greens
            elif _cur_greens is not None and _cur_greens > (_best_greens or 0):
                _best_greens = _cur_greens
                _seen_pairs.clear()
                # Board changed for the better: a previously blocked index
                # may be solvable now, so unblock and re-scan.
                _blocked.clear()
            if _seen_pairs.get(_pair, 0) >= 3:
                _dbg("waffle", f"same pair {_pair} repeated 3x with no green gain; stopping (was 15x loop)")
                break
        except Exception:
            pass
        if "ERR" in str(detail) or str(detail).startswith(("no-", "bad-", "already-")):
            print(f"[waffle] swap {swaps}: {detail}")
            if swaps >= 3:
                break
    page.wait_for_timeout(3000)
    try:
        _end = [t for t in _waffle_board(page) if not t.get("empty")]
        greens = sum(1 for t in _end if t["green"])
        total = len(_end)
        g1 = [greens, total]
        body = (page.evaluate("() => document.body.innerText") or "")
    except Exception:
        g1, body = [0, 0], ""
    low = body.lower()
    try:
        greens, total = int(g1[0]), int(g1[1])
    except Exception:
        greens, total = 0, 0
    # STRICT: every tile green (a partial board is never a win, even if the
    # page contains words like "complete" somewhere in static text).
    won = (total > 0 and greens >= total) or (
        ("star" in low and "swaps" in low) or "you win" in low
        or "sweet" in low or "well done" in low)
    if won and total > 0 and greens < total and "you win" not in low \
            and "sweet" not in low:
        won = False
    if won:
        # capture the final board screenshot for the "answer" slide
        try:
            box = page.evaluate("""() => {
              const c = document.querySelector('div.board');
              if (!c) return null;
              const r = c.getBoundingClientRect();
              return {x: r.x, y: r.y, width: r.width, height: r.height};
            }""")
            if box and box["width"] > 50 and box["height"] > 50:
                bp = HERE / "videos" / "waffle" / f"board_{A.target_date().isoformat()}.png"
                bp.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(bp), clip=box)
                global _LAST_BOARD_SHOT; _LAST_BOARD_SHOT = str(bp)
                _dbg("waffle", f"board screenshot saved: {bp}")
        except Exception as e:
            _dbg("waffle", f"board screenshot failed: {str(e)[:120]}")
        return True, f"{swaps} swaps, greens {b0}->{greens}/{total} ({str(words)[:80]})"
    return False, f"{swaps} swaps, greens {b0}->{greens}/{total}; no win"


def _shadow_dismiss(page, gid="?"):
    """Click close/X controls hidden inside shadow roots (worgle's About
    dialog lives in <game-app>'s shadow DOM - main-document queries and
    Escape never reach it)."""
    try:
        pt = page.evaluate("""() => {
          const walk = (root) => {
            for (const e of root.querySelectorAll(
                   'button, [role=button], a, [class*=close i]')) {
              const t = ((e.innerText || e.getAttribute('aria-label') || '')
                         .trim().toLowerCase());
              if (['\u00d7', '\u2715', 'close', 'got it', 'ok',
                   'dismiss'].includes(t) && t.length > 1) {
                const r = e.getBoundingClientRect();
                if (r.width > 2 && r.height > 2)
                  return {x: r.x + r.width / 2, y: r.y + r.height / 2, t: t};
              }
            }
            for (const e of root.querySelectorAll('*'))
              if (e.shadowRoot) {
                const p = walk(e.shadowRoot);
                if (p) return p;
              }
            return null;
          };
          return walk(document);
        }""")
        if pt:
            page.mouse.click(pt["x"], pt["y"])
            _dbg(gid, f"shadow-dismiss clicked {pt['t']!r}")
            page.wait_for_timeout(700)
            return True
    except Exception as e:
        _dbg(gid, f"shadow-dismiss failed: {str(e)[:70]}")
    return False


def _s_worgle(page, ans, gid):
    """Worgle: wordle-like board in a <game-app> shell; type + Enter."""
    _close_modals(page)
    for _ in range(3):           # About dialog sits in the shadow DOM
        if not _shadow_dismiss(page, gid):
            break
    try:
        _dismiss_dialogs(page, rounds=2)
    except Exception:
        pass
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
    # The board lives inside <game-app>'s shadow root and can take >10 s to
    # boot; wait for real tiles (shadow-piercing) before typing anything.
    for _tw in range(15):
        try:
            _nt = page.evaluate(_SHADOW_TILE_COUNT)
        except Exception:
            _nt = 0
        if _nt:
            break
        page.wait_for_timeout(2000)
    _dbg(gid, f"board tiles visible: {_nt} after {(_tw + 1) * 2}s")
    try:
        _gb = (page.query_selector("game-app") or page).bounding_box()
        if _gb:
            QP.ev_push(_gb["x"] + _gb["width"] / 2,
                       _gb["y"] + min(300, _gb["height"] / 2), "focus")
    except Exception:
        pass
    page.wait_for_timeout(800)
    _played_wo = []
    for n, g in enumerate(seq):
        _dbg(gid, f"guess {n + 1}/{len(seq)} {g!r}")
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
        _played_wo.append(g)
        _PLAYED[gid] = list(_played_wo)
        _dbg(gid, f"guess {n + 1}/{len(seq)} {g!r} submitted")
    page.wait_for_timeout(2500)
    try:
        body = (page.evaluate("() => document.body.innerText") or "")
    except Exception:
        body = ""
    try:
        win = page.evaluate("""() => {
          const pierce = (root) => {
            let out = [];
            for (const e of root.querySelectorAll('*')) {
              if (e.shadowRoot) out = out.concat(pierce(e.shadowRoot));
            }
            return out.concat([...root.querySelectorAll('.row')]);
          };
          const rows = pierce(document);
          for (const r of rows) {
            const tiles = [...r.querySelectorAll('.tile')].length ? [...r.querySelectorAll('.tile')] :
                (r.querySelectorAll('*').length ? Array.from(r.querySelectorAll('*')).filter(x => x.shadowRoot).flatMap(x => [...x.shadowRoot.querySelectorAll('.tile')]) : []);
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
    _played_ph = []
    for gi, g in enumerate(seq):
        _dbg(gid, f"guess {gi + 1}/{len(seq)} {g!r}")
        ok, why = _phrazle_enter_phrase(page, g, groups=groups or None)
        if not ok:
            if gi < len(seq) - 1:
                _dbg(gid, f"probe {g!r} rejected by site ({why[:60]}); skipping")
                continue
            return False, f"guess {g!r} rejected locally: {why}"
        _played_ph.append(g)
        _PLAYED[gid] = list(_played_ph)
        _dbg(gid, f"guess {gi + 1}/{len(seq)} {g!r} submitted ({why[:60]})")
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


def _attr_norm_name(s):
    """Whitespace-collapsed lowercase name key (pure)."""
    return " ".join(str(s or "").lower().split())


# Batterup probe hygiene (fix-loop iter 25, ANALYSIS22 RC-new-2).
#
# 1. NO_SUGGESTION: names the live site's suggestion index returns
#    nothing for, even though they ARE in the vendored pool. "Jesus
#    Tinoco" is in batterup-players.json but the 2026-10-18 run got
#    `pick ok=False (no suggestions ...)` for him twice — the probe
#    was never submittable, so it burned a guess slot on nothing.
#    Denylisted from _attr_pool (the single choke point feeding both
#    _plan_for slides and solve_attr_game), so it can never be sampled
#    again. Names are matched lowercased.
# 2. PROVEN_SUGGESTION: names the live site PROVABLY suggests — the
#    iter-24 frames show their suggestion rows on the live board
#    (f0030: "Vladimir Guerrero Jr"; f0145: "Alec Burleson"). When a
#    probe yields no suggestions mid-solve, it is REPLACED in place
#    by the first proven name that is not the answer and not already
#    tried — never by inventing a name, and never for the answer
#    itself (the answer is never replaced).
_BATTERUP_NO_SUGGESTION_NAMES = {"jesus tinoco"}
_BATTERUP_PROVEN_SUGGESTION_NAMES = ("Vladimir Guerrero Jr",
                                     "Alec Burleson")


def _attr_pool(gid):
    """Probe-name pool for attribute games (never contains the answer)."""
    try:
        if gid == "batterup":
            j = json.loads((HERE / "frontend_data" / "src" / "lib" / "data" /
                            "batterup-players.json").read_text(encoding="utf-8"))
            return [p.get("player_name") for p in j
                    if isinstance(p, dict) and p.get("player_name")
                    and p.get("player_name").lower()
                    not in _BATTERUP_NO_SUGGESTION_NAMES]
        if gid == "marveldle":
            j = json.loads((HERE / "frontend_data" / "src" / "lib" / "data" /
                            "marveldle-comics.json").read_text(encoding="utf-8"))
            return [c.get("name") for c in j
                    if isinstance(c, dict) and c.get("name")]
    except Exception as e:
        print(f"[{gid}] pool load failed: {e}")
    return []


def _attr_post_submit_probe(page):
    """Post-submit board probe: {rows, dropdown_open, populated,
    populated_rows, feedback_tiles}.

    Rows = filled guess rows EXCLUDING the suggestion dropdown: the old
    selector matched bare `li`, and the open suggestion dropdown IS a
    list of `li`s — so the wait saw "rows" instantly and shot_win was
    taken while the dropdown was still open and the guess rows were
    empty (rerun #5, same as rerun #3). dropdown_open = a visible
    suggestion listbox / autocomplete / dropdown container exists.

    Fix-loop iter 9: narrow the row probe to the game's GUESS-ROW
    elements — [class*=guess] containers and [class*=tile] containers
    holding >=2 tile descendants (a tile container is a row; a lone
    tile is a cell, not a row). The old selector also matched
    [class*=row] layout rows and bare `li`, which pulled in nav/footer
    junk ('Log in', 'Privacy', 'FAQs', 'Contact') and the header row
    while the real guess cells were EMPTY (rerun #8: rows=6 with
    those texts, guess-row attribute cells empty in shot_win).
    populated = at least one guess row carries submitted feedback:
    >=2 descendant leaf cells with non-empty text (header rows, whose
    leaf texts are all column labels, do not count) OR >=1
    coloured/feedback tile. The post-submit wait keys on populated,
    never on dropdown state. Never raises.
    """
    try:
        return page.evaluate("""() => {
          const vis = e => e && e.offsetParent !== null;
          const inDrop = e => !!e.closest(
            '[role=listbox], [role=combobox], [class*=suggest i], ' +
            '[class*=autocomplete i], [class*=dropdown i], ' +
            '[class*=popover i], [class*=menu i]');
          const inChrome = e => !!e.closest('nav, header, footer');
          const cands = Array.from(document.querySelectorAll(
            '[class*=guess i], [class*=tile i]'))
            .filter(e => e && e.offsetParent !== null && !inDrop(e)
              && !inChrome(e));
          const rowsEls = cands.filter(e => {
            if (/guess/i.test(String(e.className || ''))) return true;
            return e.querySelectorAll('[class*=tile i]').length >= 2;
          });
          const rows = rowsEls
            .map(e => (e.innerText || '').trim().slice(0, 60))
            .filter(t => t).slice(-6);
          const headerLabels = new Set(['#', 'team', 'div.', 'div',
            'born', 'age', 'pos.', 'pos', 'name', 'player']);
          const fbRe = /exact|correct|green|yellow|match|present|absent/i;
          let populated = 0, feedback = 0, exactCells = 0;
          for (const r of rowsEls) {
            const descs = Array.from(r.querySelectorAll('*'));
            const filled = descs.filter(x => x.children.length === 0 &&
              (x.innerText || '').trim());
            const tiles = descs.filter(x => fbRe.test(
              String(x.className || '')));
            feedback += tiles.length;
            const isHeader = filled.length > 0 && filled.every(x =>
              headerLabels.has(
                (x.innerText || '').trim().toLowerCase()));
            if ((filled.length >= 2 || tiles.length >= 1) && !isHeader) {
              populated++;
              // Visible exact/green cells INSIDE populated rows only:
              // the old page-wide [class] count swept hidden templates
              // and static page chrome into exact_n.
              exactCells += tiles.length;
            }
          }
          const drop = Array.from(document.querySelectorAll(
            '[role=listbox], [class*=suggest i], [class*=autocomplete i], ' +
            '[class*=dropdown i]')).filter(vis).length > 0;
          return {rows: rows, dropdown_open: drop,
                  populated: populated > 0, populated_rows: populated,
                  feedback_tiles: feedback,
                  visible_exact_cells: exactCells};
        }""") or {"rows": [], "dropdown_open": False, "populated": False,
                  "populated_rows": 0, "feedback_tiles": 0,
                  "visible_exact_cells": 0}
    except Exception:
        return {"rows": [], "dropdown_open": False, "populated": False,
                "populated_rows": 0, "feedback_tiles": 0,
                "visible_exact_cells": 0}


def _attr_dismiss_dropdown_soft(page):
    """Close the suggestion dropdown WITHOUT clearing the input, for use
    BETWEEN pick and submit (fix-loop iter 11): Enter sent while the
    autocomplete is open is consumed by the dropdown (select/close)
    instead of submitting, which is how batterup's rerun-#9 guess sat
    in the input with empty rows. Escape closes the suggestion list
    while keeping the picked name in the input; blur finishes it.
    Unlike _attr_dismiss_suggestion (post-submit, clears the input),
    this must NOT clear — clearing would destroy the picked guess.
    Never raises. The submit-verification gate below arbitrates: if
    the soft dismiss ever lost the pick, verification fails and the
    retry re-picks."""
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
    except Exception:
        pass
    try:
        page.evaluate("() => { try { if (document.activeElement) "
                      "document.activeElement.blur(); } catch (e) {} }")
    except Exception:
        pass


def _attr_dismiss_suggestion(page):
    """Blur + clear the guess input so the suggestion container closes
    before the win screenshot (rerun #8: Escape alone left the
    dropdown open because the input still held text + focus, and
    shot_win showed the pre-submit suggestion box over empty rows).
    Post-submit only: the guess is already submitted, so clearing the
    search box cannot alter the board. Focus + Ctrl+A + Backspace goes
    through real key events (a JS value="" would bypass a
    React-controlled input); Escape + blur finish the dismiss. Never
    raises."""
    try:
        _focused = bool(page.evaluate("""() => {
          const inp = document.querySelector('input[name=guess]');
          if (!inp) return false;
          inp.focus(); return true;
        }"""))
    except Exception:
        _focused = False
    try:
        if _focused:
            page.keyboard.press("ControlOrMeta+a")
            page.wait_for_timeout(150)
            page.keyboard.press("Backspace")
            page.wait_for_timeout(300)
        page.keyboard.press("Escape")
        page.wait_for_timeout(800)
    except Exception:
        pass
    try:
        page.evaluate("() => { try { if (document.activeElement) "
                      "document.activeElement.blur(); } catch (e) {} }")
    except Exception:
        pass


def _attr_guess_rows(page):
    """Filled guess rows on an attribute-game board (batterup/marveldle).

    The post-submit board shows one row per SUBMITTED guess. A pick that
    failed (no suggestion row) never submits, so it must never appear here.
    Suggestion-dropdown `li`s are excluded (see _attr_post_submit_probe).
    Returns the row texts (possibly []). Never raises."""
    return _attr_post_submit_probe(page).get("rows") or []


def _attr_wait_post_submit(page, gid, timeout_s=8.0):
    """Wait for the post-submit board to render: a POPULATED guess row
    (attribute cells non-empty / coloured feedback tiles). Fix-loop
    iter 9: the old wait required rows AND dropdown closed — but the
    dropdown_open detector flags the suggestion container as open even
    after submit (the input still holds text + focus; Escape does not
    dismiss it), so the wait timed out on every run while the guess
    rows were empty (reruns #5–#8 shot_win showed the pre-submit
    board). Dropdown state is now logged, never a gate. Returns the
    rows.
    """
    import time as _time
    _t0 = _time.time()
    rows, _drop, _pop = [], True, False
    _pop_n, _fb = 0, 0
    while _time.time() - _t0 < timeout_s:
        _pr = _attr_post_submit_probe(page)
        rows, _drop = _pr.get("rows") or [], bool(_pr.get("dropdown_open"))
        _pop = bool(_pr.get("populated"))
        _pop_n, _fb = int(_pr.get("populated_rows") or 0), \
            int(_pr.get("feedback_tiles") or 0)
        if _pop:
            break
        try:
            page.wait_for_timeout(500)
        except Exception:
            break
    _dbg(gid, f"post-submit board: rows={len(rows)} populated={_pop} "
              f"populated_rows={_pop_n} feedback_tiles={_fb} "
              f"dropdown_open={_drop} "
              f"after {_time.time() - _t0:.1f}s")
    return rows


def _attr_consent_recheck(page, gid, why, full=False):
    """Re-dismiss a CMP/consent overlay that (re-)appeared mid-solve.

    Fix-loop iter 24 (ANALYSIS21 RC4): Batterup's "We value your
    privacy" CMP re-appears after the first guess (ad-iframe navigation
    re-triggers it) and covers the board, so every later pick/submit
    click times out. The one-shot _close_modals sweep at solve start
    never re-runs (dedup via _qp_modals_closed), so the attribute-game
    loop must re-check consent explicitly: before every pick, after
    every submit, and whenever submit verification fails with the RC4
    signature (dropdown open + no row growth). _dismiss_consent clicks
    an Accept-all button inside a consent/cookie/CMP/privacy container
    (including shadow DOM / same-origin iframes); full=True also forces
    a full modal sweep for non-CMP dialogs. Returns the clicked button
    label, or None. Never raises."""
    hit = None
    try:
        hit = _dismiss_consent(page)
    except Exception:
        hit = None
    if hit:
        _dbg(gid, f"consent re-check ({why}): dismissed CMP via {hit}")
    if full:
        try:
            _close_modals(page, _force=True)
        except Exception:
            pass
    return hit


def _attr_verify_submitted(page, gid, baseline_rows, timeout_s=8.0):
    """Verify a submit actually landed on an attribute-game board.

    A pick + sent keystroke is NOT a submission: batterup rerun #9
    counted "1 submitted guess" while Enter never submitted, the
    suggestion dropdown stayed open, and the guess rows stayed empty.
    The ONLY proof of submission is populated-row GROWTH past the
    pre-submit baseline (the probe excludes suggestion-dropdown rows,
    so a stale row from an earlier guess can never vouch for this one).

    After growth is seen, the lingering suggestion dropdown is dismissed
    and the board is re-probed: a genuine submit must leave populated
    rows AND a closed dropdown. If the dropdown detector still reports
    open after the dismiss, the submission is still accepted on row
    growth alone (with a WARN): the dropdown detector is known to stay
    open after genuine submits (iter9), while row growth cannot happen
    without a submission. Returns (verified, probe). Never raises.
    """
    import time as _time
    _t0 = _time.time()
    _pr = {"rows": [], "dropdown_open": False, "populated": False,
           "populated_rows": 0, "feedback_tiles": 0}
    while _time.time() - _t0 < timeout_s:
        _pr = _attr_post_submit_probe(page)
        if int(_pr.get("populated_rows") or 0) > int(baseline_rows or 0):
            break
        try:
            page.wait_for_timeout(500)
        except Exception:
            break
    _grew = int(_pr.get("populated_rows") or 0) > int(baseline_rows or 0)
    if _grew:
        _pr["failure_kind"] = "verified"
        # Dismiss the lingering suggestion dropdown, then re-probe: the
        # board must still show the new row, and the dropdown should
        # now read closed.
        try:
            _attr_dismiss_suggestion(page)
        except Exception:
            pass
        try:
            _pr = _attr_post_submit_probe(page)
        except Exception:
            pass
        _grew = int(_pr.get("populated_rows") or 0) > int(baseline_rows or 0)
        if _grew and _pr.get("dropdown_open"):
            _dbg(gid, "WARN: dropdown still open after dismiss; accepting "
                      "submission on populated-row growth (probe excludes "
                      "dropdown rows) — dropdown detector is known to stay "
                      "open after genuine submits (iter9)")
    else:
        # Iter 25 (ANALYSIS22 RC-new-2): an unverified submit that
        # leaves the dropdown OPEN with NO row growth is the
        # "selection never happened" signature — NOT the CMP signature.
        # Iter-23/24 blamed the CMP overlay, but iter-24 proved it
        # wrong: zero CMP/consent lines and NO consent dialog in any
        # gameplay frame, yet all 6 submits failed exactly this way.
        # The CMP re-check below is kept ONLY as a fallback (a CMP
        # may still appear on some runs); it is no longer treated as
        # the cause when no dialog is present. The caller uses the
        # failure_kind on the probe to trigger a RE-SELECT (click the
        # suggestion row again), not a blind re-pick.
        _pr["failure_kind"] = _attr_submit_failure_kind(_pr, baseline_rows)
        if _pr.get("dropdown_open"):
            try:
                _attr_consent_recheck(
                    page, gid,
                    "unverified submit, dropdown open + no row growth",
                    full=True)
            except Exception:
                pass
        # Clean the board state for the retry's fresh pick.
        try:
            _attr_dismiss_suggestion(page)
        except Exception:
            pass
    _dbg(gid, f"submit verify: baseline={baseline_rows} "
              f"populated_rows={_pr.get('populated_rows')} "
              f"feedback_tiles={_pr.get('feedback_tiles')} "
              f"dropdown_open={_pr.get('dropdown_open')} verified={_grew} "
              f"after {_time.time() - _t0:.1f}s")
    return bool(_grew), _pr


def _attr_submit_failure_kind(probe, baseline_rows=0):
    """Classify an unverified submit from its post-submit probe (pure).

    - "verified": populated rows grew past the baseline.
    - "selection-never-happened": dropdown still OPEN and zero
      populated rows — the iter-24 Batterup shape (ANALYSIS22
      RC-new-2). The suggestion was never selected, so the submit
      keystroke was consumed by the open autocomplete; re-SELECT,
      do not blindly re-pick.
    - "no-row-growth": anything else (dropdown closed, still no row).
    """
    try:
        pr = probe or {}
        if int(pr.get("populated_rows") or 0) > int(baseline_rows or 0):
            return "verified"
        if pr.get("dropdown_open") and int(pr.get("populated_rows") or 0) == 0:
            return "selection-never-happened"
    except Exception:
        pass
    return "no-row-growth"


def _attr_selection_state(page, name):
    """Did the suggestion pick actually SELECT a player (Batterup)?

    The old pick check could not tell: `_type_country_guess` verified
    only that the input held the typed name — but typed text equals
    the name whether or not the suggestion row was ever selected, so
    "pick ok=True" fired while the dropdown sat open with nothing
    selected (iter-24: pick ok=True, then every submit left
    dropdown_open=True + 0 rows). The ONLY discriminator between
    "typed" and "selected" is the suggestion dropdown CLOSING with
    the input holding exactly the picked name.

    Returns {"selected": True/False/None, "suggestion_open": bool,
    "input_value": str}. selected=None means the probe itself was
    unavailable (evaluate failed / returned nothing) — the caller
    falls back to the pick's own verdict with a WARN, never crashes.
    Never raises.
    """
    try:
        res = page.evaluate("""(want) => {
          const inp = document.querySelector(
            'input[name=guess], #input-box, input[type=text], ' +
            'input:not([type]), input[type=search]');
          const val = inp ? (inp.value || '') : '';
          const sels = ['[role=listbox]', '[role=option]',
            '.react-autosuggest__suggestions-container--open',
            'ul[class*=suggest i]', '[class*=suggestion i]',
            '[class*=autocomplete i] [role=listbox]'];
          let open = false;
          for (const s of sels) {
            const els = document.querySelectorAll(s);
            for (const e of els) {
              if (e && e.offsetParent !== null) { open = true; break; }
            }
            if (open) break;
          }
          return {input_value: val, suggestion_open: open};
        }""", name)
    except Exception:
        return {"selected": None, "suggestion_open": None,
                "input_value": ""}
    if not isinstance(res, dict):
        return {"selected": None, "suggestion_open": None,
                "input_value": ""}
    _open = bool(res.get("suggestion_open"))
    _val = str(res.get("input_value") or "")
    _selected = (not _open) and \
        _attr_norm_name(_val) == _attr_norm_name(name)
    return {"selected": _selected, "suggestion_open": _open,
            "input_value": _val}


def _attr_click_suggestion_once(page, name, method):
    """One attempt to click/select the suggestion row for `name`.

    Methods, in escalating order:
    - "playwright-click": a REAL Playwright click on the exact-match
      row (real mousedown/mouseup/click + scrolling). This is the fix
      for the iter-24 failure: the old path used only a JS
      `element.click()`, which dispatches a bare `click` event — a
      React autocomplete that selects on mousedown / real pointer
      events never sees a selection from that, so the dropdown
      stayed open and nothing was ever selected.
    - "mouse-events": dispatch the full mousedown + mouseup + click
      sequence in-page (covers onMouseDown handlers when no element
      handle is reachable).
    - "keyboard": ArrowDown to highlight, then Enter to select — the
      autocomplete-native selection path. Enter here SELECTS (the
      dropdown is open); it is not the submit keystroke.
    Returns True when the attempt was actually performed. Never raises.
    """
    try:
        if method == "playwright-click":
            _sels = ("[role=option]",
                     ".react-autosuggest__suggestion",
                     "[class*=suggest i] li", "[class*=suggestion i]",
                     ".dropdown-item", ".dropdown-menu li")
            for _sel in _sels:
                try:
                    _rows = page.query_selector_all(_sel)
                except Exception:
                    continue
                for _row in _rows or []:
                    try:
                        _txt = (_row.inner_text() or "").strip()
                    except Exception:
                        continue
                    if _attr_norm_name(_txt) != _attr_norm_name(name) \
                            and not _attr_norm_name(_txt).startswith(
                                _attr_norm_name(name)):
                        continue
                    try:
                        _row.scroll_into_view_if_needed(timeout=3000)
                    except Exception:
                        pass
                    try:
                        _bb = _row.bounding_box()
                        if _bb:
                            QP.ev_push(_bb["x"] + _bb["width"] / 2,
                                       _bb["y"] + _bb["height"] / 2,
                                       "click")
                    except Exception:
                        pass
                    _row.click(timeout=3000)
                    return True
            return False
        if method == "mouse-events":
            return bool(page.evaluate("""(want) => {
              const rows = Array.from(document.querySelectorAll(
                '.react-autosuggest__suggestion, [role=option], ' +
                '[role=listbox] li, .dropdown-menu li, .dropdown-item, ' +
                'ul[class*=suggest i] li, [class*=suggestion i]'));
              const w = want.trim().toLowerCase();
              const best = rows.find(e =>
                (e.innerText || '').trim().toLowerCase() === w)
                || rows.find(e =>
                  (e.innerText || '').trim().toLowerCase().startsWith(w));
              if (!best) return false;
              const opts = {bubbles: true, cancelable: true, view: window};
              best.dispatchEvent(new MouseEvent('mousedown', opts));
              best.dispatchEvent(new MouseEvent('mouseup', opts));
              best.dispatchEvent(new MouseEvent('click', opts));
              return true;
            }""", name))
        if method == "keyboard":
            page.keyboard.press("ArrowDown")
            try:
                page.wait_for_timeout(400)
            except Exception:
                pass
            page.keyboard.press("Enter")
            try:
                page.wait_for_timeout(800)
            except Exception:
                pass
            return True
    except Exception:
        return False
    return False


def _attr_reselect(page, name):
    """Re-select the suggestion row WITHOUT retyping (Batterup).

    Used when a pick reported ok but no selection landed, and when a
    submit fails with the "selection-never-happened" signature
    (dropdown open + 0 rows): the input still holds the name and the
    dropdown still shows the suggestion, so clicking the row again is
    the repair — retyping + re-picking is the fallback, not the first
    move. Each method is verified via _attr_selection_state before
    the next is tried. Returns (ok, detail). Never raises.
    """
    for _method in ("playwright-click", "mouse-events", "keyboard"):
        try:
            _did = _attr_click_suggestion_once(page, name, _method)
        except Exception:
            _did = False
        if not _did:
            continue
        try:
            _st = _attr_selection_state(page, name)
        except Exception:
            _st = {"selected": None}
        if _st.get("selected") is True:
            return True, f"selected via {_method}"
        if _st.get("selected") is None:
            return False, "selection probe unavailable"
    return False, "selection never landed after re-select attempts"


def solve_attr_game(page, answer_name, gid, api_probes=()):
    """Attribute search-box games (batterup, marveldle): 2 real probes with
    on-screen column feedback, then the answer. Suggestion rows are picked by
    exact-name match like a human; every step is debug-logged.

    Evidence counts SUBMITTED guesses only: a name whose suggestion pick
    failed (pick ok=False) was never submitted, so it is recorded in
    _REJECTED (attempted-pick-failed), never in _PLAYED, and never in the
    "after N guesses" evidence count (rerun #3 batterup said "after 3
    guesses" when only 1 guess was submitted). A pick + sent keystroke
    is likewise NOT a submission: the guess joins _submitted only after
    _attr_verify_submitted sees populated guess rows grow past the
    pre-submit baseline (batterup rerun #9: Enter never submitted, yet
    the guess was counted). A guess with no populated row never reaches
    the win check, and the answer-echo fallback is gone: the answer
    merely sitting in the input / open dropdown is not win evidence.

    Fix-loop iter 25 (ANALYSIS22 RC-new-2, batterup only): "pick ok"
    now means the suggestion was SELECTED, not merely that a
    suggestion appeared. Typing + suggestion appearance alone never
    counts: after the pick, _attr_selection_state must report the
    dropdown closed with the input holding exactly the picked name,
    or the pick is re-selected (real click / mouse events /
    keyboard) before anything is submitted. A submit that leaves
    dropdown_open=True + 0 populated rows is classified
    "selection-never-happened" and triggers a re-select first, not a
    blind re-pick. A probe whose suggestion index returns nothing is
    replaced in place by a proven-suggestion name; the answer is
    never replaced. The CMP re-check stays as a fallback only —
    iter-24 refuted CMP as the cause (no dialog in any frame)."""
    _close_modals(page)
    answer_name = str(answer_name or "").strip()
    if not answer_name:
        return False, "empty answer"
    pool = [n for n in _attr_pool(gid) if n and n.lower() != answer_name.lower()]
    probes = [str(p) for p in (list(api_probes or [])[:2]) if p]
    if gid == "batterup":
        # Never play a denylisted no-suggestion name even if a caller
        # passed it in as an api_probe.
        probes = [p for p in probes
                  if p.lower() not in _BATTERUP_NO_SUGGESTION_NAMES]
    # Date-seeded sample, same seed as _plan_for (fix-loop iter 11):
    # the old pool[0] / pool[mid] picks opened every video with the
    # same two probes. Never the answer (excluded from pool above).
    import random as _r4
    for cand in (_r4.Random(_day_seed(gid)).sample(
            pool, min(2, len(pool))) if pool else []):
        if len(probes) >= 2:
            break
        if cand and cand not in probes:
            probes.append(cand)
    seq = probes[:2] + [answer_name]
    _submitted = []    # names VERIFIED submitted (pick ok + board growth)
    _pick_failed = []  # names whose suggestion pick failed (never submitted)
    _submit_failed = []  # names picked but never verified submitted
    _dbg(gid, f"plan: probes={probes} answer={answer_name!r}")

    def _pick_and_confirm(_name, _label="pick"):
        """Type + click the suggestion, then CONFIRM a real selection.

        Returns (ok, detail). For batterup, ok=True means SELECTED:
        after _type_country_guess reports ok, _attr_selection_state
        must report the dropdown closed with the input holding exactly
        the name; if it does not, the row is re-selected (real click /
        mouse events / keyboard) and only a confirmed selection keeps
        ok=True. A pick that fails outright is retried once after a
        consent re-check + soft dropdown close (the iter-24 CMP path),
        then confirmed again. For other games this is just the pick:
        their pick paths are proven and their dropdowns may
        legitimately stay open, so no dropdown-closed gate is applied.
        """
        _ok, _detail = _type_country_guess(page, page, _name)
        if not _ok:
            # A pick that fails while a CMP covers the board
            # (Playwright click timeout) is NOT a missed suggestion —
            # the board was unclickable. Re-check consent, close the
            # dropdown, and retry the pick once before dropping it.
            _attr_consent_recheck(page, gid, f"pick failed for {_name!r}",
                                  full=True)
            _attr_dismiss_dropdown_soft(page)
            _ok, _detail = _type_country_guess(page, page, _name)
            _dbg(gid, f"guess {_label} {_name!r}: re-pick ok={_ok} "
                      f"({_detail})")
        if _ok and gid == "batterup":
            try:
                _st = _attr_selection_state(page, _name)
            except Exception:
                _st = {"selected": None}
            if _st.get("selected") is False:
                _dbg(gid, f"guess {_label} {_name!r}: pick reported ok "
                          f"but no selection landed "
                          f"(suggestion_open={_st.get('suggestion_open')} "
                          f"input={_st.get('input_value')!r}); re-selecting")
                _sel_ok, _sel_detail = _attr_reselect(page, _name)
                if _sel_ok:
                    _detail = (f"{_detail} (selection confirmed via "
                               f"{_sel_detail})")
                else:
                    _ok = False
                    _detail = (f"suggestion never selected "
                               f"({_sel_detail}); pick ok=False")
            elif _st.get("selected") is None:
                _dbg(gid, f"guess {_label} {_name!r}: selection probe "
                          f"unavailable; accepting pick verdict "
                          f"({_detail})")
        return _ok, _detail

    for n, name in enumerate(seq):
        last = (n == len(seq) - 1)
        _idle_drift(page)
        # RC4 (iter 24): the CMP can (re-)appear between guesses
        # (ad-iframe navigation re-triggers it); the start-of-solve
        # sweep never re-runs (dedup), so re-check consent before
        # every pick. Cheap CMP-only probe; a no-op when no CMP is
        # present.
        _attr_consent_recheck(page, gid, f"before guess {n + 1}")
        # Pre-submit baseline: the verification gate is ROW GROWTH past
        # this count, so a stale row from an earlier guess can never
        # vouch for this one.
        try:
            _baseline_rows = int(
                _attr_post_submit_probe(page).get("populated_rows") or 0)
        except Exception:
            _baseline_rows = 0
        ok, detail = _pick_and_confirm(name, f"{n + 1}/{len(seq)}")
        _dbg(gid, f"guess {n + 1}/{len(seq)} {name!r}: pick ok={ok} ({detail})")
        if not ok and gid == "batterup" and not last \
                and "no suggestions" in str(detail):
            # Iter 25: the probe produced NO suggestion at all (the
            # "Jesus Tinoco" shape) — it can never be submitted, so
            # REPLACE it in place with a proven-suggestion name
            # instead of burning the slot. The replacement is picked
            # and confirmed like any other guess; the failed probe is
            # recorded as pick-failed (never submitted). The answer
            # (last) is NEVER replaced.
            _tried = {_attr_norm_name(x)
                      for x in list(seq) + _pick_failed + _submitted}
            for _fb in _BATTERUP_PROVEN_SUGGESTION_NAMES:
                if _attr_norm_name(_fb) in _tried \
                        or _attr_norm_name(_fb) == \
                        _attr_norm_name(answer_name):
                    continue
                _fb_ok, _fb_detail = _pick_and_confirm(
                    _fb, f"{n + 1}/{len(seq)}")
                _dbg(gid, f"guess {n + 1}/{len(seq)}: probe {name!r} "
                          f"had no suggestions; replacement {_fb!r} "
                          f"pick ok={_fb_ok} ({_fb_detail})")
                if _fb_ok:
                    _pick_failed.append(name)
                    _REJECTED[gid] = list(_pick_failed)
                    name, detail, ok = _fb, _fb_detail, True
                    break
        if not ok:
            # suggestion missed: drop this probe and continue like a human.
            # It was NEVER submitted: record separately, do not count it.
            _pick_failed.append(name)
            _REJECTED[gid] = list(_pick_failed)
            if not last:
                continue
            return False, (f"answer pick failed ({detail}); "
                           f"submitted={len(_submitted)} "
                           f"attempted-pick-failed={len(_pick_failed)}")
        # Submit with bounded retries, VERIFIED against the board: a pick
        # + sent keystroke is not a submission (batterup rerun #9: Enter
        # did not submit, dropdown stayed open, rows stayed empty, yet
        # the guess was counted as submitted). The guess joins _submitted
        # only after _attr_verify_submitted sees populated rows grow.
        _verified = False
        _pr = {"populated_rows": _baseline_rows, "dropdown_open": False,
               "feedback_tiles": 0,
               "failure_kind": "no-row-growth"}
        _reselect_tried = False  # re-select already attempted this guess
        _skip_soft_dismiss = False  # confirmed selection already closed it
        for _attempt in range(3):
            if gid == "batterup":
                # NEVER submit without a confirmed selection: an Enter
                # sent while the autocomplete is open is consumed by
                # the dropdown instead of submitting (iter-24: 6/6
                # submits failed exactly this way). If no selection
                # landed, re-select first; only a confirmed selection
                # (or an unavailable selection probe) reaches submit.
                try:
                    _sel = _attr_selection_state(page, name)
                except Exception:
                    _sel = {"selected": None}
                if _sel.get("selected") is False:
                    _sel_ok, _sel_detail = _attr_reselect(page, name)
                    _reselect_tried = True
                    if not _sel_ok:
                        _skip_soft_dismiss = False
                        _dbg(gid, f"guess {n + 1}/{len(seq)} {name!r}: "
                                  f"no selection before submit "
                                  f"({_sel_detail}); NOT submitting")
                        _verified = False
                        _pr = {"populated_rows": _baseline_rows,
                               "dropdown_open": True,
                               "feedback_tiles": 0,
                               "failure_kind":
                                   "selection-never-happened"}
                        # Fall through to the retry handling below,
                        # which re-picks (retypes) since re-select
                        # already failed for this guess.
                        _attempt_failed_no_submit = True
                    else:
                        _attempt_failed_no_submit = False
                        # Re-select closed the dropdown: nothing left
                        # for the Escape soft-dismiss to do.
                        _skip_soft_dismiss = True
                elif _sel.get("selected") is True:
                    # Dropdown already closed on a confirmed selection:
                    # skip the Escape soft-dismiss — there is nothing
                    # to dismiss, and Escape must not disturb a
                    # confirmed selection.
                    _attempt_failed_no_submit = False
                    _skip_soft_dismiss = True
                else:
                    _attempt_failed_no_submit = False
                    _skip_soft_dismiss = False
            else:
                _attempt_failed_no_submit = False
            if not _attempt_failed_no_submit:
                # Dismiss the suggestion dropdown FIRST (soft: no
                # clearing) — unless a confirmed Batterup selection
                # already closed it (see above): Enter sent while the
                # autocomplete is open is consumed by the dropdown
                # instead of submitting (batterup rerun #9).
                if not _skip_soft_dismiss:
                    _attr_dismiss_dropdown_soft(page)
                _skip_soft_dismiss = False
                _submit_branch = "Enter key"
                try:
                    btn = page.query_selector(
                        "button:has-text('Guess'), "
                        "button:has-text('Submit'), "
                        "button:has-text('Go'), "
                        "button[aria-label*='Guess'], "
                        "button[aria-label*='Submit'], "
                        "button[title*='Guess'], "
                        "button[title*='Submit']")
                    if btn and btn.is_visible():
                        btn.click(timeout=3000)
                        _submit_branch = "Guess-button click"
                    else:
                        page.keyboard.press("Enter")
                except Exception:
                    try:
                        page.keyboard.press("Enter")
                    except Exception:
                        pass
                _dbg(gid, f"guess {n + 1}/{len(seq)} {name!r}: submit "
                          f"attempt {_attempt + 1}/3 via {_submit_branch}")
                _settle(page, base=3000)
                _verified, _pr = _attr_verify_submitted(
                    page, gid, _baseline_rows)
            if _verified:
                break
            if _attempt < 2:
                _kind = _attr_submit_failure_kind(_pr, _baseline_rows)
                _dbg(gid, f"guess {n + 1}/{len(seq)} {name!r}: submit "
                          f"attempt {_attempt + 1}/3 NOT verified on the "
                          f"board (failure_kind={_kind}); "
                          f"re-selecting/re-picking and retrying")
                if _kind == "selection-never-happened" \
                        and not _reselect_tried:
                    # Iter 25: selection never happened — the input
                    # still holds the name and the dropdown still
                    # shows the suggestion, so RE-SELECT (click the
                    # row again) WITHOUT retyping. Only if the
                    # re-select fails do we fall through to a full
                    # re-pick below. The CMP re-check stays as a
                    # fallback (verification already ran one); CMP is
                    # no longer presumed to be the cause.
                    _sel_ok, _sel_detail = _attr_reselect(page, name)
                    _reselect_tried = True
                    if _sel_ok:
                        _dbg(gid, f"guess {n + 1}/{len(seq)} {name!r}: "
                                  f"re-selected ({_sel_detail}); "
                                  f"retrying submit without retyping")
                        continue
                    _dbg(gid, f"guess {n + 1}/{len(seq)} {name!r}: "
                              f"re-select failed ({_sel_detail}); "
                              f"falling through to full re-pick")
                # Re-pick: the input was cleared by the dismiss inside
                # verification, so type + pick the suggestion again
                # (and confirm the selection for batterup).
                _attr_consent_recheck(
                    page, gid, "before re-pick after unverified submit")
                _attr_dismiss_dropdown_soft(page)
                ok, detail = _pick_and_confirm(
                    name, f"{n + 1}/{len(seq)}")
                _dbg(gid, f"guess {n + 1}/{len(seq)} {name!r}: re-pick "
                          f"ok={ok} ({detail})")
                _reselect_tried = False
                if not ok:
                    break
            else:
                _dbg(gid, f"guess {n + 1}/{len(seq)} {name!r}: submit "
                          f"FAILED after 3 attempts (never submitted)")
        if not _verified:
            # NEVER counted as submitted: no _submitted / _PLAYED entry,
            # no win check on it (a guess with no populated row is not a
            # submitted guess, whatever the pick reported).
            _submit_failed.append(name)
            if last:
                return False, (f"answer {name!r} NOT submitted after "
                               f"3 attempts (board never showed a new "
                               f"populated row); submitted={_submitted} "
                               f"attempted-pick-failed={_pick_failed} "
                               f"attempted-submit-failed={_submit_failed}")
            _dbg(gid, f"guess {n + 1}/{len(seq)} {name!r}: submit failed; "
                      f"dropping this probe (never submitted)")
            continue
        _submitted.append(name)
        _PLAYED[gid] = list(_submitted)
        _REJECTED[gid] = list(_pick_failed)
        try:
            txt = (page.evaluate("() => document.body.innerText") or "")
            # exact_n counts VISIBLE exact/green cells inside POPULATED
            # guess rows only (probe). The old page-wide [class] count
            # swept hidden templates/chrome into exact_n.
            _pr0 = _attr_post_submit_probe(page)
            exact_n = int(_pr0.get("visible_exact_cells") or 0)
            rows = _attr_guess_rows(page)
        except Exception:
            txt, exact_n, rows = "", 0, []
        _dbg(gid, f"guess {n + 1} feedback chars={len(txt)} "
                  f"visible_exact_cells={exact_n} rows={rows}")
        if last and not rows:
            _dbg(gid, "WARN: guess rows show no words on screen")
        if last:
            okw, ev = _strict_win(page, answer_name)
            # exact_n was read above from the visible-populated-rows
            # probe (never recomputed page-wide).
            # A win requires ALL THREE: visible win text (via _strict_win,
            # which reads rendered text only) AND populated guess rows
            # AND feedback tiles on the board. The submission gate above
            # already proved a new populated row for this guess; the
            # board cross-check below re-proves it at win time.
            _board = _attr_post_submit_probe(page)
            _board_rows = int(_board.get("populated_rows") or 0)
            _board_fb = int(_board.get("feedback_tiles") or 0)
            if okw and _board_rows < 1:
                # Win text with no populated guess row is not a win
                # (e.g. a stale banner over an unsubmitted board).
                _dbg(gid, "win signal ignored: no populated guess rows "
                          f"(rows={_board_rows} feedback={_board_fb})")
                okw, ev = False, "win signal without populated guess rows"
            if not okw and exact_n >= 5:
                # All attribute columns lit: the board agrees even when the
                # banner text differs. This path has no visible win text,
                # so it needs the board to vouch twice: a populated row
                # AND in-row feedback tiles (page-wide tile counts alone
                # can come from hidden templates / static page chrome).
                if _board_rows >= 1 and _board_fb >= 1:
                    okw, ev = True, (f"all-attribute win ({exact_n} "
                                     f"visible exact/green cells in "
                                     f"populated rows; "
                                     f"board rows={_board_rows} "
                                     f"feedback={_board_fb})")
                else:
                    _dbg(gid, "all-attribute tiles ignored: board too "
                              f"thin (rows={_board_rows} "
                              f"feedback={_board_fb})")
            if not okw:
                # Banner/confetti can lag the final submit by seconds.
                page.wait_for_timeout(5000)
                okw, ev = _strict_win(page, answer_name)
                _dbg(gid, f"recheck after 5s: ok={okw} ({ev})")
                if okw:
                    _board = _attr_post_submit_probe(page)
                    if int(_board.get("populated_rows") or 0) < 1:
                        _dbg(gid, "recheck win signal ignored: no "
                                  "populated guess rows")
                        okw, ev = False, ("win signal without populated "
                                          "guess rows")
            _board_rows = int(_board.get("populated_rows") or 0)
            _board_fb = int(_board.get("feedback_tiles") or 0)
            if okw:
                return True, (f"{ev} after {len(_submitted)} submitted "
                              f"guesses (submitted={_submitted}, "
                              f"attempted-pick-failed={_pick_failed}, "
                              f"attempted-submit-failed={_submit_failed}; "
                              f"board rows={_board_rows} "
                              f"feedback={_board_fb})")
            _echo_present = answer_name.lower() in (txt or "").lower()
            # NO answer-echo fallback (removed): the answer merely
            # appearing in the input field or an open suggestion dropdown
            # is not win evidence — it is present whether or not the
            # game was won (batterup rerun #9: "answer echo" fired on an
            # unsubmitted board). A win needs the game's own signal.
            _dbg(gid, f"answer echo present={_echo_present} (not win evidence)")
            return False, (f"no win signal; {ev} "
                           f"(submitted={len(_submitted)}, "
                           f"attempted-pick-failed={len(_pick_failed)}, "
                           f"attempted-submit-failed={_submit_failed})")
    return False, "no guesses played"


def _batterup_site_header(page):
    """The site's own displayed puzzle id/date: `#NNN MM/DD/YY` in the page
    header (rerun #3: `#971 10/16/26`). Returns
    {"number","display","iso"} or None. Never raises."""
    try:
        txt = page.evaluate("() => document.body.innerText") or ""
    except Exception:
        return None
    import re as _re
    m = _re.search(r"#\s*(\d+)\s+(\d{1,2})/(\d{1,2})/(\d{2})\b", txt)
    if not m:
        return None
    num, mo, dy, yr = m.groups()
    return {"number": num, "display": f"{mo}/{dy}/{yr}",
            "iso": f"20{yr}-{int(mo):02d}-{int(dy):02d}"}


def _s_batterup(page, ans, gid):
    _close_modals(page)
    solved, evidence = solve_attr_game(page, ans, gid)
    # Date honesty (fix-loop iter 4): log BOTH the CDN game_date (the date
    # the answer was keyed on) and the site's displayed header date, and
    # only claim the date when they agree OR the discrepancy is the known
    # CDN-vs-display rollover. Do NOT change which puzzle is served — the
    # served board already accepted the answer; this only makes the date
    # claim honest.
    try:
        _cdn = _BATTERUP_CDN.get(gid) or {}
        _cdn_date = str(_cdn.get("game_date") or "")
        _hdr = _batterup_site_header(page)
        _site_iso = (_hdr or {}).get("iso", "")
        _tgt = A.target_date()
        _tgt_iso = _tgt.isoformat()
        _prev_iso = (_tgt - timedelta(days=1)).isoformat()
        if _cdn_date and _site_iso and _cdn_date == _site_iso == _tgt_iso:
            _date_note = (f"dates agree: cdn_game_date={_cdn_date} "
                          f"site_header=#{_hdr['number']} {_hdr['display']}")
        elif _cdn_date == _tgt_iso and _site_iso == _prev_iso:
            _date_note = (
                f"dates differ by known rollover: cdn_game_date={_cdn_date} "
                f"site_header=#{_hdr['number']} {_hdr['display']} "
                f"({_site_iso}); CDN keys by the UTC-day boundary "
                f"(~01:04 UTC) while the site displays the US-local date — "
                f"same puzzle, display rollover, NOT a previous-day board")
        else:
            _date_note = (
                f"WARNING: date mismatch unexplained: "
                f"cdn_game_date={_cdn_date or '?'} "
                f"site_header={('#' + _hdr['number'] + ' ' + _hdr['display'] + ' ' + _site_iso) if _hdr else 'not found'} "
                f"target={_tgt_iso}; date claim NOT made")
        _dbg(gid, f"batterup dates: {_date_note}")
        evidence = f"{evidence} [{_date_note}]"
    except Exception as _e:
        _dbg(gid, f"batterup date check failed: {str(_e)[:100]}")
    return solved, evidence


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
    n = 0
    for _iw in range(10):
        try:
            n = page.evaluate(
                "() => document.querySelectorAll("
                "'input,textarea,[contenteditable=true]').length")
        except Exception:
            n = 0
        if n:
            break
        page.wait_for_timeout(2000)
    _dbg(gid, f"after menu: url={page.url} inputs={n} "
              f"(waited {(_iw + 1) * 2}s)")
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
    except SystemExit as e:
        print(f"[marveldle] probe source exited: {e}")
    except Exception as e:
        print(f"[marveldle] probe info failed: {str(e)[:110]}")
    if not str(ans or "").strip() or str(ans) == "None":
        # Iteration 2: NEVER browser-solve with no answer. A missing answer
        # means the pick-id gate fired, so the site's board is the previous
        # day's puzzle. Defer instead of playing yesterday's game.
        return False, ("answer unavailable: pick-id gate; defer, do not "
                       "browser-solve previous-day board")
    return solve_attr_game(page, ans, gid, api_probes=probes)


def _marveldle_browser_solve(page, gid, max_guesses=14):
    # DISABLED (iteration 2): no longer called. Browser-solving with no
    # answer played the previous-day board when the pick-id gate fired.
    # Kept for reference; do not re-enable without a served-day check.
    """Identify today's character IN-PAGE (the public guess API answers
    205-empty): guess date-seeded candidates from the vendored comics pool,
    read the column feedback, stop on an all-Exact win."""
    import json as _j
    import random as _r
    try:
        pool = [c for c in _j.loads((HERE / "frontend_data" / "src" / "lib" /
                                     "data" / "marveldle-comics.json")
                                    .read_text(encoding="utf-8"))
                if isinstance(c, dict) and c.get("name")]
    except Exception as e:
        return False, f"no comics pool: {str(e)[:80]}"
    _dbg(gid, f"browser-solve pool={len(pool)} candidates")
    rnd = _r.Random(f"marveldle:{A.target_date().isoformat()}")
    rnd.shuffle(pool)
    tried = set()
    for n in range(max_guesses):
        cand = next((c for c in pool if c["name"] not in tried), None)
        if not cand:
            break
        tried.add(cand["name"])
        ok, detail = _type_country_guess(page, page, cand["name"])
        _dbg(gid, f"browser-solve guess {n + 1}: {cand['name']!r} ok={ok}")
        if not ok:
            continue
        try:
            btn = page.query_selector(
                "button:has-text('Guess'), button:has-text('Submit')")
            if btn and btn.is_visible():
                btn.click(timeout=3000)
            else:
                page.keyboard.press("Enter")
        except Exception:
            page.keyboard.press("Enter")
        _settle(page, base=3000)
        fb = page.evaluate("""() => {
          const rows = Array.from(document.querySelectorAll(
            '[class*=guess i], [class*=row i], li, tr'))
            .filter(e => e.offsetParent !== null);
          const last = rows[rows.length - 1];
          if (!last) return null;
          const cols = Array.from(last.querySelectorAll(
            '[class*=exact i], [class*=correct i], [class*=none i], td, div'))
            .map(e => ((e.className || '').toString() + ' ' +
                       (e.innerText || '')).toLowerCase());
          return cols.slice(0, 14);
        }""") or []
        blob = " ".join(fb)
        _dbg(gid, f"feedback cols: {blob[:140]!r}")
        if "exact" in blob and " none" not in blob:
            _FOUND_ANSWER[gid] = cand["name"]
            return True, (f"browser-solve win on guess {n + 1}: "
                          f"{cand['name']!r}")
    return False, f"browser-solve exhausted {len(tried)} candidates"


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
                if os.environ.get("FORCE_REGENERATE") == "true":
                    print(f"[{gid}] force mode: re-rendering despite record")
                    return None
                return r
    except Exception:
        pass
    return None


def _write_skip_result(gid, date_key, done):
    """Fix-loop iter8: a no-duplicate skip must leave an auditable artifact.

    Run 39029436203 (iter7 rerun, dispatched without force=true) hit the
    no-duplicate guard for all 18 games and exited 0 writing NOTHING to
    videos/{gid}/ — every artifact was a ~270-byte zip holding only the
    START debug line, yet the run concluded success. A skip that claims
    success must say so in videos/{gid}/result.json (skipped=true, no fresh
    video in this run) so the artifact, the bundle pack, and the watch can
    tell "skipped: already done" apart from "solved fresh in this run".
    """
    try:
        vdir = HERE / "videos" / gid
        vdir.mkdir(parents=True, exist_ok=True)
        (vdir / "result.json").write_text(json.dumps(
            {"game": gid, "date": date_key, "solved": True, "skipped": True,
             "evidence": "skipped: database already solved",
             "video": done.get("video")}, indent=1), encoding="utf-8")
    except Exception:
        pass


def _db_save(date_key, gid, record):
    """Save structured record: database/{date}/{game}/result.json + youtube.txt.

    Keeps per-date + per-game separation so search by date or by game is a
    plain folder listing. video mp4 itself stays in videos/{game}/ (gitignored)
    and is uploaded as a workflow artifact; only the link + json commit cleanly.
    """
    d = _db_dir(date_key, gid)
    try:
        # Never DOWNGRADE a solved record: a later flaky re-run must not
        # replace a good publishable video entry with its own failure.
        _old_p = d / "result.json"
        if _old_p.exists() and not record.get("solved"):
            try:
                _old = json.loads(_old_p.read_text(encoding="utf-8"))
                if _old.get("solved") and str(_old.get("video") or "").endswith(".mp4"):
                    print(f"[db] keeping solved {gid} record for {date_key} "
                          f"(new run failed: {str(record.get('evidence'))[:60]})")
                    return
            except Exception:
                pass
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


def _embed_chapters(mp4_path, chapters):
    """Embed chapters into an MP4 via ffmetadata remux (chapters were JSON-only).

    chapters: list of (start_seconds_float, title). Writes a temp ffmetadata
    file, remuxes with -map_metadata 1, replaces the original on success.
    Returns True on success, False otherwise. Never raises.
    """
    try:
        import subprocess as _sp
        p = Path(str(mp4_path))
        if not p.exists() or not chapters or len(chapters) < 1:
            return False
        # Need durations: use next chapter start as end, last chapter to EOF.
        # ffmetadata needs TIMEBASE 1/1000 and START/END in ms.
        import json as _js
        _r = _sp.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                      "-of", "json", str(p)], capture_output=True, text=True, timeout=60)
        _dur = float((_js.loads(_r.stdout or "{}").get("format") or {}).get("duration") or 0)
        if _dur <= 0:
            return False
        lines = [";FFMETADATA1"]
        for _i, (_s, _t) in enumerate(chapters):
            _start = int(float(_s) * 1000)
            _end = int(float(chapters[_i + 1][0]) * 1000) if _i + 1 < len(chapters) else int(_dur * 1000)
            if _end <= _start:
                _end = _start + 1000
            _title = str(_t).replace("=", "-").replace(";", ",").replace("#", "").replace("\n", " ")[:100]
            lines.append("[CHAPTER]")
            lines.append("TIMEBASE=1/1000")
            lines.append(f"START={_start}")
            lines.append(f"END={_end}")
            lines.append(f"title={_title}")
        _meta = p.parent / (p.stem + ".ffmetadata")
        _meta.write_text("\n".join(lines) + "\n", encoding="utf-8")
        _tmp = p.parent / (p.stem + ".chapters.mp4")
        _r2 = _sp.run(["ffmpeg", "-y", "-v", "error", "-i", str(p), "-i", str(_meta),
                       "-map", "0", "-map_metadata", "1", "-c", "copy", str(_tmp)],
                      capture_output=True, text=True, timeout=600)
        try:
            _meta.unlink()
        except Exception:
            pass
        if _r2.returncode == 0 and _tmp.exists() and _tmp.stat().st_size > 1000:
            _tmp.replace(p)
            return True
        try:
            _tmp.unlink()
        except Exception:
            pass
        return False
    except Exception:
        return False


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


def _nerdle_effective_date(now_utc=None):
    """Website effective date for Nerdle (mirror of
    nerdle_solver._nerdle_effective_date — keep the two in agreement;
    test_iter13_fixes asserts it). Boundary constant lives in
    nerdle_solver.NERDLE_SITE_BOUNDARY_UTC; the (16, 30) default here
    must match it."""
    now = now_utc or datetime.utcnow()
    boundary = now.replace(hour=16, minute=30, second=0, microsecond=0)
    boundary = boundary + timedelta(seconds=60)
    eff = now.date() + timedelta(days=1 if now >= boundary else 0)
    return datetime(eff.year, eff.month, eff.day)


def _nerdle_site_solutions_match(site_solutions, official_eqs):
    """Runner-side mirror of nerdle_solver._nerdle_site_solutions_match
    (iter-19, P0): exact set equality of site solutions vs official
    answers for site-solutions-gated modes."""
    site = [str(e) for e in (site_solutions or [])]
    official = [str(e) for e in (official_eqs or [])]
    return bool(site) and bool(official) and set(site) == set(official)


def _nerdle_number_gate(served, api_numbers, official=None):
    """Runner-side mirror of nerdle_solver._nerdle_number_gate (iter 14
    rework; iter-19 mini fix): hard-check served == API ONLY for the
    4 proven modes (classic, mini, midi, maxi); micro/minibi/quad/
    instant/speed are site-rules — an API mismatch there NEVER blocks
    shipping, and the API is never cited as proof for those modes.
    Returns (blocked, detail, per_mode). See the solver for the full
    rule + the per-mode trust table.

    Iter-19: when mini's header EQUALS classic's header it IS the
    global daily number, not a mode-specific sequence — the header
    is ignored and mini gates on site_solutions == official answers
    (exact set equality when `official` is passed). Any other mini
    header falls through to the header-vs-API check. Keep this
    mirror in agreement with the solver; test_iter13/14 assert it.
    """
    per_mode = {}
    blocked_reasons = []
    if not isinstance(api_numbers, dict) or not api_numbers:
        return True, "no API puzzle numbers (API row missing)", per_mode
    served = served if isinstance(served, dict) else {}
    _proven = ("classic", "mini", "midi", "maxi")
    _site_solutions_gated = ("mini",)
    for mode, nums in api_numbers.items():
        if mode == "classicPuzzleNumber":
            continue
        ev = served.get(mode) or {}
        hn = ev.get("header_number")
        want = [int(n) for n in (nums or [])]
        if mode == "classic":
            if not isinstance(hn, int):
                blocked_reasons.append("classic header number not exposed")
                per_mode[mode] = {"api": want, "served": None,
                                  "status": "blocked: no header",
                                  "trust": "api-proven"}
            elif hn not in want:
                blocked_reasons.append(
                    f"classic header #{hn} != API {want}")
                per_mode[mode] = {"api": want, "served": hn,
                                  "status": "blocked: mismatch",
                                  "trust": "api-proven"}
            else:
                per_mode[mode] = {"api": want, "served": hn,
                                  "status": "match",
                                  "trust": "api-proven"}
            if ev.get("matches_target_official") is False:
                blocked_reasons.append(
                    "classic served solution != API classic answer")
                per_mode[mode]["answer"] = "mismatch"
            continue
        if mode in _site_solutions_gated:
            _classic_hn = (served.get("classic") or {}).get(
                "header_number")
            _is_global = (isinstance(hn, int)
                          and isinstance(_classic_hn, int)
                          and hn == _classic_hn)
            if _is_global:
                _site = ev.get("site_solutions") or []
                if official is not None:
                    _ok = _nerdle_site_solutions_match(
                        _site, (official or {}).get(mode) or [])
                else:
                    _ok = ev.get("matches_target_official") is True
                if _ok:
                    per_mode[mode] = {
                        "api": want, "served": hn,
                        "status": "match (site-solutions == official; "
                        "header is the global number, not compared)",
                        "trust": "api-proven (site-solutions)"}
                else:
                    blocked_reasons.append(
                        f"{mode} site solutions != official answers "
                        f"(header #{hn} is the global number, not a "
                        f"mode-specific sequence; "
                        f"site_solutions={_site})")
                    per_mode[mode] = {
                        "api": want, "served": hn,
                        "status": "blocked: site-solutions mismatch",
                        "trust": "api-proven (site-solutions)"}
                continue
        if mode in _proven:
            if not isinstance(hn, int):
                per_mode[mode] = {"api": want, "served": None,
                                  "status": "warning: not exposed",
                                  "trust": "api-proven"}
            elif hn not in want:
                blocked_reasons.append(
                    f"{mode} header #{hn} not in API {want}")
                per_mode[mode] = {"api": want, "served": hn,
                                  "status": "blocked: mismatch",
                                  "trust": "api-proven"}
            else:
                per_mode[mode] = {"api": want, "served": hn,
                                  "status": "match",
                                  "trust": "api-proven"}
            if ev.get("matches_target_official") is False:
                blocked_reasons.append(
                    f"{mode} served solution != API answer")
                per_mode[mode]["answer"] = "mismatch"
            continue
        # Unproven modes: site rules; API mismatch never blocks.
        if not isinstance(hn, int):
            per_mode[mode] = {"api": want, "served": None,
                              "status": "warning: not exposed "
                              "(site rules; API not authoritative)",
                              "trust": "site-rules"}
        elif hn not in want:
            per_mode[mode] = {"api": want, "served": hn,
                              "status": "warning: header mismatch "
                              "(site rules; API not authoritative)",
                              "trust": "site-rules"}
        else:
            per_mode[mode] = {"api": want, "served": hn,
                              "status": "match (site rules)",
                              "trust": "site-rules"}
        if ev.get("matches_target_official") is False:
            per_mode[mode]["answer"] = ("warning: mismatch "
                                        "(site rules)")
    if blocked_reasons:
        return True, "; ".join(blocked_reasons), per_mode
    return False, "all proven-mode served numbers/answers match API", per_mode


def run_nerdle_external(g, tgt, date_key, today, short):
    """Run the merged 9-mode nerdle solver (async, kept as-is) via subprocess."""
    import subprocess as _sp
    # Fix-loop iter 13 revised: Nerdle's date is the WEBSITE effective
    # date (16:30 UTC boundary), NOT the pipeline date_key. Rebind so
    # the already-done check, result filing, DB record and video name
    # all use the effective date. A pinned pipeline date does NOT pin
    # Nerdle: in the 15:30-16:30 UTC window the effective date is the
    # pipeline date - 1, and the solver plays + files that date (the
    # pipeline-date video is produced by a run after the boundary).
    _eff = _nerdle_effective_date()
    _eff_key = _eff.strftime("%Y-%m-%d")
    if _eff_key != str(date_key):
        print(f"[nerdle] date basis: pipeline date_key={date_key} but "
              f"website effective date={_eff_key}; filing under effective date")
    date_key = _eff_key
    done = _db_already_done(date_key, "nerdle")
    if done:
        print(f"[nerdle] already done for {date_key}, skipping (no duplicate)")
        _dbg("nerdle", f"SKIP already done for {date_key} (no duplicate); no fresh video in this run")
        _write_skip_result("nerdle", date_key, done)
        return {"game": "nerdle", "solved": True, "video": done.get("video"),
                "evidence": "skipped: database already solved"}
    vdir = HERE / "videos" / "nerdle"
    vdir.mkdir(parents=True, exist_ok=True)
    # Fix-loop iter 12: preliminary result BEFORE the hour-long
    # subprocess — a crash/kill during the solver must still leave
    # an auditable result.json (previously only the post-solve
    # normalization wrote one).
    _CURRENT_RUN.update({"gid": "nerdle", "date_key": date_key,
                         "solved": False, "evidence": "",
                         "stage": "answer"})
    _write_preliminary_result("nerdle", date_key, False, "started")
    env = dict(os.environ)
    env.setdefault("HEADLESS", "true")
    env.setdefault("TZ_OFFSET_MINUTES", "540")
    env.setdefault("BROWSER_TZ", "Pacific/Kiritimati")
    # Fix-loop iter 13 revised: pass the WEBSITE effective date (computed
    # once in this function), not the pipeline date_key — the solver
    # targets the effective date and the browser clock is faked to it.
    env["NERDLE_EFFECTIVE_DATE"] = str(date_key)
    print(f"[nerdle] target {date_key} (9 modes, one video)")
    try:
        r = _sp.run([sys.executable, str(HERE / "nerdle_solver.py")],
                    cwd=str(HERE), env=env, capture_output=True, text=True, timeout=3600)
        # Iter-15 (P1, ANALYSIS11 maxi ² stall): forward the FULL
        # solver stdout into the artifact debug log. The old code
        # printed only the LAST 2000 chars, which hid the stall
        # reason (the solver's own "no next guess / board did not
        # react" print, if any, was lost). Console keeps the
        # truncated tail; the debug log gets everything.
        try:
            with open(vdir / f"debug_{date_key}.log", "a",
                      encoding="utf-8") as _fh:
                _fh.write(f"\n--- solver subprocess stdout (full, "
                          f"returncode={r.returncode}) ---\n")
                _fh.write(r.stdout or "")
                _fh.write("\n--- solver subprocess stderr (full) ---\n")
                _fh.write(r.stderr or "")
        except Exception as _e:
            print(f"[nerdle] full solver stdout forward failed: {_e}")
        print((r.stdout or "")[-2000:])
        if r.returncode != 0:
            print((r.stderr or "")[-2000:])
    except Exception as e:
        # Fix-loop iter 12: the old path returned here with NO
        # result.json at all (subprocess launch/timeout failure).
        _write_preliminary_result("nerdle", date_key, False,
                                  f"subprocess: {e}")
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
    _wd_blocked, _wd_detail = False, ""
    try:
        src = None
        if flat_res.exists():
            src = flat_res
        if src:
            rec = json.loads(src.read_text(encoding="utf-8"))
            solved = bool(rec.get("solved"))
            # Fix-loop iter 13 revised — NUMBER GATE (runner side, belt
            # & braces): the solver gates too, but if a final somehow
            # exists for a number-mismatched board, never ship it.
            # Re-check from the solver's own record (API numbers +
            # served numbers), not from the pipeline date.
            # Iter-19: pass the solver's official answer lists so
            # the mini site-solutions gate checks exact set equality.
            _wd_blocked, _wd_detail, _wd_per_mode = _nerdle_number_gate(
                rec.get("served"), rec.get("api_puzzle_numbers"),
                official=rec.get("equations_official"))
            if _wd_blocked:
                _dbg("nerdle", f"NUMBER GATE BLOCKED: {_wd_detail} "
                               f"— refusing to ship final")
                solved = False
                rec["number_gate"] = {"blocked": True,
                                      "detail": _wd_detail,
                                      "per_mode": _wd_per_mode}
                try:
                    if final and Path(str(final)).exists():
                        _q = vdir / f"nerdle_WRONGDAY_BLOCKED_{date_key}.mp4"
                        Path(str(final)).rename(_q)
                        _dbg("nerdle", "wrong-day final quarantined -> "
                                       f"{_q.name}")
                except Exception as _e_q:
                    _dbg("nerdle", "wrong-day quarantine failed: "
                                   f"{str(_e_q)[:80]}")
            evidence = f"modes={rec.get('modes')}"
            try:
                src.rename(vdir / f"result_{date_key}.json")
            except Exception:
                pass
            # FIX-LOOP: enrich normalized result so artifact is self-contained:
            # video/evidence/guesses/chapters/qc like other games. Previously
            # vdir/result.json was just {game,date,solved,modes} with no
            # video/evidence/answers/chapters, so answers were unrecoverable.
            _modes2 = rec.get("modes") or []
            _guesses2 = [f"{m.get('id')}:{m.get('typed')}" for m in _modes2 if isinstance(m, dict)]
            # Iteration 2: use the REAL chapters the solver recorded (it
            # computes them from per-mode starts + card offsets). The old
            # code invented (0, mode) entries, which are not chapter times.
            # Iter-19 (ANALYSIS16 §6 item 15): when the solver recorded
            # NO chapters (unsolved run: no assembly, chapters=[]), emit
            # NO chapters — the all-[0, ...] placeholders claimed nine
            # chapters all starting at 0s. Omit, never invent.
            _ch2 = [(float(s), str(t)) for s, t in (rec.get("chapters") or [])
                     if isinstance(s, (int, float))]
            # Embed chapters into the MP4 (iteration 2): the nerdle path
            # never ran _embed_chapters, so rerun #1 had 0 embedded chapters.
            try:
                if final and Path(str(final)).exists() and _ch2:
                    _ok_ch2 = _embed_chapters(str(final), _ch2)
                    _dbg("nerdle", f"chapters embedded={_ok_ch2} n={len(_ch2)}")
            except Exception as _e_ch2:
                _dbg("nerdle", f"chapter embed skipped: {str(_e_ch2)[:80]}")
            _pr2 = _probe_video(final) if final and Path(str(final)).exists() else {}
            _qc2 = {"game": "nerdle", "date": date_key, "solved": bool(rec.get("solved")) and not _wd_blocked,
                     "evidence": str(evidence)[:300], "video": None if _wd_blocked else (str(final) if final and Path(str(final)).exists() else None),
                     "probe": _pr2, "cursor_events": len(QP.ev_all()),
                     "cursor_clips": (QP.LAST_POLISH or {}).get("clips", 0),
                     "frame_mode": (QP.LAST_POLISH or {}).get("mode"),
                     "guesses": len(_guesses2)}
            _qc2["ok"] = bool(_qc2["solved"] and _qc2["video"] and str(_qc2["video"]).endswith(".mp4") and _pr2.get("ok") and _pr2.get("duration_s", 0) >= 40 and _pr2.get("width") == 1920 and _pr2.get("height") == 1080)
            for _m in _modes2:
                # Iteration 2: typed counts probes + official guesses, while
                # expected counts official equations only, so typed !=
                # expected is NORMAL (classic typed 2 = 1 probe + 1 official).
                # Only signal != "won" is a real failure.
                if isinstance(_m, dict) and _m.get("signal") != "won":
                    _dbg("nerdle", f"mode {_m.get('id')}: signal={_m.get('signal')} "
                                   f"typed={_m.get('typed')} expected={_m.get('expected')} NOT WON")
            rec = {**rec, "video": _qc2["video"], "evidence": str(evidence)[:300],
                   "guesses": _guesses2, "chapters": _ch2, "qc": _qc2,
                   # Iteration 2: the old "answers" dict held typed COUNTS,
                   # not equations. Record counts under their true name and
                   # pass through the solver's real equations (if present).
                   "typed_counts": {str(m.get("id")): m.get("typed") for m in _modes2 if isinstance(m, dict)},
                   "equations": rec.get("equations") or {}}
            try:
                (vdir / f"qc_{date_key}.json").write_text(json.dumps(_qc2, indent=1), encoding="utf-8")
            except Exception:
                pass
            (vres).write_text(json.dumps(rec, indent=1), encoding="utf-8")
    except Exception as e:
        evidence = f"result parse: {e}"
    record = {"game": "nerdle", "date": date_key, "solved": solved,
              "evidence": (f"NUMBER GATE: {_wd_detail} " if _wd_blocked else "") + str(evidence)[:300],
              "video": None if _wd_blocked else (str(final) if final.exists() else None)}
    _db_save(date_key, "nerdle", record)
    try:
        _modes = json.loads((vdir / f"result_{date_key}.json").read_text(
            encoding="utf-8")).get("modes", [])
    except Exception:
        _modes = []
    _npr = _probe_video(final) if final else {}
    # Fix-loop iter 6: the QC log line used to read PASS from the probe
    # alone, printing "QC PASS ... solved=False" for an unsolved run
    # while result.json qc.ok was already False. PASS requires solved too.
    _dbg("nerdle", f"QC {'PASS' if (final and _npr.get('ok') and solved) else 'FAIL'}: "
                   f"mp4={final} size={_npr.get('size_mb')}MB "
                   f"dur={_npr.get('duration_s')}s solved={solved}")
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
    # Fix-loop iter 12: register in-flight state + preliminary result
    # at the START of the framed path (it bypasses run_one's post-solve
    # write), so a crash/kill during the four-mode solve still leaves
    # an auditable result.json.
    _CURRENT_RUN.update({"gid": gid, "date_key": date_key, "solved": False,
                         "evidence": "", "stage": "answer"})
    _write_preliminary_result(gid, date_key, False, "started")
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
        _write_preliminary_result(gid, date_key, False,
                                  "no framed answers available")
        return {"game": gid, "solved": False, "video": None,
                "evidence": "no framed answers available"}

    vdir = HERE / "videos" / gid
    vdir.mkdir(parents=True, exist_ok=True)
    per_mode, solved_modes = [], []

    with sync_playwright() as p:
        b = p.chromium.launch(headless=HEADLESS, args=["--js-flags=--max-old-space-size=1536", "--renderer-process-limit=8", "--disable-dev-shm-usage"])
        _sstate = HERE / "browser_state.json"
        _state_kwargs = {"storage_state": str(_sstate)} if _sstate.exists() else {}
        ctx = b.new_context(record_video_dir=str(vdir),
                            record_video_size={"width": 1920, "height": 1080},
                            viewport={"width": 1920, "height": 1080},
                            timezone_id=os.environ.get("BROWSER_TZ", "Pacific/Kiritimati"),
                            locale="en-US", **_state_kwargs)
        if HAS:
            iso = os.environ.get("FAKE_DATE_ISO", tgt.strftime("%Y-%m-%dT00:05:00"))
            try:
                ctx.add_init_script(script=QP.get_fake_date_init_script(iso))
            except Exception:
                pass
        try:
            ctx.add_init_script(script=QP.prepurge_js())
            ctx.add_init_script(script=QP.consent_watch_js())
            ctx.add_init_script(script=QP.adhide_js())
        except Exception:
            pass

        def _route(route):
            try:
                u = route.request.url
                if any(x in u for x in ("user-sync", "usync", "setuid", "criteo", "quantserve", "scorecardresearch", "hotjar", "amplitude", "sentry.io", "tiktok", "facebook.net", "connect.facebook", "clarity.ms", "/ads/index", "ads/index.")):
                    return route.abort()
            except Exception:
                pass
            return route.continue_()
        ctx.route("**/*", _route)
        pg = ctx.new_page()
        _REC_T0["framed"] = time.time()
        # Per-guess logging: _play_guesses logs with page._qp_gid, which was
        # only set inside _s_wordle — every other game's per-guess lines
        # went to videos/wordle-like/ or were lost. Set it at page creation.
        try:
            pg._qp_gid = "framed"
        except Exception:
            pass
        _instrument_mouse(pg)
        # A stray JS alert/confirm (ads, "already played") blocks EVERY
        # later evaluate call forever - auto-dismiss so a popup can never
        # hang a 6-hour job. Same handler is attached in run_one below.
        pg.on("dialog", _auto_dismiss_dialog("framed"))
        try:
            pg.on("framenavigated", _log_nav("framed"))
        except Exception:
            pass
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
        try:
            ctx.storage_state(path=str(HERE / "browser_state.json"))
            print(f"[{gid}] browser_state.json saved")
        except Exception as _e:
            print(f"[{gid}] state save failed: {str(_e)[:120]}")
        ctx.close()
        b.close()

    evidence = "; ".join(
        f"{d['mode']}={'WIN' if d.get('solved') else ('DEFERRED' if d.get('deferred') else 'FAIL')}" for d in per_mode
    ) or "no modes played"
    # A success means every mode that had an answer and could be played was won.
    playable = [d for d in per_mode if d.get("answer")]
    solved = bool(playable) and all(d.get("solved") for d in playable)
    print(f"[{gid}] solved={solved} ({len(solved_modes)}/{len(per_mode)} modes) {evidence}")
    # Preliminary result.json for the framed path too (run_framed_all
    # bypasses run_one's post-solve write; _assemble_framed writes the
    # final record only after polish + QC).
    _CURRENT_RUN.update({"gid": gid, "date_key": date_key,
                         "solved": bool(solved),
                         "evidence": str(evidence)[:500],
                         "stage": "polish"})
    _write_preliminary_result(gid, date_key, solved, evidence)
    return _assemble_framed(gid, g, date_key, today, short, answers, per_mode,
                            solved, evidence, vpath)


def _framed_anchor(d):
    """Segment anchor for one Framed per_mode entry (iter 13, C2):
    the MODE-SWITCH time (when the bot navigates to the mode), NOT
    the board-ready time. Inter-mode dead time (page reload + the
    poster ad gate, ~24-30s) belongs to the mode it precedes, so it
    must air inside THAT mode's chapter, not the previous mode's.
    Falls back to t_start for entries without t_switch."""
    if d.get("t_switch") is not None:
        return d["t_switch"]
    return d.get("t_start")


def _framed_segment_bounds(per_mode, duration):
    """Segment bounds for the Framed assembly, from mode-switch marks.

    Anchors are MODE-SWITCH times (see _framed_anchor), NOT
    board-ready times: the old code anchored at board-ready (t_start),
    so the inter-mode dead time (reload + poster ad gate, ~24-30s)
    aired inside the PREVIOUS mode's chapter (iter-12: ~30s of
    poster-gate footage inside the Titleshot chapter). With
    switch-anchored bounds, a mode's segment runs from its own
    switch to the next mode's switch, so the gate footage airs in
    the gated mode's chapter, and the win screen (which precedes
    the next switch) stays uncut in its own segment.
    `t_switch` stamps are RECORDING-relative (time.time() - _REC_T0)
    and the polished gameplay clip starts at recording 0 with
    autocrop OFF, so the bounds are the RAW anchor values clamped to
    [0, duration] — NOT rebased by the first mark. Marks within 2s of
    the previous kept mark are dropped. Returns (bounds, kept) with
    bounds strictly increasing, one segment per kept mark, every
    segment >= 2s (short trailing segments are dropped from BOTH
    lists).
    """
    gd = float(duration or 0)
    marks = sorted(
        (d for d in (per_mode or []) if _framed_anchor(d) is not None),
        key=lambda d: _framed_anchor(d))
    kept, starts = [], []
    for d in marks:
        st = min(max(float(_framed_anchor(d)), 0.0), gd)
        if not starts or st - starts[-1] > 2.0:
            starts.append(st)
            kept.append(d)
    bounds = [(starts[i],
               starts[i + 1] if i + 1 < len(starts) else gd)
              for i in range(len(starts))]
    # Drop a trailing segment shorter than 2s (a mark that landed in
    # the last moments of the recording has no footage to show).
    while bounds and bounds[-1][1] - bounds[-1][0] < 2.0:
        bounds.pop()
        kept.pop()
    assert all(bounds[i][1] > bounds[i][0] >= 0
               for i in range(len(bounds))), \
        f"framed bounds not increasing: {bounds}"
    assert len(bounds) == len(kept), \
        f"framed bounds/marks mismatch: {len(bounds)} vs {len(kept)}"
    return bounds, kept


def _assemble_framed(gid, g, date_key, today, short, answers, per_mode,
                     solved, evidence, vpath):
    """Cut the four Framed modes into one captioned, bordered video."""
    vdir = HERE / "videos" / gid
    if not solved:
        # Single-mode games gate assembly on solved ("skipping final
        # assembly: not solved"); framed assembled anyway, and its end
        # cards are built from the ANSWERS table, so an unplayed mode's
        # answer was presented as solved with no FAIL disclosure. No
        # final when any mode failed — same contract as every other game.
        # Iter 9: a DEFERRED mode (e.g. poster, rewarded ad never loads
        # in CI) is still not solved, so it still blocks assembly — a
        # deferral is honest labelling, never a licence to ship a final
        # that presents an unplayed mode as solved.
        _dbg(gid, "skipping final assembly: not solved "
                  "(no publishable video)")
        # Fast-copy the raw recording for diagnosis (no re-encode,
        # no autocrop) — same unsolved-tail rule as run_one. This copy
        # is a DIAGNOSIS artifact only: vpath is set to None right
        # below, so it can never become the final or be uploaded.
        # (Fix-loop iter 12: vdir used to be assigned AFTER this block,
        # so the copy raised UnboundLocalError, was swallowed by the
        # except, and unsolved runs silently got no gameplay file.)
        try:
            if vpath:
                _dbg(gid, "unsolved: fast-copy gameplay (no re-encode)")
                _fast_copy_gameplay(str(vpath), vdir / "gameplay_1080.mp4")
        except Exception:
            pass
        vpath = None
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
            _kind = "colors" if gid in ("colorfle", "colordle") else None
            _pal = None
            try:
                if gid == "colorfle" and isinstance(ans, dict):
                    _pal = ans.get("paletteHexes") or ans.get("colorHexes")
                elif gid == "colordle" and isinstance(ans, dict) and ans.get("hex"):
                    _pal = [ans.get("hex")]
            except Exception:
                _pal = None
            DP.generate_reveal(str(reveal_p), g["name"], today,
                               ", ".join(f"{m}: {answers.get(k, '?')}"
                                         for k, m, _u in FRAMED_MODES),
                               allsteps, scoring=False)
            _framed_total_guesses = sum(
                int(d.get("guesses") or 0) for d in per_mode)
            # Iter-23 (ANALYSIS20 §6): the breakdown counts live
            # only as pixels in the facts card — the debug log
            # never carried the "N guesses played / M final
            # answers" string, so the last analysis could not
            # verify the breakdown at string level. Emit it
            # here so the artifact log proves the card's stats.
            _dbg(gid, f"framed breakdown: "
                       f"{_framed_total_guesses} guesses played / "
                       f"{len(solved_modes)} final answers")
            DP.generate_facts(str(facts_p), g["name"], today,
                              f"{len(solved_modes)}/{len(FRAMED_MODES)} modes won",
                              allsteps, scoring=False,
                              guesses_played=(_framed_total_guesses or None),
                              final_answers=len(solved_modes))
            DP.generate_teaser(str(teaser_p), g["name"], today, g["slug"])

            def _still(p, secs):
                return (ImageClip(str(p)).set_duration(secs).set_fps(24)
                        .resize(width=1920, height=1080))

            # Autocrop OFF for Framed: t_start marks are
            # recording-relative and autocrop's non-uniform cuts
            # (leading + static) would shift every mode boundary
            # (the misplaced-cards root cause). Polished timeline ==
            # recording timeline, so marks map 1:1 after rebasing.
            play_src = _polish_gameplay(str(final), vdir / "gameplay_1080.mp4",
                                        gid=gid, autocrop=False)
            gameplay = VideoFileClip(play_src)
            gd = float(gameplay.duration or 0)

            # ---- per-mode "NOW PLAYING" cards -------------------------------
            # The session is recorded as ONE continuous clip, so the mode
            # boundaries come from the t_start stamps captured during play. Each
            # mode's footage is cut out and prefixed with a card naming it, so a
            # viewer landing mid-video knows exactly which round is on screen.
            marks = sorted(
                (d for d in per_mode if _framed_anchor(d) is not None),
                key=lambda d: _framed_anchor(d))
            seg_clips, seg_meta = [], []
            if len(marks) >= 2 and gd > 0:
                # Switch-anchored marks (iter 13 C2): the card goes
                # at the segment start = mode switch, and footage
                # follows it (including that mode's own load/gate
                # dead time). Bounds come from
                # _framed_segment_bounds (raw recording-relative
                # t_switch values, NO lead rebase: the polished clip
                # starts at recording 0 with autocrop OFF).
                bounds, kept = _framed_segment_bounds(per_mode, gd)
                _dbg(gid, f"framed bounds (card-anchored): {bounds}")
                for i, (a, b) in enumerate(bounds):
                    d = kept[i]
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
            # NO shared brand intro: wordsolverx-intro-20s.mp4
            # opens EVERY video with a wrong-game, 12-day-stale Wordle
            # answer (MOCHA, Oct 5). The per-game dated recap card
            # opens the video instead (fix-loop iter 11).
            out = str(vdir / f"{gid}_final_{date_key}.mp4")
            clip.write_videofile(out, codec="libx264", audio_codec="aac",
                                 fps=24, verbose=False, logger=None)
            final = out
            try:
                _ok_ch = _embed_chapters(out, chapters)
                _dbg(gid, f"chapters embedded={_ok_ch} n={len(chapters)}")
            except Exception:
                pass
            DP.build_captions_srt(chapters, total, today, g["name"],
                                  out_path=vdir / "captions.srt")
        except Exception as e:
            print(f"[{gid}] assembly fallback raw: {str(e)[:200]}")
            chapters = [(0, "The solve")]
    else:
        # Iter-19: unsolved Framed has no final (vpath=None above),
        # so no chapters — a [(0, 'The solve')] placeholder would
        # claim a chapter for a video that does not exist.
        chapters = [] if not solved else [(0, "The solve")]
    _upload_framed(gid, g, today, short, answers, chapters, final)
    _fpr = _probe_video(final) if final else {}
    # Visible-content QC (shared helper): never QC PASS a blank video.
    _dbg(gid, "QC start")
    _CURRENT_RUN.update({"stage": "qc"})
    _fvis_ok, _fvis_detail = _qc_visible_content(
        gid, vdir, gameplay_path=(vdir / "gameplay_1080.mp4")
        if (vdir / "gameplay_1080.mp4").exists() else None,
        solve_span_s=_SOLVE_SPAN.get(gid),
        gameplay_phase_s=_gameplay_phase_s(_SOLVE_SPAN.get(gid),
                                           _FIRST_TYPE.get(gid)))
    if not _fvis_ok:
        _dbg(gid, f"QC visible-content FAIL: {_fvis_detail}")
    _fqc = {"game": gid, "date": date_key, "solved": bool(solved),
            "evidence": str(evidence)[:300], "video": str(final) if final else None,
            "probe": _fpr, "cursor_events": len(QP.ev_all()),
            "cursor_clips": (QP.LAST_POLISH or {}).get("clips", 0),
            "frame_mode": (QP.LAST_POLISH or {}).get("mode"),
            "guesses": len(per_mode) * 2,
            "visible_content": {"ok": _fvis_ok, "detail": _fvis_detail}}
    _fqc["ok"] = bool(solved and final and str(final).endswith(".mp4") and _fpr.get("ok")
                      and _fpr.get("duration_s", 0) >= 40
                      and _fpr.get("width") == 1920 and _fpr.get("height") == 1080
                      and _fvis_ok)
    _dbg(gid, f"QC {'PASS' if _fqc['ok'] else 'FAIL'}: mp4={_fqc['video']} "
               f"res={_fpr.get('width')}x{_fpr.get('height')} dur={_fpr.get('duration_s')}s solved={solved}")
    try:
        (vdir / f"qc_{date_key}.json").write_text(json.dumps(_fqc, indent=1), encoding="utf-8")
    except Exception:
        pass
    try:
        (vdir / f"telemetry_{date_key}.json").write_text(json.dumps(QP.ev_all(), indent=1), encoding="utf-8")
    except Exception:
        pass
    (vdir / "result.json").write_text(json.dumps(
        {"game": gid, "date": date_key, "solved": solved, "evidence": evidence,
         "video": str(final) if final else None, "modes": per_mode,
         "answers": answers,
         "chapters": chapters, "qc": _fqc}, indent=1))
    _dbg(gid, "result.json written (final)")
    _db_save(date_key, gid, {"game": gid, "date": date_key, "solved": solved,
                             "evidence": evidence,
                             "video": str(final) if final else None})
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
    return {"game": gid, "solved": solved,
            "video": str(final) if final else None,
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


def _waffle_facts_stat(evidence):
    """Pure: waffle-appropriate breakdown-card stat from the
    solver evidence ("11 swaps, greens 7/21->21/21 (...)").
    Returns ("11", "swaps used") — the metric that belongs on a
    swap puzzle's card — or None when the evidence carries no
    swap count (the card then drops the stat rather than
    printing a meaningless "0 green rows"; ANALYSIS18 §2j).
    Never invents a number: no match -> None."""
    m = re.search(r"(\d+)\s+swaps", str(evidence or ""))
    if not m:
        return None
    return (m.group(1), "swaps used")


def _solver_typeerror_is_signature_mismatch(exc, solver):
    """Pure: is this TypeError a signature/argument mismatch for
    the solver call itself (iter-24, ANALYSIS21 RC1)? Only then
    may run_one retry the solver without `gid`. An inner
    TypeError (e.g. Capital's None-unpack, "cannot unpack
    non-iterable NoneType object") must NOT match: the message
    must name a signature-mismatch shape AND (the solver's own
    name or 'gid') — an inner call's mismatch naming some
    other function does not qualify."""
    msg = str(exc)
    sig = bool(re.search(
        r"missing .* required positional argument"
        r"|takes .* positional argument.*but .* (?:was|were) given"
        r"|got an unexpected keyword argument"
        r"|got multiple values for argument",
        msg))
    names_solver = bool(
        getattr(solver, "__name__", "")
        and getattr(solver, "__name__") in msg)
    return bool(sig and (names_solver or "'gid'" in msg))


def run_one(gid):
    g = GAMES[gid]
    tgt = A.target_date()
    date_key = tgt.isoformat()
    _CURRENT_RUN.update({"gid": gid, "date_key": date_key, "solved": False,
                         "evidence": "", "stage": "answer"})
    today = tgt.strftime("%B %d, %Y")
    short = tgt.strftime("%b %d")
    print(f"[{gid}] {g['name']} target {date_key}")
    _RUN_T0[gid] = time.time()
    _FIRST_TYPE.pop(gid, None)
    _FIRST_TYPE_REC.pop(gid, None)
    _FIRST_ACTION_LABEL.pop(gid, None)
    _REC_T0.pop(gid, None)
    _SOLVE_SPAN.pop(gid, None)
    _BATTERUP_CDN.pop(gid, None)
    _TYPE_WINDOWS.pop(gid, None)
    _PLAYED.pop(gid, None)
    _REJECTED.pop(gid, None)
    _dbg(gid, f"START env tz={os.environ.get('BROWSER_TZ')} "
              f"tzoff={os.environ.get('TZ_OFFSET_MINUTES')} "
              f"fake={os.environ.get('FAKE_DATE_ISO', '') or 'auto'} "
              f"headless={HEADLESS} url={g['url']}")
    # Fix-loop iter 12: write the preliminary result.json BEFORE any
    # work that can crash (answer fetch, browser launch, solver). The
    # old code wrote it only after the solver returned, so a crash in
    # browser/context setup left no result.json at all — the SIGTERM
    # handler covers kills, but nothing covered a plain crash here.
    # Placed after the per-run state pops above so the preliminary
    # record never carries a previous run's guesses.
    _write_preliminary_result(gid, date_key, False, "started")
    if gid == "nerdle" or g.get("external") == "nerdle_solver.py":
        return run_nerdle_external(g, tgt, date_key, today, short)
    # No-duplicate guard: skip if database/{date}/{game} already solved.
    done = _db_already_done(date_key, gid)
    if done:
        print(f"[{gid}] already done for {date_key}, skipping (no duplicate)")
        _dbg(gid, f"SKIP already done for {date_key} (no duplicate); no fresh video in this run")
        _write_skip_result(gid, date_key, done)
        return {"game": gid, "solved": True, "video": done.get("video"),
                "evidence": "skipped: database already solved"}
    key, solver = SOLVERS[gid]
    if g.get("all_modes"):
        return run_framed_all(gid, g, tgt, date_key, today, short)
    try:
        ans = getattr(A, key)(tgt)
    except SystemExit as e:
        # Iteration 2: marveldle is NOT special here. The old fallback set
        # ans={"answer": None} and "browser solved" the site's board — but
        # when the pick-id gate fires, the site's board IS the previous
        # day's puzzle, so browser-solve played yesterday's game. Defer
        # immediately like every other game instead.
        _dbg(gid, f"QC FAIL: answer unavailable: {e}")
        _write_preliminary_result(gid, date_key, False,
                                  f"answer unavailable: {e}")
        _db_save(date_key, gid, {"game": gid, "date": date_key,
                                 "solved": False,
                                 "evidence": f"answer unavailable: {e}",
                                 "video": None})
        return {"game": gid, "solved": False, "video": None,
                "evidence": f"answer unavailable: {e}"}
    aval = ans.get("answer") or ans.get("name") or str(ans.get("colors"))
    if gid == "marveldle" and not ans.get("answer"):
        aval = None
    if gid == "batterup" and isinstance(ans, dict):
        # Stash the CDN archive entry so _s_batterup can log the CDN
        # game_date next to the site's displayed header date.
        _BATTERUP_CDN[gid] = dict(ans.get("player") or {})
    if gid == "marveldle" and isinstance(ans, dict) and ans.get("pick_id"):
        # Gate PASS in the artifact debug log, with the pick-id value
        # (fix-loop iter 4): a pass must say so, not just avoid an error.
        _dbg(gid, f"pick-id gate PASS: pick-id={ans.get('pick_id')!r}")
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
        _sstate = HERE / "browser_state.json"
        _state_kwargs = {"storage_state": str(_sstate)} if _sstate.exists() else {}
        # Record at native 1080p so the final video is real FHD, not an upscale.
        _vw, _vh = 1920, 1080   # uniform: chunked composite handles memory
        _page_tz = os.environ.get("BROWSER_TZ", "Pacific/Kiritimati")
        if gid == "globle":
            # Fix-loop iter 9: the page sends day=<faked target> +
            # timeZone=<context tz>, and the server only serves
            # day == real-today-in-timeZone. Kiritimati fails whenever
            # real UTC is in [10:00, 24:00) because real Kiritimati has
            # already rolled to the NEXT day (rerun #8: the page's own
            # answer?day=2026-10-17&timeZone=Kiritimati 404'd at 11:47
            # UTC = 01:47 Oct 18 real Kiritimati, so the guesser input
            # never mounted). Iter 7's multi-tz fallback fixed only OUR
            # direct fetch, not the page's own request. The page tz
            # must be a zone whose REAL local date == the target date
            # (see A._globle_page_tz); the faked clock still pins the
            # page's calendar day to the target.
            _page_tz = A._globle_page_tz(tgt, _page_tz)
            _dbg(gid, f"globle page tz={_page_tz} (env BROWSER_TZ="
                      f"{os.environ.get('BROWSER_TZ')}; server requires "
                      "day == real-today-in-timeZone)")
        ctx = b.new_context(record_video_dir=str(vdir),
                            record_video_size={"width": _vw, "height": _vh},
                            viewport={"width": _vw, "height": _vh},
                            timezone_id=_page_tz,
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
        # Pre-navigation storage purge (kills the on-camera reload) and the
        # continuous consent/tooltip watcher (kills the mid-video CMP banner).
        try:
            ctx.add_init_script(script=QP.prepurge_js())
            ctx.add_init_script(script=QP.consent_watch_js())
            ctx.add_init_script(script=QP.adhide_js())
        except Exception:
            pass
        # Phoodle defensive un-hide (fix-loop iter 3): clear <html>
        # display:none at document start, before recording paints.
        if gid == "phoodle":
            try:
                ctx.add_init_script(script=_PHOODLE_UNHIDE_JS)
            except Exception:
                pass

        def _route(route):
            try:
                u = route.request.url
                # Slow ad hosts aborted: countryle's boot stalled on 504s
                # from inmobi/aroa.io (plus the already-blocked scorecard).
                if any(x in u for x in ("user-sync", "usync", "setuid", "criteo", "quantserve", "scorecardresearch", "hotjar", "amplitude", "sentry.io", "tiktok", "facebook.net", "connect.facebook", "clarity.ms", "/ads/index", "ads/index.", "inmobi", "aroa.io")):
                    return route.abort()
            except Exception:
                pass
            return route.continue_()
        ctx.route("**/*", _route)
        pg = ctx.new_page()
        # Recording starts at page creation: the autocrop anchor is measured
        # against THIS clock, not run START (see _REC_T0).
        _REC_T0[gid] = time.time()
        # Per-guess logging: _play_guesses logs with page._qp_gid, which was
        # only set inside _s_wordle — every other game's per-guess lines
        # went to videos/wordle-like/ or were lost. Set it at page creation.
        try:
            pg._qp_gid = gid
        except Exception:
            pass

        def _kill_popups(popup):
            # ad popunders steal window focus mid-game (betweenle lost every
            # keystroke after guess 2 to one); close them on birth. NEVER the
            # main page: ctx 'page' also fires for ctx.new_page() itself and
            # closing it aborted the first goto (ERR_ABORTED trio).
            try:
                if popup._impl_obj is pg._impl_obj:
                    return
            except Exception:
                if popup is pg:
                    return
            try:
                _dbg(gid, f"popup closed: {(popup.url or '')[:60]}")
                popup.close(run_before_unload=False)
            except Exception:
                pass
        ctx.on("page", _kill_popups)
        _instrument_mouse(pg)
        pg.on("dialog", _auto_dismiss_dialog(gid))
        try:
            pg.on("framenavigated", _log_nav(gid))
        except Exception:
            pass
        # Attach the daily-puzzle response probe BEFORE the first navigation, so
        # the site's own answer request is captured on the initial page load.
        _install_live_day_probe(pg)

        _unpublished = {"flag": False}

        def _log_fail(resp):
            try:
                if resp.status >= 400:
                    _dbg(gid, f"HTTP {resp.status} {resp.url[:110]}")
                if resp.status == 404:
                    u = resp.url
                    host_ok = False
                    try:
                        host_ok = (u.split("//")[1].split("/")[0]
                                   == (g["url"].split("//")[1].split("/")[0]))
                    except Exception:
                        pass
                    if host_ok and any(k in u.lower() for k in
                                       ("day=", "answer", "daily", "puzzle",
                                        "game")):
                        _unpublished["flag"] = True
            except Exception:
                pass

        def _log_reqfail(req):
            try:
                _dbg(gid, f"REQFAIL {req.failure or '?'} {req.url[:110]}")
            except Exception:
                pass

        try:
            pg.on("response", _log_fail)
            pg.on("requestfailed", _log_reqfail)
        except Exception:
            pass
        solved, evidence = False, "exception before solve"
        _defer_stale = False
        try:
            global _LAST_BOARD_SHOT; _LAST_BOARD_SHOT = None
        except Exception:
            pass
        try:
            pg.goto(g["url"], wait_until="domcontentloaded", timeout=45000)
            # Startup (fix-loop iter 3): NO fixed 6 s sleep. Phoodle gets
            # its <html> un-hide first (a hidden root hides the board the
            # ready-wait looks for), then wait for the game's own
            # input/board selector (bounded, fail-fast), then ONE modal
            # sweep — solver _close_modals calls no-op via the per-page
            # dedup guard instead of re-sweeping.
            if gid == "phoodle":
                _phoodle_unhide(pg, gid)
            _wait_ready(pg, gid)
            if gid == "phoodle":
                _phoodle_unhide(pg, gid)
            _close_modals(pg)
            # The site's own answer request has now been captured, so we know
            # which day the board really is. If that differs from the day the
            # local engine answered for, the whole guess sequence targets the
            # wrong puzzle and the game is unwinnable - so re-resolve first.
            try:
                if GAMES[gid].get("no_align"):
                    # Client-seeded board: the fake clock IS the day key. The
                    # response probe otherwise sees the CDN fetch of YESTERDAY's
                    # file (previous-game widget) and defers a perfectly
                    # winnable run.
                    fixed, _served = ans, None
                else:
                    fixed, _served = _align_answer_to_live_day(gid, ans, pg)
                if fixed is not ans:
                    ans = fixed
                    aval = ans.get("answer") or ans.get("name") \
                        or str(ans.get("colors"))
                if _served and str(_served) != date_key:
                    from datetime import date as _d2
                    _sv = _d2(*(int(x) for x in str(_served).split("-")))
                    if _sv == tgt - timedelta(days=1):
                        # Server-keyed site has not rolled to the publish day
                        # yet (US rollover happens hours after 21:00 IST).
                        # Recording now burns yesterday's board on camera -
                        # defer to the dawn batch instead of faking it.
                        _defer_stale = True
                        evidence = (f"stale-board: site served {_sv} but "
                                    f"publish day is {tgt}; deferred to dawn")
                        print(f"[{gid}] {evidence}")
                    else:
                        date_key = _sv.isoformat()
                        today = _sv.strftime("%B %d, %Y")
                        short = _sv.strftime("%b %d")
                        print(f"[{gid}] RE-KEYED video to served day "
                              f"{date_key}")
            except Exception as _e:
                # Iter-17: NEVER swallow the live-day align silently
                # — a bare `except: pass` here hid _align failures
                # (including a missing decision line) for three runs.
                _dbg(gid, f"live-day align failed: {_e}")
            aval_s = str(aval)
            if _defer_stale:
                solved = False
            else:
                try:
                    # colour games need their full answer dict, not the string
                    if gid in ("colordle", "colorfle"):
                        solved, evidence = solver(pg, ans)
                    else:
                        solved, evidence = solver(pg, aval_s, gid)
                except TypeError as _te:
                    # Iter-24 RC1: this fallback exists ONLY for
                    # solvers whose signature takes (pg, aval) with
                    # no gid. The old bare `except TypeError`
                    # caught a TypeError raised INSIDE the solver
                    # (e.g. Capital's None-unpack) and re-raised
                    # the misleading "missing 'gid'" error. Retry
                    # without gid ONLY when the error is a
                    # signature mismatch for THIS solver call;
                    # otherwise re-raise the real traceback so
                    # the crash is diagnosed, not masked.
                    if _solver_typeerror_is_signature_mismatch(
                            _te, solver):
                        _dbg(gid, "solver signature mismatch "
                                  f"({str(_te)[:120]}); retrying "
                                  "without gid")
                        solved, evidence = solver(pg, aval_s)
                    else:
                        raise
                if not solved and not _defer_stale:
                    try:
                        # Shell-vs-iframe: countryle's shell body is EMPTY
                        # (0 chars) while the app iframe holds the game
                        # text — reading only the shell rebooted a healthy
                        # page. Take the max over the page + all frames.
                        _ptl = 0
                        for _fr in pg.frames:
                            try:
                                _ptl = max(_ptl, len(
                                    (_fr.evaluate(
                                        "() => document.body.innerText || ''")
                                     or "")))
                            except Exception:
                                continue
                    except Exception:
                        _ptl = 999
                    if _ptl < 80:      # Flutter/app shell never booted
                        _dbg(gid, f"page text only {_ptl} chars - rebooting "
                                  f"page and replaying solver once")
                        try:
                            pg.reload(wait_until="domcontentloaded")
                            # No fixed 9 s sleep: ready-wait + fresh modal
                            # sweep (reset the dedup guard — modals return
                            # after a reload).
                            if gid == "phoodle":
                                _phoodle_unhide(pg, gid)
                            _wait_ready(pg, gid)
                            try:
                                pg._qp_modals_closed = False
                            except Exception:
                                pass
                            _close_modals(pg, _force=True)
                            if gid in ("colordle", "colorfle"):
                                solved, evidence = solver(pg, ans)
                            else:
                                solved, evidence = solver(pg, aval_s, gid)
                            evidence = f"{evidence} (after reboot retry)"
                        except Exception as _re2:
                            _dbg(gid, f"reboot retry failed: {str(_re2)[:90]}")
                if solved and gid == "marveldle" and _FOUND_ANSWER.get(gid):
                    aval = _FOUND_ANSWER[gid]
                    aval_s = str(aval)
                    ans = {"answer": aval, "via": "browser-solve"}
                if not solved and _unpublished["flag"]:
                    # The site's own puzzle endpoint 404s for the target day:
                    # the board is not published yet (globle/batterup class).
                    # No retry can win - defer to the game's own cron window.
                    _defer_stale = True
                    evidence = (f"unpublished-board: site 404s for "
                                f"{date_key}; deferred to its cron window")
                    _dbg(gid, evidence)
                if not solved and not _defer_stale:
                    # FIX-LOOP: day-lag retry REMOVED. Never solve the
                    # previous-day board and re-key it as today (that
                    # produced marveldle_final_2026-10-16 labelled as
                    # 2026-10-17). If the target board is not winnable,
                    # leave solved=False and defer - do not guess yesterday.
                    _dbg(gid, "no win; day-lag retry removed - will NOT solve previous-day board")
        except SystemExit as _se:
            evidence = f"SystemExit in solver: {_se}"
            _dbg(gid, evidence)
        except BaseException as e:
            import traceback as _tb
            evidence = f"exception: {str(e)[:200]}"
            _dbg(gid, f"CRASH in solve: {_tb.format_exc()[-800:]}")
        if gid == "phoodle":
            # Un-hide before the post-solve screenshot: a re-hidden <html>
            # would make shot_win (and the QC visible-content check) blank.
            _phoodle_unhide(pg, gid)
        # Let the final render land before the win screenshot: batterup's
        # rerun-#3 shot_win showed the PRE-submit board because the capture
        # raced the submit render. For batterup, wait for the post-submit
        # board (filled guess rows) first.
        try:
            pg.wait_for_timeout(1200)
        except Exception:
            pass
        if gid == "batterup":
            try:
                _attr_wait_post_submit(pg, gid)
                # Dismiss the lingering suggestion dropdown so the win
                # screenshot shows the filled guess rows, not the open
                # dropdown over empty rows (reruns #5–#8 shot_win).
                # Escape alone never closed it (input still held text
                # + focus), so blur + clear the input (see
                # _attr_dismiss_suggestion) instead of bare Escape.
                _attr_dismiss_suggestion(pg)
                _pr = _attr_post_submit_probe(pg)
                _dbg(gid, f"pre-shot_win probe: rows={len(_pr.get('rows') or [])} "
                          f"populated={_pr.get('populated')} "
                          f"populated_rows={_pr.get('populated_rows')} "
                          f"feedback_tiles={_pr.get('feedback_tiles')} "
                          f"dropdown_open={_pr.get('dropdown_open')}")
            except Exception:
                pass
        # Solve span (START->shot_win) for the autocrop/QC coverage checks.
        try:
            _SOLVE_SPAN[gid] = round(time.time() - _RUN_T0[gid], 1)
            _dbg(gid, f"solve span START->shot_win {_SOLVE_SPAN[gid]}s")
        except Exception:
            pass
        try:
            pg.screenshot(path=str(vdir / "shot_win.png"))
        except Exception:
            pass
        # Iteration 2 (phoodle): the rerun #1 gameplay recording was a blank
        # white screen while solved=True + QC PASS. Check the post-solve
        # screenshot for near-blank content so a blank capture is visible in
        # the log instead of silently shipping. This does NOT fail the run —
        # the screenshot and the recording can legitimately differ — but a
        # WARNING here plus shot_win.png in the artifact lets the next
        # analysis compare screenshot vs recording directly.
        try:
            from PIL import Image as _PILImage2
            import numpy as _np2
            _shot = _PILImage2.open(vdir / "shot_win.png").convert("L")
            _arr = _np2.array(_shot, dtype=float)
            _mean, _std = float(_arr.mean()), float(_arr.std())
            _dbg(gid, f"shot_win brightness mean={_mean:.1f} std={_std:.1f}")
            if _mean > 245 and _std < 12:
                _dbg(gid, "WARNING: shot_win near-blank (mean>245, std<12); "
                          "gameplay recording may also be blank (phoodle class)")
        except Exception as _e2:
            _dbg(gid, f"shot_win blank check skipped: {str(_e2)[:80]}")
        try:
            vpath = pg.video.path()
        except Exception:
            vpath = None
        try:
            ctx.storage_state(path=str(HERE / "browser_state.json"))
            print(f"[{gid}] browser_state.json saved")
        except Exception as _e:
            print(f"[{gid}] state save failed: {str(_e)[:120]}")
        ctx.close()
        b.close()

    if str(evidence).startswith("defer:"):
        _defer_stale = True
    try:
        (vdir / f"telemetry_{date_key}.json").write_text(
            json.dumps(QP.ev_all(), indent=1), encoding="utf-8")
    except Exception:
        pass
    # Preliminary result.json IMMEDIATELY after the solver returns
    # (fix-loop iter 11): polish + QC + assembly must never be a
    # precondition for an auditable result. Overwritten by the final
    # record below; the SIGTERM handler reuses this state.
    _CURRENT_RUN.update({"solved": bool(solved),
                         "evidence": str(evidence)[:500],
                         "stage": "polish"})
    _write_preliminary_result(gid, date_key, solved, evidence,
                              answer=(aval if solved else None))
    if _defer_stale:
        _report_game(gid, date_key, answer=str(aval), guesses=[],
                     verify=evidence, video=None, upload="deferred-dawn")
        return {"game": gid, "solved": False, "video": None,
                "evidence": evidence}
    print(f"[{gid}] solved={solved} evidence={evidence}")
    _CURRENT_RUN.update({"stage": "polish"})
    final = vpath
    if not solved:
        # Iteration 2: do not assemble a publishable final for an unsolved
        # game (the marveldle/waffle class built 100-200s videos for
        # failures, with "Full solve" chapter titles). Keep a FAST
        # remux/copy of the raw recording for diagnosis (no re-encode,
        # no autocrop) — the old full-polish re-encode is where the
        # SIGTERM tail stall lives. Report video=None.
        _dbg(gid, "skipping final assembly: not solved (no publishable video)")
        try:
            _dbg(gid, "unsolved: fast-copy gameplay (no re-encode)")
            _fast_copy_gameplay(str(final), vdir / "gameplay_1080.mp4")
        except Exception:
            pass
        final = None
        # Iter-19 (ANALYSIS16 §6 item 15): no publishable video, so
        # no chapters — [[0, 'The solve']] claimed a chapter for a
        # final that does not exist (Worldle unsolved). Omit, never
        # invent placeholder chapters for unsolved games.
        chapters = []
    # Quordle-parity assembly:
    #   recap(5s) + hints(10s) + gameplay + reveal(8s) + facts(8s) + teaser(5s)
    #   + uniform music + SRT captions + real chapter list
    elif final and HAS and HAS_MOVIEPY and HAS_PIL:
        try:
            # Chapters/slides from PLAYED guesses when recorded, else plan.
            steps = _result_steps(gid, aval)
            nguess = len(steps)
            recap_p = vdir / "recap.png"
            hints_p = vdir / "hints.png"
            reveal_p = vdir / "reveal.png"
            facts_p = vdir / "facts.png"
            teaser_p = vdir / "teaser.png"
            DP.generate_recap(str(recap_p), g["name"], today)
            _kind = "colors" if gid in ("colorfle", "colordle") else None
            _pal = None
            try:
                if gid == "colorfle" and isinstance(ans, dict):
                    _pal = ans.get("colorHexes")
                elif gid == "colordle" and isinstance(ans, dict) and ans.get("hex"):
                    _pal = [ans.get("hex")]
            except Exception:
                _pal = None
            if gid == "waffle":
                try:
                    _winfo = A.waffle(A.target_date())
                    _ww = [str(w) for w in (_winfo.get("words") or []) if w]
                    _hints = ([f"{len(_ww)} words hidden in the grid"]
                              + [f"Find {_w[:12]}" for _w in _ww[:2]])
                except Exception:
                    _hints = ["Swap tiles to solve", "Greens stay put"]
            elif gid == "batterup":
                try:
                    _bp = ans.get("player") if isinstance(ans, dict) else {}
                    _hints = [f"Team: {_bp.get('team_name') or 'MLB roster'}",
                              f"Position: {', '.join(_bp.get('position') or []) or 'columns narrow it'}",
                              f"Born: {_bp.get('born') or 'silhouette swings first'}"]
                except Exception:
                    _hints = ["Guess the player", "Columns narrow it down"]
            elif gid == "marveldle":
                _hints = ["Comics hero or villain", "Check gender, type, era",
                          "Columns light up as you narrow it"]
            elif gid in ("colorfle", "colordle"):
                _n = len(_pal or []) or 3
                _hints = ([f"{len((ans or {}).get('colors') or aval.strip('[]').split(','))} "
                           f"colours from a {_n}-colour palette",
                           "Order matters - pie split shows positions",
                           "Greens lock, yellows move slot"]
                          if gid == "colorfle" else
                          ["One secret colour name today",
                           "Similarity % narrows the hue",
                           "Green row = exact colour"])
            else:
                _hints = S.compute_hints(str(aval))
            DP.generate_hints(str(hints_p), g["name"], today,
                              _hints, str(aval), kind=_kind, palette=_pal)
            if gid == "waffle" and _LAST_BOARD_SHOT and Path(_LAST_BOARD_SHOT).exists():
                _bb = Image.open(_LAST_BOARD_SHOT).convert("RGB")
                _bg = Image.new("RGB", (1920, 1080), (13, 20, 38))
                _bgy = 240
                _tw = int(_bgy * _bb.width / _bb.height)
                _bb = _bb.resize((_tw, _bgy), Image.LANCZOS)
                _bg.paste(_bb, ((1920 - _tw) // 2, (1080 - _bgy) // 2))
                _draw = ImageDraw.Draw(_bg)
                _fd = DP._fonts()
                _draw.text((80, 80), f"{g['name'].upper()} — TODAY'S SOLVED BOARD",
                            fill=(46, 204, 113), font=_fd["m"])
                _draw.text((80, 160), today, fill=(200, 210, 230),
                           font=_fd["b"])
                _bg.save(str(reveal_p), "PNG", optimize=True)
                _dbg(gid, f"reveal slide uses board screenshot {_LAST_BOARD_SHOT}")
            else:
                DP.generate_reveal(str(reveal_p), g["name"], today, str(aval),
                                   steps, kind=_kind, palette=_pal,
                                   scoring=gid not in _NO_LETTER_SCORING)
            DP.generate_facts(str(facts_p), g["name"], today, str(aval),
                                steps, kind=_kind, palette=_pal,
                                scoring=gid not in _NO_LETTER_SCORING,
                                # Iter-21 (ANALYSIS18 §2j): the
                                # Waffle card read "0 green rows" —
                                # a meaningless generic metric for a
                                # swap puzzle. Pass the
                                # waffle-appropriate stat (swaps
                                # used, parsed from the solver
                                # evidence); the card drops the
                                # stat if the parse fails.
                                extra_stat=(
                                    _waffle_facts_stat(evidence)
                                    if gid == "waffle" else None))
            DP.generate_teaser(str(teaser_p), g["name"], today, g["slug"])

            def _still(p, secs):
                return (ImageClip(str(p)).set_duration(secs).set_fps(24)
                        .resize(width=1920, height=1080))

            # Gameplay is a raw screen recording. Scale it to FILL the frame
            # (never pillarbox) and add a slow push-in so the board is readable
            # and the footage feels produced rather than screen-captured.
            play_src = _polish_gameplay(str(final), vdir / "gameplay_1080.mp4", gid=gid)
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
                        _dbg(gid, f"music {_sp.name} on main body "
                                   f"({clip.duration:.1f}s)")
                        break
                    except Exception:
                        continue
            # NO shared brand intro: wordsolverx-intro-20s.mp4
            # opens EVERY video with a wrong-game, 12-day-stale Wordle
            # answer (MOCHA, Oct 5). The per-game dated recap card
            # opens the video instead (fix-loop iter 11).
            _intro_dur = 0.0
            # Worldle all-modes: replace generic chapters with one chapter
            # per played mode (gameplay-relative segments recorded by the
            # solver) + Summary. Generic chapters would hide the modes.
            if gid == "worldle" and _WORLDLE_SEGMENTS:
                try:
                    chapters = _s_worldle_final_chapters(_intro_dur, gd)
                    _dbg(gid, f"worldle chapters wired: n={len(chapters)} "
                              f"modes={len(_WORLDLE_SEGMENTS)}")
                except Exception as e:
                    _dbg(gid, f"worldle chapters wiring failed: {str(e)[:120]}")
            out = str(vdir / f"{gid}_final_{date_key}.mp4")
            clip.write_videofile(out, codec="libx264", audio_codec="aac",
                                 fps=24, verbose=False, logger=None)
            final = out
            try:
                _ok_ch = _embed_chapters(out, chapters)
                _dbg(gid, f"chapters embedded={_ok_ch} n={len(chapters)}")
            except Exception:
                pass
            srt = DP.build_captions_srt(chapters, total, today, g["name"],
                                         out_path=vdir / "captions.srt")
        except Exception as e:
            print(f"[{gid}] assembly fallback raw: {str(e)[:200]}")
            chapters = [(0, "The solve")]
    else:
        chapters = [(0, "The solve")]

    # upload (skips without creds; never upload an unsolved game)
    nguess = len(_result_steps(gid, aval))
    if solved and final and HAS:
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
    # Visible-content QC (fix-loop iter 3): a blank video must NEVER QC
    # PASS, even when metadata (solved/res/duration) is fine (phoodle).
    _dbg(gid, "QC start")
    _CURRENT_RUN.update({"stage": "qc"})
    _vis_ok, _vis_detail = _qc_visible_content(
        gid, vdir, gameplay_path=(vdir / "gameplay_1080.mp4")
        if (vdir / "gameplay_1080.mp4").exists() else None,
        solve_span_s=_SOLVE_SPAN.get(gid),
        gameplay_phase_s=_gameplay_phase_s(_SOLVE_SPAN.get(gid),
                                           _FIRST_TYPE.get(gid)))
    if not _vis_ok:
        _dbg(gid, f"QC visible-content FAIL: {_vis_detail}")
    _qc = {"game": gid, "date": date_key, "solved": bool(solved),
           "evidence": str(evidence)[:300],
           "video": str(final) if final else None,
           "probe": _probe_video(final) if final else None,
           "cursor_events": len(QP.ev_all()),
           "cursor_clips": (QP.LAST_POLISH or {}).get("clips", 0),
           "frame_mode": (QP.LAST_POLISH or {}).get("mode"),
           "guesses": len(_result_steps(gid, aval)),
           "visible_content": {"ok": _vis_ok, "detail": _vis_detail}}
    _pr = _qc["probe"] or {}
    _qc["ok"] = bool(solved and final and str(final).endswith(".mp4") and _pr.get("ok")
                     and _pr.get("duration_s", 0) >= 40
                     and _pr.get("width") == 1920 and _pr.get("height") == 1080
                     and _vis_ok)
    # Fix-loop: QC PASS must NOT print for solved=False. solved is part of ok.
    _dbg(gid, f"QC {'PASS' if _qc['ok'] else 'FAIL'}: mp4={_qc['video']} "
              f"size={_pr.get('size_mb')}MB res={_pr.get('width')}x{_pr.get('height')} "
              f"dur={_pr.get('duration_s')}s cursor_events={_qc['cursor_events']} "
              f"cursor_clips={(QP.LAST_POLISH or {}).get('clips', 0)} "
              f"frame={(QP.LAST_POLISH or {}).get('mode', '?')} "
              f"solved={solved} guesses={_qc['guesses']} "
              f"visible_content={_vis_detail}")
    try:
        (vdir / f"qc_{date_key}.json").write_text(
            json.dumps(_qc, indent=1), encoding="utf-8")
    except Exception:
        pass
    (vdir / "result.json").write_text(json.dumps(
        {"game": gid, "date": date_key, "solved": solved, "evidence": evidence,
         # Verified winning word when solved (iter-12 (e)): a solved
         # record must never carry answer=None (run 39043936480).
         "answer": (str(aval) if solved else None),
         "video": str(final) if final else None,
         # Betweenle: submitted-only guesses, never the plan fallback
         # (plan fallback fabricated 6 "played" words, real = 3).
         "guesses": ([str(x) for x in _PLAYED[gid]] if _PLAYED.get(gid)
                     else ([] if gid == "betweenle"
                           else _guesses(gid, aval))),
         "rejected": _REJECTED.get(gid, []),
         "steps": _result_steps(gid, aval), "chapters": chapters, "qc": _qc,
         "modes": ([dict(r) for r in _WORLDLE_MODES]
                   if gid == "worldle" else [])}, indent=1))
    _dbg(gid, "result.json written (final)")
    _CURRENT_RUN.update({"stage": "report"})
    _db_save(date_key, gid, {"game": gid, "date": date_key, "solved": solved,
                             "evidence": evidence,
                             "video": str(final) if final else None,
                             "youtube_url": vid if 'vid' in dir() else None})
    _report_game(gid, date_key, answer=f"{aval} ({ans.get('via', '?')})",
                 guesses=([str(x) for x in _PLAYED[gid]] if _PLAYED.get(gid)
                          else ([] if gid == "betweenle"
                                else _guesses(gid, aval))),
                 rejected=_REJECTED.get(gid, []),
                 reloads=_RELOADS.get(gid, 0),
                 verify=f"solved={solved} evidence={evidence}",
                 video=_probe_video(final) if final else None,
                 chapters=[t for _, t in chapters],
                 upload=(vid if 'vid' in dir() and vid else
                         "skipped-no-creds"),
                 extra={"slides": "recap+hints+reveal+facts+teaser+intro"})
    _dbg(gid, "report done")
    return {"game": gid, "solved": solved, "video": str(final) if final else None,
            "evidence": evidence, "guesses": nguess}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default=None)
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    _install_signal_handlers()
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
    _close_dbg_handles()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
