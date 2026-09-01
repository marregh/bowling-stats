"""Local dashboard for Lunds BK Mamba.

Loopback-only by default: this pulls nothing secret, but there is no reason for
it to answer the LAN until it is deliberately hosted.
"""
import sys
from pathlib import Path

from flask import Flask, Response, render_template, request, abort, send_file

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "collector"))
from store import connect          # noqa: E402
from bits import TEAMS             # noqa: E402
import frames                      # noqa: E402
import json, os, re, time, unicodedata  # noqa: E402
import xlsx  # noqa: E402


def name_key(n):
    """Join key across sources: BITS says "Anna Ek", Bowlit says
    "Ek, Anna" for league play and "ANNA EK" for casual games."""
    n = (n or "").strip()
    if "," in n:
        last, first = [p.strip() for p in n.split(",", 1)]
        n = f"{first} {last}"
    n = unicodedata.normalize("NFKD", n).encode("ascii", "ignore").decode()
    return " ".join(n.lower().split())


SESSIONS_SQL = """
    SELECT match_id AS sid, season, played_at AS played_on,
           home || ' – ' || away AS label, 0 AS adhoc
      FROM bits_match
    UNION ALL
    SELECT id, season, played_on, label, 1 FROM social_session
"""


def roster_map():
    """Map Bowlit scoreboard names onto BITS roster names.

    The two sources disagree on order ("Ek, Anna" vs "Anna Ek") and on
    middle names ("ERIK LIND" vs "Erik Nordin Lind"); a token-subset match
    handles both, but only when it resolves to exactly one roster player.

    It deliberately will NOT match a single-token scoreboard name. Bowlit shows
    visiting players by first name only, so matching "JOSE" to a roster José is
    a coin flip that silently files an opponent's game under one of ours. Those
    need an explicit row in player_alias.
    """
    con = connect()
    roster = {}
    for r in con.execute("""
        SELECT DISTINCT r.player FROM bits_result r JOIN bits_match m ON m.match_id = r.match_id
        WHERE (r.side='H' AND m.home LIKE ?) OR (r.side='A' AND m.away LIKE ?)
    """, (CLUB, CLUB)):
        roster[name_key(r["player"])] = r["player"]

    alias = {r["social_name"]: r for r in
             con.execute("SELECT * FROM player_alias")}

    out = {}
    for r in con.execute("SELECT DISTINCT player FROM social_game"):
        name = r["player"]
        a = alias.get(name) or alias.get(name.strip())
        if a is not None:
            if a["is_club"]:
                out[name] = a["display"] or name
            continue
        k = name_key(name)
        if k in roster:
            out[name] = roster[k]
            continue
        toks = set(k.split())
        if len(toks) < 2:
            continue                      # first name only -> not resolvable
        hits = {v for kk, v in roster.items()
                if toks <= set(kk.split()) or set(kk.split()) <= toks}
        if len(hits) == 1:
            out[name] = hits.pop()
    return out


def shot_stats(player_name, season, include_practice=True, since=None):
    """Aggregate frame-level stats for one player, matched by name.

    `since` is a YYYY-MM-DD cutoff (inclusive) used by the last-N-matches
    window on the player page. The two sources date their sessions
    differently -- bits_match.played_at carries a time, social_session.played_on
    does not -- so the comparison is on the date part only, otherwise a practice
    session would sort ahead of a league match played the same evening.
    """
    want = name_key(player_name)
    rmap = roster_map()
    mine = {sn for sn, bn in rmap.items() if name_key(bn) == want}
    con = connect()
    rows = con.execute(f"""
        SELECT g.player, g.balls, g.game, g.game_score,
               s.played_on, s.label, s.sid, s.adhoc
        FROM social_game g JOIN ({SESSIONS_SQL}) s ON s.sid = g.match_id
        WHERE s.season = ? {"" if include_practice else "AND s.adhoc = 0"}
              {"AND substr(s.played_on, 1, 10) >= ?" if since else ""}
    """, (season, since) if since else (season,)).fetchall()
    tot, per_match = {}, {}
    for r in rows:
        if r["player"] not in mine:
            continue
        st = frames.stats(json.loads(r["balls"]))
        tot = frames.add(tot, st)
        pm = per_match.setdefault(r["sid"], {
            "played_at": r["played_on"], "label": r["label"],
            "adhoc": r["adhoc"], "games": 0, "pins": 0, "st": {}})
        pm["st"] = frames.add(pm["st"], st)
        pm["games"] += 1
        pm["pins"] += r["game_score"] or 0
    return tot, sorted(per_match.values(), key=lambda x: x["played_at"])


def pct(a, b):
    return (100.0 * a / b) if b else None

app = Flask(__name__)
CLUB = "Lunds BK Mamba%"

# Mounted under a sub-path in production (example.com/luma-bowling), at the
# root locally. Caddy strips the prefix before proxying, so the app still routes
# on bare paths -- only the links it *emits* need the prefix.
# Set it without a leading slash (LUMA_URL_PREFIX=luma-bowling): a leading slash
# gets rewritten into a Windows path by MSYS shells before Python ever sees it.
_raw = os.environ.get("LUMA_URL_PREFIX", "").strip()
URL_PREFIX = ("/" + _raw.strip("/")) if _raw.strip("/") else ""

WATCH_DIRS = [Path(__file__).resolve().parent,
              Path(__file__).resolve().parent.parent / "collector"]


def source_version():
    """Newest mtime across the app and its templates.

    Flask's reloader restarts the process on a .py change but templates are
    re-read in place, so neither on its own tells the browser anything. Hashing
    mtimes catches both.
    """
    newest = 0.0
    for d in WATCH_DIRS:
        for f in d.rglob("*"):
            if f.suffix in (".py", ".html", ".css", ".js") and f.is_file():
                newest = max(newest, f.stat().st_mtime)
    return f"{newest:.3f}"


@app.route("/__reload")
def reload_stream():
    if not app.config.get("LIVE_RELOAD"):
        abort(404)
    """Server-sent events carrying the source version; the page reloads when it
    changes. Only mounted when the dev reloader is on."""
    def stream():
        last = source_version()
        yield f"data: {last}\n\n"
        for _ in range(600):                    # ~10 min, then the browser reconnects
            time.sleep(1)
            now = source_version()
            if now != last:
                last = now
                yield f"data: {now}\n\n"
            else:
                yield ": ping\n\n"
    return Response(stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def q(sql, *args):
    return connect().execute(sql, args).fetchall()


def seasons():
    rows = q("SELECT DISTINCT season FROM bits_match ORDER BY season DESC")
    return [r["season"] for r in rows]


def season_arg():
    ss = seasons()
    want = request.args.get("season", type=int)
    return want if want in ss else (ss[0] if ss else None)


def label(season):
    return f"{season}/{str(season + 1)[2:]}"


def show_practice():
    """Practice games are included by default; ?practice=0 leaves them out.

    Mixing a three-game friendly into a season's strike rate moves it a lot, so
    the toggle is deliberately visible rather than buried in a settings page.
    """
    v = request.args.get("practice")
    return v not in ("0", "false", "no")


URVAL_COOKIE = "luma_urval"

# Each kind of page remembers its own window: a five-match form view of the
# leaderboard says nothing about whether you also want team pages cut down.
# The scope is the page kind, not the individual page -- one setting per team
# and per player would be 60-odd values in a cookie and would surprise anyone
# who set a window on one player and found the next one unfiltered.
URVAL_SCOPES = {"players": "players", "players_xlsx": "players",
                "team": "team", "team_xlsx": "team",
                "player": "player", "player_xlsx": "player"}


def urval_scope():
    """Which remembered window applies here, or None on a page without one."""
    return URVAL_SCOPES.get(request.endpoint or "")


def _parse_last(v):
    """N, or None for "the whole season" / anything unrecognised."""
    v = (v or "").strip().lower()
    return int(v) if v.isdigit() and int(v) > 0 else None


def last_arg():
    """?last=N limits a page to the N most recently played matches.

    Falls back to the remembered choice when the URL says nothing, so a fresh
    visit or a bare bookmark lands on the window you last picked. "Alla" travels
    as an explicit last=alla rather than as an absent parameter -- otherwise the
    cookie could never tell "I chose the whole season" from "I said nothing",
    and picking Alla would bounce straight back to the stored window.
    """
    v = request.args.get("last")
    if v is None:
        scope = urval_scope()
        v = request.cookies.get(f"{URVAL_COOKIE}_{scope}") if scope else None
    return _parse_last(v)


def qargs(season=None, practice=None, last=None):
    """Query string that carries the view state across a link.

    Season and practice are global and always ride along. `last` does not: it
    belongs to one kind of page, and propagating it automatically would let a
    link out of the leaderboard override whatever a team page remembers. Links
    that stay on the page (the season picker, the Träning toggle, the Excel
    button) pass it explicitly; links that leave leave it behind.
    """
    season = season_arg() if season is None else season
    practice = show_practice() if practice is None else practice
    q = f"?season={season}"
    if not practice:
        q += "&practice=0"
    if last:
        q += f"&last={last}"
    return q


@app.after_request
def remember_urval(resp):
    """Persist an explicit Urval click for a year. It is a view preference, not
    state the server needs, so the cookie is scoped to this app's own path and
    nothing reads it but last_arg()."""
    path = URL_PREFIX or "/"
    scope = urval_scope()
    v = (request.args.get("last") or "").strip().lower()
    if scope and (v == "alla" or _parse_last(v)):
        resp.set_cookie(f"{URVAL_COOKIE}_{scope}", v, max_age=365 * 24 * 3600,
                        path=path, samesite="Lax")
    if URVAL_COOKIE in request.cookies:      # the old shared setting
        resp.delete_cookie(URVAL_COOKIE, path=path)
    return resp


CLUB_PREFIX = "Lunds BK Mamba"
CLUB_LABEL = "LUMA"

# A "good game" is not the same number in every division. 200 is the mark for
# the A and F1 sides; holding the youth team to it would paint every bar grey
# and say nothing about how they are actually bowling. Teams not listed here
# use the default.
DEFAULT_TARGET = 200
TEAM_TARGET = {162098: 100,      # U
               162063: 160,      # B
               184677: 180}      # F2


def team_target(team_id):
    return TEAM_TARGET.get(team_id, DEFAULT_TARGET)


@app.template_filter("team")
def team_label(name):
    """BITS spells the club out; the club (and the alley scoreboard) says LUMA.

    The A team is registered without a suffix, so it comes back as a bare
    "Lunds BK Mamba" in standings and "Lunds BK Mamba A" in fixtures.
    Opponents pass through untouched.
    """
    if not name:
        return name
    n = name.strip()
    # Session labels are "home – away"; relabel each side, or an away fixture
    # keeps the club spelled out because the string doesn't start with it.
    if " – " in n:
        return " – ".join(team_label(part) for part in n.split(" – "))
    if not n.startswith(CLUB_PREFIX):
        return n
    return f"{CLUB_LABEL} {n[len(CLUB_PREFIX):].strip() or 'A'}"


@app.context_processor
def globals_():
    return dict(all_seasons=seasons(), season_label=label, teams=TEAMS,
                club_label=CLUB_LABEL, practice=show_practice(), qargs=qargs,
                last=last_arg(),
                live_reload=app.config.get("LIVE_RELOAD", False),
                B=URL_PREFIX)


# --- pages --------------------------------------------------------------------
@app.route("/")
def index():
    s = season_arg()
    rows = q("""
        SELECT t.team_id, t.team, t.division_id, m.division, m.league,
               t.matches, t.win, t.draw, t.loss, t.points, t.diff,
               (SELECT COUNT(*) + 1 FROM bits_standing x
                 WHERE x.season = t.season AND x.division_id = t.division_id
                   AND (x.points > t.points
                        OR (x.points = t.points AND x.diff > t.diff))) AS pos,
               (SELECT COUNT(*) FROM bits_standing x
                 WHERE x.season = t.season AND x.division_id = t.division_id) AS teams_in_div
        FROM bits_standing t
        JOIN (SELECT DISTINCT season, division_id, division, league FROM bits_match) m
          ON m.season = t.season AND m.division_id = t.division_id
        WHERE t.season = ? AND t.team LIKE ?
        ORDER BY t.division_id
    """, s, CLUB)
    recent = q("""
        SELECT * FROM bits_match
        WHERE season = ? AND has_been_played = 1
          AND (home LIKE ? OR away LIKE ?)
        ORDER BY played_at DESC LIMIT 8
    """, s, CLUB, CLUB)
    upcoming = q("""
        SELECT * FROM bits_match
        WHERE season = ? AND has_been_played = 0
          AND (home LIKE ? OR away LIKE ?)
        ORDER BY played_at LIMIT 8
    """, s, CLUB, CLUB)
    caps = q("""SELECT COUNT(*) n, COUNT(DISTINCT book_id) books,
                       MIN(ts) first_ts, MAX(ts) last_ts FROM capture""")[0]
    sessions = q("""
        SELECT s.*, (SELECT COUNT(*) FROM social_game g WHERE g.match_id = s.id) games,
               (SELECT COUNT(DISTINCT g.player) FROM social_game g WHERE g.match_id = s.id) players
        FROM social_session s WHERE s.season = ? ORDER BY s.played_on DESC
    """, s)
    return render_template("index.html", season=s, standings=rows,
                           recent=recent, upcoming=upcoming, caps=caps,
                           sessions=sessions if show_practice() else [])


def team_data(team_id):
    """Everything both the team page and its Excel export need, gathered once."""
    s = season_arg()
    name = TEAMS.get(team_id)
    if not name:
        abort(404)
    target = team_target(team_id)
    matches = q("""
        SELECT * FROM bits_match
        WHERE season = ? AND (home_id = ? OR away_id = ?)
        ORDER BY played_at
    """, s, team_id, team_id)
    div = matches[0]["division_id"] if matches else None
    table = q("""SELECT * FROM bits_standing WHERE season = ? AND division_id = ?
                 ORDER BY points DESC, diff DESC""", s, div) if div else []
    roster = q("""
        SELECT r.lic, r.player,
               SUM((r.g1>0)+(r.g2>0)+(r.g3>0)+(r.g4>0)) games,
               SUM(COALESCE(r.g1,0)+COALESCE(r.g2,0)+COALESCE(r.g3,0)+COALESCE(r.g4,0)) pins,
               -- The outer MAX takes ONE argument so it is the aggregate; a
               -- two-argument MAX is SQLite's scalar function, which under a
               -- GROUP BY silently reports whichever row the planner landed on
               -- rather than the player's best game of the season.
               MAX(MAX(MAX(COALESCE(r.g1,0),COALESCE(r.g2,0)),
                       MAX(COALESCE(r.g3,0),COALESCE(r.g4,0)))) high_game,
               MAX(r.series) high_series
        FROM bits_result r JOIN bits_match m ON m.match_id = r.match_id
        WHERE m.season = ?
          AND ((r.side='H' AND m.home_id = ?) OR (r.side='A' AND m.away_id = ?))
        GROUP BY r.lic, r.player
        HAVING games > 0
        ORDER BY 1.0*pins/games DESC
    """, s, team_id, team_id)
    # Per-match team aggregates: every game bowled by this team's players in
    # that match, pooled. COALESCE before comparing -- an unbowled 4th game is
    # NULL, and NULL >= 200 poisons the whole SUM expression rather than
    # counting as zero.
    per_match = q("""
        SELECT m.match_id, m.played_at, m.round_id,
               CASE WHEN m.home_id = ? THEN m.away ELSE m.home END opponent,
               (m.home_id = ?) AS at_home,
               SUM((r.g1>0)+(r.g2>0)+(r.g3>0)+(r.g4>0)) games,
               SUM(COALESCE(r.g1,0)+COALESCE(r.g2,0)
                   +COALESCE(r.g3,0)+COALESCE(r.g4,0)) pins,
               SUM((COALESCE(r.g1,0)>=?)+(COALESCE(r.g2,0)>=?)
                   +(COALESCE(r.g3,0)>=?)+(COALESCE(r.g4,0)>=?)) over_target
        FROM bits_result r JOIN bits_match m ON m.match_id = r.match_id
        WHERE m.season = ?
          AND ((r.side='H' AND m.home_id = ?) OR (r.side='A' AND m.away_id = ?))
        GROUP BY m.match_id
        HAVING games > 0
        ORDER BY m.played_at
    """, team_id, team_id, target, target, target, target, s, team_id, team_id)

    played = len(per_match)
    n = last_arg()
    n = n if n and n < played else None
    sel = per_match[-n:] if n else per_match
    windows = [w for w in (3, 5, 10, 20) if w < played]

    def pool(rows):
        g = sum(r["games"] for r in rows)
        pins = sum(r["pins"] for r in rows)
        return {"matches": len(rows), "games": g, "pins": pins,
                "avg": pins / g if g else 0,
                "over_target": sum(r["over_target"] for r in rows)}

    form = pool(sel)
    form["season_avg"] = pool(per_match)["avg"]

    # The chart wants plain dicts (sqlite3.Row is read-only, so the derived
    # average has nowhere to live) and a y-scale that exaggerates the spread --
    # anchoring at zero would squash a season into a flat band near the top.
    chart = [{"date": r["played_at"][:10], "opponent": r["opponent"],
              "round_id": r["round_id"],
              "at_home": r["at_home"], "games": r["games"], "pins": r["pins"],
              "over_target": r["over_target"], "avg": r["pins"] / r["games"]}
             for r in sel]
    lo = hi = 0
    if chart:
        avgs = [c["avg"] for c in chart]
        lo, hi = min(avgs) - 12, max(avgs) + 12
    best = max(chart, key=lambda c: c["avg"]) if chart else None
    worst = min(chart, key=lambda c: c["avg"]) if chart else None

    return dict(season=s, team_id=team_id, name=name, target=target,
                matches=matches, table=table, roster=roster,
                form=form, chart=chart, lo=lo, hi=hi,
                best=best, worst=worst,
                last=n, played=played, windows=windows)


@app.route("/team/<int:team_id>")
def team(team_id):
    return render_template("team.html", **team_data(team_id))


# --- Excel export -------------------------------------------------------------
def window_subtitle(d):
    """One line naming exactly which slice of the season the file holds -- an
    export detached from its filter is the easy way to mislead yourself later."""
    bits = [f"Säsong {label(d['season'])}"]
    if d["last"]:
        rows = d.get("chart") or d.get("hist") or []
        span = ""
        if rows:
            first = rows[0]["date"] if "date" in rows[0].keys() else rows[0]["played_at"][:10]
            lastd = rows[-1]["date"] if "date" in rows[-1].keys() else rows[-1]["played_at"][:10]
            span = f" ({first} – {lastd})"
        bits.append(f"senaste {d['last']} av {d['played']} matcher{span}")
    else:
        bits.append(f"hela säsongen, {d['played']} matcher")
    if not show_practice():
        bits.append("träningsmatcher borträknade")
    return " · ".join(bits)


def filename(stem, d):
    tail = f"senaste {d['last']}" if d["last"] else "hela sasongen"
    return f"{stem} {label(d['season']).replace('/', '-')} {tail}.xlsx"


def send_book(buf, name):
    return send_file(buf, as_attachment=True, download_name=name,
                     mimetype="application/vnd.openxmlformats-officedocument."
                              "spreadsheetml.sheet")


@app.route("/team/<int:team_id>/urval.xlsx")
def team_xlsx(team_id):
    d = team_data(team_id)
    if not d["chart"]:
        abort(404)
    book = xlsx.team_book(d, window_subtitle(d), team_label)
    return send_book(book, filename(team_label(d["name"]), d))


@app.route("/player/<lic>/urval.xlsx")
def player_xlsx(lic):
    d = player_data(lic)
    book = xlsx.player_book(d, window_subtitle(d), team_label)
    return send_book(book, filename(d["name"], d))


MIN_GAMES = 4


def players_data():
    """The season leaderboard, optionally cut to each player's own last N.

    The window is per player, not a shared date cutoff: "senaste 5" on a form
    table has to mean five matches for everyone, or a reserve who bowled once
    since March would rank on a single game against someone's full fifteen.
    That does mean two players' windows can cover different stretches of the
    season -- the Matcher column is there to show it.

    Aggregating in Python rather than SQL because "the last N rows per group"
    needs a window function to express and this is ~1500 rows a season.
    """
    s = season_arg()
    rows = q("""
        SELECT r.lic, r.player, m.played_at,
               (r.g1>0)+(r.g2>0)+(r.g3>0)+(r.g4>0) games,
               COALESCE(r.g1,0)+COALESCE(r.g2,0)
               +COALESCE(r.g3,0)+COALESCE(r.g4,0) pins,
               MAX(MAX(COALESCE(r.g1,0),COALESCE(r.g2,0)),
                   MAX(COALESCE(r.g3,0),COALESCE(r.g4,0))) high_game,
               r.series
        FROM bits_result r JOIN bits_match m ON m.match_id = r.match_id
        WHERE m.season = ?
          AND ((r.side='H' AND m.home LIKE ?) OR (r.side='A' AND m.away LIKE ?))
        ORDER BY m.played_at
    """, s, CLUB, CLUB)

    # A squad listing where the player never went to the line is not a match
    # they played, and letting one eat a slot in a five-match window would
    # quietly measure four.
    by_player = {}
    for r in rows:
        if r["games"]:
            by_player.setdefault(r["lic"], []).append(r)

    played_max = max((len(v) for v in by_player.values()), default=0)
    n = last_arg()
    n = n if n and n < played_max else None
    windows = [w for w in (3, 5, 10, 20) if w < played_max]

    out = []
    for lic, ms in by_player.items():
        sel = ms[-n:] if n else ms
        games = sum(m["games"] for m in sel)
        if games < MIN_GAMES:
            continue
        out.append({"lic": lic, "player": sel[-1]["player"], "mm": len(sel),
                    "games": games, "pins": sum(m["pins"] for m in sel),
                    "high_game": max(m["high_game"] for m in sel),
                    "high_series": max(m["series"] or 0 for m in sel),
                    "first": sel[0]["played_at"][:10],
                    "last": sel[-1]["played_at"][:10]})
    out.sort(key=lambda p: p["pins"] / p["games"], reverse=True)
    return dict(season=s, rows=out, last=n, windows=windows,
                played_max=played_max)


@app.route("/players")
def players():
    return render_template("players.html", **players_data())


@app.route("/players/urval.xlsx")
def players_xlsx():
    d = players_data()
    if not d["rows"]:
        abort(404)
    scope = (f"senaste {d['last']} matcherna per spelare" if d["last"]
             else "hela säsongen")
    book = xlsx.players_book(
        d, f"Säsong {label(d['season'])} · {scope} · minst {MIN_GAMES} serier")
    tail = f"senaste {d['last']}" if d["last"] else "hela sasongen"
    return send_book(book,
                     f"LUMA spelare {label(d['season']).replace('/', '-')} {tail}.xlsx")


def player_data(lic):
    """Everything both the player page and its Excel export need."""
    s = season_arg()
    hist = q("""
        SELECT m.played_at, m.round_id, m.division, m.hall, m.oil_pattern,
               CASE WHEN r.side='H' THEN m.away ELSE m.home END opponent,
               CASE WHEN r.side='H' THEN m.home ELSE m.away END own,
               r.g1, r.g2, r.g3, r.g4, r.series, r.hcp, r.total, r.lane_point, r.place
        FROM bits_result r JOIN bits_match m ON m.match_id = r.match_id
        WHERE r.lic = ? AND m.season = ?
        ORDER BY m.played_at
    """, lic, s)
    if not hist:
        abort(404)

    # Last-N window. One cutoff date drives every section on the page: taking the
    # N most recent league matches and reusing their start date for the frame
    # data keeps the two halves talking about the same stretch of the season.
    # Filtering each source to its own last N would not -- Bowlit only covers
    # about a quarter of the matches, so its "last 5" reaches much further back.
    played = len(hist)
    n = last_arg()
    n = n if n and n < played else None
    if n:
        hist = hist[-n:]
    since = hist[0]["played_at"][:10] if n else None
    windows = [w for w in (3, 5, 10, 20) if w < played]

    games = [g for h in hist for g in (h["g1"], h["g2"], h["g3"], h["g4"]) if g]
    name = q("SELECT player FROM bits_result WHERE lic = ? LIMIT 1", lic)[0]["player"]
    stats = {
        "games": len(games),
        "avg": sum(games) / len(games) if games else 0,
        "high_game": max(games) if games else 0,
        "high_series": max((h["series"] or 0) for h in hist),
        "over_200": sum(1 for g in games if g >= 200),
        "over_220": sum(1 for g in games if g >= 220),
        "under_180": sum(1 for g in games if g < 180),
    }
    shots, per_match = shot_stats(name, s, show_practice(), since=since)
    shot_games = sum(m["games"] for m in per_match)
    return dict(season=s, lic=lic, name=name,
                hist=hist, stats=stats, games=games,
                shots=shots, per_match=per_match, pct=pct,
                shot_games=shot_games, last=n, played=played,
                windows=windows, since=since)


@app.route("/player/<lic>")
def player(lic):
    return render_template("player.html", **player_data(lic))


def split_label(label):
    """Pull the two team names out of a session label like
    "LUMA F1 - BK Target (träningsmatch)". Falls back to generic sides."""
    text = re.sub(r"\s*\([^)]*\)\s*$", "", label or "").strip()
    for sep in (" - ", " – ", " vs ", " mot "):
        if sep in text:
            a, b = text.split(sep, 1)
            return a.strip() or CLUB_LABEL, b.strip() or "Motståndare"
    return (text or CLUB_LABEL), "Motståndare"


@app.route("/session/<int:sid>")
def session(sid):
    """Everything bowled in one session -- every player, no roster filter and no
    minimum-frames cut. This is the view that should match the Bowlit scorecard
    one-for-one; the leaderboards are the place for thresholds, not here."""
    con = connect()
    meta = con.execute("SELECT * FROM social_session WHERE id = ?", (sid,)).fetchone()
    rows = con.execute("""SELECT player, game, game_score, cum_total, balls
                          FROM social_game WHERE match_id = ?
                          ORDER BY player, game""", (sid,)).fetchall()
    if not rows:
        abort(404)
    rmap = roster_map()

    players, order = {}, []
    for r in rows:
        p = players.get(r["player"])
        if p is None:
            p = players[r["player"]] = {
                "player": rmap.get(r["player"], r["player"]),
                "scoreboard": r["player"], "games": [], "pins": 0, "st": {},
                "club": r["player"] in rmap}
            order.append(r["player"])
        p["games"].append({"game": r["game"], "score": r["game_score"],
                           "cum": r["cum_total"]})
        p["pins"] += r["game_score"] or 0
        p["st"] = frames.add(p["st"], frames.stats(json.loads(r["balls"])))
    for p in players.values():
        p["avg"] = p["pins"] / (len(p["games"]) or 1)
    ranked = sorted(players.values(), key=lambda x: -(x["pins"] or 0))
    n_games = max((len(p["games"]) for p in ranked), default=0)

    def side(members, name):
        st = {}
        for m in members:
            st = frames.add(st, m["st"])
        pins = sum(m["pins"] for m in members)
        gs = sum(len(m["games"]) for m in members)
        return {"name": name, "players": members, "pins": pins, "games": gs,
                "avg": pins / gs if gs else 0, "st": st}

    home_name, away_name = split_label(meta["label"] if meta else "")
    sides = [side([p for p in ranked if p["club"]], home_name),
             side([p for p in ranked if not p["club"]], away_name)]
    sides = [s for s in sides if s["players"]]
    return render_template("session.html", season=season_arg(), meta=meta, sid=sid,
                           sides=sides, total=len(ranked), n_games=n_games, pct=pct)


def klubb_points(score):
    """200-klubbens poäng för en serie, enligt klubbens egna regler:
    själva 200-serien ger 2 poäng, varje tiotal över 200 ger 1 poäng och
    entalssiffran divideras med 10 -- alltså 2 + (serie - 200) / 10.
    Undantaget är 300, som ger 30 poäng rakt av.
    """
    return 30.0 if score >= 300 else 2 + (score - 200) / 10.0


CLUB_200 = 200


@app.route("/200-klubben")
def club200():
    """LuMas interna 200-klubb, i samma format som klubben är van vid.

    Bara seriematcher räknas, vilket följer av att underlaget är BITS -- en
    träningsmatch har aldrig en rad där.
    """
    s = season_arg()
    rows = q("""
        SELECT r.lic, r.player, m.played_at, r.g1, r.g2, r.g3, r.g4
        FROM bits_result r JOIN bits_match m ON m.match_id = r.match_id
        WHERE m.season = ?
          AND ((r.side='H' AND m.home LIKE ?) OR (r.side='A' AND m.away LIKE ?))
        ORDER BY m.played_at
    """, s, CLUB, CLUB)

    by_lic, latest = {}, ""
    for r in rows:
        d = by_lic.setdefault(r["lic"], {"lic": r["lic"], "player": r["player"],
                                         "points": 0.0, "n": 0, "played": 0})
        for g in (r["g1"], r["g2"], r["g3"], r["g4"]):
            if not g:
                continue
            d["played"] += 1
            # The stamp under the heading is the last match that counts towards
            # the table, not the last one where somebody happened to break 200.
            latest = max(latest, r["played_at"][:10])
            if g >= CLUB_200:
                d["points"] += klubb_points(g)
                d["n"] += 1

    table = sorted((d for d in by_lic.values() if d["n"]),
                   key=lambda d: (-d["points"], -d["n"], d["player"]))
    for d in table:
        d["avg"] = d["points"] / d["n"]
        d["rate"] = pct(d["n"], d["played"]) or 0

    return render_template("club200.html", season=s, table=table,
                           updated=latest, mark=CLUB_200)


@app.route("/shots")
def shots():
    s = season_arg()
    con = connect()
    rows = con.execute(f"""
        SELECT g.player, g.balls, g.game_score, x.adhoc FROM social_game g
        JOIN ({SESSIONS_SQL}) x ON x.sid = g.match_id
        WHERE x.season = ? {"" if show_practice() else "AND x.adhoc = 0"}
    """, (s,)).fetchall()
    # club roster, so we only rank our own players
    rmap = roster_map()
    agg, disp, tally = {}, {}, {}
    for r in rows:
        bits_name = rmap.get(r["player"])
        if not bits_name:
            continue
        agg[bits_name] = frames.add(agg.get(bits_name, {}),
                                    frames.stats(json.loads(r["balls"])))
        t = tally.setdefault(bits_name, {"games": 0, "pins": 0, "scored": 0, "league": 0})
        t["games"] += 1
        if not r["adhoc"]:
            t["league"] += 1        # only league games have a BITS game to compare against
        if r["game_score"] is not None:
            t["pins"] += r["game_score"]
            t["scored"] += 1
        disp.setdefault(bits_name, bits_name)
    # how many games each player actually bowled that season, per BITS
    bits_games = {r["player"]: r["g"] for r in con.execute("""
        SELECT r.player, SUM((r.g1>0)+(r.g2>0)+(r.g3>0)+(r.g4>0)) g
        FROM bits_result r JOIN bits_match m ON m.match_id = r.match_id
        WHERE m.season = ?
          AND ((r.side='H' AND m.home LIKE ?) OR (r.side='A' AND m.away LIKE ?))
        GROUP BY r.player
    """, (s, CLUB, CLUB))}

    # a fixed four-game cut is meaningless in a season that is one friendly long
    busiest = max((t["games"] for t in tally.values()), default=0)
    default_min = 4 if busiest >= 8 else max(1, busiest // 2)
    min_games = request.args.get("min", type=int) or default_min
    out = []
    for k, v in agg.items():
        t = tally[k]
        if t["games"] < min_games:
            continue
        played = bits_games.get(k, 0)
        out.append({"player": disp[k], "key": k, **v,
                    "games": t["games"],
                    "avg": (t["pins"] / t["scored"]) if t["scored"] else 0,
                    "cov_games": t["league"], "bits_games": played,
                    "cov_pct": pct(t["league"], played),
                    "strike_pct": pct(v["strikes"], v["frames"]),
                    "spare_pct": pct(v["spares"], v["spare_chances"]),
                    "split_pct": pct(v["split_first"], v["first_balls"]),
                    "split_conv": pct(v["split_converted"], v["split_first"]),
                    # An open frame is one that was neither struck nor spared.
                    # frames.opens counts that per frame; the old
                    # spare_chances - spares undercounted it, because a tenth
                    # frame that opens with a strike and then spares adds to
                    # `spares` without ever having added a spare chance.
                    "covered": v["frames"] - v["opens"],
                   "covered_pct": pct(v["frames"] - v["opens"], v["frames"]) or 0,
                   "open_pct": pct(v["opens"], v["spare_chances"]) or 0,
                   # a single-pin leave is a first ball of 9: one pin standing
                   "missed_single": v["single_pin"] - v["single_pin_made"],
                   "missed_single_pct": pct(v["single_pin"] - v["single_pin_made"],
                                            v["single_pin"]) or 0,
                   "single_chances": v["single_pin"],
                    "first_avg": (v["first_ball_pins"] / v["first_balls"]) if v["first_balls"] else 0})
    out.sort(key=lambda x: -(x["strike_pct"] or 0))
    return render_template("shots.html", season=s, rows=out, min_games=min_games)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8768)
    ap.add_argument("--no-reload", action="store_true",
                    help="serve without the auto-reloader")
    ap.add_argument("--debug", action="store_true", help="also show tracebacks")
    a = ap.parse_args()

    reload_on = not a.no_reload
    app.config["LIVE_RELOAD"] = reload_on
    app.config["TEMPLATES_AUTO_RELOAD"] = reload_on
    app.jinja_env.auto_reload = reload_on
    extra = [str(f) for d in WATCH_DIRS for f in d.rglob("*")
             if f.suffix in (".html", ".css") and f.is_file()] if reload_on else None
    if reload_on:
        print(f" * hot reload on: editing anything under {', '.join(str(d) for d in WATCH_DIRS)}"
              f" refreshes the browser")
    app.run(host=a.host, port=a.port, debug=a.debug,
            use_reloader=reload_on, extra_files=extra, threaded=True)
