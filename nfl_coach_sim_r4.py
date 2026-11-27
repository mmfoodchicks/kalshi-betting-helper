"""The coaching engine's fourth revision (coach-r4.0): personnel-aware decisions.
EXPERIMENTAL and RESEARCH ONLY -- no board, viewer, DFS world or predlog bucket
reads it, and it never writes one (research/reports/nfl_coach_r4.md).

The owner's chain, inside the simulated game: a lead builds -> the starter is
pulled (R3's learned hazard, unchanged) -> the offence's calls and efficiency
change with the backup in -> the backs' and receivers' workloads change ->
R1's clock and possessions follow from the changed calls -> the fantasy lines
change. Nothing here is a threshold; every effect is learned on nflverse
2016-2024 and entered only by the rule written before the development seasons
were read (3+ SE on the fit seasons, the same sign on 2025 and 2026 weeks 1-12;
research/nfl_coach_r4_fit.py -> research/data/nfl_coach_r4_tables.json).

  * THE FORK. This module owns its own copy of R1's game loop (`play_game`) so it
    holds the two teams' side objects. It plays R1's snaps (`nfl_coach_sim._snap`,
    unedited) and between snaps sets the side's call, completion and interception
    shifts and its tempo for whoever is at quarterback. No pull, no change: a game
    without a substitution is R1's game draw for draw, and its lines are R3's
    (guards N4-2, N4-3). Pulls, their kind and returns draw on their own
    generator (`prng`), never the game's.
  * THE PULL is R3's: the hazard at the first snap of each identified drive,
    drawn at that drive's first quarterback-identifying snap (the moment the
    engine knows the drive is identified). It is the same drive, state and
    order R3 draws after the game, so the pull falls on the same drive.
  * TWO KINDS OF PULL, from the record and not from the margin. An injury is the
    play description's "was injured" or the next week's injury report (the
    report check: 50% of description-flagged pulled starters were on the next
    report, 5% of the rest). The kind is drawn at the pull from P(injury | the
    state), a logistic fitted on 717 pulls. An injured starter comes back with
    the measured probability (11%) after the measured number of the backup's
    identified drives; a strategic pull is permanent.
  * WITH THE BACKUP IN (logit shifts on R1's own probabilities; odds ratios on
    R2's deal), by kind -- the values are the tables', 0 or 1 where the rule
    refused them:
      strategic  the call by margin (leading +, trailing -, close -), completion
                 -, the lead back's carries (odds x0.48) and the top two
                 targets' targets (x0.74) -- the second unit comes in with him;
      injury     completion -, interceptions +; the personnel is unchanged.
    Measured and refused by the rule: tempo (z 2.2 strategic, 1.1 injury),
    air yards (n.s.), sacks (n.s.), the injury call (z -3.0 close, not 3).

Conservation is R2's and R3's: both quarterbacks' lines and every carry, catch,
yard, touchdown, sack, kneel and lost fumble sum to the team's box (guard N4-4).
"""
import json
import math
import os
import random

import nfl_coach_sim as C
import nfl_coach_sim_r2 as E2
import nfl_coach_sim_r3 as E3
import nfl_deep_sim as D

VERSION = "coach-r4.0"
TABLES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "research", "data", "nfl_coach_r4_tables.json")
_T = {"tables": None, "key": None}
ZERO = {"call_lead": 0.0, "call_trail": 0.0, "call_close": 0.0, "comp": 0.0, "int": 0.0, "tempo": 1.0,
        "rb1": 1.0, "top2": 1.0}


class Tables:
    def __init__(self, raw):
        self.raw = raw
        self.effects = {k: dict(v) for k, v in raw["effects"].items()}
        self.type_coef = list(raw["type_coef"])
        self.ret_rate = float(raw["returns"]["rate"])
        dk = sorted(raw["returns"]["drives_out"].items(), key=lambda kv: int(kv[0]))
        self.ret_k = [int(k) for k, _ in dk]
        self.ret_p = [float(v) for _, v in dk]


def tables(path=None):
    key = path or TABLES_PATH
    if _T["key"] != key:
        with open(key) as fh:
            _T["tables"] = Tables(json.load(fh))
        _T["key"] = key
    return _T["tables"]


def p_injury(X, sd, gsr):
    """P(the pull is an injury | the state): [1, zl, zt, log tau] (research
    nfl_coach_r3_qb.feats s3's terms 0, 1, 3, 5)."""
    tau = (max(0, gsr) + 30) / 3600.0
    st = math.sqrt(tau)
    zl = math.log1p(max(sd, 0) / st / 10.0)
    zt = math.log1p(max(-sd, 0) / st / 10.0)
    c = X.type_coef
    e = c[0] + c[1] * zl + c[2] * zt + c[3] * math.log(tau)
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, e))))


def _margin_key(sd):
    return "call_lead" if sd >= 9 else "call_trail" if sd <= -9 else "call_close"


class _Track:
    """R3's drive parser run event by event as R1 logs them, deciding the pull
    (and an injured starter's return) at each drive's first identifying snap."""

    def __init__(self, Q, X, prng, pts=(0, 0), pull=True):
        self.Q, self.X, self.prng, self.pull = Q, X, prng, pull
        self.prev = tuple(pts)
        self.drive, self.cur, self.brk, self.last_per = -1, None, True, None
        self.first = {}                 # drive -> (margin, game seconds) at its first snap
        self.ident = set()
        self.side_drives = ([], [])     # identified drives in order, per side
        self.qb = [{"in": "starter", "pull": None, "kind": None, "back": None, "k": None} for _ in (0, 1)]
        self.i = 0

    def feed(self, log):
        while self.i < len(log):
            self._event(log[self.i])
            self.i += 1

    def _event(self, e):
        k = e.get("k")
        if k in E3._BREAK:
            self.brk = True
        if k in E2._SNAPS and "o" in e:
            o, per = e["o"], e["q"]
            if self.brk or o != self.cur or (self.last_per is not None and per != self.last_per
                                             and (self.last_per == 2 or per >= 5)):
                self.drive += 1
                self.cur, self.brk = o, False
                sd = self.prev[o] - self.prev[1 - o]
                self.first[self.drive] = (sd, E3.game_seconds(per, e["c"]))
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
            if self.prng.random() < E3.hazard(self.Q.coef, sd, gsr):
                q["pull"], q["in"] = d, "backup"
                q["kind"] = "injury" if self.prng.random() < p_injury(self.X, sd, gsr) else "strategic"
                if q["kind"] == "injury" and self.prng.random() < self.X.ret_rate:
                    u, acc = self.prng.random(), 0.0
                    q["k"] = self.X.ret_k[-1]
                    for kk, pp in zip(self.X.ret_k, self.X.ret_p):
                        acc += pp
                        if u < acc:
                            q["k"] = kk
                            break
        elif q["in"] == "backup" and q["k"] is not None:
            # the backup's k-th identified drive was the last: this one is the starter's again
            n_bk = sum(1 for x in self.side_drives[o] if x >= q["pull"]) - 1
            if n_bk >= q["k"]:
                q["back"], q["in"] = d, "starter"


def play_game(home, away, rng, prng, players=None, T=None, Q=None, X=None, neutral=False, playoff=False,
              form_sd=None, env_sd=None, start=None, hfa=None, effects=True, pull=True, U=None, deal_rngs=None):
    """R1's game (its own loop, R1's snaps) with the quarterback's substitution
    and its consequences. `prng` draws the pulls, their kind and returns;
    `effects` False keeps the pull but not its consequences (guards); `pull`
    False is no substitution at all. With `players` (home list, away list) and
    `deal_rngs` (one generator a side), the box is dealt (R2's deal, R3's
    quarterback, R4's personnel)."""
    T = T or C.tables()
    Q = Q or E3.tables()
    X = X or tables()
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
    tr = _Track(Q, X, prng, (start or {}).get("pts") or (0, 0), pull=pull)
    while not S["over"]:
        tr.feed(log)
        if effects:
            _apply(sides, base, tr, box, X)
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
           "per": S["per"], "clk": S["clk"], "qb": [dict(q) for q in tr.qb], "log": log}
    if players is not None:
        sn = E3.snaps(log, (start or {}).get("pts") or (0, 0))
        res["sn"] = sn
        U = U or E2.tables()
        drs = deal_rngs or (random.Random(0), random.Random(1))
        res["lines"] = tuple(deal([s for s in sn if s[0] == side], res["h" if side == 0 else "a"],
                                  E2.TeamDeal(players[side], U), drs[side], E3.backup_of(players[side])["id"],
                                  tr.qb[side], X if effects else None, U) for side in (0, 1))
    return res


def _apply(sides, base, tr, box, X):
    """Each side's call, completion and interception shifts and tempo for whoever
    is at quarterback now (the call by the current margin)."""
    for o, s in enumerate(sides):
        b = base[o]
        q = tr.qb[o]
        if q["in"] == "backup":
            ef = X.effects[q["kind"]]
            sd = box[o]["pts"] - box[1 - o]["pts"]
            s.d_call = b[0] + ef[_margin_key(sd)]
            s.d_comp = b[1] + ef["comp"]
            s.d_int = b[2] + ef["int"]
            s.tempo = b[3] * ef["tempo"]
        else:
            s.d_call, s.d_comp, s.d_int, s.tempo = b


def _personnel(p, team, kind_ef, which):
    """R2's deal probabilities with the second unit in: the lead back's carries
    (or the top two targets' targets) by the measured odds ratio."""
    if kind_ef is None:
        return p
    orr = kind_ef["rb1" if which == "car" else "top2"]
    if orr == 1.0:
        return p
    if which == "car":
        fav = {i for i, r in team.d["car"][2].items() if r == "RB1"}
    else:
        tg = team.d["tgt"][0]
        rl = team.d["tgt"][2]
        wrte = sorted((i for i in tg if rl.get(i, "").startswith(("WR", "TE"))), key=lambda i: -tg[i])
        fav = set(wrte[:2])
    ws = {i: x * (orr if i in fav else 1.0) for i, x in p.items()}
    z = sum(ws.values())
    return {i: v / z for i, v in ws.items()} if z > 0 else p


def deal(sn, box, team, rng, backup, q, X, U=None):
    """R3's deal (R2's draws in R2's order) with the quarterback from `q` (the
    pull's drive, the return's drive) and, while a strategic backup is in, R4's
    personnel. `X` None: no personnel effect (then the lines are R3's)."""
    U = U or E2.tables()
    out = {i: E2._zero() for i in team.pl}
    st = team.qb
    for qq in (st, backup):
        if qq is not None and qq not in out:
            out[qq] = E2._zero()
    pull, back = q.get("pull"), q.get("back")
    kind_ef = X.effects[q["kind"]] if (X is not None and q.get("kind")) else None
    rush_w, rec_w = {}, {}
    for o, k, b, yd, td, fum, d, *_ in sn:
        bk = pull is not None and d >= pull and (back is None or d < back)
        qb = backup if bk else st
        ef = kind_ef if bk else None
        if k == "run":
            p = team.p("car", b)
            p = _personnel(p, team, ef, "car") if p else p
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
                p = _personnel(p, team, ef, "tgt")
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


def simulate(home, away, players, n=400, seed=None, T=None, U=None, Q=None, X=None, effects=True, pull=True, **kw):
    """n games, then each dealt as R3 deals a batch: R2's TeamDeal calibrated to
    this batch's own mix of situations, R3's deal generators side by side. The
    game, pull and deal generators are seeded apart, so a game without a
    substitution is R1's and its lines are R3's (guard N4-3)."""
    U = U or E2.tables()
    X = X or tables()
    rng = random.Random(seed)
    prng = random.Random((int(seed or 0) * 6007 + 31337) & 0x7FFFFFFF)
    pts = (kw.get("start") or {}).get("pts") or (0, 0)
    games, logs = [], []
    for _ in range(n):
        r = play_game(home, away, rng, prng, T=T, Q=Q, X=X, effects=effects, pull=pull, **kw)
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
                              bks[side], r["qb"][side], X if effects else None, U))
        out["lines"].append(tuple(lines))
    return out
