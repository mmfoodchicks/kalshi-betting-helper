# Fumble-return touchdowns: the deep engine's missing D/ST score (staged, not live)

2026-11-28. The owner saw T.J. Watt's fumble-return touchdown on 2026-11-27 and approved this fix, by option (c)
for the registrations. The fix is STAGED: production is unchanged until the deep-weight read is committed, and
then it flips together with the market-anchor fix. Files:

- the rule and fit: `research/fumble_td_fit.py` (rule committed first, 461abe0);
- the patch: `research/data/fumble_td_flip.patch`, `research/fumble_td_patch.py`;
- the D/ST evaluation: `research/fumble_td_dst.py`;
- the joint anchor check: `research/fumble_td_anchor_check.py`;
- the registration helper: `research/registered_pins.py` (procedure in `research/reports/registered_reads_runbook.md`);
- guards: `tests/guards_auto_fumble_td.py` (FT1-FT13).

## Summary

- **What is missing.**
  - Real regular seasons return 0.040 fumbles per team-game for a touchdown (2016-2025; 0.031 in 2021-25),
    8.6% of lost fumbles from scrimmage.
  - The engine plays none. `research/nfl_deep_fit.py` keeps those touchdowns in the lost-fumble RATE but drops
    them from the spot table, so every lost fumble hands the ball over at a spot.
- **The fix.** A lost fumble from scrimmage is returned for a touchdown with a probability fitted on 2016-2024
  (2,238 fumbles, 194 TDs). The probability depends on:
  - the kind of play: a strip-sack about twice a run's or a catch's;
  - the snap's 20-yard zone: a fumble snapped at the offense's own 1-19 runs 3-4x the midfield rate.
- **How it was chosen.** A rule written first picked the form by forward chaining over 2019-2024. The held-out
  check on 2025 and 2026 weeks 1-12 was read once:
  - better than a constant by 0.0176 nats a fumble (1.5 SE);
  - 24 touchdowns against 32.5 expected, z -1.6, inside the rule's 95%. The level is "confirmed" by the rule,
    but it sits low; see B.4.
- **How it scores.** As the pick-six does: `td_ret`, `td_def` (so DraftKings' points allowed leave its six out),
  then `_return_td`. With the switch off, the patched engine is the old engine draw for draw.
- **The D/ST tail on the 436 real games of 2025 and 2026 weeks 1-12.** Production undershoots the real tail;
  the patch moves every tail figure toward the real one:

  | | Production | Patched | Real |
  |---|---|---|---|
  | P(D/ST >= 15) | 6.39% | 7.35% | 8.6 +/- 0.9% |
  | P(D/ST >= 20) | 1.41% | 1.84% | 2.06 +/- 0.48% |
  | P(D/ST >= 25) | 0.25% | 0.38% | 0.57 +/- 0.26% |

  The mean moves away (6.17 -> 6.37 against the real 6.09 +/- 0.17). The share of team-games with a D/ST
  touchdown overshoots (9.5% -> 11.8% against 9.5 +/- 1.0%). Both excesses come from the engine's OWN pick-six
  rate: 0.077 a team-game, against 0.046 in this window and 0.070 over 2016-2025. That error predates this
  fix. Production's 9.5% matched the real 9.5% only because two errors cancelled. Reported, not changed (D).
- **Option (c).** Nine registrations pin the engine. Each now runs from its own registration commit, and the
  current guards check the pins there; the registrations themselves are untouched. Every read refuses the
  patched live tree (guard FT12).
- **Joint flip.** With both patches in, the anchor's 1.86 re-verifies at slope 0.9934 +/- 0.0065, within its
  own stated tolerance (1 +/- 0.03, SE <= 0.01). The win error is 0.196 against a cap of 0.267. 1.86 holds
  (E).

## A. The measurement

The engine's lost fumbles happen at three places in `nfl_deep_sim._snap`: after a sack, after a completion,
and on a run. The fit table builds the spot from non-touchdown rows only (`if code == P.FUMBLE_LOST and ...
not (fl & P.F_TD)`), while `fumble_lost` counts every lost fumble. The touchdowns therefore vanish: their
fumbles land at a spot like any other.

Real regular seasons, per team-game. Counts are the orchestrator's (`scratchpad/fumtd/rates.json`, nflverse);
2026 is weeks 1-12 under the coaching seal.

| | Pick-sixes | Fumble-return TDs (scrimmage) | Kick/punt return TDs | Lost fumbles | Fumble TD per lost fumble | Pick-six per INT |
|---|---|---|---|---|---|---|
| 2016-2025 | 0.070 | 0.040 | 0.027 | 0.466 | 8.6% | 8.9% |
| 2021-2025 | 0.066 | 0.031 | 0.024 | 0.447 | 7.0% | 8.8% |
| 2016-2024 (the fit) | 0.072 | 0.041 | 0.025 | 0.473 | 8.7% | 9.1% |
| 2025 + 2026 wk 1-12 (held out) | 0.046 | 0.028 | 0.028 | 0.430 | 6.4% | 6.5% |
| **Engine, production** (league units, 20,000 games) | **0.077** | **0** | **0.024** | **0.339** | — | **10.4%** |
| **Engine, patched** | 0.077 | **0.026** | 0.024 | 0.340 | 7.5% | 10.4% |

## B. The fix

### B.1 The rule

The rule is in `research/fumble_td_fit.py`'s docstring, committed in 461abe0 before any held-out season was
read. FT3 pins it byte for byte.

- **Rows:** lost fumbles from scrimmage. The outcome is the defense's return TD.
- **Kind:** sack, completion, or anything else (counted as a run).
- **Candidates:** four forms by two windows.
  - M0: a constant.
  - M1: kind.
  - M2: kind + a logit line in yards to go.
  - M3: kind + five 20-yard zones.
  - The windows are all prior seasons, or the last five.
- **Selection:** forward chaining over 2019-2024. The one-SE rule takes the simplest candidate within one
  paired SE of the best.
- **Held-out check**, read once:
  - (a) not worse than M0 by more than 1 SE, else M0 ships;
  - (b) the touchdown count inside the 95% interval of the prediction, else "level not confirmed". Nothing is
    refitted on held-out data.
- **Seen before the rule:** the held-out seasons' aggregate rate, in the orchestrator's count. Their split by
  kind and zone was not.
- **What informed the candidates:** a descriptive look at 2016-2024 by kind and zone.

### B.2 Selection

Forward-chained mean log loss per fumble, 1,501 fumbles over 2019-2024:

| | ALL | W5 |
|---|---|---|
| M0 | 0.27582 (+0.0156, SE 0.0050) | 0.27507 |
| M1 | 0.27106 (+0.0108, SE 0.0041) | 0.27066 |
| M2 | 0.26317 (+0.0030, SE 0.0022) | 0.26288 (+0.0027, SE 0.0020) |
| M3 | **0.26027** (+0.00005, SE 0.0007) | 0.26021 (best) |

M3/W5 was best. M3/ALL sits within one SE and has more data, so **M3/ALL** ships, fitted on 2016-2024. M2 lost
by more than one SE: the zone curve is convex, not a logit line. Midfield runs at 7.5%, the 81-99 zone at
24.3%.

The shipped probability of a touchdown given a lost fumble, at the snap's yards to go (the zone's
representative point):

| Kind | 1-20 | 21-40 | 41-60 | 61-80 | 81-99 |
|---|---|---|---|---|---|
| Sack | 5.5% | 10.5% | 11.8% | 14.8% | 36.5% |
| Completion | 1.9% | 3.8% | 4.3% | 5.5% | 16.1% |
| Run | 2.5% | 4.8% | 5.4% | 6.9% | 19.8% |

The logit is -2.0147 + kind (completion -1.0966, run -0.8421) + zone (-0.8230, -0.1237, 0, +0.2608, +1.4594).
FT4 checks `nfl_deep_sim._fum_td` against `fumble_td_fit.predict` for every kind and every yards-to-go value from
1 to 99. It pins the definition (zones, base levels), not only the numbers.

### B.3 Held-out (2025 + 2026 weeks 1-12, read once from 461abe0)

375 fumbles, 24 touchdowns.

- **(a) PASS.** Log loss 0.2238 against M0's 0.2414: -0.0176, SE 0.0115.
- **(b) PASS.** 32.5 expected, SD 5.3, z -1.60.

By kind, sacks returned 16 of 119 (13.4%, the fit 13.6%). Runs returned 2 of 143 (1.4%, the fit 6.3%), the
season's largest shortfall.

### B.4 Is the level drifting?

The observed count is 74% of the prediction. The long run declines: 8.7% in 2016-2024, 7.0% in 2021-25, 6.4%
held out. The rule forbids refitting on held-out data, and (b) passed, so the 2016-2024 level ships.

A recency-weighted refit is a change for a new rule on fresh games. The rule's W5 window was tried and did not
beat ALL in the forward chain.

The engine's fumble-TD rate per team-game (0.026) sits below the long-run 0.040 anyway. The engine loses fewer
fumbles than the real game (0.34 a team-game, against 0.43-0.47), so the per-game rate lands near the
recent real one (0.028 in the window, 0.031 in 2021-25).

### B.5 In the engine

- **The draw.** In each of the three lost-fumble branches, `if FUMBLE_TD and rng.random() < _fum_td(kind,
  ytg)` is drawn BEFORE the spot. A touchdown skips the spot draw.
- **The score.** The play is resolved before the safety check: `td_ret` and `td_def` for the defense, the log
  event (`six=1, to="fum"`), the clock, then `_return_td` (six, the try, the kickoff owed).
- **No other draw is added**, so every game without a lost fumble is unchanged (FT5).
- **Stamps.** `FUMBLE_TD` is a switch: off, the patched file is the read's engine draw for draw on 1,500
  games, the old bytes taken from git (FT5). `ENGINE_VERSION = 2` is the deep engine's own stamp.
- **The DFS engine.** `nfl_dfs_engine.SIM_MODEL_VERSION` 3 (the boards' simulator stamp) and `off_points`'
  docstring change. `off_points` already subtracts `6 x td_def`, so DraftKings' rule holds with no code
  change (FT7).
- **Measured on the engine.** Over 4,000 logged games the touchdown count matches the model's probabilities at
  the engine's own fumble sites within 3 SE, and every one is followed by the defense's score (FT6). Every
  point is still a touchdown, field goal, try or safety (FT5). Guards C2b/C2c hold (C2c is now version-aware).
- **The viewer.** `nfl_viewer.tell` narrates a fumble six ("recovered by ... and returned for a TOUCHDOWN";
  the recoverer credited "dtd"). This is live now and harmless, because the old engine never logs one.
  The viewer's continuity guard accepts the fumble banner. `tests/guards_viewer.py` passes on the patched
  engine (36/36).

## C. The D/ST tail, before and after

The real side covers 436 games, 872 team-games: 2025 and 2026 weeks 1-12, under DraftKings' rule as production
scores it.

- **Points allowed** excludes the opponent defense's pick-sixes and fumble-return TDs (their tries count) and
  the safeties it scored.
- **Scoring:** sacks 1, INTs 2, fumble recoveries 2, every return TD 6, safeties 2, blocked punts and field
  goals 2.
- **"Engine scope"** drops what the engine cannot play: special-teams fumbles and returns. In this window there
  were no special-teams fumble TDs and no blocked or missed-kick return TDs.

The engine plays the same 436 games, 400 each, in three arms:

- **A**, production.
- **B**, the patched engine on A's profiles.
- **C**, the patched engine re-anchored on its own points, which is what production would play.

Inputs: league-average units whose offenses are anchored to each game's closing line (implied team totals; one
analytic step at the engine's points elasticity, landing 22.50 against a target of 22.49). The DFS worlds'
passing environment is on, and every arm uses the same seeds.

These are the closest reproducible inputs. The real games' own unit profiles are built from ESPN play-by-play
this session does not hold. Engine SEs are per simulated team-game; differences are paired.

| | Mean | P(>=10) | P(>=15) | P(>=20) | P(>=25) | TD game |
|---|---|---|---|---|---|---|
| Real, DraftKings | 6.09 +/- 0.17 | 24.3 +/- 1.3% | 8.6 +/- 0.9% | 2.06 +/- 0.48% | 0.57 +/- 0.26% | 9.5 +/- 1.0% |
| Real, engine scope | 5.97 +/- 0.17 | 23.4 +/- 1.3% | 8.5 +/- 0.9% | 1.83 +/- 0.45% | 0.46 +/- 0.23% | 9.5 +/- 1.0% |
| A production | 6.17 +/- 0.01 | 22.6% | 6.39% | 1.41% | 0.25% | 9.51% |
| B patched, same profiles | 6.33 | 23.9% | 7.29% | 1.81% | 0.37% | 11.77% |
| C patched, re-anchored | 6.37 | 24.2% | 7.35% | 1.84% | 0.38% | 11.80% |
| C - A (paired) | +0.201 +/- 0.007 | +1.51 +/- 0.07 pts | +0.96 +/- 0.05 | +0.43 +/- 0.02 | +0.13 +/- 0.01 | +2.29 +/- 0.05 |

### Reading the table

- **The tail.** The patch moves every tail figure toward the real one.
  - P(>=15): from 2.4 SE under the real figure to 1.4 SE under.
  - P(>=20): from 1.4 SE under to 0.5 SE under.
  - P(>=25): from 1.2 SE under to 0.7 SE under.
- **The mean.** It moves from inside one SE of the real mean (+0.08) to 1.6 SE over (+0.28).
- **The D/ST-touchdown share.** It moves from exactly the real share to 2.3 SE over.
- **Why.** The excess is NOT the new term. The engine's fumble-return TDs (0.026 a team-game) sit at the real
  window's 0.028. Its pick-sixes (0.077) sit at 1.7x the window's 0.046, and 1.1x the long run's 0.070.

Production's "right" share of D/ST-touchdown games was two errors cancelling: too many pick-sixes, no fumble
returns. The patch removes one of them. What remains is the pick-six rate, which this rule only reports (D).

## D. The other return rates (reported, not changed)

- **Pick-sixes.** The engine returns 10.4% of its interceptions for a touchdown. The real rate is 8.9% over
  2016-2025 and 8.8% over 2021-25, and 6.5% held out.
  - The cause is the `int_ret` table: research/nfl_deep_fit.py over ESPN 2023-2026, where 2023 alone ran 0.083
    pick-sixes a team-game.
  - The engine also throws fewer interceptions: 0.735 a team-game against 0.76-0.78.
  - Net pick-sixes per team-game: 0.077 against 0.070 / 0.066 / 0.046.
- **Lost fumbles.** The engine loses 0.34 a team-game against 0.43-0.47, 21-28% low. This is the fit's
  ESPN-coded rate.
- **Kick and punt return TDs.** 0.024 against 0.027 / 0.024 / 0.028, consistent.

Any of these is its own change. It moves every deep number, as the fourth-down refit registered in
`nfl_viewer.md` 6.2 does, so it would be judged on fresh games under a new rule. Nothing here sets them.

## E. The joint flip with the anchor

`research/fumble_td_anchor_check.py` re-runs the anchor's verification with both patches in:

- the same 24 seasons, margins at K 1.86, the committed "verify" seeds, n 300;
- the anchor-patched `apply_anchor` building the levels;
- the fumble-patched engine playing every game.

The tolerance is the anchor's own, stated in `deep_anchor_fix.md`: slope 1 +/- 0.03, SE <= 0.01, win error at
most the analytic reference + 0.10.

| | Slope | SE | Win error | Holds |
|---|---|---|---|---|
| Committed (anchor patch only, the read's engine) | 1.0002 | 0.0064 | 0.1963 | yes |
| Both patches | **0.9934** | 0.0065 | 0.1961 | **yes** (cap: 1 +/- 0.03, SE <= 0.01, error <= 0.1669 + 0.10) |
| Change, same seeds | -0.0068 | 0.0041 | | |

**1.86 holds within its stated tolerance.**

- The fumble-return TD compresses the anchored seasons slightly: -0.007, 1.7 SE. It adds return scores that do
  not follow team strength.
- Read literally, slope 1 would now sit near K 1.87 (1.86 / 0.9934). That is inside the original K* interval,
  1.8385-1.8865.
- The anchor's rule ships the 0.01-rounded K* only if the verification fails, and it did not, so nothing is
  re-measured. The figure is in `research/data/fumble_td_anchor_check.json`, pinned by FT13.

The seeds are shared with the committed run, but one game's 300 sims share one stream. Each stream decouples
after its first fumble-return TD, so the per-season "paired" change is close to unpaired. Its SD is about
0.044 a season, consistent with the committed per-season spread of 0.031 times sqrt(2).

## F. Contamination: every registered read

| Registered read | Touches the engine? | Before the flip | After the flip |
|---|---|---|---|
| **NFL deep weight, 100 games** (`nfl_deep_weight_read.py`; 87/100 on 11-27) | Grades the rows production's deep engine LOGGED | Rows from the read's engine | Gated: FT2 (and DA2) fail the patched engine until `research/data/nfl_deep_weight_read.json` is committed and triggered. Rows logged after the 100th game are outside the read. |
| **R1** (eaca95d), **R2** H1/H2 (dfc026e), **R3** (59b15fc), **R4** (c569318), **R5** 2028 (79f2bee), **R5-2027** (416ba98), **R6** (35809d1), **C4** (33c49ab), **coaching DFS** (33c49ab) | Pin `nfl_deep_sim.py` (DFS also `nfl_dfs_engine.py`). Their control arms ARE production's engine. | Unaffected | Run at their registration commits (runbook), where the control arm is the read's engine. The live tree's reads refuse (FT12). Live-tree DEVELOPMENT of a coaching engine after the flip compares against a v2 control: state the engine version. |
| **NFL deep weight, prospective** (rule section 6: fresh games, 0.30, O'Brien-Fleming looks) | Grades the live deep engine | Not started | Start its window AT the flip, so one engine version is graded. Rows carry the version through `simulator` / `ENGINE_VERSION`. |
| **cl_field2** (3d9194f; Millionaires 11-29 and 12-06) | Its boards' worlds are the DFS engine's | 11-29 is a v1 board | A flip between them makes 12-06 a v2 board. Same as the anchor: flip after the 12-06 lock, or record that the two contests ran different engines. |
| **DFS eligibility default** (`dfs_eligibility/prereg.json`, 12 showdowns) | Both arms share each board's worlds | — | The paired comparison is unaffected within a slate. Slates on either side of the flip differ in engine; boards stamp it. |
| **S7 prospective, sd_dup** | Judge the FIELD on real contests | — | The field's ownership comes from the board's worlds, so a slate after the flip is a v2 board. Note it per contest. |
| **NFL market anchor 8.3** (`game_xref.md`, weeks 9-18) | The raw DRIVE sim only (`p_home_raw`) | Unaffected | Unaffected |
| **C3-N** (analytic 1.842 vs the drive engine) | Not the deep engine | Unaffected | Unaffected |
| **Accuracy 13.2** (B1, B4/H2, C1, N1, N2, G2, tennis) | NBA, NHL, college, props, golf, tennis | Unaffected | Unaffected |
| **nfl_dfs_engine 2024/2025/att tests** | Completed reads | — | A reproduction runs at its own commit (the harness refuses a dirty tree for 2025+). |
| **Rushing model** (read 10-29, nothing passed) | Not the engine | — | — |
| **Week-board distributions** (`nfl_wb_dist/prereg.json`, read 2027-01-13) | The week board (`nfl_dfs_sim.simulate_game`, continuous) and its candidates, not the deep engine | Unaffected | Unaffected |
| **Defensive coaching 2028** (`prereg_def_2028.json`) | nflverse tendencies; pins only its own research files | Unaffected | Unaffected |

## G. Option (c) and the guard changes

The procedure, the commit table and the guard changes are in `research/reports/registered_reads_runbook.md`.
In short:

- `research/registered_pins.py` checks every registration's pins at the commit that added it, in a clone
  holding the whole history (CI now fetches it).
- Live, a pinned file must keep its bytes, with two named exceptions: a staged flip's bytes behind the
  deep-weight read's gate, and the current coaching guard files.
- Every guard that used to pin a live file still fails when the registered version is lost or altered. FT10
  exercises each failure: a missing commit, the wrong commit, a frozen file moved, the patched engine before
  the gate, any other bytes after it.
- FT12 is the read scripts' own refusal, run against a patched copy of each registration's tree.

## H. The flip checklist (one commit, with the anchor's)

1. `research/data/nfl_deep_weight_read.json` is committed (the read triggered at its 100th game). Nothing
   below happens before that.
2. **The owner's timing call.** The cl_field2 12-06 Millionaire argues for after its lock; the deep-weight
   prospective test's window starts at the flip.
3. Apply both patches: `git apply research/data/deep_anchor_flip.patch research/data/fumble_td_flip.patch`.
   - No pin edits are needed: the coaching guards, DA5 and FT accept exactly the flipped bytes once step 1
     holds.
   - The anchor report's older plan ("move the four pins") is superseded by `registered_pins.live_ok`. Moving
     them is still valid.
4. In the same commit, `ENGINE_VERSION` 2 and `ANCHOR_VERSION` 2 land together. That is one cache
   invalidation:
   - Both `nfl_deep_sim.py` and `nfl_deep_data.py` are in `pool_cache._CODE_FILES`, so every worlds key
     changes and a resumable build's frozen inputs hit `PinMiss` and restart fresh (FT8, DA7).
   - `nfl_game_sim._deep_profiles` refuses v1 nightly payloads.
   - The deep season payload stamps `engine_version` beside `anchor_version`. The stamp is live now and
     reads 1 until the flip.
   - The DFS boards stamp `SIM_MODEL_VERSION` 3.
5. Run the suite. Every guard must pass. Then flip the "after the flip" column of F into CLAUDE.md, and record
   that the 2026 season's deep figures before and after the flip are different engines.

## I. Checks run for this change

- **The rule and its reads:** the rule commit (461abe0), the fit (`research/data/fumble_td_fit.json`), and the
  held-out read, once.
- **The unpatched tree:** **[I-UNPATCHED]**.
- **A scratch tree with both patches applied:** **[I-PATCHED]**.
