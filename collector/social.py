"""Read a full booking's scorecards from social.bowlit.nu.

This is the real source for frame-level data, and it is retroactive: give it an
alley id and a booking number and it returns every player, every game, every
ball -- with splits already marked. It replaces the scoresheet-OCR path.

    https://social.bowlit.nu/{alley_id}/{booking_id}
"""
import re
import requests
from bs4 import BeautifulSoup

LUNDS_BOWLING = 1037
BASE = "https://social.bowlit.nu"


def fetch(booking, alley=LUNDS_BOWLING, timeout=60):
    r = requests.get(f"{BASE}/{alley}/{booking}", timeout=timeout,
                     headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    return r.text


def _cell(td):
    """One ball. Bowlit marks splits with class="split" on the ball cell."""
    txt = td.get_text(strip=True)
    classes = td.get("class") or []
    if not txt:
        return None
    return {"ball": txt, "split": "split" in classes}


def parse(html):
    """-> {"games": [...], "players": {name: aggregate}}"""
    soup = BeautifulSoup(html, "lxml")

    games = []
    for div in soup.select("div.scoreTable"):
        rows = div.select("table tbody tr")
        if not rows:
            continue
        balls = [_cell(td) for td in rows[0].find_all("td")]
        balls = [b for b in balls if b is not None]
        totals = []
        if len(rows) > 1:
            for td in rows[1].find_all("td"):
                t = td.get_text(strip=True)
                if t.isdigit():
                    totals.append(int(t))
        games.append({
            "index": div.get("data-index"),
            "lane": _int(div.get("data-lane")),
            "game": _int(div.get("data-serie")),
            "player": div.get("data-name"),
            "player_id": div.get("data-id"),
            "total": _int(div.get("data-total")),
            "completed": (div.get("data-completed") or "").lower() == "true",
            "balls": balls,
            "frame_totals": totals,
        })

    players = {}
    for div in soup.select("div.extra"):
        name_el = div.select_one(".nameXtr")
        if not name_el:
            continue
        chart = div.select_one(".chart")
        total_el = div.select_one(".totalXtr")
        players[name_el.get_text(strip=True)] = {
            "miss": _int(div.get("data-miss")),
            "split": _int(div.get("data-split")),
            "strikes": _int(chart.get("data-strikes")) if chart else None,
            "spares": _int(chart.get("data-spares")) if chart else None,
            "open_frames": _int(chart.get("data-openframes")) if chart else None,
            "frames": _int(chart.get("data-frames")) if chart else None,
            "total": _int(total_el.get_text(strip=True)) if total_el else None,
        }
    return {"games": games, "players": players}


def _int(v):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None
