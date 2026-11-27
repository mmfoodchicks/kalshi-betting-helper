# NFL coaching engine: R1 preserved, a confirmatory test, three verdicts, and an independent DFS read

Written 2026-11-27, after the owner read `research/reports/nfl_coach_r1.md`. **Research only. Nothing here
has been run on sealed data, and no production file changed.** Four things, each the owner's decision:

1. R1's registration and its verdict are preserved as they are.
2. A separate confirmatory test of criterion 4, on fresh games, with its own error control.
3. A report that states football realism apart from winner prediction.
4. An independent DFS read, with the research-only engine variant it needs.

## 0. The short version

- **R1 is untouched.** `prereg_r1.json` (block `ba0bae08`), its read, `nfl_coach_sim.py` (coach-r1.0) and every
  other pinned file are byte-identical. If R1 fails, the failure stays recorded.
- **The confirmatory test runs only if R1 fails criterion 4 and nothing else.** It reads the 2027 regular
  season, two looks, the second final. The family-wise one-sided false-positive rate over R1's criterion 4
  and this test together is 5%.
- **It will usually not confirm at the effect sizes development saw.** Power is 4%, 18% and 29% at 0.0012,
  0.0054 and 0.0072 nats, and 80% at 0.0143. A whole season cannot do better.
- **The DFS read is its own question with its own 2.5% rate.** On about 96 games it has 80% power at a CRPS
  gain of 0.034 DraftKings points a player-week, and about 28% at the largest gain an engine change has shown
  here (0.017).
- **Neither new read could be rehearsed in this container.** The DFS read needs the PC's deep store; its
  `--dry` mode must complete there before the date.

## 1. R1 preserved

Nothing R1 pins was edited. The new guards live in a new file because R1 pins `tests/guards_auto_nfl_coach.py`.
Guard NF0 holds R1's block hash and its engine, tables and code.

## 2. The confirmatory test of criterion 4

| | |
|---|---|
| registration | `research/data/nfl_coach/prereg_c4.json`, block `bacf92f1102d98dacd9c62d03874442ef1fa47d2ce6dcd5850bdf99342606638` |
| the read | `research/nfl_coach_c4_confirm.py --look 1` or `--look 2` |
| runs only if | `read_r1.json` exists, criterion 4 failed, and criteria 1, 2, 3 and 5 all passed |
| games | the 2027 regular season. Sealed: `nfl_coach_data` drops them for every caller but a registered read |
| candidate | coach-r1.0, the same pinned engine, tables, anchor, checkpoints and clip as R1 |
| statistic | R1's criterion 4, by R1's code: paired late-state log loss, one number a game |

**Why 2027 and not the 2026 postseason.** The postseason is 13 games and needs postseason overtime, which
R1's pinned measuring code does not pass to the engines. Editing that code is not allowed, so those games are
left out.

**Error control.**

- The family is two tests: R1's criterion 4 and this one. Family-wise one-sided rate: **5%**.
- R1's "better by more than 2 SE" spends **2.43%** (Student t on 89 degrees of freedom, the fewest games its
  read accepts).
- This test gets the other **2.57%**. The split is Bonferroni, so it holds whatever the dependence between
  the two tests.
- Two looks, O'Brien-Fleming shaped: z 2.780 after week 9 (about 136 games), z 1.966 after week 18 (about
  272). Each z becomes the Student t value of the same tail probability on games - 1 degrees of freedom
  (2.825 and 1.975 at the planned sizes), as `research/nfl_deep_weight_read.py` converts its own.
- A simulation of the two looks under no effect crossed in 2.55% of 400,000 trials (guard NF3b).
- **Futility:** look 1 ends the test if the candidate is not ahead at all. It is not credited to the rate, so
  the boundaries hold either way.

**Power**, at an assumed SD of 0.084 nats a game (development: 0.077, 0.083, 0.091):

| true gain (nats a game) | chance of confirming |
|---|---|
| 0.0012 (2024's estimate) | 4% |
| 0.0054 (2026 weeks 1-12) | 18% |
| 0.0072 (2025, the largest) | 29% |
| 0.0143 | 80% |

**The stopping rule ends.** Confirmed at either look, or futility at look 1, or look 2: each one closes the
test. There is no third look, no third test and no added season. The read refuses a look 3 and refuses look 2
after look 1 stopped.

**What each outcome means.**

- **CONFIRMED**: criterion 4 is met on fresh games at the family rate. R1's registered verdict stays FAIL;
  both are reported together.
- **NOT CONFIRMED**: a better late-state win probability is *not established* for coach-r1.0. The read prints
  the largest gain the data do not exclude (95%). The question is closed for this candidate. It is not
  evidence that the engine is worse, and it says nothing about realism.
- A new win-probability claim after that needs a new candidate engine and its own registration. It is not a
  third try at this one.

**Refusals**, as R1's read: a dirty tree, any pinned file that differs, a look's date, the data's own last
game + 78 h, too few games, a look already read.

## 3. Realism apart from winner prediction

`research/nfl_coach_r1_report.py` reads `read_r1.json` and prints, in this order:

1. the registered verdict, unchanged (all five criteria);
2. **realism**: criteria 1-3 (scenario tests, the four inside-2:00 figures, the level not worse);
3. **late-state win probability**: criterion 4;
4. **pre-game non-inferiority**: criterion 5.

It recomputes nothing. A group passes when every registered criterion in it passed. It also says whether the
confirmatory test may run. The R1 read itself is not changed.

## 4. The independent DFS read

| | |
|---|---|
| registration | `research/data/nfl_coach/prereg_dfs.json`, block `c1817fdf01de067427cd84953296161410aa1c1bc57ecec3c3f26d885e80b877` |
| the read | `research/nfl_coach_dfs_read.py <deep store> --table <player weeks>` |
| runs | whatever R1 and the confirmatory test say |
| window | 2026 weeks 13-18, at least 85 games, on or after 2027-01-06 06:00 UTC |

**The arms.** Both go through the production DFS harness's own week runner
(`research.nfl_dfs_engine_eval._week`), on the same games, seeds, anchor targets, Sleeper usage deal and
allocation noise.

- **engine**: `nfl_dfs_engine` as shipped, games by `nfl_deep_sim`.
- **coach**: the same, games by `nfl_coach_sim`.

**The variant** (`research/nfl_coach_dfs.py`). `nfl_dfs_engine.py` is not edited and has no hook. It calls
`nfl_deep_sim.play_game` by name; `coach_games()` swaps that one function inside a `with` block, in the
calling process only, and always restores it. The swapped game plays on the caller's base tables with the
coaching clock tables on top, at production's own form tilt and home edge. Guards NF13-NF15: no hook, no
production import, and production's worlds are identical before a block, after it, and after an error in it.

**The data.** Player-week DraftKings points for weeks 13-18, built at the read by
`research.hist_data.build`. No committed file holds a 2026 player-week after week 5 (guard NF10).

**Why the same games as R1 is allowed.** R1 reads game-level clock counts and the winner. This reads player
points. The candidate is frozen and pinned in both registrations, so neither read can change what the other
tests. Its false-positive rate is its own, for a DFS claim; it is not part of the win-probability family.

**The rule.**

| | |
|---|---|
| primary | offence CRPS, coach minus engine, paired by player-week, SE clustered by game: t below -1.985 (2.5% one-sided, t on games - 1) |
| guardrails | pair-correlation RMSE within +0.01 of the engine's; PIT KS within +0.01; defenses and kickers each not worse by more than 1 SE |
| PASS | the primary and every guardrail |
| NOT ESTABLISHED | the guardrails hold, the primary does not |
| WORSE | a guardrail fails |
| looks | one. No extension |

Reported beside it, not gating: the role-pair table (QB-WR, same-team stacks, cross-team) against real, the
cross-team pair RMSE, per-role CRPS, and the worlds' attempts and yards against Sleeper and real.

**Late-game usage cannot be scored here.** The deal sees a game's totals, not its plays. That is R2's
subject, and is not started.

**Power.** The paired offence SE between two engine arms was 0.0150 on 63 games (the passing-volume anchor's
2026 test). That is another pair of arms, so it is a guide. Scaled to 96 games it is about 0.0122.

| true CRPS gain (DK points a player-week) | chance of a PASS |
|---|---|
| 0.006 (the anchor's 2026 gain) | 7% |
| 0.017 (the anchor's 2025 gain) | 28% |
| 0.034 | 80% |

**What each outcome means.**

- **PASS**: eligible for a DFS shadow build. Any production use is the owner's word and its own test.
- **NOT ESTABLISHED**: no DFS gain shown on 96 games; the upper bound is printed. Not re-read, not extended.
- **WORSE**: the coaching engine is not used for DFS worlds as it stands.

**Running it.** It needs the PC: the ESPN deep store, the Sleeper dumps for the table, and Sleeper's K/DST
feed for weeks 13-18 (the harness fetches and caches that file).

1. Before the date: `python3 -m research.nfl_coach_dfs_read <store> --dry`. It plays 2026 weeks 2-5 (seen
   rows) end to end and writes no result. If it needs a fix, the fix is committed with a NEW registration
   before any week 13-18 row is built.
2. On the date: check out the registration commit in a clean worktree (it pins `nfl_dfs_engine.py` and the
   harness, which production may have moved by then), build the table, run the read.

## 5. Guards (`tests/guards_auto_nfl_coach2.py`, 18 checks, section NF)

- NF0: R1's block hash and pinned engine, tables and code.
- NF1: the three-verdict report states the registered verdict first and cannot change it.
- NF2-NF5: the confirmatory registration's hashes; the 5% family split; a simulation of the two looks; the
  looks and the season; the stated power recomputed.
- NF6-NF8: its refusals (R1 passed, R1 failed more, early, dirty, a third look, look 2 after a stop) and its
  decision at each look.
- NF9-NF12: the DFS registration, the absence of the window's rows, its refusals, its three outcomes.
- NF13-NF15: the DFS variant leaves production alone.

## 6. For the owner

1. **The family rate is 5% one-sided.** R1 alone was 2.4%. A confirmatory test cannot be added at no cost;
   5% is my choice of the total, written before any result. Say so if he wants it tighter: at 2.5% for the
   family the confirmatory test would have almost nothing left to spend.
2. **Both new tests are weak at the effect sizes seen so far.** That is stated before the reads, as he
   asked. A "not confirmed" or "not established" is the likely outcome even if the gain is real.
3. **The DFS `--dry` run must be done on the PC before 2027-01-06**, and the read itself needs the deep
   store. I could not run either here.
4. **The DFS window is the same games as R1's.** I think that is sound, for the reason in section 4. If he
   would rather it wait for games no read has touched, it needs a new registration on 2027.
5. **Run every read from a checkout of its registration commit.** All three pin files that later work will
   edit.
