# Coaching engine R6: the selectively drifting league

2026-11-27. `nfl_coach_sim_r6.py` (`coach-r6.0`) is experimental and research only: no board, viewer, DFS world
or predlog bucket reads it. It is built on R5. R1-R5's files and registrations are untouched (guard N6-0).

The owner, after the evolution study (`research/reports/nfl_coach_evo.md`): "Approved: build R6 using the
selectively drifting training architecture. Clock management, running back target share and quarterback
substitutions use the supported drifting-league methods. Play calling and no-huddle retain full-history
training. ..."

## A. What R6 changes (`research/nfl_coach_r6_fit.py` -> `research/data/nfl_coach_r6_tables.json`)

Each drifting component is a league tendency on top of a full-history reference. It follows the evo study's
season-level random walk, with the innovation variance fitted by marginal likelihood (Kalman filter) on 2016 to
2026 week 12, and is forecast for 2027.

| component | how R6 moves it | season offsets 2016 -> 2026 | 2027 forecast | in the engine |
|---|---|---|---|---|
| **clock** | log seconds between a run and the next snap, in R1's unhurried phases (where R1's pace hook acts), against the full-history phase x score means | -0.012 -> +0.020 (2025), +0.010 (2026); tau 0.01 | +0.0104 | the pace multiplier m_clock = exp(0.0104 - R1's 2016-2023 level -0.0037) = **1.014** on both sides, through R1's own `tempo_mult` |
| **backs' target share** | the evo T4 table (a target to a running back), a logit shift | +0.14 (2017) -> -0.13 (2024), -0.08 (2026); tau 0.08 | -0.079 | every running back's target odds x exp(-0.079 - R2's 2016-2023 level +0.030) = **x0.896** (`Team`) |
| **pull** | R3's s6 drive hazard, the shift measured against **R5's own hazard** | -0.24 to -0.56 (2016-19), +0.10 to +0.13 (2024-25), -0.06 (2026); tau 0.16 | **+0.0095** | R5's intercept + 0.0095 |

- **The call and no-huddle tables are R1's, unchanged.** R6 passes R1's own tables (N6-1), so their difference
  from R5 is identically 0.
- **The clock is not forked.** R1's `_gap` already scales a running clock's gap by the side's pace (`tempo_mult`)
  in the unhurried phases. R6 sets that pace before the game, in its own copy of R5's loop, and R1's code is
  neither copied nor edited (N6-0, N6-5). The cost: the drift acts only where R1 lets pace act, which is the
  same place it was measured.
- **Two honest caveats:**
  - **The pull barely moves.** R5's hazard was already fitted with recent seasons up-weighted (half-life 2,
    anchored 2025), and 2026 weeks 1-12 dipped (-0.06). So the random walk's forecast against R5 is +0.0095
    logit: R6's pull is R5's to within 1%. That is the method's answer, not a choice.
  - **Rosters may already carry the backs' drift.** A roster whose target shares are this season's usage already
    carries part of that drift. The x0.896 is the league's, and the registered test reads it at the league level
    (the evo table), not through the deal.
- **Parity:** with `drift=False` (the three components set back), R6 plays R5's game draw for draw: boxes, logs,
  pulls and lines (N6-2). The backs' odds move by exactly exp(delta) and carries never move (N6-3).
- **Conservation** is exact, with the drift on (N6-4).

## B. Development (walk-forward, `research/data/nfl_coach/r6_fit.json`)

Each fold S = 2019-2025 plus 2026 weeks 1-12 fits the references on the seasons before S, then the offsets, then
the forecast, and scores S. For the pull, the development reference is a flat full-history hazard (the evo
protocol), because R5's hazard has seen 2016-2025.

| | clock | backs' targets | pull | pooled (2,035 games) | 2025 + 2026 folds (436) |
|---|---|---|---|---|---|
| gain, nats a game | +0.055 +/- 0.009 (z 6.2) | +0.022 +/- 0.006 (3.9) | +0.006 +/- 0.002 (2.5) | **+0.082 +/- 0.011 (z 7.7)** | +0.161 +/- 0.035 (4.7) |

The pooled gain is negative in the 2019 and 2020 folds. From 2021 it is positive, and largest in 2024-25.

## C. The 2027 family with R6 (`research/nfl_coach_family_2027_r6.py`, `research/data/nfl_coach/family_2027_r6.json`)

**The definition:** the primary criterion of every coaching read whose primary window is the 2027 regular season.

- **Members:** R2-H2, R4 (its window adds 2026 weeks 13-18), R5's amendment and R6's primary.
- **Not members:** C4 stays in R1's owner-approved family. C3-N is not coaching. The 2028 replications are not
  members.

**The correction:** Holm at a family-wise one-sided 5%, with steps at z 2.241, 2.128, 1.960 and 1.645.

- **Uncorrected,** with four tests at z > 2, the family-wise rate would be 8.8% if the tests were independent.
- **Every original verdict stands under its own rule.** The family statement is reported beside it.
- **R5's amendment pins the three-test file** (`family_2027.json`), which stands as registered. This file adds R6
  beside it.

**Power:**

| test | effect / SE | own rule (z 2) | Holm strictest (2.241) | Holm last (1.645) |
|---|---|---|---|---|
| R2-H2 (development mean) | 3.23 | 0.89 | 0.84 | 0.94 |
| R2-H2 (2025-26 level) | 1.92 | 0.47 | 0.38 | 0.61 |
| R4 | 3.20 | 0.88 | 0.83 | 0.94 |
| R5 (~45 pulls) | 3.35 | 0.91 | 0.87 | 0.96 |
| R5 (30-game floor) | 2.74 | 0.77 | 0.69 | 0.86 |
| **R6 (development mean, pull excluded)** | 2.64 | 0.74 | 0.65 | 0.84 |
| **R6 (2025-26 level)** | 3.68 | 0.95 | 0.92 | 0.98 |

**R6's correlation with the others** on the development games, as the correlation of the tests' means (R6 a
game, the others a team-game, summed within the game):

| pair | 2025 | 2026 |
|---|---|---|
| R6 x R2-H2 | -0.09 | +0.01 |
| R6 x R4 | -0.04 | -0.04 |
| R6 x R5 | +0.05 | +0.03 |

All are small. Every other 2027 read's units sit inside R6's games, though, so the tests share games. A joint
pass is not independent confirmation, which is why R6 has a 2028 replication.

## D. The registration (`research/data/nfl_coach/prereg_r6.json`, block 7ef4defe)

It was written before any 2027 game exists, with the components, the implementation and the criteria frozen. It
pins R6's files, the evo study (its rule and its rejected all-table verdict, unchanged; N6-8), the family file and
the R1-R5 engine files R6 plays.

- **Primary:** the 2027 regular season, read on or after 2028-01-13 06:00 UTC after R1's read.
- **Replication:** the 2028 regular season at the SAME frozen tables, read on or after 2029-01-16 after the
  primary.
- **Both:** at least 200 games, no simulation, and the same statistic (`nfl_coach_r6_fit.window_gains`), each
  component's held-out log score with its forecast offset minus without it, per game.
- **Script:** `research/nfl_coach_r6_read.py primary|replication`.

The criteria (all must hold; N6-12):

1. The R6 guards exit 0.
2. The pooled gain is above 2 SE (games).
3. Each drifting component is above -1 SE.

**Power:**

| | development mean | 2025-26 level |
|---|---|---|
| criterion 2 | ~0.74 | ~0.95 |
| criterion 3, clock and backs | ~0.99 or better | ~0.99 or better |
| criterion 3, the pull's guardrail | ~0.84 | ~0.84 |
| jointly | ~0.65 | ~0.79 |

The pull's guardrail holds only about 84% of the time because its shift is about 0, and its guardrail is about a
chance event.

**Dependence:** R6 and R5 share R5's hazard and second unit, and every 2027 read's games. The evo study read
2019-2026 week 12, so no development figure is a test.

## E. Files and guards

| file | what |
|---|---|
| `nfl_coach_sim_r6.py` | the engine |
| `research/data/nfl_coach_r6_tables.json` | the three components, their offsets and forecasts, the frozen references |
| `research/nfl_coach_r6_fit.py` | the fit, the walk-forward and the registered statistic; `r6_fit.json`, `r6_dev_games.json` |
| `research/nfl_coach_r6_prereg.py`, `research/data/nfl_coach/prereg_r6.json` | the registration |
| `research/nfl_coach_r6_read.py` | both reads (not run) |
| `research/nfl_coach_family_2027_r6.py`, `research/data/nfl_coach/family_2027_r6.json` | the four-test family |
| `tests/guards_auto_nfl_coach_r6.py` | 14 guards, section N6 (AUTO) |

## F. Next: defensive coaching intelligence -- a plan only, nothing built

The owner's rule: "I would rather have a smaller, empirically supported defensive model than a sophisticated-looking
system built on invented assumptions."

### F.1 Data, and what limits it

| source | what it gives | licence and limits |
|---|---|---|
| nflverse play-by-play | every play's situation, the defending team, run/pass, sacks and hits, air yards, both teams' timeouts, the head coaches | CC BY 4.0 |
| FTN charting via nflverse | per play: blitzers, pass rushers, men in the box, play action, screen, RPO | CC BY-SA 4.0 (credit "FTN Data via nflverse"; share-alike on derived data); **2022 on only** |
| nflverse participation | defensive personnel, coverage and man/zone fields | each field's original source must be checked before use. Under the licence default (PFR and NGS display-only, never fitted), an NGS-era field stays out; an FTN-era field (2023 on) is usable with credit. Published after the postseason, so the current season is never in it. |

**No lawful structured source gives defensive coordinators or play-callers** (R2's audit found none for offences
either). Persistence can therefore be tested only:

- by team-season;
- through head-coach continuity;
- later, by coordinator, if the owner supplies a typed coordinator / play-caller list. That is his 2026-11-26
  "future enhancement".

### F.2 Item by item

The first test is always the same two steps, in order:

1. **Persistence:** split-half within season and year over year, against a shuffled-team placebo.
2. **Only if (1) holds, a decision model:** fit on 2022-2024, chosen on 2025 by a rule written first, checked on
   2026 weeks 1-12. A component enters an engine only after a registered prospective read.

| item (owner's priority) | lawful measure | plausibly persists with the coordinator / head coach? | first registered test |
|---|---|---|---|
| **Blitz frequency** (high) | FTN blitzers by down x distance x score x field zone | yes: a known scheme trait | (1), then the team-season situational blitz rate, shrunk, predicting the next game's blitz rate better than the league table (the decision's own log score) |
| **Pass-rush tendencies** (high) | FTN pass rushers (3 / 4 / 5+); sacks and hits | the rush count yes; sack results less (personnel) | (1) on the rusher-count distribution; then the rusher count as a feature in R1's sack and completion tables (does it beat the league table?) |
| **Run vs pass response** (high) | FTN box count against the offence's personnel and the situation | partly scheme, partly opponent | (1) on the box rate given offensive personnel; then whether the box tendency moves the offence's run success or call. Prior evidence: box added nothing past Sleeper's lines for backs' props (`nfl_matchup_model.md`), so expect small. |
| **Prevent defense** (high) | no label: behaviour when leading late (last 4:00, up 9+): rushers <= 3, the depth of completions allowed, yards after catch, the trailing offence's clock use | uncertain; may be league-wide | (1) on a late-lead posture index; then whether R1's late drive tables mis-predict trailing drives against high-posture defences. This is the late-state question R1's criterion 4 and C4 read, so the dependence must be stated. |
| **Red-zone defense** (high) | play-by-play: touchdown vs field goal per trip inside the 20; FTN blitz and box there | doubtful: red-zone touchdown rates are notoriously noisy | (1) first. The likely finding is "no persistence beyond the defence's overall level", and that is the answer if so. |
| **Halftime adjustments** (high research value) | play-by-play: the defence's second-half expected points allowed and success rate against first-half expectation, with regression to the mean modelled | test it: R2 found no halftime effect for offences | (1) on the "adjustment" as a repeatable trait, with placebo boundaries (Q1 -> Q2) |
| **Adaptation to opponent success** (high research value) | FTN box and blitz play by play within a game, after the opponent's success running or passing | R2 found real in-game adaptation for the offence's call (z 18, placebo 0) | the defensive mirror of R2-H2: the box (or blitz) decision after the 10th snap with the opponent's in-game success gap as a feature, against an order-shuffled placebo |
| **Defensive timeouts** (partly addressed) | play-by-play timeouts; R1 already plays them league-wide (`to_def`, by phase x score x timeouts) | a head-coach trait | (1) by head coach on timeout use over R1's table; only if persistent, a coach term |
| **Coverage schemes** (data-dependent) | participation coverage / man-zone, FTN-era only after the source check | yes (coordinator) | first the source and licence audit; then (1); prior evidence: the man/zone target split is real but added nothing past Sleeper |
| **Personnel packages** (data-dependent) | participation defensive personnel, after the source check | coordinator and roster | the source audit; (1) on nickel/dime use given the offence's personnel; then its effect on run/pass outcomes |

**What a first registration would cover.** It would take only the items that pass (1) and (2). It would be
registered on games no read has seen: 2028, or 2027 if the owner adds it to the 2027 family as a fifth Holm
member, with the dependence stated.

**A built-in caution.** FTN starts in 2022, so every defensive history is four seasons long. Expect wide
uncertainty and few components to survive. That is the smaller, empirically supported model the owner asked for.
