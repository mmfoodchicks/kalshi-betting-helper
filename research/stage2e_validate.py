"""Stage 2E, part two: the blind 2025 validation. The first and only
place 2025 outcomes are read. Nothing here changes a parameter: the
constrained simulator runs with the frozen artifact (its hash is
recorded), the legacy simulator runs as production runs it, and both
are judged on the same 2025 games by the same rules, which are written
down here before any number is seen:

  individual calibration  PIT of every roled player-week in each model's
                          simulated distribution (uniform if calibrated),
                          the 10/90 and 5/95 coverage, and the CRPS (a
                          proper score; lower is better) by role;
  pairs                   the 2025 observed role-pair correlations against
                          each model's, with the observed bootstrap sd;
  stacks                  PIT and tail rates of the stack totals;
  tails                   P(over 1.5x, over 2x, under 0.5x) by role, observed
                          against each model;
  seed robustness         the constrained model under two seeds.

The promotion rule, fixed in advance: the constrained model may go to the
live board as the ALTERNATE (Stage 2F) only if, on 2025, (a) its mean
CRPS over the seven roles is no worse than the legacy model's by more
than 1%, (b) its pair-correlation RMSE against the observed 2025 pairs
is lower than the legacy model's, and (c) its PIT is no less uniform
(Kolmogorov distance) than the legacy's on the pooled roles. It becomes
the DEFAULT for nothing here. If it fails (a) it is materially worse and
stays experimental with the reasons listed.

    python3 -m research.stage2e_validate
"""
import json
import os
import sys
import time

import numpy as np

from research import hist_data as H
from research import csim_fit as F

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DATA = os.path.join(ROOT, "research", "data")
REPORTS = os.path.join(ROOT, "research", "reports")
BASE_SEED = 20250901
ALT_SEED = 20250902
K = 400
ROLES = F.ROLES
STACK_NAMES = ("QB+WR1", "QB+WR1+WR2", "QB+WR1+TE1", "game 5: QB+WR1+WR2+oppWR1+oppWR2", "game 6: +RB1")


def _commit():
    """Kept for the artifacts that only record a hash; `_prov()` is what new
    metadata blocks should carry. The commit alone was never enough: it names
    the tree the research code was sitting on, not the code that ran."""
    from research import provenance
    return provenance.commit()


def _prov(**extra):
    """commit, dirty flag, and a hash over the source that actually ran."""
    from research import provenance
    return provenance.stamp(**extra)


def legacy_sample(games, K):
    """The legacy simulator (nfl_dfs_sim.simulate_game, unseeded, as in
    production) on every game: the sample shape of csim_fit (points only;
    the legacy model keeps no per-world touchdown or target components)."""
    import nfl_dfs_sim
    sample = []
    for key, g in games.items():
        pl = [p for p in g["players"] if not p.get("actual_only")]
        sim = nfl_dfs_sim.simulate_game({"label": g["label"], "teams": g["teams"], "players": pl}, n=K, with_samples=True)
        P = len(g["players"])
        pts = np.full((P, K), np.nan, dtype=np.float32)
        by_name = {p["name"]: p for p in sim["players"]}
        for i, p in enumerate(g["players"]):
            if p.get("actual_only"):
                pts[i] = 0.0
                continue
            pts[i] = np.asarray(by_name[p["name"]]["arr"], dtype=np.float32)
        comps = {c: np.full((P, K), np.nan, dtype=np.float32) for c in F.COMPS}
        sample.append({"key": key, "players": g["players"], "teams": g["teams"], "pts": pts, "comps": comps, "K": K})
    return sample


def pit_and_crps(sample, obs):
    """Per role: the PIT values, the coverage and the CRPS of every
    roled player-week that played, plus the stack PITs."""
    pit = {r: [] for r in ROLES}
    crps = {r: [] for r in ROLES}
    cover = {r: {"80": [], "90": []} for r in ROLES}
    spit = {n: [] for n in STACK_NAMES}
    for gm, ob in zip(sample, obs):
        K = gm["K"]
        by_team = {}
        for i, p in enumerate(gm["players"]):
            by_team.setdefault(p["team"], []).append(i)
        troles = {}
        for t, idx in by_team.items():
            roles = {}
            for i in idx:
                r = gm["players"][i].get("role") or ""
                if r and r not in roles:
                    roles[r] = i
            troles[t] = roles
            for r, i in roles.items():
                if r not in ROLES:
                    continue
                y = float(ob["pts"][i, 0])
                if np.isnan(y):
                    continue
                x = np.sort(gm["pts"][i].astype(np.float64))
                u = (np.searchsorted(x, y, side="left") + 0.5 * (np.searchsorted(x, y, side="right") - np.searchsorted(x, y, side="left"))) / K
                pit[r].append(u)
                lo10, hi90 = x[int(0.1 * K)], x[min(K - 1, int(0.9 * K))]
                lo5, hi95 = x[int(0.05 * K)], x[min(K - 1, int(0.95 * K))]
                cover[r]["80"].append(float(lo10 <= y <= hi90)); cover[r]["90"].append(float(lo5 <= y <= hi95))
                # CRPS from the sample: E|X - y| - 0.5 E|X - X'|
                e1 = float(np.abs(x - y).mean())
                # E|X - X'| for a sorted sample: (2/K^2) sum_i (2i - K - 1) x_i  (i one-based)
                i1 = np.arange(1, K + 1)
                e2 = float((2.0 / (K * K)) * np.sum((2 * i1 - K - 1) * x))
                crps[r].append(e1 - 0.5 * e2)
        ts = list(by_team)
        if len(ts) == 2:
            A, B = ts
            for name, mine, theirs in F.STACKS:
                if name not in STACK_NAMES:
                    continue
                for me, opp in ((troles[A], troles[B]), (troles[B], troles[A])):
                    if all(r in me for r in mine) and all(r in opp for r in theirs):
                        ii = [me[r] for r in mine] + [opp[r] for r in theirs]
                        y = float(sum(ob["pts"][i, 0] for i in ii))
                        if np.isnan(y):
                            continue
                        x = np.sort(sum(gm["pts"][i].astype(np.float64) for i in ii))
                        spit[name].append((np.searchsorted(x, y, side="left") + 0.5 * (np.searchsorted(x, y, side="right") - np.searchsorted(x, y, side="left"))) / K)
    out = {}
    for r in ROLES:
        u = np.asarray(pit[r])
        if len(u) < 30:
            continue
        hist = np.histogram(u, bins=10, range=(0, 1))[0] / len(u)
        ks = float(np.max(np.abs(np.sort(u) - (np.arange(1, len(u) + 1) - 0.5) / len(u))))
        out[r] = {"n": int(len(u)), "pit_hist": [round(float(h), 3) for h in hist], "pit_ks": ks, "pit_mean": float(u.mean()),
                  "cover_80": float(np.mean(cover[r]["80"])), "cover_90": float(np.mean(cover[r]["90"])), "crps": float(np.mean(crps[r]))}
    allu = np.concatenate([np.asarray(pit[r]) for r in ROLES if len(pit[r])])
    out["_pooled"] = {"n": int(len(allu)), "pit_ks": float(np.max(np.abs(np.sort(allu) - (np.arange(1, len(allu) + 1) - 0.5) / len(allu)))),
                      "pit_hist": [round(float(h), 3) for h in np.histogram(allu, bins=10, range=(0, 1))[0] / len(allu)],
                      "crps_mean_over_roles": float(np.mean([out[r]["crps"] for r in ROLES if r in out]))}
    out["_stacks"] = {}
    for name, us in spit.items():
        u = np.asarray(us)
        if len(u) >= 30:
            out["_stacks"][name] = {"n": int(len(u)), "pit_ks": float(np.max(np.abs(np.sort(u) - (np.arange(1, len(u) + 1) - 0.5) / len(u)))),
                                    "pit_hist": [round(float(h), 3) for h in np.histogram(u, bins=10, range=(0, 1))[0] / len(u)],
                                    "share_above_p90": float((u > 0.9).mean()), "share_below_p10": float((u < 0.1).mean())}
    return out


def pair_table(obs_m, model_ms, scales):
    keys = [k for k in obs_m if k.startswith("pair ") and obs_m[k] is not None]
    rows = []
    for k in keys:
        row = {"moment": k, "obs": obs_m[k], "scale": scales.get(k)}
        for name, mm in model_ms.items():
            row[name] = mm.get(k)
        rows.append(row)
    rmse = {}
    for name, mm in model_ms.items():
        d = [(mm[k] - obs_m[k]) for k in keys if mm.get(k) is not None]
        rmse[name] = float(np.sqrt(np.mean(np.square(d)))) if d else None
    return rows, rmse


def run(log=print, allow_dirty=False):
    # Reading the holdout is the one-way door. It may only be opened against
    # a committed tree, so the parameters it judges are provably older than
    # the reading.
    if not allow_dirty:
        from research import provenance
        provenance.require_clean("a holdout validation")
    t0 = time.time()
    import nfl_dfs_csim as C
    if not C.PARAMS["fitted"]:
        raise RuntimeError("the parameter artifact is not fitted; run research.stage2e first")
    rows = H.read_csv_gz(os.path.join(DATA, "player_weeks_2022_2025.csv.gz"))
    games = F.games_from_table(rows, (H.HOLDOUT,))
    log(f"[2E-validate] {len(games)} games of {H.HOLDOUT}; reading their outcomes for the first time")
    obs = F.observed_sample(games)
    obs_m = F.moments(obs)
    from research import stage2e as E
    scales = E.bootstrap_scales(games, np.random.default_rng(BASE_SEED), boots=200)
    log("[2E-validate] constrained (frozen parameters), two seeds...")
    con = F.simulate_sample(games, C.PARAMS, K, BASE_SEED)
    con2 = F.simulate_sample(games, C.PARAMS, K, ALT_SEED)
    log("[2E-validate] legacy (production, unseeded)...")
    leg = legacy_sample(games, K)
    ms = {"constrained": F.moments(con), "constrained_seed2": F.moments(con2), "legacy": F.moments(leg)}
    cal = {"constrained": pit_and_crps(con, obs), "constrained_seed2": pit_and_crps(con2, obs), "legacy": pit_and_crps(leg, obs)}
    pairs, rmse = pair_table(obs_m, ms, scales)
    # tails and marginals by role
    marg = {}
    for r in ROLES:
        marg[r] = {"obs": {k: obs_m.get(f"marg {r} {k}") for k in ("ratio_sd", "p_over_1_5", "p_over_2", "p_under_half")}}
        for name, mm in ms.items():
            marg[r][name] = {k: mm.get(f"marg {r} {k}") for k in ("ratio_sd", "p_over_1_5", "p_over_2", "p_under_half")}
    stacks = {}
    for name in STACK_NAMES:
        stacks[name] = {"obs": {k: obs_m.get(f"stack {name} {k}") for k in ("ratio_var", "p_over_1_5", "p_over_2", "p_under_0_5")}}
        for mname, mm in ms.items():
            stacks[name][mname] = {k: mm.get(f"stack {name} {k}") for k in ("ratio_var", "p_over_1_5", "p_over_2", "p_under_0_5")}
    # the gate
    c, l = cal["constrained"]["_pooled"], cal["legacy"]["_pooled"]
    gate = {"crps_constrained": c["crps_mean_over_roles"], "crps_legacy": l["crps_mean_over_roles"],
            "crps_ok": c["crps_mean_over_roles"] <= l["crps_mean_over_roles"] * 1.01,
            "pair_rmse_constrained": rmse["constrained"], "pair_rmse_legacy": rmse["legacy"], "pairs_ok": rmse["constrained"] < rmse["legacy"],
            "pit_ks_constrained": c["pit_ks"], "pit_ks_legacy": l["pit_ks"], "pit_ok": c["pit_ks"] <= l["pit_ks"] + 1e-9}
    gate["promote_to_alternate"] = bool(gate["crps_ok"] and gate["pairs_ok"] and gate["pit_ok"])
    gate["materially_worse"] = bool(not gate["crps_ok"])
    seed_rob = {"crps_diff": abs(cal["constrained"]["_pooled"]["crps_mean_over_roles"] - cal["constrained_seed2"]["_pooled"]["crps_mean_over_roles"]),
                "pair_rmse_diff": abs(rmse["constrained"] - rmse["constrained_seed2"]),
                "pit_ks_diff": abs(cal["constrained"]["_pooled"]["pit_ks"] - cal["constrained_seed2"]["_pooled"]["pit_ks"])}
    st = {"meta": {"stage": "2E-validate", "commit": _commit(), "holdout_season": H.HOLDOUT, "holdout_outcomes_read": True, "games": len(games),
                   "k": K, "base_seed": BASE_SEED, "alt_seed": ALT_SEED, "params_hash": C.PARAMS["hash"], "params_fit_commit": C.PARAMS["meta"].get("commit"),
                   "params": C.PARAMS["params"], "legacy": "nfl_dfs_sim.simulate_game, unseeded, points rescaled to the Sleeper mean (production)",
                   "built": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())},
          "gate": gate, "calibration": cal, "pairs": pairs, "pair_rmse": rmse, "marginals": marg, "stacks": stacks, "seed_robustness": seed_rob,
          "observed_moments": {k: v for k, v in obs_m.items() if not k.startswith("_")}}
    st["meta"]["seconds"] = round(time.time() - t0, 1)
    json.dump(st, open(os.path.join(DATA, "stage2e_validation.json"), "w"), indent=1, sort_keys=True)
    write_report(st)
    return st


def _f(x, d=3):
    return "-" if x is None else f"{x:.{d}f}"


def write_report(st):
    m, g = st["meta"], st["gate"]
    L = [f"# Stage 2E, part two: the blind {m['holdout_season']} validation, legacy against constrained\n",
         f"Commit {m['commit']}, {m['games']} games of {m['holdout_season']}, {m['k']} worlds per game per model, constrained seeds {m['base_seed']} and {m['alt_seed']}, "
         f"frozen parameters {m['params_hash'][:16]} (fitted at commit {m['params_fit_commit']}). Built {m['built']} ({m['seconds']}s). "
         f"This is the first read of any {m['holdout_season']} outcome. Legacy: {m['legacy']}. Nothing was tuned here.\n",
         "## The gate (written before the numbers)\n",
         f"- CRPS, mean over the seven roles (lower is better): constrained {g['crps_constrained']:.3f}, legacy {g['crps_legacy']:.3f} -> {'pass' if g['crps_ok'] else 'FAIL'} (no worse than 1%)",
         f"- Pair-correlation RMSE against observed {m['holdout_season']}: constrained {g['pair_rmse_constrained']:.4f}, legacy {g['pair_rmse_legacy']:.4f} -> {'pass' if g['pairs_ok'] else 'FAIL'} (lower)",
         f"- PIT uniformity, pooled roles (Kolmogorov distance, lower is better): constrained {g['pit_ks_constrained']:.4f}, legacy {g['pit_ks_legacy']:.4f} -> {'pass' if g['pit_ok'] else 'FAIL'} (no worse)",
         f"\n**Verdict: {'the constrained model is promoted to the live board as the alternate (not the default)' if g['promote_to_alternate'] else ('the constrained model is MATERIALLY WORSE on the proper score and stays experimental' if g['materially_worse'] else 'the constrained model is not promoted; it stays experimental')}.**\n",
         "## Individual calibration by role\n",
         "| Role | n | Model | PIT histogram (10 bins) | PIT KS | 80% coverage | 90% coverage | CRPS |\n|---|---|---|---|---|---|---|---|"]
    for r in ROLES:
        for name in ("legacy", "constrained"):
            c = st["calibration"][name].get(r)
            if c:
                L.append(f"| {r} | {c['n']} | {name} | {' '.join(f'{h:.2f}' for h in c['pit_hist'])} | {c['pit_ks']:.3f} | {c['cover_80']:.3f} | {c['cover_90']:.3f} | {c['crps']:.3f} |")
    for name in ("legacy", "constrained"):
        p = st["calibration"][name]["_pooled"]
        L.append(f"\nPooled {name}: n {p['n']}, PIT histogram {' '.join(f'{h:.2f}' for h in p['pit_hist'])}, KS {p['pit_ks']:.4f}, mean CRPS {p['crps_mean_over_roles']:.3f}.")
    L.append("\n## Role-pair correlations on 2025\n")
    L.append("| Pair | Observed 2025 | Bootstrap sd | Legacy | Constrained | Constrained, seed 2 |\n|---|---|---|---|---|---|")
    for row in st["pairs"]:
        L.append(f"| {row['moment'][5:]} | {_f(row['obs'])} | {_f(row['scale'])} | {_f(row['legacy'])} | {_f(row['constrained'])} | {_f(row['constrained_seed2'])} |")
    L.append(f"\nRMSE against the observed pairs: legacy {st['pair_rmse']['legacy']:.4f}, constrained {st['pair_rmse']['constrained']:.4f} (seed 2: {st['pair_rmse']['constrained_seed2']:.4f}).\n")
    L.append("## Marginal tails by role (projection ratio; floored on a 3-point projection)\n")
    L.append("| Role | Statistic | Observed | Legacy | Constrained |\n|---|---|---|---|---|")
    for r in ROLES:
        for k in ("ratio_sd", "p_over_1_5", "p_over_2", "p_under_half"):
            L.append(f"| {r} | {k} | {_f(st['marginals'][r]['obs'][k])} | {_f(st['marginals'][r]['legacy'][k])} | {_f(st['marginals'][r]['constrained'][k])} |")
    L.append("\n## Stacks\n")
    L.append("| Structure | Statistic | Observed | Legacy | Constrained |\n|---|---|---|---|---|")
    for name in STACK_NAMES:
        for k in ("ratio_var", "p_over_1_5", "p_over_2", "p_under_0_5"):
            L.append(f"| {name} | {k} | {_f(st['stacks'][name]['obs'][k])} | {_f(st['stacks'][name]['legacy'][k])} | {_f(st['stacks'][name]['constrained'][k])} |")
    L.append("\nStack PIT (the actual stack total's percentile in each model's distribution; share above the 90th and below the 10th should be 0.10 each):\n")
    L.append("| Structure | Model | n | PIT KS | Share above p90 | Share below p10 |\n|---|---|---|---|---|---|")
    for name in STACK_NAMES:
        for mname in ("legacy", "constrained"):
            s = st["calibration"][mname]["_stacks"].get(name)
            if s:
                L.append(f"| {name} | {mname} | {s['n']} | {s['pit_ks']:.3f} | {s['share_above_p90']:.3f} | {s['share_below_p10']:.3f} |")
    sr = st["seed_robustness"]
    L.append(f"\n## Seed robustness (constrained, two seeds)\n\nCRPS differs by {sr['crps_diff']:.4f}, pair RMSE by {sr['pair_rmse_diff']:.4f}, PIT KS by {sr['pit_ks_diff']:.4f}.\n")
    with open(os.path.join(REPORTS, "stage2e_validation_report.md"), "w") as fh:
        fh.write("\n".join(L))


if __name__ == "__main__":
    st = run()
    print("stage 2E validation written; gate:", json.dumps(st["gate"]))
