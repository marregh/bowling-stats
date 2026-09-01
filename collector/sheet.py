"""Decode a Bowlit live-scoring lane image into frame-by-frame throws.

The scoresheet is a deterministic render, but its layout is *not* fixed:
 - the grid origin shifts between lanes,
 - the number of player rows varies with how many people are bowling,
 - row height scales with that count.
So everything is detected per image. Rows are anchored on the coloured name
bars (red normally, green for whoever is up), and the x-grid is fitted to the
ten running-total numbers, which are always present and evenly spaced.
"""
import numpy as np
from PIL import Image

REF_ROW_PITCH = 56.0     # row pitch when two bowlers share a lane


def load(path):
    im = Image.open(path).convert("RGB")
    a = np.asarray(im).astype(int)
    gray = np.asarray(im.convert("L")).astype(int)
    return a, gray


def name_bars(rgb):
    """y-extents of each player's name bar, top to bottom."""
    R, G, B = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    red = (R > 110) & (R - G > 55) & (R - B > 55)
    green = (G > 90) & (G - R > 40) & (G - B > 40)
    cnt = (red | green).sum(axis=1)
    ys = [y for y, v in enumerate(cnt) if v > 40]
    if not ys:
        return []
    runs, cur = [], [ys[0]]
    for y in ys[1:]:
        if y - cur[-1] <= 2:
            cur.append(y)
        else:
            runs.append((cur[0], cur[-1])); cur = [y]
    runs.append((cur[0], cur[-1]))
    return [r for r in runs if r[1] - r[0] >= 4]


def row_bands(rgb):
    """(balls, totals) y-ranges per player, scaled to the detected row pitch."""
    bars = name_bars(rgb)
    if not bars:
        return []
    pitch = REF_ROW_PITCH
    if len(bars) > 1:
        pitch = float(np.median(np.diff([b[0] for b in bars])))
    s = pitch / REF_ROW_PITCH
    out = []
    for top, _ in bars:
        out.append({
            "balls": (int(round(top - 40 * s)), int(round(top - 20 * s))),
            "totals": (int(round(top - 19 * s)), int(round(top - 3 * s))),
        })
    return out


def _col_runs(mask, gap=3, min_w=3):
    col = mask.sum(axis=0)
    runs, cur, blank = [], None, 0
    for x, v in enumerate(col):
        if v > 0:
            cur = [x, x] if cur is None else [cur[0], x]
            blank = 0
        elif cur is not None:
            blank += 1
            if blank > gap:
                runs.append(tuple(cur)); cur = None
    if cur:
        runs.append(tuple(cur))
    return [r for r in runs if r[1] - r[0] + 1 >= min_w]


def fit_grid(gray, band, width=480):
    """Fit frame pitch and origin to the ten running-total numbers.

    Returns (x0, pitch) where frame k (1-indexed) is centred at x0 + (k-1)*pitch.
    Runs touching either image edge are border chrome, not digits, so they go
    first; the remaining centres are phase-aligned and the densest run of ten
    consecutive slots wins.
    """
    y0, y1 = band
    runs = _col_runs(gray[y0:y1] < 110, gap=4, min_w=5)
    runs = [r for r in runs if r[0] > 2 and r[1] < width - 3]
    cen = [(a + b) / 2 for a, b in runs]
    if len(cen) < 6:
        return None
    d = [cen[i + 1] - cen[i] for i in range(len(cen) - 1)]
    d = [v for v in d if 30 < v < 55]
    if not d:
        return None
    pitch = float(np.median(d))

    best = None
    for anchor in cen:
        ks = [(c - anchor) / pitch for c in cen]
        inl = [(round(k), c) for k, c in zip(ks, cen) if abs(k - round(k)) * pitch < 5]
        if len(inl) < 6:
            continue
        kk = sorted({k for k, _ in inl})
        # densest window of ten consecutive slots
        for start in kk:
            win = [(k, c) for k, c in inl if start <= k <= start + 9]
            if best is None or len(win) > len(best[0]):
                best = (win, start)
    if best is None:
        return None
    win, start = best
    ks = np.array([k - start for k, _ in win], float)
    cs = np.array([c for _, c in win], float)
    if len(ks) < 6 or ks.std() == 0:
        return None
    pitch = float(((ks - ks.mean()) @ (cs - cs.mean())) / ((ks - ks.mean()) ** 2).sum())
    x0 = float(cs.mean() - pitch * ks.mean())
    return x0, pitch


def ball_cells(frame, x0, pitch):
    """x-ranges of the ball cells of a 1-indexed frame, from the frame centre."""
    c = x0 + (frame - 1) * pitch
    left, right = c - pitch / 2, c + pitch / 2
    if frame < 10:
        mid = (left + right) / 2
        return [(left, mid), (mid, right)]
    right = c + pitch * 0.9                      # frame 10 carries a third ball
    w = (right - left) / 3
    return [(left + i * w, left + (i + 1) * w) for i in range(3)]


def _ink(cell, thresh=110):
    """Binarise a cell and crop to the glyph.

    Cell dividers are 1-px full-height hairlines. Left in, they inflate the
    bounding box and warp the normalised patch, which is enough to turn a
    slash into a circled digit -- so drop isolated full-height columns first.
    """
    m = cell < thresh
    if m.shape[1] == 0:
        return None
    h = m.shape[0]
    col = m.sum(axis=0)
    for x in range(m.shape[1]):
        if col[x] >= 0.8 * h:
            left = col[x - 1] if x > 0 else 0
            right = col[x + 1] if x + 1 < m.shape[1] else 0
            if left < 0.3 * h and right < 0.3 * h:
                m[:, x] = False
    if m.sum() < 6:
        return None
    ys, xs = np.where(m)
    if xs.max() - xs.min() + 1 < 3 or ys.max() - ys.min() + 1 < 0.35 * h:
        return None
    sub = cell[ys.min():ys.max() + 1, xs.min():xs.max() + 1].astype(np.float32)
    return np.clip(200.0 - sub, 0, None)      # ink weight, keeps anti-aliasing


def _norm(patch, size=(16, 16)):
    """Scale to a fixed grid and blur, so stroke weight stops mattering.

    Bowlit renders the same glyph at two weights; binarising and stretching
    made a thin slash and a bold one look like different characters.
    """
    v = patch / (patch.max() or 1.0)
    img = Image.fromarray((v * 255).astype(np.uint8)).resize(size, Image.BILINEAR)
    a = np.asarray(img).astype(np.float32) / 255.0
    k = np.array([[1, 2, 1], [2, 4, 2], [1, 2, 1]], np.float32) / 16.0
    pad = np.pad(a, 1, mode="edge")
    a = sum(k[i, j] * pad[i:i + a.shape[0], j:j + a.shape[1]]
            for i in range(3) for j in range(3))
    n = np.linalg.norm(a)
    return a / n if n else a


def _trim(strip):
    """Drop the near-black separator rows that bound each player block."""
    while strip.shape[0] > 4 and strip[0].mean() < 120:
        strip = strip[1:]
    while strip.shape[0] > 4 and strip[-1].mean() < 120:
        strip = strip[:-1]
    return strip


def cells(gray, band, x0, pitch):
    y0, y1 = band["balls"]
    strip = _trim(gray[y0:y1])
    for frame in range(1, 11):
        for ball, (a, b) in enumerate(ball_cells(frame, x0, pitch)):
            lo, hi = int(round(a)) + 2, int(round(b)) - 1
            patch = _ink(strip[:, max(lo, 0):hi]) if hi > lo else None
            yield frame, ball, (_norm(patch) if patch is not None else None)
