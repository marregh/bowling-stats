"""Announce newly collected matches to a Discord webhook.

Runs after the collectors, and only ever reports matches that already have
results in the database -- so a message means "the data is there to look at",
not "a match happened somewhere". Sending is recorded in `notified`, so a
re-run is silent rather than posting the same match twice.

What is deliberately *not* sent: licence numbers (they encode a birth date) and
opponents' individual scores. Our own players' game scores are the point of the
exercise; everyone else's are not ours to republish. LUMA_NOTIFY_PLAYERS=0
drops the player lines entirely -- worth considering for the U team, which is
a youth side.

Configuration, from the environment or a .env file beside this project (both
gitignored -- the webhook URL is a credential, anyone holding it can post):

    LUMA_DISCORD_WEBHOOK=https://discord.com/api/webhooks/...
    LUMA_PUBLIC_BASE=https://<your-domain>/luma-bowling   # optional, for links
    LUMA_NOTIFY_PLAYERS=1
"""
import argparse
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from store import connect

ROOT = Path(__file__).resolve().parent.parent
CLUB_PREFIX = "Lunds BK Mamba"
BITS_MATCH = "https://bits.swebowl.se/match-detail?matchid={id}"
SOCIAL = "https://social.bowlit.nu/{alley}/{id}"
# How long a match may sit incomplete before it is announced anyway. Long
# enough that a slow protocol is never announced early; short enough that a
# walkover still reaches Discord the next morning.
GRACE_DAYS = 0.5
KIND = "result"

# Discord allows ~5 posts per few seconds per webhook; a six-match Saturday is
# well inside that, but a gap keeps a backlog from tripping the limit.
GAP_SECONDS = 1.5


def env(key, default=""):
    """Environment first, then .env -- so a scheduled task needs no profile."""
    if os.environ.get(key):
        return os.environ[key]
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == key:
                return v.strip().strip('"').strip("'")
    return default


def short(name):
    n = (name or "").strip()
    if not n.startswith(CLUB_PREFIX):
        return n
    return f"LUMA {n[len(CLUB_PREFIX):].strip() or 'A'}"


def dashboard_url(base, m):
    """The match page, carrying its season.

    The site reads ?season= (app.season_arg) to decide which season the whole
    view is about, and falls back to the newest one when it is missing. A link
    to a match from a previous season therefore has to say so, or the page
    opens with the current season selected around last season's match.
    """
    if not base:
        return None
    return f"{base.rstrip('/')}/match/{m['match_id']}?season={m['season']}"


def pending(con, season=None, limit=None):
    """Matches with results collected but never announced.

    Hidden rows are skipped, teams as well as matches. A hidden match announced
    to Discord would link to a page that deliberately does not show it, which is
    the worst of both: the result still gets out, and the link goes nowhere
    useful.
    """
    q = """
        SELECT m.*
        FROM bits_match m
        WHERE m.has_been_played = 1
          AND EXISTS (SELECT 1 FROM bits_result r WHERE r.match_id = m.match_id)
          AND NOT EXISTS (SELECT 1 FROM notified n
                          WHERE n.match_id = m.match_id AND n.kind = ?)
          AND NOT EXISTS (SELECT 1 FROM hidden h WHERE h.kind = 'match'
                          AND h.ref = CAST(m.match_id AS TEXT))
          AND NOT EXISTS (SELECT 1 FROM hidden h WHERE h.kind = 'team'
                          AND h.ref IN (CAST(m.home_id AS TEXT),
                                        CAST(m.away_id AS TEXT)))
          -- Complete protocols only. BITS sets has_been_played while a match
          -- is still being bowled: on 2026-09-19 both the B team's match and
          -- the A team's at Lerum were announced from one serie of four, with
          -- a score and a set of points that were simply wrong by the end. A
          -- Discord message cannot be recalled, so it waits for every game the
          -- scheme calls for.
          --
          -- The consistency check fetch_bits uses cannot catch this: the
          -- player rows sum to the match score at every stage, partial or
          -- not. Only the expected count can.
          AND (m.expected_games IS NULL
               OR (SELECT SUM((r.g1>0)+(r.g2>0)+(r.g3>0)+(r.g4>0))
                   FROM bits_result r WHERE r.match_id = m.match_id)
                   >= m.expected_games
               -- Safety valve: a walkover never reaches the expected count --
               -- the U team's opponents fielded nobody -- and silence would be
               -- the one outcome worse than being early. After this long,
               -- whatever BITS holds is what the match was.
               OR julianday('now') - julianday(m.played_at) > ?)
    """
    args = [KIND, GRACE_DAYS]
    if season:
        q += " AND m.season = ?"
        args.append(season)
    q += " ORDER BY m.played_at"
    if limit:
        q += f" LIMIT {int(limit)}"
    return con.execute(q, args).fetchall()


def build(con, m, base, with_players):
    ours_home = (m["home"] or "").startswith(CLUB_PREFIX)
    us, them = (m["home"], m["away"]) if ours_home else (m["away"], m["home"])
    our_side = "H" if ours_home else "A"
    us_score = m["home_score"] if ours_home else m["away_score"]
    them_score = m["away_score"] if ours_home else m["home_score"]
    us_pts = m["home_pts"] if ours_home else m["away_pts"]
    them_pts = m["away_pts"] if ours_home else m["home_pts"]

    if us_score is None or them_score is None:
        outcome, colour = "Klart", 0x9AA3B2
    elif us_score > them_score:
        outcome, colour = "Vinst", 0x4ADE80
    elif us_score < them_score:
        outcome, colour = "Förlust", 0xE63946
    else:
        outcome, colour = "Oavgjort", 0xFBBF24

    played = (m["played_at"] or "")[:16].replace("T", " ")
    fields = [
        {"name": "Resultat",
         "value": f"**{us_score} – {them_score}**"
                  + (f"  ({us_pts:g} – {them_pts:g} p)" if us_pts is not None else ""),
         "inline": True},
        {"name": "Serie", "value": m["division"] or "–", "inline": True},
        {"name": "Hall", "value": f"{m['hall'] or '–'}"
                                  + (f", {m['city']}" if m["city"] else ""),
         "inline": True},
    ]

    if with_players:
        rows = con.execute("""
            SELECT player, g1, g2, g3, g4, total, series
            FROM bits_result WHERE match_id = ? AND side = ?
            ORDER BY COALESCE(series, total) DESC
        """, (m["match_id"], our_side)).fetchall()
        lines = []
        for r in rows:
            games = " ".join(str(g) for g in (r["g1"], r["g2"], r["g3"], r["g4"])
                             if g is not None)
            tot = r["series"] if r["series"] is not None else r["total"]
            lines.append(f"`{tot or '   '}`  {r['player']}" + (f"  ({games})" if games else ""))
        if lines:
            # Discord caps a field at 1024 characters
            text = "\n".join(lines)
            if len(text) > 1000:
                text = text[:1000].rsplit("\n", 1)[0] + "\n…"
            fields.append({"name": f"{short(us)} — serier", "value": text,
                           "inline": False})

    links = [f"[BITS]({BITS_MATCH.format(id=m['match_id'])})"]
    frames = con.execute("SELECT alley_id, games FROM social_fetch WHERE match_id = ?",
                         (m["match_id"],)).fetchone()
    if frames and frames["games"]:
        links.append(f"[Slagdetaljer]({SOCIAL.format(alley=frames['alley_id'], id=m['match_id'])})")
    dash = dashboard_url(base, m)
    if dash:
        links.append(f"[Dashboard]({dash})")
    fields.append({"name": "Länkar", "value": "  ·  ".join(links), "inline": False})

    return {
        "embeds": [{
            "title": f"{short(us)} – {short(them)}",
            "description": f"{outcome} · {played}",
            "color": colour,
            "url": dash or BITS_MATCH.format(id=m["match_id"]),
            "fields": fields,
            "footer": {"text": f"{m['league'] or ''}  ·  match {m['match_id']}".strip()},
        }]
    }


def post(url, payload):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "User-Agent": "luma-bowling-collector"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would be sent and record nothing")
    ap.add_argument("--season", type=int)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--match", type=int, help="announce one match, even if already sent")
    ap.add_argument("--seed", action="store_true",
                    help="mark everything currently collected as already announced, "
                         "sending nothing (run once, before the first real run)")
    a = ap.parse_args()

    con = connect()

    if a.seed:
        # Every match already in the database was played before anyone asked
        # for notifications. Without this, the first real run would announce a
        # whole season at once.
        rows = pending(con)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        con.executemany("INSERT OR REPLACE INTO notified VALUES (?,?,?)",
                        [(m["match_id"], KIND, now) for m in rows])
        con.commit()
        print(f"{len(rows)} redan insamlade matcher markerade som meddelade "
              f"(inget skickat)")
        return 0
    url = env("LUMA_DISCORD_WEBHOOK")
    base = env("LUMA_PUBLIC_BASE")
    with_players = env("LUMA_NOTIFY_PLAYERS", "1") not in ("0", "false", "no")

    if not url and not a.dry_run:
        print("LUMA_DISCORD_WEBHOOK är inte satt (miljövariabel eller .env) "
              "— hoppar över notifieringar")
        return 0

    if a.match:
        rows = con.execute("SELECT * FROM bits_match WHERE match_id = ?",
                           (a.match,)).fetchall()
    else:
        rows = pending(con, a.season, a.limit)

    if not rows:
        print("inget nytt att meddela")
        return 0

    sent = 0
    for m in rows:
        payload = build(con, m, base, with_players)
        title = payload["embeds"][0]["title"]
        if a.dry_run:
            print(json.dumps(payload, ensure_ascii=False, indent=1))
            continue
        try:
            code = post(url, payload)
        except urllib.error.HTTPError as e:
            print(f"  {title}: Discord svarade {e.code} {e.read()[:200]!r}")
            continue
        except urllib.error.URLError as e:
            print(f"  {title}: nådde inte Discord: {e}")
            continue
        con.execute("INSERT OR REPLACE INTO notified VALUES (?,?,?)",
                    (m["match_id"], KIND,
                     datetime.now(timezone.utc).isoformat(timespec="seconds")))
        con.commit()
        sent += 1
        print(f"  {title}  -> {code}")
        time.sleep(GAP_SECONDS)

    print(f"{sent} matcher meddelade" if not a.dry_run
          else f"{len(rows)} matcher skulle meddelats")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
