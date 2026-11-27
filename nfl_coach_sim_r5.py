"""The coaching engine's fifth revision (coach-r5.0): the trailing team's pull and
the second unit as drawn decisions. EXPERIMENTAL and RESEARCH ONLY -- no board,
viewer, DFS world or predlog bucket reads it, and it never writes one
(research/reports/nfl_coach_r5.md).

R4 (nfl_coach_sim_r4) with two changes, each learned on nflverse 2016-2024 and
chosen on 2025 by a rule written before 2025 was read (research/nfl_coach_r5_fit.py
-> research/data/nfl_coach_r5_tables.json):

  * THE HAZARD. R3's form under-predicted a trailing team's late pull (30 real
    against 12.2, trailing 17-27 with 2-8 minutes left, 2024-26; 28 of the 30
    strategic). R3 reads the deficit only in points over the square root of the
    time left. A trailing offence's question is POSSESSIONS: how many scores it
    needs (ceil(deficit / 8), a touchdown and a try) against the possessions left
    (an offence's possession every 327 s, the league's 11 a game) -- and its
    timeouts, which buy possessions. The candidates are nested (R3's s6, + the
    need-against-left term, + the timeouts, + the leader's own need term, + the
    head coach's earlier pull rate) and a larger one is kept only when it beats
    the smaller by more than 2 SE on 2025 (`spec` in the tables). The timeouts are
    R1's own: three a half, two in a regular-season overtime, as the engine logs
    them ("tmo"), read at the drive's first snap -- the same moment the pull's
    other inputs are read.
  * THE SECOND UNIT. R4 shifted the lead back's and the top two targets' per-snap
    odds after a strategic pull, so every one of them kept playing a little: in
    R4's games the lead back sat out the rest of the game after 4% of strategic
    pulls, against 50% in the real ones (research/data/nfl_coach/r4_checks.json).
    Real coaches decide per player. R5 draws, once per strategic pull, whether
    the lead back sits (q_b) and whether each of the top two targets sits (q_w,
    which may depend on whether the back sat: fitted, not assumed -- neither
    "everyone leaves together" nor "independent"); a player who plays keeps his
    share shifted by a fitted log-odds (d_b, d_w). The decisions draw on their own
    generator, seeded from the deal generator's state without consuming it, so a
    deal without a strategic pull is R4's draw for draw. Injury pulls keep R4's
    personnel (none: the rule refused it).

Everything else is R4's: the kind of pull (P(injury | margin, time), from the
state, never a report), returns, the backup's calls and efficiency, R1's snaps.
With `pull` False a game is R4's (and R1's) draw for draw and its lines R4's
(guard N5-2). Conservation: both quarterbacks' lines and every carry, catch,
yard, touchdown, sack, kneel and lost fumble sum to the team's box (guard N5-4).
"""
import json
import math
import os
import random

import nfl_coach_sim as C
import nfl_coach_sim_r2 as E2
import nfl_coach_sim_r3 as E3
import nfl_coach_sim_r4 as E4
import nfl_deep_sim as D

VERSION = "coach-r5.0"
TABLES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "research", "data", "nfl_coach_r5_tables.json")
_T = {"tables": None, "key": None}
SPECS = ("s6", "p1", "p2", "p3", "p4")
# the regime candidates (research/nfl_coach_r5_fit.py section 3): R3's form plus
# terms that switch on from a change-point season
ERA_SPECS = ("s6e0", "s6e1", "s6e2", "s6e3", "s6e4", "s6e5", "s6e6")
# an offence's possession every 327 s of game clock: 3,600 s over the league's
# ~11 possessions a team-game (nflverse 2016-2024, 10.9-11.2 a season)
POSS_S = 327.0


def features(sd, gsr, to=3, spec="p2", ct=0.0, era=1.0):
    """The pull hazard's features at a drive's first snap. `sd` the offence's
    margin, `gsr` the game's seconds left (overtime: the period's, at most 600),
    `to` the offence's timeouts left, `ct` the head coach's earlier pull tendency
    (log observed over expected, shrunk; 0 when unknown), `era` 1 from the
    change-point season on (every simulated game is in the present: 1). s6 is
    R3's form exactly (nfl_coach_sim_r3.features); each later spec adds terms."""
    f = E3.features(sd, gsr)
    if spec == "s6":
        return f
    left = (max(0, gsr) + 30) / POSS_S
    nt = math.log1p(math.ceil(max(-sd, 0) / 8.0) / left)
    if spec in ERA_SPECS:
        # E3.features: [1, zl, zl^2, zt, zt^2, lt, zl lt, zt lt, zl^2 lt, zt^2 lt]
        zl, zt, lt, e = f[1], f[3], f[5], float(era)
        if spec == "s6e5":
            return f + [e * nt, e * nt * nt]
        if spec == "s6e6":
            return f + [nt, nt * nt, e * nt, e * nt * nt]
        if spec == "s6e0":
            return f + [e]
        if spec == "s6e1":
            return f + [e * zt]
        if spec == "s6e2":
            return f + [e * zt, e * zt * lt]
        if spec == "s6e3":
            return f + [e * zt, e * zt * zt, e * zt * lt, e * zt * zt * lt]
        return f + [e * zl, e * zt]
    f = f + [nt, nt * nt]
    if spec == "p1":
        return f
    t = max(0, min(3, int(to))) / 3.0
    f = f + [t * (1.0 if sd < 0 else 0.0), t * nt]
    if spec == "p2":
        return f
    nl = math.log1p(math.ceil(max(sd, 0) / 8.0) / left)
    f = f + [nl, nl * nl]
    if spec == "p3":
        return f
    return f + [float(ct)]


def hazard(coef, sd, gsr, to=3, spec="p2", ct=0.0, era=1.0):
    e = sum(c * v for c, v in zip(coef, features(sd, gsr, to, spec, ct, era)))
    e = max(-30.0, min(30.0, e))
    return 1.0 / (1.0 + math.exp(-e))


class Tables:
    def __init__(self, raw):
        self.raw = raw
        self.coef = list(raw["coef"])
        self.spec = raw["spec"]
        self.era_from = raw.get("era_from")
        su = raw["second_unit"]
        self.q_b = float(su["q_b"])
        self.q_w0 = float(su["q_w0"])
        self.q_w1 = float(su["q_w1"])
        self.d_b = float(su["d_b"])
        self.d_w = float(su["d_w"])


def tables(path=None):
    key = path or TABLES_PATH
    if _T["key"] != key:
        with open(key) as fh:
            _T["tables"] = Tables(json.load(fh))
        _T["key"] = key
    return _T["tables"]


def _reset_to(per, playoff):
    """R1's timeouts at the start of period `per` when they reset there, else None:
    three at the second half; two at a regular-season overtime, three at the
    postseason's and after every second postseason overtime period."""
    if per == 3:
        return 3
    if per == 5:
        return 3 if playoff else 2
    if per > 5 and per % 2 == 1:
        return 3
    return None


class _Track(E4._Track):
    """R4's tracker with the offence's timeouts kept from the log and R5's hazard.
    Every input the pull reads is the game state at the drive's first snap:
    margin, game seconds, the offence's timeouts (and the coach term, fixed before
    the game). Nothing after that snap is read."""

    def __init__(self, Q, X, prng, pts=(0, 0), pull=True, to=(3, 3), playoff=False, ct=(0.0, 0.0)):
        super().__init__(Q, X, prng, pts, pull)
        self.to = [int(to[0]), int(to[1])]
        self.playoff = playoff
        self.ct = tuple(ct)
        self.to_per = None
        self.first_to = {}              # drive -> the offence's timeouts at its first snap
        self.inputs = []                # (side, drive, sd, gsr, to) each time the hazard is read (guards)

    def _event(self, e):
        per = e.get("q")
        if per is not None:
            if self.to_per is not None and per != self.to_per:
                r = _reset_to(per, self.playoff)
                if r is not None:
                    self.to = [r, r]
            self.to_per = per
        if e.get("k") == "tmo":
            self.to[e["o"]] = int(e["left"])
        k = e.get("k")
        if k in E3._BREAK:
            self.brk = True
        if k in E2._SNAPS and "o" in e:
            o = e["o"]
            if self.brk or o != self.cur or (self.last_per is not None and per != self.last_per
                                             and (self.last_per == 2 or per >= 5)):
                self.drive += 1
                self.cur, self.brk = o, False
                sd = self.prev[o] - self.prev[1 - o]
                self.first[self.drive] = (sd, E3.game_seconds(per, e["c"]))
                self.first_to[self.drive] = self.to[o]
            self.last_per = per
            if k in E3._IDENT and self.drive not in self.ident:
                self.ident.add(self.drive)
                self.side_drives[o].append(self.drive)
                self._decide(o, self.drive)
        if "s" in e:
            self.prev = e["s"]

    def _decide(self, o, d):
        q = self.qb[o]
        if q["in"] == "starter" and q["pull"] is None and self.pull:
            sd, gsr = self.first[d]
            to = self.first_to[d]
            self.inputs.append((o, d, sd, gsr, to))
            if self.prng.random() < hazard(self.Q.coef, sd, gsr, to, self.Q.spec, self.ct[o]):
                q["pull"], q["in"] = d, "backup"
                q["kind"] = "injury" if self.prng.random() < E4.p_injury(self.X, sd, gsr) else "strategic"
                if q["kind"] == "injury" and self.prng.random() < self.X.ret_rate:
                    u, acc = self.prng.random(), 0.0
                    q["k"] = self.X.ret_k[-1]
                    for kk, pp in zip(self.X.ret_k, self.X.ret_p):
                        acc += pp
                        if u < acc:
                            q["k"] = kk
                            break
        elif q["in"] == "backup" and q["k"] is not None:
            n_bk = sum(1 for x in self.side_drives[o] if x >= q["pull"]) - 1
            if n_bk >= q["k"]:
                q["back"], q["in"] = d, "starter"


def play_game(home, away, rng, prng, players=None, T=None, Q=None, X=None, neutral=False, playoff=False,
              form_sd=None, env_sd=None, start=None, hfa=None, effects=True, pull=True, U=None, deal_rngs=None,
              ct=(0.0, 0.0)):
    """R4's game (nfl_coach_sim_r4.play_game, line for line) with R5's tracker
    and, with `players`, R5's deal. `ct` the two head coaches' pull tendencies
    (0 unless the chosen spec reads them)."""
    T = T or C.tables()
    Q = Q or tables()
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
    tr = _Track(Q, X, prng, (start or {}).get("pts") or (0, 0), pull=pull, to=S["to"], playoff=playoff, ct=ct)
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
        res["lines"] = tuple(deal([s for s in sn if s[0] == side], res["h" if side == 0 else "a"],
                                  E2.TeamDeal(players[side], U), drs[side], E3.backup_of(players[side])["id"],
                                  tr.qb[side], X if effects else None, U, Q if effects else None)
                             for side in (0, 1))
    return res


def _sit_rng(rng):
    """A generator for the sit decisions seeded from the deal generator's state
    WITHOUT drawing from it (so the deal's own draws are R4's)."""
    st = rng.getstate()[1]
    return random.Random((st[0] * 1000003 + st[1] * 9176 + st[623] + 0x5EED) & 0x7FFFFFFFFFFF)


def second_unit(team):
    """The lead back (R2's RB1 role) and the top two wide receivers / tight ends by
    the team's targets -- R4's own definition (nfl_coach_sim_r4._personnel)."""
    rb1 = {i for i, r in team.d["car"][2].items() if r == "RB1"}
    tg = team.d["tgt"][0]
    rl = team.d["tgt"][2]
    wrte = sorted((i for i in tg if rl.get(i, "").startswith(("WR", "TE"))), key=lambda i: -tg[i])
    return rb1, wrte[:2]


def sit_decisions(team, P, srng):
    """One strategic pull's decisions: {player: True (sits for the rest of the
    game) / False (plays, his share shifted)} for the lead back and the top two
    targets. The receivers' sit probability is the fitted one GIVEN the back's
    decision."""
    rb1, top2 = second_unit(team)
    out = {}
    b_sits = srng.random() < P.q_b
    for i in rb1:
        out[i] = b_sits
    qw = P.q_w1 if b_sits else P.q_w0
    for i in top2:
        out[i] = srng.random() < qw
    return out


def _second(p, sits, members, delta):
    """R2's deal probabilities with the drawn second-unit decisions: a seated
    player's weight 0, a playing one's odds x exp(delta). All weight gone (no
    one else could take the touch): R2's probabilities unchanged."""
    if not p:
        return p
    ws = {}
    for i, x in p.items():
        if i in members:
            ws[i] = 0.0 if sits[i] else x * math.exp(delta)
        else:
            ws[i] = x
    z = sum(ws.values())
    return {i: v / z for i, v in ws.items()} if z > 0 else p


def deal(sn, box, team, rng, backup, q, X, U=None, P=None):
    """R4's deal (nfl_coach_sim_r4.deal, line for line) with R5's second unit
    after a STRATEGIC pull. `P` None (or no strategic pull): R4's lines exactly."""
    U = U or E2.tables()
    out = {i: E2._zero() for i in team.pl}
    st = team.qb
    for qq in (st, backup):
        if qq is not None and qq not in out:
            out[qq] = E2._zero()
    pull, back = q.get("pull"), q.get("back")
    kind_ef = X.effects[q["kind"]] if (X is not None and q.get("kind")) else None
    r5 = P is not None and X is not None and q.get("kind") == "strategic" and pull is not None
    sits, car_m, tgt_m = {}, set(), set()
    if r5:
        sits = sit_decisions(team, P, _sit_rng(rng))
        rb1, top2 = second_unit(team)
        car_m, tgt_m = rb1 & set(sits), set(top2) & set(sits)
    rush_w, rec_w = {}, {}
    for o, k, b, yd, td, fum, d, *_ in sn:
        bk = pull is not None and d >= pull and (back is None or d < back)
        qb = backup if bk else st
        ef = kind_ef if bk else None
        if k == "run":
            p = team.p("car", b)
            if p:
                p = _second(p, sits, car_m, P.d_b) if (r5 and bk) else E4._personnel(p, team, ef, "car")
            i = E2._draw(rng, p) if p else st
            if i == st:
                i = qb
            ln = out[i]
            ln["car"] += 1
            ln["car_b"][b] += 1
            ln["rush_td"] += td
            ln["fum"] += fum
            ypc = team.pl[i].get("ypc") if i in team.pl else E3.BACKUP_PLACEHOLDER["ypc"]
            rush_w[i] = rush_w.get(i, 0.0) + max(0.5, float(ypc or 4.2))
        elif k in ("cmp", "inc", "int", "spike"):
            qq = out[qb]
            qq["pass_att"] += 1
            if k == "spike":
                continue
            if k == "int":
                qq["int"] += 1
            if k == "cmp":
                qq["pass_cmp"] += 1
                qq["pass_td"] += td
                qq["pass_yd"] += yd
            if k == "cmp" or (k == "inc" and rng.random() < U.p_tgt_inc) or (
                    k == "int" and rng.random() < U.p_tgt_int):
                p = E2._reweight(team.p("tgt", b), team, k == "cmp")
                p = _second(p, sits, tgt_m, P.d_w) if (r5 and bk) else E4._personnel(p, team, ef, "tgt")
                i = E2._draw(rng, p)
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
            out[qb]["kneels"] += 1
    named = sum(v["fum"] for v in out.values())
    if box.get("fum_lost", named) != named:
        out[st]["fum"] += box["fum_lost"] - named
    for i, v in E2._split(box["rush_yds"], rush_w).items():
        out[i]["rush_yd"] += v
    for i, v in E2._split(box["pass_yds"], rec_w).items():
        out[i]["rec_yd"] += v
    return out


def simulate(home, away, players, n=400, seed=None, T=None, U=None, Q=None, X=None, effects=True, pull=True,
             ct=(0.0, 0.0), **kw):
    """R4's simulate (nfl_coach_sim_r4.simulate) on R5's game and deal: the game,
    pull and deal generators seeded as R4 seeds them."""
    U = U or E2.tables()
    X = X or E4.tables()
    Q = Q or tables()
    rng = random.Random(seed)
    prng = random.Random((int(seed or 0) * 6007 + 31337) & 0x7FFFFFFF)
    pts = (kw.get("start") or {}).get("pts") or (0, 0)
    games, logs = [], []
    for _ in range(n):
        r = play_game(home, away, rng, prng, T=T, Q=Q, X=X, effects=effects, pull=pull, ct=ct, **kw)
        logs.append(E3.snaps(r.pop("log"), pts))
        games.append(r)
    mixes = E2.mix_of([[x[:6] for x in sn] for sn in logs])
    teams = [E2.TeamDeal(list(players[s_]), U, mix=mixes[s_], situational=True) for s_ in (0, 1)]
    bks = [E3.backup_of(list(players[s_]))["id"] for s_ in (0, 1)]
    out = {"games": games, "lines": [], "qb": [g["qb"] for g in games]}
    for j, (r, sn) in enumerate(zip(games, logs)):
        lines = []
        for side in (0, 1):
            drng = random.Random((int(seed or 0) * 1000003 + 2 * j + side) & 0x7FFFFFFF)
            lines.append(deal([x for x in sn if x[0] == side], r["h" if side == 0 else "a"], teams[side], drng,
                              bks[side], r["qb"][side], X if effects else None, U, Q if effects else None))
        out["lines"].append(tuple(lines))
    return out
