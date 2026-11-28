# Vigil — working agreement

Flask sports-betting / Kalshi analysis app, live at https://vigil-vdoh.onrender.com.
Single-user (the repo owner). Read this before touching anything.

## Non-negotiables

- **Branch**: all work goes on `claude/kalshi-crypto-predictor-ckutwm`. Never
  push anywhere else without being asked.
- **No pull requests** unless explicitly requested. Pushing to that branch
  auto-deploys.
- **Commit trailers**: match the trailer block on recent commits exactly —
  `git log -1 --format=%B` and copy the footer format.
- **Secret scan before every commit**: run `sh scripts/secret-scan.sh` and get
  a clean result. It greps the staged diff's added lines for two
  historically-leaked key prefixes and for credential-shaped assignments. The
  patterns live in that script (which excludes itself from the search) rather
  than in prose here, so documenting the check can never trip it.
  `vigil-pc.cfg` (holds SIM_TOKEN) is gitignored and must stay that way, along
  with `pc-simcache/` and `pc-deepcache/`.
- **Guard suite gates every push**: `python3 tests/combo_audit_guards.py`
  (~1,450 checks, 3-8 min). Check the **exit code explicitly** —
  `python3 tests/... ; SUITE=$?; [ $SUITE -eq 0 ] && git commit ...`. Piping to
  `tail` once masked a red suite and shipped a broken commit. The suite also
  runs in CI on every push (`.github/workflows/guards.yml`) — a red X on the
  commit is the backstop, but never push red on purpose; Render deploys
  regardless. The suite is hermetic via `VIGIL_NO_BG` (importing app.py must
  never start the production loops in a test process — it once did, and the
  accumulated live predictions flipped a guard).
- **Batch pushes.** Every push triggers a Docker build on Render and a swap
  that cold-starts the app and emails a health-check alert; measured
  2026-09-05, the swap landed 1, 3 and 9 minutes after the push, so a push
  is a restart the owner sees almost immediately. Group related
  work into one commit where you reasonably can. Measured cost of ignoring
  this: seven deploys in one day read, from the owner's seat, as "the server
  keeps crashing" — the ledger was clean and every "crash" was a swap.

## Conventions

- **No silent `except Exception: pass`** in service modules — the guard suite
  fails the build on it. Use `errlog.note("<STABLE-ID>", e)` so the failure is
  visible in the ledger. IDs are grep-able and stable (e.g. `SLATE-child`,
  `WARM-game-sim`, `PCUP-art-write`, `MEM-high`).
- **pyflakes is part of the guard suite** — unused imports/vars fail it.
- **Times for the owner are Mountain time** (MDT, six hours behind UTC in
  season). Logs, workflows and commit messages stay UTC.
- Comments explain *why*, especially for anything counter-intuitive or
  measured. Most constants in this codebase were fitted against real data; say
  what was measured and what it cost. Match that density.
- New behaviour ships with a guard in `tests/combo_audit_guards.py` that would
  have caught the bug it fixes.
- **Treat a dramatic small-case result as a hypothesis** until it survives the
  actual contest geometry, held-out worlds and the real production path. Every
  severity figure in the showdown pass shrank on contact with a real board
  ("probably halves ties" -> 6.14x -> 2.36x -> x1.07/x0.94; a 150x tie-convention
  swing -> 1.1%). The mechanism was geometry, not noise, each time.
- **A guard pins a number; only a reader pins its meaning.** Before quoting a
  research figure, open the function that produced it and read the definition
  beside the sentence. x1.07, x0.94, 84.156 and 96.758 were all exactly what the
  code computed for two full commits while the report attached the wrong event to
  two of them, and every guard stayed green because each asked "is this number
  still present?" Where a guard can pin the definition as well as the claim, it
  should.

## Reading the running app without touching it

The app holds no GitHub credentials. Workflows PULL from it and commit to the
data-only `sim-history` branch (which never deploys):

- `.github/workflows/error-log.yml` — the error ledger + an instance memory
  snapshot. Fire it (`actions_run_trigger`, ref = the work branch), then
  `git fetch origin sim-history` and read `errors/latest.json` and
  `errors/mem-latest.json`. Since 2026-10-29 it also pulls the records:
  `errors/sharp-latest.json` (every predlog bucket against the Kalshi price it
  was logged beside), `nfl-record-latest.json`, `cfb-record-latest.json`,
  `mx-record-{nba,nfl}-latest.json` (the Matchups record) and
  `calib-params-latest.json` (the shared calibration file) -- read them before
  judging any model change against the live record.
- `.github/workflows/nightly-history.yml` — nightly deep-sim history.
- `/healthz` and `/robots.txt` are the only anonymous routes; everything else is
  behind HTTP Basic auth. `X-Sim-Token` (SIM_TOKEN) is the automation door.

## Architecture, briefly

- `app.py` — every route; background jobs are claimed by ONE worker via a
  file lock (`_own_background_jobs`).
- `baseball.py` — MLB slate, game sims, the shared TTL cache, disk-shared
  boards/sims. **Since 2026-10-16 every MLB simulation path checks
  `baseball.MLB_SIMS_PAUSED`** (paused by default in code so the PC worker
  inherits it from git; `VIGIL_MLB_SIMS=on` resumes): pages and last results
  stay, nothing new is simulated. `mlb_sim.py` is the 4,000-run engine; `deep_season.py` /
  `deep_data.py` / `deep_sim.py` are the pitch-by-pitch season engine.
- `combo_engine.py` — the parlay frontier DP + objective chooser. Leg-count
  depth is demand-driven (`dp_legs`).
- `store.py` — SQLite ledgers (picks, props) and the shared scoreboard math
  (`_pick_stats`). `predlog.py` grades market predictions; `calibrate.py` fits
  the CV-gated calibration.
- `nfl_*.py`, `racing*.py`, `basket.py`, `hockey.py`, `golf.py`, `tennis_*.py`,
  `ufc_sim.py`, `lol.py` — the other sports. `boardshare.py` publishes any
  board so all workers serve one build.
- **The NFL deep stack** (2026-10-16): `nfl_pbp.py` turns ESPN game summaries
  into compact play-by-play records (fourteen ints a play) cached per season
  in the deep store; `research/nfl_deep_fit.py` builds every table the engine
  samples from 2023-2026 (`research/data/nfl_deep_tables.json`, committed) and
  measures the matchup exponents, the shrinkage K per stat and the
  year-over-year persistence; `nfl_deep_sim.py` plays a game snap by snap
  (both units against each other, clock, fourth downs, kicks, OT; two fitted
  parameters, `_FORM_SD` and `_HFA_YDS`; team multipliers applied with
  UNBIASED random rounding -- `int(round())` erased them); `nfl_deep_data.py`
  builds the team unit profiles (shrunk toward the measured prior by the
  pooled full-season K; the level anchored at full weight to Kalshi's win
  totals converted to REST-OF-SEASON pace, `nfl_season.market_pace` -- the
  ladder includes banked wins); `nfl_deep_season.py` plays the rest of the
  season and the bracket with the NFL tiebreakers (deep job `nfl_deep`,
  PC-first, HEAVY_BUILD, quality gate incl. a full 17-game schedule). The weekly
  slate blends the deep engine into the headline at `nfl_game_sim._DEEP_WEIGHT`
  (0.30) and logs its raw probability with `predlog.log_shadow` to bucket
  `nfl_deep` -- `predictions` is keyed on the ticker alone, so a second model
  can only be graded through the shadow table. Validation:
  `research/reports/nfl_deep_validation.md` (OUT-OF-SAMPLE walk-forward,
  2024-25, tables refit on past seasons only) — read before quoting any skill
  figure; the review that found and fixed the first version's bugs is in
  `research/reports/nfl_deep_plan.md` §9.
- **The DFS boards' worlds** (2026-10-16): `nfl_dfs_engine.py` plays every
  tournament world as one game in the deep engine, its scoring level the
  drive model's and the deep engine's blended at `_DEEP_WEIGHT`, the box
  dealt to the players by Sleeper usage with the constrained model's fitted
  allocation noise, defenses and kickers scored off the same game. One added
  engine term, the shared passing environment (`ENV_SD` 0.14, chosen on 2024
  by a rule written first; off for every other caller of `play_game`). Each
  team's pass attempts are anchored to Sleeper's projected attempts
  (`ATT_WEIGHT` 1, a pass-rate `call_shift` iterated with the points anchor;
  chosen on 2024, shipped on a pre-registered 2026 weeks 2-5 test whose gain
  sits inside the noise). 2025 is SEEN for anything the engine does now; new
  engine changes are judged on 2026 (`player_weeks_2026`). A replay's scored
  worlds must come from the tables its anchor measured on
  (`play_worlds_parallel(..., T=)`): the first runs drew them from the shipped
  tables, which contain the season scored (`research/data/superseded/`).
  `dfs_tourney.BOARD_MODEL = "engine"` because the pre-registered 2025 test
  passed (`research/reports/nfl_dfs_engine.md`; the harness
  `research/nfl_dfs_engine_eval.py` refuses a dirty tree for 2025+). PC only
  (numpy, `workers` processes); the app's lineup sheet stays legacy-discrete.
  DraftKings writes TWO clocks: the lobby's `StartDateEst` is Eastern with no
  zone, contest and draftables strings are UTC with a Z. Parse both through
  `nfl_dfs.iso_ts` (reading the lobby as UTC ran every pre-lock refresh four
  hours early).
- **The DFS builder sheet** (`nfl_dfs.build`, the tab's Build; checked end to end
  on the week-6 slates, 2026-10-16). The tab pastes DraftKings' own pool back
  into the builder, so that pool is the contract: `dk.salaries_csv` adds a
  Status column (healthy = ACTIVE, so the sheet KNOWS the pool was checked) and,
  for NFL, Game Info in DK's dated export shape ("CHI@ATL 10/18/2026 01:00PM
  ET"), so the sheet dates its own week. DK's football out-statuses (IR, OUT,
  PUP, ...) are unavailable -- `dk._UNAVAILABLE` held only baseball's IL until
  then, and 65 IR players were "available" on the main slate. The NFL slate list
  is an ALLOWLIST (`dk._LINEUP_TYPES`: Classic 21, Showdown 96); Snake came
  UNTAGGED and listed exactly like the 10-game main slate. The contest sim
  prices the picked contest off DK's own payout table and says which figures
  its sampled field can READ (`nfl_dfs.resolution`: a line needs ten sampled
  lineups above it; ROI needs <=10% of the prize money above the deepest line
  read) -- a Millionaire's first place and ROI never can, and the card prints
  "not readable", never a five-figure ROI. The classic optimizer is a seeded
  local search (fills, every stack team polished by 1-2 swaps, kicks, a defense
  switch) measured exact against an integer program on four week-6 slates and
  against brute force on 180 two-game pools; the old fills ran 3-6% short.
- **The classic board on every core** (2026-10-18, `dfs_tourney`). The
  week-6 build took 45 minutes on the PC, ~40 of them on one core (best
  lineups 1,268 s, scoring 774, field 262, portfolio 158). The best lineup of
  each world is now solved alone over the players who can be in it
  (`_survivors`: a player goes when as many of his position as a lineup holds
  outscore him STRICTLY for no more salary -- exact, 9x on one core), in
  blocks over processes (`pool_map`); the scoring and portfolio passes split
  their worlds over processes that map the lineup matrices from shared memory
  (`_par_map`, one BLAS thread each, the chunk memory divided by the process
  count). Those three give the old answers, held to the old paths by guards on
  DraftKings' 0.02-point lattice (continuous test data flips a payout bucket on
  summation order). The field and candidate draws run `classic_sample` UNTOUCHED
  (research/cl_slot.py and cl_week2.py hash its source) in seeded 1,000-row
  blocks over one pool for the whole stage (`FieldSampler`): the same sample on
  any process count, but a different sample than the single call drew -- the
  one way a seeded board differs from the old code's. Any pool that will not
  start reruns serially, ledgered `TOURN-pool`. The PC's first such build: 778 s
  (week 6, two contests; the old code 3,409 s). **Worlds**
  (`research/cl_worlds.py`, rule committed first; `research/reports/cl_worlds.md`):
  the PC builds at 180,000 (160,000 score), or the old 60,000 when kickoff is
  nearer than a full build plus ten minutes (`pc_worker._cl_sims`). By the rule's
  letter 160k won on the money list by a hundredth of a point, on replicates that
  share their worlds; with production's spread it is 1.47 SE and nothing is
  proven. First place and EV picked ~11-12% better on fresh worlds (z 1.5, 1.95),
  and a 1.2M field bought nothing. The board's own top first-place and EV figures
  read 3-4x high in-sample (40k), 2-3x (160k); `money_gate` says so.
  **Showdown** (`research/sd_worlds.py`, DAL @ GB 2026-10-18, descriptive): at
  60,000 games the board's top-20 figures HELD on fresh games (shown/fresh
  0.92-1.00), two independent draws shared 14-19 of 20 and 120,000 bought
  nothing visible -- its field is the whole weighted universe, so the classic
  tail inflation does not arise. **Since 2026-10-22 (SD_ENGINE 7) the scoring
  pass scores only the rows the board reads** -- every lineup we may enter
  and the chalk -- against the whole field (`run_subset`): their buckets are
  read out of the field's own score matrix, so the figures are run()'s bit
  for bit, and `opt` still counts the best of ALL lineups. **Its field is flatter
  than the real one** (`research/sd_dup.py`, `research/reports/sd_dup.md`,
  2026-10-19): Sunday night's served field, rebuilt exactly, had a pair
  collision of 2.07e-4 against the real 5.05e-4 (S7 found the same on three
  older games); at the knob that matches it (`FIELD_TOP_SHARE` 0.468%)
  Monday's lists kept 16-18 of every top 20 and the money list's cash rate read
  3.6 points lower. Descriptive; the S7 prospective test
  (`research/s7_prospective.py`) is what judges fields. **Monday after the
  game** (`research/sd_postgame.py`, `research/reports/sd_mnf.md`): S7's first
  prospective contest -- the frozen projection+salary field beat
  projection-only by 0.459 nats an entry (95% 0.436-0.483); the served
  universe held only 56.9% of the real field (Juszczyk, Burks, McNichols,
  Watkins: our depth and roster gates, inherited by the FIELD) where every DK
  player Sleeper projects >= 1.0 would hold 96.0% at 1.34M lineups. **Shipped
  2026-10-22** (`_field_extras`): the field takes the players our depth and
  roster gates keep out (never an injury listing) projected 1.0+, the best
  eight, the universe held to 900,000 lineups (`_FIELD_MAX_LINEUPS`, the
  least-projected extra dropped first); Sunday's knob (0.468%) read
  Monday's real collision within 11%; the best-lineup share beat the field's
  ownership on both nights. **A showdown export lists every player twice**
  (FLEX, and CPT at 1.5x): `dk_standings.parse` takes the FLEX figure (the
  CPT one / 1.5 when there is none) -- keyed on the name alone it read 1.5x.
  **Ownership** (`research/cl_own.py`, 2026-10-18, rule hashed first): a
  per-position pull on projection and value (TE allowed at FLEX), fit on
  week 1, FAILED on week 6 (top-40 MAE 8.95 vs the served field's 8.29) -- the
  public paid up at RB in week 1 and chased value in week 6, and no pull
  fixes the real field's salary-to-cap (50% at $50,000 both weeks). The lead:
  each player's share of the simulated Sundays' best lineups (`opt_pct`, on
  every board) beat `field_pct` on BOTH weeks read (top 40: 5.02 vs 5.89,
  6.27 vs 8.29) -- the public drafts like an optimizer. **Week 2** (the
  owner's export, the one week NOT read to find it, `research/cl_own_w2.py`
  hashed first) confirmed it: 5.57 vs 6.62. So the board's field is now the
  **optimizer field** (`FIELD_MODEL`, ENGINE 10): the same sampler, its pulls
  steered (`steer_field`, ten passes of 40,000) until each player's ownership
  sits at 0.9 x his best-lineup share + 0.1 x a fresh draw of the calibrated
  field (`OPT_FIELD_ALPHA`, a sweep over weeks 1, 2, 6); field AND candidates
  drawn with the pulls (`FieldSampler(..., pulls=)`, blocks unchanged). On the
  three served boards the steered field drew 1.86/5.14, 2.09/5.64, 2.66/6.27
  (all / top-40 MAE) against 2.16/5.89, 2.38/6.62, 3.42/8.29, with 43-48% of
  lineups at the cap; duplicates are still ~100x too rare. Every board stamps
  `casual_pct` (the calibrated field it replaced) beside `field_pct`;
  `research/cl_own_w7b.py` (written before week 7) KEEPS the field only if it
  beats `casual_pct` on week 7's top 40, else `FIELD_MODEL = "softmax"`.
  **Slot-allocation v1** (`research/cl_slot.py`, frozen 2026-09-20) PASSED its
  held-out week 2 (`research/cl_week2.py postgame`, clean run at ac09ae4,
  `research/reports/cl_week2.md`): slot cross-entropy 23.24 -> 22.04, salary
  Wasserstein-1 $920 -> $36 (mean $49,839 vs real $49,871), all four
  secondary seeds the same way. Its FLEX allocation and spend mechanism are not
  in production; joining them to the optimizer field is a change to test
  first. NEVER edit dfs_tourney.py while a frozen harness runs: they rebuild
  variants from the source on disk (the first week-2 run refused mid-way).
- **The NHL deep stack** (2026-10-16): `nhl_pbp.py` turns the league's own
  feed (api-web.nhle.com -- strength states, shooter and goalie, shot type and
  location; ESPN has no strength states) into fourteen-int event records and
  holds the ONE game `walk` the fit and the team units both cut games with
  (delayed penalties as their own phase, same-second calls netted into
  man-advantage incidents, the clock split half-and-half across a state
  change); `research/nhl_deep_fit.py` -> `research/data/nhl_deep_tables.json`
  (2022-23 to 2025-26); `nhl_deep_sim.py` plays a game as exponential clocks
  per strength state, score and the last five minutes (the box, the delayed
  call, the goalie pull's measured intention and conditioned timing, 3-on-3,
  the shootout); `nhl_deep_data.py` ten team units shrunk by the K the
  validation RAN WITH (the measured K are tabled beside them -- swapping is a
  change for 2026-27 games to judge), last season's profiles committed as the
  prior (`nhl_deep_priors.json`), the nightly `nhl_deep` job (server and PC)
  keeping counts, never play-by-play. The board (`hockey.py`) shows both
  models; the headline blends the deep engine at `_DEEP_WEIGHT` 0.5, chosen on
  2024-25 and confirmed on 2025-26 out of sample by a rule written first
  (`research/reports/nhl_deep_validation.md`); deep figures are computed once a
  date in a niced child and never for a date already played; shadow bucket
  `nhl_deep`. The league's abbreviations are not ESPN's (five differ,
  `nhl_pbp.to_espn`).
- **The NBA deep stack** (2026-10-16): `nba_pbp.py` turns ESPN game summaries
  into thirteen-int play records (2022-23 to 2025-26 held, every stored game
  reproducing its final score). **ESPN files the All-Star games as
  regular-season games**: everything measures `nba_pbp.league_game` (both sides
  in `FRANCHISES`) -- the first elasticity measurement let two one-game "teams"
  in and read 1.5 instead of 1.15. `nba_poss.py` cuts possessions;
  `research/nba_deep_fit.py` -> `research/data/nba_deep_tables.json`;
  `nba_deep_sim.py` plays possessions on the real clock (how the ball came
  decides the shot, the clutch and garbage time, the period's last
  possession, the pull back toward an even game); `nba_deep_data.py` eighteen
  units, round K, `LEVER_EXP` 1.15 (measured on 2023-24), priors committed,
  nightly `nba_deep` job (server and PC). **The pre-registered test FAILED**:
  0.3 chosen on 2024-25, the 2025-26 blend +0.00065 above production, so
  `basket._DEEP_WEIGHT` is 0 -- the card shows the deep figure, shadow bucket
  `nba_deep` grades it, the headline is production's
  (`research/reports/nba_deep_validation.md`: both models under-confident
  ~1.5x in logit, the deep one more compressed, the two correlating 0.9).
  Any change to K or the exponent is judged on 2026-27 games.
- **Soccer** (2026-10-17, `soccer.py`): every Kalshi series tagged Soccer
  ending in GAME (discovered from `/series`, ~80 leagues, ~900 open three-way
  events in October) priced by a Dixon-Coles goals model fitted in pure
  Python on two years of ESPN results (one call per league per calendar
  year, `scoreboard?dates=YYYY&limit=1000`), every rating shrunk toward its
  league's mean, the leagues tied together by their cross-league matches.
  The owner's bet is NO on the underdog (pays on the favourite AND the tie),
  so the card leads with that leg; "heavy underdogs" are the matches whose
  underdog YES ask sits `HEAVY_GAP` (5c) or more under the tie's, in Kalshi's
  own prices. **The EVENT ticker's two team codes are home then away** (638
  of 640 ESPN-matched events); Kalshi's market titles are in no fixed order
  and are never read for that. Home/away, kickoff and team ids come from
  ESPN's all-leagues scoreboard (`soccer.match_event`: the mapped league on
  the ticker's date +-1, token containment on the names, `_ALIAS` for
  Kalshi's spellings); leagues ESPN does not carry (`ESPN_LEAGUE` None) show
  the de-vigged price and say so. The fitter updates attack, defence and
  home advantage in TURN (Gauss-Seidel) and re-centres the attacks each
  sweep -- the simultaneous update oscillated (correlation 0.1 with the
  synthetic truth, a home advantage of 3.3; the guard replays that pool).
  Parameters chosen on 2024 and tested on 2025 by a rule written first
  (`research/soccer_validation.py`, `research/reports/soccer_validation.md`).
  Bucket `soccer` in predlog (all three outcomes vs Kalshi's de-vigged
  price), DraftKings' de-vigged line (ESPN carries it on upcoming matches
  only) in shadow bucket `soccer_dk` -- **pre-game matches only**
  (`row["pregame"]`: ESPN "pre" and kickoff ahead); an in-play match shows
  no edge. **Extra time**: a match that went to extra time or penalties
  enters at its 90-minute score rebuilt from ESPN's goal minutes
  (`soccer._ninety`; 197 of 1,264 were not level at 90 -- second legs), never
  dropped (the first version's cup "weakness" was that artifact). **The fit
  runs in a niced child** every six hours (`refit_main` under
  `deep_cache.HEAVY_BUILD`, ~120 MB, ~200 ESPN year files; past years kept in
  the deep store) and lands in the deep store as `soccer_model`; the board
  thread only loads it. **Recent form and head-to-head carry weight 0**:
  tested 2024/2025/2026 by a rule written first
  (`research/soccer_form_validation.py`, `research/reports/soccer_form.md`),
  form did not confirm and every head-to-head weight made predictions worse.
  The card shows both as information; a weight is a change to judge on new
  seasons, not to set by hand. **Both teams to score** (2026-10-17): the raw
  grid figure FAILED its held-out test (worse than the league's own BTTS rate,
  too extreme both ways); the shipped figure is recalibrated toward the
  league rate (`BTTS_RECAL`, fitted on 2024, beat the league rate on 2025 AND
  2026; `research/reports/soccer_btts.md`). Kalshi's KX<league>BTTS markets
  are fetched by name off the GAME tickers (100 a call -- one call a series
  read 429), edged only off two-sided books within 10c, pre-game, bucket
  `soccer_btts`. The figure is flatter than Kalshi's (SD 6.1 vs 9.1) and is
  untested against the market; the tab says so. **Accuracy pass**
  (2026-10-17, `research/reports/soccer_accuracy.md`): the fit reads
  pseudo-goals, a quarter weight on shots on target (`SHOTS_W` 0.25,
  `SHOTS_COEF` from 2023; beat goals alone on 2025 AND 2026 held out; ESPN's
  year files carry shots for ~75% of matches, kept beside the rows in the deep
  store, `artifacts.SCHEMA` 5). Against Pinnacle's CLOSING price
  (football-data.co.uk, 24,841 matches, 27 leagues) the model LOST in every
  league and added nothing at any blend weight, so the card's headline chance
  is the pool of model and Kalshi's de-vigged price at `MODEL_WEIGHT` 0 --
  Kalshi's own; the edge reads the headline, the model's figure sits beside
  it, the record (bucket `soccer`) and the quick picks' 15-point rule keep
  the raw model. A weight above 0 is a change for the live record to earn
  against Kalshi's pre-game price. **Data sources**: ESPN carries
  73% of Kalshi's matches; FotMob would carry ~96% but its terms forbid
  automated use and its robots.txt disallows /api/* -- not used.
- **Quick combo** (2026-10-17): one number, the best slip of that many legs
  for Kalshi's combo builder. Soccer: `soccer.quick_combo` off the cached
  board (`/api/soccer/combo`); college: `cfb_board.build_parlay(quick=True)`.
  Both take ONLY combo-eligible events (`kalshi.combo_events`; on 2026-10-17
  189 of 957 soccer GAME events, every college leg), one leg per match, the
  owner's today in **Mountain time** first (`clock.mt_date`; a 9 pm MDT
  kickoff is tomorrow in UTC), the next day only while today is short. The
  legs come by the owner's **tier rule** (`combo_engine.quick_tier`): locks
  95%+, then 90%+, then 80%+, nothing under, a leg's tier the LOWER of the
  model's number and Kalshi's de-vigged price, and any leg the two put 15+
  points apart disqualified (inclusive: the owner's example, 85 vs 70, is
  blocked) and listed on the slip. The model's side is its OWN number --
  college's is the raw ratings (`p_home_raw`), never the slip's leg figure,
  which is 75% Kalshi's line and would never disagree. College's is
  **moneylines only and has no leg cap** (the owner: "endless, not 12"): the
  route passes `types={"ML"}`, the owner tier skips the 30-leg cap, and
  `frontier(ceiling=)` lifts the DP ceiling for this one-leg-a-game pool
  only; the slip counts how many of its safest legs already pass
  `MAX_PAYOUT_X`. On 2026-10-17 every all-favourite moneyline slip was -EV
  by the model (4 legs -8%, 33 legs -55%). **`kalshi_nfl._fetch` never
  returns part of a series**: a page that fails after two retries fails the
  build and `index()` keeps the last good one (one 429 had rebuilt the
  college index from 400 of 552 moneyline markets, the weekend's 76 games
  cut).
- **The NFL combo maker** (audited 2026-10-19,
  `research/reports/nfl_combo_audit.md`). A same-game stack is costed at its
  asks TIMES its correlation (`combo_engine.stack_lift`, every sport's maker):
  the product of the asks paid a stack for correlation Kalshi's maker prices
  himself, and Monday night's default slip read +25.9% EV with every leg under
  its ask (-11.6% priced). NFL pools hold only legs Kalshi lists while its
  index answers, as baseball's, college's and UFC's do. The drive sim's trims
  (`_TD_CAL`, `_FG_CAL`, `_PROJ_BIAS`) are IN-SEASON constants now: 2025's
  Sleeper ran hot (TDs 0.957x), 2026's does not (1.056x), and the 2025 trims
  left the sim 2.2 points under the real totals and 2.55 under Kalshi's;
  refit by `research/nfl_trim.py` (rule first, report `nfl_trim.md`) -- re-run
  it every few weeks. The preseason path keeps the 2025 trims its own
  constants were measured through (`_TD_CAL_PRE`, `_FG_CAL_PRE`). **The
  accuracy batch** (2026-10-19 evening, `research/reports/nfl_accuracy_gaps.md`):
  a capped game pool keeps its most liquid legs spread over the band, never
  the biggest model-vs-price gap (`_cap_pool`); a player Sleeper still counts
  who carries an out-type tag, or Questionable after missing his last game,
  is in doubt (`availability`) -- his legs leave, and a quarterback's game
  (else his position group) takes Kalshi's number. A Monday tag is LAST
  week's status (ESPN dates 56 Out players back for the coming Sunday), so it
  marks doubt, never "out". `nfl_props_log` now keeps the model's figure
  beside Kalshi's mid on every priced leg, first sighting and close
  (`_model_rungs`, read from the shared slate sim, never run there), and
  ledgers `NFL-DRIFT` when a leg type sits 4+ points one way across 4+
  games (`drift`) -- the record totals, spreads and props never had.
- **The NBA / NHL seasons** (2026-10-17, `pro_season.py` rules + Monte
  Carlo, `pro_season_job.py` the job and the board): NHL three per division +
  two wildcards, the division bracket, points then RW, ROW, W, head-to-head,
  goal difference; NBA top six, the 7-10 play-in, two-club and multi-club
  tiebreakers with the restart. Replaying every real result of 2023-24 to
  2025-26 reproduces ESPN's final conference order place by place and the
  NHL's real first-round pairs (`research/pro_format_check.py`; three
  committed fixtures replay offline in the guards). **ESPN's NBA
  `playoffSeed` is POST-play-in** (the two clubs knocked out keep their
  regular-season order). Results and schedule come from ESPN's MONTHLY
  scoreboards (`espn_games`; a date range answers 400, a year stops at 1,000
  events); the NBA Cup final (competition type CC) is the one Cup game outside
  the standings. **The NHL plays 84 games a club from 2026-27**
  (`games_per_club`). Each ordered pair is priced at the board's validated
  probability (NBA production, calibrated; NHL 0.5 production + 0.5 deep
  engine, whose games give the overtime/shootout split); 10,000 seasons from
  the real standings in a niced child (`run_isolated`, HEAVY_BUILD, PC-first;
  deep jobs `nba_season` / `nhl_season`). **The NBA's level is Kalshi's
  win-total ladder** (median and IQR of books within 10c: a dead book's "mid"
  read Portland 65+ wins at 49.5%), each club's strength SD from its ladder's
  width; the NHL draws each club's strength every season with SD
  TAU x sqrt(K/(K+g)), TAU chosen by `research/pro_season_backtest.py`. The
  board prices the series Kalshi actually lists (`SERIES`; Kalshi's
  WAS/GSW/NOP/NYK/SAS/UTA map to ESPN's codes only when the code is not
  itself a club that season), edges net of fee on YES and NO. ESPN's bare
  `/standings` serves the PRESEASON until opening night: both boards read
  `seasontype=2`. Report: `research/reports/pro_seasons.md`.
- **DFS information sources** (2026-10-18; each rule written before its
  numbers). **Recent usage SHIPPED** (`nfl_usage.py`, from 2026 week 7):
  inside `nfl_dfs_sim.weekly_games`, before `nfl_recon`, each team's
  targets and carries are re-split q = 0.7 Sleeper's share + 0.3 the trailing
  allocation (target share over 6 games, carry share over 3, from Sleeper's
  own box scores in the deep store; players with two games for THIS team
  split the share Sleeper gives them, the rest keep Sleeper's). Team totals
  never move; catch rate and per-touch figures stay Sleeper's; `proj_pts`
  moves by the PPR change (the legacy sim pins to it). 2025 test: targets
  -0.0049 +/- 0.0011, carries -0.0032 +/- 0.0015 per team-game in log loss;
  air yards (WOPR) lost to plain share (`research/reports/nfl_usage.md`,
  which also covers the absent-lead-back case). **Weather FAILED** stage 1
  (wind, rain, cold vs Sleeper's team lines and the FG curve, 2025): the
  forecasts (`nfl_weather.py`: stadium table by ESPN venue name, baseball's
  `weather.get_weather`) show on the DFS tab as information only
  (`/api/nfl/weather` reads the log, never fetches); a wind-only stage 2
  (`research/nfl_weather_stage2.py`) is read once after 2026 week 18 and
  needs the frozen pre-game forecast to agree (`research/reports/nfl_weather.md`).
  **Kalshi's player props** are logged by `nfl_props_log` (the recorder,
  every 30 min): ladders, implied means, Sleeper's projection and the
  stadium forecast per game, FROZEN from five minutes before kickoff
  (deep store `nfl_props_<season>_wNN`); `research/nfl_prop_anchor.py`
  chooses a blend on weeks 6-7 and tests it on 8-10 -- until it passes,
  nothing reads the props. **The real fields** (`dk_standings.py`): the
  owner drops DraftKings standings exports into `pc-standings/` on the PC
  (gitignored: 170 MB each, and they name every entrant);
  `pc_worker._task_standings` summarizes each once under `cl_field`'s
  definitions (reproduced exactly on week 1's 832,342 entries) into the
  deep store (`dk_field_<cid>`, `dk_field_index`), the deep sync ships them,
  and the server only reads them (`/api/dfs/fields`, the tab's Real fields
  card). `research/field_refit.py` decides the CL_* field constants on week
  7+ Sunday GPPs (fit 3, test 2); until then nothing changes a board.
  **From away** (2026-10-19): an export the owner sends to a session is
  summarized there exactly as intake does and only the SUMMARY is committed
  (`research/data/dk_fields/`, `final` in it). `dk_standings.listing`/`summary`
  read those beside the deep store, a committed summary winning, and a
  look-back stored before its field arrived is recomputed once
  (`dfs_hindsight.due`). The export itself never enters git.
  **Football hindsight** (`dfs_hindsight.py`, run from `dfslog.tick` after
  grading): the tournament boards' top lineups are logged at lock
  (objective `tourney:<list>`, kept OUT of the builder's correction fit);
  each settled classic draft group then gets, once, the best lineup on the
  real points (the sheet's own optimizer, so the sheet's rules -- not
  necessarily DK's true optimum; 1.1 s measured on week 1's 746-player pool),
  every logged lineup's share of it and its finish in the real field (the
  standings summary's rank knots), Sleeper's pre-kickoff projection vs the
  box stat by stat, and an ESPN box-score cross-check (dfs_actuals sport
  `nfl_hindsight`). Week 1, checked: best 292.1 against the Millionaire's
  273.98 winner; the board's top lineup 205.4, about 12,107th of 832,342.
- **The Matchups tab** (2026-10-23; every piece reviewed by a Fable agent and the
  orchestrator until both agreed, `research/reports/matchup_agreement_log.md` and
  `research/reviews/`). Offense vs defense for NBA and NFL player props.
  - **Shared parts:** `prop_value` turns a model probability and a Kalshi book into
    fee-in EV at the ask, with labels (high probability, +EV, robust +EV, strong fit,
    low evidence).
    - `thin_book` / `stale_quote` qualifiers stand in for a +EV label on a book that is
      not believable or a quote over 600 s old.
    - Robust is refused on a zero-width band.
  - **`prop_dist`** holds the ladder distributions. **`kalshi_nba_props`** reads
    KXNBAPTS/REB/AST/3PT/PRA and their fees. Its event ticker is AWAY then HOME on the
    Eastern date. A player who never plays settles at a fair price, so every
    probability is conditional on him entering.
  - **NBA engine** (`nba_matchup*.py`):
    - ESPN play-by-play cut into player-games, with as-of profiles from strictly earlier
      MT dates.
    - Validated on 2024-25 and tested once on 2025-26 by a hashed rule
      (`research/reports/nba_matchup.md`).
    - Overall defense allowed and teammates out ship in every market. Position
      allowances ship for rebounds and assists. Style x concession and comparable
      opponents ship for rebounds only; for points, assists and threes they measured
      nothing and show as weight 0.
    - Teammates-out reads the box in research and ESPN "Out" in production, so its gain
      is an UPPER BOUND (`research/nba_absent_reasons.py`).
    - Kalshi's tight books beat it (0.542 vs 0.556 log loss), and betting its edges lost
      14%.
    - `dk_fp` beats DK's average (MAE 8.16 -> 7.81) but feeds no builder: Vigil has no
      NBA lineup builder. It shows on the tab.
    - The replay never takes in the owner's MT today, because `board()` refuses a date
      the book holds. The job therefore runs every six hours, not nightly.
  - **NFL**:
    - **Data:** `nfl_matchup_data` / `_profiles` hold nflverse pbp, participation and
      FTN (credit "FTN Data via nflverse", CC BY-SA). Participation is published after
      the postseason, so 2026's coverage is prior-season only.
    - **Model:** `nfl_matchup.py`, `research/reports/nfl_matchup_model.md`. Nothing about
      the defense beats Sleeper's line out of sample: coverage, shells, blitz, box,
      position allowances, similar defenses. The man/zone target split is real but adds
      nothing past Sleeper.
    - **What ships:** only a game-script term on passing TDs (weak evidence). The DFS
      hook does not ship.
    - **Calibration:** low-threshold rushing yards run high for RBs (10+ 0.86 vs 0.67
      hit). The line-dependent fix failed its own 2025 gate, so the slate says so.
  - **Serving:** `matchups.py`.
    - A slate is built in a niced child under HEAVY_BUILD, today or tomorrow MT only.
    - It is published as an index (the scanner's lines) plus one boardshare piece per
      game. One NBA night is 13-19 MB whole.
    - An empty or failed build never out-freshens a full board. It is backdated, retried
      after 120 s, and the PC's `pc_board` never publishes one.
    - "market better on record" mutes the EV of any +EV line in a stat where Kalshi's
      pre-game price beat the engine (NBA tight books; NFL 2026 frozen lines).
    - Deep jobs `nba_matchup` (6 h) and `nfl_matchup` (daily; backfills once, with raw
      files dropped, any of the two prior seasons the daily rule does not read itself). Guards: `tests/guards_{prop_math,nba_matchup,nfl_matchup_data,nfl_matchup_model,matchups}.py`,
      run from combo_audit_guards' MX section.
- **The NFL rushing model** (registered 2026-10-29 02:13:45 UTC; every piece reviewed by
  Sonnet, Opus and Fable agents until all agreed, `research/reviews/nfl_rush_*`,
  `research/reports/matchup_agreement_log.md`).
  - **Data** (`nfl_rush_data.py`, `nfl_rush_profiles.py`, `research/nfl_rush_dataset.py`;
    `research/reports/nfl_rush_data.md`): aux stores beside the matchup stores (on-field counts,
    PFR advanced and snaps, NGS, injury reports), as-of RB and QB profiles shrunk by measured
    split-half k. **Licence default**: PFR and NGS are DISPLAYED with credit and never enter a
    fitted feature (`RP.SOURCE_OF`, `RP.restricted`); the owner's call on them is still open.
  - **Model** (`nfl_rush.py`, `research/nfl_rush_model_{frame,fit,rule}.py`;
    `research/reports/nfl_rush_model.md`): opportunities x efficiency as compound distributions
    -- RB carries, yards and receiving; QB designed runs, scrambles, sneaks and kneels (sacks are
    not rushing, kneels are) -- with Sleeper's line as the level (no intercept), walked up a
    35-decision ladder (Sleeper -> overall defense -> RB/QB defense -> concept/front -> designed
    vs scramble -> full).
  - **The test** (`research/data/nfl_rush_model/prereg.json`, block 92a99b8e): chosen on 2025;
    2026 weeks 1-6 can only block; then each kept group is KEPT or reverted ONCE by the
    prospective window -- every game after the registration through week 11 (to 13 while under
    40 games), read from a registered tree after the window's last game + 48 h (the runbook is in
    the report). A real effect at the 2025 gate's edge survives that window about half the time.
  - **The result (read 2026-10-29): NO group passed its 2025 gate, in any of the 8 markets** -- nothing
    ships, the prospective window has nothing to keep (validate.json e8df86cb, report section 9).
    Reviewed (Opus, Fable): valid records, reproduced to the digit, not a defect -- and a statement
    about POWER: the gate needed ~0.17-0.20 per SD on RB rushing yards, two to three times the largest
    term the training seasons carry. Say "no group passed its pre-registered test", never "the market
    cannot be beaten"; a retry needs pooled seasons or a lower-variance statistic. Descriptively,
    2026's rushing outcomes ran UNDER Sleeper's frozen line on every market (RB rushing yards -4 to
    -5 a row).
  - **The board hook is inert until `research/data/nfl_rush_model/coefs.json` exists** (the board
    byte-identical without it); `nfl_rush.load_coefs` refuses a file whose record chain breaks. The
    export ships nothing but would still switch on display-only content (+11% board, ~1.7 s a
    build). The owner said not now (2026-10-29), so it is committed as `coefs_export.json` and the
    breakdowns are one rename away.
  - **Custody**: the registered dataset's CSVs and both frames carry PFR/NGS-derived columns, and
    the 17 registration-era deep pickles hold raw feeds: none is in this public repo
    (`.gitignore`). prereg.json pins each by sha256 (the pickles also by content digest, so a
    fresh nflverse rebuild verifies); the owner holds the archive (sent 2026-10-29); the week-6
    props log is on sim-history. Restore them into the WORKING tree before any command.
  - `matchups.nfl_data_job` refreshes the rushing aux beside the matchup stores (prior seasons
    backfilled one at a time, a weekly retry marker). Guards: `tests/guards_nfl_rush_{data,model}.py`
    in the MX section.
- **The Matchups record** (2026-10-29, `mx_record.py`; design and build reviewed by a Fable
  agent, `research/reviews/matchups_record.md`). The owner: "add a record to the match up tab
  for both nfl and NBA ... grade itself and tune itself based on results."
  - **What it keeps**: the background owner reads the published Matchups INDEX (whoever built
    it) every 10 minutes and keeps ONE row per Kalshi ticker: the first pre-game sighting (never
    changed) and the last (the close, until the start). A sighting's moment is the LATEST of its
    quote, its build and the published file's mtime (the server's clock: a PC whose clock runs slow
    cannot pass a build after the start off as pre-game); a game without a readable start proves
    nothing pre-game. Kalshi's figure is kept only off a fresh quote (600 s). Standard series
    only (a *PROT twin's scalar is a refund). Kept: the engine's figure (`p_raw` when tuned),
    Kalshi's de-vigged mid off a believable two-sided book, the labels as a bitmask, and for a +EV
    line on a fresh quote the cost on the side its labels read (`label_side`: the NFL's strong-fit
    side, else the larger-EV side, else YES -- grading NFL picks on best_side graded a fit-side NO
    on YES).
  - **Grading**: one `/markets?event_ticker=` call per event, paced, oldest first; an event tried
    and unsettled waits `RETRY_S` (1,800 s), and the first refused fetch ends the pass (it was already
    retried three times: the rest would be ~120 requests into a rate limit). yes/no is scored at grade
    time (log loss, Brier, the one-contract result fee in); SCALAR -- a player who never played,
    settled at the pre-game fair price -- is a void, counted, never scored; seven days unanswered is
    dead. Dates are Mountain-time sports days (`_DAY`, game_ts - 7 h: UTC dates split one NBA night
    in two and doubled the date floors).
  - **Tuning**: per sport x stat, a Platt on the engine's figure, fitted on graded closes within
    2 h of the start, each rung weighted 1/rungs (a ladder is ONE forecast); adopted only when it
    beats the engine OUT OF SAMPLE (forward chaining over dates, > 0.001 nats and > 2 SE clustered
    by game, over at least 30 scored games -- right after the date floor the chain scores a day or
    two, and 2 SE over 4 games adopted a calibrated engine 6.5% of looks) past 300 ladders over more
    than 10 dates, damped by days/30, refused unless increasing. The decision is STICKY (a look
    comes every 6 h all season): a tuned stat keeps its Platt while the gain stays above 0 and 1 SE. The
    blend with Kalshi's mid is fitted and REPORTED, never applied (priced against the mid it was
    fitted to, its EV is ~0 by construction). Applied at build time (`matchups.produce`, server
    child and PC alike) with each engine's own value and label helpers; the index carries `p_raw`
    and `tuning`. A synthetic season (96,000 rungs) fits in ~11 s a stat in pure Python
    (`_agg`: equal rows are one weighted row, exactly).
  - **Kalshi's side**: a stat whose live engine-vs-book record is DECIDED (`mx_record._decide`: 10
    dates AND 30 games, 2 SE by game) replaces the static research figure in
    `matchups.market_record`, either way -- the "market better" mute lifts where the engine earned it
    and holds where Kalshi did. The tab's bold call is that same decision, never a looser one.
  - `/api/matchups/record?sport=` is SQL sums over per-row scores (never a fetchall); the tab's
    Record view. The fit runs inside `calibrate.write_params` (next item). Guards:
    `tests/guards_records.py` (the RC section).
- **One calibration, every process** (2026-10-29, `calibrate.write_params`). The PC's predlog.db
  is never graded (`predlog.start` runs in app.py only), so every `calibrate.apply` on the PC was
  the identity -- and the server serves the PC's boards (uncalibrated NFL / college / NBA / NHL
  games). The background owner now fits every registered calibration, the combo maker's blend
  weights and the Matchups tuning ONCE every 6 h, in a niced child under the heavy-build gate
  (`mx_record.tick`), into `calib_params.json` beside predlog.db (gitignored); every process reads
  that file (`file_params`), and the PC fetches it before each cycle and boards pass
  (`pc_worker._fetch_params`, `/api/calibration/params`). No file, or one past 48 h, or a model
  registered after the file was fitted: the old per-process fit.
- **Carried predictions** (2026-10-29). The boards that log inside their builders (NBA/WNBA,
  NHL, NFL, college, soccer) also carry those rows beside the board (`predlog.carry`,
  `plcarry_<key>`); the boards sync ships them and the server logs them from its copy
  (`predlog._harvest_carried`), only rows whose event START is known and still ahead. Before
  this a PC-built board reached the server's record only when the server rebuilt it as the
  fallback. Golf carries too since the accuracy batch (below), its start ESPN's event date
  (midnight ET of round one's day). NBA/NHL/soccer rows now log their start, so their closing
  snapshots run to the start, not midnight ET of the day.
- **NFL and college game models against the market** (2026-10-29,
  `research/reports/game_xref.md`: a Fable agent's study, its figures reproduced from its rows by
  the orchestrator and every item agreed, section 8).
  - **Cross-referencing adds nothing to the price.** The drive sim, the PD ratings, ESPN FPI and
    Sleeper's points, alone or stacked, add nothing to the closing line for either sport (every
    stack failed walk-forward). Do not add them as features.
  - **College** (`cfb_board`): the drive engine compressed every margin (actual on engine slope
    1.37-1.44) and played every game at 27.5 +/- margin/2 a side (a 56-point total, SD 2.3,
    correlation -0.09 with the real one). The headline is now Phi(fair margin / 18) when priced
    (-0.016 +/- 0.002 nats, 4+ SE each season) and the calibrated Phi(model margin / 18) when not
    (the "cfb" calibrator is fitted on that figure). The simulated margins are centred on that
    margin and the totals on Kalshi's implied total (`kalshi_cfb.implied_total`), with unbiased
    integer rounding (`_centre`: a fractional shift leaves every whole-number rung where it was),
    before the ladders, the ATS cover, the maker's legs and the expected score read them. The
    card's total is Kalshi's (`exp_total_src`). No total lean is filed (`cfb_tot` keeps its history)
    and `cfb_total` is no longer logged. The ATS pick is now the model's lean at its weight (51.0%
    on 2024-26, no edge either way) instead of the underdog 95% of the time. The season sim (CFP
    odds) still plays the compressed engine: untested, unchanged.
  - **NFL**: the drive sim trails the closing favourite by 2-3 points over 2024-26 (65.0% vs
    67.6%; 2026's first 107 games 59.8% vs 63.5%), and no tuning closed it (a fitted temperature
    and margin/total scalings each made the next season worse). The market anchor (0.3 sim +
    0.7 close) FAILED its own rule (2026 reversed inside 1 SE) and is a registered test (section
    8.3: weeks 9-18, read once, not before 2027-01-12). The NFL record shows Kalshi's favourite
    and log loss on the same games (`store.market_benchmark`). The combo maker's game legs lean
    on the market: `nfl:Spread` 0.3 and `nfl:Total` 0.2 (the sim's walk-forward weights beside
    the close were ~0 and negative); the moneyline keeps its live fitted weight.
  - Registered for later (section 8.4): the college unpriced total (item 5), the anchored sigma
    and engine dispersion (6), the NFL late-season PD blend (7), the NFL total level off Kalshi's
    ladder (8). Never hand-set a margin expansion or a total shrink.
- **The accuracy batch** (2026-10-29; the owner: "look through and talk through any updates we
  don't have that would give us better simulation accuracy"; a Fable agent's survey and the
  orchestrator's, reconciled in `research/reports/accuracy_gaps.md` section 13, the build in 13.4,
  guards `tests/guards_accuracy.py`, section AG).
  - **The book beside Kalshi.** `predlog.book_probs` reads DraftKings' (or ESPN BET's) pre-game
    moneyline off the ESPN competition each schedule parser already holds (ESPN calls the latest
    pre-game line `close`; never a "live" provider, never a game past "pre"), and `book_row`
    refuses a start unknown or passed (the schedules are cached) and a row with no Kalshi price
    beside it (the read compares the two in the same row). The NBA, NHL, NFL (regular season)
    and college boards log it beside the model's row, same pass, same Kalshi price, to shadow buckets
    `nba_book`, `nhl_book`, `nfl_book`, `cfb_book`, carried. Nothing reads them until the registered
    13.2 read (10 dates and 150 games a league): only there may the book replace Kalshi as a level.
  - **NBA and NHL headlines on the measured weight.** The win% is `model_trust.anchor`: w x model +
    (1 - w) x Kalshi's de-vigged moneyline, w = `model_trust.weight(league)` (backtested; moved only by
    `refresh()`, never by hand). Spread and total rungs read the same w against their own de-vigged
    price (`model_trust.rung`, the YES ask where one-sided) even when the moneyline is one-sided;
    best bets and the cross-sport combo read the rung headline (`pct`). The model's own figure stays
    on the card (`p_home_ours`, `model_pct`) and is what predlog logs -- the record grades the model,
    not the headline. The weights travel in the shared calibration file (`calibrate.compute_params`
    "trust", plain numbers): `model_trust._record` reads this machine's store, then the file, so a PC
    without its own record anchors at the server's measurement; an anchor served the 0.20 default
    is ledgered `TRUST-default`.
  - **The record's plumbing.** UFC rows carry Kalshi's de-vigged price (`mkt_win`); a fight is logged
    only before its card's day (the ticker's day, `_event_ts` -- UFC close_time is a backstop weeks
    later), and the raw model goes beside the blend to shadow `ufc_model`. Tennis logs pre-match
    rows only (`in_play` or no readable start skipped; the start is Kalshi's `occurrence_datetime`,
    on the raw `/markets` rows -- `kalshi.markets_for_series` does not return it), the raw model to
    shadow `tennis_model`, and `/api/sharp` splits
    both tennis buckets by series (`predlog.by_series`, SQL sums). Golf rows carry the two-sided YES
    mid, Kalshi's close and the start, logged before round one only, and carried. Racing has no
    bucket: deferred to the 2027 openers (13.4: the boards keep only the model's top drivers and
    match prices by name).
  - **ESPN's projections beside Sleeper's** (N1): `nfl_props_log` keeps them in `store["espn"]`
    (same name key and freeze; pages of 50 sorted by the week's projection, stopped under 0.5
    points, at most every 3 h; stat ids checked by rebuilding ESPN's PPR to 0.01 on four players).
    Read by nothing until the registered read after week 18 + 48 h.
  - **The DFS engine's own weight**: `nfl_dfs_engine.DEEP_WEIGHT` 0.30, the weight it was tested
    at, so `nfl_game_sim._DEEP_WEIGHT` can be judged on the live record (13.2) without moving it.
  - **Registered reads, dates fixed** (13.2): B1 NBA last-season prior on or after 2027-01-02; B4/H2
    deep weights at 400 graded shadow games a league; NFL deep weight at 200 graded `nfl_deep`
    games (asymmetric: a null sends it to 0); C1 at 10 dates and 150 games; N1 after week 18 + 48 h;
    N2 weeks 10-16; G2 and C3 replays (C3 before 2026-12-06); tennis blend per series in December.
- **The NFL game viewer** (2026-10-29, `nfl_viewer.py`, `static/nflview.js`; designed, built and reviewed with a
  Fable agent, `research/reports/nfl_viewer.md`). The owner: "view the most average play ... for every game ...
  play by play", tapped from the NFL card ("Watch the typical game").
  - **The typical game.** The deep engine plays the matchup 5,000 times on the board's own profiles, each game on
    its own seed (`game_seed`, crc32). The viewer opens on the game nearest the medians of margin, total, each
    side's net passing and rushing yards, turnovers and plays (`DIMS`, scaled by SD), among the games won by the
    side the MEDIAN margin favours. Ties are excluded; a median of 0 is a pick'em.
  - **Beside it.** Ten other games ("Another sim") and a per-down table over all 5,000 games. The header's
    probability is the BOARD's deep figure, never a rate of the viewer's own games.
  - **The log is passive.** `play_game(log=)` carries the log in the state dict, and every hook tests
    `is not None`. A guard plays every box identically with and without it.
  - **Names are drawn after the game**, on their own generator.
    - Source: Sleeper's week (offence, IDP, kickers); out-type tags are never drawn.
    - A defender is named only on a certain event: the tackle that ends an in-bounds non-scoring play, a sack, an
      interception, a lost fumble, a return touchdown. No breakups, no missed tackles (the owner's call).
    - Kickoffs and punts are spots. An interception or fumble is told as where the defence takes over (the
      engine draws the net spot, never a return length).
  - **Where it runs.**
    - The PC builds in its boards pass (`pc_build`, a niced child, 150 s a pass); a piece is rebuilt at 12 h.
    - The route reads only. A missing upcoming game is built in a niced child under HEAVY_BUILD.
    - Nothing within ten minutes of kickoff is built. After kickoff a piece is the pre-game simulation, marked
      as such and refused at +4 h.
    - One boardshare piece a game (`nflview_<season>_wNN_<AWAY>_<HOME>`, in `_KEEP_LONG`), with the games inside
      as JSON text; the route sends one.
  - **Engine findings** (the report's section 6).
    - A sack's net gain could carry the ball past the goal line without a score (3.7-3.8% of games). It is now
      stopped at the 1, as this engine's pre-snap penalty is (-0.056 points a game; home-win rates within
      +/-0.3). The screen marks every sack that nets yards as an artifact until the refit below.
    - `research/nfl_deep_fit.py` reads a failed fourth down's end spot in the NEXT team's frame (ESPN does not
      flag a turnover on downs). The sack table therefore holds 30-yard gains, and the down-4 cells hold 15-yard
      losses (a stopped fourth down in a team's own territory can even read as a conversion). This is NOT fixed
      here: a refit moves every deep number, so it is its own change, registered for 2026-11-06 with the
      walk-forward re-run (the report's 6.2).
- **The DFS player picker** (2026-10-30, `research/reports/dfs_picker.md`; designed with a Fable agent, the build
  reviewed by Fable (round 1) and an Opus agent (round 2, Fable out of credits), every item agreed). The owner:
  "exclude player ... force captain ... It would create all lineups with those players forced into the lineup. Of
  course the automatic builds would do all available players anywhere."
  - **Where.** The lineup sheet's Run and Build on the PC take a `pick` -- `{exclude, force, captain}`, always its
    own key (`exclude` on `/api/simulate/dfs` is GAMES). Nothing on a schedule reads one: the PC's own boards,
    the look-back and the hindsight record (`picked:<objective>`, `_BOARD` skipping `_pick`) see the builder's
    lineups only.
  - **The FIELD is never picked.** The public still plays the man excluded and never the man forced past the
    depth gate. The sheet's contest sim takes `field_players` and a floor pinned to the unpicked optimum (the
    classic sheet's field hash is equal; the showdown sheet's floor comes from its unpicked search, which is
    unseeded). The classic board keeps its first solve, field and `opt_pct` exactly; its candidates come from a
    SECOND solve over the picked pool, picked steady columns and grafted draws (`graft`, never bringing an excluded
    man), and the board stamps how many of each solve's answers hold the pick (`pick.second_solve_held`,
    `steady_held`: all of them, since a forced man carries 4x a stack pair's bonus and the solver never trades
    him for the pair's catcher; `_safe_pairs`' `fits` keeps each pair buildable beside the forced men -- the solver
    holds one tight end, four receivers, six backs and receivers -- pinned directly, since no board count sees it). The showdown board narrows only `entry_ok`/`cpt_ok`; a forced man its universe cap would
    drop is refused (dropping the next extra instead moved the field). A forced man must be projected, and on a
    board already a field extra gated by the depth chart's RANK only (one more row in the sampler's pool changes
    the field even masked out; a roster or off-chart gate is refused, as on the sheet).
  - **The sheet's search** (`nfl_dfs.optimize(fixed=)`): forced men are SEATED, not just biased (+1,000 alone
    answered "no valid lineup" beside a legal 124.3), and a picked fill holds a salary reserve and chooses the
    quarterback, his stack and the bring-back before the slots; exact against a brute force on 225 random
    feasible picks (`research/dfs_picker_stress.py`). With any ONE of the seat, the reserve or the bring-back
    recount removed, the guard file's brute-force check stays exact (the cheapest start and the polish repair it on
    a pool that small; the stress harness missed 7 of 225 only without the recount), so each mechanism has its own
    guard (`_fill_checks`) -- a brute-force check alone cannot see them. A showdown captaincy counts the bias once
    (1.5x outranked every other captain), a forced captain's multi-entry is his n best distinct fills, and a picked
    captaincy draws 2x the fills.
  - **House rules vs the owner's picks.** DraftKings' rules are never waived. Ours (the depth rank, the team cap
    -- per team -- the punt's role, the showdown construction and captain rules) bind the free slots and are
    waived for the forced men only, each named on the result (on the showdown board, beside a free captain too).
    A forced defense facing a forced offense, a roster-shape break, a quarterback whose catchers are all excluded,
    or a pick that leaves no lineup we may enter is refused with the rule named, never a board of empty lists.
  - **The queue.** A picked Build is checked on DraftKings' pool by arithmetic (`nfl_dfs.pick_resolve`) when
    queued; Sleeper's side when the PC builds (`queued_attempt`). `pc_worker._picked_builds` builds the
    `..._pick` board first each cycle, under the plain path's showdown window (eight days, one game) and with the
    second solve in `_cl_sims`' estimate; a press the PICKER refused ("player picker: ...") is tried again every six
    hours, or half the time left to lock when sooner, or on a new press (`_PICK_RETRY_S`), never every cycle, while
    any other refusal (a roster lookup down) is tried again next cycle as the plain builds are. The automatic paths
    never see a picked request (`_split_picked`) and a window is consumed by the plain build only. Served on
    `/api/dfs/tourney?dg=&pick=1`. Guards: `tests/guards_picker.py` (section PK).
  - **Roster rules and portfolio caps** (2026-11-26, `dfs_portfolio.py`; the owner's priorities 5 and 6). The same
    `pick` carries `rules` (QB + N receivers, a bring-back required or forbidden, a team maximum, a minimum salary)
    and `portfolio` (a player cap with per-player overrides, a cap on the team an entry is built around -- the
    quarterback's, or the captain's -- and the most players two entries may share), percent of the entries, at
    least one entry for any cap above 0. Our entries only, never the field (a board with them keeps its field
    exactly); conflicts among the owner's own choices are refused at the queue with the rule named
    (`pick_resolve`); the stack and bring-back are classic-only. The caps are a constraint INSIDE
    `dfs_tourney.greedy_cover` (`limits`, held at 5, 10 and k entries; the best feasible candidate each step; a
    starved cover stops short and names the cap); `limits=None` is the old loop line for line. The sheet's search
    takes the rules (`_opt_rules`: under them the reserve fill and three polished starts a stack team; exact
    against the brute force) and its multi-entry the caps (`SheetCaps`). Every portfolio, capped or not, carries
    `concentration` (display only; `static/dfspick.js` dfsConcentration is the same definition for the prefix on
    screen). Guards: `tests/guards_auto_portfolio.py`.
- **Player eligibility** (2026-11-27, `dfs_eligibility.py`, `research/reports/dfs_eligibility.md`; the owner:
  "let the simulator determine whether a player belongs in a lineup rather than ... an occasionally inaccurate depth
  chart"). Three modes; production stays `conservative` (today's depth gate; guard EL1). `adaptive` admits a
  depth-gated man when his projection and recent (or pre-absence) target+carry share give at least the chance of a
  starter-level game the weakest 5% of kept players have (rule committed first in 3dae0ec; fitted 2022-24, tested
  2025); `unrestricted` takes every active projected man. Hard exclusions (inactive, Out/IR/suspended, no roster, no
  projection) never lift, no projection moves, PFR/NGS are not read; the new modes add players only from the field's
  own extras, so the field and every field figure stay byte-identical. DEN @ PIT: every mode chose conservative's
  exact best lineup and 20 entries (paired differences 0); best-lineup coverage 44.5 / 56.9 / 59.0%; Pittman
  admitted by adaptive (flagged returning: missed 6 games) but in no mode's lineups. History (could-have-built only:
  past boards are unseeded): the real best lineup buildable on 6 / 8 / 10 of 20 showdowns and 6 / 9 / 12 of 12
  classics. The house rules audited: the $11,000+ captain rule blocks the best lineup in 28% of DEN @ PIT worlds and
  lifting it raised the 20 entries' top-1% by 1.1 points (4.7 SE); no DST beside the other team's captain 12.6%, no
  K/DST captain 7.8%. Any rule change gets its own registered test. A default change needs
  `research/data/dfs_eligibility/prereg.json` (12 future showdowns recorded before lock, adaptive > conservative on
  the entries' top-1% by 2 SE, not worse on cash, the owner's sign-off; due at the 12th slate or 2027-02-16); the
  recorder (`research/dfs_eligibility_record.py`) is manual today.
- **The 2026-11-26 sprint** (`research/reports/sprint_2026_11_26.md`): the owner's Phase 0-10 backlog
  reconciled item by item (done / partial / missing / rejected / blocked), four audits with their scripts
  (`research/sprint_2026_11_26/`), and the readability pass (`tests/guards_ui.py`, section UI: the page's
  own intro notes fold to two lines; a note a board renders at run time never does). Read its section E
  before acting on a recommendation: each needs the owner's call or a test on week 13+ games.
- **The worlds cache and the lineup evaluator** (2026-11-26; `pool_cache.py`, `dfs_evaluate.py`,
  `static/dfseval.js`; design `research/reports/dfs_cache_evaluator.md`). Production tournament boards
  are UNSEEDED (the PC passes no seed), so a picked build used to simulate different worlds from its plain
  board.
  - **The cache.** `nfl_dfs_sim.player_pool` on the engine model files its worlds on the PC's disk
    (`VIGIL_POOL_CACHE_DIR`, set by pc_worker only). The key is the call, the simulator's code and every
    input. Scores are stored as int32 hundredths only where every value round-trips; writes are atomic and
    sha256-verified; the store keeps 24 entries / 12 GB. A rebuild or a picked build at the same world count
    reads the very same games. The suite keeps the cache off (`VIGIL_POOL_CACHE_DIR=""`).
  - **The artifact.** Every tournament build leaves one: the players, the full payout tables and the field
    it scored against (`eval_artifact` on the board). A board built before this has none: rebuild it.
  - **The evaluator.** The tab's "Evaluate your own lineup" queues a lineup (`/api/dfs/eval`, small JSON on
    the server). The PC's `--eval` lane (pc_loop, every 15 s, nice 10) simulates the slate's FINAL worlds
    once (40,000, their own seed, through the cache) and scores the field on them into per-world rank
    thresholds. It then scores the owner's lineup, labelled `custom`, beside the board's own
    (`optimizer:<list>`) on those worlds: out of sample for both. Ties are not split, so EV is
    experimental; `looks` flags repeat comparisons as development data. Exact against a brute force
    (`tests/guards_evaluator.py`, EV).
  - **The GPU** (2026-11-26; `research/gpu_bench.py` on the owner's RTX 4070 SUPER: the field-threshold
    kernel 7.8x the CPU, 102.5 s vs 11.6 s + 1.5 s start-up, every bucket and threshold equal, once the
    histograms take 50 worlds a bincount -- one world a call was launch/sync-bound). The evaluator uses it
    when `"gpu": true` is in vigil-pc.cfg (`dfs_evaluate.field_thresholds`, VIGIL_GPU=1 in the eval lane
    only): every job's first chunk is recomputed on the CPU and compared cell by cell, and a mismatch or any
    error sends the whole job to the CPU (`EVAL-gpu-mismatch` / `EVAL-gpu`). Worlds per histogram call is
    `"gpu_hist_batch"` (default 50; the owner's bench read 250 at 1.3 s of histograms against 9.0 s, the whole
    kernel 20.1x the CPU, 2.3 GB VRAM -- the candidate, pending the real workload's timing). With no "gpu" key
    the evaluator lane uses the GPU wherever CuPy is installed, at 250 (`EVAL_GPU_HIST_BATCH`; the owner,
    away for Thanksgiving, could not edit the config), and every result says what computed its thresholds and
    how long (`compute`, shown on the tab). **The unattended benchmark** (`pc_worker._task_gpu_bench`, the
    cycle's last task): fixed configurations (evaluator 40k and tournament 160k worlds, batches 50 and 250) on
    the newest REAL cached slate (`gpu_bench --source cache` refuses synthetic), once per bench version, never
    within 3 h of an NFL slate's start or with the lobby unreadable, GPU skipped above 83 C (nvidia-smi), 45-min
    cap a run; reports via the PC's token door to `/api/pc/bench` (last 20) and `errors/pc-bench-latest.json`.
    Nothing the server says shapes what runs. Guards PB. **The PC's real-slate bench** (2026-11-27,
    Thursday's showdown worlds): every bucket and threshold equal, evaluator 139.6 s CPU vs 3.3 s GPU at
    batch 250 (3.9 s at 50; 2.2 GB VRAM), tournament kernel 211.5 s vs 5.1 s. **The tournament scoring pass on
    the GPU** (`dfs_tourney._score_range_dev`, 2026-11-27): the products, buckets and the field's integer
    COUNTS on the device; the per-world tables and gathers in the CPU's own arithmetic, the histogram rebuilt
    from the counts to the CPU's doubles (`_uniform_sums`: n copies of 1/M added in order) -- so a UNIFORM
    field only (classic; the showdown universe stays on the CPU); the first range's first chunk recomputed the
    CPU's way and compared, any difference or error back to the CPU (`TOURN-gpu-mismatch` / `TOURN-gpu`); the
    board stamps `timings.score_device`. The best-of-candidates tally may break exact ties differently and the
    classic board never reads it. Off unless `VIGIL_TOURNEY_GPU=1`, which pc_worker sets only when CuPy is
    installed, "gpu" is not false, and the PC's own bench config `score` (the whole pass, CPU pool vs GPU, every
    figure compared, CLASSIC slates only) found it exact for the scoring code now running
    (`dfs_tourney.score_path_sig`). Guards GS1-GS5 (numpy standing in for CuPy must give the CPU's sums exactly).
- **Showdown vs Classic on real results** (`research/dfs_format_validation.py`, report
  `research/reports/dfs_format_validation.md`). It grades every exported board on real DraftKings points
  against the projection-optimal lineup on the board's own pool. No 2-6-game classic has been graded yet,
  so the small-slate hypothesis is open. Real-field finishes need the owner's standings exports
  (`dkfield-<cid>.json` on sim-history, three contests so far).
- **The NFL coaching engine** (2026-11-27; audit `research/reports/nfl_coaching_audit.md`, R1
  `research/reports/nfl_coach_r1.md`). A SEPARATE experimental engine, `nfl_coach_sim.py` (`coach-r1.0`),
  importing `nfl_deep_sim`'s samplers: timeouts, the clock by fifteen phases x score x no-huddle,
  situational calls, kneels (victory formation a rule), spikes, the two-minute warning, out of bounds,
  onside kicks, overtime; tables `research/data/nfl_coach_tables.json` from nflverse 2016-2023 (CC BY 4.0;
  raw files gitignored). **2026 week 13 on is SEALED** (`research/nfl_coach_data.load_season` drops those
  rows) for R1's registered read (`prereg_r1.json`, commit eaca95d, read on/after 2027-01-06 by
  `research/nfl_coach_r1_read.py`, which pins `nfl_coach_sim.py` and its guard file: never edit them; R2+
  are new files). Development: the end of each half repaired (Q2/Q4 snaps inside 2:00 8.5/5.2 vs real
  9.0/5.6, production 6.6/3.8), the leader's runoff 41.9 s vs 41.8 (production 26.8). Criterion 4 (late
  win probability) is expected to FAIL for power. The owner (2026-11-27): the original verdict stands
  permanently; a SEPARATE confirmatory test on fresh games with its own error control, realism reported
  apart from winner prediction, an independent DFS validation path; nothing reaches a DFS world or board
  until validated. **R2** (`nfl_coach_sim_r2.py`, `coach-r2.0`; evidence `research/reports/nfl_coach_r2_evidence.md`,
  registration `prereg_r2.json`, commit dfc026e): the evidence audit over 2016-2025 -- head-coach pass rate over
  expected, no-huddle and pace are real team-season traits that persist only with the same coach (r 0.5-0.7
  vs ~0.1 after a change); two-point rate, halftime breaks, fatigue, adaptation to the opponent's strengths and
  any staff-vs-staff interaction showed no evidence; adaptation to in-game success is real (z 18, placebo 0)
  and registered at the decision level only; offensive coordinators have no lawful structured source. Built:
  who gets the ball by situation (role x score / field zone / down, mean-preserving, dealt snap by snap from R1's
  passive log, conservation exact), +0.8 to +1.1 nats a team-game on who carried or was targeted in
  development. H1 reads 2026 weeks 13-18 after R1's read (the owner's call on sharing those games is open), H2
  (adaptation) after 2027. Nothing reaches a DFS world until H1 passes and a separate DFS-link test does.
  **R3** (`nfl_coach_sim_r3.py`, `coach-r3.0`; `research/reports/nfl_coach_r3.md`, `prereg_r3.json`, commit 59b15fc):
  the starting quarterback's substitution, a per-drive logistic hazard in margin against time left (lead and
  deficit apart), learned on 2016-2023 with recent seasons up-weighted; +0.28 / +0.26 nats a team-game over a
  constant (z 6.7, 5.5); a pulled starter stays out (6 returns in 410 decided-game pulls); conservation exact.
  Not built (no evidence or out of R3's reach): coach-specific pull habits, a starter already out, a backup's
  play selection (real, 4.7 SE, needs R4's fork of R1's call), a persistent pull for backs (they rotate, R2
  deals it). Known miss: trailing 17-27 late, under-predicted ~2.7x.
  **R4** (`nfl_coach_sim_r4.py`, `coach-r4.0`; `research/reports/nfl_coach_r4.md`, `prereg_r4.json`, commit c569318):
  personnel-aware calls after R3's pull. Pulls are labelled injury (the play says "injured" or next week's report
  lists him) or strategic, the kind drawn from the game state; injured starters return 11.2% (strategic 1%).
  Shipped by a rule written first (3 SE fit, same sign in development): a strategic backup's dropbacks over expected
  (+3.9 leading, -3.2 trailing, -4.6 close), completion -3.0 points either kind, an injury backup's interceptions
  +1.2, and after a STRATEGIC pull the first unit sits (lead back's carry share -14.7 points, top-two targets -6.5);
  tempo, air yards and sacks refused or no evidence. R4 forks R1's loop and plays R1's snap code unedited: with no
  pull it is R1 draw for draw and R3 line for line, conservation exact. Development: +0.33 / +0.52 nats a pulled
  team-game (z 2.8, 3.5). Registered window 2026 weeks 13-18 plus 2027 (80+ pulled team-games), read on/after
  2028-01-13 after R1's. R3's trailing-late miss is trailing teams protecting their quarterback (28 of 30 strategic,
  not late-season rest); a new hazard form is an R5 candidate on fresh games. R3's report carries an erratum: its
  R3 arm's deal draws are seeded one game later than R2's (noise, not bias; the registered read stands).
  **R4 checks** (`research/nfl_coach_r4_checks.py`, R4 report section H; guards N5-T1..T3): at play time R4 reads
  only each drive's first-snap margin and clock -- injury reports and play descriptions are training LABELS only.
  Flipping the ambiguous pulls' labels keeps the lead back's sit and the injury completion shift in every variant;
  the call shifts are fragile; the development gain stays positive. **R3's seed discrepancy** cannot move its read
  (`research/nfl_coach_r3_seed_check.py`: the registered endpoints take no draw -- 185,600 draws identical, bias 0,
  power unchanged). **R5** (`nfl_coach_sim_r5.py`, `coach-r5.0`; `research/reports/nfl_coach_r5.md`, `prereg_r5.json`):
  every richer pull hazard (possessions left, timeouts, the leader's need, coach tendency) was REFUSED by its rule on
  2025, so R5's hazard is R3's form refitted on 2016-2025; the trailing-late miss is a regime change (2016-21: 30 real
  vs 31.6; 2022-26: 47 vs 20.3) and stays UNFIXED -- a corner-specific term would be a threshold by another name; it
  is a registered secondary question. R5 does ship the second unit as per-player sit decisions drawn once per
  strategic pull (lead back 0.530; each top-two target 0.478 if the back sat, 0.050 if not; fitted jointly): lead
  back sits the rest of the game 48.8% simulated vs 51.3% real (R4 5.2%), +1.34 / +1.51 nats a pulled team-game
  over R4 in development; no-pull parity with R4. Registered on the 2028 regular season (the first season R1-R4
  have not taken), read on/after 2029-01-16, 30+ strategic pulls. **Amended by the owner (2026-11-27)**: R5's
  primary read is the 2027 regular season (`prereg_r5_2027.json`, block 02d486e9; its files pinned now; criteria
  copied verbatim; read on/after 2028-01-13 after R1, R2-H2 and R4), `prereg_r5.json` byte-identical and its 2028
  read a REPLICATION. The 2027 coaching family (R2-H2, R4, R5 primaries) is reported under Holm at a family-wise
  one-sided 5% beside each test's own z > 2 verdict (`research/reports/nfl_coach_family_2027.md`: statistics
  correlate at most +0.22, R4 x R5; a shared-games pass is weaker evidence than an independent one).
  **Coaching evolution** (`research/nfl_coach_evo.py`, `research/reports/nfl_coach_evo.md`, rule committed first in
  ae6e018): walk-forward 2019-2026 wk 12 over five tables -- the clock runoff drifts (a season-level random walk
  +0.169 nats a game, z 9.8, half-life ~1 season: more clock between snaps since 2016), backs' target share
  (+0.022, z 3.9) and the pull hazard (+0.0056, z 2.5; league odds ~1.7x since 2022) drift, the call and
  no-huddle do not; pooled the random walk gains +0.147 (z 4.4) but its no-huddle loss trips the rule's 1-SE veto,
  so full history stays and nothing enters R1-R5. League drift explains a tenth to a sixth of the trailing-pull
  gap. A per-table scheme (random walk for clock, targets, pull) would be R6, a new engine version, tested
  prospectively -- the owner's call, and on which games. **R6** (approved by the owner 2026-11-27;
  `nfl_coach_sim_r6.py`, `coach-r6.0`; `research/reports/nfl_coach_r6.md`, `prereg_r6.json` block 7ef4defe, commit
  35809d1): R5 with the three drifting tables moved by the evo study's league random walk projected to 2027 --
  pace x1.014 through R1's own pace setting, backs' target odds x0.896 in the deal (league-level; may partly
  double-count usage a roster already carries), the pull +0.0095 log-odds (R5's hazard already up-weighted recent
  seasons, so it barely moves); the call and no-huddle R1's. Drift off = R5 draw for draw; R1-R5 and R5's amendment
  byte-identical. Walk-forward +0.082 nats a game (z 7.7; clock +0.055, targets +0.022, pull +0.006), negative in
  the 2019-20 folds, positive from 2021. Primary 2027 (read on/after 2028-01-13, after R1), replication 2028 with
  the same frozen tables; joint power ~0.65 at the development mean, ~0.79 at the 2025-26 level. The 2027 family is
  now four (R2-H2, R4, R5-2027, R6; Holm z 2.241 / 2.128 / 1.960 / 1.645; `family_2027_r6.py` -- the three-test file
  R5's amendment pins stands as registered). R6's report section F holds the evidence-first DEFENSIVE plan (FTN
  from 2022 under CC BY-SA, persistence before any decision model, no lawful coordinator source); nothing built.
- **The deep engine's market anchor spreads 1.21x too wide** (2026-11-27, `research/data/c3_margin_scale.json`,
  C3 addendum): `nfl_deep_data.market_margins` converts Kalshi's win totals to points with
  `nfl_season._PTS_PER_WIN` 2.25, the drive engine's constant (set to undo ITS compression; the drive season sim
  returns the market's wins at slope 1.04), but the deep engine does not compress (realized vs anchored 1.007),
  so a deep season seeded from the market returns its wins at slope 1.206 +/- 0.031. An implementation error
  against `market_margins`' own contract. NOT fixed yet: the registered NFL deep-weight read is grading the
  deep engine as it stands; the owner's call on timing (proposed: its own constant, ~1.84, measured at higher n,
  with a slope-1 guard, right after that read). C3-N (the analytic resolution at 1.842) is registered on fresh
  games, read not before 2028-01-12. **The fix is STAGED OFF** (2026-11-27, `research/reports/deep_anchor_fix.md`,
  guards DA1-DA7): the constant measured on 24 real seasons (2002-2025, real wins shrunk halfway, production's
  `apply_anchor`) -- K* 1.8635 (95% 1.8385-1.8865), shipped as 1.86, fresh-seed slope 1.0002 +/- 0.0064 (tolerance
  1 +/- 0.03, stated first). `nfl_deep_data.py` stays byte-frozen for the deep-weight read (four coaching guards pin
  it), so the fix is `research/data/deep_anchor_flip.patch`: the flip is `git apply` plus moving those four pins to
  its sha256 in the SAME commit, and DA2 fails the patched file until `research/data/nfl_deep_weight_read.json` is
  committed. `nfl_deep_data.py` is in `pool_cache._CODE_FILES` (a flip is a new worlds key; frozen inputs then hit
  `PinMiss` and restart fresh), `nfl_game_sim._deep_profiles` refuses a nightly payload of the other anchor
  version (unstamped = version 1, served as today), the deep season payload stamps it. Downstream (v1 -> v2, shared
  seeds, weeks 12 and 9): 17+ blowouts 32.4 -> 30.0% / 28.5 -> 26.4%, totals unchanged, deep home-win figures 2.5-2.8
  points on average, playoff odds 1-2, DFS projections hundredths, correlations unchanged; the classic board moves
  less than its own seed-to-seed spread at reduced size. The OWNER'S CALL: flip right after the read or after the
  12-06 Millionaire locks (cl_field2's second registered contest would otherwise be a v2 board).
- **Classic field, second pass** (2026-11-27, `research/reports/cl_field2.md`, development weeks 10-11):
  the served field's 15-29 point deficit is per-player ownership (one chalk back carries most of it);
  steering onto a noisy-optimizer share of Sleeper's line moved by Kalshi's prop means (F1c) closed it to
  6.0 / 2.8 in sample, still 0 of the public's 20 most-copied builds. Registered
  (`research/reports/cl_field2_protocol.md`, commit 3d9194f): the first two Sunday main-slate Millionaires
  locking after it (2026-11-29, 12-06), each read once, need the owner's standings exports.
  `dfs_field_capture.py` (from `mlb_recorder`, every 30 min) freezes DraftKings' FPPG / opponent rank /
  status and ESPN's start and roster percentages at each kickoff, inside the props log's weekly store.
- **Owner decisions, 2026-11-27** (coaching and C3).
  - The 5% family-wise one-sided false-positive rate for R1's criterion 4 and its confirmatory test is
    APPROVED, on the condition that the calculation covers every scheduled look and the conditional
    eligibility (the confirmatory runs only when R1 fails criterion 4 alone); R1's own verdict stands
    permanently. Power is the chance a test passes IF the engine is better by a given amount, never the
    probability that it is better: a failed test can be insufficient evidence.
  - The coaching DFS read and R2's H1 MAY use 2026 weeks 13-18 (frozen implementation, endpoints and
    criteria; the dependence between tests acknowledged). If R2 is modified using R1's held-out results,
    R2 moves to fresh games.
  - Offensive coordinators stay OUT. A future enhancement, once reliable historical assignments exist: head
    coach, offensive and defensive coordinators, and the ACTUAL offensive and defensive play callers, which
    can differ and change mid-season.
  - R3 = quarterback blowout substitutions first (learned from score, time, possession, quarterback
    availability and team tendencies, never hand-set thresholds; conservation exact), later running backs
    and receivers; the coaching-change prior after it or in parallel.
  - C3: the suspected double compression correction (`nfl_season._PTS_PER_WIN` fed into Phi(./13.2)) is to
    be classified as an implementation error or a modeling proposal; the original replay stands; any new
    comparison is registered on fresh data.
- **Owner decisions, 2026-11-26.**
  - DraftKings D/ST points allowed include kickoff, punt and blocked-kick return TDs. They exclude the
    opponent defense's pick-six and fumble-return TDs (their conversions count). Verified on the official
    classic and showdown rules pages. Implemented the same day: `nfl_deep_sim` counts `td_def`
    (pick-sixes, the only defensive TD it plays) and `nfl_dfs_engine.off_points` is every opponent point
    less 6 x td_def and the safeties this defense scored (`SIM_MODEL_VERSION` 2). The effect is -0.08
    D/ST points a game, 3.9% of worlds moved. Guards C2b/C2c in `tests/guards_conservation.py`.
    Still open: safeties (not in the owner's table), and Sleeper's `pts_allow` on the actuals side.
  - The NFL deep weight stays 0.30 until its registered read. 200 rows = 100 games, paired per game,
    clustered by week, both the registered comparison and the production blend; an inconclusive result
    is insufficient evidence; any replacement protocol uses fresh data. **Settled the same day (owner):
    inconclusive -> 0** ("a null sends it to 0"; only deep > drive by 2 SE keeps 0.30, which 100 games
    cannot reach, so expect 0 at the read); the raw-drive comparison decides; the production blend vs
    calibrated drive alone is reported apart; the deep engine stays in shadow and the DFS engine's 0.30 is
    untouched. The only way back is the registered PROSPECTIVE test (report section 6, `prospective()`):
    fresh games only, one candidate (0.30), production comparison, O'Brien-Fleming looks at 130/260/390
    converted to Student t on weeks - 1 (plain z over 8-26 week clusters measured 3.7% false positives),
    futility at look 2, powered 80% at 0.015 nats a game (why 0.015, the dependence table and the schedule
    are in section 6). A 0 says improvement NOT ESTABLISHED with its upper bound -- never "no improvement"
    unless the bound is under 0.005. The full rule, every ambiguity
    and its setting, the decision table and the fresh-data protocol: `research/reports/nfl_deep_weight_rule.md`;
    the read as code, pinned there by sha256: `research/nfl_deep_weight_read.py` (counts only before the
    100th game); its data: `errors/nfl-deep-pairs-latest.json` (`/api/predlog/paired`). Guards: section DR.
  - Where Kalshi is measurably better, the Matchups tab may lead with its de-vigged probability, bid, ask
    and Vigil's figure shown apart. **Built the same day** (`static/matchups.js` `_mxLead`): a line's
    chance leads with Kalshi's two-sided mid where the stat's record has Kalshi the better forecaster (the
    "market better on record" test, live record once decided) and the book is believable; Vigil's figure
    and band beside it; one-sided or thin books and stats Vigil leads stay Vigil's. EV is unchanged (Vigil's
    probability at the ask, muted). The scanner shows the side's bid / ask and sorts "Highest probability"
    by the figure in bold; a line above each table names who leads per stat with both log losses.
  - **Duplication** (`research/reports/dk_duplication.md`, `research/dk_duplication.py`, guards DD): the real
    classic fields are 340-520x more concentrated than the board's sampled field (3.9e-6 to 1.0e-5 against
    1.0e-8 to 1.9e-8, each estimate within ~3-8% sampling): a field-MODEL gap, not sampling. Showdown 2.4x
    (sd_dup). Never scaled per lineup. Every board stamps `field_duplication` (the standings' own
    definition); summaries keep `most_copied` and `copies_histogram` from 2026-11-26.
  - **Duplication mechanisms** (2026-11-26, a Fable agent; `research/reports/dk_mech.md`, `research/dk_mech.py`,
    `dk_mix.py`, `dk_mix_classic.py`): both pre-registered mixture fields FAILED the owner's five criteria on their
    held-out contests -- nothing changes a board. Classic's noisy-optimizer cluster (w 0.10, sigma 0.10) reached the
    AGGREGATE collision on week 11 (0.61x real) while holding 1 of the public's 20 most-copied builds: aggregate
    accuracy is not lineup accuracy. Findings to build on, not repeat: the duplicates are EXACT, cross-entrant, at
    the $50,000 cap and near an optimum -- but another projection's (our optimum has 0 real copies; in showdown the
    public's chalk sits at our projection rank 108-9,074); 150-max entrants are the LEAST duplicated class;
    production's sampler steered onto the REAL per-player ownership closes ~85x of the classic gap (ownership
    concentration, sum of own^2 1.5-1.65 real vs 0.87 served), and the served classic field scores 15-29 real
    points under the real one -- probably the bigger payout/EV error. Next (each the owner's call): a public
    consensus projection captured before lock; classic ownership concentration and field strength first; the
    optimizer cluster re-registered with a stack rule on contests not read; weeks 1, 2, 6 raw exports for classic.
- **The Pick6 paper ledger** (2026-11-27, `pick6_ledger.py`, `static/pick6log.js`, the NFL tab's Pick 6 view;
  guards `tests/guards_auto_pick6.py`, P1-P13). The owner TYPES each DraftKings Pick6 entry (2-6 picks, the
  payout schedule as DK shows it, `k/n=x` with the stake included, a paper stake); nothing is scraped and no money
  moves. Vigil's figure is read off the boards the Pick 6 views already draw, only at the owner's exact line
  (never built in a web worker; an entry saved after its game date gets none). Graded hourly from `mlb_recorder`
  (`MREC-pick6`): NFL off Sleeper's week (regular season only), MLB off statsapi, NBA off ESPN's box; every other
  sport by hand. **DraftKings' own rules, read by the owner on its pages 2026-11-27** (they answer 403 to us): a
  tie with the line is VOID either side (`TIE_RULE` "void"); minimum participation NFL one snap, NBA one second,
  MLB one plate appearance (batting) / one pitch (pitching) -- not met is VOID, unknown is manual review (Sleeper
  carried snaps on 12 of 95 week-12 QB/WR rows, so any recorded stat counts as a snap and `gp` never does; an
  active NBA line with 0 MIN and no stats, or a 0-PA runner's specialized stat, is review). **The Pardon**
  (`PARDON`, the policy as a table): a LOSING More pick on a FULL-GAME stat (event-based and part-game markets
  never) is void when the qualifying exit is established (MLB: a hitter with exactly 1 PA), a loss when ruled out
  (NBA: ESPN's play-by-play has him on the floor after halftime, `nba_halves`; NFL: a defender or kicker, or
  second-half yards in Sleeper's split), else review -- Sleeper has no snaps by half and ESPN logs no period
  starters, so an NFL or NBA pardon is never established automatically. A pick in review carries two answers on
  the card; an entry settles only when every pick is decided and the record counts nothing in review. The record
  (SQL sums, 95% Wilson): outright accuracy (voids not a hit, stated), accuracy on non-void picks, void frequency
  by cause (did not play, tie, pardon, by hand), Vigil's calibration, paper ROI (void refunds are Pick6 credits).
  **Any line** (NFL): the PC's week board also files one integer histogram a player and stat on the whole-yard
  lattice (`nfl_dfs_sim.stat_hist`, comparisons only, so P(stat > L) at every half-point L is the board's own
  world count bit for bit; 387 players / 210 KB on week 13, +1.8 s) as boardshare piece
  `nfl_sim_dists_<season>_w<NN>_0`, built only where `VIGIL_PC_WORKER` is set; whole-number lines are not priced
  (continuous yards, no tie mass). The joint worlds stay on the PC.
  **v3 (the owner's confirmations, 2026-11-27):** a pardoned pick is a void; the Pardon covers only NFL, NBA,
  WNBA, MLB and NHL; an entry left with 0 or 1 non-void pick is REFUNDED (stake back, P/L 0, a push in ROI; cash
  or credits not stated by the owner). DraftKings' specialized participation rules are market kinds (`KINDS`),
  checked from each grader's own box: NBA first-quarter stats need Q1 participation (starter flag or a logged Q1
  entry), game-first markets need a start (ESPN's `starter` flag, trusted only when a team flags five), the
  second-half first scorer needs to start the second half (his first Q3 record is not an entry); MLB first-event
  markets need the starting lineup (`battingOrder` N00) and 1+ PA; a strikeout race needs both pitchers to start
  and one to reach 3 Ks. Not met is void, not established is review. **Whole-number lines** (`hist_mtl`): the week
  board's yards AND receptions are continuous, so More / tie / Less there is a MODELED convention (tie = the mass
  in (L-0.5, L+0.5]); the ledger keeps P(side | not a tie) as Vigil's figure with the outright and tie
  probabilities beside it, every one labelled "modeled (not a calibrated real-world rate)".
  **Where fractional realized stats reach the owner** (2026-11-27, `research/reports/nfl_discrete_alloc.md`): the
  tournament boards (engine model, classic and showdown) deal REAL integer events -- whole plays, team totals dealt
  by multinomial; the engine-fallback games, preseason boards and the showdown sheet (`SD_DISCRETE`) round
  continuous draws; the classic sheet and the NFL tab's WEEK BOARD (Pick 6 view, the ledger's figures, Sim and Best
  ball cards) are CONTINUOUS -- Sleeper's means times shared multipliers, TDs Poisson apart from the yards, no
  target, catch or carry drawn. Against 257 real games (2025 wk 1-12, 2026 wk 1-5) the week board's player spreads
  are too NARROW: real VARIANCE 3.40 / 4.39 / 3.38x its own for receptions / rec yards / rush yards (a variance
  ratio -- in SD about 1.8-2.1x; passing yards 0.88x, slightly wide; the first summary read the ratio as an SD and
  said "3-4x", corrected the same day); real DK points under its 10th percentile 38.7% of the time, inside 10-90
  41.9%; the Sim / Best ball cards' OWN floor/ceiling/boom over 5,738 player-weeks: under the floor 37.5%, over the
  ceiling 19.3%, boom 22.5% vs 11.7% shown. Its prop and Pick6 probabilities are overconfident, and every surface
  showing one now says so (muted label, no number changed; each ledger pick stores `model_kind`). The experimental
  discrete allocation (`nfl_discrete_alloc.py`, `ENABLED_IN_PRODUCTION = False`, nothing imports it) fixes those
  spreads (variance ratios 0.89-0.96, half-line log loss 0.486/0.480/0.458 vs 0.668/0.702/0.602, DK points KS 0.018 vs 0.302) but
  plays QB passing yards ~2x too wide and +2.7% / +4.4% high: NOT shipped; a registered test first (the constrained
  model, which already draws counts, is the nearer candidate). The 99.6-vs-100 bonus question is immaterial
  (0.13-0.19% of mass): the week board's bonuses run low because its spread is narrow. Guards DA1-DA5.
  **The registered replacement test** (owner-approved 2026-11-27, `research/nfl_wb_dist.py`,
  `research/reports/nfl_wb_dist.md`, `research/data/nfl_wb_dist/prereg.json`, candidates committed first in 88a3fed,
  registration 8c9fa95, guards WB1-WB7). The asymmetric misses are SHAPE, not mean: pass catchers have zero-yard games
  17-27% of the time and the week board gives zero ~0 mass (a mean times a continuous factor); targets run 1.37-1.48x
  Poisson (RB carries 1.98) and the board draws no workload; spread errors run 0.72 (QB pass TDs, too wide) to 8.52
  (RB receiving yards); teammates 0.16-0.38 too correlated. Development (fit 2025, scored 2026 wk 1-5): A the
  constrained simulator +0.099 nats a line (z 7.2), B the discrete allocation with a fitted QB term +0.112 (z 30), C a
  per-position x stat quantile map of production +0.101 (z 27.6) -- A and B bring 80% coverage to 72-85% and put
  15-25% on zero; C cannot create a zero and fails the vetoes. Registered read: 2026 weeks 14-18 (week 13 excluded
  whole: Thanksgiving had kicked off), Holm over A/B/C, vetoes on any position x stat cell (2 SE), pooled 80% coverage
  in 0.70-0.90, pair correlations; read once on/after 2027-01-13; nothing changes until it passes and the owner signs
  off. Pinned: `nfl_dfs_sim.py`, `nfl_dfs_csim.py`, `dk_scoring.py` (a changed file means the read runs from the
  registration commit's tree).
- **The crypto terminal** (2026-11-27, `crypto_terminal.py`, `static/crypto.js`, the Crypto tab; the owner asked
  for it back in this sprint's backlog; READ-ONLY and paper: nothing places an order). Kalshi's 15-minute up/down and
  hourly above/below contracts on BTC, ETH, SOL, XRP and DOGE, each priced off ITS OWN rules text (the simple average
  of CF Benchmarks' 60 seconds before the close; ETH's index is "ERTI"; DOGE's strike from `custom_strike`, since
  `floor_strike` is rounded; range, one-touch, a wrong index or a text/field disagreement refused, shown unpriced).
  The level is the median of four exchanges, labelled a proxy (CF Benchmarks is licensed). The model is a driftless
  walk at recent one-minute volatility with the settlement average's variance exact; where no volatility reconciles
  it with Kalshi's price the card says the market is reading a different level. **Kalshi's price beat it on the
  held-out week at every look** (`research/reports/crypto_validation.md`: +0.0057 to +0.0329 nats, z 2.3-4.8;
  the paper strategy's +4.04c in sample was +0.56 +/- 1.09c held out), so every edge reads "unproven" until the
  live record decides (500 markets over 10 days, 2 SE). The record is its own file (`crypto_record.db` beside
  predlog.db, `cx_forecasts`): rows at fixed looks 60 s+ before the close (nothing logged has seen the settlement
  window), graded off Kalshi's result, read by SQL sums into a summary file the background owner writes -- web
  requests read only that file. 14 days of every look, then the decision look to 180 days, capped at 300,000 rows
  (~53 MB). Measured in the guards (`tests/guards_auto_crypto.py`): 400 recorder passes 0.0 MB RSS growth, a
  200,000-row read +0.4 MB. `VIGIL_CRYPTO=off` stops it without a code change.
  **V2 stage 1** (2026-11-27; the owner's workflow: $10 Quick Order buys, cash out, maybe flip sides inside the
  15 minutes; `research/reports/crypto_moves.md`, rule committed first in eceaf12): the movement model PASSED its
  held-out test at 1, 3 and 5 minutes but is only a 1-3 point mean-reversion tilt; a settlement drift from it does
  not ship, and trading inside the window at REAL quotes lost 4.59c a trade +/- 0.75. The chart is vendored
  Lightweight Charts 4.1.3 (static/vendor, Apache-2.0 licence kept; no CDN) with 15 s / 30 s bars built in the
  BROWSER from Coinbase's public WebSocket (the server holds no stream), EMAs, VWAP, Bollinger, a forecast band and
  signal markers; panels for momentum/reversal, "what if" with the strike-touch probability, a banner that the model
  is losing to Kalshi, Kalshi's YES/NO price chart, paper positions. **CF Benchmarks via Kalshi**
  (`/trade-api/v2/cfbenchmarks/values?id=BRTI`, the `cfbenchmarks_value` WebSocket with the 60-second averages)
  needs SIGNED Kalshi API-key requests and an account entitlement -- not used; the four-exchange proxy stays until
  the owner supplies a key (env, never committed; a separate signing module -- the paper-only guard forbids signing
  code in `crypto_terminal.py`). **Stage 2** (`research/reports/crypto_hybrid.md`, rule first in df45623): Beginner
  Mode is the default (dollars; Quick-Order sizing with Kalshi's fee rounded up per order; cash-out at the BID after
  the fee; paper positions priced by prices the SERVER reads, never the page's). "Was that the last dip?": the random
  walk's textbook answer is badly calibrated on real data (final low said 4% where it held 13%); a challenger beat it
  on the final low, final high and re-crossing the strike (z 3.5, 7.1, 3.8) and is shown with the walk beside it.
  Hold / cash out / switch at $10 on Kalshi's REAL books (1-minute fill delay, every fee, 3,320 held-out windows):
  hold -$638, trade-only -$814, take-profit +15c -$712, the learned time-aware hybrid +$141 (+4.2c a window +/- 17.8)
  -- NO strategy is profitable past 2 SE; the hybrid learned to trust Kalshi's mid over Vigil's model. The proxy is
  off the official minute average by only 1.6-3.7 bp; the live record's 20.8 bp is mostly the price's own move in the
  last minute. Not yet: live logging of the direction and reversal predictions (only v1's settlement rows are graded
  live), the 30-second horizon, order-book imbalance, regime-switching models. Browser buffers capped at 480 bars, the
  feed closed when the tab hides; the library's sha256 pinned. Guards 60 in `tests/guards_auto_crypto.py`.
- `artifacts.py` — the schema-gated contract for the three compute stores
  (gamesim / boards / deep). Bump `SCHEMA` with any pickle-shape change.

## The PC offload

The checkout on the owner's PC is `D:\Games\Kalshi Betting\kalshi-betting-helper` (the owner, 2026-11-26).
The path has a space in it, so quote it in commands: `cd /d "D:\Games\Kalshi Betting\kalshi-betting-helper"`.
The worker runs from there with `vigil-pc.bat` on the `python` on PATH. One-off tools such as
`research\gpu_bench.py` run in a second Command Prompt in that folder, never in the worker's window.

The owner's always-on Windows PC runs `pc_loop.py` (git-checks every 60s,
restarts itself on update) which runs `pc_worker.py`. The loop WAITS on the
worker, and one cycle can be hours of board builds, so the worker checks for
newer code BETWEEN builds (`pc_worker._yield_point`, never mid-build) and exits
`UPDATE_WAITING` for the loop to pull at once; the heartbeat reports the build
in progress and any pull error, and the light shows both (on 2026-10-16 the PC
sat on the morning's code all day while the light said "within a minute").
The worker builds the SAME
artifacts with the SAME functions and uploads anything fresher than the
server's copy through `/api/art/upload`. The server adopts by file freshness
and self-computes whenever the PC is off — **the PC can only add speed or be
ignored**. **The server serves, the PC builds** (2026-10-29; the owner: the
website should house what the PC builds and take over only when it is off):
while the PC is on, a board on disk up to `boardshare.PC_GRACE_S` (30 min, or
the board's own TTL plus `PC_SLACK_S`, 12 min, when that is longer: the PC
rebuilds at the TTL on its own clock and its upload lands minutes later, and
the upload keeps the PC's age) is served as is (`boardshare.adopt` /
`pc_grace`, every builder: `nonblocking`, the NFL, DFS, CFB and futures boards,
the NFL slate sims); the server builds in a web worker only when the PC is off
or its copy is past the grace. Two exceptions: a placeholder or empty board
keeps its own TTL (`boardshare._short_lived`), and the Kalshi NFL and college
indexes keep their 120 s TTL whatever the PC's state -- they are where every
ask on those boards is read, a fetch and not a build. The PC's boards run on a pass
of their own every 5 minutes (`pc_loop._boards_loop` -> `pc_worker.py
--boards`), beside the cycle, so a tournament or deep build no longer ages
them, and each upload carries `X-Art-Age` so a board keeps its PC age on the
server. The pass also builds soccer (its board and the 6-hourly model refit)
and the NFL and UFC recipes (`pc_build`, never logged on the PC; the server's
tick files their slips, presets.tick's rule; with the PC on, neither the tick
nor the tab's kick builds here until the PC's payload is 45 minutes old, or 15
have passed without one: `_pc_covers`). A pass is a process that exits after
its upload and its builders are daemon threads, so it uploads what is done,
waits for the builds it claimed (`boardshare.held_claims`) up to 20 minutes
from its start (`pc_worker._BOARDS_JOIN_S`), and uploads again. An update stops
a boards pass in flight (`pc_loop._stop_boards`). Every nightly deep job is PC-first
(`_deep_pc_first`); the eight that ran inline in the background worker's
scheduler thread (f1, nascar, nfl_season, cfb, nfl, model_trust, nhl_deep,
nba_deep) fall back to one niced child (`deep_cache.run_child`) under the
heavy-build gate. `deep_cache.is_stale_daily` reads a payload's stamp once per
file version (`generated_at`), not one multi-MB unpickle per pass and poll. **The tab's "Build on the PC" is a dropdown of slates by name**
(2026-10-22; `dk.slates` "matchup" names a single game, "NE @ CHI
showdown"), nothing picked until the owner picks, grouped: what the PC
builds on its own first (`app._tourney_auto`, pc_worker's rules mirrored --
$500K+ showdowns, then the main slate named by its Millionaire), the rest,
then the classic slates it cannot build greyed out. The old button built
whatever the contest picker showed, which defaulted to the earliest start,
and the owner's Build meant for NE @ CHI queued the 14-game Thu-Mon classic
at the same 8:15 PM ET. A classic slate with no $1M+ contest cannot be
queued (the PC answered it "nothing to build" forever); a request can be
cancelled. **The Sunday main slate's board builds on its own only Saturday
6 pm-midnight MT and 85-40 minutes before lock** (after the inactives;
`pc_worker._CL_WEEK_WINDOWS`, `_CL_PREGAME_MIN`): never when the slate
posts, on a dropped player, a status move or an engine bump (8+ builds of
week 7's 43-minute board by Thursday) -- the owner: "no reason to have it
automatically run on like monday or tuesday". **Every board records who asked
for it** (`requested_by`: "PC request" for a Build from the tab, "Schedule"
with the reason), and the PC's building-now note says the same. **The main
slate is the NEAREST week's** (`dk.main_slate`, the one rule the PC and the
tab's "built automatically" entry read; 2026-10-28): the most games across
weeks made week 8's 11-game slate "main" on week 7's Saturday, so week 7's
Millionaire never rebuilt and the owner's Build went to next week's slate.
Eight games is the floor (`_MAIN_MIN_GAMES`; Thanksgiving week's main slate
has nine). **Every tournament build simulates the slate's OWN week**
(`nfl_game_sim.current_week(day=)` off its first kickoff's ET date): today's
week was simulated for any slate, and that weekend's replay of week 8's slate
dropped Allen, Jackson and Lawrence as "no projection this week". Tournament
boards and the Build queue outlive the day-old board sweep by a week
(`boardshare._KEEP_LONG`; it had deleted Thursday's Millionaire board by
Friday night). **A build the PC owed and did not land says why**: a queued
request's beside it (`queued_attempt`), the schedule's own (req_ts 0, the
Saturday-night Millionaire) on the slate's card (`last_attempt`), each in
the ledger as `TOURN-attempt` (showdowns `TOURN-attempt-sd`) -- read the
error-log snapshot before guessing why a board is missing. The builders say
why they returned nothing (`dfs_tourney.last_refusal`). A request lives until
its slate locks (`starts_ts`). pc_loop's git runs with prompts off (`GIT_TERMINAL_PROMPT=0`, `GCM_INTERACTIVE=never`) and a 300 s timeout
(2026-11-26): a repository that starts asking for credentials is a pull error on the light, never a loop hung
on a sign-in window. `vigil-pc.bat` is frozen on purpose (cmd reads .bat by byte offset,
so a self-pulling batch file corrupts itself); all logic lives in Python.

- **Resumable builds** (2026-11-26, `build_ckpt.py`; the owner closes `vigil-pc.bat` whenever he
  needs the PC). On the PC only (`VIGIL_CKPT_DIR`, set where the worker RUNS, inside pc-simcache;
  `"checkpoints": false` in vigil-pc.cfg switches it off; the server and the suite never have it).
  - **Worlds**: each simulated game is kept as it is played, under pool_cache's own key; the pool
    is filed in the worlds cache when the last game lands. Cached worlds are NOT extended in place
    (pool_cache's docstring: the anchor is re-measured per call, the deal and the floors are
    functions of the whole count).
  - **The build**: production boards are unseeded, so the generator's entropy is pinned in the
    checkpoint -- a restart redraws the same field and candidates from the same cached worlds. The
    best-lineup solves are kept whole; the scoring pass keeps each world range's running sums
    (written by the range's own worker every 60 s, resumed on the same chunk boundaries, so the
    sums are the uninterrupted run's bit for bit); the portfolio pass keeps each finished range of
    its make-it matrix (packed rows since 2026-11-27) when the old dense matrix would fit the budget.
  - **Safety**: every file name carries a sha256 of every input of its pass (matrices byte for
    byte, weights, grids, ranges, chunking, `dfs_tourney.py`'s source), so a changed input is a
    different file; every file is checked against its own sha256 and a damaged one is recomputed
    (`CKPT-integrity`). 6 builds / 8 GB / 8 days (`"checkpoint_gb"`); removed when the board lands
    (`pc_worker._ckpt_landed`). A board stamps what it resumed (`checkpoint`).
  - Not resumable: the field and candidate draws and the greedy portfolio pick (redone, minutes).
    A scheduled window that closed while the PC was off is not re-opened by a checkpoint.
  - **Frozen inputs** (2026-11-27; the deep probe deep-v1 resumed NOTHING: every restart re-fetched
    Sleeper, the deep profiles, the drive levels and DraftKings live, they had moved, and keys of the
    inputs named another build). A classic engine build on the worlds cache freezes every live read --
    the slate and contest details, the week, the worlds' inputs and their cache key (`player_pool(pin=)`,
    frozen before a game is played), the depth gate's answer, the roster state, every pool player's status
    class -- under a key of the REQUEST (counts, seed, model, pick, the code; `build_ckpt.request_key`).
    The owner's two operations, named so on every stamp (`checkpoint.inputs.operation`) and log: **Resume
    existing build** (frozen inputs younger than `INPUTS_MAX_AGE_S` 6 h -- the Saturday window is 6 h, the
    deep build ~2 h -- the slate not locked, not frozen before the inactives at lock - 90 min) and **Start
    fresh build** (anything else; the 85-40-minute pre-lock rebuild always, `fresh_inputs` from
    pc_worker). Frozen worlds no longer on disk (`nfl_dfs_sim.PinMiss`) restart the WHOLE build fresh
    (`CKPT-inputs`), never half old. Removed with the checkpoint when the board lands. X's -0.0 is made
    +0.0 where checkpoints are on (`_ckpt_zero`: the cache's int32 has no -0.0, and the keys hash bytes).
    Showdown is not frozen (20-minute builds; its pre-lock refreshes must be fresh). Guards
    `tests/guards_auto_resume_inputs.py` (RI, MP, PV); the probe is v2 (`deep-v2`: one build killed in
    the solves, scoring and the portfolio pass and resumed, then C on A1's frozen inputs and entropy;
    refused across code versions).
- **Build profiles** (2026-11-26, `pc_profiles.py`): vigil-pc.cfg `"profile"`: `standard` (the
  default: production's 20,000 generation + 160,000 ranking worlds, 40,000 final, a 300,000 field --
  with no key the worker's calls are unchanged), `deep` (50k / 500k / 160k), `maximum` (100k / 1M /
  500k, a 1M field), `research` (250k / 2M / 1M, a 3M field), `custom` (`profile_custom`). Also
  `"workers"`, `"cache_gb"`, `"cache_entries"`, `"gpu_vram_fraction"`, `"ram_gb"`.
  - A profile runs only when it can land: its time scaled from the last board's own stages must fit
    before lock (x1.25 + 15 min), its estimated memory must fit 80% of the PC's RAM, and the 85-40
    minute pre-lock rebuild is ALWAYS standard. Otherwise the next one down, then standard. Every
    classic board stamps `profile` (ran, requested, why).
  - **Memory, measured** (2026-11-26, `research/hp_memory.py`: the real classic build on a 546-player
    synthetic slate, a fresh process per point, resident memory by stage). The pool's Python lists were
    the wall (37.8 bytes a score; 1.24 of a 1.75 GB peak at 60,000 worlds; the first estimates read deep
    ~29 GB, maximum ~58). Now: the tournament and the evaluator take a COMPACT pool
    (`player_pool(compact=True)`: float32 rows, int32 hundredths while built, the disk cache's bytes
    unchanged; 0.14 GB and a 0.65 GB peak at the same point), a compact pool the PC's disk cache holds is
    not kept in memory too, `_cached` drops expired pools (it kept every pool a PC cycle built),
    `pool_cache.encode` goes row by row, the classic build lets the pool go once X holds it, and the
    make-it matrix is packed rows built range by range (below) and past `VIGIL_MAKEIT_RAM_GB` (a quarter of
    RAM, pc_worker) goes to files in `pc-simcache/scratch` (`memory_gb` still counts it dense: conservative). Every one leaves a board exactly as it was
    (`tests/guards_auto_memory.py`, MEM1-6). `memory_gb` follows what is held: deep ~14 GB and maximum
    ~13 GB fit a 32 GB PC; research (~28 GB, three copies of a 3M-lineup field matrix) does not -- that
    matrix is the next wall. They are research targets -- nothing has measured a gain past 160,000
    ranking worlds (`cl_worlds.md`). Exact at scale (`research/hp_memory_exact.py`, 20M scores: X
    byte-identical, every score, bucket, ranking, tie and field mass equal; a row holding -0.0 stays
    float64 while built, since int32 has none). **Measured on the PC**: every tournament build carries
    `memory` (memprobe: process and machine peaks) beside the estimate and posts a `build_memory`
    report to `/api/pc/bench`; `_task_profile_probe` runs ONE unattended deep probe
    (`research/profile_probe.py`: interrupted, resumed and fresh builds; resumed must equal fresh) only
    when its whole budget ends 3 h before any slate and outside the Saturday window, after the cycle's
    syncs, its process tree stopped at the cap.
  - Generation, ranking and final worlds stay disjoint (`world_split`, the evaluator's own pool).
- **The portfolio pass, exact and packed** (2026-11-27; `dfs_tourney.portfolio_vs_field`, both formats -- the
  showdown's `portfolio` is the same pass on its weighted universe; guards `tests/guards_auto_portfolio_speed.py`,
  PS1-PS6, against the old pass and cover FROZEN verbatim in that file). Every portfolio, its order and its
  coverage figures are the old pass's bit for bit, at any worker count, with or without the owner's caps,
  checkpointed or not. Three exact changes:
  - **The cover** (`greedy_cover`, `_CoverStep`): the old loop summed every candidate over every world at
    every step. Now each row's gain is screened off its nonzero worlds (`PackedRows.screen`), a row is
    re-screened only while its bound from an earlier step still reaches the leader (gains only fall: a lazy
    greedy), and the rows the float64 error bound cannot separate (|numpy's sum - any other order| <=
    4 (N + 2 parts + 8) u x the sum) are summed exactly as the old loop sums them, its first maximum taken. Measured
    10.8-32 s -> 0.1-0.3 s (~100x). Packed bit rows with a popcount were considered and NOT used: a tenth to a
    quarter of a top-1% row's nonzero entries are fractions, and float64 sums of ones and fractions depend
    on the order, so a popcount cannot reproduce numpy's gain; the stale-gain bound is tighter than a count.
  - **The matrix is packed rows** (`PackedRows`: per range, each candidate's nonzero worlds and float32
    values): 3.2% of the synthetic main slate's matrix is nonzero (94% exact 0 on the real 832,342-entry
    slate, section O), so 824 MB -> 53 MB at 60,000 worlds; the deep profile's 7.2 GB is a few hundred MB.
    Checkpoint files hold the packed rows (`"format": "packed-rows-v1"` in the pass signature).
  - **The per-world work** (`_make_it_world`): each contest's float32 value is constant from grid index
    i_sat on (top-1%: the mass above us past F_cut 1.1-1.3% on 100k-832k-entry contests, 3% on 5,000), so
    only the field's top matters: lineups at or above a score that a 1,024-lineup sample (weight-
    proportional on the showdown) puts at 3 x F_cut are bucketed, their buckets' weights and running sums are
    the old doubles exactly, and every lower candidate's index is >= i_sat once that sum passes F_cut plus a
    1e-9 margin; a region the sample left short is recounted over the whole field. The candidates' indices
    are read at their own buckets (the old pass searched 40,001). Measured (hp_memory's synthetic slate, a
    shared 4-core box under load): make-it 50-56 s -> 28 s (20,000 worlds, 100,000 field), 347 s -> 173 s
    and 428 s -> 170 s on 4 workers (60,000 worlds, 300,000 field), 163 s -> 86 s on a showdown-shaped
    weighted universe (15,000 worlds, 400,000 lineups); whole pass 1.6-2.7x. What is left is
    the field's score product (`Xc @ WfT`, unchanged so every bucket stays the old one) and a compare over
    the field a world. Not done: a GPU path for the make-it products (the owner: exact CPU first), and the
    same restricted per-world read in the SCORING pass (`G[iF][bj]` gathers 40,001 x 6 x contests a world
    there too) -- that edits `score_path_sig`'s functions, so the PC's GPU validation would wait for a
    new real-slate comparison.

## Hard-won gotchas

- Render is memory-constrained. Anything that caches per-process, or spawns a
  build child, gets measured before it ships. Two "exceeded memory" kills came
  from caches keyed on values that never repeat.
  A third took the instance down inside a minute (2026-09-05 08:56 ET): the
  deep season sim ran inline inside a web worker (Render's quota is one core,
  so the pool sized to one and skipped the child) beside a slate child. Heavy
  work on the server takes `deep_cache.HEAVY_BUILD`, runs in a child even at
  one worker, and is handed to the PC first whenever it is on. A fourth
  (11:26 ET the same day) was the combo maker simulating games inside a web
  worker beside a slate child; per-game sims now run in a niced child too
  (`baseball._game_sim_isolated`). Nothing CPU-bound belongs in a gunicorn
  process on a one-core quota. A fifth pattern (Sep-Oct 2026): ONE worker at
  1.0-1.77 GB, the peak growing 17.6 MB a day, 2,047/2,048 MB on 10-28 -- every
  app open called the crypto recorder's backtest, which read every quote saved
  every 90 s since August into the worker as dicts (~1.39 KB a row, measured).
  The owner had every crypto feature removed (2026-10-29) and its tables are
  dropped once (`store.drop_crypto_tables`, secure_delete OFF: Debian's SQLite
  zeroes freed pages through the WAL, 181 MB for a 1M-row copy; then a VACUUM,
  193 -> 13 MB on a copy, 0.29 s). A read that
  grows with a table belongs in SQL, never a fetchall. (The paper crypto terminal that came back on 2026-11-27 was
  built against exactly this: its own file, SQL sums, a summary file for pages, a row cap -- above.) Every worker now ledgers
  `MEM-worker` past 600 MB with its requests in flight and every thread's
  frames (in the row's `tb`): read the error-log snapshot first.
- The slate builds in a **fresh subprocess** every few minutes; anything cached
  only in memory is re-paid every time. Persist to the deep store instead.
- **DraftKings standings exports name every entrant: never commit one** (2026-11-26). Three September S7
  exports had been committed to this public repo; they were removed from the tree that day, their hashes
  kept in `research/data/dk_standings/manifest.json`, private copies read at the same (gitignored) paths.
  `scripts/secret-scan.sh` refuses a staged export by name or header and a guard fails on a tracked one.
  The history still holds them: `research/reports/standings_privacy.md` is the cleanup plan (make the repo
  private, or a filter-repo rewrite that breaks 63 artifacts' commit provenance and the PC's ff-only pull),
  awaiting the owner -- do not rewrite history without that approval.
- Never let in-game information touch a number that claims to be pre-game
  (entry prices, closing lines, "model vs market" comparisons). That mistake
  produced a fake +14.8c CLV and a fake +47% prop ROI.
- The user reads these numbers as a scoreboard. Honesty beats flattery: fees
  in, one-sided pools labelled, small samples flagged.
