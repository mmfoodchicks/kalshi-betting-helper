# S7, first artifact: the Showdown field as it actually entered — three contests, fit-free

**Artifact `research/data/s7_field.json`, provenance clean at `8ffe6f3`. Raw
files frozen by hash first (`research/data/dk_standings/manifest.json`).
Nothing here is fitted.** Every money number in the Showdown work rests on one
guess about the public — that lineup frequencies follow `softmax(beta ×
projection)` over the legal universe with the most popular lineup holding 0.2%
of entries — and this is the first time the guess has been put beside what
people actually played, on three contests the owner exported:

| | NE @ SEA, Wednesday Kickoff Millionaire | DEN @ KC, Monday Night Showdown | DET @ BUF, Thursday Night Showdown |
|---|---|---|---|
| Contest | 193391013, $20, $2.25M | 195526287, $20, $1.5M — **the S5 / A/B primary** | 195677825, $20, $1.5M |
| Entries / capacity | 126,020 / 132,352 (**did not fill**, overlay 6,332) | 88,235 / 88,235 | 88,235 / 88,235 |
| What was captured pre-lock | nothing: pool and roster post-game; the week-1 feeds were captured 2026-09-12, after this 09-10 game | **pool, contest and feeds, by S6 on 09-12** (game 09-14); roster post-game | nothing: all post-game |

Two caveats travel with every model column. Only DEN @ KC has a projection
column that is a real as-of-lock capture; Sleeper's projections are not
versioned, so the other two are not proven equal to what was served at lock.
And every roster capture is post-game, so for the model universe injury
statuses were **cleared** and the depth-chart rank rule kept; the gate's
post-game verdicts still show, and section 3 measures how much.

## 1. Reconciliation: all three checks pass on all three contests

| Check | NE @ SEA | DEN @ KC | DET @ BUF |
|---|---|---|---|
| Rows | 126,020 parsed; 220 empty lineups counted; the contest did not fill, and the ownership recomputation over the 126,020 rows reproduces DraftKings' percentages, so the rows are the field | 88,235 = capacity; 326 empty | 88,235 = capacity; 212 empty |
| Lineups | 125,800 resolve to 1 CPT + 5 FLEX and to the 68-player pool; 0 unresolved | 87,909 to the 55-player pool; 0 | 88,023 to the 47-player pool; 0 |
| Ownership (DraftKings' CPT/FLEX %Drafted recomputed from the raw lineups) | max diff 0.005 pp | 0.005 pp | 0.005 pp |

## 2. The raw field beside the placeholder model

Each cell is real / model.

| | NE @ SEA | DEN @ KC | DET @ BUF |
|---|---|---|---|
| Distinct lineups (real) | 18,620 | 18,539 | 14,746 |
| Most popular lineup, share of entries | **0.43%** (537 copies) / 0.20 | **0.36%** (313) / 0.20 | **1.09%** (962) / 0.20 |
| Effective number of lineups (1/Σp²) | **2,153** / 4,900 | **3,406** / 7,451 | **1,624** / 5,889 |
| Entries in lineups with 51+ copies | **33.6%** / 14.2 | **14.2%** / 4.9 | **23.1%** / 6.5 |
| Captain position QB / RB / WR / TE | 20 / 30 / **37** / 6 vs **38** / 18 / 30 / 8 | 28 / 24 / 33 / 8 vs **40** / 15 / 32 / 4 | 24 / 27 / 30 / 16 vs 14 / **49** / 15 / 14 |
| Team split 5-1 | **13.5** / 8.9 | **15.0** / 13.7 | **15.9** / 10.9 |
| No kicker or defense | 42.1 / 38.4 | 40.1 / 32.6 | **48.9** / 35.8 |
| Salary left, mean | **$1,288** / $3,221 | **$1,072** / $2,377 | **$627** / $1,612 |
| At least $1,000 left | 39% / **78%** | 36% / **69%** | 21% / **56%** |
| Entries from users with 150 entries | 23.3% | 26.5% | 24.0% |

## 3. The universe: who the model cannot roster at all

| | NE @ SEA | DEN @ KC | DET @ BUF |
|---|---|---|---|
| Entries outside the model's universe, as served | **52.5%** | 8.4% | 25.7% |
| …with the depth-chart gate lifted (only players Sleeper never projected) | 3.3% | 1.3% | **17.4%** |
| Who | **A.J. Brown, 49.8%** of entries: projected 16.8 by Sleeper, dropped by the gate on the post-game roster | Cyrus Allen 3.3%, Brashard Smith 2.3%, Nate Adkins 1.1%: RB3 / WR4 / TE3 the gate drops | **Frank Gore Jr., 13.3%: no projection**; Jackson Hawes 6.4% (TE3, gate); Greg Dortch 1.8% (no projection) |

Two different holes. The gate's share is a capture artifact on NE @ SEA (a
player in half the field was on the depth chart at lock, whatever a roster
read nine days later says) and a genuine rule difference on DEN @ KC (the
field plays RB3s and WR4s our rules keep out). Either way the field is not
bound by our gate, so a fit must lift it. The other hole cannot be lifted:
a player Sleeper never projected has no lineup in the model at any beta, and
on DET @ BUF that is a sixth of the field.

## 4. Observed frequency against model probability, lineup by lineup

| | NE @ SEA | DEN @ KC | DET @ BUF |
|---|---|---|---|
| Observed / model, the model's 10⁻³–10⁻² band (its chalk) | 0.48 | 0.46 | **0.18** |
| Observed / model, 10⁻⁴–10⁻³ | 0.55 | 0.61 | 0.46 |
| Observed / model, 10⁻⁵–10⁻⁴ | 0.46 | 1.10 | 0.94 |
| The field's most duplicated lineup: model rank | 1,823 | **2** | 1,911 |
| The model's most probable lineup: copies (share) | 136 (0.11%) | 56 (0.06%) | 20 (0.02%) |
| Rank correlation, observed copies vs model probability (matched lineups) | 0.53 (8,547) | 0.42 (15,293) | 0.30 (10,168) |

On the one board with a pre-lock capture the model's ordering is nearly right
at the very top: the real chalk (CPT Bo Nix; Dobbins, Waddle, Bryant,
Mahomes, Kelce) is its second-ranked lineup, and its first was played 56
times. On the two boards whose projections were captured after the game the
real chalk sits near model rank 1,900 — which is exactly the confound the
caveat names, and cannot be read as the model's fault or its excuse. On all
three the model's own densest bands are under-played by half or more.

## 5. The winning lineups, as diagnostic rows only

| | NE @ SEA | DEN @ KC | DET @ BUF |
|---|---|---|---|
| Lineup | CPT Smith-Njigba; Maye, Stevenson, Price, Seahawks, Hollins | CPT Kenneth Walker III; Mahomes, Rice, Kelce, Chiefs, Engram | CPT Josh Allen; St. Brown, Goff, Kincaid, Palmer, Knox |
| Points, entries tied at the top | 101.02, 23 | 124.61, 44 | 174.51, 17 |
| Structure, salary left | 3-3, $500 | **5-1**, $300 | 4-2, $100 |
| Model rank (= projection rank) | 1,239 | 16,209 | 19,809 |
| Admitted by our rules | yes | yes | **no**: one tight end per team (Kincaid and Knox); Palmer field-only |

Three observations, not targets. The DEN @ KC winner was a 5-1 that 44 people
played, admitted by every rule, ranked 16,209th of 777,056 by projection.

## 6. What this says, descriptively

This artifact fits nothing, so it settles nothing about beta. What it shows is
the shape of the miss, and on three boards the shape repeats:

- **The real field is two to three and a half times more concentrated than
  the model** (effective lineups 0.44, 0.46 and 0.28 of the model's; chalk
  2.1×, 1.8× and 5.5× the knob) — but not on the model's lineups: the model's
  top bands are under-played on all three.
- **The captain is chosen differently in kind.** Projection × 1.5 captains
  the top projection (the quarterback on both week-1 boards, the running back
  on DET @ BUF); the field spreads captains and captained wide receivers
  most on two of three.
- **The field spends the cap** (mean left $627–$1,288 against $1,612–$3,221)
  and plays 5-1 more often on all three (13.5–15.9% against 8.9–13.7%).
- **A share of every field is invisible to the model** — a sixth of DET @ BUF
  even with the gate lifted.

On DEN @ KC, the board every S5 number was read against, the model's own
chalk was nearly right and 8% of the field was outside its rules; the
concentration (0.36% against 0.20%) and the salary behaviour were not.

## 7. What is next

The cross-fit, leave-one-out over three contests: fit beta by the lineup-count
likelihood on the gate-lifted universe (entries with an unprojected player
held out as unreachable mass, and reported), evaluate on the other two, and
treat captain rates, structure, salary and duplication as out-of-objective
diagnostics. DEN @ KC is the one clean pre-lock board; the other two carry the
post-game-feed caveat into every projection-side number. If one beta cannot
transfer, that is the result, before any richer family. When S5 is rerun, the
comparison stays controlled: current source with the placeholder field
against current source with the calibrated field, same worlds, same universe,
never against the historical +0.047078. The classic Millionaire export
(832,342 entries) is pinned by hash for its own check (task #8).
