"""Background MLB player-prop recorder + grader.

Like the crypto quote recorder (recorder.py), but for batter props. While the
app is open it periodically logs, for every Kalshi-listed batter prop (1+/2+/3+
hits, 1+ HR) it can match to a player:
  - the model's matchup-adjusted probability (from baseball.analyze_slate)
  - Kalshi's live YES price (the implied market probability)
  - the batter's recent-form and season hit/HR rate (value.recent_form)

When games go final it grades each logged prop against the real box score. Over
many games this accumulates the dataset that validates the prop model and the
value finder IN AGGREGATE -- Brier, calibration, edge ROI. A single night is
noise; the point is the long-run honest read (store.prop_report).
"""

import datetime
import threading
import time

import kalshi
import value
import baseball
import store

SAMPLE_INTERVAL = 600  # seconds (props drift slowly; no need to hammer the APIs)

_STAT_MARKETS = {"hits": ("hit1", "hit2", "hit3"), "hr": ("hr1",)}


def _season():
    return str(datetime.date.today().year)


def _kalshi_prop_prices():
    """[{nm, name, stat, line, yes}] for the open Kalshi batter-prop markets."""
    out = []
    for series, stat in (("KXMLBHIT", "hits"), ("KXMLBHR", "hr")):
        for m in value._markets(series):
            mt = value._TITLE.match(m.get("title", "") or "")
            if not mt:
                continue
            raw_name, line = mt.group(1).strip(), int(mt.group(2))
            yc = kalshi._cents(m.get("yes_ask_dollars"))
            if yc is None or yc <= 1 or yc >= 99:    # no real two-sided market yet
                continue
            out.append({"nm": value._norm(raw_name), "name": raw_name,
                        "stat": stat, "line": line, "yes": yc})
    return out


def _market_key(stat, line):
    return ("hit" if stat == "hits" else "hr") + str(line)


def record_once():
    """Log every Kalshi batter prop we can attach to a player, with model %,
    price, and recent form. Returns the number of props (re)logged."""
    season, date = _season(), datetime.date.today().isoformat()

    prices = _kalshi_prop_prices()
    if not prices:
        return 0

    # Player ids + game_pk from the posted lineups (skip games already final).
    try:
        schedule = baseball._schedule(date, season)
    except Exception:
        return 0
    pid_by_name, pk_by_name = {}, {}
    for g in schedule:
        if (g.get("live") or {}).get("is_final"):
            continue
        lu = baseball._boxscore_lineup(g["game_pk"]) or {}
        for side in ("home", "away"):
            for b in lu.get(side) or []:
                nm = value._norm(b.get("name", ""))
                if nm and b.get("id"):
                    pid_by_name[nm] = b["id"]
                    pk_by_name[nm] = g["game_pk"]

    # Matchup-adjusted model probabilities from the full slate analysis.
    model = {}   # norm_name -> {(stat, line): pct}
    try:
        for g in baseball.analyze_slate(date, season):
            p = g.get("props") or {}
            for key in ("batters_home", "batters_away"):
                for b in p.get(key) or []:
                    nm = value._norm(b.get("name", ""))
                    if not nm:
                        continue
                    d = model.setdefault(nm, {})
                    for mk, sl in (("hit1", ("hits", 1)), ("hit2", ("hits", 2)),
                                   ("hit3", ("hits", 3)), ("hr1", ("hr", 1))):
                        if b.get(mk) is not None:
                            d[sl] = b[mk]
    except Exception:
        pass

    forms, n = {}, 0
    for m in prices:
        pid = pid_by_name.get(m["nm"])
        if not pid:
            continue                       # not in a posted lineup -> can't grade
        pk = pk_by_name.get(m["nm"])
        stat, line = m["stat"], m["line"]
        mpct = model.get(m["nm"], {}).get((stat, line))
        if pid not in forms:
            forms[pid] = value.recent_form(pid, season) or {}
        f = forms[pid]
        rate = (f.get("recent") or {}).get((stat, line))
        srate = (f.get("season") or {}).get((stat, line))
        store.log_prop(
            pk, date, pid, m["name"], stat, line, _market_key(stat, line),
            mpct, m["yes"],
            round(rate * 100, 1) if rate is not None else None,
            round(srate * 100, 1) if srate is not None else None,
        )
        n += 1
    return n


def _final_pks(date):
    """Set of game_pks that are Final on `date` (checking +/-1 day for timezone
    spillover and doubleheaders, like baseball.grade_picks does)."""
    pks = set(baseball._final_winners(date).keys())
    try:
        d0 = datetime.date.fromisoformat(date)
        for off in (1, -1):
            pks |= set(baseball._final_winners((d0 + datetime.timedelta(days=off)).isoformat()).keys())
    except Exception:
        pass
    return pks


def _box_actuals(game_pk):
    """{player_id: {'hits': int, 'hr': int}} for players who actually batted."""
    try:
        d = kalshi._get_json(f"{baseball.STATS_BASE}/game/{game_pk}/boxscore")
    except Exception:
        return {}
    out = {}
    for side in ("home", "away"):
        t = d.get("teams", {}).get(side, {})
        for pl in (t.get("players") or {}).values():
            pid = pl.get("person", {}).get("id")
            bat = pl.get("stats", {}).get("batting", {})
            if pid and bat:                 # a batting stat block => they played
                out[pid] = {"hits": int(bat.get("hits", 0) or 0),
                            "hr": int(bat.get("homeRuns", 0) or 0)}
    return out


def grade_due():
    """Grade any logged props whose game is now final, from the real box score."""
    props = store.ungraded_props()
    if not props:
        return
    by_date = {}
    for p in props:
        by_date.setdefault(p["date"], []).append(p)
    for date, ps in by_date.items():
        finals = _final_pks(date)
        actuals = {}                        # game_pk -> box actuals (fetched once)
        for p in ps:
            pk = p["game_pk"]
            if pk not in finals:
                continue
            if pk not in actuals:
                actuals[pk] = _box_actuals(pk)
            ga = actuals[pk]
            if not ga:                       # box not available yet -> leave pending
                continue
            line_stat = ga.get(p["player_id"])
            # Played -> real total; in the final box but not batting -> 0 (DNP).
            got = line_stat.get(p["stat"], 0) if line_stat else 0
            store.grade_prop(p["id"], 1 if got >= p["line"] else 0)


def status():
    with store._lock, store._conn() as c:
        s = c.execute(
            "SELECT COUNT(*) n, SUM(graded) g, MIN(ts) lo, MAX(ts) hi FROM prop_log"
        ).fetchone()
    return {"logged": s["n"] or 0, "graded": s["g"] or 0,
            "first_ts": s["lo"], "last_ts": s["hi"]}


def _loop():
    while True:
        try:
            record_once()
            grade_due()
        except Exception:
            pass
        time.sleep(SAMPLE_INTERVAL)


def start_background():
    store.init_db()
    threading.Thread(target=_loop, daemon=True, name="mlb-prop-recorder").start()
