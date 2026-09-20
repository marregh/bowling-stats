"""Read a QubicaAMF board: the frames, the totals and the splits.

The fourth scoring system, and the third that has to be read off pixels. Its
boards come from onlinescore.qubicaamf.com, which capture_live.py records as
512x286 PNGs, one per lane, two players to a board.

The layout, measured off the 2026-09-20 Helsingborg capture:

    y  6- 16   header: "S. n", the frame numbers, "Tot + hdcp"
    y 25- 49   player 1, the balls
    y 59- 77   player 1, the running totals
    y 93-117   player 2, the balls
    y128-145   player 2, the running totals
    x 60-384   frames 1-9, 36px apart, two ball slots each
    x 384-418  frame 10, three ball slots
    x 420-512  pinfall, then pinfall + handicap

Two conventions carried over from the scoring.se boards, because they turn out
to be the same: a strike is a single glyph in the *right* slot with the left
empty, and a split is a digit drawn inside a circle.

What this system gives that the others do not is the handicap, printed beside
the pinfall on every board -- "465 534" is 69 of handicap, and no inference is
needed. On the scoring.se boards that figure had to be guessed, and guessing
it wrong once produced a card that validated against the wrong arithmetic.

Nothing is trusted on its own: a reading is accepted only when scoring the
decoded balls reproduces every running total printed under them.
"""
import argparse
import io
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from store import connect

HERE = Path(__file__).resolve().parent
TEMPLATES = HERE / "qubica_glyphs.npz"

NW, NH = 10, 14
MAX_DIST = 3.2
INK = 140                      # a lit pixel, on this board's dark ground

# Rows, as measured above. Each player is (ball row, totals row).
ROWS = [((25, 50), (58, 78)), ((93, 118), (127, 146))]
SERIE_BOX = (28, 4, 40, 18)    # the digit after "S."
FRAME_X0, FRAME_W, NFRAMES = 60, 36.0, 9
TENTH_X0, TENTH_X1 = 384, 418
# The pinfall and the pinfall-plus-handicap, printed to the right of each
# player. They have their own band: the digits are about twice the height of
# the ones in the frames and are centred across both the ball and totals rows,
# so reading them from the totals row alone clipped them to nothing.
TOTALS_X = (420, 462, 512)     # pinfall | pinfall + handicap
TOTALS_Y = [(37, 79), (105, 147)]


def overlaid(im):
    """Is a message box covering the board?

    QubicaAMF draws two of them over the frames: "BANA I ÖVNINGSLÄGE" while a
    lane is in practice mode, and "SPELSERIEN ÄR SLUT" once play ends. Both
    hide the second player's row completely, and the first is what filled the
    glyph clusters with the letters of the word ÖVNINGSLÄGE the first time the
    templates were built.

    They are found by their border rather than their text: a rounded box drawn
    across the frame area leaves a bright horizontal rule some 380px long,
    where a real board's longest run in that band is a glyph, about 40px.
    """
    a = np.asarray(im.convert("L"), dtype=float)
    for y in range(78, 110):
        row = a[y, 60:460] > 55
        best = cur = 0
        for v in row:
            cur = cur + 1 if v else 0
            if cur > best:
                best = cur
        if best > 150:
            return True
    return False


def ball_boxes(frame):
    """Pixel boxes for the ball slots of one frame, left to right."""
    if frame < 9:
        x0 = FRAME_X0 + FRAME_W * frame
        return [(int(x0), int(x0 + FRAME_W / 2)),
                (int(x0 + FRAME_W / 2), int(x0 + FRAME_W))]
    # Measured, not divided. The tenth frame's three balls sit at 384-391,
    # 394-401 and 403-411, so splitting the cell into equal thirds cut the
    # second and third glyphs in half and lost them: a "X X 4" read as "X".
    return [(382, 392), (392, 402), (402, 414)]


def _vec(g):
    a = np.asarray(g.resize((NW, NH), Image.LANCZOS), dtype=np.float32)
    return ((a - a.min()) / max(1.0, (a.max() - a.min()))).ravel()


def is_split(im, box, row):
    """Is this ball ringed as a split?

    The ring is drawn in red, and that is the whole test. Looking for it in
    the luminance channel finds nothing: red on this dark ground comes out
    around 80, well under the ink threshold, so the first attempt saw no rings
    at all -- and the edge-of-slot heuristic borrowed from the scoring.se
    decoder fired on every glyph tall enough to touch the top of its box,
    which marked most of the match as splits.
    """
    x0, x1 = box
    a = np.asarray(im.crop((x0, row[0], x1, row[1])).convert("RGB"), dtype=int)
    red = ((a[:, :, 0] > 110) & (a[:, :, 0] > a[:, :, 1] + 45)
           & (a[:, :, 0] > a[:, :, 2] + 45))
    return bool(red.sum() >= 20)


def glyph_in(im, box, row):
    """Tightest crop around the ink in one slot, or None if the slot is blank."""
    x0, x1 = box
    y0, y1 = row
    sub = np.asarray(im.crop((x0, y0, x1, y1)).convert("L"), dtype=float)
    mask = sub > INK
    if mask.sum() < 4:
        return None, False
    ys, xs = np.nonzero(mask)
    g = Image.fromarray(
        np.uint8(np.clip(sub[ys.min():ys.max() + 1, xs.min():xs.max() + 1], 0, 255)))
    return g, is_split(im, box, row)


def load_templates(path=None):
    p = Path(path or TEMPLATES)
    if not p.exists():
        return None, None
    z = np.load(p)
    return z["vectors"], [str(x) for x in z["labels"]]


def classify(g, V, labels):
    # A miss is a bare dash, measured at 4x3 on these boards. It never reaches
    # the template set: normalising so few pixels to 10x14 gives a smear that
    # sits near several digits at once, and the glyph harvester skips it for
    # the same reason. Height settles it, and nothing else on this board is
    # under six pixels tall -- every digit measures 18 to 25.
    w, h = g.size
    if h <= 6:
        return "-", 0.0
    v = _vec(g)
    d = np.linalg.norm(V - v, axis=1)
    j = int(np.argmin(d))
    return (labels[j], float(d[j])) if d[j] <= MAX_DIST else (None, float(d[j]))


def read_row_digits(im, row, V, labels):
    """The ten running totals under one player's frames."""
    out = []
    for f in range(10):
        if f < 9:
            x0 = int(FRAME_X0 + FRAME_W * f)
            x1 = int(FRAME_X0 + FRAME_W * (f + 1))
        else:
            x0, x1 = TENTH_X0, TENTH_X1
        sub = np.asarray(im.crop((x0, row[0], x1, row[1])).convert("L"), dtype=float)
        mask = sub > INK
        if mask.sum() < 4:
            out.append(None)
            continue
        out.append(read_number(im, (x0, x1), row, V, labels))
    return out


def read_number(im, xr, row, V, labels):
    """Digits side by side in one cell -> an integer, or None."""
    x0, x1 = xr
    sub = np.asarray(im.crop((x0, row[0], x1, row[1])).convert("L"), dtype=float)
    mask = sub > INK
    cols = mask.any(axis=0)
    runs, inr = [], False
    for i, v in enumerate(cols):
        if v and not inr:
            s, inr = i, True
        elif not v and inr:
            runs.append((s, i)); inr = False
    if inr:
        runs.append((s, len(cols)))
    text = ""
    for s, e in runs:
        if e - s < 2:
            continue
        ys = np.nonzero(mask[:, s:e].any(axis=1))[0]
        if not len(ys):
            continue
        g = Image.fromarray(np.uint8(np.clip(
            sub[ys.min():ys.max() + 1, s:e], 0, 255)))
        lab, _ = classify(g, V, labels)
        if lab and lab.isdigit():
            text += lab
    return int(text) if text else None


def read_balls(im, row, V, labels):
    """Frames of ball tokens. "X", "/", "-", a digit, or "sN" for a split."""
    frames = []
    for f in range(10):
        marks = []
        for box in ball_boxes(f):
            g, ring = glyph_in(im, box, row)
            if g is None:
                marks.append("")
                continue
            lab, _ = classify(g, V, labels)
            marks.append(("s" + lab) if (lab and ring and lab.isdigit()) else (lab or ""))
        frames.append(marks)
    return frames


def pins(tok):
    t = tok[1:] if tok.startswith("s") else tok
    if t == "X":
        return 10
    if t in ("-", "F", ""):
        return 0
    return int(t) if t.isdigit() else 0


def score(frames):
    """Standard scoring; returns the ten running totals it implies."""
    flat = []
    for i, fr in enumerate(frames):
        toks = [t for t in fr if t != ""]
        if i < 9:
            # A strike is drawn in the right slot with the left blank.
            if any(t.lstrip("s") == "X" for t in toks):
                flat.append(10)
                continue
            vals = [pins(t) for t in toks]
            if len(vals) == 1:
                vals.append(0)
            if len(toks) > 1 and toks[1].lstrip("s") == "/":
                vals[1] = 10 - vals[0]
            flat.extend(vals[:2])
        else:
            vals = []
            for t in toks:
                # A spare mark with nothing before it means the first ball of
                # the pair was not read, so there is no count to subtract
                # from. Guessing 10 would invent a strike; leaving the frame
                # short lets the totals check reject the card, which is what
                # should happen.
                if t.lstrip("s") == "/":
                    vals.append(10 - vals[-1] if vals else 0)
                else:
                    vals.append(pins(t))
            flat.extend(vals)
    totals, run, k = [], 0, 0
    for _ in range(10):
        if k >= len(flat):
            return totals
        if flat[k] == 10:
            run += 10 + sum(flat[k + 1:k + 3])
            k += 1
        elif k + 1 < len(flat) and flat[k] + flat[k + 1] == 10:
            run += 10 + (flat[k + 2] if k + 2 < len(flat) else 0)
            k += 2
        else:
            run += sum(flat[k:k + 2])
            k += 2
        totals.append(run)
    return totals


def agrees(frames, printed):
    """Do the decoded balls reproduce the printed running totals?"""
    got = score(frames)
    hit = n = 0
    for a, b in zip(got, printed):
        if b is None:
            continue
        n += 1
        hit += (a == b)
    return (n > 0 and hit == n), hit, n


def board_rows(im, V, labels):
    """Both players on one board: their frames and their printed totals."""
    out = []
    for i, (ball_row, tot_row) in enumerate(ROWS):
        frames = read_balls(im, ball_row, V, labels)
        printed = read_row_digits(im, tot_row, V, labels)
        big = TOTALS_Y[i]
        pin = read_number(im, (TOTALS_X[0], TOTALS_X[1]), big, V, labels)
        tot = read_number(im, (TOTALS_X[1], TOTALS_X[2]), big, V, labels)
        out.append({"frames": frames, "printed": printed,
                    "pins": pin, "total": tot,
                    "hcp": (tot - pin) if (pin is not None and tot is not None) else None})
    return out


def serie_of(im, V, labels):
    x0, y0, x1, y1 = SERIE_BOX
    return read_number(im, (x0, x1), (y0, y1), V, labels)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lane", type=int, required=True)
    ap.add_argument("--day", default="2026-09-20")
    ap.add_argument("--center", default="8615")
    ap.add_argument("--at", help="HH:MM, the board at or before this time")
    a = ap.parse_args()
    V, labels = load_templates()
    if V is None:
        print("inga glyfmallar an -- kor tools/build_qubica_glyphs.py forst")
        return 1
    con = connect()
    q = """SELECT ts, png FROM capture WHERE slug = ? AND lane = ?
             AND substr(ts,1,10) = ?"""
    args = [f"qubica:{a.center}", a.lane, a.day]
    if a.at:
        q += " AND ts <= ?"
        args.append(f"{a.day}T{a.at}")
    q += " ORDER BY ts DESC LIMIT 1"
    r = con.execute(q, args).fetchone()
    if not r:
        print("ingen bild")
        return 1
    im = Image.open(io.BytesIO(r["png"]))
    print(f"bana {a.lane}  {r['ts'][11:19]}  serie {serie_of(im, V, labels)}")
    for row in board_rows(im, V, labels):
        ok, hit, n = agrees(row["frames"], row["printed"])
        toks = " ".join("".join(t for t in f if t) or "." for f in row["frames"])
        print(f"   {'OK ' if ok else 'FEL'} {hit}/{n}  kglor {row['pins']} "
              f"tot {row['total']} hcp {row['hcp']}")
        print(f"        {toks}")
        print(f"        avkodat {score(row['frames'])}")
        print(f"        tavlan  {row['printed']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())


def cards(con, day, center, lanes, t0, t1, V, labels):
    """The best verified reading of every (lane, serie, row) on a day.

    Only complete cards are returned. A board verified at eight of ten frames
    is a correct reading of an unfinished serie, not a card -- the frames that
    are missing are missing because the board moved on before showing them.
    """
    best, partial = {}, {}
    for r in con.execute(
            """SELECT ts, lane, png FROM capture WHERE slug = ?
                 AND lane BETWEEN ? AND ? AND ts BETWEEN ? AND ? ORDER BY ts""",
            (f"qubica:{center}", lanes[0], lanes[-1],
             f"{day}T{t0}", f"{day}T{t1}")):
        im = Image.open(io.BytesIO(r["png"]))
        if overlaid(im):
            continue
        serie = serie_of(im, V, labels)
        if serie not in (1, 2, 3, 4):
            continue
        for i, row in enumerate(board_rows(im, V, labels)):
            ok, hit, n = agrees(row["frames"], row["printed"])
            if not ok:
                continue
            key = (r["lane"], serie, i)
            if n >= 10:
                if key not in best or n > best[key]["frames_ok"]:
                    tot = [t for t in score(row["frames"]) if t is not None]
                    best[key] = {"lane": r["lane"], "serie": serie, "row": i,
                                 "frames": row["frames"], "score": tot[-1],
                                 "hcp": row["hcp"], "ts": r["ts"], "frames_ok": n}
            else:
                partial[key] = max(partial.get(key, 0), n)
    return best, {k: v for k, v in partial.items() if k not in best}


def to_balls(frames):
    """The flat ball list social_game stores."""
    out = []
    for fr in frames:
        for t in fr:
            if not t:
                continue
            split = t.startswith("s")
            out.append({"ball": t[1:] if split else t, "split": split})
    return out
