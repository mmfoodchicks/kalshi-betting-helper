# Stage 2C live receipt: the reconciliation on this week's slate

Sleeper's week 1, 2026 regular-season projections, fetched 2026-09-10 20:51 UTC (2:51 PM MDT), run through the production path (`nfl_dfs_sim.weekly_games` with the fetch replaced by the cached feed), weights artifact SHA-256 500fb0dcfd4a30f2 fitted at commit 395c3af on 2022-2024. Reconciliation model wls-identity v1. Snapshot: the feed moves hourly; the numbers below describe that fetch only.

## Slate

16 games, 32 teams. One tripwire: IND (IND @ BAL), passing touchdowns, the receivers sum to 1.24 against Daniel Jones's 0.82 (gap +0.512). Every other team-week sits inside every wire. No team without a quarterback, no bound violations, nothing infeasible.

| Identity | Absolute relative gap, median | p90 | max |
|---|---|---|---|
| pass_att (targets vs attempts times 0.950) | 0.007 | 0.030 | 0.072 |
| pass_cmp (receptions vs completions) | 0.032 | 0.074 | 0.090 |
| pass_yd (receiving yards vs passing yards) | 0.041 | 0.112 | 0.151 |
| pass_td (receiving TDs vs passing TDs) | 0.073 | 0.194 | 0.512 |

DraftKings-point shift of the projected mean (reconciled minus original, threshold bonuses excluded):

| Position | n | Mean (signed) | Abs median | Abs p90 | Abs max |
|---|---|---|---|---|---|
| QB | 33 | +0.204 | 0.256 | 0.867 | 1.103 |
| WR | 161 | -0.111 | 0.115 | 0.507 | 1.004 |
| RB | 100 | -0.033 | 0.037 | 0.171 | 0.337 |
| TE | 98 | -0.066 | 0.064 | 0.332 | 0.770 |

The receivers out-project their quarterbacks on most teams this week, so quarterbacks move up a little and receivers down a little; the largest single move is Daniel Jones, +1.10 points (0.82 to 1.06 passing TDs, 240.6 to 244.1 yards).

## The probe lineups' teams

NO @ DET, DET (no flags): attempts 33.47 vs 33.87 to 33.58; completions 23.69 vs 24.80 to 24.19; yards 262.4 vs 271.8 to 264.5; touchdowns 2.11 vs 2.24 to 2.18. Goff +0.38 points; St. Brown -0.50 (85.0 to 82.6 yards); Williams -0.36; LaPorta -0.33; Gibbs -0.21.

NO @ DET, NO (no flags): attempts 32.69 vs 32.92 to 32.75; completions 22.58 vs 23.45 to 22.97; yards 222.1 vs 227.6 to 223.3; touchdowns 1.57 vs 1.64 to 1.61. Shough +0.21; Olave -0.30; Johnson -0.19; Vele -0.19; Etienne -0.11.

IND @ BAL, BAL (no flags): attempts 27.01 vs 27.06 to 27.02; completions 19.17 vs 18.87 to 19.04; yards 238.6 vs 237.5 to 238.3; touchdowns 1.36 vs 1.29 to 1.32. Jackson -0.17; Flowers +0.11; Andrews +0.09; Henry +0.02.

IND @ BAL, IND (tripped: pass_td +0.512): attempts 32.09 vs 32.15 to 32.11; completions 21.97 vs 22.38 to 22.15; yards 240.6 vs 256.4 to 244.1; touchdowns 0.82 vs 1.24 to 1.06. Jones +1.09; Warren -0.59; Downs -0.56; Pierce -0.45; Allen -0.35; Taylor -0.22.

## What was and was not changed

`means` on every player is byte-identical to before; the legacy simulator reads only `means`, so the board it builds is the same board. `recon` is the balanced line the constrained simulator (Stage 2D) will read. The reconciliation is not applied to any 2025 projection until Stage 2E.
