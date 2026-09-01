"""Generate data/sample.db -- a synthetic database the dashboard can run on.

The real club.db is not published: it holds 400+ named people, licence numbers
that encode a birth date, and a youth division. Nothing in here is derived from
it. Player names, licences, opponent clubs and every score are invented; only
the club's own team names and ids are real, because they are public BITS data
and the app keys off them.

Deterministic: same seed, same database. Regenerate with

    python tools/make_sample_db.py
"""
import json
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "collector"))
from store import SCHEMA                       # noqa: E402
import frames as frame_stats                   # noqa: E402
import sqlite3                                 # noqa: E402

OUT = ROOT / "data" / "sample.db"
SEED = 20260901

# Real, public: the club's own teams as BITS registers them. The app filters on
# this name and looks teams up by id, so the sample has to agree with bits.py.
TEAMS = {
    90611:  ("Lunds BK Mamba",    "Sydallsvenskan",            4),
    107121: ("Lunds BK Mamba F1", "Div 2 Sodra Gotaland 2",  924),
    184677: ("Lunds BK Mamba F2", "Div 3 Sodra Gotaland 3",  803),
    162063: ("Lunds BK Mamba B",  "Skane Syd 1",             874),
    162098: ("Lunds BK Mamba U",  "NVSK Ungdom",             789),
}

# Invented opponents. Any resemblance to a real club is unintended.
OPPONENTS = [
    "BK Delfinen", "Strike IF", "Kagelgillet BK", "IK Sparen", "BK Nio Ratt",
    "Rannans BK", "Bowling Klubb Vast", "IF Kaglan", "BK Sjustrike",
]
HALLS = [("Bowlinghallen Nord", "Norrkoping"), ("City Bowling", "Vasteras"),
         ("Strandvagens Bowling", "Halmstad"), ("Arena Bowling", "Boras")]
HOME_HALL = ("Bowlinghallen Nord", "Norrkoping")
OILS = ["Hus 40", "Hus 42", "Tavling 39", "Ingen OljeProfil"]

FIRST = ["Anna", "Erik", "Jose", "Karin", "Nils", "Sara", "Oskar", "Lena",
         "Tobias", "Maja", "Rasmus", "Ingrid", "Petter", "Ylva", "Gustav",
         "Elin", "Hampus", "Siri", "Viktor", "Alma", "Fredrik", "Nora",
         "Malte", "Tove", "Axel", "Doris", "Jonas", "Vera", "Ove", "Britt"]
LAST = ["Ek", "Lind", "Berg", "Holm", "Sund", "Falk", "Nystrom", "Ahlgren",
        "Dahl", "Bjork", "Ryd", "Palm", "Sjogren", "Hedlund", "Almqvist"]


def make_people(rng, n):
    """Fabricated players with synthetic licences -- no birth date, no initials."""
    seen, out = set(), []
    while len(out) < n:
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        if name in seen:
            continue
        seen.add(name)
        out.append((f"S{len(out) + 1:05d}", name))
    return out


# --- one simulated game ------------------------------------------------------

def roll_game(rng, skill):
    """Return (rolls, balls) for one game. `skill` is P(strike), roughly."""
    rolls, balls = [], []

    def first_ball():
        if rng.random() < skill:
            return 10
        return min(9, max(0, int(rng.gauss(8.0, 1.8))))

    def mark_split(pins):
        # a wide leave is what gets flagged; never on a strike or a full rack
        return pins <= 8 and rng.random() < 0.12

    for _ in range(9):
        a = first_ball()
        if a == 10:
            rolls.append(10)
            balls.append({"ball": "X", "split": False})
            continue
        split = mark_split(a)
        rolls.append(a)
        balls.append({"ball": str(a) if a else "-", "split": split})
        left = 10 - a
        # converting off a split is harder
        made = rng.random() < (0.35 if split else 0.70)
        b = left if made else rng.randint(0, max(0, left - 1))
        rolls.append(b)
        balls.append({"ball": "/" if a + b == 10 else (str(b) if b else "-"),
                      "split": False})

    # tenth frame: a strike or spare earns the extra ball(s)
    a = first_ball()
    if a == 10:
        rolls.append(10)
        balls.append({"ball": "X", "split": False})
        for _ in range(2):
            c = first_ball()
            rolls.append(c)
            balls.append({"ball": "X" if c == 10 else (str(c) if c else "-"),
                          "split": False})
    else:
        split = mark_split(a)
        rolls.append(a)
        balls.append({"ball": str(a) if a else "-", "split": split})
        left = 10 - a
        made = rng.random() < (0.35 if split else 0.70)
        b = left if made else rng.randint(0, max(0, left - 1))
        rolls.append(b)
        balls.append({"ball": "/" if a + b == 10 else (str(b) if b else "-"),
                      "split": False})
        if a + b == 10:
            c = first_ball()
            rolls.append(c)
            balls.append({"ball": "X" if c == 10 else (str(c) if c else "-"),
                          "split": False})
    return rolls, balls


def score_frames(rolls):
    """Standard ten-pin scoring. Returns (total, cumulative per frame)."""
    total, i, cum = 0, 0, []
    for _ in range(10):
        if i >= len(rolls):
            break
        if rolls[i] == 10:                                    # strike
            total += 10 + sum(rolls[i + 1:i + 3])
            i += 1
        elif i + 1 < len(rolls) and rolls[i] + rolls[i + 1] == 10:   # spare
            total += 10 + (rolls[i + 2] if i + 2 < len(rolls) else 0)
            i += 2
        else:
            total += sum(rolls[i:i + 2])
            i += 2
        cum.append(total)
    return total, cum


def main():
    rng = random.Random(SEED)
    if OUT.exists():
        OUT.unlink()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(OUT)
    con.executescript(SCHEMA)

    people = make_people(rng, len(TEAMS) * 7)
    roster = {}
    for i, tid in enumerate(TEAMS):
        roster[tid] = people[i * 7:(i + 1) * 7]

    match_id = 500000
    social_targets = []

    for season in (2025, 2026):
        for tid, (tname, division, div_id) in TEAMS.items():
            opponents = OPPONENTS[:8]
            # 2025 is a finished season; 2026 has two rounds played so the
            # dashboard has both "Kommande matcher" and "Senaste resultat".
            played_through = 16 if season == 2025 else 2
            table = {o: dict(m=0, w=0, d=0, l=0, hp=0, ap=0, pts=0.0)
                     for o in opponents}
            table[tname] = dict(m=0, w=0, d=0, l=0, hp=0, ap=0, pts=0.0)

            for rnd in range(1, 17):
                opp = opponents[(rnd - 1) % len(opponents)]
                at_home = (rnd % 2 == 1)
                match_id += 1
                played = rnd <= played_through
                hall, city = HOME_HALL if at_home else rng.choice(HALLS)
                month = 9 + (rnd - 1) // 3
                day = 6 + ((rnd - 1) % 3) * 7
                yr = season + (0 if month <= 12 else 1)
                mo = month if month <= 12 else month - 12
                played_at = f"{yr}-{mo:02d}-{day:02d}T{rng.choice([10,11,13,15])}:00:00"

                home, away = (tname, opp) if at_home else (opp, tname)
                home_id = tid if at_home else 0
                away_id = tid if not at_home else 0

                hs = asc = hp = ap = None
                if played:
                    bowlers = rng.sample(roster[tid], 4)
                    ours, theirs = 0, 0
                    results = []
                    for lic, pname in bowlers:
                        skill = rng.uniform(0.28, 0.52)
                        gs, all_balls = [], []
                        for _ in range(4):
                            rolls, balls = roll_game(rng, skill)
                            tot, _cum = score_frames(rolls)
                            gs.append(tot)
                            all_balls.append(balls)
                        ours += sum(gs)
                        results.append((lic, pname, gs, all_balls))
                    theirs = int(ours * rng.uniform(0.90, 1.10))
                    hs, asc = (ours, theirs) if at_home else (theirs, ours)
                    # 20 points a match: 16 on the lanes, 4 on total pins
                    ourp = rng.choice([4, 6, 8, 10, 12, 14, 16])
                    ourp += 4 if ours > theirs else 0
                    theirp = 20 - ourp
                    hp, ap = (ourp, theirp) if at_home else (theirp, ourp)

                    for lic, pname, gs, all_balls in results:
                        con.execute(
                            "INSERT INTO bits_result VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (match_id, lic, pname, "H" if at_home else "A",
                             gs[0], gs[1], gs[2], gs[3], 0, sum(gs), sum(gs),
                             float(rng.randint(0, 4)), float(rng.randint(0, 4)),
                             rng.randint(1, 8)))

                    # keep frame data for a few home matches only, mirroring how
                    # patchy real Bowlit coverage is
                    if at_home and rnd <= 6 and season == 2025 and tid == 90611:
                        social_targets.append((match_id, played_at, season, results))

                    for side, pts, pins, opp_pins in (
                            (tname, ourp, ours, theirs), (opp, theirp, theirs, ours)):
                        t = table[side]
                        t["m"] += 1
                        t["pts"] += pts
                        t["hp"] += pins
                        t["ap"] += opp_pins
                        if pins > opp_pins:
                            t["w"] += 1
                        elif pins < opp_pins:
                            t["l"] += 1
                        else:
                            t["d"] += 1

                con.execute(
                    "INSERT INTO bits_match VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (match_id, season, div_id, division, "Serier", rnd, played_at,
                     home_id, home, away_id, away, hs, asc,
                     float(hp) if hp is not None else None,
                     float(ap) if ap is not None else None,
                     rng.randint(1000, 1099), hall, city,
                     rng.choice(OILS), str(rng.randint(100, 999)), 1 if played else 0))

            for i, (name, t) in enumerate(table.items()):
                if not t["m"]:
                    continue
                con.execute("INSERT OR REPLACE INTO bits_standing VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                            (season, div_id, tid if name == tname else 900000 + i,
                             name, t["m"], t["w"], t["d"], t["l"],
                             t["hp"], t["ap"], t["hp"] - t["ap"], t["pts"]))

    # --- frame-level data for the matches we "covered" ------------------------
    for mid, played_at, season, results in social_targets:
        alley = 1037
        con.execute("INSERT OR REPLACE INTO social_fetch VALUES (?,?,?,?)",
                    (mid, alley, played_at, 4))
        for idx, (lic, pname, gs, all_balls) in enumerate(results):
            agg, cum = {}, 0
            for g, (score, balls) in enumerate(zip(gs, all_balls), start=1):
                cum += score
                # frame_totals is what the scoreboard prints, so recompute it
                # from the same balls rather than storing a second opinion
                _t, frame_totals = score_frames(_rolls_from_balls(balls))
                con.execute("INSERT OR REPLACE INTO social_game VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                            (mid, alley, idx, 11 + (idx % 2), g, pname, lic,
                             cum, score, 1, json.dumps(balls),
                             json.dumps(frame_totals)))
                agg = frame_stats.add(agg, frame_stats.stats(balls))
            con.execute("INSERT OR REPLACE INTO social_player VALUES (?,?,?,?,?,?,?,?,?)",
                        (mid, pname, agg["strikes"], agg["spares"], agg["misses"],
                         agg["splits"], agg["opens"], agg["frames"], sum(gs)))

    # one practice session so the "Traningar" section is not empty
    con.execute("INSERT OR REPLACE INTO social_session VALUES (?,?,?,?,?,?)",
                (990001, 1037, 2026, "2026-08-30", "friendly",
                 "Lunds BK Mamba F1 - BK Delfinen (traningsmatch)"))
    prng = random.Random(SEED + 1)
    for lic, pname in roster[107121][:5]:
        gs, cum = [], 0
        for g in range(1, 5):
            rolls, balls = roll_game(prng, prng.uniform(0.25, 0.5))
            score, _c = score_frames(rolls)
            gs.append(score)
            cum += score
            _t, frame_totals = score_frames(_rolls_from_balls(balls))
            con.execute("INSERT OR REPLACE INTO social_game VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (990001, 1037, 0, 11, g, pname, lic, cum, score, 1,
                         json.dumps(balls), json.dumps(frame_totals)))

    con.execute("INSERT OR REPLACE INTO player_alias VALUES (?,?,?,?)",
                ("ANNA EK", "Anna Ek", 1, "ny i klubben, ingen BITS-historik an"))
    con.execute("INSERT OR REPLACE INTO player_alias VALUES (?,?,?,?)",
                ("PELLE", "PELLE", 0, "BK Delfinen"))

    con.commit()
    n = lambda t: con.execute("SELECT COUNT(*) FROM " + t).fetchone()[0]
    print(f"wrote {OUT}")
    for t in ("bits_match", "bits_result", "bits_standing", "social_game",
              "social_player", "social_session", "player_alias"):
        print(f"  {t:16s} {n(t):6d}")
    con.close()


def _rolls_from_balls(balls):
    """Pin counts back out of the printed tokens, for scoring."""
    rolls, prev = [], 0
    for b in balls:
        tok = b["ball"]
        if tok == "X":
            rolls.append(10); prev = 0
        elif tok == "/":
            rolls.append(10 - prev); prev = 0
        elif tok == "-":
            rolls.append(0); prev = 0
        else:
            v = int(tok)
            rolls.append(v); prev = v
    return rolls


if __name__ == "__main__":
    main()
