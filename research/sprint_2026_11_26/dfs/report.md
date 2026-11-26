# DFS field and portfolio audit (Opus agent, 2026-11-26)

Read-only on the repo; data from origin/sim-history errors/tourney-<dg>.json (snapshot 3a86b72) and research/data/dk_fields/. Scripts beside this file.

## Summary

I could not write `sprint/dfs/report.md`: the harness blocks subagents from writing report files. The full report is below this summary. The scripts and their JSON outputs are in research/sprint_2026_11_26/dfs/ (`concentration.py`, `checks.py`, `swap.py`, `ownership.py`, and `tables.py`, which writes `tables.md`). The board exports are in its `data/` folder. Nothing in the repo was changed.

**A. Concentration (3.6).**
- On the Thanksgiving MEGA, the money list's shown 8 hold LaPorta, Odunze and Emmett Johnson in all 8, Caleb Williams at QB in 6, and 4+ CHI@DET players in all 8.
- Across the top 20 of the lists, pairs share 5.4 of 9 players.
- The 20-entry portfolio spreads more: 3.1 shared players, four QBs, and the Caleb stack in only 2 of 20. It still holds Odunze 65% (field 29.9%), E. Johnson 60% (40.5%) and LaPorta 55% (22.4%). E. Johnson is also captain in 75-80% of the KC@BUF showdown lists.
- The owner's three properties all hold in the code and are pinned by guards:
  - identical lineup adds zero, and a dominated lineup adds zero: `tests/combo_audit_guards.py:15204`
  - union on shared worlds, never added up: `:15209`, `:14977`, `:15219`, `:15223`
- All served coverage curves behave as unions. Adding the 20 probabilities would overstate coverage 1.26-1.55x.
- There is no exposure cap or exposure display on the board.

**B. Why Javonte + Burden beat Gibbs + Raymond (3.5).** The forced-Gibbs board is not exported. The export loop has no `pick=1` (`.github/workflows/error-log.yml:163-165`). But the unpicked board holds the exact 2-for-2 swap, scored on the same worlds against the same field.
- Both lineups project 134.4. On the MEGA, A beats B on top 1% (6.33% vs 4.78%), money (44.2% vs 39.0%) and EV ($25.5k vs $19.5k). It wins every column in all three contests.
- B is the less-owned build (pair field_pct 51.6 vs 67.7), so ownership is not the reason.
- Gibbs is the steadiest of the four (90th percentile only 1.49x his mean). Pair B has 8.0 fewer points of summed 90th percentile (54.4 vs 62.4).
- Gibbs's share of the simulated best lineups per $1k is lowest of the four: 3.51 vs 5.4-5.6.
- The per-world correlations are not exported, so they cannot be read.

**C. Field model (4.1).**
- No real ownership is committed for any of the six current boards: insufficient data.
- Week 6 reproduces the known lead: opt_pct 6.27 vs field_pct 8.28 (top-40 MAE).
- On the two committed showdowns, opt also beat field at captain and flex. The showdown field gives QBs 7-12 points too much captain ownership.
- Duplication is about 100-400x too flat, so every classic row reads 0.0 expected copies.
- The pre-registered week-7 keep/revert test (`research/cl_own_w7b.py`) has never run.

**D. Selection inflation (4.4).**
- Every list and portfolio figure is ranked on the same 160k worlds it is printed from. No board carries a fresh-world read.
- The tab shows the experimental warning. It does not mark the classic portfolio coverage as in-sample (`static/app.js:8627`; the board has no `p_any_basis` at `dfs_tourney.py:3476`).
- The money gate is computed once per board, at the largest contest.

**E. Top recommendations:**
1. Export picked boards.
2. Log the portfolio in hindsight and check its realised any-top-1% rate against the served figure.
3. Label and measure the classic portfolio's in-sample optimism.
4. Show held-out figures for the lists.
5. Add an exposure block to the board.
6. Test an exposure-capped cover on held-out worlds and under perturbed projections.

---

# Full report

Data: `origin/sim-history:errors/tourney-<dg>.json` (snapshot 3a86b72) and `research/data/dk_fields/`. Every figure is a count over the boards' own rows or is quoted as served. Nothing was re-simulated.

## The boards

| dg | kind | contests read | eval / generation worlds | built (UTC) |
|---|---|---|---|---|
| 156932 | classic, 3 games (KC@BUF, PHI@DAL, CHI@DET) | MEGA Millionaire $4,444 x 634; Thanksgiving Millionaire $5 x 594,530; Wildcat $333 x 4,170 | 160,000 / 20,000 | 11-25 20:31 |
| 157066 | classic, Sunday main 9 games | Millionaire $20 x 147,058; Millionaire $555 x 4,504 | 160,000 / 20,000 | 11-26 07:21 |
| 157074 | showdown PHI@DAL | $15 x 31,372 | 60,000 | 11-26 06:43 |
| 157075 | showdown KC@BUF | $25 x 37,647 | 60,000 | 11-26 08:11 |
| 157072 | showdown GB@LAR | $25 x 47,058 | 60,000 | 11-25 23:10 |
| 157086 | showdown LV@CLE | $25 x 47,058 | 60,000 | 11-25 22:49 |

`tourney-156932.json` is the **unpicked** board in both committed versions (`picked: false`). The picked board, built 11-26 05:25, was never exported.

## A. Concentration (3.6)

### Thanksgiving MEGA (156932 / 197897297): exposure %

| player | field_pct | opt_pct | money, shown 8 | money 20 | top-1% 20 | win 20 | EV 20 | portfolio 20 |
|---|---|---|---|---|---|---|---|---|
| Rome Odunze | 29.9 | 31.84 | 100 | 100 | 90 | 90 | 90 | 65 |
| Sam LaPorta | 22.4 | 23.93 | 100 | 95 | 95 | 95 | 95 | 55 |
| Emmett Johnson | 40.5 | 42.67 | 100 | 95 | 85 | 80 | 80 | 60 |
| Luther Burden III | 31.0 | 32.58 | 88 | 80 | 95 | 95 | 100 | 45 |
| Caleb Williams | 23.8 | 24.68 | 75 | 65 | 60 | 55 | 60 | 30 |
| Javonte Williams | 36.7 | 37.8 | 62 | 70 | 60 | 50 | 55 | 50 |
| Jahmyr Gibbs | 35.3 | 33.34 | 0 | 0 | 0 | 5 | 5 | 20 |

### Thanksgiving MEGA: structure of each list

| measure | money, shown 8 | money 20 | top-1% 20 | win 20 | EV 20 | portfolio |
|---|---|---|---|---|---|---|
| Caleb Williams at QB % | 75 | 65 | 60 | 55 | 60 | 30 (Prescott 40, Goff 25) |
| Caleb + Burden + Odunze stack % | 75 | 50 | 55 | 50 | 55 | 10 |
| CHI@DET share of roster slots % (field 39.8, best lineups 40.4) | 51.4 | 50.0 | 56.7 | 57.8 | 56.7 | 46.7 |
| lineups with 4+ from CHI@DET % | 100 | 95 | 100 | 100 | 100 | 70 |
| distinct players used | 22 | 25 | 25 | 24 | 25 | 31 |
| mean shared players per pair (of 9) | 5.64 | 5.43 | 5.46 | 5.25 | 5.39 | 3.11 |
| pairs sharing 7-8 players % | 39 | 18 | 21 | 16 | 19 | 0 |

- **Overlap between lists.** The portfolio shares only 2-4 of its 20 lineups with any list. Top-1%, top-0.1%, win and EV share 18-20 of 20 with each other.
- **Union of everything the board recommends.** 83 lineups over 35 players; Odunze is in 88% of them.
- **Other boards.**
  - Sunday main 157066: the money list's shown 8 all hold Trevor Lawrence. The portfolio has six QBs (2.33 shared players) but holds the Patriots DST at 65% against a 9.1% field.
  - Showdowns: in the money and top-1% lists the top captain holds 50-95%: Kyren Williams 95, Watson 90, E. Johnson 75-80. In the portfolios it falls to 20-50%. Same-captain pairs: 39-90% in the money and top-1% lists against 12-32% in the portfolios.

### What the portfolio logic does

`portfolio_vs_field` (`dfs_tourney.py:1132`) runs a greedy top-1% cover (`greedy_cover` `:1089-1129`). It picks from the 1,200 candidates with the best top-1% on contest 0, and that one shortlist feeds every contest (`:3428`).

| owner's property | code | guard |
|---|---|---|
| identical lineup adds zero | gain = Σ max(0, P−cov); a chosen row is barred (`:1124`); candidates de-duplicated (`np.unique` `:3399`) | `tests/combo_audit_guards.py:15204`, `:14977` |
| lineup whose successes are a subset adds zero | same gain | `:15204` (nested case) |
| union on shared worlds, never added | `cov = np.maximum` (`:1127`), `p_any = cov.mean()` (`:1128`) | `:15209`, `:15219` (monotone grid), `:15223` (one function) |

- **Served data obeys the properties.** On every contest the coverage curve starts at entry 1's top-1%, never falls, never steps up by more than the added entry's own figure, and ends below the sum.
- **Summing would overstate.** 20-entry coverage is served at 52.6-56.0% (classic) and 50.1-63.6% (showdown); the summed probabilities would read 1.26-1.55x higher.
- **Missing:** exposure caps and exposure display on the board. The lineup sheet has a 60% cap (`nfl_dfs.py:2470-2476`) and exposure chips (`static/app.js:6682`).
- **Model risk.** Every world comes from the same projections, so the coverage objective cannot hedge an error in a player's projection.

## B. Javonte + Burden vs Gibbs + Raymond (3.5)

The exact swap is on the unpicked board. Shared core: Caleb Williams, E. Johnson, St. Brown, Odunze, Flournoy, LaPorta, Cowboys.

| MEGA | A: + Javonte + Burden | B: + Gibbs + Raymond |
|---|---|---|
| salary / proj | $49,800 / 134.4 | $50,000 / 134.4 |
| top 1% | 6.33% (list #3) | 4.78% (#32) |
| top 0.1% | 1.669% | 1.233% |
| money | 44.2% | 39.0% |
| first place / EV (both experimental) | 1.657% / $25,471 | 1.224% / $19,450 |
| teams | CHI 3, DAL 3, DET 2 | CHI 3, DET 3, DAL 2 |

The Thanksgiving Millionaire (6.73 vs 5.08) and the Wildcat (6.61 vs 4.99) read the same way: **A wins every printed column**.

The four players (p10 / p90 are the 10th and 90th percentiles of the player's simulated points, `nfl_dfs_sim.py:1136`):

| player | salary | proj | p10 | p90 | field_pct | casual_pct | opt_pct | opt_pct per $1k |
|---|---|---|---|---|---|---|---|---|
| Javonte Williams | 6,800 | 17.6 | 6.3 | 31.9 | 36.7 | 24.8 | 37.80 | 5.56 |
| Luther Burden III | 6,000 | 16.0 | 5.3 | 30.5 | 31.0 | 22.1 | 32.58 | 5.43 |
| Jahmyr Gibbs | 9,500 | 25.9 | 10.9 | 38.5 | 35.3 | 55.4 | 33.34 | 3.51 |
| Kalif Raymond | 3,500 | 7.7 | 1.5 | 15.9 | 16.3 | 12.1 | 16.61 | 4.75 |

What the board's own numbers say:
- **Not the mean, salary or ownership.** Both pairs project 33.6. B is the less-owned pair (51.6 vs 67.7), which should help it, not hurt it.
- **Upside.** B's summed 90th percentile is 54.4 vs A's 62.4. Gibbs's p90 is only 1.49x his mean, against 1.81-2.06x for the other three.
- **Value in the best lineups.** Gibbs is in the simulated best lineup least often per dollar (3.51 per $1k).
- **Stack structure.** A keeps Caleb's WR1 in the CHI stack. B downgrades the stack to the WR3 and makes the run-back three DET players, who split one offence's touchdowns in this engine.
- **Price of the force.** Gibbs's best listed lineup ranks #22 by top 1% (5.19%) against #1 at 6.57%.
- **Robust inside the model.** Top-1% reads about 0.5% high in-sample at 160k worlds; the 1.55-point gap is far larger.

Cannot be determined from the exports: per-world correlations, a one-for-one decomposition (core + Gibbs + Burden is over the cap), the lineups' own score quantiles, and the picked board's own figures.

## C. Field model (4.1)

Real fields (top-40 MAE in points):

| real field | board | field_pct | opt_pct |
|---|---|---|---|
| Week-6 Millionaire (dg 154941; board engine 9, built a minute after lock) | any slot | 8.28 | 6.27 |
| DAL@GB showdown (154959; 24 of 40 matched) | captain / flex | 2.07 / 5.74 | 1.60 / 4.75 |
| WAS@SF showdown (154960; 20 of 40 matched) | captain / flex | 2.27 / 6.62 | 1.76 / 6.34 |

- **Week 6.** Reproduces CLAUDE.md (8.29 there, from rounding). Both columns sit about 5-6 points low: the public's chalk is heavier than either model. Example: Isaiah Likely, 35.1% real against 5.0 field / 11.7 opt.
- **WAS@SF misses.** The unmatched players held 46.5 points of real any-slot ownership: Juszczyk, Burks, McNichols, Watkins. That is the gap `_field_extras` (`dfs_tourney.py:1224`) later closed.
- **Showdown QB captains are over-owned by the field:** Dak 13.6 vs 5.8 real, Love 20.6 vs 13.6, Purdy 32.2 vs 19.9.
- **Duplication.** Real classic pair collision is 3.87e-6 against the model's 1.0e-8. Real showdowns: 5.05e-4 / 6.32e-4, with 93% of entries in duplicated lineups.
- **Current boards (no real field, so insufficient data).**
  - Classic: field_pct vs opt_pct MAE is 0.74 / 0.38, by construction. field_pct vs casual_pct is 4.72 / 1.71 (Gibbs 35.3 vs casual 55.4).
  - Showdown: the softmax field puts QB captains 9-12 points above the best lineups (Dak 22.5 vs 11.5, Love 15.4 vs 5.7, Watson 28.6 vs 17.2).
- **The keep/revert test has not run.** `research/cl_own_w7b.py` has no committed week-7 export and no result file.

## D. Selection inflation (4.4)

- **Stamps.**
  - 156932: `authoritative: false`. The 300k sample does not resolve the 594,530-entry contest (1.98 entries per sampled lineup), and duplication is not exact.
  - 157066: resolves (0.49), but still experimental on duplication.
  - Showdowns: `sd_money_gate` is not authoritative; the portfolio carries `p_any_basis` (in-sample, +2.7% mean optimism).
- **What is in-sample.** Candidates are found on separate worlds (`world_split` `:3067-3078`). Ranking and the portfolio cover then read the same 160k eval worlds (`Xe` `:3422`), and no board carries a fresh-world read.
- **Illustration with week 1's 160k ratios** (fresh/shown: first place 0.37, EV 0.46). These are a different geometry and unmeasured here, especially for the 634-entry MEGA:

| contest | shown EV #1 | if week 1's ratio held |
|---|---|---|
| MEGA $4,444 | $26,936 (+506%) | ~$12.3k |
| Thanksgiving Millionaire $5 | $388 (+7,668%) | ~$178 |
| 157066 Millionaire $20 | $167 | ~$76 |

  Top 1% and money read about 0.5% high in-sample. Showdown showed no inflation at 60k games (`sd_worlds.md`: shown/fresh 0.92-1.00).
- **What the tab shows** (`static/app.js`):
  - Shown: the experimental note with the gate's full text (`:8670`), "experimental" in the headers and list names (`:8604`, `:8647`), and the showdown in-sample label (`:8612`, `:8620-8625`).
  - **Not shown:**
    - the classic portfolio's in-sample status (`:8627`)
    - that the list figures are selection scores; the "(...held out)" line at `:8659` can read as if they were held-out
    - a per-contest gate (one gate per board, computed at `dfs_tourney.py:3536`)
    - any exposure display

## E. Improvements, prioritized (with what accepts or rejects each)

| # | change | where | accept / reject |
|---|---|---|---|
| 1 | Export picked boards (`&pick=1`) | `error-log.yml:163-172` | Accept when the next snapshot carries `tourney-<dg>-pick.json`. |
| 2 | Log all 20 portfolio entries in hindsight (today only the top 3 of win, money and top-1%) | `dfs_hindsight.py:45-46`, `:64-75` | After 15 or more boards, the realised any-top-1% rate falls inside the binomial 95% band of the served coverage; outside it, label the figure miscalibrated. |
| 3 | Stamp `p_any_basis` on the classic portfolio and show "(in-sample)"; measure its optimism by cross-fit | `dfs_tourney.py:3476`, `app.js:8627` | A guard that every board kind carries a basis; serve held-out coverage if optimism is over 5%. |
| 4 | Held-out list figures: rank on 120k worlds, print from 40k | after `:3423` | On the cl_worlds harness: first place and EV within 0.9-1.1 of fresh values, and the top-1% list not worse by more than 1 SE. |
| 5 | Exposure block (player, QB, captain, team, game, stack, shared players) and a warning beside single-entry lists | payload and tab | Reproduces `conc.json` exactly for 156932 and 157074. |
| 6 | Exposure-capped cover | `greedy_cover` variant | Default only if held-out coverage loss is 0.5 points or less and it gains under projection perturbation; otherwise a knob showing its measured price. Final judge is item 2. |
| 7 | Explanation panel: per-row p10/p50/p90/p99 (as probes already have, `:3047`) and the swapped players' correlation with the rest | `row()` `:3433-3460` | Reproduces table B. |
| 8 | Run cl_own_w7b and field_refit; register a showdown optimizer-steer test before reading its export | `research/` | The pre-registered rules decide. |
| 9 | Per-contest money gate | `:3536` | MEGA reads `sample_resolves: true`, Thanksgiving Millionaire `false`. |
| 10 | Per-contest shortlist (low priority; the three contests' portfolios share 18-20 of 20) | `:3428` | Keep only if a portfolio changes by 2 or more entries. |
| 11 | Compare a top-0.1% cover and an EV-sum cover with the top-1% cover | `:1132` | Held-out results only, more than 2 SE. |

### Already implemented

| backlog | implemented (file:line) | still missing |
|---|---|---|
| 3.6 portfolio overlap | `dfs_tourney.py:1089-1177`, `:3399`; guards `tests/combo_audit_guards.py:14977`, `:15204`, `:15209`, `:15219`, `:15223` | exposure caps and display on the board; portfolio in hindsight |
| 3.5 explanations | per-player field / casual / opt (`:3490-3498`); picked rows carry `source` (`:2862`); probes with quantiles (`:2984-3047`) | lineup-vs-lineup explanation; per-row quantiles; picked board export |
| 4.1 field model | optimizer field `:2530-2537`, casual_pct `:3497`, showdown field extras `:1224`; `dk_standings.py` and `dfs_hindsight.py` | week-7 verdict; showdown calibration; duplication |
| 4.4 selection inflation | `:3067-3078`, `:574-601`, `:1551-1581`; `app.js:8604`, `:8612-8625`, `:8647`, `:8670` | held-out list figures; classic portfolio basis; per-contest gate |

Backlog items 2.1 and 3.1 were not described in my brief, so I have not mapped them.
