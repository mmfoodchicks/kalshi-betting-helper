# Registered reads: run each from its registration commit (option (c))

2026-11-28. The owner approved option (c) on 2026-11-27 so that production's NFL engine can be fixed (the
fumble-return touchdown, `research/reports/fumble_td_fix.md`) without touching any registration. This page is
the procedure every registered read below follows from now on, and what the current tree's guards check.

## Why

Nine registrations pin `nfl_deep_sim.py` by sha256 (and the coaching DFS read pins `nfl_dfs_engine.py` too).
Their read scripts refuse to run when a pinned file differs from its pin. Fixing the engine in production would
therefore make every one of them unrunnable in the live tree, and editing a registration is never allowed.

Option (c) resolves this:

- Each read runs from a checkout of **its own registration commit**, where every pinned file is byte-identical
  to its pin.
- Each prereg, protocol, data file and read script stays exactly as registered.
- The current tree's guards verify the pins **at the registration commit** (`git show <commit>:<path>`), not in
  the live files.

## The registrations

Each commit below is the one that added the prereg (`git log --diff-filter=A`). Every pinned file was
verified there on 2026-11-28 (`python3 research/registered_pins.py verify`).

| Registration | Commit | Read | Not before (UTC) | Needs first |
|---|---|---|---|---|
| R1 `prereg_r1.json` | eaca95d | `research/nfl_coach_r1_read.py` | 2027-01-06 06:00 | — |
| R2 `prereg_r2.json` | dfc026e | `research/nfl_coach_r2_read.py H1` / `H2` | H1 2027-01-06, H2 2028-01-13 | R1's read |
| R3 `prereg_r3.json` | 59b15fc | `research/nfl_coach_r3_read.py` | 2027-01-06 06:00 | R1's read |
| R4 `prereg_r4.json` | c569318 | `research/nfl_coach_r4_read.py` | 2028-01-13 06:00 | R1's read |
| R5 `prereg_r5.json` (2028 replication) | 79f2bee | `research/nfl_coach_r5_read.py` | 2029-01-16 06:00 | R1's and R4's reads |
| R5-2027 `prereg_r5_2027.json` (primary) | 416ba98 | `research/nfl_coach_r5_read_2027.py` | 2028-01-13 06:00 | R1's and R4's reads |
| R6 `prereg_r6.json` | 35809d1 | `research/nfl_coach_r6_read.py primary` / `replication` | 2028-01-13 / 2029-01-16 | R1's read / R6's primary |
| C4 `prereg_c4.json` | 33c49ab | `research/nfl_coach_c4_confirm.py` (looks 1, 2) | per look | R1's read (failed criterion 4 alone) |
| Coaching DFS `prereg_dfs.json` | 33c49ab | `research/nfl_coach_dfs_read.py` | 2027-01-06 06:00 | — |

Every read script exists at its registration commit (checked 2026-11-28).

### Registrations this procedure does NOT cover

None of these pins the engine files, and none needs to change:

- **The deep-weight read** (`research/nfl_deep_weight_read.py`). It grades the rows production logged, not an
  engine run, so it runs in the live tree as its rule says. The engine fix is GATED on its result
  (guards DA2 and FT2), so the rows it grades come from the engine as it stood.
- **C3-N, the rushing model, cl_field2, the DFS eligibility test** and the others listed in the contamination
  table of `fumble_td_fix.md` section F.

## Running a read

```
python3 research/registered_pins.py prepare research/data/nfl_coach/prereg_r2.json ../read-r2
```

`prepare` does the following:

1. `git worktree add --detach ../read-r2 dfc026e`.
2. Copies every earlier read result the work branch holds that the commit lacks
   (`research/data/nfl_coach/read_*.json`, `confirm_c4_look*.json`). The copies are byte for byte, and they
   go into one LOCAL commit that is never pushed, because every read refuses a dirty tree.
3. Re-checks every pinned file at that tree's HEAD.

`git diff --stat dfc026e HEAD` in the worktree must list only the copied earlier reads.

Then:

1. Restore the read's data **outside git or into gitignored paths**. Untracked, non-ignored files make the tree
   dirty and the read refuses. For the coaching reads this means the nflverse play-by-play directory (CC BY
   4.0, never committed), passed as the read's argument.
2. Run it: `cd ../read-r2 && VIGIL_NO_BG=1 python3 research/nfl_coach_r2_read.py H1 <nflverse dir>`. The
   arguments are in each read script's docstring.
3. Copy its output (`research/data/nfl_coach/read_*.json` or `confirm_c4_look*.json`) back to the work branch
   byte for byte, and commit it there. The commit message names the registration commit and the worktree
   HEAD the read recorded (`commit` in its output).
4. `git worktree remove ../read-r2`.

A read run from the LIVE tree after the engine fix refuses with "nfl_deep_sim.py differs from its registered
sha256". This is the safety net, and guard FT12 checks it on every registration.

## What the guards check now

The helper is `research/registered_pins.py`. The checks are in the coaching guard files, DA5 and
`tests/guards_auto_fumble_td.py` FT9-FT12.

- **At the commit.** The registration commit is in the clone; it is the commit that added the prereg; the
  prereg is unedited since; and every pinned file hashes to its pin there.
- **CI history.** CI checks out the whole history (`fetch-depth: 0` in `.github/workflows/guards.yml`). A
  shallow clone fails these checks, correctly.
- **Live.** A pinned file must still be its pinned bytes, with two named exceptions:
  - **Staged flips** (`nfl_deep_sim.py`, `nfl_dfs_engine.py`, `nfl_deep_data.py`) may be exactly the flipped
    bytes of `research/data/fumble_td_flip.patch` or `research/data/deep_anchor_flip.patch`. This applies only
    once `research/data/nfl_deep_weight_read.json` is tracked and triggered.
  - **Current guard files** (`tests/guards_auto_nfl_coach*.py`) may differ. Their registered copies are checked
    at the commit; the live copies are the current tree's checkers, edited for option (c) on 2026-11-28.
- **Frozen files.** Everything else stays frozen live, as before: the coaching engines, their tables, and the
  research scripts.
- **"Would run".** Each read's "would run on a clean tree" check is asked of its registration commit's tree
  (`registered_pins.registered_view`).
- **Loss.** FT10 shows that each check fails when the registered version is lost or altered.

## Guard changes (2026-11-28), and why

| File | Checks | Change |
|---|---|---|
| `tests/guards_auto_nfl_coach.py` | NC0, NC10, NC12 | NC0: the live engine files are the deep-weight read's bytes, or a staged flip's after that read. NC10: R1's pins at eaca95d, plus the live exceptions. NC12: "would run" asked of eaca95d's tree. |
| `tests/guards_auto_nfl_coach2.py` | NF7 | The confirmatory test's "would run" is asked of 33c49ab's tree. |
| `tests/guards_auto_nfl_coach_r2.py` .. `_r6.py`, `_r5_2027.py` | NR0/NQ0/N4-0/N5-0; NR0a, NR9b, NQ0a, NQ7b, N4-0a, N4-9b, N5-0a, N5-8b, N5A-3, N6-0, N6-10; NR10, NQ8, N4-10, N5-9, N5A-4, N6-11 | The same three changes for each registration. Each changed check's name says so ("option (c)"). |
| `tests/guards_auto_nfl_coach_evo.py` | EVO-8 | R1-R5's pins at their commits. |
| `tests/guards_auto_deep_anchor.py` | DA5 | Every registration at its commit; the live engine files through the same gate. |
| `tests/guards_conservation.py` | C2c | Version-aware. Engine version 1 keeps "a pick-six is the only defensive TD"; version 2 allows a lost fumble's TD too. |
| `tests/guards_viewer.py` | the continuity check | A return six's banner is its turnover's ("fum" for a fumble TD). |

The registered copies of the coaching guard files are untouched at their commits. Those copies are what each
read pins and runs.
