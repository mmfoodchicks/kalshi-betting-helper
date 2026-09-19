# Task 8: the classic Millionaire field as it entered, fit-free

Artifact `research/data/cl_field.json` (module `research/cl_field.py`), built
on the owner's full standings export of the week-1 NFL $3.5M Fantasy
Football Millionaire (contest 193028206, 832,342 entries, $5, 150 max per
user, $1M to first, locked 2026-09-13 17:00 UTC) and the DraftKings draft
group 151307 captured after the game. The export is not committed (167 MB
against a repository Render clones on every deploy); it is pinned by sha256
in `research/data/dk_standings/manifest.json` and the module refuses to read
a file whose hash differs. No Sleeper input is used: nothing here is fitted
and nothing here needs a projection.

**On this week-1 contest, four of the six public-field targets and
benchmarks differ materially from the observed field; the realised sampler
comparison has not yet been run.** The public was sharper on the chalk than
the model's target (Jahmyr Gibbs in 42.8% of lineups against the 38%
target), spent more than the model's salary target ($49,850 against
$49,400, with half the field at exactly $50,000), almost never played a
defense against its own quarterback (0.5% against the 8% the model lets not
avoid it), and collided about eleven times more often than the rate the
model reports (one distinct pair of entries in 771,000 holding the same
lineup, against one in 8.3 million, on the constant's own definition). The
stack distribution and the bring-back rate, the two taken from a published
source or a stated assumption, were close. The constants are input targets
and benchmarks; what the sampler actually produces from them is a separate
measurement (section 4).

## 1. Reconciliation

| Check | Result |
|---|---|
| Rows against capacity | 832,342 parsed, 832,342 capacity: filled exactly; 832,342 distinct entry ids |
| Lineups | 1,314 blank rows (never set a lineup), 0 malformed; every other row is exactly QB, 2 RB, 3 WR, TE, FLEX, DST and every name resolves to one draft-group player (0 unresolved) |
| Ownership | DraftKings' %Drafted recomputed per roster position over all rows: max difference 0.005 pp across 1,088 (player, slot) rows |
| Points | every active entry's Points recomputed from the export's FPTS table: max difference 0.00003 on 831,028 entries |

DraftKings' ownership column is per roster position: a back at RB and the
same back at FLEX are two rows, so the export shows Gibbs at 38.58% (RB) and
4.17% (FLEX). The model's `own.max()` counts a player wherever he sits, so
the comparable number is 42.75% of all rows and 42.82% of active lineups.

## 2. The raw table

| Quantity | Value |
|---|---|
| Active lineups | 831,028 (773,891 distinct) |
| Most-copied lineup | 346 copies, 0.042% of the field |
| Entries in unique lineups | 89.8%; in lineups with 51 or more copies 0.51% |
| Concentration (plug-in HHI, sum of squared lineup shares) | 2.50e-6, effective lineups 400,044; its floor is 1.20e-6 (1/N) even if every lineup were unique, so it is a descriptive figure, not a collision probability |
| Distinct-entry collision (pairs of entries holding the same lineup over all pairs) | 1.30e-6, one pair in 771,367; the unbiased estimate of P(two random distinct entries hold the same lineup), the constant's definition |
| Most-owned player, any slot | Jahmyr Gibbs 42.82%; then Ja'Marr Chase 30.2%, Michael Mayer 27.5%, Chris Olave 24.8%, Saquon Barkley 21.1% |
| Players used | 665 of the 746 in the draft group |
| Stack (WR/TE teammates of the QB) | none 19.2%, one 53.0%, two 26.1%, three or more 1.7%; counting backs too: 14.9 / 46.0 / 33.4 / 5.7% |
| Bring-back (a WR or TE from the QB's opponent) | 32.2% of all entries, 34.5% of stacked entries |
| Defense against own QB | 0.50%; against one of the lineup's backs 2.45% |
| Salary used | mean $49,850, median $49,900; 49.9% at exactly $50,000, 94.7% at $49,500 or more, 0.6% at $48,000 or less |
| Players from one team (non-DST), max | 2: 52.8%, 3: 34.9%, 4: 5.3%, 5 or more: 0.5% |
| Players from one game, max | 3: 39.4%, 4: 21.3%, 5 or more: 8.3% |
| Punts under $3,000 | none 36.6%, one 48.4%, two 14.9% |
| FLEX position | RB 42.5%, WR 36.1%, TE 21.4% |
| Entries from 150-max users | 22.7% |

## 3. The targets and benchmarks, model beside real

Every "model" value is an input constant: a target the calibration aims at
(max ownership, mean salary), a rate the sampler draws from (stack
distribution, bring-back, defense against own QB) or a benchmark it reports
(collision). None is yet a statement about the field the sampler produces.

| Constant | Model (input) | Real | Reads |
|---|---|---|---|
| CL_FIELD_MAX_OWN | 0.38 | **0.428** | Gibbs, any slot. The calibration code's own note of 2026-09-10 ("every (beta, kappa) that spends $49,400 holds Gibbs at 42% or more, so 38% and $49,400 are not on the curve") is consistent with this: the public's observed Gibbs share and spend sit where the sampler could not reach both targets. Whether the curve itself is right is what the sampler run will show, not this row. |
| CL_SALARY_USED | $49,400 | **$49,850** | half the field spends every dollar; the target leaves $450 more on the table than the public does |
| CL_STACK_DIST | 17.4 / 49.0 / 28.9 / 4.7% | 19.2 / 53.0 / 26.1 / 1.7% | Levitan's published trend, close; the real field runs lighter on two- and three-stacks |
| CL_BRING_BACK | 0.35 | 0.32 | the "assumed, not published" number was within three points (the sampler applies it to every lineup, so the all-entries figure is the comparable one) |
| CL_DST_VS_OWN_QB | 0.08 | **0.005** | the public avoids its own quarterback's opponent almost universally; the constant is the share of the field the sampler lets not avoid it, and the realised holding share is that times the softmax's pick rate, so the sampler run is where the like-for-like number comes from |
| CL_FIELD_COLLISION | 1.2e-7 | **1.30e-6** | on the constant's own definition (P that two random distinct entries hold the same lineup, exactly the statistic the sampler's `_collision` reports for a sampled field): 10.8 times the reported rate, one pair in 771,367 against one in 8.3 million. The plug-in HHI (2.50e-6, 400,044 effective lineups) is the descriptive concentration figure and is not comparable: with 831,028 entries its floor is 1.20e-6 even when every lineup is unique, above the constant itself. |

## 4. What this means for the classic build, and what it does not

The two targets the calibration solves for (max ownership and mean salary)
both sit on the flatter, cheaper side of the public: the sampler was told
to aim below the public's chalk share and below its spend, and its own
tension at 2026-09-10 (a 42.9% Gibbs whenever it spent $49,400) sits where
the public's actual behaviour would. The collision benchmark is the same
finding the Showdown work made, on the corrected basis: the constant the
sampler reports is an order of magnitude below the real field's
distinct-entry collision. The defense-against-own-QB rate is one the public
follows almost universally that the sampler lets 8% of the field break.

None of this is yet a statement about the sampler's realised output. The
targets are inputs; the sampler's rejection and legality mechanics (the
per-row salary floors, the reserved defense, the drawn stack and
bring-back) can move the realised ownership, salary, stack, bring-back and
defense shares away from them, and a target the calibration cannot reach on
a pool is met by a least-combined-miss compromise rather than hit. That
comparison, the untouched sampler run on the pinned week-1 pool with the
constants exactly as served and its realised field put player by player
beside this one, is the second artifact.

Two of our own rules against this field: the team cap (at most three from
one team) excludes the 5.8% of the field that plays four or more, and the
punt rule (at most one under $3,000) excludes the 15% that plays two.
Recorded, not a reason to change a rule: those rules are strategic
exclusions, not descriptions of what the public never does.

This artifact fits nothing and changes nothing. The constants are not
edited here: whether a measured value from one week-1 contest replaces an
assumed one is the reviewer's and the owner's call, and the Showdown
lesson applies (a per-player ownership model needs a held-out test, not a
constant swap). One week-1 contest is not enough to establish transferable
replacement values; it is enough to say the assumptions deserve the test.
