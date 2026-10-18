# Recent usage in the DFS projections: shipped at 0.3 for targets and carries

Run 2026-10-18 at the owner's request ("Is there anything else you can do to
learn more information that would help us make the best possible lineup
choices", then "go for it"). The rule sits in the docstring of
`research/nfl_usage_validation.py`. It was written at 13:30 UTC, before any
number was computed; its sha256 at that time is in the session's
pre-registration log. Results are in `research/data/nfl_usage_validation.json`. The player-week
table is committed. nflverse's weekly files (for air yards) are not
committed; they come from
`https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_<season>.csv`.

## The question

The DFS engine and the app's lineup sheet split a team's targets and carries
by Sleeper's projected lines. Sleeper already looks at usage. The test asks
whether a player's recent share of his team's targets and carries adds
anything to Sleeper's line. Recent share is the most-quoted usage signal.
nflverse publishes it with air yards beside it.

## The rule, in short

- **Unit.** One team-game per component: targets or carries.
- **Players.** The team's players with a positive projection who played.
- **Sleeper's share** `s`: each player's projection over the set's total.
- **Trailing allocation** `t`:
  - Players with two or more games for this team this season split the
    share Sleeper gives them. They split it by their recent share of the
    team's total, floored at 0.005.
  - Everyone else keeps Sleeper's share.
- **Candidate** `q = (1 - w) s + w t`.
- **Loss.** The multinomial log loss of who actually got the ball, because
  the engine deals the ball by a multinomial on these shares.
- **Candidates and grid.** Target share over 3 or 6 games, WOPR (air yards)
  over 3 or 6, and carry share over 3 or 6, each at `w` in
  {0, 0.1, ... 0.6, 0.8, 1}.
- **Procedure.**
  - Select on 2024.
  - Test on 2025: the candidate must beat Sleeper alone by two standard
    errors of the paired difference.
  - Confirm on 2026 weeks 1-5: a veto only if the candidate is worse by more
    than two standard errors.

## Results

Mean loss per team-game (lower is better):

| | 2024 (select) | 2025 (test) | 2026 (confirm) |
|---|---|---|---|
| **targets**, Sleeper alone | 1.995512 | 2.023182 | 2.067820 |
| targets, share over 6 games at 0.3 | **1.990019** | **2.018314** | 2.068153 |
| **carries**, Sleeper alone | 1.185003 | 1.169864 | 1.136885 |
| carries, share over 3 games at 0.3 | **1.177080** | **1.166687** | 1.134308 |

The paired difference against Sleeper alone, over 544 team-games in 2025 and
158 in 2026:

| | 2025 | 2026 |
|---|---|---|
| targets | -0.004869 +/- 0.001117 (4.4 SE): **pass** | +0.000334 +/- 0.001859: flat, no veto |
| carries | -0.003177 +/- 0.001459 (2.2 SE): **pass** | -0.002578 +/- 0.002000: same direction |

Both components ship at 0.3, as the rule says.

- **Air yards (WOPR) lost to plain target share.** Its best 2024 weight
  (0.2) was worse than target share at 0.3, and it was worse on 2025 and
  2026 too. Nothing in production reads nflverse.
- **Joins.** 72-76% of the players with a trailing share joined nflverse by
  name. A player who did not join kept Sleeper's share in the WOPR
  candidates, which handicaps WOPR somewhat.
- **What the gains mean.** They are small: about a quarter of a percent of
  the log loss. They sharpen who gets the ball inside a team. The team's
  totals never move.
- **Carries.** The six-game window at 0.3 was slightly better than the
  three-game window on 2025 (1.165199 against 1.166687). The rule chose
  three games on 2024, so three games shipped.
- **2026's flat target result.** The early weeks hold at most four games of
  history. The six-game window is really two to four games there. The rule
  does not let a flat confirmation block a passing test, and it did not.
- **Weeks 1-2.** Every season's first two weeks are in the unit counts with
  a zero difference, because no player has two games yet. That dilutes the
  mean and the SE alike.

## The production path

`nfl_usage.py` is called from `nfl_dfs_sim.weekly_games`, after the lines
are built and before `nfl_recon`. So the legacy board, the constrained model
and the engine all deal the same shares. The shipped behaviour:

- **The moved lines.**
  - Each player's targets move to `q x` the team's projected targets.
    Receptions, receiving yards and receiving touchdowns move by the same
    factor, so his catch rate and per-catch figures stay Sleeper's.
  - Carries work the same way, with rushing yards and touchdowns.
  - `proj_pts` moves by the PPR change, because the legacy simulator pins
    its mean to it.
- **The history.** It comes from Sleeper's own weekly box scores, the feed
  dfslog grades from, stored in the deep store a week at a time.
  - A week two or more back is fetched once.
  - The last week is refetched every six hours.
  - Any unreadable week leaves the whole board on Sleeper's lines.
- **Start date.** The change starts with 2026 week 7. It shipped on the
  Sunday of week 6, hours before the main slate locked, and that week's
  boards and lineups were already built on Sleeper's lines.
- **It reproduces the harness exactly.** On the 1,088 units of 2025, the
  largest share difference between the production code and the harness was
  3e-16. The guard suite replays a small team the same way.
- **The real week-6 path, measured.** Weeks 1-5 were fetched live and
  applied to Sleeper's week-6 lines:
  - 0.9 s for five weeks of history, at 16 KB per stored week.
  - 490 players moved.
  - Team totals held to 1e-14.
  - Players projected 8+ PPR moved 0.41 points on average (median 0.33,
    largest 1.8: Kenneth Walker, 22.81 to 24.61).

### One case worth knowing: a back who is out

Arizona's carries in weeks 3-5 went mostly to a back with no week-6
projection. The players with history split the share Sleeper gives them by
their recent shares, so the quarterback (Brissett) went from 2.85 to 4.70
projected carries. Tyler Allgeier, whom Sleeper projects for 62% of the
carries, dipped from 13.93 to 13.27.

The mechanism is the rule's, not a bug. It was in the test's units too. A
post-hoc look (not a test) at the units where the players in the set held
under 60% of the team's trailing carries found:

- They are 6-7% of the carries units that have any history.
- They were helped on 2024 (-0.016 +/- 0.021) and hurt on 2025 (+0.004 +/-
  0.009). Neither is distinguishable from zero.

Any guard against it, for example skipping the blend when coverage is low,
is a new candidate. It would be judged on games after it is written, not
fitted to this.
