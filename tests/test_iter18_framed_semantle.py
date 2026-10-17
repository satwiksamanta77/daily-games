"""Fix-loop iteration 18 tests — slice FRAMED poster gate + SEMANTLE probe.

1. Framed poster ad gate (P0, ANALYSIS15 §11b / ROOT CAUSE 8) — VERDICT:
   SITE / AD-SERVER CHANGE, not a path regression. Evidence (artifact
   logs, not code): the tile-click + unlock sequence is identical in
   every run — tile element-click times out at 5s, tile JS-click
   True, board NOT ready, unlock start gate_up=True, WATCH AD
   element-click — and in six earlier runs (iter-12 through the
   iter-17 void run) that click unlocked poster in ~0s
   (gate_up=False). In the iter-17 run the same click REGISTERED
   (URL gained #goog_rewarded, a videoreward googletag slot
   existed) but NO creative ever loaded (ad_loaded=False for the
   full 45s: no ad-like iframe, no video, no IMA SDK). The
   tile-click path and the unlock function are unchanged between
   the winning and the deferred runs, so what changed is upstream:
   the site / ad server stopped filling the rewarded slot in
   headless CI. There is no legitimate non-ad path around it —
   bypassing the gate or faking the reward would be faking an ad
   view — so the honest outcome stays DEFER with no video for a
   3/4 game. Code change: the reward-ad snapshot now records
   whether the rewarded flow STARTED (reward_click_registered),
   and the DEFER is reserved for the proven site/ad no-fill
   (click registered + ad never loaded). A click that never
   registered falls through to the failure branch instead of
   being deferred as a site gate.

2. Semantle live-day probe host-filter (P1, ANALYSIS15 §4 /
   ROOT CAUSE 4): the probe accepted a date from ANY URL, so a
   DoubleVerify ad URL (dvtagver=dvot_2026-10-14) became
   Semantle's "served" date. Semantle's own responses carry NO
   date-bearing URL, so the ad URL was the probe's ONLY reading
   and the single-value fallback returned it. Now: ad / tracker /
   third-party URLs are ignored entirely at capture (never
   recorded), ad hosts are dropped in provenance (defense in
   depth), and a game with a known first-party host hint gets NO
   reading when no hint host was recorded (no fallback to a lone
   third-party host). The re-key bound + displayed-Game-# check
   still apply to genuine game-host readings.

All tests are synthetic/local: no network, no browser, no CI run.
Typing speed / pacing untouched (no solver timing code changed).
"""
import contextlib
import io
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import runner as R

SRC_RUNNER = (Path(__file__).parent.parent / "runner.py").read_text(
    encoding="utf-8")


def _fn_src(src, start_marker, end_marker):
    start = src.index(start_marker)
    end = src.index(end_marker, start)
    return src[start:end]


# The exact iter-17 DoubleVerify URL (truncated query tail; the
# decisive part is host + dvtagver=dvot_2026-10-14).
DV_URL = (
    "https://tpsc-video-ue.doubleverify.com/visit.js"
    "?flvr=1&brid=97&sup=Google2&uid=1792231500197442"
    "&dvtagver=dvot_2026-10-14_61a062145_ac28f5c"
    "&referrer=about%3Asrcdoc&omcurl=https%3A%2F%2Fsemantle.com%2F"
)


class _FakeAnswers:
    """Stub for runner.A: target_date + per-game engine."""

    def __init__(self, target):
        self._target = target

    def target_date(self):
        return self._target

    def semantle(self, d):
        idx = (d - date(2022, 1, 30)).days
        return {"answer": f"word-{idx}", "puzzle": idx, "date": str(d)}


class _FakeResp:
    def __init__(self, url, status=200):
        self.url = url
        self.status = status


class _FakeProbePage:
    """Stand-in for a Playwright page: only .on("response", fn)."""

    def __init__(self):
        self.handlers = {}

    def on(self, event, fn):
        self.handlers[event] = fn

    def fire(self, url, status=200):
        self.handlers["response"](_FakeResp(url, status))


class _FakeFramedPage:
    """Stand-in for a Playwright page for _framed_reward_ad_state."""

    def __init__(self, url, js_result, frames=None):
        self.url = url
        self._js = js_result
        self.frames = frames or []

    def evaluate(self, js, *a):
        return dict(self._js)


def _ad_state(googletag_slots=(), ad_iframes=(), videos=(), ima=False):
    """The dict the page JS in _framed_reward_ad_state would return."""
    ad_loaded = (any(f.get("adLike") for f in ad_iframes)
                 or bool(videos) or ima)
    return {"overlays": [], "close": [],
            "googletag": {"slots": list(googletag_slots)},
            "ad_iframes": list(ad_iframes), "videos": list(videos),
            "ima": ima, "ad_loaded": ad_loaded}


# ------------------------------------------------------- Semantle probe


class TestSemantleProbeHostFilter(unittest.TestCase):
    TARGET = date(2026, 10, 18)

    def setUp(self):
        self._orig_a = R.A
        self._orig_live = dict(R._LIVE_DAY)
        R.A = _FakeAnswers(self.TARGET)
        R._LIVE_DAY.clear()

    def tearDown(self):
        R.A = self._orig_a
        R._LIVE_DAY.clear()
        R._LIVE_DAY.update(self._orig_live)

    def _install(self):
        page = _FakeProbePage()
        R._install_live_day_probe(page)
        return page

    def test_doubleverify_url_ignored_by_probe(self):
        # The iter-17 vector: the probe must never record the
        # DoubleVerify tag-version date.
        page = self._install()
        page.fire(DV_URL)
        self.assertEqual(R._LIVE_DAY.get("hosts") or {}, {})
        self.assertEqual(R._LIVE_DAY.get("urls") or {}, {})

    def test_dvtagver_param_ignored_on_any_host(self):
        # dvtagver= is an ad marker wherever it appears, even on a
        # host that is not itself on the ad-host list.
        page = self._install()
        page.fire("https://example.com/track.js"
                  "?dvtagver=dvot_2026-10-14_x")
        self.assertEqual(R._LIVE_DAY.get("hosts") or {}, {})

    def test_other_ad_and_tracker_hosts_ignored(self):
        page = self._install()
        for url in (
            "https://x.safeframe.googlesyndication.com/container.html"
            "?d=2026-10-14",
            "https://cm.g.doubleclick.net/partnerpixels?d=2026-10-14",
            "https://www.google-analytics.com/collect?d=2026-10-14",
            "https://googletagmanager.com/gtag/js?d=2026-10-14",
        ):
            page.fire(url)
        self.assertEqual(R._LIVE_DAY.get("hosts") or {}, {})

    def test_game_host_url_accepted(self):
        # The filter must not eat real game-API signals.
        page = self._install()
        page.fire("https://semantle.com/api/word/2026-10-18")
        self.assertEqual(R._LIVE_DAY.get("hosts") or {},
                         {"semantle.com": "2026-10-18"})

    def test_other_games_host_urls_accepted(self):
        # Phoodle / Countryle / Globle date-bearing API hosts are
        # not ad hosts and must keep recording (guard against an
        # over-broad blocklist).
        page = self._install()
        page.fire("https://phoodlebackend.com/daily-word/rest/2026-10-18")
        page.fire("https://countryle.com/hidden-api/"
                  "get-daily-country-valid.php?date=18/10/2026")
        page.fire("https://globle-game.com/answer?day=2026-10-18&list=197")
        hosts = R._LIVE_DAY.get("hosts") or {}
        self.assertEqual(hosts.get("phoodlebackend.com"), "2026-10-18")
        self.assertEqual(hosts.get("countryle.com"), "2026-10-18")
        self.assertEqual(hosts.get("globle-game.com"), "2026-10-18")

    def test_doubleverify_only_provenance_returns_no_date(self):
        # Defense in depth: even an ad-host entry seeded directly
        # (recorded before the capture filter) must never be
        # returned as a served-date reading.
        R._LIVE_DAY["hosts"] = {
            "tpsc-video-ue.doubleverify.com": "2026-10-14"}
        R._LIVE_DAY["urls"] = {
            "tpsc-video-ue.doubleverify.com": DV_URL}
        self.assertEqual(R._live_day_provenance("semantle"),
                         (None, None, None))

    def test_lone_third_party_host_returns_no_date_for_hinted_game(self):
        # The old single-value fallback returned whatever lone host
        # existed — the hole the DoubleVerify URL fell through.
        # A hinted game with no hint host recorded gets NO reading.
        R._LIVE_DAY["hosts"] = {"tracker.example.com": "2026-10-14"}
        R._LIVE_DAY["urls"] = {"tracker.example.com":
                               "https://tracker.example.com/x/2026-10-14"}
        self.assertEqual(R._live_day_provenance("semantle"),
                         (None, None, None))

    def test_hinted_game_host_still_accepted(self):
        # Existing iter-15 behaviour preserved: a genuine
        # game-host reading is still returned.
        R._LIVE_DAY["hosts"] = {"semantle.io": "2026-10-14"}
        R._LIVE_DAY["urls"] = {"semantle.io":
                               "https://semantle.io/api/word/2026-10-14"}
        self.assertEqual(
            R._live_day_provenance("semantle"),
            ("2026-10-14", "semantle.io",
             "https://semantle.io/api/word/2026-10-14"))

    def test_globle_fallback_unchanged(self):
        # Unhinted games (Globle's API host carries no game-name
        # hint) keep the single-value fallback — only the ad-host
        # drop applies to them.
        R._LIVE_DAY["hosts"] = {"globle-game.com": "2026-10-18"}
        R._LIVE_DAY["urls"] = {"globle-game.com":
                               "https://globle-game.com/answer"
                               "?day=2026-10-18"}
        iso, host, _url = R._live_day_provenance("globle")
        self.assertEqual((iso, host), ("2026-10-18", "globle-game.com"))

    def test_align_keeps_target_when_only_ad_url_present(self):
        # End to end for the iter-17 case: the only probe entry is
        # the DoubleVerify URL. The decision must be "no live-day
        # signal, keeping target" — the ad date must never reach
        # the decision, let alone a re-key.
        R._LIVE_DAY["hosts"] = {
            "tpsc-video-ue.doubleverify.com": "2026-10-14"}
        R._LIVE_DAY["urls"] = {
            "tpsc-video-ue.doubleverify.com": DV_URL}
        ans = {"answer": "cry", "puzzle": 1722, "date": "2026-10-18"}
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            out, served = R._align_answer_to_live_day("semantle", ans)
        self.assertIs(out, ans)
        self.assertIsNone(served)
        log = buf.getvalue()
        self.assertIn("no live-day signal", log)
        self.assertNotIn("doubleverify", log)
        self.assertNotIn("2026-10-14", log)

    def test_align_still_refuses_far_jump_from_game_host(self):
        # The ±1 bound still applies to genuine game-host readings.
        R._LIVE_DAY["hosts"] = {"semantle.io": "2026-10-14"}
        R._LIVE_DAY["urls"] = {"semantle.io":
                               "https://semantle.io/api/word/2026-10-14"}
        ans = {"answer": "cry", "puzzle": 1722, "date": "2026-10-18"}
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            out, served = R._align_answer_to_live_day("semantle", ans)
        self.assertIs(out, ans)
        self.assertIsNone(served)
        self.assertIn("RE-KEY REFUSED", buf.getvalue())


# ------------------------------------------------- Framed reward state


class TestFramedRewardClickRegistered(unittest.TestCase):
    _VIDEOREWARD = [{"path": "/21726375739,22803128949/"
                     "ca-video-pub-5781531207509232-tag/"
                     "framed.wtf_1095/videoreward_6812",
                     "sizes": "[object Object]"}]

    def test_click_registered_via_hash_and_slot(self):
        # The iter-17 artifact state: #goog_rewarded + videoreward
        # slot, but no creative ever loaded.
        page = _FakeFramedPage(
            "https://framed.wtf/poster#goog_rewarded",
            _ad_state(googletag_slots=self._VIDEOREWARD))
        st = R._framed_reward_ad_state(page)
        self.assertTrue(st["reward_click_registered"])
        self.assertFalse(st["ad_loaded"])

    def test_click_registered_via_hash_alone(self):
        page = _FakeFramedPage(
            "https://framed.wtf/poster#goog_rewarded",
            _ad_state())
        self.assertTrue(
            R._framed_reward_ad_state(page)["reward_click_registered"])

    def test_click_registered_via_slot_alone(self):
        page = _FakeFramedPage(
            "https://framed.wtf/poster",
            _ad_state(googletag_slots=self._VIDEOREWARD))
        self.assertTrue(
            R._framed_reward_ad_state(page)["reward_click_registered"])

    def test_no_click_when_neither_hash_nor_reward_slot(self):
        page = _FakeFramedPage(
            "https://framed.wtf/poster",
            _ad_state(googletag_slots=[
                {"path": "/12345/framed.wtf_1095/leaderboard",
                 "sizes": "[object Object]"}]))
        self.assertFalse(
            R._framed_reward_ad_state(page)["reward_click_registered"])

    def test_ad_loaded_signal_intact(self):
        # The original ad-load probe is unchanged: an ad-like
        # iframe still reports ad_loaded=True.
        page = _FakeFramedPage(
            "https://framed.wtf/poster",
            _ad_state(ad_iframes=[
                {"src": "https://googleads.g.doubleclick.net/x",
                 "vis": True, "adLike": True}]))
        st = R._framed_reward_ad_state(page)
        self.assertTrue(st["ad_loaded"])

    def test_unlock_timeout_distinguishes_no_fill(self):
        # The timeout path must log click_registered and name the
        # no-fill case, so the artifact proves which side failed.
        src = _fn_src(SRC_RUNNER, "def _framed_try_unlock_poster",
                      "def _framed_open_board_with_unlock")
        self.assertIn("click_registered=", src)
        self.assertIn("site/ad", src)
        self.assertIn("no-fill", src)
        self.assertIn("reward_click_registered", src)

    def test_defer_reserved_for_proven_no_fill(self):
        # The DEFERRED branch in solve_framed_modes fires only when
        # the click registered AND no ad loaded — a click that
        # never registered falls through to the failure branch.
        src = _fn_src(SRC_RUNNER, "def solve_framed_modes",
                      "def solve_semantle")
        self.assertIn('and _ad_ev.get("reward_click_registered")', src)
        self.assertIn("poster DEFERRED", src)
        self.assertIn("site/ad no-fill", src)


if __name__ == "__main__":
    unittest.main()
