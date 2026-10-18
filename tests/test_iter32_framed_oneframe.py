"""Fix-loop iteration 32 tests — Framed one-frame answer source (ANALYSIS30 §5).

Root cause (pinned from frame + site-source evidence): the bot answered
one-frame from the titles top-ten API's LAST frame leader
(frames[-1] = frame 6, '2001: A Space Odyssey' for #685), but
one-frame shows ONLY frame 001 (site image builder:
"/one-frame/images/<id>/001.jpeg"). The frame-6 leader answers a
frame the mode never shows, and the site scored it wrong twice.
The site scores one-frame against its own bundled list Mh[puzzle-1]
(frontend _app.js challenge loader). Indexing validated end-to-end:
classic's Us list gives Us[1681] == 'Warm Bodies' for classic #1682,
the known-won puzzle.

Fix: answers.framed() for one-frame uses the bundled list first
(via "oneframe-bundle"); if the bundle is unreachable, the titles
fallback uses FRAME 1's leader (via "oneframe-frame1") — never
frames[-1]. runner._framed_live_answer's last-resort block does the
same. Classic keeps frames[-1] (it shows all 6 frames).
"""
import json
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

import answers as A
import runner as R


class _Resp:
    def __init__(self, status, text=None, payload=None):
        self.status_code = status
        self.ok = 200 <= status < 300
        self.text = text if text is not None else ""
        self._payload = payload

    def json(self):
        return self._payload


_BUNDLE_JS = (
    '9690:function(e,t,i){"use strict";'
    'i.d(t,{Mh:function(){return r},oL:function(){return a}});'
    'var n=i(6834),r=[{title:"Alpha",id:1},'
    '{title:"The Blob",id:2},{title:"Gamma",id:3}];'
    'var o=1;'
)
_BUNDLE_HTML = (
    '<html><head></head><body><script src="/_next/static/chunks/'
    'pages/_app-abc123.js"></script></body></html>'
)
_TOP_TEN = {
    "game_number": "685",
    "frames": [
        {"frame_number": 1,
         "items": json.dumps([{"title": "Outbreak", "count": 130},
                              {"title": "Contagion", "count": 117}])},
        {"frame_number": 6,
         "items": json.dumps([{"title": "2001: A Space Odyssey",
                               "count": 45},
                              {"title": "Apollo 13", "count": 43}])},
    ],
}


def _bundle_get(url, **kw):
    if url == "https://framed.wtf/one-frame":
        return _Resp(200, text=_BUNDLE_HTML)
    if url.endswith("/_next/static/chunks/pages/_app-abc123.js"):
        return _Resp(200, text=_BUNDLE_JS)
    if "titles.framed.wtf" in url:
        return _Resp(200, payload=_TOP_TEN)
    raise AssertionError(f"unexpected fetch: {url}")


class TestOneFrameBundled(unittest.TestCase):
    def setUp(self):
        A._FRAMED_ONEFRAME_TITLES_CACHE = None

    def tearDown(self):
        A._FRAMED_ONEFRAME_TITLES_CACHE = None

    def test_bundled_index(self):
        with mock.patch("requests.get", _bundle_get):
            self.assertEqual(A.framed_oneframe_bundled(2), "The Blob")
            self.assertEqual(A.framed_oneframe_bundled(1), "Alpha")

    def test_bundled_out_of_range(self):
        with mock.patch("requests.get", _bundle_get):
            self.assertIsNone(A.framed_oneframe_bundled(0))
            self.assertIsNone(A.framed_oneframe_bundled(4))
            self.assertIsNone(A.framed_oneframe_bundled("nope"))

    def test_bundled_html_without_app_chunk(self):
        def _get(url, **kw):
            if url == "https://framed.wtf/one-frame":
                return _Resp(200, text="<html></html>")
            raise AssertionError(f"unexpected fetch: {url}")

        with mock.patch("requests.get", _get):
            self.assertIsNone(A.framed_oneframe_bundled(1))

    def test_framed_oneframe_uses_bundle(self):
        with mock.patch("requests.get", _bundle_get):
            got = A.framed(date(2026, 10, 18), mode="one-frame")
        # _framed_number(one-frame, 2026-10-18) = 685; the 3-entry
        # mock bundle has no index 684 -> bundled lookup misses ->
        # falls through to the titles frame-1 fallback. The bundle
        # path itself is pinned by test_bundled_index; here we pin
        # that the fallback is frame 1, NOT frame 6.
        self.assertEqual(got["answer"], "Outbreak")
        self.assertEqual(got["via"], "oneframe-frame1")

    def test_framed_oneframe_bundle_hit(self):
        # Small-numbered puzzle inside the mock bundle: patch
        # _framed_number to 2 so the bundled path hits.
        with mock.patch("requests.get", _bundle_get), \
                mock.patch.object(A, "_framed_number", return_value=2):
            got = A.framed(date(2026, 10, 18), mode="one-frame")
        self.assertEqual(got["answer"], "The Blob")
        self.assertEqual(got["via"], "oneframe-bundle")
        self.assertEqual(got["puzzle"], 2)

    def test_framed_oneframe_fallback_is_frame1_not_frame6(self):
        def _get(url, **kw):
            if url == "https://framed.wtf/one-frame":
                return _Resp(404, text="")
            if "titles.framed.wtf" in url:
                return _Resp(200, payload=_TOP_TEN)
            raise AssertionError(f"unexpected fetch: {url}")

        with mock.patch("requests.get", _get):
            got = A.framed(date(2026, 10, 18), mode="one-frame")
        self.assertEqual(got["answer"], "Outbreak")
        self.assertEqual(got["via"], "oneframe-frame1")

    def test_classic_still_uses_last_frame(self):
        def _get(url, **kw):
            if "titles.framed.wtf" in url:
                return _Resp(200, payload=_TOP_TEN)
            raise AssertionError(f"unexpected fetch: {url}")

        with mock.patch("requests.get", _get):
            got = A.framed(date(2026, 10, 18), mode="classic")
        self.assertEqual(got["answer"], "2001: A Space Odyssey")
        self.assertEqual(got["via"], "live")


class _FakePage:
    """Fake page for _framed_live_answer: evaluate() returns the board
    header number for the label script; goto/wait are no-ops."""

    def __init__(self, shown):
        self._shown = shown
        self.gotos = []

    def evaluate(self, script, arg=None):
        if "new RegExp" in script:
            return self._shown
        return ""

    def goto(self, url, **kw):
        self.gotos.append(url)

    def wait_for_timeout(self, ms):
        pass


class TestLiveAnswerOneFrame(unittest.TestCase):
    def setUp(self):
        A._FRAMED_ONEFRAME_TITLES_CACHE = None

    def tearDown(self):
        A._FRAMED_ONEFRAME_TITLES_CACHE = None

    def _framed_never_matches(self, *a, **kw):
        # Date-walk finds no date whose number == shown.
        return {"puzzle": 999, "answer": "Nope"}

    def test_live_answer_oneframe_uses_bundle(self):
        pg = _FakePage(shown=2)
        with mock.patch("requests.get", _bundle_get), \
                mock.patch("answers.framed", self._framed_never_matches):
            got = R._framed_live_answer(pg, "one-frame", "fallback")
        self.assertEqual(got, "The Blob")

    def test_live_answer_oneframe_bundle_down_uses_frame1(self):
        def _get(url, **kw):
            if "framed.wtf/one-frame" in url:
                return _Resp(404, text="")
            if "titles.framed.wtf" in url:
                return _Resp(200, payload=_TOP_TEN)
            raise AssertionError(f"unexpected fetch: {url}")

        pg = _FakePage(shown=685)
        with mock.patch("requests.get", _get), \
                mock.patch("answers.framed", self._framed_never_matches):
            got = R._framed_live_answer(pg, "one-frame", "fallback")
        self.assertEqual(got, "Outbreak")

    def test_live_answer_classic_still_uses_last_frame(self):
        def _get(url, **kw):
            if "titles.framed.wtf" in url:
                return _Resp(200, payload=_TOP_TEN)
            raise AssertionError(f"unexpected fetch: {url}")

        pg = _FakePage(shown=1682)
        with mock.patch("requests.get", _get), \
                mock.patch("answers.framed", self._framed_never_matches):
            got = R._framed_live_answer(pg, "classic", "fallback")
        self.assertEqual(got, "2001: A Space Odyssey")


if __name__ == "__main__":
    unittest.main()
