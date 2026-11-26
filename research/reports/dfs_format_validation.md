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

## Update, same day: the owner's five standings exports

The owner sent five more DraftKings standings exports ("I can't go further back"). They were summarized
off the PC exactly as `dk_standings.intake` does (the same parse, contest detail and pool) and committed
as summaries only (`research/data/dk_fields/`); no entrant name is in any summary (checked against the
handles in each export). Each matches a board on the data branch, so seven contests now have an ACTUAL
finish (where the lineup's real points would have placed in the real standings; not a reconstruction
against the board's simulated field).

| format | wk | dg | contest | entries | cash line (top %) | top-1% list #1 finish (top %) | money list #1 | win list #1 | best of our listed lineups | cashed |
|---|---|---|---|---|---|---|---|---|---|---|
| classic 10 games | 6 | 154941 | $2.75M Millionaire | 158,904 | 23.6 | 28.8 | 37.8 | - | 28.8 | 0/7 |
| classic 10 games | 10 | 156416 | $2.5M Millionaire | 147,058 | 20.7 | 66.8 | 66.8 | 21.5 | 21.5 | 2/12 |
| classic 10 games | 11 | 156561 | $2.5M Millionaire | 147,058 | 20.7 | 6.0 | 6.0 | 6.0 | 0.85 | 8/11 |
| showdown | 6 | 154959 | $1M Sunday Night (DAL @ GB) | 78,431 | 22.0 | 63.7 | 7.2 | 63.7 | 7.2 | 2/9 |
| showdown | 6 | 154960 | $1.5M Monday Night (WAS @ SF) | 87,465 | 21.9 | 57.9 | 52.5 | 60.0 | 29.7 | 0/9 |
| showdown | 11 | 156781 | $1.11M Thursday Night | 65,359 | 21.6 | 77.4 | 48.8 | 86.1 | 45.6 | 0/9 |
| showdown | 11 | 156793 | $1.2M Monday Night | 70,588 | 21.4 | 73.8 | 26.7 | 73.8 | 5.0 | 2/9 |

"Cashed" counts the board's listed lineups (the first three of each list, one row per distinct roster per
list) that would have cashed. The week-12 Wednesday showdown (157072) settles after this read.

**Reading it.**

- **The unit is the contest, not the lineup.** A board's lineups share most of their players and
  one game's outcome, so 30 classic and 36 showdown lineup-rows are 3 and 4 observations.
- **Classic.** One great week (11: every list's first lineup in the top 6%, one listed lineup in the
  top 0.85%) and two misses (week 6 none cashed; week 10 two of twelve).
- **Showdown.** The money list's first lineup cashed in one of four contests (top 7.2% on DAL @ GB).
  The top-1% and win lists' first lineups finished in the bottom half in all four.
- **Taken at face value this runs AGAINST the small-slate hypothesis**: showdown's listed lineups
  cashed 4 of 36 times, below the 21-22% cash line a random entry would reach. But that is four
  contests, and one week-11 classic slate carries the classic side.
- **Nothing here is a verdict either way.** With entry-level outcomes this correlated, telling a
  real format difference from luck needs on the order of 20+ contests per format.
- **What would decide it.** Every future contest's standings export, dropped into `pc-standings/` on
  the PC (summarized there, exported to the data branch as `dkfield-<cid>.json`, read by this script).
  Plus the 2-6-game classics, which still have no settled board.
