# S5: the shadow-forbidden rule study

**Baseline** `96bf08b` (the post-audit Showdown tree). **Research only.** No
production rule was changed, no strategy default moved, the money gate stayed
shut throughout, and no S5 module computes a payout column at all — an AST guard
asserts that rather than trusting it.

**The question**, stricter than "which rule rejects the most lineups": *which
hard owner rule, if any, excludes lineups that remain strong when selected on one
set of worlds and judged on another?*

**The answer, in one paragraph.** Four of nine rules have **zero marginal
admissions when disabled individually under the current production rule set** —
another rule already rejects everything they reject. That is a statement about
the current *conjunction*, not about the rules: `CPT-POS` has zero marginal
admissions alone and a **+0.038** effect when disabled alongside `CPT-SALARY`
(section K), so it plainly has strategic effect that another rule is masking. Of
the rest, exactly one moves the production objective on its own: `DST-OPP`, the
ban on rostering the defense that faces your captain, worth **+0.047** mean
held-out Top 1%, stable in 7 of 8 cross-fit cells, and robust across a
field-concentration sweep on the one board and seed it was swept on — which S4's
headline effect was not. **Recommendation: keep every rule.** The reason is not
that the gain is fake: it is that the gain is **not separable** from the two
things this app distrusts. `DST-OPP`'s Top-1% advantage is an interaction between
the simulated joint score distribution and the **uncalibrated weighted opponent
field**; football-only portfolio statistics neither independently validate it nor
show it to be an ownership artifact (section L). Add that one of two boards
supplies essentially the whole effect, that the lineups producing it are
captain-opposite-defense builds resting on exactly the cross-side covariance
Stage 2B–2F measured legacy-latent getting wrong, and that removing the rule is
structural and reshapes the candidate universe by +41%. `DST-OPP` is the one
reproducible candidate effect in this study, **INCONCLUSIVE** rather than
harmful, with the experiment that would settle it named in section O.

**Corrected twice, after two independent reviews.** The review of `321c0b7`
withdrew "four of nine rules are provably inert" (too strong — see above) and
found that "every ablation that raises Top 1% lowers the portfolio's p99" was
**false under the correct statistic**, being true only of the mean of twenty
per-entry p99s. The review of `9187d4e` then withdrew the replacement claim
— *"the gain is ownership, not football"* — as an attribution the artifacts do
**not** identify: the stored football distribution is mixed, not flat, and
production's optimistic Top-1% metric uses field mass **strictly above** the
candidate, so the candidate's own modelled ownership is not directly in the
number at all. Section L carries the full distribution and the corrected reading;
section U records both rounds.

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

## F. The rule-overlap matrix, and which rules are masked by another

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
named rule, on both boards independently.

**This is masking, not inertness, and the distinction is the whole point.** The
correct statement about such a rule is: *zero marginal admissions when disabled
individually under the current production rule set.* Nothing stronger. `CPT-POS`
cannot be shown to protect anything *while `CPT-SALARY` stands* — and section K
measures what happens when it does not stand: **+0.038**, an order of magnitude
above anything either rule does alone. A rule with zero marginal admissions is a
rule whose effect is currently **masked**, and if its masking partner is ever
retuned or removed the masked effect becomes live. Calling such a rule inert,
redundant or harmless would licence exactly the wrong conclusion.

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
the shortlist hiding their value. Without this column the zero verdicts would
have rested on an untested assumption, which is why it was added mid-run. Note
what the column does **not** establish: it is still a statement about these rules
*under the current conjunction*, measured at one shortlist size.

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

## L. Football-only evidence — and the aggregation that had to be fixed

**Two different statistics, and an earlier revision of this section used the
wrong one for the claim it was making.**

`mean-of-entry p99` — the mean, over the 20 selected entries, of each entry's own
held-out p99. Descriptive: *how high does a typical entry in this portfolio
reach.* This is what `s5_ablate.football_selected.p99` records and what this
section previously reported as "the portfolio's football p99".

`portfolio p99` — for each held-out world take the **maximum** raw DK score
across the 20 entries, then take the p99 of that worldwise-max distribution. This
is the portfolio-level tail: a 20-entry submission delivers its best entry in any
given world, and it is the football analogue of Top-q coverage, which the contest
metric already computes as a worldwise max.

Both, from the portfolios the artifacts already recorded (`research/s5_tail.py`;
no re-selection, so the old figure is reproduced exactly and the relabel is
checkable). Means over the four cells per board.

| board | portfolio | held-out Top 1% | **portfolio p99** | Δ | (old) mean-of-entry p99 | Δ |
|---|---|---|---|---|---|---|
| DEN @ KC | baseline | 0.60784 | **160.20** | — | 137.08 | — |
| | `ablate:DST-OPP` | 0.69365 | 159.31 | **−0.89** | 135.86 | −1.22 |
| | `ablate:CPT-SALARY` | 0.61618 | 160.36 | **+0.16** | 136.19 | −0.89 |
| | `ablate:FLEX-POOL` / `MAX-KDST` / `PUNT-ROLE` | 0.60784 | 160.20 | 0.00 | 137.08 | 0.00 |
| | full legal | 0.70807 | 159.29 | **−0.91** | 135.65 | −1.43 |
| DAL @ NYG | baseline | 0.73506 | **187.93** | — | 165.37 | — |
| | `ablate:DST-OPP` | 0.74341 | 187.81 | **−0.12** | 164.48 | −0.89 |
| | `ablate:CPT-SALARY` | 0.73362 | 187.83 | **−0.11** | 165.12 | −0.25 |
| | full legal | 0.75650 | 188.39 | **+0.45** | 164.53 | −0.84 |

### What the corrections changed

**Round one (review of `321c0b7`): the aggregation.** This section said *"Every
variant that raises Top 1% lowers the portfolio's football p99, on both boards,
without exception."* That is **false** under the portfolio-tail definition — it
was true of the mean-of-entry statistic. `ablate:CPT-SALARY` is **+0.16** on
DEN @ KC and `full_legal` is **+0.45** on DAL @ NYG: two sign flips, so "without
exception" was doing work it had not earned.

**Round two (review of `9187d4e`): the attribution.** The replacement claim —
*"the gain is ownership, not football"*, *"the portfolio scores the same and
places far better"*, *"essentially all of the advantage is attributed to low
modelled ownership"* — is **also withdrawn**. Two reasons, both decisive.

*The football distribution is mixed, not flat.* The full stored distribution for
`DST-OPP`, which the single p99 row was hiding:

| | DEN @ KC | Δ | DAL @ NYG | Δ |
|---|---|---|---|---|
| worldwise-max **mean** | 93.4849 → **94.0666** | **+0.5817** | 112.1273 → 112.1775 | +0.0503 |
| p90 | 124.8110 → 124.8618 | +0.0507 | 151.1792 → 151.0700 | −0.1093 |
| p95 | 135.4513 → 135.2495 | −0.2018 | 163.3219 → 163.2562 | −0.0656 |
| p99 | 160.2015 → 159.3138 | −0.8877 | 187.9340 → 187.8120 | −0.1220 |
| p99.9 | 186.2069 → 185.2869 | −0.9200 | 216.9416 → **217.6934** | **+0.7518** |

On the board that supplies essentially the whole Top-1% effect, the **expected
best score across the portfolio rises by 0.58 DK in every fold and seed cell**.
That is genuine football-side diversification, not "scores the same". The upper
percentiles fall slightly on DEN and DAL's extreme tail *rises*. "Mixed" is the
only word the data support.

*And the metric does not work the way the withdrawn claim assumed.* Production's
optimistic Top-1% reads field mass **strictly above** the candidate:
`_field_pass` computes `above = cumsum(hist[::-1])[::-1] − hist`, and
`equiv_mass(..., "optimistic")` returns `strict` unchanged. The candidate's own
bucket — its same-score mass, which is where its own modelled ownership lives —
is **excluded**. Same-score mass enters only the tie conventions, which the
production convention ignores. So "this lineup is low-owned, therefore its
Top-1% is higher" is not the mechanism, and no number here isolates ownership as
a cause.

### The supported reading

> `DST-OPP`'s Top-1% advantage is an interaction between the **simulated joint
> score distribution** and the **uncalibrated weighted opponent field**: the
> admitted lineups score well in worlds where the high-weight public
> constructions score badly. Football-only portfolio statistics do not
> independently validate the gain, and they do not show it to be an ownership
> artifact either. The decomposition is not identified by this study.

Which fits `DST-OPP` exactly, and tightens reason 2 in section O rather than
competing with it: a captain-versus-opposing-defense build is valuable precisely
when its scoring pattern runs counter to what the model says the public owns —
and that pattern *is* cross-side football covariance, the part of legacy-latent
Stage 2B–2F measured as limited. The mechanism and the known weakness are the
same object.

**Why the two aggregations diverge, which is itself informative.** Mean-of-entry
p99 falls further than portfolio p99 in every case. The admitted lineups are
individually lower-ceiling but *less correlated* with the rest of the portfolio —
so the portfolio's best-in-world score holds up or improves while its average
member's ceiling drops. That is exactly what a greedy **cover** objective is built
to do, and it is visible only once the two aggregations are separated. It is also
the cleanest piece of evidence that something real is happening on the football
side, which is why the ownership-only story had to go.

## L2. The opponent field is invariant under every owner-rule ablation

Required by the review, and the right thing to require: disabling an **owner**
rule must change which lineups *we* may enter, not who the modelled opponents
are. If `field_weights` were renormalised over the enlarged owner universe, every
ablation would conflate "we may now enter this" with "the public now behaves
differently", and the +0.047 would be partly an artifact of moving the opponents.

Two reasons it holds, one structural and one measured.

**Structural.** `dfs_tourney.field_weights(players, idx, cpt_mult, top_share)`
has no `allowed` parameter at all. It is a softmax over projected points across
the lineups in `idx`, and every S5 call site passes the **full legal universe**,
once per board, before any ablation universe is constructed. The owner-allowed
mask is applied only when choosing *candidates*.

**Measured** (`research/s5_tail.field_invariance`): compute `(f, beta)` from the
full universe, then rebuild the owner-allowed mask with each of the nine rules
disabled in turn and recompute. Across all nine ablations on all four
board × seed combinations, `f` is **byte-identical** and there is exactly **one**
beta value per board — 0.454838 on DEN @ KC, 0.305193 on DAL @ NYG.

The artifact also records the size of the defect that does **not** exist, so the
avoided confound is on file rather than merely asserted. Had the field been
renormalised over the owner-allowed subset, beta would have moved from **0.4548
to 0.3075** on DEN @ KC (−0.147, a 32% change) and 0.3052 to 0.2072 on
DAL @ NYG. That is not a rounding error; it would have changed every Top-q
number in the stage. A guard now proves the invariance behaviourally rather than
leaving it to the function signature.

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

**Scope, stated as tightly as corrected S4 states its own.** This sweep covers
**one board** (DEN @ KC, draft group `153086`), **one seed** (`20260912`), **both
fold directions**, and **three concentrations** (top share 0.001 / 0.002 / 0.004).
It is not the two-board two-seed grid the main comparison used. So the supported
statement is:

> *On the tested board and seed, `DST-OPP`'s modelled Top-1% benefit survives all
> three field concentrations without changing sign.*

Nothing wider. In particular **this does not touch the board heterogeneity in
section I.** Field robustness and slate robustness are different axes: the sweep
says the effect does not ride the beta knob *on DEN @ KC*, while section I says
DEN @ KC supplies essentially the whole effect and DAL @ NYG is near null
(+0.001 to +0.013). A sweep run on the board that carries the effect cannot
speak to the board that does not.

With that scope: +0.073 to +0.098 across the range, sign-stable at both exact
conventions in all six cells. That is the opposite of S4's headline effect, which
fell by two thirds across the same range, so the "it is only an artifact of one
beta setting" escape is **not available** for this rule on this board.
`PUNT-ROLE` is 0.000 at every setting but one (+0.0014, not stable), confirming
it as noise.

So the sweep removes one explanation for `DST-OPP` rather than supporting its
removal. What holds the recommendation back is section L's attribution of the
gain and the model limitation in section O — not section N.

### A statistical footnote, recorded rather than blocking

Nine single-rule tests, four pre-declared pairs and a full-legal counterfactual
across eight cells is **not** a familywise-controlled hypothesis family, and no
multiplicity correction was applied. Since S5 promotes no rule change, nothing
rests on a formal significance threshold. `DST-OPP` is therefore described
throughout as **the only reproducible candidate effect under this study** — not as
a discovery at any stated significance level. The paired bootstraps are
descriptive interval estimates for each comparison taken on its own.

## O. Recommendation, per rule

S5.11's bar, declared before the results: a rule is a serious removal candidate
only if its admissions actually enter selected portfolios, held-out
production-objective coverage improves, the improvement is consistent across
boards/seeds/directions, **football-only tail evidence is directionally
compatible**, and the result is not an artifact of one field setting.

The football column below is the **portfolio p99** (section L), not the
mean-of-entry statistic an earlier revision used.

| rule | marginal (single) | Δ held-out Top 1% | stable | portfolio p99 | verdict | S4 rerun if changed? |
|---|---|---|---|---|---|---|
| `DST-OPP` | 9,656–9,819 | **+0.047** | 7/8 | −0.89 DEN / −0.12 DAL | **INCONCLUSIVE — keep** | **yes** (structural) |
| `CPT-POS` (only with `CPT-SALARY`) | 0 alone | **+0.038** paired | 7/8 | −1.67 mean-of-entry; portfolio n/m | **INCONCLUSIVE — keep** | **yes** (structural) |
| `CPT-SALARY` | 37,002–37,077 | +0.003 | 2/8 | +0.16 DEN / −0.11 DAL | no candidate effect — keep | no (threshold, >10% though: **yes**) |
| `PUNT-ROLE` | 19,851–20,705 | −0.002 | 1/8 | 0.00 / −0.11 | no candidate effect — keep | n/a |
| `FLEX-POOL` | 27,011–47,346 | +0.000 | 0/8 | 0.00 | no candidate effect — keep | n/a |
| `MAX-KDST` | 537–546 | +0.000 | 0/8 | 0.00 | no candidate effect — keep | n/a |
| `CPT-POOL` | **0 under the current conjunction** | 0.000 | — | 0.00 | masked by `CPT-SALARY` — keep | yes (structural) |
| `MAX-PUNT` | **0 under the current conjunction** | 0.000 | — | 0.00 | masked by `PUNT-ROLE` — keep | no |
| `MAX-TE-TEAM` | **0 under the current conjunction** | 0.000 | — | 0.00 | masked by the union of the others — keep | no |

`CPT-SALARY`'s rerun column is corrected from an earlier revision: its marginal
set is 37,077 on DEN @ KC, **+155.7%** of the allowed universe, so the count
trigger fires even though the rule is a threshold rather than a structural one.

**Ranking.** Categories chosen so that none of them can be read as "this rule
does nothing".

*The only reproducible candidate effects:* `DST-OPP`, and the
`CPT-SALARY`+`CPT-POS` pair. Both clear four of the five criteria and fail the
football one. Neither is promoted.

*No candidate effect under this study:* `CPT-SALARY`, `PUNT-ROLE`, `FLEX-POOL`,
`MAX-KDST`. Their admissions reach the shortlist — 104–281, 132–224, 161–198 and
3–28 respectively — and the greedy either does not pick them or gains nothing by
it. That is evidence about *the model's opinion of what they discard*, under a
model whose covariance is known to be limited. It is not evidence the rules are
correct.

*Zero marginal admissions when disabled individually, under the current
production rule set:* `CPT-POOL`, `MAX-PUNT`, `MAX-TE-TEAM`. Each rejects only
lineups another rule also rejects — 100% of the time, on both boards — so
disabling it alone changes nothing **while its masking partner stands**. These
rules are **not** inert, redundant or harmless in any general sense: `CPT-POS` is
in the same category and the `CPT-SALARY`+`CPT-POS` pair is the largest
interaction in the study. If a masking partner is ever retuned or removed, the
masked rule's effect becomes live and has to be re-measured.

### Why `DST-OPP` is held rather than removed

Four reasons, re-weighted after the portfolio-tail correction. The football
argument is now the *attribution* of the gain rather than a contradiction of it.

1. **The gain is not separable from the uncalibrated field model.** `DST-OPP`
   raises held-out Top 1% materially, and football-only portfolio statistics are
   **mixed**: the expected worldwise-max score improves (+0.58 DK on DEN @ KC)
   while upper percentiles are flat to slightly lower, and DAL @ NYG's extreme
   tail rises. The data therefore support neither "essentially all of the gain is
   ownership" nor "there is an independent football advantage". The effect is an
   interaction between the simulated joint score distribution and the weighted
   opponent field — and that field is `softmax(beta × projected points)` with
   beta fixed by one published 2021 contest, the single thing the money gate is
   shut *because of*. An advantage that cannot be separated from an uncalibrated
   model is not an advantage this app may act on. (Section L; the earlier
   "ownership, not football" attribution is withdrawn.)

2. **It sits on the model's known weak point.** `DST-OPP` bans rostering the
   defense that faces your captain — a lineup needing one offense to produce and
   the other to be stopped. Its value is a *cross-side covariance* quantity, and
   cross-side covariance is precisely what Stage 2B–2F measured legacy-latent
   getting wrong. The one rule S5 flags is the one whose evidence depends most on
   the part of the simulator we have least confidence in. Not a coincidence to
   shrug at.

3. **One board supplies the effect.** +0.078 to +0.093 on DEN @ KC against
   +0.001 to +0.013 on DAL @ NYG. Two boards cannot separate a property of the
   rule from a property of one game's player pool, and section N's field sweep
   does not help here: it ran on DEN @ KC, the board that carries the effect.

4. **It is structural, so removal triggers the S4 rerun.** 9,819 marginal
   admissions is +41.2% of the allowed universe, clearing the count trigger
   alone; and it bans a shape rather than setting a number, clearing the
   structural trigger independently.

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
- **A field-free portfolio objective arm, selected on the SELECTION fold.**
  Every portfolio here was selected for Top 1%, which is a field-weighted
  objective. A parallel arm would show whether `DST-OPP`'s admissions are ever
  chosen by an objective the field model cannot touch at all.

  **Corrected:** an earlier revision proposed "selecting on held-out p99". That
  reintroduces exactly the leakage S4 spent a stage removing — the scoring fold
  must never choose candidates. The arm must **select on the selection fold and
  grade on the held-out fold**, like everything else here.

  And the objective should be *portfolio-native* rather than per-lineup, since
  section L shows the two differ in a way that matters: greedily maximise
  **selection-fold E[max raw DK score across the 20 entries]**, or fixed-score
  exceedance coverage (the share of worlds where the portfolio's best entry
  clears a fixed threshold), then grade held out. Both are computable from
  `W[rows] @ X[:, sel]` with no field weights anywhere, so they test football
  diversification directly. Cheap — no new screening pass is needed, only a
  different greedy over the same shortlist.

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

**This section was inconsistent with the artifacts and is rebuilt with one
precise meaning per column.** An earlier revision had a "generated at" column
that shifted several artifacts to the commit that first *contained* them and
omitted `s5_tail.json` entirely. The columns now mean exactly this:

- **stamped commit** — `meta.provenance.commit`, read from the artifact. This is
  the HEAD the tree was sitting on when the run happened. Nothing more.
- **stamped dirty** — `meta.provenance.dirty`, as recorded at the time.
- **source verified at** — the *first* commit in history whose tree recomputes
  the artifact's stamped `source_sha` exactly (`research/prov_verify.py`).
  **none** means no commit does, so the exact source snapshot that produced the
  artifact is not reconstructible from Git.

| artifact | bytes | stamped commit | stamped dirty | source verified at | reconstructible |
|---|---|---|---|---|---|
| `s5_rules.json` | 12,907 | `96bf08b` | false | **none** | **NO** |
| `s5_ablate.json` | 957,451 | `96bf08b` | false | **none** | **NO** |
| `s5_pairs.json` | 64,770 | `b01e179` | false | `ed7ffbc` | yes |
| `s5_sens.json` | 26,991 | `ed7ffbc` | false | `ed7ffbc` | yes |
| `s5_tail.json` | 31,546 | `321c0b7` | false | **none** | **NO** |

### R1. The clean-state hole, found by audit and present in this very stage

The independent review of `9187d4e` found that `provenance.dirty()` and
`provenance.source_files()` had **incompatible contracts**:

- `dirty()` ran `git status --porcelain --untracked-files=no`;
- `source_files()` listed every `research/*.py` by `listdir`, **including
  untracked files**.

So a brand-new untracked research module could be hashed into `source_sha`,
produce an artifact, be absent from the stamped commit, and the stamp would still
say `dirty: false`. That is the exact defect this module was written to close, and
a false-clean stamp is worse than a dirty one because a dirty one tells the truth.

**It is not hypothetical — four of the five S5 artifacts above carry a false-clean
stamp.** `s5_rules.json` and `s5_ablate.json` say `96bf08b, dirty: false`, and no
commit in history reconstructs their source: when they ran, `s5_rules.py` existed
only as an untracked file, and no commit has a `research/` containing it *without*
the later S5 modules. `s5_pairs.json` says `b01e179` and verifies at `ed7ffbc`, a
**later** commit. Only `s5_sens.json` is honest on both counts.

**Fixed prospectively, historical artifacts NOT regenerated.** `source_state()`
now returns clean only when every hashed path is **tracked and byte-identical to
HEAD**; `dirty()` is its negation; the stamp carries a `dirty_detail` naming
untracked versus modified sources, because those are different problems; and
`require_clean()` refuses both. The artifact bytes above stay as they are — they
are the evidence this audit verified against, and rewriting them to look tidy
would destroy it. `prov_verify.json` is the separate, checkable record.

### R2. What a `source_sha` can and cannot prove

For an artifact with **source verified at = none**, the chain is: the numbers are
internally consistent, the hash is stable, and the code that produced them is
**not recoverable from Git**. That is strictly more information than
`dirty: false` was conveying, and it is the honest answer for a run from a tree
that was never committed in that exact state.

### R3. Reproducibility is partial, and the missing half matters for S6

The pinned Sleeper feeds (`research/data/feeds`, three files, hashes in the
bundle manifest) make the **projection side** reproducible — the previous audit
could not rerun anything at all. But **S5 as a whole is not hermetic**, and the
earlier claim that the feeds "make S5 reproducible" was too broad:
`research/sd_board.slate_and_contest` still live-fetches the DraftKings slate
CSV, the player pool and the contest detail.

DAL @ NYG proves it: 427,048 legal lineups when the live A/B ran, **583,082** on
rebuild four days later, because its DK pool gained a player. DEN @ KC happened to
reproduce byte-identically (777,056 / 23,820), which is luck rather than a
property of the setup.

> **Sleeper inputs are pinned; exact S5 reproduction remains non-hermetic until
> the DraftKings slate, player pool and contest inputs are captured.**

**This should be fixed before S6**, which is explicitly about scoring multiple
contests — so contest metadata stops being incidental and becomes the subject.
What to capture, with hashes: the DK slate CSV and player pool, contest id and
name, field size (`max_entries` / `entered`), entry fee, places paid, and the full
payout schedule. Every one of those is already read by `grids_by_size`, so a
wrong or drifted value changes Top-q silently.

### R4. Seeds

Every S5 artifact carries a non-null seed under the contract tightened at
`96bf08b`: `None` means "did not say" and fails `provenance.complete()`. Seeds are
`{"boards": [20260912, 771033], "bootstrap": 11}` for the cross-fit stages and
`20260912` for the inventory.

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
  equivalence is not proved on a universe where half the rules never fire;
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

## U. What the independent review changed, and what it did not

Four methodological contracts tightened after review of `321c0b7`. Recorded in
full because two of them changed a claim, not just a word.

**1. "Provably inert" withdrawn.** Four rules have **zero marginal admissions
when disabled individually under the current production rule set**. That is all
that was shown. `CPT-POS` is the standing counterexample to the stronger reading:
zero alone, **+0.038** paired with `CPT-SALARY`. The words *inert*, *redundant*
and *harmless* are gone from the verdicts, the ranking and the guards; the
replacement is *masked*, which carries the conditional. This matters later: if a
masking partner is retuned, the masked effect becomes live.

**2. The portfolio tail was not a portfolio tail — and the claim built on it was
false.** `football_selected.p99` is the mean of twenty per-entry p99s. The
portfolio quantity is the p99 of the worldwise maximum across the twenty entries.
Recomputed in `research/s5_tail.py` from the recorded portfolios, so the old
figure is reproduced beside the new one. The withdrawn sentence — *"every variant
that raises Top 1% lowers the portfolio's football p99, on both boards, without
exception"* — is **false** under the correct statistic: `ablate:CPT-SALARY` is
+0.16 on DEN @ KC and `full_legal` is +0.45 on DAL @ NYG. `DST-OPP` remains
negative on both boards but by −0.55% and −0.06%, which is small. The argument is
now the *attribution* of the gain (ownership, not football) rather than a
contradiction, and section L explains why the two aggregations diverge — the
admitted lineups are individually lower-ceiling but less correlated, which is what
a greedy cover is built to exploit.

**3. Field invariance proved rather than assumed.** Section L2. One beta per
board across all nine ablations, `f` byte-identical, plus the size of the
confound that would have existed had the field been renormalised over the
owner-allowed subset (beta 0.4548 → 0.3075 on DEN @ KC). Guarded behaviourally.

**4. The field sweep's scope narrowed to what it ran on.** One board, one seed,
both directions, three concentrations — and explicitly *not* a statement about
the DEN-versus-DAL heterogeneity, which is a different axis. A sweep on the board
that carries the effect cannot speak for the board that does not.

Plus a footnote: nine rule tests, four pairs and a counterfactual across eight
cells is not a familywise-controlled family, and no correction was applied.
`DST-OPP` is "the only reproducible candidate effect under this study", never a
discovery at a stated significance level.

### Round two: review of `9187d4e`

**5. "The gain is ownership, not football" withdrawn.** The replacement claim from
round one was itself an attribution the artifacts do not identify. Two reasons:
the stored football distribution is **mixed** — worldwise-max mean **+0.58 DK** on
the board carrying the effect, upper percentiles slightly down, DAL's p99.9 up
+0.75 — and production's optimistic Top-1% reads field mass **strictly above** the
candidate, excluding its own same-score mass, so the candidate's modelled
ownership is not directly in the metric at all. The supported reading is a
**field-weighting × joint-football interaction**, with the decomposition not
identified. Section L. Two guards that had frozen the withdrawn attribution were
repinned — a guard must not pin a causal claim the data cannot support.

**6. The proposed football arm would have reintroduced S4's leakage.** An earlier
revision suggested "a parallel arm selecting on held-out p99". The scoring fold
must never select. Corrected to select on the selection fold and grade held out,
and upgraded to a *portfolio-native* field-free objective — selection-fold
E[max raw DK score across the 20 entries], or fixed-score exceedance coverage —
since section L shows per-lineup and portfolio statistics diverge in a way that
matters here.

**7. The provenance clean-state hole.** `dirty()` ignored untracked files while
`source_sha()` hashed them, so a new untracked module could produce an artifact
stamped `dirty: false` at a commit that does not contain it. Four of five S5
artifacts turn out to carry exactly that false-clean stamp (section R1). Fixed
prospectively; historical artifacts untouched; `prov_verify.json` records which
commit, if any, reconstructs each one.

**8. Section R rebuilt and the reproducibility claim narrowed.** One precise
meaning per column, `s5_tail.json` no longer omitted, and the feeds now described
as pinning the Sleeper side only — DraftKings slate and contest inputs are still
live-fetched, which DAL @ NYG's 427,048 → 583,082 already demonstrates. Capture
list for S6 in section R3.

**What did not change across either round: the recommendation.** Keep every rule.
Criterion 3 (consistency across boards) was always weak, and criterion 4 now reads
as *not independently validated* rather than *fails* — which is enough, because the
bar was "football-only evidence is directionally compatible" and mixed evidence is
not compatible. The production decision is identical through two rounds of
correction; what improved is that its justification no longer rests on an
attribution the data cannot make. **The recommendation never needed the
overclaim:** it needs only that the advantage is inseparable from an uncalibrated
field model and a known-weak covariance model, is concentrated on one of two
slates, and would reshape the candidate universe by +41% if shipped. All four were
true before either correction and remain true after.

## T. What S5 did not do

No production rule was changed. No strategy default moved. `SD_ENGINE` did not
move, so no board is invalidated. The money gate is still shut on both links and
no S5 number went near it. S6, S7, S8 and S9 were not started.
