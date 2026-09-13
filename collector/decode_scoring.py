"""Read the running-totals row off a captured scoring.se board.

The frames themselves are the eventual prize; the totals row is the sensible
first milestone. It is digits only -- no X, no spare slash, no circled split --
and it is *self-checking*, which the frames are not on their own: running totals
climb, each step is a legal frame score of 0..30, and the last one is the game
score, which BITS publishes independently. So a decode can be proved rather
than trusted, which is the same rule sheet.py already applies.

Geometry, measured off the captures rather than guessed (360x270, all lanes):

    frame i spans x = 8.3 + 32.66*i .. 8.3 + 32.66*(i+1)
    player 1  balls y 53..70   totals y 72..89
    player 2  balls y 115..132 totals y 134..151
    frame 10 is wider -- three ball boxes -- and runs x 302..351

The horizontal rules at y = 71, 90, 133, 152 must be excluded: they are dark
across the full width, so including one makes every column look occupied and
the digits refuse to separate.

Templates live in scoring_digits.npz, built by clustering real glyphs and
labelling the clusters by eye. Anything that does not match one closely enough
is returned as None rather than guessed -- an unreadable frame is a far smaller
problem than a confidently wrong one.
"""
import argparse
import io
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
from collections import Counter
from PIL import Image

from store import connect

HERE = Path(__file__).resolve().parent
X0, W = 8.3, 32.66
# Per hall. The columns are the same everywhere -- frame boundaries land on
# 41, 74, 106 ... 335 at Klippan exactly as at Baltiska -- but the rows are not:
# each hall skins the board (Baltiska blue, Klippan orange) and the row pitch
# differs with it, 19px against 17px. Only the y bands need calibrating; the
# digit templates, the column maths and the glyph font are shared.
PROFILES = {
    524: {"p1": (72, 90), "p2": (134, 152)},    # Baltiska Malmö
    497: {"p1": (64, 80), "p2": (126, 142)},    # Klippans Bowlinghall
}
DEFAULT_ALLEY = 524
BANDS = PROFILES[DEFAULT_ALLEY]
NW, NH = 8, 15
DARK = 115
MAX_DIST = 2.6          # beyond this, call it unreadable
MAX_FRAME = 30          # a frame cannot score more than a strike plus two more
MAX_HANDICAP = 150      # added into the first total where the league uses it
RIGHT_EDGE = 351        # where the scoring table ends


def load_templates():
    z = np.load(HERE / "scoring_digits.npz")
    return z["vectors"], [str(x) for x in z["labels"]]


def _vec(g):
    a = np.asarray(g.resize((NW, NH), Image.LANCZOS), dtype=np.float32)
    return ((a - a.min()) / max(1.0, (a.max() - a.min()))).ravel()


def digit_boxes(im, who, frame, bands=None):
    """The separate glyphs in one totals cell, left to right."""
    y0, y1 = (bands or BANDS)[who]
    x0 = int(round(X0 + W * frame)) + 3
    # The tenth frame is half as wide again: it holds three ball boxes, not two,
    # and the cell runs to the table's right edge. Using the uniform width here
    # clipped the last digit, turning 248 into 24 -- which the check then caught
    # as the total dropping, rather than letting it through as a plausible score.
    x1 = (RIGHT_EDGE if frame == 9 else int(round(X0 + W * (frame + 1)))) - 2
    cell = im.crop((x0, y0, x1, y1)).convert("L")
    px = cell.load()
    w, h = cell.size
    dark = [[px[x, y] < DARK for y in range(h)] for x in range(w)]
    runs, s = [], None
    for x, m in enumerate([any(col) for col in dark] + [False]):
        if m and s is None:
            s = x
        elif not m and s is not None:
            runs.append((s, x))
            s = None
    out = []
    for s, e in runs:
        ys = [y for y in range(h) if any(dark[x][y] for x in range(s, e))]
        if not ys or max(ys) - min(ys) < 5:
            continue                      # a border nick or a stray speck
        out.append(cell.crop((s, min(ys), e, max(ys) + 1)))
    return out


def read_cell(im, who, frame, V, labels, bands=None):
    """The number in one totals cell: int, or None if empty or unreadable."""
    boxes = digit_boxes(im, who, frame, bands)
    if not boxes:
        return None
    digits = []
    for g in boxes:
        d = np.linalg.norm(V - _vec(g), axis=1)
        j = int(d.argmin())
        if d[j] > MAX_DIST:
            return None
        digits.append(labels[j])
    try:
        return int("".join(digits))
    except ValueError:
        return None


def read_totals(im, V=None, labels=None, alley=DEFAULT_ALLEY):
    if V is None:
        V, labels = load_templates()
    bands = PROFILES.get(alley, BANDS)
    return {who: [read_cell(im, who, f, V, labels, bands) for f in range(10)]
            for who in bands}


def check(totals):
    """What the printed numbers must satisfy if they were read correctly.

    Returns (ok, frames, why). `frames` is the per-frame score implied by the
    steps between totals, which is the thing worth keeping.
    """
    seen = [(i, t) for i, t in enumerate(totals) if t is not None]
    if not seen:
        return False, [], "tom"
    frames, prev = [], 0
    for n, (i, t) in enumerate(seen):
        step = t - prev
        if step < 0:
            return False, [], f"ram {i + 1}: summan sjunker, {prev} -> {t}"
        # The first total carries any handicap: the youth league adds it up
        # front, so Sixten Bengtsson's game reads 102 after one frame -- 74 of
        # handicap plus 28 bowled. Every later step is a frame and must be a
        # legal frame score, which still checks nine of the ten.
        limit = MAX_FRAME + MAX_HANDICAP if n == 0 else MAX_FRAME
        if step > limit:
            return False, [], f"ram {i + 1}: steg {step} > {limit}"
        frames.append(step)
        prev = t
    return True, frames, ""


def board_score(totals):
    """The game score so far: the last total that could be read."""
    vals = [t for t in totals if t is not None]
    return vals[-1] if vals else None


def split_games(seq, who):
    """Break a lane's capture sequence into games.

    Weak, and known to be: it starts a new game when frame 1's total drops,
    which a single misread of "20" as "0" also does. The board prints the game
    number ("Serie 3") in its top right corner and reading that would be the
    honest fix -- it is white on blue in a different face from the totals, so
    it needs its own templates.
    """
    out, last = [[]], None
    for ts, t in seq:
        v = t[who]
        if v[0] is not None:
            if last is not None and v[0] < last:
                out.append([])
            last = v[0]
        out[-1].append((ts, v))
    return [g for g in out if g]


def stitch(game):
    """Merge one game's readings into a single row of totals.

    A total, once printed, never changes: frame 4 says the same thing for the
    rest of the game. So each cell can be voted on across every capture of that
    game, which is what recovers the frames the "Nästa bana" overlay sits on
    top of -- they were plainly readable before it appeared. Majority vote also
    absorbs the occasional single-frame misread.
    """
    votes = [Counter() for _ in range(10)]
    for _, v in game:
        for i, x in enumerate(v):
            if x is not None:
                votes[i][x] += 1
    return [(c.most_common(1)[0][0] if c else None) for c in votes]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", default="scoring:524")
    ap.add_argument("--lane", type=int)
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--json", action="store_true", help="emit each decode as JSON")
    ap.add_argument("--stitch", action="store_true",
                    help="merge each game's readings across time, which is what "
                         "recovers the cells the overlay covers")
    a = ap.parse_args()

    V, labels = load_templates()
    con = connect()
    q = "SELECT ts, lane, png FROM capture WHERE slug = ?"
    args = [a.slug]
    if a.lane:
        q += " AND lane = ?"
        args.append(a.lane)
    q += " ORDER BY ts"
    rows = con.execute(q, args).fetchall()[: a.limit]

    if a.stitch:
        by_lane = {}
        for r in rows:
            im = Image.open(io.BytesIO(r["png"]))
            by_lane.setdefault(r["lane"], []).append((r["ts"], read_totals(im, V, labels)))
        whole = part = 0
        for lane, seq in sorted(by_lane.items()):
            for who in BANDS:
                for gi, g in enumerate(split_games(seq, who), 1):
                    merged = stitch(g)
                    ok, frames, why = check(merged)
                    filled = sum(1 for x in merged if x is not None)
                    if filled < 3:
                        continue
                    flag = "ok " if ok else "FEL"
                    print(f"  bana {lane:>2} {who} spel {gi}: {filled:>2}/10  {flag}  "
                          f"{merged}")
                    if not ok:
                        print(f"        {why}")
                        part += 1
                    else:
                        whole += 1
                        print(f"        ramar {frames}  summa {board_score(merged)}")
        print()
        print(f"{whole} hållbara rader, {part} förkastade")
        return 0

    good = bad = empty = 0
    best = {}
    for r in rows:
        im = Image.open(io.BytesIO(r["png"]))
        tot = read_totals(im, V, labels)
        for who, vals in tot.items():
            ok, frames, why = check(vals)
            if not any(v is not None for v in vals):
                empty += 1
                continue
            if ok:
                good += 1
                sc = board_score(vals)
                key = (r["lane"], who)
                if sc is not None and sc >= (best.get(key) or (0, ""))[0]:
                    best[key] = (sc, r["ts"], frames)
            else:
                bad += 1
            if a.json:
                print(json.dumps({"ts": r["ts"], "lane": r["lane"], "who": who,
                                  "totals": vals, "ok": ok, "why": why},
                                 ensure_ascii=False))

    n = good + bad
    print(f"\n{len(rows)} bilder, {empty} tomma rader, {n} avlästa")
    if n:
        print(f"  aritmetiskt hållbara: {good} ({100*good//n}%)")
        print(f"  förkastade:           {bad}")
    print("\nhögsta hållbara summa per bana och spelare:")
    for (lane, who), (sc, ts, frames) in sorted(best.items()):
        print(f"   bana {lane:>2} {who}  {sc:>3}  kl {ts[11:19]}  ramar {frames}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
