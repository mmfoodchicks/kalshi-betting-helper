# Stage 2F: the two simulators on the current slate

Commit 9b39e8c, base seed 20260912, 2026 week 1, 20,000 worlds a game, field 150,000, 60,000 candidate draws. Built 2026-09-10 23:59 UTC (4272.1s). Both models read the same slate, the same salaries and the same field model; only the point arrays differ.

## The board each model builds

| | legacy | constrained |
|---|---|---|
| slate entries | 243 | 243 |
| candidates | 66,666 | 66,666 |
| allowed | 52,655 | 53,205 |
| distinct hindsight-optimal lineups | 6,666 | 6,666 |
| strongest candidate, top 1% | 9.06% | 6.08% |
| strongest candidate, top 0.1% | 2.532% | 1.063% |
| strongest candidate, outright win | 0.1278% | 0.0001% |
| mean projection of the top fifty | 132.3 | 132.8 |
| players from one game, mean of the top 200 | 3.77 | 3.08 |
| share of the top 200 with five from one game | 0.175 | 0.010 |
| share with six | 0.025 | 0.000 |

Strongest under legacy: Geno Smith, Jonathan Taylor, Breece Hall, Zay Flowers, Garrett Wilson, Josh Downs, Tyler Warren, David Montgomery, Jaguars.

Strongest under constrained: Jared Goff, Bijan Robinson, Rico Dowdle, Amon-Ra St. Brown, Tetairoa McMillan, Garrett Wilson, Sam LaPorta, Tre Tucker, Eagles.

## The two probe lineups under each model

| Probe | Model | top 1% | rank | top 0.1% | rank | outright win | expected payout | mean | p90 | p99 |
|---|---|---|---|---|---|---|---|---|---|---|
| L1 | legacy | 8.78% | #3 | 2.363% | #3 | 0.0001% | $28.12 | 124.4 | 171.5 | 217.9 |
| L1 | constrained | 2.66% | #2182 | 0.329% | #2917 | 0.0000% | $4.84 | 125.4 | 165.8 | 204.1 |
| L2 | legacy | 1.45% | #16753 | 0.167% | #13327 | 0.0000% | $2.64 | 111.0 | 145.2 | 176.2 |
| L2 | constrained | 0.43% | #47991 | 0.030% | #46588 | 0.0000% | $1.47 | 111.8 | 143.5 | 174.6 |

## Parameter uncertainty

Each draw resamples every fitted parameter uniformly inside the wider of its profile half-width and the distance the second fit moved it under different common random numbers (the profile step was too coarse to resolve most intervals; Stage 2E's report says so), then rebuilds the whole board on a lighter grid (6,000 worlds, 60,000 field, 20,000 candidate draws). The top-1% figure is the comparable number across draws; the rank is against that draw's own smaller candidate pool, so it is not comparable with the full board's rank.

| Draw | probe L1 top 1% (rank) | probe L2 top 1% (rank) | strongest lineup |
|---|---|---|---|
| 0 | 2.843% (#539) | 0.375% (#16173) | Joe Burrow, Bijan Robinson, Jaylen Warren, Ja'Marr Chase... |
| 1 | 2.749% (#684) | 0.277% (#16815) | Jared Goff, Jeremiyah Love, David Montgomery, Amon-Ra St. Brown... |
| 2 | 2.326% (#1400) | 0.377% (#16176) | Jalen Hurts, David Montgomery, Jaylen Warren, DeVonta Smith... |
| 3 | 2.397% (#1203) | 0.291% (#16776) | Jalen Hurts, Breece Hall, Saquon Barkley, DeVonta Smith... |
| 4 | 2.06% (#2166) | 0.585% (#14340) | Geno Smith, Jonathan Taylor, Chase Brown, Zay Flowers... |
| 5 | 2.495% (#1033) | 0.407% (#15948) | Joe Burrow, Jahmyr Gibbs, Bijan Robinson, Ja'Marr Chase... |

This report does not promote anything. The blind 2025 validation decides which model is better; this only says what the change would look like on the board the owner reads.
