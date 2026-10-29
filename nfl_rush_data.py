"""The rushing matchup layer's auxiliary stores: what the agreed nflverse play
store (nfl_matchup_data, `nfl_matchup_<season>`) does not carry, compacted per
season into `nfl_rush_aux_<season>` beside it.

The agreed store keeps one array per column over every offensive snap, with
the run location / gap, box, personnel, FTN's RPO and sneak flags -- enough
for most of the rushing profiles. It lacks five things:

  pbp extras      nflverse pbp's `play_id`, `aborted_play` and `rushing_yards`
                  per store row. An aborted snap (a fumbled exchange) is typed
                  "run" with rush_attempt 1 and the QB as rusher -- 87 in the
                  2025 regular season -- so without the flag every one of them
                  reads as a QB designed run. The rows are ALIGNED to the
                  store by replaying build_season's own row filter over the
                  same pbp file and checking each row's game, quarter, down,
                  yards, rusher and kind; a row (or game) that does not match
                  reads NA, never a guess (`extras.aligned`).
  situational on-field  per game, per RB/FB, the plays he was on the field
                  for by situation (early down, third/fourth down, short
                  yardage, red zone, goal line, two-minute, neutral, dropbacks,
                  designed runs), and the team's totals over the SAME charted
                  plays, from participation's offense_players -- so a
                  third-down or two-minute snap share is <= 1 by construction
                  (the agreed F1/F7 lesson). Participation ends at 2025: none
                  for 2026.
  PFR advanced    advstats_week_rush (yards before / after contact, broken
                  tackles, per player-game), advstats_week_def (missed
                  tackles, pressures, hurries, QB hits, blitzes, per defender,
                  summed per team-game), advstats_week_pass (times pressured /
                  hurried / hit / blitzed per QB-game -- the one in-season
                  pressure source for 2026, which has no participation).
  NGS rushing     nextgen_stats ngs_rushing.csv.gz: rush yards over expected,
                  expected yards, efficiency, 8+ defenders %, time to LOS.
                  WEEKLY ROWS EXIST ONLY FOR GAMES WITH 10+ RUSHES (measured:
                  every weekly row 2016-2026 has rush_attempts >= 10, ~30 backs
                  a week) and only for backs (RB 5,450, HB 43, FB 8 rows;
                  never a QB): a back's
                  low-volume games are absent, and so is every QB. The week-0
                  rows are SEASON totals and are never stored (a cutoff inside
                  the season would read the season's own future).
  injuries / snap counts  nflverse injuries_<Y> (the weekly report's game
                  status and practice status, every position) and PFR snap
                  counts for offensive linemen and the skill players (the snap
                  source for 2026, and the OL starters).

Player identity is the gsis id as an int (nfl_matchup_data.gsis_int). PFR rows
carry pfr ids: they are joined through nflverse players.csv `pfr_id` (16,988
ids with a well-formed gsis, no conflicts, checked 2026-10-23), then
DynastyProcess `pfr_id` (8 more; players.csv wins the 8 disagreements), then the agreed crosswalk's name
resolver (team-disambiguated). Every store records how many rows each source
had, how many joined and by which route, and which skill players are not in
the agreed crosswalk (`joins`). Nothing is simulated or estimated here: every
number is a count or a sum read off the files.

Credits: FTN Data via nflverse (CC BY-SA 4.0); nflverse-data (CC BY 4.0) for
pbp, participation, players, snap counts and injuries; Pro Football Reference
(Sports Reference) advanced stats via nflverse -- Sports Reference welcomes
sharing its data with credit, and is never scraped here
(research/reports/matchup_data_sources.md); NFL Next Gen Stats via nflverse --
NFL-owned data, governed by the NFL's terms; the nflverse page carries no
licence text for it.

Pure stdlib: importable by the web app. The build is a deep-job body, not a
request path (refresh()).
"""
import array
import csv
import os
import time

import deep_cache
import errlog
import nfl_matchup_data as D

# 1: the first shape (extras, sit, pfr_rush, pfr_def, pfr_pass, ngs, inj, snaps).
# 2: snaps' team total is the most offensive snaps any player took (it was the
#    largest snaps / pct, which a 1-snap 1% player turns into 100 -- the review's
#    B2: +7.35 a game on 2024); skill rows carry special-teams snaps; `coverage`
#    per block against the store's games / weeks.
SCHEMA = 2
KEY = "nfl_rush_aux_{season}"
CREDIT = dict(D.CREDIT,
              pfr="Pro Football Reference advanced stats and snap counts via nflverse (Sports Reference; credit PFR)",
              ngs="NFL Next Gen Stats via nflverse (NFL-owned data, governed by the NFL's terms)",
              injuries="NFL injury reports via nflverse")

# The seasons each auxiliary source exists for (checked 2026-10-23: every file
# below answered 206 to a ranged GET; ngs_<Y>_rushing per season 404s for 2026,
# the all-seasons file carries 2026 weeks 1-6 and one week-7 row).
PFR_SEASONS = range(2018, 2027)
PFR_PASS_SEASONS = range(2021, 2027)    # advstats_week_pass_2018-2020 do not exist (404, checked 2026-10-23)
INJ_SEASONS = range(2009, 2027)
SNAP_SEASONS = range(2012, 2027)
NGS_FIRST = 2016

# ---- codes ------------------------------------------------------------------------
OL_POS = ("T", "G", "C", "OL", "OT", "OG")
SKILL_SNAP_POS = ("QB", "RB", "FB", "HB", "WR", "TE")
# the weekly report's game status (`report_status`) and practice status
STATUS = ("", "Questionable", "Doubtful", "Out")
STATUS_IX = {s.upper(): i for i, s in enumerate(STATUS)}
PRACTICE = ("", "Full", "Limited", "DNP")
_TEAM = {"LAR": "LA", "JAC": "JAX", "STL": "LA", "SD": "LAC"}

# situational on-field vector: [team index, then the counts below]
SIT = ("all", "early", "third", "short_yardage", "red_zone", "goal_line", "two_minute", "neutral", "dropback", "designed_run")
SIT_W = len(SIT)
SIT_POS = ("RB", "FB")


def team_code(t):
    """Every source's team code in nflverse pbp's spelling (LA = Rams)."""
    t = (t or "").strip().upper()
    return _TEAM.get(t, t)


def practice_code(s):
    s = (s or "").strip().lower()
    if s.startswith("full"):
        return 1
    if s.startswith("limited"):
        return 2
    if s.startswith("did not"):
        return 3
    return 0


def two_minute(qtr, gsr):
    """The last two minutes of either half (game seconds remaining <= 120 of the
    half): the hurry-up drill, kept out of every "neutral" figure."""
    return (qtr == 2 and gsr <= 1920) or (qtr == 4 and gsr <= 120)


def neutral(wp, qtr, gsr):
    """Win probability 20-80% (the store keeps wp x 1000, 0 when nflverse had
    none -- which is then never neutral) outside the two-minute drill."""
    return 200 <= wp <= 800 and not two_minute(qtr, gsr)


# ---- fetching (the agreed freshness discipline, by import) ----------------------
def url_for(kind, season=None):
    if kind == "pfr_rush":
        return D.NFLVERSE.format(tag="pfr_advstats", file=f"advstats_week_rush_{season}.csv")
    if kind == "pfr_def":
        return D.NFLVERSE.format(tag="pfr_advstats", file=f"advstats_week_def_{season}.csv")
    if kind == "pfr_pass":
        return D.NFLVERSE.format(tag="pfr_advstats", file=f"advstats_week_pass_{season}.csv")
    if kind == "ngs_rush":
        return D.NFLVERSE.format(tag="nextgen_stats", file="ngs_rushing.csv.gz")
    if kind == "injuries":
        return D.NFLVERSE.format(tag="injuries", file=f"injuries_{season}.csv")
    return D.url_for(kind, season)


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def _i(v, default=0):
    x = _f(v)
    return default if x is None else int(round(x))


# ---- the pfr -> gsis crosswalk ------------------------------------------------------
def pfr_crosswalk(players_path, dp_path=None):
    """{pfr id: gsis int} for every position: nflverse players.csv `pfr_id`
    first, DynastyProcess `pfr_id` for ids players.csv lacks (players.csv wins
    a disagreement; DynastyProcess's are counted). Returns (map, stats)."""
    out, stats = {}, {"players_csv": 0, "dp_added": 0, "dp_disagree": 0, "conflicts": 0}
    with open(players_path, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            p = (row.get("pfr_id") or "").strip()
            g = D.gsis_int(row.get("gsis_id"))
            if not p or p == "NA" or not g:
                continue
            if p in out and out[p] != g:
                stats["conflicts"] += 1
                continue
            out[p] = g
    stats["players_csv"] = len(out)
    if dp_path and os.path.exists(dp_path):
        with open(dp_path, encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                p = (row.get("pfr_id") or "").strip()
                g = D.gsis_int(row.get("gsis_id"))
                if not p or p == "NA" or not g:
                    continue
                if p not in out:
                    out[p] = g
                    stats["dp_added"] += 1
                elif out[p] != g:
                    stats["dp_disagree"] += 1
    return out, stats


class _Joiner:
    """pfr id (then name + team) -> gsis, counting every route and failure."""

    def __init__(self, pfr_map, ids=None):
        self.pfr = pfr_map or {}
        self.ids = ids or {}
        self.idx = D.ids_index(self.ids) if self.ids else None
        self.counts = {}

    def _c(self, src, key):
        d = self.counts.setdefault(src, {"rows": 0, "pfr_id": 0, "name": 0, "unjoined": 0, "skill_not_in_crosswalk": 0,
                                         "examples": []})
        d[key] = d.get(key, 0) + 1
        return d

    def gsis(self, src, pfr_id, name="", team="", skill=None):
        d = self._c(src, "rows")
        g = self.pfr.get((pfr_id or "").strip())
        if g:
            d["pfr_id"] += 1
        elif self.idx is not None and name:
            g = D.resolve_name(D.norm_name(name), self.idx, self.ids, team=team)
            if g:
                d["name"] += 1
        if not g:
            d["unjoined"] += 1
            if len(d["examples"]) < 8:
                d["examples"].append(f"{name} ({pfr_id}, {team})")
            return 0
        if skill and self.ids and g not in self.ids:
            d["skill_not_in_crosswalk"] += 1
        return g


# ---- pbp extras, aligned to the agreed store -------------------------------------------
def _kind(g):
    """build_season's kind, replayed (nfl_matchup_data.build_season)."""
    if D._i(g("qb_kneel"), 0) == 1:
        return D.KNEEL
    if D._i(g("qb_spike"), 0) == 1:
        return D.SPIKE
    if D._i(g("sack"), 0) == 1:
        return D.SACK
    if D._i(g("qb_scramble"), 0) == 1:
        return D.SCRAMBLE
    if D._i(g("complete_pass"), 0) == 1 or D._i(g("incomplete_pass"), 0) == 1 or D._i(g("interception"), 0) == 1:
        return D.ATTEMPT
    if D._i(g("rush_attempt"), 0) == 1:
        return D.RUN
    return D.OTHER_DB if D._i(g("qb_dropback"), 0) == 1 else D.RUN


def pbp_kept(pbp_path):
    """{game_id: [(play_id, qtr, down, yds, rusher, kind, aborted, rushing_yards)]}
    over exactly the rows build_season keeps (regular season; pass / run /
    kneel / spike; no two-point try, no deleted play; both teams named), in file
    order. `aborted` is -1 where the file has no aborted_play column."""
    out = {}
    with D._open_text(pbp_path) as fh:
        r = csv.reader(fh)
        hdr = next(r)
        ix = {h: i for i, h in enumerate(hdr)}
        for row in r:
            g = (lambda c, row=row: row[ix[c]] if c in ix else "")
            if g("season_type") != "REG":
                continue
            if g("play_type") not in ("pass", "run", "qb_kneel", "qb_spike"):
                continue
            if D._i(g("two_point_attempt"), 0) == 1 or D._i(g("play_deleted"), 0) == 1:
                continue
            if not g("posteam") or not g("defteam"):
                continue
            ry = _f(g("rushing_yards"))
            out.setdefault(g("game_id"), []).append((
                D._i(g("play_id"), -1), D._clamp(D._i(g("qtr"), 0), 0, 255), D._clamp(D._i(g("down"), 0), 0, 4),
                D._clamp(D._i(g("yards_gained"), 0), -99, 120), D.gsis_int(g("rusher_player_id")), _kind(g),
                D._i(g("aborted_play"), 0) if "aborted_play" in ix else -1,
                D.NA_H if ry is None else int(D._clamp(ry, -99, 120))))
    return out


def align_extras(store, kept):
    """Lay pbp_kept's rows onto the store's rows. A game whose kept-row count
    differs from the store's is left NA whole; a row whose quarter, down,
    yards, rusher or kind differ is left NA alone (the file and the store
    were not built from the same upstream version for it). Returns
    ({"play_id", "aborted", "rush_yds"} arrays over the store's rows, stats)."""
    import nfl_matchup_profiles as P
    n = store["n"]
    C = store["cols"]
    pid = array.array("i", [-1] * n)
    abt = array.array("b", [D.NA] * n)
    ryd = array.array("h", [D.NA_H] * n)
    st = {"games": 0, "games_aligned": 0, "games_count_mismatch": [], "rows": n, "rows_aligned": 0, "rows_mismatch": 0,
          "aborted_column": True}
    for gi, a, b in P.game_ranges(store):
        st["games"] += 1
        rows = kept.get(store["games"][gi]["id"]) or []
        if len(rows) != b - a:
            if len(st["games_count_mismatch"]) < 20:
                st["games_count_mismatch"].append(f"{store['games'][gi]['id']}: store {b - a}, file {len(rows)}")
            continue
        st["games_aligned"] += 1
        for j, i in enumerate(range(a, b)):
            p_id, qtr, down, yds, rusher, kind, ab, ry = rows[j]
            if (qtr, down, yds, rusher, kind) != (C["qtr"][i], C["down"][i], C["yds"][i], C["rusher"][i], C["kind"][i]):
                st["rows_mismatch"] += 1
                continue
            pid[i] = p_id
            if ab < 0:
                st["aborted_column"] = False
            else:
                abt[i] = ab
            ryd[i] = ry
            st["rows_aligned"] += 1
    st["games_count_mismatch_n"] = st["games"] - st["games_aligned"]
    return {"play_id": pid, "aborted": abt, "rush_yds": ryd}, st


# ---- situational on-field counts (participation) ---------------------------------------
def sit_flags(C, i, aborted):
    """The SIT counts one play adds (designed run = a RUN that was not an aborted
    snap; an aborted snap with no flag (NA) is counted a designed run, as the
    agreed store does)."""
    down, ytg, yl, qtr, gsr = C["down"][i], C["ytg"][i], C["yl"][i], C["qtr"][i], C["gsr"][i]
    tm = two_minute(qtr, gsr)
    return (1, 1 if down in (1, 2) else 0, 1 if down in (3, 4) else 0, 1 if down in (3, 4) and ytg <= 2 else 0,
            1 if yl <= 20 else 0, 1 if yl <= 5 else 0, 1 if tm else 0, 1 if neutral(C["wp"][i], qtr, gsr) else 0,
            1 if C["db"][i] == 1 else 0, 1 if (C["kind"][i] == D.RUN and aborted != 1) else 0)


def read_sit(participation_path, store, extras, positions):
    """{"players": {gi: {gsis: array('H', [team ix] + SIT counts)}}, "teams": {gi:
    {team ix: array('H', SIT counts)}}} over the store's charted, non-kneel,
    non-spike plays. Only RB/FB are kept per player (the rushing layer's
    question); the team totals cover the same plays, so every situational snap
    share is <= 1."""
    C = store["cols"]
    keyed = {}
    games = store["games"]
    gi_col = C["gi"]
    for i in range(store["n"]):
        p = extras["play_id"][i]
        if p >= 0:
            keyed[(games[gi_col[i]]["id"], p)] = i
    players, teams = {}, {}
    n_rows = 0
    with D._open_text(participation_path) as fh:
        r = csv.reader(fh)
        hdr = next(r)
        ix = {h: i for i, h in enumerate(hdr)}
        c_g, c_p, c_pl = ix["nflverse_game_id"], ix["play_id"], ix["offense_players"]
        for row in r:
            i = keyed.get((row[c_g], D._i(row[c_p], -1)))
            if i is None or C["kind"][i] in (D.KNEEL, D.SPIKE):
                continue
            pl = row[c_pl]
            if not pl:
                continue
            n_rows += 1
            gi, off = gi_col[i], C["off"][i]
            fl = sit_flags(C, i, extras["aborted"][i])
            tv = teams.setdefault(gi, {}).get(off)
            if tv is None:
                tv = teams[gi][off] = array.array("H", [0] * SIT_W)
            for k, v in enumerate(fl):
                tv[k] += v
            gp = players.setdefault(gi, {})
            for x in pl.split(";"):
                g = D.gsis_int(x)
                if not g or positions.get(g) not in SIT_POS:
                    continue
                pv = gp.get(g)
                if pv is None:
                    pv = gp[g] = array.array("H", [off] + [0] * SIT_W)
                for k, v in enumerate(fl):
                    pv[k + 1] += v
    return {"players": players, "teams": teams, "plays": n_rows}


# ---- PFR, NGS, injuries, snaps -----------------------------------------------------------
def _reg(row):
    return (row.get("game_type") or row.get("season_type") or "REG").strip().upper() == "REG"


def read_pfr_rush(path, joiner):
    """{game_id: {gsis: (carries, yards before contact, yards after contact,
    rushing broken tackles, receiving broken tackles)}} (REG only)."""
    out = {}
    with D._open_text(path) as fh:
        for row in csv.DictReader(fh):
            if not _reg(row):
                continue
            g = joiner.gsis("pfr_rush", row.get("pfr_player_id"), row.get("pfr_player_name") or "",
                            team_code(row.get("team")), skill=True)
            if not g:
                continue
            out.setdefault(row.get("game_id") or "", {})[g] = (
                _i(row.get("carries")), _i(row.get("rushing_yards_before_contact")), _i(row.get("rushing_yards_after_contact")),
                _i(row.get("rushing_broken_tackles")), _i(row.get("receiving_broken_tackles")))
    return out


PFR_DEF_FIELDS = ("def_tackles_combined", "def_missed_tackles", "def_pressures", "def_times_hurried", "def_times_hitqb",
                  "def_sacks", "def_times_blitzed")


def read_pfr_def(path):
    """{game_id: {defense team: [defenders, tackles, missed tackles, pressures,
    hurries, QB hits, sacks, blitzes]}} summed over the defenders PFR lists
    (REG only). A play with two pressuring defenders counts twice in the
    pressure sum -- the team figure is a count of defender-pressures."""
    out = {}
    with D._open_text(path) as fh:
        for row in csv.DictReader(fh):
            if not _reg(row):
                continue
            t = out.setdefault(row.get("game_id") or "", {})
            v = t.get(team_code(row.get("team")))
            if v is None:
                v = t[team_code(row.get("team"))] = [0.0] * (1 + len(PFR_DEF_FIELDS))
            v[0] += 1
            for k, f in enumerate(PFR_DEF_FIELDS):
                v[k + 1] += _f(row.get(f)) or 0.0
    return {g: {t: tuple(round(x, 1) for x in v) for t, v in d.items()} for g, d in out.items()}


PFR_PASS_FIELDS = ("times_pressured", "times_hurried", "times_hit", "times_blitzed", "times_sacked")


def read_pfr_pass(path, joiner):
    """{game_id: {gsis: (times pressured, hurried, hit, blitzed, sacked)}} per
    QB-game (REG only)."""
    out = {}
    with D._open_text(path) as fh:
        for row in csv.DictReader(fh):
            if not _reg(row):
                continue
            g = joiner.gsis("pfr_pass", row.get("pfr_player_id"), row.get("pfr_player_name") or "",
                            team_code(row.get("team")), skill=True)
            if not g:
                continue
            out.setdefault(row.get("game_id") or "", {})[g] = tuple(_i(row.get(f)) for f in PFR_PASS_FIELDS)
    return out


def read_ngs_rush(path, seasons, ids=None, counts=None):
    """{season: {week: {gsis: (team, attempts, yards, expected yards, RYOE,
    efficiency, 8+ defenders %, time to LOS, TDs)}}} from the weekly rows
    (week >= 1, REG). The week-0 rows are season totals and are dropped."""
    out = {}
    seasons = set(int(s) for s in seasons)
    c = counts if counts is not None else {}
    with D._open_text(path) as fh:
        for row in csv.DictReader(fh):
            s = _i(row.get("season"), 0)
            if s not in seasons or (row.get("season_type") or "").upper() != "REG":
                continue
            wk = _i(row.get("week"), 0)
            d = c.setdefault(s, {"rows": 0, "season_rows_dropped": 0, "unjoined": 0, "not_in_crosswalk": 0, "examples": []})
            if wk < 1:
                d["season_rows_dropped"] += 1
                continue
            d["rows"] += 1
            g = D.gsis_int(row.get("player_gsis_id"))
            if not g:
                d["unjoined"] += 1
                continue
            if ids and g not in ids:
                d["not_in_crosswalk"] += 1
                if len(d["examples"]) < 5:
                    d["examples"].append(row.get("player_display_name") or "")
            exp = _f(row.get("expected_rush_yards"))
            ryoe = _f(row.get("rush_yards_over_expected"))
            out.setdefault(s, {}).setdefault(wk, {})[g] = (
                team_code(row.get("team_abbr")), _i(row.get("rush_attempts")), _i(row.get("rush_yards")),
                None if exp is None else round(exp, 2), None if ryoe is None else round(ryoe, 2),
                _f(row.get("efficiency")), _f(row.get("percent_attempts_gte_eight_defenders")),
                _f(row.get("avg_time_to_los")), _i(row.get("rush_touchdowns")))
    return out


def read_injuries(path):
    """{week: {team: {gsis: (position, game-status code, practice code)}}} (REG
    only; codes in STATUS / PRACTICE). A player on injured reserve leaves the
    weekly report -- his absence is NOT on it (see the OL starters logic in
    nfl_rush_profiles, which also reads the last game's snaps)."""
    out = {}
    with D._open_text(path) as fh:
        for row in csv.DictReader(fh):
            if not _reg(row):
                continue
            g = D.gsis_int(row.get("gsis_id"))
            if not g:
                continue
            out.setdefault(_i(row.get("week"), 0), {}).setdefault(team_code(row.get("team")), {})[g] = (
                (row.get("position") or "").strip().upper(), STATUS_IX.get((row.get("report_status") or "").strip().upper(), 0),
                practice_code(row.get("practice_status")))
    return out


def read_snaps(path, joiner, stats=None):
    """{game_id: {team: {"team": team offensive snaps, "ol": {gsis (or "pfr:<id>"
    when the crosswalk lacks him): (pos, snaps)}, "skill": {gsis: (offense snaps,
    offense share, special-teams snaps)}}}} from PFR's snap counts (REG).

    The team total is the MOST offensive snaps any player took -- a lineman or
    the quarterback plays every snap (a player at 100% in 543 of 544 team-games
    of 2024). The first version took the largest snaps / pct, so a back with 1
    snap at 0.01 made the team total 100 (the review's B2: +7.35 snaps a game on
    2024, every snap share read ~10% low). Only when no player is at >= 99.5%
    does the total come from the max-snap player's snaps / pct, and `stats`
    counts those games (`team_fallback`). A skill player with special-teams
    snaps only is kept (offense 0) so a dataset can tell "took a snap" from
    "played on offense"."""
    out = {}
    acc = {}
    with D._open_text(path) as fh:
        for row in csv.DictReader(fh):
            if not _reg(row):
                continue
            snaps = _i(row.get("offense_snaps"))
            st_snaps = _i(row.get("st_snaps"))
            pos = (row.get("position") or "").strip().upper()
            if snaps <= 0 and not (st_snaps > 0 and pos in SKILL_SNAP_POS):
                continue
            gid, team = row.get("game_id") or "", team_code(row.get("team"))
            rec = out.setdefault(gid, {}).setdefault(team, {"team": 0, "ol": {}, "skill": {}})
            pct = _f(row.get("offense_pct")) or 0.0
            if snaps > 0:
                a = acc.setdefault((gid, team), [0, 0.0, 0, 0.0])   # max snaps, its pct, max pct seen
                if snaps > a[0]:
                    a[0], a[1] = snaps, pct
                a[3] = max(a[3], pct)
            if pos in OL_POS and snaps > 0:
                # keyed by gsis (the injury report's key) through the crosswalk; an
                # unjoined lineman keeps "pfr:<id>" and can never match the report
                pfr_id = (row.get("pfr_player_id") or "").strip()
                g = joiner.gsis("snaps_ol", pfr_id, row.get("player") or "", team)
                rec["ol"][g if g else f"pfr:{pfr_id}"] = (pos, snaps)
            elif pos in SKILL_SNAP_POS:
                g = joiner.gsis("snaps", row.get("pfr_player_id"), row.get("player") or "", team, skill=True)
                if g:
                    rec["skill"][g] = (snaps, round(pct, 4), st_snaps)
    fallback = 0
    for (gid, team), (mx, mx_pct, _u, top_pct) in acc.items():
        if top_pct >= 0.995 or not mx_pct:
            out[gid][team]["team"] = mx
        else:
            out[gid][team]["team"] = int(round(mx / mx_pct))
            fallback += 1
    if stats is not None:
        stats["team_games"] = len(acc)
        stats["team_fallback"] = fallback
    return out


def coverage(store, aux):
    """Each block's reach against the store: game-keyed blocks (pfr_rush,
    pfr_def, snaps, sit) by store game, week-keyed ones (injuries, ngs) by
    store week. A truncated or late file shows as `short` (the review's m3 / M3:
    a PFR file cut at 60% built 116 of 272 games and said nothing)."""
    games = store["games"]
    gids = [g["id"] for g in games]
    weeks = sorted({g["week"] for g in games})
    out = {}
    for blk in ("pfr_rush", "pfr_def", "pfr_pass", "snaps"):
        d = aux.get(blk)
        if d is None:
            continue
        have = [g for g in gids if g in d]
        out[blk] = {"games": len(gids), "covered": len(have), "short": [g for g in gids if g not in d][:12],
                    "n_short": len(gids) - len(have)}
    sit = (aux.get("sit") or {}).get("players") or {}
    if aux.get("sit", {}).get("plays"):
        have = sum(1 for gi in range(len(games)) if gi in sit)
        out["sit"] = {"games": len(games), "covered": have, "n_short": len(games) - have,
                      "short": [games[gi]["id"] for gi in range(len(games)) if gi not in sit][:12]}
    for blk in ("injuries", "ngs"):
        d = aux.get(blk)
        if d is None:
            continue
        out[blk] = {"weeks": weeks, "covered": sorted(w for w in weeks if w in d),
                    "short": [w for w in weeks if w not in d], "n_short": sum(1 for w in weeks if w not in d)}
    return out


# ---- the season build -----------------------------------------------------------------
def build_aux(season, store, paths, pfr_map=None, ids=None, ngs=None, log=None):
    """One season's auxiliary store. `store` is the agreed nfl_matchup store of
    that season (the extras and situational counts align to its rows);
    `paths` {kind: local file} for pbp, participation, pfr_rush, pfr_def,
    pfr_pass, injuries, snaps (any may be missing: that block is empty and
    `missing` says so); `ngs` the weekly NGS blocks by season (read once for all
    seasons by refresh; None = no NGS file at all). `complete` / `absent`: the
    sources the season should have (`expected_sources`) that had no file.
    Returns the store dict."""
    t0 = time.time()
    season = int(season)
    ids = ids or {}
    positions = {pid: r.get("pos") for pid, r in ids.items()}
    joiner = _Joiner(pfr_map, ids)
    out = {"schema": SCHEMA, "season": season, "built": time.time(), "store_built": store.get("built"),
           "store_schema": store.get("schema"), "store_n": store["n"], "credit": dict(CREDIT), "missing": [],
           "sources": {}, "joins": {}}
    p = paths.get("pbp")
    if p and os.path.exists(p):
        extras, stx = align_extras(store, pbp_kept(p))
        out["extras"], out["joins"]["pbp_extras"] = extras, stx
        if stx["rows_mismatch"] or stx["games_count_mismatch_n"]:
            errlog.note("RUSH-align", msg=f"{season}: {stx['rows_mismatch']} rows / {stx['games_count_mismatch_n']} games "
                                          f"of the pbp file do not match the matchup store")
    else:
        out["extras"] = None
        out["missing"].append("pbp extras (no pbp file): aborted snaps unknown, every aborted snap reads a designed run")
    p = paths.get("participation")
    if p and os.path.exists(p) and out["extras"] is not None:
        out["sit"] = read_sit(p, store, out["extras"], positions)
    else:
        out["sit"] = {"players": {}, "teams": {}, "plays": 0}
        out["missing"].append("situational on-field counts (no participation file for this season)")
    snap_stats = {}
    for kind, reader in (("pfr_rush", lambda q: read_pfr_rush(q, joiner)), ("pfr_def", read_pfr_def),
                         ("pfr_pass", lambda q: read_pfr_pass(q, joiner)), ("injuries", read_injuries),
                         ("snaps", lambda q: read_snaps(q, joiner, snap_stats))):
        p = paths.get(kind)
        if p and os.path.exists(p):
            out[kind] = reader(p)
        else:
            out[kind] = {}
            out["missing"].append(f"{kind} (no file for {season})")
    out["ngs"] = (ngs or {}).get(season) or {}
    if not out["ngs"]:
        out["missing"].append("ngs_rush (no weekly rows for this season)")
    out["joins"].update(joiner.counts)
    out["joins"]["snaps_team"] = snap_stats
    # complete = every source the season should have had a file; a short (late or truncated) block is listed in
    # `missing` below but does not make the aux incomplete -- in season PFR lags the store by a week as a rule
    absent = [k for k in expected_sources(season) if (ngs is None if k == "ngs_rush" else not (paths.get(k) and os.path.exists(paths[k])))]
    out["absent"], out["complete"] = absent, not absent
    if absent:
        errlog.note("RUSH-incomplete", msg=f"{season}: no file for {absent}: the aux is incomplete and stays due")
    out["coverage"] = coverage(store, out)
    for blk, c in out["coverage"].items():
        if c.get("n_short") and out.get(blk):
            what = "store games" if "games" in c else "store weeks"
            out["missing"].append(f"{blk}: {c['n_short']} {what} with no rows (first: {c['short'][:4]}) -- a truncated or late file")
    out["seconds"] = round(time.time() - t0, 2)
    if log:
        log(f"[rush] {season} aux: extras {out['joins'].get('pbp_extras', {}).get('rows_aligned')} rows, sit "
            f"{out['sit']['plays']} plays, pfr_rush {len(out['pfr_rush'])} games, ngs weeks {len(out['ngs'])}, "
            f"{out['seconds']}s")
    return out


def load_aux(season):
    """The season's auxiliary store from the deep store, or None."""
    payload, _ts = deep_cache.load(KEY.format(season=int(season)))
    if not payload or payload.get("schema") != SCHEMA:
        return None
    return payload


def expected_sources(season):
    """The sources a season's aux must have read to be COMPLETE: every one that exists for the season by the
    tables above (pbp and participation by the agreed layer's). A source that should exist and has no file (a
    failed download, a raw file dropped before a re-download that failed) makes the aux incomplete -- before
    this, such an aux was saved as if whole, still matched its store, and was never retried (the data
    re-checks' Sonnet R1 / Opus R3: with the network off, no extras, PFR, injuries or snaps, stamped "aligned")."""
    season = int(season)
    out = []
    for kind, ok in (("pbp", season in D.PBP_SEASONS), ("participation", season in D.PARTICIPATION_SEASONS),
                     ("pfr_rush", season in PFR_SEASONS), ("pfr_def", season in PFR_SEASONS),
                     ("pfr_pass", season in PFR_PASS_SEASONS), ("injuries", season in INJ_SEASONS),
                     ("snaps", season in SNAP_SEASONS), ("ngs_rush", season >= NGS_FIRST)):
        if ok:
            out.append(kind)
    return out


def aux_complete(aux):
    """True only for an aux that read every source its season should have (`complete`, set by build_aux). An
    aux without the flag (none was ever deployed) reads incomplete, so the job rebuilds it."""
    return bool(aux) and aux.get("complete") is True


def aux_matches(aux, store):
    """Was this aux built against this very matchup store? (its row extras
    align to the store's rows only then)."""
    return bool(aux and store and aux.get("store_built") == store.get("built") and aux.get("store_n") == store.get("n"))


# ---- the refresh (a deep job's body) ---------------------------------------------------------
def refresh(seasons=None, raw_dir=None, keep_raw=True, log=None, max_age_s=None):
    """Download what is newer than what is on disk (D._fresh: upstream's
    Last-Modified against the one recorded at the fetch) and rebuild the
    auxiliary store of every season whose files changed, whose store is
    missing or at an older SCHEMA, or whose matchup store was rebuilt since
    (the extras align to its rows). Requires the matchup store of the season
    (nfl_matchup_data.refresh runs first, over the same raw_dir). Returns
    {"seasons": {season: summary}, "seconds"}.

    One season's failure is ledgered (RUSH-build) with its error in the
    summary and never stops the next. Not wired into any job here: the
    orchestrator writes the integration."""
    t0 = time.time()
    raw_dir = raw_dir or os.path.join(deep_cache.CACHE_DIR, "nfl_matchup_raw")
    os.makedirs(raw_dir, exist_ok=True)
    if seasons is None:
        import clock
        seasons = D.default_seasons(D._current_season(), clock.today_et().month)
    seasons = [int(s) for s in seasons]
    summary = {"seasons": {}}

    def get(kind, season=None):
        url = url_for(kind, season)
        path = os.path.join(raw_dir, os.path.basename(url))
        if D._fresh(path, url, max_age_s):
            return path, False
        try:
            D.fetch(url, path, log=log)
            return path, True
        except Exception as e:
            errlog.note("RUSH-fetch", e, path=f"{season} {kind}")
            return (path if os.path.exists(path) else None), False

    p_players, _ = get("players")
    p_dp, _ = get("dp_ids")
    pfr_map, pfr_stats = ({}, {})
    if p_players:
        pfr_map, pfr_stats = pfr_crosswalk(p_players, p_dp)
    ids_payload = D.load_ids()
    ids = (ids_payload or {}).get("ids") or {}
    p_ngs, ngs_changed = get("ngs_rush")
    ngs_counts = {}
    ngs = read_ngs_rush(p_ngs, seasons, ids, ngs_counts) if p_ngs else None     # None: no NGS file at all
    for season in seasons:
        # one season's bad file never stops the next (the review's O3): ledgered, and the error in the summary
        try:
            summary["seasons"][season] = _refresh_season(season, get, pfr_map, pfr_stats, ids, ngs, ngs_changed,
                                                         ngs_counts, keep_raw, log)
        except Exception as e:
            errlog.note("RUSH-build", e, path=str(season))
            summary["seasons"][season] = {"error": f"{type(e).__name__}: {e}"}
    summary["seconds"] = round(time.time() - t0, 1)
    return summary


def _refresh_season(season, get, pfr_map, pfr_stats, ids, ngs, ngs_changed, ngs_counts, keep_raw, log):
    store = D.load_season(season)
    if store is None:
        return {"error": "no nfl_matchup store for this season (run nfl_matchup_data.refresh first)"}
    paths, changed = {}, ngs_changed
    for kind, ok in (("pbp", season in D.PBP_SEASONS), ("participation", season in D.PARTICIPATION_SEASONS),
                     ("pfr_rush", season in PFR_SEASONS), ("pfr_def", season in PFR_SEASONS),
                     ("pfr_pass", season in PFR_PASS_SEASONS), ("injuries", season in INJ_SEASONS),
                     ("snaps", season in SNAP_SEASONS)):
        if not ok:
            continue
        path, new = get(kind, season)
        if path:
            paths[kind] = path
        changed = changed or new
    if season in D.PBP_SEASONS and "pbp" not in paths:
        # the extras align to the store's rows only through this season's pbp file: without it nothing is
        # saved and the season stays due (the agreed refresh refuses the same way)
        return {"error": f"no play-by-play file for {season} (download failed?): nothing saved, the season stays due"}
    old = load_aux(season)
    if not changed and old is not None and aux_matches(old, store) and aux_complete(old):
        return {"skipped": "no source changed, the aux store matches the matchup store and is complete"}
    aux = build_aux(season, store, paths, pfr_map, ids, ngs, log=log)
    aux["sources"] = {k: dict(D._read_meta(p), url=url_for(k, season), bytes=os.path.getsize(p))
                      for k, p in paths.items() if os.path.exists(p)}
    aux["joins"]["pfr_crosswalk"] = pfr_stats
    aux["joins"]["ngs"] = ngs_counts.get(season, {})
    deep_cache.save(KEY.format(season=season), aux)
    out = {"built": True, "complete": aux["complete"], "absent": aux["absent"], "missing": aux["missing"], "seconds": aux["seconds"],
           "extras": aux["joins"].get("pbp_extras", {}).get("rows_aligned")}
    if not keep_raw:
        for k, p in paths.items():
            if k in ("pbp", "participation"):
                continue      # the matchup store's files: its own refresh decides
            try:
                os.remove(p)
            except FileNotFoundError:
                pass
            except OSError as e:
                errlog.note("RUSH-rm", e, path=p)     # a failed delete on a 1 GB disk is never silent
    return out
