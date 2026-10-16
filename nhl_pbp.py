"""NHL play-by-play -- the hockey twin of nfl_pbp under the NHL deep engine.

The league's own feed (api-web.nhle.com, no key) carries, for every game, each
event with its period, the clock, the STRENGTH STATE (`situationCode`: away
goalie, away skaters, home skaters, home goalie -- "1551" is five-on-five with
both goalies in, "0651" the away side's net empty), the shooter and goalie, the
shot type, the rink coordinates and which end the home team defends. ESPN's
hockey summary has none of the strength states, so the NHL feed is the source;
ESPN stays the slate's (hockey.py) because its abbreviations are the board's.

This module turns a game's feed into a COMPACT record -- fourteen integers an
event -- caches a season of them in the deep store (`nhl_pbp_<season>.pkl`,
the season named by the year it STARTS: 2025 is 2025-26), and exposes the
schedule and the records the deep engine's fit (research/nhl_deep_fit.py) and
its team profiles (nhl_deep_data) read.

Sizes, measured 2026-10-16 on 2025-03-05 WSH @ NYR: the feed is 135 KB of
JSON and 0.4 s through the proxy, 330 events of which ~120 are shot attempts;
a regular season is 1,312 games, so a cold season is ~9 minutes and ~180 MB of
transfer ONCE (finished games never change). The compact record keeps the
~200 events the engine reads x 14 int32 = 11 KB a game, ~15 MB a season.

Event record (array('i'), stride EV_W):
  per    period number (1-3 regulation, 4 the first overtime, 5+ playoff
         overtimes; a shootout's attempts carry F_SO)
  t      seconds elapsed in the period
  team   0 = home, 1 = away, -1 = neither (the side the event BELONGS to: the
         shooter's for every shot attempt including a blocked one, the
         offender's for a penalty, the winner's for a faceoff)
  code   event class (CODES below)
  sit    the situationCode as an int (1551 = 5v5), -1 when absent
  dist   shot distance to the net the shooter attacks, in tenths of a foot
         (-1 when not a shot or no coordinates)
  ang    absolute shot angle off the goal line's normal, degrees (-1 likewise)
  stype  shot type (STYPES below), -1 when not a shot
  p1     shooter / scorer / penalized player / faceoff winner / hitter /
         the player who gave or took the puck away
  p2     first assist / blocker / player who drew the penalty / faceoff loser /
         player hit
  p3     second assist
  goalie the goalie in net for a shot attempt (0 = the net was empty)
  dur    penalty minutes (2, 4 for a double minor, 5, 10); 0 otherwise
  flags  bitmask (F_* below)
"""
import array
import datetime
import math
import time

import deep_cache
import errlog
import racing

_API = "https://api-web.nhle.com/v1"
# The league's abbreviations against ESPN's (the board's, hockey.py): five
# differ. Arizona's ARI became Utah's UTA in 2024-25.
_TO_ESPN = {"LAK": "LA", "NJD": "NJ", "SJS": "SJ", "TBL": "TB", "UTA": "UTAH"}
_FROM_ESPN = {v: k for k, v in _TO_ESPN.items()}


def to_espn(ab):
    return _TO_ESPN.get(ab, ab)


def from_espn(ab):
    return _FROM_ESPN.get(ab, ab)

EV_W = 14
(I_PER, I_T, I_TEAM, I_CODE, I_SIT, I_DIST, I_ANG, I_STYPE, I_P1, I_P2, I_P3, I_GOALIE, I_DUR,
 I_FLAGS) = range(EV_W)

FACEOFF, SOG, MISS, BLOCK, GOAL, PENALTY, HIT, GIVEAWAY, TAKEAWAY, STOPPAGE, PERIOD_START, \
    PERIOD_END, DELAYED_PEN, GAME_END, OTHER = range(15)
CODES = {FACEOFF: "faceoff", SOG: "shot-on-goal", MISS: "missed-shot", BLOCK: "blocked-shot",
         GOAL: "goal", PENALTY: "penalty", HIT: "hit", GIVEAWAY: "giveaway", TAKEAWAY: "takeaway",
         STOPPAGE: "stoppage", PERIOD_START: "period-start", PERIOD_END: "period-end",
         DELAYED_PEN: "delayed-penalty", GAME_END: "game-end", OTHER: "other"}
_CODE_OF = {v: k for k, v in CODES.items()}
# Shot attempts (Corsi): what the engine's shot generation is fitted on.
ATTEMPTS = frozenset((SOG, MISS, BLOCK, GOAL))
# Unblocked attempts (Fenwick): what shot QUALITY is fitted on -- a blocked
# shot's coordinates are where it was blocked, not where it was taken from.
UNBLOCKED = frozenset((SOG, MISS, GOAL))

STYPES = ("wrist", "snap", "slap", "backhand", "tip-in", "deflected", "wrap-around", "poke",
          "bat", "between-legs", "cradle")
_STYPE_OF = {s: i for i, s in enumerate(STYPES)}

F_SO = 1             # a shootout attempt
F_EN = 2             # a shot attempt at an EMPTY net
F_PS = 4             # a penalty shot
F_POST = 8           # a missed shot that hit the post or crossbar
F_MAJOR = 16         # a major (5) or match penalty
F_MISC = 32          # a misconduct (no man advantage)
F_BENCH = 64         # a bench minor (no player in the box of record)

# The net sits 89 feet from centre ice on each end.
_NET_X = 89.0


def situation(code):
    """(away goalie in, away skaters, home skaters, home goalie in) from a
    situationCode int, or None."""
    if code is None or code < 0:
        return None
    s = f"{int(code):04d}"
    return int(s[0]), int(s[1]), int(s[2]), int(s[3])


def strength(sit, side):
    """The strength state from `side`'s view (0 home, 1 away): (own skaters,
    opponent skaters, own goalie in, opponent goalie in), or None."""
    st = situation(sit)
    if st is None:
        return None
    ag, ask, hsk, hg = st
    return (hsk, ask, hg, ag) if side == 0 else (ask, hsk, ag, hg)


def _secs(mmss):
    try:
        m, s = str(mmss).split(":")
        return int(m) * 60 + int(s)
    except (ValueError, AttributeError):
        return 0


def _geom(x, y, side, home_defends):
    """(distance in tenths of a foot, absolute angle in degrees) from the
    shooter's side to the net it attacks. The home team attacks the end
    opposite the one it defends; the feed says which, event by event."""
    if x is None or y is None or home_defends not in ("left", "right"):
        return -1, -1
    home_attacks_right = home_defends == "left"
    attacks_right = home_attacks_right if side == 0 else not home_attacks_right
    nx = _NET_X if attacks_right else -_NET_X
    dx, dy = abs(nx - float(x)), float(y)
    d = math.hypot(dx, dy)
    ang = math.degrees(math.atan2(abs(dy), dx)) if d > 0 else 0.0
    return int(round(d * 10)), int(round(ang))


def parse_game(d):
    """One game's feed -> the compact record, or None when the feed has no
    events or no final score. {id, season, gtype, date, home, away, home_id,
    away_id, home_score, away_score, last (REG / OT / SO), neutral, events
    (array('i')), roster {player id: (0 home / 1 away, position code)}}."""
    if not d or not d.get("plays"):
        return None
    ht, at = d.get("homeTeam") or {}, d.get("awayTeam") or {}
    hid, aid = ht.get("id"), at.get("id")
    if hid is None or aid is None or ht.get("score") is None or at.get("score") is None:
        return None
    side_of_team = {hid: 0, aid: 1}
    roster = {}
    for r in d.get("rosterSpots") or []:
        if r.get("playerId") and r.get("teamId") in side_of_team:
            roster[int(r["playerId"])] = (side_of_team[r["teamId"]], r.get("positionCode") or "")
    ev = array.array("i")

    def side_of_player(pid, fallback):
        r = roster.get(int(pid)) if pid else None
        return r[0] if r else fallback

    for p in d["plays"]:
        code = _CODE_OF.get(p.get("typeDescKey"), OTHER)
        det = p.get("details") or {}
        per_d = p.get("periodDescriptor") or {}
        per = int(per_d.get("number") or 0)
        flags = F_SO if per_d.get("periodType") == "SO" else 0
        owner = side_of_team.get(det.get("eventOwnerTeamId"), -1)
        try:
            sit = int(p.get("situationCode")) if p.get("situationCode") not in (None, "") else -1
        except (TypeError, ValueError):
            sit = -1
        p1 = p2 = p3 = goalie = dur = 0
        dist = ang = stype = -1
        team = owner
        if code in ATTEMPTS:
            p1 = int(det.get("shootingPlayerId") or det.get("scoringPlayerId") or 0)
            # the SHOOTER's side, whatever the feed calls the event's owner (it
            # has named the blocking team on blocked shots in some seasons)
            team = side_of_player(p1, owner)
            if code == GOAL:
                p2 = int(det.get("assist1PlayerId") or 0)
                p3 = int(det.get("assist2PlayerId") or 0)
            elif code == BLOCK:
                p2 = int(det.get("blockingPlayerId") or 0)
            goalie = int(det.get("goalieInNetId") or 0)
            if code != BLOCK and not goalie and not (flags & F_SO):
                flags |= F_EN
            reason = det.get("reason") or ""
            if code == MISS and ("post" in reason or "crossbar" in reason):
                flags |= F_POST
            stype = _STYPE_OF.get(det.get("shotType"), -1)
            if team in (0, 1):
                dist, ang = _geom(det.get("xCoord"), det.get("yCoord"), team, p.get("homeTeamDefendingSide"))
        elif code == PENALTY:
            p1 = int(det.get("committedByPlayerId") or 0)
            p2 = int(det.get("drawnByPlayerId") or 0)
            team = side_of_player(p1, owner)
            dur = int(det.get("duration") or 0)
            tc = (det.get("typeCode") or "").upper()
            if tc in ("MAJ", "MAT") or dur == 5:
                flags |= F_MAJOR
            if tc in ("MIS", "GMIS") or (dur == 10 and tc not in ("MAJ", "MAT")):
                flags |= F_MISC
            if tc == "BEN" or not p1:
                flags |= F_BENCH
            if tc == "PS" or "penalty-shot" in (det.get("descKey") or ""):
                flags |= F_PS
        elif code == FACEOFF:
            p1, p2 = int(det.get("winningPlayerId") or 0), int(det.get("losingPlayerId") or 0)
        elif code == HIT:
            p1, p2 = int(det.get("hittingPlayerId") or 0), int(det.get("hitteePlayerId") or 0)
        elif code in (GIVEAWAY, TAKEAWAY):
            p1 = int(det.get("playerId") or 0)
        ev.extend((per, _secs(p.get("timeInPeriod")), team, code, sit, dist, ang, stype,
                   p1, p2, p3, goalie, dur, flags))
    go = d.get("gameOutcome") or {}
    return {"id": int(d.get("id") or 0), "season": int(str(d.get("season") or "0")[:4] or 0),
            "gtype": int(d.get("gameType") or 0), "date": d.get("gameDate"),
            "home": ht.get("abbrev"), "away": at.get("abbrev"), "home_id": hid, "away_id": aid,
            "home_score": int(ht["score"]), "away_score": int(at["score"]),
            "last": go.get("lastPeriodType") or "REG", "neutral": bool(d.get("neutralSite")),
            "events": ev, "roster": roster}


def iter_events(rec):
    """Yield each event of a record as a tuple of EV_W ints."""
    a = rec["events"]
    for i in range(0, len(a), EV_W):
        yield tuple(a[i:i + EV_W])


def goals_check(rec):
    """(home, away) goals the record's events add up to, the shootout winner's
    extra goal included the way the league scores it: the check that a parse
    reproduced the final score."""
    h = a = 0
    so = [0, 0]
    for e in iter_events(rec):
        if e[I_CODE] != GOAL:
            continue
        if e[I_FLAGS] & F_SO:
            so[e[I_TEAM]] += 1
        elif e[I_TEAM] == 0:
            h += 1
        elif e[I_TEAM] == 1:
            a += 1
    if so != [0, 0]:
        if so[0] > so[1]:
            h += 1
        elif so[1] > so[0]:
            a += 1
    return h, a


# ---- Fetch + season cache ----------------------------------------------------
def _get(url, timeout=40):
    return racing._get_json(url, timeout=timeout)


def season_label(season):
    """2025 -> 20252026, the league's season id."""
    return int(f"{int(season)}{int(season) + 1}")


def _week_games(date):
    d = _get(f"{_API}/schedule/{date}", timeout=20)
    out = []
    for day in (d or {}).get("gameWeek") or []:
        for g in day.get("games") or []:
            ht, at = g.get("homeTeam") or {}, g.get("awayTeam") or {}
            out.append({"id": int(g["id"]), "season": int(g.get("season") or 0), "gtype": int(g.get("gameType") or 0),
                        "date": day.get("date"), "start": g.get("startTimeUTC"),
                        "state": g.get("gameState"), "home": ht.get("abbrev"), "away": at.get("abbrev"),
                        "home_score": ht.get("score"), "away_score": at.get("score"),
                        "last": (g.get("gameOutcome") or {}).get("lastPeriodType"),
                        "neutral": bool(g.get("neutralSite"))})
    return out, (d or {}).get("nextStartDate")


# A game is final once the league marks it OFF (official) or FINAL.
FINAL_STATES = ("OFF", "FINAL")


def schedule(season, log=None):
    """Every regular-season and playoff game of `season` (by its start year)
    from the league's weekly schedule, walked from September to July."""
    label = season_label(season)
    date = datetime.date(int(season), 9, 1)
    end = datetime.date(int(season) + 1, 7, 15)
    games, guard = {}, 0
    while date <= end and guard < 60:
        guard += 1
        rows, nxt = _week_games(date.isoformat())
        for g in rows:
            if g["season"] == label and g["gtype"] in (2, 3):
                games[g["id"]] = g
        if not nxt:
            date += datetime.timedelta(days=7)
        else:
            nd = datetime.date.fromisoformat(nxt)
            date = nd if nd > date else date + datetime.timedelta(days=7)
    if log:
        log(f"[nhl-pbp] {season}: {len(games)} scheduled games")
    return sorted(games.values(), key=lambda g: (g["date"] or "", g["id"]))


def fetch_game(game_id):
    """One game's compact record (one feed call), or None."""
    d = _get(f"{_API}/gamecenter/{int(game_id)}/play-by-play")
    return parse_game(d) if d else None


def _key(season):
    return f"nhl_pbp_{int(season)}"


def load_season(season):
    """The cached season store: {"games": {id: rec}, "events": [...], "updated": epoch}."""
    payload, _ts = deep_cache.load(_key(season))
    if not payload or not isinstance(payload, dict):
        return {"games": {}, "events": [], "updated": 0.0}
    return payload


def season_records(season, max_fetch=1500, log=None, refresh_schedule=True):
    """Every finished game of `season` as a compact record, fetching what the
    cache lacks (at most `max_fetch` feeds per call; call again to finish).
    The schedule is MERGED by game id, never replaced (nfl_pbp's lesson: a
    failed week must not erase games). A record whose events do not add up to
    the final score is NOT stored and is counted in `mismatch`."""
    store = load_season(season)
    games = store.setdefault("games", {})
    if refresh_schedule:
        try:
            ev = {e["id"]: e for e in store.get("events") or []}
            for g in schedule(season, log=log):
                ev[g["id"]] = g
            store["events"] = sorted(ev.values(), key=lambda g: (g["date"] or "", g["id"]))
        except Exception as e:
            errlog.note("NHLPBP-sched", e, path=str(season))
    fetched, mismatch = 0, list(store.get("mismatch") or [])
    for g in store.get("events") or []:
        if g.get("state") not in FINAL_STATES or g["id"] in games:
            continue
        if fetched >= max_fetch:
            break
        try:
            rec = fetch_game(g["id"])
        except Exception as e:
            errlog.note("NHLPBP-game", e, path=str(g["id"]))
            rec = None
        if not rec:
            continue
        fetched += 1
        if goals_check(rec) != (rec["home_score"], rec["away_score"]):
            if g["id"] not in mismatch:
                mismatch.append(g["id"])
            continue
        games[g["id"]] = rec
        if log and fetched % 100 == 0:
            log(f"[nhl-pbp] {season}: {len(games)} games held")
    store["mismatch"] = mismatch
    if fetched or not store.get("updated"):
        store["updated"] = time.time()
        deep_cache.save(_key(season), store)
    return store


def coverage(store):
    """(finished games, records held, mismatches) -- the deep job's quality gate."""
    done = [e for e in store.get("events") or [] if e.get("state") in FINAL_STATES]
    return len(done), sum(1 for e in done if e["id"] in (store.get("games") or {})), len(store.get("mismatch") or [])


# ---- the game walk shared by the deep engine's fit and its team profiles ------
# (research/nhl_deep_fit.py builds the tables from it, nhl_deep_data the team
# units; one definition, so a game is cut the same way for both)
DIST_EDGES = (10, 20, 30, 40, 55)          # feet; bucket i is < DIST_EDGES[i], the last is beyond
LATE_SECS = 300
REB_SECS = 3


def dist_bucket(dist_tenths):
    if dist_tenths < 0:
        return len(DIST_EDGES)
    f = dist_tenths / 10.0
    for i, e in enumerate(DIST_EDGES):
        if f < e:
            return i
    return len(DIST_EDGES)


def skey(st):
    return ",".join(str(x) for x in st)


def clamp_sd(sd):
    return max(-3, min(3, sd))


def walk(rec):
    """Yield (kind, payload) over one game in order, with the derived state:
      ("time", (home-view state, sd home view, late, per, seconds))
      ("att", (side, state, sd, late, per, code, dist bucket, reb, flags))
      ("pen", (side, state, sd, late, per, kind, delayed))  one per same-second
              penalty GROUP that leaves a man advantage: the side with more
              penalty minutes, its net minutes as the kind (minor / double /
              major), whether a delayed-penalty window preceded it
      ("pen_even", (per,))  a group that leaves none (matching minors, fights,
              misconducts)
      ("dwin", (seconds, attempts, unblocked, on goal, goals, waived))
      ("so", (side, goal 0/1))
    A DELAYED PENALTY is its own phase: from the call (the feed's
    delayed-penalty row, owned by the penalized side -- 3,232 of 3,244 on
    2025-26) until the whistle (that side's penalty row) or a goal by the
    fouled side (which waives a minor), the fouled side plays with its net
    empty. Its clock and attempts are kept OUT of the strength-state tables
    (they would read as a late extra attacker) and summarised per window;
    the penalty is charged to the state at the call, not to the empty net."""
    score = [0, 0]
    prev_per, prev_t = None, 0
    last_sog = {0: -99, 1: -99}            # time of each side's last saved shot
    delay = None                           # [penalized side, home-view state at the call, start t, att, unbl, sog, goals]
    grp = []                               # the current same-second penalty group
    prev_hst = None                        # the previous event's home-view state
    for e in _with_flush(rec):
        if e is None or (grp and (e[I_PER], e[I_T]) != grp[0][0]):
            if grp:
                yield _penalty_incident(grp)
                grp = []
            if e is None:
                break
        per, t, side, code, sit, flags = e[I_PER], e[I_T], e[I_TEAM], e[I_CODE], e[I_SIT], e[I_FLAGS]
        if flags & F_SO:
            if code in ATTEMPTS and side in (0, 1):
                yield "so", (side, 1 if code == GOAL else 0)
            continue
        if per != prev_per:
            prev_per, prev_t = per, 0
            last_sog = {0: -99, 1: -99}
            delay = None
            prev_hst = None
        dt = max(0, t - prev_t)
        prev_t = t
        late = 1 if (per == 3 and t >= 1200 - LATE_SECS) else 0
        hst = strength(sit, 0)
        if delay is not None:
            if code in ATTEMPTS and side == 1 - delay[0]:
                delay[3] += 1
                if code != BLOCK:
                    delay[4] += 1
                if code == SOG:
                    delay[5] += 1
            if code == GOAL and side == 1 - delay[0]:
                delay[6] += 1
                yield "dwin", (t - delay[2], delay[3], delay[4], delay[5], delay[6], 1)
                score[side] += 1
                delay = None
                continue
            if code == PENALTY and side == delay[0]:
                snap = delay[1]
                yield "dwin", (t - delay[2], delay[3], delay[4], delay[5], delay[6], 0)
                delay = None
                st = snap if side == 0 else (snap[1], snap[0], snap[3], snap[2])
                grp.append(((per, t), side, st, clamp_sd(score[side] - score[1 - side]), late, per,
                            e[I_DUR], flags, 1))
                continue
            if code in ATTEMPTS or code == GOAL:
                if code == GOAL and side in (0, 1):
                    score[side] += 1
                continue
            continue
        if code == DELAYED_PEN and side in (0, 1) and hst is not None:
            yield from _charge(prev_hst, hst, clamp_sd(score[0] - score[1]), late, per, dt)
            delay = [side, hst, t, 0, 0, 0, 0]
            prev_hst = None
            continue
        if hst is not None:
            yield from _charge(prev_hst, hst, clamp_sd(score[0] - score[1]), late, per, dt)
            prev_hst = hst
        if side not in (0, 1) or hst is None:
            if code == GOAL and side in (0, 1):
                score[side] += 1
            continue
        st = strength(sit, side)
        sd = clamp_sd(score[side] - score[1 - side])
        if code in ATTEMPTS:
            reb = 1 if (code != BLOCK and t - last_sog[side] <= REB_SECS) else 0
            yield "att", (side, st, sd, late, per, code, dist_bucket(e[I_DIST]) if code != BLOCK else -1, reb, flags)
            if code == SOG:
                last_sog[side] = t
            if code == GOAL:
                score[side] += 1
        elif code == PENALTY:
            grp.append(((per, t), side, st, sd, late, per, e[I_DUR], flags, 0))


def _charge(prev_hst, hst, sd, late, per, dt):
    """The clock since the previous event: half to each state when they differ."""
    if not dt:
        return
    if prev_hst is None or prev_hst == hst:
        yield "time", (hst, sd, late, per, dt)
    else:
        yield "time", (prev_hst, sd, late, per, dt / 2.0)
        yield "time", (hst, sd, late, per, dt / 2.0)


def _with_flush(rec):
    yield from iter_events(rec)
    yield None


def _penalty_incident(grp):
    """One same-second penalty group -> ("pen", ...) for the side left a man
    down by NET minutes (two a minor, four a double, five or more a major), or
    ("pen_even", ...) when the minutes cancel (matching minors, a fight) or
    only misconducts were called."""
    mins = [0, 0]
    first = {}
    delayed = 0
    for (_pt, side, st, sd, late, per, dur, flags, dl) in grp:
        delayed |= dl
        if flags & F_MISC or dur not in (2, 4, 5):
            continue
        mins[side] += dur
        first.setdefault(side, (st, sd, late, per))
    net = mins[0] - mins[1]
    if net == 0:
        return "pen_even", (grp[0][5],)
    side = 0 if net > 0 else 1
    d = abs(net)
    kind = "minor" if d < 4 else ("double" if d == 4 else "major")
    st, sd, late, per = first.get(side) or (grp[0][2], grp[0][3], grp[0][4], grp[0][5])
    return "pen", (side, st, sd, late, per, kind, delayed)
