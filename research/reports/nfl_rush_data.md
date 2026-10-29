# The NFL rushing matchup layer: data (RB + QB)

2026-10-23. The data layer under the rushing upgrade of the Matchups tab: two
new production-safe modules (`nfl_rush_data.py`, `nfl_rush_profiles.py`), a
research builder (`research/nfl_rush_dataset.py`), the committed tables
(`research/data/nfl_rush/`) and the guards (`tests/guards_nfl_rush_data.py`).
Nothing here was fitted to an outcome. The agreed foundation
(`nfl_matchup_data.py`, `nfl_matchup_profiles.py`,
`research/data/nfl_matchup/dataset_2023_2026.csv.gz` sha256 `766a60ed...`)
is read, never edited.

**Revised 2026-10-24** after the two independent reviews of this layer
(`research/reviews/nfl_rush_data_{opus,sonnet,orchestrator}.md`), every fix
F1-F12 made, plus F14 (the licence: source tags on every field, a pbp
pressure proxy, an OL-absence count that needs no PFR snaps) and F13 checked
and declined (§3.5). The two blockers: **B1** the "RB" units of every
measurement held every WR and TE who carried (now RB/FB only; the RB k table
re-measured, §5) and **B2** the PFR team snap total read a 1-snap 1% back as
100 snaps (now the most any player took, §2.1).

## 0. API (stable; the model agent builds on exactly this)

### 0.1 Building a snapshot

```python
import nfl_matchup_data as D, nfl_rush_data as R, nfl_rush_profiles as RP, nfl_matchup_profiles as P
stores = {s: D.load_season(s) for s in (2024, 2025, 2026)}      # the agreed play stores
auxes  = {s: R.load_aux(s) for s in stores}                       # nfl_rush_aux_<season> (this layer)
pos    = P.positions_from_ids(D.load_ids())                       # {gsis int: "QB"/"RB"/"FB"/"WR"/"TE"}
S = RP.build(stores, auxes, season, week, pos)                    # as of (season, week): weeks < week + 2 seasons back
S.rb(pid, team=None)        # RB rushing + receiving (usage team-scoped when team is given)
S.qb(pid, team=None)        # QB rushing, decomposed
S.defense_run(team)         # what the defense allows to RB runs, what it lines up in, contact, RB receiving
S.defense_qb(team)          # QB rushing allowed, observed / expected against the QBs it faced
S.offense_line(team)        # line yards, stuffs, success by gap, YBC, the as-of starting five, the week's absences, ol_listed_out
S.team_context(team)        # neutral / leading / trailing / red-zone / early-down run rates, pace, QB-keep share, RPO rate
S.status(pid, team)         # the cutoff week's injury report (pre-kickoff): active / questionable / doubtful / out / unknown (no report)
S.rb_room_out(team, exclude=None)   # carry share (team's last 4 games) of its backs listed Out/Doubtful this week
RP.flatten(profile)         # {dotted path: field}
RP.SOURCE_OF(kind, path)    # the field's source tag; RP.restricted(tag): PFR / NGS -> display only (licence default)
```

- `pid` is the gsis id as an int (`D.gsis_int("00-0036355")`); `team` is
  nflverse's code (LA = Rams).
- `RP.build(..., decay=RP.DECAY, back=RP.BACK)`: `DECAY` 0.75, `BACK` 2 (§5.3:
  the rule gives 0.75 before and after the B1 fix, on its boundary).
- `RushSnapshot(season, week, current, priors, aux=auxes)` refuses tallies that hold
  the cutoff week or later (`ValueError`); `S.contains(season, week)` is the test.
- Every profile stamps `aux` {season: "aligned" | "incomplete" | "stale" |
  "missing"}: a stale aux (built on another matchup store) or a missing one
  turns aborted snaps into designed runs and empties the on-field shares;
  an INCOMPLETE one was built on this store without a source its season should
  have (`R.expected_sources(season)`; a failed download -- round 3's G1) and is
  still read for what it has. A snapshot holding any of the three ledgers
  `RUSH-aux` once (the reviews' M3). `R.aux_complete(aux)` is the test the job's
  `rush_due` reads.
- `S.status` reads "unknown", and `starters.out_report`, `ol_listed_out` and
  `rb_room_out` read None, when the aux holds NO report for the cutoff week, and
  also when the week's report exists but lists NOBODY from the team (G4: a
  playing team always has rows -- 2 of 1,818 playing team-games in 2023-26 had
  none, a data gap); ledgered `RUSH-noreport` once per (season, week) or
  (season, week, team) a process. A player of a listed team who is not on the
  report is "active" (M1: a week-8 board before nflverse carried the week's
  report read every back "active").
- `RP.SOURCE_OF(kind, path)` (kind "rb" / "qb" / "def" / "dq" / "ol" / "tc"),
  `RP.restricted(tag)`, `RP.SOURCE_TAGS`: the licence filter (§1).
- Lower level: `RP.season_tallies(store, aux, pos, max_week=None, min_week=None,
  week_filter=None)`, `RP.add_game(T, store, aux, gi, start, stop, pos)`,
  `RP.line_yards(y)`, `RP.sneak_rule(sg, ytg, rloc, rgap)`, `RP.k_for(metric)`.
- A back's games -- every usage rate, share, window and games count -- are the
  games pbp or participation shows him in (a rush, target, dropback or
  on-field snap), never a game only PFR's snap counts list (round 4, M8);
  `rb().any_snap.*` holds the PFR-inclusive versions, restricted, for display.
- RB units in every measurement are crosswalk position RB/FB only; a WR's or
  TE's carry (a jet sweep, Taysom Hill) counts in the team's designed runs and
  in the defense's run tallies (keyed by the rusher's position, so it never
  enters "vs RB" figures) and never becomes a back's (`T.rb`) -- B1.
- A three-season snapshot builds in ~1.8 s and holds ~100 MB RSS: build it in
  the PC job or a niced child, never a gunicorn worker (the agreed rule).

### 0.2 Every field

Every leaf is a dict:

```
{"value": float|None, "unit": "share"|"yds_per_carry"|"per_game"|..., "kind": "observed"|"modelled"|"proxy"|"not_available",
 "n": denominator (weighted), "basis": "how it was measured", "raw": unshrunk ratio,
 "metric": "<k-table key>", "k": measured k | None, "reliability": n/(n+k) (0.0 when k is None; absent when unmeasured),
 "prior": shrinkage target, "base": the same target from the unit's RAW own figure (splits only),
 "source": "pbp"|"participation"|"ftn"|"injuries"|"pfr"|"pfr_snaps"|"ngs"|"none"|"derived:<a>+<b>..."}
```

- `value` is the empirical-Bayes figure `(sum + k*prior)/(n + k)` when the field
  is shrunk (`kind` "modelled"), else `raw`. A metric whose split-half r was
  <= 0.02 has `k` None and `value == prior` -- the data does not support it
  (§5.1 lists them; most run-direction and box SPLITS of efficiency).
- `unit` "share" is a probability or share (draw as a percent); everything else is
  a number with that unit. A share's `prior`, `base` and `value` are CLAMPED to
  [0, 1] (an additive split offset pushed a box-by-personnel prior to -0.048 and
  a target-rate-vs-zone prior to 1.24 -- the reviews' m1); `raw` stays the
  measured ratio.
- `source` says where the field comes from (§1, the licence default):
  `RP.SOURCE_OF(kind, path)` gives it for a path, `RP.restricted(tag)` is True
  for anything holding `pfr`, `pfr_snaps` or `ngs` -- display with credit, never
  a fitted model feature until the owner decides otherwise.
- Splits (`by_direction`, `by_side`, `by_box`, `by_personnel_faced`,
  `by_formation`, `scramble.vs.*`, `box.by_offense_personnel.*`,
  `box.by_formation.*`, `by_gap.*`) shrink toward `prior` = the unit's own
  overall EB figure moved by the league's split offset (additive; multiplicative
  for scramble rates), with the k measured on exactly that departure.

### 0.3 Field paths (`<dir>` = left_end, left_tackle, left_guard, middle, right_guard, right_tackle, right_end; `<side>` = edge, tackle, interior; `<box>` = light, neutral, heavy; `<pers>` = base, nickel, dime; `<form>` = shotgun, under_center; `<op>` = 11, 12, 21, 13, 22, 10)

**What a back's "games" are** (round 4, the rule review's M8; round 3's G2
first described it). Every field a fitted feature may read takes a back's
games from pbp and participation ONLY: a game counts when he had a rush of
any class, a target, a dropback, or a participation on-field snap (seasons
that have participation) -- the per-game line's `app` flag. That covers
`games.current` / `games.weighted` (`p_games_cur` / `p_games_w`), every
`usage.*` per-game rate and team-scoped share (the team's designed runs,
targets and situational plays of THOSE games), the on-field shares and the
`recent.*` window. A game that only PFR's snap counts list him in (special
teams only, or offensive snaps with no play and no participation line) no
longer enters them: in round 3 it did, so those fields moved with PFR rows
while tagged "pbp" -- the licence default broken in a way the tag-equality
guard could not see (~1,100 of 6,041 RB rows). The PFR-inclusive versions are
kept for display only, explicitly named and restricted: `rb().any_snap.{games,
carries_pg, targets_pg, carry_share, target_share, recent_carry_share}`, tagged
`derived:pbp+pfr_snaps` (Kalshi settles a player on one snap). The PFR snap
shares (`usage.snap_share`, `recent.snap_share`, restricted) read every game
PFR lists him in. A guard plants a special-teams-only PFR line and requires
that no field outside the pfr / pfr_snaps tags moves -- on a game that does
carry participation-charted plays, beside a back who is on the field with no
touch and so IS an appearance (the confirmation's O4f / R8). Because the game
set reads participation, every other `usage.*` and `recent.*` field is tagged
`derived:participation+pbp` (round 5; it read "pbp"; both parts are
unrestricted, and no value changed). **2026 has no participation file**, so
there a back's game is a touch only: in 2023-25 about 12% of RB
appearance-games (222 / 199 / 186 of ~1,650 a season) are on the field with no
touch, and for the 49 backs with 100+ designed runs in 2025 a touch-only game
set moves carry share by 0.0 points at the median (largest 2.64, 6% of them by
more than a point) and carries per game by at most 8% (the Sonnet
confirmation's count) -- a model fit on 2023-25 and judged on 2026 sees that
shift. Re-measured (§5.1):
18 k moved, every one an RB usage metric -- carry share r 0.911 / k 16.95 -> 0.906 / k 17.8, RB-carry share 15.5 -> 16.4, carries per game 1.10 -> 1.12, targets per game 1.78 -> 1.86, target share 57.9 -> 59.9, on-field share 42.9 -> 45.8 (the seven situational on-field shares 3-6% up likewise), red-zone share 10.4 -> 10.8, goal-line 4.96 -> 4.92, the touch shares 3-4% up; snap share (PFR) and targets per on-field dropback did not move. Round 3's G2 measurement (carry share k 17.95) had removed only the special-teams-only lines; this rule also drops the lines PFR alone lists with offensive snaps, which is why it lands at 17.8, beside the Opus re-check's offense-only 17.76. The 18 `rushing.by_box.*` fields read participation's
box count, which is NGS charting before 2023, and are tagged unrestricted
(`derived:participation+ftn+pbp`) by the owner's participation decision;
`schema.json` says the same beside each.

#### `RushSnapshot.rb(pid, team=None)` (178 fields)

- `usage.carries_pg` -- per_game, observed, source derived:participation+pbp, metric `rb_carries_pg`
- `usage.targets_pg` -- per_game, observed, source derived:participation+pbp, metric `rb_targets_pg`
- `usage.carry_share` -- share, observed, source derived:participation+pbp, metric `rb_carry_share`
- `usage.rb_carry_share` -- share, observed, source derived:participation+pbp, metric `rb_rb_carry_share`
- `usage.target_share` -- share, observed, source derived:participation+pbp, metric `rb_target_share`
- `usage.rz_carry_share` -- share, observed, source derived:participation+pbp, metric `rb_rz_share`
- `usage.gl_carry_share` -- share, observed, source derived:participation+pbp, metric `rb_gl_share`
- `usage.early_touch_share` -- share, observed, source derived:participation+pbp, metric `rb_early_touch_share`
- `usage.third_touch_share` -- share, observed, source derived:participation+pbp, metric `rb_third_touch_share`
- `usage.two_minute_touch_share` -- share, observed, source derived:participation+pbp, metric `rb_two_minute_touch_share`
- `usage.snap_share_charted` -- share, observed, source participation, metric `rb_sit_all`
- `usage.early_onfield` -- share, observed, source participation, metric `rb_sit_early`
- `usage.third_onfield` -- share, observed, source participation, metric `rb_sit_third`
- `usage.short_yardage_onfield` -- share, observed, source participation, metric `rb_sit_short_yardage`
- `usage.red_zone_onfield` -- share, observed, source participation, metric `rb_sit_red_zone`
- `usage.goal_line_onfield` -- share, observed, source participation, metric `rb_sit_goal_line`
- `usage.two_minute_onfield` -- share, observed, source participation, metric `rb_sit_two_minute`
- `usage.neutral_onfield` -- share, observed, source participation, metric `rb_sit_neutral`
- `usage.onfield_plays_current` -- count, observed, source participation
- `usage.snap_share` -- share, observed, source pfr_snaps, metric `rb_snap_share`
- `usage.tgt_per_db` -- share, proxy, source derived:participation+pbp, metric `rb_tgt_per_db`
- `usage.tgt_per_db_vs_man` -- share, proxy, source derived:participation+pbp, metric `rb_tgt_per_db_vs_man_dev`
- `usage.tgt_per_db_vs_zone` -- share, proxy, source derived:participation+pbp, metric `rb_tgt_per_db_vs_zone_dev`
- `rushing.ypc` -- yds_per_carry, modelled, source pbp, metric `rb_ypc`
- `rushing.success` -- share, modelled, source pbp, metric `rb_success`
- `rushing.epa` -- epa_per_play, modelled, source pbp, metric `rb_epa`
- `rushing.stuff` -- share, modelled, source pbp, metric `rb_stuff`
- `rushing.expl10` -- share, modelled, source pbp, metric `rb_expl10`
- `rushing.expl20` -- share, modelled, source pbp, metric `rb_expl20`
- `rushing.first_down` -- share, modelled, source pbp, metric `rb_fd`
- `rushing.td_rate` -- share, modelled, source pbp, metric `rb_td`
- `rushing.short_yardage_conv` -- share, modelled, source pbp, metric `rb_sy_conv`
- `rushing.line_yds` -- line_yds_per_carry, modelled, source pbp, metric `rb_line_yds`
- `rushing.by_direction.<dir>.ypc` -- yds_per_carry, modelled, source pbp, metric `rb_dir_ypc_dev`
- `rushing.by_direction.<dir>.success` -- share, modelled, source pbp, metric `rb_dir_success_dev`
- `rushing.by_direction.<dir>.epa` -- epa_per_play, modelled, source pbp, metric `rb_dir_epa_dev`
- `rushing.by_direction.<dir>.stuff` -- share, modelled, source pbp, metric `rb_dir_stuff_dev`
- `rushing.by_direction.<dir>.expl10` -- share, modelled, source pbp, metric `rb_dir_expl10_dev`
- `rushing.by_direction.<dir>.expl20` -- share, modelled, source pbp, metric `rb_dir_expl20_dev`
- `rushing.by_direction.<dir>.share` -- share, modelled, source pbp, metric `rb_dir_share`
- `rushing.by_side.<side>.ypc` -- yds_per_carry, modelled, source pbp, metric `rb_side_ypc_dev`
- `rushing.by_side.<side>.success` -- share, modelled, source pbp, metric `rb_side_success_dev`
- `rushing.by_side.<side>.epa` -- epa_per_play, modelled, source pbp, metric `rb_side_epa_dev`
- `rushing.by_side.<side>.stuff` -- share, modelled, source pbp, metric `rb_side_stuff_dev`
- `rushing.by_side.<side>.expl10` -- share, modelled, source pbp, metric `rb_side_expl10_dev`
- `rushing.by_side.<side>.expl20` -- share, modelled, source pbp, metric `rb_side_expl20_dev`
- `rushing.by_side.<side>.share` -- share, modelled, source pbp, metric `rb_side_share`
- `rushing.by_box.<box>.ypc` -- yds_per_carry, modelled, source derived:participation+ftn+pbp, metric `rb_box_ypc_dev`
- `rushing.by_box.<box>.success` -- share, modelled, source derived:participation+ftn+pbp, metric `rb_box_success_dev`
- `rushing.by_box.<box>.epa` -- epa_per_play, modelled, source derived:participation+ftn+pbp, metric `rb_box_epa_dev`
- `rushing.by_box.<box>.stuff` -- share, modelled, source derived:participation+ftn+pbp, metric `rb_box_stuff_dev`
- `rushing.by_box.<box>.expl10` -- share, modelled, source derived:participation+ftn+pbp, metric `rb_box_expl10_dev`
- `rushing.by_box.<box>.expl20` -- share, modelled, source derived:participation+ftn+pbp, metric `rb_box_expl20_dev`
- `rushing.by_personnel_faced.<pers>.ypc` -- yds_per_carry, modelled, source derived:participation+pbp, metric `rb_pers_ypc_dev`
- `rushing.by_personnel_faced.<pers>.success` -- share, modelled, source derived:participation+pbp, metric `rb_pers_success_dev`
- `rushing.by_personnel_faced.<pers>.epa` -- epa_per_play, modelled, source derived:participation+pbp, metric `rb_pers_epa_dev`
- `rushing.by_personnel_faced.<pers>.stuff` -- share, modelled, source derived:participation+pbp, metric `rb_pers_stuff_dev`
- `rushing.by_personnel_faced.<pers>.expl10` -- share, modelled, source derived:participation+pbp, metric `rb_pers_expl10_dev`
- `rushing.by_personnel_faced.<pers>.expl20` -- share, modelled, source derived:participation+pbp, metric `rb_pers_expl20_dev`
- `rushing.by_formation.<form>.ypc` -- yds_per_carry, modelled, source pbp, metric `rb_sg_ypc_dev`
- `rushing.by_formation.<form>.success` -- share, modelled, source pbp, metric `rb_sg_success_dev`
- `rushing.by_formation.<form>.epa` -- epa_per_play, modelled, source pbp, metric `rb_sg_epa_dev`
- `rushing.by_formation.<form>.stuff` -- share, modelled, source pbp, metric `rb_sg_stuff_dev`
- `rushing.by_formation.<form>.expl10` -- share, modelled, source pbp, metric `rb_sg_expl10_dev`
- `rushing.by_formation.<form>.expl20` -- share, modelled, source pbp, metric `rb_sg_expl20_dev`
- `rushing.shotgun_share` -- share, modelled, source pbp, metric `rb_sg_share`
- `rushing.line.ybc` -- yds_per_carry, modelled, source pfr, metric `rb_ybc`
- `rushing.back.yac` -- yds_per_carry, modelled, source pfr, metric `rb_yac`
- `rushing.back.broken_tackles` -- per_carry, modelled, source pfr, metric `rb_btk`
- `rushing.back.ryoe` -- yds_per_carry, modelled, source ngs, metric `rb_ryoe`
- `rushing.back.ryoe_carry_coverage` -- share, observed, source derived:ngs+pbp
- `rushing.back.eight_box_share_ngs` -- share, observed, source ngs
- `rushing.back.time_to_los` -- seconds, observed, source ngs
- `receiving.catch` -- share, modelled, source pbp, metric `rb_catch`
- `receiving.ypt` -- yds_per_target, modelled, source pbp, metric `rb_ypt`
- `receiving.yac_rec` -- yds_per_reception, modelled, source pbp, metric `rb_yac_rec`
- `receiving.adot` -- yds, modelled, source pbp, metric `rb_adot`
- `receiving.screen_share` -- share, modelled, source derived:ftn+pbp, metric `rb_screen_share`
- `receiving.checkdown_share` -- share, proxy, source derived:ftn+pbp, metric `rb_chk_share`
- `receiving.ypt_vs_man` -- yds_per_target, observed, source derived:participation+pbp
- `receiving.ypt_vs_zone` -- yds_per_target, observed, source derived:participation+pbp
- `recent.carries_pg` -- per_game, observed, source derived:participation+pbp
- `recent.carry_share` -- share, observed, source derived:participation+pbp
- `recent.target_share` -- share, observed, source derived:participation+pbp
- `recent.snap_share` -- share, observed, source pfr_snaps
- `recent.rz_carry_share` -- share, observed, source derived:participation+pbp
- `any_snap.games` -- games, observed, source derived:pbp+pfr_snaps
- `any_snap.carries_pg` -- per_game, observed, source derived:pbp+pfr_snaps
- `any_snap.targets_pg` -- per_game, observed, source derived:pbp+pfr_snaps
- `any_snap.carry_share` -- share, observed, source derived:pbp+pfr_snaps
- `any_snap.target_share` -- share, observed, source derived:pbp+pfr_snaps
- `any_snap.recent_carry_share` -- share, observed, source derived:pbp+pfr_snaps

#### `RushSnapshot.qb(pid, team=None)` (45 fields)

- `designed.per_game` -- per_game, observed, source derived:ftn+pbp
- `designed.rate` -- per_play, modelled, source derived:ftn+pbp, metric `qb_dsg_rate`
- `designed.ypc` -- yds_per_carry, modelled, source derived:ftn+pbp, metric `qb_dsg_ypc`
- `designed.success` -- share, modelled, source derived:ftn+pbp, metric `qb_dsg_success`
- `designed.expl10` -- share, modelled, source derived:ftn+pbp, metric `qb_dsg_expl10`
- `designed.yds_rate` -- yds_per_play, modelled, source derived:ftn+pbp, metric `qb_dsg_yds_rate`
- `designed.rpo_keeps_rate` -- per_play, modelled, source derived:ftn+pbp, metric `qb_rpo_rate`
- `designed.rpo_share_of_designed` -- share, observed, source derived:ftn+pbp
- `designed.read_option` -- share, not_available, source none
- `designed.by_side.edge` -- share, observed, source derived:ftn+pbp
- `designed.by_side.tackle` -- share, observed, source derived:ftn+pbp
- `designed.by_side.interior` -- share, observed, source derived:ftn+pbp
- `sneak.rate` -- per_play, modelled, source derived:ftn+pbp, metric `qb_sneak_rate`
- `sneak.per_game` -- per_game, observed, source derived:ftn+pbp
- `sneak.short_yardage_conv` -- share, modelled, source derived:ftn+pbp, metric `qb_sneak_conv`
- `scramble.rate` -- share, modelled, source pbp, metric `qb_scr_rate`
- `scramble.ypa` -- yds_per_carry, modelled, source pbp, metric `qb_scr_ypa`
- `scramble.expl10` -- share, modelled, source pbp, metric `qb_scr_expl10`
- `scramble.per_game` -- per_game, observed, source pbp
- `scramble.yds_per_db` -- yds_per_dropback, modelled, source pbp, metric `qb_scr_yds_db`
- `scramble.yds_per_pressured_db` -- yds_per_dropback, modelled, source derived:participation+pbp, metric `qb_scr_yds_press`
- `scramble.vs.pressure` -- share, modelled, source derived:participation+pbp, metric `qb_scr_press_dev`
- `scramble.vs.no_pressure` -- share, modelled, source derived:participation+pbp, metric `qb_scr_nopress_dev`
- `scramble.vs.blitz` -- share, modelled, source derived:ftn+pbp, metric `qb_scr_blitz_dev`
- `scramble.vs.no_blitz` -- share, modelled, source derived:ftn+pbp, metric `qb_scr_noblitz_dev`
- `scramble.vs.man` -- share, modelled, source derived:participation+pbp, metric `qb_scr_man_dev`
- `scramble.vs.zone` -- share, modelled, source derived:participation+pbp, metric `qb_scr_zone_dev`
- `scramble.vs.single_high` -- share, modelled, source derived:participation+pbp, metric `qb_scr_single_dev`
- `scramble.vs.two_high` -- share, modelled, source derived:participation+pbp, metric `qb_scr_two_dev`
- `scramble.share_under_pressure` -- share, observed, source derived:participation+pbp
- `scramble.pressure_rate_faced` -- share, observed, source derived:participation+pbp
- `scramble.man_rate_faced` -- share, observed, source derived:participation+pbp
- `kneel.per_game` -- per_game, observed, source pbp, metric `qb_kneel_pg`
- `kneel.yds_per_game` -- yds_per_game, observed, source pbp
- `aborted.per_game` -- per_game, observed, source pbp
- `aborted.yds_per_game` -- yds_per_game, observed, source pbp
- `scoring.rush_td_rate` -- per_play, modelled, source pbp, metric `qb_td_rate`
- `scoring.rz_rush_rate` -- per_play, modelled, source pbp, metric `qb_rz_rate`
- `scoring.gl_rush_rate` -- per_play, modelled, source pbp, metric `qb_gl_rate`
- `scoring.rush_td_pg` -- per_game, observed, source pbp
- `scoring.x10_rate` -- per_play, modelled, source pbp, metric `qb_x10_rate`
- `totals.rushes_pg` -- per_game, observed, source pbp
- `totals.yds_pg` -- yds_per_game, observed, source pbp
- `hit_sack_rate` -- share, proxy, source pbp, metric `qb_hit_sack_rate`
- `pressure_pfr` -- share, proxy, source pfr

#### `RushSnapshot.defense_run(team)` (152 fields)

- `vs_rb.ypc` -- yds_per_carry, modelled, source pbp, metric `def_rb_ypc`
- `vs_rb.success` -- share, modelled, source pbp, metric `def_rb_success`
- `vs_rb.epa` -- epa_per_play, modelled, source pbp, metric `def_rb_epa`
- `vs_rb.stuff` -- share, modelled, source pbp, metric `def_rb_stuff`
- `vs_rb.expl10` -- share, modelled, source pbp, metric `def_rb_expl10`
- `vs_rb.expl20` -- share, modelled, source pbp, metric `def_rb_expl20`
- `vs_rb.line_yds` -- line_yds_per_carry, modelled, source pbp, metric `def_rb_line_yds`
- `vs_rb.first_down` -- share, modelled, source pbp, metric `def_rb_fd`
- `vs_rb.short_yardage_conv` -- share, modelled, source pbp, metric `def_rb_sy_conv`
- `vs_rb.by_direction.<dir>.ypc` -- yds_per_carry, modelled, source pbp, metric `def_dir_ypc_dev`
- `vs_rb.by_direction.<dir>.success` -- share, modelled, source pbp, metric `def_dir_success_dev`
- `vs_rb.by_direction.<dir>.epa` -- epa_per_play, modelled, source pbp, metric `def_dir_epa_dev`
- `vs_rb.by_direction.<dir>.stuff` -- share, modelled, source pbp, metric `def_dir_stuff_dev`
- `vs_rb.by_direction.<dir>.expl10` -- share, modelled, source pbp, metric `def_dir_expl10_dev`
- `vs_rb.by_direction.<dir>.expl20` -- share, modelled, source pbp, metric `def_dir_expl20_dev`
- `vs_rb.by_side.<side>.ypc` -- yds_per_carry, modelled, source pbp, metric `def_side_ypc_dev`
- `vs_rb.by_side.<side>.success` -- share, modelled, source pbp, metric `def_side_success_dev`
- `vs_rb.by_side.<side>.epa` -- epa_per_play, modelled, source pbp, metric `def_side_epa_dev`
- `vs_rb.by_side.<side>.stuff` -- share, modelled, source pbp, metric `def_side_stuff_dev`
- `vs_rb.by_side.<side>.expl10` -- share, modelled, source pbp, metric `def_side_expl10_dev`
- `vs_rb.by_side.<side>.expl20` -- share, modelled, source pbp, metric `def_side_expl20_dev`
- `vs_rb.by_box.<box>.ypc` -- yds_per_carry, modelled, source derived:participation+ftn+pbp, metric `def_box_ypc_dev`
- `vs_rb.by_box.<box>.success` -- share, modelled, source derived:participation+ftn+pbp, metric `def_box_success_dev`
- `vs_rb.by_box.<box>.epa` -- epa_per_play, modelled, source derived:participation+ftn+pbp, metric `def_box_epa_dev`
- `vs_rb.by_box.<box>.stuff` -- share, modelled, source derived:participation+ftn+pbp, metric `def_box_stuff_dev`
- `vs_rb.by_box.<box>.expl10` -- share, modelled, source derived:participation+ftn+pbp, metric `def_box_expl10_dev`
- `vs_rb.by_box.<box>.expl20` -- share, modelled, source derived:participation+ftn+pbp, metric `def_box_expl20_dev`
- `vs_qb_designed.ypc` -- yds_per_carry, observed, source pbp
- `vs_qb_designed.success` -- share, observed, source pbp
- `vs_sneak.conv` -- share, observed, source derived:ftn+pbp
- `box.light` -- share, modelled, source derived:participation+ftn+pbp, metric `def_light_share`
- `box.heavy` -- share, modelled, source derived:participation+ftn+pbp, metric `def_heavy_share`
- `box.mean` -- defenders, modelled, source derived:participation+ftn+pbp, metric `def_box_mean`
- `box.by_offense_personnel.<op>.heavy` -- share, modelled, source derived:participation+ftn+pbp, metric `def_box_op_heavy_dev`
- `box.by_offense_personnel.<op>.light` -- share, modelled, source derived:participation+ftn+pbp, metric `def_box_op_light_dev`
- `box.by_offense_personnel.<op>.mean_box` -- defenders, observed, source derived:participation+ftn+pbp
- `box.by_offense_personnel.<op>.nickel` -- share, observed, source participation
- `box.by_offense_personnel.<op>.dime` -- share, observed, source participation
- `box.by_offense_personnel.<op>.base` -- share, observed, source participation
- `box.by_formation.<form>.heavy` -- share, modelled, source derived:participation+ftn+pbp, metric `def_box_sg_dev`
- `box.by_formation.<form>.mean_box` -- defenders, observed, source derived:participation+ftn+pbp
- `box.on_designed_runs.heavy` -- share, observed, source derived:participation+ftn+pbp
- `box.on_designed_runs.mean_box` -- defenders, observed, source derived:participation+ftn+pbp
- `personnel.nickel` -- share, modelled, source participation, metric `def_nickel_share`
- `personnel.dime` -- share, modelled, source participation, metric `def_dime_share`
- `personnel.base` -- share, modelled, source participation, metric `def_base_share`
- `front_proxy.dl3_or_fewer` -- share, proxy, source participation, metric `def_dl3_share`
- `front_proxy.dl4` -- share, proxy, source participation, metric `def_dl4_share`
- `front_proxy.dl5_plus` -- share, proxy, source participation
- `contact.ybc_allowed` -- yds_per_carry, modelled, source pfr, metric `def_ybc`
- `contact.yac_allowed` -- yds_per_carry, modelled, source pfr, metric `def_yac`
- `contact.missed_tackles_pg` -- per_game, modelled, source pfr, metric `def_mtkl_pg`
- `contact.missed_tackle_rate` -- share, modelled, source pfr, metric `def_mtkl_rate`
- `vs_rb_receiving.tgt_per_db` -- share, modelled, source pbp, metric `def_rb_tgt_db`
- `vs_rb_receiving.ypt` -- yds_per_target, modelled, source pbp, metric `def_rb_ypt`
- `vs_rb_receiving.yac_rec` -- yds_per_reception, modelled, source pbp, metric `def_rb_yac_rec`
- `vs_rb_receiving.catch` -- share, modelled, source pbp, metric `def_rb_catch`
- `per_game.rb_carries` -- per_game, observed, source pbp
- `per_game.rb_rush_yds` -- yds_per_game, observed, source pbp
- `per_game.opp_plays` -- per_game, observed, source pbp

#### `RushSnapshot.defense_qb(team, rates_from=None)` (32 fields)

- `scrambles.oe_ratio` -- ratio, modelled, source pbp, metric `def_scrambles_oe`
- `scrambles.oe_per_opp` -- per_opportunity, observed, source pbp
- `scrambles.raw_rate` -- rate, observed, source pbp
- `scramble_yds.oe_ratio` -- ratio, modelled, source pbp, metric `def_scramble_yds_oe`
- `scramble_yds.oe_per_opp` -- per_opportunity, observed, source pbp
- `scramble_yds.raw_rate` -- rate, observed, source pbp
- `designed_runs.oe_ratio` -- ratio, modelled, source pbp, metric `def_designed_runs_oe`
- `designed_runs.oe_per_opp` -- per_opportunity, observed, source pbp
- `designed_runs.raw_rate` -- rate, observed, source pbp
- `designed_yds.oe_ratio` -- ratio, modelled, source pbp, metric `def_designed_yds_oe`
- `designed_yds.oe_per_opp` -- per_opportunity, observed, source pbp
- `designed_yds.raw_rate` -- rate, observed, source pbp
- `explosive_runs.oe_ratio` -- ratio, modelled, source pbp, metric `def_explosive_runs_oe`
- `explosive_runs.oe_per_opp` -- per_opportunity, observed, source pbp
- `explosive_runs.raw_rate` -- rate, observed, source pbp
- `rush_tds.oe_ratio` -- ratio, modelled, source pbp, metric `def_rush_tds_oe`
- `rush_tds.oe_per_opp` -- per_opportunity, observed, source pbp
- `rush_tds.raw_rate` -- rate, observed, source pbp
- `red_zone_rushes.oe_ratio` -- ratio, modelled, source pbp, metric `def_red_zone_rushes_oe`
- `red_zone_rushes.oe_per_opp` -- per_opportunity, observed, source pbp
- `red_zone_rushes.raw_rate` -- rate, observed, source pbp
- `hit_sack_rate.oe_ratio` -- ratio, proxy, source pbp, metric `def_hit_sack_rate_oe`
- `hit_sack_rate.oe_per_opp` -- per_opportunity, proxy, source pbp
- `hit_sack_rate.raw_rate` -- rate, proxy, source pbp
- `containment_proxy.oe_ratio` -- ratio, proxy, source derived:participation+pbp, metric `def_containment_proxy_oe`
- `containment_proxy.oe_per_opp` -- per_opportunity, proxy, source derived:participation+pbp
- `containment_proxy.raw_rate` -- rate, proxy, source derived:participation+pbp
- `pressure` -- share, modelled, source derived:participation+pbp, metric `def_pressure`
- `pressure_pfr` -- share, proxy, source pfr, metric `def_pressure_pfr`
- `blitz` -- share, modelled, source derived:ftn+pbp, metric `def_blitz`
- `scramble_rate_raw` -- share, observed, source pbp
- `scramble_ypa_allowed` -- yds_per_carry, observed, source pbp

#### `RushSnapshot.offense_line(team)` (14 fields)

- `line_yds` -- line_yds_per_carry, modelled, source pbp, metric `ol_line_yds`
- `stuff` -- share, modelled, source pbp, metric `ol_stuff`
- `success` -- share, modelled, source pbp, metric `ol_success`
- `ybc` -- yds_per_carry, modelled, source pfr, metric `ol_ybc`
- `by_gap.<side>.success` -- share, modelled, source pbp, metric `ol_gap_success_dev`
- `by_gap.<side>.line_yds` -- line_yds_per_carry, observed, source pbp
- `starters.out_report` -- count, observed, source derived:injuries+pfr_snaps
- `starters.missed_last_game` -- count, observed, source pfr_snaps
- `starters.continuity` -- share, observed, source pfr_snaps
- `ol_listed_out` -- count, observed, source injuries

#### `RushSnapshot.team_context(team)` (10 fields)

- `neutral_run_rate` -- share, modelled, source pbp, metric `tm_neutral_run`
- `leading_run_rate` -- share, modelled, source pbp, metric `tm_lead_run`
- `trailing_run_rate` -- share, modelled, source pbp, metric `tm_trail_run`
- `red_zone_run_rate` -- share, modelled, source pbp, metric `tm_rz_run`
- `early_down_run_rate` -- share, modelled, source pbp, metric `tm_early_run`
- `plays_pg` -- per_game, modelled, source pbp, metric `tm_plays_pg`
- `qb_keep_share` -- share, modelled, source pbp, metric `tm_qb_keep`
- `rpo_rate` -- share, modelled, source derived:ftn+pbp, metric `tm_rpo`
- `rb_carries_pg` -- per_game, observed, source pbp
- `designed_runs_pg` -- per_game, observed, source pbp

Non-field keys on the profiles: `as_of` [season, week], `weights` {season:
weight}, `k_source`, `aux` {season: "aligned" / "stale" / "missing"} (M3), `games` {"current", "weighted"}, `scope` (rb),
`exposure_basis` and `totals.partition` {designed, sneak, scramble, kneel,
aborted} (qb), `observed` / `expected` / `opportunities` (each defense_qb
block), `starters.five` (offense_line), `rpo_split` (team_context),
`rushing.back.ryoe_games` and `receiving.targets` (rb).

Definitions the model must not re-derive differently:
- **Official rushes** (Kalshi's KXNFLRSHYDS settles this way, inferred from 239 of
  239 settled 2025 QB ladders, `rush_data_sources.md` §9.2): designed runs
  (sneaks included) + scrambles + kneels + aborted snaps with the player as
  rusher; yards = nflverse `rushing_yards`. Sacks are never rushes. §4.1 shows this
  matches Sleeper's box on 100% of 2023-25 QB-games (attempts) and 99.76% (yards).
  What Kalshi's ladders can and cannot tell (the reviews' m4): sacks are
  testable and excluded -- on the Oct-Dec 2025 QB ladders adding sack yards fits
  128 of 239 and all 111 that discriminate favour excluding them (the
  data-source report's window, Sonnet's count); on settled ladders dated Sep
  2025-Jan 2026 the Opus re-check's own code finds all 117 discriminating
  ladders favour excluding them; aborted snaps are NOT
  tested -- no ladder discriminates (including or excluding them both fit
  239 / 239), so "aborted snaps settle as rushing" follows the NFL's scoring
  and Sleeper's box (§4.1), not a settlement record.
- **Changed paths since the first report**: `rushing.line.line_yds` is gone
  (it duplicated `rushing.line_yds` -- N4); added `defense_qb.scramble_ypa_allowed`
  (raw yards per scramble faced), `qb.hit_sack_rate` and
  `defense_qb.hit_sack_rate.*` (F14), `offense_line.ol_listed_out` (F14);
  `designed.read_option` is kind `not_available`; every `containment_proxy`
  leaf is kind proxy; `recent.*` carry a basis; `rb().any_snap.*` (round 4,
  restricted display).

- **Designed run** = a pbp `RUN` (rush_attempt, not a scramble, kneel or spike)
  that was not an aborted snap. For QBs, `designed.*` EXCLUDES sneaks (`sneak.*`).
- **Exposure** (QB rates per play) = the team's offensive snaps x his share of its
  dropbacks, game by game.
- **Dropback** = attempt + sack + scramble (nflverse leaves the passer empty on
  scrambles; the scrambler is the rusher).
- **Line yards**: this module's stated convention (120% of losses, 100% of 0-4,
  50% of 5-10, 0 past 10), NOT Football Outsiders' official adjusted line yards
  (§3.4).

### 0.4 The dataset

`research/data/nfl_rush/rb_games_2023_2026.csv.gz` (6,041 rows x 905 columns)
and `qb_games_2023_2026.csv.gz` (2,201 x 392); column meanings, units, kinds,
human-readable sources and machine `source_tag`s in `schema.json` ({"license",
"k_note", "kind_note", "rb": [...], "qb": [...]}). A profile field at path
`a.b.c` is the column `<prefix>a_b_c` with `_raw` (unshrunk) and `_n`
(denominator) beside it; a designed-run rate block writes one `<block>_carries`
instead of six `_n`. Prefixes: `p_` rb(pid, team), `q_` qb(pid), `d_`
defense_run(opp) (QB rows: without the direction / side / box / personnel
cells), `dq_` defense_qb(opp) (RB rows: pressure, blitz and the hit + sack
proxy only), `ol_` offense_line(team) (so `ol_ol_listed_out`), `tc_`
team_context(team); of rb()'s restricted `any_snap` block only
`p_any_snap_games` (the PFR-inclusive weighted games) is written. Outcomes `act_*` -- `act_played` is OFFENSIVE
participation only (a rush, target, dropback or offensive snap), `act_any_snap`
adds special teams (PFR `st_snaps`; Kalshi settles a player on one snap).
Sleeper's lines `sl_proj_*`, `in_sleeper` and `sl_qb1` come from the revised
POST-WEEK snapshot (NOT provably pre-kickoff) and the box `sl_act_*`. Each
row's shrunk `value` columns use its season's k frame: 2023-2025 rows
`k_fit_only` (pooled 2018-2024), 2026 rows the full 2018-2025 table
(`meta.json` `k_frames`, `schema.json` `k_note`). Rows of a week come from a
snapshot frozen before that week's games.

## 1. Files

| file | role |
|---|---|
| `nfl_rush_data.py` | production-safe, stdlib only: fetch (the agreed `_fresh` / `fetch` by import), parse and compact the auxiliary sources into `nfl_rush_aux_<season>` (own `SCHEMA` 2), the PFR -> gsis crosswalk, each block's `coverage`, `refresh()` as a deep-job body (one season's failure never stops the next) |
| `nfl_rush_profiles.py` | production-safe, stdlib only: the as-of rushing tallies and `RushSnapshot` profiles (§0), the measured k table (`_K_DEFAULT` == `reliability.json`), the source tags (`SOURCE_OF`, `restricted`) |
| `research/nfl_rush_dataset.py` | numpy (lazily): the split-half reliability (three splits), persistence and sneak-rule measurements, the data-quality checks, the RB / QB dataset build |
| `research/data/nfl_rush/` | `rb_games_2023_2026.csv.gz` (7.68 MB), `qb_games_2023_2026.csv.gz` (1.57 MB), `schema.json`, `meta.json`, `reliability.json`, `persistence.json`, `sneak_rule.json`, `checks.json` -- 9.99 MB in all (under the 10 MB cap, narrowly), every file rebuilt by the script |
| `tests/guards_nfl_rush_data.py` | 198 hermetic checks in 26 isolated sections, `run(ck)`, standalone exit code, ~1 s; mutation-tested against both reviewers' mutant sets and the author's (§7) |

Credits, carried in every committed table's `license` block (`schema.json`
included since the review's N5 / m11; the two CSVs carry none in-file -- their
credit is `schema.json`'s and `meta.json`'s): **FTN Data via nflverse** (CC BY-SA
4.0; tables derived from FTN charting are shared under CC BY-SA 4.0);
nflverse-data (CC BY 4.0) for pbp, participation, players and injuries;
participation through 2022 also needs the credit **"NFL NextGenStats via
nflverse (for 2022 and earlier)"** (`load_participation`); **Pro Football
Reference** advanced stats AND snap counts (nflreadr: snap counts are "provided
by Pro Football Reference") via nflverse; **NFL Next Gen Stats** via nflverse
(NFL-owned); DynastyProcess for Sleeper ids.

**Licence default (F14, until the owner decides otherwise).** Sports
Reference's terms (`sports-reference.com/termsofuse.html`, "Last Updated: May
19, 2023"), section 5, bar using its Content "to create any database, archive,
or other data store that competes with or constitutes a material substitute for
the services or data stores offered on the Site" and "for purposes of training,
fine-tuning, prompting, or instructing artificial intelligence models or
technologies in any manner, including without limitation for purposes of ...
(ii) supporting machine learning methods used to predict, classify, label, or
score inputs into the models". PFR advanced stats and PFR snap counts are that
Content even via nflverse. The NFL.com terms (`nfl.com/legal/terms/`, "Updated:
May 16, 2024") limit use to "individual non-commercial and informational
purposes", bar systematic retrieval to compile a database, and prohibit "using
the Services to gamble or wager on the result of any NFL game"; whether they
follow the nflverse-hosted NGS copies is not settled by the text. So: every
profile leaf carries `source` (`pbp`, `participation`, `ftn`, `injuries`, `pfr`,
`pfr_snaps`, `ngs`, `none`, or `derived:<a>+<b>...`), `schema.json` mirrors it
per column as `source_tag`, and `RP.SOURCE_OF(kind, path)` /
`RP.restricted(tag)` let the model filter: **a field whose tag names `pfr`,
`pfr_snaps` or `ngs` is DISPLAYED with credit and never enters a fitted model
feature** (contact / YBC / YAC / broken and missed tackles, PFR pressure, every
PFR snap share and the OL starters' snap-based fields, RYOE and the NGS box
share and time to LOS). The fields themselves stay: the board shows them with
credit and the owner may decide to use them. Participation through 2022 is NGS
charting by its own credit line; it is tagged `participation` and the owner's
decision on NFL-sourced data covers it too. Open owner decisions: PFR-derived
features in a predictive model; NFL-sourced data for wagering.

Local run (deep store and raw files in scratch; the agreed stores were COPIED
from `deep_nfl_matchup`, never rebuilt):

    export DEEP_CACHE_DIR=<scratch>/deep_nfl_rush VIGIL_NO_BG=1
    python3 -c "import nfl_rush_data as R; R.refresh(seasons=range(2018, 2027), raw_dir='<scratch>/data/nflverse')"
    python3 -m research.nfl_rush_dataset measure
    python3 -m research.nfl_rush_dataset checks --raw <scratch>/data/nflverse
    python3 -m research.nfl_rush_dataset build --sleeper <scratch>/data/sleeper

(This session's aux stores were built by `build_aux` over the same files
without the network refresh, because the curl-downloaded raw files carry no
freshness record and `refresh` would have fetched all of them again once.)

## 2. Sources, joins and coverage

### 2.1 What each auxiliary block is (`nfl_rush_aux_<season>`)

| block | source | seasons | per season | key |
|---|---|---|---|---|
| `extras` | nflverse pbp `play_id`, `aborted_play`, `rushing_yards` | 2016+ | one value per agreed-store row | aligned to `nfl_matchup_<season>` rows |
| `sit` | participation `offense_players` | 2016-2025 (none for 2026) | per game, per RB/FB: on-field plays by situation; the team's plays over the SAME charted plays | gsis |
| `pfr_rush` | `advstats_week_rush_<Y>` | 2018-2026 (2026 wks 1-6) | carries, yards before / after contact, broken tackles (rush, rec) | game_id, gsis |
| `pfr_def` | `advstats_week_def_<Y>` | 2018-2026 (wks 1-6) | per team-game sums: defenders, tackles, missed tackles, pressures, hurries, QB hits, sacks, blitzes | game_id, team |
| `pfr_pass` | `advstats_week_pass_<Y>` | 2021-2026 (2018-20 files absent) | per QB-game: times pressured / hurried / hit / blitzed / sacked | game_id, gsis |
| `ngs` | `ngs_rushing.csv.gz` WEEKLY rows | 2016-2026 (wks 1-7; week 7 = 1 row) | attempts, yards, expected yards, RYOE, efficiency, 8+ box %, time to LOS | week, gsis (+ team) |
| `inj` | `injuries_<Y>` | 2009-2026 (wks 1-7) | game status + practice status, every position | week, team, gsis |
| `snaps` | `snap_counts_<Y>` (PFR) | 2012-2026 (wks 1-6 as downloaded) | team snaps = the MOST offensive snaps any player took (a lineman or the QB plays every snap); only when nobody is at 99.5%+ the max-snap player's snaps / share -- 13 of 4,440 team-games 2018-2026 (2019 4, 2022 3, 2025 3, 2021 / 2024 / 2026 1), counted in `joins.snaps_team`; OL snaps by gsis; RB/QB offensive snaps, share and special-teams snaps (special-teams-only rows kept) | game_id, team |

**The B2 fix.** The first build took the team total as the largest snaps /
share of any player, so a back with 1 snap at 1% made it 100: +7.35 snaps a
game on 2024 and every PFR snap share ~10% low. The total is now the most
offensive snaps any player took (a player is at 100% in 543 of 544 2024
team-games). **Completeness (round 3's G1).** An aux records `complete` and
`absent`: the sources its season should have (`expected_sources`: pbp
2016-2026, participation 2016-2025, PFR rush / def 2018+, PFR pass 2021+,
injuries 2009+, snaps 2012+, the NGS file 2016+) that had no file. Before, a
failed download saved an aux without the block that still matched its store,
stamped "aligned" and was never retried (Sonnet R1 with the network off: no
extras, PFR, injuries or snaps; Opus R3 with one PFR file failing). Now
`refresh` saves NOTHING for a season whose pbp file is absent (an error in the
summary; the extras cannot align without it), an aux missing any other
expected source is saved with `complete` False (ledgered `RUSH-incomplete`),
is never skipped as done, and stamps "incomplete" on the profiles. A short
(late or truncated) block is listed in `missing` but leaves the aux complete:
in season PFR lags the store by a week as a rule. **Coverage (M3).** Every aux records each block's reach against
its store (`coverage`: games for pfr_rush / pfr_def / pfr_pass / snaps / sit,
weeks for injuries / ngs) and lists a short block in `missing` ("a truncated or
late file"); on 2018-2026 every block reaches every store game or week except
the absent 2018-20 PFR pass files (already listed as missing) and 2026's
participation. A PFR file cut at 60% now says so instead of building 173 of 272
games silently.

Build: 3-7 s a season, 46 MB peak RSS for two seasons; pickles 0.69-0.75 MB a
season (2026 so far 0.23 MB).

### 2.2 Joins (`checks.json` `coverage.joins`)

- **pbp extras**: 100% of store rows aligned in every season 2018-2026
  (32,220 ... 34,449 rows; 0 mismatched rows, 0 games with a different row
  count). The alignment replays build_season's filter and checks quarter, down,
  yards, rusher and kind row by row; a mismatch leaves that row (or game) NA and
  ledgers `RUSH-align`.
- **PFR -> gsis**: players.csv `pfr_id` gives 16,988 ids (no conflicts),
  DynastyProcess adds 8 (8 disagreements, players.csv kept). PFR rush rows
  unjoined: 0, 0, 1, 0, 0, 0, 0, 2, 0 (2018-2026; "Nathan Carter" in 2025);
  PFR pass rows unjoined: 0 every season; snap-count skill rows unjoined 0-14 a
  season (special-teams-only rows now kept, m2). 5-20 PFR rushers a season are not in the agreed (skill-only) crosswalk
  (punters, linemen): counted, kept, never read as backs.
- **NGS**: every weekly row's gsis is in the crosswalk (0 unjoined, 0 outside it,
  every season); the 45-55 week-0 SEASON rows a year are dropped by design
  (`rush_data_sources.md` §6: they do not equal the weekly sums).
- **Injuries** carry gsis directly.

### 2.3 NGS is censored (measured, not assumed)

NGS publishes a weekly rushing row only for a game with 10+ rush attempts
(every weekly row 2016-2026 has `rush_attempts >= 10`; ~30 backs a week) and
only for RB/FB (no QB rows). Of the RB designed runs, 71-74% a season fall in
games with an NGS row (2018 73.1%, 2025 74.1%, 2026 72.7%); of RB games with 10+
designed runs, 99.6-100% have one. So `rushing.back.ryoe` is "RYOE per attempt
in his 10+-carry games"; the profile carries `ryoe_carry_coverage` beside it.

### 2.4 2026 (`checks.json` `coverage.aux_2026`)

Store weeks 1-6. PFR rush / def / pass: weeks 1-6. Snap counts: weeks 1-6 (as
downloaded 20:25 UTC). NGS: weeks 1-7 (week 7 is one Thursday row; no store
game to join it to). Injuries: weeks 1-7. Participation: none -- so every
on-field share (`usage.*_onfield`, `snap_share_charted`, `tgt_per_db*`),
box-by-personnel, personnel faced, the DL-count front proxy, pressure and the
scramble pressure / coverage splits read 2024-2025 only on 2026 rows
(`usage.onfield_plays_current` is 0 on every 2026 row). In-season 2026 stand-ins:
PFR snap share, FTN box count, FTN blitz, PFR pressures (`pressure_pfr`, proxy).

## 3. What the profiles measure, what is a proxy, what does not exist

### 3.1 Running backs
Usage (team-scoped, same games both sides; a back's games are the ones pbp or
participation shows him in -- §0.3): carries / targets per game, carry
share of the team's designed runs and of its RB runs (committee), target
share, red-zone and goal-line (inside the 5) carry shares, early / third-down /
two-minute TOUCH shares ((his designed runs + targets) / (team designed runs +
dropbacks) in that situation; every season incl. 2026), on-field shares by
situation (participation, same charted plays; 2016-2025), PFR snap share
(every season), targets per on-field dropback (proxy for targets per route;
the agreed same-plays construction) and vs man / vs zone. Rushing (his, all
teams): YPC, EPA-success, EPA, stuff (<= 0), 10+ and 20+, first-down and TD
rates, 3rd/4th & 1-2 conversion, line yards; by direction x gap, by side
(edge / tackle / interior), by box (<= 6 / 7 / 8+), by defensive personnel
faced (base / nickel / dime), by formation (shotgun / under center), with his
direction and shotgun SHARES. The LINE's part (PFR yards before contact, line
yards) is kept apart from the BACK's (PFR yards after contact, broken tackles,
NGS RYOE). Receiving: catch, YPT, YAC per reception, aDOT, FTN screen share,
FTN CHK share (proxy: CHK is read as checkdown, FTN's dictionary unverified),
YPT vs man / zone (reported, not shrunk).

### 3.2 Quarterbacks
Designed runs (not sneaks, kneels, scrambles, aborted snaps) per game and per
exposure play, YPC, success, 10+, yards per exposure play; RPO keeps (FTN
`is_rpo` on a QB designed run; an RPO that became a pass is a pass); sneaks
(FTN `is_qb_sneak`; rule-coded only where FTN has no row) and their 3rd/4th &
1-2 conversion; scrambles per dropback, yards per scramble, 10+, scramble yards
per dropback and per pressured dropback, and the scramble rate under
pressure / no pressure (the pressure-to-scramble conversion), vs blitz / no
blitz (FTN 5+ rushers), vs man / zone, vs single-high / two-high (2023+
charting only); share of scrambles under pressure; kneels and aborted snaps per
game; rushing TDs, red-zone and goal-line rushes, 10+ runs per exposure play;
totals and the five-way partition. **Read-option keeps are not identifiable**
(the field says so).

### 3.3 Defense, line, context
Defense vs RB runs: YPC / success / EPA / stuff / 10+ / 20+ / line yards /
first downs / short-yardage allowed, by direction, side and box; QB designed
runs and sneaks allowed (reported); box usage (light / heavy / mean) overall, by
offensive personnel (11 / 12 / 21 / 13 / 22 / 10: heavy, light, mean, nickel,
dime, base) and by formation, and on the designed runs it faced; personnel
(nickel / dime / base share); the front PROXY (DL count <= 3 / 4 / 5+:
bodies, not alignment); PFR YBC and YAC allowed, missed tackles per game and
per tackle attempt; RB receiving allowed. Defense vs QB legs: scrambles,
scramble yards, designed runs, designed yards, 10+ runs, rushing TDs, red-zone
rushes and the containment PROXY (scramble yards per pressured dropback),
each observed / expected against the facing QBs' leave-one-out rates, plus
pressure (participation), pressure_pfr (proxy), blitz and the raw scramble
rate. Offensive line: line yards, stuff, success, PFR YBC, success by gap, the
as-of starting five (most PFR snaps over the team's last four games THIS
season) with how many the week's report lists Out/Doubtful and how many missed
half the last game, and the five's snap continuity. Team: neutral (WP 20-80%,
no two-minute drill) / leading by 8+ / trailing by 8+ / red-zone / early-down
run rates, plays per game, QB-keep share of designed runs, FTN RPO rate and
what RPO plays became.

### 3.4 Proxies and conventions (labelled in the data)
- **Line yards** by a stated convention (120% / 100% / 50% / 0%), NOT FO's
  adjusted line yards (`rush_data_sources.md` §4: the definition page 404s,
  secondary sources disagree).
- **Front**: DL count from the personnel labels (kind proxy).
- **Containment**: scramble yards per pressured dropback, OE (kind proxy on every leaf: `oe_ratio`,
  `oe_per_opp`, `raw_rate`).
- **CHK = checkdown** (kind proxy). **Targets per on-field dropback** (proxy for
  per-route). **pressure_pfr** (PFR charting; proxy for 2026 pressure).
- **Exposure plays** for QB rates: team snaps x his dropback share in the game.
- **RPO**: FTN's flag only. FTN and PFR disagree completely on RPO runs (§4.3).

- **Pressure without PFR (F14)**: `qb.hit_sack_rate` and
  `defense_qb.hit_sack_rate` (observed / expected against the facing QBs'
  leave-one-out rates, like the other defense-vs-QB fields) = sacks + QB hits
  that were not sacks, per dropback, from the agreed store's pbp `qb_hit` --
  every season incl. 2026, kind proxy ("a hit or a sack is not a pressure"),
  source pbp, its k measured like the rest (§5.1).
- **OL absences without PFR snaps (F14)**: `offense_line.ol_listed_out` = the
  team's T / G / C (any, not only the starters) listed Out or Doubtful on the
  cutoff week's report; source injuries; None with no report for the week.
- **The owner's "edge setting"** maps to `defense_run.vs_rb.by_side.edge`
  (RB runs to the end gaps allowed) -- a direction split of what a defense
  allows, which is not measurable at this sample (§5.2), so the field reads the
  defense's overall figure moved by the league's edge offset. **"QB yards
  allowed per scramble"** is `defense_qb.scramble_ypa_allowed` (raw; the
  per-dropback scramble-yards OE beside it is the opponent-adjusted version).
- **Pace** = offensive plays per game (`team_context.plays_pg`, kneels and
  spikes out), not seconds per play.

### 3.5 Not available (stated, not approximated)
Run concepts (zone / gap / power / counter / duo / trap / toss / stretch),
fronts as schemes on a play (odd / even, over / under), DL / LB alignment, gap
integrity, QB spies, read-option identification (`designed.read_option`, kind
`not_available`), blocking grades. 2026 participation (box by personnel,
personnel faced, on-field situational shares, pressure on dropbacks) until
after the postseason.

**The nominal base front (3-4 / 4-3) exists for 2025-2026 only, so it is not
used (checked 2026-10-24 for the coordinator's F13).** nflverse
`depth_charts_<Y>` carries ESPN's listed package (`pos_grp` "Base 3-4 D" /
"Base 4-3 D") with a snapshot timestamp (`dt`) only in its new layout:
`depth_charts_2025` (221 snapshots, 2025-08-03 to 2026-03-14) and
`depth_charts_2026` (253 snapshots, 2026-03-22 to 2026-10-23; 19 teams 3-4,
13 teams 4-3 in the latest, two of 32 changing across the snapshots, per the
data-source review). `depth_charts_2023` and `_2024` are the old layout -- one
chart per week (`season, club_code, week, game_type, depth_team, formation,
position, depth_position`), no `pos_grp`, no timestamp. There the front could
only be INFERRED from position codes (2023's defensive codes include NT, ILB,
OLB, DE, DT, DL, MLB, SAM, WILL, EDGE), a proxy of a proxy, and no season
carries both layouts, so the inference cannot be scored against the explicit
field. The old layout's week key is not a timestamp either, so the as-of rule
(the latest snapshot strictly before the cutoff week's first game; refuse a
snapshot without one) cannot hold for 2023-2024. With the fit seasons
uncovered, `defense_run()["front"]["nominal"]` was not added; a 2025-2026-only
field is a change for a later season to earn. The DL-count front PROXY
(participation, 2016-2025, kind proxy) stays.

## 4. Data-quality checks (`checks.json`; 2023-2025 unless stated)

### 4.1 QB and RB rushing against Sleeper's box score
Per player-game with a Sleeper box (`gp` 1), pbp official rushes = designed
(incl. sneaks) + scrambles + kneels + aborted snaps, yards = nflverse
`rushing_yards`:

| | player-games | attempts exact | yards exact | attempts exact WITHOUT kneels | without aborted snaps | without scrambles |
|---|---|---|---|---|---|---|
| QB | 2,046 | **100.0%** | **99.76%** | 63.4% | 88.3% | 35.0% |
| RB | 5,106 | 99.98% | 99.96% | 99.98% | 99.67% | 99.98% |

**Sleeper's rushing figures include kneels** (all 749 QB-games with a kneel
match with kneels counted, none without), aborted snaps and scrambles -- the
same convention Kalshi's KXNFLRSHYDS settles on (`rush_data_sources.md` §9.2,
239 of 239 2025 QB ladders). Sacks are in no rushing figure (0 sacks in the
store carry a rusher id; the guard plants one and proves it is excluded).
Kalshi's ladders (`rush_data_sources.md` §9.2, the reviews' m4): sacks are
tested -- adding sack yards fits 128 of 239 ladders and all 111
discriminating ladders favour excluding them; aborted snaps are untested -- no
ladder discriminates, so their inclusion rests on NFL scoring and Sleeper's
box, not on a settlement record.
The dataset's own `act_rush` / `act_rush_yds` reproduce it: QB 100.0% / 99.75%,
RB 99.98% / 99.96% exact on 1,990 / 4,915 rows.

### 4.2 The QB partition
Every QB rush counted independently from the store equals designed + sneak +
scramble + kneel + aborted, count AND yards, for every QB: 2,285 / 2,352 /
2,215 rushes (2023 / 2024 / 2025), 0 mismatches. Classes: designed 453 / 518 /
357, sneaks 295 / 267 / 259 (all FTN; the rule never fired 2023+), scrambles
1,029 / 1,060 / 1,087, kneels 428 / 405 / 433, aborted 80 / 102 / 79; RPO keeps
56 / 29 / 67; RPO plays that became dropbacks (never rushes) 555 / 349 / 433.

### 4.3 Against PFR's own charting
- **Scrambles** (PFR season `scrambles`, populated 2024+ only): 2024 1,012
  (nflverse) vs 1,007 (PFR) over 47 QBs, r 0.999, 72% exact, 100% within 3;
  2025 1,021 vs 1,028 over 45, r 0.998, 73% exact, 98% within 3.
- **RPO**: FTN flags 3-5x more RPO runs than PFR's `rpo_rush_att` (FTN RPO
  designed runs 888 / 1,224 / 839 / 1,585 vs PFR 276 / 247 / 282 / 309,
  2022-2025) and the two do not agree team by team (r -0.12, 0.09, 0.05, 0.11).
  "RPO" depends on the charter; every RPO figure here is FTN's and says so.
  (A reviewer's independent count read 893 / 1,235 for 2022 / 2023. That is
  every REG `play_type` run with FTN `is_rpo` that is not a scramble, aborted
  snap, two-point try or deleted play, straight off the raw files -- this
  session reproduces it -- and all of them are RUN rows by the agreed store's
  classifier. The 888 / 1,224 above are the store tallies' `rpo_runs`, i.e.
  the rows where the agreed store's own FTN join set the RPO flag; the 5 / 11
  (0.6% / 0.9%) are lost in that join and were not traced further. The
  conclusion -- FTN flags 3-5x PFR's count and the two do not agree by team --
  does not move.)
- **PFR carries** equal official rushes on 100% / 99.91% / 99.96% of
  player-games, all within one. `rushing_yards` differs from `yards_gained` on
  2 / 3 / 1 of ~14,600 official rushes a season.

### 4.4 Direction and gap
Of RB designed runs, run_location is blank on 0.00-0.05% a season; middle
runs are 25-31% and carry no gap by convention; a left / right run with no gap:
0 in every season 2018-2026.

### 4.5 The sneak rule (`sneak_rule.json`)
Against FTN's `is_qb_sneak` on every FTN-charted QB designed run: precision
0.989 / 0.986 / 0.992 / 0.996, recall 0.945 / 0.942 / 0.936 / 0.896
(2022-2025). It is used only where FTN has no row (2021, a 2023 cutoff's
two-seasons-back prior at weight 0.5625).

## 5. Measurements

### 5.1 Split-half reliability and year-over-year persistence
`reliability.json` (2018-2025, odd vs even weeks; teams 256 team-seasons;
RBs are crosswalk RB/FB only -- the review's B1: the first run's "RB" units
included every WR and TE who carried, 2024's tallies held 92 WR and 14 TE
carriers -- 284-528 RB-seasons by gate, QBs 282; k = n(1 - r)/r; **none** = r <=
0.02 or too few units: the profile keeps the prior). YoY r = persistence,
`persistence.json`. Two disclosure splits sit beside every k and decide
nothing (written into the script header before the run): weeks 1-9 vs 10-18
and a fixed mix (weeks 1, 4, 5, 8, 10, 11, 14, 15, 17 vs the rest). **Odd /
even flatters usage**: a role changes through a season, and alternate weeks
straddle every change -- carry share reads r 0.91 / k 18 odd-even but 0.77 /
k 54 first-half vs second-half (snap share 0.91 / k 49 vs 0.73 / k 182); QB
rates are stable on every split (designed-run rate k 36 / 44 / 36, scramble
rate 80 / 81 / 76). The model should read the usage k as optimistic for
forward use (the reviews' m7).

Runs (the rule never changed; each pre-run hash recorded before its run):
- **F1 re-measure** (RB/FB units): 2026-10-23 22:47:43 UTC, script
  `112d5e6c...`, profiles `d60edb65...`, data `4122f839...`. 64 of 171 k
  moved: 59 RB metrics, and five defense departures whose share bases are
  clamped to [0, 1] now (m1: three box-by-personnel / formation, one box 10+,
  one side 10+); every QB, team and other defense k
  reproduced exactly.
- **F14 re-measure** (adds `qb_hit_sack_rate`, `def_hit_sack_rate_oe`):
  2026-10-24 00:16:41 UTC, script `01180bfe...`, profiles `c8796cbf...`, data
  `4122f839...`. Only the two new metrics differ from the F1 run; every other
  pooled block is identical.
- **Round 3 reproduction** (01:01:11 and 01:04:38 UTC): byte-identical but for
  the licence block.
- **Round 4 re-measure** (M8: a back's games from pbp / participation only):
  2026-10-24 02:01:13 UTC, script `02e53c7b...`, profiles `dac01593...`, data
  `247cc625...`. 18 k moved, all RB usage metrics (the same 18 in `k_fit_only`); nothing else in `reliability.json`; `persistence.json` moved on 16 usage metrics by at most 0.006, and the DECAY tendency r stayed 0.602 -> 0.75 (none of the ten tendencies moved). After the run only the dataset's column selection changed (`_p_cols`: the `any_snap` block enters the dataset as its games count alone, to keep the committed files under 10 MB) -- the measurement does not read it.
The phase-1 note on run reproducibility stands with its figure corrected (N1):
one by-season `mean_n_half` moved **4.35 -> 4.34** on set iteration order
between the first runs, fixed by a sorted unit order.

The B1 figures the reviewers re-derived, against the round-4 run: carry share
r 0.906 / k 17.8 (Sonnet's harness 0.905 / k 18; the Opus re-check's
offense-only 0.906 / k 17.76); catch rate r 0.032 / k 877 (0.03 / k 877);
target share 0.792 / k 59.9 (0.792 / 59.9); on-field share 0.905 / k 45.8
(0.905 / 46.2); goal-line share 0.717 / k 4.9 (0.719 / 4.9).
Before the fix: carry share 0.966 / k 6, catch 0.441 / k 47, aDOT 0.92 / k 3,
target share 0.895 / k 27 -- WR and TE carriers' roles, not backs'.

**Running back: efficiency (per designed run, all his teams)**

| metric | r | units | n / half | k | season reliability | k 2018-24 | r / k weeks 1-9 vs 10-18 | r / k fixed mix | YoY r |
|---|---|---|---|---|---|---|---|---|---|
| `rb_epa` | 0.181 | 499 | 79.03 | 358 | 0.307 | 299 | 0.190 / 361 | 0.230 / 268 | 0.166 |
| `rb_expl10` | 0.248 | 499 | 79.03 | 240 | 0.397 | 229 | 0.198 / 343 | 0.187 / 348 | 0.325 |
| `rb_expl20` | 0.147 | 499 | 79.03 | 457 | 0.257 | 421 | 0.114 / 658 | 0.099 / 729 | 0.323 |
| `rb_fd` | 0.115 | 499 | 79.03 | 609 | 0.206 | 616 | 0.224 / 294 | 0.186 / 351 | 0.209 |
| `rb_line_yds` | 0.260 | 499 | 79.03 | 225 | 0.412 | 243 | 0.281 / 218 | 0.257 / 231 | 0.172 |
| `rb_stuff` | 0.152 | 499 | 79.03 | 441 | 0.264 | 599 | 0.232 / 281 | 0.165 / 404 | 0.143 |
| `rb_success` | 0.196 | 499 | 79.03 | 323 | 0.328 | 295 | 0.292 / 206 | 0.261 / 226 | 0.161 |
| `rb_sy_conv` | 0.024 | 47 | 11.07 | 450 | 0.047 | none | 0.034 / 317 | -0.006 / none | 0.150 |
| `rb_td` | 0.112 | 499 | 79.03 | 627 | 0.201 | 728 | 0.211 / 318 | 0.130 / 533 | 0.110 |
| `rb_ypc` | 0.257 | 499 | 79.03 | 229 | 0.409 | 218 | 0.237 / 274 | 0.237 / 257 | 0.343 |

**Running back: line vs back (PFR / NGS)**

| metric | r | units | n / half | k | season reliability | k 2018-24 | r / k weeks 1-9 vs 10-18 | r / k fixed mix | YoY r |
|---|---|---|---|---|---|---|---|---|---|
| `rb_btk` | 0.164 | 499 | 79.06 | 404 | 0.281 | 403 | 0.176 / 397 | 0.233 / 263 | 0.201 |
| `rb_ryoe` | 0.113 | 449 | 69.64 | 544 | 0.204 | 505 | 0.152 / 427 | 0.127 / 478 | 0.221 |
| `rb_yac` | 0.207 | 499 | 79.06 | 303 | 0.343 | 297 | 0.176 / 399 | 0.254 / 235 | 0.277 |
| `rb_ybc` | 0.326 | 499 | 79.06 | 164 | 0.491 | 167 | 0.317 / 183 | 0.293 / 194 | 0.306 |

**Running back: splits (departure from own overall + league offset; shares from the league share)**

| metric | r | units | n / half | k | season reliability | k 2018-24 | r / k weeks 1-9 vs 10-18 | r / k fixed mix | YoY r |
|---|---|---|---|---|---|---|---|---|---|
| `rb_box_epa_dev` | 0.008 | 1211 | 30.88 | none | none | none | -0.035 / none | 0.024 / 1284 | 0.068 |
| `rb_box_expl10_dev` | 0.008 | 1211 | 30.88 | none | none | none | 0.018 / none | 0.077 / 374 | 0.088 |
| `rb_box_expl20_dev` | 0.039 | 1211 | 30.88 | 757 | 0.075 | 802 | 0.074 / 414 | 0.044 / 670 | 0.082 |
| `rb_box_stuff_dev` | 0.090 | 1211 | 30.88 | 314 | 0.164 | 332 | -0.010 / none | 0.041 / 721 | 0.133 |
| `rb_box_success_dev` | -0.000 | 1211 | 30.88 | none | none | none | 0.005 / none | 0.031 / 962 | 0.009 |
| `rb_box_ypc_dev` | 0.061 | 1211 | 30.88 | 475 | 0.115 | 599 | 0.030 / 1076 | 0.023 / 1305 | 0.099 |
| `rb_dir_epa_dev` | 0.008 | 1637 | 17.38 | none | none | none | 0.044 / 396 | 0.016 / none | 0.010 |
| `rb_dir_expl10_dev` | 0.028 | 1637 | 17.38 | 597 | 0.055 | 668 | 0.021 / 856 | 0.012 / none | 0.048 |
| `rb_dir_expl20_dev` | 0.023 | 1637 | 17.38 | 724 | 0.046 | 750 | 0.016 / none | 0.010 / none | 0.039 |
| `rb_dir_share` | 0.425 | 3493 | 79.01 | 107 | 0.596 | 111 | 0.413 / 121 | 0.469 / 90.49 | 0.502 |
| `rb_dir_stuff_dev` | 0.008 | 1637 | 17.38 | none | none | none | 0.003 / none | 0.011 / none | 0.018 |
| `rb_dir_success_dev` | 0.045 | 1637 | 17.38 | 371 | 0.086 | 368 | 0.047 / 365 | 0.009 / none | -0.015 |
| `rb_dir_ypc_dev` | 0.035 | 1637 | 17.38 | 485 | 0.067 | 778 | 0.058 / 292 | 0.024 / 721 | 0.060 |
| `rb_pers_epa_dev` | 0.074 | 929 | 40.44 | 503 | 0.138 | 554 | 0.054 / 764 | 0.090 / 412 | 0.032 |
| `rb_pers_expl10_dev` | -0.016 | 929 | 40.44 | none | none | 1883 | -0.002 / none | 0.023 / 1723 | 0.075 |
| `rb_pers_expl20_dev` | -0.059 | 929 | 40.44 | none | none | none | -0.004 / none | -0.022 / none | -0.031 |
| `rb_pers_stuff_dev` | 0.056 | 929 | 40.44 | 680 | 0.106 | 816 | 0.065 / 617 | -0.034 / none | 0.004 |
| `rb_pers_success_dev` | -0.024 | 929 | 40.44 | none | none | none | 0.013 / none | -0.000 / none | 0.005 |
| `rb_pers_ypc_dev` | 0.043 | 929 | 40.44 | 889 | 0.083 | 749 | 0.019 / none | 0.130 / 271 | 0.098 |
| `rb_sg_epa_dev` | 0.025 | 931 | 41.83 | 1617 | 0.049 | 1142 | 0.114 / 352 | 0.005 / none | -0.052 |
| `rb_sg_expl10_dev` | 0.045 | 931 | 41.83 | 891 | 0.086 | 722 | 0.073 / 576 | 0.089 / 433 | 0.063 |
| `rb_sg_expl20_dev` | -0.035 | 931 | 41.83 | none | none | none | 0.035 / 1263 | 0.058 / 688 | 0.026 |
| `rb_sg_share` | 0.873 | 499 | 79.03 | 11.45 | 0.932 | 10.67 | 0.802 / 20.92 | 0.876 / 11.32 | 0.565 |
| `rb_sg_stuff_dev` | -0.036 | 931 | 41.83 | none | none | none | -0.045 / none | -0.069 / none | 0.034 |
| `rb_sg_success_dev` | 0.063 | 931 | 41.83 | 622 | 0.119 | 555 | 0.129 / 306 | 0.008 / none | -0.016 |
| `rb_sg_ypc_dev` | 0.013 | 931 | 41.83 | none | none | none | 0.063 / 670 | 0.007 / none | -0.022 |
| `rb_side_epa_dev` | 0.024 | 1227 | 30.45 | 1248 | 0.047 | none | 0.057 / 533 | 0.033 / 902 | -0.045 |
| `rb_side_expl10_dev` | 0.099 | 1227 | 30.45 | 278 | 0.180 | 374 | 0.036 / 864 | 0.044 / 661 | 0.025 |
| `rb_side_expl20_dev` | 0.018 | 1227 | 30.45 | none | none | none | 0.013 / none | 0.024 / 1224 | 0.079 |
| `rb_side_share` | 0.522 | 1497 | 79.01 | 72.25 | 0.686 | 77.62 | 0.555 / 68.03 | 0.564 / 61.76 | 0.562 |
| `rb_side_stuff_dev` | 0.048 | 1227 | 30.45 | 602 | 0.092 | 841 | 0.019 / none | 0.084 / 332 | 0.007 |
| `rb_side_success_dev` | 0.015 | 1227 | 30.45 | none | none | none | 0.039 / 784 | -0.002 / none | -0.041 |
| `rb_side_ypc_dev` | 0.073 | 1227 | 30.45 | 389 | 0.135 | 628 | 0.073 / 406 | 0.086 / 322 | 0.051 |

**Running back: usage (team-scoped)**

| metric | r | units | n / half | k | season reliability | k 2018-24 | r / k weeks 1-9 vs 10-18 | r / k fixed mix | YoY r |
|---|---|---|---|---|---|---|---|---|---|
| `rb_carries_pg` | 0.866 | 508 | 7.21 | 1.12 | 0.928 | 1.06 | 0.724 / 2.84 | 0.887 / 0.92 | 0.699 |
| `rb_carry_share` | 0.906 | 528 | 171 | 17.80 | 0.951 | 17.70 | 0.767 / 54.05 | 0.918 / 15.32 | 0.650 |
| `rb_early_touch_share` | 0.888 | 528 | 339 | 42.55 | 0.941 | 42.53 | 0.751 / 117 | 0.895 / 39.77 | 0.687 |
| `rb_gl_share` | 0.717 | 221 | 12.49 | 4.92 | 0.835 | 4.30 | 0.580 / 9.06 | 0.751 / 4.08 | 0.475 |
| `rb_rb_carry_share` | 0.905 | 528 | 155 | 16.35 | 0.950 | 16.37 | 0.770 / 48.30 | 0.918 / 13.87 | 0.651 |
| `rb_rz_share` | 0.738 | 518 | 30.35 | 10.75 | 0.850 | 10.13 | 0.618 / 19.46 | 0.798 / 7.72 | 0.576 |
| `rb_sit_all` | 0.905 | 528 | 439 | 45.79 | 0.950 | 46.15 | 0.731 / 169 | 0.905 / 46.42 | 0.594 |
| `rb_sit_early` | 0.910 | 528 | 340 | 33.43 | 0.953 | 33.67 | 0.745 / 122 | 0.912 / 32.99 | 0.621 |
| `rb_sit_goal_line` | 0.687 | 439 | 18.63 | 8.47 | 0.815 | 8.08 | 0.587 / 13.53 | 0.728 / 6.98 | 0.488 |
| `rb_sit_neutral` | 0.906 | 528 | 243 | 25.11 | 0.951 | 26.60 | 0.728 / 94.53 | 0.911 / 23.73 | 0.615 |
| `rb_sit_red_zone` | 0.787 | 527 | 65.94 | 17.80 | 0.881 | 18.35 | 0.601 / 45.76 | 0.785 / 18.12 | 0.574 |
| `rb_sit_short_yardage` | 0.714 | 515 | 24.08 | 9.63 | 0.833 | 9.75 | 0.591 / 17.23 | 0.736 / 8.70 | 0.468 |
| `rb_sit_third` | 0.925 | 528 | 99.15 | 7.98 | 0.961 | 8.10 | 0.774 / 30.06 | 0.911 / 9.67 | 0.633 |
| `rb_sit_two_minute` | 0.812 | 527 | 50.81 | 11.74 | 0.896 | 11.14 | 0.720 / 20.54 | 0.805 / 12.42 | 0.595 |
| `rb_snap_share` | 0.905 | 522 | 467 | 48.81 | 0.950 | 48.64 | 0.728 / 182 | 0.901 / 51.67 | 0.583 |
| `rb_target_share` | 0.792 | 528 | 228 | 59.93 | 0.884 | 59.75 | 0.759 / 75.78 | 0.791 / 60.81 | 0.671 |
| `rb_targets_pg` | 0.795 | 508 | 7.21 | 1.86 | 0.886 | 1.76 | 0.710 / 3.05 | 0.796 / 1.85 | 0.656 |
| `rb_tgt_per_db` | 0.472 | 528 | 126 | 140 | 0.642 | 135 | 0.557 / 108 | 0.538 / 109 | 0.559 |
| `rb_tgt_per_db_vs_man_dev` | 0.182 | 507 | 43.05 | 194 | 0.308 | 178 | 0.194 / 194 | 0.206 / 168 | 0.265 |
| `rb_tgt_per_db_vs_zone_dev` | 0.154 | 525 | 75.90 | 418 | 0.266 | 394 | 0.206 / 314 | 0.186 / 335 | 0.254 |
| `rb_third_touch_share` | 0.648 | 528 | 98.86 | 53.69 | 0.786 | 53.55 | 0.578 / 75.12 | 0.627 / 58.89 | 0.484 |
| `rb_two_minute_touch_share` | 0.500 | 527 | 50.71 | 50.70 | 0.667 | 52.38 | 0.434 / 68.64 | 0.482 / 54.79 | 0.469 |

**Running back: receiving**

| metric | r | units | n / half | k | season reliability | k 2018-24 | r / k weeks 1-9 vs 10-18 | r / k fixed mix | YoY r |
|---|---|---|---|---|---|---|---|---|---|
| `rb_adot` | 0.538 | 284 | 26.86 | 23.09 | 0.699 | 22.94 | 0.414 / 39.16 | 0.514 / 25.38 | 0.429 |
| `rb_catch` | 0.032 | 284 | 28.60 | 877 | 0.061 | 673 | 0.151 / 165 | 0.052 / 524 | 0.039 |
| `rb_chk_share` | 0.348 | 138 | 27.84 | 52.06 | 0.517 | 61.13 | 0.415 / 39.92 | 0.302 / 64.58 | 0.283 |
| `rb_screen_share` | 0.427 | 138 | 27.84 | 37.35 | 0.599 | 37.91 | 0.403 / 41.98 | 0.394 / 42.82 | 0.293 |
| `rb_yac_rec` | 0.181 | 283 | 22.37 | 101 | 0.306 | 94.25 | 0.157 / 124 | 0.201 / 88.50 | 0.310 |
| `rb_ypt` | 0.143 | 284 | 28.60 | 172 | 0.250 | 153 | 0.131 / 195 | 0.168 / 142 | 0.101 |

**Quarterback rushing**

| metric | r | units | n / half | k | season reliability | k 2018-24 | r / k weeks 1-9 vs 10-18 | r / k fixed mix | YoY r |
|---|---|---|---|---|---|---|---|---|---|
| `qb_dsg_expl10` | -0.079 | 44 | 23.45 | none | none | 143 | -0.345 / none | -0.236 / none | 0.137 |
| `qb_dsg_rate` | 0.917 | 282 | 403 | 36.34 | 0.957 | 32.40 | 0.911 / 43.66 | 0.919 / 35.83 | 0.934 |
| `qb_dsg_success` | 0.140 | 44 | 23.45 | 144 | 0.245 | 76.08 | -0.310 / none | -0.210 / none | -0.015 |
| `qb_dsg_yds_rate` | 0.865 | 282 | 403 | 63.08 | 0.927 | 54.01 | 0.820 / 98.12 | 0.815 / 92.56 | 0.922 |
| `qb_dsg_ypc` | 0.221 | 44 | 23.45 | 82.60 | 0.362 | 50.48 | -0.166 / none | -0.164 / none | -0.143 |
| `qb_gl_rate` | 0.356 | 282 | 403 | 729 | 0.525 | 654 | 0.576 / 329 | 0.364 / 712 | 0.557 |
| `qb_hit_sack_rate` | 0.609 | 282 | 247 | 159 | 0.757 | 163 | 0.618 / 168 | 0.654 / 132 | 0.616 |
| `qb_kneel_pg` | 0.182 | 272 | 6.97 | 31.43 | 0.307 | 30.28 | 0.103 / 65.32 | 0.136 / 44.53 | -0.042 |
| `qb_rpo_rate` | 0.632 | 282 | 403 | 235 | 0.774 | 215 | 0.502 / 442 | 0.677 / 195 | 0.328 |
| `qb_rz_rate` | 0.638 | 282 | 403 | 229 | 0.779 | 215 | 0.743 / 154 | 0.664 / 206 | 0.798 |
| `qb_scr_blitz_dev` | -0.034 | 145 | 58.80 | none | none | none | -0.055 / none | 0.028 / 2065 | 0.181 |
| `qb_scr_expl10` | 0.057 | 144 | 16.65 | 275 | 0.108 | 140 | 0.057 / 286 | -0.139 / none | 0.187 |
| `qb_scr_man_dev` | 0.160 | 112 | 94.01 | 494 | 0.276 | 259 | 0.210 / 403 | 0.035 / 2698 | -0.244 |
| `qb_scr_noblitz_dev` | -0.004 | 145 | 178 | none | none | 3210 | -0.005 / none | 0.023 / 7827 | 0.200 |
| `qb_scr_nopress_dev` | 0.141 | 112 | 164 | 997 | 0.248 | 2053 | 0.265 / 520 | 0.032 / 5129 | 0.164 |
| `qb_scr_press_dev` | 0.226 | 112 | 70.01 | 240 | 0.369 | 334 | 0.339 / 155 | 0.104 / 619 | 0.275 |
| `qb_scr_rate` | 0.755 | 282 | 247 | 79.94 | 0.861 | 81.16 | 0.771 / 80.96 | 0.767 / 75.64 | 0.803 |
| `qb_scr_single_dev` | 0.152 | 112 | 120 | 671 | 0.264 | 1467 | -0.078 / none | -0.026 / none | -0.005 |
| `qb_scr_two_dev` | 0.135 | 112 | 93.35 | 599 | 0.238 | none | 0.184 / 466 | 0.195 / 397 | 0.053 |
| `qb_scr_yds_db` | 0.691 | 282 | 247 | 111 | 0.817 | 112 | 0.705 / 114 | 0.738 / 88.54 | 0.764 |
| `qb_scr_yds_press` | 0.415 | 112 | 70.01 | 98.80 | 0.586 | 107 | 0.471 / 88.89 | 0.375 / 119 | 0.749 |
| `qb_scr_ypa` | 0.181 | 144 | 16.65 | 75.26 | 0.307 | 55.68 | 0.017 / none | -0.020 / none | 0.263 |
| `qb_scr_zone_dev` | 0.093 | 112 | 140 | 1363 | 0.170 | 1211 | 0.212 / 588 | 0.008 / none | -0.112 |
| `qb_sneak_conv` | none | 5 | none | none | none | none | none / none | none / none | none |
| `qb_sneak_rate` | 0.551 | 282 | 403 | 328 | 0.711 | 386 | 0.623 / 269 | 0.563 / 317 | 0.636 |
| `qb_td_rate` | 0.454 | 282 | 403 | 485 | 0.625 | 461 | 0.506 / 436 | 0.429 / 543 | 0.671 |
| `qb_x10_rate` | 0.686 | 282 | 403 | 185 | 0.814 | 161 | 0.691 / 199 | 0.655 / 215 | 0.830 |

**Defense vs RB runs and what it lines up in**

| metric | r | units | n / half | k | season reliability | k 2018-24 | r / k weeks 1-9 vs 10-18 | r / k fixed mix | YoY r |
|---|---|---|---|---|---|---|---|---|---|
| `def_base_share` | 0.819 | 256 | 515 | 114 | 0.901 | 134 | 0.692 / 230 | 0.763 / 160 | 0.518 |
| `def_box_epa_dev` | -0.048 | 741 | 62.00 | none | none | none | -0.017 / none | -0.017 / none | 0.034 |
| `def_box_expl10_dev` | 0.045 | 741 | 62.00 | 1331 | 0.085 | 2737 | -0.062 / none | -0.039 / none | 0.003 |
| `def_box_expl20_dev` | -0.027 | 741 | 62.00 | none | none | none | -0.057 / none | -0.067 / none | 0.054 |
| `def_box_mean` | 0.707 | 256 | 515 | 213 | 0.829 | 200 | 0.566 / 395 | 0.699 / 221 | 0.444 |
| `def_box_op_heavy_dev` | 0.231 | 1092 | 115 | 383 | 0.376 | 366 | 0.175 / 544 | 0.222 / 404 | 0.136 |
| `def_box_op_light_dev` | 0.076 | 1092 | 115 | 1405 | 0.141 | 1327 | 0.102 / 1013 | 0.106 / 968 | 0.100 |
| `def_box_sg_dev` | 0.559 | 512 | 257 | 203 | 0.717 | 195 | 0.419 / 357 | 0.485 / 273 | 0.444 |
| `def_box_stuff_dev` | -0.021 | 741 | 62.00 | none | none | none | -0.042 / none | 0.018 / none | 0.089 |
| `def_box_success_dev` | 0.033 | 741 | 62.00 | 1838 | 0.063 | 1310 | -0.011 / none | 0.038 / 1548 | 0.026 |
| `def_box_ypc_dev` | 0.013 | 741 | 62.00 | none | none | none | -0.040 / none | -0.018 / none | -0.035 |
| `def_dime_share` | 0.946 | 256 | 515 | 29.15 | 0.972 | 29.10 | 0.841 / 97.19 | 0.937 / 34.65 | 0.551 |
| `def_dir_epa_dev` | -0.045 | 1717 | 26.44 | none | none | none | -0.006 / none | -0.010 / none | 0.012 |
| `def_dir_expl10_dev` | -0.010 | 1717 | 26.44 | none | none | none | 0.004 / none | 0.018 / none | -0.002 |
| `def_dir_expl20_dev` | -0.043 | 1717 | 26.44 | none | none | none | -0.014 / none | -0.018 / none | 0.001 |
| `def_dir_stuff_dev` | 0.012 | 1717 | 26.44 | none | none | 1024 | 0.005 / none | 0.028 / 908 | 0.027 |
| `def_dir_success_dev` | 0.013 | 1717 | 26.44 | none | none | none | 0.045 / 561 | 0.046 / 550 | -0.006 |
| `def_dir_ypc_dev` | -0.009 | 1717 | 26.44 | none | none | none | -0.027 / none | 0.019 / none | -0.019 |
| `def_dl3_share` | 0.993 | 256 | 514 | 3.72 | 0.996 | 3.82 | 0.951 / 26.63 | 0.991 / 4.74 | 0.743 |
| `def_dl4_share` | 0.992 | 256 | 514 | 4.39 | 0.996 | 4.45 | 0.948 / 27.95 | 0.990 / 4.94 | 0.736 |
| `def_heavy_share` | 0.755 | 256 | 515 | 167 | 0.860 | 179 | 0.582 / 370 | 0.721 / 199 | 0.508 |
| `def_light_share` | 0.759 | 256 | 515 | 163 | 0.863 | 159 | 0.601 / 341 | 0.762 / 161 | 0.446 |
| `def_mtkl_pg` | 0.400 | 254 | 8.29 | 12.46 | 0.571 | 11.34 | 0.334 / 16.51 | 0.364 / 14.48 | 0.200 |
| `def_mtkl_rate` | 0.434 | 254 | 504 | 657 | 0.606 | 599 | 0.368 / 866 | 0.386 / 803 | 0.250 |
| `def_nickel_share` | 0.885 | 256 | 515 | 66.88 | 0.939 | 67.81 | 0.739 / 182 | 0.856 / 86.57 | 0.496 |
| `def_rb_catch` | 0.074 | 256 | 51.93 | 650 | 0.138 | 904 | 0.135 / 334 | 0.074 / 653 | -0.004 |
| `def_rb_epa` | 0.262 | 256 | 181 | 508 | 0.416 | 477 | 0.250 / 543 | 0.294 / 433 | 0.252 |
| `def_rb_expl10` | 0.372 | 256 | 181 | 305 | 0.542 | 298 | 0.251 / 539 | 0.271 / 485 | 0.360 |
| `def_rb_expl20` | 0.226 | 256 | 181 | 619 | 0.368 | 590 | 0.241 / 570 | 0.136 / 1147 | 0.145 |
| `def_rb_fd` | 0.282 | 256 | 181 | 459 | 0.441 | 426 | 0.237 / 581 | 0.249 / 544 | 0.277 |
| `def_rb_line_yds` | 0.449 | 256 | 181 | 222 | 0.620 | 197 | 0.445 / 225 | 0.520 / 167 | 0.352 |
| `def_rb_stuff` | 0.440 | 256 | 181 | 230 | 0.611 | 221 | 0.501 / 180 | 0.528 / 162 | 0.382 |
| `def_rb_success` | 0.324 | 256 | 181 | 377 | 0.489 | 363 | 0.284 / 456 | 0.341 / 350 | 0.229 |
| `def_rb_sy_conv` | -0.089 | 188 | 12.50 | none | none | none | 0.028 / 427 | -0.048 / none | 0.015 |
| `def_rb_tgt_db` | 0.346 | 256 | 314 | 595 | 0.514 | 619 | 0.324 / 655 | 0.255 / 918 | 0.219 |
| `def_rb_yac_rec` | 0.053 | 256 | 40.10 | 712 | 0.101 | 695 | 0.111 / 323 | 0.112 / 318 | -0.002 |
| `def_rb_ypc` | 0.351 | 256 | 181 | 333 | 0.520 | 306 | 0.307 / 408 | 0.305 / 412 | 0.253 |
| `def_rb_ypt` | 0.063 | 256 | 51.93 | 777 | 0.118 | 508 | 0.156 / 281 | 0.143 / 310 | 0.176 |
| `def_side_epa_dev` | -0.016 | 766 | 60.30 | none | none | none | 0.090 / 610 | 0.046 / 1245 | 0.032 |
| `def_side_expl10_dev` | 0.040 | 766 | 60.30 | 1450 | 0.077 | 1712 | 0.050 / 1146 | 0.056 / 1016 | 0.109 |
| `def_side_expl20_dev` | -0.035 | 766 | 60.30 | none | none | none | 0.043 / 1352 | 0.011 / none | 0.065 |
| `def_side_stuff_dev` | 0.018 | 766 | 60.30 | none | none | 1143 | -0.045 / none | -0.003 / none | 0.097 |
| `def_side_success_dev` | 0.024 | 766 | 60.30 | 2445 | 0.047 | 1927 | 0.093 / 585 | 0.044 / 1317 | 0.052 |
| `def_side_ypc_dev` | 0.010 | 766 | 60.30 | none | none | none | 0.060 / 944 | 0.047 / 1223 | 0.051 |
| `def_yac` | 0.260 | 256 | 181 | 513 | 0.413 | 467 | 0.200 / 721 | 0.246 / 553 | 0.229 |
| `def_ybc` | 0.304 | 256 | 181 | 414 | 0.466 | 365 | 0.344 / 344 | 0.300 / 421 | 0.258 |

**Defense vs QB rushing (observed / expected) and pressure**

| metric | r | units | n / half | k | season reliability | k 2018-24 | r / k weeks 1-9 vs 10-18 | r / k fixed mix | YoY r |
|---|---|---|---|---|---|---|---|---|---|
| `def_blitz` | 0.772 | 128 | 313 | 92.20 | 0.872 | 87.46 | 0.675 / 151 | 0.727 / 118 | 0.552 |
| `def_containment_proxy_oe` | 0.139 | 96 | 53.88 | 333 | 0.244 | 343 | 0.318 / 116 | 0.146 / 315 | 0.033 |
| `def_designed_runs_oe` | 0.155 | 256 | 7.39 | 40.26 | 0.269 | 55.76 | 0.101 / 66.10 | 0.150 / 42.02 | 0.166 |
| `def_designed_yds_oe` | 0.067 | 256 | 38.16 | 528 | 0.126 | 838 | 0.067 / 529 | 0.200 / 153 | 0.144 |
| `def_explosive_runs_oe` | 0.090 | 256 | 5.06 | 51.24 | 0.165 | 51.50 | 0.070 / 67.66 | 0.033 / 149 | 0.115 |
| `def_hit_sack_rate_oe` | 0.403 | 256 | 45.18 | 66.97 | 0.574 | 72.69 | 0.345 / 85.84 | 0.328 / 92.50 | 0.296 |
| `def_pressure` | 0.464 | 96 | 315 | 364 | 0.634 | 357 | 0.366 / 544 | 0.344 / 600 | 0.489 |
| `def_pressure_pfr` | 0.316 | 160 | 316 | 685 | 0.480 | 832 | 0.207 / 1209 | 0.322 / 665 | 0.273 |
| `def_red_zone_rushes_oe` | 0.101 | 256 | 5.33 | 47.54 | 0.183 | 52.92 | 0.104 / 46.15 | 0.110 / 42.96 | 0.089 |
| `def_rush_tds_oe` | 0.018 | 256 | 1.54 | none | none | 63.60 | 0.067 / 21.54 | 0.027 / 54.68 | 0.057 |
| `def_scramble_yds_oe` | 0.072 | 256 | 107 | 1381 | 0.134 | 972 | 0.083 / 1179 | 0.050 / 2042 | 0.180 |
| `def_scrambles_oe` | 0.038 | 256 | 14.36 | 359 | 0.074 | 135 | 0.090 / 145 | 0.067 / 200 | 0.200 |

**Offensive line**

| metric | r | units | n / half | k | season reliability | k 2018-24 | r / k weeks 1-9 vs 10-18 | r / k fixed mix | YoY r |
|---|---|---|---|---|---|---|---|---|---|
| `ol_gap_success_dev` | 0.069 | 767 | 60.23 | 816 | 0.129 | 1043 | 0.022 / 2636 | 0.038 / 1513 | 0.057 |
| `ol_line_yds` | 0.344 | 256 | 181 | 344 | 0.512 | 354 | 0.313 / 396 | 0.311 / 400 | 0.320 |
| `ol_stuff` | 0.287 | 256 | 181 | 448 | 0.446 | 572 | 0.256 / 525 | 0.279 / 466 | 0.248 |
| `ol_success` | 0.349 | 256 | 181 | 338 | 0.517 | 347 | 0.383 / 291 | 0.305 / 411 | 0.338 |
| `ol_ybc` | 0.368 | 256 | 181 | 311 | 0.538 | 296 | 0.340 / 350 | 0.289 / 444 | 0.332 |

**Team context**

| metric | r | units | n / half | k | season reliability | k 2018-24 | r / k weeks 1-9 vs 10-18 | r / k fixed mix | YoY r |
|---|---|---|---|---|---|---|---|---|---|
| `tm_early_run` | 0.555 | 256 | 398 | 319 | 0.714 | 279 | 0.351 / 734 | 0.513 / 377 | 0.390 |
| `tm_lead_run` | 0.334 | 218 | 72.59 | 144 | 0.501 | 153 | 0.235 / 236 | 0.220 / 256 | 0.208 |
| `tm_neutral_run` | 0.574 | 256 | 284 | 211 | 0.729 | 192 | 0.477 / 311 | 0.663 / 145 | 0.467 |
| `tm_plays_pg` | 0.398 | 256 | 8.31 | 12.55 | 0.570 | 13.00 | 0.290 / 20.33 | 0.402 / 12.36 | 0.176 |
| `tm_qb_keep` | 0.891 | 256 | 199 | 24.28 | 0.943 | 23.25 | 0.808 / 47.30 | 0.883 / 26.49 | 0.699 |
| `tm_rpo` | 0.761 | 128 | 522 | 164 | 0.864 | 200 | 0.725 / 198 | 0.807 / 125 | 0.377 |
| `tm_rz_run` | 0.454 | 256 | 77.05 | 92.79 | 0.624 | 82.71 | 0.295 / 184 | 0.460 / 90.47 | 0.379 |
| `tm_trail_run` | 0.149 | 245 | 101 | 575 | 0.260 | 436 | 0.200 / 406 | 0.177 / 473 | 0.282 |

### 5.2 Reading
- **What a defense allows by run DIRECTION, side or box is not measurable at
  this sample.** Every `def_dir_*`, most `def_side_*` and `def_box_*`
  departures have r between -0.05 and +0.04 (1,717 / 766 / 741 team-cells).
  With ~26-60 carries a cell a half, a true spread below about 0.3 yards a
  carry cannot be told from zero (the reviews' m12): the data bounds the
  effect, it does not show there is none. Those fields read the defense's
  overall figure moved by the league's offset. The defense's OVERALL run
  defense is real but modest (YPC r 0.35, k 333 carries; stuff 0.44; line
  yards 0.45).
- **A back's efficiency splits are not measurable either** (direction / side /
  box / personnel / formation departures r -0.06 to 0.10), but **where he runs
  is a trait**: direction shares r 0.43 (k 107), side shares 0.52, shotgun
  share 0.87 (k 11).
- **Usage is known within a few games** (backs only, their pbp / participation
  games): carry share r 0.91 (k 18; 0.77 / k 54 first-half vs second-half),
  RB-carry share 0.91, carries per game 0.87, snap share 0.91 (k 49, after the
  B2 snap fix), on-field share 0.91, target share 0.79, goal-line share 0.72,
  third-down touch share 0.65, two-minute 0.50.
- **A back's receiving EFFICIENCY is mostly noise**: catch rate r 0.03 (k 877
  targets), YPT 0.14, YAC per reception 0.18 -- the first run's 0.44-0.92 were
  WR and TE roles. What he is used for is real: aDOT 0.54, screen share 0.43,
  CHK share 0.35.
- **The line vs the back**: YBC per carry r 0.33, YAC 0.21, broken tackles
  0.16, NGS RYOE only 0.11. Line yards 0.26 on 499 backs (the first run's
  "1,004 units" counted one field twice: N4).
- **QB legs are traits**: designed-run rate r 0.92 (k 36 plays), designed
  yards rate 0.87, scramble rate 0.76 (k 80 dropbacks), scramble yards per
  dropback 0.69, 10+ runs 0.69, red-zone rushes 0.64, sneak rate 0.55;
  per-carry efficiency much less (designed YPC 0.22, scramble YPA 0.18). Of the
  scramble conditions only pressure registers (departure r 0.23); blitz is
  noise, man / zone / shells weak (0.09-0.16).
- **"Allowed few QB yards" is mostly the QBs faced**: opponent-adjusted
  scrambles allowed r 0.04 -- its k is NOT pinned by the data: 359 expected
  scrambles odd-even, 145 first-half vs second-half, 200 on the fixed mix
  (Sonnet's splits read 117-610); quote it as a range -- scramble yards 0.07
  (k 1,178-2,042), designed runs 0.16 (k 40-66), designed yards 0.07, 10+ runs
  0.09, rushing TDs 0.02 (none odd-even; 22-55 elsewhere), containment proxy
  0.14 (k 116-333).
- **The pbp hit + sack proxy (F14) is a real trait on both sides**: a QB's
  sacks + hits per dropback r 0.61 (k 159 dropbacks, YoY 0.62); a defense's,
  opponent-adjusted, r 0.40 (k 67 expected events; 86 / 93 on the other splits;
  YoY 0.30) -- steadier than PFR's per-game pressure (r 0.32, k 685) and close
  to participation pressure (r 0.46, k 364, which 2026 lacks).
- **Team context**: QB-keep share 0.89, RPO rate 0.76, neutral run rate 0.57,
  plays per game 0.40, trailing run rate 0.15.

### 5.3 Decay (`persistence.json`)
The agreed rule, written before the run, on ten rushing tendencies. **Before
B1: pooled r 0.602 -> DECAY 0.75. After B1 (RB/FB units): pooled r 0.602 ->
DECAY 0.75**, unchanged to the third decimal: persistence admits a back only
with 100+ designed runs in both seasons, which no WR or TE reached, so the two
RB tendencies (direction share 0.50, shotgun share 0.57) read the same units
as before; B1 moved the split-half figures, not the persistence pool. It still
sits on the boundary (0.600 without the 2024->2025 pair): the team
tendencies alone read 0.535 (light box 0.45, heavy 0.51, nickel 0.50, dime
0.55, DL<=3 0.74, neutral run rate 0.47) and the player traits 0.70 (QB
designed-run rate 0.93, scramble rate 0.80). Applied as written; the agreed
passing profiles keep 0.5. A per-group decay is a change to validate (not to
set by hand); a guard holds the shipped DECAY to the rule applied to the
shipped r.

## 6. The dataset

`rb_games_2023_2026.csv.gz`: 6,041 rows x 905 columns (2023 1,832; 2024
1,790; 2025 1,801; 2026 618, weeks 1-6). `qb_games_2023_2026.csv.gz`: 2,201
rows x 392 columns (662 / 664 / 664 / 211). Built in 73 s; gzip written with
mtime 0, so a rebuild of the same stores hashes the same.
- **Universe** (RB): every RB/FB with a designed run, target, aborted snap or
  snap (NGS on-field or PFR), plus Sleeper-projected backs. QB: every QB with a
  dropback or rush, plus Sleeper's QB1. **The rows of players who did not play
  come from Sleeper's POST-WEEK snapshot** (`proj > 0`, the QB1 role), so
  `status` never reads doubtful or out (RB rows: 5,786 active, 247 questionable, 8 unknown; QB 2,162 / 37 / 2;
  the 10 unknowns are exactly the two playing team-games whose week's report
  lists nobody from the team -- 2023 week 5 ATL, 2024 week 1 DEN -- read as a
  gap since G4) and `sl_qb1 == 1` implies `act_played == 1` on
  1,817 of 1,817 QB rows: any "will he play" learning from these rows is
  post-hoc (the reviews' m4 / m6).
- **`act_played` vs `act_any_snap`** (m2): `act_played` counts offensive
  participation only. 523 RB rows read `act_played` 0; 453 of them have a
  Sleeper box (`gp` 1) and 450 of those took a special-teams snap
  (`act_any_snap` 1). Kalshi settles on one snap: `act_any_snap` is the one to
  join.
- **As of the game**: every week from a snapshot frozen before its games (the
  builder raises if the snapshot holds the week, at the start and the end of
  each week). `p_games_cur` counts his earlier games for the team that pbp or
  participation shows him in (round 4, §0.3). In round 3 it also counted
  special-teams-only and PFR-only games (offense + special-teams-only on 6,034
  of 6,041 rows, offense-only on 4,908, by the Opus re-check); round 4 moved `p_games_cur` on 1,286 RB rows and `p_games_w` on 1,974, and only the 65 `p_usage_*` / `p_recent_*` / `p_games_*` columns moved, on 1,974 of 6,041 RB rows (2023 556, 2024 651, 2025 556, 2026 211); no QB row moved (the QB file is byte-identical to round 3's).
  The PFR-inclusive count is `p_any_snap_games` (restricted). `q_games_cur`
  (dropback or QB rush games) and `d_games_cur` are unchanged. Every row's
  `aux_status` reads aligned for each season read (0 incomplete, stale or
  missing).
- **Kinds and sources** (`schema.json`): kind is read from the field
  definition across every row (not_available > proxy > modelled > observed;
  the first build took the first row's, a week-1 row with no prior, and
  labelled the eight shrunk QB scramble-split columns and `dq_pressure`
  "observed" -- M4 / m5): 787 observed, 337 modelled, 37 proxy, 1
  not_available, 84 outcomes, 21 Sleeper (labelled "post-week snapshot; not
  provably pre-kickoff"), 6 pre-kickoff injury report, 24 keys. Every column
  has a unit. `source_tag` per column: 76 of 1,297 columns are restricted
  (pfr 50, pfr_snaps 11, ngs 7, mixes 8 -- `p_any_snap_games` among them -- including the two outcome columns
  `act_played` / `act_any_snap`, which read PFR snaps).
- **The F14 columns**: `q_hit_sack_rate` (mean raw 0.149 a dropback over 2,146
  QB rows), `dq_hit_sack_rate_*` on RB and QB rows, `ol_ol_listed_out` (0 on
  4,056 RB rows, 1 on 1,669, 2+ on 316).
- **k frames** (F7, m8): 2023-2025 rows are shrunk with `k_fit_only` (pooled
  2018-2024, so a 2025 choose-on-validation reads no 2025 reliability), 2026
  rows with the full table; `_raw` and `_n` sit beside every value for
  re-shrinking. Stated in `meta.json` (`k_frames`, `k_note`) and `schema.json`
  (`k_note`).
- Actuals: `act_*` (official rushes / yards and the five classes, RPO keeps,
  red-zone and goal-line designed runs, longest run, targets, receptions,
  receiving yards and TDs, fumbles, PFR snaps / YBC / YAC / broken tackles;
  QB rows add dropbacks, designed non-sneak runs, exposure, pressured
  dropbacks and scrambles under pressure). The 2026 prospective test's
  baseline is the frozen pre-kickoff Sleeper capture, which this dataset does
  not hold.
- Blank means no denominator. On 2026 rows the participation-based columns
  read 2024-25 only (§2.4).

## 7. Guards (`tests/guards_nfl_rush_data.py`, 198 checks, ~1 s)

A synthetic six-week season written as every file the code reads (pbp with a
sack carrying a rusher id, a kneel, an RPO keep, an RPO pass, a sneak, an
aborted snap, a deleted play, a two-point try, a scramble TD, a lateral, a
backup QB's dropback, 3rd & 2 vs 3rd & 3, a lead and a trail by 10, a QB hit,
a WR jet sweep and a TE carry with 15+ WR targets in each half; participation;
FTN; PFR rush / def / pass with a postseason and an unjoinable row; NGS with a
season row, a postseason row and a wrong-team row; injuries with weeks 1 and 6
missing and a Doubtful / Questionable / non-starter week; snap counts with a
1-snap 1% back, a special-teams-only back, a team-game with nobody at 99.5%
and a lineman who leaves for injured reserve), built through the agreed
`build_season` and this layer's `build_aux`, read through the real profiles,
the real dataset builder and the real measurement functions. 25 sections, each
isolated: one that raises reports "<section>: runs to the end" and the next
still runs. Everything the first version pinned (§7 of the first report) is
kept; added:

| fix | guards |
|---|---|
| B1 RB/FB units | a WR with carries and 15+ targets in each half is never an RB unit (and the position filter holds even if one lands in `T.rb`); a WR / TE carry counts for the team (3 designed runs) and in the defense's tally keyed WR, never in `T.rb` |
| B2 snaps | KC's total is 60 with a 1-snap 1% back (the old code: 100); the 99.5% fallback (30 / 0.6 = 50) and its count; snap share 45 / 60 and team-scoped 50 / 90 |
| M1 no report | week 6: status "unknown" for every team, OL out count / listed-out / RB room None, `RUSH-noreport` once; a week with a report but no listing reads active / 0 |
| M3 aux | aligned / stale (ledgered `RUSH-aux`) / missing stamps on every profile; `coverage` and a truncated PFR file listed in `missing`; `refresh` over a cut gzip in 2025 and a good 2026 (2025's error in the summary, `RUSH-build`, 2026 built) |
| M4 / m5 schema | `q_scramble_vs_pressure` modelled though its week-1 rows have no prior; containment leaves proxy; read option not_available; `act_played` offensive / `act_any_snap` outcomes; Sleeper columns "post-week"; the OL out count names the injury report; every column has a unit; the licence and k-frame notes |
| m1 clamp | a share prior of 1.24 / -0.048 clamps to 1 / 0 (value and base too); a ratio never clamps |
| m2 snaps | the special-teams-only back kept (0, 0.0, 5), his row `act_played` 0 / `act_any_snap` 1 |
| m3 k frames | a fit-only season's rows read `k_fit_only`; the loaded table is restored |
| F14 licence | every leaf of every profile carries a valid tag equal to `SOURCE_OF`; 28 pinned tags (every PFR / NGS field); no field whose basis reads PFR or NGS data escapes `restricted`; `schema.json` tags; the hit + sack proxy (2 of 3 dropbacks; BUF allowed 2 of 4, proxy, metric `def_hit_sack_rate_oe`); `ol_listed_out` (2 where the starters' count is 1; None without a report; 0 with a report and no lineman) |
| the reviews' mutation gaps | the recent block, snap share and QB team filter scoped; the builder's `rb(pid, team)` (1 / 3, not 4 / 9), `d_` the opponent's (BUF 5 RB carries), `tc_` the team's (DEN 2.0); `RP.build` never takes the cutoff season as a prior; the split-prior offset sign; the multiplicative scramble-split prior from the SHRUNK rate (base from the raw); the two-season leave-one-out at each game's own weight and with the QB metric's own k; the reliability stamp 5 / 55; the FULL path -> metric map of all six profiles (243 compressed paths, embedded); the league RB prior counted straight off the plays (55 / 15); personnel 4 / 5 / 6+ DBs, box edges, the neutral band's edges, lead / trail, short yardage (sit and RV), status codes; scramble yards and TDs, two-QB exposure (7.5 / 2.5), the lateral (18 official vs 22 designed); the measurement's split (odd / even disjoint; the two disclosure splits), unit gate, k formula (numpy; reported as skipped where numpy is absent, like the agreed model guards), the DECAY rule's thresholds and the shipped DECAY == the rule applied to the shipped r |

**Against the pre-fix code** (the three modules and `reliability.json` as they
stood before this round, the new guard file run on them, plus a variant that
drops the `k_frames` argument so the builder section can run): 110 pass, 32
fail outright, and the variant adds 4 builder / schema failures before it stops at the missing `act_played` column. Failing on the
old code: every B2 / m2 snap check (8), the rb / qb / dq / ol metric maps
(the duplicate `rushing.line.line_yds`; the new fields), the containment
leaves, the QB / defense section (no `scramble_ypa_allowed`), the three M1
checks, the B1 `T.rb` check and units section, the measurement section (no
`half_tallies`), the three aux stamps, both coverage checks, the m1 clamp, the
three F14 sections, the builder (no `k_frames`; in the variant: `act_any_snap`,
`aux_status`, the modelled and proxy schema kinds), and the refresh section
(the cut file stopped the whole refresh). The guards written for the reviews'
MUTATION gaps pass on the old code by design -- it was right there; each is
shown failing on its mutant instead.

**Mutation runs on the fixed tree** (each reviewer's own mutant list,
copied and run unchanged except three strings the fixes moved, marked
"adapted"; a fresh copy of the tree, `python3 -B`, `__pycache__` cleared before
every mutant):

| set | mutants | caught | missed | not applicable | control |
|---|---|---|---|---|---|
| Opus (`mutate_opus.py`) | 35 | 31 | 2, both equivalent | 3 | green |
| Sonnet (`e_mutate.py`) | 45 | 45 | 0 | 0 | green |
| this layer's (`mutate.py`) | 41 | 40 | 1, equivalent | 0 | green |

- Opus RPO-2 ("an RPO keep credited on a sneak too") is equivalent: the line
  sits in the `else` branch of `if sneak`, so `sneak` is always False there.
- Opus STAT-2 ("the injury report keeps postseason rows") is equivalent on
  every file nflverse publishes: postseason rows carry weeks 18-21 (2018-20) or
  19-22 (2021-25), always past the last regular-season week, so no REG
  snapshot can read one (measured on injuries_2018 ... _2026).
- Opus SACK-3 and SCR-3 wrote `TL["rush_yds"]` / `TL["qb_scr"]`, dead fields
  now removed (N7); STAT-3 IS the M1 fix, now the code.
- This layer's survivor removes `week_rows`' start-of-week check: the
  end-of-week check fires on the same condition.
- Adapted strings: CUT-2 / U3 mutate `_report`'s one week read (`self.week +
  1` / `- 1`); D1 makes `read_snaps` keep the FEWEST snaps; M4 makes the second
  half the same weeks as the first (`half_tallies`).
- Re-run on the round-3 tree: the same tallies (Opus 31 / 2 equivalent / 3
  not applicable; Sonnet 45 of 45; this layer's 40 of 41 -- its "refresh never
  skips" mutant re-pointed at the new skip condition, which also needs a
  complete aux), every control green.

**Round 3 (the re-checks' G1-G5 and FIX-m1), 11 more checks:**

| fix | guards | on the round-2 code |
|---|---|---|
| G1 incomplete aux | a failed PFR download saves an aux with `complete` False, `absent` ["pfr_rush"], `RUSH-incomplete`; its profiles stamp "incomplete" (RUSH-aux) and still read its plays; the retry after the file arrives is not skipped and builds it complete; a failed NGS download makes a season incomplete too; a season with no pbp file saves nothing and reports the error; `expected_sources` by season and `aux_complete` | all six FAIL |
| G3 pins | `K_FRAMES` == {2023-2025 fit_only, 2026 full}; the hit + sack OE's expected uses each facing QB's leave-one-out rate shrunk by `qb_hit_sack_rate`'s own k (0.475 at k 10, 0.333 at k 2, by hand) | pass (the code was right); each fails on its mutant |
| G4 team absent | a team the week's report lists nobody from reads "unknown" (ledgered once), its OL listed-out None | both FAIL |
| G5 source text | every column's human-readable source names each part of its `source_tag` | FAIL |

On the round-2 code (`nfl_rush_data.py` 4122f839, `nfl_rush_profiles.py`
6da5fa7d, `research/nfl_rush_dataset.py` 01180bfe, recovered from the
round-2 mutation tree): 183 pass, 9 fail -- the five G1 refresh checks plus
the section stopping at `expected_sources` (the sixth), both G4 checks and the
G5 check. The two G3 pins and the m1 value check pass there by design (the
round-2 code was right on them); each fails on its mutant instead. The round-3 mutants
(`<scratch>/rush_fix/mutants/mut_r3.py`, the same runner): the Opus
re-check's two survivors (FIX-F7 `K_FRAMES[2025] = "full"`, FIX-F14b the hit +
sack OE without its QB k) are caught, and so are `aux_complete` always True,
an aux saved without its pbp, an incomplete aux skipped as done, an
incomplete aux stamped aligned, a failed NGS download not counted absent,
a team missing from the report read active, a source text written without
its tag, and the Opus re-check's near-equivalent FIX-m1 (share VALUES
unclamped while priors and bases stay clamped -- it bites only where a raw
exceeds 1, `ryoe_carry_coverage` on 40 rows; a new check shrinks a raw of 5
and requires the value at 1) -- 10 of 10, the control green.

**Round 4 (the rule review's M8), 4 more checks:** a special-teams-only PFR snap line planted for 100006 in G6 (where pbp and participation never show him) moves NO field outside the pfr / pfr_snaps tags across seven profiles, nor the games counts; it does move the restricted `any_snap` block (games 5 -> 6, carry share down); a back whom PFR alone lists (100009's one offensive snap at 1%, 100010's special teams) has no appearance, one with a play does; the dataset's games columns are tagged `derived:participation+pbp` and the PFR-inclusive count `p_any_snap_games` `derived:pbp+pfr_snaps`. On the round-3 code (247cc625 / 1bddcab7 / 28bdea90): 191 pass, 4 fail -- the invariance check (usage.carries_pg 1.8 -> 1.5 and the rest moved with the PFR line), the section stopping at the missing `any_snap` block (the appearance check after it cannot run there: no `app` flag), the schema check, and the rb metric map (the new paths). Round-4 mutants (`mutants/mut_r4.py`): the usage game set back to every PFR-listed game, an appearance that counts PFR snaps, the `any_snap` block tagged pbp, the recent window over PFR-listed games, the games columns tagged pbp -- 5 of 5 caught, the control green.

**Round 5 (the round-4 confirmation's shared gap, Opus O4f / Sonnet R8), 2
more checks.** The M8 fixture's planted game G6 now carries 16
participation-charted KC plays, with back 100011 on the field for all of them
and no touch, and 100006 (the planted special-teams-only PFR line) on none.
The checks pin: the planted game's 16 charted plays with 100006 off the field
and no appearance; no unrestricted field moving, `onfield_plays_current` and
the on-field shares included; and 100011 as an appearance (1 game, on-field
share 1.0, `onfield_plays_current` 16, no carries). The usage and recent tags
are pinned at `derived:participation+pbp`. On the round-4 code (`nfl_rush_profiles.py`
cf230d8c): 197 pass, 1 fails -- the tag pin; the two leak checks pass there
because the round-4 code is right, and each fails on its mutant
(`mutants/mut_r5.py`): **O4f / M11** (`onfield_plays_current` summed over every
PFR-listed game) caught by the invariance check, **M9** (an appearance ignoring
participation on-field snaps) caught by the on-field-only back, the tag back to
pbp caught, the control green. Sonnet's M12 (on-field dropbacks over every
PFR-listed game) stays equivalent by construction: a line outside the
appearance set has no on-field snap, so no on-field dropback.

Full suite (`python3 tests/combo_audit_guards.py`, 2026-10-24 ~02:12 UTC
after round 4, exit code checked): **3,104 passed, 0 failed, exit 0**; after round 5
(~05:47 UTC): **3,091 passed, 0 failed, exit 0** (the total moves with other work in the tree). pyflakes
is clean; the agreed matchup guards and the integration's "rushing aux (O1)"
checks pass inside it. This layer's 198 checks run standalone until the
orchestrator adds the module to the MX list (§8.3). The agreed files and dataset are byte-identical (§10).

## 8. Integration

**8.1** The orchestrator writes the integration. This layer's `refresh()`
needs nothing more from it than one call per season list after
`nfl_matchup_data.refresh` over the same raw files (the extras align to the
store's rows); one season's failure is ledgered `RUSH-build` with its error in
the summary and never stops the next (F4(b)); a season whose pbp file is absent
saves nothing and stays due, and an aux missing another expected source says
so (`R.aux_complete`), which the job's `rush_due` reads (G1). Disk: the job
holds one prior season's raw files at a time, about 77 MB at peak (the
re-checks; the 154 MB figure here was the first integration's).

**8.2 `artifacts.py`**: the deep store gains `nfl_rush_aux_<season>.pkl`
(`nfl_rush_data.SCHEMA` 2 since the snap-count fix: the team total and the
special-teams snaps changed shape, and every aux records its `coverage`).

**8.3 `tests/combo_audit_guards.py`**: `guards_nfl_rush_data` joins the MX
section's module list.

**8.4 Production read** (for the model agent / matchups.py): build the
rushing snapshot in the same niced child as the agreed one -- both together
measured 3.0 s and 137 MB RSS (2024-2026 stores, as of 2026 week 7), and 228
profiles in 0.4 s. Never in a gunicorn worker. A model reads features only
where `RP.restricted(field["source"])` is False (the licence default, §1).

## 9. Limitations and open risks

1. **DECAY 0.75 sits on the rule's boundary** (0.602 / 0.600, before and after
   B1); team tendencies alone would take 0.5. Applied as written; a per-group
   decay is a change to validate.
2. **2026 has no participation**: box by personnel, personnel faced, the front
   proxy, on-field situational shares, pressure and the scramble splits are
   2024-25 tendencies on every 2026 row. A 2026 scheme change is invisible
   until the file ships after the postseason. The pbp hit + sack proxy (F14)
   is the in-season pressure stand-in that is neither PFR nor participation.
3. **NGS RYOE is censored** to 10+-carry games (71-74% of RB runs) and is the
   least stable back measure (r 0.11). It is also restricted (licence).
4. **RPO is charter-dependent** (FTN vs PFR r about 0); read-option is not
   identifiable; CHK = checkdown is unverified.
5. **`sl_proj_*`, `in_sleeper` and `sl_qb1` are Sleeper's revised post-week
   snapshot** (the agreed §6.2), and so is the universe of non-players (§6);
   the 2026 prospective baseline must come from the frozen pre-kickoff captures.
6. **Kalshi settlement**: kneels / scrambles inferred from brackets (239 of
   239); sacks tested and excluded (111 of 111 discriminating Oct-Dec 2025
   ladders; 117 of 117 on Sep 2025-Jan 2026 by the Opus re-check); aborted
   snaps untested (no ladder discriminates).
7. **The defense's QB-rushing OE is near noise** (scrambles r 0.04, k 145-359
   by split; TDs none): shrunk hard by its measured k; a model should expect
   little from it. The hit + sack OE is the exception (r 0.40).
8. **A finding about the AGREED layer (not fixed; its files are frozen)**:
   nflverse leaves `passer_player_id` empty on scrambles, so
   `nfl_matchup_profiles.qb()["scramble"]` reads 0.0 raw for every quarterback
   (Allen, Jackson, Hurts as of 2025 week 10: 0.0 on 504-637 dropbacks; the eb
   then sits at 0.016-0.018 instead of ~0.08), and its QB `dropbacks`,
   `epa_db` and pressure / blitz splits exclude scrambles. The agreed DEFENSE
   scramble rate is unaffected (it counts every dropback). **The agreed
   `nfl_matchup.py` does read scramble-excluded QB splits** (the reviews' m5):
   `qb()["epa_db"]` and `["vs"][pressure / no_pressure / blitz /
   no_blitz]["epa_db"]` through `qb_sens`, for the `press_qb` / `blitz_qb` /
   `press_catch` evidence -- weight 0 in `coefs.json` (only `ptd` ships) but
   shown on cards -- and the agreed research dataset writes `q_dropbacks`,
   `q_epa_press` and the rest from the same fields. This layer's `qb()` counts
   the scrambler as the dropback's QB.
9. **Sacks with a rusher id**: none in 2023-2025; the classification would hold
   if one appeared (guarded).
10. **The injury report is pre-kickoff by construction, nearly always**: by
    `date_modified` (2018 and 2021-2024, ~27,000 rows) 6 rows were modified
    after kickoff (2 in 2018, 3 in 2021 without a game status, 1 in 2024: BAL
    week 1, Kyle Van Noy, Out, 2.5 h after the Thursday opener's kickoff);
    the 2025 and 2026 files carry no `date_modified`, so it cannot be checked
    there (the reviews' m9).
11. **Licence**: PFR- and NGS-derived fields are display-only by default
    (§1); a model that uses one needs the owner's decision first.
    Participation through 2022 is NFL NGS charting by its credit line and is
    not tagged restricted -- the owner's NFL-data decision covers it.
12. **Disk**: the job drops each prior season's raw files before the next
    downloads -- about 77 MB at peak on a 1 GB server disk (m6; the re-checks).
13. **An incomplete aux is retried, at a cost**: a source that keeps failing
    to download leaves its season due on every run; the job's weekly retry
    marker caps that at one download a week (the orchestrator's).

## 10. Hashes (sha256, 2026-10-24, round 5)

| file | bytes | sha256 |
|---|---|---|
| `nfl_rush_data.py` | 38,110 | `247cc6256182d9ecca66384b5fe37112594671391828e5b53861e4083329ccd1` |
| `nfl_rush_profiles.py` | 118,311 | `0d319ed09e7ea13598c952f63d672438f8da3dcd200b5aa8496add9e0af6f0c7` |
| `research/nfl_rush_dataset.py` | 69,695 | `8b97b7af888b28f9da982021d7a888d76ec5b77a650bd0255c2e79e8cabdc8d3` (2026-10-29; the lineage is below the table) |
| `tests/guards_nfl_rush_data.py` | 110,910 | `6fab3e1db94a14dc57d38349f80946f319bad2b814e29712a6c000fa76b9ac8f` |
| `research/data/nfl_rush/checks.json` | 21,315 | `3bca9800a9d31df144123e383d99bb35a04f681b0448834ff0e2290444179586` |
| `research/data/nfl_rush/meta.json` | 2,477 | `ced28e4c6534d41e4b3258acfa5cb870d24a34afd5670e6fbff8f7701966b809` |
| `research/data/nfl_rush/persistence.json` | 77,822 | `1234070ea07f62addcc6d854473d3b855a1284bf5f66abcb25a094a0792a61a0` |
| `research/data/nfl_rush/qb_games_2023_2026.csv.gz` | 1,567,255 | `d7a42025f001a1f66e4dbb702ca7c7356d2f6696534177c888b6995275d5de42` |
| `research/data/nfl_rush/rb_games_2023_2026.csv.gz` | 7,677,076 | `f39a12ede637e30680043a1250a062dde93bd6ad160bfcc4fa2278d325dffe0f` |
| `research/data/nfl_rush/reliability.json` | 299,280 | `25e9bc1d8854c4cc46b8368dafc596f55c79e5879362a317c0ca9941a1043fc3` |
| `research/data/nfl_rush/schema.json` | 343,540 | `18b581ac189cf73beeaf26c504266bafe6ba368d68d96662e88768b3c992eaea` |
| `research/data/nfl_rush/sneak_rule.json` | 1,894 | `01da9a7cf15c96eae4d5fcd257c8b5395bbb85d3ec7e2f8ea37468dfcf357f2d` |

The agreed foundation, unchanged (byte-identical to the agreed hashes):

| file | sha256 |
|---|---|
| `nfl_matchup_data.py` | `8511346956bb4ae2f77fb4832075e861fb4dd289b9384b484f5f8c4c5ec04d13` |
| `nfl_matchup_profiles.py` | `d2007503a6d85dd5bf5b69d7e146c7d95fc6c49b09bc513859d19d0ca1014b8d` |
| `research/data/nfl_matchup/dataset_2023_2026.csv.gz` | `766a60ed11ee3c017141443bd114625b3d756c5bb03737dfc3148389bbef2738` |

Round 5 changed no data value. Against round 4: `nfl_rush_data.py`, `research/nfl_rush_dataset.py`, both datasets, `reliability.json`, `persistence.json`, `sneak_rule.json`, `meta.json` and `checks.json` are byte-identical; `nfl_rush_profiles.py` changed in one `SOURCE_OF` rule (the usage / recent tag) and `schema.json` in the `source_tag` and `source` text of those 38 RB columns only (written alone, from a rebuild into a temporary directory whose two datasets equalled the committed ones byte for byte); the guard file and this report changed. Round 4's measurement ran at 02:01:13 UTC with its pre-run hashes recorded first (script `02e53c7b...`, profiles `dac01593...`, data `247cc625...`); after it `nfl_rush_profiles.py` changed in `_K_DEFAULT` (copied from the json) and the tag rule, `research/nfl_rush_dataset.py` in the column selection (`_p_cols`) -- none of it read by the measurement. Committed data: 9,990,659 bytes in all.

**The builder since round 5** (written 2026-10-29, when the rushing model registered; this row read 82e4aa12 for
two rounds after the code moved). `research/nfl_rush_dataset.py` went 82e4aa12 (66,707 bytes, round 5) ->
3451bede (`--out`: the prospective dataset's own directory) -> d75ad292 (its `meta.json` records the sha256 of
every repository module the build loaded and the Python / numpy versions, the model's C2b) -> 8b97b7af (the
model's rule round 4: `--all-weeks` / `--max-week N`, Fable's B1, so the decisive test's 2026 rows past week 7
can be built at all; `meta["inputs"]`, the sha256 of every deep pickle the build loaded, Opus's F1b; and
`by_season` names its `last_week`). None of it moves a row the default build writes: with no flag `run_build`
still gets `max_week=None`, which is `TEST_MAX_WEEK` ({2026: 7}), exactly as before; the round-3 scratch build
with the code record reproduced both CSVs byte for byte (f39a12ed / d7a42025), and round 4 changed only the
argument parsing and the meta. The committed `meta.json` (ced28e4c) predates all three changes and is
unchanged, so it records no code and no inputs (the model report's Fable n2: only the PROSPECTIVE dataset's
meta is held to them). 8b97b7af is the copy the rushing model registered
(`research/data/nfl_rush_model/prereg_code/research__nfl_rush_dataset.py.txt`).
