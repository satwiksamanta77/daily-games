"""Nerdle daily video — all modes solved in one run (Quordle-style).

Modes (9, from all-wordsolverx-workers/nerdle-answers):
  classic(8), micro(5), mini(6), midi(7), maxi(10),
  minibi(2x6), quad(4x8), speed(2x8), instant(8-hint)
Answers: nerdle-answers worker (D1) -> direct nerdlegame md5 URLs fallback.
Video: Playwright record 1280x720, BROWSER_TZ (default Pacific/Kiritimati for next-day),
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
import weakref
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
    from strip_nerdle_state import strip_nerdle_state_file as _strip_state_file
except Exception:
    _strip_state_file = None

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

try:  # moviepy 1.0.3 uses Image.ANTIALIAS, removed in Pillow 10+
    from PIL import Image as _PILImage
    if not hasattr(_PILImage, "ANTIALIAS"):
        _PILImage.ANTIALIAS = _PILImage.LANCZOS
except Exception:
    pass

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

# Website effective-date boundary (ONE named constant, iter 13):
# the wordsolverx puzzle-window rule for nerdle (boundary 16:30 UTC,
# offset +1). If the site moves its Nerdle post to 9pm IST, change
# this to (15, 30) and nothing else. Default 16:30 UTC.
NERDLE_SITE_BOUNDARY_UTC = (16, 30)
# The site's rollover grace (puzzle-window rolloverGraceSeconds 60
# for nerdle): the boundary instant is 16:30:60 UTC. A run inside
# 16:30:00–16:30:60 still sees the pre-boundary date.
NERDLE_SITE_ROLLOVER_GRACE_S = 60


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


def _nerdle_effective_date(now_utc=None):
    """Website effective date (wordsolverx puzzle-window rule, nerdle
    entry: boundaryHourUtc 16, boundaryMinuteUtc 30,
    visibleDateOffsetDays 1, rolloverGraceSeconds 60).

    After the boundary (16:30 UTC) -> UTC date + 1; before -> UTC date.
    16:30 UTC = 22:00 IST = the site's 10pm IST Nerdle post. If the
    site moves its post to 9pm IST, change ONLY NERDLE_SITE_BOUNDARY_UTC
    to (15, 30) — do NOT re-derive the rule (iter 13). The 60 s grace
    is the site's rolloverGraceSeconds: the boundary instant is
    16:30:60 UTC, so a run inside 16:30:00–16:30:60 still sees the
    pre-boundary date, exactly as the site does.
    """
    now = now_utc or datetime.utcnow()
    _bh, _bm = NERDLE_SITE_BOUNDARY_UTC
    boundary = now.replace(hour=_bh, minute=_bm, second=0, microsecond=0)
    boundary = boundary + timedelta(seconds=NERDLE_SITE_ROLLOVER_GRACE_S)
    eff = now.date() + timedelta(days=1 if now >= boundary else 0)
    return datetime(eff.year, eff.month, eff.day)


def _target_date():
    """Puzzle day the VIDEO must show = the WEBSITE effective date
    (wordsolverx puzzle-window rule for nerdle: boundary 16:30 UTC,
    visibleDateOffsetDays 1 — after 16:30 UTC the effective date is
    UTC date + 1, before it is the UTC date; 16:30 UTC = 22:00 IST =
    the site's 10pm IST Nerdle post).

    Uses the REAL UTC wall clock, never FAKE_DATE_ISO / Kiritimati /
    the pipeline date_key. The runner passes the effective date via
    NERDLE_EFFECTIVE_DATE (computed once per run, so a run straddling
    the boundary cannot disagree with itself). A pinned pipeline date
    does NOT pin Nerdle: Nerdle always follows the website effective
    date (a pinned rerun for a past date is a re-play of that date's
    video only if the effective date still equals it).
    """
    env_date = os.environ.get("NERDLE_EFFECTIVE_DATE", "").strip()
    if env_date:
        try:
            return datetime.fromisoformat(env_date)
        except ValueError:
            pass
    return _nerdle_effective_date()


# Per-mode API trust table (iter 14, evidence-grounded in ANALYSIS10):
# the site's own API is authoritative for the PROVEN modes ONLY —
# classic, mini, midi, maxi (proven across two dates: the site's
# served answers equal the API's for these 4). For the UNPROVEN modes
# — micro, minibi, quad, instant, speed — the site's served answers
# DIFFER from the API's (iter-13: site micro 5+3=8 vs API 5+4=9;
# minibi site 56/7=8, 40/8=5 vs API 18/6=3, 54/6=9; quad site
# 65-45=20, 98-31=67, 8*6+4=52, 27*5=135 vs API 72-44=28, 126/7=18,
# 378/6=63, 97-44=53; instant site 28/7-3=1 vs API 9-12/3=5; speed
# 1/2 equations matched), so for those modes the SITE's served
# answers rule: an API mismatch NEVER blocks shipping, and the API
# is never cited as proof for those modes. Gate stays FAIL-CLOSED.
# Iter-19 exception (ANALYSIS16 §2a): mini's ANSWERS are still
# API-proven (site_solutions must equal the official answers), but
# its HEADER is not API-compared — the site shows the global daily
# number on the mini page. See NERDLE_SITE_SOLUTIONS_GATED_MODES.
NERDLE_API_PROVEN_MODES = ("classic", "mini", "midi", "maxi")
NERDLE_API_UNPROVEN_MODES = ("micro", "minibi", "quad", "instant", "speed")


# Modes whose site header carries the site's GLOBAL daily number while
# the API numbers the mode on a SEPARATE sequence (iter-19, ANALYSIS16
# §2a/§6.1, P0): mini's own nav header reads 'mini nerdle . #1733'
# (the global number) but the API's mini numbers are [497]. Comparing
# mini's header against the API mini index is apples-to-oranges and
# false-blocked iter-18. These modes gate on
# site_solutions == official answers, NEVER on header-vs-API.
# NOT in this set: midi — midi's header ALSO reads the global #1733,
# but the API's midi numbers ARE the global sequence ([1733] in the
# iter-13/14 fixtures and in the iter-18 run, where midi did not
# block), so header-vs-API is apples-to-apples for midi and stays.
# Maxi's header is mode-specific (#1368 vs global #1733). Classic's
# header IS the global sequence by definition.
NERDLE_SITE_SOLUTIONS_GATED_MODES = ("mini",)


def _nerdle_site_solutions_match(site_solutions, official_eqs):
    """Exact day-proof for site-solutions-gated modes (iter-19, P0).

    True iff the site exposed solutions AND, as sets, they equal the
    official answers for the mode. Set equality (not any-overlap):
    a partial overlap is NOT a match. Pure (unit-tested).
    """
    site = [str(e) for e in (site_solutions or [])]
    official = [str(e) for e in (official_eqs or [])]
    return bool(site) and bool(official) and set(site) == set(official)


def _nerdle_number_gate(served, api_numbers, official=None):
    """Hard gate (iter 14 rework; iter-19 mini fix): hard-check served
    == API ONLY for the 4 PROVEN modes (classic, mini, midi, maxi).
    Returns (blocked, detail, per_mode dict).

    - proven modes: an exposed header_number must be a MEMBER of the
      API's puzzleNumbers for that mode; a served-answer mismatch
      (matches_target_official is False) BLOCKS. Classic additionally
      REQUIRES a header number (missing blocks) — classic is the day
      proof. For midi/maxi a missing header is a warning (the site
      may not expose it), but the answer check still gates.
    - mini (iter-19, P0): the header comparison applies ONLY when
      mini's header is NOT the global number. When mini's header
      EQUALS classic's header, it IS the global daily number (the
      site's nav shows the global number on the mini page), not a
      mode-specific sequence — the header is ignored and mini gates
      on site_solutions == official answers (exact set equality
      when `official` is passed; the matches_target_official flag
      otherwise). With the global header and no site-solutions
      proof, mini BLOCKS (fail-closed: no day-proof). The gate stays
      FAIL-CLOSED — only the comparison is fixed, never the
      fail-closed behaviour.
    - unproven modes (micro/minibi/quad/instant/speed): the SITE's
      served answers rule. Header mismatch, missing header, and
      answer mismatch are WARNINGS only — never blocking. Do NOT
      claim the API proves those modes.
    """
    per_mode = {}
    blocked_reasons = []
    if not isinstance(api_numbers, dict) or not api_numbers:
        return True, "no API puzzle numbers (API row missing)", per_mode
    served = served if isinstance(served, dict) else {}
    for mode, nums in api_numbers.items():
        if mode == "classicPuzzleNumber":
            continue
        ev = served.get(mode) or {}
        hn = ev.get("header_number")
        want = [int(n) for n in (nums or [])]
        proven = mode in NERDLE_API_PROVEN_MODES
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
        if mode in NERDLE_SITE_SOLUTIONS_GATED_MODES:
            # Iter-19 (P0): mini's header is the GLOBAL number iff it
            # equals classic's header (classic IS the global
            # sequence). Only then is header-vs-API apples-to-oranges:
            # ignore the header, gate on site_solutions == official.
            # Any other mini header (a mode-specific claim, or
            # classic's header unknown) falls through to the
            # header-vs-API check below — the pre-iter-19 behaviour.
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
                    # Legacy callers (no official list): the
                    # matches_target_official flag is the only
                    # day-proof available — True passes, anything
                    # else blocks (fail-closed: no proof).
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
        if proven:
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


# Mode names that can prefix a Nerdle header logo line
# ("maxi nerdle. #1368"). Any OTHER word before "nerdle" ("the",
# "play", ...) is chrome wording, NOT a mode prefix — it counts as
# no prefix (classic's header), never as another mode's header.
_NERDLE_HEADER_MODE_PREFIXES = frozenset(
    ("classic", "micro", "mini", "midi", "maxi", "minibi", "quad",
     "speed", "instant"))


def _nerdle_own_header_from_probe(probe, mode_id=None):
    """The mode's OWN header logo line (iter 15, P0; iter 16 relaxed).

    Frame-proven format (ANALYSIS11, gameplay g_20 / g_140): the page
    header carries `<mode> nerdle. #<n>` — classic `nerdle. #1732`,
    maxi `maxi nerdle. #1368`. The probe's `own_headers` list carries
    one {tag, cls, text} dict per element whose text contains such a
    logo line (see SERVED_JS). Returns (number_or_None, evidence_dict
    or None). When mode_id is given, ONLY an entry whose mode prefix
    matches mode_id (or no prefix, which IS classic's own header)
    counts — a bare `nerdle. #1733` on the mini page is classic's
    number leaking through shared chrome, NOT mini's header, and is
    rejected. Pure (unit-tested).

    Iter-16 (ANALYSIS12 §2): the iter-15 matcher was a FULL-LINE
    anchor (`^...$` per line) — it captured NOTHING on all 9 modes
    even though frames show the headers, because the logo line in
    the DOM is never alone on its line: it sits beside nav text,
    or "nerdle." and "#1733" are split across lines, or the text
    comes from textContent (innerText empty for the logo node).
    The matcher is now a SEARCH on the whole element text
    (`nerdle.?` + whitespace + `#` + digits, anywhere), and a
    prefix only counts as a mode prefix when it IS a known mode
    name — other leading words ("the nerdle. #1733") are chrome
    wording, not a mode claim.

    Iter-17 (ANALYSIS13 §1, P0): the iter-16 pattern STILL missed
    every header because the real DOM text is `nerdle . #1733` —
    a SPACE BEFORE the dot. The pattern `nerdle\.?` requires the
    dot immediately after `nerdle`, so it matched nothing on the
    nav logo line in EITHER matcher (this one or SERVED_JS).
    Fixed: `nerdle\s*\.?\s*#` — optional whitespace before the
    optional dot — which matches all five observed formats
    (`nerdle . #1733`, `micro nerdle . #1506`,
    `maxi nerdle . #1368`, `quad nerdle #1620`,
    `nerdle. #1733`).
    """
    import re as _re
    probe = probe if isinstance(probe, dict) else {}
    want = str(mode_id or "").strip().lower()
    own = []
    for ent in (probe.get("own_headers") or []):
        if not isinstance(ent, dict):
            continue
        # Iter-20 (P0): a GENUINE popup trip (dialog/modal/popup/
        # about/overlay) is never the game header — drop the
        # entry even when it carries a logo line (a win popup can
        # echo the header). An 'aria-hidden' trip is NOT a popup:
        # the game wrapper itself carries aria-hidden while a win
        # popup is open, so an aria-hidden-tripped header still
        # counts (untripped preferred, tripped fallback, below).
        # A missing trip (legacy probe) is collected — collection
        # fails OPEN; the number gate still fails closed on a
        # missing header.
        _trip = str(ent.get("popup_trip") or "").strip().lower()
        if _trip in ("dialog", "modal", "popup", "about", "overlay"):
            continue
        text = str(ent.get("text") or "")
        # Iter-19 (P2, minibi header scope): the site spells the
        # mode "mini-bi" (its About popup reads 'About mini-bi
        # nerdle'), so a minibi header line reads 'mini-bi nerdle .
        # #<n>'. The prefix matcher below only knows "minibi" — an
        # un-normalized line yields prefix "bi", which matches
        # nothing, so minibi's own header was rejected as chrome
        # and the probe fell back to popup/about text. Normalize
        # the hyphenated (and spaced) spelling before matching.
        text = _re.sub(r"mini\s*-\s*bi", "minibi", text,
                       flags=_re.IGNORECASE)
        text = _re.sub(r"mini\s+bi(?=\s+nerdle)", "minibi", text,
                       flags=_re.IGNORECASE)
        # finditer, not search: one container entry can hold SEVERAL
        # mode logo lines (nav listing every variant); the mode's own
        # line may not be the first match in that entry.
        for m in _re.finditer(
                r"([a-z]+)?\s*nerdle\s*\.?\s*#\s*(\d{1,6})",
                text, _re.IGNORECASE):
            raw_prefix = (m.group(1) or "").lower()
            # Only a KNOWN mode name is a mode prefix; any other word
            # is chrome wording and counts as no prefix (classic).
            prefix = raw_prefix if raw_prefix in _NERDLE_HEADER_MODE_PREFIXES \
                else ""
            own.append({"number": int(m.group(2)),
                        "prefix": prefix,
                        "tag": str(ent.get("tag") or ""),
                        "cls": str(ent.get("cls") or ""),
                        "text": m.group(0).strip(),
                        # Iter-20 (P0): the probe records which
                        # popup selector (if any) trips on this
                        # entry ('aria-hidden' when the win popup
                        # is open and the wrapper is hidden).
                        # Genuine-popup trips are DROPPED below
                        # (a popup copy of a logo line is never
                        # the game header); an 'aria-hidden' trip
                        # still counts — the game wrapper itself
                        # carries aria-hidden while a win popup
                        # is open — with untripped entries
                        # preferred by _pick.
                        "trip": str(ent.get("popup_trip") or "")})
    if not own:
        return None, None
    # Prefer an UNTRIPPED match; fall back to a tripped one only
    # when no untripped match exists (iter-20: the game header
    # itself trips 'aria-hidden' while a win popup is open).
    def _pick(pred):
        for ent in own:
            if pred(ent) and not ent.get("trip"):
                return ent
        for ent in own:
            if pred(ent):
                return ent
        return None
    if want:
        # The mode's own header: prefix == mode, or (classic only)
        # no prefix at all. A different mode's prefix is shared
        # chrome, never the mode's own header.
        _hit = _pick(lambda e: e["prefix"] == want)
        if _hit is not None:
            return _hit["number"], _hit
        if want == "classic":
            _hit = _pick(lambda e: not e["prefix"])
            if _hit is not None:
                return _hit["number"], _hit
        return None, None
    _hit = _pick(lambda e: True)
    if _hit is not None:
        return _hit["number"], _hit
    return None, None


def _nerdle_header_number_from_probe(probe, mode_id=None):
    """Multi-strategy header-number capture (iter 14, P0; iter 15:
    own-header-only when mode_id is given).

    Iter-13's SERVED_JS regex /nerdle\\.\\s*#(\\d+)/i on
    document.body.innerText matched NOTHING on any mode page, so the
    number gate blocked a correct solve on header_number=null. The
    browser-side probe (SERVED_JS) now returns RAW evidence —
    header_text, per-element #<digits> texts, data-* values, URL,
    body text — and this pure function applies the strategies in
    order; first plausible number wins. Returns
    (number_or_None, strategy_name, raw_header_text). Strategies:
    header-element, hash-element, data-attribute, url,
    json-blob-or-body. Plausible = 1..999999. Pure (unit-tested).

    Iter-15: when mode_id is given, ONLY the mode's OWN header logo
    line counts (strategy "own-header"; see
    _nerdle_own_header_from_probe). The iter-14 hash-element strategy
    took the FIRST `#<digits>` in DOM order, which on variant pages
    is shared chrome carrying CLASSIC's number — mini/midi/speed all
    returned identical 1733 for three different puzzles (ANALYSIS11
    trust table). A bare first-match #number from shared chrome is
    NEVER accepted for a mode: no own header -> (None,
    "own-header-not-found", ...) so the gate warns instead of
    blocking on another mode's number. Callers without a mode
    (legacy) keep the iter-14 strategies.
    """
    probe = probe if isinstance(probe, dict) else {}
    header_text = str(probe.get("header_text") or "")
    if mode_id:
        n, ev = _nerdle_own_header_from_probe(probe, mode_id)
        if n is not None and ev is not None:
            raw = (f"tag={ev['tag']} class={ev['cls']} "
                   f"text={ev['text']!r} "
                   f"popup_trip={ev.get('trip') or 'none'}")
            return n, "own-header", raw
        return None, "own-header-not-found", header_text

    def _nums(text):
        out = []
        # Iter-17: `nerdle\s*\.?\s*#` — the logo text is
        # `nerdle . #1733` (space before the dot), not `nerdle.#`.
        for pat in (r"nerdle\s*\.?\s*#\s*(\d+)", r"#\s*(\d+)",
                    r"nerdle\s*(\d+)", r"puzzle\s*#?\s*(\d+)",
                    r"puzzleNumber\"?\s*[:=]\s*\"?(\d+)",
                    r"puzzle_number\"?\s*[:=]\s*\"?(\d+)"):
            for m in re.finditer(pat, str(text or ""), re.IGNORECASE):
                try:
                    n = int(m.group(1))
                except (ValueError, IndexError):
                    continue
                if 1 <= n <= 999999 and n not in out:
                    out.append(n)
        return out

    # 1. Header element directly (header/h1/h2, header/title classes).
    got = _nums(header_text)
    if got:
        return got[0], "header-element", header_text
    # 2. Elements containing '#<digits>'-like text.
    for el_text in (probe.get("hash_texts") or []):
        got = _nums(el_text)
        if got:
            return got[0], "hash-element", header_text
    # 3. Puzzle number in data-* attributes.
    for val in (probe.get("data_attrs") or []):
        got = _nums(str(val))
        if got:
            return got[0], "data-attribute", header_text
        if re.fullmatch(r"\d{1,6}", str(val or "").strip()):
            return int(str(val).strip()), "data-attribute", header_text
    # 4. URL (only a #number / /number path segment counts — a bare
    # date or build id in the URL must never be mistaken for a
    # puzzle number).
    url = str(probe.get("url") or "")
    m = re.search(r"[#/]puzzle[-_]?number[-_/=](\d+)", url, re.IGNORECASE) \
        or re.search(r"[#](\d{1,6})\b", url)
    if m:
        return int(m.group(1)), "url", header_text
    # 5. JSON blobs / body text (puzzleNumber fields, #<digits>).
    # This also covers the iter-13 pattern (nerdle. #<digits>),
    # which needs no separate step: the #\s*(\d+) pattern above
    # matches it.
    got = _nums(probe.get("body_text") or "")
    if got:
        return got[0], "json-blob-or-body", header_text
    return None, "none", header_text


def _nerdle_speed_matches(site_solutions, official_eqs, boards_n=2):
    """Speed verification (iter 14, P3): BOTH speed equations must
    match the API pair. Iter-13 counted matches=True on ANY overlap
    (1/2 matched) — that is NOT day proof. Returns
    (matches_bool, detail_str): matches is True only when every
    played solution is in the official list AND at least boards_n
    distinct solutions matched; a partial (e.g. 1/2) is recorded
    honestly as matches=False with a 'partial' detail, and speed is
    never counted as day proof on a partial. Pure (unit-tested).
    """
    site = [str(e) for e in (site_solutions or [])]
    official = [str(e) for e in (official_eqs or [])]
    matched = sorted({e for e in site if e in official})
    if not site or not official:
        return False, "no site/API solutions to compare"
    if len(matched) >= max(1, boards_n) and all(e in official for e in site):
        return True, f"{len(matched)}/{len(site)} equations match API (both)"
    return False, (f"partial: {len(matched)}/{len(site)} equations match "
                   f"API (matched={matched}); both required")


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
    """All-mode answers + puzzle numbers from the nerdle-answers
    worker (the site's own published answers) for the target date.

    Returns (date_key, equations, numbers) where equations is
    {mode_id: [equation, ...]} and numbers is {mode_id: [puzzleNumber,
    ...], "classicPuzzleNumber": N}. Fetches ONLY the dated URL —
    NEVER falls back to /today (the old fallback silently substituted
    a different date's answers when the target row was missing).
    On any failure returns (date_key, {}, None): the caller must
    DEFER (no API row = unpublished = no video), never blind-play.
    Public GET only — never /add or /delete.
    """
    target = target or _target_date()
    date_key = target.strftime("%Y-%m-%d")
    try:
        r = requests.get(
            f"https://nerdle-answers.nerdleapi.workers.dev/{date_key}?v=2",
            timeout=20, headers={"User-Agent": "WordSolverX Video"})
        if not r.ok:
            print(f"[nerdle] API no row for {date_key}: HTTP {r.status_code} "
                  f"-> defer (unpublished)")
            return date_key, {}, None
        j = r.json()
        data = (j.get("data") or j) if isinstance(j, dict) else {}
        if not (isinstance(data, dict) and data.get("modes")):
            print(f"[nerdle] API row for {date_key} has no modes -> defer")
            return date_key, {}, None
        m, numbers = {}, {}
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
            if md.get("puzzleNumbers"):
                numbers[md["id"]] = [int(n) for n in md["puzzleNumbers"]]
        if data.get("classicPuzzleNumber") is not None:
            numbers["classicPuzzleNumber"] = int(data["classicPuzzleNumber"])
        print(f"[nerdle] answers via API {date_key}: " +
              ", ".join(f"{k}={len(v)}eq" for k, v in m.items()) +
              f"; classicPuzzleNumber={numbers.get('classicPuzzleNumber')}")
        return date_key, m, numbers
    except Exception as e:
        print(f"[nerdle] API fetch failed for {date_key}: {e} -> defer")
        return date_key, {}, None


async def block_ads(route):
    # pixels only - ad libraries stay (boot gates); visuals hidden by adhide
    try:
        u = route.request.url
        if any(x in u for x in ("user-sync", "usync", "setuid", "criteo",
                                "quantserve", "scorecardresearch", "hotjar",
                                "amplitude", "sentry.io", "clarity.ms")):
            return await route.abort()
    except Exception:
        pass
    return await route.continue_()


FILLERS = {5: "1+2=3", 6: "12/4=3", 7: "1+23=24", 8: "12+34=46", 10: "1+23+45=69"}
CUT_MODES = set()   # modes that failed verify; their chapters are cut

# --- Iteration 3: artifact debug log / served-date / chapter helpers ---
# play_mode runs in the solver subprocess; the runner only prints the last
# 2000 chars of its stdout (one buffered dump, no timing). Per-mode guess
# lines therefore go through _nlog: timestamped, flushed to stdout AND
# appended to videos/nerdle/debug_<date>.log, which the runner opens in
# append mode — the two writers coexist in the one artifact log.
VIDEO_DIR = None    # set by main() to videos/nerdle (play_mode's scope)
_DEBUG_LOG = None   # Path to videos/nerdle/debug_<date>.log
_REC_T0 = None      # time.time() when the recording/mode loop started
_FIRST_TYPE_T = None  # seconds from _REC_T0 to the first successful typing
_TYPE_PRIMED = False  # type_guess first-attempt priming, reset per mode by play_mode (iter-18)
_PRE_GUESS_DISMISSED = False  # play_mode's pre-guess cookies/modals stage ran for this mode (iter-20)
SERVED = {}         # mode_id -> served-date evidence (see SERVED_JS)
PLAYED = {}         # mode_id -> equations ACTUALLY typed/submitted on site


def equations_report(played_by_mode, official_by_mode):
    """Build the result.json equations block from PLAYED equations.

    `equations` reports what was actually played on the site (the solver's
    typed guesses, which converge on the site's own solutions via game
    feedback) — NEVER the official list when they differ. Rerun #3 reported
    the official answers for 5/9 modes (micro, minibi, quad, speed,
    instant) although the site's solutions were what was played.
    `equations_official` records the target-date official list alongside,
    with a note, so a mismatch is explicit evidence (served puzzle !=
    official list), not a silent substitution. Pure function (unit-tested).
    """
    equations = {}
    official_out = {}
    for mid, played in (played_by_mode or {}).items():
        equations[str(mid)] = [str(e) for e in (played or [])]
        official_out[str(mid)] = [str(e) for e in
                                  ((official_by_mode or {}).get(mid) or [])]
    for mid in (official_by_mode or {}):
        if str(mid) not in equations:
            equations[str(mid)] = []
        if str(mid) not in official_out:
            official_out[str(mid)] = [str(e) for e in
                                      (official_by_mode.get(mid) or [])]
    return {
        "equations": equations,
        "equations_official": official_out,
        "equations_note": (
            "equations = equations ACTUALLY PLAYED on the site "
            "(typed guesses converging on the site's own solutions); "
            "equations_official = target-date official list. When they "
            "differ, the site served a different puzzle than the official "
            "list (see served.matches_target_official) — the played "
            "equations are the true solve, never the official ones."),
    }


def _nlog(msg):
    """Timestamped line -> stdout (flushed, not buffered) + artifact debug log."""
    import datetime as _dt
    line = f"[{_dt.datetime.now().isoformat(timespec='seconds')}] [nerdle] {msg}"
    print(line, flush=True)
    try:
        if _DEBUG_LOG is not None:
            with open(_DEBUG_LOG, "a", encoding="utf-8") as _fh:
                _fh.write(line + "\n")
    except Exception:
        pass


def _mark_first_type():
    """Record the first successful typing (for autocrop's first_typing_s)."""
    global _FIRST_TYPE_T
    if _FIRST_TYPE_T is None and _REC_T0 is not None:
        _FIRST_TYPE_T = time.time() - _REC_T0
        _nlog(f"START->first-typing {_FIRST_TYPE_T:.1f}s")


def compute_nerdle_chapter_starts(segments):
    """Chapter start (pre-intro timeline) per kept mode.

    segments: [(card_dur, gameplay_dur), ...] in final-video order.
    chapter[i] = sum_{j<i} (card_dur_j + gameplay_dur_j) — the accumulator
    fix for the iteration-2 bug where `cursor` never advanced per mode and
    all nine mode chapters landed at 20–40 s of a ~442 s video.
    """
    starts, t = [], 0.0
    for card_dur, seg_dur in segments:
        starts.append(t)
        t += float(card_dur) + float(seg_dur)
    return starts


SERVED_JS = """
() => { try {
  const gs = JSON.parse(localStorage.getItem('gameState') || 'null');
  const keys = (gs && typeof gs === 'object') ? Object.keys(gs) : [];
  const dateish = {};
  for (const k of keys) {
    if (/date|day|puzzle|number|seed/i.test(k)) dateish[k] = gs[k];
  }
  // Iter-14 multi-strategy header probe (P0): iter-13's single regex
  // /nerdle\\.\\s*#(\\d+)/i on body.innerText matched NOTHING on any
  // mode page, so header_number was null everywhere and the gate
  // blocked a correct solve. This probe returns RAW evidence; the
  // Python side (_nerdle_header_number_from_probe) applies the
  // strategies (header-element, hash-element, data-attribute, url,
  // json-blob-or-body) — first plausible number wins — and
  // logs which strategy matched + the raw header text.
  // Iter-20 (P0, ANALYSIS17 §2b/§6.1): the iter-19 `_inPopup`
  // blanket filter REGRESSED the header probe 8/9 -> 0/9: it
  // skipped every element whose closest() matched dialog/modal/
  // popup/about/overlay/aria-hidden — and while a win popup is
  // open the game wrapper itself carries aria-hidden="true"
  // (standard modal pattern: background content hidden from
  // assistive tech), so the GAME HEADER was excluded with the
  // popup. The filter never logged which selector tripped, so
  // the artifact could not name the cause. Now:
  // - `_popupTrip(el, includeAriaHidden)` returns the FIRST
  //   matching selector NAME ('dialog'/'modal'/'popup'/'about'/
  //   'overlay'/'aria-hidden') or null — the probe logs which
  //   selector trips instead of a boolean.
  // - The OWN-HEADER collection applies NO popup filter: its
  //   pattern (`nerdle\s*\.?\s*#\d+`) is strict — an About/win
  //   popup title ('About mini-bi nerdle', 'You won!') carries
  //   no #<digits> logo line, so it cannot pollute own_headers
  //   — and each entry records its popup_trip so the Python
  //   side prefers untripped entries while still accepting a
  //   tripped game header when it is the only match.
  // - The LOOSE header/title selectors (header_text) and the
  //   hash_texts fallback keep the strict filter (dialog/modal/
  //   popup/about/overlay) — that is where About pollution
  //   actually happened for minibi — but WITHOUT aria-hidden:
  //   aria-hidden on a wrapper is not a popup.
  const _POPUP_SELECTORS = [
    ['dialog', '[role="dialog"]'],
    ['modal', '[class*="modal" i]'],
    ['popup', '[class*="popup" i]'],
    ['about', '[class*="about" i]'],
    ['overlay', '[class*="overlay" i]'],
    ['aria-hidden', '[aria-hidden="true"]']];
  const _popupTrip = (el, includeAriaHidden) => { try {
    for (const pair of _POPUP_SELECTORS) {
      if (pair[0] === 'aria-hidden' && !includeAriaHidden) continue;
      if (el.closest(pair[1])) return pair[0];
    }
    return null; } catch (e) { return null; } };
  const _inPopupStrict = (el) => _popupTrip(el, false) !== null;
  const _headerCandidates = Array.from(document.querySelectorAll(
    'header, h1, h2, [class*=header i], [id*=header i], ' +
    '[class*=title i], [id*=title i], [class*=main-nav i]'))
    .filter(e => e && e.offsetParent !== null);
  const headerEls = _headerCandidates.filter(e => !_inPopupStrict(e));
  const header_text = headerEls
    .map(e => (e.innerText || '').trim()).filter(Boolean).join(' | ')
    .slice(0, 2000);
  // Iter-15 own-header probe (P0): the mode's OWN header logo line
  // `<mode> nerdle. #<n>` (frame-proven: classic "nerdle. #1732",
  // maxi "maxi nerdle. #1368"). Collect every visible element whose
  // text contains such a line, with its tag + FULL class + the
  // matched line, so the Python side can accept ONLY the mode's own
  // header and reject classic's number leaking via shared chrome.
  // A container whose innerText merely CONTAINS the line also
  // matches, so the Python side prefers the SMALLEST text (the logo
  // element itself) — entries are ordered shortest-first.
  // Iter-16 (P0) diagnostics + looser match: visibility via
  // getBoundingClientRect, NOT offsetParent — a position:fixed header
  // bar has offsetParent === null in Chrome, so the iter-15
  // offsetParent filter may have silently skipped the very header
  // carrying the logo line (collection-stage failure; the exact DOM
  // cause is unproven, so the probe now returns probe_diag proving
  // which filter excludes what). Text: innerText first, textContent
  // fallback (innerText is empty for some SVG / display:contents
  // nodes). Match: search WITHIN the line, not full-line ^...$ —
  // the Python side applies the own-header prefix discipline on the
  // matched substring.
  const probe_diag = {elements_total: 0, elements_visible_rect: 0,
    elements_offsetparent_visible: 0, header_els_count: headerEls.length,
    logo_selector_matched: false, own_headers_raw_count: 0,
    hash_line_samples: [], nerdle_text_samples: [],
    // Iter-20: which popup selector trips, counted over the
    // scanned elements; own_headers entries tripped by any
    // selector; header candidates the OLD (aria-hidden) filter
    // would have dropped but the strict filter keeps.
    popup_trips: {}, popup_tripped_own_headers: 0,
    header_els_aria_hidden_skipped: _headerCandidates.filter(
      e => _popupTrip(e, false) === null &&
        _popupTrip(e, true) === 'aria-hidden').length};
  const own_headers = [];
  const _all = Array.from(document.querySelectorAll('body *'));
  probe_diag.elements_total = _all.length;
  for (const e of _all) {
    if (!e) continue;
    let visRect = false;
    try { const _r = e.getBoundingClientRect();
      visRect = _r.width > 0 && _r.height > 0; } catch (_err) {}
    if (visRect) probe_diag.elements_visible_rect++;
    if (e.offsetParent !== null)
      probe_diag.elements_offsetparent_visible++;
    if (!visRect) continue;
    const t = (e.innerText || e.textContent || '');
    if (t) {
      if (probe_diag.hash_line_samples.length < 8) {
        for (const l of t.split('\\n')) {
          if (l.includes('#') && /[0-9]/.test(l)) {
            probe_diag.hash_line_samples.push(l.trim().slice(0, 120));
            if (probe_diag.hash_line_samples.length >= 8) break;
          }
        }
      }
      if (probe_diag.nerdle_text_samples.length < 8 && /nerdle/i.test(t)) {
        probe_diag.nerdle_text_samples.push(
          {tag: e.tagName || '', cls: String(e.className || '').slice(0, 80),
           text: t.split('\\n').join(' ').replace(/  +/g, ' ').trim().slice(0, 120)});
      }
    }
    // Iter-20 (P0): NO popup skip for the OWN-HEADER collection
    // (see _popupTrip comment above) — record the trip instead,
    // so the artifact shows which selector trips on the header
    // and the Python side can prefer untripped entries. The
    // diagnostics above stay unfiltered, so a future empty
    // capture is still diagnosable.
    const _trip = _popupTrip(e, true);
    if (_trip) {
      probe_diag.popup_trips[_trip] =
        (probe_diag.popup_trips[_trip] || 0) + 1;
    }
    if (!t || t.length > 300) continue;
    // Iter-18 (ANALYSIS15 §1): the logo spans split `nerdle .` and
    // `#1733` onto SEPARATE innerText lines, so the per-line test
    // below can NEVER match the logo (diagnostics carry the lone
    // `#1733` line + the joined `nerdle . #1733` sample,
    // diagnostics-only). Test the JOINED text (lines joined with a
    // space) as well; the Python side applies the own-header prefix
    // discipline on the matched substring.
    const joined = t.split('\\n').join(' ').replace(/  +/g, ' ').trim();
    const lines = t.split('\\n').filter(l =>
      // Iter-17 (ANALYSIS13 §1): `nerdle\\s*\\.?\\s*#` — the real DOM
      // text is `nerdle . #1733` (SPACE before the dot); the old
      // `nerdle\\.?\\s*#` required the dot immediately after
      // `nerdle` and matched NOTHING on the nav logo line.
      /([a-z]+\\s+)?nerdle\\s*\\.?\\s*#\\s*\\d{1,6}/i.test(l));
    const joinedHit = /([a-z]+\\s+)?nerdle\\s*\\.?\\s*#\\s*\\d{1,6}/i.test(joined);
    if ((lines.length || joinedHit) && own_headers.length < 40) {
      own_headers.push(
        {tag: e.tagName || '', cls: String(e.className || ''),
         text: joinedHit ? joined : lines.join('\\n'),
         popup_trip: _trip || null});
      if (_trip) probe_diag.popup_tripped_own_headers++;
    }
  }
  own_headers.sort((a, b) => a.text.length - b.text.length);
  probe_diag.logo_selector_matched = own_headers.length > 0;
  probe_diag.own_headers_raw_count = own_headers.length;
  const hash_texts = [];
  for (const e of Array.from(document.querySelectorAll('body *'))) {
    if (hash_texts.length >= 20) break;
    if (!e) continue;
    let visRect = false;
    try { const _r = e.getBoundingClientRect();
      visRect = _r.width > 0 && _r.height > 0; } catch (_err) {}
    if (!visRect) continue;
    // Iter-20 (P0): strict popup scoping for the hash_texts
    // fallback (dialog/modal/popup/about/overlay) — a win-popup
    // '#<digits>' must never feed the legacy strategies — but
    // WITHOUT aria-hidden (see _popupTrip comment above).
    if (_inPopupStrict(e)) continue;
    const t = (e.innerText || e.textContent || '').trim();
    if (t && /#\\s*\\d/.test(t) && t.length <= 200) hash_texts.push(t);
  }
  const data_attrs = [];
  for (const e of Array.from(document.querySelectorAll('body *'))) {
    if (data_attrs.length >= 20) break;
    if (!e || !e.dataset) continue;
    for (const k of Object.keys(e.dataset)) {
      if (/puzzle|number|game|day/i.test(k) && e.dataset[k])
        data_attrs.push(k + '=' + e.dataset[k]);
    }
  }
  return { page_local: new Date().toString(),
           page_iso: new Date().toISOString(),
           tz_offset_min: new Date().getTimezoneOffset(),
           header_probe: { header_text: header_text,
                           own_headers: own_headers,
                           hash_texts: hash_texts,
                           data_attrs: data_attrs,
                           probe_diag: probe_diag,
                           url: window.location.href,
                           body_text: (document.body.innerText || '')
                             .slice(0, 8000) },
           useLocalTime: (() => { try {
             return localStorage.getItem('useLocalTime'); }
             catch (e) { return null; } })(),
           gameState_keys: keys, dateish: dateish };
} catch (e) { return { error: String(e) }; } }
"""

def _solutions_played_cover(played, solutions):
    """True when every solution in `solutions` was played (iter-19,
    P2, ANALYSIS16 §6.13). Pure. A played row counts via
    _row_matches_equation, so the DOM-normalized plain-2 form of a
    superscript equation (row "12+67+8=76" for "1²+67+8=76") still
    counts. Empty `solutions` (unknown) counts as covered — the
    caller falls back to the per-board grid check alone.
    """
    played = [str(p) for p in (played or [])]
    for s in (solutions or []):
        if not any(_row_matches_equation(p, s) for p in played):
            return False
    return True


def _unplayed_pool(pool, played):
    """Site solutions not yet played (iter-20, P0).

    Pure. A pool solution counts as played when any played row
    matches it via _row_matches_equation (so the DOM-normalized
    plain-2 form of a superscript equation still counts), NOT
    by raw string equality — typed_guesses can hold a read-back
    / seeded form that differs textually from the pool entry.
    Used by the guess loop's pool-drain backstop: a multi-board
    mode must never be abandoned with a site solution unplayed.
    """
    played = [str(p) for p in (played or [])]
    return [s for s in (pool or [])
            if not any(_row_matches_equation(p, s) for p in played)]


def _green_row_count(state):
    """Fully-green (all-feedback-2) rows across all grids. Pure.

    Iter-21 (ANALYSIS18 §6.3): speed's 2 "boards" are 2 fully-green
    rows in ONE grid (the pre-filled starter row + the played
    answer), not 2 `div.pb-grid` elements — `state` never shows a
    2nd grid. Counting green rows is how the single-grid flow is
    reconciled with the 2-board model.
    """
    n = 0
    for g in (state or []):
        for r in (g.get("rows") or []):
            if r and all(v == 2 for v in r):
                n += 1
    return n


def _mode_won_by_state(state, boards_n, mode_id=None):
    """State-only win: every grid solved, or (speed only) enough
    fully-green rows in the single-grid flow. Pure.

    Iter-21: for speed, `len(state) >= boards_n` is never true
    (one grid), so the old state check could never win speed even
    with both solutions green. The green-row count is the
    single-grid equivalent — and it is only a state signal; the
    game-granted win (popup / WIN_JS) is still required by
    _check_win_verified.
    """
    if isinstance(state, list) and state and len(state) >= boards_n \
            and all(grid_solved(g) for g in state):
        return True
    if mode_id == "speed" and isinstance(state, list) and state:
        return _green_row_count(state) >= boards_n
    return False


async def _check_win_verified(page, cols, boards_n, state, played,
                              solutions, mode_id=None):
    """Page-win check with multi-board verification (iter-19, P2,
    ANALYSIS16 §6.13: speed typed only 1 of 2 site solutions yet
    signal='won' rested on the page wonText).

    Single-board modes: WIN_JS as-is (page wonText or a fully-green
    row). Multi-board modes (speed/quad/minibi): the page wonText
    must NEVER win on its own — it can appear while only ONE board
    is solved. A multi-board win additionally requires EVERY grid
    solved AND every known site solution played. Returns False
    (never raises) on probe failure. `solutions` is the SITE
    solution pool (answer files + gameState), NOT the merged
    solver pool — padded worker guesses are not solutions and must
    never gate a win.

    Iter-21 (ANALYSIS18 §6.3, speed single-grid flow): for speed
    ONLY, the 2-board demand is reconciled with the site's actual
    flow — one grid holding 2 fully-green rows (pre-filled
    submitted starter + played answer). A speed win is verified
    when EITHER (a) WIN_JS reports won AND at least `boards_n`
    fully-green rows exist AND every site solution is accounted
    for in `played` (which includes the seeded pre-filled starter
    history), AND every grid in `state` is solved, OR (b) the
    game's own win popup is present AND every grid in `state` is
    solved AND every site solution is accounted for. The popup alone never wins: coverage + solved grids are
    still required, so a win the game did not grant is never
    claimed. Quad/minibi keep the strict every-grid rule.
    """
    try:
        won = bool(await page.evaluate(
            WIN_JS, {"cols": cols, "boards": boards_n}))
    except Exception:
        return False
    if boards_n <= 1:
        return won
    if isinstance(state, list) and len(state) >= boards_n \
            and all(grid_solved(g) for g in state):
        # Iter-19: no page win means no win — even with every
        # grid solved, WIN_JS must report won.
        if not won:
            return False
        if solutions and not _solutions_played_cover(played, solutions):
            print(f"[nerdle] multi-board win UNVERIFIED: all grids solved "
                  f"but not all site solutions played "
                  f"(played={played} solutions={solutions}) — continuing")
            return False
        return True
    if mode_id == "speed":
        # WIN_JS does NOT gate the popup path: in the real
        # single-grid flow the starter row arrives SUBMITTED but
        # not green (a pre-filled guess, not the solution), so
        # only ONE row is green when the game grants the win and
        # WIN_JS (green rows >= boards) is False. The game's own
        # popup is an INDEPENDENT verdict signal — it is checked
        # even when WIN_JS misses the win.
        _covered = (not solutions
                    or _solutions_played_cover(played, solutions))
        _green = _green_row_count(state)
        # Every grid in `state` must be solved: after an advance
        # reveals a 2nd board, the old win (green rows from board
        # 1) must NOT re-verify while board 2 is still unsolved
        # — the loop keeps playing until all boards are solved.
        _all_solved = bool(state) and all(
            grid_solved(g) for g in state)
        if won and _green >= boards_n and _covered and _all_solved:
            print(f"[nerdle] speed win VERIFIED (single-grid flow): "
                  f"{_green} fully-green rows >= {boards_n} boards, "
                  f"all site solutions accounted for")
            return True
        # (b) The game granted the win (popup present): accept the
        # popup as the verdict signal ONLY with every grid solved
        # + coverage. The popup alone never wins.
        _popup = False
        try:
            _popup = bool(await page.evaluate(WIN_POPUP_JS))
        except Exception:
            _popup = False
        if _popup and _all_solved and _covered:
            print(f"[nerdle] speed win VERIFIED (game win popup): "
                  f"popup present + all grids solved + all site "
                  f"solutions accounted for")
            return True
        print(f"[nerdle] speed win UNVERIFIED: page reports won={won} "
              f"but single-grid flow not satisfied (green_rows={_green} "
              f"boards={boards_n} covered={_covered} popup={_popup} "
              f"all_solved={_all_solved}) "
              f"— continuing")
        return False
    if not won:
        return False
    print(f"[nerdle] multi-board win UNVERIFIED: page reports "
          f"won but not all {boards_n} grids solved — continuing")
    return False


WIN_POPUP_JS = """
// Iter-21 (SPEED_WIN_POPUP): the game's own win popup, as a
// verdict signal for speed's single-grid flow. Matches a win
// popup/dialog/modal carrying the game's win text — NOT bare
// page text (a stale "you won" line elsewhere must not count).
// Returns true iff such a popup element is visible.
() => {
  const re = /you won|won in \\d|great job/i;
  const vis = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    if (!r || r.width <= 0 || r.height <= 0) return false;
    try {
      const cs = getComputedStyle(el);
      if (cs && (cs.display === 'none' || cs.visibility === 'hidden'))
        return false;
    } catch (e) {}
    return true;
  };
  for (const el of document.querySelectorAll(
      '[role="dialog"], [role="alertdialog"], .modal, .popup, '
      + '[class*="modal"], [class*="popup"], [class*="Modal"]')) {
    if (vis(el) && re.test(el.innerText || '')) return true;
  }
  return false;
}
"""

SPEED_ADVANCE_JS = """
// Iter-21 (SPEED_ADVANCE): look for a "Next"-style advance
// button INSIDE the win popup. Returns {popup, next_label} —
// the label of the first visible Next/Continue button, or null.
// NEVER matches X/close/share/play-again: advancing is not
// dismissing, and restarting is not advancing.
() => {
  const re = /you won|won in \\d|great job/i;
  const vis = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    if (!r || r.width <= 0 || r.height <= 0) return false;
    try {
      const cs = getComputedStyle(el);
      if (cs && (cs.display === 'none' || cs.visibility === 'hidden'))
        return false;
    } catch (e) {}
    return true;
  };
  let popup = null;
  for (const el of document.querySelectorAll(
      '[role="dialog"], [role="alertdialog"], .modal, .popup, '
      + '[class*="modal"], [class*="popup"], [class*="Modal"]')) {
    if (vis(el) && re.test(el.innerText || '')) { popup = el; break; }
  }
  if (!popup) return {popup: false, next_label: null};
  const scope = popup;
  for (const b of scope.querySelectorAll('button, a')) {
    if (!vis(b)) continue;
    const t = (b.textContent || '').trim();
    if (/^(next|continue|next board|play next|next puzzle)\\b/i.test(t))
      return {popup: true, next_label: t};
  }
  return {popup: true, next_label: null};
}
"""

SPEED_ADVANCE_CLICK_JS = """
// Iter-21 (SPEED_ADVANCE): click the Next-style button inside
// the win popup (same match as SPEED_ADVANCE_JS). Returns the
// label clicked, or null.
(label) => {
  const vis = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    if (!r || r.width <= 0 || r.height <= 0) return false;
    try {
      const cs = getComputedStyle(el);
      if (cs && (cs.display === 'none' || cs.visibility === 'hidden'))
        return false;
    } catch (e) {}
    return true;
  };
  const re = /you won|won in \\d|great job/i;
  for (const el of document.querySelectorAll(
      '[role="dialog"], [role="alertdialog"], .modal, .popup, '
      + '[class*="modal"], [class*="popup"], [class*="Modal"]')) {
    if (!vis(el) || !re.test(el.innerText || '')) continue;
    for (const b of el.querySelectorAll('button, a')) {
      if (!vis(b)) continue;
      if ((b.textContent || '').trim() === label) {
        b.click();
        return label;
      }
    }
  }
  return null;
}
"""

SPEED_LOAD_DUMP_JS = """
// Iter-21 (SPEED_LOAD_DUMP): dump EVERY storage key (not just
// the 5 restore-gate keys) + all grid states on speed load.
// The restore gate read keys=[] guesses=0 while the video
// showed a filled row — the gate reads a DIFFERENT store than
// the renderer, or the starter row is site-provided rather
// than restored. This dump names the true store.
() => {
  const dumpStore = (store) => {
    const out = [];
    try {
      for (let i = 0; i < store.length; i++) {
        const k = store.key(i);
        let v = null;
        try { v = store.getItem(k); } catch (e) {}
        out.push({key: k, len: (v || '').length,
                  preview: (v || '').slice(0, 300)});
      }
    } catch (e) {}
    return out;
  };
  const grids = [];
  for (const g of document.querySelectorAll('div.pb-grid')) {
    const texts = [];
    for (const r of g.children) {
      const cells = [];
      for (const c of r.children) {
        if ((c.className || '').toString().includes('keyboard-cell'))
          cells.push(c);
      }
      if (cells.length) texts.push(
        Array.from(cells).map(
          (c) => (c.innerText || c.textContent || '').trim()).join(''));
    }
    grids.push({rows: texts});
  }
  const t = (document.body.innerText || '');
  return {local: dumpStore(localStorage),
          session: dumpStore(sessionStorage),
          grids: grids,
          win_popup_present: /you won|won in \\d|great job/i.test(t),
          body_len: t.length};
}
"""


async def _nerdle_speed_load_dump(page):
    """Log full storage + grid states on speed load (iter-21).

    Returns the dump dict or None. Never raises: a failed dump
    is evidence of a probe failure, not a reason to stop play.
    """
    try:
        dump = await page.evaluate(SPEED_LOAD_DUMP_JS)
    except Exception as _e:
        print(f"[speed] load dump failed: {str(_e)[:120]}")
        return None
    if not isinstance(dump, dict):
        print("[speed] load dump unavailable (probe returned non-dict)")
        return None
    _local = dump.get("local") or []
    _grids = dump.get("grids") or []
    print(f"[speed] load dump: localStorage keys="
          f"{[d.get('key') for d in _local]} "
          f"sessionStorage keys="
          f"{[d.get('key') for d in (dump.get('session') or [])]} "
          f"grids={len(_grids)} "
          f"grid_rows={[g.get('rows') for g in _grids]} "
          f"win_popup_present={dump.get('win_popup_present')}")
    for _d in _local:
        print(f"[speed] load dump localStorage[{_d.get('key')!r}] "
              f"len={_d.get('len')} preview={str(_d.get('preview'))[:300]!r}")
    return dump


async def _speed_try_advance(page, cols, prev_state):
    """Try to advance past speed's win popup via its Next button.

    Iter-21 (ANALYSIS18 §6.3): if board 2 requires advancing past
    the win popup, a "Next"/"Continue" click does that — the sweep
    never X-dismisses the win popup, and this helper never clicks
    X/close/share/play-again either. Returns (outcome, new_state):
    "advanced" (Next clicked AND a new/reset board appeared —
    keep playing), "no-next" (win popup has no Next button —
    single-grid flow, game granted the win), "no-popup" (no win
    popup at all), "no-new-board" (Next clicked but no new board
    appeared — single-grid flow confirmed). Never raises.
    """
    try:
        _found = await page.evaluate(SPEED_ADVANCE_JS)
    except Exception as _e:
        print(f"[speed] advance probe failed: {str(_e)[:120]}")
        return "no-popup", prev_state
    if not isinstance(_found, dict) or not _found.get("popup"):
        print("[speed] advance: no win popup present")
        return "no-popup", prev_state
    _label = _found.get("next_label")
    if not _label:
        print("[speed] advance: win popup present, no Next button "
              "(single-grid flow — game granted the win)")
        return "no-next", prev_state
    try:
        _clicked = await page.evaluate(SPEED_ADVANCE_CLICK_JS, _label)
    except Exception as _e:
        print(f"[speed] advance Next click failed: {str(_e)[:120]}")
        return "no-new-board", prev_state
    if not _clicked:
        print("[speed] advance: Next button vanished before click")
        return "no-new-board", prev_state
    print(f"[speed] advance: clicked Next button { _clicked!r} — "
          f"waiting for board 2")
    try:
        await page.wait_for_timeout(1500)
        _new = await read_state(page, cols, tries=3)
    except Exception:
        _new = []
    if _new and (len(_new) > len(prev_state or [])
                 or any(eval_count(_new[b]) < eval_count(prev_state[b])
                        for b in range(min(len(_new),
                                           len(prev_state or []))))):
        print(f"[speed] advance: NEW board appeared "
              f"(grids {len(prev_state or [])} -> {len(_new)}) — "
              f"continuing play")
        return "advanced", _new
    print("[speed] advance: Next clicked but no new board appeared "
          "(single-grid flow confirmed)")
    return "no-new-board", (_new or prev_state)



WIN_JS = """
({cols, boards}) => {
  const t = document.body.innerText || '';
  // Iter-19 (P2, ANALYSIS16 §6.13): for multi-board modes (speed:
  // 2 boards) the page wonText can appear while only ONE board is
  // solved — it must NOT count as a win. The text branch applies
  // to single-board modes only; multi-board modes must show
  // `boards` fully-green rows (the count below).
  if (boards <= 1 && /you won|won in \\d|great job/i.test(t)) return true;
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


async def wait_change(page, cols, prev, timeout=3.0):
    """Poll until evaluated-row counts change (empty reads never count).
    Then settle: one extra re-read after 600ms.

    Iter-18 (ANALYSIS15 §5): default timeout 3.0 (was 8.0) — a
    registered Enter evaluates in ~0.6s, so 8s per failed attempt
    was pure burn (8.1s x 2 failed first attempts). Callers that
    need more (the final retype attempt) pass timeout explicitly."""
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
            try:
                QP.ev_push(box["x"] + 10, box["y"] + box["height"] - 10, "click")
            except Exception:
                pass
            await page.wait_for_timeout(400)
            return
    except Exception:
        pass
    await page.mouse.click(640, 360)
    try:
        QP.ev_push(640, 360, "click")
    except Exception:
        pass
    await page.wait_for_timeout(400)


CONSENT_SELS = [
    "button:has-text('Accept All')", "button:has-text('Reject All')",
    "button:has-text('Accept all')", "button:has-text('Reject all')",
    "button:has-text('Accept')", "button:has-text('Reject')",
    "button:has-text('Agree')", "button:has-text('Allow all')",
    "button:has-text('Got it')", "button:has-text('OK')",
    "#truste-consent-button",
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
                # Iter-18 (ANALYSIS15 §5): element.click() fails when
                # an overlay intercepts pointer events (`div#root
                # intercepts pointer events`) — the consent clicks
                # kept failing while the banner stayed visible. A JS
                # click dispatches without hit-testing, so it still
                # dismisses the banner.
                try:
                    await el.evaluate("(e) => e.click()")
                    print(f"[consent] clicked {sel!r} via JS in {fr.url[:60]}")
                    await page.wait_for_timeout(700)
                    return True
                except Exception:
                    pass
                if not quiet:
                    print(f"[consent] {sel!r} on {fr.url[:40]} failed: {e}")
                continue
    if not quiet:
        print(f"[consent] none ({len(page.frames)} frames)")
    return False


async def dismiss_modals(page, has_grid):
    """Dismiss splash/hint modals. Returns True iff something was
    dismissed (iter-19: the pre-guess stage uses this to decide
    whether a second accept_cookies scan is worth running)."""
    sels = ["button:has-text('play now')", "button:has-text('Play Now')",
            "button:has-text('Accept')", "button:has-text('Got it')",
            "button:has-text('X Disable Hints')", "button:has-text('Disable Hints')",
            "[aria-label='dismiss']", ".fc-cta-consent"]
    for sel in sels:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                try:
                    await el.click(timeout=2000)
                except Exception:
                    # Iter-18: JS-click fallback (same interception
                    # failure as accept_cookies — overlay intercepts
                    # pointer events, JS click bypasses hit-testing).
                    await el.evaluate("(e) => e.click()")
                await page.wait_for_timeout(600)
                return True
        except Exception:
            continue
    return False


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


# Shared power-key matcher (iter-19, P0): the ²/³ on-screen key is
# matched by SEVERAL selectors, not just an exact aria-label.
# Iter-18's exact aria-label match returned clicked=False on the
# SECOND ² in the same session while the X² button was visibly
# present (frame gp_0190) — a single-selector, single-attempt click
# is too brittle against a keyboard that re-renders between
# guesses (transient absence / label variance such as 'x²').
# Candidates, in order: aria-label exact, aria-label 'x²'/'X²',
# short aria-label/text containing the char, button text exact /
# 'x²', data-key/data-char. The short-length guard on the
# contains-matches keeps a '10²'-style label from matching.
#
# Iter-21 (P0, ANALYSIS18 §6.2): the matcher collected ONLY the
# FIRST DOM-order match and both consumers gave up on it: the
# click returned false when that one element had a zero rect,
# never trying the other matches. A hidden/stale/zero-rect
# duplicate (keyboard re-render between guesses) therefore hid
# the VISIBLE X² button. Now the matcher collects ALL matches
# (`_cands`) and every consumer iterates them, clicking / boxing
# the first VISIBLE + ENABLED one. The candidate dump JS
# (POWER_BTN_CANDIDATES_JS) lists every match for failure logs.
_POWER_BTN_MATCH_JS = """
  const norm = (s) => (s || '').trim();
  const isPowerBtn = (b) => {
    if (!b) return false;
    const aria = norm(b.getAttribute('aria-label'));
    if (aria === ch) return true;
    if (aria === 'x' + ch || aria === 'X' + ch) return true;
    if (aria.length <= 3 && aria.includes(ch)) return true;
    const txt = norm(b.textContent);
    if (txt === ch || txt === 'x' + ch || txt === 'X' + ch) return true;
    if (txt.length <= 3 && txt.includes(ch)) return true;
    if (b.dataset && (b.dataset.key === ch || b.dataset.char === ch))
      return true;
    return false;
  };
  const _cands = Array.from(document.querySelectorAll('button'))
    .filter(isPowerBtn);
  const _candUsable = (b) => {
    if (!b) return false;
    if (b.disabled) return false;
    if ((b.getAttribute('aria-disabled') || '').toLowerCase() === 'true')
      return false;
    const r = b.getBoundingClientRect();
    if (!r || r.width <= 0 || r.height <= 0) return false;
    try {
      const cs = getComputedStyle(b);
      if (cs && (cs.display === 'none' || cs.visibility === 'hidden'))
        return false;
    } catch (e) {}
    return true;
  };
"""

CLICK_POWER_JS = """
(ch) => {
""" + _POWER_BTN_MATCH_JS + """
  // Iter-21: iterate ALL matches — the first DOM-order match may
  // be a hidden/stale/zero-rect duplicate. Click the first
  // VISIBLE + ENABLED one.
  // Iter-17: NO offsetParent filter — a position:fixed keyboard bar
  // has offsetParent === null in Chrome, so the old filter could
  // hide the very button being sought (same class of bug as the
  // iter-15 header probe). Re-find fresh on every call, scroll into
  // view, and require a non-empty rect before clicking.
  for (const b of _cands) {
    if (!_candUsable(b)) continue;
    try { b.scrollIntoView({block: 'center'}); } catch (e) {}
    if (!_candUsable(b)) continue;
    b.click();
    return true;
  }
  return false;
}
"""

POWER_BTN_BOX_JS = """
(ch) => {
""" + _POWER_BTN_MATCH_JS + """
  // Iter-21: iterate ALL matches (same first-match trap as the
  // click) — return the first VISIBLE + ENABLED one's centre.
  for (const b of _cands) {
    if (!_candUsable(b)) continue;
    try { b.scrollIntoView({block: 'center'}); } catch (e) {}
    if (!_candUsable(b)) continue;
    const r = b.getBoundingClientRect();
    return {x: r.x + r.width / 2, y: r.y + r.height / 2,
            w: r.width, h: r.height};
  }
  return null;
}
"""

POWER_BTN_CANDIDATES_JS = """
// Iter-21 (POWER_BTN_CANDIDATES): every power-key match with the
// fields that decide clickability — so a failure log names the
// blocker (hidden duplicate, zero rect, disabled) instead of
// just clicked=False.
(ch) => {
""" + _POWER_BTN_MATCH_JS + """
  return _cands.map((b) => {
    const r = b.getBoundingClientRect();
    let display = null, visibility = null;
    try {
      const cs = getComputedStyle(b);
      display = cs ? cs.display : null;
      visibility = cs ? cs.visibility : null;
    } catch (e) {}
    return {tag: b.tagName,
            text: (b.textContent || '').trim().slice(0, 24),
            aria: b.getAttribute('aria-label'),
            rect: {x: Math.round(r.x), y: Math.round(r.y),
                   w: Math.round(r.width), h: Math.round(r.height)},
            disabled: !!b.disabled,
            aria_disabled: b.getAttribute('aria-disabled'),
            display: display, visibility: visibility,
            usable: _candUsable(b)};
  });
}
"""


# Equations whose full ²/³ entry ladder already failed once, PER
# PAGE (iter-21, P2 ladder cap). A full ladder = all four entry
# methods (js-click + coordinate + clipboard + insert_text) with
# fresh re-queries, each verified by the tile/sup delta.
# Re-running the identical ladder after it exhausted is
# deterministically futile (~27s per type_guess repeat x 4
# repeats = ~108s burned in iter-20) — the cap makes the second
# and later attempts fail fast instead. Keyed by (page,
# equation): a module-global per-equation set would leak across
# modes AND across tests sharing an equation (a failing test
# for '1²+67+8=76' would cap every later caller of that
# equation), so the set lives per page object (WeakKeyDictionary
# — Playwright Page has no __slots__, so it is weakrefable —
# auto-cleaned when the page is dropped) and play_mode resets
# it for its page at mode start (one page serves all 9 modes).
# Plain (non-superscript) equations never enter the set, so the
# site-solution retry still rescues them. No `global` statement
# is used anywhere (type_equation holds no global entry state).
_POWER_LADDER_FAILED_BY_PAGE = weakref.WeakKeyDictionary()


def _ladder_failed_set(page):
    """The ² ladder-cap set for `page` (iter-21, P2).

    Returns the live per-page set, creating it on first use.
    Falls back to a fresh (uncapped) set for an unweakrefable
    page rather than crashing — the cap simply never engages
    there instead of breaking entry.
    """
    try:
        _s = _POWER_LADDER_FAILED_BY_PAGE.get(page)
        if _s is None:
            _s = set()
            _POWER_LADDER_FAILED_BY_PAGE[page] = _s
        return _s
    except TypeError:
        return set()


async def _power_button_candidates(page, ch):
    """All power-key matches with clickability fields (iter-21).

    Pure probe — never raises, returns [] on failure.
    """
    try:
        cands = await page.evaluate(POWER_BTN_CANDIDATES_JS, ch)
        if isinstance(cands, list):
            return cands
    except Exception:
        pass
    return []


async def _dump_power_candidates(page, ch):
    """Log every power-key candidate on entry failure (iter-21).

    The iter-20 log said only `clicked=False` while the X² button
    was visibly rendered — the next artifact must show WHICH
    element blocked the click (hidden duplicate, zero rect,
    disabled) instead. Never raises.
    """
    try:
        cands = await _power_button_candidates(page, ch)
    except Exception:
        cands = []
    print(f"[type] power key {ch!r} candidates ({len(cands)}): "
          f"{str(cands)[:2000]}")
    return cands


async def _click_power_key(page, ch, attempts=4, wait_ms=200):
    """Click the on-screen power key `ch` (²/³), re-querying FRESH on
    every attempt (iter-19, P0, ANALYSIS16 §2b).

    Iter-18's ladder called CLICK_POWER_JS once per method: the
    second ² in a session returned clicked=False while the X²
    button was visibly present, and the ladder fell through to
    coordinate / clipboard / insert_text — which failed the same
    way. The failure is a single-attempt click against a keyboard
    that re-renders between guesses (transient absence / label
    variance), NOT a missing button. So: fresh query per attempt
    (the JS re-queries document on every call — no handle is ever
    reused across guesses), up to `attempts` attempts with a short
    settle wait between them. Returns True on the first reported
    click. Verification still happens in type_equation (tile/sup
    delta); this helper only delivers the click.

    Iter-21 (ANALYSIS18 §6.2): each CLICK_POWER_JS evaluation now
    iterates ALL matches and clicks the first VISIBLE + ENABLED
    one — a hidden/stale/zero-rect first DOM-order match no
    longer hides the visible button.
    """
    for _ in range(max(1, attempts)):
        try:
            if await page.evaluate(CLICK_POWER_JS, ch):
                return True
        except Exception:
            pass
        try:
            await page.wait_for_timeout(wait_ms)
        except Exception:
            pass
    return False


async def _power_button_box(page, ch, attempts=4, wait_ms=200):
    """Bounding-box centre of the on-screen power key `ch`,
    re-queried fresh per attempt (iter-19, P0 — same
    transient-absence cause as _click_power_key). Returns the box
    dict or None.
    """
    for _ in range(max(1, attempts)):
        try:
            box = await page.evaluate(POWER_BTN_BOX_JS, ch)
            if box and box.get("x") is not None:
                return box
        except Exception:
            pass
        try:
            await page.wait_for_timeout(wait_ms)
        except Exception:
            pass
    return None


def _last_evaluated_text(state):
    """Text of the last evaluated row across all grids (or None).

    Pure. The settled row text is the board's own record of what was
    submitted — the read-back source for equations_played (iter-17).
    """
    for g in (state or []):
        rows = g.get("rows") or []
        texts = g.get("texts") or []
        for r in range(len(rows) - 1, -1, -1):
            if _done(rows[r]) and r < len(texts) and (texts[r] or "").strip():
                return texts[r].strip()
    return None


def _readback_played(state, prev):
    """Newly evaluated row text after a guess (or None).

    Pure. Finds the first grid whose evaluated-row count grew vs
    `prev` and returns its last evaluated row's text. `prev` is the
    pre-guess eval counts (same layout as type_guess's `prev`).
    """
    for b, g in enumerate(state or []):
        before = prev[b] if b < len(prev or []) else 0
        if eval_count(g) > before:
            rows = g.get("rows") or []
            texts = g.get("texts") or []
            for r in range(len(rows) - 1, -1, -1):
                if _done(rows[r]) and r < len(texts) \
                        and (texts[r] or "").strip():
                    return texts[r].strip()
    return None


def _entry_missing_superscript(state, eq):
    """True when `eq` needs ²/³ and an IN-PROGRESS row lacks it.

    Pure. Used by type_guess to tell an ENTRY failure (row missing
    the superscript — Enter-only can never fix it) from a SUBMIT
    failure (row complete but unevaluated). No in-progress row ->
    False (nothing to judge; caller falls through to Enter-only).
    """
    need = {c for c in (eq or "") if c in "²³"}
    if not need:
        return False
    for g in (state or []):
        rows = g.get("rows") or []
        texts = g.get("texts") or []
        for r, fb in enumerate(rows):
            if not _done(fb):
                t = texts[r] if r < len(texts) else ""
                if any(c not in (t or "") for c in need):
                    return True
                break
    return False


def _in_progress_row_text(state):
    """Text of the first non-empty, not-yet-evaluated row (or None).

    Pure. The row currently being typed — the pre-Enter verification
    target (iter-18). Evaluated rows are settled guesses, not the
    row in progress.
    """
    for g in (state or []):
        rows = g.get("rows") or []
        texts = g.get("texts") or []
        for r, fb in enumerate(rows):
            if not _done(fb) and r < len(texts) and (texts[r] or "").strip():
                return texts[r].strip()
    return None


def _row_matches_equation(row_text, eq):
    """True when the board row holds exactly the intended equation.

    Pure. Compares against BOTH the raw equation and the
    DOM-normalized form (²->2, ³->3): the DOM stores the exponent as
    a plain 2 in a superscript-styled cell, so a correct row reads
    back as "12+66+8=75" for intended "1²+66+8=75". Whitespace is
    ignored. A double-² row ("1²²+66+8=75") matches NEITHER form.
    """
    if row_text is None or eq is None:
        return False
    row = "".join(str(row_text).split())
    cand = "".join(str(eq).split())
    if row == cand:
        return True
    return row == cand.replace("²", "2").replace("³", "3")


async def _grid_superscript_count(page, ch, cols=None):
    """Count occurrences of `ch` (²/³) in the grid's row texts.

    Iter-17 (ANALYSIS13 §2–§3): the old leaf-cell DOM probe
    (`div.pb-grid *` leaf innerText includes ch) returned 0 even
    when ² was visibly in the row (maxi guess 3: `1²+66+8=75`
    frame-proven, verifier said `cells 0->0`) — a false-alarm
    `² FAILED / row will stall`. The exact DOM cause is unproven
    (leaf innerText may exclude the superscript node), so the
    counter now counts via `read_state` row text — the same reader
    the solver trusts for every eval count — when `cols` is known.
    Falls back to the DOM probe when read_state yields nothing
    (grid not found yet). Returns None when both probes fail
    (unverified, not zero). Callers compare before/after, so the
    count's unit (occurrences across all row texts) only needs to
    be monotone in "² entered".

    Iter-18 (ANALYSIS15 §2): DIAGNOSTIC-ONLY for entry
    verification — the DOM stores the exponent as a plain 2 in a
    superscript-styled cell, so this count NEVER rises on a
    successful entry (it stayed 0->0 on the frame-proven double-²
    row). type_equation gates entry on _grid_superscript_state
    (tile / superscript-styled-cell delta) and logs this count
    only to keep proving the DOM finding on every run.
    """
    if cols:
        try:
            state = await read_state(page, cols, tries=2)
            if state:
                return sum((t or "").count(ch)
                           for g in state
                           for t in (g.get("texts") or []))
        except Exception:
            pass
    try:
        return await page.evaluate("""(ch) => {
          let n = 0;
          for (const e of document.querySelectorAll('div.pb-grid *')) {
            if (e.children.length === 0 &&
                (e.innerText || '').includes(ch)) n++;
          }
          return n;
        }""", ch)
    except Exception:
        return None


SUPERSCRIPT_STATE_JS = """
(cols) => {
  // Iter-18 (ANALYSIS15 §2): the DOM stores a superscript exponent as
  // a PLAIN 2 in a superscript-styled cell — row text contains "2",
  // never U+00B2 — so entry MUST be verified by tile-count delta
  // and/or superscript-styled-cell detection, never by counting ²
  // in row text. Returns totals + per-grid rows in the SAME DOM
  // order/traversal as READ_JS (div.pb-grid -> children rows ->
  // keyboard-cell children with count == cols), so per-row indices
  // align with read_state output.
  const supHit = (el) => {
    const stack = [el];
    while (stack.length) {
      const n = stack.pop();
      if (!n || n.nodeType !== 1) continue;
      const tag = (n.tagName || '').toUpperCase();
      const cls = String(n.className || '').toLowerCase();
      if (tag === 'SUP' || cls.indexOf('sup') !== -1) return true;
      try { const cs = getComputedStyle(n);
        if (cs && cs.verticalAlign === 'super') return true; } catch (e) {}
      if (n.style && n.style.verticalAlign === 'super') return true;
      for (const ch of (n.children || [])) stack.push(ch);
    }
    return false;
  };
  let tiles = 0, sup_cells = 0;
  const grids = [];
  for (const g of document.querySelectorAll('div.pb-grid')) {
    const grows = [];
    for (const r of g.children) {
      const cells = [];
      for (const c of r.children) {
        if ((c.className || '').toString().includes('keyboard-cell')) cells.push(c);
      }
      if (cells.length !== cols) continue;
      let rt = 0, rs = 0;
      const texts = [];
      for (const c of cells) {
        const t = ((c.innerText || '') || (c.textContent || '')).trim();
        texts.push(t);
        if (t) { rt++; tiles++; }
        if (t && supHit(c)) { rs++; sup_cells++; }
      }
      grows.push({text: texts.join(''), tiles: rt, sup_cells: rs});
    }
    grids.push(grows);
  }
  return {tiles: tiles, sup_cells: sup_cells, grids: grids};
}
"""


async def _grid_superscript_state(page, cols):
    """Tile/sup-cell snapshot of the grid (iter-18, ANALYSIS15 §2).

    Returns {tiles, sup_cells, grids} from SUPERSCRIPT_STATE_JS, or
    None when the probe fails. `tiles` = non-empty cells across all
    grids; `sup_cells` = non-empty cells that are superscript-styled
    (<sup>, sup* class, or vertical-align: super on the cell or any
    descendant). `grids` mirrors read_state row order for per-row
    lookups (_current_row_sup_cells). This — NOT a U+00B2 count in
    row text — is the entry verifier: the DOM stores the exponent
    as a plain 2, so a U+00B2 count never rises on a successful entry.
    """
    try:
        st = await page.evaluate(SUPERSCRIPT_STATE_JS, cols)
        if isinstance(st, dict) and "tiles" in st and "grids" in st:
            return st
    except Exception:
        pass
    return None


def _superscript_entry_verified(before, after):
    """True when a superscript entry landed between two snapshots.

    Pure. Verified iff the tile count rose (a new tile appeared) OR
    the superscript-styled-cell count rose (a tile became
    superscript-styled). Either signal alone suffices — a plain-2
    exponent in a sup-styled cell raises both; a tile appearing in
    any form raises tiles. False when either snapshot is missing
    (unverified, not verified).
    """
    if not isinstance(before, dict) or not isinstance(after, dict):
        return False
    try:
        return (int(after.get("tiles", 0)) > int(before.get("tiles", 0))
                or int(after.get("sup_cells", 0))
                > int(before.get("sup_cells", 0)))
    except (TypeError, ValueError):
        return False


def _current_row_sup_cells(state, probe_state):
    """Superscript-styled cells in the first in-progress row.

    Pure. `state` is read_state output; `probe_state` is
    _grid_superscript_state output (per-grid rows in the same DOM
    order as READ_JS, so indices align). Returns the sup_cells of
    the first non-empty, not-yet-evaluated row, or None when the
    probe is unavailable/misaligned. Used by type_guess to tell a
    real ENTRY failure (current row lacks ²) from the plain-2 DOM
    illusion (row text holds "2", no U+00B2, but the cell IS
    superscript-styled).
    """
    grids = probe_state.get("grids") if isinstance(probe_state, dict) else None
    if not isinstance(grids, list):
        return None
    for gi, g in enumerate(state or []):
        rows = g.get("rows") or []
        texts = g.get("texts") or []
        if gi >= len(grids) or not isinstance(grids[gi], list):
            continue
        pgrid = grids[gi]
        for r, fb in enumerate(rows):
            if not _done(fb) and r < len(texts) and (texts[r] or "").strip():
                if r < len(pgrid) and isinstance(pgrid[r], dict):
                    try:
                        return int(pgrid[r].get("sup_cells", 0))
                    except (TypeError, ValueError):
                        return None
                return None
    return None


async def type_equation(page, eq, delay=200):
    _t_eq = time.time()
    print(f"[type] stage type_equation start eq={eq!r}")
    try:
        await page.evaluate("() => { window.__QP_HOLD = 1; window.__QP_HOLD_T = Date.now(); }")
    except Exception:
        pass
    try:
        QP.ev_push(960, 540, "focus")
    except Exception:
        pass
    """Type `eq`, entering ²/³ via the board's own power keys.

    `keyboard.type` silently DROPS superscripts (Maxi's row then holds one
    fewer tile and the guess can never score - the exact failure that lost
    the mode). The site exposes them as buttons whose aria-label is the
    superscript, so split the equation and click those in place.

    Iter-15 (P1, ANALYSIS11 maxi ² stall): the ² keypress in iter-14
    produced NO cell — the 4th row sat half-typed as `1+67+8=76`
    (official minus ²), cursor in the empty 10th cell, never
    submitted. Every power-key press is now VERIFIED (leaf-cell ²
    count before vs after), with a fallback entry method:
    power-key click -> keyboard.type(²) -> keyboard.press(²). A
    still-unverified press is logged explicitly (never silent), so
    the debug log shows the stall point the truncated stdout hid.

    Iter-17 (P0, ANALYSIS13 §2–§3): the iter-15 ladder could NOT
    deliver ² at all — the retry row shows `1+67+8=76` (9 chars,
    no ²) after all three methods. The artifact proves WHY each
    old method fails: power-key click `not found on board`
    (selector/state dependent); `keyboard.type` drops ² (observed
    Playwright behaviour on this page); `keyboard.press(²)` ->
    `Unknown key: "²"` (Playwright has no key mapping for ²).
    The old `keyboard.type` / `keyboard.press` fallbacks are
    therefore REMOVED for superscripts (proven dead ends, not
    fallbacks) and replaced with paths that can actually deliver
    the character, each VERIFIED via the tile-count /
    superscript-styled-cell delta (iter-18; the read_state U+00B2
    count never rises because the DOM stores a plain 2 — see the
    branch comment for the ANALYSIS15 §2 double-² mechanism):
      1. JS click on the re-found aria-label button (re-find fresh
         on every attempt, after any popup/settle — never a stale
         handle);
      2. coordinate click on the same button's bounding-box centre
         via page.mouse (bypasses element-click dispatch issues);
      3. clipboard paste: navigator.clipboard.writeText + Control+V
         (may be permission-gated in headless Chrome — attempted,
         verified, and logged either way, never assumed);
      4. keyboard.insert_text (dispatches an input event without a
         key mapping — works only if the board listens to input
         events; verified, never assumed).
    Typing speed / pacing is UNCHANGED: `delay` still applies to
    plain-text typing; only the superscript entry ladder changed.

    Iter-19 (P0, ANALYSIS16 §2b): the iter-17/18 ladder made ² entry
    work ONCE — maxi guess 3 js-click clicked=True, guess 4
    clicked=False with the X² button visibly present (frame
    gp_0190), 3x ~21s stalls. The click path was a SINGLE attempt
    against a keyboard that re-renders between guesses. Now the
    js-click (and the coordinate box) re-query the X² button FRESH
    before every ² entry — up to 4 attempts with a short settle
    between, never a reused handle/selector across guesses — and
    match by several selectors (aria-label exact / 'x²', button
    text, data-key). Verification is unchanged: tile/sup count
    (sup 0->1), never U+00B2 text count; max one entry per ².
    """
    if not any(c in eq for c in "²³"):
        await page.keyboard.type(eq, delay=delay)
        print(f"[type] stage type_equation done eq={eq!r} "
              f"+{time.time() - _t_eq:.1f}s (plain, no superscript)")
        return
    cols = len(eq)
    for part in re.split(r"([²³])", eq):
        if not part:
            continue
        if part in "²³":
            _t_part = time.time()
            # Iter-21 (P2, ANALYSIS18 §7.8 ladder cap): this
            # equation's FULL ladder already failed once on this
            # page (all four methods, fresh re-queries,
            # tile/sup-verified). The iter-20 run burned ~108s
            # re-running the identical ladder across type_guess
            # + outer retries. Fail fast: skip the method loop
            # entirely; the FAILED branch below logs the cap
            # instead of re-marking/dumping.
            _ladder_failed = _ladder_failed_set(page)
            _ladder_capped = eq in _ladder_failed
            if _ladder_capped:
                print(f"[type] power key {part!r} ladder cap: full "
                      f"ladder already failed once for {eq!r} — "
                      f"skipping re-entry (fail fast)")
            # Iter-18 (ANALYSIS15 §2): the entry verifier is the
            # TILE / SUPERSCRIPT-STYLED-CELL delta between snapshots
            # taken before and after each method — NOT the U+00B2
            # count in row text. The DOM stores the exponent as a
            # plain 2 in a superscript-styled cell, so the U+00B2
            # count NEVER rises: the old ladder treated a successful
            # js-click as unverified and ran the coordinate click,
            # which inserted a SECOND ² (frame-proven row
            # 1²²+66+8=5). The legacy count is still taken and
            # logged, DIAGNOSTIC-ONLY — it must never gate entry.
            # AT MOST ONE entry method runs once a tile appears: the
            # ladder stops at the first verified entry, and if BOTH
            # probes are blind (state probe None AND legacy count
            # None) after a method that reported delivery, it stops
            # anyway (single-entry rule) and lets the pre-Enter
            # full-row verification arbitrate. When EITHER probe can
            # still see the row, its verdict governs — a performed
            # click that placed no tile (legacy count unchanged)
            # must fall through to the next method.
            before = await _grid_superscript_count(page, part, cols=cols)
            _before_state = await _grid_superscript_state(page, cols)
            verified = False
            after = before
            _after_state = _before_state
            _exhausted = True
            for _method in (() if _ladder_capped else
                            ("js-click", "coordinate click",
                             "clipboard paste", "insert_text")):
                _delivered = False
                if _method == "js-click":
                    # Method 1: JS click via _click_power_key —
                    # iter-19 (P0): the button is re-queried FRESH
                    # on every attempt (up to 4, short settle
                    # between), never a single-attempt click and
                    # never a reused handle/selector across guesses.
                    # Iter-18's single attempt returned
                    # clicked=False on the 2nd ² while the X² button
                    # was visibly present.
                    _delivered = bool(
                        await _click_power_key(page, part))
                    print(f"[type] stage superscript {part!r} js-click "
                          f"clicked={_delivered} "
                          f"+{time.time() - _t_part:.1f}s")
                elif _method == "coordinate click":
                    # Method 2: coordinate click on the button's
                    # centre (iter-19: box also re-queried fresh
                    # per attempt via _power_button_box).
                    try:
                        _box = await _power_button_box(page, part)
                        if _box and _box.get("x") is not None:
                            await page.mouse.click(_box["x"], _box["y"])
                            try:
                                QP.ev_push(_box["x"], _box["y"], "click")
                            except Exception:
                                pass
                            _delivered = True
                    except Exception as _e:
                        print(f"[type] power key {part!r} coordinate "
                              f"click failed: {_e}")
                elif _method == "clipboard paste":
                    # Method 3: clipboard paste.
                    try:
                        await page.evaluate(
                            "(t) => navigator.clipboard.writeText(t)", part)
                        await page.keyboard.press("Control+V")
                        _delivered = True
                    except Exception as _e:
                        print(f"[type] power key {part!r} clipboard "
                              f"paste failed: {_e}")
                else:
                    # Method 4: insert_text (input event, no key).
                    try:
                        await page.keyboard.insert_text(part)
                        _delivered = True
                    except Exception as _e:
                        print(f"[type] power key {part!r} "
                              f"insert_text failed: {_e}")
                await page.wait_for_timeout(150)
                _after_state = await _grid_superscript_state(page, cols)
                after = await _grid_superscript_count(page, part, cols=cols)
                _new_ok = _superscript_entry_verified(
                    _before_state, _after_state)
                _legacy_ok = (before is not None and after is not None
                              and after > before)
                if _new_ok or _legacy_ok:
                    print(f"[type] power key {part!r} verified in row "
                          f"text via {_method} (tile/sup "
                          f"{_before_state and _before_state.get('tiles')}->"
                          f"{_after_state and _after_state.get('tiles')} "
                          f"sup "
                          f"{_before_state and _before_state.get('sup_cells')}->"
                          f"{_after_state and _after_state.get('sup_cells')}; "
                          f"U+00B2 count {before}->{after} diagnostic-only)")
                    verified = True
                    _exhausted = False
                    break
                if _after_state is None and after is None \
                        and _delivered:
                    print(f"[type] power key {part!r} BOTH probes "
                          f"blind after {_method} (reported "
                          "delivery) — stopping ladder (single-entry "
                          "rule); pre-Enter row verification will "
                          "arbitrate")
                    _exhausted = False
                    break
                print(f"[type] power key {part!r} unverified after "
                      f"{_method} (delivered={_delivered} tile/sup "
                      f"{_before_state and _before_state.get('tiles')}->"
                      f"{_after_state and _after_state.get('tiles')} "
                      f"sup "
                      f"{_before_state and _before_state.get('sup_cells')}->"
                      f"{_after_state and _after_state.get('sup_cells')}; "
                      f"U+00B2 count {before}->{after}); trying next "
                      "method")
            if not verified and _exhausted:
                if _ladder_capped:
                    print(f"[type] power key {part!r} still failed "
                          f"(ladder cap — failure already recorded "
                          f"for {eq!r}; no re-entry)")
                else:
                    # Iter-21: record the full-ladder failure so
                    # later attempts fail fast (ladder cap), and
                    # dump EVERY candidate so the artifact names
                    # the blocker instead of just clicked=False.
                    _ladder_failed.add(eq)
                    print(f"[type] power key {part!r} FAILED after all "
                          "entry methods (js-click + coordinate click + "
                          "clipboard paste + insert_text); "
                          f"U+00B2 count {before}->{after} — row will "
                          "stall; ladder cap engaged (no re-entry)")
                    await _dump_power_candidates(page, part)
            print(f"[type] stage superscript {part!r} done "
                  f"verified={verified} +{time.time() - _t_part:.1f}s")
            await page.wait_for_timeout(120)
        else:
            await page.keyboard.type(part, delay=delay)
    print(f"[type] stage type_equation done eq={eq!r} "
          f"+{time.time() - _t_eq:.1f}s")


async def _verify_row_or_retype(page, eq, cols):
    """Verify the in-progress row holds exactly `eq` before Enter.

    Iter-18 (ANALYSIS15 §2): the old code pressed Enter on whatever
    the ladder left in the row — a double-² row (1²²+66+8=5) or a
    row with a lost keystroke (missing 7) was submitted as-is and
    could never evaluate. Now: read the in-progress row; if it
    matches `eq` (raw or DOM-normalized ²->2 / ³->3), proceed. On
    mismatch, clear + retype the WHOLE row once and re-check. If it
    still mismatches, log loudly and return False — the caller still
    presses Enter (the board's row is the game's truth; the
    read-back will record it), but the artifact now shows the
    pre-Enter row, which attributes a later read-back mismatch to
    ENTRY (row already wrong before Enter, e.g. the midi
    1-1+5=5 -> 5+1-1=5 divergence, ANALYSIS15 §5) vs SUBMIT.
    Returns True iff the row matches after at most one retype.
    """
    try:
        _st = await read_state(page, cols, tries=2)
    except Exception:
        _st = []
    _row = _in_progress_row_text(_st)
    if _row is None:
        # One settle retry: the row may render a beat after typing.
        await page.wait_for_timeout(400)
        try:
            _st = await read_state(page, cols, tries=2)
        except Exception:
            _st = []
        _row = _in_progress_row_text(_st)
    if _row is None:
        print(f"[type] pre-Enter row check: no in-progress row for "
              f"{eq!r} (unverified — proceeding)")
        return False
    if _row_matches_equation(_row, eq):
        print(f"[type] pre-Enter row check: row={_row!r} matches {eq!r}")
        return True
    print(f"[type] pre-Enter row MISMATCH typed={eq!r} row={_row!r} — "
          "clear + retype whole row")
    # Iter-21 ladder cap: the full ² ladder already failed once
    # for this equation — a clear + retype would just re-run the
    # identical ladder. Fail fast; the caller still presses Enter
    # on the board row as-is (read-back records it).
    if any(c in eq for c in "²³") and eq in _ladder_failed_set(page):
        print(f"[type] pre-Enter retype SKIPPED (ladder cap): full "
              f"² ladder already failed for {eq!r} — submitting "
              f"board row as-is")
        return False
    try:
        await focus_grid(page)
        for _ in range(cols + 2):
            await page.keyboard.press("Backspace")
        await type_equation(page, eq)
    except Exception as _e:
        print(f"[type] pre-Enter retype failed: {_e}")
        return False
    try:
        _st2 = await read_state(page, cols, tries=2)
    except Exception:
        _st2 = []
    _row2 = _in_progress_row_text(_st2)
    if _row2 is not None and _row_matches_equation(_row2, eq):
        print(f"[type] pre-Enter row check after retype: row={_row2!r} "
              f"matches {eq!r}")
        return True
    print(f"[type] pre-Enter row STILL mismatched after retype "
          f"typed={eq!r} row={_row2!r} — submitting board row as-is "
          "(read-back will record it)")
    return False


async def type_guess(page, eq, cols, prev):
    """Type one equation + Enter with a retry ladder.
    Returns (state, counts, ok); ok only when evaluated-row counts changed.

    Iter-17 (P1, ANALYSIS13 §4): stage timing INSIDE the type path.
    Iter-16 localized a 22s silence between `solver_suggest done`
    and the first keystroke to this function (or an unlogged sleep
    before it) but logged nothing inside it. Every step below logs
    its elapsed time so the next run names the cause. Typing speed
    / pacing UNCHANGED — logging only. No sleep/wait was removed:
    the waits in this path (wait_change polls, 150/120ms settles)
    are accounted for by the stage logs, and any single step that
    eats seconds will now show up by name.
    """
    _t_tg = time.time()

    async def done(state):
        counts = [eval_count(g) for g in state]
        ok = bool(state) and (
            len(counts) != len(prev)
            or any(counts[i] != prev[i] for i in range(len(counts))))
        return state, counts, ok

    print(f"[type] stage type_guess start eq={eq!r}")
    # Iter-18 (P1, ANALYSIS15 §5): the first-guess failures
    # (attempts 1–2 ok=False counts=[0]) correlate with NO focus +
    # a still-visible cookie banner / hints popup: attempt1 typed
    # into an unfocused board (keystrokes dropped, row empty,
    # Enter no-op), attempt2 Enter-only could never submit an
    # empty row, and only retry2 — the first path that calls
    # focus_grid — succeeded. So BEFORE the first typing attempt
    # on a page: dismiss popups/consent, then focus the grid.
    # Once per mode (reset by play_mode): later guesses on the same
    # page are already focused with the banner dismissed. Typing
    # speed / pacing UNCHANGED (no delay value touched).
    global _TYPE_PRIMED
    if not _TYPE_PRIMED:
        _TYPE_PRIMED = True
        await sweep_popups(page, tag=":type_guess")
        await accept_cookies(page, quiet=True)
        await dismiss_modals(page, has_grid=True)
        await focus_grid(page)
    try:
        _t = time.time()
        await type_equation(page, eq)
        print(f"[type] stage type_equation call done eq={eq!r} "
              f"+{time.time() - _t:.1f}s (total +{time.time() - _t_tg:.1f}s)")
        # Iter-18: verify the FULL row == intended equation BEFORE
        # Enter; on mismatch, clear + retype the whole row (once).
        await _verify_row_or_retype(page, eq, cols)
        _t = time.time()
        await page.keyboard.press("Enter")
        print(f"[type] stage Enter press done eq={eq!r} "
              f"+{time.time() - _t:.1f}s (total +{time.time() - _t_tg:.1f}s)")
    except Exception as e:
        print(f"[type] {eq!r} failed: {e}")
        return [], [], False
    _t = time.time()
    # Iter-18: fail-fast cap — a registered Enter evaluates in
    # ~0.6s (guesses 2–3); the old 8.0s default burned 8.1s per
    # failed first attempt. 3s is 5x headroom, never a pacing
    # change to successful typing.
    state, counts, ok = await done(await wait_change(page, cols, prev, timeout=3))
    print(f"[type] stage wait_change attempt1 done eq={eq!r} "
          f"+{time.time() - _t:.1f}s (total +{time.time() - _t_tg:.1f}s)")
    print(f"[type] {eq} attempt1 ok={ok} counts={counts}")
    if ok:
        return state, counts, True
    # retry 1: dismiss popups that appeared, flush with Enter alone
    # — UNLESS the row is an ENTRY failure: if the equation needs a
    # superscript and the in-progress row lacks it, Enter-only can
    # never submit an incomplete row (iter-16 burned a full
    # wait_change cycle here); skip straight to clear + re-entry.
    _skip_enter_only = False
    if any(c in eq for c in "²³"):
        try:
            _cur = await read_state(page, cols, tries=2)
            if _entry_missing_superscript(_cur, eq):
                # Iter-18: the pure check reads U+00B2 in row text,
                # but the DOM stores a plain 2 in a
                # superscript-styled cell — a present ² looks
                # "missing". If the probe sees a
                # superscript-styled cell in the CURRENT row, the ²
                # IS entered: this is a submit failure, not an entry
                # failure — keep the Enter-only retry.
                _sup_probe = await _grid_superscript_state(page, cols)
                _cur_sup = _current_row_sup_cells(_cur, _sup_probe)
                if _cur_sup is not None and _cur_sup > 0:
                    print(f"[type] {eq} current row holds a "
                          "superscript-styled cell — ² entered "
                          "(plain-2 DOM), keeping Enter-only retry")
                else:
                    _skip_enter_only = True
                    print(f"[type] {eq} in-progress row lacks superscript — "
                          "ENTRY failure, skipping Enter-only retry")
        except Exception:
            pass
    if not _skip_enter_only:
        await accept_cookies(page)
        try:
            await page.keyboard.press("Enter")
        except Exception:
            pass
        state, counts, ok = await done(await wait_change(page, cols, prev, timeout=3))
        print(f"[type] {eq} attempt2 ok={ok} counts={counts}")
        if ok:
            return state, counts, True
    # retry 2: clear popups, refocus, clear row, retype — BUT never retype an
    # equation that is already sitting in the grid (that printed the same
    # attempt twice on camera in quad mode). If it is there, Enter only.
    # Iter-17: this is the ENTRY retry — clear + re-enter via the full
    # power-key ladder (type_equation), not just a retype of the same
    # dead path. The "already in grid" check compares the FULL
    # equation INCLUDING ² (² is alnum in Python), so a ²-less row
    # (`1+67+8=76`) never counts as the equation being present.
    print(f"[type] stage retry2 clear+retype start eq={eq!r} "
          f"+{time.time() - _t_tg:.1f}s")
    await accept_cookies(page)
    try:
        _st = await read_state(page, cols, tries=2)
        _flat = "".join(
            str(t) for g in (_st or []) for t in (g.get("texts") or []))
        _norm = "".join(ch for ch in _flat if ch.isalnum() or ch in "+-*/=")
        _want = "".join(ch for ch in str(eq) if ch.isalnum() or ch in "+-*/=")
        # Iter-18: normalize ²/³ -> 2/3 — the DOM stores the
        # exponent as a plain 2 in a superscript-styled cell, so a
        # row holding ² reads back as "2" and the raw comparison
        # never matched (² rows were needlessly retyped).
        _norm = _norm.replace("²", "2").replace("³", "3")
        _want = _want.replace("²", "2").replace("³", "3")
        if _want and _want in _norm:
            print(f"[type] {eq} already in grid, Enter only (no retype)")
            try:
                await page.keyboard.press("Enter")
            except Exception:
                pass
            state, counts, ok = await done(await wait_change(page, cols, prev, timeout=5))
            print(f"[type] {eq} enter-only ok={ok} counts={counts}")
            return state, counts, ok
    except Exception:
        pass
    # Iter-21 ladder cap: the full ² ladder already failed once
    # for this equation (attempt1 + pre-Enter retype, if any).
    # Retry 2's clear + retype would re-run the identical ladder
    # (~27s per type_guess repeat in iter-20). Fail fast instead
    # of re-entering a deterministically failing entry.
    if any(c in eq for c in "²³") and eq in _ladder_failed_set(page):
        print(f"[type] {eq} retry2 SKIPPED (ladder cap): full ² "
              f"ladder already failed once — not re-entering")
        print(f"[type] stage type_guess done eq={eq!r} ok=False "
              f"+{time.time() - _t_tg:.1f}s")
        return state, counts, False
    try:
        await page.keyboard.press("Escape")
    except Exception:
        pass
    await focus_grid(page)
    try:
        for _ in range(cols + 2):
            await page.keyboard.press("Backspace")
        await type_equation(page, eq)
        # Iter-18: verify the FULL row before Enter (same as attempt1).
        await _verify_row_or_retype(page, eq, cols)
        await page.keyboard.press("Enter")
    except Exception as e:
        print(f"[type] retry failed: {e}")
        return state, counts, False
    state, counts, ok = await done(await wait_change(page, cols, prev, timeout=5))
    print(f"[type] {eq} attempt3 ok={ok} counts={counts}")
    print(f"[type] stage type_guess done eq={eq!r} ok={ok} "
          f"+{time.time() - _t_tg:.1f}s")
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


# Fix-loop iteration 6: the site restores an already-completed board from
# localStorage gameState/miniGameState when the context loads a committed
# browser_state.json (rerun #5 replayed rerun #4's won boards, typed 0).
# Every mode therefore starts from a FRESH board: probe the restore gate
# (which keys are present, whether the loaded board is already won), clear
# the site's own game-state keys for this origin, and reload so the app
# boots empty. Clearing AFTER load is not enough — the app hydrates its
# board from storage at load — so the reload is the reset, not the clear.
_NERDLE_STATE_KEYS = ("gameState", "miniGameState", "statsState",
                      "deviceStatsState", "timer")

_NERDLE_RESTORE_PROBE_JS = """() => {
  const out = {keys: [], guesses: 0, hasSolution: false, wonText: false};
  for (const k of ['gameState', 'miniGameState', 'statsState',
                   'deviceStatsState', 'timer']) {
    const v = localStorage.getItem(k);
    if (v !== null) {
      out.keys.push(k);
      if (k === 'gameState' || k === 'miniGameState') {
        try {
          const gs = JSON.parse(v);
          const arr = gs && gs.guesses;
          if (Array.isArray(arr)) out.guesses = arr.length;
          if (gs && (gs.solution || gs.gameStatus === 'WIN' ||
                     gs.status === 'WIN')) out.hasSolution = true;
        } catch (e) {}
      }
    }
  }
  const t = (document.body.innerText || '').toLowerCase();
  out.wonText = /you won|won in \\d|great job/.test(t);
  return out;
}"""

_NERDLE_CLEAR_JS = """(keys) => {
  const cleared = [];
  for (const k of keys) {
    if (localStorage.getItem(k) !== null) {
      localStorage.removeItem(k);
      cleared.push(k);
    }
  }
  return cleared;
}"""


async def _nerdle_fresh_board(page, mode):
    """Probe the restore gate, clear site game state, reload fresh.

    Logs which storage keys were present/cleared and the board state on
    load (keys, guess count, won-ish text) so the next artifact proves
    whether the fresh-board reset held. Returns the post-reload probe.
    """
    try:
        before = await page.evaluate(_NERDLE_RESTORE_PROBE_JS)
    except Exception:
        before = {}
    _nlog(f"[{mode['id']}] restore gate on load: keys={before.get('keys')} "
          f"guesses={before.get('guesses')} hasSolution={before.get('hasSolution')} "
          f"wonText={before.get('wonText')}")
    try:
        cleared = await page.evaluate(_NERDLE_CLEAR_JS, list(_NERDLE_STATE_KEYS))
    except Exception:
        cleared = []
    _nlog(f"[{mode['id']}] gameState cleared: {cleared or 'none present'}")
    if cleared:
        try:
            await page.goto(mode["url"], wait_until="domcontentloaded",
                            timeout=45000)
            # Iter-19 (P2, leading-wait trim): the fixed 1500ms
            # post-reload wait was pure idle whenever the grid was
            # already present. Wait for the grid (same 1500ms cap)
            # and proceed the moment it appears.
            try:
                await page.wait_for_selector("div.pb-grid", timeout=1500)
            except Exception:
                await page.wait_for_timeout(400)
        except Exception as _e:
            _nlog(f"[{mode['id']}] fresh-board reload failed: {str(_e)[:100]}")
    try:
        after = await page.evaluate(_NERDLE_RESTORE_PROBE_JS)
    except Exception:
        after = {}
    _nlog(f"[{mode['id']}] board after reset: keys={after.get('keys')} "
          f"guesses={after.get('guesses')} wonText={after.get('wonText')}")
    return after


async def play_mode(page, mode, script_dir, official=None):
    """Solve ONE mode: algorithm first (solver-worker entropy guesses);
    official day's answer only when rows_left <= 2 (or solver is empty).
    Returns (typed_count, expected_count, signal). typed counts probes +
    official guesses; expected counts official equations only — they are
    NOT comparable (see the counts_note in result.json)."""
    _nlog(f"=== {mode['name']} ({mode['url']}) ===")
    # Iter-18: reset the type_guess first-attempt priming (popup
    # dismiss + focus) for this mode's page.
    global _TYPE_PRIMED, _PRE_GUESS_DISMISSED
    _TYPE_PRIMED = False
    _PRE_GUESS_DISMISSED = False
    # Iter-21: fresh ² ladder-cap state per mode (one mode's
    # failure never poisons another mode — one page serves all
    # 9 modes, so the per-page set is reset here), and the
    # seeded board history (pre-filled evaluated rows at load)
    # for win verification — the speed starter row counts toward
    # the played solutions even though the bot did not type it.
    try:
        _POWER_LADDER_FAILED_BY_PAGE[page] = set()
    except TypeError:
        pass
    seeded_history = []
    eqs = list((official or {}).get(mode["id"]) or [])
    official_eqs = list(eqs)  # target-date official list, BEFORE site merge
    cols = mode["len"]
    boards_n = mode.get("boards", 1)
    typed = 0
    signal = ""
    site_eqs = []
    # Look human: 2-4 solver probes before the official answer is allowed,
    # seeded by date so it is stable per day but varies across days.
    import random as _rnd
    _seed = int(os.environ.get("NERDLE_SEED", "0") or 0) or classic_puzzle_number(
        _target_date())
    min_probes = 2 + (_seed % 2) + (1 if boards_n > 1 else 0)
    _nlog(f"[{mode['id']}] min_probes={min_probes}")
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
        # Fresh-board reset (fix-loop iter 6): never replay into a
        # completed board restored from committed storage state.
        await _nerdle_fresh_board(page, mode)
        # Iter-16 (P2): stage timing for the board-reset -> first-guess
        # window. Iter-15 showed a 37s SILENT gap here (board reset
        # done 19:50:35, first guess 19:51:12) with ZERO log lines, so
        # the next artifact could not say what the 37s was. Every
        # stage below logs its elapsed time; the first solver-worker
        # call (blocking POST, timeout 30, retry backoff) is timed
        # because it is the prime suspect for the gap.
        _t_pre = time.time()
        _t_last = [_t_pre]

        def _stage(name):
            _now = time.time()
            _nlog(f"[{mode['id']}] pre-guess stage {name}: "
                  f"+{_now - _t_last[0]:.1f}s "
                  f"(total {_now - _t_pre:.1f}s since board reset)")
            _t_last[0] = _now
        # Fill the frame: the board column is narrow on wide viewports,
        # so zoom per mode (multi-board modes stay at 1.0).
        _zoom = {"classic": 1.2, "micro": 1.35, "mini": 1.3, "midi": 1.25,
                 "maxi": 1.05, "minibi": 1.1, "quad": 1.0, "speed": 1.05,
                 "instant": 1.2}.get(mode["id"], 1.15)
        try:
            await page.evaluate(f"() => {{ document.body.style.zoom = '{_zoom}'; }}")
            print(f"[{mode['id']}] zoom {_zoom}")
        except Exception:
            pass
        _stage("zoom")
        # Iter-16: the old fixed 4x(1s + sweep) loop burned 4s+ of pure
        # idle even when no popup ever appeared. Sweep until two
        # consecutive sweeps report no action (sweep reports acted=True
        # whenever it dismisses anything, including a splash), waiting
        # 1s only after a sweep that acted.
        # Iter-20 (P2, leading-wait trim): ONE idle sweep suffices —
        # a delayed splash/banner is still caught by the escape
        # loop, the cookies/modals stage, and type_guess's priming
        # sweep immediately before the first keystroke. The second
        # consecutive idle sweep was pure re-scan (a full cookie
        # scan per sweep when ad iframes exist).
        _idle_sweeps = 0
        for _ in range(4):
            _acted = await sweep_popups(page, tag=f":{mode['id']}")
            _stage("sweep")
            if not _acted:
                _idle_sweeps += 1
                if _idle_sweeps >= 1:
                    break
            else:
                _idle_sweeps = 0
                await page.wait_for_timeout(1000)
        # Iter-16: the old blind 2x(Escape + 400ms) pressed Escape even
        # with no splash on screen. Press Escape only while the splash
        # is actually present.
        for _ in range(2):
            try:
                _splash = bool(await page.evaluate(SPLASH_JS))
            except Exception:
                _splash = False
            if not _splash:
                break
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass
            await page.wait_for_timeout(400)
        _stage("escape")
        # Iter-19 (P2, leading-wait trim): the second accept_cookies
        # scan is pure redundancy when the first found nothing to
        # dismiss and no modal was dismissed — each scan walks every
        # frame x every consent selector. Re-run only if something
        # was dismissed (a dismissal can reveal a second banner).
        _cookies_acted = await accept_cookies(page)
        _modals_acted = await dismiss_modals(page, has_grid=True)
        if _cookies_acted or _modals_acted:
            await accept_cookies(page)
        # Iter-20 (P2): the pre-guess dismissal ran — the loop's
        # first-iteration sweep may skip (see below).
        _PRE_GUESS_DISMISSED = True
        _stage("cookies/modals")
        state = await read_state(page, cols, tries=6)
        _stage(f"read_state state={'found' if state else 'empty'}")
        # Iter-21 (ANALYSIS18 §6.3): on speed load, dump FULL
        # storage + all grid states — the restore gate read
        # keys=[] guesses=0 while the video showed a filled row,
        # so the gate reads a different store than the renderer
        # (or the starter row is site-provided, not restored).
        if mode["id"] == "speed":
            await _nerdle_speed_load_dump(page)
        if not site_eqs:
            try:
                for body in (await page.evaluate(FETCH_JS) or []):
                    dec = decode_site_file(body)
                    if dec and is_valid_eq(dec, cols) and dec not in site_eqs:
                        site_eqs.append(dec)
                        print(f"[{mode['id']}] site answer via fetch -> {dec}")
            except Exception as e:
                print(f"[{mode['id']}] fetch fallback failed: {e}")
        _stage("fetch-fallback")
        gs_eqs = await read_game_state(page, cols)
        _stage("gameState")
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

        # Iter-19 (P2, leading-wait trim): the standalone
        # "pre-loop sweep" is REMOVED — the main loop sweeps at the
        # top of every iteration (including the first), and the
        # instant/legacy branches sweep before typing (below), so
        # this sweep ran back-to-back with the next one with no
        # navigation or time between. Dropping it removes one full
        # sweep from the leading wait. Typing speed / pacing
        # unchanged.

        if mode["id"] == "instant":
            # hint row is the pre-given starter; exactly one guess allowed.
            # Fix-loop iter 4: instant used to claim typed=1/won with ZERO
            # logged guesses (its guess bypassed the per-guess path). It now
            # logs through the same per-guess path as every other mode, and
            # PLAYED records the equation only if it was actually submitted.
            await focus_grid(page)
            if eqs:
                _submitted = False
                if state:
                    prev = [eval_count(g) for g in state]
                    _st, _ct, _ok = await type_guess(page, eqs[0], cols, prev)
                    _submitted = bool(_ok)
                else:
                    # Iter-19: this path bypasses type_guess (so no
                    # priming sweep) — sweep here, since the
                    # pre-loop sweep was removed.
                    await sweep_popups(page, tag=f":{mode['id']}")
                    _prev0 = [eval_count(g) for g in (state or [])]
                    await page.keyboard.type(eqs[0], delay=200)
                    await page.keyboard.press("Enter")
                    await page.wait_for_timeout(3000)
                    try:
                        _fresh = await read_state(page, cols, tries=2)
                        _submitted = bool(_fresh) and (
                            [eval_count(g) for g in _fresh] != _prev0
                            or await check_win())
                    except Exception:
                        _submitted = False
                _mark_first_type()
                if _submitted:
                    typed = 1
                    # Iter-17: record the row READ BACK from the board,
                    # not the suggestion (ANALYSIS13 §10).
                    _rb = None
                    try:
                        _fresh_rb = await read_state(page, cols, tries=2)
                        _rb = _last_evaluated_text(_fresh_rb)
                    except Exception:
                        _rb = None
                    PLAYED[mode["id"]] = [_rb or eqs[0]]
                    _nlog(f"[{mode['id']}] guess 1: {eqs[0]} "
                          f"(instant hint solve; submitted; "
                          f"read-back={_rb!r})")
                else:
                    _nlog(f"[{mode['id']}] guess 1: {eqs[0]} "
                          f"NOT submitted (board did not react); "
                          f"not counted as played")
                    PLAYED[mode["id"]] = []
        elif not state:
            print(f"[{mode['id']}] no pb-grid found, legacy direct typing")
            # Iter-19: this path never enters the loop (so no loop
            # sweep) — sweep here, since the pre-loop sweep was removed.
            await sweep_popups(page, tag=f":{mode['id']}")
            await focus_grid(page)
            PLAYED[mode["id"]] = []
            for eq in eqs:
                await page.keyboard.type(eq, delay=200)
                _mark_first_type()
                await page.keyboard.press("Enter")
                await page.wait_for_timeout(2500)
                typed += 1
                PLAYED[mode["id"]].append(eq)
                _nlog(f"[{mode['id']}] guess {typed}: {eq} "
                      f"(legacy direct typing)")
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
            # Iter-21: the seeded evaluated rows (e.g. speed's
            # pre-filled SUBMITTED starter row) are board history:
            # they count toward the played solutions in every
            # win verification below, including the post-loop and
            # final checks (which previously saw played_here only
            # and could never cover the starter).
            seeded_history = list(typed_guesses)
            # Equations WE typed this run (typed_guesses also holds
            # pre-filled starter rows from load; PLAYED must be ours only).
            played_here = []
            _suggest_timed = False  # iter-16 P2: time the 1st worker call
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
                # Iter-19 (P2): wonText alone never wins a
                # multi-board mode — _check_win_verified requires
                # every grid solved + every site solution played.
                # `pool` is the SITE solution pool (not the merged
                # solver pool); `typed_guesses + played_here` is
                # everything submitted so far (seeded starter
                # history + our guesses).
                if await _check_win_verified(
                        page, cols, boards_n, state,
                        list(typed_guesses) + list(played_here), pool,
                        mode_id=mode["id"]):
                    # Iter-21 (speed): a verified win may hide a
                    # 2nd board behind the win popup's Next button.
                    # Advance past it when there is one; the
                    # single-grid flow (no Next / no new board)
                    # is the game-granted win.
                    if mode["id"] == "speed":
                        _adv, _adv_state = await _speed_try_advance(
                            page, cols, state)
                        if _adv == "advanced":
                            state = _adv_state
                            prev_counts = [eval_count(g) for g in state]
                            continue
                    signal = "won"
                    break
                # Iter-20 (P2, leading-wait trim): the loop's own
                # first-iteration sweep is redundant — play_mode's
                # pre-guess stage JUST swept + dismissed cookies /
                # modals, and type_guess's priming re-sweeps
                # immediately before the first keystroke (after the
                # solver POST, when a delayed banner can actually
                # appear). Skip it on iteration 0 only; later
                # iterations still sweep (popups can appear between
                # guesses). The skip is stage-logged so the next
                # artifact proves the trim.
                if _it == 0 and _PRE_GUESS_DISMISSED:
                    _nlog(f"[{mode['id']}] loop iter0 sweep skipped "
                          f"(pre-guess dismissal ran; priming re-sweeps "
                          f"before typing)")
                else:
                    await sweep_popups(page, tag=f":{mode['id']}")
                unsolved = [i for i, g in enumerate(state) if not grid_solved(g)]
                if not unsolved:
                    if len(state) >= boards_n:
                        signal = "won"
                        break
                    # Iter-21 (speed): before waiting for a board
                    # that may never appear in `state`, try
                    # advancing past the win popup's Next button —
                    # board 2 may sit behind it. (The win check
                    # above already failed — e.g. coverage missing
                    # board 2's solution — so a verified win is
                    # NOT claimed here; the advance only rescues
                    # play when a new board actually appears.)
                    if mode["id"] == "speed":
                        _adv, _adv_state = await _speed_try_advance(
                            page, cols, state)
                        if _adv == "advanced":
                            for b in range(len(state), len(_adv_state)):
                                usable0[b] = len(_adv_state[b]["rows"])
                                hists[b] = []
                            state = _adv_state
                            prev_counts = [eval_count(g) for g in state]
                            continue
                    # more boards may still appear (speed board2)
                    # Iter-20 (P0, ANALYSIS17 §2e/§6.3): iter-19
                    # speed ABANDONED the mode here — one 2.5s wait
                    # + one re-read, then a SILENT break, with the
                    # 2nd site solution ('2+10-6=6') unplayed and
                    # rows remaining. A single transient read (the
                    # solved board re-rendering, the 2nd board not
                    # yet visible) must never end the mode. Retry
                    # the fresh read up to 3 times, and log the
                    # exit with the pool state when giving up.
                    _grew = False
                    for _retry in range(3):
                        await page.wait_for_timeout(2500)
                        _fresh = await read_state(page, cols, tries=3)
                        if _fresh and len(_fresh) > len(state):
                            for b in range(len(state), len(_fresh)):
                                usable0[b] = len(_fresh[b]["rows"])
                                hists[b] = []
                            state = _fresh
                            prev_counts = [eval_count(g) for g in state]
                            _grew = True
                            break
                    if _grew:
                        continue
                    print(f"[{mode['id']}] no unsolved grid and no "
                          f"new board after 3 re-reads "
                          f"(grids={len(state)} boards={boards_n} "
                          f"pool_unplayed="
                          f"{_unplayed_pool(pool, list(typed_guesses) + list(played_here))}); "
                          f"stopping (2nd board never appeared)")
                    break
                first_open = unsolved[0]
                rows_left = min(
                    usable0.get(i, len(state[i]["rows"])) - eval_count(state[i])
                    for i in unsolved)
                if rows_left <= 0:
                    # Iter-20 (P0): a transient misread (a row
                    # flashing evaluated mid-animation, a grid
                    # re-rendering) can report zero rows left.
                    # Re-read once before abandoning the mode with
                    # pool solutions unplayed, and log the exit.
                    _reread = await read_state(page, cols, tries=2)
                    _rows_after = None
                    if _reread:
                        _uns = [i for i, g in enumerate(_reread)
                                if not grid_solved(g)]
                        if _uns:
                            _rows_after = min(
                                usable0.get(i, len(_reread[i]["rows"]))
                                - eval_count(_reread[i]) for i in _uns)
                    if _rows_after is not None and _rows_after > 0:
                        print(f"[{mode['id']}] rows_left was 0 but "
                              f"re-read shows {_rows_after} rows — "
                              f"transient misread, continuing")
                        state = _reread
                        prev_counts = [eval_count(g) for g in state]
                        continue
                    print(f"[{mode['id']}] rows_left=0 on re-read "
                          f"(grids={len(state)} "
                          f"pool_unplayed="
                          f"{_unplayed_pool(pool, list(typed_guesses) + list(played_here))}); "
                          f"stopping")
                    break
                need = len([e for e in eqs if e not in typed_guesses])
                nxt = None
                if rows_left <= 2 and need and len(typed_guesses) >= min_probes:
                    # user rule: official answer only in the last 2 attempts,
                    # and never before min_probes real solver probes
                    while official_i < len(eqs) and eqs[official_i] in typed_guesses:
                        official_i += 1
                    if official_i < len(eqs):
                        nxt = eqs[official_i]
                        official_i += 1
                else:
                    if not _suggest_timed:
                        _stage("solver_suggest start")
                        raw = solver_suggest(mode["id"],
                                             hists.get(first_open, []))
                        _stage("solver_suggest done")
                        _suggest_timed = True
                    else:
                        raw = solver_suggest(mode["id"],
                                             hists.get(first_open, []))
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
                        # Iter-20 (P0, pool-drain backstop): never
                        # abandon a multi-board mode with a site
                        # solution unplayed — play the next
                        # unplayed pool solution instead of
                        # breaking. (Covers the iter-19 speed
                        # shape: the loop exited with
                        # '2+10-6=6' unplayed.) Coverage uses
                        # _row_matches_equation, not raw string
                        # equality, so a DOM-normalized read-back
                        # still counts as played.
                        _unplayed = _unplayed_pool(
                            pool, list(typed_guesses) + list(played_here))
                        if boards_n > 1 and _unplayed:
                            nxt = _unplayed[0]
                            print(f"[{mode['id']}] solver + official "
                                  f"exhausted with site solution "
                                  f"{nxt!r} unplayed — playing it "
                                  f"(pool-drain backstop)")
                        else:
                            print(f"[{mode['id']}] no next guess available, stopping")
                            break
                # Iter-20 (P0, ANALYSIS17 §2e/§6.3): play EACH
                # site solution. The pool-drain backstop above
                # only fires when solver + official are EXHAUSTED
                # — while the solver keeps returning fresh entropy
                # probes, the loop could burn every row on probes
                # with a known site solution still unplayed (the
                # iter-19 shape: probe + 1st site solution, 2nd
                # never played, per-board verifier correctly
                # refusing). So: once min_probes probes are typed
                # or a board is already solved, an unplayed SITE
                # solution beats another solver probe. This
                # override only ever replaces a solver probe —
                # never an official/endgame pick (nxt in pool) —
                # and never fires when the pool is drained.
                if (boards_n > 1 and nxt is not None
                        and nxt not in pool):
                    _unplayed2 = _unplayed_pool(
                        pool, list(typed_guesses) + list(played_here))
                    if (_unplayed2 and (len(typed_guesses) >= min_probes
                                        or any(grid_solved(g)
                                               for g in state))):
                        _nlog(f"[{mode['id']}] site-solution override: "
                              f"solver probe {nxt!r} deferred, "
                              f"playing unplayed site solution "
                              f"{_unplayed2[0]!r}")
                        nxt = _unplayed2[0]
                prev = list(prev_counts)
                # Iter-17 (P1): stage-log the type path — the 22s
                # silence in iter-16 sits between `solver_suggest
                # done` and the first keystroke, i.e. inside
                # type_guess. Logging only; typing speed/pacing
                # unchanged.
                _t_type = time.time()
                _nlog(f"[{mode['id']}] type_guess start "
                      f"suggestion={nxt!r} (rows_left~{rows_left})")
                state, counts, ok = await type_guess(page, nxt, cols, prev)
                _nlog(f"[{mode['id']}] type_guess done ok={ok} "
                      f"+{time.time() - _t_type:.1f}s")
                if ok:
                    _mark_first_type()
                if not ok:
                    # Iter-20 (P0): ONE bounded re-entry when the
                    # FAILED guess is a SITE solution — do not
                    # strand a known solution on a single failed
                    # entry. type_guess's 3 internal attempts never
                    # sweep the splash, so a popup that appeared
                    # mid-guess (e.g. during the solver POST)
                    # blocks every internal attempt identically;
                    # this retry sweeps first, then re-enters.
                    # Bounded: one re-entry, site solutions only.
                    # Iter-21 ladder cap: when the full ² ladder
                    # already failed once for this equation, the
                    # re-entry would just re-run the identical
                    # ladder — skip it (fail fast). Plain
                    # equations never hit the cap, so the
                    # site-solution retry still rescues them.
                    if nxt in pool and nxt not in _ladder_failed_set(page):
                        await sweep_popups(page, tag=f":{mode['id']}")
                        _nlog(f"[{mode['id']}] site-solution retry "
                              f"for {nxt!r} (sweep + re-enter once)")
                        _t_type = time.time()
                        prev = list(prev_counts)
                        state, counts, ok = await type_guess(
                            page, nxt, cols, prev)
                        _nlog(f"[{mode['id']}] site-solution retry "
                              f"done ok={ok} "
                              f"+{time.time() - _t_type:.1f}s")
                        if ok:
                            _mark_first_type()
                    elif nxt in pool and nxt in _ladder_failed_set(page):
                        _nlog(f"[{mode['id']}] site-solution retry "
                              f"SKIPPED (ladder cap) for {nxt!r}: "
                              f"full ² ladder already failed once")
                    # Iter-17 (P0): do NOT abandon the mode on an
                    # ENTRY failure while rows remain — retry the
                    # ENTRY (clear + re-enter via the full
                    # power-key ladder), not just the typing.
                    # type_guess already tried 3 attempts
                    # internally; these outer re-entries cover a
                    # transient popup/focus loss between attempts
                    # ("power key not found on board"). Bounded:
                    # 2 outer re-entries, superscript equations
                    # only (plain equations have no entry ladder
                    # to retry — their failure is a submit failure).
                    # (Iter-20: re-guarded on `not ok` so a ² site
                    # solution rescued by the retry above is not
                    # re-entered a second time.)
                    # Iter-21 ladder cap: the entry retries re-run
                    # the same full ladder that already exhausted
                    # — skip both when the cap is engaged.
                    if not ok and any(c in nxt for c in "²³") and rows_left >= 1 \
                            and nxt not in _ladder_failed_set(page):
                        for _re in range(2):
                            await sweep_popups(page, tag=f":{mode['id']}")
                            _nlog(f"[{mode['id']}] entry retry "
                                  f"{_re + 1}/2 for {nxt!r} (rows remain)")
                            _t_type = time.time()
                            prev = list(prev_counts)
                            state, counts, ok = await type_guess(
                                page, nxt, cols, prev)
                            _nlog(f"[{mode['id']}] entry retry "
                                  f"{_re + 1}/2 done ok={ok} "
                                  f"+{time.time() - _t_type:.1f}s")
                            if ok:
                                _mark_first_type()
                                break
                    elif not ok and any(c in nxt for c in "²³") \
                            and nxt in _ladder_failed_set(page):
                        _nlog(f"[{mode['id']}] entry retries SKIPPED "
                              f"(ladder cap) for {nxt!r}: full ² "
                              f"ladder already failed once")
                    if not ok:
                        print(f"[{mode['id']}] board did not react to {nxt!r}, stopping")
                        break
                typed += 1
                typed_guesses.append(nxt)
                # Iter-17 (P1, ANALYSIS13 §10): equations_played
                # must record the row READ BACK from the board, not
                # the solver suggestion — midi logged `1-1+5=5`
                # while the settled board showed `5+1-1=5`. The
                # suggestion stays in typed_guesses (de-dup);
                # played_here carries the read-back row. A
                # mismatch is logged loudly, never silently.
                _readback = _readback_played(state, prev)
                if _readback is None:
                    played_here.append(nxt)
                    _nlog(f"[{mode['id']}] guess {typed}: read-back "
                          f"unavailable, recording suggestion {nxt!r}")
                else:
                    played_here.append(_readback)
                    if _readback != nxt:
                        _nlog(f"[{mode['id']}] guess {typed}: "
                              f"READ-BACK MISMATCH suggestion={nxt!r} "
                              f"board={_readback!r} (recording board row)")
                    else:
                        _nlog(f"[{mode['id']}] guess {typed}: "
                              f"read-back={_readback!r} matches suggestion")
                for b, g in enumerate(state):
                    before = prev[b] if b < len(prev) else -1
                    c = counts[b] if b < len(counts) else before
                    if c < before:
                        hists[b] = []
                        usable0[b] = len(g["rows"])
                    elif c > before:
                        last = next((r for r in reversed(g["rows"]) if _done(r)), None)
                        if last is not None:
                            # Pair the feedback with the READ-BACK row
                            # text (the row actually submitted), not
                            # the suggestion.
                            hists.setdefault(b, []).append(
                                ((_readback or nxt), list(last)))
                prev_counts = counts
                _nlog(f"[{mode['id']}] guess {typed}: {nxt} "
                      f"rows_left~{rows_left - 1} hist={len(hists.get(first_open, []))}")
            PLAYED[mode["id"]] = list(played_here)
            if not signal:
                fresh = await read_state(page, cols, tries=3)
                if _mode_won_by_state(fresh, boards_n, mode["id"]):
                    signal = "won"
                # Iter-19 (P2): same multi-board verification as
                # the loop — wonText alone never wins speed/quad/
                # minibi. Coverage sees seeded starter history +
                # read-back rows (the starter alone never proves
                # a win, but it counts toward the solutions).
                elif await _check_win_verified(
                        page, cols, boards_n, fresh,
                        list(seeded_history) + list(played_here), pool,
                        mode_id=mode["id"]):
                    signal = "won"

        if not signal:
            fresh = await read_state(page, cols, tries=3)
            if _mode_won_by_state(fresh, boards_n, mode["id"]):
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
        _nlog(f"[{mode['id']}] shot@ {url_now} splash={splash_now} won={bool(signal)}")
        # Served-date evidence: what the SITE served (page clock + any
        # date-ish gameState fields) and whether the site's own solution
        # (answer file / gameState) matches the TARGET date's official
        # equation list. matches_target_official=None means the site
        # exposed no solution to compare — unknown, NOT verified.
        # A False means the served solution differs from the target-day
        # official list (wrong-day board or worker-list error) — it is
        # evidence, not a verified day claim.
        try:
            _served = await page.evaluate(SERVED_JS)
        except Exception:
            _served = {"error": "evaluate failed"}
        # Iter-16 P3: dedupe the site solutions list (preserve order,
        # drop exact duplicates) at the reader. site_eqs (answer
        # files) and gs_eqs (localStorage gameState) each dedupe
        # internally, but the same equation appears in BOTH sources
        # (speed: '50-6*7=8' in the answer file AND in gameState),
        # so the plain concatenation double-counted one board:
        # ['50-6*7=8', '2+10-6=6', '50-6*7=8']. Downstream
        # consumers (_nerdle_speed_matches, SERVED) must never see
        # the same board twice.
        _site_only = []
        for _e in (site_eqs + gs_eqs):
            if _e and _e not in _site_only:
                _site_only.append(_e)
        _matches = None
        _speed_detail = ""
        if _site_only and official_eqs:
            if mode["id"] == "speed":
                # Iter-14 P3: BOTH speed equations must match — an
                # ANY-overlap matches=True (1/2) is not day proof and
                # speed is not counted in the matches rollup on a
                # partial (see _nerdle_speed_matches).
                _matches, _speed_detail = _nerdle_speed_matches(
                    _site_only, official_eqs, boards_n)
            else:
                _matches = any(e in official_eqs for e in _site_only)
        # Iter-14 P0: header number from the multi-strategy probe
        # (raw evidence -> Python strategies); log which strategy
        # matched + the raw header text for the rerun artifact.
        # Iter-15 P0: pass mode_id — ONLY the mode's OWN header logo
        # line counts; shared-chrome first-match #numbers are
        # rejected (mini false-positive in iter-14).
        _hn, _hn_strategy, _hn_raw = _nerdle_header_number_from_probe(
            _served.get("header_probe"), mode_id=mode["id"])
        # Iter-16 (ANALYSIS12 §2): log the RAW probe diagnostics on
        # every served probe — the raw own_headers list, the
        # probe_diag counters/samples, and #-line samples — so a
        # future empty capture is diagnosable from the artifact
        # instead of guessed. Bounded: first 5 own_headers, first
        # 8 hash-line samples.
        _probe = _served.get("header_probe") or {}
        _diag = _probe.get("probe_diag") or {}
        _nlog(f"[{mode['id']}] header probe diagnostics: "
              f"own_headers={str(_probe.get('own_headers') or [])[:1200]} "
              f"diag_total={_diag.get('elements_total')} "
              f"vis_rect={_diag.get('elements_visible_rect')} "
              f"vis_offsetparent="
              f"{_diag.get('elements_offsetparent_visible')} "
              f"header_els={_diag.get('header_els_count')} "
              f"logo_matched={_diag.get('logo_selector_matched')} "
              f"own_raw_count={_diag.get('own_headers_raw_count')} "
              f"popup_trips={_diag.get('popup_trips')} "
              f"popup_tripped_own="
              f"{_diag.get('popup_tripped_own_headers')} "
              f"aria_hidden_only_header_els="
              f"{_diag.get('header_els_aria_hidden_skipped')} "
              f"hash_lines={str(_diag.get('hash_line_samples') or [])[:800]} "
              f"nerdle_samples="
              f"{str(_diag.get('nerdle_text_samples') or [])[:800]}")
        SERVED[mode["id"]] = {
            "page_local": _served.get("page_local"),
            "page_iso": _served.get("page_iso"),
            "tz_offset_min": _served.get("tz_offset_min"),
            "header_number": _hn,
            "header_strategy": _hn_strategy,
            "header_text": _hn_raw,
            "header_probe_diag": _diag,
            "useLocalTime": _served.get("useLocalTime"),
            "gameState_dateish": _served.get("dateish"),
            "site_solutions": _site_only,
            "matches_target_official": _matches}
        if _speed_detail:
            SERVED[mode["id"]]["speed_match"] = _speed_detail
        _nlog(f"[{mode['id']}] served page_local={_served.get('page_local')} "
              f"gameState_dateish={_served.get('dateish')} "
              f"site_solutions={_site_only} matches_target_official={_matches} "
              f"header_number={_hn} header_strategy={_hn_strategy} "
              f"header_text={_hn_raw[:300]!r} "
              f"useLocalTime={_served.get('useLocalTime')!r} "
              f"{_speed_detail}")
        try:
            # VIDEO_DIR is the resolved videos/nerdle dir (set by main).
            # This used to reference bare `video_dir`, undefined in this
            # scope — the NameError was swallowed and NO per-mode
            # screenshot was ever saved.
            _shot_dir = VIDEO_DIR or (Path(script_dir) / "videos" / "nerdle")
            _shot_p = _shot_dir / f"shot_{mode['id']}.png"
            await page.screenshot(path=str(_shot_p))
            _nlog(f"[{mode['id']}] shot saved: {_shot_p}")
        except Exception as _e:
            _nlog(f"[{mode['id']}] shot failed: {str(_e)[:120]}")
        if not signal:
            try:
                # Iter-19 (P2): final win check goes through the
                # same multi-board verification — a bare WIN_JS
                # wonText never wins speed/quad/minibi. Fresh state
                # + seeded starter history + PLAYED read-back rows
                # are the evidence (iter-21: the starter counts
                # toward the played solutions here too).
                _fresh_final = await read_state(page, cols, tries=2)
                _played_final = (list(seeded_history)
                                 + list(PLAYED.get(mode["id"]) or []))
                if await _check_win_verified(
                        page, cols, boards_n, _fresh_final,
                        _played_final, pool, mode_id=mode["id"]):
                    signal = "won"
            except Exception as e:
                print(f"[{mode['id']}] win check failed: {e}")
        _nlog(f"[{mode['id']}] typed {typed} (probes+official guesses) "
              f"official={len(eqs)} won={bool(signal)}")
        return typed, len(eqs), signal
    except Exception as e:
        print(f"[{mode['id']}] play failed: {e}")
        import traceback
        traceback.print_exc()
        return 0, len(eqs), ""


async def main():
    global VIDEO_DIR, _DEBUG_LOG, _REC_T0
    script_dir = Path(__file__).parent
    # Iteration 3: write into videos/nerdle/ — the dir the workflow uploads
    # telemetry_*.json / *.mp4 / shot_win.png FROM. The old flat videos/
    # location is why telemetry + gameplay_1080.mp4 never reached the
    # artifact: the runner only moves the final + result.json.
    video_dir = script_dir / "videos" / "nerdle"
    video_dir.mkdir(parents=True, exist_ok=True)
    VIDEO_DIR = video_dir
    target = _target_date()
    date_key = target.strftime("%Y-%m-%d")
    _DEBUG_LOG = video_dir / f"debug_{date_key}.log"
    today = target.strftime("%B %d, %Y")
    date_short = target.strftime("%b %d")
    day_num = target.timetuple().tm_yday
    print(f"[nerdle] target {date_key} puzzle #{classic_puzzle_number(target)} "
          f"(website effective date, boundary {NERDLE_SITE_BOUNDARY_UTC})")

    # Fetch the site's OWN published answers + puzzle numbers BEFORE
    # playing. No API row for the target date (404) -> HONEST DEFER:
    # the puzzle is unpublished, so no video (same rule as other
    # games). Never blind-play, never fall back to /today.
    _, official, api_numbers = await asyncio.to_thread(
        fetch_nerdle_answers, target)
    if not official or not api_numbers:
        _nlog(f"[nerdle] DEFER: API has no row for target {date_key} "
              f"(unpublished) — no video")
        _res_doc = {"game": "nerdle", "date": date_key, "solved": False,
                    "modes": [], "evidence": f"defer: API no row for {date_key}",
                    "served": {}, "api_puzzle_numbers": api_numbers,
                    "number_gate": {"blocked": False, "detail":
                                    "defer: no API row, never played"}}
        (video_dir / f"result_{date_key}.json").write_text(
            json.dumps(_res_doc, indent=1))
        try:
            (script_dir / "videos" / f"result_{date_key}.json").write_text(
                json.dumps(_res_doc, indent=1))
        except Exception:
            pass
        print(f"[nerdle] DEFER: no API row for {date_key}")
        sys.exit(1)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        tz = os.environ.get("BROWSER_TZ", "Pacific/Kiritimati")
        print(f"[tz] {tz}")
        _sstate = script_dir / "browser_state.json"
        _state_kwargs = {"storage_state": str(_sstate)} if _sstate.exists() else {}
        context = await browser.new_context(
            record_video_dir=str(video_dir),
            record_video_size={"width": 1920, "height": 1080},
            viewport={"width": 1920, "height": 1080},
            timezone_id=tz, locale="en-US",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
            **_state_kwargs,
        )
        await context.route("**/*", block_ads)
        # Fix-loop iter 13 revised: the browser clock must show the
        # EFFECTIVE date (target 00:05 local), NOT FAKE_DATE_ISO (the
        # pipeline date, which can differ from the effective date in
        # the 15:30–16:30 UTC window). With useLocalTime=true the game
        # serves the browser-local date; faking it to the pipeline
        # date would serve the pipeline date's puzzle while the gate
        # checks the effective date's numbers.
        try:
            fake_iso = target.strftime("%Y-%m-%dT00:05:00")
            if QP_AVAILABLE:
                await context.add_init_script(script=QP.get_fake_date_init_script(fake_iso))
                print(f"[fakedate] {fake_iso} (effective date)")
        except Exception as e:
            print(f"[fakedate] skipped: {e}")
        # Fix-loop iter 15 — NERDLE CLASSIC-DAY-KEY FINDING
        # (ANALYSIS11 §1; P0 investigation, no boundary change).
        #
        # Observed: iter-14 classic served the Oct-17 board (#1732,
        # frame g_20: the board header itself read "nerdle. #1732",
        # and the site accepted the Oct-17 answer as a win) while
        # iter-13, under the SAME fake clock and the SAME
        # navigation/clock/play code, served Oct-18 (#1733) — and
        # within iter-14 itself the VARIANT modes served Oct-18
        # boards (mini answer = API Oct-18) while classic served
        # Oct-17. The classic flip is therefore NOT a stale header
        # capture (frame evidence refutes that) and NOT a fake-clock
        # change. Leading hypothesis, proven in OUR code (the site
        # side is still unproven — see below):
        #
        # The site's daily index follows localStorage `useLocalTime`
        # (default false = UTC day). With the fake clock at effective
        # date 00:05 Kiritimati (= 10:05 UTC previous day), UTC-day
        # mode serves YESTERDAY's board (#1732) and local-midnight
        # mode serves the target board (#1733). Whether the bot's
        # `useLocalTime=true` survived to boot depended on init-script
        # ORDER + the prepurge one-shot cookie: prepurge_js() runs
        # localStorage.clear() on the first document where the
        # `_qp_purged` cookie is absent, and until iter-15 it was
        # registered AFTER the useLocalTime setter — so on the first
        # https document it WIPED the bot's own setting moments
        # before the site booted. Classic is modes[0], so classic is
        # the victim of that first-document wipe, while later modes
        # survive (cookie set -> purge is a no-op). The `_qp_purged`
        # cookie has max-age=3600s, so whether the wipe fired on a
        # given run depended on the committed browser_state.json's
        # cookie state at run start — consistent with the iter-13
        # (survived -> #1733) vs iter-14 (wiped -> #1732) flip, and
        # with classic-vs-variants inside iter-14. The flip itself
        # is NOT artifact-proven (the iter-13 state file is not in
        # the artifact); what IS proven is the ordering hazard, fixed
        # below. If the site itself serves yesterday's classic board
        # in some window DESPITE useLocalTime=true, the correct
        # behavior stays the existing honest defer (gate blocks, no
        # final shipped) — never play yesterday's board as today's.
        # NERDLE_SITE_BOUNDARY_UTC is UNCHANGED (16, 30): the site
        # has NOT moved its post to 9pm IST.
        #
        # Fix: register prepurge BEFORE the useLocalTime setter, so
        # the wipe (when it fires) happens first and the setting
        # survives on every document, independent of cookie state.
        # The per-mode SERVED evidence now also logs the live
        # useLocalTime value, so the next artifact PROVES whether the
        # setting survived at classic boot.
        try:
            if QP_AVAILABLE:
                await context.add_init_script(script=QP.prepurge_js())
        except Exception as e:
            print(f"[nerdle] prepurge init skipped: {e}")
        # Fix-loop iter 13 revised: the site's daily index follows
        # localStorage `useLocalTime` (default false = UTC day). The
        # browser clock is faked to the EFFECTIVE date 00:05 local
        # (see above), so with the site's own local-midnight setting
        # on ("Change game to change at local midnight"), the site
        # serves the effective date's puzzle — the same date the API
        # fetch + number gate check. MUST be registered AFTER
        # prepurge (see iter-15 note above): prepurge clears
        # localStorage on the first document, which would otherwise
        # wipe this setting before the site boots.
        try:
            await context.add_init_script(
                script="try{localStorage.setItem('useLocalTime','true')}catch(e){}")
            print("[nerdle] useLocalTime=true (site local-midnight rollover)")
        except Exception as e:
            print(f"[nerdle] useLocalTime init skipped: {e}")
        try:
            if QP_AVAILABLE:
                await context.add_init_script(script=QP.consent_watch_js())
        except Exception as e:
            print(f"[watchers] skipped: {e}")
        page = await context.new_page()
        try:
            _m = page.mouse
            for _name in ("move", "down", "up", "click"):
                _orig = getattr(_m, _name)

                async def _wrap(*a, _o=_orig, _n=_name, **kw):
                    r = await _o(*a, **kw)
                    try:
                        x = a[0] if len(a) > 0 else kw.get("x")
                        y = a[1] if len(a) > 1 else kw.get("y")
                        if x is not None and y is not None:
                            QP.ev_push(x, y, "click" if _n in ("click", "down")
                                       else "move")
                    except Exception:
                        pass
                    return r
                setattr(_m, _name, _wrap)
        except Exception:
            pass

        async def _dismiss(d):
            try:
                print(f"[nerdle] dialog auto-dismiss {d.type}")
                await d.dismiss()
            except Exception:
                pass

        page.on("dialog", _dismiss)
        t0 = time.time()
        _REC_T0 = t0
        starts = []
        card_starts = []
        results = []
        for i, m in enumerate(NERDLE_MODES):
            # Mode card BEFORE play (fix-loop iter 11): generate the
            # NOW PLAYING card, SHOW it in the browser (file:// URL)
            # for 2.5s so it is in the recording before play, and
            # record the card start (recording-relative). Play does
            # not start until the card has been shown; segment start
            # is recorded AFTER the card. Card-generation failure is
            # a mode failure: retry 3x, then fail the mode — never emit
            # a chapter with card_dur=0.0.
            _card_ok = False
            _cp = video_dir / f"mode_{i}.png"
            for _attempt in range(3):
                try:
                    QP.generate_mode_card(
                        str(_cp), "Nerdle",
                        m.get("name", f"Mode {i + 1}"),
                        f"Round {i + 1} of {len(NERDLE_MODES)}")
                    _card_ok = True
                    break
                except Exception as _e:
                    _nlog(f"[nerdle] mode card gen failed for "
                          f"{m['id']} attempt {_attempt + 1}/3: "
                          f"{str(_e)[:90]}")
                    await asyncio.sleep(0.5)
            if not _card_ok:
                _nlog(f"[nerdle] mode {m['id']}: card generation failed "
                      f"after 3 attempts — failing the mode (never a "
                      f"chapter with card_dur=0.0)")
                card_starts.append(None)
                starts.append(time.time() - t0)
                results.append((m["id"], 0, 0, "card-failed"))
                continue
            card_starts.append(time.time() - t0)
            try:
                await page.goto(_cp.as_uri(),
                                wait_until="domcontentloaded",
                                timeout=15000)
                await page.wait_for_timeout(2500)
            except Exception as _e:
                _nlog(f"[nerdle] mode {m['id']}: card display failed: "
                      f"{str(_e)[:90]}")
                card_starts[-1] = None
                starts.append(time.time() - t0)
                results.append((m["id"], 0, 0, "card-failed"))
                continue
            starts.append(time.time() - t0)
            r = await play_mode(page, m, script_dir, official if isinstance(official, dict) else None)
            results.append((m["id"],) + r)
        total = time.time() - t0
        vpath = await page.video.path()
        try:
            await context.storage_state(path=str(script_dir / "browser_state.json"))
            print("[nerdle] browser_state.json saved")
        except Exception as _e:
            print(f"[nerdle] state save failed: {str(_e)[:120]}")
        # Never persist completed nerdle gameStates: strip them from the
        # saved file so the next run cannot replay into a won board.
        try:
            if _strip_state_file is not None:
                _removed = _strip_state_file(
                    str(script_dir / "browser_state.json"))
                if _removed:
                    print(f"[nerdle] stripped completed gameState: {_removed}")
        except Exception as _e:
            print(f"[nerdle] state strip failed: {str(_e)[:120]}")
        # shot_win.png for the artifact (the workflow uploads shot_win.png):
        # screenshot the last mode's page while it is still open; fall back
        # to the last won mode's per-mode shot.
        try:
            await page.screenshot(path=str(video_dir / "shot_win.png"))
            _nlog("[nerdle] shot_win.png saved (last mode page)")
        except Exception as _e:
            _nlog(f"[nerdle] shot_win screenshot failed: {str(_e)[:120]}")
            for _mid in reversed([m["id"] for m in NERDLE_MODES]):
                _cand = video_dir / f"shot_{_mid}.png"
                if _cand.exists():
                    try:
                        import shutil as _sh
                        _sh.copy(str(_cand), str(video_dir / "shot_win.png"))
                        _nlog(f"[nerdle] shot_win.png fallback copied from {_cand.name}")
                    except Exception:
                        pass
                    break
        await context.close()
        await browser.close()

    # verify gate: every mode must show a REAL on-page win (WIN_JS)
    # Fix-loop iter 4: a mode with zero LOGGED guesses must NOT claim won
    # (rerun #3 instant: typed 1, won=True, zero guess lines in a 41 s
    # gap). Such a mode is UNPROVEN: demoted, cut from chapters, fails
    # the run unless it is the single allowed miss.
    _unproven = []
    _results2 = []
    for _mid, _t, _e, _s in results:
        if _s == "won" and not PLAYED.get(_mid):
            _unproven.append(_mid)
            _nlog(f"[nerdle] mode {_mid} claims won with ZERO logged "
                  f"guesses — marking UNPROVEN (not counted as won)")
            _results2.append((_mid, _t, _e, "unproven"))
        else:
            _results2.append((_mid, _t, _e, _s))
    results = _results2
    # Fix-loop iter 14 — NUMBER GATE (hard, per-mode trust): hard-check
    # served == API ONLY for the 4 proven modes (classic/mini/midi/
    # maxi); unproven modes are site-rules (API mismatch never blocks).
    # A block here sets all_solved=False (no final assembly, no
    # upload, exit 1). Gate stays FAIL-CLOSED.
    # Target date + API numbers + served numbers are ALL logged per
    # mode below, so the rerun artifact proves the gate's inputs.
    # Iter-19: pass the official answer lists so the mini
    # site-solutions gate can check exact set equality (see
    # _nerdle_number_gate).
    _wd_blocked, _wd_detail, _wd_per_mode = _nerdle_number_gate(
        SERVED, api_numbers, official=official)
    _nlog(f"[nerdle] number gate: target={date_key} "
          f"api_classic=#{api_numbers.get('classicPuzzleNumber')} "
          f"blocked={_wd_blocked} detail={_wd_detail}")
    for _mid, _info in sorted(_wd_per_mode.items()):
        _nlog(f"[nerdle] number gate { _mid}: api={_info.get('api')} "
              f"served={_info.get('served')} status={_info.get('status')}")
    if _wd_blocked:
        _nlog(f"[nerdle] NUMBER GATE BLOCKED: {_wd_detail} — "
              f"refusing to ship (target {date_key})")
    _wd_warnings = [mid for mid, ev in SERVED.items()
                    if mid != "classic" and isinstance(ev, dict)
                    and ev.get("matches_target_official") is False]
    if _wd_warnings:
        _nlog(f"[nerdle] served-mismatch warnings (not gating): "
              f"{_wd_warnings}")
    won_modes = [mid for mid, t, e, s in results if s == "won"]
    weak = [(mid, t, e) for mid, t, e, s in results if s != "won"]
    failed = weak
    all_solved = len(won_modes) >= len(NERDLE_MODES) - 1 and not _wd_blocked
    if all_solved and len(won_modes) != len(NERDLE_MODES):
        print(f"[nerdle] shipping with cut modes: "
              f"{[m for m, _t, _e, st in results if st != 'won']}")
    print(f"[verify] won {len(won_modes)}/{len(NERDLE_MODES)} no-win={weak}")

    global CUT_MODES
    CUT_MODES = {mid for mid, _t, _e, st in results if st != "won"}
    if CUT_MODES:
        print(f"[nerdle] unwon modes (chapters will be cut): {CUT_MODES}")
    final = vpath
    try:
        if QP_AVAILABLE:
            (video_dir / "telemetry_nerdle.json").write_text(
                json.dumps(QP.ev_all(), indent=1), encoding="utf-8")
            # videos/nerdle/ is the dir the workflow uploads
            # telemetry_*.json from — this file used to land in flat
            # videos/ and was never picked up.
            _nlog(f"[nerdle] telemetry -> {video_dir / 'telemetry_nerdle.json'}")
    except Exception as _e:
        _nlog(f"[nerdle] telemetry write failed: {str(_e)[:120]}")
    chapters = []
    # Wordle-parity assembly (moviepy) + uniform music
    if final and MOVIEPY_AVAILABLE and QP_AVAILABLE:
        try:
            try:
                if QP_AVAILABLE and not all_solved:
                    # Unsolved: fast-copy the raw recording for
                    # diagnosis (no re-encode, no autocrop) — same
                    # unsolved-tail rule as the runner (fix-loop
                    # iter 11). The full polish re-encode is where
                    # the SIGTERM tail stall lives.
                    _nlog("[nerdle] unsolved: fast-copy gameplay "
                          "(no re-encode)")
                    _fc = subprocess.run(
                        ["ffmpeg", "-y", "-v", "error", "-i", str(final),
                         "-c", "copy",
                         str(video_dir / "gameplay_1080.mp4")],
                        capture_output=True, text=True, timeout=300)
                    if _fc.returncode == 0 and (
                            video_dir / "gameplay_1080.mp4").exists():
                        final = str(video_dir / "gameplay_1080.mp4")
                elif QP_AVAILABLE:
                    # Autocrop OFF for Nerdle (fix-loop iter 11): the
                    # assembly cuts are absolute from recorded card
                    # starts, and autocrop's non-uniform cuts would
                    # invalidate them.
                    _pol = QP.polish_gameplay(
                        str(final), str(video_dir / "gameplay_1080.mp4"),
                        first_typing_s=_FIRST_TYPE_T, autocrop=False)
                    if _pol and str(_pol) != str(final):
                        final = _pol
                        _nlog(f"[nerdle] gameplay polished -> {_pol}")
                    if not (video_dir / "gameplay_1080.mp4").exists():
                        # Polish returned the raw capture (card composite
                        # fell back): still produce the artifact gameplay
                        # file with a plain 1920x1080 re-encode so the
                        # artifact is not missing it again.
                        _fb = subprocess.run(
                            ["ffmpeg", "-y", "-v", "error", "-i", str(final),
                             "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,"
                             "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,format=yuv420p,fps=24",
                             "-c:v", "libx264", "-preset", "medium", "-crf", "17",
                             "-pix_fmt", "yuv420p", "-r", "24", "-c:a", "aac",
                             str(video_dir / "gameplay_1080.mp4")],
                            capture_output=True, text=True, timeout=1800)
                        if _fb.returncode == 0 and (video_dir / "gameplay_1080.mp4").exists():
                            final = str(video_dir / "gameplay_1080.mp4")
                            _nlog("[nerdle] gameplay fallback re-encode -> gameplay_1080.mp4")
                        else:
                            _nlog(f"[nerdle] gameplay fallback failed: {(_fb.stderr or '')[:160]}")
            except Exception as _e:
                # Full message to the debug log — the old [:100] truncation
                # is what hid the TimeoutExpired root cause last run.
                _nlog(f"[nerdle] polish skipped: {_e}")
            if not all_solved:
                _nlog("[nerdle] skipping final assembly: not solved (no publishable video)")
            else:
                gameplay = VideoFileClip(str(final))
                gd = float(gameplay.duration or 0)
                parts, cursor = [], 0.0
                def _img(p, d):
                    return ImageClip(p).set_duration(d).set_fps(24).resize(width=1920, height=1080)
                classic = []
                if isinstance(official, dict):
                    classic = [e for e in (official.get("classic") or [])][:4]
                # No answer-bearing slides: the old hints card printed the classic
                # equations and the definition/facts cards said QUORDLE. Start
                # each mode with its own NOW PLAYING card instead.
                # Gameplay split into 9 mode chapters, each OPENING with
                # its NOW PLAYING card (the card was shown in-browser
                # for 2.5s before play, so it is IN the recording).
                # ABSOLUTE cuts from the recorded card starts (fix-loop
                # iter 11): the old proportional rescaling of wall-clock
                # weights assumed uniform scaling and broke under
                # non-uniform autocrop. Autocrop is OFF for Nerdle, so
                # the polished timeline == the recording timeline and
                # card starts map 1:1 after rebasing on the first card.
                # Assembly-time rule: a mode with no recorded card start
                # (card never shown) or a missing card PNG gets NO
                # segment and NO chapter — never card_dur=0.0.
                _base = None
                for _cs in card_starts:
                    if _cs is not None:
                        _base = float(_cs)
                        break
                if _base is None:
                    _base = 0.0
                cuts = []
                for i in range(len(NERDLE_MODES)):
                    _cs = card_starts[i] if i < len(card_starts) else None
                    if _cs is None:
                        cuts.append(None)
                        continue
                    _nxt = None
                    for _j in range(i + 1, len(NERDLE_MODES)):
                        if (card_starts[_j] if _j < len(card_starts)
                                else None) is not None:
                            _nxt = float(card_starts[_j])
                            break
                    _a = max(0.0, float(_cs) - _base)
                    _b = (max(0.0, _nxt - _base) if _nxt is not None
                          else gd)
                    cuts.append((_a, min(_b, gd)))

                # Chapter times (iteration-3 accumulator fix): chapter[i] =
                # sum over kept modes j<i of (card_dur_j + segment_dur_j).
                # The old code appended (cursor + card_sec) with `cursor`
                # stuck at 0.0 for the whole loop, so all nine mode chapters
                # landed at 20–40 s of a ~442 s video.
                mode_parts = []
                emitted = []  # (mode, card_dur, seg_dur) in final-video order
                for i, m in enumerate(NERDLE_MODES):
                    if m["id"] in CUT_MODES:
                        _nlog(f"[nerdle] cutting unwon mode chapter: {m['id']}")
                        continue
                    _cut = cuts[i] if i < len(cuts) else None
                    if _cut is None:
                        _nlog(f"[nerdle] cutting mode {m['id']}: no card "
                              f"start recorded (card never shown)")
                        continue
                    a, b = _cut
                    if b - a < 0.5:
                        continue
                    # The card is in-recording (shown before play); the
                    # segment opens with it. card_dur is the 2.5s hold,
                    # seg_dur the play after it. No still is prepended
                    # (that would duplicate the in-recording card).
                    cp = video_dir / f"mode_{i}.png"
                    if not cp.exists():
                        _nlog(f"[nerdle] cutting mode {m['id']}: card PNG "
                              f"missing (never a chapter with card_dur=0)")
                        continue
                    mode_parts.append(gameplay.subclip(a, b))
                    emitted.append((m, 2.5, max(0.0, (b - a) - 2.5)))
                for _s, (_m, _cd, _sd) in zip(
                        compute_nerdle_chapter_starts(
                            [(cd, sd) for (_m, cd, sd) in emitted]), emitted):
                    chapters.append((_s, f"{_m['name']} Solve"))
                if mode_parts:
                    parts.extend(mode_parts)
                    cursor = sum(cd + sd for (_m, cd, sd) in emitted)
                else:
                    parts.append(gameplay)
                    cursor = gd
                # analysis slides (reuse quordle def/freq/facts with nerdle equations as words)
                # Skipped: they print answers up front and say QUORDLE. The video
                # is mode cards + real solves only.
                final_clip = concatenate_videoclips(parts, method="compose")
                # NO shared brand intro: wordsolverx-intro-20s.mp4
                # opens EVERY video with a wrong-game, 12-day-stale
                # Wordle answer (MOCHA, Oct 5). The mode cards open
                # the video instead (fix-loop iter 11).
                _nlog(f"[nerdle] chapters: {[(round(float(s), 1), t) for s, t in chapters]}")
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

    # Iteration 2: never upload an unsolved nerdle video (same rule as runner).
    if YOUTUBE_AVAILABLE and final and all_solved:
        title_v = [
            f"Nerdle Answer Today ({date_short}) - All 9 Modes Solved!",
            f"Nerdle Answer Today - {today} Classic/Mini/Maxi + More",
            f"Nerdle Answers Today - {today} Full Solve + Hints",
        ]
        title = title_v[day_num % len(title_v)]
        # Upload description fallback must NOT mutate `chapters`: the
        # (0, mode) fallback is a display placeholder, and result.json
        # below must record only real assembly chapter times.
        up_chapters = chapters or [(0, f"{m['name']} Solve")
                                   for m in NERDLE_MODES]
        mode_lines = []
        if isinstance(official, dict):
            for m in NERDLE_MODES:
                # Description lists what was PLAYED, not the official list.
                eqs = PLAYED.get(m["id"]) or official.get(m["id"]) or []
                if eqs:
                    mode_lines.append(f"{m['name']}: {' | '.join(eqs)}")
        chap_str = "\n".join(f"{int(s//60)}:{int(s%60):02d} {l}" for s, l in up_chapters)
        desc = (f"Nerdle Answer Today — {today} | All 9 Modes Solved\n\n"
                + ("\n".join(mode_lines) + "\n\n" if mode_lines else "")
                + f"Watch every mode solved step by step with hints.\n\nCHAPTERS:\n{chap_str}\n\n"
                f"Nerdle solver: https://wordsolverx.com/nerdle-solver\n\n"
                f"#Nerdle #NerdleAnswerToday #DailyPuzzle #MathPuzzle #WordGame")
        tags = ["nerdle answer today", "Nerdle", "Nerdle Answer", "Daily Puzzle",
                "Math Puzzle", "Nerdle Classic", "Nerdle Mini", "Nerdle Maxi",
                "Nerdle Hints", "Nerdle Solver"]
        vid = upload_to_youtube(str(final), title=title, description=desc,
                                chapters=up_chapters, tags=tags, ytd_info={})
        print(f"[upload] {vid}")
    import json as _json
    # Iteration 2: write the REAL chapter times computed during assembly
    # (previously result.json had no chapters, so the runner invented
    # (0, mode) entries) and the official equations per mode (previously
    # nothing recorded the answers). typed counts probes + official guesses;
    # expected counts official equations only — they are NOT comparable,
    # so the evidence must not present them as a fraction: judge a mode by
    # signal == "won" only (see counts_note).
    _eq_rep = equations_report(PLAYED, official if isinstance(official, dict) else {})
    _res_doc = {"game": "nerdle", "date": date_key, "solved": all_solved,
         "modes": [{"id": mid, "typed": t, "expected": e, "signal": s,
                    "equations_played": list(PLAYED.get(mid) or []),
                    "equations_official": list(
                        (official or {}).get(mid) or [])}
                   for mid, t, e, s in results],
         "counts_note": ("typed counts probes+official guesses; expected "
                         "counts official equations only; they are NOT "
                         "comparable — judge a mode by signal=='won' only."),
         "chapters": [[round(float(s), 1), str(t)] for s, t in chapters],
         **_eq_rep,
         "unproven_modes": _unproven,
         "served": SERVED,
         "api_puzzle_numbers": api_numbers,
         "number_gate": {"blocked": _wd_blocked, "detail": _wd_detail,
                         "per_mode": _wd_per_mode,
                         "warnings": _wd_warnings,
                         "use_local_time": True},
         "first_typing_s": _FIRST_TYPE_T}
    (video_dir / f"result_{date_key}.json").write_text(_json.dumps(_res_doc, indent=1))
    # The runner's run_nerdle_external only reads the FLAT
    # videos/result_<date>.json (it never looks in videos/nerdle/), so
    # leave an identical copy there for runner pickup — it renames that
    # copy into videos/nerdle/ afterwards.
    try:
        (script_dir / "videos" / f"result_{date_key}.json").write_text(
            _json.dumps(_res_doc, indent=1))
    except Exception as _e:
        _nlog(f"[nerdle] flat result copy failed: {str(_e)[:120]}")
    print("Done!" if all_solved else "NOT SOLVED — see result json")
    import sys as _sys
    _sys.exit(0 if all_solved else 1)


if __name__ == "__main__":
    asyncio.run(main())
