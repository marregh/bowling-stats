"""Excel export of whatever the Urval filter currently selects.

These files are for reading, not for crunching: one sheet per topic, a header
block that spells out exactly which slice of the season it is, and real numbers
in the cells (not "13 (41%)" strings) so a column still sums and sorts.
"""
from datetime import datetime
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

INK = "1F2430"
MUTED = "6B7280"
HEAD_BG = "1F2937"
BAND = "F3F4F6"
GOOD = "15803D"

TITLE_F = Font(name="Calibri", size=16, bold=True, color=INK)
SUB_F = Font(name="Calibri", size=10, color=MUTED)
KEY_F = Font(name="Calibri", size=10, color=MUTED)
VAL_F = Font(name="Calibri", size=11, bold=True, color=INK)
HEAD_F = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
TOT_F = Font(name="Calibri", size=11, bold=True, color=INK)
HEAD_FILL = PatternFill("solid", fgColor=HEAD_BG)
BAND_FILL = PatternFill("solid", fgColor=BAND)
RULE = Border(bottom=Side(style="thin", color="D1D5DB"))

AVG = "0.0"
PCT = "0.0%"
PINS = "#,##0"


def _sheet(wb, name, title, subtitle, first=False):
    ws = wb.active if first else wb.create_sheet()
    ws.title = name[:31]
    ws.sheet_view.showGridLines = False
    ws["A1"] = title
    ws["A1"].font = TITLE_F
    ws.row_dimensions[1].height = 23
    ws["A2"] = subtitle
    ws["A2"].font = SUB_F
    ws["A3"] = "Uttag " + datetime.now().strftime("%Y-%m-%d %H:%M")
    ws["A3"].font = SUB_F
    return ws


def _rendered(c):
    """Roughly what Excel will actually paint in the cell -- the stored value is
    a raw float, so measuring str(v) would size a column for "0.5687096774" and
    a percentage column for nothing at all."""
    v = c.value
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return str(v)
    f = c.number_format
    if f == PCT:
        return f"{v * 100:.1f}%"
    if f == AVG:
        return f"{v:.1f}"
    if f == PINS:
        return f"{v:,.0f}"
    if f == "0.00":
        return f"{v:.2f}"
    if f.startswith("+0.0"):
        return f"{v:+.1f}"
    return str(v)


def _autofit(ws, pad=2.6, cap=44):
    """Widen every column to its widest cell.

    The per-column widths declared in a table are only floors: the summary block
    shares column A with the table's first column, so "Klubbsnitt" sitting above
    a "#" column would otherwise be clipped to five characters. Rows 1-3 are the
    title block and are meant to overflow into their empty neighbours, so they
    are excluded -- measuring them would stretch column A to the full subtitle.
    """
    want = {}
    for row in ws.iter_rows(min_row=4):
        for c in row:
            if c.value is None:
                continue
            # A filter dropdown eats roughly two and a half characters at the
            # right edge of a header cell; centred headers lose half of that on
            # each side, so they need the widest allowance.
            if c.row not in getattr(ws, "head_rows", ()):
                arrow = 0
            elif c.alignment.horizontal == "center":
                arrow = 5
            else:
                arrow = 3
            want[c.column] = max(want.get(c.column, 0), len(_rendered(c)) + arrow)
    for col, w in want.items():
        dim = ws.column_dimensions[get_column_letter(col)]
        dim.width = max(dim.width or 0, min(w + pad, cap))


def _summary(ws, row, pairs):
    """Two-column key/value block. `pairs` is (label, value, number_format)."""
    for label, value, fmt in pairs:
        ws.cell(row=row, column=1, value=label).font = KEY_F
        c = ws.cell(row=row, column=2, value=value)
        c.font = VAL_F
        c.alignment = Alignment(horizontal="left")
        if fmt:
            c.number_format = fmt
        row += 1
    return row + 1


def _table(ws, row, cols, rows, total=None, good=200):
    """`cols` is (header, width, number_format, align). A row value of None
    renders as an em dash so an unbowled 4th game does not read as a zero."""
    for i, (head, width, _fmt, align) in enumerate(cols, start=1):
        c = ws.cell(row=row, column=i, value=head)
        c.font, c.fill = HEAD_F, HEAD_FILL
        # Numeric headers are centred, not right-aligned: Excel paints the
        # autofilter arrow flush against the right edge of the cell, straight on
        # top of right-aligned text no matter how wide the column is. The body
        # cells below stay right-aligned so the digits still line up.
        c.alignment = Alignment(horizontal="center" if align == "right" else align,
                                vertical="center")
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.row_dimensions[row].height = 19
    ws.freeze_panes = ws.cell(row=row + 1, column=1)
    head_row = row
    ws.head_rows = getattr(ws, "head_rows", ()) + (row,)
    row += 1

    for n, data in enumerate(rows):
        for i, ((_h, _w, fmt, align), v) in enumerate(zip(cols, data), start=1):
            c = ws.cell(row=row, column=i, value="–" if v is None else v)
            c.alignment = Alignment(horizontal=align)
            c.border = RULE
            if v is not None and fmt:
                c.number_format = fmt
            if n % 2:
                c.fill = BAND_FILL
            if fmt == AVG and isinstance(v, (int, float)) and v >= good:
                c.font = Font(name="Calibri", size=11, bold=True, color=GOOD)
        row += 1

    if total:
        for i, ((_h, _w, fmt, align), v) in enumerate(zip(cols, total), start=1):
            c = ws.cell(row=row, column=i, value=v)
            c.font, c.alignment = TOT_F, Alignment(horizontal=align)
            c.border = Border(top=Side(style="medium", color=HEAD_BG))
            if v is not None and fmt:
                c.number_format = fmt
        row += 1

    ws.auto_filter.ref = (f"A{head_row}:"
                          f"{get_column_letter(len(cols))}{row - (1 if total else 0) - 1}")
    _autofit(ws)
    return row + 1


def _book():
    wb = Workbook()
    wb.properties.creator = "LUMA"
    return wb


def _save(wb):
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _r(a, b):
    """Ratio as a fraction for Excel's percent format; None when undefined so
    the cell shows a dash instead of a misleading 0%."""
    return (a / b) if b else None


def team_book(d, subtitle, tl):
    """Lagsnitt for the selected window, plus the whole-season roster."""
    wb = _book()
    f = d["form"]
    t = d["target"]          # the "good game" mark for this team's division
    ws = _sheet(wb, "Lagsnitt", f"{tl(d['name'])} – lagsnitt", subtitle, first=True)

    pairs = [("Lagsnitt", f["avg"], AVG),
             ("Matcher", f["matches"], None),
             ("Serier", f["games"], None),
             ("Käglor", f["pins"], PINS),
             (f"Serier {t}+", f["over_target"], None),
             (f"Andel {t}+", _r(f["over_target"], f["games"]), PCT)]
    if d["last"]:
        pairs += [("Säsongssnitt", f["season_avg"], AVG),
                  ("Mot säsongen", f["avg"] - f["season_avg"], "+0.0;-0.0")]
    row = _summary(ws, 5, pairs)

    cols = [("Datum", 12, None, "left"), ("Omgång", 8, None, "right"),
            ("Motstånd", 30, None, "left"),
            ("Plats", 8, None, "left"), ("Serier", 8, None, "right"),
            ("Käglor", 10, PINS, "right"), ("Snitt", 9, AVG, "right"),
            (f"{t}+", 7, None, "right")]
    rows = [[c["date"], c["round_id"], tl(c["opponent"]),
             "Hemma" if c["at_home"] else "Borta",
             c["games"], c["pins"], c["avg"], c["over_target"]] for c in d["chart"]]
    total = ["Totalt", None, None, None,
             f["games"], f["pins"], f["avg"], f["over_target"]]
    _table(ws, row, cols, rows, total, good=t)

    if d["roster"]:
        ws = _sheet(wb, "Trupp (hela säsongen)", f"{tl(d['name'])} – trupp",
                    "Spelarsnitt för hela säsongen – påverkas inte av urvalet"
                    f" · grönt = {t}+")
        cols = [("Spelare", 26, None, "left"), ("Serier", 8, None, "right"),
                ("Snitt", 9, AVG, "right"), ("Bästa serie", 12, None, "right"),
                ("Bästa match", 12, None, "right")]
        _table(ws, 5, cols,
               [[p["player"], p["games"], p["pins"] / p["games"],
                 p["high_game"], p["high_series"]] for p in d["roster"]],
               good=t)
    return _save(wb)


def player_book(d, subtitle, tl):
    """Match results for the selected window, plus frame stats where Bowlit
    covered the hall."""
    wb = _book()
    st = d["stats"]
    ws = _sheet(wb, "Matcher", f"{d['name']} – matcher", subtitle, first=True)

    row = _summary(ws, 5, [
        ("Snitt", st["avg"], AVG),
        ("Serier", st["games"], None),
        ("Matcher", len(d["hist"]), None),
        ("Bästa serie", st["high_game"], None),
        ("Bästa match", st["high_series"], None),
        ("Serier 200+", st["over_200"], None),
        ("Andel 200+", _r(st["over_200"], st["games"]), PCT),
        ("Serier under 180", st["under_180"], None)])

    cols = [("Datum", 12, None, "left"), ("Omgång", 8, None, "right"),
            ("Lag", 18, None, "left"),
            ("Motstånd", 28, None, "left")] + \
           [(f"S{i}", 7, None, "right") for i in (1, 2, 3, 4)] + \
           [("Totalt", 9, None, "right"), ("Snitt", 9, AVG, "right"),
            ("Bp", 6, None, "right"), ("Hall", 26, None, "left"),
            ("Oljeprofil", 18, None, "left")]
    rows = []
    for h in d["hist"]:
        gs = [g for g in (h["g1"], h["g2"], h["g3"], h["g4"]) if g]
        # BITS records an unbowled game as 0, not NULL. Left as-is it reads as a
        # gutter series and drags the eye; blank it the way the web table does.
        rows.append([h["played_at"][:10], h["round_id"],
                     tl(h["own"]), tl(h["opponent"]),
                     h["g1"] or None, h["g2"] or None, h["g3"] or None,
                     h["g4"] or None, h["series"] or None,
                     (sum(gs) / len(gs)) if gs else None,
                     h["lane_point"], h["hall"], h["oil_pattern"]])
    total = ["Totalt", None, None, None, None, None, None, None,
             sum(h["series"] or 0 for h in d["hist"]), st["avg"], None, None, None]
    _table(ws, row, cols, rows, total)

    sh = d["shots"]
    if sh and sh.get("frames"):
        ws = _sheet(wb, "Slag", f"{d['name']} – slagstatistik",
                    subtitle + " · endast matcher i Bowlit-hallar")
        row = _summary(ws, 5, [
            ("Serier med slagdata", d["shot_games"], None),
            ("Rutor", sh["frames"], None),
            ("Strike%", _r(sh["strikes"], sh["frames"]), PCT),
            ("Spärr%", _r(sh["spares"], sh["spare_chances"]), PCT),
            ("Snitt 1:a klot", _r(sh["first_ball_pins"], sh["first_balls"]), "0.00"),
            ("Hål", sh["split_first"], None),
            ("Hål tagna", sh["split_converted"], None),
            ("Spärr miss", sh["spare_chances"] - sh["spares"], None),
            ("Enkel miss", sh["single_pin"] - sh["single_pin_made"], None)])

        cols = [("Datum", 12, None, "left"), ("Match", 34, None, "left"),
                ("Serier", 8, None, "right"), ("Käglor", 9, PINS, "right"),
                ("Strike", 8, None, "right"), ("Strike%", 9, PCT, "right"),
                ("Spärr", 8, None, "right"), ("Spärrlägen", 11, None, "right"),
                ("Spärr%", 9, PCT, "right"), ("Hål", 7, None, "right"),
                ("Hål%", 8, PCT, "right"), ("Spärr miss", 11, None, "right"),
                ("Enkel miss", 11, None, "right"), ("Enkla", 8, None, "right")]
        rows = []
        for m in d["per_match"]:
            s = m["st"]
            rows.append([m["played_at"][:10],
                         tl(m["label"]) + (" (träning)" if m["adhoc"] else ""),
                         m["games"], m["pins"],
                         s["strikes"], _r(s["strikes"], s["frames"]),
                         s["spares"], s["spare_chances"],
                         _r(s["spares"], s["spare_chances"]),
                         s["split_first"], _r(s["split_first"], s["first_balls"]),
                         s["spare_chances"] - s["spares"],
                         s["single_pin"] - s["single_pin_made"], s["single_pin"]])
        total = ["Totalt", None, d["shot_games"],
                 sum(m["pins"] for m in d["per_match"]),
                 sh["strikes"], _r(sh["strikes"], sh["frames"]),
                 sh["spares"], sh["spare_chances"],
                 _r(sh["spares"], sh["spare_chances"]),
                 sh["split_first"], _r(sh["split_first"], sh["first_balls"]),
                 sh["spare_chances"] - sh["spares"],
                 sh["single_pin"] - sh["single_pin_made"], sh["single_pin"]]
        _table(ws, row, cols, rows, total)
    return _save(wb)


def players_book(d, subtitle):
    """The season leaderboard exactly as the page ranks it."""
    wb = _book()
    ws = _sheet(wb, "Spelare", f"LUMA – spelare {d['season']}/{str(d['season'] + 1)[2:]}",
                subtitle, first=True)
    rows = d["rows"]
    games = sum(p["games"] for p in rows)
    pins = sum(p["pins"] for p in rows)
    row = _summary(ws, 5, [
        ("Spelare", len(rows), None),
        ("Serier", games, None),
        ("Käglor", pins, PINS),
        ("Klubbsnitt", _r(pins, games), AVG)])

    cols = [("#", 5, None, "right"), ("Spelare", 26, None, "left"),
            ("Matcher", 9, None, "right"), ("Serier", 8, None, "right"),
            ("Snitt", 9, AVG, "right"), ("Bästa serie", 12, None, "right"),
            ("Bästa match", 12, None, "right")]
    body = [[i, p["player"], p["mm"], p["games"], p["pins"] / p["games"],
             p["high_game"], p["high_series"]] for i, p in enumerate(rows, 1)]
    total = ["Totalt", None, None, games, _r(pins, games), None, None]
    if d["last"]:
        # Each player's window covers their own stretch of the season, so the
        # file has to say which one or the averages are not comparable.
        cols += [("Från", 12, None, "left"), ("Till", 12, None, "left")]
        for line, p in zip(body, rows):
            line += [p["first"], p["last"]]
        total += [None, None]
    _table(ws, row, cols, body, total)
    return _save(wb)
