"""Turn decoded scoring.se boards into social_game rows.

The point of the whole capture-and-decode chain: once the frames are in
social_game they are indistinguishable from Bowlit's, and every statistic the
site already computes -- strikes, spares, splits, single pins, first-ball
average -- works on them without a line of change.

Only fully verified games are written. A board is accepted when scoring its
decoded balls reproduces every printed total on it, and the card is only
attributed to a player when the game score *and* the handicap both match what
BITS publishes for that player in that game. Anything short of that is skipped
and reported, because a wrong name on a real score is worse than a gap.

    python collector/ingest_scoring.py --match 3315357 --alley 497 \
        --lanes 5-8 --from 11:50 --to 12:45 [--write]
"""
import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone

from PIL import Image
import io

import board
import decode_balls
from decode_scoring import read_cell, load_templates as digit_templates
from store import connect


def boards_for(con, slug, lane, day, t0, t1):
    rows = con.execute(
        """SELECT ts, png FROM capture WHERE slug = ? AND lane = ?
             AND ts BETWEEN ? AND ? ORDER BY ts""",
        (slug, lane, f"{day}T{t0}", f"{day}T{t1}")).fetchall()
    return [(r["ts"], Image.open(io.BytesIO(r["png"]))) for r in rows]


def split_games(seq, read_row, band):
    """Group a lane's boards into games: the totals reset when one starts."""
    games, last_first = [[]], None
    for ts, im in seq:
        vals = read_row(im, band)
        first = vals[0]
        if first is not None:
            if last_first is not None and first < last_first:
                games.append([])
            last_first = first
        games[-1].append((ts, im, vals))
    return [g for g in games if g]


def best_board(game, ball_band, Vb, Lb):
    """The most complete verified reading in one game, or None."""
    best = None
    for ts, im, printed in game:
        frames = decode_balls.read_frames(im, ball_band, Vb, Lb)
        hcp = decode_balls.infer_handicap(frames, printed)
        ok, hit, n = decode_balls.agrees(frames, printed, hcp or 0)
        if not ok or n < 5:
            continue
        if best is None or n > best[3]:
            best = (ts, frames, hcp or 0, n)
    return best


def to_balls(frames):
    return [{"ball": m[1:] if m.startswith("s") else m, "split": m.startswith("s")}
            for f in frames for m in f if m not in ("", None)]


def candidates(con):
    """Played matches whose boards we hold but whose frames we have not read.

    A match qualifies when BITS has published it (so players and scores exist to
    attribute against), the hall is one we photograph, captures exist from that
    day, and social_game has nothing for it yet.
    """
    out = []
    for m in con.execute("""SELECT * FROM bits_match WHERE has_been_played = 1
                            AND (home LIKE 'Lunds BK Mamba%' OR away LIKE 'Lunds BK Mamba%')"""):
        alley = board.ALLEYS.get(m["hall"])
        if not alley:
            continue
        if con.execute("SELECT 1 FROM social_game WHERE match_id = ? LIMIT 1",
                       (m["match_id"],)).fetchone():
            continue
        day = (m["played_at"] or "")[:10]
        n = con.execute("""SELECT COUNT(*) FROM capture
                           WHERE slug = ? AND substr(ts,1,10) = ?""",
                        (f"scoring:{alley}", day)).fetchone()[0]
        if n:
            out.append((m, alley, day, n))
    return out


def run_auto(con, write):
    found = candidates(con)
    if not found:
        print("  inga matcher med ofangade ramar")
        return 0
    total = 0
    for m, alley, day, n in found:
        # The whole hall is captured, so the window keeps other matches out.
        t = (m["played_at"] or "")[11:16]
        hh, mm = int(t[:2]), int(t[3:])
        t0 = f"{max(0, hh):02d}:{mm:02d}"
        t1 = f"{min(23, hh + int(board.MATCH_HOURS)):02d}:{mm:02d}"
        print(f"  {m['home']} - {m['away']} ({m['hall']}, {n} bilder) {t0}-{t1}")
        total += ingest_one(con, m, alley, day, t0, t1, None, write)
    print()
    print(f"  {total} spel {'skrivna' if write else 'skulle skrivas'}")
    return 0


def ingest_one(con, m, alley, day, t0, t1, lanes, write):
    """Decode and store one match's boards. Returns how many games were kept."""
    match_id = m["match_id"]
    who = {}
    for r in con.execute("SELECT * FROM bits_result WHERE match_id = ?", (match_id,)):
        hcp = ((r["total"] or 0) - (r["series"] or 0)) // 4
        who[r["player"]] = ([r["g1"], r["g2"], r["g3"], r["g4"]], hcp)
    if not who:
        print("    (BITS har inga spelarresultat an -- hoppar over)")
        return 0

    Vd, Ld = digit_templates()
    Vb, Lb = decode_balls.load_templates()
    slug = f"scoring:{alley}"
    if lanes:
        lo, hi = (int(x) for x in lanes.split("-"))
    else:
        got = con.execute("""SELECT MIN(lane) a, MAX(lane) b FROM capture
                             WHERE slug = ? AND substr(ts,1,10) = ?""",
                          (slug, day)).fetchone()
        lo, hi = got["a"], got["b"]

    rows, skipped = [], []
    for lane in range(lo, hi + 1):
        seq = boards_for(con, slug, lane, day, t0, t1)
        if not seq:
            continue

        def read_row(im, band):
            return [read_cell(im, "p1", f, Vd, Ld, {"p1": band}) for f in range(10)]

        name, _ = board.pick_layout_for_series([im for _, im in seq], read_row)
        if not name:
            skipped.append((lane, None, "ingen layout kunde avgoras"))
            continue

        for ci, (ball_band, tot_band) in enumerate(board.LAYOUTS[name]):
            for gi, game in enumerate(split_games(seq, read_row, tot_band), start=1):
                got = best_board(game, ball_band, Vb, Lb)
                if not got:
                    skipped.append((lane, gi, "ingen tavla kunde verifieras"))
                    continue
                ts, frames, hcp, n = got
                totals = [t for t in decode_balls.score(frames) if t is not None]
                sc = totals[-1] if totals else None
                # Not `gs[gi - 1]`: bowlers change lanes between games, so a
                # lane's third game is not the player's third. The score and the
                # handicap together say who this is and which of their games it
                # was -- and if they do not say it uniquely, nothing is claimed.
                hit = [(p, j + 1) for p, (gs, h) in who.items()
                       for j, g in enumerate(gs) if g == sc and h == hcp]
                if len(hit) != 1:
                    skipped.append((lane, gi, f"spel {sc} hcp {hcp}: "
                                    f"{len(hit)} traffar i BITS"))
                    continue
                player, real_game = hit[0]
                rows.append(dict(match_id=match_id, alley_id=alley, idx=ci, lane=lane,
                                 game=real_game, player=player, score=sc, hcp=hcp,
                                 frames=frames, verified=n, ts=ts))

    for r in rows:
        toks = " ".join("".join(x for x in f if x) for f in r["frames"])
        print(f"    bana {r['lane']:>2} spel {r['game']}  {r['player'][:22]:<24}"
              f"{r['score']:>4}  ({r['verified']}/10)  {toks}")
    for lane, gi, why in skipped:
        print(f"    bana {lane} spel {gi}: {why}")

    if write and rows:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for r in rows:
            totals = decode_balls.score(r["frames"])
            con.execute("""INSERT OR REPLACE INTO social_game VALUES
                           (?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (r["match_id"], r["alley_id"], r["idx"], r["lane"], r["game"],
                         r["player"], None, None, r["score"], 1,
                         json.dumps(to_balls(r["frames"]), ensure_ascii=False),
                         json.dumps([t for t in totals if t is not None])))
        con.execute("INSERT OR REPLACE INTO social_fetch VALUES (?,?,?,?)",
                    (match_id, alley, now, len(rows)))
        con.commit()
    return len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--match", type=int)
    ap.add_argument("--alley", type=int, help="scoring.se alley id")
    ap.add_argument("--lanes", help='"5-8"')
    ap.add_argument("--auto", action="store_true",
                    help="find every played match that has captures and no frames "
                         "yet, and ingest each one")
    ap.add_argument("--day", help="YYYY-MM-DD; defaults to the match date")
    ap.add_argument("--from", dest="t0", default="00:00")
    ap.add_argument("--to", dest="t1", default="23:59")
    ap.add_argument("--write", action="store_true",
                    help="actually write; without it, only report")
    a = ap.parse_args()

    con = connect()
    if a.auto:
        return run_auto(con, a.write)
    if not (a.match and a.alley):
        print("ange --match och --alley (och garna --lanes), eller --auto")
        return 2
    m = con.execute("SELECT * FROM bits_match WHERE match_id = ?", (a.match,)).fetchone()
    if not m:
        print(f"match {a.match} finns inte i bits_match")
        return 1
    day = a.day or (m["played_at"] or "")[:10]
    n = ingest_one(con, m, a.alley, day, a.t0, a.t1, a.lanes, a.write)
    print()
    print(f"  {n} spel {'skrivna' if a.write else 'skulle skrivas'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
