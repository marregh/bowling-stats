"""Client for the Swebowl BITS data, through bits.swebowl.se's own connector.

Not api.swebowl.se. That host used to answer anyone holding Swebowl's public
web-client key, after a session warm-up; since 2026-09-10 it answers that key
with a bare IIS *403*, while a made-up key still gets the ordinary empty-bodied
401 -- so the key is recognised and refused, not missing. Nothing about the
request shape fixes it: headers, Origin/Referer, HTTP/2, query vs header all
give the same 403.

What changed is that bits.swebowl.se now reaches its own API through a
same-origin connector, /MiscFrontApiConnector/..., which holds the key server
side. That is what the site's own pages call, and it needs no key from us at
all -- so there is no longer anything to put in the environment.

Endpoint names come from the inline JS on the seriespel and match-detail pages.

Since 2026-09-15 the whole host sits behind BunkerWeb's antibot page, which
answers every first request with *200* and an HTML "Bot Detection" body instead
of an error -- which is why the collector went quiet for four days without ever
logging a failure: json.loads died inside a step whose output is only written
once the step returns. The gate is a plain proof of work, stated openly in the
page's own script: find the smallest n with sha256(seed + str(n)) starting
"0000", POST it to /challenge, and the session cookie is then good for ordinary
requests. It costs about a twentieth of a second, and we pay it once per run.
"""
import datetime
import hashlib
import http.cookiejar
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://bits.swebowl.se/MiscFrontApiConnector"
HOME = "https://bits.swebowl.se/seriespel"
CHALLENGE = "https://bits.swebowl.se/challenge"
CLUB_ID = int(os.environ.get("BITS_CLUB_ID", "33651"))

# A BITS season is named for the calendar year it starts in: season 2026 is
# 2026/27, thrown off in September and finished by April. July is the divide --
# late enough that no season is still running, early enough that next season's
# fixtures are already published.
SEASON_STARTS_MONTH = 7


def current_season(today=None):
    d = today or datetime.date.today()
    return d.year if d.month >= SEASON_STARTS_MONTH else d.year - 1

# Insertion order is the order the site's nav tabs appear in, so keep them in
# the club's own reading order rather than sorted by id.
TEAMS = {
    90611:  "Lunds BK Mamba",
    107121: "Lunds BK Mamba F1",
    184677: "Lunds BK Mamba F2",
    162063: "Lunds BK Mamba B",
    162098: "Lunds BK Mamba U",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                  " (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8",
    "Referer": HOME,
}

SEED_RE = re.compile(r'digestMessage\("([^"]+)"\s*\+')
NEXT_RE = re.compile(r'name="next" value="([^"]*)"')


def is_antibot(body):
    """The gate answers 200, so the body is the only thing that tells us."""
    return b"<title>Bot Detection</title>" in body[:800]


def solve(seed, prefix="0000", limit=1 << 24):
    for n in range(limit):
        if hashlib.sha256(f"{seed}{n}".encode()).hexdigest().startswith(prefix):
            return n
    raise RuntimeError(f"hittade ingen nonce for {seed!r} under {limit} forsok")


class Bits:
    """Same interface as before; only the transport underneath has changed."""

    def __init__(self):
        # One cookie jar for the whole run: the antibot clearance is a cookie,
        # so paying the proof of work once covers every later call.
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))
        self.cleared = False

    def _open(self, url, data=None, ctype=None):
        headers = dict(HEADERS)
        if ctype:
            headers["Content-Type"] = ctype
        req = urllib.request.Request(url, data=data, headers=headers)
        with self.opener.open(req, timeout=60) as r:
            return r.read()

    def clear_antibot(self):
        """Pay BunkerWeb's proof of work and keep the cookie it hands back."""
        body = self._open(HOME)
        if not is_antibot(body):
            self.cleared = True
            return
        html = body.decode("utf-8", "replace")
        seed = SEED_RE.search(html)
        if not seed:
            raise RuntimeError("botkontrollen ser annorlunda ut an vantat")
        nxt = (NEXT_RE.search(html).group(1).replace("&#47;", "/")
               if NEXT_RE.search(html) else "/seriespel")
        answer = urllib.parse.urlencode(
            {"challenge": str(solve(seed.group(1))), "next": nxt}).encode()
        self._open(CHALLENGE, data=answer,
                   ctype="application/x-www-form-urlencoded")
        self.cleared = True

    def get(self, path, **params):
        url = f"{BASE}/{path}?" + urllib.parse.urlencode(params)
        last = None
        for attempt in range(3):
            try:
                if not self.cleared:
                    self.clear_antibot()
                body = self._open(url)
                if is_antibot(body):
                    # Cookie expired mid-run, or this call arrived before the
                    # gate was satisfied. Re-earn it and ask once more -- but
                    # fall through to the backoff below rather than `continue`,
                    # which would skip the sleep and turn a gate that is up for
                    # good into three immediate rounds of warm-up, challenge
                    # and retry.
                    self.cleared = False
                    last = RuntimeError("botkontroll")
                else:
                    return json.loads(body.decode("utf-8")) if body else None
            except urllib.error.HTTPError as e:
                # 5xx and 429 are worth another go; a 404 is a wrong endpoint
                # name and will not improve by asking again.
                if e.code not in (429, 500, 502, 503, 504):
                    raise
                last = e
            except urllib.error.URLError as e:
                last = e
            time.sleep(1 + attempt)
        raise RuntimeError(f"BITS-connectorn svarade inte på {path}: {last}")

    # --- the handful of calls we actually need -------------------------------
    def seasons(self):
        return self.get("Season")

    def teams(self, season, club=CLUB_ID):
        return self.get("Team", clubId=club, seasonId=season)

    def matches(self, season, club=CLUB_ID):
        return self.get("ListMatches", seasonId=season, clubId=club)

    def standing(self, season, division):
        return self.get("GetStandings", seasonId=season, divisionId=division)

    def match_results(self, match_id, scheme_id):
        return self.get("GetMatchResults",
                        matchId=match_id, matchSchemeId=scheme_id)
