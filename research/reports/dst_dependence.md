# The dependence `DST-OPP` turns on, measured

**Measured 2026-09-18 from `research/data/player_weeks_2022_2025.csv.gz` (2,173
team-weeks with a passing offense, 2022–2025) and from a components-bearing run
of the served simulator on the pinned DEN @ KC game (6,000 worlds); re-run
2026-09-19 with the like-for-like rows the reviewer asked for.** Artifact:
`research/data/dst_dependence.json`, provenance clean at `338802c`.

## The question

`DST-OPP` bans rostering the defense that faces your captain. Captain Mahomes
plus the DEN defense: DEN DST's DraftKings score is a function of **KC's own**
offensive output — the giveaways KC commits, the points it allows to be scored
against it, the yards it moves. So the covariance the rule turns on is a side's
production against that same side's giveaways, in the same game.

Two things must be kept apart, and the first version of this report did not
keep them apart well enough. **The served simulator does not have zero
captain/DST covariance from turnovers**: it draws each interception and lost
fumble once and shares it — the quarterback loses a point and the opposing
defense gains two — so the mechanical anti-correlation is in the model. What
it draws at the raw mean with no game, quarterback or script factor
(`ints = _pois(m["int"])`, `fums = _pois(m["fum"])`) is the **turnover process
itself**: whether a productive offense gives the ball away more or less
often. That football-state dependence is what this measures — and, since
history's DraftKings score carries the same −1 per giveaway, it can only be
measured like for like with the penalty added back on **both** sides.

## History, 2022–2025

| Relationship | Pearson r | 95% CI | Spearman | Per-unit slope |
|---|---|---|---|---|
| Team giveaways vs team offensive DK points, raw | **−0.113** | [−0.154, −0.071] | −0.120 | −0.052 giveaways per 10 DK points |
| **…with the −1 per giveaway added back** (the football dependence) | **−0.067** | [−0.108, −0.025] | −0.077 | |
| QB1 giveaways vs QB1 DK points, raw | −0.154 | [−0.194, −0.115] | −0.154 | −0.183 per 10 DK points |
| **…with the −1 per giveaway added back** | **−0.036** | [−0.076, +0.004] | −0.041 | not distinguishable from zero |
| QB1 interceptions vs QB1 DK points | −0.144 | [−0.184, −0.102] | −0.143 | |
| QB1 interceptions vs passing attempts | **+0.197** | [+0.158, +0.236] | +0.198 | **+0.200 INT per 10 attempts** |
| Team giveaways vs passing attempts | +0.203 | [+0.163, +0.244] | +0.212 | |
| Team giveaways vs total yards | −0.037 | [−0.077, +0.006] | −0.036 | not distinguishable from zero |
| Team giveaways vs **opponent's** offensive DK | +0.033 | [−0.008, +0.072] | +0.031 | not distinguishable from zero |
| Team offensive DK vs opponent's offensive DK | +0.180 | [+0.134, +0.224] | +0.170 | the shootout coupling |

By quartile of QB1's DraftKings points, his giveaways run **1.14 → 0.94 → 0.87 →
0.71** raw — but **0.98 → 0.93 → 0.89 → 0.86** once the penalty is added back.
Most of the raw pattern is the scoring identity itself (a giveaway costs a
point, so low-scoring games mechanically carry more of them), and the served
simulator carries that identity too. At the quarterback level the football
dependence that remains is −0.036 with an interval through zero. At the team
level, which is what the opposing defense actually scores off, a dependence
remains: **−0.067, interval clear of zero**, about 60% of the raw figure.
Volume still raises interceptions (+0.20 per 10 attempts); production lowers
giveaways on net only at the team level, and modestly.

The cross-side channel ("a trailing offense throws picks") is not there at the
team-week level: giveaways against the *opponent's* production is +0.033 with a
confidence interval through zero.

## The served simulator, same relationships

On the pinned DEN @ KC game, 6,000 worlds, `legacy-latent-discrete`:

| | DEN (Bo Nix) | KC (Patrick Mahomes) | History |
|---|---|---|---|
| Team giveaways vs team offensive DK, raw | −0.038 | −0.044 | −0.113 |
| **…with the penalty added back to both** | **−0.005** | **−0.004** | **−0.067** [−0.108, −0.025] |
| QB1 giveaways vs QB1 DK, raw | −0.113 | −0.141 | −0.154 |
| **…with the penalty added back to both** | **−0.004** | **−0.016** | **−0.036** [−0.076, +0.004] |
| QB1 interceptions vs passing yards | −0.011 | +0.002 | +0.055 |

The penalty-removed rows are the like-for-like comparison. At the quarterback
level the served model and history agree: the football dependence is not
distinguishable from zero in either. At the team level history carries a small
negative dependence and the served model carries none.

## What it means for `DST-OPP`

Historical evidence indicates that the served simulator omits state-dependent
turnover behaviour at the team level and **likely understates part of the real
anti-correlation between offensive production and opposing-defense scoring**.
That makes the S5 benefit of removing `DST-OPP` (+0.047 held-out Top-1%)
**suspect in the direction of over-valuation** of captain-plus-opposing-defense
builds. It does **not** identify how much of the +0.047 would survive a
corrected joint model: the missing dependence is −0.067 at the team level, one
of the defense's inputs, and the rule's direct object — captain-role DK against
the opposing DST's DK — cannot be measured from this table. The rule is kept on
that basis; an earlier version of this report said the +0.047 was "on this
evidence an over-estimate" and that the quarterback's giveaways fall 40% with
production, and both went farther than the like-for-like numbers allow.

Limits. Giveaways are one of three defense inputs; sacks and points allowed
are not in the table (the simulator couples points allowed to the offense
in-world; sacks it does not model at all). The DST score itself, and so the
direct captain-vs-opposing-DST relationship by role, needs a team-week defense
source that is not in the repository. 2025 was the Stage 2E holdout and is
used here only to describe a relationship, never to fit one. None of this is a
promotion, a rule change or a money conclusion.
