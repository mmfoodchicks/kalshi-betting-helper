# Slot-allocation v1: pre-registered, fitted on week 1 only, frozen before the week-2 lock

Module `research/cl_slot.py` (the pre-registration is its docstring,
committed and pushed at 02:25 UTC on 2026-09-20 before the first grid pass
finished). Frozen file `research/data/cl_slot_frozen.json` (written once at
03:43:19 UTC, 13 hours 17 minutes before the week-2 main slate locks;
immutable; stamped clean at the pre-registration commit 00e2943). Fit
artifact `research/data/cl_slot_fit.json` (the in-sample realisation at
831,028 lineups). Reference `research/data/cl_slot_week1_reference.json`
(the untouched current family on the same pool at the same size, graded by
the same functions). Research only; no production change; nothing is
refitted after this.

**In-sample verdict, stated before week 2 so it cannot be rewritten after.
Slot-allocation v1 wins both pre-registered co-primaries against the
untouched family on week 1 (slot cross-entropy 21.99 against 23.63, the real
entropy floor being 19.27; salary Wasserstein-1 $32 against $566, the mean
on the public's $49,850) and leaves the chalk ordering exactly where it was
(top-50 Spearman 0.32 against 0.32; top-20 bias −6.3 pp against −6.3;
Michael Mayer 1.3% against 27.5% real, the Cardinals defense 22.3% against
2.0%). The gain is the FLEX slot and the salary distribution, not who the
public owns. The week-2 test therefore decides whether the slot and salary
mechanisms transfer; it cannot decide the chalk, which this family does not
model, and a positive week-2 result on the co-primaries will not be read as
more than that.**

## 1. What was frozen

| Parameter | Value | How |
|---|---|---|
| FLEX position shares (RB / WR / TE) | 0.4251 / 0.3613 / 0.2136 | week-1 maximum likelihood from the observed FLEX counts (first artifact) |
| beta, projection temperature | 0.15 | grid, slot cross-entropy, pass 2 |
| eta, cheap-enabler coefficient on TE and DST | 0.0 | grid, slot cross-entropy, pass 2 (0 on both passes) |
| delta, completed-lineup salary-left penalty per $1,000 | 3.05371 | bisection to the week-1 mean salary used, pass 2 |

The search ran exactly as pre-registered: pass 1 over the 7 × 6 grid at
delta 0 (best beta 0.25, eta 0, cross-entropy 21.205; salary $48,438);
delta 1 by bisection at that point (2.1123; the mean landed on $49,850);
pass 2 over the grid at delta 1 (best beta 0.15, eta 0, cross-entropy
21.878); delta 2 at that point (3.0537); stop. 40,000 accepted lineups per
evaluation, seeds from SeedSequence(20260920, pass, i, j); the realisation
seed 20260913. Smoothing add-half per slot group. Real mass on players
outside the served pool, dropped from the objective: 0.1 to 0.7% per group.
The sampler is production's classic_sample under five stamped source
substitutions; production's function hash at the freeze is on the frozen
file, so a later change to the production sampler changes the candidate's
meaning and a guard says so.

The frozen file also carries the week-2 protocol: the pool is the latest
valid pre-lock production pool of draft group 153428 (a served-board
snapshot whose build stamp and fetch both precede 17:00 UTC; a post-lock
fetch is archival only); two arms, the untouched current family re-run on
that pool at the field's size and this candidate with these parameters;
co-primary lower slot cross-entropy and closer salary-used Wasserstein-1;
everything else out of objective; no refit.

## 2. Week 1, in-sample: the candidate beside the untouched family

Both at 831,028 lineups on the served pre-lock pool of 240, graded by the
same functions. The current family's arm re-calibrated as a build does
(beta 0.198, kappa −1.875).

| | Current family | Slot-allocation v1 | Real |
|---|---|---|---|
| **Slot cross-entropy, total (co-primary)** | **23.630** (KL 4.355) | **21.989** (KL 2.714) | entropy 19.275 |
| by group: QB / RB / WR | 3.045 / 3.075 / 3.813 | 3.084 / 3.089 / 3.838 | |
| by group: TE / DST | 3.411 / 3.480 | 3.481 / 3.529 | |
| by group: FLEX | 6.805 (KL 2.511) | 4.968 (KL 0.673) | |
| **Salary Wasserstein-1 (co-primary)** | **$566** | **$32** | |
| Salary mean; at $50,000; ≥ $49,500; ≤ $48,000 | $49,285; 30.8%; 71%; 12.2% | $49,850; 46.1%; 93.9%; 0.08% | $49,850; 49.9%; 94.7%; 0.6% |
| FLEX is RB / WR / TE | 29.9 / 70.2 / 0.0 | 57.6 / 33.7 / 8.7 | 42.5 / 36.1 / 21.4 |
| Punts under $3,000: 0 / 1 / 2+ | 15 / 77 / 9 | 13.7 / 74.2 / 12.1 | 36.6 / 48.4 / 15.0 |
| Top-50 Spearman | 0.319 | 0.325 | |
| Top-20 MAE / bias (pp) | 8.35 / −6.28 | 8.75 / −6.29 | |
| Top-10 / 20 / 50 overlap | 3 / 9 / 35 | 3 / 10 / 36 | |
| Most-owned player | Gibbs 39.7% | Gibbs 33.3% | Gibbs 42.8% |
| Mayer / Jaguars / Cardinals | 1.6 / 2.2 / 22.6 | 1.3 / 2.0 / 22.3 | 27.5 / 17.8 / 2.0 |
| Stack 0 / 1 / 2 / 3+; bring-back | 17.4 / 49.1 / 28.8 / 4.7; 34.9 | 18.1 / 51.5 / 27.2 / 3.2; 34.9 | 19.2 / 53.0 / 26.1 / 1.7; 32.2 |
| Defense vs own QB / own back | 0.38 / 10.4 | 0.38 / 11.3 | 0.50 / 2.4 |
| Unique-lineup share; most-copied | 99.25%; 29 | 99.77%; 8 | 89.8%; 346 |
| Distinct-entry collision | 1.8e-8 | 3.2e-9 | 1.30e-6 |

Reading. The whole cross-entropy gain sits in the FLEX group (6.81 → 4.97):
the current family cannot put a tight end at FLEX and puts a receiver there
twice as often as the public; the candidate draws the position first. The
salary distribution is matched by the penalty alone (mean, the share at
the cap, the share above $49,500 and the tail below $48,000 all land near
the public), and that is what eta was meant to help with and did not: the
objective chose eta 0 on both passes, and the TE and DST groups are
slightly worse than the current family's (3.48 against 3.41; 3.53 against
3.48), because the public's tight-end slot is dominated by one $2,900
player and its defense slot by two cheap units, which a linear salary term
across all tight ends and defenses does not single out. The salary tilt
also moves the realised FLEX mix away from the drawn shares, as the
pre-registration said it would (a tight end at FLEX 8.7% against the drawn
21.4%): lineups with a tight end at FLEX leave more salary and are kept
less often. On the chalk, nothing moved: the same players are missed by the
same amounts, the top-50 order is the same, and the most-owned player fell
from 39.7% to 33.3% against the public's 42.8%. Concentration went the
wrong way again (the field is more diffuse than the current family's,
which was already sixty times too diffuse).

## 3. What week 2 can and cannot say

Week 2 is graded on the two co-primaries exactly as frozen. Transfers on
both: the slot-allocation and spend mechanisms are not slate-specific, and
the next problem is concentration and user-level behaviour, and separately
the chalk, which needs a role-specific choice mechanism this family does
not contain. Fails slot cross-entropy on week 2: the week-1 FLEX shares and
temperature were slate-specific. Fixes salary but not the slots: the
penalty transfers and the allocation does not. No reading is rescued by
refitting week 2. The candidate's known in-sample limits (chalk untouched;
tight-end and defense slots not improved; the FLEX mix distorted by the
tilt; concentration too diffuse) are stated here so they are not discovered
on week 2 and mistaken for a transfer failure.

Nothing in production changed. The current family is the control on week 2
and the served week-2 board is its realisation.
