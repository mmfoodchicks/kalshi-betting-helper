# S7, first artifact: the Showdown field as it actually entered — three contests, fit-free

**Artifact `research/data/s7_field.json`, provenance clean at `1cb64f4`. Raw
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
| Effective number of lineups (1/Σp², over submitted lineups) | **2,145** / 4,900 | **3,381** / 7,451 | **1,616** / 5,889 |
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
  the model** (effective lineups 0.44, 0.45 and 0.27 of the model's; chalk
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

## 7. Reconciliation with the independent S7 study

The reviewer ran the same three exports independently and stopped before
fitting. Every figure below was traced to the raw standings, not voted on.

| Quantity | Independent study | This artifact | Agreement |
|---|---|---|---|
| Rows / active lineups / unique lineups | 126,020 / 125,800 / 18,620; 88,235 / 87,909 / 18,539; 88,235 / 88,023 / 14,746 | identical | exact |
| Most duplicated lineup | 537 (0.426%), 313 (0.355%), 962 (1.090%) | identical | exact |
| Blank entries | 220, 326, 212 | identical, counted not dropped | exact |
| Ownership recomputed vs DraftKings' %Drafted | within 0.005 pp, denominator the whole field including blanks | within 0.005 pp, same denominator, stamped `"denominator": "all entries, including empty ones"` | exact; nothing was renormalised to active lineups |
| Points recomputed from the role-specific FPTS table | max discrepancy ≈ 0.00002 | max 0.00001 / 0.00001 / 0.00002, every active entry | exact (a first version of this check keyed the table by player and failed by tens of points; keyed by (player, slot) it agrees) |
| Effective number of lineups | 2,145 / 3,381 / 1,616 (over active lineups); ≈ 2,139 / 3,255 / 1,609 with a null state | first version 2,153 / 3,406 / 1,624; **corrected** to 2,145 / 3,381 / 1,616, null-state 2,139 / 3,255 / 1,609 | **the study was right**: the first version summed active-lineup frequencies divided by all rows, a sub-normalised vector, not an HHI; the study reproduced the error exactly by scaling its figure by (all / active)² = 1.0035, 1.0074, 1.0048. Corrected in the artifact (`effective_lineups.basis`), both variants reported; the concentration conclusion is unchanged (0.44 / 0.45 / 0.27 of the model's). Shares of the field (max lineup, top-100 mass, duplicates, ownership) keep DraftKings' denominator, all entries, and the artifact says so. |
| Top-100 lineups' share | 14.9 / 10.7 / 16.7% | 14.94 / 10.72 / 16.67% | exact |
| Team split 5-1 / 4-2 / 3-3 | 13.53 / 47.74 / 38.73; 14.96 / 46.81 / 38.22; 15.92 / 50.61 / 33.47 | identical | exact |
| Field failing any of the four recoverable rules | 16.76 / 16.62 / 17.92% | 16.75 / 16.61 / **18.09%** | NE and DEN exact; DET @ BUF 0.17 pp apart, all of it inside the tight-end rule (below) |
| CPT is a kicker or defense | 7.23 / 7.32 / 3.41% | 7.21 / 7.30 / 3.41% of all entries | exact (the study's denominator is active entries) |
| More than two K + DST | 1.19 / 2.28 / 0.75% | 1.19 / 2.27 / 0.74% | exact |
| Non-DST captain with the opposing defense | 8.26 / 7.18 / 5.90% | 8.46 / 7.37 / 5.96% | **definition**: production bars the opposing defense beside ANY non-DST captain, kickers included; restricted to offensive captains this artifact gives 8.25 / 7.15 / 5.89 of all entries, the study's figures once divided by active entries. The 0.2 pp are kicker captains with the opposing defense. |
| Top 1% failing any rule | 38.78 / 4.21 / 27.34% | 38.78 / 4.21 / **27.68%** | NE and DEN exact |
| Top 0.1% failing any rule | 60.53 / 0 / 40.25% | 60.53 / 0 / **41.51%**; every DET @ BUF failure is the tight-end rule (66 of 159 entries at rank ≤ 88, ties included); NE's are DST-OPP (71) and K + DST (30) | NE and DEN exact; DET @ BUF 1.3 pp apart |
| DET @ BUF winner breaks the tight-end rule | yes (Kincaid + Knox) | yes, and uses a field-only player | exact |
| Winners' duplicate rank among unique lineups | 1,121st / 205th / 1,006th | 1,121 / 205 / 1,006 (69, 15 and 69 lineups share each copy count, so the ranks are the top of a tie range) | exact |

The only residual is DET @ BUF's tight-end rule, 0.2–1.3 pp in the three
groups. This artifact takes positions from the DraftKings pool, so the
unprojected tight ends the field played (Jackson Hawes, 6.4% of entries;
Tyler Conklin) count toward "one tight end per team"; it also counts every
entry tied at the rank boundary (159 entries hold rank 88 or better on DET @
BUF). Either would move the study's figure toward this one; which one it is
needs the study's definition, and the raw file settles it either way.

Where the study went further than this artifact and this artifact agrees:
the four rules remove a sixth of every real field, not bizarre constructions
nobody enters; the tight-end rule deserves its own historical validation
rather than removal on one winner; the field's 5-1 share (13–16%) is far
below the top of the modelled ranking (18–19 of 20 in the A/B) **and** below
the served portfolio (10 of 20 five-one on DEN @ KC, from the A/B); the
winners are low-frequency constructions (205th to 1,121st most duplicated) and
a training target built on them would fail. Where this artifact went further
than the study: the placeholder model beside every quantity, the two universe
holes with their causes, and the DEN @ KC chalk at model rank 2.

## 8. What is next

The fit, under the fences the independent study set and this artifact
accepts:

- **Support coverage first.** Before any beta, each board's post-gate-lift
  support coverage is reported (already here: 96.7%, 98.7% and 82.6% of
  entries representable). The public-field universe is the full DK-legal
  projected pool, not our owner rules or depth strategy filters.
- **No substitutes for a missing projection.** An entry with a player Sleeper
  never projected is not repaired by lifting a gate. The likelihood is fitted
  conditional on representable support and labelled so, the excluded observed
  mass is reported beside it, and no final points, post-game projection, zero
  or invented floor stands in for a pre-game line. On DET @ BUF that excludes a
  sixth of the field and the report will say so first.
- **Counts only.** Beta is fitted to exact-lineup counts; captain mix,
  5-1 / 4-2 / 3-3, salary left and duplication are out-of-objective
  validation metrics. If one beta cannot transfer, that is recorded as a
  model-form failure before any parameter is added.
- **Leave-one-contest-out is a reconstruction sensitivity, not a clean
  historical validation.** Only DEN @ KC has genuinely captured pre-lock
  inputs; NE @ SEA and DET @ BUF carry post-game reconstruction caveats, so a
  transfer failure onto them may be the inputs rather than the family. DEN @
  KC is the highest-integrity board and is labelled so in every table.
- **Two 5-1 observations stay separate.** The public-field model underweights
  5-1 against the real fields (8.9–13.7% against 13.5–15.9%); our optimizer's
  top-ranked list is vastly more 5-1-heavy than the real field (18–19 of 20
  in the A/B; 10 of 20 in the served portfolio). Compatible, opposite in
  direction, and never collapsed into one statement. When S5 is rerun, the
comparison stays controlled: current source with the placeholder field
against current source with the calibrated field, same worlds, same universe,
never against the historical +0.047078. The classic Millionaire export
(832,342 entries) is pinned by hash for its own check (task #8).
