"""Fix-loop iteration 43 tests — Spotle pool capture (production fix).

Production failure (run 39367828267): the date gate passed, but the
solver failed 'artist pool unavailable' — capture returned 'non-pool
JSON' 4/4. Two root causes, both reproduced here from the LIVE chunk
layout fetched 2026-10-20:

1. SELECTION: `_SPOTLE_POOL_JS` took the FIRST `JSON.parse(`...`)`
   match in the first chunk that merely mentioned `listener_rank`.
   The pool is a lazily-imported data chunk
   (`var e=JSON.parse(`<pool>`); export{e as default}`); its position
   in resource iteration order is not guaranteed, and other chunks
   carry non-pool JSON.parse payloads. Fix: collect ALL payloads
   across ALL immutable chunks; `_spotle_pick_pool` selects the one
   that parses to >=100 artist-shaped entries.

2. ESCAPING (the actual parse killer): the regex-extracted payload is
   the template literal's SOURCE text. The bundler escapes literal
   backslashes as `\\`, so entries like `"Weird Al" Yankovic`
   (listener_rank 1084 of the live pool) appear in the source as
   `\\"Weird Al\\" Yankovic` and `json.loads` on the raw source text
   fails (Expecting ',' delimiter). Fix: `_spotle_parse_pool` first
   applies `_spotle_tpl_unescape` (only the bundler-emitted escapes:
   `\\`, \` and `\${`), then json.loads.

All tests are synthetic/local — no browser, no network.

Run:  python3 -m unittest tests.test_iter43_spotle_pool -v
"""

import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import runner as R


# ---------------------------------------------------------------- fixtures

def _pool_entries(n=150):
    entries = []
    for i in range(n):
        entries.append({
            "listener_rank": i,
            "artist": f"Artist {i}",
            "country": "us",
            "genre": "Pop",
            "gender": "m" if i % 2 else "f",
            "group_size": 1,
            "debut_album_year": 1990 + (i % 30),
            "image_uri": "https://example.invalid/img.jpg",
        })
    # The live-pool shape that broke production parsing: an artist
    # name with embedded double quotes (live: rank 1084).
    entries.append({
        "listener_rank": n,
        "artist": '"Weird Al" Yankovic',
        "country": "us",
        "genre": "Pop",
        "gender": "m",
        "group_size": 1,
        "debut_album_year": 1983,
        "image_uri": "https://example.invalid/weird.jpg",
    })
    return entries


def _pool_json(n=150):
    return json.dumps(_pool_entries(n))


def _tpl_escape(json_text):
    """Mirror the bundler: JSON text -> template-literal source text."""
    return (json_text.replace("\\", "\\\\")
                     .replace("`", "\\`")
                     .replace("${", "\\${"))


def _chunk(*payload_sources):
    return "".join("var v%d=JSON.parse(`%s`);" % (i, p)
                   for i, p in enumerate(payload_sources))


def _extract(chunk_text, url):
    """Python mirror of _SPOTLE_POOL_JS candidate extraction."""
    out = []
    for i, m in enumerate(re.finditer(r"JSON\.parse\(`([\s\S]*?)`\)",
                                      chunk_text)):
        if m.group(1) and len(m.group(1)) > 1000:
            out.append({"u": url, "i": i, "t": m.group(1)})
    return out


def _big_non_pool_payload():
    """A >1000-char JSON.parse payload that is NOT the artist pool
    (e.g. a site config/archive block in another chunk)."""
    return json.dumps([{"key": "cfg-%d" % i, "value": "x" * 20}
                       for i in range(150)])


# ---------------------------------------------------------------- unescape

class TestTplUnescape(unittest.TestCase):
    def test_roundtrip_escaped_pool_parses(self):
        raw_source = _tpl_escape(_pool_json())
        # The raw source text (what the old code fed json.loads) is
        # NOT valid JSON — this is the production failure shape.
        with self.assertRaises(Exception):
            json.loads(raw_source)
        pool = R._spotle_parse_pool(raw_source, "spotle")
        self.assertEqual(len(pool), 151)
        names = {p["artist"] for p in pool}
        self.assertIn('"Weird Al" Yankovic', names)

    def test_unescape_is_identity_for_plain_json(self):
        plain = _pool_json()
        self.assertEqual(R._spotle_tpl_unescape(plain), plain)
        self.assertEqual(len(R._spotle_parse_pool(plain, "spotle")), 151)

    def test_unescape_leaves_json_escapes_to_json_loads(self):
        # A lone \n inside a JSON string is a JSON escape, not a
        # template-literal escape: the unescaper must not eat it.
        s = '[{"artist": "a\\nb"}]'
        self.assertEqual(R._spotle_tpl_unescape(s), s)

    def test_unescape_handles_interpolation_and_backtick(self):
        src = _tpl_escape(json.dumps([{"a": "${x}`y"}]))
        self.assertEqual(json.loads(R._spotle_tpl_unescape(src)),
                         [{"a": "${x}`y"}])


# ---------------------------------------------------------------- selection

class TestPickPool(unittest.TestCase):
    def test_first_match_not_pool_production_shape(self):
        """Multi-payload chunk: FIRST JSON.parse is a big non-pool
        block, the pool is the SECOND match — the old code returned
        the first and failed. Pick must skip it and take the pool."""
        chunk = _chunk(_big_non_pool_payload(), _pool_json())
        cands = _extract(chunk, "https://x/_app/immutable/chunks/a.js")
        self.assertEqual(len(cands), 2)
        pool, winner = R._spotle_pick_pool(cands, "spotle")
        self.assertEqual(len(pool), 151)
        self.assertEqual(winner[0], "https://x/_app/immutable/chunks/a.js")
        self.assertEqual(winner[1], 1)   # second match won
        self.assertEqual(winner[2], 151)

    def test_pool_in_later_chunk(self):
        """Pool only in a later chunk; earlier chunks carry only
        non-pool payloads (the lazy-import layout of the live site)."""
        cands = _extract(_chunk(_big_non_pool_payload()),
                         "https://x/_app/immutable/chunks/first.js")
        cands += _extract(_chunk(_tpl_escape(_pool_json())),
                          "https://x/_app/immutable/chunks/pool.js")
        pool, winner = R._spotle_pick_pool(cands, "spotle")
        self.assertEqual(len(pool), 151)
        self.assertEqual(winner[0],
                         "https://x/_app/immutable/chunks/pool.js")
        self.assertEqual(winner[1], 0)

    def test_escaped_pool_selected_despite_earlier_valid_json(self):
        """An earlier VALID-JSON non-pool payload must not shadow the
        escaped pool payload (old code also failed this way: the pool
        payload itself was 'non-pool' to a raw json.loads)."""
        cands = [
            {"u": "a", "i": 0, "t": _big_non_pool_payload()},
            {"u": "b", "i": 0, "t": _tpl_escape(_pool_json())},
        ]
        pool, winner = R._spotle_pick_pool(cands, "spotle")
        self.assertEqual(len(pool), 151)
        self.assertEqual(winner[0], "b")

    def test_all_garbage_yields_no_pool(self):
        cands = [{"u": "a", "i": 0, "t": "{not json"},
                 {"u": "a", "i": 1, "t": _big_non_pool_payload()}]
        pool, winner = R._spotle_pick_pool(cands, "spotle")
        self.assertEqual(pool, [])
        self.assertIsNone(winner)

    def test_empty_candidates(self):
        self.assertEqual(R._spotle_pick_pool([], "spotle"), ([], None))
        self.assertEqual(R._spotle_pick_pool(None, "spotle"), ([], None))

    def test_legacy_bare_string_candidate(self):
        """The pre-iter-43 capture contract returned a bare payload
        string; _spotle_pick_pool must accept it (the iter-41 fake
        page returns evaluate() -> json.dumps(pool))."""
        pool, winner = R._spotle_pick_pool(
            _tpl_escape(_pool_json()), "spotle")
        self.assertEqual(len(pool), 151)


# ---------------------------------------------------------------- capture

class _FakePage:
    def __init__(self, evaluate_result):
        self._result = evaluate_result
        self.reloads = 0

    def evaluate(self, js):
        return self._result

    def reload(self, wait_until=None):
        self.reloads += 1


class TestCapturePool(unittest.TestCase):
    def test_capture_returns_pool_from_candidates(self):
        cands = [{"u": "u1", "i": 0, "t": _tpl_escape(_pool_json())}]
        page = _FakePage(cands)
        pool = R._spotle_capture_pool(page, "spotle")
        self.assertEqual(len(pool), 151)
        self.assertEqual(page.reloads, 0)

    def test_capture_defers_when_no_pool_candidate(self):
        page = _FakePage([])
        self.assertEqual(R._spotle_capture_pool(page, "spotle"), [])

    def test_capture_defers_when_evaluate_raises(self):
        class _Boom:
            def evaluate(self, js):
                raise RuntimeError("no page")

            def reload(self, wait_until=None):
                raise RuntimeError("no page")

        self.assertEqual(R._spotle_capture_pool(_Boom(), "spotle"), [])


if __name__ == "__main__":
    unittest.main()
