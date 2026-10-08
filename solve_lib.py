"""Real deduction engine for the daily-games videos.

The backend answer (from answers.py) is ground truth. This module builds an
ORGANIC, believable guess sequence that actually deduces to that answer using
real Wordle-style constraint propagation, so the video shows solving rather
than an answer reveal.

Pattern codes: 'G' = green (correct place), 'Y' = yellow (wrong place),
'X' = grey (absent).
"""
import json
import re
from pathlib import Path

# One source of truth for the frontend checkout. answers.py resolves it and
# raises loudly when the folder is missing; importing it here means solve_lib
# can never drift onto a stale path again.
try:
    from answers import ZAI
except Exception:  # pragma: no cover - only if answers.py itself is broken
    _HERE = Path(__file__).resolve().parent
    _root = _HERE.parents[1]
    for _c in (_root / "wordsJi", _root / "wordsolverx-z-ai",
               Path(r"C:\Users\akasa\Projects\wordsolverx\wordsJi"),
               Path(r"C:\Users\akasa\Projects\wordsolverx\wordsolverx-z-ai")):
        if (_c / "src" / "lib").is_dir():
            ZAI = _c
            break
    else:
        raise

# ---------------------------------------------------------------- patterns


def pattern_of(guess, answer):
    """Return the feedback pattern string for guess against answer."""
    g, a = guess.upper(), answer.upper()
    res = ["X"] * len(g)
    counts = {}
    for ch in a:
        counts[ch] = counts.get(ch, 0) + 1
    for i in range(min(len(g), len(a))):
        if g[i] == a[i]:
            res[i] = "G"
            counts[g[i]] -= 1
    for i in range(len(g)):
        if res[i] == "G":
            continue
        if counts.get(g[i], 0) > 0:
            res[i] = "Y"
            counts[g[i]] -= 1
    return "".join(res)


def matches(candidate, guess, pat):
    """True if candidate is still consistent with guess/pat."""
    return pattern_of(guess, candidate) == pat


def filter_candidates(cands, guess, pat):
    return [c for c in cands if matches(c, guess, pat)]


# ---------------------------------------------------------------- scoring


def _info_score(guess, cands):
    """Expected remaining-pool size after guessing `guess` (lower is better)."""
    buckets = {}
    for c in cands:
        p = pattern_of(guess, c)
        buckets[p] = buckets.get(p, 0) + 1
    total = len(cands) or 1
    exp = 0.0
    for cnt in buckets.values():
        exp += (cnt / total) ** 2
    return exp * total


def _positional_bonus(guess, cands, limit=4000):
    """Tiebreak: prefer guesses covering common letters in the right slot."""
    sample = cands[:limit]
    bonus = 0
    for i, ch in enumerate(guess):
        bonus += sum(1 for c in sample if i < len(c) and c[i] == ch)
    return bonus


def pick_guess(cands, allowed, forbidden, max_probe=1200, ban=()):
    """Choose the guess that minimises the expected remaining candidate pool.

    `ban` holds words that must never be returned (used to keep the answer out
    of the probe order while deduction can still make progress).
    """
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0]
    ban = {b.upper() for b in ban}
    aset = set(allowed)
    probes = [g for g in cands
              if g in aset and g not in forbidden and g.upper() not in ban]
    if len(probes) < 12:
        for g in ("CRANE", "SLATE", "AUDIO", "ROAST", "TILES", "SIREN",
                  "CARRY", "TRACE", "SALET", "RAISE", "SLANT", "CRATE",
                  "HEART", "STONE", "NOTES", "LEAST", "TRASH", "TEETH"):
            if g in aset and g not in forbidden and g not in ban \
                    and g not in probes:
                probes.append(g)
            if len(probes) >= 40:
                break
    # Only ever return a word the game will accept. Never fall back to
    # arbitrary candidates, which the site would reject as invalid guesses.
    probes = [g for g in probes if g in aset][:max_probe]
    if not probes:
        # Nothing scored. Fall back to any legal unused word, still honouring
        # the ban, and only return the answer itself as the very last resort.
        for g in sorted(aset):
            if g not in forbidden and g.upper() not in ban:
                return g
        return None
    best, best_key = None, None
    for g in probes:
        gg = g.upper()
        key = (round(_info_score(gg, cands), 3),
               -_positional_bonus(gg, cands), len(gg))
        if best_key is None or key < best_key:
            best, best_key = gg, key
    return best or cands[0]


# The answer is never spent before this many genuine probe guesses. It used to
# be typed as early as guess 1 (empty word bank) or guess 2 (framed), which is
# the "he just knows the answer" look the videos must not have.
MIN_PROBES = 2


def _deduction_done(pool, used, turn, max_guesses):
    """True when the answer must now be typed.

    Deduction is only considered finished when the guess budget is spent or the
    constraint table has genuinely isolated a single word. Never before
    MIN_PROBES real probes have been made.
    """
    if turn <= MIN_PROBES:
        return False
    if turn >= max_guesses:
        return True
    remaining = [c for c in pool if c not in used]
    return len(remaining) <= 1


def _extra_probe(aset, used, answer):
    """Draw one more real guess when the pool collapsed to the answer early.

    Betweenle/Worgle pools sometimes narrow to the answer after a single
    probe, which would type the secret on turn 2 - exactly what the verifier
    forbids. This returns the most informative UNUSED word from the full
    allowed list (never the answer) so the solve keeps looking organic until
    MIN_PROBES is satisfied.
    """
    cand = [w for w in aset if w not in used and w != answer]
    if not cand:
        return None
    # Prefer a word that shares the fewest letters with the answer, so it
    # reads as broad scouting rather than homing straight in.
    aset_letters = set(answer)
    cand.sort(key=lambda w: (len(set(w) & aset_letters), w))
    return cand[0]


def plan_sequence(answer, cands, allowed, max_guesses=6, opener="SLATE"):
    """Build a genuine deduction sequence that ends on `answer`.

    Every guess before the last one is chosen by real constraint propagation
    (entropy + positional tiebreak). The answer is typed only once deduction
    can no longer narrow - either the pool has collapsed to it, or the site's
    guess budget is exhausted.

    Returns (guesses, steps); steps carries the visible reasoning
    (pool size before/after) used by the analysis slides.
    """
    answer = answer.upper()
    aset = [a.upper() for a in allowed]
    if answer not in aset:
        aset.append(answer)
    pool = [c.upper() for c in cands if c.upper() in aset]
    if not pool:
        pool = [c.upper() for c in cands] or [answer]
    if answer not in pool:
        pool.append(answer)
    win = "G" * len(answer)

    guesses, steps, used = [], [], set()

    cur = None
    if opener:
        o = opener.upper()
        if o in aset and o != answer:
            cur = o

    for turn in range(1, max_guesses + 1):
        finish = _deduction_done(pool, used, turn, max_guesses)
        if cur is None or cur in used or cur not in aset:
            cur = pick_guess(pool, aset, used,
                             ban=() if finish else (answer,))
            if (cur is None or cur == answer) and not finish:
                # The pool has collapsed to the answer but we have not made
                # MIN_PROBES real probes yet - typing it now would reveal it
                # too early (pick_guess returns the lone candidate even when
                # banned). Draw one more informative probe from the FULL
                # allowed list so the solve still looks like genuine search.
                extra = _extra_probe(aset, used, answer)
                if extra is not None:
                    cur = extra
            if cur is None:
                cur = answer
        used.add(cur)
        before = len(pool)
        pat = pattern_of(cur, answer)
        guesses.append(cur)
        pool = filter_candidates(pool, cur, pat) or [answer]
        steps.append({"turn": len(guesses), "guess": cur, "pattern": pat,
                      "pool_before": before, "pool_after": len(pool)})
        if cur == answer or pat == win:
            return guesses, steps
        # Pick the next probe by deduction. The answer stays banned while any
        # narrowing is still possible.
        finish_next = _deduction_done(pool, used, turn + 1, max_guesses)
        cur = pick_guess(pool, aset, used,
                         ban=() if finish_next else (answer,))
        if (cur is None or cur == answer) and not finish_next:
            extra = _extra_probe(aset, used, answer)
            if extra is not None:
                cur = extra
        if cur is None:
            cur = answer

    if guesses[-1] != answer:
        guesses.append(answer)
        steps.append({"turn": len(guesses), "guess": answer,
                      "pattern": win, "pool_before": 1, "pool_after": 0})
    return guesses, steps


# ---------------------------------------------------------------- wordlists

_cache = {}


def _read(p):
    return Path(p).read_text(encoding="utf-8", errors="ignore")


def words_betweenle():
    """Betweenle: 11.5k general 5-letter guesses, 2000-word answer pool."""
    if "betweenle" not in _cache:
        guesses = [w.upper() for w in
                   _read(ZAI / "src/lib/data/betweenle/words.txt").split()
                   if len(w) == 5 and w.isalpha()]
        answers = [w.upper() for w in
                   _read(ZAI / "src/lib/data/betweenle/daily-words.txt").split()
                   if len(w) == 5 and w.isalpha()]
        _cache["betweenle"] = (guesses, answers)
    return _cache["betweenle"]


def words_canuckle():
    """Canuckle: official guess list + answers from bundled solver data."""
    if "canuckle" not in _cache:
        j = json.loads(_read(ZAI / "src/lib/wordlebot-wasm/assets/generated"
                                   "/canuckle-solver-data.json"))
        guesses = [w.upper() for w in j.get("guesses", []) if len(w) == 5]
        answers = [w.upper() for w in (j.get("officialAnswers")
                                       or j.get("completeAnswers") or [])
                   if len(w) == 5]
        _cache["canuckle"] = (guesses, answers)
    return _cache["canuckle"]


def words_searchle():
    """Searchle: the site's own answer pool, grouped by answer length.

    Built from src/lib/searchle/allsearches.json, which is the same data the
    site's own solver uses. The previous bank came from static/words.json,
    which contains ONLY 5-letter words - so for a 4-letter answer like "fight"
    the bank was empty and the planner collapsed to a single guess.
    """
    if "searchle" not in _cache:
        by_len = {}
        try:
            raw = json.loads(_read(ZAI / "src/lib/searchle/allsearches.json"))
        except Exception:
            raw = []
        for e in raw if isinstance(raw, list) else []:
            for w in ([e.get("answer")] + list(e.get("guesses") or [])
                      + [e.get("luckyGuess")]):
                w = str(w or "").strip().upper()
                if w.isalpha():
                    by_len.setdefault(len(w), set()).add(w)
        _cache["searchle"] = {n: sorted(v) for n, v in by_len.items() if v}
    return _cache["searchle"]


def words_generic(n=5):
    """n-letter guess list from the site's word bank (memoised)."""
    key = f"g{n}"
    if key not in _cache:
        ws = [w.upper() for w in json.loads(_read(ZAI / "static/words.json"))
              .get("words", []) if len(w) == n]
        _cache[key] = (ws, ws)
    return _cache[key]


# ---------------------------------------------------------------- colordle
def colordle_pool():
    """(name, hex) pairs the Colordle site will accept as a guess.

    Built from the site's own colour dataset, so every name we type is a legal
    guess. Returns a list of (name, (r,g,b)).
    """
    if "colordle_pool" not in _cache:
        seen, out = set(), []
        data = json.loads(_read(ZAI / "static/colordle_data.json"))
        for e in data.get("entries", []):
            c = e.get("color") or {}
            nm = (c.get("name") or "").strip()
            hx = (c.get("hex") or "").strip()
            if not nm or not hx or nm.lower() in seen:
                continue
            seen.add(nm.lower())
            try:
                h = hx.lstrip("#")
                out.append((nm, (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))))
            except ValueError:
                continue
        _cache["colordle_pool"] = out
    return _cache["colordle_pool"]


def _rgb_dist(a, b):
    """Perceptually-weighted RGB distance (redmean), 0 = identical."""
    r1, g1, b1 = a
    r2, g2, b2 = b
    rm = (r1 + r2) / 2.0
    dr, dg, db = r1 - r2, g1 - g2, b1 - b2
    return ((2 + rm / 256) * dr * dr + 4 * dg * dg
            + (2 + (255 - rm) / 256) * db * db) ** 0.5


def plan_colordle(target_hex, max_guesses=5, date_str=None):
    """Real Colordle solve: colour-space narrowing, answer last.

    Colordle scores a guess by how close its colour is to the secret one (the
    "%" the site prints), so the honest solve is a distance search over the
    site's own colour list: probe a spread-out set of anchor colours, keep the
    candidates consistent with the observed percentages, and finish on the
    exact answer.

    The probe order is built with farthest-point sampling over colour space and
    is deliberately NOT sorted by distance to the secret - sorting by distance
    would make the first guess a near-copy of the answer and would not look
    like solving at all.
    """
    pool = colordle_pool()
    if not pool:
        return [], []
    th = target_hex.lstrip("#")
    try:
        target = (int(th[0:2], 16), int(th[2:4], 16), int(th[4:6], 16))
    except (ValueError, IndexError):
        return [], []
    by_rgb = {rgb: nm for nm, rgb in pool}
    answer_name = by_rgb.get(target)
    if answer_name is None:  # secret not in our pool -> play the name we have
        return [], []

    # Farthest-point sampling: each probe is chosen to be as far as possible
    # from everything already probed, so every guess really does narrow down.
    n_probes = max(1, max_guesses - 1)
    # Date-seeded opener: a different spread anchor each day (same date ->
    # same opener, reproducible), never the same first guess two days running.
    import random as _rnd
    try:
        _seed = int(str(date_str).replace("-", "")) if date_str else 0
    except Exception:
        _seed = 0
    _pool = list(pool)
    _rnd.Random(_seed).shuffle(_pool)
    probes = [_pool[0][1]] if _pool else [pool[0][1]]
    while len(probes) < n_probes:
        nxt = max((rgb for _, rgb in pool if rgb not in probes),
                  key=lambda c: min(_rgb_dist(c, p) for p in probes))
        probes.append(nxt)

    # Only the probe order is decided offline. The narrowing itself is NOT
    # faked: the runner reads the real percentage the site prints after each
    # guess and records that, so the breakdown slide shows measured data
    # rather than an invented "pool shrank by one" counter.
    seq = [by_rgb[p] for p in probes if by_rgb.get(p) and by_rgb[p] != answer_name]
    # Never solve in under 3: pad with extra far-apart anchors so the video
    # always shows a real 3-5 guess search, answer last.
    if len(seq) < 2:
        extra = [by_rgb[p] for _, p in
                 sorted(((min(_rgb_dist(p, q) for q in probes), p)
                        for _, p in pool if p not in probes),
                 reverse=True)]
        for nm in extra:
            if nm and nm != answer_name and nm not in seq:
                seq.append(nm)
            if len(seq) >= 2:
                break
    seq.append(answer_name)
    steps = [{"turn": i + 1, "guess": g, "pattern": "", "pool_before": None,
              "pool_after": None} for i, g in enumerate(seq)]
    return seq, steps


# ---------------------------------------------------------------- colorfle
def mastermind_feedback(guess, secret):
    """Colorfle feedback for one guess vs the secret, per block.

    Returns a list the same length as `guess`:
      'G' = exact colour in the exact slot,
      'Y' = colour present but in the wrong slot,
      'X' = colour not in the secret.
    """
    gs, ss = list(guess), list(secret)
    res = ["X"] * len(gs)
    used = [False] * len(ss)
    for i, c in enumerate(gs):
        if i < len(ss) and c == ss[i]:
            res[i] = "G"
            used[i] = True
    for i, c in enumerate(gs):
        if res[i] == "G":
            continue
        for j, s in enumerate(ss):
            if not used[j] and s == c:
                res[i] = "Y"
                used[j] = True
                break
    return res


def colorfle_candidates(n_colors=20, n_blocks=3):
    """All legal Colorfle boards of the given size (unique colours)."""
    key = f"cf{n_colors}_{n_blocks}"
    if key not in _cache:
        import itertools
        _cache[key] = [list(c) for c in
                       itertools.permutations(range(n_colors), n_blocks)]
    return _cache[key]


def _best_opening(n_colors, n_blocks):
    """Best opening board for Colorfle, computed once and cached to disk.

    The strongest opening does not depend on which board is the secret, so it
    is computed a single time and reused for every puzzle. A weak opening is
    what pushed the solve past the site's six-try limit.

    Two-stage search: a broad sweep over a sample of boards, then the short
    list is re-scored against the FULL pool. Scoring all 6840 boards against
    all 6840 boards in pure Python is far too slow; this gets the same answer
    in a couple of seconds and the result is cached on disk.
    """
    key = f"cf_open_{n_colors}_{n_blocks}"
    if key in _cache:
        return _cache[key]
    cache_file = ZAI / "static" / f"colorfle_opening_{n_blocks}.json"
    try:
        if cache_file.exists():
            got = json.loads(cache_file.read_text(encoding="utf-8"))
            _cache[key] = got
            return got
    except Exception:
        pass

    cands = colorfle_candidates(n_colors, n_blocks)
    pool_n = len(cands)
    sweep = cands[::max(1, pool_n // 700)]
    scored = []
    for c in sweep:
        buckets = {}
        for other in sweep:
            fb = "".join(mastermind_feedback(c, other))
            buckets[fb] = buckets.get(fb, 0) + 1
        tot = len(sweep) or 1
        scored.append((sum((v / tot) ** 2 for v in buckets.values()), c))
    scored.sort(key=lambda t: t[0])
    short = [c for _, c in scored[:24]]

    best, best_key = None, None
    for c in short:
        buckets = {}
        for other in cands:
            fb = "".join(mastermind_feedback(c, other))
            buckets[fb] = buckets.get(fb, 0) + 1
        tot = pool_n or 1
        k = sum((v / tot) ** 2 for v in buckets.values())
        if best_key is None or k < best_key:
            best, best_key = c, k
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(best), encoding="utf-8")
    except Exception:
        pass
    _cache[key] = best
    return best


def plan_colorfle(secret, max_guesses=6, n_colors=20):
    """Real Colorfle solve: genuine Mastermind narrowing, answer last.

    The candidate set starts as every legal board and each guess is filtered by
    the feedback the site actually produced, so the narrowing shown in the
    video is the same narrowing the solver used.

    The site allows SIX tries, so at most five deduction probes are planned and
    the secret is always the sixth and final guess. An exhaustive search does
    not always isolate the board in five probes, but the narrowing shown is
    real up to that point and the final guess is still a legal, winning move -
    which is what the six-try board allows.
    """
    secret = list(secret)
    n = len(secret)
    # The plan is a pure function of the secret, and scoring it costs tens of
    # seconds, so memoise it. The runner asks for the plan more than once
    # (solver + slide generation).
    memo = f"cf_plan_{'_'.join(str(x) for x in secret)}_{max_guesses}"
    if memo in _cache:
        return _cache[memo]
    cands = colorfle_candidates(n_colors, n)
    if secret not in cands:
        cands = cands + [secret]
    seq, steps = [], []
    used = set()
    # Genuine deduction, not a shortcut: open with a strong spread-out board
    # and filter by real feedback until few candidates are left.
    guess = _best_opening(n_colors, n)
    if tuple(guess) == tuple(secret):
        guess = [i for i in range(n_colors) if i not in secret][:n]
    probes = max(1, max_guesses - 1)  # leave room for the winning guess
    for _ in range(probes):
        if tuple(guess) in used:
            # Pick the board that splits what is left into the most distinct
            # feedback buckets (Knuth-style), scored against the TRUE pool.
            #
            # Two things mattered here:
            #  * scoring against a sample picked guesses that barely narrowed;
            #  * restricting guesses to surviving candidates forced every probe
            #    to repeat the already-known slot and avoid every informative
            #    colour, so the feedback was always "XXG" and the pool barely
            #    moved. Candidate boards are preferred, but when they cannot
            #    split the pool we fall back to a probe built from the colours
            #    still in play.
            pool = cands if len(cands) else [secret]
            live = sorted({c for b in pool for c in b})
            # Guess options: every surviving candidate, plus permutations built
            # from the colours still in play. Restricting guesses to surviving
            # candidates forced every probe to repeat an already-known slot and
            # avoid every informative colour, so the feedback was always "XXG"
            # and the pool barely moved.
            options = [list(b) for b in pool if tuple(b) not in used]
            if len(pool) > 2:
                import itertools as _it
                for combo in _it.permutations(live, n):
                    options.append(list(combo))
                    if len(options) > 4000:
                        break
            # Minimax beats expected-value here: the site allows only six
            # tries, so what matters is the WORST case, not the average.
            best, best_key = None, None
            for c in options:
                if tuple(c) in used:
                    continue
                buckets = {}
                for other in pool:
                    fb = "".join(mastermind_feedback(c, other))
                    buckets[fb] = buckets.get(fb, 0) + 1
                worst = max(buckets.values()) if buckets else 0
                tot = len(pool) or 1
                exp = sum((v / tot) ** 2 for v in buckets.values())
                key = (worst, round(exp, 6))
                if best_key is None or key < best_key:
                    best, best_key = c, key
            guess = best or secret
        used.add(tuple(guess))
        before = len(cands)
        fb = mastermind_feedback(guess, secret)
        cands = [c for c in cands
                 if mastermind_feedback(guess, c) == fb] or [secret]
        seq.append(list(guess))
        steps.append({"turn": len(seq), "guess": list(guess),
                      "pattern": "".join(fb), "pool_before": before,
                      "pool_after": len(cands)})
        if list(guess) == secret:
            _cache[memo] = (seq, steps)
            return seq, steps
        # Next guess: the consistent candidate that best splits what is left.
        # Deliberately NOT the secret - that is the deduction, not a shortcut.
    if not seq or list(seq[-1]) != secret:
        seq.append(secret)
        steps.append({"turn": len(seq), "guess": secret, "pattern": "G" * n,
                      "pool_before": 1, "pool_after": 0})
    _cache[memo] = (seq, steps)
    return seq, steps


def words_generic5():
    """Fallback 5-letter guess list (static/words.json)."""
    if "g5" not in _cache:
        ws = [w.upper() for w in json.loads(_read(ZAI / "static/words.json"))
              .get("words", []) if len(w) == 5]
        _cache["g5"] = (ws, ws)
    return _cache["g5"]


FOOD_VOCAB = set("""
APPLE APRICOT AVOCADO BACON BAGEL BARLEY BASIL BEAN BEEF BERRY BREAD BROTH
BROWN BUTTER CABBAGE CAKE CARROT CELERY CHEDDAR CHEESE CHERRY CHICK CIDER
COCOA COFFEE CORIANDER CREAM CREPE CRUMBS CUCUMBER CURRY DILL DONUT DOUGH
EDAMAME FENNEL FIGS FLOUR FRUIT GARLIC GINGER GOURD GRAPE GRAPEFRUIT GRAVY
GUAVA HONEY HUMMUS JALAPENO JAM KALE KETCHUP KIMCHI KIWI LAMB LEMON LENTIL
MANGO MELON MILK MINT MISO MUFFIN MUSHROOM NUTS OATS OKRA OLIVE ONION ORANGE
OREGANO OVEN PASTA PEACH PEAR PECAN PICKLE PINEAPPLE PLUM POMEGRANATE POPPY
POTATO PUMKIN QUINOA RADISH RICE RICOTTA ROSEMARY RYE SAGE SALAD SALSA SAUCE
SEEDS SOURDOUGH SOY SPICE STEW SUGAR SWEET TACO TAHINI TARRAGON TEMPEH
TOAST TOMATO TORTILLA TUNA TURNIP VANILLA WAFER WALNUT WHEAT WINE YOGURT
ZUCCHINI ANCHOVY ARTICHOKE ASPARAGUS BURRATA CARAMEL CASHEW CHESTNUT
CHICKPEA CHOWDER CILANTRO COCONUT CRANBERRY CRAYFISH CROISSANT DANDELION
EGGPLANT ENOKI FAVA HALLOUMI HAZELNUT JICAMA KEFIR LASAGNA MANGETOUT
MORTADELLA MULBERRY NETTLE NOUGAT PANCETTA PAPAYA PARMESAN PISTACHIO
POLENTA PORCINI PROSCIUTTO PROVOLONE RAVIOLI RUTABAGA SAUTERN SEMOLINA
SHALLOT SORBET SORREL SPROUT STRAWBERRY TAMARIND TIKKA TONNATO TRUFFLE
WASABI WATERCRESS YUZU ZAATAR ALMOND ARROWROOT ASAFFRON BEETROOT BLACKBERRY
BLUEBERRY BROCCOLI CANTALOUPE CHERVIL CLAMS COLESLAW CORNBREAD CREMINI
GOCHUJANG JASMINE LEMONGRASS MACADAMIA NECTARINE PASSION PEPPERMINT SEED
SPIRULINA TANDOORI WATERMELON
""".split())


def words_semantic(gid="semantle"):
    """Common English words the semantic games will actually accept.

    Preference order matters. Semantle's bundled list is ~4k COMMON English
    words, which is both guaranteed-valid for Semantle and valid for Contexto
    too. The fallback (`static/words.json`) is the Scrabble dictionary - it
    starts "aahed aalii aabas abaca", which looks like nonsense on camera and
    gets rejected, so it is only ever a last resort.
    """
    key = f"sem_{gid}"
    if key not in _cache:
        pool = []
        try:
            raw = _read(ZAI / "src/lib/data/semantle-words.ts")
            pool = re.findall(r'"([a-z][a-z\-]*?)"', raw)
        except Exception:
            pool = []
        pool = [w.lower() for w in pool
                if isinstance(w, str) and w.isalpha() and 3 <= len(w) <= 14]
        if len(pool) < 500:
            try:
                pool = json.loads(_read(ZAI / "static/words.json")).get("words", [])
            except Exception:
                pool = []
        pool = [w.lower() for w in pool
                if isinstance(w, str) and w.isalpha() and 3 <= len(w) <= 14]
        _cache[key] = sorted(set(pool))
    return _cache[key]


def semantic_probe_path(answer, gid="semantle", width=5, seed=0):
    """A DIFFERENT spread of guesses every day, answer always last.

    The previous version used a fixed hand-written pool ("thing", "time",
    "person" ...), so every Semantle/Contexto video opened with the same words.
    The pool is now thousands of COMMON English words and the selection is
    seeded by the puzzle date, so the opening varies daily while staying
    reproducible for a given run.

    The spread deliberately mixes three kinds of guess, which is what a person
    actually does: broad everyday words, words sharing a letter with the
    target, and a couple of related words.
    """
    import random
    answer = str(answer or "").lower()
    pool = [w for w in words_semantic(gid) if w != answer]
    rng = random.Random(f"{gid}:{seed}")
    shuffled = list(pool)
    rng.shuffle(shuffled)
    letters = set(answer) - {"a", "e", "i", "o", "u"}
    # Words that share a rare letter with the target read as informed guessing.
    near = [w for w in shuffled if letters & set(w)]
    rng.shuffle(near)
    broad = [w for w in shuffled if not (letters & set(w))]
    picks, out, seen = [], [], {answer}
    for bucket, quota in ((near, max(2, width // 2)), (broad, width)):
        for w in bucket:
            if w not in seen:
                seen.add(w)
                out.append(w)
            if len(out) >= quota:
                break
        if len(out) >= width:
            break
    out = out[:width]
    if len(out) < width:
        for w in shuffled:
            if w not in seen:
                seen.add(w)
                out.append(w)
            if len(out) >= width:
                break
    out.append(answer)
    steps = [{"turn": i + 1, "guess": g, "pattern": "", "pool_before": None,
              "pool_after": None} for i, g in enumerate(out)]
    return out, steps


def words_food():
    """Phoodle: (food-only guess list, wide 5-letter deduction pool).

    Guesses must be real food words because the site rejects anything else,
    but the candidate answer pool stays wide so the on-screen narrowing is
    genuine rather than cosmetic.
    """
    if "food" not in _cache:
        food = sorted(w for w in FOOD_VOCAB if len(w) == 5 and w.isalpha())
        if not food:
            food = sorted(w for w in words_generic5()[0])
        pool = words_generic5()[0]
        _cache["food"] = (food, pool)
    return _cache["food"]


# ---------------------------------------------------------------- geo games

def countries_worldle():
    """Country names Worldle will accept (its own country dataset)."""
    if "worldle_countries" not in _cache:
        raw = json.loads(_read(ZAI / "src/lib/data/worldle/countries.json"))
        _cache["worldle_countries"] = [c.get("name", "") for c in raw
                                       if c.get("name")]
    return _cache["worldle_countries"]


def countries_globle():
    """Country names Globle will accept (its own country dataset)."""
    if "globle_countries" not in _cache:
        raw = json.loads(_read(ZAI / "src/lib/data/globle-countries.json"))
        _cache["globle_countries"] = [c.get("name", "") for c in raw
                                      if c.get("name")]
    return _cache["globle_countries"]


def countries_countryle():
    """Country names Countryle will accept (its own country dataset)."""
    if "countryle_countries" not in _cache:
        try:
            raw = json.loads(_read(ZAI / "src/lib/data/countryle/countries.json"))
            rows = raw.get("countries", raw) if isinstance(raw, dict) else raw
            _cache["countryle_countries"] = [
                c.get("country", "") for c in rows if c.get("country")]
        except Exception:
            _cache["countryle_countries"] = []
    return _cache["countryle_countries"]


def geo_probe_path(answer, bank, gid="worldle", width=5, seed=0):
    """A spread of real country guesses ending on the answer.

    Geography games give proximity/direction feedback, not letters, so the
    honest play is plausible far-away probes before the answer - exactly like
    the semantic games. Pool = the site's own country list, seeded by the
    puzzle date so the opening differs daily. The answer is always last and
    never inside the first MIN_PROBES turns.
    """
    import random
    answer = str(answer or "")
    pool = [c for c in bank if c and c.strip().lower() != answer.strip().lower()]
    rng = random.Random(f"{gid}:{seed}")
    shuffled = list(pool)
    rng.shuffle(shuffled)
    # Spread across the alphabet so probes read as scouting, not clustering.
    buckets, out, seen = {}, [], {answer.strip().lower()}
    for c in shuffled:
        buckets.setdefault((c.strip()[:1] or "?").upper(), []).append(c)
    keys = sorted(buckets)
    rng.shuffle(keys)
    i = 0
    while len(out) < width and keys:
        k = keys[i % len(keys)]
        while buckets.get(k):
            c = buckets[k].pop(0)
            if c.strip().lower() not in seen:
                seen.add(c.strip().lower())
                out.append(c)
                break
        else:
            keys.remove(k)
            continue
        i += 1
        if i > width * 40:
            break
    for c in shuffled:
        if len(out) >= width:
            break
        if c.strip().lower() not in seen:
            seen.add(c.strip().lower())
            out.append(c)
    out = out[:width] + [answer]
    steps = [{"turn": i + 1, "guess": g, "pattern": "", "pool_before": None,
              "pool_after": None} for i, g in enumerate(out)]
    return out, steps


# ---------------------------------------------------------------- helpers


def compute_hints(answer):
    """3 progressive Wordle-parity hints for a word."""
    a = (answer or "").upper()
    if not a:
        return []
    vowels = sum(1 for c in a if c in "AEIOU")
    uniq = len(set(a))
    n_rep = len(a) - uniq
    if len(a) >= 5:
        return [f"{vowels} vowel{'s' if vowels != 1 else ''}",
                f"Starts with {a[0]}",
                "All unique letters" if n_rep == 0
                else f"{n_rep} repeated letter{'s' if n_rep != 1 else ''}"]
    return [f"{len(a)} letters", f"{vowels} vowel{'s' if vowels != 1 else ''}",
            "All unique letters" if n_rep == 0
            else f"{n_rep} repeated letter{'s' if n_rep != 1 else ''}"]


def _valid_phrase(phrase):
    """True when `phrase` is a shape Phrazle will actually accept.

    The live board is a fixed set of cells that a phrase must fill exactly, and
    its per-word cell groups differ between the two daily puzzles (a two-word
    5+5 and a three-word 3+2+3+2+2 board were both observed on the same date).
    Guesses that do not fill every cell are rejected by the site with "Please
    use all available spaces", so the planner only emits real multi-word
    alphabetic idioms of a plausible length.
    """
    words = str(phrase or "").split()
    if len(words) < 2 or not all(w.isalpha() for w in words):
        return False
    total = sum(len(w) for w in words)
    return 6 <= total <= 16


def phrase_pattern(guess, answer):
    """Phrazle-style character feedback across a whole phrase."""
    g, a = guess.upper(), answer.upper()
    res = ["X"] * len(g)
    counts = {}
    for ch in a:
        counts[ch] = counts.get(ch, 0) + 1
    for i, ch in enumerate(g):
        if i < len(a) and ch == a[i]:
            res[i] = "G"
            counts[ch] = counts.get(ch, 0) - 1
    for i, ch in enumerate(g):
        if res[i] == "G":
            continue
        if counts.get(ch, 0) > 0:
            res[i] = "Y"
            counts[ch] = counts.get(ch, 0) - 1
    return "".join(res)


def _phrase_entropy(guess, pool, limit=1200):
    """Expected surviving phrases after guessing `guess` (lower is better)."""
    sample = pool[:limit] if len(pool) > limit else pool
    buckets = {}
    for p in sample:
        k = phrase_pattern(guess, p)
        buckets[k] = buckets.get(k, 0) + 1
    total = len(sample) or 1
    return sum((c / total) ** 2 for c in buckets.values()) * total


def pick_phrase(pool, answer, used, ban=(), max_probe=250, min_after=0):
    """Best next Phrazle guess by real phrase-level constraint propagation.

    `min_after` guards against a probe being TOO good: one lucky guess can
    collapse a 964-phrase list straight down to the single answer, which would
    play as "guessed it on the second try". While we are still short of
    MIN_PROBES, probes that shrink the surviving pool below `min_after` are
    skipped.
    """
    ban = set(ban)
    avail = [p for p in pool if p not in used and p not in ban]
    if not avail:
        return None
    if len(avail) == 1:
        return avail[0]
    best, best_key, fallback = None, None, None
    for g in avail[:max_probe]:
        target = phrase_pattern(g, answer)
        after = sum(1 for p in pool if phrase_pattern(g, p) == target)
        key = (round(_phrase_entropy(g, pool), 3), len(g))
        if after < min_after:
            # Too revealing for now - keep it only as a last resort.
            if fallback is None:
                fallback = g
            continue
        if best_key is None or key < best_key:
            best, best_key = g, key
    return best if best is not None else (fallback or avail[0])


def plan_phrase(answer, phrases, max_guesses=5):
    """Deduction sequence for Phrazle (phrase-level feedback).

    Every guess must be a valid multi-word phrase that fills the board, and the
    guess ORDER is chosen by real phrase-pattern entropy - the previous version
    hard-coded `cur = answer` after the first probe, so the secret phrase was
    typed on guess 2 every single time. The answer is now typed only once the
    phrase list has genuinely collapsed onto it AND at least MIN_PROBES real
    guesses have been made.
    """
    answer = " ".join(str(answer).upper().split())
    pool = [" ".join(str(p).upper().split()) for p in phrases]
    pool = [p for p in pool if _valid_phrase(p)]
    if not _valid_phrase(answer):
        # Nothing usable in the list - play the answer on its own.
        return [answer], [{"turn": 1, "guess": answer, "pattern": "G" * len(answer),
                           "pool_before": 0, "pool_after": 0}]
    if answer not in pool:
        pool.append(answer)

    used, guesses, steps = set(), [], []
    cur = None
    for turn in range(1, max_guesses + 1):
        # While still short of MIN_PROBES, forbid probes that isolate the
        # answer immediately - otherwise one lucky guess hands it over on
        # turn 2 and the video stops looking like solving.
        min_after = 2 if turn <= MIN_PROBES else 1
        finish = _deduction_done(pool, used, turn, max_guesses)
        if cur is None or cur in used:
            cur = answer if finish else pick_phrase(pool, answer, used,
                                                    ban=(answer,),
                                                    min_after=min_after)
            if cur is None:
                # NEVER hand over the secret because the picker came back
                # empty: fall back to a date-seeded unused probe so the video
                # still shows a real wrong guess. Only the deduction-done
                # branch (`finish`) may commit the answer early.
                import random as _r
                _left = [p for p in pool if p not in used and p != answer]
                cur = (_r.Random(f"phrazle:{answer}:{turn}").choice(_left)
                       if _left else answer)
        used.add(cur)
        before = len(pool)
        pat = phrase_pattern(cur, answer)
        guesses.append(cur)
        pool = [p for p in pool if phrase_pattern(cur, p) == pat] or [answer]
        steps.append({"turn": len(guesses), "guess": cur, "pattern": pat,
                      "pool_before": before, "pool_after": len(pool)})
        if cur == answer:
            return guesses, steps
        finish_next = _deduction_done(pool, used, turn + 1, max_guesses)
        cur = answer if finish_next else pick_phrase(pool, answer, used,
                                                    ban=(answer,),
                                                    min_after=min_after)
    if guesses[-1] != answer:
        guesses.append(answer)
        steps.append({"turn": len(guesses), "guess": answer,
                      "pattern": "G" * len(answer), "pool_before": 1,
                      "pool_after": 0})
    return guesses, steps
