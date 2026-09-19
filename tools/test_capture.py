"""Exercise the scoring.se capture-and-decode chain on a match of our own.

A dry run for a hall, using whatever is being bowled there rather than waiting
for a LUMA fixture. It answers the three questions that decide whether the
next real match at that hall will come out right:

  1. did the capture survive the whole match, or stop early
  2. can a layout be picked for each lane
  3. do the decoded balls re-score to the totals printed on the board

Attribution is deliberately not part of it. Lining a card up with a player
needs that player's BITS scores, and these are other clubs' bowlers -- their
names and games are not ours to copy into a database that backs a public site.
The arithmetic check is the one that actually tests the decoder anyway: a card
that re-scores to its own printed totals has been read correctly whoever threw
it. Where BITS publishes the match pinfall, that is used as an independent
check on the total, which needs no names at all.

    python tools/test_capture.py --alley 524 --day 2026-09-20 \
        --from 08:50 --to 13:00 --lanes 5-12 --match 3311376 --match 3311377
"""
import argparse
import io
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "collector"))

from PIL import Image                                          # noqa: E402

import board                                                   # noqa: E402
import decode_balls                                            # noqa: E402
from decode_scoring import read_cell, load_templates as digit_templates  # noqa: E402
from ingest_scoring import split_games                         # noqa: E402
from store import connect                                      # noqa: E402


def capture_health(con, slug, day, t0, t1):
    rows = con.execute(
        """SELECT ts, lane FROM capture WHERE slug = ? AND ts BETWEEN ? AND ?
           ORDER BY ts""", (slug, f"{day}T{t0}", f"{day}T{t1}")).fetchall()
    if not rows:
        return None
    stamps = sorted({r["ts"] for r in rows})
    gaps = []
    for a, b in zip(stamps, stamps[1:]):
        d = (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds()
        if d > 180:
            gaps.append((a[11:19], b[11:19], int(d)))
    return {"n": len(rows), "first": stamps[0][11:19], "last": stamps[-1][11:19],
            "lanes": Counter(r["lane"] for r in rows), "gaps": gaps,
            "minutes": (datetime.fromisoformat(stamps[-1])
                        - datetime.fromisoformat(stamps[0])).total_seconds() / 60}


def run(con, alley, day, t0, t1, lanes, match_ids):
    slug = f"scoring:{alley}"
    print(f"== fangst, {slug}, {day} {t0}-{t1} ==")
    h = capture_health(con, slug, day, t0, t1)
    if not h:
        print("   inga bilder alls -- fangsten kordes inte")
        return 1
    print(f"   {h['n']} bilder, {h['first']}-{h['last']} ({h['minutes']:.0f} min)")
    print(f"   banor: " + ", ".join(f"{k}:{v}" for k, v in sorted(h["lanes"].items())))
    if h["gaps"]:
        print(f"   {len(h['gaps'])} avbrott over 3 min:")
        for a, b, d in h["gaps"][:6]:
            print(f"      {a} -> {b}  ({d}s)")
    else:
        print("   inga avbrott over 3 min")

    Vd, Ld = digit_templates()
    Vb, Lb = decode_balls.load_templates()
    print()
    print("== avkodning, bana for bana ==")
    tot_cards = tot_ok = 0
    per_lane = {}
    for lane in lanes:
        rows = con.execute(
            """SELECT ts, png FROM capture WHERE slug = ? AND lane = ?
                 AND ts BETWEEN ? AND ? ORDER BY ts""",
            (slug, lane, f"{day}T{t0}", f"{day}T{t1}")).fetchall()
        if not rows:
            print(f"   bana {lane:>2}: inga bilder")
            continue
        seq = [(r["ts"], Image.open(io.BytesIO(r["png"]))) for r in rows]

        def read_row(im, band):
            return [read_cell(im, "p1", f, Vd, Ld, {"p1": band}) for f in range(10)]

        name, _ = board.pick_layout_for_series([im for _, im in seq], read_row)
        if not name:
            print(f"   bana {lane:>2}: {len(seq):>4} bilder, INGEN LAYOUT")
            per_lane[lane] = (0, 0)
            continue
        cards = ok = 0
        scores = []
        for ci, (ball_band, tot_band) in enumerate(board.LAYOUTS[name]):
            for game in split_games(seq, read_row, tot_band):
                cards += 1
                best = None
                for ts, im, printed in game:
                    frames = decode_balls.read_frames(im, ball_band, Vb, Lb)
                    hcp = decode_balls.infer_handicap(frames, printed)
                    good, _, n = decode_balls.agrees(frames, printed, hcp or 0)
                    if good and n >= 5 and (best is None or n > best[1]):
                        best = (frames, n)
                if best:
                    ok += 1
                    totals = [t for t in decode_balls.score(best[0]) if t is not None]
                    if totals:
                        scores.append(totals[-1])
        tot_cards += cards
        tot_ok += ok
        per_lane[lane] = (ok, cards)
        pct = 100.0 * ok / cards if cards else 0
        print(f"   bana {lane:>2}: {len(seq):>4} bilder, layout {name:<10} "
              f"{ok:>2}/{cards:<2} kort verifierade ({pct:>3.0f}%)"
              + (f"  summa {sum(scores)}" if scores else ""))

    print()
    print(f"== totalt: {tot_ok} av {tot_cards} kort verifierade "
          f"({100.0*tot_ok/tot_cards if tot_cards else 0:.0f}%) ==")

    if match_ids:
        print()
        print("== mot BITS matchsummor (ingen spelardata lagras) ==")
        from bits import Bits
        rows = Bits().get("ListMatches", seasonId=2026, hallId=735)
        rows = rows if isinstance(rows, list) else rows.get("data", rows)
        by = {r["matchId"]: r for r in rows}
        for mid in match_ids:
            m = by.get(mid)
            if not m:
                print(f"   {mid}: finns inte i BITS-svaret")
                continue
            hs, aws = m.get("matchHomeTeamScore"), m.get("matchAwayTeamScore")
            print(f"   {mid} {m.get('matchVsTeams','')[:40]:<42} "
                  f"BITS {hs}+{aws} = {(hs or 0)+(aws or 0)} kglor")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--alley", type=int, default=524)
    ap.add_argument("--day", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--from", dest="t0", default="00:00")
    ap.add_argument("--to", dest="t1", default="23:59")
    ap.add_argument("--lanes", default="5-12")
    ap.add_argument("--match", type=int, action="append", default=[])
    a = ap.parse_args()
    lo, hi = (int(x) for x in a.lanes.split("-"))
    return run(connect(), a.alley, a.day, a.t0, a.t1, range(lo, hi + 1), a.match)


if __name__ == "__main__":
    sys.exit(main())
