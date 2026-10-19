"""
YouTube Upload Module
Uploads videos to YouTube using OAuth2 credentials.
Gracefully skips if no credentials are found.
"""

import os
import json
import pickle
from pathlib import Path
from datetime import datetime

# Check if google libraries are available
try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    GOOGLE_LIBS_AVAILABLE = True
except ImportError:
    GOOGLE_LIBS_AVAILABLE = False
    print("YouTube upload libraries not installed. Skipping upload functionality.")

SCOPES = ['https://www.googleapis.com/auth/youtube.upload']

# Broader scopes enable thumbnail set + playlists when the token allows it.
# Iteration 37: youtube.force-ssl added for commentThreads.insert
# (pinned comment) — matches the Wordle repo's full-scope set. The
# stored refresh token was minted with youtube.upload (+blogger)
# only, so in practice the first publish runs will fall back to
# upload-only creds and playlist/comment steps will be skipped with
# a clear reason until the token is re-consented with full scopes.
SCOPES_FULL = [
    'https://www.googleapis.com/auth/youtube.upload',
    'https://www.googleapis.com/auth/youtube',
    'https://www.googleapis.com/auth/youtube.force-ssl',
]
SCOPES_UPLOAD_ONLY = ['https://www.googleapis.com/auth/youtube.upload']

import requests as _rq

GEMINI_PROXY_URL = "https://gemini-web-proxy.shonratt.workers.dev/v1/chat/completions"
GEMINI_PROXY_MODEL = "gemini-3.6-flash"


def _gemini_chat(prompt, timeout=30):
    try:
        r = _rq.post(GEMINI_PROXY_URL,
                     json={"model": GEMINI_PROXY_MODEL,
                           "messages": [{"role": "user", "content": prompt}]},
                     headers={"Content-Type": "application/json"}, timeout=timeout)
        r.raise_for_status()
        return (r.json().get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
    except Exception as e:
        print(f"[gemini] request failed: {e}")
        return ""


def fetch_word_meanings(words, timeout=40):
    """Return {WORD: 'one-line meaning'} for a list of words via one AI call.

    Batches all words into a single prompt for speed. Returns {} on failure.
    """
    import json as _json
    words = [w.strip().upper() for w in words if w and len(w.strip()) == 5]
    words = list(dict.fromkeys(words))  # dedupe, keep order
    if not words:
        return {}
    prompt = (
        "Return ONLY minified JSON: an object mapping each WORD to one short "
        "family-friendly definition sentence. No prose, no code fences. "
        "Words: " + ", ".join(words)
    )
    content = _gemini_chat(prompt)
    if not content:
        return {}
    t = content.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:]
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        obj = _json.loads(t[start:end + 1])
    except Exception:
        return {}
    out = {}
    for k, v in obj.items():
        ku = str(k).strip().upper()
        if len(ku) == 5:
            out[ku] = str(v).strip()
    return out


def fetch_word_analysis(words, timeout=45):
    """Rich per-word analysis like Wordle's fetch_word_info_ai.

    Returns {WORD: {part_of_speech, definition, example}} via one batched
    AI call. Falls back to the simpler fetch_word_meanings on failure.
    """
    import json as _json
    words = [w.strip().upper() for w in words if w and len(w.strip()) == 5]
    words = list(dict.fromkeys(words))
    if not words:
        return {}
    prompt = (
        "Return ONLY minified JSON: an object mapping each WORD to an object "
        "with keys part_of_speech, definition (one sentence), example (a short "
        "family-friendly example sentence using the word). No prose, no code "
        "fences. Words: " + ", ".join(words)
    )
    content = _gemini_chat(prompt, timeout=timeout)
    if not content:
        return {}
    t = content.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:]
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        obj = _json.loads(t[start:end + 1])
    except Exception:
        return {}
    out = {}
    for k, v in obj.items():
        ku = str(k).strip().upper()
        if len(ku) == 5 and isinstance(v, dict):
            out[ku] = {
                "part_of_speech": str(v.get("part_of_speech", "")).strip(),
                "definition": str(v.get("definition", "")).strip(),
                "example": str(v.get("example", "")).strip(),
            }
    return out


MODE_ORDER = ["Classic", "Chill", "Extreme", "Sequence", "Rescue", "Weekly"]


def build_quordle_description(today, official_map=None, chapters=None,
                            meanings=None, ytd_info=None, analysis=None):
    """SEO-rich description matching Wordle-level quality.

    Includes: keyword headline, real chapters, FAQ quick-answers block,
    per-mode answers + AI definitions, yesterday recap / tomorrow teaser,
    puzzle stats, and keyword-optimized hashtags.
    """
    official_map = official_map or {}
    meanings = meanings or {}
    ytd_info = ytd_info or {}
    analysis = analysis or {}
    lines = []

    # Headline (exact match keyword first)
    classic = official_map.get("Classic") or []
    classic_str = ", ".join(classic) if classic else "watch to reveal"
    lines.append("🎲 Quordle Answer Today — " + today + " | All 6 Modes Solved 🎲")
    lines.append("")
    lines.append(
        f"Today's Quordle Classic answers: {classic_str}. "
        "Watch the full solve for Classic, Chill, Extreme, Sequence, "
        "Rescue and Weekly modes — every puzzle solved with hints!"
    )
    lines.append("")
    lines.append("Quordle answer today: https://wordsolverx.com/quordle-answer-today")
    lines.append("Quordle solver: https://wordsolverx.com/quordle-solver")
    lines.append("")

    # Chapters (YouTube auto-links these when the first is 0:00)
    if chapters:
        lines.append("⏱️ CHAPTERS:")
        for sec, label in chapters:
            m, s = divmod(int(sec), 60)
            lines.append(f"{m}:{s:02d} {label}")
        lines.append("")

    # FAQ quick-answers block (captures long-tail voice queries)
    faq = ["❓ What is today's Quordle answer? ➤ " +
           (", ".join(classic) if classic else "Watch the video!")]
    if ytd_info.get("yesterday"):
        _y = ytd_info["yesterday"]
        _yc = _y.get("classic") or []
        if _yc:
            faq.append("❓ What was yesterday's Quordle answer? ➤ " +
                       ", ".join(_yc))
    lines.append("💡 QUICK ANSWERS:")
    lines.extend(faq)
    lines.append("")

    # Per-mode answers + AI definitions
    any_ans = False
    for mode in MODE_ORDER:
        words = official_map.get(mode)
        if not words:
            continue
        any_ans = True
        lines.append(f"🔓 {mode.upper()} ANSWERS: {', '.join(words)}")
        for w in words:
            wu = w.upper()
            a = analysis.get(wu)
            if a:
                pos = a.get("part_of_speech", "")
                defn = a.get("definition", "")
                ex = a.get("example", "")
                if defn:
                    pos_s = f" ({pos})" if pos else ""
                    lines.append(f"   📖 {wu}{pos_s}: {defn}")
                if ex:
                    lines.append(f'      Example: "{ex}"')
            else:
                mean = meanings.get(wu)
                if mean:
                    lines.append(f"   📖 {wu}: {mean}")
        lines.append("")

    if not any_ans:
        lines.append("Full solve for every Quordle mode. Subscribe for daily answers!")
        lines.append("")

    # Yesterday recap + tomorrow teaser
    if ytd_info.get("yesterday"):
        _y = ytd_info["yesterday"]
        _yc = _y.get("classic") or []
        if _yc:
            lines.append(f"📅 YESTERDAY'S QUORDLE: {', '.join(_yc)}")
            lines.append("")
    if ytd_info.get("tomorrow"):
        _t = ytd_info["tomorrow"]
        _tc = _t.get("classic") or []
        if _tc:
            _first = _tc[0] if _tc else "?"
            lines.append(
                f"🔮 TOMORROW'S QUORDLE: First Classic word starts "
                f"with '{_first[0].upper()}'")
            lines.append("")

    # Stats
    _mode_count = sum(1 for m in MODE_ORDER if official_map.get(m))
    lines.append("📊 PUZZLE STATS:")
    lines.append(f"   🗓️ Date: {today}")
    lines.append(f"   🎮 Modes solved: {_mode_count}/6")
    if classic:
        lines.append(f"   ✅ Classic answers: {', '.join(classic)}")
    lines.append("")

    lines.append("🔔 Subscribe for a new Quordle answer every day — never lose your streak!")
    lines.append("")
    lines.append("🧠 Try our FREE Quordle Solver:")
    lines.append("🔗 https://wordsolverx.com/quordle-solver")
    lines.append("")
    lines.append("#Quordle #QuordleAnswerToday #QuordleAnswer #Wordle #DailyPuzzle "
                 "#BrainTeaser #WordGame #Shorts #PuzzleSolver #WordChallenge "
                 "#QuordleHints #QuordleSolver")
    lines.append("")
    lines.append("quordle answer today, quordle solver, quordle answers, how to play "
                 "quordle today, quordle classic answer today, quordle chill answer today, "
                 "quordle extreme answer today, quordle sequence answer today, "
                 "quordle rescue answer today, quordle weekly answer, best starting word "
                 "for quordle, quordle hints, quordle answers today")
    lines.append("")
    lines.append("Quordle is a word game where you solve four 5-letter puzzles at once. "
                 "Subscribe for daily solutions!")
    return "\n".join(lines)

def get_credentials():
    """
    Get YouTube API credentials from:
    1. Environment variable YOUTUBE_CLIENT_SECRET (for CI)
    2. Local client_secret.json file
    Returns None if neither is available.
    """
    if not GOOGLE_LIBS_AVAILABLE:
        return None
    
    creds = None
    token_path = Path(__file__).parent / 'token.pickle'
    
    # Check for existing token
    if token_path.exists():
        with open(token_path, 'rb') as token:
            creds = pickle.load(token)
    
    # If no valid creds, try to get new ones
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"Failed to refresh credentials: {e}")
                creds = None
        
        if not creds:
            # PREFERRED (same as the Wordle repo -> SAME YouTube channel):
            # three separate secrets. Whatever channel the refresh token was
            # minted on is the channel the video lands on, so reusing the
            # Wordle repo's YOUTUBE_CLIENT_ID / _SECRET / _REFRESH_TOKEN
            # guarantees Wordle and Quordle upload to the SAME channel.
            rid = os.environ.get('YOUTUBE_CLIENT_ID', '').strip()
            rsecret = os.environ.get('YOUTUBE_CLIENT_SECRET', '').strip()
            rtok = os.environ.get('YOUTUBE_REFRESH_TOKEN', '').strip()
            if rid and rtok and rsecret and not rsecret.lstrip().startswith('{'):
                try:
                    creds = Credentials.from_authorized_user_info({
                        'client_id': rid,
                        'client_secret': rsecret,
                        'refresh_token': rtok,
                        'token_uri': 'https://oauth2.googleapis.com/token',
                        'scopes': SCOPES,
                    }, SCOPES)
                    if creds and creds.expired and creds.refresh_token:
                        creds.refresh(Request())
                    print("[youtube] Using 3-part refresh-token creds (same channel as Wordle).")
                except Exception as e:
                    print(f"[youtube] 3-part cred build failed: {e}")
                    creds = None

            # FALLBACK: single full-OAuth-JSON in YOUTUBE_CLIENT_SECRET (legacy).
            client_secret_env = os.environ.get('YOUTUBE_CLIENT_SECRET')
            if not creds and client_secret_env and client_secret_env.lstrip().startswith('{'):
                try:
                    client_config = json.loads(client_secret_env)
                    if 'token' in client_config or 'refresh_token' in client_config:
                        creds = Credentials.from_authorized_user_info(client_config, SCOPES)
                    else:
                        print("YOUTUBE_CLIENT_SECRET JSON lacks token/refresh_token.")
                        return None
                except json.JSONDecodeError:
                    print("Invalid JSON in YOUTUBE_CLIENT_SECRET")
                    return None

            if not creds:
                # Try local client_secret.json or client-secret.json
                client_secret_path = Path(__file__).parent / 'client-secret.json'
                if not client_secret_path.exists():
                    client_secret_path = Path(__file__).parent / 'client_secret.json'
                
                if client_secret_path.exists():
                    try:
                        flow = InstalledAppFlow.from_client_secrets_file(
                            str(client_secret_path), SCOPES
                        )
                        creds = flow.run_local_server(port=0)
                    except Exception as e:
                        print(f"Failed to authenticate: {e}")
                        return None
                else:
                    print("No YouTube credentials found. Skipping upload.")
                    return None
        
        # Save credentials for next run
        if creds:
            with open(token_path, 'wb') as token:
                pickle.dump(creds, token)
    
    return creds

def youtube_find_or_create_playlist(youtube, title, description=""):
    try:
        r = youtube.playlists().list(part="snippet", mine=True, maxResults=50).execute()
        for it in r.get("items", []):
            if it["snippet"]["title"].strip().lower() == title.strip().lower():
                return it["id"]
        r = youtube.playlists().insert(part="snippet,status",
            body={"snippet": {"title": title, "description": description},
                  "status": {"privacyStatus": "public"}}).execute()
        return r.get("id")
    except Exception as e:
        # Iteration 37: quota errors must propagate so the caller can
        # defer the remaining enrichment (a swallowed quota error
        # would be misreported as a plain failure / success).
        if _classify_upload_error(e) == "quota":
            raise
        print(f"[playlist] find/create failed: {e}")
        return None


def youtube_add_to_playlist(youtube, playlist_id, video_id):
    """True on success, False on failure. Quota errors propagate."""
    try:
        youtube.playlistItems().insert(part="snippet",
            body={"snippet": {"playlistId": playlist_id,
                              "resourceId": {"kind": "youtube#video", "videoId": video_id}}}).execute()
        print(f"[playlist] added {video_id}")
        return True
    except Exception as e:
        if _classify_upload_error(e) == "quota":
            raise
        print(f"[playlist] add failed: {e}")
        return False


def youtube_pin_comment(youtube, video_id, text):
    """Comment id on success, None on failure. Quota errors propagate."""
    try:
        t = youtube.commentThreads().insert(part="snippet",
            body={"snippet": {"videoId": video_id,
                              "topLevelComment": {"snippet": {"textOriginal": text}}}}).execute()
        cid = t.get("id")
        # pin not directly supported via API v3 for all; best-effort like
        return cid
    except Exception as e:
        if _classify_upload_error(e) == "quota":
            raise
        print(f"[comment] pin failed (scope?): {e}")
        return None


def youtube_upload_captions(youtube, video_id, srt_text):
    """True on success, False on failure. Quota errors propagate."""
    import tempfile
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".srt", delete=False, encoding="utf-8") as f:
            f.write(srt_text)
            p = f.name
        youtube.captions().insert(part="snippet",
            body={"snippet": {"videoId": video_id, "language": "en", "name": "English"}},
            media_body=MediaFileUpload(p)).execute()
        print("[captions] uploaded")
        return True
    except Exception as e:
        if _classify_upload_error(e) == "quota":
            raise
        print(f"[captions] failed (scope?): {e}")
        return False


def upload_to_youtube(video_path: str, title: str = None, description: str = None,
                      official_map=None, chapters=None, thumbnail_path: str = None,
                      ytd_info=None, tags=None):
    """
    Upload a video to YouTube.
    Returns video ID if successful, None otherwise.
    """
    creds = get_credentials()
    if not creds:
        print("Skipping YouTube upload - no credentials available.")
        return None
    
    try:
        youtube = build('youtube', 'v3', credentials=creds)
        
        # Generate title and description if not provided
        today = datetime.now().strftime("%B %d, %Y")
        if not title:
            title = f"Quordle Answer Today ({today}) - All 6 Modes Solved!"
        
        # SEO description: per-mode answers + AI word meanings + chapters.
        if not description:
            meanings = {}
            analysis = {}
            if official_map:
                all_words = []
                for _mwords in official_map.values():
                    all_words.extend(_mwords or [])
                try:
                    analysis = fetch_word_analysis(all_words)
                    print(f"[wordinfo] fetched {len(analysis)} rich analyses via gemini-proxy")
                except Exception as _e:
                    print(f"[wordinfo] analysis fetch failed: {_e}")
                # Fallback: simple one-liners for anything the rich call missed
                _missing = [w for w in all_words
                            if w and len(w) == 5 and w.upper() not in analysis]
                if _missing:
                    try:
                        meanings = fetch_word_meanings(_missing)
                        print(f"[wordinfo] fallback meanings for {len(meanings)} words")
                    except Exception as _e:
                        print(f"[wordinfo] fallback meanings failed: {_e}")
            description = build_quordle_description(
                today, official_map, chapters, meanings, ytd_info, analysis)
        
        try:
            import quordle_parity as _QP
            _ds = today.split(",")[0]
            _dn = today.replace(",", "").split()[:2]
            _date_num = "/".join(_dn) if len(_dn) == 2 else _ds
            _tags = _QP.build_optimized_tags(_ds, _date_num)
        except Exception:
            _tags = [
                'Quordle', 'Quordle Answer Today', 'Quordle Answer',
                'Wordle', 'Daily Puzzle', 'Word Game',
                'Brain Teaser', 'Puzzle Solution', 'Shorts',
                'Daily Quordle', "Today's Quordle", 'Quordle Hints',
                'Quordle Solver', 'Quordle Classic', 'Quordle Chill',
                'Quordle Extreme', 'Quordle Sequence', 'Quordle Rescue',
            ]
        # YouTube snippet.description max is 5000 chars (400 invalidDescription)
        if len(description) > 5000:
            _orig = len(description)
            _cut = description[:5000].rsplit("\n", 1)[0]
            description = _cut if _cut else description[:5000]
            print(f"[desc] capped {_orig} -> {len(description)} chars")
        body = {
            'snippet': {
                'title': title,
                'description': description,
                'tags': tags or _tags,
                'categoryId': '20'  # Gaming
            },
            'status': {
                'privacyStatus': 'public',
                'selfDeclaredMadeForKids': False,
                'embeddable': True,
                'license': 'youtube'
            }
        }
        
        media = MediaFileUpload(
            video_path,
            mimetype='video/mp4',
            resumable=True
        )
        
        print(f"Uploading video: {title}")
        request = youtube.videos().insert(
            part=','.join(body.keys()),
            body=body,
            media_body=media
        )
        
        response = request.execute()
        video_id = response.get('id')
        print(f"Upload successful! Video ID: {video_id}")
        print(f"URL: https://www.youtube.com/watch?v={video_id}")
        # Best-effort custom thumbnail (needs youtube scope; ignore on failure)
        if thumbnail_path and video_id:
            try:
                import os as _os
                if _os.path.exists(thumbnail_path):
                    youtube.thumbnails().set(
                        videoId=video_id,
                        media_body=MediaFileUpload(thumbnail_path)
                    ).execute()
                    print(f"[thumbnail] set custom thumbnail: {thumbnail_path}")
            except Exception as _te:
                print(f"[thumbnail] set failed (scope?): {_te}")
        # Wordle-parity enrichment: playlist + pin + captions (best-effort)
        try:
            _pl = youtube_find_or_create_playlist(youtube, "Quordle Answer Today - Daily", "Daily Quordle answers - all modes solved")
            if _pl:
                youtube_add_to_playlist(youtube, _pl, video_id)
        except Exception:
            pass
        try:
            _classic = ((official_map or {}).get("Classic") or [])
            _pin = "Today's Classic: " + (", ".join(_classic) if _classic else "watch to reveal") + " | Solver: https://wordsolverx.com/quordle-solver"
            youtube_pin_comment(youtube, video_id, _pin)
        except Exception:
            pass
        try:
            if chapters:
                import quordle_parity as _QP2
                _srt = _QP2.build_captions_srt(chapters, chapters[-1][0] + 30 if chapters else 300, today)
                if _srt:
                    youtube_upload_captions(youtube, video_id, _srt)
        except Exception:
            pass
        return video_id
        
    except Exception as e:
        print(f"YouTube upload failed: {e}")
        return None


if __name__ == "__main__":
    # Test upload
    import sys
    if len(sys.argv) > 1:
        upload_to_youtube(sys.argv[1])
    else:
        print("Usage: python youtube_upload.py <video_path>")


# ============================================================================
# ITERATION 37 — per-game YouTube publish (YouTube parts only, ported
# from the Wordle repo's YouTube pattern). upload_to_youtube above
# (Quordle path) is untouched; this is the generic per-game path.
#
# Quota model (CORRECTED 2026-10-19 — the old 1600-units-per-upload
# model is stale): since 1 June 2026 Google uses GRANULAR buckets per
# Cloud project — videos.insert 100 calls/day (1 unit each),
# search.list 100 calls/day, everything else 10,000 general
# units/day shared. ONE project fits 18 videos/day (18/100 upload
# calls). Splitting projects to multiply quota violates the YouTube
# API Terms of Service — never do it. The GENERAL bucket is the
# tight one: full enrichment is ~550 units/video (thumbnail 50 +
# playlist add 50 + comment 50 + captions 400); x18 = ~9,900/10,000.
# Enrichment is therefore ORDERED upload-first, captions-last: if
# the general bucket runs out, the video stays published and only
# the remaining enrichment is recorded as deferred (retried next
# day), never the video. Costs below are ESTIMATES logged per job;
# the Cloud Console Quotas page is the definitive source.
# search.list is NEVER used (100/day bucket, unnecessary — no streak
# counter via search).
# ============================================================================

# General-bucket cost estimates (videos.insert is its own 100/day
# bucket and is NOT counted in the general total).
QUOTA_COSTS = {
    "videos.insert": 1,          # upload bucket (100/day), not general
    "thumbnails.set": 50,
    "playlists.list": 1,
    "playlists.insert": 50,
    "playlistItems.insert": 50,
    "commentThreads.insert": 50,
    "captions.insert": 400,
}

UPLOAD_MAX_ATTEMPTS = 3
UPLOAD_BACKOFF_BASE_S = 2.0


def _error_reason(exc):
    """Best-effort YouTube API error reason (e.g. 'quotaExceeded')."""
    try:
        import json as _json
        content = getattr(exc, "content", None)
        if content:
            if isinstance(content, bytes):
                content = content.decode(errors="replace")
            obj = _json.loads(content)
            errs = ((obj.get("error") or {}).get("errors") or [{}])
            return str(errs[0].get("reason") or "")
    except Exception:
        pass
    return ""


def _error_status(exc):
    try:
        resp = getattr(exc, "resp", None)
        if resp is not None and getattr(resp, "status", None):
            return int(resp.status)
    except Exception:
        pass
    return 0


def _classify_upload_error(exc):
    """'quota' | 'transient' | 'fatal'.

    Quota errors fail fast (retrying won't help until quota resets).
    Transient errors (5xx / 429 / rateLimitExceeded / network) retry
    with backoff. Everything else is fatal (bad request, auth, ...).
    """
    reason = _error_reason(exc)
    status = _error_status(exc)
    if reason in ("quotaExceeded", "dailyLimitExceeded",
                  "uploadLimitExceeded"):
        return "quota"
    if status in (429, 500, 502, 503, 504) or reason in (
            "rateLimitExceeded", "backendError", "internalError"):
        return "transient"
    txt = str(exc)
    if "quotaExceeded" in txt or "uploadLimitExceeded" in txt:
        return "quota"
    if "Connection" in txt or "Timeout" in txt or "timed out" in txt:
        return "transient"
    return "fatal"


def _build_youtube_credentials(scopes):
    """Fresh 3-part creds (same channel as the Wordle repo) for `scopes`.

    No token.pickle cache: the cached pickle may hold creds minted
    for narrower scopes, which would silently cap enrichment. Raises
    on failure (caller falls back to narrower scopes).
    """
    if not GOOGLE_LIBS_AVAILABLE:
        return None
    rid = os.environ.get('YOUTUBE_CLIENT_ID', '').strip()
    rsecret = os.environ.get('YOUTUBE_CLIENT_SECRET', '').strip()
    rtok = os.environ.get('YOUTUBE_REFRESH_TOKEN', '').strip()
    if not (rid and rtok and rsecret):
        return None
    creds = Credentials.from_authorized_user_info({
        'client_id': rid,
        'client_secret': rsecret,
        'refresh_token': rtok,
        'token_uri': 'https://oauth2.googleapis.com/token',
        'scopes': list(scopes),
    }, list(scopes))
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return creds


def get_credentials_for_scopes():
    """(creds, enrichment_enabled).

    Tries full scopes first (upload + youtube + force-ssl: playlists,
    pinned comment, captions). On invalid_scope (the stored refresh
    token was minted with youtube.upload only) falls back to
    upload-only creds with enrichment disabled for the scope-gated
    steps. Returns (None, False) when no 3-part creds exist.
    """
    try:
        creds = _build_youtube_credentials(SCOPES_FULL)
        if creds:
            print("[youtube] Authenticated with full scopes "
                  "(upload + playlists + comments).")
            return creds, True
    except Exception as e:
        print(f"[youtube] Full-scope auth failed ({str(e)[:120]}); "
              f"falling back to upload-only scope.")
    try:
        creds = _build_youtube_credentials(SCOPES_UPLOAD_ONLY)
        if creds:
            print("[youtube] Authenticated with upload-only scope "
                  "(playlists/pinned-comment disabled; thumbnail + "
                  "captions still attempted).")
            return creds, False
    except Exception as e:
        print(f"[youtube] Upload-only auth failed: {str(e)[:120]}")
    return None, False


def _upload_insert_with_retry(youtube, video_path, body):
    """videos().insert with retry on transient errors, fail-fast on quota.

    Returns (response, attempts). Raises _QuotaError on quota errors,
    otherwise the last exception.
    """
    import time as _time
    last = None
    for attempt in range(1, UPLOAD_MAX_ATTEMPTS + 1):
        try:
            media = MediaFileUpload(video_path, mimetype='video/mp4',
                                    resumable=True)
            req = youtube.videos().insert(
                part=','.join(body.keys()), body=body, media_body=media)
            return req.execute(), attempt
        except Exception as e:
            last = e
            # Iteration 37: attach the real attempt count so the
            # caller reports attempts honestly (quota fail-fast = 1,
            # not UPLOAD_MAX_ATTEMPTS).
            try:
                e.attempts = attempt
            except Exception:
                pass
            kind = _classify_upload_error(e)
            if kind == "quota":
                print(f"[upload] quota error on attempt {attempt} "
                      f"({_error_reason(e) or e}); NOT retrying.")
                raise
            if kind != "transient" or attempt >= UPLOAD_MAX_ATTEMPTS:
                print(f"[upload] {kind} error on attempt {attempt}: "
                      f"{str(e)[:200]}")
                raise
            _wait = UPLOAD_BACKOFF_BASE_S * (2 ** (attempt - 1))
            print(f"[upload] transient error on attempt {attempt}, "
                  f"retrying in {_wait:.0f}s: {str(e)[:120]}")
            _time.sleep(_wait)
    raise last


def _enrich_video(youtube, video_id, enrichment_enabled, thumbnail_path=None,
                  playlist_titles=None, comment_text=None, chapters=None,
                  duration_s=None, game=None, date_str=""):
    """Post-upload enrichment in quota order: thumbnail, playlists,
    pinned comment, captions LAST.

    Returns (enrichment dict, general_units_estimated). Each step is
    best-effort and records done/deferred/skipped + reason. Quota
    exhaustion marks the current + remaining steps deferred and
    stops enrichment — the video stays published. Scope-gated steps
    (playlists, comment) are skipped — not attempted — when creds
    are upload-only.
    """
    import tempfile as _tf
    enrichment = {}
    general_units = 0

    def _mark(step, status, reason="", cost=0):
        enrichment[step] = {"status": status, "reason": str(reason)[:200],
                            "cost": int(cost)}
        return enrichment[step]

    # 1. Thumbnail (upload scope suffices).
    if thumbnail_path and os.path.exists(thumbnail_path):
        try:
            youtube.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(thumbnail_path)).execute()
            _mark("thumbnail", "done", "", QUOTA_COSTS["thumbnails.set"])
            general_units += QUOTA_COSTS["thumbnails.set"]
            print("[thumbnail] set custom thumbnail")
        except Exception as e:
            _mark("thumbnail", "deferred",
                  f"{_error_reason(e) or e}", QUOTA_COSTS["thumbnails.set"])
            print(f"[thumbnail] set failed: {str(e)[:160]}")
    else:
        _mark("thumbnail", "skipped", "no thumbnail file")

    # 2. Playlists (need full scopes).
    _scope_reason = ("upload-only scopes (token granted youtube.upload "
                     "only; playlists need youtube scope — re-consent "
                     "with full scopes to enable)")
    if not enrichment_enabled:
        _mark("playlist", "skipped", _scope_reason)
    elif playlist_titles:
        try:
            _added = 0
            _perrs = []
            for _title, _desc in playlist_titles:
                _pid = youtube_find_or_create_playlist(
                    youtube, _title, _desc)
                general_units += QUOTA_COSTS["playlists.list"]
                if _pid:
                    if youtube_add_to_playlist(youtube, _pid, video_id):
                        general_units += QUOTA_COSTS["playlistItems.insert"]
                        _added += 1
                    else:
                        _perrs.append(f"add failed for {_title}")
                else:
                    _perrs.append(f"find/create failed for {_title}")
            if _added:
                _mark("playlist", "done",
                      "; ".join(_perrs) if _perrs else "",
                      general_units)
                print("[playlist] added to per-game playlists")
            else:
                # A failed step is DEFERRED, never done: it must be
                # retried next day, not silently dropped.
                _mark("playlist", "deferred",
                      "; ".join(_perrs) or "no playlist add succeeded")
                print(f"[playlist] failed: {'; '.join(_perrs)}")
        except Exception as e:
            if _classify_upload_error(e) == "quota":
                _mark("playlist", "deferred",
                      f"quota: {_error_reason(e) or e}")
                _mark("comment", "deferred",
                      "quota — bucket exhausted, deferred")
                _mark("captions", "deferred",
                      "quota — bucket exhausted, deferred")
                print("[enrichment] quota exhausted at playlist step; "
                      "comment + captions deferred, video stays published.")
                return enrichment, general_units
            _mark("playlist", "deferred", f"{_error_reason(e) or e}")
            print(f"[playlist] failed: {str(e)[:160]}")
    else:
        _mark("playlist", "skipped", "no playlist titles")

    # 3. Pinned comment (needs youtube.force-ssl scope).
    if not enrichment_enabled:
        if "comment" not in enrichment:
            _mark("comment", "skipped", _scope_reason)
    elif comment_text:
        try:
            _cid = youtube_pin_comment(youtube, video_id, comment_text)
            if _cid:
                _mark("comment", "done", "",
                      QUOTA_COSTS["commentThreads.insert"])
                general_units += QUOTA_COSTS["commentThreads.insert"]
                print("[comment] pinned comment posted")
            else:
                # Failed pin is DEFERRED, never done.
                _mark("comment", "deferred", "pin comment returned no id")
                print("[comment] pin failed: no id returned")
        except Exception as e:
            if _classify_upload_error(e) == "quota":
                _mark("comment", "deferred",
                      f"quota: {_error_reason(e) or e}")
                _mark("captions", "deferred",
                      "quota — bucket exhausted, deferred")
                print("[enrichment] quota exhausted at comment step; "
                      "captions deferred, video stays published.")
                return enrichment, general_units
            _mark("comment", "deferred", f"{_error_reason(e) or e}")
            print(f"[comment] failed: {str(e)[:160]}")
    else:
        if "comment" not in enrichment:
            _mark("comment", "skipped", "no comment text")

    # 4. Captions LAST (400 units — the expensive step).
    if chapters:
        try:
            import seo as _seo
            _srt = _seo.build_captions_srt(
                chapters, duration_s or (chapters[-1][0] + 30),
                date_str, game or "")
            if _srt:
                if youtube_upload_captions(youtube, video_id, _srt):
                    _mark("captions", "done", "",
                          QUOTA_COSTS["captions.insert"])
                    general_units += QUOTA_COSTS["captions.insert"]
                    print("[captions] uploaded")
                else:
                    # Failed captions are DEFERRED, never done.
                    _mark("captions", "deferred",
                          "captions upload returned failure")
                    print("[captions] upload failed; deferred")
            else:
                _mark("captions", "skipped", "empty SRT")
        except Exception as e:
            if _classify_upload_error(e) == "quota":
                _mark("captions", "deferred",
                      f"quota: {_error_reason(e) or e}")
                print("[enrichment] quota exhausted at captions step; "
                      "video stays published.")
            else:
                _mark("captions", "deferred", f"{_error_reason(e) or e}")
                print(f"[captions] failed: {str(e)[:160]}")
    else:
        _mark("captions", "skipped", "no chapters")

    return enrichment, general_units


def _make_shorts_clip(src_video_path, out_path, max_duration=45):
    """First <=45s of the video, centre-cropped/padded to 9:16 (1080x1920).

    MoviePy only; returns True on success. Never raises.
    """
    try:
        from moviepy.editor import VideoFileClip, CompositeVideoClip, ColorClip
        clip = VideoFileClip(src_video_path)
        duration = min(max_duration, clip.duration)
        short = clip.subclip(0, duration)
        target_w, target_h = 1080, 1920
        scale = target_w / short.w
        new_h = int(short.h * scale)
        short = short.resize((target_w, new_h))
        if new_h > target_h:
            y1 = max(0, (new_h - target_h) // 2)
            short = short.crop(y1=y1, y2=y1 + target_h)
        elif new_h < target_h:
            pad_top = (target_h - new_h) // 2
            pad_bot = target_h - new_h - pad_top
            top = ColorClip((target_w, pad_top),
                            color=(0, 0, 0)).set_duration(short.duration)
            bot = ColorClip((target_w, pad_bot),
                            color=(0, 0, 0)).set_duration(short.duration)
            short = CompositeVideoClip(
                [short.set_position(('center', pad_top)),
                 top.set_position(('center', 0)),
                 bot.set_position(('center', target_h - pad_bot))],
                size=(target_w, target_h))
        short.write_videofile(out_path, codec='libx264', audio_codec='aac',
                              fps=24, verbose=False, logger=None)
        clip.close()
        short.close()
        print(f"[short] Saved Shorts clip to {out_path}")
        return True
    except Exception as e:
        print(f"[short] Failed: {e}")
        return False


def upload_game_video(video_path, title, description, tags=None,
                      chapters=None, thumbnail_path=None, game=None,
                      date_str="", playlist_titles=None, comment_text=None,
                      duration_s=None, analysis_words=None, seo_context=None,
                      enrich=True, shorts=False, full_video_url=None):
    """Upload one per-game video to YouTube. Returns a publish dict.

    published=True ONLY after the API returns a video ID. On quota
    errors during the insert, published stays False (fail fast, no
    retry — retrying won't help until quota resets). Enrichment
    failures never unpublish the video; deferred steps are recorded
    for next-day retry. Never raises for upload/enrichment failures
    (returns the publish dict with last_publish_error set); raises
    only on programmer errors (missing file, bad args).
    """
    from datetime import datetime as _dt, timezone as _tz
    pub = {"published": False, "youtube_video_id": None, "youtube_url": None,
           "published_at": None, "publish_attempts": 0,
           "last_publish_error": None, "enrichment": {},
           "enrichment_deferred": [], "quota_estimate": {},
           "shorts_video_id": None}
    if not os.path.exists(video_path):
        pub["last_publish_error"] = f"video file missing: {video_path}"
        return pub
    if not GOOGLE_LIBS_AVAILABLE:
        pub["last_publish_error"] = "google API libs not installed"
        return pub

    creds, enrichment_enabled = get_credentials_for_scopes()
    if not creds:
        pub["last_publish_error"] = "no YouTube credentials"
        print("[youtube] Skipping upload — no credentials available.")
        return pub

    # Analysis rebuild: if the caller supplied words + the kwargs to
    # rebuild the description, fetch the analysis (best-effort) and
    # rebuild the description with it. The pre-analysis description
    # (seo.json artifact) stays as built by the caller.
    if analysis_words and seo_context:
        try:
            import seo as _seo
            _analysis = fetch_word_analysis(list(analysis_words))
            if _analysis:
                _first = next(iter(_analysis.values()))
                _w = next(iter(_analysis.keys()))
                _ctx = dict(seo_context)
                _ctx["analysis"] = {"word": _w,
                                    "part_of_speech": _first.get("part_of_speech", ""),
                                    "definition": _first.get("definition", ""),
                                    "example": _first.get("example", "")}
                description = _seo.build_description(**_ctx)
                print(f"[wordinfo] description rebuilt with analysis for {_w}")
        except Exception as e:
            print(f"[wordinfo] analysis rebuild skipped: {str(e)[:120]}")

    if len(description) > 5000:
        _orig = len(description)
        _cut = description[:5000].rsplit("\n", 1)[0]
        description = _cut if _cut else description[:5000]
        print(f"[desc] capped {_orig} -> {len(description)} chars")
    if len(title) > 100:
        title = title[:100]
        print("[seo] title capped to 100 chars")
    tags = list(tags or [])
    while tags and sum(len(t) for t in tags) + max(0, len(tags) - 1) > 500:
        tags.pop()

    youtube = build('youtube', 'v3', credentials=creds)
    body = {
        'snippet': {'title': title, 'description': description,
                    'tags': tags, 'categoryId': '20'},
        'status': {'privacyStatus': 'public',
                   'selfDeclaredMadeForKids': False,
                   'embeddable': True, 'license': 'youtube'},
    }
    try:
        response, attempts = _upload_insert_with_retry(
            youtube, video_path, body)
        pub["publish_attempts"] = attempts
    except Exception as e:
        pub["publish_attempts"] = int(getattr(e, "attempts",
                                              UPLOAD_MAX_ATTEMPTS))
        pub["last_publish_error"] = (
            f"{_error_reason(e) or type(e).__name__}: {str(e)[:300]}")
        print(f"[youtube] Upload failed: {pub['last_publish_error']}")
        pub["quota_estimate"] = {"upload_calls": 1,
                                 "general_units_estimated": 0}
        return pub

    video_id = (response or {}).get('id')
    if not video_id:
        pub["last_publish_error"] = "upload API returned no video id"
        pub["quota_estimate"] = {"upload_calls": 1,
                                 "general_units_estimated": 0}
        return pub
    pub["published"] = True
    pub["youtube_video_id"] = video_id
    pub["youtube_url"] = f"https://www.youtube.com/watch?v={video_id}"
    pub["published_at"] = _dt.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[youtube] Upload successful! Video ID: {video_id}")
    print(f"[youtube] URL: {pub['youtube_url']}")

    general_units = 0
    if enrich:
        enrichment, general_units = _enrich_video(
            youtube, video_id, enrichment_enabled,
            thumbnail_path=thumbnail_path, playlist_titles=playlist_titles,
            comment_text=comment_text, chapters=chapters,
            duration_s=duration_s, game=game, date_str=date_str)
        pub["enrichment"] = enrichment
        pub["enrichment_deferred"] = [
            k for k, v in enrichment.items()
            if v.get("status") == "deferred"]
        _ran = [k for k, v in enrichment.items()
                if v.get("status") == "done"]
        _skipped = [k for k, v in enrichment.items()
                    if v.get("status") != "done"]
        print(f"[enrichment] ran={_ran} skipped/deferred={_skipped} "
              f"general_units_estimated={general_units}")
    pub["quota_estimate"] = {"upload_calls": 1,
                             "general_units_estimated": general_units}

    # Shorts (default OFF — env YOUTUBE_SHORTS). Each Short costs 1
    # upload-bucket call (36/100 total if enabled for all 18 games).
    if shorts:
        try:
            import tempfile as _tf2
            _short_path = os.path.join(
                _tf2.gettempdir(), f"shorts_{video_id}.mp4")
            if _make_shorts_clip(video_path, _short_path):
                _short_body = {
                    'snippet': {
                        'title': f"{game or ''} Answer Today \u26a1".strip(),
                        'description': (
                            f"#{ (game or '').replace(' ', '')} #Shorts "
                            f"#{(game or '').replace(' ', '')}AnswerToday\n\n"
                            f"Full video: {pub['youtube_url']}\n"),
                        'tags': [f"{(game or '').lower()} answer today",
                                 game or '', 'Shorts'],
                        'categoryId': '20'},
                    'status': {'privacyStatus': 'unlisted'},
                }
                _resp = youtube.videos().insert(
                    part='snippet,status', body=_short_body,
                    media_body=MediaFileUpload(
                        _short_path, mimetype='video/mp4',
                        resumable=True)).execute()
                pub["shorts_video_id"] = _resp.get("id")
                print(f"[short] Shorts uploaded: "
                      f"https://youtu.be/{pub['shorts_video_id']} (unlisted)")
                try:
                    os.remove(_short_path)
                except Exception:
                    pass
                pub["quota_estimate"]["upload_calls"] = 2
        except Exception as e:
            print(f"[short] Workflow failed: {str(e)[:160]}")
    return pub


def retry_deferred_enrichment(video_id, deferred_steps, context):
    """Retry deferred enrichment steps for an already-published video.

    context: {chapters, comment_text, playlist_titles, thumbnail_path,
    duration_s, game, date_str}. Best-effort; returns (enrichment
    dict, general_units). Used by the runner for the previous day's
    record when its enrichment deferred (quota/scope). Never raises.
    """
    _out = {}
    if not deferred_steps or not video_id or not GOOGLE_LIBS_AVAILABLE:
        return _out, 0
    try:
        creds, enrichment_enabled = get_credentials_for_scopes()
        if not creds:
            return _out, 0
        youtube = build('youtube', 'v3', credentials=creds)
        _ctx = dict(context or {})
        # Re-run only the deferred steps, in quota order.
        if "thumbnail" in deferred_steps:
            _tp = _ctx.get("thumbnail_path")
            if _tp and os.path.exists(_tp):
                try:
                    youtube.thumbnails().set(
                        videoId=video_id,
                        media_body=MediaFileUpload(_tp)).execute()
                    _out["thumbnail"] = {"status": "done", "reason": "",
                                         "cost": QUOTA_COSTS["thumbnails.set"]}
                except Exception as e:
                    _out["thumbnail"] = {"status": "deferred",
                                         "reason": str(e)[:200], "cost": 0}
            else:
                _out["thumbnail"] = {"status": "skipped",
                                     "reason": "thumbnail file gone",
                                     "cost": 0}
        if "playlist" in deferred_steps:
            if not enrichment_enabled:
                _out["playlist"] = {"status": "deferred",
                                    "reason": "still upload-only scopes",
                                    "cost": 0}
            elif _ctx.get("playlist_titles"):
                try:
                    _added = 0
                    for _t, _d in _ctx["playlist_titles"]:
                        _pid = youtube_find_or_create_playlist(
                            youtube, _t, _d)
                        if _pid and youtube_add_to_playlist(
                                youtube, _pid, video_id):
                            _added += 1
                    _out["playlist"] = {
                        "status": "done" if _added else "deferred",
                        "reason": "" if _added else "no playlist add "
                        "succeeded", "cost": 0}
                except Exception as e:
                    _out["playlist"] = {"status": "deferred",
                                        "reason": str(e)[:200], "cost": 0}
        if "comment" in deferred_steps:
            if not enrichment_enabled:
                _out["comment"] = {"status": "deferred",
                                   "reason": "still upload-only scopes",
                                   "cost": 0}
            elif _ctx.get("comment_text"):
                try:
                    _cid = youtube_pin_comment(
                        youtube, video_id, _ctx["comment_text"])
                    _out["comment"] = {
                        "status": "done" if _cid else "deferred",
                        "reason": "" if _cid else "pin comment returned "
                        "no id", "cost": 0}
                except Exception as e:
                    _out["comment"] = {"status": "deferred",
                                       "reason": str(e)[:200], "cost": 0}
        if "captions" in deferred_steps and _ctx.get("chapters"):
            try:
                import seo as _seo
                _srt = _seo.build_captions_srt(
                    _ctx["chapters"],
                    _ctx.get("duration_s") or (_ctx["chapters"][-1][0] + 30),
                    _ctx.get("date_str", ""), _ctx.get("game", ""))
                if _srt:
                    _ok = youtube_upload_captions(youtube, video_id, _srt)
                    _out["captions"] = {
                        "status": "done" if _ok else "deferred",
                        "reason": "" if _ok else "captions upload "
                        "returned failure", "cost": 0}
            except Exception as e:
                _out["captions"] = {"status": "deferred",
                                    "reason": str(e)[:200], "cost": 0}
        _units = sum((v or {}).get("cost", 0) for v in _out.values())
        return _out, _units
    except Exception as e:
        print(f"[enrichment] retry failed: {str(e)[:160]}")
        return _out, 0
