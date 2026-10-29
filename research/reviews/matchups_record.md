# The Matchups record (and the PC prediction / calibration gap): design, review, build

The owner, 2026-10-29: "add a record to the match up tab for both nfl and NBA. I'd like it to
grade itself and tune itself based on results ... You and fable work on it." The owner's standing
rule: Fable and the orchestrator agree on anything built or discovered.

This file keeps the whole exchange: the design as reviewed (section 1), the Fable agent's design
review verbatim (section 2), what the build did with every finding (section 3), and the review of
the build (section 4).

## 1. Design v1 (orchestrator), as sent for review

# Matchups record that grades and tunes itself (NBA + NFL), and the PC prediction/calibration gap — design v1

Owner (2026-10-29): "add a record to the match up tab for both nfl and NBA. I'd like it to grade itself and tune
itself based on results." Repo: /home/user/kalshi-betting-helper. Branch HEAD 19cf29c plus uncommitted Part 1
(the DFS weekly-inputs stamp: nfl_dfs_sim.weekly_stamps / week_stamp, dfs_tourney.inputs_stamp, the matchup hook
wired after nfl_usage).

## 0. A prerequisite found while designing: the PC logs and calibrates in its own world

Facts, read in code:
- Six game boards log their predictions INSIDE the builder: `basket.board` (nba/wnba + shadow nba_deep),
  `hockey` (nhl + nhl_deep), `nfl_game_sim` board (nfl/nfl_pre + nfl_deep), `cfb_board` (cfb, cfb_ats/cfb_total?),
  `soccer` (soccer, soccer_dk, soccer_btts), `golf`. Tennis and UFC are harvested on the server from the
  adopted board (`predlog.harvest`). The pick records `nfl_track` / `cfb_track` are written on the server from
  the shared board (safe).
- The grading loop (`predlog.start` -> resolve_due) runs only in app.py. `pc_worker` never touches predlog or
  calibrate. So on the PC the predlog rows are written to the PC's own predlog.db and never graded.
- `calibrate._params(model)` fits from `predlog.pairs(model)` (graded rows only). On the PC that is empty, so
  every `calibrate.apply("nfl"|"cfb"|"nba"|"nhl", p)` in a PC-built board is the identity, and
  `calibrate.blend_weight(bucket)` (combo_engine's fitted model/market weight) is None -> the hand-set table.
- Since this morning's 19cf29c ("the server serves, the PC builds"), the server serves the PC's copy of these
  boards almost always while the PC is on. Consequences:
  (a) the site mostly shows UNCALIBRATED probabilities for NFL/CFB/NBA/NHL games, and the PC-built recipes
      (NFL/UFC/MLB presets) use the hand-set blend instead of the fitted one;
  (b) the server's predlog record for those sports accrues only when the server builds (the fallback), so their
      calibration and "vs market" records grow far more slowly.
  Before 19cf29c the same divergence existed for every board the server adopted from the PC instead of building,
  but the server built often (its copy aged past the TTL), so most tickers were logged there.

Proposed fix (generic, no per-sport code):
- A1. Calibration params are fitted ONCE, in the background owner every 6 h (not per web worker), and written to
  a small JSON beside predlog.db (`calib_params.json`: {model: [t, q0, b, n]}, {bucket: [w, n] | null} for
  blend weights, the matchup tuning of section 3, `ts`). `calibrate._params` / `blend_weight` read the file when
  it exists and is younger than 12 h (else fit as today). Side benefit: three workers stop refitting every model
  every 6 h each.
- A2. `GET /api/calibration/params` (token door) serves that file. The PC fetches it at the start of every boards
  pass and cycle, keeps the last good copy on disk, and installs it (`calibrate.install(params)`), so a PC build
  is calibrated exactly as a server build. Fetch failure: the last good copy if < 24 h old, else identity, and
  the boards' existing stamps say "calibration: none".
- A3. `POST /api/predlog/upload` (token door): the PC sends its predlog `predictions` and `shadow` rows logged
  since its watermark (gzip JSON, capped per call), the server INSERT OR IGNOREs them (first write wins, the
  PC's own `ts`), and the PC advances the watermark only on success. Same pre-game discipline as today: rows are
  only ever logged pre-game by the builders, and the server's resolver grades them like its own.

## 1. What the Matchups record logs

Source: the published Matchups INDEX (matchups.index_name(sport, key) in boardshare) for today and tomorrow
(MT). It is the same object on the server whoever built it (server child or PC pc_board), so logging happens on
the SERVER from the published index, never inside the builder: no PC gap. A harvester in the background owner
reads the index every 10 min, but only when its file mtime changed (boardshare.held-style), so it costs one
unpickle per new build.

Which lines: every line with a Kalshi market block (ticker) whose engine p or market reference is in
[0.05, 0.95], plus every line carrying a label (high_probability, positive_ev, robust_positive_ev). Measured
volume to confirm on a real index; rough guess 1-3k lines per NBA night, 2-5k per NFL week.

New SQLite file `mx_record.db` (beside predlog.db, WAL), one table:

  mx_lines(ticker PK, sport, stat, k, player_id, player, team, game_id, event_ticker, game_ts,
           -- first pre-game sighting (immutable once written)
           f_ts, f_p, f_p_lo, f_p_hi, f_p_tuned, f_mkt, f_yes_ask, f_no_ask, f_best_side, f_labels, f_ev_c,
           -- last pre-game sighting (updated while now < game_ts)
           c_ts, c_p, c_p_tuned, c_mkt, c_yes_ask, c_no_ask, c_best_side, c_labels, c_ev_c,
           -- grading
           graded INT DEFAULT 0, outcome INT, settle_value REAL, resolved_ts,
           -- per-row contributions computed AT GRADE TIME so the record is SQL sums, never a fetchall
           ll_f_eng, ll_f_tuned, ll_f_mkt, br_f_eng, br_f_mkt, ll_c_eng, ll_c_tuned, ll_c_mkt,
           pnl_f_c, cost_f_c, pnl_c_c, cost_c_c)

  - `f_mkt` / `c_mkt`: prop_value.kalshi_reference(market, "mid") de-vigged YES probability when the book is
    believable (prop_value.book_is_believable), else NULL (no market benchmark for that row).
  - `pnl/cost`: only for lines labelled positive_ev at that sighting: buy `best_side` at its ask plus the fee
    (prop_value.ev_buy's all-in cost), pnl = 100 - cost on a win, -cost on a loss, per contract.
  - `game_ts`: the game's start from the index's `games` (matchups.game_started reads the same).

## 2. Grading

Kalshi settles each prop market; a player who never plays settles SCALAR (fair price): graded=3, no outcome,
excluded from every metric (the engines' probabilities are conditional on the player entering, CLAUDE.md).

Grader in the background owner every 15 min: ungraded rows whose game started more than 4 h (NBA) / 5 h (NFL)
ago, grouped by event ticker (the first two dash segments of the market ticker; verify on real tickers), ONE
`GET /markets?event_ticker=E&limit=1000` per event, paced at <= 1 request/s, at most 40 events a pass. result
yes/no -> graded=1 + outcome + the per-row contributions; scalar -> graded=3; still open 7 days after game ->
graded=2 (dead). Separate from predlog.resolve_due on purpose: thousands of prop tickers in `predictions`
would starve the game records' 150-a-pass budget and multiply single-ticker Kalshi calls (we are already
rate-limited: 13 429s on the NFL index last week).

## 3. Tuning ("tunes itself")

Fitted in a niced child (deep_cache.run_child) every 6 h by the background owner, per sport x stat, on graded
CLOSE sightings (the last pre-game forecast, closest to when a bet is placed); first sightings are reported but
not fitted. Candidates:
  identity            q = p
  platt               q = s(a + b*logit p)
  blend (book rows)   q = s(a + b*logit p + c*logit m), m the believable book's de-vigged mid; rows without a
                      believable book use that stat's platt (or identity)
Selection: forward-chaining by date (fit on dates before D, score on D, for every D after the first 10 dates);
per-date paired log-loss difference vs identity; a candidate is adopted only if its mean gain > 0.001 nats/row
AND > 2 clustered SE (by date). Then refit on all rows and damp toward identity by min(1, days/30) (calibrate's
day floor). Floors: >= 200 graded rows and >= 10 dates before anything but identity is considered. Output into
calib_params.json under "mx" (section 0): {sport: {stat: {family, a, b, c, n, days, gain, se, fitted_ts}}}.

Applied at BUILD time in matchups.produce (server child and PC alike, the PC with the params it fetched, A2):
line["p_raw"] = engine p; p, p_lo, p_hi mapped (monotone in p for fixed m, so the band stays ordered); value and
labels recomputed with prop_value.line_value / line_labels exactly as the engines do; the payload stamps
`tuning: {stat: family, n, gain}`. The static market_better flag (research coefs) stays as the prior.

## 4. The record on the tab

`GET /api/matchups/record?sport=nba|nfl`: SQL aggregates only (GROUP BY stat; calibration bins by
CAST(c_p*10)), per stat and pooled:
- graded n, voids, dates; mean predicted vs hit rate (engine, tuned, market) at first and close;
- log loss and Brier: engine vs tuned vs Kalshi on the SAME rows (only rows with a believable book for the
  market column); "who forecasts better" with a paired SE;
- the tab's picks: positive_ev lines bought at the ask, fees in (count, hit rate, ROI +/- SE); high-probability
  lines (best side >= 70%): count, hit rate vs predicted;
- the tuning in effect per stat (family, n, gain +/- se) and when it was fitted;
- small-sample flags (n < 100 or < 10 dates).
UI: a "Record" panel on the Matchups tab (static/matchups.js), sport-switched, one row per stat plus pooled.

## 5. Load (the owner's 1.6 GB complaint)

Harvest: one index unpickle per new build (NBA index a few MB), in the owner worker. Grade: <= 40 HTTP calls
per 15 min. Record route: SQL aggregates, no row fetch. Tuning fit: in a child, rows loaded per stat there only.
calib params: fitted once in the owner (or that same child) instead of per worker.

## Open questions for review
Q1. Fit on close sightings (proposed) or first sightings?
Q2. Include the market term (blend), given Kalshi's tight NBA books beat the engine (0.542 vs 0.556 on
    2025-26)? It will pull the tab toward Kalshi and shrink EV; honest, but is that what the owner wants from
    "tune itself"?
Q3. Line selection for logging, [0.05, 0.95] plus labelled lines?
Q4. A1-A3: any risk in the PC installing the server's params and uploading its predlog rows?
Q5. The gate: forward-chaining by date, 2 SE clustered by date, 0.001 nats, damping by days/30, floors 200/10.
Q6. Anything that would make this record dishonest (leakage of in-game information, survivorship from lines
    dropped at game start, the scalar voids, two sightings of one forecast)?

## 2. Design review (Fable agent), verbatim

## Review of design_v1 (Matchups record that grades and tunes itself) — Fable, 2026-10-29

**Verdict: AGREE WITH CHANGES** — Section 0 is correct and worth fixing first; the record and grader are the right shape; three changes before building.

### Section 0, checked in code

Confirmed: `predlog.start` only at app.py:175; pc_worker/pc_loop reference neither predlog nor calibrate; `predlog._DB` is `PREDLOG_DB` (Render: `/data/predlog.db`) else `<repo>/predlog.db`, and the builders call `predlog.init_db()` themselves, so the PC writes its own file that nothing grades. `calibrate._params` fits `_predlog_pairs(model)` and an empty record is `(1.0, 0.5, 0.0, 0)` — identity at basket:639, cfb_board:311, hockey:542, nfl_game_sim:1170, **and pro_season_job:136/183** (the NBA/NHL season boards, PC-first too — add them to the list). `combo_engine._effective_tau` falls to `_MODEL_TRUST` when `blend_weight` is None. Models logged inside builders: `nba`/`wnba` (+shadow `nba_deep`), `nhl` (+`nhl_deep`), `nfl`/`nfl_pre` (+`nfl_deep`), `cfb`, `cfb_ats`, `cfb_total`, `soccer`, `soccer_btts` (+`soccer_dk`), `golf`. Two facts that matter for the fix: every builder logs the **raw** p (`raw_ph`, cfb's `raw`), and every game card already carries `p_home_raw` plus its quotes and close (basket:682, hockey:586, nfl_game_sim:1235, cfb_board:357).

### Findings

**MAJOR 1 — replace A3 with a server-side harvest.** The cards carry what `log_many` needs, so the server can log the six boards from the adopted payload exactly as tennis/UFC are logged today (`predlog._HARVESTERS`) and as Section 1 proposes for matchups. One mechanism for every board, no new write door into the only record (an upload door needs a model whitelist, ticker regex, `0<p<1`, pre-game `ts`, size caps), no PC watermark state. Harvest the raw p only; skip games already started at harvest time (the boards sync lands 5-10 min after a build). Keep A3 only for a board whose payload lacks a logged field — check the deep shadow p, soccer's three buckets and golf before choosing. A1/A2 stand; note `matchups.build_isolated` and the soccer refit are subprocesses, so the PC must write the fetched params to disk where `_params` reads them, not only `install()` them in memory.

**MAJOR 2 — Q2: do not make the blend the headline.** A blend fitted against Kalshi's mid and then priced against the same mid gives EV ≈ 0 by construction; the tab would go quiet not because it learned anything but tautologically. Apply the engine-only Platt per stat as the tuned p; fit and *report* the blend; and let the record's per-stat engine-vs-market verdict (paired, clustered) drive `mark_record`'s `market_better` once past the floors, the static research file staying the prior. That is "tunes itself" through the mechanism that already exists (matchups.py:190-215). Split the picks record into muted and un-muted +EV lines.

**MAJOR 3 — Q3/Q5: the fit's n is not the row count.** A player-stat ladder is one forecast at several thresholds (for engine and market alike). Log every marketed rung (the record needs them), but weight each row 1/(rungs of its player-stat) in the fit and the floors (effective n ≥ 300), cluster SE by game rather than date (10 date clusters give an unstable SE), and require the blend to beat Platt, not identity, by the same gate. Forward chaining and days/30 damping are right.

**MINOR**
- `mx_record.db` must live beside `PREDLOG_DB` (`/data`), never the module dir: the container FS is wiped each deploy (Dockerfile:20).
- The label is `positive_ev_robust` (prop_value.LABELS:664), not `robust_positive_ev`.
- The published index keeps started games (`drop_started` runs on serve, matchups.py:564); the harvester must apply `game_started` to the **first** sighting too, using `market.ts`/`built_ts` as the sighting time, skip `placeholder` indexes, and keep `value.quote_age_s` > 600 rows out of pnl.
- Add `c_tuning_rev` (or family) per row: compare tuned vs engine only where a non-identity tuning was in force at the sighting, else the tuned column is a hindsight fit scored in-sample.
- Nothing in the repo uses `/markets?event_ticker=` yet; verify on one paced call that settled markets return `result`/`settlement_value` without a `status` filter, and reuse `predlog.resolve_due`'s rule (result decides; status without result is dead only past close). Use `kalshi._get_json` for its 429 backoff.
- `market_for_line(..., protected=False)`: assert non-PROT series in the harvester; PROT scalars are refunds, not DNPs.
- Volume: kalshi_nba_props counted ~1,800 standard markets open on 2026-10-23, so ~300k rows a season; keep labels as a bitmask and contributions as REAL (~150 MB).
- The index tail is `_TAIL` 0.02 and marketed lines are kept regardless, so [0.05, 0.95] is a logging rule, not the index's.

### Q1-Q6
**Q1** Fit on close (the forecast a bet is made on, most information); report both. Store `c_ts` and restrict "close" metrics to `game_ts - c_ts <= 2 h` — with the PC off, the last build is whenever someone last visited. **Q2** See MAJOR 2. **Q3** Marketed lines only (ungradable otherwise) plus labelled; see MAJOR 3 for the fit. **Q4** A2 is safe: params are fitted on raw p and applied to raw p; stamp the fitting commit and refuse a different calibrate schema. A3: prefer the harvest (MAJOR 1). **Q5** See MAJOR 3; otherwise agreed. **Q6** Covered by the MINORs: pre-game first sighting, the era-mixed tuned column, stale quotes in pnl, PROT scalars; voids excluded and counted is right — also report the void rate for labelled vs unlabelled lines, since injury-prone players concentrate in both. First and close as columns of one row is not double counting.

## 3. What the build did with each finding (orchestrator)

- **MAJOR 1 (no new write door; the server logs PC builds).** Built as the builders CARRYING the
  exact rows they log beside their board (`predlog.carry` -> boards store `plcarry_<key>`), shipped
  by the existing boards sync, logged on the server by `predlog._harvest_carried` -- one of
  `predlog._HARVESTERS`, beside tennis and UFC. No upload door was added: the rows travel as a
  board does, and the server re-validates each one (a Kalshi ticker by regex, 0 < p < 1, the
  event start KNOWN and still ahead -- so a carry that lands after kickoff logs nothing), main
  models to `predictions`, shadows (`nba_deep`, `nhl_deep`, `nfl_deep`, `soccer_dk`) to `shadow`,
  first write wins as everywhere in predlog. Carrying the rows rather than re-deriving them from
  each sport's card keeps ONE function instead of six per-sport parsers and logs exactly what the
  builder logged (the raw p, the deep shadow p, the de-vigged price at build time, the start).
  Golf carries nothing: no start time on its rows and no registered bucket. The calibration side
  (A1/A2) is as reviewed: the background owner fits once into a file beside predlog.db
  (`calibrate.write_params`, in a niced child under the heavy-build gate, every 6 h); the PC
  fetches it before each cycle and boards pass and WRITES it where `calibrate.file_params` reads
  it, so the matchups child and the soccer refit see it; `pro_season_job` reads the same
  `calibrate.apply`, so the season boards are covered by the same file.
- **MAJOR 2 (the blend is not the headline).** The tuned figure is the engine-only Platt per sport
  x stat; the blend with Kalshi's mid is fitted and reported (`blend_report`, `applied: False`).
  A stat's live engine-vs-Kalshi verdict, once decided past its floors (10 dates AND 30 games,
  2 SE clustered by game), replaces the static research figure in `matchups.market_record` --
  the "market better" mute lifts or holds by the tab's own record. The picks record is split into
  shown and muted (+EV lines carrying `market_better`).
- **MAJOR 3 (n is not the row count).** Every marketed rung is logged; the fit, the floors (300
  effective ladders) and the record's `n_eff` weight each rung 1/(rungs of its player-stat); SEs
  are clustered by game; the blend must beat the Platt, not the identity, to be reported as a gain.
- **MINORs.** `mx_record.db` sits beside predlog.db (`/data` on Render). The label name is
  `positive_ev_robust`. The first AND the close sighting must be pre-game, a sighting's time
  being the later of its quote's and its build's, a placeholder or empty index is skipped, and a
  quote older than 600 s earns no P&L. Every row keeps the tuning family in force at its sighting
  (`f_fam` / `c_fam`), and the tuned-vs-engine comparison reads only rows whose tuning was not the
  identity. Grading is one `/markets?event_ticker=` call per event through `kalshi._get_json`
  (its 429 backoff), verified on KXNBAPTS-26OCT28LALLAC before building (41 markets in one call:
  12 yes, 25 no, Doncic's four rungs scalar at 0.82 / 0.66 / 0.43 / 0.28); result decides, only
  an explicit `scalar` is a void, seven days unanswered is dead. PROT series are never recorded.
  Labels are a bitmask and the per-row scores REAL. The 5-95% band is the logging rule (plus every
  labelled line). The void rate is reported for labelled and unlabelled lines apart.

Found while building, beyond the review:
- the side a pick is graded on is the side its LABELS were read for (`mx_record.label_side`):
  NFL labels read the strong-fit side when there is one (`nfl_matchup.line_labels`), so grading
  every pick on `best_side` graded an NFL fit-side NO on YES;
- an event tried and not yet settled waits `RETRY_S` (1,800 s) before it is asked again, and a
  fetch that fails still lets its rows go dead after seven days, so the oldest-first queue cannot
  stall;
- a harvest whose write fails marks nothing seen, so the next pass records that file version;
- equal (outcome, figure) rows are summed into one weighted row before the Newton fit (exact; the
  engines round p to four places): 96,000 synthetic rungs fit in ~11 s a stat in pure Python;
- carrying the start on NBA / NHL / soccer predlog rows makes `predlog.snapshot_closes` stop their
  closing snapshots at the start instead of midnight ET of the ticker's day (NFL and college already
  did).

Guards: `tests/guards_records.py` (the RC section of the suite). Every guard was checked against
a mutant that re-introduces the bug it names; the first pass caught 22 of 24 and the two survivors
(the 2-SE adoption rule, the verdict's floors on zero-variance test data) got guards that catch
them.

## 4. Review of the build (Fable agent), verdict AGREE WITH CHANGES, summarised

Every design change was confirmed in the build. The reviewer accepted the carried rows over its
literal harvest-from-board ("the boards' door, raw p, first write wins, no PC bookkeeping"). It
verified `tests/guards_records.py`, `e2e.py` and `node --check`, both build paths (tune in
`produce`, mark in `publish`), NFL `start_ts`, and the `X-Sim-Token` door for the PC's fetch.
Its answers: (a) no post-start figure can enter a pre-game row (residuals: findings 3 and 4);
(b) the gate is sound but weakest right after the floor and non-sticky (finding 1); (c) no
label is dropped or doubled; (d) no half-written reads, and the closing-snapshot change (#8) is
safe; (e) the aggregates are right; (f) finding 2 is the one misleading element.

Findings, each with what the build did (round 2):

1. **MAJOR: the gate right after the day floor scored one day's games.** Measured on a calibrated
   engine (200 seeds), false adoptions ran 6.5% per look at 11 days x 4 games, 5.0% at 12 and
   1.0% at 14, with a look every 6 h.
   FIXED: `_forward` returns nothing under `MIN_SCORED_GAMES` (30) scored games. The decision is
   sticky: `compute_params` passes the last file's block to `fit_all(prev=)`; a tuned stat keeps
   its Platt while gain > 0 and > 1 SE, and an untuned one needs MIN_GAIN and 2 SE.
2. **MAJOR: the tab's bold verdict skipped the floors the mute obeys.**
   FIXED: one rule, `_decide`, read by `verdict()` and by `record()`'s `vs_market.decided`. The
   tab bolds only a decided call and otherwise reads "not decided yet (N games, D dates)".
3. **MINOR: the sighting's clock was the builder's.**
   FIXED: a sighting's moment is the max of the quote, the build and the file's mtime (the
   server's clock, kept as the PC's build age by X-Art-Age).
4. **MINOR: a stale pre-game quote still fed the market column.**
   FIXED: no Kalshi figure from a quote older than 600 s.
5. **MINOR: the reason at exactly 10 days.**
   FIXED: `days <= FLOOR_DAYS` is "below the floors", and a chain with nothing scored says
   "nothing scored out of sample yet".
6. **MINOR: `calibrate`.**
   FIXED: a model the file does not list fits itself as before, and a failed `os.replace` leaves
   no tmp file.
7. **MINOR: a 429 storm in `grade_due`.**
   FIXED: the first refused fetch ends the pass; the untried events lead the next one.
8. **MINOR: "said" read the engine's figure once a Platt is live.**
   FIXED: the high-probability picks read `COALESCE(c_pt, c_p)`, the figure the label was awarded
   on. The calibration panel says it shows the engine's own figure.
9. **Nits**, all FIXED:
   - `GRADE_S` is gone.
   - Ladders are keyed by `player_id` (the name only without one).
   - Dates are Mountain-time sports days (`_DAY` = game_ts - 7 h). UTC dates split an NBA night
     in two and doubled the date floors.
   - The pooled "*" query is built from parts.

Guards for every finding are in `tests/guards_records.py`. Each was checked against a mutant
re-introducing it: 12 of 12 caught. Measured beside the review: one harvest of a full NBA index
(8,100 lines, 2.37 MB pickled) takes 0.21 s and retains +5 MB of RSS in the worker; an unchanged
file costs nothing.

## 5. Round 2 (Fable agent): AGREE

All nine findings are fixed as asked. The reviewer read each hunk, ran `tests/guards_records.py`
(42/42) and `node --check`, and re-ran its gate experiment against the new code:

- False adoptions per look on a calibrated engine: 0/100 at 11, 14 and 20 days (they were 6.5%,
  1.0% and 0.5%).
- Power kept: the slope-0.6 miscalibration is adopted 28/30 at 20 days x 4 games.
- Stickiness: a borderline draw (1.11 SE) is identity without a previous Platt and kept with one.
  A false Platt that got in survives 7/100 looks on a calibrated engine, so it dies within a day
  of refits.
- The reasons read right at 10 and 11 days.

One note, not a finding: `_DAY`'s fixed 7-hour shift is MST. In MDT only a tip between midnight
and 1 am would fall on the previous sports day, which no NBA or NFL game does.

