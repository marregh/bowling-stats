"""Derive shot statistics from a parsed ball sequence.

Bowlit gives per-player aggregates on the page (strikes/spares/misses/splits),
so anything computed here can be checked against them -- see verify().
"""
import json


def racks(fr, is_tenth):
    """A frame's balls grouped into racks -- one full set of ten pins each.

    Frames 1-9 are always a single rack. The tenth is not: a strike there earns
    a fresh rack, so "X 6 /" is a strike and then a genuine spare attempt, and
    "X X 8" is two strikes and a single bonus ball that can never be a spare.

    Counting per frame instead of per rack is what made the spare column
    disagree with itself -- a tenth-frame spare after a strike was counted in
    `spares` while never adding a chance to compare it against.
    """
    if not is_tenth:
        return [fr]
    out, i = [], 0
    while i < len(fr):
        if fr[i]["ball"] == "X":
            out.append(fr[i:i + 1])
            i += 1
        else:
            out.append(fr[i:i + 2])
            i += 2
    return out


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
             makable=0, makable_made=0, split_tries=0, split_made=0,
             racks=0, split_racks=0,
             single_pin=0, single_pin_made=0, first_ball_pins=0, first_balls=0)
    parts = split_frames(balls)
    for fi, fr in enumerate(parts):
        if not fr:
            continue
        is_tenth = (fi == len(parts) - 1)
        s["frames"] += 1
        for j, b in enumerate(fr):
            tok, sp = b["ball"], b["split"]
            is_first = (j == 0)
            if tok == "X":
                s["strikes"] += 1
            elif tok == "/":
                s["spares"] += 1
                # only a frame's *second* ball converts that frame's first-ball
                # leave; later balls in the tenth are fresh racks, not conversions
                if j == 1 and fr[0]["split"]:
                    s["split_converted"] += 1
            elif tok == "-":
                s["misses"] += 1
            if sp:
                s["splits"] += 1
                if is_first:
                    s["split_first"] += 1
        # Spare accounting, per rack. An attempt is a rack whose first ball
        # left pins AND that had a second ball to knock them down with -- the
        # lone bonus ball of "X X 8" is neither a spare nor a miss. Splits are
        # counted apart: they are their own column, with their own conversion
        # rate, and folding them in here made both the made-count and the
        # total read as something they were not.
        for rk in racks(fr, is_tenth):
            b0 = rk[0]
            tok0 = b0["ball"]
            s["racks"] += 1
            s["first_balls"] += 1
            s["first_ball_pins"] += 10 if tok0 == "X" else (0 if tok0 in "-/" else _num(tok0))
            if b0["split"]:
                s["split_racks"] += 1
            if tok0 == "X" or len(rk) < 2:
                # Either nothing was left, or nothing could be done about what
                # was: the last ball of "9 / 9" and of "X X 9" is a bonus throw
                # with no follow-up. It leaves a single pin standing and the
                # bowler never gets to shoot at it, so counting it as a spare
                # chance invented misses that are not on the scoresheet.
                continue
            made = rk[1]["ball"] == "/"
            if _num(tok0) == 9:
                # a single pin standing: the most makable leave there is
                s["single_pin"] += 1
                s["single_pin_made"] += made
            if b0["split"]:
                s["split_tries"] += 1
                s["split_made"] += made
            else:
                s["makable"] += 1
                s["makable_made"] += made

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
