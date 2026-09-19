"""Mirror the club's BITS data into SQLite. Safe to re-run; upserts throughout.

Only the current season by default. A finished season cannot change: 2025 sat
at 93 of 93 played and fetched nothing for a week, yet half of every run went
on re-asking for its fixture list and six standings tables. Naming a season
explicitly still works, which is how a finished one gets refreshed if BITS ever
corrects it:

    python collector/fetch_bits.py           # current season
    python collector/fetch_bits.py 2025      # one finished season
    python collector/fetch_bits.py --all     # everything we hold
"""
import argparse, sys
from datetime import datetime, timezone

from bits import Bits, TEAMS, current_season
from store import connect


def expected_games(m):
    """Player-games a complete protocol holds, or None if the scheme is odd.

    players per side x rounds on this many lanes x 2 sides. The rounds figure
    is per lane count -- numberOfRounds8Lanes and so on -- so it has to be
    looked up by the match's own matchNbrOfLanes.
    """
    n = m.get("matchNbrOfPlayers") or 0
    lanes = m.get("matchNbrOfLanes") or 0
    rounds = m.get(f"numberOfRounds{lanes}Lanes") or 0
    return (n * rounds * 2) or None


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
               has_been_played, first_seen_played, expected_games)
            VALUES
              (:mid,:season,:div_id,:div,:league,:round,:played_at,
               :home_id,:home,:away_id,:away,:home_score,:away_score,
               :home_pts,:away_pts,:hall_id,:hall,:city,:oil,:scheme,:played,
               :first_seen,:expected)
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
              expected_games=excluded.expected_games,
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
            expected=expected_games(m),
            first_seen=now if m["matchHasBeenPlayed"] else None))
    con.commit()

    # Every request first, then one short write. Interleaved, the first INSERT
    # opened a write transaction that stayed open across the remaining five
    # GetStandings calls -- so the database was locked against every other
    # process for as long as BITS took to answer all of them. At last week's
    # 8s that was invisible; at today's 280s it is minutes of held lock.
    standings = []
    for div in sorted(divisions):
        for s in api.standing(season, div) or []:
            standings.append((
                season, div, s["standingsTeamId"], s["standingsTeamName"],
                s["standingsMatches"], s["standingsWin"], s["standingsDraw"],
                s["standingsLoss"], s["standingsHomePoints"], s["standingsAwayPoints"],
                s["standingsDiff"], s["standingsPoints"]))
    con.executemany("""INSERT OR REPLACE INTO bits_standing VALUES
        (?,?,?,?,?,?,?,?,?,?,?,?)""", standings)
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
        # Already asked about this exact disagreement and BITS said the same
        # thing? Then asking again is a request spent to learn nothing.
        if have["n"] and con.execute(
                "SELECT 1 FROM bits_result_gap WHERE match_id = ? AND want = ?",
                (m["matchId"], want)).fetchone():
            continue
        if have["n"] and verbose:
            print(f"   {m['matchId']}: {have['pins']} kglor lagrade, BITS sager "
                  f"{want} -- hamtar om", flush=True)
        # Fetch first, delete after. The DELETE used to come before this call,
        # which opened a write transaction and then held it across a request to
        # BITS -- fine at the 8s BITS of last week, ruinous today at 280s: that
        # is a write lock on the whole database for the length of a network
        # round trip, and it is what killed the Falkenberg capture at 12:00:39.
        res = api.match_results(m["matchId"], m["matchSchemeId"]) or {}
        if have["n"]:
            con.execute("DELETE FROM bits_result WHERE match_id = ?", (m["matchId"],))
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
        # Did this fetch actually settle it? If the rows still do not add up to
        # the match score, record the disagreement so the next run recognises
        # it rather than spending another request on the same answer.
        now_pins = con.execute(
            "SELECT COALESCE(SUM(total), 0) FROM bits_result WHERE match_id = ?",
            (m["matchId"],)).fetchone()[0]
        if want and now_pins != want:
            con.execute("INSERT OR REPLACE INTO bits_result_gap VALUES (?,?,?,?)",
                        (m["matchId"], want, now_pins, now))
            if verbose:
                print(f"   {m['matchId']}: BITS ger {now_pins} av {want} kglor "
                      f"-- slutar fraga om just detta", flush=True)
        got += 1
        con.commit()
        if verbose and got % 10 == 0:
            print(f"   results {got}/{len(played)}", flush=True)

    if verbose:
        print(f"season {season}: {len(matches)} matches, {len(played)} played, "
              f"{len(divisions)} divisions, {got} result sets fetched")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("seasons", nargs="*", type=int,
                    help="season ids; defaults to the current one")
    ap.add_argument("--all", action="store_true",
                    help="every season already in the mirror")
    a = ap.parse_args()
    con, api = connect(), Bits()

    if a.seasons:
        seasons = a.seasons
    elif a.all:
        seasons = [r[0] for r in con.execute(
            "SELECT DISTINCT season FROM bits_match ORDER BY season")]
    else:
        seasons = [current_season()]

    for s in seasons:
        sync_season(con, api, s)
    print("teams:", ", ".join(TEAMS.values()))


if __name__ == "__main__":
    main()
