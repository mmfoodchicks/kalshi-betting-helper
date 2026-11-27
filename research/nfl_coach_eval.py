"""The measurements R1 is judged on, for real games and for both engines, and
the development table (research/reports/nfl_coach_r1.md section 4). RESEARCH.

  python3 -m research.nfl_coach_eval <nflverse dir> [--seasons 2024,2025] [--sims 60]
          [--out research/data/nfl_coach/dev_r1.json]

Both arms play the SAME matchups: each real game's two teams are the league
profile with an offensive level set so the arm's own expected points equal the
closing line's implied points ((total +/- spread) / 2), on a neutral field
(the home edge is inside the spread). The level curve is measured per arm by
simulation, so neither arm is handed the other's scale.

  control    nfl_deep_sim.play_game on this engine's `base` tables
  candidate  nfl_coach_sim.play_game

The registered read (research/nfl_coach_r1_read.py) imports these functions;
its registration pins this file's sha256. The development run refuses nothing
because nfl_coach_data never hands it a sealed game.
"""
import json
import math
import os
import random
import sys
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nfl_coach_sim as C                     # noqa: E402
import nfl_deep_sim as D                      # noqa: E402
import nfl_pbp as P                           # noqa: E402
from research import nfl_coach_data as CD     # noqa: E402

OUT_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "nfl_coach", "dev_r1.json")
SNAPS = ("run", "cmp", "inc", "sack", "int")
_REAL_SNAP = (P.RUSH, P.PASS_COMP, P.PASS_INC, P.SACK, P.INT)
LEVELS = [0.70 + 0.05 * i for i in range(15)]          # 0.70 .. 1.40
CHECKPOINTS = (900, 300, 120)                           # fourth-quarter clock: start, 5:00, 2:00
P_CLIP = 0.002
FIGS = ("q2_snaps", "q2_pts", "q4_snaps", "q4_pts")     # the four inside-2:00 figures
KEYS = FIGS + ("kneels_q2", "kneels_q4", "spikes", "timeouts", "total", "ot",
               "run_n", "run_s", "dto_n53", "dto_k53", "dto_n32", "dto_k32", "dto_n21", "dto_k21")


def era_of(season):
    return "2016-2023" if season <= 2023 else "2024" if season == 2024 else "2025+"


# ---- one game's figures, real and simulated -------------------------------------
def real_figures(g):
    """The KEYS of one real regular-season game (regulation only where a half
    is named)."""
    f = dict.fromkeys(KEYS, 0)
    rows = g["rows"]
    n = len(rows)
    first = {2: None, 4: None}          # the score before the first row at or inside 2:00
    end = {2: None, 4: None}
    last = (0, 0)
    for i, p in enumerate(rows):
        per, clk, code = p[CD.R_PER], p[CD.R_CLK], p[CD.R_CODE]
        sc = p[CD.R_SH] + p[CD.R_SA]
        if per == 3 and end[2] is None:
            end[2] = sc
        if per >= 5 and end[4] is None:
            end[4] = sc
        last = sc
        if code == P.TIMEOUT:
            if p[CD.R_TOTEAM] >= 0 and per <= 4:
                f["timeouts"] += 1
            continue
        if per in (2, 4) and clk <= 120 and first[per] is None and code != P.END_PERIOD:
            first[per] = sc
        if code == P.KNEEL and per in (2, 4):
            f["kneels_q%d" % per] += 1
        elif code == P.SPIKE:
            f["spikes"] += 1
        if code not in _REAL_SNAP:
            continue
        if per in (2, 4) and clk <= 120:
            f["q%d_snaps" % per] += 1
        if per != 4 or not 60 < clk <= 300:
            continue
        fl = p[CD.R_FLAGS]
        if (code not in (P.RUSH, P.PASS_COMP, P.SACK) or fl & (P.F_OOB | P.F_TD | P.F_TURNOVER)
                or not 1 <= p[CD.R_SD] <= 8):
            continue
        j = i + 1
        dto = oto = 0
        while j < n and rows[j][CD.R_CODE] in (P.TIMEOUT, P.TWO_MIN):
            if rows[j][CD.R_TOTEAM] == 1 - p[CD.R_OFF]:
                dto += 1
            elif rows[j][CD.R_TOTEAM] == p[CD.R_OFF]:
                oto += 1
            j += 1
        if j >= n or rows[j][CD.R_PER] != 4 or rows[j][CD.R_OFF] != p[CD.R_OFF]:
            continue
        have = p[CD.R_TOA] if p[CD.R_OFF] == 0 else p[CD.R_TOH]
        _tail(f, clk, clk - rows[j][CD.R_CLK], have, dto, oto, rows[j][CD.R_CODE] in _REAL_SNAP)
    f["total"] = g["home_pts"] + g["away_pts"]
    f["ot"] = int(g["ot"])
    reg = end[4] if end[4] is not None else (g["home_pts"] + g["away_pts"] if not g["ot"] else last)
    for per, e in ((2, end[2]), (4, reg)):
        if first[per] is not None and e is not None:
            f["q%d_pts" % per] = e - first[per]
    return f


def _tail(f, clk, dt, have, dto, oto, snap_next):
    """The leader's runoff (5:00-3:00, no timeout between) and the trailing
    defence's timeout after an in-bounds play, fourth quarter, lead 1-8."""
    if clk > 180 and not dto and not oto and snap_next and 0 <= dt <= 60:
        f["run_n"] += 1
        f["run_s"] += dt
    if have > 0:
        b = "53" if clk > 180 else "32" if clk > 120 else "21"
        f["dto_n" + b] += 1
        f["dto_k" + b] += 1 if dto else 0


def sim_figures(log, res, coach):
    """The same KEYS from an engine's passive log. The control has no timeouts:
    its timeout fields stay 0 and its runoff is every in-bounds gap."""
    f = dict.fromkeys(KEYS, 0)
    before = {2: 0, 4: 0}
    last = {2: 0, 4: 0}
    to = [3, 3]
    n = len(log)
    half = 1
    for k, e in enumerate(log):
        kind, q, c = e["k"], e["q"], e["c"]
        if q >= 5:
            break
        if (q > 2) != (half == 2):
            half, to = 2, [3, 3]
        if q in (2, 4):
            if c > 120:
                before[q] = sum(e["s"])
            last[q] = sum(e["s"])
        if kind == "tmo":
            to[e["o"]] -= 1
            f["timeouts"] += 1
            continue
        if kind == "kneel" and q in (2, 4):
            f["kneels_q%d" % q] += 1
        elif kind == "spike":
            f["spikes"] += 1
        if kind not in SNAPS:
            continue
        if q in (2, 4) and c <= 120:
            f["q%d_snaps" % q] += 1
        if q != 4 or not 60 < c <= 300:
            continue
        o = e["o"]
        s0 = log[k - 1]["s"] if k else (0, 0)
        if (kind not in ("run", "cmp", "sack") or e.get("ob") or e.get("td") or e.get("to") or e.get("sf")
                or not 1 <= s0[o] - s0[1 - o] <= 8):
            continue
        j = k + 1
        dto = oto = 0
        while j < n and log[j]["k"] in ("tmo", "2mw"):
            if log[j]["k"] == "tmo":
                if log[j]["o"] == o:
                    oto += 1
                else:
                    dto += 1
            j += 1
        if j >= n or log[j]["q"] != 4 or log[j].get("o") != o or log[j]["k"] in ("ko", "end", "fin"):
            continue
        # `to` already reflects timeouts logged before this play only
        _tail(f, c, c - log[j]["c"], to[1 - o] if coach else 0, dto, oto, log[j]["k"] in SNAPS)
    for q in (2, 4):
        f["q%d_pts" % q] = last[q] - before[q]
    f["total"] = res["h"]["pts"] + res["a"]["pts"]
    f["ot"] = int(res["ot"])
    return f


# ---- the level anchor -----------------------------------------------------------
def _play(arm, T, home, away, rng, log=None, start=None, form_sd=None, playoff=False):
    if arm == "coach":
        return C.play_game(home, away, rng, T, neutral=True, log=log, start=start, form_sd=form_sd,
                           playoff=playoff)
    if start is None:
        return D.play_game(home, away, rng, T.base, neutral=True, log=log, form_sd=form_sd, playoff=playoff)
    return C.control_from(home, away, rng, T, start=start, neutral=True, log=log, form_sd=form_sd,
                          playoff=playoff)


def _lvl_profile(T, lvl):
    p = D.league_profile(T.base)
    p["level_off"] = lvl
    return p


def level_curve(arm, T, n=2500, seed=11):
    """[(level, expected points against a league-average defence)], this arm."""
    lg = D.league_profile(T.base)
    out = []
    for i, lv in enumerate(LEVELS):
        rng = random.Random(seed * 1000 + i)
        tot = 0
        me = _lvl_profile(T, lv)
        for _ in range(n):
            tot += _play(arm, T, me, lg, rng, form_sd=0.0)["h"]["pts"]
        out.append((lv, tot / n))
    return out


def level_for(curve, pts):
    """The level whose expected points are `pts` (linear between grid points,
    held at the ends)."""
    if pts <= curve[0][1]:
        return curve[0][0]
    for (l0, p0), (l1, p1) in zip(curve, curve[1:]):
        if pts <= p1:
            return l0 + (l1 - l0) * (pts - p0) / (p1 - p0) if p1 > p0 else l0
    return curve[-1][0]


def implied(g):
    """(home, away) points the closing line implies; None without a line."""
    if g.get("spread") is None or g.get("total_line") is None:
        return None
    return (g["total_line"] + g["spread"]) / 2.0, (g["total_line"] - g["spread"]) / 2.0


def checkpoint_states(g):
    """{clock: start state} at the first scrimmage snap at or inside each
    fourth-quarter checkpoint of a real game."""
    out = {}
    for p in g["rows"]:
        if p[CD.R_PER] != 4 or p[CD.R_CODE] not in _REAL_SNAP + (P.KNEEL, P.SPIKE) or not 1 <= p[CD.R_DOWN] <= 4:
            continue
        for cp in CHECKPOINTS:
            if cp not in out and p[CD.R_CLK] <= cp:
                out[cp] = {"per": 4, "clk": p[CD.R_CLK], "off": p[CD.R_OFF], "down": p[CD.R_DOWN],
                           "dist": max(1, p[CD.R_DIST]), "ytg": max(1, min(99, p[CD.R_YTG])),
                           "pts": (p[CD.R_SH], p[CD.R_SA]), "to": (p[CD.R_TOH], p[CD.R_TOA])}
    return out


# ---- one real game through both arms --------------------------------------------
_W = {}


def _init(tables_path, curves, form, sims, playouts):
    with open(tables_path) as fh:
        raw = json.load(fh)
    _W.update(raw=raw, T={}, curves=curves, form=form, sims=sims, playouts=playouts)


def _tables(era):
    if era not in _W["T"]:
        _W["T"][era] = C.Tables(_W["raw"], era=era)
    return _W["T"][era]


def game_job(g):
    """Both arms on one real game: the mean simulated KEYS, the pre-game home
    win probability and total draws, and the win probability from each
    checkpoint state. The real figures and result ride along."""
    imp = implied(g)
    if imp is None:
        return None
    era = era_of(g["season"])
    T = _tables(era)
    out = {"id": g["id"], "week": g["week"], "season": g["season"], "real": real_figures(g),
           "y": 1.0 if g["home_pts"] > g["away_pts"] else 0.0 if g["home_pts"] < g["away_pts"] else 0.5,
           "real_margin": g["home_pts"] - g["away_pts"], "spread": g["spread"], "total_line": g["total_line"]}
    states = checkpoint_states(g)
    seed0 = int(g["id"].split("_")[0]) * 100000 + sum(ord(ch) for ch in g["id"])
    for arm in ("control", "coach"):
        curve = _W["curves"][era][arm]
        home, away = _lvl_profile(T, level_for(curve, imp[0])), _lvl_profile(T, level_for(curve, imp[1]))
        fs = _W["form"][arm]
        rng = random.Random(seed0)
        acc = dict.fromkeys(KEYS, 0)
        hw = tie = 0
        totals, margins = [], []
        for _ in range(_W["sims"]):
            log = []
            res = _play(arm, T, home, away, rng, log=log, form_sd=fs)
            f = sim_figures(log, res, arm == "coach")
            for k in KEYS:
                acc[k] += f[k]
            m = res["h"]["pts"] - res["a"]["pts"]
            hw += m > 0
            tie += m == 0
            totals.append(f["total"])
            margins.append(m)
        ns = float(_W["sims"])
        a = {"fig": {k: acc[k] / ns for k in KEYS}, "p_home": (hw + 0.5 * tie) / ns,
             "totals": totals, "margins": margins, "wp": {}}
        for cp, st in (states.items() if _W["playouts"] else ()):
            rng = random.Random(seed0 + cp)
            w = t = 0
            for _ in range(_W["playouts"]):
                res = _play(arm, T, home, away, rng, start=st, form_sd=fs)
                m = res["h"]["pts"] - res["a"]["pts"]
                w += m > 0
                t += m == 0
            a["wp"][str(cp)] = (w + 0.5 * t) / float(_W["playouts"])
        out[arm] = a
    return out


def run_games(games, tables_path, curves, form, sims, playouts, workers=3):
    games = [g for g in games if g["st"] == 2]
    with Pool(workers, initializer=_init, initargs=(tables_path, curves, form, sims, playouts)) as pool:
        return [r for r in pool.imap(game_job, games, chunksize=4) if r]


# ---- the summary both the development table and the read print ------------------
def _mean_se(xs):
    n = len(xs)
    if not n:
        return 0.0, 0.0
    m = sum(xs) / n
    if n < 2:
        return m, 0.0
    return m, math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1) / n)


def _ll(p, y):
    p = min(1 - P_CLIP, max(P_CLIP, p))
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def _crps(draws, x):
    """Sample CRPS of a set of draws against one outcome."""
    n = len(draws)
    s = sorted(draws)
    a = sum(abs(d - x) for d in s) / n
    b = sum((2 * i - n + 1) * d for i, d in enumerate(s)) / (n * n)
    return a - b


def summarize(rows):
    """The registered statistics from run_games' rows."""
    n = len(rows)
    out = {"games": n, "real": {}, "control": {}, "coach": {}}
    for k in FIGS + ("kneels_q2", "kneels_q4", "spikes", "timeouts", "total", "ot"):
        m, se = _mean_se([r["real"][k] for r in rows])
        out["real"][k] = [round(m, 4), round(se, 4)]
        for arm in ("control", "coach"):
            out[arm][k] = round(sum(r[arm]["fig"][k] for r in rows) / n, 4)
    for who, get in (("real", lambda r: r["real"]), ("control", lambda r: r["control"]["fig"]),
                     ("coach", lambda r: r["coach"]["fig"])):
        rn = sum(get(r)["run_n"] for r in rows)
        out[who]["leader_runoff_s"] = round(sum(get(r)["run_s"] for r in rows) / rn, 2) if rn else None
        for b in ("53", "32", "21"):
            dn = sum(get(r)["dto_n" + b] for r in rows)
            out[who]["def_timeout_rate_" + b] = [round(sum(get(r)["dto_k" + b] for r in rows) / dn, 4) if dn else None,
                                                 round(dn, 1)]
    # spreads: the residual of the margin from the line, and of the total
    rm = [r["real_margin"] - r["spread"] for r in rows]
    rt = [r["real"]["total"] - r["total_line"] for r in rows]
    out["real"]["margin_resid_sd"] = round(math.sqrt(sum(x * x for x in rm) / n), 3)
    out["real"]["total_resid_sd"] = round(math.sqrt(sum(x * x for x in rt) / n), 3)
    out["real"]["home_win"] = round(sum(r["y"] for r in rows) / n, 4)
    for arm in ("control", "coach"):
        sm = st = 0.0
        c = 0
        for r in rows:
            for m, t in zip(r[arm]["margins"], r[arm]["totals"]):
                sm += (m - r["spread"]) ** 2
                st += (t - r["total_line"]) ** 2
                c += 1
        out[arm]["margin_resid_sd"] = round(math.sqrt(sm / c), 3)
        out[arm]["total_resid_sd"] = round(math.sqrt(st / c), 3)
        out[arm]["home_win"] = round(sum(r[arm]["p_home"] for r in rows) / n, 4)
        out[arm]["pregame_logloss"] = round(sum(_ll(r[arm]["p_home"], r["y"]) for r in rows) / n, 5)
        out[arm]["total_crps"] = round(sum(_crps(r[arm]["totals"], r["real"]["total"]) for r in rows) / n, 4)
    # paired differences, candidate minus control; a game is the cluster
    d = [_ll(r["coach"]["p_home"], r["y"]) - _ll(r["control"]["p_home"], r["y"]) for r in rows]
    out["pregame_logloss_diff"] = [round(x, 5) for x in _mean_se(d)]
    d = [_crps(r["coach"]["totals"], r["real"]["total"]) - _crps(r["control"]["totals"], r["real"]["total"])
         for r in rows]
    out["total_crps_diff"] = [round(x, 4) for x in _mean_se(d)]
    out["wp"] = {}
    late = []
    for cp in CHECKPOINTS:
        d = [_ll(r["coach"]["wp"][str(cp)], r["y"]) - _ll(r["control"]["wp"][str(cp)], r["y"])
             for r in rows if str(cp) in r["coach"]["wp"]]
        m, se = _mean_se(d)
        out["wp"][str(cp)] = {"n": len(d), "diff": round(m, 5), "se": round(se, 5),
                              "control": round(sum(_ll(r["control"]["wp"][str(cp)], r["y"]) for r in rows
                                                   if str(cp) in r["control"]["wp"]) / max(1, len(d)), 5)}
    for r in rows:
        ds = [_ll(r["coach"]["wp"][str(cp)], r["y"]) - _ll(r["control"]["wp"][str(cp)], r["y"])
              for cp in (300, 120) if str(cp) in r["coach"]["wp"]]
        if ds:
            late.append(sum(ds) / len(ds))          # one number a game: the game is the cluster
    m, se = _mean_se(late)
    out["wp_late"] = {"n": len(late), "diff": round(m, 5), "se": round(se, 5)}
    return out


def curves_for(raw_path, eras, n=2500):
    with open(raw_path) as fh:
        raw = json.load(fh)
    out = {}
    for era in eras:
        T = C.Tables(raw, era=era)
        out[era] = {"control": level_curve("control", T, n), "coach": level_curve("coach", T, n)}
    return out


FREE_GRID = (0.0, 0.04, 0.065, 0.08)
FREE_SEASONS = (2023,)


def free_fit(dirname, sims=30):
    """nfl_deep_sim's procedure for its form tilt, for each arm on THESE tables
    and THIS anchor: the tilt whose margin spread about the closing line is the
    real one's. The target is every fit season's real residual SD; the
    simulated side is played on one fit season's lines (the spread of a
    simulated margin does not depend on which real games lend their lines).
    The home edge needs no fit here: every game is anchored to its line on a
    neutral field."""
    sq = n = 0
    for s in range(2016, 2024):
        for g in CD.cached(dirname, s):
            if g["st"] == 2 and g.get("spread") is not None:
                sq += (g["home_pts"] - g["away_pts"] - g["spread"]) ** 2
                n += 1
    target = math.sqrt(sq / n)
    curves = curves_for(C.TABLES_PATH, ["2016-2023"])
    games = [g for s in FREE_SEASONS for g in CD.cached(dirname, s)]
    grid = {"control": [], "coach": []}
    for fs in FREE_GRID:
        rows = run_games(games, C.TABLES_PATH, curves, {"control": fs, "coach": fs}, sims, 0)
        for arm in grid:
            sm = c = 0
            for r in rows:
                for m in r[arm]["margins"]:
                    sm += (m - r["spread"]) ** 2
                    c += 1
            grid[arm].append([fs, round(math.sqrt(sm / c), 3)])
        print("[free]", fs, {a: grid[a][-1][1] for a in grid}, "target", round(target, 3), flush=True)
    best = {}
    for arm, pts in grid.items():
        best[arm] = pts[0][0]
        for (f0, s0), (f1, s1) in zip(pts, pts[1:]):
            if s0 <= target <= s1 and s1 > s0:
                best[arm] = round(f0 + (f1 - f0) * (target - s0) / (s1 - s0), 3)
        if target > pts[-1][1]:
            best[arm] = pts[-1][0]
    return {"target_margin_resid_sd": round(target, 3), "games": n, "grid": grid, "form_sd": best,
            "sims": sims, "seasons_played": list(FREE_SEASONS)}


def main(argv):
    if not argv or argv[0].startswith("-"):
        print(__doc__)
        return 2
    if "--free" in argv:
        res = free_fit(argv[0])
        outp = os.path.join(os.path.dirname(OUT_DEFAULT), "free_r1.json")
        os.makedirs(os.path.dirname(outp), exist_ok=True)
        with open(outp, "w") as fh:
            json.dump(res, fh, indent=1, sort_keys=True)
        print(json.dumps(res["form_sd"]))
        return 0
    seasons, sims, playouts, outp = [2024, 2025], 60, 200, OUT_DEFAULT
    form = {"control": 0.0, "coach": C._FORM_SD}        # both from --free (free_r1.json)
    for i, a in enumerate(argv):
        if a == "--seasons":
            seasons = [int(x) for x in argv[i + 1].split(",")]
        elif a == "--sims":
            sims = int(argv[i + 1])
        elif a == "--playouts":
            playouts = int(argv[i + 1])
        elif a == "--out":
            outp = argv[i + 1]
        elif a == "--form":
            c_, k_ = argv[i + 1].split(",")
            form = {"control": float(c_), "coach": float(k_)}
    curves = curves_for(C.TABLES_PATH, sorted({era_of(s) for s in seasons}))
    res = {"version": C.VERSION, "sims": sims, "playouts": playouts, "form_sd": form, "seasons": {}}
    for s in seasons:
        rows = run_games(CD.cached(argv[0], s), C.TABLES_PATH, curves, form, sims, playouts)
        res["seasons"][str(s)] = summarize(rows)
        print(s, json.dumps(res["seasons"][str(s)], indent=1), flush=True)
    os.makedirs(os.path.dirname(outp), exist_ok=True)
    with open(outp, "w") as fh:
        json.dump(res, fh, indent=1, sort_keys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
