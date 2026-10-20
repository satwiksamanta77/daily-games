"""Tests for admin_delete_duplicates.py (YouTube duplicate cleanup).

Covers the hard safety guard: the keep-set is built from EVERY
youtube_video_id in database/**/result.json and any candidate in it is
refused; videos that no longer exist are reported already-absent; a
quota error stops the run with the remainder marked pending-quota.

All tests are hermetic: a fake service object mimics the
googleapiclient .videos().list()/.delete() chain, and fake classify /
reason functions stand in for youtube_upload's error helpers (the real
ones need the google client libraries, which this test must not).

Run:  python3 -m unittest tests.test_admin_delete_duplicates -v
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import admin_delete_duplicates as adm


class FakeApiError(Exception):
    def __init__(self, kind, reason=""):
        super().__init__(f"{kind}:{reason}")
        self.kind = kind
        self.reason = reason


def fake_classify(exc):
    return getattr(exc, "kind", "fatal")


def fake_reason(exc):
    return getattr(exc, "reason", "")


class _Req:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class FakeVideos:
    def __init__(self, existing=(), delete_errors=None):
        self.existing = set(existing)
        self.delete_errors = dict(delete_errors or {})
        self.delete_calls = []

    def list(self, part=None, id=None):
        wanted = [t for t in (id or "").split(",") if t]

        def go():
            return {"items": [{"id": v, "snippet": {"title": f"T-{v}"}}
                              for v in wanted if v in self.existing]}
        return _Req(go)

    def delete(self, id=None):
        def go():
            self.delete_calls.append(id)
            if id in self.delete_errors:
                raise self.delete_errors[id]
            self.existing.discard(id)
            return {}
        return _Req(go)


class FakeYouTube:
    def __init__(self, existing=(), delete_errors=None):
        self._videos = FakeVideos(existing, delete_errors)

    def videos(self):
        return self._videos


def outcomes_by_id(rows):
    return {r["video_id"]: r["outcome"] for r in rows}


class TestKeepSet(unittest.TestCase):
    def test_build_keep_set_scans_all_dates(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for date, game, vid in [
                    ("2026-10-19", "wordle", "aaa111"),
                    ("2026-10-20", "spotle", "i9zO-0iM-Ns"),
                    ("2026-10-20", "nerdle", None)]:
                d = root / date / game
                d.mkdir(parents=True)
                rec = {"solved": True}
                if vid:
                    rec["youtube_video_id"] = vid
                (d / "result.json").write_text(json.dumps(rec))
            keep = adm.build_keep_set(root)
        self.assertEqual(keep, {"aaa111", "i9zO-0iM-Ns"})

    def test_partition_refuses_keepers(self):
        refused, todo = adm.partition_candidates(
            ["keep1", "stale1", "keep2", "stale2"], {"keep1", "keep2"})
        self.assertEqual(refused, ["keep1", "keep2"])
        self.assertEqual(todo, ["stale1", "stale2"])


class TestRunCleanup(unittest.TestCase):
    def test_keeper_refused_and_never_deleted(self):
        yt = FakeYouTube(existing={"keeperX", "staleA"})
        rows = adm.run_cleanup(yt, ["keeperX", "staleA"], {"keeperX"},
                               fake_classify, fake_reason)
        by_id = outcomes_by_id(rows)
        self.assertEqual(by_id["keeperX"], adm.OUT_REFUSED)
        self.assertEqual(by_id["staleA"], adm.OUT_DELETED)
        self.assertEqual(yt.videos().delete_calls, ["staleA"])

    def test_already_absent_is_reported_not_deleted(self):
        yt = FakeYouTube(existing=set())
        rows = adm.run_cleanup(yt, ["ghost1"], set(),
                               fake_classify, fake_reason)
        self.assertEqual(outcomes_by_id(rows)["ghost1"], adm.OUT_ABSENT)
        self.assertEqual(yt.videos().delete_calls, [])

    def test_quota_stops_and_marks_remainder_pending(self):
        err = FakeApiError("quota", "quotaExceeded")
        yt = FakeYouTube(existing={"s1", "s2", "s3"},
                         delete_errors={"s1": err})
        rows = adm.run_cleanup(yt, ["s1", "s2", "s3"], set(),
                               fake_classify, fake_reason)
        by_id = outcomes_by_id(rows)
        self.assertEqual(by_id["s1"], adm.OUT_PENDING)
        self.assertEqual(by_id["s2"], adm.OUT_PENDING)
        self.assertEqual(by_id["s3"], adm.OUT_PENDING)
        # Exactly one delete was attempted before the stop.
        self.assertEqual(yt.videos().delete_calls, ["s1"])

    def test_permission_error_aborts(self):
        err = FakeApiError("fatal", "insufficientPermissions")
        yt = FakeYouTube(existing={"s1", "s2"},
                         delete_errors={"s1": err})
        rows = adm.run_cleanup(yt, ["s1", "s2"], set(),
                               fake_classify, fake_reason)
        by_id = outcomes_by_id(rows)
        self.assertEqual(by_id["s1"], adm.OUT_ERROR)
        self.assertEqual(by_id["s2"], adm.OUT_ERROR)
        self.assertEqual(yt.videos().delete_calls, ["s1"])

    def test_summary_groups_outcomes(self):
        yt = FakeYouTube(existing={"gone", "keeperX"})
        rows = adm.run_cleanup(yt, ["keeperX", "gone", "ghost"],
                               {"keeperX"}, fake_classify, fake_reason)
        s = adm.summarize(rows)
        self.assertEqual(s["refused_keep_set"], ["keeperX"])
        self.assertEqual(s["deleted"], ["gone"])
        self.assertEqual(s["already_absent"], ["ghost"])
        self.assertEqual(s["pending_quota"], [])
        self.assertEqual(s["errors"], {})


if __name__ == "__main__":
    unittest.main()
