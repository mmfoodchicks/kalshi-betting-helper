# Opus review: the NFL rushing data layer (RB + QB)

Reviewer: a fresh Opus instance with no stake in the build. 2026-10-23, ~21:37-23:30 UTC. Brief:
`<scratchpad>/review_rush_data_brief.md`. I did not read the Sonnet review or
`research/reviews/nfl_rush_data_orchestrator.md`, which appeared while I worked.

Under review, hashed when I started and again at the end (unchanged): `nfl_rush_data.py` 9b27b86c,
`nfl_rush_profiles.py` 144e2fc6, `research/nfl_rush_dataset.py` 39174eb4, `tests/guards_nfl_rush_data.py` 80298759,
and the eight files in `research/data/nfl_rush/` (all equal the report's §10).

The agreed foundation is byte-identical before and after my work: `nfl_matchup_data.py` 85113469,
`nfl_matchup_profiles.py` d2007503, `dataset_2023_2026.csv.gz` 766a60ed11ee..., and `git diff` is empty.

My only repo write is this file. Everything else is under
`<scratchpad>/review_rush_data_opus/` (called `W` below):
- `work/`: my scripts and their outputs;
- `deep/`: my copy of the builder's deep store;
- `leak/`: the poisoned raw files and their rebuilds;
- `mut/`: the mutation tree.

Python ran with `VIGIL_NO_BG=1`. Raw files were read with my own parser (`work/rawparse.py`, csv module, run with
`python3 -I`), never with the builder's counting code. Wherever I used the builder's code, I say so and say why: to diagnose
its units, to rebuild the dataset for the leakage test, or to time it.

## 1. Verdict

**AGREE AFTER FIXES.**

### What holds

The counting definitions are right, and I reproduced them exactly from raw:
- sacks, kneels, scrambles, aborted snaps, the five-way QB partition, the Sleeper reconciliation, the PFR and NGS
  joins, the sneak rule, and the Kalshi-settlement inference;
- the as-of machinery does not leak. I poisoned every source at and after a week, on 2025 week 10 and 2026 week 5, and
  compared 192,673 feature cells. No profile-derived cell moved. The only 802 that moved are the Sleeper projections I
  poisoned myself, which pass straight through;
- the injury status is the pre-kickoff game status;
- the DECAY rule's input is right: 0.6025 / 0.6011 from my own code, against the builder's 0.602 / 0.600;
- 71 of 71 hand-checked dataset cells match raw.

### What does not hold

**BLOCKER.** The split-half and year-over-year figures for running backs were not measured on running backs.
- The RB unit set is every non-QB with a designed run. On usage metrics 302 of 860 units are WRs and 30 are TEs; on
  receiving metrics 302 of 614 are WRs.
- Restricted to RB/FB, with the same pairs and the same code:
  - RB catch rate goes from r 0.441 / k 47 to **r 0.032 / k 877**, that is noise;
  - carry share goes from 0.966 / k 6 to **0.905 / k 18**;
  - aDOT goes from 0.921 / k 3 to **0.538 / k 23**;
  - line yards goes from 0.436 / k 102 to **0.26 / k 225**: three Taysom Hill (TE) seasons whose line yards are forced
    to 0 do that.
- My own raw code reproduces both the builder's figure and the RB-only one.
- The report's §5.1 tables, its §5.2 reading ("usage is known quickly: carry share r 0.97 (k 6)") and the shipped k
  table are wrong as RB figures.
- The dataset's RB receiving values are under-shrunk: a median 1.5 points of catch rate, and up to 8.1 yards of aDOT.

**MAJOR**, five findings:
- **M1.** A week with no injury report reads "active" for every player, with a source string claiming a report.
- **M2.** In the proposed `nfl_data_job` diff, a failed or killed rush backfill is never retried, and can leave about
  154 MB of raw files on the 1 GB disk forever.
- **M3.** A missing or stale aux store degrades the profiles silently.
- **M4.** `schema.json` mislabels the kind, meaning or source of more than 20 columns.
- **M5.** 15 of my 31 non-equivalent mutants survive the guard.

### Required fixes, all of them before ship

1. Restrict the RB units to RB/FB.
   - In `unit_profiles`, filter `T.rb` by `pos in ("RB", "FB")`. Better: never let a TE or WR rusher reach an "RB" unit
     or the RB line-yards tally.
   - Re-run `measure`, the two-pass logic included.
   - Rewrite §5.1 and §5.2 and refresh `_K_DEFAULT`.
   - Rebuild the dataset and check DECAY again. Its inputs are unaffected (§3, B1), but that should be confirmed.
2. `RushSnapshot._report` returns None when the cutoff week is absent from `aux["injuries"]`, and ledgers it (M1).
   - My mutant STAT-3 is that fix, and the guard suite stays green on it.
   - Add a guard: a snapshot as of a week with no report must read "unknown".
3. In the §8.1 diff:
   - backfill on aux status, not only on the matchup store;
   - delete the backfill's raw files in a `finally`;
   - sweep any leftover backfill raw files on later runs (M2).
4. Stamp aux status on every profile and ledger a missing or mismatched aux (M3).
5. Correct `schema.json`: the kinds, the three `ol_starters_*` meanings, the containment leaves as proxy, and the
   sources (M4).
6. Close the guard gaps that are real (§4), at minimum:
   - team scoping of `recent` and `snap_share`;
   - the leave-one-out weight across seasons;
   - a full path-to-metric map;
   - the status code mapping;
   - the OL `out_report` threshold;
   - scramble TDs;
   - two-QB exposure.
7. Disclose in the report that the dataset's shrunk `value` columns use the 2018-2025 table, which includes 2025.
   Better: write the 2023-2025 rows with `k_fit_only` (m3).

## 2. Claims table

The handback message was not given to me, so this table covers the report's numbered claims (§2.2-§2.4, §4-§6, §8.4, §9,
§10). "Mine" is my own code from raw unless the row says otherwise.

| claim (report) | theirs | mine | verdict |
|---|---|---|---|
| §4.1 QB player-games with a Sleeper box | 2,046 | 2,046 | AGREE |
| QB attempts exact / yards exact | 100.0% / 99.76% | 1.0000 / 0.9976 | AGREE |
| QB attempts exact without kneels / aborted / scrambles | 63.4 / 88.3 / 35.0% | 0.6339 / 0.8832 / 0.3504 | AGREE |
| Sleeper includes kneels: QB-games with a kneel exact with / without | 749 / 0 | 749 / 0 | AGREE |
| RB player-games; attempts / yards exact | 5,106; 99.98 / 99.96% | 5,106; 0.9998 / 0.9996 | AGREE |
| RB without kneels / aborted / scrambles | 99.98 / 99.67 / 99.98% | 0.9998 / 0.9967 / 0.9998 | AGREE |
| Sacks with a rusher id 2023-25 | 0 | 0 (no sack=1 row carries a rusher id) | AGREE |
| Dataset act_rush vs Sleeper, QB | 100.0 / 99.75% on 1,990 | 1.0 / 0.9975 on 1,990 | AGREE |
| Dataset act_rush vs Sleeper, RB | 99.98 / 99.96% on 4,915 | same on the 4,915 `played==1` rows; 5,315 rows have Sleeper gp >= 1 | AGREE (filter unstated, m2) |
| §4.2 QB rushes 2023 / 24 / 25 | 2,285 / 2,352 / 2,215 | identical | AGREE |
| designed / sneak / scramble / kneel / aborted | 453/518/357; 295/267/259; 1,029/1,060/1,087; 428/405/433; 80/102/79 | identical | AGREE |
| sneaks from the rule 2023+ | 0 | 0 | AGREE |
| RPO keeps / RPO dropbacks | 56/29/67; 555/349/433 | identical | AGREE |
| five-way partition mismatches | 0 | 0 | AGREE |
| §4.3 scrambles vs PFR 2024 | 1,012 v 1,007, 47 QBs, r 0.999, 72% exact, 100% within 3 | 1,012 v 1,007, 47, r 0.9991, 0.723, 1.000 | AGREE |
| scrambles vs PFR 2025 | 1,021 v 1,028, 45, r 0.998, 73%, 98% | 1,021 v 1,028, 45, r 0.9985, 0.733, 0.978 | AGREE |
| RPO runs FTN v PFR 2022-25 | 888/1,224/839/1,585 v 276/247/282/309; r -0.12/0.09/0.05/0.11 | same; r -0.124/0.085/0.048/0.113 | AGREE |
| PFR carries = official rushes 2023 / 24 / 25 | 100 / 99.91 / 99.96% | 1.0 / 0.9991 / 0.9996 (2,276 / 2,254 / 2,251 player-games) | AGREE |
| rushing_yards differs from yards_gained 2023 / 24 / 25 | 2 / 3 / 1 | 2 / 3 / 1 of 14,606 / 14,686 / 14,601 | AGREE |
| §4.4 location blank / middle / L-R with no gap | 0-0.05% / 25-31% / 0 | 0.01-0.05% / 24.9-26.7% (2022-25) / 0 | AGREE |
| §4.5 sneak rule precision 2022-25 | .989 / .986 / .992 / .996 | identical | AGREE |
| sneak rule recall 2022-25 | .945 / .942 / .936 / .896 | identical | AGREE |
| §2.2 PFR crosswalk: players.csv / DP added / disagree | 16,988 / 8 / 8 | 16,988 / 8 / 8 | AGREE (the `nfl_rush_data` docstring says 22,694 and 7: N2) |
| §2.3 NGS weekly rows >= 10 attempts; no QB rows | yes | min 10; 0 QB rows | AGREE |
| RB runs in games with an NGS row, 2018 / 2025 / 2026 | 73.1 / 74.1 / 72.7% | 0.731 / 0.741 / 0.727 | AGREE |
| 10+ carry games with an NGS row | 99.6-100% | 519/521, 569/569, 185/185 | AGREE |
| §2.4 2026 aux weeks: PFR 1-6, NGS 1-7, injuries 1-7, no participation | as stated | as stated (`work/status_missing.py`) | AGREE |
| §5.1 `rb_carry_share` r / k | 0.966 / 5.99 | builder's units: 0.967 / 6.0. RB/FB only: **0.906 / 17.8**; random weeks 0.891 / 21.4; first v second half 0.766 / 54.6 | DISAGREE as an RB figure (B1) |
| `qb_dsg_rate` r / k | 0.917 / 36.34 | 0.917 / 36.34 (odd/even); 0.890 / 51.6 (random); 0.912 / 43.0 (halves) | AGREE |
| `qb_scr_rate` r / k | 0.755 / 79.94 | 0.755 / 79.93; 0.763 / 79.3; 0.768 / 82.1 | AGREE |
| `def_dir_ypc_dev` r (claimed noise) | -0.009, none | -0.009 (1,717 cells); 0.027 / k 955; -0.025 | AGREE |
| `def_box_ypc_dev` r | 0.013, none | 0.013 (741 cells); -0.016; -0.058 | AGREE |
| `def_scrambles_oe` r / k | 0.038 / 359 | 0.039 / 357; 0.056 / 242; 0.096 / 136 | AGREE ("near noise" holds on every split) |
| §5.1 RB receiving: `rb_catch`, `rb_ypt`, `rb_yac_rec`, `rb_adot` | r .441 / .389 / .571 / .921 | RB/FB only: **.032 / .143 / .181 / .538** (k 877 / 172 / 101 / 23) | DISAGREE (B1) |
| `rb_chk_share`, `rb_screen_share` | r .892 / .635 | RB/FB only: **.348 / .427** | DISAGREE (B1) |
| §5.1 RB usage (`rb_sit_*`, `rb_rz_share`, `rb_gl_share`, `rb_target_share`, `rb_carries_pg`, `rb_targets_pg`) | r .846-.977 | RB/FB only: .689-.926 (e.g. sit_all .905 k 46, gl_share .719, target_share .792) | DISAGREE (B1) |
| `rb_snap_share`, `rb_tgt_per_db*`, `rb_third/two_minute_touch_share` | as tabled | unchanged or within 0.02 on RB/FB only | AGREE |
| `rb_line_yds` | r 0.436, k 102, 1,004 units | RB/FB only **0.26, k 225**; 1,004 = 502 units counted twice (N4) | DISAGREE (B1) |
| `rb_fd` / `rb_td` / `rb_success` | .143 / .133 / .211 | RB/FB only .115 / .112 / .196 | DISAGREE, small (B1) |
| §5.1 YoY column, RB usage / receiving | e.g. carry share .926, catch .568, aDOT .924 | RB/FB only .648 / .039 / .429 | DISAGREE (B1) |
| `reliability.json` / `persistence.json` / `sneak_rule.json` reproduce from the final code | yes | byte-identical on a re-run into my dir (builder's code, `work/remeasure.py`) | AGREE |
| §5.2 "usage is known quickly: carry share r 0.97 (k 6), on-field 0.88-0.98, goal line 0.85" | | RB/FB only: carry share .905 (k 18), on-field .689-.926, goal-line share .719 | DISAGREE (B1) |
| §5.2 defense direction / side / box noise; QB legs are traits; QB OE near noise | | reproduced (rows above) | AGREE |
| §5.2 team context r (qb_keep .89, rpo .76, neutral .57) | | reproduced by re-running the builder's code only | CANNOT VERIFY independently |
| §5.3 DECAY pooled r / without 2024-25 | 0.602 / 0.600 -> 0.75 | **0.6025 / 0.6011** (own raw code); 9 of 10 metrics identical to 3 dp; rb_dir_share .513 v .502 (my league base) | AGREE |
| team tendencies / player traits | 0.535 / 0.70 | 0.535 / 0.701 | AGREE |
| §6 rows / columns | 6,041 x 896; 2,201 x 378 | same | AGREE |
| rows that did not play | 523 | 523 | AGREE |
| `p_games_cur` equals earlier games for the team | 6,041 / 6,041 | 6,041 / 6,041 (own raw appearance rule) | AGREE |
| `status` reads active / questionable only | yes | yes (both files) | AGREE |
| rebuild reproduces the dataset byte for byte | yes | my clean rebuilds from raw (2025 wk 1-10, 2026 wk 1-5): 0 differing cells against the committed file | AGREE |
| §8.4 both snapshots / 228 profiles | 3.0 s, 137 MB / 0.4 s | builds 3.02-3.08 s (two runs), peak RSS 137 MB / 0.41-0.43 s (`work/perf.py`) | AGREE |
| §0.1 rushing snapshot alone | ~1.8 s, ~100 MB | 1.3-1.5 s, peak 92 MB | AGREE |
| §7 guards; agreed guards | 87 / 0.4 s; 149 / 149 | 87 passed 0.38 s; 149 passed | AGREE |
| §7 mutation run | 40 of 41 caught | their log says so; my own mutants: 16 of 35 caught, 15 real gaps | AGREE on their run, but it overstates the guards' reach (M5) |
| §9.6 Kalshi settles kneels and scrambles as rushing yards | 239 / 239; 227 without kneels; 94 without scrambles | 250 / 250 incl. Jan 2026 regular season; 236 / 98; all 14 kneel and 152 scramble discriminating cases favour inclusion | AGREE (an inference, as stated) |
| §9.8 agreed `qb()["scramble"]` reads 0.0 raw for every QB; nothing downstream reads it | | 55 of 55 QBs with 50+ dropbacks read v 0.0 (Allen, Jackson, Hurts eb .0156 / .0183 / .0166); no repo reader of that field | AGREE, with a caveat (m5) |
| §10 hashes | | all equal | AGREE |

## 3. Findings

### BLOCKER

**B1. The RB reliability, YoY and k figures were measured on a population that is about 40% WRs and TEs.**

The mechanism:
- `add_game` puts every non-QB designed-run rusher into `T.rb`: the `else` of `if rpos == "QB"`.
- `unit_profiles` turns every key of `T.rb` into an `("rb", pid)` unit.
- `_gate` admits usage metrics on "25+ carries or 15+ targets each half" and receiving metrics on "15+ targets each
  half". So any WR or TE with one jet sweep in each half and 15 targets becomes an "RB" unit.
- The rule in the script's own header says "Running backs: player-scope ...". The code does not implement the written
  rule.
- The defect was there at the first, pre-registered run: `reliability.first.json` equals the shipped file except one
  by-season `mean_n_half`.

Evidence: `W/work/diag_units2.py`, using the builder's `unit_profiles` and `_pairs` on my deep copy and splitting the same
rows by players.csv position.

```
rb_catch          non-RB 330/614  r 0.441 -> 0.032  k 46.85 -> 876.94  YoY 0.568 -> 0.039  {'WR': 302, 'RB': 282, 'FB': 2, 'TE': 28}
rb_adot           non-RB 330/614  r 0.921 -> 0.538  k 3.06 -> 23.09   YoY 0.924 -> 0.429
rb_ypt            non-RB 330/614  r 0.389 -> 0.143  k 58.16 -> 171.91 YoY 0.539 -> 0.101
rb_yac_rec        non-RB 326/609  r 0.571 -> 0.181  k 19.59 -> 101.4  YoY 0.714 -> 0.31
rb_chk_share      non-RB 167/305  r 0.892 -> 0.348  k 4.48 -> 52.06
rb_carry_share    non-RB 332/860  r 0.966 -> 0.905  k 5.99 -> 17.95  YoY 0.926 -> 0.648
rb_sit_all        non-RB 332/860  r 0.977 -> 0.905  k 10.36 -> 46.19 YoY 0.951 -> 0.589
rb_gl_share       non-RB 153/376  r 0.846 -> 0.719  k 2.25 -> 4.87   YoY 0.823 -> 0.474
rb_line_yds       non-RB   6/1004 r 0.436 -> 0.26   k 102.1 -> 225.45  (3 TE units counted twice; line yards are 0 for any non-RB)
rb_fd / rb_td     non-RB   3/502  r 0.143 -> 0.115 / 0.133 -> 0.112
```

I confirmed it independently from raw with my own code:
- `W/work/rel_raw.py`, carry share: builder population r 0.967 / k 6.0; RB/FB only 0.906 / k 17.8.
- An inline rb_catch script: builder population 614 units, r 0.441, k 46.9; RB/FB only 284 units, r 0.032, k 877.0.

The impact on shipped values is in the dataset's own `_raw` / `_n` columns, with the prior backed out of value, raw, n and
k:

```
p_receiving_catch    |shipped - RB-only k|  median 0.0148  p90 0.0389  max 0.0902
p_receiving_yac_rec                         median 0.3382  p90 0.8734  max 2.0017
p_receiving_adot                            median 0.3087  p90 1.3167  max 8.1234
p_rushing_line_yds                          median 0.0291  p90 0.0755  max 0.2338
```

The usage fields have no prior, so their values are raw. But their stamped `k` / `reliability`, and every sentence that
quotes them, are wrong. The DECAY inputs `rb_dir_share` and `rb_sg_share` are unaffected: 21 and 3 non-RB rows of 3,514
and 502, with YoY identical. So DECAY 0.75 stands.

**Fix:**
- restrict RB units to RB/FB, and keep TE / WR rushers out of the RB line-yards tally;
- re-run `measure`;
- replace the §5.1 rows, the §5.2 sentences and `_K_DEFAULT`;
- rebuild the dataset;
- add a guard that every `("rb", pid)` unit in `unit_profiles` has position RB/FB. A fixture WR with one carry and 15
  targets per half would catch it.

### MAJOR

**M1. A cutoff week with no injury report reads "active" for everyone, with a source that claims a report exists.**

`_report` returns `((aux["injuries"].get(self.week) or {}).get(team)) or {}`, an empty dict rather than None, when the
week is missing. So:
- `status` says "active" with source "nflverse injuries 2026 week 8 (final report, pre-kickoff)";
- `rb_room_out` is 0;
- `out_report` is 0.

The 2026 aux holds weeks 1-7. The week-8 Thursday report (NE / CHI, played 22 Oct) was not in the file pulled on 23 Oct,
so nflverse lags by days. That puts the prospective test window (weeks 8-11) and every production board at risk.

```
$ python3 -B W/work/status_missing.py
as of 2026 week 7: [('CHI', '00-0040236', 'out', ...week 7...), ('NE', '00-0036979', 'out', ...), ...]   rb_room_out 0.3008
as of 2026 week 8: [('CHI', '00-0040236', 'active', 'nflverse injuries 2026 week 8 (final report, pre-kickoff)'), ...]   rb_room_out 0.0
```

**Fix:**
- return None, which reads "unknown", when `self.week not in aux["injuries"]`, and ledger it (e.g. `RUSH-inj-missing`);
- consider "unknown" for a team with no rows in a week that has other teams' reports;
- guard it. Mutant STAT-3 is this fix, and the suite stays green on it.

**M2. In the proposed `nfl_data_job` (§8.1), a failed or killed rush backfill is never retried, and can strand raw files.**

`back` is decided only by `D.load_season(s) is None`. Once the agreed stores exist, the rush backfill and the cleanup loop
never run again. A simulation of the proposed function with stubbed refreshes (`W/work/jobsim.py`):

```
A run1 (rush backfill raises): stores [2024, 2025, 2026] auxes []     notes ['RUSH-refresh']
A run2 (network back):         stores [2024, 2025, 2026] auxes [2026] rush calls [[2026]]      <- 2024/2025 aux never built
B run1 (killed after the backfill): raw left ['ftn_2024', 'ftn_2025', 'participation_2024', 'participation_2025', 'pbp_2024', 'pbp_2025']
B run2:                        raw left [the same six files]                                   <- never removed
```

Why it matters:
- On the real files the six are about 154 MB: pbp 19.4 + 19.1, participation 49.7 + 49.1, FTN 8.3 + 8.1 MB.
- The old code's peak was one season, about 77 MB.
- A kill is likely: every push swaps the instance (CLAUDE.md).
- A future aux `SCHEMA` bump would also never rebuild the back seasons.
- With the back aux missing, 2026's prior-season on-field shares, extras, PFR and NGS vanish silently (M3).
- When the backfill raises, the same dict literal also skips that run's daily rush refresh.

**Fix:**
- include `R.load_aux(s) is None or not R.aux_matches(...)` in `back`;
- put the cleanup in a `finally`;
- delete leftover raw files for seasons outside `daily` on every run.

**M3. A missing or stale aux degrades the profiles silently.**

`W/work/robust.py`, profiles as of 2026 week 7, the same stores:

```
aux present      | Hurts designed per_game 2.0762 aborted pg 0.1646 | Barkley snap_share 0.6256 onfield 0.776 | status active | OL out_report 0
aux missing      | Hurts designed per_game 2.1966 aborted pg 0.0    | Barkley snap_share None   onfield None  | status unknown | OL out_report None
aux other store  | Hurts designed per_game 2.1966 aborted pg 0.0    | Barkley snap_share 0.6256 onfield None  | status active | OL out_report 0
                 | any aux flag in profile: none | ledger []
```

Aborted snaps become designed runs, and on-field shares vanish. Nothing in the profile or the ledger says so. Only the
research loader prints a WARNING.

Truncated sources behave as follows:
- A truncated pbp or participation file raises `EOFError` / `IndexError`. That is loud, and the §8.1 try / except would
  ledger it.
- A truncated PFR, injury or snap file builds silently with fewer games: 116 of 272 PFR games, 9 of 18 injury weeks,
  115 of 272 snap games. `missing` is empty and nothing is ledgered.

**Fix:**
- stamp `aux: present / missing / other-store` on each profile;
- `errlog.note("RUSH-aux", ...)` once per build;
- in `build_aux`, record the last week read per source and flag a season that ends early.

**M4. `schema.json` mislabels columns the model agent is told to read it for.**

The schema takes each column's field from the first row it sees (2023 week 1). From `W/work/schema_kinds.py` and a
direct read:
- **Kinds.** Nine columns are labelled "observed" but are EB-shrunk ("modelled") on a 2025 week-10 profile:
  - `q_scramble_vs_pressure`, `_no_pressure`, `_man`, `_zone`, `_single_high`, `_two_high`;
  - `q_scramble_yds_per_pressured_db`;
  - `dq_pressure`, in both files.
- **Meanings.** `ol_starters_out_report`, `_missed_last_game` and `_continuity` all mean "no game this season before the
  cutoff".
- **Proxy label.** The `dq_containment_proxy_*` leaves are modelled / observed, not proxy. §3.4 says "(kind proxy)"; the
  `kind: proxy` set on the block never reaches the leaves.
- **Sources.**
  - `p_usage_snap_share` says "nflverse pbp"; it is PFR snap counts.
  - `d_box_*` and `p_rushing_by_box_*` say pbp; they are participation (NGS) boxes.
  - `q_pressure_pfr` and `dq_pressure_pfr` say "participation"; they are PFR.

**Fix:**
- take kinds and bases from a full-data reference snapshot, or record the set of kinds seen per column;
- set `kind: proxy` on the containment leaves;
- correct `_source`.

**M5. The guard leaves the brief's own target classes open.** 16 of my 35 single-point mutants are caught. Of the 19
that survive, 4 are equivalent and 15 are real gaps (§4). Survivors fall in the classes the brief names:
- team scoping: `recent`, `snap_share`, the `qb(team=)` filter;
- the leave-one-out weight across seasons, and the QB-rate k inside the OE. The fixture sets `qb_scr_rate` k 0, which
  masks it;
- k application: the path-to-metric map covers only 31 paths, the reliability stamp, the scramble-split prior form;
- the OL `out_report` threshold;
- status codes.

**Fix:** the specific guards are listed per survivor in §4.

### MINOR

- **m1. Impossible shares from additive priors.**
  - `d_box_by_offense_personnel_10_heavy` is below 0 on 409 of 6,041 RB rows (min -0.0481); `_11_heavy` (17 rows),
    `_13_light` (3) and `_22_light` (5) also go below 0. Cause: own EB share plus the league offset.
  - `p_usage_tgt_per_db_vs_zone` is 1.20-1.24 on 4 rows: one target on one on-field dropback, times the league
    zone / overall ratio.
  - `p_rushing_back_ryoe_carry_coverage` is above 1 on 40 rows (e.g. 1.004).
  - Fix: logit-scale offsets, or clamp priors to [0, 1].
- **m2. `played` counts only offensive participation.** 400 RB rows from 2023-25 have Sleeper gp >= 1 and `played = 0`;
  397 of them had special-teams snaps only (PFR `st_snaps`). Kalshi settles on "at least one snap". The report's 4,915
  is the `played == 1` subset, unstated. Fix: add `st_only` or `any_snap`, and state the filter.
- **m3. The dataset's shrunk `value` columns use the 2018-2025 k table, which includes the validation season.**
  - `meta.json k_source` says so, but §6 says only "the k table it should use ... is `k_fit_only`".
  - Some k differ about 2x: `qb_scr_single_dev` 671 v 1,467, `qb_scr_nopress_dev` 997 v 2,053.
  - A 2025 validation on `value` columns is mildly contaminated.
  - Fix: write the dataset with `k_fit_only`, or say plainly that `value` columns must be re-shrunk from `_raw` / `_n`.
- **m4. The row universe for non-players comes from the post-week Sleeper snapshot.** It uses `proj > 0` and the QB1
  role. That is why `status` never reads doubtful or out. It is disclosed in part. Any "will he play" learning from these
  rows is post-hoc.
- **m5. §9.8 should add that the agreed `nfl_matchup.py` reads scramble-excluded QB splits.**
  - It reads `qb()["epa_db"]` and `["vs"][pressure / no_pressure / blitz / no_blitz]["epa_db"]`, all of which exclude
    scrambles, through `qb_sens`, for the `press_qb` / `blitz_qb` / `press_catch` evidence.
  - Those terms are weight 0 in `coefs.json`, where only `ptd` ships, but they are shown on cards.
  - The agreed research dataset writes `q_dropbacks`, `q_epa_press` and the rest from the same fields.
- **m6.** The §8.1 cost note leaves out that two backfill seasons' raw files now coexist: about 154 MB at peak against
  about 77 MB before.

### NIT

- **N1.** §5.1 says one by-season `mean_n_half` moved 5.44 -> 5.43. The diff of `reliability.first.json` against the
  shipped file is line 160, **4.35 -> 4.34**.
- **N2.** The `nfl_rush_data.py` docstring says "22,694 ids ... 7 more". That counts players.csv rows with a pfr_id; the
  map holds 16,988 + 8.
- **N3.** The `nfl_rush_profiles.py` docstring says "~0.8% of designed runs have no location". RB runs measure
  0.01-0.05%.
- **N4.** `rushing.line_yds` and `rushing.line.line_yds` are the same field at two paths, so `rb_line_yds` shows 1,004
  "units" (502 counted twice) and appears as two dataset columns.
- **N5.** `schema.json` has no `license` block. Every other committed table does, and §1 says "every committed table".
- **N6.** In `refresh`, and in the §8.1 diff, the cleanup is `except OSError: pass`. The suite allows it, but a failed
  delete on a 1 GB disk goes unseen.
- **N7.** `TL["rush"]`, `TL["rush_yds"]` and `TL["qb_scr"]` are written and never read. Mutants SACK-3 and SCR-3 are
  equivalent for that reason.
- **N8.** Some labels:
  - "pace" means plays per game;
  - the owner's "edge setting" and "QB yards allowed per scramble" are neither mapped to a proxy nor listed as missing
    in §3.5;
  - `designed.read_option` is kind "observed" although it is not available;
  - the basis of `totals.yds_pg`, "official rushing yards", is a sum of yards_gained classes. It differs only on
    laterals.

## 3a. Leakage (brief B)

Poison test, using `W/work/poison.py`, then `build_leak.py` and `cmp_leak.py`.

For 2025 week 10, every source was poisoned:
- pbp at week >= 10: yards, EPA, success, first downs, locations, gaps, shotgun, WP, score, down, distance and yard line;
- weeks > 10 additionally: 30% of plays deleted, rusher and receiver ids shuffled, scramble, aborted and dropback flags
  flipped;
- participation: box, personnel, pressure, man/zone, coverage, and on-field players at week > 10;
- FTN: RPO, sneak, screen, box, rushers, read;
- PFR rush, def and pass: every numeric column;
- snap counts: every count;
- NGS 2025 at week >= 10;
- injuries at weeks > 10 only;
- Sleeper projections and box at week >= 10.

Then I rebuilt the agreed store, the aux and the dataset rows from the poisoned files. A clean twin was rebuilt from the
real files by the same path.

```
[rb] rows clean 980 poison 980; clean rebuild vs committed: differing cells 0
   weeks < 10: differing cells 0
   week 10: FEATURE cells compared 83,323, differing 662: only sl_proj_* (the poisoned Sleeper inputs, passed through)
[qb] rows 361 / 361; week 10: FEATURE cells 11,390, differing 140: only sl_proj_*
2026 week 5 (no participation): rb FEATURE cells 85,900 differing 0; qb 12,060 differing 0; clean = committed, 0 cells
```

What this shows:
- No same-week PFR, NGS, snap-count, pbp, participation or FTN value reaches a feature.
- The OL starting five and missed-last-game read no game-W snaps; `ol_*` is identical under poisoned week-10 snaps.
- No feature is built from `sl_proj_*`. The schema labels them "observed (not provably pre-kickoff)", "revised post-week
  snapshot".
- `report_status` is a pre-kickoff designation:
  - Questionable players played 66.2 / 65.4 / 65.8 / 67.3% of games (2023-26);
  - Out players played 0-0.3%, Doubtful players 0-2.1%;
  - 2023's `date_modified` is before game day on 2,666 of 2,670 status rows and on game day for 4.

## 3b. Process integrity (brief F)

The sequence:
1. `prerun_hashes.txt` was written at 20:52:54 UTC with dataset script 262da6dc. `measure.log` logs the same sha at
   20:52:59 and the result "tendency r 0.602 -> decay 0.75". So the hash preceded the first number by 5 s.
2. At 20:47, `nfl_rush_profiles.py` b6f1ce12 still had `DECAY = 0.5` and an empty `_K_DEFAULT`. It was set to 0.75 after
   the run, as the rule says.
3. The 262da6dc file itself is not preserved, so **I cannot read the rule text as it stood at the run**. The earliest
   copy I found, `nfl_rush_dataset.after_measure_checks.py` at 21:06:58, has the same RULES header as the final file.
4. `persistence.first.json`, copied at 21:14:23 before the aux rebuild, is byte-identical to the shipped
   `persistence.json`. The shipped reliability differs only on line 160.
5. My re-run of `measure` on the final code reproduces all three jsons byte for byte.
6. The aux rebuild at 21:14 (the OL gsis fix) and the later profile edits changed no measured value.

My conclusion:
- I found no sign that the DECAY rule or the k rule changed after a number was seen.
- 0.602 / 0.600 sits on the 0.6 boundary. My independent 0.6025 / 0.6011 agrees.
- One plain statement is needed: the k rule as written ("running backs") was not what the code measured (B1). That was
  a defect from the first run, not a post-hoc change.

## 4. Mutation table (my own mutants)

The harness is `W/work/mutate_opus.py`. It works on a copy of the tree, runs `python3 -B`, sets
`PYTHONDONTWRITEBYTECODE=1`, clears `__pycache__` before every run, and keeps a private `ERRLOG_DB` and deep store. The
control and the fix probe stay green. Results are in `W/mut/mutation_results.json`.

Tally: 35 mutants; 16 caught; 19 survived, of which 4 are equivalent and 15 are real gaps.

| id | mutant | result | classification / guard that would catch it |
|---|---|---|---|
| SACK-1 | a sack counted as a scramble in the QB line | caught (partition) | |
| SACK-2 | sacks dropped from dropbacks | caught | |
| SACK-3 | sack yards added to team rushing yards | survived | equivalent: `TL["rush_yds"]` is never read (N7) |
| KNEEL-1 | kneel yards counted positive | caught | |
| KNEEL-2 | a QB kneel booked as a designed run | caught | |
| KNEEL-3 | kneels counted in team snaps (exposure) | caught | |
| SCR-1 | a scramble TD is not a QB rush TD | **survived** | real gap: no fixture scramble TD; pin `scoring.rush_td_rate` on one |
| SCR-2 | scrambles dropped from dropbacks (the agreed-layer bug) | caught (9 checks) | |
| SCR-3 | team QB-scramble count credits any rusher | survived | equivalent: `TL["qb_scr"]` never read |
| RPO-1 | RPO rate counts only RPO runs | caught | |
| RPO-2 | RPO keep credited on a sneak | survived | equivalent: the line sits in the not-sneak branch |
| TEAM-1 | `recent` block reads all his teams | **survived** | real gap: pin the traded back's DEN `recent.carry_share` |
| TEAM-2 | snap-share numerator from all his teams | **survived** | real gap: give the traded back PFR snaps for both teams and pin `usage.snap_share` |
| TEAM-3 | RB room reads the team's defensive games | caught | |
| TEAM-4 | `qb(pid, team)` ignores the team | **survived** | real gap (no caller passes a team yet): pin a two-team QB |
| CUT-1 | `season_tallies` keeps the cutoff week | caught (ValueError) | |
| CUT-2 | status reads next week's report | caught | |
| CUT-3 | priors one season too recent | caught | |
| CUT-4 | injuries keyed one week late | caught (crash) | |
| LOO-1 | OE numerator not left out | caught | |
| LOO-2 | leave-one-out weight always 1 (prior-season games at 0.75 over-removed) | **survived** | real gap: a two-season OE fixture |
| LOO-3 | QB rates inside the OE unshrunk | **survived** | fixture artifact (`qb_scr_rate` k 0); real gap: give it k > 0 |
| K-1 | reliability stamped k / (n + k) | **survived** | real gap: pin one shrunk field's reliability |
| K-2 | target share stamped with the carry-share metric | **survived** | real gap: the pinned map covers 31 paths; pin all, or generate the expected map |
| K-3 | scramble-split prior additive, not multiplicative | **survived** | real gap: pin `scramble.vs.pressure.prior` |
| K-4 | off-by-one k in the shrinkage denominator | caught | |
| OL-1 | six starters | caught | |
| OL-2 | `out_report` counts Questionable | **survived** | real gap: a Questionable starter in the fixture report |
| OL-3 | missed-last-game threshold 25% | **survived** | real gap (boundary): a starter on 40% of snaps |
| OL-4 | window = last game only | caught | |
| STAT-1 | doubtful and out swapped in `status()` | **survived** | real gap: fixture has a Doubtful (DEN wk 2) and an Out (KC wk 4) back; assert both strings |
| STAT-2 | injury reader keeps postseason rows | survived | equivalent in practice: postseason weeks are 19-22 (checked 2023-25) |
| YDS-1 | official yards = yards_gained | **survived** | real gap: no lateral in the fixture (rushing_yards != yards_gained on 1-3 plays a season) |
| YDS-2 | exposure ignores the dropback share | **survived** | fixture artifact (one QB 3/3); real gap: a two-QB game |
| SIT-1 | short yardage = 3 or fewer to go | **survived** | real gap: pin `rushing.short_yardage_conv` |
| STAT-3 | (not a bug) the M1 fix: no report -> unknown | green | the fix does not break the suite |
| CTRL | comment-only change | green | |

## 5. What I could not check, and why

- **The rule text at the first run.** The 262da6dc file is not on disk (searched the scratchpad and the filesystem by
  hash). Only its hash and its outputs exist.
- **The handback numbers.** The builder's handback message was not given to me; §2 covers the report.
- **Team-context and line reliabilities.** Not independently derived: `tm_*`, `ol_*`, `def_rb_*`, `def_mtkl_*`, and the
  remaining QB splits. They are confirmed only by re-running the builder's code, which reproduced every json byte for
  byte. I independently derived the 5 required metrics plus `def_box_ypc_dev` and `rb_catch`.
- **The 4 game-day `date_modified` rows (2023).** Whether they were set before kickoff: the file stamps dates only.
  2024-26 files have no `date_modified`, so pre-kickoff status there rests on the Questionable / Out play rates.
- **Kalshi settlement.** Inferred from ladder brackets only, as the report says. `expiration_value` is empty, so there is
  no settlement record.
- **The real §8.1 job on the server.** Not run on a 1 GB disk; M2 is a stubbed simulation of the diff as written.
- **The aux build's 46 MB peak.** Not re-measured (my store-plus-aux rebuild of 2025 took 7.8-9.1 s).


---

## RE-CHECK (2026-10-24)

The fixes were F1-F12 and F14, with F13 declined. They are listed in
`research/reviews/nfl_rush_data_orchestrator.md`, which also carries the
orchestrator's integration fix for my M2 (its O1).

I hashed and froze the files under review at the start of the re-check, and nothing changed while I worked:
- `nfl_rush_data.py` 4122f839
- `nfl_rush_profiles.py` 6da5fa7d
- `research/nfl_rush_dataset.py` 01180bfe
- `tests/guards_nfl_rush_data.py` f7ed12b5
- `research/data/nfl_rush/*`, as in the report's §10 (`meta.json` carries run times)

The integration files did change while I worked, so §4 reviews two versions:
- **v1**, as handed over: `matchups.py` 349ec71a, `tests/guards_matchups.py` 790981f4;
- **v2**, edited about 00:50-00:53 UTC: `matchups.py` 9416b2ef, `tests/guards_matchups.py` 34cd7608.

`app.py` also changed at 00:50, for work outside this review.

The agreed foundation is byte-identical: 85113469 / d2007503 / 766a60ed.

Working files are under `W/recheck/`: a copy of the aux-SCHEMA-2 deep store, my scripts and logs, and the mutation tree. I made
no git writes and no repo edits other than this section. The only 2025 and 2026 outcome columns I read were the reconciliation
counts I had already run (the `act_played`, `act_any_snap` and Sleeper `gp` tallies for m2). The poison test compares rebuilt
rows against each other, not against outcomes.

### Verdict: AGREE AFTER FIXES

Every original finding is fixed: B1, M1, M3, M4, m1-m6 and N1-N8. I checked each by re-deriving it, not by reading the
report.

**M5 (guard gaps): all 15 of my real-gap mutants are now caught.**

**M2 (the integration):** the backfill is now keyed on the aux, and a killed run's files are swept on the next run. One case
is still not retried: a failed DOWNLOAD (R3 below).

**Required before ship:**

1. **R3 (MAJOR).** A source whose download fails during a backfill must leave its season due. It must not leave an aux that
   reads as matching its store and is missing the block forever.
2. **R4 (MINOR).** Isolate each backfill season's agreed `D.refresh`, so a build that keeps failing cannot block the daily
   refresh. v2 already does this for rebuilds that only the rushing layer asked for (rc6).
3. **R5 (MINOR).** Make the five "rushing aux (O1)" checks run every month, not only October to December.
4. **R1 (MINOR).** State that team-scoped usage windows now count games a back played only on special teams. Correct §6,
   which says the phase-1 `p_games_cur` definition did not change.
5. **R6 (NIT, two guards).** Pin `K_FRAMES` for 2023-2025, and pin the QB-rate k inside the hit + sack OE.

R2 and the nits are recommended, not required.

### 1. The original findings, re-derived

**B1: RB units. FIXED.**

`reliability.json` now states that its RB units are crosswalk RB/FB only, and `T.rb` holds RB/FB runs only
(`elif rpos == "RB"`). Both filters are mutation-tested (FIX-B1a and FIX-B1b below are caught). From raw, with my own code:

| metric | shipped (r / units / k) | my raw code | note |
|---|---|---|---|
| `rb_catch` | 0.032 / 284 / 877 | 0.032 / 284 / 877.0 | exact |
| `rb_carry_share` | 0.911 / 528 / 16.95 | 0.912 / 529 / 16.78 counting special-teams-only games; 0.906 / 529 / 17.76 offense-only | see R1 |
| `rb_snap_share` (after the B2 snap fix) | 0.905 / 522 / 48.81 | 0.905 / 523 / 48.8, team total = the most offensive snaps | `W/recheck/work/rel_raw2.py` |

Two more numbers in the re-measure check out:
- **The other RB metrics.** `rb_adot`, `rb_ypt`, `rb_yac_rec`, `rb_chk_share`, `rb_screen_share`, `rb_line_yds` (499 units,
  no longer double counted), `rb_fd`, `rb_td`, `rb_ypc` and `rb_tgt_per_db` equal my phase-1 RB-only figures to the decimal.
- **Reproduction.** A re-run of `measure` on the final code into my own directory reproduces `reliability.json`,
  `persistence.json` and `sneak_rule.json` byte for byte.

On process:
- The pre-run hashes 112d5e6c (F1) and 01180bfe (F14) match the sha that each `measure` log records at its start.
- DECAY is 0.602, giving 0.75, unchanged. My phase-1 raw value was 0.6025.
- The only change to the measured definition is that share bases are clamped to [0, 1]. It moves five defense departures,
  and the report discloses it.

**B2 (Sonnet's snap fix), re-derived.**
- 13 of 4,440 team-games from 2018 to 2026 have nobody at 99.5% of snaps or more, the same distribution as the report.
- The old snaps / pct estimate read +7.21 snaps a game high on average (max +34), across all seasons.

**M1: absent-week report. FIXED.** `W/recheck/work/status_aux.py`:

```
week 7: CHI 00-0040236 out  'nflverse injuries 2026 week 7 (final report, pre-kickoff)' | rb_room_out 0.3008 | out_report 0 | ol_listed_out 0
week 8: CHI 00-0040236 unknown 'no injury report for 2026 week 8 in the aux store' | rb_room_out None | out_report None | ol_listed_out None | ledger RUSH-noreport
```

**M3: aux stamps. FIXED.** Every profile now carries `aux` per season, and a snapshot whose aux is not aligned writes
`RUSH-aux` to the ledger:
- present: `{2024: aligned, 2025: aligned, 2026: aligned}`;
- missing: every season `missing`, ledgered;
- stale: every season `stale`, ledgered;
- 2025 missing only: `2025: missing`, ledgered.

Truncated or late blocks now appear in `coverage` and `missing`; FIX-M3c is caught. Every dataset row's `aux_status` reads
aligned.

**M4: `schema.json`. FIXED.**
- Kinds are read across all rows. The nine scramble-split and `dq_pressure` columns read "modelled", the containment leaves
  and the hit + sack fields "proxy", and `q_designed_read_option` "not_available".
- The three `ol_starters_*` meanings are correct.
- Sources and `source_tag` are right for snap share, box, PFR pressure and NGS.
- A `license` block is present.
- Small leftovers, which are nits: the human `source` TEXT of `dq_containment_proxy_*`, `in_sleeper` and `act_played` still
  reads "nflverse pbp", although their `source_tag` is right. And "every column has a unit" leaves out the ten key columns.

**m1: share clamps. FIXED.** No share value or raw is outside [0, 1] in either file. 349 RB rows now read exactly 0.0 for the
10-personnel heavy share; the minimum was -0.0481.

**m2 (`act_any_snap`). FIXED.** 523 RB rows read `act_played` 0; 453 of them have a Sleeper `gp` of 1, and 450 of those read
`act_any_snap` 1. These equal the report.

**m3: k frames. FIXED.** I backed the league prior out of `value`, `_raw` and `_n` with each candidate k. The true prior is
the same for every row of a week, so the right k gives a constant:

```
p_receiving_catch  2023-25: within-week SD of the implied prior  k_fit 3e-05   k_full 0.00076-0.00087   2026: k_fit 0.00074  k_full 3e-05
p_rushing_line_yds 2023-25:                                       k_fit 0.0005  k_full 0.006            2026: k_fit 0.0062   k_full 0.0005
q_hit_sack_rate    2023-25:                                       k_fit 0.0002  k_full 0.0007           2026: k_fit 0.0007   k_full 0.0002
```

So the 2023-2025 rows read `k_fit_only` and the 2026 rows the full table, as `meta.json` `k_frames` says.

**m4, m5. FIXED.**
- §6 discloses the post-week universe.
- §9.8 now says the agreed `nfl_matchup.py` reads the scramble-excluded QB splits at weight 0.

**m6. FIXED in the integration:** one season's files at a time. But §8.1 and §9.12 of the report still say "~154 MB at peak".
That is stale (nit).

**N1-N8. FIXED.**
- N1: 4.35 -> 4.34.
- N2: the docstring reads 16,988 / 8.
- N3: 0.01-0.05%.
- N4: the duplicate line-yards path is gone.
- N5: the schema has a license block.
- N6: `FileNotFoundError` is passed; any other `OSError` is ledgered `RUSH-rm`.
- N7: the dead counters are removed.
- N8: "pace" is labelled; edge setting is mapped in §3.4 to `vs_rb.by_side.edge`; `scramble_ypa_allowed` is added;
  `read_option` reads `not_available`; the `totals.yds_pg` basis is corrected.

**F13 (declined): AGREE.** The nominal front exists only in the 2025-26 depth-chart layout. With 2023-24 uncovered, the fit
seasons cannot use it.

### 2. My mutants against the fixed tree

The harness is `W/recheck/work/mutate_opus2.py`: my phase-1 list plus 15 mutants aimed at the fixes. It runs on a copy of
the tree with `python3 -B` and `__pycache__` cleared, and its results are in `W/recheck/mut/mutation_results.json`.

**The 15 phase-1 real gaps are all caught:** SCR-1, TEAM-1, TEAM-2, TEAM-4, LOO-2, LOO-3, K-1, K-2, K-3, OL-2, OL-3, STAT-1,
YDS-1, YDS-2, SIT-1. All 16 phase-1 catches still catch.

Unchanged or retired:
- RPO-2 and STAT-2 still survive. Both are equivalent, as before.
- CUT-2 was re-pointed at the new `_report` and is caught.
- SACK-3 now crashes, which counts as caught but is really N/A: its counter was removed.
- SCR-3 and STAT-3 were retired, because the code they mutated is gone.

**The 15 fix mutants: 12 caught, 3 survive.**

| id | mutant | result |
|---|---|---|
| FIX-B1a | the back tally takes every non-QB carrier | caught |
| FIX-B1b | RB units unfiltered by position | caught |
| FIX-M1 | an absent week reads {} (active) | caught |
| FIX-M3a | no aux stamp | caught |
| FIX-M3b | a stale aux not ledgered | caught |
| FIX-M3c | a truncated block not flagged | caught |
| FIX-B2 | team snaps = largest snaps / pct | caught |
| FIX-B2b | a special-teams-only back gets offensive snap-share denominators | caught |
| FIX-F14a | a sack without a hit drops out of hit + sack | caught |
| FIX-F14c | `ol_listed_out` counts Questionable | caught |
| FIX-m2 | `act_any_snap` ignores special teams | caught |
| FIX-DECAY | 0.6 moves to the lower band | caught |
| **FIX-F7** | `K_FRAMES[2025] = "full"` | **survived**: a real gap. The builder's checks run on fixture season 2030, which `K_FRAMES` does not hold. Pin the dict (R6). |
| **FIX-F14b** | the hit + sack OE uses unshrunk QB rates (`qmetric` None) | **survived**: a real gap, the LOO-3 analogue for the new metric (R6). |
| FIX-m1 | share VALUES not clamped (priors and bases still are) | survived: near-equivalent. A convex mix of a raw and a prior in [0, 1] stays in [0, 1]; it bites only when a raw is above 1 (`ryoe_carry_coverage`, 40 rows). |

### 3. Nothing new is broken

**Leakage, poisoned again** (`W/recheck/work/poison2.py`, which now also poisons pbp `qb_hit` at week >= W and flips `sack`
at week > W).

| week poisoned | RB feature cells compared | QB feature cells compared | cells that moved |
|---|---|---|---|
| 2025 week 10 | 83,905 | 11,798 | 661 + 140, all `sl_proj_*` (the Sleeper inputs I poisoned myself) |
| 2026 week 6 | 79,580 | 10,063 | 0 |

That is 185,346 feature cells in all, and no profile-derived cell moved. `q_hit_sack_rate` did not move on week 10 although
that week's `qb_hit` was poisoned. Both clean rebuilds from raw equal the committed files cell for cell (0 differing).

**The new `hit_sack_rate` fields**, from raw with my own code: I parsed `qb_hit` myself; a dropback counts when it was a sack
or a hit (`W/recheck/work/hs_raw.py`).
- `qb_hit_sack_rate`: r 0.609, 282 units, k 158.78. Shipped: 0.609 / 282 / 158.7.
- `def_hit_sack_rate_oe`, using leave-one-out QB rates shrunk by k 158.7: r 0.404, 256 units, k 66.61. Shipped:
  0.403 / 66.97.
- Six hand cells all match, for `q_hit_sack_rate_raw` and `dq_hit_sack_rate_oe_ratio_raw`. They are as-of, with the
  leave-one-out at the game's season weight and the QB-rate k from each row's frame: 163.16 for 2023-25, 158.7 for 2026.
  - Rodgers 2024 wk 11: 0.143 / 0.969
  - Rodgers 2025 wk 4: 0.1515 / 1.037
  - Huntley 2023 wk 4: 0.1114 / 1.021
  - Daniels 2026 wk 5: 0.175 / 1.079
  - Stafford 2026 wk 6: 0.1412 / 0.8171
  - Brissett 2026 wk 6: 0.1907 / 1.036

**Hand cells against the rebuilt dataset.** 65 of 65 agree once my own appearance rule takes the B2 snap total and counts
special-teams-only games. The cells include:
- the traded back, Cam Akers;
- a 2026 participation share, Breece Hall 0.6759;
- `d_`, `q_`, `ol_` and status cells.

**Kalshi sacks (new §9.6 claim): AGREE.** Including sack yards in QB rushing yards is wrong in all 117 discriminating settled
2025 ladders (theirs: 111 of 111).

**Guards and suite.**
- `guards_nfl_rush_data`: 181 / 181 in 1.0 s.
- `guards_nfl_matchup_data`: 149 / 149.
- `guards_matchups`: 38 / 38.
- pyflakes is clean on all six files.
- Full suite (`tests/combo_audit_guards.py`): **3,098 passed, 2 failed, exit 1**. The run started about 00:30 UTC.
  `app.py`, `matchups.py` and `tests/guards_matchups.py` were edited mid-run (00:50-00:53). Both failures sit in that work,
  outside this layer:
  - "the routes: /api/nfl/deep/status ...";
  - "matchups live view: /api/matchups with no date ...".

  The standalone `guards_matchups` reads 45 / 45 on the 00:53 tree. **The full suite must be re-run, with its exit code, on
  the final tree before any commit.** The builder's own run at 00:28 had 1 failure, a docstring naming
  `nfl_rush.qb_profile` (the model's module), which did not recur in mine.
- §8.3 (`guards_nfl_rush_data` in combo_audit_guards' MX list) is not yet applied: `tests/combo_audit_guards.py` is
  unmodified. That is a pre-commit item.

### 4. The integration (`matchups.nfl_data_job`, `_drop_raw`, the five O1 checks)

**Is the backfill keyed correctly? Yes.** A prior season is due when its matchup store is missing, or when
`R.aux_matches(R.load_aux(s), D.load_season(s))` fails. The order for each due season:
1. its raw files are dropped first;
2. the agreed store is rebuilt from the fresh download (`keep_raw=True`);
3. the aux is built from those same files;
4. the files are dropped, one season at a time.

**Can a failure strand raw files? Only until the next run.**
- The `finally` drops every prior season's files for both layers, and their `.meta.json`.
- A SIGKILL bypasses the `finally`, but the next run's `finally` sweeps every prior season whatever is due.
- The guard asserts the cleanup on a daily refresh that raises and on a build that fails after its download (rc3, rc5).
- Nit: the upfront drop covers `back`, not `prior`. A leftover from a killed run for a season that is no longer due can sit
  beside the season downloading until the `finally` removes it.

**Is a failed season retried? Only if the failure raises.**

**R3 (MAJOR, open in v1 and v2).** A source that fails to DOWNLOAD yields an aux missing that block that still matches its
store. The season is
then never due again: a prior season's sources never change, so `changed` stays False.

Demonstration (`W/recheck/work/fetchfail.py`): the real `R.refresh` on 2025, with `D.fetch` failing for the one missing PFR
rush file.

```
refresh summary: {'built': True, 'missing': ['pfr_rush (no file for 2025)'], 'seconds': 2.79, 'extras': 33325}
ledger: ['RUSH-fetch']
aux_matches(aux, store) = True -> matchups.rush_due would read False
```

The same happens on the integration's own path. Take a stale-aux season whose files the upfront drop has removed. If the pbp
re-download fails:
- `D.refresh` keeps the old store;
- `R.refresh` builds an aux with no extras and no situational counts;
- that aux matches the old store, and the profiles stamp it **aligned**.

2026's on-field shares and extras read only those prior seasons, so the loss is silent and permanent.

Fix, either of:
- `_refresh_season` refuses to save, and returns an error that leaves the season due, when a source the season should have
  (pbp, participation where it exists, PFR, injuries, snaps) has no file;
- or the aux records `incomplete`, `rush_due` treats it as due, and `aux_status` stamps it.

Guard: a backfill whose PFR download fails is due on the next run. Under v2's weekly marker, the retry would then cost at
most one download a week.

**R4 (MINOR).** A backfill season whose agreed `D.refresh` raises (a build failure after its download) aborts the job before
the daily refresh.

| version | what happens |
|---|---|
| v1 | My probe, with the guard's own fakes and `build_raises={2025}`, shows the calls `D[2024]`, `R[2024]`, `D[2025]`, then the raise. There is no `D[2026]`, so the current season stops refreshing for as long as the failure persists. |
| v2 | A rebuild of a healthy store that only the rushing layer asked for is caught, ledgered `NFLM-backfill`, and no longer costs the daily refresh (rc6). It is retried at most weekly (the `nfl_rush_backfill_try_<s>` marker, rc7). |

v2 fixes the part this integration introduced. The agreed layer's own no-store backfill still fails the job "as it always
did", which predates this work and is acceptable.

**R5 (MINOR, guards).** The "rushing aux" checks sit under `if daily == [2026]:` and silently do not run from January to
September. With `clock.today_et` patched (`W/recheck/work/month_probe.py`), `guards_matchups` runs:

| tree | October dates | 2027-01-15 | what goes unrun Jan-Sep |
|---|---|---|---|
| v1 | 38 (2026-10-24 and 2027-10-15) | 33, no failure reported | the five O1 checks |
| v2 | 45 (2027-10-15) | 38 | seven checks |

The probe also showed three unrelated date-dependent failures on 2027-09-15, an artifact of my patched clock that I did not
pursue. Fix: patch the month inside those checks so they always run, or fail when they would be skipped.

### 5. New, smaller findings

**R1 (MINOR).** Team-scoped usage now counts special-teams-only games.
- The B2 / m2 snap change creates a player line for every RB or QB with special-teams snaps only (`_close_game`). He
  therefore "appears" in that game:
  - `p_games_cur` and `p_games_w`;
  - the `carries_pg` denominator;
  - the team designed runs in `carry_share`;
  - the reliability units.
- On the committed RB file, `p_games_cur` matches offense-only appearances on 4,908 of 6,041 rows, and offense +
  special-teams-only on 6,034. The other 7 are Ben VanSumeren, whose PFR position is not a skill code.
- The usage k move with it: carry share 0.906 / k 17.8 offense-only against 0.912 / k 16.8.
- Counting those games is defensible: Kalshi settles on one snap. But it is undisclosed. §6 still says
  "`p_games_cur` equals the player's earlier played weeks ... (the phase-1 check; ... did not change)".
- Fix: state it in §0, §3.1 and §6, or exclude those games from the usage windows.

**R2 (MINOR, recommended).** A team with no rows in a week that HAS a report still reads every player "active". Across
2023-26 only 2 of 1,818 playing team-games had no injury rows at all. So a playing team with no rows is almost always a data
gap, and "unknown" would be the safer reading.

**R6 (NIT).** Add the two guards that would catch FIX-F7 and FIX-F14b.

**Nits.**
- Report §8.1 and §9.12 still give the 154 MB backfill peak; the integration holds about 77 MB.
- The `artifacts.py` SCHEMA 7 comment says `nfl_rush_data` SCHEMA 1; it is 2.
- `SOURCE_OF` tags `designed.*` as pbp, although the sneak exclusion reads FTN.
- The schema `source` texts in §1 above.


---

## CONFIRMATION (2026-10-24)

This confirmation covers round 3 (G1-G5) and the job's use of `R.aux_complete`. I froze the files at about 01:16 UTC:
- `nfl_rush_data.py` 247cc625
- `nfl_rush_profiles.py` 1bddcab7
- `research/nfl_rush_dataset.py` 28bdea90
- `tests/guards_nfl_rush_data.py` f9e5168d
- `matchups.py` 85bcde85
- `research/data/nfl_rush/*`, equal to the report's new §10 table, every row checked

`tests/guards_matchups.py` changed three times while I worked:
- 2efafeb3, as frozen;
- e993580e at 01:17, a live-view JS change only;
- 07f2ba80 at 01:28. This version makes the fake `aux_complete` as strict as the real one and adds a check that a failed
  rushing-only backfill writes the weekly marker.

I re-ran the guard on each version.

The agreed foundation is byte-identical: 85113469 / d2007503 / 766a60ed. My working files are under `W/confirm/`. I made no
git writes and no other repo edits, and I read no outcome column.

### Verdict: AGREE TO SHIP

There is one condition, which is not a code fix: the full suite must pass with its exit code checked on the tree that is
committed (see the end of this section).

### G1 (my R3), re-derived with the real code
`W/confirm/work/ff.py`: the real `R.refresh` on 2025 with the network off for one file.

```
PFR rush download fails: summary {'built': True, 'complete': False, 'absent': ['pfr_rush'], ...}; ledger RUSH-fetch, RUSH-incomplete
  aux_matches True, aux_complete False -> the job's rush_due: True
  profile stamp {2025: 'incomplete'}, ledger RUSH-aux
  second run: rebuilt (not skipped as unchanged), still complete False while the file is missing
pbp download fails: {'error': 'no play-by-play file for 2025 (download failed?): nothing saved, the season stays due'}; no aux saved
```

- Every shipped aux, 2018-2026, reads `complete True`, `absent []`.
- No dataset row's `aux_status` reads incomplete, stale, missing or mixed.
- In the job, `rush_due = not aux_matches or not aux_complete`, and the weekly retry marker bounds the cost.

### G2 (my R1)
§0.3 and §6 now state that a back's games include special-teams-only games. They give the k under both definitions:
carry share k 16.95 as shipped, 17.95 offense-only. My phase-1 run of the builder's pairs with RB units only gave 0.905 /
17.95, and my raw code 0.906 / 17.76. The report says the first version's "unchanged" claim was wrong.

### G3 (my R6)
I re-ran my full harness (`W/recheck/work/mutate_opus2.py`) on the round-3 tree. **47 of 49 mutants are caught.**
- FIX-F7 (2025 rows on the full table) is now caught, by "k frames (G3)".
- FIX-F14b (the hit + sack OE with unshrunk QB rates) is now caught, by "hit + sack allowed (G3)".
- FIX-m1 (share values not clamped) is now caught, with a raw above 1 in the fixture.
- RPO-2 and STAT-2 still survive. Both are equivalent, as before.

### G4 (my recommendation)
I removed ARI's rows from the real 2026 week-7 report. ARI then reads `unknown`, with the source "2026 week 7's report lists
nobody from ARI (read as a gap)". `out_report`, `ol_listed_out` and `rb_room_out` read None, and `RUSH-noreport` is ledgered
once. Teams still on the report are unchanged.

In the dataset this turns 8 RB rows and 2 QB rows "unknown": the two playing team-games with no report rows, 2023 week 5
ATL and 2024 week 1 DEN. That matches my count.

### G5 (nits)
All fixed:
- §8.1 and §9.12 now give about 77 MB;
- the `artifacts.py` comment says aux SCHEMA 2;
- `schema.json` `source` text is now written from `source_tag`, so the containment proxy, `in_sleeper` and `act_played`
  texts agree with their tags;
- `designed.*` is tagged ftn+pbp.

### New mutants aimed at round 3
`W/confirm/work/mutate_g.py`. I ran each against its own guard file. For the job mutant I used a tree copy that includes
`research/data`; the control run passes 44 / 44 there.

| id | mutant | result |
|---|---|---|
| G1a | an aux missing a source still reads complete | caught (4) |
| G1b | `aux_complete` lenient when the `complete` flag is absent | **survived** |
| G1c | an incomplete aux skipped as unchanged | caught |
| G1d | a season with no pbp file still saves | caught |
| G1e | profiles stamp an incomplete aux "aligned" | caught |
| G1f | `pfr_pass` expected for 2018-2020 | caught |
| G4a | a reported week's absent team reads active again | caught |
| JOB-a | the job ignores completeness | caught (rc8) |

G1b is a nit. It matters only for an aux built before round 3, and no such aux was ever deployed: the scratch store was
rebuilt with the flag. A one-line guard, `not R.aux_complete({"schema": 2})`, would pin the docstring's claim.

### Nothing new is broken
- **Re-measure.** A re-run of `measure` on the final code reproduces `reliability.json`, `persistence.json` and
  `sneak_rule.json` byte for byte. Against round 2, the only change in those files is the credit text.
- **Leakage, poisoned again** (2025 week 10 and 2026 week 6, with `qb_hit` and `sack` poisoned too). Of 185,346 feature
  cells, none derived from a profile moved; the only 801 that moved are the Sleeper projections I poisoned myself. The clean
  rebuilds from raw equal the committed round-3 files, with 0 differing cells.
- **Guards.**
  - `guards_nfl_rush_data`: 192 / 192.
  - `guards_matchups`: 44 / 44 on 2efafeb3 and on e993580e; 45 / 45 on the latest version, 07f2ba80.
  - `guards_nfl_matchup_data`: 149 / 149.
  - pyflakes: clean.
- **R5 holds.** With `clock.today_et` patched to 2027-01-15, 2027-06-15 and 2027-10-15, `guards_matchups` runs 44 checks
  every time.

### One observation (not required)
In the days before a new season's first PFR or injury file is published, the current season's aux reads incomplete, so each
snapshot ledgers `RUSH-aux` until the file appears. It is transient and the ledger says why.

### The full suite
I ran it only after the other `combo_audit_guards` run (PID 22753, 01:17-01:24 UTC) had finished. My run went from 01:24:23
to 01:30:26 UTC:

```
VIGIL_NO_BG=1 python3 -B tests/combo_audit_guards.py ; SUITE=$?   ->   RESULT: 3102 passed, 0 failed   SUITE exit 0
```

One file changed during the run, `tests/guards_matchups.py` at 01:28:48 (the 07f2ba80 edit above). Every other file was
hashed identical before and after: `app.py`, `matchups.py`, `static/matchups.js`, `artifacts.py`, the four rush files and
`combo_audit_guards.py`. That last edit passes standalone, 45 / 45.

**Condition for commit:** run the suite once more, exit code checked, on the exact tree that is committed. Until §8.3 is
applied, `guards_nfl_rush_data` is not part of the suite, so run it beside the suite as well (192 / 192 here).

## CONFIRMATION 2 (round 4)

2026-10-24, about 01:40-05:30 UTC. This round answers the Opus rule review's M8
(`research/reviews/nfl_rush_rule_opus.md`): the model's `dec_trend` read usage windows built from every game in
`T.player_games`, and that set included games that only PFR's snap counts list (special teams only, or offence with no play
or participation line). The result was PFR content inside fields tagged `pbp`.

Round 4 adds a PL `app` flag in `_close_game`: 1 when pbp shows a rush of any class, a target, a scramble, an aborted snap or a
kneel, or participation shows an on-field snap, or he is in the game's `qbs`. PFR snaps are never part of it. `rb()` now
builds `games` from `app` games only. The PFR-inclusive set (`games_any`, `line_any`, `rec_any`) feeds only the two PFR snap
shares and a new `any_snap` display block tagged `derived:pbp+pfr_snaps`.

I froze the files at about 01:40 UTC and re-hashed them at the end, unchanged:
- `nfl_rush_profiles.py` cf230d8c
- `research/nfl_rush_dataset.py` 82e4aa12
- `nfl_rush_data.py` 247cc625 (as in round 3)
- `tests/guards_nfl_rush_data.py` d2ee444c
- `matchups.py` 85bcde85
- `research/data/nfl_rush/`:
  - rb_games f39a12ed
  - qb_games d7a42025 (byte-identical to round 3)
  - reliability 25e9bc1d
  - persistence 1234070e
  - schema ec18b369
  - meta ced28e4c
  - checks 3bca9800
  - sneak_rule 01da9a7c

The agreed foundation is byte-identical: `nfl_matchup_data.py` 85113469, `nfl_matchup_profiles.py` d2007503,
`dataset_2023_2026.csv.gz` 766a60ed. My working files are under `W/confirm2/`. I made no git writes and no other repo edits.

Outcome columns I read, as disclosure:
- the probe read 2025 `act_played` to choose backs and QBs who did NOT play a game, as planting targets;
- the 2026 row-universe count (N2 below) read `act_played`, the touch counts and `in_sleeper`;
- every other comparison counts differing cells and excludes `act_*` values from any figure.

### Verdict: AGREE TO SHIP

There are two nits for whoever builds the model frame (N1, N2). Neither is a data-layer fix. The standing condition is
unchanged: run the suite with its exit code checked on the committed tree, and run `guards_nfl_rush_data` beside it. That
guard is still not wired into `combo_audit_guards.py`; it has no `nfl_rush` reference.

### (1) A PFR snap row moves no field a fitted feature can read: real data, planted lines

`W/confirm2/work/plant.py`:
- **Planting.** I took the real 2025 aux and planted 420 PFR snap-count lines in weeks 3-8, each for a player rostered on
  that team whom neither pbp nor participation shows in that game:
  - 161 RB special-teams-only lines (0 offensive snaps, 12 special-teams snaps);
  - 102 RB offence-only lines (14 offensive snaps, share 0.2, no play and no participation line);
  - 157 QB special-teams-only lines.
- **Rebuild.** I saved the aux to a deep-store copy and rebuilt 2025 weeks 1-12 with the shipped code.
- **Comparison.** I compared each column against the committed file, classing it by its `schema.json` `source_tag`.

| file | unrestricted feature cells compared | moved | restricted cells moved | outcome cells moved |
|---|---|---|---|---|
| RB (1,174 common rows) | 940,374 | **0** | 732, in `p_any_snap_games`, `p_usage_snap_share` (+`_raw`, `_n`), `p_recent_snap_share` (+`_n`) | `act_any_snap` 6, `act_played` 1, `act_pfr_snaps` 1, `act_pfr_team_snaps` 1 |
| QB (430 rows) | 132,870 | **0** | 0 | 0 |

Every restricted column that moved is tagged `pfr_snaps` or `derived:pbp+pfr_snaps`, and `RP.restricted` is True on each.
The planted offence-only lines also put 101 new RB rows into the universe, each with `act_played` 1; see N2.

**The probe can see a leak.** I applied O4f below to a tree copy: this season's charted on-field plays summed over
`games_any`. The same probe then moved 333 cells of `p_usage_onfield_plays_current` (tag `participation`) and nothing else
unrestricted. The fixture guard misses that mutant (N1); the real-data probe does not. The shipped code moves 0.

Code reading agrees with the probe:
- **`line_all`** still spans `games_all`. It feeds only player-scoped efficiency (receiving, PFR, NGS), where a PFR-only
  game adds zeros to every pbp count.
- **`T.player_games`** is otherwise read only by the dataset's own unit gate. There the `tgt` sum is unchanged by a game
  with no target. It is also read by the reconciliation checks.

### (2) The moved k's reproduce from raw

**From raw.** `W/confirm2/work/rel_raw4.py` is my own parser over `W/work/raw/plays_<s>.pkl` and the nflverse snap-count
CSVs, with no repo import. It computes the RB/FB-only odd-vs-even split-half for 2018-2025 under three appearance sets:
- `app` = pbp touches + participation on-field (round 4);
- `pfroff` = `app` + PFR offensive snaps;
- `pfrall` = `pfroff` + PFR special-teams-only lines (round 3).

The gates are the builder's: 25+ rushes or 15+ targets in both halves, share n >= 8, per-game n >= 4.

| metric | shipped round 4: r / k (units) | raw `app`: r / k (units) | shipped fit-only k (2018-24) | raw fit-only k | raw `pfrall` k (round 3's shipped) |
|---|---|---|---|---|---|
| carry share | 0.906 / 17.80 (528) | 0.907 / 17.61 (529) | 17.70 | 17.50 | 16.78 (16.95) |
| target share | 0.792 / 59.93 (528) | 0.792 / 59.93 (529) | 59.75 | 59.74 | 57.88 (57.89) |
| on-field, all plays (`sit_all`) | 0.905 / 45.79 (528) | 0.906 / 45.78 (529) | 46.15 | 46.14 | 42.93 (42.94) |
| carries per game | 0.866 / 1.12 (508) | 0.867 / 1.11 (509) | 1.06 | 1.05 | 1.08 (1.10) |
| targets per game | 0.795 / 1.86 (508) | 0.795 / 1.86 (509) | 1.76 | 1.76 | 1.78 (1.78) |

- **Agreement.** Target share and targets per game agree exactly, and `sit_all` and carries per game to the second
  decimal.
- **Carry share** sits about 1% low in my code: 17.61 against 17.80. The offset is the same one round 3 showed (16.78
  against 16.95), so it is my designed-run definition, not the appearance change.
- **The shift reproduces.** Shipped round 3 to round 4 is +0.85 on carry share; raw `pfrall` to `app` is +0.83.
- **Where the move comes from.** Nearly all of it is the special-teams-only lines (`pfrall` to `pfroff`). The offence-only
  PFR lines move the k's by under 1% (`pfroff` to `app`).

**Re-measure on the final code.** I re-ran `measure` into `W/confirm2/remeasure/` on a fresh deep-store copy
(`W/recheck/work/remeasure.py`). `reliability.json`, `persistence.json` and `sneak_rule.json` reproduce byte for byte
(25e9bc1d, 1234070e, 01da9a7c). The persistence tendency is still 0.602, so `DECAY` stays 0.75.

**The rows that moved.** I compared feature columns only (no `act_*`) against the round-3 file (43fa3ce7, the builder's
round-3 mutant-tree copy):
- 1,974 of 6,041 RB rows moved: 2023 556, 2024 651, 2025 556, 2026 211;
- they moved in 65 columns: 55 `p_usage_*`, 8 `p_recent_*`, plus `p_games_cur` (1,286 rows) and `p_games_w` (1,974);
- one new column, `p_any_snap_games`;
- the keys are identical.

This is exactly the report's claim. The QB file is byte-identical.

### (3) Leakage is still clean

I used `W/recheck/work/poison2.py`'s files, which poison every source at and after the week, including pbp `qb_hit` and
`sack`. I rebuilt them with the round-4 code (`build_leak.py`, `cmp_leak.py`) on fresh deep-store copies:

| week poisoned | RB feature cells compared | QB feature cells compared | feature cells that moved | earlier weeks |
|---|---|---|---|---|
| 2026 week 6 | 79,672 | 10,063 | **0** | 0 differing |
| 2025 week 10 | 84,002 | 11,798 | 661 + 140, all `sl_proj_*` (the Sleeper inputs I poisoned myself, passed straight through) | 0 differing |

No profile-derived cell moved. Both clean rebuilds from raw equal the committed round-4 files cell for cell, with 0
differing cells, the same keys, and 618/211 and 980/361 rows.

### (4) The foundation

85113469 / d2007503 / 766a60ed, at the start and at the end.

### Mutants

The builder's `mut_r4.py` covers M8a-e: the usage set is every PFR game; the appearance counts PFR snaps; `any_snap` is
tagged pbp; the recent window is over `games_any`; the games count is tagged pbp. I wrote seven of my own, each a different
road from a PFR-only game into an unrestricted field (`W/confirm2/work/mutate_r4.py`, `mutate_r4b.py`). I ran each against
`guards_nfl_rush_data` on a tree copy.

| id | mutant | result |
|---|---|---|
| O4a | usage team denominators (`tml`) over `games_any` | caught (M8 invariance) |
| O4b | per-game denominator and weighted games (`wsum`) over `games_any` | caught |
| O4c | recent team denominators over the PFR-inclusive last-N | caught |
| O4d | `games.current` counted over `games_any` | caught (2) |
| O4e | appearance counts a PFR offensive-snap line (offence only) | caught (the appearance check) |
| O4f | `onfield_plays_current` summed over `games_any` | **survived** the guards; caught by the real-data probe (333 cells) |
| O4g | `usage.snap_share` retagged pbp | caught (3, the source-tag checks) |
| control | comment only | passes |

The tally is 6 of 7 caught by the guards, and 7 of 7 by the guards plus the probe.

### Guards and the suite

- `guards_nfl_rush_data`: **196 / 196**, rc 0.
- `guards_matchups`: **47 / 47**, rc 0 (4e189573).
- **Full suite.** No other `combo_audit_guards` run was live, and no repo file had changed in the 30 minutes before. My run
  went from 05:14:02 to 05:22:40 UTC:

```
VIGIL_NO_BG=1 python3 -B tests/combo_audit_guards.py ; SUITE=$?   ->   RESULT: 3091 passed, 0 failed   SUITE exit 0
```

  During the run the model-layer files changed: `nfl_rush.py`, `research/nfl_rush_model_fit.py`, `_frame.py`, `_rule.py`
  and `tests/guards_nfl_rush_model.py`. Another agent is still working on them. Every data-layer file hashed identical
  before and after: the four rush files, the guard, `matchups.py` and the foundation. The total is 3091 here against 3102
  in round 3. The difference is in suite sections outside this layer, because `guards_nfl_rush_data` is not part of the
  suite.

### Nits (for the model frame; not data-layer fixes)

- **N1. The fixture guard cannot see `onfield_plays_current`.** The M8 invariance check plants its special-teams-only line
  in a fixture game (G6) that has no team participation line for KC. So a participation-only count summed over the
  PFR-inclusive set cannot move there (O4f survives). The shipped code is right, and the real-data probe pins it. A team
  participation line for KC in G6 would let the guard pin it as well.
- **N2. PFR still decides some rows (row selection, not a feature).**
  - **What happens.** `week_rows`'s `backs` (`research/nfl_rush_dataset.py`) admits a back on `pl[PL["pfr_snaps"]] > 0` alone. That sets `act_played`
    to 1, and `act_played` is tagged `derived:participation+pbp+pfr_snaps`, which is restricted.
  - **How many.** In the committed 2026 rows, 48 of 618 have `act_played` 1 with no touch and no Sleeper projection, so
    they exist only through PFR. 2026 has no participation, so PFR is the only source that can say a no-touch back played.
    The probe's 101 new rows are the same mechanism.
  - **What it means.** No feature reads this. But a model that fits on every row, or conditions on `act_played`, lets PFR
    choose its training rows.
  - **What to do.** The model frame should select on an unrestricted condition (`in_sleeper`, or a touch), or record that
    it accepts PFR for row selection.
