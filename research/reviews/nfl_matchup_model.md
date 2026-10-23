# Fable review: the NFL matchup model (pre-registered validation 2025, one-shot test 2026 weeks 1-6)

Reviewer: a fresh Fable instance with no stake in the build. 2026-10-23, ~14:15-14:45 UTC.
Protocol: `research/reports/matchup_agreement_log.md`. The data foundation (`nfl_matchup_data.py`,
`nfl_matchup_profiles.py`, `research/nfl_matchup_dataset.py`) was taken as agreed
(`research/reviews/nfl_matchup_data.md`, re-check R.1) and is questioned here only where the model
reads it. Nothing in the repo was edited; this file is the only write. `test_lock.json` was never
touched and the official `validate` / `test` commands were never run. Every recomputation ran on
scratch copies under `<scratchpad>/review_nflmodel/` (`my_ladder.py` -- my own GLM, CV, dispersion,
families, threshold log loss, clustered SE, acceptance and walk; `my_kalshi.py` -- my own pre-kickoff
matching, scoring, fee and bootstrap; `my_mutate.py`; the leak / equivalence / board scripts are
inline in the session and their outputs are quoted below). The deep store was my own copy of the
builder's (`review_nflmodel/deep`); the frames were read in place from `<scratchpad>/nflm/work/`
(validation frame sha256 `97e90a68c29e...`, test frame `5180c0d59140...`, both as recorded in
`validate.json` / `test.json`).

Hashes of every file under review were recorded before (`review_nflmodel/sha_before.txt`) and after
(`sha_after.txt`); they are identical -- the tree ends byte-identical apart from this new file.

## 0. Verdict in one paragraph

**AGREE AFTER FIXES.** The science holds: the rule was hashed before the runs, the runs carry the
registered code hashes, no feature of any 2025 or 2026 row reads that row's week or later (proven on
real data by removing and poisoning the cutoff week and everything after it: 701 rows, every feature
identical to the last bit), the 2025 validation read only the 2018-2024 k, the 2026 decision read only
captures stamped strictly before kickoff, and my independent re-implementation reproduces every one of
the 72 rungs' lambda and decision, the shipping walk, and the shipped passing-TD coefficients to every
printed digit; my own Kalshi matching reproduces 2026 exactly (1,510 rungs, 0.4563 vs 0.4322, 1,028
bets, -6.95%). Three things need fixing before the board is honest about what it prints: (F1) the
yards family selection in `fit_dispersion` is degenerate -- a NaN in the comparison means "gamma" wins
by being first in the loop, the rule as written would have chosen **lognormal for rushing yards**
(1.366 vs 1.392 nats on the training rows), and `strip()` then drops the `_family` key from every JSON
so production could never read the choice anyway; (F2) 99% of the board's lines carry a degenerate
zero-width "band" (the coordinator's finding -- CONFIRMED), which made every +EV line "robust"; (F3)
the single-CV gamma is badly miscalibrated at low rushing-yard thresholds (RB lines of 20-30 yards:
model 0.857 vs hit 0.671 at 10+; QB rushing is the worst stat/position against Kalshi, 0.552 vs 0.407
on 67 rungs), and the board labels those lines +EV. None of the three changes a shipping decision --
nothing ships in rushing yards either way, and the one shipped term (V on passing TDs) is reproduced
exactly -- but F1 is a pre-registered procedure that the code did not execute as written, and F2/F3
are what the owner would read on the tab.

## 1. What I ran (independent evidence)

| check | how | result |
|---|---|---|
| prereg hash | sha256 of the block between the markers in the current report (one START, one END) | `4b7e792c...f051` = `prereg.json` = `validate.json.prereg` = `test.json.prereg` = `coefs.json.prereg_sha256` |
| code hashes at run | `validate.json.code`, `test.json.code` vs `prereg.json.code_at_prereg` | identical (nfl_matchup.py `cdea47a5...`, frame.py `8e41776a...`, fit.py `3b0254c5...`) |
| order of events | file mtimes (full-iso) | k_fit_only 09:32:19; frames 09:38; prereg 09:39:09.9; validate 09:40:00.5; test/coefs/lock 09:40:50.57; def_history 09:41:33; kalshi 14:13 (descriptive). `test_lock.json`: `forced: false`. `prereg.json` byte-identical in the 09:54 and 14:15 scratch copies |
| frame k | `def_history_val.json` / `def_history_test.json` k_mode | `fit_only` (2018-2024; e.g. def.man k 44.9, QB pass-yds-pg 29.2, PROE 165.4) / `pooled` (44.7, 23.1, 176.4) |
| frozen captures | frame_test 2026 rows | 1,400 frozen rows, **0 with lead <= 0 h**; week 1 lead 25.2-56.4 h, week 2 0.37-60.8 h, week 3 168.5-264.5 h, week 6 0.18-1.02 h; weeks 4-5 none; frozen QB rows 115, with completions 91, with interceptions 91 |
| leakage, real data | 2025 week 10 (357 rows) and 2026 week 6 (344 rows) of the test frame: every feature recomputed with the CURRENT code on (a) the full stores and (b) stores with every play, on-field record and score of that week and later removed/poisoned | max abs difference (a) vs (b) **0.0** on every feature; `def_vectors` (a) vs (b) 0.0 |
| current code == frame code | (a) above vs the frame's `f_*` columns (6 dp) | max 5.0e-7 (the rounding), 0 features off |
| research == production | `fit.agreement()` rerun with the CURRENT `nfl_matchup.py` | 2.22e-16 / 1.987e-14 / 1.11e-16 on 3,997 rows -- identical to `test.json.agreement` |
| ladder re-derivation | `my_ladder.py` (own code) on the two frames | 72 rungs: every lambda and every decision equal; d/se equal to <1e-5 on 8 markets; the yards markets differ by <=1.5e-3 and match to six decimals once I clip y at 0 in the GLM and use gamma (section 3) |
| shipping walk | same script, frozen 2026 rows | pcmp D3 +0.011262 +/- 0.012532 weight 0; ptd V -0.006651 +/- 0.008148 ships -- identical to `test.json` |
| coefficients | my refit 2023-2025 vs `coefs.json.models.ptd` | b [0.051018, 0.053366, 0.446881] identical; lambda 1000; cov diagonal identical; dispersion keys and values identical (max diff 0.0) |
| shipped-model log losses | `Model.from_json(coefs)` through the research path on the test frame | ptd frozen d -0.0066506 se 0.0081483 (n 114), revised +0.0012855 (n 185); pcmp 0.0 -- identical to `test.json` |
| Kalshi 2026 | `my_kalshi.py`: own name key, last candle ending <= kickoff, bid/ask within 20c, kickoff within 6 h, frozen rows only, production's `market_dist` | n 1,510, hit 0.2682, model 0.45633 vs mid 0.43222, Brier 0.14506 vs 0.13820; by stat identical to `kalshi.json`; outcome agrees 1,510/1,510; `no_frozen` 836; ROI 1,028 bets / 54 games -6.95% (my seed: 95% -14.2% to +0.6%) |
| fee math | `prop_value.ev_buy(p, ask, contracts=1)` | 1c at 10c/90c, 2c at 30-70c: Kalshi's ceil(0.07 x P(1-P)) per contract |
| guards | `python3 tests/guards_nfl_matchup_model.py` standalone | 63 checks, 0 failed, 0.2 s; pyflakes clean on all six files; `numpy`/`pandas` absent after importing `nfl_matchup`, `prop_dist`, `prop_value` |
| mutation | 14 single-line bugs of my own (not the builder's 22) on copies | 5 caught, 9 survived (section 6) |
| board, offline | `board(2026, 6, rows=<Sleeper week 6>, with_market=False)` on my store copy | 3.8 s, peak RSS 131 MB, payload 7.3 MB, 14 games / 296 players, numpy not imported, no NaN, every fact with a unit, every player's game_id in `games`, strong_fit 0, 9,387 of 9,471 lines zero-width |
| apply() | committed coefs on 300 real Sleeper pairs | `applied False`, reason "the pre-registered test shipped no DFS adjustment (weight 0)", `markets ['ptd']`, pairs byte-identical |
| def_history write | committed table minus 2026 week 6, `def_history(ctx, 2026, 7, save=True)` | computed and saved `nfl_matchup_defhist_2026_w06.pkl` to the deep store; equals the committed 3-dp rows within 0.0005 |

## 2. Leakage and the order of events -- AGREE

**2.1 The features.** `features` reads `defense_block(ctx, s, w, opp)` from `ctx.snap(s, w)` =
`Snapshot(s, w, season_tallies(max_week=w), priors)` (`nfl_matchup.py:306-318`); `season_tallies`
keeps `week < max_week` (`nfl_matchup_profiles.py:559-571`) and the Snapshot constructor raises on any
tally holding the cutoff week or later (`:664-667`). Participation fields (`pressure`, `man`,
`sh_frac`) read `prior_snap(s)` = priors only (`:320-324`, `:397`, `:405-412`); `player_splits` loops
`range(season - BACK, season)` (`:482`). `history` reads `ctx.current(s, w)` for the current season
(`:745`) and filters `r["week"] >= week` (`:750`, redundant with the tallies' own cut -- my mutation of
it was an equivalent mutant). Comparables use `defhist[(sb, r["week"], r["dfn"])]`, the opponent's
vector AS OF THAT GAME (`:755`). The real-data test above (section 1) is the proof: with every play,
on-field record and final score of 2025 week 10 (resp. 2026 week 6) and later removed and the removed
games' scores and lines poisoned, all 357 (resp. 344) rows' features are bit-identical. The frame's
`margin` / `total` come from the game's own closing line (`nfl_matchup_frame.py:343-344`) -- pre-game
information by construction (nflverse `spread_line` / `total_line`), see F5 for the caveat.

**2.2 The k and DECAY.** The validation frame was built under `NFLM_K=fit_only`
(`def_history_val.json` says so) with `k_fit_only.json` = `measure_reliability` on 2018-2024
(`research/nfl_matchup_kfit.py:25`, `SEASONS = range(2018, 2025)`); its k differ from the pooled table
(table in section 1). The k sensitivity on the 2018-2025-k frame made the same two decisions
(`validate.json.k_pooled_sensitivity`). DECAY 0.5 is a constant with the disclosure in R6; I did not
re-derive the persistence pairs (the data review did).

**2.3 The 2026 decision reads frozen captures only.** `_test_idx(F, m, "fz")` keeps rows with a
non-empty `fz_src` (`fit.py:887-891`); `eval_mask(..., "fz")` and `Model.predict(..., "fz")` read the
`fz_*` columns (`:106-147`, `:312-319`); the walk at `:936-951` uses kind `"fz"` throughout.
`choose_frozen` keeps `c[0] < kick` strictly (`frame.py:188-191`); the frame has no frozen row with a
non-positive lead. The props-log rows' content time is the log's own pass time (`frame.py:183`). The
re-saves (`sl_*`) enter the 2026 scoring only on the REVISED arm, which is reported, not decisive.

**2.4 The plumbing runs** (`plumbing_scramble`, `fit.py:755-767`) replace every `y_*` of 2025/2026
rows with a seeded draw from 2023 rows of the same position; their outputs went to
`<scratchpad>/nflm/work/plumbing_out/` (09:20-09:21, before the real runs) and their logs
(`plumb_val.log`, `plumb_test.log`, `plumb_k.log`, 09:14-09:15) show different numbers from the real
ones (e.g. patt R accepted). The real 2025/2026 FEATURES and Sleeper BASELINES were visible in those
runs; the outcomes were not. That is what the rule discloses (R6). `traincheck.json` (04:52) loads
`Frame(a.frame, set(FIT_SEASONS))` -- 2023-2024 rows only (`fit.py:726`). The dev scripts
(`nflm/dev/devcoefs.py`, `week7_measure.py`, `board_smoke.py`) fit on 2023-2024 and build boards; none
reads a 2025/2026 outcome.

**CANNOT VERIFY:** the 09:31:16Z hold release itself (no artifact; `k_fit_only.json` at 09:32:19 is
consistent); the absence of unlogged real-outcome runs before 09:40 (`write_json` replaces in place;
the only validate/test logs in the scratchpad are the 09:40 ones); that the two traincheck-induced
changes (lambda grid to 1e6, the 0.0005 floor) were the only ones -- no pre-traincheck code exists.

## 3. Re-deriving the decisions -- AGREE (with F1 found on the way)

`my_ladder.py` implements R1-R4 from the rule's text: Newton on the ridge-penalised Poisson
quasi-likelihood with offset log(Sleeper), columns over their training SD, intercepts unpenalised,
leave-one-season-out deviance over the ten lambdas, factor clamped to [-1, 1]; NegBin alpha by moments
floored at 0; yards = point mass from the count market's NegBin at the scaled count line plus a
gamma / lognormal positive part with a per-position CV, family by training threshold log loss;
passing yards NormalCC with a decile fit; the per-row threshold log loss summed over the market's
thresholds; the paired difference clustered by game with the G/(G-1) factor; accept iff d < -2 SE and
d < -0.0005 x thresholds; the walk on frozen rows, keep iff d < 0. Own incomplete-gamma and erfc.

Result: all 72 rungs give the same lambda and the same decision; d and SE agree to <1e-5 on eight
markets. The two yards markets differed by up to 1.5e-3 for two reasons I then confirmed by switching
each off: (i) the builder clips negative yards at 0 inside the GLM (`fit_glm`, `fit.py:234`; a
defensible quasi-Poisson reading not stated in R2 -- nit N1), and (ii) my family choice took lognormal
for rushing yards because I compared NaN-free means (F1 below). With y clipped and gamma, rec_yd and
rush_yd R and V match to six decimals (e.g. rush_yd V +0.002241 +/- 0.004564, lambda 30000). The
accepted set is {pcmp: D3, ptd: V}; the walk drops D3 (+0.011262 +/- 0.012532, 91 QB-games) and keeps
V (-0.006651 +/- 0.008148, 114); the shipped set is exactly `coefs.json.shipped`; the refit
coefficients and covariance are identical to `coefs.json`. **The rule as written produces the
committed shipped set.**

**F1 (major) -- the yards family was never chosen by the rule, and could not have reached
production.** `fit_dispersion` (`fit.py:503-512`) scores each family over `idx_by_market[m]`, the whole
fit universe, and takes `float(np.mean(ll))`. The fit universe contains rows whose yards line is
positive but whose COUNT line is missing or zero (rush_yd: 62 such rows in 2023-2025, 60 of them WRs;
rec_yd: 3); `market_probs` leaves those rows NaN (`:535-544`), so the mean is NaN for BOTH families,
`nan < nan` is False, and the first family in the loop ("gamma") is kept whatever the data say. On
the NaN-free rows the training log loss is: rec_yd gamma 2.1464 vs lognormal 2.1549 (gamma wins);
**rush_yd gamma 1.3921 vs lognormal 1.3662 (lognormal wins by 0.026 nats) on both frames**. Then
`strip()` (`:666-668`) removes every key beginning with `_` from what `write_json` writes, so
`coefs.json`, `validate.json` and `test.json` carry no `_family` at all (grep: 0 occurrences), and
production's `market_dist` (`nfl_matchup.py:966`) falls back to `"gamma"`. The `agreement` check
(`:1503-1543`) cannot see this: it hands production the IN-MEMORY `disp` that still holds `_family`.
The guards cannot see it either: `_coefs()` sets `_family` in a dict, never through the JSON.
Consequence today: no decision changes (nothing ships in either yards market), but every rushing- and
receiving-yards probability on the board, the DK variance, the validation rungs of rush_yd and the
Kalshi rush_yd comparison were scored under a family the written rule did not choose. By luck the
accidental family is the better one out of sample: on the 2025 eval rows lognormal is worse by
+0.0213 +/- 0.0084 (rush_yd) and +0.0138 +/- 0.0051 (rec_yd); on 2026 frozen rows +0.0232 +/- 0.0212
and +0.0324 +/- 0.0127. So "fix the code and re-run R2 as written" would make the board worse. Fix:
(a) compare families on the rows both can score (`np.nanmean`, or drop count-less rows from the yards
fit universe and say so); (b) persist the family under a key `strip()` keeps (e.g. `"family"`) and
have `market_dist` read it, with `agreement` reading the JSON round-trip; (c) a guard that each yards
market's committed dispersion names its family; (d) in the report, disclose that R2's family choice
was not executed, that the written rule picks lognormal for rushing yards in-sample and gamma is
better on 2025 and 2026, and amend the rule for future refits to choose the family on the eval
universe (or on held-out seasons), rather than silently re-running.

## 4. Post-test edits -- AGREE on behaviour; byte-wise CANNOT VERIFY for `nfl_matchup.py`

`research/nfl_matchup_fit.py`: the registered file (`3b0254c5...`) survives at
`<scratchpad>/review_integration/full2/research/nfl_matchup_fit.py`; `diff` against the current
`44946387...` shows ONLY the `cmd_kalshi` change (per-season frames via `--frame2025`, the `frames`
count) and the new argparse flag -- nothing on the fit, validate, test or export path. AGREE.

`research/nfl_matchup_frame.py`: current hash equals the registered one. AGREE.

`nfl_matchup.py`: **no copy of the registered `cdea47a5...` exists anywhere on this machine**
(searched the whole filesystem; the files are untracked so git has no history either). What exists:
`review_integration/sha_before.txt` (09:34) listing `cdea47a5` for `nfl_matchup.py`, an intermediate
copy `a2454c16...` (09:54) whose only difference from the current `4081a6a7...` is the Kalshi note's
"basis" clause, and the 09:41 real board whose single slate note has the OLD wording ("Matchup
adjustments that passed ... move: pass_td") while the current `slate_notes` produces the new one and
adds the Kalshi record -- consistent with "notes wording, the Kalshi note". Behavioural proof that
nothing the test or the board reads changed: the current code recomputes every feature of 701 frame
rows to the frame's 6-dp rounding and `def_vectors` exactly (section 1); `agreement()` with the
current code returns the test's exact figures; the shipped-model log losses recompute exactly. I
therefore AGREE that the numbers are unchanged, and record as F6 that the byte-wise claim cannot be
checked because the registered production file was not preserved (commit the files before `prereg`
next time, or store the three files' bytes beside `prereg.json`).

## 5. The Kalshi comparison (R5) -- AGREE

Pre-kickoff only: `nfl_matchup_kalshi.prekick` walks the hourly candles and breaks at the first
whose `end_period_ts > kick` (`:158-171`); a candle that ended at or before kickoff holds no in-game
trade. `kick_ts` is the gamebook's start time in US Eastern (`kick_map`, `:218-238`). The candle pull
asks `start_ts = kick - 4 d, end_ts = kick` (`:151`). Matching: normalised name (the app's own key),
kickoff within six hours, exactly one candidate (`fit.py:1436-1437`); frozen rows only for 2026
(`:1441-1443`); `k = ceil(floor_strike)` (`:1444`; 174.5 -> 175+). Fee: `ev_buy(p, ask, contracts=1)`
-> `fee_order_c` = ceil-to-cent of 0.07 x P(1-P) per contract (`prop_value.py:415-453`), Kalshi's
schedule (2c at 50c). ROI = profit / (price + fee) over the positive-EV side; the bootstrap resamples
GAMES with replacement, ratio of sums (`fit.py:1362-1392`). My own implementation of all of this
reproduces 2026 exactly (section 1); the 2025 arm is Sleeper's revised line and the report says so.
"Model" equals Sleeper on every matched event (no shipped factor touches those five series), and the
report says so (l.370-371).

Two things the report does not have (feeding F3): by position, 2026 frozen -- rush_yd/QB model
0.5524 vs mid 0.4067 (n 67, the worst cell), rush_yd/RB 0.4921 vs 0.4589 (217), rec_yd/WR 0.4605 vs
0.4171 (168), rec/TE 0.5542 vs 0.4949 (85), while rec/RB 0.5296 vs 0.5740 (66) and td/TE 0.5445 vs
0.5522 (82) go the model's way; by rushing-yard threshold -- k 20-40: model 0.676 vs mid 0.592 (82),
k 40-60: 0.631 vs 0.509 (54), k 60+: 0.246 vs 0.217 (105).

## 6. Production -- AGREE on the contract and the no-ops; F2 and F3 on what the lines claim

- **Pure Python, cost.** `nfl_matchup` imports `json/math/os/time/errlog`; `prop_dist` / `prop_value`
  likewise; `numpy` is absent after import and after a full board build. Offline week-6 board on my
  store copy: 3.8 s, 131 MB peak RSS, 7.3 MB payload (14 games, 296 players, no market). The
  report's 14.0 s / 146 MB / 10.4 MB with live feeds is plausible; CANNOT VERIFY (network). The
  matchups route builds in a niced child (`matchups.py:163-183`), so none of this sits in a gunicorn
  worker. `production_ctx` loads five seasons (`:1306`).
- **Payload contract.** On the coordinator's real 2026-10-25 board and on my offline board: no
  NaN/inf (`_clean`), every role / opponent fact has a `unit`, every player's `game_id` is in `games`
  and is digits, `fit_side` is present on every `strong_fit` line (there are none), top-level `notes`
  carry what ships and the Kalshi record (two sentences, one per season, basis stated).
- **strong_fit never fires.** `matchup_shift` excludes `int, margin, total_c, proe` (`:1057-1064`),
  the only shipped terms, so the shift is 0 on every market; 0 strong_fit lines on both boards. AGREE.
- **V term, train / serve skew.** Fit on the closing spread and total; production reads Kalshi's
  de-vigged ladder (`game_lines`, `:1239-1268`); a missing ladder reads margin 0 / total 44. On the
  real Sunday board 11 of 12 games had a ladder; CLE @ TEN read margin 0.0 with total 37.0 (the
  spread ladder not believable). Magnitude: Josh Allen's pass-TD line 1.53 -> 1.512 with no ladder
  (the PROE term alone) vs 1.679 with margin +7 / total 54 -- an 11% swing in the one shipped mean.
  Disclosed in report section 6 and in every factor row's text; the fallback is not MARKED as a
  fallback (N3). The sign convention agrees on both sides (favourite positive; store `spread` is
  nflverse's home-positive line, `frame.py:344`).
- **def_history** writes one deep-store key per missing week (`:1339-1345`), only for weeks the
  committed table lacks, and the saved vectors equal the committed ones within the 3-dp rounding.
  `artifacts.py:42` documents the key. AGREE.
- **apply() is a true no-op**: with the committed coefs it returns before touching a line
  (`:1185-1190`), the stamp names the reason, and 300 real pairs come back byte-identical. The guard
  pins the weight-0 switch. AGREE. (It is not yet wired into `nfl_dfs_sim.weekly_games`; the report's
  section 4 hands that to the orchestrator -- correct while nothing ships.)

**F2 (major) -- the zero-width band (the coordinator's finding): CONFIRMED.** `probs_with_band`
(`nfl_matchup.py:1098-1104`) returns `lo = hi = p` whenever `se` is 0, which is every market with no
shipped term (`market_adjust` returns `se 0.0` at `:878`), every `td` line (`:1893`, se always 0) and
every `dk_fp` line (`:1890`, `(p, p, p)` by construction). On the saved real board
(`<scratchpad>/nflm/board_w7_real.json`): 9,138 of 9,210 lines zero-width (99.2%; only the 72
pass_td lines carry a band, max width 0.083), and all 738 positive_ev lines were also
positive_ev_robust (the coordinator's rebuild: 9,153 / 9,225 and 786 / 786 -- same picture). Bryce
Young rush_yd 10+: p 0.7898 = p_lo = p_hi, Kalshi 48/54, labels high_probability, positive_ev,
positive_ev_robust, low_evidence -- reproduced. I agree with the coordinator's view: where no shipped
coefficient carries uncertainty the engine has no measured band and must emit `p_lo = p_hi = null`
(the contract allows it; "robust" then cannot apply; the UI prints "-"), not a degenerate interval
that claims zero parameter uncertainty around a Sleeper line the model did not fit. The prop_value
refusal the coordinator added stops the label but leaves the false claim in the payload. Fix in
`probs_with_band` (return `None, None` for lo/hi when `se` is 0), in the `dk_fp` and `td` branches,
in `matchups.index_of`'s line keys, and in the guard "every band holds its point" (allow None) plus a
new guard that a market WITH shipped terms has a band of positive width (my mutation "band always
zero width" survived the current guards).

**F3 (major) -- low-threshold rushing-yards calibration, and the coordinator's 30-point question.**
Measured on the frames through production's own `market_dist` with the committed coefs:

| rows | line bucket | n | 10+: hit / model | 15+ | 25+ | 40+ |
|---|---|---|---|---|---|---|
| QB 2023-25 revised | 10-20 yds | 619 | 0.527 / 0.579 | 0.384 / 0.381 | **0.234 / 0.148** | **0.086 / 0.032** |
| QB 2023-25 revised | 20-30 | 238 | **0.735 / 0.798** | 0.643 / 0.653 | 0.412 / 0.399 | 0.197 / 0.169 |
| QB 2026 frozen | 10-20 | 49 | 0.469 / 0.600 | 0.367 / 0.401 | 0.224 / 0.160 | 0.020 / 0.036 |
| QB 2026 frozen | 20-30 | 21 | 0.714 / 0.799 | 0.667 / 0.651 | 0.333 / 0.394 | 0.095 / 0.164 |
| RB 2023-25 revised | 20-30 | 487 | **0.671 / 0.857** | **0.579 / 0.714** | 0.394 / 0.435 | 0.232 / 0.171 |
| RB 2026 frozen | 20-30 | 30 | 0.667 / 0.862 | 0.467 / 0.721 | 0.300 / 0.442 | 0.233 / 0.175 |
| RB 2023-25 revised | 50+ | 1,322 | 0.982 / 0.981 | 0.964 / 0.952 | 0.900 / 0.863 | 0.743 / 0.693 |

One CV per position (QB 0.70, RB 0.63) with the point mass from a NegBin on the carry line is far too
thin at both tails for low lines: a backup RB projected 20-30 yards reaches 10 two times in three, the
model says six in seven (-19 points); a QB projected 10-20 reaches 25 and 40 yards far more often than
a gamma with CV 0.7 allows. At the Bryce Young level (QB, 20-30) the model's 10+ is ~6-8 points high
on 2023-25 and 2026, not 30: most of the 30-point gap is Kalshi disagreeing with SLEEPER'S MEAN (a 10+
line at 51c implies a market median near 10 against Sleeper's 23.05), and that is not the matchup
model's to fix; the shape is. Against Kalshi's 2026 prices QB rushing yards is the worst stat/position
(0.552 vs 0.407 on 67 rungs) and every rushing-yard threshold band below 60 loses by 0.08-0.12 nats.
The report's calibration table pins only rush_yd >= 50 (ECE 0.049), the one threshold where the
family is fine. Fix: a line-dependent CV (the passing-yards sd = c x mu^d decile fit already in the
code is the template) or a position x line-decile CV, judged out of sample by a rule written first;
until then the tab should not print +EV on low rushing-yard thresholds without the `market_better`
qualifier `matchups.mark_record` already attaches (it does, from `kalshi.json`), and the report should
carry this table.

## 7. Guards -- meaningful; gaps listed

63 checks, hermetic, 0.2 s, pass. The builder's 22 mutations (`<scratchpad>/nflm/mut/mutate.py`) are
real bugs and I accept the 20/22 count as reported. My own 14 (`review_nflmodel/my_mutate.py`):
caught -- backup QB under 10 attempts gets the passing factor; split rates unweighted across prior
seasons; yards point mass dropped; Kalshi receiving-yards quotes never attached; a 49.5 line read as
49+. Survived -- (a) `pressure` read from the CURRENT season with the "prior seasons only" label
kept (the guard pins the label string, not the value); (b) **band always zero width even where terms
ship** (F2's gap); (c) `proe` removed from `_NOT_MATCHUP` so strong_fit could fire on the shipped V
term (the guard's synthetic `adj` has no `proe` contribution); (d) split reliability from the LARGER
side; (e) yards family hard-coded lognormal (nothing reads a family from a file); (f) every market
reported "shipped" (`matchup_weight` label unguarded); (g) 300-yard bonus requiring 301 (the guard's
QB has 310); (h) the +/-1 clamp removed (equivalent on the fixture); (i) `history` including the
cutoff week (equivalent: the tallies already exclude it). Beyond mutations: nothing exercises
`research/nfl_matchup_fit.py`'s rule -- `accept()`, `MIN_GAIN`, the walk's `d < 0`, `_test_idx`'s
frozen filter, `fit_dispersion`'s family loop (F1 lives there) -- and no guard replays the committed
`validate.json` rungs through `accept()` to assert `decisions == coefs.validation_accepted`, or the
committed walk to assert `shipped == coefs.shipped`. Those two replays would pin the rule's APPLICATION
("a guard pins a number; only a reader pins its meaning" -- here the guard would pin that the number
was read by the rule). Minor (F7).

## 8. The report against the JSON that produced it

Checked sentence by sentence against `validate.json`, `test.json`, `kalshi.json`, `coefs.json`:
status line and hash (l.3-11); the one-paragraph answer (l.13-31: -0.0110 +/- 0.0055 z -2.00;
-0.0103 +/- 0.0036 z -2.84; +0.0113 +/- 0.0125 / 91; -0.0067 +/- 0.0082 / 114; +0.0013 revised);
the validation table (l.253-264, every row); the D5 targets line (+0.00000 +/- 0.00002, lambda 1e6);
the k sensitivity; O1 (all four pairs); low reliability (tgt D2 high -0.0040 +/- 0.0019, low -0.0001;
rung -0.0020 +/- 0.0013; pcmp D6 high +0.0177 +/- 0.0056; ptd D5 low +0.0060 +/- 0.0024); history
depth (-0.0158 +/- 0.0068 / 333; -0.0235 +/- 0.0075 / 87); anytime TD 3,357 / 0.7489; the DK table
and both differences (-0.00001 +/- 0.00001; -0.0008 +/- 0.0005 = 1.75 SE); the test table (every n and
Sleeper log loss; frozen 642/286/114/91, revised 1,007/464/185); the walk; the coefficients and the
exp(0.051 + 0.053) = 1.11 reading; "targets D3 +0.0050, receptions R +0.0162"; the DFS hook's
+2.8e-17 and the 772-row DK figures; the revision effect (all five markets); the five ECEs; the
agreement figures; the frozen coverage (330/407 at 25 h, 377/415 at 0.4 h, 403/440 at 233 h,
290/344 at 0.3 h); Kalshi's sample, matching, agreement, table and both ROIs. All as the JSON says.

Two sentences do not match their JSON:
- l.273-275: "Recalibrating Sleeper's level per position (R) made 2025 worse in seven of ten markets.
  Three of those were by two SE or more" -- seven is right; **five** were worse by two SE or more:
  receptions z +2.14, receiving yards +2.06, carries +2.49, completions +2.48, passing yards +2.08
  (F4, minor).
- l.367-368: "836 more 2026 markets fell in weeks 4-5" -- 836 is every matched 2026 market whose
  frame row has no frozen line; 949 such rows exist and 743 of them are weeks 4-5, the rest are
  weeks 1-3 and 6 players without a capture (N4, nit).

One sentence is right but should say more: l.26-27 "It is weak evidence: under one SE on the frozen
rows". The frozen gain of the shipped V term by week is +0.0060 +/- 0.0136 (week 1, 28), -0.0167 +/-
0.0204 (week 2, 30), **-0.0148 +/- 0.0144 (week 3, 32)**, +0.0020 +/- 0.0164 (week 6, 24); without
week 3, -0.0035 +/- 0.0099 (82). Week 3's captures are 7-11 days old and the V term reads the CLOSING
spread and total, so on 32 of the 114 QB-games the comparison pairs a stale Sleeper line with a line
dated after it. The decision stands by the rule's letter (d < 0 either way); the report should carry
the split (F5, minor).

## 9. Findings

| id | severity | file:line | what | evidence | fix |
|---|---|---|---|---|---|
| F1 | **major** | `research/nfl_matchup_fit.py:503-512` (family loop over NaN rows), `:535-544` (NaN for count-less rows), `:666-668` (`strip` drops `_family`), `:1001`; `nfl_matchup.py:966` (fallback "gamma") | R2's "family chosen by training log loss" was never executed: the comparison is NaN vs NaN and the first family wins; the chosen key is stripped from every JSON so production cannot read it | NaN-free training ll: rush_yd gamma 1.3921 vs lognormal **1.3662** (both frames); rec_yd 2.1464 vs 2.1549; `grep _family` on the three JSONs = 0; out of sample lognormal is worse (+0.021 +/- 0.008 on 2025, +0.023 +/- 0.021 on 2026 frozen) | nanmean / restrict to scorable rows; persist `family`; `market_dist` reads it; `agreement` through the JSON round-trip; a guard on the committed family; disclose in the report and amend R2 for future refits (do not silently re-run) |
| F2 | **major** | `nfl_matchup.py:1098-1104`, `:1890`, `:1893` | zero-width band on every line without a shipped term (99% of the board); made every +EV line "robust" | 9,138 / 9,210 lines; 738 / 738; Bryce Young 10+ p = lo = hi = 0.7898 | emit null lo/hi where no shipped coefficient carries uncertainty; guard it (positive width where terms ship, null elsewhere) |
| F3 | **major** | `nfl_matchup.py:955-967`; `research/nfl_matchup_fit.py:485-502` (one CV per position) | rushing-yards family far too thin at low lines; the board prints +EV on those lines | RB lines 20-30: 10+ model 0.857 vs hit 0.671 (n 487), 2026 0.862 vs 0.667; QB lines 10-20: 25+ 0.148 vs 0.234, 40+ 0.032 vs 0.086; Kalshi rush_yd/QB 0.552 vs 0.407 (67) | line-dependent CV (the pass_yd decile template), judged out of sample by a rule first; add the table to the report; keep `market_better` on those lines meanwhile |
| F4 | minor | report l.273-275 | "Three ... by two SE or more" | rec 2.14, rec_yd 2.06, car 2.49, pcmp 2.48, pass_yd 2.08 | "five" |
| F5 | minor | report l.26-27, 6; `frame.py:343-344` | the frozen V-term gain leans on week 3, whose captures are 7-11 days old while the feature is the closing line | week 3 -0.0148 +/- 0.0144 (32); without it -0.0035 +/- 0.0099 (82) | state the split beside "weak evidence" |
| F6 | minor (process) | `prereg.json` / untracked files | the registered `nfl_matchup.py` (`cdea47a5`) is not preserved anywhere, so "presentation only" is unverifiable byte-wise | filesystem search; behaviour verified (features, def_vectors, agreement, shipped log losses all exact) | commit before `prereg`, or store the three files' bytes beside `prereg.json` |
| F7 | minor | `tests/guards_nfl_matchup_model.py` | survivors: pressure from the current season (label pinned, value not); zero-width band where terms ship; `proe` as a matchup term; split reliability side; family; `matchup_weight` label; 300-yard bonus at exactly 300; nothing replays `accept()` / the walk on the committed JSONs | my 14 mutations, 9 survived | add the value pins and the two replays |
| N1 | nit | `fit.py:234`, report R2 | the GLM clips negative yards at 0 (a quasi-Poisson needs y >= 0); R2 does not say so | my unclipped fit differs by 3e-4 on rec_yd R | one sentence in R2 |
| N2 | nit | `fit.py:791-792` | `fit_dk_scale` fitted on 2023 and immediately overwritten | code | drop the dead line |
| N3 | nit | `nfl_matchup.py:1588-1592` | a missing ladder prints "expected margin 0.0 pts, total 44.0" without saying it is the fallback | CLE @ TEN on the real board read 0.0 / 37.0 (margin None) | mark it; 11% swing on the shipped mean (Allen 1.512 vs 1.679) |
| N4 | nit | report l.367-368 | "836 ... fell in weeks 4-5" | 743 of 949 unfrozen 2026 rows are weeks 4-5 | "had no frozen line (most of them weeks 4-5)" |
| N5 | nit | `research/nfl_matchup_kalshi.py:223` | `k2 = {"LA": "LA", "WAS": "WAS"}` is a no-op; Kalshi's tickers do use WAS (3,219 markets) so nothing is lost | market lists | delete it |

## 10. What I verified and AGREE with

The prereg hash and its one block; the registered code hashes in both result files; the order of
the artifacts; the single, unforced test; the validation frame's 2018-2024 k; the strict pre-kickoff
frozen capture and its lead times; no leakage of the cutoff week or later into any feature (real
data, two weeks, 701 rows, bit-identical) nor into the defense vectors; the current production code
computing the frame's features and the test's agreement exactly; every rung's lambda and decision
(72/72), the walk, the shipped set, the coefficients, the covariance and the dispersion values; the
shipped model's frozen and revised log losses; the Kalshi comparison's pre-kickoff rule, matching,
outcome agreement, fee schedule, ROI and bootstrap, and its 2026 figures to the fourth decimal; the
DFS hook as a no-op and the +2.8e-17 reading; strong_fit never firing; the payload contract on two
boards; pure Python at 131 MB peak; the def_history deep-store write; 63 guards and pyflakes; every
headline number in the report except the two sentences in section 8.

## 11. Verdict

**AGREE AFTER FIXES.** The decisions and every number behind them are reproduced; nothing leaks;
the one shipped term is weak and the report says so. Fix F1 (the family selection and its export --
with the deviation disclosed, not quietly re-run), F2 (null bands where nothing was fitted) and F3
(the low-line rushing-yards shape, or at least its disclosure and the qualifier on the tab) before
calling the board's probabilities honest; F4-F7 and the nits in the same pass. The coordinator's
zero-width-band finding is confirmed in full and I share its remedy; its 30-point example is mostly
Sleeper's mean against the market's, with a 6-8 point shape error on top that F3 measures.

Proposed row for `research/reports/matchup_agreement_log.md`:

| # | Deliverable / finding | Built by | Fable review | Orchestrator | Resolution | Ships? |
|---|---|---|---|---|---|---|
| 2 | NFL matchup model: ladder, 2025 validation, 2026 test, board | Fable | AGREE AFTER FIXES: F1 family selection degenerate + stripped from the JSONs (rule not executed; gamma shipped by loop order, happens to be better OOS), F2 zero-width bands (coordinator's finding confirmed), F3 low-line rushing-yards calibration; decisions, walk, coefficients and Kalshi reproduced exactly (`research/reviews/nfl_matchup_model.md`) | zero-width band found independently; prop_value refusal added | F1-F3 to fix and disclose; both reviewers to re-check the export and the band | **after fixes** |

---

# RE-CHECK (2026-10-23, ~15:20-15:45 UTC, after the builder's F1-F7 / N1-N5 fixes)

Same reviewer, same protocol. Nothing in the repo was edited except this appended section; the
official `validate` / `test` commands were not run; `test_lock.json` is untouched. Scripts and
outputs: `<scratchpad>/review_nflmodel/` (`sha_recheck_before.txt`, `build_board.py` with
`board_old.json` / `board_new.json`, `my_rushcv_gate.py`, `my_mutate2.py`, `oldcode/`).

## R.1 Verdict

**AGREE TO SHIP** the fixes. Every number the test or the board reads is unchanged: the same
9,471 offline lines give the same `p` to the last bit under the review-time code and the current
code, with the same means, distributions, shifts, hit rates, factor effects and profiles; the only
differences are the bands (null where nothing ships, the 84 passing-TD bands identical), the
labels that follow from them, and the new notes. `coefs.json`'s byte diff is exactly the two
`"family": "gamma"` lines. Every one of `kalshi.json`'s 224 earlier leaves is unchanged and the 330
added cells equal my own recomputation to four decimals. The section 9 rule was registered before
its gate ran, its stored code bytes equal the live file, and my independent recomputation of the
gate reproduces it to six decimals: it FAILED, and that is read honestly. The new guards kill 18
of my 21 mutants on copies (one survivor is an equivalent mutant, two are small gaps, R.7).
Three notes for the record, none blocking: the amended R2 uses one bit of the validation season
before the ladder is judged on it (R.5); the section 9 gate ran on a season my own table had
already pooled, which the block discloses and which is moot while nothing ships (R.5); and two
guard gaps (R.7).

## R.2 Hashes (my `sha_before.txt` vs now; the coordinator's list)

Every current hash equals the coordinator's list. Against my review-time record: CHANGED
`nfl_matchup.py` (4081a6a7 -> 63365400), `research/nfl_matchup_fit.py` (44946387 -> 0802b123),
`research/nfl_matchup_kalshi.py` (8a1c3978 -> 16fad83f), `tests/guards_nfl_matchup_model.py`
(eacd5a56 -> 72e47395), `research/reports/nfl_matchup_model.md` (280f5660 -> d69579e6),
`coefs.json` (ee318ff5 -> fddfdee0), `kalshi.json` (8f507a84 -> 5584eb31); NEW
`research/nfl_matchup_rushcv.py` (0cf82493) and `research/data/nfl_matchup_model/rush_cv/`. SAME:
`prereg.json`, `validate.json`, `test.json`, `test_lock.json` (`forced: false`), `traincheck.json`,
`def_history.json`, `k_fit_only.json`, `research/nfl_matchup_frame.py`, `research/nfl_matchup_kfit.py`,
and the three data-foundation files. The main PREREG block still hashes to `4b7e792c...f051` (one
START, one END). The registered `research/nfl_matchup_fit.py` (3b0254c5) and the review-time
`nfl_matchup.py` (4081a6a7), `nfl_matchup_kalshi.py`, `coefs.json` and `kalshi.json` survive in
`<scratchpad>/review_integration/{full2,mut}/`, so every diff below is byte-exact.

## R.3 (a) Production probabilities unchanged except the bands -- AGREE

`build_board.py` built the same offline week-6 board (Sleeper week-6 rows, my deep-store copy,
no market, a line of +3 / 47 handed to every team) twice: with the review-time `nfl_matchup.py`
and the review-time `coefs.json` in a private tree, and with the current code and file. 9,471
lines, identical keys; **max |p old - p new| = 0.0**; every `baseline_mean`, `adjusted_mean`,
`dist`, `matchup_shift`, `hit_all` / `hit_recent` / `hit_similar_def`, factor `effects`, `role`
and `opponent_profile` identical. Bands: old 9,387 zero-width lines -> new 9,387 with
`p_lo = p_hi = null`, 0 zero-width; the 84 pass_td lines keep a band identical to the old one
(widths 0.0083-0.0495). Labels: 111 lines gained `low_evidence` -- lines with no band and no
similar-defense history, which prop_value's rule now reads as unknown evidence instead of a
"known" zero width; a correct consequence. Notes: the F3 rushing-yards warning added; with every
team given a line, no fallback note and every game-script evidence kind "observed" (296 of 296).
No NaN. The builder's live 2026-10-25 rebuild (`<scratchpad>/nflm/fixcheck/board_w7.pkl`, built
15:18:54 UTC): 9,227 lines, 9,155 no band, 0 zero-width, 72 pass_td bands 0.0047-0.0833, 813
positive_ev, **0 positive_ev_robust**, 0 strong_fit, the F3 note, and one fallback note ("CIN @
BAL: no believable Kalshi spread (margin 0 assumed)") -- the ladder that was missing at 09:41 was
CLE @ TEN, so the gap moves through the week, which is why the note earns its place. The
coordinator's figures are confirmed.

The `nfl_matchup.py` diff itself (review-time -> current) touches only: the margin / total
evidence `kind` and `why` ("fallback" when the line is missing; the VALUES are unchanged, as the
p comparison proves), `yards_family` and its use in `market_dist`, `probs_with_band` returning
null bands at se 0, the DK ladder's null band, `labels_from(width=None)`, the fallback marking in
the factor text, the F3 slate note, and `line_fallback_notes`. Nothing in `features`,
`defense_block`, `player_splits`, `market_adjust`, `effects`, the distributions or `apply`.

## R.4 (b) and (c): the coefs diff and kalshi.json's old cells -- AGREE

`diff` of the review-time `coefs.json` (ee318ff5) against the current one: lines 239-240 and
267-269 only -- a comma and `"family": "gamma"` under `dispersion.rec_yd` and
`dispersion.rush_yd`. Nothing else, so the shipped set (`ptd: ["V"]`), `dfs_hook` off, the
coefficients, covariance and every dispersion value are the test's. `kalshi.json`: 224 leaves in
the old file, all present and equal in the new one (0 changed, 0 missing); 330 leaves added, all
under `by_season/2025` and `by_season/2026` as `by_stat_pos` and `rush_yd_by_k`. The added 2026
cells equal my `my_kalshi.py` output on every cell (e.g. rush_yd/QB n 67, 0.5524 vs 0.4067;
rush_yd/RB 217, 0.4921 vs 0.4589; rec/TE 85, 0.5542 vs 0.4949; k 20-39 0.6758 vs 0.5919; k 40-59
0.6309 vs 0.5087). The ROI draws are untouched (the new cells use no RNG).

## R.5 (d) The section 9 rule: timing, integrity, and whether the FAIL was read honestly -- AGREE

- **Integrity.** The RUSHCV block in the current report hashes to
  `a04c7e8ab0ae20700d87bb436679f724b9b0f486481f28082bd12845b4b90b3f` (one START, one END) =
  `rush_cv/prereg.json` = `rush_cv/code/rule_block.md.txt` = `choose.json.prereg` =
  `judge.json.prereg`. `rush_cv/code/nfl_matchup_rushcv.py.txt` is byte-identical to the live
  `research/nfl_matchup_rushcv.py` (0cf82493). The extract's sha (aed3732e...) and the
  `kalshi_nfl.py` dependency hash (fdb75941...) equal the record; `dep_drift` is empty.
- **Timing** (mtimes, full-iso): extract 15:05:50.47 -> register 15:10:09.19 (prereg.json, the
  code copy, the block copy) -> `choose.json` 15:10:13.27 -> `judge.json` 15:10:31.62. The gate
  ran four seconds after registration, as claimed; `cmd_extract` refuses once registered;
  `cmd_choose` fits on 2023-2024 only and refuses unless its single-CV refit reproduces
  `validate.json`'s alphas and CVs (it does -- my own refit gives the same six decimals).
- **The gate, recomputed independently** (`my_rushcv_gate.py`, my own incomplete gamma, NegBin
  zero mass, decile fit and clustered SE from `my_ladder.py`): alpha RB 0.125330 / QB 0.130862 /
  WR 0.196827; single CV 0.637173 / 0.734674 / 0.988401; line fit RB c 3.6858 d -0.4323, QB c
  3.6443 d -0.4767; 2025 priced rows n 1,327 over 272 games; ll single 3.245624, line 3.236980;
  **d -0.008645 +/- 0.013657** -- identical to `choose.json`. The rule needs d < -2 SE (-0.0273)
  and < -0.004: it fails, `passes: false`, `judge.json` records the skip, and no `judge_lock.json`
  was written (the code returns before the lock; correct, the once-only judgement has not been
  spent). By position RB -0.0121 +/- 0.0169, QB -0.0021 +/- 0.0216. The calibration cells show
  the candidate does what it was built for (RB lines 20-30 at 10+: 0.856 -> 0.722 against 0.693
  hit) and the report says plainly that a low-threshold-only test is a different question needing
  its own block. The FAIL is read honestly; the conservative outcome stands.
- **Taint.** My review's F3 table pooled 2023-2025 revised rows and 2026 weeks 1-6 frozen rows,
  so the hypothesis behind section 9 (a line-dependent CV) was formed with 2025 and 2026 weeks 1-6
  outcomes in view, and the 2025 gate is a check on a seen season, not a blind test. The block
  says exactly this ("What was seen before writing it"; Q4 "2025 is not blind for this question").
  Because the gate FAILED and nothing ships, no production number carries the taint; a PASS would
  have been weak evidence, a FAIL is the safe reading. The only clean judgement for this or any
  low-threshold rule is 2026 weeks 8+ (unseen), which is where Q5 puts it. I agree with the
  block's handling. Related, on the F1 amendment: `cmd_validate` now chooses the yards family on
  the VALIDATION season's priced rows before the ladder is judged on that season -- a one-bit use
  of held-out outcomes inside the validation. Section 2a says it binds only future refits and that
  its agreement with the shipped choice is "known, not tested"; for a clean future run I would
  choose the family on the last fitting season (as `traincheck` fits 2023 and scores 2024) or keep
  the one bit and say so in R2's successor. Minor.

## R.6 The report edits -- AGREE

F4: "Five of those were by two SE or more" (l.287). F5: the by-week split in 1.2 (l.349-354) and
in the one-paragraph answer (l.39-40). N4: the 836 sentence now says "no frozen baseline ...
weeks 4-5 have no capture at all, and some players in weeks 1-3 and 6 were not captured". N1: the
clip is disclosed in 2a (the main block is unchanged, as its hash requires). Section 2a's F1 table
(2023-2024 fit rows, 26 NaN rows, gamma 1.3923 vs lognormal 1.3658) equals my `my_ladder_out.json`
validation-frame figures (1.392349 / 1.365763); its attribution of my 1.3921 / 1.3662 to a
different row set is right (those were the 2023-2025 test-frame fit rows, 62 NaN rows, which I
also quoted). The F3 table is my table. Section 6 and 7 are updated (null bands; `test` now
refuses the committed `validate.json`, which names no family -- it is locked in any case).

## R.7 (e) Guards -- AGREE, two small gaps

`python3 tests/guards_nfl_matchup_model.py`: **96 checks with numpy, 93 without** (the three
research-rule checks: `accept()` and the family choice), 0 failed, 0.6 s; pyflakes clean on all
six files; `numpy` still absent after importing production. The builder's
`<scratchpad>/nflm/fixcheck/oldcode_failures.txt` shows 15 of the new checks failing on the
pre-fix code (null bands, the committed family, production drawing it, the fallback marks, the F3
note, the rush_cv record, N2, N5, `choose_family`, the carried family). My own 21 mutants on
copies (`my_mutate2.py`: my nine earlier survivors re-targeted, plus twelve reversals of the
fixes): **18 killed** -- pressure from the current season, zero-width band where nothing ships,
no band where a term ships, PROE as a matchup term, split reliability side, production ignoring
the committed family, every market "shipped", the 300-yard bonus, an unknown family name
(crash -> caught), the fallback kind, the fallback slate note, the F3 note, the DK zero band,
`choose_family` back to the NaN mean, the family under the stripped key, `choose_family` always
first, the dead DK-scale fit, the identity code map. **3 survived**: the +/-1 clamp removed
(equivalent on the fixture, as before); a missing band passed to `labels_from` as width 0.0 (only
the `low_evidence` label on a no-band, no-n_eff line differs -- nit); and **`fit_dispersion`
ignoring a family it is handed (always gamma)** -- latent, since every family today is gamma, but
it is the path by which a future `validate` choosing lognormal would be silently scored as gamma
by `test`; the guard "fit_dispersion carries a family it is handed" should hand it `lognormal`
and assert it comes back. The coordinator's 27/27 is on a different mutant set; on mine the count
is 18/21 with one equivalent mutant.

## R.8 Findings carried

| id | severity | where | what | fix |
|---|---|---|---|---|
| R1 | minor | `tests/guards_nfl_matchup_model.py` (the carried-family check) | `fit_dispersion(family=)` ignoring its argument survives | hand `lognormal`, assert it is carried |
| R2 | minor | `research/nfl_matchup_fit.py` `cmd_validate` (amended R2) | the family is chosen on the validation season's rows before the ladder is judged on them (one bit of held-out data) | choose on the last fitting season, or disclose the bit in the successor rule |
| R3 | nit | `nfl_matchup.py` `line_labels` | a no-band line without n_eff relies on prop_value's `width is None` rule for `low_evidence`; a `width=0.0` regression would not be caught | one guard line |

Proposed row for `research/reports/matchup_agreement_log.md`:

| # | Deliverable / finding | Built by | Fable review | Orchestrator | Resolution | Ships? |
|---|---|---|---|---|---|---|
| 2b | NFL matchup model fixes F1-F7 / N1-N5 and the section 9 rushing-spread rule | Fable | RE-CHECK AGREE TO SHIP: p unchanged on 9,471 lines, bands null except pass_td, coefs diff family-only, kalshi old cells unchanged and new cells reproduced, section 9 hashed before its gate and the FAIL reproduced to six decimals; two minor guard gaps and the one-bit family choice noted (`research/reviews/nfl_matchup_model.md` RE-CHECK) | verified 96/96, the main hash, the shipped set, the rush rule's timing | both agree | **ships** |
