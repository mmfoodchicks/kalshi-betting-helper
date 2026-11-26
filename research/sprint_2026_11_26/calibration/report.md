# Matchups calibration audit (Phase 6): NFL and NBA props against Kalshi

Read 2026-11-26. Read-only on the repo. Scripts and rows are beside this file:
`build_rows.py`, `analyze.py`, `extra.py`, `rows_nfl_w06_w11.csv`, `nfl_tables.md`, `extra_tables.md`.

## 0. What the evidence is, and what it is not

| source | shape | used for |
|---|---|---|
| `origin/sim-history:errors/mx-record-{nfl,nba}-latest.json` | AGGREGATES only (per stat: ten engine bins, log-loss sums, pick ROI). No graded rows, no market bins. | live verdicts, live pick ROI, engine reliability (sections 1e, 2, 4) |
| `errors/calib-params-latest.json` | the tuning and the verdicts per stat | section 4 |
| `research/data/nfl_matchup_model/kalshi.json` | aggregates (2025; 2026 wk 1-3, 6) | the quoted -7.0% and 0.456 vs 0.432 |
| `research/data/nfl_matchup_model/rush_cv/rows_2023_2025.csv.gz` | 12,227 player-game rows | section 3a |
| **rebuilt here**: `sim-history:props/nfl_props_2026_w06..w11.json` (Kalshi's de-vigged mid per rung, frozen 5 min before kickoff, and Sleeper's frozen line) + `nfl_matchup.player_markets` on that line (the Matchups engine: no shipped term touches a Kalshi stat) + Sleeper's official box (fetched from Sleeper's public stats endpoint) | **20,859 rungs, 4,301 ladders, 82 games** | every row-level NFL table |

Checks on the rebuild: week 6 gives model 0.5049 / Kalshi 0.4569 on 2,841 rungs against the report's
0.5051 / 0.4553 on 2,875; rushing yards model-minus-Kalshi is +0.051 +/- 0.012 here against the live
record's +0.048 +/- 0.012; anytime TD +0.0136 +/- 0.0025 against +0.0128 +/- 0.0033.

Limits, stated once:
- The props log holds MIDS, not asks. Execution cost in section 2 is the exact fee plus an ASSUMED half-spread; the at-the-ask truth is the live record's pick ROI.
- Weeks 8-11 are the same games the live record graded (it started 2026-10-29): the rebuild and the live record are two readings of one sample, not two samples. Neither was used to fit the engine (fit on 2023-2025).
- Players with no offensive snap are dropped as voids (69 ladders); 61 ladders had no Sleeper line.
- **NBA row-level data: none available (n = 0 rows).** Everything NBA below is from aggregates; market reliability by decile for the NBA is "insufficient data".
- Which is which in CLAUDE.md's "0.542 vs 0.556": **Kalshi's mid 0.542, the model 0.556** (tight books, 2,278 markets / 445 games, 2025-26; market better by 0.0138 +/- 0.0041). The NFL figure is model 0.456, Kalshi 0.432.

All SEs are clustered by game unless marked. "Diff" is model minus Kalshi: positive means Kalshi is better.

## Summary of answers

**1. Calibration.** NFL: the model is overconfident, Kalshi is not. Slope of logit(outcome) on logit(p), all stats, 82 games: model **0.854 +/- 0.029**, Kalshi **0.978 +/- 0.031**. It is concentrated in rushing yards (model 0.703 +/- 0.054; Kalshi 0.970 +/- 0.052) and receiving yards (0.826 +/- 0.041 vs 0.935 +/- 0.042); receptions (0.924 +/- 0.046), TDs (0.913 +/- 0.052) and passing yards (1.036 +/- 0.079) are within 2 SE of 1. Both tails are wrong in the direction of too little spread: rungs the model calls >= 0.80 hit 0.809 against 0.856 (gap -0.047 +/- 0.018; Kalshi -0.007 +/- 0.018 on the same rows), and rungs it calls < 0.10 hit 0.073 against 0.048 (+0.025 +/- 0.006; Kalshi -0.003 +/- 0.005). Rushing yards >= 0.80: hit 0.712 against 0.861 (-0.149 +/- 0.030). The live record's bins agree (rush_yd slope ~0.75; top bins 0.861 said, 0.694 hit, n 186).
NBA: the engine is CALIBRATED (binned slope 0.989, approx SE 0.020; top bins 0.880 said / 0.884 hit, n 4,917) and still loses to Kalshi in all five stats. Its problem is resolution (information), not calibration. NBA market deciles: insufficient data (aggregates only).

**2. Why "+EV" loses (NFL rebuilt rows, 12,144 one-contract bets that are fee-in +EV at mid + 2c).**

| step | ROI | what it measures |
|---|---|---|
| the model's own claim | +10.4% | |
| bought at the MID, no fee | +2.2% [-1.4, +5.8] | about 8 points of the claim are not there: miscalibration + selection |
| + Kalshi fee (mean 1.67c on 50.5c) | -1.2% [-4.7, +2.4] | fee: -3.4 points |
| + 2c half-spread (assumed) | -4.9% [-8.4, -1.4] | spread: -3.7 points (about 1.9 points per cent of half-spread) |

Live, at the real ask: NFL close picks -1.7% +/- 1.8 (12,011), first sightings -5.4% +/- 3.1 (2,253), rushing yards -9.9% +/- 3.8 (2,306); NBA close -4.9% +/- 1.1 (19,687). Research: NFL -7.0% [-14.4, +1.1], NBA -14% (90% [-24.9, -3.5]).
- **Selection dominates the modelling share.** In logit(y) = a + b logit(mid) + w [logit(model) - logit(mid)], w = **+0.05 +/- 0.08** (all stats): where the model and Kalshi disagree, about 5% of the disagreement is real. Rushing -0.10 +/- 0.10, TD -0.19 +/- 0.11; best case passing yards +0.34 +/- 0.33 (not distinguishable from 0 or from 1/2).
- **Miscalibration alone is the smaller part.** An out-of-sample Platt on the engine (forward chained by week, weeks 8-11) recovers 0.0030 +/- 0.0017 of the 0.0174 +/- 0.0045 nats Kalshi leads by (17%); in rushing yards 0.0168 +/- 0.0070 of 0.0391 +/- 0.0138 (43%).
- **Does the loss concentrate in the biggest disagreements? Not in ROI; yes in shortfall.** Realised edge is flat at 12-23% of the claimed edge in every bucket (claimed 0-5 points: +0.9 +/- 1.0 realised; 25+ points: +3.6 +/- 5.1 realised of 31 claimed, n 232). The worst executable bucket is 10-15 points (-10.3% [-15.9, -4.4]); 15+ points is about break-even with wide intervals. By stat: rushing -11.8% [-18.5, -5.1] and TD -7.9% [-11.8, -4.4] carry the loss; receptions, receiving and passing yards are -2 to -3%, intervals spanning 0. Cheap contracts (< 20c) lose most after costs (-19.0% [-35.1, -1.5]): a 1c fee and 2c of spread on a 10c contract.
- Thin books / stale quotes: not measurable here (the log keeps believable mids only); `prop_value` already withholds +EV on both.

**3. The RB low-threshold tail: CONFIRMED. The QB "opposite at higher thresholds": NOT confirmed on 2026 weeks 6-11.**
- RB, Sleeper line 20-30, thresholds <= 10: hit **0.620**, model **0.853**, Kalshi 0.670 (50 player-games; gap -0.233 +/- 0.070; Kalshi -0.050 +/- 0.071). On 2025 (207 rows): 0.676 vs 0.847 (-0.171 +/- 0.032). The same backs clear high thresholds MORE than modelled (41+: 0.193 vs 0.055, +0.137 +/- 0.060): the spread is too narrow, as the report said.
- QB line 10-20: 21-40 yards hit 0.170 vs 0.122 (+0.048 +/- 0.043), 41+ insufficient data (n = 25 rungs, 14 player-games). Direction as claimed, not established.
- QB lines of 20+ fail the other way and at every threshold: actual mean 15.0 against Sleeper 23.8 (35 player-games, SE 2.2) and 17.9 against 37.7 (22, SE 3.1). That is a LEVEL miss (Sleeper's QB rushing line ran high; Kalshi was high too but less), not a shape miss. 57 player-games over six weeks: flag, do not fit.
- **The fix already tried:** a pre-registered line-dependent CV, cv(L) = c x max(L,1)^d (RB 3.69, -0.43; QB 3.64, -0.48; fitted 2023-24), report section 9. It repaired the cells it was built for on 2025 (RB 10+ on 20-30 lines from 16 points high to 3) but **failed its gate: -0.0086 +/- 0.0137 nats on 1,327 priced 2025 rows, against a rule of < -0.004 and < -2 SE**. The gate scored every priced row with a summed threshold loss, where the affected cells are a few hundred rows and the per-row SE is 0.014: a power failure, not evidence the fix is wrong. Nothing shipped, and the registered weeks 8-10 judgement did not run.
- Descriptive only (not that judgement, decides nothing): on 2026 Kalshi-listed rushing rungs the same candidate reads -0.0173 +/- 0.0060 (weeks 6-11, 82 games), -0.0119 +/- 0.0044 (weeks 8-11), almost all from RB lines under 30 (-0.068 +/- 0.028). **Kalshi still beats the repaired model by 0.034 +/- 0.009.**

**4. Market-aware calibration, per stat (calib-params-latest.json, fitted 2026-11-26).**

| sport | stat | Platt on the engine | engine vs Kalshi, live closes (diff +/- SE, games) | blend report (blend minus engine-Platt gain) |
|---|---|---|---|---|
| nba | pts | identity: gain -0.0015 +/- 0.0007, "did not beat the identity" | market better +0.0161 +/- 0.0029 (179) | 0.0145 +/- 0.0041 |
| nba | pra | identity: +0.0002 +/- 0.0012 | market better +0.0177 +/- 0.0043 | 0.0130 +/- 0.0049 |
| nba | ast | identity: +0.0006 +/- 0.0004 (under the 0.001 floor) | market better +0.0106 +/- 0.0033 | 0.0072 +/- 0.0027 |
| nba | fg3m | identity: +0.0001 +/- 0.0004 | market better +0.0088 +/- 0.0025 | 0.0111 +/- 0.0026 |
| nba | reb | identity: -0.0002 +/- 0.0005 | market better +0.0053 +/- 0.0022 | 0.0031 +/- 0.0025 |
| nfl | rush_yd | identity: nothing scored out of sample yet (12 dates; needs 30 games past the first 10) | market better +0.0456 +/- 0.0125 (56) | none (under its floors) |
| nfl | td | identity: same reason | market better +0.0134 +/- 0.0033 | not scored yet |
| nfl | rec | identity: same reason | market better +0.0133 +/- 0.0065 (z 2.03: borderline; the record file's later look reads 0.0120 +/- 0.0065, undecided) | not scored yet |
| nfl | rec_yd | identity: same reason | not decided +0.0089 +/- 0.0065 | not scored yet |
| nfl | pass_yd | identity: below the floors (112 ladders) | not decided +0.0093 +/- 0.0130 | none |

- The blend report's number is the blend against the ENGINE's Platt, so a positive figure says Kalshi's mid improves the engine. It never compares the blend with Kalshi alone and so cannot say whether the engine adds to the market.
- That missing comparison, on the rebuilt NFL rows (forward chained, weeks 8-11, 56 games, ladder-weighted): **blend minus Kalshi = +0.0011 +/- 0.0009 (worse), mean weight on the engine 0.08.** Per stat: rec +0.0008 +/- 0.0012, rec_yd +0.0001 +/- 0.0013, rush_yd +0.0007 +/- 0.0021, td +0.0004 +/- 0.0018, pass_yd +0.0184 +/- 0.0082 (974 rungs). NBA: insufficient data (no rows).
- **No stat, in either sport, has the model beating or adding to the market out of sample.** The two cells that leaned the model's way in research (NBA threes on all books, z +2.5 on 593 markets; NFL RB receptions, 66 rungs) did not hold: live threes are market better by 0.0088 +/- 0.0025; RB receptions here +0.010 +/- 0.011. The nearest thing left is RB receiving yards (-0.009 +/- 0.010, 1,383 rungs): one of twelve stat-by-position cells, under 1 SE.

**5. Recommendations: section 5 below.**

## 5. Recommendations, ranked

Every criterion is written here before any test and reads only games played after 2026-11-26 (NFL week 13 on; week 12 is in progress and its Thursday games may already be in the live record). Weeks 6-11 are now spent for every question in this report.

1. **Headline Kalshi's price; mute the model. This is the honest answer for every stat in both sports today.**
   - What the UI already does: `matchups.mark_record` adds "market better on record" to every +EV line in a stat whose record has Kalshi ahead, and `static/matchups.js` then draws that EV grey, unbolded, ranked with thin books. The live record shows it working: **picks "shown" n = 0, "muted" = all of them, in every stat of both sports** (the static research record covers the stats the live rule has not decided, through the "*" fallback).
   - What it does not do: the model's probability is still the line's headline figure, and the EV is still printed (grey). Proposed: where the stat is market-better, show Kalshi's de-vigged mid as the probability and the engine's figure as a secondary "engine says" beside it; keep the matchup breakdown as explanation.
   - Acceptance: none needed to mute (already decided: NBA five of five at 2.4-5.6 SE over 179 games; NFL rush_yd and td at 3.7-4.1 SE). The mute lifts for a stat only by the existing `_decide` rule reading "engine_better" (10 dates, 30 games, 2 SE by game) on games after today, and the change is display-only: board probabilities and the record's rows must be byte-identical before and after.
2. **Report "blend minus Kalshi" in `mx_record`'s blend_report** (reported, never applied, as now). It is the only number that answers "does the engine add to the market", and the file does not carry it.
   - Acceptance for ever SHOWING a blended figure in a stat: blend minus Kalshi < -0.001 nats and < -2 SE clustered by game, over >= 30 games on dates after the change, forward chained. Today's reading on NFL is +0.0011 +/- 0.0009 with 8% weight on the engine: expect "no".
3. **Re-register the line-dependent rushing spread against a rung-level gate.** The candidate is already fitted (2023-24) and frozen in `rush_cv/choose.json`; only the gate changes, to the question that matters on the tab.
   - Acceptance (NFL weeks 13-18, Kalshi-listed RB and QB rushing rungs, frozen Sleeper lines): per-rung log loss, line CV minus single CV, < -0.004 and < -2 SE by game; AND RB lines under 30, thresholds <= 15: |hit - model| within 2 SE of zero; AND QB rungs not worse by more than 1 SE. Fail any: the single CV and the slate note stay.
   - This is a display-accuracy fix. It does not un-mute rushing: Kalshi leads the repaired model by 0.034 +/- 0.009.
4. **Let the live Platt gate read rushing yards; change nothing.** On the rebuilt rows a forward-chained Platt gains 0.0168 +/- 0.0070 in rushing yards (2.4 SE, 56 games) and under 1.4 SE elsewhere, except passing yards, where it is WORSE (+0.0114 +/- 0.0063). The live gate (0.001 nats, 2 SE, 30 scored games) is the right criterion and will read rush_yd within about two weeks. If item 3 passes first, the Platt must be refitted on the new figure, not stacked.
5. **Export enough of the record to audit the NBA.** Add Kalshi's own ten bins (p, hit, n) and per-game sums to the record payload, or a row export to sim-history. Acceptance: this report's tables 1a-1d and 2a reproducible for the NBA from sim-history alone. Until then the NBA answers to questions 1 (market side), 2 (decomposition) and 4 (blend vs market) are "insufficient data (n = 0 rows)".
6. **Long-shot floor (low priority while muted).** The model under-prices its < 0.10 rungs in receiving yards (+0.040 +/- 0.010), rushing yards (+0.047 +/- 0.017) and receptions (+0.027 +/- 0.010); Kalshi is within 1.3 SE of calibrated on all three. A heavier right tail is the candidate; acceptance on weeks 13-18: log loss on rungs the shipped model prices under 0.10 improves by > 2 SE with no loss beyond 1 SE on the rest.
7. **QB rushing level: no action.** Insufficient data (57 player-games with lines of 20+, six weeks, the same few quarterbacks repeating, so a by-player cluster would be wider still). Re-read once after week 18.

Not recommended: any threshold on the size of the model-market disagreement as a "+EV" filter. Realised edge is 12-23% of claimed in every bucket and no bucket clears costs with an interval above zero.

---

# Tables

# NFL rebuilt rows: n 20859 games 82 ladders 4301 | weeks 7-11 n 18018
check wk6 vs kalshi.json props_log_w06 (2875; 0.5051 / 0.4553): n 2841 model 0.5049 mkt 0.4569

## 1a. log loss, model vs Kalshi mid (per rung; diff = model - market, game-clustered SE)
| scope | n | games | hit | model | Kalshi | diff | SE | z | ladder-weighted diff +/- SE |
|---|---|---|---|---|---|---|---|---|---|
| all wk6-11 | 20859 | 82 | 0.323 | 0.5001 | 0.4813 | +0.0188 | 0.0040 | +4.7 | +0.0192 +/- 0.0036 |
| all wk7-11 | 18018 | 70 | 0.325 | 0.4994 | 0.4852 | +0.0142 | 0.0042 | +3.4 | +0.0154 +/- 0.0038 |
| pass_yd | 1429 | 82 | 0.421 | 0.4802 | 0.4739 | +0.0063 | 0.0106 | +0.6 | +0.0059 +/- 0.0103 |
| rec | 4900 | 82 | 0.348 | 0.5075 | 0.4932 | +0.0143 | 0.0052 | +2.8 | +0.0182 +/- 0.0069 |
| rec_yd | 8056 | 82 | 0.349 | 0.5321 | 0.5216 | +0.0105 | 0.0053 | +2.0 | +0.0139 +/- 0.0055 |
| rush_yd | 3704 | 82 | 0.337 | 0.5571 | 0.5057 | +0.0514 | 0.0122 | +4.2 | +0.0498 +/- 0.0133 |
| td | 2770 | 82 | 0.135 | 0.3281 | 0.3145 | +0.0136 | 0.0025 | +5.5 | +0.0152 +/- 0.0032 |
| rush_yd/RB | 2757 | 82 | 0.368 | 0.5594 | 0.5076 | +0.0518 | 0.0146 | +3.5 | +0.0535 +/- 0.0168 |
| rush_yd/QB | 895 | 82 | 0.250 | 0.5499 | 0.4998 | +0.0501 | 0.0203 | +2.5 | +0.0413 +/- 0.0199 |
| rec/RB | 984 | 82 | 0.342 | 0.5261 | 0.5160 | +0.0100 | 0.0107 | +0.9 | +0.0143 +/- 0.0130 |
| rec/WR | 2642 | 82 | 0.359 | 0.4883 | 0.4756 | +0.0127 | 0.0064 | +2.0 | +0.0179 +/- 0.0088 |
| rec/TE | 1274 | 82 | 0.330 | 0.5330 | 0.5119 | +0.0211 | 0.0108 | +2.0 | +0.0222 +/- 0.0124 |
| rec_yd/RB | 1383 | 82 | 0.302 | 0.5348 | 0.5435 | -0.0087 | 0.0102 | -0.9 | -0.0013 +/- 0.0095 |
| rec_yd/WR | 4781 | 82 | 0.367 | 0.5309 | 0.5199 | +0.0110 | 0.0068 | +1.6 | +0.0138 +/- 0.0077 |
| rec_yd/TE | 1892 | 82 | 0.337 | 0.5331 | 0.5101 | +0.0230 | 0.0101 | +2.3 | +0.0278 +/- 0.0113 |
| td/RB | 777 | 82 | 0.187 | 0.3710 | 0.3637 | +0.0073 | 0.0050 | +1.5 | +0.0069 +/- 0.0054 |
| td/WR | 1099 | 82 | 0.126 | 0.3234 | 0.3061 | +0.0173 | 0.0049 | +3.5 | +0.0196 +/- 0.0063 |
| td/TE | 615 | 82 | 0.106 | 0.3026 | 0.2895 | +0.0131 | 0.0058 | +2.2 | +0.0162 +/- 0.0073 |
| td/QB | 260 | 82 | 0.092 | 0.2898 | 0.2713 | +0.0185 | 0.0074 | +2.5 | +0.0138 +/- 0.0075 |

## 1b. calibration slope: logit(y) = a + b logit(p), b<1 = overconfident (game-clustered SE)
| scope | n | model a | model b (SE) | Kalshi a | Kalshi b (SE) |
|---|---|---|---|---|---|
| all | 20859 | -0.094 (0.051) | 0.854 (0.029) | -0.063 (0.054) | 0.978 (0.031) |
| pass_yd | 1429 | -0.059 (0.145) | 1.036 (0.079) | +0.027 (0.149) | 1.060 (0.076) |
| rec | 4900 | +0.040 (0.066) | 0.924 (0.046) | -0.089 (0.067) | 0.983 (0.042) |
| rec_yd | 8056 | -0.070 (0.061) | 0.826 (0.041) | -0.050 (0.062) | 0.935 (0.042) |
| rush_yd | 3704 | -0.281 (0.071) | 0.703 (0.054) | -0.123 (0.080) | 0.970 (0.052) |
| td | 2770 | -0.181 (0.089) | 0.913 (0.052) | +0.122 (0.097) | 1.117 (0.059) |
| rush_yd/RB | 2757 | -0.155 (0.088) | 0.742 (0.070) | -0.016 (0.097) | 1.010 (0.069) |
| rush_yd/QB | 895 | -0.726 (0.160) | 0.604 (0.091) | -0.521 (0.177) | 0.817 (0.116) |

## 1c. reliability by decile of each forecaster's own probability (mean p / hit rate / n; gap = hit - p with game-clustered SE)

**all stats**

| decile | model p | model hit | n | gap (SE) | Kalshi p | Kalshi hit | n | gap (SE) |
|---|---|---|---|---|---|---|---|---|
| 0.0-0.1 | 0.048 | 0.073 | 5131 | +0.025 (0.006) | 0.056 | 0.052 | 4801 | -0.003 (0.005) |
| 0.1-0.2 | 0.147 | 0.154 | 3523 | +0.007 (0.008) | 0.145 | 0.147 | 3756 | +0.002 (0.009) |
| 0.2-0.3 | 0.247 | 0.245 | 2668 | -0.002 (0.010) | 0.247 | 0.240 | 2631 | -0.007 (0.011) |
| 0.3-0.4 | 0.348 | 0.344 | 2154 | -0.004 (0.014) | 0.347 | 0.354 | 2122 | +0.007 (0.013) |
| 0.4-0.5 | 0.450 | 0.422 | 1817 | -0.028 (0.015) | 0.448 | 0.421 | 1891 | -0.026 (0.013) |
| 0.5-0.6 | 0.548 | 0.530 | 1626 | -0.019 (0.016) | 0.548 | 0.526 | 1669 | -0.021 (0.016) |
| 0.6-0.7 | 0.649 | 0.620 | 1480 | -0.028 (0.016) | 0.646 | 0.618 | 1493 | -0.029 (0.018) |
| 0.7-0.8 | 0.750 | 0.725 | 1289 | -0.026 (0.015) | 0.748 | 0.727 | 1336 | -0.021 (0.016) |
| 0.8-0.9 | 0.845 | 0.799 | 995 | -0.046 (0.018) | 0.842 | 0.843 | 1039 | +0.001 (0.017) |
| 0.9-1.0 | 0.919 | 0.864 | 176 | -0.056 (0.031) | 0.919 | 0.917 | 121 | -0.001 (0.029) |

**pass_yd**

| decile | model p | model hit | n | gap (SE) | Kalshi p | Kalshi hit | n | gap (SE) |
|---|---|---|---|---|---|---|---|---|
| 0.0-0.1 | 0.057 | 0.036 | 250 | -0.021 (0.015) | 0.062 | 0.042 | 264 | -0.020 (0.015) |
| 0.1-0.2 | 0.146 | 0.145 | 179 | -0.001 (0.028) | 0.145 | 0.126 | 206 | -0.018 (0.027) |
| 0.2-0.3 | 0.244 | 0.230 | 148 | -0.015 (0.034) | 0.247 | 0.252 | 147 | +0.004 (0.035) |
| 0.3-0.4 | 0.348 | 0.289 | 135 | -0.059 (0.040) | 0.351 | 0.397 | 131 | +0.046 (0.045) |
| 0.4-0.5 | 0.453 | 0.467 | 120 | +0.014 (0.048) | 0.446 | 0.485 | 97 | +0.039 (0.052) |
| 0.5-0.6 | 0.552 | 0.578 | 116 | +0.025 (0.045) | 0.545 | 0.539 | 115 | -0.006 (0.046) |
| 0.6-0.7 | 0.650 | 0.697 | 122 | +0.047 (0.041) | 0.654 | 0.689 | 122 | +0.035 (0.044) |
| 0.7-0.8 | 0.747 | 0.723 | 141 | -0.024 (0.039) | 0.754 | 0.739 | 142 | -0.015 (0.039) |
| 0.8-0.9 | 0.846 | 0.818 | 159 | -0.029 (0.036) | 0.853 | 0.860 | 186 | +0.007 (0.031) |
| 0.9-1.0 | 0.922 | 0.898 | 59 | -0.024 (0.039) | insufficient | (n=19) | 19 | - |

**rec**

| decile | model p | model hit | n | gap (SE) | Kalshi p | Kalshi hit | n | gap (SE) |
|---|---|---|---|---|---|---|---|---|
| 0.0-0.1 | 0.049 | 0.076 | 1091 | +0.027 (0.010) | 0.057 | 0.048 | 967 | -0.009 (0.008) |
| 0.1-0.2 | 0.148 | 0.158 | 793 | +0.010 (0.014) | 0.146 | 0.158 | 804 | +0.012 (0.014) |
| 0.2-0.3 | 0.248 | 0.251 | 622 | +0.003 (0.018) | 0.247 | 0.224 | 593 | -0.022 (0.018) |
| 0.3-0.4 | 0.347 | 0.354 | 565 | +0.007 (0.022) | 0.347 | 0.339 | 508 | -0.008 (0.023) |
| 0.4-0.5 | 0.450 | 0.438 | 496 | -0.012 (0.024) | 0.446 | 0.392 | 528 | -0.054 (0.021) |
| 0.5-0.6 | 0.548 | 0.562 | 420 | +0.014 (0.024) | 0.551 | 0.556 | 396 | +0.005 (0.023) |
| 0.6-0.7 | 0.648 | 0.675 | 375 | +0.027 (0.025) | 0.644 | 0.610 | 421 | -0.033 (0.026) |
| 0.7-0.8 | 0.752 | 0.769 | 303 | +0.017 (0.024) | 0.745 | 0.723 | 307 | -0.022 (0.023) |
| 0.8-0.9 | 0.845 | 0.870 | 207 | +0.025 (0.024) | 0.846 | 0.842 | 291 | -0.004 (0.024) |
| 0.9-1.0 | 0.914 | 0.857 | 28 | -0.057 (0.068) | 0.918 | 0.918 | 85 | -0.000 (0.033) |

**rec_yd**

| decile | model p | model hit | n | gap (SE) | Kalshi p | Kalshi hit | n | gap (SE) |
|---|---|---|---|---|---|---|---|---|
| 0.0-0.1 | 0.053 | 0.092 | 1626 | +0.040 (0.010) | 0.064 | 0.076 | 1431 | +0.012 (0.009) |
| 0.1-0.2 | 0.147 | 0.161 | 1388 | +0.014 (0.012) | 0.145 | 0.146 | 1473 | +0.001 (0.013) |
| 0.2-0.3 | 0.248 | 0.263 | 1058 | +0.015 (0.013) | 0.248 | 0.254 | 1050 | +0.007 (0.014) |
| 0.3-0.4 | 0.350 | 0.355 | 876 | +0.005 (0.017) | 0.348 | 0.364 | 912 | +0.016 (0.017) |
| 0.4-0.5 | 0.450 | 0.426 | 772 | -0.024 (0.020) | 0.449 | 0.428 | 822 | -0.021 (0.017) |
| 0.5-0.6 | 0.549 | 0.517 | 718 | -0.032 (0.019) | 0.546 | 0.524 | 761 | -0.022 (0.019) |
| 0.6-0.7 | 0.650 | 0.611 | 614 | -0.039 (0.024) | 0.646 | 0.590 | 629 | -0.056 (0.023) |
| 0.7-0.8 | 0.751 | 0.729 | 543 | -0.022 (0.021) | 0.749 | 0.736 | 592 | -0.012 (0.024) |
| 0.8-0.9 | 0.844 | 0.812 | 421 | -0.031 (0.024) | 0.838 | 0.853 | 381 | +0.015 (0.022) |
| 0.9-1.0 | 0.913 | 0.875 | 40 | -0.038 (0.061) | insufficient | (n=5) | 5 | - |

**rush_yd**

| decile | model p | model hit | n | gap (SE) | Kalshi p | Kalshi hit | n | gap (SE) |
|---|---|---|---|---|---|---|---|---|
| 0.0-0.1 | 0.049 | 0.096 | 677 | +0.047 (0.017) | 0.064 | 0.055 | 673 | -0.009 (0.012) |
| 0.1-0.2 | 0.148 | 0.146 | 610 | -0.002 (0.018) | 0.146 | 0.150 | 668 | +0.004 (0.016) |
| 0.2-0.3 | 0.248 | 0.227 | 481 | -0.022 (0.022) | 0.247 | 0.228 | 482 | -0.019 (0.020) |
| 0.3-0.4 | 0.349 | 0.341 | 390 | -0.008 (0.025) | 0.348 | 0.321 | 405 | -0.027 (0.024) |
| 0.4-0.5 | 0.448 | 0.381 | 344 | -0.067 (0.022) | 0.448 | 0.422 | 360 | -0.026 (0.024) |
| 0.5-0.6 | 0.547 | 0.502 | 323 | -0.045 (0.027) | 0.549 | 0.494 | 332 | -0.055 (0.031) |
| 0.6-0.7 | 0.647 | 0.553 | 333 | -0.095 (0.024) | 0.646 | 0.645 | 299 | -0.000 (0.027) |
| 0.7-0.8 | 0.750 | 0.668 | 289 | -0.083 (0.026) | 0.748 | 0.705 | 292 | -0.042 (0.028) |
| 0.8-0.9 | 0.846 | 0.688 | 208 | -0.159 (0.031) | 0.835 | 0.807 | 181 | -0.029 (0.033) |
| 0.9-1.0 | 0.924 | 0.816 | 49 | -0.108 (0.055) | insufficient | (n=12) | 12 | - |

**td**

| decile | model p | model hit | n | gap (SE) | Kalshi p | Kalshi hit | n | gap (SE) |
|---|---|---|---|---|---|---|---|---|
| 0.0-0.1 | 0.039 | 0.046 | 1487 | +0.007 (0.005) | 0.043 | 0.033 | 1466 | -0.009 (0.005) |
| 0.1-0.2 | 0.148 | 0.145 | 553 | -0.003 (0.014) | 0.145 | 0.137 | 605 | -0.008 (0.015) |
| 0.2-0.3 | 0.244 | 0.214 | 359 | -0.029 (0.021) | 0.245 | 0.234 | 359 | -0.011 (0.023) |
| 0.3-0.4 | 0.343 | 0.309 | 188 | -0.035 (0.034) | 0.342 | 0.398 | 166 | +0.056 (0.038) |
| 0.4-0.5 | 0.449 | 0.388 | 85 | -0.061 (0.054) | 0.445 | 0.464 | 84 | +0.019 (0.047) |
| 0.5-0.6 | 0.545 | 0.510 | 49 | -0.034 (0.075) | 0.545 | 0.508 | 65 | -0.038 (0.063) |
| 0.6-0.7 | 0.635 | 0.583 | 36 | -0.051 (0.080) | 0.655 | 0.773 | 22 | +0.118 (0.088) |
| 0.7-0.8 | insufficient | (n=13) | 13 | - | insufficient | (n=3) | 3 | - |
| 0.8-0.9 | insufficient | (n=0) | 0 | - | insufficient | (n=0) | 0 | - |
| 0.9-1.0 | insufficient | (n=0) | 0 | - | insufficient | (n=0) | 0 | - |

## 1d. high-probability rungs vs long shots (bands of the MODEL's p; both forecasters scored on the same rows)
| stat | band | n | model p | Kalshi p | hit | model gap (SE) | Kalshi gap (SE) | LL diff model-mkt (SE) |
|---|---|---|---|---|---|---|---|---|
| all | long shot <0.20 | 8654 | 0.088 | 0.108 | 0.106 | +0.018 (0.006) | -0.002 (0.006) | +0.0245 (0.0059) |
| all | 0.20-0.50 | 6639 | 0.335 | 0.340 | 0.326 | -0.010 (0.011) | -0.015 (0.011) | +0.0111 (0.0044) |
| all | 0.50-0.80 | 4395 | 0.641 | 0.628 | 0.617 | -0.024 (0.014) | -0.011 (0.014) | +0.0167 (0.0054) |
| all | high >=0.80 | 1171 | 0.856 | 0.816 | 0.809 | -0.047 (0.018) | -0.007 (0.018) | +0.0278 (0.0094) |
| pass_yd | long shot <0.20 | 429 | 0.094 | 0.100 | 0.082 | -0.013 (0.018) | -0.018 (0.018) | +0.0026 (0.0089) |
| pass_yd | 0.20-0.50 | 403 | 0.341 | 0.321 | 0.320 | -0.021 (0.034) | -0.001 (0.034) | +0.0046 (0.0144) |
| pass_yd | 0.50-0.80 | 379 | 0.656 | 0.642 | 0.670 | +0.014 (0.035) | +0.029 (0.035) | +0.0100 (0.0166) |
| pass_yd | high >=0.80 | 218 | 0.867 | 0.836 | 0.839 | -0.027 (0.033) | +0.004 (0.033) | +0.0101 (0.0165) |
| rec | long shot <0.20 | 1884 | 0.091 | 0.116 | 0.110 | +0.020 (0.010) | -0.005 (0.010) | +0.0253 (0.0086) |
| rec | 0.20-0.50 | 1683 | 0.340 | 0.369 | 0.340 | -0.000 (0.017) | -0.029 (0.016) | +0.0036 (0.0064) |
| rec | 0.50-0.80 | 1098 | 0.638 | 0.664 | 0.658 | +0.019 (0.018) | -0.006 (0.017) | +0.0125 (0.0064) |
| rec | high >=0.80 | 235 | 0.853 | 0.870 | 0.868 | +0.015 (0.024) | -0.002 (0.024) | +0.0117 (0.0091) |
| rec_yd | long shot <0.20 | 3014 | 0.096 | 0.120 | 0.124 | +0.028 (0.009) | +0.004 (0.010) | +0.0223 (0.0079) |
| rec_yd | 0.20-0.50 | 2706 | 0.339 | 0.347 | 0.339 | +0.001 (0.014) | -0.008 (0.014) | +0.0022 (0.0062) |
| rec_yd | 0.50-0.80 | 1875 | 0.640 | 0.622 | 0.609 | -0.031 (0.018) | -0.013 (0.018) | +0.0031 (0.0070) |
| rec_yd | high >=0.80 | 461 | 0.850 | 0.808 | 0.818 | -0.032 (0.025) | +0.010 (0.024) | +0.0115 (0.0104) |
| rush_yd | long shot <0.20 | 1287 | 0.096 | 0.123 | 0.120 | +0.023 (0.015) | -0.004 (0.014) | +0.0571 (0.0268) |
| rush_yd | 0.20-0.50 | 1215 | 0.337 | 0.324 | 0.307 | -0.030 (0.020) | -0.017 (0.019) | +0.0375 (0.0100) |
| rush_yd | 0.50-0.80 | 945 | 0.645 | 0.602 | 0.570 | -0.074 (0.021) | -0.031 (0.020) | +0.0520 (0.0119) |
| rush_yd | high >=0.80 | 257 | 0.861 | 0.763 | 0.712 | -0.149 (0.030) | -0.051 (0.030) | +0.0866 (0.0282) |
| td | long shot <0.20 | 2040 | 0.069 | 0.075 | 0.073 | +0.004 (0.006) | -0.002 (0.006) | +0.0112 (0.0027) |
| td | 0.20-0.50 | 632 | 0.301 | 0.280 | 0.266 | -0.035 (0.015) | -0.014 (0.014) | +0.0225 (0.0053) |
| td | 0.50-0.80 | 98 | 0.602 | 0.555 | 0.571 | -0.030 (0.050) | +0.016 (0.049) | +0.0078 (0.0146) |
| td | high >=0.80 | insufficient data (n=0) | | | | | | |

## 2a. Does the model add anything to the mid? logit(y) = a + b logit(p_mkt) + w [logit(p_model) - logit(p_mkt)]  (w=0: the disagreement is noise; w=1: the model is right)
| scope | n | b on market (SE) | w on model-minus-market (SE) | w ladder-weighted (SE) |
|---|---|---|---|---|
| all | 20859 | 0.977 (0.031) | +0.051 (0.081) | +0.021 (0.075) |
| wk7-11 | 18018 | 0.965 (0.033) | +0.143 (0.090) | +0.095 (0.084) |
| pass_yd | 1429 | 1.073 (0.076) | +0.344 (0.331) | +0.371 (0.323) |
| rec | 4900 | 0.985 (0.043) | +0.074 (0.124) | +0.050 (0.132) |
| rec_yd | 8056 | 0.926 (0.042) | +0.212 (0.133) | +0.171 (0.120) |
| rush_yd | 3704 | 0.974 (0.054) | -0.097 (0.104) | -0.010 (0.107) |
| td | 2770 | 1.128 (0.060) | -0.193 (0.110) | -0.228 (0.128) |

## 2b. Hypothetical one-contract bets on the reconstructed rows (weeks 6-11). The log has mids only; the spread is ASSUMED (half-spread h cents), the fee is Kalshi's quadratic taker fee rounded up to the cent.
Selection is fixed per row of the table (the bets that are fee-in +EV at mid + h); the same bets are then costed three ways. ROI with game-clustered bootstrap 95%.
| half-spread h | bets | model's claimed ROI | ROI at the MID, no fee | ROI at mid + fee | ROI at mid + h + fee (executable) |
|---|---|---|---|---|---|
| 0c | 17066 | +10.8% | +1.7% [-1.3, +4.6] | -1.5% [-4.3, +1.5] | -1.4% [-4.3, +1.4] |
| 1c | 14415 | +10.6% | +1.7% [-1.6, +5.0] | -1.6% [-4.8, +1.4] | -3.5% [-6.6, -0.3] |
| 2c | 12144 | +10.4% | +2.2% [-1.4, +5.8] | -1.2% [-4.7, +2.4] | -4.9% [-8.4, -1.4] |
| 3c | 10154 | +10.2% | +2.0% [-2.0, +6.1] | -1.5% [-5.4, +2.4] | -7.0% [-10.6, -3.2] |
| 5c | 7092 | +9.9% | +1.8% [-3.2, +6.9] | -1.9% [-6.9, +2.8] | -11.0% [-15.6, -6.4] |

## 2c. By size of the disagreement (h = 2c selection; edge = model q - mid on the side bought)
| claimed edge | bets | mean model q | mean mid | win rate | realised edge vs mid (SE) | share of claim realised | ROI at mid, no fee | ROI executable (h=2c, fee) |
|---|---|---|---|---|---|---|---|---|
| 0.00-0.05 | 2789 | 0.578 | 0.537 | 0.546 | +0.009 (0.010) | +21% | +1.6% [-2.0, +5.1] | -4.1% [-7.5, -0.6] |
| 0.05-0.10 | 5556 | 0.557 | 0.485 | 0.498 | +0.013 (0.011) | +18% | +2.6% [-1.4, +6.8] | -4.6% [-8.7, -0.6] |
| 0.10-0.15 | 2301 | 0.576 | 0.455 | 0.442 | -0.012 (0.015) | -10% | -2.7% [-8.7, +3.7] | -10.3% [-15.9, -4.4] |
| 0.15-0.25 | 1266 | 0.630 | 0.444 | 0.487 | +0.043 (0.026) | +23% | +9.6% [-2.3, +20.6] | +0.8% [-10.4, +10.8] |
| 0.25-1.00 | 232 | 0.736 | 0.425 | 0.461 | +0.036 (0.051) | +12% | +8.6% [-14.7, +31.0] | -0.6% [-21.3, +20.2] |

## 2d. By stat and by side (h = 2c)
| cut | bets | win rate | mean mid | ROI at mid, no fee | ROI executable |
|---|---|---|---|---|---|
| pass_yd | 891 | 0.477 | 0.450 | +6.1% [-6.5, +19.3] | -1.8% [-13.5, +10.1] |
| rec | 2855 | 0.494 | 0.473 | +4.5% [-1.0, +9.9] | -3.0% [-8.3, +1.9] |
| rec_yd | 4745 | 0.539 | 0.516 | +4.4% [-1.1, +9.4] | -2.5% [-7.1, +2.2] |
| rush_yd | 2596 | 0.434 | 0.455 | -4.7% [-11.6, +2.9] | -11.8% [-18.5, -5.1] |
| td | 1057 | 0.482 | 0.489 | -1.5% [-5.6, +2.5] | -7.9% [-11.8, -4.4] |
| YES side | 5500 | 0.338 | 0.335 | +1.0% [-6.8, +8.8] | -8.9% [-15.8, -2.0] |
| NO side | 6644 | 0.627 | 0.611 | +2.7% [-1.1, +6.7] | -3.0% [-6.8, +0.8] |
| price < 20c | 2121 | 0.107 | 0.102 | +5.5% [-15.6, +28.6] | -19.0% [-35.1, -1.5] |
| price 20-80c | 7680 | 0.488 | 0.472 | +3.3% [-1.6, +8.2] | -4.7% [-9.0, -0.3] |
| price > 80c | 2343 | 0.878 | 0.878 | -0.1% [-2.6, +2.0] | -3.5% [-5.9, -1.3] |
| weeks 7-11 only | 10527 | 0.507 | 0.487 | +4.0% [-0.0, +7.8] | -3.2% [-6.9, +0.7] |

## 2e. Fee and spread as a share of stake (h = 2c bets): mean fee 1.67c on a mean price of 50.5c = 3.3% of stake; a 2c half-spread = 4.0% of stake

## 3. Rushing yards by Sleeper line and threshold, weeks 6-11 2026 (hit / model / Kalshi mid, n; rows are rungs Kalshi listed)
| pos | Sleeper line | k band | n | player-games | hit | model | Kalshi | model gap (SE) | Kalshi gap (SE) |
|---|---|---|---|---|---|---|---|---|---|
| RB | 0-20 | 1-10 | 54 | 52 | 0.593 | 0.589 | 0.531 | +0.004 (0.073) | +0.062 (0.069) |
| RB | 0-20 | 11-20 | 91 | 49 | 0.451 | 0.315 | 0.368 | +0.135 (0.076) | +0.082 (0.071) |
| RB | 0-20 | 21-40 | 116 | 48 | 0.224 | 0.095 | 0.188 | +0.129 (0.056) | +0.036 (0.056) |
| RB | 0-20 | 41-400 | 36 | 22 | 0.056 | 0.008 | 0.078 | +0.047 (0.041) | -0.023 (0.042) |
| RB | 20-30 | 1-10 | 50 | 50 | 0.620 | 0.853 | 0.670 | -0.233 (0.070) | -0.050 (0.071) |
| RB | 20-30 | 11-20 | 108 | 56 | 0.509 | 0.640 | 0.512 | -0.131 (0.063) | -0.003 (0.062) |
| RB | 20-30 | 21-40 | 146 | 54 | 0.288 | 0.316 | 0.299 | -0.028 (0.056) | -0.011 (0.054) |
| RB | 20-30 | 41-400 | 109 | 47 | 0.193 | 0.055 | 0.097 | +0.137 (0.060) | +0.096 (0.057) |
| RB | 30-50 | 1-10 | 31 | 31 | 0.806 | 0.927 | 0.786 | -0.120 (0.066) | +0.020 (0.068) |
| RB | 30-50 | 11-20 | 115 | 65 | 0.739 | 0.798 | 0.708 | -0.059 (0.049) | +0.032 (0.050) |
| RB | 30-50 | 21-40 | 225 | 78 | 0.529 | 0.539 | 0.513 | -0.010 (0.053) | +0.016 (0.051) |
| RB | 30-50 | 41-400 | 318 | 75 | 0.176 | 0.150 | 0.178 | +0.026 (0.036) | -0.002 (0.034) |
| RB | 50-200 | 1-10 | insufficient data (n=2) | | | | | | |
| RB | 50-200 | 11-20 | insufficient data (n=15) | | | | | | |
| RB | 50-200 | 21-40 | 180 | 104 | 0.689 | 0.748 | 0.761 | -0.059 (0.042) | -0.072 (0.041) |
| RB | 50-200 | 41-400 | 1161 | 129 | 0.294 | 0.321 | 0.313 | -0.027 (0.025) | -0.020 (0.024) |
| QB | 0-10 | 1-10 | 26 | 26 | 0.192 | 0.256 | 0.227 | -0.064 (0.083) | -0.035 (0.080) |
| QB | 0-10 | 11-20 | 45 | 27 | 0.111 | 0.086 | 0.124 | +0.025 (0.062) | -0.013 (0.063) |
| QB | 0-10 | 21-40 | insufficient data (n=12) | | | | | | |
| QB | 0-10 | 41-400 | insufficient data (n=0) | | | | | | |
| QB | 10-20 | 1-10 | 75 | 71 | 0.453 | 0.618 | 0.468 | -0.165 (0.061) | -0.014 (0.061) |
| QB | 10-20 | 11-20 | 140 | 75 | 0.321 | 0.343 | 0.318 | -0.022 (0.053) | +0.004 (0.052) |
| QB | 10-20 | 21-40 | 135 | 64 | 0.170 | 0.122 | 0.175 | +0.048 (0.043) | -0.005 (0.044) |
| QB | 10-20 | 41-400 | 25 | 14 | 0.080 | 0.015 | 0.110 | +0.065 (0.078) | -0.030 (0.078) |
| QB | 20-30 | 1-10 | 35 | 35 | 0.543 | 0.794 | 0.669 | -0.251 (0.087) | -0.126 (0.080) |
| QB | 20-30 | 11-20 | 68 | 35 | 0.397 | 0.580 | 0.494 | -0.182 (0.087) | -0.097 (0.078) |
| QB | 20-30 | 21-40 | 96 | 34 | 0.177 | 0.285 | 0.252 | -0.108 (0.059) | -0.075 (0.055) |
| QB | 20-30 | 41-400 | 39 | 23 | 0.026 | 0.067 | 0.078 | -0.041 (0.028) | -0.053 (0.026) |
| QB | 30-200 | 1-10 | insufficient data (n=14) | | | | | | |
| QB | 30-200 | 11-20 | 44 | 22 | 0.477 | 0.763 | 0.717 | -0.286 (0.100) | -0.240 (0.101) |
| QB | 30-200 | 21-40 | 66 | 22 | 0.197 | 0.509 | 0.464 | -0.312 (0.068) | -0.267 (0.065) |
| QB | 30-200 | 41-400 | 75 | 22 | 0.013 | 0.170 | 0.131 | -0.157 (0.016) | -0.118 (0.012) |

Mean rushing yards vs Sleeper line (one row per player-game with a Kalshi rushing ladder):
- RB line 0-20: n=53 Sleeper 13.7, Kalshi implied mean 17.9, actual 17.7 (SD 17.2; SE 2.4); zero-or-less 0.151
- RB line 20-30: n=56 Sleeper 25.4, Kalshi implied mean 25.3, actual 29.9 (SD 34.7; SE 4.6); zero-or-less 0.071
- RB line 30-50: n=78 Sleeper 38.9, Kalshi implied mean 40.8, actual 38.0 (SD 26.4; SE 3.0); zero-or-less 0.038
- RB line 50-200: n=130 Sleeper 72.2, Kalshi implied mean 73.6, actual 67.1 (SD 36.6; SE 3.2); zero-or-less 0.000
- QB line 0-20: n=103 Sleeper 12.5, Kalshi implied mean 13.9, actual 12.0 (SD 14.9; SE 1.5); zero-or-less 0.223
- QB line 20-30: n=35 Sleeper 23.8, Kalshi implied mean 23.3, actual 15.0 (SD 13.0; SE 2.2); zero-or-less 0.114
- QB line 30-50: n=22 Sleeper 37.7, Kalshi implied mean 34.6, actual 17.9 (SD 14.7; SE 3.1); zero-or-less 0.091
- QB line 50-200: insufficient data (n=0)

## 4. Forward chaining by week on the rebuilt rows (fit on all earlier weeks, score the next; test weeks 8-11, fit starts with weeks 6-7). Ladder-weighted like mx_record; diff vs the comparator with game-clustered SE. Negative = better.
| stat | test n | games | engine LL | Platt(engine) - engine (SE) | Kalshi - engine (SE) | blend - Kalshi (SE) | mean blend weight on engine | Platt(Kalshi) - Kalshi (SE) |
|---|---|---|---|---|---|---|---|---|
| all (per-stat fits) | 14452 | 56 | 0.4658 | -0.0030 (0.0017) | -0.0174 (0.0045) | +0.0011 (0.0009) | +0.08 | +0.0008 (0.0006) |
| pass_yd | 974 | 56 | 0.5032 | +0.0114 (0.0063) | -0.0090 (0.0129) | +0.0184 (0.0082) | +0.44 | +0.0093 (0.0054) |
| rec | 3253 | 56 | 0.5422 | -0.0014 (0.0031) | -0.0177 (0.0080) | +0.0008 (0.0012) | +0.09 | +0.0001 (0.0009) |
| rec_yd | 5778 | 56 | 0.5443 | -0.0032 (0.0033) | -0.0106 (0.0068) | +0.0001 (0.0013) | +0.10 | +0.0007 (0.0011) |
| rush_yd | 2544 | 56 | 0.5566 | -0.0168 (0.0070) | -0.0391 (0.0138) | +0.0007 (0.0021) | -0.07 | -0.0008 (0.0015) |
| td | 1903 | 56 | 0.3320 | -0.0012 (0.0009) | -0.0158 (0.0044) | +0.0004 (0.0018) | -0.17 | +0.0009 (0.0015) |

## 3a. 2023-2025 revised rows (rush_cv/rows_2023_2025.csv.gz): hit minus model at fixed thresholds, game-clustered SE
| pos | Sleeper line | seasons | n | k | hit | model (single CV) | gap (SE) | model (line CV) | gap (SE) |
|---|---|---|---|---|---|---|---|---|---|
| RB | 20-30 | 2023+2024 | 378 | 10+ | 0.648 | 0.849 | -0.201 (0.025) | 0.706 | -0.058 (0.025) |
| RB | 20-30 | 2023+2024 | 378 | 15+ | 0.569 | 0.703 | -0.134 (0.026) | 0.578 | -0.009 (0.026) |
| RB | 20-30 | 2023+2024 | 378 | 25+ | 0.378 | 0.421 | -0.043 (0.025) | 0.382 | -0.004 (0.026) |
| RB | 20-30 | 2023+2024 | 378 | 40+ | 0.233 | 0.162 | +0.071 (0.021) | 0.201 | +0.032 (0.021) |
| RB | 20-30 | 2025 | 207 | 10+ | 0.676 | 0.847 | -0.171 (0.032) | 0.703 | -0.026 (0.032) |
| RB | 20-30 | 2025 | 207 | 15+ | 0.570 | 0.700 | -0.130 (0.033) | 0.575 | -0.005 (0.033) |
| RB | 20-30 | 2025 | 207 | 25+ | 0.353 | 0.418 | -0.065 (0.032) | 0.379 | -0.027 (0.032) |
| RB | 20-30 | 2025 | 207 | 40+ | 0.193 | 0.160 | +0.033 (0.027) | 0.199 | -0.006 (0.027) |
| RB | 10-20 | 2023+2024 | 520 | 10+ | 0.492 | 0.637 | -0.145 (0.022) | 0.464 | +0.028 (0.022) |
| RB | 10-20 | 2023+2024 | 520 | 15+ | 0.383 | 0.421 | -0.038 (0.021) | 0.347 | +0.036 (0.021) |
| RB | 10-20 | 2023+2024 | 520 | 25+ | 0.242 | 0.156 | +0.087 (0.018) | 0.199 | +0.043 (0.018) |
| RB | 10-20 | 2023+2024 | 520 | 40+ | 0.112 | 0.030 | +0.082 (0.014) | 0.090 | +0.021 (0.014) |
| RB | 10-20 | 2025 | 211 | 10+ | 0.483 | 0.642 | -0.159 (0.033) | 0.468 | +0.016 (0.033) |
| RB | 10-20 | 2025 | 211 | 15+ | 0.365 | 0.428 | -0.063 (0.032) | 0.350 | +0.015 (0.032) |
| RB | 10-20 | 2025 | 211 | 25+ | 0.223 | 0.161 | +0.062 (0.028) | 0.202 | +0.020 (0.028) |
| RB | 10-20 | 2025 | 211 | 40+ | 0.095 | 0.031 | +0.064 (0.020) | 0.092 | +0.003 (0.020) |
| QB | 10-20 | 2023+2024 | 432 | 10+ | 0.551 | 0.570 | -0.019 (0.024) | 0.465 | +0.086 (0.024) |
| QB | 10-20 | 2023+2024 | 432 | 15+ | 0.400 | 0.370 | +0.031 (0.024) | 0.334 | +0.066 (0.024) |
| QB | 10-20 | 2023+2024 | 432 | 25+ | 0.257 | 0.139 | +0.118 (0.021) | 0.176 | +0.081 (0.021) |
| QB | 10-20 | 2023+2024 | 432 | 40+ | 0.102 | 0.029 | +0.073 (0.014) | 0.068 | +0.034 (0.014) |
| QB | 10-20 | 2025 | 204 | 10+ | 0.475 | 0.590 | -0.115 (0.033) | 0.486 | -0.010 (0.033) |
| QB | 10-20 | 2025 | 204 | 15+ | 0.358 | 0.398 | -0.040 (0.032) | 0.356 | +0.002 (0.033) |
| QB | 10-20 | 2025 | 204 | 25+ | 0.201 | 0.162 | +0.039 (0.029) | 0.191 | +0.010 (0.029) |
| QB | 10-20 | 2025 | 204 | 40+ | 0.054 | 0.038 | +0.016 (0.017) | 0.076 | -0.022 (0.017) |
| QB | 20-30 | 2023+2024 | 121 | 10+ | 0.769 | 0.789 | -0.021 (0.038) | 0.735 | +0.034 (0.038) |
| QB | 20-30 | 2023+2024 | 121 | 25+ | 0.438 | 0.381 | +0.057 (0.045) | 0.372 | +0.066 (0.045) |
| QB | 20-30 | 2023+2024 | 121 | 40+ | 0.165 | 0.155 | +0.011 (0.034) | 0.172 | -0.006 (0.034) |
| QB | 20-30 | 2025 | 117 | 10+ | 0.701 | 0.806 | -0.106 (0.041) | 0.765 | -0.064 (0.041) |
| QB | 20-30 | 2025 | 117 | 25+ | 0.385 | 0.418 | -0.033 (0.046) | 0.407 | -0.023 (0.046) |
| QB | 20-30 | 2025 | 117 | 40+ | 0.231 | 0.183 | +0.048 (0.040) | 0.195 | +0.036 (0.040) |

## 3b. The registered line-dependent CV (fitted 2023-24, never shipped) on 2026 Kalshi-listed rushing rungs. DESCRIPTIVE ONLY: the registered weeks 8-10 judgement was skipped when the 2025 gate failed; this read is not that judgement and decides nothing.
| rows | n | games | single CV LL | line CV LL | Kalshi LL | line - single (SE) | Kalshi - line (SE) |
|---|---|---|---|---|---|---|---|
| RB+QB wk6-11 | 3652 | 82 | 0.5571 | 0.5397 | 0.5057 | -0.0173 (0.0060) | -0.0340 (0.0085) |
| RB+QB wk8-11 | 2507 | 56 | 0.5540 | 0.5421 | 0.5080 | -0.0119 (0.0044) | -0.0341 (0.0109) |
| RB wk6-11 | 2757 | 82 | 0.5594 | 0.5388 | 0.5076 | -0.0206 (0.0077) | -0.0312 (0.0097) |
| QB wk6-11 | 895 | 82 | 0.5499 | 0.5425 | 0.4998 | -0.0074 (0.0059) | -0.0427 (0.0181) |
| RB, line < 30 | 710 | 70 | 0.6776 | 0.6094 | 0.5774 | -0.0682 (0.0277) | -0.0319 (0.0260) |
| RB, line >= 30 | 2047 | 82 | 0.5184 | 0.5143 | 0.4834 | -0.0040 (0.0030) | -0.0309 (0.0088) |

## 1e. Live Matchups record (aggregates only): calibration slope from the ten published bins. APPROXIMATE: a logistic fit to bin means, SE = binomial SE x sqrt(n / n_eff) (ladder design effect), not a game-clustered SE.
| sport | stat | graded | games | dates | slope b | approx SE | intercept a | top bins (p>=0.8): p / hit / n | bottom bin (p<0.1): p / hit / n |
|---|---|---|---|---|---|---|---|---|---|
| nfl | * | 18255 | 57 | 13 | 0.882 | 0.037 | -0.140 | 0.856 / 0.799 / 998 | 0.047 / 0.063 / 5101 |
| nfl | pass_yd | 1044 | 57 | 13 | 0.951 | 0.185 | -0.228 | 0.870 / 0.814 / 167 | 0.059 / 0.045 / 179 |
| nfl | rec | 4723 | 57 | 13 | 0.986 | 0.080 | -0.046 | 0.854 / 0.838 / 216 | 0.047 / 0.053 / 1480 |
| nfl | rec_yd | 7182 | 57 | 13 | 0.870 | 0.074 | -0.115 | 0.849 / 0.818 / 429 | 0.051 / 0.076 / 1672 |
| nfl | rush_yd | 3030 | 57 | 13 | 0.745 | 0.099 | -0.264 | 0.861 / 0.694 / 186 | 0.048 / 0.076 / 726 |
| nfl | td | 2276 | 57 | 13 | 0.879 | 0.091 | -0.204 | nan / nan / 0 | 0.037 / 0.050 / 1044 |
| nba | * | 42599 | 197 | 26 | 0.989 | 0.020 | +0.027 | 0.880 / 0.884 / 4917 | 0.057 / 0.065 / 5728 |
| nba | ast | 7627 | 197 | 26 | 1.000 | 0.045 | +0.073 | 0.892 / 0.899 / 1072 | 0.051 / 0.061 / 1450 |
| nba | fg3m | 10366 | 197 | 26 | 0.965 | 0.045 | +0.044 | 0.862 / 0.856 / 958 | 0.054 / 0.060 / 2066 |
| nba | pra | 6045 | 197 | 26 | 0.903 | 0.059 | +0.010 | 0.835 / 0.830 / 206 | 0.076 / 0.271 / 59 |
| nba | pts | 8493 | 197 | 26 | 1.007 | 0.045 | +0.024 | 0.864 / 0.875 / 672 | 0.069 / 0.079 / 1040 |
| nba | reb | 10068 | 197 | 26 | 1.029 | 0.042 | -0.014 | 0.891 / 0.898 / 2009 | 0.060 / 0.058 / 1113 |

## Live record, pick ROI at the ask (aggregates; "shown" is n = 0 in every cell, so all picks are muted)

| sport | stat | close picks n | close ROI +/- SE | first-sighting picks n | first ROI +/- SE | high-probability picks: said / hit (close) |
|---|---|---|---|---|---|---|
| nfl | all | 12,011 | -1.7% +/- 1.8 | 2,253 | -5.4% +/- 3.1 | 0.887 / 0.847 (7,577) |
| nfl | pass_yd | 853 | -0.2% +/- 6.5 | 169 | +3.1% +/- 13.8 | 0.864 / 0.820 (378) |
| nfl | rec | 2,695 | +1.7% +/- 2.6 | 446 | -7.1% +/- 7.2 | 0.898 / 0.883 (1,901) |
| nfl | rec_yd | 4,722 | +0.6% +/- 2.5 | 611 | +2.0% +/- 5.3 | 0.872 / 0.833 (3,035) |
| nfl | rush_yd | 2,306 | -9.9% +/- 3.8 | 393 | -23.7% +/- 7.6 | 0.882 / 0.784 (1,209) |
| nfl | td | 1,435 | -3.8% +/- 1.5 | 634 | -2.9% +/- 2.3 | 0.927 / 0.901 (1,054) |
| nba | all | 19,687 | -4.9% +/- 1.1 | 4,246 | -3.7% +/- 2.0 | 0.859 / 0.828 (13,644) |
| nba | pts | 5,143 | -6.1% +/- 1.7 | 1,316 | -6.5% +/- 2.4 | 0.845 / 0.800 (2,446) |
| nba | pra | 2,872 | -5.2% +/- 2.5 | 612 | -0.1% +/- 4.4 | 0.795 / 0.734 (1,415) |
| nba | ast | 3,417 | -3.7% +/- 1.8 | 746 | +0.5% +/- 2.8 | 0.885 / 0.859 (2,762) |
| nba | fg3m | 4,187 | -5.7% +/- 2.1 | 780 | -9.0% +/- 4.1 | 0.868 / 0.833 (3,282) |
| nba | reb | 4,068 | -3.6% +/- 1.7 | 792 | -0.3% +/- 4.0 | 0.867 / 0.853 (3,739) |

The NBA's high-probability picks hit 3 points under what the engine said while the engine's ten bins are calibrated overall: the gap appears only on the lines that earned a label, which is selection, not miscalibration. Its SE is not in the aggregates (insufficient data to test).
