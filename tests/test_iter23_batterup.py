"""Fix-loop iteration 23 tests — Batterup publish proof (P1, ANALYSIS20 §4).

The iter-22 evidence exposed the defect: a 403 with the identical
111-byte body / same sha256 for BOTH 2026-10-18 and 2026-10-19 (a date
that cannot be published yet) proves the 403 carries zero information
about publication on its own. Traced live 2026-10-18: the 403 body is
S3's standard missing-key XML, NOT a bot-block — the same client +
headers get 200 + a real archive for 2026-10-17, and browser-identical
site-context headers (Referer/Origin/Accept/Sec-Fetch-*) change nothing.
So there is no header-based site-context fetch to replicate; the fix is
a CONTROL: a negative verdict ("not published yet") is only reported
when the fetch path is PROVEN working (a parsed target archive, or a
known-published control file D-1/D-2). When NO fetch returns a
parseable archive, the defer reason is "unverified (fetch blocked)" —
never "not published yet". Safe behaviour is unchanged in both cases
(raise SystemExit, no video, solved=false, qc pending).

All tests are synthetic/local: no network (requests.get is mocked).
Run:  python3 -m unittest tests.test_iter23_batterup -v
"""

import json
import unittest
from datetime import date
from unittest import mock

import answers as ANS


class _FakeResp:
    def __init__(self, status=200, body=b"{}", server_date=None):
        self.status_code = status
        self.content = body
        self.headers = {"Date": server_date or "Sun, 18 Oct 2026 02:09:44 GMT"}
        self.ok = 200 <= status < 300
        self.text = body.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.text)


_S3_DENIED = (b'<?xml version="1.0" encoding="UTF-8"?>\n<Error>'
              b'<Code>AccessDenied</Code><Message>Access Denied</Message>'
              b'</Error>')


def _body(games):
    return json.dumps({"games": games}).encode("utf-8")


def _entry(game_date, name="Some Player"):
    return {"game_date": game_date, "player_name": name, "game_number": 1}


def _route(mapping):
    """requests.get mock routing by the date embedded in the URL."""
    def _get(url, **kw):
        for key, resp in mapping.items():
            if f"games_batterup{key}.json" in url:
                return resp() if callable(resp) else resp
        raise AssertionError(f"unexpected URL {url}")
    return _get


class TestBatterupPublishProof(unittest.TestCase):
    DAY = date(2026, 10, 18)

    def test_identical_block_fingerprint_is_unverified(self):
        # ANALYSIS20 §4 exact shape: target + control share the
        # identical 403 / 111-byte / same-sha fingerprint -> the
        # fetch cannot distinguish unpublished from blocked, so the
        # defer must say "unverified (fetch blocked)", never
        # "not published yet".
        denied = _FakeResp(status=403, body=_S3_DENIED)
        self.assertEqual(len(_S3_DENIED), 111)
        mapping = {"2026-10-18": denied, "2026-10-19": denied,
                   "2026-10-17": denied, "2026-10-16": denied}
        with mock.patch("requests.get", side_effect=_route(mapping)):
            with self.assertRaises(SystemExit) as cm:
                ANS.batterup(self.DAY)
        msg = str(cm.exception)
        self.assertIn("unverified (fetch blocked)", msg)
        self.assertNotIn("not published yet", msg)
        self.assertIn("status=403", msg)
        self.assertIn("games_batterup2026-10-17.json", msg)  # control fetched

    def test_control_proves_unpublished(self):
        # Target files 403, but the known-published control (D-1)
        # returns 200 + its own entry -> the fetch path works, so
        # the target 403s genuinely mean unpublished.
        denied = _FakeResp(status=403, body=_S3_DENIED)
        control = _FakeResp(status=200,
                            body=_body([_entry("2026-10-17", "Wyatt Langford")]))
        mapping = {"2026-10-18": denied, "2026-10-19": denied,
                   "2026-10-17": control}
        seen = []

        def _get(url, **kw):
            seen.append(url)
            return _route(mapping)(url, **kw)

        with mock.patch("requests.get", side_effect=_get):
            with self.assertRaises(SystemExit) as cm:
                ANS.batterup(self.DAY)
        msg = str(cm.exception)
        self.assertIn("not published yet", msg)
        self.assertNotIn("unverified", msg)
        self.assertIn("control=2026-10-17 archive ok", msg)
        self.assertIn("games_batterup2026-10-17.json", seen[-1])

    def test_parsed_target_archive_needs_no_control(self):
        # A target file that returns a parseable archive already
        # proves the path works: the missing target entry is
        # genuinely unpublished, with NO extra control fetches.
        body = _body([_entry("2026-10-17")])
        seen = []

        def _get(url, **kw):
            seen.append(url)
            return _FakeResp(status=200, body=body)

        with mock.patch("requests.get", side_effect=_get):
            with self.assertRaises(SystemExit) as cm:
                ANS.batterup(self.DAY)
        msg = str(cm.exception)
        self.assertIn("not published yet", msg)
        self.assertEqual(len(seen), 2)
        self.assertTrue(all("2026-10-17" not in u and "2026-10-16" not in u
                            for u in seen))

    def test_published_day_still_returns_player(self):
        body = _body([_entry("2026-10-18", "Star Player")])
        with mock.patch("requests.get",
                        return_value=_FakeResp(body=body)):
            out = ANS.batterup(self.DAY)
        self.assertEqual(out["answer"], "Star Player")
        self.assertEqual(out["via"], "cdn")

    def test_all_fetch_exceptions_are_unverified(self):
        def _get(url, **kw):
            raise ConnectionError("boom")

        with mock.patch("requests.get", side_effect=_get):
            with self.assertRaises(SystemExit) as cm:
                ANS.batterup(self.DAY)
        msg = str(cm.exception)
        self.assertIn("unverified (fetch blocked)", msg)
        self.assertNotIn("not published yet", msg)
        self.assertIn("fetch_failed=boom", msg)

    def test_control_without_own_entry_does_not_validate(self):
        # A 200 control archive that does NOT carry its own date's
        # entry proves nothing about publication readability: fall
        # through to D-2, and if that fails too -> unverified.
        denied = _FakeResp(status=403, body=_S3_DENIED)
        hollow = _FakeResp(status=200, body=_body([_entry("2026-10-15")]))
        mapping = {"2026-10-18": denied, "2026-10-19": denied,
                   "2026-10-17": hollow, "2026-10-16": denied}
        with mock.patch("requests.get", side_effect=_route(mapping)):
            with self.assertRaises(SystemExit) as cm:
                ANS.batterup(self.DAY)
        msg = str(cm.exception)
        self.assertIn("unverified (fetch blocked)", msg)
        self.assertIn("games_batterup2026-10-16.json", msg)  # D-2 tried

    def test_same_headers_for_control_and_target(self):
        # No site-context header spoofing: control and target are
        # fetched with the IDENTICAL headers. Verified live that
        # browser-identical Referer/Origin/Accept headers change
        # nothing (200 stays 200, 403 stays 403), so the code must
        # not pretend a different header set is the site's path.
        denied = _FakeResp(status=403, body=_S3_DENIED)
        control = _FakeResp(status=200,
                            body=_body([_entry("2026-10-17")]))
        mapping = {"2026-10-18": denied, "2026-10-19": denied,
                   "2026-10-17": control}
        seen_headers = []

        def _get(url, **kw):
            seen_headers.append(kw.get("headers"))
            return _route(mapping)(url, **kw)

        with mock.patch("requests.get", side_effect=_get):
            with self.assertRaises(SystemExit):
                ANS.batterup(self.DAY)
        self.assertEqual(len(seen_headers), 3)
        self.assertTrue(all(h == seen_headers[0] for h in seen_headers))
        self.assertEqual(seen_headers[0],
                         {"User-Agent": "WordSolverX-batterup-daily/1.0"})


if __name__ == "__main__":
    unittest.main()
