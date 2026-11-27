# Classic opponent field: the prospective protocol (registered 2026-11-27 UTC)

Registered by the commit that adds this file and `research/data/cl_field2/protocol.json`
(this file's sha256, the frozen candidates, the diagnostic thresholds). After that commit
this file is never edited: a change is a new protocol with a new name. A guard
(`tests/guards_auto_fieldcap.py`, FC18-FC21) fails if the file, the constants or the
owner's criteria drift from the registration, and the harness refuses to read a contest
when they do.

Why it exists: `research/reports/cl_field2.md`. Weeks 10 and 11 (contests 197645153 and
197882014) were read by `dk_mech` and again here, so they are DEVELOPMENT data and can
validate nothing. Everything below is judged on contests that had not locked when this
was registered: no lineup of theirs existed for anyone to read.

## 1. The validation contests

- **Which.** The DraftKings NFL classic "Millionaire" ($1M+ to first) on the Sunday main
  slate (`dk.main_slate`'s rule: the untagged classic slate of eight or more games), for
  every Sunday whose slate locks AFTER the registration commit's author time.
- **Numbering.** By ESPN's and Sleeper's count the first such Sunday, 2026-11-29, is week
  12 (the Sundays read in development, 11-15 and 11-22, are their weeks 10 and 11). The
  brief that asked for this said "week 13+". The rule here is the lock time, not a week
  number: 2026-11-29 qualifies. If the owner reads "week 13+" as a later start, dropping
  11-29 loses one contest and changes nothing else.
- **How many.** The FIRST TWO qualifying contests that have all three inputs of section 5.
  A Sunday missing an input is skipped and recorded as skipped, with the reason. No
  contest is chosen after its result is known: the order is the calendar's.
- **Once.** `python3 -m research.cl_field2_fields validate <exports dir> --contest CID
  --dg DG --week W` writes `research/data/cl_field2/validation_<CID>.json` and refuses to
  run again for that contest. It refuses the development contests, a tree whose harness,
  sampler or protocol differs from the registration, and a contest with no served board.
- **A verdict needs both contests.** One contest is reported as one contest.

## 2. The fields compared (all built by production's own sampler; no board changes)

| | What it is |
|---|---|
| F0 | The served field's replica (`dk_mech.classic_replica`): production's sampler steered onto the ownership the board stamped (`field_pct`), the board's own beta and kappa. This is "production" in every criterion. |
| F1c (PRIMARY) | The same sampler steered onto T = the noisy-optimizer share: of 10,000 exact DraftKings-legal optimal lineups (`dfs_tourney.optimal_lineups`), each solved on the `kalshi` source with every player's figure multiplied by 1 + 0.25 x a standard normal, the share holding each player. |
| F1 (secondary) | The same, steered onto 0.5 x T + 0.5 x the board's stamped ownership. |

The `kalshi` source (defined in `research/cl_field2.sources`): Sleeper's weekly line as
`nfl_props_log` froze it before each kickoff, scored by DraftKings' weights from the logged
components, each position's level set to the board's; moved, stat by stat, by Kalshi's
frozen prop-implied mean minus Sleeper's frozen mean wherever a ladder exists and reads
within a factor 2 of Sleeper's; a player in neither log, and every defense, keeps the
board's projection.

Frozen (`research/cl_field2_fields.FROZEN`, equal to `protocol.json`'s): source `kalshi`,
sigma 0.25, tau 0, alpha 0.5 (F1), alpha_c 1.0 (F1c), w 0, pool 10,000. Seeds are the
modules' constants. Chosen in sample, on two weeks, from about a hundred cells
(`choose.json`): expect the development gains to shrink.

Not registered, and why: **F2**, F1 with a share of exact optimizer lineups (the cluster).
At sigma 0.25 the pool repeats nothing; every share tried (5, 10, 20%) lowered the
collision and broke the stack distribution, and the pre-stated loss chose 0.

## 3. The owner's five criteria (unchanged: `research/reports/dk_mech.md` B.7)

Judged by `research/dk_mix_classic.owner_side` / `owner_verdict` with
`dk_mix_classic.OWNER`, the same object `dk_mech` used; nothing is re-coded. On EACH
contest, for each field, with F0 beside it:

1. At least 10 of the real 20 most-copied builds among the field's 200 most-copied builds
   at the contest's own entry count (a build with one copy never counts).
2. The median of field copies / real copies over those 20 in [0.5, 2.0].
3. The collision C within a factor 2 of real.
4. Entries in unique lineups within 5 percentage points of real; entries in 2-10-copy
   builds, and in 11+-copy builds, each within a factor 2 of real.
5. Real-top-40 ownership MAE no more than 0.5 pp worse than F0; salary mean within $200 of
   real or no worse than F0; no stack category (four stack shares, bring-back) worse than
   F0 by more than 2 pp; the real-points P50, P90 and P99 errors each no worse than F0.

**"Field calibrated"** = all five, on both contests. Said in advance: on the development
weeks F1c found 0 of the 20 builds both weeks, so criteria 1 and 2 are EXPECTED to fail.
That is the registered expectation, not an excuse: if they fail, the field is not
calibrated and `field_duplication`, `money_gate` and every dollar figure stay experimental.

## 4. The diagnostics (this study's; a separate, smaller claim)

Each on EACH contest, the field against F0 (`cl_field2_fields.diagnostics`, `DIAG`):

- **D1 field strength.** |mean real DraftKings points of the real field - the field's| is
  at least 3.0 points smaller than F0's. (Development: 28.9 -> 6.0 and 15.2 -> 2.8.)
- **D2 concentration.** The field's sum of squared any-slot ownership within a factor 1.33
  of the real field's. (Development: 1.51 against 1.53; 1.63 against 1.65; F0 0.87, 0.88.)
- **D3 ownership.** Real-top-40 ownership MAE at least 0.5 pp BELOW F0's. (Development:
  5.05 against 6.37; 5.91 against 7.25.)

**"Ownership and field strength improved"** = D1, D2 and D3, on both contests, for F1c.
F1 is reported beside it by the same three. D1 reads real points, so one week's luck is in
it: a Sunday on which the public's chalk fails can fail D1 for a correct field, and a
Sunday on which F0's gap is already under 3 points cannot pass it. Either is reported as
it falls and counted as a fail.

Also reported, deciding nothing: the collision ratio, the 8-of-9 and 7-of-9 pair shares,
salary at the cap, the naked-quarterback share, the score deficit split by
`cl_field2.deficit`, and each source's noisy-optimizer share against the real ownership.

## 5. What must exist before lock, and where it is kept

| Input | Kept by | When it is fixed |
|---|---|---|
| The served board (players, projection, salary, `field_pct`, `opt_pct`, the field's beta and kappa) | the PC's tournament build, exported as `errors/tourney-<dg>.json` on the data branch | built 85-40 minutes before the slate's lock |
| Sleeper's and ESPN's weekly lines, Kalshi's prop ladders and implied means | `nfl_props_log`, exported as `props/nfl_props_<season>_wNN.json` | each game frozen 5 minutes before ITS kickoff |
| The real field | the owner's standings export (never committed) | after the contest |
| DraftKings' draft screen (FPPG, opponent rank, status) and ESPN's start percentages | `dfs_field_capture` (new), inside the same props file under `fieldcap` | each game frozen 5 minutes before its kickoff |

The first three are everything the registered fields need, and all three were already
kept before this protocol: the candidates do not depend on the new capture landing in
time. The fourth is read by NO candidate here. It is kept so that the next development
round has inputs that cannot be rebuilt afterwards (a status tag and a start percentage
are only ever the latest).

One timing fact, stated so nobody finds it later: the props log freezes a late game's
figures at that game's kickoff, up to several hours after the slate's first lock.
DraftKings classic allows late swap, so the public can act on them too; and the board the
fields are steered from was built before the first lock. A candidate therefore sees
slightly later Sleeper and Kalshi figures for late games than the board did. This is the
same for both contests and is not corrected.

## 6. What a result permits

Nothing here changes a board. A pass on section 4 is evidence that steering the served
field onto a market-informed noisy-optimizer share makes it as strong and as concentrated
as the public's on two more Sundays; whether production's `steer_field` target changes is
the owner's decision, and a change to `dfs_tourney` is its own tested change. A pass on
section 3 would be the first calibrated classic field; it is not expected. A fail on
either is a result, is committed as it falls, and the frozen candidates are not re-tuned
on the validation contests: a new candidate is a new protocol on newer contests.
