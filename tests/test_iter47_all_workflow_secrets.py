"""Fix-loop iteration 47 regression tests: YouTube secrets wiring in
daily-all-videos.yml.

Root cause (production, run 39391240996, dispatched publish=true to post
Spotle): the ALL workflow carried the iter-37 publish gate
(top-level env YOUTUBE_PUBLISH) but passed NO YouTube secrets to its
Run-game steps. The Spotle job SOLVED but the uploader found no
credentials: result.json recorded published=false, publish_attempts=0,
last_publish_error='no YouTube credentials'. The batch workflows pass
YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET / YOUTUBE_REFRESH_TOKEN
(from secrets.*) as step env on EVERY game-running step; the ALL
workflow must mirror that exactly, or any future publish=true dispatch
of the ALL workflow silently solves-but-never-publishes again.

Run:  python3 -m unittest tests.test_iter47_all_workflow_secrets -v
All tests are synthetic/local: text-level workflow structure only.
"""
import re
import unittest
from pathlib import Path

WF_DIR = Path(__file__).parent.parent / ".github" / "workflows"

SECRET_LINES = (
    "YOUTUBE_CLIENT_ID: ${{ secrets.YOUTUBE_CLIENT_ID }}",
    "YOUTUBE_CLIENT_SECRET: ${{ secrets.YOUTUBE_CLIENT_SECRET }}",
    "YOUTUBE_REFRESH_TOKEN: ${{ secrets.YOUTUBE_REFRESH_TOKEN }}",
)

GATE_LINE = ("YOUTUBE_PUBLISH: ${{ github.event_name == 'schedule' || "
             "(inputs.publish || '') == 'true' }}")


def _steps(text):
    """Split workflow text into step blocks (steps start with '      - ')."""
    steps, cur = [], None
    for ln in text.splitlines():
        if re.match(r"^      - ", ln):
            if cur is not None:
                steps.append(cur)
            cur = [ln]
        elif cur is not None:
            cur.append(ln)
    if cur is not None:
        steps.append(cur)
    return steps


def _game_steps(text):
    """Steps that actually run a game (invoke runner.py)."""
    return [s for s in _steps(text) if any("runner.py" in l for l in s)]


def _missing_secret_lines(step_lines):
    joined = [l.strip() for l in step_lines]
    return [s for s in SECRET_LINES if s not in joined]


class TestAllWorkflowYouTubeSecrets(unittest.TestCase):

    def test_all_workflow_keeps_publish_gate(self):
        text = (WF_DIR / "daily-all-videos.yml").read_text(encoding="utf-8")
        self.assertIn(GATE_LINE, text)

    def test_all_game_steps_carry_youtube_secrets(self):
        text = (WF_DIR / "daily-all-videos.yml").read_text(encoding="utf-8")
        steps = _game_steps(text)
        # Guard against a vacuous pass: the ALL workflow runs 19 games.
        self.assertEqual(len(steps), 19)
        for idx, step in enumerate(steps):
            missing = _missing_secret_lines(step)
            self.assertEqual(
                missing, [],
                f"daily-all-videos.yml game step #{idx + 1} is missing "
                f"YouTube secret env: {missing} (uploader would find no "
                f"credentials and silently skip publishing)")

    def test_batch_workflows_still_carry_secrets(self):
        """Parity reference: the batch workflows are the pattern the ALL
        workflow mirrors; every game step there must keep its secrets."""
        for name, expected in (
                ("daily-batch-a-dawn.yml", 18),
                ("daily-batch-a-9pm.yml", 15),
                ("daily-batch-b-marveldle.yml", 1),
                ("daily-batch-c-1030pm.yml", 1)):
            text = (WF_DIR / name).read_text(encoding="utf-8")
            steps = _game_steps(text)
            self.assertEqual(len(steps), expected, name)
            for idx, step in enumerate(steps):
                self.assertEqual(
                    _missing_secret_lines(step), [],
                    f"{name} game step #{idx + 1} lost its YouTube secrets")


if __name__ == "__main__":
    unittest.main()
