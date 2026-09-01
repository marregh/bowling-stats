"""Poll a Bowlit alley and store every distinct lane scoresheet.

Capture is deliberately decoupled from decoding: a lane's sheet shows only the
current game and is overwritten when the next booking starts, so anything not
stored while it is on screen is gone for good. Storing raw PNGs means the
decoder can be improved and re-run against history later.
"""
import argparse, base64, hashlib, sys, time
from datetime import datetime, timezone

import requests

from store import connect

URL = "https://livescoring.bowlit.nu/api/getlanes"


def poll(slug):
    r = requests.post(URL, data={"Slug": slug}, timeout=45,
                      headers={"Referer": f"https://livescoring.bowlit.nu/{slug}"})
    r.raise_for_status()
    return r.json().get("lanes", [])


def store(con, slug, lanes):
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    new = 0
    for ln in lanes:
        b64 = ln.get("LaneImageBase64String")
        if not b64:
            continue
        png = base64.b64decode(b64)
        sha = hashlib.sha256(png).hexdigest()
        cur = con.execute(
            "INSERT OR IGNORE INTO capture (ts, slug, lane, book_id, sha, png)"
            " VALUES (?,?,?,?,?,?)",
            (ts, slug, ln.get("Lane"), ln.get("BookId") or None, sha, png))
        new += cur.rowcount
    con.commit()
    return new


def main():
    ap = argparse.ArgumentParser(description="capture Bowlit lane scoresheets")
    ap.add_argument("--slug", default="lunds-bowling")
    ap.add_argument("--interval", type=int, default=75, help="seconds between polls")
    ap.add_argument("--minutes", type=int, default=0, help="stop after N minutes (0 = forever)")
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()

    con = connect()
    deadline = time.time() + a.minutes * 60 if a.minutes else None
    total = 0
    while True:
        try:
            lanes = poll(a.slug)
            n = store(con, a.slug, lanes)
            total += n
            books = sorted({l.get("BookId") for l in lanes if l.get("BookId")})
            print(f"{datetime.now():%H:%M:%S}  {len(lanes)} lanes, {n} new "
                  f"(total {total}), bookings {books}", flush=True)
        except Exception as e:                      # keep the loop alive
            print(f"{datetime.now():%H:%M:%S}  poll failed: {e}", file=sys.stderr, flush=True)
        if a.once or (deadline and time.time() > deadline):
            break
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
