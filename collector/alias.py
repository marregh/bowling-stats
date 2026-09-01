"""Declare who a Bowlit scoreboard name actually is.

Needed for players with no BITS record yet (new to the club) and to stop
first-name-only visitors being guessed onto our roster.
"""
import argparse
from store import connect


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True, help="exactly as Bowlit spells it")
    ap.add_argument("--display", help="name to show (defaults to --name)")
    ap.add_argument("--not-club", action="store_true", help="explicitly not one of ours")
    ap.add_argument("--note", default="")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    con = connect()
    con.execute("INSERT OR REPLACE INTO player_alias VALUES (?,?,?,?)",
                (a.name, a.display or a.name, 0 if a.not_club else 1, a.note))
    con.commit()
    for r in con.execute("SELECT * FROM player_alias ORDER BY social_name"):
        print(f"  {r['social_name']:22s} -> {r['display']:24s} club={r['is_club']}  {r['note']}")


if __name__ == "__main__":
    main()
