# Showdown legacy-vs-constrained structural A/B — not a promotion test

**Run 2026-09-18 22:43–23:02 UTC on the two pinned boards at 12,000 worlds,
four arms each, provenance clean at `dc34b17` (`research/data/sd_model_ab.json`).**
The question was narrow: *does removing legacy's known same-team
over-correlation materially change the lineups and the portfolio Showdown
recommends?* The answer is **yes for the lineups, modestly for the structure
mix, and by 41–54% for every modelled Top-q number the board serves** — with
two corrections to the record, entered first.

## 0. Two corrections to the record, before the result

**0.1 The premise.** The review that ordered this experiment — and the audit
document it responded to — said the served Showdown *portfolios* were "17 to
19 of 20 five-one stacks". That figure was the **top-20 list by Top-1% rank**,
not the 20-entry greedy-cover portfolio. The live A/B's actual portfolios were
mixed:

| Board (live A/B, 2026-09-12) | Portfolio 5-1 / 4-2 / 3-3 | Top-20 list 5-1 / 4-2 / 3-3 |
|---|---|---|
| DEN @ KC, served | 11 / 5 / 4 | 18 / 2 / 0 |
| DAL @ NYG, served | 7 / 6 / 7 | 17 / 2 / 1 |

The number was real; the event attached to it was wrong. It was my error, the
same class this pass exists to catch. Both quantities are reported below,
separately.

**0.2 The first run broke its own fence.** A first run of this study
(21:30–22:27 UTC the same day) built the DEN @ KC served arm on 24 players and
777,056 legal lineups and the three other DEN arms on 23 players and 583,565.
The depth-chart gate that decides who is rosterable (`nfl_dfs._apply_depth`)
reads Sleeper's roster through `nfl_adp.consensus()`, and that was fetched
**live** in every arm's process: Marvin Mims Jr. was listed Out between the
first arm and the second, eight minutes apart. Nothing refused, and the DEN
"portfolio overlap 2 of 20" that run reported was partly a change of universe,
not of football model. That artifact was discarded. The roster is now a pinned
input — 104 Sleeper records for the four teams, captured 22:41 UTC with Mims
listed Out, `research/data/feeds/players_2026_1.json`, sha256 `b8095d57…` —
`sd_board.feeds` refuses to run without it, and `run_all` refuses to write a
board whose arms disagree on players, legal lineups, enterable set or field
beta. Under the pin all eight arms were rebuilt: the seven whose universe had
not moved reproduce the first run to every recorded digit, and the DEN served
arm below is the new one. The pinned DEN universe is the 23-player state
Sleeper reported at capture, not the 24-player state of 2026-09-12, which is
why DEN's legal-lineup count (583,565) differs from the live A/B's (777,056);
the DAL arms sat on one universe in both runs.

## 1. What changed between the arms — and only that

Same pinned DraftKings slate and primary contest (S6 capture), same pinned
Sleeper feeds, same pinned Sleeper roster, same seed per arm pair, same legal
universe, same public field (`softmax(beta × projection)` over the same
lineups — beta 0.4426 on DEN @ KC, 0.3080 on DAL @ NYG in every arm), same
payout grid, same scoring engine. The `_b` arms are the same model one seed
later: the Monte Carlo floor.

The correlation tables — computed inside each arm from its own simulated
arrays, in the moment names the blind validation used — confirm the swap did
what the blind-2025 validation said it would, and nothing else:

| Pair (mean of both sides) | DEN served → constrained | DAL served → constrained | Validated 2025: observed / legacy / constrained |
|---|---|---|---|
| WR1 / WR2 | **+0.553 → −0.007** | **+0.621 → −0.010** | 0.024 / 0.544 / −0.006 |
| WR1 / TE1 | +0.563 → +0.010 | +0.597 → −0.013 | −0.006 / 0.520 / −0.003 |
| WR2 / TE1 | +0.499 → +0.001 | +0.547 → −0.002 | 0.064 / 0.453 / 0.004 |
| QB1 / RB1 | +0.260 → +0.037 | +0.300 → +0.043 | 0.083 / 0.308 / 0.069 |
| QB1 / WR1 | +0.562 → +0.417 | +0.595 → +0.383 | 0.379 / 0.559 / 0.391 |
| QB1 / opp QB1 | +0.163 → +0.012 | +0.205 → +0.036 | 0.235 / 0.181 / 0.033 |
| Team offense / opp offense | +0.305 → +0.070 | +0.317 → +0.084 | 0.262 / 0.323 / 0.082 |
| QB1 / opp DST | −0.441 → −0.365 | −0.389 → −0.315 | not validated |

Mean absolute change, within-team: **0.28 (DEN), 0.32 (DAL)** against floors of
0.005–0.007. Cross-side: **0.16, 0.17** against floors of 0.006–0.009. The
served model on these boards reproduces the validated legacy figures to a few
hundredths; constrained reproduces its own. The instrument measures what it
claims to.

## 2. The portfolio

| | DEN served | DEN floor | DEN constrained | DAL served | DAL floor | DAL constrained |
|---|---|---|---|---|---|---|
| Portfolio overlap with served (of 20) | — | 15 | **2** | — | 14 | **3** |
| 5-1 / 4-2 / 3-3 | 10 / 7 / 3 | 10 / 6 / 4 | **9 / 3 / 8** | 9 / 7 / 4 | 7 / 7 / 6 | **5 / 7 / 8** |
| constrained's own floor | | | 9 / 5 / 6 | | | 6 / 4 / 10 |
| K/DST slots (of 120) | 19 | 19 | 18 | 9 | 10 | 7 |
| Mean salary left | $475 | $420 | $385 | $425 | $370 | $245 |
| Lead captain | Nix 8 | Nix 9 | Nix 5 | Dart 12 | Dart 12 | Dart 9 |
| P(≥1 of 20 in Top 1%), in-sample | 62.3% | 62.6% | **50.1%** | 74.3% | 75.6% | **58.5%** |

**The lineups are replaced.** Reseeding keeps 14–15 of the 20 portfolio
entries; swapping the football model keeps 2–3. Whatever the portfolio is
optimising, its specific answer is a property of the football dependence
model, not of the slate.

**The structure mix moves modestly, toward 3-3, and five-one does not
collapse.** On DEN the five-one count goes 10 → 9 with the reseed floor at 10:
**no movement the floor can distinguish**; what moves there is 4-2 into 3-3
(7 → 3 and 3 → 8, against floors of 6 and 4). On DAL five-one goes 9 → 5 with
the floor at 7, and 3-3 4 → 8 with the floor at 6. Neither the "18 collapses to
4" scenario nor the "stays at 17" scenario happened; the truthful reading is a
shift of one to four entries out of twenty, inside the floor on DEN and clear
of it on DAL. Both models over-select five-one relative to the enterable
universe (five-one is 15% of enterable lineups on both boards and 25–50% of
every portfolio).

## 3. The top-20 list

| | DEN served | DEN floor | DEN constrained | DAL served | DAL floor | DAL constrained |
|---|---|---|---|---|---|---|
| Top-20 overlap with served | — | 17 | 13 | — | 17 | **7** |
| Rank correlation with served (Spearman) | — | 0.82 | **0.46** | — | 0.86 | **−0.17** |
| 5-1 / 4-2 / 3-3 in the top 20 | 19 / 1 / 0 | 20 / 0 / 0 | **18 / 2 / 0** | 13 / 5 / 2 | 14 / 4 / 2 | **7 / 8 / 5** |

Here the two boards part. On **DEN @ KC the top of the ranking stays five-one
under both models** (18 of 20 with constrained): the five-one dominance at the
top of that board is not a product of the within-team over-correlation — it
survives its removal — and on this evidence belongs to the slate and the field
model. On **DAL @ NYG it is** a product of it: 13 → 7, with the ordering
uncorrelated (Spearman −0.17 against a floor of 0.86). One board falsifies the
obvious mechanism for the top of the list and the other confirms it.

## 4. The served numbers

| Best lineup, in-sample | DEN served | floor | constrained | DAL served | floor | constrained |
|---|---|---|---|---|---|---|
| Top 1% | 7.99% | 8.09% | **4.75%** | 11.76% | 12.70% | **5.47%** |
| Top 0.1% | 2.13% | 1.98% | **0.89%** | 1.83% | 2.02% | **0.95%** |

The Top-1% figure the board serves for its best lineup falls by **41% and 54%**
when the football model changes; reseeding moves it by 1–8%. The portfolio's
coverage figure falls by 12 and 16 points. Under the fence these are
sensitivities, not corrections: constrained under-couples the two sides of the
game (its team-offense/opp-offense on these boards is 0.07–0.08 against 0.26
observed), so its lower numbers are not "right" either. What is established is
that every Top-q number Showdown serves is conditional on a covariance
structure the blind validation puts 0.5 away from the season, and moves by
about half when that structure is corrected.

## 5. What this does and does not license

- **Served Showdown strategy stays explicitly model-limited.** The portfolio
  entries, the ranking order on DAL @ NYG, and the level of every Top-q column
  are model-determined to a degree far above the Monte Carlo floor. That was
  the review's condition for "yes", and it is met.
- **Constrained is not promoted and nothing here argues for it.** Its
  within-team half has blind support; its cross-side half is known deficient
  and is visibly deficient in the table above. A portfolio it prefers is not
  thereby a better portfolio.
- **The five-one question got a two-part answer.** The portfolio's five-one
  count does not move on DEN and moves by two beyond the floor on DAL; the
  top-of-ranking mix moves on DAL and not on DEN. Attributing "the five-one
  preference" to the covariance error in general would be wrong.
- **No money conclusion.** Every dollar column is a diagnostic under a shut
  gate.
- **S6 plumbing stays blocked.** This finishing is not a reason to resume it.

Next, as ordered: the field reality check (task #2) once the two contests'
standings exports are available, then put the two pieces together.

## 6. Method notes

Each arm ran in its own process, four at a time (peak RSS 2.09–2.16 GB a
process; scoring 371–517 s a build). The correlation tables are computed in
the arm from its own arrays; an earlier draft could not resolve the
"team offense / opp offense" moment and needed a rebuild pass, which is gone.
The DAL @ NYG pinned capture holds 779,329 legal lineups, against 583,082 when
S5 rebuilt it from feeds and 427,048 when the live A/B ran: DraftKings' pool
for that draft group changed three times, and every arm here saw the same one.
The DEN @ KC pinned universe holds 583,565 against the live A/B's 777,056 for
the reason in section 0.2: Sleeper's roster, not DraftKings' pool.

One production finding fell out of the fence break and is **not** shipped
here, because it is a production change nobody has agreed to yet:
`nfl_adp.consensus()` answers a failed Sleeper fetch with an empty roster, and
`racing._cached` keeps that empty answer for twelve hours, so one truncated
blob switches the depth-chart gate off for every board a worker builds until
then, with no ledger row (the `NFLD-depth` note fires only if `consensus()`
raises, which it never does). Verified by behaviour: with the fetch raising,
two calls make one attempt, the cache holds `{}` at 43,200 s, and a
practice-squad name passes the gate. Recorded as task #5 for agreement.
