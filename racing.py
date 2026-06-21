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
import re
import unicodedata
import urllib.request

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


def _finalize(grid, race, series):
    if not grid:
        return None
    by_full, by_last = _index(grid)
    return {"grid": grid, "race": race, "series": series,
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
            return _finalize(grid, race.get("race_name", "race"), SERIES[series])
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
