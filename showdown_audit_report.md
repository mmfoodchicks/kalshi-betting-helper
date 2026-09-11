# Showdown correction pass: what was audited, what was fixed, what was not

**Baseline** `a1c4836` (the frozen, audited Classic tree).
**This pass** stages S1, S2 and S3 of nine. S4 through S9 were not reached and
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
grid twice as fine as the real one makes exact ties roughly half as likely as
they truly are. The tie arithmetic is correct. What reaches it is not.

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
