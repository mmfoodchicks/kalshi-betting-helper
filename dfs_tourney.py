"""The DFS tournament engine: every legal lineup, every simulated world, one
weighted field, ranked by how often each lineup actually wins.

The lineup builders answer "which lineup has the best distribution?" with a
summary of that lineup's own totals. A top-heavy contest asks a different
question: in how many of the possible games does THIS lineup finish first
(or top 1%) against the people who entered? That is what this module
counts, and it counts it without shortcuts:

  1. the game is simulated N times (the per-player point arrays the sims
     already produce, one column per simulated game);
  2. EVERY legal lineup is enumerated -- a showdown pool of twenty players
     is a few hundred thousand lineups, and this is a matrix multiply, not
     a search;
  3. the field is a probability over those same lineups -- a softmax over
     projected lineup points, sharpened until the single most popular
     lineup holds FIELD_TOP_SHARE of the field -- so an entry's rank in a
     world is the field mass scoring above it, and a lineup's duplication
     risk is its own mass. The players the depth gate keeps us off (a WR4,
     a third back) stay in the FIELD's pool: the public plays them, and a
     world where one of them booms is a world we lose;
  4. per world, every lineup's win / top-1% / cash chance and payout are
     read off that mass (first place exactly, the payout curve through a
     normal rank approximation), and the tallies over worlds are the
     answer -- plus which lineup was the hindsight-optimal one in each
     world, tallied per lineup and per player;
  5. a portfolio of K entries is chosen greedily to cover the most worlds:
     the next entry is the one that adds the most top-1% probability in
     the worlds the entries so far do not already cover.

Needs numpy. Built for the owner's PC (pc_worker ships the result as a
board the tab serves); the server never runs this -- 200,000 lineups
across 60,000 worlds is a desktop's afternoon, not a one-core web
worker's request. Owner, 2026-09-09: "We can make this as cpu intensive as
possible ... I want like Skyrim at full max render distance."
"""

import itertools
import math
import time

import errlog

try:
    import numpy as np
except ImportError:                      # the server: no numpy on purpose
    np = None

VERSION = 1


def available():
    return np is not None


# ---- lineups ---------------------------------------------------------------
def enumerate_showdown(players, cap, cpt_mult=1.5, min_teams=2, entry_ok=None,
                       cpt_ok=None):
    """Every legal 1 CPT + 5 FLEX lineup: (idx (L, 6) int32 with the captain
    first, weights (L, P) float32 with cpt_mult on the captain, allowed (L,)
    bool -- which lineups WE may enter (entry_ok(cap_p, picked) -> bool),
    the field may hold any of them). Legal = under the cap, both teams."""
    P = len(players)
    sal = [int(p["salary"]) for p in players]
    csal = [int(p["cpt_salary"]) for p in players]
    team = [p.get("team") or "" for p in players]
    rows, allowed = [], []
    for c in range(P):
        if cpt_ok is not None and not cpt_ok(players[c]):
            cap_allowed = False
        else:
            cap_allowed = True
        budget = cap - csal[c]
        if budget < 0:
            continue
        others = [i for i in range(P) if i != c and sal[i] <= budget]
        for combo in itertools.combinations(others, 5):
            s = sal[combo[0]] + sal[combo[1]] + sal[combo[2]] + sal[combo[3]] + sal[combo[4]]
            if s > budget:
                continue
            if len({team[c], team[combo[0]], team[combo[1]], team[combo[2]],
                    team[combo[3]], team[combo[4]]} - {""}) < min_teams:
                continue
            rows.append((c,) + combo)
            ok = cap_allowed
            if ok and entry_ok is not None:
                ok = entry_ok(players[c], [players[i] for i in combo])
            allowed.append(ok)
    if not rows:
        return None, None, None
    idx = np.asarray(rows, dtype=np.int32)
    W = np.zeros((len(rows), P), dtype=np.float32)
    ar = np.arange(len(rows))
    W[ar, idx[:, 0]] = cpt_mult
    for k in range(1, 6):
        W[ar, idx[:, k]] = 1.0
    return idx, W, np.asarray(allowed, dtype=bool)


FIELD_TOP_SHARE = 0.002
# How concentrated the public is. The one hard number: DraftKings' 2021-09-20
# GB-DET showdown Millionaire paid 231 identical winning lineups (about 0.1%
# of that field), and the winner is rarely the MOST popular build, so the
# most popular one is taken as 0.2% -- roughly 260 copies in a 132,000-entry
# contest. A placeholder until the app can grade a real results file: the v0
# field (a product of the app's ownership guesses, nine players pinned at a
# 45% cap) was so flat that the best lineup showed a 25% top-1% chance and a
# +10,000% ROI against a 132,000-entry field. Measured 2026-09-09.


def field_weights(players, idx, cpt_mult=1.5, top_share=FIELD_TOP_SHARE):
    """The field as a probability over lineups: softmax(beta * projected
    points), beta found by bisection so the most popular lineup holds
    `top_share` of the field. Returns (f, beta). One knob, and it is the
    knob that decides duplication and the mass ahead of a chalk build."""
    proj = np.asarray([float(p.get("proj") or 0.0) for p in players], dtype=np.float64)
    mu = proj[idx[:, 0]] * cpt_mult + proj[idx[:, 1:]].sum(axis=1)
    mu = mu - mu.max()                      # the top lineup's weight is exp(0) = 1
    lo, hi = 0.0, 12.0
    for _ in range(60):
        beta = 0.5 * (lo + hi)
        share = 1.0 / np.exp(beta * mu).sum()
        if share < top_share:
            lo = beta
        else:
            hi = beta
    beta = 0.5 * (lo + hi)
    f = np.exp(beta * mu)
    return f / f.sum(), float(beta)


def field_ownership(players, idx, f):
    """What the field model implies per player: (captain %, flex %)."""
    P = len(players)
    oc = np.bincount(idx[:, 0], weights=f, minlength=P)
    of = np.zeros(P, dtype=np.float64)
    for k in range(1, idx.shape[1]):
        of += np.bincount(idx[:, k], weights=f, minlength=P)
    return 100.0 * oc, 100.0 * of


# ---- the payout curve as a function of the field mass above you -----------
def _ncdf_arr(x):
    # numpy has no erf; a rational approximation good to ~1e-7 (Abramowitz
    # and Stegun 7.1.26), vectorized.
    z = np.abs(x) / math.sqrt(2.0)
    t = 1.0 / (1.0 + 0.3275911 * z)
    y = 1.0 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t
                - 0.284496736) * t + 0.254829592) * t * np.exp(-z * z)
    return np.where(x >= 0, 0.5 * (1.0 + y), 0.5 * (1.0 - y))


def payout_grid(C, payouts, entry_fee, places, n=6000):
    """For F = the field mass scoring above an entry, on a grid dense near 0:
    P(first), P(top 1%), P(cash), expected payout. First place is exact
    ((1-F)^(C-1): nobody above); the rest use the normal approximation of a
    Binomial(C-1, F) rank, the same one the contest sims use."""
    C = max(2, int(C))
    F = np.concatenate([np.array([0.0]), np.logspace(-9, 0, n - 1)])
    F = np.minimum(F, 1.0)
    m = 1.0 + (C - 1) * F
    s = np.sqrt(np.maximum(1e-9, (C - 1) * F * (1.0 - F)))
    first = np.power(1.0 - F, C - 1)
    top1_line = max(1, int(0.01 * C))
    top1 = _ncdf_arr((top1_line + 0.5 - m) / s)
    cash = _ncdf_arr((places + 0.5 - m) / s)
    ev = np.zeros_like(F)
    for row in payouts or []:
        lo, hi, prize = int(row["from"]), int(row["to"]), float(row["prize"])
        if prize <= 0:
            continue
        if lo == 1:
            ev += prize * first
            lo = 2
            if hi < 2:
                continue
        ev += prize * (_ncdf_arr((hi + 0.5 - m) / s) - _ncdf_arr((lo - 0.5 - m) / s))
    return {"F": F, "first": first, "top1": top1, "cash": cash, "ev": ev,
            "C": C, "places": places, "entry_fee": float(entry_fee)}


# ---- the tournament ---------------------------------------------------------
_RES = 10.0           # score buckets of 0.1 DK points for the field ranking
_MAXPTS = 400.0
_NB = int(_MAXPTS * _RES) + 1
_CHUNK_ELEMS = 1.5e8  # scores per chunk: (c x L) float32 + int32 ~ 1.2 GB


def chunk_for(L, chunk):
    """Worlds per chunk that keep the (c x L) score and bucket matrices
    inside _CHUNK_ELEMS -- a 225,000-lineup pool takes 666 worlds a chunk,
    a 1.2-million one 125."""
    return max(16, min(int(chunk), int(_CHUNK_ELEMS // max(1, L))))


def _buckets(St):
    """Scores (c, L) float32 -> 0.1-point buckets, in place (St is spent)."""
    np.multiply(St, _RES, out=St)
    St += 0.5
    np.clip(St, 0, _NB - 1, out=St)
    return St.astype(np.int32)


def _world_tables(b, f, F_grid):
    """One world: the field mass strictly above each score bucket, as an
    index into the payout grid. Everything per-lineup in a world is then a
    gather by bucket -- the old per-(lineup, world) binary search was 90%
    of the run (measured: 20s of a 33s chunk on 225,000 lineups)."""
    hist = np.bincount(b, weights=f, minlength=_NB)
    strictly = np.cumsum(hist[::-1])[::-1] - hist
    pos = np.searchsorted(F_grid, np.clip(strictly, 0.0, 1.0))
    return np.minimum(pos, len(F_grid) - 1)


def run(W, X, f, grid, chunk=500, progress=None):
    """Score every lineup in every world. W (L, P) lineup weights, X (P, N)
    player points per world, f (L,) field probability, grid from
    payout_grid. Returns per-lineup means over worlds: win, top1, cash, ev
    (fees not yet netted), plus the hindsight-optimal tallies.

    World-major: each chunk's scores come out as (worlds, lineups) so a
    world is a contiguous row -- the (lineups, worlds) layout paid 25x on
    the per-world pass (9.9s vs 0.4s a chunk, measured)."""
    L, N = W.shape[0], X.shape[1]
    F_grid = grid["F"]
    G = np.stack([grid["first"], grid["top1"], grid["cash"], grid["ev"]], axis=1)
    acc = np.zeros((L, 4), dtype=np.float64)
    opt = np.zeros(L, dtype=np.int64)          # worlds in which this lineup scored highest of all
    WT = np.ascontiguousarray(W.T)             # (P, L)
    chunk = chunk_for(L, chunk)
    t0 = time.time()
    for start in range(0, N, chunk):
        Xc = np.ascontiguousarray(X[:, start:start + chunk].T)   # (c, P)
        St = Xc @ WT                                             # (c, L)
        opt += np.bincount(np.argmax(St, axis=1), minlength=L)
        Bt = _buckets(St)
        for j in range(Bt.shape[0]):
            b = Bt[j]
            acc += G[_world_tables(b, f, F_grid)][b]
        if progress:
            progress(min(N, start + chunk), N, time.time() - t0)
    return {"win": acc[:, 0] / N, "top1": acc[:, 1] / N, "cash": acc[:, 2] / N,
            "ev": acc[:, 3] / N, "opt": opt, "n_worlds": N}


def portfolio(W, X, f, grid, cand_idx, k, chunk=500):
    """Greedy cover over candidate lineups: each pick adds the most top-1%
    probability in the worlds the picks so far leave uncovered. Returns the
    chosen candidate positions (best first) and, per prefix length, the
    portfolio's P(at least one entry finishes top 1%)."""
    if not len(cand_idx):
        return [], []
    L, N = W.shape[0], X.shape[1]
    F_grid = grid["F"]
    top1_g = grid["top1"]
    WT = np.ascontiguousarray(W.T)
    WcT = np.ascontiguousarray(W[cand_idx].T)
    chunk = chunk_for(L, chunk)
    # per-world top-1% probability for every candidate (K x N), built once;
    # the field pass is repeated (a second matmul and bucket count) rather
    # than kept from run(): 60,000 worlds x 4,001 buckets would be a
    # gigabyte of tables.
    P = np.zeros((len(cand_idx), N), dtype=np.float32)
    for start in range(0, N, chunk):
        Xc = np.ascontiguousarray(X[:, start:start + chunk].T)
        Bt = _buckets(Xc @ WT)
        Bc = _buckets(Xc @ WcT)
        for j in range(Bt.shape[0]):
            P[:, start + j] = top1_g[_world_tables(Bt[j], f, F_grid)][Bc[j]]
    # Greedy cover for the LARGEST k; a smaller portfolio is its prefix, so
    # the caller slices rather than re-scoring the field per size.
    chosen, miss = [], np.ones(N, dtype=np.float64)   # P(no pick in top 1%) per world
    p_any = []
    for _ in range(min(k, len(cand_idx))):
        gain = (miss[None, :] * P).sum(axis=1)
        if chosen:
            gain[chosen] = -1.0
        j = int(np.argmax(gain))
        chosen.append(j)
        miss = miss * (1.0 - P[j])
        p_any.append(float(1.0 - miss.mean()))
    return chosen, p_any


# ---- NFL showdown adapter ----------------------------------------------------
_FIELD_EXTRA_MAX = 8      # depth-gated players the FIELD still plays (WR4, RB3...)
_FIELD_EXTRA_MIN_PROJ = 2.0    # a WR4 projected at 3 is in thousands of their lineups


def build_nfl_showdown(dg, contest_id=None, n_sims=60000, n_worlds=None, chunk=500,
                       week=None, preseason=False, top=60, portfolio_sizes=(5, 10, 20),
                       candidates=1200, log=print):
    """The tournament for one DraftKings NFL showdown draft group, against the
    biggest GPP on it (or `contest_id`). Returns the artifact the tab serves,
    or None when the pool or the contest cannot be read."""
    if np is None:
        raise RuntimeError("numpy is required for the tournament")
    import dk
    import nfl_dfs
    import nfl_dfs_sim
    import simulate
    t0 = time.time()
    slate = dk.slate_for("nfl", draft_group_id=int(dg))
    if not slate:
        return None
    contests = slate.get("contests") or []
    contest = None
    if contest_id:
        contest = dk.contest_detail(int(contest_id))
    if not contest and contests:
        gpp = [c for c in contests if (c.get("entries") or 0) >= 100]
        big = max(gpp or contests, key=lambda c: (c.get("prize_pool") or 0))
        contest = dk.contest_detail(int(big["id"]))
    if not contest:
        return None
    csv_players = simulate.parse_dk_csv(slate["csv"])
    ents = nfl_dfs.showdown_pool(csv_players)
    teams = {e.get("team") for e in ents if e.get("team")}
    if week is None:
        try:
            import nfl_game_sim
            import nfl_preseason
            preseason = nfl_preseason.is_preseason()
            week = nfl_game_sim.current_week(preseason)
        except Exception as e:
            errlog.note("TOURN-week", e)
            week = 1
    log(f"[tourney] {slate.get('n_players')} players, {contest['name']}, week {week}: "
        f"simulating {n_sims:,} games of {' vs '.join(sorted(teams))}...")
    pool = nfl_dfs_sim.player_pool(week, n=int(n_sims), preseason=preseason, teams=teams) or {}
    nidx, norm = nfl_dfs._norm_index(pool)
    excluded = []
    for e in ents:
        sim = nfl_dfs._pool_match(pool, e["name"], e["pos"], e.get("team"), nidx, norm)
        if sim and sim.get("arr"):
            e["proj"], e["ceiling"], e["floor"], e["arr"] = (
                round(sim["proj"], 1), sim["ceiling"], sim["floor"], sim["arr"])
        else:
            excluded.append({"name": e["name"], "pos": e["pos"], "team": e.get("team"),
                             "why": "no projection this week"})
            e["_drop"] = True
    ents = [e for e in ents if not e.get("_drop")]
    by_name = {e["name"]: e for e in ents}
    ents, dx = nfl_dfs._apply_depth(ents, preseason)
    # The depth gate is OUR rule, not the public's: a WR4 with a real
    # projection is in thousands of their lineups, so he stays in the field's
    # pool (never in ours) and his boom worlds count against us honestly.
    extra = []
    for d in dx:
        e = by_name.get(d["name"])
        if e is not None and "depth chart" in (d.get("why") or "") \
                and (e.get("proj") or 0) >= _FIELD_EXTRA_MIN_PROJ:
            e["_field_only"] = True
            e["depth"] = (d["why"].split(" ") or [""])[0]
            extra.append(e)
    extra.sort(key=lambda e: -e["proj"])
    extra = extra[:_FIELD_EXTRA_MAX]
    field_only = {e["name"] for e in extra}
    for d in dx:
        d["field"] = d["name"] in field_only
    excluded += dx
    if len(ents) < 6:
        return None
    ents = ents + extra
    t1 = time.time()
    N = min(len(e["arr"]) for e in ents)
    if n_worlds:
        N = min(N, int(n_worlds))
    X = np.asarray([e["arr"][:N] for e in ents], dtype=np.float32)   # (P, N)
    log(f"[tourney] pool {len(ents)} players ({len(extra)} field-only) x {N:,} worlds "
        f"({t1 - t0:.0f}s); enumerating lineups...")

    def entry_ok(cap_p, picked):
        got = []
        for p in picked:
            if p.get("_field_only") or not nfl_dfs._sd_allowed(p, cap_p, got):
                return False
            got.append(p)
        return True

    rich = sum(1 for p in ents if p.get("pos") in nfl_dfs._SD_GPP_CPT_POS
               and not p.get("_field_only")
               and p["cpt_salary"] >= nfl_dfs._SD_MIN_CPT_SALARY) >= 3

    def cpt_ok(p):
        if p.get("_field_only") or p.get("pos") not in nfl_dfs._SD_GPP_CPT_POS:
            return False
        return (not rich) or p["cpt_salary"] >= nfl_dfs._SD_MIN_CPT_SALARY

    idx, W, allowed = enumerate_showdown(ents, nfl_dfs.CAP, cpt_mult=nfl_dfs.CPT_MULT,
                                         entry_ok=entry_ok, cpt_ok=cpt_ok)
    if idx is None:
        return None
    f, beta = field_weights(ents, idx, cpt_mult=nfl_dfs.CPT_MULT)
    own_c, own_f = field_ownership(ents, idx, f)
    for i, e in enumerate(ents):
        e["own"] = round(float(own_c[i] + own_f[i]), 1)
    C = int(contest.get("max_entries") or contest.get("entered") or 0) or 10000
    t2 = time.time()
    chunk = chunk_for(len(idx), chunk)
    log(f"[tourney] {len(idx):,} legal lineups, {int(allowed.sum()):,} we may enter "
        f"({t2 - t1:.0f}s); field beta {beta:.3f}, top build {100 * f.max():.2f}% "
        f"(~{C * f.max():.0f} copies); scoring every lineup in every world, "
        f"{chunk} worlds a chunk...")
    grid = payout_grid(C, contest.get("payouts") or [], float(contest.get("entry_fee") or 1.0),
                       int(contest.get("places_paid") or max(1, C // 5)))
    every = max(chunk, (N // 6 // chunk) * chunk)
    res = run(W, X, f, grid, chunk=chunk,
              progress=lambda done, n, dt: log(f"[tourney]   {done:,}/{n:,} worlds, {dt:.0f}s")
              if done % every == 0 or done == n else None)
    t3 = time.time()
    fee = float(contest.get("entry_fee") or 1.0)
    first = float(contest.get("first_prize") or 0.0)
    copies = C * f                                         # expected duplicates in the field
    ev_dup = res["ev"] - res["win"] * first * (1.0 - 1.0 / (1.0 + copies))
    roi = (ev_dup - fee) / fee
    ok = np.where(allowed)[0]

    def row(i, rank_by):
        cap = ents[idx[i, 0]]
        legs = [ents[idx[i, k]] for k in range(1, 6)]
        return {"captain": cap["name"], "captain_pos": cap["pos"], "captain_team": cap["team"],
                "flex": [p["name"] for p in legs],
                "lineup": ([{"slot": "CPT", "name": cap["name"], "pos": cap["pos"], "team": cap["team"],
                             "salary": cap["cpt_salary"], "depth": cap.get("depth"),
                             "own": round(float(own_c[idx[i, 0]]), 1),
                             "proj": round(1.5 * cap["proj"], 1)}]
                           + [{"slot": "FLEX", "name": p["name"], "pos": p["pos"], "team": p["team"],
                               "salary": p["salary"], "depth": p.get("depth"),
                               "own": round(float(own_f[idx[i, k]]), 1),
                               "proj": p["proj"]} for k, p in zip(range(1, 6), legs)]),
                "salary": int(cap["cpt_salary"] + sum(p["salary"] for p in legs)),
                "proj": round(1.5 * cap["proj"] + sum(p["proj"] for p in legs), 1),
                "win_pct": round(100.0 * float(res["win"][i]), 4),
                "top1_pct": round(100.0 * float(res["top1"][i]), 2),
                "cash_pct": round(100.0 * float(res["cash"][i]), 1),
                "ev": round(float(res["ev"][i]), 2), "ev_dup": round(float(ev_dup[i]), 2),
                "roi_pct": round(100.0 * float(roi[i]), 1),
                "expected_copies": round(float(copies[i]), 1),
                "opt_worlds": int(res["opt"][i]),
                "opt_pct": round(100.0 * float(res["opt"][i]) / res["n_worlds"], 2),
                "rank_by": rank_by}
    by_win = ok[np.argsort(-res["win"][ok])][:top]
    by_ev = ok[np.argsort(-ev_dup[ok])][:top]
    by_top1 = ok[np.argsort(-res["top1"][ok])][:top]
    # the portfolio: greedy cover over the strongest allowed lineups by top-1%
    cand = ok[np.argsort(-res["top1"][ok])][:int(candidates)]
    kmax = max(portfolio_sizes)
    log(f"[tourney] scored ({t3 - t2:.0f}s); covering worlds with a portfolio of {kmax} "
        f"from the {len(cand):,} strongest...")
    chosen, p_any = portfolio(W, X, f, grid, cand, kmax, chunk=chunk)
    ports = {}
    for k in portfolio_sizes:
        kk = min(k, len(chosen))
        if not kk:
            continue
        ports[str(k)] = {"p_any_top1_pct": round(100.0 * p_any[kk - 1], 1),
                         "entries": [row(int(cand[j]), "cover") for j in chosen[:kk]]}
    t4 = time.time()
    # per-player: share of hindsight-optimal worlds at captain and at flex
    P = len(ents)
    opt_c = np.bincount(idx[:, 0], weights=res["opt"], minlength=P)
    opt_f = np.zeros(P, dtype=np.float64)
    for k in range(1, 6):
        opt_f += np.bincount(idx[:, k], weights=res["opt"], minlength=P)
    # the public's most popular build, so the sheet can say what the chalk is
    chalk_i = int(np.argmax(f))
    players_out = []
    for i, e in enumerate(ents):
        players_out.append({"name": e["name"], "pos": e["pos"], "team": e["team"],
                            "salary": e["salary"], "cpt_salary": e["cpt_salary"],
                            "depth": e.get("depth"), "field_only": bool(e.get("_field_only")),
                            "proj": e["proj"], "floor": e["floor"], "ceiling": e["ceiling"],
                            "field_cpt_pct": round(float(own_c[i]), 1),
                            "field_flex_pct": round(float(own_f[i]), 1),
                            "opt_cpt_pct": round(100.0 * float(opt_c[i]) / res["n_worlds"], 2),
                            "opt_flex_pct": round(100.0 * float(opt_f[i]) / res["n_worlds"], 2)})
    players_out.sort(key=lambda p: -(p["opt_cpt_pct"] + p["opt_flex_pct"]))
    return {"version": VERSION, "sport": "nfl", "draft_group_id": int(dg),
            "built_ts": int(time.time()),
            "contest": {k: contest.get(k) for k in ("id", "name", "entry_fee", "prize_pool",
                                                    "first_prize", "places_paid", "max_entries",
                                                    "entered", "max_entries_per_user", "starts")},
            "slate": {"n_players": slate.get("n_players"), "dropped": slate.get("dropped"),
                      "teams": sorted(teams), "week": week, "preseason": bool(preseason),
                      "field_only": sorted(field_only)},
            "sims": int(n_sims), "worlds": int(res["n_worlds"]),
            "lineups_legal": int(len(idx)), "lineups_allowed": int(allowed.sum()),
            "field_model": {"kind": "softmax over projected points",
                            "beta": round(beta, 4), "top_share_pct": round(100.0 * FIELD_TOP_SHARE, 2),
                            "top_copies": round(float(C * f.max()), 1),
                            "chalk": row(chalk_i, "chalk"),
                            "note": ("one knob: the most popular build holds "
                                     f"{100 * FIELD_TOP_SHARE:.1f}% of the field (a 231-way tie "
                                     "won DraftKings' 2021 GB-DET showdown Millionaire). "
                                     "Dollar figures assume the sim is the truth; read the ranks.")},
            "rules": list(nfl_dfs._SD_RULES),
            "top_win": [row(int(i), "win") for i in by_win],
            "top_ev": [row(int(i), "ev") for i in by_ev],
            "top_top1": [row(int(i), "top1") for i in by_top1],
            "portfolio": ports,
            "players": players_out,
            "excluded": excluded[:40],
            "timings": {"sims_s": round(t1 - t0, 1), "enumerate_s": round(t2 - t1, 1),
                        "score_s": round(t3 - t2, 1), "portfolio_s": round(t4 - t3, 1),
                        "total_s": round(t4 - t0, 1)}}
