"""The coaching engine's sixth revision (coach-r6.0): the selectively drifting
league. EXPERIMENTAL and RESEARCH ONLY -- no board, viewer, DFS world or predlog
bucket reads it, and it never writes one (research/reports/nfl_coach_r6.md).

The owner (2026-11-27): "Approved: build R6 using the selectively drifting training
architecture. Clock management, running back target share and quarterback
substitutions use the supported drifting-league methods. Play calling and
no-huddle retain full-history training." The coaching-evolution study
(research/reports/nfl_coach_evo.md) found those three drifting and the other two
not; R6 is R5 with each of the three moved by a league tendency that follows a
season-level random walk (innovation variance by marginal likelihood, fitted on
2016 to 2026 week 12; research/nfl_coach_r6_fit.py ->
research/data/nfl_coach_r6_tables.json), forecast one season ahead:

  * THE CLOCK. R1's between-snap gaps were fitted on 2016-2023. Offences have
    taken longer between snaps every season since (the evo study's runoff
    offset -0.16 -> +0.24). R6 scales the gaps where R1 already lets a team's pace
    act -- a running clock after a run or an in-bounds play, no no-huddle, the
    unhurried phases (nfl_coach_sim._gap's `tempo_mult`) -- by m_clock = exp(the
    2027 forecast of the league's mean log gap there minus the level R1's
    seasons hold). R1's clock code is not copied or edited: R6 sets each side's
    pace before the game, as R1's own hook reads it.
  * THE BACKS' TARGET SHARE. R2's deal was fitted on 2016-2023; R6 multiplies
    every running back's odds of a target by exp(delta_rb), the 2027 forecast of
    the league's RB-target logit minus the level R2's seasons hold. A roster
    whose shares are this season's usage already carries part of that drift: the
    shift is the league's, and the registered test reads it at the league level.
  * THE PULL. R5's hazard plus the 2027 forecast of the league's pull offset
    measured against R5's own hazard (an intercept shift).

The call and no-huddle tables are R1's, unchanged. With `drift` False every
offset is 0 and R6 plays R5's game draw for draw with R5's lines (guard N6-2);
conservation is R5's (N6-4). R1-R5's files are untouched (N6-0).
"""
import json
import math
import os
import random

import nfl_coach_sim as C
import nfl_coach_sim_r2 as E2
import nfl_coach_sim_r3 as E3
import nfl_coach_sim_r4 as E4
import nfl_coach_sim_r5 as E5
import nfl_deep_sim as D

VERSION = "coach-r6.0"
TABLES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "research", "data", "nfl_coach_r6_tables.json")
_T = {"tables": None, "key": None}


class Tables:
    def __init__(self, raw):
        self.raw = raw
        self.m_clock = float(raw["clock"]["m_clock"])
        self.delta_rb = float(raw["rb_target"]["delta"])
        self.mu_pull = float(raw["pull"]["mu"])
        r5 = E5.tables()
        coef = list(r5.coef)
        coef[0] += self.mu_pull
        # R5's tables with the pull's intercept moved: the hazard and the second unit in one object,
        # as R5's tracker and deal read them
        self.pull_q = E5.Tables(dict(r5.raw, coef=coef))


def tables(path=None):
    key = path or TABLES_PATH
    if _T["key"] != key:
        with open(key) as fh:
            _T["tables"] = Tables(json.load(fh))
        _T["key"] = key
    return _T["tables"]


class Team(E2.TeamDeal):
    """R2's TeamDeal with every running back's odds of a target x exp(delta). delta
    0: R2's probabilities, the same dict (parity)."""

    def __init__(self, players, U, mix=None, situational=True, delta=0.0):
        super().__init__(players, U, mix=mix, situational=situational)
        self.delta = float(delta)

    def p(self, kind, b):
        base = super().p(kind, b)
        if kind != "tgt" or self.delta == 0.0 or not base:
            return base
        key = ("tgt6", b)
        if key not in self._cache:
            rl = self.d["tgt"][2]
            f = math.exp(self.delta)
            ws = {i: x * (f if rl.get(i, "").startswith("RB") else 1.0) for i, x in base.items()}
            z = sum(ws.values())
            self._cache[key] = {i: v / z for i, v in ws.items()}
        return self._cache[key]


def play_game(home, away, rng, prng, players=None, T=None, Q=None, X=None, neutral=False, playoff=False,
              form_sd=None, env_sd=None, start=None, hfa=None, effects=True, pull=True, U=None, deal_rngs=None,
              ct=(0.0, 0.0), drift=True, R6=None):
    """R5's game (nfl_coach_sim_r5.play_game, line for line) with the league's pace
    on both sides, R6's pull hazard and, with `players`, the backs' target odds.
    `drift` False: R5's game exactly."""
    R6 = R6 or tables()
    T = T or C.tables()
    Q = Q or (R6.pull_q if drift else E5.tables())
    X = X or E4.tables()
    B = T.base
    if form_sd is None:
        form_sd = C._FORM_SD
    hfa = 1.0 if neutral else (C._HFA_YDS if hfa is None else hfa)
    g = rng.gauss(0.0, form_sd) if form_sd else 0.0
    th, ta = math.exp(g - form_sd * form_sd / 2.0), math.exp(-g - form_sd * form_sd / 2.0)
    sides = (D._Side(home, away, B, hfa * th), D._Side(away, home, B, ta / hfa))
    if env_sd:
        z = rng.gauss(0.0, 1.0)
        pe = math.exp(env_sd * z - env_sd * env_sd / 2.0)
        for sd_ in sides:
            sd_.m_pass *= pe
            sd_.d_comp += env_sd * z
    m = R6.m_clock if drift else 1.0
    if m != 1.0:
        # the league's pace, where R1's own hook (_gap's tempo_mult) lets pace act
        for sd_ in sides:
            sd_.tempo *= m
    base = [(s.d_call, s.d_comp, s.d_int, s.tempo) for s in sides]
    box = (C._box(), C._box())
    log = []
    S = {"per": 1, "clk": 900, "off": None, "down": 1, "dist": 10, "ytg": 75,
         "ot_poss": [False, False], "sudden": False, "over": False, "kick_pending": None,
         "drive_live": False, "playoff": playoff, "log": log, "to": [3, 3], "spike_next": False,
         "nh": 0, "elapsed": 0}
    first_recv = rng.randrange(2)
    S["half2_recv"] = 1 - first_recv
    if start is None or (start.get("off") is None and start.get("kick") is None):
        if start:
            C._apply_start(S, box, start)
        C._kickoff(S, box, sides, T, rng, kicking=1 - first_recv)
    else:
        C._apply_start(S, box, start)
        if start.get("kick") is not None:
            S["kick_pending"] = int(start["kick"])
        else:
            off = int(start["off"])
            S["off"], S["down"] = off, int(start.get("down") or 1)
            S["ytg"] = max(1, min(99, int(start.get("ytg") or 75)))
            S["dist"] = max(1, min(S["ytg"], int(start.get("dist") or 10)))
            S["drive_live"], S["rz"] = True, S["ytg"] <= 20
            box[off]["drives"] += 1
            if S["per"] >= 5:
                S["ot_poss"][off] = True
            if start.get("pending") == "run":
                C._gap(S, box, sides, T, rng, "run", sides[off].tempo)
    tr = E5._Track(Q, X, prng, (start or {}).get("pts") or (0, 0), pull=pull, to=S["to"], playoff=playoff, ct=ct)
    tr.to_per = S["per"]
    while not S["over"]:
        tr.feed(log)
        if effects:
            E4._apply(sides, base, tr, box, X)
        k = S["kick_pending"]
        if k is not None:
            S["kick_pending"] = None
            C._kickoff(S, box, sides, T, rng, kicking=k)
            continue
        C._snap(S, box, sides, T, rng)
    tr.feed(log)
    D._ev(S, box, "fin", ot=int(S["per"] > 4))
    h, a = box
    res = {"h": h, "a": a, "ot": S["per"] > 4, "tie": h["pts"] == a["pts"], "elapsed": S["elapsed"],
           "per": S["per"], "clk": S["clk"], "qb": [dict(q) for q in tr.qb], "log": log, "inputs": tr.inputs}
    if players is not None:
        sn = E3.snaps(log, (start or {}).get("pts") or (0, 0))
        res["sn"] = sn
        U = U or E2.tables()
        drs = deal_rngs or (random.Random(0), random.Random(1))
        dl = R6.delta_rb if drift else 0.0
        res["lines"] = tuple(E5.deal([s for s in sn if s[0] == side], res["h" if side == 0 else "a"],
                                     Team(players[side], U, delta=dl), drs[side], E3.backup_of(players[side])["id"],
                                     tr.qb[side], X if effects else None, U, Q if effects else None)
                             for side in (0, 1))
    return res


def simulate(home, away, players, n=400, seed=None, T=None, U=None, Q=None, X=None, effects=True, pull=True,
             ct=(0.0, 0.0), drift=True, **kw):
    """R5's simulate (nfl_coach_sim_r5.simulate) on R6's game and deal, seeded as R5 seeds them."""
    R6 = tables()
    U = U or E2.tables()
    X = X or E4.tables()
    Q = Q or (R6.pull_q if drift else E5.tables())
    rng = random.Random(seed)
    prng = random.Random((int(seed or 0) * 6007 + 31337) & 0x7FFFFFFF)
    pts = (kw.get("start") or {}).get("pts") or (0, 0)
    games, logs = [], []
    for _ in range(n):
        r = play_game(home, away, rng, prng, T=T, Q=Q, X=X, effects=effects, pull=pull, ct=ct, drift=drift, **kw)
        logs.append(E3.snaps(r.pop("log"), pts))
        games.append(r)
    mixes = E2.mix_of([[x[:6] for x in sn] for sn in logs])
    dl = R6.delta_rb if drift else 0.0
    teams = [Team(list(players[s_]), U, mix=mixes[s_], situational=True, delta=dl) for s_ in (0, 1)]
    bks = [E3.backup_of(list(players[s_]))["id"] for s_ in (0, 1)]
    out = {"games": games, "lines": [], "qb": [g["qb"] for g in games]}
    for j, (r, sn) in enumerate(zip(games, logs)):
        lines = []
        for side in (0, 1):
            drng = random.Random((int(seed or 0) * 1000003 + 2 * j + side) & 0x7FFFFFFF)
            lines.append(E5.deal([x for x in sn if x[0] == side], r["h" if side == 0 else "a"], teams[side], drng,
                                 bks[side], r["qb"][side], X if effects else None, U, Q if effects else None))
        out["lines"].append(tuple(lines))
    return out
