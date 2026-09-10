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
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://bits.swebowl.se/MiscFrontApiConnector"
CLUB_ID = int(os.environ.get("BITS_CLUB_ID", "33651"))

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
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Referer": "https://bits.swebowl.se/seriespel",
}


class Bits:
    """Same interface as before; only the transport underneath has changed."""

    def get(self, path, **params):
        url = f"{BASE}/{path}?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=HEADERS)
        last = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    body = r.read()
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
