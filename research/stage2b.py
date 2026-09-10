"""Stage 2B: the historical evidence base. Builds (or reads) the player-week
table, computes every statistic on the TRAINING seasons only (2022-2024),
counts 2025's coverage without reading its outcomes, and writes the report
and the machine-readable artifact the later stages fit against.

    python3 -m research.stage2b [raw_dir]

With a raw directory the table is rebuilt; without one the committed table
is read. The artifact stamps the code commit, the seed and the seasons."""
import json
import os
import subprocess
import sys
import time

import numpy as np

from research import hist_data as H
from research import hist_stats as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "research", "data")
REPORTS = os.path.join(ROOT, "research", "reports")
TABLE = os.path.join(DATA, "player_weeks_2022_2025.csv.gz")
BASE_SEED = 20220901


def _commit():
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return "unknown"


def _f(x, d=3):
    return "-" if x is None else f"{x:.{d}f}"


def run(raw_dir=None, boots=400):
    t0 = time.time()
    if raw_dir:
        rows, cov = H.build(raw_dir)
        H.to_csv_gz(rows, TABLE)
        json.dump(cov, open(os.path.join(DATA, "coverage.json"), "w"), indent=1, sort_keys=True)
    rows = H.read_csv_gz(TABLE)
    cov = json.load(open(os.path.join(DATA, "coverage.json")))
    rng = np.random.default_rng(BASE_SEED)
    stats = {
        "meta": {"stage": "2B", "commit": _commit(), "base_seed": BASE_SEED, "train_seasons": list(S.TRAIN),
                 "holdout_season": H.HOLDOUT, "holdout_outcomes_read": False, "bootstraps": boots,
                 "method": {"residual": "actual minus projected DraftKings points (components, no bonuses on the projection side), "
                                        "standardised by the role's residual sd on the training seasons",
                            "roles": "pregame only: QB1 by projected pass attempts; RB1-3 by projected carries plus targets; "
                                     "WR1-4 and TE1-2 by projected targets, receiving yards as the tie-break",
                            "bootstrap": "clusters are team-seasons (the repeated weeks of one offense resample together); "
                                         "for opponent pairs the cluster is the first-named side's team-season; 90% intervals",
                            "demeaned": "within-pair demeaned correlation on pairs seen six or more times"},
                 "built": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())},
        "coverage": cov,
        "relationships": S.all_relationships(rows, S.TRAIN, rng, boots=boots),
        "marginals": S.marginals(rows, S.TRAIN),
        "components": S.components(rows, S.TRAIN),
        "target_competition": S.target_competition(rows, S.TRAIN),
        "touchdowns": S.touchdowns(rows, S.TRAIN),
        "sleeper_audit": S.sleeper_audit(rows, S.TRAIN),
        "stacks": S.stacks(rows, S.TRAIN),
        "rb_buckets": S.rb_buckets(rows, S.TRAIN),
    }
    stats["meta"]["seconds"] = round(time.time() - t0, 1)
    json.dump(stats, open(os.path.join(DATA, "stage2b_stats.json"), "w"), indent=1, sort_keys=True)
    write_report(stats)
    return stats


def write_report(st):
    L = []
    m = st["meta"]
    L.append("# Stage 2B: historical evidence, 2022 to 2024 fitting seasons, 2025 held out\n")
    L.append(f"Commit {m['commit']}, base seed {m['base_seed']}, {m['bootstraps']} clustered bootstraps. Built {m['built']}. "
             f"2025 is loaded and coverage-counted only; no 2025 outcome enters any number below except the coverage table. "
             f"Residual method: {m['method']['residual']}. Roles: {m['method']['roles']}. Bootstrap: {m['method']['bootstrap']}.\n")
    L.append("## Coverage\n")
    L.append("| Season | Weeks proj / act | Games | Real projected player-weeks | Matched with a box score | Projected, did not play | Played, no projection | Kept | Excluded (reasons) | Feed placeholders |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for s, c in sorted(st["coverage"].items()):
        L.append(f"| {s}{' (holdout)' if c.get('holdout') else ''} | {c['weeks_with_projections']} / {c['weeks_with_actuals']} | {c['games']} | {c['projected_player_weeks']:,} | {c['matched']:,} | {c['projection_without_actual']} | {c['actual_without_projection']:,} | {c['kept']:,} | {c['excluded']} ({', '.join(f'{k} {v}' for k, v in c['exclusion_reasons'].items())}) | {c.get('empty_rows', 0):,} |")
    L.append("\nEvery row keeps its flags; a hard flag (position out of scope, no team, no opponent, team conflict between the feeds on a real projection, inconsistent opponent pair, receptions above targets, a negative counting stat) excludes the row with the reason counted. Soft flags (no real projection, did not play) keep the row and only bar it from residual statistics. Stable Sleeper player ids join the feeds; no name joins. Feed placeholder rows (no projection and no game) are counted, not stored.\n")
    L.append("## Role-pair co-movement (standardised projection residuals)\n")
    L.append("| Pair | n | Distinct pairs | Team-seasons | Pooled r | Within-pair demeaned r (pairs 6+) | 90% clustered bootstrap | 2022 / 2023 / 2024 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for k, v in st["relationships"].items():
        if not v.get("n"):
            continue
        ps = v["per_season"]
        L.append(f"| {k} | {v['n']:,} | {v['pairs']} | {v['team_seasons']} | {_f(v['pooled'])} | {_f(v['demeaned'])} ({v['pairs_6plus']}) | [{_f(v['boot_lo'])}, {_f(v['boot_hi'])}] | {' / '.join(_f(ps.get(str(s)), 2) for s in (2022, 2023, 2024))} |")
    L.append("\n## Marginal distribution around the projection, by role\n")
    L.append("| Role | n | Proj mean | Actual mean | Bias pts | Resid sd | CV | Skew | Ratio p10 / p50 / p90 / p95 / p99 | P under 0.5x | P over 1.5x | P over 2x |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for k, v in st["marginals"].items():
        q = v["ratio_q"]
        L.append(f"| {k} | {v['n']:,} | {v['proj_mean']:.1f} | {v['act_mean']:.1f} | {v['bias_pts']:+.2f} | {v['resid_sd']:.2f} | {v['cv_actual']:.2f} | {_f(v['resid_skew'], 2)} | {q['p10']:.2f} / {q['p50']:.2f} / {q['p90']:.2f} / {q['p95']:.2f} / {q['p99']:.2f} | {v['p_under_half']:.3f} | {v['p_over_1_5']:.3f} | {v['p_over_2']:.3f} |")
    L.append("\n## Components\n")
    c = st["components"]
    qb = c["QB1"]
    L.append(f"QB1 passing yards: ratio sd {_f(qb['pass_yd']['ratio_sd'])}, residual sd {_f(qb['pass_yd']['resid_sd'], 1)} yards, p10/p50/p90/p99 {' / '.join(_f(x, 2) for x in qb['pass_yd']['ratio_q'].values())}. "
             f"Passing TDs: projected mean {qb['pass_td_proj_mean']:.2f}, distribution {json.dumps({k: round(v, 3) for k, v in qb['pass_td_dist'].items()})}. "
             f"Interceptions: projected mean {qb['int_proj_mean']:.2f}, distribution {json.dumps({k: round(v, 3) for k, v in qb['int_dist'].items()})}. "
             f"QB rushing yards ratio sd {_f((qb.get('rush_yd') or {}).get('ratio_sd'))}.\n")
    for role in ("RB1", "RB2", "WR1", "WR2", "WR3", "TE1"):
        cc = c[role]
        parts = []
        for comp in ("rush_att", "rec_tgt", "rec", "rush_yd", "rec_yd"):
            v = cc.get(comp)
            if v:
                parts.append(f"{comp}: proj {v['proj_mean']:.1f}, ratio sd {v['ratio_sd']:.2f}, p10/p90 {v['ratio_q']['p10']:.2f}/{v['ratio_q']['p90']:.2f}")
        tds = {k: v for k, v in cc.items() if k.endswith("_dist")}
        L.append(f"{role}: " + "; ".join(parts) + "; " + "; ".join(f"{k} {json.dumps({kk: round(vv, 3) for kk, vv in v.items()})}" for k, v in tds.items()) + "\n")
    L.append("## Target competition\n")
    tc = st["target_competition"]
    L.append(f"{tc['team_weeks']:,} team-weeks with WR1, WR2, WR3, TE1 and RB1 all present. Team targets actual over projected: mean {_f(tc['team_target_ratio_mean'])}, sd {_f(tc['team_target_ratio_sd'])}. Mean actual share by role: {json.dumps({k: round(v, 3) for k, v in tc['share_mean_actual'].items()})}. Share residual sd (actual share minus projected share): {json.dumps({k: round(v, 3) for k, v in tc['share_resid_sd'].items()})}. Week-to-week share sd of one player, by role: {json.dumps({k: (round(v, 3) if v is not None else None) for k, v in tc['week_to_week_share_sd_by_role'].items()})}.\n")
    L.append("Correlation of share residuals across roles (the competition targets for the team-budget model):\n")
    L.append("| Pair | r |\n|---|---|")
    for k, v in tc["share_resid_corr"].items():
        L.append(f"| {k} | {v:+.3f} |")
    L.append("\nWhen WR1 beats his projected targets by three or more (and when he misses by three or more), the others' target residual, and the slope of each on WR1's residual:\n")
    L.append("| Role | Mean residual when WR1 hot | When WR1 cold | Slope on WR1 |\n|---|---|---|---|")
    for k, v in tc["when_wr1_beats_targets_by_3"].items():
        L.append(f"| {k} | {_f(v['hot_mean_resid'], 2)} | {_f(v['cold_mean_resid'], 2)} | {_f(v['slope_on_wr1'], 3)} |")
    L.append("\n## Touchdowns\n")
    td = st["touchdowns"]
    L.append(f"{td['team_weeks']:,} team-weeks. Team passing TDs: mean {td['pass_td_mean']:.2f}, distribution {json.dumps({k: round(v, 3) for k, v in td['pass_td_dist'].items()})}. Team rushing TDs: mean {td['rush_td_mean']:.2f}, distribution {json.dumps({k: round(v, 3) for k, v in td['rush_td_dist'].items()})}. Passing TDs equal the sum of teammates' receiving TDs in {100 * td['identity_pass_td_equals_sum_rec_td']:.1f}% of team-weeks (the rest are laterals, receivers outside the four positions, or feed gaps). Two or more teammates catch a TD in {_f(td['p_two_or_more_receivers_score_given_2plus_pass_td'], 3)} of team-weeks with two or more passing TDs. Receiving TD share by role: {json.dumps({k: round(v, 3) for k, v in td['rec_td_share_by_role'].items()})}. Rushing TD share by role: {json.dumps({k: round(v, 3) for k, v in td['rush_td_share_by_role'].items()})}.\n")
    L.append("## Sleeper projection consistency audit (projections only)\n")
    L.append("| Identity | Team-weeks | Relative gap (sum minus QB, over QB): median | mean | p10 | p90 | p95 of abs | Share where the sum exceeds the QB | By season |\n|---|---|---|---|---|---|---|---|---|")
    for k, v in st["sleeper_audit"].items():
        L.append(f"| {k} | {v['team_weeks']:,} | {v['rel_median']:+.3f} | {v['rel_mean']:+.3f} | {v['rel_p10']:+.3f} | {v['rel_p90']:+.3f} | {v['abs_rel_p95']:.3f} | {v['share_sum_above_qb']:.2f} | {json.dumps({kk: round(vv, 3) for kk, vv in v['by_season_rel_mean'].items()})} |")
    L.append("\nWorst cases: " + "; ".join(f"{k}: " + ", ".join(f"{w['season_week_team']} QB {w['qb']} vs sum {w['sum']}" for w in v["worst"][:3]) for k, v in st["sleeper_audit"].items()) + ".\n")
    L.append("## Stack totals against their summed projection\n")
    L.append("| Structure | n | Mean ratio | Variance | p10 / p50 / p90 / p95 / p99 | P over 1.5x | P over 2x | P under 0.5x |\n|---|---|---|---|---|---|---|---|")
    for k, v in st["stacks"].items():
        if v.get("n", 0) >= 30:
            q = v["ratio_q"]
            L.append(f"| {k} | {v['n']:,} | {v['ratio_mean']:.3f} | {v['ratio_var']:.3f} | {q['p10']:.2f} / {q['p50']:.2f} / {q['p90']:.2f} / {q['p95']:.2f} / {q['p99']:.2f} | {v['p_over_1_5']:.3f} | {v['p_over_2']:.3f} | {v['p_under_0_5']:.3f} |")
        else:
            L.append(f"| {k} | {v.get('n', 0)} | too few | | | | | |")
    L.append("\n## Receiving backs as stack partners (training seasons only, no policy chosen)\n")
    L.append("| Bucket | n | QB/RB residual r | Joint top decile (1% if independent) | Joint 1.5x boom | P QB boom | P RB boom |\n|---|---|---|---|---|---|---|")
    for grp in ("by_projected_targets", "by_receiving_share_of_projection"):
        for k, v in st["rb_buckets"][grp].items():
            if v.get("n", 0) >= 40:
                L.append(f"| {k} | {v['n']} | {_f(v['corr'])} | {v['joint_top_decile']:.3f} | {v['joint_boom_1_5x']:.3f} | {v['p_qb_boom']:.3f} | {v['p_rb_boom']:.3f} |")
            else:
                L.append(f"| {k} | {v.get('n', 0)} | too few | | | | |")
    L.append("\n## What this says for the simulator (no parameters chosen here)\n")
    L.append("The fitting stage reads research/data/stage2b_stats.json. The within-team pair correlations, the share-residual correlations and volatilities, the marginal and component dispersions, the touchdown distributions and the stack ratio distributions are the moments the constrained model will be fitted to on these three seasons; 2025 stays closed until the parameters are frozen.\n")
    with open(os.path.join(REPORTS, "stage2b_report.md"), "w") as fh:
        fh.write("\n".join(L))


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else None)
    print("stage 2B written")
