"""Mirror the club's BITS data into SQLite. Safe to re-run; upserts throughout."""
import argparse, sys
from datetime import datetime, timezone

from bits import Bits, TEAMS
from store import connect


def sync_season(con, api, season, verbose=True):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    matches = api.matches(season) or []
    divisions = set()
    for m in matches:
        divisions.add(m["matchDivisionId"])
        # Columns named rather than positional: bits_match has grown a column
        # since, and a bare VALUES list silently shifts everything along.
        con.execute("""
            INSERT INTO bits_match
              (match_id, season, division_id, division, league, round_id,
               played_at, home_id, home, away_id, away, home_score, away_score,
               home_pts, away_pts, hall_id, hall, city, oil_pattern, scheme_id,
               has_been_played, first_seen_played)
            VALUES
              (:mid,:season,:div_id,:div,:league,:round,:played_at,
               :home_id,:home,:away_id,:away,:home_score,:away_score,
               :home_pts,:away_pts,:hall_id,:hall,:city,:oil,:scheme,:played,
               :first_seen)
            -- This table is a mirror, so everything BITS can change is taken
            -- from BITS every run. It used to refresh only the scores, which
            -- meant a fixture kept whatever it said the first time we ever saw
            -- it: a match entered months ahead sat there as "Ingen OljeProfil"
            -- long after the oil pattern was set, and a moved venue or renamed
            -- team would have stuck in exactly the same way.
            ON CONFLICT(match_id) DO UPDATE SET
              season=excluded.season, division_id=excluded.division_id,
              division=excluded.division, league=excluded.league,
              round_id=excluded.round_id, played_at=excluded.played_at,
              home_id=excluded.home_id, home=excluded.home,
              away_id=excluded.away_id, away=excluded.away,
              home_score=excluded.home_score, away_score=excluded.away_score,
              home_pts=excluded.home_pts, away_pts=excluded.away_pts,
              hall_id=excluded.hall_id, hall=excluded.hall, city=excluded.city,
              oil_pattern=excluded.oil_pattern, scheme_id=excluded.scheme_id,
              has_been_played=excluded.has_been_played,
              -- the one column that is ours, not BITS's: stamped once, the
              -- first time BITS admitted the match was played
              first_seen_played=COALESCE(bits_match.first_seen_played,
                                         excluded.first_seen_played)
        """, dict(
            mid=m["matchId"], season=season, div_id=m["matchDivisionId"],
            div=m["matchDivisionName"], league=m["matchLeagueName"],
            round=m["matchRoundId"], played_at=m["matchDateTime"],
            home_id=m["matchHomeTeamId"], home=m["matchHomeTeamAlias"],
            away_id=m["matchAwayTeamId"], away=m["matchAwayTeamAlias"],
            home_score=m["matchHomeTeamScore"], away_score=m["matchAwayTeamScore"],
            home_pts=m["matchHomeTeamResult"], away_pts=m["matchAwayTeamResult"],
            hall_id=m["matchHallId"], hall=m["matchHallName"], city=m["matchHallCity"],
            oil=m["matchOilPatternName"], scheme=m["matchSchemeId"],
            played=1 if m["matchHasBeenPlayed"] else 0,
            first_seen=now if m["matchHasBeenPlayed"] else None))
    con.commit()

    for div in sorted(divisions):
        for s in api.standing(season, div) or []:
            con.execute("""INSERT OR REPLACE INTO bits_standing VALUES
                (?,?,?,?,?,?,?,?,?,?,?,?)""", (
                season, div, s["standingsTeamId"], s["standingsTeamName"],
                s["standingsMatches"], s["standingsWin"], s["standingsDraw"],
                s["standingsLoss"], s["standingsHomePoints"], s["standingsAwayPoints"],
                s["standingsDiff"], s["standingsPoints"]))
    con.commit()

    played = [m for m in matches if m["matchHasBeenPlayed"]]
    got = 0
    for m in played:
        # Having rows is not the same as having the right rows. A protocol can
        # be registered incomplete and corrected later: match 3314110 sat with
        # game 4 missing for all but one bowler, 3211-3314, until someone fixed
        # it to 4368-4374. "Skip if we already have something" would have kept
        # the wrong scoresheet forever, so the stored total has to agree with
        # the match score before we leave it alone.
        have = con.execute("""SELECT COUNT(*) n, COALESCE(SUM(total), 0) pins
                              FROM bits_result WHERE match_id = ?""",
                           (m["matchId"],)).fetchone()
        want = (m["matchHomeTeamScore"] or 0) + (m["matchAwayTeamScore"] or 0)
        if have["n"] and (want == 0 or have["pins"] == want):
            continue
        if have["n"]:
            if verbose:
                print(f"   {m['matchId']}: {have['pins']} kglor lagrade, BITS sager "
                      f"{want} -- hamtar om", flush=True)
            con.execute("DELETE FROM bits_result WHERE match_id = ?", (m["matchId"],))
        res = api.match_results(m["matchId"], m["matchSchemeId"]) or {}
        for side, key in (("H", "playerListHome"), ("A", "playerListAway")):
            for p in res.get(key) or []:
                lic = p.get("licNbr") or p.get("player", "")
                name = (p.get("player") or "").split(" (")[0]
                con.execute("""INSERT OR REPLACE INTO bits_result VALUES
                    (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    m["matchId"], lic, name, side,
                    p.get("result1"), p.get("result2"), p.get("result3"), p.get("result4"),
                    p.get("hcp"), p.get("totalResult"), p.get("totalResultWithoutHcp"),
                    p.get("lanePoint"), p.get("rankPoints"), p.get("place")))
        got += 1
        con.commit()
        if verbose and got % 10 == 0:
            print(f"   results {got}/{len(played)}", flush=True)

    if verbose:
        print(f"season {season}: {len(matches)} matches, {len(played)} played, "
              f"{len(divisions)} divisions, {got} result sets fetched")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("seasons", nargs="*", type=int, default=[2025, 2026])
    a = ap.parse_args()
    con, api = connect(), Bits()
    for s in a.seasons:
        sync_season(con, api, s)
    print("teams:", ", ".join(TEAMS.values()))


if __name__ == "__main__":
    main()
