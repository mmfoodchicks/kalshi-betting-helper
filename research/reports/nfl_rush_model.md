# The RB and QB rushing model: design and draft rule

Status (2026-10-29): **REGISTERED 2026-10-29T02:13:45Z, rule block sha256 `92a99b8e791a7a98ec9e6da849ad9b07b751fd5cab6bd51c03f70d13fc0fbe61`** (`research/data/nfl_rush_model/prereg.json`, the file itself 907f0bed…; the frames val 906e9a7e…, test b3a28947…; 35 decisions), after the Sonnet, Opus and Fable round-4 confirmations. The window starts with week 8's Thursday game (2026-10-30 00:15 UTC). Next: `validate`, `check2026`, `export` once the registration commit is pushed; the prospective test after week 11 closes (Friday 2026-11-27 at the earliest; the runbook after the block).

- No 2025 or 2026 OUTCOME has been read for this model. Those rows have been
  built, counted and audited for missing features (counts only; the audit
  loads no outcome column). The plumbing run (section 6) replaced every held
  outcome with a random 2023 row's.
- The rule below was hashed on 2026-10-29, before any 2025 row was scored
  (every command refused until then).
- Every metric in this file is from the FITTING seasons 2023-2024, or is
  quoted from another report.

**Files (this phase):**
- `nfl_rush.py`: production, pure Python. The compound distributions, the
  RB / QB components, the longest rush, the breakdown, the correlations, the
  adapter (`features`, `availability_view`, `team_blocks`, the comparables),
  the ship gate and the board model API (`in_universe`, `plan`,
  `family_dist`, `linear`).
- `nfl_matchup.py`: the board hook (`_rush_env`, `_rush_board`,
  `_rush_player`), inert without `coefs.json` (section 5).
- `research/nfl_rush_model_explore.py`: the design measurements.
- `research/nfl_rush_model_frame.py`: the frame. Every feature is computed
  by production's adapter on the data layer's own as-of snapshot; the
  dataset is read by declared column name (`ID_COLS`, `BASELINE_COLS`,
  `OUTCOME_COLS`, `PLAYED_COLS`, `SELECTION_COLS`), outcomes only as `y_*`;
  `feature_lists_md` and `thresholds_md` generate Q3a and Q4's table.
- `research/nfl_rush_model_fit.py`: the distribution fit, `traincheck`,
  `traincheck_frame`, `audit`, `register` (refuses without the go),
  `registered-tree` (the stored code, for the prospective run), and the
  hold (`HELD` + `prereg_status`).
- `research/nfl_rush_model_rule.py`: the decision machinery (Q4-Q6):
  `validate`, `check2026`, `export`, `prospective`, `capture-missing`.
- `research/data/nfl_rush_model/inputs/nfl_props_2026_w06.json`: the week-6
  props log the test frame reads. It carries Kalshi's prices, so it is kept
  out of the public repository (.gitignore); its byte-identical copy is
  sim-history's `props/nfl_props_2026_w06.json` (sha256 9d951cf7…).
- `research/data/nfl_rush_model/`: `design_explore_2023_2024.json`,
  `design_traincheck_2023_to_2024.json` (section 1, 2023-24 only); later
  `prereg.json`, the decision records and `coefs.json`, written only by the
  registered commands.
- `tests/guards_nfl_rush_model.py` (section 8).

## 1. What the training seasons say (2023-2024 only)

| measurement | 2023-24 |
|---|---|
| per carry, RB (23,524 runs) | mean 4.26, sd 6.05, skew **4.16**, 17.9% at or below 0, 10.1% 10+, 2.2% 20+ |
| per designed QB run, not a sneak (1,148) | mean 4.21, sd 6.39, skew 2.16, **33.2% at or below 0** |
| per QB sneak, FTN (567) | mean 1.48, sd 1.02 |
| per QB RPO keep, FTN (90) | mean 4.74 (too few to stand alone; it sits inside the designed runs) |
| per scramble (2,097) | mean 7.51, sd 6.01, skew 1.92, 0.05% at or below 0, 26.8% 10+ |
| per kneel (833) | mean -1.12 |
| a QB's rushing attempts (1,150 QB-games, 10+ dropbacks) | designed 25.1%, sneaks 12.3%, scrambles 46.6%, kneels 16.0%; 5.1% of dropbacks are scrambles |
| kneels per team-game by pre-game spread | 7+ favourite 1.06, 3-6.5 favourite 0.97, pick 0.90, 3-6.5 underdog 0.50, 7+ underdog 0.48; winners 1.31, losers 0.22 |
| kneels in Sleeper's box | counted as rushing attempts on **504 of 504** QB-games with a kneel (official scoring; sacks are not rushes) |
| Sleeper's revised QB rushing line, actual minus projected | favourites +0.05 to +0.16 attempts, -0.2 to -0.5 yards; 7+ underdogs +0.44 attempts, **+3.4 yards** (trailing teams scramble more) |
| RB carries' over-dispersion by Sleeper's carry line | alpha **0.29** at 5-7 projected, 0.18 at 7-11, 0.12 at 11-13.5, 0.06 at 13.5+ |
| within-team residual correlations (actual minus Sleeper) | QB rush attempts vs RB1 carries +0.082 (1,087 team-games); QB rush TDs vs the RBs' rush TDs -0.050; an RB's rush vs receiving yards -0.023 (1,674) |

**The shape question, fit 2023 and checked on 2024** (inside the fitting
seasons, like the first model's traincheck). The candidate is a compound:
carries ~ NegBin times a per-carry kernel rescaled to Sleeper's yards per
carry, against the shipped point-mass + single-CV gamma.

- **RBs** (837 rows with 5+ projected carries): -0.090 +/- 0.015 nats a row.
  - Lines 20-30, 10+: the gamma reads 0.873, the compound 0.792, and 0.678
    hit.
  - Lines 50+, 40+: 0.716 / 0.748, and 0.760 hit.
- **QBs**, built from population shares (424 starters with 2+ projected
  attempts): -0.167 +/- 0.040.
  - Lines 10-20, 25+: 0.131 / 0.181, and 0.288 hit.

The compound is closer everywhere, but not yet right at the low RB lines.
Its count used ONE carries alpha (0.094), so the line-dependent alpha was
added to the design.

**The same check through PRODUCTION's engine** (`nfl_rush.Compound` with the
fitted line-dependent alpha curves; `python3 -m research.nfl_rush_model_fit
traincheck`, fit 2023 and scored 2024):

| | rows | compound minus single CV | calibration cell: hit / single CV / compound |
|---|---|---|---|
| RB rushing yards | 837 | **-0.120 +/- 0.023** | lines 20-30, 10+: 0.678 / 0.873 / **0.731**; 15+: 0.569 / 0.727 / 0.610; lines 50+, 40+: 0.760 / 0.716 / 0.762 |
| QB rushing yards (position shares) | 441 | **-0.181 +/- 0.050** | lines 10-20, 25+: 0.272 / 0.131 / 0.191; but 10+: 0.558 / 0.560 / **0.464** |

- **Fitted curves (2023):** RB carries alpha = 4.37 x mean^-1.51, which is
  0.38 at 5 projected carries and 0.07 at 15; the kneels by margin read
  0.40 (7+ underdog) to 1.15 (7+ favourite).
- **What the QB row seemed to show:** the compound built from POSITION
  shares under-calls a quarterback's 10+ on small lines, because a pocket
  passer and a designed runner both get the same split. The obvious fix
  was the player's own as-of shares from the data layer. Through the frame
  (below), that fix made things worse.

**The same check through the FRAME** (`python3 -m
research.nfl_rush_model_fit traincheck_frame --frame
rush_frame_val.csv.gz`). Every input is what the rule will read: the data
layer's snapshot through production's adapter, the participation mask
(Q1), and the pre-game spread for the kneels. Fit 2023, scored 2024:

| arm (2024 rows) | rows | minus the single CV | minus the position-share compound |
|---|---|---|---|
| RB: compound | 839 | **-0.122 +/- 0.023** | |
| QB: compound, POSITION shares | 441 | **-0.181 +/- 0.050** | |
| QB: compound, his OWN shares (429 rows have them) | 441 | -0.160 +/- 0.046 | **+0.021 +/- 0.009** (worse) |
| QB: own shares, alpha refit on own-share expectations | 441 | -0.154 +/- 0.043 | +0.027 +/- 0.012 |
| QB: own shares, own alpha, designed kernel by QB type | 441 | -0.160 +/- 0.045 | +0.021 +/- 0.010 |

- **A fix, 2026-10-24.** The QB shares and component curves dropped every
  QB-game with no rush at all (82 of 1,087 in 2023-24: rows selected on the
  outcome). They now enter as zeros (Q2). The QB figures above are the
  re-run; the fix moved each QB comparison by 0.0005 nats or less and the
  shares' fit rows from 504 to 544 (2023). The blend and designed-runner
  figures below are from before it.
- **Blending did not help.** On the same 2024 rows, the shares at weight w
  on his own split and 1 - w on the position's, minus w = 0: +0.0037
  (0.25), +0.0085 (0.5), +0.0142 (0.75), +0.0207 (1). With the designed
  kernel by type, the same weights read -0.0004 +/- 0.0026 at 0, then
  +0.0023, +0.0064, +0.0116 and +0.0176.
- **Where it loses:** in the designed runners (designed share 0.4+),
  +0.043 +/- 0.021. The own-share compound puts P(yards <= 0) at about
  0.18-0.22 against 0.10-0.15 hit, across the line bins.
- **It is not a bias in the shares.** His expected component counts match
  what he did; Lamar Jackson's designed runs read 4.95 a game against 5.04.
  The first look at bias was an unweighted-versus-pooled artefact.
- **The kernel by QB type is not the answer either.** It is real: a runner's
  designed run reads 23.7% at or below 0 with a mean of 4.59 yards, everyone
  else 49.2% and 2.88. But it did not rescue the own-share arm.
- **Decision (inside the fitting seasons, before any 2025 row):** the
  compound (SHAPE) splits a QB's attempts by the POSITION's shares with one
  pooled designed-run kernel (`nfl_rush.QB_SHARES`). His own split is on the
  board as information, and the rule does not score it.
- **The traincheck does not move with the participation mask.** The
  shares, Sleeper's lines and the pre-game spread are not
  participation-derived, so the numbers above are the same through the
  frame built with and without it.

All of this is design evidence, not a result: the rule decides on 2025.

## 2. Design

### 2.1 Opportunities separately from efficiency

**Running backs.**
- Carries C ~ NegBin(mu_C, alpha(mu_C)), with mu_C = Sleeper's carries x
  exp(opportunity terms).
- Per-carry yards come from the 2023-24 RB kernel, rescaled to mean
  ypc = Sleeper's yards / carries x exp(efficiency terms).
- Rushing yards is the compound of the two. Opportunity terms never move
  ypc and efficiency terms never move carries, so a projection says WHY it
  moved.
- Receiving is the same construction, receptions x a per-catch kernel; it
  is its own market, with its own terms.

**Quarterbacks: four components added.**
- **Kneels.** K comes from the game script (the table above, linear in the
  pre-game expected margin), at the kneel kernel (about -1 yard).
- **Designed runs, sneaks, scrambles.** The other A - K of Sleeper's
  attempts A are split by the POSITION's shares, fitted on the fitting
  seasons (`nfl_rush.QB_SHARES`). Yards per designed run and per scramble
  are scaled by one common factor, so the four components' mean IS
  Sleeper's yards line; sneaks and kneels keep their own.
  - The QB's own as-of split (the data layer's decomposition) was the
    design's first choice. Through the frame, inside the fitting seasons,
    it priced his rushing yards WORSE than the position's split, and every
    blend toward it was worse too (section 1). The board shows it as
    information; it moves no probability.
- **Matchup terms.** They act on a component's count (designed runs, the
  scramble rate) or its yards per attempt, never on the total.
- **What we can and cannot see.** An RPO keep is a designed run (FTN flags
  it). An RPO that becomes a pass is not a rush. Read-option keeps are not
  identifiable in free data; the model says so and has no such term.

**Rush + rec, anytime TD, longest rush.**
- Rush + rec yards is the two compounds added, independent; the measured
  correlation is -0.023.
- Anytime TD is Poisson(rush TDs + rec TDs).
- Longest rush (KXNFLLONGRSH) is P(longest >= k) = 1 - prod G_j(P(X_j < k)),
  over the same parts.

### 2.2 The distribution engine (`nfl_rush.Compound`)

Each part's kernel is placed on a 1,024-point integer grid (-128 to 895
yards) and transformed by a pure-Python radix-2 FFT (1.7 ms). The NegBin
generating function is applied pointwise, and one inverse transform gives
the exact compound pmf.

- **Accuracy:** against brute-force convolution, the pmf agrees to 6e-11,
  and the analytic mean and variance agree with the grid's to 1e-12.
- **Cost:** 5.6 ms for a two-part compound, so about 30 ms for a QB's four
  parts and both markets.
- **No moment matching, no normal.** The explosive tail, the kneel mass
  below zero, and a backup's chance of a dud come from the measured kernels.
- **Rescaling a kernel to a new mean** splits each value's mass between the
  two integers around s x, which keeps the mean exact.

The fitted parameters live in the committed model: the kernels by role, the
count alpha(mean) curves (the passing-yards decile template), the kneel
table and the QB position priors.

### 2.3 The groups (the owner's ladder, in his order)

| rung | RB rushing (carries / ypc) | QB rushing (component counts / yards per attempt) | RB receiving |
|---|---|---|---|
| 1 Sleeper | Sleeper's line in the rule's own rung-1 family (NegBin; point mass + gamma), its dispersion refit on the fitting seasons | same | same |
| 2 OVD overall defense | run defense allowed, opponent-adjusted: YPC, success, EPA, stuff rate, 10+ rate; run rate faced | run defense allowed; pass rush (sacks + QB hits per dropback, pbp; blitz, FTN) | EPA per dropback and catch rate allowed |
| 3 SPD position defense | RB rushing allowed (adjusted per game and per carry) | QB rushing allowed per designed run and per scramble, ADJUSTED for the rushing propensity of the QBs faced | RB targets and yards allowed (adjusted) |
| 4 CFP concept / front PROXY | direction: his edge / interior share x the defense's edge / interior YPC allowed (edge = `run_gap` end; a proxy; DEAD: the defense side shrinks to its prior, so the term has zero training SD and run direction is untested, Q2); box: his light / heavy-box YPC split x the defense's box mix (participation prior seasons, FTN box in season); OL vs front: the team's line yards (proxy) x line yards and stuffs allowed, plus his offensive linemen listed Out / Doubtful on the week's report (injuries). The contact terms (PFR's yards after contact, broken tackles, missed tackles, yards before contact) are NOT fitted: data licence, Q10 |  the scramble interactions MEASURED: his scramble rate under pressure vs clean x the defense's pressure rate; vs blitz; vs man / zone; vs single / two-high; containment PROXY (scramble yards allowed per scramble, adjusted) | screens / checkdowns: his screen share x the defense's blitz rate; his target rate under pressure x its pressure rate; man / zone target split x the defense's man share (prior seasons) |
| 5 SHAPE (no term) | the compound (carries x per-carry kernel, line-dependent alpha), the terms already accepted re-expressed on carries and yards per carry | the four-part compound on the position's split (designed, sneak, scramble, kneel), the accepted terms on their components (`QB_COMPONENT`) | the compound (receptions x per-catch kernel) |
| 6 DEC opportunity | game script (margin, total), team neutral run rate, usage trend (pbp / participation appearances), the room's backs listed out | game script on both components | game script |
| 7 FULL | comparable defenses (his games vs similar run defenses), the team's QB keep share, YPC allowed x game script | comparable defenses, scramble split x game script | comparable defenses |
| TD (own groups) | RB: FULL (red-zone run rate, QB keep share) | QB: FULL (his goal-line rush rate); SPD dead | - |

Every feature is a deviation from the league at the same cutoff, shrunk by
n / (n + k) with k MEASURED. The k come from 2018-2024 for the validation
frame and 2018-2025 for the test and production frame, as in the first
model's R2.

Anything the free data cannot carry is either a labelled proxy (edge runs,
line yards, containment) or absent: run concepts, fronts as a scheme,
alignment, spies, read option, blocking grades.

### 2.4 The board payload (what changes for RB and QB rows, once a model ships)

Nothing changes until `research/data/nfl_rush_model/coefs.json` exists (section 5).
With it:
- **`markets`** (RB / QB rows, each in its Q1 universe):
  - a market whose group shipped replaces the first model's block: RB
    carries, rushing yards, receptions, receiving yards, anytime TD; QB
    rushing attempts, rushing yards, TD;
  - a market the first model never priced is ADDED only through shipped
    components: RB rush + rec yards (both yards SHAPEs), the longest rush
    (the rushing yards' SHAPE), labelled "derived";
  - a market at rung 1 keeps the first model's block byte for byte, and no
    new market is priced at rung 1;
  - `dist` reads `{"family": "compound", ...}` where SHAPE shipped;
    `matchup_weight` is "shipped" or "provisional"; `engine` is "rushing";
  - lines carry `p_lo` / `p_hi` only where a shipped coefficient carries
    uncertainty; otherwise they are null (the first model's F2);
  - Kalshi's rungs come from the index plus `kalshi_nfl.extra_props()`
    (KXNFLRSHATT, KXNFLRRYDS, KXNFLLONGRSH), merged through
    `_kalshi_lines(extra=)`; the first model's `KALSHI_STAT` is untouched.
- **`breakdown`** (new): a list of `{label, value, unit, kind, basis, lo, hi}`.
  - RB: expected carries, yards per carry, yards before / after contact per
    carry (his own, as-of; PFR, credited, display only: Q10), expected
    receptions, yards per catch, targets, rush / rec TDs.
  - QB: expected designed runs, sneaks and scrambles, each labelled
    "(position split)" with a basis that says it is the same for every QB;
    kneels; yards per attempt of each; expected dropbacks; scrambles per
    dropback; and HIS own split (shares, and the counts it would give from
    the same attempts), labelled information only.
  - Until a group ships, the basis says the split "does not move a
    probability".
- **`role` / `opponent_profile`:** the rushing facts the data layer provides,
  each `{value, unit, kind, n, basis, source, credit}`, merged with
  `setdefault` so no first-model fact is overwritten. PFR / NGS facts say
  they are display only.
- **`factors` and `comparables`** stay the first model's.
- **`notes`:** "shipped (one prospective test)", "derived ...", "rushing
  model at rung 1 for ..." and the slate note for provisional markets.
- **Game-level `correlations`:** `{a, b, rho, n, basis}`, the measured
  within-team residual correlations, for pricing two props together.
- **`model.rushing`:** the registered block's sha256, the fit seasons, each
  market's status and the snapshot's seconds.

## 3. The rule (REGISTERED 2026-10-29T02:13:45Z, sha256 `92a99b8e…`; the feature lists are filled, Q3a)

<!-- RUSH-PREREG-START -->
### Q1. Data, rows, baselines

- **Seasons.**
  - Fit 2023-2024; choose on 2025 (the validation, Q4).
  - Refit 2023-2025 for the 2026 secondary check (Q5), the export and the
    prospective test (Q6): every coefficient, kernel, curve and dispersion
    those steps read is the 2023-2025 refit, exactly what would ship.
  - The SECONDARY check reads 2026 weeks 1-6, on rows with a frozen capture
    (weeks 1-3 and 6; weeks 4-5 have none). It is disclosed as
    knowledge-contaminated (Q8) and can only block.
  - The decisive PROSPECTIVE test reads every regular-season 2026 game whose
    kickoff -- the pbp's recorded start time (the store's Eastern-time
    `kick`, `kick_ts`; minutes from the scheduled time, and the props log
    freezes before either) -- is strictly later than the registration time
    (the UTC time in
    `research/data/nfl_rush_model/prereg.json`), through the last game of
    week 11 (nflverse numbering). It is read ONCE, after that game is final.
    - The window starts with the first game kicking off after the
      registration time; the status line at registration names it. Hashed
      before week 8's Thursday game (2026-10-30 00:15 UTC) it holds weeks
      8-11, 56 games on ESPN's schedule; hashed after week 8, weeks 9-11,
      42 games.
    - A week-11 game postponed past week 12's first kickoff is in the
      window only if the store holds it when the prospective frame is
      built; one not yet played then is left out, whatever its outcome
      would be (no leak: Opus's F4).
  - **Too few games.** If fewer than 40 games in the window hold a universe
    row with a frozen capture, the window extends a week at a time through
    week 13. The count uses pre-game information only (kickoffs, frozen
    lines, universes), never an outcome. Still under 40 at week 13, the
    test runs on what exists and says so (`window_games`).
- **Baselines.**
  - 2023-2025: Sleeper's revised lines.
  - 2026: only the frozen pre-kickoff captures. A capture counts when its
    CONTENT time is strictly before the player's kickoff (`kick_ts`,
    `choose_frozen`). The content time is the props log's pass time, or a
    capture's Sleeper `updated_at` and, where the capture records when it
    was fetched (`capture.json` `captured_utc`), no earlier than that fetch.
    The latest such capture wins.
  - **The props log** (deep store `nfl_props_2026_wNN`, exported to
    sim-history as `props/nfl_props_2026_wNN.json`) rewrites Sleeper's
    projection for every player whose team is still to kick off, on each
    half-hourly pass, and freezes it five minutes before kickoff
    (`nfl_props_log.LEAD_S`). It keys records by normalised NAME only.
  - **Its lead covers the clock this rule reads.** The props log freezes
    300 s before ESPN's SCHEDULED kickoff; `kick_ts`, the pbp's recorded
    start, came 15-218 s AFTER the scheduled time on week 6's Sunday games
    (Fable's measurement, re-derived on 11 of them), so a props-log
    record is before both clocks (0 of week 6's 638 records within 300 s
    of the scheduled kickoff). The DK captures ran 40+ minutes early.
  - **What it was checked to carry** (weeks 6 and 7, 2026-10-23):
    - every RB with a projection has rushing attempts, yards and TDs,
      targets, receptions, receiving yards and receiving TDs (69 of 145 RBs
      in week 6, 88-90 of 172 in week 7; the rest are backups with empty
      projections);
    - every QB with a projection has pass attempts and rushing attempts,
      yards and TDs (25 of 90, 28 of 104).
  - **The reader is week-agnostic** (`research.nfl_rush_model_frame.frozen_lines`):
    - every capture `proj_2026_*.json` (any week) in the repo's feeds, the
      prospective captures and the Sleeper directory;
    - every props-log file;
    - a props-log name maps to a Sleeper id through that week's own
      captures, else the dataset's roster (name, team), else any week's
      captures.

    A back first seen after week 7 is kept. The first model's reader
    stopped its map at week 7 and its glob at one digit (guarded).
- **The prospective dataset** is built AFTER the window closes, by the
  REGISTERED code's stored copies, in a registered tree
  (`python3 -m research.nfl_rush_model_fit registered-tree --dest DIR`: the
  repository with every registered file replaced by its `prereg_code/`
  copy, refused unless each copy hashes to `prereg.json`). Production files
  may change in the weeks between.
  - Inside the tree the decision records' folder is the one its marker
    names, the repository's `research/data/nfl_rush_model/`, outside the
    tree: a run there reads and writes the repository's records and its
    once-lock, never copies (two trees share one lock). An `NFLR_MODEL_OUT`
    naming another folder is refused, and a tree may not hold its records'
    folder (`resolve_model_out`, `in_registered_tree`).
  - The 2026 season store and its rushing aux, built HERE from a fresh
    download (`research.nfl_rush_model_fit build-stores`) into a scratch
    deep folder (`DEEP_CACHE_DIR`): EMPTY at first, then holding copies of
    the registration's 2019-2025 stores and aux taken from their archive
    and checked against `prereg_manifest.sha256`; never a live deep folder
    or a copy of one, and never production's job. `build-stores` first
    moves the season's store and aux already in the folder aside
    (`<key>.pkl.replaced`), then vouches only for what its own refresh
    BUILT: the season's store must carry a play count, its aux `built`,
    and the ids must be rebuilt; a store or aux the refresh kept (unchanged
    upstream) or could not fetch refuses (Opus's F1a). It records the 2026 store, aux and ids pickles' sha256,
    every repository module that ran and the versions, under this
    registration. Should the archive be lost, `build-stores --older 2019
    ... 2025` rebuilds the older seasons beside 2026, and each store and
    aux must match the registration's CONTENT digest (Q9) or it refuses,
    naming the parts that differ (2023's pair, rebuilt from a fresh download
    on 2026-10-28, matched the registration-time pickles).
  - The rushing dataset: `research.nfl_rush_dataset build --all-weeks
    --sleeper <box> --out <records folder>/prospective_dataset`, the
    records' folder by ABSOLUTE path (the marker's `model_out`: run from
    the tree's root, a relative `--out` writes the tree's own copy, which
    `prospective` refuses; Sonnet's F2, Opus's F3). `registered-tree`
    prints these commands with the paths filled in. `--all-weeks` builds
    every 2026 week the store holds; with no flag the builder stops at
    week 7, the registered dataset's cap (Fable's B1). A SEPARATE
    directory: the registered `research/data/nfl_rush/` files are never
    rebuilt in place (a rebuild there would change registered bytes and
    every command would refuse). Its `meta.json` records the sha256 of
    every repository module the build loaded, the versions, the weeks it
    holds and the sha256 of every store, aux and ids pickle it read.
  - The frame: `research.nfl_rush_model_frame --k pooled --seasons 2026
    --weeks <the window's> --frozen-only --dataset <that directory>
    --sleeper <box> --props <every week's props log>`.
  - The props logs and captures above. A week with no props log in the
    sim-history listing is recorded ONCE, before the box is fetched, by
    `research.nfl_rush_model_rule capture-missing --week W`, run in the
    registered tree like everything after the hash; the record names this
    registration, and a record naming another is refused.
  - Sleeper's box (`stats_2026_<w>.json`) fetched 48+ hours after the
    window's last game.

  Its definition is registered; its bytes cannot be. At build time its meta
  records the sha256 of every input it read (the dataset and its meta,
  stores, aux, k tables, props logs, every capture file and its
  `capture.json`, by repo-relative path) and of the code it ran, and each
  box file's sha256 and modification time. The frame's gzip has a zero
  mtime and no name, so a rebuild of the same rows has the same sha256.
  - `prospective` refuses unless it runs in a registered tree, and refuses a
    frame not built by EVERY registered code file at its registered bytes,
    not built as above (pooled k, 2026 only, frozen rows only), or built
    before the registration
    (`research.nfl_rush_model_rule.prospective_frame_refusal`).
  - It refuses a frame whose DATA the registered bytes did not make
    (`provenance_refusal`): a dataset that is not, by realpath,
    `research/data/nfl_rush_model/prospective_dataset` (the registered
    folder by any path refuses), whose `meta.json` no longer hashes to what
    the frame read, whose recorded code is not the registered bytes
    (behaviour-neutral `errlog.py` and `research/__init__.py` aside), which
    does not record the 2026 store, aux and ids it read or read a pickle
    the frame did not (the frame's outcomes are its rows: Opus's F1b), or
    which holds no rows of a window week (Fable's B1); a 2026 store, aux
    or ids the store build's record does not vouch for, or a record that
    changed, ran on other code or under another registration; and any
    older store or aux that is not the registered test frame's, by sha256
    or, re-saved or rebuilt, by content digest (Q9).
  - It derives the window's weeks itself, never from the frame's rows: EVERY
    week from the week of the first game kicking off after the registration
    through the window's last week (the extension included), a range
    (`window_weeks`). The store holds played games only, so each of those
    weeks must hold a game in it, and the last week must be CLOSED: the
    store must hold a game of the week after it (so every last-week game has
    kicked off), and the window's last kickoff is the last week's last game
    in the store. An empty window refuses.
  - It refuses a frame that omits any of those weeks; a week with neither a
    props log among the frame's inputs nor a "capture missing" entry
    recorded before the box under THIS registration; a week whose props
    log is an input but which holds no frozen universe row (the dataset or
    the captures short of it: Fable's m5); a week with no box file; a box
    fetched under 48 hours after the window's last game; and a run under
    48 hours after it (`coverage_refusal`).
  - It records the frame's sha256 and build record in
    `prospective_lock.json`.
- **Outcomes.**
  - Sleeper's box first: official scoring, kneels are rushes, sacks are not.
  - The pbp is the fallback for a player who played with no box line.
  - TDs are the pbp's rushing + receiving TDs. Kalshi's KXNFLTD also counts
    return and fumble-recovery TDs, rare for RBs and not counted here.
  - The longest rush is the pbp's longest designed run or scramble; an
    aborted snap recovered for positive yards, an official rush Kalshi
    would count, is left out (rare; `nfl_rush.Longest` prices the same
    definition, and the derived market decides nothing: Fable's n4).
- **Scored and fitted CONDITIONAL ON PLAYING, in every season** (matching
  Kalshi's settle-at-fair-price for a player who never plays, and the
  board's conditional probabilities). He played iff Sleeper's box shows
  `gp >= 1`, or the pbp credits him a rush, a target or a dropback
  (`y_cond_played`). The all-rows figure is reported beside every decision
  and decides nothing; the dropped rows are counted in the record.
- **No restricted column selects a row.** The data layer gives a back a row,
  and `act_played` 1, from PFR offensive snaps alone (48 of 618 RB rows in
  2026). So:
  - "played" never reads `act_played`, `act_any_snap` or a PFR snap count;
  - the universes select on Sleeper's line only;
  - every RB universe needs projected carries or targets, which is what
    gives a back a dataset row whatever PFR says (`week_rows`' Sleeper
    set), so no universe row exists through PFR alone;
  - the distribution fits' box rows are Sleeper's (`gp >= 1`).

  The columns that select a row are `research.nfl_rush_model_frame.SELECTION_COLS`;
  a guard holds each to an unrestricted Sleeper or pbp tag in the dataset's
  own schema.
- **Row universes** (`nfl_rush.in_universe`, the board's own; the FIT
  universe equals the SCORED universe):
  - RB rushing (carries, rushing yards, rush + rec, the longest rush): 5+
    projected carries and a positive yards line.
  - RB receiving: 2+ projected targets and a positive receptions line.
  - RB anytime TD: rush + rec TDs >= 0.15, with projected carries or
    targets.
  - QB rushing (attempts, yards, TD, the longest rush): 20+ projected pass
    attempts and 2+ projected rushing attempts.
- **Features.** Each is computed AS OF the week (strictly earlier games) by
  the agreed data layer's code through production's adapter (`nfl_rush.features`,
  `team_blocks`), shrunk by measured k:
  - the validation frame on `reliability.json`'s `k_fit_only` (2018-2024),
    and the first model's defense deviations on its fit-only k too (`NFLM_K`,
    asserted);
  - the test, export and prospective frames on the pooled 2018-2025 table.
  - **Participation: prior seasons only, in every frame.** nflverse
    publishes participation after the postseason, so no live board has the
    current season's. Every snapshot goes through `nfl_rush.availability_view`,
    which masks the season's own participation. That puts every season where
    2026 is: the box falls back to FTN's count in season, and the rest reads
    prior seasons. The pbp, FTN and the injury reports are in season.
  - **Pressure** is the pbp's sacks + QB hits per opponent dropback, in
    season, observed over what the QBs faced do elsewhere
    (`dq.hit_sack_rate.oe_ratio`, a proxy; the layer measured r 0.40, k 67).
  - **OL absences** are the team's linemen listed Out / Doubtful on the
    week's report (`ol.ol_listed_out`).
  - **Usage windows** (`dec_trend`, the carry shares) count pbp /
    participation appearances only (the data layer's round-4 fix). A
    special-teams-only PFR snap line moves no fitted input (guarded).
  - **Missing inputs.** A feature with a missing input is missing; a sum
    feature drops its missing parts and is missing only when every part is.
    A missing feature reads 0, the deviation's "nothing known", in the fit
    and on the board alike.
    - The two non-negative counts (`cfp_ol_out`, `dec_room_out`,
      `nfl_rush.MEAN_FILL`) read their training mean instead. Their 0 is
      "nobody out", their minimum, and a board built before the week's
      injury report posts reads None for every team.
    - The section 4 missing counts treat a sum as missing only when every
      part is.
- **The prospective frame's features (C1, option b).** RECOMPUTED after the
  window from all data on games strictly before each row's game. They can
  differ from what the live board served if a feed lagged. The `live_lag`
  column is blank: no board build logs its provenance stamp yet, so the
  lag-split report is not available, and the report says so.
- **Game script and kneels read the PRE-GAME line only (C2).**
  - Research: nflverse's closing `spread_line` / `total_line`
    (`team_margin`: + for the favoured home side).
  - Production: the board's pre-game Kalshi de-vigged ladder; with none
    believable, margin 0 / total 44, and the board says so.
  - Never the realised score.

### Q2. The model

- **The mean** is Sleeper x exp(sum b (x - x_mean) / x_sd). There is NO
  intercept anywhere: the level is Sleeper's line, fixed.
  - Level recalibration is OUT of this rule. The first model's level rung
    made 2025 worse, and `nfl_trim` governs the drive sim's level (Q8).
  - x_mean and x_sd come from the fit rows (the market's universe,
    played, in the fitting seasons). A missing x reads 0 before centring
    (a `MEAN_FILL` count its fit rows' mean), in the fit and on the board
    alike.
- **The fit.** Ridge quasi-Poisson, with every column penalised alike and
  outcomes clipped at 0.
  - Lambda comes from {1, 10, 100, 300, 1e3, 3e3, 1e4, 3e4, 1e5, 1e6}, by
    leave-one-season-out deviance over the FITTING seasons only: two folds
    for 2023-2024, three for 2023-2025. Two folds make a coarse choice. A
    column constant within one fold is penalised like the rest, and is
    dropped only when its SD over all the fit rows is zero.
  - The log factor is clamped to [-1, 1] when it is applied.
  - The band is the delta-method SE from the penalised information's
    inverse (`fit_ridge`: Z'WZ + lambda), with NO quasi-Poisson dispersion
    factor, so it runs narrower than a dispersion-scaled band by about
    sqrt(phi) for carries and yards. Display only: no decision reads a
    band (Fable's m4).
- **Dead features.** A feature whose SD over a position's training rows is
  zero in EITHER frame (validation 2023-2024, test 2023-2025) is dropped
  from every fit. A group left with no live term is not evaluated and
  cannot ship. Within one fit, a column with zero SD in that market's fit
  rows is dropped and recorded.
  - Today that leaves out `cfp_direction` (RB), and `cfp_dsg_edge` and
    `spd_qb_td_oe` (QB). Their defense signals (`def_side_ypc_dev`,
    `def_dir_ypc_dev`, `def_rush_tds_oe` pooled) were measured UNSUPPORTED
    by the data layer, so their deviations shrink to the prior.
  - "Run direction" and "QB rushing TDs allowed" are therefore UNTESTED by
    this rule. The QB TD market keeps only its FULL group.
  - `spd_qb_td_oe` is dead only in the TEST frame: its shrinkage there is
    the pooled k (2018-2025), which reads 2025's games and finds
    `def_rush_tds_oe` unsupported; on the fit-only k (2018-2024) it is
    live. One candidate group (QB anytime TD's SPD) leaves the 2025 walk
    on a 2025-informed reliability: a removal, never an addition (Fable's
    m3).
- **Distributions.**
  - **Rung 1 is this rule's own family**: NegBin counts (alpha by moments)
    and rushing / receiving yards as a point mass from the count line at its
    alpha plus a gamma positive part (CV by moments). These are the first
    model's shapes and estimators, on this rule's universes and played rows,
    with anytime TD Poisson.
  - The rung-1 parameters, and the compound's kernels, alpha(mean) curves,
    kneel table and QB position priors, are fit on 2023-2024 for the 2025
    validation and on 2023-2025 for the 2026 check, the export and the
    prospective test. The first model's committed (2023-2025) dispersion is
    never used for 2025.
  - Their rows (`research.nfl_rush_model_fit.fit_distributions`):
    - the kernels: every pbp run, catch, designed QB run, sneak, scramble
      and kneel of the position (yards clipped to [-15, 99]);
    - the RB alpha curves, alpha = a x mean^b by moments over quintiles of
      the line: Sleeper's box rows (`gp >= 1`) with a positive carries /
      receptions line and yards line;
    - the QB shares and component curves: QB-games with Sleeper's box
      (`gp >= 1`), 20+ projected pass attempts and a positive rushing
      line, a game with no rush a row of zeros;
    - the kneel table: every team-game with a pre-game spread;
    - the positions and Sleeper ids they read (which runs are an RB's,
      whose box a QB's components join) are the registration's record of
      the ids pickle (`prereg.json` `ids_positions`,
      `fit.registered_positions`), never a later ids pickle: the
      prospective test's folder holds a fresh one, in which nflverse may
      have relisted a player since the hash (Fable's m7).
  - SHAPE (Q3) switches a market to the compound of section 2.2.
  - The QB compound splits his attempts by the POSITION's shares with one
    pooled designed-run kernel (`nfl_rush.QB_SHARES`), chosen inside the
    fitting seasons (section 1).
- **What the board prices is this model's own code.** The walk scores each
  row through `nfl_rush.plan` / `family_dist`, exactly what the board
  calls. A replay guard checks fit -> export -> board probability.

### Q3. The groups and their order

Per market, in the owner's order:
1. Sleeper (rung 1).
2. OVD.
3. SPD.
4. CFP.
5. SHAPE: the decomposition with NO new term. It is the compound
   distribution in place of the rung-1 family, with the terms already
   accepted re-expressed on components (Q3b).
6. DEC: the opportunity terms.
7. FULL.

The feature lists are Q3a, generated from the adapter's definitions and
frozen when this block is hashed; section 2.3 describes them.

- **The TD markets** read only their own groups (RB: FULL; QB: FULL, its SPD
  group dead).
- **The derived markets** are rush + rec yards (RB) and the longest rush
  (RB, QB). They are scored rung by rung (Q4's thresholds), deciding
  nothing, wherever every one of their components' SHAPE is accepted on
  both sides of a comparison. The board prices them only through components
  whose SHAPE shipped, labelled "derived" (Q5).
- A market whose baseline this rule never scored is never priced.

#### Q3a. The feature lists (frozen at hashing; generated by `research.nfl_rush_model_frame.feature_lists_md` from `nfl_rush.FEATURES` / `GROUPS`)

Frame column = `f_<feature>`. Blocks: `p` rb(pid, team), `q` qb(pid, team), `d` defense_run(opp), `dq` defense_qb(opp), `ol` offense_line(team), `tc` team_context(team) (`research/reports/nfl_rush_data.md` §0.3 paths), each from the data layer's snapshot as of the week through the frame's `availability_view` (the season's own participation never enters: Q1). `[m1_*]` are the first model's `nfl_matchup.defense_block` deviations (`x`) for the opponent, prior seasons for coverage; `[margin7]` the team-side PRE-GAME expected margin / 7 and `[total_c]` (total - 44) / 10 (research: nflverse closing spread_line / total_line; production: the board's pre-game Kalshi line); `[room_out]` `rb_room_out(team, exclude=pid)`; `[comp_*]` the comparables (frame docstring). `dq.hit_sack_rate` is the pbp's sacks + QB hits per opponent dropback, in season, observed over what the QBs faced do elsewhere (a pressure proxy), and `ol.ol_listed_out` the team's offensive linemen listed Out / Doubtful on the week's report (injuries; None without a report). Every input's source is declared (`nfl_rush.PATH_SOURCE` / `EXT_SOURCE`) and is pbp, participation, FTN, injuries or the pre-game line: no PFR or PFR-snap field and no NGS field (Q10). Participation keeps the shipped first model's status: nflverse took its 2022-and-earlier records from NGS, so the 2023 and 2024 rows' prior-season reads are NGS-originated (Q10). A missing input makes the feature missing (a `sum` drops a missing part and is missing only when every part is), and a missing feature reads 0, the deviation's "nothing known"; the two non-negative counts (`cfp_ol_out`, `dec_room_out`, `nfl_rush.MEAN_FILL`) read their training mean instead. "moves": `count` the opportunity (carries / designed runs / scrambles / receptions), `eff` yards per attempt, `mean` the market's mean; QB terms also name their component (`nfl_rush.QB_COMPONENT`); before SHAPE is accepted every term acts on its market's mean (Q3b).

**No feature reads a dataset column.** The dataset (`research/data/nfl_rush/{rb,qb}_games_2023_2026.csv.gz`) supplies, by name (`research.nfl_rush_model_frame.ID_COLS` / `BASELINE_COLS` / `OUTCOME_COLS` / `PLAYED_COLS`), only the row's identity and pre-game injury-report status, Sleeper's line (`sl_proj_*`: the BASELINE, never a feature) and the outcomes (`act_*`, `sl_act_*`, `act_played`; written to the frame as `y_*`, which the frame reader drops for every season the hold forbids). `in_sleeper` and `sl_qb1` (post-week Sleeper fields) are read by nothing. Every feature below is computed from the snapshot API.

- **RB rushing (carries, rushing yards; rush + rec and longest rush derived)**
  - OVD: `ovd_run_epa` (eff) = d.vs_rb.epa (value - prior); `ovd_run_success` (eff) = d.vs_rb.success (value - prior)
  - SPD: `spd_rb_ypc` (eff) = d.vs_rb.ypc (value - prior); `spd_rb_stuff` (eff) = d.vs_rb.stuff (value - prior); `spd_rb_expl10` (eff) = d.vs_rb.expl10 (value - prior); `spd_rb_yds_pg` (eff) = d.per_game.rb_rush_yds - league mean; `spd_rb_car_pg` (count) = d.per_game.rb_carries - league mean
  - CFP: `cfp_direction` (eff) = sum: p.rushing.by_side.edge.share x d.vs_rb.by_side.edge.ypc (value - prior) + p.rushing.by_side.tackle.share x d.vs_rb.by_side.tackle.ypc (value - prior) + p.rushing.by_side.interior.share x d.vs_rb.by_side.interior.ypc (value - prior); `cfp_box_split` (eff) = sum: p.rushing.by_box.light.ypc (value - prior) x d.box.light (value - prior) + p.rushing.by_box.heavy.ypc (value - prior) x d.box.heavy (value - prior); `cfp_def_light` (eff) = d.box.light (value - prior); `cfp_def_heavy` (eff) = d.box.heavy (value - prior); `cfp_pers_split` (eff) = sum: p.rushing.by_personnel_faced.base.ypc (value - prior) x d.personnel.base (value - prior) + p.rushing.by_personnel_faced.nickel.ypc (value - prior) x d.personnel.nickel (value - prior) + p.rushing.by_personnel_faced.dime.ypc (value - prior) x d.personnel.dime (value - prior); `cfp_ol_line` (eff) = ol.line_yds (value - prior); `cfp_ol_stuff` (eff) = ol.stuff (value - prior); `cfp_def_line` (eff) = d.vs_rb.line_yds (value - prior); `cfp_ol_x_def_line` (eff) = ol.line_yds (value - prior) x d.vs_rb.line_yds (value - prior); `cfp_ol_out` (count) = ol.ol_listed_out; `cfp_front_dl4` (eff) = d.front_proxy.dl4 (value - prior)
  - SHAPE: no term -- the compound distribution in place of the rung-1 family, the terms already accepted re-expressed on components (Q3b)
  - DEC: `dec_margin` (count) = [margin7]; `dec_total` (count) = [total_c]; `dec_neutral_run` (count) = tc.neutral_run_rate (value - prior); `dec_trend` (count) = (p.recent.carry_share - p.usage.carry_share); `dec_room_out` (count) = [room_out]
  - FULL: `full_comp_rush_yd` (eff) = [comp_rush_yd]; `full_qb_keep` (count) = tc.qb_keep_share (value - prior); `full_ypc_x_margin` (eff) = d.vs_rb.ypc (value - prior) x [margin7]
- **QB rushing (attempts, rushing yards; longest rush derived)**
  - OVD: `ovd_run_epa` (eff, designed) = d.vs_rb.epa (value - prior); `ovd_pressure` (count, scramble) = log dq.hit_sack_rate.oe_ratio; `ovd_blitz` (count, scramble) = dq.blitz (value - prior)
  - SPD: `spd_qb_dsg_oe` (count, designed) = log dq.designed_runs.oe_ratio; `spd_qb_dsgyds_oe` (eff, designed) = log dq.designed_yds.oe_ratio; `spd_qb_scr_oe` (count, scramble) = log dq.scrambles.oe_ratio; `spd_qb_scryds_oe` (eff, scramble) = log dq.scramble_yds.oe_ratio; `spd_qb_expl_oe` (eff, both) = log dq.explosive_runs.oe_ratio
  - CFP: `cfp_scr_press` (count, scramble) = (q.scramble.vs.pressure - q.scramble.vs.no_pressure) x log dq.hit_sack_rate.oe_ratio; `cfp_scr_blitz` (count, scramble) = (q.scramble.vs.blitz - q.scramble.vs.no_blitz) x dq.blitz (value - prior); `cfp_scr_man` (count, scramble) = (q.scramble.vs.man - q.scramble.vs.zone) x [m1_man]; `cfp_scr_shell` (count, scramble) = (q.scramble.vs.single_high - q.scramble.vs.two_high) x [m1_sh_frac]; `cfp_contain` (eff, scramble) = log dq.containment_proxy.oe_ratio; `cfp_dsg_edge` (eff, designed) = q.designed.by_side.edge x d.vs_rb.by_side.edge.ypc (value - prior)
  - SHAPE: no term -- the compound distribution in place of the rung-1 family, the terms already accepted re-expressed on components (Q3b)
  - DEC: `dec_margin` (count, both) = [margin7]; `dec_total` (count, both) = [total_c]
  - FULL: `full_comp_qb_rush_yd` (eff, both) = [comp_qb_rush_yd]; `full_scr_x_margin` (count, scramble) = (q.scramble.vs.pressure - q.scramble.vs.no_pressure) x [margin7]
- **RB receiving (receptions, receiving yards)**
  - OVD: `ovd_pass_epa` (eff) = [m1_pass_epa]; `ovd_catch` (eff) = [m1_catch_all]
  - SPD: `spd_rbrec_tgt` (count) = d.vs_rb_receiving.tgt_per_db (value - prior); `spd_rbrec_ypt` (eff) = d.vs_rb_receiving.ypt (value - prior); `spd_rbrec_catch` (count) = d.vs_rb_receiving.catch (value - prior); `spd_rbrec_yac` (eff) = d.vs_rb_receiving.yac_rec (value - prior)
  - CFP: `cfp_screen_x_blitz` (count) = p.receiving.screen_share (value - prior) x dq.blitz (value - prior); `cfp_chk_x_press` (count) = p.receiving.checkdown_share (value - prior) x log dq.hit_sack_rate.oe_ratio; `cfp_rec_mz` (count) = (p.usage.tgt_per_db_vs_man - p.usage.tgt_per_db_vs_zone) x [m1_man]
  - SHAPE: no term -- the compound distribution in place of the rung-1 family, the terms already accepted re-expressed on components (Q3b)
  - DEC: `dec_margin` (count) = [margin7]; `dec_total` (count) = [total_c]
  - FULL: `full_comp_rec_yd` (eff) = [comp_rec_yd]
- **RB anytime TD**
  - FULL: `full_rz_run` (mean) = tc.red_zone_run_rate (value - prior); `full_qb_keep` (mean) = tc.qb_keep_share (value - prior)
- **QB anytime TD (rushing + receiving)**
  - SPD: `spd_qb_td_oe` (mean) = log dq.rush_tds.oe_ratio
  - FULL: `full_qb_gl` (mean) = q.scoring.gl_rush_rate (value - prior)

#### Q3b. Which terms enter which market, the walk order, and how many decisions

- **Before SHAPE** (or where SHAPE is not accepted), each market has one
  mean GLM: its outcome, offset log(Sleeper's line).
  - A COUNT market (RB carries, QB rushing attempts, RB receptions) reads a
    group's `count` terms.
  - A YARDS market (RB and QB rushing yards, RB receiving yards) reads all
    of a group's terms.
  - The distribution is the rung-1 family at the fitted mean.
- **SHAPE and after: RBs.**
  - The carries GLM (outcome: official rushing attempts, aborted snaps
    included) reads the count terms. It has the same coefficients as the
    mean form; only the count's dispersion changes, to the line-dependent
    alpha.
  - The yards-per-carry GLM (outcome: rushing yards, offset log(Sleeper's
    yards) + the carries market's FINAL accepted log factor) reads `eff`
    terms only.
  - A count-type term accepted earlier on the yards market leaves at SHAPE:
    the yards compound carries the carries market's factor instead.
  - Receiving works the same way: receptions, then yards per catch.
- **SHAPE and after: QBs.**
  - **Two count GLMs.** Designed runs (outcome: the pbp's designed runs,
    sneaks excluded) and scrambles (outcome: the pbp's scrambles). Each has
    offset log(its expected count): Sleeper's attempts less the margin
    table's kneels, split by the position's shares (`nfl_rush.qb_parts`),
    under the count factors fitted so far.
  - **Two efficiency GLMs.** Designed-run yards and scramble yards
    (outcomes: the pbp's component yards, clipped at 0). Each has offset
    log(its expected yards): the expected count x the anchored yards per
    attempt.
  - **The component map.** Each term moves the component(s) Q3a names
    (`nfl_rush.QB_COMPONENT`). "both" means a coefficient of its own in
    each component's GLM.
  - Sneaks and kneels are never moved.
  - If the attempts market's SHAPE is not accepted but the yards market's
    is, the attempts' mean factor scales both component counts and the
    yards anchor.
- **The walk order.**
  - RB: carries, rushing yards, receptions, receiving yards, TD.
  - QB: attempts, rushing yards, TD.

  A count market's walk completes before its yards market's starts.
- **SHAPE on yards but not on counts.** The count market keeps its rung-1
  NegBin and its own factor. The yards compound draws its count with the
  line-dependent alpha at the SAME factor, so the expected carries agree.
- **TD (the link).** A GLM on the pbp's rushing + receiving TDs, offset
  log(Sleeper's rushing + receiving TDs). It reads only its own groups; no
  carries or receptions factor reaches it.
- **Decision count (C4, after the SHAPE split and the dead features).** The
  2025 walk makes **35** group x market decisions:
  - RB carries 5 (SPD, CFP, SHAPE, DEC, FULL);
  - RB rushing yards 6 (OVD, SPD, CFP, SHAPE, DEC, FULL);
  - RB receptions 4 (SPD, CFP, SHAPE, DEC);
  - RB receiving yards 6;
  - RB TD 1 (FULL);
  - QB attempts 6;
  - QB rushing yards 6;
  - QB TD 1 (FULL).
- **Error rates (computed by `research.nfl_rush_model_rule.error_rates`).**
  Three figures for a null group:
  - **The 2025 gate alone.** A null group passes about 2.275% of the time
    on the normal figure (Phi(-2)). Opus's null simulation (fit 2023, score
    2024, 1,000 replicates per setting) puts it at 0.0-1.3% with the floor,
    mostly 0.3-0.7%.
  - **A false provisional ship.** It must also pass the 2026 check (d <= 0,
    about a coin flip). That is 1.14% a decision on the normal figure: 0.40
    expected across 35 decisions, and 33.0% for at least one. On the
    simulated figures, 8.4% (at 0.5%) to 20.4% (at 1.3%).
  - **A false permanent ship.** It must also pass the prospective KEEP
    (d < -1 SE, 15.87% for a null). That is 0.18% a decision on the normal
    figure: 0.063 expected and **6.1%** for at least one across the 35. On
    the simulated figures, 1.4% (at 0.5%) to 3.5% (at 1.3%).
  - The decisions are sequential and correlated, so these are guides, not
    bounds. SHAPE is not a null group: its shape gain was seen in the
    fitting seasons (section 1).
  - **Up to three of the 35 are vacuous by construction:** DEC on RB
    rushing yards, RB receiving yards and QB rushing yards once that
    market's SHAPE is accepted. DEC holds only count terms, which leave a
    yards market at SHAPE, so the candidate equals the accepted model, d is
    0 and the group is rejected. The figures above count them, so they are
    conservative by up to three decisions.

### Q4. The 2025 decision

- **Metric per row:** the threshold log loss summed over the market's
  thresholds. Probabilities are clipped to [1e-6, 1 - 1e-6]. The thresholds
  are generated from `nfl_rush.THRESHOLDS`:


| market | RB thresholds | QB thresholds |
|---|---|---|
| carries / QB rushing attempts | 5 / 10 / 15 / 20 | 2 / 4 / 6 / 8 |
| rushing yards | 10 / 15 / 25 / 40 / 50 / 60 / 75 / 100 | 10 / 15 / 25 / 40 / 50 / 60 |
| receptions | 1 / 2 / 3 / 4 / 5 | - |
| receiving yards | 10 / 15 / 25 / 40 / 50 | - |
| rush + rec yards (derived) | 25 / 40 / 50 / 60 / 75 / 100 / 125 | - |
| anytime TD | 1 / 2 | 1 |
| longest rush (derived) | 10 / 15 / 20 / 25 / 30 / 40 | 10 / 15 / 20 / 25 / 30 / 40 |

- **d** is the candidate minus the accepted model, per scored row (Q1: in
  the universe, played). Its **SE** is the LARGER of the game-clustered and
  the player-clustered SE. Each is G / (G - 1) x the sum of the clusters'
  CENTRED sums squared / n^2 (`research.nfl_rush_model_rule.clustered`).
  On the 2024 traincheck the player-clustered SE was 1.31x the game one
  for RBs.
- **The gate:** a group is ACCEPTED on 2025 iff d < -2 SE AND d < -0.0005 x
  the NUMBER of the market's thresholds. Both are strict, and a non-finite
  d or SE rejects. It is one standard: SHAPE faces it exactly as a term
  group does.
- **The walk is SEQUENTIAL** in the owner's order. Each group's candidate is
  the groups already accepted for that market plus the group, with the
  count market's final model in a yards market's offsets. Both sides are
  fit on 2023-2024, and a rejected group is never retried.
- **Reported, deciding nothing:** for each step,
  - the all-rows figure (players who did not play included);
  - the derived markets' d where both sides price them, and, at the step
    that first prices one (its components' SHAPE), that form's own log
    loss, MAE and 10-bin ECE: the version that would be priced on
    KXNFLRRYDS / KXNFLLONGRSH;
  - each side's MAE of the mean and its 10-bin ECE per threshold;
  - the rows dropped because a side could not price them.

### Q5. The 2026 check, the provisional ship and the export

- **The walk.** Over the 2025-accepted groups, in ladder order, on 2026
  weeks 1-6's frozen rows (played).
  - Each group is judged against the model with the groups already KEPT.
  - Both sides are refit on 2023-2025 (kernels, curves and dispersion
    included).
  - A group is kept iff d <= 0 (point estimate).
  - A dropped group is not refit into later ones: later groups are judged
    on top of what was kept.
  - Each row's capture lead time is reported.
- **The export** refits exactly the kept set on 2023-2025 and writes
  `coefs.json` ONCE (stage "provisional"). Each group carries status
  "provisional" and the records it passed: the sha256 of this block, of
  prereg.json, of validate.json and of check2026.json. It refuses once
  `prospective_lock.json` exists: after the lock the only `coefs.json` is
  the prospective test's.
- **The board** reads only "provisional" and "shipped"; any other status,
  None included, is not shipped and is ledgered.
  - `nfl_rush.load_coefs` believes a file only when its statuses are one
    stage's and trace to the records beside it (`record_chain`):
    provisional needs prereg.json, validate.json and check2026.json to hash
    to its records; shipped needs `prospective_lock.json` too. Its shipped
    groups must also BE that record's set: check2026's provisional set, or
    the lock's kept set. Otherwise the board has no rushing model at all,
    ledgered `NFLR-coefs`.
  - The board marks its markets "provisional" (`matchup_weight` and the
    slate note).
  - It prices a derived market only through components whose SHAPE
    shipped, labelled "derived".
  - With nothing shipped, it shows the facts and the breakdown, and prices
    no new market: the first model's markets stand, unchanged.
- **Nothing that failed 2025 ships.**

### Q6. The prospective test (once)

- **When:** after the window (Q1) closes and the Sleeper box is fetched 48+
  hours later, on the prospective frame built as Q1 says.
- **The walk.** Over the provisional groups, in ladder order, sequential:
  - each group against the model with the groups already kept;
  - both sides refit on 2023-2025 by the registered fitter, which is exactly
    what would ship;
  - **KEEP iff d < -1 SE** (one-sided; the SE as Q4); otherwise REVERT.
- **Its power is modest; read the verdict with it** (Fable's m2). d's SE
  scales as 1 / sqrt(games): 2025 holds 272 games, the window 56 (weeks
  8-11) or 42 (weeks 9-11), so the window's SE is 2.20x or 2.54x the 2025
  SE. A group whose TRUE gain sits exactly at the 2025 gate's edge (d = -2
  SE of 2025) is kept with probability 0.46 (56 games) or 0.42 (42); at
  1.5x the gate 0.64 or 0.57; at 2x 0.79 or 0.72
  (`research.nfl_rush_model_rule.keep_power`, the normal figure). A real
  effect of the gate's size is reverted about half the time: "reverted"
  says the window did not confirm it, not that it is absent.
- **Where it runs.** In a registered tree (Q1), from the code bytes stored
  at registration, after the store pickles it reads directly (2023-2025)
  are re-verified against the registration (by sha256, or, re-saved or
  rebuilt, by content digest: Q9). It reads only
  records written under this registration on the registered code
  (`read_record`), check2026.json's link to validate.json intact, and
  `coefs.json` as export wrote it.
- **Once.** It writes `prospective_lock.json`. A re-run needs `--force`, and
  the record says so with the previous sha256. The final `coefs.json`
  (stage "shipped", written once, after the lock) holds the kept groups as
  "shipped"; the board labels them "shipped (one prospective test)".
  Reverted groups leave.
- **Reported beside the decision:**
  - the window's last week, its games and its note;
  - the games and rows per market (C3);
  - the all-rows figure;
  - the MAE and ECE;
  - the derived markets' first priced form (Q4);
  - Kalshi's pre-kickoff prices (Q7, descriptive).

### Q7. Kalshi (descriptive, no decision)

Computed after the decisions, by a descriptive script outside the registered
set; it decides nothing.
- The last hourly candle at or before kickoff, books within 20c.
- Series: KXNFLRSHYDS, KXNFLRSHATT, KXNFLRRYDS, KXNFLREC, KXNFLRECYDS,
  KXNFLTD and KXNFLLONGRSH, for RB / QB rows.
- Scored on 2025 (revised) and on 2026's frozen rows.
- One-contract ROI on the positive-EV side, fee in, bootstrap over games
  (the first model's R5).

### Q8. What was seen before writing this

- The 2023-2024 measurements of section 1, and this model's own design work
  inside the fitting seasons. The own-share QB split lost to the position's
  (+0.021 +/- 0.009 nats a row), and so did every blend.
- **The first model's 2025 and 2026 results** (`research/reports/nfl_matchup_model.md`),
  which read every RB / QB market here:
  - its 2025 ladder accepted nothing for targets, receptions, receiving
    yards, carries or rushing yards (best rungs -0.0026, -0.0012, -0.0007,
    -0.0004, +0.0002);
  - its level rung R made 2025 WORSE: receptions +0.0106 +/- 0.0049,
    receiving yards +0.0079 +/- 0.0039, carries +0.0052 +/- 0.0021,
    rushing yards +0.01106 +/- 0.00609;
  - its F3 calibration tables (2023-2025 revised, 2026 frozen);
  - its section 9 line-dependent CV gate on 2025 (FAILED, -0.0086 +/-
    0.0137; the low-line cells moved the right way);
  - Kalshi beating it on 2026 rushing yards (0.506 vs 0.447; QBs 0.552 vs
    0.407).
- **The 2025 / 2026 levels** (`research/reports/nfl_trim.md`): rushing
  yards landed at 0.958x Sleeper in 2025 and 0.922x in 2026 weeks 1-6;
  touchdowns at 0.957x and 1.056x. The level is OUT of this rule (Q2), but
  2025 is not blind on it.
- **`nfl_usage`'s carry-share test** (`research/reports/nfl_usage.md`,
  shipped 2026-10-18). Last-3-games carry share at 0.3 against Sleeper's:
  -0.003177 +/- 0.001459 on 2025 (passed), -0.002578 +/- 0.002000 on 2026.
  That is `dec_trend`'s signal; its absent-lead-back case "hurt on 2025",
  which is `dec_room_out`'s. DEC's opportunity terms are therefore NOT
  blind on 2025.
- **The orchestrator's slice** of the first model's Kalshi record, given to
  the owner on 2026-10-23: buying YES on rushing yards lost 36.5% (95% -63%
  to -7%) over 166 bets, 2025-26. It is disclosed here and enters nothing
  else.
- **Outcome-touching checks with no model in them:**
  - the data layer's 2025 reconciliation of pbp against Sleeper's box;
  - the data-source report's 2025-26 settlement checks (239 of 239 QB
    ladders, the sacks tests);
  - the data reviews' 2025-26 `act_played` / `act_any_snap` / `gp` tallies;
  - the data layer's DECAY, BACK, sneak rule (its precision and recall on
    2022-25), the hit + sack proxy's r (0.40, 2018-25), the pooled k and the
    QB-game box-versus-pbp match on 2023-25. All of these read 2025. DECAY
    0.75 was set on pairs through 2024 -> 2025; without them the tendency r
    is 0.600, and DECAY is 0.75 either way.
  - the data confirmation's count of 2026 RB rows that exist through PFR
    offensive snaps alone (48 of 618), which set Q1's row-selection rule.
- **Outcome-free looks at the 2025 / 2026 rows:** row counts per market,
  missing-feature counts per group, frozen-capture counts and lead times,
  and feature / line comparisons between frame builds and against the
  dataset's own feature columns (section 4). Those comparison scripts read
  whole CSV rows and skipped every outcome column: no 2025 / 2026 outcome
  cell was compared, computed on or printed.
- **The frames physically carry the 2025-26 `y_*` columns.** The reader
  refuses them until the registration holds (Q9); the hold is enforced by
  code, not by the files.
- **The licence default** (Q10, 2026-10-24) removed three CFP terms and
  swapped two inputs. It read the data sources' terms, never an outcome.

So 2025 is not blind on the SHAPE question, on the level, or on DEC's
opportunity terms, and 2026 weeks 1-6 are not blind on anything rushing.
Only the prospective window (Q1) is blind, and it is the judge that makes a
group "shipped".

### Q9. Code, registration, refusals

At registration (`research.nfl_rush_model_fit register`), `prereg.json`
records:
- the sha256 of this block;
- the UTC time;
- the sha256 and stored bytes of every code file the runs execute;
- the sha256 of every data file;
- the two frames' sha256, each with its build record (k table, seasons,
  participation mode, the code and every input it read);
- the dead features;
- the decision count;
- the Python and numpy versions (recorded, not enforced; every decision
  record carries the versions it ran on too);
- every deep pickle the frames read (the 2019-2026 stores and aux, the
  ids) by sha256 AND by CONTENT digest (`stores`), and the ids pickle's
  positions and Sleeper ids (`ids_positions`: what the distribution fit
  reads, Q2).

Beside it, `prereg_manifest.json` and `prereg_manifest.sha256` list the 19
files the later commands verify -- the two frames and the 17 pickles --
and the frames' build records, each with its sha256 and where it lay. They
exist nowhere else when the hash is made and cannot be rebuilt byte for
byte (`deep_cache.save` stamps the save time into every pickle), so they
are archived at the hash (the runbook after this block).

**The content digest** (`research.nfl_rush_model_fit.content_digest`):
sha256 over a canonical encoding of a pickle's payload with the parts no
registered computation reads left out (`NOT_CONTENT`: the save clocks
`built`, `seconds`, `store_built`; the download bookkeeping `sources`; the
aux build's join diagnostics `joins`, read only by the dataset's `checks`
report; deep_cache's `generated_at` sits outside the payload): dict keys and
sets sorted, floats by their exact hex, arrays by type code and bytes, an
unknown type refused. Each top-level part (and each key of a small dict part) is
digested on its own, so a mismatch names what differs (`cols.yds`). A
re-saved or rebuilt pickle holding the same data passes; one changed value
does not.

The registered code (`REG_CODE`):
- `nfl_rush.py`, `prop_dist.py`;
- `research/nfl_rush_model_frame.py`, `research/nfl_rush_model_fit.py`,
  `research/nfl_rush_model_rule.py`;
- the agreed data layer: `nfl_rush_data.py`, `nfl_rush_profiles.py`,
  `research/nfl_rush_dataset.py`;
- `nfl_matchup_data.py`, `nfl_matchup_profiles.py`, `nfl_matchup.py`,
  `research/nfl_matchup_frame.py`, `research/nfl_matchup_fit.py`,
  `kalshi_nfl.py`;
- `deep_cache.py` (it resolves and loads every store pickle) and `kalshi.py`
  (imported by `kalshi_nfl`);
- `research/nfl_matchup_dataset.py`: the prospective dataset build's
  Sleeper parser (`load_sleeper`, the box: the decisive test's outcomes).

Audited by listing every repository module loaded by each path: the frame
build, the four commands, the dataset build (`build --out`) and the store
build (`build-stores`, a fresh 2026 download, 9 s). The only others loaded
are `errlog.py` (the ledger) and `research/__init__.py` (empty), which
change no number (`research.nfl_rush_model_fit.NEUTRAL`). The dataset and
store builds record the sha256 of every module they ran, and `prospective`
refuses either unless each is a registered file at its registered bytes
or one of those two.

The registered data (`REG_DATA`):
- `research/data/nfl_rush/rb_games_2023_2026.csv.gz`,
  `research/data/nfl_rush/qb_games_2023_2026.csv.gz`;
- `research/data/nfl_rush/reliability.json`, `research/data/nfl_rush/schema.json`;
- `research/data/nfl_matchup/reliability.json`,
  `research/data/nfl_matchup_model/k_fit_only.json`;
- `research/data/player_weeks_2022_2025.csv.gz`.

**Refusals.**
- **Registration** refuses:
  - without the orchestrator's go;
  - a frame whose code or any input changed since it was built (`stale_frames`);
  - a fitted input that is pending or carried by no row (`blocked_inputs`);
  - a second registration.
- **Every command** (`research.nfl_rush_model_rule` `validate`, `check2026`,
  `export`, `prospective`) refuses unless `prereg.json` exists and this
  block and every registered file still hash to it (`prereg_status`). The
  held seasons (2025, 2026) become readable ONLY through that check;
  there is no constant to edit.
- Each command also refuses a frame that is not the registered one, and
  runs in order (validate -> check2026 -> export -> prospective).
- Each command re-verifies the store pickles its distribution parts read
  directly (the season stores of its fitting seasons) against the
  registration: by sha256, or, re-saved or rebuilt, by content digest; a
  mismatch refuses, naming the parts that differ (`verify_stores`).
- Each decision record carries the registration it ran under (this block's
  sha256 and time) and every registered file's sha256 at run time; the
  next command refuses a record that does not match `prereg.json`, or whose
  link to the record before it is broken (`read_record`).
- Each record is written once; `--force` re-runs and says so. `coefs.json`
  is written once per stage (export, then prospective), and export refuses
  after the prospective lock; the board refuses a provisional-stage
  `coefs.json` with the lock beside it (an older stage restored).
- **The decisive run executes from the stored copies.** The registered set
  includes live production files (`nfl_rush.py`, `nfl_matchup.py`,
  `kalshi_nfl.py`) that others edit; `prereg_status` would refuse after
  any edit. The prospective dataset, frame and test therefore run in a
  registered tree (Q1). The owner should know the live files may change
  meanwhile; the registered bytes stay in `prereg_code/`, for anyone to
  re-hash. The tree's report carries the block stored there
  (`rule_block.md.txt`), so an edit inside the live report's block after
  the hash cannot stop the run (Sonnet's F5e).
- **The plumbing run** (`--plumb`) exercises the whole chain with every held
  outcome replaced by a random 2023 row's, never reading a held one (the
  distribution parts are then fit on the open seasons only), and writes
  only under `research/data/nfl_rush_model/plumb/`. It is disclosed in
  section 6.

The QB's own split (the board's information) is the rushing layer's `qb()`
through `nfl_rush.qb_profile`, never the first model's QB profile, whose
"scramble" reads 0 for every QB (C6).

### Q10. Data licences (the default; the orchestrator, 2026-10-24)

- **Never fitted:** any PFR-, PFR-snap- or NGS-derived field, in any fitted
  group (OVD, SPD, CFP, DEC, FULL), the comparables' vector or the trend
  terms, with the one exception of participation (below).
  - Sports Reference's terms (May 19, 2023, section 5) bar using its
    Content for "supporting machine learning methods used to predict,
    classify, label, or score inputs into the models". PFR advanced stats
    and snap counts are its Content even via nflverse.
  - NGS is the NFL's, whose terms bar using the Services to gamble
    (`research/reviews/rush_data_sources_opus.md` F8).
- **Fitted inputs come only from** the pbp, participation, FTN, the injury
  reports and the pre-game line, or are derived from them.
  - Every input declares its source (`nfl_rush.PATH_SOURCE`, `EXT_SOURCE`).
  - A guard fails on an undeclared source, a PFR / NGS source, or a PFR /
    NGS path name.
  - It also holds every declaration EQUAL to the data layer's own tag
    (`nfl_rush_profiles.SOURCE_OF`), and fails if `restricted()` is true
    for any fitted input.
  - A special-teams-only PFR snap line moves no fitted input (guarded on
    the data layer's fixture): the usage windows count pbp and
    participation appearances only.
  - PFR never chooses a fitted or scored row either: the row selection
    reads Sleeper's line, Sleeper's gp and the pbp only (Q1, guarded).
- **The board may still show** PFR / NGS fields (snap share, yards before /
  after contact, broken tackles, missed tackles, PFR pressure, RYOE). Each
  is credited ("Pro Football Reference via nflverse", "NFL Next Gen Stats
  via nflverse"), and its basis says it does not move a probability.
- **Participation** keeps the shipped first model's status, by the
  orchestrator's decision.
  - nflverse took its records from NGS through 2022 and from FTN from 2023.
  - Every frame reads participation from PRIOR seasons only (Q1). The 2023
    rows read 2021-22 and the 2024 rows 2022-23, so part of what the
    fitting seasons read is NGS-originated. It is the one NGS-originated
    input this rule fits; "never fitted" covers every other NGS field.
  - Credit: "NFL Next Gen Stats via nflverse (2022 and earlier); FTN Data
    via nflverse (2023 on)". FTN: "FTN Data via nflverse" (CC BY-SA).
- **The pre-game line** (game script, kneels) is nflverse's closing line in
  research and Kalshi's de-vigged price in production. It is in the fitted
  set as "lines"; it is neither PFR nor NGS.
  - The orchestrator confirmed this source on 2026-10-24, with one caveat.
    The nflverse nfldata documentation (DATASETS.md, the Games section,
    fetched 2026-10-24) defines `spread_line` and `total_line` but names
    no source for them, so their provenance is UNDOCUMENTED.
  - Production reads Kalshi's own de-vigged game prices instead.
  - The kneel and game-script terms read only the line, never the result.
- **Adding PFR or NGS later** is a NEW pre-registered group, judged
  prospectively, and only if the owner approves. Nothing in this rule
  fits one.

Every command refuses when a file or this block no longer hashes to what was
registered.
<!-- RUSH-PREREG-END -->

### Runbook: the hash and the decisive run (outside the hashed block)

**Registered 2026-10-29T02:13:45Z**, block sha256 `92a99b8e791a7a98ec9e6da849ad9b07b751fd5cab6bd51c03f70d13fc0fbe61`,
`DEEP_CACHE_DIR` = deep3 and the two frames exactly as dry-run (nothing rebuilt or changed first).
`prereg.json` (the file 907f0bed…), `prereg_code/` (the 17 code copies and `rule_block.md.txt`),
`prereg_manifest.json` and `prereg_manifest.sha256` sit in `research/data/nfl_rush_model/`.

**What the registration pins, and where each piece lives.** Every command re-hashes, from the WORKING tree:
- the 7 REG_DATA files (`prereg_status`). Three are tracked and clean; `research/data/nfl_rush/reliability.json`
  and `schema.json` are committed with the registration; the two dataset CSVs
  (`research/data/nfl_rush/{rb,qb}_games_2023_2026.csv.gz`, f39a12ed… / d7a42025…) carry PFR- and NGS-derived
  columns (Q10) and are kept OUT of the public repository (`.gitignore`): the owner's archive holds them;
- the two frames, by sha256 (`require_frame`): `rush_frame_val.csv.gz` 906e9a7e… and `rush_frame_test.csv.gz`
  b3a28947… with their `.meta.json`. Both carry one restricted-derived column, `y_played` (section 4), so they go to
  the private archive with the pickles; committing them is the orchestrator's call;
- the 17 store, aux and ids pickles, by sha256 or content digest (`verify_stores`, `provenance_refusal`): the
  2019-2026 stores and aux and the ids pickle of `<scratch>/nfl_rush_model/deep3` (deep3 also holds 2018 and the
  comparables' history, which nothing verifies). `deep_cache.save` stamps the save time into every pickle, so none
  can be rebuilt to the same sha256; a rebuild is accepted by content digest (Q9: 2023's pair, and in Sonnet's run
  all 14 older pickles, rebuilt from a fresh download on 2026-10-28/29, matched), but a season nflverse has revised
  since would refuse, so the archive is the primary path;
- the week-6 props log the test frame read (`research/data/nfl_rush_model/inputs/nfl_props_2026_w06.json`,
  9d951cf7…; Kalshi's prices, so `.gitignore`d): sim-history's `props/nfl_props_2026_w06.json` is byte-identical.

`prereg_manifest.json` / `.sha256` list the frames, their build records and the 17 pickles (21 lines, each with its
sha256 and where it lay); `prereg.json`'s `data` holds the dataset CSVs' sha256.

**At the hash (done 02:13:45Z) and right after it:**
1. Archive, exactly as listed: the 21 manifest files and the two dataset CSVs -- the owner's private copy (the
   orchestrator's insurance) and one more place the owner controls, never a live deep folder (a live job rewrites
   the 2026 pickles: Opus's F1a). Check each copy (`sha256sum -c prereg_manifest.sha256` for the 21; the CSVs against
   `prereg.json` `data`) and record both locations here.
2. Commit exactly the frozen files and the registration's artifacts (`prereg.json`, `prereg_code/`, the manifest, this
   report, `research/data/nfl_rush/{reliability,schema}.json`) and push once, before the first window kickoff
   (Thursday 2026-10-29, 8:15 pm EDT = Friday 00:15 UTC = 6:15 pm MDT).
3. Then `validate`, `check2026`, `export` with `DEEP_CACHE_DIR` = deep3 (or the 17 archived pickles in a scratch
   folder); commit their records and the provisional `coefs.json` (a second push).

**At the decisive run** (the earliest: Friday 2026-11-27, with the window through week 11). Before ANY command and
before `registered-tree` (which copies the working tree, untracked files included):
1. Restore into the WORKING tree every untracked file the registration names, each checked by sha256: the two
   dataset CSVs from the archive, against `prereg.json` `data` (without them `prereg_status` refuses every command;
   `research/data/nfl_rush/{reliability,schema}.json` come committed with the registration and the other three
   REG_DATA files are tracked); and the week-6 props log into `research/data/nfl_rush_model/inputs/` from
   sim-history's `props/nfl_props_2026_w06.json` (9d951cf7…, byte-identical to the one the test frame read,
   checked 2026-10-29: only a rebuild of the test frame reads it, but `registered-tree` copies the working tree as
   it stands, so restore it first).
2. Put the two frames where the commands' `--frame` / `--frame-val` will name them, checked by sha256.
3. `python3 -m research.nfl_rush_model_fit registered-tree --dest <an empty folder>`: it prints the commands below
   with the records' folder filled in by absolute path.
4. An EMPTY scratch deep folder; copy into it, from the archive, the 14 older pickles (2019-2025 stores and aux),
   each checked against the manifest (the 2026 pair and the ids are built by build-stores, which moves any copy in
   the folder aside).
5. In the tree, with that folder as `DEEP_CACHE_DIR`: `build-stores`; the dataset `build --all-weeks --out <records
   folder>/prospective_dataset`; the prospective frame; `capture-missing` for a week with no props log (before the
   box); `prospective`.
6. The archive lost: `build-stores --older 2019 2020 2021 2022 2023 2024 2025` rebuilds the 14 beside 2026, and each
   must match the registration's content digest (a revised season refuses, naming what differs).

**Known limit (next freeze, Sonnet's R4-F3).** `content_digest` builds its top-level digest from the parts whose
name has no ".", because a sub-part is named `key.sub`: a TOP-level payload key containing a "." would be left out of
the digest. None of the 36 pickles scanned (the 17 registered, Sonnet's rebuilt ones) has one, so nothing is affected
today; a future digest should keep sub-parts apart by structure, not by a character.

### Decisions (the orchestrator, 2026-10-23)

1. **Gate.** The DEC rung faces the same 2 SE gate and per-threshold floor
   as every mean term. There is one standard.
2. **Order.** The owner's. Since the 2026-10-24 decisions (section 7, #5)
   the decomposition is two steps, SHAPE fifth and DEC sixth, each gated;
   no other order is run.
3. **Prospective window.** Every game kicking off after the registration
   time, through week 11, on frozen pre-kickoff lines (Q1). The props log is
   checked to carry RB and QB rushing projections.
4. **Unpriced.** The protected, ladder and escalator series are not priced
   (`nfl_rush.SERIES`). Priced: KXNFLRSHYDS, KXNFLRSHATT, KXNFLLONGRSH and
   KXNFLTD for both positions, and KXNFLRRYDS, KXNFLREC and KXNFLRECYDS for
   RBs.

### Kalshi, as the data-source report read it (2026-10-23)

- **Which series list RBs and QBs.**
  - KXNFLRSHYDS and KXNFLRSHATT list both (149 and 20 QB rungs that
    weekend).
  - KXNFLRRYDS (rush + rec) lists RBs and WRs, no QBs.
  - KXNFLTD is the anytime TD; its contract excludes passing TDs, so a QB's
    TD market is his rushing (and receiving) TDs. That is `nfl_rush`'s
    `td` for QBs, and a guard pins it.
- **Settlement.** Settled 2025 QB rushing ladders bracket the total WITH
  kneels and scrambles in 239 of 239 (9.2), as this model counts them.
- **The app reads only five series** (`kalshi_nfl._PROP_SERIES`: pass yds,
  rush yds, rec yds, receptions, TD). Rushing attempts, rush + rec and
  longest rush need the shared index to read KXNFLRSHATT, KXNFLRRYDS and
  KXNFLLONGRSH. That map also feeds the game engine's combos, so the
  proposal (in the hand-off, when the board needs it) is a SEPARATE map for
  the matchup board, not an edit to `_PROP_SERIES`.

### 2026 feed lag (a risk for the prospective window, not a leak)

FTN charting for 2026 ends at week 6 (PFR too, display only), participation
has no 2026 file, and NGS rushing has no QB rows. As of a week-8 to week-11
game, a feature reads whatever those feeds held before kickoff. If they still
stop at week 6, the in-season part is staler: still leak-free, but weaker.
The prospective frame RECOMPUTES its features after the window from every
game before each row's (Q1, C1 option b), so it does not carry the live
board's lag. A split of the result by that lag would need the board's
provenance stamps, which no build logs yet; the `live_lag` column stays
blank and the report says so (Q1).

### The board hook (built 2026-10-24; inert until coefs.json exists)

- **The gate.** `nfl_matchup._board` reads `nfl_rush.load_coefs()` once.
  With no file (or an unreadable one, ledgered `NFLR-coefs`) it builds the
  board exactly as before: no `extra_props()` call, no snapshot, no new key.
  Section 5 proves the bytes.
- **The snapshot, once a board.** `_rush_env` runs in the board's own niced
  child: `kalshi_nfl.extra_props()` (with the market), the data layer's
  snapshot as of the week through `nfl_rush.availability_view`, every
  team's blocks, the league means, and (only when a fitted model reads a
  comparables term) the weekly vector history, stored in the deep store.
  An unaligned or incomplete aux turns the hook off for the board,
  ledgered `NFLR-board-aux`, with a slate note.
- **Per RB / QB.** `_rush_player` builds the features through the same
  adapter the frame used (`nfl_rush.features`), calls `nfl_rush.plan`, and
  prices only the forms the rule shipped (`compound`, `poisson`, `mean`,
  `derived`). `rung1` and `none` leave the first model's block alone. A
  failure is ledgered `NFLR-board-player` and the player keeps the first
  model's markets.
- **The ship gate fails closed.** Only "shipped" and "provisional" are
  read; any other status, None included, is not shipped and is ledgered
  (`nfl_rush.shipped_groups`). A compound whose grid wrapped is never
  priced (`NFLR-grid-wrap`).

### What the data layer must provide (the adapter waits for its signatures)

**Per RB, as of the week:**
- carry share and its trend; snap share (PFR: display only, Q10);
- early-down, third-down, red-zone and goal-line carry shares;
- edge / interior run shares (`run_gap` end vs tackle / guard; `run_location`);
- YPC by box (light / heavy);
- yards before and after contact per carry, broken tackles (PFR: display
  only, Q10);
- RYOE (NGS: display only, Q10);
- targets per on-field dropback (prior seasons), screen share, target rate
  under pressure.

**Per QB, as of the week:**
- designed runs per game, sneaks per game;
- the scramble rate per dropback: overall, under pressure, clean, vs blitz,
  vs man / zone, vs single / two-high;
- yards per designed run and per scramble;
- the shares of his non-kneel attempts.

**Per defense, as of the week, opponent-adjusted:**
- run defense: YPC, success, EPA, stuffs and 10+ runs allowed, overall and
  by edge / interior;
- box mix; line yards allowed (proxy); missed tackles (PFR: display only);
- RB rushing and receiving allowed;
- QB rushing allowed per designed run and per scramble (adjusted for the
  QBs faced);
- scramble frequency allowed; sacks + QB hits per dropback (pbp), blitz,
  man share.

**Per team:**
- neutral run rate, pace;
- OL listed Out / Doubtful on the week's report (injuries; the snap-based
  starters are PFR's);
- the QB's designed share vs the RBs' carries, and the goal-line QB vs RB
  share.

## 4. The frame and its audit (no 2025 / 2026 outcome read)

**What these frames are built on.**
- The agreed data layer, round 5 (round 4's licence fix -- the usage windows count pbp and
  participation appearances only -- plus round 5's tag-only change: every other `usage.*` /
  `recent.*` RB field tagged `derived:participation+pbp`, both parts unrestricted, no value moved;
  `nfl_rush.PATH_SOURCE` follows it): `nfl_rush_data.py` 247cc625, `nfl_rush_profiles.py`
  0d319ed0, `research/data/nfl_rush/schema.json` 18b581ac, and `research/nfl_rush_dataset.py`
  8b97b7af (82e4aa12 plus the `--out` flag and the code record below, and round 4's `--all-weeks` /
  `--max-week` and record of the pickles it read; none touches a row its default build writes).
  The registered dataset's own `meta.json` (ced28e4c, an input of both frames) predates the code
  record: it was written by the builder before `--out`. Only the PROSPECTIVE dataset's meta is held to
  a code record (`provenance_refusal`), so nothing reads the registered one's for that (Fable's n2).
- The round-3 stores and schema-2 aux, unchanged in round 4, copied and checked by sha256. Every
  season's aux is aligned AND complete (`check_inputs`).
- The licence-default feature lists (Q10) and the row-selection rule (Q1).

**Two frames** (`research/nfl_rush_model_frame.py`, rebuilt on the round-4 FROZEN bytes 2026-10-28 22:36-22:42
UTC on deep3, the registration-era stores, never live feeds -- a live rebuild moves 93 of week 6's 95 frozen rows,
Sonnet's F5a -- in parallel, 316 s and 337 s; the same sha256 as every build since 06:55 on 10-24, cell for cell:
nothing since moved a row. Their `meta.json` sidecars record the 17 code files' sha256, the dataset's realpath and
its `meta.json` sha256, the box files read and the Python / numpy versions):
- val: k_fit_only (2018-2024), 2023-2025, 7,413 rows, sha256 `906e9a7e…`;
- test: the pooled k (2018-2025), 2023-2026 with the frozen 2026 captures, 8,242 rows, sha256
  `b3a28947…`.

Every input is named by its repo-relative path (the test frame: 63, the 38 capture files and their
`capture.json` among them; the week-6 props log is committed at
`research/data/nfl_rush_model/inputs/nfl_props_2026_w06.json`). The test frame no longer reads the
scratch Sleeper directory, whose revised projections postdate every kickoff and never froze a row:
its content equals the 05:56 build's to the cell except the props log's file name in `fz_src` (91
rows). Both are written with a zero gzip mtime and no name, so a rebuild re-derives the sha256. The
dataset builder gained `--out` (research/nfl_rush_dataset.py, the prospective dataset's separate
directory; nothing it writes by default moved) and records, in its `meta.json`, the sha256 of every repository
module the build loaded and the Python / numpy versions (C2b; a scratch build of the registered dataset with it
reproduced both CSVs byte for byte, f39a12ed / d7a42025).

Both go through `availability_view` (Q1), and neither reads a dataset column as a feature
(Q3a). Every fitted input is on the frames' rows (`paths_absent` empty). The registration dry
run (section 6) finds no stale frame and no blocked input.

**Every frame column against the licence tags** (round 4, the durability item's (c);
`<scratch>/nfl_rush_model/r4/audit/frame_columns.json`; column NAMES and their definitions only, no
value read). Both frames hold the same 120 columns:
- 54 features (`f_*`): each input declared through `nfl_rush.feature_sources`, every tag pbp,
  participation, FTN, injuries or the pre-game lines, none restricted;
- the QB decomposition (`qp_*`, 6): the rushing layer's `qb()` rates, tagged pbp or FTN+pbp;
- identity and baselines: ids and names (the crosswalk), the injury-report status, Sleeper's revised
  lines (`sl_*`) and frozen captures (`fz_*`: Sleeper's projections, by file name in `fz_src`), the
  kickoff (the pbp's start) and the pre-game line (`margin`, `total`: nflverse's closing line);
- bookkeeping: `k_mode`, `features_through`, `fz_lead_h`, `n_comp1` / `n_comp2` (the comparables'
  counts, pbp and participation), `live_lag` (blank);
- outcomes (`y_*`): Sleeper's box (`sl_act_*`) else the pbp's (`act_*`), every one pbp or Sleeper --
  except ONE: **`y_played`**, the dataset's `act_played`, tagged `derived:participation+pbp+pfr_snaps`
  (PFR snaps give a back `act_played` 1). The rule never reads it (played is `y_cond_played`: Sleeper's
  `gp` or a pbp touch, Q1).

So the frames are the kind already committed: the registered rushing dataset (`research/data/nfl_rush/`)
carries `act_played` itself and 51 other restricted RB columns (23 QB). If the frames are to hold no
PFR-derived cell at all, `y_played` has to leave the frame build, which changes both frames' bytes and
needs another rebuild and look; my proposal is to commit them as they are.

**Round 4 against round 3** (every column compared on 2023-2024; on 2025-2026 every feature,
line and identity column, never an outcome):
- `f_dec_trend` moved on 2,389 rows (729 / 770 / 714 / 176 a season): the layer's round-4 fix.
- New columns: `live_lag` (blank, Q1), `y_gp`, `y_pbp_play`, `y_cond_played` (Q1's played).
  `y_src` relabelled 20 fitting-season rows (a target or a dropback now counts as a pbp
  appearance).
- 2026's frozen lines: the week-agnostic reader (decision 2) finds a capture for 449 rows of
  weeks 1-6 (438 before; 11 more in weeks 1-3, mapped through the roster or another week's
  captures). It moved 129 rows' source, 254 rows' lead time, and the line itself on 8 rows'
  rushing and 11 rows' receiving fields. No universe count changed.
- The capture clock (a capture counts only when its recorded fetch precedes kickoff, too)
  removed no row: every capture was fetched before its game.
- Every other cell is identical, and the 2024 traincheck through the val frame is section 1's,
  to every digit.

**Rows per market.** Rows / games, on Sleeper's REVISED line for 2023-2025 and the FROZEN capture
for 2026. These are counts only: the audit loads no outcome column. They are unchanged since
round 3, the RB TD universe's new "projected carries or targets" condition included (no TD row
lacked them).

| market (Q1 universe) | 2023 | 2024 | 2025 | 2026 wk 1-6 (frozen) |
|---|---|---|---|---|
| RB carries, rushing yards, rush + rec, longest rush | 838 / 272 | 839 / 272 | 871 / 272 | 182 / 57 |
| RB receptions, receiving yards | 631 / 270 | 667 / 272 | 612 / 272 | 133 / 57 |
| RB anytime TD | 966 / 272 | 979 / 272 | 1,063 / 272 | 212 / 57 |
| QB attempts, rushing yards, TD, longest rush | 420 / 259 | 441 / 263 | 456 / 262 | 95 / 55 |

- **2026:** 829 rows in weeks 1-6, of which 449 have a frozen capture.
- **Weeks 4-5 have no capture,** as the first model found. The secondary check therefore reads
  weeks 1-3 and 6, 57 games.

**Missing features, 2025 and 2026 rows** (counts; a sum is missing only when every part is).
Every feature not listed has zero missing. Unchanged since round 3.

| market family, feature | 2025 | 2026 | why |
|---|---|---|---|
| RB rushing, `dec_trend` | 18 / 871 | 14 / 182 | mostly week 1: no recent window yet |
| RB rushing, `full_comp_rush_yd` | 11 / 871 | 2 / 182 | no past game in the window |
| RB receiving, `cfp_rec_mz` | 170 / 612 | 28 / 133 | no prior-season on-field dropbacks (rookies, new backs) |
| RB receiving, `full_comp_rec_yd` | 4 / 612 | 2 / 133 | no past game |
| QB rushing, `cfp_dsg_edge` (dead) | 67 / 456 | 3 / 95 | no designed run in the window |
| QB rushing, `full_comp_qb_rush_yd` | 4 / 456 | 0 / 95 | no past game |

`cfp_ol_out` and `dec_room_out` are missing nowhere in 2025-26; a missing one reads its
training mean (`MEAN_FILL`). In the fitting seasons they are missing on the backs of two team-weeks (ATL 2023 week 5,
DEN 2024 week 1: the team has no rows in that week's report), which now read the training mean.

- **The fitting seasons.** In 2023 the QB scramble splits (`cfp_scr_press`, `cfp_scr_man`,
  `cfp_scr_shell`, `full_scr_x_margin`) are missing on all 420 QB rows. Participation charts
  scrambles only from 2023, and under the mask a 2023 row reads 2021-22. They read 0 on 2023 and
  are fitted from 2024's rows (Q2: two folds, a column constant within one).

**Against the dataset's own columns** (features only):
- `cfp_ol_out` equals `ol_ol_listed_out` on 3,614 of 3,614 RB rows of 2023-24.
- `ovd_pressure` equals log `dq_hit_sack_rate_oe_ratio` on 1,326 of 1,326 QB rows of the val
  frame, within the CSV's rounding (the test frame differs by design: the pooled k).
- `dec_trend` equals `p_recent_carry_share - p_usage_carry_share` on all 539 2026 RB rows, and on
  2,773 of 3,370 (2023-24) and 1,305 of 1,662 (2025) earlier ones. That split is the mask, as
  designed: round 4's appearance rule reads participation's on-field snaps, the dataset is built
  WITHOUT the mask, and the frame reads the season's own participation as 2026 does, not at all.
  Where the mask has nothing to remove (2026), the two agree on every row.

**What the participation mask changes** (measured on the pre-rebuild layer, features only):

| feature | rows changed, 2023 / 2024 / 2025 / 2026 |
|---|---|
| `cfp_box_split` | 1,081 / 1,404 / 1,528 / 0 |
| `cfp_def_light` | 295 / 1,511 / 2,272 / 0 |
| `cfp_def_heavy` | 8 / 1,412 / 428 / 0 |
| `cfp_pers_split` | 1,670 / 1,588 / 1,605 / 0 |
| `cfp_front_dl4` | about 2,300 a season / 0 |
| `cfp_contain` | about 2,300 a season / 0 |
| `cfp_rec_mz` | about 1,600 a season / 0 |
| the QB scramble splits | about 620 a season / 0 |

On 2026 the mask changes nothing, which is the check that it puts 2023-2025 exactly where 2026
is. In round 4 it also moves `dec_trend` (above).

## 5. The board hook, measured (2026-10-24)

**Byte identity with no model.** The pre-hook `nfl_matchup.py` (HEAD plus the orchestrator's
`_kalshi_lines(extra=)` edit, reconstructed) and the current one, with no `coefs.json`, build the
2026 week-7 board (Sleeper's week-7 projections as fetched 2026-10-23 00:58 UTC,
`<scratchpad>/data/sleeper/proj_2026_7.json`, 294 players; the deep store; no market) to the same
bytes: sha256 `a4123646ba87e88185aa83997727f52ee7f12d48fa8ffc15fb81dd7ffab74b38`, 7,314,228
bytes of sorted JSON, both (`<scratchpad>/nfl_rush_model/identity_proof.py`). Re-run on the final
code at 07:25 UTC: identical again. The Opus re-check reproduced the same digest against git
HEAD's module; the Sonnet re-check, on other captures, found identity on each. The guard holds the same property on a fixture, and holds
that no `extra_props()` call and no snapshot happen.

**Cost.** One live week-7 board each, Kalshi live, a fresh niced process
(`<scratchpad>/nfl_rush_model/boardmeasure/run_board.py`), with a MEASUREMENT model (2023-24
distribution parts, SHAPE shipped on every RB / QB market, one SPD term per target; never
committed):

| board | 02:08 UTC (the hook as built) | 05:56 UTC (the final code) |
|---|---|---|
| hook off (`rush_coefs=False`) | 5.6 s, 151 MB peak | 15.8 s, 151 MB |
| hook on, the SHAPE model | 21.9 s, 219 MB; snapshot 2.3 s | 28.0 s, 220 MB; snapshot 3.0 s |
| hook on, plus a comparables term, first build (computes and stores ~40 weekly vectors) | 102.8 s, 277 MB | not re-run (the path is unchanged) |
| the same, the vectors stored | 26.8 s, 224 MB | 27.8 s, 224 MB |

Both runs priced 76 RBs / QBs with the rushing model, and 217 Kalshi rungs on the new series
(KXNFLRSHATT, KXNFLRRYDS, KXNFLLONGRSH) reached a player. The wall time includes Kalshi's live
fetches, which moved the hook-off board from 5.6 s to 15.8 s between the two runs; the hook's own
cost is about 12-16 s and 69-73 MB of peak memory on the board's child, and the payload grows
from 11.9 MB to 13.4 MB.

The snapshot is built once a board in the board's own niced child. The comparables' weekly vector
history is computed only when a SHIPPED group carries a comparables term, and then by the
`nfl_matchup` deep job (`nfl_rush.precompute_vectors`), never by a board: the 102.8 s first build
above (136 s in the Sonnet re-check, under contention) would otherwise run against the board child's
240 s timeout on Render's one core. A board reads the stored vectors only, and a week not stored yet
is skipped and noted. A failure anywhere in the hook's snapshot keeps the whole board, the first
model's (`RUSH-board`).

## 6. The plumbing run (no held outcome read)

Every command ran in order on a COPY of the tree (`<scratchpad>/nfl_rush_model/plumb/plumb.sh`;
nothing was written into the repository), with `--plumb`: each 2025 / 2026 row's outcomes replaced
by a random 2023 row's of the same position (`rule.load`), the distribution parts fit on the open
seasons only (`rule.PLUMB`), the prospective window given registration time 0 and the test frame
as its prospective frame. Its numbers are noise by construction and decide nothing; what it shows is
that the chain runs and that what it writes is what the board reads.

Re-run 2026-10-28 22:43-22:49 UTC on the round-4 FROZEN code and frames (the 10-24 runs at 05:57, 07:02 and
09:13 gave the same decisions; the times below are this run's, beside the mutation run):

| step | seconds | wrote |
|---|---|---|
| `validate` (35 decisions, the dead features as Q2) | 259 | `validate.json` |
| `check2026` | 8 | `check2026.json` |
| `export` | 5 | `coefs.json` (stage "provisional") |
| `prospective` (window: week 11, 57 games; QB rushing yards 95 rows / 55 games) | 12 | `prospective_lock.json`, `coefs.json` ("shipped") |

On the donor outcomes the walk kept two groups (QB attempts FULL, QB rushing yards SHAPE), which
exercised the mixed path Q3b describes (the attempts in the mean form, the yards as the compound).
The final `coefs.json`, copied to the copy's `research/data/nfl_rush_model/coefs.json`:
`nfl_rush.load_coefs` REFUSES it (its records chain to no registration: the plumbing run has
none), as it must. Handed to the REAL board code directly (`rush_coefs=`) on the 2026 week-7
capture (`<scratchpad>/nfl_rush_model/plumb/board_check.py`):
- 25 QBs priced by the rushing model in each of attempts (NegBin, "shipped"), rushing yards
  (compound, "shipped") and the longest rush (derived), with "shipped (one prospective test)" on
  each; every ladder monotone in [0, 1]; no NaN;
- every RB market, and every other QB market, byte for byte the first model's block;
- all 193 rows that are not RBs or QBs byte for byte the hook-off board's;
- `model.rushing` names the fit seasons and both statuses (the registration's sha is null: the
  plumbing run has none).

The board check was re-run on the round-4 code (2026-10-29 01:28 UTC): the same 25 QBs in each of the three
markets, the 193 other rows byte for byte, no NaN, the snapshot 2.62 s.

**The registration dry run** (`research.nfl_rush_model_fit register --dry-run`, `DEEP_CACHE_DIR` = deep3, on the
frames rebuilt on the round-4 frozen bytes, 2026-10-28 22:42 UTC):
- rule block sha256 `92a99b8e791a7a98ec9e6da849ad9b07b751fd5cab6bd51c03f70d13fc0fbe61` (the block as frozen
  after the final confirmations, 22:36 UTC; any edit to it changes this);
- 35 decisions; dead features RB `cfp_direction`, QB `cfp_dsg_edge`, `spd_qb_td_oe` (each
  position's list also holds the other position's features, which its rows never carry);
- no stale frame, no blocked input;
- frames: val `906e9a7e184f5ece84ee6f7ede5ab3ab67da47ca2db1fbe8aab42914d42ee9df`, test
  `b3a289470aaf98653a7aae010a4702df432f5de940f17b345157c2f7c454e0bc`;
- Python 3.11.17, numpy 2.4.6;
- it stores the 17 registered code files (Q9's list: the rushing model's five, the data layer's three, the
  matchup model's five, `kalshi_nfl.py`, `deep_cache.py`, `kalshi.py` and `research/nfl_matchup_dataset.py`, the
  Sleeper parser the dataset build loads, registered in the Opus confirmation's C2a) and hashes the 7 registered
  data files of Q9; `errlog.py` and `research/__init__.py` are the two neutral modules (loaded, not registered);
  five changed in round 4 (`nfl_rush.py`, the frame, the fit, the rule and the dataset build), the other twelve
  and every data file are the round-3 bytes;
- every deep pickle the frames read, 17, by sha256 and content digest (1.4 s for all), and the ids pickle's
  positions and Sleeper ids (6,476 players) -- what a real registration writes to `prereg.json` and the manifest.
- The plumbing run above is the frozen code's; the golden guard (section 8) runs the four commands end to end on
  it as well.

## 7. The rule reviews, answered (`research/reviews/nfl_rush_rule_{opus,sonnet}.md`)

### The orchestrator's sixteen decisions, where each landed

| # | decision | where |
|---|---|---|
| 1 | B1: the ladder fit and the four commands, each refused without a matching `prereg.json` | `research/nfl_rush_model_rule.py` (`validate`, `check2026`, `export`, `prospective`; `require_registered` -> `prereg_status`); the hold is `HELD` + `prereg_status`, no constant to edit; guards and section 8's mutants |
| 2 | the prospective frame | week-agnostic `frozen_lines`; capture glob `proj_2026_*.json`; built after the window by the registered code, its meta recording every input's sha256; `prospective_frame_refusal`; guard: a back first seen after week 7 is kept |
| 3 | no free intercept; level fixed to Sleeper | `fit_ridge` (no intercept column), `nfl_rush.linear` (centred, no intercept); Q2; level recalibration OUT; Q8 discloses the 2025 levels |
| 4 | rung-1 parameters refit per step | `dispersion` / `parts_for` on 2023-24 for validate, 2023-25 for the rest; the first model's committed CVs never read |
| 5 | SHAPE, then DEC, each gated | `nfl_rush.LADDER`, `SHAPE_MARKETS`, `market_groups`; recount in Q3b |
| 6 | the spec holes | Q2 / Q3b: the QB component map, each GLM's outcome and offset, the walk order, the DEC coupling, the TD link, the fit universe, the SD rows, the offsets' chain, the baselines, the export refit |
| 7 | dead features | `dead_features` (zero training SD in EITHER frame); groups left with no live term are not evaluated; Q2 discloses run direction and QB TDs allowed untested |
| 8 | ship status, derived markets, the replay | `SHIP_STATUSES`, `shipped_groups`; `plan` prices a derived market only through compound components; guard: export -> board -> the walk's scored probability |
| 9 | conditional on playing | `y_cond_played` (Sleeper gp or a pbp touch); all-rows reported; PLUS the add-on: no restricted column selects a row (`SELECTION_COLS`, Q1, guarded) |
| 10 | the dec_trend licence leak | frames rebuilt on the round-4 layer (section 4); guard: dec_trend's inputs unrestricted under the new tags; the special-teams-only PFR line moves no fitted input |
| 11 | Q8 completeness | Q8 |
| 12 | the longest rush and derived markets | `THRESHOLDS` long_rush 10/15/20/25/30/40; derived scored deciding nothing; priced only through shipped components; nothing shipped -> no new market |
| 13 | SE and the prospective keep | `paired` (max of game- and player-clustered), `keep_prospective` (d < -1 SE); the error rates computed (Q3b) |
| 14 | build records | frame meta: dataset, k tables, every aux / store pickle, props logs; `stale_frames` compares all; `BUILD_CODE == REG_CODE` |
| 15 | the named guards | `tests/guards_nfl_rush_model.py` (`leak_checks`, `rule_checks`, `reg_checks`, `selection_checks`, `prosp_checks`) |
| 16 | every minor and nit | below |

### Opus

- **m1 (the family-wise error rate).** Computed, not asserted:
  `research.nfl_rush_model_rule.error_rates` on the block's own decision
  count, and a guard holds Q3b's figures to it (it caught a 3.55% rounded as
  3.6%).
- **m2 (the window sentences).** Q1 names the clock (the scheduled kickoff,
  the store's Eastern `kick`), says week 7's first Sunday kickoff is 13:30
  UTC, drops the week-8 promise, and says what happens under 40 games at
  week 13.
- **m3 (grid wrap).** `Compound.wrapped` compares the analytic mean with the
  grid's (beyond max(0.5, 1%) it wrapped), ledgers `NFLR-grid-wrap`, and
  `plan` refuses the market. Guarded on your case (14 carries, 500 yards:
  mass_raw 1, wrapped detected, unpriced).
- **m4 (missing counts read "nobody out").** `nfl_rush.MEAN_FILL`: the two
  counts read their training mean, on the board and in the fit
  (`fill_column`); guarded.
- **m5 (the QB breakdown).** The position's components are labelled
  "(position split)", their basis says the split is the same for every QB,
  and his own split sits beside them as shares and as the counts it would
  give from the same attempts, information only; guarded.
- **m6 (wording).** The floor says the NUMBER of thresholds; QB TD is
  rushing + receiving (KXNFLTD); the RB receiving universe needs a
  receptions line; the fit universe is the scored universe; lambda's
  folds are the fitting seasons only. All in Q1, Q2, Q4.
- **m7 (the props log, the fallback captures).** Q1 says the props log keys
  by name only. A capture counts only when its Sleeper `updated_at` AND its
  recorded fetch (`capture.json` `captured_utc`) precede kickoff; guarded.
- **m8 (KXNFLTD).** Q1: the outcome counts rushing + receiving TDs; Kalshi
  also counts return and fumble-recovery TDs, rare for RBs.
- **m9 (build records).** `BUILD_CODE` is every registered file
  (`kalshi_nfl.py` included, guarded equal to `REG_CODE`); each frame's meta
  records every input it read, deep-store pickles as `deep:<key>`, and the
  registration stores those records.
- **N1 (freeze the tree).** Registration refuses a stale frame, so a change
  after the frames are built cannot slip through; the frames in section 4
  were built on the final bytes of every registered file, and the tree
  should stay frozen from the hash review on.
- **N2 (lead times).** Every comparison reports its rows' capture lead time
  (min / median / max).
- **N3 (rescale above 99 yards).** Measured on the 2023-24 kernels: at the
  clamp's scale (e), 0.58% of an RB carry's mass (1.07% of a catch's, 0.44%
  of a designed QB run's) lies beyond 99 yards; at 1.5, 0.08%. Not changed:
  no priced threshold separates a 99-yard run from a longer one (the highest
  are 100 game yards and a 40-yard longest), the rescale keeps the mean
  exact, and the grid holds 895.
- **N4 (the sha and UTC in the report).** At registration: the status line
  will carry both, beside `prereg.json`.

### Sonnet

- **m1 (Q8's data-layer reads).** Q8 lists DECAY, BACK, the sneak rule's
  precision and recall, the proxy's r, the pooled k, the QB-game box-vs-pbp
  match and the settlement bracket.
- **m2 (participation is NGS to 2022).** Q3a and Q10 say participation keeps
  the first model's status by the orchestrator's decision and is the one
  NGS-originated input fitted (the 2023 and 2024 rows' prior seasons).
- **m3 (a sum drops a missing part).** Q1 and Q3a say so; section 4 counts a
  sum as missing only when every part is.
- **m4 (TDs from the pbp).** Q1 says so.
- **m5 (no production wrap check).** Opus m3.
- **m6 (`NFLM_K` set after the import).** The frame build asserts
  `nfl_matchup.K_MODE` equals the frame's k mode; guarded.
- **m7 (props log by name).** Q1.
- **m8 (content time; secondary-check lags).** Q1 says content time, and the
  2026 check reports each comparison's lead times.
- **m9 (two folds).** Q2 says two folds make a coarse choice and that a
  column constant within one fold is penalised like the rest.
- **m10 (Q3's derived markets).** Q3 says the TD markets read their own
  groups (QB: FULL, its SPD group dead) and that the derived markets are
  scored rung by rung, deciding nothing.
- **m11 (the extension's count).** The count is games holding a universe
  row with a frozen capture, from pre-game information only
  (`cmd_prospective`), and under 40 at week 13 the test runs and says so.
- **m12 (the frames carry held outcomes).** Kept, by the orchestrator's
  decision 11: the reader refuses them until `prereg_status` holds, and Q8
  discloses that the files carry them.
- **m13 (`PENDING_PATHS` empty).** The guard sets a pending path itself and
  expects the block, an explicit expectation.
- **Nits.** Week 7: Q1 says 88-90 of 172 RBs. The same split for a designed
  runner and a pocket passer: the breakdown labels say so, and section 1
  carries the own-share compound's P(yards <= 0) of 0.18-0.22 against
  0.10-0.15 hit. The uncentred counts: m4 above. The RB receiving
  universe's `rec > 0`: Q1.

### The re-checks (2026-10-24, 06:05-06:35 UTC), answered

Both re-checks agree after fixes, with no BLOCKER. Every item, and where it landed:

| item | what was done |
|---|---|
| **R1 / R-M1** (both, MAJOR): the commands' bodies and the scoring core unguarded | `golden_checks`: the four commands end to end on a synthetic frame pair whose `sl_*` and `fz_*` lines differ (each the other's garbage), a planted carries effect absent from the prospective weeks, a receptions effect REVERSED in 2026 weeks 1-6, a noise group. It pins which frame, seasons, baseline and played rows each step reads, the store re-verification per step, the accepted / provisional / kept sets, the window (77 games: pre-registration week-7 games and week 12 out, rows without a frozen capture out), export = check2026's set as provisional, the final `coefs.json` = only the kept groups as shipped, both record chains, and the refusals (out of order, a second export, export after the lock, a second prospective, outside the registered tree, a forged `validate.json`, the wrong frame). `core_checks` pins `row_ll` (y == k hits, the clip, no distribution), the clamp, the ridge fit on a fixture (lambda 100 by leave-one-season-out, every column penalised, a negative outcome clipped), the floor's threshold count, the window at exactly 40 and at the registration instant, the dead-feature rule on a flip fixture, DEC-without-SHAPE as the mean form, `prereg_status` on a changed DATA file, and the plumbing donors replacing a held row's whole outcome block. The comparables guard has distinct opponent vectors (it was vacuous): the feature is non-zero, poisoning the row's own and later weeks moves nothing, poisoning an earlier week moves it. Section 8 has the three mutation tallies. |
| **R2** (Opus): the prospective dataset rebuilt in place over registered files | `research.nfl_rush_dataset build --out DIR` (three lines in the data layer's `main`, at the orchestrator's instruction) and `research.nfl_rush_model_frame --dataset DIR`. Q1 names the directory. `prospective` refuses a frame whose dataset is `research/data/nfl_rush` or unrecorded; guarded. |
| **R3** (Opus): the prospective frame's coverage | `prospective` derives the window's weeks from the store's schedule and the registration time through the window's last week, never from the frame's rows, and refuses a frame missing a week, a week with neither a props log among its inputs nor a `capture-missing` entry recorded before the box, a missing box file, a box fetched under 48 h after the last game, or a run under 48 h after it (`coverage_refusal`, guarded case by case). The capture files and their `capture.json` are frame inputs now; input paths are repo-relative (the week-6 props log is committed at `research/data/nfl_rush_model/inputs/nfl_props_2026_w06.json`, and the test frame no longer reads the scratch Sleeper directory, whose revised projections never froze a row). |
| **R4** (Opus): export after the lock | `export` refuses once `prospective_lock.json` exists; `coefs.json` is written once per stage (export via `write_once`; prospective after its lock, after checking the file on disk is this check2026's export); `nfl_rush.load_coefs` refuses a file whose statuses are not one stage's or do not chain to `prereg.json`, `validate.json`, `check2026.json` (and `prospective_lock.json` when shipped) beside it. Every decision record carries the registration's sha256 and time, every registered file's run-time sha256 and the versions, and `read_record` refuses one that does not match `prereg.json` or whose chain is broken. Guarded, a re-export after the lock included. |
| **R-m1** (Sonnet): captures unhashed; a scratch props path | as R3 |
| **R-m2** (Sonnet): the comparables guard vacuous | as R1 |
| **R-m3** (Sonnet): the hook's survivors | `hook_gate_checks`: `rush_coefs=False` never reads `coefs.json`; only RBs and QBs enter; a back outside the universe keeps the first model's markets; the pre-game margin reaches the kneels; an unmapped player keeps his row; a failing snapshot keeps the whole board. H05 (the merge order) is equivalent: the index and the extra series share no series. |
| **R-m4** (Sonnet): the 48 h rule; records without code shas; unchecked records | as R3 / R4. The box's 48 h is checked on each box file's modification time: a procedural guard against an early run, not a proof (anyone can touch a file); the frame's build time after the registration and the lock are the hard parts. |
| **R-m5** (Sonnet) / Opus m3: executed files outside the set | `deep_cache.py` and `kalshi.py` registered (16 code files); `errlog.py` and `research/__init__.py` named as behaviour-neutral (Q9); Python and numpy versions recorded at registration and in every record (recorded, not enforced); each command re-verifies the season store pickles its distribution parts read directly against the registered frames' record (`verify_stores`; the ids pickle grows in season and is not compared). `research/nfl_matchup_frame.py` stays registered though the rushing frame no longer calls it: harmless. |
| **R-m6** (Sonnet): the registered set holds live production files | the decisive run executes from `prereg_code/` in a registered tree (`registered-tree`); `prospective` refuses outside one; Q9 says the live files may change meanwhile. |
| **R-m7** (Sonnet): the frame's sha not reproducible | `write_frame`: gzip with mtime 0 and no file name; guarded (two writes of the same rows, a second apart, hash the same). |
| **R-m8** (Sonnet): the refusal is self-referential | agreed and inherent; the registered bytes sit in `prereg_code/` for anyone to re-hash, and every record names the code shas it ran on. |
| **Opus m1**: the comparables' first build on Render's one core | the vector history is built ONLY when a shipped group carries a comparables term (`nfl_rush.comparables_shipped`), and then by the `nfl_matchup` deep job (`nfl_rush.precompute_vectors`, PC first; the hand-off carries the one-call diff to `matchups.nfl_data_job`, which is not mine). A board reads stored vectors only (`compute=False`): a missing week is skipped, ledgered `NFLR-board-vectors` and noted, never computed. |
| **Opus m2**: a failure in `_rush_env` blanks the NFL board | the gate's whole env + board step is wrapped: any failure is ledgered `RUSH-board`, noted, and the board is the first model's; `_rush_player` catches every exception per player (`NFLR-board-player`). Guarded. |
| **Opus m4**: the extra series fetched every build | accepted: three Kalshi calls a build, every 15 minutes, while a model ships; said in `_rush_env`'s docstring. |
| **Opus m5**: "scheduled kickoff" | Q1 says the pbp's recorded start time. |
| **Opus m6**: a derived market's first priced form never scored | `derived_report` reports the candidate's own log loss, MAE and ECE where only it prices the derived market; Q4 says so. |
| **Opus m7**: the record chain; vacuous decisions | as R4; Q3b discloses the up-to-three vacuous DEC decisions beside the error rates. |
| **Opus**: `PLUMB` a module global | `parts_for(train, plumb)`, passed from each command's `--plumb`; guarded. |
| **the equivalent mutants** | of those their authors called equivalent, G4, WI9, F08, F14 and O13 are now CAUGHT (a one-cluster SE, the golden counts and the 2026 check's row count, `fit_seasons`, the pending set pinned); G09, PL4, B3 and H05 survive, with F06 (the inner fit clips again). Section 8. |

### The Sonnet confirmation (2026-10-24, 08:05-08:30 UTC), answered

| item | what was done |
|---|---|
| **C-M1** (MAJOR): the coverage check could not stop an early run (the store holds played games only; an empty schedule passed) | `window_weeks`: the window's weeks are EVERY week from the first game kicking off after the registration through the window's last week, a range; each must hold a game in the store; the last week must be CLOSED (the store holds a game of the week after it), and the last kickoff is that week's last game; an empty window refuses, and `coverage_refusal` refuses an empty set and an unknown last kickoff. Guarded on mocked schedules (stopping at week 9, empty, week 11's first game only, closed) and in the golden run (a store of weeks 7-9, a week 11 not closed). |
| **C-M2** (MAJOR): the registered tree could lose the once-lock | `resolve_model_out`: with the tree's marker present, the records' folder is the marker's `model_out` (the repository's), a disagreeing `NFLR_MODEL_OUT` refuses; `in_registered_tree` requires that folder and refuses one inside the tree; `registered_tree` refuses a records folder inside the tree it builds. Guarded: no variable, an agreeing and a disagreeing one, a folder inside the tree, and two trees sharing one lock. |
| **C-m1**: `record_chain` checked hashes only | it also requires the shipped groups to BE the record's set (check2026's provisional, or the lock's kept); guarded with the reviewer's forged file, provisional and shipped. |
| **C-m2** (nits) | `capture_missing.json` is bound to the registration (refused on a mismatch, in `prospective` and in `capture-missing`); `capture-missing` runs only in the registered tree; a guard pins the index's and the extra series' stats disjoint; the environment override stays, disclosed: the registration is an honesty fence, as the hold was. |

### The Opus confirmation (2026-10-24, 08:07-08:36 UTC), answered

| item | what was done |
|---|---|
| **C1** (= Sonnet C-M1) | `window_weeks` as above, with the closed last week; guarded on mocked schedules and in the golden run. |
| **C2** (MAJOR): the prospective dataset's provenance unpinned | (a) the dataset build (`build --out`) and a fresh 2026 store build were audited by listing every repository module each loaded: the dataset build loads `research/nfl_matchup_dataset.py` (the Sleeper parser of the box), now REGISTERED; the store build loads only registered files and the two neutral ones; Q9 says so. (b) The dataset build records every module's sha256 and the versions in its `meta.json`. (c) `prospective` refuses a dataset whose recorded code is not the registered bytes, whose `meta.json` is not the one the frame read, or which is not, by realpath, `research/data/nfl_rush_model/prospective_dataset` (the registered folder by a link or by path refuses). (d) The 2026 store and aux are built by the procedure itself, in the registered tree, from a fresh download into a scratch deep folder holding the registration's older stores (`build-stores`; it ran end to end in 9 s on today's feeds, 94 games); its record holds the pickles' sha256, the code that ran and the registration, the prospective frame records that record, and `prospective` refuses a 2026 store, aux or ids it does not vouch for, and any older store that is not the registered test frame's. (e) Q9 corrected. Guarded (`provenance_checks`, `build_stores_checks`, the golden run). |
| **C3**: a provisional `coefs.json` beside a lock still loaded | `record_chain` refuses a provisional stage with `prospective_lock.json` beside it; guarded. |
| **C4**: six survivors (N6, N7, N13, N14, N15, N18) | guards: a broken record chain refuses export (N6); `verify_stores` refuses a changed and an unrecorded store (N7); prospective refuses a `coefs.json` that is not this check2026's export (N13); capture-missing refuses a second record of a week (N14); a mixed file refuses with its own reason (N15); `in_registered_tree` refuses another registration's marker (N18). Section 8 has the re-run. |

### Rule round 4: the Sonnet, Opus and Fable final confirmations, answered (2026-10-28, 21:55-22:36 UTC)

Fable's two reviews (`research/reviews/nfl_rush_{rule,data}_fable.md`) and the two final confirmations
(`nfl_rush_rule_sonnet.md` from "FINAL CONFIRMATION", `nfl_rush_rule_opus.md` from its "FINAL CONFIRMATION").
Every item below changed registered bytes or the block, so the files were re-frozen at 22:36 UTC and both frames
rebuilt on deep3 (section 4).

| item | what was done |
|---|---|
| **Fable B1 = data D1** (BLOCKER): the dataset builder stopped at 2026 week 7, so the window's rows could never be built | `research.nfl_rush_dataset build` takes `--all-weeks` (every week the store holds) or `--max-week N`; with neither, the registered cap (`TEST_MAX_WEEK`, week 7) stands, so the registered CSVs stay reproducible. Q1's command carries `--all-weeks`. `provenance_refusal` refuses a dataset whose `meta.json` 2026 `weeks` miss a window week; `write_frame` refuses no rows with a reason (the old `IndexError`). Guards: the CLI path on a fixture store of weeks 4-9 (default: 4-7; `--all-weeks`: 4-9; `--max-week 8`: 4-8), a dataset short of a window week refused; F09 is caught. |
| **Durability** (Opus F2 = Sonnet F1, MAJOR): 19 files in one scratch folder, unrebuildable | (a) `content_digest`: canonical sha256 of a pickle's payload without what no registered computation reads (the save clocks, the download bookkeeping, the aux build's join diagnostics); each part digested so a mismatch names it. `register` records every deep pickle's sha256, digest and parts in `prereg.json` `stores`. (b) `verify_stores` and `provenance_refusal` accept a pickle by sha256 or by content digest and otherwise refuse naming the differing parts; `build-stores --older S ...` rebuilds older seasons beside 2026 and checks each against the registration. Measured: 2023's store and aux rebuilt from a fresh download (2026-10-28) matched (the aux differed only in `joins.pfr_crosswalk`, players.csv having grown: why `joins` is left out). (c) Section 4: every frame column checked; one restricted-derived column, `y_played`. (d) `register` writes `prereg_manifest.json` / `.sha256`; the runbook after the block says what to archive and where. Guards: a re-saved identical store passes, a one-play change refuses naming `cols.yds`, the ids vouched for (below). |
| **Opus F1a**: build-stores vouched for whatever the folder held | It moves the season's store and aux already in the folder aside (`<key>.pkl.replaced`) and refuses unless its refresh's summaries show the store built (`n`), the aux built (`built: True`) and the ids rebuilt. A store or aux kept (skipped) or not fetched (error) refuses. The move aside is mine: in the rehearsal a re-run after a refusal past the refresh was refused forever (the refresh found the folder current). Guards: skipped and error, store and aux, and no ids, each refused. |
| **Opus F1b**: the dataset's stores unbound | The dataset's `meta.json` records the sha256 of every store, aux and ids pickle it loaded (`inputs`); `provenance_refusal` refuses one that does not record the 2026 three or read a pickle the frame did not. Guards: the producer (the CLI path's `inputs`) and the consumer (missing record, missing ids, another 2026 store, another ids). |
| **Fable m7 / Sonnet PV10**: the ids pickle | The registered commands' distribution parts read the positions and Sleeper ids the REGISTRATION recorded (`prereg.json` `ids_positions`), never the ids pickle in the folder (the prospective folder's is fresh), so a relisted player moves no kernel and refuses nothing. The fresh ids the frame reads are vouched for by build-stores' record (a provenance case per vouched pickle, PV10). |
| **Sonnet F2 = Opus F3** | Q1 names the records' folder by absolute path; `registered-tree` prints the commands with the paths filled in. |
| **Sonnet F3** | Q1's window: the first game after the registration through week 11 (56 games hashed before week 8's Thursday game, 42 after week 8), the floor and the extension; the stale week-7 bullets are gone. |
| **Fable m2** | Q6 states the KEEP's power (`keep_power`, pinned by a guard): 0.46 at the gate's edge, 0.64 at 1.5x, 0.79 at 2x on 56 games; 0.42 / 0.57 / 0.72 on 42. |
| **Fable m3** | Q2: `spd_qb_td_oe` is dead only through the pooled (2025-informed) k: a removal, never an addition. |
| **Fable m4** | Q2: the bands carry no dispersion factor (display only). A guard pins the covariance to the inverse penalised information (F02 caught). |
| **Fable m6** | Q1: the props log's 300 s lead is before ESPN's scheduled kickoff, and `kick_ts` (the pbp's start) came 15-218 s later on week 6 (re-derived here on 11 games). |
| **Opus F4** | Q1: a week-11 game postponed past week 12's first kickoff is in the window only if the store holds it when the frame is built. |
| **Fable m1** | `wrap_flag`: the mean test or more than 1e-5 in the grid's top 32 values. The priced error is about the mass past the top, 1-2x the top 32's (measured against an 8,192-value grid: Fable's case 1.9e-3 / 3.9e-3), so 1e-5 bounds it near 2e-5; lines of 5-30 carries at 2.5-5.5 yards and a factor up to 1.25 hold at most 1.6e-8 there. Guards: the threshold's edges (F01 ported and caught) and a heavy line the mean test passes. |
| **Fable m5** | `coverage_refusal` refuses a window week whose props log is an input but which holds no frozen universe row; a week recorded "capture missing" may hold none (refusing it would block the test for good). |
| **Opus F5 (16 gaps), Sonnet F4 (PV10, FM1-3, DS2, WW1, CP2), Fable F01 / F02 / F07 / F09** | A guard each (section 8); the frame's provenance fields now come from one function (`provenance_fields`) so the producer is pinned; the mutants were ported to the new code where it moved. |
| **Sonnet F5e** (beyond the list) | The registered tree's report carries the block stored at registration; an edit inside the live block after the hash cannot stop the run. Guarded, with a stored block that no longer hashes refused. |
| **NITs** | Section 8's stale MX sentence fixed (both rush guard files run in the suite); section 4 says the registered dataset's `meta.json` predates the code record; `load_sleeper`'s "fetched 2026-10-23" never reaches a rushing-dataset column (no column reads `source`), so it stays the first model's; Q1 notes the longest rush leaves aborted snaps out. |

**The real-data rehearsal** (`<scratch>/nfl_rush_model/r4/reh/`, never the repository): a scratch registration
record (the 17 pickles' digests from deep3); `build_stores(2026, older=[2023])` for real into a scratch folder
holding deep3's 2018-2025 copies (fresh downloads; 2023 matched by content); the dataset `build --all-weeks
--out <absolute>` (2026 weeks 1-7); the prospective frame for weeks 1-7 (572 frozen rows, 206 s). On those real
metas `provenance_refusal` accepts weeks 6-7, the rebuilt 2023 pair by content, and refuses week 8 (not in the
dataset), the dataset capped at week 5, and a dataset on another 2026 store; the frozen weeks are 1-3 and 6-7
(14, 15, 16, 12 and 14 games); a registration before week 6 reads "week 7 is not closed" (no week-8 game yet).

## 8. Guards and the mutation test

`tests/guards_nfl_rush_model.py`: 232 checks, about 35 s, hermetic (synthetic kernels, coefficients,
frames, stores and boards; no network, no deep store). Each section runs on its own (`_section`): a
section that raises is a named failure and the others still run. The suite's MX section runs it and
`tests/guards_nfl_rush_data.py` (198 checks); the full suite on the round-4 freeze: 3,523 passed, 0
failed, exit 0 (2026-10-29 01:35 UTC, the model guards then at 224 checks), and with the final guard file
3,531 passed, 0 failed, exit 0 (02:47 UTC; the eight checks and three provenance cases added since are
listed below). The guard file is not registered, so it may grow after the hash; no registered byte moved.
- **A checkout without the two dataset CSVs** (CI's: they are kept out of git) runs the same 232 checks.
  The checks that hash REG_DATA need every file to exist, so `run` writes a one-line stand-in for each
  one missing and removes it after the run; no check reads their rows (Sonnet's R4-F1). Verified on a
  scratch copy without them: 232 passed, 0 failed, and neither file was left behind.
- **With the real registration present**, the two checks that need an UNregistered records folder (the
  frame reader refusing 2025 outcomes before the hash; `register` refusing without the orchestrator's go
  and writing nothing) run against a temporary one: they had read the repository's and went red once the
  real `prereg.json` existed.

**Round 4's sections** (the final confirmations; each would have caught the gap it names):
- `digest_checks`: the content digest canonical (dict and set order, the save clock, the join
  diagnostics move nothing; a float's last bit, an int read as a float, an array's type code move it; an
  unknown type refuses) and a mismatch named (`cols.yds`, `games`).
- `stores_checks` (rewritten on real pickles): the registered store and a RE-SAVED one with the same data
  pass; one changed play refuses naming `cols.yds`; an unrecorded season refuses.
- `build_stores_checks` (rewritten): outside the tree nothing downloads; the record holds exactly the 2026
  store, aux and ids; a skipped or failed store or aux, ids not rebuilt, an unregistered module and a
  missing pickle each refuse (F1a, BS3, BS4); `--older` passes a rebuilt season with the same data,
  refuses a one-play change and a season the frames never read.
- `provenance_checks` (rewritten, each case held to ITS refusal's words): the dataset's realpath, code,
  recorded stores (none, no ids, another 2026 store, another ids: F1b) and weeks (B1); the build record;
  the 2026 store, aux and ids each vouched for (PV6b / PV6c / PV10); an older aux and an older matchup store
  (PV7d), re-saved with the same content (passes) or different (refused with the part); the right folder
  through a link passes (PV2).
- `provenance_io_checks`: `provenance_fields` records the realpath through a link, the meta's sha256 and the
  stores record for a prospective frame only (FM1-3, both reviewers' numbering); `_provenance` reads the
  dataset meta and the stores record as they are NOW (PV9a / PV9b) and checks an older store by content.
- `round4_checks`: `window_weeks` gives a reason for a window past week lw (WW9) and leaves a game AT the
  registration second out (WW1); a non-empty tree destination refuses (RT2) and `procedure` prints absolute
  paths; a coefs.json shipping a SUBSET refuses (RC3); `repo_modules` covers non-research modules (RMOD);
  the wrap flag's two edges and a heavy line the mean test passes (m1, F01); the bands' covariance is the
  inverse penalised information (F02); the rescale floor (F07); Q6's power figures are `keep_power`'s.
- `dataset_cli_checks`: the build's CLI on a fixture store of weeks 4-9 -- default 4-7, `--all-weeks`
  4-9, `--max-week 8` 4-8 (B1, F09) -- its code record covering every repository module it ran (DS2,
  DC1, DC2) and its record of the pickles it read (F1b).
- `register_checks`: a real registration in a temporary folder records every deep pickle by sha256 and
  digest and the ids positions, and writes a manifest whose every line checks.
- In the golden run: prospective's wiring (window_weeks gets an EXTENDED window's last week, WW11;
  provenance the window's weeks; coverage this run's clock, CP2, and the frozen weeks), a props-log week
  with no frozen row refused (m5), and every command's parts read with the registration's record (m7).
  In `plumb_checks`: the registered positions map read back IS `positions()` on its ids.
  In `chain_checks`: the tree's report carries the stored block; a stored block that no longer hashes
  refuses (F5e).
- **Added after the registration** (the guard file only; each pins a survivor of the round-4 mutation
  runs, below): `write_frame` refuses no rows (Sonnet FR1 = Opus FR1 = m144); `fit_distributions` reads
  the positions it is handed (Sonnet FT2 = Opus PM3 = m164); `build_stores` moves the folder's store
  and aux aside before its refresh (Sonnet FT13 = Opus FB1 = m161); the build-stores CLI hands `--older`
  on, and nothing without it (FT27); the frame build hands `provenance_fields` its own `frozen_only`
  (FR5); `in_registered_tree` refuses another records folder holding the same registration (Sonnet
  IT2); a decision record that hashes to the coefs' records but is not JSON refuses (Opus RC6); a window
  week whose every game kicked off before the registration is not played (Sonnet WW12). Three provenance
  cases: a dataset built on another OLDER store than the frame's (Opus PR2), a frame that records no ids
  (Opus PV6d), an older store the registered frames never read (Opus PV7e).

**What the sections pin** (beyond the distribution engine's original checks):
- `golden_checks`: the four commands end to end on a synthetic frame pair (section 7, R1): which
  frame, seasons, baseline and played rows each reads; the stores re-verified per step; the
  accepted, provisional and kept sets and the pinned 2025 figures; the 2026 check's scored rows;
  the window's games and counts; export = check2026's set; the final `coefs.json` = the kept
  groups; both record chains; and fifteen refusals (out of order, an export twice, an export after
  the lock, a second prospective, a re-lock with the export's `coefs.json` back, the live tree, a
  record not written under this registration, a test frame and a validation frame that are not the
  registered ones, a prospective frame built by other code, provenance not vouched by this
  registration, a `coefs.json` that is not the export, an early window, a week not closed, a broken
  record chain).
- `core_checks`: `row_ll`, the clip, the clamp, the ridge fit and its lambda CV on a fixture, the
  floor's threshold count, the window at exactly 40 and at the registration instant, the
  dead-feature rule on a flip fixture, DEC without SHAPE, `prereg_status` on a changed data file,
  the plumbing donors.
- `chain_checks`: `record_chain` and `load_coefs` (a mix, a missing record, a changed file); the
  registered tree holding the stored copies; the reproducible gzip; the coverage refusals case by
  case; the dataset builder's `--out`.
- `rule_checks`: the clustered SE exactly (G/(G-1), centred sums) and the LARGER of game- and
  player-clustered, either way round; the gates at their edges (strict, NaN rejects); the walk
  sequential and never retrying; the ladder order; the window; groups and terms; played;
  write-once and `--force`; the refusal without a registration; the export's status refusal; the
  fit (no intercept, centring, recovery of a generating effect); the yards GLM's offset row by
  row; the REPLAY (the walk's scored distribution equals the board's price at every threshold);
  the export's groups, statuses and records; a synthetic walk that keeps the generating term.
- `leak_checks`: the k tables and `NFLM_K`; comparables strictly earlier (distinct vectors); the
  kickoff clock; the margin's sign; the Q1 universes; the Q4 table; the outcome TDs; the pending
  block; grid wrap; the week-agnostic frozen reader; dec_trend's tags; a special-teams-only PFR
  line moving no fitted input.
- `reg_checks`, `prosp_checks`, `qbfit_checks`, `plumb_checks`, `selection_checks`: the block's
  markers and file list, the computed error rates, the outcome-free read, the MEAN_FILL fill,
  the capture clock; the prospective frame's refusals (the in-place dataset included); a
  rushless QB-game kept; the plumbing parts on open seasons; the row selection on unrestricted
  columns only.
- `tree_checks` (the confirmations' C-M1 / C-M2 / C-m2): the window's weeks on mocked schedules
  (stopping at week 9, empty, week 11's first game only, closed); the records' folder from the
  tree's marker (no variable, an agreeing and a disagreeing one, a folder inside the tree, two
  trees sharing one lock); capture-missing bound to the registration, in the tree, once a week;
  the index's and the extra series' stats disjoint.
- `provenance_checks`, `stores_checks`, `build_stores_checks` (C2, C4's N7): the prospective
  dataset by realpath, its `meta.json` the frame's, its recorded code registered or neutral; the
  2026 stores' build record (this registration, the registered code) vouching for the 2026 store,
  aux and ids, the older stores the registered frame's; `verify_stores` refusing a changed and an
  unrecorded store; `build-stores` only in the tree, refusing unregistered code; the dataset
  build's code record. `reg_checks` also pins Q9's registered list to `REG_CODE` both ways and its
  neutral pair to `NEUTRAL`.
- `hook_checks`, `hook_more_checks`, `hook_gate_checks`: byte identity without coefs; shipped
  markets and statuses; rung-1 blocks byte for byte; derived markets only through shipped
  components; bands; the breakdown and facts with their credits; first-model facts never
  overwritten; the snapshot on the availability view; `rush_coefs=False` never reading the file;
  RBs and QBs only; the universes; the pre-game margin reaching the kneels; an unmapped player; a
  failing snapshot keeping the whole board.

**The mutation test, round 4** (`<scratchpad>/nfl_rush_model/mut4/run.py`: four worker copies of the
round-4 frozen tree, one mutant at a time, the model guard file on each, and Fable's data-layer mutants
against `tests/guards_nfl_rush_data.py` as well). It ran every list a reviewer has run, and mine:
- mine: `mut2/mutate.py` (m000-m134), `mut3/extra.py` (m135-m137) and 44 for this round's fixes (m138-m181);
- Sonnet's re-check (`review_rush_rule_sonnet/mutate2.py`, 110) and its final confirmation (`final/mutate4.py`, 59);
- Opus's re-check (`review_rush_rule_opus/re/mutate_re.py`, 40, and `mutate_re2.py`, 8) and its final
  confirmation (`final/mut/mutate_final.py`, 131);
- Fable's 16.

A replacement two lists share ran once: 546 listed, 449 distinct, none failing to apply. A mutant whose
target round 4 rewrote was PORTED to the same intent on the new code (26, `run.py`'s `PORTS`). The run
started on the freeze before the registration, was cut by the usage limit after 68 verdicts, resumed at
01:27 UTC with those verdicts standing, and ended at 02:39 UTC. The first pass ran against the guard file
as frozen (224 checks). Its survivors were re-run as the guard file grew to its final 232 checks (230 for
the first batch; adding a check cannot un-catch a mutant): `mut4/rerun.py` and `rerun_r4.py`, the second
with the reviewers' own round-4 survivors that the orchestrator named.

| list | mutants | caught, first pass (by a section that raised) | survivors, first pass | survivors, final guards |
|---|---|---|---|---|
| mine | 182 | 179 (16) | 3: m144, m161, m164 | 0 |
| Sonnet re-check | 110 | 107 (18) | 3: G09, F06, H05 | 3: G09, F06, H05 |
| Sonnet final | 59 | 55 (10) | 4: WW7, WW12, RM2, IT2 | 2: WW7, RM2 |
| Opus re-check | 40 | 38 (8) | 2: B3 (ported), PL4 | 2: B3, PL4 |
| Opus round-1 re-runs | 8 | 8 | 0 | 0 |
| Opus final | 131 | 121 (18) | 10: PV1b, PV6d, PV7e, BS5, RC6, DC2, B3, and three shared (RM2, IT1 = IT2, PL4) | 6: PV1b, BS5, DC2, B3, RM2, PL4 |
| Fable | 16 | 14 | 2: F12, F13 (both CAUGHT by the data guards) | 0 by the file that owns them |
| **distinct** | **449** | **428 (54)** | **21** | **13; 11 past both guard files** |

A row counts a replacement it shares with another list (the "distinct" row counts it once). Fable's F09
(the dataset's week cap) is caught by the model guards (`dataset_cli_checks`) and not by the data guards.

**The reviewers' own round-4 runs** (Opus's `r4/mut/mut_r4.py`, 56 on the round-4 hunks, and
`mut_r4_f5.py`, 13; Sonnet's `final/mutate5.py`) left survivors the orchestrator named for a pin: Opus's
PR2, PM3, FR1, FB1 and Sonnet's FR1, FR5, FT2, FT13, FT27. With my own m144, m161, m164 and Sonnet's IT2
(13 names, nine distinct replacements: PM3 = FT2 = m164, FB1 = FT13 = m161, both FR1s = m144), every one
is CAUGHT by the final guards (`rerun_r4.py`), as are WW12, PV6d, PV7e and RC6. Their other survivors the
reviewers argued, and I agree:
- **WR2 = NR6** (the wrap read at the grid's bottom 32 values): near-equivalent. With `GRID_LO` -128 the
  bottom is where a wrap folds the overflow, so it reads the folded mass itself; it still flags the
  builder's own line (1.28e-3 against 1.87e-3, Sonnet's run).
- **RG2 = FT21** (`register`'s missing-pickle refusal dropped): `stale_frames` refuses a missing input
  first, and the registration is made.
- **FT9** (`store_refusal` without the equal-sha256 short cut): an equal sha256 is an equal digest.
- **FT12** (a missing pickle passes `store_refusal`): the run stops in `season_plays` ("no compact
  store") instead: the same stop, another message.

**The eleven left past both guard files**, each argued equivalent (or a harness artifact):
- **G09** (Sonnet's own control): `keep_2026` without `isfinite`. NaN <= 0 is False as before, and -inf
  cannot arise: every row's loss is clipped (`CLIP` 1e-6), so d is finite or NaN.
- **F06**: removing the GLM's clip at 0 leaves `fit_glm` and `deviance` (research/nfl_matchup_fit.py),
  which clip again.
- **H05**: the merge order of the index and the extra series, whose stats a guard pins disjoint.
- **PL4** (Opus's own control, in both its lists): the plumbing branch reordered.
- **B3** (Opus's re-check, ported): the rushing env built when an env is injected without coefs. Only the
  guards inject one; no production caller passes `rush_env`.
- **B3** (Opus's final): `_rush_env(rc or {}, ...)` inside `if rc:`, where `rc or {}` IS `rc`.
- **WW7** (Sonnet's "equivalent on a real schedule?"): week lw closed by any LATER week's game, not week
  lw + 1's. They differ only on a store holding a week lw + 2 game and none of week lw + 1. NFL weeks kick
  off in order, every regular-season week has games, and the store holds played games only.
- **RM2** (both lists): inside the tree an `NFLR_MODEL_OUT` naming another folder is refused just above,
  so `env` is unset or `abspath(env)` IS `mo`. Identical.
- **PV1b**: `want_ds` is itself a realpath. When the recorded path equals it, so does its realpath
  (realpath is idempotent at one instant), so the short cut never changes the answer.
- **BS5** (ported): the stores record's `prereg_sha256` from the tree's marker instead of `prereg.json`.
  `build_stores` refuses outside the tree first, and `in_registered_tree` returns the marker only when its
  `prereg_sha256` equals that very file's: the same value.
- **DC2**: the dataset's code record filtered to paths containing "nfl". The harness's copies live under
  `.../nfl_rush_model/mut4/`, so every path passes and the mutant does nothing there. Opus's own run, on
  a path without "nfl", CAUGHT it (`review_rush_rule_opus/r4/mut/mut_r4_f5.log`), and CI's checkout path
  has none either.

Round 3's run (`mut3/run.py`, the 10-24 freeze: 316 mutants against 198-199 checks, 311 caught, five
argued) is superseded: every list it ran re-ran here, and its five (G09, F06, H05, PL4, B3) are above.

A mutant counted "by a section that raised" fails a named `... ran to the end` check; the other
sections still run.
