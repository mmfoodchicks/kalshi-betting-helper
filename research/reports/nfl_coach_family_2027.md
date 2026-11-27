# The coaching reads on shared games: R5 on 2027, dependence and multiple comparisons

2026-11-27. On this date the owner, after reading R5, said: "Allow R5 to use the 2027 regular season rather
than waiting until January 2029. Freeze its implementation and acceptance criteria now. Account explicitly for
dependence and multiple comparisons across R1-R5. Preserve all original registrations and verdicts. Do not modify
R5 based on the shared evaluation games."

The amendment was registered before any 2027 game exists: `research/data/nfl_coach/prereg_r5_2027.json`, block
02d486e9. Its read is `research/nfl_coach_r5_read_2027.py`, its guards are `tests/guards_auto_nfl_coach_r5_2027.py`
(N5A-1 to N5A-7), and its registry entry is `coach_r5_2027`.

## A. What the amendment changes, and what it does not

- **`prereg_r5.json` is byte-identical** (block d20d0e13; guard N5A-1). Its 2028 read stands, now as a later
  REPLICATION on games no 2027 read sees. Each verdict is reported as its own rule gives it.
- **The 2027 window:** the 2027 regular season, weeks 1-18.
  - **Date:** read on or after 2028-01-13 06:00 UTC.
  - **Order:** R1's read first (on or after 2027-01-06). Then R2-H2's and R4's, both due 2028-01-13, in either
    order. Then this read, which refuses until R1's and R4's results are committed. The 2028 replication last
    (on or after 2029-01-16).
- **Criteria, statistics, the corner secondary and the 30-game floor** are copied verbatim from
  `prereg_r5.json` (N5A-2):
  1. R5's guards exit 0.
  2. The second unit's gain over R4 is above 2 SE.
  3. The hazard is not worse than R4's by more than 2 SE.
- **The implementation is frozen.** The amendment pins every R5 file that `prereg_r5.json` pins, plus the
  wrapper, its guard and this analysis, by sha256 (N5A-3).
- **No R5 file may change on the strength of a 2027 game.** A change is a new version, registered on games no
  read has seen.
- **The wrapper adds nothing.** It runs `nfl_coach_r5_read`'s frozen statistics, criteria and refusals against
  the amendment's block. It also refuses if the original registration's file no longer matches the hash the
  amendment recorded (N5A-4).
- **Power at 2027:**
  - **Expected size:** about 45 strategic pulled team-games.
  - **Under its own z > 2 rule:** about 0.91 at the development effect (+1.34 nats a pulled team-game, SD 2.68),
    and about 0.77 at the 30-game floor.
  - **At Holm's strictest step:** about 0.89, and about 0.73 at the floor.

## B. Every coaching read, by the games it reads

| read | games | statistic | its own error rule |
|---|---|---|---|
| R1 | 2026 weeks 13-18 | four criteria (clock, calls, late win probability, ...) | as registered |
| C4 | 2027 weeks 1-9 (only if R1 fails criterion 4 alone) | late win probability, two looks | owner-approved: R1 criterion 4 + C4 at family-wise one-sided 5% over every look and the eligibility |
| coaching DFS | 2026 weeks 13-18 | offence CRPS, guardrails | one-sided 0.025 |
| R2-H1 | 2026 weeks 13-18 | who got the ball | z > 2 |
| R3 | 2026 weeks 13-18 | the pull hazard, the starter's line | z > 2 |
| R4 | 2026 weeks 13-18 and 2027 | what happens after a pull | z > 2 |
| R2-H2 | 2027 | in-game adaptation of the call | z > 2 |
| **R5 (amendment)** | **2027** | the second unit after a strategic pull | z > 2 |
| R5 (original) | 2028 | the same, now a replication | z > 2 |
| C3-N (not coaching) | 2027 | the deep engine's season scale | its own |

The owner's 5% family-wise approval covers **R1's criterion 4 and C4 only**. No other read is in that family,
and nothing here changes it.

## C. The 2027 family and its correction (`research/nfl_coach_family_2027.py`, `research/data/nfl_coach/family_2027.json`)

**The family** is the three 2027 primaries: R2-H2's criterion 2, R4's criterion 2 and R5's criterion 2 (the
amendment). Each is registered at z > 2, one-sided 0.0228.

- **Uncorrected, if independent:** the chance at least one passes with no effect is 6.7%.
- **The proposal:** Holm at a family-wise one-sided 5%. Order the three by z. Test the largest against 2.128,
  the next against 1.960, the last against 1.645, and stop at the first that fails. Holm holds under any
  dependence.
- **How it is reported:** every original verdict stands as its own rule gives it. Beside each, the report says
  whether Holm's step-down confirms it as a family. A test can pass its own z > 2 and still not be
  family-confirmed if it is the largest and sits between 2.000 and 2.128.

**Power each test keeps** (Phi(effect/SE - z), each at its registered effect):

| test | effect / SE | own rule (z 2) | Holm strictest (2.128) | Holm last (1.645) |
|---|---|---|---|---|
| R2-H2 at the development mean (0.042) | 3.23 | 0.89 | 0.86 | 0.94 |
| R2-H2 at the 2025-26 level (0.025) | 1.92 | 0.47 | 0.42 | 0.61 |
| R4 (low end of its registered z) | 3.20 | 0.88 | 0.86 | 0.94 |
| R5, 2027, ~45 strategic pulls | 3.35 | 0.91 | 0.89 | 0.96 |
| R5, the 30-game floor | 2.74 | 0.77 | 0.73 | 0.86 |

The correction costs each test 2-5 points of power at its strictest step and nothing at its last. R2-H2 at the
recent level is under-powered with or without it.

## D. How correlated the shared-games statistics are

Each test's per-unit statistic was computed on the SAME development games with its frozen tables (2025;
2026 weeks 1-12). For tests over units SA and SB, the correlation of their means is
n_shared / sqrt(nA x nB) x rho, where rho is the per-unit correlation on the shared units.

| pair | 2025: shared / nA / nB, rho (SE), corr of means | 2026 w1-12 |
|---|---|---|
| R2-H2 x R4 | 79 / 431 / 102, -0.25 (0.12), **-0.10** | 33 / 229 / 48, +0.16 (0.18), **+0.05** |
| R2-H2 x R5 | 41 / 431 / 57, +0.09 (0.16), **+0.02** | 20 / 229 / 27, -0.21 (0.24), **-0.05** |
| R4 x R5 | 57 / 102 / 57, +0.29 (0.14), **+0.22** | 27 / 48 / 27, +0.11 (0.20), **+0.08** |

- **R4 and R5 nest.** Every strategic pulled team-game is a pulled one, and their statistics correlate modestly
  and positively (+0.1 to +0.2).
- **R2-H2 reads every team-game,** so its overlap with the pull tests is a small share of its units, and the
  correlation is near 0.

**Plainly:** these tests share games. A joint pass is not two independent confirmations, and a defect common to
R3's pull or R4's labels (R4's report, H.2) would move R4 and R5 together. A pass on shared games is weaker
evidence than the same pass on independent games. That is why the 2028 replication is kept: it is the
independent confirmation of the 2027 result.

## E. Files and guards

| file | what |
|---|---|
| `research/data/nfl_coach/prereg_r5_2027.json` | the amendment (block 02d486e9) |
| `research/nfl_coach_r5_prereg_2027.py` | writes it once |
| `research/nfl_coach_r5_read_2027.py` | the 2027 read (not run): R5's frozen read on the amendment's block |
| `research/nfl_coach_family_2027.py`, `research/data/nfl_coach/family_2027.json` | C and D |
| `tests/guards_auto_nfl_coach_r5_2027.py` | 7 guards, section N5A (AUTO) |
