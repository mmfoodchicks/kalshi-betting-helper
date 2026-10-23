# Review: prop math (independent, Fable)

Deliverable under review: `prop_value.py`, `prop_dist.py`, `kalshi_nba_props.py`,
`tests/guards_prop_math.py`, `research/reports/prop_math.md`. Built by another agent; I did
not build it and did not read the orchestrator's review before writing this. Protocol:
`research/reports/matchup_agreement_log.md`. Review written 2026-10-23 01:30-02:05 UTC.
Nothing under review was edited; the repo tree was checked unchanged (`cmp`) after every
mutation experiment, which ran on copies in scratch.

## 0. What I ran and fetched (all times UTC, 2026-10-23)

Repo state: all five files untracked, uncommitted; `app.py` does not import any of the three
modules and `tests/combo_audit_guards.py` does not call `guards_prop_math.run` (wiring is the
orchestrator's step, not a finding here).

| check | result |
|---|---|
| `python3 tests/guards_prop_math.py` (VIGIL_NO_BG=1, scratch DEEP_CACHE_DIR / ERRLOG_DB) | 187 passed, 0 failed, 0.78 s, exit 0 |
| the same suite with `socket.connect / create_connection / getaddrinfo` replaced by raisers in-process | 187 passed, 0 failed, 0.63 s -- hermetic |
| `pyflakes` on the four .py files | clean |
| the main suite's repo-wide docstring attribute/route scan (lines ~9994-10035) applied to the three modules | clean |
| `except Exception` handlers in the three modules | 5, every one ledgers (`KNBA-fetch`, `KNBA-series`, `KNBA-index-build`, `KNBA-espn-board`, `KNBA-roster`) |
| my own re-derivations (scratch, no repo code): `review_fable/check_value.py` (45 checks), `check_dist.py`, `check_live.py`, `mutate.py` | below |
| live: `docs.kalshi.com/getting_started/fee_rounding` | 01:36:56Z, HTTP 200 |
| live: `/series/KXNBAPTS`, `/series/KXNBAPTSPROT` | 01:36:57Z |
| live: every open page of KXNBAPTS (3 pages, 416 markets), first pages of PTSPROT / 2D / 3D / PR, settled PTS page 1 | 01:42-01:43Z; page 2 answered **429** on its first try and 200 on the retry (the case `_fetch` exists for) |
| live: ESPN scoreboards 2026-10-22 and 10-23, 30 ESPN rosters | 01:42Z, 01:46:48-56Z |
| live: `/markets/KXNBAPTS-26OCT23NYKBOS-NYKJBRUNSON11-25/orderbook` | 01:43Z |
| live: `/series/fee_changes` | 01:49:05Z, `{"series_fee_change_arr":[]}` |
| live: the reader's own `kalshi_nba_props._build()` over all 18 series | 01:49:05-21Z: 15.9 s, 3,836 markets, 14 events, complete, 0 skipped, no ledger note, 2.51 MB pickled |

Scratch paths (not in the repo): `/tmp/claude-0/-home-user-kalshi-betting-helper/2a51b1d4-3290-577b-b97b-fbbf27fce813/scratchpad/review_fable/` (scripts, `dl/` payloads, `mut/` sandbox, `live_build.log`, `mutate2.out`).

## 1. Claim by claim

Legend: **AGREE** (re-derived or re-observed), **DISAGREE** (with evidence), **CANNOT VERIFY**.

### 1.1 Conventions (report §1, module docstrings)

| claim | verdict | evidence |
|---|---|---|
| probabilities fractions; prices / fees / EV cents per contract; ROI fraction of all-in cost; stakes dollars; `units` entry | AGREE | code; guard; `line_value` output carries `units` |
| `P(X >= k)` is the "k+" event; fractional k reads as `ceil(k)` | AGREE on the code; **DISAGREE that the guards pin it** | my check `sf_ge(24.5) == sf_ge(25)` passes; my mutation D2 (ceil -> floor in `_kint`) **survives the whole guard suite** (F5) |
| NegBin `alpha` = variance excess, `Var = mu + alpha mu^2`, Poisson below 1e-6 | AGREE | pmf-moment check 9 + 0.4 x 81; Poisson branch agrees to 1e-10 |
| zero inflation: `mu` is the base mean | AGREE | `from_overall_mean(3.0, pi .25)` -> base 4.0; my pmf second-moment check of `var()` |
| yards: point mass + gamma / lognormal by moments, `k - 0.5`; `yards_from_moments` rejects negative positive-part variance | AGREE | trapezoid integral of my own gamma density from k-0.5 agrees < 1e-6 at 6 thresholds; lognormal moments reproduce (60, 40); inconsistent moments raise |
| copula correlation is the latent normals', smaller for counts | AGREE | measured count Pearson r on 40,000 draws: latent .3 -> .292, .6 -> .585, .9 -> .882 (Poisson 4, 6); Poisson(.3) pair at latent .6 -> .441 (the gap grows at low means, as the docstring says) |

### 1.2 Odds and no-vig (§2)

| claim | verdict | evidence |
|---|---|---|
| American <-> decimal <-> probability, invalid input raises | AGREE | hand values; guard rejects 0, 50, nan, bool, string |
| multiplicative default = `p / sum p`; power `p^k`, bisection; -150/+130 gives .5798/.4202 vs .5840/.4160 at k 1.053; power puts more on the longshot | AGREE | my values: 69/119 = 0.57983, power p_a 0.58397, k 1.0530; `pa^k + pb^k = 1` to 1e-10; also checked (.5,.5), (.95,.07), (.02,.99), an underround (k < 1) |
| one-sided market returns p unchanged with `vig_known False` | AGREE | |
| Kalshi binary: fair value is the YES mid; wider than 20c -> one-sided on the ask | AGREE | `kalshi_reference({4, 54})` -> one_sided .54; `combo_engine._SPREAD_WIDE` is 20.0 (read) |

### 1.3 Kalshi fees (§3)

| claim | verdict | evidence |
|---|---|---|
| all five (and 18) NBA prop series are `quadratic` x 1; `fee_changes` empty | AGREE | live 01:36:57Z: KXNBAPTS and KXNBAPTSPROT `fee_type quadratic, fee_multiplier 1`, KXNBAPTS `last_updated_ts 2026-10-22T19:23:52Z` as the report says; `fee_changes` empty at 01:49:05Z. The other 13: CANNOT VERIFY individually (not re-fetched); the live `_build()` ran `_fee_for` on all 18 and ledgered no `KNBA-fee-changed` |
| the schedule PDF was not read; formula cross-checked against `kalshi.taker_fee_cents` | AGREE (honest) | I did not read the PDF either; `fee_per_contract_c == kalshi.taker_fee_cents x mult` on every integer price |
| rounding is the docs' algorithm: `ceil_6dp` per fill, balance floored to the grid, rounding fee into a per-ORDER accumulator, whole-grid rebates capped so the fill's net fee is never negative | AGREE | I fetched the page (01:36:56Z) and wrote the algorithm from it in `Fraction`s with no repo code: it reproduces the docs' example (-$0.055, model $0.00363825 -> $0.005, carry $0.001361) and the accumulator table, and **agrees with `fee_fills_c` on 400 random multi-level orders** (1-5 levels, integer and fractional quantities, multipliers 1, 0.5, 2) |
| cent-grid collapse: `order fee = ceil_cent(sum ceil_6dp(fill fees))`; one fill = ceil to the cent | AGREE | `fee_order_c(c, n) == ceil(7 n c (100-c) / 10000)` for all 99 prices x 15 sizes (my integer check); 25 @ 44c + 10 @ 45c = 61c (62c per-fill), re-derived by hand: 0.4312 + 0.17325 -> 0.44 + 0.17 |
| float trap: 4 @ 50c is 7c, float math gives 7.000000000000001 -> 8 | AGREE | reproduced: `0.07*4*.5*.5*100 == 7.000000000000001`, ceil 8. The guard text's "/ 71" for 40 @ 50c does not reproduce on this platform (`70.0` exactly) -- nit F9 |
| `contracts=None` smooth, `contracts=N` exact; 1 @ 50c is 2c | AGREE | mutation V14 (always smooth) caught by 3 checks |
| not verified against a real fill | AGREE | nothing to add; still true |

### 1.4 Value, intervals, labels (§4)

| claim | verdict | evidence |
|---|---|---|
| `ev = 100 p - (ask + fee)`, `roi = ev / cost`, `breakeven = cost / 100`, always at the ASK | AGREE | hand values at 5 (p, ask) pairs and on the NO side |
| the NO interval runs the other way: its bad end is the HIGH P(YES) | AGREE | `ev_interval(.55, .65, 52, "no")`: lo = EV at P(YES) .65, hi at .55; through `line_value` the NO interval is `[EV(p_hi), EV(p_lo)]`, `positive_at_lower_bound` reads the p_hi end, and the stake block's interval uses `(1 - p_hi, 1 - p_lo)` -- all three checked |
| `price_used` / `fee` per side, NO ask = 100 - YES bid only when a bid exists | AGREE | live order book confirms the mirror (below) |
| labels defined only from inputs; thresholds are conventions | AGREE | |
| `high_probability` says nothing about price | AGREE | p .95 vs a 98c ask: `["high_probability"]`, EV negative |
| `positive_ev` = fee-adjusted EV > 0 at the executable ask | AGREE as a definition; **see F3** | it fires on an ask-only book, and on a 2c / 68c zero-volume first listing when the model says .80 -- correct by its definition, but the label cannot see `believable`, book kind or quote age |
| with no side given the labels follow the best-EV side | AGREE | |
| "lock" appears nowhere | AGREE | guard grid |
| `refund_adjusted_sides`: `pay_yes = p_yes + p_refund fair_p`, sums to 1, EV linear | AGREE | hand; mutation V11 caught |
| the fair price "sits near the pre-game price" | CANNOT VERIFY, and the data I saw cut the other way (F7) | settled page 1 and the sibling probe: Monk 10+ settled 0.51 (last 0.46), 15+ 0.22 (last 0.34); Nesmith 10+ 0.71 (last 0.00), 15+ 0.39 (last 0.84). The report's §6 says "I did not test how near"; the `refund_adjusted_sides` docstring states "sit near" as if observed |

### 1.5 Distributions and fitting (§5)

| claim | verdict | evidence |
|---|---|---|
| Poisson tail = regularised lower incomplete gamma | AGREE | my log-space direct sums: 6 means (0.01 .. 120) x 8 thresholds (0 .. 300), max abs err < 1e-12; Poisson(1e4) converges |
| NegBin tail = `I_{mu/(r+mu)}(k, r)` | AGREE | 7 (mu, alpha) incl. alpha 25, alpha 1e-5, x 8 thresholds, < 1e-10; alpha 1000 (r .001) stays in [0,1], monotone, pmf sums to 1 - 2e-4 over 200k; mu 400 alpha .05 behaves |
| extreme parameters | AGREE | mean 0 -> tail 0 / pmf(0) 1; k <= 0 and negative k -> 1.0 for every family; negative alpha raises |
| gamma / lognormal yards vs Simpson < 1e-7 | AGREE | my trapezoid integral of the density: < 1e-6 (coarser than theirs); lognormal moments exact |
| `enforce_monotone` PAVA, `[.5,.6,.3,.35] -> [.55,.55,.325,.325]`, input order, equal k pooled | AGREE | **PAVA equals a brute-force weighted least-squares non-increasing fit on 300 random ladders** (every contiguous-block partition enumerated); input order and equal-k pooling checked |
| `negbin_fit_mom` unbiased reliability-weighted variance; underdispersed -> alpha 0 | AGREE | `ss / (W - sum w^2 / W)` re-derived; `n_eff = W^2 / sum w^2` |
| `negbin_fit_mle` score collapses to `sum T_j/(r+j) + W ln(r/(r+m))`; equals the grid argmax | AGREE | derivative re-derived (the mean terms cancel at the profiled mean); my dense grid (step 5e-4) on three samples: 0.3268 vs 0.3270, 0.0157 vs 0.0155, 1.8255 vs 1.8255 |
| degenerate samples return `ok False`, no alpha | AGREE | |
| copula: PSD-tolerant Cholesky, rho 1 works, independent sum = Poisson(13), rho .6 fattens, SE binomial | AGREE | plus: marginals preserved under rho -.5 (within 3 SE); rho -.6 thins the sum's tail; rho = -1 factors; 1.0000001 rejected |
| 20,000 x 3 draws "a fraction of a second" | AGREE | 0.04 s here |
| **sampling a NormalCC through the copula** | **DISAGREE -- F1** | not claimed in the report, but `NormalCC` is a `Dist` and `copula_draws` takes any `Dist`: with the default `lower=None` the mass below -0.5 is dropped from `cdf_table`, the loop runs to `kmax` = 20,000 and `cum[-1] = 1.0` lands that mass on k = 20,000. NormalCC(1, 3): 30.6% of draws are 20,000, mean 6,130 (mu 1). NormalCC(25, 6): 1 of 200,000 draws at 20,000 (expected 2.1 = n x P(X < -0.5)), which adds 5e-6 to every `P(S >= k)` per hit. With `lower=0` the table has 68 entries and the draw's P(0) matches pmf(0) |

### 1.6 Kalshi NBA markets (§6, module docstring)

| claim | verdict | evidence |
|---|---|---|
| open counts per series at 00:54 | CANNOT VERIFY the minute; consistent | my 01:49 build: PTS 415, REB 455, AST 355, 3PT 493, PRA 178, STL 68, BLK 53, STOCK 27, 2D 65, 3D 20, PTSPROT 408, REBPROT 453, ASTPROT 353, 3PTPROT 493; PR / PA / RA / FTM 0 (PR page: `{"cursor":"","markets":[]}`) |
| no integer-cent fields; `*_dollars` strings, `*_fp` sizes; no `no_*_size_fp`; 0 / 1.0000 sentinels | AGREE | 416 / 416 KXNBAPTS markets carry the four dollar fields and both YES sizes; `yes_bid`, `yes_ask`, `volume`, `no_*_size_fp` absent on all; `price_level_structure` = `linear_cent` on all, no sub-cent strings |
| book shapes at 01:09 (2,096 / 1,429 / 92 / 619 believable of 3,617) | CANNOT VERIFY the minute | my 702-market subset at 01:43: two-sided 494, ask-only 201, bid-only 7, believable 186 -- same shape |
| ticker grammar parses every game market; K = floor_strike + 0.5, 0 mismatches | AGREE | 702 / 702 downloaded (PTS, PROT, 2D, 3D) and 3,836 / 3,836 in the live build, 0 skipped; 0 ticker-vs-floor_strike and 0 title-vs-floor_strike mismatches |
| 44 no-jersey codes | CANNOT VERIFY the count | 10 in my subset (`DETIJOESF`, `CHADSCHRODERPG`): the shape agrees |
| ET date; **AWAY then HOME**, 14 / 14 vs ESPN | AGREE | `attach_games` on my downloads: matched 14, unmatched 0, order_ok 14, order_bad 0 (e.g. `26OCT23NOPUTA` -> ESPN away NO, home UTAH) |
| six code differences | AGREE | all six appear in today's events and map |
| `occurrence_datetime` = tip + 3 h (3 / 3) | AGREE, stronger | 14 / 14 games: ESPN tip - (occurrence - 3 h) = 0 s |
| live markets stay listed; `pregame()` True only for ESPN "pre" with the tip ahead | AGREE | CLE@PHI state "in" -> False; the 13 others True at 01:43; `None` without ESPN |
| settlement text (standard, protected, 2D / 3D, early close) | AGREE | `rules_secondary` on live PTS, PTSPROT, 2D and 3D markets match the docstring's quotes nearly verbatim; `can_close_early` True, "This market will close and expire early if the event occurs." |
| DNP: 18 of 697 settled KXNBAPTS scalar, 2.6%, five players, six "Confirmed DNP" | AGREE on the counts; **DISAGREE on "18 events 2026-10-05 to 10-22"** (F7) | the probe file holds 697 markets, 18 scalar, 5 players, 6 "Confirmed DNP" / 12 blank -- but they sit in **4 event tickers on 3 dates (26OCT05, 26OCT08, 26OCT21)**; my own settled page 1 (01:43Z): 3 scalar of 200, Monk 10+ / 15+ / 20+ = 0.51 / 0.22 / 0.08 |
| player matching 137 / 137 by exact name; Kuminga 0 vs 24, Mathurin 9 vs 24; six accent names | AGREE | at 01:46 with 30 fresh rosters (516 players): **142 / 142 players, 702 / 702 markets, every one `team_name`**; jersey agrees 138, disagrees 2 (Kuminga 0 / 24, Mathurin 9 / 24); the same six accent-only spellings |
| index build 28 s, 2.9 MB | AGREE in shape | 15.9 s, 2.51 MB here, no ledger note, under live throttling |
| order book: bids only, ascending; NO bid at y = YES ask at 100 - y | AGREE | Brunson 25+: book's best NO bid 0.45 x 5 -> YES ask 55 x 5 = the list endpoint's `yes_ask_dollars 0.5500`, `yes_ask_size_fp 5.00`; YES bid 0.49 x 1 both ways |
| `walk_book` on the docs' book: 17 @ 44c + 13 @ 55c, 1,463c + 52c (53c per-fill) | AGREE | my algorithm gives the same fee on those two fills |
| `believable` is `kalshi_nfl.believable`'s rule; the one difference is the 0c bid | AGREE, and it is the better reading | on 702 live markets the two disagree on 12, all `(K False, nfl True)`: ask-only books with volume (e.g. bid 0.0000 / ask 0.0300 / vol 574) that kalshi_nfl reads as a 3c-wide two-sided book |

### 1.7 Guards (§7) and house rules

| claim | verdict | evidence |
|---|---|---|
| 187 checks, 0.7 s, hermetic, standalone or `run(ck)` | AGREE | 0.78 s standalone; 187 / 0 with sockets blocked in-process |
| expected values by a route that shares no code | AGREE, mostly | recurrence pmfs, Simpson, integer ceil, Fractions, the docs' example; a few checks compare the function to itself (e.g. `market_ref == combo_engine.market_reference(q)`) which is the right thing to pin there |
| 13 deliberate bugs each caught, control unchanged | CANNOT VERIFY their run; my own: **40 of 44 applied mutations caught** | sandbox copies, bytecode caching disabled (a first run mis-attributed one failure through a same-size / same-second `.pyc`; rerun clean). Survivors: V8 stake sizing on the smooth fee (F6), V13 crossed book (F10), D2 fractional k floors (F5); my control (sort removed) survived as it should. Caught: EV without fee (14 checks), NO interval swapped, no 6dp ceil, 100c ask accepted, NO leg priced on P(YES), wide-book mid, edge_exec without fee, robust flag on the favourable end, refund payoff, no rebate, NegBin beta args swapped, ZI tail, yards continuity, PAVA off, copula ignores rho, table not forced to 1, MLE sign, SE zero, sorted-order output, biased variance, Poisson Q for P, NormalCC continuity, Wilson centre, k = floor, 0c bid kept, opp wrong, partial series returned, NO sizes mirrored wrong, unconsulted game "pregame", team not checked, dead rungs kept, dollars as cents, order check off, HOME-then-AWAY, smooth fee for contracts=N, no-evidence not flagged, negative American sign, asks not flipped |
| pure stdlib; no numpy / scipy / pandas; nothing at import | AGREE | guard's subprocess + my socket-blocked import |
| no silent excepts; stable errlog IDs | AGREE | `KNBA-*`, `PDIST-*`, `PVAL-label`; none swallowed |
| nothing heavy in a request path | AGREE | `index()` and `load_rosters` are network and say so; `line_value` makes no network call unless `lookup_fee=True`; copula is documented as a precompute |

## 2. Findings

| id | severity | file:line | what | evidence | suggested fix |
|---|---|---|---|---|---|
| F1 | **major (blocking)** | `prop_dist.py:202-213` (`Dist.cdf_table`), `prop_dist.py:359` (`NormalCC` default `lower=None`) | Sampling an untruncated `NormalCC` through `copula_draws` silently puts `P(X < -0.5)` on k = `kmax` (20,000): the table omits the negative mass, never reaches `1 - tol`, runs 20,001 steps, and `cum[-1] = 1.0` assigns the shortfall to the last index. No ledger, no error, no guard samples a NormalCC. | NormalCC(1, 3): 30.6% of 20,000 draws equal 20,000, mean 6,130 vs mu 1. NormalCC(25, 6) with Poisson(5) at rho .3, 200,000 draws: one draw at 20,000 (expected 2.1 = n x 1.07e-5), i.e. +5e-6 on every `P(S >= k)` per hit and a garbage sample mean. `NormalCC(25, 6, lower=0)` builds a 68-entry table and samples correctly. | In `cdf_table`, when the loop exhausts `kmax` with `1 - c >= tol`: raise `ValueError` (or ledger `PDIST-cdf-table` and, for a family unbounded below, fold the shortfall onto index 0 and say so in the docstring). Make `NormalCC` default `lower=0` for count use, or have `copula_draws` refuse a `NormalCC` with `lower is None`. Guard: `copula_draws([NormalCC(25, 6)], n=20000)` has max draw < mu + 10 sd and a table under 200 entries; `NormalCC(1, 3).cdf_table()` raises. |
| F2 | **major (blocking)** | `kalshi_nba_props.py:555-590` (`ladders`) | Ladders are keyed `(stat, player_norm)`, so with the default `key=None` a player listed in two games (a back-to-back: Kalshi lists tomorrow's games while today's are on, both dates were listed at 01:43) gets both games' rungs pooled through PAVA into one ladder with duplicated k's; only `monotone_fixed` hints. | Fixture: Brunson 15/20/25/30 (.85/.71/.52/.32) in `26OCT23NYKBOS` plus the same rungs at .32 in a synthetic `26OCT24NYKPHI`: `ladders(idx)` returns `n_listed 10`, rungs `[(15,.585),(15,.585),(20,.515),...]`, `key` = the first game. Per-game calls are right. Not triggered today (0 players in two events in the live build), but back-to-backs are routine. | Key the result by `(key, stat, player_norm)` (or require `key`). Guard: the two-game fixture above must yield two ladders. |
| F3 | minor | `prop_value.py:645-743, 746-796` | `positive_ev` / `positive_ev_robust` do not see believability, book kind or quote age: they fire on a 2c / 68c zero-volume first listing when the model says .80, and on ask-only books. Correct by definition, but it is the "model vs a listing" trap `believable()` exists for, and the owner reads labels as a scoreboard. | `line_value(.80, {2, 68, 32, 98, vol 0})` -> `market_kind one_sided`, `p_market .68`, labels `["positive_ev", "positive_ev_robust"]`. | `line_value` carries `believable` (via `kalshi_nba_props.believable` on the block) and `quote_age_s` (from `market["ts"]`); `line_labels` suppresses or suffixes the EV labels when the book is not believable or the quote is older than a stated age; `label_reasons` says "ask-only book" when `market_kind != two_sided`. |
| F4 | minor | `kalshi_nba_props.py:491-527` (`index`) | A COMPLETE build with zero markets (every morning before listings, the off-season) is treated as a failure: ledgered `KNBA-index-incomplete` with `failed series: []`, never published to boardshare (every builder re-pays ~50 GETs each 120 s), and the docstring's "retried after 30 s" holds only when no stale copy exists (else `max_age`). | Fake `_build` returning complete + empty: `KNBA-index-incomplete failed series: []` ledgered once, not published, next call served the empty index as "stale last good". | Publish a complete empty index (it is the truth); reserve the incomplete path for `failed` non-empty; fix the docstring's retry clause. |
| F5 | minor | `tests/guards_prop_math.py` (section E), report §1 | The fractional-k = `ceil(k)` convention is stated as "pinned by the guards" but no check exercises a fractional k. | Mutation D2 (`ceil` -> `floor` in `_kint`) passes all 187 checks. | Add `NegBin(20, .1).sf_ge(24.5) == sf_ge(25)` and `Poisson(3).sf_ge(0.2) == sf_ge(1)`. |
| F6 | minor | `tests/guards_prop_math.py:684-691` | The stake check ($50 at 50c -> 96) is satisfied by the smooth estimate alone, so the exact-fee adjustment loops are unguarded. | Mutation V8 (adjustment loops removed) survives. `stake_profile(0.518, .6, 50)` is `None` (smooth would buy 1); `stake_profile(51.235, .6, 50)` is 98 (smooth 99; 99 costs 5,124c). | Add those two cases. |
| F7 | minor | `research/reports/prop_math.md` §6 (DNP), `kalshi_nba_props.py:34-40`, `prop_value.py:509-513` | Provenance wording: the 18 scalar settlements are 18 MARKETS in 4 event tickers on 3 dates (2026-10-05, 10-08, 10-21), not "18 events 2026-10-05 to 10-22". And `refund_adjusted_sides`' docstring says the values "sit near the pre-game YES price" as an observation; the report says it was not tested, and the data I saw do not show it. | Probe file recount; settled page 1 at 01:43Z. Nesmith 10+ settled 0.71 with `last_price 0.00`, 15+ 0.39 with last 0.84; Monk 10+ 0.51 / last 0.46. | Fix the counts and dates; in `prop_value` say "the exchange's determination, unpublished; untested against the pre-tip price". |
| F8 | nit | report §6 | "the series object carries only `contract_terms_url` BASKETBALLENTITYSTAT.pdf" | true for KXNBAPTS; KXNBAPTSPROT carries `NBAENTITYSTATCOVERED.pdf` (the protected contract's own document, worth reading). | Name both. |
| F9 | nit | `tests/guards_prop_math.py:614` | "(float math gives 7.000000000000001 and rounds up to 8 / 71)": the 40 @ 50c half does not reproduce (`0.07*40*.25*100 == 70.0` here); the 4 @ 50c half does. | computed | Drop "/ 71" or say "on some platforms". |
| F10 | nit | `prop_value.py:270` | A crossed book (bid > ask) falls to the ask-only branch via `yb <= ya`; unguarded. | Mutation V13 survives. An exchange feed does not cross, so low value. | One check. |
| F11 | nit | `prop_value.py:774-777` | The `_FORBIDDEN` scan runs over the fixed `LABELS` tuple and can never fire. | reading | Harmless; keep or drop. |
| F12 | nit | `kalshi_nba_props.py:486, 606-616` | `by_line` / `market_for_line` key on `player_norm` without team: two same-named players in one game would collide silently. None today. | reading; 0 collisions in the live build | Add `team` to the key when the two differ. |

## 3. Verdict

**AGREE AFTER FIXES.** Blocking: **F1** (untruncated `NormalCC` sampled through the copula puts
its negative mass on k = 20,000, silently) and **F2** (`ladders()` without a game key pools a
back-to-back player's two games). Both are small, both need a guard that would have caught
them. Everything else is minor or a nit, and nothing I re-derived disagreed with the math as
shipped: the fee algorithm matches Kalshi's documented rounding on 400 random orders, the EV
and interval directions (YES and NO) are right, PAVA is the exact least-squares fit, the tails
match direct summation to 1e-10 or better, the MLE is the grid argmax, and every live claim I
could re-observe (AWAY-then-HOME 14/14, tip = occurrence - 3 h 14/14, k = floor_strike + 0.5,
dollar fields, sentinels, settlement text, 142/142 players by exact name, the order-book
mirror, fee_type quadratic x 1, empty fee_changes) held.

---

# Re-check (Fable, independent), 2026-10-23 04:19-04:40 UTC

The builder reports F1-F12 fixed. I re-checked every fix against the new bytes with the same
rules: nothing under review edited (the four files were diffed against my pristine pre-fix
copies: prop_value.py 149 changed lines, prop_dist.py 60, kalshi_nba_props.py 87, the guards
267), all experiments on sandbox copies with bytecode caching off, scratch under
`review_fable/` (`mutate3.py`, `mut_old/` = pre-fix bytes, `mut/` = fixed bytes).

## R.0 Suite and hygiene

| check | result |
|---|---|
| `python3 tests/guards_prop_math.py` | **218 passed, 0 failed, 0.82 s**, exit 0 (builder: 218, 0.8 s -- AGREE) |
| same suite with sockets blocked in-process | 218 / 0 in 0.75 s; numpy / scipy / pandas not loaded |
| pyflakes on the four files | clean (`errlog` import dropped from prop_value along with its only use) |
| main suite's docstring attribute / route scan on the new docstrings | clean |
| broad `except` handlers | 5, four ledger directly (`KNBA-index-build`, `KNBA-series`, `KNBA-espn-board`, `KNBA-roster`), the fifth is `_fetch`'s retry loop (`err = e`, ledgered `KNBA-fetch` after the retries) |

## R.1 Each fix, re-run myself

| id | verdict | what I ran / saw |
|---|---|---|
| F1 | **AGREE, fixed** | `NormalCC` defaults to `lower=0`. My repro: NormalCC(1, 3) through `copula_draws` (20,000 draws) -- max draw 13 (was 20,000), P(0) 0.1850 vs pmf(0) 0.1812 (inside 3 SE), mean matches the truncated pmf's. `NormalCC(1, 3, lower=None).cdf_table()` and `NormalCC(25, 6, lower=None).cdf_table()` raise `ValueError` and ledger `PDIST-cdf-table` (the second has only 1.07e-5 of negative mass -- still refused, correctly, since 1e-5 >= the 1e-12 table tolerance); `copula_draws` with one raises; `sf_ge` / `pmf` on `lower=None` still work. `Poisson(30000).cdf_table()` (mean beyond kmax) raises instead of piling onto k = 20,000. Realistic marginals still build: NegBin(25, .5) 404 entries, NegBin(3, 3) 233, NegBin(5, 25) (the MLE bound) 2,693, ZI 66, Yards gamma 1,019, Yards lognormal 5,166, NormalCC(25, 6) 68, NormalCC(120, 25) 297, Poisson(400) 550. `mean()` / `var()` are now the truncated variable's (NormalCC(25, 6): 25.000287 / 36.076; NormalCC(1, 3): 2.5416 vs latent 1.0), matching my own pmf sums to 1e-9 -- a behaviour change the docstring states. |
| F2 | **AGREE, fixed** | My two-game repro (real Brunson rungs + a synthetic second game at .32): `ladders(idx)` now returns TWO ladders keyed `(game, stat, player)`; game 1 keeps .85/.71/.52/.32, game 2 .32 x 5 with `monotone_fixed False`; `key=` still restricts. |
| F3 | **AGREE, fixed** (semantics below) | `line_value` carries `believable`, `quote_ts`, `quote_age_s`, `book {kind, spread_c, volume, oi}`; `line_labels` replaces an EARNED `positive_ev` / `positive_ev_robust` with `thin_book` and/or `stale_quote`; a -EV line on a thin book gets no qualifier (nothing was withheld); `high_probability`, `strong_fit`, `low_evidence` untouched. 600 s default, inclusive (600 fresh, 601 stale), a parameter (`max_quote_age_s`), unknown age (no `ts`, or `ts` True / nan / "x") = stale. `label_reasons` says "EV label withheld: ..." with the book shape / age. The reader's blocks carry `ts`, `volume`, `oi` under the names read, so the fields populate from a real index (age 30 s on my test). JSON-safe. |
| F4 | **AGREE, fixed** | Fake complete-empty `_build`: served `complete True`, published to boardshare, no ledger note. Fake incomplete build with a stale good copy: the stale copy is served, nothing published. |
| F5 | **AGREE, fixed** | New guard exercises fractional k in five families; my D2 mutation (ceil -> floor) is now caught. |
| F6 | **AGREE, fixed** | New guard: $0.518 at 50c buys nothing, $51.235 buys 98 not 99 -- exactly my two cases; my V8 mutation now caught. |
| F7 | **AGREE, fixed** | I recounted the probe file: 697 markets, **18 event tickers, 180 player-games; 18 scalar markets = 5 scalar player-games (2.8%)**; event dates 26OCT05, 26OCT08 (preseason: James; Tatum, George) and 26OCT20-22 (regular season: 174 player-games, scalar 2 = Nesmith, Monk, 1.1%). All of the builder's recounted figures reproduce. `refund_adjusted_sides` now says UNMEASURED / GUESS and cites the Nesmith counterexample; the reader docstring no longer says "roughly EV-neutral". |
| F8, F9, F11 | AGREE, fixed | both contract PDFs named; "/ 71" removed; the forbidden-word loop runs over `LABELS + QUALIFIERS` at import. |
| F10 | AGREE, fixed | crossed book -> `one_sided` on the ask with a "crossed" note, not believable; my V13 mutation now caught. |
| F12 | AGREE, fixed | Two same-named players in one game: `by_line` drops the clashing rung and ledgers `KNBA-dup-line`; `market_for_line` returns None without `team`, the right book with `team` (Kalshi or ESPN code); `ladders` keeps the first club's ladder and ledgers `KNBA-dup-player`. (Skipping the twin's ladder rather than keying it by club is a choice; it is ledgered, so not silent.) |

## R.2 The orchestrator's question: can a NO-side +EV be withheld for the YES book's thinness?

A Kalshi binary is one order book seen from two sides (NO ask = 100 - YES bid, NO bid = 100 -
YES ask, volume and open interest shared), so the NO face is two-sided, tight and traded
exactly when the YES face is. `book_is_believable` reads the YES face (filling it from the NO
fields when only those are present). I checked this on all **702 live blocks**: the YES-face and
the NO-face answers differ on **0**, and both agree with `kalshi_nba_props.believable` and
`kalshi_nfl.believable` on the cleaned quote (0 disagreements). A block carrying only NO fields
reads the same through the mirror. So a NO +EV is never withheld for a thinness the NO side
would not share -- there is one believability per book, which is right.

The one case worth the owner's eye is a **bid-only YES book**: e.g. LeBron 10+ at a 99c YES bid
with no offer and 131k traded (fixture). The NO ask (1c) IS executable against a deep real
bid, yet the book is one-sided, so a NO +EV there reads `thin_book`. That is the repo's
existing rule (`kalshi_nfl.believable`; `combo_engine.market_reference` gives a one-sided
book quality 0.2) applied consistently -- one party's resting order is not a market opinion
-- and that fixture is in-game, which `pregame()` already excludes. Defensible; I agree with
withholding. Two things for the integrator: (a) `ev_yes` / `ev_no` / `roi` stay in the value
whether or not the book is believable -- the UI must read `believable` before it headlines an
EV number; (b) a qualifier's presence reveals that the numbers WOULD have earned +EV, so the
UI should not style `thin_book` / `stale_quote` like value labels (the reason text says
"withheld", which is the honest framing).

## R.3 Mutations on the fixed modules (sandbox copies, bytecode off)

61 applied (my original 44 less a mis-patterned one, plus K13 re-patterned and 17 new ones
aimed at each fix): **59 caught, 1 equivalent mutant, 1 control survived.** The three former
survivors are caught: V8 stake sizing (2 checks), V13 crossed book (1), D2 fractional k (1).
New: every-book-believable (8), quote age ignored (3), qualifiers never replace (8), default
age 6,000 s (2), thin book still awards positive_ev (6), spread limit ignored (2), volume
ignored (1), `cdf_table` kmax raise removed (1), NormalCC default `lower=None` (2), latent
moments (2), ladders without the game key (1), empty index not published (1), by_line clash
kept (1), ladders merging twins (1), quote ts never read (13). N10 (NormalCC's early
negative-mass refusal removed) survives because the base `cdf_table` still raises after
exhausting kmax -- the behaviour is preserved, only 20,001 steps slower; an equivalent mutant,
not a gap. N15 (`market_for_line` ignoring `team`) turned the suite red by a `TypeError`
traceback (`None["ticker"]`) rather than a FAIL line (nit R-2 below).

## R.4 New findings (none blocking)

| id | severity | file:line | what | suggested fix |
|---|---|---|---|---|
| R-1 | nit | `prop_value.py` `label_reasons` (thin_book text) | On a one-sided book WITH heavy trading the reason reads "one-sided book, 256700 contracts traded or open -- a listing, not an opinion"; "a listing" is wrong for a book that traded 131k. | Say "one-sided book (no offer on the other side)" when volume + oi > 0, keep "a listing, not an opinion" for the zero-volume case. |
| R-2 | nit | `tests/guards_prop_math.py` (F12 `market_for_line(..., team=)` check) | A regression there raises `TypeError` on `None["ticker"]` instead of recording a FAIL with its sentence; the suite still goes red. | `((x or {}).get("ticker") or "").endswith(...)`. |
| R-3 | nit | `research/reports/prop_math.md` §7 | "found four survivors": my review listed three survivors (V8, V13, D2) plus my own no-op control, which was meant to survive. | "three survivors". |
| R-4 | note | `prop_dist.py` NormalCC | `mean()` / `var()` changed meaning (truncated moments); nothing in the repo calls them yet, and the docstring says so. | None; flagged so the matchup engines do not assume `mean() == mu`. |

## R.5 Verdict

**AGREE TO SHIP.** Both blocking defects are fixed and guarded by checks that fail on the old
code (I re-applied the old behaviours myself: caught). The F3 behaviour is correct and
consistent across the two faces of a Kalshi book on every live block I hold; its one
conservative edge (a deep bid-only book reads thin) is the repo's existing rule and is
explained in the reason text. The DNP provenance recount reproduces exactly. Remaining items
are wording nits and one guard-robustness nit.
