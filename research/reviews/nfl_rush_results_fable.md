# Fable results review: is the rushing model's 2025 null real?

Reviewer: Fable 5.1, 2026-10-29 (01:30-04:00 UTC). The question: no group passed its 2025 gate in any of the 8
markets (`validate.json` e8df86cb, `check2026.json` b2583913, `coefs.json` 0684544d, stage "provisional", nothing
shipped). Is that real, or did a defect suppress a true effect?

**How I worked.** Outcomes are readable: the registration holds (`prereg.json` 907f0bed, block 92a99b8e, 35
decisions). I set `NFLR_MODEL_OUT` to a scratch folder holding a COPY of `prereg.json` (so `prereg_status` is true
and no write can land in `research/data/nfl_rush_model/`), `DEEP_CACHE_DIR` to a private copy of deep3, and called
the registered functions (`load`, `dead_features`, `parts_for`, `dispersion`, `fit_position`, `compare`,
`accept_2025`) directly on copies of the registered frames. No registered command was re-run, nothing was written
into the records folder, no git write. Scripts and logs: `<scratchpad>/review_rush_fable/results_{a,b,c}.py` and
`.log`.

**Sanity on the records first.** `dead_features` recomputed = the registration's; rows 869 / 272 games (RB), 456 /
262 (QB) on 2025, 0 rows dropped at any step, the all-rows figures within 0.002 of the played ones; dispersion fit
on 1,674 / 1,297 / 861 training rows (RB rushing / receiving / QB), the same counts as my universe tallies. The walk
reproduces: fit 2023-24 -> 2025 SHAPE on RB rushing yards d -0.0323 SE 0.0254 (validate.json -0.0323 / 0.0254), QB
rushing yards +0.0034 / 0.0245, RB receiving yards -0.0140 / 0.0088, QB attempts +0.0088 / 0.0055 -- to the digit.

---

## Verdict: **AGREE -- the null stands, and it is not a defect. But it is a statement about POWER, not about absence.**

Nothing in the machinery suppressed an effect: a planted effect large enough is accepted, a planted effect that
exists only in training is rejected, the features are populated on 2025 as on 2023-24, and the baseline is not
contaminated. What the plants also show is that the 2025 gate on this geometry can only detect effects two to three
times larger than any signal the training seasons carry, so "no group passed" is what the test would show whether or
not effects of realistic size exist. The owner should read the result as "not detectable at this sample", never as
"Sleeper's line cannot be beaten".

---

## Findings

### MAJOR (interpretation, not a defect): M1. The 2025 gate's power is low for effects of realistic size.

Power on the real geometry (item 1). I planted a multiplicative effect on the outcomes of EVERY season (2023, 2024,
2025), `y' = y x exp(b x z)`, `z` the standardised real feature, so the effect is one a real world would carry into
the fit and the test alike; then ran the group's own step through the registered functions (fit 2023-24, score 2025).

| market / group / feature | planted b per SD | fitted b (2023-24) | lambda | 2025 d | SE | z | gate |
|---|---|---|---|---|---|---|---|
| RB rushing yards / OVD / `ovd_run_epa` | 0.05 | 0.064 | 10,000 | +0.0007 | 0.0149 | +0.05 | reject |
| | 0.10 | 0.123 | 3,000 | -0.0146 | 0.0262 | -0.56 | reject |
| | 0.15 | 0.171 | 3,000 | -0.0561 | 0.0350 | -1.60 | reject |
| | **0.20** | 0.230 | 1,000 | -0.1163 | 0.0429 | **-2.71** | **accept** |
| | 0.25 | 0.280 | 1,000 | -0.1722 | 0.0494 | -3.48 | accept |
| RB carries / SPD / `spd_rb_car_pg` | 0.05 | 0.099 | 1 | -0.0024 | 0.0121 | -0.20 | reject |
| | 0.10 | 0.151 | 1 | -0.0357 | 0.0189 | -1.89 | reject |
| | **0.15** | 0.201 | 1 | -0.0813 | 0.0243 | **-3.35** | **accept** |
| | 0.20 | 0.254 | 1 | -0.1436 | 0.0265 | -5.42 | accept |
| QB rushing yards / SPD / `spd_qb_scryds_oe` | 0.10 | 0.171 | 1 | +0.0048 | 0.0412 | +0.12 | reject |
| | 0.20 | 0.276 | 1 | -0.0651 | 0.0560 | -1.16 | reject |
| training-only plants (effect in 2023-24, none in 2025) | 0.15 / 0.10 / 0.20 | | | +0.125 / +0.078 / +0.208 | | | reject (as they must) |

- The gate first accepts at about **b = 0.17-0.20 per SD on RB rushing yards** and **0.12-0.15 per SD on RB
  carries**; QB rushing yards does not accept even at 0.20 (104 fewer rows, a noisier market). At the median 2025
  line that is a +1 SD defense moving a 51-yard projection to about 62 yards, or a 12.1-carry projection to about
  13.8-14.8.
- The signal the fitting seasons actually carry is far smaller: the largest standardised coefficient of any group
  fit on 2023-24 is 0.079 (QB TD, `full_qb_gl`), then 0.062 / 0.054 / 0.042 (QB rushing yards SPD), 0.047 (RB
  carries SPD), 0.047 / 0.031 / 0.024 (`dec_margin`), 0.017 (RB rushing yards OVD). Effects of that size sit at
  z 0 to -1 on 2025 whatever their truth.
- The mechanism is the metric's variance, not the ridge: as the planted effect grows, the per-row log-loss
  differences grow too (SE 0.015 -> 0.049), so z rises only roughly with b^2. The gate (2 SE and a floor) is right
  to be strict; the sample is what it is: 869 RB rows / 272 games.
- Consequence for the prospective window: 56 games is a fifth of 2025's 272, so its SE is about 2.2x larger again
  (the rule's own `keep_power` says a true effect at the gate's edge is kept 46% of the time). A future ship of a
  realistic effect needs pooled seasons or a sharper statistic, not another single-season walk.

What this does NOT say: it does not say an effect exists. It says the registered test could not have seen one of
the size the data suggests, so the null carries little evidence either way. Report the minimum detectable effect
beside the null.

### MINOR

**m1. The compound's 2024 gain did not carry to 2025, and the machinery is not the reason.** Fit 2023 -> 2024
through the registered walk functions reproduces the traincheck: RB rushing yards SHAPE d -0.1204 (traincheck
-0.122), QB -0.1807 (-0.181). The same code on fit 2023-24 -> 2025 gives -0.032 and +0.003. Where it goes, on 2025's
869 RB rows (compound minus rung 1, nats a row): it WINS at the low thresholds (10+ -0.030, 15+ -0.016: rung 1 says
0.958 at 10+ against 0.908 hit, the compound 0.916, the first model's F3 problem fixed) and LOSES at 40-60 (+0.005 to
+0.007: the compound reads 0.566 at 40+ against 0.554 hit, rung 1 0.536) and on the big lines (60+ yards, 339 rows:
+0.075; lines under 30: -0.19; 30-60: -0.06). 2025's outcomes ran under Sleeper's line (nfl_trim: 0.958x; on 2026's
frozen rows -4.2 to -4.8 yards a row), and a narrower distribution centred on a biased mean pays more at the top
thresholds than the wider gamma. The level is OUT of the rule by decision; this is where its absence showed.
QB rushing yards: wins at 10+/15+ (0.703 -> 0.629 against 0.621 hit), loses at 40-60 (60+: 0.081 against 0.042
hit); net zero.

**m2. QB attempts SHAPE is WORSE (+0.0088 +/- 0.0055).** The four-part attempts compound reads 0.297 / 0.147 at 6+ /
8+ against 0.279 / 0.123 hit (rung 1 0.287 / 0.143): the summed components, each with its own alpha curve plus the
kneels, are more dispersed than 2025's QB attempts. Data, not a defect; the rule's gate did its job.

**m3. Training signal (item 3): fifteen of the 29 term groups carry no out-of-fold signal at all; the fourteen that
do lose it on 2025.**
- Fifteen groups chose lambda 1e6 and the leave-one-season-out deviance is WORSE at every smaller lambda (RB rushing
  yards SPD: +584 at lambda 1, +35 at 1e5; CFP +578; FULL +365; every RB receiving group; RB TD; QB OVD / DEC on
  both markets). Their coefficients are 0.000-0.003 and their 2025 d is 0 +/- 1e-5. That is the CV saying 2023 does
  not predict 2024 through these terms, not the ridge silencing a signal: with a planted 0.05/SD effect the same
  CV chose lambda 10,000 and recovered b 0.064.
- Fourteen groups found signal in training (lambda 1 to 100,000; in-sample d on 2023-24 from -0.002 to -0.025 nats a
  row): RB carries SPD (-0.0126) and DEC (-0.0127), RB rushing yards OVD / DEC, QB attempts SPD / CFP / FULL, QB
  rushing yards SPD (-0.0249) / CFP (-0.0123) / DEC / FULL, QB TD FULL (-0.0022). Every one read 0 or worse on 2025;
  two significantly worse (RB carries SPD +0.0080 +/- 0.0061; QB rushing yards SPD +0.0278 +/- 0.0195). So the
  training-season signal, where it exists, is small and did not carry -- the same picture the first model drew.

### Checks that passed (items 2 and 4)

- **Feature coverage, 2025 against 2023-24** (every fitted feature on each family's universe rows, played): 0
  flags on missing rate, zero rate, SD or mean shift; **0 rows with half their features missing** (no join failure)
  in either season set. The non-negative counts behave: `cfp_ol_out` zero on 61-68% of rows, `dec_room_out` on
  88-90% (the training mean fills a missing one on 0.2% of 2023-24 rows, none in 2025). The QB scramble splits and
  `cfp_contain` are blank or zero on 48.8% of 2023-24 rows -- the 2023 rows, disclosed in Q2 -- and populated on
  every 2025 row.
- **The baseline is not contaminated.** On 2026's frozen rows (the only rows holding both lines), Sleeper's
  post-week REVISED line is no closer to the outcome than the frozen pre-kickoff one on rushing: MAE revised vs frozen
  RB carries 3.83 vs 3.92, **RB rushing yards 23.43 vs 23.07 (revised worse)**, receptions 1.38 vs 1.40, receiving
  yards 13.60 vs 13.70, QB attempts 2.05 vs 2.01 (worse), QB rushing yards 12.59 vs 12.25 (worse); correlations
  with the outcome equal to two digits; 35-46% of lines change, by 0.1-0.4 carries or 0.7-2.0 yards. The first
  model's own `revision_effect` (test.json) says the same (carries -0.013 +/- 0.011 nats, rushing yards +0.021 +/-
  0.017 for the revised line). The 2025 "revised" baseline therefore did not beat the terms by knowing the answer.
- **The dead features, rows, games, dispersion counts and the all-rows figures** in `validate.json` are the
  registration's and mine.
- Not a defect but worth the owner's eye: on 2026's frozen rows the outcomes sit UNDER Sleeper's line on every
  rushing market (RB rushing yards -4.2 to -4.8 yards a row, carries -0.25 to -0.39, QB rushing yards -1.2 to -1.8).
  The rule fixed the level at Sleeper's by decision; the prospective window is judged at that level too.

---

## What I could not check

- Whether 2025's revised lines are contaminated IN 2025: no frozen 2025 captures exist, so 2026's rows are the proxy.
- The plants assume a multiplicative effect on the mean with Sleeper's shape otherwise intact; a real effect could be
  shaped differently (tails, counts), but the gate's variance would bind the same way.

## Bottom line for the owner

The registered procedure ran clean and the null is genuine: on 2025, nothing fitted on 2023-24 beat Sleeper's line
by the rule's standard, and the one shape that beat it on 2024 did not on 2025. The test's power, measured on its own
rows, says the standard could only have been met by an effect two to three times the largest one the data ever
showed. "No group passed its pre-registered test" is the right sentence for the board; "the market cannot be beaten"
is not. The prospective window, with no provisional group, has nothing to decide; a future attempt needs more
seasons pooled, or a statistic with less variance than a ladder's log loss, before another gate of this kind can say
anything about effects of realistic size.
