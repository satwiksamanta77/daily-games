#!/usr/bin/env python3
"""Admin cleanup: delete user-approved stale duplicate YouTube videos.

Context (2026-10-20): force=true recovery dispatches, run before the
publish path became idempotent (iter-48, f8ea519), re-uploaded duplicate
videos for games that were already published. database/<date>/<game>/
result.json now points at the newest copy per game; the user approved
deleting the OLD copies. This script performs exactly that deletion,
inside GitHub Actions where the YouTube secrets live.

SAFETY GUARD (non-bypassable):
- Before anything is deleted, a keep-set is built from EVERY
  youtube_video_id found in database/**/result.json of the checked-out
  repo (ALL dates). Any candidate ID present in the keep-set is REFUSED
  (reported, never deleted) no matter what the caller passed.
- Only IDs passed explicitly via --video-ids are ever candidates.
- On a quota error the run STOPS immediately; the ID that hit quota
  plus every not-yet-attempted ID are reported as pending-quota so a
  later run (after the quota reset) can finish them.
- On a permission/scope error the run also stops: retrying with the
  same credentials cannot succeed, and the table says why.

Credentials come from the repo's own youtube_upload module (env-based
3-part OAuth). No credential value is ever printed.
"""

import argparse
import json
import sys
from pathlib import Path

LIST_CHUNK = 50  # YouTube videos.list accepts at most 50 ids per call.

OUT_DELETED = "deleted"
OUT_ABSENT = "already-absent"
OUT_REFUSED = "refused-keep-set"
OUT_PENDING = "pending-quota"
OUT_ERROR = "error"

_PERMISSION_REASONS = ("insufficientPermissions", "forbidden",
                       "unauthorized", "authError")


# ---------------------------------------------------------------- keep-set

def build_keep_set(db_root):
    """Every youtube_video_id in any database/**/result.json (all dates)."""
    keep = set()
    for path in sorted(Path(db_root).rglob("result.json")):
        try:
            rec = json.loads(path.read_text())
        except Exception:
            continue
        if isinstance(rec, dict):
            vid = rec.get("youtube_video_id")
            if vid:
                keep.add(str(vid).strip())
    return keep


def parse_candidates(raw):
    """Comma-separated CLI input -> ordered, deduped, non-empty ID list."""
    out, seen = [], set()
    for tok in (raw or "").replace(";", ",").split(","):
        tok = tok.strip()
        if tok and tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def partition_candidates(candidates, keep_set):
    """(refused, todo): refused = candidates that are in the keep-set."""
    refused = [v for v in candidates if v in keep_set]
    todo = [v for v in candidates if v not in keep_set]
    return refused, todo


# ------------------------------------------------------- service wrappers
# Thin wrappers around the googleapiclient resource so tests can pass a
# fake service object with the same .videos().list()/.delete() shape.

def list_video_titles(youtube, ids):
    """{id: title} for the ids that exist (one videos.list call)."""
    resp = youtube.videos().list(part="snippet",
                                  id=",".join(ids)).execute()
    found = {}
    for item in resp.get("items", []):
        found[item["id"]] = (item.get("snippet") or {}).get("title", "")
    return found


def delete_video(youtube, video_id):
    youtube.videos().delete(id=video_id).execute()


# ------------------------------------------------------------------- core

def _is_permission_error(exc, classify, reason_of):
    if classify(exc) == "fatal":
        reason = reason_of(exc)
        if reason in _PERMISSION_REASONS:
            return True
        status = getattr(getattr(exc, "resp", None), "status", None)
        if status in (401, 403):
            return True
        if "insufficient" in str(exc).lower():
            return True
    return False


def run_cleanup(youtube, candidates, keep_set, classify, reason_of):
    """Delete every non-keeper candidate; return a list of outcome rows.

    Each row: {"video_id", "outcome", "detail"}. Outcomes: deleted,
    already-absent, refused-keep-set, pending-quota, error.
    Stops early on quota (rest -> pending-quota) and on permission
    errors (rest -> error/aborted).
    """
    rows = []
    refused, todo = partition_candidates(candidates, keep_set)
    for vid in refused:
        rows.append({"video_id": vid, "outcome": OUT_REFUSED,
                     "detail": "present in a current DB record; "
                               "deletion refused by keep-set guard"})

    def mark_pending(ids, why):
        for vid in ids:
            rows.append({"video_id": vid, "outcome": OUT_PENDING,
                         "detail": why})

    def mark_aborted(ids, why):
        for vid in ids:
            rows.append({"video_id": vid, "outcome": OUT_ERROR,
                         "detail": why})

    chunks = [todo[i:i + LIST_CHUNK] for i in range(0, len(todo), LIST_CHUNK)]
    for ci, chunk in enumerate(chunks):
        rest_after_chunk = [v for later in chunks[ci + 1:] for v in later]
        try:
            titles = list_video_titles(youtube, chunk)
        except Exception as exc:  # list failed: classify and stop/skip
            if classify(exc) == "quota":
                mark_pending(chunk + rest_after_chunk,
                             f"quota error on videos.list: "
                             f"{reason_of(exc) or exc}")
                return rows
            if _is_permission_error(exc, classify, reason_of):
                mark_aborted(chunk + rest_after_chunk,
                             f"permission error on videos.list: "
                             f"{reason_of(exc) or exc}")
                return rows
            for vid in chunk:
                rows.append({"video_id": vid, "outcome": OUT_ERROR,
                             "detail": f"videos.list failed: "
                                       f"{reason_of(exc) or exc}"})
            continue
        for idx, vid in enumerate(chunk):
            remaining = chunk[idx + 1:] + rest_after_chunk
            if vid not in titles:
                rows.append({"video_id": vid, "outcome": OUT_ABSENT,
                             "detail": "videos.list returned no item"})
                continue
            try:
                delete_video(youtube, vid)
                rows.append({"video_id": vid, "outcome": OUT_DELETED,
                             "detail": f"title: {titles[vid]}"})
            except Exception as exc:
                if classify(exc) == "quota":
                    rows.append({"video_id": vid, "outcome": OUT_PENDING,
                                 "detail": f"quota error on delete: "
                                           f"{reason_of(exc) or exc}"})
                    mark_pending(remaining,
                                 "not attempted (stopped on quota)")
                    return rows
                if _is_permission_error(exc, classify, reason_of):
                    rows.append({"video_id": vid, "outcome": OUT_ERROR,
                                 "detail": f"permission error on delete: "
                                           f"{reason_of(exc) or exc}"})
                    mark_aborted(remaining,
                                 "aborted after permission error")
                    return rows
                rows.append({"video_id": vid, "outcome": OUT_ERROR,
                             "detail": f"delete failed: "
                                       f"{reason_of(exc) or exc}"})
    return rows


def summarize(rows):
    out = {"deleted": [], "already_absent": [], "refused_keep_set": [],
           "pending_quota": [], "errors": {}}
    key = {OUT_DELETED: "deleted", OUT_ABSENT: "already_absent",
           OUT_REFUSED: "refused_keep_set", OUT_PENDING: "pending_quota"}
    for row in rows:
        if row["outcome"] == OUT_ERROR:
            out["errors"][row["video_id"]] = row["detail"]
        else:
            out[key[row["outcome"]]].append(row["video_id"])
    return out


def print_report(rows):
    print("=" * 78)
    print(f"{'video_id':<16} {'outcome':<18} detail")
    print("-" * 78)
    for row in rows:
        print(f"{row['video_id']:<16} {row['outcome']:<18} {row['detail']}")
    print("=" * 78)
    print("SUMMARY_JSON " + json.dumps(summarize(rows)))


# ------------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--video-ids", required=True,
                    help="comma-separated candidate video IDs")
    ap.add_argument("--db-root", default="database",
                    help="repo database dir scanned for the keep-set")
    args = ap.parse_args(argv)

    candidates = parse_candidates(args.video_ids)
    if not candidates:
        print("ERROR: no candidate video IDs given", file=sys.stderr)
        return 1
    keep_set = build_keep_set(args.db_root)
    print(f"candidates: {len(candidates)}; keep-set size: {len(keep_set)}")
    refused, todo = partition_candidates(candidates, keep_set)
    if refused:
        print(f"keep-set guard refuses {len(refused)} candidate(s): "
              + ", ".join(refused))
    if not todo:
        print("nothing to delete (all candidates refused)")
        print_report([{"video_id": v, "outcome": OUT_REFUSED,
                       "detail": "present in a current DB record; "
                                 "deletion refused by keep-set guard"}
                      for v in refused])
        return 0

    # Lazy import: the pure guard logic above (and its tests) must not
    # depend on the google client libraries being installed.
    import youtube_upload as yu
    creds, _full = yu.get_credentials_for_scopes()
    if not creds:
        print("ERROR: no YouTube credentials available "
              "(YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET / "
              "YOUTUBE_REFRESH_TOKEN)", file=sys.stderr)
        return 1
    youtube = yu.build("youtube", "v3", credentials=creds)

    rows = run_cleanup(youtube, candidates, keep_set,
                       yu._classify_upload_error, yu._error_reason)
    print_report(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
