# daily-games — multi-game YouTube solver videos (Wordle-parity)

Standalone repo layout like `wordle-video` / `quordle-video`:
`runner.py` + `answers.py` + `solve_lib.py` + `daily_parity.py` + `nerdle_solver.py`
at root, `requirements.txt`, `.github/workflows/`, `videos/{game}/`, `database/{date}/{game}/`.

## Batches (category-wise, Cloudflare cron TODO — user wires timing later)

| Batch | Workflow | UTC | IST | Games |
|---|---|---|---|---|
| A 9pm | `daily-batch-a-9pm.yml` | 15:30 | 21:00 prev-day puzzle, baked into 22:05 site build | betweenle, colordle, colorfle, framed (4 modes ONE video), searchle, phrazle, phoodle, contexto, semantle, worldle, worgle, countryle, nerdle (9 modes ONE video) |
| B waffle | `daily-batch-b-waffle.yml` | 02:30 | ~08:00 run → 09:00 waffle build | waffle |
| B morning | `daily-batch-b-morning.yml` | 05:15 | ~10:45 canuckle rebake window | canuckle, batterup (CDN 01:00 UTC flip) |
| B gamedle | `daily-batch-b-gamedle.yml` | 10:30 | ~16:00 run → 17:01 gamedle build | globle |
| B marveldle | `daily-batch-b-marveldle.yml` | 06:20 | ~11:40 marveldle rebake window | marveldle (api live-solve) |

Group B rule: video starts at the same time the game scraping + website rebuild
happen — 1hr-before is not possible for these date-gated games.

## No-duplicate / resume

- `runner.py --game X` checks `database/{date}/{game}/result.json` first.
  If solved=true with a video, it skips — a retry or next cron never remakes
  the same video.
- Parallel safe: matrix `fail-fast: false`, one runner per game.
- After each run the workflow commits `database/` back, so GitHub itself tracks
  which date+game is done. If a run fails, the next run picks it up.

## Database (structured links, easy search)

- `videos/{game}/{game}_final_{date}.mp4` — working + artifact (gitignored).
- `videos/{game}/result.json` — working copy.
- `database/{YYYY-MM-DD}/{game}/result.json` — committed.
- `database/{YYYY-MM-DD}/{game}/youtube.txt` — committed video URL.
- Search by date: list `database/{date}/`. Search by game: `database/*/{game}/`.

## Manual run from GitHub

Actions → pick batch → Run workflow → optional `game` input (empty = whole
matrix). Each game also runs locally:

```
pip install -r requirements.txt
python -m playwright install chromium
python runner.py --game nerdle
python runner.py --game framed
python runner.py --game waffle
python runner.py --all
```

## Cloudflare cron TODO (user does later)

- [ ] Create per-batch cron triggers matching the table above.
- [ ] Verify `publish-pages.yml` 22:05 / 00:15 / 09:00 / 17:01 IST rebuilds are live.
- [ ] Add batterup + marveldle answer sources, then add them to morning batch.
- [ ] Re-mint YouTube token if playlist/pin/comment 403s persist.

## Nerdle merge note

`nerdle_solver.py` is the 9-mode async solver copied as-is from
`youtube/nerdle-video/solver.py`. `runner.py --game nerdle` dispatches it via
subprocess and normalizes `videos/result_{date}.json` → `videos/nerdle/` +
`database/{date}/nerdle/`. Old probes/PNGs/webm live in `temp/` for re-analysis.
