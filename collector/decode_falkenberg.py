"""Turn a bowlingscoring.se live feed into social_game rows.

The third source of frame data, after Bowlit and the scoring.se boards, and by
far the easiest of them: no images and no OCR. The hall's own page polls a
public JSON endpoint that carries a full `player_protocol` -- every player,
every serie, every frame, both balls -- and capture_live.py records it verbatim
while the match is on. This module reads what was recorded.

Two fields describe the same throws. `rutor` gives plain pin counts, and `slag`
gives the same numbers with a flag bit kept on:

    rutor  {"r": 6, "b1": 8,   "b2": 1}
    slag   [..., 264, 1, ...]        264 == 256 | 8

Across the 630 frames of the 2026-09-19 match, `slag & 0xFF` reproduced `rutor`
exactly, and the 256 bit appeared only on values 5 to 8, only on a first ball
(or on a fresh rack in the tenth), and converted 18% of the time. That is a
split, so `slag` is what this module reads: it is the only one of the two that
knows about them, and without it every split in the hall would be recorded as
an ordinary miss.

Nothing is written unless the decoded balls re-score to the total the feed
itself prints for that serie, and a card is only attributed to a player whose
name BITS also lists for that match.

    python collector/decode_falkenberg.py --match 3304731 [--write]
    python collector/decode_falkenberg.py --auto --write
"""
import argparse
import json
import sys
from datetime import datetime, timezone

from store import connect

SPLIT_BIT = 256
PINS = 10
# Halls whose live feed capture_live.py records under this source name. The
# endpoint is per-hall (<slug>.bowlingscoring.se), so the slug is the source.
HALLS = {"Falkenbergs Bowlinghall": "falkenberg"}
# bowlingscoring.se numbers its own halls; Falkenberg is 20. Offset well clear
# of the Bowlit and scoring.se alley ids that already appear in social_game.
ALLEY_BASE = 90000


def throws(slag):
    """[b1, b2] x9 + [b1, b2, b3] -> [(pins, split), ...] per frame."""
    if not slag or len(slag) < 21:
        return None
    out = []
    for f in range(9):
        out.append([(v & 0xFF, bool(v & SPLIT_BIT)) for v in slag[f * 2:f * 2 + 2]])
    out.append([(v & 0xFF, bool(v & SPLIT_BIT)) for v in slag[18:21]])
    return out


def tokens(frames):
    """Frames of (pins, split) -> the flat ball list social_game stores.

    A strike in frames 1-9 is padded with a second value in the feed; it is not
    a throw and must not become a "-", or every strike would read as a frame
    with a gutter ball after it.
    """
    out = []

    def push(pins, split, mark=None):
        out.append({"ball": mark or ("-" if pins == 0 else str(pins)),
                    "split": split})

    for i, fr in enumerate(frames):
        (b1, s1) = fr[0]
        if i < 9:
            if b1 == PINS:
                push(b1, s1, "X")
                continue
            b2, s2 = fr[1]
            push(b1, s1)
            push(b2, s2, "/" if b1 + b2 == PINS else None)
        else:
            b2, s2 = fr[1]
            b3, s3 = fr[2] if len(fr) > 2 else (0, False)
            if b1 == PINS:
                push(b1, s1, "X")
                # A strike clears the rack, so the next two are a fresh start:
                # ball three only spares ball two when they share a rack.
                push(b2, s2, "X" if b2 == PINS else None)
                push(b3, s3, "X" if b3 == PINS
                     else ("/" if b2 != PINS and b2 + b3 == PINS else None))
            elif b1 + b2 == PINS:
                push(b1, s1)
                push(b2, s2, "/")
                push(b3, s3, "X" if b3 == PINS else None)
            else:
                push(b1, s1)
                push(b2, s2)
    return out


def score(frames):
    """Standard ten-pin scoring; returns the ten running totals."""
    flat = []
    for i, fr in enumerate(frames):
        if i < 9 and fr[0][0] == PINS:
            flat.append(PINS)
        else:
            flat.extend(p for p, _ in fr)
    totals, run, k = [], 0, 0
    for i in range(10):
        if k >= len(flat):
            return totals
        if flat[k] == PINS:                       # strike
            run += PINS + sum(flat[k + 1:k + 3])
            k += 1
        elif k + 1 < len(flat) and flat[k] + flat[k + 1] == PINS:   # spare
            run += PINS + (flat[k + 2] if k + 2 < len(flat) else 0)
            k += 2
        else:
            run += sum(flat[k:k + 2])
            k += 2
        totals.append(run)
    return totals


def snapshots(con, day=None, ts=None):
    """Every Falkenberg feed recorded that day, oldest first.

    All of them, not the best one. Picking a single snapshot was the first
    attempt and it was wrong twice over: the newest can be a crash away from
    complete, and the longest is not the most complete either -- the 11:54 body
    of 2026-09-19 is bigger than the 12:00:39 body yet carries less of serie 4.
    Since every card is verified on its own, the union across snapshots is
    strictly better than any one of them.
    """
    q = "SELECT ts, body FROM capture_raw WHERE source = 'falkenberg'"
    args = []
    if ts:
        q += " AND ts = ?"
        args.append(ts)
    elif day:
        q += " AND substr(ts,1,10) = ?"
        args.append(day)
    q += " ORDER BY ts"
    for row in con.execute(q, args):
        yield row["ts"], json.loads(row["body"])


def collect(con, day, ts=None):
    """The best verified card for each (player, serie) across every snapshot.

    Keyed so a player the feed lists twice -- Snapp appears under two roster
    entries in the 2026-09-19 protocol -- contributes one card per serie rather
    than scoring twice.
    """
    best, skipped, seen = {}, {}, 0
    for stamp, feed in snapshots(con, day, ts):
        seen += 1
        got, bad = cards(feed)
        for c in got:
            # Latest wins, not first. Two verified readings of the same card
            # agree on the score but not on the splits: the hall back-fills the
            # split flag minutes after the frame is thrown -- Assarsson's serie
            # 3 frame 8 was a plain "8, 1" at 11:21 and became "264, 1" at
            # 11:57. Keeping the first reading scored every game correctly and
            # threw away almost every split in the match.
            c["ts"] = stamp
            best[(c["player"], c["serie"])] = c
        for who, serie, why in bad:
            skipped.setdefault((who, serie), why)
    # A card that was verified from some snapshot is not a failure, however
    # many earlier ones caught it half-thrown.
    skipped = {k: v for k, v in skipped.items() if k not in best}
    return list(best.values()), skipped, seen


def cards(feed):
    """Every verified (player, serie) card in the feed."""
    out, skipped = [], []
    for m in feed.get("banp_matches") or []:
        pp = m.get("player_protocol") or {}
        for side in ("home", "away"):
            for pl in pp.get(side) or []:
                for se in pl.get("series") or []:
                    fr = throws(se.get("slag"))
                    if not fr:
                        skipped.append((pl["namn"], se.get("serie"), "inga slag"))
                        continue
                    totals = score(fr)
                    want = se.get("scratch")
                    # A serie not yet thrown is all zeros with a scratch of 0,
                    # and scoring it gives 0 -- which "agrees" perfectly. That
                    # is the one way this check can pass while knowing nothing.
                    if not want:
                        skipped.append((pl["namn"], se.get("serie"), "inte spelad"))
                        continue
                    if len(totals) < 10 or totals[-1] != want:
                        skipped.append((pl["namn"], se.get("serie"),
                                        f"summerar till {totals[-1] if totals else '-'}"
                                        f", tavlan sager {want}"))
                        continue
                    out.append(dict(player=pl["namn"], team=pl.get("team_name"),
                                    side=side, serie=se["serie"], lane=se.get("lane"),
                                    slot=se.get("lane_slot"), score=totals[-1],
                                    balls=tokens(fr), totals=totals,
                                    hall=(feed.get("hall") or {}).get("id")))
    return out, skipped


def bits_games(con, match_id):
    """{BITS name: [their game scores, in order]}.

    BITS numbers games per player, not per serie: a substitute who came in for
    the third serie has that game as their g1. So the serie number on a card
    cannot be used as the game number, and the scores are what line the two
    up.
    """
    out = {}
    for r in con.execute("SELECT * FROM bits_result WHERE match_id = ?", (match_id,)):
        out[r["player"]] = [g for g in (r["g1"], r["g2"], r["g3"], r["g4"]) if g]
    return out


def attribute(by_player, published):
    """Keep only players whose whole card set agrees with BITS, game for game.

    Not "this score appears somewhere in BITS": a player's cards in serie order
    must equal their published games in order, same length, same values. That
    settles substitutes without guessing, and it refuses the case that actually
    matters -- a roster the feed lists twice under one name, where the extra
    card would otherwise be attributed to someone who never threw it.
    """
    kept, refused, trimmed = {}, [], []
    for who, cs in by_player.items():
        cs = sorted(cs, key=lambda c: c["serie"])
        mine = [c["score"] for c in cs]
        theirs = published.get(flip(who))
        if theirs is None:
            refused.append((who, mine, None, "namnet finns inte i BITS"))
            continue
        if mine == theirs:
            take = cs
        elif len(mine) > len(theirs) and mine[:len(theirs)] == theirs:
            # The feed keeps naming a lane slot after the player who started
            # in it, so a substituted bowler's later games appear under the
            # original name as well as their own. The leading games are still
            # that player's own and BITS confirms every one of them; the tail
            # belongs to whoever came in, and is attributed to them by their
            # own card. Keeping the prefix saves two real games per
            # substitution that refusing the whole player would have lost.
            take = cs[:len(theirs)]
            trimmed.append((who, mine, theirs))
        else:
            refused.append((who, mine, theirs, "spelen stammer inte med BITS"))
            continue
        for n, c in enumerate(take, start=1):
            c["game"] = n                # the player's nth game, as BITS counts
        kept[who] = take
    return kept, refused, trimmed


def flip(name):
    """"Lindqvist, Magnus" -> "Magnus Lindqvist", which is how BITS writes it."""
    if "," not in name:
        return name
    last, first = (p.strip() for p in name.split(",", 1))
    return f"{first} {last}"


def candidates(con):
    """Played matches at a feed hall whose frames we hold but have not read."""
    out = []
    for m in con.execute("""SELECT * FROM bits_match WHERE has_been_played = 1
                            AND (home LIKE 'Lunds BK Mamba%'
                                 OR away LIKE 'Lunds BK Mamba%')"""):
        source = HALLS.get(m["hall"])
        if not source:
            continue
        if con.execute("SELECT 1 FROM social_game WHERE match_id = ? LIMIT 1",
                       (m["match_id"],)).fetchone():
            continue
        day = (m["played_at"] or "")[:10]
        n = con.execute("""SELECT COUNT(*) FROM capture_raw
                           WHERE source = ? AND substr(ts,1,10) = ?""",
                        (source, day)).fetchone()[0]
        if n:
            out.append((m, day, n))
    return out


def ingest_one(con, m, day=None, ts=None, write=False):
    """Decode and optionally store one match. Returns how many cards were kept."""
    day = day or (m["played_at"] or "")[:10]
    got, skipped, seen = collect(con, day, ts)
    if not seen:
        print(f"  ingen fangst for {day}")
        return 0
    print(f"  {seen} tavlor fran {day}  ({m['home']} - {m['away']})")

    published = bits_games(con, m["match_id"])
    if not published:
        print("    BITS har inga spelarresultat an -- kan inte tillskriva korten")
        return 0
    alley = ALLEY_BASE + (got[0]["hall"] if got and got[0]["hall"] else 0)

    by = {}
    for c in got:
        by.setdefault(c["player"], []).append(c)
    kept, refused, trimmed = attribute(by, published)

    print(f"    {len(got)} verifierade kort, {len(skipped)} som aldrig gick ihop")
    for (who, serie), why in list(skipped.items())[:10]:
        print(f"      {who} serie {serie}: {why}")
    if trimmed:
        print(f"    {len(trimmed)} spelare avbytta -- behaller de spel BITS bekraftar:")
        for who, mine, theirs in trimmed:
            print(f"      {who:<24} tavlan {mine}  BITS {theirs}")
    if refused:
        print(f"    {len(refused)} spelare avvisade:")
        for who, mine, theirs, why in refused:
            print(f"      {who:<24} tavlan {mine}  BITS {theirs}  -- {why}")

    keep = [c for cs in kept.values() for c in cs]
    print(f"    {len(keep)} kort bekraftade mot BITS, {len(kept)} spelare")
    for who in sorted(kept):
        cs = kept[who]
        line = "  ".join(f"s{c['serie']}={c['score']}" for c in cs)
        splits = sum(1 for c in cs for b in c["balls"] if b["split"])
        print(f"      {who:<26} {line}   summa {sum(c['score'] for c in cs):>4}"
              f"  {splits} splittar")

    if write and keep:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        con.execute("DELETE FROM social_game WHERE match_id = ? AND alley_id = ?",
                    (m["match_id"], alley))
        for c in keep:
            con.execute("INSERT OR REPLACE INTO social_game VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (m["match_id"], alley, c["slot"] or 0, c["lane"], c["game"],
                         c["player"], None, c["totals"][-1], c["score"], 1,
                         json.dumps(c["balls"], ensure_ascii=False),
                         json.dumps(c["totals"])))
        con.execute("INSERT OR REPLACE INTO social_fetch VALUES (?,?,?,?)",
                    (m["match_id"], alley, now, len(keep)))
        con.commit()
    return len(keep)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--match", type=int)
    ap.add_argument("--auto", action="store_true",
                    help="every played match at a feed hall that has captures "
                         "but no frames yet")
    ap.add_argument("--day", help="YYYY-MM-DD; defaults to the match date")
    ap.add_argument("--ts", help="one exact capture timestamp")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()

    con = connect()
    if a.auto:
        found = candidates(con)
        if not found:
            print("  inga matcher med ofangade ramar")
            return 0
        total = sum(ingest_one(con, m, day, None, a.write) for m, day, _ in found)
    elif a.match:
        m = con.execute("SELECT * FROM bits_match WHERE match_id = ?",
                        (a.match,)).fetchone()
        if not m:
            print(f"match {a.match} finns inte i bits_match")
            return 1
        total = ingest_one(con, m, a.day, a.ts, a.write)
    else:
        print("ange --match eller --auto")
        return 2
    print()
    print(f"  {total} spel {'skrivna' if a.write else 'skulle skrivas'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
