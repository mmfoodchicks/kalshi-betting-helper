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

Its ~10-minute loop is also the app's recorder CADENCE: the same pass grades
the slip ledger (sliplog.grade_due), rebuilds the locked presets
(presets.tick) and, in season, records and grades the NFL track record
(nfl_track.tick) -- each behind its own error code so one dead feed never
reads as another's fault. Owner worker only.
"""

import datetime
import clock
import threading
import time

import kalshi
import value
import baseball
import store
import errlog

SAMPLE_INTERVAL = 600  # seconds (props drift slowly; no need to hammer the APIs)

_STAT_MARKETS = {"hits": ("hit1", "hit2", "hit3"), "hr": ("hr1",)}


def _season():
    return str(clock.today_et().year)


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
            # The bid rides along so a NO "fill" can be priced at what selling
            # YES actually pays (100 - bid), not at 100 - ask, which pockets
            # the whole spread. None when the book has no resting YES buyer.
            bid = kalshi._cents(m.get("yes_bid_dollars"))
            if bid is not None and bid <= 0:
                bid = None
            out.append({"nm": value._norm(raw_name), "name": raw_name,
                        "stat": stat, "line": line, "yes": yc, "bid": bid})
    return out


def _market_key(stat, line):
    return ("hit" if stat == "hits" else "hr") + str(line)


def record_once():
    """Log every Kalshi batter prop we can attach to a player, with model %,
    price, and recent form. Returns the number of props (re)logged."""
    season, date = _season(), clock.today_et().isoformat()

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
        lv = g.get("live") or {}
        # PRE-GAME ONLY. This used to skip just finals, so a game in progress
        # kept refreshing its props' "closing" price and model every ten
        # minutes -- and a price read in the 7th inning is the box score
        # talking, not a market opinion. Every price-anchored stat downstream
        # (market Brier, edge ROI, CLV) inherited the leak.
        if lv.get("is_final") or lv.get("is_live"):
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
    except Exception as _e:
        errlog.note("MREC-record_once", _e)

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
            kalshi_bid_cents=m.get("bid"),
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
    except Exception as _e:
        errlog.note("MREC-final_pks", _e)
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
            if line_stat is None:
                # Scratched / didn't bat: the books VOID the leg, so grading it
                # as a loss would bias the recorded accuracy pessimistically.
                store.grade_prop_void(p["id"])
                continue
            got = line_stat.get(p["stat"], 0)
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
            # The slip ledger settles on the same cadence: built parlays grade
            # off Kalshi settlement once their games are done.
            import sliplog
            sliplog.grade_due()
            # The locked daily slips ride the same tick: rebuilt when the day
            # rolls or a lineup posts / a starter is scratched, logged into
            # the ledger each time. Cheap when nothing changed (a hash).
            import presets
            presets.tick()
        except Exception as _e:
            errlog.note("MREC-loop", _e)
        # Football rides the same cadence in season; its own failure code
        # so a dead NFL feed can never read as a baseball recorder fault.
        try:
            import nfl_track
            nfl_track.tick()
        except Exception as _e:
            errlog.note("MREC-nfl", _e)
        # The locked UFC slips: rebuilt when the card or the rev changes or
        # the build ages out, logged under their own tags. Own code, same
        # reason as football.
        try:
            import ufc_presets
            ufc_presets.tick()
        except Exception as _e:
            errlog.note("MREC-ufc", _e)
        # College football's track record: same cadence, same rule as the
        # NFL one (reads an existing board, never builds), own code.
        try:
            import cfb_track
            cfb_track.tick()
        except Exception as _e:
            errlog.note("MREC-cfb", _e)
        # The DFS look-back: logged big-event lineups graded off the real
        # DraftKings scoring once their events are over. Own code.
        try:
            import dfslog
            dfslog.tick()
        except Exception as _e:
            errlog.note("MREC-dfslb", _e)
        time.sleep(SAMPLE_INTERVAL)


def start_background():
    store.init_db()
    threading.Thread(target=_loop, daemon=True, name="mlb-prop-recorder").start()
