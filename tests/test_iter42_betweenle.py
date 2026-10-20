"""Fix-loop iteration 42 Betweenle entry tests.

Root cause (run 39299438462): the DAILY WORD entry clicked exactly once,
immediately after navigation, with two permanently-dead selectors
(button:/a: — the element is a SPAN#menupanel-mode-daily) and no wait for
the menu to render. When the lookup raced the render, the bot typed all 5
guesses into the menu (WARN at 18:05:31, menu in every frame, counters
stuck at the hidden panel's 'Guess 1 / 12', stats='WORD').

The fix: wait for the menu, click the real element, VERIFY the board
opened, retry, and FAIL FAST (no guesses typed) if it won't open.

Tests:
(a) _betweenle_board_open distinguishes menu from board via #upperheader;
(b) _betweenle_enter_daily fails fast when the menu never renders;
(c) _betweenle_enter_daily fails fast when clicks never open the board;
(d) _betweenle_enter_daily succeeds when the board opens;
(e) _s_betweenle returns False immediately when entry fails (no typing);
(f) the dead button:/a: selectors are gone from the entry path.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

import runner as R


class _MockElement:
    def __init__(self, visible=True, on_click=None):
        self._visible = visible
        self.clicked = False
        self._on_click = on_click

    def is_visible(self):
        return self._visible

    def bounding_box(self):
        return {"x": 100, "y": 200, "width": 300, "height": 60}

    def click(self, timeout=None):
        self.clicked = True
        if self._on_click:
            self._on_click()


class _MockPage:
    """Simulates the Betweenle menu/board DOM for entry tests."""

    def __init__(self, menu_visible=True, board_opens_on_click=True):
        self.menu_visible = menu_visible
        self.board_open = False
        self.board_opens_on_click = board_opens_on_click
        self.clicks = []
        self.evaluates = []
        self.daily_word_element = _MockElement(
            visible=True, on_click=self._open_board)

    def _open_board(self):
        if self.board_opens_on_click:
            self.board_open = True
            self.menu_visible = False

    def evaluate(self, js, arg=None):
        self.evaluates.append(js)
        if "menupanel" in js and "classList" in js:
            return self.menu_visible
        if "upperheader" in js and "classList" in js:
            return self.board_open
        return None

    def query_selector(self, sel):
        if sel == "#menupanel-mode-daily":
            return self.daily_word_element if self.menu_visible else None
        return None

    @property
    def mouse(self):
        return self

    def click(self, x, y):
        self.clicks.append((x, y))
        # Only a click on the DAILY WORD button opens the board (its bbox
        # is 100,200,300,60); clicks elsewhere (e.g. help-panel X) do not.
        if 100 <= x <= 400 and 200 <= y <= 260:
            self._open_board()

    def wait_for_timeout(self, ms):
        pass


class TestBoardOpen(unittest.TestCase):
    def test_menu_is_not_board_open(self):
        # Menu state: #upperheader has the hidden class.
        page = _MockPage(menu_visible=True)
        self.assertFalse(R._betweenle_board_open(page))

    def test_board_open_when_header_visible(self):
        page = _MockPage(menu_visible=False)
        page.board_open = True
        self.assertTrue(R._betweenle_board_open(page))

    def test_board_open_false_on_evaluate_error(self):
        class _BadPage:
            def evaluate(self, js, arg=None):
                raise RuntimeError("boom")
        self.assertFalse(R._betweenle_board_open(_BadPage()))


class TestEnterDaily(unittest.TestCase):
    def test_fail_fast_when_menu_never_renders(self):
        # Menu never becomes visible: entry must give up, not type.
        page = _MockPage(menu_visible=False)
        self.assertFalse(R._betweenle_enter_daily(page, "betweenle"))
        self.assertEqual(page.clicks, [])

    def test_fail_fast_when_click_never_opens_board(self):
        # DAILY WORD clickable but the board never opens: fail fast.
        page = _MockPage(menu_visible=True, board_opens_on_click=False)
        self.assertFalse(R._betweenle_enter_daily(page, "betweenle"))
        # Clicks were attempted (entry tried), but board never opened.
        self.assertTrue(page.daily_word_element.clicked)

    def test_success_when_board_opens(self):
        page = _MockPage(menu_visible=True, board_opens_on_click=True)
        self.assertTrue(R._betweenle_enter_daily(page, "betweenle"))
        self.assertTrue(page.board_open)
        self.assertTrue(page.daily_word_element.clicked)

    def test_already_open_skips_entry(self):
        page = _MockPage(menu_visible=False)
        page.board_open = True
        self.assertTrue(R._betweenle_enter_daily(page, "betweenle"))
        self.assertFalse(page.daily_word_element.clicked)
        self.assertEqual(page.clicks, [])


class TestSolveFailsFast(unittest.TestCase):
    def test_s_betweenle_returns_false_when_entry_fails(self):
        # Entry failure must abort before any guess is typed.
        page = _MockPage(menu_visible=False)
        with mock.patch.object(R, "_close_modals"), \
             mock.patch.object(R, "_betweenle_enter_daily", return_value=False):
            ok, ev = R._s_betweenle(page, {"answer": "ABOUT"}, "betweenle")
        self.assertFalse(ok)
        self.assertIn("did not open", ev)
        self.assertEqual(page.clicks, [])


class TestDeadSelectorsGone(unittest.TestCase):
    def test_no_button_or_anchor_daily_word_selector(self):
        import inspect
        src = inspect.getsource(R._betweenle_enter_daily)
        self.assertNotIn("button:has-text('DAILY WORD')", src)
        self.assertNotIn("a:has-text('DAILY WORD')", src)
        self.assertIn("#menupanel-mode-daily", src)


if __name__ == "__main__":
    unittest.main()
