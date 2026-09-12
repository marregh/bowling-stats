# Lunds BK Mamba — club statistics site

Two data sources, joined by player.

## 1. BITS (Swebowl) — league results.  Solved.

`https://bits.swebowl.se/MiscFrontApiConnector`. **No API key, no login, no
session warm-up** — this is the same-origin connector the BITS site's own pages
call, and it holds Swebowl's key server-side. Send a browser `User-Agent` and
`Referer: https://bits.swebowl.se/seriespel` and ask:

| `bits.py` | connector |
|---|---|
| `matches(season)` | `ListMatches?seasonId=&clubId=` |
| `standing(season, div)` | `GetStandings?seasonId=&divisionId=` |
| `match_results(id, scheme)` | `GetMatchResults?matchId=&matchSchemeId=` |
| `seasons()` / `teams(...)` | `Season` / `Team?clubId=&seasonId=` |

Endpoint names come from the inline JS on the `seriespel` and `match-detail`
pages. `ListMatches` returns more than the old `/Match` did: `matchDateTime`,
`matchHallName`/`City`, `matchDivisionName`, `matchSchemeId`, oil pattern and
`matchHasBeenPlayed`.

**Why not `api.swebowl.se` any more (changed 2026-09-10).** That host used to
answer anyone holding Swebowl's public web-client key. It now answers *that key*
with a bare IIS **403**, while a made-up key still gets the ordinary
empty-bodied 401 — so the key is recognised and refused, not missing. Nothing
about the request shape helps: headers, `Origin`/`Referer`, HTTP/2, query string
vs header all give the same 403. The old auth quirk (empty 401 until the session
had loaded bits.swebowl.se, `Referer` required, session lapsing when idle) is
history along with the host; it cost real debugging time twice and is written
down here only so it is not mistaken for the current failure.

This went unnoticed for nine days because nothing schedules the collector — see
**Keeping it current** below.

| Endpoint (all under the connector) | Returns |
| --- | --- |
| `/Season` | season ids (2026 = 2026/2027) |
| `/Club?seasonId=` | every club, for id lookup |
| `/Team?clubId=&seasonId=` | the club's teams |
| `/GetStandings?seasonId=&divisionId=` | division table |
| `/ListMatches?seasonId=&clubId=` | fixtures: date, hall, opponent, `matchSchemeId` |
| `/GetMatchResults?matchId=&matchSchemeId=` | per-player, per-game scores |
| `/GetMatchHeadInfo`, `/GetMatchScores` | match detail, not used yet |
| `/Division`, `/Hall`, `/County` | lookups |

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

## 3. scoring.se (Meriq) — the halls Bowlit does not reach.  UNPROVEN.

One JPEG per lane, regenerated as play goes on:

```
https://scoring.se/{alley}/{session}/{lane}_small.jpg     360x270  (the ceiling)
https://scoring.se/{alley}/{session}/{lane}_micro.jpg     240x180
```

Baltiska Bowlinghallen (Malmö) is alley **524** and carries **20 of our fixtures
a season**. Also covered: Eslöv 443, Klippan 497, Trelleborg 661, Nässjö 645,
Höganäs 478, Strike & Co Göteborg 637, Ed 438 — about 33 matches in all.
**Helsingborg is not on scoring.se**; there is no Olympia in its 50-hall list,
so Baltiska is the only hall we use where two league matches run at once.

Three constraints, all verified rather than assumed:

- **Nothing is retroactive.** Only today's `showdate` returns images; every
  earlier day comes back empty, last Saturday included. A match not captured
  while it is played is gone.
- **360x270 is the maximum.** `_big`, `_large`, `_full` and a bare name all 404,
  and the "2 stora skärmar" on the all-lanes page are these same files scaled up
  in the browser. Asking for fewer lanes does not help: lane 5 is the same URL
  whether the page is opened at startlane 1, 3 or 5.
- **The board shows only the current game.** Earlier games' frames are already
  gone from it, so polling must happen at least once per game.

For comparison, the shelved scoresheet decoder was built against **480x270
lossless PNGs**; these are 360x270 JPEGs at about an eighth the bytes. Whether
eight players' frames survive that is the open question — which is why
`capture_scoring.py` stores raw images and decodes nothing.

Which lanes to watch comes from BITS: `ListMatches` returns
`matchAlleyGroupName` ("5 - 12"), and `ListMatches?hallId=` gives every match at
a hall, not just ours.

**Each hall skins the board, so the rows need calibrating per hall.** Baltiska
is blue, Klippan orange, and the row pitch differs with it -- 19px against 17px:

| hall | alley | player 1 totals | player 2 totals |
|---|---|---|---|
| Baltiska Malmö | 524 | y 72..89 | y 134..151 |
| Klippans Bowlinghall | 497 | y 64..79 | y 126..141 |

The **columns are identical** everywhere: frame boundaries land on 41, 74, 106
… 335 at both, so the frame maths, the digit templates and the glyph font are
all shared. Only the y bands go in `decode_scoring.PROFILES`. A hall renders a
warm-up screen with no grid at all before the match ("Inspelning pågår"), so
calibrate from a board that is actually in play.

```powershell
.\capture_scoring.ps1 -Alley 524 -Lanes 5-12 -Until 10:35   # logs to logs/scoring.log
python collector/capture_scoring.py --once                   # one sweep
```

Captures land in the `capture` table as `slug = "scoring:524"`, deduplicated by
hash, with the session id in `book_id`.

### Reading the totals row — works, and is proved against BITS

`collector/decode_scoring.py` reads the running-totals row off a captured board.
The totals first, not the frames: they are digits only, and unlike the frames
they are **self-checking**. Totals climb, each step is a legal frame score of
0..30, and the last is the game score, which BITS publishes independently.

Measured on the first capture (Baltiska, 12 Sep, 1329 images):

| | |
|---|---|
| complete rows that also satisfied the arithmetic | **16** |
| of those, whose total is a real BITS game score | **16** |
| false positives | **0** |

45 distinct scores over a ~180-wide range, so 16 straight matches is not luck.
Precision is the property being claimed here, not recall: a cell that cannot be
read confidently comes back `None` and the row is rejected, which is why the
rows that survive are trustworthy.

Recall is the weak half, and the reasons are known rather than mysterious:

- **the overlay.** "Nästa bana: N" sits across frames 2-4 through the closing
  frames of every game — exactly when the board is finally complete. `--stitch`
  is the answer: a total never changes once printed, so each cell is voted on
  across every capture of that game, and the covered frames are recovered from
  before the overlay appeared.
- **game segmentation** currently starts a new game when frame 1's total drops,
  which one misread of "20" as "0" also does. The board prints "Serie 3" in its
  top corner; reading that is the honest fix and needs its own templates.
- **the capture window.** The first run stopped during game 3 of 4.

Two geometry facts that cost real debugging: the horizontal rules at y = 71, 90,
133 and 152 are dark across the full width, so including one in the band makes
every column look occupied and the digits refuse to separate; and the tenth
frame is half as wide again — three ball boxes — so a uniform cell width clips
its last digit and turns 248 into 24.

```
python collector/decode_scoring.py --lane 5            # per image
python collector/decode_scoring.py --stitch            # merged per game
```

Templates are in `collector/scoring_digits.npz`: 28 of them over the ten digits,
built by clustering 8084 real glyphs and labelling the clusters by eye.

### Shot statistics: count per rack, not per frame

`python tests/test_frames.py` -- 6 constructed sequences against 8 invariants,
then the same invariants over every player-game collected.

The tenth frame is where this goes wrong. A strike there earns a fresh rack, so
a frame is not always one rack, and any column whose numerator is counted per
ball against a denominator counted per frame will disagree with itself. It has
happened twice:

- **Spärr** showed 11 made of 16 chances, where the 11 counted every spare
  anywhere -- including a tenth-frame spare after a strike -- and the 16 counted
  only frames. Now `makable_made / makable`, both per rack.
- **Strike%** divided strikes by frames, so **a perfect game read 120%**. Twelve
  strikes really are thrown in ten frames. Now over `racks`.

Neither was visible in the data: nobody in the club had bowled 300, which is
exactly why the test builds the sequence instead of waiting for it.

Also per rack now, having silently ignored the tenth: single-pin chances (`X 9 /`
converted a single pin and counted nothing), split rate, and first-ball average.

`spares`, `strikes`, `misses`, `splits` and `frames` keep their per-ball
meanings on purpose -- `verify()` checks those against Bowlit's own published
aggregates, so redefining them would break the one external check there is.

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
```

Nothing to configure: the BITS collector needs no key. `BITS_CLUB_ID` and
`LUMA_DB` can be set to point at another club or another database file, and
both have working defaults.

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

### Keeping it current

`collect.ps1` runs one pass — BITS, then frame data, then Discord — and the
scheduled task **`LumaBowlingCollector`** runs it Saturdays and Sundays at 18:00
and 21:00 plus a daily 08:00 catch-up, logging to `logs/collect.log`. The order
inside is not a preference: `fetch_social.py` only looks at matches BITS has
already marked played, and notifications go last so a message always means the
data is there.

Nothing scheduled it before 2026-09-10, which is how the `api.swebowl.se` 403
went unnoticed for nine days: the dashboard kept serving, just from a database
that had stopped growing. The three collectors have genuinely different stakes,
and only one of them has a real deadline.

| | what a missed run costs | how often |
|---|---|---|
| `fetch_bits.py` | **nothing permanent.** BITS keeps results, standings and fixtures indefinitely, so catching up is just re-running it. A gap means a stale dashboard, not lost data | daily, or after each round |
| `fetch_social.py` | **eventually everything.** social.bowlit.nu retains roughly nine months; anything older is gone for good | weekly is ample |
| `capture.py` | the raw scoresheet for that lane, which cannot be re-fetched once it is rebooked | only useful *during* play, and only for casual games |

`capture.py` cannot usefully be scheduled blind — it has to run while people are
bowling, which is what `capture.ps1` is for. It also only earns its keep for
practice and friendlies now: league play is retroactive through
social.bowlit.nu, keyed by the BITS match id.

So the one worth scheduling for *preservation* is `fetch_social.py`. Scheduling
`fetch_bits.py` is worth it for a different reason — it is the thing that fails
loudly when Swebowl changes something, and a collector nobody runs is a
collector nobody notices breaking.

### Discord notifications

`collector/notify_new.py` posts a match to a Discord webhook once its results
are in the database — never before, so a message always means "there is
something to look at". It runs last in `collect.ps1`.

Configure in `.env` (gitignored; the webhook URL is a credential — anyone
holding it can post to the channel):

```
LUMA_DISCORD_WEBHOOK=https://discord.com/api/webhooks/...
LUMA_PUBLIC_BASE=https://<your-domain>/luma-bowling   # optional, adds a dashboard link
LUMA_NOTIFY_PLAYERS=1                               # 0 drops the per-player scores
```

**Run `--seed` once before the first real run.** It marks everything already
collected as announced without sending anything; skip it and the first run
posts an entire season in one go.

```
python collector/notify_new.py --seed       # once, at setup
python collector/notify_new.py --dry-run    # print payloads, send nothing
python collector/notify_new.py --match 123  # re-announce one match
```

Each message carries the result and points, the division, the hall, our own
players' game scores, and links to BITS, to the frame-by-frame sheet when the
alley runs Bowlit, and to the dashboard when `LUMA_PUBLIC_BASE` is set. The
dashboard link carries `?season=`, because the site reads it (`season_arg`) to
decide which season the whole view is about and otherwise defaults to the
newest — which would open the current season around a match from a past one. Sends
are recorded in `notified`, so re-running is silent rather than duplicating.

What is deliberately left out: **licence numbers** (they encode a birth date)
and opponents' individual scores. Our own players' scores are the point;
everyone else's are not ours to republish. Worth a thought for the U team,
which is a youth side — `LUMA_NOTIFY_PLAYERS=0` drops player lines entirely.

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
