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


def parse_grid_text(text, sport="f1"):
    """Parse a hand-pasted starting grid into a grid dict, for when the feed hasn't
    posted the real STARTING order yet (penalties, or an un-ingested race). Accepts
    lines like '1st Kimi Antonelli', '1. Charles Leclerc', 'P4 George Russell', or
    just one name per line (position = line order). Returns a finalized grid or None.
    This is the STARTING grid, so it already reflects any grid penalties."""
    if not text or not text.strip():
        return None
    grid, auto = {}, 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^\s*(?:P|#)?\s*(\d+)\s*(?:st|nd|rd|th)?[\.\):]?\s+(.+)$", line, re.I)
        if m:
            pos, name = int(m.group(1)), m.group(2)
        else:
            auto += 1
            pos, name = auto, line
        # strip a trailing team/constructor in parens or after a dash
        name = re.split(r"\s+[\-–—(]", name)[0].strip()
        nm = norm_name(name)
        if nm and pos and nm not in grid:
            grid[nm] = pos
            auto = max(auto, pos)
    if len(grid) < 2:
        return None
    return _finalize(grid, "Pasted grid", "F1" if sport == "f1" else "NASCAR")


def _finalize(grid, race, series, series_id=None, track=None):
    if not grid:
        return None
    by_full, by_last = _index(grid)
    return {"grid": grid, "race": race, "series": series, "series_id": series_id,
            "track": track, "field": max(grid.values()),
            "_full": by_full, "_last": by_last}


def nascar_track_type(name):
    """Classify a Cup track by the skill set it rewards. This drives EVERYTHING
    downstream: form lookups, win-model randomness, and DFS dominator modeling.
    Road/street courses are a different sport from ovals — a road ace's oval
    results say nothing about his street-race chances (and vice versa)."""
    t = (name or "").lower()
    if any(k in t for k in ("daytona", "talladega", "atlanta")):
        return "superspeedway"           # pack/draft racing -> high chaos
    if any(k in t for k in ("sonoma", "watkins", "cota", "circuit of the americas",
                            "road course", "roval", "street", "mexico")):
        return "road"                    # NOT bare "chicago" — that hits Chicagoland
                                         # Speedway (an oval); "street" = the Chicago
                                         # street race
    if any(k in t for k in ("martinsville", "richmond", "bristol", "phoenix",
                            "new hampshire", "wilkesboro", "iowa")):
        return "short"
    return "intermediate"                # 1.5-mile ovals, the default


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
                             series_id=series, track=race.get("track_name"))
    return None


def get_nascar_practice(race_name=None, date=None, year=None):
    """{normalized driver name: practice speed rank} from the upcoming weekend's
    FINAL practice run. Practice at THIS track in race trim is the freshest pace
    signal there is — a car that hasn't unloaded fast doesn't suddenly find
    speed on Sunday. None until practice runs."""
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
        practices = [r for r in feed.get("weekend_runs", [])
                     if r.get("run_type") == 1 and (r.get("results") or [])]
        if not practices:
            continue
        run = practices[-1]                      # final practice = race trim
        grid = {}
        for res in run["results"]:
            pos = res.get("finishing_position")
            nm = norm_name(res.get("driver_name", ""))
            if pos and nm:
                grid[nm] = int(pos)
        if grid:
            return grid
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


def get_nascar_form(year, today, series=1, n_races=5, track_type=None):
    """Recency-weighted average finish per driver over recent completed races.

    When `track_type` is given, form comes from the last several races OF THAT
    TYPE (reaching into the prior season — a year has only ~6 road races),
    blended 65/35 with overall recent form. Without this, a road/street ace's
    number gets poisoned by his oval results: SVG runs 25th on ovals and wins
    road courses; season-wide form made him a mid-packer at street races."""
    def _race_list(yr):
        try:
            return _get_json(f"https://cf.nascar.com/cacher/{yr}/{series}/race_list_basic.json")
        except Exception:
            return []

    def _form_from(done, yr_of):
        if not done:
            return {}
        with ThreadPoolExecutor(max_workers=5) as ex:
            results = list(ex.map(lambda r: _race_results(yr_of[id(r)], series, r["race_id"]), done))
        acc, wsum = {}, {}
        for w, res in zip(range(len(results), 0, -1), results):   # most recent heaviest
            for nm, fp in (res or {}).items():
                acc[nm] = acc.get(nm, 0.0) + w * fp
                wsum[nm] = wsum.get(nm, 0.0) + w
        prior, k = 20.0, 2.0
        return {nm: (acc[nm] + k * prior) / (wsum[nm] + k) for nm in acc}

    def build():
        races = _race_list(year)
        yr_of = {}
        done = []
        for r in races:
            if (r.get("race_date") or "")[:10] < today and r.get("race_id"):
                done.append(r); yr_of[id(r)] = year
        done.sort(key=lambda r: r["race_date"], reverse=True)
        overall = _form_from(done[:n_races], yr_of)
        if not track_type:
            return overall
        # Same-type races: this season first, then last season's, up to 8 total.
        typed = [r for r in done if nascar_track_type(r.get("track_name", "")) == track_type]
        if len(typed) < 8:
            prev = _race_list(int(year) - 1)
            pv = [r for r in prev
                  if r.get("race_id") and nascar_track_type(r.get("track_name", "")) == track_type]
            pv.sort(key=lambda r: r.get("race_date", ""), reverse=True)
            for r in pv[:8 - len(typed)]:
                typed.append(r); yr_of[id(r)] = int(year) - 1
        for r in typed:
            yr_of.setdefault(id(r), year)
        tf = _form_from(typed[:8], yr_of)
        # Blend: type form dominates where we have it; overall covers the rest.
        out = dict(overall)
        for nm, v in tf.items():
            base = overall.get(nm, v)
            out[nm] = 0.65 * v + 0.35 * base
        return out
    return _cached(("nascar_form", year, series, track_type), 6 * 3600, build) or {}


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
# NASCAR randomness varies hugely by track type: superspeedways are drafting
# lotteries (anyone can win), road/street courses are skill-deterministic — the
# ace converts. Grid/form weights shift too: road results are about the driver,
# so form (same-type!) dominates the starting spot.
_NASCAR_TAU = {"road": 6.0, "short": 9.5, "intermediate": 11.0, "superspeedway": 17.0}
_NASCAR_FORM_W = {"road": 0.65, "short": 0.5, "intermediate": 0.5, "superspeedway": 0.5}


def win_probs(grid_info, sport, form=None, track_type=None):
    """{normalized name: win probability} from the starting grid, optionally
    blended with recent form (a position-scale quality estimate per driver).
    For NASCAR, `track_type` steepens/flattens the field and re-weights form
    vs grid to match how that kind of race actually behaves."""
    if not grid_info:
        return {}
    sp = (sport or "").lower()
    tau = _TAU.get(sp, 8.0)
    fw = 0.5
    if sp == "nascar" and track_type:
        tau = _NASCAR_TAU.get(track_type, tau)
        fw = _NASCAR_FORM_W.get(track_type, fw)
    form = form or {}
    strengths = {}
    for nm, pos in grid_info["grid"].items():
        f = _match_prob(form, grid_info, nm) if form else None  # reuse name matcher
        eff = (1 - fw) * pos + fw * f if f is not None else pos  # blend grid + form
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
    # Blend in recent form (driver/car quality) when we can fetch it. NASCAR form
    # is TRACK-TYPE aware: at a street race the road-course results carry the
    # signal, not last week's oval.
    sp = (sport or "").lower()
    form, ttype = None, None
    try:
        if sp == "nascar":
            ttype = nascar_track_type(grid.get("track") or grid.get("race") or "")
            year = (date or datetime.date.today().isoformat())[:4]
            form = get_nascar_form(year, date or datetime.date.today().isoformat(),
                                   series=grid.get("series_id") or 1, track_type=ttype)
        elif sp == "f1":
            form = get_f1_form()
    except Exception:
        form = None
    probs = win_probs(grid, sport, form, track_type=ttype)
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
                    "track_type": ttype, "form_used": bool(form)}

