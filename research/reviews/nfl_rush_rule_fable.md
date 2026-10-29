# Fable review: the RB / QB rushing model, its pre-registered rule, the registration machinery and the board hook

Reviewer: Fable 5.1, 2026-10-28 (the owner's "Fable", unavailable during the Sonnet / Opus rounds). I reviewed the
FROZEN tree independently and read the two prior rule reviews only after my own pass through the code. Brief: the
orchestrator's seven areas (leakage, statistics, registration, model, data layer, hook / integration, guards).
Work files: `<scratchpad>/review_rush_fable/` (`coefs_2023_2024.json` the 2023-24 distribution parts I fitted on a
private copy of the registration's stores, `deep/`, `mut/` the mutation harness and `mut.log`, `g_model.log`,
`g_data.log`).

**What I checked first.** Every one of the 26 registered files still hashes to
`<scratchpad>/nfl_rush_model/work/freeze_shas.json` (checked 2026-10-28; the newest registered file's mtime is
08:53:53 UTC 2026-10-24, before the 09:02 freeze). The private store copy I fitted on is the registration's
(`nfl_matchup_2023.pkl` b59ee9dc…, `nfl_matchup_2026.pkl` 82c733ac…, `nfl_rush_aux_2026.pkl` 0b1220d1…, all equal to
the test frame's recorded inputs).

**Rules kept.** No git write; no repository edit but my two review files. No 2025 or 2026 outcome read: every frame read
went through `research.nfl_rush_model_fit.read_frame(..., outcomes=False)` (which drops every `y_*`) or selected
identity, `sl_*`, `fz_*` and `f_*` columns; every distribution fit was `fit_distributions((2023, 2024))`, which
`_guard`s; the one dataset build I ran (B1's demonstration) wrote 2026 weeks 1-5 rows into my scratch directory and I
read its `week` column only, then deleted the files (those cells exist in the registered dataset already). Python ran
with `VIGIL_NO_BG=1`.

---

## 1. Verdict: **AGREE AFTER FIXES**

The model is sound, the machinery does what the rule says, and I found no leak. But the rule as frozen cannot run its
own decisive test:

- **B1 (BLOCKER).** The registered dataset builder stops at 2026 week 7 (`research/nfl_rush_dataset.py:101`
  `TEST_MAX_WEEK = {2026: 7}`), and Q1 names that builder, with no way round the cap, as the command that builds the
  prospective dataset. Weeks 8-11 would never enter it; the prospective frame build then dies on an empty row list;
  and the only remedy is an edit to a registered file, after which `prereg_status` refuses every command and the
  registration is void. It fails closed, which is right, but hashing it means hashing a test that cannot be run.

**Required before the hash** (the first touches three registered files, so: re-freeze, rebuild both frames, re-run the
dry run and the guards, report the new shas):

1. **B1.** Give `research.nfl_rush_dataset build` a `--max-week N` (or `--all-weeks`) flag; the default keeps today's
   behaviour so the registered dataset's build stays reproducible (the registered CSVs are REG_DATA and are not
   rebuilt; on the registration's 2026 store, which holds weeks 1-6 only, lifting the cap changes no byte). Q1's
   prospective command carries the flag. `provenance_refusal` also requires the prospective dataset's
   `meta.json` `by_season["2026"]["weeks"]` to cover the window's weeks. `frame.build` refuses zero rows with a
   message instead of an `IndexError`. Guards: the CLI path builds a fixture week beyond the cap when asked; a
   prospective dataset meta whose weeks omit a window week is refused.
2. **In the same re-freeze, cheap and worth it:** m1 (a wrap detector that reads the grid's tail), m5 (a window week
   with no frozen universe row refuses), and the text of m2, m3, m4, m6 in the block.

The MINOR and NIT items below do not block the hash on their own.

---

## 2. Findings

### BLOCKER

**B1. The registered dataset builder cannot build the window's rows.**

*Evidence.*
- `research/nfl_rush_dataset.py:101` `TEST_MAX_WEEK = {2026: 7}`; `:932` `max_week = TEST_MAX_WEEK if max_week is None
  else max_week`; `:970` `last_week = max_week.get(season, 18)`; `:973-975` `for week in sorted(by_week): if week >
  last_week: break`; `:1207` `main()` calls `run_build(stores, auxes, pos, ids, sleeper, out)` with no `max_week`, and
  `build` has no flag for it (`--raw`, `--sleeper`, `--out` only).
- Q1 (report lines 344-345): "The rushing dataset: `research.nfl_rush_dataset build --sleeper <box> --out
  research/data/nfl_rush_model/prospective_dataset`".
- Demonstration on my private copy of the registration's stores (2026 store weeks 1-6, 93 games): relabelling week 6's
  games as week 9 and running `run_build(..., seasons=(2026,))` with the default `max_week` built weeks
  `[1, 2, 3, 4, 5]`, 482 RB / 182 QB rows; week 9 was dropped by the `break`.
- Downstream: `research/nfl_rush_model_frame.py:413-415` filters the dataset's rows to `--weeks`; with none,
  `write_frame` (`:371`, `rows[0]`) raises on the empty list. `prospective` can therefore never see a frame. Changing
  the cap afterwards changes a REG_CODE file: `prereg_status` then refuses `validate`, `check2026`, `export` and
  `prospective` alike (Q9), and the registration is dead.
- Why no guard saw it: the golden run uses synthetic frames; `chain_checks` exercises `--out`'s directory only; the data
  guards call `run_build` with an explicit `max_week={2030: 3}` (guards_nfl_rush_data.py:1354). Nothing runs the
  default on a week past 7. My mutant F09 (`TEST_MAX_WEEK` 7 -> 5) is in the table below.

*Fix.* As in the verdict. Keep the registered dataset's bytes; put the flag in Q1; make `provenance_refusal` read the
dataset meta's weeks; refuse an empty frame; guard both paths.

### MAJOR

None beyond B1. Everything else I examined either holds or is MINOR.

### MINOR

**m1. The grid-wrap detector misses the reviewer's own measured case.** `Compound.wrapped` flags
`|grid mean - analytic mean| > max(0.5, 0.01 x |mean|)`. On the 2023-24 kernels, 30 carries / 150 yards with ypc x e
(the log-factor clamp): mass beyond 895 = 3.86e-3, grid mean 403.8 against 407.7 (a 3.9-yard shift, under the
4.08-yard threshold), max |delta p| against an 8,192-point grid 3.7e-3, **`wrapped` False**. This is the case the Opus
review's m3 measured (0.39%, -3.96 yards, 0.004) and the fix was calibrated to a different one (14 carries / 500 yards).
The heavier cases are caught (rush + rec both x e: 3.1e-2 wrapped; carries x e and ypc x e: 30.7% wrapped). Exposure
is nil on a real board (a 150-yard line at the full clamp) and the priced error is at most 0.004, so MINOR. Fix: read
the tail directly (mass in the grid's top cells, e.g. `sum(p[-32:]) > 1e-7`, or `mean + 8 sd` against the top), beside
the mean test; pin it with this case. Mutant F01 (threshold 5x looser) survives every guard today.

**m2. The prospective KEEP's power is not stated, and it is about a coin flip at the gate's edge.** From the registered
test frame's counts (lines and identities only): RB rushing 871 rows / 272 games on 2025 against 182 / 57 on the 2026
frozen rows, so a 4-week window of about 60 games holds about 190 RB rushing rows, 140 RB receiving rows and 104 QB
rows, and the prospective SE is 2.09-2.13x the 2025 SE. A TRUE effect that just passes the 2025 gate (d = -2 SE_2025)
is kept (d < -1 SE_p) with probability **0.48**; 1.5x the gate 0.66-0.67; 2x the gate 0.81-0.82. The rule says the
window "keeps or reverts"; the owner reads "shipped" and "reverted" as verdicts. Q6 should say that a real effect at
the gate's size is reverted about half the time. Text only.

**m3. A 2025-informed quantity removes one group from the 2025 walk.** `dead_features` takes the union over BOTH
frames. On the validation frame (k_fit_only, 2018-2024) the QB dead set is `{cfp_dsg_edge}`; on the test frame (pooled
2018-2025 k) it is `{cfp_dsg_edge, spd_qb_td_oe}` (my run, features only). So `spd_qb_td_oe`, alive in the fit-only
frame, leaves the 2025 walk because the pooled table, measured with 2025's games, calls `def_rush_tds_oe` unsupported.
The direction is conservative (a candidate group is removed, never added) and Q2 says "pooled", but not that the
removal rests on 2025 reliability. One sentence in Q2.

**m4. The bands' SE carry no dispersion factor.** `fit_ridge` returns `np.linalg.inv(H)` as the covariance; the first
model scaled its covariance by the quasi-Poisson phi (`research/nfl_matchup_fit.py`, `Model.fit`). For carries (alpha
0.06-0.3) and yards (CV about 0.6) the delta-method band is therefore too narrow, by roughly sqrt(phi). Display only;
no decision reads a band. Mutant F02 (covariance x4) survives: nothing pins the band's scale. Either say so in Q2 ("the
penalised-information SE, without a dispersion factor") or multiply by phi.

**m5. `coverage_refusal` never checks that a window week has rows.** It checks the frame's `--weeks`, a props-log
input per week and a box file per week, fetched 48 h on. A week whose rows are absent for another reason (a dataset
week skipped, a box file present but empty) passes, and `window_games` counts fewer games. B1 is the live example of a
dataset silently short of weeks. Fix (rule.py): refuse a window week with no frozen universe row (pre-game information).

**m6. The kickoff clock has a thin margin, in principle.** `kick_ts` is the pbp's recorded start. On week 6's twelve
Sunday / Monday games it sits 15-218 s AFTER ESPN's scheduled kickoff (my measurement from the committed week-6 props
log). A capture whose content time lands in that gap counts as pre-kickoff. The props log cannot produce one (it
freezes 300 s before ESPN's kickoff: 0 of 638 week-6 records sit after kickoff - 300 s), the DK captures were 40+
minutes early, and the frozen rows' leads on week 6 ran 0.18-0.92 h. Say in Q1 that the 300 s lead covers the
pbp's start delay, or take the earlier of the two clocks where both exist.

**m7. The ids pickle.** `verify_stores` compares the 2023-2025 stores and leaves `nfl_matchup_ids` unchecked ("positions
for the fit seasons do not change"). The prospective `parts_for((2023, 2024, 2025))` reads positions from the FRESH ids
`build-stores` writes, so the shipped kernels' composition can differ from export's by any relisted player. Tiny, and
disclosed in spirit; recording the ids sha in `coefs.json` and comparing would pin it.

### NIT

- **n1.** Report §8 says "The suite's MX section does not list it yet". The working tree's `tests/combo_audit_guards.py`
  (lines 23566-23567) lists `guards_nfl_rush_data` and `guards_nfl_rush_model`. Stale sentence.
- **n2.** The registered dataset's `research/data/nfl_rush/meta.json` carries no `code` record (it was built by the
  pre-`--out` builder). `provenance_refusal` reads the prospective dataset's meta only, so nothing breaks; the report
  should say the committed meta predates the record.
- **n3.** `research/nfl_matchup_dataset.load_sleeper` writes "fetched 2026-10-23 (post-week snapshot)" into every 2026
  row's `source`, a hard-coded date the prospective dataset will carry wrongly. Cosmetic.
- **n4.** `_actual_long` (dataset) excludes aborted snaps; `nfl_rush.Longest` has no aborted part. Consistent with each
  other, and an aborted snap recovered for positive yards is rare, but it is an official rush Kalshi would count.

---

## 3. The seven areas

### 3.1 Leakage: none found

- **Features see nothing from their own week or later.** My own poison test on the data layer (private store copy):
  as of 2024 week 8, I set every 2024 play of weeks >= 8 (20,637 rows, 165 games) to garbage in the store (`yds`, `epa`,
  `succ`, `fd`, `rtd`, direction, box, personnel, pressure, coverage, FTN flags, `wp`, `sd`), emptied `onfield` for
  those games, poisoned the aux's extras, PFR, snaps, situational counts and NGS for them, and blanked the injury
  reports of weeks 9+ (the cutoff week's report is pre-game by design). Rebuilt the snapshot: **348 profiles
  (`defense_run`, `defense_qb`, `offense_line`, `team_context` for 32 teams; `rb`, `rb_room_out`, `status` for 60 backs;
  `qb` for 40 quarterbacks) identical; 30 RB feature dicts through `nfl_rush.features` identical.**
- **The comparables** skip `(s, w) >= (season, week)` (`nfl_rush.comparables`), and `hist[(s, w, team)]` is written
  from a snapshot as of week w BEFORE week w's rows are processed (frame.py:427-437).
- **The participation mask** (`availability_view`) zeroes `part`, `onfield` and the aux's `sit` for the season being
  scored; every participation column in `add_game` is gated on `part`, the FTN box falls back in season as it does on a
  2026 board.
- **The k tables.** `use_k("fit_only")` for the validation frame and `NFLM_K=fit_only` for the first model's deviations,
  asserted at build (`M.K_MODE`); pooled for the test frame. The one 2025-informed effect on the 2025 walk is m3.
- **The pre-game line** is nflverse's closing `spread_line` / `total_line` (frame.py:89-96), never the score; sign =
  favourite positive, the same as production's `game_lines` (`mg if x == fav else -mg`).
- **Frozen captures.** `choose_frozen` takes the latest content time strictly before `kick_ts`; the props log stamps
  the pass time and never rewrites a team inside 300 s of ESPN's kickoff (`nfl_props_log.snapshot`); a DK capture's
  content time is `max(updated_at, captured_utc)`. Week 6: 0 late records, leads 0.18-0.92 h. The pbp-start gap is m6.
- **The hold.** Every outcome-capable read (`read_frame(outcomes=True)`, `season_plays`, `box_rows`,
  `fit_distributions`) goes through `_guard` -> `prereg_status`, which needs `prereg.json`, the block's sha and every
  registered file's sha. My feature-only reads of 2025 / 2026 rows carried no `y_*` column.
- **The dead features** are computed on features only (my run reproduces the registration's union: RB
  `cfp_direction`; QB `cfp_dsg_edge`, `spd_qb_td_oe`; 35 decisions).

### 3.2 The decision rule's statistics

- **The ladder** is the owner's order; `market_groups` skips a group with no live term (OVD has only `eff` terms, so a
  count market's walk starts at SPD), SHAPE enters where the compound prices the market. 35 decisions recounted.
- **The SE** is `max` of the game- and player-clustered cluster-robust SE, each `G/(G-1) x sum(centred cluster
  sums^2)/n^2` (`clustered`, `paired`): correct, conservative.
- **The gate** `d < -2 SE and d < -0.0005 x n_thr`, strict, NaN rejects. The floor is 0.004 nats a row on RB rushing
  yards (8 thresholds), 0.002 on receptions.
- **The 2026 check** keeps on `d <= 0`; **the prospective KEEP** on `d < -1 SE`. Power at the gate's edge: 0.48 (m2).
- **Error rates.** `error_rates(35)`: a null group ships provisionally with probability 1.1375% (0.398 expected,
  33.0% for at least one), permanently 0.1805% (0.0632, 6.13%); Phi(-2) x 0.5 x Phi(-1). Q3b's text matches to the
  digit. The max(game, player) SE makes the real gate stricter than Phi(-2); up to three DEC decisions are vacuous by
  construction (disclosed).
- **Conditional on playing.** `played()` = Sleeper `gp >= 1` or a pbp rush / target / dropback (`y_cond_played`),
  applied to fit and score alike, never PFR snaps (`SELECTION_COLS` guarded). It matches Kalshi's settle-at-fair-price
  and the board's conditional probability. The all-rows figure is reported and decides nothing.
- **The derived markets** (rush + rec, longest rush) decide nothing; `derived_report` reports both-sides d and the
  first priced form's log loss, MAE and ECE; `plan` prices them only through shipped SHAPEs, else "none".
- **Nothing is chosen after seeing held data** that I can find. The QB position split (section 1) was chosen on
  2023 -> 2024. Q8 lists what 2025 / 2026 is not blind on (shape, level, DEC's opportunity terms, everything rushing on
  2026 weeks 1-6). m3 is the one 2025-informed input I add to that list.

### 3.3 Registration and locks

I read `cmd_register`, `prereg_status`, `registered_tree`, `in_registered_tree`, `resolve_model_out`, `build_stores`,
`require_registered`, `verify_stores`, `require_frame`, `read_record`, `write_once`, `record_chain`,
`prospective_frame_refusal`, `provenance_refusal`, `window_games`, `window_weeks`, `coverage_refusal`,
`cmd_capture_missing` and the four commands line by line against Q1, Q5, Q6 and Q9.

- **Twice:** the lock is `write_once`; a `--force` re-run records `forced_rerun` and the previous sha; `export` refuses
  after the lock; a provisional `coefs.json` beside a lock is refused by the board (`record_chain`).
- **Early:** `window_weeks` needs every window week played in the store and the last week closed (a game of week
  lw + 1 in the store); `coverage_refusal` needs each week's box 48 h after the last kickoff and the run 48 h on;
  `prospective_frame_refusal` needs the frame built after the registration.
- **Unregistered code:** the tree marker names the registration; `prereg_status` re-hashes every registered file;
  dataset and store builds record every repository module they ran and `unregistered_code` refuses any other than
  `errlog.py` and `research/__init__.py`; the 2019-2025 stores must be the registered frames'.
- **Re-shipped after a revert:** `export` refuses after the lock; `record_chain` requires the shipped set to BE the
  lock's `kept` and a provisional set to be check2026's.
- **Two trees, one lock:** `resolve_model_out` takes the records' folder from the marker and refuses an
  `NFLR_MODEL_OUT` naming another.

What remains, by construction: the registration is an honesty fence (`prereg_status` lives in a registered file; a
copy of the tree can register itself), as every reviewer has said. And B1: the procedure's dataset step cannot run.

### 3.4 The model

- **Brute force** (2,000,000 games a case, numpy gamma-Poisson counts and kernel draws, on the 2023-24 parts I fitted:
  RB carries alpha = 3.959 x mean^-1.469, kneels by margin 0.476 … 1.064, QB shares 0.300 / 0.149 / 0.552):
  - RB 18 carries / 80 yards (+ 3 receptions / 24 yards): P(yards >= 10 … 100), the longest rush 10 … 40, carries
    5 … 20 and rush + rec 25 … 125 all within |z| <= 2.23 of the simulation; largest absolute difference 0.00076; means
    80.000 / 80.022 and 104.000 / 104.024.
  - QB 6 attempts / 30 yards, margin -7 (kneels 0.487, phi 0.985): P(yards >= 10 … 60), P(yards <= 0) 0.02838 vs
    0.02833, attempts 2 … 8, longest 10 … 40 within |z| <= 2.69 (the two tail points at 50+ / 60+ differ by 0.0007 and
    0.0006 with shared draws); means 30.000 / 30.033, attempts 6.000 / 6.003.
- **Coherence.** With SHAPE "shipped" on every market and no term, every ladder on both players is monotone and in
  [0, 1]; rush + rec mean = 80 + 24 = 104 exactly; QB attempts 6.0 and yards 30.0 exactly; longest-rush means 19.96
  (RB) and 12.85 (QB).
- **Opportunities vs efficiency.** `rb_parts`: `carries` and `rec` move the count, `ypc` / `ypr` the kernel's mean;
  `qb_parts`: counts of designed runs and scrambles, their yards per attempt, sneaks and kneels fixed, the anchoring
  phi keeps the mean at Sleeper's line. The fit mirrors it (`fit_position`: count GLMs, then yards-per-attempt GLMs
  with the count's final factor in the offset; QB components through `_qb_expected`, which is `plan` itself).
- **QB components.** Designed (sneaks excluded by FTN's flag, rule where FTN is absent), sneak, scramble, kneel; sacks
  never (`add_game` falls through to the dropback); kneels at the game script's expected count, capped at 0.8 A.
- **TD link.** Poisson on rush + rec TDs with offset log(Sleeper's TDs), its own groups only.
- **Correlations** are measured residual correlations on the training rows, printed with their basis.
- **Grid wrap:** m1.

### 3.5 The data layer

Reviewed in `research/reviews/nfl_rush_data_fable.md`: AGREE AFTER FIXES, the one fix being B1's cap. The poison test
above, the licence re-derivation (60 fitted (block, path) inputs, none restricted, every declaration equal to the
layer's tag, `EXT_SOURCE` inside `FIT_SOURCES`, no path naming pfr / ngs / snap), RB/FB units, the snap total, the
"unknown" status on a missing report and the appearance rule are there.

### 3.6 The board hook and the integration

- **Byte identity without coefs:** the guard builds both boards on a fixture; the two prior reviewers reproduced it on
  the live week-7 board (a4123646…) against git HEAD's module. I read the hook: `rc` is None without a file, and then no
  `extra_props()` call, no snapshot, no new key.
- **Fallback:** `_rush_env` / `_rush_board` sit in one `try` (`RUSH-board`, a slate note, the first model's board
  stands); `_rush_player` catches every exception per player (`NFLR-board-player`).
- **Wrong-number paths I checked:** Kalshi's floor strike maps to `ceil` (`_lines_from`, 19.5 -> 20; mutant F05 in the
  table); the hook's `team` / `opp` are `to_nflverse` codes and `tb` is keyed by the store's teams; `base` carries
  `rush_att`, `pass_att`, `rec_tgt` (so `in_universe` works); the margin reaches `qb_parts` with the favourite positive
  on both sides; the `td` base is rush + rec TDs; a market outside its universe or at rung 1 keeps the first model's
  block.
- **Cost on Render's one core:** the hook adds 12-16 s and about 70 MB to the board's niced child (measured by the
  builder; the child's timeout is 240 s, the instance 2 GB); the comparables' first build (103-136 s) runs only in the
  deep job and only when a shipped group carries a comparables term (`precompute_vectors`); a board reads stored
  vectors only. Three extra Kalshi fetches a build every 15 minutes while a model ships: accepted and said.
- **The job** (`matchups.nfl_data_job`): a prior season backfills when its store is missing or its aux is stale /
  incomplete, one season at a time (~77 MB on disk), files dropped before the next and again in a `finally`, failures
  isolated and ledgered, a weekly retry marker; `_drop_raw` ledgers a failed removal. `kalshi_nfl.extra_props` fails a
  series whole (`KNFL-extra`) and shares `_add_props` with the index; `artifacts.SCHEMA` 7 names the aux. Reasonable.

### 3.7 Guards

- `python3 tests/guards_nfl_rush_model.py`: **199 checks, 0 failed** (exit 0). `python3 tests/guards_nfl_rush_data.py`:
  **198 passed, 0 failed** (exit 0). Both rush modules are in the suite's MX list (n1).
- My mutants: section 4.

---

## 4. Mutation table (my own, 16 single-point mutants on a private copy of the frozen tree; oracle: the guard file
named, run with `python3 -B`; the control is green)

Runner: `<scratchpad>/review_rush_fable/mut/run.py`, `mutants.json`, log `mut.log`. CAUGHT = the guard file exits
non-zero (the failing check is named).

| id | file | mutant | result | note |
|---|---|---|---|---|
| F01 | nfl_rush.py | wrap detector threshold `0.01 x mean` -> `0.05 x mean` | **survived** | real gap (m1): the threshold is pinned by nothing, and today's value already misses the Opus m3 case |
| F02 | rule.py | band covariance `inv(H)` -> `4 x inv(H)` | **survived** | real gap (m4): no guard pins the band's scale; display only |
| F03 | frame.py | `choose_frozen` admits a capture AT kickoff (`<` -> `<=`) | CAUGHT | "the frozen line must be strictly BEFORE kickoff" |
| F04 | nfl_rush.py | RB TD universe `>= 0.15` -> `> 0.15` | CAUGHT | the Q1 universes check |
| F05 | nfl_matchup.py | Kalshi floor strike `ceil(line)` -> `int(line)` (19.5 -> rung 19) | CAUGHT | "the extra rushing series reach the right player's rungs" |
| F06 | rule.py | rung-1 yards CV over `yy >= 0` instead of `yy > 0` | CAUGHT | three golden checks: the pinned 2025 figures move |
| F07 | nfl_rush.py | `rescale` floor 0.05 -> 0.5 | **survived** | real but low-line-only: binds when a component's target mean falls under half its kernel's (a QB yards line near the sneaks' and kneels' carry); never at a board line |
| F08 | rule.py | `coverage_refusal` ignores an omitted window week | CAUGHT | the coverage check |
| F09 | research/nfl_rush_dataset.py | `TEST_MAX_WEEK` `{2026: 7}` -> `{2026: 5}` (data guards) | **survived** | real gap: B1's cap is pinned by nothing; the guards build fixtures with an explicit `max_week` |
| F10 | rule.py | the run's 48 h lag dropped (the box lag kept) | CAUGHT | the coverage check |
| F11 | frame.py | a capture's content time ignores its fetch clock | CAUGHT | "fetched after kickoff, it is never the frozen line" |
| F12 | nfl_rush_profiles.py | `rb_room_out` reads 0 with no report (data guards) | CAUGHT | "the RB room's share read None, never 0" |
| F13 | nfl_rush_profiles.py | the appearance rule admits PFR snaps (data guards) | CAUGHT | "a back PFR lists only ... does not appear" |
| F14 | rule.py | `window_weeks` starts one week after the registration's week | CAUGHT | "EVERY week from the first after the registration" |
| F15 | nfl_rush.py | the kneel margin's sign flipped inside `qb_parts` | CAUGHT | the kneel cap check and the hook's margin check |
| F16 | rule.py | the floor counts one threshold fewer | CAUGHT | two gate checks |

**Tally: 16 applied; 12 caught; 4 survived, none equivalent.** F09 is B1. F01 and F02 are m1 and m4. F07 is a NIT.
Of the prior reviewers' five argued-equivalent survivors (G09, F06, H05, PL4, B3) I re-read each and agree.

---

## 5. What I could not check

- The real 2025 / 2026 decisions: they need the hash, by design.
- The hook's timing on Render's actual core and the live `extra_props()` network path: I relied on the builder's and the
  two reviewers' measurements.
- Whether Sleeper's `updated_at` is honest for a capture fetched after kickoff: the fetch clock bounds it.
- The full suite `tests/combo_audit_guards.py` (3-8 min): not run here; the two rush guard files pass.
- The builder's 316-mutant run: I ran my own 16 and read the tally.

---

# CONFIRMATION (2026-10-28 22:45 - 2026-10-29 UTC, Fable; round 4, on the frozen bytes)

**What I confirmed on.** `<scratchpad>/nfl_rush_model/work/freeze_shas_r4.json`: 26 files and the rule block,
all hashing as frozen on 2026-10-29 (block `92a99b8e791a7a98ec9e6da849ad9b07b751fd5cab6bd51c03f70d13fc0fbe61`;
`nfl_rush.py` 86af21fa, `research/nfl_rush_model_frame.py` 6d787c4c, `research/nfl_rush_model_fit.py` 1daf3f32,
`research/nfl_rush_model_rule.py` 6904a6a1, `research/nfl_rush_dataset.py` 8b97b7af). The other twelve registered
code files and the seven data files are byte-identical to the round-3 bytes I reviewed (`cmp` against my copy); the
registered set itself (17 code, 7 data) is unchanged. I read the five files' diffs and the report's diff against my
round-3 copy line by line. Work: `<scratchpad>/review_rush_fable/` (`r4diff/`, `mut_r4/`, `dryrun_fable_r4.json`,
`g_model_r4.log`, `g_data_r4.log`).

**Rules kept.** No git write; no repository edit but this section and the data review's. No held outcome: my checks
read features, lines and identities, the 2023-24 parts I had already fitted, and relabelled-store builds whose CSVs
I read for their `weeks` meta only and deleted.

## Verdict: **AGREE TO HASH**

Every item of mine is fixed on these bytes and I reproduced each fix directly, not from the summary:

- **Guards on the frozen bytes** (the guard file's mtime, 22:30, precedes my run at 22:46): model **224 checks, 0
  failed**; data **198 passed, 0 failed**.
- **The dry run, reproduced** (`register --dry-run`, `DEEP_CACHE_DIR` = my private copy of deep3, 01:28 UTC): block
  92a99b8e…, frames val 906e9a7e… / test b3a28947…, **35 decisions**, no stale frame, no blocked input, the same dead
  features, code and data shas as the builder's 22:42 record; **17 store / aux / ids pickles with sha256 AND content
  digest equal to the builder's**; `ids_positions` digest equal (6,476 players); Python 3.11.17 / numpy 2.4.6.
- **The rebuilt frames** carry the r4 bytes of all 17 code files in their metas (built 22:41-22:42 on deep3), the
  same shas as before (nothing moved a row).

### My items

| item | status | how I checked |
|---|---|---|
| **B1** (BLOCKER): the dataset builder capped at 2026 week 7 | **FIXED** | `_week_cap`: no flag -> None (the cap), `--all-weeks` -> {}, `--max-week 9` -> {2026: 9}, a mutually exclusive group. On my relabelled store (week 6 as week 9): the default builds weeks [1-5] (`last_week` 7), `--all-weeks` [1-5, 9] (18), `--max-week 8` [1-5] (8). `provenance_refusal(..., weeks=)` refuses a dataset whose 2026 `weeks` miss a window week; `write_frame` refuses an empty row list with a reason; Q1 names `--all-weeks` and the ABSOLUTE `--out`; `registered-tree` prints the procedure. Mutants: R03 (weeks check off) caught; R05m (`--all-weeks` ignored) caught by the model guards' CLI fixture ("default: 4-7; --all-weeks: 4-9"), survived the DATA guards alone (which build with an explicit `max_week`); R02 (the empty-row refusal removed) survived -- near-equivalent: the build still fails on `rows[0]`, only the message is lost. |
| **m1**: the wrap detector missed my case | **FIXED** | `wrap_flag`: the mean test OR more than 1e-5 in the grid's top 32 values. My case (30 car / 150 yd, ypc x e): top-32 mass 1.87e-3 -> `wrapped` True; 12 / 66 x e: 2.5e-5 -> wrapped; realistic lines at x1.25 (25 / 120; 30 / 165 + 8 / 70 rush + rec): at most 1.3e-6, not flagged; weight 0: 3e-13. **Old vs new engine on 1,700 probabilities: identical (max difference 0).** R01 (threshold x1000) caught by two checks. |
| **m2**: the KEEP's power unstated | **FIXED** | Q6 states it; `keep_power(272, 56)` = 0.46 / 0.64 / 0.79 at 1x / 1.5x / 2x the gate, `(272, 42)` = 0.42 / 0.57 / 0.72: reproduced. R11 (the KEEP's SE dropped from the formula): see the table. |
| **m3**: `spd_qb_td_oe` dead through the pooled k | **FIXED** (text) | Q2 says it is dead only in the test frame, on a 2025-informed reliability, a removal never an addition: matches my per-frame computation. |
| **m4**: the band without phi | **FIXED** (disclosure chosen) | Q2 says the band carries no dispersion factor and runs narrower by about sqrt(phi); display only. F02 (covariance x4): see the table. |
| **m5**: a window week with no rows | **FIXED** | `coverage_refusal(frozen_weeks=)`: a week whose props log is an input but holds no frozen universe row refuses; a "capture missing" week may hold none (said). R04 caught (golden). |
| **m6**: the kickoff clock's margin | **FIXED** (text) | Q1: the props log's 300 s lead is before ESPN's scheduled kickoff; the pbp's start came 15-218 s later on week 6 (re-derived on 11 games). |
| **m7**: the fresh ids pickle | **FIXED** | `register` records `ids_positions` (every player's position and Sleeper id, digested); `parts_for(rec)` -> `registered_positions` -> `fit_distributions(pos_sl=)`; a registration without the record refuses ("register on the current code"); the plumbing path reads the pickle. R06 (the folder's ids read instead) caught. |
| **n1-n4** | n2, n3, n4 done; **n1 not** | Q1 notes the longest rush leaves aborted snaps out; §4 says the registered `meta.json` predates the code record; `load_sleeper`'s date never reaches a dataset column (`_sleeper` reads `proj` / `act` only: I agree). **§8 still reads "199 checks ... The suite's MX section does not list it yet (the proposed one-line diff is in the hand-off)"** (report line 1649-1652) although the round-4 table says it was fixed and the suite lists both rush guard files. Outside the block; cosmetic; fix with the status line at registration. |

### The other reviewers' round-4 items, as the diff shows them

- **Durability.** `content_digest` (canonical sha256 of a pickle's payload without `built`, `seconds`,
  `store_built`, `sources`, `joins`; each part digested; an unknown type raises), `deep_digest`, `digest_diff`,
  `store_refusal` (sha256, else content digest, else a refusal naming the parts); `verify_stores` and
  `provenance_refusal` (`older`) read it; `register` records `stores` and writes `prereg_manifest.json` /
  `.sha256`; `build-stores --older` rebuilds older seasons and holds each to the digest. Mutants R07 (any digest
  accepted), R09 (the save clocks digested), R10 (`digest_diff` always empty) all caught, by two to four checks.
- **Provenance.** `build_stores` moves the season's pickles aside and vouches only for what its refresh BUILT
  (`"n"` in the store summary, `built: True` in the aux's, the ids rebuilt) -- I checked `nfl_matchup_data.refresh`'s
  summary shape: `{"seasons": {s: {"n", ...} | {"skipped"} | {"error"}}, "ids": {...} | None}`, so the vouching reads
  the right keys. The dataset meta records the pickles it read (`_deep_inputs`), and `provenance_refusal` requires
  the 2026 three and refuses a dataset that read another store than the frame. R08 caught; R13 / R14: see the table.
- **Text.** Q1's window (56 games hashed before week 8's Thursday game, 42 after week 8), the absolute `--out`, the
  postponed-game rule; `registered_tree` restores the stored block into the tree's report (R12: see the table).

### Mutants on the changed functions (`<scratchpad>/review_rush_fable/mut_r4/`; the control is green)

Runner `mut_r4/run.py`, `mutants.json`, log `mut.log`; one string replacement each on a private copy of the frozen
tree; oracle the guard file named, `python3 -B`; CAUGHT = a non-zero exit (the failing check is quoted in the log).

| id | file | mutant (the changed function it attacks) | result | note |
|---|---|---|---|---|
| R01 | nfl_rush.py | `WRAP_TAIL` 1e-5 -> 1e-2 (`wrap_flag`) | CAUGHT | two checks: the flag's edges and a heavy line the mean test passes |
| R02 | frame.py | `write_frame`'s empty-rows refusal removed | **survived** | near-equivalent: the build still fails on `rows[0]`; only the reason is lost. A one-line guard would pin the message. |
| R03 | rule.py | `provenance_refusal` stops checking the dataset's 2026 weeks | CAUGHT | the provenance check |
| R04 | rule.py | `coverage_refusal` ignores `frozen_weeks` | CAUGHT | the golden run (`coefs.json` refusal downstream) |
| R05 | dataset.py | `_week_cap` ignores `--all-weeks` (DATA guards) | survived | the data guards build with an explicit `max_week`; **R05m, the same mutant against the MODEL guards: CAUGHT** ("default: 4-7; --all-weeks: 4-9") |
| R06 | rule.py | `parts_for` reads the folder's ids instead of `registered_positions` | CAUGHT | "never a later ids pickle" |
| R07 | fit.py | `store_refusal` accepts any content digest | CAUGHT | two checks (re-saved vs changed pickle; `--older`) |
| R08 | fit.py | `build_stores` vouches for a store or aux its refresh did not build | CAUGHT | Opus F1a's check |
| R09 | fit.py | `NOT_CONTENT = ()` (the save clocks enter the digest) | CAUGHT | four checks |
| R10 | fit.py | `digest_diff` always empty (a changed pickle passes) | CAUGHT | three checks |
| R11 | rule.py | `keep_power` drops the KEEP's 1 SE | CAUGHT | Q6's figures pinned to `keep_power` |
| R12 | fit.py | `registered_tree` keeps the live report's block | CAUGHT | Sonnet F5e's check |
| R13 | rule.py | `provenance_refusal` ignores a dataset built on other stores | CAUGHT | |
| R14 | dataset.py | `_deep_inputs` omits the ids pickle | CAUGHT | the producer check (Opus F1b) |
| F09 | dataset.py | `TEST_MAX_WEEK` 7 -> 5 (DATA guards) | survived | as R05: the data guards never run the default past week 7 |
| F09m | dataset.py | `TEST_MAX_WEEK` 7 -> 5 (MODEL guards) | CAUGHT | the B1 CLI fixture |
| F02 | rule.py | band covariance x4 | CAUGHT | "the inverse penalised information, with no dispersion factor: Q2 says so" |

**Tally: 18 applications; 15 caught; 3 survived -- two of them (R05, F09) the same mutants caught by the model
guard file, and R02 near-equivalent (fail-closed either way).** Every fix I asked for is pinned where it matters.

### Separately, as asked

- **`y_played` reaches no selection and no decision.** It is written by `outcomes_of` (frame.py:326) and read by
  nothing in `nfl_rush_model_rule.py`, `nfl_rush_model_fit.py` or `nfl_rush.py` (my grep): `played()` reads
  `y_cond_played`, else `y_gp` / `y_pbp_play`; `SELECTION_COLS` exclude `act_played`; `dispersion`, `correlations`
  and every walk select through `played()`. Dropping it from the frames would change their bytes for no decision;
  committing them as they are is sound for the hash.
- **The private archive of the registered dataset, a procedural note.** `prereg_status` hashes the seven REG_DATA
  files from the WORKING TREE, and `registered_tree` copies the working tree (untracked files included). If the
  two CSVs leave the public repository, they must be restored into the working tree from the archive (checked by
  sha) before any command and before the tree is built, or every command refuses. One line in the runbook.
