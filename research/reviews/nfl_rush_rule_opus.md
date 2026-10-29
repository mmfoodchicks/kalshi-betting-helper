# Opus review: the RB / QB rushing model's pre-registered rule, before the hash

Reviewer: a fresh Opus instance. I did not build this work, did not review the data layer, and have no stake in it.
2026-10-24, about 01:29-02:00 UTC. Brief: `<scratchpad>/review_rush_rule_brief.md` and `<scratchpad>/rush_brief_common.md`.
I did not read the Sonnet rule review (`research/reviews/nfl_rush_rule_sonnet.md`), which appeared while I worked.

**What I reviewed.** I froze a copy of the tree at 01:35 UTC (`W/tree`, `W` = `<scratchpad>/review_rush_rule_opus/`). Its hashes
are in `W/sha_start.txt`:
- `nfl_rush.py` 771eae73, `research/nfl_rush_model_frame.py` 3413f395, `research/nfl_rush_model_fit.py` 37d2d9d0,
  `tests/guards_nfl_rush_model.py` 6e410e8c;
- the round-3 data layer 247cc625 / 1bddcab7 / 28bdea90;
- `nfl_matchup.py` 63365400, `research/nfl_matchup_frame.py` 8e41776a, `research/nfl_matchup_fit.py` 0802b123,
  `kalshi_nfl.py` fdb75941, `nfl_props_log.py` 9e238d5c.

The rule block (`RUSH-PREREG-START` to `-END`, 363 lines) hashed `f4c9be07…` at the start and at the end. The report changed
only outside the block.

**The tree moved while I worked.** That is a finding in itself (N1). Each move:
- `nfl_rush.py` went from 8b13ed6b (my first read) to 771eae73 (the PATH_SOURCE fix), then fc773ec8 / 95264fa7 at 01:53.
  The last two add about 400 lines of board code (`plan`, `multipliers`, `TARGETS`); I read them but did not review them fully.
- `research/nfl_rush_model_frame.py` went from 4941b7c8 to 3413f395 (the `aux_complete` refusal).
- The frames were rebuilt at 01:33-01:39 on the round-3 layer.
- `nfl_matchup.py` and `kalshi_nfl.py` changed at 01:42, for live-board work. A dry run of `register` against the live tree
  at 01:53 lists BOTH new frames as stale (`nfl_rush.py`, `nfl_matchup.py`).

**Rules kept.**
- I made no git writes and wrote no repo file but this one.
- Python ran with `VIGIL_NO_BG=1`.
- I read no 2025 or 2026 outcome:
  - every outcome I touched came through `research.nfl_rush_model_fit.read_frame(..., (2023, 2024), outcomes=True)`, which
    refuses 2025 and 2026;
  - the dataset reads were the season and week columns only;
  - the props-log reads were projections and timestamps.

---

## 1. Verdict: **AGREE AFTER FIXES**. It is not hashable as it stands.

**What is sound.**
- The distribution engine.
- The participation mask.
- The licence filter as far as the declared tags go.
- The refusals that exist.
- The honesty of Q8 about shape.

**What is wrong.** The rule cannot be hashed yet, for two reasons.
- **It describes decision machinery that does not exist (B1).** Q9 freezes the bytes "the runs execute", and the closing line
  says every command refuses when a file no longer hashes. Yet `research/nfl_rush_model_fit.py` has no validate, 2026-check,
  export or prospective command. Writing them after the hash breaks the hash; writing them before is what the rule needs.
- **Its decisive prospective test cannot be built by the registered code and data (B2).**

Eleven MAJOR findings follow. Several would let two honest implementers reach different decisions (M1, M3, M4, M5). One
is a licence leak the guard cannot see (M8), and one would ship a group that failed (M6).

**Required before the hash.** Each is a change to the rule text or the code:
1. **B1.** Write the commands the rule runs:
   - `validate` (2025);
   - `check2026` (the secondary walk);
   - `export` (provisional refit and `coefs.json`);
   - `prospective` (the window count, the walk, `prospective_lock.json`).

   Each refuses unless `prereg.json` and every registered file still hash. The 2025 / 2026 hold lifts through that check,
   never by editing `FORBIDDEN`. Plumb each with outcomes replaced by random 2023 outcomes, as the first model's R6 did.
   Guard the gate, the floor, the walk, the ties and the window. Send the code to the reviewers, then hash.
2. **B2.** Say how the prospective frame is built:
   - its row source, its outcome file, and which registered code builds it;
   - that the rebuilt dataset and store are hashed at read time.

   Fix the frozen reader:
   - the name-to-id map stops at week 7;
   - the capture glob drops weeks 10-11.

   Guard both with a week-10 fixture.
3. **M1.** Say whether rung 1 carries a fitted level. Either add an explicit level rung, as the first model's R did, or
   state "no intercept". Disclose that the 2025 level is already known (M9).
4. **M2.** Rungs 1-4 for the 2025 decision read dispersion fitted on 2023-2024, not the committed 2023-2025 `coefs.json`.
5. **M3.** Split DEC into a shape step and a terms step, each through the gate.
6. **M4.** State, for every term:
   - which component target it moves (QB designed or scramble, count or efficiency);
   - which outcome each component GLM is fitted on;
   - the order of the count and yards walks, and which count factor the yards compound carries;
   - what happens to count-type terms accepted on a yards market at DEC, and when DEC is accepted on only one of the pair;
   - how TD markets follow their components.
7. **M5.** For the 2026 walk and the prospective walk, state:
   - which coefficients they use;
   - "shipped so far" against leave-one-out;
   - whether a later group is refit when an earlier one drops.
8. **M6.** `ship_status` / `uses_compound` accept only `provisional` / `kept`. The export writes only groups with their
   pass records. Guard both.
9. **M7.** Score only player-games where he took a play, as Kalshi settles. Drop the rest, in every season.
10. **M8.** Take the PFR-snap game window out of `dec_trend`'s inputs, or tag it and swap the inputs. Guard it.
11. **M9.** Complete Q8.
12. **M10.** Score the longest rush, and give the derived markets a block-only check. Otherwise do not price them.
13. **M11.** Add the guards listed in §3.

The MINOR and NIT items should go in with the same edit, but none of them blocks the hash.

---

## 2. Findings

### BLOCKER

**B1. The rule's decision machinery is not written, so the rule cannot be frozen with the code that runs it.**

*Evidence.*
- `research/nfl_rush_model_fit.py` offers four commands: `traincheck`, `traincheck_frame`, `audit` and `register`
  (line 734).
- The report says so too: "The ladder and the validate / test commands come once the data layer is agreed."
- Q9 nevertheless stores "the bytes of every file the runs execute" at registration. Its last line says "Every command
  refuses when a file or this block no longer hashes to what was registered."
- No command checks a hash, and the hold is the literal `FORBIDDEN = (2025, 2026)`.

*Why it matters.*
- **The hash cannot cover the code that decides.** Adding the code after the hash changes `research/nfl_rush_model_fit.py`,
  and by Q9's own words every command then refuses. Not adding it leaves the decisions to code nobody has frozen or
  reviewed.
- **The machinery cannot be reviewed.** The gate, the floor, the SE's use, the walk, the ties, the 2026 walk, the window
  count, the extension, the export and the lock do not exist. They cannot be checked, and I could not mutate them (§3).
- **The hold has no lawful way to lift.** Editing `FORBIDDEN` changes a registered file.
- **There has been no plumbing run.** The first model ran validate and test on outcomes replaced by random 2023 outcomes
  before its hash (its R6). Here there is nothing to run.

*Fix.* Write the four commands. Each:
- verifies `prereg.json`, the rule block and every REG_CODE / REG_DATA file by sha256;
- lifts the 2025 / 2026 refusal ONLY through that verification;
- writes its result with the hashes it ran on.

Then:
- plumb them on permuted 2023 outcomes and disclose the run;
- guard the gate (strict `<` at -2 SE and at the floor), the walk, ties, the 40-game count and the extension, the
  lock-and-`--force` behaviour, and that the export writes only groups that passed;
- bring the code back to the reviewers, then hash.

**B2. The decisive prospective test cannot be built by the registered code and data.**

*Evidence.*
- **The rows.** They come only from `dataset_rows()`, the committed dataset (REG_DATA, hashed). It holds 2026 weeks 1-6
  only: RB 106/110/105/105/100/92 rows, QB 37/39/36/34/36/29, read from the season and week columns. Weeks 7-11 need a
  rebuilt dataset and a rebuilt 2026 store. Q9 says any changed registered file makes every command refuse. Nothing in the
  rule says how the prospective rows are built or hashed.
- **The name-to-id map stops at week 7.** The frozen reader is `research/nfl_matchup_frame.frozen_lines` (REG_CODE, the
  first model's). It maps props-log names to Sleeper ids through `proj_2026_{1..TEST_MAX_WEEK+1}.json`, which is weeks 1-7.
  Two synthetic probes, with no outcome read:
  - `W/probe/frozen_wk9.py`: a back already in week 6's file is found in week 9;
  - `W/probe/frozen_wk9b.py`: a back whose (name, team) first appears after week 7 is **silently dropped** ("week 9
    props-log row ... False").

  That loses every trade, promotion and injury replacement, which are exactly the low-line backups and QB2s the DEC rung is
  about.
- **The capture glob drops weeks 10-11.** `FROZEN_SOURCES` globs `proj_2026_[0-9].json`, a single digit. `proj_2026_10.json`
  and `proj_2026_11.json` are never read.

*Fix.*
- **Say how the prospective frame is built.** The rule names:
  - the row source (the frozen-projected players of the window, joined to the rebuilt dataset by Sleeper id);
  - the outcome source (`stats_2026_<w>.json`, fetched 48+ h after the game);
  - the code (the registered frame and dataset builders, unchanged);
  - the hash policy: the rebuilt dataset and 2026 store are recorded by sha256 at read time, and the code must equal the
    registered code.
- **Fix the reader.**
  - Build the name-to-id map from every week's file, or better, store the Sleeper id in the props log.
  - Widen the glob to `proj_2026_*.json`.
  - Guard both with a week-10 fixture and a traded-player fixture.

### MAJOR

**M1. The level is unspecified. A bare intercept passes the gate on its own, and the 2025 level is already known.**

*The ambiguity.*
- Q2: "the mean is Sleeper x exp(X b): ridge quasi-Poisson ... intercepts unpenalised."
- Rung 1 is "Sleeper's line, the shipped distributions".
- Nothing says whether rung 1 carries a fitted level. If it does not, the first matchup rung on each market (OVD, or SPD
  where OVD has no term) is judged with the level bundled in.

*Measured inside the fitting seasons* (`W/level.py`): an intercept fitted on 2023, scored on 2024 against raw Sleeper, on the
rung-1 families.

| market | exp(b0), 2023 fit | actual / Sleeper, 2024 | d | SE | floor | the gate |
|---|---|---|---|---|---|---|
| QB rushing yards | 1.041 | 1.109 | **-0.0422** | 0.0102 | -0.0030 | **accepted** |
| RB receiving yards | 1.019 | 1.085 | **-0.0070** | 0.0025 | -0.0025 | **accepted** |
| RB rushing yards | 1.013 | 1.023 | -0.0042 | 0.0025 | -0.0040 | rejected |
| RB carries, QB attempts, RB receptions, TDs | | | | | | rejected |

So an OVD group whose defense terms are worthless would pass on QB rushing yards for the level alone, and the board would
say "overall defense" moved it.

*The other direction is already known for 2025.*
- The first model's R rung (an intercept per position, fit 2023-2024) was WORSE on 2025: rushing yards +0.01106 +/- 0.00609,
  carries +0.00523 +/- 0.00210, receptions +0.01058, receiving yards +0.00795 (`nfl_matchup_model.md` §8).
- `nfl_trim.md`: 2025 rushing yards landed at 0.958x Sleeper and touchdowns at 0.957x.
- A 2023-24 intercept therefore starts every rung behind on 2025, and the writer of this rule could know it.

*Fix.* Choose one and write it into Q2 / Q3:
- an explicit level rung R before OVD, through the same gate, with later rungs carrying the intercept only if R was
  accepted; or
- no intercept in any GLM.

Disclose in Q8 that the 2025 level was seen (M9).

**M2. "The committed CVs" put 2025 inside the 2025 baseline.**

*Evidence.*
- Q2: "Rungs 1-4 use the shipped families (NegBin counts; point mass + gamma yards, with the committed CVs)".
- The committed `research/data/nfl_matchup_model/coefs.json` is fit on 2023-2025. Its `dispersion` reads:
  - RB carries alpha 0.1158 (n 4,807);
  - RB rushing yards CV 0.6298;
  - QB rushing yards CV 0.7045.
- The first model's own validation used 2023-2024: 0.1253 (n 3,186), 0.6372 and 0.7347 (`validate.json`).

*Why it matters.* The baseline every 2025 d is measured against would carry 2025 dispersion, while DEC's kernels and curves
are fit on 2023-2024 only. The comparison tilts toward the baseline, and the baseline has seen the season it is tested on.

*Fix.* For the 2025 decision, rungs 1-4 use dispersion fitted on 2023-2024. Name the source: `validate.json`'s, or a refit
on this rule's universes, said which. For the 2026 checks, use the 2023-2025 refit.

**M3. DEC bundles a large shape gain with five opportunity terms, so the terms ride free.**

*Evidence.*
- On 2024 (fit 2023) the compound alone beat the single-CV family by -0.122 +/- 0.023 nats a row on RB rushing yards and
  -0.181 +/- 0.051 on QB rushing yards (§1).
- The gate needs d < -2 SE and d < -0.004. The shape alone clears that by a wide margin if 2025 looks like 2024.
- DEC's opportunity terms then ship with it, untested: `dec_margin`, `dec_total`, `dec_neutral_run`, `dec_trend` and
  `dec_room_out`, or `dec_margin` and `dec_total` for QBs and RB receiving. Q4's "DEC with no matchup term, against rung 1"
  is reported and decides nothing.

*Fix.* Decide DEC in two steps:
1. **DEC-shape:** the compound, with the accepted terms re-expressed on components and no new term, against the accepted
   model.
2. **DEC-terms:** the opportunity terms on top of DEC-shape.

Both go through the same gate, and the second is decided only if the first passes. The decision count rises by up to six,
so update Q3b and its guard.

**M4. Two implementers would map terms to components differently.**

*What GROUPS says.* `GROUPS` labels each term only `count` / `eff` / `mean`.

*What the QB distribution takes.* `qb_parts` takes four separate multipliers: `designed`, `scramble`, `designed_eff` and
`scramble_eff`. The board code added at 01:53 fits one GLM per target, the QB ones being `designed`, `scramble`,
`designed_eff` and `scramble_eff`.

*What the rule never says.*
- **Which QB component each term moves.** For example, `ovd_pressure`, `ovd_blitz`, `dec_margin`, `dec_total` and
  `spd_qb_expl_oe`: does each move designed runs, scrambles, or both?
- **Which outcome each QB count GLM is fitted on.** `y_des`, `y_scr`, or total attempts?
- **Count and yards coupling.** The rule leaves open:
  - the order of the count and yards walks ("the count's accepted log factor" depends on it);
  - what happens at DEC to count-type terms accepted on a yards market but rejected on its count market (for example
    `spd_rb_car_pg`);
  - what the board shows when DEC is accepted on yards but not on carries. Two different "expected carries" would appear
    for the same back.
- **TD.** Q3 says the derived TD market uses "their components' accepted models" without saying how a carries factor
  reaches the rushing-TD mean. The first model wrote "rushing TDs move with the carries factor only".
- **A contradiction.** Q3 calls QB rushing TDs derived and deciding nothing "except ... goal-line terms ... in FULL", yet
  Q3b and `decisions()` give QB TD two decisions (SPD and FULL).

*Fix.* Add a component map per feature (`designed` / `scramble` / `both`; count or efficiency). Generate it from code into
Q3a, with the existing text-equals-code guard. State the walk order across the pair, the coupling, and the TD link.

**M5. The 2026 walk and the prospective walk are underspecified.**

*Q5 (the 2026 walk).* "keep a group iff its d is not worse (d <= 0)". It does not say:
- whether the coefficients are the 2023-2024 validation fit or the 2023-2025 refit (Q5 says the group "ships PROVISIONALLY,
  refit on 2023-2025", which suggests the latter);
- whether kernels, curves and dispersion are refit;
- whether the comparison is "shipped so far + group" against "shipped so far" (the first model's R4) or against the full
  accepted set;
- whether a later group is refit when an earlier one drops.

*Q6 (the prospective walk).* "each against the model without it". That reads two ways: leave-one-out from the full
provisional set, or sequential. The two can disagree.

*Fix.* State all of it:
- the 2026 walk uses the 2023-2025 refits (kernels, curves and dispersion included), shipped-so-far plus the group, with
  no refit when a group drops (or with one, said which);
- the prospective walk uses the exported `coefs.json` by its sha256, sequential and shipped-so-far.

**M6. The ship gate fails open, and no export enforces it.**

*Evidence.*
- `ship_status` returns "provisional" if any entry says so, else "shipped".
- `uses_compound` ignores `status` altogether.
- Measured on the reviewed code: a DEC entry with status `"reverted"`, `"failed"`, `"rejected_2025"` or `None` reads
  **"shipped"**, and its market is priced with the compound.
- There is no export code, so nothing ties a `shipped` entry to a 2025 accept, a 2026 keep or a prospective keep.
- Mutant S2 (the compound where only FULL shipped) survives.

*Fix.*
- `ship_status` / `uses_compound` accept exactly `provisional` and `kept`; anything else reads "0" and ledgers
  `NFLR-coefs`.
- `load_coefs` refuses a `shipped` entry without its decision records: the sha256 of the validate, check2026 and prospective
  results.
- The export writes only from those records.
- Guard all three.

**M7. Did-not-play rows score 0, but Kalshi settles them at the last fair price.**

*What the rule does.* Q1: "A frozen-projected player with no box line did not play and scores 0."

*What Kalshi does* (`rush_data_sources.md` §9, FOOTBALLENTITYSTAT): "A player must take one play to settle on stats; a player
who is active but never plays settles at the last fair price."

*So the test and the board differ.*
- The board's probability is conditional on his taking a play; the test scores him 0.
- A shape that puts more mass near 0 gains on rows the board never sells.
- On 2023-2024 revised lines the effect is small (`W/dnp_count.py`): of 1,677 RB rushing rows, 3 are `did_not_play` and 4
  have a box line with no offensive snap; of 1,945 RB TD rows, 3 and 14; QBs 0.
- The prospective window is more exposed. The props log keeps a player's last record when Sleeper stops listing him, so a
  late scratch can carry a stale frozen line and score 0.

*Fix.* In every season and window, score only rows where he took a play: Sleeper `gp` > 0, or the dataset's
`act_any_snap`. Kalshi counts any play, so a special-teams-only game stays and scores his 0. Drop the rest, and count the
drops before any outcome is read.

**M8. The licence default leaks through the game window into `dec_trend`.**

*Evidence.*
- `dec_trend` = `p.recent.carry_share` - `p.usage.carry_share`.
- Both are "his designed runs / the team's, same games" over `RushSnapshot._games(pid)`, which is `T.player_games`.
- `_close_game` creates a player line whenever PFR's snap counts list him, including special-teams-only games
  (`pl[PL["pfr_st_snaps"]] = st_n`). Such a game puts the team's designed runs in his denominator and enters his last-N
  window.
- The Opus data re-check measured the size (R1): `p_games_cur` matches offense-only appearances on 4,908 of 6,041 RB rows,
  and offense plus special-teams-only on 6,034. About 1,126 rows' windows move with PFR snaps.
- `SOURCE_OF` tags both fields `pbp`, the default, so the equality guard is green.

*The rule.* Q10 says no "PFR-snap-derived field ... or the trend terms" enters a fitted rung.

*Fix.*
- Fitted features read usage windows built from pbp appearances only (a designed run, target or dropback), with
  participation's on-field lines in prior seasons as the layer allows. Or the layer tags these fields
  `derived:pbp+pfr_snaps` and `dec_trend` reads licence-clean variants.
- Add a guard: a fixture with a special-teams-only PFR line must not move any fitted input.

**M9. Q8 is incomplete. 2025 is not blind on DEC's opportunity terms or the level either.**

These were seen before the rule and are not listed:
- **`nfl_usage.md` (shipped 2026-10-18).** Carry share over the last 3 games at 0.3 against Sleeper's: **-0.003177 +/-
  0.001459 on 2025 (pass)**, -0.002578 +/- 0.002000 on 2026. That is `dec_trend`'s signal. Its absent-lead-back case "hurt
  on 2025", which is `dec_room_out`'s.
- **`nfl_trim.md`.** Actual over projected rushing yards: 0.958 on 2025, 0.922 on 2026 weeks 1-6. Touchdowns: 0.957 and
  1.056. This bears on the level (M1) and on the TD markets.
- **The first model's 2025 results.** Its R rung and its receptions, receiving-yards and anytime-TD ladders on 2025, and
  its 2026 weeks 1-6 test, all cover RBs. Q8 lists only its rushing-yards results.
- **Outcome-touching checks with no model in them.**
  - The data layer's 2025 reconciliation of pbp against Sleeper's box.
  - The data-source report's 2025 settlement checks (239 of 239 QB ladders, the sacks tests).
  - The data reviews' 2025-26 `act_played` / `act_any_snap` / `gp` tallies.
- **The frames carry 2025 and 2026 `y_*` columns.** The reader drops them (the first model's R6 disclosed the same).
- **DECAY 0.75 was read off pairs through 2024 -> 2025.** I re-ran the data layer's `measure_persistence` on 2018-2024
  stores only (`W/decay_wo2025.py`): the tendency r is 0.600 (per-metric mean 0.6002), so DECAY is 0.75 either way, by
  0.0002. The first model's R6 disclosed its own equivalent.

*Fix.* List each in Q8. Say plainly that DEC's opportunity terms and the level are not blind on 2025, so the prospective
window is the only blind judge of them.

**M10. Markets the rule never tests would be priced.**
- `SERIES` prices KXNFLLONGRSH for RBs and QBs, and `THRESHOLDS` carries `long_rush`. Q4's threshold table has no longest
  rush, and no rung scores it.
- Rush + rec yards and the derived TD "decide nothing of their own". A derived market that is worse than its baseline still
  ships when its parents do.
- The board code added at 01:53 (`plan`, not fully reviewed) prices `rush_rec_yd` and `long_rush` from the compound at
  weight 0 **even when nothing shipped** ("rung 1: Sleeper's line through the decomposition's 2023-24 shape at weight 0").
  It would put an untested distribution on two Kalshi series.

*Fix.*
- Add the longest rush to Q4: RB 10 / 15 / 20 / 30 / 40, QB 10 / 15 / 20 / 30.
- Give every derived market a block-only check: not worse than its rung 1 on 2025 and on the 2026 frozen rows, point
  estimate.
- Price a derived market only when its parents shipped and its own check holds.
- KXNFLRRYDS and KXNFLLONGRSH have no first-model price, so with nothing shipped they stay unpriced.

**M11. The leakage-relevant definitions are not guarded.** Hard rule 7 asks for a guard proving no leakage. These mutants
survive the rule's guard file (§3):
- **F3:** the comparables read the row's own week (`>=` to `>`). The code is right today: my poison probe
  (`W/probe/comp_poison.py`) leaves the feature identical when the own and later games are set to 9,999 yards. Nothing
  pins it.
- **K1, K2:** the validation frame shrinks with the 2018-2025 k, or the first model's defense block reads its pooled k.
- **F4:** the frame's kickoff is read as UTC instead of Eastern, which would admit captures 4-5 hours after kickoff.
- **SE1, SE2:** the clustered SE without G/(G-1), or clustered by row. No guard file anywhere covers
  `research.nfl_matchup_fit.clustered`.
- **R7, R8, R9:** REG_CODE / REG_DATA and the hashed block's bounds are not pinned to Q9's list.
- **F8, F9, U1:** the row universes (QB 20+ pass attempts, RB 5+ carries, TD 0.15) are not pinned to Q1.
- **F5:** the TD outcome's default is not pinned.
- **R3:** an audit read that keeps `y_played`, `y_src`, `y_long` and `y_des` passes (the fixture has only `y_rush_yd`).
  `cmd_audit`'s own assert would still catch it at run time.
- **S2:** see M6.

Also, `guards_nfl_rush_model` is not in `combo_audit_guards.py`'s MX list, so CI never runs it.

*Fix.* Add a guard for each. Add the module to MX.

### MINOR

**m1. The family-wise error rate.**
- *The rule's figures are right as a normal approximation.* Phi(-2) = 2.28% a decision, so over 30 decisions 0.68 expected
  false accepts and 49.9% for at least one.
- *The real per-decision rate is lower.* A fitted null group loses out of sample on average. My null simulation
  (`W/nullsim.py`, fit 2023, score 2024, 1,000 replicates per setting; 1, 3 and 6 pure-noise features; lambda 0 and n) gives:
  - P(d < -2 SE) 0.0-1.6%;
  - with the floor, **0.0-1.3%**, mostly 0.3-0.7%.
- *The later steps halve, they do not control.* The 2026 check (d <= 0 on 57 games) and the prospective keep (d < 0) each
  pass a null group about half the time. End to end, that is about 0.57% a decision on the normal figure: P(at least one
  of 30) 15.7%, 0.17 expected. On the simulated figure, about 4% and 0.04.
- *Fix.* Write those figures in Q3b in place of "That is what the 2026 secondary check ... and the prospective window ... are
  for".

**m2. Q1's window sentences contradict the operative rule.**
- Week 7's PIT @ NO kicks off **13:30 UTC** on Sunday 2026-10-25 (props log, `26OCT25PITNO`). "Registered before Sunday
  2026-10-25 17:00 UTC: week 7's Sunday and Monday games are in" is false between 13:30 and 17:00.
- "At the latest week 8 (29 Oct)" promises a registration date the BLOCKERs make unlikely.
- Name the kickoff clock (scheduled ESPN kickoff, or nflverse's actual start time).
- Say what happens if fewer than 40 games remain at week 13.

**m3. Grid wrap is undetectable.**

Circular convolution preserves mass, so `mass_raw` reads 1.000002 even when 82% of the mass has wrapped. The comment
"1 up to floating point when nothing wrapped" is wrong, and so is the guard "nothing wrapped around the grid", which tests
`mass_raw`.

Measured on an 8,192-point grid (`W/wrap.py`, with the 2023-24 kernels):

| case | mass beyond 895 | grid-mean error | max error in a probability |
|---|---|---|---|
| a 30-carry back at 5 ypc, weight 0 | 9e-14 | | |
| ypc x e (the log-factor clamp) | 0.39% | -3.96 yards | 0.004 |
| rush + rec at the clamp | 4.7% | | 0.045 |

Realistic lines are safe; the clamp the rule allows is not. Add a runtime check: the analytic mean minus the grid mean, or
mean + 8 sd < 895, with a ledger and a refusal on breach. Guard it.

**m4. Missing reads 0 for the deviations, which is right, but wrong for the two non-negative counts.**
- `cfp_ol_out` and `dec_room_out` read 0 = "nobody out", their minimum, not their mean.
- Research rows always have the final report. A production board built before nflverse posts the week's report reads None,
  and so 0, on every team. That is a systematic train / serve skew of about -b x (the mean count).
- Fix: impute the training mean on a missing report, or define the production read in Q1.

**m5. The QB breakdown is not candid about the position split.**
- On 2024 QBs whose own designed share is 0.40+ (99 rows, `W/qbsplit.py`), "Expected designed runs" (the position's split)
  reads **2.09 a game against 3.83 actual**, and scrambles read 3.84 against 2.60.
- The basis says "split by the position's shares", but the label reads as his.
- Fix: the label says "the position's split of Sleeper's attempts, the same for every QB", and his own expected counts sit
  beside it.

**m6. Wording.**
- The floor: "x the market's thresholds" should say "x the NUMBER of the market's thresholds".
- "QB rushing TDs" is rushing plus receiving TDs (KXNFLTD).
- The RB receiving universe in code also needs projected receptions > 0.
- Say that the fit universe equals the scored universe.
- Say that lambda's leave-one-season-out runs over the fitting seasons only.

**m7. The props log and the fallback captures.**
- `nfl_props_log` keys Sleeper records by normalised name only. The rule says "name and team".
- No team-assigned collision exists among QB / RB / WR / TE in weeks 1-7. A collision would lose a row, never leak.
- For the DK fallback captures, prefer "fetched before kickoff" (`capture.json` `captured_utc`) to `updated_at < kickoff`
  alone.

**m8. KXNFLTD counts every touchdown** (returns and fumble recoveries; `rush_data_sources.md` 9). The outcome counts rushing
plus receiving only. It is rare for RBs; say so or include them.

**m9. Build records.**
- The frame's `BUILD_CODE` omits `kalshi_nfl.py`. The test frame's frozen join calls `kalshi_nfl._norm`, and `kalshi_nfl.py`
  changed at 01:42 with no stale flag.
- `prereg.json` records no deep-store file hashes, which the prospective rebuild will read for 2019-2025.

### NIT

- **N1.** Freeze the tree for the hash review. The model, the frames and two shared modules changed during this review.
  The round-3 frames went stale within minutes, on `nfl_matchup.py`. Registration would refuse, which is right.
- **N2.** Week 3's only frozen capture is from 2026-09-19 (about 8 days before kickoff). Report each 2026 row's lead time
  in the secondary check.
- **N3.** A rescale with s > 1 lets a run exceed 99 yards. This is harmless at the priced thresholds.
- **N4.** Record the sha256 and UTC in the report as well as in `prereg.json` (hard rule 8).

---

## 3. The checks, A-G

**A. Nothing seen that the rule claims is blind.**
- **The code paths.** The only `outcomes=True` read is `traincheck_frame`, on (2023, 2024). `season_plays`, `box_rows`
  and `fit_distributions` call `_guard`. `research/nfl_rush_model_explore.py` reads `SEASONS = (2023, 2024)`.
- **The outputs.** Every model output under `<scratchpad>/nfl_rush_model/` (`explore.json`, the two traincheck JSONs,
  `traincheck_frame.json`, and the committed `design_*.json`) carries no 2025 or 2026 key. The audit and dry-run hold only
  counts and hashes. I could not read the builder's session.
- **The refusals.** The frame reader refuses 2025 / 2026 outcomes (guarded; mutant R2 caught). "Fit / validate refuse
  without the registration" does not exist: there is no validate, and the hold is a constant (B1).
- **Q8.** It is honest that 2025 is not blind on shape and 2026 weeks 1-6 not on anything rushing, but it is incomplete
  (M9).

**B. Decidable and unambiguous.**

| item | verdict |
|---|---|
| feature lists | generated, and the text equals the code (guard; mutant Q3a caught) |
| metric and threshold lists | match `THRESHOLDS` |
| SE | `clustered()` does G/(G-1) by game, but nothing guards it |
| the gate | strict, so ties are decided |
| the 2026 window | weeks 1-3 and 6 fall out of "frozen rows" |
| the 30 decisions | counted from GROUPS and guarded |
| the walk order across the count / yards pair, the component mapping, derived markets, the 2026 and prospective walks' coefficients and conditioning | ambiguous (M3-M5, M10) |
| registration time | defined (`prereg.json` `utc`), but Q1's example contradicts it (m2) |

The false-accept computation is in m1.

**C. The baseline and the prospective data.**
- **The freeze holds.** `nfl_props_log` rewrites a player only while his team's game is ESPN "pre" with kickoff - now >=
  300 s, and stamps the pass time. So a props-log record cannot carry post-kickoff content. A delayed kickoff only freezes
  it earlier. Week 6's log: all 73 RB / QB records with 2+ projected attempts sit 0-1 h before kickoff.
- **The fallback.** The rule is implementable in principle: `frozen_lines` compares the props log's pass time with each
  capture's `updated_at` under `choose_frozen`'s strict `<`. Its week limits break it for the window (B2).
- **The outcome.** The definition matches official scoring: kneels in, sacks out, Sleeper's box first, pbp as fallback. The
  did-not-play treatment does not match Kalshi (M7).

**D. The features.**
- **As-of.** The snapshot refuses its own week (the data layer, reviewed). The comparables skip same and later weeks
  (poison probe), as do the defense block and the league means at the cutoff. The game script reads the closing line, not
  the score (guarded).
- **The participation mask.** `availability_view` masks the season's own participation for every snapshot (guarded;
  F1, F2 and F6 caught).
- **The k.** `fit_only` for the validation frame and pooled for the test frame. The code is right, but unguarded (K1, K2).
- **The licence.** Every declared source sits in FIT_SOURCES and equals the layer's tag. `PENDING_PATHS` is empty, and both
  replacement inputs are present on every row (`paths_absent` empty). The window leak is M8.
- **The missing null.** See m4.

**E. The distributions.**
- **The pieces.** I checked each:
  - the NegBin pgf `(1 + alpha mu (1 - z))^(-1/alpha)`, whose principal branch is continuous for |z| <= 1;
  - the mean-preserving split `f(1-fr) + (f+1) fr = x`;
  - the longest rush, `1 - prod G_j(P(X_j < k))`;
  - the anytime TD, `1 - exp(-lambda)`, exact.
- **Brute force.** My numpy Monte Carlo (`W/mc.py`, 2,000,000 games each, a gamma-Poisson count with kernel draws) against
  the FFT, on the 2023-24 kernels:

| case | what I compared | largest difference |
|---|---|---|
| RB, 14 carries / 61 yards | P(yards >= 10 ... 100) | 0.00045 (1.3 SE) |
| | longest rush 10+ ... 40+ | 1.1 SE |
| | rush + rec | 1.0 SE |
| RB backup, 6 / 22 | | 1.7 SE |
| QB, 5.0 attempts / 27 yards, +4 margin, four parts | | 2.1 SE |
| | attempts | 1.1 SE |

  The simulations share draws across thresholds, so the sign runs consistently.
- **The grid.** See m3.
- **The QB position split.** It was chosen on 2023 -> 2024, inside the fitting seasons, which the rule allows. The board's
  wording is m5.

**F. Shipping.**
- **What the rule says.** Q5 needs a 2025 pass and 2026 d <= 0. Q6 keeps or reverts. The board says "provisional"
  (`ship_status`, the breakdown; guarded).
- **What stops a failed group shipping.** No code does: there is no export, and the gate fails open (M6).

**G. Guards.**
- The guard file runs 89 checks.
- At my first read (`nfl_rush.py` 8b13ed6b) one check failed. The round-3 layer had retagged `q.designed.by_side.edge`
  `derived:ftn+pbp`; the equality guard caught it, and 771eae73 fixes the declaration.
- On the reviewed tree it runs 89 / 0, pyflakes clean.

## 4. Mutation table (my own, on `W/tree`)

Runner: `W/mutate.py` and `W/mutate2.py`. Each mutant is one string replacement. CAUGHT means `tests/guards_nfl_rush_model.py`
exits non-zero. The control was green before and after.

| id | target | mutant | result | equivalent? |
|---|---|---|---|---|
| E1 | engine | NegBin pgf exponent -1/alpha -> -alpha | CAUGHT | |
| E2 | engine | pgf base drops alpha | CAUGHT | |
| E3 | engine | rescale split weights swapped | CAUGHT | |
| E4 | engine | rescale factor rounded to 0.1 | CAUGHT | |
| E5 | engine | grid 1,024 -> 128 points | CAUGHT | |
| E6 | engine | longest rush `< k` -> `<= k` | CAUGHT | |
| E7 | engine | kneels not capped at 0.8 x attempts | CAUGHT | |
| E8 | engine | anchoring ignores the kneels' yards | CAUGHT | |
| E9 | engine | mean x (1 + 0 x alpha) | survived | **yes** (the control) |
| E10 | engine | top 64 grid values dropped | survived | near: realistic lines never reach 832 yards; not at the clamp (m3) |
| S1 | ship | provisional / shipped swapped | CAUGHT | |
| S2 | ship | compound where only FULL shipped | **survived** | no (M6) |
| L1 | licence | PFR admitted to FIT_SOURCES | CAUGHT | |
| L2 | licence | `cfp_ol_out` back on `starters.out_report` | CAUGHT | |
| L3 | licence | a PFR snap path declared pbp, read by no feature | survived | **yes** (inert) |
| L4 | licence | a FULL term reads PFR snap share | CAUGHT | |
| L5 | licence | `dec_trend` on PFR snap shares | CAUGHT (by a crash in the adapter check first) | |
| R1 | refusal | FORBIDDEN drops 2025 | CAUGHT | |
| R2 | refusal | the frame reader stops refusing held outcomes | CAUGHT | |
| R3 | refusal | an audit read keeps `y_played` / `y_src` / `y_long` / `y_des` | **survived** | no (M11) |
| R4 | refusal | registration without the go | CAUGHT | |
| R5 | refusal | stale check on research files only | CAUGHT | |
| R6 | refusal | blocked inputs no longer block | CAUGHT | |
| R7 | registration | the fit file left out of REG_CODE | **survived** | no |
| R8 | registration | the RB dataset left out of REG_DATA | **survived** | no |
| R9 | registration | the END marker left out of the hashed block | **survived** | no (the sha no longer matches the rule's definition) |
| R10 | decisions | count markets also decide eff-only groups | CAUGHT | |
| F1 | mask | in-season on-field table kept | CAUGHT | |
| F2 | mask | the prior season masked instead | CAUGHT | |
| F3 | leak | comparables read the row's own week | **survived** | no (M11) |
| F4 | freeze | kickoff read as UTC | **survived** | no (M11) |
| F5 | outcome | a missing rush TD reads 1 | **survived** | no |
| F6 | mask | in-season situational counts kept | CAUGHT | |
| F7 | inputs | an incomplete aux accepted | CAUGHT | |
| F8 | universe | QB rows at 10+ pass attempts | **survived** | no |
| F9 | universe | RB rushing rows at 4+ carries | **survived** | no |
| U1 | universe | RB TD rows at 0.10 | **survived** | no |
| SE1 | SE | G/(G-1) dropped | **survived** (`guards_nfl_matchup_model` too) | no |
| SE2 | SE | clustered by row | **survived** (`guards_nfl_matchup_model` too) | no |
| K1 | leak | the validation frame on the 2018-2025 k | **survived** | no |
| K2 | leak | the first model's defense block on its pooled k | **survived** | no |
| FZ1 | freeze | `choose_frozen` admits a capture up to 1 h after kickoff | survived here; CAUGHT by `guards_nfl_matchup_model` (in the suite) | |
| Q3a | rule text | a QB DEC term moved from count to eff | CAUGHT | |
| O1 | outcome | Sleeper's box never used | CAUGHT | |
| O2 | outcome | a did-not-play row labelled pbp | CAUGHT | |

**Tally: 45 mutants.**
- The rushing guard catches 26.
- The suite catches 27 (FZ1 by the first model's guard).
- 18 survive everything:
  - 2 are equivalent (E9, L3);
  - 1 is near-equivalent at realistic inputs (E10);
  - **15 are real gaps:** S2, R3, R7, R8, R9, F3, F4, F5, F8, F9, U1, SE1, SE2, K1, K2.

**Not mutable.** The gate, the floor's use, the walk, the ties, the 2026 walk, the window count, the extension, the export
and the lock. Their code does not exist (B1).

## 5. What I could not check

- **The decision machinery.** It is not written (B1), so I could not review it or mutation-test it.
- **2025 and 2026 numbers.** I read no outcome for those seasons. I therefore could not:
  - count those seasons' did-not-play rows;
  - re-derive any 2025 / 2026 frame cell beyond feature and line columns;
  - say whether the M1 intercept passes or fails on 2025. The first model's 2025 table says it hurt.
- **The builder's session.** I checked artifacts, not the transcript, for what was read.
- **The board code added at 01:53** (`nfl_rush.plan`, `multipliers`, `vector_history`). It arrived during the review and
  was read for M4 / M10 only. It needs its own review.
- **The full suite.** I did not run `tests/combo_audit_guards.py`. The rushing guard file is not in it (M11).
- **Sleeper's `updated_at`.** I could not test whether it is honest for captures fetched after kickoff (m7).

Work files, all under `<scratchpad>/review_rush_rule_opus/`:
- the measurement scripts: `level.py`, `nullsim.py`, `mc.py`, `wrap.py`, `dnp_count.py`, `qbsplit.py`, `decay_wo2025.py`;
- the probes in `probe/`;
- the mutation runners `mutate.py` / `mutate2.py`, with `mutants.json` and `mutants2.json`;
- the fitted 2023-24 distribution half, `coefs_2023_2024.json`;
- the hash snapshots `sha_start.txt` / `sha_end.txt`.

---

## RE-CHECK (2026-10-24, 06:07-06:23 UTC)

**What I re-checked.** The tree hashed the same at the start and the end of this re-check (`W/re/sha_start.txt`,
`W/re/sha_end.txt`; `W` = `<scratchpad>/review_rush_rule_opus/`):
- `nfl_rush.py` bb73f21d, `research/nfl_rush_model_rule.py` 19927411, `research/nfl_rush_model_fit.py` d9b2bb30,
  `research/nfl_rush_model_frame.py` 9dbb26ec, `tests/guards_nfl_rush_model.py` 04d2f91d;
- `nfl_matchup.py` c93a526f, `kalshi_nfl.py` 869db13e;
- the round-4 layer: 247cc625 / 0d319ed0 / 82e4aa12;
- the report 76b1ea6a.

The rule block hashes **f4b42366…**. The registration dry run, which I re-ran and which writes nothing, gives:
- frames val **a366f228…** and test **9815c520…**;
- no stale frame and no blocked input;
- **35** decisions;
- 14 code files and 7 data files.

All of this matches the builder's §6.

**Rules kept.**
- I made no git writes, and this section is my only repo write.
- I read no 2025 or 2026 outcome. My only outcome reads were 2023-2024, through `read_frame`, which refuses the held
  seasons.
- I removed `prereg.json` from my copies. There was none to remove.

### Verdict: **AGREE AFTER FIXES**. There is no BLOCKER. Four MAJOR findings are required before the hash.

**What holds.**
- Both BLOCKERs are answered:
  - the machinery exists;
  - the hold lifts only through `prereg_status`;
  - the frozen reader is week-agnostic.
- Every MAJOR from the first review is fixed in substance (status table below).
- I read every command body line by line against Q4-Q6, and **the wiring is right as written.**
  - **validate:** fits the validation frame on 2023-24 (parts and dispersion), scores 2025 with the strict 2-SE-and-floor
    gate, and walks sequentially.
  - **check2026:** walks only the 2025-accepted groups, refit on 2023-25, keeps on d <= 0, frozen rows only.
  - **export:** ships check2026's set as "provisional".
  - **prospective:** counts the window from the registration time, uses frozen rows, and keeps on d < -1 SE over the
    provisional set.
- The board hook is byte-identical with no `coefs.json`. I reproduced this against **git HEAD's** `nfl_matchup.py`, not the
  builder's reconstruction: both builds give sha a4123646… on the week-7 board, 7,314,228 bytes, 294 players.
- **My own traced plumbing run read no held outcome** (below).

**Required before the hash.**
1. **R1 (MAJOR).** No guard exercises the four commands' bodies. 30 of my 40 machinery mutants survive, and 25 of those
   are real gaps (below). They include:
   - a 2026 check that walks every group;
   - an export that ignores the 2026 check;
   - a prospective test that ignores the registration time, keeps on d <= 0, or ships every provisional group;
   - a validate whose rung 1 and kernels are fitted with 2025.

   The comparables' "strictly earlier" guard is also **vacuous**: its fixture gives every opponent the same vector, so the
   feature is identically 0 and a same-week read passes (my O4).

   The code is right today and the hash will freeze it. But the decisive wiring is pinned by nothing, and each survivor
   is a one-line slip that every guard passes.

   *Fix.* Add a hermetic end-to-end guard: a temp `MODEL_OUT` with `prereg_status` patched to true, tiny synthetic frames
   with one generating term and one noise term, and `parts_for` / `dispersion` stubbed as the existing checks do. Run
   validate -> check2026 -> export -> prospective on it and assert:
   - which groups each command walks, and with which rule (a group at d = -0.5 SE is kept in 2026 and reverted
     prospectively);
   - that games before the registration time are excluded;
   - that only rows with a frozen capture count;
   - write-once on the lock;
   - the `require_frame` refusals;
   - export's set = check2026's;
   - the final `coefs.json` = only the kept groups, as "shipped".

   Give the comparables check distinct opponent vectors.
2. **R2 (MAJOR, the residual of B2).** Q1's prospective build locks the test out.
   - Q1 says the rushing dataset is rebuilt "by the registered data layer". `research/nfl_rush_dataset.py build` writes
     only to `research/data/nfl_rush/` (its `OUT`), and the frame reads only that path (`DATASET`).
   - Those files are REG_DATA. Rebuilt in place, `prereg_status` returns "no longer hashes", so **`prospective` (and every
     command) refuses**.
   - This fails safe (a refusal, never a leak). But the decisive test as written cannot run without an unwritten copy-tree
     or restore-the-file procedure.

   *Fix.* Give the dataset builder an `--out` and the frame builder a `--dataset`, and build the prospective dataset
   outside REG_DATA. Record its sha256 in the prospective meta (it is already listed). Say so in Q1. Guard it: a
   prospective build in a temp directory leaves `prereg_status` true.
3. **R3 (MAJOR, the residual of B2).** The prospective frame's coverage is not pinned.
   - `prospective_frame_refusal` checks the code, the k, the season, the frozen-only flag and the build time. It does not
     check the weeks or the props logs.
   - The frame is built AFTER the window, with outcomes in hand, from `--weeks` and `--props` lists that a person chooses.
     Omitting a week's props log quietly "drops that week" (Q1), and a narrower `--weeks` shrinks the window.

   *Fix.* `prospective` derives the expected weeks itself (the registration's week through `lw`, extended as Q1 says) and
   refuses a frame whose `meta["weeks"]` does not cover them. It also refuses unless, for each such week, either a
   `nfl_props_2026_wNN` log was an input or the rule's "capture missing" is recorded. Make that record from the
   sim-history listing, written before the box is fetched.
4. **R4 (MAJOR, the residual of M6).** `export` can re-ship groups the prospective test reverted.
   - `cmd_export` never checks for `prospective_lock.json`, and it writes `coefs.json` with a plain `open` (not
     `write_once`).
   - Running `export` after `prospective`, with no `--force`, silently rewrites the final `coefs.json` with check2026's
     WHOLE provisional set as "provisional", reverted groups included.
   - The board's gate reads only the status string (`shipped_groups`), never the `records`, so it would price them.
   - My X2 mutant (prospective writing every provisional group as "shipped") also survives.

   *Fix.*
   - `export` refuses once a prospective lock exists.
   - `coefs.json` is written once per stage (`--force` says so).
   - `load_coefs` refuses a shipped entry whose records do not chain: provisional entries need `check2026_sha256`,
     shipped ones `prospective_sha256`, each matching the file beside it.
   - Guard the export-after-lock refusal.

### The first review's findings, re-checked

| # | status | how I checked |
|---|---|---|
| **B1** machinery missing | **FIXED** (wiring unguarded: R1) | read every command; the hold is `HELD` + `prereg_status` (rule block, REG_CODE, REG_DATA sets and hashes); H1, H3, H4 caught; dry run reproduced |
| **B2** prospective unbuildable | **FIXED in the reader; R2 / R3 open** | `frozen_lines` maps per week, else roster, else any week, over `proj_2026_*.json`; my round-1 probe of a back first seen after week 7 is now guarded |
| **M1** level | **FIXED** | `fit_glm` has no intercept column; `fit_ridge` and `linear` centre with no intercept; Q2 puts level recalibration OUT; Q8 discloses the 2025 levels |
| **M2** committed CVs | **FIXED** (unguarded: V1 survives) | `cmd_validate` uses `dispersion(Fv, (2023, 2024))` and `parts_for((2023, 2024))`; `family_dist` reads this rule's dispersion |
| **M3** DEC bundles shape | **FIXED** | the LADDER has SHAPE then DEC; after SHAPE a yards market reads only `eff` terms |
| **M4** spec holes | **FIXED** | `QB_COMPONENT` and the two count / two efficiency GLMs with their offsets; the walk order and the "count market's final factor" coupling are code (W1 caught); TD reads only its own groups |
| **M5** 2026 / prospective walks | **FIXED** (wiring unguarded: W3, W4, P1, P4, E2 survive) | sequential `walk()` on top of what was kept; both refit on 2023-25 |
| **M6** fail-open gate | **FIXED for statuses; R4 open** | `SHIP_STATUSES` whitelist, ledgered (B1 caught) |
| **M7** did-not-play | **FIXED** | played = Sleeper `gp >= 1` or a pbp rush, target or dropback, never PFR (selection_checks) |
| **M8** licence leak | **FIXED** (data round 4) | the `app` flag (rush / designed run / target / scramble / abort / kneel / on-field / situational, or a dropback) decides appearance; the special-teams-only PFR guard exists; both data reviewers agreed |
| **M9** Q8 | **FIXED** | nfl_usage, nfl_trim, the first model's R rung, the frames carrying `y_*`, DECAY at 0.600 are all in Q8 |
| **M10** untested markets | **FIXED in substance** (m6 below) | `plan` prices `rush_rec_yd` / `long_rush` only through shipped SHAPE, else "none"; the longest rush has a Q4 row |
| **M11** guard gaps | **MOSTLY FIXED** | my round-1 survivors re-run: kickoff clock, G/(G-1), the k mode, the universes, the TD default, the outcome-free read and REG_CODE are all CAUGHT; **O4 (comparables) still survives** (R1) |

### The `--plumb` run: what it substitutes, and whether it can touch a held outcome

**What it substitutes** (`rule.load`, `rule.PLUMB`):
- For every 2025 / 2026 row, read through `read_frame(..., outcomes=False)` (which drops every `y_*`), every `y_*` column is
  replaced by that of a random same-position **2023** row (numpy `default_rng(7)`).
- The distribution parts are fit on the open seasons only.
- The prospective step takes registration time `--plumb-reg-ts` (0), and its frame is the test frame.
- Output goes only under `research/data/nfl_rush_model/plumb/` in the tree it runs in. The board reads only `coefs.json`.

**Can it touch a held outcome? Not before the hash.**
- Every outcome-capable read goes through `_guard` -> `prereg_status`, which raises SystemExit (nothing catches it)
  without a valid registration: `read_frame` with outcomes, `season_plays`, `box_rows` and `fit_distributions`.
- I ran my own `validate --plumb` on a copy, with every such read traced and any held outcome read made fatal
  (`W/re/plumb_trace.py`, 344 s):
  - the outcome reads were 2023-2024 only;
  - the 2025 reads were outcome-free, with no `y_*` column surviving;
  - `_guard` saw only 2023 and 2024.
- It reproduced the builder's noise result: QB attempts FULL and QB rushing yards SHAPE "accepted" on donor outcomes.

**After the hash the plumbing code is unguarded.** My PL1 and PL2 mutants (read the held rows WITH outcomes; donors from
2025) are refused at run time today, but would read held outcomes once `prereg.json` exists. That is permitted then, and no
decision reads plumbing. `PLUMB` is a module global set only in `main()`. Fold it into R1: derive it from `a.plumb`.

### Error rates, recomputed

The formulas are the code's (`error_rates`). The normal figure is the gate's Phi(-2) = 2.275%; the 2026 check is taken as
0.5; the prospective keep is Phi(-1) = 15.87%.

| per null decision (35 decisions) | rate | expected | P(at least one) |
|---|---|---|---|
| provisional ship, normal gate | 1.14% | 0.398 | **33.0%** |
| permanent ship, normal gate | 0.180% | 0.063 | **6.1%** |
| provisional, simulated gate 0.5% / 1.3% | 0.25% / 0.65% | | 8.4% / 20.4% |
| permanent, simulated gate 0.5% / 1.3% | 0.040% / 0.103% | | 1.4% / 3.5% |

Every figure matches Q3b. Two notes:
- The max(game, player) SE makes the real gate stricter than Phi(-2).
- **Up to three of the 35 decisions are vacuous:** RB rushing yards, RB receiving yards and QB rushing yards DEC once
  their SHAPE is in. DEC holds only count terms, so the candidate equals the accepted model, d = 0 and it is always
  rejected. The rates are conservative by that much; Q3b should say so (m7).

### Does the registration list cover every file the runs execute?

**Nearly.** I imported the rule, fit and frame modules and `nfl_matchup`, called the lazy paths (`input_shas`, `fit_glm`),
and listed every repo module loaded.
- **Executed but unregistered:**
  - `deep_cache.py`: it resolves and loads every store pickle, so it is behaviour-relevant;
  - `errlog.py`: the ledger only;
  - `kalshi.py`: imported by `kalshi_nfl`, with nothing called;
  - `research/__init__.py`.
- **Registered but no longer executed:** `research/nfl_matchup_frame.py`. The rushing frame has its own reader. This is
  harmless.

*Fix* (m3): register `deep_cache.py`, or state the four as behaviour-neutral imports. The claim "every code file the runs
execute" is not literally true today.

### The board hook on Render

- **Memory: safe.**
  - The child peaked at 220-277 MB (the builder's measurement).
  - The instance snapshot (`mem-latest`, 2026-10-23) shows a 2,048 MB cgroup with about 430 MB in use.
  - `HEAVY_BUILD` is a per-process semaphore, so at worst three gunicorn workers build at once: about 0.8 GB + 0.43 GB,
    under 2 GB.
- **CPU: the steady state is safe, the first comparables build is not proven.**
  - **Steady state.** The hook adds 12-16 s to a niced child every 15 minutes (TTL 900). That is about 1-2% of the one
    core.
  - **First comparables build.** It was 103 s on this 16-CPU sandbox, against the child's **240 s timeout**. On Render's
    one contended core at nice 10 (CLAUDE.md: "Nothing CPU-bound belongs in a gunicorn process on a one-core quota" --
    the child is niced, but it competes with three workers), it may exceed that.
  - What follows is bounded. The weekly vectors are saved one by one, so retries converge. But each attempt blanks the
    NFL board and holds `HEAVY_BUILD` for up to 240 s.
  - *Fix* (m1): compute the vector history in the `nfl_matchup` deep job (PC-first), never on a board build. Measure a
    hook-on board under a one-CPU limit before any `coefs.json` with a comparables term ships.
- **Failure modes** (m2, inert until a model ships).
  - `_rush_env` is not wrapped. Any exception in `RD.load_aux`, `RP.build`, `team_blocks` or `vector_history` propagates
    to `board()`'s catch-all and blanks the **whole** NFL board, WR and TE included.
  - `_rush_player` catches only four exception types.
  - Wrap both in a ledgered `except Exception` (`NFLR-board-env`) that falls back to the first model's board.
- **The extra series** (m4). `kalshi_nfl.extra_props()` caches in process memory, but the board builds in a fresh child,
  so the three extra series are fetched on every build. That is the CLAUDE.md gotcha "anything cached only in memory is
  re-paid every time". Persist the cache to the deep store or accept the fetches, and say which.

### MINOR (new)

- **m5.** Q1 calls the store's `kick` the "SCHEDULED" kickoff. It is the pbp's recorded `start_time` (`nfl_matchup_data`
  line 571; seconds included, e.g. 16:25:43). Say "the pbp's start time". The difference is minutes, and the props log
  freezes before either.
- **m6.** A derived market's first priced form is never scored.
  - `derived_report` compares only where both sides price.
  - At the SHAPE step the accepted side has no `rush_rec_yd` / `long_rush`, so the version that ships on KXNFLRRYDS and
    KXNFLLONGRSH is never compared or calibration-reported.
  - Report the candidate's own MAE and ECE on the derived markets at every step that prices them, deciding nothing.
- **m7.** Two record-keeping gaps:
  - **Unchecked record chain.** `cmd_export` and `cmd_prospective` do not check that `check2026.json`'s
    `validate_sha256` equals the `validate.json` on disk.
  - **Unstated vacuous decisions.** Q3b should say that up to three decisions are vacuous by construction.

### My machinery mutants (40, on `W/re/mtree`; runner `W/re/mutate_re.py`)

Each mutant is one string replacement. Each verdict is from `tests/guards_nfl_rush_model.py` (155 checks); the control was
green before and after. The builder's 112 / 112 are mostly on the pieces (`accept_2025`, `walk`, `window_games`,
`write_once`, `prospective_frame_refusal`). Mine aim at the commands that join them.

| id | mutant | result |
|---|---|---|
| G1 | the walk's floor counts one threshold fewer (`gate_2025`) | **survived** |
| G2 | `compare` clusters its "games" by player | **survived** |
| G3 | d = accepted minus candidate | CAUGHT |
| G4 | a single cluster reads SE 0 | survived (near-equivalent: needs a one-cluster comparison) |
| W1 | kept groups never reach the yards offsets | CAUGHT |
| W2 | each group judged against rung 1 | CAUGHT |
| W3 | check2026 walks EVERY group, not the 2025-accepted | **survived** |
| W4 | check2026 keeps every group | **survived** |
| X1 | export ships the 2025-accepted set | **survived** |
| X2 | prospective ships every provisional group as "shipped" | **survived** |
| P1 | prospective KEEP on d <= 0 | **survived** |
| P2 | the window ignores the registration time | **survived** |
| P3 | window rows without a frozen capture scored | **survived** |
| P4 | prospective walks the 2025-accepted groups | **survived** |
| P5 | the window count includes games with no capture | **survived** |
| P6 | a frame built before the registration accepted | CAUGHT |
| L1 | the prospective lock overwritten without `--force` | **survived** |
| L2 | `write_once` never refuses | CAUGHT |
| F1 | commands accept an unregistered frame | **survived** |
| F2 | validate does not check the validation frame | **survived** |
| F3 | prospective never checks the frame's build | **survived** |
| H1 | `prereg_status` ignores an edited block | CAUGHT |
| H2 | `prereg_status` ignores changed DATA files | **survived** |
| H3 | `prereg_status` accepts a different file set | CAUGHT |
| H4 | the hold lifts without a registration | CAUGHT |
| PL1 | plumbing reads held rows WITH outcomes | survived (refused at run time before the hash) |
| PL2 | plumbing donors include 2025 | survived (refused at run time before the hash) |
| PL3 | plumbing keeps the held row's own "played" | **survived** (a vacuous plumb, no leak) |
| PL4 | the plumb branch reordered | survived (**equivalent**) |
| E1 | export refits on 2023-24 only | **survived** |
| E2 | check2026 fits on 2023-24 | **survived** |
| V1 | validate's dispersion and kernels fit WITH 2025 | **survived** |
| V2 | validate fits on the TEST frame (pooled k) | **survived** |
| V3 | validate also scores 2024 | **survived** |
| D1 | dead features from the validation frame only | **survived** |
| R1 | lambda by the WORST held-out deviance | CAUGHT |
| R2 | lambda CV trains on the held-out season | **survived** |
| B1 | the board ships a "reverted" group | CAUGHT |
| B2 | the compound where only DEC shipped | **survived** |
| B3 | hook env built when an env is injected without coefs | survived (production-equivalent: only guards inject) |

My round-1 survivors, re-run on the new tree (`W/re/mutate_re2.py`):
- **CAUGHT:** O3 (kickoff as UTC), O5 (G/(G-1)), O6 (pooled k for the validation frame), O7 (the QB universe), O8 (the TD
  default), O9 (an outcome-free read keeping `y_*`), O10 (the rule module out of REG_CODE).
- **SURVIVED:** O4 (comparables include the row's own week), because the guard is vacuous (R1).

**Tally: 48 mutants.**
- The guard catches 17: 10 of the 40 machinery mutants and 7 of the 8 re-runs.
- 31 survive:
  - 1 is equivalent (PL4) and 2 are production- or near-equivalent (G4, B3);
  - 2 are refused at run time until the hash (PL1, PL2);
  - **26 are real gaps:** 25 in the commands' wiring and plumbing, plus O4.

### What I could not check

- The real 2025 / 2026 decisions. They need the hash; that is by design.
- The hook's timing on Render's actual CPU. I relied on the builder's sandbox measurements, and m1 asks for a one-CPU
  measurement.
- Whether Sleeper's `updated_at` is honest. The capture-fetch clock now bounds it.
- The full suite. I did not run it, and the rushing guards are still not in its MX list.

Work files: `W/re/`. They hold the traced plumbing run (`plumb_trace.py`, `plumb_trace.log`), the HEAD identity proof
(`identity_proof_mine.py`, `identity.log`), the mutation runners and their JSONs, and the hash snapshots.

---

## CONFIRMATION (2026-10-24, 08:07-08:36 UTC)

**What I reviewed.** I froze a copy of the tree at 08:07 UTC (`W/conf/mtree`; hashes in `W/conf/sha_start.txt`):
- `nfl_rush.py` 1ab7afdf, `research/nfl_rush_model_rule.py` e79f46e5, `research/nfl_rush_model_fit.py` 80d1f645,
  `research/nfl_rush_model_frame.py` bcac8e8a, `research/nfl_rush_dataset.py` 3451bede;
- the guards 1902fee0, 186 checks, all passing, about 50 s;
- `nfl_matchup.py` de6ad576, `deep_cache.py` and `kalshi.py`, newly registered.

On that tree I re-ran the registration dry run, which writes nothing:
- rule sha **75add8d9…**;
- frames val **906e9a7e…** and test **b3a28947…**;
- 35 decisions, no stale frame, no blocked input;
- 16 code files, 7 data files, Python 3.11.17 / numpy 2.4.6 recorded.

That is exactly the builder's report. pyflakes is clean.

**The tree moved while I worked.**
- At about 08:34, `nfl_rush.py`, `research/nfl_rush_model_fit.py`, `research/nfl_rush_model_rule.py` and the guards
  changed again: `window_weeks`, a content check in `record_chain`, `resolve_model_out`, and `tree_checks`.
- A dry run at 08:35 shows a NEW rule sha (**c2271bc4…**) and **both frames stale** (nfl_rush.py, fit, rule).
- So the figures handed to me no longer describe the tree that would be hashed.
- I read the 08:34 changes and say below where they bear on my findings, but they are not confirmed.

**Rules kept.**
- I read no held outcome: no 2025 or 2026 outcome column was touched.
- I made no git writes, and this section is my only repo write.

### Verdict: **NOT YET AGREE TO HASH. AGREE AFTER FIXES**

**Every item of my re-check is fixed on the 08:07 tree.**
- R1: the golden end-to-end guard now catches every machinery mutant that was a real gap.
- R2 / R3: the prospective dataset is built to `--out`, and the frame's coverage is checked.
- R4: export refuses after the lock, `coefs.json` is written once, and the record chain is verified.
- The equivalence arguments for PL4 and B3 hold.

**Three things are still wrong.** One is a MAJOR that is still open (C2). One is a MAJOR fixed only in the moving tree
(C1). The third is a fail-open minor (C3).

**Freeze the tree, rebuild the frames, then confirm C1 and the minors on the frozen bytes.**

**C1 (MAJOR on the 08:07 tree; FIXED at 08:34, pending confirmation): the R3 coverage check passed vacuously on a stale
store.**
- `cmd_prospective` derived the expected weeks from `schedule(2026)`, the deep store's 2026 games after the registration.
  That store need not be the one the frame was built on.
- It is plausibly NOT the same. `verify_stores` pins 2023-25 to the registration-time pickles, so an operator who reuses
  the registration's deep folder has a 2026 store that stops at week 6. The schedule then holds no window game.
- In that case `coverage_refusal(meta, [], 0.0, now, {})` returns **None** (`W/conf/probe/fail_open.py`). A frame
  omitting weeks 9-11 is refused against a real schedule. A store refreshed only part-way opens the same hole for its
  missing weeks, and the 48-hour box rule is skipped with it.
- **The 08:34 code closes it**, by my reading:
  - `window_weeks` takes EVERY week from the first post-registration week through `lw`;
  - each must be played in the store and `lw` closed (a week `lw + 1` game);
  - an empty set or an unknown last kickoff refuses;
  - `tree_checks` guards a store that stops at week 9 and a week 11 that is not closed.

  Confirm it on the frozen tree.

**C2 (MAJOR, OPEN): the prospective dataset's provenance is not pinned. It holds the decisive test's OUTCOMES.**
- **The Sleeper parser is unregistered.** The dataset build (`research/nfl_rush_dataset.py build --out`) loads Sleeper's
  2026 box and projections through `research.nfl_matchup_dataset.load_sleeper`. That is the parser of the primary
  outcome, and `research/nfl_matchup_dataset.py` is not in REG_CODE. A registered tree copies it from the LIVE repository
  at copy time, weeks after the hash. My import audit of the build shows it is the one unregistered module besides
  `errlog` and the empty package.
- **The dataset records no code.** The dataset's `meta.json` records no code hashes, and nothing checks that the dataset
  was built in the registered tree at all. The frame records the dataset's and the meta's sha256 as inputs, not the code
  that made them.
- **The dataset check is a folder-name test.** `prospective_frame_refusal` only checks that the dataset folder is not the
  string `research/data/nfl_rush`. A frame built in a registered tree that points at the MAIN repository's registered
  folder by absolute path passes (`W/conf/probe/dataset_check.py`: `/home/user/kalshi-betting-helper/research/data/nfl_rush`
  -> None). The same goes for a dataset built in the live tree by later-edited code.
- **The 2026 store is unverified too.** The store the prospective frame reads is refreshed by the production job on the
  live code, and nothing verifies which code built it.
- **Q9's sentence is false for this path.** Q9 says "the only other repository modules the runs load are errlog.py and
  research/__init__.py". That is not true of the prospective dataset build.

*Fix.*
- Register `research/nfl_matchup_dataset.py`.
- Have the dataset build write the sha256 of every code file it executed, and the versions, into its `meta.json`.
- Have `prospective` refuse unless those equal `prereg.json` and the dataset meta is the one the frame read.
- State how the 2026 store is refreshed (in the registered tree, from a fresh deep folder), and record its builder's code
  the same way, or verify the store-building files' hashes at refresh.
- Correct Q9.

**C3 (MINOR, fail-open, the residual of R4; still open at 08:34): a provisional `coefs.json` beside a lock still loads.**
- `record_chain` checks a provisional file against prereg / validate / check2026, and since 08:34 also that its groups
  are check2026's set.
- It never asks whether `prospective_lock.json` exists beside it. A provisional file restored beside a lock whose test
  REVERTED its groups loads, prices SHAPE as the compound, and says "provisional" (`W/conf/probe/prov_beside_lock.py`,
  run on the 08:34 code: `True | provisional | compound True`).
- Restoring an older `coefs.json` is one `git revert` or `checkout` away on an auto-deploying branch.
- *Fix* (one line): refuse a "provisional" `coefs.json` when a lock exists beside it. Guard it.

**C4 (MINOR): six of my 21 new mutants on the round-2 checks survive.** The code is correct today; these are guard gaps
(below). The 08:34 changes touch N14 and N18 (capture-missing bound to the registration, a stricter
`in_registered_tree`). Re-run on the frozen tree.

### My mutants on the 08:07 tree (`W/conf/mutate_conf.py`, 4 parallel copies; the control was green)

**Carried over from the re-check (40).**
- **38 are CAUGHT,** including every one the re-check left as a real gap:
  - the gate floor (G1) and game clustering (G2);
  - the one-cluster SE (G4);
  - the 2026 walk (W3, W4);
  - the export and the prospective ship (X1, X2);
  - the window and the keep (P1-P5);
  - the lock (L1);
  - the frame refusals (F1-F3);
  - the data files in `prereg_status` (H2);
  - plumbing (PL1-PL3);
  - the refit seasons (E1, E2, V1-V3);
  - the dead-feature frames (D1), lambda's CV (R2), and the compound on DEC (B2).
- **PL4 and B3 survive, and both are equivalent.**
  - **PL4** (`not plumb or not held` split into two returns) is the same boolean.
  - **B3** (`rc or {}`) now sits inside `if rc:`, so `rc or {}` is `rc`. An injected env without coefs never reaches
    `_rush_env` any more, which was the original B3's concern. No production path injects one.

**O4** (the comparables reading their own week) is now **CAUGHT**: the guard uses distinct vectors and a control.

**New, on the round-2 fixes (21): 15 CAUGHT, 6 survived.**
- **CAUGHT (15):**
  - N1, the export-after-lock refusal; N2, `coefs.json` write-once; N3 and N4, the record chain;
  - N5, `read_record`'s registration and code check;
  - N8-N10, coverage: weeks, box lag and run lag; N11, the dataset-folder test; N12, the registered-tree requirement;
  - N16, `registered_tree`'s stored-code check; N17, capture-missing before the box; N19, the hook's fallback;
    N20, the props-log requirement.
- **SURVIVED (6):**
  - **N6:** `read_record`'s chain check removed;
  - **N7:** `verify_stores` skipped. The golden says "the stores re-verified" but does not bite;
  - **N13:** `prospective`'s "coefs.json is this check2026's export" check removed;
  - **N14:** capture-missing may re-record a week;
  - **N15:** `record_chain` accepts mixed statuses;
  - **N18:** `in_registered_tree` accepts a marker of another registration.

**Tally: 61 applied, 53 caught.** Of the 8 survivors, 2 are equivalent and 6 are real gaps in defence-in-depth checks whose
code is right.

### Also confirmed on the 08:07 tree

- **Byte identity.** With no `coefs.json`, the hook's board is still byte-identical to **git HEAD's** board on week 7
  (a4123646…, 7,314,228 bytes, 294 players), on the new `nfl_matchup.py`.
- **Hook fallback.** A failing `_rush_env` / `_rush_board` keeps the whole board (ledgered `RUSH-board`), and
  `_rush_player` catches every exception.
- **Comparables.** The history is computed only in the deep job, and only when a comparables term ships
  (`precompute_vectors`, ledgered `NFLR-vectors`). The board reads stored vectors only (`compute=False`). That closes my
  Render CPU minor.
- **The text.** Q1 says "the pbp's recorded start time". Q3b discloses the vacuous DEC decisions. Derived markets report
  their first priced form's log loss, MAE and ECE.
- **No new leak.** On the 08:07 tree:
  - every outcome read still goes through `_guard` -> `prereg_status`;
  - `parts_for` takes `plumb` from the command, so the module global is gone;
  - `verify_stores` pins the 2023-25 pickles the kernels read;
  - read_record binds every record to this registration and its code.

  `NFLR_MODEL_OUT` lets a registration live outside the repository. The hold was always a trip-wire, not a lock: a copy of
  the tree could always register itself. The 08:34 `resolve_model_out` pins it inside a registered tree.

### Before the hash

1. Freeze the tree. Rebuild both frames on the final bytes. Re-run the dry run, and report the new rule sha and frame
   hashes.
2. Fix C2 (MAJOR) and C3 (one line). Close the six guard gaps (C4), or argue each.
3. A reviewer confirms C1's `window_weeks` and the above on the frozen tree.

Work files: `W/conf/`. They hold the frozen copy and its hashes (`sha_start.txt`, `sha_end.txt`), the mutation runner and
`mutants_conf.json`, the probes in `probe/`, and the identity proof (`identity_proof_conf.py`).

## FINAL CONFIRMATION (2026-10-28, 20:25-21:45 UTC, on the frozen bytes)

**What I reviewed: the frozen bytes.**
- The 26 files of the 09:02 freeze (`<SP>/nfl_rush_model/work/freeze_shas.json`: 17 code, 7 data, 2 neutral). My
  copy (`W/final/tree`) and the live tree hash to all 26 at 20:30, 20:53 and 21:34 UTC, matching the orchestrator's
  20:40 re-hash.
- **My dry run on the copy reproduces the registration** (it writes nothing):
  - rule sha **c38d2da604a603fc8de712cd1ea8da453cc790e8379b0e49cf099e2fb555c1a2**;
  - frames val **906e9a7e…** and test **b3a28947…** (7,413 and 8,242 rows);
  - 35 decisions, no stale frame, no blocked input;
  - 17 code and 7 data files, Python 3.11.17 / numpy 2.4.6.

  The report's block hashes to c38d2da6, between one START and one END marker.
- The guards on the frozen copy: **199 checks, 0 failed**. pyflakes is clean on the 17 registered files and the
  guards.
- **Nothing unreviewed changed since my 08:07 copy.** Diffing every registered file against it, each change is one of
  the listed fixes:
  - `nfl_rush.py` (18 lines): C3 and C-m1;
  - the frame (10): the stores record as an input, the dataset's realpath and meta sha, `BUILD_CODE`;
  - the fit (105): `resolve_model_out`, `REG_CODE` / `NEUTRAL`, `repo_modules`, `unregistered_code`,
    `build_stores`, the tree checks;
  - the rule (100): `provenance_refusal` / `_provenance`, `window_weeks`, coverage's empty-window checks,
    capture-missing's tree and registration checks;
  - the dataset build (9): its code record.

  The other twelve registered files are byte-identical to 08:07.
- **No-coefs byte identity, re-proved on the frozen bytes.** The week-7 board equals git HEAD's pre-hook board
  (a4123646…, 7,314,228 bytes, 294 players).

**Rules kept.**
- I read no held outcome: no frame outcome column, no Sleeper box, no pbp result. The end-to-end run hashed and
  re-saved store pickles without inspecting them.
- Its network was stubbed, so no 2026 pbp was downloaded. My one outside read was ESPN's 2026 schedule for weeks
  8-13, with every game still `pre`.
- I made no git writes and touched no registered file. The scratch registration and its tree live in `W/final/e2e/`,
  outside the repository. This section is my only repo write.

### Verdict: **AGREE TO HASH**, on two conditions

**There is no BLOCKER, and the rule, the window and the walk need nothing.**
- C1 and C3 are closed on the frozen bytes.
- C4 is closed: all six of its guard gaps are now caught. New guard gaps on the final fixes are listed as F5.
- C2 is closed except for two residuals: F1a in `build_stores`, and F1b in the dataset's provenance.

**Condition 1: F1 (MINOR, fail-open) goes into the batched round.** It has two parts:
- (a) `build_stores` must refuse a 2026 store or aux that its own refresh did not build;
- (b) the dataset must record the stores it read, and `provenance_refusal` must bind them to the frame's.

Each is a few lines in REGISTERED files, so it is now or never. If the batch cannot take them, the minimum is Q1's
sentence: one scratch deep folder for every step, never a live folder or a copy of one.

**Condition 2: F2 (MAJOR, operational) is done at the hash.** Archive the two registered frames and the
registration's deep folder durably, and say where. Today they exist only in this session's scratchpad, and
`prospective` cannot run without them.

F3 (wording) and F5 (guard cases for the functions the batch edits) belong in the same batch. F4 is a NIT.

If the batch carries F1 with its guards, F3 and F5, and changes nothing else I have reviewed, a diff check of the
batch is enough for me. I need no further round.

### C1 (the window): CLOSED on the frozen bytes

I ran `window_weeks` on synthetic schedules (`W/final/probe/c1_window.py`), with a LATE registration at Sunday
2026-11-01 19:00 UTC, mid-week 8:
- an unrefreshed store (weeks 1-6) and an empty schedule: "the store holds no game kicking off after the
  registration";
- a store refreshed through week 9: "window week(s) [10, 11] have not been played in the store";
- week 11 played but no week-12 game: "week 11 is not closed";
- closed through week 11: weeks [8, 9, 10, 11], the last kickoff the last week-11 game's;
- extended to week 13: OK only once a week-14 game is in the store.

`coverage_refusal` refuses an empty week list, an unknown last kickoff and a frame that omits week 11. The end-to-end
run (S9 below) reads the registration-time 2026 store as "the store holds no game kicking off after the registration".
That is exactly the stale-store case that passed vacuously at 08:07.

### C3 (a provisional file beside the lock): CLOSED

`W/conf/probe/prov_beside_lock.py`, on the frozen copy: a provisional `coefs.json` beside a lock that reverted its
group no longer loads (`False | None | None`). The mutant that removes the check (RC1) is caught.

### C2(d) end to end: is a 2026 store built by live code refused?

**The setup** (`W/final/e2e/c2d_e2e.py`, log `c2d_e2e.log`, results `c2d_e2e.json`):
- a scratch registration of the frozen bytes, reproducing c38d2da6, with its records in `W/final/e2e/records`,
  outside any repository;
- a registered tree built from it (`e2e/T`), whose marker names that registration;
- a scratch deep folder copied from the registration's (`deep3`), which hashes to the registered test frame's inputs.

`build_stores` ran for real. Only the network was stubbed: nflverse was unreachable, or "unchanged" in S7b.
`provenance_refusal` was fed by the real `_provenance`, on frame metas computed the way `build()` records them. A
"live-built" pickle is the same payload re-saved by `deep_cache.save`, as a live job leaves it: other bytes.

| case | result |
|---|---|
| S1: build-stores, then a frame on what it vouched for | accepted |
| S2: the 2026 store re-saved after the build | "nfl_matchup_2026 is not the store the registered build made" |
| S3: the 2026 aux, likewise | "nfl_rush_aux_2026 is not the store the registered build made" |
| S4: the ids, likewise | "nfl_matchup_ids is not the store the registered build made" |
| S5: the 2024 store, likewise | "the stores ['deep:nfl_matchup_2024'] are not the registered frames'" |
| S6: the build record re-made after the frame read it | "the 2026 stores' build record is missing or is not the one the frame read" |
| **S7: a 2026 store and aux re-saved BEFORE build-stores, nflverse unreachable** | **recorded, and the frame accepted** |
| **S7b: the same, with raw downloads current in the folder (upstream "unchanged")** | **"[matchup] 2026: unchanged upstream, store kept"; the aux "skipped"; recorded, and the frame accepted** |
| S8a: the dataset at the tree's own `research/data/nfl_rush_model/prospective_dataset` (Q1's relative `--out`) | "the prospective dataset is not <records>/prospective_dataset (by realpath)" |
| S8b: the registered `research/data/nfl_rush` | refused, by realpath |
| S8c: the records' folder reached through a symlink | accepted (it IS that folder) |
| S8d: `<records>/prospective_dataset` itself a link to the registered folder | refused: that `meta.json` carries no code record |
| S8e: a dataset meta naming an unregistered module | "not built by the registered code (['clock.py'])" |
| S9: the window, read off the registration-time 2026 store | "the store holds no game kicking off after the registration" |
| S10: an `NFLR_MODEL_OUT` naming another folder, inside the tree | refused at import |

`build-stores` itself is refused outside a registered tree. In the tree its record listed 12 modules, all registered
or neutral, with nothing unregistered. The hold machinery accepts the tree: `prereg_status()` there reads
`(True, 'registered')`, so the stored copies, the block and the data all verify.

**The answer: wherever the FRAME reads a live-built store after build-stores, it is refused. There are two
exceptions (F1).**
- A live store written after the build, or read from any other folder, is refused (S2-S5).
- A build record changed after the frame read it is refused (S6).
- **F1a.** A live-built store and aux that already sit in the scratch folder when build-stores runs are NOT refused,
  if the refresh keeps them. `build_stores` never asks whether its refresh BUILT anything:
  - `nfl_matchup_data.refresh` keeps an existing season store when no source changed, or when the download fails;
  - `nfl_rush_data.refresh` does the same for the aux;
  - `build_stores` then records whatever pickles the folder holds, and ignores both summaries (S7, S7b).
- **F1b.** A store the DATASET build read is not refused either. The dataset records its code but not its stores, and
  the frame takes its OUTCOMES from the dataset's rows. S1's accepted dataset meta said nothing about which stores
  it read.

### My mutants on the frozen copy

**Setup.**
- 131 mutants, each ONE string replacement on its own copy of the frozen tree, judged by the guard file's exit code.
  There were 4 parallel copies, and the control was green (199/0).
- The first 8 ran in waves (`mutate_final.py`); the rest ran from a queue in priority order (`mutate_final2.py`).
- After the run, all four worker copies diff clean against the frozen copy.

**Tally: 130 applied, 104 caught.**
- The 26 survivors are 10 equivalent and 16 guard gaps. In each of the 16, the frozen code is right (F5).
- N11 no longer applies: its folder-name test became `provenance_refusal`'s realpath test, whose mutants PV1 and PV10
  are caught.

**Carried from the confirmation round (60 applied, 58 caught).**
- **All six C4 gaps are now caught:**
  - N6, `read_record`'s chain check;
  - N7, `verify_stores`;
  - N13, `prospective`'s "this check2026's export" check;
  - N14, capture-missing recording a week twice;
  - N15, mixed statuses;
  - N18, a marker of another registration.

  The golden run catches N6 and N13 indirectly. The refusal no longer comes, so a record is written too early, and
  the next step's refusal escapes the section. A section that raises counts as a failure, by design.
- **PL4 and B3 survive and are equivalent,** as argued in the CONFIRMATION.

**New, on the final fixes (70 applied, 46 caught).**
- **CAUGHT:**
  - RC1 (C3);
  - the window (WW1-8, WW10);
  - coverage (CV1-3);
  - provenance (PV1, PV2, PV3b, PV4, PV5, PV5b, PV6, PV7, PV7b, PV7c, PV8, PV10);
  - `build_stores` (BS1, BS2, BS6-8);
  - the code identity (UC1-5);
  - `resolve_model_out` (RM1, RM3), `in_registered_tree` (IT2), `registered_tree` (RT1);
  - capture-missing (CM1, CM2);
  - `record_chain`'s content (RC2, RC4, RC5, RC7);
  - the dataset's code line (DC1).
- **8 survivors are equivalent:**
  - PV1b: `want_ds` is a realpath, so a literal match is a realpath match;
  - PV6d: a frame always records the 2026 store, aux and ids (`check_inputs` requires the first two, the build the
    third);
  - PV7e: both frames load the same registered `STORE_SEASONS` (2019-2026), so no older key can be missing from the
    registered frame's;
  - PV9b: it lets through only a re-made build record that vouches for exactly the frame's stores;
  - BS5: `in_registered_tree` returns only a marker naming prereg.json's sha;
  - RM2: the refusal before it makes `env` equal to `mo`;
  - IT1: inside a tree, `MODEL_OUT` is the marker's `model_out` by construction;
  - RC6: the decision record was just hashed to its recorded sha, so it parses.
- **16 survivors are guard gaps.** The frozen code is right in each.
  - **My end-to-end harness kills 9 of them on the real path** (`W/final/e2e/mut_e2e.py`). Each mutant was exec'd in
    memory from the registered source, so no file was touched, and the e2e scenarios re-ran. A kill is a verdict
    flipping between accept and refuse:
    - PV6c (the aux dropped from the vouched keys): S3's re-saved aux is ACCEPTED;
    - PV6b (the ids dropped): S4 is accepted;
    - PV7d (the older matchup stores unchecked): S5's 2024 store is accepted;
    - PV3 (a dataset meta without a code record): accepted, and so is `<records>/prospective_dataset` linked to the
      registered folder (S8d);
    - PV9a (`_provenance` hands back the frame's own dataset-meta sha): a meta edited after the frame is accepted;
    - BS3 (a missing store recorded): a build with no aux is RECORDED;
    - BS4 (the aux left out of the record): the GENUINE run is refused, a liveness failure, and a missing aux is
      recorded;
    - RMOD (`repo_modules` misses non-research modules): a build after loading an unregistered `clock.py` is
      RECORDED;
    - WW9 (`window_weeks` with no week gives no reason): accepted at the function level. `cmd_prospective` still
      refuses, through coverage's empty-window check.
  - **The other 7, judged by reading:**
    - WW11 (`window_weeks` always run through week 11): an EXTENDED window's weeks 12-13 would go unchecked for
      play, closure, props logs and box. The golden never extends.
    - RT2 (a non-empty tree destination allowed): stale files would survive the copy.
    - RC3 (a `coefs.json` shipping a SUBSET of the decided groups): it loads. The forged-content guard only adds
      groups.
    - DC2 (the dataset's module list filtered): an unregistered module outside the filter escapes the dataset's code
      check. The guard pins the `meta["code"]` line, not the list.
    - FM1 / FM3 (the frame records no stores record, or no dataset-meta sha): every genuine prospective frame is
      refused, a liveness failure.
    - FM2 (the frame records the records' folder whatever it read): `provenance_refusal`'s path check is bypassed,
      a fail-open.

### A later registration

**Nothing in the rule breaks.**
- `window_games` and `window_weeks` start the window at the first kickoff after the registration time, whenever that
  is. My probe used a 2026-11-01 registration; the golden run and `tree_checks` use their own.
- Q1's week-7 sentence is conditional ("Registered before week 7's first Sunday kickoff…"), and the next bullet covers
  a later one.
- The error-rate figures are per-decision rates for a null group, so a shorter window does not move them. It costs
  POWER: a real effect is likelier to fail the d < -1 SE keep.

**ESPN's 2026 schedule** (read today, every game `pre`) has weeks 8-13 at 14, 15, 14, 13, 16 and 14 games.
- **A hash before week 8's first kickoff** (Thursday 2026-10-29, 20:15 ET = 10-30 00:15 UTC) puts weeks 8-11 in the
  window: 56 games.
- **A hash after week 8** (its last kickoff is 11-03 01:15 UTC) leaves weeks 9-11: 42 games, two above the 40 floor.
  - One game without a frozen universe row extends the window to week 12, which has 16 games.
  - The read then waits for week 13's first game (Thursday 12-03, 20:15 ET) and the 48-hour box, so early December
    instead of about November 26.
  - That is the rule working, not breaking. The owner should know the date can move.
- **A later hash** moves the window and its end:
  - after week 9, weeks 10-11 hold 27 games and the window runs to week 12 (43);
  - after week 10, it runs to week 13 (43);
  - after week 11 it stays under the floor at week 13 (30 games), and the test runs on what exists and says so, as Q1
    provides.
  - These counts assume every game holds a frozen universe row.
- **A hash after week 13's last kickoff (12-08 01:15 UTC) leaves no window at all.** `window_weeks` refuses ("no
  window week between week 14 and week 13"), and the provisional ships could never be confirmed or reverted by the
  test. That is not a concern at today's timing.

### Findings

**F1 (MINOR, fail-open: two residuals of C2(d)). Fix both in the batched round: the files are registered.**

**F1a: build-stores vouches for what it did not build.**
- `build_stores` vouches for the 2026 store, aux and ids that the deep folder holds AFTER its refresh, without
  checking that the refresh built them (S7, S7b). The procedure as written cannot produce that case, because the
  scratch folder holds only the registration's 2019-2025 pickles and the download is fresh. It takes a deviation.
- **The likely deviation comes from F2.** Suppose the registration's archive is copied INTO a live deep folder, such
  as the PC's `pc-deepcache`, as its durable home:
  - the live job then refreshes only 2026 in season (`default_seasons`), so the older pickles stay the registration's
    bytes;
  - a build-stores pointed at that folder keeps the live 2026 store and aux, with their raw downloads current, and
    vouches for them;
  - `prospective` accepts the result.
- *Fix* (a few lines, sketched): take the season's summaries from the two refresh calls,
  `sd = D.refresh(...)["seasons"][season]` and `sr = RD.refresh(...)["seasons"][season]`.
  - Refuse unless `"n" in sd` (the store was built) and `sr.get("built") is True` (the aux was built).
  - Preferably also refuse unless `summary["ids"]` is set, since the ids are rebuilt only when `players.csv` came
    down.
  - The guard's refresh stubs must then return built summaries. Add a case where a "skipped" or "error" summary is
    refused.
- **I tried the sketch in memory**, with no file touched (`W/final/e2e/f1_fix_probe.py`: the registered
  `build_stores` source plus those three lines, exec'd into the module):
  - a genuine build, whose refresh writes new bytes and says it built, is recorded and accepted end to end;
  - S7 is refused ("store {'error': 'no play-by-play'}, aux {'error': …}");
  - S7b is refused ("store {'skipped': 'no source changed and the store is current'}, aux {'skipped': …}").
- Q1 should also say: never a live deep folder, and never a copy of one.

**F1b: the dataset's stores are not bound.**
- `research/nfl_rush_dataset.py` loads every store, every aux and the ids from `DEEP_CACHE_DIR` (`load_all`). Its
  `meta.json` records none of their sha256: it holds the row counts, the output files, and now the code and versions.
- The frame takes its OUTCOMES from the dataset's rows (`outcomes_of`):
  - `y_*` come from Sleeper's `sl_act_*`, falling back to the pbp's `act_*`;
  - the touchdowns, and the pbp half of the played condition, come from the pbp's `act_*` only.
- So `provenance_refusal` binds the frame's own store reads to the build record, but it does not bind the dataset's.
  A dataset built against another deep folder passes; S1's accepted dataset meta named no store.
  - **A live folder holding the window's games** gives outcomes from live-built tallies, and nothing notices.
  - **A stale folder** (or the scratch folder before build-stores) gives a frame without the window's rows. The
    floor's extension reads that as "too few games":
    - it is refused until week 13 closes;
    - a frame then built for every week through 13 runs on whatever rows the dataset holds, possibly none.
- Nothing checks that the frame holds a row for each window game in the store's schedule.
- *Fix*: the dataset build records `input_shas` of every deep key it loaded, as the frame does. `provenance_refusal`
  then requires those to equal the frame's inputs for the same keys, which are already bound to the build record
  and to the registered frames.

**F2 (MAJOR, operational; a condition at the hash).**
- **What `prospective` needs, and what `validate` and `check2026` need before it:**
  - the registered frames, by sha256: `<SP>/nfl_rush_model/work/rush_frame_{val,test}.csv.gz` and their metas, about
    2 MB each;
  - the registration-time store and aux pickles, by sha256: `verify_stores` (2023-25) and `provenance_refusal`'s older
    stores (2019-25). They are in `<SP>/nfl_rush_model/deep3`, 38 MB.
- **The pickles cannot be remade.** Each carries its save time (`generated_at`), so a rebuild never hashes the same,
  and `verify_stores` and `provenance_refusal` compare the pickles' hashes. A frame rebuilt on rebuilt stores could
  match only if nflverse never revised a past season.
- **Both live only in this session's scratchpad.** `cmd_register` copies the code and the block into `prereg_code/`,
  not these. Neither the report nor the agreement log says where they will be kept.
- **The cost of losing them.** The decisive test can never run, and check2026's provisional ships stay on the board
  unvetted. The rule's own figure for a false provisional ship is 33% for at least one across the 35 decisions (the
  normal figure; 8-20% on the simulated ones).
- *Condition*: at the hash, before this session's scratchpad can go, archive both durably (for example on the
  data-only `sim-history` branch, or the PC).
  - Archive them outside any deep folder a live job writes (see F1).
  - Record the location and a sha256 manifest beside `prereg.json` (`deep3.sha256` exists).
  - Have Q1 name them as the scratch folder's source.
  - Commit the registered data with `prereg.json`: four of the seven registered data files
    (`research/data/nfl_rush/`) are still untracked.

**F3 (MINOR, wording; it fails closed).**
- Q1 gives the dataset build `--out research/data/nfl_rush_model/prospective_dataset`, a relative path, "in a
  registered tree".
- Run from the tree's root, that writes the TREE's copy, which `provenance_refusal` rightly refuses (S8a).
- The code is right. The sentence should name the repository's folder, the marker's `model_out`, by absolute path.

**F4 (NIT).**
- "Closed" means a week-`lw`+1 game is in the store. A week-`lw` game postponed past that point is left out of the
  window.
- That is no leak, since it is dropped whatever its outcome. One clause in Q1 would cover it.

**F5 (MINOR): 16 guard gaps on the final fixes.**
- The frozen code is right in each (above), so they do not stand between the bytes and the hash.
- But the batch will edit exactly these functions for F1, so add the cases with it:
  - one synthetic case per gap in `provenance_checks`, `build_stores_checks`, `tree_checks` and `chain_checks`:
    the aux, the ids, an older matchup store, a dataset meta with no code record, a meta edited after the frame,
    a missing store, a non-research module, an extended window, a non-empty destination, a subset shipped;
  - the frame's three provenance fields and the dataset's module list pinned the way DC1 is caught.
- Failing that, re-run `W/final/e2e/mut_e2e.py` on the batch. It kills 9 of the 16 in under five minutes.

### Before the hash

1. **The batch.** Add F1 (a and b) with guards that would have caught S7 / S7b and F1b, the F3 wording and the F5
   cases. Then re-freeze, rebuild both frames (the fit, rule and dataset files are in `BUILD_CODE`), re-run the dry
   run, and report the new rule sha.
2. **At the hash.** Do F2: archive, manifest and location.
3. If you send me the batch diff, I will confirm F1 against S7 / S7b. `W/final/e2e/c2d_e2e.py` re-runs in under a
   minute.

Work files are in `W/final/` (`W` = `<SP>/review_rush_rule_opus`):
- `tree/`: the frozen copy;
- `dryrun.json` and `guards_control.log`;
- `probe/c1_window.py`;
- `e2e/`: the scratch registration, the tree, the deep copy, `c2d_e2e.py` (with its log and json),
  `f1_fix_probe.py` and `mut_e2e.py` (logs `mut_e2e_*.log`);
- `idp/`: the identity proof;
- `mut/`: `mutate_final.py`, the queued `mutate_final2.py`, `mutants_final.json` and the logs.

## ROUND 4 CONFIRMATION (2026-10-29, 01:25-02:10 UTC, on the round-4 freeze)

**What I reviewed.**
- **The freeze** (`<SP>/nfl_rush_model/work/freeze_shas_r4.json`). My copy (`W/r4/tree`) and the live tree hash to all
  26 files at 01:25 and again at 02:00 UTC. `rule_block()` hashes to
  **92a99b8e791a7a98ec9e6da849ad9b07b751fd5cab6bd51c03f70d13fc0fbe61**.
- **My dry run on the copy reproduces the registration:**
  - frames val 906e9a7e… and test b3a28947…, unchanged;
  - 35 decisions, no stale frame, no blocked input, 17 code and 7 data files;
  - `stores` holds all 17 pickles by sha256 and by content digest (1.0 s);
  - `ids_positions` holds 6,476 players, and `registered_positions` of that map returns exactly `positions()` on deep3.
- **Guards on my copy:** model **224/0**, data **198/0**. pyflakes is clean on the five changed files and both guard
  files.
- **The diff against my round-3 copy.** Five registered files changed: `nfl_rush.py` (31 lines), the frame (51), the fit
  (322), the rule (118) and the dataset build (43). I read every hunk, and each is one of the listed items. The other
  12 code files and the 7 data files are byte-identical.

**Rules kept.**
- I read no held outcome. The end-to-end run hashed and re-saved pickles but never inspected them, nflverse was
  stubbed, and nothing that fits on 2025 ran.
- I made no git writes and touched no registered file. The scratch registration (`W/r4/e2e/records`) and its tree are
  outside the repository. This section is my only repo write.

### Verdict: **AGREE TO HASH**

- **Both of my conditions are met in the bytes:**
  - F1a and F1b (condition 1) are fixed, and both refuse on the real path;
  - F2 (condition 2) is carried by the code (content digests, `--older`, the manifest) and by the runbook. The archive
    itself is the act at the hash.
- **No BLOCKER or MAJOR remains.**
- **Fable's B1 was a real blocker, and I missed it in round 3.** I had read `run_build`'s
  `max_week = TEST_MAX_WEEK if max_week is None else max_week` without following it: with no flag, 2026 stopped at
  week 7, so the decisive dataset could hold no window row. It is fixed and refuses on the real path (N5, N6 below).
- **What is left is outside the hashed block, or in the guards.** It is two runbook items from the restricted-files
  decision (R1, R2) and two guard gaps (G1). None of them moves the rule sha.

### Item by item, on the real path

**Setup** (`W/r4/e2e/c2d_r4.py`, log `c2d_r4_base.log`):
- a scratch registration of these bytes (92a99b8e…), with its registered tree;
- a scratch deep folder copied from deep3;
- `build_stores` and `provenance_refusal` / `_provenance` run for real. The refresh was stubbed either to BUILD (new
  bytes and built summaries) or left real with nflverse unreachable.

| item | case | result |
|---|---|---|
| F1a | S7: a store and aux saved BEFORE build-stores, nflverse unreachable | refused: "the refresh did not BUILD season 2026's store and aux" |
| F1a | S7b: the same with raw downloads current (upstream "unchanged") | the moved-aside store is rebuilt from the raw file; my placeholder crashes the registered parser; nothing recorded |
| F1a | S7c: the ids not rebuilt | refused |
| F1a | an aux missing after the refresh / an unregistered module loaded | refused / refused |
| F1b | N1: the dataset meta records no stores | refused |
| F1b | N2 / N3: the dataset read another 2026 / 2023 store than the frame | refused / refused |
| F1b | N4: the dataset records no 2026 aux | refused |
| B1 | N5: a dataset capped at week 7 / N6: missing window week 10 | refused, naming [8, 9, 10, 11] / [10] |
| F2 | E: an older store re-saved with the same content, frame on it | ACCEPTED, by content digest (by design) |
| F2 | E': the same with one value changed | refused: "its content differs in ['n']" |
| F2 | `--older 2024` rebuilt faithfully / with content changed / `--older 2026` | recorded / refused, naming `n` / refused |
| F2 | `verify_stores` with 2024 re-saved / changed | passes / refused, naming the part |
| F2 | the manifest's 21 files against their sources; `sha256sum` lines | all equal; 21 lines (2 frames, 2 records, 17 pickles) |
| F3 | the tree-relative dataset; `registered-tree`'s printed procedure | refused; `--out` by absolute path, `--all-weeks` |
| C2 | the 2026 store / aux / ids re-saved after the build; the record re-made; the dataset meta edited | all refused |
| C2 | the registered folder; `<records>/prospective_dataset` linked to it; an unregistered or missing code record | all refused; the records' folder through a link is accepted |
| F5e | the registered tree's report | its block (restored from `prereg_code/`) hashes to 92a99b8e; `prereg_status` (True, 'registered') |
| m7 | `registered_positions(map)` against `positions()` on deep3 | equal (6,476 players, int keys) |
| window | off the registration-time 2026 store | refused |

F4 is disclosed in Q1. One more refusal is new: the coverage check now refuses a week whose props log is an input but
which holds no frozen universe row (Fable's m5).

### Targeted mutants, on the changed functions only

**Batch 1** (`W/r4/mut/mut_r4.py`): 56 mutants on the round-4 hunks; the control was green (224/0).

**50 are caught.** The 6 survivors:
- **WR2** reads the grid's BOTTOM 32 values instead of its top 32. This is near-equivalent. With `GRID_LO` -128, the
  bottom 32 values are where wrapped mass lands, so it reads the folded mass itself rather than its proxy at the top.
- **RG2** drops `register`'s missing-pickle refusal. This is equivalent in practice: `stale_frames` refuses a missing
  pickle first, and a pickle whose bytes match the frame's record unpickles.
- **FR1** drops `write_frame`'s empty-rows refusal. This changes the message only: an empty frame still cannot be
  written, because `rows[0]` raises.
- **FB1** drops the move-aside. This costs liveness: a re-run after a refusal past the refresh stays stuck. It opens
  no hole on the real path, because the refresh's "built" summary is truthful. My harness flips it only through a
  stub that claims "built" without writing (`c2d_r4_mut.log`).
- **PR2** binds only the vouched keys of the dataset to the frame's. This is a guard gap; my harness kills it (N3 is
  accepted).
- **PM3**: `fit_distributions` ignores the registered positions. This is a guard gap: the parts_for guard stubs
  `fit_distributions`. The frozen code passes them through.

**Batch 2: F5 re-checked** (`mut_r4_f5.py`). I put my 16 round-3 guard gaps on the round-4 text. All 16 are now
caught: 13 re-run here, plus the three frame fields, which are FR3-FR5 in batch 1.

**Tally:** 69 mutants applied (56 + 13), 63 caught. Of the 6 survivors, 4 are equivalent or liveness-only and 2 are
guard gaps (G1).

### The restricted columns (the separate question)

**Nothing in the rule changes.**
- `y_played` is written to the frame by `outcomes_of` and read by nothing in the rule, the fit or `nfl_rush`.
- "Played" is `y_cond_played`: Sleeper's `gp` or a pbp touch.
- The block already says no restricted column selects a row (the `SELECTION_COLS` guard), and Q10 keeps PFR and NGS
  out of every fitted feature. I did not re-audit Q10's list this round; its guards pass.

**What the decision does change is custody, outside the block:**
- **R1 (MINOR, runbook; fails closed).**
  - `.gitignore` now keeps `rb_games_2023_2026.csv.gz` and `qb_games_2023_2026.csv.gz` out of git. They are REG_DATA,
    so `prereg_status` refuses every command until they are back at their registered bytes ("... no longer hashes to
    the registration").
  - `registered-tree` copies them from the checkout, which a fresh clone lacks.
  - The manifest (21 entries: the frames, their records, the 17 pickles) and the decisive-run steps do not list
    them.
  - Add them to the archive list and to step 2 of the decisive run: restore them, checked against `prereg.json`'s
    `data`, before `registered-tree`.
- **R2 (MINOR, `.gitignore`).** `research/data/nfl_rush_model/prospective_dataset/` will hold the same restricted
  columns, inside the records folder whose other files are committed. Ignore it now.

### Findings

- **R1 and R2** (above): runbook and `.gitignore`. Neither is hashed, so both can be fixed at or after the hash.
- **G1 (MINOR): two guard gaps on code that is right.**
  - PR2: add a provenance case where the dataset recorded a different OLDER store.
  - PM3: add a `fit_distributions` case whose `pos_sl` differs from the ids pickle's.

  The registered bytes no longer change after the hash, so these protect only a future re-registration.

Work files are in `W/r4/` (`W` = `<SP>/review_rush_rule_opus`):
- `tree/`: the copy;
- `dryrun.json`, `guards_model.log` and `guards_data.log`;
- `e2e/`: the scratch registration, the tree, the deep copy, `c2d_r4.py` and its logs;
- `mut/`: `mut_r4.py`, `mut_r4_f5.py` and their logs and json.
