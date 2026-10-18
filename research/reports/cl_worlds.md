# More simulations for the classic board: what each dial buys

*2026-10-18. Harness `research/cl_worlds.py`, committed (e94df87) and hashed
(sha256 `ceb61f73…`) before the run was read; data `research/data/cl_worlds.json`.*

## The question

The owner, once the classic build fell from 45 minutes to a few: "can we add a
lot more simulations?" A board has three sampling dials:

- the **evaluation worlds** (40,000 of the 60,000; the other 20,000 generate the
  candidates);
- the **sampled field** (300,000 lineups standing in for the contest's entries);
- the **field's calibration draw**.

The test measures whether picking with more worlds or a bigger field picks
lineups that do better on worlds *and* a field the picking never saw.

## Design (from the harness docstring, unchanged)

- **Inputs:** week 1's pinned Millionaire (dg 151307, contest 193028206, 832,342
  entries), run through the real `build_nfl_classic` to its scoring pass:
  `constrained` model, seed 7, 300,000 worlds.
  - The first 20,000 worlds generate the 137,913 candidates (126,296 allowed),
    as the board's do.
  - The other 280,000 are cut into a 40,000-world pre-screen, 160,000 for
    selection (four blocks of 40,000) and an 80,000-world reference.
- **Fields:** nine 300,000-lineup fields, drawn with the build's own beta and
  kappa (0.1273, -3.891).
- **Shortlist:** 3,915 lineups, the union of each list's top 2,000 in the
  pre-screen.
- **Selection:** six configurations, each taking every list's top 20.
- **Valuation:** each pick is valued on the reference, against a separate pooled
  1.2M field, in the list's own metric.
- **Rule:** ship the cheapest configuration that beats production (40k x 300k)
  by more than 2 SE on the **top-1%** or **money** list.
- **Sanity check:** the variant scorer reproduced `run_vs_field` exactly (max
  difference 0.0, block 1 against Q1).

## Results

"Reference" is the mean over replicates of the top 20's value on the reference
(± SD across replicates). "Shown" is the same 20 lineups' own figure in the
selection worlds, which is what a board prints. Cost is scoring work relative
to production.

**Top 1%** (P(top 1%), percent):

| config | worlds x field | reps | cost | reference | shown | diff vs P | z | beats |
|---|---|---|---|---|---|---|---|---|
| P | 40k x 0.3M | 4 | 1.0 | 7.368 ± 0.007 | 7.426 | — | — | — |
| W2 | 80k x 0.3M | 2 | 2.0 | 7.371 ± 0.004 | 7.410 | +0.002 | +0.55 | no |
| W4 | 160k x 0.3M | 4 | 4.0 | 7.373 ± 0.003 | 7.412 | +0.004 | +1.15 | no |
| F4 | 40k x 1.2M | 4 | 3.1 | 7.369 ± 0.006 | 7.424 | +0.001 | +0.15 | no |
| W2F4 | 80k x 1.2M | 2 | 6.1 | 7.374 ± 0.009 | 7.415 | +0.006 | +0.84 | no |
| W4F4 | 160k x 1.2M | 1 | 12.2 | 7.375 | 7.412 | +0.007 | +0.95 | no |

**Money** (P(cash), percent):

| config | reps | cost | reference | shown | diff vs P | z | beats |
|---|---|---|---|---|---|---|---|
| P | 4 | 1.0 | 55.313 ± 0.009 | 55.49 | — | — | — |
| W2 | 2 | 2.0 | 55.321 ± 0.002 | 55.47 | +0.008 | +1.61 | no |
| W4 | 4 | 4.0 | 55.323 ± 0.000 | 55.49 | +0.010 | +2.08 | **yes** |
| F4 | 4 | 3.1 | 55.310 ± 0.014 | 55.49 | -0.003 | -0.37 | no |
| W2F4 | 2 | 6.1 | 55.314 ± 0.012 | 55.49 | +0.001 | +0.09 | no |
| W4F4 | 1 | 12.2 | 55.323 | 55.49 | +0.010 | +0.93 | no |

**Reported beside, never decisive:**

| list | P reference | W4 reference (z) | F4 reference (z) | P shown | W4 shown |
|---|---|---|---|---|---|
| top 0.1% (%) | 1.330 ± 0.009 | 1.333 (+0.67) | 1.328 (-0.24) | 1.365 | 1.357 |
| first place (%) | 0.0052 ± 0.0008 | 0.0058 (+1.47) | 0.0044 (-1.79) | 0.0208 | 0.0158 |
| EV ($ on a $20 entry) | 73.9 ± 8.3 | 83.2 (+1.95) | 71.1 (-0.58) | 228.0 | 181.5 |

## Reading it

**The rule says W4 (160,000 worlds, 300,000-lineup field).** It is the
cheapest configuration that beats production by more than 2 SE on a primary
list.

**But that "beat" is a hundredth of a point, and its spread is understated.**
The money list went from 55.313% to 55.323%. W4's four replicates share their
160,000 worlds and differ only in the field. All four picked the same 20
lineups, so W4's measured spread is zero. The docstring flags exactly this:
replicates that share worlds understate their spread. If W4's spread is taken
as production's instead, the gain is 1.47 SE and nothing beats production. The
pre-registered letter is W4; the conservative reading is "no difference
proven". Both are stated here.

**What the data does support:**

- **More worlds point the right way on all five lists.**
  - The primary lists barely move: top 1% +0.004 points, money +0.010.
  - The two noisy top-prize lists improve about 11-12% in value on fresh
    worlds: first place 0.0052% → 0.0058%, EV $73.9 → $83.2. Neither reaches
    2 SE (z +1.47 and +1.95).
- **More worlds steady the lists.**
  - Top-1% replicates share 19.2 of 20 lineups at W4 against 16.8 at P.
  - Win replicates share 9.5 against 2.3, but W4's shared worlds account for
    part of that.
- **A bigger field buys nothing.**
  - F4 is no better than P on any list.
  - On first place and EV, all three 1.2M configurations sit *below* their
    300k counterparts (-1.79 z for F4). They all share one pooled field draw,
    so this is one draw's effect until shown otherwise; it is not read as a
    finding.

**Selection inflation: the board's own win and EV figures are 2-4x too high.**
The figures a board prints for its top first-place and EV lineups are their
in-sample values. On fresh worlds against a 1.2M field, those lineups held
about:

| | first place | EV |
|---|---|---|
| 40k worlds | a quarter (0.0208% shown → 0.0052%) | a third ($228 → $74) |
| 160k worlds | a third (0.0158% → 0.0058%) | under half ($181 → $83) |

Top 1% and money are barely inflated (7.43 → 7.37, 55.49 → 55.31).

- The board already marks first place and EV as experimental and says to rank
  on top 1% and top 0.1%. Its explanation text now states the inflation too.
- A held-out display (score the listed lineups on worlds the ranking never
  saw) would remove the inflation.

**Calibration spread (descriptive):** four independent 20,000-draw calibrations
landed at:
- beta 0.1295-0.1310 and kappa -3.89 to -3.77;
- top ownership 40.9-41.4% and mean salary $48,973-$49,014.

The build's own draw (beta 0.1273, 40.2%) sits just outside that range. The
targets (38%, $49,400) cannot both be met on this pool, so each draw's landing
point wanders along the compromise curve. Not tested for its effect on picks.

**Caveats:**
- **Win/EV lists:** their picks came from deep in the pre-screen (ranks up to
  61,103), so the 3,915-lineup shortlist limited what those lists could pick in
  every configuration alike. The primary lists' picks all sat within pre-screen
  rank 59.
- **Inputs:** one slate, one contest. The roster pinned with the inputs is a
  later capture than week 1's (sha256 in the data). The question here is
  sampling noise, not week 1's result.

## What shipped

The PC builds classic boards at **180,000 worlds** (20,000 generate, 160,000
score): `pc_worker._CL_SIMS_FULL`.

**Cost**, from the PC's first new-code board (2026-10-18, 60,000 worlds, two
contests, 778 s):
- 60,000 worlds: 778 s (13 min).
- 180,000 worlds, estimated: sims 198 → ~594 s, scoring and portfolio
  451 → ~1,804 s, the rest unchanged. About 2,530 s, roughly 42 min.

**Lock-aware fallback.** A build that would land after kickoff runs the old
60,000 instead (`_cl_sims`: full only when kickoff is more than the estimated
full build plus ten minutes away; the estimate is the last full board's own
time, or the last quick board's stages scaled). On 2026-10-18 a 60,000-world
build pressed at 10:48 MT landed at 11:01, a minute after lock.

**Showdown boards are unchanged** at 60,000 worlds; this test did not cover
them.
