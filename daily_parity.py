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

# Games whose wins are attribute/banner wins, never whole-green-row
# wins (iter-24, ANALYSIS21 RC6): Batterup/Marveldle solve by matching
# attribute columns, Countryle by a "you found the ..." banner. A
# "0 green rows" stat on their solved breakdown cards reads as a
# failure, so generate_facts drops that stat for these games (same
# treatment as Waffle with no extra_stat). Compared lower-cased
# against the display game name passed to generate_facts.
_ATTRIBUTE_WIN_GAMES = {"batterup", "marveldle", "countryle"}


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
    # Iter-19: the old fall-through returned the ORIGINAL (oversize)
    # font when nothing fit, so callers drew overflowing text. Return
    # the smallest font actually tried instead — a too-small full
    # string beats a truncated/overflowing one.
    size = getattr(font, "size", 32)
    best = font
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
            return best
        best = nf
        try:
            if d.textlength(str(txt), font=nf) <= max_w:
                return nf
        except Exception:
            return best
    return best


def _wrap_text(d, txt, font, max_w):
    """Word-wrap `txt` to lines fitting `max_w` — NEVER drops text.

    Iter-19 (ANALYSIS16 §6 item 12): the card renderer sliced guess
    rows to a fixed character count (``[:20]``), so "SAINT KITTS AND
    NEVIS" rendered as "SAINT KITTS AND NEVI" and "LIVING ON BORROWED
    TIME" as "LIVING ON BORROWED T". Wrapping (and, where a single
    line is required, _fit_font shrinking) keeps the full text.
    Over-long single words are hard-split by characters so they
    cannot overflow either.
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
        # Hard-split one over-long word so it cannot overflow.
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


def _fit_wrap(d, txt, font, max_w, max_h, min_size=14):
    """Shrink + wrap `txt` until its lines fit max_w x max_h.

    Returns (lines, font). Text is never dropped: at min_size the
    lines are returned even if they exceed max_h.
    """
    cur_font = font
    while True:
        lines = _wrap_text(d, txt, cur_font, max_w)
        try:
            total_h = sum(_text_h(d, ln, cur_font) + 4
                          for ln in lines)
        except Exception:
            total_h = 0
        size = getattr(cur_font, "size", 32)
        if total_h <= max_h or size <= min_size:
            return lines, cur_font
        try:
            cur_font = _clone_font(cur_font, max(min_size,
                                                 int(size * 0.9)))
        except Exception:
            return lines, cur_font


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
    # Iter-19: long answers (e.g. Phrazle "LIVING ON BORROWED TIME",
    # 22 tiles) overflowed the 1920px frame at size=112 because the
    # row width was never fitted. Shrink tile size/gap/font to fit
    # the frame instead of drawing tiles off-screen.
    n = len(word)
    total = n * size + (n - 1) * gap
    tile_font = f["tile"]
    if n and total > W - 140:
        scale = (W - 140) / total
        size = max(28, int(size * scale))
        gap = max(4, int(gap * scale))
        try:
            tile_font = _clone_font(
                f["tile"],
                max(18, int(getattr(f["tile"], "size", 62) * scale)))
        except Exception:
            pass
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
            tw = d.textlength(t, font=tile_font)
        except Exception:
            tw = 0
        d.text((x + (size - tw) / 2,
                y + size / 2 - int(getattr(tile_font, "size", 62) * 0.61)),
               t, fill=fg, font=tile_font)
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
        # Iter-19: no [:60] slice — wrap + shrink so the full hint
        # is drawn, however long it is.
        hlines, hf = _fit_wrap(d, str(h), f["b"], W - 320, 110 - 40)
        ly = y + 34
        for hline in hlines:
            d.text((200, ly), hline, fill=TEXT, font=hf)
            ly += _text_h(d, hline, hf) + 4
        y += 132
    d.text((70, H - 150), "Pause & guess before the solve!", fill=YELLOW,
           font=f["s"])
    img.convert("RGB").save(out_path, "PNG", optimize=True)
    return out_path


def generate_reveal(out_path, game, date_str, answer, steps, pattern=None,
                    kind=None, palette=None, scoring=True):
    """The answer + a plain-language account of how each guess landed.

    scoring=False for games with NO per-letter feedback (Betweenle,
    Framed, Semantle, Contexto): the card shows only the answer and
    the played words, never fabricated Wordle-style letter scoring
    ("2 in the word, wrong spot") — Betweenle gives only bound rows,
    so a pattern-derived clue line would teach a false mechanic.
    """
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
    d.text((70, 430), "HOW EACH GUESS SCORED" if scoring else
           "GUESSES PLAYED", fill=GREEN, font=f["m"])
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
        # Iter-19: NO [:20] slice — that slice is the truncation
        # root cause ("SAINT KITTS AND NEVI", "LIVING ON BORROWED T").
        # The full guess is drawn, auto-shrunk via _fit_font to the
        # available width. Same for the clue (no [:64] slice).
        g = str(s.get("guess", "")).upper()
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
            clue = (_clue_line(s.get("pattern", ""), kind=kind)
                    if scoring else "")
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
        clue = (_clue_line(s.get("pattern", "")) if scoring else "")
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


def _facts_row_height(n, top=520, bottom_limit=None, gap=12):
    """Pure: breakdown-card row height that fits ALL n rows
    (iter-22, ANALYSIS19 §6 wart): the old card drew a fixed
    88px row for rows[:5], so a 6-row Waffle card clipped the
    6th row (TRYST) off the bottom edge while claiming "6
    guesses played". Row height now shrinks to fit all N rows
    above the footer (bottom_limit = H-110, same as
    generate_reveal), min 36px so text stays legible for the
    largest realistic N. Returns (card_h, gap)."""
    if bottom_limit is None:
        bottom_limit = H - 110
    n = max(1, int(n))
    avail = bottom_limit - top - (n - 1) * gap
    card_h = min(88, max(36, avail // n))
    return int(card_h), int(gap)


def generate_facts(out_path, game, date_str, answer, steps,
                 kind=None, palette=None, scoring=True,
                 guesses_played=None, final_answers=1,
                 extra_stat=None):
    """Solve breakdown: stat cards instead of a wall of text.

    scoring=False for games with NO per-letter feedback (Betweenle,
    Framed, Semantle, Contexto): no "green rows" stat and no
    pattern-derived clue lines — only true facts (submitted guesses,
    guess count, final answer).

    extra_stat=(value, label) REPLACES the "green rows" stat with
    a game-appropriate metric (Waffle: ("11", "swaps used")).
    Waffle never shows "green rows": the generic per-letter
    metric is meaningless for a swap puzzle (iter-20 card read
    "0 green rows" on a real solve), so with no extra_stat a
    Waffle card drops that stat instead of printing a 0.
    Attribute/banner games (Batterup, Marveldle, Countryle) also
    never show "green rows" (iter-24, RC6): their wins are
    attribute/banner wins, not green-row wins, so the stat would
    print a misleading "0" on a solved card.

    guesses_played overrides the step count when the steps are NOT
    the submitted guesses (Framed: one step per mode answer, but
    every mode submits a probe + an answer — 8 submitted guesses,
    4 mode answers; the old card said "4 guesses played / 1 final
    answer", iter-13 breakdown fix). final_answers is the number of
    final answers the card summarizes (1 for single-answer games).
    """
    img, d, f = _base(f"{game} breakdown", date_str)
    d.text((70, 165), "SOLVE BREAKDOWN", fill=TEXT, font=f["t"])
    rows = list(steps or [])
    ng = int(guesses_played) if guesses_played else (len(rows) or 1)
    stats = [(str(ng), "guesses played")]
    if extra_stat is not None:
        # Iter-21: game-appropriate metric replaces "green rows"
        # (Waffle: swaps used, from the solver evidence).
        stats.append((str(extra_stat[0]), str(extra_stat[1])))
    elif scoring and str(game or "").lower() != "waffle" \
            and str(game or "").lower() not in _ATTRIBUTE_WIN_GAMES:
        # Iter-24 (RC6): attribute/banner games never have whole-green
        # rows, so the "green rows" stat would print a misleading "0"
        # on a solved card — drop it instead (same as Waffle above).
        stats.append((str(sum(1 for s in rows
                         if "G" in str(s.get("pattern", "")).upper())),
                 "green rows"))
    stats.append((str(int(final_answers)),
                  "final answer" if int(final_answers) == 1
                  else "final answers"))
    x = 70
    for val, lab in stats:
        box = [x, 300, x + 540, 470]
        _card(img, d, box)
        d = ImageDraw.Draw(img)
        _center(d, val, f["xl"], x + 270, 372)
        _center(d, lab, f["xs"], x + 270, 440)
        x += 578
    # Iter-22: fit ALL N rows (no rows[:5] slice) — the
    # Waffle card clipped its 6th row (TRYST) off the bottom
    # edge. Row height auto-shrinks via _facts_row_height so
    # every row lands above the footer; text is vertically
    # centred in the (possibly shrunk) row instead of at a
    # fixed +26 offset tuned for 88px rows.
    y = 520
    _row_h, _row_gap = _facts_row_height(len(rows))
    for s in rows:
        box = [70, y, W - 70, y + _row_h]
        _card(img, d, box, radius=18)
        d = ImageDraw.Draw(img)
        # Iter-19: NO [:24] / [:70] slices — same truncation class
        # as generate_reveal; full text, auto-shrunk via _fit_font.
        g = str(s.get("guess", "")).upper()
        clue = (_clue_line(s.get("pattern", ""), kind=kind)
                if scoring else "")
        _ty = y + max(6, _row_h // 2 - 14)
        d.text((110, _ty), f"{s.get('turn', '?')}.", fill=GREEN,
               font=f["b"])
        gi = _parse_ints(g) if kind == "colors" else None
        if gi:
            gx = 190
            for ii in gi[:8]:
                hx = palette[ii] if palette and 0 <= ii < len(palette) \
                    else None
                _swatch(d, gx, y + max(4, (_row_h - 44) // 2), 44,
                        hx, label=ii)
                gx += 54
        else:
            gf = _fit_font(d, g, f["b"], 620)
            d.text((190, _ty), g, fill=TEXT, font=gf)
        cf = _fit_font(d, clue, f["s"], W - 900)
        d.text((860, y + max(6, _row_h // 2 - 12)), clue, fill=MUTED,
               font=cf)
        y += _row_h + _row_gap
    img.convert("RGB").save(out_path, "PNG", optimize=True)
    return stats


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
