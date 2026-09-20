"""Turn decoded QubicaAMF boards into social_game rows.

Attribution works the way it does for every other board source: a card is
claimed only when its score matches exactly one of the games BITS publishes
for exactly one player. Nothing here reads names off the board -- it cannot,
the name column is truncated to four characters -- so the scores do the
identifying, and where they do not identify uniquely, nothing is written.

The handicap helps here in a way it does not elsewhere. This board prints it,
so a card carries its own, and two players who happen to shoot the same score
in the same serie are still told apart when their handicaps differ.

    python collector/ingest_qubica.py --match 3315355 --center 8615 \
        --lanes 13-16 --from 10:55 --to 12:45 [--write]
"""
import argparse
import json
import sys
from datetime import datetime, timezone

import decode_qubica as dq
from store import connect

ALLEY_BASE = 95000              # clear of Bowlit, scoring.se and bowlingscoring


def bits_games(con, match_id):
    """{player: {game score: [game numbers]}}, plus their handicap."""
    out = {}
    for r in con.execute("SELECT * FROM bits_result WHERE match_id = ?", (match_id,)):
        games = {}
        for i, g in enumerate((r["g1"], r["g2"], r["g3"], r["g4"]), start=1):
            if g:
                games.setdefault(g, []).append(i)
        out[r["player"]] = {"games": games, "hcp": r["hcp"] or 0, "side": r["side"]}
    return out


def attribute(cards, published):
    """Claim each card for the one player whose published games it can be."""
    claimed, ambiguous = {}, []
    for key, c in sorted(cards.items()):
        hits = [(who, n) for who, d in published.items()
                for n in d["games"].get(c["score"], [])]
        if len(hits) > 1:
            # Two ways a score can be shared, and each has its own answer.
            #
            # Different players with the same score: the handicap tells them
            # apart, and this board is the only one that prints it.
            if c["hcp"] is not None:
                narrowed = [h for h in hits if published[h[0]]["hcp"] == c["hcp"]]
                if len(narrowed) == 1:
                    hits = narrowed
            # One player with the same score twice -- Lindell shot 135 in the
            # first serie and again in the fourth. For someone who bowled
            # every serie the game number is the serie number, so the card
            # says which of the two it is.
            if len(hits) > 1 and len({h[0] for h in hits}) == 1:
                who = hits[0][0]
                if len(published[who]["games"]) and sum(
                        len(v) for v in published[who]["games"].values()) == 4:
                    hits = [(who, c["serie"])]
        if len(hits) != 1:
            ambiguous.append((key, c["score"], len(hits)))
            continue
        who, game = hits[0]
        # One player cannot bowl the same game twice. A repeat means the score
        # was not the unique key it looked like.
        if (who, game) in claimed:
            ambiguous.append((key, c["score"], 2))
            continue
        claimed[(who, game)] = c
    return claimed, ambiguous


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--match", type=int, required=True)
    ap.add_argument("--center", default="8615")
    ap.add_argument("--lanes", default="13-16")
    ap.add_argument("--day")
    ap.add_argument("--from", dest="t0", default="00:00")
    ap.add_argument("--to", dest="t1", default="23:59")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()

    con = connect()
    m = con.execute("SELECT * FROM bits_match WHERE match_id = ?", (a.match,)).fetchone()
    if not m:
        print(f"match {a.match} finns inte")
        return 1
    published = bits_games(con, a.match)
    if not published:
        print("BITS har inga spelarresultat an -- kan inte tillskriva korten")
        return 1

    V, labels = dq.load_templates()
    if V is None:
        print("inga glyfmallar -- kor tools/build_qubica_glyphs.py")
        return 1
    day = a.day or (m["played_at"] or "")[:10]
    lo, hi = (int(x) for x in a.lanes.split("-"))
    found, partial = dq.cards(con, day, a.center, list(range(lo, hi + 1)),
                              a.t0, a.t1, V, labels)
    print(f"  {len(found)} kompletta kort, {len(partial)} som aldrig blev fardiga")
    for (lane, serie, row), n in sorted(partial.items()):
        print(f"     bana {lane} serie {serie} rad {row}: kom till {n} rutor")

    claimed, ambiguous = attribute(found, published)
    print(f"\n  {len(claimed)} kort tillskrivna, {len(ambiguous)} gick inte att placera")
    for key, sc, n in ambiguous:
        print(f"     bana {key[0]} serie {key[1]} rad {key[2]}: {sc} -> {n} traffar")

    our_side = "H" if m["home"].startswith("Lunds") else "A"
    print(f"\n  {'spelare':<24}{'spel':>5}{'poang':>7}{'hcp':>5}  ramar")
    for (who, game), c in sorted(claimed.items()):
        toks = " ".join("".join(t for t in f if t) or "." for f in c["frames"])
        mark = " *" if published[who]["side"] == our_side else "  "
        print(f"  {who[:22]:<24}{game:>5}{c['score']:>7}{c['hcp'] or 0:>5}{mark} {toks}")
    print(f"\n  * = {'hemmalaget' if m['home'].startswith('Lunds') else 'bortalaget'} (vara)")

    if a.write and claimed:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        alley = ALLEY_BASE + int(a.center)
        con.execute("DELETE FROM social_game WHERE match_id = ? AND alley_id = ?",
                    (a.match, alley))
        for (who, game), c in claimed.items():
            totals = [t for t in dq.score(c["frames"]) if t is not None]
            con.execute("INSERT OR REPLACE INTO social_game VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (a.match, alley, c["row"], c["lane"], game, who, None,
                         totals[-1], c["score"], 1,
                         json.dumps(dq.to_balls(c["frames"]), ensure_ascii=False),
                         json.dumps(totals)))
        con.execute("INSERT OR REPLACE INTO social_fetch VALUES (?,?,?,?)",
                    (a.match, alley, now, len(claimed)))
        con.commit()
        print(f"\n  {len(claimed)} spel skrivna")
    elif claimed:
        print(f"\n  {len(claimed)} spel skulle skrivas (kor med --write)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
