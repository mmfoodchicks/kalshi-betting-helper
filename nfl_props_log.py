"""Kalshi's NFL player props, kept for every game until its kickoff -- the
price history a prop anchor needs and no feed keeps.

Kalshi lists passing, rushing and receiving-yard ladders, reception ladders
and anytime-touchdown markets on every game (kalshi_nfl._PROP_SERIES). Where
a market and a projection model both exist the market has tended to win
(soccer.py: the goals model lost to the closing price in all 27 leagues
tested, research/reports/soccer_accuracy.md), so the DFS players'
projections may do better leaning toward these prices -- a question only
prices recorded BEFORE kickoff and outcomes after can answer. This keeps
them: every believable rung de-vigged off the mids
(kalshi_nfl.prop_ladders) and each ladder's implied mean
(nfl_preseason.implied_mean), per game, rewritten on every pass while the
game has not started and FROZEN from LEAD_S before its kickoff on -- a live
price carries the game (CLAUDE.md) and must never stand in for a pre-game
one. Kickoffs and states come from ESPN's NFL scoreboard; a game ESPN does
not list is not logged. Stored in the deep store as nfl_props_<season>_w<week>;
research/nfl_prop_anchor.py holds the test, its rule written before the
first logged week was read. Sleeper's projection for every player on a team
still to kick off is kept beside the prices on the same pass and frozen the
same way, so the test compares the market with the projection the boards
actually had, not with whatever Sleeper serves for the week afterwards.
"""
import time

import errlog

KEY = "nfl_props_{season}_w{week:02d}"
LATEST = "nfl_props_latest"      # the key of the week the last pass wrote (the weather route reads it)
LEAD_S = 300            # a game kicking off inside five minutes is frozen
EVERY_S = 1800          # one pass every half hour is plenty for a pre-game price
_ESPN = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
# Sleeper's projected components kept per player (the ones a prop prices,
# plus the touchdowns the anytime-TD market prices)
SLEEPER_STATS = ("pass_yd", "rush_yd", "rec_yd", "rec", "rec_tgt", "rush_att", "pass_att",
                 "pass_td", "rush_td", "rec_td", "pts_ppr")
_last = [0.0]


def espn_games(payload):
    """(season, week, {frozenset of the two team codes: {"kickoff", "state",
    "id", "home", "away", "venue"}}) from ESPN's NFL scoreboard payload, the
    codes in Kalshi's canon."""
    import kalshi
    import kalshi_nfl
    season = ((payload.get("season") or {}).get("year"))
    week = ((payload.get("week") or {}).get("number"))
    out = {}
    for e in payload.get("events") or []:
        c = (e.get("competitions") or [{}])[0]
        side = {x.get("homeAway"): kalshi_nfl._canon(((x.get("team") or {}).get("abbreviation") or ""))
                for x in c.get("competitors") or []}
        codes = frozenset(side.values())
        if len(codes) != 2:
            continue
        out[codes] = {"kickoff": kalshi._parse_time(e.get("date")),
                      "state": ((c.get("status") or {}).get("type") or {}).get("state"),
                      "id": e.get("id"), "home": side.get("home"), "away": side.get("away"),
                      "venue": (c.get("venue") or {}).get("fullName")}
    return season, week, out


def snapshot(now=None, payload=None, index=None, ladders=None, sleeper=None, forecast=None):
    """One logging pass: every game still ahead of its kickoff gets its props
    rewritten, every player of its two teams his Sleeper projection, and,
    inside nfl_weather.FORECAST_AHEAD_S, its stadium's kickoff forecast;
    every other game keeps what it had. Returns {"key", "updated", "frozen",
    "games", "sleeper", "weather"}. `payload`, `index`, `ladders` (a suffix ->
    props function), `sleeper` (Sleeper's projection rows) and `forecast` (a
    game -> forecast function) are for tests; production reads ESPN,
    kalshi_nfl, Sleeper and weather.py."""
    import deep_cache
    import kalshi_nfl
    import nfl_preseason as pre
    import racing
    now = time.time() if now is None else now
    if payload is None:
        payload = racing._get_json(_ESPN) or {}
    season, week, games = espn_games(payload)
    if not season or not week:
        return None
    idx = kalshi_nfl.index() if index is None else index
    ladders = ladders or kalshi_nfl.prop_ladders
    key = KEY.format(season=int(season), week=int(week))
    store, _ts = deep_cache.load(key)
    store = store or {"season": int(season), "week": int(week), "games": {}}
    store.setdefault("sleeper", {})
    store.setdefault("weather", {})
    updated = frozen = 0
    live = set()                       # team codes whose game is still ahead of its kickoff
    for codes, g in games.items():
        if g.get("kickoff") and g.get("state") == "pre" and g["kickoff"] - now >= LEAD_S:
            live |= set(codes)
    wx_n = _weather(store, games, now, forecast)
    if live:
        rows = sleeper
        if rows is None:
            import nfl_data
            rows = nfl_data._rows(int(season), int(week), ("QB", "RB", "WR", "TE")) or []
        for r in rows:
            pl = r.get("player") or {}
            team = kalshi_nfl._canon(r.get("team") or pl.get("team") or "")
            name = f"{pl.get('first_name', '')} {pl.get('last_name', '')}".strip()
            if team not in live or not name:
                continue
            st = r.get("stats") or {}
            store["sleeper"][kalshi_nfl._norm(name)] = {
                "name": name, "team": team, "pos": pl.get("position"), "ts": round(now),
                "stats": {k: st[k] for k in SLEEPER_STATS if st.get(k) is not None}}
    for suffix, e in (idx or {}).items():
        g = games.get(e.get("pair"))
        if not g or not g.get("kickoff"):
            continue
        if g.get("state") != "pre" or g["kickoff"] - now < LEAD_S:
            frozen += 1
            continue
        lad = ladders(suffix) or {}
        if not lad:
            continue
        players = {}
        for (stat, nm), v in lad.items():
            rungs = [(float(x), round(float(p), 4)) for x, p in v.get("rungs") or []]
            mean = pre.implied_mean(stat, rungs)
            players.setdefault(nm, {"name": v.get("name") or nm, "stats": {}})["stats"][stat] = {
                "rungs": rungs, "mean": None if mean is None else round(mean, 3)}
        store["games"][suffix] = {"ts": round(now), "kickoff": g["kickoff"], "espn_id": g.get("id"),
                                  "teams": sorted(e["pair"]), "players": players}
        updated += 1
    if updated or live or wx_n:
        deep_cache.save(key, store)
        deep_cache.save(LATEST, key)
    return {"key": key, "updated": updated, "frozen": frozen, "games": len(store["games"]),
            "sleeper": len(store["sleeper"]), "weather": wx_n}


def _weather(store, games, now, forecast=None):
    """Each game still ahead of its kickoff (by LEAD_S) and inside the
    forecast window gets its stadium's forecast rewritten; a game past that
    point keeps the last one it had -- the frozen pre-game forecast. One
    stadium failing is ledgered and skipped. Returns how many were written."""
    import nfl_weather
    forecast = forecast or nfl_weather.forecast
    n = 0
    for _codes, g in games.items():
        ko = g.get("kickoff")
        if not ko or g.get("state") != "pre" or not (LEAD_S <= ko - now <= nfl_weather.FORECAST_AHEAD_S):
            continue
        try:
            wx = forecast(g)
        except Exception as e:
            errlog.note("NFLPROPS-weather", e)
            continue
        store["weather"][str(g.get("id"))] = {
            "ts": round(now), "kickoff": ko, "home": g.get("home"), "away": g.get("away"),
            "label": f"{g.get('away')} @ {g.get('home')}", "venue": g.get("venue"), "wx": wx}
        n += 1
    return n


def export(back=0):
    """{"key", "payload"} for the latest logged week (`back` weeks before it),
    for the error-log workflow to commit under props/ on sim-history -- the
    anchor test reads weeks 6-10 from there, and the server's deep store is
    not reachable from anywhere else. None before the first pass."""
    import deep_cache
    latest, _ts = deep_cache.load(LATEST)
    if not latest:
        return None
    season, week = (int(x) for x in latest.replace("nfl_props_", "").split("_w"))
    key = KEY.format(season=season, week=week - int(back))
    payload, ts = deep_cache.load(key)
    return {"key": key, "generated_at": ts, "payload": payload} if payload else None


def tick():
    """The recorder loop's hook: one pass every EVERY_S, never two at once
    across a restart's first minutes (the loop runs in the one worker that
    owns background jobs)."""
    now = time.time()
    if now - _last[0] < EVERY_S:
        return None
    _last[0] = now
    try:
        return snapshot(now)
    except Exception as e:
        errlog.note("NFLPROPS-snapshot", e)
        return None
