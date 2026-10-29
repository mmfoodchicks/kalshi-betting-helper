# Review: the NFL rushing data layer (RB + QB) -- Sonnet reviewer

Reviewer: Sonnet (independent; no contact with the Opus reviewer). 2026-10-23.
Under review: `nfl_rush_data.py`, `nfl_rush_profiles.py`, `research/nfl_rush_dataset.py`, `tests/guards_nfl_rush_data.py`,
`research/data/nfl_rush/*`, `research/reports/nfl_rush_data.md`. Frozen foundation checked: `nfl_matchup_data.py`,
`nfl_matchup_profiles.py`, `research/data/nfl_matchup/dataset_2023_2026.csv.gz` hash to `8511346956bb...`, `d2007503a6d8...`,
`766a60ed11ee...` (unchanged, `git diff` empty). Every file hash in report section 10 matches the tree.

All my code is in `$SP/review_rush_data_sonnet/scripts/` (`$SP` = the scratchpad). The raw re-derivations (`indep_lib.py`,
`indep_tally.py`, `a1_*`, `a3_*`, `a4*`, `a5_*`, `a6*`, `c_*`, `h_*`) read the nflverse CSVs directly and import nothing from the
builder. The leakage, degradation and mutation scripts (`b_*`, `d_*`, `e_mutate.py`) run the production code on altered inputs.

## 1. Verdict: AGREE AFTER FIXES

The data is, with two exceptions, right and leak-free. I recomputed it from raw with my own code and it agrees:

- Counting, against Sleeper's box score and against PFR.
- 141 hand-checked dataset cells.
- Every split-half and persistence figure I could rebuild, except the RB usage and receiving rows.
- The DECAY arithmetic (0.6015, 0.6001).
- Kalshi's settlement convention.
- Leakage: poisoning every source row at (season, week) and later changed no feature in 4 weeks (3,817 RB rows x 846 feature columns
  and 1,378 QB rows x 322, counting the clean builds' rows).
- The full dataset rebuilds byte for byte.

Two numbers are wrong, and they sit in headline fields:

- The RB reliability/k tables were measured on units that include wide receivers and tight ends: 39% of the usage units (332 of 860) and 54% of the receiving units (330 of 614) (B1).
- Every PFR snap share carries a team-snap denominator inflated by about 10% (B2).

The proposed job diff (8.1) would not build the prior-season aux stores on a deployment that already has the agreed stores, and
it can strand raw files for good (M1). The guard suite passes 87/87 but pins neither the dataset's cell wiring nor the
measurement code; 27 of my 45 single-point mutants survive (M2).

### Required fixes before AGREE TO SHIP

1. **B1.** Restrict the RB reliability/persistence unit set to RB/FB, re-run `measure`, regenerate `reliability.json`,
   `_K_DEFAULT`, the dataset (the shrunk receiving and line-yards columns change), `schema.json`, and report 5.1/5.2. Add a guard
   (a WR with a carry and 15+ targets must not become an RB unit).
2. **B2.** Make the PFR team snap total what its docstring says (the most snaps any offensive player took, or the implied total
   from the 100% player). Rebuild the aux stores and the dataset. Add a guard with a low-snap player whose rounded percent implies a
   larger total.
3. **M1.** Fix section 8.1: decide the rush backfill from the aux stores (`R.load_aux(s) is None`), not the agreed store. Put the
   raw-file cleanup in a `finally`. Split the rush backfill and daily `try` blocks. Make the job's result say when the rush
   refresh failed.
4. **M2.** Add the missing guards (list in section 4). At least these:
   - the builder's team/opp wiring and the traded-back call;
   - a two-season fixture through `RP.build` and the builder;
   - the split-prior sign;
   - a measurement-side guard for `unit_profiles` / `_gate`.
5. **m5/m6.** Correct the schema kinds and meanings (8 QB columns, the three `ol_starters_*` columns, `played`, `in_sleeper`,
   `sl_qb1`).

I can re-verify B1/B2 quickly: the scripts re-run in about two minutes.

## 2. Claims table

Their figure first, mine second. AGREE means my own computation reproduces it within rounding.

### Counting, against Sleeper's box and against PFR (report 4.1-4.5)

| claim (report section) | mine | verdict |
|---|---|---|
| 4.1 QB: 2,046 player-games, attempts exact 100.0%, yards 99.76% | 2,046; 100.0%; 99.76% | AGREE |
| 4.1 QB attempts exact without kneels 63.4%, without aborted 88.3%, without scrambles 35.0% | 63.4%, 88.3%, 35.0% | AGREE |
| 4.1 all 749 QB-games with a kneel match with kneels counted, none without | 749 of 749 with, 0 without | AGREE |
| 4.1 RB: 5,106 player-games, 99.98% / 99.96%; without aborted 99.67% | 5,106; 99.98% / 99.96%; 99.67% | AGREE |
| 4.1 dataset's own `act_rush`: QB 100.0% / 99.75% (1,990 rows), RB 99.98% / 99.96% (4,915 rows) | 1,990: 100.0% / 99.75%. RB: 99.98% / 99.96% (4,915 = rows with `played`=1 and Sleeper gp>=1) | AGREE |
| 4.1 sacks in no rushing figure (0 sacks with a rusher id) | 0 in 2023, 2024 and 2025 | AGREE |
| 4.2 QB rushes 2,285 / 2,352 / 2,215; designed 453 / 518 / 357; sneaks 295 / 267 / 259; scrambles 1,029 / 1,060 / 1,087; kneels 428 / 405 / 433; aborted 80 / 102 / 79 | identical, and the five classes sum to the total each season | AGREE |
| 4.2 RPO keeps 56 / 29 / 67; RPO dropbacks 555 / 349 / 433 | identical | AGREE |
| 4.3 scrambles vs PFR 2024: 1,012 vs 1,007, 47 QBs, r .999, 72% exact, 100% within 3 | 1,012 vs 1,007, 47, r .999, 72.3%, 100% | AGREE |
| 4.3 scrambles vs PFR 2025: 1,021 vs 1,028, 45 QBs, r .998, 73%, 98% | 1,021 vs 1,028, 45, r .998, 73.3%, 97.8% | AGREE |
| 4.3 FTN RPO designed runs 888 / 1,224 / 839 / 1,585 vs PFR 276 / 247 / 282 / 309 (2022-25) | FTN 893 / 1,235 / 839 / 1,585; PFR 276 / 247 / 282 / 309 | AGREE (within 1%) |
| 4.3 PFR carries = official rushes 100% / 99.91% / 99.96%; `rushing_yards` differs from `yards_gained` on 2 / 3 / 1 | 100% / 99.91% / 99.96%; 2 / 3 / 1 of 14,606 / 14,686 / 14,601 | AGREE |
| 4.4 RB location blank 0.00-0.05%, middle 25-31%, left/right with no gap 0 | 0.01-0.05%, 24.9-31.1%, 0 | AGREE |
| 4.5 sneak rule precision .989 / .986 / .992 / .996, recall .945 / .942 / .936 / .896 | identical | AGREE |
| 2.3 NGS weekly rows all have >=10 attempts; 73.1% (2018), 74.1% (2025) of RB runs in games with a row; 99.6-100% of 10+ games | min 10; 73.1%, 74.1%; 99.62%, 100% | AGREE |
| 2.4 / 4.3 / section 6: 2025 Out listings 1,342, of which 93 RB | 1,342 total; 83 RB + 10 FB | AGREE |

### Split-half reliability (5.1), odd vs even weeks, 2018-2025 pooled, own code

| claim | mine | verdict |
|---|---|---|
| `qb_dsg_rate` r .917, 282 units, n 403, k 36.34 | .917, 282, 403.4, 36.34 | AGREE |
| `qb_scr_rate` r .755, 282, 247, k 79.94 | .755, 282, 246.9, 79.93 | AGREE |
| `qb_sneak_rate` r .551, k 328 | .551, k 328.2 | AGREE |
| `def_dir_ypc_dev` r -.009, 1,717 cells, n 26.44 | -.009, 1,717, 26.44 | AGREE |
| `def_box_ypc_dev` r .013, 741 | .013, 741, 62.0 | AGREE |
| `def_scrambles_oe` r .038, k 359 | .038, 256, k 359.6 | AGREE (k is poorly determined, see m10) |
| `def_rb_ypc` r .351 / k 333; `def_light_share` .759 / 163; `def_heavy_share` .755 / 167; `def_nickel_share` .885 / 67; `def_dl3_share` .993 / 3.7; `tm_neutral_run` .574 / 211 | identical to 3 digits | AGREE |
| `rb_ypc` r .254, 502 units, k 231 | .257, 499 units, k 228.6 (3 non-RB units in theirs) | AGREE (approx.) |
| **`rb_carry_share` r .966, 860 units, k 5.99** | **.903 (RB only, my own count, 529 units, n 169.6, k 18.3)**. Their pipeline restricted to RB/FB: .905, 528 units, k 17.95 | **DISAGREE (B1)** |
| `rb_target_share` .895 / k 27.2; `rb_sit_all` .977 / 10.4; `rb_rz_share` .888 / 3.9; `rb_gl_share` .846 / 2.3 | RB only: .792 / 59.9; .905 / 46.2; .739 / 10.7; .719 / 4.9 | DISAGREE (B1) |
| receiving: `rb_catch` .441 / 46.9; `rb_ypt` .389 / 58.2; `rb_yac_rec` .571 / 19.6; `rb_adot` .921 / 3.1; `rb_chk_share` .892 / 4.5; `rb_screen_share` .635 / 21.3 | RB only: .032 / 877; .143 / 172; .181 / 101; .538 / 23.1; .348 / 52.1; .427 / 37.4 | DISAGREE (B1) |
| `rb_line_yds` .436 / 102 | RB only: .260 / 225 (three non-RB units with 0 line yards swing it) | DISAGREE (B1) |
| alternative splits (first 9 weeks vs the rest; three random 9/9 week splits), same code | QB dsg rate .89-.93; QB scr rate .75-.79; def dir/box ypc dev -.04 to +.07; def scramble OE .02-.09 (k 117-610); RB carry share (RB only) .77-.90 | consistent; the OE k is not pinned (m10) |

### The DECAY rule (5.3)

| claim | mine | verdict |
|---|---|---|
| the ten pooled r (`def_light_share` .446 ... `tm_neutral_run` .467) and every per-pair r and n_units | identical, all 70 pair r | AGREE |
| tendency pooled r .602; .600 without 2024->25 | .6015; .6001 | AGREE |
| team tendencies alone .535; player traits .70 | .5353; .7009 | AGREE |
| DECAY 0.75 | rule applied as written | AGREE; the margin is 0.0015 and convention-fragile: n-weighted mean of the pair r is .558, Fisher-z mean .665, median of the ten .530 |

### The agreed layer, Kalshi, process, production

| claim | mine | verdict |
|---|---|---|
| 9.8 `nfl_matchup_profiles.qb()["scramble"]` is 0.0 raw for every QB, nothing downstream reads it | all 168 QBs in the 2025 week 10 snapshot have `v` 0.0 (Allen 637 dropbacks, eb .0156; Jackson 504, .0183; Hurts 582, .0166); repo-wide grep finds no reader (`nfl_matchup.qb_sens` reads `epa_db` and `vs`; `qb_metrics` does not read `scramble`) | AGREE |
| 0.3 / 9.6 Kalshi KXNFLRSHYDS includes kneels and scrambles, 239 of 239 ladders; kneels 12 discriminating, scrambles 145 | 239 of 239; without kneels 227; without scrambles 94; 12 and 145 | AGREE. Two additions in m4 |
| 8.4 board snapshot 3.0 s and 137 MB; 228 profiles in 0.4 s | 3.85 s wall and 133 MB peak including 349 profiles in 0.60 s | AGREE (this box is slower) |
| 2.1 aux build 3-7 s a season, 46 MB for two | 3.5 / 3.8 s; 53 MB peak for two | AGREE |
| 0.3 field counts 173 / 44 / 152 / 28 / 13 / 10 | identical | AGREE |
| 6 dataset 6,041 x 896 and 2,201 x 378; byte-identical rebuild | identical row counts; a full rebuild gives the same sha256 for both CSVs and `schema.json` | AGREE |
| 7 guards 87/87, agreed guards 149/149 | 87 passed; 149 passed | AGREE |
| 7 mutation 40 of 41 caught | not re-run (their harness); my own 45: 18 caught, 27 survived | see section 4 |
| 6 "as of the game ... `p_games_cur` equals the earlier played weeks on 6,041 of 6,041" | not enumerated; leakage section below | AGREE by other means |
| F: first-run persistence identical to final | `persistence.first.json` byte-identical to the shipped file; `reliability.first.json` k and pooled identical | AGREE |

## 3. Findings

### BLOCKER

**B1. The RB reliability / k tables are measured on a unit set that includes WRs and TEs (39% of the usage units, 54% of the receiving units).**
Evidence. `research/nfl_rush_dataset.py` `unit_profiles` builds an "rb" unit for every key in `T.rb`, and `RP.add_game`
files every non-QB rusher there (WRs and TEs with a jet sweep). `_gate` passes a usage metric on `car_ok or tgt_ok`, and a
receiver passes the 15-target gate. Running the shipped pipeline and splitting the units by the agreed crosswalk position
(`$SP/review_rush_data_sonnet/scripts/f1_units_all.py`, table in `rb_units_nonrb.json`):

```
rb_carry_share         units 860 (332 WR/TE)   shipped r .966 k 5.99    RB/FB only r .905 k 17.95  (528 units)
rb_sit_all             units 860 (332)         shipped r .977 k 10.4    RB only r .905 k 46.2
rb_target_share        units 860 (332)         shipped r .895 k 27.2    RB only r .792 k 59.9
rb_catch               units 614 (330)         shipped r .441 k 46.9    RB only r .032 k 877
rb_ypt                 units 614 (330)         shipped r .389 k 58.2    RB only r .143 k 172
rb_yac_rec             units 609 (326)         shipped r .571 k 19.6    RB only r .181 k 101
rb_adot                units 614 (330)         shipped r .921 k 3.06    RB only r .538 k 23.1
rb_chk_share           units 305 (167)         shipped r .892 k 4.48    RB only r .348 k 52.1
rb_line_yds            units 1004 (6 = 3 units x 2 paths)   shipped r .436 k 102    RB only r .260 k 225
```

24 metrics are affected with a k change of 2x or more: the usage and on-field shares, the per-game rates, receiving, `rb_line_yds`.
My own count from raw (RB/FB only, 529 units, r .903, k 18.3) reproduces the RB-only figure, not the shipped one. The two causes:
- Cluster at the origin. WR/TE units sit at a near-zero carry share (or an exactly zero on-field share) in both halves.
- A different base rate. Their targets and yards per target differ from backs'.

Both inflate a pooled Pearson r. The report's own table shows 860 usage units against 502 efficiency units without comment.

Consequences:
- The shrunk RB receiving fields in the dataset (`p_receiving_catch`, `_ypt`, `_yac_rec`, `_adot`, `_screen_share`,
  `_checkdown_share`) and `p_rushing_line_yds` / `p_rushing_line_line_yds` are shrunk with k's 2-17x too small. A back with 40
  targets puts about 0.41 weight on his own yards per target instead of about 0.19.
- `_K_DEFAULT`, `reliability.json`, and report 5.1/5.2 carry the wrong figures. 5.2 says "Usage is known quickly: carry share r
  0.97 (k 6 carries), on-field shares 0.88-0.98"; for backs it is r .90 (k 18) and on-field .69-.93.
- Persistence, DECAY and the team/QB metrics are not affected (my independent persistence matched theirs exactly).

The rule text says "Running backs"; the implementation counts every non-QB rusher. This is an implementation deviation, not a
rule change after the fact.
Fix. Gate `kind == "rb"` units on `pos in ("RB", "FB")` in `unit_profiles` or `_gate`; re-run `measure`; regenerate everything
downstream; correct report 5.1/5.2; add a fixture guard.

**B2. The PFR team snap total is inflated, so every PFR snap share reads low.**
Evidence. `nfl_rush_data.read_snaps`: `rec["team"] = max(rec["team"], int(round(snaps / pct)))` over every player. The docstring
and the guard label say "team snaps = the most any offensive player took (a lineman's 100%)". A player with 1 snap at 0.01
implies 100. In 2024 every one of 544 team-games has a player at pct >= 0.99, so the true total is the maximum snap count.
`$SP/review_rush_data_sonnet/scripts/f_teamsnaps.py`:

```
2023 team-games 544 | implied == max snaps: 32 | implied > max+5: 141 | mean excess 6.6 snaps | max 33
2024 team-games 544 | 47 exact | 162 over +5 | mean 7.4 | max 33
2025 team-games 544 | 37 exact | 149 over +5 | mean 7.0 | max 33
2026 team-games 186 | 11 exact |  56 over +5 | mean 7.3 | max 33
2024_01_DAL_CLE CLE: QB, T, G all 76 snaps at 100%; D'Onta Foreman 1 snap at 0.01  -> aux "team" = 100
```

`f_snapshare.py`, 40 random 2025-26 rows: the dataset's `p_usage_snap_share_raw` equals my replication of the code's method
in 40 of 40. Against the share on the true total it reads 9.5% low on average (worst -16.8%): for example dataset 0.494 vs
0.553, 0.346 vs 0.388, 0.744 vs 0.815. Across the dataset's 5,501 RB rows with PFR snaps, `act_pfr_team_snaps` exceeds the maximum
snap count by 10.1% on average (median 3.8%, 41% of rows over 5%).

This hits `usage.snap_share` and `recent.snap_share` in all 6,041 RB rows. It is the only snap source for 2026, and it also feeds
`ol_starters_missed_last_game` (the threshold is 0.5 x this total) and the `rb_snap_share` k. The clean fixture has exact
percents, so no guard can see it (my mutant D1 survives for that reason).
Fix. `team = max(offense_snaps)` over the team's rows (or `round(snaps/pct)` taken from the player with the highest pct). Rebuild
the aux stores and dataset. Add a fixture row with a 1-snap, 1% player.

### MAJOR

**M1. The proposed `nfl_data_job` diff (8.1) does not build the prior-season aux stores on an existing deployment, and it can strand raw files.**
Evidence. `back` is computed from the agreed store only (`D.load_season(s) is None`), and the rush backfill is gated on `back`.
`$SP/review_rush_data_sonnet/scripts/d_job_sim.py` runs the diff's logic against stubs:

```
existing deployment (agreed stores 2024, 2025, 2026 present): back=[]  ->  R.refresh seasons = [2026] only
fresh deployment: D.refresh([2024,2025], keep_raw=True), D.refresh([2026]), R.refresh([2024,2025], keep_raw=False), R.refresh([2026])
fresh, the DAILY agreed refresh raises: job raises; the manual raw-file removal never runs
fresh, the rush backfill raises: ledgered RUSH-refresh; NEXT run back=[] -> R.refresh([2026]) only
```

- Today (October, `default_seasons` gives [2026]) the aux for 2024 and 2025 is never built on a server whose agreed stores
  exist, and the raw files were deleted by the original backfill. 2026 has no participation, so every participation-derived
  field of a 2026 board would come from aux stores that are not there.
- The `keep_raw=True` change leaves the backfill's raw files (pbp, participation, FTN: 77.3 MB for 2024, 76.3 MB for 2025,
  153.6 MB together, on a 1 GB disk) on disk if the daily agreed refresh raises after it. The next run has `back == []`, so the
  removal loop never reaches them: they stay forever.
- A rush-backfill failure is permanent for the same reason, and the job returns `rush: None`, which looks like "nothing to do".
- One `try` wraps both rush calls, so a backfill failure also skips the daily refresh.

Fix. `back_rush = [s for s in (cur-2, cur-1) if s not in daily and R.load_aux(s) is None]`, independent of the agreed store.
Cleanup in a `finally` that looks at the raw directory, not at `back`. Separate try blocks. Put `{"rush_error": ...}` in the result.

**M2. The guard suite is blind to the dataset's wiring, to `RP.build`, and to all measurement code.**
Evidence. 27 of my 45 single-point mutants pass 87/87 (section 4). The ones that matter:
- T3: the builder calls `S.rb(pid)` instead of `S.rb(pid, team)`. This is the agreed review's F1 class at the dataset layer.
- T4/T5: the builder reads the defense or team-context profile of the wrong side.
- U2: `RP.build` counts the cutoff season again as a prior (the guards never call `build`).
- P1: the split-prior offset has the wrong sign; every direction/box/personnel/formation cell is shrunk toward it.
- G2/G3: nickel threshold; leading/trailing swapped.
- C2: scramble yards not added to the QB line.
- M1-M4: the measurement code (`_rel`, `_gate`, the decay rule, the odd/even split) has no guard at all.

The current data is right where I checked (141 cells, 0 real differences), so these are gaps, not live bugs. But B1 is exactly
the kind of bug M2 lets through. The builder guards only check stability (rows do not move when another game is removed).
Fix. Pin cell values in the builder (one RB row and one QB row against hand figures, including a traded back); add a two-season
fixture through `RP.build` and `run_build`; pin the split prior and the lead/trail and personnel shares; add a WR with a carry to
the fixture for the measurement.

### MINOR

- **m1.** `nfl_rush_data.py:676` `except OSError: pass` on the raw-file cleanup. The guard suite only flags `except Exception: pass`, and
  the agreed layer does the same, but a failed removal on the 1 GB disk is invisible. Note `RUSH-rm` for anything but ENOENT.
- **m2.** A missing, stale or other-store aux degrades silently at the profile level. `d_degrade.py` (2025 aux missing or built on
  another store, snapshot as of 2026 week 7): the counter `aborted_unknown` goes 0 -> 13,079 run plays whose aborted flag is
  unknown, and 87 aborted snaps of 2025 (223 -> 136 recognised) read as designed runs; nothing in any profile says so. With no aux at
  all Lamar Jackson's designed runs per game read 3.11 instead of 2.99, the OL starters come back empty, and `status` honestly reads
  "unknown". Stamp `aux: aligned | stale | missing` per season in each profile.
- **m3.** Truncated plain-CSV sources are accepted without a record. A PFR rush file cut at 60% builds an aux with 173 of 272 games and
  `missing: []`; injuries 13 of 18 weeks; snaps 172 of 272 games. A truncated pbp .gz raises `EOFError` (loud). `D.fetch` is
  atomic so this needs a bad upstream file, but a per-block week-coverage record (and a check against the store's weeks) would make
  it visible.
- **m4.** Kalshi inference, two additions. The data cannot discriminate aborted snaps (`no_abort` is also 239/239, zero discriminating
  ladders), so "aborted snaps settle as rushing" in the 0.3 definition is untested. Sacks are testable and the data supports the
  claim: adding sack yards fits 128 of 239 ladders, and 111 discriminating ladders all favour excluding them. Report 9.6 and 0.3
  should say both.
- **m5.** Schema labels (`schema.json`, taken from the first row written, which is a week-1 row with no prior):
  - Eight QB columns read `observed` where the profile field is `modelled` (shrunk): `q_scramble_vs_{pressure, no_pressure, man,
    zone, single_high, two_high}`, `q_scramble_yds_per_pressured_db`, `dq_pressure`. Report 0.3 lists the right kinds.
  - The three `ol_starters_*` columns carry the week-1 no-data sentence as their `meaning` ("no game this season before the
    cutoff"), and a `source` that names only PFR (`out_report` reads the injury report too).
  - `dq_containment_proxy_*` are `modelled` / `observed`. The report's 3.4 "(kind proxy)" exists only on a wrapper dict that never
    reaches a column; the name and basis say proxy.
  - The five `p_recent_*` RB columns have no description beyond their name; `tm_rb_out_share` and the `*_games_*` columns have an empty unit.
- **m6.** `played` (an outcome: it comes from the game's own plays), `in_sleeper` and `sl_qb1` (Sleeper's post-week snapshot) are labelled
  `observed`. `sl_qb1 == 1` implies `played == 1` on 1,817 of 1,817 QB rows. The model's whitelist (`nfl_rush.FEATURES`) does not use
  them, so nothing leaks today, but "everything except `act_*` and `sl_act_*` is a feature" would. Rename `played` to `act_played`
  and label the other two as `sl_` / not provably pre-kickoff.
- **m7.** Odd/even-week split-half is optimistic for role-driven quantities that will be used forward in time. First-9-weeks vs
  rest gives RB carry share r .77 / k 53 against odd/even .90 / 18, and RB ypc k 274 against 229. QB rates are stable (36 vs 44,
  80 vs 81). Disclose it next to the usage k (this is the agreed method; the issue is its fit to usage).
- **m8.** The dataset's shrunk `value` columns use the 2018-2025 k table, so a 2025 choose-on-validation sees k fitted partly on 2025.
  `k_fit_only` exists and `_raw` / `_n` are in the file; say in the dataset notes that re-shrinking with `k_fit_only` is the
  clean route.
- **m9.** "The injury status is the pre-kickoff game status." By `date_modified` (the five seasons I checked, 2018 and 2021-2024,
  about 27,000 rows) 6 rows were modified after kickoff: 2 in 2018, 3 in 2021 (no game status), 0 in 2022 and 2023, and one in 2024
  (BAL week 1, Kyle Van Noy, Out, modified 2.5 h after the Thursday opener's kickoff). The 2025 and 2026 files have no `date_modified`, so it is not verifiable
  there. The poison test shows the only same-week inputs read are the week-W injury rows (positive control below).
- **m10.** `def_*_oe` k is not pinned by the data: the scramble OE reads k 359 on odd/even and 117-610 on the other splits.
  It is near noise either way; the heavy shrinkage is right, but quote it as a range.
- **m11.** CC BY-SA. The credit and share-alike notice are in `meta`, `reliability`, `persistence`, `sneak_rule`, `checks` and the aux
  pickles. `schema.json` has no `license` block and the two CSVs (the FTN-derived tables that matter) carry nothing in-file, so
  "carried in every committed table's license block" is not literally true. Add the block to `schema.json`.
- **m12.** The 5.2 wording "is noise" for direction/side/box splits: 26 carries a cell cannot separate a true spread below about
  0.3 yards a carry from zero. "Not measurable at this sample, bounded below ~0.3 yd/carry" is what the data supports.

### NIT

- `fetched` in `R.refresh` is appended to and never read.
- `sit_flags` short-yardage is `ytg <= 2`, the profile numerator `0 < ytg <= 2`; consistent per ratio, immaterial.
- NGS weekly rows are RB 5,450, HB 43 and FB 8, not "RB/FB only"; they join by gsis so nothing moves.
- Report 4.3 FTN RPO runs 888 / 1,224 vs my 893 / 1,235 (2022, 2023): under 1%.

## 4. Mutation table

Own mutants, applied one at a time to a copy of the tree (`cp` of the repo without `.git`, `python3 -B`, `__pycache__` cleared,
guard run per mutant). The comment-only control stays green. The source is `$SP/review_rush_data_sonnet/scripts/e_mutate.py`;
results in `mutation_results.json`. 45 mutants, **18 caught, 27 survived** (the control makes 46 runs).

| id | mutant | result | survivor class |
|---|---|---|---|
| S1 | sack with a rusher id becomes a QB designed run | CAUGHT (6 checks) | |
| S2 | sack dropped from the QB's dropbacks | CAUGHT | |
| K1 | kneel yards zero in the official total | CAUGHT | |
| K2 | kneel yards zero in the class yards | CAUGHT | |
| K3 | QB kneel line skipped | CAUGHT | |
| C1 | scrambler's per-player scramble line dropped | survived | real gap: dataset `act_scr` unpinned |
| C2 | scramble yards not added to the QB line | survived | real gap: QB scramble ypa, yds/dropback, scramble-yards OE all unpinned |
| C3 | scramble rate over exposure | CAUGHT | |
| R1 | every QB designed run counts as an RPO keep | survived | real gap: the fixture has no non-RPO QB designed run |
| R2 | RPO plays that became passes not tallied | CAUGHT | |
| T1 | `recent.*` not team-scoped | survived | real gap: no traded-back check on `recent` |
| T2 | `rb_room_out` reads 2 games not 4 | CAUGHT | |
| T3 | builder calls `rb(pid)` without the team | survived | real gap (F1 class at the dataset layer) |
| T4 | builder reads `defense_run(team)` not `(opp)` | survived | real gap: no cell value pinned |
| T5 | builder reads `team_context(opp)` | survived | real gap |
| U1 | tallies include the cutoff week (`w > max_week`) | CAUGHT (by the constructor's ValueError) | |
| U2 | `RP.build` counts the cutoff season again as a prior | survived | real gap: `build` never called |
| U3 | injury report read from the previous week | CAUGHT (3) | |
| U4 | status reads the practice code | CAUGHT | |
| L1 | defense OE not leave-one-out on the denominator | CAUGHT (2) | |
| L2 | observed not weighted like expected | survived | equivalent in the one-season fixture; real gap |
| L3 | facing QB's rate never shrunk (k 0) | survived | equivalent in the fixture (k None there); real gap |
| P1 | split offset sign flipped | survived | real gap: every split prior unpinned |
| P2 | scramble-condition prior divides by the ratio | CAUGHT (a ZeroDivisionError crash, not an assertion) | |
| P3 | league RB prior includes QB designed runs | survived | real gap (fixture has one such run) |
| P4 | proxy kind not preserved on fields | CAUGHT | |
| P5 | reliability = n/(n+2k) | survived | real gap: formula unpinned for a supported k |
| O1 | OL window 5 games | survived | equivalent in the fixture (KC has exactly 4 games); real gap |
| O2 | OL absences count Out only, not Doubtful | survived | real gap: no Doubtful lineman |
| O3 | missed-last-game at a quarter of the snaps | survived | real gap: no partial-snap lineman |
| O4 | OL starting six | CAUGHT (3) | |
| O5 | injury decode swaps Doubtful / Out | CAUGHT | |
| D1 | team snaps = fewest, not most | survived | equivalent on a clean fixture; this is where B2 hides |
| D2 | on-field short-yardage counts 3 yards | survived | real gap (boundary) |
| D3 | `aux_matches` ignores the store's row count | survived | equivalent in practice (same `built` implies same rows) |
| D4 | neutral band starts at 25% | survived | real gap (boundary) |
| G1 | light and heavy box swapped | CAUGHT | |
| G2 | nickel threshold moved | survived | real gap: personnel shares unpinned |
| G3 | leading / trailing swapped | survived | real gap |
| G4 | QB exposure over snaps not dropbacks | CAUGHT | |
| G5 | official yards ignore `rushing_yards` | survived | equivalent in the fixture (no lateral); real gap |
| M1 | `_rel` k formula inverted | survived | real gap: measurement code unguarded |
| M2 | RB gate returns True for rushing metrics | survived | real gap |
| M3 | decay rule threshold 0.6 -> 0.7 | survived | real gap |
| M4 | split-half uses even vs even weeks | survived | real gap |
| CONTROL | comment-only change | green (as it should) | |

The builder's own 41-mutant run (40 caught) covers different points: sack-as-rush, kneel-as-rush, the unscoped usage lines, the
RPO pass, the sneak rule, `week_rows` and the k-table mapping, and the OL window of 3 games. Together the two runs say the guards
are strong on the definitions inside `add_game` and weak on everything wired around it.

The survivors I checked against the data are all right today: split priors (4 shrunk cells recomputed with their k and my league
prior), personnel shares (3 rows), lead/trail run rates (8 cells), scramble yards per attempt (4 cells), the builder's team/opp
wiring (the d_, dq_, tc_ and ol_ cells in the rows of section 6).

## 5. Leakage, on the real data (task B): none found

Method (`b_poison.py`, `b_control.py`, `b_inj_live.py`, `b_sleeper.py`). For (season, week) = (2023, 9), (2024, 10), (2025, 14), (2026, 5):
- poison every source row at that week and later, by shuffling each column independently among the poisoned rows:
  pbp (identities kept at week W, everything shuffled after), participation, FTN, PFR rush / def / pass, NGS (season rows only),
  snap counts, and injuries for weeks > W;
- rebuild the agreed store, the aux store and the dataset rows through the production code (`D.build_season`, `R.build_aux`,
  `DS.run_build`);
- compare every column except identity, `act_*`, `sl_act_*`, `played`, `in_sleeper`.

| case | RB rows (common) | RB feature columns | QB rows (common) | QB feature columns | rows with a feature difference | rows whose `act_*` changed |
|---|---|---|---|---|---|---|
| 2023 wk 9 | 923 (923) | 846 | 325 (325) | 322 | **0 / 0** | 87 / 31 |
| 2024 wk 10 | 994 (994) | 846 | 368 (368) | 322 | **0 / 0** | 88 / 32 |
| 2025 wk 14 | 1,374 (1,374) | 846 | 503 (503) | 322 | **0 / 0** | 92 / 36 |
| 2026 wk 5 | 526 (520) | 846 | 182 (182) | 322 | **0 / 0** | 86 / 36 |

- **Controls.** (a) A deliberately leaky builder (the week's games added to the live tallies before the rows are written, the F3
  class) on the same poisoned inputs: 92 of 92 RB and 32 of 32 QB week-10 rows differ (`p_rushing_ypc`, `p_rushing_success`, ...).
  The harness detects the bug it is meant to. (b) Poisoning only the week-W injury rows changes exactly `status`, `practice`,
  `tm_rb_out_share` and `ol_starters_out_report`: the only same-week inputs read. (c) The clean rebuild from raw equals the committed
  dataset for 2024 weeks 1-10 (994 / 368 rows, 0 differences) and, in a full rebuild, byte for byte.
- **Same-week PFR / NGS / snap-count values reaching a feature:** none. They were poisoned at week W and later; nothing moved.
- **OL starting five and absences:** read only the team's last four games this season before the cutoff plus the cutoff week's
  injury report. 16 rows by hand: out_report, missed_last_game and continuity all match raw (section 6).
- **`sl_proj_*`:** labelled `observed (not provably pre-kickoff)` (9 columns), and no feature is built from them: shuffling every
  Sleeper projection among players (2025 wk 10) changed `sl_proj_*` on 882 RB and 298 QB rows and left every other column of every
  row unchanged.
- **Injury `status` timing:** m9.

## 6. Hand-checked cells (task A6): 141 cells, 0 real differences

Each is the dataset value vs my own as-of computation from raw (`a6_cells.py`, `a6b_cells.py`, `a6c_ol_status.py`,
`a6d_more.py`). Windows: current season weeks < W at weight 1, season-1 at 0.75, season-2 at 0.5625; usage team-scoped.

| row (game_id team name gsis) | field | dataset | mine |
|---|---|---|---|
| 2024_17_TEN_JAX JAX Tank Bigsby 00-0038555 | `p_usage_carry_share_raw` | 0.3183 | 0.31834 |
| 2024_15_NE_ARI NE Rhamondre Stevenson 00-0036875 | `p_usage_carry_share_raw` | 0.5934 | 0.59335 |
| 2025_09_KC_BUF BUF James Cook 00-0037248 | `p_usage_target_share_raw` | 0.0849 | 0.08488 |
| **traded** 2023_14_SEA_SF SF Jordan Mason 00-0037525 (MIN, SF) | `p_usage_carry_share_raw` | 0.1399 | 0.13987 |
| **traded** 2024_15_BAL_NYG BAL Derrick Henry 00-0032764 (TEN, BAL) | same | 0.6742 | 0.67416 |
| **traded** 2024_06_DET_DAL DET Craig Reynolds 00-0035567 (WAS, DET) | same | 0.0807 | 0.08073 |
| **traded** 2023_06_PHI_NYJ PHI Kenny Gainwell 00-0036919 (PHI, PIT, TB) | same | 0.1634 | 0.16342 |
| **defense** 2025_07_NYG_DEN DEN RJ Harvey (opp NYG) | `d_vs_rb_ypc_raw` / `d_box_light_raw` | 4.693 / 0.6608 | 4.69276 / 0.66081 |
| **defense** 2023_10_GB_PIT PIT Jaylen Warren (opp GB) | same | 4.413 / 0.6621 | 4.41314 / 0.66205 |
| **2026** 2026_05_BUF_LA BUF Ray Davis 00-0039875 (opp LA) | `d_personnel_nickel_raw` (prior seasons only; `p_usage_onfield_plays_current` 0) | 0.3954 | 0.39537 |
| **2026** 2026_05_SF_SEA SF Christian McCaffrey 00-0033280 (opp SEA) | same | 0.7423 | 0.74231 |
| **2026** 2026_05_MIN_NO MIN Max Bredeson 00-0041081 (opp NO) | `d_box_light_raw` (FTN box in 2026) | 0.6704 | 0.67042 |
| **QB q_** 2024_08_TEN_DET DET Jared Goff 00-0033106 | `q_scramble_rate_raw` / `q_sneak_rate_raw` / `q_kneel_per_game_raw` | 0.0193 / 0.0019 / 0.9492 | 0.01927 / 0.00188 / 0.94923 |
| **QB q_** 2025_10_JAX_HOU JAX Trevor Lawrence 00-0036971 | `q_designed_rate_raw` / `q_kneel_per_game_raw` | 0.0071 / 0.6301 | 0.00712 / 0.6301 |
| **QB 2026** 2026_06_CAR_PHI CAR Bryce Young 00-0039150 | `q_scramble_rate_raw` / `q_kneel_per_game_raw` | 0.0597 / 0.6728 | 0.0597 / 0.67277 |
| **QB q_** 2023_09_SEA_BAL SEA Geno Smith 00-0030565 | `q_scramble_ypa_raw` | 8.425 | 8.42535 |
| **dq_** 2025_08_MIA_ATL MIA Zach Wilson 00-0037013 (opp ATL) | `dq_scrambles_oe_ratio_raw` (k 79.94, leave-one-out) | 1.193 | 1.19278 |
| **dq_** 2025_10_ATL_IND IND Daniel Jones 00-0035710 (opp ATL) | same | 1.171 | 1.1707 |
| **ol_** 2023_16_ARI_CHI CHI wk 16 | `ol_starters_out_report` / `missed_last_game` / `continuity` (00-0036953 Out) | 1 / 1 / 0.9329 | 1 / 1 / 0.9329 |
| **ol_** 2024_12_DAL_WAS DAL wk 12 (00-0031402 Doubtful) | same | 1 / 0 / 0.9086 | 1 / 0 / 0.9086 |
| **ol_** 2026_04_GB_TB GB wk 4 (00-0039817 Out) | same | 1 / 1 / 0.8122 | 1 / 1 / 0.8122 |
| **status** 2024_07_BAL_TB TB Rachaad White | `status` / `practice` | questionable / Full | raw report 'Questionable', 'Full Participation in Practice' |
| **status** 2025_16_LV_HOU HOU Nick Chubb | same | questionable / Full | raw 'Questionable' (2025 file, no `date_modified`) |
| **status** 2023_15_BAL_JAX JAX Tank Bigsby | `status` | active | not on the report |
| 2025_14_DEN_LV LV Dylan Laube | `tc_leading_run_rate_raw` / `tc_trailing_run_rate_raw` | 0.5382 / 0.2614 | 0.53821 / 0.26137 |
| **shrunk split** 2024_11_IND_NYJ NYJ Breece Hall | `p_rushing_by_direction_middle_ypc` (k 473.77, prior = own EB ypc + league offset) | 4.018 | 4.01798 |
| **shrunk** 2025_10_PHI_GB GB Josh Jacobs | `p_rushing_ypc` (k 231.2) | 4.082 | 4.08209 |

The 141 cells break down as 21 RB usage / defense / 2026 cells, 33 QB and defense-OE cells (30 q_ cells on 6 + 3 rows, 3 dq_), 48 OL
cells (16 rows x 3 fields), 20 status rows, and 19 more (8 lead/trail, 4 scramble ypa, 4 shrunk splits, 3 shrunk overall values). One
value (Trubisky, `q_kneel_per_game_raw` 1.072 vs 1.07234) differs only in the file's 4-significant-figure formatting.

## 7. Labelling and claims (task C)

- About 50 paths spot-checked (the 34 RB and 15 QB names in the review script, all families). RB kinds match a mid-season profile
  on 818 of 818 columns; QB on 173 of 181 (the 8 in m5). Units are right except the empty ones in m5. Bases say what they measure
  (e.g. "edge: end gap, either side", "a snap is not a route", "FTN's dictionary was not reached, so CHK is READ as checkdown, unverified").
- **Proxies.** Front (DL count), checkdown (CHK), targets per on-field dropback, `pressure_pfr` and the containment OE are stated as
  proxies in the field name or basis. `kind` carries `proxy` for all but the containment OE (m5).
- **Overclaims.** None on run concepts or fronts: 3.5 lists them as not available (zone / gap / power / counter / duo / trap / toss /
  stretch, 4-3 vs 3-4, odd / even, over / under, DL / LB alignment, gap integrity, spies, read option, blocking grades, 2026
  participation). Compared with `rush_brief_common.md`, nothing the owner asked for is silently missing. "Edge setting" is offered
  only as edge-gap runs allowed, labelled as such. The overclaims that exist are B1's "usage is known quickly" and the "noise" wording (m12).
- **Credit.** The CC BY-SA 4.0 notice and the share-alike line are in every `license` block, the aux pickles, the module docstrings and
  the report. Gaps in m11.

## 8. Production code (task D)

- **Import.** stdlib only plus repo modules; no numpy / pandas loaded (`sys.modules` checked); 0.05 s.
- **Excepts.** No `except Exception: pass`; the new IDs are `RUSH-fetch`, `RUSH-align`, `RUSH-k-table`; `errlog.note(code, exc=None, msg="",
  path=None)` accepts the keyword uses. One `except OSError: pass` (m1). pyflakes clean on all four files.
- **Memory and time.** 3.85 s and 133 MB for the three-season snapshot plus 349 profiles; aux build 3.5-3.8 s a season, 53 MB for two.
  Nothing CPU-bound in a request path as designed.
- **Degradation.** Stale, missing or other-store aux: m2. Missing file: that block is empty and `missing` says so; `status` reads
  "unknown". Truncated: m3. A truncated pbp .gz raises `EOFError`; in the proposed job that is caught by the `try` and ledgered.
  A corrupt `reliability.json` falls back to the code defaults and ledgers `RUSH-k-table` (guarded).
- **Proposed diffs.** 8.2 (`artifacts.SCHEMA` 7) and 8.3 (the MX loop) are right. 8.1 is M1. `/api/art/upload` accepts
  `nfl_rush_aux_<season>.pkl` (name regex) and `_sync_kind` uploads every deep file, so the PC path needs no change.

## 9. Process integrity (task F)

- The pre-run hashes (`prerun_hashes.txt`, 2026-10-23 20:52:54 UTC) were recorded five seconds before the first `measure` run, and
  the script prints its own sha256 at run time: `measure.log` shows `262da6dc...` at 20:52:59, equal to the recorded hash. The first
  run's output was "tendency r 0.602 -> decay 0.75".
- `persistence.first.json` (the first run's output) is **byte-identical** to the shipped `persistence.json`, which embeds the tendency
  list, the thresholds and the rule text; `reliability.first.json` has identical `k` and `pooled`. So the ten tendencies and the 0.3 /
  0.6 thresholds in force at the first run are the shipped ones, and nothing about the rule or its inputs moved after a number was seen.
- The later edits the report names (checks, builder, an explicit empty k table for pass 1, sorted units) did not touch an output. I could
  not diff the pre-run source (the 262da6dc file is not kept anywhere in the scratchpad); the byte-identical outputs are the evidence.
- I cannot show from the files that the ten-metric list was fixed before any rushing persistence number was ever seen in the session.
  What I can say: four of the ten are player traits and six are team tendencies, the team subset alone lands at 0.535 (decay 0.5), and
  the pooled number sits 0.0015 above the boundary (my alternatives in 2). The report discloses all of it.
- The B1 error is not a post-hoc rule change: the rule says "Running backs"; the code counted every non-QB rusher from the first run
  on (the first and final k tables agree).

## 10. What I could not check

- Whether the ten-tendency list pre-dates any persistence number (the pre-run source is not retained).
- Injury `status` timing for 2025 and 2026 (no `date_modified` column in those files).
- Kalshi settlement of aborted snaps (undiscriminating in the data) and of the 2026 markets.
- The builder's own 41-mutant run (not re-run; I used my own 45). The model-side files (`nfl_rush.py`, model fits), as instructed.
- NGS against any ground truth (RYOE is NGS's own model; I only checked the join, the censoring and the team match).
- `meta.json` / `checks.json` run times (they change per run by design).
- A live run of `nfl_data_job` against the network or a Render disk; section 8.1's behaviour is from a stubbed simulation of its logic
  (`d_job_sim.py`) and the code of `D.refresh` / `R.refresh`.
- Whether the PFR team-snap bias (B2) was visible to the model agent's validation: not in scope here.


---

# RE-CHECK (2026-10-24)

Reviewer: Sonnet, same rules as before (no git writes, no other repo file edited, no 2025/26 outcome column read beyond the
reconciliation checks of the first pass; the poison test below skips the `act_*` count for 2026). The builder applied F1-F12 and F14
and declined F13 (a nominal base front from depth charts, which exist only for 2025-26; I did not check that decision, it is the
data-source review's). The orchestrator applied the integration fix for my M1 in `matchups.nfl_data_job` / `_drop_raw`.
Scripts: `$SP/review_rush_data_sonnet/scripts/` (`r1_*` ... `r9_*`, `e_mutate2.py`, `e_mutate3.py`, `b_poison2.py`, `b_control2.py`,
`b_inj_live2.py`, `g_rebuild2.py`). I re-derived each fix from the raw nflverse files or by running the production code on altered
inputs; I did not take the report's figures.

## R.1 Verdict: AGREE AFTER FIXES (one required fix, in the integration; the data layer itself needs none)

- **Data layer (`nfl_rush_data.py`, `nfl_rush_profiles.py`, `research/nfl_rush_dataset.py`, `research/data/nfl_rush/*`, guards):
  AGREE TO SHIP.** B1, B2, M2 and m1-m12 are fixed and I re-derived each. The leakage test is clean on the new code, the
  foundation is byte-identical, a full rebuild reproduces the committed datasets and `schema.json` byte for byte, and all my
  mutants but one are now caught.
- **Integration (`matchups.nfl_data_job`): keyed correctly, strands no raw file, retries a season whose refresh raises, but a season
  whose DOWNLOADS fail is never retried (R1 below).** One small fix is required before it runs in production. R2 and R3 are
  recommended.

### Required fix

**R1 (MAJOR, integration; two lines in `nfl_rush_data._refresh_season` plus a test). A network failure during the one-time backfill
leaves a permanently empty aux store that the job treats as done.**
Evidence (`r9_netdown.py`, the real `R.refresh` with the network down, a copy of the store, aux absent):

```
R.refresh result: {2025: {'built': True, 'missing': ['pbp extras (no pbp file) ...', 'situational on-field counts ...',
                   'pfr_rush (no file for 2025)', 'pfr_def ...', 'pfr_pass ...', 'injuries ...', 'snaps ...', 'ngs_rush ...']}}
aux saved: True | aux_matches(store): True | extras: None | blocks empty: pfr_rush 0, pfr_def 0, snaps 0, injuries 0
rush_due next run: False        profile stamp: {2025: 'aligned'}
```

`_refresh_season` builds and saves an aux when every fetch failed (the agreed `D.refresh` refuses: "no play-by-play", nothing
saved). `aux_matches` compares only the store's identity, so `rush_due` is False from then on, the profile stamps the season
`aligned`, and nothing ever refetches a prior season (the daily rule does not read it). This is the property O1 was meant to
guarantee ("a failed season is retried"): it holds when the refresh raises, not when `get()` swallows the fetch error and builds
from nothing. A single outage during the first run after deploy would do it.
Fix: in `_refresh_season`, return `{"error": "no play-by-play ..."}` and save nothing when the season's pbp is absent (as the
agreed refresh does); then `load_aux is None` keeps the season due. Beyond that, a block that failed to fetch for a season that
should have it (PFR rush / def, injuries, snaps) is the same weakness the agreed layer has for participation / FTN; a
`complete` flag on the aux that `rush_due` also checks would close it (optional).

### Recommended (not blocking)

- **R2 (MINOR).** A raising `D.refresh(seasons=[s])` for a prior season stops the daily matchup refresh (`r8_job.py` S2: the call
  list ends at `D [2024]`, the 2026 refresh never runs). That was always true of a missing store, but the new job also rebuilds
  HEALTHY prior stores solely for the rushing layer. Wrap the per-season matchup rebuild in a try that ledgers and continues when
  the season already has a store.
- **R3 (MINOR).** A rush failure that persists (a bad PFR file) costs a 77 MB download and a matchup-store rebuild per season per day
  (`r8_job.py` S4: `D [2024]`, `D [2025]` again the next day). A retry backoff (record the failure date, try weekly) would cap it.
- **R4 (NIT).** `K_FRAMES` (2023-25 fit-only, 2026 full) has no guard (my mutant N12 survives); the shipped data is right (below).
  `artifacts.py`'s comment still says "nfl_rush_data SCHEMA 1" (it is 2). A back's "games" now include games he played special teams
  only (213 of 1,826 RB/FB PFR rows in 2024): a sensible definition, but §0.3 should say it moves `carries_pg` and the denominators of
  his shares. `rb_sy_conv` k 450 comes from r .024 on 47 units (the rule's r > .02, applied as written; its sampling error is
  about +/-.15). The 18 `by_box` fields name "NGS" in their basis and carry the unrestricted `derived:participation+ftn+pbp` tag:
  that is the owner's participation decision (report 9.11), flagged here for the licence reviewer only.

## R.2 Each earlier finding, re-derived

| finding | what I re-derived | result |
|---|---|---|
| **B1** RB units held WR/TE | New `reliability.json` against my own RB-only counts from raw (`r1_rbk.py`, my own code): `rb_carry_share` r .911 / k 16.95 (mine .903 / 18.3; my presence set is smaller, their games now include special-teams-only rows, R4), `rb_ypt` .143 / 171.9 (mine .143 / 170.7), `rb_line_yds` .260 / 225.45 (mine .260 / 225.45, exact), `rb_ypc` .257 / 228.6 (exact), `rb_catch` .032 / 877 (mine .021 / 1,358: both noise, k 30-50 times n). Of the 25 affected metrics (24 plus `rb_line_yds`), 22 sit within 6% of my first-pass RB/FB-only k; the three on-field shares (`rb_sit_all` 42.9 vs 46.2, `_early` 31.6 vs 33.8, `_neutral` 23.7 vs 25.5) are 5-7% lower, which follows from special-teams-only games now counting as games the back was available (R4). `T.rb` holds RB/FB runs only (`elif rpos == "RB"`) and `unit_profiles` filters by position as well; DECAY unchanged: tendency r 0.602, 0.75, same ten metrics, same rule text (`persistence.json`), and my independent persistence (0.6015) was RB-only all along | **FIXED** |
| **B2** team snap total | 2024, all 544 team-games: aux team total == the most snaps any player took in 543; the one other is DET 2024_02 (85 snaps at 0.99, fallback 86). 13 of 4,440 team-games 2018-2026 used the fallback (report: 13). 40 of 40 random 2024 rows: `p_usage_snap_share_raw` == RB snaps / true total, as-of with the 0.75 / 0.5625 weights | **FIXED** |
| **M1 = Opus M2 = O1** integration | R.3 below | keyed correctly; **R1 open** |
| **M2** guard blind spots | my 45 mutants on the fixed tree: **45 of 45 caught** (42 as written, 3 with a moved string adapted: U3, D1, M4); control green. 181 / 181 guards pass | **FIXED** |
| Opus M1 = O2 absent injury week | 2026 week 8 snapshot (injury weeks 1-7 held): `status` "unknown", `out_report` None, `ol_listed_out` None, `rb_room_out` None (before: active / 0 / 0.0). A week that exists with a team missing from it: "active", 0 | **FIXED** |
| **m1** silent `except OSError: pass` | `FileNotFoundError` passes, any other error ledgers `RUSH-rm` (in `R._refresh_season` and in `matchups._drop_raw`) | **FIXED** |
| **m2** silent stale / missing aux | every profile carries `aux: {season: aligned / stale / missing}`; missing and stale read so (r7); stale ledgers `RUSH-aux`. Residual: an aux built with no pbp reads `aligned` (R1) | **FIXED** except R1 |
| **m3** truncated CSVs | each source cut at 60%: PFR rush "99 store games with no rows", PFR def 101, snaps 100, participation `sit` 106, injuries "5 store weeks with no rows" -- all in `missing` (`r7_degrade.py` and an inline script on the same inputs) | **FIXED** |
| **m4** Kalshi | report 9.6: sacks tested and excluded (111 of 111 discriminating ladders), aborted snaps untested -- my numbers | **FIXED** |
| **m5** schema labels | the 8 QB columns `modelled`; `dq_pressure` `modelled`; containment and the hit + sack fields `proxy`; the read-option column `not_available`; the three `ol_starters_*` columns carry their definition and name the injury report; no empty unit; no column without a description; `license`, `k_note`, `kind_note` in `schema.json`; each column has a machine `source_tag` | **FIXED** |
| **m6** `played`, `in_sleeper`, `sl_qb1` | `played` is now `act_played` (kind "actual (outcome)", text "OUTCOME: ..."), `act_any_snap` likewise; `in_sleeper` / `sl_qb1` / `sl_proj_*` read "observed (Sleeper post-week snapshot; not provably pre-kickoff)"; the model frame reads `act_played` as `y_played` only and lists `in_sleeper` / `sl_qb1` as NEVER_READ | **FIXED** |
| **m7** odd/even optimism | disclosed beside the usage k (carry share .91 / k 17; first-half vs second-half .77 / k 54), and each k in the json carries both disclosure splits | **FIXED** |
| **m8** k frame | 2023-2025 rows are shrunk with `k_fit_only`, 2026 with the full table: 9 of 9 sampled 2023-25 `p_rushing_ypc` values equal my recomputation with k_fit_only (rb_ypc 218.34; the full-k value differs in the 3rd-4th decimal), 3 of 3 2026 values equal it with the full 228.62 (`r6_kframe.py`) | **FIXED** (constant unguarded, R4) |
| **m9 / m10 / m12** disclosures | 9.10 (6 late rows of ~27,000), the OE k as a range (145 / 200 / 359; 117-610), "not measurable at this sample, bounded below ~0.3 yd" | **FIXED** |
| **m11** licence | `schema.json` has the `license` block; the CSVs carry none in-file (meta + schema beside them) | **FIXED** |
| nits | `fetched` removed; NGS position wording in the docstring; dead `TL["rush"]` / `TL["qb_scr"]` removed | **FIXED** |

## R.3 The integration (task 4), run against the real `matchups.nfl_data_job`

`r8_job.py` injects fake `nfl_matchup_data` / `nfl_rush_data` modules (my own, not the guards' helpers), a scratch deep store and
ledger, and plants raw files. October, so the daily season is 2026 and the prior seasons are 2024 and 2025.

| scenario | result |
|---|---|
| S1 existing deployment: both stores present, 2024's aux missing, 2025's matching | `D [2024] keep, R [2024] no-keep, D [2026], R [2026]`; 2025 untouched; nothing left on disk. **Keyed correctly: the original bug is gone** |
| S3 a killed run's leftover raw files for seasons that are not due | swept by the final `finally` (and by the next run); nothing left |
| daily refresh raises (their guard, confirmed in code) | the `finally` removes every prior season's files; the error reaches the job |
| S4 the rush refresh raises for both prior seasons | each season isolated and ledgered `RUSH-refresh`, the daily rush refresh still runs, no file left. The next day both seasons are due again and retried; the day after the source is fixed both auxes exist. **A failed season is retried when its refresh raises** |
| S5 the daily season's aux fails | isolated, `{'error': 'ValueError: bad source'}` in the result |
| S2 the matchup rebuild of the first backfill season raises | the daily refresh never runs; the files are removed (R2) |
| network down at the first backfill (`r9`) | an empty aux is saved, matches its store, is never retried (**R1**) |

Peak disk is one prior season (about 77 MB, not 154) because each season's files are dropped before the next downloads. The
guards' five "rushing aux (O1)" checks pass and I mutated the job nine ways (J1-J9: backfill keyed on the matchup store alone, no
`finally` cleanup, `rush_due` never due, freshness records kept, no per-season drop, `keep_raw=False` on the backfill, no upfront
drop, rush failure not isolated, import not guarded): all nine caught by `tests/guards_matchups.py` (38 of 38 pass unmutated).

## R.4 Nothing new is broken

- **Foundation:** `nfl_matchup_data.py`, `nfl_matchup_profiles.py` and `dataset_2023_2026.csv.gz` hash to `8511346956bb...`,
  `d2007503a6d8...`, `766a60ed11ee...`; `git status` shows them untouched. Every hash in the rewritten report's section 10 matches the
  tree. `tests/guards_nfl_matchup_data.py` 149 / 149; `tests/guards_nfl_rush_data.py` 181 / 181; `tests/guards_matchups.py` 38 / 38;
  pyflakes clean on all changed modules and guards. I did not run `tests/combo_audit_guards.py` (the report says 3,094 passed, 1 failed,
  the failure in the model agent's docstring check).
- **Leakage, again** (`b_poison2.py`, the new code, a rebuilt aux SCHEMA 2 store): poisoning every source row at (season, week) and
  later changes **no feature** in 2024 week 10 (994 RB rows x 853 feature columns, 368 QB rows x 335) or 2026 week 5 (526 x 853, 182
  x 335). Positive controls on the new code: a deliberately leaky builder differs on 92 of 92 week-10 RB rows; poisoning only the
  week-W injury rows changes exactly `status`, `practice`, `tm_rb_out_share`, `ol_starters_out_report` and the new
  `ol_ol_listed_out`. The new pbp hit + sack proxy (`qb_hit` shuffled at week W) did not move.
- **Outcome and post-week fields can never be features.** By construction: `act_*` and `sl_act_*` (39 RB / 45 QB columns, kind
  "actual (outcome)"), `act_played` / `act_any_snap` among them, plus `in_sleeper`, `sl_qb1`, `sl_proj_*` (kind "Sleeper post-week
  snapshot") are the only columns outside the six profile prefixes (`p_ q_ d_ dq_ ol_ tc_`), `status`, `practice` and
  `tm_rb_out_share`, and the poison test shows every one of those feature columns invariant to the week's own plays, snaps and
  stats. Shuffling Sleeper's projections (first pass) left every feature unchanged. The model frame reads `act_played` only as
  `y_played` and lists `in_sleeper` / `sl_qb1` as `NEVER_READ`.
- **Rebuild:** a full `run_build` from the rebuilt store reproduces `rb_games_2023_2026.csv.gz`, `qb_games_2023_2026.csv.gz` and
  `schema.json` byte for byte (`g_rebuild2.py`).
- **New numbers, from raw:**
  - Hit + sack proxy: the QB reliability is r .608 / k 159.0 (shipped .609 / 158.7) and the defense OE r .402 / k 67.1 (shipped
    .403 / 66.97); 8 of 8 `q_hit_sack_rate_raw` cells and 3 of 3 `dq_hit_sack_rate_oe_ratio_raw` cells match.
  - 84 of 84 OL and status cells match: 16 rows x (`out_report`, `missed_last_game`, `continuity`, `ol_ol_listed_out`) and 20 status rows.
  - 21 of 21 RB and defense cells, and 33 of 33 QB and defense-OE cells (one is a rounding at 4 significant figures), on new random rows
    that include traded backs, 2026 rows and special-teams-only presence.
- **Licence tags:** all 425 leaves of the six profiles carry a valid `source`; every field whose basis names PFR snaps / yards-contact /
  broken tackles / RYOE / NGS data is `restricted` except the 18 `by_box` fields (R4, the owner's participation decision).

## R.5 Mutation tally for the re-check

| set | mutants | caught | survived |
|---|---|---|---|
| my original 45 on the fixed tree (three strings adapted) | 45 | **45** | 0 (control green) |
| new, on the fixes: B1 in the tallies and at the units, the old snap total, an absent week reading `{}`, the aux stamp, coverage, refresh isolation, the licence tags and `RESTRICTED`, the hit + sack proxy, the share clamp, `act_any_snap`, the schema kind priority, special-teams-only rows | 15 | 14 | 1: N12 (`K_FRAMES` 2025 -> "full"), a constant no guard pins; the data is right, R4 |
| new, on the integration (`matchups.py`, run under `guards_matchups.py`) | 9 | **9** | 0 |
| total | 69 | **68** | 1 |

## R.6 Not checked

`tests/combo_audit_guards.py` (the orchestrator's); F13's decision (depth charts exist for 2025-26 only); a live network run of the job
(the scenarios use injected modules and one real `R.refresh` with the network patched off); the model-side files.

---

# CONFIRMATION (2026-10-24)

Round 3: the data layer's G1 (my re-check R1), my R4 nits, and the orchestrator's job side (my R2 / R3). Re-derived from the files and by running
the real code, not from the report. Scripts are mine, in my scratchpad (`c1_g1.py`, `c2_noreport.py`, `c3_job.py`, `e_mutate6.py`,
`e_mutate7.py`); no git writes, no repo file edited but this one.

## C.1 Verdict: AGREE TO SHIP

Nothing in the shipped behaviour is wrong. Every fix does what it says, on the real code. Three guard gaps remain (C.6: R5-R7); none hides a
defect I could find, all are cheap, and none blocks the push.

| question | answer |
|---|---|
| G1 (R1): an incomplete aux is saved as incomplete, stamped, and retried | **confirmed**, six scenarios on the real `R.refresh` (C.2) |
| a missing pbp saves nothing | **confirmed**, and an aux already on disk is left alone |
| R4 nits (K_FRAMES, ST-only disclosure, artifacts comment, by_box note, source text from the tag) | **confirmed**, one is not pinned by a guard (R7) |
| R2: a failed rebuild of a healthy store never costs the daily refresh | **confirmed** on the real `matchups.nfl_data_job` (C.4 T2) |
| R3: a rushing-only rebuild is tried at most once a week | **confirmed** end to end with a persistent store, the marker written by the job (C.4 T3) |
| the rush checks run in every month | **confirmed**: `guards_matchups` 44 / 44 with the clock real (October) and with it patched to April |
| the agreed foundation is untouched | **confirmed**, byte-identical (C.3) |

## C.2 G1: the aux says what it read

`c1_g1.py` runs the real `R.refresh(seasons=[2025])` against the delivered 2025 store with a fake network (my fetch serves the raw files for
the kinds I switch on, raises `OSError` for the rest), a scratch copy of the deep store, and builds the stamp through
`RP.build(...).team_context(...)["aux"]`:

| scenario | `R.refresh` result | aux on disk | `aux_complete` | stamp |
|---|---|---|---|---|
| A network down (aux deleted first) | `error: no play-by-play file for 2025 (download failed?): nothing saved, the season stays due` | **none** | False | none |
| B only pbp + participation | built, `complete False`, absent `[pfr_rush, pfr_def, pfr_pass, injuries, snaps, ngs_rush]` | saved | False | **incomplete** |
| B again, same network | rebuilt (not skipped), the same absent list | saved | False | incomplete |
| C everything but NGS | built, `complete False`, absent `[ngs_rush]` | saved | False | incomplete |
| D everything | built, `complete True`, absent `[]` | saved | True | **aligned** |
| D again | `skipped: no source changed, the aux store matches the matchup store and is complete` | unchanged | True | aligned |
| an aux with no `complete` key | n/a | n/a | **False** (strict) | n/a |

- The completeness test reads `expected_sources(season)` (pbp, participation per `D.PARTICIPATION_SEASONS`, pfr_rush, pfr_def, pfr_pass from
  2021, injuries, snaps, NGS from `NGS_FIRST`), so a 2018 aux is not "incomplete" for lacking 2021+ sources. NGS absence means `ngs is None`
  (no NGS file at all); a season with no weekly NGS rows does not make the aux incomplete (the "short block" listing handles that).
- **All nine delivered aux stores (2018-2026) are `complete: True`, `absent: []`**, and each matches its store (`store_built` / `store_n`).
- A **complete** aux on disk and the network down on the next run (`c4_keep.py`): the refresh returns the no-pbp error, the aux file is
  byte-identical afterwards and still complete. The message's "the season stays due" is loose there (a complete, matching aux is not due);
  wording only.
- "The week's report lists nobody from the team reads unknown" (the Opus R2, folded in): `c2_noreport.py` against the delivered stores: the
  8 RB + 2 QB rows of `rb_games` / `qb_games` that changed are exactly the team-weeks the report omits. From the raw injury file, **2 of
  1,818** playing team-games have no row (ATL 2023 week 5, DEN 2024 week 1), so "unknown, not active" is the right reading. An absent week
  reads None as before; `RUSH-noreport` is ledgered once per team-week.

## C.2b My R4 nits

- **Special-teams-only games, k both ways** (report section 0.3, "what a back's games are"): the shipped definition counts a game in which a
  back took only special-teams snaps (Kalshi settles a player on one snap), and the report prints the k each way: carry share r 0.911 / k
  16.95 as shipped against 0.905 / k 17.95 with those games excluded, and the same for carries per game, target share, on-field share and
  RB-carry share. The "excluded" figures equal the RB-only values I measured myself in the first review from the raw files, so the
  disclosure is independently corroborated, not just internally consistent.
- **K_FRAMES pinned:** `K_FRAMES = {2023: fit_only, 2024: fit_only, 2025: fit_only, 2026: full}`, written into `schema.json`'s `k_note`, and
  a guard fails if any frame moves (N12, N12b, N12c all caught; N12 was the lone survivor last round).
- **`artifacts.py`:** the SCHEMA 7 comment now reads "nfl_rush_data SCHEMA 2" and names the once-a-week marker
  `nfl_rush_backfill_try_<season>.pkl`; the marker key matches `matchups._RUSH_TRY` and passes `artifacts._NAME_RE`.
- **Source text from the tag:** `_source(c, tag)` writes each column's human text FROM its machine tag (`_CREDIT`), and a guard (G5) checks
  that every column's text names each part of its tag; four source-text mutants (text ignores the tag, pfr credit says pbp, a derived tag keeps
  one source, ngs credit says pbp) plus the dropped `source_tag` column are all caught.
- **by_box note:** present on all 78 `by_box` columns of `schema.json` ("box: NGS count 2016-25, FTN where none (all 2026); unrestricted
  (owner's participation decision)"). Not pinned by a guard (R7).

## C.3 Hashes, the foundation, guards

- `nfl_matchup_data.py` `8511346956bb`, `nfl_matchup_profiles.py` `d2007503a6d8`, `dataset_2023_2026.csv.gz` `766a60ed11ee`: unchanged, and `git
  status` shows them clean. `nfl_rush_data.py` `247cc625`, `nfl_rush_profiles.py` `1bddcab7`, `research/nfl_rush_dataset.py` `28bdea90`, the
  guard file `f9e5168d`, the two datasets and `schema.json` (`d7a42025`, `43fa3ce7`, `5be07529`) all match report section 10. The tree I ran
  and mutated is byte-identical to the main tree (`cmp` on all seven touched files, checked again after the runs).
- Run on the main tree with a scratch deep store and ledger: `guards_nfl_rush_data.py` **192 / 192**; `guards_matchups.py` **44 / 44** (also
  44 / 44 with `clock.today_et` patched to April, so the rush checks do not silently skip outside October); `guards_nfl_matchup_data.py`
  **149 / 149**; pyflakes clean on `nfl_rush_data.py`, `nfl_rush_profiles.py`, `research/nfl_rush_dataset.py`, `matchups.py` and both guard files.
  (A run with the clock patched to September fails three NBA out-of-season checks in `guards_matchups`; they are about the NBA board's
  date handling, not this layer, and I did not chase them.)
- I did **not** run `tests/combo_audit_guards.py`: `ps` shows two other runs of it active (pids 22753 and 25698), which are the orchestrator's.
- I did not rebuild the datasets from the stores this round (I did in the re-check, byte for byte); the 10 changed rows are accounted for by
  the "nobody listed" rule (C.2), and the report's section 10 records the rebuild.

## C.4 The job side, on the real `matchups.nfl_data_job`

`c3_job.py` imports the real `matchups`, replaces only `nfl_matchup_data` / `nfl_rush_data` with fakes that write real raw files, and keeps one
persistent scratch deep store per scenario so the marker survives between "days" (October, so the daily season is 2026 and the prior ones 2024
and 2025). Each call below is `D` = the matchup refresh, `R` = the rushing refresh, `k` = raw files kept:

| scenario | calls | result |
|---|---|---|
| T1 2024's aux is **incomplete** (matches its store) | `D[2024]k, R[2024], D[2026]k, R[2026]k` | rebuilt, aux now complete, marker set, nothing left on disk. **R1 reaches the job** |
| T2 `D.refresh([2024])` raises on a **healthy** store | `D[2024]k, D[2026]k, R[2026]k` | job returns a dict, `backfill[2024] = {error: ...}`, `NFLM-backfill` ledgered, raw files gone, **the daily refresh ran** |
| T2 next day | `D[2026]k, R[2026]k` | 2024 not retried (marker under a week) |
| T2 eight days later (marker aged) | `D[2024]k, R[2024], D[2026]k, R[2026]k` | retried, aux built |
| T3 a persistent rush failure, day 1 / 2 / 3 / after a week | tried / `D[2026]k, R[2026]k` / same / tried | **once a week**; the marker is the one the job wrote (nothing was planted) |
| T4 no store at all, its build raises | `D[2024]k` | the job raises, as it always did; **no marker written**, next day retried at once (the matchup data's own backfill is not throttled) |
| T5 first run, store present, rush fails for both seasons | day 1 tries both; day 2 daily only | a first failure waits a week (see below) |
| T6 everything complete | `D[2026]k, R[2026]k` | only the daily |

- Raw files: after every scenario nothing of 2024 or 2025 is left on disk, including the T2 and T4 failures. One season on disk at a time.
- **A note, not a blocker (T5):** the marker is stamped before the attempt, so a transient failure on the very first try (a network blip) leaves
  the 2024 / 2025 aux missing or incomplete for up to a week, and the profiles stamp it "incomplete" / "missing" meanwhile. That is what the
  weekly throttle trades for not downloading ~77 MB a day on a persistent failure, it is disclosed, and 2026 (the daily season) is not affected.
- The guards' fake `aux_complete` in `guards_matchups.py` treats a missing flag as complete; the real one is strict (C.2). See R5.

## C.5 Mutation, on the round-3 tree

All run on a copy of the tree (`python3 -B`, `__pycache__` cleared before each mutant, scratch deep store and ledger), layer mutants under
`tests/guards_nfl_rush_data.py`, job mutants under `tests/guards_matchups.py`. The control (a comment-only change) stayed green every time.

| set | mutants | caught | survived |
|---|---|---|---|
| my original 45 on the round-3 tree (three strings adapted again: injury week, team-snap total, split-half) | 45 | **45** | 0 |
| the re-check's new set: B1 in the tallies and at the units, the old snap total, an absent week reading `{}`, the stamp, coverage, refresh isolation, licence tags, hit + sack proxy, share clamp, `act_any_snap`, schema kind priority, ST-only rows (N1-N15), and the job (J1-J9) | 24 | **24** | 0 (**N12, the one survivor last round, is caught: K_FRAMES is pinned**) |
| K_FRAMES in the other two frames (2026 fit-only; 2023 full) | 2 | **2** | 0 |
| G1: `complete` always True; `aux_complete` ignoring the flag; the skip without `aux_complete`; the no-pbp guard removed; `expected_sources` dropping each of snaps, injuries, pfr_pass, pfr_rush, participation; the stamp ignoring completeness; the "nobody listed" rule reverted; NGS absence ignored (two ways); the `RUSH-incomplete` note removed; `absent` hidden from the summary | 16 | 15 | **1: G1-b2** |
| the job: `rush_due` ignoring completeness / ignoring alignment; `due` ignoring the marker; marker never saved; healthy-store failure raising; retry window 0; marker saved for a missing store; wrapped failure leaving its files; wrapped failure falling through to the rush refresh; marker stamped 0; window comparison inverted; one marker for every season; a missing store throttled; `NFLM-backfill` note removed | 14 | 9 | **5: JR-b, JR-e, JR-f, JR-h, JR-k** |
| source text generated from the tag (text ignores the tag; the pfr credit says pbp; a derived tag keeps one source; the ngs credit says pbp; the `source_tag` column dropped) and the by_box note dropped | 6 | 5 | **1: R4-b** |
| total | 107 | **100** | 7 |

(One of my own mutants in this set was invalid and is not counted: "pfr credit dropped" added a duplicate dict key ahead of the real one, so the
last key won and the code did not change. It was replaced by the five source-text mutants above.)

The seven survivors, read:

- **G1-b2, `aux_complete` lenient on a missing flag** (`aux.get("complete", True)`): the docstring promises that an aux without the flag
  reads incomplete, and my C.2 run shows the code does; no guard pins it, in either guard file (the `guards_matchups` fake is itself lenient).
  Reachable only by an aux built before round 3, and none was ever deployed. **R5.**
- **JR-b (marker never saved) and JR-h (marker stamped 0):** both make the weekly throttle inert, which is exactly the daily 77 MB download R3
  was raised about, and both pass. The R3 guard pre-plants the marker (`marks={2024: 0.5, 2025: 8.0}`), so it pins the READ half only. The
  shipped code is right (line 378 saves it before the attempt, and T3 above runs the real job over four days with nothing planted), so this is
  a guard gap, not a defect. **R6.**
- **JR-e (marker also saved when the store is missing), JR-k (the missing-store shortcut in `due` removed), JR-f (the wrapped failure's own
  `_drop_raw` removed):** each differs only in an edge. JR-f ends in the same state, because the `finally` drops every prior season anyway;
  the files just sit on disk until the daily refresh is done (one extra season, about 77 MB, at the peak). JR-e delays the retry of a failed
  rush step after a matchup rebuild by up to a week. JR-k only matters if a marker is under a week old when the store has vanished. I would
  not add guards for these. Part of **R6** if it is cheap.
- **R4-b (the by_box note):** the note is in the shipped `schema.json` (78 `by_box` columns across `rb` and `qb`, each ending "box: NGS count
  2016-25, FTN where none (all 2026); unrestricted (owner's participation decision)"); no guard pins the text. **R7**, cosmetic.

## C.6 Recommendations, none blocking

- **R5** Pin `aux_complete` on a flag-less aux (`R.aux_complete({"store_built": 1.0}) is False`), and make the `guards_matchups` fake strict
  (`a.get("complete") is True`) so the job guards test the contract the job runs against.
- **R6** Make the R3 guard prove the JOB writes the marker: run `_run_job` once with a failing rush refresh and nothing planted, then a second
  time, and require that the second run skips the season (and that the marker's `ts` is within a few seconds of now). That catches JR-b and
  JR-h in six lines.
- **R7** Pin the by_box note (one `in` check on a `by_box` column's `source`) if a guard for the licence text is wanted at all.

## C.7 Not checked

- `tests/combo_audit_guards.py` (two runs of it were active; the orchestrator's isolated-tree run is the one that counts).
- A live network run of the job. The scenarios use injected modules and one real `R.refresh` with a scripted network, as before.
- The model-side files (`nfl_rush.py`, `nfl_rush_model_*`), and the Matchups live-view edits in `app.py`, `static/matchups.js` and
  `matchups.py` that sit beside the job side. I read only `nfl_data_job`, `_drop_raw` and the marker constants.
- **A slip of mine, disclosed:** the first run of `c1_g1.py` this round had no `DEEP_CACHE_DIR`, so it pointed at the repo's gitignored
  `data/deep`. That script deletes `nfl_rush_aux_*` files in the store it is given; there were none in `data/deep`, nothing was saved there
  (every refresh returned the "no matchup store" error), and `git status` is unchanged. It touched `data/errlog.db` (gitignored, which every
  guard run also touches). I re-ran it on a scratch copy of the delivered store, and every later run set `DEEP_CACHE_DIR` and `ERRLOG_DB`.

---

# CONFIRMATION 2 (round 4, 2026-10-24)

The Opus rule review's M8: `dec_trend` read usage windows built from a back's game list, and that list counted games that only PFR's snap
counts supply (special-teams-only, or an offensive snap with no play and no participation line), so unrestricted fields tagged "pbp" moved with
PFR rows. Round 4 takes a back's games from pbp / participation APPEARANCES only (the per-game `app` flag) and moves the PFR-inclusive
versions to `rb().any_snap.*` (`derived:pbp+pfr_snaps`, restricted). Re-derived on the real data with my own code; scripts are mine, in my
scratchpad (`r4_probe.py`, `r4_ds_probe.py`, `r4_k.py`, `b_poison4.py`, `b_control4.py`, `g_rebuild4.py`, `e_mutate8.py`). No git writes, no
repo file edited but this one.

## D.1 Verdict: AGREE TO SHIP

The licence fix does what it says. On real data no field tagged unrestricted moves with any PFR snap row I could plant, remove or shuffle; the
moved k's reproduce from the raw files; leakage is still clean; the foundation is byte-identical. I found no defect. Two guard gaps and two
notes are listed in D.6; none blocks the push.

| your question | answer |
|---|---|
| (1) no field a fitted feature can read moves with a PFR snap row | **confirmed**, 7 perturbations x 4 as-of points on ~54,000 profile leaves each, and 7 runs on the rebuilt dataset: 0 unrestricted-tag fields moved in any of them (D.2) |
| (2) the moved k's reproduce from raw | **confirmed**: six of the 18 independently, to the last printed digit; and my PFR-inclusive variant reproduces round 3's values (D.3) |
| (3) leakage still clean on one poisoned week | **confirmed**: 2024 week 10 and 2026 week 5, 0 feature differences; the positive control still fires (D.4) |
| (4) the foundation is byte-identical | **confirmed**: `8511346956bb` / `d2007503a6d8` / `766a60ed11ee`, `git status` clean on them |

## D.2 Probe 1: does any unrestricted field move with a PFR snap row?

**Design.** I did not rely on the guard's one planted line. `r4_probe.py` loads the delivered stores and aux stores (all nine seasons, all
complete), changes only the PFR rows in the aux (`snaps`, and for one variant `pfr_rush` / `pfr_def` / `pfr_pass`) in EVERY season, builds the
snapshot as of a (season, week) through the production `RP.build`, flattens every field of every RB, QB and team profile (`RP.flatten`), and
compares each leaf's whole dict (value, raw, n, base, basis). A leaf whose own `source` tag is restricted may move; one tagged unrestricted must
not. The tag decides only what is ALLOWED to move, so a mis-tagged field is exactly what shows up. Seven perturbations:

| perturbation | what it does |
|---|---|
| `plant_st` | a special-teams-only PFR line (0 offensive snaps, 5 ST snaps) for every back of the club in every game he is not listed in; +3 ST snaps where he is |
| `plant_off` | a 1-snap, 1% offensive PFR line (no play, no participation) for the same backs |
| `plant_both` | the two alternating |
| `no_skill` | every skill-position PFR line removed |
| `shuffle` | the PFR lines shuffled among players |
| `no_ol` | the offensive-line snap lines removed |
| `no_pfr_all` | `no_skill` plus PFR rush / defense / pass removed |

(Planting uses only backs who appear for ONE club in a season: a line planted for a traded back puts him on the wrong side of a game against
his old club, a conflict of my own making; I hit it once and set that row aside, below.)

**Result, profile level** (as of 2024 wk 10, 2023 wk 9, 2025 wk 14, 2026 wk 5; ~51-57k leaves each; 28 runs): **0 unrestricted-tag leaves
moved in any run.** Restricted leaves moved as they should (positive control inside every run): `any_snap.*` (13-14 paths) under the plants,
`usage.snap_share` / `recent.snap_share` under the shuffle, `ol.starters.*` under `no_ol`, 18 paths under `no_pfr_all`. QB and team profiles:
nothing moved. Units that exist only because of a planted PFR line (0-6 per as-of point) carry one unrestricted leaf with n > 0,
`usage.onfield_plays_current` at n 1 and value 0.0, the placeholder every unit has; nothing else.

**Result, dataset level** (`r4_ds_probe.py`: the same perturbations through the production `run_build`, 2024 weeks 1-18 and 2026 weeks 1-8, every
column compared by its `schema.json` tag, rows matched on (game, gsis)):

| run | RB common rows | RB columns moved | QB |
|---|---|---|---|
| 2024 `plant_st` | 1,790 of 1,790 (no new rows) | 4, all restricted / outcome (`act_any_snap`, `p_any_snap_games`, `p_recent_snap_share`, `_n`); **0 unrestricted** | 0 columns moved |
| 2024 `plant_off` | 1,789 (1 traded-back row set aside; 832 new rows exist only because of a planted line) | 10 restricted / outcome; **0 unrestricted** | 0 |
| 2024 `plant_both` | 1,789 (384 new rows) | 10 restricted / outcome; **0 unrestricted** | 0 |
| 2024 `shuffle` | 1,790 | 8 restricted / outcome; **0 unrestricted** | 2 restricted / outcome |
| 2024 `no_skill` | 1,779 (11 rows exist only through PFR lines) | 10 restricted / outcome; **0 unrestricted** | 2 restricted / outcome |
| 2026 `plant_both` | 618 (101 new rows) | 6 restricted / outcome; **0 unrestricted** | 0 |
| 2026 `no_skill` | 570 (48 of 618 rows exist only through PFR lines) | 10 restricted / outcome; **0 unrestricted** | 2 restricted / outcome |

**Positive control: the probe would have caught M8.** The same probe against the ROUND-3 `nfl_rush_profiles.py` (my copy of the tree from the
last round): **23 unrestricted-tagged paths move** in `plant_st`, `plant_off` and `no_skill`, `usage.carries_pg`, `targets_pg`, `carry_share`,
`rb_carry_share`, `target_share`, `rz_` / `gl_carry_share`, the early / third / two-minute touch shares, ..., for 209 backs under the plants and 101 under the removal (2024 wk 10; 227 in 2023), all
tagged "pbp". Round 4: zero.

## D.3 The moved k's, from raw (`r4_k.py`, my own tallies of the nflverse files, none of the builder's code)

The builder's rule: odd vs even weeks of 2018-2025 pooled, a unit must carry the ball in both halves and clear 25 designed runs or 15 targets
a half, n the mean half denominator, k = n(1-r)/r. I compute each metric under three game sets: **involve** (a designed run or a target),
**onfield** (involve plus a participation on-field offensive snap: the round-4 rule) and **pfr** (onfield plus any PFR snap-count line of the
back's: the round-3 rule). Joined to the PFR file by `pfr_id` through `db_playerids` (the club code mapped through the opponent column for
OAK / SD / STL).

| metric | round-4 shipped | mine, onfield | round-3 shipped | mine, pfr | mine, involve |
|---|---|---|---|---|---|
| `rb_carry_share` | r .906, 528 units, n 171.43, **k 17.8** | r .906, 528, 171.43, **17.80** | 16.95 | 16.97 | 18.49 |
| `rb_carries_pg` | r .866, 508, 7.21, **1.12** | .866, 508, 7.21, **1.12** | 1.10 | 1.10 | 1.14 |
| `rb_targets_pg` | r .795, 508, 7.21, **1.86** | .795, 508, 7.21, **1.86** | 1.78 | 1.78 | 1.91 |
| `rb_target_share` | r .792, 528, 228.26, **59.93** | .792, 528, 228.26, **59.94** | 57.89 | 57.88 | 61.83 |
| `rb_rb_carry_share` | r .905, 528, 155.48, **16.35** | .905, 528, 155.48, **16.35** | 15.53 | 15.55 | 17.05 |
| `rb_sit_all` | r .905, 528, 438.66, **45.79** | .905, 528, 438.66, **45.79** | 42.94 | 42.99 | 48.03 |

Six of the 18 reproduce exactly under the round-4 rule (the 59.93 / 59.94 is the rounding of 59.935), and the same code with PFR lines added
returns round 3's figures to within 0.05: the movement is the game set and nothing else. The other twelve are the same family (the touch
shares, the situational on-field shares, red-zone and goal-line shares) computed by the same code from the same game set; I did not
re-derive them one by one. Also checked in the committed files:

- `reliability.json` and `persistence.json` differ from round 3's in **exactly 18 metrics, all `rb_*` usage**; no QB, team or defense figure moved. Persistence moves
  on 16 pooled figures by at most 0.006 (per season pair at most 0.018); the DECAY tendency stays 0.602 -> 0.75.
- **The dataset moved in the places it should.** Against round 3's file: the same 6,041 keys, one added column (`p_any_snap_games`, 905
  columns), **1,974 RB rows with a changed value** (the claim, exactly), the changed columns all in `p_usage_*` (55), `p_recent_*` (8) and
  `p_games_*` (2), tagged pbp (38), participation (25) and `derived:participation+pbp` (2). `p_usage_snap_share` (PFR) did not move. The QB file's hash is
  unchanged (`d7a42025`).
- **Full rebuild:** `run_build` over all seasons from the delivered stores and aux (`g_rebuild4.py`) reproduces `rb_games_2023_2026.csv.gz`,
  `qb_games_2023_2026.csv.gz` and `schema.json` **byte for byte**.

## D.4 Leakage, one poisoned week (`b_poison4.py`: shuffle every source row at the week and later, rebuild stores, aux and dataset through the production path)

| season, week | RB | QB |
|---|---|---|
| 2024 wk 10 | 994 common rows x 854 feature columns, **0 rows with a difference** (89 rows have an outcome column that changed, as expected) | 368 x 335, **0** |
| 2026 wk 5 | 520 common rows x 854, **0** | 182 x 335, **0** |

The row sets differ at the poisoned week itself (2024: 1 row only in the poisoned build; 2026: 6 only clean, 2 only poisoned), because a row of the
week exists when someone is listed in that week's PFR / Sleeper data: the outcome side, as in m2; their features are not compared. Positive
control, the deliberately leaky builder (the week's own games folded in before the rows are written, `b_control4.py`): **92 of 92 week-10 RB rows
and 32 of 32 QB rows differ**, in every profile column. The poison harness shuffles week >= W rows, so it cannot see a licence leak; that is what
D.2 is for.

## D.5 Mutation, on the round-4 change (`e_mutate8.py`, a copy of the tree with `research/data`, the control green)

| set | mutants | caught | survived |
|---|---|---|---|
| the leak paths: usage windows over every PFR-listed game; an appearance that counts a PFR offensive snap; one that counts a PFR special-teams snap; the recent window; team-scoped denominators; the per-game denominator; `games.current` | 7 | **7** | 0 |
| tags and columns: `any_snap` tagged pbp; the games columns tagged pbp; `p_any_snap_games` left out; the whole `any_snap` block written (size); `any_snap.games` over appearance games | 5 | **5** | 0 |
| the k defaults at round 3's values (carry share, target share) | 2 | **2** | 0 |
| an appearance ignoring participation on-field snaps | 1 | 0 | **M9** |
| an appearance ignoring a kneel / aborted snap | 1 | 0 | **M10** |
| the current on-field charted plays summed over PFR-listed games (`onfield_plays_current`) | 1 | 0 | **M11** |
| the on-field dropbacks summed over PFR-listed games (`tgt_per_db` n_current) | 1 | 0 | M12 (equivalent) |
| snap share over appearance games; the `any_snap` recent window over appearance games (both display, restricted) | 2 | 0 | M13, M15 |
| total | 20 | **14** | 6 |

- **M11 is a real leak path the guards do not see.** Put back, it makes `usage.onfield_plays_current` (tag participation, unrestricted) move with
  PFR-only games: my real-data probe finds 78-94 backs moving at three of the four as-of points. The guard's planted-line fixture (G6) gives the
  PFR-only game no participation-charted plays, so the sum adds zero. The shipped code is right (D.2: zero). **R8.**
- **M9, the participation clause**, is not pinned either: no fixture back is on the field with no touch. The clause is what takes carry share
  from k 18.49 to 17.80 (the table above), so a regression would silently move every usage k. **R8.**
- **M12 is equivalent on real data** (a back with no participation line has zero on-field dropbacks; my probe finds no movement under it). M10 is the rare
  RB kneel / aborted-snap term. M13 and M15 change restricted display fields only. None is a licence leak.
- The builder's own five round-4 mutants (`mutants/mut_r4.py`) overlap my first two sets; I did not run them.

## D.6 Notes and recommendations, none blocking

- **R8** In the M8 guard, give the PFR-only game participation-charted team plays (so `sit_all` / `of_db` sums over it are non-zero), and add a
  back who is on the field per participation with no touch (counts as a game) beside the PFR-only back (does not). That catches M9 and M11.
- **N1, 2026 uses a narrower game set than 2023-2025.** There is no participation file for 2026, so there a back's game is a touch only; in
  2023-25 about 12% of RB appearance-games (222 / 199 / 186 of ~1,650 a season) are on-field with no touch. For the 49 backs with 100+ designed runs in 2025,
  carry share changes by 0.0 points at the median under a touch-only game set (largest 2.64, 6% of backs by more than one point) and carries per game by
  at most 8%. The report says "seasons that have participation"; it does not size the shift. A model fit on 2023-25 and judged on 2026 sees it. One
  sentence in section 0.3 would do.
- **N2, who has a row still depends on PFR.** A back PFR lists only for special teams or one offensive snap gets a row with `act_played` 0 /
  `act_any_snap` 1 (11 of 1,790 RB rows in 2024; 48 of 618 in 2026 through week 8), by design (m2). Their features are the unrestricted ones. And `act_played`
  itself is tagged `derived:participation+pbp+pfr_snaps`, so a PFR-derived label reaches the model's frame as `y_played` (a target, not a feature;
  `nfl_rush.py` line 63 keeps it out of the features). Whether the licence default extends to a label is a policy question for the model side, not a defect of this layer.
- `p_usage_carry_share` and its siblings are tagged "pbp" although the game set behind them now reads participation too. Both are unrestricted, so the
  licence gate is unaffected; the provenance label is incomplete.

## D.7 Guards and the rest

- On the main tree, scratch deep store and ledger: `guards_nfl_rush_data.py` **196 / 196**; `guards_matchups.py` **47 / 47**;
  `guards_nfl_matchup_data.py` **149 / 149**; `guards_nfl_rush_model.py` 132 / 132 (not asked for; run because the model guard also
  checks `dec_trend`'s inputs); pyflakes clean on `nfl_rush_data.py`, `nfl_rush_profiles.py`, `research/nfl_rush_dataset.py` and `tests/guards_nfl_rush_data.py`.
- Hashes match report section 10: `nfl_rush_data.py` `247cc625` (unchanged since round 3), `nfl_rush_profiles.py` `cf230d8c`,
  `research/nfl_rush_dataset.py` `82e4aa12`, `tests/guards_nfl_rush_data.py` `d2ee444c`, `rb_games` `f39a12ed`, `qb_games` `d7a42025`,
  `schema.json` `ec18b369`, `reliability.json` `25e9bc1d`.
- **The full suite**, `tests/combo_audit_guards.py` (no other run was active; scratch deep store and ledger; this layer's files and the two guard files byte-identical to my mutation copy; the
  orchestrator's unrelated edits to `kalshi_nfl.py` / `nfl_matchup.py` were in flight and are not mine): **3,091 passed, 0 failed, exit code 0**, read from the exit code, not a pipe.
- **Not checked:** the model-side files beyond running their guard; a live network run of the job.
