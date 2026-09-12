"""Derive shot statistics from a parsed ball sequence.

Bowlit gives per-player aggregates on the page (strikes/spares/misses/splits),
so anything computed here can be checked against them -- see verify().
"""
import json


def split_frames(balls):
    """Group a flat ball list into 10 frames. Frame 10 takes the remainder."""
    frames, i = [], 0
    for f in range(9):
        if i >= len(balls):
            break
        if balls[i]["ball"] == "X":
            frames.append(balls[i:i + 1]); i += 1
        else:
            frames.append(balls[i:i + 2]); i += 2
    frames.append(balls[i:])
    return frames


def stats(balls):
    """Counts over one game's balls."""
    # `splits` counts every split-marked ball, matching Bowlit's own aggregate so
    # verify() can check us; `split_first` counts only first-ball splits, which is
    # the honest denominator for a conversion rate.
    s = dict(frames=0, strikes=0, spares=0, opens=0, misses=0, splits=0,
             split_first=0, split_converted=0, spare_chances=0,
             makable=0, makable_made=0,
             single_pin=0, single_pin_made=0, first_ball_pins=0, first_balls=0)
    for fr in split_frames(balls):
        if not fr:
            continue
        s["frames"] += 1
        for j, b in enumerate(fr):
            tok, sp = b["ball"], b["split"]
            is_first = (j == 0)
            if is_first:
                s["first_balls"] += 1
                s["first_ball_pins"] += 10 if tok == "X" else (0 if tok in "-/" else _num(tok))
            if tok == "X":
                s["strikes"] += 1
            elif tok == "/":
                s["spares"] += 1
                # only a frame's *second* ball converts that frame's first-ball
                # leave; later balls in the tenth are fresh racks, not conversions
                if j == 1 and fr[0]["split"]:
                    s["split_converted"] += 1
                if j == 1 and _num(fr[0]["ball"]) == 9:
                    s["single_pin_made"] += 1
            elif tok == "-":
                s["misses"] += 1
            if sp:
                s["splits"] += 1
                if is_first:
                    s["split_first"] += 1
        # a spare chance is any frame whose first ball left pins standing
        if fr and fr[0]["ball"] != "X":
            s["spare_chances"] += 1
            # ...and a *makable* one is a spare chance that is not a split.
            # Lumping missed splits in with missed spares reads as sloppiness
            # at the line when it is usually just a bad first ball, and it
            # punishes the player twice: the split is already its own column,
            # with its own conversion rate. Splits are hard by definition, so
            # the honest question for the spare game is what happened to the
            # leaves that were there to be made.
            if not fr[0]["split"]:
                s["makable"] += 1
                if len(fr) > 1 and fr[1]["ball"] == "/":
                    s["makable_made"] += 1
            if _num(fr[0]["ball"]) == 9:
                s["single_pin"] += 1
            if len(fr) > 1 and fr[1]["ball"] not in ("/",):
                s["opens"] += 1
    return s


def _num(tok):
    try:
        return int(tok)
    except (TypeError, ValueError):
        return 0


def add(a, b):
    return {k: a.get(k, 0) + b.get(k, 0) for k in set(a) | set(b)}


def verify(con, match_id):
    """Compare derived counts against Bowlit's own per-player aggregates."""
    out = []
    rows = con.execute("""SELECT player, balls FROM social_game
                          WHERE match_id = ? ORDER BY player, game""", (match_id,))
    agg = {}
    for r in rows:
        agg[r["player"]] = add(agg.get(r["player"], {}), stats(json.loads(r["balls"])))
    for r in con.execute("""SELECT player, strikes, spares, misses, splits, frames
                            FROM social_player WHERE match_id = ?""", (match_id,)):
        mine = agg.get(r["player"])
        if not mine:
            continue
        out.append((r["player"],
                    (mine["strikes"], r["strikes"]),
                    (mine["spares"], r["spares"]),
                    (mine["misses"], r["misses"]),
                    (mine["splits"], r["splits"]),
                    (mine["frames"], r["frames"])))
    return out
