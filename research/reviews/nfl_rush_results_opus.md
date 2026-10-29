# Opus review: the rushing rule's results (validate, check2026, export)

2026-10-29, 03:02-03:13 UTC. This reviews the records the registered commands wrote under prereg.json 907f0bed (rule
92a99b8e, registered 02:13:45Z).

## Verdict: **AGREE.** The records are valid, and the null stands as computed.

There is no BLOCKER, MAJOR or MINOR finding, and two informational notes.

**What I did and did not touch.**
- I wrote nothing into `research/data/nfl_rush_model/`, re-ran no registered command there, and made no git writes.
- Every computation ran on scratch copies: the records were copied to a scratch folder, and the hold was lifted
  there by the same `prereg_status`.
- I read the validation frame's 2025 outcomes, which `validate` has already spent. I read no 2026 outcome.

## 1. The records' chain: valid

| record | sha256 | written | binds to |
|---|---|---|---|
| prereg.json | 907f0bed… | 02:13:45Z | rule 92a99b8e…; its 17 code shas equal the live files and the `prereg_code/` copies; the stored block hashes to 92a99b8e |
| validate.json | e8df86cb… | 03:01:09Z | `prereg_sha256` 92a99b8e, `prereg_utc` 02:13:45Z, `code` equal to prereg's (17), the same versions, `frame_val_sha256` = prereg's val frame (906e9a7e), `plumb` false, no forced re-run |
| check2026.json | b2583913… | 03:01:28Z | the same, plus `validate_sha256` = the sha256 of validate.json beside it |
| coefs.json | 0684544d… | 03:01:35Z | stage "provisional", `shipped` {}, `prereg_sha256` 92a99b8e; `records` name prereg.json 907f0bed, validate e8df86cb and check2026 b2583913, each equal to the file beside it |

- **Where 907f0bed is named.** validate and check2026 name the registration by its rule sha, its utc and its code (as
  `read_record` checks them). The prereg.json FILE sha (907f0bed) is named in coefs.json's records. That is the
  design, not a gap.
- **When they were written.** The three records are untracked. They postdate the registration push (ed23ebd,
  02:57:10Z) by four minutes, as the runbook orders: they go in a second commit.
- **`load_coefs` accepts the genuine file for the right reason.** `record_chain` finds no shipping status, so it asks
  for no chain. It accepts the file even alone in an empty folder.
- **Forged non-empty files are refused** (scratch copies):
  - a provisional group beside the genuine records: "its shipped groups are not the decision record's set" (check2026's
    set is empty);
  - a "shipped" status: "its records carry no prospective_sha256";
  - records naming other files: "check2026.json does not hash to records.check2026_sha256";
  - a provisional file alone: "prereg.json is not beside it".

  An entry with an unknown status loads but ships nothing: the status gate fails closed, by design.

## 2. Three d / SE pairs, recomputed: exact

**Method.**
- The models come from the registered functions, fit exactly as the walk fits them at those steps: `parts_for`
  (2023-24, with the registration's positions), `dispersion` and `fit_position`.
- Every earlier group in those markets was rejected, so each baseline is rung 1. The dead features equal prereg's
  `dead_features`, and the dispersion equals the record's.
- The scoring arithmetic is my own:
  - the 2025 rows, re-selected from the frame's raw `y_gp` / `y_pbp_play` columns;
  - the threshold log loss, clipped at 1e-6;
  - the per-row difference and its mean;
  - both clustered SEs (G/(G-1) times the clusters' centred sums squared, over n squared), and the larger of the two.

| step | record d / SE | mine d / SE | difference | d / SE | verdict |
|---|---|---|---|---|---|
| RB rush yards SHAPE | -0.0322686 / 0.0254455 (player; game 0.0215182) | the same | 3.5e-17 / 6.9e-18 | -1.27 | rejected: passes the floor (-0.004), fails -2 SE |
| QB rush yards SPD | +0.0278243 / 0.0195393 (game; player 0.0164522) | the same | 1.7e-17 / 3.5e-18 | +1.42 | rejected: worse |
| RB carries CFP | -8.33e-6 / 2.17e-5 (player; game 2.11e-5) | the same | 0 / 6.8e-21 | -0.38 | rejected: fails both |

The row sets are identical to the registered selection: 869, 456 and 869 rows, over 272, 262 and 272 games, with none
dropped.

## 3. The 2025 scoring rows: correct, in all 35 steps

| market | n | played universe rows (all) | dropped | thresholds |
|---|---|---|---|---|
| RB rush_att | 869 | 869 (871) | 0 | 5, 10, 15, 20 |
| RB rush_yd | 869 | 869 (871) | 0 | 10 ... 100 (8) |
| RB rec | 612 | 612 (612) | 0 | 1-5 |
| RB rec_yd | 612 | 612 (612) | 0 | 10 ... 50 (5) |
| RB td | 1,061 | 1,061 (1,063) | 0 | 1, 2 |
| QB rush_att | 456 | 456 (456) | 0 | 2, 4, 6, 8 |
| QB rush_yd | 456 | 456 (456) | 0 | 10 ... 60 (6) |
| QB td | 456 | 456 (456) | 0 | 1 |

- **Played rows.** Every step scores the 2025 played rows (`y_cond_played`: Sleeper's gp >= 1 or a pbp touch), in the
  market's universe. The two non-played RB rows appear only in the reported `all_rows`.
- **Thresholds.** Each step's thresholds are `nfl_rush.THRESHOLDS`.
- **The gate.** Re-applying `accept_2025` to every recorded d and SE gives the recorded verdict: all 35 are rejected.

## 4. check2026: as the rule says

- **Q5:** "the walk. Over the 2025-accepted groups, in ladder order, on 2026 weeks 1-6's frozen rows."
- **What the record shows.** With no group accepted, every market's walk is empty: 0 steps in all 8 markets, the
  provisional set empty, and no 2026 row scored.
- **The export** refits exactly the kept set, which is empty, so `shipped` is {}.

## Notes (informational)

1. **Committing coefs.json turns the board hook on, although it ships nothing.**
   - On the builder's week-7 replay (`<scratch>/nfl_rush_model/real/board_{on,off}.json`), all 9,463 priced lines are
     identical: p, the bands, value and labels.
   - The 101 RB and QB rows gain the rushing model's breakdown, role and opponent profile, and a note: "no group passed
     its test: the first model's market stands, unchanged".
   - The board grows from 7.31 to 8.15 MB (+11%), and each build pays a snapshot of about 1.7 s.
   - The prices and the label are right. Whether that cost is wanted on Render is the owner's call.
2. **The prospective test now has nothing to judge,** since no group is provisional. Run or not, nothing ships.

Work files are in `<scratch>/review_results_opus/`:
- `recompute.py` / `.json` / `.log`: the three pairs;
- `rows_check.py`: all 35 steps;
- `chain/`: the `load_coefs` checks;
- `records/`: the scratch copy.
