"""Mirror the club's BITS data into SQLite. Safe to re-run; upserts throughout."""
import argparse, sys

from bits import Bits, TEAMS
from store import connect


def sync_season(con, api, season, verbose=True):
    matches = api.matches(season) or []
    divisions = set()
    for m in matches:
        divisions.add(m["matchDivisionId"])
        con.execute("""
            INSERT INTO bits_match VALUES
              (:mid,:season,:div_id,:div,:league,:round,:played_at,
               :home_id,:home,:away_id,:away,:home_score,:away_score,
               :home_pts,:away_pts,:hall_id,:hall,:city,:oil,:scheme,:played)
            ON CONFLICT(match_id) DO UPDATE SET
              home_score=excluded.home_score, away_score=excluded.away_score,
              home_pts=excluded.home_pts, away_pts=excluded.away_pts,
              played_at=excluded.played_at, has_been_played=excluded.has_been_played
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
            played=1 if m["matchHasBeenPlayed"] else 0))
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
        have = con.execute("SELECT 1 FROM bits_result WHERE match_id=? LIMIT 1",
                           (m["matchId"],)).fetchone()
        if have:
            continue
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
