"""Invariants the shot statistics must satisfy, whatever the ball sequence.

Two columns went wrong the same way before this existed: a numerator counted per
ball against a denominator counted per frame. Neither was visible in the data we
happened to hold -- a perfect game reads 120% strikes under the old maths, and
nobody in the club had bowled one. So these check the arithmetic rather than the
sample, with the tenth frame doing most of the work, since that is where a
frame stops being a single rack.

    python tests/test_frames.py
"""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "collector"))
import frames                                                    # noqa: E402


def B(seq):
    """"7* / 8 1" -> ball dicts. A trailing * marks a split."""
    return [{"ball": t.rstrip("*"), "split": t.endswith("*")} for t in seq.split()]


CASES = {
    "perfekt 300": (
        "X X X X X X X X X X X X",
        dict(strikes=12, racks=12, makable=0, spares=0)),
    "alla spärrar plus bonusklot": (
        # ten single pins left and all ten cleared. The eleventh 9 is the bonus
        # ball earned by the tenth-frame spare: it leaves a pin, but there is no
        # ball left to clear it with, so it is not a chance and not a miss.
        "9 / 9 / 9 / 9 / 9 / 9 / 9 / 9 / 9 / 9 / 9",
        dict(spares=10, makable=10, makable_made=10,
             single_pin=10, single_pin_made=10)),
    "tionde X X 8: sista klotet är varken spärr eller miss": (
        "8 1 8 1 8 1 8 1 8 1 8 1 8 1 8 1 8 1 X X 8",
        dict(strikes=2, racks=12, makable=9, makable_made=0)),
    "enkel i tionde efter strike": (
        "8 1 8 1 8 1 8 1 8 1 8 1 8 1 8 1 8 1 X 9 /",
        dict(single_pin=1, single_pin_made=1, spares=1)),
    "split som räddas räknas inte som maklig": (
        "7* / 8 1 8 1 8 1 8 1 8 1 8 1 8 1 8 1 8 1",
        dict(split_tries=1, split_made=1, makable=9, spares=1)),
    "tionde 9 / 9: bonusklotet är inget spärrläge": (
        "8 1 8 1 8 1 8 1 8 1 8 1 8 1 8 1 8 1 9 / 9",
        dict(spares=1, makable=10, makable_made=1,
             single_pin=1, single_pin_made=1)),
    "tionde X X 9: bonusklotet är inget spärrläge": (
        "8 1 8 1 8 1 8 1 8 1 8 1 8 1 8 1 8 1 X X 9",
        dict(strikes=2, single_pin=0, single_pin_made=0, makable=9)),
    "öppen tionde": (
        "X X X X X X X X X 9 -",
        dict(strikes=9, racks=10, makable=1, makable_made=0)),
}

INVARIANTS = {
    "enkel-lägen har alltid ett klot till att lösas med":
        lambda v: v["single_pin"] <= v["makable"] + v["split_tries"],
    "gjorda + split_gjorda == spärrar":
        lambda v: v["makable_made"] + v["split_made"] == v["spares"],
    "strikes <= rutor":
        lambda v: v["strikes"] <= v["racks"],
    "splitrutor <= rutor":
        lambda v: v["split_racks"] <= v["racks"],
    "enkel gjorda <= enkel lägen":
        lambda v: v["single_pin_made"] <= v["single_pin"],
    "spärrförsök <= rutor":
        lambda v: v["makable"] + v["split_tries"] <= v["racks"],
    "första klot == rutor":
        lambda v: v["first_balls"] == v["racks"],
    "öppna <= ramar":
        lambda v: v["opens"] <= v["frames"],
    "käglor på första klot <= 10 per ruta":
        lambda v: v["first_ball_pins"] <= 10 * v["first_balls"],
}


def main():
    bad = 0
    for name, (seq, want) in CASES.items():
        got = frames.stats(B(seq))
        for k, v in want.items():
            if got[k] != v:
                print(f"  FEL {name}: {k} = {got[k]}, väntade {v}")
                bad += 1
        for label, fn in INVARIANTS.items():
            if not fn(got):
                print(f"  FEL {name}: bryter '{label}'")
                bad += 1
    print(f"  {len(CASES)} konstruerade fall mot {len(INVARIANTS)} invarianter")

    # ...and the same invariants over everything actually collected
    db = Path(__file__).resolve().parent.parent / "data" / "club.db"
    if not db.exists():
        print("  (ingen club.db -- hoppar över den riktiga datan)")
    else:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        per = {}
        for r in con.execute("SELECT match_id, player, balls FROM social_game"):
            k = (r["match_id"], r["player"])
            per[k] = frames.add(per.get(k, {}), frames.stats(json.loads(r["balls"])))
        for label, fn in INVARIANTS.items():
            off = [k[1] for k, v in per.items() if not fn(v)]
            if off:
                print(f"  FEL riktig data bryter '{label}': {len(off)} st, t.ex. {off[0]}")
                bad += len(off)
        print(f"  {len(per)} riktiga spelarmatcher kontrollerade")

    print("  allt stämmer" if not bad else f"  {bad} FEL")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
