"""Fix-loop iteration 42 + 45 Betweenle entry tests.

Iter-42 root cause (run 39299438462): the DAILY WORD entry clicked exactly
once, immediately after navigation, with two permanently-dead selectors
(button:/a: — the element is a SPAN#menupanel-mode-daily) and no wait for
the menu to render. When the lookup raced the render, the bot typed all 5
guesses into the menu.

Iter-45 root cause (run 39374184013, ANALYSIS42): the iter-42 board-open
probe tested ONLY #upperheader's classList. The game's own source
(frontend main.js, header controller `class ns`) removes 'hidden' in
init() — `init(){ this.show() }` — at startup, regardless of the screen
displayed, and the opaque fullscreen #menupanel covers the header while
the menu is up. The probe false-positived on the menu, entry took its
already-open fast path, and 5 guesses went into the void again.

The iter-45 probe therefore decides from RENDERED facts (computed style,
rect, offsetParent, elementFromPoint hit-testing) in Python. The mock
below models the LIVE DOM states — crucially, on the menu #upperheader
has NO 'hidden' class (removed at init) yet the board is NOT open:

  state          header facts                       menu facts     hit
  --------------- ---------------------------------- -------------- -------
  menu (covered) class removed, rect ok, parent ok  rendered       'menu'
  menu (unbuilt) class removed, zero rect, no parent rendered      []
  board open     class removed, rect ok, parent ok  display:none   'header'
  board + help   class removed, rect ok, parent ok  display:none   'help'
  nothing yet    class removed, zero rect           display:none   []

Tests:
(a) _betweenle_board_open is False on the LIVE menu (both sub-states),
    True on a genuinely open board, True when only #helppanel covers it,
    False on probe errors/garbage;
(b) _betweenle_enter_daily does NOT take the fast path on the live menu —
    it clicks DAILY WORD (the run-39374184013 regression);
(c) _betweenle_enter_daily fails fast when the menu never renders;
(d) _betweenle_enter_daily fails fast when clicks never open the board;
(e) _betweenle_enter_daily succeeds when the board opens;
(f) _s_betweenle returns False immediately when entry fails (no typing);
(g) the dead button:/a: selectors are gone from the entry path.
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


def _facts(hidden, display, w, h, offset_parent, visibility="visible"):
    return {"hidden": hidden, "display": display, "visibility": visibility,
            "w": w, "h": h, "offsetParent": offset_parent}


class _MockPage:
    """Simulates the Betweenle LIVE menu/board DOM for entry tests.

    Mirrors betweenle.com: ns.init() removes #upperheader's 'hidden'
    class at startup in EVERY state, so 'hidden' is False for the
    header throughout; what changes is rendering + hit-testing.
    """

    def __init__(self, menu_visible=True, board_opens_on_click=True,
                 header_rendered_on_menu=True, help_covers_board=False):
        self.menu_visible = menu_visible
        self.board_open = False
        self.board_opens_on_click = board_opens_on_click
        self.header_rendered_on_menu = header_rendered_on_menu
        self.help_covers_board = help_covers_board
        self.clicks = []
        self.evaluates = []
        self.daily_word_element = _MockElement(
            visible=True, on_click=self._open_board)

    def _open_board(self):
        if self.board_opens_on_click:
            self.board_open = True
            self.menu_visible = False

    def _board_state(self):
        gone = _facts(True, "none", 0, 0, False)
        if self.board_open:
            header = _facts(False, "grid", 600, 40, True)
            if self.help_covers_board:
                return {"header": header, "menu": gone,
                        "help": _facts(False, "flex", 800, 600, True),
                        "hit": ["help"] * 5}
            return {"header": header, "menu": gone, "help": gone,
                    "hit": ["header"] * 5}
        if self.menu_visible:
            # LIVE menu: class removed at init; the header is laid out
            # (or not built yet) but the opaque menupanel covers the
            # whole game area, so hit-testing lands on the menu.
            if self.header_rendered_on_menu:
                header = _facts(False, "grid", 600, 40, True)
                hit = ["menu"] * 5
            else:
                header = _facts(False, "grid", 0, 0, False)
                hit = []
            return {"header": header,
                    "menu": _facts(False, "flex", 800, 600, True),
                    "help": gone, "hit": hit}
        # Nothing rendered yet (pre-menu): zero rects, no offsetParent.
        return {"header": _facts(False, "grid", 0, 0, False),
                "menu": gone, "help": gone, "hit": []}

    def evaluate(self, js, arg=None):
        self.evaluates.append(js)
        if "betweenle-board-probe" in js:
            return self._board_state()
        if "betweenle-facts" in js:
            st = self._board_state()
            return {"menupanel": st["menu"],
                    "upperheader": st["header"],
                    "helppanel": st["help"]}.get(arg)
        if "return cs.display" in js:
            # _close_betweenle_help computed-style probe: both how-to
            # panels are hidden in the mock world.
            return "none/hidden"
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
        # LIVE menu: #upperheader has NO 'hidden' class (removed at
        # controller init) and is laid out, but the menupanel covers it.
        # The iter-42 classList probe returned True here — the bug.
        page = _MockPage(menu_visible=True)
        self.assertFalse(R._betweenle_board_open(page))

    def test_menu_header_not_rendered_is_not_board_open(self):
        # ANALYSIS42 regression case: #upperheader without 'hidden' but
        # not rendered (zero rect / offsetParent null) -> not open.
        page = _MockPage(menu_visible=True, header_rendered_on_menu=False)
        self.assertFalse(R._betweenle_board_open(page))

    def test_board_open_when_header_visible(self):
        page = _MockPage(menu_visible=False)
        page.board_open = True
        self.assertTrue(R._betweenle_board_open(page))

    def test_board_open_when_only_help_covers_header(self):
        # Board open + the how-to overlay (#helppanel, raised by starting
        # the game) covering every sample point: still open — the entry
        # flow dismisses the overlay before typing.
        page = _MockPage(menu_visible=False, help_covers_board=True)
        page.board_open = True
        self.assertTrue(R._betweenle_board_open(page))

    def test_board_open_false_on_evaluate_error(self):
        class _BadPage:
            def evaluate(self, js, arg=None):
                raise RuntimeError("boom")
        self.assertFalse(R._betweenle_board_open(_BadPage()))

    def test_board_open_false_on_garbage_probe(self):
        # A legacy boolean/garbage probe result must fail closed.
        class _BoolPage:
            def evaluate(self, js, arg=None):
                return True
        self.assertFalse(R._betweenle_board_open(_BoolPage()))

    def test_rendered_pure_facts(self):
        # _betweenle_rendered: the decision table over raw facts.
        self.assertTrue(R._betweenle_rendered(
            _facts(False, "grid", 600, 40, True)))
        self.assertFalse(R._betweenle_rendered(
            _facts(True, "grid", 600, 40, True)))       # hidden class
        self.assertFalse(R._betweenle_rendered(
            _facts(False, "none", 600, 40, True)))      # display:none
        self.assertFalse(R._betweenle_rendered(
            _facts(False, "grid", 0, 40, True)))        # zero width
        self.assertFalse(R._betweenle_rendered(
            _facts(False, "grid", 600, 40, False)))     # no offsetParent
        self.assertFalse(R._betweenle_rendered(
            _facts(False, "grid", 600, 40, True, visibility="hidden")))
        self.assertFalse(R._betweenle_rendered(None))
        self.assertFalse(R._betweenle_rendered({}))


class TestEnterDaily(unittest.TestCase):
    def test_entry_clicks_daily_word_on_live_menu(self):
        # THE run-39374184013 regression: on the live menu the classList
        # probe lied "already open" and DAILY WORD was never clicked.
        # Entry must click it (no fast path) and then verify the board.
        page = _MockPage(menu_visible=True, board_opens_on_click=True)
        self.assertTrue(R._betweenle_enter_daily(page, "betweenle"))
        self.assertTrue(page.daily_word_element.clicked)
        self.assertTrue(page.board_open)

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
        # A GENUINELY open board (menu gone, header hit-testable) may
        # take the fast path: no click needed, no clicks at all.
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
