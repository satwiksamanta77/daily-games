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
NERDLE_API_PROVEN_MODES = ("classic", "mini", "midi", "maxi")
NERDLE_API_UNPROVEN_MODES = ("micro", "minibi", "quad", "instant", "speed")


def _nerdle_number_gate(served, api_numbers):
    """Hard gate (iter 14 rework): hard-check served == API ONLY for
    the 4 PROVEN modes (classic, mini, midi, maxi). Returns
    (blocked, detail, per_mode dict).

    - proven modes: an exposed header_number must be a MEMBER of the
      API's puzzleNumbers for that mode; a served-answer mismatch
      (matches_target_official is False) BLOCKS. Classic additionally
      REQUIRES a header number (missing blocks) — classic is the day
      proof. For mini/midi/maxi a missing header is a warning (the
      site may not expose it), but the answer check still gates.
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


def _nerdle_header_number_from_probe(probe):
    """Multi-strategy header-number capture (iter 14, P0).

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
    """
    probe = probe if isinstance(probe, dict) else {}
    header_text = str(probe.get("header_text") or "")

    def _nums(text):
        out = []
        for pat in (r"nerdle\s*#\s*(\d+)", r"#\s*(\d+)",
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
  const headerEls = Array.from(document.querySelectorAll(
    'header, h1, h2, [class*=header i], [id*=header i], ' +
    '[class*=title i], [id*=title i]'))
    .filter(e => e && e.offsetParent !== null);
  const header_text = headerEls
    .map(e => (e.innerText || '').trim()).filter(Boolean).join(' | ')
    .slice(0, 2000);
  const hash_texts = [];
  for (const e of Array.from(document.querySelectorAll('body *'))) {
    if (hash_texts.length >= 20) break;
    if (!e || e.offsetParent === null) continue;
    const t = (e.innerText || '').trim();
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
                           hash_texts: hash_texts,
                           data_attrs: data_attrs,
                           url: window.location.href,
                           body_text: (document.body.innerText || '')
                             .slice(0, 8000) },
           gameState_keys: keys, dateish: dateish };
} catch (e) { return { error: String(e) }; } }
"""

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


async def type_equation(page, eq, delay=200):
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
    # retry 2: clear popups, refocus, clear row, retype — BUT never retype an
    # equation that is already sitting in the grid (that printed the same
    # attempt twice on camera in quad mode). If it is there, Enter only.
    await accept_cookies(page)
    try:
        _st = await read_state(page, cols, tries=2)
        _flat = "".join(
            str(t) for g in (_st or []) for t in (g.get("texts") or []))
        _norm = "".join(ch for ch in _flat if ch.isalnum() or ch in "+-*/=")
        _want = "".join(ch for ch in str(eq) if ch.isalnum() or ch in "+-*/=")
        if _want and _want in _norm:
            print(f"[type] {eq} already in grid, Enter only (no retype)")
            try:
                await page.keyboard.press("Enter")
            except Exception:
                pass
            state, counts, ok = await done(await wait_change(page, cols, prev, timeout=8))
            print(f"[type] {eq} enter-only ok={ok} counts={counts}")
            return state, counts, ok
    except Exception:
        pass
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
            await page.wait_for_timeout(1500)
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
                    PLAYED[mode["id"]] = [eqs[0]]
                    _nlog(f"[{mode['id']}] guess 1: {eqs[0]} "
                          f"(instant hint solve; submitted)")
                else:
                    _nlog(f"[{mode['id']}] guess 1: {eqs[0]} "
                          f"NOT submitted (board did not react); "
                          f"not counted as played")
                    PLAYED[mode["id"]] = []
        elif not state:
            print(f"[{mode['id']}] no pb-grid found, legacy direct typing")
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
            # Equations WE typed this run (typed_guesses also holds
            # pre-filled starter rows from load; PLAYED must be ours only).
            played_here = []
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
                if rows_left <= 2 and need and len(typed_guesses) >= min_probes:
                    # user rule: official answer only in the last 2 attempts,
                    # and never before min_probes real solver probes
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
                if ok:
                    _mark_first_type()
                if not ok:
                    print(f"[{mode['id']}] board did not react to {nxt!r}, stopping")
                    break
                typed += 1
                typed_guesses.append(nxt)
                played_here.append(nxt)
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
                _nlog(f"[{mode['id']}] guess {typed}: {nxt} "
                      f"rows_left~{rows_left - 1} hist={len(hists.get(first_open, []))}")
            PLAYED[mode["id"]] = list(played_here)
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
        _site_only = [e for e in (site_eqs + gs_eqs) if e]
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
        _hn, _hn_strategy, _hn_raw = _nerdle_header_number_from_probe(
            _served.get("header_probe"))
        SERVED[mode["id"]] = {
            "page_local": _served.get("page_local"),
            "page_iso": _served.get("page_iso"),
            "tz_offset_min": _served.get("tz_offset_min"),
            "header_number": _hn,
            "header_strategy": _hn_strategy,
            "header_text": _hn_raw,
            "gameState_dateish": _served.get("dateish"),
            "site_solutions": _site_only,
            "matches_target_official": _matches}
        if _speed_detail:
            SERVED[mode["id"]]["speed_match"] = _speed_detail
        _nlog(f"[{mode['id']}] served page_local={_served.get('page_local')} "
              f"gameState_dateish={_served.get('dateish')} "
              f"site_solutions={_site_only} matches_target_official={_matches} "
              f"header_number={_hn} header_strategy={_hn_strategy} "
              f"header_text={_hn_raw[:160]!r} "
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
                won = await page.evaluate(WIN_JS, {"cols": cols, "boards": boards_n})
                if won:
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
        # Fix-loop iter 13 revised: the site's daily index follows
        # localStorage `useLocalTime` (default false = UTC day). The
        # browser clock is faked to the EFFECTIVE date 00:05 local
        # (see above), so with the site's own local-midnight setting
        # on ("Change game to change at local midnight"), the site
        # serves the effective date's puzzle — the same date the API
        # fetch + number gate check.
        try:
            await context.add_init_script(
                script="try{localStorage.setItem('useLocalTime','true')}catch(e){}")
            print("[nerdle] useLocalTime=true (site local-midnight rollover)")
        except Exception as e:
            print(f"[nerdle] useLocalTime init skipped: {e}")
        try:
            if QP_AVAILABLE:
                await context.add_init_script(script=QP.prepurge_js())
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
    _wd_blocked, _wd_detail, _wd_per_mode = _nerdle_number_gate(
        SERVED, api_numbers)
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
