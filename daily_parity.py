"""Wordle-parity slide + caption generation for the daily-games videos.

Quordle's video is recap(5s) + hints(10s) + gameplay + 3x analysis(8s) +
teaser(5s) with real SRT captions. The daily-games videos were a single
hints card + a single reveal card. This module builds the same structure.
"""
import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))  # vendored quordle_parity lives beside this file
sys.path.insert(0, str(HERE / ".." / "quordle-video" / "quor-dle-video"))
try:
    import quordle_parity as QP
except Exception:
    QP = None

W, H = 1920, 1080
BG_TOP, BG_BOT = (13, 20, 38), (30, 45, 80)
CARD, CARD_BORDER = (23, 34, 58), (52, 68, 100)
TEXT, MUTED = (255, 255, 255), (168, 180, 200)
GREEN, YELLOW, GREY = (46, 204, 113), (255, 205, 0), (120, 128, 140)
TILE_G, TILE_Y, TILE_X = (46, 204, 113), (255, 205, 0), (68, 78, 92)


def _fonts():
    pairs = [("C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/segoeui.ttf"),
             ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")]
    for b, r in pairs:
        if os.path.exists(b):
            return {"t": ImageFont.truetype(b, 74),
                    "b": ImageFont.truetype(b, 42),
                    "s": ImageFont.truetype(r if os.path.exists(r) else b, 32),
                    "m": ImageFont.truetype(b, 54),
                    "tile": ImageFont.truetype(b, 62)}
    d = ImageFont.load_default()
    return {k: d for k in ("t", "b", "s", "m", "tile")}


def _bg(draw):
    for y in range(H):
        t = y / H
        draw.line([(0, y), (W, y)],
                  fill=(int(BG_TOP[0] + (BG_BOT[0] - BG_TOP[0]) * t),
                        int(BG_TOP[1] + (BG_BOT[1] - BG_TOP[1]) * t),
                        int(BG_TOP[2] + (BG_BOT[2] - BG_TOP[2]) * t)))


def _base(kicker, date_str):
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    _bg(d)
    f = _fonts()
    d.rectangle([0, 0, 14, H], fill=GREEN)
    d.text((70, 52), kicker.upper(), fill=GREEN, font=f["m"])
    if date_str:
        d.text((W - 620, 66), date_str, fill=MUTED, font=f["s"])
    return img, d, f


def _tile_row(d, f, word, pattern, y, size=96, gap=14):
    n = len(word)
    total = n * size + (n - 1) * gap
    x = (W - total) // 2
    for i, ch in enumerate(word.upper()):
        p = pattern[i] if i < len(pattern) else "X"
        fill = {"G": TILE_G, "Y": TILE_Y}.get(p, TILE_X)
        d.rounded_rectangle([x, y, x + size, y + size], radius=12, fill=fill)
        fg = (12, 18, 30) if p in ("G", "Y") else (222, 228, 238)
        t = str(ch)
        try:
            tw = d.textlength(t, font=f["tile"])
        except Exception:
            tw = 0
        d.text((x + (size - tw) / 2, y + size / 2 - 38), t, fill=fg,
               font=f["tile"])
        x += size + gap


def generate_recap(out_path, game, date_str, prev_answer=None):
    """5s opener so the video matches Quordle's structure.

    NOTE: this is a TODAY intro card, not a yesterday's-answer card. The
    previous version rendered the literal string "YESTERDAY" as the answer,
    which told the viewer nothing and leaked no information. It now states
    the puzzle that is being solved, without giving the answer away.
    """
    img, d, f = _base(f"{game} today", date_str)
    d.text((70, 170), "TODAY'S", fill=MUTED, font=f["m"])
    d.text((70, 250), game.upper(), fill=TEXT, font=f["t"])
    d.rounded_rectangle([70, 380, 900, 560], radius=16, fill=CARD,
                        outline=CARD_BORDER, width=3)
    d.text((110, 410), "Puzzle date", fill=MUTED, font=f["s"])
    d.text((110, 460), str(date_str)[:24], fill=YELLOW, font=f["m"])
    d.text((70, 640), "Full solve, live on the real site", fill=MUTED, font=f["s"])
    d.text((70, 720), "New puzzle live now", fill=GREEN, font=f["b"])
    img.save(out_path, "PNG", optimize=True)
    return out_path


def generate_hints(out_path, game, date_str, hints, answer):
    """Progressive hint card (Wordle parity)."""
    img, d, f = _base(f"{game} hints", date_str)
    d.text((70, 160), "HINTS", fill=TEXT, font=f["t"])
    a = str(answer or "").upper()
    masked = "".join(ch if i == 0 else "_" for i, ch in enumerate(a))
    d.text((70, 260), f"Answer: {masked}", fill=MUTED, font=f["m"])
    y = 380
    for i, h in enumerate(hints[:3], 1):
        d.rounded_rectangle([70, y, W - 70, y + 110], radius=16, fill=CARD,
                            outline=CARD_BORDER, width=3)
        d.text((110, y + 34), f"HINT {i}", fill=YELLOW, font=f["b"])
        d.text((380, y + 34), str(h)[:48], fill=TEXT, font=f["b"])
        y += 130
    d.text((70, H - 110), "Pause & guess before the solve!", fill=YELLOW,
           font=f["s"])
    img.save(out_path, "PNG", optimize=True)
    return out_path


def _clue_line(pat):
    """Turn a G/Y/X feedback pattern into a sentence a viewer can follow.

    The raw codes ("GYYXX") and the candidate-pool arithmetic ("pool 6840 ->
    2184") were internal solver telemetry. They mean nothing to a viewer, made
    the video look like it was written for engineers, and for colour games
    there is no letter pattern at all - so the slide was pure noise. Each guess
    is now described the way a player would say it out loud.
    """
    if not pat:
        return ""
    a = str(pat).upper()
    n = len(a)
    greens, yellows = a.count("G"), a.count("Y")
    greys = n - greens - yellows
    bits = []
    if greens:
        bits.append(f"{greens} letter{'s' if greens > 1 else ''} right")
    if yellows:
        bits.append(f"{yellows} in the word, wrong spot")
    if greys:
        bits.append(f"{greys} not in the word")
    return ", ".join(bits) or "no match"


def _text_h(d, txt, font):
    """Rendered height of `txt`, measured rather than guessed."""
    try:
        b = d.textbbox((0, 0), str(txt), font=font)
        return int(b[3] - b[1])
    except Exception:
        return int(getattr(font, "size", 32) * 1.25)


def _fit_font(d, txt, font, max_w):
    """Shrink `font` until `txt` fits `max_w`. Never crop text."""
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
    """Rebuild a TrueType font at a new pixel size (PIL has no resize)."""
    path = getattr(font, "path", None)
    if path:
        return ImageFont.truetype(path, size)
    raise ValueError("font has no path")


def _center(d, txt, font, cx, cy):
    """Draw `txt` centred on (cx, cy)."""
    t = str(txt)
    try:
        w = d.textlength(t, font=font)
        b = d.textbbox((0, 0), t, font=font)
    except Exception:
        d.text((cx, cy), t, font=font)
        return
    d.text((cx - w / 2, cy - (b[3] - b[1]) / 2 - b[1]), t, font=font)


def generate_reveal(out_path, game, date_str, answer, steps, pattern=None):
    """The answer plus a plain-language account of how each guess landed.

    The word and its clue used to be stacked inside a 92px card - a 54px word
    drawn at y+26 plus a 32px clue at y+62 - so the two lines printed straight
    through each other. Word and clue now sit side by side inside a card whose
    height is derived from the measured text, and both are width-fitted, so no
    row can ever collide or spill past the card.
    """
    img, d, f = _base(f"{game} answer", date_str)
    a = str(answer or "").upper()
    pat = pattern or ("G" * len(a))
    d.text((70, 150), "TODAY'S ANSWER", fill=MUTED, font=f["m"])
    _tile_row(d, f, a, pat, 240, size=112, gap=18)
    d.text((70, 412), "HOW EACH GUESS SCORED", fill=GREEN, font=f["m"])

    rows = list(steps or [])[-4:]
    # Geometry derived from the measured glyph heights, not magic numbers.
    word_f, clue_f = f["m"], f["s"]
    word_h = _text_h(d, "X", word_f)
    clue_h = _text_h(d, "X", clue_f)
    pad = 22
    card_h = max(96, word_h + clue_h + pad * 2)
    gap = 16
    top = 486
    # Keep the stack clear of the footer.
    bottom_limit = H - 120
    while rows and top + len(rows) * card_h + (len(rows) - 1) * gap > bottom_limit:
        rows = rows[-3:] if len(rows) > 3 else rows
        if len(rows) == 3 and top + 3 * card_h + 2 * gap > bottom_limit:
            rows = rows[-2:]
        if len(rows) <= 2:
            break
    for s in rows:
        d.rounded_rectangle([70, top, W - 70, top + card_h], radius=14,
                            fill=CARD)
        g = str(s.get("guess", "")).upper()[:20]
        num = f"{s.get('turn', '?')}."
        d.text((104, top + pad), num, fill=GREEN, font=clue_f)
        try:
            nw = d.textlength(num, font=clue_f)
        except Exception:
            nw = 24
        # Word and clue are SIBLINGS on one baseline group, never stacked.
        x = 104 + nw + 18
        right_pad = 104
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
                # Not enough room beside it - put the clue under the word but
                # INSIDE the card, using the measured card height.
                cf = _fit_font(d, clue, clue_f, (W - 200))
                d.text((x, top + pad + word_h + 6), clue, fill=MUTED, font=cf)
        top += card_h + gap

    d.text((70, H - 90),
           f"Full solve: wordsolverx.com/{game.lower()}-answer-today",
           fill=GREEN, font=f["s"])
    img.save(out_path, "PNG", optimize=True)
    return out_path


def generate_mode_card(out_path, game, mode_name, subtitle=""):
    """A 'NOW PLAYING: <mode>' card shown at the start of each mode chapter.

    Multi-mode videos (Framed's four modes, Nerdle's nine, Quordle's six) run
    back to back with no visual break, so a viewer landing mid-video has no idea
    which round they are watching. This card is spliced in before each mode's
    footage to name it explicitly.
    """
    img, d, f = _base(f"{game} mode", "")
    d.text((70, 150), "NOW PLAYING", fill=MUTED, font=f["m"])

    # Fit the mode name inside the safe area, shrinking until it does.
    safe_w = W - 200
    mf = _fit_font(d, str(mode_name).upper(), f["t"], safe_w)
    mh = _text_h(d, str(mode_name), mf)
    _center(d, str(mode_name).upper(), mf, W // 2, 330)

    # A rule under the name, sized to the text it belongs to.
    try:
        nw = d.textlength(str(mode_name).upper(), font=mf)
    except Exception:
        nw = safe_w
    half = min(int(nw / 2) + 60, safe_w // 2)
    y = 330 + int(mh / 2) + 40
    d.rectangle([W // 2 - half, y, W // 2 + half, y + 4], fill=GREEN)

    if subtitle:
        sf = _fit_font(d, str(subtitle), f["m"], safe_w)
        _center(d, str(subtitle), sf, W // 2, y + 110)
    img.save(out_path, "PNG", optimize=True)
    return out_path


def generate_facts(out_path, game, date_str, answer, steps):
    """Third analysis slide: stats about the solve itself.

    "Start pool" was the candidate-count the internal solver started from. It
    is meaningless to a viewer, so only eyeball-verifiable facts remain.
    """
    img, d, f = _base(f"{game} analysis", date_str)
    a = str(answer or "").upper()
    d.text((70, 150), "SOLVE BREAKDOWN", fill=TEXT, font=f["t"])
    rows = [
        ("Guesses used", str(len(steps or []))),
        ("Final answer", a),
        ("Letter count", str(len(a))),
        ("Vowels", str(sum(1 for c in a if c in "AEIOU"))),
        ("Unique letters", str(len(set(a)))),
    ]
    y = 300
    for k, v in rows:
        d.rounded_rectangle([70, y, W - 70, y + 84], radius=14, fill=CARD)
        d.text((110, y + 26), k, fill=MUTED, font=f["s"])
        # RIGHT-aligned and width-fitted: Phrazle's 16-character phrase used to
        # be drawn from a fixed x with a 54px font and ran off the right edge.
        val = str(v)[:24]
        vx_right = W - 110
        try:
            vw = d.textlength(val, font=f["m"])
        except Exception:
            vw = 120
        vmax = int(vw * 0.55)
        vf = _fit_font(d, val, f["m"], max(120, vmax))
        try:
            vw = d.textlength(val, font=vf)
        except Exception:
            vw = 120
        d.text((vx_right - vw, y + 18), val, fill=YELLOW, font=vf)
        y += 98
    img.save(out_path, "PNG", optimize=True)
    return out_path


def generate_teaser(out_path, game, date_str, slug):
    img, d, f = _base(f"{game} teaser", date_str)
    d.text((70, 200), "COME BACK", fill=TEXT, font=f["t"])
    d.text((70, 320), "TOMORROW", fill=GREEN, font=f["t"])
    d.text((70, 460), "A brand new puzzle.", fill=MUTED, font=f["m"])
    d.text((70, 560), "Subscribe so you never miss a solve.", fill=MUTED,
           font=f["b"])
    d.rounded_rectangle([70, 680, 1000, 790], radius=14, fill=CARD,
                        outline=GREEN, width=3)
    d.text((110, 715), f"wordsolverx.com/{slug}", fill=GREEN, font=f["b"])
    img.save(out_path, "PNG", optimize=True)
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
