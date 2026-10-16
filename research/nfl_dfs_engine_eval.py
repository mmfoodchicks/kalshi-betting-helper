"""The game-engine DFS worlds (nfl_dfs_engine) against the two models already
on the boards, replayed on a past season, game by game. RESEARCH.

Every regular-season game of week >= 2 is simulated by all three models on
the same inputs:

  legacy       nfl_dfs_sim.simulate_game (production for the boards today),
               unseeded, points rescaled to Sleeper; its defense from the
               component model shifted to Sleeper's DEF projection, its kicker
               off the offence's draws (nfl_dfs_sim.player_pool's own steps)
  constrained  nfl_dfs_csim.simulate_game with the frozen fit (2022-2024),
               child-seeded; the same defense and kicker steps
  engine       nfl_dfs_engine.simulate_game: the deep engine plays every
               world (tables REFIT on the seasons before the one scored,
               profiles from that season's games before the week, the prior
               season's units as the prior, levels anchored to the points-
               differential ratings at weight 1 -- PD stands in for Kalshi's
               win totals, which have no history), its level blended with the
               drive model's expected points (nfl_game_sim, on the week's
               Sleeper team projections from the player table, the week's
               kicker projections and the opponents' points allowed to date),
               the box dealt by the frozen allocation parameters

and scored against what happened: the seven offensive roles exactly as Stage
2E scored them (PIT, coverage, CRPS, the role-pair correlations against the
observed ones, stacks), plus the defenses and the kickers against Sleeper's
own box scores (DraftKings points through dfslog's scorer).

What is NOT replayed: the deep layer's weekly roster factor (it reads the
whole season's Sleeper projections; the drive model's 70% of the level
carries this week's personnel anyway), and Kalshi (PD instead). The deep
engine's two global constants (_FORM_SD, _HFA_YDS) were calibrated on
2023-2025 margins and home win rates; they are game-level and are stated,
not hidden.

Development season 2024 (allocation params fit on 2022-2024 -- in-sample for
the constrained model, which flatters it there); the blind test is 2025.

THE RULES, written 2026-10-16 before the engine model has been run on any 2025
game (2025's outcomes were opened once already, for Stage 2E's legacy-vs-
constrained test; this is the engine's first contact with them):

  Choosing ENV_SD (2024 only). The value on the grid 0 / 0.05 / 0.08 / 0.11 /
  0.14 with the smallest RMSE over the cross-team pairs (`opp_pair_rmse`),
  among those whose offensive CRPS (mean over the seven roles) is within 0.5%
  of the grid's env-0 value. Frozen in nfl_dfs_engine.ENV_SD before 2025 runs.

  Promotion (2025, weeks 2-18, at the frozen ENV_SD). The engine becomes the
  boards' model (dfs_tourney.BOARD_MODEL = "engine") only if ALL hold:
    (a) offensive calibration: CRPS mean over the seven roles <= 1.01 x the
        constrained model's (the better of the two on 2025 in Stage 2E);
    (b) structure: role-pair correlation RMSE against observed 2025 below
        BOTH legacy's and constrained's;
    (c) PIT: pooled Kolmogorov distance <= max(0.05, constrained's + 0.01);
    (d) defenses and kickers: CRPS <= 1.02 x legacy's, each (legacy's
        defense and kicker models are production).
  Any link failing keeps BOARD_MODEL at "legacy" and the engine an alternate,
  with the failing link in the report. Nothing is tuned on 2025.

  python3 -m research.nfl_dfs_engine_eval <pbp store dir> --season 2024 [--env 0,0.05,0.08] [--k 400]
      [--workers 4] [--out research/data/nfl_dfs_engine_2024.json]
"""
import collections
import gzip
import json
import multiprocessing as mp
import os
import pickle
import sys
import time
import zlib

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from research import hist_data as H            # noqa: E402
from research import csim_fit as F             # noqa: E402
from research import seeds as SEEDS            # noqa: E402

DATA = os.path.join(ROOT, "research", "data")
FEEDS = os.path.join(DATA, "feeds")
BASE_SEED = 20261016
K_DEFAULT = 400
FROM_WEEK = 2
DST_ROLES = ("DST", "K")


# ---- Sleeper's kicker and defense feeds, trimmed and kept --------------------
_KEEP_K = ("fgm", "xpm", "xpa", "fgm_0_19", "fgm_20_29", "fgm_30_39", "fgm_40_49", "fgm_50p", "fgm_50_59",
           "fgm_60p", "pts_ppr", "pts_std")
_KEEP_D = ("pts_ppr", "pts_allow", "sack", "int", "fum_rec", "def_td", "def_st_td", "st_td", "safe", "blk_kick",
           "def_2pt")


def _trim(rows, keep):
    out = []
    for r in rows or []:
        p = r.get("player") or {}
        out.append({"team": r.get("team"), "opp": r.get("opponent"), "pos": p.get("position"),
                    "name": f"{p.get('first_name', '')} {p.get('last_name', '')}".strip(),
                    "last": p.get("last_name"),
                    "stats": {k: v for k, v in (r.get("stats") or {}).items() if k in keep}})
    return out


def kdef_feeds(season, weeks=range(1, 19)):
    """{week: {proj_k, proj_def, stats_k, stats_def}} from Sleeper, cached in
    research/data/feeds/sleeper_kdef_<season>.json.gz (small, committed, so
    the record does not depend on Sleeper keeping its archive)."""
    path = os.path.join(FEEDS, f"sleeper_kdef_{season}.json.gz")
    if os.path.exists(path):
        with gzip.open(path, "rt") as fh:
            return {int(k): v for k, v in json.load(fh).items()}
    import nfl_dfs_sim
    out = {}
    for w in weeks:
        got = {}
        for kind, base in (("proj", "projections"), ("stats", "stats")):
            for pos, tag, keep in (("K", "k", _KEEP_K), ("DEF", "def", _KEEP_D)):
                url = (f"https://api.sleeper.com/{base}/nfl/{season}/{w}?season_type=regular"
                       f"&position[]={pos}&order_by=pts_ppr")
                got[f"{kind}_{tag}"] = _trim(nfl_dfs_sim._get(url), keep)
        out[w] = got
        time.sleep(0.2)
    os.makedirs(FEEDS, exist_ok=True)
    with gzip.open(path, "wt") as fh:
        json.dump({str(k): v for k, v in out.items()}, fh, separators=(",", ":"), sort_keys=True)
    return out


def kicker_line(st):
    """nfl_dfs_sim.kicker_projections' line from one kicker's projection stats
    (the same arithmetic: the distance buckets are trusted over fgm)."""
    import dk_scoring
    fg = [(dk_scoring.NFL_K["fg_0_39"], sum(float(st.get(k) or 0.0) for k in ("fgm_0_19", "fgm_20_29", "fgm_30_39"))),
          (dk_scoring.NFL_K["fg_40_49"], float(st.get("fgm_40_49") or 0.0)),
          (dk_scoring.NFL_K["fg_50p"], float(st.get("fgm_50p") or 0.0))]
    xpm = float(st.get("xpm") or 0.0)
    xpa = float(st.get("xpa") or 0.0) or xpm
    pts = sum(v * m for v, m in fg) + dk_scoring.NFL_K["xp"] * xpm
    return {"fg": fg, "xpm": xpm, "xpa": xpa, "pts": round(pts, 2)} if pts > 0 else None


def kicker_actual(st):
    """DraftKings points for a kicker's real line (Sleeper stats)."""
    import dk_scoring
    K = dk_scoring.NFL_K
    g = lambda k: float(st.get(k) or 0.0)            # noqa: E731
    long_ = g("fgm_50p") if st.get("fgm_50p") is not None else g("fgm_50_59") + g("fgm_60p")
    return (K["fg_0_39"] * (g("fgm_0_19") + g("fgm_20_29") + g("fgm_30_39")) + K["fg_40_49"] * g("fgm_40_49")
            + K["fg_50p"] * long_ + K["xp"] * g("xpm"))


# ---- the season's store and the week's inputs ---------------------------------
def _load_store(store_dir, season):
    with open(os.path.join(store_dir, f"nfl_pbp_{season}.pkl"), "rb") as fh:
        st = pickle.load(fh)
    return st["payload"] if "payload" in st else st


def _espn(t):
    import nfl_dfs_sim
    return nfl_dfs_sim.espn_abbr(t)


def week_profiles(store, season, week, T):
    """The deep layer's profiles for `week`: S's games before it, anchored to
    the PD ratings at weight 1 (research.nfl_deep_validate's anch_full arm)."""
    import nfl_deep_data as D
    import nfl_season
    from research import nfl_deep_validate as V
    sub = V._subset(store, week)
    rt = V._pd_rating(store, week)
    base, _q = D.build_profiles(season, store=sub, T=T, anchor=False)
    pd_margin = {ab: (r - 8.5) * nfl_season._PTS_PER_WIN for ab, r in rt.items()}
    orig_mm, orig_k = D.market_margins, D._ANCHOR_PRIOR_GAMES
    try:
        D.market_margins = lambda _s, recs=None: (dict(pd_margin), "pd_ratings")
        D._ANCHOR_PRIOR_GAMES = None
        pr = {ab: dict(p) for ab, p in base.items()}
        D.apply_anchor(pr, season, T)
    finally:
        D.market_margins, D._ANCHOR_PRIOR_GAMES = orig_mm, orig_k
    return pr


def points_allowed(store, week):
    """{ESPN abbr: (points allowed per game, games)} from S's games before `week`."""
    pa, g = collections.Counter(), collections.Counter()
    for r in store["games"].values():
        if r.get("st") != 2 or r.get("week", 99) >= week:
            continue
        pa[r["home"]] += r["away_pts"]; g[r["home"]] += 1
        pa[r["away"]] += r["home_pts"]; g[r["away"]] += 1
    return {ab: (pa[ab] / g[ab], g[ab]) for ab in g}


def drive_profile(abbr, players, k_line, home, pa_tab):
    """nfl_data.week_teams' profile rebuilt from the player table's projections
    (the same sums, the same 14 posted lines, the same points formula)."""
    import nfl_data
    keys = ("pass_yd", "pass_td", "pass_int", "rush_yd", "rush_td", "rec", "rec_yd", "rec_td", "fum_lost")
    e = {k: 0.0 for k in keys} | {"fgm": 0.0, "xpm": 0.0}
    rows = []
    for p in players:
        r = p.get("row") or {}
        if p.get("actual_only") or (p.get("proj_pts") or 0) <= 0.5:
            continue
        row = {"name": p["name"], "pos": p["pos"]}
        for k in keys:
            v = float(r.get(f"proj_{k}") or 0.0)
            e[k] += v
            row[k] = round(v, 2)
        row["int"] = row.pop("pass_int")
        row["fum"] = row.pop("fum_lost")
        rows.append(row)
    if k_line:
        e["fgm"] = sum(m for _v, m in k_line["fg"])
        e["xpm"] = k_line["xpm"]
    tds = e["pass_td"] + e["rush_td"]
    xp = e["xpm"] if e["xpm"] > 0 else 0.97 * tds
    rows.sort(key=lambda p: -(p["pass_yd"] + p["rush_yd"] + p["rec_yd"]))
    prof = {"abbr": _espn(abbr), "home": home, "exp": e, "players": rows[:14],
            "points": round(6.0 * tds + xp + 3.0 * e["fgm"] + nfl_data._DST_PTS, 2)}
    # each club carries its OWN points allowed (nfl_data.week_teams); the
    # engine rates the other offence against it
    vals = [v for v, _g in pa_tab.values()]
    if vals and _espn(abbr) in pa_tab:
        lg = sum(vals) / len(vals)
        prof["lg_pa_pg"] = round(lg, 3)
        prof["def_pa_pg"] = nfl_data.shrink_pa(pa_tab[_espn(abbr)][0], lg, pa_tab[_espn(abbr)][1])
    return prof


def drive_expectation(g, k_by_team, pa_tab, season, week):
    """(home pts, away pts) from the drive model on the table's projections."""
    import nfl_game_sim
    h, a = g["home"], g["away"]
    ph = drive_profile(h, [p for p in g["players"] if p["team"] == h], k_by_team.get(h), True, pa_tab)
    pa_ = drive_profile(a, [p for p in g["players"] if p["team"] == a], k_by_team.get(a), False, pa_tab)
    sim = nfl_game_sim.simulate_game(ph, pa_, n=2400, seed=zlib.crc32(f"{season}|{week}|{h}|{a}".encode()))
    return float(sim["exp_home"]), float(sim["exp_away"])


def home_away(store, week, teams):
    """(home, away, neutral) in the table's (Sleeper) codes from the store."""
    want = {_espn(t): t for t in teams}
    ev = {e["id"]: e for e in store.get("events") or []}
    for r in store["games"].values():
        if r.get("st") == 2 and r.get("week") == week and {r["home"], r["away"]} == set(want):
            return want[r["home"]], want[r["away"]], bool((ev.get(r["id"]) or {}).get("neutral") or r.get("neutral"))
    return None


# ---- one week, every model ------------------------------------------------------
_W = {}


def _init(store_dir, season, fit_seasons, k, envs):
    import nfl_deep_sim as E
    from research import nfl_deep_fit as NF
    stores = NF.load_stores(store_dir, fit_seasons)
    _W["T"] = E.Tables(NF.build_tables(stores, prior_season=max(fit_seasons)))
    _W["store"] = _load_store(store_dir, season)
    _W["season"], _W["k"], _W["envs"] = season, k, envs
    rows = H.read_csv_gz(os.path.join(DATA, "player_weeks_2022_2025.csv.gz"))
    _W["games"] = F.games_from_table(rows, (season,))
    _W["feeds"] = kdef_feeds(season)


def _legacy_side(sim_team_def, teams, dst_proj, k_lines, K, rng, team_off_lists):
    """nfl_dfs_sim.player_pool's defense and kicker steps for one game."""
    import nfl_dfs_sim as S
    dst, kick = {}, {}
    for me in teams:
        opp = next(t for t in teams if t != me)
        d = sim_team_def[opp]
        arr = S._dst_from_components(list(d["td"]), list(d["gv"]), list(d["yd"]), list(d["pa"]), rng)
        if me in dst_proj:
            shift = dst_proj[me] - sum(arr) / len(arr)
            arr = [max(-4.0, x + shift) for x in arr]
        dst[me] = np.asarray(arr, dtype=np.float32)
        if k_lines.get(me):
            ka = S._kicker_arr(k_lines[me], team_off_lists[me], K, rng)
            if ka:
                kick[me] = np.asarray(ka, dtype=np.float32)
    return dst, kick


def _week(w):
    import random as _r
    import nfl_dfs_csim as C
    import nfl_dfs_engine as G
    import nfl_dfs_sim as S
    T, store, season, K = _W["T"], _W["store"], _W["season"], _W["k"]
    feeds = _W["feeds"].get(w) or {}
    k_by_team = {}
    for r in feeds.get("proj_k") or []:
        kl = kicker_line(r["stats"])
        if kl and r["team"] and r["team"] not in k_by_team:
            k_by_team[r["team"]] = kl
    dst_proj = {r["team"]: float(r["stats"].get("pts_ppr") or 0.0) for r in feeds.get("proj_def") or [] if r.get("team")}
    dst_act = {}
    for r in feeds.get("stats_def") or []:
        if r.get("team"):
            import dfslog
            dst_act[r["team"]] = dfslog._nfl_points("DEF", r["stats"])
    k_act = {}
    for r in feeds.get("stats_k") or []:          # highest-scoring kicker first (order_by)
        st_ = r.get("stats") or {}
        if r.get("team") and (st_.get("xpm") is not None or st_.get("fgm") is not None):
            k_act.setdefault(r["team"], kicker_actual(st_))
    profiles = week_profiles(store, season, w, T)
    pa_tab = points_allowed(store, w)
    out = []
    for key, g in _W["games"].items():
        if g["week"] != w:
            continue
        ha = home_away(store, w, g["teams"])
        if not ha:
            continue
        g = dict(g, home=ha[0], away=ha[1], neutral=ha[2])
        teams = [ha[0], ha[1]]
        try:
            drive = drive_expectation(g, k_by_team, pa_tab, season, w)
        except Exception as e:
            print(f"[eval] {key}: drive model failed ({type(e).__name__}: {e})", flush=True)
            drive = (None, None)
        rec = {"key": key, "season": season, "week": w, "home": ha[0], "away": ha[1], "neutral": ha[2],
               "players": [{k: p.get(k) for k in ("name", "pos", "team", "opp", "role", "proj_pts", "player_id",
                                                  "actual_only")} for p in g["players"]],
               "drive": drive, "dst_proj": {t: dst_proj.get(t) for t in teams},
               "dst_act": {t: dst_act.get(t) for t in teams}, "k_act": {t: k_act.get(t) for t in teams},
               "k_proj": {t: (k_by_team.get(t) or {}).get("pts") for t in teams}, "models": {}}
        # ---- legacy (production)
        leg = S.simulate_game({"label": g["label"], "teams": g["teams"],
                               "players": [p for p in g["players"] if not p.get("actual_only")]}, n=K, with_samples=True)
        by = {p["name"]: p for p in leg["players"]}
        lpts = np.zeros((len(g["players"]), K), dtype=np.float32)
        for i, p in enumerate(g["players"]):
            if not p.get("actual_only") and p["name"] in by:
                lpts[i] = by[p["name"]]["arr"]
        lrng = _r.Random(SEEDS.child_seed(BASE_SEED, season, w, key[2], "legacy-sides"))
        toff = {t: {"td": leg["team_def"][t]["td"], "yd": leg["team_def"][t]["yd"]} for t in g["teams"]}
        ldst, lk = _legacy_side(leg["team_def"], g["teams"], dst_proj, k_by_team, K, lrng, toff)
        rec["models"]["legacy"] = {"pts": lpts, "dst": ldst, "k": lk}
        # ---- constrained (frozen fit)
        cs = C.simulate_game(g, n=K, rng=np.random.default_rng(SEEDS.child_seed(BASE_SEED, season, w, key[2], "constrained")))
        cpts = np.asarray([p["arr"] for p in cs["players"]], dtype=np.float32)
        ctd = {t: {k: list(v) for k, v in d.items()} for t, d in cs["team_def"].items()}
        crng = _r.Random(SEEDS.child_seed(BASE_SEED, season, w, key[2], "constrained-sides"))
        cdst, ck = _legacy_side(ctd, g["teams"], dst_proj, k_by_team, K, crng,
                                {t: {"td": ctd[t]["td"], "yd": ctd[t]["yd"]} for t in g["teams"]})
        rec["models"]["constrained"] = {"pts": cpts, "dst": cdst, "k": ck}
        # ---- the engine, at each environment value on the same seed
        for env in _W["envs"]:
            ctx = {"T": T, "profiles": profiles, "drive": {ha[0]: drive[0], ha[1]: drive[1]}, "env_sd": env,
                   "workers": 1}
            es = G.simulate_game(g, K, np.random.default_rng(SEEDS.child_seed(BASE_SEED, season, w, key[2], "engine")), ctx)
            if es is None:
                continue
            epts = np.asarray([p["arr"] for p in es["players"]], dtype=np.float32)
            rec["models"][f"engine_{env:g}"] = {"pts": epts, "dst": {t: es["dst"][t].astype(np.float32) for t in teams},
                                                "k": {t: es["k"][t].astype(np.float32) for t in teams},
                                                "anchor": {k: es["anchor"].get(k) for k in ("deep", "target", "landed", "box", "corr_pts")}}
        out.append(rec)
    return w, out


def run(store_dir, season, envs=(0.0,), k=K_DEFAULT, workers=4, log=print):
    fit = [s for s in (2023, 2024, 2025) if s < season]
    t0 = time.time()
    weeks = list(range(FROM_WEEK, 19))
    with mp.Pool(workers, initializer=_init, initargs=(store_dir, season, fit, k, list(envs))) as pool:
        res = []
        for w, rows in pool.imap_unordered(_week, weeks):
            res.extend(rows)
            log(f"[eval] {season} week {w}: {len(rows)} games ({time.time() - t0:.0f}s)")
    res.sort(key=lambda r: (r["week"], r["key"][2]))
    return res, {"season": season, "fit_seasons": fit, "k": k, "envs": list(envs), "base_seed": BASE_SEED,
                 "from_week": FROM_WEEK, "seconds": round(time.time() - t0, 1)}


# ---- scoring ----------------------------------------------------------------------
def samples(res, model):
    """Stage 2E's sample and observed-sample shapes for one model."""
    rows = H.read_csv_gz(os.path.join(DATA, "player_weeks_2022_2025.csv.gz"))
    games = F.games_from_table(rows, ({r["season"] for r in res} or {0}))
    sample, obs = [], []
    ob_all = {gm["key"]: gm for gm in F.observed_sample({r["key"]: games[r["key"]] for r in res if r["key"] in games})}
    for r in res:
        m = r["models"].get(model)
        g = games.get(r["key"])
        if m is None or g is None:
            continue
        sample.append({"key": r["key"], "players": g["players"], "teams": g["teams"], "pts": m["pts"],
                       "comps": {c: np.zeros_like(m["pts"]) for c in F.COMPS}, "K": m["pts"].shape[1]})
        obs.append(ob_all[r["key"]])
    return sample, obs


def crps_pit(xs, ys):
    """CRPS and PIT of real values `ys` in simulated samples `xs`."""
    pits, crps = [], []
    for x, y in zip(xs, ys):
        x = np.sort(np.asarray(x, dtype=np.float64))
        K = len(x)
        lo, hi = np.searchsorted(x, y, side="left"), np.searchsorted(x, y, side="right")
        pits.append((lo + 0.5 * (hi - lo)) / K)
        i1 = np.arange(1, K + 1)
        crps.append(float(np.abs(x - y).mean() - 0.5 * (2.0 / (K * K)) * np.sum((2 * i1 - K - 1) * x)))
    u = np.asarray(pits)
    ks = float(np.max(np.abs(np.sort(u) - (np.arange(1, len(u) + 1) - 0.5) / len(u)))) if len(u) else None
    return {"n": len(u), "crps": float(np.mean(crps)) if crps else None, "pit_ks": ks,
            "pit_hist": [round(float(h), 3) for h in np.histogram(u, bins=10, range=(0, 1))[0] / max(1, len(u))],
            "mean_sim": float(np.mean([np.mean(x) for x in xs])) if xs else None,
            "mean_obs": float(np.mean(ys)) if ys else None}


def score_sides(res, model):
    out = {}
    for kind in ("dst", "k"):
        xs, ys = [], []
        for r in res:
            m = r["models"].get(model)
            if not m:
                continue
            for t in (r["home"], r["away"]):
                y = (r["dst_act"] if kind == "dst" else r["k_act"]).get(t)
                x = m[kind].get(t)
                if y is None or x is None:
                    continue
                xs.append(x); ys.append(float(y))
        out[kind] = crps_pit(xs, ys)
    return out


def score(res, models):
    from research import stage2e_validate as V
    from research import stage2e as E2
    out = {"calibration": {}, "sides": {}, "moments": {}}
    obs_m = None
    for mname in models:
        s, ob = samples(res, mname)
        if not s:
            continue
        if obs_m is None:
            obs_m = F.moments(ob)
            games = {gm["key"]: None for gm in s}
            out["n_games"] = len(games)
        out["calibration"][mname] = V.pit_and_crps(s, ob)
        out["moments"][mname] = F.moments(s)
        out["sides"][mname] = score_sides(res, mname)
    rows = H.read_csv_gz(os.path.join(DATA, "player_weeks_2022_2025.csv.gz"))
    games = F.games_from_table(rows, ({r["season"] for r in res}))
    scales = E2.bootstrap_scales({r["key"]: games[r["key"]] for r in res if r["key"] in games},
                                 np.random.default_rng(BASE_SEED), boots=200)
    pairs, rmse = V.pair_table(obs_m, out["moments"], scales)
    out["pairs"], out["pair_rmse"], out["observed"] = pairs, rmse, {k: v for k, v in obs_m.items() if k.startswith("pair ")}
    # cross-team pairs alone: the coupling the engine exists to carry
    opp_keys = [r["moment"] for r in pairs if "opp" in r["moment"] or "offense" in r["moment"]]
    out["opp_pair_rmse"] = {m: (float(np.sqrt(np.mean([(row[m] - row["obs"]) ** 2 for row in pairs
                                                        if row["moment"] in opp_keys and row.get(m) is not None])))
                                if opp_keys else None) for m in out["moments"]}
    # player means against Sleeper and against what happened, by role
    means = {}
    for mname in models:
        s, ob = samples(res, mname)
        acc = collections.defaultdict(lambda: [0.0, 0.0, 0.0, 0])
        for gm, o in zip(s, ob):
            for i, p in enumerate(gm["players"]):
                rl = p.get("role") or ""
                if rl in F.ROLES and not np.isnan(o["pts"][i, 0]):
                    a = acc[rl]
                    a[0] += float(gm["pts"][i].mean()); a[1] += float(p.get("proj_pts") or 0.0); a[2] += float(o["pts"][i, 0]); a[3] += 1
        means[mname] = {rl: {"sim": round(a[0] / a[3], 3), "sleeper": round(a[1] / a[3], 3), "actual": round(a[2] / a[3], 3),
                             "n": a[3]} for rl, a in acc.items() if a[3]}
    out["means"] = means
    return out


GRID = (0.0, 0.05, 0.08, 0.11, 0.14)
# the production modules this evaluation runs that research.provenance does
# not hash; stamped separately
ENGINE_SOURCES = ("nfl_dfs_engine.py", "nfl_deep_sim.py", "nfl_deep_data.py", "nfl_game_sim.py", "nfl_data.py",
                  "research/data/nfl_deep_tables.json")


def engine_sources_sha():
    import hashlib
    h = hashlib.sha256()
    for rel in ENGINE_SOURCES:
        with open(os.path.join(ROOT, rel), "rb") as fh:
            h.update(rel.encode() + b"\0" + fh.read())
    from research import provenance
    return {"sha256": h.hexdigest()[:16], "files": list(ENGINE_SOURCES), "commit": provenance.commit(),
            "dirty": bool(provenance._git("status", "--porcelain", "--", *ENGINE_SOURCES))}


def choose_env(sc):
    """The 2024 rule for ENV_SD (see the module docstring)."""
    cal = sc["calibration"]
    base = cal.get("engine_0", {}).get("_pooled", {}).get("crps_mean_over_roles")
    best = None
    for e in GRID:
        k = f"engine_{e:g}"
        if k not in cal or base is None:
            continue
        crps = cal[k]["_pooled"]["crps_mean_over_roles"]
        if crps > 1.005 * base:
            continue
        r = sc["opp_pair_rmse"].get(k)
        if r is not None and (best is None or r < best[1]):
            best = (e, r)
    return best[0] if best else 0.0


def gate(sc, env):
    """The 2025 promotion rule (see the module docstring): {link: (ok, detail)}."""
    k = f"engine_{env:g}"
    cal, pr, sd = sc["calibration"], sc["pair_rmse"], sc["sides"]
    e, c = cal[k]["_pooled"], cal["constrained"]["_pooled"]
    out = {"a_crps": (e["crps_mean_over_roles"] <= 1.01 * c["crps_mean_over_roles"],
                      f"engine {e['crps_mean_over_roles']:.4f} vs constrained {c['crps_mean_over_roles']:.4f} (limit x1.01)"),
           "b_pairs": (pr[k] < pr["constrained"] and pr[k] < pr["legacy"],
                       f"engine {pr[k]:.4f} vs constrained {pr['constrained']:.4f}, legacy {pr['legacy']:.4f}"),
           "c_pit": (e["pit_ks"] <= max(0.05, c["pit_ks"] + 0.01),
                     f"engine {e['pit_ks']:.4f} vs limit {max(0.05, c['pit_ks'] + 0.01):.4f}"),
           "d_dst": (sd[k]["dst"]["crps"] <= 1.02 * sd["legacy"]["dst"]["crps"],
                     f"engine {sd[k]['dst']['crps']:.4f} vs legacy {sd['legacy']['dst']['crps']:.4f} (limit x1.02)"),
           "d_k": (sd[k]["k"]["crps"] <= 1.02 * sd["legacy"]["k"]["crps"],
                   f"engine {sd[k]['k']['crps']:.4f} vs legacy {sd['legacy']['k']['crps']:.4f} (limit x1.02)")}
    out["promote"] = (all(v[0] for v in out.values()), "all links" if all(v[0] for v in out.values()) else
                      "failed: " + ", ".join(n for n, v in out.items() if not v[0]))
    return out


def main(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("store")
    ap.add_argument("--season", type=int, default=2024)
    ap.add_argument("--env", default="0")
    ap.add_argument("--k", type=int, default=K_DEFAULT)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", default=None)
    ap.add_argument("--raw", default=None, help="pickle the raw per-game results here")
    a = ap.parse_args(argv)
    envs = [float(x) for x in a.env.split(",") if x != ""]
    if a.season >= 2025:
        # the holdout may only be opened against a committed tree, and the
        # engine's own modules (outside provenance's hashed set) must be
        # committed too, so the frozen ENV_SD provably predates the reading
        from research import provenance
        provenance.require_clean("a holdout validation")
        dirty = provenance._git("status", "--porcelain", "--", *ENGINE_SOURCES)
        if dirty:
            raise RuntimeError(f"engine sources not committed: {dirty}")
        import nfl_dfs_engine
        if envs != [nfl_dfs_engine.ENV_SD]:
            raise RuntimeError(f"2025 runs at the frozen ENV_SD ({nfl_dfs_engine.ENV_SD}) only, not {envs}")
    res, meta = run(a.store, a.season, envs, a.k, a.workers)
    if a.raw:
        with open(a.raw, "wb") as fh:
            pickle.dump((res, meta), fh)
    models = ["legacy", "constrained"] + [f"engine_{e:g}" for e in envs]
    sc = score(res, models)
    sc["meta"] = meta
    from research import provenance
    sc["meta"]["provenance"] = provenance.stamp()
    sc["meta"]["engine_sources"] = engine_sources_sha()
    anchors = [r["models"][f"engine_{envs[0]:g}"]["anchor"] for r in res if f"engine_{envs[0]:g}" in r["models"]]
    sc["anchor"] = {"mean_abs_landing_err": round(float(np.mean([abs(a["landed"][0] - a["target"][0]) + abs(a["landed"][1] - a["target"][1])
                                                                 for a in anchors]) / 2), 3) if anchors else None,
                    "mean_target_minus_deep": round(float(np.mean([(a["target"][0] - a["deep"][0] + a["target"][1] - a["deep"][1]) / 2
                                                                   for a in anchors])), 3) if anchors else None}
    if a.out:
        with open(a.out, "w") as fh:
            json.dump(sc, fh, indent=1, sort_keys=True, default=float)
    return sc


if __name__ == "__main__":
    s = main(sys.argv[1:])
    for m, c in s["calibration"].items():
        print(m, "CRPS", round(c["_pooled"]["crps_mean_over_roles"], 4), "PIT KS", round(c["_pooled"]["pit_ks"], 4),
              "| DST", {k: (round(v, 3) if isinstance(v, float) else v) for k, v in s["sides"][m]["dst"].items() if k in ("crps", "pit_ks", "mean_sim", "mean_obs")},
              "| K", {k: (round(v, 3) if isinstance(v, float) else v) for k, v in s["sides"][m]["k"].items() if k in ("crps", "pit_ks", "mean_sim", "mean_obs")})
    print("pair RMSE", s["pair_rmse"], "\nopp-pair RMSE", s["opp_pair_rmse"])
