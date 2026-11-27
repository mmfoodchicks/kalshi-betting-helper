"""The coaching engine's second revision (coach-r2.0): who gets the ball, by the
situation of each snap. EXPERIMENTAL and RESEARCH ONLY -- no board, viewer, DFS
world or predlog bucket reads it, and it never writes one
(research/reports/nfl_coach_r2_evidence.md).

R1 (`nfl_coach_sim`, coach-r1.0, pinned by its registration) plays the game:
this module never edits it and never draws from the game's generator. A game
here is R1's game draw for draw; R2 then DEALS that game's box to the players,
snap by snap, from R1's passive log:

  * every carry and every target goes to one player, drawn from his expected
    share times a role x situation multiplier (`bucket`: inside the 2, the 5,
    the 10, the last two minutes of a half, a big lead, a late lead, a big
    deficit, passing downs, short yardage, the rest), fitted on nflverse
    2016-2023 (research/nfl_coach_r2_fit.py -> research/data/nfl_coach_r2_tables.json);
  * the multipliers REDISTRIBUTE, never add: each player's weight is calibrated
    (`calibrate`) so his expected share over the matchup's own expected mix of
    situations is the share he was given (Sleeper's line in production terms).
    A blowout world takes carries from the lead back; the average world does
    not. The control deal (`situational=False`) is the same code at the static
    share, so the two differ by the multipliers alone;
  * touchdowns go to the player who had the ball on the scoring snap, fumbles
    to the man who held it, interceptions and sacks to the passer;
  * yards: each team's rushing and receiving yards are split over its players
    by touches x the player's yards per touch, largest remainder, so they sum
    to the box exactly (player efficiency is an input, as in production's deal;
    the per-snap yards are the engine's league tables and carry no player).

Conservation (guards NR in tests/guards_auto_nfl_coach_r2.py): carries, rushing
yards and rushing touchdowns, targets <= attempts, receptions, receiving yards
and touchdowns, passing attempts, completions, yards, touchdowns and
interceptions all sum to the team's box exactly, in every game.

What the audit did NOT support is not here: head-coach and team deviations on
usage (no year-to-year persistence of a coach's resting habit, r 0.03 +/- 0.08),
the goal-line specialist's own in-season deviation (+0.003 to +0.018 nats a
team-game), fatigue, halftime, opponent adaptation and coach interactions.
"""
import json
import math
import os
import random

import nfl_coach_sim as C

VERSION = "coach-r2.0"          # this engine's own version; nothing in production reads it
TABLES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "research", "data", "nfl_coach_r2_tables.json")

BUCKETS = ("neutral", "goal_line", "inside5", "inside10", "two_min", "lead_big", "lead_late",
           "trail_big", "pass_down", "short_yd")
NB = len(BUCKETS)
CARRY_ROLES = ("QB", "RB1", "RB2", "RB3", "WR", "TE", "OT")
TGT_ROLES = ("RB1", "RB2", "RB3", "WR1", "WR2", "WR3", "TE1", "TE2", "OT")
_CAL_ITERS = 40                 # the calibration's fixed point settles to 1e-9 in ~25 on real rosters
_T = {"tables": None, "key": None}


def bucket(per, hsr, sd, down, dist, yl):
    """The situation a snap is dealt in, from the offence's side: field zone
    first (it decides touchdowns), then the clock, the score, the down. `hsr`
    is the half's seconds left, `yl` the yards to the goal line."""
    if 1 <= yl <= 2:
        return 1
    if 3 <= yl <= 5:
        return 2
    if 6 <= yl <= 10:
        return 3
    if hsr <= 120 and per in (2, 4):
        return 4
    if (sd >= 17 and per >= 3) or (sd >= 14 and per == 4):
        return 5
    if 9 <= sd and per == 4:
        return 6
    if sd <= -14 and per >= 3:
        return 7
    if (down >= 3 and dist >= 5) or (down == 2 and dist >= 10):
        return 8
    if down >= 3 and dist <= 2:
        return 9
    return 0


def half_seconds(per, clk):
    """The half's seconds left for a period and its clock (overtime: its own)."""
    return clk + 900 if per in (1, 3) else clk


def grp(pos):
    pos = (pos or "").upper()
    if pos in ("RB", "FB", "HB"):
        return "RB"
    if pos in ("WR", "TE", "QB"):
        return pos
    return "OT"


def roles(car, tgt, pos, kind):
    """Pre-game roles: carries -- the backs ranked by expected carries (RB1,
    RB2, RB3 for the rest), the quarterback, receivers, tight ends; targets --
    the backs in the same order, receivers and tight ends ranked by expected
    targets. `car`/`tgt` {player: expected count or share}, `pos` {player:
    position}. Only players with a positive figure of `kind` are dealt."""
    r = {}
    if kind == "car":
        rbs = sorted([i for i in car if car[i] > 0 and grp(pos.get(i)) == "RB"], key=lambda i: -car[i])
        for j, i in enumerate(rbs):
            r[i] = "RB1" if j == 0 else "RB2" if j == 1 else "RB3"
        for i in car:
            if car[i] > 0 and i not in r:
                g = grp(pos.get(i))
                r[i] = g if g in ("QB", "WR", "TE") else "OT"
    else:
        rbs = sorted([i for i in tgt if tgt[i] > 0 and grp(pos.get(i)) == "RB"],
                     key=lambda i: (-car.get(i, 0), -tgt[i]))
        for j, i in enumerate(rbs):
            r[i] = "RB1" if j == 0 else "RB2" if j == 1 else "RB3"
        for P_, names in (("WR", ("WR1", "WR2", "WR3")), ("TE", ("TE1", "TE2", "TE2"))):
            lst = sorted([i for i in tgt if tgt[i] > 0 and grp(pos.get(i)) == P_], key=lambda i: -tgt[i])
            for j, i in enumerate(lst):
                r[i] = names[min(j, 2)]
        for i in tgt:
            if tgt[i] > 0 and i not in r:
                r[i] = "OT"
    return r


class Tables:
    """The fitted usage tables: per kind ("car", "tgt") the role x bucket
    multipliers, the role-only constants, the league's mix of situations, and
    the share of incompletions and interceptions that had a target."""

    def __init__(self, raw):
        self.raw = raw
        self.version = raw.get("version")
        self.M = {k: {r: list(v) for r, v in raw["mult"][k].items()} for k in ("car", "tgt")}
        self.M0 = {k: dict(raw["mult_role_only"][k]) for k in ("car", "tgt")}
        self.mix = {k: list(raw["mix"][k]) for k in ("car", "tgt")}
        self.p_tgt_inc = float(raw["p_target"]["inc"])
        self.p_tgt_int = float(raw["p_target"]["int"])


def tables(path=None):
    key = path or TABLES_PATH
    if _T["key"] != key:
        with open(key) as fh:
            _T["tables"] = Tables(json.load(fh))
        _T["key"] = key
    return _T["tables"]


# ---- the deal's probabilities ------------------------------------------------------------
def calibrate(w, rl, M, mix, iters=_CAL_ITERS):
    """Weights c (a player each) so that p_i(b) = w_i c_i M[r_i][b] / sum_j(...)
    averages, over the situation mix `mix`, to the player's own share w_i /
    sum(w): the multipliers move WHEN a player gets the ball, not how often on
    average. `w` {player: weight > 0}, `rl` {player: role}."""
    tot = sum(w.values())
    tgt = {i: w[i] / tot for i in w}
    msum = sum(mix)
    f = [m / msum for m in mix] if msum > 0 else [1.0 / NB] * NB
    c = {i: 1.0 for i in w}
    for _ in range(iters):
        avg = {i: 0.0 for i in w}
        for b in range(NB):
            if f[b] <= 0:
                continue
            ws = {i: w[i] * c[i] * M[rl[i]][b] for i in w}
            z = sum(ws.values())
            if z <= 0:
                continue
            for i in w:
                avg[i] += f[b] * ws[i] / z
        for i in w:
            if avg[i] > 0:
                c[i] *= tgt[i] / avg[i]
    return c


def probs(w, c, rl, M, b):
    """{player: probability} that the snap in bucket b is his."""
    ws = {i: w[i] * c[i] * M[rl[i]][b] for i in w}
    z = sum(ws.values())
    if z <= 0:
        n = len(w)
        return {i: 1.0 / n for i in w}
    return {i: x / z for i, x in ws.items()}


def _draw(rng, p):
    u = rng.random()
    acc = 0.0
    last = None
    for i, x in p.items():
        acc += x
        last = i
        if u < acc:
            return i
    return last


def _split(total, weights):
    """Integer split of `total` (may be negative) over {player: weight >= 0},
    largest remainder: whole parts summing to the total exactly. All-zero
    weights split evenly."""
    ks = list(weights)
    if not ks:
        return {}
    s = sum(weights.values())
    share = {k: (weights[k] / s if s > 0 else 1.0 / len(ks)) for k in ks}
    exact = {k: share[k] * total for k in ks}
    base = {k: math.floor(exact[k]) for k in ks}
    rem = int(total - sum(base.values()))
    order = sorted(ks, key=lambda k: -(exact[k] - base[k]))
    for k in order[:rem]:
        base[k] += 1
    return base


# ---- the game's snaps, from R1's log ----------------------------------------------------
_SNAPS = ("run", "cmp", "inc", "int", "sack", "kneel", "spike")


def snaps(log):
    """[(side, kind, bucket, yards, td, fumble lost)] for every offensive snap in
    R1's passive log, the score read BEFORE the snap (each event carries the
    score after it)."""
    out = []
    prev = (0, 0)
    for e in log:
        k = e.get("k")
        if k in _SNAPS and "o" in e:
            o = e["o"]
            sd = prev[o] - prev[1 - o]
            b = bucket(e["q"], half_seconds(e["q"], e["c"]), sd, e.get("d", 1), e.get("t", 10), e.get("y", 50))
            out.append((o, k, b, int(e.get("yd", 0)), int(e.get("td", 0)), 1 if e.get("to") == "fum" else 0))
        if "s" in e:
            prev = e["s"]
    return out


def mix_of(games_snaps):
    """Per side, per kind: the count of carries / targets in each bucket over a
    batch of games' snaps -- the matchup's expected mix of situations."""
    m = [{"car": [0] * NB, "tgt": [0] * NB} for _ in range(2)]
    for sn in games_snaps:
        for o, k, b, *_ in sn:
            if k == "run":
                m[o]["car"][b] += 1
            elif k in ("cmp", "inc", "int"):
                m[o]["tgt"][b] += 1
    return m


class TeamDeal:
    """One team's players, ready to deal: roles, the calibrated weights for the
    situational deal (or the static share for the control). `players` is a
    list of {id, pos, car, tgt, ypc, ypr, cr} -- expected carries and targets
    (any scale), yards per carry and per catch, catch rate -- with exactly one
    quarterback first among the QBs (the passer)."""

    def __init__(self, players, U, mix=None, situational=True):
        self.pl = {p["id"]: p for p in players}
        pos = {p["id"]: p.get("pos") for p in players}
        car = {p["id"]: float(p.get("car") or 0.0) for p in players}
        tgt = {p["id"]: float(p.get("tgt") or 0.0) for p in players}
        qbs = [p["id"] for p in players if grp(p.get("pos")) == "QB"]
        self.qb = qbs[0] if qbs else (players[0]["id"] if players else None)
        self.situational = situational
        self.d = {}
        for kind, src in (("car", car), ("tgt", tgt)):
            w = {i: v for i, v in src.items() if v > 0}
            if not w:
                # nobody projected for this kind: every non-quarterback evenly,
                # else the quarterback -- a carry or a catch never goes unowned,
                # so the box's yards and touchdowns always land on someone
                w = {i: 1.0 for i in self.pl if i != self.qb} or ({self.qb: 1.0} if self.qb else {})
                src.update(w)
            rl = roles(car, tgt, pos, kind)
            for i in w:
                # a caller that knows the roles (the research replays rank on
                # raw as-of counts, then scale them) may name them
                rl[i] = self.pl[i].get("role_" + kind) or rl.get(i) or "OT"
            mx = (mix or {}).get(kind) or U.mix[kind]
            c = calibrate(w, rl, U.M[kind], mx) if (situational and w) else {i: 1.0 for i in w}
            M = U.M[kind] if situational else {r: [1.0] * NB for r in U.M[kind]}
            self.d[kind] = (w, c, rl, M)
        self._cache = {}

    def p(self, kind, b):
        key = (kind, b)
        if key not in self._cache:
            w, c, rl, M = self.d[kind]
            self._cache[key] = probs(w, c, rl, M, b) if w else {}
        return self._cache[key]


def _zero():
    return {"car": 0, "rush_yd": 0, "rush_td": 0, "tgt": 0, "rec": 0, "rec_yd": 0, "rec_td": 0,
            "fum": 0, "pass_att": 0, "pass_cmp": 0, "pass_yd": 0, "pass_td": 0, "int": 0, "sacks": 0,
            "kneels": 0, "car_b": [0] * NB}


def deal(sn, box, team, rng, U=None):
    """One side's box dealt to its players over that side's snaps `sn` (from
    `snaps`, already filtered to the side). Returns {player: line}."""
    U = U or tables()
    out = {i: _zero() for i in team.pl}
    qb = team.qb
    if qb is not None and qb not in out:
        out[qb] = _zero()
    rush_w, rec_w = {}, {}
    for o, k, b, yd, td, fum in sn:
        if k == "run":
            p = team.p("car", b)
            i = _draw(rng, p) if p else qb
            ln = out[i]
            ln["car"] += 1
            ln["car_b"][b] += 1
            ln["rush_td"] += td
            ln["fum"] += fum
            rush_w[i] = rush_w.get(i, 0.0) + max(0.5, float(team.pl.get(i, {}).get("ypc") or 4.2))
        elif k in ("cmp", "inc", "int", "spike"):
            q = out[qb]
            q["pass_att"] += 1
            if k == "spike":
                continue
            if k == "int":
                q["int"] += 1
            if k == "cmp":
                q["pass_cmp"] += 1
                q["pass_td"] += td
            # every completion has a receiver; a miss had one at the measured rate
            if k == "cmp" or (k == "inc" and rng.random() < U.p_tgt_inc) or (
                    k == "int" and rng.random() < U.p_tgt_int):
                p = team.p("tgt", b)
                # a completion goes to a receiver by share x catch rate, a
                # targeted miss by share x (1 - catch rate): production's own split
                p = _reweight(p, team, k == "cmp")
                i = _draw(rng, p)
                ln = out[i]
                ln["tgt"] += 1
                if k == "cmp":
                    ln["rec"] += 1
                    ln["rec_td"] += td
                    ln["fum"] += fum
                    rec_w[i] = rec_w.get(i, 0.0) + max(1.0, float(team.pl.get(i, {}).get("ypr") or 9.0))
        elif k == "sack":
            out[qb]["sacks"] += 1
            out[qb]["fum"] += fum
        elif k == "kneel":
            # the box score files a kneel as the quarterback's rush for -1 (production's deal does too)
            out[qb]["kneels"] += 1
    # an untargeted completion cannot happen above (every completion draws a
    # receiver when one exists); yards follow the touches
    # R1's log names the fumble's play on every path but one or two (a lost
    # fumble on a snap that ends in a safety logs no turnover: 3 team-games in
    # 4,000 measured); the box's count is the truth and an unnamed one is the passer's
    named = sum(v["fum"] for v in out.values())
    if box.get("fum_lost", named) != named:
        out[qb]["fum"] += box["fum_lost"] - named
    for i, v in _split(box["rush_yds"], rush_w).items():
        out[i]["rush_yd"] += v
    for i, v in _split(box["pass_yds"], rec_w).items():
        out[i]["rec_yd"] += v
    out[qb]["pass_yd"] = box["pass_yds"]
    return out


def _reweight(p, team, caught):
    ws = {}
    for i, x in p.items():
        cr = team.pl.get(i, {}).get("cr")
        cr = 0.7 if cr is None else min(0.95, max(0.25, float(cr)))
        ws[i] = x * (cr if caught else 1.0 - cr)
    z = sum(ws.values())
    return {i: v / z for i, v in ws.items()} if z > 0 else p


# ---- games ------------------------------------------------------------------------------
def play_game(home, away, rng, players, deal_rng, T=None, U=None, mixes=None, situational=True, **kw):
    """R1's game on `rng` (draw for draw R1's: the log is passive and the deal
    draws only on `deal_rng`), then its box dealt to (home players, away
    players). `mixes` (per side, from `mix_of`) is the matchup's expected mix of
    situations; default the league's."""
    log = []
    res = C.play_game(home, away, rng, T, log=log, **kw)
    sn = snaps(log)
    U = U or tables()
    for side, key in ((0, "h"), (1, "a")):
        td = TeamDeal(players[side], U, mix=(mixes[side] if mixes else None), situational=situational)
        res[key + "_players"] = deal([s for s in sn if s[0] == side], res[key], td, deal_rng, U)
    return res


def simulate(home, away, players, n=400, seed=None, T=None, U=None, arms=("r2", "static"), **kw):
    """n games of the matchup; every game dealt by each arm on the SAME game
    (paired), the situational arm calibrated to this batch's own mix of
    situations so each player's expected share is the one he was given.
    Returns {"games": [result without players], arm: [(home lines, away lines)]}."""
    U = U or tables()
    rng = random.Random(seed)
    games, logs = [], []
    for _ in range(n):
        log = []
        res = C.play_game(home, away, rng, T, log=log, **kw)
        games.append(res)
        logs.append(snaps(log))
    mixes = mix_of(logs)
    out = {"games": games, "mixes": mixes}
    for a, arm in enumerate(arms):
        teams = [TeamDeal(players[s], U, mix=mixes[s], situational=(arm == "r2")) for s in (0, 1)]
        drng = random.Random((seed or 0) * 7919 + 104729 * (a + 1))
        lines = []
        for res, sn in zip(games, logs):
            lines.append(tuple(deal([s for s in sn if s[0] == side], res["h" if side == 0 else "a"],
                                    teams[side], drng, U) for side in (0, 1)))
        out[arm] = lines
    return out
