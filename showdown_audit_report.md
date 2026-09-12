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

One finding is worse than the review framed it. Showdown's first-place column
is a sole-win probability, so it is decided by how often scores tie, and 47%
of the simulated scores cannot occur under DraftKings scoring at all. That is
measured, in every position, kickers and defenses included.

The money columns now carry a gate. It is shut, for two independent reasons.

No strategy default changed. No simulator was promoted. Classic is untouched
except through shared code, and the shared change is proved inert for Classic
by guard.

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

**What this changes.** The lattice defect is a **money** defect, not a
strategy one, on the evidence so far: the ranking the owner reads did not move
in a single slot, while the payout moved about 6% and the tie rate by 6x. That
is consistent with the money gate being shut and the ranks being the thing to
read.

**Not promoted.** `discrete` defaults to False in both places it exists, rides
in the pool's cache key, and neither `build_nfl_showdown` nor
`build_nfl_classic` accepts it at all, so no production builder can be handed
it even by mistake. The Classic path is byte-identical: the discrete branch is
entered only on an explicit argument no production caller passes.

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

## V. Production defaults after this pass

| | Value | Why |
|---|---|---|
| Showdown simulator | legacy | unchanged; promoting anything is not this pass's call |
| Showdown engine | `SD_ENGINE = 2` | separate from Classic's 6 |
| Showdown pool signature | rich | price, status and id sensitive |
| Classic pool signature | names only | deliberate; an hour of rebuild for a price tweak is a bad trade |
| Pre-lock refreshes | T-4h, T-2h, T-1h, T-25m | once each |
| Showdown money gate | **shut**, two links open | lattice and field model |
| Classic behaviour | unchanged | proved by guard |

## U. Remaining limitations and what was not reached

- **S3 now has a working fix that is not yet promoted.** The sentence that
  stood here -- that a discrete legacy scorer "changes Classic" -- was wrong,
  and section Y2 says why: the mode is a separate argument Classic never
  passes, and the Classic path is byte-identical on a seeded digest of the
  whole pool. `DISCRETE_VERSION` 2 puts the entire Showdown pool on the legal
  support (section Y4). What remains is the decision to serve it.
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
