# S5: the shadow-forbidden rule study

**Baseline** `96bf08b` (the post-audit Showdown tree). **Research only.** No
production rule was changed, no strategy default moved, the money gate stayed
shut throughout, and no S5 module computes a payout column at all — an AST guard
asserts that rather than trusting it.

**The question**, stricter than "which rule rejects the most lineups": *which
hard owner rule, if any, excludes lineups that remain strong when selected on one
set of worlds and judged on another?*

**The answer, in one paragraph.** Four of nine rules are provably inert — they
admit nothing when switched off alone, because another rule rejects everything
they reject. Of the rest, exactly one moves the production objective:
`DST-OPP`, the ban on rostering the defense that faces your captain, worth
**+0.047** mean held-out Top 1% and stable in 7 of 8 cross-fit cells. It survives
a field-concentration sweep, which S4's headline effect did not. But the
football-only evidence runs the other way — every ablation that raises Top 1%
*lowers* the selected portfolio's 99th-percentile DK score — and the lineups
driving the gain are captain-opposite-defense builds whose value depends on
exactly the cross-side covariance Stage 2B–2F measured legacy-latent getting
wrong. **Recommendation: keep every rule.** `DST-OPP` is recorded as the one
genuine candidate, INCONCLUSIVE rather than harmful, with the specific experiment
that would settle it named in section O.

---

## A. The production rule inventory, read off the source

Nine rules, taken from `nfl_dfs._sd_allowed`, the two captain gates in
`dfs_tourney.build_nfl_showdown`, and the depth gate — not from the earlier
audit's prose, which was not re-verified. `structural` means the rule bans a
*shape* and cannot be relaxed by moving a number; a threshold rule has a
constant that could be retuned instead of removed. `slate-dependent` means its
behaviour changes with the pool, which matters because a rule that switches
itself off on some boards cannot be audited from one board.

| rule | kind | shape | slate-dependent | predicate |
|---|---|---|---|---|
| `CPT-POOL` | entry pool | structural | yes | captain is a `_field_only` player (depth-gate dropped, kept for the field) |
| `CPT-POS` | owner strategy | structural | no | captain pos not in `_SD_GPP_CPT_POS` = {QB, RB, WR, TE} |
| `CPT-SALARY` | owner strategy | threshold | yes | captain `cpt_salary` < `_SD_MIN_CPT_SALARY` = 11000, applied only when the pool offers ≥ 3 eligible captains at that price |
| `FLEX-POOL` | entry pool | structural | yes | any flex is a `_field_only` player |
| `DST-OPP` | owner strategy | structural | no | a flex DST whose team differs from the captain's, when the captain is not himself a DST |
| `MAX-KDST` | owner strategy | threshold | no | count of K or DST over all six slots ≥ 3 |
| `MAX-PUNT` | owner strategy | threshold | no | count of slots whose **flex** salary ≤ 2000 is ≥ 3 (the captain counts at his flex salary, not his captain price) |
| `PUNT-ROLE` | owner strategy | structural | yes | a **flex** at salary ≤ 2000 whose depth tag is set and is not in `_SD_PUNT_ROLES`; the captain slot is not checked by this rule in production |
| `MAX-TE-TEAM` | owner strategy | threshold | no | some team supplies ≥ 2 tight ends across all six slots |

Two details the earlier prose did not carry, both found by reading the code and
both load-bearing for the classifier: `MAX-PUNT` counts the captain at his
**flex** salary rather than his captain price, and `PUNT-ROLE` is applied to flex
slots **only**.

## B. DK legality versus owner strategy versus entry pool

Three categories, and keeping them apart is the point of S5.0.

**DK legality** — enforced inside `enumerate_showdown`: one captain and five
distinct flex, salary under the cap with the captain at his captain price, at
least two teams. Never studied, never ablated. A lineup breaking one of these
cannot be submitted at all, so "admitting" it would be a recommendation to enter
a lineup DraftKings rejects. A guard asserts every enumerated lineup satisfies
all three.

**Entry pool** — `CPT-POOL` and `FLEX-POOL`, the depth-chart gate. This is an
owner heuristic and the code says so outright: *"The depth gate is OUR rule, not
the public's: a WR4 with a real projection is in thousands of their lineups, so
he stays in the field's pool (never in ours)."* So S5 studies it. But it is a
**pool** rule rather than a lineup rule — the only one that changes which players
exist rather than which combinations are allowed.

**Owner strategy** — the other seven. These are what S5 is about.

One scope limit stated plainly: the enumerated universe is built over
`ents + extra`, which is the DK pool *after* players with no Sleeper projection
were dropped (they cannot be simulated) and after the depth gate put the
projectable drops back as field-only. Players dropped for other reasons are not
in the enumeration at all, so "full DK-legal" in section J means *legal over the
simulable pool*, not over DraftKings' entire player list. That is a real
limitation and section Q records it.

## C. The classifier, and the gate S5 was not allowed past

Production answers one boolean and stops at the first failure, so it can never
say *why* a lineup is forbidden or what else was also wrong with it. S5 needs the
reason set. So `research/s5_rules.reason_masks` evaluates all nine rules
independently, and the stage was gated on one requirement:

> `shadow_reasons(lineup)` is empty **iff** production says the lineup is
> enterable — over the **entire** legal universe, not a sample.

| board | pool players | legal lineups | allowed | survival | mismatches |
|---|---|---|---|---|---|
| DEN @ KC (`153086`) | 24 (4 field-only) | 777,056 | 23,820 | 3.07% | **0** |
| DAL @ NYG (`153085`) | 23 (3 field-only) | 583,082 | 22,440 | 3.85% | **0** |

The count rules are evaluated as set predicates over all six slots, which is
equivalent to production's cumulative form — production fails the *k*-th
qualifying player when *k−1* already sit in the lineup, so it rejects exactly the
lineups whose total exceeds the max. That equivalence is argued here and
**proved** above; the proof is what makes the argument admissible.

A guard repeats the proof on a synthetic pool built so all nine rules fire, so a
future change to `_sd_allowed` that the classifier does not follow fails the
suite rather than silently re-basing the attribution.

## D–E. Counts are not cost

| rule | all-hits (DEN @ KC) | exclusive hits | marginal admissions |
|---|---|---|---|
| `CPT-SALARY` | 556,515 | 37,077 | 37,077 |
| `FLEX-POOL` | 495,325 | 47,346 | 47,346 |
| `PUNT-ROLE` | 408,566 | 20,705 | 20,705 |
| `DST-OPP` | 159,492 | 9,819 | 9,819 |
| `CPT-POS` | 131,321 | **0** | **0** |
| `CPT-POOL` | 129,413 | **0** | **0** |
| `MAX-TE-TEAM` | 83,158 | **0** | **0** |
| `MAX-KDST` | 28,498 | 546 | 546 |
| `MAX-PUNT` | 7,980 | **0** | **0** |

DAL @ NYG has the same shape: the same four rules at zero, `CPT-SALARY` 37,002,
`FLEX-POOL` 27,011, `PUNT-ROLE` 19,851, `DST-OPP` 9,656, `MAX-KDST` 537.

**"No kicker or defense as captain" rejects 131,321 lineups and admits nothing.**
Every one of them also fails the captain salary floor, which is mechanical once
seen: a kicker never costs $11,000 at captain price. So a report saying that rule
"removes 131,321 lineups" tells you nothing about what happens if you turn it
off — the answer is *nothing happens*.

**Exclusive hits equal marginal admissions, by definition.** A lineup becomes
allowed when rule R is switched off exactly when R was its only violation. The
two columns above are the same set. They are both printed because the spec asked
for both and because a reader is entitled to see the identity rather than take it
on trust — and because they stop being equal the moment two rules come off
together, which is section K. A guard asserts the identity on synthetic data and
on both live boards.

Violations per lineup, DEN @ KC:

| rules violated | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|---|---|---|
| lineups | 23,820 | 115,493 | 234,313 | 233,121 | 137,697 | 29,726 | 2,834 | 52 |

Most forbidden lineups fail two or three rules at once, which is why
rule-order attribution would have been meaningless: assigning each lineup to its
"first" failed rule would have produced a ranking of the order the code happens
to check in.

## F. The rule-overlap matrix, and which rules are redundant

The zero-marginal rules, and what subsumes them:

| rule | board | all-hits | fully subsumed by |
|---|---|---|---|
| `CPT-POOL` | `153086` | 129,413 | `CPT-SALARY` |
| `CPT-POS` | `153086` | 131,321 | `CPT-SALARY` |
| `MAX-PUNT` | `153086` | 7,980 | `PUNT-ROLE` |
| `MAX-TE-TEAM` | `153086` | 83,158 | (no single rule — the union of others) |
| `CPT-POOL` | `153085` | 77,758 | `CPT-SALARY` |
| `CPT-POS` | `153085` | 103,208 | `CPT-SALARY` |
| `MAX-PUNT` | `153085` | 24,282 | `PUNT-ROLE` |
| `MAX-TE-TEAM` | `153085` | 68,586 | (no single rule) |

"Subsumed" means **100%** of that rule's hits on that board are also hits of the
named rule, on both boards independently. This is redundancy, not harmlessness:
`CPT-POS` cannot be shown to protect anything *while `CPT-SALARY` stands*, and
that conditional matters — section K shows removing both together is a different
question with a different answer.

## G. Cross-fit design

Inherited from S4 unchanged: 8,000 simulated worlds per board, first 4,000 = fold
A, second 4,000 = fold B, disjoint; both directions; two boards; two seeds
(`20260912`, `771033`); 2 × 2 × 2 = **8 cells**. Candidate enumeration and rule
classification are world-independent and need no cross-fit; the shortlist and
the greedy do.

Selection objective is **Top 1% at the optimistic tie convention**, i.e. what
production actually serves. Shortlist 1,200 candidates — production's size, and
identical across every universe so the comparison is not confounded by the
shortlist parameter. Greedy cover to 20 entries. Paired bootstrap over held-out
worlds, 2,000 resamples, seed 11.

Memory: the screen is one pass over the whole universe returning **scalars**. The
tempting (lineups × worlds) matrix is 777,056 × 4,000 float32 = 12.4 GB and is
not needed. An earlier stage asked for 43 GiB the same way, which is why
`s5_ablate.screen` exists rather than a one-liner. A guard proves the scoring
fold cannot reach the screen: overwrite every scoring-fold world with garbage and
the selection screen comes back byte-identical.

## H. The shadow-forbidden frontier

Does any forbidden lineup beat the allowed frontier out of sample?

| cell | best allowed held-out Top 1% | forbidden top-20 beating it | on Top 0.1% | on football p99 |
|---|---|---|---|---|
| 086/0912/A→B | 0.08366 | 2/20 | 1/20 | 2/20 |
| 086/0912/B→A | 0.08113 | 7/20 | 4/20 | 0/20 |
| 086/1033/A→B | 0.07501 | 3/20 | 3/20 | 0/20 |
| 086/1033/B→A | 0.08236 | 3/20 | 3/20 | 0/20 |
| 085/0912/A→B | 0.11562 | 1/20 | 0/20 | 0/20 |
| 085/0912/B→A | 0.12347 | 1/20 | 0/20 | 0/20 |
| 085/1033/A→B | 0.12193 | 1/20 | 1/20 | 0/20 |
| 085/1033/B→A | 0.12619 | 1/20 | 1/20 | 1/20 |

Yes — but rarely, and on football p99 almost never (3 of 160 across all cells).
The shape of the best forbidden lineups is the finding. DEN @ KC, seed 1, A→B:

| captain | flex | salary left | split | K/DST | violates | held-out Top 1% | held-out p99 |
|---|---|---|---|---|---|---|---|
| **Chiefs (DST)** | Dobbins, Walker, Mahomes, Rice, Kelce | $400 | 5-1 | 0/1 | `CPT-POS`, `CPT-SALARY` | 0.09753 | **120.8** |
| Bo Nix (QB) | **Chiefs**, Sutton, Waddle, Walker, Bryant | $100 | 4-2 | 0/1 | `DST-OPP` | 0.08061 | 143.2 |
| **Chiefs (DST)** | Broncos, Waddle, Walker, Mahomes, Rice | $0 | 4-2 | 0/2 | `CPT-POS`, `CPT-SALARY` | 0.08700 | **117.3** |
| Rashee Rice (WR) | Broncos, **Chiefs**, Dobbins, Walker, Mahomes | $100 | 4-2 | 0/2 | `DST-OPP` | 0.07129 | 118.9 |

Against a best-allowed held-out Top 1% of 0.08366 and allowed portfolios
averaging p99 **137.1** on this board. The forbidden lineups that win Top 1%
score *far worse* in the raw football tail. They rank high because the field does
not own them, not because they put up points — and that is the thread the rest of
the report pulls.

## I. One-rule ablations: Δ held-out Top 1%

Each cell: shortlist and greedily build 20 entries over allowed ∪ marginal(R) on
the selection fold, grade on the held-out fold against the baseline portfolio
built the same way over allowed alone. **Bold** = sign-stable at both exact tie
conventions.

| cell | baseline | `CPT-SALARY` | `DST-OPP` | `FLEX-POOL` | `MAX-KDST` | `PUNT-ROLE` | full legal |
|---|---|---|---|---|---|---|---|
| 086/0912/A→B | 0.60997 | **+0.01092** | **+0.08598** | +0.00000 | +0.00000 | +0.00000 | **+0.10394** |
| 086/0912/B→A | 0.61075 | **+0.00956** | **+0.09277** | +0.00000 | +0.00000 | +0.00000 | **+0.09536** |
| 086/1033/A→B | 0.60118 | +0.00816 | **+0.08695** | +0.00000 | +0.00000 | +0.00000 | **+0.10571** |
| 086/1033/B→A | 0.60945 | +0.00474 | **+0.07754** | +0.00000 | +0.00000 | +0.00000 | **+0.09591** |
| 085/0912/A→B | 0.73543 | +0.00000 | **+0.00906** | +0.00000 | +0.00000 | +0.00000 | **+0.01669** |
| 085/0912/B→A | 0.72246 | +0.00256 | **+0.01304** | +0.00000 | +0.00000 | −0.00389 | **+0.02664** |
| 085/1033/A→B | 0.74350 | −0.00832 | +0.00119 | +0.00000 | +0.00000 | **−0.01133** | **+0.01567** |
| 085/1033/B→A | 0.73887 | +0.00000 | **+0.01009** | +0.00000 | +0.00000 | +0.00000 | **+0.02674** |
| **mean** | | **+0.00345** | **+0.04708** | **+0.00000** | **+0.00000** | **−0.00190** | **+0.06083** |
| **stable both conventions** | | 2/8 | **7/8** | 0/8 | 0/8 | 1/8 | 8/8 |

`DST-OPP` is the only rule that moves the objective, and it is **strongly
board-dependent**: +0.0775 to +0.0928 on DEN @ KC against +0.0012 to +0.0130 on
DAL @ NYG. One board supplies essentially the whole effect.

### The zeros are real, not shortlist truncation

A zero delta has two very different causes: the rule discards nothing the model
rates highly, or the 1,200-lineup shortlist cut its admissions off at the knee.
Measured rather than assumed:

| rule | marginal admissions | best marginal rank in its own union | how many reach the 1,200 shortlist | entries selected (of 20) |
|---|---|---|---|---|
| `CPT-SALARY` | 37,002–37,077 | 3–24 | 104–281 | 0–2 |
| `FLEX-POOL` | 27,011–47,346 | 2–56 | **161–198** | **0** |
| `DST-OPP` | 9,656–9,819 | 1–8 | 343–416 | 1–8 |
| `MAX-KDST` | 537–546 | 54–706 | 3–28 | **0** |
| `PUNT-ROLE` | 19,851–20,705 | 10–40 | **132–224** | 0–1 |

`FLEX-POOL` puts 161–198 of its admissions **inside** the shortlist — one ranked
as high as 2nd in its whole union — and the greedy still never picks one.
`PUNT-ROLE` the same with 132–224. So those zeros are the rules being free, not
the shortlist hiding their value. Without this column the "apparently harmless"
verdicts would have rested on an untested assumption, which is why it was added
mid-run.

## J. The full-legal counterfactual

Removing every owner rule while keeping DraftKings legality gains **+0.061** mean
held-out Top 1%, stable in 8 of 8 cells. That is more than `DST-OPP` alone
(+0.047) and more than the sum of all single-rule effects (+0.049), so the hard
gates are **not** collectively free under the current model — the question is
whether the gain is trustworthy, not whether it exists.

It is also the answer to the premise check: the ~3% survival rate does **not**
mean the gates are doing their job efficiently. A portfolio drawn from the full
simulable-legal universe beats one drawn from the 3% that survives, by a margin
larger than any single rule explains.

| | DEN @ KC | DAL @ NYG |
|---|---|---|
| baseline held-out Top 1% | 0.60784 | 0.73506 |
| full-legal held-out Top 1% | 0.70807 | 0.75650 |

## K. Interactions — triggered, and structurally predicted

The residual that triggered S5.9: full-legal exceeds the sum of single-rule
ablations by **+0.0122** on average and **+0.0341** in the worst cell, and in one
cell (085/1033/A→B) the singles sum *negative* while full-legal is positive.

Section F predicted where it comes from. `CPT-POS` is 100% subsumed by
`CPT-SALARY`, so a kicker-or-defense captain becomes enterable **only when both
come off** — no single ablation can reach it. Four pairs were declared from that
structure **before** the run, with reasons recorded in the artifact; `PUNT-ROLE`,
`MAX-KDST`, `MAX-PUNT` and `MAX-TE-TEAM` were excluded with reasons.

| cell | `CPT-SALARY`+`CPT-POOL` | `CPT-SALARY`+`CPT-POS` | `CPT-SALARY`+`DST-OPP` | `CPT-SALARY`+`FLEX-POOL` | full legal |
|---|---|---|---|---|---|
| 086/0912/A→B | +0.01092 | **+0.06333** | **+0.08801** | +0.01092 | +0.10394 |
| 086/0912/B→A | +0.00956 | **+0.05469** | **+0.09370** | +0.00956 | +0.09536 |
| 086/1033/A→B | +0.00816 | **+0.07230** | **+0.09382** | +0.00816 | +0.10571 |
| 086/1033/B→A | +0.00474 | **+0.05579** | **+0.08315** | +0.00419 | +0.09591 |
| 085/0912/A→B | +0.00000 | **+0.01523** | **+0.00906** | +0.00000 | +0.01669 |
| 085/0912/B→A | +0.00256 | **+0.01605** | **+0.01304** | +0.00256 | +0.02664 |
| 085/1033/A→B | −0.00832 | +0.00817 | −0.00144 | −0.00832 | +0.01567 |
| 085/1033/B→A | +0.00000 | **+0.01719** | **+0.01009** | +0.00000 | +0.02674 |
| **mean** | **+0.00345** | **+0.03784** | **+0.04868** | **+0.00338** | **+0.06083** |
| **stable both conventions** | 2/8 | **7/8** | 7/8 | 2/8 | 8/8 |

**The prediction held exactly.** `CPT-SALARY`+`CPT-POS` is +0.038 where
`CPT-POS` alone is 0.000 and `CPT-SALARY` alone is +0.003 — a super-additive
interaction of about +0.035, which is the residual. The other three pairs add
nothing over their components: `CPT-SALARY`+`DST-OPP` (+0.049) is
indistinguishable from `DST-OPP` alone (+0.047), and both pool pairs equal
`CPT-SALARY` alone.

So the interaction is **one pair, one mechanism**: kicker and defense captains.
Not "rules interact in general".

The effects do not add, and the reason is worth stating: the portfolio has a
fixed budget of 20 entries, so admitting more strong lineups *displaces* rather
than accumulates. `DST-OPP` (+0.047) and the K/DST-captain interaction (+0.035)
do not sum to full-legal's +0.061 — they compete for the same slots.

**Untested:** every combination of three or more rules. Full-legal is the only
evidence about them and it cannot attribute. Pairs beyond the four declared are
also untested — all 36 would have been a search, not a test.

## L. Football-only evidence — the half the field model cannot move

Held-out DK score of the 20 selected entries, no field model anywhere, averaged
over the four cells per board.

| board | portfolio | held-out Top 1% | football mean | football p99 |
|---|---|---|---|---|
| DEN @ KC | baseline | 0.60784 | 75.57 | **137.08** |
| | `ablate:DST-OPP` | 0.69365 | 76.12 (+0.54) | 135.86 (**−1.22**) |
| | `ablate:CPT-SALARY` | 0.61618 | 75.39 (−0.18) | 136.19 (−0.89) |
| | `pair:CPT-SALARY+CPT-POS` | 0.66937 | 75.71 (+0.14) | 135.41 (**−1.67**) |
| | full legal | 0.70807 | 76.31 (+0.74) | 135.65 (**−1.43**) |
| DAL @ NYG | baseline | 0.73506 | 93.22 | **165.37** |
| | `ablate:DST-OPP` | 0.74341 | 93.04 (−0.18) | 164.48 (−0.89) |
| | `ablate:CPT-SALARY` | 0.73362 | 93.23 (+0.02) | 165.12 (−0.25) |
| | `pair:CPT-SALARY+CPT-POS` | 0.74922 | 93.08 (−0.14) | 164.63 (−0.74) |
| | full legal | 0.75650 | 93.14 (−0.08) | 164.53 (−0.84) |

**Every variant that raises Top 1% lowers the portfolio's football p99, on both
boards, without exception.** The mean is mixed and small; the tail is
consistently negative. The effect is modest in relative terms (−0.9% to −1.2% of
p99) against a large relative Top-1% gain (+14% on DEN @ KC), so this is not a
contradiction of equal magnitude — but it is a contradiction of **direction**,
and S5.11 asks for directional compatibility.

The mechanism is legible in section H: the admitted lineups are leverage plays.
A Chiefs-defense captain at p99 120.8 against an allowed field at 137.1 is not a
higher-scoring lineup; it is a lower-scoring lineup almost nobody owns. In a
tournament that can genuinely be better — leverage *is* the game. But the
entire value of the trade is computed by the ownership model, and that model is
`softmax(beta × projected points)` with beta solved so the most popular build
holds 0.2% of the field, a figure taken from one published 2021 contest and never
fitted to real Showdown ownership.

## M. Model-conditional contest evidence

Everything in sections I–K is **MODEL-CONDITIONAL**: held-out Top 1% and Top 0.1%
under the placeholder ownership model. The held-out folds remove in-sample
optimism; they do not make the field model right. Nothing in this stage validates
it, and the `field_model_calibrated` gate link stays open.

## N. Field-concentration sensitivity

Swept for the three rules whose removal moved held-out Top 1% by ≥ 0.005 in any
cell at either exact convention — `CPT-SALARY`, `DST-OPP`, `PUNT-ROLE`. One board
(DEN @ KC), one seed, both fold directions, three concentrations, same football
worlds. **Scenarios only; none may become a default and none is a calibrated
alternative.**

| concentration | β | `DST-OPP` A→B | `DST-OPP` B→A | `CPT-SALARY` A→B | `CPT-SALARY` B→A |
|---|---|---|---|---|---|
| 0.001 flatter | 0.379 | **+0.0896** | **+0.0727** | **+0.0111** | +0.0053 |
| 0.002 production | 0.455 | **+0.0860** | **+0.0928** | **+0.0109** | **+0.0096** |
| 0.004 concentrated | 0.544 | **+0.0950** | **+0.0982** | **+0.0173** | **+0.0150** |

**`DST-OPP` does not depend on the field knob.** +0.073 to +0.098 across the
whole range, sign-stable at both exact conventions in all six cells. This is the
opposite of S4's headline effect, which fell by two thirds across the same range
— and it means the "it is only an artifact of one beta setting" escape is not
available here. `PUNT-ROLE` is 0.000 at every setting but one (+0.0014, not
stable), confirming it as noise.

So the sensitivity sweep **strengthens** the case against `DST-OPP` rather than
dissolving it. What holds the recommendation back is section L, not section N.

## O. Recommendation, per rule

S5.11's bar, declared before the results: a rule is a serious removal candidate
only if its admissions actually enter selected portfolios, held-out
production-objective coverage improves, the improvement is consistent across
boards/seeds/directions, **football-only tail evidence is directionally
compatible**, and the result is not an artifact of one field setting.

| rule | marginal | Δ held-out Top 1% | stable | football p99 | verdict | S4 rerun if changed? |
|---|---|---|---|---|---|---|
| `DST-OPP` | 9,656–9,819 | **+0.047** | 7/8 | **worse on both boards** | **INCONCLUSIVE — keep** | **yes** (structural) |
| `CPT-POS` (with `CPT-SALARY`) | 0 alone | **+0.038** paired | 7/8 | **worst of any variant** | **INCONCLUSIVE — keep** | **yes** (structural) |
| `CPT-SALARY` | 37,002–37,077 | +0.003 | 2/8 | −0.25 to −0.89 | apparently harmless — keep | no (threshold, <10%) |
| `PUNT-ROLE` | 19,851–20,705 | −0.002 | 1/8 | ≈0 | apparently harmless — keep | n/a |
| `FLEX-POOL` | 27,011–47,346 | +0.000 | 0/8 | 0.00 | apparently harmless — keep | n/a |
| `MAX-KDST` | 537–546 | +0.000 | 0/8 | 0.00 | apparently harmless — keep | n/a |
| `CPT-POOL` | 0 | 0.000 | — | 0.00 | redundant given `CPT-SALARY` | n/a |
| `MAX-PUNT` | 0 | 0.000 | — | 0.00 | redundant given `PUNT-ROLE` | n/a |
| `MAX-TE-TEAM` | 0 | 0.000 | — | 0.00 | redundant given the others | n/a |

**Ranking.**

*Most likely harmful (but not established):* `DST-OPP`, and the
`CPT-SALARY`+`CPT-POS` pair. Both clear four of the five criteria. Both fail the
football one.

*Inconclusive:* nothing else — the remaining rules do not reach the first
criterion.

*Apparently harmless under current evidence:* `CPT-SALARY`, `PUNT-ROLE`,
`FLEX-POOL`, `MAX-KDST`. "Harmless under current evidence" is not "correct" — it
means the model does not rate what they discard, under a model whose covariance
is known to be limited.

*Redundant:* `CPT-POOL`, `MAX-PUNT`, `MAX-TE-TEAM`. Each rejects lineups another
rule also rejects, 100% of the time, on both boards. Removing any of them changes
nothing **while its partner stands**. That is not a reason to remove them — it is
a reason to know that the partner is doing the work.

### Why `DST-OPP` is held rather than removed

Four reasons, in order of weight.

1. **The football tail goes the wrong way.** Every Top-1%-improving variant
   lowers held-out p99, on both boards, without exception. The gain is placement,
   not scoring.

2. **It sits on the model's known weak point.** `DST-OPP` bans rostering the
   defense that faces your captain — a lineup that needs one offense to produce
   and the other to be stopped. Its value is a *cross-side covariance* quantity,
   and cross-side covariance is precisely what Stage 2B–2F measured
   legacy-latent getting wrong. The one rule S5 flags is the one whose evidence
   depends most on the part of the simulator we have the least confidence in.
   That is not a coincidence to shrug at; it is the single best reason to wait.

3. **One board supplies the effect.** +0.078 to +0.093 on DEN @ KC against
   +0.001 to +0.013 on DAL @ NYG. Two boards cannot tell a property of the rule
   from a property of one game's player pool — and DEN @ KC happens to pair two
   strong offenses with two defenses the model likes.

4. **It is structural, so removing it triggers the S4 rerun.** Its marginal set
   is 9,819 lineups, 41% of the allowed universe, which clears the 10% count
   trigger on its own; and it bans a shape rather than setting a number, which
   clears the structural trigger independently. The Top-1%-versus-Top-0.1%
   conclusion would have to be re-measured on the new universe before it could be
   quoted again.

### What would settle it

Named, so the next stage has a target rather than an instruction to think harder:

- **S9 / joint-model validation on cross-side covariance specifically.** If
  legacy-latent's opposing-side coupling is validated, the `DST-OPP` Top-1% gain
  becomes a real number rather than a model artifact. If it is found wrong in the
  direction that inflates captain-opposite-defense builds, the rule is vindicated
  and this finding dissolves.
- **More boards.** Four to six Showdown slates with varied pool shapes would
  separate "property of the rule" from "property of DEN @ KC". Cheap compared to
  S9 and worth doing first.
- **A football-objective arm.** Every portfolio here was selected for Top 1%. A
  parallel arm selecting on held-out p99 would show whether `DST-OPP`'s
  admissions are *ever* chosen by an objective the field model cannot touch.

## P. S4-rerun triggers, per proposed change

`s5_rules.s4_rerun_required(before, after, removed)` is executable, not prose, so
the answer for a given proposal is a computation. Either trigger suffices: the
enterable count moving more than 10%, or the removal of a structural rule.

| proposed change | enterable after | relative change | count trigger | structural | rerun required |
|---|---|---|---|---|---|
| remove `DST-OPP` | 33,639 | +41.2% | yes | yes | **yes** |
| remove `CPT-SALARY` + `CPT-POS` | 78,255 | +228.5% | yes | yes | **yes** |
| remove `CPT-SALARY` | 60,897 | +155.7% | yes | no | **yes** |
| remove `MAX-KDST` | 24,366 | +2.3% | no | no | no |
| remove `CPT-POOL` / `MAX-PUNT` / `MAX-TE-TEAM` | 23,820 | 0.0% | no | structural for `CPT-POOL` | yes for `CPT-POOL`, no for the other two |

Since S5 recommends no change, no rerun is triggered by this stage. The table
exists so that a later decision cannot skip the question.

## Q. Limitations

- **Two boards, both primetime, both ~88,000 entries.** `DST-OPP`'s effect
  differs by a factor of seven between them. Smaller, flatter or differently
  priced contests are unmeasured, and the one finding that matters is the one
  most exposed to this.
- **One simulator, with known covariance limitations**, and the flagged rule is
  the one most sensitive to them (section O, reason 2).
- **"Full DK-legal" means legal over the simulable pool.** Players with no
  Sleeper projection are not in the enumeration at all. The counterfactual is
  therefore a lower bound on what removing every owner rule would admit.
- **The 1,200-lineup shortlist is still binding and still unvaried**, inherited
  from S4. Section I's rank diagnostic shows the zeros are not caused by it, which
  is narrower than showing the cutoff does not matter anywhere.
- **Three-or-more-rule combinations untested**, and pairs beyond the four
  declared.
- **Selection objective is Top 1% only.** A rule that discards lineups valuable
  under a different objective would not be visible here.
- **DAL @ NYG is not the board S4 used.** Rebuilt from feeds captured
  2026-09-12 it has 23 pool players and 583,082 legal lineups against 22 and
  427,048 when the live A/B ran, because its DK pool gained a player. DEN @ KC
  reproduces byte-identically (777,056 / 23,820). Cross-stage comparisons on
  DAL @ NYG need that caveat; comparisons on DEN @ KC do not.
- **Field sensitivity ran on one board and one seed**, like S4's.

## R. Provenance

| artifact | sha256 (first 16) | bytes | generated at | dirty |
|---|---|---|---|---|
| `s5_rules.json` | see manifest | 12,907 | `b01e179` | no |
| `s5_ablate.json` | see manifest | 957,451 | `b01e179` | no |
| `s5_pairs.json` | see manifest | 64,770 | `ed7ffbc` | no |
| `s5_sens.json` | see manifest | 26,991 | `ed7ffbc` | no |

Every S5 artifact carries a non-null seed in its provenance block, under the
contract tightened at `96bf08b`: `None` now means "did not say" and fails
`provenance.complete()`. Seeds are `{"boards": [20260912, 771033],
"bootstrap": 11}` for the cross-fit stages and `20260912` for the inventory.

**The Sleeper projection feeds are committed** (`research/data/feeds`), which the
previous audit could not do — it found that no artifact could be reproduced
because the feeds were live-fetched and absent from the bundle. S5 reruns from
committed inputs. That also produced the reproducibility data point in section Q:
one board came back byte-identical four days later and one did not.

Nothing in `research/` is imported by the app, the PC worker, or the guard
suite's production paths.

## S. Tests

**Zero failures, with numpy and with numpy hidden, both exit codes checked
explicitly as the final statement of the command.** That is the invariant; the
printed count is market-dependent and is not a checksum (see the Showdown
report's section W).

Guards added for S5, beyond the artifact assertions:

- the nine owner-rule constants are pinned, so a retune cannot leave this report
  describing a rule that no longer exists;
- `_sd_allowed`'s rejection paths are **counted from the AST**, so a tenth rule
  cannot be added without S5 noticing;
- the shadow classifier is proved equal to production on a synthetic pool built
  so all nine rules fire — and a second guard asserts all nine *do* fire, so the
  equivalence is not proved on a universe where half the rules are inert;
- every forbidden lineup carries all its reasons and every allowed lineup none;
- a single-rule ablation admits exactly the exclusive set and keeps every other
  rule enforced;
- the full-legal counterfactual is still DK-legal (six distinct, under cap, both
  teams);
- the scoring fold cannot change the screen, proved by overwriting it;
- the S4-rerun condition fires on either trigger independently;
- no S5 module contains an `ev`/`roi`/`cash`/`payout` symbol, checked as exact
  AST constants and attribute names rather than substrings — `"ev"` matches
  `"every"`, which cost this suite a false green earlier in the pass.

## T. What S5 did not do

No production rule was changed. No strategy default moved. `SD_ENGINE` did not
move, so no board is invalidated. The money gate is still shut on both links and
no S5 number went near it. S6, S7, S8 and S9 were not started.
