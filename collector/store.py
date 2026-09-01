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
    oil_pattern TEXT, scheme_id TEXT, has_been_played INTEGER
);
CREATE TABLE IF NOT EXISTS bits_result (
    match_id INTEGER NOT NULL, lic TEXT NOT NULL, player TEXT, side TEXT,
    g1 INTEGER, g2 INTEGER, g3 INTEGER, g4 INTEGER,
    hcp INTEGER, total INTEGER, series INTEGER,
    lane_point REAL, rank_points REAL, place INTEGER,
    PRIMARY KEY (match_id, lic)
);
CREATE TABLE IF NOT EXISTS bits_standing (
    season INTEGER, division_id INTEGER, team_id INTEGER, team TEXT,
    matches INTEGER, win INTEGER, draw INTEGER, loss INTEGER,
    home_pts INTEGER, away_pts INTEGER, diff INTEGER, points REAL,
    PRIMARY KEY (season, division_id, team_id)
);
"""


def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con
