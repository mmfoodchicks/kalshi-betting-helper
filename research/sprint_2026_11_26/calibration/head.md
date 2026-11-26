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
4. **Let the live Platt gate read rushing yards; change nothing.** On the rebuilt rows a forward-chained Platt gains 0.0168 +/- 0.0070 in rushing yards (2.4 SE, 56 games) and under 1 SE elsewhere except passing yards, where it is WORSE (+0.0114 +/- 0.0063). The live gate (0.001 nats, 2 SE, 30 scored games) is the right criterion and will read rush_yd within about two weeks. If item 3 passes first, the Platt must be refitted on the new figure, not stacked.
5. **Export enough of the record to audit the NBA.** Add Kalshi's own ten bins (p, hit, n) and per-game sums to the record payload, or a row export to sim-history. Acceptance: this report's tables 1a-1d and 2a reproducible for the NBA from sim-history alone. Until then the NBA answers to questions 1 (market side), 2 (decomposition) and 4 (blend vs market) are "insufficient data (n = 0 rows)".
6. **Long-shot floor (low priority while muted).** The model under-prices its < 0.10 rungs in receiving yards (+0.040 +/- 0.010), rushing yards (+0.047 +/- 0.017) and receptions (+0.027 +/- 0.010); Kalshi is within 1.3 SE of calibrated on all three. A heavier right tail is the candidate; acceptance on weeks 13-18: log loss on rungs the shipped model prices under 0.10 improves by > 2 SE with no loss beyond 1 SE on the rest.
7. **QB rushing level: no action.** Insufficient data (57 player-games with lines of 20+, six weeks, the same few quarterbacks repeating, so a by-player cluster would be wider still). Re-read once after week 18.

Not recommended: any threshold on the size of the model-market disagreement as a "+EV" filter. Realised edge is 12-23% of claimed in every bucket and no bucket clears costs with an interval above zero.

---

# Tables

