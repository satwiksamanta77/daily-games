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
    # ONE rule for every batch (verified against all six crons on 2026-10-09):
    # UTC+9h == IST+3:30. At the 21:00-IST batch (15:30 UTC) it rolls to the
    # NEXT calendar day - the board the video publishes for - while every
    # morning batch (02:30/05:15/06:20/10:30 UTC) stays on the same day. It is
    # also exactly nerdle_solver's old `+1 if UTC hour >= 15` rule, so runner
    # and nerdle can never disagree again. The 330 (IST-same-day) default used
    # between audits made the 9pm batch record a board that is stale by
    # publish time - do not "fix" this back to 330.
    return (datetime.utcnow() + timedelta(minutes=off)).date()


def shift_target_date(days):
    """`target_date()` moved by `days`.

    Daily puzzle sites key their puzzle off the BROWSER's local calendar day.
    BROWSER_TZ is deliberately a far-east zone (Pacific/Kiritimati, UTC+14) and
    the faked clock sits just after local midnight, so the browser very often
    reports the NEXT calendar day. An answer fetched for the unshifted date then
    describes yesterday's puzzle: every guess is scored wrong and the run can
    never win. Callers that have seen the page's own date use this to agree
    with it.
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


def waffle(d=None):
    """Waffle grid: worker API is what wordsolverx's frontend calls."""
    import requests
    d = d or target_date()
    key = d.isoformat()
    hdr = {"User-Agent": "WordSolverX Video"}
    for u in (f"{WAFFLE_WORKER}/date/{key}", f"{WAFFLE_WORKER}/today"):
        try:
            r = requests.get(u, timeout=25, headers=hdr)
            if not r.ok:
                continue
            j = r.json()
        except Exception:
            continue
        if not j or j.get("error") or not j.get("solution"):
            continue
        return {"answer": j["solution"], "puzzle": j.get("puzzle"),
                "words": j.get("words") or [], "number": j.get("number"),
                "date": j.get("date") or key, "via": u}
    raise SystemExit(f"waffle: no puzzle for {key}")


# ---------- worldle ----------
def worldle(d=None):
    """Worldle: seedrandom(worldleNumber) over countries sorted by code.

    Ported from src/lib/worldle/logic.ts. seedrandom is an ARC4 variant, so
    the index is computed by Node with the SAME package the frontend uses
    rather than a hand-rolled port that could drift by one.
    """
    d = d or target_date()
    number = _days(d, date(2022, 1, 21)) + 1
    known = {"2026-02-25": "WS", "2026-02-26": "SM", "2026-02-27": "BN",
             "2026-02-28": "BG", "2026-03-01": "GR", "2026-03-02": "MG"}
    try:  # LIVE list at answer time: a drifted vendored snapshot shifts the
        # seed index and hands us the wrong country (France vs Kyrgyzstan)
        import requests as _rq
        _r = _rq.get("https://worldle.teuteuf.fr/common/countries.json",
                     headers={"User-Agent": "WordSolverX Video"}, timeout=20)
        countries = _r.json() if _r.ok else None
        if not isinstance(countries, list) or not countries:
            raise ValueError("bad live list")
    except Exception as _e:
        print(f"[worldle] live list failed ({str(_e)[:60]}); vendored")
        countries = json.loads(
            (ZAI / "src/lib/data/worldle/countries.json").read_text("utf-8"))
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
        r = requests.get(
            "https://www.countryle.com/hidden-api/get-daily-country-valid.php",
            params={"date": f"{dd}/{mm}/{yy}"},
            headers={"User-Agent": "WordSolverX Video",
                     "accept": "application/json"}, timeout=20)
        j = r.json() if r.ok else None
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
def globle(d=None, countries=None):
    """Globle: /answer?day=... returns an AES-passphrase country index.

    The site refuses future-dated days (404), so if the target day is ahead
    of its calendar we fall back to whatever it calls today rather than
    failing the whole run.
    """
    import requests
    d = d or target_date()
    key = d.isoformat()
    countries = countries or json.loads(
        (ZAI / "src/lib/data/globle-countries.json").read_text("utf-8"))
    K = "ee53e68c3074206a002bf01333b047d5"

    def _decode(day):
        r = requests.get(f"https://globle-game.com/answer?day={day}",
                         params={"list": len(countries)},
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

    last = ""
    for day in dict.fromkeys((key, date.today().isoformat())):
        try:
            return _decode(day)
        except Exception as e:
            last = str(e)[:110]
    raise SystemExit(f"globle: no answer for {key} ({last})")


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

    for back in range(5):
        key = _shift(d.isoformat(), -back)
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
                          if isinstance(g, dict) and g.get("game_date") == key),
                         None)
            pl = {"player_name": (entry or {}).get("player_name")}
            if pl["player_name"]:
                return {"answer": pl["player_name"], "player": entry,
                        "date": key, "via": "cdn"}
        except Exception as e:
            print(f"[batterup] cdn {key} failed: {str(e)[:100]}")
            continue
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
                        "via": "live-first-try"}
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
                        "probes": probes, "date": d.isoformat(), "via": "live"}
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
                    "date": d.isoformat(), "via": "snapshot"}
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
