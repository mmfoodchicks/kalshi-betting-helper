# Week 2, held out: slot-allocation v1 against the untouched current family

Written as a template with blank cells BEFORE the week-2 field existed
(committed 2026-09-20, before the 17:00 UTC lock of draft group 153428), so
no metric can be added afterwards because we liked the answer. The harness
`research/cl_week2.py` fills the cells this template carries and refuses to
render if the numbers it computed and the cells this file asks for are not
the same set. The frozen candidate, its parameters, its code and production's
sampler are unchanged; nothing is refitted; production is untouched.

**Verdict. the slot-allocation and spend mechanisms transferred on this slate**

Read only as far as the co-primaries go. A positive result means the slot
allocation and the salary spending transferred to a new slate. It does not
validate chalk modelling, concentration, duplication or money EV, and the
week-1 in-sample run already showed why: the whole cross-entropy gain was
the FLEX group while the tight-end and defense slots and the chalk ordering
stayed where the current family had them.

## 1. The two co-primaries (the only pass/fail metrics)

Both arms on the same pre-lock pool at the real field's size, graded by the
frozen protocol's functions, on the frozen realisation seed 20260913.

| Co-primary | Current family | Slot-allocation v1 | Candidate minus current |
|---|---|---|---|
| Slot cross-entropy, total | 23.24369 (KL 4.02185) | 22.04207 (KL 2.82024) | -1.20162 |
| Salary Wasserstein-1 | $920.2 | $36.1 | $-884.1 |

The real field's slot entropy, the floor cross-entropy cannot go below, is
19.22183.

### Slot cross-entropy by roster-slot group

| Group | Current family | Slot-allocation v1 | Real entropy | Players with real mass |
|---|---|---|---|---|
| QB | 3.14042 (KL 0.31108) | 3.07809 (KL 0.24875) | 2.82934 | 26 |
| RB | 3.08047 (KL 0.37350) | 3.06776 (KL 0.36078) | 2.70697 | 59 |
| WR | 4.02134 (KL 0.31008) | 4.02036 (KL 0.30910) | 3.71126 | 91 |
| TE | 3.48750 (KL 0.66435) | 3.46196 (KL 0.63880) | 2.82316 | 54 |
| FLEX | 6.02223 (KL 1.83048) | 4.83045 (KL 0.63870) | 4.19175 | 203 |
| DST | 3.49172 (KL 0.53237) | 3.58345 (KL 0.62410) | 2.95935 | 26 |

### Salary used

| | Current family | Slot-allocation v1 | Real |
|---|---|---|---|
| Mean | $48,950.5 | $49,839.2 | $49,871.0 |
| p10 / p25 / median / p75 / p90 | $46,600 / $48,600 / $49,700 / $50,000 / $50,000 | $49,600 / $49,800 / $49,900 / $50,000 / $50,000 | $49,700 / $49,800 / $49,900 / $50,000 / $50,000 |
| At the cap / >= $49,500 / <= $48,000 | 25.45% / 60.93% / 19.26% | 44.30% / 93.04% / 0.09% | 49.76% / 95.60% / 0.20% |

## 2. The interpretation rules, fixed before the answer existed

The verdict above is mechanical from the two co-primaries on the primary
seed:

- candidate lower cross-entropy AND lower salary Wasserstein-1: the slot and
  spend mechanisms transferred on this slate;
- cross-entropy lower, Wasserstein-1 worse: the allocation transferred, the
  spend penalty did not;
- cross-entropy worse, Wasserstein-1 lower: the spend behaviour transferred,
  the week-1 slot allocation did not;
- both worse: the candidate failed to transfer.

An exact tie counts as not lower. There is no combined score, no winner
chosen on an out-of-objective metric, no rescue by reseeding, and no week-2
refit of any kind. The secondary seeds and the timing arm below never decide
the verdict.

## 3. The input, selected before lock and pinned

| | |
|---|---|
| Primary input | served_snapshot |
| Board built | 2026-09-19 22:15:08 UTC (1,124.9 minutes before lock) |
| Fetched (committed to the data branch) | 2026-09-20 04:51:49 UTC |
| Board sha256 | c858bf10a08c4c67b1e709e283906cb2a2fc24c23a5b945889002880d5f62a49 |
| Source commit or capture | f480566584d3 |
| Pool after the served-pool reader | 260 players |
| Frozen file sha256 | 62b83debdd0c |
| Candidate module sha256 | a8e3eba302fe |
| Production classic_sample sha256 | 987d30db002c |

Selection rule, as pre-registered: the served-board snapshot of dg 153428 on sim-history whose build stamp and data-branch commit both precede lock, latest build first, earliest commit on a tie; a post-lock commit is archival only; with no valid snapshot, the pool reconstructed from the latest pre-lock prospective capture

The receipt `research/data/cl_week2/week2_input_receipt.json` carries every
candidate snapshot and why each one lost. The post-game command refuses to
run at all if any of the three hashes above differs from the tree it runs
in, so a later change to production's sampler cannot silently rescore the
candidate.

## 4. Support: what the co-primaries condition on

Pre-registered definition, unchanged after seeing week 2: the co-primaries
condition on FULLY REPRESENTABLE entries. A lineup containing any player
outside the served pool is omitted, and no projection is invented for a
missing player.

| | |
|---|---|
| Contest rows (excluding the header) | 172,761 |
| Blank entries | 69 |
| Malformed lineups | 0 |
| Non-blank active entries | 172,692 |
| Fully representable entries (used by the co-primaries) | 171,257 |
| Excluded entries | 1,435 (0.831% of active) |
| Week 1 representable share, for comparison | 99.733% |

Unsupported players behind the exclusions (entries, share of active):
Elijah Sarratt (227, 0.13%); Jahdae Walker (117, 0.07%); Nico Collins (83, 0.05%); Drew Sample (75, 0.04%); Travis Hunter (75, 0.04%); Josh Cameron (60, 0.04%); Zay Flowers (58, 0.03%); Zavion Thomas (51, 0.03%); Hollywood Brown (42, 0.02%); Michael Pittman Jr. (41, 0.02%)

Roster-slot mass outside support: DST 0.000%; FLEX 0.258%; QB 0.027%; RB 0.034%; TE 0.053%; WR 0.152%

If coverage is materially worse than week 1, every reading above is
correspondingly weaker, and this section is where that is visible.

## 5. Stochastic sensitivity (never a validation)

The primary result is the frozen seed alone. These four seeds re-run both
arms through the same frozen functions so a small difference on the
co-primaries is not read as structural when it is field-sampler noise.

| Seed | Current CE | Candidate CE | Delta CE | Current W1 | Candidate W1 | Delta W1 |
|---|---|---|---|---|---|---|
| 20260914 | 23.30268 | 22.03793 | -1.26475 | $830.4 | $36.4 | $-793.9 |
| 20260915 | 23.26194 | 22.05356 | -1.20838 | $894.3 | $35.4 | $-858.9 |
| 20260916 | 23.32226 | 22.05825 | -1.26401 | $808.9 | $36.1 | $-772.7 |
| 20260917 | 23.40870 | 22.03837 | -1.37033 | $713.6 | $35.5 | $-678.0 |

Candidate minus current across the four secondary seeds, mean / SD / range:
cross-entropy -1.27687 / 0.06767 / [-1.37033, -1.20838]; salary Wasserstein-1 $-775.9 / $74.8 / [$-858.9, $-678.0].

The wrapper's own arms reproduce the frozen scorer's co-primaries exactly on
the primary seed: yes.

## 6. Input-timing sensitivity (never decides)

The early pre-lock capture, kept as a second input arm rather than thrown
away. reconstructed from the capture of 2026-09-20 00:47:24 UTC (16.21 h before lock), 260 players.

| | Current family | Slot-allocation v1 |
|---|---|---|
| Slot cross-entropy | 23.33896 | 22.04560 |
| Salary Wasserstein-1 | $794.8 | $35.8 |

Pool differences against the primary: only in the primary 0, only in the early capture 0, salary mismatches 0, projections differing by 1+ points 0 of 260 (max 0.5), depth tags differing 2, field-only flags differing 0; identical pools: False.

Representable entries on this pool: 171,257 (99.169%) against the primary's 171,257. When the
supports differ the two arms are not on identical universes, and that is
stated here rather than papered over by presenting the numbers as
like-for-like.

## 7. Out of objective (diagnostics only, never pass/fail)

Current family | Slot-allocation v1 (and Real where it applies).

| | Values |
|---|---|
| Top-20 MAE / bias (pp) | 9.184 / -8.604 | 8.883 / -7.858 |
| Top-50 Spearman | 0.3546 | 0.3982 |
| Top-10 / 20 / 50 overlap with the real top set | 4 / 9 / 33 | 4 / 9 / 31 |
| Real mass captured by the sampler's top 10 / 20 / 50 | 0.6089 (4 overlap) / 0.7165 (9 overlap) / 0.7877 (33 overlap) | 0.6089 (4 overlap) / 0.7080 (9 overlap) / 0.7777 (31 overlap) |
| FLEX is RB / WR / TE (%) | 36.21 / 63.79 / 0.00 | 55.85 / 35.19 / 8.95 | 50.75 / 32.55 / 16.70 |
| Punts under $3,000: 0 / 1 / 2+ (%) | 19.46 / 70.27 / 10.27 | 16.54 / 70.37 / 12.95 | 51.83 / 47.03 / 1.13 |
| Stacks 0 / 1 / 2 / 3+ (%) | 17.30 / 49.17 / 28.85 / 4.68 | 19.23 / 51.57 / 26.17 / 3.02 | 16.43 / 56.03 / 25.97 / 1.57 |
| Bring-back (%) | 34.98 | 32.15 | 33.31 |
| Defense against own QB / own back (%) | 0.411 / 13.428 | 0.411 / 13.507 | 0.200 / 2.453 |
| Unique-lineup share; most-copied | 99.96%; 3 | 99.99%; 2 | 96.29%; 150 |
| Distinct-entry collision | 4.842e-09 | 9.547e-10 | 4.206e-06 |
| Plug-in HHI; effective lineups | 5.844e-06; 171,115.1 | 5.840e-06; 171,229.0 | 1.005e-05; 99,551.5 |
| Duplicate buckets 1 / 2 / 3-5 / 6-10 / 11-50 / 51+ (entry share) | 99.92% / 0.07% / 0.01% / 0.00% / 0.00% / 0.00% | 99.98% / 0.02% / 0.00% / 0.00% / 0.00% / 0.00% | 94.29% / 2.97% / 1.46% / 0.46% / 0.43% / 0.39% |

## 8. Per-player ownership, the real top 50

| # | Player | Pos | Salary | Real % | Current family % | Slot-allocation v1 % |
|---|---|---|---|---|---|---|
| 1 | Bijan Robinson | RB | $8,200 | 47.29 | 39.93 | 39.42 |
| 2 | Derrick Henry | RB | $7,200 | 37.49 | 10.03 | 10.21 |
| 3 | Christian McCaffrey | RB | $8,000 | 22.81 | 26.04 | 25.36 |
| 4 | Dalton Schultz | TE | $3,200 | 22.55 | 2.57 | 2.65 |
| 5 | Aaron Jones Sr. | RB | $5,100 | 21.71 | 3.48 | 4.29 |
| 6 | CeeDee Lamb | WR | $7,300 | 19.01 | 16.74 | 19.85 |
| 7 | Justin Jefferson | WR | $7,800 | 18.87 | 17.31 | 20.38 |
| 8 | George Pickens | WR | $6,300 | 18.77 | 9.28 | 11.46 |
| 9 | Javonte Williams | RB | $6,400 | 17.24 | 9.30 | 10.71 |
| 10 | Jalen Coker | WR | $5,100 | 15.79 | 4.25 | 4.21 |
| 11 | Ashton Jeanty | RB | $6,800 | 15.16 | 9.29 | 9.97 |
| 12 | Matthew Golden | WR | $4,700 | 14.92 | 3.61 | 3.53 |
| 13 | Terry McLaurin | WR | $5,200 | 14.18 | 7.47 | 6.86 |
| 14 | Ja'Marr Chase | WR | $7,600 | 14.14 | 16.70 | 19.50 |
| 15 | Emeka Egbuka | WR | $6,400 | 13.55 | 5.37 | 5.15 |
| 16 | Buccaneers | DST | $3,600 | 13.48 | 2.85 | 2.25 |
| 17 | Michael Mayer | TE | $3,600 | 13.15 | 1.75 | 1.68 |
| 18 | Dak Prescott | QB | $6,400 | 13.06 | 6.46 | 8.98 |
| 19 | Bucky Irving | RB | $6,100 | 12.22 | 5.21 | 5.68 |
| 20 | Garrett Wilson | WR | $6,000 | 12.15 | 7.81 | 8.24 |
| 21 | Mark Andrews | TE | $4,400 | 12.06 | 7.24 | 6.30 |
| 22 | Christian Watson | WR | $6,200 | 11.50 | 8.22 | 8.97 |
| 23 | Colston Loveland | TE | $5,000 | 10.86 | 6.52 | 8.58 |
| 24 | Luther Burden III | WR | $5,700 | 10.62 | 6.13 | 6.65 |
| 25 | Saquon Barkley | RB | $7,000 | 10.62 | 11.09 | 11.78 |
| 26 | David Montgomery | RB | $6,500 | 10.31 | 9.37 | 10.68 |
| 27 | Jaxon Smith-Njigba | WR | $8,100 | 10.10 | 20.00 | 23.00 |
| 28 | 49ers | DST | $3,800 | 9.91 | 2.54 | 2.01 |
| 29 | Brock Purdy | QB | $6,200 | 9.47 | 6.24 | 6.78 |
| 30 | Carson Wentz | QB | $4,600 | 9.46 | 1.65 | 2.67 |
| 31 | Parker Washington | WR | $5,900 | 9.34 | 6.40 | 6.44 |
| 32 | Jaguars | DST | $2,400 | 9.27 | 4.45 | 4.89 |
| 33 | Devaughn Vele | WR | $4,200 | 9.21 | 2.18 | 1.87 |
| 34 | Stefon Diggs | WR | $5,300 | 9.21 | 6.64 | 6.04 |
| 35 | Breece Hall | RB | $6,200 | 9.13 | 5.83 | 6.52 |
| 36 | Caleb Williams | QB | $6,800 | 9.09 | 6.97 | 8.74 |
| 37 | Baker Mayfield | QB | $5,600 | 8.86 | 3.21 | 2.83 |
| 38 | Jayden Daniels | QB | $6,300 | 8.83 | 6.87 | 5.93 |
| 39 | Dallas Goedert | TE | $4,800 | 8.09 | 5.16 | 6.99 |
| 40 | Mike Evans | WR | $6,600 | 8.09 | 8.98 | 9.00 |
| 41 | Rashod Bateman | WR | $4,400 | 7.97 | 6.01 | 3.35 |
| 42 | Deebo Samuel Sr. | WR | $5,300 | 7.34 | 4.30 | 4.26 |
| 43 | Quentin Johnston | WR | $5,000 | 7.23 | 3.28 | 2.71 |
| 44 | DK Metcalf | WR | $5,200 | 6.97 | 5.97 | 5.02 |
| 45 | Eagles | DST | $3,700 | 6.10 | 2.72 | 2.13 |
| 46 | Ravens | DST | $3,300 | 5.78 | 2.66 | 2.29 |
| 47 | Xavier Hutchinson | WR | $3,500 | 5.72 | 2.54 | 1.32 |
| 48 | Tetairoa McMillan | WR | $6,100 | 5.71 | 6.95 | 7.47 |
| 49 | Bryce Young | QB | $5,400 | 5.66 | 2.61 | 2.43 |
| 50 | Trey McBride | TE | $6,900 | 5.62 | 17.73 | 15.47 |

Nothing in production changed. Nothing was refitted. The frozen file, the
candidate's producing functions and production's sampler were verified
identical to the freeze before a single lineup was drawn.
