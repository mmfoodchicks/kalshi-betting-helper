# NFL matchup model — orchestrator's review (2026-10-23, independent of the Fable review)

Scope: nfl_matchup.py, research/nfl_matchup_{frame,fit,kalshi,kfit}.py, research/data/nfl_matchup_model/,
research/reports/nfl_matchup_model.md, tests/guards_nfl_matchup_model.py.

## Verified (AGREE)

- **The hold and the order of events.** The validation and the test were held until the data foundation was
  agreed (research/reviews/nfl_matchup_data.md, RE-CHECK). My release message reached the modelling agent at
  2026-10-23T09:31:16.810Z; I read that timestamp from its own transcript, which the Fable reviewer could not see
  (its CANNOT VERIFY). The order after that: rule hashed 09:39:09Z (prereg.json), validate 09:40:00, test 09:40:50
  (test_lock.json, not forced).
- **The rule's hash.** sha256 over the block INCLUDING its markers = `4b7e792c…f051`, the recorded value.
  validate.json and test.json carry code hashes equal to prereg.json's `code_at_prereg` for all three files.
- **Guards** 63/63 and pyflakes clean on all six code files.
- **What ships.** coefs.json ships exactly `ptd: ["V"]` and `dfs_hook.ships: false`. With nothing defensive
  shipped, `strong_fit` never fires on the board, as the contract now requires.
- **The board, built for real.** I built 2026-10-25 (week 7 Sunday) through matchups.build_isolated with the agreed
  deep store and live Sleeper, ESPN and Kalshi feeds: 25 s, 254 players, 12 pieces of ~0.5 MB, 2,941 Kalshi-listed
  rungs. The slate notes state the result in plain words: nothing beyond Sleeper ships, and Kalshi beat the model
  on 2026 (0.456 vs 0.432; one-contract bets −7.0%, 95% −14.4% to +1.1%).

## Findings

**O1 (major) — zero-width bands made every +EV line "robust".** On that real board, 99% of lines carried
p_lo = p = p_hi, so all 786 +EV lines were also labelled positive_ev_robust. Fable confirmed it (F2:
`probs_with_band` returns lo = hi = p when se == 0, i.e. on every market without a shipped term, and on td and
dk_fp). Fixed on my side in prop_value: robust is refused on a band known to be zero-width (guarded). The engine
should still emit no band where nothing fitted carries uncertainty.

**O2 (major, presentation) — "+EV" on a model the market beat.** Kalshi's pre-kickoff price beat the model on every
2026 stat. matchups.mark_record now puts "market better on record" on every such +EV line, mutes its EV and ranks
it with the thin books (the integration review's F17/F18).

**O3 — the 30-point gap on Bryce Young's 10+ rushing yards.** Fable sized it (F3): about 6-8 points of the gap is the
model's low-threshold rushing shape (QB lines 20-30: 10+ forecast 0.798 vs hit 0.735), and the rest is Kalshi
disagreeing with Sleeper's mean (market median ~10 yards vs Sleeper's 23). The shape error is a real calibration
problem. Running backs are worst: on lines of 20-30, 10+ is forecast 0.857 and hit 0.671 (n 487), the same on 2026's
frozen rows. I AGREE with F3.

## Agreement with the Fable review

AGREE with F1-F7 and N1-N5:
- **F1 (family selection).** The fix must keep the TESTED behaviour (gamma, which also scored better out of sample),
  persist it, and disclose the rule's deviation. It must not silently re-run anything.
- **F3 (calibration).** Every 2026 week through week 7 has been read in review. A line-dependent dispersion should be
  pre-registered now and judged on weeks the review has not seen (2026 weeks 8+). Until it passes, the board should say
  on the slate that low-threshold rushing-yard probabilities run high.

## Verdict

AGREE AFTER FIXES: Fable's F1-F7 and N1-N5, O1's engine half and O3's slate note. O2 is done in the serving layer.
