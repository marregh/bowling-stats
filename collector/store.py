"""SQLite storage. Raw captures are kept forever; decodes are derived and
can be recomputed, so the decoder is free to improve after the fact.
"""
import os
import sqlite3
from pathlib import Path

# The real database is not in the repo -- it holds named people. A fresh
# clone therefore falls back to the generated sample so the dashboard has
# something to render; point LUMA_DB anywhere to override either default.
_DATA = Path(__file__).resolve().parent.parent / "data"
_REAL = _DATA / "club.db"
DB = Path(os.environ.get("LUMA_DB")
          or (_REAL if _REAL.exists() else _DATA / "sample.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS capture (
    id        INTEGER PRIMARY KEY,
    ts        TEXT NOT NULL,          -- when we polled
    slug      TEXT NOT NULL,
    lane      INTEGER NOT NULL,
    book_id   INTEGER,
    game      INTEGER,                -- game number off the sheet header, if read
    sha       TEXT NOT NULL,          -- hash of the PNG bytes
    png       BLOB NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS capture_uq ON capture(slug, lane, sha);
CREATE INDEX IF NOT EXISTS capture_book ON capture(book_id);

CREATE TABLE IF NOT EXISTS decode (
    capture_id INTEGER NOT NULL REFERENCES capture(id),
    row_ix     INTEGER NOT NULL,
    player     TEXT,
    team       TEXT,
    frames     TEXT,                  -- JSON: [["9","/"],["X"],...]
    game_score INTEGER,
    prior      INTEGER,               -- series total before this game
    verified   INTEGER NOT NULL,      -- 1 = reproduced the printed totals
    confidence REAL,
    PRIMARY KEY (capture_id, row_ix)
);

-- social.bowlit.nu frame-level data, keyed by BITS match id
CREATE TABLE IF NOT EXISTS social_game (
    match_id INTEGER NOT NULL, alley_id INTEGER NOT NULL,
    idx INTEGER, lane INTEGER, game INTEGER,
    player TEXT NOT NULL, player_id TEXT,
    cum_total INTEGER,               -- series total THROUGH this game, not the game
    game_score INTEGER,              -- derived
    completed INTEGER,
    balls TEXT,                      -- JSON [{"ball":"X","split":false}, ...]
    frame_totals TEXT,               -- JSON [17, 24, 54, ...]
    PRIMARY KEY (match_id, player, game)
);
CREATE TABLE IF NOT EXISTS social_player (
    match_id INTEGER NOT NULL, player TEXT NOT NULL,
    strikes INTEGER, spares INTEGER, misses INTEGER, splits INTEGER,
    open_frames INTEGER, frames INTEGER, total INTEGER,
    PRIMARY KEY (match_id, player)
);
-- Bowlit scoreboard names that BITS cannot resolve on its own: players new to
-- the club (no league record yet) and anyone whose scoreboard name is ambiguous.
CREATE TABLE IF NOT EXISTS player_alias (
    social_name TEXT PRIMARY KEY,   -- exactly as Bowlit spells it
    display     TEXT,               -- what we show
    is_club     INTEGER NOT NULL,   -- 1 = ours, 0 = explicitly not ours
    note        TEXT
);

-- sessions that are not BITS league matches: practice, friendlies, cups.
-- Their social.bowlit.nu id is the Bowlit BookId, learned from getlanes.
CREATE TABLE IF NOT EXISTS social_session (
    id        INTEGER PRIMARY KEY,      -- the BookId, used as social_game.match_id
    alley_id  INTEGER,
    season    INTEGER,
    played_on TEXT,
    kind      TEXT,                     -- 'practice', 'friendly', 'cup'
    label     TEXT
);

CREATE TABLE IF NOT EXISTS social_fetch (
    match_id INTEGER PRIMARY KEY, alley_id INTEGER, ts TEXT, games INTEGER
);

-- BITS mirror
CREATE TABLE IF NOT EXISTS bits_match (
    match_id   INTEGER PRIMARY KEY,
    season     INTEGER, division_id INTEGER, division TEXT, league TEXT,
    round_id   INTEGER, played_at TEXT,
    home_id INTEGER, home TEXT, away_id INTEGER, away TEXT,
    home_score INTEGER, away_score INTEGER,
    home_pts REAL, away_pts REAL,
    hall_id INTEGER, hall TEXT, city TEXT,
    oil_pattern TEXT, scheme_id TEXT, has_been_played INTEGER,
    -- When the collector first saw this match marked played. BITS flips
    -- has_been_played only once someone registers the protocol, and how long
    -- that takes is the thing that decides when the collectors are worth
    -- running. Recording it turns that from a guess into a measurement.
    first_seen_played TEXT
);
CREATE TABLE IF NOT EXISTS bits_result (
    match_id INTEGER NOT NULL, lic TEXT NOT NULL, player TEXT, side TEXT,
    g1 INTEGER, g2 INTEGER, g3 INTEGER, g4 INTEGER,
    hcp INTEGER, total INTEGER, series INTEGER,
    lane_point REAL, rank_points REAL, place INTEGER,
    PRIMARY KEY (match_id, lic)
);
CREATE TABLE IF NOT EXISTS notified (
    -- What has already been announced, so a re-run is silent rather than
    -- spamming the channel again. Everything else here is safe to re-run
    -- because it upserts; this one is safe because it remembers.
    match_id INTEGER NOT NULL,
    kind     TEXT NOT NULL,
    ts       TEXT,
    PRIMARY KEY (match_id, kind)
);
CREATE TABLE IF NOT EXISTS hidden (
    -- Things the site should not show for now, without deleting anything.
    -- A walkover, a protocol BITS has entered wrongly, a team that turns out
    -- not to be what its name suggests: all reasons to hide a row rather than
    -- lose it. Delete the row to unhide.
    kind   TEXT NOT NULL,          -- 'match' or 'team'
    ref    TEXT NOT NULL,          -- match_id, or team_id
    reason TEXT,
    ts     TEXT,
    PRIMARY KEY (kind, ref)
);
CREATE TABLE IF NOT EXISTS capture_raw (
    -- Whatever a live-scoring source hands us that is not an image: JSON from
    -- an API, mostly. Kept verbatim, because the point of recording during a
    -- match is that it cannot be done afterwards -- working out what the
    -- fields mean can wait, having the bytes cannot.
    id     INTEGER PRIMARY KEY,
    ts     TEXT NOT NULL,
    source TEXT NOT NULL,          -- 'falkenberg', 'lanetalk:<uuid>', ...
    kind   TEXT,                   -- 'live', 'leaderboard', ...
    sha    TEXT NOT NULL,
    body   TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS capture_raw_uq ON capture_raw(source, kind, sha);
CREATE INDEX IF NOT EXISTS capture_raw_ts ON capture_raw(ts);
CREATE TABLE IF NOT EXISTS bits_standing (
    season INTEGER, division_id INTEGER, team_id INTEGER, team TEXT,
    matches INTEGER, win INTEGER, draw INTEGER, loss INTEGER,
    home_pts INTEGER, away_pts INTEGER, diff INTEGER, points REAL,
    PRIMARY KEY (season, division_id, team_id)
);

-- Result fetches whose player rows did not add up to the match score, so that
-- one that can never add up is not re-asked for on every run forever.
--
-- `want` is the match score at the time of the attempt, and it is what makes
-- this safe: a protocol BITS later corrects arrives with a different score, so
-- it counts as new information and is fetched again. Only an unchanged
-- disagreement is left alone. Match 3318305 is the case in hand -- a SUL
-- fixture BITS publishes as 636-774 with every player's games as 0, which will
-- not reconcile no matter how often it is asked.
CREATE TABLE IF NOT EXISTS bits_result_gap (
    match_id INTEGER, want INTEGER, got INTEGER, ts TEXT,
    PRIMARY KEY (match_id, want)
);
"""


def migrate(con):
    """Columns added after a database already existed.

    CREATE TABLE IF NOT EXISTS silently leaves an older table alone, so a new
    column has to be added by hand. Cheap enough to check on every connect.
    """
    have = {r[1] for r in con.execute("PRAGMA table_info(bits_match)")}
    if "first_seen_played" not in have:
        con.execute("ALTER TABLE bits_match ADD COLUMN first_seen_played TEXT")
        # Matches already recorded as played were played before anyone was
        # watching. Stamping them "now" would read as a real observation and
        # make every lag calculation nonsense, so they are marked as what they
        # are and excluded from the measurement.
        con.execute("UPDATE bits_match SET first_seen_played = 'backfilled' "
                    "WHERE has_been_played = 1")
        con.commit()


def connect():
    """Open the database so that concurrent writers wait instead of dying.

    On 2026-09-19 the Falkenberg capture was killed mid-match by
    "database is locked": the hourly collector took the write lock at 12:00:39,
    and sqlite3's default five-second patience ran out on a capture that had
    been recording cleanly for 270 sweeps. The images and feeds these captures
    hold cannot be fetched again afterwards, so losing one to a lock held by a
    job that could simply have waited is the worst trade in the project.

    WAL lets readers -- the website, most of all -- carry on while a writer
    works, and a minute of busy_timeout is far longer than any write here takes.
    """
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB, timeout=60)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode = WAL")
        con.execute("PRAGMA synchronous = NORMAL")
    except sqlite3.DatabaseError:
        # An exclusive lock elsewhere can refuse the journal switch. It is a
        # persistent property of the file, so the next caller sets it instead.
        pass
    con.execute("PRAGMA busy_timeout = 60000")
    con.executescript(SCHEMA)
    migrate(con)
    return con
