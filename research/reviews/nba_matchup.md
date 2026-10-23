# NBA matchup engine -- Fable review (independent; a fresh instance, none of this built by me)

Reviewed 2026-10-23, 04:31-09:30 UTC (the container restarted twice, 04:35 and ~09:12; every run below was
re-done or completed after). Deliverable: nba_matchup_data.py, nba_matchup_profiles.py, nba_matchup.py,
research/nba_matchup_{fetch,build,walk,fit,kalshi,parity}.py, research/data/nba_matchup/{params,coefs}.json,
tests/guards_nba_matchup.py, research/reports/nba_matchup.md. Scratch inputs read (never written):
`scratchpad/nbam_work/`, `scratchpad/deep_nba_matchup/`, `scratchpad/data/{espn_nba_summary,kalshi_hist}`.
Everything I produced is under `scratchpad/fable_nba_review/` (recompute.py / .json, raw_asof.py / .json,
kalshi_check.py / .json, tmout2.py / .json, mutate.py, mem.py, board_size.py, work_copy/ = a validate re-run).
The orchestrator's review (research/reviews/nba_matchup_orchestrator.md) was read AFTER the findings below were
formed; my answers to O1-O6 are in section 9.

**Verdict: AGREE AFTER FIXES.** No blocker: nothing I re-derived makes a shipped number wrong -- every test-season
gain, the calibration table, the DFS arms, the Kalshi comparison and 240 feature rows reproduce to the digit with my
own code, and 60 player-games recomputed straight from the raw ESPN box scores match the builder's as-of inputs
(0 mismatches). What must change before shipping is listed in section 10: a mislabel the owner would read as an edge
(`strong_fit`), a board function that leaks when asked for a past date, guard gaps on the team accounting the shipped
rungs are built on, and report sentences that overstate or mislabel (rung 6's gain is an upper bound for production
-- the late-scratch leak itself is ~5% of the points gain, the unmeasured part is that production feeds rung 6 a
smaller feature than it was fitted on; the "DraftKings' own AvgPointsPerGame" arm is a box-score reconstruction; the
DK scoring table was not read from DraftKings; the Kalshi log-loss comparison has no uncertainty; the parity note's
"2021-22" is wrong).

## 1. Leakage in the path the numbers actually took

| # | Claim / check | Verdict | Evidence (mine) |
|---|---|---|---|
| L1 | Profiles are as of strictly earlier Mountain-time dates; a date's games never see themselves | AGREE | `W.walk` calls `on_date` BEFORE `add_date` (research/nba_matchup_walk.py:63-66); `pass3` snapshots inside `on_date` (fit.py:401-440); production `replay.flush` does the same (nba_matchup.py:450-477). Independent recomputation from the RAW summaries (`raw_asof.py`, my own box parser): 60 random 2025-26 player-games, the builder's `own` cur/prev sums (pts, min, ast, reb), `y`, `g_hist` all equal my sums over strictly-earlier MT days of the kept games; 46 of the 60 tipped on a different UTC date than their MT day, so the day boundary was exercised. 0 mismatches. |
| L2 | Season rollover | AGREE | `Book._p` / `_t` roll lazily (profiles.py:144-168); prev = last season only when consecutive; the recent EWMA carries (declared, guarded). The raw check above covers `prev` sums for 2024-25 -> 2025-26. |
| L3 | Priors (league position rates) are as-of | AGREE | `player_rate` reads `lg_pos` = LAST season's position sums first (profiles.py:315-319); `concessions` uses this season's league + 0.25 x last (profiles.py:475). |
| L4 | The minutes model, nodes, rate weights, dispersion, ridge penalty, K / rho / role K / oe K are fitted on 2022-23 + 2023-24 only | AGREE | fit.py: `fit_minutes` 272-281, `minutes_dist` 288-302, `fit_rate_weights` 230, `Ladder.__init__` (dispersion on `eval_population(rows, FIT)`, 770-808), `fit_market` CV 2022<->2023 (604-613), `measure_team` 165, `measure_roles` 199, `pass2` 379. Validation/test arms mix over PROJECTED minutes (`Ladder.minutes` -> `pred_minutes`, 824-836); actual minutes enter only the fit offset (596). |
| L5 | Observed-over-expected (rung 2/3/6) is as-of | AGREE | A defense's `oe` sums are added in `add_date` from `expected_for` of the game being added (profiles.py:232-255); the row's `oe_all` / `oe_pos` / `oe_form` read the snapshot before the date. `expected_for` uses that game's actual minutes and pace -- legitimate, it describes a game already played. |
| L6 | Comparables (rung 5) use strictly earlier games | AGREE | `add_comparables`: `prior = dn < dn[a]` (fit.py:480); production `comparables`: `if d >= day: continue` (nba_matchup.py:251). |
| L7 | "Teammates out" (rung 6) in research is read off the box -- the admitted late-scratch leak | AGREE, and it is larger than "a few rows" | Sized in section 7(b): of 4,187 absent as-of regulars in 2025-26, 667 (15.9%) are DNP-COACH'S DECISION (active, on no injury report -- production can never see them), 2,804 (67.0%) are not on ESPN's box at all (ESPN's box carries the active roster only: 12-14 players a side; these are the league's inactive list, announced before the tip), 716 (17.1%) are listed with an injury / rest / suspension reason. Rung 6 is essentially the WHOLE points gain (removing tm_out alone: -0.002433 +/- 0.000231 of the +0.001994 gain vs rung 1) and most of the assists gain; the coach's-decision part of it is worth +0.000097 +/- 0.000041 of the points gain (5%), so the LEAK is small and the larger issue is the train/serve skew (section 7b). |
| L8 | Opponent adjustment / defense form | AGREE | `oe_form` = ln(recent oe) - ln(oe) from the snapshot (nba_matchup.py:221). |
| L9 | The dispersion fit saw no 2024-25 / 2025-26 row | AGREE | `self.fit_pop = eval_population(rows, FIT, ...)`, subsample seeded 20261023 (fit.py:770-773). |
| L10 | Production board for a PAST date | DISAGREE with "no leak" | `board()` prices any date from the current book; `hit_all` / `hit_recent` read `hist_sorted` = every logged game including the date itself and later (nba_matchup.py:940-962). Harmless for today (his own game is not in the book yet), wrong for yesterday. The serving layer (matchups.py:78-86) only builds today / tomorrow, so nothing leaks TODAY; the function should refuse or label a date the book has passed. = orchestrator O2. |
| L11 | Research vs production feature definitions (modules edited at 04:29, after the test) | AGREE they are identical | `recompute.py` (G): replayed the store with the CURRENT modules to 2026-03-10 and rebuilt every rung's features for that date's 240 rows: max |diff| 0.0 on every feature and rate; the walk's tm_out equals rows.pkl's on all 16,098 test rows. A full `validate` re-run on a COPY of the work dir with the current fit code reproduces val.json field for field (0 differences, same selection). |

## 2. The pre-registered process

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| P1 | The rule's hash is b6f647c9...0d13 and unchanged | AGREE | My own sha256 of the bytes between the markers = b6f647c9235d923306ad58cf3e603b55198ac3ff02eee51fff8a3ed30a4d0d13; val.json, test.json and coefs.json carry that hash; `_require_prereg` (fit.py:1079-1083) refuses otherwise. |
| P2 | Frozen at 01:48:48 UTC, before `validate` first ran | CANNOT VERIFY the time; AGREE the text is unchanged since validation | No git commit or external timestamp exists for the freeze (the report's mtime is 04:24, after later edits). What IS verifiable: val.json (02:11:19) was written by code that checked the hash and recorded it, so the rule validation ran under is the rule in the report now. The work-file times are consistent with the run log (rows.pkl 01:47:11, fitcheck 01:46:55 on seasons [2022, 2023] only, disp.json 02:09:23, val.json 02:11:19, test.json 04:14:19, params.json 04:15:56, coefs.json 04:20:32). |
| P3 | Two `validate` starts crashed before any 2024-25 row was scored | AGREE (by code order; no log survives) | `run_eval` builds `Ladder` (the dispersion fit, fit seasons only) before `eval_population(rows, VAL)` and any arm (fit.py:950-966). disp.json's progressive save (785-795) means a crash there left a partial file and no val.json. |
| P4 | The test ran once | AGREE | One test.json (04:14:19); `test()` refuses when it exists (1101-1102). The refusal is a file-existence check -- adequate here, weak in general (a second work dir would run again). |
| P5 | No choice was made after the test (shipped_report, dump_preds, parity, compare) | AGREE | The shipped set is a function of test.json's verdicts alone (`freeze`, 1128-1153); the later commands read coefs.json and change nothing but `market_test` in it (`record_market`). The fit script's 04:20 edit did not change the scoring: my validate re-run reproduces val.json exactly. A provisional coefs.json existed before validation (the 01:50 smoke board has priced markets); the committed file is `freeze`'s (verdicts match test.json). |
| P6 | The dispersion metric switch (exact likelihood -> ladder loss) happened before the freeze on fit-season data | AGREE | fitcheck.json: seasons [2022, 2023], 31,439 rows, 01:46:55. |

## 3. The numbers (re-derived with my own code: own NB survival via lgamma, own minutes mixture, own ladder loss, own game-clustered SE)

| # | Claim (report) | Verdict | Mine |
|---|---|---|---|
| N1 | Test population 16,098 rows / 1,213 games / 419 players; DFS population 23,544 | AGREE | 16,098 / 1,213 / 419; 23,544. |
| N2 | Shipped vs rung 1, 2025-26: pts +0.00199 (0.00021, z 9.29); reb +0.00101 (0.00017, 5.88); ast +0.00153 (0.00024, 6.45); tpm +0.00066 (0.00019, 3.51); pra +0.00278 (9.1); pr +0.00256 (9.42); pa +0.00286 (9.3); ra +0.00234 (8.38) | AGREE | +0.001994 (0.000215, 9.29); +0.001014 (0.000172, 5.88); +0.001533 (0.000238, 6.45); +0.000663 (0.000189, 3.51); +0.002783 (0.000306, 9.1); +0.00256 (0.000272, 9.42); +0.002858 (0.000307, 9.3); +0.002344 (0.00028, 8.38). Ladder log losses to 5 decimals identical. |
| N3 | Calibration, pts shipped: 0.549 -> 0.585 (n 4,651), 0.649 -> 0.682 | AGREE | [0.549, 0.585, 4651], [0.649, 0.682, 4253]; reb and pra deciles identical to the report's. |
| N4 | dk_fp on 2025-26: DK-average arm MAE 8.164 / LL 0.33281; shipped 7.814 / 0.28266; by-threshold table | AGREE | 8.1643 / 0.332805; 7.814 / 0.282663; thresholds 0.759/0.823/0.820 ... 0.044/0.020/0.016; gain +0.0501 +/- 0.0019. |
| N5 | "sd 0.55 x mean -- what the DFS builder uses today" | AGREE | simulate.py:744 `dfs_sim(lineup, n=20000, cv=0.55)`, :1161 default cv 0.55 for sports other than nascar/f1/ufc. |
| N6 | "DraftKings' AvgPointsPerGame" arm | DISAGREE with the label | It is a RECONSTRUCTION: season-to-date DK points per game PLAYED from the box, last season's when none (fit.py:728-746), not DK's figure. Directionally the same thing (dk.py:21 calls DK's a lagging season average), but DK's own number was never read for 2025-26 and could differ (its game count, zero-minute games, the opening-night value). Label it "reconstructed" in the report and the rule. |
| N7 | "the defense terms (rungs 2-5) have an SD of 2.8% of the points mean (5-95%: -3.6% to +4.9%), 2.4% rebounds, 3.8% assists, 3.9% threes; teammates ruled out ... SD 2.3% when a regular is out, 78% of rows" | AGREE | 2.76% (-3.63%, +4.88%); 2.37%; 3.79%; 3.89%; tm_out SD 2.47% on the 78.4% of rows with a regular out (`tmout2.py`). |
| N8 | Validation tables (2024-25) | AGREE | Not re-derived rung by rung by hand; instead the full `validate` was re-run with the current code on a copy: val.json identical in every number and the same selection. |
| N9 | Research's comparables saw "back to 2021-22" (parity note, report 573-576; parity.py docstring) | DISAGREE | `pass3` skips HIST rows (fit.py:419-420): rows.pkl holds seasons 2022-2025 only (counts 25,338 / 25,949 / 25,952 / 26,090). Research comparables start 2022-23; production holds the season before and the current one. The direction of the explanation is right, the season is wrong. |

## 4. The Kalshi comparison (replicated independently: `kalshi_check.py`)

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| K1 | Sampling: a seeded sample of player-games per series until 800 markets, 4 series (PRA none) | AGREE | 3,200 candle files = 800 x 4; KXNBAPRA has 0 historical markets; the sample is by player-game (ticker prefix) with every threshold kept (kalshi.py:166-187). |
| K2 | Pre-tip price = last hourly candle ending at or before ESPN's tip; no in-game candle | AGREE | All 3,200 markets: `occurrence_datetime` is null, so the tip comes from ESPN's schedule (verified equal to the stored `tip_ts`); no candle in any file ends after the tip; no market's `close_time` precedes its tip. Quote age: median 0 s, p90 30 min, 0.5% older than 3 h (a stale quote hurts the market's score, not the model's; restricting to quotes <= 1 h old changes nothing: tight set 2,259 of 2,278). |
| K3 | Strikes | AGREE | Every `floor_strike` is x.5, so `round(k + 0.5)` is exact (Python's banker's rounding would have bitten on whole strikes; none exist). |
| K4 | Matching: 17 unmatched players, 555 without a two-sided book; n 2,628 / 478 games; tight 2,278 / 445 | AGREE | Reproduced exactly (own code). |
| K5 | Log losses model 0.5523 / rung 1 0.5527 / mid 0.5541 (all); 0.5562 / 0.5555 / 0.5424 (tight); bets 505 ROI -13.8% [-24.3, -2.7]; 485 ROI -14.3% [-24.9, -3.5]; by stat | AGREE | Identical to five digits, fees via `prop_value.fee_order_c` (a one-contract fee rounds up to a whole cent, as the owner would pay). |
| K6 | "The market is the better forecaster where its book is tight" -- stated with the right uncertainty? | DISAGREE on completeness | The report gives no SE on the log-loss difference. Mine (game-clustered): tight books, mid minus model = -0.0138 +/- 0.0041 nats (z -3.3, the market better); all two-sided books +0.0018 +/- 0.0052 (z +0.3, indistinguishable); by stat the market is better on assists (z -2.2), the model on threes (z +2.5), points and rebounds within noise. Add these. Also worth saying: on the Kalshi-priced rows the shipped model vs rung 1 is +0.0004 +/- 0.0020 (all) / -0.0006 +/- 0.0022 (tight): the matchup rungs' gains (0.001-0.002 nats per threshold) are below what 2,628 markets can resolve. |
| K7 | Calibration against the market | note | On the tight set, binned by the model's probability, the market's mid tracks the outcome at least as well in every bin (e.g. model 0.350 / mid 0.368 / observed 0.373; model 0.453 / mid 0.472 / observed 0.471): the model is too flat where it disagrees. Consistent with the report's conclusion. |

## 5. Production

| # | Claim / check | Verdict | Evidence |
|---|---|---|---|
| S1 | Pure Python on the server path | AGREE | nba_matchup*.py import no numpy; the guard's numpy use is optional. pyflakes clean on all ten files. |
| S2 | Memory: rebuild ~250 MB peak, board ~136 MB | AGREE (my fresh-process VmHWM: rebuild of 2024-25 + 2025-26 242 MB, 7.4 s, state pickle 22 MB; board 163 MB with the book loaded (85 MB after `load_book`), 4.3 s for 240 players offline) | Both belong in a niced child under `deep_cache.HEAVY_BUILD`, PC-first -- which is what `run_job_isolated` / `board_isolated` / matchups.py do. Never two at once on Render. |
| S3 | Payload | see 7(a) | 13.9 MB JSON for 240 players offline (no Kalshi blocks); the orchestrator's 18.8 MB for 260 with markets is consistent. |
| S4 | Contract: units on every fact, no NaN, monotone p, factor effects = what was applied, weight-0 shown | AGREE | On my offline board: every role / opponent fact has `unit`; no NaN/inf; p non-increasing in k for all 2,160 market ladders; adjusted rate = as-of rate x exp(intercept) x product of factor mults for every priced market (0 mismatches); 6,461 of 13,421 factor rows are weight 0 with mult 1. |
| S5 | `factor.evidence.kind` honesty | minor | The `comp` factor's evidence is `kind: "observed"` (nba_matchup.py:830); a similarity-weighted residual is modelled. `tm_out` is `observed` (ESPN's "Out" is observed; the usage share is modelled). Nits. |
| S6 | `strong_fit` label | DISAGREE with the semantics | `strong = has_rung and abs(matchup_mult - 1) >= 0.05`, `matchup_mult = mean / mean_base` (nba_matchup.py:951-965): it fires in BOTH directions (a defense that cuts the projection 8% puts "strong fit" on every YES line), includes rung 6 (a teammate out), and `mean_base` is the rung-1 fit with ITS OWN pace / home / intercept coefficients, so the ratio is not even the rung 2-6 product. On my board 2,293 of 17,280 lines carry it. = orchestrator O1. |
| S7 | `dfs_projections` never raises; `replaces_avg` | AGREE | try/except returns `[None] * n` (1132-1142); the guard feeds a broken state. `replaces_avg` reads `shipped.dk_fp_replaces_avg` (1215) -- a mutation hard-coding it True passes the guard (it only checks truthiness). No caller exists (orchestrator O6). |
| S8 | Statuses | AGREE | The live 01:50 smoke board parsed ESPN's pre-game injury block: 31 "Out", 14 "Day-To-Day" -> questionable, 257 not listed -> active. Only "Out" feeds rung 6 (1104); questionable players are priced at full minutes (declared). |
| S9 | `provenance.covers` | DISAGREE (nit) | nba_matchup.py:1066 prints "2025-27 season" for season 2026. = O3. |
| S10 | Duplicate `game_day` | minor | nba_matchup.game_day (421-425) and research/nba_matchup_walk.game_day (22-24) are copies; only the research one is guarded. A drift in production's would be a same-day leak. |
| S11 | DK scoring table | CANNOT VERIFY from a primary source (section 7(c)) | |

## 6. Guards (tests/guards_nba_matchup.py): hermetic, 45/45 in 0.9 s; mutation test on COPIES (`mutate.py`, symlinked tree)

38 real mutations + 3 controls. CAUGHT 23: as-of snapshot contamination; walk adds before snapshot; season roll drops / keeps sums; comparables same-day and un-centred score; NB recursion and size parameter; DK >10, triple-double without double-double, swapped weights; weight-0 factor with mult != 1; decorative 1% on the adjusted rate; dropped intercept; ruled-out players priced; role facts without `unit`; DREB chances on the wrong side; sub-out player kept on the floor; 2021-22 shot-value patch removed; role and player_rate shrinkage formulas.

MISSED 15 real bugs:

| mutation (a real bug) | why the guard misses it | fix |
|---|---|---|
| team possession credited to the DEFENSE (`T[1 - off][TI["poss"]]`) | the fixture is symmetric (3 and 3 possessions), so a side swap passes; player-level poss are checked, team-level feed every per-possession concession | an asymmetric fixture (4 vs 3) |
| `oe` sums include the defense's OWN players | the OE accounting (rung 2's feature for every market) has no guard | a two-game fixture with known E and actuals, pin `oe()` |
| concessions ignore K / ignore rho | `concessions()` has no guard | pin one channel's shrunk ratio on a fixture (K = n gives half-way) |
| `parse_box` counts DNP players as played | `parse_box` is never exercised (the fixture builds `box` by hand) | parse a tiny boxscore dict with a DNP row |
| `rotation()` without the last-3-games window | unguarded | fixture: a regular who last played 5 team games ago is not in the rotation |
| production `game_day` = UTC date | only the research copy is guarded | one check on `NM.game_day`, or one function |
| early clock counts a period-start possession | the fixture's "#1 first shot of the period" is 20 s in, outside EARLY_S, so it does not pin the rule | a period-start shot within 7 s |
| band collapses to a point (`lo, hi = max, min`) | `p_lo <= p <= p_hi` still holds | require `p_lo < p < p_hi` on a line with se > 0 |
| short/mid zone boundary moved | definition not pinned | one shot at 13 ft and one at 14 ft |
| questionable counted as out in rung 6; minutes floor removed; M6 ignores its context (not shipped); `replaces_avg` hard-coded True | not pinned | small fixture checks |

The guards pin the distributions, the as-of rule, the stint walk, the DK table and the payload identities well; the TEAM accounting and shrinkage that produce the shipped rung-2/4 features are unguarded. My independent recomputation shows that code is right today (feature parity 0 diff; raw-box check 0 mismatches); the gap is regression protection, which CLAUDE.md requires ("new behaviour ships with a guard").

## 7. The coordinator's three items

**(a) Payload size and memory.** Measured on my offline board (240 players, 12 games, no Kalshi blocks; `board_size.py`,
`mem.py`, fresh `python3 -I` interpreters reading /proc/self/status):

- Whole board: 13.9 MB JSON, 8.1 MB pickle; unpickled object tree +45 MB RSS, +17 MB more for `json.dumps`. Sections:
  factors 5.63 MB, markets 4.57 MB, opponent_profile 1.58 MB, role 1.38 MB, comparables 0.49 MB, notes 0.10 MB.
- The serving layer (matchups.py, the orchestrator's) does NOT unpickle the whole board per request: it publishes one
  piece per game plus a slimmed index. Measured: index 1.69 MB JSON (10,264 of 16,560 lines kept) -> +8 MB object,
  +11 MB with its JSON string; the largest game piece 1.67 MB JSON -> +5 MB object, +8 MB with its string. The index
  is cached per worker (`_MEM`, 4 slates): budget ~8 MB x slates x sports per worker. So the per-request cost today
  is ~10 MB transient, not the board.
- What is redundant (largest piece, 29 players, 1,615 factor rows = 677 KB): `effect` 128 KB (`mult` is exp(coef x x),
  `weight` a repeated string), `evidence` 109 KB (696 of 1,615 rows are all-None except `kind`), `detail` 114 KB
  (`coef` has only 56 distinct values, repeated per player), `opponent` text 94 KB (100 distinct strings), and 774 of
  the 1,615 rows (48%) are weight 0; `markets` 552 KB of which ~62 KB is the literal `"market": null, "value": null`;
  `opponent_profile` is byte-identical for every player facing the same opponent (board: 1.58 MB, 24 distinct blobs
  = 158 KB).
- Smallest payload that keeps the contract: (1) one factor row per UNSHIPPED rung per market, its channels in that
  row's `detail` (the contract's "supporting table the UI shows on click"), 56 -> ~26 rows a player; (2) drop
  None-valued `evidence` keys and either `mult` or `coef` (keep `coef`, the guard uses it); (3) shorten `opponent` to
  the figure (the profile already carries the basis). That is roughly -70% on factors and -45% on a piece
  (0.75-1.67 MB -> ~0.4-0.9 MB). Moving `opponent_profile` to a per-team table referenced by the player is the next
  -1.4 MB a board but is a contract change. The memory lever beyond that: store the pieces as JSON (gzip) bytes and
  return the bytes, so a request builds no object tree at all.

**(b) The late-scratch leak (rung 6).** It cannot be measured exactly: ESPN's post-game summaries carry no injury
block (checked: `injuries` is absent from every stored summary), so when an absence was announced is unknowable from
this data. What can be measured (`recompute.py` E, `tmout2.py`; the as-of rotation = `Book.rotation`: 15+ minutes on
his six-game EWMA, played in one of the team's last three games; 2025-26):

| absent regular, the box says | n | share |
|---|---|---|
| not on ESPN's box at all (ESPN's box is the ACTIVE roster: 12-14 players a side in 2025-26; these are the league's inactive list, announced before the tip -- injured, G League, not with team) | 2,804 | 67.0% |
| listed with an injury / illness / suspension / rest reason | 716 | 17.1% |
| DNP-COACH'S DECISION (active, on no injury report: production can never count him) | 667 | 15.9% |

Sensitivity, the shipped model re-scored on the 2025-26 props population with ONLY tm_out recomputed (everything else
fixed; gain vs rung 1 in ladder log loss, game-clustered SE):

| absences counted as "out" | pts | ast | reb | tpm |
|---|---|---|---|---|
| A: every absence (what research scored) | +0.001994 (0.000215) | +0.001533 (0.000238) | +0.001014 (0.000172) | +0.000663 (0.000189) |
| B: every absence EXCEPT coach's decision (the most a pre-tip report can show) | +0.001896 (0.000207) | +0.001485 (0.000233) | +0.000983 (0.000170) | +0.000672 (0.000190) |
| C: coach's-decision absences ONLY | -0.000203 (0.000189) | +0.000365 (0.000201) | +0.000833 (0.000163) | +0.000520 (0.000183) |
| D: only absences listed with a reason (if the inactive list were NOT known) | +0.000276 (0.000195) | +0.000666 (0.000208) | +0.000911 (0.000167) | +0.000637 (0.000185) |
| none (tm_out = 0) | -0.000439 (0.000195) | +0.000223 (0.000205) | +0.000779 (0.000164) | +0.000514 (0.000184) |
| A minus B (the coach's-decision absences' contribution, paired) | +0.000097 (0.000041) | +0.000048 (0.000036) | +0.000031 (0.000017) | -0.000009 (0.000015) |

Rows with a regular out: A 12,615 of 16,098 (78%), B 11,279 (70%), C 3,696 (23%).

Reading: rung 6 is essentially the whole points gain (without it the shipped points model is slightly WORSE than
rung 1, its other coefficients having been fitted jointly) and most of the assists gain; rebounds and threes barely
depend on it. The part production can never see before the tip (the coach's decisions) is worth about 5% of the
points gain (+0.000097 of +0.001994, z 2.4), 3% of assists', nothing on rebounds and threes -- IF every absence of
the other two kinds was known before the tip, which the league's inactive list and the injury report make
plausible but which this data cannot prove. So the report's "a leak of a few rows" understates the rows (1,336 of
16,098 test rows change) but the leak's effect on the measured gain is small. Two caveats matter more and the
report should state them: (i) train/serve skew -- the coefficient (pts +0.219 per ln(1 + usage share out)) was
fitted on ALL box absences, production feeds a subset (ESPN "Out" only: a regular rested, G-League-assigned or
"not with team" is on the inactive list but not always "Out" on ESPN), so production's adjustments are
systematically smaller than research's and the live gain is unknown until measured; (ii) a late scratch of an
injury-listed player (Questionable, then out) is in research's A and in production's "Out" only if ESPN flipped
him before the board built. The honest statement: row B is the best production can match, the measured gain is an
UPPER BOUND for production, and the live figure needs production's own tm_out logged beside each prediction (a
shadow bucket) and graded.

**(c) The DraftKings triple-double rule.** CANNOT VERIFY from a primary source from this host, 04:44-04:50 UTC:
www.draftkings.com/help/rules/nba answers 404 to curl and 403 to the fetch tool; /help/rules/4/1 answers 200 but is
a script-rendered shell (572 KB, no scoring text); api.draftkings.com answers 403 through this proxy (the lobby on
www.draftkings.com answers 200 but carries no scoring rules); support.draftkings.com is a script shell;
dknetwork.draftkings.com's search returns daily picks only; DuckDuckGo and Bing are bot-walled or useless. The
builder's table (point 1, three 0.5, rebound 1.25, assist 1.5, steal 2, block 2, turnover -0.5, double-double 1.5,
triple-double 3, a triple-double earning both bonuses = 4.5) matches my recollection of DraftKings' published NBA
Classic rules, but a recollection is not a source and I will not call it verified. A verification that uses
DraftKings' own numbers and needs no page: any DK NBA contest standings export (or the draftables feed after a slate
settles, which the owner's PC can reach -- dk.py reads api.draftkings.com there) carries each player's actual DK
points; compare them with `nba_matchup.dk_points` on a night with a triple-double. Until then the report should say
"unverified against DraftKings' page" rather than "the published one". The stake is small (a triple-double is rare
and the difference is 1.5 points) but the DFS hook has no caller yet, so nothing is lost by waiting.

## 8. Things that could mislead the owner

- `strong_fit` (S6 / O1): reads as a positive signal; fires on unfavourable matchups and on teammate absences.
- "DraftKings' own AvgPointsPerGame" (N6): a reconstruction.
- "17 markets could not be matched ... the market is the better forecaster" without an SE (K6): true, but give the z.
- The rung-6 story ("the largest single improvement", test z 10): true on the box, an upper bound live (7b); the
  coach's-decision leak itself is ~5% of the points gain, the skew between the fitted and the served feature is the
  unmeasured part.
- The report's "a coach's late scratch is a leak of a few rows": 667 of 4,187 absences (1,336 test rows) in one
  season is not a few, even if their effect on the gain is.
- The parity note's "2021-22" (N9).
- The board's top note correctly states the market result; the `positive_ev` label is only the model disagreeing
  (stated in the report and the module docstring) -- good.

## 9. The orchestrator's findings (read after the above)

- **O1 (strong_fit) -- AGREE**, adding: `mean_base` carries rung 1's own pace / home / intercept coefficients, so the
  ratio is not the rung 2-6 product either; the fix should take the defense terms' multiplier explicitly.
- **O2 (past date leaks) -- AGREE** (my L10); fix in the function, not only in the serving layer.
- **O3 (provenance text) -- AGREE** (my S9).
- **O4 (late-scratch exposure) -- AGREE**, with my own numbers: a different regular definition (the as-of rotation,
  one season) gives 67.0% not on the box / 17.1% listed / 15.9% coach's decision against the orchestrator's
  57.1 / 24.7 / 18.2 over two seasons; both say about one absence in six is invisible before the tip. The
  sensitivity the orchestrator asks for is table 7(b): row B is the production-visible best case, A minus B the leak
  -- +0.000097 +/- 0.000041 of the +0.001994 points gain (5%), 3% of assists', nothing on rebounds and threes. So
  "an upper bound" is right, but the coach's-decision leak is a small part of it; I add the train/serve skew (the
  coefficient fitted on all box absences is applied to a smaller feature) as the larger, unmeasured reason.
- **O5 (payload / serving) -- AGREE**; my measurements in 7(a) (index +8 MB, piece +5 MB per request; the
  redundancies and the smallest contract-keeping payload).
- **O6 (DFS hook has no caller) -- AGREE**: simulate.py contains no NBA reference at all (dk.py only lists NBA among
  showdown sports); a limitation, not a wiring task.

## 10. Findings

| id | severity | file:line | what | evidence | suggested fix |
|---|---|---|---|---|---|
| F1 | major (fix before ship) | nba_matchup.py:951-965 | `strong_fit` fires on unfavourable matchups and teammate absences, on every line of the market, from a ratio that includes rung 1's own coefficient differences | 2,293 of 17,280 lines on my board; code reading | multiplier of the DEFENSE terms kept (rungs 2-5 + oe_form) only, label the favoured side, emit `fit_side`; guard on a fixture with a -8% matchup (= O1) |
| F2 | major | nba_matchup.py:940-962, 1034-1064 | `board()` for a past date reads a book and hit tables that saw the date | code reading; matchups.py only hides it | filter `vals` to `h[0] < day_n`; refuse / label a date <= `state["through"]`; guard (= O2) |
| F3 | major | tests/guards_nba_matchup.py:144-148, 155-157; nba_matchup_data.py:143-177 (parse_box), 370-381 (team poss); nba_matchup_profiles.py:191-202 (rotation), 232-255 and 505-527 (oe), 463-502 (concessions); nba_matchup.py:421-425 (game_day) | guards miss the team accounting and shrinkage behind the shipped rung-2/4 features, the `played` rule, the rotation window and production's game day | 15 of 38 real mutations pass the suite (section 6) | the fixtures in section 6, each failing on the mutated code |
| F4 | minor (report wording) + one follow-up | research/reports/nba_matchup.md:580-582, 504-505; nba_matchup.py:356-361 | "a leak of a few rows" understates the rows (1,336 of 16,098 test rows carry a coach's-decision absence) though the leak's effect is small (5% of the points gain); the real gap -- production feeds rung 6 a smaller feature than research fitted it on -- is not stated | 7(b): A minus B +0.000097 +/- 0.000041 (pts); rung 6 is the whole points gain; "Out" is a subset of the box's absences | state table 7(b) and the skew; log production's tm_out beside each prediction (shadow bucket) and grade the live gain |
| F5 | major (report) | research/reports/nba_matchup.md:60-62, 335, 521, 526; research/nba_matchup_fit.py:728-746 | "DraftKings' own AvgPointsPerGame" is a box-score reconstruction | code reading | call it "reconstructed from the box (season-to-date DK points per game played)"; when the PC can read DK's figure, compare once |
| F6 | major (report) | research/reports/nba_matchup.md:534-537; nba_matchup.py:70-79 | the DK table is called "the published one" but was not read from DraftKings | 7(c) | mark unverified; verify against DK's own FPTS from a standings export / draftables on the PC |
| F7 | minor (report) | research/reports/nba_matchup.md:546-551 | the market comparison states no uncertainty on the log-loss difference | K6: tight -0.0138 +/- 0.0041 (z -3.3); all +0.0018 +/- 0.0052 (z 0.3) | add the clustered SEs and the by-stat z |
| F8 | minor (report) | research/reports/nba_matchup.md:573-576; research/nba_matchup_parity.py:10-12 | "research rows saw back to 2021-22" | rows.pkl holds 2022-2025 (fit.py:419-420 skips HIST) | "back to 2022-23" |
| F9 | minor | nba_matchup.py:1066 | provenance `covers` "2025-27 season" | my board's provenance | f"{season}-{(season + 1) % 100:02d}" (= O3) |
| F10 | minor | nba_matchup.py:421-425 vs research/nba_matchup_walk.py:22-24 | duplicated `game_day`; only one guarded | mutation missed | one function, one guard |
| F11 | minor | nba_matchup.py:846-876, 921-937, 960-964 | payload redundancy (48% weight-0 rows, repeated coef / opponent text / None evidence, duplicated opponent_profile, literal nulls) | 7(a) | the trims in 7(a); pieces as JSON bytes |
| F12 | minor | nba_matchup.py:830-834 | `comp` evidence kind "observed" (a modelled score); `tm_out` kind "observed" | contract: `kind` is honest | "modelled" |
| F13 | nit | nba_matchup.py:1215; tests/guards_nba_matchup.py:462-465 | `replaces_avg` guard checks truthiness only | mutation missed | assert it equals `shipped.dk_fp_replaces_avg` on a params fixture with False |
| F14 | nit | research/nba_matchup_fit.py:1101-1102 | the test's "run once" is a file-existence check | process review | adequate for this run; note it |

## 11. Verdict

**AGREE AFTER FIXES.** Blockers: none -- the numbers are what the code computed, the as-of path holds against the
raw feed, the test ran once under the frozen rule, and the market comparison is honest. Before shipping: F1 and F2
(user-facing mislabel and a leaking function, each with a guard), F3 (guards on the team accounting / shrinkage /
played rule / rotation / production game day), and the report wording F4-F8 (rung 6 as an upper bound with the
sensitivity table and the skew, the reconstructed DK-average arm, the DK table marked unverified, the Kalshi SEs,
the 2021-22 correction). F9-F14 with them if cheap. Re-check by both reviewers after the fixes, per the protocol.

## RE-CHECK (Fable, 2026-10-23 09:37-09:55 UTC) -- the builder's fixes for F1-F13 / O1-O6

Checked against the CODE PATH on copies (the repo's tree is byte-identical to my 09:37 snapshot afterwards,
`fable_nba_review/recheck_sha_start.txt`; mutations ran in the symlinked `fable_nba_review/mut` tree only).

**Nothing shipped changed.** params.json 5ba6026508a6624b… and coefs.json 43ec107ffb7aaa30… equal the builder's
`sha_before.txt` and `nbam_sha_after.txt` and the contents I read at 04:3x; nba_matchup_data.py and
nba_matchup_profiles.py are byte-identical to before (0e3de38d…, 29a47dd4…); the rule still hashes to
b6f647c9…0d13 and the new "clarification" paragraph sits OUTSIDE the hashed block; research/nba_matchup_fit.py changed
by one docstring (3 lines), parity.py by one docstring, walk.py only in that `game_day` is now `nba_matchup.game_day`
(the same expression: UTC minus six hours on `iso[:16]`); the report's numeric tables are unchanged (full diff read).
validate / test were not re-run (val.json / test.json / selection.json untouched) -- and my own 04:5x re-run of
`validate` with the current fit code had already reproduced val.json exactly. **(e) AGREE.**

**Guards.** 65/65 in 1.3 s, pyflakes clean on all ten files plus matchups.py. The new guard file run against the
pre-fix tree copy (`nbam_oldtree_v1`, old nba_matchup.py 60527e35…) fails exactly 9 checks (strong_fit x4, past
date x2, the collapsed weight-0 row, None evidence keys, game_day identity) -- the builder's claim reproduced. My
harness, patterns updated to the new code plus 11 mutants for the fixes (`fable_nba_review/mutate2.py`, 47 real + 1
control): 45 CAUGHT, including every F3 gap from the first review (asymmetric possessions, OE sums, concessions K and
rho, parse_box's played rule, rotation window, production game day, period-start early clock, zone line, collapsed
band, questionable-as-out, minutes floor, replaces_avg, M6 context) and every fix (fit_side flipped, fit_multiplier
counting tm_out or pace, threshold moved, label on one side only, fit_side not emitted, hit tables reading the date,
both past-date refusals, weight-0 rows dropped, None keys kept). Two real mutants still pass, both small: a decorative
`effect.mult` on SHIPPED rows (`mult = exp(b x) * 1.0001`) -- the identity guard reads `detail.coef x x`, not `mult`,
and the UI does not read `mult` either; and the COMBO / dk_fp `fit_multiplier` formula (`fmc = fm[parts[0]]` passes:
`fit_payload` pins only the four base markets' multipliers). So "catches every real bug" is slightly overstated; see
R2, R3.

**(a) strong_fit on the real 2026-10-23 slate (new code, offline).** Rebuilt with `nba_matchup.board("2026-10-23")`
from the live board's 12 games and its 31 Out / 11 Day-To-Day statuses on the deep store's book (through 2026-10-22,
fit b6f647c9…): 229 priced players, 15,801 lines, every line carries `fit_side`; strong_fit on 419 lines, 161 yes /
258 no -- the builder's figures exactly; by stat fg3m 288, reb 104, ra 27, none on pts / ast / pra / pr / pa / dk_fp
(the shipped points terms have an SD of 2.8%, so 5% is rare; threes' 3.9% makes them the label's main home). Label
present iff `fit_side` set, side equal to the market's `fit_multiplier` against 1.05 / 0.95: 0 inconsistencies.
Recomputed with my own formulas from the payload -- each base market's multiplier as the product over its shipped
rung 2-5 rows plus `oe_form`, each combo's as adjusted rate / sum(component rate / component multiplier), dk_fp's as
the DK-weighted linear rate over the same with stl / blk / tov at 1 (their adjusted rates rebuilt from `rate_*` and the
AUX coefficients) -- all nine agree with the payload to 5e-6 (its six significant digits). Combos and dk_fp are right
by the stated definition (a rate-weighted mean of the components' defense multipliers; dk_fp's bonus terms ignored,
which is fine for a label). 135 of the 419 lines sit in the far tails (p < 2% or > 98%); the index drops those unless
Kalshi lists them, so the scanner shows ~284. **The labelling is acceptable to me as labelled**: the UI's text ("the
matchup terms the model kept … move this projection 5% or more toward the named side. Says nothing about price") says
exactly what the code computes, a MARKET-level fact carried on every line with its side; "Strong fit (YES)" on a 6+
threes line at p 1% is true and harmless. It is not a per-line statement and should not be sold as one.

**(b) Past date.** `board("2026-10-22")` on the book through 2026-10-22 returns 0 players and the note; `dfs_projections`
returns [None] (ledgered NBAM-dfs-past). `hist_sorted = sorted(prior_h)` so hit_all / hit_recent read games before the
date (guard `past_hits` + my mutant CAUGHT). AGREE -- with one operational consequence the fix opens, R1 below.

**(c) Payload trim vs the contract and the UI.** Every factor row keeps factor / player / opponent / assessment /
effect / evidence / detail; `evidence.kind` is always present, `n` / `n_eff` / `reliability` only when known (5,038
shipped rows carry kind alone, 1,374 kind+n+reliability, 229 kind+n+n_eff) -- a relaxation of the contract's evidence
block the UI tolerates (`_mxOk(ev.n)`), to be written into the contract doc. Weight-0: exactly 8 collapsed rows a
player (37 rows, was 55.9), `effect.weight` "0 (rung not shipped)", which is what `_mxZero` folds on; the collapsed
row's `detail.channels` is a list of dicts, which `_mxDetail` renders recursively as a table (read the function). No
NaN, p monotone, units on every fact, the factor-product identity holds on all 2,061 priced markets. Sizes on this
slate: JSON 12.35 MB (factors 5.38 -> 4.16 MB, markets 4.68 MB), per-game piece median 0.98 -> 0.92 MB, max 1.62 ->
1.51 MB -- the builder's numbers. AGREE.

**(d) The report's new text.** Read in full against the pre-fix copy (scratchpad/nflm/mut/repo, a6b6d84e…): the
DK-average arm is labelled a reconstruction in the rule clarification, both tables and the dk_fp section; the DK table
is "as UNDERSTOOD … NOT machine-read", with the FPTS verification path; the Kalshi SEs and by-stat z quote my
kalshi_check.py figures correctly (-0.0138 +/- 0.0041, z -3.3; +0.0018 +/- 0.0052; ast z -2.2, tpm z +2.5; shipped vs
rung 1 +0.0004 +/- 0.0020 / -0.0006 +/- 0.0022); the parity note says 2022-23; the "Rung 6 in production" section
quotes my 4,187-absence composition and the A / B / difference / none rows to the digit, states the leak as ~5% of the
points gain and the train/serve skew as the larger unmeasured gap, calls the measured gain an UPPER BOUND and names the
shadow-bucket follow-up; the limitation no longer says "a few rows"; the strong_fit and past-date behaviour are
described as implemented. The "Review round" paragraph's "catches every real bug" should read "every real bug but two
small ones" (R2, R3); its 88 / 174-line figures for the pre-fix synthetic boards I did not reproduce (CANNOT VERIFY,
immaterial: the mechanism is pinned by the guards and my mutants). Otherwise AGREE.

**Orchestrator O1-O6 after the fixes:** O1 fixed and verified on the real slate (AGREE); O2 fixed (AGREE, see R1);
O3 fixed (`covers` now names both seasons; AGREE); O4 written into the report with both compositions and my
sensitivity (AGREE); O5 measured as the builder states (AGREE); O6 stated in the report (AGREE).

### Findings of the re-check

| id | severity | file:line | what | evidence | fix |
|---|---|---|---|---|---|
| R1 | major (operational, opened by the F2/O2 fix) | nba_matchup.py:1126-1132 (the refusal), 465-474 (`_entries`), nba_matchup_data.py:658-666 (`update` ingests every "post" game up to `today`) | The nightly job runs just after ET midnight = 22:00 MT (deep_cache cadence "daily", `clock._last_midnight_epoch` is ET) and, PC-first, whenever the PC's 19-hour `_due` lands (it drifts 5 h earlier a day: 5 pm, noon, 7 am, 2 am, 9 pm MT). Any run after the 5 pm MT tips are final ingests TODAY's finished games, `through` becomes today, and `board(today)` -- rebuilt by matchups.py every 15 min -- is refused: the Matchups tab goes blank for the late games from ~22:00 MT to midnight most game nights (the pre-fix code served a board whose profiles and hit tables had quietly swallowed the early finals; neither is right) | code reading of deep_cache.register/is_stale_daily, clock.py:17-22, pc_worker.py:373-382, nba_matchup_data.update; the refusal verified on the 10-22 book | Keep today's games out of the book until the MT day is over: in `_entries()` drop entries whose `day` >= `clock.mt_date(now)` (or in `update()` skip events whose game day is today MT); `through` then never reaches the board's date. Guard: a state holding a same-day game still prices that day from the earlier games only |
| R2 | minor (guard gap) | tests/guards_nba_matchup.py `fit_payload`; nba_matchup.py:666-668, 678-681 | The combo and dk_fp `fit_multiplier` formulas are unguarded (27 R+A lines carry the label on 10-23) | mutant `fmc = fm[parts[0]]` passes 65/65 | pin a combo's multiplier = adjusted rate / sum(component rate / component multiplier) and dk_fp's linear form in `fit_payload`, read back from the payload as my recomputation does |
| R3 | nit | tests/guards_nba_matchup.py product-identity check; nba_matchup.py:919-920 | `effect.mult` on shipped rows is not pinned to exp(coef x x) | mutant `* 1.0001` passes | add `abs(mult - exp(coef x x)) < 1e-9` to the identity check |
| R4 | nit (contract doc) | scratchpad matchup_payload_contract.md | `evidence` may omit n / n_eff / reliability when unknown | 5,038 shipped rows carry `kind` alone | write "absent when unknown" into the contract |
| R5 | nit (report wording) | research/reports/nba_matchup.md "Review round" | "catches every real bug" | R2, R3 | "every real bug but the two small ones the re-check names" |

### Verdict of the re-check

**AGREE AFTER ONE FIX (R1).** Every number the first review verified is untouched; the four code fixes do what the
report says on the real slate and under mutation; the guards now cover the team accounting, the shrinkage, the played
rule and the production game day; the report's new text is accurate to the digit. R1 is a two-line change (keep the
current MT day's games out of the replayed book) plus a guard; without it the fix for O2/F2 blanks the owner's board
for the late games from about 22:00 MT on most game nights. R2-R5 with it if cheap. After R1, AGREE TO SHIP.

## R1-R5 CONFIRMATION (Fable, 2026-10-23 09:54-10:05 UTC)

Checked on copies; the fifteen files under review hash identically to my 09:54 snapshot afterwards
(`fable_nba_review/confirm_sha_start.txt`). Hashes match the builder's `nbam_sha_after_r.txt`: nba_matchup.py
a070a47d533fa7c9, tests/guards_nba_matchup.py 46a27871db11b12b, research/reports/nba_matchup.md 2e6081989b5b2805;
params.json 5ba6026508a6624b and coefs.json 43ec107ffb7aaa30 unchanged; data / profiles / fit / parity / walk / kalshi
modules unchanged since the re-check.

- **R1 -- AGREE, done right.** `_entries(season, today=None)` drops every game whose MT game day is `>= today`, with
  `today` defaulting to `mt_today()` = `clock.mt_date(time.time())`, a real America/Denver date (MST and MDT both
  right), not the server's UTC date; `rebuild(..., today=None)` passes it through and `run_job` leaves it at the
  default. The data store keeps ingesting finished games (`update` is unchanged), the book never holds the current MT
  day, so `through` is at most yesterday and `board(today)` is never refused. The new guard builds a store with
  today's early game final and checks the book stops at yesterday and the late game is still priced; it fails on the
  pre-R1 tree (`nbam_oldtree_r`: 67/68) and passes now (68/68, pyflakes clean on nba_matchup.py, the guards, walk.py
  and app.py). My mutants on copies: the day filter removed -- CAUGHT; the boundary moved to `> today` -- CAUGHT. One
  nit: the guard passes `today` explicitly, so the `mt_today()` default itself is unpinned (a mutant defaulting to the
  UTC date passes); trivial to add if wanted. A second nit: `game_day()` labels a game by UTC minus six hours while
  `mt_today()` is the true zone; they differ only for tips between 23:00 and 24:00 MST, which the NBA never schedules.
- **R2 -- AGREE.** The new guard pins each combo's `fit_multiplier` to adjusted rate / sum(component rate / component
  multiplier) and dk_fp's to the DK-weighted linear rate with stl / blk / tov at 1, read back off the payload on a
  board whose components disagree (largest spread > 2%). Mutants "combo = first component's" and "dk_fp = points'":
  both CAUGHT.
- **R3 -- AGREE.** A shipped row's `effect.mult` must equal exp(coef x value) within 1e-5; the decorative mutant is
  CAUGHT. The past-date refusal mutant is still caught after the R1 change.
- **R5 -- AGREE.** The report's review-round paragraph now says 47 real mutants + 1 control with the two small misses
  named (R2, R3), documents R1 (lines 707-713) and the 68 guards. R4 (the contract doc's "evidence keys absent when
  unknown") is the orchestrator's document, not re-checked here.

**The 6-hour cadence (app.py:1733, the orchestrator's change) -- AGREE, with two notes.** `register("nba_matchup",
run_nba_matchup, max_age=6 * 3600, cadence="age")`: the scheduler ticks every 30 min in the one background worker
and starts the job when its marker (deep key `nba_matchup`, saved from the run's summary) is older than 6 h. With the
PC on, `_deep_pc_first` stamps one request (re-stamping is a no-op while pending) and returns DEFERRED, saving
nothing; the PC's `_due` treats a request newer than the server's copy as due now, its `_plain` saves the summary and
the sync uploads it, which answers the request (`pc_pending`: answered once the artifact is newer than the request)
and makes the marker fresh for 6 h; if the PC does not deliver within `_DEEP_PC_WAIT_S` = 45 min (it may be inside
an hour-long board build), the server runs the job itself in a niced child under HEAVY_BUILD, and the PC's later
`_due` sees a copy newer than the request and stands down. The PC's own 19-hour `_due` is thereby superseded by the
server's 6-hour requests while the PC is on; with the PC off the server runs it every ~6-6.5 h. Sane, no double run.
Timing after MT midnight: the first run lands within 6 h + the 30-min tick (+ up to 45 min for a slow PC), i.e. by
~06:30-07:15 MT at the latest, typically earlier; from then until midnight the book holds yesterday's games, never
today's (R1), so an evening run at 22:00 MT no longer blanks the board and a Saturday board reads Friday's games.
Before that morning run, an early visitor's board reads a book through the day before yesterday (the index's 15-min
TTL picks the new book up on the next visit) -- acceptable. Note 1: with the PC off, the server now runs a 250 MB
child four times a day instead of once; the measured peak is unchanged and the HEAVY_BUILD gate serialises it, but
it is four exposures, not one. Note 2: `nba_matchup._entries`'s docstring still describes "the nightly job … 22:00 MT
… the PC's 19-hour due"; the reasoning holds under the 6-hour cadence, the wording is a day old.

**Verdict: AGREE TO SHIP.** R1-R3 fixed and guarded (fail on the pre-fix tree, catch my mutants), R5 worded, no
shipped number touched, the tree byte-identical after my checks, and the cadence change sound. Nits above are optional.
