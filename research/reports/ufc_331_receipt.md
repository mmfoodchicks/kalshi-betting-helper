# UFC 331 (2026-09-19): the pre-lock receipt

Capture `research/data/ufc/20260919T210108Z/` (module `research/ufc_capture.py`),
stamped 2026-09-19 21:01:08 UTC, 29 minutes before the 21:30 UTC lock of every
DraftKings contest on draft group 153470 (24 fighters, 12 bouts; the biggest
contests: the $500K UFC 331 Special, $25, 23,529 entries, 150 per user; the
$120K MEGA mini-MAX, $3, 47,562 entries). Seven files, no errors: the slate
and eight contests' detail, the card, every fighter's rating as simulated
(bio adjustments applied) with the flags, the board at production's 15,000
sims with the Kalshi blend, the raw Kalshi map, the trust state, and the
tab's own lineup outputs built by the code as it stands. Shadow universe
`shadow.json` (module `research/ufc_shadow.py`) added afterwards on the same
capture. Nothing in production was changed.

## 1. Every fighter has a model; one is not matched by the builder

No fighter is defaulted (every one carries a pro record). Three are thin
(fewer than three UFC box-score fights): Gable Steveson (1 UFC fight, 4-0
career), JooSang Yoo (2) and Sean Sharaf (2). Steveson's distribution (proj
75.3, ceiling 115.4) rests on one fight's rates shrunk toward league average;
the model has him at 68.8% against a de-vigged market of 92.1%, and the blend
leans to the market (87.4% at this container's 0.20 cap, 90.9% at 0.05).

**Live defect found by the capture:** DraftKings lists "Michael Aswell Jr.";
ESPN and the board say "Michael Aswell". The builder's name matching (exact
normalised name, then last name) resolves 23 of 24 fighters and falls back to
DraftKings' AvgPointsPerGame for Aswell, so tonight's builds project him from
the CSV number, not the simulator, with no per-sample array. Fix later:
strip generational suffixes in `simulate._norm_name`; not tonight.

**Trust state, and a limitation of this capture:** `model_trust` persists in
the server's deep store, which this container does not have, so the captured
blend used the unmeasured 0.20 fallback and every fighter shows confidence
0.2. The server blends with its measured weight (the reviewer's read of the
backtest: 0.05 fitted; `model_trust.weight` shrinks a fit toward 0.20 by
n / (n + 150), so the served number is between the two and cannot be read
from here). The table carries fair win at both weights; the board the tab
served tonight is the server's own unseeded draw, so its lineups can differ
from the capture's in detail while resting on the same model.

| Fighter | Rds | Salary | Model % | Kalshi ¢ | Market % | Fair @0.20 | Fair @0.05 | Proj | Ceil | Flags |
|---|---|---|---|---|---|---|---|---|---|---|
| Giga Chikadze | 3 | 7,000 | 60.7 | 22 | 21.6 | 29.4 | 23.5 | 52.7 | 102.6 | fades market |
| Joanderson Brito | 3 | 9,200 | 39.3 | 80 | 78.4 | 70.6 | 76.5 | 48.3 | 94.4 | |
| Casey O'Neill | 3 | 8,500 | 69.9 | 68 | 67.3 | 67.8 | 67.5 | 61.1 | 99.8 | |
| Eduarda Moura | 3 | 7,700 | 30.1 | 33 | 32.7 | 32.2 | 32.5 | 44.9 | 90.0 | |
| Edmen Shahbazyan | 3 | 8,400 | 61.6 | 63 | 63.0 | 62.7 | 62.9 | 60.4 | 105.6 | |
| Brunno Ferreira | 3 | 7,800 | 38.4 | 37 | 37.0 | 37.3 | 37.1 | 41.4 | 100.6 | |
| Ozzy Diaz | 3 | 6,800 | 10.9 | 19 | 18.8 | 17.2 | 18.4 | 16.9 | 44.8 | |
| Ryan Gandra | 3 | 9,400 | 89.1 | 82 | 81.2 | 82.8 | 81.6 | 97.8 | 125.1 | |
| Michael Aswell | 3 | (Jr. on DK) | 56.7 | 39 | 38.6 | 42.2 | 39.5 | 54.1 | 101.8 | fades market; UNMATCHED in the builder |
| JooSang Yoo | 3 | 9,000 | 43.3 | 62 | 61.4 | 57.8 | 60.5 | 51.6 | 100.8 | thin |
| Tai Tuivasa | 3 | 6,600 | 42.7 | 20 | 19.8 | 24.4 | 20.9 | 39.3 | 100.6 | |
| Robelis Despaigne | 3 | 9,600 | 57.3 | 81 | 80.2 | 75.6 | 79.1 | 51.6 | 102.8 | |
| Marlon Vera | 3 | 7,400 | 48.4 | 36 | 35.6 | 38.2 | 36.3 | 46.3 | 95.0 | |
| Charles Jourdain | 3 | 8,800 | 51.6 | 65 | 64.4 | 61.8 | 63.7 | 47.1 | 93.0 | |
| Alonzo Menifield | 3 | 7,500 | 3.2 | 32 | 31.7 | 26.0 | 30.3 | 12.4 | 27.3 | model far below market |
| Iwo Baraniewski | 3 | 8,700 | 96.8 | 69 | 68.3 | 74.0 | 69.7 | 110.1 | 140.0 | model far above market |
| Gable Steveson | 3 | 9,900 | 68.8 | 93 | 92.1 | 87.4 | 90.9 | 75.3 | 115.4 | thin (1 UFC fight) |
| Sean Sharaf | 3 | 6,300 | 31.2 | 8 | 7.9 | 12.6 | 9.1 | 38.6 | 98.5 | thin |
| Patricio Pitbull | 3 | 7,300 | 35.1 | 30 | 29.7 | 30.8 | 30.0 | 40.8 | 92.2 | |
| Dooho Choi | 3 | 8,900 | 64.9 | 71 | 70.3 | 69.2 | 70.0 | 64.0 | 107.6 | |
| Arman Tsarukyan | 5 | 9,100 | 48.0 | 73 | 72.3 | 67.4 | 71.1 | 62.0 | 107.3 | co-main |
| Mauricio Ruffy | 5 | 7,100 | 52.0 | 28 | 27.7 | 32.6 | 28.9 | 62.8 | 104.9 | co-main; fades market |
| Alexandre Pantoja | 5 | 7,900 | 30.9 | 42 | 41.6 | 39.4 | 41.0 | 69.2 | 118.3 | main |
| Joshua Van | 5 | 8,300 | 69.1 | 59 | 58.4 | 60.6 | 59.0 | 82.8 | 116.4 | main |

Model % is the raw power-rating simulation; Market % the de-vigged Kalshi
pair; Proj and Ceil the unblended simulator distribution (the blend resamples
them toward the fair win, see section 2).

## 2. The cross-bout reblend defect: reproduced at HEAD, fix ready, not deployed

`simulate._reblend_bout` (unchanged since the reviewer's snapshot) builds the
resampling index list as every win draw followed by every loss draw and never
reorders it. `dfs_sim` and `_contest_sim` score a lineup by picking ONE index
across every fighter on the card, so an ordering shared by every bout (wins
first) is read as a joint world in which every A-side wins together.

Reproduced on five independent synthetic bouts (2,000 samples each, values
encoding the index so the pairing is checked exactly): cross-bout correlation
of the win indicator 0.47 to 0.78 after the served reblend, 0.00 before it,
0.02 to 0.04 with the index list shuffled once per bout and the same shuffled
list applied to both fighters. Within a bout the pairing holds either way
(entry k of A is entry k of B, exactly one side banks the win) and each bout's
win share lands on its blended target.

The fix is one line after the list is built, `rng.shuffle(idx)`, with a
comment that says what was measured; the guard (drafted, validated to fail on
HEAD and pass with the fix) pins the pairing, the win shares and an absolute
cross-bout correlation under 0.08. Both are in `research/reports/ufc_reblend_fix.patch`.

What it means for tonight, before any decision: the single lineup under the
projection objective is unaffected (a mean does not depend on sample order),
so the tab's projection lineup stands as built. Every joint number is not:
lineup floors, medians and ceilings, the leverage objective (it scores joint
worlds) and every contest win%, cash% and ROI are computed on manufactured
worlds. Those were already experimental (section 4).

Deploying the fix is a push, and a push restarts the app and blanks the UFC
board while it recomputes; in the last minutes before lock that is the wrong
trade for a fix that does not move the projection lineup. Recommendation: the
fix and its guard go in the next push after lock. The shadow analysis below
already runs on corrected worlds.

## 3. Shadow universe on corrected worlds: the rule bites, on the main event

Both universes scored on the captured board re-blended with the per-bout
shuffle (research only). Production's rule: never both fighters of one bout.
DraftKings allows it. The two five-round bouts are Tsarukyan vs Ruffy and Van
vs Pantoja.

| Build | Universe | Lineup | Proj | Joint floor / median / ceiling | Same-bout pair |
|---|---|---|---|---|---|
| projection | rule | Steveson, Gandra, Chikadze, Ruffy, Baraniewski, Pantoja | 440.1 | 340.7 / 442.3 / 540.7 | none |
| projection | DK-legal | Gandra, Baraniewski, Shahbazyan, Van, Pantoja, Ruffy | 459.1 | 358.5 / 462.3 / 557.9 | Van + Pantoja (5 rounds) |
| ceiling | rule | Gandra, Ruffy, Choi, Baraniewski, Ferreira, Pantoja | 423.7 | 310.0 / 417.4 / 540.9 | none |
| ceiling | DK-legal | same as projection DK-legal | 459.1 | 360.2 / 461.6 / 557.3 | Van + Pantoja |
| leverage | rule | Tuivasa, Gandra, Brito, Menifield, O'Neill, Pantoja | 353.9 | 242.4 / 352.6 / 459.1 | none |
| leverage | DK-legal | Brito, Yoo, Van, Pantoja, Ferreira, Menifield | 351.5 | 246.7 / 349.1 / 469.2 | Van + Pantoja |
| portfolio of 20 | rule | 20 lineups | | median joint ceiling 519.7 | 0 of 20 |
| portfolio of 20 | DK-legal | 20 lineups | | median joint ceiling 524.5 | 17 of 20 carry a pair; 12 of 20 a five-round pair (Van + Pantoja, Tsarukyan + Ruffy; also O'Neill + Moura, Steveson + Sharaf) |

Every bout's own pair in the joint worlds, best first: Pantoja + Van
($16,200; five rounds) projects 155.7 with joint floor / median / ceiling
106.8 / 151.5 / 209.5 against 236.6 for the sum of the two solo ceilings (one
of them always loses, so the pair's ceiling is 27 points under the naive sum,
and still the highest joint value of any pair on the card); then Menifield +
Baraniewski (127.8; joint ceiling 170.7), Tsarukyan + Ruffy (124.0; 159.4),
Diaz + Gandra (115.3; 137.5), Steveson + Sharaf (113.9; 135.8).

Reading: on this card the rule is not idle. Lifted, the optimizer takes both
main-event fighters under every objective, and the corrected joint worlds
still put that lineup above the rule's on every quantile. The five-round main
event is exactly where two high-volume strikers can both score without the
win bonus. Whether to allow it is a strategy call with one slate of evidence;
no rule changes tonight, and the pair's real value is the kind of thing the
post-event field and scoring study should grade.

## 4. What the tab shows tonight, as built here

Single lineup, projection objective (the rule universe; the same lineup as
the shadow's rule row): Steveson, Gandra, Chikadze, Ruffy, Baraniewski,
Pantoja, $50,000, projection 440.1. Its contest read against the $500K Special
(sample field 666, 500 iterations): win 0.0%, cash 44.3%, ROI -24.8%. The
leverage lineup: Steveson, Brito, Menifield, Moura, Ferreira, Pantoja. A
portfolio of ten was built as well (all in `lineups.json`).

Treat win%, cash% and ROI as diagnostics, not money authority: the field is a
hand-built softmax of points per dollar with a favourite multiplier, filtered
to lineups projecting at least 84% of our own best build (so the modelled
public depends on what we evaluate), the rank tail is a Normal approximation,
there is no historical UFC lineup-frequency calibration, no salary-left
calibration, no duplicate model and no tie-aware exact payout. The lineup
ranking itself is usable. And the joint numbers above were computed on the
manufactured worlds of section 2.

## 5. Recorded, not acted on tonight (the reviewer's other findings, all confirmed at HEAD)

- The backtest is not fully point-in-time: `fighter_rating(as_of=...)`
  filters box-score fights by date but calls today's `career_profile()` for
  durability and strength of schedule, and `ufc_backtest.predict()` never
  applies `_apply_bio`, which the live board does. The 0.05 fitted weight is
  therefore not a clean validation of the live model. Weight unchanged.
- The fantasy-point simulator has been validated on fight-winner
  probabilities against the market, never on DraftKings point distributions:
  strike counts are independent Poissons given duration, the finish
  propensity is shared between winners, control time and advances are
  heuristics of takedowns, and arrays are rounded to 0.1 where DraftKings'
  control pays 0.03 per second. A train / holdout stage against historical
  DraftKings scoring is the next study.

## 6. What the owner does

Enter at least one contest before 21:30 UTC (5:30 pm ET); the $3 mini-MAX or
the $25 Special give the largest public fields. After the event, export the
full standings ZIP and hand it over. That is UFC's missing piece, the real
public field, and the start of UFC's version of S7: reconcile every entry,
ownership, salary use, same-fight stacking, exact duplicates, favourite and
underdog construction, concentration; then the sampler against reality.
