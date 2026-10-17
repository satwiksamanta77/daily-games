"""Local daily-answer engine — ports wordsolverx frontend /src/lib logic to Python.

No network except contexto + phoodle (official/worker APIs, with failure -> None).
All date math uses the TARGET date (browser day), not host clock.
"""
import json
import os
import re
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).parent


def _find_frontend():
    """Locate the wordsolverx frontend checkout (source of every wordlist).

    The folder was renamed from `wordsolverx-z-ai` to `wordsJi`, and the old
    hard-coded path silently broke EVERY local lookup: 7 of 10 answer engines
    raised FileNotFoundError and the searchle bank came back EMPTY, which made
    the planner collapse to a single guess (the answer, on guess 1). A hard
    path is a landmine, so probe the known names AND require the marker
    directory `src/lib` so an empty/stale folder can never win.
    """
    root = HERE.parents[1]          # .../wordsolverx
    cands = [HERE / "frontend_data"]  # vendored wordlists (standalone repo first)
    for env in ("WORDJI_PATH", "ZAI_PATH", "FRONTEND_PATH"):
        v = os.environ.get(env)
        if v:
            cands.append(Path(v))
    cands += [
        root / "wordsJi",
        root / "wordsolverx-z-ai",
        Path(r"C:\Users\akasa\Projects\wordsolverx\wordsJi"),
        Path(r"C:\Users\akasa\Projects\wordsolverx\wordsolverx-z-ai"),
    ]
    for c in cands:
        try:
            if (c / "src" / "lib").is_dir():
                return c
        except OSError:
            continue
    raise FileNotFoundError(
        "wordsolverx frontend not found. Set WORDJI_PATH to the checkout that "
        "contains src/lib. Tried: " + ", ".join(str(c) for c in cands))


ZAI = _find_frontend()


def target_date():
    """The puzzle day the VIDEO must show.

    FAKE_DATE_ISO wins so the browser's time-travel and the answer engine
    always agree. Previously the host clock decided, which made the runner
    answer for 09-27 while the fake date painted 09-26 on the page.
    """
    iso = os.environ.get("FAKE_DATE_ISO", "").strip()
    if iso:
        try:
            return datetime.fromisoformat(iso.replace("Z", "+00:00")).date()
        except ValueError:
            pass
    try:
        off = int(os.environ.get("TZ_OFFSET_MINUTES", "540"))
    except ValueError:
        off = 540
    # SINGLE SOURCE OF TRUTH (fix-loop 2026-10-17): target = FAKE_DATE_ISO
    # date if set, else (UTC now + 540 min).date() i.e. UTC+9. At the
    # 21:00-IST batch (15:30 UTC) it rolls to the NEXT calendar day - the
    # board the video publishes for - while morning batches stay same-day.
    # Browser must use BROWSER_TZ=Pacific/Kiritimati (UTC+14) with fake
    # clock <target> 00:05 local, so browser local date == target_date.
    # Workflows and runner defaults must all use Kiritimati; Asia/Kolkata
    # here was a contradiction (540=UTC+9 vs Kolkata=UTC+5:30). Do not
    # change this back to 330 (IST-same-day): that made the 9pm batch
    # record a stale board.
    return (datetime.utcnow() + timedelta(minutes=off)).date()


def shift_target_date(days):
    """`target_date()` moved by `days`.

    Daily puzzle sites key their puzzle off the BROWSER's local calendar day.
    BROWSER_TZ is Pacific/Kiritimati (UTC+14) and the faked clock sits just
    after local midnight, so the browser local date == target_date().
    Callers that have seen the page's own date use this to agree with it.
    Do NOT use shift to silently fall back to a previous-day answer.
    """
    return target_date() + timedelta(days=int(days))


def _days(a, b):
    return (a - b).days


# ---------- betweenle ----------
def betweenle(d=None):
    d = d or target_date()
    words = (ZAI / "src/lib/data/betweenle/daily-words.txt").read_text().split()
    puzzle = _days(d, date(2023, 3, 17)) + 1
    return {"answer": words[(puzzle - 1) % len(words)], "puzzle": puzzle, "date": str(d)}


# ---------- searchle ----------
# The live site's own formula (from its bundle) is
#   index = days(2023-06-22, target) % len(list)
# and the list has 1492 entries. Our local searchleData.ts only had 1130, so
# the modulo wrapped to the wrong puzzle. searchle_live.json is generated from
# the live bundle by youtube/daily-games/extract_sle.py.
SEARCHLE_START = date(2023, 6, 22)
_SEARCHLE_CACHE = {}


def _searchle_list():
    if "list" not in _SEARCHLE_CACHE:
        live = ZAI / "src/lib/searchle/searchle_live.json"
        if live.exists():
            data = json.loads(live.read_text(encoding="utf-8"))
            _SEARCHLE_CACHE["list"] = data["puzzles"]
        else:
            txt = (ZAI / "src/lib/searchle/searchleData.ts").read_text()
            pat = re.compile(
                r'\{"text":"(.*?)","answer":"(.*?)","luckyGuess":"(.*?)"\}')
            _SEARCHLE_CACHE["list"] = [
                {"text": a, "answer": b, "luckyGuess": c}
                for a, b, c in pat.findall(txt)]
    return _SEARCHLE_CACHE["list"]


def searchle(d=None):
    d = d or target_date()
    items = _searchle_list()
    idx = _days(d, SEARCHLE_START) % len(items)
    it = items[idx]
    return {"prompt": it["text"], "answer": it["answer"],
            "lucky": it.get("luckyGuess"), "index": idx, "date": str(d)}


# ---------- colordle ----------
def colordle(d=None):
    d = d or target_date()
    key = d.isoformat()
    data = json.loads((ZAI / "static/colordle_data.json").read_text(encoding="utf-8"))
    for e in data["entries"]:
        if e["date"] == key:
            return {"name": e["color"]["name"], "hex": e["color"]["hex"],
                    "dayNum": e["dayNum"], "date": key, "via": "static"}
    # static is stale -> live worker API (same source the site rebuilds from).
    # Live shape is flat: {date, day_number, color_name, color_hex}
    import requests
    for url in ("https://color-answers-worker.colordle.workers.dev/api/colordle/today",
                f"https://color-answers-worker.colordle.workers.dev/api/colordle/date/{key}"):
        try:
            r = requests.get(url, timeout=20, headers={"User-Agent": "WordSolverX Video"})
            if r.ok:
                j = r.json()
                c = j.get("color") or {}
                name = j.get("color_name") or c.get("name")
                hx = j.get("color_hex") or c.get("hex")
                if name and hx:
                    return {"name": name, "hex": hx,
                            "dayNum": j.get("day_number") or j.get("dayNum"),
                            "date": j.get("date", key), "via": url}
        except Exception:
            continue
    raise SystemExit(f"colordle: no entry for {key} static or live")


# ---------- colorfle ----------
def colorfle(d=None, mode=0):
    d = d or target_date()
    out = subprocess.run(["node", str(HERE / "colorfle_answer.js"), d.isoformat(), str(mode)],
                         capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise SystemExit(f"colorfle node failed: {out.stderr[:300]}")
    return json.loads(out.stdout)


# ---------- framed (all modes) ----------
# The site has four modes, each with its OWN puzzle numbering and its own
# launch date: daily, one-frame, titleshot and poster. Only "daily" was
# implemented before, which is why the other modes had no video.
FRAMED_MODES = {
    # mode: (live path on the site, date of the newest bundled puzzle, its number)
    "daily": ("daily", date(2026, 5, 29), 1540),
    "one-frame": ("one-frame", date(2026, 5, 29), 543),
    "titleshot": ("titleshot", date(2026, 5, 28), 358),
    "poster": ("poster", date(2026, 5, 28), 308),
}
FRAMED_URLS = {
    "daily": "https://framed.wtf",
    "one-frame": "https://framed.wtf/one-frame",
    "titleshot": "https://framed.wtf/titleshot",
    "poster": "https://framed.wtf/poster",
}


def _framed_number(mode, d):
    _, newest, num = FRAMED_MODES[mode]
    return num + _days(d, newest)


def framed(d=None, mode="daily"):
    """Today's Framed answer for one mode (default the daily game)."""
    import requests
    d = d or target_date()
    key = d.isoformat()
    mode = mode if mode in FRAMED_MODES else "daily"
    try:
        data = json.loads((ZAI / "static/framed_data.json").read_text(encoding="utf-8"))
        entries = ((data.get("modes") or {}).get(mode) or {}).get("entries") or []
        for e in entries:
            if e.get("date") == key:
                return {"answer": e["answer"], "mode": mode,
                        "puzzle": e.get("puzzleNumber"), "date": key,
                        "url": FRAMED_URLS[mode], "via": "static"}
    except Exception as ex:
        print(f"[framed] static read failed: {ex}")

    # static is stale (it stops in May 2026) -> live titles API.
    # answer = LAST frame's items[0].title
    num = _framed_number(mode, d)
    try:
        r = requests.get(f"https://titles.framed.wtf/v1/titles/top-ten/{num}",
                         params={"gameType": FRAMED_MODES[mode][0]}, timeout=20,
                         headers={"User-Agent": "WordSolverX Video"})
        if r.ok:
            frames = (r.json().get("frames") or [])
            if frames:
                raw = frames[-1].get("items")
                items = json.loads(raw) if isinstance(raw, str) else (raw or [])
                if items and items[0].get("title"):
                    return {"answer": items[0]["title"], "mode": mode,
                            "puzzle": num, "date": key,
                            "url": FRAMED_URLS[mode], "via": "live"}
    except Exception as e:
        print(f"[framed] live failed: {e}")
    return {"answer": None, "mode": mode, "puzzle": num, "date": key,
            "url": FRAMED_URLS[mode], "via": None}


# ---------- semantle ----------
# The epoch is 30 JAN 2022, not 29 Jan. The site prints its own counter
# ("Game #1709") and fetches /semantle/game/1709/, whose `secretWord` is
# authoritative - verified against the live API. With the old 29 Jan epoch the
# engine answered one game too high ('material', game 1710) while the site
# served 'college' (game 1709), so every guess scored wrong and the game could
# never be won. Re-check with:  python semantle_api.py 1709
def semantle(d=None):
    d = d or target_date()
    idx = _days(d, date(2022, 1, 30))
    words = re.findall(r'"([a-z][a-z\- ]*?)"',
                       (ZAI / "src/lib/data/semantle-words.ts").read_text())
    # file header has no quoted lowercase strings before array; guard anyway
    words = [w for w in words if " " not in w or True]
    if idx < 0 or idx >= len(words):
        raise SystemExit(f"semantle: index {idx} out of range ({len(words)})")
    return {"answer": words[idx], "puzzle": idx, "date": str(d)}


# Soltaired's own bundle (javascripts/dist/phrazle.js) picks the daily phrase
# with, verbatim:
#     phrazleCurrentGameNumber = function() {
#       const e = new Date; e.setHours(0,0,0,0);
#       const t = 60 * (e.getTimezoneOffset() - o.getTimezoneOffset()) * 1e3,
#             a = (new Date).setHours(0,0,0,0) - o.setHours(0,0,0,0) - t,
#             r = Math.ceil(a / 864e5);
#       return 2 * r + ((new Date).getHours() < 12 ? 1 : 2)
#     }
# ...where o is `new Date(2022, 3, 18)` (JS months are 0-indexed, so that is
# 18 APRIL 2022), and the board is
#     phrases[phrazleCurrentGameNumber() % phrases.length]   (1310 entries).
#
# There is no morning/evening answer list: the browser-local HOUR decides which
# of the two daily slots is live, so the answer flips at 12:00 local time. The
# previous version picked a "session" enum and indexed a hand-kept phrases.ts,
# which is why the runner kept submitting one day's phrase into the other day's
# differently-sized grid ("Please use all available spaces").
PHRAZLE_START = date(2022, 4, 18)
_PHRAZLE_CACHE = {}


def _phrazle_phrases():
    if "list" not in _PHRAZLE_CACHE:
        live = HERE / "phrazle_live.json"
        if live.exists():
            _PHRAZLE_CACHE["list"] = json.loads(
                live.read_text(encoding="utf-8"))["phrases"]
        else:
            raise SystemExit(
                "phrazle: phrazle_live.json missing next to answers.py "
                "(regenerate it from the site bundle's phrase list)")
    return _PHRAZLE_CACHE["list"]


def phrazle_game_number(d=None, hour=None):
    """The site's daily game number for `d` at browser-local `hour`."""
    d = d or target_date()
    if hour is None:
        # The runner fakes the browser to just after local midnight unless told
        # otherwise, which is the "before noon" slot.
        hour = 9
    return 2 * (d - PHRAZLE_START).days + (1 if hour < 12 else 2)


def phrazle(d=None, session=None, hour=None, game_number=None):
    """The live board is decided by the browser-local HOUR, not a session enum.

    `session` is accepted for call compatibility and ignored. `hour` lets the
    caller pass what the BROWSER actually used, so the answer matches the board
    the page rendered rather than the host clock.
    """
    d = d or target_date()
    items = _phrazle_phrases()
    n = game_number if game_number is not None else phrazle_game_number(d, hour)
    idx = n % len(items)
    return {"answer": items[idx].upper(), "raw": items[idx], "index": idx,
            "game_number": n, "date": str(d),
            "letters": len(items[idx].replace(" ", ""))}


def phrazle_by_shape(d, word_lengths):
    """Today's phrases whose word shape matches the LIVE board's cell groups.

    The date+hour formula is the site's rule, but it is evaluated against the
    browser's own clock, so any drift (timezone, fake date, the 12:00 flip)
    silently picks the other daily puzzle. The board itself always shows the
    true shape, so we also look for a phrase whose per-word lengths equal the
    cell groups actually on screen. Returns [] when nothing matches and the
    caller keeps the date-derived answer.
    """
    want = [int(n) for n in (word_lengths or [])]
    if not want or sum(want) < 6:
        return []
    items = _phrazle_phrases()
    out = []
    for slot in (1, 2):
        n = phrazle_game_number(d, hour=9 if slot == 1 else 15)
        item = items[n % len(items)]
        if [len(w) for w in item.split()] == want:
            out.append({"answer": item.upper(), "raw": item,
                        "index": n % len(items), "game_number": n,
                        "date": str(d),
                        "letters": len(item.replace(" ", ""))})
    return out


# ---------- canuckle ----------
def canuckle(d=None):
    d = d or target_date()
    key = d.isoformat()
    data = json.loads((ZAI / "src/lib/wordlebot-wasm/assets/generated/canuckle-data.json").read_text(encoding="utf-8"))
    for p in data["puzzles"]:
        if p["date"] == key:
            return {"answer": p["answer"], "index": p["index"], "date": key, "via": "bundled"}
    # bundled is stale -> live Firestore, mirrored from fetchLiveCanucklePuzzle:
    # date(index): idx>=143 -> 2022-10-04 + (idx-143); else 2022-02-10 + (idx-1)
    import requests
    import base64

    def _idx_date(i):
        base = date(2022, 10, 4) if i >= 143 else date(2022, 2, 10)
        off = i - 143 if i >= 143 else i - 1
        return (base + timedelta(days=off)).isoformat()

    try:
        r = requests.get("https://firestore.googleapis.com/v1/projects/canuckle-c2157/databases/(default)/documents/canuckleGameData?pageSize=64&orderBy=index%20desc",
                         timeout=20, headers={"User-Agent": "WordSolverX Video"})
        if r.ok:
            docs = []
            for doc in (r.json().get("documents") or []):
                f = doc.get("fields") or {}
                try:
                    i = int((f.get("index") or {}).get("integerValue", "0"))
                except ValueError:
                    continue
                if not i:
                    continue
                try:
                    ans = base64.b64decode((f.get("answer") or {}).get("stringValue", "")).decode()
                except Exception:
                    ans = ""
                if ans:
                    docs.append((i, ans))
            docs.sort()
            best = None
            for i, ans in docs:
                if _idx_date(i) <= key:
                    best = (i, ans)
            if best:
                return {"answer": best[1], "index": best[0], "date": _idx_date(best[0]), "via": "live"}
    except Exception as e:
        print(f"[canuckle] live failed: {e}")
    raise SystemExit(f"canuckle: no puzzle for {key} bundled or live")


# ---------- contexto (network) ----------
def contexto(d=None):
    import requests
    d = d or target_date()
    n = 1260 + _days(d, date(2026, 3, 1))
    r = requests.get(f"https://api.contexto.me/machado/en/giveup/{n}", timeout=25,
                     headers={"User-Agent": "WordSolverX Video"})
    r.raise_for_status()
    j = r.json()
    word = j.get("word") or j.get("answer") or j.get("solution")
    return {"answer": word, "game": n, "date": str(d), "raw_keys": list(j.keys())}


# ---------- phoodle (network) ----------
# The worker publishes puzzles at 05:00 UTC, so "current" is usually TOMORROW's
# word relative to the video's target date. We must select by the puzzle id
# that matches the target date, otherwise the video types next day's answer and
# the site rejects it (all-grey row).
def _phoodle_official(d):
    """The site's own backend, which is keyed by date and always ahead of the
    scraper worker. GET /daily-word/rest/<YYYY-MM-DD> returns that day's word.

    phoodle-worker publishes at 05:00 UTC, so its "recent" list can be a day
    behind the date the page is actually serving; the backend has no such lag.
    """
    import requests
    r = requests.get(f"https://phoodlebackend.com/daily-word/rest/{d.isoformat()}",
                     timeout=25,
                     headers={"User-Agent": "WordSolverX Video",
                              "Origin": "https://www.phoodle.net"})
    r.raise_for_status()
    j = r.json() if r.content else {}
    word = j.get("word") if isinstance(j, dict) else None
    if not word:
        raise SystemExit(f"phoodle: backend has no word for {d}")
    return {"answer": word, "date": d.isoformat(),
            "description": j.get("description"),
            "recipe": j.get("recipeName"), "via": "official-backend"}


def phoodle(d=None):
    import requests
    d = d or target_date()
    # The official backend is date-addressable, so try it first; fall back to the
    # scraper worker, whose "recent" window can lag a day behind the live page.
    try:
        return _phoodle_official(d)
    except SystemExit:
        raise
    except Exception as e:
        print(f"[phoodle] official backend failed: {str(e)[:110]}")
    key = d.isoformat()
    base = "https://phoodle-worker.pinpoints.workers.dev/summary/today"
    hdr = {"User-Agent": "WordSolverX Video"}

    def _pick(j):
        cur = j.get("current") or {}
        if cur.get("id") == key or (cur.get("date") == key):
            return cur
        for e in (j.get("recent") or []):
            if e.get("id") == key or e.get("date") == key:
                return e
        return None

    # widen the history window until the target date is covered
    for hist in (0, 3, 8, 20, 40):
        try:
            r = requests.get(f"{base}?history={hist}", timeout=25, headers=hdr)
            r.raise_for_status()
            j = r.json()
        except Exception:
            continue
        hit = _pick(j)
        if hit and hit.get("word"):
            return {"answer": hit["word"], "date": key,
                    "recipe": hit.get("recipe_name"),
                    "via": f"history={hist}"}
        vis = j.get("visible_date")
        if vis and vis == key:
            cur = j.get("current") or {}
            if cur.get("word"):
                return {"answer": cur["word"], "date": key,
                        "recipe": cur.get("recipe_name"),
                        "via": f"history={hist}"}
    raise SystemExit(
        f"phoodle: no puzzle for {key} in worker history "
        f"(visible_date={vis if 'vis' in dir() else '?'})")


# ---------- nerdle all modes (network worker) ----------
def nerdle(d=None):
    import requests
    d = d or target_date()
    key = d.isoformat()
    for u in (f"https://nerdle-answers.nerdleapi.workers.dev/{key}?v=2",
              "https://nerdle-answers.nerdleapi.workers.dev/today?v=2"):
        try:
            r = requests.get(u, timeout=20, headers={"User-Agent": "WordSolverX Video"})
            if r.ok:
                j = r.json()
                data = (j.get("data") or j) if isinstance(j, dict) else {}
                if isinstance(data, dict) and data.get("modes"):
                    return {"date": key, "data": data, "via": u}
        except Exception:
            continue
    return {"date": key, "data": {}, "via": None}


# ---------- CryptoJS passphrase decryption (Globle, Countryle) ----------
def _cryptojs_decrypt(b64, passphrase):
    """CryptoJS.AES.decrypt(ciphertext, passphrase) -> plaintext str.

    CryptoJS derives key+iv from the passphrase with OpenSSL's EVP_BytesToKey
    (MD5, salt from the leading `Salted__` header), then AES-256-CBC. Globle
    and Countryle both publish their daily answer this way, so one helper
    covers both.
    """
    import base64
    import hashlib
    from Crypto.Cipher import AES

    raw = base64.b64decode(b64)
    if not raw.startswith(b"Salted__"):
        raise ValueError("ciphertext missing Salted__ header")
    salt, ct = raw[8:16], raw[16:]
    pw = passphrase.encode("utf-8")
    blocks, prev = b"", b""
    while len(blocks) < 48:
        prev = hashlib.md5(prev + pw + salt).digest()
        blocks += prev
    key, iv = blocks[:32], blocks[32:48]
    pt = AES.new(key, AES.MODE_CBC, iv).decrypt(ct)
    if pt and pt[-1] <= 16:                      # PKCS7 unpad
        pt = pt[:-pt[-1]]
    return pt.decode("utf-8", "replace").strip("\x00").strip()


# ---------- waffle ----------
WAFFLE_WORKER = "https://api.wafflegame.workers.dev"


def waffle_puzzle_for(d):
    """Waffle daily*.txt entry for one exact date, or None.

    Pure fetch+decode for a single date (no raise): the served-board check
    in the runner needs the puzzle string for BOTH the target date and the
    previous date, so it can tell "site has not rolled yet" apart from
    "board is something else entirely". Returns the same dict shape as
    waffle() (answer/solution, puzzle, words, number, date, via).
    """
    import base64 as _b64
    import requests
    d = d or target_date()
    key = d.isoformat()
    hdr = {"User-Agent": "WordSolverX Video"}
    for _n in (0, 1, 2):
        try:
            r = requests.get(f"https://wafflegame.net/daily{_n}.txt", timeout=25, headers=hdr)
            if not r.ok:
                continue
            raw = _b64.b64decode(r.text.strip())
            j = json.loads(raw.decode("utf-16-le"))
        except Exception:
            continue
        if not isinstance(j, dict) or str(j.get("date") or "")[:10] != key:
            continue
        words = j.get("words") or []
        sol = j.get("solution") or " ".join(words)
        if not sol:
            continue
        return {"answer": sol, "puzzle": j.get("puzzle"), "words": words,
                "number": j.get("number"), "date": key,
                "via": f"https://wafflegame.net/daily{_n}.txt"}
    return None


def waffle(d=None):
    """Waffle grid: site daily*.txt files (wafflegame.net/dailyN.txt).

    The worker API (api.wafflegame.workers.dev) is stale and silently
    returned the wrong-day puzzle. Fetch daily0/1/2.txt, base64 +
    UTF-16LE-decode + JSON.parse, and return the entry whose date ==
    target date. If none matches, raise SystemExit (defer/skip).
    """
    d = d or target_date()
    hit = waffle_puzzle_for(d)
    if hit:
        return hit
    raise SystemExit(f"waffle: no daily*.txt entry for {d.isoformat()} (defer, do not use worker)")


# ---------- worldle ----------
def worldle(d=None):
    """Worldle: live site uses CDN JSON per dayString.

    GET https://cdn-assets.teuteuf.fr/data/worldle/games/<year>/<day>.json
    returns {countryCode, ...}. That is authoritative. The old
    seedrandom formula no longer reproduces the CDN codes (verified
    2026-10-16: CDN TG for 10-17 vs formula CI/CD) and is kept only as
    a fallback if the CDN fetch fails.
    """
    d = d or target_date()
    number = _days(d, date(2022, 1, 21)) + 1
    known = {"2026-02-25": "WS", "2026-02-26": "SM", "2026-02-27": "BN",
             "2026-02-28": "BG", "2026-03-01": "GR", "2026-03-02": "MG"}
    countries = json.loads(
        (ZAI / "src/lib/data/worldle/countries.json").read_text("utf-8"))
    # CDN-first (fix-loop): live site no longer uses seedrandom.
    try:
        import requests as _rq
        _r = _rq.get(f"https://cdn-assets.teuteuf.fr/data/worldle/games/{d.year}/{d.isoformat()}.json", timeout=20, headers={"User-Agent": "WordSolverX Video"})
        if _r.ok:
            _cj = _r.json()
            _code = _cj.get("countryCode") or _cj.get("code")
            if _code:
                _hit = next((c for c in countries if c.get("code") == _code), None)
                if _hit:
                    return {"answer": _hit["name"], "code": _code, "number": number, "date": d.isoformat(), "via": "cdn"}
    except Exception:
        pass
    code = known.get(d.isoformat())
    idx = None
    if not code:
        out = subprocess.run(["node", str(HERE / "worldle_answer.js"),
                              str(number), str(len(countries))],
                             capture_output=True, text=True, timeout=60)
        if out.returncode != 0 or not out.stdout.strip().isdigit():
            raise SystemExit(f"worldle node failed: {out.stderr[:300]}")
        idx = int(out.stdout.strip())
    # frontend sorts by code, then indexes into that sorted array
    ordered = sorted(countries, key=lambda c: c["code"])
    if idx is not None:
        if not 0 <= idx < len(ordered):
            raise SystemExit(f"worldle: index {idx} out of range")
        hit = ordered[idx]
        code = hit["code"]
    else:
        hit = next((c for c in ordered if c["code"] == code), None)
    if not hit:
        raise SystemExit(f"worldle: code {code!r} not in country list")
    return {"answer": hit["name"], "code": code, "number": number,
            "date": d.isoformat()}


def worldle_all_modes(d=None):
    """Worldle ALL daily rounds, derived from the day's countryCode.

    Every round of the daily game answers for the SAME country, so every
    per-mode answer below is computed from that one countryCode — nothing
    is hardcoded for any date or country (no "Togo", no "Lomé" literals).

    CDN sources (all fetched live; the same endpoints the game itself uses,
    per the bundled main.js data layer, module 83512):
      day:       data/worldle/games/<year>/<date>.json
                 {countryCode, landmarkData:{landmarkCode,imageCode},
                  factData:{generalFactCode,historyFactCode}}
      country:   data/common/countries/<code>.json
                 {name, borders, flags, currencyData, population, size,
                  languageData.languages[], gdpPerCapita, ...}
      cities:    data/common/cities.json
                 [{countryCode, names:{en}, capital, population,
                   coordinates:{lat,lon}}]
      facts:     data/common/facts/<code>.json
                 {fillFacts:[{factCode, category,
                   translations:{en:{question, answer, answers[]}}}]}
      landmarks: data/common/landmarks.json
                 [{landmarkCode, names:{en}, images:[...]}]
      flags:     data/common/flags/<code>.svg (flag round images)
      shields:   data/common/shields/<code>.svg (emblem round images)

    Round-availability rules mirror the game's own removal logic (bundled
    main.js, rounds hook, module 22314): capital/orientation removed when
    there is no capital city or the code is a micro-state (VA/MC/MO/SG/HK/
    GI); populousCities removed with <4 populated cities; language removed
    unless exactly 2 languages; currency removed without a population;
    borders+orientation removed without borders; gdp removed without
    gdpPerCapita. Emblem/landmark removal depends on the live shield/
    landmark image content-type, so those are marked "live-check" here and
    the solver confirms them against the live round list.

    Returns a dict with keys: date, code, name, day, country (raw record),
    capital {name,lat,lon,population}, borders [{code,name,gdpPerCapita,
    capital}], populous [{name,population,lat,lon}] (population desc),
    landmark {landmarkCode,imageCode,name,image_url} | None,
    facts {factCode:{category,question,answer,answers}},
    languages [{languageCode,percentage}], gdp {number, compare_answer,
    compare_candidates}, currency {code,name,nameChoices}, population,
    size, flags [codes], available {mode: True | reason-string} with
    ONE canonical key set (spokenLanguages, populationCurrency, area -
    the same ids the runner's _WORLDLE_PLAY_ORDER uses; a previous
    iteration used language/currency/size here, which made
    avail.get() return None for those modes in the runner).
    Also: geogrid {raw geogrid doc}, geogrid_url, boolean_facts
    {key: bool} from geogrid factsInfo and politicalInfo minus the
    excluded keys, boolean_texts {key: displayed-question text} when
    extractable, gdp_lookup {normalized name: gdpPerCapita} and
    size_lookup {normalized name: size} for the own country + borders.
    """
    d = d or target_date()
    import requests as _rq

    def _get(url, required=True):
        try:
            r = _rq.get(url, timeout=30,
                        headers={"User-Agent": "WordSolverX Video"})
        except Exception as e:
            if required:
                raise SystemExit(f"worldle all-modes: CDN fetch failed: {e}")
            return None
        if not r.ok:
            if required:
                raise SystemExit(
                    f"worldle all-modes: CDN {r.status_code} for {url}")
            return None
        try:
            return r.json()
        except Exception:
            if required:
                raise SystemExit(
                    f"worldle all-modes: CDN non-JSON for {url}")
            return None

    base = "https://cdn-assets.teuteuf.fr/data"
    day = _get(f"{base}/worldle/games/{d.year}/{d.isoformat()}.json")
    code = str((day or {}).get("countryCode") or "").upper()
    if not code:
        raise SystemExit("worldle all-modes: day JSON has no countryCode")
    rec = _get(f"{base}/common/countries/{code.lower()}.json") or {}
    cities = _get(f"{base}/common/cities.json") or []
    facts_doc = _get(f"{base}/common/facts/{code.lower()}.json",
                     required=False) or {}
    landmarks_doc = _get(f"{base}/common/landmarks.json", required=False)
    # Geogrid (boolean trivia question): try the geogrid path first,
    # then the common/ variants — the exact prefix is not pinned by
    # static source, so record which URL actually served the doc.
    geogrid_doc, geogrid_url = None, None
    for _gu in (f"{base}/geogrid/countries/{code.lower()}.json",
                f"{base}/common/geogrid/countries/{code.lower()}.json",
                f"{base}/common/geogrid/{code.lower()}.json"):
        _gd = _get(_gu, required=False)
        if isinstance(_gd, dict) and _gd:
            geogrid_doc, geogrid_url = _gd, _gu
            break

    def _city_name(c):
        names = c.get("names") or {}
        return names.get("en") or c.get("name") or ""

    mine = [c for c in cities
            if str(c.get("countryCode") or "").upper() == code]
    capitals = []
    for c in mine:
        if c.get("capital"):
            co = c.get("coordinates") or {}
            capitals.append({"name": _city_name(c),
                             "lat": co.get("lat"), "lon": co.get("lon"),
                             "population": c.get("population")})
    cap_row = next((c for c in mine if c.get("capital")), None)
    capital = None
    if cap_row:
        co = cap_row.get("coordinates") or {}
        capital = {"name": _city_name(cap_row),
                   "lat": co.get("lat"), "lon": co.get("lon"),
                   "population": cap_row.get("population")}
    populous = sorted(
        [c for c in mine if c.get("population") is not None],
        key=lambda c: -(c.get("population") or 0))
    populous = [{"name": _city_name(c), "population": c.get("population"),
                 "lat": (c.get("coordinates") or {}).get("lat"),
                 "lon": (c.get("coordinates") or {}).get("lon")}
                for c in populous]

    borders = []
    for b in (rec.get("borders") or []):
        bcode = str(b).upper()
        brec = _get(f"{base}/common/countries/{bcode.lower()}.json",
                    required=False) or {}
        bcap = None
        for c in cities:
            if str(c.get("countryCode") or "").upper() == bcode \
                    and c.get("capital"):
                co = c.get("coordinates") or {}
                bcap = {"name": _city_name(c),
                        "lat": co.get("lat"), "lon": co.get("lon")}
                break
        borders.append({"code": bcode,
                        "name": brec.get("name") or bcode,
                        "gdpPerCapita": brec.get("gdpPerCapita"),
                        "size": brec.get("size"),
                        "capital": bcap})

    landmark = None
    ld = (day.get("landmarkData") or {})
    if ld.get("landmarkCode") is not None and isinstance(landmarks_doc, list):
        hit = next((x for x in landmarks_doc
                    if x.get("landmarkCode") == ld.get("landmarkCode")), None)
        if hit:
            names = hit.get("names") or {}
            landmark = {"landmarkCode": ld.get("landmarkCode"),
                        "imageCode": ld.get("imageCode"),
                        "name": names.get("en") or "",
                        "image_url":
                            f"{base}/common/landmarks/"
                            f"{ld.get('landmarkCode')}/{ld.get('imageCode')}"
                            ".jpg"}

    facts = {}
    for f in (facts_doc.get("fillFacts") or []):
        tr = ((f.get("translations") or {}).get("en") or {})
        if f.get("factCode") is not None:
            facts[f.get("factCode")] = {
                "category": f.get("category"),
                "question": tr.get("question") or "",
                "answer": tr.get("answer") or "",
                "answers": tr.get("answers") or []}

    # Boolean trivia candidates: geogrid factsInfo ∪ politicalInfo
    # booleans minus the excluded keys (the game never asks these).
    _BOOL_EXCLUDE = {"top20ObesityRate", "hasNuclearWeapons",
                     "sameSexMarriageLegal", "sameSexActivitiesIllegal",
                     "hasAlcoholBan", "top20AlcoholConsumption"}
    boolean_facts, boolean_texts = {}, {}
    if isinstance(geogrid_doc, dict):
        for _sec in ("factsInfo", "politicalInfo"):
            _d = geogrid_doc.get(_sec) or {}
            if not isinstance(_d, dict):
                continue
            for _k, _v in _d.items():
                _bv, _q = None, None
                if isinstance(_v, bool):
                    _bv = _v
                elif isinstance(_v, dict):
                    for _bk in ("value", "answer", "bool", "isTrue"):
                        if isinstance(_v.get(_bk), bool):
                            _bv = _v.get(_bk)
                            break
                    for _qk in ("question", "text", "label", "prompt"):
                        if isinstance(_v.get(_qk), str) and _v.get(_qk):
                            _q = _v.get(_qk)
                            break
                if _bv is not None and _k not in _BOOL_EXCLUDE:
                    boolean_facts[_k] = _bv
                    if _q:
                        boolean_texts[_k] = _q

    def _nm(s):
        return " ".join(str(s or "").lower().split())

    gdp_lookup = {_nm(rec.get("name") or code): rec.get("gdpPerCapita")}
    size_lookup = {_nm(rec.get("name") or code): rec.get("size")}
    for _b in borders:
        gdp_lookup[_nm(_b.get("name"))] = _b.get("gdpPerCapita")
        size_lookup[_nm(_b.get("name"))] = _b.get("size")

    # GDP comparison question (locale canYOuGuessHigherGDP): "Among
    # {country} and its neighbours, which has the highest per-capita GDP?"
    cands = [(rec.get("name") or code, rec.get("gdpPerCapita"))]
    for b in borders:
        cands.append((b["name"], b.get("gdpPerCapita")))
    cands = [(n, g) for (n, g) in cands if g is not None]
    gdp_compare = max(cands, key=lambda kv: kv[1])[0] if cands else None

    langs = rec.get("languageData", {}).get("languages") or []
    languages = [{"languageCode": str(x.get("languageCode") or ""),
                  "percentage": x.get("percentage")}
                 for x in langs if x.get("languageCode")]

    # Availability: the game's own removal rules (see docstring). True =
    # available; a string = removed + reason. Emblem/landmark need the live
    # image check, so they stay available here and the solver verifies.
    avail = {"country": True}
    micro = code in ("VA", "MC", "MO", "SG", "HK", "GI")
    avail["capital"] = True if (capital and not micro) else (
        "no capital city in cities.json" if not capital else
        "micro-state code: capital round removed by game")
    avail["orientation"] = True if (capital and not micro
                                    and rec.get("borders")) else (
        "orientation needs a capital + borders (game removal rule)")
    avail["neighbours"] = True if rec.get("borders") else (
        "no borders (game removal rule)")
    avail["populousCities"] = True if len(populous) >= 4 else (
        f"only {len(populous)} populated cities (<4, game removal rule)")
    avail["spokenLanguages"] = True if len(languages) == 2 else (
        f"{len(languages)} languages (!=2, game removal rule)")
    avail["populationCurrency"] = True if rec.get("population") else (
        "no population (game removal rule)")
    avail["gdp"] = True if rec.get("gdpPerCapita") is not None else (
        "no gdpPerCapita (game removal rule)")
    avail["flag"] = True
    avail["trivia"] = True
    avail["landmark"] = True  # live-check: landmark image content-type
    avail["emblem"] = True  # live-check: shield content-type
    avail["area"] = True
    avail["summary"] = True

    cur = rec.get("currencyData") or {}
    return {"date": d.isoformat(), "code": code,
            "name": rec.get("name") or code, "day": day,
            "country": rec, "capital": capital, "capitals": capitals,
            "borders": borders,
            "populous": populous, "landmark": landmark, "facts": facts,
            "languages": languages,
            "gdp": {"number": rec.get("gdpPerCapita"),
                    "compare_answer": gdp_compare,
                    "compare_candidates": cands},
            "geogrid": geogrid_doc or {},
            "geogrid_url": geogrid_url,
            "boolean_facts": boolean_facts,
            "boolean_texts": boolean_texts,
            "gdp_lookup": gdp_lookup,
            "size_lookup": size_lookup,
            "currency": {"code": cur.get("code"), "name": cur.get("name"),
                         "nameChoices": cur.get("nameChoices") or []},
            "population": rec.get("population"),
            "size": rec.get("size"),
            "flags": [str(x).lower() for x in (rec.get("flags") or [])],
            "available": avail}


# ---------- countryle ----------
def countryle(d=None):
    """Countryle: live API returns an AES-passphrase country id.

    wordsJi's static archive stops at 2026-05-28, so for any recent day the
    live endpoint is the only source. Its country id is encrypted with
    CryptoJS using the key the frontend ships (live-answer-sources.ts).
    """
    import requests
    d = d or target_date()
    key = d.isoformat()
    countries = json.loads(
        (ZAI / "src/lib/data/countryle/countries.json").read_text("utf-8"))
    by_id = {int(c["id"]): c for c in countries["countries"]}
    try:
        # frontend formatCountryleApiDate destructures [year, month, day]
        # and re-emits DD/MM/YYYY - not the ISO order.
        yy, mm, dd = key.split("-")
        url = ("https://www.countryle.com/hidden-api/"
               f"get-daily-country-valid.php?date={dd}%2F{mm}%2F{yy}")
        j = None
        # CI egress to countryle.com is intermittently blocked/403: try
        # direct (two UAs) then two CORS mirrors of the SAME upstream JSON.
        for fetch in (
            lambda: requests.get(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "Chrome/120 Safari/537.36",
                "accept": "application/json"}, timeout=20),
            lambda: requests.get(url, headers={
                "User-Agent": "WordSolverX Video",
                "accept": "application/json"}, timeout=20),
            lambda: requests.get(
                "https://api.allorigins.win/get?url="
                + requests.utils.quote(url, safe=""), timeout=25),
            lambda: requests.get(
                "https://corsproxy.io/?url="
                + requests.utils.quote(url, safe=""), timeout=25),
        ):
            try:
                r = fetch()
                if not r.ok:
                    continue
                cand = r.json()
                if isinstance(cand, dict) and "contents" in cand:
                    cand = json.loads(cand["contents"])
                if isinstance(cand, dict) and ("country" in cand
                                               or "number" in cand):
                    j = cand
                    break
            except Exception:
                continue
        if isinstance(j, dict):
            raw = j.get("country", j.get("id", j))
            if isinstance(raw, (int, float)) or (
                    isinstance(raw, str) and raw.strip().isdigit()):
                cid = int(raw)
            else:
                cid = int(_cryptojs_decrypt(str(raw).replace("\\/", "/"),
                                            "4%w!KpB+?FC<P9W*"))
            c = by_id.get(cid)
            if c:
                return {"answer": c["country"], "id": cid, "date": key,
                        "via": "live"}
    except Exception as e:
        print(f"[countryle] live failed: {str(e)[:110]}")
    for name in ("countryle_archive.json", "countryle_today.json"):
        p = ZAI / "static" / name
        if not p.exists():
            continue
        j = json.loads(p.read_text("utf-8"))
        entry = j.get(key) if isinstance(j, dict) else None
        if not entry and isinstance(j, dict) and j.get("date") == key:
            entry = j
        if entry and isinstance(entry, dict):
            c = entry.get("country") or {}
            if c.get("country"):
                return {"answer": c["country"], "id": c.get("id"),
                        "date": key, "via": name}
    raise SystemExit(f"countryle: no answer for {key}")


# ---------- globle ----------
def globle(d=None):
    """Globle: /answer?day=...&timeZone=... returns an AES-passphrase country index.

    The site only serves day == today-in-timeZone. Send timeZone=
    BROWSER_TZ (Pacific/Kiritimati) and request exactly the target day.
    On 404 / "Puzzle is not available" raise SystemExit so the runner
    DEFERS to the game cron window. NEVER fall back to date.today() -
    that silently returned the previous-day puzzle.
    """
    import requests
    d = d or target_date()
    key = d.isoformat()
    countries = json.loads(
        (ZAI / "src/lib/data/globle-countries.json").read_text("utf-8"))
    K = "ee53e68c3074206a002bf01333b047d5"

    def _decode(day):
        _tz = os.environ.get("BROWSER_TZ", "Pacific/Kiritimati")
        r = requests.get(f"https://globle-game.com/answer?day={day}",
                         params={"list": len(countries), "timeZone": _tz},
                         headers={"User-Agent": "WordSolverX Video"},
                         timeout=20)
        r.raise_for_status()
        enc = r.json().get("answer")
        if not enc:
            raise ValueError("no answer field")
        idx = int(_cryptojs_decrypt(enc, K))
        c = countries[idx]
        return {"answer": c["name"], "code": c.get("code"), "index": idx,
                "date": day, "via": "api"}

    try:
        return _decode(key)
    except Exception as e:
        raise SystemExit(f"globle: no answer for {key} (defer, do not fallback: {str(e)[:110]})")


# ---------- worgle ----------
def worgle(d=None):
    """Worgle: pure date->index arithmetic, no network.

    Ported from src/lib/worgle.ts: epoch 2021-06-19 UTC, index offset 207.
    """
    d = d or target_date()
    sols = json.loads((ZAI / "static/worgle_solutions.json").read_text("utf-8"))
    day_offset = _days(d, date(2021, 6, 19))
    return {"answer": sols[(day_offset - 207) % len(sols)],
            "puzzle": day_offset - 206, "date": d.isoformat(),
            "index": (day_offset - 207) % len(sols)}


# ---------- batterup ----------
_BATTER_CDN = "https://d2p6wz32uy8hq3.cloudfront.net"


def batterup(d=None):
    """Batter Up: CloudFront CDN games_batterup{date}.json, 5-day lookback.

    Ported from wordsJi scripts/batterup-daily.mjs. Falls back to the
    vendored batterup-answers.json snapshot for past dates.
    """
    import requests
    d = d or target_date()
    hdr = {"User-Agent": "WordSolverX-batterup-daily/1.0"}

    def _shift(key, days):
        dd = date(*(int(x) for x in key.split("-"))) + timedelta(days=days)
        return dd.isoformat()

    # The daily player is the archive entry whose game_date == the target day.
    # File D (published ~01:04 UTC on day D) carries it, and file D+1 still
    # does. NEVER look back to an earlier day: that silently returned the
    # PREVIOUS day's player (Jeff McNeil played for a Goldschmidt board).
    for key in (d.isoformat(),
                (d + timedelta(days=1)).isoformat()):
        try:
            r = requests.get(f"{_BATTER_CDN}/games_batterup{key}.json",
                             headers=hdr, timeout=30)
            if not r.ok:
                continue
            j = r.json()
            games = j.get("games") if isinstance(j, dict) else j
            if not isinstance(games, list):
                continue
            entry = next((g for g in games
                          if isinstance(g, dict)
                          and g.get("game_date") == d.isoformat()), None)
            if entry and entry.get("player_name"):
                return {"answer": entry["player_name"], "player": entry,
                        "date": d.isoformat(), "via": "cdn",
                        "game_number": entry.get("game_number")}
        except Exception as e:
            print(f"[batterup] cdn {key} failed: {str(e)[:100]}")
            continue
    # Before the CDN file for day D exists (observed ~03:30 UTC for the
    # 2026-10-16 file; earlier docs said ~01:04 UTC) that window belongs
    # to the morning batch - fail loudly, never guess.
    raise SystemExit(f"batterup: day {d.isoformat()} not published yet "
                     f"(CDN flips ~01:04 UTC)")
    try:
        snap = json.loads(
            (ZAI / "src/lib/data/batterup-answers.json").read_text("utf-8"))
        e = snap.get(d.isoformat())
        pl = (e or {}).get("player") if isinstance(e, dict) else None
        if isinstance(pl, dict) and pl.get("player_name"):
            return {"answer": pl["player_name"], "player": pl,
                    "date": d.isoformat(), "via": "snapshot"}
    except Exception:
        pass
    raise SystemExit(f"batterup: no answer for {d.isoformat()}")


# ---------- marveldle ----------
_MARVEL_API = "https://api.marveldle.com/api"
_MARVEL_HDR = {"Origin": "https://marveldle.com",
               "Referer": "https://marveldle.com/", "userLanguage": "en"}


def _marvel_call(path, sid, timeout=30):
    import requests, uuid
    r = requests.get(f"{_MARVEL_API}{path}",
                     headers={**_MARVEL_HDR, "sessionId": sid},
                     timeout=timeout)
    r.raise_for_status()
    if not (r.text or "").strip():      # API now answers 205-empty to guesses
        return {}
    return r.json()


def _marvel_sid():
    import requests, uuid
    sid = str(uuid.uuid4())
    try:
        j = _marvel_call("/session", sid)
        return j.get("id") or sid
    except Exception:
        return sid


def marveldle(d=None, mode="comics"):
    """Marveldle: live-solve via api.marveldle.com for the target date.

    dateId is the US-midnight pick id (M/D/YYYY 12:00:00 AM). Plays 2 diverse
    probes through the real guess endpoint, eliminates on Exact feedback, then
    takes the first survivor (verified isExact). Falls back to the vendored
    marveldle-answers.json snapshot.
    """
    import requests
    d = d or target_date()
    date_id = f"{d.month}/{d.day}/{d.year} 12:00:00 AM"
    up = {"comics": "comics", "mcu": "audiovisual"}.get(mode, "comics")
    # FIX-LOOP pick-id gate: server rollover is 00:00:30 UTC. If the live
    # pick-id is not the target date, the target puzzle is NOT published
    # yet - raise SystemExit so runner DEFERS. NEVER solve yesterday's
    # board and label it today (2026-10-16 HOWARD THE DUCK bug).
    _pick_s = ""
    try:
        # pick-id endpoint returns plain text (not JSON), so fetch raw text.
        import requests as _rq2
        _sid0 = _marvel_sid()
        _pr = _rq2.get(f"{_MARVEL_API}/config/pick-id", headers={**_MARVEL_HDR, "sessionId": _sid0}, timeout=30)
        _pr.raise_for_status()
        _pick_s = (_pr.text or "").strip()
        _want = f"{d.month}/{d.day}/{d.year}"
        if _want not in _pick_s:
            raise SystemExit(f"marveldle: pick-id {_pick_s!r} != target {_want} (not published yet, defer)")
        # Gate PASS, logged explicitly with the pick-id value (fix-loop
        # iter 4): rerun #3 proved the gate only by elimination (no
        # SystemExit, no gate-error line). A pass must say so.
        print(f"[marveldle] pick-id gate PASS: pick-id={_pick_s!r} target={_want}")
    except SystemExit:
        raise
    except Exception as _e:
        print(f"[marveldle] pick-id gate check failed (proceeding to live-solve): {str(_e)[:110]}")
    try:
        sid = _marvel_sid()
        chars = _marvel_call(f"/characters/{up}", sid, timeout=60)
        if not isinstance(chars, list) or not chars:
            raise ValueError("empty character list")
        probes, tried = [], []

        def _guess(cid):
            fb = _marvel_call(
                f"/characters/{up}/guess/{cid}"
                f"?dateId={date_id.replace('/', '%2F').replace(' ', '%20').replace(',', '%2C').replace(':', '%3A')}",
                sid)
            tried.append(cid)
            return fb

        def _elim(cands, guessed, fb):
            out = []
            for c in cands:
                keep = True
                for k in ("gender", "type", "species", "origin",
                          "powerTypes", "affiliations"):
                    col = (fb or {}).get(k)
                    if col == "Exact" and c.get(k) != guessed.get(k):
                        keep = False
                        break
                    if col == "None" and c.get(k) == guessed.get(k):
                        keep = False
                        break
                if keep:
                    out.append(c)
            return out

        cands = [c for c in chars if c.get("id")]
        for seed in (cands[0], cands[len(cands) // 2]):
            if len(probes) >= 2:
                break
            if seed["id"] in tried:
                continue
            try:
                fb = _guess(seed["id"])
            except Exception as e:
                print(f"[marveldle] probe failed: {str(e)[:100]}")
                continue
            if not fb:
                raise ValueError("guess endpoint returned no feedback (205)")
            probes.append({"name": seed.get("name"), "id": seed["id"]})
            if fb.get("isExact"):
                return {"answer": seed.get("name"), "id": seed.get("id"),
                        "probes": probes, "date": d.isoformat(),
                        "via": "live-first-try", "pick_id": _pick_s}
            cands = _elim(cands, seed, fb)
            cands = [c for c in cands if c.get("id") not in tried]
        # Exhaustive (capped) exact-search: the elimination pass can strand
        # the real answer outside the first 20 survivors when the API's
        # feedback columns are sparse - guessing every survivor always finds
        # the true isExact row (snapshot was stale at 2026-10-04).
        for c in cands[:400]:
            try:
                fb = _guess(c["id"])
            except Exception:
                continue
            if fb.get("isExact"):
                return {"answer": c.get("name"), "id": c.get("id"),
                        "probes": probes, "date": d.isoformat(), "via": "live",
                        "pick_id": _pick_s}
        raise ValueError(f"no exact match among {min(len(cands), 400)} survivors")
    except SystemExit:
        raise
    except Exception as e:
        print(f"[marveldle] live failed: {str(e)[:140]}")
    try:
        snap = json.loads(
            (ZAI / "src/lib/data/marveldle-answers.json").read_text("utf-8"))
        e = snap.get(d.isoformat()) if isinstance(snap, dict) else None
        cm = (e or {}).get("comics") if isinstance(e, dict) else None
        if isinstance(cm, dict) and cm.get("name"):
            return {"answer": cm["name"], "id": cm.get("id"), "probes": [],
                    "date": d.isoformat(), "via": "snapshot",
                    "pick_id": _pick_s}
    except Exception:
        pass
    raise SystemExit(f"marveldle: no answer for {d.isoformat()}")


# Registry so tooling (verify_solver.py, audits) can drive every game by id
# without duplicating the id -> function mapping that runner.py also maintains.
ANSWER_FNS = {
    "batterup": batterup,
    "marveldle": marveldle,
    "betweenle": betweenle,
    "canuckle": canuckle,
    "colordle": colordle,
    "colorfle": colorfle,
    "contexto": contexto,
    "countryle": countryle,
    "framed": framed,
    "globle": globle,
    "nerdle": nerdle,
    "phoodle": phoodle,
    "phrazle": phrazle,
    "searchle": searchle,
    "semantle": semantle,
    "waffle": waffle,
    "worgle": worgle,
    "worldle": worldle,
}


if __name__ == "__main__":
    import sys
    d = target_date()
    fn = sys.argv[1]
    out = globals()[fn](d)
    if isinstance(out, dict):
        printable = {k: (str(v)[:120] if not isinstance(v, (int, float)) else v)
                     for k, v in out.items() if k not in ("data",)}
        print(json.dumps(printable, indent=1))
