"""Quordle parity helpers — Wordle-level features for quordle-video.

Covers: hints, analysis slides (definition/frequency/facts), recap/teaser,
SEO title/tags, captions SRT, playlist/pin/thumbnail helpers,
uniform full-video music, fake-JS-date init script (time-travel trick).
Pillow-only for images; moviepy optional for assembly (ffmpeg fallback).
"""
import os
import random

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:
    Image = ImageDraw = ImageFont = None

SLIDE_W, SLIDE_H = 1920, 1080
BG_TOP = (13, 20, 38)
BG_BOT = (30, 45, 80)
CARD = (23, 34, 58)
CARD_BORDER = (52, 68, 100)
TEXT = (255, 255, 255)
MUTED = (168, 180, 200)
GREEN = (46, 204, 113)
YELLOW = (255, 205, 0)
GRAY = (110, 118, 132)

SCRABBLE_SCORES = {
    'a': 1, 'b': 3, 'c': 3, 'd': 2, 'e': 1, 'f': 4, 'g': 2, 'h': 4,
    'i': 1, 'j': 8, 'k': 5, 'l': 1, 'm': 3, 'n': 1, 'o': 1, 'p': 3,
    'q': 10, 'r': 1, 's': 1, 't': 1, 'u': 1, 'v': 4, 'w': 4, 'x': 8,
    'y': 4, 'z': 10,
}

ENGLISH_FREQ = {
    'e': (12.7, 1), 't': (9.1, 2), 'a': (8.2, 3), 'o': (7.5, 4),
    'i': (7.0, 5), 'n': (6.7, 6), 's': (6.3, 7), 'h': (6.1, 8),
    'r': (6.0, 9), 'd': (4.3, 10), 'l': (4.0, 11), 'c': (2.8, 12),
    'u': (2.8, 13), 'm': (2.4, 14), 'w': (2.4, 15), 'f': (2.2, 16),
    'g': (2.0, 17), 'y': (2.0, 18), 'p': (1.9, 19), 'b': (1.5, 20),
    'v': (1.0, 21), 'k': (0.8, 22), 'j': (0.15, 23), 'x': (0.15, 24),
    'q': (0.10, 25), 'z': (0.07, 26),
}


def format_chapter_timestamp(seconds):
    try:
        total = max(0, int(round(seconds)))
        return f"{total // 60}:{total % 60:02d}"
    except Exception:
        return "0:00"


def compute_hints_for_word(solution):
    """3 progressive hints for one 5-letter word (Wordle parity)."""
    try:
        if not solution or len(solution) != 5:
            return []
        s = solution.upper()
        vowels = sum(1 for c in s if c in "AEIOU")
        unique = len(set(s))
        return [
            f"Hint 1: {vowels} vowel(s)",
            f"Hint 2: Starts with '{s[0]}'",
            f"Hint 3: {'All unique' if unique == 5 else f'{5-unique} repeat'}",
        ]
    except Exception:
        return []


def _load_fonts():
    pairs = [
        ("C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/segoeui.ttf"),
        ("C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/arial.ttf"),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    bold_path = regular_path = None
    for b, r in pairs:
        if os.path.exists(b):
            bold_path = b
            regular_path = r if (r and os.path.exists(r)) else b
            break
    if not bold_path or ImageFont is None:
        d = ImageFont.load_default() if ImageFont else None
        return {k: d for k in ('big', 'title', 'medium', 'bold', 'body', 'small', 'regular', 'tile')}
    return {
        'big': ImageFont.truetype(bold_path, 110),
        'tile': ImageFont.truetype(bold_path, 90),
        'title': ImageFont.truetype(bold_path, 70),
        'medium': ImageFont.truetype(bold_path, 50),
        'bold': ImageFont.truetype(bold_path, 40),
        'body': ImageFont.truetype(regular_path, 40),
        'small': ImageFont.truetype(regular_path, 30),
        'regular': ImageFont.truetype(regular_path, 36),
    }


def _slide_base(kicker, meta_text):
    img = Image.new("RGB", (SLIDE_W, SLIDE_H), BG_TOP)
    draw = ImageDraw.Draw(img)
    for y in range(SLIDE_H):
        t = y / SLIDE_H
        r = int(BG_TOP[0] + (BG_BOT[0] - BG_TOP[0]) * t)
        g = int(BG_TOP[1] + (BG_BOT[1] - BG_TOP[1]) * t)
        b = int(BG_TOP[2] + (BG_BOT[2] - BG_TOP[2]) * t)
        draw.line([(0, y), (SLIDE_W, y)], fill=(r, g, b))
    fonts = _load_fonts()
    draw.rectangle([0, 0, 14, SLIDE_H], fill=GREEN)
    draw.text((70, 52), kicker.upper(), fill=GREEN, font=fonts['medium'])
    if meta_text:
        try:
            mw = draw.textlength(meta_text, font=fonts['small'])
            draw.text((SLIDE_W - 70 - mw, 66), meta_text, fill=MUTED, font=fonts['small'])
        except Exception:
            pass
    return img, draw, fonts, 200


def _fit_font(font, txt, draw, max_w):
    """Shrink a TrueType font until txt fits max_w (never crops text)."""
    try:
        f = font
        for _ in range(24):
            if draw.textlength(txt, font=f) <= max_w:
                return f
            ns = max(32, int(getattr(f, "size", 70) * 0.92) - 2)
            if ns >= getattr(f, "size", 0):
                break
            f = ImageFont.truetype(f.path, ns)
        return f
    except Exception:
        return font


def _center_text(draw, y, txt, font, fill, max_w=1780):
    """Center txt horizontally; shrink to fit max_w so it never leaves the frame."""
    f = _fit_font(font, txt, draw, max_w)
    try:
        w = draw.textlength(txt, font=f)
    except Exception:
        w = 0
    draw.text(((SLIDE_W - w) / 2, y), txt, fill=fill, font=f)
    return f


def _truncate_to_width(draw, txt, font, max_w):
    """Cut txt with an ellipsis until it measures <= max_w."""
    t = txt
    while t and draw.textlength(t + "...", font=font) > max_w:
        t = t[:-4].rstrip(" ,;:-")
    return (t + "...") if t != txt else txt


def generate_mode_card(out_path, game, mode_name, subtitle=""):
    """The "NOW PLAYING: <mode>" intro card, shared by every pipeline.

    Multi-mode videos used to run their rounds back to back with no visual
    break, so a viewer landing mid-video could not tell which round was on
    screen. This card is spliced in before each mode's footage to name it, and
    is also used as the opening card of single-mode videos.

    Written against the shared _slide_base/_fit_font/_center_text helpers so
    Quordle, Nerdle and Wordle all render the identical card.
    """
    img, draw, fonts, _y = _slide_base(f"{game} mode", "")
    safe_w = SLIDE_W - 200
    draw.text((70, 150), "NOW PLAYING", fill=MUTED, font=fonts["medium"])

    name = str(mode_name).upper()
    mf = _fit_font(fonts["title"], name, draw, safe_w)
    _center_text(draw, 330, name, mf, GREEN)

    # Rule under the name, sized to the text it belongs to.
    try:
        nw = draw.textlength(name, font=mf)
    except Exception:
        nw = 0
    half = min(int(nw / 2) + 60, safe_w // 2)
    y = 330 + int(getattr(mf, "size", 70) * 1.15) + 26
    draw.rectangle([SLIDE_W // 2 - half, y, SLIDE_W // 2 + half, y + 4],
                   fill=GREEN)
    if subtitle:
        _center_text(draw, y + 60, str(subtitle), fonts["medium"], MUTED)
    img.save(out_path, "PNG", optimize=True)
    return out_path


def generate_quordle_hints_image(out_path, date_str, classic_words):
    """4-column hints card: one 3-hint column per Classic answer."""
    if Image is None:
        return False
    try:
        W, H = 1920, 1080
        img = Image.new("RGB", (W, H), BG_TOP)
        draw = ImageDraw.Draw(img)
        fonts = _load_fonts()
        draw.text((W // 2 - 450, 70), "HINTS — ALL 4 CLASSIC WORDS", fill=GREEN, font=fonts['title'])
        draw.text((W // 2 - 120, 160), date_str, fill=TEXT, font=fonts['small'])
        words = (classic_words or [])[:4]
        card_w, card_h = 420, 560
        gap = 40
        total_w = card_w * 4 + gap * 3
        start_x = (W - total_w) // 2
        card_y = 260
        for i, w in enumerate(words):
            x = start_x + i * (card_w + gap)
            draw.rectangle([x, card_y, x + card_w, card_y + card_h], fill=CARD)
            draw.text((x + 30, card_y + 20), f"WORD {i+1}", fill=YELLOW, font=fonts['bold'])
            draw.text((x + 30, card_y + 80), "XXXXX", fill=MUTED, font=fonts['medium'])
            hints = compute_hints_for_word(w)
            ty = card_y + 180
            for h in hints:
                draw.text((x + 30, ty), h[:28], fill=TEXT, font=fonts['body'])
                ty += 60
            # curiosity: reveal first letter
            if w:
                draw.text((x + 30, ty + 20), f"Starts: {w[0].upper()} _ _ _ _", fill=GREEN, font=fonts['bold'])
        draw.text((W // 2 - 350, H - 90), "Pause & guess before the solve!", fill=YELLOW, font=fonts['small'])
        img.save(out_path, "PNG", optimize=True)
        return True
    except Exception as e:
        print(f"[quordle-hints] failed: {e}")
        return False


def get_letter_frequency_info(words):
    try:
        letters = sorted(set("".join(w.lower() for w in (words or []) if w)))
        if not letters:
            return {}
        avg = sum(ENGLISH_FREQ.get(c, (0, 0))[0] for c in letters) / len(letters)
        tier = "very common" if avg > 7.0 else ("common" if avg > 4.0 else ("moderately rare" if avg > 2.0 else "rare"))
        return {"letters": letters, "avg_freq": round(avg, 1), "tier": tier}
    except Exception:
        return {}


def generate_quordle_definition_slide(out_path, date_str, classic_words, analysis):
    if Image is None:
        return False
    try:
        img, draw, fonts, y = _slide_base("Quordle Analysis — Definitions", date_str)
        words = (classic_words or [])[:4]
        draw.text((70, y), "TODAY'S 4 ANSWERS", fill=TEXT, font=fonts['title'])
        y += 120
        for w in words:
            wu = (w or "").upper()
            a = (analysis or {}).get(wu, {})
            defn = a.get("definition", "") if isinstance(a, dict) else ""
            pos = a.get("part_of_speech", "") if isinstance(a, dict) else ""
            card_h = 150
            draw.rounded_rectangle([70, y, SLIDE_W - 70, y + card_h], radius=20, fill=CARD, outline=CARD_BORDER, width=2)
            chip = _truncate_to_width(draw, (pos or "word").upper()[:16], fonts['bold'], 170)
            draw.rounded_rectangle([100, y + 24, 100 + 220, y + 82], radius=12, fill=GREEN)
            draw.text((132, y + 32), chip, fill=(10, 20, 14), font=fonts['bold'])
            draw.text((340, y + 32), wu, fill=TEXT, font=fonts['bold'])
            if defn:
                draw.text((100, y + 92), _truncate_to_width(draw, defn[:160], fonts['regular'], 1740),
                          fill=TEXT, font=fonts['regular'])
            y += card_h + 20
        img.save(out_path, "PNG", optimize=True)
        return True
    except Exception as e:
        print(f"[quordle-def] failed: {e}")
        return False


def generate_quordle_frequency_slide(out_path, date_str, classic_words, letter_freq):
    if Image is None:
        return False
    try:
        img, draw, fonts, y = _slide_base("Letter Frequency Analysis", date_str)
        draw.text((70, y), "How common are today's letters?", fill=TEXT, font=fonts['title'])
        y += 120
        letters = sorted(set("".join(w.lower() for w in (classic_words or []) if w)),
                         key=lambda c: -ENGLISH_FREQ.get(c, (0, 27))[0])[:8]
        max_freq = 12.7
        bar_max_w = SLIDE_W - 700
        for letter in letters:
            freq, rank = ENGLISH_FREQ.get(letter, (0.05, 27))
            draw.rounded_rectangle([70, y, 142, y + 72], radius=14, fill=GREEN if freq >= 4.0 else GRAY)
            draw.text((106, y + 36), letter.upper(), fill=(10, 20, 14), font=fonts['bold'], anchor="mm")
            bar_w = max(24, int(bar_max_w * (freq / max_freq)))
            draw.rounded_rectangle([170, y + 12, 170 + bar_max_w, y + 60], radius=10, fill=(30, 42, 66))
            draw.rounded_rectangle([170, y + 12, 170 + bar_w, y + 60], radius=10, fill=GREEN)
            draw.text((190 + bar_max_w, y + 36), f"{freq:.1f}%", fill=TEXT, font=fonts['bold'])
            draw.text((190 + bar_max_w + 150, y + 36), f"#{rank}", fill=MUTED, font=fonts['small'])
            y += 92
        tier = (letter_freq or {}).get("tier", "common")
        draw.text((70, SLIDE_H - 120), f"rarity: {tier} | {(len(letters))} unique letters", fill=YELLOW, font=fonts['bold'])
        img.save(out_path, "PNG", optimize=True)
        return True
    except Exception as e:
        print(f"[quordle-freq] failed: {e}")
        return False


def generate_quordle_facts_slide(out_path, date_str, classic_words):
    if Image is None:
        return False
    try:
        img, draw, fonts, y = _slide_base("Quordle Facts & Solve Path", date_str)
        words = [w.lower() for w in (classic_words or []) if w]
        scrabble = sum(SCRABBLE_SCORES.get(c, 0) for w in words for c in w)
        vowels = sum(1 for w in words for c in w if c in "aeiou")
        facts = [
            ("WORDS", f"{len(words)} solved"),
            ("LETTERS", f"{len(words)*5} total"),
            ("VOWELS", f"{vowels}"),
            ("SCRABBLE", f"{scrabble} pts"),
            ("MODES", "6/6 solved"),
            ("STREAK", "keep it alive!"),
        ]
        cx, cy = 70, y + 40
        for k, v in facts:
            draw.rounded_rectangle([cx, cy, cx + 520, cy + 140], radius=16, fill=CARD, outline=CARD_BORDER, width=2)
            draw.text((cx + 24, cy + 16), k, fill=GREEN, font=fonts['bold'])
            draw.text((cx + 24, cy + 70), v, fill=TEXT, font=fonts['title'])
            cx += 560
            if cx + 520 > SLIDE_W - 70:
                cx = 70
                cy += 170
        # solve path chips: the 4 answers
        py = cy + 190
        draw.text((70, py), "SOLVE PATH:", fill=YELLOW, font=fonts['bold'])
        py += 70
        xx = 70
        for w in words:
            wu = w.upper()
            tw = 220
            draw.rounded_rectangle([xx, py, xx + tw, py + 70], radius=12, fill=CARD, outline=GREEN, width=3)
            draw.text((xx + tw // 2, py + 35), wu, fill=TEXT, font=fonts['bold'], anchor="mm")
            xx += tw + 20
        img.save(out_path, "PNG", optimize=True)
        return True
    except Exception as e:
        print(f"[quordle-facts] failed: {e}")
        return False


def generate_quordle_recap_image(out_path, ytd_info):
    if Image is None:
        return False
    y = (ytd_info or {}).get("yesterday", {})
    classic = y.get("classic") or []
    if not classic:
        # No yesterday data: skip the segment entirely (never render placeholders)
        return False
    try:
        W, H = 1920, 1080
        img = Image.new("RGB", (W, H), BG_TOP)
        draw = ImageDraw.Draw(img)
        fonts = _load_fonts()
        _center_text(draw, 200, "YESTERDAY'S QUORDLE", fonts['title'], GREEN, max_w=1780)
        _center_text(draw, 400, ", ".join(w.upper() for w in classic[:4]), fonts['tile'], GREEN, max_w=1780)
        _center_text(draw, 600, str(y.get("date", "")), fonts['small'], TEXT, max_w=1780)
        _center_text(draw, 800, "Did you keep your streak?", fonts['body'], YELLOW, max_w=1780)
        img.save(out_path, "PNG", optimize=True)
        return True
    except Exception:
        return False


def generate_quordle_teaser_image(out_path, ytd_info):
    if Image is None:
        return False
    try:
        W, H = 1920, 1080
        img = Image.new("RGB", (W, H), BG_TOP)
        draw = ImageDraw.Draw(img)
        fonts = _load_fonts()
        draw.text((W // 2 - 350, 200), "TOMORROW'S TEASER", fill=GREEN, font=fonts['title'])
        t = (ytd_info or {}).get("tomorrow", {})
        classic = t.get("classic") or []
        if not (classic and classic[0]):
            # No tomorrow data: skip the segment entirely (never render placeholders)
            return False
        draw.text((W // 2 - 60, 450), classic[0][0].upper(), fill=GREEN, font=fonts['big'])
        draw.text((W // 2 - 300, 650), "_ _ _ _ _  x4", fill=TEXT, font=fonts['tile'])
        draw.text((W // 2 - 350, 820), "First letters revealed — subscribe!", fill=YELLOW, font=fonts['body'])
        img.save(out_path, "PNG", optimize=True)
        return True
    except Exception:
        return False


def build_optimized_title(date_short, day_num):
    variants = [
        f"Quordle Answer Today ({date_short}) - All 6 Modes Solved!",
        f"Quordle Answer Today - {date_short} Classic/Chill/Extreme Solved",
        f"Quordle Answers Today - {date_short} Full Solve + Hints",
    ]
    return variants[day_num % len(variants)]


def build_optimized_tags(date_short, date_num):
    return [
        'quordle answer today', 'Quordle', 'Quordle Answer',
        f'quordle answer today {date_num}', f'Quordle {date_short}',
        'Quordle Classic', 'Quordle Chill', 'Quordle Extreme',
        'Quordle Sequence', 'Quordle Rescue', 'Quordle Weekly',
        'Quordle Hints', 'Quordle Solver', 'Wordle', 'Daily Puzzle',
    ]


def build_captions_srt(chapters, total_duration, date_str):
    try:
        lines = []
        for i, (sec, label) in enumerate(chapters):
            start = int(sec)
            end = int(chapters[i+1][0]) if i + 1 < len(chapters) else int(total_duration)
            def _ts(s):
                return f"{s//3600:02d}:{(s%3600)//60:02d}:{s%60:02d},000"
            lines.append(str(i+1))
            lines.append(f"{_ts(start)} --> {_ts(end)}")
            lines.append(f"Quordle {date_str} - {label}")
            lines.append("")
        return "\n".join(lines)
    except Exception:
        return ""


def get_fake_date_init_script(target_iso):
    """Playwright add_init_script: freeze Date to target (time-travel trick).

    target_iso like '2026-09-27T00:00:00'. Shifts Date.now/new Date() so
    date-seeded games (quordle-likes, nerdle, etc.) serve NEXT day early.
    Keeps performance.now real to avoid breaking animations.
    """
    return f"""
(() => {{
  const SHIFT = new Date('{target_iso}').getTime() - Date.now();
  const RealDate = Date;
  function FakeDate(...args) {{
    if (args.length === 0) return new RealDate(RealDate.now() + SHIFT);
    return new RealDate(...args);
  }}
  FakeDate.now = () => RealDate.now() + SHIFT;
  FakeDate.parse = RealDate.parse;
  FakeDate.UTC = RealDate.UTC;
  FakeDate.prototype = RealDate.prototype;
  window.__TIME_SHIFT_MS = SHIFT;
  try {{ Object.defineProperty(window, 'Date', {{ value: FakeDate, configurable: true }}); }} catch(e) {{ window.Date = FakeDate; }}
}})();
"""


def apply_uniform_music_ffmpeg(video_path, script_dir, out_path=None):
    """One consistent song over full video (ffmpeg fallback, no moviepy)."""
    import subprocess
    from pathlib import Path
    try:
        sd = Path(script_dir)
        songs = [f for f in os.listdir(sd) if f.endswith('.mp3') and f.startswith('song')]
        if not songs:
            return video_path
        song = sd / random.choice(songs)
        out = out_path or str(Path(video_path).with_name(Path(video_path).stem + "_music.mp4"))
        cmd = ["ffmpeg", "-y", "-i", str(video_path), "-stream_loop", "3", "-i", str(song),
               "-shortest", "-map", "0:v", "-map", "1:a",
               "-c:v", "copy", "-c:a", "aac", "-shortest", out]
        subprocess.run(cmd, check=False, capture_output=True)
        if os.path.exists(out) and os.path.getsize(out) > 0:
            return out
        return video_path
    except Exception as e:
        print(f"[music] uniform mix failed: {e}")
        return video_path


# ============ input telemetry + Recordly-grade cursor overlay (2026-10-09) ====
# Replaces the old post-hoc overlay (64x84 PIL arrow at click points, 0.35 s
# linear glides, 96 px rings). Design follows Recordly's published parameters:
#   * real input-event bus (we drive the mouse ourselves, so every position is
#     known at call time - no native sampler needed, unlike Recordly's C++
#     cursor-monitor which exists only because it records a HUMAN's pointer),
#   * damped-spring smoothing with Recordly's getCursorSpringConfig(0)
#     constants k=1000, c=100, m=1 (motionSmoothing.ts),
#   * native-size Windows-style arrow (≈25 px tall at 1080p, hotspot at tip),
#   * small expanding click ripple + 90 ms press squash,
#   * exact hotspot anchoring (no magic -10,-4 offset).
import time as _qp_time
from pathlib import Path as _QPPath

_EV = {"t0": None, "events": []}


def ev_reset():
    """Start a new telemetry track (call right before the first navigation)."""
    _EV["t0"] = _qp_time.time()
    _EV["events"] = []


def ev_push(x, y, kind="click"):
    """Record one input event. kinds: click|move|drag|focus|key."""
    if _EV["t0"] is None:
        ev_reset()
    _EV["events"].append({"t": round(_qp_time.time() - _EV["t0"], 3),
                          "x": float(x), "y": float(y), "kind": kind})


def ev_all():
    return list(_EV["events"])


def prepurge_js():
    """document-start storage purge: kills the on-camera reload that
    _reset_site_state used to trigger (committed browser_state.json means
    persisted state now exists on every run)."""
    return """
(() => {
  // ONE-SHOT per context: clearing on EVERY navigation put wafflegame into a
  // redirect loop (its email-skip flag lives in localStorage) and wiped
  // mid-game state on reloads. A cookie flag survives navigations.
  try {
    if (document.cookie.indexOf('_qp_purged=1') >= 0) return;
    document.cookie = '_qp_purged=1;path=/;max-age=3600';
  } catch (e) {}
  try { localStorage.clear(); } catch (e) {}
  try { sessionStorage.clear(); } catch (e) {}
  try {
    if (window.indexedDB && indexedDB.databases) {
      indexedDB.databases().then(ds => ds.forEach(d => {
        try { indexedDB.deleteDatabase(d.name); } catch (e) {}
      })).catch(() => {});
    }
  } catch (e) {}
  try {
    if (window.caches && caches.keys) {
      caches.keys().then(ks => ks.forEach(k => caches.delete(k))).catch(() => {});
    }
  } catch (e) {}
})();
"""


_CONSENT_TXT = ("'ok','okay','accept','accept all','accept all cookies',"
                "'reject all','got it','agree','i agree','allow all',"
                "'no thanks','dismiss','x disable hints','disable hints',"
                "'continue','all rules'")


def consent_watch_js():
    """Continuous consent/tooltip watcher. The old one-shot sweep lost the
    race against async CMPs (TrustArc) and delayed hint tooltips, which is why
    the nerdle video shows Settings/OK + the hint bubble at 1:25. Every
    auto-click is recorded so the cursor overlay shows it."""
    return """
(() => {
  if (window.__CONSENT_WATCH) return;
  window.__CONSENT_WATCH = 1;
  window.__CONSENT_CLICKS = [];
  const TXT = new Set([%s]);
  const done = new WeakSet();
  const inOverlay = (el) => {
    try {
      if (el.closest('[class*=consent i],[class*=cookie i],[class*=modal i],' +
          '[class*=dialog i],[class*=tooltip i],[class*=hint i],[role=dialog],' +
          '[id*=truste],[class*=fc-]')) return true;
      let n = el;
      for (let i = 0; i < 4 && n; i++) {
        const cs = getComputedStyle(n);
        if (cs && (cs.position === 'fixed' || cs.position === 'sticky')) return true;
        n = n.parentElement;
      }
    } catch (e) {}
    return false;
  };
  const sweep = () => {
    const roots = [document];
    try {
      for (const f of Array.from(document.querySelectorAll('iframe'))) {
        const d = f.contentDocument;
        if (d) roots.push(d);
      }
    } catch (e) {}
    for (const root of roots) {
      let els = [];
      try {
        els = Array.from(root.querySelectorAll(
          'button, a[role=button], [role=button], #truste-consent-button, .fc-cta-consent, [aria-label=dismiss]'));
      } catch (e) { continue; }
      for (const el of els) {
        if (done.has(el)) continue;
        let r = null;
        try { r = el.getBoundingClientRect(); } catch (e) {}
        if (!r || r.width < 8 || r.height < 8) continue;
        const t = ((el.innerText || el.getAttribute('aria-label') || '')
                   .trim().toLowerCase());
        if (!t || t.length > 30 || !TXT.has(t)) continue;
        if (!inOverlay(el)) continue;   // never click game buttons
        done.add(el);
        try {
          window.__CONSENT_CLICKS.push(
            [Date.now(), r.x + r.width / 2, r.y + r.height / 2]);
          el.click();
        } catch (e) {}
      }
    }
  };
  sweep();
  const iv = setInterval(sweep, 800);
  setTimeout(() => clearInterval(iv), 20000);
  try {
    new MutationObserver(() => sweep()).observe(
      document.documentElement, {childList: true, subtree: true});
  } catch (e) {}
})();
""" % _CONSENT_TXT


def adhide_js():
    """Visual cleanup: collapse ad slots / sticky promo bars so blocked ads
    never leave black holes or banner strips inside the gameplay frame."""
    return """
(() => {
  const css = document.createElement('style');
  css.textContent = `
    iframe[src*="intergient"], iframe[src*="pageos"], iframe[src*="doubleclick"],
    iframe[src*="googlesyndication"], iframe[src*="btloader"], iframe[src*="criteo"],
    iframe[src*="facebook.com"], iframe[src*="recaptcha"],
    div[id*="google_ads"], div[id*="div-gpt-ad"], .ad-container, .adsbygoogle,
    .ads, [class*="ad-slot"], [class*="adspace"], [id*="ad-slot"],
    [class*="sponsored"], .donate-banner, [class*="banner-ad"],
    ins, [id*="rail" i], [class*="rail" i], [id*="admantle" i],
    [class*="admantle" i], [id*="trendi" i], [class*="pubexchange"],
    [class*="outbrain"], [id*="google_ads_iframe"], [class*="banner" i] {
      display: none !important; visibility: hidden !important;
      width: 0 !important; height: 0 !important;
    }`;
  // hide every THIRD-PARTY iframe (ad slots, sync pixels, promo strips) and
  // collapse its wrapper so no black holes / banner strips ride in frame.
  // same-origin iframes (countryle's app frame etc.) are never touched.
  const kill3p = () => {
    try {
      for (const f of Array.from(document.querySelectorAll('iframe'))) {
        let u = '';
        try { u = f.src || ''; } catch (e) {}
        if (!u || u.startsWith('about:') || u.startsWith('data:')) continue;
        let host = '';
        try { host = new URL(u, location.href).host; } catch (e) { continue; }
        if (host === location.host) continue;
        // display:none iframes KEEP document focus - every physical
        // keystroke would die inside them (betweenle lost guesses 3+
        // exactly this way). Blur and then remove them from the DOM.
        try { f.contentWindow && f.contentWindow.blur && f.contentWindow.blur(); } catch (e) {}
        f.style.setProperty('display', 'none', 'important');
        f.setAttribute('data-qpx', '1');
        let p = f.parentElement;
        for (let i = 0; i < 2 && p && p !== document.body; i++) {
          const kids = Array.from(p.children).filter(c => {
            const r = c.getBoundingClientRect();
            return r.width > 4 && r.height > 4 &&
                   getComputedStyle(c).display !== 'none';
          });
          if (kids.length === 0) {
            p.style.setProperty('display', 'none', 'important');
            p = p.parentElement;
          } else break;
        }
      }
    } catch (e) {}
  };
  const killShellBanners = () => {
    try {
      for (const el of Array.from(document.body.children)) {
        const r = el.getBoundingClientRect();
        if (r.height < 8 || r.height > 170 || r.width < 500) continue;
        const has3p = Array.from(el.querySelectorAll('img, iframe, script'))
          .some(m => { try { return new URL(m.src || m.href || '', location.href).host !== location.host; } catch (e) { return false; } });
        const isGame = el.querySelector('canvas, #menupanel, game-app, input');
        if (has3p && !isGame) el.style.setProperty('display', 'none', 'important');
      }
    } catch (e) {}
  };
  const reap = () => {
    try {
      for (const f of Array.from(document.querySelectorAll('iframe[data-qpx]')))
        f.remove();
    } catch (e) {}
  };
  setInterval(kill3p, 1200);
  setInterval(reap, 2600);
  setInterval(killShellBanners, 1500);
  document.addEventListener('DOMContentLoaded', kill3p);
  const put = () => { if (!document.head) return;
    if (!document.querySelector('style[data-qpadhide]')) {
      css.setAttribute('data-qpadhide', '1');
      document.head.appendChild(css); } };
  put();
  document.addEventListener('DOMContentLoaded', put);
  setInterval(put, 1500);
})();
"""


def drain_consent_clicks(page):
    """Merge watcher auto-clicks into the telemetry track (best effort)."""
    try:
        hits = page.evaluate(
            "() => { const a = window.__CONSENT_CLICKS || [];"
            " window.__CONSENT_CLICKS = []; return a; }") or []
        for _ms, _x, _y in hits:
            ev_push(_x, _y, "click")
        return len(hits)
    except Exception:
        return 0


_QP_ASSET_CACHE = {}
LAST_POLISH = {}   # introspection for the QC/debug line


def _cursor_assets():
    """Native-size Windows-style arrow + click ripple, drawn once per run."""
    if _QP_ASSET_CACHE:
        return _QP_ASSET_CACHE["cur"], _QP_ASSET_CACHE["ring"], _QP_ASSET_CACHE["hot"]
    import tempfile
    from PIL import Image, ImageDraw, ImageFilter
    tmp = _QPPath(tempfile.mkdtemp(prefix="qpcursor_"))
    # Classic Windows arrow geometry, scaled to ~25 px tall (native at 1080p).
    S = 1.7
    poly = [(0, 0), (0, 13.4), (3.7, 10.2), (5.9, 15.3), (8.0, 14.4),
            (5.8, 9.4), (10.2, 9.4)]
    poly = [(4 + x * S, 4 + y * S) for x, y in poly]
    W = H = 40
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(sh).polygon([(x + 1.2, y + 1.6) for x, y in poly],
                               fill=(0, 0, 0, 90))
    sh = sh.filter(ImageFilter.GaussianBlur(1.1))
    img = Image.alpha_composite(img, sh)
    d = ImageDraw.Draw(img)
    d.polygon(poly, fill=(252, 252, 252, 255), outline=(24, 24, 28, 255))
    d.line(poly + [poly[0]], fill=(24, 24, 28, 255), width=2)
    cur_p = tmp / "cursor_win.png"
    img.save(cur_p)
    ring = Image.new("RGBA", (56, 56), (0, 0, 0, 0))
    ImageDraw.Draw(ring).ellipse([12, 12, 44, 44], outline=(255, 255, 255, 175),
                                 width=3)
    ring_p = tmp / "ring.png"
    ring.save(ring_p)
    _QP_ASSET_CACHE.update(cur=str(cur_p), ring=str(ring_p), hot=(4.0, 4.0))
    return _QP_ASSET_CACHE["cur"], _QP_ASSET_CACHE["ring"], _QP_ASSET_CACHE["hot"]


def _densify(events, duration):
    """Sparse input events -> a human-alive pointer track.

    The old overlay only knew click points, so the arrow sat frozen for tens
    of seconds (your 'cursor is not even moving'). A real recording never
    holds still: we synthesise what a hand does between the known events -
    curved glides into every target, sub-pixel fidget while holding, and an
    ambient drift across long idle gaps. Deterministic per run (date-seeded
    upstream); the spring then smooths it exactly like Recordly.
    """
    import math
    import random as _r
    evs = [e for e in sorted(events, key=lambda e: e["t"])
           if e["kind"] in ("move", "click", "drag", "focus")]
    if not evs:
        return []
    rnd = _r.Random(int(evs[0]["x"] + evs[0]["t"] * 977) & 0xFFFFFFFF)
    out = [dict(evs[0], kind="move")]
    for a, b in zip(evs, evs[1:]):
        gap = b["t"] - a["t"]
        dx, dy = b["x"] - a["x"], b["y"] - a["y"]
        dist = math.hypot(dx, dy)
        # 1) curved glide into b, ending just before b's timestamp
        if dist > 4:
            glide = min(0.7, 0.22 + dist / 2200.0)
            n = max(4, min(16, int(dist / 55)))
            mx = (a["x"] + b["x"]) / 2 - dy * 0.14
            my = (a["y"] + b["y"]) / 2 + dx * 0.14
            t0 = max(a["t"], b["t"] - glide)
            for i in range(1, n + 1):
                u = i / (n + 1.0)
                w = 4 * u * (1 - u)          # quadratic bezier weight
                out.append({"t": t0 + (b["t"] - t0) * u,
                            "x": a["x"] + dx * u + (mx - a["x"] - dx / 2) * w,
                            "y": a["y"] + dy * u + (my - a["y"] - dy / 2) * w,
                            "kind": "move"})
        # 2) fidget while holding, and ambient drift over long idle gaps
        hold = (t0 if dist > 4 else b["t"]) - a["t"]
        if hold > 1.4:
            steps = int(hold / 0.45)
            cx, cy = a["x"], a["y"]
            for i in range(1, steps + 1):
                tt = a["t"] + i * (hold / (steps + 1.0))
                if gap > 5.0 and i == steps // 2:     # one ambient sweep
                    cx += rnd.uniform(-140, 140)
                    cy += rnd.uniform(-90, 90)
                else:
                    cx += rnd.uniform(-2.5, 2.5)
                    cy += rnd.uniform(-2.0, 2.0)
                out.append({"t": tt, "x": cx, "y": cy, "kind": "move"})
        out.append(b)
    # tail fidget until the clip ends so the cursor never freezes on screen
    last = out[-1]
    tail = duration - last["t"]
    cx, cy = last["x"], last["y"]
    for i in range(1, int(tail / 0.5) + 1):
        cx += rnd.uniform(-2.5, 2.5)
        cy += rnd.uniform(-2.0, 2.0)
        out.append({"t": last["t"] + i * 0.5, "x": cx, "y": cy, "kind": "move"})
    out.sort(key=lambda e: e["t"])
    return out


def _spring_path(events, duration, fps=24.0, k=1000.0, c=100.0, m=1.0):
    """Recordly-style damped spring (k/c/m = getCursorSpringConfig(0)) via
    semi-implicit Euler at 240 Hz. Target = last known input position."""
    pos = _densify(events, duration)
    if not pos or duration <= 0:
        return []
    sx, sy = pos[0]["x"], pos[0]["y"]
    vx = vy = 0.0
    sub = 8
    dt = 1.0 / (fps * sub)
    out = []
    idx = 0
    n = int(duration * fps) + 1
    for f in range(n):
        ft = f / fps
        while idx < len(pos) and pos[idx]["t"] <= ft:
            idx += 1
        tgt = pos[idx - 1] if idx else pos[0]
        tx, ty = tgt["x"], tgt["y"]
        for _ in range(sub):
            vx += ((k * (tx - sx) - c * vx) / m) * dt
            sx += vx * dt
            vy += ((k * (ty - sy) - c * vy) / m) * dt
            sy += vy * dt
        out.append((sx, sy))
    return out


def _ring_frames(n=8, max_r=26):
    """Pre-rendered growing click ripple (moviepy 1.0.3 cannot resize by
    callable, so the growth is baked into an 8-frame RGBA sequence)."""
    import numpy as np
    from PIL import Image, ImageDraw
    frames = []
    for i in range(n):
        f = i / max(1, n - 1)
        r = int(8 + (max_r - 8) * f)
        a = int(170 * (1 - f))
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        ImageDraw.Draw(img).ellipse([32 - r, 32 - r, 32 + r, 32 + r],
                                    outline=(255, 255, 255, a), width=3)
        frames.append(np.array(img))
    return frames


def _cursor_clips(base_clip, events, ox=0.0, oy=0.0, scx=1.0, scy=1.0):
    """moviepy clips: spring-smoothed native cursor + ripple per click."""
    from moviepy.editor import ImageClip, ImageSequenceClip
    cur_p, _ring_p, (hx, hy) = _cursor_assets()
    evs = [dict(e, x=ox + e["x"] * scx, y=oy + e["y"] * scy)
           for e in sorted(events, key=lambda e: e["t"])]
    path = _spring_path(evs, base_clip.duration)
    if not path:
        return []
    clicks = [e for e in evs if e["kind"] == "click"][:80]

    def _pos(t):
        i = max(0, min(int(t * 24.0), len(path) - 1))
        return (path[i][0] - hx, path[i][1] - hy)

    dot = (ImageClip(cur_p, transparent=True)
           .set_duration(base_clip.duration).set_start(0)
           .set_position(_pos))
    clips = [dot]
    rframes = _ring_frames()
    for e in clicks:
        try:
            rc = (ImageSequenceClip(rframes, fps=24, with_mask=True)
                  .set_start(e["t"]).set_duration(0.30)
                  .set_position((e["x"] - 32, e["y"] - 32)))
            clips.append(rc)
        except Exception:
            continue
    return clips


def _probe_duration(src):
    """ffprobe duration in seconds (0.0 on failure)."""
    try:
        import subprocess as _sp
        _r = _sp.run(["ffprobe", "-v", "error", "-show_entries",
                      "format=duration", "-of", "csv=p=0", str(src)],
                     capture_output=True, text=True, timeout=60)
        return max(0.0, float((_r.stdout or "0").strip() or 0))
    except Exception:
        return 0.0


def _sample_frame_diffs(src, fps=2.0, width=160):
    """Mean-abs-diff between consecutive sampled frames.

    Decodes to small grayscale frames via ffmpeg and returns
    (diffs, fps) where diffs[i] is the mean abs diff between frame i and
    frame i+1. Empty list on any failure (caller treats as 'unknown').
    """
    try:
        import subprocess as _sp
        cmd = ["ffmpeg", "-v", "error", "-i", str(src), "-vf",
               f"fps={fps},scale={width}:-2,format=gray",
               "-f", "rawvideo", "-pix_fmt", "gray", "-"]
        p = _sp.run(cmd, capture_output=True, timeout=1800)
        raw = p.stdout or b""
        if not raw:
            return [], fps
        # Frame size: width x round(width * H / W) — recover from ffprobe.
        try:
            _r = _sp.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "stream=width,height",
                          "-of", "csv=p=0", str(src)],
                         capture_output=True, text=True, timeout=60)
            _w0, _h0 = [int(v) for v in _r.stdout.strip().split(",")[:2]]
            height = max(1, round(width * _h0 / float(_w0)))
        except Exception:
            height = 90
        fsize = width * height
        if fsize <= 0 or len(raw) < fsize * 2:
            return [], fps
        n = len(raw) // fsize
        frames = [raw[i * fsize:(i + 1) * fsize] for i in range(n)]
        diffs = []
        for a, b in zip(frames, frames[1:]):
            tot = 0
            for x, y in zip(a, b):
                tot += abs(x - y)
            diffs.append(tot / float(fsize))
        return diffs, fps
    except Exception:
        return [], fps


# Autocrop thresholds (mean abs diff on 160px-wide gray frames, same scale
# as the frozen_frac < 1.0 rule used in video analysis).
_AUTOCROP_FROZEN = 1.0    # below this a frame pair counts as static
_AUTOCROP_CHANGE = 2.5    # above this a frame pair counts as a real change
_AUTOCROP_MIN_STATIC_S = 30.0  # only cut static runs longer than this
_AUTOCROP_KEEP_STATIC_HEAD_S = 2.0  # keep this much of a cut static run
_AUTOCROP_XFADE_S = 0.5   # crossfade between kept segments (video only)


def _autocrop_keep_segments(src, first_typing_s=None, idle_cut_s=None,
                             solve_span_s=None, first_action_label=None):
    """Keep-segments (seconds, in source timeline) after autocrop.

    Leading idle: cut everything before the first player action minus a ~1 s
    safety margin. The cut point comes from `idle_cut_s` when given, else
    `first_typing_s - 1.0` when given, else frame-diff detection (first
    SUSTAINED + PERSISTENT board change — a change that holds for several
    frames and leaves the frame different from before, not page-load
    animation flicker). `first_typing_s` is a FIRST-ACTION anchor on the
    RECORDING timeline (the runner passes the recording-relative time of the
    first keystroke / swap / colour pick, labelled by `first_action_label`):
    for non-typing games (waffle drag-swaps, colorfle clicks) it is the first
    board-state change, NOT a keystroke — the old typing-only trigger had no
    drag/swap path, so waffle's frame-diff fallback keyed on the final scroll
    and cut a 59 s solve to 3.75 s. Mid-recording static waits >30 s are cut
    to a 2 s head.

    Coverage guard: when `solve_span_s` (START->shot_win) is known, the kept
    footage must cover at least 50% of the solve span. If the leading cut
    and/or static cuts would drop below that, the leading cut is clamped and
    static cuts are dropped until the floor holds (a 3.75 s clip for a 59 s
    solve must never survive). Returns (segments, detail); segments == []
    means nothing should be cut (caller keeps src as-is).
    """
    detail = {"leading_cut_s": None, "leading_cut_source": None,
              "static_cuts": [], "kept_s": 0.0, "duration_s": 0.0,
              "solve_span_s": (float(solve_span_s) if solve_span_s else None),
              "coverage_ratio": None, "guard_clamped": False,
              "first_action_label": first_action_label,
              "reason_no_cut": None}
    dur = _probe_duration(src)
    detail["duration_s"] = round(dur, 1)
    if dur <= 0:
        detail["reason_no_cut"] = "unprobed duration"
        return [], detail
    if idle_cut_s is not None:
        leading_cut = max(0.0, float(idle_cut_s))
        source = "idle_cut_s"
    elif first_typing_s is not None:
        leading_cut = max(0.0, float(first_typing_s) - 1.0)
        source = f"first_action_s-1s ({first_action_label or 'typing'})"
    else:
        leading_cut = None  # detect below
        source = None
    diffs, fps = _sample_frame_diffs(src)
    if not diffs:
        # No frame evidence: honour an explicit cut only, else cut nothing.
        if leading_cut is None:
            detail["reason_no_cut"] = "no frame evidence and no anchor"
            return [], detail
        if leading_cut < 1.0:
            detail["reason_no_cut"] = "explicit cut <1s"
            return [], detail
        detail["leading_cut_s"] = round(leading_cut, 1)
        detail["leading_cut_source"] = source
        segs = [(leading_cut, dur)]
        return _autocrop_apply_coverage(segs, dur, leading_cut, source,
                                         [], detail, solve_span_s)
    if leading_cut is None:
        # First sustained + persistent change: 3 consecutive diffs above
        # CHANGE and the frame after them still differs from the frame
        # before them (a typed guess persists; load animation flickers).
        leading_cut = 0.0
        source = "frame-diff first sustained change"
        for i in range(len(diffs) - 3):
            if all(d > _AUTOCROP_CHANGE for d in diffs[i:i + 3]):
                # Persistent check needs raw frames; approximate with the
                # following diffs staying non-frozen for one more step.
                if diffs[i + 3] > _AUTOCROP_FROZEN:
                    leading_cut = max(0.0, i / fps - 1.0)
                    break
    detail["leading_cut_s"] = round(leading_cut, 1)
    detail["leading_cut_source"] = source
    # Static runs: maximal runs of frozen diffs longer than MIN_STATIC_S.
    # A run covering diffs [i..j] spans frames i..j+1 -> seconds [i/fps, (j+2)/fps].
    segments = []
    static_cuts = []
    cur_start = leading_cut
    i = 0
    nd = len(diffs)
    while i < nd:
        if diffs[i] <= _AUTOCROP_FROZEN:
            j = i
            while j + 1 < nd and diffs[j + 1] <= _AUTOCROP_FROZEN:
                j += 1
            run_start = i / fps
            run_end = (j + 2) / fps
            if run_end - run_start > _AUTOCROP_MIN_STATIC_S and run_start >= leading_cut - 0.01:
                keep_end = min(run_start + _AUTOCROP_KEEP_STATIC_HEAD_S, run_end)
                if keep_end > cur_start + 0.5:
                    segments.append((cur_start, keep_end))
                static_cuts.append((round(run_start, 1), round(run_end, 1),
                                    f"frozen {run_end - run_start:.1f}s > "
                                    f"{_AUTOCROP_MIN_STATIC_S:.0f}s; kept "
                                    f"{_AUTOCROP_KEEP_STATIC_HEAD_S:.0f}s head"))
                cur_start = run_end
            i = j + 1
        else:
            i += 1
    if dur > cur_start + 0.5:
        segments.append((cur_start, dur))
    # Drop slivers that cannot take a crossfade cleanly; they are <1 s of
    # mostly-static footage between two long static runs.
    segments = [(a, b) for a, b in segments if b - a >= 1.0]
    if not segments:
        detail["reason_no_cut"] = "no keepable segments"
        return [], detail
    removed = dur - sum(b - a for a, b in segments)
    if removed < 1.0:
        detail["reason_no_cut"] = f"removed {removed:.1f}s < 1s"
        return [], detail
    return _autocrop_apply_coverage(segments, dur, leading_cut, source,
                                     static_cuts, detail, solve_span_s)


def _autocrop_apply_coverage(segments, dur, leading_cut, source,
                             static_cuts, detail, solve_span_s):
    """Enforce the >=50%-of-solve-span floor on a candidate segment list.

    (1) Drop static cuts first (keep the whole tail from the leading cut),
    (2) then clamp the leading cut if still below the floor. Mutates
    `detail`; returns (segments, detail)."""
    detail["static_cuts"] = [list(c) for c in static_cuts]
    span = float(solve_span_s) if solve_span_s else 0.0
    kept = sum(b - a for a, b in segments)
    detail["kept_s"] = round(kept, 1)
    if span > 0:
        detail["coverage_ratio"] = round(kept / span, 3) if span else None
        floor = 0.5 * span
        if kept < floor and dur > floor:
            detail["guard_clamped"] = True
            # (1) Static cuts are what shrank the kept set: drop them and
            # keep the whole tail from the leading cut.
            if not (len(segments) == 1 and segments[0] == (leading_cut, dur)):
                segments = [(leading_cut, dur)]
                detail["static_cuts"] = []
                kept = dur - leading_cut
                detail["kept_s"] = round(kept, 1)
                detail["coverage_ratio"] = round(kept / span, 3) if span else None
                detail["leading_cut_source"] = (
                    f"{source} + static cuts DROPPED by coverage guard "
                    f"(kept {kept:.1f}s < 50% of solve span {span:.1f}s)")
            # (2) Still short: clamp the leading cut so the tail meets it.
            if kept < floor:
                new_leading = max(0.0, dur - floor)
                detail["leading_cut_s"] = round(new_leading, 1)
                detail["leading_cut_source"] = (
                    f"{source} CLAMPED by coverage guard "
                    f"(kept {kept:.1f}s < 50% of solve span {span:.1f}s)")
                segments = [(new_leading, dur)]
                kept = dur - new_leading
                detail["kept_s"] = round(kept, 1)
                detail["coverage_ratio"] = round(kept / span, 3) if span else None
    return segments, detail


def _autocrop_render(src, segments, out_path):
    """Render keep-segments to out_path. Video-only xfade when possible
    (no audio + every segment > 1 s); otherwise hard-cut concat for both
    video and audio. Returns True on success."""
    import subprocess as _sp
    if not segments:
        return False
    dur = _probe_duration(src)
    if len(segments) == 1:
        a, b = segments[0]
        if a <= 0.01 and b >= dur - 0.01:
            return False
        cmd = ["ffmpeg", "-y", "-v", "error", "-ss", str(a), "-to", str(b),
               "-i", str(src), "-c:v", "libx264", "-preset", "medium",
               "-crf", "17", "-pix_fmt", "yuv420p", "-r", "24", "-an",
               str(out_path)]
        # Preserve audio on a single-segment cut when the source has it.
        try:
            _r0 = _sp.run(["ffprobe", "-v", "error", "-select_streams", "a",
                           "-show_entries", "stream=index",
                           "-of", "csv=p=0", str(src)],
                          capture_output=True, text=True, timeout=60)
            has_audio = bool((_r0.stdout or "").strip())
        except Exception:
            has_audio = False
        if has_audio:
            cmd = ["ffmpeg", "-y", "-v", "error", "-ss", str(a), "-to", str(b),
                   "-i", str(src), "-c:v", "libx264", "-preset", "medium",
                   "-crf", "17", "-pix_fmt", "yuv420p", "-r", "24",
                   "-c:a", "aac", str(out_path)]
        r = _sp.run(cmd, capture_output=True, text=True, timeout=1800)
        return r.returncode == 0 and _QPPath(out_path).exists() \
            and _QPPath(out_path).stat().st_size > 10000
    # Multi-segment: trim each, then join.
    try:
        _r0 = _sp.run(["ffprobe", "-v", "error", "-select_streams", "a",
                       "-show_entries", "stream=index",
                       "-of", "csv=p=0", str(src)],
                      capture_output=True, text=True, timeout=60)
        has_audio = bool((_r0.stdout or "").strip())
    except Exception:
        has_audio = False
    use_xfade = (not has_audio) and all(b - a > 1.0 for a, b in segments)
    parts = []
    for idx, (a, b) in enumerate(segments):
        parts.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{idx}]")
    filt = ";".join(parts)
    if use_xfade:
        cur = "v0"
        acc = segments[0][1] - segments[0][0]
        for idx in range(1, len(segments)):
            out = f"x{idx}" if idx < len(segments) - 1 else "vout"
            off = max(0.0, acc - _AUTOCROP_XFADE_S)
            filt += f";[{cur}][v{idx}]xfade=transition=fade:duration={_AUTOCROP_XFADE_S}:offset={off}[{out}]"
            cur = out
            acc = acc + (segments[idx][1] - segments[idx][0]) - _AUTOCROP_XFADE_S
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src),
               "-filter_complex", filt, "-map", "[vout]",
               "-c:v", "libx264", "-preset", "medium", "-crf", "17",
               "-pix_fmt", "yuv420p", "-r", "24", "-an", str(out_path)]
    else:
        filt += ";" + "".join(f"[v{i}]" for i in range(len(segments))) \
            + f"concat=n={len(segments)}:v=1:a=0[vout]"
        if has_audio:
            af = ";".join(
                f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[a{i}]"
                for i, (a, b) in enumerate(segments))
            filt += ";" + af + ";" + "".join(f"[a{i}]" for i in range(len(segments))) \
                + f"concat=n={len(segments)}:v=0:a=1[aout]"
            cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src),
                   "-filter_complex", filt, "-map", "[vout]", "-map", "[aout]",
                   "-c:v", "libx264", "-preset", "medium", "-crf", "17",
                   "-pix_fmt", "yuv420p", "-r", "24", "-c:a", "aac", str(out_path)]
        else:
            cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src),
                   "-filter_complex", filt, "-map", "[vout]",
                   "-c:v", "libx264", "-preset", "medium", "-crf", "17",
                   "-pix_fmt", "yuv420p", "-r", "24", "-an", str(out_path)]
    r = _sp.run(cmd, capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        print(f"[autocrop] render failed: {(r.stderr or '')[:240]}")
        return False
    return _QPPath(out_path).exists() and _QPPath(out_path).stat().st_size > 10000


def _apply_autocrop(src, out_path, first_typing_s=None, idle_cut_s=None,
                    solve_span_s=None, first_action_label=None):
    """Autocrop `src` -> `out_path`, returning the path to use downstream
    (`out_path` when a cut was rendered, else `src`). Never raises.

    EVERY cut is logged (leading cut + source, each static cut + reason,
    coverage vs the solve span) and recorded in LAST_POLISH['autocrop'] so
    the runner can copy it into the artifact debug log — the rerun-#3
    truncation was silent in the artifact because only stdout carried it."""
    try:
        if os.environ.get("AUTOCROP", "on").lower() in ("off", "0", "false"):
            print("[autocrop] skipped: AUTOCROP=off")
            LAST_POLISH.update(autocrop={"cut_s": 0.0, "skipped": "AUTOCROP=off"})
            return src
        segments, detail = _autocrop_keep_segments(
            src, first_typing_s, idle_cut_s, solve_span_s, first_action_label)
        if not segments:
            print(f"[autocrop] no cut: {detail.get('reason_no_cut')} "
                  f"(dur={detail.get('duration_s')}s)")
            LAST_POLISH.update(autocrop={**detail, "cut_s": 0.0,
                                         "segments": []})
            return src
        if _autocrop_render(src, segments, out_path):
            dur0 = _probe_duration(src)
            dur1 = _probe_duration(out_path)
            print(f"[autocrop] cut {dur0 - dur1:.1f}s "
                  f"({dur0:.1f}s -> {dur1:.1f}s) "
                  f"leading_cut={detail.get('leading_cut_s')}s "
                  f"source={detail.get('leading_cut_source')} "
                  f"static_cuts={detail.get('static_cuts')} "
                  f"segments={[(round(a, 1), round(b, 1)) for a, b in segments]} "
                  f"kept={detail.get('kept_s')}s "
                  f"coverage={detail.get('coverage_ratio')} "
                  f"of solve_span={detail.get('solve_span_s')}s "
                  f"guard_clamped={detail.get('guard_clamped')}")
            LAST_POLISH.update(autocrop={**detail,
                                         "cut_s": round(dur0 - dur1, 1),
                                         "segments": [[round(a, 1), round(b, 1)]
                                                      for a, b in segments]})
            return out_path
        print("[autocrop] render failed, keeping source")
        LAST_POLISH.update(autocrop={**detail, "cut_s": 0.0,
                                     "render_failed": True,
                                     "segments": []})
        return src
    except Exception as e:
        print(f"[autocrop] skipped: {str(e)[:160]}")
        return src


def polish_gameplay(src, out_path, crf=17, first_typing_s=None, idle_cut_s=None,
                    autocrop=True, solve_span_s=None,
                    first_action_label=None):
    """Full-frame 1920x1080 polish. Cursor overlay is OFF by default.

    Set CURSOR_OVERLAY=on explicitly to re-enable the drawn cursor.
    Default off: final videos must not contain a drawn cursor.

    GAMEPLAY_FRAME=card (default) keeps the wallpaper + rounded-frame look the
    other games use; GAMEPLAY_FRAME=fullbleed scales the capture edge-to-edge
    (the style of the manual Recordly waffle video). Both get the same cursor.

    Autocrop (default on, AUTOCROP=off to disable) applies ONLY to the
    gameplay clip passed here — never to intro/hints/answer card clips,
    which are separate clips assembled later. It cuts leading idle before
    the first player action (prefer `idle_cut_s`, else `first_typing_s - 1s`
    safety margin where `first_typing_s` is the RECORDING-relative first
    action — keystroke for typing games, first swap for waffle, first pick
    for colorfle — else first sustained board change from frame diffs) and
    mid-recording static waits >30 s (kept as a 2 s head + 0.5 s crossfade).
    When `solve_span_s` (START->shot_win) is given, the kept footage is
    never cut below 50% of the solve span (coverage guard). Every cut is
    logged via _apply_autocrop.
    """
    import subprocess
    import tempfile
    out_path = str(out_path)
    src = str(src)
    crop_tmp = None
    if autocrop:
        try:
            # Intermediate lives in a temp dir, never next to out_path:
            # the workflow uploads videos/<game>/*.mp4, so a
            # "<stem>.autocrop.mp4" beside the output would leak into
            # the artifact as a stray video.
            crop_tmp = str(_QPPath(tempfile.mkdtemp(prefix="autocrop_")) /
                           "cropped.mp4")
            cropped = _apply_autocrop(src, crop_tmp, first_typing_s, idle_cut_s,
                                      solve_span_s, first_action_label)
            if cropped != src:
                src = cropped
            else:
                crop_tmp = None
        except Exception as e:
            print(f"[autocrop] skipped: {str(e)[:160]}")
            crop_tmp = None
    mode = os.environ.get("GAMEPLAY_FRAME", "card").lower()
    if os.environ.get("CURSOR_OVERLAY", "off").lower() in ("off", "0", "false"):
        evs = []
    else:
        evs = ev_all()
    # auto-detect the raw capture size so cursor coords map correctly for
    # any viewport (marveldle records 720p to stay under the OOM killer)
    try:
        import subprocess as _sp2
        _r2 = _sp2.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height",
                        "-of", "csv=p=0", src],
                       capture_output=True, text=True, timeout=60)
        _rw, _rh = [int(v) for v in _r2.stdout.strip().split(",")[:2]]
    except Exception:
        _rw, _rh = 1920, 1080
    if mode == "fullbleed":
        try:
            cmd = ["ffmpeg", "-y", "-v", "error", "-i", src, "-vf",
                   "scale=1920:1080:force_original_aspect_ratio=increase,"
                   "crop=1920:1080,format=yuv420p,fps=24",
                   "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
                   "-c:a", "aac", out_path]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        except Exception as e:
            print(f"[polish] fullbleed failed: {str(e)[:160]}")
            r = None
        base = out_path if (r is not None and r.returncode == 0 and _QPPath(out_path).exists()
                            and _QPPath(out_path).stat().st_size > 10000) else src
        ox = oy = 0.0
        scx, scy = 1920.0 / float(_rw), 1080.0 / float(_rh)
    else:
        # Polish must never raise (a polish failure must not lose the
        # gameplay artifact): on any error fall back to the source clip.
        try:
            base = _card_composite(src, out_path, crf)
        except Exception as e:
            print(f"[polish] card composite failed: {str(e)[:160]}")
            base = src
        fw, fh = 1792, 1008
        ox, oy = (1920 - fw) / 2.0, (1080 - fh) / 2.0
        scx, scy = fw / float(_rw), fh / float(_rh)
    LAST_POLISH.update(mode=mode, events=len(evs), clips=0, base=base)
    # Drop the temp-dir autocrop intermediate once the composite has
    # consumed it — but never when the composite fell back to returning
    # it as `base`, or the caller would get a deleted file.
    if crop_tmp and _QPPath(crop_tmp).exists() and crop_tmp != out_path \
            and base != crop_tmp:
        try:
            _QPPath(crop_tmp).unlink()
        except Exception:
            pass
    if not evs or base == src and not _QPPath(base).exists():
        return base
    try:
        from moviepy.editor import VideoFileClip, CompositeVideoClip
        bc = VideoFileClip(base)
        clips = _cursor_clips(bc, evs, ox, oy, scx, scy)
        if not clips:
            bc.close()
            return base
        comp = CompositeVideoClip([bc] + clips, size=bc.size)
        out_cur = str(_QPPath(base).parent / (_QPPath(base).stem + "_cur.mp4"))
        comp.write_videofile(out_cur, codec="libx264", audio_codec="aac",
                             fps=24, verbose=False, logger=None)
        try:
            bc.close()
            comp.close()
        except Exception:
            pass
        if _QPPath(out_cur).exists() and _QPPath(out_cur).stat().st_size > 10000:
            LAST_POLISH.update(clips=len(clips), out=out_cur)
            print(f"[polish] cursor overlay: {len(clips)} clips over "
                  f"{len(evs)} telemetry events ({mode} frame)")
            return out_cur
    except Exception as e:
        print(f"[polish] cursor overlay failed: {str(e)[:140]}")
    return base


def _card_composite(src, out_path, crf):
    """The wallpaper + rounded-frame card look (moved verbatim from
    runner._polish_gameplay so every game - nerdle included - shares it)."""
    import subprocess
    from PIL import Image, ImageDraw, ImageFilter
    import tempfile
    tmp = _QPPath(tempfile.mkdtemp(prefix="polish_"))
    fw, fh, radius = 1792, 1008, 44
    try:
        m = Image.new("RGBA", (fw, fh), (0, 0, 0, 0))
        ImageDraw.Draw(m).rounded_rectangle([0, 0, fw - 1, fh - 1],
                                            radius=radius,
                                            fill=(255, 255, 255, 255))
        mask_p = tmp / "mask.png"
        m.save(mask_p)
        sh = Image.new("RGBA", (fw, fh), (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle([4, 4, fw - 5, fh - 5],
                                             radius=radius,
                                             fill=(0, 0, 0, 110))
        sh = sh.filter(ImageFilter.GaussianBlur(16))
        shad_p = tmp / "shadow.png"
        sh.save(shad_p)
        rim = Image.new("RGBA", (fw, fh), (0, 0, 0, 0))
        ImageDraw.Draw(rim).rounded_rectangle([1, 1, fw - 2, fh - 2],
                                              radius=radius,
                                              outline=(255, 255, 255, 150),
                                              width=3)
        rim_p = tmp / "rim.png"
        rim.save(rim_p)
    except Exception as e:
        print(f"[polish] pillow assets failed: {e}")
        return src
    wall_mp4 = tmp / "wallpaper.mp4"
    dur = 600.0
    try:
        r2 = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                             "format=duration", "-of", "csv=p=0", src],
                            capture_output=True, text=True, timeout=60)
        dur = max(1.0, float((r2.stdout or "600").strip() or 600))
    except Exception:
        pass
    wall = _QPPath(__file__).parent / "wallpapers" / "glassmorphism-3.jpg"
    ok_wall = False
    if wall.exists():
        # ROOT-CAUSE FIX (nerdle gameplay_1080.mp4 missing): the wallpaper
        # used to be pre-rendered at the FULL video duration (e.g. 442 s),
        # which takes ~250 s+ on CI, but this step allowed only timeout=60,
        # so it raised TimeoutExpired("Command '['ffmpeg', ...") out of
        # polish and the gameplay file was never produced. The wallpaper
        # is a static image, so render only 5 s of it and loop it
        # (-stream_loop -1) in the final composite — visually identical,
        # O(1) cost regardless of video length.
        try:
            r1 = subprocess.run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i",
                                 str(wall), "-vf",
                                 "scale=1920:1080:force_original_aspect_ratio=increase,"
                                 "crop=1920:1080,eq=brightness=-0.05:saturation=1.1",
                                 "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                                 "-pix_fmt", "yuv420p", "-r", "24", "-t", "5",
                                 str(wall_mp4)], capture_output=True, text=True,
                                timeout=120)
            ok_wall = r1.returncode == 0 and wall_mp4.exists()
        except Exception as e:
            print(f"[polish] wallpaper render failed: {str(e)[:120]}")
            ok_wall = False
    if not ok_wall:
        wall_mp4 = tmp / "blurred.mp4"
        try:
            r3 = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", src, "-vf",
                                 "scale=1920:1080,gblur=sigma=24,eq=brightness=-0.10:"
                                 "saturation=1.2", "-c:v", "libx264",
                                 "-preset", "medium", "-crf", "20",
                                 "-pix_fmt", "yuv420p", "-r", "24", str(wall_mp4)],
                                capture_output=True, text=True, timeout=1800)
        except Exception as e:
            print(f"[polish] blurred-bg render failed: {str(e)[:120]}")
            return src
        if r3.returncode != 0:
            return src
    filt = (
        f"[4:v]format=yuv420p[bg];"
        f"[0:v]scale={fw}:-2,format=rgba[f];"
        f"[1:v]scale={fw}:-2[fm];"
        f"[f][fm]alphamerge[fg];"
        f"[2:v]scale={fw}:-2[sh];"
        f"[3:v]scale={fw}:-2[rim];"
        f"[bg][sh]overlay=(W-w)/2:(H-h)/2+8:format=auto[b1];"
        f"[b1][fg]overlay=(W-w)/2:(H-h)/2[b2];"
        f"[b2][rim]overlay=(W-w)/2:(H-h)/2,format=yuv420p,fps=24[v]")
    # -stream_loop -1 -t dur loops the short wallpaper clip to the full
    # duration (see the TimeoutExpired fix above); -t here is an INPUT
    # option on the wallpaper input, not an output cap.
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", src,
           "-i", str(mask_p), "-i", str(shad_p), "-i", str(rim_p),
           "-stream_loop", "-1", "-t", str(dur), "-i", str(wall_mp4),
           "-filter_complex", filt, "-map", "[v]", "-map", "0:a?",
           "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
           "-pix_fmt", "yuv420p", "-r", "24", "-c:a", "aac", out_path]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    except Exception as e:
        print(f"[polish] composite failed: {str(e)[:160]}")
        return src
    if r.returncode == 0 and _QPPath(out_path).exists() \
            and _QPPath(out_path).stat().st_size > 10000:
        return out_path
    print(f"[polish] ffmpeg said: {(r.stderr or '')[:240]}")
    return src
