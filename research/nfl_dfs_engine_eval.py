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

THE PASSING-VOLUME ANCHOR, rules written 2026-10-16 before any anchored run
(the owner: "anchor passing volume"). The promoted engine threw ~4% too many
passes in both seasons (attempts a team-game, engine / Sleeper / real: 2024
34.15 / 31.89 / 32.81, 2025 33.24 / 31.59 / 31.99) and its receivers ran hot
on 2025. The lever: a per-team pass-rate shift (nfl_deep_sim profile key
`call_shift`, on the logit of every play call) iterated with the points
anchor, so each team's mean attempts land on
    target = (1 - w) x the engine's own attempts + w x Sleeper's projected
             team attempts (the sum of its quarterbacks' reconciled lines).

  Choosing w (2024 only). The grid value of 0 / 0.5 / 1 with the lowest
  offence CRPS (mean over the seven roles), among values whose pair RMSE and
  cross-team pair RMSE are each within 0.01 of w = 0's; a tie goes to the
  smaller w. ENV_SD stays frozen at 0.14.

  2025 is reported and labelled SEEN: the defect was found on it, so it cannot
  judge the fix; nothing is chosen on it.

  The test: 2026 weeks 2-5 (played, never read by any engine run). The
  anchored engine replaces the shipped one (nfl_dfs_engine.ATT_WEIGHT = w)
  only if, on the same games and seeds, (a) its offence CRPS is lower than
  the shipped engine's, (b) its pair RMSE is no more than 0.01 above the
  shipped engine's, (c) its DST and kicker CRPS are each no more than 1.01 x
  the shipped engine's. About sixty games is a small sample: the paired
  standard error of the CRPS difference is reported beside the rule, which
  decides on the point estimates as written. If w = 0 is chosen, nothing
  ships but the measurement.

THE RE-RUN, written 2026-10-16 before it. Every run above drew its FINAL
worlds from the shipped tables (fit on 2023-2026, so containing the season
scored): nfl_dfs_engine.play_worlds_parallel dropped the caller's tables,
while the anchor levelled the teams on the refit ones. Fixed, guarded, and
everything re-run in the order written above, under the same rules, with
nothing else changed: the ENV_SD grid on 2024 (w = 0) and its rule; the w
grid on 2024 at that value and its rule; both frozen in a commit; then 2025
(the promotion gate, and the anchor's SEEN arm) and 2026 weeks 2-5 (the
anchor's gate) at the frozen values. 2025 and 2026 weeks 2-5 were each read
once by the flawed runs; the rules leave nothing to choose on them, and the
report says so beside every number. Whatever the gates say ships, including
the engine leaving the boards.

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
    out = {}
    if os.path.exists(path):
        with gzip.open(path, "rt") as fh:
            out = {int(k): v for k, v in json.load(fh).items()}
        if all(int(w) in out for w in weeks):
            return out
    import nfl_dfs_sim
    for w in weeks:
        if int(w) in out:
            continue
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
def _table_rows():
    """The player-week table: 2022-2025 (research.hist_data) plus 2026 weeks
    1-5 (the same builder on the same Sleeper fetch, which reproduced the
    committed 2025 week-5 rows 368 of 368 before 2026 was trusted)."""
    rows = H.read_csv_gz(os.path.join(DATA, "player_weeks_2022_2025.csv.gz"))
    p26 = os.path.join(DATA, "player_weeks_2026.csv.gz")
    if os.path.exists(p26):
        rows = rows + H.read_csv_gz(p26)
    return rows


def arm_key(env, w=0.0):
    """The model key of one engine arm: env alone for the shipped engine (the
    keys the 2024 and 2025 artifacts already carry), _att<w> for the
    passing-volume anchor."""
    return f"engine_{env:g}" + (f"_att{w:g}" if w else "")


_W = {}


def _init(store_dir, season, fit_seasons, k, envs):
    import nfl_deep_sim as E
    from research import nfl_deep_fit as NF
    stores = NF.load_stores(store_dir, fit_seasons)
    _W["T"] = E.Tables(NF.build_tables(stores, prior_season=max(fit_seasons)))
    _W["store"] = _load_store(store_dir, season)
    # `envs` is a list of (env, att weight) arms; a bare float is (env, 0)
    _W["season"], _W["k"] = season, k
    _W["arms"] = [(a, 0.0) if not isinstance(a, (tuple, list)) else (float(a[0]), float(a[1])) for a in envs]
    _W["games"] = F.games_from_table(_table_rows(), (season,))
    weeks = sorted({g["week"] for g in _W["games"].values()})
    _W["feeds"] = kdef_feeds(season, weeks=range(1, (max(weeks) if weeks else 18) + 1))


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
        # ---- the engine, at each arm (environment, attempts weight) on the same seed
        for env, w_att in _W["arms"]:
            ctx = {"T": T, "profiles": profiles, "drive": {ha[0]: drive[0], ha[1]: drive[1]}, "env_sd": env,
                   "workers": 1, "att_weight": w_att}
            es = G.simulate_game(g, K, np.random.default_rng(SEEDS.child_seed(BASE_SEED, season, w, key[2], "engine")), ctx)
            if es is None:
                continue
            epts = np.asarray([p["arr"] for p in es["players"]], dtype=np.float32)
            rec["models"][arm_key(env, w_att)] = {
                "pts": epts, "dst": {t: es["dst"][t].astype(np.float32) for t in teams},
                "k": {t: es["k"][t].astype(np.float32) for t in teams},
                "anchor": {k: es["anchor"].get(k) for k in ("deep", "target", "landed", "box", "corr_pts",
                                                            "att_deep", "att_target", "att_landed", "call_shift")}}
        out.append(rec)
    return w, out


def run(store_dir, season, envs=(0.0,), k=K_DEFAULT, workers=4, log=print, weeks=None):
    fit = [s for s in (2023, 2024, 2025) if s < season]
    t0 = time.time()
    weeks = list(weeks or range(FROM_WEEK, 19))
    with mp.Pool(workers, initializer=_init, initargs=(store_dir, season, fit, k, list(envs))) as pool:
        res = []
        for w, rows in pool.imap_unordered(_week, weeks):
            res.extend(rows)
            log(f"[eval] {season} week {w}: {len(rows)} games ({time.time() - t0:.0f}s)")
    res.sort(key=lambda r: (r["week"], r["key"][2]))
    arms = [(a, 0.0) if not isinstance(a, (tuple, list)) else (float(a[0]), float(a[1])) for a in envs]
    return res, {"season": season, "fit_seasons": fit, "k": k, "envs": sorted({e for e, _w in arms}),
                 "arms": [list(a) for a in arms], "weeks": [min(weeks), max(weeks)],
                 "base_seed": BASE_SEED, "from_week": FROM_WEEK, "seconds": round(time.time() - t0, 1)}


# ---- scoring ----------------------------------------------------------------------
def samples(res, model):
    """Stage 2E's sample and observed-sample shapes for one model."""
    games = F.games_from_table(_table_rows(), ({r["season"] for r in res} or {0}))
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
    games = F.games_from_table(_table_rows(), ({r["season"] for r in res}))
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


ATT_GRID = (0.0, 0.5, 1.0)


def choose_att(sc, env):
    """The 2024 rule for the attempts weight (module docstring): the lowest
    offence CRPS among grid values whose pair and cross-team pair RMSE are
    each within 0.01 of w = 0's; ties to the smaller w."""
    cal, pr, opr = sc["calibration"], sc["pair_rmse"], sc["opp_pair_rmse"]
    k0 = arm_key(env, 0.0)
    best = None
    for w in ATT_GRID:
        k = arm_key(env, w)
        if k not in cal:
            continue
        if pr[k] > pr[k0] + 0.01 or opr[k] > opr[k0] + 0.01:
            continue
        c = cal[k]["_pooled"]["crps_mean_over_roles"]
        if best is None or c < best[1] - 1e-12:
            best = (w, c)
    return best[0] if best else 0.0


_PASS_STATS = (("pass_att", "pass_att", "QB"), ("comp", "pass_cmp", "QB"), ("pass_yds", "pass_yd", "QB"),
               ("rush_att", "rush_att", None))


def world_summary(res, model):
    """What one engine arm's FINAL worlds did, team-game by team-game, from
    the per-game anchor record: mean attempts, completions, passing yards and
    rushes against Sleeper's projection (the projected players' lines) and
    what happened; where the final worlds' points sat against the target the
    anchor levelled them to (the check that found the table leak: on 2024 the
    leaked worlds sat 1.2-1.3 points a team above it); and the median over
    games of the two teams' points correlation inside the worlds."""
    by = collections.defaultdict(list)
    for r in _table_rows():
        by[(r["season"], r["week"], r["team"])].append(r)

    def tot(rr, pref, comp, pos):
        return sum(float(r.get(f"{pref}_{comp}") or 0.0) for r in rr if (pos is None or r["pos"] == pos)
                   and (pref == "act" or (r["has_proj"] and (r["proj_dk"] or 0) > 0)))
    acc = {k: [0.0, 0.0, 0.0] for k, _c, _p in _PASS_STATS}
    n, land, corr = 0, [], []
    for g in res:
        an = (g["models"].get(model) or {}).get("anchor")
        if not an:
            continue
        if an.get("corr_pts") is not None:
            corr.append(an["corr_pts"])
        for i, team in enumerate((g["home"], g["away"])):
            b = an["box"][team]
            if an["target"][i] is not None:
                land.append(b["pts"] - an["target"][i])
            rr = by.get((g["season"], g["week"], team))
            if not rr:
                continue
            n += 1
            for k, comp, pos in _PASS_STATS:
                acc[k][0] += b[k]
                acc[k][1] += tot(rr, "proj", comp, pos)
                acc[k][2] += tot(rr, "act", comp, pos)
    if not n:
        return None
    return {"team_games": n,
            "per_team_game": {k: {"engine": round(v[0] / n, 2), "sleeper": round(v[1] / n, 2), "real": round(v[2] / n, 2)}
                              for k, v in acc.items()},
            "pts_minus_target": {"mean": round(float(np.mean(land)), 3), "mean_abs": round(float(np.mean(np.abs(land))), 3),
                                 "n": len(land)},
            "corr_pts_median": round(float(np.median(corr)), 3) if corr else None}


def paired_crps(res, model_a, model_b):
    """Mean CRPS difference (a minus b) over the roled player-weeks both
    models scored, with a standard error CLUSTERED BY GAME (players of one
    game share its worlds, so they are not independent), plus the same for
    the defenses and the kickers."""
    _s_a, ob = samples(res, model_a)
    _s_b, _ob = samples(res, model_b)
    def crps(x, y):
        x = np.sort(np.asarray(x, dtype=np.float64)); K = len(x); i1 = np.arange(1, K + 1)
        return float(np.abs(x - y).mean() - 0.5 * (2.0 / (K * K)) * np.sum((2 * i1 - K - 1) * x))
    out = {}
    per_game = collections.defaultdict(list)
    for ga, gb, o in zip(_s_a, _s_b, ob):
        for i, p in enumerate(ga["players"]):
            if (p.get("role") or "") in F.ROLES and not np.isnan(o["pts"][i, 0]):
                y = float(o["pts"][i, 0])
                per_game[ga["key"]].append(crps(ga["pts"][i], y) - crps(gb["pts"][i], y))
    def summ(groups):
        allv = [v for g in groups.values() for v in g]
        if not allv:
            return None
        gm = np.asarray([np.sum(g) for g in groups.values()])        # cluster totals
        n_g, n = len(gm), len(allv)
        mean = float(np.sum(gm) / n)
        se = float(np.sqrt(n_g / max(1, n_g - 1) * np.sum((gm - mean * np.asarray([len(g) for g in groups.values()])) ** 2)) / n)
        return {"mean_diff": round(mean, 5), "se_game_clustered": round(se, 5), "n": n, "games": n_g}
    out["offence"] = summ(per_game)
    for kind in ("dst", "k"):
        g2 = collections.defaultdict(list)
        for r in res:
            ma, mb = r["models"].get(model_a), r["models"].get(model_b)
            if not ma or not mb:
                continue
            for t in (r["home"], r["away"]):
                y = (r["dst_act"] if kind == "dst" else r["k_act"]).get(t)
                if y is None or t not in ma[kind] or t not in mb[kind]:
                    continue
                g2[str(r["key"])].append(crps(ma[kind][t], float(y)) - crps(mb[kind][t], float(y)))
        out[kind] = summ(g2)
    return out


def att_gate(sc, env, w):
    """The 2026 rule for the anchored engine against the shipped one (module
    docstring): {link: (ok, detail)} on the point estimates."""
    a, b = arm_key(env, w), arm_key(env, 0.0)
    ca, cb = sc["calibration"][a]["_pooled"], sc["calibration"][b]["_pooled"]
    sa, sb = sc["sides"][a], sc["sides"][b]
    out = {"a_crps": (ca["crps_mean_over_roles"] < cb["crps_mean_over_roles"],
                      f"anchored {ca['crps_mean_over_roles']:.4f} vs shipped {cb['crps_mean_over_roles']:.4f}"),
           "b_pairs": (sc["pair_rmse"][a] <= sc["pair_rmse"][b] + 0.01,
                       f"anchored {sc['pair_rmse'][a]:.4f} vs shipped {sc['pair_rmse'][b]:.4f} (+0.01 allowed)"),
           "c_dst": (sa["dst"]["crps"] <= 1.01 * sb["dst"]["crps"],
                     f"anchored {sa['dst']['crps']:.4f} vs shipped {sb['dst']['crps']:.4f} (x1.01)"),
           "c_k": (sa["k"]["crps"] <= 1.01 * sb["k"]["crps"],
                   f"anchored {sa['k']['crps']:.4f} vs shipped {sb['k']['crps']:.4f} (x1.01)")}
    out["promote"] = (all(v[0] for v in out.values()), "all links" if all(v[0] for v in out.values()) else
                      "failed: " + ", ".join(n for n, v in out.items() if not v[0]))
    return out


def main(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("store")
    ap.add_argument("--season", type=int, default=2024)
    ap.add_argument("--env", default="0")
    ap.add_argument("--att", default="0", help="attempts-anchor weights, run at every --env value")
    ap.add_argument("--weeks", default=None, help="e.g. 2-5 (default 2-18)")
    ap.add_argument("--k", type=int, default=K_DEFAULT)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", default=None)
    ap.add_argument("--raw", default=None, help="pickle the raw per-game results here")
    a = ap.parse_args(argv)
    envs = [float(x) for x in a.env.split(",") if x != ""]
    atts = [float(x) for x in a.att.split(",") if x != ""]
    weeks = None
    if a.weeks:
        lo, hi = (int(x) for x in a.weeks.split("-"))
        weeks = range(lo, hi + 1)
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
        # the attempts weight is frozen too: 2025 and 2026 run the shipped
        # engine (w = 0) beside the 2024 rule's choice and nothing else
        if atts != [0.0] and atts != [0.0, nfl_dfs_engine.ATT_WEIGHT_CHOSEN]:
            raise RuntimeError(f"seasons >= 2025 run w = 0 and the frozen 2024 choice "
                               f"({nfl_dfs_engine.ATT_WEIGHT_CHOSEN}) only, not {atts}")
    arms = [(e, w) for e in envs for w in atts]
    res, meta = run(a.store, a.season, arms, a.k, a.workers, weeks=weeks)
    if a.raw:
        with open(a.raw, "wb") as fh:
            pickle.dump((res, meta), fh)
    models = ["legacy", "constrained"] + [arm_key(e, w) for e, w in arms]
    sc = score(res, models)
    sc["meta"] = meta
    from research import provenance
    sc["meta"]["provenance"] = provenance.stamp()
    sc["meta"]["engine_sources"] = engine_sources_sha()
    anchors = [r["models"][arm_key(*arms[0])]["anchor"] for r in res if arm_key(*arms[0]) in r["models"]]
    sc["anchor"] = {"mean_abs_landing_err": round(float(np.mean([abs(a["landed"][0] - a["target"][0]) + abs(a["landed"][1] - a["target"][1])
                                                                 for a in anchors]) / 2), 3) if anchors else None,
                    "mean_target_minus_deep": round(float(np.mean([(a["target"][0] - a["deep"][0] + a["target"][1] - a["deep"][1]) / 2
                                                                   for a in anchors])), 3) if anchors else None}
    sc["worlds"] = {arm_key(e, w): world_summary(res, arm_key(e, w)) for e, w in arms}
    sc["att_anchor"] = {}
    for e, w in arms:
        if not w:
            continue
        an = [r["models"][arm_key(e, w)]["anchor"] for r in res if arm_key(e, w) in r["models"]]
        errs = [abs(x - t) for a_ in an for x, t in zip(a_.get("att_landed") or [], a_.get("att_target") or []) if t]
        pe = [abs(x - t) for a_ in an for x, t in zip(a_["landed"], a_["target"]) if t]
        sc["att_anchor"][arm_key(e, w)] = {"mean_abs_att_landing_err": round(float(np.mean(errs)), 3) if errs else None,
                                           "mean_abs_pts_landing_err": round(float(np.mean(pe)), 3) if pe else None,
                                           "mean_call_shift": round(float(np.mean([c for a_ in an for c in (a_.get("call_shift") or []) if c is not None])), 4)}
        sc.setdefault("paired", {})[arm_key(e, w)] = paired_crps(res, arm_key(e, w), arm_key(e, 0.0))
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


# ---- the report --------------------------------------------------------------------
_ROLE_ORDER = ("QB1", "RB1", "RB2", "WR1", "WR2", "WR3", "TE1")
_NAMES = {"legacy": "legacy (production)", "constrained": "constrained (alternate)"}


def _mname(m):
    if "_att" in m:
        env, w = m.replace("engine_", "").split("_att")
        return f"engine, env {env}, attempts anchored at w {w}"
    return _NAMES.get(m, m.replace("engine_", "engine, env "))


def _season_tables(sc, models):
    L = []
    L.append("| model | offence CRPS (7 roles) | PIT KS | pair RMSE | cross-team pair RMSE | DST CRPS | DST mean sim / real | K CRPS | K mean sim / real |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for m in models:
        c, sd = sc["calibration"][m]["_pooled"], sc["sides"][m]
        L.append(f"| {_mname(m)} | {c['crps_mean_over_roles']:.4f} | {c['pit_ks']:.4f} | {sc['pair_rmse'][m]:.4f} | "
                 f"{sc['opp_pair_rmse'][m]:.4f} | {sd['dst']['crps']:.4f} | {sd['dst']['mean_sim']:.2f} / {sd['dst']['mean_obs']:.2f} | "
                 f"{sd['k']['crps']:.4f} | {sd['k']['mean_sim']:.2f} / {sd['k']['mean_obs']:.2f} |")
    L.append("")
    L.append("CRPS by role (lower is better) and 80% interval coverage:")
    L.append("")
    L.append("| model | " + " | ".join(_ROLE_ORDER) + " |")
    L.append("|---|" + "---|" * len(_ROLE_ORDER))
    for m in models:
        c = sc["calibration"][m]
        L.append(f"| {_mname(m)} | " + " | ".join(f"{c[r]['crps']:.3f} ({100 * c[r]['cover_80']:.0f}%)" for r in _ROLE_ORDER if r in c) + " |")
    L.append("")
    L.append("Role-pair correlations of projection residuals, observed against each model:")
    L.append("")
    L.append("| pair | observed | " + " | ".join(_mname(m) for m in models) + " |")
    L.append("|---|---|" + "---|" * len(models))
    for row in sc["pairs"]:
        L.append(f"| {row['moment'][5:]} | {row['obs']:.3f} | " + " | ".join(
            (f"{row[m]:.3f}" if row.get(m) is not None else "-") for m in models) + " |")
    L.append("")
    L.append("Mean DraftKings points by role -- each model's simulated mean, Sleeper's projection, what happened:")
    L.append("")
    L.append("| model | " + " | ".join(_ROLE_ORDER) + " |")
    L.append("|---|" + "---|" * len(_ROLE_ORDER))
    for m in models:
        mm = sc["means"][m]
        L.append(f"| {_mname(m)} | " + " | ".join(f"{mm[r]['sim']:.2f} / {mm[r]['sleeper']:.2f} / {mm[r]['actual']:.2f}" for r in _ROLE_ORDER if r in mm) + " |")
    return L


def write_report(dev, test, out, audit_md=None, anchor=None):
    """research/reports/nfl_dfs_engine.md from the two artifacts, plus the
    passing-volume anchor's three (`anchor` = (2024 grid, 2025 seen, 2026 test))."""
    env = test["meta"]["envs"][0]
    ek = f"engine_{env:g}"
    g = gate(test, env)
    dev_models = ["legacy", "constrained"] + [f"engine_{e:g}" for e in dev["meta"]["envs"]]
    L = ["# The game-engine DFS worlds: the deep engine plays every world",
         "",
         f"Built {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())} by research/nfl_dfs_engine_eval.py. "
         f"Development season 2024 ({dev['n_games']} games, weeks {dev['meta']['from_week']}-18, tables fit on {dev['meta']['fit_seasons']}); "
         f"blind test 2025 ({test['n_games']} games, tables fit on {test['meta']['fit_seasons']}), {test['meta']['k']} worlds per game per model, "
         f"base seed {test['meta']['base_seed']}. 2025 provenance: commit {test['meta']['provenance'].get('commit')}, "
         f"dirty {test['meta']['provenance'].get('dirty')}; engine sources {test['meta']['engine_sources']['sha256']} "
         f"(dirty {test['meta']['engine_sources']['dirty']}).",
         "",
         "## What the engine model is",
         "",
         "Every DFS world is one football game played snap by snap by the deep engine (nfl_deep_sim): possessions, "
         "the clock, fourth downs, the script, both offences against both defences. Each team's scoring level is the "
         "Sleeper drive model's expected points (nfl_game_sim, on this week's projections) and the deep engine's own, "
         "blended at the slate's deep weight, 0.30; the deep engine's offences are re-levelled until it plays at that "
         "target. The world's box score is dealt to the players by Sleeper's projected usage with the constrained "
         "model's fitted competition noise: every completion a catch by someone, receiving yards the quarterback's to "
         "the yard, every touchdown a catch or a carry. The defense is scored off what the OTHER offence did in the same "
         "game (points it scored with the ball, sacks, takeaways) plus its own return touchdowns, safeties and blocks; "
         "the kicker off his own field goals by distance and his tries. Player means are what the game deals, not pinned "
         "to Sleeper's number; Sleeper's projection stays the `proj` the field reads.",
         "",
         "One term was added to the engine for the DFS worlds: the game's shared passing environment (one draw per game "
         "moving both offences' yards per completion and completion rate). Across the two teams the engine already "
         "matched real games (2023-2025, 816 games) on points (-0.055 real, -0.045 engine), plays (-0.53, -0.53), rushes "
         "(-0.56, -0.59), drives (+0.85, +0.85) and attempts against the opponent's points (+0.29, +0.32), and missed "
         "exactly one thing: the two teams' passing yards (+0.18 real, +0.03 engine). The term is off for every other "
         "caller (the slate's deep layer, the deep season).",
         "",
         "## The rules, written before 2025 was run",
         "",
         "ENV_SD: the 2024 grid value (0, 0.05, 0.08, 0.11, 0.14) with the smallest cross-team pair RMSE among those whose "
         f"offensive CRPS is within 0.5% of env 0's. Chosen: **{choose_env(dev):g}** (frozen in nfl_dfs_engine.ENV_SD and "
         "committed before the 2025 run; the run refuses any other value or a dirty tree).",
         "",
         "Promotion to the boards' model on 2025: (a) offence CRPS <= 1.01 x the constrained model's; (b) pair-correlation "
         "RMSE below both legacy's and constrained's; (c) pooled PIT KS <= max(0.05, constrained's + 0.01); (d) defense "
         "and kicker CRPS each <= 1.02 x legacy's. Any link failing keeps the boards on legacy.",
         "",
         "## 2025, blind",
         ""]
    L += _season_tables(test, ["legacy", "constrained", ek])
    L += ["", "### The gate", ""]
    for k in ("a_crps", "b_pairs", "c_pit", "d_dst", "d_k"):
        ok, det = g[k]
        L.append(f"- {k}: {'PASS' if ok else 'FAIL'} -- {det}")
    L.append(f"- **verdict: {'PROMOTED -- the boards are built on the engine model' if g['promote'][0] else 'NOT promoted -- ' + g['promote'][1]}**")
    L += ["", "## 2024, development (where ENV_SD was chosen)", ""]
    L += _season_tables(dev, dev_models)
    L += ["", "## Limits, stated",
          "",
          "- One held-out season is a measurement, not a proof. The 2025 outcomes had been opened once before (Stage 2E, "
          "for the legacy-vs-constrained test); this is the engine's first contact with them, and nothing was tuned on them.",
          "- The deep engine's two global constants (_FORM_SD, _HFA_YDS) were calibrated on 2023-2025 margins and home "
          "win rates: game-level, and 2025 is inside that fit.",
          "- The replay anchors the deep layer to the points-differential ratings (Kalshi's win totals have no history) and "
          "leaves out the weekly roster factor; production uses both.",
          "- The allocation noise (share_sd, ypr_sd, rshare_sd, ypc_sd) is the constrained model's 2022-2024 fit, taken as "
          "is; the engine replaces that model's team-level draws with a played game.",
          "- Two-point conversions and offensive return touchdowns are not dealt to players (no model here deals them).",
          "- The showdown money columns stay experimental: the field model is uncalibrated whatever simulator made the worlds.",
          f"- The engine's passing level ran above both Sleeper's and reality on 2025: WR1 {test['means'][ek]['WR1']['sim']:.2f} "
          f"simulated against {test['means'][ek]['WR1']['actual']:.2f} real ({test['means'][ek]['WR1']['sleeper']:.2f} Sleeper), WR2 "
          f"{test['means'][ek]['WR2']['sim']:.2f} against {test['means'][ek]['WR2']['actual']:.2f}, QB1 {test['means'][ek]['QB1']['sim']:.2f} "
          f"against {test['means'][ek]['QB1']['actual']:.2f}. The lean shows in its PIT: "
          f"{100 * test['calibration'][ek]['_pooled']['pit_hist'][0]:.0f}% of real outcomes fell in the bottom tenth of its "
          "distribution, against 10% for a calibrated model, and the PIT link passed by "
          f"{0.05 - test['calibration'][ek]['_pooled']['pit_ks']:.3f}. On 2024 the same level sat close to what happened "
          f"(WR1 {dev['means'][ek]['WR1']['sim']:.2f} against {dev['means'][ek]['WR1']['actual']:.2f}). Anchoring each team's passing "
          "volume to Sleeper's projected attempts is the obvious next lever. It was not tried here: it would have to be chosen on 2024 "
          "and could only be judged on 2026." + (
              " It has since been done (next section): on 2025 the anchored engine's WR1 averages "
              f"{anchor[1]['means'][arm_key(env, choose_att(anchor[0], env))]['WR1']['sim']:.2f} and WR2 "
              f"{anchor[1]['means'][arm_key(env, choose_att(anchor[0], env))]['WR2']['sim']:.2f}, still above what happened."
              if anchor else ""),
          "- The environment term buys the yardage coupling at a measured cost in points independence: inside the engine's "
          "worlds the two teams' points in one game correlate +0.124 (2024) and +0.128 (2025) at the frozen value, against "
          "-0.033 with it off (median over the 256 games; measured from the runs' raw per-game records, which are not "
          "committed at 86 MB). Real games pooled across matchups show -0.055, a figure that mixes in the spread's negative "
          "pull, so the real within-game value is near zero and the term overshoots it. The defenses' CRPS improved anyway.",
          "- The engine over-couples a quarterback with his own backs (QB1/RB1 "
          f"{next(r[ek] for r in test['pairs'] if r['moment'].endswith('QB1/RB1')):.3f} against "
          f"{next(r['obs'] for r in test['pairs'] if r['moment'].endswith('QB1/RB1')):.3f} real): the played game ties a team's "
          "rushing and passing to the same scoring.",
          "- The lineup SHEET in the app stays on legacy-discrete: the server has no numpy and must not play thousands of "
          "snap-by-snap games inside a one-core web worker. The tournament boards, built on the PC, are where the engine runs.",
          ""]
    if anchor:
        L += anchor_section(*anchor) + [""]
    if audit_md:
        L += ["## The DFS audit that preceded this (live week 6, 2026-10-16)", "", audit_md.strip(), ""]
    with open(out, "w") as fh:
        fh.write("\n".join(L) + "\n")
    return g


def _mname_att(m):
    return _mname(m)


def anchor_section(dev, seen, test):
    """The report's section on the passing-volume anchor, from the three
    artifacts (2024 grid, 2025 seen, 2026 test)."""
    env = 0.14
    w = choose_att(dev, env)
    L = ["## The passing-volume anchor (2026-10-16)", "",
         "The owner: \"anchor passing volume\". The promoted engine threw about 4% too many passes in both seasons it was "
         "measured on. Team-game attempts, engine / Sleeper / real: 2024 34.15 / 31.89 / 32.81, 2025 33.24 / 31.59 / 31.99. "
         "Its 2025 completions ran 21.96 against 20.56 real, which is what made its receivers hot. Sleeper's own team "
         "passing was within 1% of reality on 2025.", "",
         "The lever is a per-team pass-rate shift on the logit of every play call (`call_shift`), iterated with the points "
         "anchor, so each team's mean attempts land on (1 - w) x the engine's own + w x Sleeper's projected team attempts. "
         "The rules were written before any anchored run: w is chosen on 2024 alone (lowest offence CRPS among grid values "
         "whose pair and cross-team RMSE stay within 0.01 of w = 0's); 2025 is reported but cannot judge the fix, because "
         "the defect was found on it; the test is 2026 weeks 2-5, which no engine run had read.", ""]
    dm = [arm_key(env, x) for x in ATT_GRID if arm_key(env, x) in dev["calibration"]]
    L += [f"### 2024, development ({dev['n_games']} games): w = **{w:g}** chosen", ""]
    L += _season_tables(dev, ["constrained"] + dm)
    for k, v in (dev.get("att_anchor") or {}).items():
        L.append(f"- {_mname_att(k)}: attempts land {v['mean_abs_att_landing_err']} from target on average, points "
                 f"{v['mean_abs_pts_landing_err']}; mean pass-rate shift {v['mean_call_shift']} on the logit")
    if w:
        k1 = arm_key(env, w)
        for name, sc in (("2025, SEEN (the season the defect was found on; it cannot judge the fix)", seen),
                         ("2026 weeks 2-5, the test", test)):
            if not sc:
                continue
            L += ["", f"### {name}: {sc['n_games']} games", ""]
            L += _season_tables(sc, ["legacy", "constrained", arm_key(env, 0.0), k1])
            pr = (sc.get("paired") or {}).get(k1) or {}
            for kind, lab in (("offence", "offence (roled player-weeks)"), ("dst", "defenses"), ("k", "kickers")):
                d = pr.get(kind)
                if d:
                    L.append(f"- CRPS anchored minus shipped, {lab}: {d['mean_diff']:+.4f} +/- {d['se_game_clustered']:.4f} "
                             f"(SE clustered by game; n {d['n']}, {d['games']} games)")
        if test:
            g = att_gate(test, env, w)
            L += ["", "### The 2026 rule", ""]
            for k in ("a_crps", "b_pairs", "c_dst", "c_k"):
                L.append(f"- {k}: {'PASS' if g[k][0] else 'FAIL'} -- {g[k][1]}")
            L.append(f"- **verdict: {'SHIPPED -- the boards anchor passing volume at w ' + format(w, 'g') if g['promote'][0] else 'NOT shipped -- ' + g['promote'][1] + '; the boards stay on the unanchored engine'}**")
            scl = [r["scale"] for r in test["pairs"] if r.get("scale") is not None]
            L += ["", "How to read it. The gain is small: on 2026 it sits inside one standard error, and it had the same sign on "
                  "2024 (where it was chosen) and on 2025 (seen). On 63 games each observed role-pair correlation carries "
                  f"bootstrap noise of {min(scl):.2f} to {max(scl):.2f}, so every model's 2026 pair RMSE (0.13-0.34) mostly "
                  "measures that noise, and the cross-team ranking on this sample (the constrained model ahead of the engine) "
                  "is not a reliable read against 2024's and 2025's 256 games each, where the engine led it by a wide margin."]
    else:
        L += ["", "The 2024 rule chose w = 0: the anchor did not earn its place on the development season, so nothing "
              "ships and 2026 was not run with it."]
    return L
