# Showdown correction pass: what was audited, what was fixed, what was not

**Baseline** `a1c4836` (the frozen, audited Classic tree).
**This pass** stages S1, S2 and S3 of nine, plus the discrete-scorer
investigation that S3's blocker was referred to (section Y2). S4 through S9 were not reached and
are listed as such rather than summarised as if they had been.

Everything below was reproduced against the code before it was touched. Where
the reviewer was right, the mechanism was fixed rather than the test. Where
the reviewer was right but the fix is out of this pass's scope, the defect is
recorded, its consequence is wired into the money gate, and the blocker is
named.

---

## B. Executive verdict

Three of the ten findings were operational defects that could silently serve a
wrong board, and all three are fixed: a Showdown board could not be
invalidated by an engine change, its pool signature could not see a price or a
status, and a failed rebuild burned its retry window.

One finding is worse than the review framed it, and its severity is smaller
than the first measurement of it suggested. Showdown's first-place column is a
sole-win probability, so it is decided by how often scores tie, and 47% of the
simulated scores could not occur under DraftKings scoring at all — measured in
every position, kickers and defenses included. **That is fixed and the fix is
now served for Showdown** (sections Y2-Y6). But the severity claim came down
twice on the way: from "probably halves ties" (never measured, withdrawn), to
6.14x between two player scores, to 2.36x between two lineup totals, to
**×1.07 and ×0.94** on the chance first place is actually shared on two live
88,000-entry contests — because a field that concentrated already shares first
place 84% and 96.8% of the time. The defect was worth fixing because it was
wrong, not because it was costly.

The money columns now carry a gate. It is shut, on one link: the field model is
a placeholder taken from a single published contest. That link now looks like
the larger of the two problems by a wide margin — if 84-97% of first places are
shared, the model of the public drives payout economics far more than the
scoring grid ever did.

One simulator was promoted, for Showdown only and for correctness only. No
strategy default changed. Classic is untouched, and byte-identical on a seeded
digest of its whole pool.

---

## C. Findings: confirmed, partially confirmed, falsified

| # | Finding | Verdict |
|---|---|---|
| 1 | Showdown escapes engine invalidation | **Confirmed** |
| 2A | Pool signature is names only | **Confirmed** |
| 2B | No lock-relative freshness | **Confirmed** |
| 2C | Failed rebuild consumes its window | **Confirmed** |
| 3 | Showdown runs the legacy simulator | **Confirmed**; the "make the model explicit" half was **already done** |
| 4 | Scores are off the DraftKings lattice | **Confirmed, and larger than claimed** |
| 5 | Money shown without an authority gate | **Confirmed**; the placeholder was already documented in code |
| 6-10 | Rules, portfolio, multi-contest, fast path, identity | **Not reached this pass** |

---

## D-G. Finding by finding

### 1. Engine invalidation (HIGH, fixed)

`pc_worker._rebuild_reason` read `board.get("kind") == "classic"` before
comparing engines, and the Showdown artifact stamped no engine at all. A board
whose numbers meant something older stayed authoritative forever.

Classic and Showdown now version **separately**. `dfs_tourney.SD_ENGINE` is 2;
the two change for different reasons and each rebuild is expensive enough (an
hour against twenty minutes) that one shared number would spend the PC's
evening on boards whose meaning never moved. An artifact with no engine stamp
reads as 0 and rebuilds, which is what every Showdown board written before
this pass should do.

**Guard**: current engine is current, one behind rebuilds, no stamp rebuilds,
and each kind reads its own version.

### 2A. Pool signature (HIGH, fixed)

`pool_sig` hashes playable **names only**. Measured on a synthetic Showdown
export, the old signature calls all of these identical: a captain's price
moving, a flex price moving, a player turning questionable. It moves only when
somebody appears or disappears.

`pool_sig_rich` hashes the DraftKings id, name, team, game, position, roster
slot, salary and status. A Showdown export lists every player twice, CPT and
FLEX, so both prices are rows of their own and a captain-only repricing is
visible. Showdown uses it; **Classic deliberately keeps the names-only
signature**, because an hour of rebuild for a price tweak is a bad trade there
and a good one here, where the price decides which of roughly 480,000 lineups
are affordable at all.

The DraftKings player id is now parsed and kept, which finding 10 also wants.

**Guard**: the rich signature moves on each of the four changes and the
names-only one provably does not; and every field it hashes is one the CSV
reader actually produces, so it cannot look sensitive while being blind.

### 2B. Lock-relative freshness (HIGH, fixed)

The only rebuild triggers were pool membership and an explicit request,
neither of which fires for a price move, a demotion, or news. A board built
on Thursday was served at Sunday kickoff unchanged.

Four mandatory pre-lock windows now force a refresh: four hours, two hours,
one hour, and a last look about 25 minutes before kickoff. Each fires once.
This is **not** an age rebuild, which the owner rejected for good reason; it
is lock-relative and bounded. Every board records `kickoff_ts` and
`mins_before_lock`, so a board made on Thursday can be told from one made at
lock.

### 2C. Failed rebuild retry (HIGH, fixed)

The window was stamped **inside** the decision, before the build ran. A build
that then threw burned the window, and the board sat unrebuilt until the next
window came round.

`_rebuild_reason` is now pure: it reads state and never writes it, returning
the reason and the window the decision used. The caller consumes the window
only after a known outcome, a successful build or a look that found nothing.

**Guard**: deciding writes no state at all, a simulated failure is owed again
next cycle, and consuming is what stops the retry. The guard also asserts the
consume call sits after the build in the source, not before it.

### 3. Simulator model (HIGH, partially falsified)

Showdown does run the legacy simulator; `build_nfl_showdown` takes no model
argument. But the review's remedy, "make the model explicit in every
artifact", was **already in place**: every board carries
`nfl_dfs_sim.sim_stamp` with the model name, version, fitted constants and how
touchdowns are drawn. Nothing was needed and nothing was changed. Production
stays on legacy, as instructed.

### 4. The DraftKings lattice (HIGH, confirmed and larger than claimed)

Measured on the live pool, 1,972,000 simulated scores
(`research/sd_lattice.py`, artifact `research/data/sd_lattice.json`):

| Position | Off the lattice |
|---|---|
| QB | 50.21% |
| RB | 50.03% |
| WR | 50.10% |
| TE | 50.04% |
| K | 45.83% |
| DST | 31.21% |
| **All** | **47.35%** |

The worst residual is exactly **half a step**, in every position. The scores
sit on a 0.01 grid; DraftKings offence lives on 0.02.

The mechanism is the one the review suspected, and it is three mechanisms:
skill players are pinned to the Sleeper mean by multiplying the whole point
array by `proj / raw`; a defense is shifted by a floating amount; both are
then rounded to two decimals.

**Why this matters more here than anywhere else.** Showdown's headline column
is a *sole*-win probability. That is decided by tie frequency, and a score
simulated support finer than the legal one biases exact-tie probability
**downward**. The magnitude is **not** implied by the grid spacing and has not
been measured; saying "about half" because the grid is twice as fine would be
inferring a size from a spacing, which is exactly the move this project does
not make. The tie arithmetic is correct. What reaches it is not.

**Not fixed this pass, and the blocker is explicit.** Generating discrete stat
lines and scoring them with DK's rules is exactly what the constrained
simulator already does, at 0 of 1,600,000 off the lattice. Doing it inside the
legacy simulator would move every Classic number validated last pass, which
this pass may not do; promoting the constrained model is not this pass's
decision. So the defect is recorded and its consequence is wired into the
gate.

### 5. Money authority (HIGH, fixed as an authority question)

Confirmed: `FIELD_TOP_SHARE = 0.002` comes from a single published contest,
the 2021 GB-DET Millionaire's 231-way tie, and has never been fitted to real
Showdown ownership. The code already said so in a comment; what was missing
was a structured gate, so the tab rendered EV, ROI and expected copies with no
label.

`sd_money_gate` now ships on every board. Its links differ from Classic's on
purpose: Showdown **enumerates the whole lineup universe**, so nothing is lost
to sampling and expected copies are exact *given the weights*. That makes the
weights the whole question.

| Link | State |
|---|---|
| Lineup universe exhaustive | closed |
| Ties paid as the house pays them | closed |
| Scores on the DraftKings lattice | **open** |
| Field model calibrated | **open** |

**Authoritative: false.** Calibrating the field would not open it on its own
while the lattice link is open, which is the point of keeping them separate.
The tab's experimental labelling is shared code, so Showdown boards inherit it
the moment they carry the gate.

---

## Y2. The third architecture: a Showdown-only discrete legacy scorer

The previous pass called the lattice defect blocked between two bad options.
That was wrong: there is a third, and it works.

**Why it is feasible, from the architecture.** The legacy simulator already
draws components, not points. Touchdowns, interceptions and fumbles were
already integers, drawn Poisson. `_ppr` was already DraftKings' exact scorer,
bonuses included. Only two things were continuous: yards and receptions. And
the damage was not done by those at all, but by what happened afterwards, a
per-player multiply of the finished array by `proj / raw`.

So a discrete mode does not need a new football model. It needs the same
latents, the yards and catches drawn whole, and the mean pinned **before** the
draw instead of after it.

`simulate_game(discrete=True)` does exactly that. The shared game factor, the
quarterback latent and the opposed scripts are untouched. A calibration pass
sizes each player's component scale, a second pass refines it against the
rounding, and no multiply ever touches a finished score.

**Does the football survive?** Measured on CAR @ CHI, 20,000 worlds, same
game both ways:

| | Change |
|---|---|
| Player means | 0.80% at the median |
| Player SDs | 1.35% at the median |
| Pair correlations | 0.0088 median, 0.106 worst |
| Off the DK lattice | **51.40% to 0.00%** |

**Correction, from the promotion-readiness pass that followed (section Y4).**
That 0.00% is measured on `simulate_game`, which returns **offensive players
only**. The kicker and the defense were still being multiplied and shifted after
scoring, so under `discrete=True` as shipped in `f41ea02` the defense was 100%
illegal and the kicker 95% illegal on a live pool. The row above is true as
written and misleading if read as "the pool is legal". `DISCRETE_VERSION` 2
fixes the other half.

**How much does the lattice defect actually cost?** This is the question the
grid spacing cannot answer, and the answer is bigger than the spacing
suggests.

| Tie-sensitive output | legacy | discrete | change |
|---|---|---|---|
| Exact-tie rate, two lineups | 0.00010 | 0.00063 | **6.14x** |
| Best lineup expected payout | $340.77 | $320.87 | **-5.84%** |
| Best payout ignoring ties | $68,670 | $65,520 | -4.6% |
| Best top 1% | 28.58% | 27.14% | -1.44 points |
| Best top 0.1% | 7.02% | 6.76% | -0.26 points |
| Top-10 order by top 1% | | | **identical, 10 of 10** |

Six times, not two. Anyone reasoning from "the grid is twice as fine" would
have understated it by a factor of three, which is exactly why it had to be
measured.

**Sole-first did not resolve and is reported as not measured.** On the
controlled board both modes returned 0.00000%: every first place was shared,
because 4,000 lineups drawn from fourteen players are too alike for an
outright winner. That is a limitation of the test board, not a finding about
the modes. It needs a real slate's lineup diversity to measure, and it is the
single column most exposed to the defect.

**What this changes — and the part of it that was later retracted.** On this
controlled board the ranking did not move in a single slot while the payout moved
about 6% and the tie rate 6x, which read as a money defect rather than a strategy
one. The first half held up on live boards; **the second half did not.** Section
Y5 measures the chance first place is actually *shared* on two real 88,000-entry
contests and finds ×1.07 and ×0.94 — opposite directions, inside reseeding
noise — because the concentrated field already shares it 84% and 96.8% of the
time. The tie rate moving 6x between two player scores, and 2.36x between two
lineup totals, did not translate into a meaningful change in first-place
splitting. The defect is real; its severity on these boards is not.

**Not promoted at `f41ea02`, promoted at the end of this pass.** When this
section was written the mode was unreachable by design: `discrete` defaulted to
False, rode in the pool's cache key, and neither board builder accepted it. It
is now served for **Showdown only** — see section Y6 for the decision and the
five criteria it rests on. Classic's half of that isolation is unchanged and
asserted harder: `build_nfl_classic` takes no such argument and the string
appears nowhere in its body.

**Remaining honest limitation of the mode itself.** Pinning upstream cannot be
exact the way a multiply is. For players projected at a point or more the
median residual is 1.0% and the worst is 8.4%; below a point the pin cannot
move a raw mean of zero at all. The old code bought a perfect mean with an
illegal support. This buys a legal support with a small mean bias, and the
tradeoff should be decided deliberately rather than by me.

## Y3. Promotion readiness, part 1: who the outliers are

Three summary numbers were published for the discrete mode, and none of them
named anybody: means move 0.80% at the median and 16.5% at the worst, the worst
pair correlation moves 0.106. A summary cannot tell a structural regression from
the resolution of a Monte Carlo, so this pass measures the **noise floor** every
delta has to beat: two LEGACY runs of the same game, the same 20,000 worlds,
different generator state. If legacy disagrees with legacy by as much as it
disagrees with discrete, there is nothing to explain.

Measured on **DEN @ KC** (draft group 153086, the week-2 Monday night $1.5M),
26 players, 20,000 worlds, three runs. `research/sd_outliers.py` →
`research/data/sd_outliers.json`.

**A methodological correction first.** `sd_discrete` built its pair list as
`[n for n in common if std > 1.0][:14]` over a **sorted** name list, so "the
worst correlation delta" was the worst among the alphabetically first fourteen
players. That is not the worst pair and should not have been described as one.
Both are now reported. On this board they happen to be the same pair, so the
flaw did not change the answer here — but it could have, and the published
0.106 came from a board where the set was smaller than the universe.

### The worst pair, named

| | |
|---|---|
| Pair | **Cyrus Allen (WR, KC) × Jalen Royals (WR, KC)** |
| Projections | 1.38 and 1.29 DK points |
| Legacy correlation | **+0.1556** |
| Discrete correlation | **+0.3207** |
| Delta | **+0.1651** |
| Legacy-vs-legacy control on the same pair | +0.0024 |

It is a real move, well outside the noise floor — and it is between the two
lowest-projected receivers on the board, it moves correlation **up**, and the
mechanism is visible: round a five-yard receiving line to whole yards and whole
catches and a large share of worlds collapse onto zero, so "did he touch the
ball at all" becomes an event driven almost entirely by the shared game factor.
Two punt receivers therefore move together more, not less.

### The whole distribution, against its floor

| | legacy vs discrete | legacy vs legacy (floor) |
|---|---|---|
| Median \|Δ\| | 0.0097 | 0.0055 |
| 95th percentile \|Δ\| | 0.0837 | 0.0188 |
| Maximum \|Δ\| | 0.1651 | 0.0292 |

253 pairs. The median move is barely above the floor; the tail is real.

### The ten largest moves among relationships a roster is built on

| Pair | legacy → discrete | Δ | floor | relationship |
|---|---|---|---|---|
| Cyrus Allen × Jalen Royals | +0.156 → +0.321 | +0.165 | +0.002 | same-team pass catchers |
| Bo Nix × Tyler Badie | +0.204 → +0.317 | +0.113 | −0.016 | RB with own QB |
| Jalen Royals × Travis Kelce | +0.276 → +0.382 | +0.106 | +0.005 | same-team pass catchers |
| Jalen Royals × Rashee Rice | +0.291 → +0.394 | +0.103 | −0.001 | same-team pass catchers |
| Jalen Royals × Noah Gray | +0.222 → +0.320 | +0.098 | −0.004 | same-team pass catchers |
| Jalen Royals × Xavier Worthy | +0.250 → +0.341 | +0.091 | −0.001 | same-team pass catchers |
| Cyrus Allen × Travis Kelce | +0.298 → +0.387 | +0.090 | +0.017 | same-team pass catchers |
| Jalen Royals × Patrick Mahomes | +0.258 → +0.343 | +0.085 | −0.002 | QB with own pass catcher |
| Cyrus Allen × Xavier Worthy | +0.269 → +0.352 | +0.083 | +0.008 | same-team pass catchers |
| Cyrus Allen × Patrick Mahomes | +0.275 → +0.356 | +0.082 | +0.029 | QB with own pass catcher |

**Every one of the ten involves Jalen Royals (1.29), Cyrus Allen (1.38) or
Tyler Badie (0.99).** Not one is a pair of players a showdown roster is
actually built on.

### By relationship, which is the question that matters

| Relationship | n | legacy | discrete | median \|Δ\| | floor | max \|Δ\| |
|---|---|---|---|---|---|---|
| QB with own pass catcher (the stack) | 15 | +0.4240 | +0.4020 | 0.0153 | 0.0060 | 0.0851 |
| QB with the other QB (the shootout) | 1 | +0.1673 | +0.1735 | 0.0062 | 0.0033 | 0.0062 |
| QB with an opposing pass catcher (bring-back) | 15 | +0.1130 | +0.1196 | 0.0086 | 0.0056 | 0.0267 |
| RB with own QB | 6 | +0.2449 | +0.2696 | 0.0089 | 0.0126 | 0.1129 |
| Two pass catchers, same team | 49 | +0.3143 | +0.3381 | 0.0155 | 0.0055 | 0.1651 |
| Kicker with own QB | 2 | +0.4237 | +0.4169 | 0.0217 | 0.0076 | 0.0285 |
| Defense with the opposing QB | 2 | −0.4368 | −0.4254 | 0.0115 | 0.0144 | 0.0156 |
| Defense with own offense | 22 | −0.0524 | −0.0484 | 0.0142 | 0.0103 | 0.0301 |

The stack weakens by 0.022 on the median correlation. The shootout coupling, the
bring-back, the back-with-his-quarterback and the defense-against-the-arm are at
or inside the floor. The kicker still rides his quarterback at +0.42 and the
defense still opposes him at −0.43, which matters because version 2 rewrote both
of those models (section Y4) and breaking that coupling is the only way to make
those two slots worthless.

The kicker and defense pairs are measured on the real pool at 8,000 worlds, not
on `simulate_game`, which returns offensive players only — so the earlier pass
could not have seen them at all.

### The mean residual: rounding, not bias

The worst residual for a player projected over a point is **Jalen Royals**:

| | |
|---|---|
| Projection | 1.29 |
| Legacy mean | 1.290 (legacy pins the mean to the projection exactly, by construction) |
| Discrete mean | 1.354 |
| Absolute error | **+0.064 DK points** |
| Relative error | **+4.97%** |

Banded by projection, the shape is decisive:

| Band | n | median abs err | max abs err | median rel err | max rel err |
|---|---|---|---|---|---|
| 1–5 pts | 11 | 0.041 | 0.135 | 1.62% | 4.97% |
| 5–10 pts | 4 | 0.069 | 0.108 | 0.89% | 1.13% |
| 10–15 pts | 6 | 0.097 | 0.202 | 0.78% | 1.93% |
| 15+ pts | 1 | 0.094 | 0.094 | 0.56% | 0.56% |

**The absolute error never exceeds 0.21 DK points anywhere in the pool, and the
relative error falls monotonically as the projection rises.** That is the
signature of a fixed rounding granularity, not of a bias: a structural
regression would not care how large the projection was. Below a point the
relative numbers get loud and mean nothing — Nate Adkins at 0.32 projected shows
−24%, which is 0.077 of a DK point.

The legacy-vs-legacy control on means is exactly **0.000** at every band, because
legacy pins each mean to the projection by fiat. So the discrete residual is
entirely real. It is also, at every projection anyone rosters, under a fifth of a
point.

---

## Y4. Promotion readiness, part 2: the support, validated to the lineup total

`research/sd_support.py` → `research/data/sd_support.json`. DEN @ KC, 4,000
worlds, 4,000 lineups sampled from the real 777,056-lineup legal universe (half
uniform, half drawn on the field model's own weights, both without replacement
so duplicate lineups cannot be counted as score ties).

### Two findings came out of writing the validator

**First: version 1 only fixed the offense.** The kicker's DK categories are all
whole numbers (1 for an extra point, 3/4/5 for a field goal by distance) and so
are the defense's, points-allowed tiers included — and both were then destroyed
downstream. `_kicker_arr` multiplied its whole-point array by
`projection / raw`, and the defense's array was shifted by a fractional number
of points to reach Sleeper's level. Measured on this live pool under
`discrete=True` as shipped in `f41ea02`:

| Slot | illegal scores |
|---|---|
| DST | **100%** |
| K | **95.3%** |
| QB / RB / WR / TE | 0% (version 1 fixed these) |

Any lineup holding a kicker or a defense was off the lattice with it, which is
most of them. Fixed at the source, `DISCRETE_VERSION` 1 → 2:

* the **kicker** is pinned on the only lever that is a real rate, its
  field-goal Poisson mean, solved in two passes. Extra points are not scalable
  here — there is one per touchdown his own offense scored in that same world,
  and scaling them would cut the tie to the game. Points stay whole.
* the **defense's** Sleeper shift is spent as whole points plus a coin:
  `floor(shift)` always, one more point with probability `frac(shift)`. An
  integer distribution cannot be moved by a fractional amount and stay
  integral, so this is not a trick, it is the only way. The mean lands exactly
  where the plain shift put it; the cost is one Bernoulli's variance, at most
  0.25 points² against a defense's ~60, under half a percent.

**Second: the legality of a total is not the legality of its parts.** A legal
base score is an even number of hundredths. 1.5 × that is an exact integer
number of hundredths, so the engine's 0.01 bucket (`dfs_tourney._RES = 100`)
holds a captain's score exactly. But **54.2% of legacy base scores are ODD
hundredths**, and 1.5 × an odd hundredth needs half a hundredth — which the
engine has nowhere to put. So every legacy captain was being silently rounded
by up to **0.005 points, inside the tie arithmetic the split payout is computed
from**. That is a second defect of the old path, separate from the illegal
support, and nobody had written it down.

### The chain, end to end

| Check | legacy | discrete (v2) |
|---|---|---|
| Offensive score recomputes from its own integer stat line through `_ppr` | **0 of 26** | **26 of 26** |
| Worst recomputation error | 1.26 points | 7.1 × 10⁻¹⁵ |
| Whole pool legal (QB, RB, WR, TE, **K**, **DST**) | **0 of 24** | **24 of 24** |
| Captain multiplier is exactly 1.5 | yes | yes |
| Captain's product lands on the engine's tie grid | **no** (0.005 pt error) | **yes** (exact) |
| Base scores with odd hundredths | 54.2% | **0.0%** |
| Engine's float32 bucket reproduces the exact total | **73.1%** | **100.000%** |
| Worst bucket disagreement | 1 hundredth | **0** |

The 27% disagreement under legacy is not float32 noise. It is the half-hundredth
ambiguity: rounding the captain before the sum and rounding the total after it
land on different buckets. The legacy total is not well defined at the
resolution the engine counts ties on. Under discrete there is nothing to round
and the two agree exactly, in every sampled lineup and every world.

### The tie rate that actually decides a split payout

The earlier pass measured **6.14x** between two PLAYER scores. That is not the
payout number. A split payout turns on the six-player TOTAL, and adding six
scores spreads the support, so the lineup-level ratio is smaller:

| | legacy | discrete | ratio |
|---|---|---|---|
| Pairwise exact-tie rate, uniform lineups | 2.26 × 10⁻⁴ | 6.54 × 10⁻⁴ | **2.89x** |
| Pairwise exact-tie rate, field-weighted lineups | 3.20 × 10⁻⁴ | 7.56 × 10⁻⁴ | **2.36x** |
| Worlds where the top total of the sample is shared | 0.38% | **2.95%** | 7.8x |
| Distinct totals per world, of 4,000 lineups | 2,360 | 1,678 | −29% |

**2.36x at the lineup level, not 6.14x.** Quoting the player-pair figure as the
payout figure would have overstated it by more than two.

Two limits on that last row, stated rather than left to be assumed. The
field-weighted sample is drawn without replacement on the field model's
weights, which is successive sampling, so its inclusion probabilities are not
exactly the field's — it is a sample concentrated where the field is, not the
field. And "the top total of the sample is shared" is not "first place in an
88,000-entry contest is shared": that one needs the analytic field mass the
engine carries, and it is measured in section Y5 as `win_any` minus `win_sole`.

---

## Y5. Promotion readiness, part 3: the live-board A/B

`research/sd_live_ab.py` → `research/data/sd_live_ab.json` and
`sd_live_ab_control.json`. Two real boards, the production pipeline both ways
(`enumerate_showdown`, `field_weights`, `payout_grid`, `run_vs_field`), one
thing changed: whether the pool came out of the discrete scorer. Each build in
its own process, so peak memory is that build's.

| | DEN @ KC | DAL @ NYG |
|---|---|---|
| Contest | $1.5M Monday Night | $1.11M Sunday Night |
| Entries / fee | 88,235 / $20 | 87,145 / $15 |
| Legal lineups | 777,056 | 427,048 |
| Enterable | 23,820 | 13,838 |
| Worlds | 12,000 | 12,000 |
| Field beta | 0.4536 (both arms) | 0.3708 (both arms) |

Beta is identical in both arms because the field model is built on Sleeper
projections, which the mode does not touch. Every difference below is the
scorer.

### The severity claim comes DOWN

This is the headline of the section, and it goes the opposite way from the
controlled board:

| | DEN @ KC | DAL @ NYG | control (reseed only) |
|---|---|---|---|
| P(first place shared), ratio | **×1.07** | **×0.94** | ×1.04 |
| P(first shared) under legacy | **84.2%** | **96.8%** | — |

**The lattice defect materially changes raw lineup tie incidence in controlled
experiments, but did not materially change shared-first incidence on the two
tested live boards, because first place was already tied 84% and 96.8% of the
time under the concentrated field model.** The two boards move in *opposite*
directions and both sit inside the noise of merely reseeding. Once a field of
88,000 entries is that concentrated, field size decides tie incidence and the
scoring grid barely participates.

So the three layers, each measured separately and each smaller than the last:

| Layer | Effect |
|---|---|
| Two player scores tying | 6.14x |
| Two six-player lineup totals tying | 2.36x |
| First place being shared, live boards | **×1.07 and ×0.94** |

It also reframes the money gate. If the model says 84-97% of first places are
shared, the uncalibrated **field** model influences payout economics far more
than the lattice ever did. The lattice was worth fixing because it was wrong,
not because it was expensive.

### What did move, and what did not

| | DEN @ KC | DAL @ NYG | control |
|---|---|---|---|
| Best top 1% | 8.04% → 7.66% (±0.25) | 8.08% → 7.69% (±0.25) | 0.163 pts |
| Best top 0.1% | 1.926% → 2.012% (±0.13) | 1.184% → 0.915% (±0.13) | **0.289 pts** |
| Best expected payout | −7.4% | −14.0% | **−0.03%** |
| Portfolio p_any top 1% | 61.28% → 61.44% | 62.37% → 62.10% | — |
| Score time | 536s → 554s | 307s → 302s | 524s |
| Peak RSS | 2,189 → 2,191 MB | 2,064 → 2,060 MB | 2,189 MB |

Read against the control: **top 0.1% is noise** — reseeding the same board moves
it more (0.289) than the mode change does (0.086). **Expected payout is real** —
reseeding moves it 0.03% while the mode moves it 7-14%, which is the tie
correction arriving in the payout column rather than in the shared-first one.
Top 1% falls ~0.39 points on both boards against a control of 0.163, so it is
small, consistently signed, and about twice the noise.

One caution the control supplied for free: the analytic binomial standard error
understates run-to-run variation, because the "best lineup" is a maximum over
23,820 candidates and carries selection noise on top of sampling noise. The
control's 0.289-point swing in top 0.1% is 2.2 of those SEs.

### Ordering, captains and structures

| | control (legacy vs legacy) | mode change |
|---|---|---|
| Top-20 slots identical | **2 / 20** | **4 / 20** |
| Top-20 set overlap | 17 / 20 | 17 / 20 |
| Same captain in slot | 9 / 20 | 9 / 20 |
| Portfolio overlap | 16 / 20 | 15 / 20 |
| Rank move, median / max | 1.0 / 5 | 2.0 / 8 |

**The top twenty reorders LESS under the mode change than it does when the same
board is merely reseeded.** Lineups at the top of a 777,056-lineup universe are
separated by far less than the ±0.25-point error on each one's top-1%, so that
reshuffling was always arithmetic. Criterion 4 passes on its own control.

Captain exposure in the selected portfolio, every shift one entry of twenty:

| DEN @ KC | | DAL @ NYG | |
|---|---|---|---|
| Bo Nix | 8 → 9 | Jaxson Dart | 10 → 9 |
| Patrick Mahomes | 4 → 4 | Javonte Williams | 5 → 6 |
| Kenneth Walker III | 3 → 4 | Dak Prescott | 3 → 4 |
| Jaylen Waddle | 2 → 2 | Cam Skattebo | 2 → 1 |
| Rashee Rice | 2 → 1 | | |
| Courtland Sutton | 1 → 0 | | |

Top-20 team structures are identical on DAL @ NYG (`5-1`:17, `4-2`:2, `3-3`:1)
and move one lineup on DEN @ KC.

### The one behaviour change that is probably real

The greedy portfolio shifts toward balanced builds on both boards:

| `3-3` entries of 20 | legacy | discrete | control |
|---|---|---|---|
| DEN @ KC | 1 | **4** | 2 |
| DAL @ NYG | 4 | **7** | not run |

The single control moved it by one; the mode moved it by three, in the same
direction on both slates. Stated carefully, because the evidence does not
support more: **discrete support produced a modest shift toward balanced 3-3
portfolios on these two slates, reproducible across both of them and larger
than the one control run's movement.** It is consistent with same-team stack
correlation easing (+0.424 → +0.402) while cross-side coupling tightens
slightly — the direction the Classic research has suggested the old latent
structure gets wrong — but two slates and one structural control are nowhere
near enough to infer a strategic rule, and this is **not** a claim that discrete
scoring favours balanced builds. It is what happened on two boards.

---

## Y6. The promotion decision

### The five criteria, and how each was settled

| Criterion | Verdict | Evidence |
|---|---|---|
| Support exactly valid | **Pass** | 26/26 offensive scores recompute from integer stat lines to 7.1e-15; whole pool legal, K and DST included; captain product exact; engine bucket reproduces the exact total 100.000% |
| Meaningful means close | **Pass** | absolute error ≤ 0.21 DK points anywhere in the pool; ≤ 1.93% for every player projected ≥ 2; relative error falls monotonically with projection |
| Correlation not materially regressed | **Pass, practically** | worst move between two rosterable players 0.0209, sitting in the body of a control distribution whose median maximum is 0.0192 and whose own maximum is 0.0273 |
| Live-board ordering stable | **Pass** | top twenty reorders less than a reseed does (4/20 vs 2/20 identical); captains within one entry of twenty; structures near-identical |
| Runtime acceptable | **Pass** | +3.4% / −1.6% scoring time, peak memory flat |

On the third, and stated with the care the statistic deserves. A single control's
observed maximum of 0.0188 was **not** a threshold, and treating it as one would
have been the same mistake as inferring the tie bias from the grid spacing — the
maximum of 32 correlated deltas is itself a random variable. So six independent
legacy runs at the same depth were compared pairwise over the same 32 pairs
(`research/sd_corr_null.py`):

| max \|Δcorr\| over the 32 rosterable pairs | |
|---|---|
| Control comparisons, min | 0.0136 |
| Control comparisons, median | 0.0192 |
| Control comparisons, p90 | 0.0221 |
| Control comparisons, max | 0.0273 |
| **Observed, legacy vs discrete** | **0.0209** |

**Across the 15 pairwise comparisons formed from six independent legacy runs,
3/15 control comparisons produced a maximum rosterable-pair correlation change
at least as large as the discrete-vs-legacy value. Because the pairwise controls
share underlying runs, 3/15 is an empirical tail fraction rather than an
independent-sample p-value.** An earlier draft of this section called it
`p = 0.25`; that gave the number more inferential weight than it earned, and it
is withdrawn. The practical conclusion is unchanged and sufficient: 0.0209 is
ordinary simulation-scale variation for this statistic, so criterion 3 passes
practically. A formal p-value would need disjoint control pairs and several
times the compute, for a conclusion this evidence already supports.

### Decision: promote, for one reason only

`legacy-latent-discrete` version 2 becomes the Showdown-only production
simulator. `nfl_dfs_sim.SD_DISCRETE = True`, read at exactly two call sites —
`dfs_tourney.build_nfl_showdown` and `nfl_dfs._build_showdown` — so the
tournament board and the lineup sheet cannot disagree about the same game.
`SD_ENGINE` 2 → 3, so every board built under the old scorer is invalidated and
rebuilt rather than served beside a new one.

**The reason, in one sentence: `legacy-latent-discrete` v2 is a more correct
implementation of the existing Showdown football model, because it produces legal
DraftKings outcomes while preserving that model's strategically meaningful
distributional behaviour within measured simulation noise.** It is the correct
implementation of the existing legacy model on DraftKings-valid support. Not any
of these:

- **not** because expected payout improves — it falls 7-14%, and that is a
  correction, not a gain;
- **not** because tie economics improve — live impact on shared-first was
  negligible and not even consistently signed;
- **not** because portfolio construction improves — unknown, and two slates
  cannot say;
- **not** because the legacy correlation model becomes good — it does not, and
  nothing here was fitted to anything.

A simulated score DraftKings cannot print is wrong as a model of DraftKings.
Under legacy, not one of 26 offensive scores recomputed from its own stat line,
every kicker and defence score was off the legal support, and 54.2% of base
scores put their captain's 1.5× half a hundredth away from the grid the tie
arithmetic counts on. That is the whole case.

### What stays fixed

- **Classic is untouched.** `build_nfl_classic` takes no such argument and no
  classic path passes one; the Classic pool is byte-identical on a seeded digest
  of all 93 entries; `ENGINE` stays 6.
- **The money gate stays SHUT, on two links, and one of them is new.** The
  lattice link closes. `field_model_calibrated` remains false. And a link that
  was missing from this gate entirely has been added:
  **`showdown_joint_model_validated`**, also false.

  Closing the lattice link exposed the hole. A legal support is a statement about
  the *scoring rules*, not about the *football*. Showdown serves legacy-latent,
  whose teammate covariance and opposing-side/game covariance are exactly the
  limitations Stage 2B-2F measured — and a six-man single-game roster is *more*
  exposed to them than a classic lineup, because all six spots come out of one
  game's covariance. Without the new link, fitting public ownership perfectly
  would have flipped the gate to authoritative and the app would have announced
  authoritative EV on a knowingly unvalidated joint distribution. It is a real
  term in the boolean, not a renamed label, so it cannot be forgotten:
  `sd_money_gate(field_calibrated=True)` still returns false, and only
  `field_calibrated=True, joint_validated=True` opens it.

  **Promoting the discrete scorer did not validate the legacy correlation model
  and is not evidence for it.**
- **Nothing is claimed about EV, ROI, first place or expected copies.** Rank on
  top 1% and top 0.1%.

### Known costs, stated rather than buried

1. The mean is no longer exact by construction. Legacy pinned it by fiat; this
   pins it upstream and reports the residual. Worst case in the pool 0.21 DK
   points, and below a point of projection the pin cannot move a raw mean of
   zero at all.
2. The kicker's pin has a floor: a projection below what his own extra points
   are worth cannot be reached by kicking less, and the mode overshoots rather
   than multiplying. A kicker asked for 1.0 lands on 3.4. Real showdown kickers
   project 5-9 and land within 0.10.
3. A modest, reproducible shift toward balanced portfolios on two slates
   (Y5), not understood well enough to be called a strategy improvement.
4. Expected payout falls 7-14% on live boards. Behind a shut gate either way.

---

## Z. Every guard repinned in b4e3535, and why none is an accepted regression

Six assertions across five guards changed. Each is listed with what it said,
what it says now, the contract change that forced it, and the reason accepting
it is not the same as accepting a regression.

### Z1. Showdown PC task, signature and trigger pins

- **Old**: `"_rebuild_reason(cur, _pool_sig(slate), wanted.get(dg), state, dg, status_key)" in _tt15`
- **New**: the same call pinned with `_sd_pool_sig(slate)` and `prelock_key=pl_key`, plus a new pin that `_prelock_window(_dt_ts(sl.get("starts")))` is what supplies it.
- **Contract change**: Showdown moved to the price- and status-sensitive signature and gained lock-relative refresh windows.
- **Not a regression**: the guard's original intent was "requests first, richest-contest floor, and never rebuild on age alone". All three survive unchanged, including the literal `"3 * 3600" not in` age check. The pass ADDED a pin, it did not drop one: the refresh is now asserted to be keyed on kickoff rather than on board age, which is the distinction the owner actually cared about.

### Z2. Classic PC task, status-return pin

- **Old**: `'return f"status changed: {detail}"' in _pcw16`
- **New**: `'return (f"status changed: {detail}" if changed else None), status_key' in _pcw16`
- **Contract change**: `_rebuild_reason` returns `(reason, window)` so the caller can consume the window after the outcome.
- **Not a regression**: the status-window behaviour for Classic is byte-identical in effect. The same string is returned for the same condition; it is now the first element of a tuple. The age check beside it is untouched.

### Z3. Rebuild-reason fixture

- **Old**: `_b16 = {"pool_sig": "abc", "built_ts": 100, "status": {...}}`, seven bare calls compared to strings.
- **New**: the same fixture carrying `kind` and `engine`, results read at `[0]`, and the repeated-window case calling `_consume_window` explicitly.
- **Contract change**: the decision is pure, so a repeat no longer self-consumes.
- **Not a regression, and it exposed something**: all seven asserted outcomes are unchanged. But the old fixture carried no `kind`, which means the old engine check — `board.get("kind") == "classic"` — never ran on it at all. The fixture was passing partly because the check it shared a function with was inert for it. Giving it a kind and an engine makes it test the axis it claims to test.

### Z4. The engine guard, which had been pinning the defect

- **Old**: `_rebuild_reason({"kind": "showdown", "pool_sig": "abc", ...}) is None`
- **New**: the same board returns `"engine updated"`.
- **Contract change**: Showdown boards carry a semantic engine and an unstamped artifact reads as 0.
- **Not a regression, and this is the important one**: the old assertion was not incidental. It actively asserted that a Showdown board with no engine stamp must NOT rebuild. That is finding 1 written down as a requirement. Changing it is the fix, not an accommodation of the fix. The Classic half of the same guard is unchanged apart from reading `[0]`.

### Z5. Artifact plain() guard

- **Old**: `'return plain({"version": VERSION, "kind": "showdown"' in _src18`
- **New**: `'return plain({"version": VERSION, "engine": SD_ENGINE,' in _src18` **and** a second pin that `"kind": "showdown", "sport": "nfl", "draft_group_id": int(dg),` is still there.
- **Contract change**: the artifact gained its engine stamp between `version` and `kind`.
- **Not a regression**: the guard's purpose is that every artifact leaves through `plain()` at VERSION 3, which is asserted unchanged. The kind pin was re-added separately so the check came out of the edit no weaker than it went in.

### Z6. A guard I wrote earlier in this same pass

- **Old** (written in S2): `sd_money_gate(..., field_calibrated=True)["authoritative"] is True`
- **New** (corrected in S3): the gate opens only when every link closes, and calibrating the field alone leaves it shut.
- **Contract change**: the lattice audit found a third open link and added it to the gate.
- **Not a regression**: the S2 assertion was written when the gate had two links and encoded "calibrating the field is what opens this". That became false the moment a third link existed. Pinning the conjunction instead is strictly stronger, and it is the version that survives the next link being found.

**Summary.** One repin (Z4) reverses an assertion that had been encoding the
defect. Two (Z2, Z3) follow a return-type change with identical outcomes. One
(Z1) follows a deliberate behaviour change and adds a pin rather than removing
one. One (Z5) follows an artifact-shape change and was re-strengthened. One
(Z6) is my own, corrected within the pass. No assertion was weakened to let
failing code through.

## Z2. Every guard repinned for the promotion, and why none is an accepted regression

Same four fields as section Z, because the same rule applies: a guard that stops
failing because the assertion was loosened is a guard that has been disabled.

### Z2.1. `player_pool`'s kicker call and its field-goal Poisson mean

- **Old assertion.** `'arr = _kicker_arr(k, off, n, k_rng)' in _pool_src` and
  `'_pois(fg_mean * f, rng)' in _sim_src`.
- **New assertion.** The same two strings in their new shape
  (`..., discrete=discrete)` and `_pois(fg_mean * scale * f, rng)`), **plus**
  `def _kicker_arr(k, off, n, rng, discrete=False):` and
  `def draw(scale=1.0):`.
- **Contract change.** Threading the discrete mode into the side models moved
  both call shapes.
- **Not a regression.** Two assertions were *added*, and they are the two that
  matter: the defaults `discrete=False` and `scale=1.0` are what keep the legacy
  draw identical. The old guard pinned the call; the new one also pins the thing
  that makes the call safe.

### Z2.2. The defence's coin and which generator it draws on

- **Old assertion.** `"+ (1 if _random.random() < frac else 0)" in _sim_src`.
- **New assertion.** `"+ (1 if _rg.random() < frac else 0)"` plus
  `"_rg = team_rng.get(team, _random)"`.
- **Contract change.** The coin was moved onto the team's own generator so a
  seeded constrained build stays reproducible draw for draw.
- **Not a regression.** Mine, written earlier in the same session and then
  invalidated by my own follow-up fix. The new form asserts strictly more: the
  coin exists *and* it draws on the right generator.

### Z2.3. "Calibrating the field is not enough while the scores are off the lattice"

- **Old assertion.** `sd_money_gate(..., field_calibrated=True)["authoritative"]
  is False` and exactly **2** of 4 links closed.
- **New assertion.** The gate is shut today, `field_model_calibrated` is the
  **only** open link, exactly **3** of 4 closed, an empty universe still cannot
  open it — and, separately, that with the field calibrated every link *would*
  close and the gate **would** open.
- **Contract change.** The clause "while the scores are off the DraftKings
  lattice" described a defect that no longer exists.
- **Not a regression.** The invariant it protected — the gate opens only when
  every link closes, and it is shut now — is asserted unchanged. A second guard
  was added in the opposite direction, because a gate that can never open is a
  label rather than a gate, and nothing was previously testing that.

### Z2.4. The lattice link, which had been pinning the defect

- **Old assertion.** `SD_MONEY_LATTICE_OK is False`,
  `links["scores_on_the_dk_lattice"] is False`, and `"cannot occur under
  DraftKings scoring" in why`.
- **New assertion.** All three inverted, plus `"recomputes from an integer stat
  line"` and `"field model drives the payout economics"` in the gate's text.
- **Contract change.** The defect is fixed and validated end to end
  (`research/sd_support`).
- **Not a regression.** The *audit* this guard was protecting —
  `research/data/sd_lattice.json`, measured on the legacy pool, 47.35% off the
  lattice in every position — is asserted **unchanged** directly above it,
  because it is a record of what was wrong, not a claim about what is. And a
  new guard was added that the gate must carry the *smaller* live severity
  figure (×1.07 and ×0.94) rather than the larger controlled one, so nobody can
  re-inflate it later.

### Z2.5. "The discrete mode cannot reach production"

- **Old assertion.** `"discrete" not in signature(build_nfl_showdown)` and
  `not in signature(build_nfl_classic)`.
- **New assertion.** Split into three. The argument still defaults off and
  still rides in the pool's cache key. Showdown serves it on **both** surfaces
  through one constant, with the call-site counts pinned. And Classic's half is
  now `"discrete" not in signature(build_nfl_classic)` **and** the string
  `discrete` appearing nowhere in the whole `build_nfl_classic` body.
- **Contract change.** This guard was holding a door shut while the decision
  behind it was open. Section Y6 answers it, for Showdown only.
- **Not a regression.** The Classic half is *strengthened*, from "the signature
  has no such parameter" to "no classic path passes it anywhere". The Showdown
  half is replaced by a harder assertion than the one removed: not merely that
  the argument is absent, but that exactly two named call sites pass it and that
  they are the two that must agree with each other.

### Z2.6. The simulator stamp and the engine

- **Old assertion.** The stamp ends in `-discrete` and carries
  `DISCRETE_VERSION`; `SD_ENGINE >= 2`.
- **New assertion.** Both served showdown artifacts — the board and the sheet —
  call `sim_stamp(..., discrete=nfl_dfs_sim.SD_DISCRETE)`; the model is exactly
  `legacy-latent-discrete` at `discrete_version` 2; `SD_ENGINE == 3`; a board
  stamped 2 returns `"engine updated"`; and `ENGINE` is still **6**, so
  Classic's engine did not move.
- **Contract change.** A served board's numbers mean something new, so the
  boards built under the old scorer must be invalidated rather than served
  beside the new ones.
- **Not a regression.** Every old clause survives; the new ones add the sheet
  (which previously stamped nothing at all), the exact version, and the explicit
  assertion that Classic's engine is unchanged.

### Z2.7. My own call-site count, which counted comments

- **Old assertion.** `_dfs_src_p.count("nfl_dfs_sim.SD_DISCRETE") == 2` and
  `_nfd_src_p.count(...) == 3`.
- **New assertion.** `count("discrete=nfl_dfs_sim.SD_DISCRETE") == 2` in both
  files.
- **Contract change.** None. The guard was simply wrong: it counted every
  *mention* of the constant, comments included, so `dfs_tourney` returned 4 and
  the guard failed against correct code.
- **Not a regression.** It now counts **call sites**, which is what it was always
  trying to pin. A comment naming the constant can no longer fail it, and adding
  a third place that actually passes it still will.

### Z2.8. "The measured figure stands in its place"

- **Old assertion.** `"6.1x" in sd_money_gate(...)["why"]`.
- **New assertion.** `"x1.07 and x0.94"` in the gate's text, plus the 6.14x and
  2.36x ratios asserted on their own artifacts
  (`sd_discrete.json`, `sd_support.json`).
- **Contract change.** The gate's text now quotes the *live-board* measurements
  rather than the controlled one, because the live ones are what the gate is
  describing.
- **Not a regression.** The requirement was never "the text says 6.1x" — it is
  "no size is inferred from the grid spacing; a measured figure stands in its
  place". The assertion now follows the requirement instead of the old number,
  and the two controlled ratios are still pinned where they were measured. Three
  assertions where there was one.

### Z2.9. "With the field calibrated it would open" — wrong the day I wrote it

- **Old assertion.** `sd_money_gate(field_calibrated=True)["authoritative"] is
  True`.
- **New assertion.** `field_calibrated=True` alone returns **false**;
  `joint_validated=True` alone returns **false**; only both together return
  true; and an empty universe still refuses even then.
- **Contract change.** A fifth link, `showdown_joint_model_validated`, was added
  to the gate because closing the lattice link exposed that nothing was gating on
  the *football* model at all.
- **Not a regression.** This is the one repin where the **old assertion was
  unsafe**, not merely stale: it asserted that fitting public ownership alone
  should make the money columns authoritative, which would have announced
  authoritative EV over a joint distribution Stage 2B-2F measured as limited. The
  property worth keeping — that the gate is not welded shut — is asserted
  unchanged, now requiring both links. I wrote the unsafe version earlier in this
  same pass; the reviewer caught it before it shipped.

**No assertion was weakened to let failing code through.** Three guards failed
because my own edits moved a contract or my own guard was miscounted (Z2.1, Z2.2,
Z2.7); five were repinned because the promotion answered the question they were
holding open (Z2.3-Z2.6, Z2.8); and one, Z2.9, was repinned because it asserted
something that should never have been true. Every one of the nine asserts at
least as much as it did before, and Z2.9 asserts something safer.

---

## V. Production defaults after this pass

| | Value | Why |
|---|---|---|
| Showdown simulator | **`legacy-latent-discrete` v2** | promoted (section Y6), both showdown surfaces, one switch |
| Classic simulator | legacy | unchanged, byte-identical on a seeded digest |
| Showdown engine | `SD_ENGINE = 3` | separate from Classic's 6, which did not move |
| Showdown pool signature | rich | price, status and id sensitive |
| Classic pool signature | names only | deliberate; an hour of rebuild for a price tweak is a bad trade |
| Pre-lock refreshes | T-4h, T-2h, T-1h, T-25m | once each |
| Showdown money gate | **shut**, two links open | the field model (the elephant) and the joint football model |
| Classic behaviour | unchanged | proved by guard |

## U. Remaining limitations and what was not reached

- **S3 is fixed and promoted for Showdown** (sections Y2-Y6). The sentence that
  stood here -- that a discrete legacy scorer "changes Classic" -- was wrong, and
  section Y2 says why.
- **Two money links remain open, and neither is the lattice.** The field model is
  a placeholder from one published contest, and the Showdown **joint football
  model** is knowingly limited -- legacy-latent's teammate and opposing-side
  covariance are what Stage 2B-2F measured as wrong, and a single-game roster is
  more exposed to them than a classic one. Promoting the discrete scorer fixed
  the scoring rules and validated nothing about the football.
- **The live severity of the lattice defect is known for two boards only.** Both
  were primetime, both had ~88,000 entries and a field the model puts at 84-97%
  shared first place. A smaller or flatter contest could behave differently, and
  nothing here measures that.
- **The portfolio shift toward balanced builds is not understood.** It appeared
  on both tested slates and exceeded a single structural control. Two slates and
  one control cannot say whether it generalises, and it is not claimed to.
- **The kicker's pin has a floor, and it overshoots rather than multiplying.**
  An extra point is one per touchdown his own offense scored in that world, so
  it is not available to scale; a projection below what those extra points are
  worth cannot be reached by kicking less. Measured at the degenerate end: a
  kicker asked for 1.0 lands on 3.4. Real showdown kickers project 5-9 and land
  within 0.10 points, so this is a bound worth stating rather than a defect in
  practice.
- **S4 portfolio cross-fit** was not reached. Showdown still selects and
  scores on the same worlds, and still surfaces top 1% rather than top 0.1%.
- **S5 shadow-forbidden rule study** was not reached. The roughly 3% survival
  rate under owner rules is unmeasured by me and unattributed to named rules.
- **S6 multi-contest scoring** was not reached; one board, one contest.
- **S7 historical field calibration** was not reached; no historical Showdown
  ownership data has been located or ingested, which is why the field link is
  open rather than merely unvalidated.
- **S8 fast-path cleanup** was not reached; the quick builder is unlabelled.
- **S9 constrained-v2** was not started, correctly, since it sits behind all
  of the above.

## S4. The portfolio objective: Top 1% or Top 0.1%?

Baseline `9d6ffaa`. Portfolio methodology only; the money gate stays shut
throughout and no dollar figure decides anything below.
`research/s4_ties.py`, `research/s4_crossfit.py` →
`research/data/s4_ties.json`, `s4_crossfit.json`.

### A. The old methodology

| | |
|---|---|
| Candidate enumeration | `enumerate_showdown`: salary, team, position, roster rules. **World-independent** |
| Lineup metrics | `run(W, X, f, grid)` over **all N worlds** |
| Shortlist | the 1,200 best by Top 1%, over **all N worlds** |
| Greedy selection | `portfolio(...)` over **all N worlds** |
| Reported coverage | `p_any_top1_pct` = the greedy's own coverage vector, over **all N worlds** |
| Objective | Top 1%. `portfolio()` never passes `rank`, so `top01` has never driven a board |
| Union | row-wise max (shared field), correct since the Classic pass |
| Top-q | `P(A ≤ places−1)`, `A` = count **strictly above**; Poisson where its mean is small, Normal on the rank otherwise |

### B. Leakage: confirmed, and doubled

One world set feeds the shortlist, the greedy and the reported number. Every
portfolio coverage figure on a served Showdown board is in-sample. **Measured
size of that optimism**, same portfolio graded both ways:

| metric | in-sample | held-out | optimism |
|---|---|---|---|
| Top 1% | 0.60660 | 0.60872 | −0.3% |
| Top 0.1% | 0.20051 | 0.19249 | **+4.2%** |

The sparse tail metric is the one in-sample evaluation inflates; the dense one
barely notices. Enumeration is proved world-independent behaviourally —
overwrite every simulated score and the legal universe is byte-identical — so
only the shortlist and the greedy needed a fold.

### C. Cross-fit design

One pool of 8,000 worlds, fold A = first 4,000, fold B = second 4,000, disjoint
and guarded. Select on A score on B, select on B score on A, reported apart. Two
boards × two seeds × two directions = 8 cells.

**The shortlist follows the objective**: the Top-1% experiment shortlists by
Top 1% on its selection fold, the Top-0.1% experiment by Top 0.1% on its own.
Running both through a Top-1% shortlist would hand the tail objective a
candidate set preselected for its opponent. The shortlist is binding
(1,200 of 23,820 enterable), so this matters.

### D. Shared-field coverage semantics

Coverage is the mean over scoring worlds of the **row-wise maximum** over the
selected prefix — one contest, one realised field, our entries nested. Validated
against an **empirical** contest rather than against itself: opponents drawn
binomially and ranked exactly, across masses from comfortably-in to
comfortably-out including the steep transition, worst absolute disagreement
**0.0069**. Three entries scoring identically qualify as often as one of them
does (0.43588 against an empirical 0.44077), where the independent-fields form
claims 0.82048.

**Opponent count.** A submission of exactly `k` entries faces `C − k` public
opponents; the grid for size `k` is built at `C − k + 1` so its internal count is
exactly that, with the qualifying place counts asserted unchanged (882 and 88 at
every `k` on these boards). The 20-entry portfolio uses `C − 20` throughout
selection **and** scoring, so the matrix does not shift as the greedy grows.
Production uses `C − 1`, which counts our own 19 other entries as public
opponents; for an at-least-one event they cannot outrank our best, so `C − 1`
**understates** coverage. Measured, not sized from `19/C`: **1.3e-5 absolute,
0.012% relative.** Too small to move production for.

### E. Tie-at-cutoff semantics

Top-q counts only mass strictly above, so tied opponents are treated as
finishing behind us, while `win_sole` and `ev` in the same grid are tie-exact.
A real inconsistency, measured with an exact bracket (independent Poissons add,
so the pessimistic end is the same table at `F' = 1 − (1−strict)·exp(−level)`):
**1.5% of the best lineup's Top 1%, 1.1% of its Top 0.1%**, same best lineup,
rank correlation 0.995+. Documented, not changed; `SD_ENGINE` stays 3. Every S4
result below is reported at **both exact endpoints**.

### F–G. Top 1% vs Top 0.1%, both directions

Held-out Top-0.1% coverage at 20 entries, paired bootstrap over scoring worlds:

| board | seed | dir | Top1-sel | Top0.1-sel | Δ optimistic | Δ pessimistic |
|---|---|---|---|---|---|---|
| DEN@KC | 1 | A→B | 0.15336 | 0.16885 | **+0.0155** ✓ | **+0.0191** ✓ |
| DEN@KC | 1 | B→A | 0.15934 | 0.17882 | **+0.0195** ✓ | **+0.0246** ✓ |
| DEN@KC | 2 | A→B | 0.15803 | 0.18543 | **+0.0274** ✓ | **+0.0312** ✓ |
| DEN@KC | 2 | B→A | 0.19295 | 0.19295 | +0.0000 — | +0.0054 — |
| DAL@NYG | 1 | A→B | 0.16902 | 0.19249 | **+0.0235** ✓ | **+0.0307** ✓ |
| DAL@NYG | 1 | B→A | 0.16442 | 0.19047 | **+0.0261** ✓ | **+0.0300** ✓ |
| DAL@NYG | 2 | A→B | 0.16034 | 0.19260 | **+0.0323** ✓ | **+0.0426** ✓ |
| DAL@NYG | 2 | B→A | 0.16253 | 0.18763 | **+0.0251** ✓ | **+0.0361** ✓ |

✓ = 95% CI excludes zero. **Eight of eight positive, seven of eight stable at
both exact endpoints.** The null cell is a genuine null — point estimate
3.6e-06, portfolios 12/20 overlapping, not a collapsed calculation.

**The reverse cost, and it is larger.** Held-out Top-1% coverage, all eight
cells stable:

| | Top1-selected | Top0.1-selected | Δ |
|---|---|---|---|
| held-out Top 1% | ~0.603 | ~0.555 | **−0.041 to −0.058** |
| held-out Top 0.1% | ~0.165 | ~0.186 | **+0.015 to +0.043** |

Selecting for the tail buys about +0.025 of Top-0.1% coverage and sells about
−0.050 of Top-1% coverage. In absolute coverage the sale is bigger than the
purchase.

### H. Portfolio-size curves

Held-out Top-0.1% delta, averaged over the eight cells, each size scored against
`C − k` opponents as a hypothetical submission of exactly `k` entries:

| k | Top1-sel | Top0.1-sel | Δ |
|---|---|---|---|
| 1 | 0.01554 | 0.02323 | +0.0077 |
| 5 | 0.06923 | 0.07452 | +0.0053 |
| 10 | 0.10952 | 0.12090 | +0.0114 |
| 20 | 0.16500 | 0.18616 | +0.0212 |

The advantage is present throughout construction, not only at the twentieth
lineup.

### I–J. Uncertainty and seeds

Paired bootstrap (2,000 resamples) over scoring worlds, which removes the noise
both portfolios share — the only reason a ±0.25-point-scale difference is
readable at all. Two independent seeds per board; the single unstable cell is one
seed in one direction on one board, and every other cell in that group is stable.

### K. Composition (DEN@KC, seed 1, A→B)

| | Top1-selected | Top0.1-selected |
|---|---|---|
| Captains | Nix 8, Walker 4, Mahomes 4, Waddle 3 | Nix 9, Walker 3, Sutton 1, Waddle 1 |
| Structures | 5-1: 9, 4-2: 8, 3-3: 3 | 5-1: **14**, 4-2: 6, 3-3: **0** |
| K slots | 6 | **12** |
| DST slots | 13 | 10 |
| Salary left | median 300, min 0 | median 300, min 0 |
| Overlap | 10/20, Jaccard 0.333 | duplicates: none either side |

The tail objective concentrates on one team and doubles its kicker usage — a
coherent tail story, on one board and one direction, claimed no further.

### L. Fold-to-fold selection stability

| board | seed | Top 1% | Top 0.1% |
|---|---|---|---|
| DAL@NYG | 1 | 11/20 | 14/20 |
| DAL@NYG | 2 | 12/20 | 11/20 |
| DEN@KC | 1 | 13/20 | 12/20 |
| DEN@KC | 2 | 15/20 | 11/20 |

Comparable. **No instability pathology.** A 600-world pilot showed 2/20 for the
tail objective and pointed the comparison the wrong way entirely; that was
estimator noise on an 88-in-88,235 event, and it is the reason the pilot was not
read as a result.

### M. Field-concentration sensitivity — the finding that decides it

Same football worlds, the field's one knob moved either side of its placeholder.
**Scenarios only; none may become a default and none is a calibrated
alternative.**

| concentration | β | A→B opt | A→B pes | B→A opt | B→A pes |
|---|---|---|---|---|---|
| 0.001 flatter | 0.377 | +0.0227 ✓ | +0.0237 ✓ | +0.0194 ✓ | +0.0242 ✓ |
| 0.002 production | 0.454 | +0.0155 ✓ | +0.0191 ✓ | +0.0195 ✓ | +0.0246 ✓ |
| 0.004 concentrated | 0.543 | **+0.0008 ✗** | +0.0104 ✓ | +0.0129 ✓ | +0.0242 ✓ |

No sign flips. But the gain **halves monotonically** as the field concentrates —
about +0.022, +0.018, +0.009 at the optimistic convention — and at the
concentrated end it loses significance under production's own tie convention.
The effect size depends materially on the single parameter that is a placeholder
taken from one published 2021 contest.

### N. Money diagnostics

None computed. The gate is shut on `field_model_calibrated` and
`showdown_joint_model_validated`, and the decision below rests on held-out
coverage alone.

### O. Production-objective decision: **unchanged**

Selecting for Top 0.1% **does** improve held-out Top 0.1% coverage. That is
established about as well as this stage can establish anything: every cell
positive, seven of eight stable, both boards, both seeds, both fold directions,
both exact tie conventions, and the advantage builds across portfolio sizes
rather than appearing at the last lineup.

It is still **not enough to switch production**, for two reasons that are about
the decision rather than the measurement:

1. **The trade is not free and coverage cannot price it.** +0.025 of Top-0.1%
   coverage costs −0.050 of Top-1% coverage, stable in all eight cells. Which
   side wins depends on how much more a higher finish pays, weighted by finish
   probabilities you can trust — and the money gate is shut precisely because
   those probabilities are not trustworthy. Using experimental dollars to break
   the tie is exactly what this stage forbids.
2. **The effect size rides on the uncalibrated knob.** It halves across a
   plausible field-concentration range and goes insignificant at the concentrated
   end under the production convention. S7 is therefore a genuine prerequisite,
   not a formality.

So: **Top 1% remains the production selection objective.** Nothing in
`dfs_tourney` changes. This is recorded as research, and it has made S7 the
next thing worth doing rather than a box to tick.

What would change the decision: a calibrated field model (S7) that lands at or
flatter than the current placeholder, plus a payout-weighted comparison once the
money gate can open.

### P. Remaining limitations

- **Two boards, both primetime, both ~88,000 entries, both week 2.** Smaller or
  flatter contests are unmeasured.
- **One simulator.** `legacy-latent-discrete` v2 is a correct implementation of
  a model whose covariance structure Stage 2B–2F measured as limited. A better
  football model could move the objective comparison.
- **4,000 scoring worlds per cell.** Enough for the paired difference, not enough
  to quote an absolute tail coverage as calibrated. The absolute numbers here are
  model-conditional; only the paired differences are the result.
- **The 1,200-lineup shortlist is binding** (of 23,820 enterable). Whether a
  larger shortlist would favour either objective differently is unmeasured.
- **The tie convention is still inconsistent in production** — documented, 1.1%
  wide, deliberately not fixed.

---

## X. Recorded for S7/S9: a validation flag must name what it validated

Both remaining money links are booleans, and neither says what it is a statement
*about*. `SD_MONEY_JOINT_MODEL_OK` will one day be set by validating a specific
simulator at a specific version on a specific data split; if it is stored as a
bare `True`, then serving a later model silently inherits evidence that was never
collected about it. `SD_MONEY_FIELD_CALIBRATED` has the same shape — "calibrated"
is only meaningful against the ownership data and the season it was fitted on.

This is the same class of defect as finding S1: a board that could not be told
its engine had moved. The fix belongs in the S7/S9 provenance work — both links
recorded as `(model, version, split, date)` and compared against
`nfl_dfs_sim.sim_stamp()` at gate time, rather than as flags. It is **not** built
now, on purpose: inventing that contract before S7 has any calibration data to
shape it would be guessing. Recorded in `dfs_tourney.py` beside the constant so
the next pass cannot miss it.

---

## W. Tests

Full suite with numpy: **1,974 passed, 0 failed**. With numpy hidden:
**1,947 passed, 0 failed**. Both exit codes checked explicitly, and the first
attempt at that check was worthless: a `grep` appended after the suite in the
same compound command meant the shell reported the grep's status, so a red
suite (1,972 passed, **2 failed**) came back as exit 0. Read the log, not the
status line. pyflakes clean across root and research modules. Secret scan
clean.

Five pre-existing guards failed when the contract changed and were repinned to
the new contract rather than the code being bent back: the two PC task pins,
the rebuild-reason fixture, the engine guard, and the artifact guard. One
guard I had written myself in S2 failed in S3 when the lattice link was added,
and was corrected to assert the conjunction rather than a single key.

## Y. Recommended next, separated from production fixes

1. **Decide the lattice question deliberately.** It is the single largest
   defect in the Showdown numbers and both routes out of it are decisions
   about Classic and about promotion, not implementation details.
2. **S4 before S5.** A cross-fit split is cheap and makes every later strategy
   claim admissible; the rule study is worth little without it.
3. **S7 is a data question, not a modelling one.** Until historical Showdown
   ownership exists in the repo, the field link cannot close, and no amount of
   making the current slate look sensible substitutes for it.
