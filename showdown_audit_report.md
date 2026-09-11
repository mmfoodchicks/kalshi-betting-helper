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

- **S3 is diagnosed, not fixed.** The lattice defect stands. It needs either a
  discrete legacy scorer, which changes Classic, or the constrained model,
  which is not promotable here.
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

Full suite with numpy: **1,941 passed, 0 failed**. With numpy hidden:
**1,919 passed, 0 failed**. pyflakes clean across root and research modules.
Secret scan clean.

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
