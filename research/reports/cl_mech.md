# Task 8, third artifact: the current classic family's trade-off surface, and what any per-player ranking can reach

Artifacts `research/data/cl_mech.json` (module `research/cl_mech.py`; ten
preregistered arms, one seed each at 831,028 draws on the served pre-lock
pool of 240; the real week-1 Millionaire field grades only) and
`research/data/cl_rank.json` (module `research/cl_rank.py`; fit-free rank
correlations of enumerated per-player statistics with the real ownership).
Research only. No kappa is picked after looking at the field, no arm is a
model, no production change follows.

**Verdict. Can correcting legal FLEX support and removing the
expensive-player pressure improve chalk ordering without recreating the
salary failure somewhere else? No on both counts. Across the whole grid the
real top-50 ordering stays where the served build left it (Spearman 0.27 to
0.34 against the served 0.32; top-20 bias −6.1 to −6.6 pp against −6.3), and
every positive kappa makes the salary failure worse (mean $48,809 at kappa 0
and $47,326 at kappa 2 against the served $49,280 and the public's $49,850;
lineups at $48,000 or less 23% to 52% against the served 12% and the
public's 0.6%). The freed ownership does not reach the public's cheap chalk;
it lands on new wrong names. The negative-kappa reading offered by the
second artifact is therefore falsified as the cause of the chalk miss: the
sign of the price term is not what starves Mayer, the Jaguars and the Jets.
The ranking diagnostic says why no region of this family exists: no
per-player monotone function of projection and salary in the enumerated set
orders the real top 50 (Spearman −0.14 to +0.30 over the real top 50; the
best is salary alone), although each one orders players WITHIN a position
well (Sleeper's line: RB 0.94, WR 0.93, TE 0.94). The public's chalk is a
lineup-construction pattern, the top projections at the expensive slots
plus cheap enablers at tight end and defense, which no per-player ranking
on these projections can reproduce. The current family has no region that
gets both; the extra dimension is earned, and its shape is more specific
than "a separate spend knob" (section 4).**

## 1. The arms, as preregistered

Served: beta 0.194 and kappa −1.9844 exactly as the served build calibrated
them (artifact 2's arm; reproduced here at 831,028 draws). Grid: kappa in
{0, 0.5, 1, 2} points per $1,000, positive meaning the cheaper player per
projected point is preferred, with beta calibrated by bisection to
CL_FIELD_MAX_OWN (the existing 0.38) alone on 20,000-draw samples and kappa
never used to chase salary. DK-complete FLEX: each of the five with a tight
end eligible at FLEX, built from production's own source by three
substitutions (the FLEX eligibility columns, the FLEX salary floor, and the
overflow of a tight-end pick into FLEX once the TE slot is full) executed in
production's namespace, everything else production's by construction; the
substitutions and both source hashes are in the artifact. One seed
(20260913) per arm: artifact 2 measured this family's seed noise at 0.02 pp
of per-player MAE and 0.004 of top-50 Spearman, far below the arm effects
asked about, and this is stated rather than hidden. All ten arms were run on
one clean commit (880e7e3), which the assembler checks.

## 2. The surface

| kappa | TE at FLEX | beta | Max own % | Top-20 MAE / bias (pp) | Top-50 ρ | All ρ | Overlap 10 / 20 / 50 | Salary mean | At cap % | ≤ $48,000 % | FLEX RB / WR / TE % | Punts 0 / 1 / 2+ % | Unique % | Distinct-entry collision |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| served −1.98 | no | 0.194 | 39.4 | 8.35 / −6.32 | 0.320 | 0.857 | 3 / 9 / 35 | $49,280 | 30.6 | 12.2 | 30 / 70 / 0 | 15 / 77 / 9 | 99.25 | 1.9e-8 |
| 0 | no | 0.260 | 37.1 | 8.15 / −6.48 | 0.329 | 0.903 | 3 / 9 / 37 | $48,809 | 22.1 | 22.6 | 35 / 65 / 0 | 19 / 71 / 10 | 99.72 | 5.2e-9 |
| 0.5 | no | 0.295 | 37.5 | 8.03 / −6.41 | 0.335 | 0.909 | 3 / 9 / 39 | $48,694 | 20.4 | 25.1 | 35 / 65 / 0 | 20 / 70 / 10 | 99.76 | 4.3e-9 |
| 1 | no | 0.337 | 37.6 | 7.91 / −6.42 | 0.323 | 0.912 | 3 / 10 / 40 | $48,494 | 17.9 | 29.4 | 36 / 64 / 0 | 22 / 68 / 10 | 99.80 | 3.2e-9 |
| 2 | no | 0.481 | 37.7 | 7.73 / −6.63 | 0.267 | 0.896 | 4 / 10 / 37 | $47,718 | 11.1 | 45.1 | 38 / 62 / 0 | 25 / 64 / 11 | 99.86 | 2.0e-9 |
| served −1.98 | yes | 0.194 | 39.3 | 8.23 / −6.10 | 0.330 | 0.870 | 3 / 9 / 35 | $49,181 | 27.0 | 14.1 | 24 / 39 / 37 | 15 / 60 / 24 | 99.68 | 4.6e-9 |
| 0 | yes | 0.266 | 37.6 | 7.99 / −6.23 | 0.343 | 0.899 | 3 / 9 / 37 | $48,698 | 19.7 | 24.7 | 27 / 40 / 33 | 20 / 60 / 20 | 99.86 | 1.9e-9 |
| 0.5 | yes | 0.296 | 37.2 | 7.90 / −6.28 | 0.328 | 0.907 | 3 / 9 / 38 | $48,492 | 17.3 | 29.1 | 28 / 40 / 32 | 21 / 60 / 19 | 99.89 | 1.5e-9 |
| 1 | yes | 0.341 | 37.6 | 7.75 / −6.25 | 0.338 | 0.910 | 4 / 9 / 40 | $48,282 | 15.2 | 33.5 | 27 / 39 / 33 | 22 / 60 / 18 | 99.90 | 1.3e-9 |
| 2 | yes | 0.493 | 37.6 | 7.55 / −6.48 | 0.322 | 0.896 | 4 / 9 / 39 | $47,326 | 8.7 | 51.7 | 25 / 36 / 39 | 25 / 58 / 17 | 99.92 | 9.6e-10 |
| **real** | | | **42.8** | | | | | **$49,850** | **49.9** | **0.6** | **43 / 36 / 21** | **37 / 48 / 15** | **89.8** | **1.30e-6** |

Reading the surface. Three things move, none of them toward the public.
First, the all-player Spearman rises from 0.86 to 0.90 as kappa leaves
negative territory: the tail of low-owned players is ordered better, and the
top-20 MAE eases from 8.35 to 7.55 pp because the served build's over-owned
expensive names come down (the Cardinals defense 22.6 → 6.4%, Trey McBride
15.6 → 2.0%, Bijan Robinson 21.8 → 13.5% from the served arm to kappa 2
with FLEX). Second, that freed mass does not go where the public put it:
Michael Mayer rises from 1.6% to at most 7.0% against 27.5% real, the
Jaguars from 2.2% to 4.9% against 17.8%, and Chris Olave, Saquon Barkley and
Omarion Hampton FALL (12.0 / 9.8 / 9.0% to 9.3 / 7.3 / 7.0% against 24.8 /
21.1 / 19.9%); the new over-owned names are Travis Etienne (21.9% against
8.8%), Sam LaPorta (15.8% against 7.0%), Jaylen Warren and Jayden Reed. The
top-10 overlap with the real top 10 never exceeds 4 of 10. Third, salary and
concentration move the wrong way monotonically: every step of kappa lowers
the mean, empties the cap and fattens the sub-$48,000 tail, and every arm is
more diffuse than the served build (collision 1e-9 to 5e-9 against the
served 1.9e-8 and the real 1.3e-6). The tight-end-at-FLEX variant fixes the
zero (TE at FLEX 32 to 39% against the public's 21%, an over-correction at
equal eligibility) and buys about 0.01 of top-50 Spearman and 0.2 pp of
top-20 bias; it also doubles the two-punt share (17 to 24% against 15%).

## 3. Why no region exists: the ranking diagnostic

For each enumerated per-player statistic on the served pool, the Spearman
with the real any-slot ownership over all 240, over the real top 50, the
overlap of its top 20 with the real top 20, and the within-position Spearman
for RB / WR / TE:

| Statistic | All 240 | Real top 50 | Top-20 overlap | Within RB / WR / TE |
|---|---|---|---|---|
| Sleeper projection (what production ranks by) | 0.847 | 0.075 | 6 | 0.94 / 0.93 / 0.94 |
| Sleeper projection per $1,000 (value) | 0.841 | −0.140 | 2 | 0.92 / 0.92 / 0.91 |
| Projection − 2 × salary/1k (the served sign) | 0.784 | 0.161 | 8 | 0.94 / 0.90 / 0.94 |
| Projection − 1 × salary/1k | 0.814 | 0.119 | 7 | 0.94 / 0.91 / 0.94 |
| Projection + 0.5 × salary/1k | 0.861 | 0.046 | 6 | 0.94 / 0.93 / 0.94 |
| Projection + 1 × salary/1k | 0.872 | −0.005 | 5 | 0.94 / 0.93 / 0.93 |
| Projection + 2 × salary/1k | 0.848 | −0.049 | 2 | 0.93 / 0.91 / 0.88 |
| DraftKings' own season average | 0.748 | 0.147 | 6 | 0.81 / 0.77 / 0.87 |
| DraftKings' average per $1,000 | 0.690 | −0.047 | 3 | 0.76 / 0.66 / 0.84 |
| Salary alone | 0.593 | 0.296 | 10 | 0.90 / 0.81 / 0.86 |

The real top 20 by the projection's rank in the pool: Gibbs 1, Chase 4,
St. Brown 5, Bijan Robinson 8, Jonathan Taylor 9, Jefferson 16, Achane 21,
Barkley 26, Olave 27, Hampton 30; then Michael Mayer 117, the Jaguars 103,
the Jets 129, Juwan Johnson 85, Quentin Johnston 73. By value per $1,000
Mayer is 16th, the Jets 12th and the Jaguars 34th, while Gibbs falls to 28th
and Chase to 40th. No single ordering holds both halves: the public's chalk
is the projection leaders at the expensive slots AND the cheapest
serviceable tight end and defenses, which is a statement about how lineups
are built (spend on the studs, punt the slots that pay least per dollar),
not about which players a per-player statistic ranks highest. A second
public projection source (DraftKings' own averages) does not change that.

## 4. What this earns, and what it does not

It earns the extra dimension the reviewer named, with a sharper spec than a
separate spend knob. A family that can reproduce this field has to decide
at the LINEUP level where the salary goes: which slots carry the studs and
which slots are punted, with the punt slot's ownership concentrating on the
cheapest serviceable option (Mayer at $2,900, the Jets at $2,500, the
Jaguars at $3,400) rather than on the best projection at that position. A
per-player price term of either sign cannot express that, which is what the
grid and the ranking diagnostic say together: within a position Sleeper's
line orders the public well (0.93 to 0.94), across positions the public's
allocation does not follow any per-player score. The candidate for the
held-out slate should therefore be simple in exactly this direction, a
slot-allocation mechanism over which the existing per-player softmax runs
within each slot, pre-registered before the week-2 main slate locks, and it
is not proposed in detail or fitted here.

It does not say the projections are right (the public's chalk also reflects
its own projections, and DraftKings' averages did no better), it does not
say what the new family's numbers are, and it does not change production.
One slate; the grid is a measurement of the current family, and every
number here is a reading of that family on that slate.
