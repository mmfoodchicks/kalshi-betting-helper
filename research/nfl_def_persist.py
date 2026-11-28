"""Defensive coaching, step 1: which tendencies persist (research/reports/nfl_def_persist.md).
RESEARCH, development only. The rule is research/data/nfl_coach/def_rule.json,
committed alone (70278a5) before any number here was computed; this file implements
it and nothing else.

Data: FTN charting via nflverse 2022-2025 ("FTN Data via nflverse", CC BY-SA 4.0 --
anything derived from it and published is share-alike) and nflverse play-by-play
2016 to 2026 week 12 (CC BY 4.0) through R2's sealed loader; raw rows only for the
timeouts, under R1's seal.

    python3 -m research.nfl_def_persist <pbp dir> <ftn dir>

Writes research/data/nfl_coach/def_persist.json. Builds nothing into R1-R6.
"""
import csv
import gzip
import json
import math
import os
import random
import sys
import zlib
from collections import Counter, defaultdict

import nfl_pbp as P
from research import nfl_coach_data as CD
from research import nfl_coach_evo as EVO
from research import nfl_coach_r2_data as R

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "nfl_coach", "def_persist.json")
RULE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "nfl_coach", "def_rule.json")
PBP_SEASONS = tuple(range(2016, 2027))
FTN_SEASONS = (2022, 2023, 2024, 2025)
TENDENCIES = ("blitz", "rushers", "box", "prevent", "redzone", "halftime", "adaptation", "timeouts")
PERMS = 2000
GAP_K = 10.0
_DROP = (R.K_PASS, R.K_SACK, R.K_SCR)


def _i(v, d=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return d


def load_ftn(d, season):
    out = {}
    with open(os.path.join(d, f"ftn_charting_{season}.csv"), newline="") as fh:
        for r in csv.DictReader(fh):
            out[(r["nflverse_game_id"], _i(r["nflverse_play_id"]))] = (
                _i(r["n_defense_box"]), _i(r["n_offense_backfield"]), r["qb_location"] or "?",
                _i(r["n_blitzers"]), _i(r["n_pass_rushers"]))
    return out


def load_timeouts(pbp, season):
    """[(game, defteam, half, defence score state, is a defensive timeout)] for the last 2:00 of
    each half: every scrimmage snap with a defensive timeout left (an opportunity) and every
    defensive timeout row (R1's seal on every row)."""
    out = []
    last = {}          # game -> (posteam, defteam, score differential) of its latest row that names them
    with gzip.open(CD.path(pbp, season), "rt", newline="") as fh:
        rd = csv.reader(fh)
        ix = {h: j for j, h in enumerate(next(rd))}
        for raw in rd:
            if CD.sealed(raw[ix["season"]], raw[ix["week"]], raw[ix["season_type"]]):
                continue
            if raw[ix["season_type"]] != "REG":
                continue
            gid = raw[ix["game_id"]]
            if raw[ix["posteam"]] and raw[ix["defteam"]]:
                last[gid] = (raw[ix["posteam"]], raw[ix["defteam"]], _i(raw[ix["score_differential"]]))
            hsr = _i(raw[ix["half_seconds_remaining"]], 9999)
            q = _i(raw[ix["qtr"]])
            if hsr > 120 or q > 4 or gid not in last:
                continue
            # a timeout row names no posteam or defteam (nflverse leaves both empty): the possession is the
            # latest row's that did -- measured, the first run read no timeouts at all and every value was 0
            po, de, sdiff = last[gid]
            ss = P.score_state(-sdiff)
            half = 1 if q <= 2 else 2
            if raw[ix["timeout"]] in ("1", "1.0") and raw[ix["timeout_team"]] == de:
                out.append((raw[ix["game_id"]], de, half, ss, 1))
            elif raw[ix["play_type"]] in ("run", "pass") and _i(raw[ix["defteam_timeouts_remaining"]]) >= 1:
                out.append((raw[ix["game_id"]], de, half, ss, 0))
    return out


class Acc:
    """Play-level outcomes against the same season's league cell mean, summed per
    defence team-game as (sum of residuals, count)."""

    def __init__(self):
        self.rows = []

    def add(self, s, cell, y, key):
        self.rows.append((s, cell, y, key))

    def team_games(self):
        m = defaultdict(lambda: [0.0, 0])
        for s, c, y, _k in self.rows:
            m[(s, c)][0] += y
            m[(s, c)][1] += 1
        out = defaultdict(lambda: [0.0, 0.0])
        for s, c, y, k in self.rows:
            a = m[(s, c)]
            out[k][0] += y - a[0] / a[1]
            out[k][1] += 1
        return dict(out)


def _def(g, p):
    """(defence team, its coach) for an offensive play."""
    if p[R.Q_OFFH] == 1:
        return g["away"], g["away_coach"]
    return g["home"], g["home_coach"]


def collect(pbp, ftn_dir):
    acc = {t: Acc() for t in ("blitz", "rushers", "box", "prevent", "redzone")}
    half, half_pl, adapt, adapt_pl, coach = {}, {}, defaultdict(lambda: [0.0, 0.0]), defaultdict(
        lambda: [0.0, 0.0]), {}
    box_rows = []      # (s, cell, box, key, gid, offence side, play index) for the adaptation
    lg_succ = {}
    for s in PBP_SEASONS:
        sd_ = R.load_season(pbp, s)
        ftn = load_ftn(ftn_dir, s) if s in FTN_SEASONS else {}
        rs = [0, 0]
        ps = [0, 0]
        games = [g for g in sd_["games"] if g["st"] == 2]
        for g in games:
            for p in g["plays"]:
                if p[R.Q_KIND] == R.K_RUN:
                    rs[0] += p[R.Q_SUCC]
                    rs[1] += 1
                elif p[R.Q_KIND] in _DROP:
                    ps[0] += p[R.Q_SUCC]
                    ps[1] += 1
        lg_succ[s] = (rs[0] / max(1, rs[1]), ps[0] / max(1, ps[1]))
        for g in games:
            for side in (0, 1):
                pl = [p for p in g["plays"] if p[R.Q_OFFH] == (1 if side == 0 else 0) and p[R.Q_KIND] in R.SCRIMMAGE]
                if not pl:
                    continue
                dteam, dcoach = _def(g, pl[0])
                key = (s, dteam, g["week"])
                coach[key] = dcoach
                # red zone, by drive
                drives = defaultdict(lambda: [False, False])
                for p in pl:
                    dv = drives[p[R.Q_DRIVE]]
                    dv[0] |= p[R.Q_YL] <= 20
                    dv[1] |= p[R.Q_TD] == 1
                for reached, td in drives.values():
                    if reached:
                        acc["redzone"].add(s, 0, 1.0 if td else 0.0, key)
                # halftime and its Q1 -> Q2 placebo, EPA a play allowed
                by_q = defaultdict(list)
                for p in pl:
                    if p[R.Q_PER] <= 4:
                        by_q[p[R.Q_PER]].append(p[R.Q_EPA] / 1000.0)
                h1, h2 = by_q[1] + by_q[2], by_q[3] + by_q[4]
                if len(h1) >= 15 and len(h2) >= 15:
                    half[key] = sum(h2) / len(h2) - sum(h1) / len(h1)
                if len(by_q[1]) >= 8 and len(by_q[2]) >= 8:
                    half_pl[key] = sum(by_q[2]) / len(by_q[2]) - sum(by_q[1]) / len(by_q[1])
                if not ftn:
                    continue
                for j, p in enumerate(pl):
                    f = ftn.get((g["id"], p[R.Q_PLAYID]))
                    if f is None:
                        continue
                    box, bf, ql, bl, ru = f
                    dcell = (p[R.Q_DOWN], EVO._dist(p[R.Q_DIST]), P.score_state(p[R.Q_SD]), EVO._zone(p[R.Q_YL]))
                    if p[R.Q_KIND] in _DROP and ru >= 1:
                        acc["blitz"].add(s, dcell, 1.0 if bl >= 1 else 0.0, key)
                        acc["rushers"].add(s, dcell, float(ru), key)
                        if p[R.Q_PER] == 4 and p[R.Q_GSR] <= 240 and p[R.Q_SD] <= -9:
                            acc["prevent"].add(s, 0, 1.0 if ru <= 3 else 0.0, key)
                    if box >= 1:
                        bcell = (p[R.Q_DOWN], EVO._dist(p[R.Q_DIST]), EVO._zone(p[R.Q_YL]), min(bf, 3), ql)
                        acc["box"].add(s, bcell, float(box), key)
                        box_rows.append((s, bcell, float(box), key, g["id"], side, j))
    return acc, half, half_pl, box_rows, lg_succ, coach


def adaptation(pbp, box_rows, lg_succ):
    """League coefficient of the box residual on the offence's in-game success gap,
    with the order-shuffled placebo, and per team-game (sum xe, sum xx) for the slopes."""
    m = defaultdict(lambda: [0.0, 0])
    for s, c, y, *_ in box_rows:
        m[(s, c)][0] += y
        m[(s, c)][1] += 1
    resid = {(gid, side, j): (y - m[(s, c)][0] / m[(s, c)][1], key)
             for s, c, y, key, gid, side, j in box_rows}
    out = {}
    for shuffled in (False, True):
        num = den = 0.0
        per_game = defaultdict(lambda: [0.0, 0.0])
        team = defaultdict(lambda: [0.0, 0.0])
        rows_x = []
        for s in FTN_SEASONS:
            sd_ = R.load_season(pbp, s)
            r_lg, p_lg = lg_succ[s]
            for g in sd_["games"]:
                if g["st"] != 2:
                    continue
                for side in (0, 1):
                    pl = [p for p in g["plays"] if p[R.Q_OFFH] == (1 if side == 0 else 0)
                          and p[R.Q_KIND] in R.SCRIMMAGE]
                    hist = [(p[R.Q_KIND] == R.K_RUN, p[R.Q_SUCC]) for p in pl]
                    if shuffled:
                        random.Random(zlib.crc32(f"{g['id']}{side}".encode())).shuffle(hist)
                    rs, ps = [0, 0], [0, 0]
                    for j, (isrun, succ) in enumerate(hist):
                        if j >= 10 and (g["id"], side, j) in resid:
                            x = ((rs[0] + GAP_K * r_lg) / (rs[1] + GAP_K) - (ps[0] + GAP_K * p_lg) / (ps[1] + GAP_K))
                            e, key = resid[(g["id"], side, j)]
                            rows_x.append((g["id"], x, e))
                            team[key][0] += x * e
                            team[key][1] += x * x
                        a = rs if isrun else ps
                        a[0] += succ
                        a[1] += 1
        for gid, x, e in rows_x:
            num += x * e
            den += x * x
        b = num / den
        for gid, x, e in rows_x:
            per_game[gid][0] += x * (e - b * x)
        se = math.sqrt(sum(v[0] ** 2 for v in per_game.values())) / den
        out["placebo" if shuffled else "real"] = {"coef": round(b, 4), "se": round(se, 4), "z": round(b / se, 2),
                                                 "plays": len(rows_x)}
        if not shuffled:
            out["team_games"] = {k: tuple(v) for k, v in team.items()}
    return out


def timeouts_team_games(pbp, game_key):
    """Per defence team-game: (defensive timeouts minus the league's expected count, opportunities),
    the expectation per opportunity the season's league rate in its half x score-state cell."""
    out = {}
    for s in PBP_SEASONS:
        tos = load_timeouts(pbp, s)
        k_cell, n_cell = Counter(), Counter()
        for _g, _d, h, ss, is_to in tos:
            (k_cell if is_to else n_cell)[(h, ss)] += 1
        per = defaultdict(lambda: [0.0, 0.0])
        for gid, de, h, ss, is_to in tos:
            key = game_key.get((gid, de))
            if key is None:
                continue
            if is_to:
                per[key][0] += 1.0
            else:
                per[key][0] -= k_cell[(h, ss)] / max(1, n_cell[(h, ss)])
                per[key][1] += 1.0
        for k, v in per.items():
            if v[1] > 0:
                out[k] = (v[0], v[1])
    return out


# ---- persistence --------------------------------------------------------------------------------
def season_values(tg):
    """{(season, team): (sum num, sum den)} and the per-game lists in week order."""
    by = defaultdict(list)
    for (s, t, w), v in tg.items():
        by[(s, t)].append((w, v))
    return {k: sorted(v) for k, v in by.items()}


def _r(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy) if sx and sy else 0.0


def _perm_hits(pairs, r, rng):
    """How many of PERMS within-season shuffles of the second member reach r."""
    groups = defaultdict(list)
    for s_, a, b in pairs:
        groups[s_].append((a, b))
    xs = [a for s_ in sorted(groups) for a, _b in groups[s_]]
    cols = [[b for _a, b in groups[s_]] for s_ in sorted(groups)]
    hits = 0
    for _ in range(PERMS):
        ys = []
        for c in cols:
            c = list(c)
            rng.shuffle(c)
            ys += c
        hits += _r(xs, ys) >= r
    return hits


def _ratio(v):
    num = sum(a for _w, (a, b) in v)
    den = sum(b for _w, (a, b) in v)
    return num / den if den else None


def split_half(tg, rng):
    by = season_values(tg)
    pairs = []
    for (s, t), v in by.items():
        a, b = _ratio(v[0::2]), _ratio(v[1::2])
        if a is not None and b is not None:
            pairs.append((s, a, b))
    r = _r([p[1] for p in pairs], [p[2] for p in pairs])
    hits = _perm_hits(pairs, r, rng)
    return {"team_seasons": len(pairs), "r": round(r, 4), "spearman_brown": round(2 * r / (1 + r), 4) if r > -1 else None,
            "p_one_sided": round((1 + hits) / (1 + PERMS), 5)}


def year_over_year(tg, coach, rng):
    by = season_values(tg)
    val = {k: _ratio(v) for k, v in by.items()}
    hc = {}
    for (s, t, w), c in coach.items():
        hc.setdefault((s, t), Counter())[c] += 1
    hc = {k: c.most_common(1)[0][0] for k, c in hc.items()}
    out = {}
    for nm, keep in (("all", lambda a, b: True), ("same_head_coach", lambda a, b: a == b),
                     ("head_coach_changed", lambda a, b: a != b)):
        pr = [(s, val[(s, t)], val[(s + 1, t)]) for (s, t) in val if (s + 1, t) in val
              and val[(s, t)] is not None and val[(s + 1, t)] is not None and keep(hc.get((s, t)), hc.get((s + 1, t)))]
        if len(pr) < 8:
            out[nm] = {"pairs": len(pr)}
            continue
        r = _r([p[1] for p in pr], [p[2] for p in pr])
        hits = _perm_hits(pr, r, rng)
        out[nm] = {"pairs": len(pr), "r": round(r, 4), "p_one_sided": round((1 + hits) / (1 + PERMS), 5)}
    return out


def holm(ps, alpha=0.05):
    order = sorted(ps, key=ps.get)
    out, stop = {}, False
    for i, k in enumerate(order):
        ok = (not stop) and ps[k] <= alpha / (len(order) - i)
        stop |= not ok
        out[k] = ok
    return out


def main(pbp, ftn_dir):
    import hashlib
    rng = random.Random(20261127)
    acc, half, half_pl, box_rows, lg_succ, coach = collect(pbp, ftn_dir)
    tg = {t: acc[t].team_games() for t in acc}
    tg["halftime"] = {k: (v, 1.0) for k, v in half.items()}
    hp = {k: (v, 1.0) for k, v in half_pl.items()}
    ad = adaptation(pbp, box_rows, lg_succ)
    tg["adaptation"] = ad.pop("team_games")
    game_key = {}
    for s in PBP_SEASONS:
        for g in R.load_season(pbp, s)["games"]:
            if g["st"] == 2:
                game_key[(g["id"], g["home"])] = (s, g["home"], g["week"])
                game_key[(g["id"], g["away"])] = (s, g["away"], g["week"])
    tg["timeouts"] = timeouts_team_games(pbp, game_key)
    res = {"rule_sha256": hashlib.sha256(open(RULE, "rb").read()).hexdigest(), "tendencies": {},
           "credit": "FTN Data via nflverse (CC BY-SA 4.0) and nflverse-data play-by-play (CC BY 4.0)",
           "adaptation_league": ad}
    ps = {}
    for t in TENDENCIES:
        sh = split_half(tg[t], rng)
        yy = year_over_year(tg[t], coach, rng)
        res["tendencies"][t] = {"split_half": sh, "year_over_year": yy,
                                "team_games": len(tg[t])}
        ps[t] = sh["p_one_sided"]
    res["halftime_placebo_q1_q2"] = split_half(hp, rng)
    hm = holm(ps)
    for t in TENDENCIES:
        d = res["tendencies"][t]
        d["holm"] = hm[t]
        yo = d["year_over_year"]["all"]
        d["persists"] = bool(hm[t] and yo.get("p_one_sided", 1) < 0.05 and
                             (t != "halftime" or d["split_half"]["r"] > res["halftime_placebo_q1_q2"]["r"]))
    ar = ad["real"]
    res["adaptation_real"] = ar["z"] > 3 and abs(ad["placebo"]["z"]) < 2
    res["persisting"] = [t for t in TENDENCIES if res["tendencies"][t]["persists"]]
    with open(OUT, "w") as fh:
        json.dump(res, fh, indent=1, sort_keys=True)
    return res


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1], sys.argv[2]), indent=1)[:6000])
