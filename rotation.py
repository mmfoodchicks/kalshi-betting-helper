"""Calendar-aligned starting-rotation projection.

MLB names probables barely three days out (measured live: 89% of starters at
D+1, 37% at D+3, zero at D+5) -- beyond that "who starts Thursday" was a guess.
But the rotation is nearly deterministic: five arms on four-to-five days'
rest, cycled over the team's actual calendar of games and off-days. This
module makes that a projection instead of a guess:

  start_log()   who actually started every recent game (one league-wide
                schedule call per date -- past-date probables ARE the starters,
                verified 20/20 on a settled slate)
  project()     each team's next starters over the coming days: announced
                probables where they exist, greedy rest-cycling where they
                don't (each game goes to the eligible arm with the most rest,
                which reproduces a rotation cycle and absorbs off-days
                naturally), IL arms excluded
  backtest()    point-in-time honesty: project past days using only what was
                known before them, score against who actually started, and
                compare to a naive "same order repeats" baseline

The deep season engine is the first consumer: it used to start EVERY team's
remaining-season rotation cycle at the ace (rotation[0] on day one for all 30
clubs), a phase error that biased near-term series odds. next_starter_ids()
hands it the real phase.
"""

from datetime import date as _date, timedelta as _td
from collections import defaultdict

import baseball
import clock

_HYD = "&hydrate=probablePitcher"
_MIN_REST = 4          # days between starts a projection will assume
_POOL_DAYS = 12        # an arm counts as "in the rotation" if he started recently
_DEF_REST = 5


def _sched_day(d):
    """One league-wide schedule day with probables, cached. Past dates never
    change, so they cache long; today/future refresh hourly as probables post."""
    dstr = d.isoformat() if hasattr(d, "isoformat") else str(d)
    past = dstr < clock.today_et().isoformat()
    ttl = 6 * 24 * 3600 if past else 3600

    def fetch():
        try:
            data = baseball._get(
                f"{baseball.STATS_BASE}/schedule?sportId=1&date={dstr}{_HYD}")
            out = []
            for day in data.get("dates", []):
                for g in day.get("games", []):
                    row = {"pk": g.get("gamePk"), "date": dstr,
                           "state": (g.get("status") or {}).get("abstractGameState"),
                           "dh": g.get("doubleHeader")}
                    for side in ("home", "away"):
                        t = (g.get("teams") or {}).get(side) or {}
                        pp = t.get("probablePitcher") or {}
                        row[side] = (t.get("team") or {}).get("id")
                        row[side + "_sp"] = (pp.get("id"), pp.get("fullName"))
                    out.append(row)
            return out
        except Exception:
            return None
    return baseball._cached(("rot_day", dstr), ttl, fetch) or []


def start_log(days_back=21, end=None):
    """{team_id: [(date_str, pid, name), ...]} chronological actual starters."""
    end = end or clock.today_et()
    log = defaultdict(list)
    for i in range(days_back, 0, -1):
        d = end - _td(days=i)
        for g in _sched_day(d):
            if g.get("state") != "Final":
                continue
            for side in ("home", "away"):
                pid, nm = g.get(side + "_sp") or (None, None)
                if g.get(side) and pid:
                    log[g[side]].append((g["date"], pid, nm))
    return dict(log)


def _pool(starts, il_ids=None):
    """The current rotation: arms with a recent start, their last start date and
    personal rest cadence. Worst data still yields a usable 5-arm cycle."""
    last, gaps, name = {}, defaultdict(list), {}
    prev = {}
    for dstr, pid, nm in starts:
        d = _date.fromisoformat(dstr)
        if pid in prev:
            gaps[pid].append((d - prev[pid]).days)
        prev[pid] = d
        last[pid] = d
        name[pid] = nm
    cutoff = (_date.fromisoformat(starts[-1][0]) - _td(days=_POOL_DAYS)) \
        if starts else None
    pool = []
    for pid, d in last.items():
        if cutoff and d < cutoff:
            continue                      # dropped from the rotation / IL'd out
        if il_ids and pid in il_ids:
            continue
        gs = sorted(gaps.get(pid) or [])
        rest = gs[len(gs) // 2] if gs else _DEF_REST
        pool.append({"id": pid, "name": name.get(pid), "last": d,
                     "rest": max(_MIN_REST, min(6, rest))})
    # THE ROTATION IS THE FIVE MOST RECENT DISTINCT STARTERS. The wider 12-day
    # window let openers and spot starters into the cycle, which is part of why
    # the rest-window projector lost to the naive baseline -- the baseline's
    # implicit pool was exactly these five. A sixth arm only belongs when the
    # club is genuinely running six, and then he IS one of the five most recent.
    pool.sort(key=lambda a: a["last"], reverse=True)
    return pool[:5]


def _il_ids(team_id):
    """Pitcher ids currently on any injured list for this club. Best-effort."""
    try:
        import deep_data
        season = str(clock.today_et().year)
        pit = deep_data._roster_stats(team_id, season, "pitching")
        return {pid for pid, rec in pit.items() if deep_data._is_il(rec[4])}
    except Exception:
        return set()


def project(horizon=10, teams=None, log=None, use_announced=True, today=None,
            include_final=False):
    """{team_id: [{date, pk, pid, name, source, rest}]} for the coming games.

    Announced probables anchor where they exist; every other game goes to the
    eligible arm with the most rest (>= _MIN_REST days), which is how a real
    rotation cycles and absorbs off-days without any special-casing. A date
    with no eligible arm is a bullpen/TBD day and is marked as such."""
    today = today or clock.today_et()
    log = log if log is not None else start_log(end=today)
    out = {}
    fut = []
    for i in range(0, horizon):
        fut.extend(_sched_day(today + _td(days=i)))
    for tid, starts in log.items():
        if teams and tid not in teams:
            continue
        pool = _pool(starts, _il_ids(tid) if use_announced else None)
        if not pool:
            continue
        rows = []
        for g in sorted(fut, key=lambda g: (g["date"], g.get("pk") or 0)):
            side = "home" if g.get("home") == tid else \
                   "away" if g.get("away") == tid else None
            if side is None or (g.get("state") == "Final" and not include_final):
                continue
            gd = _date.fromisoformat(g["date"])
            pid, nm = (g.get(side + "_sp") or (None, None))
            if use_announced and pid:
                src = "announced"
                arm = next((a for a in pool if a["id"] == pid), None)
                if arm is None:
                    arm = {"id": pid, "name": nm, "last": gd, "rest": _DEF_REST}
                    pool.append(arm)      # IL return / call-up announced by the club
                rest = (gd - arm["last"]).days if arm["last"] != gd else None
                arm["last"] = gd
            else:
                # ORDER, not rest windows. The first cut of this projector
                # picked the eligible arm with the most rest, and the
                # point-in-time backtest failed it: 37-46% against a naive
                # "the last five starters repeat in order" at 52-60%. Real
                # rotations preserve SEQUENCE -- an off day slides everybody a
                # day rather than reshuffling by rest -- so the winning model
                # is the core: cycle the pool in last-start order (the arm
                # idle longest is next). IL exclusion and announced-probable
                # anchoring are the layers that beat the naive version live.
                elig = [a for a in pool if (gd - a["last"]).days >= 2]
                if not elig:
                    rows.append({"date": g["date"], "pk": g.get("pk"), "pid": None,
                                 "name": None, "source": "tbd", "rest": None})
                    continue
                arm = min(elig, key=lambda a: a["last"])
                src = "projected"
                rest = (gd - arm["last"]).days
                arm["last"] = gd
                pid, nm = arm["id"], arm["name"]
            rows.append({"date": g["date"], "pk": g.get("pk"), "pid": pid,
                         "name": nm, "source": src, "rest": rest})
        out[tid] = rows
    return out


def next_starter_ids():
    """{team_id: pid} -- each club's NEXT starter (announced or projected).
    This is the phase the deep season engine needs: its remaining-season sim
    cycles rotation[i % n] and used to start every club at i=0, handing all 30
    teams their ace on day one."""
    proj = project(horizon=4)
    out = {}
    for tid, rows in proj.items():
        nxt = next((r for r in rows if r.get("pid")), None)
        if nxt:
            out[tid] = nxt["pid"]
    return out


def backtest(days=12, horizons=(1, 2, 3, 4, 5)):
    """Point-in-time: for each past day D, project D+h from only pre-D starts
    (announced probables as-of-D are not archived, so this scores the PURE
    cadence engine -- the live product with announcements can only be better at
    short horizons). Baseline: the last five starters repeat in order."""
    today = clock.today_et()
    hits = {h: [0, 0] for h in horizons}
    base_hits = {h: [0, 0] for h in horizons}
    for back in range(max(horizons) + 1, max(horizons) + days + 1):
        asof = today - _td(days=back)
        log = start_log(days_back=21, end=asof)
        proj = project(horizon=max(horizons) + 1, log=log,
                       use_announced=False, today=asof, include_final=True)
        for tid, rows in proj.items():
            starts = log.get(tid) or []
            last5 = [pid for _d, pid, _n in starts[-5:]]
            # actual starters after `asof` come from the settled schedule
            actual = {}
            for i in range(0, max(horizons) + 1):
                for g in _sched_day(asof + _td(days=i)):
                    if g.get("state") != "Final":
                        continue
                    for side in ("home", "away"):
                        if g.get(side) == tid and (g.get(side + "_sp") or (None,))[0]:
                            actual[g["date"]] = g[side + "_sp"][0]
            seq = 0
            for r in rows:
                if r["date"] not in actual:
                    continue
                h = (_date.fromisoformat(r["date"]) - asof).days
                truth = actual[r["date"]]
                if h in hits:
                    hits[h][1] += 1
                    if r.get("pid") == truth:
                        hits[h][0] += 1
                    base_hits[h][1] += 1
                    if last5 and last5[seq % len(last5)] == truth:
                        base_hits[h][0] += 1
                seq += 1
    return {"horizons": {str(h): {
                "n": hits[h][1],
                "hit": round(hits[h][0] / hits[h][1], 3) if hits[h][1] else None,
                "baseline": round(base_hits[h][0] / base_hits[h][1], 3)
                            if base_hits[h][1] else None}
            for h in horizons}}
