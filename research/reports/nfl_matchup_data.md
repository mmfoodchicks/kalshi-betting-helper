# The NFL matchup engine's data foundation

2026-10-23 (revised the same night after the Fable review,
`research/reviews/nfl_matchup_data.md`; §10 lists every fix). Sources,
coverage by season, the as-of profiles, what was measured about each kind of
signal, the per-player-game dataset and its schema, and what is approximated
or missing. Nothing in this report was fitted to an
outcome; the modelling agent fits on 2023-2024, validates on 2025 and tests
once on 2026 (the brief's rule 9). The measurements here are descriptive
(reliability and persistence of the signals themselves), and the two rules
that turn them into constants were written into
`research/nfl_matchup_dataset.py`'s header before the first run
(2026-10-23 ~01:15 UTC). The decay and k rules did not change; the
scheme-change rule gained its league-centring after the first run and the
header says so (§5.4). The sha256 quoted in the first version of this report
(`b7832074...`) was of the file AFTER that change, not before it (review M1);
the pre-run hash was not recorded and cannot be recovered. The file as it
stands after the review fixes: `python3 - <<< "import hashlib; ..."` --
see §10 for the four file hashes.

Credit: **FTN Data via nflverse** (CC BY-SA 4.0) for every FTN-derived
column; nflverse-data (CC BY 4.0) for play-by-play, participation, players
and snap counts; DynastyProcess for the Sleeper ids. Every committed table
under `research/data/nfl_matchup/` carries a `license` block saying so, and
the derived tables that read FTN charting are shared under CC BY-SA 4.0.

## 1. Files

| file | role |
|---|---|
| `nfl_matchup_data.py` | production-safe, stdlib only: fetch, parse and compact the nflverse CSVs into one columnar store a season (`nfl_matchup_<season>` in the deep store), the gsis / Sleeper / ESPN / name crosswalk (`nfl_matchup_ids`), `refresh()` as the body of a PC-first deep job |
| `nfl_matchup_profiles.py` | production-safe, stdlib only: as-of defense / offense / QB / player profiles with denominators and empirical-Bayes shrinkage |
| `research/nfl_matchup_dataset.py` | numpy: the persistence and reliability measurements, the 2026 scheme-change flags, the dataset build, the pbp-vs-Sleeper cross-check |
| `research/data/nfl_matchup/` | `dataset_2023_2026.csv.gz` (7.46 MB), `dataset_schema.json`, `dataset_meta.json`, `persistence.json`, `reliability.json`, `charting_agreement.json`, `scheme_change_2026.json`, `sleeper_crosscheck.json` (7.5 MB in all, every file rebuildable by the script) |
| `tests/guards_nfl_matchup_data.py` | 149 hermetic checks, `run(ck)`, standalone exit code; the review guards (traded player, pre-2023 vocabulary, within-week ordering, k-source, freshness, no-participation season) were written to FAIL on the pre-review code and did (19 failures recorded before the fixes) |

Local run (the deep store pointed at a scratch dir, raw files under the shared
scratch data dir):

    export DEEP_CACHE_DIR=<scratch>/deep_nfl_matchup VIGIL_NO_BG=1
    python3 -c "import nfl_matchup_data as M; M.refresh(seasons=range(2018, 2027), raw_dir='<scratch>/data/nflverse')"
    python3 -m research.nfl_matchup_dataset measure
    NFL_MATCHUP_RAW=<scratch>/data/nflverse python3 -m research.nfl_matchup_dataset build --sleeper <scratch>/data/sleeper
    python3 -m research.nfl_matchup_dataset check

## 2. Sources and what each one really carries

All reachable from this sandbox on 2026-10-23 at
`https://github.com/nflverse/nflverse-data/releases/download/<tag>/<file>`
(DynastyProcess at `raw.githubusercontent.com/dynastyprocess/data/master/files/db_playerids.csv`;
the `github.com/.../raw/...` form answers 403 through the proxy).

| source | seasons | size (2023) | what is used |
|---|---|---|---|
| `pbp/play_by_play_<Y>.csv.gz` | 2016-2026 (2026 file dated 2026-10-22 15:03 UTC) | 19.2 MB gzipped, 49,665 rows, 372 columns | passer / receiver / rusher gsis ids, air yards, YAC, EPA, CPOE, xpass, success, pass location and length, run location and gap, shotgun, no-huddle, score, clock, WP, the fumbler on a lost fumble |
| `pbp_participation/pbp_participation_<Y>.csv` | 2016-2025; **no 2026 file**, and nflreadr says a season's file is published after that postseason and does not update in season | 50.0 MB, 46,168 rows | NGS post-snap charting: offense / defense personnel strings, formation, defenders in box, pass rushers, time to throw, was_pressure, `route`, `defense_man_zone_type`, `defense_coverage_type`, the 11 offensive gsis ids on the field |
| `ftn_charting/ftn_charting_<Y>.csv` | 2022-2026 | 8.3 MB, 48,226 rows | motion, play action, screen, RPO, n_blitzers, n_pass_rushers, n_defense_box, catchable / contested / drop, read thrown, QB out of pocket, sneak, starting hash, QB location, backfield count. No coverage. |
| `players/players.csv` | current | 7.3 MB, 24,861 rows | gsis id, display name, position, espn_id, latest team, status |
| DynastyProcess `db_playerids.csv` | current | 2.6 MB, 12,530 rows | gsis <-> sleeper_id (+ espn_id, a second opinion) |
| `snap_counts/snap_counts_<Y>.csv` | 2016-2026 | 2.4 MB | PFR offensive snaps per player-game, by name -- the one snap source for 2026 |
| Sleeper `projections/nfl/<season>/<week>` and `stats/nfl/<season>/<week>` | fetched for 2026 weeks 1-7; 2022-2025 already in `research/data/player_weeks_2022_2025.csv.gz` | 1.9 MB a week | the weekly line and the weekly box (see §6.2 for what "projection" means here) |

### 2.1 Joins and coverage, measured

- pbp x participation: **every** pass / run snap of 2018-2025 joined on
  (`game_id`, `play_id`) -- 34,449 / 34,449 in 2021 through 33,325 / 33,325 in
  2025 (regular season, two-point tries and deleted plays removed). pbp x FTN:
  100% in 2023-2026, 34,160 of 34,254 in 2022 (94 plays FTN never charted).
- Coverage (`defense_coverage_type`) is charted on **99.7-99.9% of pass
  attempts** every season 2021-2025 (99.5-99.9% back to 2018), on sacks and
  scrambles only from 2023 (98.5-99.8%; 0% in 2018-22), and on all but a
  handful of runs (6-20 a season). The 40-47% of rows that are blank are runs
  (plus 2018-22 sacks and scrambles), not missing charting.
  Every coverage share in the profiles is therefore per **charted dropback**
  (`coverage.dropbacks_charted`), never per play. Per-coverage sack rates do
  not exist for 2021-22.
- `route` (nflreadr: "the route of the primary receiver") is non-blank on
  98.6-99.9% of attempts **with** a `receiver_player_id` and on 0% of attempts
  without one in 2021-22 (52-55% from 2023, when NGS began charting a primary
  read on throwaways). On a targeted play it is therefore the target's route,
  and the profiles read it only there. Routes on untargeted snaps do not exist,
  so there is no "routes run" denominator anywhere (§6.4).
- **Two route vocabularies.** 2016-2022 files chart HITCH, OUT, FLAT, CROSS,
  IN, ANGLE, GO, SCREEN, SLANT, POST, CORNER, WHEEL; 2023 on QUICK OUT, DEEP
  OUT, HITCH/CURL, SHALLOW CROSS/DRAG, IN/DIG, TEXAS/ANGLE, SWING plus the six
  shared names. The first build decoded six of the twelve old names as OTHER
  (66% of 2021-22 targeted routes; review F2). Every name is now its own code
  (`nfl_matchup_data.ROUTES`, `unknown` in the store lists any string outside
  the vocabularies: empty on all nine seasons), but the two vocabularies cut
  the short game differently -- with OUT / FLAT / CROSS read as "quick" the
  2022 -> 2023 family shares move quick 62% -> 50%, screen 9% -> 15%,
  intermediate 10% -> 17% while vertical holds at 18.1% -- so **route-family
  mixes are built from 2023+ charting only** (`ROUTE_ERA`): a 2023 cutoff
  has no prior-season route mix, and `p_route_*` is blank on 45% of 2023
  weeks 1-4 skill rows (8% by week 10; 12% / 10% in 2024 / 2025 weeks 1-4).
  Formations likewise: 2016-22 SINGLEBACK / I_FORM / EMPTY / JUMBO / WILDCAT,
  2023+ UNDER CENTER / SHOTGUN / PISTOL, all decoded, no dataset column.
  PREVENT (70-97 dropbacks a season 2018-22) is its own coverage code in
  family "other"; the first build filed it as BLOWN.
- The charted **man share moves between seasons**: league man share of charted
  dropbacks 2021 0.30, 2022 0.29, 2023 0.41, 2024 0.48, 2025 0.31 (Cover 1
  0.32 / 0.37 / 0.22 for 2023-25, Cover 3 0.18 / 0.18 / 0.28). Teams keep
  their rank (§5.1) while the level jumps, so a man rate is comparable to the
  same season's league mean and to the same team's rank, not across seasons
  raw. Every profile carries the league prior of its own cutoff beside the
  value.
- The 2024 and 2025 participation FILES carry `defenders_in_box = 0` on
  ~9,100 rows a season, almost all non-scrimmage rows that never reach the
  store; on kept snaps it is 23 (2024) and 52 (2025). 0 is read as "not
  charted" (a real box is never empty).
- **NGS vs FTN charting agreement** (`charting_agreement.json`): on plays both
  charted, the box count agrees 99.97% (2023), 99.93% (2024), 99.76% (2025)
  and the pass-rusher count 99.99-100% -- from 2023 the two files are the
  same charting. In 2022 they agree on 59% of boxes and 77% of rusher counts
  (different charters). So the FTN box and blitz used for 2026 are the same
  measurement as the NGS box and blitz of 2023-2025, and 2022's FTN fields are
  a different instrument from its NGS ones.
- 2026 in the stores: 93 games, weeks 1-6 (16, 16, 16, 16, 15, 14). Week 7
  began Thursday 2026-10-22 and had not been published when the pbp file was
  built (15:03 UTC that day), so "2026 weeks 1-7" is, today, weeks 1-6 plus
  nothing; the dataset builder caps 2026 at week 7 and will take week 7 when
  the store is refreshed after Monday.

### 2.2 Cost, measured

- Fresh one-season refresh (players.csv 7.3 MB + DynastyProcess 2.6 MB +
  pbp 19.4 MB + participation 49.7 MB + FTN 8.3 MB, through the proxy):
  downloads 5.7 s, parse 3.4 s, **9.4 s in all, peak RSS 73 MB**. Nine
  seasons from cached downloads: 18 s, peak 78 MB. A compact season store
  pickles to **3.7 MB** (2026 so far 1.1 MB); the ids store 0.56 MB.
- The store: one `array` per column (61 columns, 1-4 bytes each) over the
  ~34,000 regular-season offensive snaps, plus per game a 31-short vector per
  skill player on the field (snaps, dropbacks, designed runs, dropbacks vs
  man / vs zone, the offence he was on, dropbacks under each of the eleven
  coverage codes, then HIS TARGETS on those very plays: all, vs man, vs zone,
  under each code). Numerator and denominator of every per-dropback rate
  come from that one vector, so the rate is <= 1 by construction and a
  season without participation adds to neither side (review F1 / F7). Kneels
  and spikes are not snaps in it (M2). Pickles: 3.7-3.9 MB a season.
- A full snapshot of profiles as of a week (three seasons of tallies) builds
  in 1.5 s and holds **~120 MB RSS** in the building process (review M4):
  profiles belong in the PC job or a niced child, never a gunicorn worker.
  Reading one defense profile takes 23 ms, one player 2 ms.

### 2.3 The crosswalk

`build_ids` keeps QB / RB / WR / TE / FB from players.csv (6,476 players) and
joins DynastyProcess by gsis id: 2,728 have Sleeper ids. Of the **628 skill
players with 20+ plays in 2023-2026, all 628 have a Sleeper id**; players.csv
has an ESPN id for 986 of the 987 distinct skill players in that span.
Positions come from players.csv (DynastyProcess files fullbacks as RB and
Travis Hunter as WR where players.csv says CB; FB is folded into RB for the
position splits). DynastyProcess lists ten gsis ids twice (the later row
wins). Names are keyed by `kalshi_nfl._norm`, so a Kalshi prop title, a
Sleeper roster name and nflverse agree; `resolve_name` disambiguates a shared
name by team, then position, then last active season, and returns None when
still ambiguous. In the dataset, 23,600 of 23,671 Sleeper projection rows
(99.7%) mapped to a gsis id.

## 3. The as-of profiles (`nfl_matchup_profiles.py`)

A `Snapshot(season, week, current, priors)` holds the cutoff season's games
with week < cutoff and whole prior seasons, each prior season at weight
`DECAY ** (seasons back)` with at most `BACK = 2` seasons back; the weights
are stamped on every profile. The tallies record every (season, week) they
contain and the constructor refuses tallies at or past the cutoff; the guard
proves it on a synthetic season (§7).

Every rate is `{"v", "n", "eb", "prior", "k"}`: raw value, denominator,
`(sum + k x prior) / (n + k)`, the league figure at that cutoff (or the
player's / defense's own overall figure for a split), and the k from §5.2.
The profile also says where k came from (`k_source`).

**Defense** (`Snapshot.defense(team)`): actual DL-LB-DB groupings from the
personnel string (`most_played`, the top groups with pass rate), nickel
(5 DB) / dime (6+) / base (<= 4) shares, the **inferred nominal front**
(4-3 vs 3-4 by what the unit lines up in against 12/21/13/22 personnel with
four DBs; kept apart from what it plays most, which is nickel for 31 of 32
teams), the same splits **by offensive personnel faced** (11/12/13/10/21/22/20)
and **by down-distance bucket** (early, 2nd & 7+, 3rd & 1-3 / 4-6 / 7+), each
with its pass rate; light (<= 6) / heavy (8+) box shares and run efficiency
allowed by box bucket; coverage family mix (every COVER code), man / zone,
single-high (C1 + C3) / two-high (C2 + C4 + C6 + 2-Man) / zero / other
(C9, COMBO, BLOWN kept out of the shells on purpose); blitz (5+ pass rushers)
/ pressure / sack / scramble rates, time to throw and mean rushers; efficiency
allowed per target (catch, yards, EPA, 20+, YAC, aDOT, TD, INT, contested,
catchable) for all targets, per coverage family, per man/zone, **per targeted
position with its own man/zone and depth splits**, per depth, per pass
location, per route family; run defense by direction x gap; position
allowances per game in standard / half / PPR with targets, yards and carries,
per opportunity, and **opponent-adjusted** (allowed minus that offence's
per-game average against every other defense as of the cutoff, needing two
weighted other games); red-zone targets and carries by position; the last
four games.

**Offense**: personnel usage with pass rate within each, formation mix,
pass rate, PROE (nflfastR xpass), neutral (|score diff| <= 7, Q1-Q3) and
early-down pass rate, shotgun / under center, no-huddle, play action and
screen (of dropbacks, FTN), motion and RPO (of snaps, FTN), red-zone pass
rate, targets by position / depth / location, deep rate, carries by
position, protection (pressure allowed, sacks, blitz faced, time to throw,
QB hits).

**QB**: dropbacks, attempts, completion, YPA, EPA per dropback, sack, INT,
time to throw, deep rate, scramble rate; **by depth** (attempts, completion,
CPOE, YPA, EPA); vs man / zone / pressure / no pressure / blitz / no blitz
and each coverage code (dropbacks, EPA per dropback, completion, YPA, sack);
target preferences (top receivers' shares and aDOT); `qb_target_share` for a
given receiver.

**Player** (WR / TE / RB; a QB's rushing reads through the same path). Two
scopes, named in the profile (`usage.scope`, `receiving.scope`): **usage is
team-scoped** when `player(pid, team=)` is given a team -- the games he
played for that team, numerator and denominator alike (the review's F1 was
player-scoped target counts over team-scoped dropbacks for the 23% of rows
whose player changed teams inside the window; Diontae Johnson's 37.9 targets
per dropback is now 0 of 6 at Baltimore); **receiving efficiency pools every
team he played for** in the window (it is his skill, not the team's). Usage:
snap share, target share, air-yard share, carry share, red-zone target and
carry shares, targets and carries per game, **targets per on-field dropback**
(labelled `kind: proxy` -- a snap on the field is not a route) overall, vs
man / vs zone, by shell (single-high / two-high / zero / other) and per
coverage code, every one from the on-field vector's own target slots and
carrying `n_current` (this season's on-field dropbacks: 0 for 2026).
Receiving: catch, YPT, EPA per target, aDOT, YAC per reception, 20+ rate,
contested and catchable shares, drop count; depth mix, location mix,
route-family mix (quick / screen / intermediate / vertical, from the
targeted-receiver route, 2023+ charting only); vs man / vs zone efficiency
and per-coverage efficiency; rushing: YPC, success, EPA, stuff, 10+, TD per
carry, by box bucket and by direction x gap; fantasy per game in three
formats, season and the last four games (the last four games played,
unweighted across seasons); `alignment_proxy` (§6.4).

Shrinkage k are per quantity (review F4): pressure, sack, time to throw, EPA
per dropback and scramble rate allowed each take their own measured k, box
shares `def_box`, a player's aDOT `pl_adot` (4.9 targets) and YAC per
reception `pl_yac` (17.8), a QB's time to throw `qb_ttt` (74) and deep rate
`qb_deep` (267); the profile stamps the k it applied on every rate and names
the table it read (`k_source`; a missing or corrupt table is ledgered
`NFLM-k-table`, review M3).

Fantasy convention (the owner's, `FORMATS`): reception 1 / 0.5 / 0 for PPR /
half / standard, 0.1 a receiving or rushing yard, 6 any touchdown, 0.04 a
passing yard, 4 a passing touchdown, -2 an interception, -2 a fumble lost; no
two-point conversions (the plays are dropped at the parser), no return or
fumble-recovery touchdowns, no yardage bonuses. QB kneels and spikes are not
snaps here (they are not matchup events), so a QB's pbp carries read ~0.19 a
game under Sleeper's and his rushing yards 0.2 over (§4.3).

## 4. The dataset (`research/data/nfl_matchup/dataset_2023_2026.csv.gz`)

**22,825 rows x 338 columns, 7.46 MB gzipped**, sha256
`766a60ed11ee3c017141443bd114625b3d756c5bb03737dfc3148389bbef2738`; one
row per (game, team, player) for every QB / RB / WR / TE who had a snap,
target, carry or pass attempt in the game **or** whom Sleeper projected for
it (a Sleeper-only row is dropped when the same player has a played row that
week for another team: one row, review N7). Built in 124 s. The first
version had 22,978 rows: 153 kneel-only appearances no longer count as a
game played (M2) and the one duplicate is gone.

| season | rows | games | weeks |
|---|---|---|---|
| 2023 | 6,868 | 272 | 1-18 |
| 2024 | 6,829 | 272 | 1-18 |
| 2025 | 6,875 | 272 | 1-18 |
| 2026 | 2,253 | 93 | 1-6 (the test split; touch once, after the model is frozen) |

By position: WR 9,120, RB 5,957, TE 5,527, QB 2,221. `played` = 1 on 21,505
rows (a snap, target, carry or attempt -- a decoy with snaps and no touch
counts, review N3); `in_sleeper` = 1 on 20,329 (1,320 rows are
Sleeper-projected men who did not play -- actuals read zero, `played` = 0;
2,496 rows are players with a snap or touch Sleeper did not project). Snaps:
NGS on-field ids for 19,691 rows, PFR snap counts (by normalised name + team
+ game) for 2,212 (2,109 of the 2,253 2026 rows), none for 922
(`snaps_source` says which).

**Every week's rows come from a snapshot frozen before any of that week's
games** (`week_rows`, review F3): the first version fed each game into the
live tallies as it went, so a Sunday row's opponent-adjusted columns could
read that week's Thursday game (1,360 fields in 2024 week 10, max 0.20 PPR).
The builder now raises if the snapshot holds the week's own games; the guard
proves a game's rows are identical with the week's other game removed from
the store.

**Quarterbacks, two ways.** `q_*` describe Sleeper's QB1 by projected pass
attempts **from the Tuesday post-week snapshot** (2023-25 from the repo's
`player_weeks` table, fetched after the season; 2026 from the 2026-10-23
fetch) -- `q_source` reads `sleeper_qb1_tuesday` on 22,811 rows and
`last_starter` on 14; it is NOT provably pre-kickoff (review F6 / O1), though
it equals the game's leading passer 97.2% of the time and the week-1 2026
pre-kickoff capture agrees with it for all 32 teams. `qp_*` is a pick that is
**pre-kickoff by construction**: the app's frozen Sleeper capture when one
dated before the game exists (`research/data/feeds/proj_2026_1.json`
2026-09-12 and `proj_2026_2.json` 2026-09-19, Sunday / Monday games only:
713 rows, `qp_source = frozen_capture`), else the team's leading passer in
its last game as of the cutoff (22,112 rows). The two picks differ on 2,704
rows (11.8%; 842 / 788 / 942 / 132 by season) -- injuries, benchings and
byes the Tuesday snapshot knew and the last game did not. The model decides
which to trust; both carry the same feature set.

### 4.1 Schema

The full column list with meaning, source and kind is
`dataset_schema.json` (338 entries, generated from the builder's `schema()`,
so it cannot drift from the file). Kinds: `key` (12), `observed` (260),
`observed (not provably pre-kickoff)` (13, the `sl_proj_*`), `proxy` (7:
the per-dropback target rates and the alignment proxy), `derived` (16),
`derived (opponent-adjusted)` (12), `derived (not provably pre-kickoff)`
(`q_gsis`), `derived (pre-kickoff by construction)` (`qp_gsis`),
`modelled (EB)` (16; the raw value and its `n` sit beside each). Groups:

| prefix | columns | content |
|---|---|---|
| keys | `season week game_id date team opp home gsis sleeper_id name norm_name pos played in_sleeper sl_role snaps_source` | nflverse codes (LA = Rams); `sleeper_id` from DynastyProcess |
| `sl_proj_*` (14) | `pass_att pass_cmp pass_yd pass_td pass_int rush_att rush_yd rush_td rec_tgt rec rec_yd rec_td fum_lost` + `sl_proj_ppr` | Sleeper's weekly line, exactly the fields the API returns (§6.2 on when it was taken) |
| `sl_act_*` (15) | `pass_att pass_yd pass_td pass_int rush_att rush_yd rush_td rec_tgt rec rec_yd rec_td fum_lost pts_ppr gp off_snp` | Sleeper's weekly box, for the cross-check |
| `act_*`, `team_*` (22) | targets, receptions, receiving yards / TD, air yards, carries, rushing yards / TD, pass att / cmp / yds / TD, INT, fumbles lost, red-zone targets / carries, std / half / PPR points, snaps, dropback snaps, snap share; team snaps / dropbacks / targets / carries | the outcomes, from pbp (snaps from participation or PFR) |
| `p_*` (73) | the player profile as of the game: shares, per-game usage, `tgt_per_db` (+ vs man / zone, single-high / two-high; all team-scoped, same plays), `p_tgt_per_db_n_current`, `p_tgt_per_db_proxy` (+ `_n`), efficiency raw and `_eb`, depth / location / route mixes, man / zone splits, rushing with box and direction splits, fantasy per game, last-4 figures, `p_align_mid_proxy` | `nfl_matchup_profiles.player(pid, team=)` |
| `d_*` (125) | the opponent defense as of the game: `d_cov_n`, personnel shares (incl. vs 11 / vs 12 / 3rd & long), box, man / zone / shells / every family, blitz / pressure / sack / TTT / rushers / EPA per dropback, allowed per target overall / man / zone / shells / depth / location / route family, per position (WR / TE / RB: target rate, YPT raw + eb, EPA, catch, man / zone YPT, per-game targets / yards / std / half / PPR raw + eb, opponent-adjusted PPR / targets / yards, last-4 PPR), RB carries and rushing yards (+ adjusted), QB passing yards and points (+ adjusted), run defense | `nfl_matchup_profiles.defense` |
| `o_*` (25) | own offense as of the game | `nfl_matchup_profiles.offense` |
| `q_*` (22) | Sleeper's Tuesday QB1 as of the game, incl. CPOE by depth, EPA vs man / zone / pressure / blitz, `q_tgt_share_to_player`, `q_is_player` | `nfl_matchup_profiles.qb` |
| `qp_*` (14) | the pre-kickoff QB pick: `qp_gsis qp_source qp_same_as_q qp_games qp_dropbacks qp_epa_db qp_epa_db_eb qp_ttt qp_cpoe_short qp_epa_man qp_epa_zone qp_epa_press qp_epa_blitz qp_tgt_share_to_player` | `nfl_matchup_profiles.qb` |
| `x_*` (8) | interaction-ready pieces: expected target rate under the opponent's man / zone mix and under its shell mix (both in [0, 1] because every input is), expected YPT under its man / zone mix, depth-mix x YPT-allowed-by-depth, route-mix x EPA-allowed-by-route, the position's YPT-allowed gap to the league, the position's opponent-adjusted PPR allowed, box-mix x YPC-by-box | arithmetic on the above |

Blank cells mean "no denominator" (a WR's `p_ypc`, a non-QB's `sl_proj_pass_*`,
a rookie's prior-season splits, a route mix before the 2023 charting has
accumulated). Week 1 of 2023 has only prior-season profiles (2021-2022 at
0.5 / 0.25). For 2026, every coverage column comes from 2025 (x 0.5) and 2024
(x 0.25) only: `d_cov_n` reads 424-522 weighted dropbacks,
`p_tgt_per_db_n_current` is 0 on every 2026 row, `p_tgt_per_db` is blank
for 731 of 2,253 rows (players with no 2024-25 on-field record) and FLAT
across the weeks (0.14 by week 1-6: it is the prior seasons' rate, numerator
and denominator both -- the first version divided 2026 targets by 2024-25
dropbacks and climbed 0.18 -> 0.33 over the six weeks). The same-season
per-dropback rate 2026 does have is **`p_tgt_per_db_proxy`** (kind proxy):
targets / (team dropbacks x PFR offensive snap share) over this season's
games for this team as of the cutoff, present from week 2 on 1,521 2026 rows.
Checked on 2023-2025 where both exist with 100+ dropbacks behind each (8,093
player-games): r 0.963 with the on-field rate, mean 0.150 vs 0.145, mean
|diff| 0.018. It is also filled for 2023-2025, so a model can use one
definition everywhere or switch on `p_tgt_per_db_n_current`. **It is
unshrunk** (review R3): 11 rows exceed 1.0, every one with
`p_tgt_per_db_proxy_n` under 2; gate on `p_tgt_per_db_proxy_n` (>= 20
removes every value above 1; 3,511 rows have n < 20) or shrink toward the
league rate with `pl_snap`'s k -- do not cap at 1.0, which would hide the
noise rather than remove it.

**Route mixes early in 2023** (review R5): because route families are built
from 2023+ charting only, a 2023 row's `p_route_*` rests on the current
season's targets alone -- weeks 2-5 on one to four weeks of them (235 of 398
week-2 rows carry a mix at all) -- while a 2024+ row's rests on up to three
seasons. `p_targets` counts pre-era targets too, so there is no in-row
denominator for the mix; `p_games_cur` is the honest proxy for how much a
2023 route mix rests on. A `p_route_n` column was deliberately not added: it
would change the dataset's hash after the review agreed to ship it.

### 4.2 Sleeper's fields, exactly

Projection rows carry (count of rows in the dataset's seasons with the field
present): rec_tgt 18,805, rec 18,764, rec_yd 18,767, rec_td 17,573, rush_att
14,188, rush_yd 14,203, rush_td 10,370, fum_lost 13,564, pass_att / pass_cmp
/ pass_yd 1,910, pass_td / pass_int 1,886. Sleeper omits a field it projects
at zero. Box rows carry gp, off_snp, rec_tgt, rec, rec_yd, rec_td, rush_att,
rush_yd, rush_td, pass_att, pass_yd, pass_td, pass_int, fum_lost, pts_ppr
(and more that are not kept).

### 4.3 pbp actuals vs Sleeper's box (`sleeper_crosscheck.json`, 2023-2025 only)

19,611 player-games with a Sleeper box: receptions agree **exactly on
100%**, targets 99.96%, receiving yards 99.2% (mean |diff| 0.09 -- laterals),
passing yards 99.8%; carries 89.3% exact / 95.3% within one (pbp lower by
0.18 a game: kneel-downs, which Sleeper counts as rushes and this store does
not), rushing yards 89.4% / 95.1% (pbp higher by 0.19 for the same reason);
PPR points 88.5% exact / 95.3% within one (mean diff -0.12: kneels,
two-point conversions and return touchdowns, all outside the owner's
convention). The 2026 rows were not compared (test split).

## 5. Measurements

### 5.1 Year-over-year persistence of team tendencies (`persistence.json`)

Pearson r across the 32 teams of consecutive full regular seasons (Spearman
beside it in the file; e.g. man 0.49 / 0.75 / 0.48 / 0.39 / 0.43 / 0.40 /
0.41 for the seven pairs). "pooled" is the mean of the seven pairs. League
means show the charting level shift.

| metric | 18->19 | 19->20 | 20->21 | 21->22 | 22->23 | 23->24 | 24->25 | pooled r | league mean 2023 / 2024 / 2025 |
|---|---|---|---|---|---|---|---|---|---|
| man share | 0.62 | 0.74 | 0.52 | 0.39 | 0.39 | 0.47 | 0.45 | 0.51 | 0.413 / 0.484 / 0.307 |
| single-high (C1+C3) | 0.55 | 0.70 | 0.08 | 0.45 | 0.45 | 0.48 | 0.78 | 0.50 | 0.505 / 0.544 / 0.501 |
| two-high | 0.52 | 0.71 | 0.30 | 0.54 | 0.54 | 0.46 | 0.68 | 0.54 | 0.388 / 0.376 / 0.432 |
| Cover 0 | 0.83 | 0.69 | 0.70 | 0.62 | 0.53 | 0.31 | 0.27 | 0.57 | 0.030 / 0.042 / 0.037 |
| Cover 1 | 0.54 | 0.64 | 0.31 | 0.36 | 0.32 | 0.51 | 0.67 | 0.48 | 0.322 / 0.367 / 0.223 |
| Cover 3 | 0.84 | 0.56 | 0.13 | 0.28 | 0.17 | 0.49 | 0.16 | 0.38 | 0.183 / 0.177 / 0.278 |
| Cover 2 | 0.37 | 0.71 | 0.48 | 0.52 | 0.22 | 0.27 | 0.54 | 0.44 | 0.154 / 0.206 / 0.231 |
| Cover 4 | 0.44 | 0.67 | 0.66 | 0.75 | 0.08 | 0.10 | 0.15 | 0.41 | 0.121 / 0.061 / 0.101 |
| nickel | 0.50 | 0.62 | 0.67 | 0.67 | 0.12 | 0.27 | 0.61 | 0.50 | 0.664 / 0.657 / 0.597 |
| dime | 0.56 | 0.73 | 0.66 | 0.73 | 0.45 | 0.18 | 0.54 | 0.55 | 0.118 / 0.099 / 0.094 |
| base (<= 4 DB) | 0.32 | 0.49 | 0.76 | 0.71 | 0.51 | 0.46 | 0.38 | 0.52 | 0.218 / 0.244 / 0.310 |
| light box | 0.54 | 0.33 | 0.63 | 0.68 | 0.09 | 0.38 | 0.47 | 0.45 | 0.641 / 0.720 / 0.677 |
| heavy box | 0.43 | 0.30 | 0.65 | 0.72 | 0.39 | 0.44 | 0.64 | 0.51 | 0.104 / 0.054 / 0.057 |
| blitz (5+ rushers) | 0.62 | 0.65 | 0.51 | 0.47 | 0.43 | 0.63 | 0.60 | 0.56 | 0.253 / 0.267 / 0.269 |
| pressure | 0.28 | 0.17 | 0.07 | 0.18 | 0.41 | 0.43 | 0.54 | 0.30 | 0.295 / 0.308 / 0.297 |
| sack rate | 0.20 | 0.25 | 0.26 | -0.18 | 0.17 | 0.12 | 0.10 | 0.13 | 0.068 / 0.065 / 0.065 |
| EPA / dropback allowed | 0.39 | 0.31 | 0.37 | 0.24 | 0.13 | -0.16 | 0.15 | 0.20 | -0.001 / 0.054 / 0.044 |
| EPA / target allowed in man | 0.31 | 0.09 | -0.07 | 0.18 | 0.15 | -0.25 | 0.17 | 0.08 | 0.166 / 0.199 / 0.220 |
| EPA / target allowed in zone | -0.05 | 0.17 | 0.32 | 0.15 | 0.01 | -0.46 | -0.08 | 0.01 | 0.154 / 0.205 / 0.180 |
| EPA / target allowed, Cover 1 | 0.17 | -0.05 | -0.21 | 0.38 | 0.06 | -0.09 | 0.19 | 0.06 | |
| EPA / target allowed, Cover 3 | 0.46 | 0.14 | 0.21 | -0.03 | 0.04 | -0.50 | -0.10 | 0.03 | |
| YPT allowed to WR / TE / RB | | | | | | | | 0.17 / 0.17 / 0.18 | 7.96 / 7.29 / 5.52 (2023) |
| PPR allowed per game WR / TE / RB / QB | | | | | | | | 0.18 / 0.14 / 0.31 / 0.21 | 33.3 / 11.9 / 21.5 / 15.9 (2023) |
| opponent-adjusted PPR allowed WR / TE / RB | | | | | | | | 0.17 / 0.13 / 0.32 | |
| target rate per dropback faced WR / TE / RB | | | | | | | | 0.27 / 0.13 / 0.22 | |
| YPC allowed / rush success allowed | 0.10 / 0.29 | 0.42 / 0.53 | 0.46 / 0.20 | 0.42 / 0.43 | 0.01 / -0.11 | 0.01 / 0.00 | 0.35 / 0.28 | 0.25 / 0.23 | 4.13 / 0.395 (2023) |
| offense pass rate / PROE | 0.34 / 0.40 | 0.56 / 0.54 | 0.45 / 0.59 | 0.40 / 0.54 | 0.52 / 0.62 | 0.42 / 0.55 | 0.36 / 0.25 | 0.44 / 0.50 | |
| offense motion (FTN) | | | | | 0.68 | 0.80 | 0.66 | 0.71 | 0.453 / 0.492 / 0.551 |
| offense 11-personnel share | 0.35 | 0.67 | 0.70 | 0.70 | 0.74 | 0.25 | 0.27 | 0.53 | 0.640 / 0.627 / 0.586 |

Reading: **what a defense calls** (man / shell / family shares, nickel and
dime, box, blitz) carries about half of itself into the next season (r
0.45-0.57 pooled, 32 teams a pair, so each pair's r has a standard error of
about 0.16); **what it allows** carries almost nothing -- EPA per target by
coverage r 0.01-0.08, YPT by position 0.17, EPA per dropback 0.20, with RB
production the one allowance above 0.3. The pre-written rule put DECAY at
0.5 for a pooled tendency r in [0.3, 0.6); the measured pooled r of the ten
tendency metrics is **0.485**, so DECAY = 0.5 (a season back counts half,
two back a quarter). For 2026 that means a defense's coverage profile is
2025's shares at weight 0.5 and 2024's at 0.25 -- about half a season of
evidence, which §5.2 says is plenty for the shares (k ~45-80 dropbacks) and
nothing for the allowances.

### 5.2 Split-half reliability (`reliability.json`)

Odd weeks vs even weeks of one regular season, 2018-2025 pooled (256
team-seasons; 1,446 player-seasons with 15+ targets in each half, 523 with
25+ carries in each half; 278 QB-seasons with 100+ dropbacks in each half).
`k` is the Spearman-Brown k = n (1 - r) / r with n the mean denominator of a
half: a unit with n denominator units is believed n / (n + k). "none" means
r <= 0.02: the data does not support the signal and the profile keeps the
prior.

| signal | r (odd vs even) | units | mean n per half | k | reliability of a full season |
|---|---|---|---|---|---|
| defense man share | 0.868 | 256 | 293 dropbacks | 44.7 | 0.93 |
| single-high / two-high / zero | 0.807 / 0.792 / 0.672 | 256 | 293 | 70 / 77 / 143 | 0.89 / 0.88 / 0.80 |
| Cover 1 / 2 / 3 / 4 / 6 / 2-Man share | 0.84 / 0.85 / 0.89 / 0.84 / 0.84 / 0.79 | 256 | 293 | 54 / 53 / 35 / 56 / 55 / 80 | 0.91-0.94 |
| nickel / dime / base | 0.885 / 0.946 / 0.819 | 256 | 515 snaps | 67 / 29 / 114 | 0.94 / 0.97 / 0.90 |
| light box / heavy box / mean box | 0.759 / 0.755 / 0.707 | 256 | 515 | 163 / 167 / 213 | 0.86 / 0.86 / 0.83 |
| blitz (5+) | 0.775 | 256 | 313 | 90.7 | 0.87 |
| pressure / sack / time to throw | 0.402 / 0.336 / 0.343 | 256 | 294 / 314 / 280 | 437 / 621 / 535 | 0.57 / 0.50 / 0.51 |
| EPA per dropback allowed | 0.232 | 256 | 314 | 1,040 | 0.38 |
| EPA / target allowed in man / zone | -0.007 / 0.169 | 256 | 96 / 174 targets | none / 855 | - / 0.29 |
| YPT allowed in man / zone | 0.076 / 0.229 | 256 | 96 / 174 | 1,160 / 584 | 0.14 / 0.37 |
| EPA / target allowed, Cover 1 / 3 / 2 / 4 | 0.034 / 0.130 / 0.081 / -0.019 | 256 | 75 / 76 / 43 / 32 | 2,126 / 509 / 487 / none | 0.07 / 0.23 / 0.15 / - |
| target rate per dropback faced WR / TE / RB | 0.179 / 0.058 / 0.346 | 256 | 314 | 1,442 / 5,079 / 595 | 0.30 / 0.11 / 0.51 |
| YPT allowed to WR / TE / RB | 0.148 / 0.020 / 0.063 | 256 | 159 / 58 / 52 | 914 / none / 777 | 0.26 / - / 0.12 |
| EPA / target allowed to WR / TE / RB | 0.148 / 0.045 / 0.144 | 256 | 159 / 58 / 52 | 916 / 1,221 / 309 | 0.26 / 0.09 / 0.25 |
| catch rate allowed to WR | 0.310 | 256 | 159 | 354 | 0.47 |
| PPR allowed per game WR / TE / RB / QB | 0.144 / 0.135 / 0.377 / 0.230 | 256 | 8.3 games | 50 / 53 / 14 / 28 | 0.25 / 0.24 / 0.55 / 0.37 |
| opponent-adjusted PPR allowed WR / TE / RB | 0.150 / 0.159 / 0.332 | 256 | 8.3 | 47 / 44 / 17 | 0.26 / 0.28 / 0.50 |
| targets allowed per game WR / TE / RB | 0.233 / 0.089 / 0.382 | 256 | 8.3 | 27 / 85 / 13 | 0.38 / 0.16 / 0.55 |
| YPC allowed / rush success allowed | 0.348 / 0.300 | 256 | 201 carries | 376 / 469 | 0.52 / 0.46 |
| offense pass rate / PROE / neutral pass rate | 0.578 / 0.745 / 0.523 | 256 | 515 / 515 / 261 | 376 / 176 / 238 | 0.73 / 0.85 / 0.69 |
| offense shotgun / 11 share / motion / play action / deep rate | 0.92 / 0.91 / 0.91 / 0.64 / 0.39 | 256 (128 FTN) | | 44 / 51 / 55 / 180 / 409 | 0.96 / 0.95 / 0.95 / 0.78 / 0.56 |
| QB EPA per dropback / sack / TTT / deep rate | 0.507 / 0.448 / 0.749 / 0.447 | 278 | 238 / 238 / 222 / 216 | 231 / 292 / 74 / 267 | 0.67 / 0.62 / 0.86 / 0.62 |
| QB CPOE short / intermediate / deep | 0.292 / 0.212 / 0.026 | 278 | 109 / 45 / 25 attempts | 264 / 168 / 950 | 0.45 / 0.35 / 0.05 |
| QB EPA vs man / zone / man-minus-zone | 0.234 / 0.482 / -0.054 | 278 | 82 / 146 / 81 | 268 / 157 / none | 0.38 / 0.65 / - |
| QB EPA under pressure / no pressure / blitz / no blitz | 0.613 / 0.406 / 0.265 / 0.415 | 278 | 66 / 162 / 63 / 174 | 42 / 237 / 175 / 245 | 0.76 / 0.58 / 0.42 / 0.59 |
| player target share / air-yard share | 0.855 / 0.896 | 1,446 | 241 team targets | 41 / 221 | 0.92 / 0.95 |
| player targets per on-field dropback | 0.683 | 1,446 | 196 dropbacks | 91 | 0.81 |
| player aDOT / YAC per reception | 0.882 / 0.585 | 1,446 | 36 / 25 | 4.9 / 17.8 | 0.94 / 0.74 |
| player catch / YPT / EPA per target | 0.429 / 0.308 / 0.181 | 1,446 | 37 targets | 50 / 84 / 169 | 0.60 / 0.47 / 0.31 |
| player contested share (FTN) | 0.425 | 722 | 37 | 51 | 0.60 |
| player vertical-route share / middle-third share (the alignment proxy) | 0.673 / 0.445 | 1,446 | 37 | 18 / 47 | 0.80 / 0.62 |
| player PPR per game | 0.743 | 1,446 | 7.4 games | 2.5 | 0.85 |
| player target rate vs man minus vs zone | 0.391 | 1,445 | 65 dropbacks | 101 | 0.56 |
| player YPT vs man minus vs zone / EPA vs man minus vs zone | 0.022 / -0.012 | 864 | 17 targets | 748 / none | 0.04 / - |
| player YPT vs man / vs zone | 0.139 / 0.230 | 1,443 / 1,446 | 14 / 24 | 85 / 79 | 0.25 / 0.38 |
| player target rate under Cover 1 / 3 / 2 / 4 | 0.546 / 0.365 / 0.327 / 0.209 | 1,432 / 1,438 / 1,305 / 1,164 | 52 / 50 / 31 / 25 dropbacks | 43 / 87 / 64 / 93 | 0.71 / 0.54 / 0.49 / 0.35 |
| player EPA per target under Cover 1 / 3 / 2 / 4 | 0.084 / 0.117 / 0.174 / -0.048 | 690 / 644 / 192 / 103 | 16 / 15 / 12 / 11 targets | 173 / 114 / 55 / none | 0.16 / 0.21 / 0.30 / - |
| player YPC / rush success | 0.290 / 0.336 | 523 | 77 carries | 189 / 153 | 0.45 / 0.50 |

Reading:
- **Tendencies are real and quick to read.** A defense's man share is known
  to r 0.87 after half a season (k 45 dropbacks: ~two games), its personnel
  after ~50 snaps, its blitz rate after ~90 dropbacks. The same for an
  offense's personnel, shotgun, motion and PROE, a QB's time to throw, a
  player's target share, air share, aDOT, target rate per on-field dropback
  and vertical-route share.
- **Efficiency allowed is mostly noise at a season's length.** EPA per
  target allowed vs man is not distinguishable from zero reliability; vs
  zone needs ~850 targets (a season has ~350); per coverage family 500-2,000
  targets; YPT allowed to a position 800-900 targets (a season has ~320 to
  WRs). "This defense is bad against tight ends" (TE YPT allowed, r 0.02) is
  not a measurable thing in one season of these files. Position **fantasy
  allowed per game** is weak for WR / TE (k ~50 games -- three seasons) and
  moderate for RB (k 14) and QB (k 28); the opponent adjustment does not
  change that picture (RB 0.33, WR 0.15, TE 0.16).
- **Player splits**: a player's target rate vs man minus vs zone is a real
  trait (r 0.39, k 101 dropbacks), and his target rate under each coverage
  family is real (Cover 1 r 0.55). His *efficiency* vs man minus vs zone is
  noise (YPT split r 0.02, EPA split -0.01, 864 player-seasons), and his
  efficiency under a given family barely registers (r 0.08-0.17). The
  matchup signal that exists in these files is **who gets the ball against
  what**, not how much more he gains per target against it.
- **QB**: deep CPOE is noise (r 0.03); short CPOE weak (0.29). EPA under
  pressure is the most stable QB split (0.61, k 42 dropbacks); EPA vs man
  minus vs zone is noise (-0.05).
- Effective sample size: a season of a signal with k is worth
  2n / (2n + k) of a true reading (the last column); a half-season as the
  2026 stores hold for coverage (weighted ~455 dropbacks) believes a man
  share at 455 / (455 + 45) = 0.91 and an EPA-vs-zone allowance at
  455 / (455 + 855) = 0.35 -- and that is before the year-over-year decay in
  §5.1, which the modelling agent should treat as the ceiling for a
  prior-season-only feature.

The profiles read these k from `reliability.json`'s `k` block, one entry per
shrunk quantity, and carry the same numbers as code defaults: def_cov_share
52.7, def_pers 48.1, def_blitz 90.7, def_pressure 437, def_sack 621,
def_ttt 535, def_epa_db 1,040, def_scramble 10,849 (r 0.03: a scramble rate
allowed is noise), def_box 165, def_allow_target 866, def_allow_game 36.1,
def_run 423, off_rate 197, qb_rate 262, qb_depth 461, qb_ttt 74, qb_deep 267,
pl_target 101, pl_snap 90.8, pl_adot 4.9, pl_yac 17.8, pl_share 41,
pl_game 2.5, pl_carry 171. The first version applied the blitz k to
pressure, sack, TTT and EPA per dropback, the personnel k to box shares and
the player target k to aDOT and YAC (review F4); every `k` stamped on a
profile rate now is the measured one for that quantity. The same table
pooled over **2018-2024 only** sits beside it as `k_fit_only` (review O2):
a model validated on 2025 can take its k from a pool that never saw 2025
(the two differ little: def_cov_share 51.4, def_blitz 86.8, def_allow_game
48.7, pl_target 96.9, pl_snap 92.9).

Figures that moved on the re-run after the fixes (everything else in §5.1
and §5.2 is unchanged to the third decimal): player targets per on-field
dropback r 0.683 -> 0.684, k 91.0 -> 90.8 (the numerator now counts only
targets on charted plays); player target rate under Cover 1 r 0.546 ->
0.538, k 43.4 -> 44.5; vertical-route share now measured on 2023-2025 only
(544 player-seasons, r 0.676, k 17.6); `def.scramble` added (r 0.028).

### 5.3 2026: no participation file, and the plan that follows from §5.1-5.2

nflreadr documents participation from 2023 on as "provided after all
post-season games are completed" and not updated during the season; no
`pbp_participation_2026.csv` exists (checked 2026-10-23). So for every 2026
game: coverage, man / zone, routes, pressure and NGS personnel come from
**2025 at weight 0.5 and 2024 at 0.25**, shrunk by the measured k toward the
league prior, and the profile says so (`d_cov_n` ~455 weighted dropbacks,
`d_games_cur` the 2026 games behind the FTN and pbp figures). What IS
in-season for 2026: FTN's box count, pass rushers (blitz), motion, play
action, screen, RPO, catchable / contested / drop, and everything in pbp
(targets, depth, location, EPA, personnel-free efficiency). The 2026 FTN box
and blitz are the same charting as 2023-2025's NGS fields (99.8-100%
agreement, §2.1), so a 2026 blitz rate is comparable to a 2025 one.

### 5.4 Possible 2026 scheme changes (`scheme_change_2026.json`)

Rule as first written: a team is flagged when its 2026 weeks 1-6 FTN blitz
rate or box figure sits more than two standard deviations of the
2025-weeks-1-6-vs-2024-full change away from its 2025 full-season figure.
**Changed after the first run and disclosed here**: the first run flagged 22
of 128 team-metric cells, almost all box counts in one direction, because
the whole league's box reads heavier in 2026 weeks 1-6 than in 2025
(league mean +0.17 defenders, light-box share -0.094) -- a league-wide or
charting drift, not 15 scheme changes. The rule now centres each team's
change on the league's change in the same window and uses the centred null
spread. Flags (|z| > 2; 128 cells, so ~6 would be flagged by chance; 8 are):

| team | metric | 2025 | 2026 wk 1-6 | change vs league | z |
|---|---|---|---|---|---|
| MIN | blitz | 0.460 | 0.704 | +0.195 | 2.8 |
| WAS | blitz | 0.266 | 0.457 | +0.141 | 2.0 |
| ATL | blitz | 0.352 | 0.260 | -0.141 | -2.0 |
| CLE | heavy box / mean box / light box | 0.125 / 6.42 / 0.577 | 0.054 / 6.24 / 0.665 | -0.090 / -0.34 / +0.182 | -3.3 / -2.3 / 2.3 |
| BUF | heavy box | 0.049 | 0.139 | +0.072 | 2.7 |
| LV | light box | 0.620 | 0.701 | +0.175 | 2.2 |

Minnesota's blitz rate (0.70 of dropbacks with five or more rushers) is the
one figure far outside the null; the rest sit at the two-sigma edge and
should be read as "worth a look", not as established changes. Coverage
shares cannot be checked for 2026 at all (no charting), which is the real
limitation: a team that switched from single-high to two-high in 2026 is
invisible to this engine until the 2026 participation file ships.

## 6. Limitations and approximations (what the data cannot say)

### 6.1 Missing outright
- **2026 coverage, man/zone, routes, pressure, NGS personnel**: absent until
  the 2026 participation file (after the postseason). The 2026 rows read
  2024-25 for these; `d_cov_n` says how much.
- **Alignment** (slot vs wide, X / Z, field / boundary) is in no free source.
- **Individual cornerback assignments** ("vs this corner") do not exist in
  these files. Coverage is charted per play for the defense, not per
  receiver-defender pair.
- **Routes on untargeted snaps**, hence targets per route run. The route
  column exists only for the targeted (primary) receiver.
- **Pre-kickoff Sleeper projections** for past weeks (§6.2).

### 6.2 Sleeper's "projection" is a post-week snapshot
Every row of a past week on `api.sleeper.com/projections/nfl/<season>/<week>`
carries the same `updated_at`: Tuesday 04:00 UTC after that week's games
(week 1 2026: 2026-09-15 04:00 UTC; week 6: 2026-10-20 04:00 UTC). The
current week's rows update live (week 7's `updated_at` 2026-10-23 00:50
UTC, during Thursday night's game). Against the repo's genuine pre-kickoff
capture of 2026 week 1 (`research/data/feeds/proj_2026_1.json`, taken
2026-09-12 15:50 UTC), the post-week rows differ for **96 of 249 players'
PPR (mean |diff| 0.22, max 4.27), 28 of 32 QBs' passing yards (mean |diff|
14.7, max 49.0), 50 of 217 receiving-yard lines (mean 0.46, max 17.7), 37 of
192 carry lines (max 4.9)**. The `sl_proj_*` columns for 2023-2026 (the
repo's `player_weeks` table and this fetch alike) are therefore Sleeper's
*revised* lines and cannot be proven pre-kickoff; most are unchanged, some
moved after injuries or inactives were known. A clean pre-kickoff line exists
only where the app froze it (`nfl_props_<season>_wNN` in the server's deep
store since 2026 week 6, the `sleeper` block; `research/data/feeds/` for 2026
weeks 1-2). The model should treat `sl_proj_*` as "Sleeper's line, possibly
revised by Tuesday" and the modelling agent should prefer the frozen
captures wherever a week has one.

### 6.3 Charting conventions
- Man share and family shares shift between seasons (§2.1); compare within a
  season or to the cutoff's league prior.
- 2021-22 coverage is charted on attempts only (no sacks / scrambles), so
  per-coverage dropback counts and sack rates differ in kind from 2023+.
- 2022 FTN box / rusher counts disagree with NGS's 41% / 23% of the time;
  from 2023 they are the same numbers.
- `defenders_in_box = 0` in 2024-25 is treated as uncharted.
- The nominal base front is **inferred** (what the unit lines up in against
  heavy personnel with four DBs); it is not a declared scheme.

### 6.4 Proxies, labelled in the data
- `tgt_per_db` (`kind: proxy`): targets per dropback the player was on the
  field for. Reliable (r 0.68) and the best available stand-in for targets
  per route run, but a snap is not a route (a blocking tight end counts).
- `alignment_proxy` (`kind: proxy`): the share of a player's targets thrown
  to the middle third (nflfastR `pass_location`) plus his route-family mix.
  Split-half r 0.45 (k 47 targets) -- it is a stable trait of the player --
  but it is **not alignment**: a wide receiver running digs reads
  "middle". It is never called alignment anywhere in the data or the profiles.
  Checkable against nothing free; the report makes no claim that it tracks
  slot rate.
- `q_*` describe Sleeper's QB1 by projected attempts from the TUESDAY
  post-week snapshot (not provably pre-kickoff; review F6 / O1); `qp_*` is the
  pre-kickoff pick (frozen capture dated before the game, else the last
  starter as of the cutoff). Neither is "the quarterback who played".
- The opponent adjustment compares a game with the offence's other games as
  of the cutoff; early in a season it leans on prior seasons' games at their
  weights.

### 6.5 Scope
- Regular season only in the stores; postseason games are not read.
- QB kneels and spikes are not snaps (and a kneel-only appearance is not a
  game played); two-point tries are dropped; laterals
  credit the first receiver / rusher only (the 0.8% receiving-yard
  disagreement with Sleeper).
- Positions are players.csv's current position for all seasons (a player who
  changed position is filed under the latest one).

## 7. Guards (`tests/guards_nfl_matchup_data.py`, 149 checks, ~1 s)

A synthetic season is written as the three nflverse files (only the columns
the parser reads; it indexes by header name) and run through the real
`build_season`, `build_ids` and profiles; the review fixtures add a fourth
pair of teams, a traded receiver, a kneel-only back, a lineman on the
on-field list, a box of 0, a game charted in the 2018-2022 vocabulary with
an unknown route string, a season built without participation, a two-game
week and a k-table fixture. Pinned by definition:
- **Leakage**: a profile as of week w holds nothing from week >= w (weeks
  1, 2, 3 each checked); `Snapshot` refuses tallies holding the cutoff week
  and refuses a "prior" season later than the cutoff; **the dataset
  builder's own `week_rows`** never lets the week's games into the snapshot,
  and a game's rows are identical with the week's other game removed from
  the store (F3).
- **Same-scope rates** (F1): a receiver traded between weeks reads 1 target
  vs man of 1 man dropback for his new team (not his old team's targets over
  the new team's dropbacks), 2 of 3 with no team filter; every per-dropback
  rate across every player and scope lies in [0, 1]; usage says
  "team-scoped" and receiving says "all his teams".
- **No-participation season** (F7): with the current season built without
  participation, the per-dropback rate equals the prior seasons' alone,
  `n_current` reads 0, and target share still counts the current season.
- **Vocabulary** (F2): the 2018-22 route names decode to themselves,
  PREVENT is its own code in family "other", SINGLEBACK / I_FORM / EMPTY /
  UNDER CENTER decode, an unknown string is reported in `unknown` and
  nothing else is, `ROUTE_ERA` is 2023 and a pre-2023 season contributes no
  route mix.
- **k per quantity** (F4): with a fixture table, pressure / sack / TTT / EPA
  per dropback / scramble / blitz / box / personnel / coverage / aDOT / YAC /
  catch / QB TTT / deep / EPA each carry their own kind's k, the profile
  names the table, `reload_k()` restores the shipped one.
- **Freshness** (F5): a file with no record is stale, an older Last-Modified
  is stale, an equal one is fresh, a missing file never is, inside
  `max_age_s` no HEAD is sent, an unanswerable upstream keeps the file;
  `default_seasons` reads the previous season until October.
- **Personnel parse**, **coverage families** (PREVENT in "other"),
  **fantasy** (13.7 / 16.7 / 19.7 and 26.0 on hand lines), **shrinkage**
  direction and limits, **crosswalk** on four real players plus a shared-name
  resolution by team (M5), the **build** (two-point try and deleted play
  dropped, 100% joins, kinds, decoded codes, NA handling, the on-field
  vector with its target slots, box 0 -> NA, lineman excluded, kneel-only
  excluded, a scramble credited to the QB, RECENT = 4, the FTN credit).
- **Profile arithmetic**, hand-computed on the fixture: man 2/3, nickel
  3/5, inferred 3-4 front beside a 4-2-5 most-played, blitz and pressure
  1/3, WR YPT allowed 27.5, man YPT 7.5, deep YPT 40, PPR allowed to WR 13.5
  / RB -1.6 / QB 6.2, run defense 2.0 YPC with one stuff, offense pass rate
  0.6 / play action 1/3, QB vs man 2 dropbacks, WR target share 2/3 and
  target rate vs man 1/2 vs zone 1/1, aDOT 18.5, contested 1/2, RB box and
  direction splits, a decoy's snaps counted, DECAY and DECAY^2 weights,
  BACK = 2, the opponent adjustment's two-other-games rule and its zero on
  an offence that always does the same thing, the snap-count reader.

## 8. Integration (for the orchestrator; nothing wired here)

1. **artifacts.SCHEMA 5 -> 6** with a comment line: "6: the deep store gains
   nfl_matchup_<season>.pkl and nfl_matchup_ids.pkl (nfl_matchup_data,
   its own SCHEMA 2)".
2. **Deep job** in `app.py` beside the other deep registrations, using the
   app's existing PC-first helper:
   ```python
   def _nfl_matchup_job():
       if _deep_pc_first("nfl_matchup"):        # app.py's helper: stamps the PC request
           return deep_cache.DEFERRED
       with deep_cache.HEAVY_BUILD:
           import nfl_matchup_data
           return nfl_matchup_data.refresh()     # default_seasons(): current, + previous until October
   deep_cache.register("nfl_matchup", _nfl_matchup_job, cadence="daily")
   ```
   `refresh()` asks upstream (one HEAD per file) whether anything moved
   since the recorded fetch and rebuilds only the seasons whose files
   changed (review F5: the first version took any existing download as
   fresh forever). Operations note (review R6): the first run after
   deployment re-downloads every raw file once (no `.meta.json` records
   yet) and a daily run costs one HEAD per file -- five for the current
   season plus the two id files. The job's own payload is a summary; the stores are saved
   by `refresh` under their own keys. Prior seasons are built once (the PC
   or a one-off) and never change. On the PC: `pc_worker` calls
   `nfl_matchup_data.refresh(raw_dir=<pc dir>)` in its nightly deep pass and
   uploads `nfl_matchup_<season>.pkl` and `nfl_matchup_ids.pkl` through
   `/api/art/upload` like any deep artifact. Never in a web worker: 9 s and
   73 MB a season to build, ~120 MB RSS to hold a three-season snapshot.
3. **Guard suite**: in `tests/combo_audit_guards.py` add
   `from tests.guards_nfl_matchup_data import run as _nflm_run; _nflm_run(ck)`
   (the file sets `VIGIL_NO_BG` itself and needs no store).
4. **requirements**: none (stdlib on the server; numpy only inside the
   research script's measurement functions, imported lazily so the guards
   import the module without it).
5. **.gitignore**: nothing new (raw downloads and their `.meta.json` records
   live under the deep store's `nfl_matchup_raw/` on the PC, never in the
   repo).

## 9. Open risks

- The 2026 charting gap is structural: nothing here can see a 2026 coverage
  change. Flag-worthy FTN movements are listed in §5.4; coverage-based
  features for 2026 should be judged at the §5.1 persistence ceiling.
- Sleeper's lines in the dataset are revised ones (§6.2); a model that beats
  `sl_proj_*` on 2023-2025 may be beating a line Sleeper never offered
  pre-kickoff. The 2026 test should use the frozen captures where they exist.
- The reliability k are pooled over 2018-2025 and treat each half-season as
  exchangeable; the man-share level shift (§2.1) is within-season invariant
  so it does not touch them, but a charting change mid-2026 would.
- `read_snap_counts` joins by normalised name + team; 144 of 2,253 2026 rows
  found no PFR snap row (name spellings, players without snaps), and the
  `p_tgt_per_db_proxy` inherits that join.
- The pre-kickoff QB pick (`qp_*`) is the last starter on 97% of rows; a
  known pre-game change (an announced backup) is invisible to it until the
  app's frozen captures cover more weeks.
- `players.csv`'s position is current, not historical.
- The on-field vector keeps QB / RB / WR / TE / FB only (by the ids store at
  build time); a player missing from players.csv at build time has no snaps.

## 10. Fixes after review (`research/reviews/nfl_matchup_data.md`, 2026-10-23 04:50 UTC)

Each guard below was written first and run against the pre-review code: 19
checks failed (F1 read 2.0 targets per dropback for the traded receiver; F7
read 2.25 against a prior-only 0.75; F2-F5 raised on missing functions or
decoded OTHER / BLOWN), then the fixes were made and all pass (149 after the re-check's R1 / R2 / R4 guards, each mutation-checked against its revert).

| id | what changed | where |
|---|---|---|
| F1 (blocker) | the on-field vector now carries the player's targets by coverage beside his dropbacks; `player(pid, team=)` reads every per-dropback rate (overall, vs man / zone, by shell, per coverage) from those same team-scoped plays; usage is labelled team-scoped, receiving efficiency player-scoped | `nfl_matchup_data.py:190` (OF_TGT..), `nfl_matchup_profiles.py:170` (G_TGT_OF..), `:1119` (usage scope), `:1158` (per-coverage rate); guard `tests/guards_nfl_matchup_data.py:475` |
| F7 (model agent) | with numerator and denominator from the same charted plays a season without participation adds to neither; `n_current` on the rate says how many of this season's dropbacks are in (0 for 2026); the dataset adds `p_tgt_per_db_proxy` (targets / team dropbacks x PFR snap share, same season) and `p_tgt_per_db_n_current` | `nfl_matchup_profiles.py:1128`, `research/nfl_matchup_dataset.py:922` |
| F2 | every 2016-2022 route and formation name and PREVENT decode to their own codes; `unknown` is populated (empty on all nine seasons); route-family mixes are built from `ROUTE_ERA` = 2023 on | `nfl_matchup_data.py:119`, `:123`, `:429`; `nfl_matchup_profiles.py:486` |
| F3 | `week_rows` builds a week from a snapshot frozen before any of its games and raises if the snapshot holds them; `build_dataset` adds the games after writing | `research/nfl_matchup_dataset.py:783` |
| F4 | one k per shrunk quantity (def_sack, def_ttt, def_epa_db, def_scramble, def_box, pl_adot, pl_yac, qb_ttt, qb_deep added and applied); `k_fit_only` (2018-2024) beside the pooled table | `nfl_matchup_profiles.py:81`, `:772`; `research/nfl_matchup_dataset.py:377` |
| F5 | `fetch` records `Last-Modified` in a `.meta.json` beside each download; `_fresh` compares it with a HEAD (`remote_modified`, ledgered `NFLM-head`), `max_age_s` skips the HEAD; `refresh` skips a season none of whose files changed; `default_seasons` = current + previous until October | `nfl_matchup_data.py:324`, `:360`, `:836` |
| F6 | `q_source` = `sleeper_qb1_tuesday`; `qp_*` (14 columns) = frozen capture dated before the game (2026 weeks 1-2 Sunday / Monday) else the last starter as of the cutoff | `research/nfl_matchup_dataset.py:727`, schema `q_` / `qp_` blocks |
| M1 | header docstring states the centred scheme-change rule; the hash caveat in the header of this report | `research/nfl_matchup_dataset.py` header |
| M2 | kneels and spikes leave the on-field tallies (a kneel-only appearance is no longer a game played: 153 rows fewer) | `nfl_matchup_data.py:646` |
| M3 | `k_for` ledgers `NFLM-k-table` on a missing / corrupt table; `reload_k()` | `nfl_matchup_profiles.py:89` |
| M4 | ~120 MB RSS stated (§2.2, §8) | report |
| M5 | guards for box 0 -> NA, the on-field skill filter, RECENT, the QB scramble credit, shared-name resolution by team; the F1 / F2 / F3 fixtures | `tests/guards_nfl_matchup_data.py` |
| N1, N3, N4, N6 | wording (runs with a coverage code, decoys count as played, box-0 rows are non-scrimmage, last-4 unweighted) | report §2.1, §3, §4 |
| N5, N10 | `n_ol` readable from 2023 only; `seasons` default matches the code | `nfl_matchup_data.py` docstrings |
| N7 | Sleeper-only duplicate dropped (1 row) | `research/nfl_matchup_dataset.py:783` |
| N9 | the CSV stays a plain CSV (a comment line would break every reader); `dataset_meta.json` is the licence record | — |
| N11 | the integration snippet uses `_deep_pc_first("nfl_matchup")` | report §8 |
| N12 | FTN `starting_hash` (L / M / R) and `qb_location` (U / S / P) decoded to codes | `nfl_matchup_data.py:138` |
| O2 | `k_fit_only` (2018-2024) in `reliability.json` | `research/nfl_matchup_dataset.py:377` |
| O3 | 2026 = weeks 1-6 stated throughout | report |
| R1 (re-check) | the lineman fixture sits on a pass play too, so the skill filter is what the guard pins (mutation-checked: removing the filter fails it) | `tests/guards_nfl_matchup_data.py` (play 31, `m5_mutations`) |
| R2 | `n_current` pinned at 4 on the base fixture (and unchanged by a prior season), not only at 0 (mutation `db_cur += 0` fails it) | `f7_no_participation_season` |
| R3 | `p_tgt_per_db_proxy` labelled unshrunk with the gating rule in `dataset_schema.json` (regenerated: that meaning text only) and the builder's docstring; the dataset file is untouched (sha unchanged) | `research/nfl_matchup_dataset.py` schema / `_proxy_rate`, §4.1 |
| R4 | guards that `week_rows` raises on a contaminated snapshot, that `refresh` skips an unchanged season once its store exists, and that a corrupt k table is ledgered `NFLM-k-table` (each mutation-checked against its revert) | `r4_assertions` |
| R5 | 2023 weeks 2-5 route mixes rest on 1-4 weeks of in-era targets -- stated in §4.1; no column added (it would change the hash) | report §4.1 |
| R6 | first refresh re-downloads once, one HEAD per file per run -- stated in §8 | report §8 |

Not done, with reasons: the reviewer's suggestion to credit routes on
untargeted 2023+ throwaways (a "primary read") is not a target and is left
out; pre-2023 route mixes are excluded rather than remapped because the
remap changes the quick / intermediate split by 7-12 points (§2.1); the
`d_cov_n` on 2026 rows reads 424-522 (the first report said "~455").

Dataset after the fixes: 22,825 rows x 338 columns, sha256
`766a60ed11ee3c017141443bd114625b3d756c5bb03737dfc3148389bbef2738`. File
hashes (sha256, first 16, after the re-check minors): `nfl_matchup_data.py` `8511346956bb4ae2`,
`nfl_matchup_profiles.py` `d2007503a6d85dd5`, `research/nfl_matchup_dataset.py`
`fae01351c4fde90c`, `tests/guards_nfl_matchup_data.py` `03bee43c5b95baa5`.
