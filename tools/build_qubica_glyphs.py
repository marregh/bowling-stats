"""Build the glyph templates the QubicaAMF decoder classifies against.

Every glyph on every captured board is cut out, normalised and clustered by
shape. A cluster is one character, so the whole alphabet can be labelled by
hand in a few dozen decisions rather than thousands -- and because the cutting
is the same code the decoder uses, a glyph that clusters here is a glyph the
decoder can see.

Labels are not guessed. They come from `--label`, a comma-separated list in
cluster order, which the operator reads off the printed contact sheet. The
first run writes the sheet and stops; the second, with labels, writes the
templates.

    python tools/build_qubica_glyphs.py --sheet
    python tools/build_qubica_glyphs.py --label "1,2,X,-,/,..."
"""
import argparse
import io
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "collector"))

import decode_qubica as dq                                    # noqa: E402
from store import connect                                     # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "collector" / "qubica_glyphs.npz"


def harvest(con, day, center, limit, lanes=None, t0="00:00", t1="23:59"):
    """Every distinct glyph image on the boards, with where it came from.

    Restricted to a lane set and a time window on purpose. The same URL serves
    a scoresheet during a match and something else entirely outside one -- a
    team list at warm-up, open-play names afterwards -- and harvesting those
    filled the alphabet with letters from names that the decoder will never be
    asked to read.
    """
    q = """SELECT png FROM capture WHERE slug = ? AND substr(ts,1,10) = ?
             AND ts BETWEEN ? AND ?"""
    args = [f"qubica:{center}", day, f"{day}T{t0}", f"{day}T{t1}"]
    if lanes:
        q += " AND lane IN (" + ",".join("?" * len(lanes)) + ")"
        args += list(lanes)
    q += " ORDER BY ts"
    rows = con.execute(q, args).fetchall()
    if limit:
        rows = rows[:limit]
    out = []
    skipped = 0
    for r in rows:
        im = Image.open(io.BytesIO(r["png"]))
        if dq.overlaid(im):
            skipped += 1
            continue
        for ball_row, tot_row in dq.ROWS:
            for f in range(10):
                for box in dq.ball_boxes(f):
                    g, ring = dq.glyph_in(im, box, ball_row)
                    if g is not None and g.size[0] >= 3 and g.size[1] >= 5:
                        out.append((dq._vec(g), g, ring))
            # digits in the totals row, split into single characters
            for f in range(10):
                if f < 9:
                    x0 = int(dq.FRAME_X0 + dq.FRAME_W * f)
                    x1 = int(dq.FRAME_X0 + dq.FRAME_W * (f + 1))
                else:
                    x0, x1 = dq.TENTH_X0, dq.TENTH_X1
                for g in split_digits(im, (x0, x1), tot_row):
                    out.append((dq._vec(g), g, False))
        # The pinfall and pinfall+handicap columns too. Their digits are twice
        # the height of the ones in the frames and a different weight, so a
        # template set built only from the frames classified them at distances
        # of 2.1 to 4.8 -- near the cutoff for the ones it got right, and past
        # it for the ones it dropped.
        for i, big in enumerate(dq.TOTALS_Y):
            for xa, xb in ((dq.TOTALS_X[0], dq.TOTALS_X[1]),
                           (dq.TOTALS_X[1], dq.TOTALS_X[2])):
                for g in split_digits(im, (xa, xb), big):
                    out.append((dq._vec(g), g, False))
    if skipped:
        print(f"  hoppade over {skipped} tavlor med meddelanderuta")
    return out


def split_digits(im, xr, row):
    x0, x1 = xr
    sub = np.asarray(im.crop((x0, row[0], x1, row[1])).convert("L"), dtype=float)
    mask = sub > dq.INK
    cols = mask.any(axis=0)
    runs, inr = [], False
    for i, v in enumerate(cols):
        if v and not inr:
            s, inr = i, True
        elif not v and inr:
            runs.append((s, i)); inr = False
    if inr:
        runs.append((s, len(cols)))
    out = []
    for s, e in runs:
        if e - s < 2:
            continue
        ys = np.nonzero(mask[:, s:e].any(axis=1))[0]
        if len(ys) >= 5:
            out.append(Image.fromarray(
                np.uint8(np.clip(sub[ys.min():ys.max() + 1, s:e], 0, 255))))
    return out


def cluster(items, tol):
    """Greedy nearest-centroid clustering. Returns centroids and members."""
    cents, members = [], []
    for v, g, ring in items:
        if cents:
            d = np.linalg.norm(np.array(cents) - v, axis=1)
            j = int(np.argmin(d))
            if d[j] <= tol:
                members[j].append((v, g, ring))
                cents[j] = np.mean([m[0] for m in members[j]], axis=0)
                continue
        cents.append(v)
        members.append([(v, g, ring)])
    order = np.argsort([-len(m) for m in members])
    return [cents[i] for i in order], [members[i] for i in order]


def sheet(members, path):
    """A contact sheet: one row per cluster, so labels can be read off."""
    cell, pad = 28, 4
    wide = 12
    h = len(members) * (cell + pad) + pad
    w = wide * (cell + pad) + pad + 60
    sh = Image.new("L", (w, h), 0)
    for i, mem in enumerate(members):
        y = pad + i * (cell + pad)
        for j, (_, g, _) in enumerate(mem[:wide]):
            sh.paste(g.resize((cell, cell), Image.NEAREST), (60 + pad + j * (cell + pad), y))
    sh.save(path)
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--day", default="2026-09-20")
    ap.add_argument("--center", default="8615")
    ap.add_argument("--limit", type=int, default=120, help="boards to harvest")
    ap.add_argument("--lanes", default="13-16")
    ap.add_argument("--from", dest="t0", default="11:00")
    ap.add_argument("--to", dest="t1", default="12:45")
    ap.add_argument("--tol", type=float, default=2.2)
    ap.add_argument("--sheet", action="store_true")
    ap.add_argument("--label", help="comma-separated labels, in cluster order")
    a = ap.parse_args()

    con = connect()
    lo, hi = (int(x) for x in a.lanes.split("-"))
    items = harvest(con, a.day, a.center, a.limit, range(lo, hi + 1), a.t0, a.t1)
    print(f"  {len(items)} glyfer skurna")
    cents, members = cluster(items, a.tol)
    print(f"  {len(cents)} kluster")
    for i, m in enumerate(members):
        print(f"     {i:>2}: {len(m):>5} exemplar"
              + ("  (ringad)" if sum(x[2] for x in m) > len(m) * 0.5 else ""))
    if a.sheet or not a.label:
        p = Path(__file__).resolve().parent.parent / "logs" / "qubica_clusters.png"
        p.parent.mkdir(exist_ok=True)
        sheet(members, p)
        print(f"  kontaktkarta: {p}")
        print("  kor igen med --label \"...\" i klusterordning")
        return 0

    labels = [x.strip() for x in a.label.split(",")]
    if len(labels) != len(cents):
        print(f"  {len(labels)} etiketter men {len(cents)} kluster")
        return 2
    keep = [(c, l) for c, l in zip(cents, labels) if l and l != "?"]
    np.savez(OUT, vectors=np.array([c for c, _ in keep], dtype=np.float32),
             labels=np.array([l for _, l in keep]))
    print(f"  skrev {len(keep)} mallar till {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
