# The dependence `DST-OPP` turns on, measured

**Measured 2026-09-18 from `research/data/player_weeks_2022_2025.csv.gz` (2,173
team-weeks with a passing offense, 2022–2025) and from a components-bearing run
of the served simulator on the pinned DEN @ KC game (6,000 worlds).** Artifact:
`research/data/dst_dependence.json`, provenance clean at `b5cf744`.

## The question

`DST-OPP` bans rostering the defense that faces your captain. Captain Mahomes
plus the DEN defense: DEN DST's DraftKings score is a function of **KC's own**
offensive output — the giveaways KC commits, the points it allows to be scored
against it, the yards it moves. So the covariance the rule turns on is a side's
production against that same side's giveaways, in the same game. The served
simulator draws interceptions and fumbles at their raw mean with no game,
quarterback or script factor (`ints = _pois(m["int"])`, `fums = _pois(m["fum"])`),
so it models that covariance as zero apart from DraftKings' own −1 per giveaway
inside the score. Points allowed and yards it does couple to the offense
in-world; giveaways it does not. This measures the giveaway half.

## History, 2022–2025

| Relationship | Pearson r | 95% CI | Spearman | Per-unit slope |
|---|---|---|---|---|
| **Team giveaways vs team offensive DK points** | **−0.113** | [−0.154, −0.071] | −0.120 | −0.052 giveaways per 10 DK points |
| **QB1 giveaways vs QB1 DK points** | **−0.154** | [−0.194, −0.115] | −0.154 | −0.183 per 10 DK points |
| QB1 interceptions vs QB1 DK points | −0.144 | [−0.184, −0.102] | −0.143 | |
| QB1 interceptions vs passing attempts | **+0.197** | [+0.158, +0.236] | +0.198 | **+0.200 INT per 10 attempts** |
| Team giveaways vs passing attempts | +0.203 | [+0.163, +0.244] | +0.212 | |
| Team giveaways vs total yards | −0.037 | [−0.077, +0.006] | −0.036 | not distinguishable from zero |
| Team giveaways vs **opponent's** offensive DK | +0.033 | [−0.008, +0.072] | +0.031 | not distinguishable from zero |
| Team offensive DK vs opponent's offensive DK | +0.180 | [+0.134, +0.224] | +0.170 | the shootout coupling |

By quartile of QB1's DraftKings points, his giveaways run **1.14 → 0.94 → 0.87 →
0.71**: a big quarterback game carries about 40% fewer giveaways than a bad one.
By quartile of his attempts, interceptions run 0.49 → 0.63 → 0.73 → 0.93. The
two pull opposite ways — volume raises interceptions, production lowers
giveaways — and production wins on net, which is what a captain-sized game is.

The cross-side channel ("a trailing offense throws picks") is not there at the
team-week level: giveaways against the *opponent's* production is +0.033 with a
confidence interval through zero.

## The served simulator, same relationships

On the pinned DEN @ KC game, 6,000 worlds, `legacy-latent-discrete`:

| | DEN (Bo Nix) | KC (Patrick Mahomes) | History |
|---|---|---|---|
| Team giveaways vs team offensive DK | **−0.038** | **−0.044** | **−0.113** |
| QB1 giveaways vs QB1 DK | −0.113 | −0.141 | −0.154 |
| …with the −1-per-giveaway penalty removed from the score | **−0.004** | **−0.016** | — |
| QB1 interceptions vs passing yards | −0.011 | +0.002 | +0.055 |

The penalty-removed row is the football dependence the model carries: **zero, as
the code says.** At the quarterback level the raw number lands near history by
accident — DraftKings' −1 per interception manufactures most of it. At the team
level, which is what the opposing defense actually scores off, the served
coupling is **about a third of the real one** (−0.04 against −0.11).

## What it means for `DST-OPP`

In real games, the worlds where the captain's offense produces are the worlds
where it gives the ball away **less**, so the opposing defense scores less in
exactly the captain's best worlds. A captain-plus-opposing-defense build is more
anti-correlated in reality than in the served model. **The model over-values
those builds.** Removing `DST-OPP` would admit lineups the model likes more than
the data say it should, so the +0.047 held-out Top-1% gain S5 measured for
removing the rule is, on this evidence, an over-estimate — the rule is
vindicated by history, not merely held for caution.

Limits. Giveaways are one of three defense inputs; sacks and points allowed
are not in this table (the simulator couples points allowed to the offense
in-world; sacks it does not model at all). The DST score itself needs the
team-week source. 2025 was the Stage 2E holdout and is used here only to
describe a relationship, never to fit one. None of this is a promotion, a rule
change or a money conclusion; it replaces a phrase in S5 section O reason 2
with a number.
