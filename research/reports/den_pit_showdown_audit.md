# DEN @ PIT showdown audit (DraftKings draft group 157076), 2026-11-27

Research only. Nothing in production was changed, rebuilt or deployed; no experimental engine reached a
board. Every input of the controlled builds was recorded at 16:38 UTC (3 h 22 min before the 20:00 UTC
lock) and replayed; the game's result was never read. Harness and outputs: `research/den_pit_audit/`,
`research/data/den_pit_audit/`.

The owner asked how the 2026-11-26/27 sprint affected this slate, with the pre-sprint engine on identical
projections, salaries, injury information, seeds and contest settings, and expected the slate to benefit
more from improved lineup evaluation than from improved football predictions.

## 1. The answers in brief

* **What the sprint changed on this board: the D/ST rule, and nothing else.** Built twice on identical
  recorded inputs (the same 120 HTTP answers in the same order, no miss), the same seed (20261127) and
  60,000 worlds, the pre-sprint engine (6c0194e) and HEAD (e316055) give **bit-identical worlds for 22 of
  the 24 pool players**. Only the two defenses move: Broncos 7.426 -> 7.338 (-0.087, paired SE 0.002),
  Steelers 5.254 -> 5.179 (-0.076, SE 0.002), each DOWN in about 3.9% of games and never up; floors and
  ceilings unchanged (2 / 14 and 0 / 11). Rescoring HEAD's own simulated box scores with the old and the new
  points-allowed rule reproduces BOTH builds' defense arrays bit for bit, so the D/ST correction explains
  100% of the difference.
* **The board barely moves.** Field ownership and modeled duplication are identical (the field is weighted
  on Sleeper's projections, not on the simulation). Captain best-lineup shares: Spearman 1.000, the largest
  move 0.06 points (Broncos). FLEX: Broncos -0.45, Steelers -0.28 points. The money and top-1% lists are
  identical through 20; the leader (Coleman CPT + Lutz, Broncos, Nix, Warren, Rudolph) cash 68.0 -> 67.8%,
  top 1% 7.17 -> 7.17%, EV $36.59 -> $36.43. Portfolio coverage 28.3 / 45.3 / 63.8% in both builds.
  Paired standard errors are in section 5.4.
* **Both served builds already carried the correction.** Last night's board (23:24 UTC, sim-history
  5e8f0f2) and the 19:14 UTC pre-lock refresh both stamp `simulator.model` engine-deep, **version 2**
  (`nfl_dfs_engine.SIM_MODEL_VERSION` 2 = the 2026-11-26 D/ST rule, f16a188). Everything else the sprint
  shipped is infrastructure that leaves a showdown board bit for bit or applies only when the owner asks.
* **On the owner's expectation:** on this board neither "football predictions" nor "lineup evaluation"
  moved much because of the sprint. The football change is the D/ST rule (-0.08 points a defense). The
  showdown board's evaluation machinery (exhaustive universe, weighted field, payout grid, greedy cover) is
  unchanged; the new evaluation tools (the lineup evaluator, its GPU lane, the worlds cache) are opt-in or
  speed-only and change no figure the board prints. What DID move the served board between last night and
  this afternoon were the inputs (Tyler Badie added at $200, Nate Adkins dropped from the field, lineups we
  may enter 32,771 -> 47,644, Bernard 7.6 -> 5.8, Metcalf 7.5 -> 6.7) and the unseeded worlds.
* **Caching and GPU on this slate:** the showdown scoring pass is CPU by design (its field is weighted; the
  GPU scoring path takes uniform classic fields only), the boards stamp no cache read, and the ledger holds no
  `TOURN-gpu*`, `EVAL-gpu*`, `POOLCACHE` or `CKPT` row in its 7-day window. Both served boards carry an
  evaluator artifact, so the evaluator can score a hand-built lineup on this slate; nothing the server holds
  shows that it was used (section 8).
* **The depth gate (9.1):** the field-only receivers (four at the refresh: Pittman, Mims, Franklin,
  Hurleman; five last night with Adkins) are in the universe's best lineup in 22.5% of worlds, and in 12.9%
  only the gate keeps that lineup from us; lifting it would raise our best lineup in 21% of worlds by 3.6
  points -- but the board's best top-1% (7.17%) and cash (67.8%) lineups, its best projection (80.8) and its
  list leaders are the same with the gate off.
* **Rare outcomes (9.2):** the leader's "0.000%" first place is duplication, not impossibility: it topped
  the whole universe in 89 of 60,000 worlds, but ~119 field entries share it there. The first-place leader
  (Warren captain, 0.054%) rests on 13 worlds. A true zero-count at 60,000 worlds has an exact 95% upper
  bound of 0.0061%. No resolution rule counts worlds (`nfl_dfs.resolution` governs the sheet's sampled field;
  `sd_money_gate` marks every money column non-authoritative).
* **Weather and game scripts (9.3-9.4):** the board uses no weather (the stage-1 test failed). The Coleman
  CPT + Lutz + Broncos lineup's top-1% worlds are Denver controlling a low-scoring game on the ground: DEN by
  8+ in 77% of them (36% of all worlds), PIT held to 10.6 points, Coleman 25.8, two-plus Denver rushing TDs
  in 36% (23% overall), totals of 45+ under-represented.
* **R5 (experimental, not validated):** on production's base tables and the same anchored profiles, R5's
  coaching layer leaves the score where production has it (total +0.2 +/- 0.2) and changes the game's
  shape: +1.1 snaps inside the first half's two-minute warning and +0.5 inside the game's, Denver (the favourite) running more late (+0.3 Q4 carries, +1.8
  yards), ties 0.4% -> 1.2%, and the starting quarterback pulled in ~15% of team-games. On R5's own
  registered tables the game scores 4.7 points less -- that is those tables' 2016-2023 base, not the
  coaching. Through the DFS engine's own anchor and deal it moves no player beyond the anchor's seed noise
  and no key correlation by more than 0.025 (section 10).

## 2. What was compared

| board | built (UTC) | code | inputs | seed | worlds |
|---|---|---|---|---|---|
| served, night | 2026-11-26 23:24 | the PC's, mid-sprint (stamps below) | live, roster fetched 22:55 | none (production) | 60,000 |
| served, refresh | 2026-11-27 19:14 (46 min before lock) | the PC's, resumable-builds era (`checkpoint` stamp) | live, roster fetched 18:11 | none | 60,000 |
| pre-sprint rebuild | this audit | 6c0194e (2026-11-11, the parent of the sprint's first commit 02c4a7e) | recorded 16:38 UTC, replayed | 20261127 | 60,000 |
| HEAD rebuild | this audit | e316055 (the work branch's head) | the same recording | 20261127 | 60,000 |

The two rebuilds are the controlled comparison. The served boards are compared to them **descriptively
only**: their inputs were read at other times and their worlds are unseeded.

## 3. The served boards: engine, features, counts

| | night | refresh |
|---|---|---|
| tournament engine | `SD_ENGINE` 7, board `version` 3, `model` engine | same |
| simulator | engine-deep **v2** (D/ST correction), deep weight 0.30, `ENV_SD` 0.14, form SD 0.08, home edge 1.021 yd, pass attempts anchored to Sleeper (`ATT_WEIGHT` 1), usage 0.3 (targets 6 g, carries 3 g), allocation noise fitted (`deal_params_hash` df906d36b9effb01) | same |
| worlds | 60,000 games; anchor 800 games x 2 passes; drive model 2,400 games a matchup (16 games) | same |
| matchup model in DFS | not applied (weight 0, the pre-registered test shipped nothing) | same |
| weather | not used (stage 1 FAILED; information only) | same |
| universe | 790,189 legal lineups, enumerated (exhaustive field) | 790,222 |
| lineups we may enter | 32,771 | 47,644 |
| field-only players (depth gate) | Mims WR4, Franklin WR5, Pittman WR5, Hurleman WR6, Adkins TE3 | Mims WR4, Pittman WR4, Franklin WR5, Hurleman WR5 (Adkins out of the field to hold it under 900,000) |
| field model | softmax(beta x projection), top build 0.20% (~118 copies), uncalibrated | same |
| modeled pair collision (`field_duplication`) | 1.266e-4 | 1.258e-4 |
| team level (landed vs target) | DEN 20.93 vs 21.33, PIT 17.38 vs 17.48 | DEN 21.36 vs 21.45, PIT 16.93 vs 17.33 |
| portfolio any-top-1% (5 / 10 / 20, in-sample) | 27.4 / 43.9 / 63.0 | 28.0 / 44.7 / 63.4 |
| timings | sims 38.8 s, score 343.3, portfolio 244.9, total 632.5 | 30.8 / 354.0 / 249.1 / 638.0 |
| evaluator artifact | 157076-1795735473 | 157076-1795806854 |
| checkpoint stamp | none (built before resumable builds) | key ed9f2069..., resumed nothing |
| money columns authoritative (`sd_money_gate`) | no: field uncalibrated, joint model not validated | no |

Which code built them: the night board's `simulator.version` 2 places it at or after f16a188 (16:30 UTC);
its `field_duplication` and `eval_artifact` stamps at or after 27eacbd (17:34); it has no `checkpoint`
stamp, which 1210532 (23:33) added. The PC's GPU bench reported commit 5696b7d at 21:58 UTC, so the PC was
on 5696b7d-era code. The refresh carries the checkpoint stamp, so it ran on resumable-builds code or later.

## 4. The sprint's commits, by what they can do to a showdown board

Every commit from 02c4a7e (2026-11-26 13:56) to e316055 that touches the DFS stack, classified by reading
the diff of `nfl_dfs_engine.py`, `nfl_deep_sim.py`, `nfl_dfs_csim.py`, `nfl_dfs_sim.py`, `nfl_dfs.py` and
`dfs_tourney.py` (showdown path: `build_nfl_showdown`, `enumerate_showdown`, `field_weights`,
`field_ownership`, `run_subset`, `payout_grid`, `money_places`, `tie_payout`, `sd_money_gate` and
`_field_extras` are unchanged; `_score_range`, `run_vs_field`, `greedy_cover`, `portfolio` changed only
behind checkpoint / GPU / caps switches).

| commit | what | can it move this board's numbers? | on this board |
|---|---|---|---|
| f16a188 | D/ST points allowed by event (`off_points`, `td_def`), `SIM_MODEL_VERSION` 2 | **yes** (the two DST arrays) | -0.087 / -0.076, section 6 |
| d37d3e6 | touchdown allocator cannot lose a TD; two-QB lines | only where a team deals 13+ TDs, a weightless man has room, or two QBs are projected | **no**: every offensive array bit-identical |
| 9317620 / 1210532 / 38a6e14 / b8d7fe8 / 63153eb / 311d122 | worlds cache, checkpoints, frozen inputs, compact pools, -0.0 normalisation | no (same values; guarded MEM1-6, RI) | not exercised here (no cache, no checkpoint dir) |
| 230b08a / e9c5847 / 0daacd4 / 27eacbd | lineup evaluator, its GPU lane, `field_duplication` stamp | no (separate lane; display stamp) | artifact present on both served boards |
| 7b6c34d | tournament scoring on the GPU | classic only (uniform field) | showdown stays on the CPU by design |
| 5e18e4a / 950dff2 | portfolio caps, roster rules | only with an owner's `pick` | `limits=None`: the old loop, line for line |
| 4ad1051 / 2c36839 / 44dc374 | build profiles, memprobe, the deep probe | classic only / measurement | none |
| 3ef7ce9, 3c20f21, fe2a475 | Pick6 ledger, any-line histograms | no (week board only, `with_dists`) | none |
| eaca95d ... 416ba98 | coaching engines R1-R5, studies | no (research only) | section 10 |
| cl_field2, dk_mech, C3, sensitivity, crypto | research / other tabs | no | none |

`SD_ENGINE` is 7 before and after, so nothing in the sprint forced a showdown rebuild.

## 5. The controlled comparison: pre-sprint vs HEAD

### 5.1 Method

* `research/den_pit_audit/netreplay.py` wraps `urllib.request.urlopen` (every fetch in this tree goes through
  it) before anything is imported. A recording build stored every answer; both comparison builds replayed it.
  **Both made the same 120 requests in the same order (108 distinct), with no miss and no request past a
  recorded sequence** (`compare.json` -> `requests`).
* The environment is the suite's hermetic one: `VIGIL_NO_BG=1`, `VIGIL_POOL_CACHE_DIR=""`, no
  `VIGIL_CKPT_DIR`, no `VIGIL_TOURNEY_GPU`, private disk caches seeded with the same 2026 play-by-play store.
* The builds are `dfs_tourney.build_nfl_showdown(157076, contest_id=198174394, n_sims=60000, week=12,
  model="engine", seed=20261127, workers=4)` exactly as each checkout has it. `build_board.py` also keeps
  the score matrix X, the universe, the field weights and `run_subset`'s per-lineup sums (which hold the top
  0.1% figure the showdown table never prints).
* **The first recording was wrong, and is not used.** It keyed a request on its URL alone: Sleeper's week
  projections are read twice, once with `Accept-Encoding: gzip` (`nfl_dfs_sim._get`, which decompresses)
  and once without (`racing._get_json`), so the second reader got gzip bytes, failed, and the drive model's
  level silently vanished (DEN 26.0 v PIT 16.7 instead of 21.4 v 17.2). It also answered a retry after a
  Kalshi 429 with the same 429. The harness now keys on method, URL, body, Accept-Encoding and User-Agent and
  keeps each key's answers as a sequence (a 429-429-200 retry replays as 429-429-200);
  `tests/guards_auto_netreplay.py` (RPL1-RPL4) pins both. The recording used saw Kalshi 429s and retries as
  production does: KXNFLWINS answered on the third try, AFC North/West on the second, NFC North never (that
  series reads nothing for DEN or PIT; production sees the same on a busy minute).

### 5.2 Inputs

Identical in both builds: the 24-player pool, salaries, Sleeper projections, depth chart and field-only
flags; the universe (790,222 lineups) and its order; the lineups we may enter (47,644); the field weights
(max |diff| 0.0; beta 0.3309 both). Team level, both builds: targets PIT 17.18 / DEN 21.29 (0.7 drive model
+ 0.3 deep engine), worlds PIT 17.18 / DEN 21.40.

### 5.3 Players: mean, floor (10th percentile), ceiling (90th), paired

The two builds share their worlds, so every difference carries the PAIRED Monte Carlo SE (SD of the
per-world difference / sqrt(60,000)); the floor and ceiling SEs are a paired bootstrap over worlds (200
resamples).

| player | pos | mean pre | mean HEAD | diff (paired SE) | floor pre/HEAD | ceiling pre/HEAD | worlds moved of 60,000 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Jonah Coleman | RB | 17.41 | 17.41 | +0.0000 (0.0000) | 6.6 / 6.6 | 30.4 / 30.4 | 0 |
| Bo Nix | QB | 15.765 | 15.765 | +0.0000 (0.0000) | 6.88 / 6.88 | 26.04 / 26.04 | 0 |
| Jaylen Warren | RB | 13.704 | 13.704 | +0.0000 (0.0000) | 5.2 / 5.2 | 24.6 / 24.6 | 0 |
| Mason Rudolph | QB | 11.618 | 11.618 | +0.0000 (0.0000) | 4.32 / 4.32 | 20.08 / 20.08 | 0 |
| Jaylen Waddle | WR | 9.586 | 9.586 | +0.0000 (0.0000) | 2.3 / 2.3 | 18.7 / 18.7 | 0 |
| Rico Dowdle | RB | 9.066 | 9.066 | +0.0000 (0.0000) | 2.4 / 2.4 | 17.7 / 17.7 | 0 |
| Wil Lutz | K | 8.621 | 8.621 | +0.0000 (0.0000) | 3 / 3 | 14 / 14 | 0 |
| Chris Boswell | K | 7.674 | 7.674 | +0.0000 (0.0000) | 2 / 2 | 14 / 14 | 0 |
| Broncos | DST | 7.426 | 7.338 | -0.0872 (0.0020) | 2 / 2 | 14 / 14 | 2370 |
| Pat Bryant | WR | 6.937 | 6.937 | +0.0000 (0.0000) | 1.1 / 1.1 | 14.7 / 14.7 | 0 |
| Courtland Sutton | WR | 6.625 | 6.625 | +0.0000 (0.0000) | 0 / 0 | 14 / 14 | 0 |
| DK Metcalf | WR | 6.479 | 6.479 | +0.0000 (0.0000) | 0.4 / 0.4 | 13.4 / 13.4 | 0 |
| Pat Freiermuth | TE | 6.139 | 6.139 | +0.0000 (0.0000) | 1.1 / 1.1 | 13.1 / 13.1 | 0 |
| Germie Bernard | WR | 5.712 | 5.712 | +0.0000 (0.0000) | 1.3 / 1.3 | 11.6 / 11.6 | 0 |
| Evan Engram | TE | 5.551 | 5.551 | +0.0000 (0.0000) | 0 / 0 | 12 / 12 | 0 |
| Steelers | DST | 5.254 | 5.179 | -0.0757 (0.0018) | 0 / 0 | 11 / 11 | 2313 |
| Tyler Badie | RB | 4.904 | 4.904 | +0.0000 (0.0000) | 0.7 / 0.7 | 10.5 / 10.5 | 0 |
| Michael Pittman Jr. (field only) | WR | 4.72 | 4.72 | +0.0000 (0.0000) | 0 / 0 | 10.3 / 10.3 | 0 |
| Darnell Washington | TE | 4.476 | 4.476 | +0.0000 (0.0000) | 0 / 0 | 10.3 / 10.3 | 0 |
| Marvin Mims Jr. (field only) | WR | 4.058 | 4.058 | +0.0000 (0.0000) | 0 / 0 | 9.2 / 9.2 | 0 |
| Roman Wilson | WR | 3.814 | 3.814 | +0.0000 (0.0000) | 0 / 0 | 9.4 / 9.4 | 0 |
| Troy Franklin (field only) | WR | 2.383 | 2.383 | +0.0000 (0.0000) | 0 / 0 | 6.2 / 6.2 | 0 |
| Adam Trautman | TE | 2.34 | 2.34 | +0.0000 (0.0000) | 0 / 0 | 6.2 / 6.2 | 0 |
| Max Hurleman (field only) | WR | 2.221 | 2.221 | +0.0000 (0.0000) | 0 / 0 | 5.9 / 5.9 | 0 |

### 5.4 Captain and FLEX rankings, lists, lineup probabilities

* Captain best-lineup share (`opt_cpt_pct`): Spearman 1.000 pre vs HEAD; largest move Broncos -0.06
  points. Top 10, identical in both builds: Coleman, Nix, Warren,
  Rudolph, Dowdle, Waddle, Broncos, Bryant, Lutz, Sutton.
* FLEX best-lineup share: Spearman 0.999; Broncos -0.45, Steelers -0.28, every other player +0.01 to
  +0.09 (the shares the defenses lose). Top 10 (HEAD): Nix, Rudolph, Coleman, Warren, Lutz, Waddle, Dowdle,
  Broncos, Boswell, Bryant.
* Lineup probabilities with PAIRED SE (T4, the board's own per-world arithmetic recomputed in
  `universe.py`, which reproduces every board figure to 1e-13; the two builds differ only in the 4,595
  worlds where a defense moved, so those worlds are recomputed for both builds and every other world's
  difference is exactly zero). Over all 70 tracked lineups (37 hold a defense): those with a defense
  lose 0.02-0.49 cash points (up to 18 SE), those without gain 0.06-0.26 (7-13 SE: their field rivals
  with a defense score less) -- real, and small. Top 1%: -0.11 to +0.03 points (up to 9 SE); top 0.1%:
  within 0.011 points (up to 3 SE); first place (outright or a share): within 0.005 points (under 2 SE);
  EV: -$5.17 to +$1.31 a $20 entry, the large moves on high-EV, low-copy lineups (the leaders' within
  $0.31). T4 shows the leaders of each list.
* Field ownership (`field_cpt_pct`, `field_flex_pct`): identical. Modeled duplication: identical
  (1.2528e-4 in both; the pre-sprint board has no stamp, so it was recomputed with HEAD's
  `field_duplication` on the pre build's own universe and weights).

| list | same top 10 order | top-10 overlap | top-20 overlap | all-60 overlap |
| --- | --- | --- | --- | --- |
| top_money | True | 10 | 20 | 59 |
| top_top1 | True | 10 | 20 | 60 |
| top_ev | False | 10 | 19 | 59 |
| top_win | False | 9 | 20 | 60 |

| lineup (CPT + FLEX) | labels | cash pre -> HEAD (d, SE) | top 1% | top 0.1% | first or share | sole first | EV $ |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Jonah Coleman + Wil Lutz, Broncos, Bo Nix, Jaylen Warren, Mason Rudolph | head:top_money:1; head:top_top1:1; pre:top_money:1 | 67.99 -> 67.75 (-0.23, 0.02) | 7.168 -> 7.174 (+0.006, 0.011) | 0.6380 -> 0.6355 (-0.0026, 0.0024) | 0.1507 -> 0.1490 (-0.0017, 0.0017) | 0.00000 -> 0.00000 (+0.00000, 0.00000) | 36.59 -> 36.43 (-0.16, 0.08) |
| Jonah Coleman + Chris Boswell, Broncos, Bo Nix, Jaylen Warren, Mason Rudolph | head:top_money:2; pre:top_money:2; served:top_money:2 | 66.95 -> 66.63 (-0.32, 0.03) | 2.679 -> 2.694 (+0.015, 0.007) | 0.1608 -> 0.1609 (+0.0001, 0.0001) | 0.0408 -> 0.0408 (+0.0000, 0.0000) | 0.00000 -> 0.00000 (+0.00000, 0.00000) | 26.26 -> 26.16 (-0.10, 0.01) |
| Bo Nix + Wil Lutz, Broncos, Jonah Coleman, Jaylen Warren, Mason Rudolph | head:top_money:3; pre:top_money:3; served:top_money:3 | 64.96 -> 64.75 (-0.21, 0.02) | 3.313 -> 3.302 (-0.010, 0.006) | 0.1722 -> 0.1731 (+0.0009, 0.0019) | 0.0164 -> 0.0147 (-0.0017, 0.0017) | 0.00000 -> 0.00000 (-0.00000, 0.00000) | 26.49 -> 26.34 (-0.16, 0.09) |
| Bo Nix + Chris Boswell, Broncos, Jonah Coleman, Jaylen Warren, Mason Rudolph | head:top_money:4; pre:top_money:4; served:top_money:4 | 61.73 -> 61.46 (-0.27, 0.03) | 1.236 -> 1.236 (-0.000, 0.004) | 0.0561 -> 0.0572 (+0.0010, 0.0010) | 0.0070 -> 0.0070 (+0.0000, 0.0000) | 0.00000 -> 0.00000 (+0.00000, 0.00000) | 21.85 -> 21.76 (-0.09, 0.01) |
| Jonah Coleman + Broncos, Bo Nix, Jaylen Warren, Pat Bryant, Mason Rudolph | head:top_money:5; pre:top_money:5; served:top_money:5 | 59.04 -> 58.88 (-0.16, 0.02) | 3.639 -> 3.628 (-0.011, 0.007) | 0.3286 -> 0.3299 (+0.0013, 0.0017) | 0.0618 -> 0.0618 (+0.0000, 0.0000) | 0.00000 -> 0.00000 (+0.00000, 0.00000) | 26.46 -> 26.40 (-0.06, 0.01) |
| Jonah Coleman + Wil Lutz, Broncos, Bo Nix, Jaylen Waddle, Mason Rudolph | head:top_money:20; head:top_top1:5; pre:top_money:20 | 50.59 -> 50.45 (-0.14, 0.02) | 6.385 -> 6.352 (-0.033, 0.009) | 0.8073 -> 0.7991 (-0.0082, 0.0038) | 0.0914 -> 0.0897 (-0.0017, 0.0017) | 0.00000 -> 0.00000 (-0.00000, 0.00000) | 38.36 -> 38.13 (-0.23, 0.12) |
| Jonah Coleman + Wil Lutz, Broncos, Bo Nix, Rico Dowdle, Jaylen Waddle | head:top_top1:2; pre:top_top1:2; served:top_top1:1 | 43.79 -> 43.73 (-0.05, 0.02) | 7.016 -> 6.985 (-0.031, 0.010) | 1.5199 -> 1.5167 (-0.0032, 0.0035) | 0.2244 -> 0.2244 (-0.0000, 0.0024) | 0.00000 -> 0.00000 (+0.00000, 0.00000) | 136.44 -> 136.46 (+0.02, 0.81) |
| Jonah Coleman + Wil Lutz, Broncos, Bo Nix, Jaylen Warren, Pat Bryant | head:top_top1:3; pre:top_top1:3; served:top_top1:3 | 47.43 -> 47.28 (-0.15, 0.02) | 6.738 -> 6.718 (-0.019, 0.011) | 1.5001 -> 1.4912 (-0.0089, 0.0042) | 0.3075 -> 0.3042 (-0.0033, 0.0024) | 0.00000 -> 0.00000 (+0.00000, 0.00000) | 101.29 -> 100.99 (-0.31, 0.43) |
| Jaylen Warren + Chris Boswell, Bo Nix, Rico Dowdle, Jaylen Waddle, Mason Rudolph | head:top_top1:4; head:top_ev:3; pre:top_top1:4 | 38.84 -> 38.96 (+0.12, 0.01) | 6.535 -> 6.555 (+0.020, 0.005) | 1.4361 -> 1.4388 (+0.0027, 0.0014) | 0.0886 -> 0.0886 (+0.0000, 0.0000) | 0.00685 -> 0.00685 (+0.00000, 0.00000) | 203.66 -> 203.85 (+0.18, 0.05) |
| Jaylen Warren + Chris Boswell, Steelers, Rico Dowdle, Jaylen Waddle, Mason Rudolph | head:top_ev:1; head:top_win:1; pre:top_ev:1 | 22.05 -> 22.00 (-0.05, 0.01) | 3.797 -> 3.780 (-0.016, 0.007) | 0.9438 -> 0.9403 (-0.0034, 0.0032) | 0.0754 -> 0.0753 (-0.0001, 0.0002) | 0.05413 -> 0.05404 (-0.00009, 0.00014) | 260.32 -> 258.86 (-1.45, 1.15) |
| Jaylen Warren + Chris Boswell, Steelers, Jaylen Waddle, DK Metcalf, Mason Rudolph | head:top_ev:7; head:top_win:3; pre:top_ev:7 | 17.54 -> 17.50 (-0.04, 0.01) | 2.612 -> 2.604 (-0.008, 0.007) | 0.6357 -> 0.6313 (-0.0044, 0.0037) | 0.0564 -> 0.0564 (+0.0000, 0.0003) | 0.04509 -> 0.04510 (+0.00001, 0.00019) | 187.29 -> 186.42 (-0.87, 1.31) |
| Jaylen Warren + Chris Boswell, Steelers, Jaylen Waddle, Germie Bernard, Mason Rudolph | head:top_ev:9; head:top_win:2; pre:top_ev:9 | 16.18 -> 16.12 (-0.06, 0.01) | 2.217 -> 2.212 (-0.005, 0.005) | 0.5269 -> 0.5233 (-0.0036, 0.0020) | 0.0554 -> 0.0564 (+0.0010, 0.0011) | 0.04630 -> 0.04725 (+0.00094, 0.00091) | 181.92 -> 183.01 (+1.08, 3.05) |
| Jonah Coleman + Broncos, Steelers, Bo Nix, Jaylen Warren, Mason Rudolph | head:chalk; pre:chalk; served:chalk | 53.70 -> 53.21 (-0.49, 0.03) | 1.218 -> 1.105 (-0.113, 0.013) | 0.0590 -> 0.0521 (-0.0069, 0.0032) | 0.0133 -> 0.0083 (-0.0050, 0.0029) | 0.00000 -> 0.00000 (+0.00000, 0.00000) | 18.89 -> 18.39 (-0.50, 0.12) |

### 5.5 Optimal lineup

The best-projected lineup we may enter (80.8, Coleman CPT + Lutz, Broncos, Nix, Warren, Rudolph -- also the
money and top-1% leader) is the same in both builds: projections and rules are inputs. The money and top-1%
lists keep their top 20 in order; the EV list keeps its top 10 (order shuffled) and 19 of 20; the
first-place list 9 of its top 10 and all 20 of its top 20. The share of worlds in which a lineup is the best
of the whole universe (`opt_pct`) moves only for lineups holding a defense, by the defenses' own shift.

## 6. The D/ST correction, isolated on the same games

`research/den_pit_audit/pool_boxes.py` replays HEAD's pool call and keeps both teams' box scores for all
60,000 games (its every player array equals the HEAD build's, bit for bit). Scoring those SAME boxes:

| defense | old rule mean | new rule mean | diff (paired SE) | SD old / new | worlds moved | direction | floor / ceiling |
|---|---|---|---|---|---|---|---|
| Broncos (vs PIT's offense) | 7.4255 | 7.3383 | -0.0872 (0.0020) | 4.898 / 4.889 | 2,370 (3.95%) | all down | 2 / 14 both |
| Steelers (vs DEN's offense) | 5.2545 | 5.1787 | -0.0757 (0.0018) | 4.564 / 4.560 | 2,313 (3.86%) | all down | 0 / 11 both |

* Old rule: points allowed = opponent points - every return score and its try (`ret_pts`) - safeties.
  New rule: opponent points - 6 x the opponent defense's pick-sixes - safeties. A kickoff or punt return TD
  against the defense (and every try) now counts as allowed, so the change can only push a world into a
  worse points-allowed tier: every moved world moved down. In these games DEN scored 1,455 kick/punt
  return TDs and 4,228 pick-sixes, PIT 1,449 and 4,019 (about 2.4% and 7% of team-games).
* The old rule on HEAD's boxes reproduces the pre-sprint build's defense arrays exactly, and the new rule
  HEAD's, so this rule is the entire difference between the builds. The owner's figure for the league
  (-0.08 a game, 3.9% of worlds) holds here.
* Board level: it explains all of section 5 (no other array moved). Size: about a tenth of a point a
  defense, against a defense's game-to-game SD of ~4.6-4.9 points.

## 7. The served boards before and after, and the rebuild (descriptive)

Inputs moved between the night build and the refresh: Tyler Badie (DEN RB2, questionable) joined the pool
at $200 and 4.4-4.5 points, which multiplied the cheap legal lineups; Nate Adkins left the field to hold it
under 900,000 lineups; Bernard 7.6 -> 5.8, Metcalf 7.5 -> 6.7, Pittman 3.8 -> 4.8 (WR5 -> WR4); the lineups
we may enter rose 32,771 -> 47,644. The HEAD rebuild's pool equals the refresh's (790,222 lineups, 47,644
ours, the same four field-only players); its team level and worlds differ by the seed. `proj` below is
Sleeper's projection (what the field's weights and the lists' `proj` read), not the simulated mean (5.3).

| player | pos | night proj / floor / ceil / optCPT | refresh proj / floor / ceil / optCPT | HEAD rebuild proj / floor / ceil / optCPT |
| --- | --- | --- | --- | --- |
| Jonah Coleman | RB | 18.0 / 6.7 / 30.2 / 29.65 | 17.9 / 6.7 / 30.7 / 31.48 | 17.8 / 6.6 / 30.4 / 30.05 |
| Bo Nix | QB | 14.4 / 6.7 / 25.9 / 16.25 | 14.3 / 6.8 / 25.9 / 16.19 | 14.2 / 6.9 / 26.0 / 16.64 |
| Jaylen Warren | RB | 12.4 / 5.0 / 23.8 / 14.62 | 12.6 / 5.1 / 24.3 / 15.68 | 12.6 / 5.2 / 24.6 / 16.41 |
| Mason Rudolph | QB | 12.0 / 4.7 / 21.1 / 7.91 | 11.7 / 4.3 / 19.8 / 6.3 | 11.7 / 4.3 / 20.1 / 6.62 |
| Jaylen Waddle | WR | 9.7 / 2.3 / 18.5 / 4.92 | 9.7 / 2.3 / 18.7 / 5.09 | 9.7 / 2.3 / 18.7 / 5.25 |
| Broncos | DST | 8.4 / 2.0 / 14.0 / 3.04 | 8.4 / 2.0 / 14.0 / 2.71 | 8.4 / 2.0 / 14.0 / 2.8 |
| Rico Dowdle | RB | 7.7 / 2.4 / 17.4 / 6.0 | 7.8 / 2.4 / 17.7 / 6.31 | 7.8 / 2.4 / 17.7 / 6.47 |
| Steelers | DST | 7.4 / 0.0 / 11.0 / 1.49 | 7.4 / 0.0 / 11.0 / 1.36 | 7.4 / 0.0 / 11.0 / 1.45 |
| Wil Lutz | K | 7.2 / 3.0 / 14.0 / 2.2 | 7.2 / 3.0 / 14.0 / 2.13 | 7.2 / 3.0 / 14.0 / 2.1 |
| Pat Bryant | WR | 7.1 / 1.2 / 14.5 / 2.13 | 7.1 / 1.2 / 14.7 / 2.31 | 7.1 / 1.1 / 14.7 / 2.26 |
| Chris Boswell | K | 7.1 / 2.0 / 14.0 / 1.57 | 7.1 / 2.0 / 13.0 / 1.55 | 7.1 / 2.0 / 14.0 / 1.5 |
| Courtland Sutton | WR | 6.8 / 0.0 / 13.9 / 1.85 | 6.8 / 0.4 / 14.2 / 2.0 | 6.8 / 0.0 / 14.0 / 1.75 |
| DK Metcalf | WR | 7.5 / 1.4 / 14.7 / 2.31 | 6.7 / 0.3 / 13.3 / 1.51 | 6.7 / 0.4 / 13.4 / 1.56 |
| Pat Freiermuth | TE | 7.0 / 1.2 / 13.7 / 1.72 | 6.7 / 1.2 / 13.0 / 1.24 | 6.7 / 1.1 / 13.1 / 1.28 |
| Germie Bernard | WR | 7.6 / 1.7 / 14.4 / 1.87 | 5.8 / 1.3 / 11.6 / 0.77 | 5.8 / 1.3 / 11.6 / 0.72 |
| Evan Engram | TE | 5.6 / 0.0 / 11.9 / 0.78 | 5.6 / 0.0 / 12.1 / 0.92 | 5.6 / 0.0 / 12.0 / 0.77 |
| Michael Pittman Jr. | WR | 3.8 / 0.0 / 8.6 / 0.19 | 4.8 / 0.0 / 10.3 / 0.47 | 4.8 / 0.0 / 10.3 / 0.47 |
| Darnell Washington | TE | 4.9 / 0.0 / 10.8 / 0.64 | 4.7 / 0.0 / 10.1 / 0.51 | 4.7 / 0.0 / 10.3 / 0.51 |
| Tyler Badie | RB | - | 4.5 / 0.7 / 10.5 / 0.64 | 4.4 / 0.7 / 10.5 / 0.62 |
| Roman Wilson | WR | 4.4 / 0.0 / 10.0 / 0.52 | 4.2 / 0.0 / 9.4 / 0.38 | 4.2 / 0.0 / 9.4 / 0.39 |
| Marvin Mims Jr. | WR | 4.0 / 0.0 / 9.1 / 0.22 | 4.0 / 0.0 / 9.2 / 0.3 | 4.0 / 0.0 / 9.2 / 0.24 |
| Troy Franklin | WR | 2.5 / 0.0 / 6.0 / 0.04 | 2.5 / 0.0 / 6.2 / 0.06 | 2.5 / 0.0 / 6.2 / 0.05 |
| Adam Trautman | TE | 2.3 / 0.0 / 6.0 / 0.03 | 2.4 / 0.0 / 6.3 / 0.04 | 2.3 / 0.0 / 6.2 / 0.05 |
| Max Hurleman | WR | 1.9 / 0.0 / 5.0 / 0.04 | 2.3 / 0.0 / 5.8 / 0.05 | 2.3 / 0.0 / 5.9 / 0.03 |
| Nate Adkins | TE | 1.3 / 0.0 / 3.8 / 0.01 | - | - |

The money leader is the same lineup in all three (Coleman CPT + Lutz, Broncos, Nix, Warren, Rudolph):
night cash 68.0 / top 1% 7.5 / first-or-share 0.124%, refresh 68.2 / 7.09 / 0.117%, HEAD rebuild 67.8 /
7.17 / 0.149%. The top-1% leader at the refresh is Coleman CPT + Lutz, Broncos, Nix, Dowdle, Waddle (7.21%,
11.7 expected copies), where the night board had the money leader. These moves are inputs and seed, not
code.

## 8. Caching and GPU on this slate

* **Scoring pass:** CPU by design. `run_vs_field` sends a pass to the GPU only for a uniform-weight field
  (`np.all(wf == wf[0])`); a showdown field is the weighted universe, so the GPU path is never taken, and
  `VIGIL_TOURNEY_GPU` is set only after the PC's own CLASSIC bench. Neither served board stamps
  `timings.score_device`.
* **Worlds cache:** a board does not stamp whether its pool came from the cache, so whether the PC read
  cached worlds for this slate cannot be shown from the server. The cache key is the call, the simulator's
  code and every input, so an unseeded rebuild on UNCHANGED inputs (or a picked build) reads the same games;
  the refresh's inputs had moved (Badie, Sleeper's projections), so its worlds were necessarily fresh -- its
  team level (DEN 21.36 / PIT 16.93) differs from the night's (20.93 / 17.38) accordingly.
* **Evaluator:** both served boards carry `eval_artifact`, so "Evaluate your own lineup" works on this slate
  (40,000 final worlds of its own, GPU where CuPy is installed). Whether the owner evaluated a lineup is not
  in what the server publishes to sim-history.
* **Ledger and bench:** the 7-day ledger snapshot (19:32 UTC) holds no `TOURN-gpu`, `TOURN-gpu-mismatch`,
  `EVAL-gpu`, `EVAL-gpu-mismatch`, `POOLCACHE` or `CKPT` row (its codes: NFL-DRIFT, KAL-candles,
  MXR-grade-fetch, MXR-params, CRYPTO-*). The PC's benches: a real-slate evaluator + tournament-kernel bench at
  21:58 UTC 2026-11-26 (commit 5696b7d), every bucket and threshold equal; the deep profile probe at 09:57 UTC
  (classic dg 157066); a synthetic bench at 10:05 UTC whose `score` configuration refused ("no classic
  slate's evaluator artifact yet"). None names this slate's worlds.

## 9. The owner's added items

### 9.1 The depth gate on the field-only players

Which players: on the night board five players were "field only" -- in the public's lineups, never in
ours -- all by the depth chart's RANK (`nfl_dfs._apply_depth` keeps QB1, RB1-2, WR1-3, TE1-2;
`dfs_tourney._field_extras` keeps the best eight gated players projected 1.0+ in the field): Marvin Mims Jr.
(DEN WR4, $3,000, 4.0), Troy Franklin (DEN WR5, $3,400, 2.5), Michael Pittman Jr. (PIT WR5, $6,800, 3.8),
Max Hurleman (PIT WR6, $2,000, 1.9) and Nate Adkins (DEN TE3, $1,600, 1.3). By the refresh Adkins had left
the field altogether (Badie's $200 salary multiplied the legal lineups and the universe is held under
900,000 by dropping the least-projected extra), so the refresh and the HEAD rebuild gate FOUR: Pittman (now
WR4, 4.8), Mims (WR4, 4.0), Franklin (WR5, 2.5), Hurleman (WR5, 2.3). Measured on the HEAD rebuild's 60,000
worlds with the board's own arithmetic (`universe.py`, which reproduces every board figure to 1e-13), the
other house rules unchanged, nothing changed in production:

| | gate on (the board) | gate lifted for the four |
|---|---|---|
| lineups we may enter | 47,644 | 120,818 |
| worlds whose best lineup of the whole universe we may enter | 45.2% (SE 0.2) | 58.0% (SE 0.2) |
| worlds whose universe-best holds a gated player | 22.5% (13,504) | -- |
| ... blocked by the gate ALONE (the opt share it eliminates) | 12.9% (7,735 worlds, SE 0.14) | 0 |
| ... blocked by our other rules (no K/DST captain, punt roles, caps) | 42.0% | 42.0% |
| worlds where OUR best lineup scores more with the gate lifted | -- | 21.4%, by 3.61 points when it does (0.77 a world on average) |
| best projected lineup | 80.8 | 80.8 |
| best top-1% rate on the board | 7.174% | 7.174% (the same lineup) |
| best cash rate | 67.75% | 67.75% (the same lineup) |
| gated lineups in the top 60 by top 1% / by cash | -- | 0 / 4 |

Best-lineup share by gated player: Pittman 9.4% of worlds, Mims 8.2%, Franklin 3.1%, Hurleman 3.1%. So the
gate removes real optimal-lineup coverage -- in about one world in eight the single best lineup is one only
the gate keeps us from entering -- but none of the board's ranked figures: those receivers win some worlds
outright on cheap boom games, and no lineup holding one reaches the top of the top-1% list, and four reach the
bottom of the cash top 60. The top 10 of both lists are the same lineups with the gate on or off. Descriptive;
whether the gate should change is a test for fresh slates, not a reading of one.

### 9.2 Rare outcomes: what a displayed 0.000% means

What the table prints: the showdown list shows top 1% and first place as `win_pct` (first OUTRIGHT, three
decimals); top 0.1% is computed (`run_subset`'s `top01`) and printed only on classic boards. All figures
below are the HEAD rebuild's (the served boards' differ only by inputs and seed).

* **The money / top-1% leader's "0.000%" is duplication, not impossibility or a sampling gap.** Coleman CPT +
  Lutz, Broncos, Nix, Warren, Rudolph was the single best lineup of the whole universe in **89 of 60,000
  worlds** (0.148%; exact 95% CI 0.12-0.18%), and its first-or-share figure is 0.149%. But in those worlds
  ~119 field entries hold the identical lineup (110 expected copies), so first OUTRIGHT is ~0 by construction
  and its prize is split ~120 ways. Of the 67 tracked HEAD lineups, 28 print 0.000% outright and every one of
  them was the universe's best in at least one world; each zero comes from its own copies.
* **The first-place leader rests on few worlds.** Warren CPT + Boswell, Steelers, Dowdle, Waddle, Rudolph
  (0.054% outright, 0.3 expected copies) was the universe's best in **13 worlds** (47 worlds with fewer than
  one entry expected above it); the exact 95% upper bound on the 13/60,000 rate is 0.037%. The two behind it:
  11 and 7 worlds. A figure built on 7-50 worlds carries a relative SE of roughly 15-40%, before any model
  error.
* **Top 0.1%** (58 places of 58,823): the leader's 0.636% rests on 378 worlds in which fewer than 58 entries
  are expected above it; the top-1% runner-up (Coleman + Lutz, Broncos, Nix, Dowdle, Waddle) 1.52% on 913.
* **A zero with zero worlds behind it:** at 60,000 worlds the exact (Clopper-Pearson) 95% upper bound for 0
  successes is 0.0061% (two-sided; 0.0050% one-sided). "0.000%" of that kind means "below about 1 in
  16,000", not impossible. None of the tracked lineups was in that case.
* **What the rules say:** `nfl_dfs.resolution` (a line needs ten sampled lineups above it; ROI needs <= 10% of
  the prize money above the deepest line read) governs the lineup SHEET's sampled-field contest sim; the
  showdown board's field is the exhaustive universe, so no field-sampling rule applies. `sd_money_gate` marks
  every money column (win_pct, win_any_pct, ev, ev_notie, roi_pct, expected_copies) NOT authoritative on
  every showdown board -- the field model is uncalibrated and the joint model unvalidated -- and says "rank on
  top 1% and top 0.1%". Neither rule counts the WORLDS behind a first-place figure; the board carries
  `opt_worlds` per lineup but the table does not print it. Printing it (and a bound under ~30 worlds) beside
  first place is a display change for the owner's call, not built here.

### 9.3 Weather

The board does not adjust for weather, by design: the stage-1 weather test (wind, rain, cold against
Sleeper's team lines and the field-goal curve, 2025) FAILED, so forecasts show on the DFS tab as information
only (`research/reports/nfl_weather.md`); a wind-only stage 2 is read once after 2026 week 18. The 17 mph
wind and 92% precipitation forecast therefore play no part in any figure here, and the board's running-back
lean is not a weather effect. Where it comes from is section 9.4.

### 9.4 The game scripts behind Coleman CPT + Lutz + Broncos

Not weather (9.3). The rebuild's worlds lean Denver: the anchor's targets are DEN 21.3 v PIT 17.2 (0.7 x the
drive model's 19.6 v 17.8 + 0.3 x the deep engine's raw 25.2 v 15.7), and Sleeper's usage gives Coleman
17-18 carries. Weighting each world by the lineup's own per-world top-1% value (the board's quantity;
effective 4,305 worlds, 7.17%), its top-1% worlds against all worlds:

| | all worlds | the lineup's top-1% worlds |
|---|---|---|
| DEN points / PIT points | 21.4 / 17.2 | 25.4 / 10.6 |
| margin DEN - PIT | +4.2 | +14.8 |
| total | 38.6 | 35.9 |
| DEN rushing yards / passing yards | 127 / 205 | 161 / 180 |
| DEN rushing TDs / passing TDs / FGs made | 0.93 / 1.25 / 1.83 | 1.34 / 0.87 / 2.87 |
| PIT giveaways / sacks taken | 0.99 / 2.5 | 1.46 / 3.1 |
| Coleman / Lutz / Broncos DST | 17.4 / 8.6 / 7.3 | 25.8 / 12.7 / 11.6 |
| Nix / Warren / Rudolph | 15.8 / 13.7 / 11.6 | 13.3 / 13.4 / 9.0 |

| game script | share of all worlds | share of the top-1% worlds |
|---|---|---|
| DEN by 8+ | 36.5% | 76.8% |
| DEN by 1-7 | 27.1% | 21.0% |
| PIT wins (by 1-7 / 8+) | 20.8% / 15.3% | 2.2% / 0.1% |
| total 34 or less / 35-44 / 45+ | 40.3% / 29.4% / 30.3% | 49.2% / 29.3% / 21.5% |
| DEN rushing TDs 0 / 1 / 2 / 3 | 38.3% / 37.8% / 17.7% / 5.0% | 19.6% / 41.5% / 26.8% / 9.4% |

The pattern wins when Denver controls a low-scoring game on the ground: Coleman's rushing touchdowns and
yards, drives that stall into Lutz field goals, and a Pittsburgh offense held near ten points with extra sacks
and turnovers for the Broncos D/ST. Nix and the Pittsburgh pieces are filler in those worlds (their points
fall or hold). It is not a shootout lineup: totals of 45+ are under-represented. That is a property of the
engine's worlds given the inputs; nothing here says the real game will go that way.

## 10. R1-R5: an experimental comparison (NOT validated, nothing deployed)

Every coaching engine's registered read is in the future (R1 on/after 2027-01-06; R2-H1 after it; R3, R4 and
R5's primary on 2026 weeks 13-18 / 2027 seasons, read 2028-01-13; R5's 2028 replication 2029-01-16). This
game is week 12, outside every sealed window, and its result was not read. R5 carries R1's clock and
situational calls, R3/R5's quarterback pull, R4's personnel calls after a pull, and R5's second unit; nothing
here is evidence that it predicts better.

**Two arms, both on this board's inputs.** (a) The game: `research/den_pit_audit/r5_compare.py` plays 20,000
games with production's `nfl_deep_sim.play_game` and 20,000 with `nfl_coach_sim_r5.play_game`, both on the
anchored profiles the HEAD board's worlds were played on, at production's form tilt, home edge and
passing environment. R5 plays there on its OWN registered tables, whose shared base (yards, completions,
kicks) is fitted on nflverse 2016-2023, not the deep engine's ESPN 2023-2026 tables; a second run (a2) puts
R5's coaching tables on production's base, as `research/nfl_coach_dfs.py` sets R1 up, to separate the two.
(b) The DFS link: `r5_dfs.py` runs `nfl_dfs_engine.simulate_game` itself, 20,000 worlds an arm, with the game
swapped to R5 inside a `with` block (the `nfl_coach_dfs` mechanism): each arm is anchored on its OWN engine
to the same blended targets and dealt by production's Sleeper-usage deal, so R5's box scores reach DFS points
but R5's own player deal does not. The arms draw their random numbers differently, so **no difference
below is paired: every SE is unpaired**.

**(a) The game, 20,000 a side** (`research/data/den_pit_audit/r5_report.json`; unpaired SE):

| | production | R5 | R5 - production (SE) |
|---|---|---|---|
| PIT points: mean, SD, 10/25/50/75/90% | 17.2, 8.8, 6/10/17/23/29 | 14.7, 8.4, 3/9/14/20/27 | -2.46 (0.09) |
| DEN points | 21.2, 9.4, 10/14/20/27/34 | 19.0, 9.0, 7/13/19/24/31 | -2.20 (0.09) |
| total | 38.4, 13.3 | 33.7, 12.4 | -4.66 (0.13) |
| margin PIT - DEN: mean, quantiles | -4.0, -20/-12/-4/4/11 | -4.3, the same quantiles | -0.27 (0.12) |
| final margin within 3 / within 7 | 21.3% / 48.7% | 21.6% / 47.5% | +0.3 (0.4) / -1.1 (0.5) points |
| margin 17+ / 24+ | 21.1% / 7.7% | 20.8% / 7.0% | -0.3 (0.4) / -0.7 (0.3) |
| ties | 0.5% | 1.5% | +1.0 (0.1) |
| plays PIT / DEN | 60.6 / 62.0 | 59.5 / 61.1 | -1.14 (0.08) / -0.92 (0.08) |
| pass rate (dropbacks per non-kneel snap) PIT / DEN | 0.539 / 0.522 | 0.543 / 0.517 | +0.004 / -0.005 (0.001) |
| time of possession, minutes, PIT / DEN | 27.9 / 29.2 | 27.1 / 28.2 | -0.78 (0.05) / -1.03 (0.05) |
| snaps inside 2:00 of Q2, PIT / DEN | 3.26 / 3.31 | 4.31 / 4.38 | +1.05 / +1.07 (0.02) |
| snaps inside 2:00 of Q4, PIT / DEN | 2.80 / 2.66 | 3.46 / 3.21 | +0.65 / +0.55 (0.02) |
| Q4 carries PIT / DEN | 6.76 / 7.67 | 6.53 / 7.89 | -0.22 / +0.22 (0.03) |
| Q4 rushing yards PIT / DEN | 28.8 / 33.1 | 26.7 / 32.8 | -2.1 (0.2) / -0.4 (0.2) |
| dropbacks in the last 5:00, PIT / DEN | 4.02 / 3.12 | 4.31 / 3.06 | +0.29 / -0.06 (0.03) |
| PIT D/ST DK points: mean, SD, 10/50/90% | 5.2, 4.5, 0/4/11 | 6.1, 4.9, 1/5/13 | +0.93 (0.05) |
| DEN D/ST | 7.3, 4.9, 2/7/14 | 8.5, 5.2, 2/8/16 | +1.21 (0.05) |
| PIT K / DEN K | 7.7 / 8.6 | 6.6 / 7.8 | -1.13 / -0.83 (0.04) |
| starting QB pulled, PIT / DEN | -- | 14.7% / 15.8% (SE 0.3) | strategic 1,914 / 2,331, injury 1,020 / 830 of 20,000 |
| when (pulls by quarter 1/2/3/4/OT), PIT | -- | 276 / 357 / 457 / 1,830 / 14; median 4:12 left in the period, margin -10 at the pull |
| when, DEN | -- | 272 / 263 / 417 / 2,196 / 13; median 3:48 left, margin +14 |

**(a2) The same, with R5's coaching tables on production's base tables** (10,000 a side,
`research/data/den_pit_audit/r5_game_deepbase.json`; `r5_compare.py --base deep`):

| | production | R5 on production's base | difference (SE) |
|---|---|---|---|
| PIT / DEN / total points | 17.12 / 21.33 / 38.45 | 17.20 / 21.46 / 38.66 | +0.08 (0.12) / +0.13 (0.13) / +0.21 (0.19) |
| SD of the total | 13.36 | 12.78 | |
| margin PIT - DEN | -4.22 | -4.26 | -0.04 (0.18) |
| within 3 / within 7 / 17+ / 24+ / tie | 20.9 / 48.7 / 21.3 / 8.3 / 0.4% | 20.9 / 46.7 / 21.8 / 7.5 / 1.2% | +0.1 (0.6) / -2.0 (0.7) / +0.5 (0.6) / -0.7 (0.4) / +0.8 (0.1) |
| plays PIT / DEN | 60.59 / 62.20 | 60.52 / 61.98 | -0.07 (0.11) / -0.22 (0.11) |
| possession minutes PIT / DEN | 27.81 / 29.27 | 27.14 / 28.30 | -0.67 / -0.97 (0.06) |
| snaps inside 2:00 of Q2, PIT / DEN | 3.25 / 3.35 | 4.37 / 4.42 | +1.11 / +1.07 (0.03) |
| snaps inside 2:00 of Q4, PIT / DEN | 2.81 / 2.68 | 3.38 / 3.19 | +0.58 / +0.51 (0.04) |
| Q4 carries PIT / DEN | 6.74 / 7.72 | 6.62 / 8.01 | -0.12 / +0.29 (0.05) |
| Q4 rushing yards PIT / DEN | 28.9 / 33.5 | 28.7 / 35.2 | -0.3 / +1.8 (0.3) |
| dropbacks in the last 5:00, PIT / DEN | 4.08 / 3.13 | 4.23 / 3.00 | +0.15 / -0.13 (0.05) |
| D/ST PIT / DEN | 5.18 / 7.39 | 5.09 / 7.26 | -0.08 / -0.13 (0.07) |
| K PIT / DEN | 7.64 / 8.65 | 7.91 / 8.88 | +0.27 / +0.23 (0.06) |
| starting QB pulled, PIT / DEN | -- | 14.9% / 15.5% (SE 0.4); strategic 1,004 / 1,153, injury 482 / 400 of 10,000 | median margin at the pull -11 / +15 |

So the coaching logic itself changes the game's SHAPE, not its level: many more snaps inside each
two-minute warning (R1's repaired half-ends; R1's development read
production at 6.6 / 3.8 Q2 / Q4 snaps inside 2:00 against 9.0 / 5.6 real, both teams), the leader running
late, and three times the ties (R1's overtime). The possession row is a measurement convention, not a
finding: it credits the clock from each snap to the next logged event, and R1 logs timeouts and spikes as
events of their own, so part of its -0.7 / -1.0 minutes is clock the convention stops crediting. The -4.7 points, the better D/ST and the worse kickers of arm (a)
come from R5's own base tables (nflverse 2016-2023, a lower-scoring era than the deep engine's 2023-2026
fit), not from the coaching. Its pull rate (~15% of team-games here, about two-thirds strategic) follows
R3/R5's learned hazard: Denver's pulls come leading by ~14-15 late, Pittsburgh's TRAILING by ~10-11 (the
"trailing teams protecting the quarterback" pattern R4 found real).

R5's own deal (R2's situational usage + R5's second unit), Sleeper's usage lines in R2's format, against
production's DFS deal on its 60,000 worlds: Coleman 18.85 carries (SE 0.04) v 17.29 and 2.75 targets v
2.85; Badie 3.19 v 3.64 carries; Warren 13.66 v 13.46; Dowdle 9.70 v 9.88; the top targets within 0.1 of
production (Waddle 5.85 v 5.80, Metcalf 5.14 v 5.04, Sutton 4.52 v 4.44, Bernard 4.01 v 4.03). The lead
back's share rises in R5 (late leads; the backup back sits more after a strategic pull).

**(b) The DFS link, 20,000 worlds an arm, each anchored on its own engine to the same targets:**

| player | production mean / 10% / 90% | R5 mean / 10% / 90% | mean diff (SE) | 90% diff (SE) |
|---|---|---|---|---|
| Coleman | 17.30 / 6.6 / 30.2 | 16.79 / 6.3 / 29.6 | -0.51 (0.09) | -0.6 (0.2) |
| Nix | 15.33 / 6.6 / 25.5 | 15.40 / 6.7 / 25.3 | +0.07 (0.08) | -0.1 (0.2) |
| Warren | 13.28 / 5.0 / 24.0 | 13.96 / 5.2 / 24.9 | +0.68 (0.08) | +0.9 (0.2) |
| Rudolph | 11.17 / 4.1 / 19.4 | 11.77 / 4.4 / 20.1 | +0.60 (0.06) | +0.7 (0.2) |
| Waddle | 9.33 / 2.2 / 18.1 | 9.47 / 2.3 / 18.4 | +0.14 (0.07) | +0.3 (0.2) |
| Dowdle | 8.71 / 2.3 / 17.1 | 9.31 / 2.5 / 18.3 | +0.60 (0.07) | +1.2 (0.2) |
| Lutz (K DEN) | 8.64 / 3 / 14 | 8.75 / 3 / 15 | +0.10 (0.04) | +1 (0.5) |
| Broncos D/ST | 7.61 / 2 / 14 | 7.10 / 2 / 14 | -0.51 (0.05) | 0 (0.3) |
| Boswell (K PIT) | 7.57 / 2 / 13 | 7.98 / 2 / 14 | +0.41 (0.04) | +1 (0.5) |
| Steelers D/ST | 5.21 / 0 / 11 | 5.32 / 0 / 12 | +0.11 (0.05) | +1 (0.5) |
| every receiver and tight end | | | within 0.15 | |

**Read this arm with care.** Re-anchoring pulls R5 back to the same targets, so what is left is how the
anchor LANDED: production PIT 16.42 / DEN 21.01, R5 PIT 17.74 / DEN 20.90 (the targets ~17.2 / ~21.3; the
board's own 60,000 worlds landed 17.18 / 21.40 on the same inputs and another seed). The PIT-side gains and
Coleman / Broncos losses follow that 1.3-point PIT level gap, which is the size of the anchor's seed-to-seed
landing noise (section 12), so this arm does not separate R5's game script from anchor noise at this n.

Correlations are level-free and barely move (production -> R5, each SE ~0.007, difference SE ~0.01):
Nix-Waddle 0.338 -> 0.331, Nix-Sutton 0.288 -> 0.271, Nix-Coleman 0.090 -> 0.073, Coleman-Broncos D/ST
0.083 -> 0.098, Coleman-Lutz 0.109 -> 0.114, Coleman-Steelers D/ST -0.336 -> -0.332, Nix-Rudolph 0.201 ->
0.210, Coleman-Warren -0.100 -> -0.122, Rudolph-Metcalf 0.313 -> 0.314, Rudolph-Warren 0.169 -> 0.145,
Warren-Broncos D/ST -0.283 -> -0.305, Nix-Steelers D/ST -0.460 -> -0.453, Rudolph-Broncos D/ST -0.491 ->
-0.474, Lutz-Broncos D/ST 0.154 -> 0.169, the two D/STs -0.149 -> -0.157. The largest moves are 2.4 SE
(Rudolph-Warren, Coleman-Warren).

Captain ranking implied (1.5 x mean / 1.5 x 90th / 1.5 x 99th percentile): production Coleman, Nix,
Warren, Rudolph, Waddle, Dowdle by mean and 90th; R5 the same order by mean and 90th, and by the 99th
percentile Coleman, Warren, Nix, Dowdle, Waddle, Rudolph (production: Coleman, Nix, Warren, Waddle, Rudolph,
Dowdle). The coaching layer would not change who heads this board; it shades Pittsburgh's backs up and
Denver's lead back down only through a level difference this arm cannot attribute.

**Verdict:** EXPERIMENTAL. R5 changes the game's tempo and the end of each half visibly; through the DFS
engine's anchor and deal it changes no DFS figure by more than the anchor's own noise and no correlation by
more than ~0.025. Nothing here is evidence that R5 is better; its registered reads decide that.

## 11. Active vs research-only

| piece | status on this board |
|---|---|
| engine-deep worlds, deep weight 0.30, ENV_SD, attempt anchor, usage 0.3, fitted allocation noise | active |
| D/ST points-allowed rule (SIM_MODEL_VERSION 2) | active on both served builds |
| exhaustive universe, softmax field, `_field_extras` (field-only players), 900,000 cap | active |
| conservation fixes (TD allocator, two-QB split) | active; no effect on this slate |
| worlds cache, checkpoints, frozen inputs, compact pools, build profiles | active on the PC; numbers unchanged by construction; profiles and frozen inputs classic only |
| lineup evaluator (+ GPU lane) | available (artifact on both boards); opt-in |
| GPU tournament scoring | classic only; never this board |
| portfolio caps, roster rules, player picker | only on the owner's picked builds |
| matchup model in DFS, weather, Kalshi props, slot allocation, classic field second pass, duplication mixtures | research / not applied |
| coaching engines R1-R6 | research only, registered reads 2027-2029 |
| C3 deep-anchor spread (1.21x) | reported, not fixed |
| money columns (first place, EV, ROI, copies) | shown, not authoritative (`sd_money_gate`) |

## 12. Limitations

* The rebuilds read inputs at 16:38 UTC; the served boards read theirs at 22:55 UTC (night) and 18:11 UTC
  (refresh). The served-vs-rebuild comparison is descriptive.
* Production boards are unseeded. Even at fixed inputs the anchor's 800-game measuring passes land the team
  level a few tenths of a point from its target, differently per seed (served: PIT 17.38 vs 17.48 night,
  16.93 vs 17.33 refresh; the R5 DFS arm's production run 16.42 vs the board's 17.18 at the same inputs).
* The controlled comparison holds the code fixed except the sprint's; it cannot say which board is closer to
  the truth -- only the live record can, and the money columns stay non-authoritative.
* The R5 arms are experimental and unvalidated; arm (a) mixes R5's coaching with its own base tables (arm a2
  separates them); the DFS arm's level differences sit inside the anchor's seed noise (section 10); R5's own
  player deal is shown only in the game-level arm.
* The rebuild ran here on 4 cores (about 30 minutes a build in parallel), not on the PC; that changes timings,
  never figures (the seeded pool is the same draw for draw on any process count).

## 13. Files

* `research/den_pit_audit/netreplay.py` (record / replay), `common.py`, `build_board.py`, `pool_boxes.py`,
  `universe.py`, `make_track.py`, `compare.py`, `analyze_universe.py`, `r5_compare.py`, `r5_dfs.py`,
  `r5_report.py`, `tables.py`, `drive_probe.py`.
* `research/data/den_pit_audit/compare.json`, `universe.json`, `r5_report.json` (arms a and b),
  `r5_game_deepbase.json` (arm a2).
* `tests/guards_auto_netreplay.py` (RPL1-RPL4).
* The recorded HTTP answers, the build captures (X, universe, weights) and the box scores stay in the
  session's scratch space (tens of MB; they include DraftKings' and Sleeper's raw responses), not in git.
