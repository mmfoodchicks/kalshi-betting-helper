"""The coaching engine's third revision (coach-r3.0): the quarterback is
substituted the way real staffs substitute him. EXPERIMENTAL and RESEARCH ONLY
-- no board, viewer, DFS world or predlog bucket reads it, and it never writes
one (research/reports/nfl_coach_r3.md).

R1 (`nfl_coach_sim`) plays the game, and R2 (`nfl_coach_sim_r2`) says who gets
each carry and target. Neither is edited, and the game is R1's draw for draw.
R3 adds who is AT QUARTERBACK:

  * the starter can be pulled at the first snap of any drive holding a
    quarterback-identifying snap (a pass, a sack, a spike, a kneel). The
    chance is a hazard LEARNED from nflverse 2016-2024 (research/nfl_coach_r3_qb.py
    -> research/data/nfl_coach_r3_tables.json): a logistic in the margin
    against the time left, never a score threshold. The lead and the deficit
    are separate, each through log(1 + margin / sqrt(time) / 10), with their
    squares and their products with log(time). The form was chosen on 2024
    from seven, the seasons weighted with a half-life of two (pulls rose from
    ~69 a season in 2016-19 to ~100 in 2023-25). The same hazard carries the
    injury rate in a close game (0.3-0.5% a drive);
  * a pulled starter STAYS OUT. Measured: after a pull at a margin the game
    has decided, the starter returned 6 times in 410 (1.5%). After other pulls
    (mostly injuries) he returned 26% of the time, which the engine does not
    play: his line is cut at the pull;
  * from the pull on, every dropback, sack, spike, kneel and quarterback carry
    is the backup's. Carriers and targets are R2's draws, made in R2's order on
    the same generator, so R3 differs from R2 by the quarterback alone. A
    separate generator draws the pulls;
  * what the audit did not support is not here: a head coach's own habit
    (shrunk coach multipliers lost out of sample at every K), a different rate
    when the starter is already a backup (no gain), a persistent pull for
    backs (after a backup's first carry with a 17+ lead the lead back still
    takes 41% of the rest, against 43% of that state overall: rotation, which
    R2 already deals). Play selection with a backup in is measured but not
    modelled: R1 makes the call.

Conservation (guards NQ, tests/guards_auto_nfl_coach_r3.py): both quarterbacks'
attempts, completions, passing yards (each the sum of his own completions'
yards), touchdowns, interceptions, sacks and kneels add to the team's box. Every
carry, catch, yard and touchdown adds up exactly as in R2. With the hazard at 0,
every line is R2's to the yard.
"""
import json
import math
import os
import random

import nfl_coach_sim as C
import nfl_coach_sim_r2 as E2

VERSION = "coach-r3.0"
TABLES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "research", "data", "nfl_coach_r3_tables.json")
SPEC = "s6"
_IDENT = ("cmp", "inc", "int", "sack", "spike", "kneel")
# events after which the next snap starts a new drive even by the same side
# (a kickoff, a punt, a field goal try, a try after a score, a free kick)
_BREAK = ("ko", "punt", "fg", "try", "rtd", "fk")
BACKUP_PLACEHOLDER = {"id": "QB2*", "pos": "QB", "car": 0.0, "tgt": 0.0, "ypc": 3.5, "ypr": 0, "cr": 0}
_T = {"tables": None, "key": None}


def features(sd, gsr):
    """The hazard's features at a drive's first snap (`sd` the offence's margin,
    `gsr` the game's seconds left; overtime: the period's, at most 600).
    research.nfl_coach_r3_qb.feats(sd, gsr, *, "s6") is the same function
    (guard NQ1)."""
    tau = (max(0, gsr) + 30) / 3600.0
    st = math.sqrt(tau)
    zl = math.log1p(max(sd, 0) / st / 10.0)
    zt = math.log1p(max(-sd, 0) / st / 10.0)
    lt = math.log(tau)
    return [1.0, zl, zl * zl, zt, zt * zt, lt, zl * lt, zt * lt, zl * zl * lt, zt * zt * lt]


def hazard(coef, sd, gsr):
    e = sum(c * v for c, v in zip(coef, features(sd, gsr)))
    e = max(-30.0, min(30.0, e))
    return 1.0 / (1.0 + math.exp(-e))


def game_seconds(per, clk):
    return (4 - per) * 900 + clk if per <= 4 else min(clk, 600)


class Tables:
    def __init__(self, raw):
        self.raw = raw
        self.coef = list(raw["coef"])
        self.spec = raw["spec"]


def tables(path=None):
    key = path or TABLES_PATH
    if _T["key"] != key:
        with open(key) as fh:
            _T["tables"] = Tables(json.load(fh))
        _T["key"] = key
    return _T["tables"]


def snaps(log, pts=(0, 0)):
    """R2's snaps with their drive: [(side, kind, bucket, yards, td, fumble,
    drive, period, clock, margin before)], the score read before each snap.
    `pts` is the score the game started from (a `start` state's; R2's own
    parser assumes 0-0, which read a 24-point lead at 2:00 as a tie)."""
    out = []
    prev = tuple(pts)
    drive, cur, brk, last_per = -1, None, True, None
    for e in log:
        k = e.get("k")
        if k in _BREAK:
            brk = True
        if k in E2._SNAPS and "o" in e:
            o = e["o"]
            per = e["q"]
            if brk or o != cur or (last_per is not None and per != last_per and (last_per == 2 or per >= 5)):
                drive += 1
                cur, brk = o, False
            last_per = per
            sd = prev[o] - prev[1 - o]
            b = E2.bucket(per, E2.half_seconds(per, e["c"]), sd, e.get("d", 1), e.get("t", 10), e.get("y", 50))
            out.append((o, k, b, int(e.get("yd", 0)), int(e.get("td", 0)), 1 if e.get("to") == "fum" else 0,
                        drive, per, e["c"], sd))
        if "s" in e:
            prev = e["s"]
    return out


def pull_drive(sn, coef, rng):
    """The drive at which the starter is pulled (None: never), drawing the
    hazard at the first snap of each identified drive in order. `coef` "off":
    never (the guards' R2-equality check)."""
    if coef == "off":
        return None
    first = {}
    ident = set()
    for s in sn:
        d = s[6]
        if d not in first:
            first[d] = s
        if s[1] in _IDENT:
            ident.add(d)
    for d in sorted(ident):
        s = first[d]
        if rng.random() < hazard(coef, s[9], game_seconds(s[7], s[8])):
            return d
    return None


def deal(sn, box, team, rng, backup, pulled_at, U=None):
    """One side's box dealt to its players, R2's deal snap for snap (the same
    draws in the same order on `rng`) with the quarterback switched to
    `backup` from drive `pulled_at` on."""
    U = U or E2.tables()
    out = {i: E2._zero() for i in team.pl}
    st = team.qb
    for q in (st, backup):
        if q is not None and q not in out:
            out[q] = E2._zero()
    rush_w, rec_w = {}, {}
    for o, k, b, yd, td, fum, d, *_ in sn:
        qb = backup if (pulled_at is not None and d >= pulled_at) else st
        if k == "run":
            p = team.p("car", b)
            i = E2._draw(rng, p) if p else st
            if i == st:
                i = qb                      # the quarterback's carry is whoever is at quarterback
            ln = out[i]
            ln["car"] += 1
            ln["car_b"][b] += 1
            ln["rush_td"] += td
            ln["fum"] += fum
            ypc = team.pl[i].get("ypc") if i in team.pl else BACKUP_PLACEHOLDER["ypc"]
            rush_w[i] = rush_w.get(i, 0.0) + max(0.5, float(ypc or 4.2))
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
                q["pass_yd"] += yd
            if k == "cmp" or (k == "inc" and rng.random() < U.p_tgt_inc) or (
                    k == "int" and rng.random() < U.p_tgt_int):
                p = team.p("tgt", b)
                p = E2._reweight(p, team, k == "cmp")
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
    # the one fumble R1's log can leave unnamed (a safety's) is the starter's, as in R2
    named = sum(v["fum"] for v in out.values())
    if box.get("fum_lost", named) != named:
        out[st]["fum"] += box["fum_lost"] - named
    for i, v in E2._split(box["rush_yds"], rush_w).items():
        out[i]["rush_yd"] += v
    for i, v in E2._split(box["pass_yds"], rec_w).items():
        out[i]["rec_yd"] += v
    return out


def backup_of(players):
    """The second quarterback on the list, else a placeholder (league backup)."""
    qbs = [p for p in players if E2.grp(p.get("pos")) == "QB"]
    return qbs[1] if len(qbs) > 1 else dict(BACKUP_PLACEHOLDER)


def simulate(home, away, players, n=400, seed=None, T=None, U=None, Q=None, coef=None, **kw):
    """n games of the matchup, each dealt by R2 and by R3 on the SAME game and
    the SAME deal draws (R3 adds the pull, on its own generator). Returns
    {"games", "r2": [(home lines, away lines)], "r3": [...], "pulled": [(home
    drive or None, away)]}. `coef` overrides the fitted hazard (the guards set
    it to switch the pull off)."""
    U = U or E2.tables()
    Q = Q or tables()
    coef = Q.coef if coef is None else coef
    rng = random.Random(seed)
    games, logs = [], []
    for _ in range(n):
        log = []
        res = C.play_game(home, away, rng, T, log=log, **kw)
        games.append(res)
        logs.append(snaps(log, (kw.get("start") or {}).get("pts") or (0, 0)))
    mixes = E2.mix_of([[s[:6] for s in sn] for sn in logs])
    pls = [list(players[0]), list(players[1])]
    backups = [backup_of(pls[0]), backup_of(pls[1])]
    teams = [E2.TeamDeal(pls[s], U, mix=mixes[s], situational=True) for s in (0, 1)]
    out = {"games": games, "r2": [], "r3": [], "pulled": []}
    prng = random.Random((seed or 0) * 6007 + 31337)
    for res, sn in zip(games, logs):
        pd = []
        for side in (0, 1):
            s_sn = [s for s in sn if s[0] == side]
            pd.append(pull_drive(s_sn, coef, prng))
        out["pulled"].append(tuple(pd))
        for arm in ("r2", "r3"):
            # both arms draw carriers and targets from one generator seeded per game,
            # so they are the same draws; R2's arm is R3's with no pull
            lines = []
            for side in (0, 1):
                s_sn = [s for s in sn if s[0] == side]
                drng = random.Random((int(seed or 0) * 1000003 + 2 * len(out["r2"]) + side) & 0x7FFFFFFF)
                bk = backups[side]["id"]
                at = pd[side] if arm == "r3" else None
                lines.append(deal(s_sn, res["h" if side == 0 else "a"], teams[side], drng, bk, at, U))
            out[arm].append(tuple(lines))
    return out
