"""Stage 3A and 3C: what the extreme tail and the duplicate count really
are, measured against a field big enough to resolve them.

The board's first-place column asks a question the field sample cannot
answer: with 300,000 sampled lineups, the smallest resolvable mass above
an entry is one in 300,000, while the contest asks about one in 832,000.
Everything that rides on that -- the win column, EV, ROI -- inherits the
granularity. This module builds the ground truth by brute force on a real
slate: a field of several million lineups, scored on several hundred
worlds, giving the exact mass above every candidate at each of those
worlds. Then the estimators a 300,000 sample can offer are judged against
it at 1e-3, 1e-4, 1e-5 and 1e-6:

  raw          the sample's own count, mass = (number above) / M
  Jeffreys     the same count smoothed by (k + 1/2) / (M + 1), which
               cannot say zero and is the standard interval-free fix
  tail model   a generalised Pareto fitted to the sample's own top scores
               above a threshold (peaks over threshold), which extrapolates
               past the last sampled lineup instead of stopping at it
  (importance sampling is not available here and the module says why:
   this field sampler has no closed-form density to reweight by)

Stage 3C rides along on the same field: the number of field entries that
are EXACTLY our lineup. The engine reads that from the field sample's
collision count, which at the tail is zero, one or two lineups; the big
field says what it converges to, and an independence baseline (the product
of the nine ownerships) says how far wrong the obvious shortcut is.

    python3 -m research.tails <dir with ents.json and X.npy>
"""
import collections
import json
import os
import subprocess
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DATA = os.path.join(ROOT, "research", "data")
REPORTS = os.path.join(ROOT, "research", "reports")
BASE_SEED = 20260910
BIG = 3_000_000          # the ground-truth field
SAMPLE = 300_000         # what a board actually draws
WORLDS = 400             # worlds the ground truth is resolved on
LEVELS = (1e-3, 1e-4, 1e-5, 1e-6)


def _commit():
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return "unknown"


def load_slate(d):
    ents = json.load(open(os.path.join(d, "ents.json")))
    X = np.load(os.path.join(d, "X.npy"))
    return ents, X


def draw_field(ents, n, rng, beta, kappa, chunk=500_000, rules=False, exclude=None):
    """`n` field lineups in chunks, as (n, 9) int32 sorted per row."""
    import dfs_tourney as T
    out = np.empty((n, 9), dtype=np.int32)
    at = 0
    while at < n:
        k = min(chunk, n - at)
        rep = {}
        idx = T.classic_sample(ents, k, rng, beta, kappa, rules=rules, exclude=exclude, report=rep)
        T.check_completion(rep, f"tail field chunk at {at}")
        out[at:at + len(idx)] = np.sort(idx, axis=1)
        at += len(idx)
    return out


def row_keys(idx):
    """A hashable key per lineup row, for counting duplicates."""
    a = idx.astype(np.int64)
    k = np.zeros(len(a), dtype=np.int64)
    for j in range(a.shape[1]):
        k = k * 257 + a[:, j]           # 243 players fit in a byte; nine of them in 63 bits
    return k


def score_rows(idx, Xw):
    """Every lineup's score in one world."""
    return Xw[idx].sum(axis=1)


def ground_truth(field, cands, X, worlds, log=print):
    """Exact field mass strictly above (and level with) every candidate, in
    every one of `worlds`."""
    F = np.empty((len(cands), len(worlds)), dtype=np.float64)
    Eq = np.empty((len(cands), len(worlds)), dtype=np.float64)
    M = len(field)
    t0 = time.time()
    for wi, w in enumerate(worlds):
        Xw = X[:, w].astype(np.float64)
        sf = np.sort(score_rows(field, Xw))
        sc = score_rows(cands, Xw)
        # counts on a 0.01 lattice, the engine's bucket
        bf = np.round(sf * 100).astype(np.int64)
        bc = np.round(sc * 100).astype(np.int64)
        hi = M - np.searchsorted(bf, bc, side="right")
        eq = np.searchsorted(bf, bc, side="right") - np.searchsorted(bf, bc, side="left")
        F[:, wi] = hi / M
        Eq[:, wi] = eq / M
        if log and (wi + 1) % 50 == 0:
            log(f"  world {wi + 1}/{len(worlds)} ({time.time() - t0:.0f}s)")
    return F, Eq


def gpd_fit(x, thr):
    """Generalised Pareto (shape, scale) on the excesses over `thr`, by
    probability-weighted moments -- stable on a few hundred points and
    needs no optimiser."""
    y = np.sort(x[x > thr] - thr)
    n = len(y)
    if n < 30:
        return None
    b0 = y.mean()
    b1 = float(np.sum(y * (np.arange(1, n + 1) - 1) / (n - 1))) / n
    if b0 <= 0 or 2 * b1 - b0 <= 0:
        return None
    k = b0 / (2 * b1 - b0) - 2.0
    a = b0 * (1.0 + k) * (2.0 + k) / (2.0 + 2.0 * k) if (1.0 + k) * (2.0 + k) != 0 else None
    if a is None or a <= 0:
        return None
    return -k, a                        # (shape xi, scale sigma) in the usual sign convention


def gpd_tail(sample_scores, s, q=0.98):
    """P(a field lineup scores above s) from the sample's own upper tail: the
    empirical rate down to the threshold, a fitted Pareto beyond it."""
    n = len(sample_scores)
    thr = float(np.quantile(sample_scores, q))
    fit = gpd_fit(sample_scores, thr)
    base = float((sample_scores > thr).mean())
    if fit is None:
        return None
    xi, sig = fit
    z = np.maximum(np.asarray(s, dtype=np.float64) - thr, 0.0)
    if abs(xi) < 1e-6:
        surv = np.exp(-z / sig)
    else:
        surv = np.power(np.maximum(1.0 + xi * z / sig, 1e-300), -1.0 / xi)
    out = base * surv
    return np.where(np.asarray(s) <= thr, np.nan, out), (xi, sig, thr, base, n)


def estimators(sample_scores, cand_scores, q=0.98):
    """What a 300,000-lineup sample can say about the mass above each
    candidate score: the raw count, the Jeffreys-smoothed count, and the
    fitted-tail extrapolation. Classical importance sampling is NOT among
    them, and the reason is worth writing down: it needs the probability
    the sampler assigns to a drawn lineup, and this sampler has no such
    closed form -- the salary reserve and the cap-aware renormalisation
    make a pick's probability depend on the partial roster's accumulated
    salary, so the density of a lineup is a sum over construction paths
    that is exactly the sum Stage 3C could not take either."""
    n = len(sample_scores)
    ss = np.sort(sample_scores)
    k = n - np.searchsorted(ss, cand_scores, side="right")
    raw = k / n
    jeff = (k + 0.5) / (n + 1.0)
    g = gpd_tail(sample_scores, cand_scores, q=q)
    gpd = g[0] if g else np.full(len(cand_scores), np.nan)
    diag = g[1] if g else None
    return {"raw": raw, "jeffreys": jeff, "gpd": gpd, "k": k, "gpd_diag": diag}


def level_calibration(field, sample, X, worlds, levels=LEVELS, q=0.98, tol=3.0):
    """The question the board actually asks, put directly: at each world take
    the SCORE whose true mass above is one in a thousand, ten thousand, a
    hundred thousand, a million -- the big field's own order statistics --
    and ask what a 300,000-lineup sample says the mass above that score is.
    A candidate lineup rarely sits that deep, so measuring on candidates
    alone (the table above) never reaches the decades that matter."""
    M, m = len(field), len(sample)
    acc = {f"{L:g}": {"raw": [], "jeffreys": [], "gpd": [], "true": []} for L in levels}
    for w in worlds:
        Xw = X[:, w].astype(np.float64)
        sf = np.sort(score_rows(field, Xw))
        ss = np.sort(score_rows(sample, Xw))
        thr = float(np.quantile(ss, q))
        fit = gpd_fit(ss, thr)
        base = float((ss > thr).mean())
        for L in levels:
            k = max(1, int(round(L * M)))
            s = float(sf[-k])
            true = (M - np.searchsorted(sf, s, side="right")) / M
            if true <= 0:
                continue
            kk = m - int(np.searchsorted(ss, s, side="right"))
            a = acc[f"{L:g}"]
            a["true"].append(true)
            a["raw"].append(kk / m)
            a["jeffreys"].append((kk + 0.5) / (m + 1.0))
            if fit is None or s <= thr:
                a["gpd"].append(np.nan)
            else:
                xi, sig = fit
                z = s - thr
                surv = np.exp(-z / sig) if abs(xi) < 1e-6 else np.power(max(1.0 + xi * z / sig, 1e-300), -1.0 / xi)
                a["gpd"].append(base * surv)
    out = {}
    for key, a in acc.items():
        t = np.asarray(a["true"])
        if len(t) < 10:
            out[key] = {"n": int(len(t))}
            continue
        row = {"n": int(len(t)), "true_median": float(np.median(t))}
        for name in ("raw", "jeffreys", "gpd"):
            e = np.asarray(a[name], dtype=np.float64)
            ok = np.isfinite(e)
            r = np.where(e[ok] > 0, e[ok] / t[ok], np.nan)
            row[name] = {"median_ratio": float(np.nanmedian(r)) if ok.any() else None,
                         "within_3x": float(np.nanmean((r >= 1.0 / tol) & (r <= tol))) if ok.any() else None,
                         "zero_share": float((e[ok] <= 0).mean()) if ok.any() else None,
                         "defined_share": float(ok.mean()),
                         "p10_ratio": float(np.nanquantile(r, 0.1)) if ok.any() else None,
                         "p90_ratio": float(np.nanquantile(r, 0.9)) if ok.any() else None}
        out[key] = row
    return out


def calibration(true_F, est, levels=LEVELS, tol=3.0):
    """For each decade of the true mass, how the estimators land: the median
    ratio to the truth, the share within `tol`x, and the share that says
    zero (which no payout curve can use)."""
    out = {}
    t = true_F.reshape(-1)
    for L in levels:
        m = (t >= L / np.sqrt(10.0)) & (t < L * np.sqrt(10.0))
        if m.sum() < 30:
            out[f"{L:g}"] = {"n": int(m.sum())}
            continue
        row = {"n": int(m.sum()), "true_median": float(np.median(t[m]))}
        for name in ("raw", "jeffreys", "gpd"):
            e = est[name].reshape(-1)[m]
            ok = np.isfinite(e)
            r = np.where(e[ok] > 0, e[ok] / t[m][ok], np.nan)
            row[name] = {"median_ratio": float(np.nanmedian(r)) if ok.any() else None,
                         "within_3x": float(np.nanmean((r >= 1.0 / tol) & (r <= tol))) if ok.any() else None,
                         "zero_share": float((e[ok] <= 0).mean()) if ok.any() else None,
                         "defined_share": float(ok.mean())}
        out[f"{L:g}"] = row
    return out


def duplication(field, sample, cands, ents, C):
    """Stage 3C: how many entries of a real contest are our exact lineup.
    The big field's count is the truth; the 300,000 sample's count is what
    the board uses; the independence baseline multiplies the nine sampled
    ownerships, which is what one would write down without thinking."""
    keys_big = collections.Counter(row_keys(field).tolist())
    keys_small = collections.Counter(row_keys(sample).tolist())
    own = np.bincount(sample.reshape(-1), minlength=len(ents)) / len(sample)
    kc = row_keys(cands)
    out = []
    for i, key in enumerate(kc.tolist()):
        nb, ns = keys_big.get(key, 0), keys_small.get(key, 0)
        p_ind = float(np.prod(own[cands[i]])) * 362880.0        # nine slots, any order
        out.append({"big_count": nb, "big_rate": nb / len(field), "sample_count": ns,
                    "sample_rate": ns / len(sample),
                    "copies_big": C * nb / len(field), "copies_sample": C * ns / len(sample),
                    "copies_independent": C * p_ind})
    return out, {"distinct_big": len(keys_big), "distinct_sample": len(keys_small),
                 "singletons_big": sum(1 for v in keys_big.values() if v == 1),
                 "max_copies_big": max(keys_big.values()) if keys_big else 0,
                 "collision_big": float(sum(v * v for v in keys_big.values())) / (len(field) ** 2),
                 "collision_sample": float(sum(v * v for v in keys_small.values())) / (len(sample) ** 2)}


def run(slate_dir, big=BIG, sample_n=SAMPLE, worlds=WORLDS, log=print):
    import dfs_tourney as T
    t0 = time.time()
    ents, X = load_slate(slate_dir)
    P, N = X.shape
    rng = np.random.default_rng(BASE_SEED)
    log(f"[3A] {P} players x {N:,} worlds; calibrating the field...")
    beta, kappa, max_own, mean_sal, coll, top_share = T.calibrate_field(ents, rng, n=20000)
    log(f"[3A] beta {beta:.3f} kappa {kappa:.3f}; drawing {big:,} field lineups...")
    field = draw_field(ents, big, np.random.default_rng(BASE_SEED + 1), beta, kappa)
    sample = field[:sample_n]                       # the board's sample is a prefix of the same draw
    log(f"[3A] {big:,} drawn ({time.time() - t0:.0f}s); candidates...")
    # candidates: the most popular builds, the best by projection, and a spread
    keys = row_keys(field)
    cnt = collections.Counter(keys[:sample_n].tolist())
    top_keys = [k for k, _ in cnt.most_common(20)]
    first_of = {}
    for i, k in enumerate(keys[:sample_n].tolist()):
        if k in top_keys and k not in first_of:
            first_of[k] = i
    chalk = np.stack([sample[first_of[k]] for k in top_keys])
    proj = np.asarray([float(p.get("proj") or 0.0) for p in ents])
    by_proj = np.argsort(-proj[sample].sum(axis=1))
    best = sample[by_proj[:40]]
    spread = sample[rng.choice(sample_n, 140, replace=False)]
    cands = np.unique(np.concatenate([chalk, best, spread]), axis=0)
    ws = np.linspace(0, N - 1, worlds).astype(int)
    log(f"[3A] {len(cands)} candidates x {len(ws)} worlds against {big:,}...")
    trueF, trueE = ground_truth(field, cands, X, ws, log=log)
    log(f"[3A] ground truth done ({time.time() - t0:.0f}s); the sample's estimators...")
    est = {"raw": np.empty_like(trueF), "jeffreys": np.empty_like(trueF), "gpd": np.empty_like(trueF)}
    diags = []
    for wi, w in enumerate(ws):
        Xw = X[:, w].astype(np.float64)
        ss = score_rows(sample, Xw)
        cs = score_rows(cands, Xw)
        e = estimators(ss, cs)
        for nm in est:
            est[nm][:, wi] = e[nm]
        if e["gpd_diag"]:
            diags.append(e["gpd_diag"])
    cal = calibration(trueF, est)
    log("[3A] the estimators at exact mass levels (the field's own order statistics)...")
    lvl = level_calibration(field, sample, X, ws)
    # threshold diagnostics for the fitted tail
    thr_diag = {}
    Xw = X[:, ws[len(ws) // 2]].astype(np.float64)
    ss = score_rows(sample, Xw)
    cs = score_rows(cands, Xw)
    tf = trueF[:, len(ws) // 2]
    for q in (0.90, 0.95, 0.98, 0.995):
        e = estimators(ss, cs, q=q)
        ok = np.isfinite(e["gpd"]) & (tf > 0)
        thr_diag[f"{q:g}"] = {"shape": (e["gpd_diag"] or (None,))[0], "scale": (e["gpd_diag"] or (None, None))[1],
                              "median_ratio": float(np.nanmedian(e["gpd"][ok] / tf[ok])) if ok.any() else None,
                              "n_above": int((ss > (e["gpd_diag"] or (0, 0, 0))[2]).sum()) if e["gpd_diag"] else 0}
    C = 832_000
    dup, dupstats = duplication(field, sample, cands, ents, C)
    # how far a 300,000 sample can resolve at all
    resolution = {"sample": sample_n, "smallest_mass": 1.0 / sample_n, "contest": C,
                  "entries_per_sampled_lineup": C / sample_n,
                  "worlds_with_zero_above": float((trueF == 0).mean()),
                  "sample_says_zero_share": float((est["raw"] == 0).mean()),
                  "truth_below_sample_resolution": float((trueF < 1.0 / sample_n).mean())}
    st = {"meta": {"stage": "3A+3C", "commit": _commit(), "base_seed": BASE_SEED, "slate_dir": os.path.basename(slate_dir.rstrip("/")),
                   "players": int(P), "sim_worlds": int(N), "field_big": int(big), "field_sample": int(sample_n),
                   "worlds_resolved": int(len(ws)), "candidates": int(len(cands)), "contest_entries": C,
                   "beta": float(beta), "kappa": float(kappa), "built": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
                   "seconds": round(time.time() - t0, 1)},
          "resolution": resolution, "calibration": cal, "level_calibration": lvl, "threshold_diagnostics": thr_diag,
          "duplication_stats": dupstats,
          "duplication": sorted(dup, key=lambda d: -d["big_count"])[:25],
          "duplication_all": dup}
    json.dump(st, open(os.path.join(DATA, "stage3a_tails.json"), "w"), indent=1, sort_keys=True)
    write_report(st)
    return st


def _f(x, d=3):
    return "-" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:.{d}f}"


def write_report(st):
    m, r = st["meta"], st["resolution"]
    L = ["# Stage 3A and 3C: the extreme tail and the duplicate count, measured\n",
         f"Commit {m['commit']}, base seed {m['base_seed']}, the real Sunday main slate ({m['players']} players, {m['sim_worlds']:,} simulated worlds). "
         f"Ground-truth field {m['field_big']:,} lineups, the board's sample {m['field_sample']:,} (a prefix of the same draw), resolved on {m['worlds_resolved']} worlds "
         f"for {m['candidates']} candidate lineups. Contest size {m['contest_entries']:,}. Built {m['built']} ({m['seconds']}s).\n",
         "## What a 300,000-lineup sample can and cannot resolve\n",
         f"- One sampled lineup stands for {r['entries_per_sampled_lineup']:.1f} entries of the contest; the smallest mass the sample can report is {r['smallest_mass']:.2e}, "
         f"against the 1 in {m['contest_entries']:,} that first place asks about. No smoothing can cross that: the sample has no draw there to smooth.",
         f"- Of the candidate lineups below, the true mass above sits under the sample's resolution in {100 * r['truth_below_sample_resolution']:.2f}% of (candidate, world) pairs "
         f"and the sample reports nobody above in {100 * r['sample_says_zero_share']:.2f}% -- a candidate lineup is rarely that deep in the field, which is why the second table "
         "below asks the question at the field's own order statistics instead.\n",
         "## The estimators against the truth, by decade of the true mass (candidate lineups)\n",
         "| True mass | pairs | median | raw: median ratio | within 3x | says zero | Jeffreys: ratio | within 3x | fitted tail: ratio | within 3x | defined |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for lev, v in st["calibration"].items():
        if v.get("n", 0) < 30:
            L.append(f"| {lev} | {v.get('n', 0)} | too few | | | | | | | | |")
            continue
        rw, je, gp = v["raw"], v["jeffreys"], v["gpd"]
        L.append(f"| {lev} | {v['n']:,} | {v['true_median']:.2e} | {_f(rw['median_ratio'], 2)} | {_f(rw['within_3x'], 2)} | {_f(rw['zero_share'], 2)} | "
                 f"{_f(je['median_ratio'], 2)} | {_f(je['within_3x'], 2)} | {_f(gp['median_ratio'], 2)} | {_f(gp['within_3x'], 2)} | {_f(gp['defined_share'], 2)} |")
    L.append("\n### The same question asked directly: the score whose true mass above is exactly one in N\n")
    L.append("A candidate lineup rarely sits a millionth of the way into the field, so the table above cannot reach the decades the first-place column lives in. "
             "Here the score itself is taken from the big field's order statistics at each world, and the 300,000-lineup sample is asked what mass sits above it.\n")
    L.append("| True mass above | worlds | raw: median ratio | 10th-90th | within 3x | says zero | Jeffreys: ratio | within 3x | fitted tail: ratio | within 3x |\n|---|---|---|---|---|---|---|---|---|---|")
    for lev, v in st.get("level_calibration", {}).items():
        if v.get("n", 0) < 10:
            L.append(f"| {lev} | {v.get('n', 0)} | too few | | | | | | | |")
            continue
        rw, je, gp = v["raw"], v["jeffreys"], v["gpd"]
        L.append(f"| {lev} | {v['n']:,} | {_f(rw['median_ratio'], 2)} | {_f(rw['p10_ratio'], 2)}-{_f(rw['p90_ratio'], 2)} | {_f(rw['within_3x'], 2)} | {_f(rw['zero_share'], 2)} | "
                 f"{_f(je['median_ratio'], 2)} | {_f(je['within_3x'], 2)} | {_f(gp['median_ratio'], 2)} | {_f(gp['within_3x'], 2)} |")
    L.append("\n### Threshold diagnostics for the fitted tail\n")
    L.append("| Quantile threshold | sample points above it | shape | scale | median ratio to the truth |\n|---|---|---|---|---|")
    for q, d in st["threshold_diagnostics"].items():
        L.append(f"| {q} | {d['n_above']:,} | {_f(d['shape'], 3)} | {_f(d['scale'], 2)} | {_f(d['median_ratio'], 2)} |")
    L.append("\nImportance sampling is not on this list. It needs the probability the field sampler assigns to a drawn lineup, and this sampler has none in closed form: "
             "the salary reserve and the cap-aware renormalisation make each pick's probability depend on the partial roster's accumulated salary, so a lineup's density is a sum over "
             "construction paths whose state is the set of players AND the money spent. That is the same sum Stage 3C set out to take by dynamic programming, and it is why 3C is measured instead.\n")
    d = st["duplication_stats"]
    L.append("## Stage 3C: how many entries are our exact lineup\n")
    L.append(f"The big field holds {d['distinct_big']:,} distinct lineups among {m['field_big']:,} draws ({100.0 * d['singletons_big'] / max(1, d['distinct_big']):.1f}% of them drawn once), "
             f"the most popular {d['max_copies_big']:,} times. Collision probability (two random entries identical) {d['collision_big']:.2e} on the big field, {d['collision_sample']:.2e} on the sample.\n")
    L.append("| Lineup | copies, 3,000,000 field | copies, 300,000 sample | copies if the nine picks were independent |\n|---|---|---|---|")
    for row in st["duplication"][:15]:
        L.append(f"| (a popular build) | {row['copies_big']:.1f} | {row['copies_sample']:.1f} | {row['copies_independent']:.2g} |")
    L.append("\nAn exact dynamic programme over the sampler's state was the plan and is not possible: the state is the set of players chosen AND the salary spent so far "
             "(the reserve changes which players are affordable), so the paths do not collapse. The count above is a direct measurement instead, and the table says how far "
             "the 300,000-lineup estimate the board uses sits from it.\n")
    with open(os.path.join(REPORTS, "stage3a_tails_report.md"), "w") as fh:
        fh.write("\n".join(L))


if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "research", "data", "slate")
    st = run(d)
    print("stage 3A/3C written;", json.dumps(st["resolution"]))
