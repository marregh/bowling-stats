"""Record a live-scoring source during a match, whatever shape it comes in.

Written the night before the A team played at two halls neither Bowlit nor
scoring.se reaches. The rule that matters is the same as always: none of this
is retroactive. A board not recorded while it is played is gone, so the job
tonight is to *record*, and working out what the bytes mean can wait.

Two sources so far, deliberately kept apart from decoding:

  falkenberg  https://falkenberg.bowlingscoring.se/api/public/hall/<hall>/live
              A public JSON endpoint -- players, matches, competitions -- which
              the hall's own page polls every 30s. No images, no OCR: the
              structured data is simply there, and it is stored verbatim.

  lanetalk    https://scoring.lanetalk.com/upload/<uuid>/VTVFile<lane>.jpg
              One JPEG per lane at 640x480, the same board family as
              scoring.se (X, /, circled splits, HCP/SER/TOT, two cards) but
              nearly four times the pixels. Its API offers only a leaderboard;
              /live is 403. The leaderboard is recorded alongside anyway.

Both dedupe on content, so a board that has not changed is not stored twice.

    python collector/capture_live.py --source falkenberg --hall falkenberg \\
        --until 13:00
    python collector/capture_live.py --source lanetalk --lanes 1-10 \\
        --uuid a7c574b0-222c-11e8-8972-005056a558aa --until 18:00
"""
import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta

from store import connect

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
LANETALK_KEY = "ifqUIAvwExByDEA0NLbqEXN2w8vSef2dQovE"
# QubicaAMF's public board, as used by Olympia Bowling in Helsingborg. One
# JSON call returns every lane pair: the teams, each player's running total
# and their strike/spare/split counters, plus a content hash per lane. The
# hash is the frame-by-frame scoreboard image, fetched separately -- so both
# are recorded, the JSON because it is already structured and the images
# because only they carry the individual balls.
QUBICA_STATUS = ("https://onlinescore.qubicaamf.com/GetLanesStatusView.ashx"
                 "?idcenter={center}")
QUBICA_IMAGE = "https://onlinescore.qubicaamf.com/GetImage.ashx?hash={hash}"
QUBICA_REF = "https://onlinescore.qubicaamf.com/Content.aspx?idcenter={center}"
LANETALK_IMG = "https://scoring.lanetalk.com/upload/{uuid}/VTVFile{lane}.jpg"
LANETALK_API = "https://api.lanetalk.com/v1/bowlingcenters/{uuid}/{what}"
FALKENBERG = "https://{hall}.bowlingscoring.se/api/public/hall/{hall}/live"


def fetch(url, headers=None, timeout=25):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def store_raw(con, source, kind, body):
    sha = hashlib.sha256(body).hexdigest()
    cur = con.execute(
        "INSERT OR IGNORE INTO capture_raw (ts, source, kind, sha, body)"
        " VALUES (?,?,?,?,?)",
        (datetime.now().isoformat(timespec="seconds"), source, kind, sha,
         body.decode("utf-8", "replace")))
    return cur.rowcount


def store_image(con, slug, lane, blob):
    sha = hashlib.sha256(blob).hexdigest()
    cur = con.execute(
        "INSERT OR IGNORE INTO capture (ts, slug, lane, book_id, game, sha, png)"
        " VALUES (?,?,?,?,?,?,?)",
        (datetime.now().isoformat(timespec="seconds"), slug, lane, None, None,
         sha, blob))
    return cur.rowcount


def sweep_falkenberg(con, hall):
    try:
        body = fetch(FALKENBERG.format(hall=hall), {"Accept": "application/json"})
    except Exception as e:                                   # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"
    return store_raw(con, hall, "live", body), None


def sweep_lanetalk(con, uuid, lanes):
    slug = f"lanetalk:{uuid}"
    new = 0
    for lane in lanes:
        try:
            blob = fetch(LANETALK_IMG.format(uuid=uuid, lane=lane))
        except Exception:                                    # noqa: BLE001
            continue
        new += store_image(con, slug, lane, blob)
    try:
        lb = fetch(LANETALK_API.format(uuid=uuid, what="leaderboards"),
                   {"apikey": LANETALK_KEY, "Accept": "application/json"})
        new += store_raw(con, slug, "leaderboard", lb)
    except Exception:                                        # noqa: BLE001
        pass
    return new, None


def sweep_qubica(con, center, lanes):
    """Record the lane-status JSON, and the board image behind every hash.

    Images are keyed by hash, so a board that has not changed is fetched once
    and stored once however long it stays on screen.
    """
    slug = f"qubica:{center}"
    ref = {"Referer": QUBICA_REF.format(center=center)}
    try:
        body = fetch(QUBICA_STATUS.format(center=center),
                     {**ref, "Accept": "application/json"})
    except Exception as e:                                   # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"
    new = store_raw(con, slug, "lanes", body)

    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except ValueError as e:
        return new, f"JSON gick inte att lasa: {e}"

    for pair in data.get("PairItems") or []:
        for side in ("Left", "Right"):
            lane = pair.get(f"{side}Nr")
            if lanes and lane not in lanes:
                continue
            if pair.get(f"{side}ImageStatus") != "Valid":
                continue
            h = pair.get(f"{side}ImageHash")
            if not h:
                continue
            # Already held? The hash is the image's identity, so there is
            # nothing to fetch and nothing to store.
            #
            # Keyed by lane as well, matching the unique index. Two lanes
            # showing a byte-identical board has not been observed -- the
            # player names differ, so the images do -- but without the lane
            # here the second lane's board would be skipped as a duplicate of
            # the first and that lane would simply have a gap.
            if con.execute(
                    "SELECT 1 FROM capture WHERE slug = ? AND lane = ? AND sha = ?",
                    (slug, lane, h)).fetchone():
                continue
            try:
                blob = fetch(QUBICA_IMAGE.format(hash=h), ref)
            except Exception:                                # noqa: BLE001
                continue
            cur = con.execute(
                "INSERT OR IGNORE INTO capture (ts, slug, lane, book_id, game,"
                " sha, png) VALUES (?,?,?,?,?,?,?)",
                (datetime.now().isoformat(timespec="seconds"), slug, lane,
                 None, None, h, blob))
            new += cur.rowcount
    return new, None


def parse_lanes(text):
    if not text:
        return []
    if "-" in text:
        a, b = (int(x) for x in text.split("-"))
        return list(range(a, b + 1))
    return [int(x) for x in text.replace(",", " ").split()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True,
                    choices=("falkenberg", "lanetalk", "qubica"))
    ap.add_argument("--center", help="qubica idcenter")
    ap.add_argument("--hall", default="falkenberg", help="bowlingscoring.se hall slug")
    ap.add_argument("--uuid", help="lanetalk bowling centre uuid")
    ap.add_argument("--lanes", default="1-10")
    ap.add_argument("--interval", type=int, default=30)
    ap.add_argument("--until", help="stop at this local time, HH:MM")
    ap.add_argument("--hours", type=float, default=3.5)
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()

    if a.source == "lanetalk" and not a.uuid:
        print("lanetalk behover --uuid")
        return 2
    if a.source == "qubica" and not a.center:
        print("qubica behover --center")
        return 2

    con = connect()
    lanes = parse_lanes(a.lanes)
    if a.until:
        h, m = (int(x) for x in a.until.split(":"))
        stop = datetime.now().replace(hour=h, minute=m, second=0, microsecond=0)
        if stop < datetime.now():
            stop += timedelta(days=1)
    else:
        stop = datetime.now() + timedelta(hours=a.hours)

    if a.source == "falkenberg":
        what = a.hall
    elif a.source == "qubica":
        what = f"center {a.center} banor {a.lanes}"
    else:
        what = f"{a.uuid[:8]} banor {a.lanes}"
    print(f"{a.source}: {what}, var {a.interval}s, till {stop:%H:%M}")

    total = sweeps = lost = 0
    try:
        while True:
            # Nothing that happens to one sweep may end the recording. A board
            # is only available while it is on screen, so a locked database, a
            # dropped connection or a malformed response costs that sweep and
            # nothing more -- the loop has to still be here 30 seconds later.
            try:
                if a.source == "falkenberg":
                    new, err = sweep_falkenberg(con, a.hall)
                elif a.source == "qubica":
                    new, err = sweep_qubica(con, a.center, lanes)
                else:
                    new, err = sweep_lanetalk(con, a.uuid, lanes)
                con.commit()
            except Exception as e:                           # noqa: BLE001
                new, err, lost = 0, f"{type(e).__name__}: {e}", lost + 1
                try:
                    con.rollback()
                except Exception:                            # noqa: BLE001
                    con = connect()
            total += new
            sweeps += 1
            note = f"  {err}" if err else ""
            print(f"  {datetime.now():%H:%M:%S}  {new} nya (totalt {total}){note}",
                  flush=True)
            if a.once or datetime.now() >= stop:
                break
            time.sleep(a.interval)
    except KeyboardInterrupt:
        print("\n  avbruten")
    if lost:
        print(f"  {lost} svep gick forlorade pa fel")

    print(f"{sweeps} svep, {total} nya poster sparade")
    return 0


if __name__ == "__main__":
    sys.exit(main())
