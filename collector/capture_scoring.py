"""Capture live scoreboards from scoring.se (Meriq), for halls Bowlit does not cover.

Baltiska in Malmö is the one that matters: 20 of our fixtures a season, and
nothing else reaches it. scoring.se serves one JPEG per lane, regenerated as
play goes on:

    https://scoring.se/{alley}/{session}/{lane}_small.jpg      360x270
    https://scoring.se/{alley}/{session}/{lane}_micro.jpg      240x180

360x270 is the ceiling -- _big, _large, _full and a bare name are all 404, and
the "2 stora skärmar" on the all-lanes page are these same files scaled up in
the browser. Which lanes you ask for changes nothing: lane 5 is the same URL
whether the page is opened at startlane 1, 3 or 5.

**Nothing here is retroactive.** Only today's `showdate` returns images; every
earlier day comes back empty, including last Saturday. So a match not captured
while it is played is gone, exactly as with the old livescoring route. That is
the whole reason this runs as a timed poller rather than a nightly collector.

The board shows only the *current game* -- earlier games' frames are already
gone from it -- so polling has to happen at least once per game.

A league board carries **two players**, one pair per lane (2v2 across four pairs
makes 8v8), so the rows are large and the frames legible. What is actually
wanted is the finished board: ten frames thrown by both. Nothing here can tell a
finished board from a half-played one -- that needs the decoding this is meant
to make possible -- so every distinct state is kept and the complete ones are
picked out afterwards. Two things make that cheap: a new state is roughly one
ball thrown, about twenty per player-pair per game, and the finished board stays
on screen until the machine resets for the next game, which makes it one of the
longest-lived states rather than a flicker.

Images are stored raw and deduplicated by hash. Nothing is decoded: whether the
frames are readable at this size is the open question this is meant to answer,
and a raw capture keeps it answerable later.
"""
import argparse
import hashlib
import re
import sys
import time
import urllib.request
from datetime import datetime

from store import connect

BASE = "https://scoring.se"
BALTISKA = 524
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
IMG = re.compile(r'(\d+)/(\d+)/(\d+)_small\.jpg')


def get(url, timeout=30):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                  timeout=timeout).read()


def today_showdate(alley):
    """The site's own day number for today.

    It is days since 2008-02-23, but that is worked out rather than documented,
    so it is read off the page instead of computed -- the page always carries
    today's value even when asked without one.
    """
    page = get(f"{BASE}/matchloader.asp?alley={alley}&startlane=1").decode("cp1252", "replace")
    m = re.search(r"showdate=(\d+)", page)
    return int(m.group(1)) if m else None


def board(alley, showdate, first_lane):
    """Current {lane: (session, url)} for the eight lanes from first_lane."""
    page = get(f"{BASE}/match.asp?alley={alley}&showdate={showdate}"
               f"&startlane={first_lane}").decode("cp1252", "replace")
    out = {}
    for a, session, lane in IMG.findall(page):
        out[int(lane)] = (int(session), f"{BASE}/{a}/{session}/{lane}_small.jpg")
    return out


def parse_lanes(text):
    m = re.match(r"^\s*(\d+)\s*-\s*(\d+)\s*$", text)
    if m:
        return list(range(int(m.group(1)), int(m.group(2)) + 1))
    return [int(x) for x in re.split(r"[,\s]+", text.strip()) if x]


def cycle(con, alley, showdate, lanes, verbose=True):
    """One sweep. Returns (new, seen)."""
    seen = board(alley, showdate, min(lanes))
    slug = f"scoring:{alley}"
    new = 0
    for lane in lanes:
        if lane not in seen:
            continue
        session, url = seen[lane]
        try:
            blob = get(url, timeout=20)
        except Exception as e:                                  # noqa: BLE001
            if verbose:
                print(f"    bana {lane}: {type(e).__name__} {e}")
            continue
        sha = hashlib.sha256(blob).hexdigest()
        cur = con.execute(
            "INSERT OR IGNORE INTO capture (ts, slug, lane, book_id, game, sha, png)"
            " VALUES (?,?,?,?,?,?,?)",
            (datetime.now().isoformat(timespec="seconds"), slug, lane,
             session, None, sha, blob))
        if cur.rowcount:
            new += 1
    con.commit()
    return new, len(seen)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--alley", type=int, default=BALTISKA)
    ap.add_argument("--lanes", default="5-12",
                    help='"5-12" or "5 6 7 8". Which pair a match uses is in BITS: '
                         'ListMatches gives matchAlleyGroupName ("5 - 12")')
    ap.add_argument("--interval", type=int, default=30,
                    help="seconds between sweeps. Well under a game, and well "
                         "under the gap between the last ball and the reset -- "
                         "that gap is the only chance at a finished board")
    ap.add_argument("--until", help="stop at this local time, HH:MM")
    ap.add_argument("--minutes", type=int, help="stop after this many minutes")
    ap.add_argument("--hours", type=float, default=3.0,
                    help="default stop, in hours. A four-game league match runs "
                         "about 2h10m -- the first capture used the 1h40m the BITS "
                         "schedule implies and stopped during game 3 of 4, missing "
                         "game 4 entirely. The next match's scheduled time on the "
                         "same lane group is nominal, not a real handover.")
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()

    lanes = parse_lanes(a.lanes)
    con = connect()
    showdate = today_showdate(a.alley)
    if showdate is None:
        print("hittade inget showdate — scoring.se svarade inte som väntat")
        return 1

    stop = None
    if a.until:
        h, mi = (int(x) for x in a.until.split(":"))
        stop = datetime.now().replace(hour=h, minute=mi, second=0, microsecond=0)
    elif a.minutes:
        stop = datetime.fromtimestamp(time.time() + a.minutes * 60)
    elif a.hours:
        stop = datetime.fromtimestamp(time.time() + a.hours * 3600)

    print(f"scoring.se hall {a.alley}, banor {lanes[0]}-{lanes[-1]}, "
          f"showdate {showdate}, var {a.interval}s"
          + (f", till {stop:%H:%M}" if stop else ""))

    total = sweeps = 0
    try:
        while True:
            new, seen = cycle(con, a.alley, showdate, lanes)
            total += new
            sweeps += 1
            print(f"  {datetime.now():%H:%M:%S}  {seen} banor på skärmen, "
                  f"{new} nya bilder (totalt {total})", flush=True)
            if a.once:
                break
            if stop and datetime.now() >= stop:
                print("  sluttid nådd")
                break
            time.sleep(a.interval)
    except KeyboardInterrupt:
        print("\n  avbruten")

    held = con.execute("SELECT COUNT(*) FROM capture WHERE slug = ?",
                       (f"scoring:{a.alley}",)).fetchone()[0]
    print(f"{sweeps} svep, {total} nya bilder sparade; {held} totalt för hallen")
    return 0


if __name__ == "__main__":
    sys.exit(main())
