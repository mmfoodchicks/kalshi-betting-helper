"""S7, second artifact: the ONE softmax knob fitted to the real Showdown fields.

The placeholder field is softmax(beta x projected points) over the legal
universe with beta solved so the top lineup holds 0.2% of entries. The first
S7 artifact (research/s7_field.py) put the real standings beside it, fit-free,
and the independent study and this repo agreed on the fences for the fit. This
module runs that fit and nothing more: no new feature, no owner rule, no
substitute for a missing projection.

The fences, as adopted (research/reports/s7_field.md section 8):

  Support first.  The public-field universe is every DraftKings pool player
  with a Sleeper projection under the game's teams, enumerated by production's
  own legality (under the cap, both teams) with the depth-chart gate LIFTED and
  no owner rule -- the field is not bound by our rules and every roster capture
  is post-game. Each board's representable share of the active entries is
  stated before any number that depends on it (DET @ BUF: a sixth of the field
  holds a player Sleeper never projected, and nothing here repairs that).

  Counts only.  beta is the maximum-likelihood value for the exact-lineup
  counts (captain identity included) over the representable universe. The
  log-likelihood of a one-parameter exponential family is concave in beta and
  its score is the moment condition E_beta[x] = mean observed x, so the MLE is
  solved by bisection on that condition and the residual is the checksum. The
  second moment is the family's first over-identifying test: Var_beta[x] is
  fixed once beta is, and the observed variance of x across entries is
  reported beside it.

  Ordering is beta-invariant.  In this family every ordering statistic (rank
  correlation, the real chalk's model rank, which lineups make the model's
  top k) is a property of the projections alone: beta rescales the
  probabilities monotonically and cannot reorder them. Those metrics are
  reported once per board and labelled so; the fit moves only the SHAPE of
  the distribution (the top share, the effective number of lineups, the bin
  calibration, the duplication curve).

  Transfer.  Six single-board directions (each board's beta graded on the
  other two) and leave-one-contest-out pooling, entry-weighted (every entry
  counts once) and contest-balanced (every contest counts once). Only DEN @ KC
  has pre-lock inputs; a transfer failure onto NE @ SEA or DET @ BUF may be
  their reconstruction rather than the family, and the tables say which board
  is which.

  Out of objective.  Captain ownership and position mix, flex ownership,
  structures, salary left, K/DST slots and duplication are graded AFTER the
  fit and never enter it. If one beta cannot carry them, that is a model-form
  finding, recorded before any parameter is added.

    python3 -m research.s7_beta
"""
import collections
import json
import math
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from research import s7_field as F     # noqa: E402  (the frozen exports, the parsers, the reconciliation)

DATA = F.DATA
CONTESTS = F.CONTESTS
SEED = F.SEED
CAP, CPT_MULT = F.CAP, F.CPT_MULT
#: the pre-lock label every table carries; the reader never has to look it up
INTEGRITY = {193391013: "post-game reconstruction (pool, roster and feeds captured after the game)",
             195526287: "highest integrity: pool, contest and feeds captured pre-lock (about 50 h before kickoff); roster post-game",
             195677825: "post-game reconstruction (pool, feeds and roster captured after the game)"}
BETA_HI = 12.0     # production's bisection bracket (dfs_tourney.field_weights); the MLEs land well inside it


# ---- the lifted universe ---------------------------------------------------
def lifted_pool(slate, week, roster_name=None, log=print):
    """Every DraftKings pool player with a Sleeper projection under the game's
    teams: production's pool builder and matcher (nfl_dfs.showdown_pool,
    nfl_dfs_sim.player_pool, nfl_dfs._pool_match) with the depth-chart gate
    NOT applied and no field-only cap. The projections are Sleeper's numbers
    (player_pool keeps `proj` as the public's line under either simulator), so
    they are byte-identical to the first artifact's; the 300 seeded worlds only
    exist because the pool builder runs the simulator to produce them."""
    import nfl_adp
    import nfl_dfs
    import nfl_dfs_sim
    import simulate
    from research import sd_board
    S = sd_board.feeds(F.FEEDS, week=week, roster_name=roster_name)
    S._cache.clear()
    recs, stamp = nfl_adp._PINNED
    nfl_adp._PINNED = ({k: dict(v, injury=None, injury_return=False) for k, v in recs.items()},
                       dict(stamp, injury_statuses="CLEARED (as in the first artifact); the gate itself is not applied here"))
    ents = nfl_dfs.showdown_pool(simulate.parse_dk_csv(slate["csv"]))
    teams = {e.get("team") for e in ents if e.get("team")}
    nfl_dfs_sim._random.seed(int(SEED))
    pool = nfl_dfs_sim.player_pool(week, n=300, preseason=False, teams=teams, discrete=True, model="legacy") or {}
    nidx, norm = nfl_dfs._norm_index(pool)
    kept, dropped = [], []
    for e in ents:
        sim = nfl_dfs._pool_match(pool, e["name"], e["pos"], e.get("team"), nidx, norm)
        if sim and sim.get("arr"):
            kept.append(dict(e, proj=round(float(sim["proj"]), 1)))
        else:
            dropped.append(e["name"])
    log(f"[S7b] lifted pool: {len(kept)} projected players of {len(ents)} in the DraftKings pool "
        f"({len(dropped)} without a Sleeper line under {'/'.join(sorted(teams))})")
    return kept, sorted(dropped), nfl_adp._PINNED[1]


def enumerate_lifted(players):
    """Production's enumeration (dfs_tourney.enumerate_showdown) with no
    entry or captain predicate: legal = under the cap, both teams. The
    (L, P) weight matrix it also builds is dropped at once; only the index
    rows and the lineup projection x are kept."""
    import dfs_tourney as T
    idx, W, _ = T.enumerate_showdown(players, CAP, cpt_mult=CPT_MULT)
    del W
    proj = np.asarray([float(p.get("proj") or 0.0) for p in players], dtype=np.float64)
    x = proj[idx[:, 0]] * CPT_MULT + proj[idx[:, 1:]].sum(axis=1)
    return idx, x


def _encode(idx, P):
    """One int64 per lineup: captain first, then the five flex indices in
    ascending order (enumerate_showdown yields them so; an observed entry's
    are sorted here). P^6 fits comfortably for P < 1,000."""
    key = np.asarray(idx[:, 0], dtype=np.int64)
    for k in range(1, 6):
        key = key * P + np.asarray(idx[:, k], dtype=np.int64)
    return key


def observed_counts(std, players, idx):
    """Exact-lineup counts (captain identity included) for every active entry
    whose six players are all in the lifted pool, keyed to the universe row;
    and the entries that are not, by reason. `legal_but_missing` must be zero:
    a real entry that the enumerator did not produce would mean the salary
    file and the standings disagree."""
    P = len(players)
    name_idx = {p["name"]: i for i, p in enumerate(players)}
    keys = _encode(idx, P)
    order = np.argsort(keys)
    sorted_keys = keys[order]
    counts = collections.Counter()
    unrepresentable, by_player, legal_missing = 0, collections.Counter(), 0
    for e in std["entries"]:
        if e["lineup"] is None:
            continue
        c, fl = e["lineup"]
        names = [c] + fl
        miss = [nm for nm in names if nm not in name_idx]
        if miss:
            unrepresentable += 1
            for nm in miss:
                by_player[nm] += 1
            continue
        k = name_idx[c]
        for v in sorted(name_idx[nm] for nm in fl):
            k = k * P + v
        pos = int(np.searchsorted(sorted_keys, k))
        if pos >= len(sorted_keys) or sorted_keys[pos] != k:
            legal_missing += 1
            continue
        counts[int(order[pos])] += 1
    rows = np.asarray(sorted(counts), dtype=np.int64)
    n = np.asarray([counts[r] for r in rows], dtype=np.float64)
    return rows, n, {"unrepresentable_entries": unrepresentable, "legal_but_missing": legal_missing,
                     "by_player": [{"name": nm, "entries": v} for nm, v in by_player.most_common(10)]}


# ---- the likelihood --------------------------------------------------------
class Board:
    """One contest's representable support: universe x, observed rows and
    counts, and the moment / likelihood functions of beta on it."""

    def __init__(self, cid, x, rows, n):
        self.cid = cid
        self.x = x - x.max()                # the top lineup's weight is exp(0), as in production
        self.x_max = float(x.max())
        self.rows, self.n = rows, n
        self.N = float(n.sum())
        self.xbar = float((self.x[rows] * n).sum() / self.N)
        self.x2bar = float((self.x[rows] ** 2 * n).sum() / self.N)

    def probs(self, beta):
        w = np.exp(beta * self.x)
        return w / w.sum()

    def moments(self, beta):
        p = self.probs(beta)
        m = float((p * self.x).sum())
        return m, float((p * self.x ** 2).sum()) - m * m

    def nll_per_entry(self, beta):
        """-(1/N) sum_l n_l log p_l(beta), nats per entry."""
        w = beta * self.x
        logZ = float(np.log(np.exp(w).sum()))
        return float(-(self.n * (w[self.rows] - logZ)).sum() / self.N)

    def saturated_nll(self):
        """The empirical distribution's own entropy: the floor no model beats."""
        q = self.n / self.N
        return float(-(q * np.log(q)).sum())


def solve_moment(boards, weights, lo=0.0, hi=BETA_HI):
    """beta with sum_c w_c (E_beta,c[x] - xbar_c) = 0; each term is increasing
    in beta (Var > 0), so the weighted sum is monotone and bisection is exact
    to the bracket's precision. weights = N_c for entry-weighted pooling, 1 for
    contest-balanced; a single board is either."""
    def g(b):
        return sum(w * (B.moments(b)[0] - B.xbar) for B, w in zip(boards, weights))
    if g(lo) > 0:
        return lo, "the observed mean projection is below the uniform mean: beta would be negative"
    if g(hi) < 0:
        return hi, "hit the bracket's top"
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if g(mid) < 0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi), None


def top_share_beta(B, top_share):
    """Production's rule re-solved on this universe: beta with the top lineup
    at `top_share` (dfs_tourney.field_weights' bisection, same bracket)."""
    lo, hi = 0.0, BETA_HI
    for _ in range(60):
        beta = 0.5 * (lo + hi)
        if 1.0 / np.exp(beta * B.x).sum() < top_share:
            lo = beta
        else:
            hi = beta
    return 0.5 * (lo + hi)


# ---- grading one beta on one board ------------------------------------------
def _duplication_expectation(lam):
    """Expected lineups and entries per copy-count bucket under Poisson(N f)
    per lineup. The 51-plus bucket is the complement of the first fifty terms
    (its lineups: sum 1 - P(k <= 50); its entries: sum lambda - E[k; k <= 50]),
    and the k >= 2 terms are summed only where lambda > 1e-4: below that the
    whole tail is under 5e-9 per lineup, 0.02 lineups over three million."""
    out = {}
    big = lam[lam > 1e-4]
    logl = np.log(big)
    cdf_n, cdf_e = np.zeros_like(big), np.zeros_like(big)     # P(k <= K), E[k; k <= K] so far
    p0 = np.exp(-lam)
    cdf_n += np.exp(-big); k_terms = {}
    for k in range(1, 51):
        pk = np.exp(-big + k * logl - math.lgamma(k + 1))
        k_terms[k] = (float(pk.sum()), float((k * pk).sum()))
        cdf_n += pk; cdf_e += k * pk
    for lo, hi in F.DUP_BUCKETS:
        key = f"{lo}-{hi if hi < 10 ** 9 else 'plus'}"
        if lo == 1 and hi == 1:
            nl, ne = float((lam * p0).sum()), float((lam * p0).sum())
        elif hi < 10 ** 9:
            nl = sum(k_terms[k][0] for k in range(lo, hi + 1)); ne = sum(k_terms[k][1] for k in range(lo, hi + 1))
        else:
            nl, ne = float((1.0 - cdf_n).sum()), float((big - cdf_e).sum())
        out[key] = (nl, ne)
    return out


def grade(B, beta, players, idx, pool, std, full=False):
    """Everything one beta implies on one board, against what the board did.
    The likelihood block is the objective; everything after `out_of_objective`
    was never fitted."""
    p = B.probs(beta)
    m1, var = B.moments(beta)
    obs_var = B.x2bar - B.xbar ** 2
    N = B.N
    nll = B.nll_per_entry(beta)
    order = np.argsort(-p)
    cum = np.cumsum(p[order])
    top10, top100, top1k = set(order[:10].tolist()), set(order[:100].tolist()), set(order[:1000].tolist())
    obs_in = lambda S: float(sum(n for r, n in zip(B.rows.tolist(), B.n.tolist()) if r in S) / N)
    lam = N * p
    pred_distinct = float((1.0 - np.exp(-lam)).sum())
    dup_pred, dup_obs = {}, {}
    exp_dup = _duplication_expectation(lam)
    for lo, hi in F.DUP_BUCKETS:
        key = f"{lo}-{hi if hi < 10 ** 9 else 'plus'}"
        nl, ne = exp_dup[key]
        m = (B.n >= lo) & (B.n <= hi)
        dup_pred[key] = {"lineups": round(nl, 1), "entry_share_pct": round(100 * ne / N, 2)}
        dup_obs[key] = {"lineups": int(m.sum()), "entry_share_pct": round(100 * float(B.n[m].sum()) / N, 2)}
    # bin calibration on the model's log10 probability
    logp = np.log10(np.maximum(p, 1e-300))
    bins = []
    for lo in range(-9, 0):
        m_all = (logp >= lo) & (logp < lo + 1)
        if not m_all.any():
            continue
        m_obs = (logp[B.rows] >= lo) & (logp[B.rows] < lo + 1)
        model_share = float(p[m_all].sum())
        obs_share = float(B.n[m_obs].sum() / N)
        bins.append({"log10_p": f"{lo}..{lo + 1}", "universe_lineups": int(m_all.sum()), "model_pct": round(100 * model_share, 3),
                     "observed_pct": round(100 * obs_share, 3), "observed_over_model": round(obs_share / model_share, 3) if model_share > 0 else None})
    out = {"beta": round(float(beta), 5),
           "likelihood": {"nll_per_entry_nats": round(nll, 5),
                          "moment_residual": round(m1 - B.xbar, 6),
                          "model_mean_x": round(m1 + B.x_max, 4), "observed_mean_x": round(B.xbar + B.x_max, 4),
                          "model_var_x": round(var, 4), "observed_var_x": round(obs_var, 4),
                          "var_ratio_observed_over_model": round(obs_var / var, 4) if var > 0 else None},
           "shape": {"max_share_pct": {"model": round(100 * float(p.max()), 4), "observed": round(100 * float(B.n.max()) / N, 4)},
                     "effective_lineups_inverse_sum_p2": {"model": round(float(1.0 / (p ** 2).sum()), 1),
                                                          "observed": round(float(1.0 / ((B.n / N) ** 2).sum()), 1)},
                     "distinct_lineups": {"model_expected": round(pred_distinct, 1), "observed": int(len(B.rows))},
                     "mass_in_model_top_k_pct": {"10": {"model": round(100 * float(cum[9]), 3), "observed": round(100 * obs_in(top10), 3)},
                                                 "100": {"model": round(100 * float(cum[99]), 3), "observed": round(100 * obs_in(top100), 3)},
                                                 "1000": {"model": round(100 * float(cum[999]), 3), "observed": round(100 * obs_in(top1k), 3)}},
                     "duplication": {"model": dup_pred, "observed": dup_obs},
                     "bins": bins}}
    if full:
        out["out_of_objective"] = out_of_objective(B, p, players, idx, pool, std)
    return out


def _l1(a, b):
    keys = set(a) | set(b)
    return round(sum(abs(a.get(k, 0.0) - b.get(k, 0.0)) for k in keys), 2)


def out_of_objective(B, p, players, idx, pool, std):
    """The metrics the fit never saw, model against the representable entries
    (the same support the likelihood was conditioned on) and, for the
    structural ones, against ALL active entries beside it. Distances are L1 in
    percentage points over the categories (0 = identical, 200 = disjoint)."""
    P = len(players)
    pos = np.asarray([q["pos"] for q in players]); team = np.asarray([q["team"] for q in players])
    sal = np.asarray([int(q["salary"]) for q in players]); csal = np.asarray([int(q["cpt_salary"]) for q in players])
    name = [q["name"] for q in players]
    own_c = 100 * np.bincount(idx[:, 0], weights=p, minlength=P)
    own_f = np.zeros(P)
    for k in range(1, 6):
        own_f += 100 * np.bincount(idx[:, k], weights=p, minlength=P)
    obs_c = 100 * np.bincount(idx[B.rows, 0], weights=B.n, minlength=P) / B.N
    obs_f = np.zeros(P)
    for k in range(1, 6):
        obs_f += 100 * np.bincount(idx[B.rows, k], weights=B.n, minlength=P) / B.N
    teams = sorted(set(team))
    a = (team[idx] == teams[0]).sum(axis=1)
    struct_key = np.asarray(["-".join(str(v) for v in sorted((s, 6 - s), reverse=True)) for s in range(7)])[a]
    kd = np.isin(pos[idx], ("K", "DST")).sum(axis=1)
    left = CAP - (csal[idx[:, 0]] + sal[idx[:, 1:]].sum(axis=1))
    cpos = pos[idx[:, 0]]
    w_obs = np.zeros(len(idx)); w_obs[B.rows] = B.n / B.N

    def dist(keys, w):
        d = collections.defaultdict(float)
        for k_, v in zip(keys, w):
            d[str(k_)] += float(v)
        return {k_: round(100 * v, 2) for k_, v in sorted(d.items())}
    # the whole active field's structural mix (the first artifact's numbers)
    E = [e for e in std["entries"] if e["lineup"] and all(nm in pool for nm in [e["lineup"][0]] + e["lineup"][1])]
    all_struct = collections.Counter(F._structure([e["lineup"][0]] + e["lineup"][1], pool) for e in E)
    all_cpos = collections.Counter(pool[e["lineup"][0]]["pos"] for e in E)
    all_kd = collections.Counter(sum(1 for nm in [e["lineup"][0]] + e["lineup"][1] if pool[nm]["pos"] in ("K", "DST")) for e in E)
    all_left = np.asarray([CAP - pool[e["lineup"][0]]["cpt_salary"] - sum(pool[nm]["salary"] for nm in e["lineup"][1]) for e in E], dtype=np.float64)
    pct = lambda cnt: {str(k): round(100 * v / len(E), 2) for k, v in sorted(cnt.items())}
    model_struct, obs_struct = dist(struct_key, p), dist(struct_key, w_obs)
    model_cpos, obs_cpos = dist(cpos, p), dist(cpos, w_obs)
    model_kd, obs_kd = dist(kd, p), dist(kd, w_obs)
    topc = np.argsort(-obs_c)[:12]; topf = np.argsort(-obs_f)[:12]
    return {"support": "model and 'observed' on the representable entries; 'all_active' is every active entry (the first artifact's basis)",
            "cpt_position_mix_pct": {"model": model_cpos, "observed": obs_cpos, "all_active": pct(all_cpos), "l1_pp": _l1(model_cpos, obs_cpos)},
            "structure_pct": {"model": model_struct, "observed": obs_struct, "all_active": pct(all_struct), "l1_pp": _l1(model_struct, obs_struct)},
            "k_dst_slots_pct": {"model": model_kd, "observed": obs_kd, "all_active": pct(all_kd), "l1_pp": _l1(model_kd, obs_kd)},
            "salary_left": {"model_mean": round(float((p * left).sum()), 1), "observed_mean": round(float((w_obs * left).sum()), 1),
                            "all_active_mean": round(float(all_left.mean()), 1),
                            "model_share_ge_1000_pct": round(100 * float(p[left >= 1000].sum()), 2),
                            "observed_share_ge_1000_pct": round(100 * float(w_obs[left >= 1000].sum()), 2)},
            "cpt_ownership_pct": {"l1_pp_over_all_players": round(float(np.abs(own_c - obs_c).sum()), 2),
                                  "max_abs_diff_pp": round(float(np.abs(own_c - obs_c).max()), 2),
                                  "top_observed": [{"name": name[i], "observed": round(float(obs_c[i]), 2), "model": round(float(own_c[i]), 2)} for i in topc]},
            "flex_ownership_pct": {"l1_pp_over_all_players": round(float(np.abs(own_f - obs_f).sum()), 2),
                                   "max_abs_diff_pp": round(float(np.abs(own_f - obs_f).max()), 2),
                                   "top_observed": [{"name": name[i], "observed": round(float(obs_f[i]), 2), "model": round(float(own_f[i]), 2)} for i in topf]}}


def ordering(B, players, idx, std, pool):
    """The beta-invariant statistics: every one is a function of x alone."""
    x = B.x
    rk = np.argsort(np.argsort(-x))            # 0 = the top projected lineup
    obs_rank = np.argsort(np.argsort(-B.n))
    at = {int(r): i for i, r in enumerate(B.rows.tolist())}   # universe row -> observed slot
    top_obs = int(B.rows[int(np.argmax(B.n))])
    name = [q["name"] for q in players]
    lineup = lambda r: {"cpt": name[int(idx[r, 0])], "flex": sorted(name[int(v)] for v in idx[r, 1:])}
    model_top = int(np.argmax(x))
    best = max(e["points"] for e in std["entries"])
    w = next(e for e in std["entries"] if e["points"] == best and e["lineup"])
    name_idx = {nm: i for i, nm in enumerate(name)}
    win = None
    if all(nm in name_idx for nm in [w["lineup"][0]] + w["lineup"][1]):
        key = (name_idx[w["lineup"][0]], tuple(sorted(name_idx[nm] for nm in w["lineup"][1])))
        hit = [r for r in np.where(idx[:, 0] == key[0])[0] if tuple(sorted(int(v) for v in idx[r, 1:])) == key[1]]
        if hit:
            r = int(hit[0])
            win = {"model_rank": int(rk[r]) + 1, "copies": int(B.n[at[r]]) if r in at else 0,
                   "points": best, "tied": sum(1 for e in std["entries"] if e["points"] == best and e["lineup"])}
    return {"note": "invariant to beta: the softmax is monotone in x, so no value of the knob can change any of these",
            "spearman_observed_count_vs_x_over_observed_lineups": round(float(np.corrcoef(obs_rank, rk[B.rows])[0, 1]), 4),
            "real_chalk": {"copies": int(B.n.max()), "model_rank": int(rk[top_obs]) + 1, **lineup(top_obs)},
            "model_top_lineup": {"observed_copies": int(B.n[at[model_top]]) if model_top in at else 0,
                                 "observed_rank": (int(obs_rank[at[model_top]]) + 1 if model_top in at else None),
                                 **lineup(model_top)},
            "observed_mass_in_model_top_k_pct": {str(k): round(100 * float(B.n[np.isin(B.rows, np.argsort(-x)[:k])].sum() / B.N), 3)
                                                 for k in (10, 100, 1000, 10000)},
            "winner": win}


# ---- the run -----------------------------------------------------------------
def build(cid, spec, log=print):
    from research import s6_capture
    path = os.path.join(F.STANDINGS, f"contest-standings-{cid}.csv.gz")
    std = F.load_standings(path)
    slate, details = s6_capture.replay(spec["dg"])
    pool = F.dk_pool(slate)
    players, dropped, roster_stamp = lifted_pool(slate, spec["week"], spec["roster"], log)
    idx, x = enumerate_lifted(players)
    rows, n, miss = observed_counts(std, players, idx)
    active = sum(1 for e in std["entries"] if e["lineup"])
    B = Board(cid, x, rows, n)
    support = {"active_entries": active, "representable_entries": int(B.N),
               "representable_pct": round(100 * B.N / active, 2),
               "unrepresentable_pct": round(100 * miss["unrepresentable_entries"] / active, 2),
               "unrepresentable_by_player": miss["by_player"], "legal_but_not_enumerated": miss["legal_but_missing"],
               "statement": (f"conditional on the {100 * B.N / active:.1f}% of active entries whose six players all carry a "
                             f"Sleeper projection under the game's teams; the other {100 * miss['unrepresentable_entries'] / active:.1f}% "
                             f"hold a player Sleeper never projected and are outside the likelihood, not repaired")}
    log(f"[S7b] {cid} {spec['label']}: {len(players)} players, {len(idx):,} legal lineups, "
        f"{support['representable_pct']}% of {active:,} active entries representable, {len(rows):,} distinct")
    return {"spec": spec, "std": std, "pool": pool, "players": players, "dropped": dropped, "roster_stamp": roster_stamp,
            "idx": idx, "B": B, "support": support, "detail": details[str(cid)]}


def run(log=print):
    import dfs_tourney as T
    from research import provenance, s6_capture, sd_board
    prior = json.load(open(os.path.join(DATA, "s7_field.json")))["contests"]
    boards = {cid: build(cid, spec, log) for cid, spec in CONTESTS.items()}
    ids = list(CONTESTS)
    # --- the fits
    own = {}
    for cid in ids:
        b, note = solve_moment([boards[cid]["B"]], [1.0])
        own[cid] = {"beta": b, "note": note}
        log(f"[S7b] {cid}: beta_hat {b:.4f}" + (f" ({note})" if note else ""))
    loo = {}
    for cid in ids:
        others = [c for c in ids if c != cid]
        bw, nw = solve_moment([boards[c]["B"] for c in others], [boards[c]["B"].N for c in others])
        bb, nb = solve_moment([boards[c]["B"] for c in others], [1.0] * len(others))
        loo[cid] = {"fitted_on": others, "entry_weighted": {"beta": bw, "note": nw}, "contest_balanced": {"beta": bb, "note": nb}}
    bw, nw = solve_moment([boards[c]["B"] for c in ids], [boards[c]["B"].N for c in ids])
    bb, nb = solve_moment([boards[c]["B"] for c in ids], [1.0] * len(ids))
    pooled = {"entry_weighted": {"beta": bw, "note": nw}, "contest_balanced": {"beta": bb, "note": nb}}
    # --- grading
    contests = {}
    for cid in ids:
        bd = boards[cid]; B = bd["B"]
        baselines = {"uniform": 0.0,
                     "production_beta_on_this_universe": float(prior[str(cid)]["model"]["beta"]),
                     "top_share_rule_resolved_here": top_share_beta(B, T.FIELD_TOP_SHARE)}
        candidates = {"own_mle": own[cid]["beta"],
                      **{f"transfer_from_{c}": own[c]["beta"] for c in ids if c != cid},
                      "loo_entry_weighted": loo[cid]["entry_weighted"]["beta"],
                      "loo_contest_balanced": loo[cid]["contest_balanced"]["beta"],
                      "pooled_all_entry_weighted": pooled["entry_weighted"]["beta"],
                      "pooled_all_contest_balanced": pooled["contest_balanced"]["beta"]}
        graded = {k: grade(B, v, bd["players"], bd["idx"], bd["pool"], bd["std"], full=(k in ("own_mle", "loo_entry_weighted", "loo_contest_balanced")))
                  for k, v in {**baselines, **candidates}.items()}
        # a scan so the reader can see the concavity and the optimum, not take it on trust
        scan = [{"beta": round(b_, 2), "nll_per_entry_nats": round(B.nll_per_entry(b_), 5)} for b_ in np.arange(0.0, 1.21, 0.05)]
        spec = bd["spec"]
        contests[str(cid)] = {
            "label": spec["label"], "integrity": INTEGRITY[cid], "captured": spec["captured"], "week": spec["week"],
            "support": bd["support"],
            "universe": {"players": len(bd["players"]), "legal_lineups": int(len(bd["idx"])),
                         "players_without_projection": bd["dropped"],
                         "gated_universe_in_first_artifact": {"players": prior[str(cid)]["model"]["players_in_universe"],
                                                              "lineups": prior[str(cid)]["model"]["universe_lineups"]},
                         "legality": "production's enumerate_showdown: under the $50,000 cap with the 1.5x captain salary, both teams; no owner rule, no depth gate, no field-only cap",
                         "roster": {**sd_board.roster_stamp(F.FEEDS, week=spec["week"], name=spec["roster"]),
                                    "injury_statuses": bd["roster_stamp"]["injury_statuses"]}},
            "inputs": {"draftkings": s6_capture.dk_hashes(spec["dg"])},
            "saturated_nll_per_entry_nats": round(B.saturated_nll(), 5),
            "uniform_nll_per_entry_nats": round(math.log(len(bd["idx"])), 5),
            "fit": {"own_mle": {**own[cid], "beta": round(own[cid]["beta"], 5)},
                    "leave_one_out": {"fitted_on": loo[cid]["fitted_on"],
                                      **{k: {**loo[cid][k], "beta": round(loo[cid][k]["beta"], 5)} for k in ("entry_weighted", "contest_balanced")}}},
            "nll_scan": scan,
            "graded": graded,
            "ordering_beta_invariant": ordering(B, bd["players"], bd["idx"], bd["std"], bd["pool"])}
    summary = []
    for cid in ids:
        c = contests[str(cid)]; g = c["graded"]
        summary.append({"contest": cid, "label": c["label"], "integrity": c["integrity"],
                        "representable_pct": c["support"]["representable_pct"],
                        "beta": {"own_mle": g["own_mle"]["beta"], "production": g["production_beta_on_this_universe"]["beta"],
                                 "top_share_rule_here": g["top_share_rule_resolved_here"]["beta"],
                                 "loo_entry_weighted": g["loo_entry_weighted"]["beta"], "loo_contest_balanced": g["loo_contest_balanced"]["beta"]},
                        "nll_per_entry": {k: g[k]["likelihood"]["nll_per_entry_nats"] for k in g},
                        "saturated": c["saturated_nll_per_entry_nats"], "uniform": c["uniform_nll_per_entry_nats"],
                        "var_ratio_at_own_mle": g["own_mle"]["likelihood"]["var_ratio_observed_over_model"],
                        "max_share_pct": {"observed": g["own_mle"]["shape"]["max_share_pct"]["observed"],
                                          "own_mle": g["own_mle"]["shape"]["max_share_pct"]["model"],
                                          "production": g["production_beta_on_this_universe"]["shape"]["max_share_pct"]["model"],
                                          "loo_entry_weighted": g["loo_entry_weighted"]["shape"]["max_share_pct"]["model"]},
                        "effective_lineups": {"observed": g["own_mle"]["shape"]["effective_lineups_inverse_sum_p2"]["observed"],
                                              "own_mle": g["own_mle"]["shape"]["effective_lineups_inverse_sum_p2"]["model"],
                                              "production": g["production_beta_on_this_universe"]["shape"]["effective_lineups_inverse_sum_p2"]["model"],
                                              "loo_entry_weighted": g["loo_entry_weighted"]["shape"]["effective_lineups_inverse_sum_p2"]["model"]},
                        "distinct_lineups": g["own_mle"]["shape"]["distinct_lineups"],
                        "out_of_objective_l1_pp_at_own_mle": {k: g["own_mle"]["out_of_objective"][k]["l1_pp"] for k in ("cpt_position_mix_pct", "structure_pct", "k_dst_slots_pct")},
                        "salary_left_mean": {"observed": g["own_mle"]["out_of_objective"]["salary_left"]["observed_mean"],
                                             "own_mle": g["own_mle"]["out_of_objective"]["salary_left"]["model_mean"]},
                        "ordering": {"spearman": c["ordering_beta_invariant"]["spearman_observed_count_vs_x_over_observed_lineups"],
                                     "real_chalk_model_rank": c["ordering_beta_invariant"]["real_chalk"]["model_rank"],
                                     "observed_mass_in_model_top_1000_pct": c["ordering_beta_invariant"]["observed_mass_in_model_top_k_pct"]["1000"]}})
    out = {"meta": {"stage": "S7 second artifact: the softmax field's one knob fitted by maximum likelihood to the exact-lineup counts of three real Showdown fields",
                    "contests": {str(cid): spec["label"] for cid, spec in CONTESTS.items()},
                    "inputs": {"sleeper_feeds": s6_capture.sleeper_hashes(F.FEEDS), "first_artifact": "research/data/s7_field.json"},
                    "fences": ["support first: the universe is every DraftKings pool player with a Sleeper projection under the game's teams, "
                               "production's legality, depth gate lifted, no owner rule; each board's representable share is stated before its fit",
                               "no substitute for a missing projection: entries holding an unprojected player are outside the likelihood and reported",
                               "counts only: beta maximises the likelihood of the exact-lineup counts (captain identity included); "
                               "captain mix, structures, salary left, K/DST and duplication are graded after the fit and never enter it",
                               "ordering statistics are invariant to beta in this family and are reported once per board, labelled so",
                               "transfer: six single-board directions and leave-one-contest-out pooling, entry-weighted and contest-balanced",
                               "leave-one-out is a reconstruction sensitivity: only DEN @ KC has pre-lock inputs",
                               "no new feature, no second parameter"],
                    "pooled_all_three": {k: {**v, "beta": round(v["beta"], 5)} for k, v in pooled.items()},
                    "fit_free": False,
                    "provenance": provenance.stamp(worlds=None, model="softmax(beta x projection) over the lifted universe; beta by maximum likelihood", seed=SEED)},
           "summary": summary, "contests": contests}
    with open(os.path.join(DATA, "s7_beta.json"), "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    return out


if __name__ == "__main__":
    run()
    print("S7 beta artifact written")
