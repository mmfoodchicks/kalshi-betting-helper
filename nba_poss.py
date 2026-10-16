"""Possessions out of the NBA play stream (nba_pbp records) -- the unit the
NBA deep engine plays and its tables are fitted on.

A possession belongs to one offence and ends when the ball changes hands: a
made field goal (unless free throws follow -- an and-one), a defensive
rebound, a turnover, the last free throw of a trip made (or missed and
rebounded by the defence), or the period's end. An offensive rebound extends
the possession and starts a new SHOT OPPORTUNITY inside it.

Each possession: {side, per, start_clk, end_clk, start (START_* below), sd
(offence minus defence before it), opps: [(zone, made, blocked, oreb_after)],
fts: [made...], foul_kinds, tov (0 none / 1 live-ball steal / 2 dead ball),
pts, end (how it ended)}.
"""
import nba_pbp as P

START_PERIOD, START_MAKE, START_DREB, START_STEAL, START_DEADTOV, START_FT, START_OTHER = range(7)
STARTS = ("period", "make", "dreb", "steal", "dead_tov", "ft", "other")
ZONES = ("rim", "short", "mid", "three")


def zone(p):
    if p[P.I_VAL] == 3:
        return 3
    d = p[P.I_DIST]
    if d < 0 or d <= 3:
        return 0
    return 1 if d <= 13 else 2


def possessions(rec):
    """The game's possessions in order."""
    out = []
    cur = None
    plays = list(P.iter_plays(rec))
    score = [0, 0]

    last_end = {"clk": 720, "per": 1}

    def close(end, nxt_start, clk, nxt_side):
        nonlocal cur
        if cur is not None:
            cur["end_clk"] = clk
            cur["end"] = end
            out.append(cur)
        cur = None
        last_end["clk"] = clk
        return nxt_start, nxt_side

    pending = (START_PERIOD, None)
    for i, p in enumerate(plays):
        per, clk, side, code = p[P.I_PER], p[P.I_CLK], p[P.I_TEAM], p[P.I_CODE]
        if per != last_end["per"]:
            # a new period opens on its full clock (12:00, overtime 5:00)
            last_end["per"] = per
            last_end["clk"] = 720 if per <= 4 else 300
        if code == P.END_PERIOD or code == P.END_GAME:
            pending = close("period", START_PERIOD, clk, None)
            last_end["clk"] = 720 if per + 1 <= 4 else 300
            continue
        if code in (P.SUB, P.TIMEOUT, P.REVIEW, P.EJECTION, P.JUMPBALL, P.OTHER):
            continue
        if side not in (0, 1):
            continue
        # the acting side for a defensive play (a defensive rebound, a foul by
        # the defence) is not the offence; the offence is inferred per play
        if code == P.DREB:
            if cur is not None and cur["side"] != side:
                pending = close("dreb", START_DREB, clk, side)
            continue
        off = None
        if code in (P.FGA, P.FT, P.OREB, P.TOV, P.FOUL_OFF):
            off = side
        elif code in (P.FOUL_SHOOT, P.FOUL_PERS, P.FOUL_TAKE, P.FOUL_FLAG):
            off = 1 - side
        elif code == P.FOUL_TECH:
            continue
        if off is None:
            continue
        if cur is None or cur["side"] != off:
            if cur is not None:
                # the ball changed hands without a recorded rebound/turnover
                # (a team rebound on a shot clock violation, a missed final
                # free throw rebounded off the record): close it
                pending = close("implicit", START_OTHER, clk, off)
            st = pending[0] if pending[1] in (None, off) else START_OTHER
            cur = {"side": off, "per": per, "start_clk": last_end["clk"], "start": st,
                   "sd": score[off] - score[1 - off], "opps": [], "fts": [], "fouls": [], "tov": 0,
                   "pts": 0, "end": None, "seq": []}
            pending = (START_OTHER, None)
        if code == P.FGA:
            made = p[P.I_MADE]
            # an and-one: free throws by the same side on the same clock follow
            and1 = False
            if made:
                for q in plays[i + 1:i + 6]:
                    if q[P.I_CLK] != clk:
                        break
                    if q[P.I_CODE] == P.FT and q[P.I_TEAM] == off and q[P.I_VAL] == 11:
                        and1 = True
                        break
            cur["opps"].append([zone(p), made, 1 if p[P.I_FLAGS] & P.F_BLOCKED else 0, 0, p[P.I_VAL], and1])
            cur["seq"].append(["fga", zone(p), made, 1 if p[P.I_FLAGS] & P.F_BLOCKED else 0, and1])
            if made:
                cur["pts"] += p[P.I_VAL]
                score[off] += p[P.I_VAL]
                if not and1:
                    pending = close("make", START_MAKE, clk, 1 - off)
        elif code == P.OREB:
            if cur["opps"]:
                cur["opps"][-1][3] = 1
            elif cur["fts"]:
                cur["fts"][-1] = (cur["fts"][-1][0], 1)
            # a team rebound after a missed FIRST free throw is the feed's
            # bookkeeping (the ball is dead, the second shot follows), not a
            # second chance; only one after a live miss counts
            if cur["seq"] and not (cur["seq"][-1][0] == "fts" and cur["seq"][-1][3] < cur["seq"][-1][1]):
                cur["seq"].append(["oreb"])
        elif code == P.FT:
            k, n = divmod(p[P.I_VAL], 10)
            made = p[P.I_MADE]
            cur["fts"].append((made, 0))
            retained_ = p[P.I_FLAGS] & (P.F_FLAGRANT_FT | P.F_TECH_FT | P.F_CLEAR)
            if k == 1 or not cur["seq"] or cur["seq"][-1][0] != "fts":
                after_make = bool(cur["seq"]) and cur["seq"][-1][0] == "fga" and cur["seq"][-1][2] == 1
                # ["fts", shots in the trip, last made, shots taken so far, and-one, retained]
                cur["seq"].append(["fts", n, made, 0, 1 if (n == 1 and after_make) else 0, 1 if retained_ else 0])
            trip = cur["seq"][-1]
            trip[2] = made
            trip[3] += 1
            if made:
                cur["pts"] += 1
                score[off] += 1
            last = k == n
            retained = p[P.I_FLAGS] & (P.F_FLAGRANT_FT | P.F_TECH_FT | P.F_CLEAR)
            if last and made and not retained:
                pending = close("ft", START_FT, clk, 1 - off)
        elif code == P.TOV:
            cur["tov"] = 1 if p[P.I_FLAGS] & P.F_STEAL else 2
            cur["seq"].append(["tov", cur["tov"]])
            pending = close("tov", START_STEAL if cur["tov"] == 1 else START_DEADTOV, clk, 1 - off)
        elif code in (P.FOUL_SHOOT, P.FOUL_PERS, P.FOUL_TAKE, P.FOUL_FLAG, P.FOUL_OFF):
            cur["fouls"].append(code)
            if code == P.FOUL_OFF:
                pass                  # the turnover row that follows ends it
    if cur is not None:
        cur["end_clk"] = 0
        cur["end"] = "period"
        out.append(cur)
    return out
