"""Shared brand intro + watermark helpers (fix-loop iter 33).

ITEM 1 - INTRO (restored, CLEANED): the 20 s brand intro
``intro/wordsolverx-intro-20s.mp4`` was switched off in iter 11 because
its middle shows a WRONG-GAME, STALE Wordle answer: the
"Wordle Hints and Answer for Today #1934 - OCTOBER 5, 2026" page with
the MOCHA tiles, the "MOCHA - BEST GUESS" solver card, and a Phoodle
"BRE..." answer fragment (frame audit, 1 fps: dirty from ~2 s to ~12 s;
logo 0-1 s, "40+ solvers" 12-14 s, DAILY VIDEOS, outro all clean).
Re-enabling the raw file would put a 13-day-stale answer at the head
of every video - the exact thing the no-stale-answer rule forbids.

So assembly NEVER prepends the raw file. ``clean_intro()`` builds a
cleaned copy with ffmpeg: the dirty window [DIRTY_START, DIRTY_END)
is replaced by a static hold of the clean WORDSOLVERX logo frame
(HOLD_AT), the rest is the original, and the audio track is DROPPED
(the intro's own audio is unverified - a spoken "MOCHA" voiceover
would leak the stale answer through audio; the per-game bg song
covers the full final clip instead). Duration stays 20.0 s, so the
chapter shift is a plain +20 s (the Worldle chapter helper already
takes intro_s; see _s_worldle_final_chapters).

ITEM 2 - WATERMARK (new; no video watermark ever existed in repo
history): ``make_watermark_png()`` renders a small "wordsolverx.com"
badge (white text, soft shadow, translucent dark pill) and
``apply_watermark()`` composites it bottom-right over the WHOLE
final clip (gameplay frames AND cards), small + low-opacity so it
never covers boards, tiles, guesses or scores.

Every helper fails SOFT: on any error it logs loudly and returns the
input unchanged (intro skipped / no watermark), so a helper failure
can never fail a game job - the video ships exactly as before.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
INTRO_SRC = HERE / "intro" / "wordsolverx-intro-20s.mp4"

# Dirty window in the raw intro (frame-audited, 1 fps):
#   t=1s generic Wordle-grid demo (clean), t=2s full demo grid (clean),
#   t=3s "WORDLE ANSWER FOR OCTOBER 5, 2026" page animating in (DIRTY),
#   t=5s full MOCHA tiles, t=9s "MOCHA - BEST GUESS" solver card,
#   t=11s Phoodle "BRE..." answer fragment, t=12s "40+ SOLVERS" (clean).
# [1.8, 12.5) covers every dirty frame with margin on both sides.
DIRTY_START = 1.8
DIRTY_END = 12.5
HOLD_AT = 0.3  # clean WORDSOLVERX logo frame used for the hold
WATERMARK_TEXT = "wordsolverx.com"


def _ffmpeg_exe():
    """ffmpeg binary: imageio-ffmpeg (what moviepy uses in CI) first,
    then system ffmpeg on PATH."""
    try:
        import imageio_ffmpeg
        p = imageio_ffmpeg.get_ffmpeg_exe()
        if p and os.path.exists(p):
            return p
    except Exception:
        pass
    return shutil.which("ffmpeg") or "ffmpeg"


def _run(cmd):
    return subprocess.run(cmd, stdout=subprocess.DEVNULL,
                          stderr=subprocess.PIPE, timeout=300,
                          check=False)


def clean_intro(src=None, out=None, log=print):
    """Build a cleaned intro (no stale answers, no audio) with ffmpeg.

    Returns the output path on success, None on any failure.
    ``src`` defaults to the repo intro; ``out`` defaults to a temp file.
    The caller owns deleting ``out`` (assembly jobs write into vdir).
    """
    src = Path(src) if src else INTRO_SRC
    if not src.exists():
        log(f"[intro] clean skipped: missing {src}")
        return None
    if out is None:
        fd, out = tempfile.mkstemp(prefix="intro_clean_",
                                   suffix=".mp4")
        os.close(fd)
    out = str(out)
    ff = _ffmpeg_exe()
    tmpdir = tempfile.mkdtemp(prefix="intro_clean_")
    hold_png = os.path.join(tmpdir, "hold.png")
    try:
        # Hold frame: clean logo frame from the head of the intro.
        r = _run([ff, "-y", "-v", "error", "-ss", str(HOLD_AT),
                  "-i", str(src), "-frames:v", "1", hold_png])
        if r.returncode != 0 or not os.path.exists(hold_png):
            log("[intro] clean skipped: hold-frame extract failed")
            return None
        hold_dur = DIRTY_END - DIRTY_START
        # [0, DIRTY_START) original + [DIRTY_START, DIRTY_END) logo hold
        # + [DIRTY_END, end) original; no audio (-an).
        filt = (
            f"[0:v]trim=0:{DIRTY_START},setpts=PTS-STARTPTS[v0];"
            f"[1:v]trim=0:{hold_dur},setpts=PTS-STARTPTS,"
            f"scale=1920:1080,fps=30[v1];"
            f"[0:v]trim={DIRTY_END}:999,setpts=PTS-STARTPTS[v2];"
            f"[v0][v1][v2]concat=n=3:v=1:a=0,format=yuv420p[vout]"
        )
        r = _run([ff, "-y", "-v", "error",
                  "-i", str(src),
                  "-loop", "1", "-t", str(hold_dur), "-i", hold_png,
                  "-filter_complex", filt,
                  "-map", "[vout]", "-an",
                  "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                  "-r", "30", out])
        if r.returncode != 0 or not os.path.exists(out):
            log("[intro] clean skipped: ffmpeg build failed")
            return None
        return out
    except Exception as e:
        log(f"[intro] clean skipped: {str(e)[:160]}")
        return None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def shift_chapters_for_intro(chapters, intro_dur):
    """[(0, 'Intro')] + every chapter shifted by intro_dur (pure).

    intro_dur <= 0 -> chapters unchanged (intro skipped path).
    """
    try:
        d = float(intro_dur or 0)
    except Exception:
        d = 0.0
    if d <= 0:
        return list(chapters or [])
    return ([(0, "Intro")] +
            [(round(float(s) + d, 1), t) for s, t in (chapters or [])])


def make_watermark_png(path, text=WATERMARK_TEXT):
    """Render the small bottom-right watermark badge (RGBA PNG).

    White bold text + soft shadow on a translucent dark pill.
    Returns (width, height) of the PNG.
    """
    from PIL import Image, ImageDraw, ImageFont
    path = str(path)
    font = None
    for cand in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                 "C:/Windows/Fonts/segoeuib.ttf"):
        if os.path.exists(cand):
            try:
                font = ImageFont.truetype(cand, 34)
                break
            except Exception:
                continue
    if font is None:
        font = ImageFont.load_default()
    # Measure text first so the pill fits it.
    probe = Image.new("RGBA", (8, 8))
    d = ImageDraw.Draw(probe)
    bbox = d.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad_x, pad_y = 22, 12
    w, h = tw + pad_x * 2, th + pad_y * 2 + 4
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # Translucent dark pill.
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=18,
                        fill=(0, 0, 0, 78))
    # Soft shadow then white text (alpha ~140 = discreet).
    tx, ty = pad_x - bbox[0], pad_y - bbox[1]
    d.text((tx + 2, ty + 2), text, font=font, fill=(0, 0, 0, 110))
    d.text((tx, ty), text, font=font, fill=(255, 255, 255, 140))
    img.save(path, "PNG")
    return (w, h)


def watermark_position(png_w, png_h, frame_w=1920, frame_h=1080,
                        margin_x=28, margin_y=30):
    """Bottom-right position for the badge (pure)."""
    return (int(frame_w - png_w - margin_x),
            int(frame_h - png_h - margin_y))


def prepend_clean_intro(clip, chapters, workdir, log=print):
    """Prepend the CLEANED 20 s intro to a moviepy clip.

    Returns (new_clip, new_chapters, intro_dur). On any failure the
    input clip/chapters are returned unchanged with intro_dur 0.0.
    """
    try:
        from moviepy.editor import (VideoFileClip, concatenate_videoclips)
        out = Path(workdir) / "intro_clean.mp4"
        cleaned = clean_intro(INTRO_SRC, out, log=log)
        if not cleaned:
            return clip, list(chapters or []), 0.0
        intro = VideoFileClip(str(cleaned))
        dur = float(intro.duration or 0)
        if dur < 10:
            log(f"[intro] skipped: cleaned intro too short ({dur:.1f}s)")
            return clip, list(chapters or []), 0.0
        new_clip = concatenate_videoclips([intro, clip],
                                          method="compose")
        log(f"[intro] prepended cleaned intro ({dur:.1f}s, "
            f"stale-answer window {DIRTY_START}-{DIRTY_END}s held)")
        return (new_clip,
                shift_chapters_for_intro(chapters, dur),
                dur)
    except Exception as e:
        log(f"[intro] prepend skipped: {str(e)[:160]}")
        return clip, list(chapters or []), 0.0


def apply_watermark(clip, workdir, log=print):
    """Composite the wordsolverx badge bottom-right over the clip.

    Returns the composited clip; on any failure the input clip.
    """
    try:
        from moviepy.editor import CompositeVideoClip, ImageClip
        png = Path(workdir) / "watermark.png"
        w, h = make_watermark_png(png)
        x, y = watermark_position(w, h)
        audio = getattr(clip, "audio", None)
        wm = (ImageClip(str(png))
              .set_duration(float(clip.duration or 0))
              .set_position((x, y)))
        out = CompositeVideoClip([clip, wm])
        if audio is not None:
            try:
                out = out.set_audio(audio)
            except Exception:
                pass
        log(f"[watermark] applied bottom-right at ({x},{y}) size {w}x{h}")
        return out
    except Exception as e:
        log(f"[watermark] skipped: {str(e)[:160]}")
        return clip


if __name__ == "__main__":
    # Manual smoke: python3 intro_watermark.py [out.mp4]
    dest = sys.argv[1] if len(sys.argv) > 1 else "/tmp/intro_clean.mp4"
    print("cleaned ->", clean_intro(INTRO_SRC, dest))
    print("watermark ->", make_watermark_png("/tmp/watermark.png"))
