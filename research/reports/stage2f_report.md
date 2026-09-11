# Stage 2F: the two simulators on the current slate

Commit 59e563b, base seed 20260912, 2026 week 1, 20,000 worlds a game, field 150,000, 60,000 candidate draws. Built 2026-09-11 18:00 UTC (4324.0s). Both models read the same slate, the same salaries and the same field model; only the point arrays differ.

## The board each model builds

| | legacy | constrained |
|---|---|---|
| slate entries | 243 | 243 |
| candidates | 66,666 | 66,666 |
| allowed | 52,749 | 53,135 |
| distinct hindsight-optimal lineups | 6,666 | 6,666 |
| strongest candidate, top 1% | 9.27% | 5.89% |
| strongest candidate, top 0.1% | 1.849% | 1.039% |
| strongest candidate, outright win | 0.0301% | 0.0151% |
| mean projection of the top fifty | 133.0 | 132.7 |
| players from one game, mean of the top 200 | 3.81 | 3.04 |
| share of the top 200 with five from one game | 0.215 | 0.010 |
| share with six | 0.060 | 0.000 |

Strongest under legacy: Joe Burrow, Jahmyr Gibbs, Jaylen Warren, Ja'Marr Chase, Tee Higgins, Devaughn Vele, Sam LaPorta, Rico Dowdle, Jets.

Strongest under constrained: Jared Goff, Ashton Jeanty, Derrick Henry, Amon-Ra St. Brown, Devaughn Vele, Drake London, Sam LaPorta, Tee Higgins, Jets.

## The two probe lineups under each model

| Probe | Model | top 1% | rank | top 0.1% | rank | outright win | expected payout | mean | p90 | p99 |
|---|---|---|---|---|---|---|---|---|---|---|
| L1 | legacy | 10.11% | #1 | 2.445% | #1 | 0.0451% | $482.63 | 134.3 | 174.3 | 211.9 |
| L1 | constrained | 4.39% | #50 | 0.617% | #128 | 0.0000% | $9.92 | 135.5 | 173.1 | 211.7 |
| L2 | legacy | 9.85% | #1 | 2.973% | #1 | 0.1803% | $1855.89 | 131.5 | 167.0 | 200.8 |
| L2 | constrained | 4.59% | #38 | 0.866% | #5 | 0.0075% | $86.56 | 132.5 | 168.2 | 204.4 |

## Parameter uncertainty

Each draw resamples every fitted parameter uniformly inside the wider of its profile half-width and the distance the second fit moved it under different common random numbers (the profile step was too coarse to resolve most intervals; Stage 2E's report says so), then rebuilds the whole board on a lighter grid (6,000 worlds, 60,000 field, 20,000 candidate draws). The top-1% figure is the comparable number across draws; the rank is against that draw's own smaller candidate pool, so it is not comparable with the full board's rank.

| Draw | probe L1 top 1% (rank) | probe L2 top 1% (rank) | strongest lineup |
|---|---|---|---|
| 0 | 4.498% (#16) | 4.72% (#8) | Joe Burrow, Bucky Irving, Omarion Hampton, Ja'Marr Chase... |
| 1 | 4.097% (#38) | 5.283% (#1) | Jared Goff, De'Von Achane, David Montgomery, Amon-Ra St. Brown... |
| 2 | 5.093% (#5) | 4.187% (#33) | Jalen Hurts, Rico Dowdle, Jahmyr Gibbs, DeVonta Smith... |
| 3 | 3.641% (#83) | 4.089% (#33) | Jalen Hurts, Derrick Henry, Breece Hall, DeVonta Smith... |
| 4 | 4.667% (#9) | 4.737% (#9) | Bryce Young, Bijan Robinson, Omarion Hampton, Jalen Coker... |
| 5 | 4.579% (#10) | 4.754% (#7) | Jalen Hurts, Ashton Jeanty, David Montgomery, Amon-Ra St. Brown... |

This report does not promote anything. The blind 2025 validation decides which model is better; this only says what the change would look like on the board the owner reads.
