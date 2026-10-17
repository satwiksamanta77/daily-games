"""Strip completed Nerdle game state from a Playwright storage-state file.

Fix-loop iteration 6: the committed browser_state.json carried rerun #4's
COMPLETED nerdle gameStates, and rerun #5's solver loaded them as storage
state and replayed into already-won boards (typed 0, UNPROVEN guard fired).
A storage-state file must never be committed with completed nerdle
gameStates: this strips the game-state keys (gameState, miniGameState,
statsState, deviceStatsState, timer) from every nerdlegame.com origin in
the file, leaving cookies / other origins / other keys untouched.

Usage:  python strip_nerdle_state.py [path/to/browser_state.json]
Default path: browser_state.json next to this script. Safe to run when the
file is absent or already clean (no-op, exit 0). Pure stdlib.
"""
import json
import sys
from pathlib import Path

NERDLE_STATE_KEYS = ("gameState", "miniGameState", "statsState",
                     "deviceStatsState", "timer")


def strip_nerdle_state_data(data):
    """Remove nerdle game-state keys from a parsed storage-state dict.

    Returns (stripped_data, removed) where removed is a list of
    "origin:key" entries that were deleted. The input dict is mutated
    in place and also returned.
    """
    removed = []
    if not isinstance(data, dict):
        return data, removed
    for origin in (data.get("origins") or []):
        if not isinstance(origin, dict):
            continue
        if "nerdlegame.com" not in str(origin.get("origin") or ""):
            continue
        for item in list(origin.get("localStorage") or []):
            name = (item or {}).get("name")
            if name in NERDLE_STATE_KEYS:
                origin["localStorage"].remove(item)
                removed.append(f"{origin.get('origin')}:{name}")
    return data, removed


def strip_nerdle_state_file(path):
    """Strip nerdle game state from the storage-state file at `path`.

    Returns the list of removed "origin:key" entries ([] when the file
    is absent, unparseable, or already clean). Never raises.
    """
    try:
        p = Path(path)
        if not p.exists():
            return []
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    _data, removed = strip_nerdle_state_data(data)
    if removed:
        try:
            # Compact (no indent): matches the committed file's format
            # (one line), so a strip does not reformat the whole file.
            p.write_text(json.dumps(data), encoding="utf-8")
        except Exception:
            return []
    return removed


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    target = Path(argv[0]) if argv else Path(__file__).parent / "browser_state.json"
    removed = strip_nerdle_state_file(target)
    print(f"strip_nerdle_state: {target} removed={removed or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
