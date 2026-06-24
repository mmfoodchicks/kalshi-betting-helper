"""Deep full-season simulator for F1 (and NASCAR — see racing_sim_nascar pieces).

Mirrors the MLB deep engine for motorsport: we estimate each driver's qualifying
pace, race pace and reliability from this season's results, then for every
REMAINING weekend simulate qualifying (to set the grid / pole), the race (and the
sprint where the weekend has one), award championship points, and roll the season
forward. Over many simulated seasons we get each driver's title odds, expected
wins / poles / podiums, and the constructors' championship — to compare against
Kalshi and Polymarket futures.
"""

import random
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import racing

ERGAST = "https://api.jolpi.ca/ergast/f1/current"
F1_POINTS = [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]       # race top 10
SPRINT_POINTS = [8, 7, 6, 5, 4, 3, 2, 1]              # sprint top 8

# Field-spread knobs (in finishing-position units of noise). Tuned so the pole
# sits on a realistic share of wins and the title race isn't a coin flip.
_SIGMA_Q = 2.3      # qualifying variance
_SIGMA_R = 3.1      # race variance
_GRID_W = 0.40      # how much the race result leans on grid vs raw race pace


def _f1_results():
    """Per-driver season form: avg grid, avg finish, DNF rate, starts."""
    def build():
        races = []
        for off in (0, 100):                  # Ergast caps page size at 100
            d = racing._get_json(f"{ERGAST}/results.json?limit=100&offset={off}")
            rs = d.get("MRData", {}).get("RaceTable", {}).get("Races", [])
            races += rs
            if len(rs) < 5:
                break
        grid, fin, dnf, starts = (defaultdict(list), defaultdict(list),
                                  defaultdict(int), defaultdict(int))
        for r in races:
            for res in r.get("Results", []):
                did = res["Driver"]["driverId"]
                starts[did] += 1
                try:
                    grid[did].append(int(res["grid"]))
                except (ValueError, KeyError):
                    pass
                st = res.get("status", "")
                if st == "Finished" or st.startswith("+"):
                    try:
                        fin[did].append(int(res["position"]))   # running pace only
                    except (ValueError, KeyError):
                        pass
                else:
                    dnf[did] += 1                                # reliability, handled separately
        out = {}
        for did in starts:
            g = grid[did] or [13]
            f = fin[did] or [13]
            out[did] = {"avg_grid": sum(g) / len(g), "avg_fin": sum(f) / len(f),
                        "dnf": dnf[did] / starts[did], "starts": starts[did]}
        return out
    return racing._cached(("f1_results",), 6 * 3600, build) or {}


def f1_profiles():
    """{driver_id: profile} with points, constructor, and pace/reliability."""
    def build():
        d = racing._get_json(f"{ERGAST}/driverStandings.json")
        lst = d["MRData"]["StandingsTable"]["StandingsLists"]
        if not lst:
            return {}
        form = _f1_results()
        out = {}
        for x in lst[0]["DriverStandings"]:
            dr = x["Driver"]; did = dr["driverId"]
            con = x["Constructors"][-1]
            f = form.get(did, {"avg_grid": 13.0, "avg_fin": 13.0, "dnf": 0.08, "starts": 0})
            out[did] = {
                "id": did, "name": f"{dr.get('givenName','')} {dr.get('familyName','')}".strip(),
                "code": dr.get("code") or dr.get("familyName", "")[:3].upper(),
                "constructor": con["name"], "constructor_id": con["constructorId"],
                "points": float(x["points"]), "wins": int(x["wins"]),
                "position": int(x["position"]),
                "quali": f["avg_grid"], "race": f["avg_fin"], "dnf": min(0.45, f["dnf"]),
            }
        return out
    return racing._cached(("f1_profiles",), 3 * 3600, build) or {}


def f1_remaining():
    """Remaining races this season as [{round, name, sprint}]."""
    import datetime
    def build():
        d = racing._get_json(f"{ERGAST}.json")
        today = datetime.date.today().isoformat()
        out = []
        for r in d["MRData"]["RaceTable"]["Races"]:
            if r.get("date", "") >= today:
                out.append({"round": int(r["round"]), "name": r["raceName"],
                            "sprint": "Sprint" in r})
        return out
    return racing._cached(("f1_remaining",), 6 * 3600, build) or []


def _sim_quali(drivers, rng):
    """Grid order (list of driver ids) from qualifying pace + variance."""
    scored = [(d["quali"] + rng.gauss(0, _SIGMA_Q), d["id"]) for d in drivers]
    scored.sort()
    return [did for _, did in scored]


def _sim_race(drivers, grid, rng):
    """Finishing order (list of ids). Blends grid + race pace with variance, and
    drops DNFs to the back (classified behind all finishers)."""
    pos = {did: i + 1 for i, did in enumerate(grid)}
    by_id = {d["id"]: d for d in drivers}
    finishers, retired = [], []
    for did in grid:
        d = by_id[did]
        if rng.random() < d["dnf"]:
            retired.append(did)
            continue
        score = _GRID_W * pos[did] + (1 - _GRID_W) * d["race"] + rng.gauss(0, _SIGMA_R)
        finishers.append((score, did))
    finishers.sort()
    rng.shuffle(retired)                      # DNF order among themselves is noise
    return [did for _, did in finishers] + retired


def _award(order, points_table):
    return {did: points_table[i] for i, did in enumerate(order) if i < len(points_table)}


def _sim_one_season(profiles, remaining, rng):
    """Play out the remaining schedule once. Returns final driver points and the
    per-driver win/pole/podium tallies for this season."""
    drivers = list(profiles.values())
    pts = {d["id"]: d["points"] for d in drivers}
    wins = defaultdict(int); poles = defaultdict(int); podiums = defaultdict(int)
    for race in remaining:
        grid = _sim_quali(drivers, rng)
        poles[grid[0]] += 1
        if race["sprint"]:
            # Sprint: a second (noisier) shootout off the same quali pace.
            sgrid = _sim_quali(drivers, rng)
            sorder = _sim_race(drivers, sgrid, rng)
            for did, p in _award(sorder, SPRINT_POINTS).items():
                pts[did] += p
        order = _sim_race(drivers, grid, rng)
        wins[order[0]] += 1
        for did in order[:3]:
            podiums[did] += 1
        for did, p in _award(order, F1_POINTS).items():
            pts[did] += p
    return pts, wins, poles, podiums


def sim_f1(n=2000, seed=None):
    """Monte-Carlo the rest of the F1 season. Returns per-driver title odds,
    expected points/wins/poles/podiums, and the constructors' championship."""
    profiles = f1_profiles()
    remaining = f1_remaining()
    if not profiles:
        return None
    rng = random.Random(seed)
    con_of = {d["id"]: d["constructor"] for d in profiles.values()}
    champ = defaultdict(int); con_champ = defaultdict(int)
    pts_sum = defaultdict(float)
    w_sum = defaultdict(float); pole_sum = defaultdict(float); pod_sum = defaultdict(float)

    def one(_):
        return _sim_one_season(profiles, remaining, random.Random(rng.random()))

    with ThreadPoolExecutor(max_workers=8) as ex:
        results = ex.map(one, range(n))
        for pts, wins, poles, podiums in results:
            champ[max(pts, key=pts.get)] += 1
            con_pts = defaultdict(float)
            for did, p in pts.items():
                con_pts[con_of[did]] += p
                pts_sum[did] += p
                w_sum[did] += wins.get(did, 0)
                pole_sum[did] += poles.get(did, 0)
                pod_sum[did] += podiums.get(did, 0)
            con_champ[max(con_pts, key=con_pts.get)] += 1

    drivers = []
    for d in profiles.values():
        did = d["id"]
        drivers.append({
            "id": did, "name": d["name"], "code": d["code"], "constructor": d["constructor"],
            "points_now": d["points"], "wins_now": d["wins"], "position": d["position"],
            "title_pct": round(100 * champ[did] / n, 1),
            "proj_points": round(pts_sum[did] / n, 1),
            "exp_wins": round(w_sum[did] / n, 1), "exp_poles": round(pole_sum[did] / n, 1),
            "exp_podiums": round(pod_sum[did] / n, 1),
        })
    drivers.sort(key=lambda x: x["title_pct"], reverse=True)
    constructors = sorted(({"name": c, "title_pct": round(100 * v / n, 1)}
                           for c, v in con_champ.items()),
                          key=lambda x: x["title_pct"], reverse=True)
    return {"sport": "f1", "n_sims": n, "races_left": len(remaining),
            "drivers": drivers, "constructors": constructors}

