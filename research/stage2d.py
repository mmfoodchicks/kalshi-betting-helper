"""Stage 2D: the constrained simulator exists as an alternate mode; this
proves what can be proved before any parameter is fitted.

  1. the moment harness reproduces the Stage 2B statistics from the same
     table (the observed side of every fit is the artifact GPT reviewed);
  2. the per-world identities hold on every training game at 200 worlds:
     completions, yards and touchdowns of the quarterbacks equal their
     receivers' in every world, receptions within targets, touchdowns
     within receptions, nothing negative, points recomputable;
  3. seeded reproducibility (the same seed, the same worlds; a different
     seed, different worlds) and speed;
  4. the provisional (unfitted) parameters against the observed moments,
     so the Stage 2E fit starts from a stated distance, with the terms
     that miss most listed.

No 2025 outcome is read. Writes research/data/stage2d_stats.json and
research/reports/stage2d_report.md.

    python3 -m research.stage2d
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
BASE_SEED = 20220903
K_INVARIANTS = 200
K_MOMENTS = 60


def _commit():
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return "unknown"


def harness_check(obs_m):
    """The harness against the Stage 2B artifact: every pair correlation,
    marginal, component ratio sd and stack variance within 0.003 (they
    are the same numbers computed two ways); target competition and
    touchdowns within the tolerance the two definitions allow."""
    st = json.load(open(os.path.join(DATA, "stage2b_stats.json")))
    out = {}
    worst = 0.0
    for k, v in st["relationships"].items():
        d = abs(obs_m["pair " + k] - v["pooled"]); worst = max(worst, d)
    out["pairs_max_abs_diff"] = worst
    # the harness floors every ratio on a real projection (3 DraftKings
    # points, csim_fit.RATIO_PROJ_FLOOR); 2B did not, so the roles whose
    # depth rows sit under the floor differ by construction and are
    # reported side by side rather than compared
    worst = 0.0
    for role in ("QB1", "WR1"):
        m = st["marginals"][role]
        worst = max(worst, abs(obs_m[f"marg {role} ratio_sd"] - m["ratio_sd"]), abs(obs_m[f"marg {role} p_over_1_5"] - m["p_over_1_5"]),
                    abs(obs_m[f"marg {role} p_under_half"] - m["p_under_half"]))
    out["marginals_max_abs_diff"] = worst
    out["marginals_floored_vs_2b"] = {role: (obs_m[f"marg {role} ratio_sd"], st["marginals"][role]["ratio_sd"]) for role in ("RB1", "RB2", "WR2", "WR3", "TE1")}
    c = st["components"]
    out["components_max_abs_diff"] = max(abs(obs_m["comp QB1 pass_yd ratio_sd"] - c["QB1"]["pass_yd"]["ratio_sd"]),
                                         abs(obs_m["comp WR1 rec_yd ratio_sd"] - c["WR1"]["rec_yd"]["ratio_sd"]),
                                         abs(obs_m["comp RB1 rush_yd ratio_sd"] - c["RB1"]["rush_yd"]["ratio_sd"]))
    sk = st["stacks"]
    out["stacks_max_abs_diff"] = max(abs(obs_m[f"stack {n} ratio_var"] - sk[n]["ratio_var"]) for n in ("QB+WR1", "QB+WR1+WR2", "game 5: QB+WR1+WR2+oppWR1+oppWR2", "game 6: +RB1"))
    tc = st["target_competition"]
    out["target_team_ratio_sd"] = (obs_m["tgt team_ratio_sd"], tc["team_target_ratio_sd"])
    out["share_resid_sd_WR1"] = (obs_m["tgt share_resid_sd WR1"], tc["share_resid_sd"]["WR1"])
    out["share_corr_WR1_WR2"] = (obs_m["tgt share_corr WR1/WR2"], tc["share_resid_corr"]["WR1/WR2"])
    td = st["touchdowns"]
    out["pass_td_dist"] = ({k: round(obs_m[f"td pass {k}"], 3) for k in ("0", "1", "2", "3", "4", "5+")}, {k: round(v, 3) for k, v in td["pass_td_dist"].items()})
    out["two_scorers"] = (obs_m["td two_scorers_given_2plus"], td["p_two_or_more_receivers_score_given_2plus_pass_td"])
    out["ok"] = (out["pairs_max_abs_diff"] < 0.003 and out["marginals_max_abs_diff"] < 0.003 and out["components_max_abs_diff"] < 0.003
                 and out["stacks_max_abs_diff"] < 0.003
                 and abs(out["target_team_ratio_sd"][0] - out["target_team_ratio_sd"][1]) < 0.01
                 and max(abs(out["pass_td_dist"][0][k] - out["pass_td_dist"][1][k]) for k in out["pass_td_dist"][0]) < 0.01)
    return out


def invariants(games, K, base_seed):
    import nfl_dfs_csim as C
    from research import seeds as SEEDS
    counts = {"games": 0, "worlds": 0, "identity_failures": 0, "bound_failures": 0, "negative": 0, "points_mismatch": 0}
    worst = []
    t0 = time.time()
    for key, g in games.items():
        rng = np.random.default_rng(SEEDS.child_seed(base_seed, g["season"], g["week"], g["game_key"], "constrained"))
        sim = C.simulate_game(g, n=K, rng=rng)
        inv = C.check_invariants(sim, g["players"])
        counts["games"] += 1; counts["worlds"] += K
        for name, (ok, bad) in inv.items():
            if ok:
                continue
            if "=sum" in name:
                counts["identity_failures"] += bad
            elif "within" in name:
                counts["bound_failures"] += bad
            elif "negative" in name:
                counts["negative"] += bad
            worst.append((str(key), name, bad))
        # points recompute from the components (bonuses included)
        import dk_scoring
        Sd = dk_scoring.NFL_OFF
        for i, p in enumerate(sim["players"]):
            c = sim["components"][i]
            z = np.zeros(K)
            fp = (c.get("pass_yd", z) * Sd["pass_yd"] + c.get("pass_td", z) * Sd["pass_td"] + c.get("int", z) * Sd["int"] + c.get("rush_yd", z) * Sd["rush_yd"]
                  + c.get("rush_td", z) * Sd["rush_td"] + c.get("rec", z) * Sd["rec"] + c.get("rec_yd", z) * Sd["rec_yd"] + c.get("rec_td", z) * Sd["rec_td"]
                  + c.get("fum", z) * Sd["fumble_lost"] + Sd["pass_300"] * (c.get("pass_yd", z) >= 300) + Sd["rush_100"] * (c.get("rush_yd", z) >= 100) + Sd["rec_100"] * (c.get("rec_yd", z) >= 100))
            counts["points_mismatch"] += int((np.abs(fp - p["arr"]) > 1e-6).sum())
    counts["seconds"] = round(time.time() - t0, 1)
    counts["worst"] = worst[:20]
    counts["ok"] = counts["identity_failures"] == 0 and counts["bound_failures"] == 0 and counts["negative"] == 0 and counts["points_mismatch"] == 0
    return counts


def reproducibility(games):
    import nfl_dfs_csim as C
    g = next(iter(games.values()))
    a = C.simulate_game(g, n=300, rng=np.random.default_rng(11))
    b = C.simulate_game(g, n=300, rng=np.random.default_rng(11))
    c = C.simulate_game(g, n=300, rng=np.random.default_rng(12))
    same = all(np.array_equal(x["arr"], y["arr"]) for x, y in zip(a["players"], b["players"]))
    diff = any(not np.array_equal(x["arr"], y["arr"]) for x, y in zip(a["players"], c["players"]))
    t0 = time.time()
    C.simulate_game(g, n=60000, rng=np.random.default_rng(1))
    return {"same_seed_same_worlds": same, "different_seed_different_worlds": diff, "seconds_60000_worlds_one_game": round(time.time() - t0, 2)}


def run():
    t0 = time.time()
    rows = H.read_csv_gz(os.path.join(DATA, "player_weeks_2022_2025.csv.gz"))
    games = F.games_from_table(rows, S.TRAIN)
    obs_m = F.moments(F.observed_sample(games))
    hc = harness_check(obs_m)
    inv = invariants(games, K_INVARIANTS, BASE_SEED)
    rep = reproducibility(games)
    import nfl_dfs_csim as C
    sim_m = F.moments(F.simulate_sample(games, C.PARAMS, K_MOMENTS, BASE_SEED))
    val, terms = F.objective(sim_m, obs_m)
    terms.sort(key=lambda t: -abs((t[1] - t[2]) / t[3]))
    st = {"meta": {"stage": "2D", "commit": _commit(), "base_seed": BASE_SEED, "train_seasons": list(S.TRAIN), "holdout_season": H.HOLDOUT,
                   "holdout_outcomes_read": False, "games": len(games), "k_invariants": K_INVARIANTS, "k_moments": K_MOMENTS,
                   "simulator": {"model": C.SIM_MODEL, "version": C.SIM_MODEL_VERSION, "params_fitted": C.PARAMS["fitted"], "params_hash": C.PARAMS["hash"][:16],
                                 "params": C.PARAMS["params"]},
                   "built": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())},
          "harness_check": hc, "invariants": inv, "reproducibility": rep,
          "provisional_fit": {"objective": val, "terms": len(terms),
                              "worst": [{"moment": k, "sim": round(s, 4), "obs": round(o, 4), "scale": round(sc, 4), "z": round((s - o) / sc, 2)} for k, s, o, sc in terms[:25]]},
          "observed_moments": {k: v for k, v in obs_m.items() if not k.startswith("_")},
          "provisional_moments": {k: v for k, v in sim_m.items() if not k.startswith("_")}}
    st["meta"]["seconds"] = round(time.time() - t0, 1)
    json.dump(st, open(os.path.join(DATA, "stage2d_stats.json"), "w"), indent=1, sort_keys=True)
    write_report(st)
    return st


def write_report(st):
    m = st["meta"]; hc = st["harness_check"]; inv = st["invariants"]; rep = st["reproducibility"]; pf = st["provisional_fit"]
    L = ["# Stage 2D: the constrained team simulator as an alternate mode\n",
         f"Commit {m['commit']}, base seed {m['base_seed']}, {m['games']} training games (2022-2024), {m['k_invariants']} worlds per game for the invariants, "
         f"{m['k_moments']} for the moments. Built {m['built']}. Simulator {m['simulator']['model']} v{m['simulator']['version']}, parameters "
         f"{'fitted' if m['simulator']['params_fitted'] else 'PROVISIONAL (unfitted)'}, hash {m['simulator']['params_hash']}. No 2025 outcome is read.\n",
         "## The moment harness reproduces Stage 2B\n",
         f"Same table, two code paths: pair correlations agree to {hc['pairs_max_abs_diff']:.4f}, marginals (QB1, WR1) to {hc['marginals_max_abs_diff']:.4f}, "
         f"component ratio sds to {hc['components_max_abs_diff']:.4f}, stack variances to {hc['stacks_max_abs_diff']:.4f}. "
         f"Target competition: team target ratio sd {hc['target_team_ratio_sd'][0]:.3f} vs {hc['target_team_ratio_sd'][1]:.3f}, WR1 share residual sd "
         f"{hc['share_resid_sd_WR1'][0]:.3f} vs {hc['share_resid_sd_WR1'][1]:.3f}, WR1/WR2 share correlation {hc['share_corr_WR1_WR2'][0]:.3f} vs {hc['share_corr_WR1_WR2'][1]:.3f} "
         f"(the harness drops the catchers who did not play from both totals; 2B drops them from the actual total only). "
         f"Team passing touchdowns {json.dumps(hc['pass_td_dist'][0])} vs {json.dumps(hc['pass_td_dist'][1])}; two scorers given two or more "
         f"{hc['two_scorers'][0]:.3f} vs {hc['two_scorers'][1]:.3f}. Check {'passes' if hc['ok'] else 'FAILS'}. "
         f"The harness floors every ratio on a projection of at least 3 points (Sleeper once gave a starting quarterback 26.5 attempts and 0.3 points; at 200 worlds one such row would own a role's ratio sd), applied to both sides alike; "
         f"2B did not floor, so the depth roles differ by construction, harness vs 2B: " + ", ".join(f"{r} {a:.3f} vs {b:.3f}" for r, (a, b) in hc["marginals_floored_vs_2b"].items()) + ".\n",
         "## Per-world invariants on every training game\n",
         f"{inv['games']} games x {inv['worlds'] // max(1, inv['games'])} worlds = {inv['worlds']:,} team-game-worlds in {inv['seconds']}s: "
         f"identity failures {inv['identity_failures']}, bound failures {inv['bound_failures']}, negative values {inv['negative']}, "
         f"points that do not recompute from their components {inv['points_mismatch']}. {'All hold.' if inv['ok'] else 'FAILURES: ' + json.dumps(inv['worst'])}\n",
         "## Reproducibility and speed\n",
         f"Same seed, same worlds: {rep['same_seed_same_worlds']}. Different seed, different worlds: {rep['different_seed_different_worlds']}. "
         f"One game at 60,000 worlds: {rep['seconds_60000_worlds_one_game']}s (a sixteen-game slate in well under a minute on the PC).\n",
         "## The provisional parameters against the observed moments (the fit's starting point)\n",
         f"Objective (mean squared standardised mismatch over {pf['terms']} moments): {pf['objective']:.2f}. The worst terms:\n",
         "| Moment | Provisional simulator | Observed 2022-2024 | Scale | z |\n|---|---|---|---|---|"]
    for w in pf["worst"]:
        L.append(f"| {w['moment']} | {w['sim']:.3f} | {w['obs']:.3f} | {w['scale']:.3f} | {w['z']:+.1f} |")
    L.append("\nThese are the distances Stage 2E must close on the training seasons; the provisional values are a starting point and nothing is claimed for them. "
             "Not in production: the classic build accepts model=\"constrained\" and stamps it, the PC does not request it, and the tab serves legacy boards.\n")
    with open(os.path.join(REPORTS, "stage2d_report.md"), "w") as fh:
        fh.write("\n".join(L))


if __name__ == "__main__":
    st = run()
    print("stage 2D written; harness", st["harness_check"]["ok"], "invariants", st["invariants"]["ok"], "objective %.2f" % st["provisional_fit"]["objective"])
