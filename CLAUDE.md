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
  `errors/mem-latest.json`.
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
  tail inflation does not arise. Its scoring pass scores all ~780k legal
  lineups where only the ~26k we may enter are listed: scoring those alone
  against the full field is ~3.7x faster (the `opt_worlds` column, best of ALL
  lineups, would need its own pass) -- not shipped yet.
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
- `artifacts.py` — the schema-gated contract for the three compute stores
  (gamesim / boards / deep). Bump `SCHEMA` with any pickle-shape change.

## The PC offload

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
ignored**. `vigil-pc.bat` is frozen on purpose (cmd reads .bat by byte offset,
so a self-pulling batch file corrupts itself); all logic lives in Python.

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
  process on a one-core quota.
- The slate builds in a **fresh subprocess** every few minutes; anything cached
  only in memory is re-paid every time. Persist to the deep store instead.
- Never let in-game information touch a number that claims to be pre-game
  (entry prices, closing lines, "model vs market" comparisons). That mistake
  produced a fake +14.8c CLV and a fake +47% prop ROI.
- The user reads these numbers as a scoreboard. Honesty beats flattery: fees
  in, one-sided pools labelled, small samples flagged.
