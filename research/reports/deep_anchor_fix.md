# The deep engine's market anchor: the fix, built OFF (2026-11-27)

The fix moves the deep engine's market anchor from 2.25 to 1.86 points of margin per win. It is built behind
one switch, `nfl_deep_data.ANCHOR_V2`, which is off, so production is unchanged. The deep-weight read must be
committed before the switch may be flipped; guard DA2 enforces that.

## The owner's decision

The C3 follow-up found the problem (`research/reports/c3_season_replay.md`, addendum).
`nfl_deep_data.market_margins` converts Kalshi's rest-of-season pace to a margin at the drive engine's 2.25. The
deep engine does not compress margins: its realized margin is 1.007x the anchored one. So a deep season seeded
with the ladder returns the market's spread 1.21x.

The owner's decision: "Fix the deep-engine market anchor immediately after the registered 100-game read,
preserving its original result and model version."

## 1. The constant, measured

The criterion is the one nfl_season._PTS_PER_WIN was fitted by: seeded with the market's wins, the season
gives them back, so the recovered-win slope is 1.

**Why permutations were dropped.** A first design permuted one season's wins over the teams to get replicates.
It was stopped, because a market's win totals are schedule-inclusive and production converts them ignoring the
schedule. Permuting removes that joint structure: at the analytic resolution's 1.842, permutations of 2025
returned slopes of 0.78-0.98 (mean 0.90), against 1.00 for the real assignment.

**The replicates are 24 real seasons** (`research/deep_anchor_scale.py`, `research/data/deep_anchor_scale.json`,
`deep_anchor_seasons.json`):
- every season from 2002 to 2025 from ESPN, each with its own schedule and its own wins shrunk halfway to the
  mean;
- league-average units anchored through production's `apply_anchor`;
- 300 games per game, with common seeds across K.

| | value |
|---|---|
| slope at K = 1.80 | 0.9671 |
| slope at K = 1.90 | 1.0190 |
| K* (interpolated) | **1.8635**, 95% bootstrap over seasons 1.8385-1.8865 |
| shipped `MARKET_PTS_PER_WIN_V2` | **1.86** |
| verification at 1.86, fresh seeds | slope **1.0002**, SE 0.0064 (season SD 0.032); win error 0.196 wins |
| analytic reference (Phi(./13.2) at its own slope-1 K, 1.896 over these seasons) | win error 0.167 wins (the schedule's alone) |
| tolerance (stated before the run) | slope 1 +/- 0.03 with SE <= 0.01; win error <= reference + 0.10 |
| **passes** | **yes** (slope inside 1 +/- 0.03, SE <= 0.01, win error 0.196 <= 0.267) |

Guard DA3 pins the shipped value to the measurement, and recomputes the analytic reference exactly from the
committed seasons.

## 2. The switch and the versions

The flip is ONE line, `ANCHOR_V2 = True` in `nfl_deep_data.py`. Everything else follows from it.

- **The constants.** `ANCHOR_VERSION` becomes 2, and `market_pts_per_win()` returns 1.86 instead of
  `nfl_season._PTS_PER_WIN`.
- **The stamps.**
  - Every anchored profile's `anchor` block stamps `version` and `pts_per_win`.
  - The deep season payload's `engine` stamps `anchor_version` and `anchor_pts_per_win`.
  - The anchor block is in each profile, and the profiles are in the pool cache's inputs hash, so a version-1
    world cannot be read back after the flip. `nfl_deep_data.py` is now in `pool_cache._CODE_FILES` as well.
  - (DA4 checks that the two hashes differ.)
- **The loader.** `nfl_game_sim._deep_profiles` refuses a nightly deep payload of another anchor version and
  rebuilds. That loader feeds both the slate's deep figure and the DFS worlds. Without the refusal, version-1
  levels would have served for up to 36 hours after the flip.
- **The futures board.** The deep season futures board shows the last nightly payload until the next run,
  under 24 hours. The flip checklist below queues that rerun.
- **The DFS engine's stamp.** `nfl_dfs_engine.SIM_MODEL_VERSION` is NOT bumped, and its board stamp does not
  change. `nfl_dfs_engine.py` is pinned by sha256 in the coaching DFS registration (`prereg_dfs.json`), whose
  read refuses a changed file. A DFS board built after the flip says so through its profiles' anchor block and
  through the pool cache key, not through the engine's version number.
- **Pinned files.** `nfl_dfs_engine.py` and `nfl_deep_sim.py` are untouched (DA5 checks their pins).

**Flip checklist** (the orchestrator, after `research/data/nfl_deep_weight_read.json` is committed):
1. Set `ANCHOR_V2 = True` in `nfl_deep_data.py`. Guard DA2 passes only with the read's result committed.
2. Run the suite.
3. Queue an `nfl_deep` rerun.
4. Note the flip commit's UTC time. The prospective test's rule (section 6.9) reports games before and after
   an engine change.

## 3. Downstream, before deployment (`research/deep_anchor_downstream.py`, `research/data/deep_anchor_downstream.json`)

The same production inputs were built both ways on shared seeds, using production's `build_profiles` with the
switch and Sleeper's roster factors. No outcome was read.
- **Current:** 2026 week 12's games not yet final, on the live ladder.
- **Past:** 2026 week 9, with the store cut before it and the ladder rebuilt from Kalshi's daily candlesticks.

Each cell is v1 -> v2. v1 is the switch off (2.25), v2 the switch on (1.86).

**Games.** The deep engine on the slate's profiles, 4,000 games each, pooled over the week's games.

| | current (week 12, 12 games) | past (week 9, 15 games) |
|---|---|---|
| mean absolute margin | 13.23 -> 12.57 (-0.66) | 12.23 -> 11.68 (-0.54) |
| margin SD | 16.26 -> 15.50 | 15.49 -> 14.78 |
| total, mean / SD | 45.50 / 12.85 -> 45.41 / 12.92 | 46.54 / 13.27 -> 46.34 / 13.26 |
| total 10/50/90% | 29 / 45 / 62, unchanged | 30 / 46 / 64, unchanged |
| blowouts, margin >= 17 | 32.4% -> 30.0% | 28.5% -> 26.4% |
| blowouts, margin >= 24 | 16.6% -> 14.4% | 13.7% -> 11.8% |
| home win, per game | mean move 2.5 pts, largest 4.9 | mean move 2.8 pts, largest 5.5 |

The largest single-game moves:
- **Current:** NYJ @ MIA, 22.7% -> 27.6% home; TEN @ JAX, 92.6% -> 88.1%.
- **Past:** DET @ MIA, 22.4% -> 27.9%.

**Deep season.** 1,000 seasons on one seed, run from that week. The figures carry Monte Carlo noise of about
1-1.5 points where a team sits near 50%.

| | current | past |
|---|---|---|
| division title | mean move 1.0 pt, largest 3.7 (CHI 39.1% -> 42.8%) | mean 1.4, largest 4.4 (DEN 64.2% -> 59.8%) |
| playoffs | mean 1.1, largest 4.9 | mean 1.9, largest 8.0 |

**DFS worlds.** `player_pool` on the engine model, 10,000 worlds on one seed.

| | current | past |
|---|---|---|
| mean DK points by position | QB -0.08, RB -0.01, WR -0.02, TE -0.02, DST -0.02, K -0.01 | QB -0.19, RB -0.04, WR -0.05, TE -0.03, DST +0.03, K -0.02 |
| per player | mean move 0.10, largest 0.84 | mean 0.12, largest 1.22 |
| QB-WR1/WR2, same team | 0.359 -> 0.359 | 0.361 -> 0.363 |
| RB1-DST, same team | 0.053 -> 0.054 | 0.042 -> 0.039 |

The worlds' scoring level is 70% the drive model's (`DEEP_WEIGHT` 0.30), so a sixth narrower deep margin
barely reaches the players. Means move by hundredths, the largest single player by about a point, and the
correlations not at all.

**The classic board.** Current main slate, DraftKings draft group 157066, two contests. Both builds used
seed 20261127, at reduced size: 20,000 worlds, a 100,000-lineup field and 600 candidates. Each pair below is
the top-20 overlap and the Spearman correlation of the lineups the two lists share.

| list (two contests) | v1 vs v2 | v1 vs v1, another seed (the board's own spread) |
|---|---|---|
| money | 30% / 0.57, 30% / 0.65 | 10%, 10% |
| top 1% | 25% / 0.51, 20% / 0.63 | 5%, 5% |
| top 0.1% | 5% / -, 0% / 0.49 | 5%, 5% |
| EV | 0%, 0% | 0%, 5% |
| first place | 0%, 0% | 0%, 5% |

**Reading the board.** At this reduced size the board's own lists are unstable from seed to seed: two
version-1 builds on different seeds share 0-10% of their top 20s. Built on the same seed, the anchor change
keeps more than that: 0-30% shared, with rank correlations of 0.5-0.65 where lists overlap. The fix moves the
board less than the board's own sampling does.

That does not mean the lists will stand still. A production build (180,000 worlds, a 300,000-lineup field) is
steadier than this one, so the fix's own share of the change there is unmeasured. It changes which lineups top
each list, not the board's shape. Every board after the flip is a different board, and is labelled as one
(section 2).

**Two inputs are not fully historical.**
- The past week's DFS players carry Sleeper's injury tags as of today, not as of week 9.
- 315 of the 497 win-total markets in the 2026 events had a daily candle in the four days before week 9. Every
  one of the 32 teams kept a ladder.

Both inputs are the same on the two sides, so the deltas are the anchor's.

## 4. Contamination: what the flip can and cannot change

Every registered read was checked against the files it pins and the anchor it uses. Most cannot be touched by
the flip.

**Cannot be changed by the flip:**
- **Coaching R1, C4, R2 H1/H2** (`prereg_r1`, `prereg_c4`, `prereg_r2`). They pin `nfl_deep_sim.py`, which is
  untouched. They build their own profiles and import no market anchor; none of the coaching reads mentions
  `nfl_deep_data`.
- **Coaching DFS read** (`prereg_dfs`). It pins `nfl_dfs_engine.py` and `nfl_deep_sim.py` (both untouched). Its
  harness, `nfl_dfs_engine_eval.week_profiles`, anchors to PD ratings at `nfl_season._PTS_PER_WIN` with
  `market_margins` replaced, so it never calls `market_pts_per_win` (DA5). The new anchor-block keys are
  metadata the engine does not read.
- **The DFS engine's own tests** (2025 blind, the 2026 attempts test). They run on the same PD-anchored
  harness, so the switch cannot reach them.
- **NFL market anchor 8.3** (game_xref). It grades the raw drive sim against the close; there is no deep
  engine in either arm.
- **C3** (read) and **C3-N** (registered 2028-01-12). Both are the drive engine against an analytic
  resolution; there is no deep engine.
- **Accuracy batch reads** (B1, B4/H2, C1, N1, N2, tennis, G2), **weather stage 2**, **the rushing model**
  and **the matchup model**. None of them reads the NFL deep anchor.
- **R3.** No R3 registration exists in this tree.

**Can be changed by the flip:**
- **The NFL deep-weight 100-game read** (`nfl_deep_weight_read.py`). It grades the live `nfl_deep` shadow rows,
  so a flip before it is committed would grade a different engine. DA2 blocks the flip until
  `research/data/nfl_deep_weight_read.json` is tracked and triggered. The read writes that file only after its
  100th game has settled, and every one of those games has kicked off, so the read grades anchor version 1
  only. Its result and that model version are preserved: the file is never rewritten, and the rows it read are
  in the past.
- **The prospective test** (same script, fresh games after the read). Yes, by its own rule. Section 6.9 says
  "Engine changes during the window ... the read reports the games before and after each one, as description
  only. The decision uses all of them, because the weight is a property of the engines as they run."
  - Fresh games after the flip grade anchor version 2 in the same `nfl_deep` bucket. That is by design, and the
    bucket is deliberately not renamed: a rename would drop those games from the registered test.
  - **The owner must know** that the prospective test will mix versions 1 and 2. The before/after split needs
    the flip commit's time.
- **Classic field, second pass** (`cl_field2`, the 2026-11-29 and 12-06 Millionaires' served boards). Yes,
  through the served board. Its read takes the served board's `field_pct` / `opt_pct`, and the board's worlds
  carry the deep level at weight 0.30.
  - 11-29 locks before any flip.
  - 12-06 is the risk. The read could trigger around week 12's end (84 of 100 graded games on 11-26; about 14
    more in week 12), and a flip right after it would serve a version-2 board to 12-06.
  - **The owner must choose:** flip after the 12-06 lock, or accept that contest 2 reads a version-2 board.
    `cl_field2_fields.py` also refuses a dirty `dfs_tourney.py`; this change does not touch it.
- **DK duplication criteria** (`dk_dup_criteria`). It reads served boards' fields on future contests. A
  version-2 board's field moves with its worlds; this is informational, and nothing gates on it.

## 5. What is NOT done

The switch is not flipped. No production number moves today. Two things change today: profiles carry two more
anchor-block keys, and `nfl_deep_data.py` is in the pool cache's code files. Both change the pool cache key
once, so the PC rebuilds its cached worlds one time; every number is unchanged.
