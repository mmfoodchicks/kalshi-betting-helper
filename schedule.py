"""Unified 'what starts when' board — every event we cover, on one screen, with
its start time.

Each sport keeps its own feed and quirks, so this is a thin aggregator: pull the
day's events from each model's existing data path, normalize to a common row
{sport, icon, matchup, epoch, status, where}, and let the caller sort by time.
Every pull is best-effort and independently degradable; `sources` reports what
loaded so the UI can be honest about coverage (an empty NFL in July is not a
bug — there are no games).

Times are returned as UTC epoch seconds plus a preformatted ET string; the
frontend also renders the viewer's local time from the epoch.
"""

import time as _t
import datetime as _dt

import clock


def _epoch(iso):
    """Parse an ISO timestamp (with Z, an offset, or bare) -> UTC epoch seconds.
    Returns None if there's no usable time (date-only or unparseable)."""
    if not iso or not isinstance(iso, str):
        return None
    s = iso.strip()
    # Date-only ("2026-07-19") carries no start time.
    if len(s) <= 10:
        return None
    s = s.replace("Z", "+00:00")
    try:
        d = _dt.datetime.fromisoformat(s)
    except ValueError:
        # Ergast style "2026-07-19" + "13:00:00Z" is handled by the callers;
        # anything else we can't parse we drop rather than guess.
        try:
            d = _dt.datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=_dt.timezone.utc)
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return d.timestamp()


def _et(epoch):
    """Epoch seconds -> '7:15 PM ET' in America/New_York (no leading zero)."""
    if epoch is None:
        return None
    try:
        from zoneinfo import ZoneInfo
        d = _dt.datetime.fromtimestamp(epoch, ZoneInfo("America/New_York"))
    except Exception:
        d = _dt.datetime.fromtimestamp(epoch)
    h = d.hour % 12 or 12
    return f"{h}:{d.minute:02d} {'AM' if d.hour < 12 else 'PM'} ET"


def _day_label(epoch, board_date):
    """Short 'Sat Jul 19' label if the event's ET date isn't the board date, else
    None (today needs no label)."""
    if epoch is None:
        return None
    try:
        from zoneinfo import ZoneInfo
        d = _dt.datetime.fromtimestamp(epoch, ZoneInfo("America/New_York"))
    except Exception:
        d = _dt.datetime.fromtimestamp(epoch)
    if d.date().isoformat() == board_date:
        return None
    return d.strftime("%a %b %-d")


def _row(sport, icon, matchup, epoch, status="scheduled", where=None, note=None):
    return {"sport": sport, "icon": icon, "matchup": matchup,
            "epoch": epoch, "et": _et(epoch), "status": status,
            "where": where, "note": note}


def _mlb(date, season):
    import baseball
    games = baseball.analyze_slate(date, season)
    rows = []
    for g in games:
        ep = _epoch(g.get("start"))
        state = (g.get("live") or {}).get("state")
        status = ("live" if state == "Live" else
                  "final" if state == "Final" else "scheduled")
        rows.append(_row("MLB", "⚾", g.get("matchup"), ep, status,
                         where="Kalshi · MLB"))
    return rows


def _tennis(date):
    import tennis_live
    rows = []
    for m in tennis_live.snapshot():
        # Only rows for the requested day (the snapshot is today's board).
        ep = _epoch(m.get("start"))
        st = m.get("state")
        status = ("live" if st == "in" else "final" if st == "post" else "scheduled")
        tour = m.get("tournament") or "Tennis"
        where = (f"Kalshi · {m.get('city')}" if m.get("city") else "Kalshi · Tennis")
        rows.append(_row("Tennis", "🎾", f"{m.get('a')} vs {m.get('b')}", ep, status,
                         where=where, note=tour if tour else None))
    return rows


def _f1(date):
    import racing
    import racing_sim
    d = racing._get_json(f"{racing_sim.ERGAST}.json")
    today = clock.today_et().isoformat()
    rows = []
    for r in d["MRData"]["RaceTable"]["Races"]:
        rd = r.get("date", "")
        if rd < today:
            continue
        # Ergast gives date + optional UTC time; combine to an ISO instant.
        iso = f"{rd}T{r['time']}" if r.get("time") else None
        ep = _epoch(iso)
        rows.append(_row("F1", "🏎️", f"{r.get('raceName')} — {((r.get('Circuit') or {}).get('circuitName') or '')}",
                         ep, "scheduled", where="Kalshi · F1",
                         note=None if ep else "start time TBA"))
        break   # just the next race
    return rows


def _nascar(date):
    import racing
    year = clock.today_et().year
    rows = []
    for series, label in ((1, "Cup"), (2, "Xfinity")):
        try:
            races = racing._get_json(
                f"https://cf.nascar.com/cacher/{year}/{series}/race_list_basic.json")
        except Exception:
            continue
        today = clock.today_et().isoformat()
        upcoming = sorted((r for r in (races or [])
                           if (r.get("race_date") or "")[:10] >= today),
                          key=lambda r: r.get("race_date") or "")
        if not upcoming:
            continue
        r = upcoming[0]
        # NASCAR race_date is local ("2026-07-20T15:00:00") — treat as ET.
        ep = _epoch_local_et(r.get("race_date"))
        rows.append(_row(f"NASCAR {label}", "🏁",
                         f"{r.get('race_name')} — {r.get('track_name') or ''}".strip(" —"),
                         ep, "scheduled", where=f"Kalshi · NASCAR {label}",
                         note=None if ep else "start time TBA"))
    return rows


def _epoch_local_et(iso):
    """Parse a wall-clock ISO with no zone as America/New_York -> UTC epoch."""
    if not iso or len(iso) <= 10:
        return None
    try:
        from zoneinfo import ZoneInfo
        d = _dt.datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")
        return d.replace(tzinfo=ZoneInfo("America/New_York")).timestamp()
    except Exception:
        return None


def _ufc(date):
    import ufc_sim
    board = ufc_sim.board()
    if not board:
        return []
    ep = _epoch(board.get("date"))
    ev = board.get("event") or "UFC"
    n = len(board.get("bouts") or [])
    return [_row("UFC", "🥊", ev, ep, "scheduled", where="Kalshi · UFC",
                 note=f"{n} bouts" if n else None)]


def _nfl(date, season):
    """In-season only: find the games on `date` across the nearby weeks."""
    import nfl_live
    rows = []
    for wk in range(1, 24):                       # regular season + playoffs
        try:
            sched = nfl_live.schedule(wk, season)
        except Exception:
            sched = None
        if not sched:
            continue
        for g in sched:
            if (g.get("date") or "")[:10] != date:
                continue
            st = g.get("state")
            status = ("live" if st == "in" else "final" if st == "post" else "scheduled")
            rows.append(_row("NFL", "🏈",
                             f"{g.get('away_name') or g.get('away')} @ {g.get('home_name') or g.get('home')}",
                             _epoch(g.get("date")), status, where="Kalshi · NFL"))
        if rows:
            break                                 # the date lives in one week
    return rows


_memo = {"key": None, "at": 0.0, "out": None}
_MEMO_TTL = 180   # start times don't move minute to minute; re-opens reuse the pull


def board(date=None):
    date = date or clock.today_et().isoformat()
    if _memo["out"] is not None and _memo["key"] == date and _t.time() - _memo["at"] < _MEMO_TTL:
        return _memo["out"]
    season = date[:4]
    rows, sources = [], {}

    def pull(name, fn, *a):
        try:
            got = fn(*a)
            rows.extend(got)
            sources[name] = {"ok": True, "rows": len(got)}
        except Exception as e:
            sources[name] = {"ok": False, "error": str(e)[:80]}

    pull("mlb", _mlb, date, season)
    pull("tennis", _tennis, date)
    pull("f1", _f1, date)
    pull("nascar", _nascar, date)
    pull("ufc", _ufc, date)
    pull("nfl", _nfl, date, season)

    # Forward-looking board: drop anything already final (it's not "supposed to
    # start" — it's over). Live games stay, flagged so the UI can mark them.
    rows = [r for r in rows if r["status"] != "final"]
    # Racing/UFC surface the NEXT event, which can be days out. Tag any row whose
    # ET date isn't today with a short day label so the board stays honest about
    # what's actually today vs. "coming up".
    for r in rows:
        r["day"] = _day_label(r["epoch"], date)
    # Group by sport, keep each group time-sorted (untimed rows sink to the end),
    # then order the groups by their earliest event so the soonest sport leads.
    groups = {}
    for r in rows:
        groups.setdefault((r["icon"], r["sport"]), []).append(r)
    out = []
    for (icon, sport), evs in groups.items():
        evs.sort(key=lambda r: (r["epoch"] is None, r["epoch"] or 0))
        first = next((e["epoch"] for e in evs if e["epoch"] is not None), None)
        out.append({"sport": sport, "icon": icon, "events": evs,
                    "first_epoch": first, "count": len(evs)})
    out.sort(key=lambda g: (g["first_epoch"] is None, g["first_epoch"] or 0))
    result = {"date": date, "tz": "America/New_York",
              "groups": out, "sources": sources,
              "n_events": len(rows), "generated_at": _t.time()}
    _memo.update(key=date, at=_t.time(), out=result)
    return result
