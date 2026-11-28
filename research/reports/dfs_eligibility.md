# DFS eligibility: the depth-chart gate, three modes, and the house rules

2026-11-27. The owner asked us to reconsider the hard depth-chart exclusions on both the Classic and Showdown
boards. His example was Michael Pittman Jr. on DEN @ PIT: Sleeper lists him as WR4 after a long absence, so Vigil
keeps him out of its own lineups while the simulated field still rosters him.

**Production is unchanged.** The default mode is `conservative`, which is today's gate. No caller passes another
mode, and guard EL1 checks both. Everything in this report is descriptive, apart from the registered rule in
section 7.

## Summary

- **Three modes are built behind a switch** (`dfs_eligibility.py`, `dfs_tourney._apply_eligibility`):
  - *conservative*: today's gate.
  - *adaptive*: today's players, plus the depth-gated players whose workload gives a credible chance of a
    starter-level game. The model behind this is fitted on 2022-2024 and tested on 2025 (section 3).
  - *unrestricted*: today's players plus every depth-gated player.

  Both new modes widen only OUR pool, and only from the field's own extras. The field, the lineup universe and
  every field figure stay exactly the conservative board's (EL4). Hard exclusions hold in every mode (EL2). No
  projection moves (EL3).
- **Real results, 20 past showdowns.** Out of the 20 real hindsight-optimal lineups:
  - Conservative could build 6. Adaptive could build 8 and unrestricted 10.
  - Adding the six sub-1.0-point players the field never plays raises that to 13.
  - Lifting every house rule (with the depth gate kept) builds 12.

  The average best buildable lineup trailed DraftKings' optimum by 2.58 points under conservative. It trailed by
  1.82 under adaptive, 1.49 under unrestricted and 0.68 with the sub-1.0 players added.
- **Real results, 12 classic main slates.** Conservative could build the real optimum on 6 of 12, adaptive on 9
  and unrestricted on all 12. The mean shortfall was 3.08, 0.94 and 0 points.
- **These are optimum-coverage figures, not contest results.** No past board can be rebuilt with identical inputs:
  production boards are unseeded and their worlds are not kept. So no mode's chosen lineups can be replayed on
  history. Section 4 runs the controlled comparison on DEN @ PIT's own simulated worlds, with independent worlds
  for scoring.
- **Michael Pittman Jr.** played weeks 1, 3 and 4, on 72-88% of snaps and 3-5 targets a game. He then missed PIT's
  six games from week 5 to week 11. He is projected 4.8 points.
  - The adaptive model gives him a 0.456 chance of a WR3-level game (4.0+ DK points), against a 0.322 bar, so
    adaptive admits him. It flags him as "returning: missed the team's last 6 games; pre-absence share 7%".
  - Section 5 has what each mode recommended.
- **The decision rule is registered**, and committed before any prospective slate is recorded
  (`research/data/dfs_eligibility/prereg.json`). Adaptive becomes eligible to be the default only if it beats
  conservative on independent-world top 1% (20 entries) by more than 2 SE over 12 prospective showdowns, is not
  significantly worse on cash or on the best single lineup, and the owner signs off.

## 1. What restricts our lineups today

**The depth gate** (`nfl_dfs._apply_depth`):
- It ranks each team's position group by Sleeper's depth order, then by projection.
- It keeps QB1, RB1-2, WR1-3 and TE1-2. Kickers and defenses pass through.

**Hard exclusions** (unchanged in every mode):
- not on any roster Sleeper knows;
- a roster status other than Active;
- Out, Doubtful, IR, PUP, suspended, NA or COV;
- no depth-chart entry (practice squad);
- DraftKings' unavailable statuses;
- no projection this week.

**Showdown house rules** (`nfl_dfs._sd_allowed` and the board's `cpt_ok`):
- `dst_vs_cpt`: no defense beside the opposing team's captain.
- `kdst_cpt`: no kicker or defense as captain.
- `cpt_salary`: the captain costs $11,000 or more whenever three such captains exist.
- `kdst_max`: at most two kickers or defenses.
- `punt_max`: at most two players at $2,000 or under.
- `te_team`: one tight end per team.
- `punt_role`: a punt must be RB1-2, WR1-3 or TE1. This is a depth rule too.

**Classic house rules** (`dfs_tourney.classic_allowed`):
- the quarterback is stacked with his own WR or TE;
- no defense facing our quarterback or running backs;
- at most three players from one team;
- at most one sub-$3,000 punt, and it must hold a role.

The depth-gated players that the field still plays are the field extras:
- showdown: the best 8 projected 1.0 or more (`_field_extras`);
- classic: the best 60.

## 2. The modes

| mode | our pool | punt-role rule | the field |
|---|---|---|---|
| conservative | the gate's players | as today | the conservative board's |
| adaptive | + the field extras the model admits | admitted players hold a role | the same |
| unrestricted | + every field extra | lifted (it is a depth rule) | the same |

**Why the field stays the conservative board's.** Admitted players are taken only from the field's own extras, so
the universe, the field weights and the chalk are byte-identical (EL4). A gated player the field never plays could
be added, but only by moving the field. In the research harness that variant is "unrestricted_all"; in production
it is listed on the board (`outside_field`) and left out.

**The showdown universe cap** (900,000 lineups) still drops admitted extras exactly as it drops field-only ones
(`_elig_extra`, EL4b).

**Hard exclusions are never lifted.** Only a reason of the form "WR4 by depth chart" can be lifted. Field extras
gated by a roster lookup ("not on any roster Sleeper knows") stay field-only even in unrestricted (EL2b).

**Flags** are display only. They are carried on an experimental board's players and shown on the tab
(`static/app.js` `dfsEligTag` / `dfsEligNote`: escaped, no inline handler, EL7). The flags are:
- returning (with the games missed and the pre-absence share);
- no game, or only one game, for this team this season;
- promoted (a teammate at his position who played the team's last game is now out);
- Questionable.

All except "promoted" read as "uncertain workload".

**What the model does not read:**
- PFR snap counts and NGS. These are display-only under the licence default.
- Practice participation (nflverse injury reports). It was not reachable from this session; Sleeper's injury tag
  and DraftKings' status are what the gate sees.

**What is wired, and what is not.** `build_nfl_showdown(..., eligibility=)` and
`build_nfl_classic(..., eligibility=)` take the mode. No route, queue or PC task passes one yet. A mode selector on
the tab ("Conservative / Adaptive / Unrestricted") is the owner's call after section 7's read.

## 3. The adaptive model (`research/dfs_eligibility_fit.py`, rule committed first in 3dae0ec)

**Data.** Sleeper's weekly projections and box scores (`research/data/player_weeks_*.csv.gz`).
- Fit: 2022-2024. Test: 2025. A second test: 2026 weeks 1-5.

**Outcome.** A player reaches L_pos, the median DraftKings score of the last rank the gate keeps (QB1, RB2, WR3,
TE2). In history the ranks are by Sleeper projection, because Sleeper keeps no weekly depth charts.

| pos | L_pos (DK points) | tau |
|---|---|---|
| QB | 16.46 | 0.263 |
| RB | 4.4 | 0.226 |
| WR | 4.0 | 0.322 |
| TE | 1.5 | 0.272 |

**Features.** These are the same code production runs (`dfs_eligibility.features` / `usage_row`):
- ln(1 + projection);
- his targets + carries over his last 3 games played for this team, as a share of the team's;
- no earlier game for this team;
- returning from an absence;
- returning x share (his pre-absence share).

**Model.** A logistic regression per position.

**Admission rule.** Admit when p is at least tau_pos: the 5th percentile of p among the players the gate already
keeps. In other words: at least as likely to have a starter-level game as the weakest 5% of the players we already
trust.

**Test (descriptive; the model ships as fitted):**

| | 2025 | 2026 wk 1-5 |
|---|---|---|
| log loss vs projection-only (per player-week) | -0.0029 +/- 0.0011 | +0.0025 +/- 0.0024 |
| gated player-weeks (rank past the gate) | 1,748 | 585 |
| admitted | 391 (0.72 per team-week) | 107 (0.68) |
| starter-level games among admitted | 159 (41%) | 43 (40%) |
| among gated, not admitted | 186 of 1,357 (14%) | 65 of 478 (14%) |
| share of the gated starter-level games admitted | 46% | 40% |

- **The usage terms add a little on 2025 and nothing on 2026 weeks 1-5.** The 2026 tests hold at most four games of
  history. The projection carries almost all of it.
- **The rule is a credibility screen, not a forecast.** It admits about three gated players in ten, and those
  players reach a starter-level game about three times as often as the ones it leaves out.

## 4. DEN @ PIT, all modes on the same worlds (descriptive)

`research/dfs_eligibility_compare.py`, output `research/data/dfs_eligibility/compare_157076.json`.

**Inputs.** All were recorded by this session between 19:43 and 19:52 UTC, before the 20:00 lock. The game's result
is not read.
- The production board's 19:14 refresh.
- DraftKings' pool and payout table.
- Sleeper's week-12 rows and box history.
- 60,000 worlds from production's simulator (engine model, seed 20261127).

**Checks before comparing.** The rebuilt universe holds exactly the board's 790,222 legal lineups. The conservative
rule set allows exactly its 47,644, and the field's beta is its 0.3298.

**Worlds.** Worlds 0-29,999 chose every mode's lists and its greedy 20-entry portfolio. Worlds 30,000-59,999 only
scored them. Every comparison is paired by world.

**Who each mode admits.**
- Adaptive: Pittman (p 0.456), Mims (0.419), Hurleman (0.339, one game for PIT) and Adkins (TE, 0.387; tau 0.272).
  Adkins is not a field extra, so production's adaptive takes the first three.
- Unrestricted: all four extras (Pittman, Mims, Franklin, Hurleman).
- "+ sub-1.0": also the six players the field never plays (Schrader, Nowakowski, Nichols, Homer, Tonyan, Adkins).

| | conservative | adaptive | unrestricted | + sub-1.0 |
|---|---|---|---|---|
| lineups we may enter | 47,644 | 121,160 | 159,595 | 524,218 |
| scoring pass, 3,000 worlds, 4 workers (s) | 15.3 | 18.9 | 26.7 | 48.4 |
| scoring workers' peak memory (MB) | 824 | 907 | 950 | 1,294 |
| worlds whose DK-legal optimum we may enter (10,000 independent) | 44.5% | 56.9% | 59.0% | 60.7% |
| points short of that optimum, mean | 1.78 | 1.20 | 1.11 | 1.05 |
| best single lineup: top 1% / top 0.1% / cash (independent) | 6.77% / 0.67% / 67.4% | identical | identical | identical |
| 20 entries: any in top 1% / any in top 0.1% / mean cash | 63.48% / 13.31% / 43.4% | identical | identical | identical |
| depth-gated players among the 20 entries | none | none | none | none |

Notes on the table:
- **Timing and memory** were measured on a shared 4-core machine and are relative figures only. Production scores
  60,000 worlds, which is 20 times this. The memory is the scoring workers' peak resident size as a fresh process
  reported it, and it includes what each worker inherits at fork.
- **"Points short"** is over the worlds where the mode's best lineup is among the 400 best legal lineups. It was
  further down in 231, 101, 96 and 91 of the 10,000 worlds, so the means flatter every mode a little and
  conservative most.

**The finding: every depth mode chose exactly conservative's lineups.** The best lineup was Coleman CPT, Nix, Warren,
Rudolph, Lutz and the Broncos, and the 20-entry portfolio was identical, entry for entry. Every paired difference on
the independent worlds is exactly 0.
- So d_s = 0 here. The simulator's own numbers never preferred a gated player for the board's objective (top 1%
  against this field), even though they did put one in the hindsight-optimal lineup of 24.0% of worlds.
- Hindsight coverage is not the objective. A lineup that wins a world outright is not the lineup with the best
  chance of the top 1% across worlds.
- Widening eligibility cost 2.5x (adaptive) to 11x (+ sub-1.0) the rows to score. On this slate it bought nothing.

**Portfolio diversity** (`dfs_portfolio.concentration`, the board's own measure; the same in every depth mode):
- 16 distinct players across the 20 entries.
- Nix and Coleman are in 18 of 20 entries, Rudolph in 17 and Warren in 13.
- The two most alike entries share 5 of 6 players. DEN leads 75% of entries.

**The choosing worlds' figure was a little optimistic.** P(any of 20 in the top 1%) read 63.77% on the choosing
worlds against 63.48% on the independent ones.

**Dollar figures are not reported.** The field model is flatter than the real field: duplicates are rarer than real
(research/reports/sd_dup.md). The board's own money gate marks showdown EV as experimental for that reason.

## 5. Michael Pittman Jr.

**His season (Sleeper's box, weeks before this game only; the result of DEN @ PIT is not read anywhere in this
study):**

| week | snaps | targets | receptions | yards | PPR |
|---|---|---|---|---|---|
| 1 | 59 of 67 (88%) | 3 | 3 | 58 | 8.8 |
| 2 | did not play | | | | |
| 3 | 45 of 62 (73%) | 5 | 2 | 16 | 3.6 |
| 4 | 52 of 72 (72%) | 4 | 2 | 15 | 3.5 |
| 5-11 | did not play (PIT's bye was week 9) | | | | |

- **Pre-absence share.** 12 targets in 3 games: 7% of PIT's targets + carries, and 20% of its wide-receiver
  targets.
- **Status.** Sleeper: Active, RWR order 4, no injury tag. DraftKings: no status.
- **Price and projection.** $6,800 (captain $10,200). Projected 4.8 on the board (Sleeper 5.28 before the usage
  re-split).
- **The gate.** WR4 by depth chart, so he is a field extra: the field drafted him in 8.3% of FLEX slots and 0.6% of
  captain slots.
- **On the production board's own worlds** (not ours) he was in the hindsight-optimal lineup of 9.3% of games:
  0.47% as captain and 8.81% at FLEX.
- **The adaptive model** gives him p = 0.456 against tau 0.322, and flags him "returning".

**On our 10,000 independent worlds** he is in the DK-legal hindsight optimum of 9.5% of worlds, which agrees with
the board's 9.3%. In the same worlds the optimum held Mims in 8.2%, Roman Wilson (the WR3 who was kept) in 8.4%,
and Hurleman and Franklin in under 3%.

**What each mode recommended:**
- **Conservative** (production): no lineup of ours holds him. The field holds him.
- **Adaptive:** he is enterable, admitted by his workload with the "returning" flag. He made neither the best lineup
  nor any of the 20 entries.
- **Unrestricted:** the same.

The case therefore splits in two:
- **The owner was right about the rule.** Pittman's pre-injury role (72-88% of snaps), Sleeper's own projection
  (which ranks him PIT's second receiver) and a 9% share of hindsight optima are what a credible player looks like.
  The gate excluded him on Sleeper's depth order alone.
- **Including him would not have changed Vigil's recommendations on this slate.** At 4.8 points projected and
  $6,800 he is a poor top-1% value beside the chalk core. Eligibility lets the simulator decide, and the simulator
  said no.

## 4b. Real results on every past board (descriptive; `research/dfs_eligibility_history.py`, `history.json`)

For each past board we found the best lineup on the REAL DraftKings points that each variant may enter, and compared
it with DraftKings' best over its whole pool. There is one world per slate: the one that happened. DEN @ PIT is not
read.

**Showdown, 20 games from week 6 to week 12:**

| variant | real optimum buildable | mean points short | slates improved vs conservative |
|---|---|---|---|
| conservative | 6 / 20 | 2.58 | - |
| adaptive (field extras it admits) | 8 / 20 | 1.82 | 4 |
| unrestricted (every field extra) | 10 / 20 | 1.49 | 6 |
| + the sub-1.0 gated players | 13 / 20 | 0.68 | 8 |
| conservative depth, every house rule lifted | 12 / 20 | 1.65 | 9 |

Adaptive's real gains:
- HOU @ IND, week 11: +7.8 (Anthony Gould WR4).
- CIN @ WAS, week 11: +3.0 (Jacory Croskey-Merritt, RB3, returning after four missed games, p 0.69). This is the
  Pittman situation that paid off.
- CAR @ GB, week 8: +2.35 (Mitchell Evans TE3).
- MIN @ SF, week 11: +2.1 (Tai Felton WR4).

Misses that only unrestricted recovers:
- KaVontae Turpin (WR4, +2.1).
- Drew Sample: a TE2 punt that the punt-role rule refuses, +4.4.

Misses that need the players the field never plays:
- Treylon Burks (+5.65).
- Ben Sinnott (+10.2).

**Classic, 12 slates** (main slates from week 1 to week 11, plus Thanksgiving). The real optimum was built by
production's exact DP over each pool:

| variant | real optimum buildable | mean points short |
|---|---|---|
| conservative | 6 / 12 | 3.08 |
| adaptive | 9 / 12 | 0.94 |
| unrestricted (the board's own pool with its 10-46 extras) | 12 / 12 | 0 |

The gated players in real classic optima were:
- Jonah Coleman, Kyle Williams, Omar Cooper Jr. (twice), Kayshon Boutte with Tank Dell, and Ted Hurst III.
- Adaptive admitted all of them except Williams, Dell and Hurst.

**What these are not.** These figures measure whether a mode could have built what won. They do not measure what
it would have chosen: no past board's worlds survive, and production boards are unseeded. DEN @ PIT is the warning
here: higher coverage there bought no change in the chosen lineups. The real-field finishes the owner asked for need
each mode's chosen lineups, so they arrive only with the prospective slates (section 7).

## 6. The house-rule audit

Each rule was lifted alone, with the depth gate kept. "Eliminates the optimum" means the DK-legal hindsight-optimal
lineup breaks that rule.

**Showdown:**

| rule | DEN @ PIT: share of 10,000 independent worlds whose optimum it eliminates | DEN @ PIT: 20 entries' top 1% / cash when lifted (paired SE) | 20 real showdowns: slates whose real optimum it eliminated (mean gain) |
|---|---|---|---|
| captain $11,000+ (`cpt_salary`) | 28.0% | +1.08 pts (0.23) / +1.12 pts (0.03) | 3 (+0.38) |
| no defense beside the opposing captain (`dst_vs_cpt`) | 12.6% | +0.15 (0.16) / -0.21 (0.03) | 2 (+0.11) |
| no kicker or defense captain (`kdst_cpt`) | 7.8% | no change (the salary rule binds first) | 0 |
| at most two kickers/defenses (`kdst_max`) | 5.1% | +0.01 (0.13) / +1.08 (0.03); top 0.1% +0.54 (0.08) | 1 (+0.08) |
| punt must hold a role (`punt_role`) | 4.6% | no change | 1 (+0.22) |
| one tight end a team (`te_team`) | 1.3% | no change | 0 |
| at most two punts (`punt_max`) | 0% | no change | 0 |
| every house rule | coverage 44.5% to 76.2% | +1.55 (0.24) / +0.87 (0.04) | 9 improved, 6 to 12 buildable |
| the depth gate (for comparison) | 24.0% (optimum holds a gated player) | 0 in every mode | see 4b |

- **The captain-salary rule is the most restrictive house rule**, on both the simulated and the real record.
- **On DEN @ PIT's independent worlds, lifting it raised the 20 entries' top 1% by 1.1 points** (4.7 paired SE) and
  cash by 1.1. That is one slate, and the SE is over worlds, not slates. Under this repo's rule a dramatic
  small-case result is a hypothesis: the change is a candidate for its own registered test, not for a hand edit.
- **Lifting `dst_vs_cpt` slightly lowered cash and top 0.1%.** It is not a free win.

**Classic, the house rules on the 12 real unrestricted optima:**
- the quarterback stack was broken in 3 of 12;
- a defense facing our quarterback or a running back in 1;
- the three-a-team cap in 0;
- the one-punt rule in 0.

A classic independent-world comparison needs the whole classic board per variant on the PC, so it is not done
here.

## 7. The registered decision (`research/data/dfs_eligibility/prereg.json`)

The rule was written and committed before any prospective slate was recorded. It is listed on the research
dashboard as `dfs_eligibility_adaptive`.

**Sample.**
- Every NFL showdown the PC builds on its own ($500K+ GPP) that locks after the registration.
- Its inputs must be recorded before lock by `research/dfs_eligibility_record.py`. A slate not recorded in time is
  never backfilled.
- DEN @ PIT is excluded.

**Per slate.** `research/dfs_eligibility_compare.py`, unchanged:
- 60,000 worlds: 30,000 choose and 30,000 independent worlds score.
- d_s = P(any of adaptive's 20 entries in the top 1%) minus the same for conservative.

**Primary test.**
- D = mean d_s over 12 slates, with the SE taken over slates.
- PASS needs D > 0 and D > 2 SE.

**Non-inferiority.** Both must hold:
- cash (20 entries): mean + 2 SE >= 0;
- the best single lineup's top 1%: mean + 2 SE >= 0.

**Read.**
- Read once, at the 12th slate or on 2027-02-16.
- Fewer than 8 slates by then is INCONCLUSIVE.
- `research/dfs_eligibility_read.py` answers NOT READY before either condition and computes nothing.

**Decision.** If the read passes, adaptive *may* become the default, with the owner's sign-off. Anything else keeps
conservative. Unrestricted is reported but never decided here.

**Not covered.** Classic needs the whole classic board built per mode on the PC; it is registered separately once
that harness exists. Recording the inputs before each lock is a manual command today. A PC task to do it is the
owner's call.

**Running a slate** (record before lock, score any time after):

    python3 research/dfs_eligibility_record.py <dg> <dir>
    python3 research/dfs_eligibility_compare.py <dir> --out research/data/dfs_eligibility/prospective/compare_<dg>.json

The recorder refuses after lock. Each slate takes about 40 minutes on a 4-core machine; most of that is the
field's thresholds and the candidates' top-1% pass.

## 8. Caveats

- **DEN @ PIT's worlds are our own simulation.** The production simulator, on the same inputs, seeded 20261127.
  They are not the board's worlds, because production boards are unseeded. The harness checks first that it has
  rebuilt the board's universe (790,222 lineups), its rule set (47,644) and its field (beta 0.3298) exactly.
- **The choosing convention is the evaluator's**: per-world field thresholds, ties not split. It is not the board's
  grid-based top 1%. Both modes are chosen and scored the same way.
- **The real-results audit is one world per slate.** It measures whether each mode could build what actually won,
  not what it would have chosen.
  - Names Sleeper's box could not match count 0 points: 102 rows across 20 games, mostly backups who did not play.
    At least one (Hollywood Brown) is a spelling mismatch.
  - Kickers are scored 3/4/5 per field goal by distance plus 1 per extra point.
- **Past slates' adaptive verdicts use Sleeper's projection rows for that week as read today**, for players the
  board did not list.
- **The depth gate is not reproducible in history.** History ranks by projection because Sleeper keeps no weekly
  depth charts. Pittman is the counterexample: by projection he was PIT's second receiver, and by depth order its
  fourth.

## 10. Follow-ups the owner approved (2026-11-27, evening)

### 10.1 Every showdown captured before lock (`sd_capture.py`, `pc_worker._task_sd_capture`)

The owner: "Automatically capture every Showdown's complete pre-lock input snapshot, model version, configuration
and seeds. Preserve everything necessary for exact replay and prevent post-lock data leakage."

**When.** The PC captures each showdown twice, in its cycle and between builds (never beside one):
- the lead look, between 6 h and 2 h before lock;
- the close look, between 50 and 15 minutes before lock, after the inactives;
- never within 10 minutes of lock.

The richest slates go first. A long build in the cycle can make a window pass unused; a capture is never taken
late to make up for it.

**What a capture is.** A child process runs the eligibility recorder: the production gate and the production
simulator, seeded with the draft group id, on one worker. While it runs:
- every HTTP answer is recorded in order, through the audit's harness (`research/den_pit_audit/netreplay.py`,
  guards RPL);
- every deep-store file it reads is copied as first read (`deep_orig/`), and its writes stay in the snapshot, never
  in the PC's store;
- every disk cache is private and empty: no worlds cache, no checkpoints, no GPU.

**What the manifest holds.**
- The commit, the showdown and classic engine versions, the board model, the simulator version, the scoring-path
  signature and the eligibility model's sha256.
- The seed and the world count.
- The sha256 of every file in the snapshot.

**Post-lock leakage is refused three ways.**
- A capture never starts within 10 minutes of lock.
- The recorder refuses a locked slate.
- A snapshot whose last network answer, or whose end, came at or after lock is deleted and listed `refused`.

**Store.** At most 40 snapshots and 8 GB under `pc-simcache/sd-captures` (gitignored), oldest first. A short row
per capture goes to `/api/pc/captures` through the token door. The error-log workflow pulls it to sim-history as
`errors/sd-captures-latest.json`. `"sd_capture": false` in vigil-pc.cfg switches it off.

**Exact replay is the test.** Right after each capture a second child plays it back:
- with the network forbidden (a request the recording does not hold fails the replay);
- with the deep store a copy of what the capture first read;
- with the clock shifted to the capture's.

Every file must come back bit for bit: the board, all 60,000 worlds of every player, DraftKings' pool, the
contest, Sleeper's rows and the history. The manifest says `replay: exact` or names the first difference.
- **Measured on a real slate**, CLE @ LV (157086) here, 2026-11-27: 99 network requests, 70 MB, capture plus replay
  190 s, `replay: exact`.
- **Guarded hermetically** by SC2: a probe build captured by the same child, with a 429 then a 200, a deep read, a
  deep write, the clock and seeded worlds. The live store is then changed and the network removed, and the replay
  must still come back exact.

### 10.2 The house-rule registration (`research/data/dfs_house_rules/prereg.json`, `research/dfs_house_rules_read.py`)

It is separate from the eligibility registration, and its rule was hashed before any recorded slate was scored.

**Six primaries**, each one house rule lifted alone against conservative:

| primary | rule lifted |
|---|---|
| H1 | the $11,000 captain floor |
| H2 | kicker and defense captains, exempt from that floor (the only way one can exist where it binds) |
| H3 | a defense beside the opposing team's captain |
| H4 | more than two kickers/defenses |
| H5 | more than one tight end per team |
| H6 | the cheap-punt rules (the two-punt cap and the punt role) |

- **Multiplicity:** Holm across the six, at a family-wise one-sided 5%.
- **Secondary:** every rule lifted jointly, reported only.

**The statistic is the optimizer's performance.** Each variant's own 20 chosen entries are scored on the
independent worlds: P(any in the top 1%), the variant minus conservative.
- The SE is taken over SLATES (Student's t, n - 1 degrees of freedom), never over worlds.
- A passing rule must also not be significantly worse on cash.
- Optimal-lineup eligibility (how often a rule removes the hindsight optimum) is reported beside it and never
  decides.
- The entries' real DraftKings points, once the games are final, are reported with SEs over slates and never
  decide.

**Sample and read.** The same 12 prospective showdowns as the eligibility test, read once (same dates).

**The primary snapshot is named in advance.** Both registrations, amended before any slate was read, take only
the LEAD look (6 h to 2 h before lock), and only one that replayed exact. The owner: "We shouldn't retrospectively
choose whichever snapshot produced better results."
- The close look is diagnostic only.
- A slate whose lead look is missing, refused or not exact is out of the sample. It is never replaced by its close
  look.
- Every capture stamps its label and its replay verdict on the recording, and the reads refuse any other label
  (SC9). The harness
gained the two combined variants (H2, H6) and keeps every variant's 20 entries by name. The eligibility
registration records this as an amendment made before any slate was scored, and carries its new hash.

**Status.** DEN @ PIT's +1.1 points for the captain floor (4.7 SE over worlds, one slate) is the hypothesis, not
evidence.

### 10.3 The Conservative / Adaptive / Unrestricted selector (the tab's Build on the PC)

**On the tab.** An "Eligibility" control sits in the player picker. "Conservative (production default)" is the
default. "Adaptive (experimental)" and "Unrestricted (experimental)" carry a 🧪 line saying they widen only our
lineups, that the field and projections are unchanged, and that production stays conservative until the registered
test says otherwise. An experimental board says so under its player table (`dfsEligNote`).

**How the mode travels.**
- The mode rides the pick (`nfl_dfs.pick_parse`: `mode`, refused if misspelled). A Build with one is always the
  picked board.
- `pc_worker._picked_builds` is the only place a mode reaches a builder. No scheduled build passes one (SC6, EL1).
- The lineup sheet refuses a mode.

### 10.4 Rare outcomes carry their simulation (`dfs_tourney.rare_counts`)

The owner asked for "event counts or effective sample sizes" on rare outcomes, then: "distinguish between the
number of simulated games where a lineup was best and its duplication-adjusted probability of winning outright".

Every board row, showdown and classic, now carries `rare`, with the two kept apart:

- **`best`, a COUNT.** The worlds in which the lineup scored the most, with the worlds it is out of.
  - Showdown: of every legal lineup (the field's whole support), over the scoring worlds.
  - Classic: the best possible lineup over every player, on the generation worlds.
- **`win`, the duplication-adjusted outright first place.** This is the board's win%: a mean of per-world
  probabilities after ties and the field's expected copies.
  - Its "worlds' worth" is that probability summed over the worlds. It is not a count.
  - It is shown outright and with ties; the gap between the two is the duplication split.
- **Top 0.1%**, shown beside them the same way.

Where a count (or worlds' worth) is 0, the row carries the exact Clopper-Pearson 95% upper bound,
1 - 0.025^(1/n). The tab prints all of this under the first-place column (and top 0.1% on classic), labelled and
escaped: "outright, dup-adjusted: ..." and "best in k of n worlds".

**The scoring pass is untouched.** Everything is read from what the pass already returns: its means, and the `opt`
tally it already counts. `score_path_sig` stays the validated `b0bf761df43c7f49` until Saturday night's GPU
validation (guard SC8).

**Deferred until after that validation.** A per-world count of the worlds where the lineup actually finished
outright first in the field (rather than the probability) would need the pass to keep a count beside each mean.

## 9. Files

- `dfs_eligibility.py`: the modes, the model's features, `assess`, `week_context`.
- `dfs_tourney._apply_eligibility` / `_elig_row`; `nfl_dfs._sd_allowed` and `dfs_tourney.classic_allowed` honour
  `_elig_role`.
- `static/app.js` `dfsEligTag` / `dfsEligNote`.
- `research/dfs_eligibility_fit.py`, writing `research/data/dfs_eligibility/{model,fit}.json`.
- `research/dfs_eligibility_compare.py`, writing `research/data/dfs_eligibility/compare_157076.json`.
- `research/dfs_eligibility_history.py`, writing `research/data/dfs_eligibility/history.json`.
- `research/dfs_eligibility_record.py`, `research/dfs_eligibility_read.py`,
  `research/data/dfs_eligibility/prereg.json`.
- Guards: `tests/guards_auto_eligibility.py` (EL1-EL7).
- The DEN @ PIT recording (60,000 worlds, DraftKings' pool, the contest, Sleeper's rows, the roster) is not
  committed (22 MB, and the raw feeds). Its sha256 prefixes are in compare_157076.json.
