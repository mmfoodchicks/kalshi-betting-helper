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

VERSION = 3                              # 3: every leaf a plain Python value (no numpy)
# What the board's numbers MEAN, as opposed to how they are stored (VERSION):
# bumped when the rules, the field or the evaluation change, so pc_worker
# rebuilds a board that was computed under the old semantics instead of
# re-saving it. 2: opponents parsed from the live feed (the defense rule
# and the field's bring-backs active for the first time), the per-game cap
# an explicit knob, candidates scored on worlds they were not found in.
ENGINE = 2


def available():
    return np is not None


def plain(obj):
    """`obj` with every numpy leaf replaced by its Python value (scalars to
    float/int/bool, arrays to lists), recursively; dicts, lists and tuples keep
    their shape. Every artifact leaves through here. The server has no numpy
    on purpose, so ONE numpy scalar anywhere in the pickle makes the whole
    board unreadable there: the first classic build (2026-09-10, 72 minutes)
    carried np.float64 in the ev_dup/roi_pct of every row (round() keeps the
    numpy type, and a json.dump check hides it because np.float64 subclasses
    float); the server ledgered BOARD-read x41, the tab said "not built yet"
    and the queued request looked like it never fired."""
    if isinstance(obj, dict):
        return {plain(k): plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [plain(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(plain(v) for v in obj)
    if np is not None:
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, np.generic):
            return obj.item()
    return obj


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
    # top 0.1%: in an 832,000-entry Millionaire first place is too rare to
    # rank on and the top 1% is 8,300 places; the top 832 is the money.
    top01 = _ncdf_arr((max(1, int(0.001 * C)) + 0.5 - m) / s)
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
    return {"F": F, "first": first, "top1": top1, "top01": top01, "cash": cash, "ev": ev,
            "C": C, "places": places, "entry_fee": float(entry_fee)}


_GRID_COLS = ("first", "top1", "top01", "cash", "ev")


def _grid_table(grids):
    """Every contest's five curves side by side, (n_grid, 5 x contests), so a
    world costs one gather however many contests share the slate."""
    return np.concatenate([np.stack([g[c] for c in _GRID_COLS], axis=1) for g in grids], axis=1)


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


def run_vs_field(Wc, Wf, wf, X, grids, chunk=500, progress=None):
    """Score candidates against a field in every world. Wc (K, P) candidate
    lineup weights, Wf (M, P) the field's lineups with probability wf (M,)
    summing to one (showdown: every legal lineup and the field model;
    classic: a sample from the field generator, 1/M each), X (P, N) player
    points per world, grids one payout_grid per contest. Returns
    ([{win, top1, top01, cash, ev} per contest], opt (K,), N): means over
    worlds, and how often each candidate scored highest of the candidates.

    World-major: each chunk's scores come out as (worlds, lineups) so a
    world is a contiguous row -- the (lineups, worlds) layout paid 25x on
    the per-world pass (9.9s vs 0.4s a chunk, measured)."""
    K, M, N = Wc.shape[0], Wf.shape[0], X.shape[1]
    F_grid = grids[0]["F"]
    G = _grid_table(grids)
    acc = np.zeros((K, G.shape[1]), dtype=np.float64)
    opt = np.zeros(K, dtype=np.int64)
    same = Wc is Wf
    WcT = np.ascontiguousarray(Wc.T)
    WfT = WcT if same else np.ascontiguousarray(Wf.T)
    chunk = chunk_for(max(K, M), chunk)
    t0 = time.time()
    for start in range(0, N, chunk):
        Xc = np.ascontiguousarray(X[:, start:start + chunk].T)   # (c, P)
        Sc = Xc @ WcT                                             # (c, K)
        opt += np.bincount(np.argmax(Sc, axis=1), minlength=K)
        Bc = _buckets(Sc)
        Bf = Bc if same else _buckets(Xc @ WfT)
        for j in range(Bc.shape[0]):
            acc += G[_world_tables(Bf[j], wf, F_grid)][Bc[j]]
        if progress:
            progress(min(N, start + chunk), N, time.time() - t0)
    out = []
    for gi in range(len(grids)):
        cols = acc[:, gi * 5:(gi + 1) * 5] / N
        out.append({"win": cols[:, 0], "top1": cols[:, 1], "top01": cols[:, 2],
                    "cash": cols[:, 3], "ev": cols[:, 4]})
    return out, opt, N


def run(W, X, f, grid, chunk=500, progress=None):
    """Showdown form: every legal lineup is both the candidate set and the
    field. Returns per-lineup means over worlds: win, top1, top01, cash, ev
    (fees not yet netted), plus the hindsight-optimal tallies."""
    res, opt, N = run_vs_field(W, W, f, X, [grid], chunk=chunk, progress=progress)
    r = res[0]
    return {"win": r["win"], "top1": r["top1"], "top01": r["top01"], "cash": r["cash"],
            "ev": r["ev"], "opt": opt, "n_worlds": N}


def portfolio_vs_field(Wc, Wf, wf, X, grids, cand_idx, k, chunk=500, rank="top1"):
    """Greedy cover over candidate lineups, per contest: each pick adds the
    most `rank` probability (top1 or top01) in the worlds the picks so far
    leave uncovered. Returns, per contest, (chosen candidate positions best
    first, P(at least one entry makes it) for every prefix length)."""
    if not len(cand_idx):
        return [([], []) for _ in grids]
    K, M, N = Wc.shape[0], Wf.shape[0], X.shape[1]
    F_grid = grids[0]["F"]
    same = Wc is Wf
    WfT = np.ascontiguousarray(Wf.T)
    WkT = np.ascontiguousarray(Wc[cand_idx].T)
    chunk = chunk_for(max(K, M), chunk)
    # per-world make-it probability for every candidate and contest,
    # (contests, K', N) float32, built once; the field pass is repeated (a
    # second matmul and bucket count) rather than kept from the scoring
    # pass: 60,000 worlds x 4,001 buckets would be a gigabyte of tables.
    P = np.zeros((len(grids), len(cand_idx), N), dtype=np.float32)
    tabs = [g[rank] for g in grids]
    for start in range(0, N, chunk):
        Xc = np.ascontiguousarray(X[:, start:start + chunk].T)
        Bf = _buckets(Xc @ WfT)
        Bk = _buckets(Xc @ WkT)
        for j in range(Bf.shape[0]):
            pos = _world_tables(Bf[j], wf, F_grid)
            for gi, tab in enumerate(tabs):
                P[gi, :, start + j] = tab[pos][Bk[j]]
    del same
    out = []
    for gi in range(len(grids)):
        chosen, miss = [], np.ones(N, dtype=np.float64)   # P(no pick makes it) per world
        p_any = []
        for _ in range(min(k, len(cand_idx))):
            gain = (miss[None, :] * P[gi]).sum(axis=1)
            if chosen:
                gain[chosen] = -1.0
            j = int(np.argmax(gain))
            chosen.append(j)
            miss = miss * (1.0 - P[gi, j])
            p_any.append(float(1.0 - miss.mean()))
        out.append((chosen, p_any))
    return out


def portfolio(W, X, f, grid, cand_idx, k, chunk=500):
    """Showdown form of portfolio_vs_field: one contest, the field is every
    legal lineup. Returns (chosen, p_any per prefix)."""
    return portfolio_vs_field(W, W, f, X, [grid], cand_idx, k, chunk=chunk)[0]


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
    try:
        st_sig, st_cls = status_sig([e["name"] for e in ents])
    except Exception as e:
        errlog.note("TOURN-status", e)
        st_sig, st_cls = None, {}
    return plain({"version": VERSION, "kind": "showdown", "sport": "nfl", "draft_group_id": int(dg),
            "built_ts": int(time.time()), "status_sig": st_sig, "status": st_cls,
            "pool_sig": pool_sig(slate["csv"]),
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
                        "total_s": round(t4 - t0, 1)}})


# ---- classic (nine slots) ------------------------------------------------------
# A 12-game main slate has ~400 projected players and 9 slots: trillions of
# legal lineups, so two things change from showdown and everything else
# stays. The FIELD is a sample (classic_sample, the generator's knobs pinned
# to published numbers), and OUR candidates are built: the exact best
# lineup of every simulated world (optimal_lineups -- the lineup that wins
# that world, by construction) plus rule-abiding draws from a sharper
# generator. Scoring, the payout curves, duplication, the hindsight tallies
# and the portfolio are the showdown machinery unchanged.
CL_SLOTS = ("QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "DST")
_CL_POS = ("QB", "RB", "WR", "TE", "DST")
_CL_CODE = {p: i for i, p in enumerate(_CL_POS)}
CL_FIELD_MAX_OWN = 0.38
# How sharp the public is: the most-owned player's share of Milly Maker
# lineups runs 30-45% in a normal week (a mega-chalk back at 45% is the
# top of it). Smooth, huge sample support, and it is the number the DFS
# sites publish every week, so it can be checked against the real sheet
# after lock. The first cut bisected on the sample's collision rate and
# chased noise: eight duplicate pairs among 8,000 draws is not a signal.
CL_FIELD_COLLISION = 1.2e-7    # reported, not fitted: P(two random entries hold the same lineup)
CL_STACK_DIST = (0.174, 0.490, 0.289, 0.047)
# Establish The Run (Levitan, Milly Maker trends): entries with a naked
# QB 17.4%, one teammate 49.0%, two 28.9%, three-plus 4.2% (the remainder
# folded into three-plus).
CL_BRING_BACK = 0.35        # a pass-catcher from the QB's opponent; assumed, not published
CL_SALARY_USED = 49400.0    # mean salary the field spends; assumed near the cap
CL_DST_VS_OWN_QB = 0.08     # share of the field that plays a defense against its own QB
_CL_KEEP = {"QB": 16, "RB": 40, "WR": 60, "TE": 20, "DST": 32}   # the solver's pool, by 90th pct
_CL_FIELD_KEEP = 60         # depth-gated players the field still plays (WR4s, third backs)
_CL_PUNT_SALARY = 3000
_CL_PUNT_ROLES = {"RB1", "RB2", "WR1", "WR2", "WR3", "TE1"}
# Roster concentration, two knobs. The team cap is the rule the owner set
# (QB plus two teammates at most). The per-game cap was written as four,
# but the opponent field it needs was blank on every live board (see
# nfl_dfs._matchup), so the optimizer has only ever run with the team cap
# binding -- six from one game is legal under it -- and the lineups the
# owner entered were built that way. None keeps that freedom on purpose:
# the cap is a policy decision for after the simulator work, not a side
# effect of fixing a parser. Measured 2026-09-10 on holdout worlds: a cap of
# four costs the top-40 about 5% of top 1%, 13% of top 0.1% and 25% of
# first place, and excludes both entered lineups.
CL_MAX_PER_TEAM = 3
CL_MAX_PER_GAME = None
_KNOB = object()                # "use the module knob" for classic_allowed


def _cap_rule():
    if CL_MAX_PER_GAME is None:
        return (f"at most {CL_MAX_PER_TEAM} from one team; no cap on players from one game "
                f"(CL_MAX_PER_GAME, a knob, decided after the simulator work)")
    return f"at most {CL_MAX_PER_GAME} players from one game and {CL_MAX_PER_TEAM} from one team"


CL_RULES = (
    "the quarterback is stacked with at least one of his receivers or tight ends",
    "no defense against our quarterback's team or our running backs' teams",
    _cap_rule(),
    "at most one punt under $3,000, and he must hold a role (RB1-2, WR1-3, TE1)",
    "roster gate: QB1, RB1-2, WR1-3, TE1-2 by Sleeper's depth chart; OUT/IR/doubtful out",
)


def _cl_state(q, r, w, t, d):
    return (((q * 4 + r) * 5 + w) * 2 + t) * 2 + d


_CL_S = 2 * 4 * 5 * 2 * 2
_CL_FINALS = (_cl_state(1, 2, 4, 1, 1), _cl_state(1, 3, 3, 1, 1))   # FLEX a WR, FLEX an RB


def _cl_transitions():
    """Per position, (from_states, to_states): roster counts (q, r, w, t, d)
    with r + w <= 6 (two backs, three receivers, one FLEX of either). A
    bijection per position, so adding a player is one fancy-indexed max."""
    out = {}
    for p in _CL_POS:
        fr, to = [], []
        for q in range(2):
            for r in range(4):
                for w in range(5):
                    for t in range(2):
                        for d in range(2):
                            if r + w > 6:
                                continue
                            nq, nr, nw, nt, nd = (q + (p == "QB"), r + (p == "RB"), w + (p == "WR"),
                                                  t + (p == "TE"), d + (p == "DST"))
                            if nq > 1 or nr > 3 or nw > 4 or nt > 1 or nd > 1 or nr + nw > 6:
                                continue
                            fr.append(_cl_state(q, r, w, t, d))
                            to.append(_cl_state(nq, nr, nw, nt, nd))
        out[p] = (np.asarray(fr, dtype=np.int64), np.asarray(to, dtype=np.int64))
    return out


def optimal_lineups(X, sal, pos, cap=50000, chunk=64, progress=None):
    """The best legal nine-man lineup of EVERY world: an exact 0/1 knapsack
    over players with the roster counts as state, vectorized across a chunk
    of worlds, backpointers kept per chunk and walked back per world. X (P,
    N) points, sal (P,) dollars, pos (P,) position strings. Returns (one
    sorted tuple of player indices per world -- empty when no legal lineup
    exists -- and the values (N,)). Measured: 3.6s per 64 worlds on a
    160-player pool here; the PC does the 20,000 worlds it is asked for in
    a coffee break."""
    P, N = X.shape
    s100 = np.asarray([int(v) // 100 for v in sal], dtype=np.int64)
    CAP = int(cap) // 100
    trans = _cl_transitions()
    pred = {p: dict(zip(to.tolist(), fr.tolist())) for p, (fr, to) in trans.items()}
    NEG = -1.0e6
    lineups, values = [], np.full(N, NEG, dtype=np.float64)
    take = None
    t0 = time.time()
    for start in range(0, N, chunk):
        Xc = np.ascontiguousarray(X[:, start:start + chunk], dtype=np.float32)
        c = Xc.shape[1]
        V = np.full((_CL_S, CAP + 1, c), NEG, dtype=np.float32)
        V[0, 0, :] = 0.0
        if take is None or take.shape[3] != c:
            take = np.zeros((P, _CL_S, CAP + 1, c), dtype=bool)
        else:
            take[:] = False
        for i in range(P):
            si = int(s100[i])
            if si > CAP:
                continue
            fr, to = trans[pos[i]]
            src = V[fr, :CAP + 1 - si, :]                 # gathered BEFORE the write: 0/1
            cand = src + Xc[i][None, None, :]
            cur = V[to, si:, :]
            better = (cand > cur) & (src > NEG / 2)
            V[to, si:, :] = np.where(better, cand, cur)
            take[i, to, si:, :] = better
        flat = V[list(_CL_FINALS)].reshape(-1, c)
        best = np.argmax(flat, axis=0)
        vals = flat[best, np.arange(c)]
        for j in range(c):
            f_, s = divmod(int(best[j]), CAP + 1)
            st = _CL_FINALS[f_]
            picks = []
            if vals[j] > NEG / 2:
                for i in range(P - 1, -1, -1):
                    if take[i, st, s, j]:
                        picks.append(i)
                        st = pred[pos[i]][st]
                        s -= int(s100[i])
                        if st == 0:
                            break
            ok = len(picks) == 9
            lineups.append(tuple(sorted(picks)) if ok else ())
            values[start + j] = float(vals[j]) if ok else NEG
        if progress:
            progress(min(N, start + chunk), N, time.time() - t0)
    return lineups, values


def _gumbel_pick(logits, elig, rng):
    """One player per row by Gumbel-max over the eligible ones: a softmax
    draw for every row at once. -1 where a row has nobody eligible."""
    g = rng.gumbel(size=elig.shape).astype(np.float32)
    z = np.where(elig, logits[None, :] + g, -np.inf)
    pick = np.argmax(z, axis=1)
    pick[~elig.any(axis=1)] = -1
    return pick


def classic_sample(players, n, rng, beta, kappa, cap=50000, stack_dist=CL_STACK_DIST,
                   bring_p=CL_BRING_BACK, dst_own_p=CL_DST_VS_OWN_QB, rules=False, exclude=None):
    """`n` classic lineups the way the public builds them, vectorized. A
    player's pull is beta * projection - kappa * salary / 1000 (beta the
    sharpness, kappa the price sensitivity; both calibrated by the caller).
    The quarterback first; then a stack count drawn from `stack_dist`
    (teammates at WR/TE by the same pull); a bring-back from his opponent
    with `bring_p`; then the remaining slots, cap-aware, DST last. With
    `rules` the stack is at least one and the defense never faces our QB
    or backs (the other rules are checked by classic_allowed). Returns idx
    (rows, 9) int32 -- QB, RB, RB, WR, WR, WR, TE, FLEX, DST -- rows that
    came out legal (a few percent die on the cap and are simply dropped)."""
    P = len(players)
    proj = np.asarray([float(p.get("proj") or 0.0) for p in players], dtype=np.float32)
    sal = np.asarray([int(p["salary"]) for p in players], dtype=np.int64)
    pc = np.asarray([_CL_CODE.get(p["pos"], -1) for p in players], dtype=np.int64)
    teams = sorted({p.get("team") or "" for p in players} | {p.get("opp") or "" for p in players})
    tcode = {t: i for i, t in enumerate(teams)}
    team = np.asarray([tcode[p.get("team") or ""] for p in players], dtype=np.int64)
    opp = np.asarray([tcode[p.get("opp") or ""] for p in players], dtype=np.int64)
    # kappa is points per $1,000 (the public's price of a projected point);
    # beta scales the whole thing, so sharpness and price sensitivity are
    # separate knobs and the two calibrations do not fight.
    logits = (beta * (proj - kappa * sal / 1000.0)).astype(np.float32)
    mins = {k: int(sal[pc == _CL_CODE[k]].min()) if (pc == _CL_CODE[k]).any() else 10 ** 9
            for k in _CL_POS}
    min_flex = min(mins["RB"], mins["WR"])
    idx = np.full((n, 9), -1, dtype=np.int32)
    used = np.zeros((n, P), dtype=bool)
    if exclude is not None:
        used[:, np.asarray(exclude, dtype=bool)] = True
    spent = np.zeros(n, dtype=np.int64)
    need = {"QB": np.full(n, 1), "RB": np.full(n, 2), "WR": np.full(n, 3), "TE": np.full(n, 1),
            "DST": np.full(n, 1), "FLEX": np.full(n, 1)}
    slot_of = {"RB": (1, 2), "WR": (3, 4, 5), "TE": (6,), "DST": (8,), "FLEX": (7,)}

    def floor_cost():
        return (need["QB"] * mins["QB"] + need["RB"] * mins["RB"] + need["WR"] * mins["WR"]
                + need["TE"] * mins["TE"] + need["DST"] * mins["DST"] + need["FLEX"] * min_flex)

    def place(rows, pick):
        """Record a pick for `rows` (pick (n,), -1 = none) into the right slot."""
        ok = rows & (pick >= 0)
        r = np.where(ok)[0]
        if not len(r):
            return
        p = pick[r]
        used[r, p] = True
        spent[r] += sal[p]
        code = pc[p]
        for kind, cd in (("RB", 1), ("WR", 2), ("TE", 3), ("DST", 4)):
            sel = r[code == cd]
            if not len(sel):
                continue
            main = need[kind][sel] > 0
            for k_, arr_ in ((kind, main), ("FLEX", ~main)):
                rows_k = sel[arr_]
                for row in rows_k:
                    for s_ in slot_of[k_]:
                        if idx[row, s_] < 0:
                            idx[row, s_] = pick[row]
                            break
                need[k_][rows_k] -= 1

    def elig_for(kinds, rows, extra=None):
        """(n, P) eligibility: kinds a set of position names, cap-aware."""
        e = np.zeros((n, P), dtype=bool)
        budget = int(cap) - spent - floor_cost()
        for kind in kinds:
            if kind == "FLEX":
                cols = (pc == 1) | (pc == 2)
                room = need["FLEX"] > 0
                relief = min_flex
            else:
                cols = pc == _CL_CODE[kind]
                room = need[kind] > 0
                relief = mins[kind]
            # a WR/RB may also take the FLEX slot once its own slots are full
            if kind in ("RB", "WR"):
                room = room | (need["FLEX"] > 0)
                relief = max(relief, min_flex)
            fit = sal[None, :] <= (budget + relief)[:, None]
            e |= (cols[None, :] & fit & room[:, None] & rows[:, None])
        e &= ~used
        if extra is not None:
            e &= extra
        return e

    allr = np.ones(n, dtype=bool)
    # 1. the quarterback
    qb = _gumbel_pick(logits, elig_for(("QB",), allr), rng)
    idx[:, 0] = qb
    ok = qb >= 0
    used[np.arange(n)[ok], qb[ok]] = True
    spent[ok] += sal[qb[ok]]
    need["QB"][ok] = 0
    qteam = np.where(ok, team[np.maximum(qb, 0)], -1)
    qopp = np.where(ok, opp[np.maximum(qb, 0)], -1)
    # 2. the stack: how many of his pass-catchers
    dist = np.asarray(stack_dist, dtype=np.float64)
    if rules:
        dist = dist.copy()
        dist[0] = 0.0
    dist = dist / dist.sum()
    k_stack = rng.choice(len(dist), size=n, p=dist)
    for step in range(1, len(dist)):
        rows = ok & (k_stack >= step)
        same_team = team[None, :] == qteam[:, None]
        pick = _gumbel_pick(logits, elig_for(("WR", "TE"), rows, extra=same_team), rng)
        place(rows, pick)
    # 3. the bring-back
    rows = ok & (rng.random(n) < bring_p)
    from_opp = team[None, :] == qopp[:, None]
    place(rows, _gumbel_pick(logits, elig_for(("WR", "TE"), rows, extra=from_opp), rng))
    # 4. the rest: backs, tight end, receivers, FLEX, defense last. The
    # stack count and the bring-back are OUTCOMES in the published numbers,
    # so the fill never adds a pass-catcher from either side of the QB's
    # game: what was drawn is what the lineup ends with.
    catcher = np.isin(pc, (2, 3))
    no_more = ~((team[None, :] == qteam[:, None]) | (team[None, :] == qopp[:, None])) | ~catcher[None, :]
    for kind, times in (("RB", 3), ("TE", 1), ("WR", 4), ("FLEX", 1)):
        for _ in range(times):
            if kind == "FLEX":
                rows = ok & (need["FLEX"] > 0)
                pick = _gumbel_pick(logits, elig_for(("FLEX",), rows, extra=no_more), rng)
            else:
                rows = ok & (need[kind] > 0)
                pick = _gumbel_pick(logits, elig_for((kind,), rows, extra=no_more), rng)
            place(rows, pick)
    off_teams = np.zeros((n, len(teams)), dtype=bool)
    for s_ in (0, 1, 2, 7):
        r = np.where(idx[:, s_] >= 0)[0]
        off_teams[r, team[idx[r, s_]]] = True
    if rules:
        avoid = off_teams[:, opp]                                   # (n, P): DST facing our QB / backs
    else:
        # the public mostly avoids a defense against its own quarterback
        careful = rng.random(n) >= dst_own_p
        avoid = (opp[None, :] == qteam[:, None]) & careful[:, None]
    rows = ok & (need["DST"] > 0)
    place(rows, _gumbel_pick(logits, elig_for(("DST",), rows, extra=~avoid), rng))
    good = (idx >= 0).all(axis=1) & (spent <= int(cap))
    return idx[good]


def classic_allowed(idx, players, max_per_game=_KNOB, max_per_team=_KNOB):
    """Our rulebook on a batch of lineups (rows, 9): stacked QB, no defense
    against our QB's or backs' teams, the team cap and (when set) the game
    cap, at most one sub-$3,000 punt and he holds a role. Field-only players
    (the depth gate's exclusions) fail it. The caps default to the module
    knobs; max_per_game=None means no per-game cap."""
    if max_per_game is _KNOB:
        max_per_game = CL_MAX_PER_GAME
    if max_per_team is _KNOB:
        max_per_team = CL_MAX_PER_TEAM
    teams = sorted({p.get("team") or "" for p in players} | {p.get("opp") or "" for p in players})
    tcode = {t: i for i, t in enumerate(teams)}
    team = np.asarray([tcode[p.get("team") or ""] for p in players])
    opp = np.asarray([tcode[p.get("opp") or ""] for p in players])
    game = np.minimum(team, opp) * len(teams) + np.maximum(team, opp)
    sal = np.asarray([int(p["salary"]) for p in players])
    pc = np.asarray([_CL_CODE.get(p["pos"], -1) for p in players])
    role = np.asarray([str(p.get("depth") or "").split("·")[0] in _CL_PUNT_ROLES for p in players])
    field_only = np.asarray([bool(p.get("_field_only")) for p in players])
    T = team[idx]                                              # (rows, 9)
    qb_team = T[:, 0]
    catchers = idx[:, [3, 4, 5, 6, 7]]
    stacked = ((team[catchers] == qb_team[:, None]) & np.isin(pc[catchers], (2, 3))).any(axis=1)
    dst_opp = opp[idx[:, 8]]
    dst_ok = (dst_opp != qb_team) & (dst_opp != T[:, 1]) & (dst_opp != T[:, 2]) \
        & ~((dst_opp == T[:, 7]) & (pc[idx[:, 7]] == 1))
    Gm = game[idx]
    per_game = np.max(np.stack([(Gm == Gm[:, [k]]).sum(axis=1) for k in range(9)], axis=1), axis=1)
    per_team = np.max(np.stack([(T == T[:, [k]]).sum(axis=1) for k in range(9)], axis=1), axis=1)
    punt = (sal[idx] < _CL_PUNT_SALARY) & (pc[idx] != 4)
    punts_ok = (punt.sum(axis=1) <= 1) & ~(punt & ~role[idx]).any(axis=1)
    game_ok = (per_game <= max_per_game) if max_per_game is not None else np.ones(len(idx), dtype=bool)
    return (stacked & dst_ok & game_ok & (per_team <= max_per_team)
            & punts_ok & ~field_only[idx].any(axis=1))


def _dup_share(idx):
    """The most common lineup's share of a sample (rows sorted by player)."""
    if not len(idx):
        return 0.0
    rows = np.sort(idx, axis=1)
    _u, counts = np.unique(rows, axis=0, return_counts=True)
    return float(counts.max()) / len(rows)


def _collision(idx):
    """Unbiased estimate of P(two random field entries are the same lineup)
    from a sample: colliding pairs over all pairs."""
    n = len(idx)
    if n < 2:
        return 0.0
    rows = np.sort(idx, axis=1)
    _u, counts = np.unique(rows, axis=0, return_counts=True)
    return float((counts * (counts - 1)).sum()) / (n * (n - 1))


def field_stats(idx, players):
    """What a sampled field actually does, measured on the sample: the
    most-owned player's share, the mean salary, the share of lineups with a
    bring-back (a pass-catcher from the QB's opponent), the share whose
    defense faces its own QB, and the stack-count distribution. The
    calibrator's targets are claims; these are the receipts, stamped on the
    board and checked by the guards. With a blank opponent field (the live
    boards before 2026-09-10) bring-backs read 0% and the defense share 0%."""
    idx = np.asarray(idx)
    if not len(idx):
        return {"n": 0}
    P = len(players)
    team = np.asarray([p.get("team") or "" for p in players])
    opp = np.asarray([p.get("opp") or "" for p in players])
    sal = np.asarray([int(p["salary"]) for p in players])
    pc = np.asarray([_CL_CODE.get(p["pos"], -1) for p in players])
    own = np.bincount(idx.ravel(), minlength=P) / len(idx)
    qb = idx[:, 0]
    catchers = idx[:, 3:8]
    is_catcher = np.isin(pc[catchers], (2, 3))
    stack = ((team[catchers] == team[qb][:, None]) & is_catcher).sum(axis=1)
    has_opp = opp[qb] != ""
    bring = ((team[catchers] == opp[qb][:, None]) & is_catcher).any(axis=1) & has_opp
    dst_own = (opp[idx[:, 8]] == team[qb]) & (opp[idx[:, 8]] != "")
    dist = np.bincount(np.minimum(stack, 3), minlength=4) / len(idx)
    return {"n": int(len(idx)), "max_own": float(own.max()),
            "mean_salary": float(sal[idx].sum(axis=1).mean()),
            "bring_back": float(bring.mean()), "dst_vs_own_qb": float(dst_own.mean()),
            "stack_dist": [float(x) for x in dist]}


def calibrate_field(players, rng, n=40000, max_own=CL_FIELD_MAX_OWN, salary_used=CL_SALARY_USED,
                    exclude=None, rounds=4, own_tol=0.01, salary_tol=100.0):
    """(beta, kappa) for classic_sample: kappa (points per $1,000) so the
    mean salary spent lands on `salary_used`, beta so the most-owned player
    holds `max_own` of the sample; the two bisections alternated until both
    land inside the tolerances (at most `rounds` times), because each moves
    the other. Returns (beta, kappa, top ownership, mean salary, collision,
    top build share), the middle two measured on a final fresh draw.

    Why the tolerances: the first cut stopped after two rounds with an
    eight-step beta bisection over [0, 3], a resolution of 0.047 in beta
    while ownership moves about 1.3 points per 0.01 of beta -- so a 38%
    target landed at 41% -- and its kappa had been fitted to the PREVIOUS
    beta, so a $49,400 target landed at $48,958 (measured 2026-09-10).
    Now: ten beta steps (0.003), kappa refitted after beta before the check,
    and the loop runs until the draw is within a point of ownership and $100
    of spend. The first round searches the full brackets (22 draws of `n`);
    later rounds bisect a narrow bracket round the last answer (17 draws),
    so the worst case at four rounds is 73 draws against the old cut's 30,
    and the usual case, one or two rounds, costs about what it did. When the
    two targets cannot both be met on a pool, a pattern search (at most 12
    more draws) lands the least combined miss instead of leaving the whole
    miss on whichever target the last bisection did not touch."""
    sal = np.asarray([int(p["salary"]) for p in players])
    P = len(players)

    def draw(beta, kappa):
        idx = classic_sample(players, n, rng, beta, kappa, exclude=exclude)
        if not len(idx):
            return idx, 0.0, 0.0
        own = np.bincount(idx.ravel(), minlength=P) / len(idx)
        return idx, float(sal[idx].sum(axis=1).mean()), float(own.max())

    def kappa_for(beta, lo, hi, steps):
        for _ in range(steps):
            kappa = 0.5 * (lo + hi)
            _idx, mean, _own = draw(beta, kappa)
            if mean > salary_used:
                lo = kappa                     # spending too much: charge more per dollar
            else:
                hi = kappa
        return 0.5 * (lo + hi)

    def beta_for(kappa, lo, hi, steps):
        for _ in range(steps):
            beta = 0.5 * (lo + hi)
            _idx, _mean, own = draw(beta, kappa)
            if own < max_own:
                lo = beta
            else:
                hi = beta
        return 0.5 * (lo + hi)
    beta, kappa = 0.5, 2.0
    idx, mean, own = None, 0.0, 0.0
    for _r in range(max(1, int(rounds))):
        if _r == 0:
            kappa = kappa_for(beta, -4.0, 6.0, 7)      # negative = the public pays up for stars
            beta = beta_for(kappa, 0.0, 3.0, 10)
        else:
            kappa = kappa_for(beta, kappa - 0.75, kappa + 0.75, 5)
            beta = beta_for(kappa, max(0.0, beta - 0.3), beta + 0.3, 7)
        kappa = kappa_for(beta, kappa - 0.75, kappa + 0.75, 5)   # kappa was fitted to the previous beta
        idx, mean, own = draw(beta, kappa)
        if abs(own - max_own) <= own_tol and abs(mean - salary_used) <= salary_tol:
            break
    if not (abs(own - max_own) <= own_tol and abs(mean - salary_used) <= salary_tol):
        # The two targets can conflict: on the 2026-09-10 pool every (beta,
        # kappa) that spends $49,400 holds Gibbs at 42% or more, so 38% and
        # $49,400 are not on the curve, and the alternation ends wherever its
        # last bisection left it (42.9% and $49,410, the whole miss on one
        # target). Finish with a pattern search on the combined miss in units
        # of the tolerances, so the miss is shared the way the tolerances
        # weigh it and the receipts on the board say what was reached.
        def miss(o, m):
            return ((o - max_own) / own_tol) ** 2 + ((m - salary_used) / salary_tol) ** 2
        best = (miss(own, mean), beta, kappa, idx, mean, own)
        db, dk = 0.02, 0.5
        for _ in range(3):
            improved = False
            for b2, k2 in ((best[1] + db, best[2]), (best[1] - db, best[2]),
                           (best[1], best[2] + dk), (best[1], best[2] - dk)):
                if b2 <= 0.0:
                    continue
                i2, m2, o2 = draw(b2, k2)
                if miss(o2, m2) < best[0]:
                    best = (miss(o2, m2), b2, k2, i2, m2, o2)
                    improved = True
            if not improved:
                db, dk = db / 2.0, dk / 2.0
        _m, beta, kappa, idx, mean, own = best
    return beta, kappa, own, mean, _collision(idx), _dup_share(idx)


def pool_sig(csv_text):
    """Who is in a DraftKings pool -- the playable names only, so a salary
    tweak never triggers an hour of the PC and a player dropped (OUT) does.
    The adapters stamp it on the board; pc_worker compares it."""
    import hashlib
    import nfl_dfs
    import simulate
    names = sorted(c["name"] for c in simulate.parse_dk_csv(csv_text) if nfl_dfs._playable(c))
    return hashlib.sha1("\n".join(names).encode()).hexdigest()[:16]


def lineup_matrix(idx, P, cpt_mult=None):
    """(rows, P) float32 weights from (rows, k) player indices."""
    W = np.zeros((len(idx), P), dtype=np.float32)
    ar = np.arange(len(idx))
    for k in range(idx.shape[1]):
        W[ar, idx[:, k]] = 1.0
    if cpt_mult is not None:
        W[ar, idx[:, 0]] = cpt_mult
    return W


def status_classes(names):
    """{name: in | q | out} from Sleeper's roster (the depth-chart source):
    OUT / IR / doubtful / not Active -> out, Questionable -> q, else in. The
    rebuild trigger compares these across builds: a projection drifting is
    not a reason to spend the PC's hour, a starter turning doubtful is."""
    import nfl_dfs
    recs, norm = nfl_dfs._depth_records()
    out = {}
    for nm in names:
        cls = "in"
        if norm is not None and recs:
            key = norm(nm or "")
            rec = recs.get(key) or recs.get(key.replace(" ", ""))
            if rec is not None:
                inj = (rec.get("injury") or "").upper()
                if (rec.get("status") or "Active") != "Active" or rec.get("active") is False \
                        or inj in nfl_dfs._INJ_OUT:
                    cls = "out"
                elif inj == "QUESTIONABLE":
                    cls = "q"
        out[nm] = cls
    return out


def status_sig(names):
    """A stable digest of status_classes over `names` (sorted)."""
    import hashlib
    cls = status_classes(names)
    body = "\n".join(f"{nm}:{cls[nm]}" for nm in sorted(cls))
    return hashlib.sha1(body.encode()).hexdigest()[:16], cls


def _slot_rows(lineups, pos):
    """Sorted player-index tuples -> (rows, 9) in CL_SLOTS order: the third
    back or fourth receiver takes FLEX."""
    out = np.full((len(lineups), 9), -1, dtype=np.int32)
    for r, L in enumerate(lineups):
        rb, wr = [], []
        for i in L:
            p = pos[i]
            if p == "QB":
                out[r, 0] = i
            elif p == "TE":
                out[r, 6] = i
            elif p == "DST":
                out[r, 8] = i
            elif p == "RB":
                rb.append(i)
            else:
                wr.append(i)
        for k, i in enumerate(rb[:2]):
            out[r, 1 + k] = i
        for k, i in enumerate(wr[:3]):
            out[r, 3 + k] = i
        extra = rb[2:] + wr[3:]
        if extra:
            out[r, 7] = extra[0]
    return out


def world_split(n_worlds, opt_worlds):
    """(generation, evaluation) world ranges, disjoint: the first `opt_worlds`
    worlds -- at most a third of them -- find the hindsight-optimal
    candidates, and only the rest score every candidate. A lineup chosen
    because it won world j must not be paid for world j: with the two sets
    overlapping (the first build) optimal-world lineups' first-place rate
    read 1.9x their holdout rate while top 1% and top 0.1% did not move
    (measured 2026-09-10, 4,000 generation worlds of 20,000). 60,000 worlds
    at 20,000 requested: 20,000 generate, 40,000 evaluate."""
    n = max(0, int(n_worlds))
    gen = max(0, min(int(opt_worlds), n // 3))
    return range(0, gen), range(gen, n)


def build_nfl_classic(dg, min_pool=1_000_000, contest_ids=None, n_sims=60000, n_worlds=None,
                      opt_worlds=20000, field_n=300000, cand_n=150000, cal_n=20000, chunk=500,
                      week=None, preseason=False, top=40, k_port=20, candidates=1200,
                      seed=None, log=print):
    """The tournament for a DraftKings classic slate, against every contest
    on it whose pool clears `min_pool` (or `contest_ids`). Returns the
    artifact the tab serves, or None when the slate, the contests or the
    pool cannot be read."""
    if np is None:
        raise RuntimeError("numpy is required for the tournament")
    import dk
    import nfl_dfs
    import nfl_dfs_sim
    import simulate
    rng = np.random.default_rng(seed)
    t0 = time.time()
    slate = dk.slate_for("nfl", draft_group_id=int(dg))
    if not slate:
        return None
    rows = slate.get("contests") or []
    if contest_ids:
        want = [int(c) for c in contest_ids]
    else:
        want = [int(c["id"]) for c in rows if float(c.get("prize_pool") or 0) >= float(min_pool)]
    contests = []
    for cid in want[:6]:
        c = dk.contest_detail(cid)
        if c and c.get("payouts"):
            contests.append(c)
    if not contests:
        return None
    contests.sort(key=lambda c: -float(c.get("prize_pool") or 0))
    csv_players = [c for c in simulate.parse_dk_csv(slate["csv"]) if nfl_dfs._playable(c)]
    if week is None:
        try:
            import nfl_game_sim
            import nfl_preseason
            preseason = nfl_preseason.is_preseason()
            week = nfl_game_sim.current_week(preseason)
        except Exception as e:
            errlog.note("TOURN-week", e)
            week = 1
    log(f"[tourney] classic {dg}: {len(csv_players)} players, {len(contests)} contest(s) "
        f"{', '.join(c['name'][:40] for c in contests)}; simulating week {week} x {n_sims:,}...")
    pool = nfl_dfs_sim.player_pool(week, n=int(n_sims), preseason=preseason) or {}
    if not pool:
        return None
    nidx, norm = nfl_dfs._norm_index(pool)
    ents, excluded = [], []
    for c in csv_players:
        pos = (c.get("pos") or "").upper().split("/")[0]
        if pos not in _CL_POS:
            continue
        sim = nfl_dfs._pool_match(pool, c["name"], pos, c.get("team"), nidx, norm)
        if not (sim and sim.get("arr")):
            excluded.append({"name": c["name"], "pos": pos, "team": c.get("team"),
                             "why": "no projection this week"})
            continue
        ents.append({"name": c["name"], "pos": pos, "team": c.get("team"),
                     "opp": nfl_dfs._opp_of(c.get("team"), c.get("game")),
                     "game": nfl_dfs.game_key(c.get("game")),
                     "salary": int(c["salary"]), "proj": round(float(sim["proj"]), 1),
                     "ceiling": sim.get("ceiling"), "floor": sim.get("floor"), "arr": sim["arr"]})
    by_name = {e["name"]: e for e in ents}
    ents, dx = nfl_dfs._apply_depth(ents, preseason)
    extra = []
    for d in dx:
        e = by_name.get(d["name"])
        if e is not None and "depth chart" in (d.get("why") or "") \
                and (e.get("proj") or 0) >= _FIELD_EXTRA_MIN_PROJ:
            e["_field_only"] = True
            e["depth"] = (d["why"].split(" ") or [""])[0]
            extra.append(e)
    extra.sort(key=lambda e: -e["proj"])
    extra = extra[:_CL_FIELD_KEEP]
    field_only = {e["name"] for e in extra}
    for d in dx:
        d["field"] = d["name"] in field_only
    excluded += dx
    ents = ents + extra
    have = {p: sum(1 for e in ents if e["pos"] == p and not e.get("_field_only")) for p in _CL_POS}
    if have["QB"] < 2 or have["RB"] < 4 or have["WR"] < 6 or have["TE"] < 2 or have["DST"] < 2:
        return None
    P = len(ents)
    N = min(len(e["arr"]) for e in ents)
    if n_worlds:
        N = min(N, int(n_worlds))
    X = np.asarray([e["arr"][:N] for e in ents], dtype=np.float32)
    sal = np.asarray([e["salary"] for e in ents])
    pos = [e["pos"] for e in ents]
    fo = np.asarray([bool(e.get("_field_only")) for e in ents])
    t1 = time.time()
    log(f"[tourney] classic {dg}: pool {P} players ({len(extra)} field-only) x {N:,} worlds "
        f"({t1 - t0:.0f}s); the best lineup of each of {min(N, opt_worlds):,} worlds...")
    # --- the hindsight-optimal lineup of every world (a pruned pool) ---
    keep = []
    for p in _CL_POS:
        ids = [i for i in range(P) if pos[i] == p]
        ids.sort(key=lambda i: -float(np.percentile(X[i], 90)))
        keep += ids[:_CL_KEEP[p]]
    keep = np.asarray(sorted(keep))
    gen, ev = world_split(N, opt_worlds)
    n_opt = len(gen)
    every = max(1, (n_opt // 4 // 64) * 64)
    opt_L, opt_v = optimal_lineups(
        X[keep][:, gen.start:gen.stop], sal[keep], [pos[i] for i in keep], chunk=64,
        progress=lambda done, n, dt: log(f"[tourney]   {done:,}/{n:,} worlds solved, {dt:.0f}s")
        if done % every == 0 or done == n else None)
    opt_L = [tuple(int(keep[i]) for i in L) for L in opt_L]
    opt_count = {}
    for L in opt_L:
        if L:
            opt_count[L] = opt_count.get(L, 0) + 1
    opt_player = np.zeros(P, dtype=np.int64)
    for L, k in opt_count.items():
        for i in L:
            opt_player[i] += k
    t2 = time.time()
    log(f"[tourney] classic {dg}: {len(opt_count):,} distinct optimal lineups over {n_opt:,} worlds "
        f"({t2 - t1:.0f}s); calibrating the field...")
    # --- the field ---
    beta, kappa, max_own, mean_sal, coll, top_share = calibrate_field(ents, rng, n=int(cal_n))
    idx_f = classic_sample(ents, int(field_n), rng, beta, kappa)
    M = len(idx_f)
    Wf = lineup_matrix(idx_f, P)
    wf = np.full(M, 1.0 / M)
    field_pct = 100.0 * Wf.mean(axis=0)
    rows_f = np.sort(idx_f, axis=1)
    _uf, first_f, counts_f = np.unique(rows_f, axis=0, return_index=True, return_counts=True)
    f_count = {tuple(rows_f[i].tolist()): int(k) for i, k in zip(first_f, counts_f)}
    achieved = field_stats(idx_f, ents)
    t3 = time.time()
    log(f"[tourney] classic {dg}: field of {M:,} (beta {beta:.3f}, kappa {kappa:.3f}; achieved: top ownership "
        f"{100 * achieved['max_own']:.1f}% of {100 * CL_FIELD_MAX_OWN:.0f}%, mean salary ${achieved['mean_salary']:,.0f} "
        f"of ${CL_SALARY_USED:,.0f}, bring-back {100 * achieved['bring_back']:.1f}% of {100 * CL_BRING_BACK:.0f}%, "
        f"defense vs own QB {100 * achieved['dst_vs_own_qb']:.2f}% ({100 * CL_DST_VS_OWN_QB:.0f}% of the field does not avoid it); "
        f"collision {coll:.2e}, top build {100 * top_share:.3f}%) ({t3 - t2:.0f}s); our candidates...")
    # --- our candidates: every world's optimal (allowed or not) + rule-abiding draws ---
    idx_o = _slot_rows(list(opt_count.keys()), pos)
    idx_c = classic_sample(ents, int(cand_n), rng, beta, kappa, rules=True, exclude=fo)
    idx_c = idx_c[classic_allowed(idx_c, ents)]
    # the public's three most common builds ride along (allowed or not) so
    # the chalk row on the sheet is the real chalk
    top_f = np.argsort(-counts_f)[:3]
    idx_chalk = _slot_rows([tuple(int(v) for v in _uf[i]) for i in top_f], pos)
    both = np.concatenate([idx_chalk, idx_o, idx_c], axis=0) if len(idx_o) else np.concatenate([idx_chalk, idx_c], axis=0)
    srt = np.sort(both, axis=1)
    _u, first = np.unique(srt, axis=0, return_index=True)
    idx_k = both[np.sort(first)]
    K = len(idx_k)
    allowed = classic_allowed(idx_k, ents)
    keys_k = [tuple(r.tolist()) for r in np.sort(idx_k, axis=1)]
    is_opt = np.asarray([opt_count.get(k, 0) for k in keys_k])
    copies_share = np.asarray([f_count.get(k, 0) / M for k in keys_k])
    Wc = lineup_matrix(idx_k, P)
    grids = [payout_grid(int(c.get("max_entries") or c.get("entered") or 10000),
                         c.get("payouts") or [], float(c.get("entry_fee") or 1.0),
                         int(c.get("places_paid") or 1)) for c in contests]
    t4 = time.time()
    log(f"[tourney] classic {dg}: {K:,} candidates ({int(allowed.sum()):,} we may enter, "
        f"{len(idx_o):,} hindsight-optimal) ({t4 - t3:.0f}s); scoring against the field in every world...")
    ch = chunk_for(max(K, M), chunk)
    Xe = np.ascontiguousarray(X[:, ev.start:ev.stop])      # the evaluation worlds only (world_split)
    every = max(ch, (len(ev) // 6 // ch) * ch)
    res, opt_c, _n = run_vs_field(
        Wc, Wf, wf, Xe, grids, chunk=ch,
        progress=lambda done, n, dt: log(f"[tourney]   {done:,}/{n:,} worlds, {dt:.0f}s")
        if done % every == 0 or done == n else None)
    t5 = time.time()
    ok = np.where(allowed)[0]
    cand = ok[np.argsort(-res[0]["top1"][ok])][:int(candidates)] if len(ok) else ok
    log(f"[tourney] classic {dg}: scored ({t5 - t4:.0f}s); portfolios of {k_port} per contest from "
        f"the {len(cand):,} strongest...")
    ports = portfolio_vs_field(Wc, Wf, wf, Xe, grids, cand, k_port, chunk=ch) if len(cand) else []
    t6 = time.time()

    def row(i, gi, rank_by):
        r = res[gi]
        c = contests[gi]
        C = int(c.get("max_entries") or c.get("entered") or 10000)
        fee = float(c.get("entry_fee") or 1.0)
        first = float(c.get("first_prize") or 0.0)
        copies = float(C * copies_share[i])                 # np.float64 here leaked into the pickle
        ev_dup = float(r["ev"][i]) - float(r["win"][i]) * first * (1.0 - 1.0 / (1.0 + copies))
        lineup = [{"slot": CL_SLOTS[k], "name": ents[idx_k[i, k]]["name"], "pos": ents[idx_k[i, k]]["pos"],
                   "team": ents[idx_k[i, k]]["team"], "salary": ents[idx_k[i, k]]["salary"],
                   "depth": ents[idx_k[i, k]].get("depth"), "proj": ents[idx_k[i, k]]["proj"],
                   "field_pct": round(float(field_pct[idx_k[i, k]]), 1)} for k in range(9)]
        return {"lineup": lineup, "names": [p["name"] for p in lineup],
                "salary": int(sum(p["salary"] for p in lineup)),
                "proj": round(float(sum(p["proj"] for p in lineup)), 1),
                "win_pct": round(100.0 * float(r["win"][i]), 4),
                "top1_pct": round(100.0 * float(r["top1"][i]), 2),
                "top01_pct": round(100.0 * float(r["top01"][i]), 3),
                "cash_pct": round(100.0 * float(r["cash"][i]), 1),
                "ev": round(float(r["ev"][i]), 2), "ev_dup": round(ev_dup, 2),
                "roi_pct": round(100.0 * (ev_dup - fee) / fee, 1),
                "expected_copies": round(float(copies), 1),
                "opt_worlds": int(is_opt[i]), "opt_pct": round(100.0 * float(is_opt[i]) / n_opt, 2),
                "allowed": bool(allowed[i]), "rank_by": rank_by}
    results = {}
    chalk_i = int(np.argmax(copies_share)) if K else None
    for gi, c in enumerate(contests):
        r = res[gi]
        C = int(c.get("max_entries") or c.get("entered") or 10000)
        first = float(c.get("first_prize") or 0.0)
        ev_dup = r["ev"] - r["win"] * first * (1.0 - 1.0 / (1.0 + C * copies_share))
        chosen, p_any = ports[gi] if ports else ([], [])
        results[str(c["id"])] = {
            "contest": {k: c.get(k) for k in ("id", "name", "entry_fee", "prize_pool", "first_prize",
                                              "places_paid", "max_entries", "entered",
                                              "max_entries_per_user", "starts")},
            "top_win": [row(int(i), gi, "win") for i in ok[np.argsort(-r["win"][ok])][:top]],
            "top_ev": [row(int(i), gi, "ev") for i in ok[np.argsort(-ev_dup[ok])][:top]],
            "top_top1": [row(int(i), gi, "top1") for i in ok[np.argsort(-r["top1"][ok])][:top]],
            "top_top01": [row(int(i), gi, "top01") for i in ok[np.argsort(-r["top01"][ok])][:top]],
            "portfolio": {"entries": [row(int(cand[j]), gi, "cover") for j in chosen],
                          "p_any_top1_pct": [round(100.0 * v, 1) for v in p_any]},
            "chalk": row(chalk_i, gi, "chalk") if chalk_i is not None else None,
        }
    players_out = []
    for i, e in enumerate(ents):
        players_out.append({"name": e["name"], "pos": e["pos"], "team": e["team"], "opp": e.get("opp"),
                            "game": e.get("game"),
                            "salary": e["salary"], "depth": e.get("depth"),
                            "field_only": bool(e.get("_field_only")),
                            "proj": e["proj"], "floor": e.get("floor"), "ceiling": e.get("ceiling"),
                            "field_pct": round(float(field_pct[i]), 1),
                            "opt_pct": round(100.0 * float(opt_player[i]) / n_opt, 2)})
    players_out.sort(key=lambda p: -p["opt_pct"])
    pivotal = {e["name"] for e in sorted(ents, key=lambda e: -e["proj"])[:60]}
    for rs in results.values():
        for lst in ("top_win", "top_ev", "top_top1", "top_top01"):
            for rw in rs[lst][:10]:
                pivotal.update(rw["names"])
        for rw in rs["portfolio"]["entries"]:
            pivotal.update(rw["names"])
    try:
        st_sig, st_cls = status_sig(sorted(pivotal))
    except Exception as e:
        errlog.note("TOURN-status", e)
        st_sig, st_cls = None, {}
    return plain({"version": VERSION, "engine": ENGINE, "kind": "classic", "sport": "nfl", "draft_group_id": int(dg),
            "built_ts": int(time.time()), "status_sig": st_sig, "status": st_cls,
            "pool_sig": pool_sig(slate["csv"]),
            "contests": [results[str(c["id"])]["contest"] for c in contests],
            "slate": {"n_players": slate.get("n_players"), "dropped": slate.get("dropped"),
                      "games": len({(e["team"], e.get("opp")) for e in ents}) // 2,
                      "week": week, "preseason": bool(preseason), "field_only": sorted(field_only)},
            "sims": int(n_sims), "worlds": int(N), "opt_worlds": int(n_opt), "eval_worlds": int(len(ev)),
            "max_per_game": CL_MAX_PER_GAME, "max_per_team": CL_MAX_PER_TEAM,
            # The first-place column is the share of evaluation worlds in
            # which the lineup beats every one of the field SAMPLE's
            # lineups: with 300,000 sampled against 832,000 real entries one
            # sampled exceedance takes a world from 1.0 to 0.06, so the
            # column is a count of clean sweeps of the sample, its level is
            # a property of the sample size, and two field seeds rank the
            # top 300 by it at Spearman 0.51 (measured 2026-09-10). EV and
            # ROI inherit it through the first prize. Kept for comparison
            # while the tail estimator is validated; the tab labels them.
            "experimental": {"columns": ["win_pct", "ev", "ev_dup", "roi_pct"],
                             "why": ("the first-place estimate rests on the field sample's extreme tail "
                                     "(one in 300,000 resolves; one in 832,000 is asked) and is still "
                                     "being validated; rank on top 1% and top 0.1%")},
            "candidates": int(K), "candidates_allowed": int(allowed.sum()),
            "optimal_distinct": int(len(opt_count)),
            "field_model": {"kind": "sampled: softmax on projection and price, stack and bring-back "
                                    "rates from the public record",
                            "n": int(M), "beta": round(beta, 4), "kappa": round(kappa, 4),
                            "max_own_pct": round(100.0 * max_own, 1),
                            # dst_vs_own_qb_not_avoiding is the share of the field that does
                            # not steer round a defense facing its own QB; the achieved share
                            # that actually holds one is that times the softmax's pick rate
                            # (about 0.3% on today's pool), so the two are not compared 1:1.
                            "targets": {"max_own": CL_FIELD_MAX_OWN, "mean_salary": CL_SALARY_USED,
                                        "bring_back": CL_BRING_BACK, "dst_vs_own_qb_not_avoiding": CL_DST_VS_OWN_QB},
                            "achieved": achieved,
                            "collision": coll, "mean_salary": round(mean_sal),
                            "top_share_pct": round(100.0 * top_share, 4),
                            "stack_dist": list(CL_STACK_DIST), "bring_back": CL_BRING_BACK,
                            "note": ("the field is a sample of the public's builds: stack rates from "
                                     "Establish The Run's Milly Maker record, sharpness set so the "
                                     f"most-owned player sits at {100 * CL_FIELD_MAX_OWN:.0f}%. Dollar "
                                     "figures assume the sim is the truth; read the ranks.")},
            "rules": list(CL_RULES),
            "results": results,
            "players": players_out,
            "excluded": excluded[:60],
            "timings": {"sims_s": round(t1 - t0, 1), "optimal_s": round(t2 - t1, 1),
                        "field_s": round(t3 - t2, 1), "candidates_s": round(t4 - t3, 1),
                        "score_s": round(t5 - t4, 1), "portfolio_s": round(t6 - t5, 1),
                        "total_s": round(t6 - t0, 1)}})
