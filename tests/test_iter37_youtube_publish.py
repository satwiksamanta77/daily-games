"""Fix-loop iteration 37 tests — YouTube publish system.

Covers: SEO builders (caps, rotation, FAQ, no-fabrication), DB
semantics (published only on video_id, youtube.txt never a local
path, never-downgrade, lazy migration), publish gate (off by
default / on for true), thumbnail (partial reveal, 1280x720, no
full answer), and a mocked-YouTube end-to-end publish pass.

The mocked-YouTube tests NEVER hit the real YouTube API: they
monkeypatch youtube_upload.build / MediaFileUpload /
get_credentials_for_scopes with fakes (googleapiclient is not
installed in this environment, so the module's GOOGLE_LIBS_AVAILABLE
flag is also patched).
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import seo as SEO
import runner as R
import youtube_upload as YU


# ---------------------------------------------------------------- SEO

class TestSEOBuilders(unittest.TestCase):
    def test_title_three_variants_distinct_and_capped(self):
        variants = {SEO.build_title("Betweenle", "Oct 19", guesses=3,
                                    puzzle_number=100, seed=i)
                    for i in (0, 1, 2)}
        self.assertEqual(len(variants), 3)
        for t in variants:
            self.assertTrue(t.startswith("Betweenle Answer Today"))
            self.assertLessEqual(len(t), 100)

    def test_title_rotation_deterministic(self):
        a = SEO.build_title("Betweenle", "Oct 19", guesses=3,
                            puzzle_number=1683)
        b = SEO.build_title("Betweenle", "Oct 19", guesses=3,
                            puzzle_number=1683)
        self.assertEqual(a, b)
        # Different puzzle number -> a (usually) different variant.
        c = SEO.build_title("Betweenle", "Oct 19", guesses=3,
                            puzzle_number=1684)
        self.assertNotEqual(a, c)

    def test_title_capped_for_long_game_name(self):
        t = SEO.build_title("A" * 60, "Oct 19", guesses=3,
                            puzzle_number=123456)
        self.assertLessEqual(len(t), 100)

    def test_tags_head_keyword_first_and_capped(self):
        tags = SEO.build_tags("Betweenle", "Oct 19", date_num="10/19",
                              puzzle_number=1683)
        self.assertEqual(tags[0], "betweenle answer today")
        total = sum(len(t) for t in tags) + max(0, len(tags) - 1)
        self.assertLessEqual(total, 500)
        self.assertIn("Betweenle #1683", tags)

    def test_description_chapters_from_actual_chapters(self):
        chapters = [(0, "Intro"), (65, "The Solve"), (180, "Answer")]
        d = SEO.build_description("Betweenle", "betweenle-answer-today",
                                  "October 19, 2026", "Oct 19",
                                  answer="ORANGE", guesses=3,
                                  chapters=chapters, puzzle_number=1683)
        self.assertIn("1:05 The Solve", d)
        self.assertIn("3:00 Answer", d)
        self.assertIn("Today's Betweenle answer is ORANGE", d)
        self.assertIn("How many guesses did it take?", d)
        self.assertLessEqual(len(d), 5000)

    def test_description_no_fabrication(self):
        # Nothing supplied -> yesterday/tomorrow/analysis/solver blocks
        # are omitted, never invented.
        d = SEO.build_description("Betweenle", "betweenle-answer-today",
                                  "October 19, 2026", "Oct 19",
                                  answer="ORANGE", guesses=3,
                                  chapters=[(0, "Intro")])
        self.assertNotIn("YESTERDAY'S", d)
        self.assertNotIn("What was yesterday's", d)
        self.assertNotIn("TOMORROW'S", d)
        self.assertNotIn("ANALYSIS:", d)
        self.assertNotIn("Solver:", d)

    def test_description_yesterday_and_tomorrow_only_when_supplied(self):
        d = SEO.build_description(
            "Betweenle", "betweenle-answer-today",
            "October 19, 2026", "Oct 19", answer="ORANGE", guesses=3,
            chapters=[(0, "Intro")],
            yesterday={"answer": "APPLE", "puzzle_number": 100,
                       "date_short": "October 18"},
            tomorrow={"first_letter": "Z", "puzzle_number": 102})
        self.assertIn("YESTERDAY'S BETWEENLE (#100): APPLE", d)
        self.assertIn("What was yesterday's Betweenle answer?", d)
        # Tomorrow teaser is FIRST LETTER ONLY — no full word exists
        # to leak, and the teaser must not invent one.
        self.assertIn("TOMORROW'S BETWEENLE (#102): First letter is 'Z'", d)
        self.assertNotIn("ZEBRA", d)

    def test_description_analysis_only_when_supplied(self):
        d = SEO.build_description(
            "Betweenle", "betweenle-answer-today",
            "October 19, 2026", "Oct 19", answer="ORANGE",
            analysis={"word": "ORANGE", "part_of_speech": "noun",
                      "definition": "A citrus fruit.",
                      "example": "I ate an orange."})
        self.assertIn("ANALYSIS:", d)
        self.assertIn("A citrus fruit.", d)

    def test_description_capped_with_huge_chapters(self):
        chapters = [(i * 10, f"Chapter number {i} with a long label")
                    for i in range(500)]
        d = SEO.build_description("Betweenle", "betweenle-answer-today",
                                  "October 19, 2026", "Oct 19",
                                  answer="ORANGE", chapters=chapters)
        self.assertLessEqual(len(d), 5000)

    def test_pinned_comment_has_chapters_no_full_answer(self):
        c = SEO.build_pinned_comment(
            "Betweenle", [(0, "Intro"), (65, "The Solve")],
            solver_url="https://wordsolverx.com/betweenle-solver",
            answer_url="https://wordsolverx.com/betweenle-answer-today")
        self.assertIn("1:05 The Solve", c)
        self.assertIn("wordsolverx.com/betweenle-solver", c)
        # The comment builder has no answer parameter: it cannot leak
        # the answer.
        self.assertNotIn("ORANGE", c)

    def test_partial_tiles_curiosity_gap(self):
        self.assertEqual(SEO.partial_tiles("ORANGE"),
                         ["O", "R", "?", "?", "?", "?"])
        self.assertEqual(SEO.partial_tiles(""), ["?", "?", "?", "?", "?"])
        self.assertEqual(SEO.partial_tiles(None), ["?", "?", "?", "?", "?"])
        # Spaces/punctuation are not tiles; first 2 alnum revealed.
        self.assertEqual(SEO.partial_tiles("Will Smith"),
                         ["W", "I", "?", "?", "?", "?", "?", "?", "?"])
        self.assertEqual(SEO.partial_tiles("A"), ["A"])
        # Never reveals more than 2 characters.
        tiles = SEO.partial_tiles("SUPERCALIFRAGILISTIC")
        self.assertEqual(tiles[:2], ["S", "U"])
        self.assertTrue(all(t == "?" for t in tiles[2:]))

    def test_captions_srt_format(self):
        srt = SEO.build_captions_srt(
            [(0, "Intro"), (60, "Solve")], 120, "October 19, 2026",
            "Betweenle")
        self.assertIn("00:00:00,000 --> 00:01:00,000", srt)
        self.assertIn("Intro - Betweenle October 19, 2026", srt)

    def test_default_cross_links_18_games(self):
        self.assertEqual(len(SEO.DEFAULT_CROSS_LINKS), 18)
        urls = [u for _, u in SEO.DEFAULT_CROSS_LINKS]
        self.assertIn("https://wordsolverx.com/nerdle-answer-today", urls)
        self.assertEqual(len(set(urls)), 18)


# ---------------------------------------------------------------- gate

class TestPublishGate(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("YOUTUBE_PUBLISH")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("YOUTUBE_PUBLISH", None)
        else:
            os.environ["YOUTUBE_PUBLISH"] = self._old

    def test_gate_off_by_default(self):
        os.environ.pop("YOUTUBE_PUBLISH", None)
        self.assertFalse(R._publish_enabled())

    def test_gate_truthy_values(self):
        for v in ("true", "True", "TRUE", "1", "yes", "on", " true "):
            os.environ["YOUTUBE_PUBLISH"] = v
            self.assertTrue(R._publish_enabled(), v)

    def test_gate_falsy_values(self):
        for v in ("false", "0", "no", "off", ""):
            os.environ["YOUTUBE_PUBLISH"] = v
            self.assertFalse(R._publish_enabled(), v)

    def test_empty_publish_dict(self):
        pub = R._empty_publish("gate off")
        self.assertFalse(pub["published"])
        self.assertIsNone(pub["youtube_video_id"])
        self.assertEqual(pub["last_publish_error"], "gate off")

    def test_puzzle_number_anchors(self):
        # Verified anchors (ANALYSIS33/35): Framed classic 2026-10-19
        # -> 1683, Nerdle classic -> 1734, Semantle -> 1723.
        self.assertEqual(R._puzzle_number_for("framed", "2026-10-19"),
                         1683)
        self.assertEqual(R._puzzle_number_for("nerdle", "2026-10-19"),
                         1734)
        self.assertEqual(R._puzzle_number_for("semantle", "2026-10-19"),
                         1723)
        ph = R._puzzle_number_for("phrazle", "2026-10-19")
        self.assertIsInstance(ph, int)
        self.assertGreater(ph, 0)

    def test_puzzle_number_none_when_not_computable(self):
        # Waffle's number comes from a live fetch, Batterup's from the
        # CDN file — SEO must omit the number, never invent one.
        self.assertIsNone(R._puzzle_number_for("waffle", "2026-10-19"))
        self.assertIsNone(R._puzzle_number_for("batterup", "2026-10-19"))
        self.assertIsNone(R._puzzle_number_for("nope", "2026-10-19"))
        self.assertIsNone(R._puzzle_number_for("framed", "not-a-date"))

    def test_cross_links_exclude_self(self):
        links = R._cross_links("betweenle")
        self.assertEqual(len(links), 17)
        self.assertNotIn("betweenle-answer-today",
                         [u for _, u in links])
        self.assertIn("https://wordsolverx.com/nerdle-answer-today",
                      [u for _, u in links])

    def test_playlist_titles(self):
        titles = R._playlist_titles("Nerdle", "2026-10-19")
        self.assertEqual(titles[0][0], "Nerdle Answers \u2014 October 2026")
        self.assertEqual(titles[1][0], "Nerdle Answers \u2014 2026")


# ---------------------------------------------------------------- DB

class TestDBSemantics(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_here = R.HERE
        R.HERE = Path(self._tmp.name)
        self._old_env = os.environ.get("YOUTUBE_PUBLISH")

    def tearDown(self):
        R.HERE = self._old_here
        if self._old_env is None:
            os.environ.pop("YOUTUBE_PUBLISH", None)
        else:
            os.environ["YOUTUBE_PUBLISH"] = self._old_env
        self._tmp.cleanup()

    def _read(self, gid="betweenle", date_key="2026-10-19"):
        d = R.HERE / "database" / date_key / gid
        rec = json.loads((d / "result.json").read_text())
        yt = (d / "youtube.txt").read_text()
        return rec, yt

    def _published_record(self):
        return {"game": "betweenle", "date": "2026-10-19",
                "solved": True, "evidence": "ok",
                "video": "videos/betweenle/betweenle_final_2026-10-19.mp4",
                "published": True, "youtube_video_id": "dQw4w9WgXcQ",
                "youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "published_at": "2026-10-19T15:00:00Z",
                "publish_attempts": 1, "last_publish_error": None,
                "enrichment": {}, "enrichment_deferred": [],
                "quota_estimate": {}, "shorts_video_id": None,
                "enrichment_context": {}}

    def test_published_record_writes_youtube_url(self):
        R._db_save("2026-10-19", "betweenle", self._published_record())
        rec, yt = self._read()
        self.assertTrue(rec["published"])
        self.assertEqual(yt, "https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    def test_unpublished_record_writes_not_published(self):
        rec0 = self._published_record()
        rec0.update({"published": False, "youtube_video_id": None,
                     "youtube_url": None, "published_at": None,
                     "last_publish_error": "gate off"})
        R._db_save("2026-10-19", "betweenle", rec0)
        rec, yt = self._read()
        self.assertFalse(rec["published"])
        self.assertEqual(yt, "NOT_PUBLISHED")

    def test_youtube_txt_never_a_local_path(self):
        # Old behaviour wrote the local mp4 path into youtube.txt.
        # A record with a local path but no video id is NOT published.
        rec0 = {"game": "betweenle", "date": "2026-10-19",
                "solved": True, "evidence": "ok",
                "video": "/home/runner/work/x/betweenle_final.mp4",
                "youtube_url": None}
        self.assertFalse(R._record_is_published(rec0))
        R._db_save("2026-10-19", "betweenle", rec0)
        _rec, yt = self._read()
        self.assertEqual(yt, "NOT_PUBLISHED")
        self.assertNotIn("/home/", yt)
        self.assertNotIn(".mp4", yt)

    def test_never_downgrade_published(self):
        R._db_save("2026-10-19", "betweenle", self._published_record())
        # A later unpublished run must NOT replace the published record.
        rec0 = self._published_record()
        rec0.update({"published": False, "youtube_video_id": None,
                     "youtube_url": None,
                     "last_publish_error": "quota"})
        R._db_save("2026-10-19", "betweenle", rec0)
        rec, yt = self._read()
        self.assertTrue(rec["published"])
        self.assertEqual(rec["youtube_video_id"], "dQw4w9WgXcQ")
        self.assertEqual(yt, "https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    def test_never_downgrade_solved(self):
        R._db_save("2026-10-19", "betweenle", self._published_record())
        R._db_save("2026-10-19", "betweenle",
                   {"game": "betweenle", "date": "2026-10-19",
                    "solved": False, "evidence": "flaky re-run"})
        rec, _yt = self._read()
        self.assertTrue(rec["solved"])

    def test_lazy_migration_old_local_path(self):
        # Pre-iter-37 youtube.txt holding a local path is treated as
        # NOT_PUBLISHED; the next save corrects it (no bulk rewrite).
        d = R.HERE / "database" / "2026-10-19" / "betweenle"
        d.mkdir(parents=True)
        (d / "youtube.txt").write_text(
            "/home/runner/work/x/betweenle_final_2026-10-19.mp4")
        R._db_save("2026-10-19", "betweenle",
                   {"game": "betweenle", "date": "2026-10-19",
                    "solved": True, "evidence": "ok", "video": "x.mp4",
                    "published": False, "youtube_video_id": None,
                    "youtube_url": None, "published_at": None,
                    "publish_attempts": 0,
                    "last_publish_error": "gate off", "enrichment": {},
                    "enrichment_deferred": [], "quota_estimate": {},
                    "shorts_video_id": None, "enrichment_context": {}})
        self.assertEqual((d / "youtube.txt").read_text(), "NOT_PUBLISHED")

    def test_skip_guard_publish_semantics(self):
        # Solved but unpublished: in a publish-gated run it is NOT
        # done (re-solve + publish); in a gate-off run it IS done.
        rec0 = self._published_record()
        rec0.update({"published": False, "youtube_video_id": None,
                     "youtube_url": None, "published_at": None})
        R._db_save("2026-10-19", "betweenle", rec0)
        os.environ["YOUTUBE_PUBLISH"] = "true"
        self.assertIsNone(R._db_already_done("2026-10-19", "betweenle"))
        os.environ.pop("YOUTUBE_PUBLISH", None)
        self.assertIsNotNone(
            R._db_already_done("2026-10-19", "betweenle"))
        # Solved AND published: done even in a publish-gated run.
        R._db_save("2026-10-19", "betweenle", self._published_record())
        os.environ["YOUTUBE_PUBLISH"] = "true"
        self.assertIsNotNone(
            R._db_already_done("2026-10-19", "betweenle"))

    def test_yesterday_answer_from_own_db(self):
        d = R.HERE / "database" / "2026-10-18" / "betweenle"
        d.mkdir(parents=True)
        (d / "result.json").write_text(json.dumps(
            {"game": "betweenle", "date": "2026-10-18", "solved": True,
             "answer": "APPLE"}))
        got = R._yesterday_answer("betweenle", "2026-10-19")
        self.assertEqual(got["answer"], "APPLE")
        self.assertEqual(got["date_short"], "October 18")
        # Nerdle records carry no single answer -> omitted.
        d2 = R.HERE / "database" / "2026-10-18" / "nerdle"
        d2.mkdir(parents=True)
        (d2 / "result.json").write_text(json.dumps(
            {"game": "nerdle", "date": "2026-10-18", "solved": True}))
        self.assertIsNone(R._yesterday_answer("nerdle", "2026-10-19"))
        # No previous-day record -> None (never invented).
        self.assertIsNone(R._yesterday_answer("betweenle", "2026-10-18"))

    def test_yesterday_answer_framed_classic(self):
        d = R.HERE / "database" / "2026-10-18" / "framed"
        d.mkdir(parents=True)
        (d / "result.json").write_text(json.dumps(
            {"game": "framed", "date": "2026-10-18", "solved": True,
             "answers": {"classic": "ALIEN", "one_frame": "X"}}))
        got = R._yesterday_answer("framed", "2026-10-19")
        self.assertEqual(got["answer"], "ALIEN")


# ---------------------------------------------------------------- thumb

class _RecordingDraw:
    """ImageDraw stub that records every text() call (proves the full
    answer is never drawn on the thumbnail)."""

    def __init__(self):
        self.texts = []

    def text(self, pos, txt, **kw):
        self.texts.append(str(txt))

    def rounded_rectangle(self, *a, **kw):
        pass

    def line(self, *a, **kw):
        pass

    def textbbox(self, pos, txt, **kw):
        return (0, 0, len(str(txt)) * 10, 20)

    def textlength(self, txt, **kw):
        return len(str(txt)) * 10


class TestThumbnail(unittest.TestCase):
    def test_size_1280x720_and_puzzle_card(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as td:
            out = str(Path(td) / "thumb.png")
            R.thumb(out, "Betweenle", "October 19, 2026", "ORANGE",
                    puzzle_number=1683)
            self.assertEqual(Image.open(out).size, (1280, 720))

    def test_no_full_answer_drawn(self):
        rec = _RecordingDraw()
        orig = R.ImageDraw.Draw
        R.ImageDraw.Draw = lambda img: rec
        try:
            with tempfile.TemporaryDirectory() as td:
                R.thumb(str(Path(td) / "thumb.png"), "Betweenle",
                        "October 19, 2026", "ORANGE", puzzle_number=1683)
        finally:
            R.ImageDraw.Draw = orig
        joined = " ".join(rec.texts)
        # The full answer must never appear as drawn text; only the
        # single-letter tiles (first 2 revealed, rest '?') may.
        self.assertNotIn("ORANGE", joined)
        self.assertIn("#1683", joined)

    def test_long_answer_and_no_puzzle_number(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as td:
            out = str(Path(td) / "thumb.png")
            # Font-fit must shrink, never crash, even on a long answer.
            R.thumb(out, "Betweenle", "October 19, 2026",
                    "SUPERCALIFRAGILISTICEXPIALIDOCIOUS")
            self.assertEqual(Image.open(out).size, (1280, 720))
            out2 = str(Path(td) / "thumb2.png")
            R.thumb(out2, "Betweenle", "October 19, 2026", "ORANGE")
            self.assertEqual(Image.open(out2).size, (1280, 720))


# ------------------------------------------------------- mocked YouTube

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
    """Records every API call in order; behaviour knobs per test."""

    def __init__(self, video_id="dQw4w9WgXcQ"):
        self.calls = []
        self.video_id = video_id
        self.fail_on = {}          # (resource, method) -> exception
        self.fail_once_on = {}     # (resource, method) -> exception (1x)
        self.existing_playlists = []

    def _op(self, resource, method, kw):
        self.calls.append((resource, method))
        key = (resource, method)
        if key in self.fail_once_on:
            exc = self.fail_once_on.pop(key)
            return _FakeRequest(exc=exc)
        if key in self.fail_on:
            return _FakeRequest(exc=self.fail_on[key])
        if resource == "videos" and method == "insert":
            return _FakeRequest({"id": self.video_id})
        if resource == "thumbnails":
            return _FakeRequest({})
        if resource == "playlists" and method == "list":
            items = [{"id": p[0], "snippet": {"title": p[1]}}
                     for p in self.existing_playlists]
            return _FakeRequest({"items": items})
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


class _FakeMediaFileUpload:
    def __init__(self, *a, **kw):
        pass


class TestMockedYouTubePublish(unittest.TestCase):
    """End-to-end publish pass against a fake YouTube service.

    NEVER hits the real YouTube API.
    """

    def setUp(self):
        self._fake = _FakeYouTube()
        self._orig_libs = YU.GOOGLE_LIBS_AVAILABLE
        # googleapiclient is not installed here, so YU.build /
        # YU.MediaFileUpload may not exist as module attributes at
        # all — save with a sentinel and restore exactly.
        self._missing = object()
        self._orig_build = getattr(YU, "build", self._missing)
        self._orig_mfu = getattr(YU, "MediaFileUpload", self._missing)
        self._orig_creds = YU.get_credentials_for_scopes
        YU.GOOGLE_LIBS_AVAILABLE = True
        YU.build = lambda *a, **k: self._fake
        YU.MediaFileUpload = _FakeMediaFileUpload
        YU.get_credentials_for_scopes = lambda: (object(), True)
        self._tmp = tempfile.TemporaryDirectory()
        self._video = str(Path(self._tmp.name) / "fake.mp4")
        Path(self._video).write_bytes(b"fake-video-bytes")
        self._thumb = str(Path(self._tmp.name) / "thumb.png")
        Path(self._thumb).write_bytes(b"fake-png-bytes")

    def tearDown(self):
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

    def _publish(self, **kw):
        args = {"video_path": self._video, "title": "Betweenle Answer Today",
                "description": "desc", "tags": ["betweenle answer today"],
                "chapters": [(0, "Intro"), (60, "Solve")],
                "thumbnail_path": self._thumb, "game": "Betweenle",
                "date_str": "October 19, 2026",
                "playlist_titles": [("Betweenle Answers \u2014 October 2026",
                                     "x")],
                "comment_text": "Chapters here"}
        args.update(kw)
        return YU.upload_game_video(**args)

    def test_full_publish_pass_enrichment_order(self):
        pub = self._publish()
        self.assertTrue(pub["published"])
        self.assertEqual(pub["youtube_video_id"], "dQw4w9WgXcQ")
        self.assertEqual(pub["youtube_url"],
                         "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertIsNotNone(pub["published_at"])
        self.assertEqual(pub["enrichment_deferred"], [])
        for step in ("thumbnail", "playlist", "comment", "captions"):
            self.assertEqual(pub["enrichment"][step]["status"], "done",
                             step)
        # Quota order: thumbnail -> playlist -> comment -> captions.
        order = [c for c in self._fake.calls]
        idx = {name: next(i for i, c in enumerate(order)
                          if c[0] == name)
               for name in ("thumbnails", "playlistItems",
                            "commentThreads", "captions")}
        self.assertLess(idx["thumbnails"], idx["playlistItems"])
        self.assertLess(idx["playlistItems"], idx["commentThreads"])
        self.assertLess(idx["commentThreads"], idx["captions"])
        self.assertEqual(pub["quota_estimate"]["upload_calls"], 1)
        self.assertGreater(
            pub["quota_estimate"]["general_units_estimated"], 0)

    def test_quota_on_insert_fail_fast_never_published(self):
        self._fake.fail_on[("videos", "insert")] = _FakeHttpError(
            "quotaExceeded", 403)
        pub = self._publish()
        self.assertFalse(pub["published"])
        self.assertIsNone(pub["youtube_video_id"])
        self.assertIsNone(pub["youtube_url"])
        self.assertEqual(pub["publish_attempts"], 1)
        self.assertIn("quotaExceeded", pub["last_publish_error"])
        # No enrichment attempted after a failed insert.
        self.assertEqual(pub["enrichment"], {})

    def test_quota_mid_enrichment_keeps_video_defer_rest(self):
        self._fake.fail_on[("playlistItems", "insert")] = _FakeHttpError(
            "quotaExceeded", 403)
        pub = self._publish()
        self.assertTrue(pub["published"])
        self.assertEqual(pub["enrichment"]["thumbnail"]["status"], "done")
        self.assertEqual(pub["enrichment"]["playlist"]["status"],
                         "deferred")
        self.assertEqual(pub["enrichment"]["comment"]["status"],
                         "deferred")
        self.assertEqual(pub["enrichment"]["captions"]["status"],
                         "deferred")
        self.assertEqual(set(pub["enrichment_deferred"]),
                         {"playlist", "comment", "captions"})

    def test_scope_fallback_skips_scope_gated_steps(self):
        YU.get_credentials_for_scopes = lambda: (object(), False)
        pub = self._publish()
        self.assertTrue(pub["published"])
        self.assertEqual(pub["enrichment"]["thumbnail"]["status"], "done")
        self.assertEqual(pub["enrichment"]["playlist"]["status"],
                         "skipped")
        self.assertIn("upload-only",
                      pub["enrichment"]["playlist"]["reason"])
        self.assertEqual(pub["enrichment"]["comment"]["status"],
                         "skipped")
        self.assertEqual(pub["enrichment"]["captions"]["status"], "done")
        # Scope-gated steps were never attempted (no API calls).
        kinds = [c[0] for c in self._fake.calls]
        self.assertNotIn("playlistItems", kinds)
        self.assertNotIn("commentThreads", kinds)

    def test_transient_error_retries_then_succeeds(self):
        self._fake.fail_once_on[("videos", "insert")] = _FakeHttpError(
            "backendError", 503)
        pub = self._publish()
        self.assertTrue(pub["published"])
        self.assertEqual(pub["publish_attempts"], 2)

    def test_failed_step_never_marked_done(self):
        # Captions failure (non-quota) must be deferred, not done.
        self._fake.fail_on[("captions", "insert")] = _FakeHttpError(
            "badRequest", 400)
        pub = self._publish()
        self.assertTrue(pub["published"])
        self.assertEqual(pub["enrichment"]["captions"]["status"],
                         "deferred")
        # Comment failure (pin returns no id) must be deferred.
        self._fake.fail_on.clear()
        self._fake.fail_on[("commentThreads", "insert")] = _FakeHttpError(
            "badRequest", 400)
        pub = self._publish()
        self.assertEqual(pub["enrichment"]["comment"]["status"],
                         "deferred")

    def test_error_classification(self):
        self.assertEqual(
            YU._classify_upload_error(_FakeHttpError("quotaExceeded")),
            "quota")
        self.assertEqual(
            YU._classify_upload_error(_FakeHttpError("uploadLimitExceeded")),
            "quota")
        self.assertEqual(
            YU._classify_upload_error(
                _FakeHttpError("backendError", 503)), "transient")
        self.assertEqual(
            YU._classify_upload_error(_FakeHttpError("badRequest", 400)),
            "fatal")
        self.assertEqual(YU._error_reason(_FakeHttpError("quotaExceeded")),
                         "quotaExceeded")

    def test_retry_deferred_enrichment(self):
        pub = self._publish()
        self.assertEqual(pub["enrichment_deferred"], [])
        # Retry only the captions step (simulating a previous defer).
        out, units = YU.retry_deferred_enrichment(
            "dQw4w9WgXcQ", ["captions"],
            {"chapters": [(0, "Intro")], "duration_s": 120,
             "date_str": "October 19, 2026", "game": "Betweenle"})
        self.assertEqual(out["captions"]["status"], "done")

    def test_no_upload_without_credentials(self):
        YU.get_credentials_for_scopes = lambda: (None, False)
        pub = self._publish()
        self.assertFalse(pub["published"])
        self.assertEqual(pub["last_publish_error"], "no YouTube credentials")
        self.assertNotIn(("videos", "insert"), self._fake.calls)


if __name__ == "__main__":
    unittest.main()
