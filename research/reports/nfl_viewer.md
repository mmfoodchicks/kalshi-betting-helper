# The NFL game viewer (2026-10-29)

The owner, after Toad Sports' TikTok sims (one simulated game drawn play by play: a field with 22 dots in
stock formations, the ball carrier labelled, down / distance / spot, play text with run direction, a running
box score, TOUCHDOWN and KICKOFF banners):

> "One thing I think would be cool is being able to view the most average play fit every down for every
> game. Like this, just being able to view play by play."
>
> "Like on the model screen be able to click or tap, maybe we can be cool and simulate where the defense
> players are missed tackles, Interceptions,"
>
> "Yea leave out missed tackles. But go with everything else."

Display only. It changes no number on any board, with one exception that is a bug fix (section 6.1).

The code is `nfl_viewer.py` (selection, naming, text, the piece, the builds, the route's reader) and
`static/nflview.js` (the screen). The engine records its events through `nfl_deep_sim.play_game(log=)`.
The guards are `tests/guards_viewer.py` (section NV of the suite).

## 1. Design, review and what changed

The orchestrator wrote design v1 and a Fable agent reviewed it before any code. The verdict was **AGREE
WITH CHANGES**: three MAJOR, nine MINOR, six NIT. Each item and what the build did with it:

| Review item | What it asked | Built as |
|---|---|---|
| M1 | The typical game's winner must not be decided by the distance definition. Fable measured the unconstrained medoid choosing the favourite's LOSS once in eight matchups. | `choose`: the candidates are the games whose margin has the sign of the MEDIAN margin, with ties excluded. A median of 0 is a pick'em, and either side may win. The header shows the BOARD's deep probability and expected score, never a rate of the viewer's own games. |
| M2 | Carry the log in the state dict, never through signatures. Guard on `is not None`, not on truthiness. | `S["log"]`. No internal signature changes. Every hook is `if S["log"] is not None`. The guard plays 200 seeds x 3 matchups x 5 modes with and without the log. |
| M3 | Name the defence from Sleeper's weekly IDP projections, not from a season share table. | `rosters()` reads offence, IDP and kicker rows for the week in one call each. The data-job table step was dropped. |
| m1 | Every word must come from a logged field. | Kickoffs and punts are told as spots, blocks and return touchdowns. A penalty is "no play" plus its yards. The "Not modelled" tab lists the rest. |
| m2 | Pass length must be conditioned on the yards gained. | `_DEEP_CMP` (measured, section 3). |
| m3 | One coordinate list, pinned, with a non-tautological guard. | `DIMS`. The guard checks a brute-force argmin and a second process. |
| m4 | Mark what is drawn by share and what is pre-game. | The section is tagged "names by share", and the header reads "Simulated before kickoff · built (MT)". After kickoff the piece is marked as the pre-game simulation, and it is refused after kickoff + 4 h. |
| m5 | A provenance stamp. | `provenance`: the profiles' source and time, the roster factors, the tables' and engine's sha256, the kickoff era, the seed schemes. |
| m6 | Log the drawn yards AND the resulting spot. Name a tackler only on an in-bounds, non-scoring play. | Done. The text says "Ball at the 1" when a loss held at the goal line. |
| m7 | The owner's literal sentence: each down's typical play. | `_Downs` over all 5,000 games: the "By down" tab. |
| m8 | Lifetime and retries. | Pieces sit in `boardshare._KEEP_LONG`. They are rebuilt when 12 h old and kept to kickoff + 4 h. A missing piece of an upcoming game is built on request in a niced child. |
| m9 | A stock formation keyed by down and the shotgun flag. | 11 personnel against a 4-2-5 nickel. The shotgun is drawn by the team's measured rate. The formation is labelled illustrative. |
| n1-n6 | The two-minute warning, the OT rules, `is not None`, the footnote, the name scheme, the first event being a kickoff. | All done. The guard checks that the first event is a kickoff and the last is the final. |
| Open question | Pass breakups. | No. A defender is named only on an event the engine made certain. |

## 2. The typical game

- **Simulations.** N = 5,000 games per matchup. Each game gets its own generator, seeded with
  `crc32("season|week|home|away|i")`.
- **Profiles.** The board's own profiles, from `nfl_game_sim._deep_profiles`.
- **Centre and scale.** The centre is the medians of `DIMS`. Each coordinate is scaled by its population SD.
- **Choice.** The pick is the nearest candidate by squared scaled distance. Ties keep the lower index.
- **Pick'em.** When the median margin is 0, a game decided by three points or fewer comes first. On week 8's
  LV @ NYJ, distance alone picked a 7-point LV win under a header reading NYJ 53.6% (Fable m7).
- **When the shown game's winner is not the board's favourite.** This can happen in a pick'em or in a random
  sim. The screen then says both in one line: "In this game LV wins; the board's deep engine has NYJ at
  53.6%". It never shows a rate of the viewer's own games.
- **Replay.** The chosen game is replayed with the log on, and its coordinates must equal the selection
  run's.
- **The other ten games.** These are the first seeds, excluding the typical one.

On week 8's CAR @ GB (5,000 games, 8.8 s; the week averaged 9.6 s, 8.8-11.6):

- **The typical game.** GB won 27-24 (margin 3, total 51) against medians of 5 and 45.
- **Distance from the centre.** Every coordinate was within 0.46 SD. The week's worst was 0.57, on ATL @ TB.
- **The winner.** On all 13 of the week's other games the typical game's winner was the board's side.
- **The board's figure.** GB 66.3%, expected 25.8-20.1.

## 3. Flavour, measured

All figures are from the nflverse 2025 regular season. The 2026 file through week 5 agreed within a
couple of points everywhere.

| Measure | Value |
|---|---|
| Completions called "deep" (air yards 15+), by yards gained | 0 of 8,468 under 15 yards; 27.7% at 15-19; 58.0% at 20-29; 75.9% at 30+ |
| Incompletions called deep | 29.9% |
| Interceptions called deep | 40.0% |
| Designed-run location | middle 26.9%; left end 11.2%, left tackle 12.1%, left guard 13.2%; right guard 13.4%, right tackle 12.8%, right end 10.4% |
| Pass side | left 38.3%, middle 20.8%, right 40.9% |
| Shotgun | 81.8% of dropbacks, 41.8% of designed runs |

Each team's own 2026 mix comes from the matchup store and is shrunk toward these by 60 plays.

## 4. Names

The source is Sleeper's projections for the week: offence, IDP and kickers.

- **Exclusions.**
  - Nobody tagged out-type (`nfl_game_sim._AVAIL_OUT`) is drawn.
  - Nobody the slate holds in doubt (`nfl_game_sim.availability`: "Questionable after missing his last
    game", the rule that takes a quarterback's legs off the board) is drawn.
  - Nobody under 2% of his team's share of a role is drawn. The exception is the quarterback and the kicker:
    one man each, the most projected one left.
- **Missing roles.** A club with no name for a role says so in words ("GB: no tackler"). When a club has no
  quarterback, ball carrier, receiver, tackler or kicker, it is also ledgered as NFLV-roles: a Sleeper key
  renamed would otherwise show only in the footnote.
- **Offence.**
  - The starter throws.
  - A carry goes by projected carries, and a rushing touchdown by projected rushing touchdowns.
  - A catch goes by projected receptions, and a receiving touchdown by projected receiving touchdowns.
  - A miss goes by projected targets less receptions.
- **Defence.** A defender is named only on:
  - the solo tackle that ends an in-bounds, non-scoring run or catch (or a safety);
  - a sack;
  - an interception;
  - a lost fumble (forced fumbles and recoveries);
  - a return touchdown (kick and punt returners).
- **Kicks.** The kicker for field goals and extra points. Kickoffs and punts are told by team.
- **Draws.** Every draw comes from `random.Random(seed ^ 0x5EEDF00D)`. The guards check that the credits
  add up to the engine's box in every game: 360 games, every counter.

## 5. Where it runs

- **On the PC.** The boards pass builds `pc_build`: every upcoming game of the week whose piece is missing
  or 12 h old.
  - It runs in a niced child (`deep_cache.run_child`: the result on a private fd, the child's stderr in the
    error), starting no new game past 150 s. The rest go to the next pass.
  - Each game takes 9.6 s, at 5,000 games.
- **Sunday's inactives.** A piece built before kickoff - 85 min is rebuilt once kickoff is within 75 min, so
  the early games are not told with a roster hours old.
- **On the server.** The route (`/api/nfl/viewer`) only reads. A missing piece of an upcoming game is
  built on request in a niced child under `deep_cache.HEAVY_BUILD`, with 202 meanwhile.
  - One server build runs at a time across every gunicorn worker (the `nflview_build` claim): three taps on
    three games used to start three 60 MB children. A second request answers "A simulation is running; this
    game follows".
  - The claim is taken only once `HEAVY_BUILD` is held, and held through the child. Taken before that wait,
    a wait of over a minute behind another heavy build plus the 240 s child outlived the claim's 300 s
    takeover, and a second worker could start a second child (Fable, round 2).
  - After four minutes of 202s, the page says the build may have failed instead of "simulating" forever.
  - A stale piece is served while its rebuild runs. While the PC is on, the server waits the PC's two
    passes and the upload slack past 12 h, and lets the PC take the inactives window first.
  - Only this week's and next week's games are built.
- **Kickoff.** No game is built within ten minutes of kickoff. A build that runs past kickoff publishes
  nothing.
- **Piece size.** 400-433 KB pickled. `build_week` refuses one over PIECE_MAX (600 KB), ledgered as
  NFLV-size. The route sends one game, 36-39 KB.
- **Memory.** Measured on a one-game server build in its child: 59.5 MB peak and 38.6 s, including building
  the deep profiles cold from the play store. The web worker that waits on it holds nothing of the build.

## 6. What the viewer found in the engine

### 6.1 Fixed here: a sack could carry the ball past the goal line

The engine's sack yards are EFFECTIVE yards: the next snap's spot, with penalties enforced
(`research/nfl_deep_fit.py`). About one sack in twelve in the table nets a gain (382 of 4,704).

The engine checked the goal line on runs and catches but not on sacks. A gain on a sack could leave the
ball past the goal line with no score. The next snap came from "1st and -16", and nearly any play scored.

The viewer's legality check caught it. Measured on week 8's fourteen matchups (56,000 paired games here;
Fable's independent check, 28,000 games and 8,000 seeds a matchup for the win rates):

- **How often.** 3.7-3.8% of games reached that state.
- **The fix.** The gain stops at the 1, as this engine's pre-snap penalty does (`max(1, ...)`). A real
  penalty is half the distance. That matters only once the fourth-down refit (6.2) leaves the genuine
  penalty sacks; the rule is revisited then (Fable).
- **Its effect.**
  - 1.8% of final scores changed.
  - Total points moved -0.056 a game.
  - Home-win rates moved within +/-0.3 points at 8,000 seeds (0.6 at 2,000). That is inside the 600-game
    board figure's own +/-2.
- **Draws.** No draw was added.
- **Guard.** It plays 150 games with every sack forced to +30 and finds no snap at or past the goal line.
- **Notes.** The figures in `nfl_deep_validation.md` and `nfl_dfs_engine.md` predate the clamp; each now
  carries a note saying so.
- **The screen.** Until 6.2 ships, every sack that nets yards is marked "artifact" on the play card, in the
  play list and in the footnote. It is 0.25 a game: one typical game in four, and the first matchup the
  owner would open (Fable's build review, M1).

### 6.2 Reported, NOT fixed here: fourth-down turnovers read in the other team's frame

The fit reads a play's effective yards as `ytg - end.yardsToEndzone` whenever the play is not flagged a
turnover or a touchdown. ESPN does not flag a turnover on downs as a turnover. Its end state is the NEXT
team's: first and ten, and its own yards to go.

Every failed fourth down was therefore stored with the new offence's yards to go read as the old
offence's. Fable reproduced it to the row in 2026's 107 games:

- **How many.** 127 failed fourth downs, every one at EDIST 10: 58 incompletions, 40 rushes, 15 sacks and 14
  completions. The 15 sacks are 15 of the season's 489.
- **The proposed spot.** `100 - EYTG` recovers the raw yardage in 126 of the 127. The one miss is a penalty
  on the play, where the effective spot is right anyway.
- **Conversions.** 10 of the 40 failed fourth-down rushes are stored as CONVERSIONS (own territory: fourth
  and 1 at the own 21, no gain, stored as +58).

Examples:

- **Own territory.** Sacked for -9 at the own 30 reads as +49, clamped to +30.
- **Opponent territory.** Sacked for -1 at the opponent 19 reads as -61, clamped to -25.

The fitted tables carry it:

- **The sack table.** It holds a pile at its +30 clamp (104 of 4,704) and its -25 clamp (95). The sack table
  is not keyed by down, so 2.2% of ALL engine sacks become 30-yard gains, and most of those move the chains.
- **Fourth-down cells.** The fourth-down run and catch cells hold the same artifact. For example, the rush
  cell `4,0,4` has 60 of 255 records at -15 or worse; a failed fourth down hands the ball over 15 yards
  further back.
- **Own territory.** A failed attempt can read as a conversion.

The viewer shows these plays as the engine plays them: "sacked; the play nets +30 yards", or "runs for a
loss of 15 ... TURNOVER ON DOWNS". Its "Not modelled" list says so.

The fix is a refit, with Fable's three additions:

1. **The detector.** `down == 4 and not (F_FIRST | F_TD | F_TURNOVER) and EDOWN == 1 and EYTG > 0`, giving
   the end spot `100 - EYTG`.
   - The EDOWN term keeps a replayed fourth down (offsetting penalties, same team) out.
   - It is an inference: the parser knows `same` but drops it. A stored `F_TOD` bit would need the four
     compact stores rebuilt from the summaries.
   - The inference is safe on 2026's data (no fourth-down "first down" short of the distance without a
     penalty).
2. **The other readers.** The fit's clock table (`_clock_class`) and `nfl_pbp.team_units`' tempo read the
   same gap after a failed fourth down as the offence's own play. Both should see the possession change.
3. **Downstream.** The engine's fourth-down conversion rate, the DFS worlds and the season sims all inherit
   the refit.

Then re-run the walk-forward validation (`research/reports/nfl_deep_validation.md`) under the registered
procedure: the rule written first, tables refit on past seasons only.

It moves every deep number (the board's deep figure, the DFS worlds, the season sims), so it is its own
change. **Registered for 2026-11-06.** The "artifact" marker (6.1) is the bridge until then, not the fix.

## 7. Guards (section NV, tests/guards_viewer.py)

36 checks, in the order they would have caught a bug here:

- **The log.** It is passive (every box identical with and without it), opens on the kickoff and closes on
  the final. The log replays to a legal game.
- **Names.** They add up to the box. Every credited player is on the right side. A defender appears only on
  a certain event. The defence is named at all: each side is credited 20+ tackles a game and 0.1+ forced
  fumbles and recoveries (39.4-39.9 and 0.29 measured), and no play prints a team where a defender's name
  belongs. The text invents nothing, and nobody ruled out or in doubt is named.
- **Real names.** Sleeper's real week-8 rows (a committed GB / CAR cut,
  `research/data/fixtures/sleeper_w8_gb_car.json`) fill every role. A club with no defensive names says so
  in words and is ledgered.
- **Continuity of the told game** (adapted from Fable's checker). Every snap starts where the last event
  left the ball, with the right offence, down and distance. The field's spots agree with the log. Turnover
  banners and takeover spots match. A touchdown is followed by its try.
- **The sack clamp.** A table forced to +30 leaves no snap at or past the goal line, and must produce
  gains (723 in 150 games), so a renamed table cannot leave the check green. Every sack that nets yards is
  marked "artifact", and nothing else is. The engine's sack-gain rate is pinned at 10% or less (7.9%
  measured).
- **The typical game.**
  - The coordinate list.
  - The median-sign winner rule, on a set where the nearest game is a tie and the nearest untied one a loss.
  - The pick'em rule.
  - A brute-force argmin and a second process.
  - The replay matches the selection run, and a replay that does not is refused.
- **Determinism.** Telling a game is deterministic and leaves the log alone.
- **The per-down rules.** On real logs, every down's calls add up to 100%. The kick the clock forces on 1st
  to 3rd down is not a call.
- **The flavour shrinkage.**
- **The winner.** The typical game's winner is the board's favourite, unless the matchup is within three
  points of a coin flip.
- **The piece.** Its contents, size, provenance, and the board's figure from the board's own call.
- **The route.**
  - It reads only.
  - It serves one game at a time, also through Flask: JSON, and a 400 for a bad team.
  - After kickoff: marked, then refused, never rebuilt.
  - A stale piece is served while its rebuild runs.
  - The inactives rebuild; the PC-on wait; the two-week limit.
  - One server build at a time, its claim taken inside `HEAVY_BUILD` and released after the child.
  - A piece of another schema is treated as missing.
- **Builds.**
  - Pre-game only.
  - Never over PIECE_MAX.
  - Never in a web worker (source checks on the route, `serve`, `_server_build`, and `build_isolated` through
    `run_child`).
  - The PC's due rule and budget.
- **Wiring.** The PC pass, `_KEEP_LONG`, `artifacts.SCHEMA` 8, the page, the cache-buster, the service
  worker, the card's escaped button. The PC upload takes a piece's name, with no prefix list.
- **The script.**
  - No inline handlers.
  - Under node, the real render functions are given a piece whose every string is `<img onerror>` and
    write none of it raw.
  - Under a DOM stub, a whole game's events and field plans, drawn at four moments each, write no NaN or
    undefined.
  - The 202 loop ends with a reason.

Mutation check: see section 9.

## 8. Records

- **Design v1 and Fable's review.** Summarised in section 1; the review's full text was the session's.
- **Mutation runs.** See section 9.
- **Fable's build review.** See section 10.

## 9. Mutation runs

**Run 1: 28 mutants.** Each was written into the working tree, run against `tests/guards_viewer.py`, and
restored. Every one failed the check named for it:

| Area | Mutants |
|---|---|
| The log | truthiness on the first kickoff; an extra draw in a logging branch |
| The engine | the sack clamp removed |
| The typical game | no sign rule; ties allowed (caught by the pick'em check, since the sign rule already excludes ties unless the median margin is 0); a mean centre; no replay check; hash() seeds; the board figure's seed |
| Names | a tackler named out of bounds; a pass breakup; the injury filter off; a return length told; a fumble's credit dropped; tacklers credited to the offence |
| Tables | the success rule; no flavour shrinkage |
| The route | an inline build in the route; no close at +4 h; a rebuild after kickoff |
| Builds | a piece published past kickoff; pc_build answering None; the due rule ignoring age |
| Wiring | `_KEEP_LONG` and the PC wiring removed |
| The script | three unescaped strings (the play text, the box score names, the header) |

The harness is in the session's scratchpad (`viewer/mut/mutate_viewer.py`). Its first run reported three
misses, and all three were harness errors, not guard gaps:

- an anchor's indentation;
- a prefix anchor that left a syntax error;
- an expectation string missing a word.

Each was rerun corrected and caught.

**Run 2: 43 mutants**, the 28 above plus fifteen for the build review's items (section 10):

| Area | Mutants |
|---|---|
| The screen and text | the artifact mark removed; the takeover told in the wrong team's frame; a NaN field coordinate; the 202 loop polling forever |
| Tables | the clock-expiring kick counted as a call on downs 1-3 |
| The typical game | no pick'em preference |
| Names | the doubt filter off; the missing-roles ledger note dropped |
| The route and builds | no inactives rebuild; no wait for the PC; no week limit; no server gate; no size cap; the child built without `run_child`; the route answering without the JSON mimetype |

42 failed the check named for them. The miss was a guard, not the code. The "kick counted" mutant was caught,
but only by the synthetic per-down check. The real-logs check named for it read nothing:

- It skipped any row without an "n" key.
- No counted row of the table has one. Only an empty down carries `"n": 0`.
- So it skipped every row, and passed.

The fix:

- The check skips only the rows without `per_game`.
- It requires all eight rows to be read.
- The guard's 360 games hold 110 such kicks.

Rerun, the mutant fails both checks: 43 of 43.

**Run 3: Fable's round-2 items** (section 10). There are four new mutants:

- the gate claimed before the `HEAVY_BUILD` wait;
- no tackler drawn;
- the tackle uncredited;
- every sack losing yards.

"No server gate" was also re-anchored, because its line changed. All five were caught.

Then all 47 were re-run on the final tree, and all 47 failed the check named for them. A container restart
killed that run at the 22nd mutant, which was left in the tree. It was found by the tree's hash, taken
before the run, and put back; the hash matched again. The run resumed from that mutant. The harness now
records the original of the file it mutates and restores it at its next start.

## 10. Fable's build review

**Round 1** (2026-10-30, about 00:45 UTC). The verdict was **AGREE WITH CHANGES**: one MAJOR, nine MINOR, nine
NIT. Fable reproduced 6.1 and 6.2 independently; sections 6.1 and 6.2 quote its figures. It also listed nine
kinds of bug the guards did not catch.

Each item and its fix:

| Item | Finding | Fixed as |
|---|---|---|
| M1 | The +30-yard sack was on the screen, unmarked, in the typical game of the first matchup the owner would open. | `tell` marks every sack that nets yards (`art`). The screen tags it "artifact" on the play card and in the list. The footnote and the Not-modelled list say why. 6.2 is registered for 2026-11-06. |
| m1 | The 202 loop ended in a dead end with no message. | It gives up after 30 polls (four minutes) with a reason, and every open starts the count afresh. |
| m2 | `PIECE_MAX` was enforced nowhere in production. | `build_week` measures the pickle and refuses a piece over it (ledger `NFLV-size`). |
| m3 | `build_isolated` was `run_child` without its protections. | It is `deep_cache.run_child`. |
| m4 | Server builds could run three abreast, because `HEAVY_BUILD` is per process. | One claim across every worker (`_SERVER_GATE`), held for the child's life. A second request answers that a simulation is running (round 2 moved the claim inside `HEAVY_BUILD`). |
| m5 | The PC's budget comment and the server's threshold disagreed with the 12-hour cadence. | `_serve_ages`: with the PC on, the server waits `REBUILD_S` plus two PC passes plus `PC_SLACK_S`. |
| m6 | Sunday's inactives never reached the names, and the slate's doubt rule was not applied. | A piece built before kickoff minus 85 minutes is rebuilt in the last 75. `rosters(doubt=)` reads `nfl_game_sim.availability`. QB and K take the most-projected player left. |
| m7 | A pick'em read against the number beside it. | A pick'em prefers a game decided by three or fewer. The screen says so whenever the game shown is won by the side the board does not favour. |
| m8 | The per-down table counted the clock-expiring field goal on downs 1-3. | It is skipped. |
| m9 | Comments and reports did not say what was measured. | The clamp's comparison; 15 of 489; within +/-0.3 points at 8,000 seeds; 400-433 KB; 9.6 s. Notes were added to the two validation reports. |
| n1-n9 | The ATT header; "First down."; the clock sentence; words for missing roles, plus a ledger note; the piece's own read beside the card's; the banner cleared; no week beyond next; the poll count; the guard's wording. | All done. |
| Guard gaps 1-9 | Told-game continuity, the screen's logic, the route through Flask, real input shapes, the size cap, the winner against the board's side, per-down sums on real logs, the sack-gain pin, the upload path. | Each was added (section 7). |

**Round 2** (2026-10-30, about 01:30 UTC). The verdict was **AGREE WITH CHANGES**: the product AGREE,
and one guard addition before the push.

Fable verified every round-1 fix in the code, not the report. It rebuilt all 14 week-8 pieces on the new
code: no typical game won by the side the board does not favour, LV @ NYJ now a 3-point NYJ win,
400-433 KB, 9.5 s. It checked 560 told games for continuity, with no issue. It drove the screen in a
browser at 390 px: the artifact tag, the own-read line, the "In this game" line, the banner, the 202 dead
end, no NaN frame, no overflow.

Its remaining items, and what was done:

| Item | Finding | Done |
|---|---|---|
| G1 (before the push) | No check required a tackler, a forced fumble or a recovery ever to be named. A teller that named none passed every name check, and so did one printing the TEAM as the tackler. | Each side must be credited 20+ tackles a game and 0.1+ forced fumbles and recoveries. No play may print a team where a defender's name belongs. Two mutants: no tackler drawn, and the tackle uncredited. |
| G2 | The clamp check asserted only that the illegal state was absent. | The forced table must produce gains. A mutant where every sack loses yards is caught. |
| G3 | The two node checks passed silently without node. | Without node their titles say "(skipped for a stated reason: ...)", the suite's convention. |
| m4, note 1 | The gate was claimed before the `HEAVY_BUILD` wait, so a long wait could let a second worker take it over. | It is claimed inside the block (section 5). A guard holds `HEAVY_BUILD`, sees no claim during the wait, then sees the claim held through the child and released after. A mutant claiming before the wait is caught. |
| m4, note 2 | "Another game is being simulated first" was also said for the SAME game's build on another worker. | "A simulation is running; this game follows." |
