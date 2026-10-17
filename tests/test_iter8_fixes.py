"""Fix-loop iteration 8 regression tests (hollow-success skip).

Run 39029436203 (iter7 rerun, dispatched without force=true) hit the
no-duplicate guard for all 18 games and exited 0 writing NOTHING to
videos/{gid}/ — every artifact was a ~270-byte zip holding only the START
debug line, yet the run concluded success. A skip that claims success must
leave an auditable videos/{gid}/result.json with skipped=true.

Run:  python3 -m unittest tests.test_iter8_fixes -v
All tests are synthetic/local (HERE is monkeypatched to a temp dir; no
browser, no network, no secrets).
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent.parent
sys.path.insert(0, str(HERE))

import answers as A  # noqa: E402
import runner  # noqa: E402


class TestSkipLeavesAuditableResult(unittest.TestCase):
    """A no-duplicate skip writes videos/{gid}/result.json (skipped=true)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_here = runner.HERE
        runner.HERE = Path(self._tmp.name)
        self.date_key = A.target_date().isoformat()
        self.record = {
            "game": "waffle", "date": self.date_key, "solved": True,
            "evidence": "earlier real solve",
            "video": "/home/runner/work/daily-games/daily-games/videos/waffle/waffle_final_2026-10-17.mp4",
        }
        d = runner.HERE / "database" / self.date_key / "waffle"
        d.mkdir(parents=True)
        (d / "result.json").write_text(json.dumps(self.record), encoding="utf-8")

    def tearDown(self):
        runner.HERE = self._old_here
        # _dbg caches open file handles per gid; close the test one so the
        # temp dir can be removed cleanly on all platforms.
        fh = runner._DBG_FH.pop("waffle", None)
        if fh is not None:
            try:
                fh.close()
            except Exception:
                pass
        self._tmp.cleanup()

    def test_db_guard_still_finds_record(self):
        self.assertEqual(runner._db_already_done(self.date_key, "waffle"), self.record)

    def test_write_skip_result(self):
        runner._write_skip_result("waffle", self.date_key, self.record)
        out = json.loads((runner.HERE / "videos" / "waffle" / "result.json").read_text(encoding="utf-8"))
        self.assertTrue(out["solved"])
        self.assertTrue(out["skipped"])
        self.assertIn("skipped", out["evidence"])
        self.assertEqual(out["video"], self.record["video"])
        self.assertEqual(out["date"], self.date_key)

    def test_run_one_skip_path_writes_result(self):
        res = runner.run_one("waffle")
        self.assertTrue(res["solved"])
        self.assertIn("skipped", res["evidence"])
        out = json.loads((runner.HERE / "videos" / "waffle" / "result.json").read_text(encoding="utf-8"))
        self.assertTrue(out["skipped"])
        self.assertEqual(out["video"], self.record["video"])

    def test_skip_does_not_touch_database_record(self):
        runner.run_one("waffle")
        kept = json.loads(
            (runner.HERE / "database" / self.date_key / "waffle" / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(kept, self.record)


if __name__ == "__main__":
    unittest.main()
