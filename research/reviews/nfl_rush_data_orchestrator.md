# Orchestrator review: the NFL rushing data layer

2026-10-23. I review independently of the Sonnet and Opus reviewers
(`nfl_rush_data_sonnet.md`, `nfl_rush_data_opus.md`). The deliverable is
`nfl_rush_data.py`, `nfl_rush_profiles.py`, `research/nfl_rush_dataset.py`,
`tests/guards_nfl_rush_data.py`, `research/data/nfl_rush/` and
`research/reports/nfl_rush_data.md`.

## Verified

- **Guards:** `python3 tests/guards_nfl_rush_data.py` passes 87, fails 0, exits 0, in 0.39 s.
- **pyflakes** is clean on all four modules.
- **Hashes:** the agreed dataset `research/data/nfl_matchup/dataset_2023_2026.csv.gz` is
  still `766a60ed…`. The deliverable's files hash as the report's §10 says.
- **Snapshot cost:** on a copy of the builder's deep store, the snapshot as of
  2026 week 7 (2024-2026 stores) builds in 1.35 s, 1.47 s with imports, at 84 MB
  max RSS. That covers the rushing snapshot only; the report's 3.0 s / 137 MB covers
  both snapshots. Every aux store `aux_matches` its matchup store.
- **Production modules:** stdlib only at import. The excepts are typed or ledgered
  (`RUSH-fetch`, `RUSH-align`, `RUSH-k-table`); none is silent.
- **The decay rule's history:**
  - The first measurement ran at 20:52:59 UTC on script `262da6dc…`, hashed
    20:52:54 (`rush_work/prerun_hashes.txt`).
  - `persistence.first.json` and the final `persistence.json` carry the same rule
    text, the same ten tendency metrics, the same pooled r (0.602) and the same
    DECAY (0.75). All four measurement logs print the same line.
  - Nothing changed after the number was seen. Whether the ten-metric list itself
    was fixed before 20:52 is for the reviewers to check against the hashed bytes.

## Findings

### O1 (MAJOR, integration): the proposed job never builds 2024-25's aux stores on an instance that already backfilled

- **The defect.** `matchups.nfl_data_job` decides `back` by the agreed store alone:
  `D.load_season(s) is None`. The diff in §8.1 runs the rushing backfill over that
  same `back`.
  - The agreed backfill shipped in e81d5c4 (19:25 UTC). On any server or PC where
    it has already run, `back` is empty forever.
  - The rushing layer would then build only the daily season (2026). The board's
    two prior seasons would never get an aux store: no situational on-field
    counts, no PFR / NGS / snaps, no extras, so aborted snaps read as designed runs.
  - Every 2026 participation-derived field reads ONLY those prior seasons.
- **Fix (the orchestrator, in the integration).**
  - The rushing backfill gets its own condition: a season among cur-2 and cur-1,
    not in daily, whose aux store is missing or does not `aux_matches` its matchup
    store.
  - For such a season, call `D.refresh(seasons=[s], keep_raw=True)` FIRST. Its raw
    files were deleted, so it re-downloads and REBUILDS the agreed store, and the
    store and the aux then come from the same pbp file, so the extras align.
  - Then call `R.refresh(seasons=[s], keep_raw=False)`, then delete that season's
    pbp, participation and FTN.
  - Isolate each season, so one failure never blocks another, and ledger it.
  - Add a guard, run on a fixture deep store where the agreed store exists and the
    aux does not, that would have caught this.

### O2 (MINOR, label + train/serve skew): a week with no injury report reads "active", sourced "final report, pre-kickoff"

- **Evidence.** In `RushSnapshot._report`, `(aux["injuries"].get(self.week) or {}).get(team) or {}`
  returns `{}` when the week is absent. On the scratch store, a 2026 week-8
  snapshot (injury weeks 1-7 held) returns:
  `status(Jackson, BAL) -> {'value': 'active', 'listed': False, 'source': 'nflverse injuries 2026 week 8 (final report, pre-kickoff)'}`.
  The same path gives `out_report` 0 and `rb_room_out` 0.0.
- **Why it matters.** In research every week has a final report, so "not listed"
  means not listed. In production, a board built before nflverse carries the week's
  report would read every back as active, and his line's absences as none, while
  saying the opposite of what happened.
- **Fix.** Return `None` when the week is absent, which gives "unknown" and
  `out_report` None. Keep `{}` for a week that exists with the team absent. Add a
  guard on that exact case.

### O3 (MINOR, robustness): one season's bad source file aborts every season's rushing refresh

- **The defect.** `refresh` loops over seasons with no per-season isolation. A
  truncated or reshaped CSV raises out of `build_aux` and then out of `refresh`,
  and the seasons after it are not rebuilt.
- **Fix.** Wrap each season's build in a try that ledgers `RUSH-build` with the
  season and records the error in `summary["seasons"][season]`. Add a guard with a
  truncated file in one season and a good one in the next.

## On the Opus review (`nfl_rush_data_opus.md`)

- **B1 (BLOCKER): AGREE, confirmed independently.**
  - `research/nfl_rush_dataset.unit_profiles` iterates `T.rb` with no position filter,
    and `add_game` files every non-QB designed-run carrier there.
  - On the 2024 store, `T.rb` holds RB 135, WR 92, TE 14, FB 4 and unknown 9 units.
    Of those with 30+ targets: WR 67, RB 39, TE 11, FB 1.
  - So the RB reliability and k figures pool receivers.
- **M1 = O2: AGREE.** The reviewer reached it independently and widened it to
  `rb_room_out`, which reads 0.30 -> 0.0 for CHI/NE as of week 8.
- **M2 = O1: AGREE.** The reviewer adds the failure modes: a killed run strands about
  154 MB of raw files, and a failed rush backfill is never retried. Both go into the
  orchestrator's integration fix: backfill keyed on aux status, cleanup in `finally`,
  and a sweep of leftover backfill files.
- **M3, M4, M5: AGREE.**
- **Minors m1-m6 and nits N1-N8: AGREE.**
  - m3: write 2023-2025 rows with `k_fit_only`, so the 2025 validation reads no 2025
    reliability.
  - m4: disclose the post-week universe beside `status`.

## On the Sonnet review (`nfl_rush_data_sonnet.md`)

- **B1: AGREE.** This is the same finding as Opus B1. Both reviewers re-derived it
  from raw independently.
- **B2 (BLOCKER): AGREE, confirmed from raw.** `read_snaps` sets the team's total to
  `max(round(snaps / pct))`. I checked `snap_counts_2024.csv`:
  - it covers 544 team-games, and 543 of them have a player at 100%;
  - the estimate exceeds the real total (the max snap count) by a mean of +7.35, a
    max of +33, and on 91% of games.
  Every snap share therefore reads low.
- **M1 = O1 = Opus M2: AGREE.**
- **M2 (guard blind spots, 27 of 45 mutants survive): AGREE.** The builder must catch
  both reviewers' real-gap survivors, or argue that they are equivalent.
- **m1-m12: AGREE.**
  - m6: `played` is an outcome. Rename it.
  - m9: injury rows modified after kickoff: 6 of about 27,000. Disclose this.
  - m7: odd/even is optimistic for usage. Disclose it beside the usage k.
- **Nits: AGREE.**

## Verdict

**AGREE AFTER FIXES**, with the Opus and Sonnet reviewers. The fix list went to the
builder on 2026-10-23 at about 22:45Z as F1-F12:

| Fix | Severity | What it fixes |
|---|---|---|
| F1 | blocker | RB units restricted to RB/FB; re-measure, k, DECAY by the rule, and rebuild |
| F2 | blocker | snap totals |
| F3 | major | the absent report |
| F4 | major | aux stamps; per-season isolation; coverage |
| F5 | major | schema labels; `act_played`; the license block |
| F6 | major | both reviewers' real-gap mutants |
| F7 | minor | `k_fit_only` on 2023-25 rows |
| F8 | minor | share clamps |
| F9 | minor | `act_any_snap` |
| F10, F11 | minor / nit | disclosures and nits |
| F12 | — | §8.1 handed to the orchestrator |

The orchestrator writes the integration: backfill keyed on aux status, the agreed store
rebuilt from the same download, cleanup in `finally`, a leftover-file sweep, and
per-season isolation. It comes with guards and goes to the integration review. After the
builder's fixes, both reviewers re-check before anything is hashed or committed.

## O1 fixed (the orchestrator's integration, `matchups.nfl_data_job` + `_drop_raw`)

### Behaviour

- **Which prior seasons backfill.** Of the two seasons before the current one that the daily rule does not read, a
  season backfills when either its matchup store is missing or its aux does not `aux_matches` that store.
- **Order.** Prior seasons' raw files are removed first. Then each season, one at a time:
  - its matchup store is refreshed with its files kept. They were just removed, so this is a fresh download and a
    rebuild;
  - its aux is built from the same files (`keep_raw=False`);
  - its files are dropped.
  After that comes the daily season on both layers.
- **Peak disk.** One season's files are on disk at a time: about 77 MB, not 154.
- **Cleanup.** A `finally` removes every prior season's pbp, participation and FTN files, the rushing layer's PFR,
  injury and snap files, and their freshness records. A removal that fails for any reason other than a missing file is
  ledgered as `RUSH-rm`.
- **Failures.**
  - Each season's rush refresh is isolated: a failure is ledgered `RUSH-refresh` and reported in the job's result.
  - A rushing layer that cannot be imported (`RUSH-import`) never stops the matchup refresh.
  - A failed aux is retried the next day, because it still does not match its store.

### Guards and mutants

`tests/guards_matchups.py` now holds 37 checks. The old two job checks were rewritten onto fakes of both layers, and
five new checks were added. All run in a scratch deep store with a scratch ledger.

Eleven single-point mutants, all caught; the control stays green:

| Mutant | What it breaks |
|---|---|
| backfill keyed on the matchup store alone | the original O1 bug |
| no upfront drop | stale files survive into the rebuild |
| no per-season drop | two seasons on disk at once |
| no `finally` drop | files stranded on a failure |
| cleanup moved after the `finally` | files stranded on a failure |
| no per-season isolation | one season's failure costs the others |
| backfill with `keep_raw=False` | the aux has no files to read |
| rush kinds never dropped | the rushing layer's files stranded |
| freshness records never dropped | records left beside deleted files |
| import unguarded | a broken rushing layer stops the matchup refresh |

## Final (2026-10-24, ~02:30Z)

**AGREED TO SHIP**, with Sonnet (CONFIRMATION: AGREE TO SHIP) and Opus (CONFIRMATION: AGREE TO SHIP).

Builder rounds:
- Round 2: F1-F12 and F14, with F13 declined (the nominal front exists only for 2025-26).
- Round 3: G1-G5.

The orchestrator's job side covers both reviewers' integration findings:
- `rush_due` fires on an incomplete aux;
- a rebuild of a healthy matchup store asked for only by the rushing layer is wrapped, so it never costs the daily refresh;
- a weekly retry marker stops a persistent failure from redownloading 77 MB every day;
- one season at a time, cleaned up in a `finally`;
- the job guards run in every month.

All of it is guarded by `tests/guards_matchups.py`: 46 checks, which also hold in January, April, June, September and October with the clock patched. They found and fixed a season-dependence in the older board checks. The reviewers' mutants of each fix are caught: the marker write, a strict `aux_complete`, and an incomplete aux.
