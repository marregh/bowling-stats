# Lunds BK Mamba — club statistics site

Two data sources, joined by player.

## 1. BITS (Swebowl) — league results.  Solved.

`https://api.swebowl.se/api/v1`. The key is public in the sense that Swebowl's
own web client sends it in the clear, but it is theirs rather than ours, so it
is not committed here. Lift it from any request `bits.swebowl.se` makes to
`api.swebowl.se` (query string, `APIKey=`) and export it as `SWEBOWL_API_KEY`
before running a collector.

**Auth quirk (this one bit twice):** every call returns an *empty-bodied* 401
until the same session has loaded **bits.swebowl.se itself** — the API checks a
session the site establishes, not just the `ARRAffinity` cookie api.swebowl.se
hands out. Warm up with `GET https://bits.swebowl.se/` then `GET .../api/v1/`,
reuse the session, and send `Referer: https://bits.swebowl.se/`. The session also
lapses when idle, so `bits.py` treats any 401 as "re-warm and retry", not a
failure. A wrong *header* name gives `{"message":"API key is missing."}`; the key
belongs in the query string.

| Endpoint | Returns |
| --- | --- |
| `/Season` | season ids (2026 = 2026/2027) |
| `/Team?clubId=&seasonId=` | the club's teams |
| `/Standing?seasonId=&divisionId=` | division table |
| `/Match?seasonId=&clubId=` | fixtures: date, hall, opponent, `matchSchemeId` |
| `/matchResult/GetMatchResults?matchId=&matchSchemeId=` | per-player, per-game scores |
| `/player/PlayerDetail`, `/PlayerDetailGraphData` | player history |
| `/Club/ClubsForPlayerRanking?seasonId=` | club id lookup |

Club id **33651**. Teams: A 90611 (Sydallsvenskan), B 162063 (Skåne Syd 1),
F1 107121 (Div 2 Södra Götaland 2), F2 184677 (Div 3 Södra Götaland 3),
U 162098 (NVSK Ungdom). 97 team-matches in 2026/27, first on 12 September.

Game totals only — no frame detail. That is what source 2 is for.

## 2. social.bowlit.nu — frame-by-frame.  SOLVED.

```
https://social.bowlit.nu/{alley_id}/{id}
```

Lunds Bowling is alley **1037**. The `{id}` is, for league play, the **BITS match
id** we already store — so no booking numbers are needed and the whole thing is
retroactive. For casual games (practice, friendlies) it is the Bowlit `BookId`,
which is what `getlanes` reports and why the capture loop is still worth running
for those.

The page is plain HTML with the frame data in the markup: one `<div class=
"scoreTable">` per player per game carrying `data-lane`, `data-serie`,
`data-name`, `data-total` (**cumulative series total**, not the game score), and
a `<td>` per ball where **splits are marked `class="split"`**. Per-player
aggregates come as `data-strikes/-spares/-openframes/-frames` plus `data-miss`
and `data-split`.

The alley id is validated: a Lunds id returns nothing under any other alley.

**Retention:** data goes back to about **2025-12-06** and nothing before, so
roughly nine months. 26 of last season's matches were backfilled; everything
before December is gone.

This obsoletes the scoresheet-OCR path entirely. `sheet.py`/`decode.py` are kept
only because they still work, not because anything needs them.

### The old live-scoring route (superseded)

`POST https://livescoring.bowlit.nu/api/getlanes`, body `Slug=lunds-bowling`,
returns every lane with its `BookId` and the scoresheet as a base64 PNG.
Still the only way to learn a casual game's `BookId`.

`GET https://syncro.bowlit.nu/api/WebAuthAlleys` lists all 93 Bowlit alleys.
Home is `lunds-bowling` (id 1037, AMF, 12 lanes). ~70% of our fixtures are at a
Bowlit alley with live scoring on; all 48 home matches are.

### Hard constraints

- A match spans 8 lanes / 4 pairs, all sharing one `BookId`.
- The sheet shows **only the current game's frames**, plus two cumulative totals
  (series before this game, series after). Earlier games' frames are gone.
  → must poll at least once per game, ~60-90 s, throughout a match.
- A lane keeps its last sheet only until the next booking starts on it.
  Nothing is recoverable retroactively.
- No per-lane filter: each poll is the whole alley, ~1.3 MB. Images are
  byte-identical when unchanged, so hash-dedupe before storing.

### Scoresheet decoder (`sheet.py`, `decode.py`) — superseded, kept for reference

Working:
- row detection from the coloured name bars (red; green for whoever is up),
  handling 2-6 bowlers per lane with the row pitch scaled accordingly
- x-grid fitted per row to the ten running-total numbers (origin varies by
  lane — lanes 11 and 12 of the same match differ by half a frame pitch)
- 1-px cell dividers stripped before the glyph bbox is taken
- ten-pin scoring engine, exact on every verified row

The validator is the point: a decode is accepted only if the frames reproduce
the printed running totals. Every error so far was caught, none silent.

Not done:
- **glyph templates.** Trained on 4 hand-labelled rows, 3 of 4 leave-one-out
  rows decode exactly; failures are all missing/unrepresentative exemplars, not
  bad geometry. At least two stroke weights exist per glyph — a thin `/` and a
  bold `/` score 0.68 apart. Needs a real template set: harvest cells from a few
  hundred polls, cluster, label each distinct cluster once.
- reading the printed totals automatically (currently hand-supplied in tests)
- the collector loop, storage, and the site itself

## Test fixtures

Booking 1054853, 2026-08-30, Mamba F1 v BK Target practice, lanes 11-12.
Ground truth for four player rows is in the spike tests, verified arithmetically.


## Running it

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:SWEBOWL_API_KEY = "<key from bits.swebowl.se>"   # collectors only
```

```powershell
python collector/fetch_bits.py 2025 2026   # mirror BITS (safe to re-run)
python collector/fetch_social.py           # backfill frame data (safe to re-run)
python collector/capture.py --once         # one Bowlit poll
.\capture.ps1                              # poll every 75s during a match
.
un.ps1                                  # dashboard on 127.0.0.1:8768
```

`data/club.db` holds everything. The `capture` table is irreplaceable — raw
scoresheet PNGs that cannot be re-fetched once a lane is rebooked. The `decode`
table is derived and can be rebuilt as the decoder improves.

### The database is not in this repository

`data/club.db` is gitignored and will stay that way. It holds 400+ named
people, licence numbers that encode a birth date, and a youth division — none
of which belongs in a public repo, and almost none of which is the author's to
publish.

What ships instead is `data/sample.db`, generated by
`tools/make_sample_db.py`: invented players, invented opponents, simulated
games. Only the club's own team names and BITS ids are real, because those are
public and the app keys off them. It is enough to render every page.

`store.py` picks `club.db` when it exists and falls back to `sample.db` when it
does not, so a fresh clone runs with no setup. `LUMA_DB` overrides both:

```powershell
$env:LUMA_DB = "data/sample.db"   # force the sample even if club.db is present
python tools/make_sample_db.py    # regenerate it; deterministic, same seed
```

## Published

Runs behind a reverse proxy at `https://<your-domain>/luma-bowling`.

- `web/serve.py` runs the app under **waitress**, bound to `127.0.0.1:8768` only.
- Caddy terminates TLS and reverse-proxies (nginx or IIS do the job equally
  well; the prefix handling is the only part that matters):

  ```
  handle_path /luma-bowling* {
      reverse_proxy 127.0.0.1:8768
  }
  ```

  `handle_path` strips the prefix, so the app still routes on bare paths; only the
  links it *emits* carry the prefix, via `LUMA_URL_PREFIX=luma-bowling`.
  Set that env var **without** a leading slash -- MSYS shells rewrite a leading
  slash into a Windows path before Python sees it.
- `install-luma-service.ps1` (run **as Administrator**) installs it as the
  auto-starting `LumaBowling` service via [NSSM](https://nssm.cc/). It takes `-Nssm`, `-Port`, `-Prefix` and `-ServiceName`,
  and refuses to install over a port something else already answers on.
- `/__reload` (the dev hot-reload stream) 404s whenever `LIVE_RELOAD` is off, so
  it does not exist in production.

The dev server (`run.ps1`) stays loopback-only and is unrelated to the published
instance. Do not run both on 8768 -- Windows lets two sockets bind the same port
and you will get whichever answers first. This fails silently in the worst way:
waitress starts cleanly, `service.err.log` stays empty and the service reports
Running, while Caddy serves whatever else grabbed the port. Any process on the
box can cause it, not just `run.ps1` -- a throwaway test server in another
project is enough. When the published site shows the wrong app, find out who
actually owns the socket before touching the service:

```powershell
Get-NetTCPConnection -LocalPort 8768 -State Listen |
  ForEach-Object { Get-CimInstance Win32_Process -Filter "ProcessId=$($_.OwningProcess)" } |
  Select-Object ProcessId,CommandLine
```

## State

Done: BITS mirror (190 matches, 1516 player-match rows, seasons 2025 + 2026),
capture loop with hash-dedupe, dashboard (club overview, team pages with tables
and rosters, player leaderboard, per-player match history).

Frame data: 26 matches ingested, verified against Bowlit's own per-player counts
(401/403 players agree exactly). Dashboard has a shot-statistics leaderboard and
per-player shot breakdowns.

Coverage is the real limit: only 8 of our away cities have a Bowlit alley at all,
and several return nothing. Home matches are the dependable half.

Next: charts (form curves) — not started, deliberately. Away-alley ids beyond
Lunds (1037) and Lerum Pinyard (1069) need probing rather than name matching.
