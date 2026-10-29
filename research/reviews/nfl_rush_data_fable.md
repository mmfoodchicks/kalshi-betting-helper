# Fable review: the NFL rushing data layer (RB + QB)

Reviewer: Fable 5.1, 2026-10-28. Independent of the Sonnet, Opus and orchestrator data reviews
(`nfl_rush_data_{sonnet,opus,orchestrator}.md`), which I read after my own pass. Under review: `nfl_rush_data.py`
(247cc625…), `nfl_rush_profiles.py` (0d319ed0…), `research/nfl_rush_dataset.py` (d75ad292…),
`tests/guards_nfl_rush_data.py`, `research/data/nfl_rush/*`, `research/reports/nfl_rush_data.md`, and the
integration diff (`matchups.nfl_data_job`, `_drop_raw`, `kalshi_nfl.extra_props`, `artifacts.SCHEMA` 7,
`tests/guards_matchups.py`). All three code files still hash to the freeze (`<scratchpad>/nfl_rush_model/work/
freeze_shas.json`). Work files: `<scratchpad>/review_rush_fable/`.

**Rules kept.** No git write; no repository edit but my two review files. No 2025 or 2026 outcome read: my probes ran
on 2024 (open) or read identity, line and feature columns only; the one 2026 dataset build I ran (D1's demonstration)
wrote weeks 1-5 rows into my scratch directory, I read its `week` column only, and I deleted the files. `VIGIL_NO_BG=1`.

---

## 1. Verdict: **AGREE AFTER FIXES**

The layer is sound: as-of by construction and by my own poison test, licence-clean on every fitted input, RB/FB units,
the snap total fixed, the absent report read as "unknown", appearances from pbp and participation only. One defect
must be fixed before the model's rule is hashed, because the rule names this layer's builder as the tool that makes the
decisive test's dataset:

1. **D1 (MAJOR here; the rule review's B1).** `research.nfl_rush_dataset build` stops at 2026 week 7
   (`TEST_MAX_WEEK = {2026: 7}`) and offers no way past it. Add `--max-week N` (or `--all-weeks`), keep the default, and
   guard the CLI path on a fixture week beyond the cap. The registered CSVs do not change.

Everything else below is MINOR or NIT and does not block.

---

## 2. Findings

### MAJOR

**D1. The dataset builder cannot build 2026 weeks past 7, and nothing says so.**

- `research/nfl_rush_dataset.py:101` `TEST_MAX_WEEK = {2026: 7}`; `:932` `max_week = TEST_MAX_WEEK if max_week is None
  else max_week`; `:970` `last_week = max_week.get(season, 18)`; `:973-975` the week loop `break`s past it; `:1207`
  `main()` passes no `max_week`; `build` takes `--raw`, `--sleeper`, `--out` only.
- `--out` was added in round 4 precisely so the model's PROSPECTIVE dataset (2026 weeks 8-11, extended to 13) could be
  built outside the registered folder (report §4 of the model report; the rule's Q1 names the command). With the cap,
  that build holds 2026 weeks 1-7 at most.
- Demonstration on my private copy of the registration's stores (`nfl_matchup_2026.pkl` 82c733ac…, weeks 1-6, 93
  games): week 6's games relabelled as week 9, `run_build(..., seasons=(2026,))` with the default `max_week` -> weeks
  `[1, 2, 3, 4, 5]` built, 482 RB / 182 QB rows, week 9 dropped.
- The guards call `run_build` with an explicit `max_week={2030: 3}` (guards_nfl_rush_data.py:1354) and never the
  default past week 7. My mutant F09 (`TEST_MAX_WEEK` 7 -> 5) is in the table.
- Fix: a `--max-week` / `--all-weeks` flag on `build`, default unchanged (the registered dataset's build stays
  reproducible; on the registration's store, which has weeks 1-6, the cap changes no byte); the model's Q1 carries the
  flag; a guard runs the CLI path (`main`-equivalent) on a fixture with a week beyond the cap, with and without the
  flag. A registered file changes, so the model re-freezes (its review says how).

### MINOR

**D2. The committed `meta.json` carries no `code` record.** `research/data/nfl_rush/meta.json` (registered, ced28e4c…)
was written by the pre-`--out` builder; the `code` / `versions` block the report's §4 (model report) describes exists
only in new builds. Nothing reads it for the registered set, so nothing breaks; the report should say the committed
meta predates the record, or the meta should be regenerated from the byte-identical rebuild and re-registered.

**D3. `load_sleeper`'s 2026 `source` is a hard-coded date.** `research/nfl_matchup_dataset.py` writes "fetched
2026-10-23 (post-week snapshot)" into every 2026 row; the prospective dataset will carry it wrongly. Cosmetic.

**D4. One guard gap my mutants found** (table below): the 2026 week cap is pinned by no guard (F09 survives at 198
passed); the two data-layer definitions I attacked that matter most to the model -- the absent report reading None
(F12) and the appearance rule excluding PFR snaps (F13) -- are pinned.

### NIT

- **n1.** `_actual_long` excludes aborted snaps, which are official rushes (an aborted snap recovered for positive yards
  would be Kalshi's longest rush). Rare; the schema's meaning ("longest designed run or scramble") is honest about it.
- **n2.** `status` never reads doubtful / out for a non-player (the universe of non-players is Sleeper's post-week
  snapshot): disclosed in §6; worth repeating beside `tm_rb_out_share`, which the model's `dec_room_out` reads, since
  the backs it counts are exactly those who did not play.

---

## 3. What I re-derived

### 3.1 As-of, strictly earlier (my own poison test)

Private copy of the registration's 2022-2024 stores and auxes. Snapshot as of **2024 week 8** through
`nfl_rush.availability_view` + `RP.build`. Then: every 2024 play of weeks >= 8 (20,637 rows, 165 games) poisoned in
the store's columns (`yds`, `epa`, `succ`, `fd`, `rtd`, `rloc`, `rgap`, `box`, `ndb`, `ndl`, `press`, `mz`, `cov`,
`frush`, `fbox`, `sneak`, `rpo`, `screen`, `air`, `yac`, `wp`, `sd`), `onfield` dropped for those games, the aux's
extras (`aborted`, `rush_yds`) poisoned on those rows, `pfr_rush` / `pfr_def` / `pfr_pass` / `snaps` emptied for those
game ids, `sit` dropped for those games, `ngs` emptied from week 8, `injuries` emptied from week 9 (the cutoff week's
report is read by design). Rebuilt the snapshot and compared, as JSON: `defense_run`, `defense_qb`, `offense_line`,
`team_context` for all 32 teams; `rb(pid, team)`, `rb_room_out`, `status` for 60 backs; `qb` for 40 quarterbacks:
**348 profiles, 0 differ.** Through production's adapter (`nfl_rush.features` with the team blocks and league means):
30 RB feature dicts, 0 differ. Both snapshots built in 1.8 s.

By code: `season_tallies(max_week=cutoff)` folds weeks < cutoff only; `RushSnapshot.__init__` raises on any tally week
at or past the cutoff; the ONE cutoff-week read is `_report` (the injury report, pre-kickoff, 6 of ~27,000 rows
modified after kickoff on 2018-2024 per the report's §9.10).

### 3.2 Licence

Every (block, path) a fitted feature or the comparables' vector reads (`research.nfl_rush_model_frame.fitted_paths`,
60 inputs) mapped through `RP.SOURCE_OF`: **none restricted**, every `nfl_rush.PATH_SOURCE` declaration equal to the
layer's tag, `EXT_SOURCE` inside `FIT_SOURCES` = {ftn, injuries, lines, participation, pbp}, no fitted path naming
pfr / ngs / snap. The restricted fields (`usage.snap_share`, `recent.snap_share`, `any_snap.*`, `rushing.line.ybc`,
`rushing.back.*`, `contact.*`, `pressure_pfr`, `starters.*`) reach the board as facts with `DISPLAY_ONLY` in their basis
(`nfl_rush.facts`). The appearance rule (`_close_game`, `PL["app"]`) reads rushes, designed runs, targets, scrambles,
aborted snaps, kneels, on-field snaps, situational counts or a QB line, never PFR snaps (mutant F13 in the table).

### 3.3 Definitions

- **RB units RB/FB only:** `_pos` maps RB and FB to "RB"; `T.rb` takes `rpos == "RB"` only (profiles.py:593-594); the
  defense's "vs RB" figures are keyed by the rusher's position; `unit_profiles` filters on the crosswalk position.
- **Snap totals:** the team's offensive snaps are the most any player took, the snaps / pct fallback only when nobody is
  at 99.5%+ (`read_snaps`; guards pass on both fixtures).
- **The absent report:** `_report` returns None when the aux holds no report for the week, or the week's report lists
  nobody from the team; `status` then reads "unknown", `ol_listed_out` / `rb_room_out` None (mutant F12 in the table).
- **Appearance-based games:** `rb()`'s `games` are the games with `app > 0`; the PFR-inclusive set feeds only the
  restricted `any_snap` block and the PFR snap shares.
- **Kneels, sacks, aborted snaps:** `add_game` counts a kneel as a rush (negative yards), never a team play; a sack is
  never a rush and falls through to the dropback; an aborted snap is an official rush and never a designed run; the
  guards cover each.
- **Row selection:** `week_rows` gives a back a row from a designed run, target, aborted snap, scramble, on-field snap
  OR a PFR snap, or a Sleeper projection; the model's universes (`nfl_rush.in_universe`) select on Sleeper's line only
  and its `played` on Sleeper's `gp` or a pbp touch, so PFR never chooses a fitted or scored row.

### 3.4 Production code and integration

- Stdlib only at import in both production modules; every `except` is typed or ledgered (`RUSH-fetch`, `RUSH-align`,
  `RUSH-k-table`, `RUSH-build`, `RUSH-rm`, `RUSH-aux`, `RUSH-noreport`, `RUSH-incomplete`).
- `matchups.nfl_data_job`: a prior season backfills when its matchup store is missing or its aux is stale / incomplete;
  the prior seasons' raw files are dropped before, between and in a `finally` (~77 MB at peak on the 1 GB disk); each
  season's rush refresh is isolated and ledgered; a rushing-only rebuild is tried once a week; an un-importable rushing
  layer never stops the matchup refresh; `precompute_vectors` runs here, only when a shipped group reads a comparables
  term. 46 guards in `tests/guards_matchups.py`, the job's with the clock patched across months.
- `kalshi_nfl.extra_props`: the three series the board alone prices, `_add_props` shared with the index so the rungs
  parse alike; a failed series is left out whole (`KNFL-extra`); the index itself is unchanged (`_PROP_SERIES`).
- `artifacts.SCHEMA` 7 names `nfl_rush_aux_<season>.pkl` and the retry marker.
- Cost: the snapshot is built once a board in the niced child (the builder measured +12-16 s and about +70 MB; a
  three-season snapshot builds in 1.8 s here); never in a gunicorn worker.

### 3.5 Guards

`python3 tests/guards_nfl_rush_data.py`: **198 passed, 0 failed**, exit 0. My mutants: section 4.

---

## 4. Mutation table (the data-layer mutants of my run; oracle `tests/guards_nfl_rush_data.py` on a private copy)

Runner: `<scratchpad>/review_rush_fable/mut/run.py` (the same harness as the rule review's; `mutants.json`, `mut.log`).

| id | file | mutant | result | note |
|---|---|---|---|---|
| F09 | research/nfl_rush_dataset.py | `TEST_MAX_WEEK` `{2026: 7}` -> `{2026: 5}` | **survived** (198 passed) | real gap: D1's cap is pinned by nothing; the guards pass an explicit `max_week` to `run_build` |
| F12 | nfl_rush_profiles.py | `rb_room_out` reads 0 (not None) with no injury report | CAUGHT | "no report for the week: the OL's out count and the RB room's share read None, never 0" |
| F13 | nfl_rush_profiles.py | the appearance rule (`PL["app"]`) admits PFR offensive snaps | CAUGHT | "a back PFR lists only ... does not appear; one with a play does" |

Three data-layer mutants of my sixteen (the other thirteen aimed at the model and the hook; the rule review has them):
2 caught, 1 survived (F09 = D1). The prior data reviewers' real-gap survivors were argued or caught in rounds 2-5; I
did not re-run their lists.

---

## 5. What I could not check

- Re-derivation of the §4-5 numbers from the raw nflverse files (the 99.98% / 99.76% reconciliations, the 749 kneel
  games, the split-half k, DECAY 0.602): both prior data reviewers did it independently from raw and agreed; I checked
  the definitions in code and the as-of property by poison, not the counts.
- Kalshi's settlement of kneels / scrambles (239 of 239): the data-source report's inference; I did not re-run it.
- The job on the server (the 1 GB disk, the daily cadence): read, not run.

---

# CONFIRMATION (2026-10-29 UTC, Fable; round 4, on the frozen bytes)

`nfl_rush_data.py` 247cc625… and `nfl_rush_profiles.py` 0d319ed0… are unchanged from the bytes I reviewed;
`research/nfl_rush_dataset.py` is now 8b97b7af… (the round-4 freeze, `freeze_shas_r4.json`, all 26 files and the
block hashing as frozen). `tests/guards_nfl_rush_data.py` is unchanged: **198 passed, 0 failed** on these bytes.

## Verdict: **AGREE TO SHIP**

| item | status | how I checked |
|---|---|---|
| **D1** (MAJOR): `build` capped at 2026 week 7 | **FIXED** | `build --all-weeks` / `--max-week N` (a mutually exclusive group; `_week_cap`); the default is unchanged, so the registered CSVs stay reproducible. On my relabelled store (week 6 as week 9): default [1-5], `--all-weeks` [1-5, 9], `--max-week 8` [1-5]; `meta.json` now records `last_week`, the weeks and the sha256 of every store, aux and ids pickle the build read (`inputs`). The cap and the flags are pinned in the MODEL guard file's CLI fixture (R05m caught: "default: 4-7; --all-weeks: 4-9"); the data guards alone do not pin them (R05 and F09 survive there, by design: they build with an explicit `max_week`). |
| **D2**: the committed `meta.json` without a code record | **DISCLOSED** | The model report's §4 says the registered `meta.json` predates the code record and that only the prospective dataset's meta is held to one. The data report's §10 still lists `research/nfl_rush_dataset.py` as 82e4aa12 (the round-3 lineage, two rounds old); cosmetic. |
| **D3**: `load_sleeper`'s hard-coded date | **ARGUED, agreed** | No rushing-dataset column reads `source` (`_sleeper` reads `proj` / `act` only), so the string reaches no committed cell. |
| **D4**: the cap pinned by no guard | **FIXED** | R05m and F09m (model guards): see the table. |

The layer's own code did not change in round 4; my as-of poison test, the licence re-derivation and the definition
checks of the first review stand.

### Data-layer mutants of the round-4 run

| id | file | mutant | data guards | model guards | note |
|---|---|---|---|---|---|
| F09 / F09m | research/nfl_rush_dataset.py | `TEST_MAX_WEEK` 7 -> 5 | survived (198 passed) | **CAUGHT** ("the dataset build stops at 2026 week 7 by default ... builds every week the store holds with --all-weeks") | the cap is pinned in the model guard file's CLI fixture, which is where the prospective procedure lives; the data guards build fixtures with an explicit `max_week` |
| R05 / R05m | research/nfl_rush_dataset.py | `_week_cap` ignores `--all-weeks` | survived | **CAUGHT** | as above |
| R14 | research/nfl_rush_dataset.py | `_deep_inputs` omits the ids pickle | -- | **CAUGHT** | the dataset meta's record of the pickles it read (Opus F1b) |

The data guard file itself did not change in round 4 (and did not need to: the layer's production modules are the
round-3 bytes); the new behaviour of `build` is pinned by the model guards that own the prospective procedure.
