# Stage 3A and 3C: the extreme tail and the duplicate count, measured

Commit 9b39e8c, base seed 20260910, the real Sunday main slate (243 players, 20,000 simulated worlds). Ground-truth field 3,000,000 lineups, the board's sample 300,000 (a prefix of the same draw), resolved on 400 worlds for 200 candidate lineups. Contest size 832,000. Built 2026-09-10 23:32 UTC (1296.8s).

## What a 300,000-lineup sample can and cannot resolve

- One sampled lineup stands for 2.8 entries of the contest; the smallest mass the sample can report is 3.33e-06, against the 1 in 832,000 that first place asks about. No smoothing can cross that: the sample has no draw there to smooth.
- Of the candidate lineups below, the true mass above sits under the sample's resolution in 0.01% of (candidate, world) pairs and the sample reports nobody above in 0.00% -- a candidate lineup is rarely that deep in the field, which is why the second table below asks the question at the field's own order statistics instead.

## The estimators against the truth, by decade of the true mass (candidate lineups)

| True mass | pairs | median | raw: median ratio | within 3x | says zero | Jeffreys: ratio | within 3x | fitted tail: ratio | within 3x | defined |
|---|---|---|---|---|---|---|---|---|---|---|
| 0.0001 | 79 | 1.64e-04 | 0.98 | 1.00 | 0.00 | 0.99 | 1.00 | 0.59 | 0.81 | 1.00 |
| 0.001 | 550 | 1.62e-03 | 1.00 | 1.00 | 0.00 | 1.00 | 1.00 | 0.82 | 1.00 | 1.00 |
| 1e-05 | 10 | too few | | | | | | | | |
| 1e-06 | 4 | too few | | | | | | | | |

### The same question asked directly: the score whose true mass above is exactly one in N

A candidate lineup rarely sits a millionth of the way into the field, so the table above cannot reach the decades the first-place column lives in. Here the score itself is taken from the big field's order statistics at each world, and the 300,000-lineup sample is asked what mass sits above it.

| True mass above | worlds | raw: median ratio | 10th-90th | within 3x | says zero | Jeffreys: ratio | within 3x | fitted tail: ratio | within 3x |
|---|---|---|---|---|---|---|---|---|---|
| 0.0001 | 400 | 0.97 | 0.77-1.20 | 1.00 | 0.00 | 0.99 | 1.00 | 0.48 | 0.76 |
| 0.001 | 400 | 1.00 | 0.92-1.07 | 1.00 | 0.00 | 1.00 | 1.00 | 0.78 | 1.00 |
| 1e-05 | 400 | 1.03 | 0.34-1.72 | 0.94 | 0.06 | 1.21 | 0.94 | 0.16 | 0.26 |
| 1e-06 | 399 | 5.00 | 5.00-5.00 | 0.00 | 0.83 | 2.50 | 0.82 | 0.03 | 0.10 |

### Threshold diagnostics for the fitted tail

| Quantile threshold | sample points above it | shape | scale | median ratio to the truth |
|---|---|---|---|---|
| 0.9 | 29,999 | -0.196 | 13.67 | 0.93 |
| 0.95 | 14,999 | -0.154 | 11.64 | 0.97 |
| 0.98 | 6,000 | -0.154 | 10.13 | 0.99 |
| 0.995 | 1,500 | -0.079 | 7.86 | - |

Importance sampling is not on this list. It needs the probability the field sampler assigns to a drawn lineup, and this sampler has none in closed form: the salary reserve and the cap-aware renormalisation make each pick's probability depend on the partial roster's accumulated salary, so a lineup's density is a sum over construction paths whose state is the set of players AND the money spent. That is the same sum Stage 3C set out to take by dynamic programming, and it is why 3C is measured instead.

## Stage 3C: how many entries are our exact lineup

The big field holds 2,968,427 distinct lineups among 3,000,000 draws (99.3% of them drawn once), the most popular 109 times. Collision probability (two random entries identical) 3.54e-07 on the big field, 3.35e-06 on the sample.

| Lineup | copies, 3,000,000 field | copies, 300,000 sample | copies if the nine picks were independent |
|---|---|---|---|
| (a popular build) | 30.2 | 19.4 | 6.5e+02 |
| (a popular build) | 28.8 | 27.7 | 2.2e+03 |
| (a popular build) | 24.4 | 27.7 | 1.9e+03 |
| (a popular build) | 23.6 | 13.9 | 6.5e+02 |
| (a popular build) | 19.4 | 22.2 | 2.2e+03 |
| (a popular build) | 18.3 | 11.1 | 1.2e+03 |
| (a popular build) | 17.2 | 13.9 | 1.2e+03 |
| (a popular build) | 10.0 | 11.1 | 5.3e+02 |
| (a popular build) | 8.9 | 13.9 | 1.8e+02 |
| (a popular build) | 8.3 | 13.9 | 2.2e+02 |
| (a popular build) | 6.1 | 13.9 | 3.7e+03 |
| (a popular build) | 5.5 | 13.9 | 2.2e+02 |
| (a popular build) | 5.3 | 11.1 | 1.1e+02 |
| (a popular build) | 5.3 | 11.1 | 2.1e+02 |
| (a popular build) | 5.3 | 11.1 | 1.5e+03 |

An exact dynamic programme over the sampler's state was the plan and is not possible: the state is the set of players chosen AND the salary spent so far (the reserve changes which players are affordable), so the paths do not collapse. The count above is a direct measurement instead, and the table says how far the 300,000-lineup estimate the board uses sits from it.
