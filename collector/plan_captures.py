"""Work out which upcoming fixtures need their boards photographed.

Everything needed is already in the mirror: the fixture says which hall, BITS
says which lane group, and board.ALLEYS says whether scoring.se covers that
hall. So the capture schedule does not have to be written by hand each week --
which is just as well, because 32 fixtures this season are at halls Bowlit never
reaches, and a board not captured while it is played is gone.

Emits one job per line as JSON; plan_captures.ps1 turns those into scheduled
tasks. Splitting it there keeps the fixture reasoning in Python and leaves
Task Scheduler to PowerShell.

    python collector/plan_captures.py --days 8
"""
import argparse
import json
import re
import sys
from datetime import datetime, timedelta

import board
from store import connect

CLUB = "Lunds BK Mamba%"


def lane_group(text):
    """"5 - 12   " -> (5, 12). BITS pads these; some are a single lane."""
    if not text:
        return None
    nums = [int(x) for x in re.findall(r"\d+", text)]
    if not nums:
        return None
    return (nums[0], nums[-1] if len(nums) > 1 else nums[0])


def jobs(con, days, from_date=None):
    start = from_date or datetime.now().strftime("%Y-%m-%d")
    end = (datetime.strptime(start, "%Y-%m-%d") + timedelta(days=days)).strftime("%Y-%m-%d")
    rows = con.execute("""
        SELECT match_id, played_at, home, away, hall, alley_group
        FROM (SELECT m.*, NULL AS alley_group FROM bits_match m)
        WHERE has_been_played = 0
          AND substr(played_at, 1, 10) BETWEEN ? AND ?
          AND (home LIKE ? OR away LIKE ?)
        ORDER BY played_at
    """, (start, end, CLUB, CLUB)).fetchall()

    out = []
    for r in rows:
        alley = board.ALLEYS.get(r["hall"])
        live = board.LIVE_HALLS.get(r["hall"])
        if not alley and not live:
            continue                      # Bowlit hall, or one we cannot reach
        when = r["played_at"]
        if when.endswith("T00:00:00"):
            continue                      # BITS has no throw-off time yet
        t = datetime.fromisoformat(when)
        begin = t - timedelta(minutes=board.LEAD_MINUTES)
        job = {
            "match_id": r["match_id"],
            "hall": r["hall"],
            "teams": f"{r['home']} - {r['away']}",
            "start": begin.strftime("%Y-%m-%dT%H:%M:00"),
            "until": (t + timedelta(hours=board.MATCH_HOURS)).strftime("%H:%M"),
        }
        if live:
            # A hall with its own feed. Its lanes come from the registry, not
            # from BITS: these sources hand over every lane in one call, and
            # watching all of them costs nothing because the images are only
            # fetched when the hall's own hash changes. BITS had this fixture
            # on lanes 13-16 while the club was told 17-20, and being wrong
            # about that costs the whole match.
            job.update(kind="live", alley=None,
                       name=f"LumaLive{live['source']}_{r['match_id']}",
                       interval=live["interval"],
                       args={k: v for k, v in live.items() if k != "interval"})
        else:
            job.update(kind="scoring", alley=alley,
                       name=f"LumaScoring{alley}_{r['match_id']}",
                       interval=None, lanes=None)   # lanes filled in from BITS
        out.append(job)
    return out


def add_lane_groups(out):
    """The lane group lives in BITS, not in our mirror -- ask for it.

    Through the shared client, not a hand-rolled urlopen: the host is behind an
    antibot gate now, and a bare request gets a "Bot Detection" page with a 200
    on it rather than an error.
    """
    from bits import Bits

    if not out:
        return out
    rows = Bits().matches(2026)
    rows = rows if isinstance(rows, list) else rows.get("data", rows)
    by_id = {r["matchId"]: r for r in rows}
    for job in out:
        # A live-feed hall already knows its lanes, from board.LIVE_HALLS, and
        # they are deliberately the whole house rather than what BITS says.
        if job["kind"] == "live":
            job["lanes"] = job["args"].get("lanes", "-")
            job["from_bits"] = False
            continue
        m = by_id.get(job["match_id"])
        g = lane_group(m.get("matchAlleyGroupName")) if m else None
        # Capture the whole hall when BITS will not say: a lane group that turns
        # out to be wrong costs the entire match, and the extra images are free.
        job["lanes"] = f"{g[0]}-{g[1]}" if g else "1-8"
        job["from_bits"] = bool(g)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=8)
    ap.add_argument("--from-date")
    ap.add_argument("--human", action="store_true", help="readable, not JSON")
    a = ap.parse_args()

    con = connect()
    out = add_lane_groups(jobs(con, a.days, a.from_date))
    if a.human:
        if not out:
            print("  inga matcher att fanga i fonstret")
        for j in out:
            if j["kind"] == "live":
                src = f"{j['args']['source']}, var {j['interval']}s"
            else:
                src = ("BITS" if j["from_bits"]
                       else "hela hallen (BITS saknar bangrupp)")
            print(f"  {j['start'][:16]}  {j['hall']:<24} banor {j['lanes']:<6} "
                  f"till {j['until']}  {j['teams']}   [{src}]")
        return 0
    for j in out:
        print(json.dumps(j, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
