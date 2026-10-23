"""NFL offense-vs-defense matchup model: production inference (pure Python,
no numpy -- the web server imports it).

THE QUESTION (the owner's): how well does each player's role, usage and
production meet THIS opponent's actual defensive tendencies, and how much
should that move each prop line's probability and each DFS projection?
Answered only with what the data measurably supports:
research/reports/nfl_matchup_model.md holds the pre-registered ladder, its
validation (fit 2023-2024, validate 2025) and the one-shot 2026 test, and
research/data/nfl_matchup_model/coefs.json carries the verdict. A feature
group that did not beat the rung below it out of sample ships at WEIGHT 0:
its coefficients are absent from the shipped model, and the board prints its
factors with effect 0 (information, never decoration).

THE MODEL. Every market's mean is Sleeper's line times exp(beta . x): a
ridge-penalised Poisson regression on the log of actual over Sleeper, one per
market (targets, receptions, receiving yards, carries, rushing yards, pass
attempts, completions, passing yards, passing TDs, interceptions), each with
its own coefficients -- so a 5+ receptions candidate can be a poor 80+ yards
candidate. Opportunity x efficiency is kept STRUCTURAL: a target-rate feature
may enter targets, receptions and yards; a catch-rate feature receptions and
yards; a yards-per-catch feature yards only (`MARKET_FEATURES`). Receiving and
rushing touchdowns move with their volume market's factor only.

THE FEATURES (`features`), each a SHRUNK deviation from the league at the
same cutoff, its shrinkage n / (n + k) with k the measured split-half k
(research/data/nfl_matchup/reliability.json; `K`). Participation-derived
signals (coverage, man / zone, pressure, the on-field target rates) come from
PRIOR SEASONS ONLY for every row of every season: nflverse publishes the
season's participation after the postseason, so production never has the
current season's -- and a model trained on in-season coverage would have
learnt a signal it never gets to read. pbp- and FTN-derived signals (EPA,
targets by depth and location, run defense, blitz, box) use the current
season before the cutoff plus the prior seasons at DECAY.

Two bugs in nfl_matchup_profiles are worked around here, not inherited
(report section 2): its tgt_per_db_vs_man / vs_zone / per-coverage rates
divide targets over ALL of a player's games by on-field dropbacks over his
CURRENT team's games only (a player who changed teams reads 2-8x high:
Nelson Agholor 2023 week 5, 0.46 / 0.91 against an overall 0.15), and its
overall tgt_per_db counts 2026 targets over 2024-25 dropbacks (no 2026
participation), climbing 0.18 -> 0.33 across 2026 weeks 1-6. The split rates
here are built from the prior seasons' tallies only, numerator and
denominator over the same games. Its pressure / sack / EPA-per-dropback /
time-to-throw shrinkage uses the blitz k (90.7) and its box shares the
personnel k (48.1); this module reads the raw value and n and shrinks with
the measured k.
"""
import json
import math
import os
import time

import errlog

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(HERE, "research", "data", "nfl_matchup_model")
COEF_PATH = os.path.join(MODEL_DIR, "coefs.json")
DEFHIST_PATH = os.path.join(MODEL_DIR, "def_history.json")
DEFHIST_KEY = "nfl_matchup_defhist_{season}"
SCHEMA = 1

POS = ("QB", "RB", "WR", "TE")
DECAY = 0.5          # nfl_matchup_profiles.DECAY: a season back counts half
BACK = 2

# Measured split-half k (research/data/nfl_matchup/reliability.json, pooled
# 2018-2025, odd vs even weeks): a unit with n denominator units is believed
# n / (n + k). None = not measurably above zero (the feature is not built).
K = {
    "def_man": 44.7, "def_single_high": 70.3, "def_two_high": 77.2, "def_blitz": 90.7,
    "def_pressure": 437.0, "def_sack": 621.1, "def_epa_db": 1039.8, "def_light_box": 163.2,
    "def_heavy_box": 167.4, "def_run_ypc": 376.2, "def_run_success": 468.9,
    "def_pass_rate": 376.4,           # the OFFENSE pass rate's k (off.pass_rate): a defense's pass rate faced is unmeasured
    "def_catch_all": 354.4,           # the WR catch-allowed k, the nearest measured (overall catch allowed is unmeasured)
    "def_ypt_all": 865.5,             # def_allow_target pooled
    "def_tgt_rate": {"WR": 1441.7, "TE": 5078.8, "RB": 595.2},
    "def_catch_pos": {"WR": 354.4, "TE": 627.5, "RB": 650.2},
    "def_ypt_pos": {"WR": 914.2, "TE": None, "RB": 776.7},
    "def_tgt_pg": {"WR": 27.4, "TE": 84.6, "RB": 13.4},
    "def_adj_ppr": {"WR": 47.2, "TE": 43.8, "RB": 16.7},
    "def_qb_pass_yds_pg": 23.1,
    "def_depth": 865.5,               # by-depth / by-location allowances: unmeasured, def_allow_target's k
    "pl_mz_split": 100.9,             # player target rate vs man minus vs zone (n = the smaller side)
    "pl_shell_split": 100.9,          # the shell split is unmeasured: the man-zone split's k is borrowed
    "pl_mid_share": 46.6, "pl_depth_mix": 18.1, "pl_ypc": 189.0,
    "qb_epa_press": 42.0, "qb_epa_nopress": 236.8, "qb_epa_blitz": 174.7, "qb_epa_noblitz": 244.7,
    "off_proe": 176.4,
}

# Where each k comes from: the split-half signal it was measured on (a block of
# reliability.json's "pooled"), or a kind-level k (its "k" block) where the
# signal itself has no split-half measurement.
K_SIGNAL = {"def_man": "def.man", "def_single_high": "def.single_high", "def_two_high": "def.two_high",
            "def_blitz": "def.blitz", "def_pressure": "def.pressure", "def_sack": "def.sack", "def_epa_db": "def.epa_db",
            "def_light_box": "def.light_box", "def_heavy_box": "def.heavy_box", "def_run_ypc": "def.run_ypc",
            "def_run_success": "def.run_success", "def_pass_rate": "off.pass_rate", "def_catch_all": "def.catch_WR",
            "def_ypt_all": "k:def_allow_target", "def_depth": "k:def_allow_target",
            "def_tgt_rate": {p: f"def.tgt_per_db_{p}" for p in ("WR", "TE", "RB")},
            "def_catch_pos": {p: f"def.catch_{p}" for p in ("WR", "TE", "RB")},
            "def_ypt_pos": {p: f"def.ypt_{p}" for p in ("WR", "TE", "RB")},
            "def_tgt_pg": {p: f"def.tgt_pg_{p}" for p in ("WR", "TE", "RB")},
            "def_adj_ppr": {p: f"def.adj_ppr_{p}" for p in ("WR", "TE", "RB")},
            "def_qb_pass_yds_pg": "def.pass_yds_pg_QB", "pl_mz_split": "pl.mz_tgt_rate_split",
            "pl_shell_split": "pl.mz_tgt_rate_split", "pl_mid_share": "pl.mid_share_proxy",
            "pl_depth_mix": "pl.route_vertical", "pl_ypc": "pl.ypc", "qb_epa_press": "qb.epa_db_pressure",
            "qb_epa_nopress": "qb.epa_db_no_pressure", "qb_epa_blitz": "qb.epa_db_blitz",
            "qb_epa_noblitz": "qb.epa_db_no_blitz", "off_proe": "off.proe"}
K_PATH = os.path.join(HERE, "research", "data", "nfl_matchup", "reliability.json")          # 2018-2025: production
K_FIT_ONLY_PATH = os.path.join(MODEL_DIR, "k_fit_only.json")                                 # 2018-2024: the 2025 validation


def _k_lookup(table, ref):
    if ref.startswith("k:"):
        return (table.get("k") or {}).get(ref[2:])
    return ((table.get("pooled") or {}).get(ref) or {}).get("k")


def load_k(path):
    """K rebuilt from a reliability table (signals missing from it keep the
    constants above, which are the 2018-2025 table's own values). A signal
    measured at r <= 0.02 reads None: the feature is not built."""
    try:
        with open(path, encoding="utf-8") as fh:
            table = json.load(fh)
    except (OSError, ValueError) as e:
        errlog.note("NFLM-k", e)
        return dict(K), "constants (table unreadable)"
    out = dict(K)
    for name, ref in K_SIGNAL.items():
        if isinstance(ref, dict):
            out[name] = {p: _k_lookup(table, r) for p, r in ref.items()}
        else:
            v = _k_lookup(table, ref)
            if v is not None:
                out[name] = v
    return out, table.get("source") or os.path.basename(path)


# RESEARCH: NFLM_K=fit_only builds the features with the 2018-2024 k (the 2025
# validation must not read a k that saw 2025); production reads 2018-2025.
K_MODE = "fit_only" if os.environ.get("NFLM_K") == "fit_only" else "pooled"
K, K_SOURCE = load_k(K_FIT_ONLY_PATH if K_MODE == "fit_only" else K_PATH)

MARKETS = ("tgt", "rec", "rec_yd", "car", "rush_yd", "patt", "pcmp", "pass_yd", "ptd", "int")
# the Sleeper field each market's baseline is, and the official box field it settles on
SLEEPER_FIELD = {"tgt": "rec_tgt", "rec": "rec", "rec_yd": "rec_yd", "car": "rush_att", "rush_yd": "rush_yd",
                 "patt": "pass_att", "pcmp": "pass_cmp", "pass_yd": "pass_yd", "ptd": "pass_td", "int": "pass_int"}
RECV = ("tgt", "rec", "rec_yd")
RUSH = ("car", "rush_yd")
PASS = ("patt", "pcmp", "pass_yd", "ptd", "int")

# The ladder (the owner's, plus the level recalibration and the team-volume
# rung the model description asks for). Order matters only as the sequence in
# which each group is judged against everything accepted before it.
GROUPS = ("R", "V", "D2", "D3", "D4", "D5", "D6", "D7")
GROUP_LABEL = {"R": "Sleeper level recalibration (per position)", "V": "team volume and game script",
               "D2": "overall defense", "D3": "position-specific defense",
               "D4": "location / depth proxy vs positional weakness", "D5": "scheme vs scheme (coverage, pressure, box)",
               "D6": "comparable-opponent history", "D7": "full interaction (position x feature)"}

# Which features may enter which market, by group (STRUCTURAL: opportunity
# features reach every receiving market, catch features receptions and yards,
# per-catch features yards only). "R" is an intercept per position; D7 adds
# feature x position for the features already accepted in that market.
MARKET_FEATURES = {
    "V": {m: ("margin", "total_c", "proe") for m in MARKETS},
    "D2": {"tgt": ("pass_epa", "run_ypc", "pass_funnel"),
           "rec": ("pass_epa", "run_ypc", "pass_funnel", "catch_all"),
           "rec_yd": ("pass_epa", "run_ypc", "pass_funnel", "catch_all", "ypt_all"),
           "car": ("run_ypc", "run_succ", "pass_epa", "pass_funnel"),
           "rush_yd": ("run_ypc", "run_succ", "pass_epa", "pass_funnel"),
           "patt": ("pass_epa", "run_ypc", "pass_funnel", "sack"),
           "pcmp": ("pass_epa", "run_ypc", "pass_funnel", "sack", "catch_all"),
           "pass_yd": ("pass_epa", "run_ypc", "pass_funnel", "sack", "catch_all", "ypt_all"),
           "ptd": ("pass_epa", "pass_funnel"),
           "int": ("pass_epa", "sack")},
    "D3": {"tgt": ("tgt_rate_pos", "tgt_pg_pos"),
           "rec": ("tgt_rate_pos", "tgt_pg_pos", "catch_pos"),
           "rec_yd": ("tgt_rate_pos", "tgt_pg_pos", "catch_pos", "ypt_pos", "recyds_pg_pos"),
           "car": ("rushyds_pg_rb",), "rush_yd": ("rushyds_pg_rb",),
           "patt": ("passyds_pg_qb",), "pcmp": ("passyds_pg_qb",), "pass_yd": ("passyds_pg_qb",),
           "ptd": ("passyds_pg_qb",), "int": ()},
    "D4": {"tgt": ("loc_tgt", "deep_tgt"),
           "rec": ("loc_tgt", "deep_tgt", "depth_catch"),
           "rec_yd": ("loc_tgt", "deep_tgt", "depth_catch", "depth_ypt", "loc_ypt"),
           "car": (), "rush_yd": (), "patt": (), "pcmp": (), "pass_yd": (), "ptd": (), "int": ()},
    "D5": {"tgt": ("cov_mz", "cov_shell", "blitz_rb", "blitz_te"),
           "rec": ("cov_mz", "cov_shell", "blitz_rb", "blitz_te", "press_catch"),
           "rec_yd": ("cov_mz", "cov_shell", "blitz_rb", "blitz_te", "press_catch"),
           "car": ("light_box", "heavy_box"),
           "rush_yd": ("light_box", "heavy_box", "box_ypc"),
           "patt": ("press_qb", "blitz_qb", "pressure", "blitz"),
           "pcmp": ("press_qb", "blitz_qb", "pressure", "blitz"),
           "pass_yd": ("press_qb", "blitz_qb", "pressure", "blitz"),
           "ptd": ("press_qb", "blitz_qb", "pressure", "blitz"),
           "int": ("press_qb", "blitz_qb", "pressure", "blitz")},
    "D6": {m: ("comp_" + m,) for m in MARKETS},
}
# The comparables' per-game stat for each market (nfl_matchup_profiles' G_ line
# index), and the smoothing count in the ratio of means.
COMP_STAT = {"tgt": ("G_TGT", 0.5), "rec": ("G_REC", 0.5), "rec_yd": ("G_RECY", 5.0), "car": ("G_CAR", 0.5),
             "rush_yd": ("G_RUSHY", 5.0), "patt": ("G_PATT", 2.0), "pcmp": ("G_PCMP", 2.0),
             "pass_yd": ("G_PASSY", 20.0), "ptd": ("G_PASSTD", 0.3), "int": ("G_INT", 0.3)}
COMP_K = 10.0         # comparables shrink by n_eff / (n_eff + COMP_K) -- a convention, pre-registered
DEF_VEC = ("man", "sh_frac", "blitz", "pressure", "light_box", "pass_epa", "run_ypc", "pass_funnel")
DEF_VEC_LABEL = {"man": "man share", "sh_frac": "single-high share of shells", "blitz": "blitz rate",
                 "pressure": "pressure rate", "light_box": "light-box share", "pass_epa": "EPA/dropback allowed",
                 "run_ypc": "YPC allowed", "pass_funnel": "pass rate faced"}
SIM_H2 = len(DEF_VEC) / 2.0   # similarity exp(-d^2 / (2 h^2)) with h^2 = dim / 2 (pre-registered)

# Kalshi's ladders (the event is stat >= k) and the DFS points thresholds the
# validation scores; the board prices whatever rungs Kalshi actually lists.
THRESHOLDS = {"tgt": (3, 5, 7, 9), "rec": (2, 3, 4, 5, 6, 7), "rec_yd": (15, 25, 40, 50, 60, 75, 100),
              "car": (5, 10, 15, 20), "rush_yd": (15, 25, 40, 50, 60, 75, 100),
              "patt": (25, 30, 35, 40), "pcmp": (15, 20, 25), "pass_yd": (150, 175, 200, 225, 250, 275, 300),
              "ptd": (1, 2, 3), "int": (1, 2), "td": (1, 2), "dk_fp": (5, 10, 15, 20, 25, 30)}

# DraftKings NFL Classic scoring (dk_scoring.NFL_OFF; DraftKings' rules page,
# draftkings.com/help/rules/nfl): passing yard 0.04, passing TD 4, interception
# -1, 300+ passing yards +3; rushing / receiving yard 0.1, rushing / receiving
# TD 6, 100+ rushing yards +3, 100+ receiving yards +3; reception 1; lost
# fumble -1; two-point conversion 2.
DK = {"pass_yd": 0.04, "pass_td": 4.0, "int": -1.0, "pass_300": 3.0, "rush_yd": 0.1, "rush_td": 6.0,
      "rush_100": 3.0, "rec": 1.0, "rec_yd": 0.1, "rec_td": 6.0, "rec_100": 3.0, "fum_lost": -1.0, "two_pt": 2.0}


def dk_points(line):
    """DraftKings Classic points for one stat line {pass_yd, pass_td, int,
    rush_yd, rush_td, rec, rec_yd, rec_td, fum_lost, two_pt}, bonuses
    included (each at most once)."""
    g = lambda k: float(line.get(k) or 0.0)
    pts = (DK["pass_yd"] * g("pass_yd") + DK["pass_td"] * g("pass_td") + DK["int"] * g("int")
           + DK["rush_yd"] * g("rush_yd") + DK["rush_td"] * g("rush_td") + DK["rec"] * g("rec")
           + DK["rec_yd"] * g("rec_yd") + DK["rec_td"] * g("rec_td") + DK["fum_lost"] * g("fum_lost")
           + DK["two_pt"] * g("two_pt"))
    if g("pass_yd") >= 300:
        pts += DK["pass_300"]
    if g("rush_yd") >= 100:
        pts += DK["rush_100"]
    if g("rec_yd") >= 100:
        pts += DK["rec_100"]
    return pts


# ---- small numerics ------------------------------------------------------------
def _num(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def shrink(v, n, prior, k, log=False):
    """(n/(n+k)) x the deviation of v from prior (log ratio when `log`), and
    the reliability n/(n+k). Anything missing -> (0.0, 0.0): no evidence moves
    nothing."""
    v, n, prior = _num(v), _num(n), _num(prior)
    if v is None or prior is None or not n or n <= 0 or k is None:
        return 0.0, 0.0
    rel = n / (n + k)
    if log:
        if v <= 0 or prior <= 0:
            return 0.0, rel
        return rel * math.log(v / prior), rel
    return rel * (v - prior), rel


def _v(blk):
    return None if not isinstance(blk, dict) else blk.get("v")


def _n(blk):
    return 0.0 if not isinstance(blk, dict) else (blk.get("n") or 0.0)


# ---- the as-of context ----------------------------------------------------------
class Ctx:
    """The stores and the cached as-of pieces the features read. `stores` is
    {season: nfl_matchup_data store}; positions {gsis: pos}."""

    def __init__(self, stores, positions):
        import nfl_matchup_profiles as P
        self.P = P
        self.stores = {int(s): st for s, st in (stores or {}).items() if st}
        self.pos = positions or {}
        self._full_t = {}     # season -> Tallies of the whole season
        self._snap = {}       # (season, week) -> Snapshot (current weeks < week + priors)
        self._prior = {}      # season -> Snapshot of the prior seasons only
        self._def = {}        # (season, week, team) -> defense block
        self._lg = {}         # (season, week) -> league extras
        self._qb = {}
        self._split = {}      # season -> {gsis: split sums over the prior seasons}
        self._cur = {}        # (season, week) -> Tallies of the season's weeks < week

    def full_tallies(self, season):
        T = self._full_t.get(season)
        if T is None and season in self.stores:
            T = self._full_t[season] = self.P.season_tallies(self.stores[season], self.pos)
        return T

    def priors(self, season):
        return [T for T in (self.full_tallies(s) for s in range(season - BACK, season)) if T is not None]

    def current(self, season, week):
        key = (season, week)
        if key not in self._cur:
            cur = self.stores.get(season)
            self._cur[key] = self.P.season_tallies(cur, self.pos, max_week=week) if cur else None
        return self._cur[key]

    def snap(self, season, week):
        key = (season, week)
        S = self._snap.get(key)
        if S is None:
            S = self._snap[key] = self.P.Snapshot(season, week, self.current(season, week), self.priors(season))
        return S

    def prior_snap(self, season):
        S = self._prior.get(season)
        if S is None:
            S = self._prior[season] = self.P.Snapshot(season, 1, None, self.priors(season))
        return S

    def drop(self, season, week):
        """Free a week's snapshot (research loops walk weeks in order)."""
        self._snap.pop((season, week), None)
        self._cur.pop((season, week), None)
        for k in [k for k in self._def if k[:2] == (season, week)]:
            self._def.pop(k, None)
        self._lg.pop((season, week), None)


def league_extras(ctx, season, week):
    """League figures at the cutoff the profiles do not carry: target rate per
    dropback by position, per-depth and per-location YPT / catch, location and
    deep target shares, per-game position allowances."""
    key = (season, week)
    got = ctx._lg.get(key)
    if got is not None:
        return got
    P, D = ctx.P, __import__("nfl_matchup_data")
    S = ctx.snap(season, week)
    L = S.league()
    tgt = L["tgt"]
    db_all = P._agg(L["cov_db"], lambda k: True, 12)[0]
    out = {"tgt_rate": {}, "depth": {}, "loc": {}}
    for pos in ("WR", "TE", "RB"):
        t = P._agg(tgt, lambda k, p=pos: k[2] == p, 15)[0]
        out["tgt_rate"][pos] = t / db_all if db_all else None
    n_all = P._agg(tgt, lambda k: True, 15)
    air_n = n_all[13]
    for d in range(4):
        b = P._agg(tgt, lambda k, d=d: k[3] == d, 15)
        out["depth"][D.DEPTH[d]] = {"share": b[0] / air_n if air_n else None,
                                    "ypt": b[2] / b[0] if b[0] else None, "catch": b[1] / b[0] if b[0] else None}
    side_n = P._agg(tgt, lambda k: k[4] > 0, 15)[0]
    for s in range(1, 4):
        b = P._agg(tgt, lambda k, s=s: k[4] == s, 15)
        out["loc"][D.SIDE[s]] = {"share": b[0] / side_n if side_n else None, "ypt": b[2] / b[0] if b[0] else None,
                                 "n": b[0]}
    lo = out["loc"]
    nl, nr = lo["left"]["n"], lo["right"]["n"]
    out["loc"]["outside"] = {"ypt": ((lo["left"]["ypt"] or 0) * nl + (lo["right"]["ypt"] or 0) * nr) / (nl + nr) if (nl + nr) else None}
    pers = L["pers"]
    pa = P._agg(pers, lambda k: True, 2)
    out["pass_rate"] = pa[1] / pa[0] if pa[0] else None
    ctx._lg[key] = out
    return out


def defense_block(ctx, season, week, team):
    """The opponent's shrunk deviations from the league at the cutoff, each
    {"x", "v", "n", "prior", "k", "rel", "src"}. Participation fields read the
    prior seasons only (production never has the current season's)."""
    key = (season, week, team)
    got = ctx._def.get(key)
    if got is not None:
        return got
    P = ctx.P
    S, Sp = ctx.snap(season, week), ctx.prior_snap(season)
    L, Lp = S.league(), Sp.league()
    LX = league_extras(ctx, season, week)
    d = S.defense(team)
    dp = Sp.defense(team)
    out = {}

    def put(name, v, n, prior, k, log=False, src="in-season + prior seasons"):
        x, rel = shrink(v, n, prior, k, log)
        out[name] = {"x": x, "v": v, "n": n, "prior": prior, "k": k, "rel": rel, "src": src}

    pr, prp = d["pressure"], dp["pressure"]
    put("pass_epa", _v(pr["epa_db"]), _n(pr["epa_db"]), L["epa_db"], K["def_epa_db"])
    put("sack", _v(pr["sack"]), _n(pr["sack"]), L["sack"], K["def_sack"])
    put("blitz", _v(pr["blitz"]), _n(pr["blitz"]), L["blitz"], K["def_blitz"])
    put("pressure", _v(prp["pressure"]), _n(prp["pressure"]), Lp["pressure"], K["def_pressure"],
        src="prior seasons only (participation)")
    run = d["run"]
    put("run_ypc", _v(run["ypc"]), run["carries"], L["run"]["ypc"], K["def_run_ypc"], log=True)
    put("run_succ", _v(run["success"]), run["carries"], L["run"]["success"], K["def_run_success"])
    bx = d["box"]
    put("light_box", _v(bx["light"]), _n(bx["light"]), L["light_box"], K["def_light_box"])
    put("heavy_box", _v(bx["heavy"]), _n(bx["heavy"]), L["heavy_box"], K["def_heavy_box"])
    cv = dp["coverage"]
    put("man", _v(cv["man"]), _n(cv["man"]), Lp["man"], K["def_man"], src="prior seasons only (participation)")
    sh, th = _v(cv["single_high"]), _v(cv["two_high"])
    lsh = sum(Lp["cov"].get(c) or 0 for c in ("COVER_1", "COVER_3"))
    lth = sum(Lp["cov"].get(c) or 0 for c in ("COVER_2", "COVER_4", "COVER_6", "2_MAN"))
    put("sh_frac", (sh / (sh + th)) if (sh is not None and th is not None and sh + th > 0) else None,
        cv.get("dropbacks_charted"), (lsh / (lsh + lth)) if (lsh + lth) else None, K["def_single_high"],
        src="prior seasons only (participation)")
    pers = P._merge(S.tallies, lambda T: (T.dfn.get(team) or {}).get("pers"))
    pa = [sum(v[0] for v in pers.values()), sum(v[1] for v in pers.values())]
    put("pass_funnel", pa[1] / pa[0] if pa[0] else None, pa[0], LX["pass_rate"], K["def_pass_rate"])
    al = d["allowed"]
    lt = L["target"]
    put("catch_all", _v(al["all"]["catch"]), al["all"]["targets"], lt.get("catch"), K["def_catch_all"], log=True)
    put("ypt_all", _v(al["all"]["ypt"]), al["all"]["targets"], lt.get("ypt"), K["def_ypt_all"], log=True)
    dbn = pr["dropbacks"]
    for pos in ("WR", "TE", "RB"):
        blk = al["per_pos"][pos]
        lpz = L["pos"][pos]
        put(f"tgt_rate_{pos}", blk["targets"] / dbn if dbn else None, dbn, LX["tgt_rate"][pos],
            K["def_tgt_rate"][pos], log=True)
        put(f"catch_{pos}", _v(blk["catch"]), blk["targets"], lpz.get("catch"), K["def_catch_pos"][pos], log=True)
        put(f"ypt_{pos}", _v(blk["ypt"]), blk["targets"], lpz.get("ypt"), K["def_ypt_pos"][pos], log=True)
        fa = d["fantasy_allowed"][pos]
        adj = fa.get("opp_adjusted") or {}
        lg = L["pos_game"][pos]
        g = adj.get("games") or 0.0
        t_adj = _v(adj.get("tgt"))
        y_adj = _v(adj.get("rec_yds"))
        # opponent-adjusted per-game allowance as a fraction of the league's per-game figure
        put(f"tgt_pg_{pos}", (1.0 + t_adj / lg["tgt"]) if (t_adj is not None and lg.get("tgt")) else None, g, 1.0,
            K["def_tgt_pg"][pos], log=True)
        put(f"recyds_pg_{pos}", (1.0 + y_adj / lg["rec_yds"]) if (y_adj is not None and lg.get("rec_yds")) else None, g,
            1.0, K["def_adj_ppr"][pos], log=True)
    fr = d["fantasy_allowed"]["RB"].get("opp_adjusted") or {}
    lr = L["pos_game"]["RB"].get("rush_yds")
    ry = _v(fr.get("rush_yds"))
    put("rushyds_pg_rb", (1.0 + ry / lr) if (ry is not None and lr) else None, fr.get("games") or 0.0, 1.0,
        K["def_adj_ppr"]["RB"], log=True)
    fq = d["fantasy_allowed"]["QB"].get("opp_adjusted") or {}
    lq = L["pos_game"]["QB"].get("pass_yds")
    qy = _v(fq.get("pass_yds"))
    put("passyds_pg_qb", (1.0 + qy / lq) if (qy is not None and lq) else None, fq.get("games") or 0.0, 1.0,
        K["def_qb_pass_yds_pg"], log=True)
    for dd in ("behind", "short", "intermediate", "deep"):
        blk = al["per_depth"][dd]
        put(f"ypt_{dd}", _v(blk["ypt"]), blk["targets"], LX["depth"][dd]["ypt"], K["def_depth"], log=True)
        put(f"catch_{dd}", _v(blk["catch"]), blk["targets"], LX["depth"][dd]["catch"], K["def_depth"], log=True)
    pl_ = al["per_location"]
    nl, nm, nr = pl_["left"]["targets"], pl_["middle"]["targets"], pl_["right"]["targets"]
    put("ypt_mid", _v(pl_["middle"]["ypt"]), nm, LX["loc"]["middle"]["ypt"], K["def_depth"], log=True)
    yl, yr = _v(pl_["left"]["ypt"]), _v(pl_["right"]["ypt"])
    out_ypt = ((yl or 0) * nl + (yr or 0) * nr) / (nl + nr) if (nl + nr) and yl is not None and yr is not None else None
    put("ypt_out", out_ypt, nl + nr, LX["loc"]["outside"]["ypt"], K["def_depth"], log=True)
    side_n = nl + nm + nr
    put("mid_share", nm / side_n if side_n else None, side_n, LX["loc"]["middle"]["share"], K["def_depth"])
    dpt = al["per_depth"]
    dn = sum(dpt[x]["targets"] for x in ("behind", "short", "intermediate", "deep"))
    put("deep_share", dpt["deep"]["targets"] / dn if dn else None, dn, LX["depth"]["deep"]["share"], K["def_depth"])
    out["_games"] = d["games"]
    out["_cov_n"] = cv.get("dropbacks_charted")
    ctx._def[key] = out
    return out


def player_splits(ctx, season, pid):
    """The player's target rates per on-field dropback vs man / zone and vs
    single-high / two-high shells, from the PRIOR seasons' tallies at DECAY
    (numerator and denominator over the same games, any team)."""
    P = ctx.P
    cache = ctx._split.setdefault(season, {})
    got = cache.get(pid)
    if got is not None:
        return got
    s = {"t_man": 0.0, "t_zone": 0.0, "db_man": 0.0, "db_zone": 0.0, "t_sh": 0.0, "t_th": 0.0, "db_sh": 0.0,
         "db_th": 0.0, "games": 0.0}
    SH, TH = (2, 4), (3, 5, 6, 8)      # nfl_matchup_data.COVERAGE codes: C1, C3 | C2, C4, C6, 2-MAN
    for sb in range(season - BACK, season):
        T = ctx.full_tallies(sb)
        if T is None:
            continue
        w = DECAY ** (season - sb)
        # numerators: his targets ON the on-field plays the denominators count
        # (nfl_matchup_profiles' per-game G_TGT_MAN / G_TGT_ZONE / G_TCOV0 slots,
        # schema 2), so a team change, a missing on-field record or a season
        # without participation can never put a target over a dropback it was
        # not counted in
        for r in T.player_games.get(pid) or []:
            line = r["players"].get(pid)
            if not line:
                continue
            s["db_man"] += w * line[P.G_MAN]
            s["db_zone"] += w * line[P.G_ZONE]
            s["db_sh"] += w * sum(line[P.G_COV0 + c - 1] for c in SH)
            s["db_th"] += w * sum(line[P.G_COV0 + c - 1] for c in TH)
            s["t_man"] += w * line[P.G_TGT_MAN]
            s["t_zone"] += w * line[P.G_TGT_ZONE]
            s["t_sh"] += w * sum(line[P.G_TCOV0 + c - 1] for c in SH)
            s["t_th"] += w * sum(line[P.G_TCOV0 + c - 1] for c in TH)
            s["games"] += w
    cache[pid] = s
    return s


def _split_feature(t_a, db_a, t_b, db_b, d_share, k):
    """log of the expected target rate under the defense's mix over the rate
    under the mix the player faced, the player's a-minus-b split shrunk by
    n/(n+k) with n the smaller side's dropbacks. d_share is the defense's
    (shrunk) share of side a. Returns (x, rel, detail)."""
    if db_a <= 0 or db_b <= 0 or d_share is None:
        return 0.0, 0.0, None
    r_a, r_b = t_a / db_a, t_b / db_b
    r_all = (t_a + t_b) / (db_a + db_b)
    if r_all <= 0:
        return 0.0, 0.0, None
    n = min(db_a, db_b)
    rel = n / (n + k)
    split = rel * (r_a - r_b)
    faced = db_a / (db_a + db_b)
    ratio = 1.0 + (d_share - faced) * split / r_all
    ratio = min(2.0, max(0.5, ratio))
    return math.log(ratio), rel, {"rate_a": round(r_a, 4), "rate_b": round(r_b, 4), "rate_all": round(r_all, 4),
                                  "faced_a": round(faced, 3), "n_min": round(n, 1)}


def qb_sens(ctx, season, week, qb):
    """The quarterback's EPA per dropback under pressure minus without (prior
    seasons only, participation) and vs blitz minus without (FTN, in-season +
    prior), each side shrunk toward his own figure by its measured k."""
    key = (season, week, qb)
    got = ctx._qb.get(key)
    if got is not None:
        return got
    out = {"press": 0.0, "blitz": 0.0, "press_n": 0.0, "blitz_n": 0.0}
    if qb:
        try:
            qp = ctx.prior_snap(season).qb(qb)
            own = _v(qp.get("epa_db"))
            a, b = qp["vs"].get("pressure"), qp["vs"].get("no_pressure")
            if own is not None and a and b:
                xa, _ = shrink(_v(a["epa_db"]), a["dropbacks"], own, K["qb_epa_press"])
                xb, _ = shrink(_v(b["epa_db"]), b["dropbacks"], own, K["qb_epa_nopress"])
                out["press"], out["press_n"] = xa - xb, min(a["dropbacks"], b["dropbacks"])
            qf = ctx.snap(season, week).qb(qb)
            own = _v(qf.get("epa_db"))
            a, b = qf["vs"].get("blitz"), qf["vs"].get("no_blitz")
            if own is not None and a and b:
                xa, _ = shrink(_v(a["epa_db"]), a["dropbacks"], own, K["qb_epa_blitz"])
                xb, _ = shrink(_v(b["epa_db"]), b["dropbacks"], own, K["qb_epa_noblitz"])
                out["blitz"], out["blitz_n"] = xa - xb, min(a["dropbacks"], b["dropbacks"])
        except (KeyError, TypeError, ValueError, ZeroDivisionError) as e:
            errlog.note("NFLM-qb", e)
    ctx._qb[key] = out
    return out


def player_block(ctx, season, week, pid, team):
    """In-season + prior: depth mix, middle share, target / carry share, YPC
    by box; prior-only: the coverage split rates."""
    S = ctx.snap(season, week)
    p = S.player(pid, team=team)
    rc, ru, u = p["receiving"], p["rushing"], p["usage"]
    LX = league_extras(ctx, season, week)
    out = {"targets": rc["targets"], "carries": ru["carries"], "games": p["games"]}
    dm = rc["depth_mix"]
    out["depth_mix"] = {}
    n_air = _n(dm["short"])
    for dd in ("behind", "short", "intermediate", "deep"):
        lg = LX["depth"][dd]["share"]
        v = _v(dm[dd])
        if v is None or lg is None:
            out["depth_mix"][dd] = lg
        else:
            out["depth_mix"][dd] = (v * n_air + lg * K["pl_depth_mix"]) / (n_air + K["pl_depth_mix"])
    mid = rc["location_mix"]["middle"]
    lgm = LX["loc"]["middle"]["share"]
    vm = _v(mid)
    out["mid_share"] = lgm if (vm is None or lgm is None) else (vm * _n(mid) + lgm * K["pl_mid_share"]) / (_n(mid) + K["pl_mid_share"])
    out["mid_n"] = _n(mid)
    out["tshare"] = _v(u["target_share"])
    out["cshare"] = _v(u["carry_share"])
    own = _v(ru["ypc"])
    out["ypc"] = own
    out["ypc_box"] = {}
    for b in ("light", "heavy"):
        blk = ru["by_box"][b]
        x, rel = shrink(_v(blk["ypc"]), blk["carries"], own, K["pl_ypc"], log=True)
        out["ypc_box"][b] = {"x": x, "rel": rel, "n": blk["carries"], "v": _v(blk["ypc"])}
    out["splits"] = player_splits(ctx, season, pid)
    return out


def offense_proe(ctx, season, week, team):
    o = ctx.snap(season, week).offense(team)
    x, rel = shrink(_v(o["proe"]), _n(o["proe"]), 0.0, K["off_proe"])
    return x, rel


def features(ctx, season, week, pid, team, opp, pos, margin=None, total=None, qb=None, comps=None):
    """{feature: value} for one player-game as of (season, week), plus
    {"_ev": {feature: evidence}}. `margin` is the team's expected margin
    (points, + = favoured), `total` the game total; `qb` the quarterback the
    passing features describe (gsis int); `comps` {market: comparables
    result} when the caller has them."""
    d = defense_block(ctx, season, week, opp)
    pb = player_block(ctx, season, week, pid, team) if pos in ("WR", "TE", "RB", "QB") else None
    f, ev = {}, {}

    def put(name, x, rel=None, n=None, kind="observed", why=""):
        f[name] = float(x or 0.0)
        ev[name] = {"rel": None if rel is None else round(rel, 3), "n": None if n is None else round(float(n), 1),
                    "kind": kind, "why": why}

    for nm in ("pass_epa", "run_ypc", "run_succ", "pass_funnel", "sack", "catch_all", "ypt_all", "blitz",
               "pressure", "light_box", "heavy_box", "man"):
        b = d[nm]
        put(nm, b["x"], b["rel"], b["n"], "observed", b["src"])
    rp = pos if pos in ("WR", "TE", "RB") else None
    for nm in ("tgt_rate", "catch", "ypt", "tgt_pg", "recyds_pg"):
        b = d.get(f"{nm}_{rp}") if rp else None
        tag = "tgt_rate_pos" if nm == "tgt_rate" else ("tgt_pg_pos" if nm == "tgt_pg" else
                                                        "recyds_pg_pos" if nm == "recyds_pg" else nm + "_pos")
        put(tag, b["x"] if b else 0.0, b["rel"] if b else None, b["n"] if b else None, "observed",
            (b or {}).get("src", ""))
    b = d["rushyds_pg_rb"]
    put("rushyds_pg_rb", b["x"] if pos == "RB" else 0.0, b["rel"], b["n"], "derived (opponent-adjusted)")
    b = d["passyds_pg_qb"]
    put("passyds_pg_qb", b["x"] if pos == "QB" else 0.0, b["rel"], b["n"], "derived (opponent-adjusted)")
    # ---- game script / volume
    # a game with no believable line reads the neutral fallback (margin 0, a
    # 44-point total); its evidence says so, so the board can mark it -- the
    # fallback swung Josh Allen's passing-TD mean 11% on the 2026-10-25 board
    # (review N3); the VALUES are unchanged (the frame stores them)
    put("margin", (margin or 0.0) / 7.0, None, None, "observed" if margin is not None else "fallback",
        "the game's spread, team side (fit: the closing line; production: Kalshi's de-vigged ladder)"
        if margin is not None else "no believable spread for this game: a level game (margin 0) is assumed")
    put("total_c", ((total - 44.0) / 10.0) if total else 0.0, None, None, "observed" if total else "fallback",
        "the game total (fit: the closing line; production: Kalshi's de-vigged ladder)"
        if total else "no believable total for this game: 44 points (the fit's centre) is assumed")
    x, rel = offense_proe(ctx, season, week, team)
    put("proe", x, rel, None, "observed", "own offense pass rate over expected")
    # ---- location / depth (receiving)
    if pb and rp:
        lgm = league_extras(ctx, season, week)["loc"]["middle"]["share"] or 0.33
        pm = pb["mid_share"] if pb["mid_share"] is not None else lgm
        dm_dev = d["mid_share"]["x"]
        put("loc_tgt", (pm - lgm) * dm_dev / max(1e-6, lgm * (1 - lgm)), d["mid_share"]["rel"], pb["mid_n"], "proxy",
            "middle-third target share (alignment PROXY) x the defense's middle share allowed")
        lgd = league_extras(ctx, season, week)["depth"]["deep"]["share"] or 0.12
        pdp = pb["depth_mix"]["deep"] if pb["depth_mix"]["deep"] is not None else lgd
        put("deep_tgt", (pdp - lgd) * d["deep_share"]["x"] / max(1e-6, lgd * (1 - lgd)), d["deep_share"]["rel"],
            pb["targets"], "observed", "deep target share x the defense's deep share allowed")
        put("depth_catch", sum((pb["depth_mix"][dd] or 0.0) * d[f"catch_{dd}"]["x"] for dd in pb["depth_mix"]),
            d["catch_short"]["rel"], pb["targets"], "derived")
        put("depth_ypt", sum((pb["depth_mix"][dd] or 0.0) * d[f"ypt_{dd}"]["x"] for dd in pb["depth_mix"]),
            d["ypt_short"]["rel"], pb["targets"], "derived")
        put("loc_ypt", pm * d["ypt_mid"]["x"] + (1 - pm) * d["ypt_out"]["x"], d["ypt_mid"]["rel"], pb["mid_n"], "proxy",
            "middle-third share (alignment PROXY) x YPT allowed middle / outside")
    else:
        for nm in ("loc_tgt", "deep_tgt", "depth_catch", "depth_ypt", "loc_ypt"):
            put(nm, 0.0)
    # ---- scheme vs scheme
    if pb and rp:
        sp = pb["splits"]
        dman = d["man"]
        man_share = (dman["prior"] + dman["x"]) if dman["prior"] is not None else None
        x, rel, det = _split_feature(sp["t_man"], sp["db_man"], sp["t_zone"], sp["db_zone"], man_share, K["pl_mz_split"])
        put("cov_mz", x, rel, (det or {}).get("n_min"), "proxy",
            "target rate per on-field dropback (proxy for per route) vs man/zone x the defense's man share (prior seasons)")
        ev["cov_mz"]["detail"] = det
        dsh = d["sh_frac"]
        sh_share = (dsh["prior"] + dsh["x"]) if dsh["prior"] is not None else None
        x, rel, det = _split_feature(sp["t_sh"], sp["db_sh"], sp["t_th"], sp["db_th"], sh_share, K["pl_shell_split"])
        put("cov_shell", x, rel, (det or {}).get("n_min"), "proxy",
            "target rate per on-field dropback vs single-/two-high x the defense's shell mix (prior seasons)")
        ev["cov_shell"]["detail"] = det
    else:
        put("cov_mz", 0.0)
        put("cov_shell", 0.0)
    bl = d["blitz"]
    put("blitz_rb", bl["x"] if pos == "RB" else 0.0, bl["rel"], bl["n"], "observed", "defense blitz rate (RB rows)")
    put("blitz_te", bl["x"] if pos == "TE" else 0.0, bl["rel"], bl["n"], "observed", "defense blitz rate (TE rows)")
    qs = qb_sens(ctx, season, week, qb) if qb else {"press": 0.0, "blitz": 0.0, "press_n": 0.0, "blitz_n": 0.0}
    pz = d["pressure"]
    put("press_catch", pz["x"] * qs["press"] * 100.0 if rp else 0.0, pz["rel"], qs["press_n"], "observed",
        "defense pressure rate (prior seasons) x the QB's EPA drop under pressure")
    put("press_qb", pz["x"] * qs["press"] * 100.0 if pos == "QB" else 0.0, pz["rel"], qs["press_n"], "observed",
        "defense pressure rate (prior seasons) x the QB's EPA drop under pressure")
    put("blitz_qb", bl["x"] * qs["blitz"] * 100.0 if pos == "QB" else 0.0, bl["rel"], qs["blitz_n"], "observed",
        "defense blitz rate x the QB's EPA change vs the blitz")
    if pb and pos in ("RB", "QB", "WR"):
        lb, hb = d["light_box"], d["heavy_box"]
        x = lb["x"] * pb["ypc_box"]["light"]["x"] + hb["x"] * pb["ypc_box"]["heavy"]["x"]
        put("box_ypc", x * 10.0, min(lb["rel"], max(pb["ypc_box"]["light"]["rel"], pb["ypc_box"]["heavy"]["rel"])),
            pb["carries"], "observed", "player YPC by box x the defense's box mix")
    else:
        put("box_ypc", 0.0)
    # ---- comparables
    for m in MARKETS:
        c = (comps or {}).get(m)
        put("comp_" + m, c["x"] if c else 0.0, c["shrink"] if c else None, c["n_eff"] if c else None,
            "modelled", "his games vs similar defenses (as of each game) over all his games")
    f["_ev"] = ev
    f["_def"] = d
    f["_pb"] = pb
    return f


# ---- the defense similarity vectors (comparables) ----------------------------------
def def_vectors(ctx, season, week, teams):
    """{team: [z-scored DEF_VEC]} as of (season, week): each team's shrunk
    deviations standardised across the teams given."""
    raw = {}
    for t in teams:
        try:
            d = defense_block(ctx, season, week, t)
        except (KeyError, TypeError, ValueError, ZeroDivisionError) as e:
            errlog.note("NFLM-defvec", e)
            continue
        raw[t] = [d[k]["x"] for k in DEF_VEC]
    if len(raw) < 4:
        return {}
    out = {t: [] for t in raw}
    for j in range(len(DEF_VEC)):
        col = [v[j] for v in raw.values()]
        mu = sum(col) / len(col)
        sd = math.sqrt(sum((c - mu) ** 2 for c in col) / max(1, len(col) - 1)) or 1.0
        for t, v in raw.items():
            out[t].append(round((v[j] - mu) / sd, 4))
    return out


def similarity(a, b):
    d2 = sum((x - y) ** 2 for x, y in zip(a, b))
    return math.exp(-d2 / (2.0 * SIM_H2))


def history(ctx, season, week, pid, opp_vec, defhist):
    """His past games as of (season, week): [(recency weight, similarity to
    this opponent, game record, his line, that opponent's vector then)] --
    every game he played (current season before `week`, BACK seasons before
    it, any team), most recent last. A game whose opponent vector is missing
    keeps similarity None (it still counts in the plain hit rates)."""
    P = ctx.P
    games = []
    for sb in range(season - BACK, season + 1):
        T = ctx.current(season, week) if sb == season else ctx.full_tallies(sb)
        if T is None:
            continue
        w = DECAY ** (season - sb)
        for r in T.player_games.get(pid) or []:
            if sb == season and r["week"] >= week:
                continue
            line = r["players"].get(pid)
            if not line or (line[P.G_SNAPS] + line[P.G_TGT] + line[P.G_CAR] + line[P.G_PATT]) <= 0:
                continue
            vec = defhist.get((sb, r["week"], r["dfn"])) if defhist else None
            s_ = similarity(opp_vec, vec) if (opp_vec and vec) else None
            games.append((w, s_, r, line, vec))
    games.sort(key=lambda g: (g[2]["season"], g[2]["week"]))
    return games


def comparables(ctx, season, week, pid, opp_vec, defhist, markets=MARKETS, keep=8, games=None):
    """His past games weighted by recency (DECAY a season) x the similarity of
    that game's opponent AS OF THAT GAME (defhist[(season, week, team)]) to
    this opponent. Per market: {"x": shrunk log ratio of the similar-weighted
    mean over the recency-weighted mean, "n_eff", "shrink", "games"}; every
    game is kept (a failure is never dropped) and the `keep` most similar are
    returned with why."""
    P = ctx.P
    if not opp_vec:
        return {}, []
    if games is None:
        games = history(ctx, season, week, pid, opp_vec, defhist)
    games = [g for g in games if g[1] is not None]
    out = {}
    if not games:
        return out, []
    for m in markets:
        gi, c = COMP_STAT[m]
        ix = getattr(P, gi)
        sw = sum(w * s for w, s, *_ in games)
        sw2 = sum((w * s) ** 2 for w, s, *_ in games)
        wa = sum(w for w, *_ in games)
        if sw <= 0 or wa <= 0:
            continue
        # yards can total below zero in a game; the ratio of means reads them at 0
        m_sim = max(0.0, sum(w * s * ln[ix] for w, s, r, ln, v in games) / sw)
        m_all = max(0.0, sum(w * ln[ix] for w, s, r, ln, v in games) / wa)
        n_eff = sw * sw / sw2 if sw2 else 0.0
        shr = n_eff / (n_eff + COMP_K)
        out[m] = {"x": shr * math.log((m_sim + c) / (m_all + c)), "n_eff": n_eff, "shrink": shr,
                  "m_sim": m_sim, "m_all": m_all, "games": len(games)}
    top = sorted(games, key=lambda g: -g[0] * g[1])[:keep]
    sel = []
    sw = sum(w * s for w, s, *_ in games)
    for w, s, r, ln, vec in top:
        diffs = sorted(((abs(x - y), DEF_VEC[j]) for j, (x, y) in enumerate(zip(opp_vec, vec))))[:2]
        sel.append({"season": r["season"], "week": r["week"], "opp": r["dfn"], "similarity": round(s, 3),
                    "weight": round(w * s / sw, 4) if sw else 0.0,
                    "stats": game_stats(P, ln),
                    "why": "closest on " + ", ".join(DEF_VEC_LABEL[n] for _d, n in diffs)
                           + f"; season weight {w:g}"})
    return out, sel


def game_stats(P, ln):
    """One game's line in the payload's stat names (nflverse pbp: kneels are
    not carries, two-point plays and return touchdowns are not in it)."""
    st = {"tgt": ln[P.G_TGT], "rec": ln[P.G_REC], "rec_yd": ln[P.G_RECY], "rush_att": ln[P.G_CAR],
          "rush_yd": ln[P.G_RUSHY], "pass_att": ln[P.G_PATT], "pass_cmp": ln[P.G_PCMP], "pass_yd": ln[P.G_PASSY],
          "pass_td": ln[P.G_PASSTD], "int": ln[P.G_INT], "td": ln[P.G_RECTD] + ln[P.G_RUSHTD]}
    st["dk_fp"] = round(dk_points({"pass_yd": st["pass_yd"], "pass_td": st["pass_td"], "int": st["int"],
                                   "rush_yd": st["rush_yd"], "rush_td": ln[P.G_RUSHTD], "rec": st["rec"],
                                   "rec_yd": st["rec_yd"], "rec_td": ln[P.G_RECTD], "fum_lost": ln[P.G_FUML]}), 2)
    return st


# =====================================================================================
# The shipped model: coefficients, distributions, the DFS hook and the board
# =====================================================================================
_COEFS = {"mtime": None, "data": None}


def load_coefs(path=None):
    """The committed model (research/nfl_matchup_fit.py test -> coefs.json),
    or None when it is missing or of another schema."""
    p = path or COEF_PATH
    try:
        mt = os.path.getmtime(p)
    except OSError:
        return None
    if path is None and _COEFS["mtime"] == mt:
        return _COEFS["data"]
    try:
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        errlog.note("NFLM-coefs", e)
        return None
    if data.get("schema") != SCHEMA:
        errlog.note("NFLM-coefs", msg=f"schema {data.get('schema')} != {SCHEMA}")
        return None
    if path is None:
        _COEFS.update(mtime=mt, data=data)
    return data


def shipped_markets(coefs):
    """Markets whose shipped model carries any term (weight > 0)."""
    if not coefs:
        return set()
    return {m for m, mod in (coefs.get("models") or {}).items() if mod.get("terms")}


def term_value(t, f, pos):
    """The research design's column for one term (research/nfl_matchup_fit.design)."""
    kind = t[0]
    if kind == "int":
        return 1.0 if pos == t[1] else 0.0
    v = float(f.get(t[1]) or 0.0)
    if kind == "f":
        return v
    if kind == "x":
        return v if pos == t[2] else 0.0
    if kind == "xm":
        return v * float(f.get("margin") or 0.0)
    ts = (f.get("_pb") or {}).get("tshare")
    ts = 0.15 if ts is None else float(ts)
    return v * (ts - 0.15) / 0.1


def market_adjust(coefs, m, f, pos):
    """{"factor": exp(eta), "eta", "se", "contrib": [(term name, b x)]} for one
    market. A market with no shipped terms has factor 1 and no contributions."""
    mod = ((coefs or {}).get("models") or {}).get(m) or {}
    terms = [tuple(t) for t in mod.get("terms") or []]
    if not terms:
        return {"factor": 1.0, "eta": 0.0, "se": 0.0, "contrib": []}
    b = mod["b"]
    x = [term_value(t, f, pos) for t in terms]
    contrib = [(nm, bi * xi) for nm, bi, xi in zip(mod["names"], b, x)]
    eta = sum(c for _n, c in contrib)
    cov = mod.get("cov") or []
    var = 0.0
    for i, xi in enumerate(x):
        if xi:
            for j, xj in enumerate(x):
                if xj:
                    var += xi * cov[i][j] * xj
    eta = max(-1.0, min(1.0, eta))        # never more than a factor e either way (a guard rail, not a fit)
    return {"factor": math.exp(eta), "eta": eta, "se": math.sqrt(max(0.0, var)), "contrib": contrib}


def effects(base, adj):
    """Split adjusted - base across the contributions in proportion to their
    log-scale share, so the parts sum EXACTLY to the applied change."""
    tot = adj["eta"]
    delta = base * (adj["factor"] - 1.0)
    if not adj["contrib"] or abs(tot) < 1e-15:
        return {nm: 0.0 for nm, _c in adj["contrib"]}
    raw = sum(c for _n, c in adj["contrib"])
    return {nm: delta * c / raw if raw else 0.0 for nm, c in adj["contrib"]}


class ShiftedGamma:
    """DraftKings points: (X + shift) ~ Gamma(mean + shift, var). Continuous
    (DK points move in tenths), so P(X >= k) has no continuity correction.
    prop_dist.Dist's interface: sf_ge, mean, var, to_dict, ladder."""
    family = "shifted_gamma"

    def __init__(self, mean, var, shift):
        import prop_dist as PD
        self._PD = PD
        self.mu, self.v, self.shift = float(mean), max(1e-6, float(var)), float(shift)
        m = max(1e-3, self.mu + self.shift)
        self.shape, self.scale = m * m / self.v, self.v / m

    def sf_ge(self, k):
        x = float(k) + self.shift
        if x <= 0:
            return 1.0
        return self._PD.gammainc_pq(self.shape, x / self.scale)[1]

    def mean(self):
        return self.mu

    def var(self):
        return self.v

    def ladder(self, ks):
        ks = list(ks)
        ps = [min(1.0, max(0.0, self.sf_ge(k))) for k in ks]
        return dict(zip(ks, self._PD.enforce_monotone(ks, ps)))

    def to_dict(self):
        return {"family": self.family, "params": {"mean": self.mu, "var": self.v, "shift": self.shift}}


def _disp(coefs, m, pos):
    return (((coefs or {}).get("dispersion") or {}).get(m) or {}).get(pos)


YARDS_FAMILIES = ("gamma", "lognormal")


def yards_family(coefs, m):
    """The positive-part family the committed model names for a yards market
    (coefs["dispersion"][m]["family"]). Until 2026-10-23 the research fit kept
    the choice under "_family", which its own JSON writer strips, so this read
    fell back to gamma on every line (review F1) -- and gamma is what the 2025
    validation, the 2026 test and the Kalshi comparison all scored, and the
    family that did better out of sample (report section 2a). A file without
    the key therefore reads gamma; a name prop_dist cannot draw is ledgered
    and reads gamma too, never a crashed board."""
    fam = (((coefs or {}).get("dispersion") or {}).get(m) or {}).get("family")
    if fam is None:
        return "gamma"
    if fam not in YARDS_FAMILIES:
        errlog.note("NFLM-family", msg=f"{m}: no yards family {fam!r}; reading gamma")
        return "gamma"
    return fam


def market_dist(coefs, m, mu, pos, base):
    """The fitted family for market m at mean mu (the research twins in
    research/nfl_matchup_fit.market_probs). `base` is Sleeper's line dict (the
    yards point mass reads the count line scaled by the yards factor)."""
    import prop_dist as PD
    prm = _disp(coefs, m, pos)
    if prm is None or not (mu and mu > 0):
        return None
    if m in ("tgt", "rec", "car", "patt", "pcmp", "ptd", "int"):
        return PD.NegBin(mu, prm["alpha"])
    if m == "pass_yd":
        return PD.NormalCC(mu, prm["sd_c"] * max(mu, 1.0) ** prm["sd_d"], lower=0)
    if m in ("rec_yd", "rush_yd"):
        cm, cf = ("rec", "rec") if m == "rec_yd" else ("car", "rush_att")
        cp = _disp(coefs, cm, pos)
        by = float(base.get(SLEEPER_FIELD[m]) or 0.0)
        bc = float(base.get(cf) or 0.0)
        if cp is None or by <= 0 or bc <= 0:
            return None
        cnt = bc * mu / by
        p0 = PD.NegBin(cnt, cp["alpha"]).pmf(0)
        p0 = min(0.999, max(0.0, p0))
        m_pos = mu / (1.0 - p0)
        return PD.Yards(p0, m_pos, prm["cv"] * m_pos, yards_family(coefs, m))
    return None


def dk_dist(coefs, means, dists, pos):
    """DraftKings points from the components: the mean of the scoring with
    each bonus at its probability, the variance the independent components'
    times the fitted per-position factor (research fit_dk_scale), shifted gamma."""
    w = {"rec": DK["rec"], "rec_yd": DK["rec_yd"], "rec_td": DK["rec_td"], "rush_yd": DK["rush_yd"],
         "rush_td": DK["rush_td"], "pass_yd": DK["pass_yd"], "pass_td": DK["pass_td"], "int": DK["int"],
         "fum_lost": DK["fum_lost"]}
    mean = sum(w[k] * float(means.get(k) or 0.0) for k in w)
    V = 0.0
    d = dists.get("rec")
    if d is not None:
        V += d.var()
    V += 36.0 * float(means.get("rec_td") or 0.0) + 36.0 * float(means.get("rush_td") or 0.0)
    V += 16.0 * float(means.get("pass_td") or 0.0) + float(means.get("int") or 0.0)
    for m, thr in (("rec_yd", 100), ("rush_yd", 100)):
        d = dists.get(m)
        if d is not None:
            V += 0.01 * d.var()
            mean += 3.0 * d.sf_ge(thr)
    d = dists.get("pass_yd")
    if d is not None:
        V += 0.0016 * d.var()
        mean += 3.0 * d.sf_ge(300)
    c = ((coefs or {}).get("dk_scale") or {}).get(pos, 1.0)
    return ShiftedGamma(mean, c * V, (coefs or {}).get("fp_shift", 3.0))


# the positions (and the passing volume) each market was FIT on
# (research/nfl_matchup_fit.SPEC / fit_mask): outside them the matchup factor is 1
MARKET_POS = {"tgt": ("WR", "TE", "RB"), "rec": ("WR", "TE", "RB"), "rec_yd": ("WR", "TE", "RB"),
              "car": ("RB", "QB", "WR"), "rush_yd": ("RB", "QB", "WR"),
              "patt": ("QB",), "pcmp": ("QB",), "pass_yd": ("QB",), "ptd": ("QB",), "int": ("QB",)}


def in_model(m, pos, base):
    if pos not in MARKET_POS[m]:
        return False
    if m in PASS and float(base.get("pass_att") or 0.0) < 10:
        return False
    return True


def player_markets(coefs, f, pos, base):
    """Every market's baseline, matchup factor, adjusted mean, distribution and
    standard error of log(mean) for one player. `base`: Sleeper's line
    {rec_tgt, rec, rec_yd, rec_td, rush_att, rush_yd, rush_td, pass_att,
    pass_cmp, pass_yd, pass_td, pass_int, fum_lost}."""
    out = {}
    for m in MARKETS:
        b = float(base.get(SLEEPER_FIELD[m]) or 0.0)
        if b <= 0:
            continue
        adj = market_adjust(coefs, m, f, pos) if (f is not None and in_model(m, pos, base)) else \
            {"factor": 1.0, "eta": 0.0, "se": 0.0, "contrib": []}
        mu = b * adj["factor"]
        out[m] = {"baseline": b, "adjusted": mu, "adj": adj, "dist": market_dist(coefs, m, mu, pos, base),
                  "effects": effects(b, adj)}
    ft = out.get("tgt", {}).get("adj", {}).get("factor", 1.0)
    fc = out.get("car", {}).get("adj", {}).get("factor", 1.0)
    rt, ut = float(base.get("rec_td") or 0.0), float(base.get("rush_td") or 0.0)
    if rt + ut > 0:
        import prop_dist as PD
        mu = rt * ft + ut * fc
        out["td"] = {"baseline": rt + ut, "adjusted": mu, "adj": {"factor": mu / (rt + ut), "se": 0.0},
                     "dist": PD.Poisson(mu), "effects": {}}
    means = {"rec": out.get("rec", {}).get("adjusted", 0.0), "rec_yd": out.get("rec_yd", {}).get("adjusted", 0.0),
             "rec_td": rt * ft, "rush_yd": out.get("rush_yd", {}).get("adjusted", 0.0), "rush_td": ut * fc,
             "pass_yd": out.get("pass_yd", {}).get("adjusted", 0.0), "pass_td": out.get("ptd", {}).get("adjusted", 0.0),
             "int": out.get("int", {}).get("adjusted", 0.0), "fum_lost": float(base.get("fum_lost") or 0.0)}
    bmeans = {"rec": float(base.get("rec") or 0.0), "rec_yd": float(base.get("rec_yd") or 0.0), "rec_td": rt,
              "rush_yd": float(base.get("rush_yd") or 0.0), "rush_td": ut, "pass_yd": float(base.get("pass_yd") or 0.0),
              "pass_td": float(base.get("pass_td") or 0.0), "int": float(base.get("pass_int") or 0.0),
              "fum_lost": float(base.get("fum_lost") or 0.0)}
    dists = {k: out[k]["dist"] for k in ("rec", "rec_yd", "rush_yd", "pass_yd") if k in out}
    bd = {k: market_dist(coefs, k, float(base.get(SLEEPER_FIELD[k]) or 0.0), pos, base) for k in ("rec", "rec_yd", "rush_yd", "pass_yd")}
    if any(v for v in bmeans.values()):
        dk = dk_dist(coefs, means, dists, pos)
        dk0 = dk_dist(coefs, bmeans, {k: v for k, v in bd.items() if v is not None}, pos)
        out["dk_fp"] = {"baseline": dk0.mean(), "adjusted": dk.mean(), "adj": {"factor": None, "se": 0.0},
                        "dist": dk, "effects": {}}
    return out


# what "strong_fit" may read (the payload contract, 2026-10-23): only the matchup
# terms the model KEPT -- what this defense measurably concedes (D2-D7) -- never
# Sleeper's level (R) or the volume / game-script context (V)
_NOT_MATCHUP = {"int", "margin", "total_c", "proe"}
STRONG_FIT = 0.05


def matchup_shift(adj):
    """exp(sum of the kept matchup terms' log contributions) - 1 for one market."""
    eta = sum(c for nm, c in (adj or {}).get("contrib") or [] if _base_feature(nm) not in _NOT_MATCHUP)
    return math.exp(max(-1.0, min(1.0, eta))) - 1.0


def line_labels(p, lo, hi, mkt, shift, n_eff, now=None):
    """(prop_value block or None, labels, fit_side) for one line. strong_fit is
    side-aware: a kept matchup shift of +5% or more labels the YES side, -5%
    or more the NO side (fit_side says which), and the EV / probability
    labels are then read for that side; otherwise the side with the larger
    EV (prop_value's default)."""
    import prop_value as PV
    strong = abs(shift or 0.0) >= STRONG_FIT
    side = ("yes" if shift > 0 else "no") if strong else None
    val, labels = None, []
    try:
        if mkt and (mkt.get("yes_ask") or mkt.get("no_ask")):
            val = PV.line_value(p, mkt, p_lo=lo, p_hi=hi, now=now)
            labels = PV.line_labels(val, side=side, strong_fit=strong, n_eff=n_eff)
        else:
            labels = PV.labels_from(p_side=p if (side or "yes") == "yes" else 1.0 - p,
                                    width=None if lo is None or hi is None else hi - lo, n_eff=n_eff,
                                    strong_fit=strong)
    except ValueError as e:
        errlog.note("NFLM-board-value", e)
    return val, labels, side


def probs_with_band(dist_fn, mu, se, ks):
    """{k: (p, lo, hi)}: P(stat >= k) at the mean and at mean x exp(-/+1.96 se)
    (parameter uncertainty of the shipped coefficients only), monotone in k.
    Where no shipped coefficient carries uncertainty (se 0: every market with
    no shipped term) there is no measured band, and lo = hi = None says so.
    It used to return lo = hi = p, a zero-width band that claimed no
    uncertainty around a Sleeper line the model never fitted: on the
    2026-10-25 board 99% of lines carried one and every +EV line read
    "robust" (review F2 / the coordinator's O1)."""
    import prop_dist as PD
    d = dist_fn(mu)
    if d is None:
        return {}
    ks = sorted(set(int(k) for k in ks))
    lad = d.ladder(ks)
    if not (se and se > 0):
        return {k: (lad[k], None, None) for k in ks}
    lo_d, hi_d = dist_fn(mu * math.exp(-1.96 * se)), dist_fn(mu * math.exp(1.96 * se))
    lo_l, hi_l = lo_d.ladder(ks), hi_d.ladder(ks)
    lo = PD.enforce_monotone(ks, [min(lo_l[k], hi_l[k], lad[k]) for k in ks])
    hi = PD.enforce_monotone(ks, [max(lo_l[k], hi_l[k], lad[k]) for k in ks])
    return {k: (lad[k], lo[i], hi[i]) for i, k in enumerate(ks)}


# ---- the DFS hook ---------------------------------------------------------------------
_MEANS_OF = {"rec_tgt": "tgt", "rec": "rec", "rec_yd": "rec_yd", "rec_td": "tgt", "rush_att": "car",
             "rush_yd": "rush_yd", "rush_td": "car", "pass_att": "patt", "pass_cmp": "pcmp", "pass_yd": "pass_yd",
             "pass_td": "ptd", "int": "int"}
# Sleeper's default PPR scoring for proj_pts (nfl_usage._PPR plus passing):
# the legacy simulator pins its mean to proj_pts, so it moves with the lines
_PPR = {"rec": 1.0, "rec_yd": 0.1, "rec_td": 6.0, "rush_yd": 0.1, "rush_td": 6.0, "pass_yd": 0.04, "pass_td": 4.0,
        "int": -1.0}
_MEANS_TO_SLEEPER = {"rec_tgt": "rec_tgt", "rec": "rec", "rec_yd": "rec_yd", "rec_td": "rec_td", "rush_att": "rush_att",
                     "rush_yd": "rush_yd", "rush_td": "rush_td", "pass_att": "pass_att", "pass_cmp": "pass_cmp",
                     "pass_yd": "pass_yd", "pass_td": "pass_td", "int": "pass_int", "fum": "fum_lost"}


def factors_for_pairs(pairs, season, week, coefs, ctx=None, qb_of=None, lines=None):
    """{index in pairs: {market: factor}} for the markets that ship. Players
    without an nflverse id or a profile keep factor 1 (their line is untouched)."""
    ship = shipped_markets(coefs)
    out = {}
    if not ship:
        return out
    if ctx is None:
        ctx = production_ctx(season)
    if ctx is None:
        return out
    ids = _ids()
    sl2g = ids.get("sleeper2gsis") or {}
    need_comp = any(any(t[0] == "f" and t[1].startswith("comp_") for t in coefs["models"][m]["terms"]) for m in ship)
    dh, vecs = (None, None)
    if need_comp:
        dh = def_history(ctx, season, week, save=getattr(ctx, "save_history", True))
        vecs = def_vectors(ctx, season, week, sorted(ctx.stores[season]["teams"])) if season in ctx.stores else {}
    if qb_of is None:
        qb_of = {}
        for pid, p in pairs:
            att = float((p.get("means") or {}).get("pass_att") or 0.0)
            t = p.get("team")
            g = sl2g.get(str(pid or ""))
            if g and att > 5 and att > qb_of.get(t, (0.0, None))[0]:
                qb_of[t] = (att, g)
        qb_of = {t: g for t, (a, g) in qb_of.items()}
    uses_lines = any(t[1] in ("margin", "total_c") or t[0] == "xm" for m in ship for t in coefs["models"][m]["terms"])
    if lines is None and uses_lines:
        lines = game_lines([(p.get("team"), p.get("opp")) for _pid, p in pairs])
    for i, (pid, p) in enumerate(pairs):
        g = sl2g.get(str(pid or ""))
        pos = p.get("pos")
        if not g or pos not in POS:
            continue
        team, opp = to_nflverse(p.get("team")), to_nflverse(p.get("opp"))
        try:
            comps = None
            if need_comp and vecs:
                comps, _sel = comparables(ctx, season, week, g, vecs.get(opp), dh)
            f = features(ctx, season, week, g, team, opp, pos, margin=(lines or {}).get(team, {}).get("margin"),
                         total=(lines or {}).get(team, {}).get("total"), qb=qb_of.get(p.get("team")), comps=comps)
        except (KeyError, TypeError, ValueError, ZeroDivisionError) as e:
            errlog.note("NFLM-apply-features", e)
            continue
        base = {_MEANS_TO_SLEEPER[k]: (p.get("means") or {}).get(k) for k in _MEANS_TO_SLEEPER}
        out[i] = {m: market_adjust(coefs, m, f, pos)["factor"] for m in ship if in_model(m, pos, base)}
    return out


def apply(pairs, season, week, coefs=None, ctx=None, lines=None):
    """The weekly DFS board's hook, called right after nfl_usage.apply on the
    (Sleeper player id, row) pairs nfl_dfs_sim.weekly_games builds: each row's
    `means` components move by the VALIDATED matchup factors only, every team
    total of every component stays Sleeper's (+ nfl_usage's) unless the
    pre-registered test freed them, and proj_pts moves by the PPR change.
    Never raises; returns the stamp the board records (None-free)."""
    stamp = {"applied": False, "players_moved": 0, "mean_abs_ppr_shift": 0.0, "markets": [],
             "team_totals": "preserved", "source": "research/reports/nfl_matchup_model.md"}
    try:
        coefs = coefs if coefs is not None else load_coefs()
        if not coefs:
            stamp["reason"] = "no committed model (research/data/nfl_matchup_model/coefs.json)"
            return stamp
        hook = coefs.get("dfs_hook") or {}
        ship = shipped_markets(coefs)
        if not hook.get("ships") or not ship:
            stamp["reason"] = "the pre-registered test shipped no DFS adjustment (weight 0)"
            stamp["markets"] = sorted(ship)
            return stamp
        pairs = [(pid, p) for pid, p in (pairs or []) if isinstance(p, dict) and isinstance(p.get("means"), dict)]
        fac = factors_for_pairs(pairs, int(season), int(week), coefs, ctx=ctx, lines=lines)
        free = bool(hook.get("team_totals_free"))
        stamp["team_totals"] = "free" if free else "preserved"
        # new value per component, then (unless free) renormalise within team
        # a component that is missing, non-numeric or non-finite reads 0 and is never written back
        old = [{k: (_num(p["means"].get(k)) or 0.0) for k in _MEANS_OF} for _pid, p in pairs]
        new = []
        for i, (_pid, p) in enumerate(pairs):
            fi = fac.get(i) or {}
            new.append({k: old[i][k] * float(fi.get(_MEANS_OF[k], 1.0)) for k in _MEANS_OF})
        if not free:
            teams = {}
            for i, (_pid, p) in enumerate(pairs):
                teams.setdefault(p.get("team"), []).append(i)
            for team, members in teams.items():
                for k in _MEANS_OF:
                    tb = sum(old[i][k] for i in members)
                    ta = sum(new[i][k] for i in members)
                    if ta > 0 and tb > 0:
                        r = tb / ta
                        for i in members:
                            new[i][k] *= r
        moved, shift = 0, 0.0
        for i, (_pid, p) in enumerate(pairs):
            d_pts = 0.0
            changed = False
            for k in _MEANS_OF:
                if _num(p["means"].get(k)) is None:
                    continue
                if abs(new[i][k] - old[i][k]) > 1e-12:
                    changed = True
                    d_pts += _PPR.get(k, 0.0) * (new[i][k] - old[i][k])
                    p["means"][k] = new[i][k]
            if changed:
                moved += 1
                shift += abs(d_pts)
                if p.get("proj_pts") is not None:
                    p["proj_pts"] = round(float(p["proj_pts"]) + d_pts, 2)
        stamp.update(applied=True, players_moved=moved, mean_abs_ppr_shift=round(shift / moved, 3) if moved else 0.0,
                     markets=sorted(ship))
        return stamp
    except Exception as e:                      # the hook must never break the DFS board
        errlog.note("NFLM-apply", e)
        stamp["reason"] = f"failed: {type(e).__name__}"
        return stamp


def game_lines(team_pairs):
    """{nflverse team: {"margin", "total"}} from Kalshi's own de-vigged ladders
    (kalshi_nfl.implied) -- the pre-game market line, standing in for the
    closing spread / total the model was fit on. Missing -> margin 0, total
    league (the features read 0)."""
    import kalshi_nfl
    out = {}
    try:
        idx = kalshi_nfl.index() or {}
    except Exception as e:
        errlog.note("NFLM-lines", e)
        return out
    seen = set()
    for t, o in team_pairs:
        key = frozenset((kalshi_nfl._canon(t), kalshi_nfl._canon(o)))
        if key in seen or len(key) != 2:
            continue
        seen.add(key)
        sfx = next((k for k, e in idx.items() if e.get("pair") == key), None)
        if not sfx:
            continue
        try:
            imp = kalshi_nfl.implied(sfx) or {}
        except Exception as e:
            errlog.note("NFLM-lines", e)
            continue
        fav, mg = imp.get("favourite"), imp.get("margin")
        for x in key:
            out[to_nflverse(x)] = {"margin": (mg if x == fav else -mg) if mg is not None else None, "total": imp.get("total")}
    return out


# ---- production data access ---------------------------------------------------------
NV2SL = {"LA": "LAR"}
SL2NV = {"LAR": "LA", "WSH": "WAS", "JAC": "JAX"}


def to_nflverse(team):
    t = str(team or "").upper()
    return SL2NV.get(t, t)


_IDS = {"data": None, "ts": 0.0}


def _ids():
    """{"ids": nfl_matchup_ids payload ids, "sleeper2gsis": {...}, "positions"}."""
    if _IDS["data"] is not None and time.time() - _IDS["ts"] < 3600:
        return _IDS["data"]
    import nfl_matchup_data as D
    import nfl_matchup_profiles as P
    ids = D.load_ids()
    if not ids:
        return {}
    idx = D.ids_index(ids["ids"])
    data = {"ids": ids["ids"], "sleeper2gsis": idx["sleeper"], "positions": P.positions_from_ids(ids)}
    _IDS.update(data=data, ts=time.time())
    return data


def production_ctx(season):
    """A Ctx over the deep store's seasons season-BACK..season, or None."""
    import nfl_matchup_data as D
    ids = _ids()
    if not ids:
        return None
    stores = {}
    for s in range(int(season) - BACK - 2, int(season) + 1):
        st = D.load_season(s)
        if st:
            stores[s] = st
    if int(season) not in stores:
        return None
    return Ctx(stores, ids["positions"])


def def_history(ctx, season, week, save=True):
    """{(season, week, team): vector} for every week before `week`: the
    committed table (research export), the deep store's per-week additions,
    and any missing week of `season` computed now and saved."""
    import deep_cache
    out = {}
    try:
        with open(DEFHIST_PATH, encoding="utf-8") as fh:
            dh = json.load(fh)
        for k, v in (dh.get("rows") or {}).items():
            s, w, t = k.split("|")
            out[(int(s), int(w), t)] = v
    except (OSError, ValueError) as e:
        errlog.note("NFLM-defhist", e)
    for s in range(int(season) - BACK, int(season) + 1):
        st = ctx.stores.get(s)
        if not st:
            continue
        weeks = sorted({g["week"] for g in st["games"]})
        for w in weeks:
            if s == int(season) and w >= int(week):
                break
            if any((s, w, t) in out for t in st["teams"]):
                continue
            key = DEFHIST_KEY.format(season=s) + f"_w{w:02d}"
            got = deep_cache.load(key)[0] if save else None
            if not got:
                got = def_vectors(ctx, s, w, sorted(st["teams"]))
                ctx.drop(s, w)
                if save and got:
                    deep_cache.save(key, got)
            for t, v in got.items():
                out[(s, w, t)] = v
    return out


# ---- the board ------------------------------------------------------------------------
_SLEEPER_PROJ = ("https://api.sleeper.com/projections/nfl/{season}/{week}?season_type=regular"
                 "&position[]=QB&position[]=RB&position[]=WR&position[]=TE&order_by=pts_ppr")
STATUS = {None: "active", "": "active", "Questionable": "questionable", "Doubtful": "questionable",
          "Out": "out", "IR": "out", "PUP": "out", "Sus": "out", "NA": "out", "DNR": "out", "COV": "out"}
STAT_NAME = {"tgt": "tgt", "rec": "rec", "rec_yd": "rec_yd", "car": "rush_att", "rush_yd": "rush_yd",
             "patt": "pass_att", "pcmp": "pass_cmp", "pass_yd": "pass_yd", "ptd": "pass_td", "int": "int",
             "td": "td", "dk_fp": "dk_fp"}
KALSHI_STAT = {"rec": "rec", "rec_yd": "rec_yd", "rush_yd": "rush_yd", "pass_yd": "pass_yd", "td": "td"}
# factor rows of the matchup matrix: (label, the features it gathers, positions it applies to)
FACTOR_ROWS = (
    ("Coverage (man / zone)", ("cov_mz",), ("WR", "TE", "RB")),
    ("Coverage shell (single- / two-high)", ("cov_shell",), ("WR", "TE", "RB")),
    ("Overall pass defense", ("pass_epa", "catch_all", "ypt_all", "sack"), POS),
    ("Run defense", ("run_ypc", "run_succ"), ("RB", "QB", "WR")),
    ("Pass funnel (pass rate faced)", ("pass_funnel",), POS),
    ("Position defense", ("tgt_rate_pos", "tgt_pg_pos", "catch_pos", "ypt_pos", "recyds_pg_pos", "rushyds_pg_rb",
                          "passyds_pg_qb"), POS),
    ("Location / depth (alignment proxy)", ("loc_tgt", "deep_tgt", "depth_catch", "depth_ypt", "loc_ypt"), ("WR", "TE", "RB")),
    ("Pressure / blitz", ("press_catch", "press_qb", "blitz_qb", "pressure", "blitz", "blitz_rb", "blitz_te"), POS),
    ("Box count", ("light_box", "heavy_box", "box_ypc"), ("RB", "QB", "WR")),
    ("Game script and volume", ("margin", "total_c", "proe"), POS),
    ("Comparable opponents", tuple("comp_" + m for m in MARKETS), POS),
    ("Sleeper level recalibration", ("int",), POS),
)


def _clean(o):
    """No NaN / inf anywhere (the payload contract)."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    return o


def _r(x, n=4):
    x = _num(x)
    return None if x is None else round(x, n)


def _base_feature(term_name):
    """The feature a shipped term name belongs to ("cov_mzxTE" -> "cov_mz",
    "int[WR]" -> "int")."""
    if term_name.startswith("int["):
        return "int"
    for suf in ("xmargin", "xtshare", "xTE", "xRB", "xQB", "xWR"):
        if term_name.endswith(suf):
            return term_name[: -len(suf)]
    return term_name


def _fact(value, unit, kind, n=None, basis="", **extra):
    out = {"value": _r(value), "unit": unit, "kind": kind, "n": _r(n, 1), "basis": basis}
    out.update({k: v for k, v in extra.items()})
    return out


def role_block(f, pos):
    pb = (f or {}).get("_pb") or {}
    ev = (f or {}).get("_ev") or {}
    out = {}
    if not pb:
        return out
    if pos in ("WR", "TE", "RB"):
        out["target_share"] = _fact(pb.get("tshare"), "share", "observed", pb.get("targets"),
                                    "share of his team's targets, this season + prior seasons at 0.5 / 0.25 (games for this team)")
        det = (ev.get("cov_mz") or {}).get("detail") or {}
        if det:
            out["tgt_rate_vs_man"] = _fact(det.get("rate_a"), "per_dropback", "proxy", det.get("n_min"),
                                           "targets per on-field dropback vs man coverage, prior seasons (participation); a snap is not a route")
            out["tgt_rate_vs_zone"] = _fact(det.get("rate_b"), "per_dropback", "proxy", det.get("n_min"),
                                            "targets per on-field dropback vs zone coverage, prior seasons (participation)")
        det = (ev.get("cov_shell") or {}).get("detail") or {}
        if det:
            out["tgt_rate_vs_single_high"] = _fact(det.get("rate_a"), "per_dropback", "proxy", det.get("n_min"),
                                                   "targets per on-field dropback vs Cover 1 / Cover 3, prior seasons")
            out["tgt_rate_vs_two_high"] = _fact(det.get("rate_b"), "per_dropback", "proxy", det.get("n_min"),
                                                "targets per on-field dropback vs Cover 2 / 4 / 6 / 2-Man, prior seasons")
        out["middle_share"] = _fact(pb.get("mid_share"), "share", "proxy", pb.get("mid_n"),
                                    "share of his targets thrown to the middle third (nflfastR pass_location), shrunk to the league "
                                    "with k 46.6 -- NOT charted alignment (slot vs wide is in no free source)")
        out["deep_share"] = _fact((pb.get("depth_mix") or {}).get("deep"), "share", "observed", pb.get("targets"),
                                  "share of his targets 20+ air yards, shrunk to the league with k 18.1")
    if pos in ("RB", "QB", "WR") and pb.get("ypc") is not None:
        out["ypc"] = _fact(pb.get("ypc"), "yds_per_carry", "observed", pb.get("carries"), "yards per carry (pbp; kneels excluded)")
        for b in ("light", "heavy"):
            bb = (pb.get("ypc_box") or {}).get(b) or {}
            if bb.get("v") is not None:
                out[f"ypc_{b}_box"] = _fact(bb.get("v"), "yds_per_carry", "observed", bb.get("n"),
                                            f"yards per carry with a {b} box ({'<= 6' if b == 'light' else '8+'} in the box)")
    if pos in ("RB", "QB", "WR"):
        out["carry_share"] = _fact(pb.get("cshare"), "share", "observed", pb.get("carries"), "share of his team's carries")
    return out


OPP_FIELDS = (
    ("man", "share", "share of charted dropbacks in man coverage, PRIOR seasons only (no in-season participation)"),
    ("sh_frac", "share", "single-high share of charted single- + two-high shells, prior seasons only"),
    ("blitz", "share", "5+ pass rushers per dropback (FTN; in season + prior)"),
    ("pressure", "share", "pressure per dropback (NGS), prior seasons only"),
    ("light_box", "share", "<= 6 defenders in the box per snap"),
    ("heavy_box", "share", "8+ defenders in the box per snap"),
    ("pass_epa", "epa_per_dropback", "EPA per dropback allowed"),
    ("run_ypc", "yds_per_carry", "yards per carry allowed"),
    ("pass_funnel", "share", "dropbacks per snap faced"),
    ("catch_all", "share", "catch rate allowed, all targets"),
    ("ypt_all", "yds_per_target", "yards per target allowed, all targets"),
)


def opponent_block(d, pos, ranks):
    out = {}
    for name, unit, basis in OPP_FIELDS:
        b = d.get(name) or {}
        if b.get("v") is None:
            continue
        rk = (ranks.get(name) or {})
        out[name] = _fact(b["v"], unit, "observed", b.get("n"), basis + f"; league {_r(b.get('prior'))}; shrinkage n/(n+k) "
                          f"{_r(b.get('rel'), 3)} with k {b.get('k')}", league=_r(b.get("prior")),
                          rank=rk.get("rank"), pct=rk.get("pct"), rank_basis="1 = highest of the teams ranked")
    if pos in ("WR", "TE", "RB"):
        for nm, unit, basis in (("tgt_rate", "per_dropback", f"targets to {pos}s per dropback faced"),
                                ("catch", "share", f"catch rate allowed to {pos}s"),
                                ("ypt", "yds_per_target", f"yards per target allowed to {pos}s")):
            b = d.get(f"{nm}_{pos}") or {}
            if b.get("v") is not None:
                rk = ranks.get(f"{nm}_{pos}") or {}
                out[f"{nm}_{pos}"] = _fact(b["v"], unit, "observed", b.get("n"),
                                           basis + f"; league {_r(b.get('prior'))}; k {b.get('k')}",
                                           league=_r(b.get("prior")), rank=rk.get("rank"), pct=rk.get("pct"),
                                           rank_basis="1 = highest of the teams ranked")
    return out


def _ranks(blocks):
    """{metric: {team: {"rank", "pct"}}} across the defense blocks given."""
    names = [n for n, _u, _b in OPP_FIELDS] + [f"{a}_{p}" for a in ("tgt_rate", "catch", "ypt") for p in ("WR", "TE", "RB")]
    out = {}
    for nm in names:
        vals = [(t, (b.get(nm) or {}).get("v")) for t, b in blocks.items()]
        vals = [(t, v) for t, v in vals if v is not None]
        vals.sort(key=lambda tv: -tv[1])
        n = len(vals)
        for i, (t, v) in enumerate(vals):
            out.setdefault(t, {})[nm] = {"rank": i + 1, "pct": round(1.0 - i / max(1, n - 1), 3) if n > 1 else None}
    return out


def factor_rows(f, mk, pos, coefs):
    """The matchup matrix rows: every factor the model has for this
    position, its applied effect per market (0 at weight 0) and its evidence."""
    ev = (f or {}).get("_ev") or {}
    d = (f or {}).get("_def") or {}
    rows = []
    for label, feats, poss in FACTOR_ROWS:
        eff, by_feat = {}, {}
        for m, blk in mk.items():
            for tn, dv in (blk.get("effects") or {}).items():
                bf = _base_feature(tn)
                if bf in feats:
                    eff[STAT_NAME[m]] = eff.get(STAT_NAME[m], 0.0) + dv
                    by_feat[bf] = by_feat.get(bf, 0.0) + abs(dv)
        if pos not in poss and not eff:
            continue
        if by_feat:
            lead = max(by_feat, key=by_feat.get)
        else:
            lead = next((x for x in feats if f and abs(f.get(x) or 0.0) > 0), feats[0])
        e = ev.get(lead) or {}
        raw = f.get(lead) if f else None
        if eff:
            main = max(eff, key=lambda k: abs(eff[k]))
            dm = eff[main]
            base_m = next((blk["baseline"] for m, blk in mk.items() if STAT_NAME[m] == main), None) or 1.0
            assess = "favorable" if dm > 0.005 * base_m else "unfavorable" if dm < -0.005 * base_m else "neutral"
        else:
            main, dm, assess = None, 0.0, "neutral"
        if label.startswith("Sleeper level"):
            if not eff:
                continue
            player_txt, opp_txt = "his position's Sleeper line", "not opponent-specific"
            kind = "modelled"
        else:
            player_txt, opp_txt = _factor_text(lead, f, d, pos)
            kind = e.get("kind") or "observed"
        nm = next((x for x in feats if x.startswith("comp_")), None)
        neff = None
        if label.startswith("Comparable") and f:
            neff = max(((ev.get(x) or {}).get("n") or 0.0) for x in feats) or None
        rows.append({"factor": label, "player": player_txt, "opponent": opp_txt, "assessment": assess,
                     "effect": {"stat": main or _default_stat(pos), "delta_mean": _r(dm, 3)},
                     "effects": {k: _r(v, 3) for k, v in eff.items()},
                     "evidence": {"n": _r(e.get("n"), 1), "n_eff": _r(neff, 1), "reliability": e.get("rel"), "kind": kind},
                     "detail": {"features": {x: _r(f.get(x), 5) for x in feats if f and x in f},
                                "weight": "shipped" if eff else "0 (did not pass the pre-registered test, or not tested for this market)",
                                "raw_direction": None if raw is None else ("up" if raw > 0 else "down" if raw < 0 else "flat"),
                                "basis": e.get("why"), "split": (e.get("detail") or None)}})
        del nm
    return rows


def _default_stat(pos):
    return {"QB": "pass_yd", "RB": "rush_yd"}.get(pos, "rec_yd")


def _pct(x):
    return "n/a" if x is None else f"{100.0 * x:.0f}%"


def _factor_text(lead, f, d, pos):
    ev = (f or {}).get("_ev") or {}
    e = ev.get(lead) or {}
    det = e.get("detail") or {}
    b = d.get(lead) or {}
    if lead == "cov_mz" and det:
        return (f"{det['rate_a']:.3f} targets per on-field dropback vs man, {det['rate_b']:.3f} vs zone "
                f"(n={det['n_min']:.0f}, prior seasons; proxy for per route)",
                f"man on {_pct((d.get('man') or {}).get('v'))} of charted dropbacks (league {_pct((d.get('man') or {}).get('prior'))}, "
                f"prior seasons only)")
    if lead == "cov_shell" and det:
        return (f"{det['rate_a']:.3f} vs single-high, {det['rate_b']:.3f} vs two-high (n={det['n_min']:.0f})",
                f"single-high {_pct((d.get('sh_frac') or {}).get('v'))} of shells (league {_pct((d.get('sh_frac') or {}).get('prior'))})")
    pb = (f or {}).get("_pb") or {}
    if lead in ("press_qb", "press_catch", "blitz_qb"):
        dd = d.get("pressure" if lead != "blitz_qb" else "blitz") or {}
        return (f"QB EPA/dropback {'under pressure minus clean' if lead != 'blitz_qb' else 'vs blitz minus not'}: "
                f"{_r((f or {}).get(lead), 3)} (scaled; n={_r(e.get('n'), 0)} dropbacks)",
                f"{'pressure' if lead != 'blitz_qb' else 'blitz'} {_pct(dd.get('v'))} of dropbacks "
                f"(league {_pct(dd.get('prior'))})")
    if lead == "box_ypc":
        bx = pb.get("ypc_box") or {}
        return (f"YPC {_r(pb.get('ypc'), 2)}; light box {_r((bx.get('light') or {}).get('v'), 2)}, heavy "
                f"{_r((bx.get('heavy') or {}).get('v'), 2)}",
                f"light box {_pct((d.get('light_box') or {}).get('v'))}, heavy {_pct((d.get('heavy_box') or {}).get('v'))}")
    if lead in ("margin", "total_c", "proe"):
        fb = [x for x in ("margin", "total_c") if (ev.get(x) or {}).get("kind") == "fallback"]
        mg_txt = f"expected margin {_r(7.0 * ((f or {}).get('margin') or 0.0), 1)} pts"
        tt_txt = f"total {_r(44.0 + 10.0 * ((f or {}).get('total_c') or 0.0), 1)}"
        if "margin" in fb:
            mg_txt += " (FALLBACK: no believable Kalshi spread ladder)"
        if "total_c" in fb:
            tt_txt += " (FALLBACK: no believable Kalshi total ladder)"
        return (f"own PROE (shrunk) {_r((f or {}).get('proe'), 3)}",
                f"{mg_txt}, {tt_txt} (the fit read the closing line; production reads Kalshi's de-vigged ladder)")
    if lead in ("loc_tgt", "loc_ypt", "deep_tgt", "depth_ypt", "depth_catch"):
        dm, dd = d.get("mid_share") or {}, d.get("deep_share") or {}
        ym, yo = d.get("ypt_mid") or {}, d.get("ypt_out") or {}
        return (f"middle-third share {_pct(pb.get('mid_share'))} (alignment PROXY), deep share "
                f"{_pct((pb.get('depth_mix') or {}).get('deep'))}",
                f"targets allowed to the middle {_pct(dm.get('v'))} (league {_pct(dm.get('prior'))}), deep {_pct(dd.get('v'))} "
                f"(league {_pct(dd.get('prior'))}); YPT middle {_r(ym.get('v'), 1)}, outside {_r(yo.get('v'), 1)}")
    if lead.startswith("comp_"):
        return (f"n_eff {_r(e.get('n'), 1)} similar games", "similarity on " + ", ".join(DEF_VEC_LABEL.values()))
    pos_key = {"tgt_rate_pos": f"tgt_rate_{pos}", "catch_pos": f"catch_{pos}", "ypt_pos": f"ypt_{pos}",
               "tgt_pg_pos": f"tgt_pg_{pos}", "recyds_pg_pos": f"recyds_pg_{pos}"}.get(lead, lead)
    b = d.get(pos_key) or {}
    base_txt = "Sleeper's line (the opponent enters only through the model's terms)"
    if b.get("v") is not None:
        return (base_txt, f"{pos_key.replace('_', ' ')} {b['v']:.3f} (league {_r(b.get('prior'), 3)}, n={_r(b.get('n'), 0)}, "
                          f"believed {_r(b.get('rel'), 2)})")
    return (base_txt, e.get("why") or "")


def _hits(games, key, k, recent=10, stats=None):
    if not games:
        return None, None, None
    if stats is None:
        P = __import__("nfl_matchup_profiles")
        stats = [game_stats(P, ln) for _w, _s, _r, ln, _v in games]
    vals = [(g[0], g[1], st[key]) for g, st in zip(games, stats)]
    h_all = {"hits": sum(1 for _w, _s, x in vals if x >= k), "n": len(vals)}
    rec = vals[-recent:]
    h_rec = {"hits": sum(1 for _w, _s, x in rec if x >= k), "n": len(rec)}
    ws = [(w * s, x) for w, s, x in vals if s is not None]
    sw = sum(a for a, _x in ws)
    sw2 = sum(a * a for a, _x in ws)
    if sw > 0:
        n_eff = sw * sw / sw2
        h_sim = {"hits_w": round(n_eff * sum(a for a, x in ws if x >= k) / sw, 2), "n_eff": round(n_eff, 2)}
    else:
        h_sim = None
    return h_all, h_rec, h_sim


def _kalshi_lines(idx, suffix, ts=None):
    """{(stat, norm name): {k: market block}} for a game's prop markets; `ts`
    is when the index's quotes were read (kalshi_nfl's build time)."""
    g = (idx or {}).get(suffix) or {}
    q, tick = g.get("q") or {}, g.get("tick") or {}
    out = {}
    for (stat, nm, line) in (g.get("players") or {}):
        yq, nq = q.get(("prop", stat, nm, line, False)), q.get(("prop", stat, nm, line, True))
        if not yq and not nq:
            continue
        tk, _close = tick.get(("prop", stat, nm, line)) or (None, None)
        k = int(math.ceil(float(line)))
        out.setdefault((stat, nm), {})[k] = {
            "ticker": tk, "yes_bid": (yq or {}).get("bid"), "yes_ask": (yq or {}).get("ask"),
            "no_bid": (nq or {}).get("bid"), "no_ask": (nq or {}).get("ask"), "ts": ts,
            "volume": (yq or {}).get("vol"), "oi": (yq or {}).get("oi"), "fee_type": "quadratic",
            "fee_multiplier": None}
    return out


KALSHI_PATH = os.path.join(MODEL_DIR, "kalshi.json")


def slate_notes(coefs):
    """The slate-wide statements the tab draws above everything: what ships,
    and the held-out record against Kalshi's pre-kickoff prices when the
    comparison has run (research/data/nfl_matchup_model/kalshi.json)."""
    notes = []
    groups = {}
    for m, gs in ((coefs or {}).get("shipped") or {}).items():
        for g in gs or []:
            groups.setdefault(g, []).append(STAT_NAME.get(m, m))
    defense = {g: ms for g, ms in groups.items() if g not in ("R", "V")}
    if defense:
        notes.append("Opponent-defense factors that passed the pre-registered test (fit 2023-24, validated 2025, "
                     "confirmed on 2026's frozen pre-kickoff lines): " + "; ".join(
                         f"{GROUP_LABEL[g]} on {', '.join(sorted(ms))}" for g, ms in sorted(defense.items())) + ".")
    else:
        notes.append("No opponent-defense factor passed the pre-registered test (fit 2023-24, validated 2025, "
                     "confirmed on 2026's frozen pre-kickoff lines): the defense rows below print with effect 0 and "
                     "every probability is Sleeper's line with the fitted spread"
                     + (", except: " + "; ".join(f"{GROUP_LABEL[g]} on {', '.join(sorted(ms))}"
                                                 for g, ms in sorted(groups.items())) if groups else "")
                     + " (research/reports/nfl_matchup_model.md).")
    rush = ((coefs or {}).get("dispersion") or {}).get("rush_yd") or {}
    if rush and not any(isinstance(v, dict) and "cv_d" in v for v in rush.values()):
        # review F3 (2026-10-23), measured through this module's own market_dist on the frames:
        # one positive-part CV per position is too thin for small lines. Shown while the
        # committed rush_yd spread is that single-CV form. The line-dependent form
        # (cv_c x line^cv_d, the report's section 9) fixed these cells on 2025 but failed
        # its pre-registered gate overall (-0.0086 +/- 0.0137 nats), so it does not ship
        notes.append("Rushing-yard chances at LOW thresholds run high for running backs: on Sleeper lines of "
                     "20-30 yards the model's 10+ read 0.86 where 0.67 hit (2023-25, 487 games; 2026's frozen lines "
                     "the same), because one spread per position is too thin for small lines. Quarterbacks on lines "
                     "of 10-20 run the other way at 25+ and 40+ (0.15 vs 0.23 hit; 0.03 vs 0.09). A line-dependent "
                     "spread fixed those cells on 2025 but did not beat the single spread overall by the "
                     "pre-registered margin, so it does not ship (research/reports/nfl_matchup_model.md, "
                     "section 9).")
    try:
        with open(KALSHI_PATH, encoding="utf-8") as fh:
            kj = json.load(fh)
        for season, blk in sorted((kj.get("by_season") or {}).items()):
            a = blk.get("all") or {}
            if not a.get("n"):
                continue
            mdl, mkt = a.get("model") or {}, a.get("market") or {}
            roi = blk.get("roi_model") or {}
            basis = ("on Sleeper's post-week REVISED lines, the only ones that season has -- a baseline that knew "
                     "the inactives" if str(season) == "2025" else "on Sleeper's frozen pre-kickoff lines")
            txt = (f"Against Kalshi's pre-kickoff prices, {season} ({a['n']} sampled prop rungs, {basis}): model log loss "
                   f"{mdl.get('ll', 0):.3f} vs Kalshi's {mkt.get('ll', 0):.3f} (lower is better)")
            if roi.get("bets"):
                lo, hi = (roi.get("roi_95") or [None, None])
                txt += (f"; one contract on every positive-EV side, fee in: {roi['bets']} bets, ROI {100 * roi['roi']:+.1f}% "
                        f"(95% {100 * lo:+.1f}% to {100 * hi:+.1f}%)")
            notes.append(txt + ".")
    except (OSError, ValueError, TypeError, KeyError) as e:
        if not isinstance(e, FileNotFoundError):
            errlog.note("NFLM-notes", e)
    return notes


def line_fallback_notes(games, lines, coefs):
    """One slate note per game whose spread or total has no believable Kalshi
    ladder, when a shipped term reads them (review N3): such a game's
    game-script term runs on the neutral fallback (margin 0, a 44-point
    total), not on a market line. On the 2026-10-25 board CLE @ TEN read
    margin 0 with total 37, and the fallback moved Josh Allen's passing-TD
    mean by 11% in a test (1.512 at no ladder vs 1.679 at +7 / 54)."""
    ship = shipped_markets(coefs)
    mods = (coefs or {}).get("models") or {}
    reads = sorted(STAT_NAME.get(m, m) for m in ship
                   if any(t[1] in ("margin", "total_c") or t[0] == "xm" for t in mods[m].get("terms") or []))
    if not reads:
        return []
    out = []
    for g in games:
        miss = set()
        for t in (g.get("home"), g.get("away")):
            ln = (lines or {}).get(to_nflverse(t)) or {}
            if ln.get("margin") is None:
                miss.add("spread (margin 0 assumed)")
            if not ln.get("total"):
                miss.add("total (44 assumed)")
        if miss:
            out.append(f"{g.get('away')} @ {g.get('home')}: no believable Kalshi " + " or ".join(sorted(miss))
                       + f" -- its {', '.join(reads)} game-script term reads the fallback, not a market line.")
    return out


def board(season, week, rows=None, kalshi_idx=None, schedule=None, ctx=None, coefs=None, lines=None,
          with_market=True, now=None, min_pts=0.5, only_from_ts=None, only_to_ts=None):
    """The matchup board for an NFL week (the payload contract,
    matchup_payload_contract.md): every player with a Sleeper projection in
    the week's games. Pure Python; reads the committed model, the deep store's
    nflverse stores and (when with_market) Kalshi's index. Never raises."""
    now = time.time() if now is None else now
    out = {"sport": "nfl", "season": int(season), "week": int(week), "built_ts": int(now), "as_of": None,
           "provenance": [], "games": [], "players": [], "model": {}}
    try:
        res = _board(out, int(season), int(week), rows, kalshi_idx, schedule, ctx, coefs, lines, with_market, now,
                     min_pts, only_from_ts, only_to_ts)
    except Exception as e:                  # a board that cannot build says so; it never takes the route down
        errlog.note("NFLM-board", e)
        out["error"] = f"{type(e).__name__}: {e}"
        res = out
    return _clean(_with_reason(res))


def _with_reason(b):
    """Any can't-build reason also goes in the slate-level `notes` (the
    contract's channel the tab draws above everything)."""
    notes = b.setdefault("notes", [])
    if b.get("error"):
        msg = "The NFL matchup board could not be built: " + str(b["error"])
        if msg not in notes:
            notes.insert(0, msg)
    elif not b.get("players") and not any("no player" in n for n in notes):
        notes.insert(0, "The NFL matchup board has no player for this slate (no Sleeper projection in its games).")
    return b


def _board(out, season, week, rows, kalshi_idx, schedule, ctx, coefs, lines, with_market, now, min_pts, only_from_ts,
           only_to_ts=None):
    import datetime
    import kalshi_nfl
    coefs = coefs if coefs is not None else load_coefs()
    if not coefs:
        out["error"] = "no committed model (research/data/nfl_matchup_model/coefs.json)"
        return out
    ship = shipped_markets(coefs)
    out["notes"] = slate_notes(coefs)
    out["model"] = {"shipped": coefs.get("shipped"), "dfs_hook": coefs.get("dfs_hook"), "fit_seasons": coefs.get("fit_seasons"),
                    "validation_accepted": coefs.get("validation_accepted"), "prereg_sha256": coefs.get("prereg_sha256"),
                    "markets_with_weight": sorted(ship),
                    "note": ("A factor's effect is the shipped model's own contribution; a factor that did not pass the "
                             "pre-registered test prints with effect 0 (research/reports/nfl_matchup_model.md).")}
    if rows is None:
        import nfl_dfs_sim
        rows = nfl_dfs_sim._get(_SLEEPER_PROJ.format(season=season, week=week)) or []
    if ctx is None:
        ctx = production_ctx(season)
    ids = _ids() if ctx is not None else {}
    sl2g = ids.get("sleeper2gsis") or {}
    if schedule is None:
        try:
            import nfl_live
            schedule = nfl_live.schedule(week, season) or []
        except Exception as e:
            errlog.note("NFLM-board-schedule", e)
            schedule = []
    idx_ts = now
    if with_market and kalshi_idx is None:
        kalshi_idx = kalshi_nfl.index() or {}
        idx_ts = kalshi_nfl._cache.get("ts") or now      # the index's build time = the quotes' age
    sched = {}
    for g in schedule:
        h, a = kalshi_nfl._canon(g.get("home")), kalshi_nfl._canon(g.get("away"))
        sched[frozenset((h, a))] = g
    # Sleeper rows -> players
    players = []
    qb1 = {}
    for r in rows:
        st = r.get("stats") or {}
        pl = r.get("player") or {}
        pos = pl.get("position")
        if pos not in POS or not r.get("team") or not r.get("opponent"):
            continue
        if float(st.get("pts_ppr") or 0.0) < min_pts:
            continue
        base = {k: float(st.get(k) or 0.0) for k in ("rec_tgt", "rec", "rec_yd", "rec_td", "rush_att", "rush_yd", "rush_td",
                                                     "pass_att", "pass_cmp", "pass_yd", "pass_td", "pass_int", "fum_lost")}
        players.append((str(r.get("player_id")), r, pl, pos, base))
        att = base["pass_att"]
        if pos == "QB" and att > qb1.get(r["team"], (0.0, None))[0]:
            qb1[r["team"]] = (att, sl2g.get(str(r.get("player_id"))))
    games = {}
    for _pid, r, _pl, _pos, _b in players:
        t, o = kalshi_nfl._canon(r["team"]), kalshi_nfl._canon(r["opponent"])
        key = frozenset((t, o))
        if key in games:
            continue
        sg = sched.get(key) or {}
        home = kalshi_nfl._canon(sg.get("home")) if sg else None
        away = kalshi_nfl._canon(sg.get("away")) if sg else None
        suffix = None
        for sfx, e in (kalshi_idx or {}).items():
            if e.get("pair") == key:
                suffix = sfx
                break
        start = None
        if sg.get("date"):
            try:
                start = int(datetime.datetime.fromisoformat(sg["date"].replace("Z", "+00:00")).timestamp())
            except ValueError:
                start = None
        # Sleeper's game id (digits: the matchups route accepts only digits);
        # the Kalshi event suffix is kept beside it
        gid = str(r.get("game_id") or "")
        if not gid.isdigit():
            import zlib
            gid = str(zlib.crc32("-".join(sorted(key)).encode()) % 10 ** 9)     # stable across processes
        approx = None
        if start is None and r.get("date"):
            try:      # Sleeper's game date with a 1 pm ET start, for the date filter only (never shown as a kickoff)
                approx = int(datetime.datetime.fromisoformat(str(r["date"]) + "T17:00:00+00:00").timestamp())
            except ValueError:
                approx = None
        games[key] = {"game_id": gid, "home": home or t, "away": away or o,
                      "start_ts": start, "state": sg.get("state"), "suffix": suffix, "kalshi_event": suffix,
                      "_approx": approx}
    if only_from_ts is not None or only_to_ts is not None:
        lo_ts = only_from_ts if only_from_ts is not None else -1e18
        hi_ts = only_to_ts if only_to_ts is not None else 1e18
        games = {k: g for k, g in games.items()
                 if (g.get("start_ts") if g.get("start_ts") is not None else g.get("_approx")) is not None
                 and lo_ts <= (g["start_ts"] if g.get("start_ts") is not None else g["_approx"]) < hi_ts}
        players = [x for x in players if frozenset((kalshi_nfl._canon(x[1]["team"]), kalshi_nfl._canon(x[1]["opponent"]))) in games]
    out["games"] = [{k: v for k, v in g.items() if k not in ("suffix", "_approx")} for g in games.values()]
    # game lines (the team's expected margin and the total): Kalshi's own, de-vigged
    if lines is None:
        lines = game_lines([tuple(k) for k in games]) if with_market else {}
    out["notes"].extend(line_fallback_notes(games.values(), lines, coefs))
    # the as-of pieces
    defs, ranks, vecs, dh = {}, {}, {}, {}
    if ctx is not None:
        teams = sorted(ctx.stores[season]["teams"])
        for t in teams:
            try:
                defs[t] = defense_block(ctx, season, week, t)
            except (KeyError, TypeError, ValueError, ZeroDivisionError) as e:
                errlog.note("NFLM-board-def", e)
        ranks = _ranks(defs)
        vecs = def_vectors(ctx, season, week, teams)
        dh = def_history(ctx, season, week, save=getattr(ctx, "save_history", True))
        st = ctx.stores[season]
        played = [g for g in st["games"] if g["week"] < week]
        out["as_of"] = (f"nflverse pbp through {season} week {max(g['week'] for g in played)}" if played
                        else f"prior seasons only ({season - 1} and {season - 2})")
        out["provenance"] = [
            {"source": "nflverse-data play-by-play, participation (CC BY 4.0)", "covers": f"{season - BACK}-{season}",
             "ts": int(st.get("built") or 0), "licence": "CC BY 4.0"},
            {"source": "FTN Data via nflverse (CC BY-SA 4.0)", "covers": "charting 2022-", "ts": int(st.get("built") or 0),
             "licence": "CC BY-SA 4.0"},
            {"source": "Sleeper weekly projections (live)", "covers": f"{season} week {week}", "ts": int(now),
             "licence": "Sleeper API, non-commercial"},
            {"source": "Kalshi player props (kalshi_nfl index)", "covers": "listed lines", "ts": int(now),
             "licence": "Kalshi developer terms"}]
    idx = kalshi_idx or {}
    for pid, r, pl, pos, base in players:
        team_s, opp_s = r["team"], r["opponent"]
        key = frozenset((kalshi_nfl._canon(team_s), kalshi_nfl._canon(opp_s)))
        g = games.get(key) or {}
        team, opp = to_nflverse(team_s), to_nflverse(opp_s)
        gsis = sl2g.get(pid)
        notes = []
        f, sel, hist = None, [], []
        if ctx is not None and gsis and opp in defs:
            try:
                hist = history(ctx, season, week, gsis, vecs.get(opp), dh)
                comps, sel = comparables(ctx, season, week, gsis, vecs.get(opp), dh, games=hist)
                f = features(ctx, season, week, gsis, team, opp, pos, margin=(lines.get(team) or {}).get("margin"),
                             total=(lines.get(team) or {}).get("total"), qb=(qb1.get(team_s) or (0, None))[1], comps=comps)
            except (KeyError, TypeError, ValueError, ZeroDivisionError) as e:
                errlog.note("NFLM-board-player", e)
                f = None
        if f is None:
            notes.append("no nflverse profile for this player (rookie, unmapped id or no store): Sleeper's line only")
        mk = player_markets(coefs, f, pos, base)
        gstats = [game_stats(ctx.P, ln) for _w, _s, _r, ln, _v in hist] if hist else None
        markets = []
        kl = _kalshi_lines(idx, g.get("suffix"), ts=idx_ts) if g.get("suffix") else {}
        nm = kalshi_nfl._norm(f"{pl.get('first_name', '')} {pl.get('last_name', '')}")
        for m, blk in mk.items():
            if blk.get("dist") is None:
                continue
            ks_market = kl.get((KALSHI_STAT.get(m), nm), {}) if m in KALSHI_STAT else {}
            ks = sorted(set(THRESHOLDS.get(m, ())) | set(ks_market))
            se = (blk.get("adj") or {}).get("se") or 0.0
            if m == "dk_fp":
                # no fitted coefficient sets the DK points spread's level: no band
                ladder = {k: (p, None, None) for k, p in blk["dist"].ladder(ks).items()}
            elif m == "td":
                import prop_dist as PD
                ladder = probs_with_band(lambda mu: PD.Poisson(mu), blk["adjusted"], se, ks)
            else:
                ladder = probs_with_band(lambda mu, m=m: market_dist(coefs, m, mu, pos, base), blk["adjusted"], se, ks)
            sname = STAT_NAME[m]
            shift = matchup_shift(blk.get("adj")) if m in MARKETS else 0.0
            lines_out = []
            for k in ks:
                if k not in ladder:
                    continue
                p, lo, hi = ladder[k]
                h_all, h_rec, h_sim = _hits(hist, sname, k, stats=gstats)
                mkt = ks_market.get(k)
                val, labels, fside = line_labels(p, lo, hi, mkt, shift, (h_sim or {}).get("n_eff"), now=now)
                lines_out.append({"k": k, "p": _r(p), "p_lo": _r(lo), "p_hi": _r(hi), "hit_all": h_all,
                                  "hit_recent": h_rec, "hit_similar_def": h_sim, "market": mkt, "value": val,
                                  "labels": labels, "fit_side": fside})
            markets.append({"stat": sname, "baseline_mean": _r(blk["baseline"], 3), "baseline_source": "sleeper",
                            "adjusted_mean": _r(blk["adjusted"], 3), "dist": blk["dist"].to_dict(),
                            "matchup_weight": "shipped" if m in ship else "0", "matchup_shift": _r(shift, 4),
                            "lines": lines_out})
        if f is not None:
            if pos in ("WR", "TE", "RB"):
                notes.append("coverage splits are prior seasons only: nflverse publishes a season's participation "
                             "after its postseason, so no 2026 coverage charting exists in season")
                notes.append("the middle-third target share is an alignment PROXY, not charted slot / wide alignment")
            if not ship:
                notes.append("no matchup factor passed the pre-registered test: every market is Sleeper's line with "
                             "the fitted dispersion; factors print with effect 0")
        status = STATUS.get(pl.get("injury_status"), "unknown")
        out["players"].append({
            "player_id": pid, "name": f"{pl.get('first_name', '')} {pl.get('last_name', '')}".strip(), "team": team_s,
            "opp": opp_s, "game_id": g.get("game_id"), "pos": pos,
            "status": {"value": status, "source": "Sleeper injury_status", "ts": int(now)},
            "role": role_block(f, pos), "opponent_profile": opponent_block(defs.get(opp) or {}, pos, ranks.get(opp) or {}),
            "factors": factor_rows(f, mk, pos, coefs) if f is not None else [],
            "markets": markets,
            "comparables": [{"date": f"{c['season']} week {c['week']}", "opp": c["opp"], "similarity": c["similarity"],
                             "weight": c["weight"], "stats": c["stats"], "why": c["why"]} for c in sel],
            "notes": notes})
    return out


def board_for_date(date_iso, now=None):
    """The matchups tab's NFL board for an owner's (Mountain-time) day: the
    regular-season week holding the first game on or after that day, its
    games from that day on (matchups.produce calls this in its child)."""
    import datetime
    import clock
    import nfl_live
    d = datetime.date.fromisoformat(date_iso)
    season = d.year if d.month >= 3 else d.year - 1
    start = datetime.datetime.combine(d, datetime.time(0, 0), tzinfo=clock.MT).timestamp()
    for wk in range(1, 19):
        try:
            sch = nfl_live.schedule(wk, season) or []
        except Exception as e:
            errlog.note("NFLM-week", e, path=f"wk{wk}")
            continue
        ts = []
        for g in sch:
            try:
                ts.append(datetime.datetime.fromisoformat((g.get("date") or "").replace("Z", "+00:00")).timestamp())
            except ValueError:
                continue
        if ts and max(ts) >= start:
            end = start + 86400
            on_day = [t for t in ts if start <= t < end]
            # the date's games when it is a game day, else the rest of the week
            b = board(season, wk, schedule=sch, now=now, only_from_ts=start,
                      only_to_ts=end if on_day else None)
            b["week"], b["season"] = wk, season
            b["slate"] = "the games on " + date_iso if on_day else f"week {wk} from {date_iso} on (no game that day)"
            return b
    return _clean(_with_reason({"sport": "nfl", "season": season, "week": None,
                                "built_ts": int(time.time() if now is None else now), "as_of": None, "provenance": [],
                                "games": [], "players": [], "notes": [],
                                "error": f"no regular-season NFL week has a game on or after {date_iso}"}))
