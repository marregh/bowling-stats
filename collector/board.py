"""Geometry of a scoring.se board, and the glyph cells inside it.

Three layouts have turned up so far and they do not differ by hall alone:
Baltiska and Klippan skin the board differently, and Klippan draws a different
one again when a lane scores a single bowler instead of a 2v2 pair. Detecting
the rows from the pixels was tried and abandoned -- the rules, the light cells
and the frame separators all shift with how much has been bowled, so the
detector agreed with itself on one board and not the next.

So the layouts are listed and the caller says which one applies. Choosing
automatically was tried -- by the rules, by the light cells, by the frame
separators, and by decoding under each layout and keeping whichever validated
-- and each worked on some boards and not others. The lists below are measured
and correct; picking between them is unfinished.

Columns are the same everywhere. Frame i spans

    x = 8.3 + 32.66*i  ..  8.3 + 32.66*(i+1)

except the tenth, which is half as wide again -- three ball boxes, not two --
and runs to the table edge at 351.
"""

X0, W = 8.3, 32.66
RIGHT_EDGE = 351

# (ball row, totals row) per player card, top to bottom.
LAYOUTS = {
    "2-kort A": [((53, 71), (72, 90)), ((115, 133), (134, 152))],
    "2-kort B": [((47, 63), (64, 80)), ((109, 125), (126, 142))],
    "1-kort":   [((75, 94), (95, 114))],
}


def frame_span(frame):
    """Left and right x of a whole frame."""
    x0 = X0 + W * frame
    x1 = RIGHT_EDGE if frame == 9 else X0 + W * (frame + 1)
    return int(round(x0)), int(round(x1))


def ball_cells(frame):
    """The ball boxes of one frame: two, or three in the tenth."""
    x0, x1 = frame_span(frame)
    n = 3 if frame == 9 else 2
    step = (x1 - x0) / n
    return [(int(round(x0 + step * i)), int(round(x0 + step * (i + 1))))
            for i in range(n)]


def cut(im, box, pad_x=2, pad_y=0):
    x0, x1 = box[0]
    y0, y1 = box[1]
    return im.crop((x0 + pad_x, y0 + pad_y, x1 - pad_x, y1 - pad_y))


def glyphs(cell, dark=140):
    """Separate ink blobs in a cell, left to right, cropped tight.

    A ball box holds one mark, but a totals box holds up to three digits, so
    the same splitter serves both.
    """
    g = cell.convert("L")
    px = g.load()
    w, h = g.size
    col = [any(px[x, y] < dark for y in range(h)) for x in range(w)]
    runs, s = [], None
    for x, m in enumerate(col + [False]):
        if m and s is None:
            s = x
        elif not m and s is not None:
            runs.append((s, x))
            s = None
    out = []
    for s, e in runs:
        ys = [y for y in range(h) if any(px[x, y] < dark for x in range(s, e))]
        tall = max(ys) - min(ys)
        # A miss is a dash: two or three pixels tall and quite wide. Filtering
        # on height alone threw every one of them away, which made a frame like
        # "8 -" look like "8" with an empty second box and quietly changed an
        # open frame into an unfinished one.
        if e - s < 2 or (tall < 4 and not (e - s >= 4 and tall >= 1)):
            continue
        out.append(g.crop((s, min(ys), e, max(ys) + 1)))
    return out


def score_layout(im, cards, read_totals_row, min_cells=5):
    """How well one layout explains this board.

    Counts cells that decode *and* sit in a sequence that climbs by legal frame
    scores. A wrong geometry can scrape two or three cells out of the header by
    luck, so a layout has to clear `min_cells` on a single card before it counts
    at all -- luck does not produce five totals in a row that behave like a game.
    """
    total = 0
    for _, band in cards:
        vals = read_totals_row(im, band)
        seen = [v for v in vals if v is not None]
        if len(seen) < min_cells:
            continue
        prev, ok = 0, True
        for n, v in enumerate(seen):
            step = v - prev
            if step < 0 or step > (180 if n == 0 else 30):
                ok = False
                break
            prev = v
        if ok and seen[-1] >= 30:
            total += len(seen)
    return total


def pick_layout(im, read_totals_row):
    """The layout that best explains this board, or (None, 0).

    The board is drawn differently per hall *and* per scoring mode -- one card
    for a single bowler, two for a 2v2 pair -- so this cannot be looked up from
    the hall alone. Rather than detect the rows from pixels, which was tried
    four ways and worked on some boards and not others, each known layout is
    decoded and the one whose numbers behave like a bowling game wins.
    """
    best, best_score = None, 0
    for name, cards in LAYOUTS.items():
        s = score_layout(im, cards, read_totals_row)
        if s > best_score:
            best, best_score = name, s
    return best, best_score


def pick_layout_for_series(images, read_totals_row):
    """The layout for a run of boards from one lane, summed over all of them.

    A single board is a poor witness: early in a game too few cells are filled
    to tell the layouts apart, and now and then a wrong geometry scrapes a
    plausible-looking row out of the furniture. Neither happens *consistently*,
    and a lane is always captured many times, so the scores are added up and the
    layout that explains the whole session wins.
    """
    totals = {name: 0 for name in LAYOUTS}
    for im in images:
        for name, cards in LAYOUTS.items():
            totals[name] += score_layout(im, cards, read_totals_row)
    best = max(totals, key=totals.get)
    return (best, totals) if totals[best] else (None, totals)
