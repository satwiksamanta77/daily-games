"""Premium slide + caption generation for the daily-games videos.

v3 (2026-10-09): full visual redesign - layered gradient + radial glow
background, accent stripe, badge/chip header, glass cards with soft shadows,
glowing answer row, numbered guess cards, branded footer. Same public API as
before (runner.py calls generate_recap/hints/reveal/facts/teaser,
build_captions_srt/title/tags, apply_uniform_music, _fonts).
"""
import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
try:
    import quordle_parity as QP
except Exception:
    QP = None

W, H = 1920, 1080
BG_TOP, BG_BOT = (9, 14, 28), (22, 34, 62)
CARD, CARD_BORDER = (20, 29, 50), (64, 82, 116)
TEXT, MUTED = (255, 255, 255), (168, 180, 200)
GREEN, YELLOW, GREY = (46, 204, 113), (255, 205, 0), (120, 128, 140)
TEAL = (34, 211, 238)
TILE_G, TILE_Y, TILE_X = (46, 204, 113), (255, 205, 0), (68, 78, 92)


def _fonts():
    pairs = [("C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/segoeui.ttf"),
             ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")]
    for b, r in pairs:
        if os.path.exists(b):
            reg = r if os.path.exists(r) else b
            return {"xl": ImageFont.truetype(b, 108),
                    "t": ImageFont.truetype(b, 74),
                    "m": ImageFont.truetype(b, 54),
                    "b": ImageFont.truetype(b, 42),
                    "s": ImageFont.truetype(reg, 32),
                    "xs": ImageFont.truetype(reg, 26),
                    "tile": ImageFont.truetype(b, 62)}
    d = ImageFont.load_default()
    return {k: d for k in ("xl", "t", "m", "b", "s", "xs", "tile")}


def _gradient_bg():
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)],
               fill=(int(BG_TOP[0] + (BG_BOT[0] - BG_TOP[0]) * t),
                     int(BG_TOP[1] + (BG_BOT[1] - BG_TOP[1]) * t),
                     int(BG_TOP[2] + (BG_BOT[2] - BG_TOP[2]) * t)))
    return img.convert("RGBA")


def _glows():
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.ellipse([-320, -420, 900, 620], fill=(46, 204, 113, 34))
    d.ellipse([1150, 520, 2350, 1500], fill=(34, 211, 238, 26))
    d.ellipse([700, -260, 1500, 260], fill=(255, 205, 0, 12))
    return layer.filter(ImageFilter.GaussianBlur(90))


def _stripe(img):
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for x in range(14):
        t = x / 14.0
        d.line([(x, 0), (x, H)],
               fill=(int(46 + (34 - 46) * t), int(204 + (211 - 204) * t),
                     int(113 + (238 - 113) * t), 255))
    img.alpha_composite(layer)


def _chip(d, x, y, text, font, fg, bg, border=None, pad_x=26, pad_y=12):
    try:
        tw = d.textlength(text, font=font)
    except Exception:
        tw = len(text) * 18
    th = _text_h(d, text, font)
    box = [int(x), int(y), int(x + tw + pad_x * 2), int(y + th + pad_y * 2)]
    rad = max(4, min(th, (box[3] - box[1]) // 2 - 2, (box[2] - box[0]) // 2 - 2))
    d.rounded_rectangle(box, radius=rad, fill=bg,
                        outline=border, width=2 if border else 0)
    d.text((x + pad_x, y + pad_y), text, fill=fg, font=font)
    return box[2]


def _card_shadow(img, box, radius=24):
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle(
        [box[0] + 6, box[1] + 10, box[2] + 6, box[3] + 12],
        radius=radius, fill=(0, 0, 0, 110))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(14)))


def _card(img, d, box, radius=24):
    _card_shadow(img, box, radius)
    d.rounded_rectangle(box, radius=radius, fill=CARD + (235,),
                        outline=CARD_BORDER + (255,), width=2)
    d.line([(box[0] + radius, box[1] + 2), (box[2] - radius, box[1] + 2)],
           fill=(255, 255, 255, 28), width=2)


def _base(kicker, date_str):
    img = _gradient_bg()
    img.alpha_composite(_glows())
    _stripe(img)
    d = ImageDraw.Draw(img)
    f = _fonts()
    _chip(d, 70, 56, kicker.upper(), f["xs"], (10, 16, 30),
          GREEN + (255,), pad_x=22, pad_y=10)
    if date_str:
        try:
            tw = d.textlength(date_str, font=f["s"])
        except Exception:
            tw = len(date_str) * 16
        _chip(d, W - 70 - tw - 52, 56, date_str, f["s"], MUTED,
              (16, 24, 42, 200), border=CARD_BORDER + (255,))
    d.ellipse([W - 250, H - 62, W - 236, H - 48], fill=GREEN)
    d.text((W - 224, H - 70), "wordsolverx daily", fill=(120, 134, 158),
           font=f["xs"])
    return img, d, f


def _text_h(d, txt, font):
    try:
        b = d.textbbox((0, 0), str(txt), font=font)
        return int(b[3] - b[1])
    except Exception:
        return int(getattr(font, "size", 32) * 1.25)


def _fit_font(d, txt, font, max_w):
    size = getattr(font, "size", 32)
    try:
        if d.textlength(str(txt), font=font) <= max_w:
            return font
    except Exception:
        return font
    while size > 12:
        size = int(size * 0.92)
        try:
            nf = _clone_font(font, size)
        except Exception:
            return font
        try:
            if d.textlength(str(txt), font=nf) <= max_w:
                return nf
        except Exception:
            return font
    return font


def _clone_font(font, size):
    path = getattr(font, "path", None)
    if path:
        return ImageFont.truetype(path, size)
    raise ValueError("font has no path")


def _center(d, txt, font, cx, cy):
    t = str(txt)
    try:
        w = d.textlength(t, font=font)
        b = d.textbbox((0, 0), t, font=font)
    except Exception:
        d.text((cx, cy), t, fill=TEXT, font=font)
        return
    d.text((cx - w / 2, cy - (b[3] - b[1]) / 2 - b[1]), t, fill=TEXT, font=font)


def _tile_row(d, f, word, pattern, y, size=96, gap=14):
    n = len(word)
    total = n * size + (n - 1) * gap
    x = (W - total) // 2
    for i, ch in enumerate(word.upper()):
        p = pattern[i] if i < len(pattern) else "X"
        fill = {"G": TILE_G, "Y": TILE_Y}.get(p, TILE_X)
        d.rounded_rectangle([x, y, x + size, y + size], radius=16, fill=fill)
        d.rounded_rectangle([x + 3, y + 3, x + size - 3, y + 10],
                            radius=6, fill=(255, 255, 255, 70))
        fg = (12, 18, 30) if p in ("G", "Y") else (222, 228, 238)
        t = str(ch)
        try:
            tw = d.textlength(t, font=f["tile"])
        except Exception:
            tw = 0
        d.text((x + (size - tw) / 2, y + size / 2 - 38), t, fill=fg,
               font=f["tile"])
        x += size + gap


def _parse_ints(txt):
    """'[9, 11, 10]' -> [9, 11, 10]; anything else -> None."""
    import re as _re
    t = str(txt or "").strip()
    if not t.startswith("["):
        return None
    nums = _re.findall(r"-?\d+", t)
    return [int(x) for x in nums] if nums else None


def _swatch(d, x, y, size, hexcol, label=None):
    try:
        h = str(hexcol or "").lstrip('#')
        rgb = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) if len(h) >= 6 \
            else (70, 80, 100)
    except Exception:
        rgb = (70, 80, 100)
    d.rounded_rectangle([x, y, x + size, y + size], radius=14, fill=rgb,
                        outline=(230, 235, 245, 255), width=3)
    d.rounded_rectangle([x + 4, y + 4, x + size - 4, y + 12],
                        radius=6, fill=(255, 255, 255, 80))
    if label and hexcol is None:
        d.text((x + size / 2 - 7, y + size / 2 - 8), str(label),
               fill=(235, 240, 248))


def _swatch_row(d, ints, palette, y, size=112, gap=18):
    n = len(ints)
    total = n * size + (n - 1) * gap
    x = (W - total) // 2
    for ii in ints:
        hx = palette[ii] if palette and 0 <= ii < len(palette) else None
        _swatch(d, x, y, size, hx, label=ii)
        x += size + gap


def _glow_band(img, y, h, color=GREEN, alpha=42):
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle(
        [140, y - 18, W - 140, y + h + 18], radius=40, fill=color + (alpha,))
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(26)))


def _clue_line(pat, kind=None):
    """Turn a G/Y/X feedback pattern into a sentence a viewer can follow."""
    if not pat:
        return ""
    a = str(pat).upper()
    n = len(a)
    greens, yellows = a.count("G"), a.count("Y")
    greys = n - greens - yellows
    if kind == "colors":
        bits = []
        if greens:
            bits.append(f"{greens} colour{'s' if greens > 1 else ''} exact")
        if yellows:
            bits.append(f"{yellows} right colour, wrong slot")
        if greys:
            bits.append(f"{greys} not in today's palette")
        return ", ".join(bits) or "no match"
    bits = []
    if greens:
        bits.append(f"{greens} letter{'s' if greens > 1 else ''} right")
    if yellows:
        bits.append(f"{yellows} in the word, wrong spot")
    if greys:
        bits.append(f"{greys} not in the word")
    return ", ".join(bits) or "no match"


def generate_recap(out_path, game, date_str, prev_answer=None):
    """Opener card: what today's video is, dated and branded."""
    img, d, f = _base(f"{game} today", date_str)
    d.text((70, 190), "TODAY'S", fill=MUTED, font=f["t"])
    d.text((70, 268), str(game).upper(), fill=TEXT, font=f["xl"])
    box = [70, 470, 830, 640]
    _card(img, d, box)
    d.text((110, 502), "Puzzle date", fill=MUTED, font=f["s"])
    d.text((110, 548), str(date_str or ""), fill=YELLOW, font=f["m"])
    d.text((70, 720), "Full solve, live on the real site", fill=MUTED,
           font=f["b"])
    d.text((70, 790), "New puzzle live now", fill=GREEN, font=f["b"])
    x = 70
    for chip in ("Real guesses", "Live feedback", "No spoilers"):
        x = _chip(d, x, 880, chip, f["xs"], GREEN, (16, 40, 30, 220),
                  border=GREEN + (140,)) + 18
    img.convert("RGB").save(out_path, "PNG", optimize=True)
    return out_path


def generate_hints(out_path, game, date_str, hints, answer,
                   kind=None, palette=None):
    """Progressive hint card (Wordle parity), premium layout."""
    img, d, f = _base(f"{game} hints", date_str)
    d.text((70, 170), "HINTS", fill=TEXT, font=f["t"])
    a = str(answer or "").upper()
    ints = _parse_ints(a) if kind == "colors" else None
    box = [70, 300, W - 70, 430]
    _card(img, d, box)
    d.text((110, 340), "Answer shape:", fill=MUTED, font=f["s"])
    if ints:
        gx = 420
        for _i in ints[:8]:
            _swatch(d, gx, 336, 58, None, label="?")
            gx += 74
    else:
        masked = "".join(ch if i == 0 else "_" for i, ch in enumerate(a))
        d.text((420, 336), masked, fill=YELLOW, font=f["m"])
    y = 470
    for i, h in enumerate(hints[:3], 1):
        box = [70, y, W - 70, y + 110]
        _card(img, d, box)
        d.ellipse([110, y + 25, 160, y + 75], fill=GREEN)
        _center(d, str(i), f["b"], 135, y + 50)
        hf = _fit_font(d, str(h), f["b"], W - 320)
        d.text((200, y + 34), str(h)[:60], fill=TEXT, font=hf)
        y += 132
    d.text((70, H - 150), "Pause & guess before the solve!", fill=YELLOW,
           font=f["s"])
    img.convert("RGB").save(out_path, "PNG", optimize=True)
    return out_path


def generate_reveal(out_path, game, date_str, answer, steps, pattern=None,
                    kind=None, palette=None):
    """The answer + a plain-language account of how each guess landed."""
    img, d, f = _base(f"{game} answer", date_str)
    a = str(answer or "").upper()
    pat = pattern or ("G" * len(a))
    d.text((70, 165), "TODAY'S ANSWER", fill=TEXT, font=f["m"])
    ints = _parse_ints(a) if kind == "colors" else None
    if ints:
        _glow_band(img, 250, 120, color=TEAL)
        d = ImageDraw.Draw(img)
        _swatch_row(d, ints, palette, 254, size=112, gap=18)
    elif kind == "colors" and palette:
        _glow_band(img, 250, 120, color=TEAL)
        d = ImageDraw.Draw(img)
        _swatch(d, 90, 250, 120, palette[0])
        d.text((250, 288), a, fill=TEXT, font=f["m"])
    else:
        _glow_band(img, 250, 120)
        d = ImageDraw.Draw(img)
        _tile_row(d, f, a, pat, 254, size=112, gap=18)
    d.text((70, 430), "HOW EACH GUESS SCORED", fill=GREEN, font=f["m"])
    rows = list(steps or [])
    word_f, clue_f = f["m"], f["s"]
    word_h = _text_h(d, "X", word_f)
    pad = 22
    top = 520
    bottom_limit = H - 110
    n = max(1, len(rows))
    card_h = min(110, max(62, (bottom_limit - top - (n - 1) * 12) // n))
    gap = 12
    for s in rows:
        box = [70, top, W - 70, top + card_h]
        _card(img, d, box, radius=18)
        d = ImageDraw.Draw(img)
        g = str(s.get("guess", "")).upper()[:20]
        d.ellipse([100, top + card_h // 2 - 25, 150, top + card_h // 2 + 25],
                  fill=(16, 40, 30, 255), outline=GREEN + (200,), width=2)
        _center(d, str(s.get("turn", "?")), f["s"], 125, top + card_h // 2)
        x = 178
        right_pad = 104
        gi = _parse_ints(g) if kind == "colors" else None
        if gi:
            gx = x
            for ii in gi[:8]:
                hx = palette[ii] if palette and 0 <= ii < len(palette) \
                    else None
                _swatch(d, gx, top + pad, 46, hx, label=ii)
                gx += 56
            clue = _clue_line(s.get("pattern", ""), kind=kind)[:64]
            cx = gx + 26
            cw_max = (W - right_pad) - cx
            if cw_max > 90:
                cf = _fit_font(d, clue, clue_f, cw_max)
                d.text((cx, top + pad + (word_h - _text_h(d, clue, cf)) / 2),
                       clue, fill=MUTED, font=cf)
            top += card_h + gap
            continue
        avail = (W - right_pad) - x
        wf = _fit_font(d, g, word_f, max(120, int(avail * 0.52)))
        d.text((x, top + pad), g, fill=TEXT, font=wf)
        try:
            gw = d.textlength(g, font=wf)
        except Exception:
            gw = 120
        clue = _clue_line(s.get("pattern", ""))[:64]
        if clue:
            cx = x + gw + 26
            cw_max = (W - right_pad) - cx
            if cw_max > 90:
                cf = _fit_font(d, clue, clue_f, cw_max)
                d.text((cx, top + pad + (word_h - _text_h(d, clue, cf)) / 2),
                       clue, fill=MUTED, font=cf)
            else:
                cf = _fit_font(d, clue, clue_f, (W - 200))
                d.text((x, top + pad + word_h + 6), clue, fill=MUTED, font=cf)
        top += card_h + gap
    d.text((70, H - 92),
           f"Full solve: wordsolverx.com/{game.lower()}-answer-today",
           fill=GREEN, font=f["s"])
    img.convert("RGB").save(out_path, "PNG", optimize=True)
    return out_path


def generate_mode_card(out_path, game, mode_name, subtitle=""):
    """A 'NOW PLAYING: <mode>' card shown at the start of each chapter."""
    img, d, f = _base(f"{game} · mode", "")
    _glow_band(img, 430, 190)
    d = ImageDraw.Draw(img)
    d.text((70, 330), "NOW PLAYING", fill=MUTED, font=f["b"])
    d.text((70, 400), str(mode_name).upper(), fill=TEXT, font=f["xl"])
    if subtitle:
        d.text((70, 560), str(subtitle), fill=TEAL, font=f["m"])
    d.text((70, 700), f"{game} - full solve, live on the real site",
           fill=MUTED, font=f["s"])
    img.convert("RGB").save(out_path, "PNG", optimize=True)
    return out_path


def generate_facts(out_path, game, date_str, answer, steps):
    """Solve breakdown: stat cards instead of a wall of text."""
    img, d, f = _base(f"{game} breakdown", date_str)
    d.text((70, 165), "SOLVE BREAKDOWN", fill=TEXT, font=f["t"])
    rows = list(steps or [])
    ng = len(rows) or 1
    stats = [(str(ng), "guesses played"),
             (str(sum(1 for s in rows
                      if "G" in str(s.get("pattern", "")).upper())),
              "green rows"),
             ("1", "final answer")]
    x = 70
    for val, lab in stats:
        box = [x, 300, x + 540, 470]
        _card(img, d, box)
        d = ImageDraw.Draw(img)
        _center(d, val, f["xl"], x + 270, 372)
        _center(d, lab, f["xs"], x + 270, 440)
        x += 578
    y = 520
    for s in rows[:5]:
        box = [70, y, W - 70, y + 88]
        _card(img, d, box, radius=18)
        d = ImageDraw.Draw(img)
        g = str(s.get("guess", "")).upper()[:24]
        clue = _clue_line(s.get("pattern", ""))[:70]
        d.text((110, y + 26), f"{s.get('turn', '?')}.", fill=GREEN, font=f["b"])
        gf = _fit_font(d, g, f["b"], 620)
        d.text((190, y + 26), g, fill=TEXT, font=gf)
        cf = _fit_font(d, clue, f["s"], W - 900)
        d.text((860, y + 30), clue, fill=MUTED, font=cf)
        y += 104
    img.convert("RGB").save(out_path, "PNG", optimize=True)
    return out_path


def generate_teaser(out_path, game, date_str, slug):
    """End-card CTA: tomorrow's puzzle + site link."""
    img, d, f = _base(f"{game} · tomorrow", date_str)
    _glow_band(img, 380, 220, color=TEAL, alpha=30)
    d = ImageDraw.Draw(img)
    d.text((70, 300), "TOMORROW'S PUZZLE", fill=MUTED, font=f["t"])
    d.text((70, 400), "DROPS AT MIDNIGHT", fill=TEXT, font=f["xl"])
    box = [70, 620, W - 70, 760]
    _card(img, d, box)
    d = ImageDraw.Draw(img)
    d.text((110, 662), f"wordsolverx.com/{slug}", fill=GREEN, font=f["m"])
    x = 70
    for chip in ("Subscribe", "Daily solve", "Free hints"):
        x = _chip(d, x, 810, chip, f["xs"], TEXT, (16, 24, 42, 220),
                  border=TEAL + (160,)) + 18
    img.convert("RGB").save(out_path, "PNG", optimize=True)
    return out_path


def build_captions_srt(chapters, total_duration, date_str, game, out_path=None):
    """SRT captions derived from the real chapter list."""
    def ts(sec):
        sec = max(0, int(sec))
        h, rem = divmod(sec, 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d},000"

    lines, n = [], 1
    for i, (sec, title) in enumerate(chapters):
        end = chapters[i + 1][0] if i + 1 < len(chapters) else total_duration
        if end <= sec:
            end = sec + 4
        lines.append(f"{n}\n{ts(sec)} --> {ts(end)}\n{title} - {game} "
                     f"{date_str}\n")
        n += 1
    out_path = out_path or (HERE / "captions.srt")
    Path(out_path).write_text("\n".join(lines), encoding="utf-8")
    return str(out_path)


def build_title(game, date_short, guesses):
    return (f"{game} Answer Today ({date_short}) - Solved in {guesses} "
            f"Guesses!")


def build_tags(game):
    return [f"{game.lower()} answer today", game, "Daily Puzzle", "Word Game",
            "Puzzle Solution", "Brain Teaser", f"{game} solve", "Answer Today"]


def apply_uniform_music(video_path, qdir, out_path=None):
    """One consistent track across the whole video (Quordle parity)."""
    try:
        import subprocess
        out_path = out_path or video_path
        for song in ("song1.mp3", "song2.mp3"):
            sp = Path(qdir) / song
            if not sp.exists():
                continue
            cmd = ["ffmpeg", "-y", "-i", str(video_path), "-stream_loop", "-1",
                   "-i", str(sp), "-filter_complex",
                   "[1:a]volume=0.18,atrim=0:600[a]",
                   "-map", "0:v", "-map", "[a]", "-c:v", "copy",
                   "-c:a", "aac", "-shortest", out_path]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
            if r.returncode == 0 and Path(out_path).exists():
                return out_path
        return video_path
    except Exception:
        return video_path
