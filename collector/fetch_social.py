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
from store import connect

ALLEYS = {"Lunds Bowlinghall": 1037, "Lerum Pinyard Bowling": 1069}


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
    return y if m >= 7 else y - 1


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

    seasons = a.season or [2025, 2026]
    qmarks = ",".join("?" * len(seasons))
    rows = con.execute(f"""
        SELECT m.match_id, m.played_at, m.hall, m.home, m.away
        FROM bits_match m
        WHERE m.season IN ({qmarks}) AND m.has_been_played = 1
        ORDER BY m.played_at
    """, seasons).fetchall()

    done = {r[0] for r in con.execute("SELECT match_id FROM social_fetch")} if not a.refetch else set()
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
