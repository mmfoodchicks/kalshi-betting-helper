# Defensive coaching, step 1: which tendencies persist

2026-11-27. This is development only and nothing is built: R1-R6 are untouched (guard DEF-7), and a defensive
engine would be R7, on new files.

The owner: "Use a separate 2028 confirmatory test for R6 defensive intelligence rather than expanding the
existing 2027 shared-games testing family. Development and exploratory research can continue beforehand."
And: "I would rather have a smaller, empirically supported defensive model than a sophisticated-looking system
built on invented assumptions."

- **The rule:** `research/data/nfl_coach/def_rule.json`, committed alone (70278a5) before any number, with its
  sha256 pinned (DEF-1).
- **The study:** `research/nfl_def_persist.py`, writing `research/data/nfl_coach/def_persist.json`.
- **The data:**
  - **FTN charting via nflverse, 2022-2025** ("FTN Data via nflverse", CC BY-SA 4.0). Anything derived from it
    and published is share-alike, so `def_persist.json` carries the credit and the licence.
  - **nflverse play-by-play, 2016 to 2026 week 12** (CC BY 4.0), with R1's seal (DEF-5).
  - **Coaches:** the head coach is the only staff member with a lawful structured source. Coordinator
    continuity is not tested.

## A. How each tendency is measured, and tested

- **Residuals, not raw rates.** Every play's outcome is taken against the SAME season's league mean in its
  cell, so neither situation mix nor league drift reads as a team trait. The cells, by tendency:
  - **blitz and rushers:** down x distance x score x zone;
  - **box:** down x distance x zone x the offence's FTN backfield count x quarterback alignment, which is
    FTN's charting of the offence's personnel;
  - **timeouts:** half x score state;
  - **red zone, prevent and halftime:** the season's league rate (one cell).
- **Split-half:** odd-order against even-order games of each team-season, correlated across team-seasons. The
  one-sided permutation p comes from 2,000 within-season shuffles of team labels.
- **Year over year:** each franchise's season against the next, for all pairs, then apart for the same head
  coach and after a change.
- **The family:** Holm over the eight split-half tests at a family-wise one-sided 0.05.
- **What "persists" means:** Holm-significant split-half AND year-over-year p < 0.05. Halftime must also beat
  its Q1 -> Q2 placebo.

## B. Results

| tendency | split-half r (p) | Holm | year over year r (p) | same head coach r (pairs) | head coach changed r (pairs, p) | **persists** |
|---|---|---|---|---|---|---|
| **blitz frequency** (FTN) | **0.73** (0.0005) | yes | **0.57** (0.0005) | **0.64** (76) | 0.08 (20, 0.35) | **yes** |
| **pass rushers** (FTN) | **0.65** (0.0005) | yes | **0.50** (0.0005) | **0.55** (76) | 0.09 (20, 0.37) | **yes** |
| **box vs the offence's personnel** (FTN) | **0.50** (0.0005) | yes | **0.47** (0.0005) | **0.53** (76) | 0.10 (20, 0.45) | **yes** |
| **defensive timeouts, last 2:00** | **0.14** (0.002) | yes | **0.34** (0.0005) | **0.38** (242) | 0.22 (78, 0.03) | **yes** |
| red-zone touchdowns allowed | 0.06 (0.079) | no | 0.20 (0.0005) | 0.18 (242) | 0.22 (78, 0.02) | no |
| prevent (<= 3 rushers, last 4:00, up 9+) | 0.10 (0.17) | no | 0.18 (0.03) | 0.18 (72) | -0.20 (15) | no |
| halftime adjustment (EPA) | -0.04 (0.76) | no | -0.05 (0.82) | 0.01 (242) | -0.22 (78) | no |
| adaptation slope (box on the in-game gap) | 0.01 (0.50) | no | -0.01 (0.56) | -0.04 (76) | 0.21 (20) | no |

- **Adaptation at the league level is not real by the rule.**
  - **Real order:** the box residual on the offence's in-game success gap has a coefficient of +0.096 +/- 0.064
    (z 1.5, 111,448 plays).
  - **Order-shuffled placebo:** +0.063 +/- 0.061 (z 1.0).
  - The defence shows no in-game adaptation distinguishable from game-level association. This contrasts with
    the offence's call, where R2 found z 18.
- **The halftime placebo outran halftime itself.** The Q1 -> Q2 difference persists a little (split-half 0.15,
  p 0.004) while halftime does not (-0.04). Whatever moves between quarters is not a halftime adjustment.
- **Red zone is what the plan expected.** Within a season its touchdown rate does not persist (split-half 0.06).
  Across seasons it does (0.20), equally with or without a coaching change (0.18 vs 0.22). That reads as overall
  defensive quality and roster, not a red-zone coaching trait. By the rule it does not persist, and that is the
  answer for now.
- **Prevent** has too few plays: 520 team-games over four seasons, and only 15 pairs after a coaching change.

## C. With which staff continuity

The three FTN scheme traits are **staff traits**:

| trait | year over year, same head coach | after a head-coach change |
|---|---|---|
| blitz | 0.64 | 0.08 |
| pass rushers | 0.55 | 0.09 |
| box | 0.53 | 0.10 |

- The changed-coach pairs are few (20), so each "after a change" figure is wide, but the drop is consistent
  across all three.
- **This is a head-coach proxy.** A new head coach usually brings a new defensive coordinator, so it is
  consistent with the coordinator carrying the trait. No lawful source lets us say whether it is the head coach
  or the coordinator; the owner's typed coordinator list would.
- **Defensive timeouts** persist with the head coach (0.38) and partly across a change (0.22, p 0.03). That fits
  a head coach's own clock management, plus something about the situations a franchise's defence sees.

**One implementation note.** nflverse leaves `posteam` and `defteam` empty on timeout rows, and the first run
therefore read no defensive timeouts at all (every value was 0). The fix reads the possession off the game's
latest row that names it. It is a parsing fix: the rule's definition is unchanged.

## D. The 2028 confirmatory test (`research/data/nfl_coach/prereg_def_2028.json`, block 106dd77b)

**Two hashes, in this order:**

1. **Now, the FRAMEWORK** (before any defensive model exists). It fixes:
   - **Window:** the 2028 regular season, read on or after 2029-01-16.
   - **Family:** its own Holm family at family-wise one-sided 5%, never the 2027 one (DEF-8).
   - **Eligibility:** a component must have persisted here (blitz, pass rushers, box, defensive timeouts). It
     must then pass a development decision-model rule written before its numbers: fit 2022-2024, chosen on
     2025, checked on 2026 weeks 1-12 where the data exist.
   - **Statistic form:** each component's held-out decision log score under R7 minus the league table, per
     team-game, clustered by game.
   - **Criteria form:** R7's guards pass; each component clears its Holm step; none is below -1 SE.
2. **Later, the COMPONENT block,** when the components are chosen. That can only be after the decision-model
   development. It names the components, the R7 files, the read script and each component's power, and must be
   committed before **2028-09-01**. Without it, no confirmatory read happens, so nothing can be added after the
   2028 games start.

It is on the registry as `def_2028`.

## E. Next (development, then the owner's call)

Decision models, in this order, each with its rule written before its development numbers:

1. Blitz and pass-rush tendencies by situation, team-season shrunk.
2. The box against the offence's personnel.
3. The head-coach timeout term against R1's league table.

Then the consequence tests: does the team tendency improve R1's sack, completion and run-success tables over the
league table?

Red zone, prevent, halftime and in-game adaptation stay out unless new seasons change the answer. Coverage and
personnel packages wait on the participation-source audit.

## F. Files and guards

| file | what |
|---|---|
| `research/data/nfl_coach/def_rule.json` | the rule (70278a5, before any number) |
| `research/nfl_def_persist.py` | the study |
| `research/data/nfl_coach/def_persist.json` | every tendency's tests (CC BY-SA, derived from FTN) |
| `research/nfl_def_prereg_2028.py`, `research/data/nfl_coach/prereg_def_2028.json` | the 2028 framework |
| `tests/guards_auto_nfl_def.py` | 9 guards, section DEF (AUTO) |
