"""NBA play-by-play -- the basketball twin of nfl_pbp / nhl_pbp under the NBA
deep engine.

The league's own feeds are closed to this host (cdn.nba.com answers 403,
stats.nba.com never answers), so the source is ESPN's game summary
(`/summary?event=<id>`), which carries every play: each shot with its value
attempted (`pointsAttempted`), whether it went in, its distance in the text
("25-foot") and its family in the type ("Driving Layup Shot"); every free
throw as "k of n"; offensive and defensive rebounds (a TEAM rebound has no
player); turnovers, fouls by kind, timeouts, substitutions and period ends,
with the running score on every row. The participants are ordered primary
first: the shooter then the assister or blocker, the player who lost the ball
then the one who stole it.

This module turns a summary into a COMPACT record -- thirteen integers a
play -- caches a season in the deep store (`nba_pbp_<season>.pkl`, the season
named by the year it STARTS: 2025 is 2025-26, which ESPN labels 2026), and
exposes the schedule and records the deep engine's fit (research/nba_deep_fit.py)
and its team profiles (nba_deep_data) read.

Sizes, measured 2026-10-16 on 2025-03-05 POR @ BOS: a summary is 355 KB of JSON
and 0.5 s through the proxy, 441 plays; a regular season is 1,230 games, so a
cold season is ~10 minutes and ~440 MB of transfer ONCE. The compact record is
~440 plays x 13 int32 = 23 KB a game, ~28 MB a season.

Play record (array('i'), stride PLAY_W):
  per    period: 1-4, 5+ overtime
  clk    seconds left in the period
  team   0 = home, 1 = away, -1 = neither (the PRIMARY participant's side: the
         shooter's on a blocked shot, the ball-loser's on a steal)
  code   play class (CODES below)
  val    a field goal's value attempted (2 or 3); a free throw's "k of n" as
         10k + n (12 = 1 of 2); 0 otherwise
  made   1 when the shot or free throw went in
  dist   shot distance in feet from the text (-1 when not stated)
  stype  shot family (STYPES below), -1 when not a field goal
  p1     primary participant's ESPN id (0 for a team play)
  p2     secondary participant (assister / blocker / stealer), 0 if none
  hs     home score AFTER the play
  as_    away score AFTER the play
  flags  bitmask (F_* below)
"""
import array
import datetime
import re
import time

import deep_cache
import errlog
import racing

_SITE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
# ESPN's abbreviations, mapped onto the board's (basket.py reads ESPN too, so
# these only matter where ESPN itself has changed a code across seasons)
_CANON = {"GS": "GS", "NO": "NO", "NY": "NY", "SA": "SA", "UTAH": "UTAH", "WSH": "WSH"}


def canon(ab):
    ab = (ab or "").upper()
    return _CANON.get(ab, ab)


# The thirty franchises as ESPN abbreviates them. ESPN files the All-Star games
# as REGULAR-SEASON games (season type 2): LEB-GIA 2023, EAST-WEST 2024, the
# four-team CHK / KEN / SHQ / CAN mini-tournament 2025, STARS / STRIPES / WORLD
# 2026 -- no defence, first-to-40 endings, one-game "teams" that would enter
# the tables, the league's totals and a team list. League play is both sides
# from this set.
FRANCHISES = frozenset(("ATL", "BKN", "BOS", "CHA", "CHI", "CLE", "DAL", "DEN", "DET", "GS", "HOU", "IND", "LAC",
                        "LAL", "MEM", "MIA", "MIL", "MIN", "NO", "NY", "OKC", "ORL", "PHI", "PHX", "POR", "SA",
                        "SAC", "TOR", "UTAH", "WSH"))


def league_game(g):
    """A regular-season game between two franchises (a record or a schedule
    event): what every table, unit and league total is measured on."""
    return g.get("st") == 2 and g.get("home") in FRANCHISES and g.get("away") in FRANCHISES


PLAY_W = 13
(I_PER, I_CLK, I_TEAM, I_CODE, I_VAL, I_MADE, I_DIST, I_STYPE, I_P1, I_P2, I_HS, I_AS,
 I_FLAGS) = range(PLAY_W)

FGA, FT, OREB, DREB, TOV, FOUL_SHOOT, FOUL_PERS, FOUL_TAKE, FOUL_OFF, FOUL_FLAG, FOUL_TECH, \
    TIMEOUT, SUB, JUMPBALL, END_PERIOD, END_GAME, VIOLATION, EJECTION, REVIEW, OTHER = range(20)
CODES = {FGA: "fga", FT: "ft", OREB: "oreb", DREB: "dreb", TOV: "tov", FOUL_SHOOT: "foul_shooting",
         FOUL_PERS: "foul_personal", FOUL_TAKE: "foul_take", FOUL_OFF: "foul_offensive",
         FOUL_FLAG: "foul_flagrant", FOUL_TECH: "foul_technical", TIMEOUT: "timeout", SUB: "sub",
         JUMPBALL: "jumpball", END_PERIOD: "end_period", END_GAME: "end_game",
         VIOLATION: "violation", EJECTION: "ejection", REVIEW: "review", OTHER: "other"}

STYPES = ("dunk", "layup", "tip", "hook", "floater", "jumper")

F_TEAM = 1           # a team play (team rebound, team turnover, team foul): no player
F_BLOCKED = 2        # a missed field goal that was blocked
F_ASSISTED = 4       # a made field goal with an assist
F_STEAL = 8          # a turnover with a steal
F_FLAGRANT_FT = 16   # a free throw awarded for a flagrant (the ball is retained)
F_TECH_FT = 32       # a technical free throw (no possession change)
F_CLEAR = 64         # a clear-path / away-from-play foul or its free throws

_RE_DIST = re.compile(r"(\d+)-foot")
_RE_FT = re.compile(r"(\d)\s+of\s+(\d)")


def _clock_s(s):
    try:
        if ":" in str(s):
            m, sec = str(s).split(":")
            return int(round(int(m) * 60 + float(sec)))
        return int(round(float(s)))
    except (ValueError, TypeError):
        return 0


def _int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _coord_dist(c):
    """Feet from the rim by ESPN's shot coordinates (the rim at x 25, y 0; a
    25-foot three charted at (9, 19) reads 24.8), for the shots whose text
    states no distance (\"makes driving layup\"). -1 when the coordinate is
    ESPN's no-data sentinel."""
    try:
        x, y = float(c["x"]), float(c["y"])
    except (TypeError, KeyError, ValueError):
        return -1
    if not (-10 <= x <= 60 and -10 <= y <= 100):
        return -1
    return int(round(((x - 25.0) ** 2 + y ** 2) ** 0.5))


def _stype(type_text):
    t = (type_text or "").lower()
    if "dunk" in t:
        return 0
    if "tip" in t or "putback" in t:
        return 2
    if "layup" in t or "finger roll" in t:
        return 1
    if "hook" in t:
        return 3
    if "float" in t:
        return 4
    return 5


def _classify(type_text, text, shooting):
    """The play class from ESPN's type text (ids drift between seasons; the
    words have not)."""
    t = (type_text or "").lower()
    x = (text or "").lower()
    if t.startswith("free throw"):
        return FT
    if shooting:
        return FGA
    if "offensive rebound" in t:
        return OREB
    if "defensive rebound" in t or t == "rebound":
        return DREB
    if "turnover" in t or "traveling" in t or "palming" in t or "double dribble" in t \
            or "3-second" in t or "backcourt" in t or "discontinue dribble" in t or "kicked ball" in t \
            or "lane violation" in t and "turnover" in x:
        return TOV
    if "technical" in t or "delay" in t and "technical" in x:
        return FOUL_TECH
    if "flagrant" in t:
        return FOUL_FLAG
    if "offensive" in t and ("foul" in t or "charge" in t) or "charge" in t:
        return FOUL_OFF
    if "take foul" in t or "transition take" in t:
        return FOUL_TAKE
    if "shooting" in t and "foul" in t:
        return FOUL_SHOOT
    if "foul" in t:
        return FOUL_PERS
    if "timeout" in t:
        return TIMEOUT
    if "substitution" in t:
        return SUB
    if "jumpball" in t or "jump ball" in t:
        return JUMPBALL
    if "end period" in t:
        return END_PERIOD
    if "end game" in t:
        return END_GAME
    if "ejection" in t:
        return EJECTION
    if "violation" in t:
        return VIOLATION
    if "review" in t or "challenge" in t:
        return REVIEW
    return OTHER


def parse_summary(d):
    """One game's summary -> the compact record, or None when it has no plays
    or no final score. {id, season, st, date, home, away, home_id, away_id,
    home_score, away_score, periods, neutral, plays (array('i')), players
    {ESPN id: 0 home / 1 away}}."""
    if not d or not d.get("plays"):
        return None
    comp = ((d.get("header") or {}).get("competitions") or [{}])[0]
    cs = comp.get("competitors") or []
    home = next((c for c in cs if c.get("homeAway") == "home"), None)
    away = next((c for c in cs if c.get("homeAway") == "away"), None)
    if not home or not away or home.get("score") in (None, "") or away.get("score") in (None, ""):
        return None
    hid, aid = str((home.get("team") or {}).get("id")), str((away.get("team") or {}).get("id"))
    side_of_team = {hid: 0, aid: 1}
    players = {}
    for t in (d.get("boxscore") or {}).get("players") or []:
        s = side_of_team.get(str((t.get("team") or {}).get("id")))
        if s is None:
            continue
        for st in t.get("statistics") or []:
            for a in st.get("athletes") or []:
                aid_ = _int((a.get("athlete") or {}).get("id"))
                if aid_:
                    players[aid_] = s
    out = array.array("i")
    per_max = 0
    for p in d["plays"]:
        ty = p.get("type") or {}
        text = p.get("text") or ""
        code = _classify(ty.get("text"), text, bool(p.get("shootingPlay")))
        per = _int((p.get("period") or {}).get("number"))
        per_max = max(per_max, per)
        clk = _clock_s((p.get("clock") or {}).get("displayValue"))
        parts = [_int((x.get("athlete") or {}).get("id")) for x in p.get("participants") or []]
        p1 = parts[0] if parts else 0
        p2 = parts[1] if len(parts) > 1 else 0
        side = side_of_team.get(str((p.get("team") or {}).get("id")), -1)
        if p1 and p1 in players:
            side = players[p1]
        val = made = 0
        dist = stype = -1
        flags = 0 if p1 else F_TEAM
        if code == FGA:
            val = _int(p.get("pointsAttempted")) or (3 if "three point" in text.lower() else 2)
            made = 1 if _int(p.get("scoreValue")) > 0 else 0
            m = _RE_DIST.search(text)
            dist = int(m.group(1)) if m else _coord_dist(p.get("coordinate"))
            stype = _stype(ty.get("text"))
            if not made and " blocks " in text:
                flags |= F_BLOCKED
            if made and p2:
                flags |= F_ASSISTED
        elif code == FT:
            m = _RE_FT.search(text)
            k, n = (int(m.group(1)), int(m.group(2))) if m else (1, 1)
            val = 10 * k + n
            made = 1 if _int(p.get("scoreValue")) > 0 else 0
            tl = (ty.get("text") or "").lower()
            if "flagrant" in tl:
                flags |= F_FLAGRANT_FT
            if "technical" in tl:
                flags |= F_TECH_FT
            if "clear path" in tl:
                flags |= F_CLEAR
        elif code == TOV and p2:
            flags |= F_STEAL
        out.extend((per, clk, side, code, val, made, dist, stype, p1, p2,
                    _int(p.get("homeScore")), _int(p.get("awayScore")), flags))
    season = ((d.get("header") or {}).get("season") or {})
    return {"id": _int((d.get("header") or {}).get("id")), "season": _int(season.get("year")) - 1,
            "st": _int(season.get("type")), "date": comp.get("date"),
            "home": canon((home.get("team") or {}).get("abbreviation")),
            "away": canon((away.get("team") or {}).get("abbreviation")),
            "home_id": hid, "away_id": aid,
            "home_score": _int(home.get("score")), "away_score": _int(away.get("score")),
            "periods": per_max, "neutral": bool(comp.get("neutralSite")),
            "plays": out, "players": players}


def iter_plays(rec):
    """Yield each play of a record as a tuple of PLAY_W ints."""
    a = rec["plays"]
    for i in range(0, len(a), PLAY_W):
        yield tuple(a[i:i + PLAY_W])


def points_check(rec):
    """(home, away) points the record's made shots and free throws add up to:
    the check that a parse reproduced the final score."""
    pts = [0, 0]
    for p in iter_plays(rec):
        if p[I_MADE] and p[I_TEAM] in (0, 1):
            if p[I_CODE] == FGA:
                pts[p[I_TEAM]] += p[I_VAL]
            elif p[I_CODE] == FT:
                pts[p[I_TEAM]] += 1
    return tuple(pts)


# ---- Fetch + season cache ----------------------------------------------------
def _get(url, timeout=40):
    return racing._get_json(url, timeout=timeout)


def _day_events(ymd):
    d = _get(f"{_SITE}/scoreboard?dates={ymd}", timeout=20)
    out = []
    for e in (d or {}).get("events") or []:
        comp = (e.get("competitions") or [{}])[0]
        cs = comp.get("competitors") or []
        home = next((c for c in cs if c.get("homeAway") == "home"), None)
        away = next((c for c in cs if c.get("homeAway") == "away"), None)
        if not home or not away:
            continue
        se = e.get("season") or {}
        out.append({"id": str(e.get("id")), "date": e.get("date"), "st": _int(se.get("type")),
                    "espn_year": _int(se.get("year")),
                    "state": ((comp.get("status") or {}).get("type") or {}).get("state"),
                    "home": canon((home.get("team") or {}).get("abbreviation")),
                    "away": canon((away.get("team") or {}).get("abbreviation")),
                    "home_pts": _int(home.get("score"), -1), "away_pts": _int(away.get("score"), -1),
                    "neutral": bool(comp.get("neutralSite")),
                    "notes": " / ".join(n.get("headline") or "" for n in comp.get("notes") or [])})
    return out


def schedule(season, log=None, today=None):
    """Every game of the season starting in `season` (ESPN year season + 1):
    regular season (2), postseason (3) and the play-in (5), one scoreboard
    call per date from October 1 to June 30. Dates after `today` are walked
    too, so the remaining schedule is known."""
    out = {}
    d0, d1 = datetime.date(int(season), 10, 1), datetime.date(int(season) + 1, 6, 30)
    day = d0
    while day <= d1:
        try:
            for ev in _day_events(day.strftime("%Y%m%d")):
                if ev["espn_year"] == int(season) + 1 and ev["st"] in (2, 3, 5):
                    out[ev["id"]] = ev
        except Exception as e:
            errlog.note("NBAPBP-day", e, path=day.isoformat())
        day += datetime.timedelta(days=1)
    if log:
        log(f"[nba-pbp] {season}: {len(out)} scheduled games")
    return sorted(out.values(), key=lambda g: (g["date"] or "", g["id"]))


def fetch_game(event_id):
    """One finished game's compact record (one summary call), or None."""
    d = _get(f"{_SITE}/summary?event={event_id}")
    return parse_summary(d) if d else None


def _key(season):
    return f"nba_pbp_{int(season)}"


def load_season(season):
    payload, _ts = deep_cache.load(_key(season))
    if not payload or not isinstance(payload, dict):
        return {"games": {}, "events": [], "updated": 0.0}
    return payload


def season_records(season, max_fetch=1500, log=None, refresh_schedule=True):
    """Every finished game of `season` as a compact record, fetching what the
    cache lacks (at most `max_fetch` per call). The schedule is MERGED by
    event id, never replaced. A record whose plays do not add up to the final
    score is not stored; its id goes in `mismatch`."""
    store = load_season(season)
    games = store.setdefault("games", {})
    if refresh_schedule:
        ev = {e["id"]: e for e in store.get("events") or []}
        for g in schedule(season, log=log):
            ev[g["id"]] = g
        store["events"] = sorted(ev.values(), key=lambda g: (g["date"] or "", g["id"]))
    fetched, mismatch = 0, list(store.get("mismatch") or [])
    for g in store.get("events") or []:
        if g.get("state") != "post" or g["id"] in games:
            continue
        if fetched >= max_fetch:
            break
        try:
            rec = fetch_game(g["id"])
        except Exception as e:
            errlog.note("NBAPBP-game", e, path=g["id"])
            rec = None
        if not rec:
            continue
        fetched += 1
        if points_check(rec) != (rec["home_score"], rec["away_score"]):
            if g["id"] not in mismatch:
                mismatch.append(g["id"])
            continue
        rec["st"] = g["st"]
        games[g["id"]] = rec
        if log and fetched % 100 == 0:
            log(f"[nba-pbp] {season}: {len(games)} games held")
    store["mismatch"] = mismatch
    if fetched or not store.get("updated"):
        store["updated"] = time.time()
        deep_cache.save(_key(season), store)
    return store


def coverage(store):
    """(finished games, records held, mismatches) -- the deep job's quality gate."""
    done = [e for e in store.get("events") or [] if e.get("state") == "post"]
    return len(done), sum(1 for e in done if e["id"] in (store.get("games") or {})), len(store.get("mismatch") or [])
