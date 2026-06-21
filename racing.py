"""Starting-grid fetch for NASCAR / F1 DFS.

DraftKings' DKSalaries.csv has no starting position, but DK racing scoring is
dominated by place differential (+1 per spot gained, -1 per spot lost). A driver
who qualifies up front has almost no place-differential upside and large
downside; season-average fantasy points (FPPR) miss this entirely.

This module pulls the actual qualifying order so the DFS sim can adjust each
driver for an atypically good/bad starting spot. Sources:
  - NASCAR: cf.nascar.com weekend feed (qualifying run)
  - F1:     jolpica/Ergast API (last qualifying)

Everything degrades gracefully: any failure returns None and the caller falls
back to the plain FPPR model.
"""

import datetime
import json
import math
import re
import unicodedata
import urllib.request
from concurrent.futures import ThreadPoolExecutor

_UA = "kalshi-betting-helper/1.0"
SERIES = {1: "Cup", 2: "Xfinity", 3: "Trucks"}


def _get_json(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def norm_name(name):
    """Normalize a driver name for matching across data sources (drop accents,
    punctuation, suffixes, case)."""
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b", " ", s.lower())
    s = re.sub(r"[^a-z ]", " ", s)
    return " ".join(s.split())


def _index(grid):
    """Build full-name and last-name lookup indexes from {norm_name: pos}."""
    by_full = dict(grid)
    by_last = {}
    for nm, pos in grid.items():
        last = nm.split()[-1] if nm.split() else nm
        # Only keep last-name keys that are unambiguous.
        by_last[last] = None if last in by_last else pos
    by_last = {k: v for k, v in by_last.items() if v is not None}
    return by_full, by_last


def lookup(grid_info, name):
    """Return the starting position for a DK driver name, or None."""
    if not grid_info:
        return None
    nm = norm_name(name)
    by_full, by_last = grid_info["_full"], grid_info["_last"]
    if nm in by_full:
        return by_full[nm]
    parts = nm.split()
    if parts and parts[-1] in by_last:
        return by_last[parts[-1]]
    return None


def _finalize(grid, race, series, series_id=None):
    if not grid:
        return None
    by_full, by_last = _index(grid)
    return {"grid": grid, "race": race, "series": series, "series_id": series_id,
            "field": max(grid.values()), "_full": by_full, "_last": by_last}


# --- NASCAR ------------------------------------------------------------------

def _pick_race(races, race_name, date):
    """Choose the race by name (preferred) or by date."""
    if race_name:
        target = norm_name(race_name)
        for r in races:
            rn = norm_name(r.get("race_name", ""))
            if rn and (rn in target or target in rn):
                return r
    if date:
        for r in races:
            if (r.get("race_date") or "")[:10] == date:
                return r
    return None


def _grid_from_feed(feed):
    """Map normalized driver name -> qualifying (starting) position."""
    grid = {}
    for run in feed.get("weekend_runs", []):
        if run.get("run_type") != 2:        # 2 == qualifying
            continue
        for res in run.get("results", []):
            pos = res.get("finishing_position")
            nm = norm_name(res.get("driver_name", ""))
            if pos and nm:
                grid[nm] = int(pos)
    return grid


def get_nascar_grid(race_name=None, date=None, year=None):
    year = year or (date[:4] if date else str(datetime.date.today().year))
    for series in (1, 2, 3):
        try:
            races = _get_json(f"https://cf.nascar.com/cacher/{year}/{series}/race_list_basic.json")
        except Exception:
            continue
        race = _pick_race(races, race_name, date)
        if not race:
            continue
        try:
            feed = _get_json(
                f"https://cf.nascar.com/cacher/{year}/{series}/{race['race_id']}/weekend-feed.json")
        except Exception:
            continue
        grid = _grid_from_feed(feed)
        if grid:
            return _finalize(grid, race.get("race_name", "race"), SERIES[series],
                             series_id=series)
    return None


# --- F1 ----------------------------------------------------------------------

def get_f1_grid(race_name=None):
    try:
        d = _get_json("https://api.jolpi.ca/ergast/f1/current/last/qualifying.json")
    except Exception:
        return None
    races = d.get("MRData", {}).get("RaceTable", {}).get("Races", [])
    if not races:
        return None
    r = races[0]
    grid = {}
    for q in r.get("QualifyingResults", []):
        nm = norm_name(f"{q['Driver'].get('givenName','')} {q['Driver'].get('familyName','')}")
        try:
            grid[nm] = int(q["position"])
        except (ValueError, KeyError):
            continue
    return _finalize(grid, r.get("raceName", "Grand Prix"), "F1")


def get_grid(sport, race_name=None, date=None):
    """Top-level: fetch the starting grid for a racing sport, or None."""
    sport = (sport or "").lower()
    if sport == "nascar":
        return get_nascar_grid(race_name=race_name, date=date)
    if sport == "f1":
        return get_f1_grid(race_name=race_name)
    return None


# --- Recent form (driver/car quality, independent of this race) --------------
# Qualifying order predicts the winner, but it misses car/driver quality: a top
# team that qualified poorly is still dangerous. We fold in recent results --
# NASCAR from the last few races' finishing positions, F1 from the championship
# standings -- so the model knows who's actually fast, not just who qualified up
# front today.

_form_cache = {}            # key -> (epoch, value)


def _cached(key, ttl, fn):
    hit = _form_cache.get(key)
    if hit and (datetime.datetime.now().timestamp() - hit[0]) < ttl:
        return hit[1]
    try:
        val = fn()
    except Exception:
        val = None
    _form_cache[key] = (datetime.datetime.now().timestamp(), val)
    return val


def _race_results(year, series, race_id):
    """{normalized name: finishing position} for a completed race."""
    feed = _get_json(f"https://cf.nascar.com/cacher/{year}/{series}/{race_id}/weekend-feed.json")
    out = {}
    for r in (feed.get("weekend_race") or [{}])[0].get("results", []):
        nm = norm_name(r.get("driver_fullname") or r.get("driver_name") or "")
        fp = r.get("finishing_position")
        if nm and fp:
            out[nm] = int(fp)
    return out


def get_nascar_form(year, today, series=1, n_races=5):
    """Recency-weighted average finish per driver over recent completed races."""
    def build():
        races = _get_json(f"https://cf.nascar.com/cacher/{year}/{series}/race_list_basic.json")
        done = [r for r in races if (r.get("race_date") or "")[:10] < today and r.get("race_id")]
        done.sort(key=lambda r: r["race_date"], reverse=True)
        done = done[:n_races]
        if not done:
            return {}
        with ThreadPoolExecutor(max_workers=5) as ex:
            results = list(ex.map(lambda r: _race_results(year, series, r["race_id"]), done))
        # Most recent race weighted highest.
        acc, wsum = {}, {}
        for w, res in zip(range(len(results), 0, -1), results):
            for nm, fp in (res or {}).items():
                acc[nm] = acc.get(nm, 0.0) + w * fp
                wsum[nm] = wsum.get(nm, 0.0) + w
        # Regress lightly toward a mid-pack prior for small samples.
        prior, k = 20.0, 2.0
        return {nm: (acc[nm] + k * prior) / (wsum[nm] + k) for nm in acc}
    return _cached(("nascar_form", year, series), 6 * 3600, build) or {}


def get_f1_form():
    """Championship-standings rank per driver (1 = leader)."""
    def build():
        d = _get_json("https://api.jolpi.ca/ergast/f1/current/driverStandings.json")
        lst = d.get("MRData", {}).get("StandingsTable", {}).get("StandingsLists", [])
        if not lst:
            return {}
        out = {}
        for x in lst[0].get("DriverStandings", []):
            nm = norm_name(f"{x['Driver'].get('givenName','')} {x['Driver'].get('familyName','')}")
            try:
                out[nm] = int(x["position"])
            except (ValueError, KeyError):
                continue
        return out
    return _cached(("f1_form",), 6 * 3600, build) or {}


# --- Win model + Kalshi edge -------------------------------------------------
# Starting position is a strong, independent predictor of who wins a race
# (especially F1, where the pole sits on ~40% of wins; NASCAR is far flatter).
# We turn the grid into win probabilities with a sport-calibrated exponential
# decay -- an independent model we can compare against Kalshi's prices to find
# an actual edge, rather than just echoing the market favorite.

# Larger tau == flatter field (more random). Calibrated to rough pole win rates.
_TAU = {"f1": 3.0, "nascar": 11.0, "motogp": 4.0}


def win_probs(grid_info, sport, form=None):
    """{normalized name: win probability} from the starting grid, optionally
    blended with recent form (a position-scale quality estimate per driver)."""
    if not grid_info:
        return {}
    tau = _TAU.get((sport or "").lower(), 8.0)
    form = form or {}
    strengths = {}
    for nm, pos in grid_info["grid"].items():
        f = _match_prob(form, grid_info, nm) if form else None  # reuse name matcher
        eff = 0.5 * pos + 0.5 * f if f is not None else pos     # blend grid + form
        strengths[nm] = math.exp(-(eff - 1) / tau)
    total = sum(strengths.values())
    if total <= 0:
        return {}
    return {nm: s / total for nm, s in strengths.items()}


def _match_prob(probs, grid_info, name):
    """Find a driver's model win prob by name (full, then last-name)."""
    nm = norm_name(name)
    if nm in probs:
        return probs[nm]
    parts = nm.split()
    if parts:
        last = parts[-1]
        hits = [p for n2, p in probs.items() if n2.split() and n2.split()[-1] == last]
        if len(hits) == 1:
            return hits[0]
    return None


def race_board(sport, events, date=None):
    """Merge an independent grid win model into Kalshi racing events.

    `events` is the de-vig'd output of sports.get_events. For each event we
    attach each driver's model win % and the edge vs the Kalshi ask, then pick
    the driver with the best positive edge (a real model lean, not the favorite).
    Returns (events, grid_meta). On no grid, events are returned unchanged.
    """
    race_name = events[0]["title"] if events else None
    grid = get_grid(sport, race_name=race_name, date=date)
    if not grid:
        return events, {"available": False,
                        "reason": "no qualifying grid posted yet"}
    # Blend in recent form (driver/car quality) when we can fetch it.
    sp = (sport or "").lower()
    form = None
    try:
        if sp == "nascar":
            year = (date or datetime.date.today().isoformat())[:4]
            form = get_nascar_form(year, date or datetime.date.today().isoformat(),
                                   series=grid.get("series_id") or 1)
        elif sp == "f1":
            form = get_f1_form()
    except Exception:
        form = None
    probs = win_probs(grid, sport, form)
    for e in events:
        best = None
        for o in e.get("outcomes", []):
            mp = _match_prob(probs, grid, o.get("name", ""))
            o["model_pct"] = round(mp * 100, 1) if mp is not None else None
            ask = o.get("yes_ask")
            if mp is not None and ask is not None:
                o["edge_cents"] = round(mp * 100 - ask, 1)
                if o["edge_cents"] > 0 and (best is None or o["edge_cents"] > best["edge_cents"]):
                    best = {"name": o["name"], "yes_ask": ask,
                            "model_pct": o["model_pct"], "edge_cents": o["edge_cents"]}
            else:
                o["edge_cents"] = None
        # Model pick: best positive edge, with a small floor to avoid noise.
        e["model_pick"] = best if (best and best["edge_cents"] >= 2) else None
    return events, {"available": True, "race": grid["race"],
                    "series": grid["series"], "field": grid["field"],
                    "form_used": bool(form)}

