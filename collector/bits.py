"""Client for the Swebowl BITS API.

The API only answers once the caller has loaded the BITS site itself: hitting
bits.swebowl.se establishes the session the API checks, and api.swebowl.se hands
out its own ARRAffinity cookie. Skip either and every call returns an
*empty-bodied* 401. The session also lapses if left idle, so a 401 is treated as
"re-warm and retry" rather than a hard failure.
"""
import os
import time
import requests

BASE = "https://api.swebowl.se/api/v1"

# Swebowl's own web client ships this key in the clear, but it is theirs, not
# ours, so it is not committed here. Read it off any request bits.swebowl.se
# makes to api.swebowl.se (it rides in the query string as APIKey) and put it
# in the environment before running a collector.
API_KEY = os.environ.get("SWEBOWL_API_KEY", "")
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


class Bits:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({
            "Referer": "https://bits.swebowl.se/",
            "Origin": "https://bits.swebowl.se",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "application/json, text/javascript, */*; q=0.01",
        })
        self._warm = False

    def _warmup(self):
        self.s.get("https://bits.swebowl.se/", timeout=30)
        self.s.get(BASE + "/", timeout=30)
        self._warm = True

    def get(self, path, **params):
        if not API_KEY:
            raise RuntimeError(
                "SWEBOWL_API_KEY is not set. The BITS API needs Swebowl's "
                "public web-client key: open bits.swebowl.se, watch a "
                "request to api.swebowl.se, and copy the APIKey query "
                "parameter.")
        if not self._warm:
            self._warmup()
        params["APIKey"] = API_KEY
        for attempt in range(3):
            r = self.s.get(f"{BASE}/{path}", params=params, timeout=45)
            if r.status_code == 401:
                time.sleep(1 + attempt)
                self._warmup()
                continue
            r.raise_for_status()
            return r.json() if r.content else None
        raise RuntimeError(f"BITS refused {path} after re-warming the session")

    # --- the handful of calls we actually need -------------------------------
    def seasons(self):
        return self.get("Season")

    def teams(self, season, club=CLUB_ID):
        return self.get("Team", clubId=club, seasonId=season)

    def matches(self, season, club=CLUB_ID):
        return self.get("Match", seasonId=season, clubId=club)

    def standing(self, season, division):
        return self.get("Standing", seasonId=season, divisionId=division)

    def match_results(self, match_id, scheme_id):
        return self.get("matchResult/GetMatchResults",
                        matchId=match_id, matchSchemeId=scheme_id)
