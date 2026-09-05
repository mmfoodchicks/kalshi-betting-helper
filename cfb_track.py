"""College football model track record -- nfl_track's twin, league "cfb".

Every pre-game pick on the college board (the straight-up pick with a
Kalshi price behind it) is recorded at first sight with its probability and
entry price, its same-side close is refreshed while the game is still
pre-game, and it is graded off ESPN's college scoreboard once the game is
final. Rows live in the same ledger as the NFL picks (the nfl_picks table) under
league "cfb", so the report math is one function.

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
import store
import cfb

_ESPN_COOLOFF_S = 6 * 3600
_GRADE_EVERY_S = 30 * 60
_WEEK_EVERY_S = 6 * 3600
_state = {"espn_block_until": 0.0, "last_grade": 0.0, "week": None, "week_ts": 0.0}
LEAGUE = "cfb"


class _EspnBlocked(Exception):
    pass


def espn_blocked():
    return time.time() < _state["espn_block_until"]


def record_from_board(data):
    """Log every pre-game board game with a Kalshi price for the model's
    pick; refresh same-side closes. Returns how many rows were touched."""
    if not data or data.get("empty"):
        return 0
    week = data.get("week")
    n = 0
    for g in data.get("games") or []:
        state = (g.get("state") or "").lower()
        if state and state != "pre":
            continue
        ph = g.get("p_home")
        kx = g.get("kalshi") or {}
        if ph is None:
            continue
        pick_home = ph >= 0.5
        price = kx.get("home_cents" if pick_home else "away_cents")
        if price is None:
            continue
        date = (g.get("date") or "")[:10]
        home, away = g.get("home"), g.get("away")
        if not (date and home and away):
            continue
        gid = f"{date}_{away}@{home}"
        side = "home" if pick_home else "away"
        raw = g.get("p_home_raw")
        store.record_nfl_pick(
            gid, date, week, False, side, home if pick_home else away,
            ph if pick_home else 1 - ph, price,
            pred_total=g.get("exp_total"),
            prob_raw=(raw if pick_home else 1 - raw) if raw is not None else None,
            league=LEAGUE)
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
        if (((comp.get("status") or {}).get("type")) or {}).get("state") != "post":
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


def grade_due():
    """Grade recorded picks whose games are now final, dates that have
    arrived only, one scoreboard fetch per date, ESPN refusal ends the pass."""
    if espn_blocked():
        return 0
    import clock
    today = clock.today_et().isoformat()
    picks = [p for p in store.ungraded_nfl_picks(league=LEAGUE)
             if (p.get("date") or "") <= today]
    if not picks:
        return 0
    by_date = {}
    for p in picks:
        by_date.setdefault(p["date"], []).append(p)
    n, memo = 0, {}
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
