# Orchestrator review: NFL matchup data foundation

Independent of the Fable review (research/reviews/nfl_matchup_data.md). 2026-10-23 ~02:00 UTC.

Checked and AGREE:
- Guards: 97 passed, 0 failed (standalone). pyflakes clean on all four code files. Production
  modules import stdlib + the repo's own modules only (kalshi_nfl for name normalisation).
- The as-of mechanism (nfl_matchup_profiles.season_tallies / Snapshot / build): the cutoff season
  contributes games with week < cutoff only; prior seasons are whole, weighted DECAY**k, at most
  BACK = 2 back; the Snapshot constructor refuses any tally from the cutoff week or later, or from a
  later season. Sound by construction.
- Opponent adjustment is described as "allowed minus the offence's per-game average against every
  other defense, as of the cutoff" -- leave-one-out, as of the cutoff.

Findings:
- O1 (major, for the modelling agent): the quarterback the q_* columns describe is "Sleeper's QB1 for
  the team-week" (research/nfl_matchup_dataset.py ~l.740), i.e. chosen from Sleeper's
  post-week-revised projections for 2023-2025. If a revision encodes a late scratch or a benching,
  q_* inherits it. Whether the revisions contain any post-kickoff information is NOT established
  (the timestamps show a Tuesday re-save; the 96/249 changes vs the week-1 capture may be legitimate
  late-week news). The model must treat q_* (and sl_proj_*) on 2023-25 as possibly contaminated and
  decide shipping on the frozen-baseline 2026 rows, as its addendum C already requires.
- O2 (minor, disclosure): DECAY and the shrinkage k come from persistence/reliability measured on
  2018-2025, which includes the 2025 validation season. Harmless for the 2026 test; mildly optimistic
  for the 2025 validation. The model report should say so.
- O3 (minor): 2026 rows cover weeks 1-6 (week 7 not yet in nflverse); the test split is 2026 wk 1-6.
- O4 (note): the headline -- the measurable signal is target allocation vs coverage, not per-target
  efficiency vs coverage -- is supported by the reliability table (target-rate splits r 0.39-0.55 vs
  YPT/EPA splits ~0.02). AGREE, pending Fable's independent recomputation.

Verdict (orchestrator): AGREE TO SHIP the data foundation, with O1 and O2 carried into the model's
validation and report. Final status waits for Fable's review and reconciliation.

## Correction after the Fable review (2026-10-23 ~04:45 UTC)

My verdict above (AGREE TO SHIP) was WRONG, and I withdraw it. The Fable reviewer
(research/reviews/nfl_matchup_data.md) found defects my review missed:
- F1 (BLOCKER): traded players' per-dropback target rates divide all-team targets by new-team
  dropbacks. I confirmed it independently: 657 rows of the dataset read p_tgt_per_db_man > 1.0
  (e.g. Chase Claypool CHI 2023 week 1: 1.52 against his overall 0.24).
- F3: my statement "sound by construction" was true of nfl_matchup_profiles.build and FALSE of
  the dataset builder, which mutates one week's tallies game by game (a within-week leak, proven
  by the reviewer on 2024 week 10). I checked the mechanism, not the code path that used it.
- F2 (pre-2023 route vocabulary), F4 (wrong shrinkage constants applied), F5 (stale-download
  refresh), F6 (= my O1, QB label).
I agree with every one. Revised verdict: AGREE AFTER FIXES -- blocked until the builder's fixes
are re-checked by both reviewers. Lesson recorded: a leakage review must follow the path the
dataset actually takes, not only the function designed to prevent leakage.
