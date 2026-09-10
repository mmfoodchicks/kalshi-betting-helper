"""Stage 2E, part one: fit the constrained simulator's parameters on the
training seasons (2022-2024) ONLY, and freeze them.

The fit matches the simulated moments (research.csim_fit.moments on
K seeded worlds per training game, common random numbers so the
objective is a deterministic function of the parameters) to the
observed moments of the same games, each mismatch measured against the
statistical uncertainty of the observed moment (a bootstrap over games
within season; a moment the data cannot pin down cannot pull the fit).
The objective is the mean squared standardised mismatch over the
moments. Search: Nelder-Mead from the provisional values, parameters
kept in bounds by a transform (sds and exponents non-negative).

What is recorded, so the fit can be judged and repeated: the seasons,
the base seed and K, the bootstrap scales, the start point, every
evaluation's objective, the fitted values with an approximate profile
interval per parameter (how far it can move, the rest held, before the
objective rises by one unit of z-squared), a second fit under different
common random numbers (the Monte Carlo uncertainty of the fit itself),
a smallest-model pass (each parameter neutralised in turn: the
objective's rise says what it buys; those that buy nothing are pinned
and the model refitted), and the final table of moments. The frozen
artifact research/data/csim_params.json carries the parameters, their
hash and this provenance; nfl_dfs_csim reads it and stamps boards with
it. 2025 is not read here in any form.

    python3 -m research.stage2e            (about half an hour)
"""
import json
import os
import subprocess
import sys
import time

import numpy as np

from research import hist_data as H
from research import hist_stats as S
from research import csim_fit as F

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DATA = os.path.join(ROOT, "research", "data")
REPORTS = os.path.join(ROOT, "research", "reports")
PARAMS_OUT = os.path.join(DATA, "csim_params.json")
BASE_SEED = 20220904
ALT_SEED = 20220905
K_FIT = 40
BOOTS = 200
# parameters and their neutral value (what "off" means) and the bound transform:
# every one is non-negative, and exponents are capped at 2
NEUTRAL = {"env_sd": 0.0, "env_rush": 0.0, "script": 0.0, "script_rush": 0.0, "att_sd": 0.0, "share_sd": 0.0, "cmp_sd": 0.0,
           "eff_sd": 0.0, "ypr_sd": 0.0, "td_vol": 0.0, "td_eff": 0.0, "rush_sd": 0.0, "rshare_sd": 0.0, "ypc_sd": 0.0, "rtd_vol": 0.0}
CAP = {"env_rush": 2.0, "td_vol": 2.0, "td_eff": 2.0, "rtd_vol": 2.0}
ORDER = tuple(NEUTRAL)
STEP = {"env_sd": 0.05, "env_rush": 0.3, "script": 0.05, "script_rush": 0.05, "att_sd": 0.05, "share_sd": 0.1, "cmp_sd": 0.1,
        "eff_sd": 0.05, "ypr_sd": 0.15, "td_vol": 0.3, "td_eff": 0.3, "rush_sd": 0.08, "rshare_sd": 0.1, "ypc_sd": 0.15, "rtd_vol": 0.3}


def _commit():
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return "unknown"


def to_x(params):
    return np.asarray([float(params[k]) for k in ORDER])


def to_params(x):
    out = {}
    for k, v in zip(ORDER, x):
        v = abs(float(v))
        if k in CAP:
            v = min(CAP[k], v)
        out[k] = v
    return out


def bootstrap_scales(games, rng, boots=BOOTS):
    """Bootstrap sd of every observed moment: games resampled with
    replacement within each season, the moments recomputed each time."""
    obs = F.observed_sample(games)
    by_season = {}
    for i, gm in enumerate(obs):
        by_season.setdefault(gm["key"][0], []).append(i)
    reps = []
    for _ in range(boots):
        pick = []
        for s, idx in by_season.items():
            pick += list(rng.choice(idx, size=len(idx), replace=True))
        reps.append(F.moments([obs[i] for i in pick]))
    keys = [k for k in reps[0] if not k.startswith("_") and not k.endswith(" n")]
    scales = {}
    for k in keys:
        vals = np.asarray([r.get(k) for r in reps if r.get(k) is not None], dtype=float)
        if len(vals) >= boots // 2:
            scales[k] = float(vals.std())
    return scales


def objective_with_scales(sim_m, obs_m, scales, floor=0.25):
    """Mean squared (sim - obs) / max(bootstrap sd, floor * fixed scale)."""
    terms = []
    for k, s, o, fixed in F.objective_terms(sim_m, obs_m):
        sc = scales.get(k)
        sc = max(sc if sc is not None else fixed, floor * fixed)
        terms.append((k, s, o, sc))
    return float(sum(((s - o) / sc) ** 2 for _, s, o, sc in terms)) / max(1, len(terms)), terms


class Fitter:
    def __init__(self, games, obs_m, scales, K, base_seed, log=print):
        self.games, self.obs_m, self.scales, self.K, self.base_seed, self.log = games, obs_m, scales, K, base_seed, log
        self.history = []

    def f(self, x):
        p = to_params(x)
        P = {"params": p, "fitted": False, "hash": "", "meta": {}}
        sim_m = F.moments(F.simulate_sample(self.games, P, self.K, self.base_seed))
        val, _ = objective_with_scales(sim_m, self.obs_m, self.scales)
        self.history.append((val, p))
        return val

    def fit(self, start, iters=250, tol=2e-4):
        t0 = time.time()
        x0 = to_x(start)
        step = np.asarray([STEP[k] for k in ORDER])
        def lg(it, best, x):
            if it % 20 == 0:
                self.log(f"  iter {it}: objective {best:.3f} ({time.time() - t0:.0f}s, {len(self.history)} evaluations)")
        xb, fb, evals = F.nelder_mead(self.f, x0, step, iters=iters, tol=tol, log=lg)
        self.log(f"  done: objective {fb:.3f} after {evals} evaluations, {time.time() - t0:.0f}s")
        return to_params(xb), fb, evals


def profile(fitter, best, fb, delta):
    """For each parameter, the range (the others held at their fitted
    values) over which the objective stays within `delta` of the minimum,
    found by stepping out from the optimum in both directions."""
    out = {}
    T = len(F.objective_terms(fitter.obs_m, fitter.obs_m))
    for k in ORDER:
        lo = hi = best[k]
        for sign in (-1, 1):
            step = max(STEP[k] * 0.5, 0.02)
            v = best[k]
            edge = v
            for _ in range(6):
                v = v + sign * step
                if v < 0 or (k in CAP and v > CAP[k]):
                    break
                p = dict(best); p[k] = v
                val = fitter.f(to_x(p))
                if val - fb > delta:
                    break
                edge = v
            if sign < 0:
                lo = edge
            else:
                hi = edge
        out[k] = {"lo": round(lo, 4), "hi": round(hi, 4)}
    out["_delta"] = delta; out["_terms"] = T
    return out


def smallest_model(fitter, best, fb):
    """Neutralise each parameter in turn; the rise in the objective is
    what it buys. Parameters that buy less than one unit of mean z^2
    are candidates to pin."""
    out = {}
    for k in ORDER:
        p = dict(best); p[k] = NEUTRAL[k]
        val = fitter.f(to_x(p))
        out[k] = {"objective_without": round(val, 3), "rise": round(val - fb, 3)}
    return out


def run(log=print):
    t0 = time.time()
    rows = H.read_csv_gz(os.path.join(DATA, "player_weeks_2022_2025.csv.gz"))
    games = F.games_from_table(rows, S.TRAIN)
    obs_m = F.moments(F.observed_sample(games))
    rng = np.random.default_rng(BASE_SEED)
    log(f"[2E] {len(games)} training games; bootstrapping the observed moments ({BOOTS} replicates)...")
    scales = bootstrap_scales(games, rng, BOOTS)
    import nfl_dfs_csim as C
    start = dict(C.DEFAULT_PARAMS)
    log(f"[2E] fitting from the provisional values, K={K_FIT}, base seed {BASE_SEED}...")
    fitter = Fitter(games, obs_m, scales, K_FIT, BASE_SEED, log)
    best, fb, evals = fitter.fit(start)
    log("[2E] smallest-model pass...")
    sm = smallest_model(fitter, best, fb)
    pinned = [k for k, v in sm.items() if v["rise"] < 1.0 / max(1, len(F.objective_terms(obs_m, obs_m))) * 1.0 and best[k] > 0]
    # a parameter that buys less than one unit of z^2 over the whole
    # objective is pinned at its neutral value and the rest refitted
    refit = None
    if pinned:
        log(f"[2E] pinning {pinned} and refitting...")
        start2 = dict(best)
        for k in pinned:
            start2[k] = NEUTRAL[k]
        fitter2 = Fitter(games, obs_m, scales, K_FIT, BASE_SEED, log)
        keep = [k for k in ORDER if k not in pinned]
        def f2(xk):
            p = dict(start2)
            for k, v in zip(keep, xk):
                p[k] = abs(float(v)) if k not in CAP else min(CAP[k], abs(float(v)))
            return fitter2.f(to_x(p))
        xk, fk, ek = F.nelder_mead(f2, np.asarray([start2[k] for k in keep]), np.asarray([STEP[k] for k in keep]), iters=300, tol=2e-4)
        best2 = dict(start2)
        for k, v in zip(keep, xk):
            best2[k] = abs(float(v)) if k not in CAP else min(CAP[k], abs(float(v)))
        refit = {"pinned": pinned, "params": best2, "objective": fk, "evaluations": ek}
        if fk <= fb + 1.0 / max(1, len(F.objective_terms(obs_m, obs_m))):
            best, fb = best2, fk
            log(f"[2E] reduced model kept: objective {fk:.3f}")
        else:
            log(f"[2E] reduced model rejected: objective {fk:.3f} vs {fb:.3f}")
    T = len(F.objective_terms(obs_m, obs_m))
    log("[2E] profile intervals...")
    prof = profile(fitter, best, fb, delta=max(1.0, fb) / T)
    log(f"[2E] second fit under different common random numbers (seed {ALT_SEED})...")
    fitter_alt = Fitter(games, obs_m, scales, K_FIT, ALT_SEED, log)
    best_alt, fb_alt, evals_alt = fitter_alt.fit(best, iters=120)
    # the final moment table at the frozen values, K larger for a clean read
    P = {"params": best, "fitted": True, "hash": C.params_hash(best), "meta": {}}
    sim_m = F.moments(F.simulate_sample(games, P, 120, BASE_SEED))
    val_final, terms = objective_with_scales(sim_m, obs_m, scales)
    terms.sort(key=lambda t: -abs((t[1] - t[2]) / t[3]))
    meta = {"stage": "2E-fit", "commit": _commit(), "train_seasons": list(S.TRAIN), "holdout_season": H.HOLDOUT, "holdout_outcomes_read": False,
            "base_seed": BASE_SEED, "alt_seed": ALT_SEED, "k_fit": K_FIT, "k_final": 120, "bootstraps": BOOTS, "games": len(games),
            "objective_start": fitter.history[0][0], "objective": fb, "objective_final_k120": val_final, "terms": T, "evaluations": evals,
            "start": start, "built": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "seconds": round(time.time() - t0, 1),
            "method": "Nelder-Mead on the mean squared standardised moment mismatch, scales from a within-season game bootstrap of the observed moments, common random numbers"}
    art = {"meta": meta, "params": best, "hash": C.params_hash(best)}
    json.dump(art, open(PARAMS_OUT, "w"), indent=1, sort_keys=True)
    st = {"meta": meta, "hash": art["hash"], "params": best, "scales": scales, "profile": prof, "smallest_model": sm, "refit": refit,
          "alt_fit": {"params": best_alt, "objective": fb_alt, "evaluations": evals_alt,
                      "max_abs_diff": max(abs(best_alt[k] - best[k]) for k in ORDER),
                      "diff": {k: round(best_alt[k] - best[k], 4) for k in ORDER}},
          "history": [(round(v, 4), {k: round(x, 4) for k, x in p.items()}) for v, p in fitter.history[:: max(1, len(fitter.history) // 60)]],
          "final_terms": [{"moment": k, "sim": round(s, 4), "obs": round(o, 4), "scale": round(sc, 4), "z": round((s - o) / sc, 2)} for k, s, o, sc in terms],
          "observed_moments": {k: v for k, v in obs_m.items() if not k.startswith("_")},
          "fitted_moments": {k: v for k, v in sim_m.items() if not k.startswith("_")}}
    json.dump(st, open(os.path.join(DATA, "stage2e_fit_stats.json"), "w"), indent=1, sort_keys=True)
    write_report(st)
    return st


def write_report(st):
    m = st["meta"]
    L = ["# Stage 2E, part one: the constrained simulator fitted on 2022 to 2024 and frozen\n",
         f"Commit {m['commit']}, base seed {m['base_seed']} (common random numbers), K {m['k_fit']} worlds per game for the search and {m['k_final']} for the final table, "
         f"{m['games']} training games, {m['bootstraps']} bootstrap replicates for the moment scales. Built {m['built']} ({m['seconds']}s). "
         f"Parameter artifact research/data/csim_params.json, SHA-256 {st['hash'][:16]}. 2025 is not read in any form. Method: {m['method']}.\n",
         f"Objective (mean squared standardised mismatch over {m['terms']} moments): start {m['objective_start']:.2f}, fitted {m['objective']:.2f} after {m['evaluations']} evaluations; "
         f"at K=120 with the frozen values {m['objective_final_k120']:.2f}.\n",
         "## Fitted parameters\n",
         "| Parameter | Start | Fitted | Profile range (objective within one z-squared unit) | Second fit, other random numbers | Objective without it |\n|---|---|---|---|---|---|"]
    for k in ORDER:
        L.append(f"| {k} | {m['start'][k]:.3f} | {st['params'][k]:.4f} | [{st['profile'][k]['lo']:.3f}, {st['profile'][k]['hi']:.3f}] | {st['alt_fit']['params'][k]:.4f} ({st['alt_fit']['diff'][k]:+.4f}) | {st['smallest_model'][k]['objective_without']:.2f} (+{st['smallest_model'][k]['rise']:.2f}) |")
    L.append(f"\nThe fit's own Monte Carlo: at K={m['k_fit']} with the fitting seed the frozen parameters read {m['objective']:.2f}, at K={m['k_final']} they read {m['objective_final_k120']:.2f}. "
             f"The difference is the fit reading the noise of its own common random numbers, and {m['objective_final_k120']:.2f} is the honest figure. "
             f"Where a profile range below reads as a single point, the search step (half a simplex step) already moved the objective by more than one z-squared unit: "
             f"the interval is narrower than the step, not zero.\n")
    L.append(f"Second fit (seed {m['alt_seed']}) objective {st['alt_fit']['objective']:.2f}; largest parameter difference {st['alt_fit']['max_abs_diff']:.4f}. "
             + (f"Smallest-model pass pinned {st['refit']['pinned']} (refit objective {st['refit']['objective']:.2f}). " if st.get("refit") else "Smallest-model pass pinned nothing: every parameter buys at least one unit of z-squared. ")
             + "\n")
    L.append("## Every moment at the frozen values (training seasons)\n")
    L.append("| Moment | Simulated | Observed | Bootstrap scale | z |\n|---|---|---|---|---|")
    for t in st["final_terms"]:
        L.append(f"| {t['moment']} | {t['sim']:.3f} | {t['obs']:.3f} | {t['scale']:.3f} | {t['z']:+.1f} |")
    L.append("\nThe parameters are now frozen and hashed; part two reads 2025 for the first time and judges both simulators on it without touching them.\n")
    with open(os.path.join(REPORTS, "stage2e_fit_report.md"), "w") as fh:
        fh.write("\n".join(L))


if __name__ == "__main__":
    st = run()
    print("stage 2E fit written; objective %.3f; hash %s" % (st["meta"]["objective"], st["hash"][:16]))
