"""NBA matchup engine, production inference (pure Python, no numpy): for a
date, every player in that day's games -> per prop market (points, rebounds,
assists, threes, PRA and the PR / PA / RA combos) the baseline, the
matchup-adjusted projection, P(stat >= k) for Kalshi's alt lines with a
band, the factors that moved it and his comparable-opponent games.

THE QUESTION (the owner's): how well does this player's style, usage and
role match what THIS defense actually concedes, and how much should that
move each line? The answer is built in rungs, each validated on held-out
seasons before it may carry weight (research/reports/nba_matchup.md; the
committed coefficients research/data/nba_matchup/coefs.json carry the
verdict -- a rung that did not improve held-out log loss ships at weight 0
and its factors print with effect 0):

  1 baseline   his per-minute rate (this season + last season + a recent
               EWMA, shrunk to his position's league rate; weights fitted)
               x projected minutes (his history; a fitted mixture of actual
               / projected minutes, so blowouts, foul trouble and early exits
               are in every probability) x the game's expected pace
  2 defense    the opponent's observed-over-expected for the stat: what its
               opponents produced over their own as-of rates
  3 position   the same against his position group (G / F / C), relative to 2
  4 style      his shares x the opponent's concession ratio in that channel
               (rim / short / mid / three attempts allowed, pull-up and
               spot-up attempts allowed, assisted share allowed, free throws,
               early-clock shots, offensive rebounds allowed, the opponent
               offense's misses and offensive rebounding for his rebounds)
  5 history    his own games against defenses like this one (similarity of
               the opponent's concession profile AS OF that game, recency,
               minutes), relative to his games overall
  6 context    regulars missing from his team (usage / assists / rebounds
               they take), the opponent's recent form, the game's expected
               margin in the minutes model

THE VERDICT (2026-10-23; rule frozen first, 2024-25 chose, 2025-26 tested
once; research/reports/nba_matchup.md): shipped -- points rungs 1, 2, 6;
rebounds 1-6; assists 1, 2, 3, 6; threes 1, 2, 6; the rung-1 minutes model
(M6 failed its test). Style x concession (rung 4) is at weight 0 for points,
assists and threes; comparables (rung 5) add nothing measurable anywhere.
Against Kalshi's pre-tip prices on 2025-26 the market was the better
forecaster on tight books (z -3.3) and betting the model's 3-point edges
lost 14% -- the figures are a projection and an explanation, not an edge.
Rung 6's measured gain is an upper bound for production (it was fitted on
every box absence; production feeds it ESPN's "Out" listings only).

strong_fit (reviews 2026-10-23): only the shipped DEFENSE terms (rungs 2-5
and the defense's recent form) decide it, on the side they favour
(`fit_side`); a board for a date the book already holds is refused.

Distributions: negative binomial given minutes (variance phi*mu + kappa*mu^2,
both fitted per market), mixed over the minutes nodes. PRA / PR / PA / RA
are the sum of the adjusted components' means with their OWN fitted
dispersion (the components' correlation lives in it). P(stat >= k) is
monotone in k by construction (a survival function).

Runs in ~5 s of pure Python per 12-game slate (board_isolated: a niced
child) on the state the `nba_matchup` job leaves in the deep store
(nba_matchup_data.update fetches and cuts the games; rebuild() replays them
into `nba_matchup_book_<season>`); the board itself fetches only ESPN's
scoreboard and injury report (and Kalshi's index when asked) and fits
nothing.
"""
import json
import math
import os

import errlog
import nba_matchup_data as D
import nba_matchup_profiles as M

MARKETS = ("pts", "reb", "ast", "tpm")
# steals, blocks and turnovers: rolling rates with the rung-1 pace/home terms
# only (no matchup rung was built for them); they feed dk_fp
AUX = ("stl", "blk", "tov")
COMBOS = {"pra": ("pts", "reb", "ast"), "pr": ("pts", "reb"), "pa": ("pts", "ast"), "ra": ("reb", "ast")}
# DraftKings NBA Classic scoring AS UNDERSTOOD from DraftKings' NBA rules
# (point +1, made three +0.5, rebound +1.25, assist +1.5, steal +2, block +2,
# turnover -0.5, double-double +1.5, triple-double +3, each bonus at most
# once; a triple-double also earning the double-double bonus). NOT
# machine-read from DraftKings: its rules pages are script-rendered shells and
# api.draftkings.com answered 403 to the research host (2026-10-23, both
# reviews). The double-/triple-double stacking is to be verified against
# DraftKings' own FPTS (a standings export or the draftables feed after a
# slate settles, readable from the owner's PC).
DK_W = {"pts": 1.0, "tpm": 0.5, "reb": 1.25, "ast": 1.5, "stl": 2.0, "blk": 2.0, "tov": -0.5}
DK_DD, DK_TD = 1.5, 3.0
DK_BONUS_PARTS = ("pts", "reb", "ast", "stl", "blk")


def dk_points(st):
    """DraftKings classic points of one box line {pts, tpm, reb, ast, stl, blk, tov}."""
    base = sum(w * st.get(k, 0) for k, w in DK_W.items())
    tens = sum(1 for k in DK_BONUS_PARTS if st.get(k, 0) >= 10)
    return base + (DK_DD if tens >= 2 else 0.0) + (DK_TD if tens >= 3 else 0.0)
KALSHI_SERIES = {"pts": "KXNBAPTS", "reb": "KXNBAREB", "ast": "KXNBAAST", "tpm": "KXNBA3PT", "pra": "KXNBAPRA",
                 "pr": "KXNBAPR", "pa": "KXNBAPA", "ra": "KXNBARA"}
LINES = {"pts": (10, 15, 20, 25, 30, 35, 40, 45), "reb": (2, 4, 6, 8, 10, 12, 14, 16),
         "ast": (2, 4, 6, 8, 10, 12, 14), "tpm": (1, 2, 3, 4, 5, 6),
         "pra": (15, 20, 25, 30, 35, 40, 45, 50, 55), "pr": (10, 15, 20, 25, 30, 35, 40, 45),
         "pa": (10, 15, 20, 25, 30, 35, 40, 45), "ra": (4, 6, 8, 10, 12, 14, 16, 18, 20)}
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "research", "data", "nba_matchup")
_PARAMS = None


def params():
    """The committed fit: research/data/nba_matchup/params.json (profiles,
    minutes, dispersion) merged with coefs.json (the rung coefficients and
    the held-out verdict). {} when missing (the board says so)."""
    global _PARAMS
    if _PARAMS is not None:
        return _PARAMS
    out = {}
    for name in ("params.json", "coefs.json"):
        try:
            with open(os.path.join(DATA_DIR, name)) as fh:
                out.update(json.load(fh))
        except (OSError, ValueError) as e:
            errlog.note("NBAM-params", e, path=name)
    _PARAMS = out
    return out


# ---- the feature builder (research and production share it) ----------------------
def base_rates(psnap, P):
    return {s: M.player_rate(psnap, s, P["rate_W"]) for s in M.RATE_STATS}


def game_pace(own_t, opp_t, P):
    """Expected possessions per 48 of the game: the league's x each team's
    pace ratio (its games' possessions per 48 over the league's, shrunk)."""
    co = M.concessions(own_t, "off", P["team_K"], P["rho"])
    cd = M.concessions(opp_t, "off", P["team_K"], P["rho"])
    lg = co["pace"].get("league") or 99.0
    return lg * co["pace"]["ratio"] * cd["pace"]["ratio"]


def _ln(x):
    return math.log(max(1e-6, x)) if x else 0.0


# Rung-4 interactions: (player share, the opponent's channel). share None = 1.
R4 = {
    "pts": (("rim_share", "def:rim"), ("short_share", "def:short"), ("mid_share", "def:mid"),
            ("three_share", "def:three"), ("ft_share", "def:fta"), ("self_share", "def:pull"),
            ("assisted_share", "def:ast_share"), ("transition_share", "def:early"),
            ("second_chance_share", "def:oreb"), ("rim_share", "def:rim_pct"), ("three_share", "def:three_pct"),
            ("two_jumper_share", "def:mid_pct")),
    "tpm": ((None, "def:three"), ("spot3_share", "def:spot3"), ("pull3_share", "def:pull"),
            ("assisted3_share", "def:ast3"), (None, "def:three_pct")),
    "reb": (("dfrac", "off:miss"), ("dfrac", "off:oreb_inv"), ("ofrac", "def:miss"), ("ofrac", "def:oreb"),
            ("dfrac", "off:three")),
    "ast": ((None, "def:ast_share"), (None, "def:fgm"), ("ast3_share", "def:ast3"), ("ast_rim_share", "def:rim"),
            (None, "def:tov")),
}
# Rung-5's similarity space per market: the opponent channels whose profile
# decides "a defense like this one" (standardized by fit-season SDs).
COMP_DIMS = {"pts": ("def:pts", "def:rim", "def:three", "def:fta", "def:pull", "def:early", "def:oreb", "def:pace"),
             "tpm": ("def:three", "def:spot3", "def:pull", "def:three_pct", "def:ast3", "def:pace"),
             "reb": ("off:miss", "off:oreb", "def:miss", "def:oreb", "off:three", "def:pace"),
             "ast": ("def:ast_share", "def:fgm", "def:ast3", "def:tov", "def:rim", "def:pace")}
RUNGS = {
    1: {m: ("pace", "home") for m in MARKETS},
    2: {"pts": ("oe_all", "def_pts"), "reb": ("oe_all",), "ast": ("oe_all",), "tpm": ("oe_all",)},
    3: {m: ("oe_pos",) for m in MARKETS},
    4: {m: tuple(f"x:{a or 'one'}*{b}" for a, b in R4[m]) for m in MARKETS},
    5: {m: ("comp",) for m in MARKETS},
    6: {m: ("tm_out", "oe_form") for m in MARKETS},
}


# strong_fit (reviews 2026-10-23, O1 / F1): the label reads ONLY what this
# defense measurably concedes -- the shipped model's rung 2-5 terms plus the
# defense's recent form -- never rung 1 (pace, home), never teammates out,
# and it goes on the side the shift favours.
RUNG_NAME = {1: "baseline (pace, home)", 2: "overall defense", 3: "position allowance",
             4: "style x concession", 5: "similar defenses", 6: "context (teammates out, defense form)"}
FIT_UP, FIT_DOWN = 1.05, 0.95


def defense_names(m):
    """The defense terms of market m: rungs 2-5 and the defense's recent form."""
    return tuple(n for k in (2, 3, 4, 5) for n in RUNGS[k][m]) + ("oe_form",)


def fit_multiplier(P, m, x):
    """exp(sum coef x value) over the SHIPPED model's defense terms of market m
    (a term the model does not ship has no coefficient and contributes 1)."""
    coef = (P.get("coefs") or {}).get(m) or {}
    return math.exp(sum(coef.get(n, 0.0) * x.get(n, 0.0) for n in defense_names(m)))


def fit_side(mult):
    """"yes" when the defense terms raise the mean 5%+, "no" when they cut it
    5%+, else None (no strong_fit)."""
    if mult is None:
        return None
    return "yes" if mult >= FIT_UP else ("no" if mult <= FIT_DOWN else None)


def channel_ratios(own_t, opp_t, P):
    """{"def:<ch>": ratio, "off:<ch>": ratio} for the OPPONENT (what it
    concedes, and its own offense) with the full per-channel detail."""
    cd = M.concessions(opp_t, "def", P["team_K"], P["rho"])
    co = M.concessions(opp_t, "off", P["team_K"], P["rho"])
    out = {f"def:{k}": v for k, v in cd.items()}
    out.update({f"off:{k}": v for k, v in co.items()})
    return out


def _shares(role, rates):
    s = {k: v["value"] for k, v in role.items()}
    fr = s.get("ft_rate", 0.0)
    s["ft_share"] = 0.44 * fr / (1.0 + 0.44 * fr)
    s["self_share"] = 1.0 - s.get("assisted_share", 0.5)
    s["two_jumper_share"] = s.get("short_share", 0.0) + s.get("mid_share", 0.0)
    o, d = rates["oreb"][0], rates["dreb"][0]
    s["dfrac"] = d / (o + d) if o + d > 0 else 0.75
    s["ofrac"] = 1.0 - s["dfrac"]
    return s


def features(psnap, own_t, opp_t, P, home, ctx=None):
    """Everything any rung reads for one player-game: {"rates", "role",
    "shares", "chan", "x": {market: {feature: value}}, "pace_hat",
    "his_pace"}. ctx: {"comp": {market: score}, "tm_out": {market: x}}."""
    ctx = ctx or {}
    rates = base_rates(psnap, P)
    role = M.role(psnap, P["role_K"])
    shares = _shares(role, rates)
    chan = channel_ratios(own_t, opp_t, P)
    ph = game_pace(own_t, opp_t, P)
    hp = M.on_floor_pace(psnap) or ph
    pos = psnap["pos"]
    x = {}
    for m in MARKETS:
        st = m
        f = {"pace": _ln(ph / hp) if hp else 0.0, "home": 1.0 if home else -1.0}
        oe_all, _n, oe_rec = M.oe(opp_t, st, 0, P.get("oe_K"), P.get("oe_rho", 0.5))
        f["oe_all"] = _ln(oe_all)
        if m == "pts":
            f["def_pts"] = _ln(chan["def:pts"]["ratio"])
        if pos >= 0:
            oe_p, _np, _r = M.oe(opp_t, st, pos + 1, P.get("oe_K"), P.get("oe_rho", 0.5))
            f["oe_pos"] = _ln(oe_p) - _ln(oe_all)
        else:
            f["oe_pos"] = 0.0
        for a, b in R4[m]:
            sh = 1.0 if a is None else shares.get(a, 0.0)
            if b == "off:oreb_inv":
                c = chan["off:oreb"]
                lg = c.get("league") or 0.27
                val = math.log(max(1e-6, 1.0 - lg * c["ratio"]) / max(1e-6, 1.0 - lg))
            else:
                val = _ln(chan[b]["ratio"])
            f[f"x:{a or 'one'}*{b}"] = sh * val
        f["comp"] = (ctx.get("comp") or {}).get(m, 0.0)
        f["tm_out"] = (ctx.get("tm_out") or {}).get(m, 0.0)
        f["oe_form"] = (_ln(oe_rec) - _ln(oe_all)) if oe_rec else 0.0
        x[m] = f
    for m in AUX:
        x[m] = {"pace": _ln(ph / hp) if hp else 0.0, "home": 1.0 if home else -1.0}
    return {"rates": rates, "role": role, "shares": shares, "chan": chan, "x": x, "pace_hat": ph, "his_pace": hp}


def comp_vector(chan, market, P):
    sd = (P.get("comp_sd") or {})
    return [(_ln(chan[d]["ratio"]) / (sd.get(d) or 0.05)) for d in COMP_DIMS[market]]


COMP_H = 0.75            # similarity bandwidth, per standardized dimension (set by rule, not tuned)
COMP_TAU = 365.0         # recency half-life, days
COMP_K = 5.0             # pseudo-games each weighted mean is shrunk by


def comparables(hist, vec, day, market):
    """Rung 5's score and its evidence. hist: the player's earlier games
    [(day_number, vector, log_residual, minutes, meta)], every one of them
    (good and bad outcomes alike). Similarity exp(-mean sq. distance /
    (2 h^2)), recency 0.5^(age/365), minutes quality min(1, min/20). Score =
    the similarity-weighted mean residual minus his recency-weighted mean
    residual, each shrunk by COMP_K pseudo-games. Returns (score, n_eff,
    sum of weights, [(weight, similarity, meta)])."""
    sw = swr = sw2 = 0.0
    bw = bwr = 0.0
    used = []
    n = len(vec)
    for d, v, r, mins, meta in hist:
        if d >= day:
            continue
        dist = sum((a - b) ** 2 for a, b in zip(vec, v)) / n
        sim = math.exp(-dist / (2.0 * COMP_H * COMP_H))
        rec = 0.5 ** ((day - d) / COMP_TAU)
        q = min(1.0, mins / 20.0)
        w = sim * rec * q
        sw += w
        swr += w * r
        sw2 += w * w
        bw += rec * q
        bwr += rec * q * r
        used.append((w, sim, meta))
    if not used:
        return 0.0, 0.0, 0.0, []
    score = swr / (sw + COMP_K) - bwr / (bw + COMP_K)
    n_eff = (sw * sw / sw2) if sw2 > 0 else 0.0
    return score, n_eff, sw, used


# ---- distributions ----------------------------------------------------------------
def nb_sf_all(mu, phi, kappa, kmax):
    """[P(Y >= k) for k = 0 .. kmax] for a negative binomial with mean mu and
    variance phi*mu + kappa*mu^2 (Poisson when that is not above mu)."""
    mu = max(mu, 1e-9)
    v = phi * mu + kappa * mu * mu
    out = [1.0] * (kmax + 1)
    if v <= mu * (1.0 + 1e-9):
        pm = math.exp(-mu)
        cdf = 0.0
        for k in range(1, kmax + 1):
            cdf += pm
            out[k] = max(0.0, 1.0 - cdf)
            pm *= mu / k
        return out
    r = mu * mu / (v - mu)
    p = r / (r + mu)
    pm = math.exp(r * math.log(p))
    cdf = 0.0
    q = 1.0 - p
    for k in range(1, kmax + 1):
        cdf += pm
        out[k] = max(0.0, 1.0 - cdf)
        pm *= (k - 1 + r) / k * q
    return out


def mixture_sf(rate, m_hat, nodes, phi, kappa, kmax):
    """P(stat >= k) mixed over the minutes nodes (equal mass)."""
    acc = [0.0] * (kmax + 1)
    for q in nodes:
        sf = nb_sf_all(rate * m_hat * q, phi, kappa, kmax)
        for k in range(kmax + 1):
            acc[k] += sf[k]
    n = float(len(nodes))
    out = [a / n for a in acc]
    # monotone by construction; enforce against float drift
    for k in range(1, kmax + 1):
        if out[k] > out[k - 1]:
            out[k] = out[k - 1]
    return out


def min_nodes(m_hat, P, key="min_dist"):
    for b in P[key]:
        if b["lo"] <= m_hat < b["hi"]:
            return b["nodes"]
    return P[key][-1]["nodes"]


def linear(beta, names, x):
    return sum(beta.get(n, 0.0) * x.get(n, 0.0) for n in names) + beta.get("_icpt", 0.0)


# ---- the as-of context every row needs (research and production share it) -------------
E_STATS = ("pts", "reb", "ast", "tpm", "tpa", "oreb", "dreb", "stl", "blk", "tov")


def _game_pace_ratio(r, sn):
    secs = r[D.PI["secs"]]
    hp = M.on_floor_pace(sn)
    if secs < 120 or not hp:
        return 1.0
    gp = (r[D.PI["poss_off"]] + r[D.PI["poss_def"]]) / 2.0 / secs * 2880.0
    return min(1.5, max(0.67, gp / hp))


def expected_for(r, sn, P):
    """A PAST player-game's expected stats from his as-of rates, his actual
    minutes and the game's on-floor pace over his usual (pace-neutral): what a
    defense's observed-over-expected and his comparables' residuals divide by.
    (Actual minutes are allowed here: this measures a game already played.)"""
    rates = {s: M.player_rate(sn, s, P["rate_W"])[0] for s in E_STATS}
    k = r[D.PI["min"]] * _game_pace_ratio(r, sn)
    return {s: rates[s] * k for s in E_STATS}


def net_rating(t, P):
    """ln(points scored per possession ratio) - ln(allowed): the team's
    strength for the minutes model's blowout term."""
    co = M.concessions(t, "off", P["team_K"], P["rho"])
    cd = M.concessions(t, "def", P["team_K"], P["rho"])
    return math.log(co["pts"]["ratio"]) - math.log(cd["pts"]["ratio"])


def team_context(book, team_ab, day, absent, P, own_t):
    """Rung 6's teammates-out context for one side of a game: the as-of
    regulars (Book.rotation) in `absent` (a set of ids, or a callable id ->
    bool). Research reads 'absent' off the box (the inactive list and the
    injury report announce it before the tip; a coach's late scratch is the
    one leak); production reads ESPN's injury statuses (Out)."""
    is_out = absent if callable(absent) else (lambda pid: pid in absent)
    miss_use = miss_ast = miss_reb = 0.0
    miss_min = [0.0, 0.0, 0.0]
    who = []
    tg = max(1.0, own_t["off"][0][M.TS["g"]] + 0.5 * own_t["off"][1][M.TS["g"]])
    tm_ast = (own_t["off"][0][M.TS["ast"]] + 0.5 * own_t["off"][1][M.TS["ast"]]) / tg or 25.0
    tm_reb = (own_t["off"][0][M.TS["oreb"]] + own_t["off"][0][M.TS["dreb"]]
              + 0.5 * (own_t["off"][1][M.TS["oreb"]] + own_t["off"][1][M.TS["dreb"]])) / tg or 44.0
    for pid in book.rotation(team_ab):
        if not is_out(pid):
            continue
        sn = book.player(pid, day)
        if sn is None:
            continue
        e6 = sn["m_e"][1]
        rl = M.role(sn, P["role_K"])
        miss_use += rl["usage"]["value"] * e6 / 48.0
        miss_ast += M.player_rate(sn, "ast", P["rate_W"])[0] * e6 / tm_ast
        miss_reb += M.player_rate(sn, "reb", P["rate_W"])[0] * e6 / tm_reb
        if sn["pos"] >= 0:
            miss_min[sn["pos"]] += e6 / 48.0
        who.append(pid)
    return {"use": miss_use, "ast": miss_ast, "reb": miss_reb, "min": miss_min, "who": who}


def tm_out_x(c):
    return {"pts": math.log1p(c["use"]), "tpm": math.log1p(c["use"]), "ast": math.log1p(c["ast"]),
            "reb": math.log1p(c["reb"])}


def minutes_row(sn, own_ab, own_tg, pos, ctx_side, nets_side, nets_opp):
    """The minutes model's inputs (rung-1 history terms + the M6 context)."""
    return {"mf": M.minutes_features(sn),
            "missed_last": 1.0 if (sn["team"] == own_ab and own_tg - sn["last_tg"] >= 1) else 0.0,
            "traded": 1.0 if sn["team"] != own_ab else 0.0,
            "m6": [ctx_side["min"][pos] if pos >= 0 else 0.0, sum(ctx_side["min"]), abs(nets_side - nets_opp),
                   abs(nets_side - nets_opp) * sn["st_e"]]}


MIN_KEYS = ("icpt", "e2", "e6", "e15", "avg_cur", "avg_prev_x_has", "has_prev", "st_e", "b2b", "long_rest",
            "g_small", "missed_last", "traded")


def min_x(r):
    f = r["mf"]
    return [1.0, f["e2"], f["e6"], f["e15"], f["avg_cur"], f["avg_prev"] * f["has_prev"], f["has_prev"], f["st_e"],
            f["b2b"], f["long_rest"], min(f["g_cur"], 10.0) / 10.0, r["missed_last"], r["traded"]]


def pred_minutes(r, beta, m6=False):
    x = min_x(r) + (list(r["m6"]) if m6 else [])
    return float(min(46.0, max(1.0, sum(b * v for b, v in zip(beta, x)))))


# ---- the job's state: the book replayed with the committed fit ------------------------
BOOK_KEY = "nba_matchup_book_{season}"
_LOG_STATS = ("pts", "reb", "ast", "tpm", "stl", "blk", "tov")


def game_day(iso):
    """The Mountain-time date of a tip (UTC minus six hours): a game's day."""
    import datetime
    t = datetime.datetime.strptime(iso[:16], "%Y-%m-%dT%H:%M")
    return (t - datetime.timedelta(hours=6)).date().isoformat()


def mt_today():
    """The owner's current Mountain-time date (ISO)."""
    import time as _t
    import clock
    return clock.mt_date(_t.time()).isoformat()


def _entries(season, today=None):
    """Both seasons' cut games for the replay -- EXCEPT any whose Mountain-time
    game day is `today` (the owner's MT date) or later. The data store keeps
    ingesting every finished game, but the book must never hold the current
    MT day: board() refuses a date the book already holds, and a job run can land
    after the early tips are final (ET midnight is 22:00 MT), so taking in
    today's early finals would blank the late games' board until midnight (Fable
    re-check R1, 2026-10-23). The job reruns every six hours (app.py registers it
    with cadence "age") so a run after MT midnight takes in the night before."""
    today = today or mt_today()
    out = []
    for s in (int(season) - 1, int(season)):
        st = D.load_state(s)
        for e in (st.get("games") or {}).values():
            if e.get("st") not in (2, None):
                continue
            day = game_day(e["date"])
            if day >= today:
                continue
            out.append(dict(e, day=day, season=s))
    out.sort(key=lambda e: (e["day"], e["id"]))
    return out


def replay(entries, P, log_from=None):
    """Walk `entries` (cut games, day-sorted) through a Book with the fit's
    expected stats filling the defenses' observed-over-expected (exactly the
    research walk). Returns (book, logs {pid: [(day number, opp, minutes,
    stats, E, vectors, gid)]}) -- the logs hold every played game from
    `log_from` (a day string) on, good and bad alike."""
    book = M.Book()
    logs = {}
    cur, day = [], None

    def flush(day, games):
        book.roll(games[0]["season"])
        ex = {}
        dn = M._day(day)
        tsn_cache, chan_cache = {}, {}
        for g in games:
            ex_g = {}
            for s, ab in ((0, g["home"]), (1, g["away"])):
                tsn_cache[(g["id"], s)] = book.team(ab)
            for pid, r in g["players"].items():
                if not r[D.PI["played"]]:
                    continue
                sn = book.player(pid, day)
                if sn is None:
                    continue
                side = r[D.PI["side"]]
                ex_g[pid] = expected_for(r, sn, P)
                if log_from is None or day >= log_from:
                    key = (g["id"], side)
                    if key not in chan_cache:
                        chan_cache[key] = channel_ratios(tsn_cache[(g["id"], side)], tsn_cache[(g["id"], 1 - side)], P)
                    chan = chan_cache[key]
                    st = {k: (r[D.PI["oreb"]] + r[D.PI["dreb"]] if k == "reb" else r[D.PI[k]]) for k in _LOG_STATS}
                    logs.setdefault(pid, []).append(
                        (dn, (g["home"], g["away"])[1 - side], r[D.PI["min"]], st,
                         {m: ex_g[pid][m] for m in MARKETS}, {m: comp_vector(chan, m, P) for m in MARKETS}, g["id"]))
            ex[g["id"]] = ex_g
        book.add_date([dict(g, date=day) for g in games], expected=ex)

    for e in entries:
        if e["day"] != day and cur:
            flush(day, cur)
            cur = []
        day = e["day"]
        cur.append(e)
    if cur:
        flush(day, cur)
    return book, logs


def rebuild(season, log=None, today=None):
    """The `nba_matchup` job's second step (after nba_matchup_data.update for
    this season -- and, once, for last season): replay both seasons' cut
    games with the committed fit and keep the book and the players' logs in
    the deep store (`nba_matchup_book_<season>`). Measured on the research
    host: ~2,450 games in ~35 s of pure Python, ~15 MB pickled. PC-first;
    under deep_cache.HEAVY_BUILD in a niced child on the server."""
    import time as _t
    import deep_cache
    P = params()
    if not P.get("rate_W"):
        return None
    t0 = _t.time()
    entries = _entries(season, today)
    book, logs = replay(entries, P)
    state = {"season": int(season), "book": book, "logs": logs, "through": entries[-1]["day"] if entries else None,
             "n_games": len(entries), "built": _t.time(), "secs": round(_t.time() - t0, 1),
             "fit": P.get("fit_id"), "seasons": [int(season) - 1, int(season)]}
    deep_cache.save(BOOK_KEY.format(season=int(season)), state)
    if log:
        log(f"[nba-matchup] rebuilt {season}: {len(entries)} games in {state['secs']}s")
    return state


def load_book(season):
    import deep_cache
    st, ts = deep_cache.load(BOOK_KEY.format(season=int(season)))
    if not isinstance(st, dict) or not st.get("book"):
        return None
    st["ts"] = ts
    return st


# ---- one player's prediction -----------------------------------------------------------
STAT_NAME = {"pts": "pts", "reb": "reb", "ast": "ast", "tpm": "fg3m", "pra": "pra", "pr": "pr", "pa": "pa",
             "ra": "ra", "dk": "dk_fp"}
DK_LINES = (10, 20, 30, 40, 50, 60)
_TEXT = {"pts": "points", "reb": "rebounds", "ast": "assists", "tpm": "threes", "pra": "PRA", "pr": "P+R",
         "pa": "P+A", "ra": "R+A", "dk": "DK points"}


def _hist_stat(st, m):
    if m == "dk":
        return dk_points(st)
    return sum(st[c] for c in COMBOS.get(m, (m,)))


def _shipped(P):
    """{market: [rungs]}, minutes "m1"/"m6" -- the held-out verdict."""
    sh = P.get("shipped") or {}
    return {m: (sh.get(m) or {}).get("rungs") or [1] for m in MARKETS}, sh.get("minutes") or "m1"


def _model_names(P, m):
    return [k for k in (P.get("coefs") or {}).get(m, {}) if k != "_icpt"]


def _rate_with(rate0, coef, x):
    return rate0 * math.exp(sum(coef.get(n, 0.0) * x.get(n, 0.0) for n in coef if n != "_icpt")
                            + coef.get("_icpt", 0.0))


def _dk_sf(rates, m_hat, nodes, P, kmax):
    """P(DK points >= k): per minutes node the components' weighted rate x
    minutes plus the double-/triple-double bonus from each component's own
    P(>= 10) at that node, then the fitted negative binomial on floor(DK)."""
    disp = P["disp"]
    phi, kappa = disp["dk"][0], disp["dk"][1]
    acc = [0.0] * (kmax + 1)
    means = []
    for q in nodes:
        m = m_hat * q
        lin = sum(w * rates[k] for k, w in DK_W.items()) * m
        dist = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        for c in DK_BONUS_PARTS:
            p10 = nb_sf_all(rates[c] * m, disp[c][0], disp[c][1], 10)[10]
            new = [dist[i] * (1 - p10) for i in range(6)]
            for i in range(1, 6):
                new[i] += dist[i - 1] * p10
            dist = new
        mu = lin + DK_DD * sum(dist[2:]) + DK_TD * sum(dist[3:])
        means.append(mu)
        sf = nb_sf_all(max(0.1, mu - 0.375), phi, kappa, kmax)
        for k in range(kmax + 1):
            acc[k] += sf[k]
    n = float(len(nodes))
    out = [a / n for a in acc]
    for k in range(1, kmax + 1):
        out[k] = min(out[k], out[k - 1])
    return out, sum(means) / n


def predict(sn, own_t, opp_t, P, home, ctx, hist, day_n, mrow):
    """Everything the payload needs for one player: per market the baseline
    and adjusted rates, the minutes, the survival functions (with the band's
    ends), the comparables. Pure Python; ~10-20 ms a player."""
    fe = features(sn, own_t, opp_t, P, home, ctx={"tm_out": tm_out_x(ctx)})
    comps = {}
    for m in MARKETS:
        vec = comp_vector(fe["chan"], m, P)
        h = [(d, v[m], math.log((st[m] + 0.5) / (E[m] + 0.5)), mins, (d, opp, st, gid))
             for (d, opp, mins, st, E, v, gid) in hist]
        comps[m] = comparables(h, vec, day_n, m)
        fe["x"][m]["comp"] = comps[m][0]
    ship, mm = _shipped(P)
    coefs, base = P["coefs"], P["coefs_base"]
    mb = P["min_beta6"] if mm == "m6" else P["min_beta"]
    m_hat = pred_minutes(mrow, mb, m6=(mm == "m6"))
    nodes = min_nodes(m_hat, P, "min_dist6" if mm == "m6" else "min_dist")
    m_hat_b = pred_minutes(mrow, P["min_beta"])
    nodes_b = min_nodes(m_hat_b, P, "min_dist")
    r0 = {s: fe["rates"][s][0] for s in fe["rates"]}
    rate_a = {m: _rate_with(r0[m], coefs[m], fe["x"][m]) for m in MARKETS}
    rate_b = {m: _rate_with(r0[m], base[m], fe["x"][m]) for m in MARKETS}
    for a in AUX:
        rate_a[a] = rate_b[a] = _rate_with(r0[a], coefs[a], fe["x"][a])
    # sampling SE of each per-minute rate (log scale): his own events plus
    # the prior's pseudo-minutes, inflated by the market's dispersion
    se = {}
    for m in MARKETS:
        rr, den, _own = fe["rates"][m]
        K = P["rate_W"][m][2]
        se[m] = math.sqrt(P["disp"][m][0] / max(1.0, rr * (den + K)))
    fm = {m: fit_multiplier(P, m, fe["x"][m]) for m in MARKETS}
    out = {"fe": fe, "comps": comps, "m_hat": m_hat, "m_hat_base": m_hat_b, "nodes": nodes, "markets": {}, "mm": mm,
           "ship": ship, "fit_mult": fm}
    for m in MARKETS + tuple(COMBOS):
        parts = COMBOS.get(m, (m,))
        ra = sum(rate_a[c] for c in parts)
        rb = sum(rate_b[c] for c in parts)
        s_ = math.sqrt(sum((rate_a[c] * se[c]) ** 2 for c in parts)) / ra if ra > 0 else 0.0
        lines = LINES[m]
        kmax = max(lines) + 1
        phi, kappa = P["disp"][m][0], P["disp"][m][1]
        sf = mixture_sf(ra, m_hat, nodes, phi, kappa, kmax)
        lo = mixture_sf(ra * math.exp(-s_), m_hat, nodes, phi, kappa, kmax)
        hi = mixture_sf(ra * math.exp(s_), m_hat, nodes, phi, kappa, kmax)
        # a combo's defense multiplier: its adjusted rate over the same rate
        # with each component's defense terms taken out
        fmc = ra / sum(rate_a[c] / fm[c] for c in parts) if ra > 0 else 1.0
        out["markets"][m] = {"rate": ra, "rate_base": rb, "se": s_, "sf": sf, "sf_lo": lo, "sf_hi": hi, "fit_mult": fmc,
                             "mean": ra * m_hat * sum(nodes) / len(nodes),
                             "mean_base": rb * m_hat_b * sum(nodes_b) / len(nodes_b), "phi": phi, "kappa": kappa}
    kmax = 120          # past the mass: the SD the DFS sheet reads comes off this table
    sf, mean = _dk_sf(rate_a, m_hat, nodes, P, kmax)
    _sfb, mean_b = _dk_sf(rate_b, m_hat_b, nodes_b, P, 2)
    # the band: every component's rate moved together by its own SE
    lo = _dk_sf({k: v * math.exp(-se.get(k, 0.1)) for k, v in rate_a.items()}, m_hat, nodes, P, kmax)[0]
    hi = _dk_sf({k: v * math.exp(se.get(k, 0.1)) for k, v in rate_a.items()}, m_hat, nodes, P, kmax)[0]
    sd = _sd_from_sf(sf)
    lin = sum(w * rate_a[k] for k, w in DK_W.items())
    lin0 = sum(w * rate_a[k] / fm.get(k, 1.0) for k, w in DK_W.items())
    out["markets"]["dk"] = {"rate": None, "sf": sf, "sf_lo": lo, "sf_hi": hi, "mean": mean, "mean_base": mean_b,
                            "fit_mult": (lin / lin0) if lin0 > 0 else 1.0,
                            "sd": sd, "phi": P["disp"]["dk"][0], "kappa": P["disp"]["dk"][1]}
    return out


def _sd_from_sf(sf):
    """SD of a nonnegative integer variable from its survival function
    (truncated at the table's end, which the callers size past the mass)."""
    m1 = sum(sf[1:])
    m2 = sum((2 * k - 1) * sf[k] for k in range(1, len(sf)))
    return math.sqrt(max(0.0, m2 - m1 * m1))


# ---- the slate --------------------------------------------------------------------------
_SB = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"


def fetch_games(date):
    """[{game_id, home, away, start_ts, state}] for a US date (ESPN's
    scoreboard), franchise games only."""
    import nba_pbp as PB
    out = []
    for ev in PB._day_events(date.replace("-", "")):
        if ev["home"] not in PB.FRANCHISES or ev["away"] not in PB.FRANCHISES or ev["st"] not in (2, 3, 5):
            continue
        out.append({"game_id": ev["id"], "home": ev["home"], "away": ev["away"],
                    "start_ts": _epoch(ev.get("date")), "state": ev.get("state"), "st": ev["st"]})
    return out


def _epoch(iso):
    import datetime
    if not iso:
        return None
    try:
        return datetime.datetime.strptime(iso[:16], "%Y-%m-%dT%H:%M").replace(tzinfo=datetime.timezone.utc).timestamp()
    except ValueError:
        return None


def status_of(espn_status):
    s = (espn_status or "").lower()
    if s == "out":
        return "out"
    if s in ("day-to-day", "questionable", "doubtful", "probable", "game time decision"):
        return "questionable"
    return "unknown"


def fetch_statuses(game_ids):
    """{espn player id: {"value", "source", "ts", "espn"}} from each game's
    pre-game summary (ESPN's injury report). A failed fetch leaves its
    players 'unknown' (ledgered NBAM-injuries)."""
    import racing
    out = {}
    for gid in game_ids:
        try:
            d = racing._get_json(f"{_SB}/summary?event={gid}", timeout=20)
        except Exception as e:
            errlog.note("NBAM-injuries", e, path=str(gid))
            continue
        for t in (d or {}).get("injuries") or []:
            for i in t.get("injuries") or []:
                pid = (i.get("athlete") or {}).get("id")
                if not pid:
                    continue
                out[int(pid)] = {"value": status_of(i.get("status")), "espn": i.get("status"),
                                 "source": "ESPN injury report (game summary)", "ts": _epoch(i.get("date"))}
    return out


def _round(x, n=3):
    return None if x is None else round(float(x), n)


def _sig(x, n=6):
    """n significant digits: the payload's size without the guard's precision
    (a factor's exp(coef x value) still multiplies back to 1e-5)."""
    if x is None or x == 0:
        return x
    return float(f"{float(x):.{n}g}")


def _rank_table(book, P, teams):
    """{channel: sorted ratios} over the league's clubs for ranks / percentiles."""
    tab = {}
    for ab in teams:
        cd = M.concessions(book.team(ab), "def", P["team_K"], P["rho"])
        co = M.concessions(book.team(ab), "off", P["team_K"], P["rho"])
        for k, v in cd.items():
            tab.setdefault(f"def:{k}", []).append(v["ratio"])
        for k, v in co.items():
            tab.setdefault(f"off:{k}", []).append(v["ratio"])
    return {k: sorted(v, reverse=True) for k, v in tab.items()}


def _rank(tab, key, v):
    arr = tab.get(key) or []
    if not arr:
        return None, None
    r = 1 + sum(1 for x in arr if x > v + 1e-12)
    return r, round(100.0 * (len(arr) - r) / max(1, len(arr) - 1), 1)


DEFINITIONS = {
    "role": "his shares and rates from his own games: this season + 0.5 x last season + a recent EWMA (half-life 10 "
            "games), each shrunk toward his position group's league value by a K measured on 2022-23 + 2023-24 "
            "(split-half reliability); 'raw' is the unshrunk share; n is the denominator he has.",
    "opponent_profile": "what the opponent CONCEDED (its defense) or did (its own offense), as a ratio to the league "
                        "('x_league'); this season shrunk in log space toward last season's (persistence measured) "
                        "with K denominators measured by split-half reliability; 'rate' is the shrunk rate in its "
                        "own unit; rank 1 = the most among the clubs. Schemes (drop / switch / zone) are not "
                        "observable in ESPN's feed and are never labelled.",
    "factors": "every feature of every market: mult = exp(coef x value) is exactly what the shipped model applied "
               "(the adjusted per-minute rate = as-of rate x exp(intercept) x the product of the mults); "
               "delta_mean = what removing that one factor would take off the adjusted mean; a rung the held-out "
               "test did not ship shows mult 1.",
    "lines": "p = P(stat >= k) from the minutes-mixed negative binomial; p_lo / p_hi move his per-minute rates by one "
             "sampling SE (minutes and coefficient uncertainty are not in the band); hit_similar_def is the "
             "similarity-weighted hit rate over his earlier games times their effective number.",
}

# units of the payload's facts (the UI draws "share" as a percent and every
# other unit as a number beside its name; it never guesses from magnitude)
ROLE_UNIT = {"ft_rate": "per_fga"}
ROLE_N_UNIT = {"usage": "team plays on floor", "ft_rate": "FGA", "assisted_share": "made FG",
               "assisted3_share": "made threes", "pull3_share": "3PA", "spot3_share": "3PA", "oreb_pct": "chances",
               "dreb_pct": "chances", "ast_pct": "teammate makes", "ast3_share": "assists", "ast_rim_share": "assists",
               "tov_rate": "plays", "min_share": "games x 48"}
CONC_UNIT = {k: ("share" if den in ("a_rim", "a_short", "a_mid", "a_3", "fgm", "oreb_ch") else
                 "per_live_start" if den == "live_starts" else "poss_per_48" if den == "secs" else "per_poss")
             for k, _n, den, _kd, _b in M.CONC_DEFS}
CONC_N_UNIT = {k: ("possessions" if den in ("poss", "secs") else "live-ball starts" if den == "live_starts"
                   else "rebound chances" if den == "oreb_ch" else "attempts" if den.startswith("a_") else "made FG")
               for k, _n, den, _kd, _b in M.CONC_DEFS}


def _finite(o):
    """The payload with every NaN / inf replaced by None (the contract: no
    non-finite number leaves the producer)."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _finite(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_finite(v) for v in o]
    return o


PROFILE_CHANNELS = ("pts", "pace", "rim", "short", "mid", "three", "rim_pct", "three_pct", "ast_share", "ast3",
                    "unast3", "pull", "spot3", "drive", "cut", "putback", "post", "fta", "tov", "oreb", "early",
                    "second")


def _off_text(basis):
    """A concession channel's wording for the team's OWN offense."""
    return (basis.replace(" allowed", "").replace(" forced", "").replace("conceded", "").replace("  ", " ").strip())


def _feature_text(name, x, fe, opp_abbr, P, pos):
    """(player side, opponent side, evidence) for one model feature."""
    chan = fe["chan"]
    rel = P.get("team_reliability") or {}
    role = fe["role"]
    if name.startswith("x:"):
        a, b = name[2:].split("*")
        c = chan.get(b) if b != "off:oreb_inv" else chan.get("off:oreb")
        sh = fe["shares"].get(a) if a != "one" else None
        rl = role.get(a) or {}
        ptxt = (f"{a.replace('_', ' ')} {sh:.0%}" + (f" (n={rl.get('n')})" if rl else "")) if sh is not None else "all of his shots"
        b_ = b.split(":")[1] if ":" in b else b
        if b.startswith("off:"):
            what = _off_text(c["basis"]) if c else ""
            otxt = (f"{opp_abbr}'s own offense: {what} {c['ratio']:.2f}x league"
                    + (" (the share of their misses they do NOT get back is what his DREB feeds on)"
                       if b == "off:oreb_inv" else "") + f" (n={c['n']}, {c['kind']})") if c else ""
        else:
            otxt = f"{opp_abbr} defense: {c['basis']} {c['ratio']:.2f}x league (n={c['n']}, {c['kind']})" if c else ""
        ev = {"n": c["n"] if c else None, "n_eff": None,
              "reliability": (rel.get(b if b != "off:oreb_inv" else "off:oreb") or {}).get("split_half_r"),
              "kind": "proxy" if (c and c["kind"] == "proxy") or (rl and rl.get("kind") == "proxy") else "observed"}
        return f"{b_}", ptxt, otxt, ev
    if name == "oe_all":
        return "Overall vs this defense", "his as-of rates", f"{opp_abbr}'s opponents produce {math.exp(x) - 1:+.0%} vs their own norms", \
            {"n": None, "n_eff": None, "reliability": None, "kind": "modelled"}
    if name == "def_pts":
        c = chan["def:pts"]
        return "Points allowed", "", f"{opp_abbr} allows {c['ratio']:.3f}x league points per possession (n={c['n']} poss)", \
            {"n": c["n"], "n_eff": None, "reliability": (rel.get("def:pts") or {}).get("split_half_r"), "kind": "observed"}
    if name == "oe_pos":
        g = ("G", "F", "C")[pos] if pos in (0, 1, 2) else "?"
        return "Position allowance", f"position {g}", f"{opp_abbr} vs {g}s {math.exp(x) - 1:+.0%} relative to its overall", \
            {"n": None, "n_eff": None, "reliability": None, "kind": "modelled"}
    if name == "comp":
        return "Similar defenses", "his games vs defenses like this one", f"residual {math.exp(x) - 1:+.0%} vs his own norm", \
            {"n": None, "n_eff": None, "reliability": None, "kind": "modelled"}
    if name == "tm_out":
        return "Teammates out", "regulars ruled out (ESPN 'Out')", f"ln(1 + their share) = {x:.3f}", \
            {"n": None, "n_eff": None, "reliability": None, "kind": "modelled"}
    if name == "oe_form":
        return "Defense recent form", "", f"{opp_abbr} last ~10 games vs its season: {math.exp(x) - 1:+.0%}", \
            {"n": None, "n_eff": None, "reliability": None, "kind": "modelled"}
    if name == "pace":
        return "Pace", f"his on-floor pace {fe['his_pace']:.1f}", f"game pace expected {fe['pace_hat']:.1f}", \
            {"n": None, "n_eff": None, "reliability": None, "kind": "modelled"}
    if name == "home":
        return "Home court", "", "", {"n": None, "n_eff": None, "reliability": None, "kind": "observed"}
    return name, "", "", {"n": None, "n_eff": None, "reliability": None, "kind": "modelled"}


def _factors(pred, P, opp_abbr, pos):
    """The matchup matrix: every feature of every market (rung 1's pace and
    home court too), with the effect the SHIPPED model applied: mult =
    exp(coef x value), so the market's adjusted per-minute rate is its as-of
    rate x exp(intercept) x the product of its factors' mults (guarded);
    delta_mean = what removing that one factor would take off the adjusted
    mean. A rung held at weight 0 shows its measured value with mult 1."""
    fe = pred["fe"]
    ship = pred["ship"]
    rows = []
    for m in MARKETS:
        coef = P["coefs"][m]
        mk = pred["markets"][m]
        for k in (1, 2, 3, 4, 5, 6):
            shipped = k in ship.get(m, [1])
            held = []
            for name in RUNGS[k][m]:
                x = fe["x"][m].get(name, 0.0)
                b = coef.get(name, 0.0) if shipped else 0.0
                title, ptxt, otxt, ev = _feature_text(name, x, fe, opp_abbr, P, pos)
                if name == "comp":
                    sc, neff, sw, used = pred["comps"][m]
                    ev = dict(ev, n=len(used), n_eff=round(neff, 1))
                ev = {kk: vv for kk, vv in ev.items() if vv is not None}
                if not shipped:
                    # compact: the channel, its measured value, the two sides' text; the
                    # kind only where it is a proxy (the row's own evidence is "modelled")
                    held.append({"feature": name, "value": _sig(x, 4), "player": ptxt or None,
                                 "opponent": otxt or None, "kind": "proxy" if ev.get("kind") == "proxy" else None})
                    continue
                mult = math.exp(b * x)
                delta = mk["mean"] * (1.0 - 1.0 / mult) if mult > 0 else 0.0
                rows.append({"factor": f"{_TEXT[m]}: {title}", "player": ptxt, "opponent": otxt,
                             "assessment": "favorable" if delta > 0.05 else ("unfavorable" if delta < -0.05 else "neutral"),
                             "effect": {"stat": STAT_NAME[m], "delta_mean": _sig(delta), "mult": _sig(mult),
                                        "weight": "shipped"},
                             "evidence": ev,
                             "detail": {"feature": name, "x": _sig(x), "coef": _sig(b), "rung": k}})
            if held:
                # a rung the held-out test did not ship: ONE row, its measured
                # channels in the detail table, nothing applied
                rows.append({"factor": f"{_TEXT[m]}: rung {k}, {RUNG_NAME[k]} -- measured, held at weight 0",
                             "player": "", "opponent": f"{len(held)} channel{'s' if len(held) != 1 else ''} measured",
                             "assessment": "neutral",
                             "effect": {"stat": STAT_NAME[m], "delta_mean": 0.0, "mult": 1.0,
                                        "weight": "0 (rung not shipped)"},
                             "evidence": {"kind": "modelled"},
                             "detail": {"feature": f"rung {k}", "x": 0.0, "coef": 0.0, "rung": k,
                                        "channels": [{kk: vv for kk, vv in h.items() if vv is not None} for h in held]}})
    return rows


_LABEL_ORDER = ("high_probability", "positive_ev", "positive_ev_robust", "strong_fit", "low_evidence")


def _labels(p, lo, hi, n_eff, side, value=None):
    """The line's labels. strong_fit is NOT prop_value's to decide: it is added
    here exactly when the defense terms favour a side (`side` "yes" / "no",
    from fit_side), and the line carries that side as fit_side."""
    try:
        import prop_value as PV
        if value is not None:
            got = PV.line_labels(value, strong_fit=False, n_eff=n_eff)
        else:
            got = PV.labels_from(p_side=p, width=hi - lo, n_eff=n_eff, strong_fit=False)
    except Exception as e:
        errlog.note("NBAM-labels", e)
        got = (["high_probability"] if p >= 0.7 else []) + (["low_evidence"] if n_eff is not None and n_eff < 8 else [])
    got = [g for g in got if g != "strong_fit"] + (["strong_fit"] if side else [])
    return [g for g in _LABEL_ORDER if g in got] + [g for g in got if g not in _LABEL_ORDER]


def player_payload(pid, name, team, opp, game_id, home, sn, book, P, ctx, hist, day, status, rank_tab,
                   kalshi_idx=None, event_key=None, min_minutes=None):
    """One player of the contract (scratchpad matchup_payload_contract.md)."""
    day_n = M._day(day)
    own_t, opp_t = book.team(team), book.team(opp)
    pos = sn["pos"]
    nets = (net_rating(own_t, P), net_rating(opp_t, P))
    own_tg = book.teams[team].tg if team in book.teams else 0
    mrow = minutes_row(sn, team, own_tg, pos, ctx, nets[0], nets[1])
    _ship, mm = _shipped(P)
    if min_minutes and pred_minutes(mrow, P["min_beta6"] if mm == "m6" else P["min_beta"], m6=(mm == "m6")) < min_minutes:
        return None
    pred = predict(sn, own_t, opp_t, P, home, ctx, hist, day_n, mrow)
    fe = pred["fe"]
    role = {k: {"value": _round(v["value"], 4), "raw": _round(v["raw"], 4), "unit": ROLE_UNIT.get(k, "share"),
                "kind": v["kind"], "n": v["n"], "n_unit": ROLE_N_UNIT.get(k, "attempts"),
                "basis": v["basis"]} for k, v in fe["role"].items()}
    for st_ in MARKETS + AUX:
        rr, den, own = fe["rates"][st_]
        role[f"rate_{STAT_NAME.get(st_, st_)}"] = {
            "value": _round(rr, 4), "unit": "per_min", "kind": "modelled", "n": _round(den, 1), "n_unit": "weighted minutes",
            "basis": f"{STAT_NAME.get(st_, st_)} per minute, own share {own:.0%} (the rest his position's league rate)"}
    role["on_floor_pace"] = {"value": _round(fe["his_pace"], 2), "unit": "poss_per_48", "kind": "observed",
                             "n": None, "basis": "possessions per 48 minutes while he is on the floor (stints)"}
    prof = {}
    for ch in PROFILE_CHANNELS:
        c = fe["chan"][f"def:{ch}"]
        r, pct = _rank(rank_tab, f"def:{ch}", c["ratio"])
        prof[ch] = {"value": _round(c["ratio"], 4), "unit": "x_league", "raw": _round(c["raw_ratio"], 4),
                    "recent": _round(c["recent_ratio"], 4),
                    "rate": _round(c["ratio"] * c["league"], 4) if c.get("league") else None,
                    "rate_unit": CONC_UNIT[ch], "kind": c["kind"], "n": c["n"], "n_unit": CONC_N_UNIT[ch],
                    "basis": c["basis"], "rank": r, "pct": pct}
    for ch in ("miss", "oreb", "three", "pace"):
        c = fe["chan"][f"off:{ch}"]
        r, pct = _rank(rank_tab, f"off:{ch}", c["ratio"])
        prof[f"offense_{ch}"] = {"value": _round(c["ratio"], 4), "unit": "x_league",
                                 "rate": _round(c["ratio"] * c["league"], 4) if c.get("league") else None,
                                 "rate_unit": CONC_UNIT[ch], "kind": c["kind"], "n": c["n"], "n_unit": CONC_N_UNIT[ch],
                                 "basis": f"{opp}'s own offense: " + _off_text(c["basis"]),
                                 "rank": r, "pct": pct}
    markets = []
    prior_h = [h for h in hist if h[0] < day_n]          # the order comparables() kept
    # the hit tables read ONLY games before the date (a past date's board must
    # not count that date or later; reviews 2026-10-23, O2 / F2)
    hist_sorted = sorted(prior_h, key=lambda h: h[0])
    for m in MARKETS + tuple(COMBOS) + ("dk",):
        vals = [_hist_stat(h[3], m) for h in hist_sorted]
        pvals = [_hist_stat(h[3], m) for h in prior_h]
        mk = pred["markets"][m]
        lines = []
        comp_m = pred["comps"].get(m if m in MARKETS else "pts")
        used = comp_m[3]
        sw = sum(u[0] for u in used)
        n_eff = comp_m[1]
        side = fit_side(mk.get("fit_mult"))
        for k in (DK_LINES if m == "dk" else LINES[m]):
            p, lo, hi = mk["sf"][k], mk["sf_lo"][k], mk["sf_hi"][k]
            lo, hi = min(lo, p), max(hi, p)
            hits_all = sum(1 for v in vals if v >= k)
            recent = vals[-10:]
            hw = sum(u[0] for u, v in zip(used, pvals) if v >= k)
            line = {"k": k, "p": _round(p, 4), "p_lo": _round(lo, 4), "p_hi": _round(hi, 4),
                    "hit_all": {"hits": hits_all, "n": len(vals)},
                    "hit_recent": {"hits": sum(1 for v in recent if v >= k), "n": len(recent)},
                    "hit_similar_def": {"hits_w": _round(hw / sw * n_eff if sw > 0 else 0.0, 2), "n_eff": _round(n_eff, 1)},
                    "market": None, "value": None}
            if kalshi_idx is not None and event_key and m != "dk":
                try:
                    import kalshi_nba_props as KP
                    import prop_value as PV
                    blk = KP.market_for_line(kalshi_idx, key=event_key, stat=STAT_NAME[m], player=name, k=k)
                    if blk:
                        line["market"] = blk
                        line["value"] = PV.line_value(p, blk, lo, hi)
                except Exception as e:
                    errlog.note("NBAM-market", e, path=f"{name}:{m}:{k}")
            line["labels"] = _labels(p, lo, hi, n_eff, side, line["value"])
            line["fit_side"] = side
            lines.append(line)
        markets.append({"stat": STAT_NAME[m], "baseline_mean": _round(mk["mean_base"], 2),
                        "baseline_source": "rolling (per-minute rate x projected minutes x pace; research/reports/nba_matchup.md)",
                        "adjusted_mean": _round(mk["mean"], 2),
                        "dist": {"family": "negbin_minutes_mixture" if m != "dk" else "negbin_on_floor_dk_minutes_mixture",
                                 "params": {"phi": mk["phi"], "kappa": mk["kappa"], "minutes": _round(pred["m_hat"], 1),
                                            "rate_raw": None if m in COMBOS or m == "dk" else _sig(fe["rates"][m][0]),
                                            "rate_adj": _sig(mk["rate"]), "intercept": None if m in COMBOS or m == "dk"
                                            else _sig(P["coefs"][m].get("_icpt", 0.0)),
                                            "minutes_nodes": len(pred["nodes"]),
                                            **({"sd": _round(mk["sd"], 2)} if m == "dk" else {})}},
                        "fit_multiplier": _sig(mk.get("fit_mult")), "lines": lines})
    comps = []
    sc, neff, sw, used = pred["comps"]["pts"]
    prior = [h for h in hist if h[0] < day_n]
    for (w, sim, meta), h in sorted(zip(used, prior), key=lambda t: -t[0][0])[:8]:
        d, oppx, st, gid = meta
        vec = h[5]["pts"]
        dims = COMP_DIMS["pts"]
        sd_ = P.get("comp_sd") or {}
        why = ", ".join(f"{dd.split(':')[1]} {math.exp(v * (sd_.get(dd) or 0.05)):.2f}x" for dd, v in zip(dims, vec)
                        if dd in ("def:rim", "def:three", "def:pace", "def:pull"))
        comps.append({"date": _day_iso(d), "opp": oppx, "similarity": round(sim, 3), "weight": round(w / sw, 4) if sw else 0.0,
                      "stats": dict(st, min=h[2]), "why": f"{oppx} then: {why}", "game_id": gid})
    notes = ["Defensive schemes (drop / switch / zone) are not observable in ESPN's feed; the profile is what the "
             "defense CONCEDED (shot mix, assisted share, free throws, offensive rebounds, early-clock shots, pace).",
             "Spot-up (catch-and-shoot), post-up and early-clock (transition) shares are proxies built from the "
             "play-by-play's shot descriptors and clock; pull-up, driving and cutting are the feed's own words."]
    if sn["cur"][M.PS["g"]] + sn["prev"][M.PS["g"]] < 10:
        notes.append(f"Only {int(sn['cur'][M.PS['g']] + sn['prev'][M.PS['g']])} games in two seasons: the rates lean on his position's league rates.")
    if ctx.get("who"):
        notes.append(f"Teammates ruled out and counted in rung 6: {len(ctx['who'])}.")
    return {"player_id": str(pid), "name": name, "team": team, "opp": opp, "game_id": str(game_id),
            "pos": ("G", "F", "C")[pos] if pos in (0, 1, 2) else None, "status": status, "role": role,
            "opponent_profile": prof, "factors": _factors(pred, P, opp, pos), "markets": markets,
            "comparables": comps, "notes": notes,
            "minutes": {"projected": _round(pred["m_hat"], 1), "model": pred["mm"],
                        "nodes": [round(pred["m_hat"] * q, 1) for q in pred["nodes"]]}}


def _day_iso(dn):
    import datetime
    return (datetime.date(2000, 1, 1) + datetime.timedelta(days=int(dn))).isoformat()


def _candidates(book, team, statuses, max_gap=10, min_games=3):
    t = book.teams.get(team)
    out = []
    for pid, p in book.players.items():
        if p.team != team or p.n_all < min_games or t is None:
            continue
        if t.tg - p.last_tg > max_gap and pid not in statuses:
            continue
        out.append(pid)
    return out


def board(date, games=None, statuses=None, state=None, kalshi_idx=None, with_market=True, min_minutes=18.0):
    """The matchup payload for a US date (scratchpad contract): every player
    of that date's games whose projected minutes reach `min_minutes`, the
    players ruled out listed with status 'out' and no markets. Never raises
    for a missing input: the payload says what is missing."""
    import time as _t
    t0 = _t.time()
    P = params()
    out = {"sport": "nba", "built_ts": _t.time(), "as_of": None, "provenance": [], "games": [], "players": [],
           "notes": [], "definitions": DEFINITIONS}
    if not P.get("coefs"):
        out["notes"].append("no committed fit (research/data/nba_matchup/) -- nothing to show")
        return out
    mt = (P.get("market_test") or {}).get("tight") or {}
    if mt.get("n"):
        b_ = mt.get("bets") or {}
        out["notes"].append(
            f"Held-out market test ({(P.get('market_test') or {}).get('season')}, {mt['n']:,} sampled Kalshi prop markets "
            f"with tight pre-tip books): Kalshi's mid scored log loss {mt['ll_mid']:.3f} against this model's "
            f"{mt['ll_p']:.3f}" + (f"; one-contract bets on the model's 3-point all-in edges returned "
                                     f"{b_['roi']:+.1%} over {b_['n']} bets (90% interval {b_['roi_ci90'][0]:+.1%} to "
                                     f"{b_['roi_ci90'][1]:+.1%})" if b_.get("n") else "")
            + ". A 'positive_ev' label is the model disagreeing with the market, not a proven edge.")
    season = D.season_of(date)
    state = state or load_book(season)
    if not state:
        out["notes"].append(f"no matchup book for {season} yet (the nba_matchup job has not run)")
        return out
    if state.get("through") and date <= state["through"]:
        # the book already holds that date's games: pricing it would read them
        out["as_of"] = f"games through {state.get('through')}"
        out["notes"].append(f"the matchup book already holds games through {state['through']}; a board for {date} "
                            "would read that date's own results, so nothing is priced (a past date needs a book "
                            "replayed to the day before)")
        return _finite(out)
    book = state["book"]
    book.roll(season)
    out["as_of"] = f"games through {state.get('through')}"
    out["provenance"] = [
        {"source": "ESPN game summaries (box score + play-by-play)",
         "covers": f"the {season - 1}-{season % 100:02d} and {season}-{(season + 1) % 100:02d} seasons to {state.get('through')}",
         "ts": state.get("ts") or state.get("built"), "licence": "ESPN public site API"},
        {"source": "research/data/nba_matchup (the fit)", "covers": "fit 2022-23 + 2023-24, validated 2024-25, tested 2025-26",
         "ts": P.get("built"), "licence": "this repo"}]
    try:
        games = games if games is not None else fetch_games(date)
    except Exception as e:
        errlog.note("NBAM-games", e, path=date)
        games = []
    if not games:
        out["notes"].append(f"no NBA games found for {date}")
        return out
    try:
        statuses = statuses if statuses is not None else fetch_statuses([g["game_id"] for g in games])
    except Exception as e:
        errlog.note("NBAM-injuries", e, path=date)
        statuses = {}
    if with_market and kalshi_idx is None:
        try:
            import kalshi_nba_props as KP
            kalshi_idx = KP.index()
        except Exception as e:
            errlog.note("NBAM-kalshi", e)
            kalshi_idx = None
    rank_tab = _rank_table(book, P, sorted({ab for ab in book.teams}))
    logs = state.get("logs") or {}
    out["games"] = [{"game_id": str(g["game_id"]), "home": g["home"], "away": g["away"], "start_ts": g.get("start_ts")}
                    for g in games]
    for g in games:
        ek = None
        if kalshi_idx is not None:
            try:
                import kalshi_nba_props as KP
                ek = KP.event_key(kalshi_idx, g["home"], g["away"])
            except Exception as e:
                errlog.note("NBAM-kalshi-key", e)
        for side, team, opp in ((0, g["home"], g["away"]), (1, g["away"], g["home"])):
            cands = _candidates(book, team, statuses)
            is_out = (lambda pid: (statuses.get(pid) or {}).get("value") == "out")
            ctx = team_context(book, team, date, is_out, P, book.team(team))
            for pid in cands:
                sn = book.player(pid, date)
                if sn is None:
                    continue
                st = statuses.get(pid) or {"value": "active" if statuses else "unknown",
                                           "source": "ESPN injury report (not listed)" if statuses else "no injury report read",
                                           "ts": None}
                name = sn.get("name") or str(pid)
                if st.get("value") == "out":
                    out["players"].append({"player_id": str(pid), "name": name, "team": team, "opp": opp,
                                           "game_id": str(g["game_id"]), "pos": None, "status": st, "role": {},
                                           "opponent_profile": {}, "factors": [], "markets": [], "comparables": [],
                                           "notes": ["ruled out: counted in his teammates' rung-6 context"]})
                    continue
                try:
                    pl = player_payload(pid, name, team, opp, g["game_id"], side == 0, sn, book, P, ctx,
                                        logs.get(pid) or [], date, st, rank_tab, kalshi_idx, ek, min_minutes)
                except Exception as e:
                    errlog.note("NBAM-player", e, path=f"{date}:{pid}")
                    continue
                if pl is not None:
                    out["players"].append(pl)
    out["timing_s"] = round(_t.time() - t0, 2)
    return _finite(out)


def dfs_projections(date, players, state=None, statuses=None, games=None):
    """DraftKings NBA projections for the DFS builder: for each pool player
    ({"name", "team", ...} as simulate.parse_dk_csv gives) -> {"proj", "sd",
    "p": {threshold: P(DK points >= threshold)}, "minutes", "source"} or None
    when he cannot be matched (name + team) or has no history. NEVER raises:
    any failure returns a list of None (ledgered NBAM-dfs)."""
    try:
        return _finite(_dfs(date, players, state, statuses, games))
    except Exception as e:
        errlog.note("NBAM-dfs", e, path=date)
        return [None] * len(players or [])


def _norm(n):
    try:
        import kalshi_nba_props as KP
        return KP.norm_name(n)
    except ImportError as e:
        errlog.note("NBAM-norm", e)
        import re
        import unicodedata
        s = unicodedata.normalize("NFKD", n or "").encode("ascii", "ignore").decode().lower()
        s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", " ", s)
        return " ".join(re.sub(r"[^a-z ]", " ", s).split())


# DraftKings' (and Kalshi's) club codes where ESPN's differ: DK writes PHO, UTA, WAS
_DK_TEAM = {"GSW": "GS", "NOP": "NO", "NYK": "NY", "SAS": "SA", "UTA": "UTAH", "WAS": "WSH", "PHO": "PHX"}


def _dfs(date, players, state, statuses, games):
    P = params()
    season = D.season_of(date)
    state = state or load_book(season)
    if not P.get("coefs") or not state:
        return [None] * len(players or [])
    if state.get("through") and date <= state["through"]:
        errlog.note("NBAM-dfs-past", msg=f"{date} is on or before the book's {state['through']}: not priced")
        return [None] * len(players or [])
    book = state["book"]
    book.roll(season)
    games = games if games is not None else fetch_games(date)
    opp_of, home_of, gid_of = {}, {}, {}
    for g in games:
        opp_of[g["home"]], opp_of[g["away"]] = g["away"], g["home"]
        home_of[g["home"]], home_of[g["away"]] = True, False
        gid_of[g["home"]] = gid_of[g["away"]] = g["game_id"]
    if statuses is None:
        try:
            statuses = fetch_statuses([g["game_id"] for g in games])
        except Exception as e:
            errlog.note("NBAM-injuries", e, path=date)
            statuses = {}
    idx = {}
    for pid, p in book.players.items():
        if p.team and p.name:
            idx.setdefault((_norm(p.name), p.team), pid)
    logs = state.get("logs") or {}
    ctx_cache = {}
    res = []
    for pl in players or []:
        team = _DK_TEAM.get((pl.get("team") or "").upper(), (pl.get("team") or "").upper())
        pid = idx.get((_norm(pl.get("name")), team))
        if pid is None or team not in opp_of:
            res.append(None)
            continue
        sn = book.player(pid, date)
        if sn is None:
            res.append(None)
            continue
        opp = opp_of[team]
        if team not in ctx_cache:
            ctx_cache[team] = team_context(book, team, date, lambda x: (statuses.get(x) or {}).get("value") == "out",
                                           P, book.team(team))
        ctx = ctx_cache[team]
        own_t, opp_t = book.team(team), book.team(opp)
        nets = (net_rating(own_t, P), net_rating(opp_t, P))
        mrow = minutes_row(sn, team, book.teams[team].tg, sn["pos"], ctx, nets[0], nets[1])
        pred = predict(sn, own_t, opp_t, P, home_of[team], ctx, logs.get(pid) or [], M._day(date), mrow)
        dk = pred["markets"]["dk"]
        # listed -> his status; not listed on a report that was read -> active
        st = (statuses.get(pid) or {}).get("value") or ("active" if statuses else "unknown")
        res.append({"proj": round(dk["mean"], 2), "sd": round(dk["sd"], 2),
                    "q": _quantiles(dk["sf"], (0.1, 0.25, 0.5, 0.75, 0.9)),
                    "p": {k: round(dk["sf"][k], 4) for k in DK_LINES}, "minutes": round(pred["m_hat"], 1),
                    "status": st, "player_id": str(pid),
                    "replaces_avg": bool((P.get("shipped") or {}).get("dk_fp_replaces_avg")),
                    "source": "nba_matchup dk_fp (components x minutes mixture; research/reports/nba_matchup.md)"})
    return res


def _quantiles(sf, qs):
    """{"10": x, ...}: the smallest whole DK point x with P(floor(DK) <= x) >= q
    (+0.375, the mean fraction a floor drops), from a survival table."""
    out = {}
    for q in qs:
        x = len(sf) - 1
        for k in range(len(sf) - 1):
            if 1.0 - sf[k + 1] >= q:
                x = k
                break
        out[str(int(round(q * 100)))] = round(x + 0.375, 2)
    return out


def _board_blob(raw):
    import pickle
    job = pickle.loads(raw)
    return pickle.dumps(board(job["date"], with_market=job.get("with_market", True)), protocol=pickle.HIGHEST_PROTOCOL)


def board_isolated(date, with_market=True, timeout=120):
    """board() in a niced child process -- the placement the server needs
    (nothing CPU-bound in a gunicorn worker on a one-core quota; ~5-8 s of
    pure Python for a 12-game slate, ~12 MB of state loaded). The caller
    holds deep_cache.HEAVY_BUILD around it. A failed child returns a payload
    that says so (ledgered NBAM-child), never an exception."""
    import pickle
    import subprocess
    import sys
    try:
        out = subprocess.run(
            [sys.executable, "-c", "import os; os.nice(10)\n"
             "import sys, nba_matchup; sys.stdout.buffer.write(nba_matchup._board_blob(sys.stdin.buffer.read()))"],
            input=pickle.dumps({"date": date, "with_market": with_market}),
            cwd=os.path.dirname(os.path.abspath(__file__)), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=timeout)
        if out.returncode == 0 and out.stdout:
            return pickle.loads(out.stdout)
        errlog.note("NBAM-child", msg=f"child rc={out.returncode}")
    except Exception as e:
        errlog.note("NBAM-child", e)
    return {"sport": "nba", "built_ts": None, "as_of": None, "provenance": [], "games": [], "players": [],
            "notes": ["the matchup board's build failed this time; see the error ledger (NBAM-child)"]}


def run_job(today=None, log=None):
    """The `nba_matchup` deep job (PC first; on the server under
    deep_cache.HEAVY_BUILD in a niced child): last season's cut games once
    (a backfill of ~1,230 summaries, ~12 minutes serial, when fewer than
    1,150 are held), this season's new games (nba_matchup_data.update), then
    the replay (rebuild). Returns a small summary -- the job's freshness
    marker -- or None out of season. The data live under their own deep keys
    (nba_matchup_<season>, nba_matchup_book_<season>)."""
    import time as _t
    try:
        import pro_sim
        if not pro_sim.in_season("nba"):
            return None
    except Exception as e:
        errlog.note("NBAM-season", e)
    import datetime
    today = today or datetime.date.today()
    season = D.season_of(today.isoformat())
    prev = D.load_state(season - 1)
    if len(prev.get("games") or {}) < 1150:
        D.update(season - 1, today=today, max_fetch=1500, log=log)
    D.update(season, today=today, log=log)
    st = rebuild(season, log=log)
    if not st:
        return None
    return {"season": season, "through": st["through"], "n_games": st["n_games"], "built": _t.time(),
            "secs": st["secs"]}


def run_job_isolated(timeout=3600):
    """run_job() in a niced child (the server's placement: the replay is ~3 s
    of CPU and ~250 MB at its peak, measured; nothing CPU-bound runs in a
    gunicorn worker). The caller holds deep_cache.HEAVY_BUILD. Returns the
    job's summary or None (ledgered NBAM-job-child)."""
    import pickle
    import subprocess
    import sys
    try:
        out = subprocess.run(
            [sys.executable, "-c", "import os; os.nice(10)\n"
             "import sys, pickle, nba_matchup; sys.stdout.buffer.write(pickle.dumps(nba_matchup.run_job()))"],
            cwd=os.path.dirname(os.path.abspath(__file__)), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=timeout)
        if out.returncode == 0 and out.stdout:
            return pickle.loads(out.stdout)
        errlog.note("NBAM-job-child", msg=f"child rc={out.returncode}")
    except Exception as e:
        errlog.note("NBAM-job-child", e)
    return None
