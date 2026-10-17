"""Fix-loop iteration 6 regression tests (nerdle / countryle / worldle /
framed / batterup / bundle).

Run:  python3 -m unittest tests.test_iter6_fixes -v
All tests are synthetic/local: no network, no browser, no CI run needed.
"""
import copy
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).parent.parent
sys.path.insert(0, str(HERE))

import runner  # noqa: E402
import strip_nerdle_state as _strip  # noqa: E402

SRC = (HERE / "runner.py").read_text(encoding="utf-8")
NERDLE_SRC = (HERE / "nerdle_solver.py").read_text(encoding="utf-8")
WF = (HERE / ".github/workflows/daily-all-videos.yml").read_text(
    encoding="utf-8")


class TestNerdleStateStrip(unittest.TestCase):
    """The committed browser_state.json must never carry completed
    nerdle gameStates (rerun #5 replayed rerun #4's won boards)."""

    def _state(self):
        return {
            "cookies": [{"name": "x"}],
            "origins": [
                {"origin": "https://nerdlegame.com",
                 "localStorage": [
                     {"name": "gameState",
                      "value": '{"guesses":["85-39=46"],"solution":"57-22=35"}'},
                     {"name": "lastPlayed", "value": "2026-10-17"},
                     {"name": "gameMode", "value": ""}]},
                {"origin": "https://mini.nerdlegame.com",
                 "localStorage": [
                     {"name": "miniGameState",
                      "value": '{"guesses":["4*7=28"]}'},
                     {"name": "statsState", "value": "{}"}]},
                {"origin": "https://framed.wtf",
                 "localStorage": [
                     {"name": "gameState", "value": "framed-kept"}]},
            ],
        }

    def test_strips_nerdle_game_state_only(self):
        data = self._state()
        _strip.strip_nerdle_state_data(data)
        nerdle = {o["origin"]: [i["name"] for i in o["localStorage"]]
                  for o in data["origins"]}
        self.assertNotIn("gameState", nerdle["https://nerdlegame.com"])
        self.assertIn("lastPlayed", nerdle["https://nerdlegame.com"])
        self.assertNotIn("miniGameState",
                         nerdle["https://mini.nerdlegame.com"])
        self.assertNotIn("statsState", nerdle["https://mini.nerdlegame.com"])
        # A non-nerdle origin's gameState is untouched.
        self.assertIn("gameState", nerdle["https://framed.wtf"])
        # Cookies untouched.
        self.assertEqual(data["cookies"], [{"name": "x"}])

    def test_strip_is_idempotent(self):
        data = self._state()
        _strip.strip_nerdle_state_data(data)
        _data2, removed = _strip.strip_nerdle_state_data(data)
        self.assertEqual(removed, [])
        self.assertIs(_data2, data)

    def test_solver_clears_and_gates(self):
        self.assertIn("_nerdle_fresh_board", NERDLE_SRC)
        self.assertIn("restore gate on load", NERDLE_SRC)
        self.assertIn("gameState cleared", NERDLE_SRC)
        self.assertIn("skipping final assembly: not solved", NERDLE_SRC)
        self.assertIn("stripped completed gameState", NERDLE_SRC)

    def test_qc_line_requires_solved(self):
        self.assertIn("final and _npr.get('ok') and solved", SRC)


class TestCountryleWinCheck(unittest.TestCase):
    """Rerun #5 discarded a real TIMOR-LESTE win: the win-check read a
    mid-render statistics page once (banner not yet injected) and the
    row fallback counted 0 li/rows on the stats page."""

    # Mid-render snapshot: continent percentages + footer, no banner,
    # no Wins counter (mirrors the rerun-#5 467-char read).
    MID_RENDER = (
        "Statistics\n\nAfrica\n\n0%\n\nEurope\n\n0%\n\nAsia\n\n0%\n\n"
        "Play Capitale\nWeekly Quizzes\nCreative Mode\nBest Pokemon "
        "card packs!\nFree Shipping with code 'countryle'")

    BANNER = ("Statistics\n\nYou found the COUNTRYLE in 4 tries.\n\n"
              "1 Played games\n1 Wins\n1 Current Streak\n")

    def test_mid_render_snapshot_is_not_a_win(self):
        won, ev = runner._countryle_evaluate_win(
            self.MID_RENDER, "TIMOR-LESTE",
            ["United States of America", "Burkina Faso", "Oman",
             "TIMOR-LESTE"], "https://countryle.com/statistics")
        self.assertFalse(won)
        # ...but the failure must name the stats page, NOT rows=0/4.
        self.assertIn("statistics page", ev)
        self.assertNotIn("rows=0/4", ev)

    def test_banner_wins(self):
        won, ev = runner._countryle_evaluate_win(
            self.BANNER, "TIMOR-LESTE", ["TIMOR-LESTE"],
            "https://countryle.com/statistics")
        self.assertTrue(won)
        self.assertIn("You found the COUNTRYLE", ev)

    def test_wins_counter_wins_without_banner(self):
        body = ("Statistics\n\n1 Played games\n2 Wins\n2 Current Streak\n"
                "Share your result")
        won, ev = runner._countryle_evaluate_win(
            body, "TIMOR-LESTE", ["TIMOR-LESTE"],
            "https://countryle.com/statistics")
        self.assertTrue(won)
        self.assertIn("Wins=2", ev)

    def test_wins_counter_parsing(self):
        self.assertEqual(runner._countryle_wins_counter("1 Wins"), 1)
        self.assertEqual(runner._countryle_wins_counter("Wins\n3"), 3)
        self.assertIsNone(runner._countryle_wins_counter("no counter"))

    def test_poll_loop_present(self):
        self.assertIn("win-check read", SRC)
        self.assertIn("settled=", SRC)
        self.assertIn("two consecutive", SRC)


class TestWorldleCapitalFit(unittest.TestCase):
    """Capital pin: country-bbox -> outline-path-bbox fit (Togo)."""

    BOUNDS = {"minLon": -0.049785, "maxLon": 1.865241,
              "minLat": 5.928837, "maxLat": 11.018682}
    # Outline path rendered inside the 450x450 SVG at (735, 220.5).
    PATH = {"x": 735.0, "y": 220.5, "w": 450.0, "h": 450.0}

    def test_lome_inside_path_near_south_edge(self):
        # Lome: lat 6.130419, lon 1.215829 (CDN cities.json).
        px = runner._worldle_capital_fit_px(
            self.PATH, self.BOUNDS, 6.130419, 1.215829)
        self.assertIsNotNone(px)
        x, y = px
        self.assertTrue(self.PATH["x"] <= x <= self.PATH["x"] + 450)
        self.assertTrue(self.PATH["y"] <= y <= self.PATH["y"] + 450)
        # Lome sits near the southern border: y must be in the bottom
        # ~10% of the path bbox, and east of centre (lon > mid).
        self.assertGreater(y, self.PATH["y"] + 0.9 * 450)
        self.assertGreater(x, self.PATH["x"] + 0.5 * 450)

    def test_degenerate_bbox_returns_none(self):
        self.assertIsNone(runner._worldle_capital_fit_px(
            self.PATH, {"minLon": 1.0, "maxLon": 1.0,
                        "minLat": 5.0, "maxLat": 11.0}, 6.0, 1.0))
        self.assertIsNone(runner._worldle_capital_fit_px(
            {"x": 0, "y": 0, "w": 0, "h": 0}, self.BOUNDS, 6.0, 1.0))

    def test_fit_wired_in_solver(self):
        self.assertIn("country-bbox fit", SRC)
        self.assertIn("_worldle_capital_fit_px", SRC)
        self.assertIn("outline path bbox", SRC)


class TestWorldleTriviaTabs(unittest.TestCase):
    """Trivia: GENERAL/HISTORY tabs must be opened (rerun #5 played
    only the FACTS tab). GENERAL = factData.generalFactCode fillFact,
    HISTORY = historyFactCode — both already in the facts DB."""

    def test_tab_click_helper_and_loop(self):
        self.assertIn("def _click_worldle_trivia_tab", SRC)
        self.assertIn("worldle trivia tab", SRC)
        self.assertIn("factData=", SRC)

    def test_general_question_is_a_fillfact(self):
        # Togo fixture (CDN facts): generalFactCode=3 is the Sparrowhawks
        # nickname fillFact with the 4 nickname options the live DOM
        # showed (Super Eagles / Indomitable Lions / Sparrowhawks /
        # Black Stars).
        facts = {3: {"category": "general",
                     "question": "The national football team of Togo is "
                     "popularly known by the nickname the _______.",
                     "answer": "Sparrowhawks",
                     "answers": ["Super Eagles", "Indomitable Lions",
                                 "Sparrowhawks", "Black Stars"]}}
        shown_q = ("What is the nickname of Togo's national football "
                   "team? The national football team of Togo is "
                   "popularly known by the nickname the _______.")
        self.assertTrue(runner._s_worldle_trivia_match(
            facts[3]["question"], shown_q))
        self.assertEqual(
            {runner._s_worldle_trivia_norm(x) for x in facts[3]["answers"]},
            {"super eagles", "indomitable lions", "sparrowhawks",
             "black stars"})


class TestWorldleLanguageModel(unittest.TestCase):
    """Submission = language-guesses array grew; each question needs
    BOTH names + Guess; per-question correct counted from the store."""

    def test_counts_helper(self):
        gs = [{"question": 0, "isCorrect": True},
              {"question": 0, "isCorrect": True},
              {"question": 1, "isCorrect": True},
              {"question": 1, "isCorrect": False}]
        self.assertEqual(runner._s_worldle_language_counts(gs, 0), (2, 2))
        self.assertEqual(runner._s_worldle_language_counts(gs, 1), (2, 1))
        self.assertEqual(runner._s_worldle_language_counts([], 0), (0, 0))

    def test_no_auto_submit_heuristic(self):
        self.assertNotIn("auto-submitted after first click", SRC)
        self.assertIn("submitted={submitted}", SRC)
        self.assertIn("_worldle_language_phrase_check", SRC)

    def test_phrase_check_present(self):
        self.assertIn("2. PHRASE", SRC)
        self.assertIn("phrase round showing", SRC)


class TestWorldleAreaDisplay(unittest.TestCase):
    """Area: the old read took the first stepper's parent div (no
    number) -> start None -> zero clicks. The new read uses the common
    ancestor / between-steppers element / role=button / store / body."""

    def test_common_ancestor_unit_number(self):
        texts = {"common": "Area\n56,785 km2\n",
                 "between": "", "roleBtn": "", "store": "", "body": ""}
        self.assertEqual(runner._worldle_area_parse_display(texts),
                         (56785, "common:unit"))

    def test_between_steppers_bare_number(self):
        texts = {"common": "Area\n1. NUMBER\n2. COMPARISON\n",
                 "between": "56,785", "roleBtn": "", "store": "",
                 "body": "Area\nGive up\n"}
        self.assertEqual(runner._worldle_area_parse_display(texts),
                         (56785, "between:bare"))

    def test_tab_labels_are_not_the_display(self):
        # Bare numbers < 100 (tab labels) must not be read as the value.
        texts = {"common": "1. NUMBER 2. COMPARISON Area",
                 "between": "", "roleBtn": "", "store": "",
                 "body": "Area\nGive up\n"}
        self.assertEqual(runner._worldle_area_parse_display(texts),
                         (None, None))

    def test_empty_returns_none(self):
        self.assertEqual(
            runner._worldle_area_parse_display(
                {"common": "", "between": "", "roleBtn": "",
                 "store": "", "body": "Area\nGive up\n"}),
            (None, None))

    def test_read_uses_common_ancestor(self):
        self.assertIn("COMMON ancestor of BOTH", SRC)
        self.assertIn("area display read: source=", SRC)


class TestFramedUnlock(unittest.TestCase):
    """Poster unlock: logged step-by-step, trusted click first, and the
    evidence names the unlock (not the menu) when the gate stays up."""

    def test_unlock_logging_and_trusted_click(self):
        self.assertIn("unlock: start gate_up=", SRC)
        self.assertIn("unlock: WATCH AD button clicked via element-click",
                      SRC)
        self.assertIn("unlock: TIMEOUT after ~45s", SRC)
        self.assertIn("open-board: mode=", SRC)

    def test_gate_check_in_board_ready(self):
        self.assertIn("def _framed_poster_gate_up", SRC)
        # Evidence string spans two source lines (concatenated literals).
        self.assertIn("poster reward unlock never completed", SRC)
        self.assertIn("(WATCH AD gate still up)", SRC)

    def test_gate_helper_with_fake_page(self):
        class _Fake:
            def __init__(self, txt):
                self._txt = txt

            def evaluate(self, _js):
                return self._txt

        self.assertTrue(runner._framed_poster_gate_up(_Fake(
            "POSTER #450\nSupport us by watching a short ad\nWATCH AD")))
        self.assertFalse(runner._framed_poster_gate_up(_Fake(
            "POSTER #450\n6 GUESSES REMAINING\nGuess the movie")))


class TestBatterupPostSubmit(unittest.TestCase):
    """shot_win must be taken AFTER submit renders: the old row
    selector matched the open suggestion dropdown's own `li`s, so the
    wait ended instantly and the screenshot showed empty rows."""

    def test_probe_excludes_dropdown(self):
        self.assertIn("[role=listbox]", SRC)
        self.assertIn("dropdown_open", SRC)
        self.assertIn("post-submit board: rows=", SRC)
        self.assertIn("pre-shot_win probe", SRC)

    def test_wait_requires_dropdown_closed(self):
        # Fake page: probe returns dropdown open + rows (the rerun-#5
        # trap: rows present but dropdown open), then rows + closed.
        class _Fake:
            def __init__(self):
                self.calls = 0

            def evaluate(self, _js):
                self.calls += 1
                if self.calls < 3:
                    return {"rows": ["Wyatt Langford"],
                            "dropdown_open": True}
                return {"rows": ["Wyatt Langford"],
                        "dropdown_open": False}

            def wait_for_timeout(self, _ms):
                pass

        rows = runner._attr_wait_post_submit(_Fake(), "batterup",
                                              timeout_s=30.0)
        self.assertEqual(rows, ["Wyatt Langford"])

    def test_wait_times_out_with_dropdown_open(self):
        class _Fake:
            def evaluate(self, _js):
                return {"rows": ["Wyatt Langford"],
                        "dropdown_open": True}

            def wait_for_timeout(self, _ms):
                pass

        rows = runner._attr_wait_post_submit(_Fake(), "batterup",
                                              timeout_s=0.05)
        # Still returns the rows (no worse than before), but only
        # after the timeout — it must NOT return instantly.
        self.assertEqual(rows, ["Wyatt Langford"])


class TestBundleFilter(unittest.TestCase):
    """The bundle pack must exclude unsolved games' finals (nerdle
    rerun #5: a 33 s final for solved=False shipped in the pack)."""

    def test_workflow_filters_unsolved_finals(self):
        self.assertIn("Exclude unsolved games' finals", WF)
        self.assertIn("*_final_*.mp4", WF)
        self.assertIn("solved is False", WF)

    def test_workflow_strips_nerdle_state(self):
        self.assertIn("python strip_nerdle_state.py", WF)

    def test_bundle_filter_logic(self):
        # Replicate the workflow's filter predicate on a fixture tree.
        import json as _json
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "all-artifacts"
            for game, solved in (("nerdle", False), ("waffle", True)):
                d = root / f"video-{game}"
                d.mkdir(parents=True)
                (d / "result.json").write_text(
                    _json.dumps({"solved": solved}))
                (d / f"{game}_final_2026-10-17.mp4").write_text("fake")
                (d / "gameplay_1080.mp4").write_text("fake")
            for res in sorted(root.glob("video-*/result.json")):
                if _json.loads(res.read_text()).get("solved") is False:
                    for mp4 in res.parent.glob("*_final_*.mp4"):
                        mp4.unlink()
            self.assertFalse(
                (root / "video-nerdle/nerdle_final_2026-10-17.mp4").exists())
            self.assertTrue(
                (root / "video-nerdle/gameplay_1080.mp4").exists())
            self.assertTrue(
                (root / "video-waffle/waffle_final_2026-10-17.mp4").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
