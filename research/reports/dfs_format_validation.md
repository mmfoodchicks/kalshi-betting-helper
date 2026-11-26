# Showdown vs Classic on real results: the first read (2026-11-26, backlog 2.1)

The hypothesis (the owner's): Vigil may do better on smaller slates, because a showdown's legal
lineups are enumerated exhaustively while a classic slate's must be searched. Script:
`research/dfs_format_validation.py` (re-runnable; reads the data branch's board exports). Rows:
28 graded boards, 8 not yet settled.

## What was measured

Every tournament board exported to `origin/sim-history` (`errors/tourney-<dg>.json`, the last board
built before each lock), graded on the slate's REAL DraftKings points (Sleeper's box scores through
`dfslog.actuals_nfl`, the production scorer). Per board:

- **best**: the best lineup that could have been entered on the real points.
  - Showdown: EXACT, by enumeration (every captain and every five-man flex under the cap, both
    teams present).
  - Classic: the lineup sheet's optimizer, under its rules: no TE at FLEX, no defense facing your
    own players.
- **Vigil**: the first lineup of each list (top_win, top_money, top_top1) as % of best.
- **baseline**: the projection-optimal lineup on the board's OWN projections and pool (mean points
  only), as % of best. This is the naive alternative the tournament objectives must beat. The
  comparison is paired by board.
- **real finish**: placement in DraftKings' real standings, where a summary exists.

## Results

| format | boards | weeks | baseline % of best | top_top1: first lineup minus baseline (pts of % best) | top_money: same | top_win: same | real field |
|---|---|---|---|---|---|---|---|
| showdown | 17 | 6-11 | 70.3 +/- 2.6 | -0.9 +/- 3.6 (beat 6/17) | +4.0 +/- 3.2 (beat 10/17) | -14.0 +/- 5.4 (beat 3/17) | 2 contests, 18 lineups: 2 cashed, best top 7.2%, median top 58% |
| classic 7-10 games | 4 | 6, 7, 10, 11 | 53.6 +/- 4.2 | +1.0 +/- 8.3 (2/4) | +1.1 +/- 7.5 (2/4) | -1.6 +/- 8.9 (1/3) | 1 contest, 7 lineups: 0 cashed, best top 28.8% |
| classic 11+ games | 7 | 1-5, 8, 9 | 47.1 +/- 4.3 | -0.4 +/- 4.2 (1/7) | +4.7 +/- 5.0 (1/2) | +5.3 +/- 5.9 (3/7) | none |
| classic 2-6 games | 0 | - | - | - | - | - | - |

Standard errors are across boards. "Beat" counts boards where the list's first lineup scored more
real points than the projection lineup.

## Reading it

1. **The small-slate hypothesis cannot be tested yet.** No 2-6-game classic has been played and
   graded. Thanksgiving's 3-game slate (156932) is the first, and it settles after today. The only
   comparison available is a 1-game showdown against 9-13-game main slates.
2. **Neither format shows Vigil's lists beating a plain projection optimizer on real points.** The
   showdown money list leans positive (+4.0 +/- 3.2, 1.2 SE). Every other cell sits inside its
   noise. The showdown "win" list's -14 is expected rather than a failure: it chases the variance
   that wins a GPP and gives up average points to do it.
3. **"% of best" is not comparable across formats.** A showdown's six-man best is far easier to
   approach (the projection lineup reaches 70%) than a nine-man main slate's (47-54%). Compare a
   format's Vigil-minus-baseline, never its raw % of best.
4. **The decisive measure, the finish in the real field, exists for 3 contests.**
   - These are the owner's standings exports summarized on the PC.
   - Since this commit the data-branch snapshot exports every summary the PC made
     (`dkfield-<cid>.json`), so the read grows with every export the owner drops.
   - A finish read off the real standings is an ACTUAL finish. A finish against the board's
     simulated field would be a RECONSTRUCTED one. This script reports actual finishes only.
5. **Confounds.**
   - Engine versions differ: classic weeks 1-5 were built on ENGINE 6, later boards on 9-10;
     showdowns on 6-7.
   - The exported board is the last one before lock, not necessarily the one the owner entered.
   - The top-3 lists overlap.
   - 17 and 11 boards are a diagnostic, not a verdict.

## What accumulates from here

- Every board is already exported before its lock. This script re-reads all of them, settled ones
  included.
- Real fields flow in through the new `dkfield-` export.
- The production look-back still groups by (sport, objective). Stamping each logged lineup with its
  format and slate size, and extending the hindsight record to showdowns, is the next step for the
  live tab.
- Acceptance, before any claim that Vigil does better on small slates: at least 10 graded
  2-6-game classic boards, and Vigil-minus-baseline there exceeding the main slates' by more than
  2 SE (paired by board, clustered by week). Separately, actual-finish data on at least 10 contests
  per format.
