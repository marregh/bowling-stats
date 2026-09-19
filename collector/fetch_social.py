"""Backfill frame-level data from social.bowlit.nu.

League matches are addressed by their **BITS match id** -- the same id already in
bits_match -- so no booking numbers are needed and the whole thing is retroactive
as far as Bowlit's retention goes (currently back to about 2025-12-06).

Only matches played at an alley running Bowlit resolve; everything else returns
an empty scorecard and is recorded as "no data" so we do not re-fetch it forever.
"""
import argparse, json
from datetime import datetime, timezone

import social
from bits import SEASON_STARTS_MONTH, current_season
from store import connect

ALLEYS = {"Lunds Bowlinghall": 1037, "Lerum Pinyard Bowling": 1069}

# How long an empty result stays worth retrying. Long enough to cover a sheet
# that appears late or a weekend of failed runs, short enough that the 50-odd
# away matches at non-Bowlit alleys are not re-asked forever.
RETRY_DAYS = 14


def ingest(con, match_id, alley_id):
    parsed = social.parse(social.fetch(match_id, alley=alley_id))
    games = parsed["games"]
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    con.execute("INSERT OR REPLACE INTO social_fetch VALUES (?,?,?,?)",
                (match_id, alley_id, ts, len(games)))
    if not games:
        con.commit()
        return 0

    # data-total is the running series total; the game's own score is the delta
    by_player = {}
    for g in games:
        by_player.setdefault(g["player"], []).append(g)
    for player, gs in by_player.items():
        gs.sort(key=lambda x: (x["game"] or 0))
        prev = 0
        for g in gs:
            cum = g["total"]
            g["_score"] = (cum - prev) if (cum is not None) else None
            prev = cum if cum is not None else prev

    for g in games:
        con.execute("""INSERT OR REPLACE INTO social_game VALUES
                       (?,?,?,?,?,?,?,?,?,?,?,?)""", (
            match_id, alley_id, g["index"], g["lane"], g["game"],
            g["player"], g["player_id"], g["total"], g.get("_score"),
            1 if g["completed"] else 0,
            json.dumps(g["balls"], ensure_ascii=False),
            json.dumps(g["frame_totals"])))
    for player, a in parsed["players"].items():
        con.execute("""INSERT OR REPLACE INTO social_player VALUES
                       (?,?,?,?,?,?,?,?,?)""", (
            match_id, player, a["strikes"], a["spares"], a["miss"], a["split"],
            a["open_frames"], a["frames"], a["total"]))
    con.commit()
    return len(games)


def season_of(date_str):
    """Bowling seasons run July-June: 2026-08-30 is season 2026."""
    y, m = int(date_str[:4]), int(date_str[5:7])
    return y if m >= SEASON_STARTS_MONTH else y - 1


def ingest_session(con, booking, alley, played_on, kind, label):
    n = ingest(con, booking, alley)
    con.execute("INSERT OR REPLACE INTO social_session VALUES (?,?,?,?,?,?)",
                (booking, alley, season_of(played_on), played_on, kind, label))
    con.commit()
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--booking", type=int,
                    help="ingest one non-league session by its Bowlit BookId")
    ap.add_argument("--alley", type=int, default=social.LUNDS_BOWLING)
    ap.add_argument("--date", help="YYYY-MM-DD the session was played")
    ap.add_argument("--kind", default="practice")
    ap.add_argument("--label", default="")
    ap.add_argument("--season", type=int, action="append")
    ap.add_argument("--refetch", action="store_true",
                    help="also retry matches previously found empty")
    a = ap.parse_args()

    con = connect()
    if a.booking:
        if not a.date:
            ap.error("--booking needs --date (the page carries no date of its own)")
        n = ingest_session(con, a.booking, a.alley, a.date, a.kind,
                           a.label or f"{a.kind} {a.date}")
        print(f"booking {a.booking}: {n} games")
        return

    # Current season only unless asked otherwise: a finished season's frame
    # data is as fixed as its scores, and re-walking it every run buys nothing.
    seasons = a.season or [current_season()]
    qmarks = ",".join("?" * len(seasons))
    rows = con.execute(f"""
        SELECT m.match_id, m.played_at, m.hall, m.home, m.away
        FROM bits_match m
        WHERE m.season IN ({qmarks}) AND m.has_been_played = 1
        ORDER BY m.played_at
    """, seasons).fetchall()

    # A match that came back empty is normally final -- the alley does not run
    # Bowlit and never will. But an empty answer for a match played *today* is
    # more likely to mean the sheet has not been published yet, and the old
    # rule ("in social_fetch at all -> never ask again") made that permanent:
    # one eager run and the frame data was lost until someone noticed and
    # passed --refetch. So an empty stays open for a while, then settles.
    if a.refetch:
        done = set()
    else:
        done = {r[0] for r in con.execute(f"""
            SELECT sf.match_id
            FROM social_fetch sf
            LEFT JOIN bits_match m ON m.match_id = sf.match_id
            WHERE sf.games > 0
               OR m.played_at IS NULL
               OR julianday('now') - julianday(m.played_at) > {RETRY_DAYS}
        """)}
    hit = miss = 0
    for r in rows:
        alley = ALLEYS.get(r["hall"])
        if not alley or r["match_id"] in done:
            continue
        try:
            n = ingest(con, r["match_id"], alley)
        except Exception as e:
            print(f"  {r['played_at'][:10]} {r['match_id']}: ERROR {e}")
            continue
        if n:
            hit += 1
            print(f"  {r['played_at'][:10]} {r['home']} - {r['away']}: {n} games")
        else:
            miss += 1
    print(f"\n{hit} matches with frame data, {miss} empty")


if __name__ == "__main__":
    main()
