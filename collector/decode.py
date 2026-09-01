"""Classify ball glyphs and read the printed running totals, then cross-check."""
import numpy as np
from PIL import Image
import sheet

# Glyph classes. "S<n>" is a split: Bowlit circles the pin count on a split ball.
BLANK = None


def build_templates(samples):
    """samples: [(gray, band, x0, pitch, [frame labels...])] -> {label: [patch,...]}"""
    bank = {}
    for gray, band, x0, pitch, labels in samples:
        flat = {}
        for frame, lab in enumerate(labels, start=1):
            for ball, v in enumerate(lab):
                flat[(frame, ball)] = v
        for frame, ball, patch in sheet.cells(gray, band, x0, pitch):
            v = flat.get((frame, ball), BLANK)
            if patch is None or v is BLANK:
                continue
            bank.setdefault(v, []).append(patch)
    return bank


def classify(patch, bank):
    if patch is None:
        return BLANK, 1.0
    best, score = None, -1.0
    for lab, pats in bank.items():
        for p in pats:
            s = float((patch * p).sum())      # cosine sim; patches are unit-norm
            if s > score:
                best, score = lab, s
    return best, score


def read_row(gray, band, x0, pitch, bank):
    out = [[] for _ in range(10)]
    conf = 1.0
    for frame, ball, patch in sheet.cells(gray, band, x0, pitch):
        lab, s = classify(patch, bank)
        if lab is not BLANK:
            conf = min(conf, s)
        out[frame - 1].append(lab)
    return [[v for v in f if v is not BLANK] for f in out], conf


# --- scoring ------------------------------------------------------------------
def pins(tok):
    if tok == "X":
        return 10
    if tok == "-":
        return 0
    if tok == "/":
        return None          # spare: resolved against the previous ball
    return int(tok[1:]) if tok.startswith("S") else int(tok)


def to_balls(frames):
    """Flatten frame tokens into a pin sequence, resolving spares."""
    seq = []
    for f in frames:
        prev = None
        for t in f:
            v = pins(t)
            if v is None:
                v = 10 - (prev or 0)
            seq.append(v)
            prev = v
    return seq


def running_totals(frames):
    """Standard ten-pin cumulative scores; None where not yet determinable."""
    seq, idx, out, total = [], [], [], 0
    for f in frames:
        start = len(seq)
        prev = None
        for t in f:
            v = pins(t)
            if v is None:
                v = 10 - (prev or 0)
            seq.append(v)
            prev = v
        idx.append(start)
    for i, f in enumerate(frames):
        s = idx[i]
        if i == 9:
            total += sum(seq[s:])
            out.append(total)
            break
        if f and f[0] == "X":
            if len(seq) < s + 3:
                out.append(None); continue
            total += 10 + seq[s + 1] + seq[s + 2]
        elif len(f) > 1 and f[1] == "/":
            if len(seq) < s + 3:
                out.append(None); continue
            total += 10 + seq[s + 2]
        else:
            if len(seq) < s + 2:
                out.append(None); continue
            total += seq[s] + seq[s + 1]
        out.append(total)
    return out
