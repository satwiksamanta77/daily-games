"""Fix-loop iteration 48 tests - publish-path idempotency +
enrichment-only retry.

Defect (ANALYSIS47 SS5, proven in production run 39401472872): the
publish path never checked the committed DB record before
uploading, so a gated run dispatched with force=true re-uploaded
all 18 already-published games as duplicates and repointed the DB
at the newest copies.

Contract under test:
- (a) A gated publish for a (date, game) whose record is already
  published NEVER calls the upload (zero videos.insert), even
  under force, and the existing video id/url/published_at/attempts
  are preserved untouched.
- (b) A published record with enrichment_deferred steps retries
  ONLY those steps against the EXISTING video id; steps that
  succeed leave the deferred list, quota failures stay deferred,
  and the video stays published throughout.
- (c) An unpublished (or missing) record still uploads normally.
- The same-date retry hook (_retry_today_enrichment) gives a
  date-targeted no-force dispatch the same recovery: it updates
  the record in place, uploads nothing, and never drops a step
  whose inputs are unavailable in this run (thumbnail file on a
  stateless runner stays deferred, not "skipped").

The mocked-YouTube tests NEVER hit the real YouTube API: they
monkeypatch youtube_upload.build / MediaFileUpload /
get_credentials_for_scopes with fakes (same pattern as iter-37).
On the pre-fix code these tests fail: _publish_game_video /
_retry_today_enrichment do not exist, and retry_deferred_enrichment
dropped a missing thumbnail as "skipped".
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import runner as R
import youtube_upload as YU


# ------------------------------------------------------- fake YouTube

class _FakeHttpError(Exception):
    """googleapiclient HttpError shape: .content (JSON bytes) + .resp."""

    def __init__(self, reason, status=403):
        super().__init__(reason)
        self.content = json.dumps(
            {"error": {"errors": [{"reason": reason}]}}).encode()
        self.resp = type("Resp", (), {"status": status})()


class _FakeRequest:
    def __init__(self, result=None, exc=None):
        self._result = result
        self._exc = exc

    def execute(self):
        if self._exc is not None:
            raise self._exc
        return self._result


class _FakeResource:
    def __init__(self, svc, name):
        self._svc = svc
        self._name = name

    def insert(self, **kw):
        return self._svc._op(self._name, "insert", kw)

    def list(self, **kw):
        return self._svc._op(self._name, "list", kw)

    def set(self, **kw):
        return self._svc._op(self._name, "set", kw)


class _FakeYouTube:
    """Records every API call (resource, method, kwargs) in order."""

    def __init__(self, video_id="NEWVID67890"):
        self.calls = []
        self.video_id = video_id
        self.fail_on = {}          # (resource, method) -> exception

    def _op(self, resource, method, kw):
        self.calls.append((resource, method, kw))
        key = (resource, method)
        if key in self.fail_on:
            return _FakeRequest(exc=self.fail_on[key])
        if resource == "videos" and method == "insert":
            return _FakeRequest({"id": self.video_id})
        if resource == "playlists" and method == "list":
            return _FakeRequest({"items": []})
        if resource == "playlists" and method == "insert":
            return _FakeRequest({"id": "PL123"})
        if resource == "playlistItems":
            return _FakeRequest({"id": "PLI1"})
        if resource == "commentThreads":
            return _FakeRequest({"id": "Ug1"})
        if resource == "captions":
            return _FakeRequest({"id": "CAP1"})
        return _FakeRequest({})

    def videos(self):
        return _FakeResource(self, "videos")

    def thumbnails(self):
        return _FakeResource(self, "thumbnails")

    def playlists(self):
        return _FakeResource(self, "playlists")

    def playlistItems(self):
        return _FakeResource(self, "playlistItems")

    def commentThreads(self):
        return _FakeResource(self, "commentThreads")

    def captions(self):
        return _FakeResource(self, "captions")

    def kinds(self):
        return [(r, m) for r, m, _kw in self.calls]


class _FakeMediaFileUpload:
    def __init__(self, *a, **kw):
        pass


# ------------------------------------------------------------- fixtures

OLD_ID = "OLDVID12345"
DATE = "2026-10-20"
GID = "betweenle"


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_here = R.HERE
        R.HERE = Path(self._tmp.name)
        self._old_has = R.HAS
        R.HAS = True
        self._old_env = {k: os.environ.get(k) for k in
                         ("YOUTUBE_PUBLISH", "FORCE_REGENERATE")}
        os.environ["YOUTUBE_PUBLISH"] = "true"
        os.environ.pop("FORCE_REGENERATE", None)
        # Fake video + thumbnail files.
        self._video = str(Path(self._tmp.name) / "fake.mp4")
        Path(self._video).write_bytes(b"fake-video-bytes")
        self._thumb = str(Path(self._tmp.name) / "thumb.png")
        Path(self._thumb).write_bytes(b"fake-png-bytes")
        # Mocked YouTube service (iter-37 pattern).
        self._fake = _FakeYouTube()
        self._missing = object()
        self._orig_libs = YU.GOOGLE_LIBS_AVAILABLE
        self._orig_build = getattr(YU, "build", self._missing)
        self._orig_mfu = getattr(YU, "MediaFileUpload", self._missing)
        self._orig_creds = YU.get_credentials_for_scopes
        YU.GOOGLE_LIBS_AVAILABLE = True
        YU.build = lambda *a, **k: self._fake
        YU.MediaFileUpload = _FakeMediaFileUpload
        YU.get_credentials_for_scopes = lambda: (object(), True)
        self.upload_calls = 0

    def tearDown(self):
        R.HERE = self._old_here
        R.HAS = self._old_has
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        YU.GOOGLE_LIBS_AVAILABLE = self._orig_libs
        if self._orig_build is self._missing:
            del YU.build
        else:
            YU.build = self._orig_build
        if self._orig_mfu is self._missing:
            del YU.MediaFileUpload
        else:
            YU.MediaFileUpload = self._orig_mfu
        YU.get_credentials_for_scopes = self._orig_creds
        self._tmp.cleanup()

    # -- helpers ------------------------------------------------------
    def _context(self, thumbnail_path=None):
        return {"comment_text": "Chapters + subscribe",
                "playlist_titles": [
                    ("Betweenle Answers \u2014 October 2026", "monthly"),
                    ("Betweenle Answers \u2014 2026", "yearly")],
                "duration_s": 120, "date_str": "October 20, 2026",
                "game": "Betweenle",
                "chapters": [(0, "Intro"), (60, "Solve")],
                "thumbnail_path": (self._thumb if thumbnail_path is None
                                   else thumbnail_path)}

    def _published_record(self, deferred=(), with_context=True):
        enr = {}
        for step in ("thumbnail", "playlist", "comment", "captions"):
            enr[step] = {"status": "deferred" if step in deferred
                         else "done",
                         "reason": "quota: quotaExceeded"
                         if step in deferred else "", "cost": 0}
        rec = {"game": GID, "date": DATE, "solved": True,
               "evidence": "ok", "video": "videos/betweenle/final.mp4",
               "published": True, "youtube_video_id": OLD_ID,
               "youtube_url": f"https://www.youtube.com/watch?v={OLD_ID}",
               "published_at": "2026-10-20T09:39:31Z",
               "publish_attempts": 1, "last_publish_error": None,
               "enrichment": enr,
               "enrichment_deferred": list(deferred),
               "quota_estimate": {"upload_calls": 1,
                                  "general_units_estimated": 50},
               "shorts_video_id": None,
               "enrichment_context": (self._context()
                                      if with_context else {})}
        return rec

    def _seed(self, rec):
        R._db_save(DATE, GID, rec)

    def _read_record(self):
        p = R.HERE / "database" / DATE / GID / "result.json"
        return json.loads(p.read_text())

    def _upload_fn(self):
        """The call sites' upload closure, bound to the fake API."""
        def _fn():
            self.upload_calls += 1
            return YU.upload_game_video(
                self._video, title="Betweenle Answer Today",
                description="desc", tags=["betweenle answer today"],
                chapters=[(0, "Intro"), (60, "Solve")],
                thumbnail_path=self._thumb, game="Betweenle",
                date_str="October 20, 2026",
                playlist_titles=[("Betweenle Answers \u2014 October 2026",
                                  "x")],
                comment_text="Chapters here")
        return _fn


# ---------------------------------------------------------------- tests

class TestIdempotencyGate(_Base):
    def test_published_record_never_reuploads_even_under_force(self):
        self._seed(self._published_record())
        for force in (None, "true"):
            if force is None:
                os.environ.pop("FORCE_REGENERATE", None)
            else:
                os.environ["FORCE_REGENERATE"] = force
            pub = R._publish_game_video(GID, DATE, self._context(),
                                        self._upload_fn())
            self.assertTrue(pub["published"])
            self.assertEqual(pub["youtube_video_id"], OLD_ID)
            self.assertEqual(
                pub["youtube_url"],
                f"https://www.youtube.com/watch?v={OLD_ID}")
            self.assertEqual(pub["published_at"],
                             "2026-10-20T09:39:31Z")
            self.assertEqual(pub["publish_attempts"], 1)
            self.assertEqual(self.upload_calls, 0,
                             f"upload_fn called (force={force})")
            self.assertNotIn(("videos", "insert"), self._fake.kinds())

    def test_published_record_retries_only_deferred_enrichment(self):
        self._seed(self._published_record(
            deferred=("thumbnail", "playlist", "comment", "captions")))
        pub = R._publish_game_video(GID, DATE, self._context(),
                                    self._upload_fn())
        # No upload; enrichment hit ONLY the existing video id.
        self.assertEqual(self.upload_calls, 0)
        self.assertNotIn(("videos", "insert"), self._fake.kinds())
        self.assertTrue(pub["published"])
        self.assertEqual(pub["youtube_video_id"], OLD_ID)
        kinds = self._fake.kinds()
        for expected in (("thumbnails", "set"),
                         ("playlistItems", "insert"),
                         ("commentThreads", "insert"),
                         ("captions", "insert")):
            self.assertIn(expected, kinds)
        for resource, method, kw in self._fake.calls:
            if resource == "thumbnails":
                self.assertEqual(kw.get("videoId"), OLD_ID)
            if resource in ("playlistItems", "commentThreads",
                            "captions"):
                body = kw.get("body") or {}
                snip = body.get("snippet") or {}
                vid = (snip.get("videoId")
                       or (snip.get("resourceId") or {}).get("videoId"))
                self.assertEqual(vid, OLD_ID, (resource, kw))
        # All steps done -> deferred list cleared.
        self.assertEqual(pub["enrichment_deferred"], [])
        for step in ("thumbnail", "playlist", "comment", "captions"):
            self.assertEqual(pub["enrichment"][step]["status"], "done",
                             step)
        self.assertEqual(pub["quota_estimate"]["upload_calls"], 0)

    def test_quota_still_exhausted_keeps_steps_deferred(self):
        self._seed(self._published_record(
            deferred=("thumbnail", "playlist", "comment", "captions")))
        self._fake.fail_on[("thumbnails", "set")] = _FakeHttpError(
            "quotaExceeded", 403)
        self._fake.fail_on[("captions", "insert")] = _FakeHttpError(
            "quotaExceeded", 403)
        pub = R._publish_game_video(GID, DATE, self._context(),
                                    self._upload_fn())
        self.assertTrue(pub["published"])  # never unpublishes
        self.assertEqual(pub["youtube_video_id"], OLD_ID)
        self.assertEqual(self.upload_calls, 0)
        self.assertNotIn(("videos", "insert"), self._fake.kinds())
        self.assertIn("thumbnail", pub["enrichment_deferred"])
        self.assertIn("captions", pub["enrichment_deferred"])
        self.assertEqual(pub["enrichment"]["comment"]["status"],
                         "done")

    def test_unpublished_record_uploads_normally(self):
        # (i) No record at all.
        pub = R._publish_game_video(GID, DATE, self._context(),
                                    self._upload_fn())
        self.assertEqual(self.upload_calls, 1)
        self.assertTrue(pub["published"])
        self.assertEqual(pub["youtube_video_id"], "NEWVID67890")
        self.assertEqual(self._fake.kinds().count(("videos", "insert")),
                         1)
        # (ii) A solved-but-unpublished record still uploads.
        self._fake.calls.clear()
        self.upload_calls = 0
        rec = self._published_record()
        rec.update({"published": False, "youtube_video_id": None,
                    "youtube_url": None, "published_at": None,
                    "last_publish_error": "publish gate off"})
        self._seed(rec)
        pub = R._publish_game_video(GID, DATE, self._context(),
                                    self._upload_fn())
        self.assertEqual(self.upload_calls, 1)
        self.assertTrue(pub["published"])
        self.assertEqual(pub["youtube_video_id"], "NEWVID67890")


class TestSameDateRetry(_Base):
    def test_retry_today_enrichment_updates_record_in_place(self):
        # Stored thumbnail path does not exist in this run (stateless
        # runner): that step must stay deferred, never be dropped.
        rec = self._published_record(
            deferred=("thumbnail", "playlist", "comment", "captions"))
        rec["enrichment_context"] = self._context(
            thumbnail_path=str(Path(self._tmp.name) / "gone.png"))
        self._seed(rec)
        R._retry_today_enrichment(GID, DATE)
        out = self._read_record()
        self.assertTrue(out["published"])
        self.assertEqual(out["youtube_video_id"], OLD_ID)
        self.assertNotIn(("videos", "insert"), self._fake.kinds())
        for step in ("playlist", "comment", "captions"):
            self.assertEqual(out["enrichment"][step]["status"], "done",
                             step)
            self.assertNotIn(step, out["enrichment_deferred"])
        self.assertIn("thumbnail", out["enrichment_deferred"])
        self.assertEqual(out["enrichment"]["thumbnail"]["status"],
                         "deferred")

    def test_retry_today_enrichment_gate_off_is_noop(self):
        os.environ.pop("YOUTUBE_PUBLISH", None)
        self._seed(self._published_record(
            deferred=("thumbnail", "playlist", "comment", "captions")))
        before = (R.HERE / "database" / DATE / GID
                  / "result.json").read_bytes()
        R._retry_today_enrichment(GID, DATE)
        after = (R.HERE / "database" / DATE / GID
                 / "result.json").read_bytes()
        self.assertEqual(before, after)
        self.assertEqual(self._fake.calls, [])


class TestRetryMissingThumbnailStaysDeferred(_Base):
    def test_missing_thumbnail_file_is_deferred_not_skipped(self):
        out, _units = YU.retry_deferred_enrichment(
            OLD_ID, ["thumbnail"],
            {"thumbnail_path": "/nonexistent/definitely/gone.png"})
        self.assertEqual(out["thumbnail"]["status"], "deferred")


class TestCallSitesRouteThroughGate(unittest.TestCase):
    """Source-level guard (iter-47 idiom): every upload_game_video
    call site must route through the idempotency gate, so a future
    refactor cannot silently restore duplicate uploads."""

    def test_all_call_sites_gated(self):
        root = Path(__file__).parent.parent
        runner_src = (root / "runner.py").read_text(encoding="utf-8")
        nerdle_src = (root / "nerdle_solver.py").read_text(
            encoding="utf-8")
        self.assertIn("_publish_game_video(gid, date_key, _ectx,",
                      runner_src)          # generic run_one path
        self.assertIn("_publish_game_video(gid, date_key, _ectx, "
                      "_do_upload)", runner_src)  # framed path
        self.assertIn('_pub_gate("nerdle", date_key, _nectx,',
                      nerdle_src)          # nerdle path


if __name__ == "__main__":
    unittest.main()
