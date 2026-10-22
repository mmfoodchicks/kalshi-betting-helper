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


def snapshot(now=None, payload=None, index=None, ladders=None, sleeper=None, forecast=None, sims=None):
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
    md = _model_rungs(store, games, idx, now, int(season), int(week), sims)
    if updated or live or wx_n or md:
        deep_cache.save(key, store)
        deep_cache.save(LATEST, key)
    if md and store.get("drift"):
        deep_cache.save(DRIFT_KEY, dict(store["drift"], week_key=key))
    return {"key": key, "updated": updated, "frozen": frozen, "games": len(store["games"]),
            "sleeper": len(store["sleeper"]), "weather": wx_n, "model_rungs": md}


# THE MODEL BESIDE THE MARKET, for the record nothing but the moneyline had
# (research/reports/nfl_accuracy_gaps.md, 2026-10-19). Every leg the combo
# maker's slate sim prices that Kalshi books with a believable quote gets the
# sim's own probability and Kalshi's de-vigged mid, twice: "first" the pass
# that first saw it, "last" every pass until the game freezes (LEAD_S), so
# the frozen "last" is the pre-game close. Graded later against the box and
# the final, it fits a blend weight per leg type the way calibrate does for
# baseball, and first-vs-last says whether the market moves toward the
# model (the model knowing something) or away. The sim is READ from the
# shared store (the PC's warm, or the tab's last build), never run here: no
# CPU-bound work in a gunicorn process. Totals keep the OVER side only and
# the moneyline both clubs (the drift below reads the home side).
SIM_NS = (4000, 3000)          # the PC's warm (pc_worker) first, then the tab's default
SIM_MAX_AGE = 3600             # an older slate sim is skipped, not logged as the model


def _rung_key(kref):
    t = kref.get("t")
    if t == "prop":
        return f"prop|{kref.get('stat')}|{kref.get('player')}|{kref.get('line')}"
    if t == "total":
        return f"total|{kref.get('n')}" if kref.get("over") else None
    if t == "spread":
        return f"spread|{kref.get('team')}|{kref.get('by')}" + ("|no" if kref.get("no") else "")
    if t == "ml":
        return f"ml|{kref.get('team')}" + ("|no" if kref.get("no") else "")
    return None


def _cached_sims(season, week):
    import boardshare
    for n in SIM_NS:
        got, _age = boardshare.get(f"nfl_parlay_sims5_{season}_w{week}_0_{n}", SIM_MAX_AGE)
        if got:
            return got
    return None


def _model_rungs(store, games, idx, now, season, week, sims=None):
    """Write the model-and-market rungs for every game still ahead of its
    freeze, and the drift over them. Returns how many rungs were written."""
    import combo_engine
    import kalshi_nfl
    if sims is None:
        sims = _cached_sims(season, week)
    if not sims or not idx:
        return 0
    mstore = store.setdefault("model", {})
    n = 0
    for g in sims:
        suf = g.get("suffix")
        e = (idx or {}).get(suf)
        gm = games.get((e or {}).get("pair")) if e else None
        if not gm or gm.get("state") != "pre" or not gm.get("kickoff") or gm["kickoff"] - now < LEAD_S:
            continue                                   # frozen: its "last" is the close
        rec = mstore.setdefault(suf, {"rungs": {}})
        rec.update({"ts": round(now), "kickoff": gm["kickoff"], "sims_n": g.get("n"), "home": gm.get("home")})
        for c in g.get("cands") or []:
            kref = c.get("kref") or {}
            k = _rung_key(kref) if c.get("side", "yes") == "yes" else None
            if not k:
                continue
            q = kalshi_nfl.quote_leg(idx, suf, kref)
            if not kalshi_nfl.believable(q):
                continue
            mid, qual = combo_engine.market_reference(q)
            p = c.get("marg")
            if mid is None or p is None:
                continue
            row = [round(now), round(float(p), 4), round(float(mid), 4)]
            r = rec["rungs"].setdefault(k, {"type": c.get("type"), "first": row})
            r["last"], r["qual"] = row, round(float(qual), 3)
            n += 1
    if n:
        store["drift"] = drift(store, now)
    return n


# THE DRIFT ALARM. The 2025 trims left the sim's totals 2-3 points under the
# real ones for six weeks of 2026 before an audit stumbled on it (2026-10-19,
# research/reports/nfl_trim.md); nothing was watching. Each pass now takes the
# mean signed gap, model minus Kalshi's mid, per leg type over the live
# games' rungs with a believable, liquid quote (quality >= DRIFT_QUAL), and
# ledgers NFL-DRIFT when it is one-sided by DRIFT_GAP or more across at
# least DRIFT_GAMES games and DRIFT_N rungs. 4 points is under the over-side
# gap the totals carried (2.6 to 10.3 points a game, 12 of 14 games) and over
# what one game's prop gaps averaged after the refit (3 to 6 points, one
# game -- the games floor is what keeps one game from ringing it). The
# moneyline is read on the home side only (the two clubs' rungs cancel).
DRIFT_GAP, DRIFT_N, DRIFT_GAMES, DRIFT_QUAL = 0.04, 30, 4, 0.5
# The latest pass's readout, kept small and apart from the week's log so the
# combo maker can read it on every build (nfl_game_sim.drift_types): a type
# the alarm rings on is priced at Kalshi's number until the model is fixed.
DRIFT_KEY = "nfl_props_drift"
DRIFT_FRESH_S = 3 * 3600


def alarmed(v):
    """Does one type's readout ring the alarm (see above)?"""
    return (v.get("n", 0) >= DRIFT_N and v.get("games", 0) >= DRIFT_GAMES
            and abs(v.get("gap", 0.0)) >= DRIFT_GAP)


def drift(store, now):
    """{"ts", "by_type": {type: {"n", "games", "gap"}}} over this pass's live
    rungs, ledgering NFL-DRIFT for each one-sided type (see above)."""
    import kalshi_nfl
    by = {}
    for suf, rec in (store.get("model") or {}).items():
        if rec.get("ts") != round(now):
            continue                                  # frozen games read nothing new
        home = kalshi_nfl._canon(rec.get("home") or "")
        for k, r in (rec.get("rungs") or {}).items():
            if r.get("qual", 0) < DRIFT_QUAL or (r.get("last") or [None])[0] != round(now):
                continue
            t = r.get("type")
            if t == "ML":
                parts = k.split("|")
                if len(parts) != 2 or kalshi_nfl._canon(parts[1]) != home:
                    continue
            b = by.setdefault(t, {"n": 0, "sum": 0.0, "games": set()})
            b["n"] += 1
            b["sum"] += r["last"][1] - r["last"][2]
            b["games"].add(suf)
    out = {"ts": round(now), "by_type": {}}
    for t, b in by.items():
        gap = b["sum"] / b["n"]
        out["by_type"][t] = {"n": b["n"], "games": len(b["games"]), "gap": round(gap, 4)}
        if alarmed(out["by_type"][t]):
            errlog.note("NFL-DRIFT", msg=(f"{t}: the model sits {100 * gap:+.1f} points off Kalshi's mid "
                                          f"over {b['n']} rungs in {len(b['games'])} games"), path=t)
    return out


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
