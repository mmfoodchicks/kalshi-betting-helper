# The DFS player picker (2026-10-30)

The owner:

> "One thing I want to add to the dfs maker is a "player picker" to it, near the build section I want a few
> options such as "exclude player": (player name) if I want to exclude him from a build "force captain":
> (player) If I want to force that player to be captain. If it's a showdown, then if it's the million it let's
> me exclude players and force players into a position. Like if I wanted a team with dak, pickens, and ceedee
> lamb. It would create all lineups with those players forced into the lineup. Of course the automatic builds
> would do all available players anywhere"

What it does:

- **Three controls.** Exclude (in none of our lineups), Force in (in every one) and, on a showdown, Captain
  (every lineup's captain; a forced captain is also forced in).
- **"Into a position" means into the lineup**, in a slot his position allows. A classic lineup scores the same
  wherever an eligible man sits, so there are no slot pins; the one slot that changes a score is the captain,
  and "force captain" covers it.
- **Two places take a pick**, both the owner's own builds:
  - the lineup sheet's Run (`/api/simulate/dfs` -> `nfl_dfs.build`);
  - Build on the PC (the request queue -> `pc_worker` -> `dfs_tourney.build_nfl_classic` /
    `build_nfl_showdown`).
- **Nothing on a schedule reads a pick.** The PC's own boards, the sheet's defaults and the look-back are as
  they were.
- **The FIELD is never picked.** The public still plays the man the owner excluded, and never the man he forced
  past the depth gate. Every contest sim, field and ownership target is the unpicked build's.
- NFL only (the owner's examples). The other sports' builders are separate code.

The code: `nfl_dfs.py` (the pick's shape and checks, the sheet's search), `dfs_tourney.py` (the picked boards),
`app.py` (the routes and the queue), `pc_worker.py` (`_picked_builds`), `dfslog.py`, `dfs_hindsight.py` and
`static/dfspick.js` (the screen). The guards are `tests/guards_picker.py` (section PK of the suite); the breadth
figures in section 2 come from `research/dfs_picker_stress.py`.

## 1. Design and review

The orchestrator wrote design v1 and a Fable agent reviewed it before any code. The verdict was **AGREE WITH
CHANGES**: three BLOCKERS, six MAJOR, nine MINOR, five NOTES. Design v2 took every item and deviated in three
places, and Fable's review of v2 accepted all three with conditions and added eight restatement fixes. Fable then
reviewed the build (round 1) and an Opus agent did round 2 (section 8).

| Item | Finding | Built as |
|---|---|---|
| B1 | A +1,000 bias on `_value` alone does not force a man into the sheet's search: the fill admits a player whenever he fits the cap with nothing held back, so the owner's own shape (a quarterback and two of his receivers) completed 0 of 2,000 fills and the search answered "no valid lineup" beside a legal 124.3. | Forced men are SEATED in every fill (`_build_one(fixed=)`); the bias stays only so polish and kicks never swap them out; one deterministic cheapest start; the answer is checked to hold them, and a miss is worded "the search found no legal lineup", never "none exists". Section 2. |
| B2 | On the classic board the picked solve cannot be THE solve: its lineups set the field's ownership targets and every row's `opt_pct`. | Two solves: the first exactly as before, a second over the picked pool for our candidates only. Section 3. |
| B3 | `exclude` already means GAMES on `/api/simulate/dfs`, the overlap rule in `optimize(exclude=)` and a mask in `classic_sample(exclude=)`. | One nested body key, `pick: {exclude, force, captain}`, and `pick=` kwargs. |
| M1 | A forced captain collapsed the showdown sheet's multi-entry to one lineup, and the GPP captain gate would refuse him. | His n best DISTINCT fills (`_showdown_bests(per_captain=)`); the gate is waived for him and named. |
| M2 | Waive showdown rules by seating, never by editing `_sd_allowed` (its five rejections are pinned). | `_sd_fill(seed=)` on the sheet; on the board `entry_ok` tests only the unforced men with `got` seeded by the forced. |
| M3 | A forced man outside the simulated pool: refuse him if he has no projection; keep a man past the depth gate out of the field. | Refused with the reason. On the sheet a man past the gate's RANK is ours only (`contest_sim(field_players=)`). |
| M4 | The look-back and the hindsight record would take picked lineups as the builder's. | `picked:<objective>`, kept out of the correction fit and the objective ranking; `_BOARD` skips `_pick` boards on purpose. |
| M5 | A picked request must answer only the picked board and never trigger or consume the automatic build. | `_split_picked`: the automatic paths see the plain requests only; `_picked_builds` builds `..._pick` boards. |
| M6 | `classic_sample` already masks; forced sets need a graft. | `exclude=fo | excluded` on our draws; `graft` puts the forced men into the draws. |
| m1-m9, N1-N5 | Uniqueness and the exposure cap; identity; the sheet's contest-sim floor; waive by knob vs refuse; the queue key; no slot pins; the tab; cost; the bias never reported; and the notes. | All built; sections 2-5. |
| D1 (v2) | Request-time validation is DraftKings-side arithmetic only (`player_pool` is the heavy correlated sim and must not run in a web worker). Accepted, on condition that the tab says what was checked. | The request's answer and the tab: "checked against DraftKings' pool...; Sleeper's projection and depth chart are checked when the PC builds". |
| D2 (v2) | Identity is the name and the team, the DK id a tiebreak. Accepted, premise corrected: the auto CSV's id is the man's (on both showdown rows); DraftKings' own export's is per slot. | `_pk_find`; DraftKings' exact spelling first (the normaliser drops digits and initials), the normalised name a fallback. |
| D3 (v2) | A forced depth-gated man on a board must already be a field extra. Accepted, on condition that the universe cap never drops a forced extra and the refusal names the rule. Measured by Fable: one row more in the sampler's pool changes the field at the same seed, even masked out. | Both built. |
| R1-R8 (v2) | No man twice in a grafted row; the team cap per team, the forced punt counting toward the punt cap; the copies after the look-back correction; two builds of one slate in one cycle; `_cl_sims` for a picked build; the board link; graft breadth; the bring-back fallback. | All built. |

## 2. The sheet's search, measured

Against a brute force over every legal lineup holding the forced men and none excluded:

- **The seat.** Forced men are seated first; the quarterback first, so the stack and bring-back read off him.
- **A salary reserve on picked fills only.** A picked fill admits a man only when the open slots' cheapest fill
  still fits. (Why: an intermediate build, before the reserve, seated $22,000 of forced men and its fills -- the
  cheapest one too -- still died at the FLEX, the bring-back taken by a $4,200 tight end ahead of a $3,500
  receiver and $47,100 spent before the last slot. That trace came from the intermediate code and is not
  reproducible from the shipped code.)
- **The stack and bring-back chosen first, on picked fills.** Left to the slot order, the tight end's slot
  always took the stack, so a lineup whose bring-back had to be the tight end was never built (a receiver, a
  back and a receiver forced on three teams: no fill of 2,000 beside a legal 222.3 -- now one of the guards'
  hand cases, exact).
- **A forced bring-back seated before the quarterback** was not counted; it is now.
- **Each mechanism is guarded on its own.** The mutation run (section 7) removed the seat, the reserve and the
  bring-back recount one at a time. Each time, the guard file's brute-force check stayed exact (all 25 checks
  passed). The stress harness stayed exact on all 225 feasible picks without the seat or the reserve; without the
  recount it missed 7 of 225. On a pool small enough to brute-force, the search's deterministic cheapest start
  and its polish repair whichever mechanism is missing; how much they repair on a 300-man slate is not measured.
  So each mechanism has a check of its own (`_fill_checks`):
  - **The seat.** The cheapest start holds every forced man whenever it returns a lineup: 131 of 131 on 150
    seeded random picks. Without the seat, 35 of 139.
  - **The recount.** With an opponent's receiver forced as the bring-back, the quarterback left to the builder
    and no other catcher of that team in the pool, the search is exact at 220.0. Without the recount it hunted
    a second bring-back and answered nothing.
  - **The reserve.** At a cap of exactly the forced salaries plus the open slots' cheapest fill, with the stack
    and the bring-back off, 188, 191 and 200 of 400 random picked fills complete on three picks. Without the
    reserve, 0, 0 and 4. (The first pin read mid-range completion counts at the full cap -- 45 and 193 against
    12 and 59 -- which moved with any edit to the fill; the round-2 review proposed this one.)
- **Results**:
  - The guard file's hand cases are exact, and so is the unpicked search, lineup for lineup.
  - `research/dfs_picker_stress.py classic` draws random picks on the suite's own 34-man optimizer pool: one to
    four forced, up to three excluded, all three objectives. Seed 7, 150 picks: 81 feasible, every one exact; 69 infeasible, every one refused. Seed 11, 300 picks:
    144 feasible, every one exact; 156 infeasible, every one refused. Nothing came back short, and no lineup turned up where the brute force found none.
  - **The showdown sheet.** A forced man tried as captain took the bias at 1.5x and outranked every other
    captain; the bias now counts once wherever he sits.
  - `research/dfs_picker_stress.py showdown` does the same on the guard file's 24-man game: one to three forced,
    a forced captain a quarter of the time, up to four excluded, the projection objective, three entries. Seed
    1, 60 picks: 60 exact. Seed 2, 120 picks: 120 exact (every random pick on this game was feasible). These run at twice the unpicked search's fills a captaincy, which
    every picked build now draws. At the unpicked 400 fills an earlier probe on a different game came out one
    pick in 60 0.2 points short, where the unpicked search was exact on all 60.

The sheet's card:

- carries the corrected projections: the copies are taken after the look-back correction, which rewrites
  `proj` and replaces `arr`;
- meets the unpicked card's field: the field pool is passed apart, and the floor is pinned to the unpicked
  optimum, so on the classic sheet the field hash is equal (without the pin the hashes differ, measured by Fable
  on the guards' own slate). The showdown sheet's contest sim has no hash, and its floor comes from its unpicked
  search, which is unseeded: it meets the unpicked card's field when the two searches agree on their best;
- names the best lineup without the picks, the rules the picks override, and the uniqueness clamp
  (`uniq` <= 9 - forced).

**Cost of a picked Run** (in the web worker, inline as the unpicked Run is). The classic sheet adds one unpicked
`optimize` for the field's bar. The showdown sheet adds a full unpicked `optimize_showdown_multi` plus twice the
fills on every captaincy when no captain is forced. Measured by the round-2 reviewer in this container (its probe
`p4_cost.py`, whole Runs, one process; not re-run here): a 12-game synthetic classic slate (192 players past the
depth gate, 20 lineups, a 3,000-lineup field) took 42.2 s unpicked against 48.5 s excluding one man and 17.0 s
forcing a quarterback and two receivers under the ceiling objective, and 8.0 s against 11.4 s and 13.4 s under
projection; the guard file's showdown game, 5 entries, 3.4-4.0 s against 3.9-4.7 s. Under projection the two
are not like for like: the unpicked and exclude-only Runs built 3 and 2 of their 20 lineups, the forced one all
20. The classic ceiling Run's 42 s is the unpicked sheet's own; Render's core was not measured.

## 3. The boards

**Classic.**

- The first solve, the field, `opt_pct` and the chalk are the unpicked board's exactly, the field's whole receipt
  included (the guard compares `field_model` unrounded, and with nothing excluded our own draws are the unpicked
  board's too).
- A second solve over the picked pool (the excluded and the unforced field-only out, every forced man in,
  `_SAFE_FORCE` on the forced rows over the generation worlds) gives our candidates. The steady columns are
  solved the same way at twice the count, their quarterback-catcher pairs agreeing with the pick, each forced man
  carrying 4 x `_SAFE_FORCE`.
- **Every pair must fit beside the forced men** (`_safe_pairs`' `fits`). The board's solver holds one tight end
  (its FLEX takes a back or a receiver, as the sheet's), so a tight-end pair beside a forced tight end kept one
  of the two. On the guards' slate, forcing a backup tight end, 65 of the 423 picked steady answers dropped the
  forced man. The `holds` mask kept them off the board, so they were wasted columns, not wrong lineups. Since the
  4x bonus below, the solver drops a misfit pair's catcher instead, so `fits` no longer changes any board count; it
  keeps a quarterback's columns on pairs that can be built. The final mutation run found it unguarded for that
  reason, so the pairs are pinned directly (`_safe_pairs`, extracted unchanged: old and new `safe_lineups` agreed on
  48 of 48 random pools, nothing forced and three kinds of picks).
- **The forced bonus is four times a pair's** (round-2 review, MAJOR 5). A pair's catcher carries `_SAFE_FORCE`
  too, so when a stack pair and the forced set overran the cap the exact solver dropped a forced man as readily
  as the catcher: three dear forced non-quarterbacks ($24,700) left 153 of 360 picked steady answers without one,
  in the review's probe. At 4x, dropping a forced man costs more than dropping the catcher or switching
  quarterbacks. Every answer of both picked solves now holds the pick on the guard's three picks, that one
  included (`steady_held == steady`).
- The board stamps the receipt: `pick.second_solve` / `second_solve_held`, `steady` / `steady_held`, `grafted`,
  `allowed`. Every listed row says where it came from (`source`: chalk, steady, hindsight, second solve, picked
  steady, drawn).
- **Draws and the rulebook.** The draws are masked of the excluded and grafted with the forced (`graft`: never a
  man twice, never over the cap, never displacing another forced man; a forced quarterback's best ALLOWED receiver
  replaces the DEAREST free receiver, so the fewest grafted rows go over the cap), held to the draw count after
  the rows that cannot be listed are dropped. (Round 2, MAJOR 6: forcing a quarterback and excluding his best
  receiver, the review's probe had 2,395 of 3,909 grafted rows holding the excluded receiver, and 1,882 of the
  3,187 candidates it scored could never be listed. With the fix: 0 of 2,487, every grafted row listable, and
  `allowed` up from 737 to 1,548 -- round 3's re-run.)
  `classic_allowed(forced=)` binds the free slots: the team cap per team, a forced punt counting toward the punt
  cap with only his role waived, a forced field extra ours -- a field extra gated by the depth chart's RANK only;
  one gated off the depth chart (practice squad, inactive) is refused, as the sheet refuses him (round 2, MAJOR 4:
  the board built him in with a garbled waiver).
- Every listed and portfolio lineup holds every forced man and no excluded one. A pick that leaves no lineup we
  may enter (the guard's case: a forced quarterback whose catchers left are all past the depth gate) is refused
  naming the house rules, never published as a board of empty lists.

**Showdown.** `entry_ok` and `cpt_ok` narrow what we may enter. The universe, the worlds, the field's weights and
chalk are the unpicked board's, and a picked build scores fewer rows.

- **The universe cap.** The field is held to 900,000 lineups by dropping the least-projected field extras. A
  forced man the unpicked board drops is refused, naming the cap. (Round 2, MAJOR 3: the first build dropped the
  next extra instead -- a different universe and so a different field; in the review's probe 593,816 lineups
  became 592,674, with other shares and chalk. On the guard's own slate the cap binds, and the guard forces the
  very extra the unpicked board drops.)
- **Forced extras** are gated by the depth chart's rank only; one a roster lookup gated ("not on any roster
  Sleeper knows", a roster status) is refused, as on the sheet.
- **Waivers.** A forced FLEX breaking a construction rule is named beside a forced captain, and since round 2
  beside a FREE captain too, for the lineups listed (a forced defense beside the other team's quarterback at
  captain was listed without a word; the sheet names it), with every captain it was waived beside, four at most
  (round 3; the sheet's waivers too).
- A pick that leaves no lineup we may enter is refused the same way.

**Measured on synthetic slates** (probe scripts in the build session, not committed: one run each, in this
container, with nothing else running, at 02:50 UTC on the build before Fable's review fixes -- none of those
adds a stage):

- Picked boards at the unpicked board's seed keep every player's field share, best-lineup share and casual
  share, and the extras, exactly.
- Classic: the unpicked board 50 s; four picked boards 57-58 s each, 7.3-7.9 s of it the second solve and the picked steady columns (`timings.pick_s`); a fifth pick refused as outside the pool.
- Showdown: the unpicked board 63 s; four picked boards 49-51 s each (a picked build scores fewer rows).
- On the PC the second solve costs about the first solve's time again (`timings.optimal_s`), and `_cl_sims`
  counts it when it decides whether a build near kickoff can afford the full worlds.

## 4. The queue and the PC

- One request per slate; the latest press, picked or not, replaces the one queued, and the tab says so.
- The request is checked against DraftKings' pool by arithmetic: the names, their status, the roster shape,
  a defense facing a forced offense, a forced quarterback with every catcher excluded, both teams on a
  showdown, the salary with the open slots at their cheapest (a lower bound: the stack and the defense rules,
  and on a showdown the both-teams rule, are not read there).
- Sleeper's side (a projection, the depth chart) is checked when the PC builds, and its refusal shows beside
  the request (`queued_attempt`).
- `_picked_builds` builds the `..._pick` board first in each cycle; the schedule then builds the automatic board
  on its own reasons, and a scheduled window is consumed by the plain build only. A picked showdown keeps the
  plain path's window: a slate more than eight days out, or one that is not a one-game showdown, is reported and
  never built (the first version fetched, simulated an unprojected week and refused, every cycle).
- **A refused press is not rebuilt every cycle** (round 2, MAJOR 2). Most picker refusals come after the slate's
  simulation -- "no candidate lineup holds your picks" after most of a classic build -- and each cycle repeated one
  ahead of the scheduled boards. The PC now tries a press the PICKER refused ("player picker: ...") again every
  six hours (`_PICK_RETRY_S`; a Tuesday's "no projection" can heal), or after half the time left to lock when that
  is sooner, or at once when the owner presses Build again, and the tab says so. Any other refusal (a roster
  lookup down, no pool posted yet) is tried again next cycle, as the plain builds are: six hours of a Sunday
  morning's outage would have held the pick past lock (round 3). A request gone takes its refusal record with it.
- The picked board is served on `/api/dfs/tourney?dg=&pick=1`; the plain answer points at it; the boards-on-file
  list labels it "Your build: ...".

## 5. The screen

`static/dfspick.js`:

- **Where it sits.** On the sheet, a picker block directly under the CSV box (not beside Run, as design v2 said:
  the CSV is its pool). A paste, a picked contest's pool and a loaded or dropped file each redraw it at once (round
  2, MAJOR 1: it showed the last pool, or none, until a Run). Paste the header row with the players: the picker
  reads the columns by name. On Build on the PC, the same block under the slate dropdown: its pool is DraftKings'
  slate, fetched once a slate; switching slates clears the picks until the new pool (and its saved picks) loads,
  and a Build never carries picks made on another slate's pool (round 2, m4). A Build pressed while the pool loads
  waits for it and its saved picks (round 3: it went out plain and replaced a queued picked Build).
- **Choosing.** A search over the pool (name, position, team, salary, DK status or "status unknown"), with
  Exclude / Force in / Captain. The picks show as chips, with a one-line sum. A captain is forced in with his
  captaincy; choosing another captain takes the old one out with it (force him again to keep him at FLEX).
- **Results.** The result and the board say what they were built under, and which house rules the picks
  override.
- **Safety and storage.** No inline handlers; every name escaped. Per-slate localStorage, a convenience only.

## 6. Guards (section PK, tests/guards_picker.py)

Twenty-nine checks, hermetic (DraftKings, Sleeper's pool and the depth gate are stubs; the boards and the queue
live in a temporary directory). The boards' checks need numpy and say so when skipped (CI's runner has none); the
node harnesses say so when node is missing.

- **The pick's shape and refusals** (1-3): its own key; every refusal with its rule named, never an empty list of
  names; the board's defense rule its own; the tight-end rule said as the builder's (DraftKings allows a second at
  FLEX); identity on both CSV shapes, and a name the normaliser would merge resolving exactly.
- **The sheet's search** (4-8): equal to a brute force under all three objectives and on the showdown sheet
  (the hand cases, 222.3 among them); the seat, the recount and the reserve each pinned on their own (section
  2); the bias on marked copies only; `_sd_allowed`'s five rejections untouched.
- **The sheet end to end** (9-11): every lineup holds the pick, with the corrected projections; the classic
  field hash is the unpicked card's; a man past the depth RANK is in and named, one ruled out for anything else
  is refused.
- **The boards** (12-18):
  - the rulebook binds only the free slots;
  - the graft never doubles a man, never displaces a forced one, never runs over the cap, replaces the dearest
    free receiver and never brings an excluded one, and the build passes the exclusion;
  - a picked classic board keeps the field exactly (the whole `field_model` receipt, unrounded, and with nothing
    excluded our own draws too);
  - every answer of its second solve and its picked steady columns holds the pick, on three picks (the dear
    forced non-quarterbacks among them), and a listed row comes from the second solve;
  - the refusals: outside the pool; off the depth chart; no lineup we may enter;
  - a picked showdown board keeps the universe, the worlds, the field's weights and chalk, lists some entries and
    every one holds the pick; it refuses the extra its universe cap drops, a roster-gated extra and an empty
    board;
  - forced-FLEX waivers beside a free captain are named.
- **The routes, the queue and the PC** (19-25):
  - the Run's two `exclude`s;
  - the request's check (a crash fails it by name) and its answer;
  - the picked board served on `?pick=1`;
  - the schedule never reads a pick, and one cycle builds the picked board and then the automatic one, the
    window consumed by the plain build only;
  - the picked showdown's window and the second solve in `_cl_sims`;
  - a refused press retried every six hours or on a new press, never every cycle.
- **The readers and the screen** (26-29):
  - `picked:<objective>` out of the look-back's fit and ranking, `_BOARD` skipping `_pick`;
  - the wiring;
  - the picker's logic under node, a hostile name written escaped;
  - the picker following the page under node with the page's own listeners: a paste or a programmatic fill
    redraws it, a new captain takes the old one's forced entry, and Build on the PC never sends another slate's
    picks.

## 7. Mutation run

`research/dfs_picker_mutants.py` puts one bug back at a time: a bug a review found, or one a guard exists to stop.
It runs the guard file on a COPY of the tree, and a mutant counts as caught only when the guard file fails on the
check named for it. A run that fails under another check's name, or never runs a check (killed by a signal), is not
caught. **66 of 66 caught**:

| File | Mutants | Which |
|---|---|---|
| `nfl_dfs.py` | 18 | no seat, no reserve, no pre-pick, no bring recount, bias in values, captain bias 1.5x, one entry per captain, field picked, exposure caps forced, no uniq clamp, copies before adjust, rank gate refused, dst rule same on board, normalised first, ref unpinned, no salary bound, pk_what holding only, one captain named |
| `dfs_tourney.py` | 23 | team cap scalar, punt role kept, graft keeps dups, field draw masked, no holds mask, extras added, cpt any, cap drops forced, second solve unbiased, steady ignores pick, steady pairs unfit, graft catcher cheapest, entry_ok tests forced, sd empty published, cl empty published, cl off-chart extra kept, sd roster extra kept, steady bonus 1x, graft brings excluded, graft call unexcluded, free captain unnamed, fits WR 5, fits no RB+WR |
| `pc_worker.py` | 9 | schedule sees picks, picked consumes window, picked sims as plain, cl_sims ignores picked, picked sd no window, refusal every cycle, throttle any refusal, no lock cap, refusals kept |
| `app.py` | 4 | pending by plain, no request check, route reads exclude, route serves plain |
| `dfslog.py` | 1 | dfslog no tag |
| `dfs_hindsight.py` | 1 | hindsight reads picked |
| `static/dfspick.js` | 7 | js raw name, js raw banner, js captain not forced, csv input ignored, pc payload any slate, old captain kept, ready ignores pending |
| `static/app.js` | 3 | js bare exclude, file fill not redrawn, build ignores loading |

The final run used four copies of the code before the `_safe_pairs` extraction, which was verified identical (old
and new `safe_lineups` agreed on 48 of 48 random pools); the guard file has only gained conditions since. The three
steady-pair mutants ran on the final code.

What the runs found along the way:
- **Three mechanisms unguarded.** The seat, the reserve and the bring-back recount each passed the whole guard file
  when removed alone: the cheapest start and the polish repaired the damage on a small pool (section 2). Each now has
  its own check (`_fill_checks`). A worker restart had first made these three look like kills; run alone, they were
  real gaps.
- **An equivalent mutant.** Removing `graft`'s distinct-rows filter changed nothing, because the `has` masks already
  keep a man out of a row that holds him. The mutant now removes the protection and the backstop together, and is
  caught.
- **A crash under the wrong name.** With the request check removed, the route raised and the whole route group failed
  under its first check's name. The request now runs under `_safe`, so the failure lands on its own check.
- **`fits` hidden by the 4x bonus** (section 3). Its pairs are now pinned directly (`_safe_pairs`), every clause:
  the tight end, four receivers, six backs and receivers.
- **Reliability.** A killed or silent guard run now reads ERROR, never MISSED. Each copy's original file is kept
  outside the tree and restored on the next start.

## 8. The build reviews

**Round 1, Fable** (a tree snapshot at 02:45 UTC): **AGREE WITH CHANGES** -- one BLOCKER, five MAJOR, seven
MINOR, four NITs. Every item was taken.

| Item | Finding | Done |
|---|---|---|
| B1 | The main suite was red on a stale pin: the boards-on-file link the picker moved. | Repinned to the new link (the clause kept); the suite run to its exit code. |
| M1 | Guard 13 was blind to the second solve. With `_SAFE_FORCE` 0 it still passed, and the best listed top-1% figure fell from 3.17 to 2.07 on the guards' slate (Fable's measurement). | The board stamps its receipts (`second_solve`, `second_solve_held`, `steady`, `steady_held`, `grafted`, `allowed`, a `source` per row); guard 14; mutants. Pinning the receipts found the tight-end pair conflict (section 3), now fixed. |
| M2 | Empty picked boards were published, and guard 15 passed them. | Both builders refuse; guards 15 and 16 require listed rows and test the refusal. |
| M3 | A picked showdown skipped the plain path's window. | The window is mirrored; guard 22. |
| M4 | The mutation list missed the solver. | Thirteen mutants added (section 7). |
| M5 | The docs were missing. | This report and the CLAUDE.md paragraph. |
| m1 | The graft's catcher replaced the cheapest incumbent. | It replaces the dearest now; guard 12. |
| m2 | The sheet's picker sits under the CSV box. | Said in section 5. |
| m3 | The pool was refetched on every redraw. | Skipped when the pool is loaded. |
| m4 | The picker needs the CSV's header row. | Said in the empty-pool note and section 5. |
| m5 | The cost of a picked Run was not stated. | Section 2 and the `_showdown_bests` docstring. |
| m6 | Field identity was checked on rounded figures. | Exact keys (guards 13 and 16). |
| m7 | Some report figures lacked provenance. | Seeds and counts stated, from a committed harness; the intermediate trace marked as such; the board timings' provenance stated. |
| n1-n4 | The bound's comment, the inline links, a docstring clause, the commit message. | Done; inline links accepted as is; done; supplied for round 2. |

**Round 2, an Opus agent** (Fable out of usage credits; the owner chose Opus for this round). It reviewed the
local commit at about 10:00 UTC: **AGREE WITH CHANGES** -- six MAJOR, six MINOR, six NITs, none a wrong lineup on
any list (the final masks held in every build it ran). Before reviewing, it verified a set of facts on its own
copy: the default path is byte-identical to the previous commit (both boards, all three sheet objectives and the
showdown sheet, old modules against new, field hashes included), the four stress figures, `_fill_checks` under
the three mutants, 26 of 26 guards, and no XSS in `dfspick.js`. Every item was taken:

| Item | Finding | Done |
|---|---|---|
| MAJOR 1 | The sheet's picker never showed the pool after a contest pick, a paste or a file load, only after a Run. | Redrawn on every fill (section 5); node-guarded with the page's own listeners. |
| MAJOR 2 | A refused picked request was rebuilt every cycle until lock. | Retried every six hours or on a new press (section 4); guarded. |
| MAJOR 3 | The showdown board's field moved when the universe cap would drop a forced extra. | Refused instead (section 3); the guard forces the very extra the unpicked board drops. |
| MAJOR 4 | The boards accepted a forced extra the gate removed for a reason other than his rank. | Refused on both boards, as on the sheet; guarded on both. |
| MAJOR 5 | Picked steady columns dropped forced men when a stack pair overran the cap. | 4x bonus (section 3); the review's pick is now a guard case. |
| MAJOR 6 | The graft brought in an excluded catcher. | Never now, and only listable rows take the budget; guarded. |
| m1, m5, m6, n4 | Doc claims: the recount's 7 misses; cost; the showdown sheet's field; "two swaps". | Corrected here and in CLAUDE.md. |
| m2 | The reserve pin was brittle. | The review's floor-cap pin (188/191/200 against 0/0/4). |
| m3 | A forced FLEX's waiver beside a free captain went unnamed on the showdown board. | Named (`_sd_free_captain_waivers`); guarded. |
| m4 | A fast Build after a slate switch could send the previous slate's picks. | Cleared on the switch; the payload checks the slate; node-guarded. |
| n1, n2, n3, n5, n6 | An empty name list in a refusal; "no lineup ... carried"; an unescaped error; the old captain kept forced; the picked view's none text. | All fixed; n1 and n5 guarded. |

**Round 3, the same Opus agent**: **AGREE**. All 18 round-2 items are fixed; it re-ran the probe that had found
each one, and each passed:
- A3's steady columns read 336 of 336 (they had read 207 of 360).
- A1's graft held the excluded receiver in 0 of 2,487 rows.
- A5 is refused.
- Three cycles start one build.

The unpicked path is still identical to the pre-picker modules. Each new guard fails when its fix is reverted, and
none passes vacuously. It left one MINOR and five NITs, none blocking. All six were taken:

| Item | Finding | Done |
|---|---|---|
| r3-1 | The six-hour throttle applied to every refusal, a roster outage too, which could hold a Sunday pick past lock. | Only the picker's own refusals wait, never past half the time to lock (section 4); guarded. |
| r3-2 | The MAJOR 6 note said "all of them scored". | The probe's figures instead (section 3). |
| r3-3 | The projection cost figures were not like for like. | Said (section 2). |
| r3-4 | `pk_refused` was never pruned. | A request gone takes its record with it; guarded. |
| r3-5 | A Build pressed while the PC picker's pool loaded went out plain. | It waits for the pool (`dfsPickReady`); node-guarded. |
| r3-6 | The free-captain waiver named one captain. | Every captain, four at most, on both surfaces; guarded. |

**Round 4, the same Opus agent**: **AGREE**. All six leftovers are fixed in the final code, checked on its own
probes, not the guard's:
- A "no pool posted" refusal was retried every cycle, and nothing was recorded.
- A picker refusal 6,300 s before lock was retried after exactly 2,100 s.
- A redraw mid-load reuses the one fetch, and Build waits for the slate's saved picks.
- The waivers read "beside captains QA, R1, W1".

The picked path is the only Python touched, so the unpicked path's identity from rounds 2-3 holds. Two NITs were
left, agreed as non-blocking and **not fixed** in this change:
- **r4-1.** When the server's request queue cannot be read, `_tourney_requests` answers `{}`, which prunes every
  refusal record, so each refused pick is built once more on the next good cycle. That costs one build after an
  outage. The fix, when wanted: have it signal the failure and skip the prune.
- **r4-2.** If the PC picker's pool fails to load, Build goes out plain and can replace a picked request already
  queued for that slate. The box says the pool did not load, and the request's answer says it replaced one. The
  fix, when wanted: hold the Build on that slate's saved picks and say so on the Build line.
