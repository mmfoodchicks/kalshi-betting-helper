"""NFL play-by-play -- the football twin of the Statcast feed under the MLB deep sim.

ESPN's game summary (`/summary?event=<id>`) carries, for every finished game,
the box score, the drive chart and every play with its down, distance, yards
to the end zone, the clock, the effective result (penalties enforced) and the
score. Nothing else in this codebase reached below the team-game level for
football: the slate engine runs on Sleeper's projections, the season engine on
a ratings scale. This module turns the summaries into COMPACT game records --
fourteen integers a play -- caches a season of them in the deep store
(`nfl_pbp_<season>.pkl`, which the PC uploads like any other artifact) and
exposes the aggregations the deep engine's fit (research/nfl_deep_fit.py) and
its team profiles (nfl_deep_data) are built from.

Sizes, measured on 2023 week 1: a summary is 430-510 KB of JSON (40 KB
gzipped) and 0.27 s through the proxy; a season is 272 games, so a cold
season is ~4 minutes and 120 MB of transfer ONCE, after which finished games
never change. The compact record keeps ~170 plays x 14 int32 = 9.5 KB a game,
2.6 MB a season.

Play record (array('i'), stride PLAY_W):
  per   quarter 1-4, 5+ overtime
  clk   seconds left in the quarter at the snap
  off   0 = home team has the ball, 1 = away
  down  0 on kickoffs / free kicks
  dist  yards to gain
  ytg   yards to the opponent's end zone at the snap
  code  play class (CODES below)
  yds   effective yardage of the play (penalties enforced)
  flags bitmask (F_* below)
  sd    offence's score minus defence's BEFORE the play
  e_down, e_dist, e_ytg   the next snap's down / distance / yards to go, from
        the perspective of whichever team has the ball next (-1 where ESPN
        has none, e.g. after a touchdown)
  pts   points the play put on the board: positive for the offence, negative
        for the defence (a pick-six reads -6 / -7, a safety -2); ESPN's running
        score on a play row is occasionally corrected a row late, so the
        official points live in the record's home_pts / away_pts and the
        point-after record in `scoring` (see parse_summary)
"""
import array
import re
import time

import deep_cache
import errlog
import racing

_SITE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
_CANON = {"WAS": "WSH", "JAC": "JAX", "LA": "LAR", "OAK": "LV", "SD": "LAC",
          # ESPN's play TEXT uses a second set of codes; the team objects do not,
          # but the drive chart has been seen carrying them.
          "BLT": "BAL", "HST": "HOU", "CLV": "CLE", "ARZ": "ARI"}


def canon(ab):
    ab = (ab or "").upper()
    return _CANON.get(ab, ab)


PLAY_W = 14
(I_PER, I_CLK, I_OFF, I_DOWN, I_DIST, I_YTG, I_CODE, I_YDS, I_FLAGS, I_SD,
 I_EDOWN, I_EDIST, I_EYTG, I_PTS) = range(PLAY_W)

# Play classes. The engine samples transitions by class, so the classes are
# what a coach decides (run / pass / kick) and how the defence answered it.
KICKOFF, RUSH, PASS_COMP, PASS_INC, SACK, INT, PUNT, FG_GOOD, FG_MISS, FG_BLOCK, \
    PENALTY, FUMBLE_LOST, SAFETY, _UNUSED13, TIMEOUT, TWO_MIN, END_PERIOD, KNEEL, \
    SPIKE, PUNT_BLOCK, OTHER = range(21)
CODES = {KICKOFF: "kickoff", RUSH: "rush", PASS_COMP: "pass_comp", PASS_INC: "pass_inc",
         SACK: "sack", INT: "int", PUNT: "punt", FG_GOOD: "fg_good", FG_MISS: "fg_miss",
         FG_BLOCK: "fg_block", PENALTY: "penalty", FUMBLE_LOST: "fumble_lost",
         SAFETY: "safety", TIMEOUT: "timeout", TWO_MIN: "two_min", END_PERIOD: "end_period",
         KNEEL: "kneel", SPIKE: "spike", PUNT_BLOCK: "punt_block", OTHER: "other"}
# Classes that are an offensive SNAP (a down is used or replayed).
SNAPS = frozenset((RUSH, PASS_COMP, PASS_INC, SACK, INT, PENALTY, FUMBLE_LOST, SAFETY,
                   KNEEL, SPIKE))
# Classes where the offence tried to advance the ball (the play-call model).
PLAYS = frozenset((RUSH, PASS_COMP, PASS_INC, SACK, INT, FUMBLE_LOST, SAFETY, KNEEL, SPIKE))

F_TD, F_FIRST, F_TURNOVER, F_PEN_ON_PLAY, F_OOB, F_NOHUDDLE, F_SHOTGUN, F_FUMBLE, \
    F_FUMBLE_LOST, F_XP_GOOD, F_XP_MISS, F_2PT_ATT, F_2PT_GOOD, F_TOUCHBACK, \
    F_FAIR_CATCH, F_SCORING, F_PASS_PLAY = (1 << k for k in range(17))

_TYPE_CODE = {
    "Kickoff": KICKOFF, "Kickoff Return (Offense)": KICKOFF, "Kickoff Return Touchdown": KICKOFF,
    "Rush": RUSH, "Rushing Touchdown": RUSH,
    "Pass Reception": PASS_COMP, "Passing Touchdown": PASS_COMP,
    "Pass Incompletion": PASS_INC,
    "Sack": SACK,
    "Pass Interception Return": INT, "Interception Return Touchdown": INT,
    "Punt": PUNT, "Punt Return Touchdown": PUNT, "Blocked Punt": PUNT_BLOCK,
    "Field Goal Good": FG_GOOD, "Field Goal Missed": FG_MISS, "Missed Field Goal Return": FG_MISS,
    "Blocked Punt Touchdown": PUNT_BLOCK,
    "Blocked Field Goal": FG_BLOCK, "Blocked Field Goal Touchdown": FG_BLOCK,
    "Penalty": PENALTY,
    "Fumble Recovery (Opponent)": FUMBLE_LOST, "Fumble Return Touchdown": FUMBLE_LOST,
    "Safety": SAFETY,
    "Timeout": TIMEOUT, "Official Timeout": TIMEOUT, "Two-minute warning": TWO_MIN,
    "End Period": END_PERIOD, "End of Half": END_PERIOD, "End of Game": END_PERIOD,
    "End of Regulation": END_PERIOD,
}

_RE_PASS = re.compile(r"\bpass\b", re.I)
_RE_OOB = re.compile(r"\b(pushed ob|ran ob|ob at|out of bounds|steps out)\b", re.I)


def _clock_s(s):
    """'12:34' -> 754; tolerant of '0:00' and junk."""
    try:
        m, sec = str(s or "0:0").split(":")
        return int(m) * 60 + int(sec)
    except (ValueError, AttributeError):
        return 0


def _int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return default


def _box_side(stats):
    """ESPN's displayValue strings -> numbers. '7-11' third downs, '21/34'
    completions, '25:08' possession, two 'interceptions' rows (thrown first,
    then the defence's)."""
    out = {}
    seen_int = 0
    for s in stats or []:
        name, v = s.get("name"), s.get("displayValue")
        if name is None:
            continue
        if name == "interceptions":
            seen_int += 1
            out["int_thrown" if seen_int == 1 else "int_caught"] = _int(v)
            continue
        if name in ("thirdDownEff", "fourthDownEff", "redZoneAttempts", "sacksYardsLost",
                    "totalPenaltiesYards", "completionAttempts"):
            a, _, b = str(v or "0-0").replace("/", "-").partition("-")
            key = {"thirdDownEff": ("third_conv", "third_att"),
                   "fourthDownEff": ("fourth_conv", "fourth_att"),
                   "redZoneAttempts": ("rz_td", "rz_trips"),
                   "sacksYardsLost": ("sacks_taken", "sack_yds"),
                   "totalPenaltiesYards": ("penalties", "penalty_yds"),
                   "completionAttempts": ("comp", "att")}[name]
            out[key[0]], out[key[1]] = _int(a), _int(b)
            continue
        if name == "possessionTime":
            out["top_s"] = _clock_s(v)
            continue
        out[{"firstDowns": "first_downs", "totalOffensivePlays": "plays",
             "totalYards": "yards", "totalDrives": "drives",
             "netPassingYards": "pass_yds", "rushingYards": "rush_yds",
             "rushingAttempts": "rush_att", "turnovers": "turnovers",
             "fumblesLost": "fumbles_lost", "defensiveTouchdowns": "def_td",
             "yardsPerPlay": "ypp", "yardsPerPass": "ypa",
             "yardsPerRushAttempt": "ypc"}.get(name, name)] = (
            float(v) if name in ("yardsPerPlay", "yardsPerPass", "yardsPerRushAttempt")
            else _int(v))
    return out


def parse_summary(d):
    """One ESPN summary -> compact game record, or None when the summary has no
    play-by-play (a game that was never played, or a feed gap)."""
    header = d.get("header") or {}
    comps = header.get("competitions") or [{}]
    comp = comps[0]
    sides = {}
    for c in comp.get("competitors") or []:
        t = c.get("team") or {}
        sides[c.get("homeAway")] = {"id": str(t.get("id")), "abbr": canon(t.get("abbreviation")),
                                    "pts": _int(c.get("score"), -1)}
    if "home" not in sides or "away" not in sides:
        return None
    drives = (d.get("drives") or {}).get("previous") or []
    if not drives:
        return None
    off_of_id = {sides["home"]["id"]: 0, sides["away"]["id"]: 1}
    off_of_ab = {sides["home"]["abbr"]: 0, sides["away"]["abbr"]: 1}
    plays = array.array("i")
    dr_rows = []
    home_s = away_s = 0
    for dr in drives:
        dteam = dr.get("team") or {}
        off = off_of_id.get(str(dteam.get("id")))
        if off is None:
            off = off_of_ab.get(canon(dteam.get("abbreviation")))
        if off is None:
            continue
        st = dr.get("start") or {}
        n_before = len(plays) // PLAY_W
        for p in dr.get("plays") or []:
            ttxt = ((p.get("type") or {}).get("text")) or ""
            text = p.get("text") or ""
            code = _TYPE_CODE.get(ttxt)
            s, e = p.get("start") or {}, p.get("end") or {}
            low = text.lower()
            if code is None:
                if ttxt == "Fumble Recovery (Own)":
                    code = PASS_COMP if (_RE_PASS.search(text) and "incomplete" not in low) else RUSH
                elif ttxt.startswith("Muffed Punt Recovery"):
                    code = PUNT
                elif ttxt.startswith("Sack"):
                    code = SACK
                else:
                    code = OTHER
            if "sacked" in low and code in (RUSH, PASS_COMP, FUMBLE_LOST, OTHER):
                code = SACK          # a sack that lost a fumble is typed by the fumble
            # ESPN's yardsToEndzone on a punt is the yardLine itself for the HOME
            # team (260 of 268 home punts in 80 games of 2025; every away punt
            # agrees), so the frame is rebuilt from the yardLine: the home side
            # reads 0 at its own goal line.
            ytg_s = _int(s.get("yardsToEndzone"), 0)
            if code in (PUNT, PUNT_BLOCK):
                yl = _int(s.get("yardLine"), 0)
                if 0 < yl < 100:
                    ytg_s = (100 - yl) if off == 0 else yl
            if code == RUSH and "kneel" in low:
                code = KNEEL
            elif code == PASS_INC and "spike" in low:
                code = SPIKE
            flags = 0
            if p.get("scoringPlay"):
                flags |= F_SCORING
            if p.get("isTurnover"):
                flags |= F_TURNOVER
            # A touchdown is ESPN's scoringPlay flag plus the word: "touchdown
            # nullified by penalty" and a reviewed-and-reversed score carry the
            # word and put nothing on the board, and the TYPE lies too -- 2024's
            # onside kicks are typed "Kickoff Return Touchdown" (scoringPlay
            # false, "didn't try to advance" in the text).
            if p.get("scoringPlay") and "touchdown" in low and "nullified" not in low:
                flags |= F_TD
            if p.get("isPenalty") or "penalty" in low:
                flags |= F_PEN_ON_PLAY
            if _RE_OOB.search(text):
                flags |= F_OOB
            if "no huddle" in low:
                flags |= F_NOHUDDLE
            if "shotgun" in low:
                flags |= F_SHOTGUN
            if "fumble" in low:
                flags |= F_FUMBLE
            if code == FUMBLE_LOST or (ttxt.startswith(("Sack Opp", "Muffed Punt Recovery"))):
                flags |= F_FUMBLE_LOST | F_TURNOVER
                if code == FUMBLE_LOST and _RE_PASS.search(text):
                    flags |= F_PASS_PLAY
            if code in (PASS_COMP, PASS_INC, SACK, INT, SPIKE):
                flags |= F_PASS_PLAY
            if "extra point is good" in low:
                flags |= F_XP_GOOD
            elif "extra point" in low or "pat failed" in low:
                flags |= F_XP_MISS
            if "two-point" in low or "two point" in low:
                flags |= F_2PT_ATT
                if "succeeds" in low or "conversion)" in low:
                    flags |= F_2PT_GOOD
            if "touchback" in low:
                flags |= F_TOUCHBACK
            if "fair catch" in low:
                flags |= F_FAIR_CATCH
            # Score before the play, offence minus defence.
            sd = (home_s - away_s) if off == 0 else (away_s - home_s)
            nh, na = _int(p.get("homeScore"), home_s), _int(p.get("awayScore"), away_s)
            d_off = (nh - home_s) if off == 0 else (na - away_s)
            d_def = (na - away_s) if off == 0 else (nh - home_s)
            pts = d_off - d_def
            home_s, away_s = nh, na
            # Whose ball next: a first down is only a first down for the SAME
            # offence (ESPN's end state after a turnover is the new offence's).
            e_team = (e.get("team") or {}).get("id")
            same = e_team is None or str(e_team) == str((s.get("team") or {}).get("id"))
            e_down = _int(e.get("down"), -1)
            # An incompletion is in the list because a defensive penalty on the
            # throw (interference, holding) moves the chains all the same.
            if (same and e_down == 1 and code in (RUSH, PASS_COMP, PASS_INC, SACK, PENALTY, KNEEL, SPIKE, OTHER)
                    and not (flags & F_TURNOVER) and not (flags & F_TD)):
                flags |= F_FIRST
            plays.extend((
                _int((p.get("period") or {}).get("number"), 0),
                _clock_s((p.get("clock") or {}).get("displayValue")),
                off,
                _int(s.get("down"), 0), _int(s.get("distance"), 0), ytg_s,
                code, _int(p.get("statYardage"), 0), flags, sd,
                e_down, _int(e.get("distance"), -1), _int(e.get("yardsToEndzone"), -1), pts))
        n_after = len(plays) // PLAY_W
        if n_after == n_before:
            continue
        dr_rows.append((
            off,
            _int((st.get("period") or {}).get("number"), 0),
            _clock_s((st.get("clock") or {}).get("displayValue")),
            # the first non-kickoff snap's yards to go is the drive's real start
            next((plays[i * PLAY_W + I_YTG] for i in range(n_before, n_after)
                  if plays[i * PLAY_W + I_CODE] not in (KICKOFF, TIMEOUT, TWO_MIN, END_PERIOD)), -1),
            _int(dr.get("offensivePlays"), 0), _int(dr.get("yards"), 0),
            (dr.get("result") or "").upper(),
            _clock_s((dr.get("timeElapsed") or {}).get("displayValue"))))
    if not dr_rows:
        return None
    # The point-after record comes from ESPN's scoringPlays list, whose running
    # score is reliable where the play rows' is not (the play text names a made
    # extra point and almost never a missed one -- 1 miss in 420 touchdowns
    # read off the text against 55 in 1,302 off the scores, 2023). Each entry:
    # (period, seconds left, offence 0/1, kind, offence-minus-defence AFTER the
    # six points and BEFORE the try) with kind 0 field goal, 1 touchdown + extra
    # point good, 2 + extra point missed, 3 + two-point good, 4 + two-point
    # failed, 5 safety, 6 other (a defensive two-point return, an untried TD).
    scoring = []
    hs = as_ = 0
    for sp in d.get("scoringPlays") or []:
        h, a = _int(sp.get("homeScore"), hs), _int(sp.get("awayScore"), as_)
        dh, da = h - hs, a - as_
        off = 0 if dh > 0 else 1 if da > 0 else off_of_id.get(str((sp.get("team") or {}).get("id")), 0)
        delta = dh if off == 0 else da
        sd_after6 = ((hs + 6) - as_) if off == 0 else ((as_ + 6) - hs)
        hs, as_ = h, a
        ttxt = ((sp.get("type") or {}).get("text") or "")
        txt = (sp.get("text") or "").lower()
        if ttxt == "Field Goal Good" or delta == 3:
            kind = 0
        elif ttxt == "Safety" or delta == 2:
            kind = 5
        elif delta == 7:
            kind = 1
        elif delta == 8:
            kind = 3
        elif delta == 6:
            kind = 4 if ("two-point" in txt or "two point" in txt or "conversion" in txt) else 2
        else:
            kind = 6
        scoring.append((_int((sp.get("period") or {}).get("number"), 0),
                        _int((sp.get("clock") or {}).get("value"), 0), off, kind, sd_after6))
    box = {}
    for t in ((d.get("boxscore") or {}).get("teams") or []):
        ha = t.get("homeAway")
        if ha in ("home", "away"):
            box[ha] = _box_side(t.get("statistics"))
    season = (header.get("season") or {})
    rec = {"id": str(comp.get("id") or ""), "season": _int(season.get("year"), 0),
           "st": _int(season.get("type"), 2), "week": _int(header.get("week"), 0),
           "date": comp.get("date"), "neutral": bool(comp.get("neutralSite")),
           "home": sides["home"]["abbr"], "away": sides["away"]["abbr"],
           "home_pts": sides["home"]["pts"], "away_pts": sides["away"]["pts"],
           "ot": any(plays[i * PLAY_W + I_PER] >= 5 for i in range(len(plays) // PLAY_W)),
           "box": box, "plays": plays, "drives": dr_rows, "scoring": scoring}
    if rec["home_pts"] < 0 or rec["away_pts"] < 0:
        rec["home_pts"], rec["away_pts"] = home_s, away_s
    return rec


def iter_plays(rec):
    """Yield each play of a record as a tuple of PLAY_W ints."""
    a = rec["plays"]
    for i in range(0, len(a), PLAY_W):
        yield tuple(a[i:i + PLAY_W])


# ---- Fetch + season cache ----------------------------------------------------
def _get(url, timeout=40):
    return racing._get_json(url, timeout=timeout)


def week_events(season, week, seasontype=2):
    """[{id, week, st, date, state, home, away, home_pts, away_pts, neutral}] for
    one week's scoreboard (regular season 2, postseason 3)."""
    d = _get(f"{_SITE}/scoreboard?dates={season}&seasontype={seasontype}&week={week}", timeout=20)
    out = []
    for e in (d or {}).get("events") or []:
        comp = (e.get("competitions") or [{}])[0]
        cs = comp.get("competitors") or []
        home = next((c for c in cs if c.get("homeAway") == "home"), None)
        away = next((c for c in cs if c.get("homeAway") == "away"), None)
        if not home or not away:
            continue
        out.append({"id": str(e.get("id")), "week": week, "st": seasontype, "date": e.get("date"),
                    "state": ((comp.get("status") or {}).get("type") or {}).get("state"),
                    "home": canon((home.get("team") or {}).get("abbreviation")),
                    "away": canon((away.get("team") or {}).get("abbreviation")),
                    "home_pts": _int(home.get("score"), -1), "away_pts": _int(away.get("score"), -1),
                    "neutral": bool(comp.get("neutralSite"))})
    return out


def fetch_game(event_id):
    """One finished game's compact record (one summary call), or None."""
    d = _get(f"{_SITE}/summary?event={event_id}")
    return parse_summary(d) if d else None


def _key(season):
    return f"nfl_pbp_{int(season)}"


def load_season(season):
    """The cached season store: {"games": {id: rec}, "events": [...], "updated": epoch}."""
    payload, _ts = deep_cache.load(_key(season))
    if not payload or not isinstance(payload, dict):
        return {"games": {}, "events": [], "updated": 0.0}
    return payload


def season_records(season, max_fetch=400, postseason=True, log=None):
    """Every finished game of `season` as a compact record, fetching what the
    cache lacks (at most `max_fetch` summaries per call so one call stays
    bounded; call again to finish). Returns the store dict. Finished games are
    immutable, so a cached record is never re-fetched."""
    store = load_season(season)
    games = store.setdefault("games", {})
    events = []
    weeks = [(2, w) for w in range(1, 19)]
    if postseason:
        weeks += [(3, w) for w in (1, 2, 3, 5)]
    for st, w in weeks:
        try:
            events.extend(week_events(season, w, st))
        except Exception as e:
            errlog.note("PBP-week", e, path=f"{season} st{st} w{w}")
    if events:
        store["events"] = events
    fetched = 0
    for ev in store.get("events") or []:
        if ev.get("state") != "post" or ev["id"] in games:
            continue
        if fetched >= max_fetch:
            break
        try:
            rec = fetch_game(ev["id"])
        except Exception as e:
            errlog.note("PBP-game", e, path=ev["id"])
            rec = None
        if rec:
            rec["week"], rec["st"] = ev["week"], ev["st"]
            games[ev["id"]] = rec
            fetched += 1
            if log:
                log(f"[pbp] {season} w{ev['week']} {ev['away']}@{ev['home']}: {len(rec['plays']) // PLAY_W} plays")
    if fetched or not store.get("updated"):
        store["updated"] = time.time()
        deep_cache.save(_key(season), store)
    return store


def coverage(store):
    """(finished events, records held) -- the deep job's quality gate reads it."""
    done = [e for e in store.get("events") or [] if e.get("state") == "post"]
    return len(done), sum(1 for e in done if e["id"] in (store.get("games") or {}))


# ---- Buckets shared by the fit and the engine ---------------------------------
# One definition, imported by both sides, so a cell means the same thing in the
# tables and in the simulation that samples them.
def dist_bucket(dist):
    """Yards to gain -> 0: 1-2, 1: 3-6, 2: 7-10, 3: 11+."""
    return 0 if dist <= 2 else 1 if dist <= 6 else 2 if dist <= 10 else 3


def zone(ytg):
    """Yards to the end zone -> 0: 1-2, 1: 3-5, 2: 6-10, 3: 11-20, 4: 21-40, 5: 41-60,
    6: 61-80, 7: 81+. Fine near the goal line on purpose: a yardage histogram is
    truncated at the end zone of the plays that built it, so a cell pooling
    plays from the 1 and the 10 under-scores from the 8 (a two-yard touchdown
    run from the 2 reads as two yards) -- one 1-10 cell cost the engine a fifth
    of its touchdowns."""
    if ytg <= 2:
        return 0
    if ytg <= 5:
        return 1
    if ytg <= 10:
        return 2
    if ytg <= 20:
        return 3
    if ytg <= 40:
        return 4
    if ytg <= 60:
        return 5
    return 6 if ytg <= 80 else 7


def score_state(sd):
    """Offence minus defence -> 0: down 9+, 1: down 1-8, 2: tied, 3: up 1-8, 4: up 9+."""
    return 0 if sd <= -9 else 1 if sd < 0 else 2 if sd == 0 else 3 if sd <= 8 else 4


def time_state(per, clk):
    """0: the body of the game; 1: the last two minutes of the half, or the fourth
    quarter above five minutes; 2: the last five minutes of the fourth, and
    overtime -- where the script (hurry-up, clock-killing) takes over the
    play call."""
    if per >= 5:
        return 2
    if per == 4:
        return 2 if clk <= 300 else 1
    if per == 2 and clk <= 120:
        return 1
    return 0


def is_pass(p):
    return bool(p[I_FLAGS] & F_PASS_PLAY)


# ---- Team unit rates -----------------------------------------------------------
_UNIT_KEYS = ("snaps", "pass", "comp", "comp_yds", "inc", "sacks", "sack_yds", "ints",
              "rush", "rush_yds", "fum_lost", "pen", "pen_yds", "first", "td", "expl")


def _unit():
    u = {k: 0 for k in _UNIT_KEYS}
    u["call"] = {}        # "down,dist" -> [passes, snaps]  (pass rate over expectation)
    u["fg"] = {}          # kick distance 5-yard bin -> [made, attempts]
    u["punt"] = [0, 0]    # [punts, sum of the receiver's yards to go]
    u["ko_ret"] = [0, 0]  # [kickoffs received, sum of yards to go after them]
    u["clock"] = [0, 0]   # [snaps with a running clock in normal tempo, seconds consumed]
    return u


def team_units(store, seasontype=None):
    """{abbr: {"g": games, "pf": points for, "pa": against, "off": unit, "def": unit}}
    from every record in a season store. `off` counts the team's own snaps,
    `def` the snaps its opponents took against it -- so the same keys read as
    "what this offence did" and "what this defence allowed". `seasontype`
    restricts to 2 (regular) or 3 (postseason); None takes both."""
    out = {}

    def team(ab):
        return out.setdefault(ab, {"g": 0, "pf": 0, "pa": 0, "off": _unit(), "def": _unit()})

    for rec in (store.get("games") or {}).values():
        if seasontype is not None and rec.get("st") != seasontype:
            continue
        h, a = team(rec["home"]), team(rec["away"])
        h["g"] += 1; a["g"] += 1
        h["pf"] += rec["home_pts"]; h["pa"] += rec["away_pts"]
        a["pf"] += rec["away_pts"]; a["pa"] += rec["home_pts"]
        plays = list(iter_plays(rec))
        for i, p in enumerate(plays):
            off_ab = rec["home"] if p[I_OFF] == 0 else rec["away"]
            def_ab = rec["away"] if p[I_OFF] == 0 else rec["home"]
            o, d = out[off_ab]["off"], out[def_ab]["def"]
            code, fl, yds = p[I_CODE], p[I_FLAGS], p[I_YDS]
            if code == KICKOFF:
                if p[I_EYTG] > 0 and not (fl & F_TD):
                    for u in (o,):
                        u["ko_ret"][0] += 1; u["ko_ret"][1] += p[I_EYTG]
                continue
            if code in (PUNT, PUNT_BLOCK):
                if p[I_EYTG] > 0 and not (fl & F_TD):
                    # the punting team's net: how far the receiver is left from scoring
                    o["punt"][0] += 1; o["punt"][1] += p[I_EYTG]
                continue
            if code in (FG_GOOD, FG_MISS, FG_BLOCK):
                b = str(min(65, max(15, (p[I_YTG] + 17) // 5 * 5)))
                cell = o["fg"].setdefault(b, [0, 0])
                cell[1] += 1
                cell[0] += code == FG_GOOD
                continue
            if code not in SNAPS:
                continue
            for u in (o, d):
                u["snaps"] += 1
                if fl & F_FIRST:
                    u["first"] += 1
                if fl & F_TD:
                    u["td"] += 1
            if code == PENALTY:
                for u in (o, d):
                    u["pen"] += 1; u["pen_yds"] += abs(yds)
                continue
            if code in (KNEEL, SPIKE):
                continue
            key = f"{p[I_DOWN]},{dist_bucket(p[I_DIST])}"
            for u in (o, d):
                c = u["call"].setdefault(key, [0, 0])
                c[1] += 1
                c[0] += 1 if is_pass(p) else 0
            if is_pass(p):
                for u in (o, d):
                    u["pass"] += 1
                if code == SACK:
                    for u in (o, d):
                        u["sacks"] += 1; u["sack_yds"] += -yds
                elif code == INT:
                    for u in (o, d):
                        u["ints"] += 1
                elif code == PASS_COMP or (code == FUMBLE_LOST and fl & F_PASS_PLAY):
                    for u in (o, d):
                        u["comp"] += 1; u["comp_yds"] += yds
                        if yds >= 20:
                            u["expl"] += 1
                elif code == PASS_INC:
                    for u in (o, d):
                        u["inc"] += 1
            else:
                for u in (o, d):
                    u["rush"] += 1; u["rush_yds"] += yds
                    if yds >= 10:
                        u["expl"] += 1
            if fl & F_FUMBLE_LOST:
                for u in (o, d):
                    u["fum_lost"] += 1
            # Tempo: seconds from this snap to the next row of the same quarter,
            # when the clock kept running (in-bounds rush or completion) in the
            # body of the game; that is the pace a team chooses, not the script.
            if (code in (RUSH, PASS_COMP) and not (fl & (F_OOB | F_TD | F_TURNOVER | F_PEN_ON_PLAY))
                    and time_state(p[I_PER], p[I_CLK]) == 0 and i + 1 < len(plays)):
                q = plays[i + 1]
                if q[I_PER] == p[I_PER] and q[I_CODE] not in (TIMEOUT, TWO_MIN, END_PERIOD):
                    dt = p[I_CLK] - q[I_CLK]
                    if 0 <= dt <= 60:
                        o["clock"][0] += 1; o["clock"][1] += dt
    return out


def unit_rates(u):
    """Unit counts -> the rates the engine reasons in (None where the unit has
    no sample). yards per completion is `ypcomp`; `ypa` divides by attempts
    (completions + incompletions + interceptions, sacks excluded)."""
    att = u["comp"] + u["inc"] + u["ints"]
    plays = u["pass"] + u["rush"]
    return {"pass_rate": u["pass"] / plays if plays else None,
            "comp": u["comp"] / att if att else None,
            "ypcomp": u["comp_yds"] / u["comp"] if u["comp"] else None,
            "ypa": u["comp_yds"] / att if att else None,
            "sack": u["sacks"] / u["pass"] if u["pass"] else None,
            "int": u["ints"] / att if att else None,
            "ypc": u["rush_yds"] / u["rush"] if u["rush"] else None,
            "fum": u["fum_lost"] / plays if plays else None,
            "pen": u["pen"] / u["snaps"] if u["snaps"] else None,
            "ypp": (u["comp_yds"] + u["rush_yds"] - u["sack_yds"]) / plays if plays else None,
            "sec_per_snap": u["clock"][1] / u["clock"][0] if u["clock"][0] else None,
            "punt_ytg": u["punt"][1] / u["punt"][0] if u["punt"][0] else None,
            "ko_ytg": u["ko_ret"][1] / u["ko_ret"][0] if u["ko_ret"][0] else None}
