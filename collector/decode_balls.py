"""Read the ball row off a captured board: the frames themselves.

The totals row gives scores; this gives the shots -- strikes, spares, misses and
the circled splits -- which is what the club's statistics are actually made of,
and the only route to them for the 55 fixtures a season Bowlit does not reach.

Nothing here is trusted on its own. Every board also prints the running totals
under the frames, those are decoded separately, and a reading of the balls is
accepted only if scoring it reproduces them. That is the same rule sheet.py
applied to the old scoresheet decoder: a decode that cannot reproduce the
printed arithmetic is wrong, whatever it looks like.

Tokens are "X", "/", "-", "F" for a foul, a digit, or "sN" for a digit inside
the circle that marks a split. A foul scores nothing, like a miss.
"""
import numpy as np
from PIL import Image

import board

NW, NH = 10, 14
# Loose on purpose. The nearest template is usually the right glyph even when
# it sits well past a tight cutoff -- an orange Klippan "5" lands 2.7 away from
# a blue Baltiska one -- and a wrong guess does not survive scoring the frames
# against the printed totals. Refusing to read costs a whole row; guessing
# wrong costs nothing, because the validator throws it out.
MAX_DIST = 3.2
MAX_FRAME_SCORE = 30    # above this, a first total must carry a handicap


def load_templates(path=None):
    from pathlib import Path
    p = Path(path or Path(__file__).resolve().parent / "scoring_balls.npz")
    z = np.load(p)
    return z["vectors"], [str(x) for x in z["labels"]]


def _vec(g):
    a = np.asarray(g.resize((NW, NH), Image.LANCZOS), dtype=np.float32)
    return ((a - a.min()) / max(1.0, (a.max() - a.min()))).ravel()


def read_frames(im, ball_band, V=None, labels=None):
    """Tokens per frame: [[...], ...] with None for a ball that could not be read."""
    if V is None:
        V, labels = load_templates()
    out = []
    for f in range(10):
        marks = []
        for cell in board.ball_cells(f):
            gl = board.glyphs(board.cut(im, (cell, ball_band)), dark=130)
            if not gl:
                # An empty box is not an unreadable one. A strike frame has no
                # second ball and the tenth often has no third, so "nothing was
                # thrown here" has to be distinguishable from "something was
                # thrown and I could not read it" -- otherwise every strike
                # looks like a failure and the frame is discarded.
                marks.append("")
                continue
            if len(gl) > 1:                       # a circled digit splits in two
                gl = [max(gl, key=lambda g: g.size[0] * g.size[1])]
            d = np.linalg.norm(V - _vec(gl[0]), axis=1)
            j = int(d.argmin())
            marks.append(labels[j] if d[j] <= MAX_DIST else None)
        out.append(marks)
    return out


def pins(tok, before=0):
    """How many pins a token knocked down. `before` is the ball before it."""
    if tok is None or tok == "":
        return None
    if tok == "X":
        return 10
    if tok == "/":
        return 10 - before
    if tok in ("-", "F"):
        return 0            # a miss and a foul both leave the rack untouched
    t = tok[1:] if tok.startswith("s") else tok
    return int(t) if t.isdigit() else None


def score(frames):
    """Running totals implied by the tokens, or None where it cannot be known."""
    flat = []
    for f, marks in enumerate(frames):
        for m in marks:
            if m != "":
                flat.append((f, m))
    totals, running = [], 0
    i = 0
    balls = [m for _, m in flat]
    idx = 0
    for f in range(10):
        marks = [m for m in frames[f] if m != ""]
        if not marks or marks[0] is None:
            totals.append(None)
            continue
        first = pins(marks[0])
        if first is None:
            totals.append(None)
            continue
        # position of this frame's first ball in the flat list
        start = sum(len([m for m in x if m != ""]) for x in frames[:f])
        def at(k):
            return balls[k] if 0 <= k < len(balls) else None
        if marks[0] == "X":
            b1, b2 = at(start + 1), at(start + 2)
            if f == 9:
                b1, b2 = (marks[1] if len(marks) > 1 else None,
                          marks[2] if len(marks) > 2 else None)
            p1 = pins(b1) if b1 else None
            p2 = pins(b2, pins(b1) or 0) if b2 else None
            if p1 is None or p2 is None:
                totals.append(None)
                continue
            running += 10 + p1 + p2
        else:
            second = marks[1] if len(marks) > 1 else None
            if second is None:
                totals.append(None)
                continue
            sp = pins(second, first)
            if sp is None:
                totals.append(None)
                continue
            if second == "/":
                nxt = at(start + 2)
                if f == 9:
                    nxt = marks[2] if len(marks) > 2 else None
                pn = pins(nxt) if nxt else None
                if pn is None:
                    totals.append(None)
                    continue
                running += 10 + pn
            else:
                running += first + sp
        totals.append(running)
    return totals


def agrees(frames, printed, handicap=0):
    """Do the decoded frames reproduce the printed totals?

    Compares only where both are known. A handicap board adds a constant to
    every total, so it is offered rather than assumed.
    """
    mine = score(frames)
    pairs = [(a, b) for a, b in zip(mine, printed) if a is not None and b is not None]
    if not pairs:
        return False, 0, 0
    hit = sum(1 for a, b in pairs if a + handicap == b)
    return hit == len(pairs), hit, len(pairs)


def infer_handicap(frames, printed):
    """The constant that makes the decoded frames match the printed totals.

    Only where the board actually plays off handicap, which it announces by
    opening above a legal frame score: a first total of 102 is 74 of handicap
    plus 28 bowled, while a first total of 19 is just a good frame. Allowing a
    free constant everywhere was letting a systematically misread card "agree"
    at an offset of 39 in a league that has no handicap at all -- the offset
    absorbed the error instead of exposing it.
    """
    first = next((t for t in printed if t is not None), None)
    if first is None or first <= MAX_FRAME_SCORE:
        return 0
    mine = score(frames)
    diffs = {b - a for a, b in zip(mine, printed) if a is not None and b is not None}
    return diffs.pop() if len(diffs) == 1 else None
