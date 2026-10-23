# NBA matchup engine — orchestrator's review (2026-10-23, independent of the Fable review)

Scope: nba_matchup.py, nba_matchup_data.py, nba_matchup_profiles.py, research/nba_matchup_*.py,
research/data/nba_matchup/, research/reports/nba_matchup.md, tests/guards_nba_matchup.py.
I re-ran the guards, re-hashed the pre-registered rule, checked the timeline against the scratch work files, followed
the path the scored rows actually took, and rebuilt the production board for 2026-10-23 through the serving layer.

## Verified (AGREE)

- **Pre-registration integrity.** `prereg_hash()` of the report's block = the recorded
  `b6f647c9…0d13`. The work files' times agree with the run log: params2 01:47:07, rule frozen 01:48:48,
  dispersion fit 02:09 (on the fit seasons, as the rule says), val.json 02:11:19, test.json 04:14:19. The
  dispersion metric was switched to the ladder log loss BEFORE the freeze, on an in-sample fit-season check only.
- **The as-of path the rows took.** `pass3` snapshots every player and both teams from the Book for a date BEFORE
  `add_date` adds that date's games (`W.walk` → `on_date`); production's `replay` does the same per date (`flush`).
  The validation and test arms mix over the minutes model's PROJECTED minutes (`Ladder.minutes` → `pred_minutes`),
  never the actual ones; actual minutes enter only as the offset when the rate coefficients are fitted on the fit
  seasons. No 2024-25 or 2025-26 row touches a fitted number.
- **The DK-average arm is the builder's rule.** Season-to-date DK points per game played (last season's when he has
  none), Normal with sd 0.55 × mean, scored on the same population as the model arm.
- **Guards** 45/45; pyflakes clean on every NBA file.
- **Calibration claim.** The decile tables in the report match `shipped_report.json`; the points under-confidence near
  the line (0.55 → 0.585) is stated, not hidden.
- **The market result is on the board.** `board()` puts the held-out Kalshi comparison (mid 0.542 vs model 0.556 on
  tight books; −14.3% ROI, 90% interval −24.9% to −3.5% on 485 bets) in the slate's top-level notes, which the tab
  now draws above everything.

## Findings

**O1 (major) — "Strong fit" lands on unfavourable matchups.** `player_payload` sets
`strong = has_rung and abs(matchup_mult - 1) >= 0.05` and passes it to EVERY line of the market. A defense that cuts a
player's projection by 8% therefore puts "Strong fit" on the YES lines of that market. `matchup_mult` is also the whole
rung 2–6 product, so a teammate's absence (rung 6, context, not a matchup) can earn the label by itself. Fix: compute
the multiplier from the DEFENSE terms the model kept (rungs 2–5 plus the defense's recent form), award the label only on
the side the shift favours, and emit `fit_side` on the line (contract updated).

**O2 (major) — a past date's board leaks.** `hit_all` / `hit_recent` read `hist_sorted` (every logged game, including
the date itself and later), and `board()` prices any date from the CURRENT book. Asked for last Tuesday, it shows
profiles and hit rates that saw Tuesday's games. The serving layer now builds only today and tomorrow (matchups.py,
guarded), but the function should not produce a leaking answer: filter the hit tables to games before the date, and
have `board()` refuse (or label) a date the book has already passed.

**O3 (minor) — provenance text.** `covers` prints "2025-27 season to 2026-10-22"; it means the 2025-26 and 2026-27
seasons.

**O4 (measurement, report wording) — rung 6's late-scratch exposure is larger than "a few rows".** The research reads
"absent" off the box (`pid not in played[s]`). Production counts only players ESPN lists "Out". I measured who the
absent regulars were in 2024-25 and 2025-26 from the raw summaries (`research/nba_absent_reasons.py`; a regular = 15+ minutes
in his team's previous game, missing from this one; 3,355 cases):

| box says | share | minutes-weighted |
|---|---|---|
| no box line at all (not with team, traded, G League, or simply omitted) | 57.1% | 59.4% |
| inactive with an injury / rest / personal reason | 24.7% | 26.0% |
| COACH'S DECISION (active, did not play: on no injury report) | 18.2% | 14.6% |

About one absence in seven (by minutes) is a coach's decision production can never see before the tip, and for most
of the rest the box cannot say when the absence was announced. The measured rung-6 gain (test z 10 for points) is
therefore an UPPER BOUND for production. The report should say so with these numbers. A descriptive sensitivity
(re-score the shipped model on 2025-26 with coach's-decision regulars counted as present) would size it; it changes
no decision.

**O5 (production) — payload size.** One NBA night is 18.8 MB of JSON (factors 5.4 MB, markets 4.3 MB), and
`board_isolated` returned it whole into the parent process. That is handled in the serving layer: matchups.py runs the
board in a niced child that publishes one piece per game (0.8–1.6 MB) and returns only the scanner's index (1.6 MB
JSON, ~9 MB in memory), and the tab fetches a game's piece when a player opens. 27 of a player's ~56 matrix rows are
weight-0 rungs; the tab folds them away behind a "measured but held at weight 0" toggle.

**O6 (scope) — the DFS hook has no caller.** `dfs_projections` is sound and tested, but Vigil has no NBA lineup
builder: the DFS tab's sport list has no NBA, and `simulate.dfs_build`'s generic path has no NBA roster positions. The
dk_fp distribution reaches the owner on the Matchups tab (market `dk_fp`, 10–60). Wiring it into a builder needs an NBA
classic/showdown builder first. That is a follow-up, not a hook to add now.

## Verdict

AGREE TO SHIP once O1–O3 are fixed (each with a guard that fails on the current code) and O4's wording is in the
report. O5 is done on my side; O6 is recorded as a limitation.
