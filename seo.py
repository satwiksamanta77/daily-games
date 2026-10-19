"""YouTube SEO builders for daily-games (iteration 37).

Ported pattern: the YouTube parts ONLY of the Wordle repo
(0xSatwik/word-ele-video-automation) — title/description/tags,
thumbnail curiosity gap, upload + post-upload enrichment. No
gameplay, popup, mouse/stealth, delay, or social-sharing code.

Pure functions: no network, no runner imports, no fabrication.
Every optional block (yesterday recap, tomorrow teaser, analysis)
is rendered ONLY when the caller supplies verified data; when the
caller passes None the block is omitted, never invented.
"""

TITLE_MAX = 100
DESCRIPTION_MAX = 5000
TAGS_TOTAL_MAX = 500


# Canonical answer-today URLs for cross-linking (same verified pattern
# https://wordsolverx.com/<slug> as the runner's GAMES table).
# Used by callers that do not have the runner's GAMES table
# (e.g. nerdle_solver); the runner builds its own list from GAMES.
DEFAULT_CROSS_LINKS = [
    ("Betweenle Answer Today",
     "https://wordsolverx.com/betweenle-answer-today"),
    ("Colordle Answer Today",
     "https://wordsolverx.com/colordle-answer-today"),
    ("Colorfle Answer Today",
     "https://wordsolverx.com/colorfle-answer-today"),
    ("Framed Answer Today",
     "https://wordsolverx.com/framed-answer-today"),
    ("Searchle Answer Today",
     "https://wordsolverx.com/searchle-answer-today"),
    ("Semantle Answer Today",
     "https://wordsolverx.com/semantle-answer-today"),
    ("Phrazle Answer Today",
     "https://wordsolverx.com/phrazle-answer-today"),
    ("Canuckle Answer Today",
     "https://wordsolverx.com/canuckle-answer-today"),
    ("Phoodle Answer Today",
     "https://wordsolverx.com/phoodle-answer-today"),
    ("Contexto Answer Today",
     "https://wordsolverx.com/contexto-answer-today"),
    ("Worldle Answer Today",
     "https://wordsolverx.com/worldle-answer-today"),
    ("Globle Answer Today",
     "https://wordsolverx.com/globle-answer-today"),
    ("Waffle Answer Today",
     "https://wordsolverx.com/waffle-answer-today"),
    ("Worgle Answer Today",
     "https://wordsolverx.com/worgle-answer-today"),
    ("Countryle Answer Today",
     "https://wordsolverx.com/countryle-answer-today"),
    ("Batterup Answer Today",
     "https://wordsolverx.com/batterup-answer-today"),
    ("Marveldle Answer Today",
     "https://wordsolverx.com/marveldle-answer-today"),
    ("Nerdle Answer Today",
     "https://wordsolverx.com/nerdle-answer-today"),
    ("Spotle Answer Today",
     "https://wordsolverx.com/spotle-answer-today"),
]


def format_chapter_timestamp(sec):
    """Seconds -> 'M:SS' chapter timestamp (YouTube auto-links these)."""
    sec = max(0, int(sec))
    return f"{sec // 60}:{sec % 60:02d}"


def _title_variants(game, date_short, guesses, puzzle_number, no_hints=False):
    """Three rotating title variants for one game.

    Every variant leads with the exact head keyword
    '{Game} Answer Today' (well inside the first ~35-40 chars for
    every game name). Puzzle # and guess count are included when
    known; the variants degrade gracefully when either is missing.
    no_hints (iter-39): the video carries no hints segment (Colorfle) —
    the "Hints & Solution" fallback becomes plain "Solution".
    """
    head = f"{game} Answer Today"
    if puzzle_number is not None and guesses is not None:
        return [
            f"{head} \u2014 #{puzzle_number} Solved in {guesses} Guesses ({date_short})",
            f"{head} ({date_short}) \u2014 Puzzle #{puzzle_number} Solved!",
            f"{head} \u2014 {date_short} Puzzle #{puzzle_number} \u00b7 {guesses} Guesses",
        ]
    if puzzle_number is not None:
        return [
            f"{head} \u2014 #{puzzle_number} {'Solution' if no_hints else 'Hints & Solution'} ({date_short})",
            f"{head} ({date_short}) \u2014 Puzzle #{puzzle_number} Solved!",
            f"{head} \u2014 {date_short} Puzzle #{puzzle_number}",
        ]
    if guesses is not None:
        return [
            f"{head} \u2014 Solved in {guesses} Guesses ({date_short})",
            f"{head} ({date_short}) \u2014 Solved in {guesses} Guesses!",
            f"{head} \u2014 {date_short} \u00b7 {guesses} Guesses",
        ]
    return [
        f"{head} \u2014 {'Solution' if no_hints else 'Hints & Solution'} ({date_short})",
        f"{head} ({date_short}) \u2014 Solved!",
        f"{head} \u2014 {date_short} Solved!",
    ]


def build_title(game, date_short, guesses=None, puzzle_number=None, seed=None,
                no_hints=False):
    """Pick one of three SEO title variants, rotated by puzzle number/date.

    Caps at 100 chars (YouTube limit). seed defaults to the puzzle
    number when known, else a hash of the date string, so the same
    puzzle always gets the same variant and variants rotate across
    days/numbers.
    """
    variants = _title_variants(str(game), str(date_short), guesses, puzzle_number,
                               no_hints=no_hints)
    if seed is None:
        seed = puzzle_number if puzzle_number is not None else hash(str(date_short))
    try:
        idx = int(seed) % len(variants)
    except Exception:
        idx = 0
    title = variants[idx]
    if len(title) > TITLE_MAX:
        # Short fallback (always well under the cap); never truncate
        # mid-phrase when a clean fallback exists.
        title = f"{game} Answer Today ({date_short})"
    if len(title) > TITLE_MAX:
        title = title[:TITLE_MAX]
    return title


def build_tags(game, date_short, date_num=None, puzzle_number=None,
               no_hints=False):
    """Tag list: head keyword first, then date/number variants.

    Total joined length capped at 500 chars (YouTube limit) by
    dropping trailing tags — the head keyword is never dropped.
    no_hints (iter-39): drop the Hints tags for games whose video
    carries no hints segment (Colorfle).
    """
    g = str(game)
    gl = g.lower()
    tags = [
        f"{gl} answer today",
        g,
        f"{g} Answer",
    ]
    if date_num:
        tags.append(f"{gl} answer today {date_num}")
        tags.append(f"{g} Answer {date_num}")
    if puzzle_number is not None:
        tags.append(f"{g} #{puzzle_number}")
    tags.extend([
        f"{g} {date_short}",
        f"{g} Today",
        f"Today's {g} Answer",
        f"{g} Solution",
    ])
    if not no_hints:
        tags.extend([
            f"{g} Hints",
            f"{g} Hint Today",
        ])
    tags.extend([
        f"{g} Solver",
        "Daily Puzzle",
        "Word Game",
        "Puzzle Solution",
    ])
    # Cap total length by dropping trailing tags (head keyword kept).
    while tags and sum(len(t) for t in tags) + max(0, len(tags) - 1) > TAGS_TOTAL_MAX:
        tags.pop()
    return tags


def build_captions_srt(chapters, total_duration, date_str, game):
    """SRT captions derived from the real chapter list (same format as
    daily_parity.build_captions_srt). Returns SRT text (no file)."""
    def ts(sec):
        sec = max(0, int(sec))
        h, rem = divmod(sec, 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d},000"

    lines, n = [], 1
    for i, (sec, title) in enumerate(chapters or []):
        end = chapters[i + 1][0] if i + 1 < len(chapters) else total_duration
        if end <= sec:
            end = sec + 4
        lines.append(f"{n}\n{ts(sec)} --> {ts(end)}\n{title} - {game} {date_str}\n")
        n += 1
    return "\n".join(lines)


def build_pinned_comment(game, chapters, solver_url=None, answer_url=None):
    """Pinned comment: chapters + solver link + subscribe prompt.

    Never includes the full answer for games whose answer is a single
    reveal (the comment is visible without watching); per-mode
    answers live in the description, not the pinned comment.
    """
    lines = [f"What was your first guess today? \U0001f914",
             "\U0001f7e9 = got it    \U0001f7e8 = close    \u2b1b = stumped"]
    if chapters:
        lines.append("")
        lines.append("\u23f1\ufe0f Chapters:")
        for sec, label in chapters:
            lines.append(f"{format_chapter_timestamp(sec)} {label}")
    if solver_url:
        lines.append("")
        lines.append(f"Try our FREE {game} Solver: {solver_url}")
    if answer_url:
        lines.append(f"{game} answer archive: {answer_url}")
    lines.append("")
    lines.append(f"\U0001f514 Subscribe for a new {game} answer every day!")
    return "\n".join(lines)


def partial_tiles(answer, max_tiles=10):
    """Curiosity-gap tiles for the thumbnail: first 2 characters
    revealed, the rest '?'. Pure + unit-testable.

    Alphanumeric characters only (spaces/punctuation are not tiles).
    Empty/unknown answers -> all '?' tiles (never reveal what is
    not known).
    """
    chars = [c for c in str(answer or "").upper() if c.isalnum()]
    if not chars:
        return ["?"] * min(5, max_tiles)
    n = min(len(chars), max_tiles)
    tiles = list(chars[:2])
    while len(tiles) < n:
        tiles.append("?")
    return tiles


def build_description(game, slug, date_long, date_short, answer=None,
                      guesses=None, chapters=None, puzzle_number=None,
                      analysis=None, yesterday=None, tomorrow=None,
                      stats=None, mode_lines=None, solver_url=None,
                      cross_links=None, no_hints=False,
                      skip_letter_faq=False):
    """SEO-rich description. Caps at 5000 chars (YouTube limit).

    Parameters (all optional except game/slug/dates):
      answer: the solved answer string (None for multi-answer games
        like Nerdle/Framed — use mode_lines there instead).
      guesses: number of guesses used (None when not applicable).
      chapters: list of (start_seconds, label) from the ACTUAL video.
      puzzle_number: served puzzle # when known (None -> omitted).
      analysis: dict with word/part_of_speech/definition/example/
        synonyms, OR dict with lines=[...] for preformatted facts.
        Omitted when None — never invented.
      yesterday: dict {answer, puzzle_number, date_short} from OUR OWN
        DB record for the previous day. Omitted when None.
      tomorrow: dict {first_letter, puzzle_number} — FIRST LETTER
        ONLY, from a trustworthy source. Omitted when None; the
        teaser is never fabricated from a guess.
      stats: dict of extra stat lines {label: value} (already
        verified facts only).
      mode_lines: list of "Mode: answers" strings for multi-answer
        games (Framed/Nerdle/Quordle-style).
      solver_url: solver page URL when one exists (None -> omitted).
      cross_links: list of (label, url) for other games' answer pages.
      no_hints (iter-39): the video carries no hints segment (Colorfle) —
        the description must not promise "hints" it does not contain.
      skip_letter_faq (iter-39): the answer is not a word (Colorfle's
        colour triple) — the "What letter does it start with?" FAQ is
        nonsense there and is omitted.
    """
    g = str(game)
    lines = []

    # Headline (exact-match keyword first).
    if puzzle_number is not None:
        lines.append(f"\U0001f7e9 {g} Answer Today \u2014 {date_long} | Puzzle #{puzzle_number} \U0001f7e9")
    else:
        lines.append(f"\U0001f7e9 {g} Answer Today \u2014 {date_long} \U0001f7e9")
    lines.append("")
    _hint_txt = "" if no_hints else " with hints"
    if answer:
        lines.append(
            f"Today's {g} answer is {answer}. Watch the full solve"
            f"{_hint_txt}, then the analysis and yesterday's recap.")
    elif mode_lines:
        lines.append(
            f"Today's {g} answers are all solved in this video. Watch "
            f"the full solve{_hint_txt}, then the analysis and "
            f"yesterday's recap.")
    else:
        lines.append(
            f"Watch today's {g} solved step by step{_hint_txt}, then "
            f"the analysis and yesterday's recap.")
    lines.append("")
    lines.append("Did you get today's puzzle? Comment your result below! \U0001f447")
    lines.append("\U0001f7e9 = got it    \U0001f7e8 = close    \u2b1b = stumped")
    lines.append("")

    # Chapters (YouTube auto-links these when the first is 0:00).
    if chapters:
        lines.append("\u23f1\ufe0f CHAPTERS:")
        for sec, label in chapters:
            lines.append(f"{format_chapter_timestamp(sec)} {label}")
        lines.append("")

    # FAQ quick-answers block (captures long-tail voice/search queries).
    faq = []
    if answer:
        faq.append(f"\u2022 What is today's {g} answer? \u2192 {answer}"
                   + (f" (#{puzzle_number})" if puzzle_number is not None else ""))
        _first = next((c for c in str(answer) if c.isalnum()), None)
        if _first and not skip_letter_faq:
            faq.append(f"\u2022 What letter does today's {g} start with? \u2192 {_first.upper()}")
        if guesses is not None:
            faq.append(f"\u2022 How many guesses did it take? \u2192 {guesses}")
    else:
        faq.append(f"\u2022 What are today's {g} answers? \u2192 Watch the video for the full solve!")
    if yesterday and yesterday.get("answer"):
        _y = yesterday["answer"]
        _yn = yesterday.get("puzzle_number")
        faq.append(f"\u2022 What was yesterday's {g} answer? \u2192 {_y}"
                   + (f" (#{_yn})" if _yn is not None else ""))
    lines.append("\u2753 QUICK ANSWERS:")
    lines.extend(faq)
    lines.append("")

    # Per-mode answers (multi-answer games).
    if mode_lines:
        for ml in mode_lines:
            lines.append(str(ml))
        lines.append("")

    # Analysis block (only when supplied — never invented).
    if analysis:
        if analysis.get("lines"):
            lines.append("\U0001f4da ANALYSIS:")
            lines.extend(str(x) for x in analysis["lines"])
            lines.append("")
        else:
            _w = analysis.get("word") or (answer or "")
            _pos = analysis.get("part_of_speech", "")
            _defn = analysis.get("definition", "")
            _ex = analysis.get("example", "")
            _syn = analysis.get("synonyms") or []
            if _defn:
                lines.append("\U0001f4da ANALYSIS:")
                if _w:
                    lines.append(f"   \u2022 Answer: {str(_w).upper()}")
                if _pos:
                    lines.append(f"   \u2022 Part of speech: {_pos}")
                lines.append(f"   \u2022 Definition: {_defn}")
                if _ex:
                    lines.append(f'   \u2022 Example: "{_ex}"')
                if _syn:
                    lines.append(f"   \u2022 Synonyms: {', '.join(str(s) for s in _syn[:5])}")
                lines.append("")

    # Yesterday recap (from our own DB only).
    if yesterday and yesterday.get("answer"):
        _yn = yesterday.get("puzzle_number")
        lines.append(f"\U0001f4c5 YESTERDAY'S {g.upper()}"
                     + (f" (#{_yn})" if _yn is not None else "")
                     + f": {yesterday['answer']}")
        lines.append("")

    # Tomorrow teaser — FIRST LETTER ONLY, only when a trustworthy
    # source supplied it. Never fabricated.
    if tomorrow and tomorrow.get("first_letter"):
        _tn = tomorrow.get("puzzle_number")
        lines.append(f"\U0001f52e TOMORROW'S {g.upper()}"
                     + (f" (#{_tn})" if _tn is not None else "")
                     + f": First letter is '{str(tomorrow['first_letter']).upper()}'")
        lines.append("")

    # Puzzle stats (verified facts only).
    lines.append("\U0001f3af PUZZLE STATS:")
    lines.append(f"   \u2022 Date: {date_long}")
    if puzzle_number is not None:
        lines.append(f"   \u2022 Puzzle Number: #{puzzle_number}")
    if guesses is not None:
        lines.append(f"   \u2022 Guesses used: {guesses}")
    if answer:
        lines.append(f"   \u2022 Answer: {answer}")
    for _k, _v in (stats or {}).items():
        lines.append(f"   \u2022 {_k}: {_v}")
    lines.append("")
    lines.append(f"\U0001f514 Subscribe for a new {g} answer every day!")
    lines.append("")
    if solver_url:
        lines.append(f"\U0001f9e0 Try our FREE {g} Solver:")
        lines.append(f"\U0001f517 {solver_url}")
        lines.append("")
    lines.append(f"\U0001f517 {g} answer today: https://wordsolverx.com/{slug}")
    lines.append("")
    if cross_links:
        lines.append("More daily answers:")
        for _label, _url in list(cross_links)[:6]:
            lines.append(f"\U0001f517 {_label}: {_url}")
        lines.append("")

    # Hashtags + keyword block.
    _tags = [f"#{g.replace(' ', '')}", f"#{g.replace(' ', '')}AnswerToday",
             "#DailyPuzzle", "#WordGame", "#PuzzleSolution"]
    if puzzle_number is not None:
        _tags.append(f"#{g.replace(' ', '')}{puzzle_number}")
    lines.append(" ".join(_tags))
    lines.append("")
    _kw_hints = "" if no_hints else f", {g.lower()} hints"
    lines.append(f"{g.lower()} answer today, {g.lower()} solver, "
                 f"{g.lower()} answers, how to play {g.lower()} today"
                 f"{_kw_hints}, {g.lower()} answer {date_short}")
    lines.append("")
    lines.append(f"{g} is a daily puzzle game. Subscribe for daily solutions!")

    desc = "\n".join(lines)
    return _cap_description(desc)


def _cap_description(desc):
    """Enforce the 5000-char YouTube description cap.

    Drops optional trailing sections (keyword block, hashtags,
    cross-links, analysis) before cutting text, and only hard-cuts
    at a newline as a last resort — the headline/chapters/FAQ
    (the high-value SEO) are never dropped first.
    """
    if len(desc) <= DESCRIPTION_MAX:
        return desc
    # Drop the trailing keyword/footer block first.
    _markers = [
        " is a daily puzzle game. Subscribe for daily solutions!",
        "More daily answers:",
        "\U0001f4da ANALYSIS:",
        "\U0001f52e TOMORROW'S",
        "\U0001f4c5 YESTERDAY'S",
    ]
    cur = desc
    for _m in _markers:
        if len(cur) <= DESCRIPTION_MAX:
            break
        _idx = cur.find(_m)
        if _idx != -1:
            # Remove from the marker's section start (previous blank line).
            _start = cur.rfind("\n\n", 0, _idx)
            cur = (cur[:_start] if _start != -1 else cur[:_idx]).rstrip() + "\n"
    if len(cur) > DESCRIPTION_MAX:
        _cut = cur[:DESCRIPTION_MAX].rsplit("\n", 1)[0]
        cur = _cut if _cut else cur[:DESCRIPTION_MAX]
    return cur
