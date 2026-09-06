"""College football model track record -- nfl_track's twin, league "cfb".

Every pre-game pick on the college board (the straight-up pick with a
Kalshi price behind it) is recorded at first sight with its probability and
entry price, its same-side close is refreshed while the game is still
pre-game, and it is graded off ESPN's college scoreboard once the game is
final. Rows live in the same ledger as the NFL picks (the nfl_picks table) under
league "cfb", so the report math is one function. Both college divisions are
rated (cfb.teams(season, "all")), so an FCS game and an FBS-vs-FCS buy game
are recorded too -- each row carries the division it was played in and the
record keeps them apart.

The ATS pick (at the rung Kalshi books nearest its line) and the total lean
(at Kalshi's total) are two more books of the same ledger, leagues "cfb_ats"
and "cfb_tot", filed under the Kalshi TICKET they are (game_id = ticker,
pick_side yes/no) and graded off Kalshi's own settlement through predlog,
which logs the same tickers under models cfb_ats / cfb_total -- no
scoreboard read, no push rule to get wrong (every rung is a half-point).
The straight-up pick carries its own moneyline ticket too and grades the
same way, so the record fills while ESPN's WAF has the scoreboard parked
(every pass of 2026-09-06); the scoreboard pass still grades rows the
settlement has not reached and is the only source of the actual total.

Runs on the recorder's cadence (mlb_recorder), August through January, off a
board that already exists (the PC's upload or a tab's build) -- it never
kicks a build. ESPN's WAF parks this module for six hours on a refusal,
exactly as it does nfl_track, because the two share one host and one
scoreboard rate limit.
"""

import datetime
import time

import errlog
import kalshi
import predlog
import store
import cfb

_ESPN_COOLOFF_S = 6 * 3600
_GRADE_EVERY_S = 30 * 60
_WEEK_EVERY_S = 6 * 3600
_state = {"espn_block_until": 0.0, "last_grade": 0.0, "week": None, "week_ts": 0.0}
LEAGUE = "cfb"
# (card field, ledger league): the two line picks, filed as tickets
_LINE_BOOKS = (("ats", "cfb_ats"), ("total", "cfb_tot"))


class _EspnBlocked(Exception):
    pass


def espn_blocked():
    return time.time() < _state["espn_block_until"]


def _line_name(key, pk):
    """The pick as the owner reads it: 'IU -17.5', 'Over 51.5'."""
    if key == "ats":
        return f"{pk.get('team')} {pk.get('spread')}"
    return f"{'Over' if pk.get('lean') == 'over' else 'Under'} {pk.get('line')}"


def record_from_board(data):
    """Log every pre-game board game with a Kalshi price for the model's
    pick, and its ATS and total picks as the tickets they are; refresh
    same-side closes. Returns how many rows were touched."""
    if not data or data.get("empty"):
        return 0
    week = data.get("week")
    n = 0
    for g in data.get("games") or []:
        state = (g.get("state") or "").lower()
        date = (g.get("date") or "")[:10]
        home, away = g.get("home"), g.get("away")
        if not (date and home and away):
            continue
        kxc = g.get("kx") or {}
        if state and state != "pre":
            # Already under way or final: nothing new to record, but a
            # straight-up row filed before the ticket column existed (week
            # 1 of 2026) gets its ticket so the settlement can grade it.
            if kxc.get("suffix") and kxc.get("home") and kxc.get("away"):
                store.backfill_nfl_ticker(
                    f"{date}_{away}@{home}", LEAGUE,
                    f"KXNCAAFGAME-{kxc['suffix']}-{kxc['home']}",
                    f"KXNCAAFGAME-{kxc['suffix']}-{kxc['away']}")
            continue
        gdiv = g.get("division") or "fbs"
        for key, league in _LINE_BOOKS:
            pk = g.get(key) or {}
            tk, side, ask = pk.get("ticker"), pk.get("side"), pk.get("ask")
            if not (tk and side in ("yes", "no") and ask and pk.get("pct") is not None):
                continue
            store.record_nfl_pick(
                tk, date, week, False, side, _line_name(key, pk),
                pk["pct"] / 100.0, ask,
                pred_total=g.get("exp_total") if key == "total" else None,
                league=league, div=gdiv)
            store.update_nfl_close(tk, ask, side, league=league)
            n += 1
        ph = g.get("p_home")
        kx = g.get("kalshi") or {}
        if ph is None:
            continue
        pick_home = ph >= 0.5
        price = kx.get("home_cents" if pick_home else "away_cents")
        if price is None:
            continue
        gid = f"{date}_{away}@{home}"
        side = "home" if pick_home else "away"
        raw = g.get("p_home_raw")
        # the pick's own market, for the settlement grader
        ticker = (f"KXNCAAFGAME-{kxc['suffix']}-{kxc[side]}"
                  if kxc.get("suffix") and kxc.get(side) else None)
        store.record_nfl_pick(
            gid, date, week, False, side, home if pick_home else away,
            ph if pick_home else 1 - ph, price,
            pred_total=g.get("exp_total"),
            prob_raw=(raw if pick_home else 1 - raw) if raw is not None else None,
            league=LEAGUE, ticker=ticker, div=gdiv)
        store.update_nfl_close(gid, price, side, league=LEAGUE)
        n += 1
    return n


def _finals_for(date, memo=None):
    """{(home_abbr, away_abbr): (home_score, away_score)} for games FINAL on
    `date` (YYYY-MM-DD) from ESPN's college scoreboard, every FBS game (the
    board's own abbreviations, so no canon step). A 403/429 parks ESPN."""
    import urllib.error
    if memo is not None and date in memo:
        return memo[date]
    try:
        d = kalshi._get_json(f"{cfb._SITE}/scoreboard?dates={date.replace('-', '')}"
                             "&groups=80&limit=400")
    except urllib.error.HTTPError as e:
        if getattr(e, "code", None) in (403, 429):
            _state["espn_block_until"] = time.time() + _ESPN_COOLOFF_S
            raise _EspnBlocked(f"ESPN {e.code}: parked {_ESPN_COOLOFF_S // 3600}h")
        raise
    out = {}
    for ev in d.get("events") or []:
        comp = (ev.get("competitions") or [{}])[0]
        stype = ((comp.get("status") or {}).get("type")) or {}
        # `completed`, not the state: a postponed or canceled game also reports
        # "post", with 0-0 on the board, and would grade the pick as a loss
        # against a game nobody played (see cfb.schedule).
        if stype.get("state") != "post" or not stype.get("completed"):
            continue
        home = away = None
        hs = as_ = None
        for c in comp.get("competitors") or []:
            ab = (c.get("team") or {}).get("abbreviation")
            try:
                sc = float(c.get("score"))
            except (TypeError, ValueError):
                sc = None
            if c.get("homeAway") == "home":
                home, hs = ab, sc
            else:
                away, as_ = ab, sc
        if home and away and hs is not None and as_ is not None:
            out[(home, away)] = (hs, as_)
    if memo is not None:
        memo[date] = out
    return out


def _grade_rows(picks, finals):
    """[(game_id, won, winner_name, actual_total, home_won)] for every pick
    whose game appears in `finals`. College games cannot tie, but the shape
    keeps nfl_track's rule (a tie is a loss for either side) anyway."""
    out = []
    for p in picks:
        gid = p["game_id"]
        try:
            _date, matchup = gid.split("_", 1)
            away, home = matchup.split("@", 1)
        except ValueError:
            continue
        res = finals.get((home, away))
        if res is None:
            continue
        hs, as_ = res
        total = hs + as_
        if hs == as_:
            out.append((gid, 0, "TIE", total, None))
            continue
        home_won = 1 if hs > as_ else 0
        won = home_won if p.get("pick_side") == "home" else 1 - home_won
        out.append((gid, won, home if home_won else away, total, home_won))
    return out


def _su_grade(r, yes):
    """A straight-up pick off its own moneyline's settlement: YES means the
    pick's team won. (won, winner_name, home_won); the actual total is not
    in a settlement and stays for the scoreboard pass to fill."""
    try:
        _date, matchup = r["game_id"].split("_", 1)
        away, home = matchup.split("@", 1)
    except ValueError:
        away = home = None
    won = 1 if yes else 0
    picked_home = r.get("pick_side") == "home"
    home_won = 1 if picked_home == bool(won) else 0
    winner = (home if home_won else away) or (r.get("pick_name") if won else "other")
    return won, winner, home_won


def grade_lines():
    """Grade picks off Kalshi's settlement of the ticket each one is
    (predlog resolves the ticker; the pick row reads its outcome): the ATS
    and total picks always, and the straight-up picks through their own
    moneyline -- ESPN's WAF parked the scoreboard on every pass of
    2026-09-06, and a record that waits on it never fills. A void or
    delisted market voids the pick."""
    n = 0
    for league in (LEAGUE,) + tuple(lg for _k, lg in _LINE_BOOKS):
        rows = [r for r in store.ungraded_nfl_picks(league=league)
                if r.get("ticker") or league != LEAGUE]
        if not rows:
            continue
        key = "ticker" if league == LEAGUE else "game_id"
        res = predlog.results([r[key] for r in rows])
        for r in rows:
            got = res.get(r[key])
            if not got:
                continue
            if got["graded"] == 2:
                store.void_nfl_pick(r["game_id"])
                n += 1
            elif got["graded"] == 1 and got["outcome"] is not None:
                yes = int(got["outcome"]) == 1
                if league == LEAGUE:
                    won, winner, home_won = _su_grade(r, yes)
                    store.set_nfl_grade(r["game_id"], won, winner, home_won=home_won)
                else:
                    won = 1 if yes == (r["pick_side"] == "yes") else 0
                    store.set_nfl_grade(r["game_id"], won, "yes" if yes else "no")
                n += 1
    return n


DIVISIONS = ("fbs", "fcs", "cross")


def record(div=None):
    """The College tab's scoreboard: the straight-up picks (ESPN-graded, the
    football ledger's math), the ATS and total picks at Kalshi's booked line
    (Kalshi-settled), the totals' lean, and the moneyline model against the
    price it was logged beside.

    `div` narrows every book to one division; the default pools them for the
    headline and carries a per-division breakdown under "divisions", because
    FBS and FCS are not one population -- an FCS rating is a heavier regressed
    prior for longer, and easy FCS favourites would otherwise flatter the FBS
    record."""
    su = store.nfl_record(league=LEAGUE, div=div)["regular"]
    ats = store.nfl_record(league="cfb_ats", div=div)["regular"]
    tot = store.nfl_record(league="cfb_tot", div=div)["regular"]
    for r in (ats, tot):
        r["clv_note"] = "pre-game closes only"
    rows = [r for r in store.nfl_pick_rows("cfb_tot", div=div) if r.get("graded") != 2]
    graded = [r for r in rows if r.get("graded") == 1]
    overs = [r for r in rows if r.get("pick_side") == "yes"]
    # the Over's own hit rate, whichever way we leaned: the pick's YES side
    # is the Over, so an Over pick that won or an Under pick that lost
    over_hit = [r for r in graded
                if (r.get("pick_side") == "yes") == (r.get("won") == 1)]

    def _line(r):
        try:
            return float((r.get("pick_name") or "").rsplit(" ", 1)[-1])
        except ValueError:
            return None
    diffs = [r["pred_total"] - _line(r) for r in rows
             if r.get("pred_total") is not None and _line(r) is not None]
    tot["lean"] = {
        "n": len(rows), "over_picks": len(overs), "under_picks": len(rows) - len(overs),
        "over_pick_pct": round(100.0 * len(overs) / len(rows), 1) if rows else None,
        "overs_hit_pct": round(100.0 * len(over_hit) / len(graded), 1) if graded else None,
        "graded": len(graded),
        # model total minus Kalshi's line, averaged: the lean in points
        "vs_line_avg": round(sum(diffs) / len(diffs), 2) if diffs else None}
    market = {}
    try:
        market = {"vs_market": predlog.vs_market(LEAGUE),
                  "close": predlog.close_report(LEAGUE)}
    except Exception as _e:
        errlog.note("CFBT-market", _e)
    out = {"regular": su, "ats": ats, "totals": tot, "market": market, "div": div}
    if div in ("fbs", "fcs"):
        # A buy game shows on BOTH divisions' tabs -- it is an FBS team's game
        # and an FCS team's game -- but it is graded in neither book. The FBS
        # side wins 86% of them (2025: 146 finals, mean margin 26.8), so
        # folding them into the FBS record would flatter it with games that
        # were never in doubt, and into the FCS record would bury it. Its own
        # line, shown on both tabs.
        out["cross"] = {
            "regular": store.nfl_record(league=LEAGUE, div="cross")["regular"],
            "ats": store.nfl_record(league="cfb_ats", div="cross")["regular"],
            "totals": store.nfl_record(league="cfb_tot", div="cross")["regular"]}
    if div is None:
        # one row per division, and only the ones that carry picks -- a book
        # with nothing in it is noise on the card
        by = {}
        for d in DIVISIONS:
            r = store.nfl_record(league=LEAGUE, div=d)["regular"]
            a = store.nfl_record(league="cfb_ats", div=d)["regular"]
            t = store.nfl_record(league="cfb_tot", div=d)["regular"]
            if any((x.get("graded") or 0) + (x.get("pending") or 0) for x in (r, a, t)):
                by[d] = {"regular": r, "ats": a, "totals": t}
        out["divisions"] = by
    return out


def grade_due():
    """Grade recorded picks whose games are now final: the line picks off
    their settlements first (no ESPN in that), then the straight-up picks
    off the scoreboard, dates that have arrived only, one fetch per date,
    ESPN refusal ends the pass."""
    n = 0
    try:
        n = grade_lines()
    except Exception as _e:
        errlog.note("CFBT-lines", _e)
    if espn_blocked():
        return n
    import clock
    today = clock.today_et().isoformat()
    picks = [p for p in store.ungraded_nfl_picks(league=LEAGUE)
             if (p.get("date") or "") <= today]
    if not picks:
        return n
    by_date = {}
    for p in picks:
        by_date.setdefault(p["date"], []).append(p)
    memo = {}
    for date, ps in sorted(by_date.items()):
        finals = {}
        try:
            d0 = datetime.date.fromisoformat(date)
            for off in (0, 1, -1):
                day = (d0 + datetime.timedelta(days=off)).isoformat()
                if day <= today:
                    finals.update(_finals_for(day, memo))
        except _EspnBlocked as _e:
            errlog.note("CFBT-espn-blocked", _e)
            return n
        except Exception as _e:
            errlog.note("CFBT-finals", _e)
            continue
        for gid, won, winner, total, home_won in _grade_rows(ps, finals):
            store.set_nfl_grade(gid, won, winner, actual_total=total, home_won=home_won)
            n += 1
    return n


def tick():
    """Recorder-cadence pass, August through January: record the current
    week's pre-game picks off the SHARED board and grade finals. Reads only a
    board that already exists; the week is looked up at most every 6h."""
    import boardshare
    import clock
    import cfb_board
    d = clock.today_et()
    if not (d.month >= 8 or d.month <= 1) or espn_blocked():
        return 0
    now = time.time()
    if _state["week"] is None or now - _state["week_ts"] > _WEEK_EVERY_S:
        _state["week"] = cfb_board.current_week()
        _state["week_ts"] = now
    data, _age = boardshare.get(f"cfb_slate_{cfb_board._season()}_w{_state['week']}",
                                3 * 3600)
    n = 0
    if data and not data.get("empty"):
        n = record_from_board(data)
    if now - _state["last_grade"] >= _GRADE_EVERY_S:
        _state["last_grade"] = now
        grade_due()
    return n
