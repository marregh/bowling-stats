"""Build a provisional scoresheet from Bowlit, for a match BITS has not posted.

Bowlit publishes when the last ball is thrown; BITS waits for a human. On
2026-09-19 that gap was most of a day, and two home matches sat finished and
invisible while the away match was already up. This fills that window.

What it can and cannot know matters, so it is worth stating plainly. Bowlit
gives names, lanes, every ball and every score -- and *nothing* about teams:
there is no team name anywhere in the page, only `data-name` and `data-lane`.
So the sides are inferred, by the one structural fact the sheets do carry:

    a lane holds two players from the same team

Grouping players by the lanes they shared gives small clusters, one per lane
pair, and each cluster is then labelled by whether anyone in it has ever
appeared for a club team in BITS. Two checks have to pass before anything is
written: no cluster may contain both a known club player and be claimed by the
other side, and the club side must come out at the eight bowlers a league team
fields. Where either fails, nothing is written and the reason is printed.

This is deliberately not written into bits_result. There are no licence
numbers, no handicap and no match points here, and the website says on the page
that it is showing a provisional sheet. When BITS publishes, its rows win and
these become dead weight rather than a conflict.

    python collector/provisional_bowlit.py --match 3306265 [--write]
    python collector/provisional_bowlit.py --auto --write
"""
import argparse
import re
import sys
import unicodedata
from datetime import datetime, timezone

from bits import TEAMS, current_season
from store import connect

TEAM_SIZE = 8


def name_key(n):
    n = unicodedata.normalize("NFKD", (n or "").lower())
    n = "".join(c for c in n if not unicodedata.combining(c))
    return frozenset(re.findall(r"[a-z0-9]+", n))


def club_players(con):
    """Everyone who has ever bowled for a club team, by name key."""
    out = set()
    for r in con.execute("""SELECT r.player, r.side, m.home_id, m.away_id
                            FROM bits_result r
                            JOIN bits_match m ON m.match_id = r.match_id"""):
        if (r["home_id"] if r["side"] == "H" else r["away_id"]) in TEAMS:
            out.add(name_key(r["player"]))
    for r in con.execute("SELECT social_name FROM player_alias WHERE is_club = 1"):
        out.add(name_key(r["social_name"]))
    return out


def clusters(con, match_id):
    """Players grouped by the lanes they shared -- i.e. by team-mate."""
    seen, pairs = set(), []
    for r in con.execute("""SELECT player, lane, game FROM social_game
                            WHERE match_id = ?""", (match_id,)):
        seen.add(r["player"])
    bylane = {}
    for r in con.execute("""SELECT player, lane, game FROM social_game
                            WHERE match_id = ?""", (match_id,)):
        bylane.setdefault((r["game"], r["lane"]), []).append(r["player"])
    for who in bylane.values():
        for other in who[1:]:
            pairs.append((who[0], other))

    parent = {p: p for p in seen}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in pairs:
        parent[find(a)] = find(b)
    out = {}
    for p in seen:
        out.setdefault(find(p), set()).add(p)
    return list(out.values())


def split_sides(con, match_id):
    """(ours, theirs, problems). Empty `ours` means do not write anything."""
    known = club_players(con)
    groups = clusters(con, match_id)
    if not groups:
        return set(), set(), ["ingen Bowlit-data for matchen"]

    ours, theirs, problems = set(), set(), []
    for g in groups:
        hits = {p for p in g if name_key(p) in known}
        (ours if hits else theirs).update(g)

    # A cluster of complete strangers is the dangerous case: it is indis-
    # tinguishable from one of our own lane pairs where neither bowler has a
    # league record yet, and guessing wrong puts an opponent on our team page.
    # The line-up size is what catches it.
    core = core_players(con, match_id)
    n_ours = len(ours & core)
    if n_ours != TEAM_SIZE:
        problems.append(f"{n_ours} egna spelare i grundlaget, vantade {TEAM_SIZE}")
    if len(theirs & core) != TEAM_SIZE:
        problems.append(f"{len(theirs & core)} motstandare i grundlaget, "
                        f"vantade {TEAM_SIZE}")
    return ours, theirs, problems


def core_players(con, match_id):
    """Who bowled the first game -- the starting line-up, before substitutes."""
    return {r["player"] for r in con.execute(
        "SELECT player FROM social_game WHERE match_id = ? AND game = 1", (match_id,))}


def sheet(con, match_id, ours, theirs):
    rows = {}
    for r in con.execute("""SELECT player, game, game_score FROM social_game
                            WHERE match_id = ? ORDER BY player, game""", (match_id,)):
        d = rows.setdefault(r["player"], {"g": {}, "side": None})
        d["g"][r["game"]] = r["game_score"]
    out = []
    for who, d in rows.items():
        gs = [d["g"].get(i) for i in (1, 2, 3, 4)]
        out.append({"player": who, "in_club": who in ours,
                    "games": gs, "series": sum(g for g in gs if g)})
    return sorted(out, key=lambda x: (-x["in_club"], -x["series"]))


def do_match(con, m, write):
    mid = m["match_id"]
    if con.execute("SELECT 1 FROM bits_result WHERE match_id = ? LIMIT 1",
                   (mid,)).fetchone():
        print(f"  {mid}: BITS har redan resultatet -- ror inte")
        return 0
    ours, theirs, problems = split_sides(con, mid)
    print(f"  {m['home']} - {m['away']}  ({m['played_at'][:16]})")
    if problems:
        for p in problems:
            print(f"    KAN INTE: {p}")
        return 0

    home_is_ours = m["home"].startswith("Lunds BK Mamba")
    rows = sheet(con, mid, ours, theirs)
    hs = sum(r["series"] for r in rows if r["in_club"] == home_is_ours)
    aw = sum(r["series"] for r in rows if r["in_club"] != home_is_ours)
    print(f"    {len(ours)} egna, {len(theirs)} motstandare, "
          f"{hs} - {aw} kglor")
    for r in rows:
        side = m["home"] if r["in_club"] == home_is_ours else m["away"]
        print(f"      {r['player']:<26} {str(r['games']):<26} {r['series']:>4}"
              f"   {side[:26]}")

    if write:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        con.execute("DELETE FROM provisional_result WHERE match_id = ?", (mid,))
        for r in rows:
            side = "H" if r["in_club"] == home_is_ours else "A"
            con.execute("INSERT OR REPLACE INTO provisional_result VALUES (?,?,?,?,?,?,?,?)",
                        (mid, r["player"], side, *r["games"], r["series"]))
        con.execute("INSERT OR REPLACE INTO provisional_match VALUES (?,?,?,?,?,?)",
                    (mid, "bowlit", now, hs, aw,
                     "Bowlit-data; BITS har inte publicerat resultatet"))
        con.commit()
        print(f"    skrivet som preliminart")
    return 1


def pending(con):
    """Matches with Bowlit frames, no BITS results, and no provisional sheet."""
    return con.execute("""
        SELECT m.* FROM bits_match m
        WHERE m.season = ?
          AND (m.home LIKE 'Lunds BK Mamba%' OR m.away LIKE 'Lunds BK Mamba%')
          AND EXISTS (SELECT 1 FROM social_game g WHERE g.match_id = m.match_id)
          AND NOT EXISTS (SELECT 1 FROM bits_result r WHERE r.match_id = m.match_id)
        ORDER BY m.played_at
    """, (current_season(),)).fetchall()


def verify(con):
    """Compare every provisional sheet against BITS, once BITS has published.

    The point of showing a provisional result is that it can be checked later,
    so this is the check: same players, same games, same side, same pinfall.
    Anything that disagrees is printed in full -- a provisional sheet that
    turned out wrong is worth knowing about in detail, because the inference
    that produced it will be used again next time.
    """
    rows = con.execute("""SELECT p.match_id FROM provisional_match p
                          WHERE EXISTS (SELECT 1 FROM bits_result r
                                        WHERE r.match_id = p.match_id)
                          ORDER BY p.match_id""").fetchall()
    if not rows:
        print("  inga preliminara resultat som BITS hunnit ikapp")
        return 0
    bad = 0
    for row in rows:
        mid = row["match_id"]
        m = con.execute("SELECT * FROM bits_match WHERE match_id = ?", (mid,)).fetchone()
        print(f"  {m['home']} - {m['away']}")
        mine = {name_key(r["player"]): r for r in
                con.execute("SELECT * FROM provisional_result WHERE match_id = ?", (mid,))}
        theirs = {name_key(r["player"]): r for r in
                  con.execute("SELECT * FROM bits_result WHERE match_id = ?", (mid,))}
        only_mine = set(mine) - set(theirs)
        only_theirs = set(theirs) - set(mine)
        for k in only_mine:
            print(f"    BARA HOS OSS   {mine[k]['player']} ({mine[k]['series']})")
            bad += 1
        for k in only_theirs:
            print(f"    BARA I BITS    {theirs[k]['player']} ({theirs[k]['series']})")
            bad += 1
        for k in set(mine) & set(theirs):
            a, b = mine[k], theirs[k]
            ga = [a["g1"], a["g2"], a["g3"], a["g4"]]
            gb = [b["g1"], b["g2"], b["g3"], b["g4"]]
            note = []
            if [g or 0 for g in ga] != [g or 0 for g in gb]:
                note.append(f"spel {ga} vs {gb}")
            if a["side"] != b["side"]:
                note.append(f"sida {a['side']} vs {b['side']}")
            if note:
                print(f"    SKILJER SIG    {b['player']}: " + "; ".join(note))
                bad += 1
        if not (only_mine or only_theirs):
            same = all(
                [mine[k]["g1"], mine[k]["g2"], mine[k]["g3"], mine[k]["g4"]]
                == [theirs[k]["g1"], theirs[k]["g2"], theirs[k]["g3"], theirs[k]["g4"]]
                and mine[k]["side"] == theirs[k]["side"]
                for k in set(mine) & set(theirs))
            if same:
                print(f"    stammer helt: {len(mine)} spelare, samma spel och sidor")
    print()
    print(f"  {bad} avvikelser" if bad else "  inga avvikelser")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--match", type=int)
    ap.add_argument("--auto", action="store_true")
    ap.add_argument("--verify", action="store_true",
                    help="compare provisional sheets against BITS, once it has "
                         "caught up")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()

    con = connect()
    if a.verify:
        return verify(con)
    if a.match:
        ms = [con.execute("SELECT * FROM bits_match WHERE match_id = ?",
                          (a.match,)).fetchone()]
        if not ms[0]:
            print(f"match {a.match} finns inte")
            return 1
    elif a.auto:
        ms = pending(con)
        if not ms:
            print("  inga matcher som behover ett preliminart resultat")
            return 0
    else:
        print("ange --match eller --auto")
        return 2

    n = sum(do_match(con, m, a.write) for m in ms)
    print()
    print(f"  {n} matcher {'skrivna' if a.write else 'skulle skrivas'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
