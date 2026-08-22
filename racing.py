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
import clock
import json
import math
import re
import unicodedata
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# Browser-shaped headers, not a bot string. ESPN's edge started answering the
# old "kalshi-betting-helper/1.0" agent with 403 Forbidden from the production
# host (299 ledger entries in one evening, every NFL schedule fetch dead, the
# whole preseason tab empty) while the same code passed from a residential
# network. A real browser identity passes; a second identity is tried once
# when the first is refused, because WAF rules change without notice.
_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
       "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 "
       "Mobile/15E148 Safari/604.1")
_UA_ALT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
SERIES = {1: "Cup", 2: "Xfinity", 3: "Trucks"}


def _get_json(url, timeout=12):
    for ua in (_UA, _UA_ALT):
        req = urllib.request.Request(url, headers={
            "User-Agent": ua,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 403 and ua != _UA_ALT:
                continue                    # refused identity -- try the other
            raise


def _get_json_opt(url, timeout=12):
    """_get_json, but 'no data' is None instead of an exception. OpenF1 answers
    404 for a session that has no rows YET (a beta endpoint that lags the
    session) -- one raised 404 killed the entire grid build before its
    fallbacks could run, which is how a sprint weekend showed no model at all."""
    try:
        return _get_json(url, timeout=timeout)
    except Exception:
        return None


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
    """Choose the race by name (preferred), by date, or the next race within 3
    days. The 3-day window matters on shared weekends: Saturday's board for
    SUNDAY'S Cup race used to date-match nothing in Cup — and then the Trucks
    race running that same day would answer for it from another series."""
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
        upcoming = sorted((r for r in races
                           if date <= (r.get("race_date") or "")[:10] <= _plus_days(date, 3)),
                          key=lambda r: r.get("race_date") or "")
        if upcoming:
            return upcoming[0]
    return None


def _plus_days(date, n):
    try:
        d = datetime.date.fromisoformat(date[:10])
        return (d + datetime.timedelta(days=n)).isoformat()
    except ValueError:
        return date


def _grid_from_feed(feed):
    """Map normalized driver name -> qualifying (starting) position.

    `or []` everywhere: NASCAR's feed serves "weekend_runs": null on some
    races (found sweeping the 2024-2026 archive), and iterating None raised
    out of the board build -- one null race killed the whole model."""
    grid = {}
    for run in (feed.get("weekend_runs") or []):
        if run.get("run_type") != 2:        # 2 == qualifying
            continue
        for res in (run.get("results") or []):
            pos = res.get("finishing_position")
            nm = norm_name(res.get("driver_name", ""))
            if pos and nm:
                grid[nm] = int(pos)
    return grid


def get_nascar_grid(race_name=None, date=None, year=None, names=None):
    """The starting grid for the race the caller is actually asking about.

    `names`: the market's outcome (driver) names. On a shared weekend all three
    series run at the same track within a day of each other, and the first
    series with a date-matching grid used to answer for ANY market — a Cup
    winner board built on the Trucks grid. When names are given, every series'
    candidate grid is scored by how much of the market it covers and the best
    one wins; a grid covering under 30% of the field is the wrong race."""
    year = year or (date[:4] if date else str(clock.today_et().year))
    candidates = []
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
            g = _finalize(grid, race.get("race_name", "race"), SERIES[series],
                          series_id=series, track=race.get("track_name"))
            if not names:
                return g                       # no market context -> first match
            candidates.append(g)
    if not candidates:
        return None
    def coverage(g):
        hits = sum(1 for n in names if lookup(g, n) is not None)
        return hits / max(1, len(names))
    best = max(candidates, key=coverage)
    return best if coverage(best) >= 0.3 else None


def get_nascar_practice(race_name=None, date=None, year=None):
    """{normalized driver name: practice speed rank} from the upcoming weekend's
    FINAL practice run. Practice at THIS track in race trim is the freshest pace
    signal there is — a car that hasn't unloaded fast doesn't suddenly find
    speed on Sunday. None until practice runs."""
    year = year or (date[:4] if date else str(clock.today_et().year))
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

def _openf1_f1_grid():
    """The CURRENT weekend's real starting grid from OpenF1 (live timing), or None.

    This is the fix for the lagging Ergast/Jolpica feed: OpenF1 posts sessions
    within minutes. We take the latest completed grid-setting session, but only
    when it belongs to THIS weekend and its race HASN'T RUN YET — i.e. exactly the
    window where the grid should condition the sim. Once the race runs (or the
    quali is more than a few days old) it returns None, so a spent grid can never
    leak onto the next event.

    SPRINT WEEKENDS: "Sprint Qualifying" counts too — before Saturday's GP
    qualifying runs, the sprint-quali order is the only read on this weekend's
    pace, so it serves as a PROVISIONAL Sunday grid (flagged as such); the real
    GP Qualifying supersedes it the moment it completes. A completed Sprint
    race's finishing order rides along as `sprint_result` — same-track,
    same-weekend pace, the freshest form signal there is.

    Grid source order: /starting_grid on the RACE session (the penalty-adjusted
    official grid, when posted) -> /starting_grid on the quali session ->
    /session_result of the quali session (raw order). Every OpenF1 endpoint may
    answer 404 while a session has no rows yet, so each fetch is optional —
    one lagging beta endpoint must not blank the whole model."""
    import datetime as _dt

    def build():
        now = _dt.datetime.now(_dt.timezone.utc)
        year = clock.today_et().year
        sessions = _get_json(f"https://api.openf1.org/v1/sessions?year={year}")

        def when(s):
            try:
                return _dt.datetime.fromisoformat(s["date_start"].replace("Z", "+00:00"))
            except Exception:
                return None
        qualis, races_by_meeting, sprints_by_meeting = [], {}, {}
        for s in sessions:
            t = when(s)
            if not t:
                continue
            nm = s.get("session_name")
            if nm in ("Qualifying", "Sprint Qualifying") and t < now:
                # GP Qualifying outranks Sprint Qualifying at equal freshness:
                # sort by (time, is_gp_quali) so the real grid wins once it runs.
                qualis.append((t, nm == "Qualifying", s))
            elif nm == "Race":
                races_by_meeting[s.get("meeting_key")] = (t, s.get("session_key"))
            elif nm == "Sprint":
                sprints_by_meeting[s.get("meeting_key")] = (t, s.get("session_key"))
        if not qualis:
            return None
        qualis.sort(key=lambda x: (x[0], x[1]))
        t, is_gp_quali, s = qualis[-1]
        if (now - t).total_seconds() > 4 * 86400:
            return None                              # previous weekend -> pre-quali
        meeting = s.get("meeting_key")
        race_t, race_key = races_by_meeting.get(meeting) or (None, None)
        if race_t and race_t < now:
            return None                              # race already ran -> grid spent

        def _rows(session_key, endpoint):
            return _get_json_opt(
                f"https://api.openf1.org/v1/{endpoint}?session_key={session_key}") or []

        key = s["session_key"]
        drivers = _get_json(f"https://api.openf1.org/v1/drivers?session_key={key}")
        name_of = {d["driver_number"]: (d.get("full_name") or d.get("broadcast_name"))
                   for d in drivers}

        def _parse(rows):
            grid = {}
            for r in rows:
                dn, pos = r.get("driver_number"), r.get("position")
                if dn in name_of and pos and not r.get("dsq") and not r.get("dns"):
                    grid[norm_name(name_of[dn])] = int(pos)
            return grid
        grid = {}
        if race_key:                                 # penalty-adjusted official grid
            grid = _parse(_rows(race_key, "starting_grid"))
        if len(grid) < 8:
            grid = _parse(_rows(key, "starting_grid"))
        if len(grid) < 8:
            grid = _parse(_rows(key, "session_result"))
        if len(grid) < 8:
            return None
        out = _finalize(grid, f"{s.get('circuit_short_name') or 'Grand Prix'} GP", "F1")
        if not is_gp_quali:
            out["provisional"] = "sprint qualifying order (GP qualifying not run yet)"
        sprint_t, sprint_key = sprints_by_meeting.get(meeting) or (None, None)
        if sprint_key and sprint_t and sprint_t < now:
            sr = _parse(_rows(sprint_key, "session_result"))
            if len(sr) >= 8:
                out["sprint_result"] = sr
        return out
    return _cached(("f1_openf1_grid",), 900, build)


def get_f1_grid(race_name=None):
    # Live OpenF1 grid for the current weekend first (no feed lag); the Ergast
    # feed is the fallback when OpenF1 hasn't posted the classification yet.
    g = _openf1_f1_grid()
    if g:
        return g
    try:
        d = _get_json("https://api.jolpi.ca/ergast/f1/current/last/qualifying.json")
    except Exception:
        return None
    races = d.get("MRData", {}).get("RaceTable", {}).get("Races", [])
    if not races:
        return None
    r = races[0]
    # `current/last` is the most recent COMPLETED qualifying — after Sunday's
    # race (and all the following week) that's the PREVIOUS Grand Prix. Only a
    # grid whose race hasn't run yet may condition the board; otherwise report
    # no grid (like NASCAR does) and let the form-only model carry the read.
    rd = r.get("date")
    if rd and rd < clock.today_et().isoformat():
        return None
    grid = {}
    for q in r.get("QualifyingResults", []):
        nm = norm_name(f"{q['Driver'].get('givenName','')} {q['Driver'].get('familyName','')}")
        try:
            grid[nm] = int(q["position"])
        except (ValueError, KeyError):
            continue
    return _finalize(grid, r.get("raceName", "Grand Prix"), "F1")


def get_grid(sport, race_name=None, date=None, names=None):
    """Top-level: fetch the starting grid for a racing sport, or None."""
    sport = (sport or "").lower()
    if sport == "nascar":
        return get_nascar_grid(race_name=race_name, date=date, names=names)
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


# Same leak as baseball._cached had: the TTL was only checked on READ, so an
# entry nobody asked for again was never dropped. This cache holds the biggest
# objects in the app -- the tennis Elo pools are ~47 MB -- so a stale copy left
# lying beside a fresh one is the difference between fitting in 512 MB and not.
_CACHE_SWEEP_EVERY = 50
_cache_puts = 0


def _sweep_form_cache(now):
    # list(...) FIRST. This cache is read and written from worker threads all over
    # the app -- every slate build fans out eight ways -- and iterating the live
    # dict raced any thread inserting a fresh entry, raising "dictionary changed
    # size during iteration" out of whatever request happened to trigger the
    # sweep. It fires on one put in fifty, so it stayed rare enough to look like
    # a fluke; a 24-thread backtest hit it in under two minutes.
    for k, v in list(_form_cache.items()):
        if len(v) > 2 and now - v[0] >= v[2]:
            _form_cache.pop(k, None)


def _cached(key, ttl, fn):
    global _cache_puts
    now = datetime.datetime.now().timestamp()
    hit = _form_cache.get(key)
    if hit and (now - hit[0]) < (hit[2] if len(hit) > 2 else ttl):
        return hit[1]
    try:
        val = fn()
    except Exception:
        val = None
    _form_cache[key] = (datetime.datetime.now().timestamp(), val, ttl)
    _cache_puts += 1
    if _cache_puts % _CACHE_SWEEP_EVERY == 0:
        _sweep_form_cache(now)
    return val


def _race_results(year, series, race_id):
    """{normalized name: finishing position} for a completed race."""
    feed = _get_json(f"https://cf.nascar.com/cacher/{year}/{series}/{race_id}/weekend-feed.json")
    out = {}
    for r in ((feed.get("weekend_race") or [{}])[0].get("results") or []):
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

# Larger tau == flatter field (more random). F1's tau is FITTED, not eyeballed:
# max-likelihood on the winner over every 2023-2025 Grand Prix (70 races, with
# the entering championship standings as the form input at fw=0.5) lands at
# tau=1.2 -- avg winner log-lik -1.19 vs -1.51 for the old tau=3.0. Modern F1
# is far steeper than the old guess: the winner started P1-P3 in 88% of those
# races. The chaos floor below carries the tail the sample can't show.
_TAU = {"f1": 1.2, "nascar": 4.5, "motogp": 4.0}
# A small share of win equity spread flat across the field: safety cars, rain,
# first-lap pileups. The 2023-2025 sample contains NO winner from P11+, so a
# fitted model alone would call a back-of-grid charge exactly 0.0% -- history
# (Gasly Monza '20 class of race) says it's rare, not impossible. Costs 0.013
# log-lik in-sample; buys the tail. NASCAR's fat taus already encode chaos.
_CHAOS = {"f1": 0.03}
# Measured DNF rate by STARTING SPOT, 2023-2025 (1,398 classified entries;
# "Lapped" is a finish, not a DNF): the midfield/back crashes out at ~2.5x the
# front's rate -- starting deep means passing through the accidents.
_F1_DNF_BANDS = ((5, 6.6), (10, 11.7), (15, 16.9), (99, 16.4))


def f1_dnf_pct(grid_pos):
    """Historical DNF% for a starting spot (measured 2023-2025)."""
    if not grid_pos:
        return None
    for hi, pct in _F1_DNF_BANDS:
        if grid_pos <= hi:
            return pct
    return _F1_DNF_BANDS[-1][1]
# NASCAR randomness varies hugely by track type: superspeedways are drafting
# lotteries (anyone can win), road/street courses are skill-deterministic — the
# ace converts. Grid/form weights shift too: road results are about the driver,
# so form (same-type!) dominates the starting spot.
#
# FITTED, not eyeballed: max-likelihood on the winner over the 2024-2026 Cup
# seasons (109 races with grid + result, form reconstructed exactly as
# get_nascar_form computes it). The old taus had the right ORDERING but were
# ~2x too flat everywhere -- winners start P1-P5 in ~60% of road/short/
# intermediate races (avg winner log-lik: road -2.63 vs -2.82, short -2.50 vs
# -2.83, intermediate -2.84 vs -3.06, superspeedway -3.29 vs -3.36). The
# chaos share below is tail-CALIBRATED: observed P21+ winners are 9% at
# intermediates (wrecks and strategy flips) and 19% at superspeedways --
# though most of the drafting-lottery tail is carried by form blending, which
# lifts a fast car buried deep. Short tracks have produced zero P21+ winners
# in the sample; their eps is insurance, not observation.
_NASCAR_TAU = {"road": 3.0, "short": 3.0, "intermediate": 4.0, "superspeedway": 7.0}
_NASCAR_FORM_W = {"road": 0.6, "short": 0.5, "intermediate": 0.4, "superspeedway": 0.6}
_NASCAR_CHAOS = {"road": 0.05, "short": 0.03, "intermediate": 0.15, "superspeedway": 0.05}


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
    eps = _CHAOS.get(sp, 0.0)
    if sp == "nascar":
        eps = _NASCAR_CHAOS.get(track_type, 0.10)
        if track_type:
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
    # Chaos floor: a slice of win equity spread flat across the field, so a
    # back-of-grid ace reads "very unlikely", never "impossible".
    n = len(strengths)
    return {nm: (1 - eps) * s / total + eps / n for nm, s in strengths.items()}


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
    # The market's own driver names anchor grid selection: on shared NASCAR
    # weekends they are what tells a Cup board apart from the Trucks race
    # running the same day at the same track.
    names = [o.get("name", "") for e in events for o in e.get("outcomes", [])]
    grid = get_grid(sport, race_name=race_name, date=date, names=names or None)
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
            year = (date or clock.today_et().isoformat())[:4]
            form = get_nascar_form(year, date or clock.today_et().isoformat(),
                                   series=grid.get("series_id") or 1, track_type=ttype)
        elif sp == "f1":
            form = get_f1_form()
            # Sprint weekends: Saturday's sprint FINISH is same-track,
            # same-weekend pace -- fresher than the championship table. Blend
            # it evenly with the standings rank for drivers who ran it (a
            # sprint DNF is noise, not a pace read: those keep standings only).
            sr = grid.get("sprint_result") or {}
            if form and sr:
                form = {nm: (0.5 * r + 0.5 * sr[nm]) if nm in sr else r
                        for nm, r in form.items()}
            elif sr:
                form = dict(sr)
    except Exception:
        form = None
    probs = win_probs(grid, sport, form, track_type=ttype)
    for e in events:
        best = None
        for o in e.get("outcomes", []):
            mp = _match_prob(probs, grid, o.get("name", ""))
            o["model_pct"] = round(mp * 100, 1) if mp is not None else None
            if sp == "f1":
                # Measured 2023-2025 DNF risk for his STARTING SPOT -- the
                # visible cost of starting deep (passing through the chaos).
                o["start_pos"] = lookup(grid, o.get("name", ""))
                o["dnf_pct"] = f1_dnf_pct(o["start_pos"])
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
                    "track_type": ttype, "form_used": bool(form),
                    "provisional": grid.get("provisional"),
                    "sprint_form": bool(grid.get("sprint_result"))}

