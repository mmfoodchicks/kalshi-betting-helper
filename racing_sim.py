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

# Field-spread knobs. Tuned so the pole sits on a realistic share of wins and the
# title race isn't a coin flip.
_SIGMA_Q = 0.20     # qualifying variance, in SECONDS of lap time (quali is a
                    # gap-to-pole now); ~one-session spread for the same car
_SIGMA_R = 3.1      # race variance, in finishing-position units
_GRID_W = 0.40      # how much the race result leans on grid vs raw race pace
# Random-event rates (per driver, per race).
_GRID_PEN = 0.045   # engine/gearbox grid penalty -> start from the back
_TIME_PEN = 0.05    # in-race time penalty -> drop ~4-9 places
_INCIDENT = 0.015   # flat crash/incident DNF on top of each driver's reliability


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
        # Recency-weighted race pace (recent rounds count more -> catches car
        # upgrades / form). fin_acc/fin_w accumulate weighted finish positions.
        grid, dnf, starts = defaultdict(list), defaultdict(int), defaultdict(int)
        fin_acc, fin_w = defaultdict(float), defaultdict(float)
        for i, r in enumerate(races):
            w = i + 1
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
                        fin_acc[did] += w * int(res["position"]); fin_w[did] += w
                    except (ValueError, KeyError):
                        pass
                else:
                    dnf[did] += 1                                # reliability, handled separately
        # Early in a season (or for part-timers) a couple of results are pure
        # noise, so we shrink toward stable priors by sample size:
        #  - race pace -> avg grid (where you start strongly predicts the finish),
        #  - DNF rate  -> a field-typical ~9%.
        # A rookie's two lucky podiums no longer read as "finishes 1st on average."
        _FIN_K, _DNF_K = 6.0, 5.0
        out = {}
        for did in starts:
            g = grid[did] or [13]
            avg_grid = sum(g) / len(g)
            raw_fin = fin_acc[did] / fin_w[did] if fin_w[did] else avg_grid
            avg_fin = (fin_w[did] * raw_fin + _FIN_K * avg_grid) / (fin_w[did] + _FIN_K)
            dnf_rate = (dnf[did] + _DNF_K * 0.09) / (starts[did] + _DNF_K)
            out[did] = {"avg_grid": avg_grid, "avg_fin": avg_fin,
                        "dnf": dnf_rate, "starts": starts[did]}
        return out
    return racing._cached(("f1_results",), 6 * 3600, build) or {}


def _qtime(t):
    """Ergast lap-time string -> seconds ('1:12.578' -> 72.578)."""
    if not t:
        return None
    try:
        if ":" in t:
            m, s = t.split(":")
            return int(m) * 60 + float(s)
        return float(t)
    except ValueError:
        return None


def _f1_quali_gaps():
    """Per-driver qualifying pace as a RECENCY-WEIGHTED average gap to pole, in
    seconds. This is far finer than grid position: it knows whether a P2 was 0.05s
    or 0.5s off pole, so pole odds reflect true pace, not just rank."""
    def build():
        races = []
        for off in (0, 100):
            d = racing._get_json(f"{ERGAST}/qualifying.json?limit=100&offset={off}")
            rs = d.get("MRData", {}).get("RaceTable", {}).get("Races", [])
            races += rs
            if len(rs) < 5:
                break
        acc, wsum = defaultdict(float), defaultdict(float)
        for i, r in enumerate(races):                 # chronological -> later = more recent
            w = i + 1                                  # linear recency weight
            bests = {}
            for x in r.get("QualifyingResults", []):
                ts = [_qtime(x.get(q)) for q in ("Q1", "Q2", "Q3")]
                ts = [t for t in ts if t]
                if ts:
                    bests[x["Driver"]["driverId"]] = min(ts)
            if not bests:
                continue
            pole = min(bests.values())
            for did, b in bests.items():
                acc[did] += w * min(b - pole, 3.0)     # cap blowout/wet-session gaps
                wsum[did] += w
        # Shrink toward a midfield ~0.7s gap so a single banker lap (or a
        # rain-shortened session) doesn't crown someone the pole favourite.
        _QK = 4.0
        return {did: (acc[did] + _QK * 0.7) / (wsum[did] + _QK) for did in acc}
    return racing._cached(("f1_quali_gaps",), 6 * 3600, build) or {}


def f1_profiles():
    """{driver_id: profile} with points, constructor, and pace/reliability."""
    def build():
        d = racing._get_json(f"{ERGAST}/driverStandings.json")
        lst = d["MRData"]["StandingsTable"]["StandingsLists"]
        if not lst:
            return {}
        form = _f1_results()
        gaps = _f1_quali_gaps()
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
                # quali pace is now a gap-to-pole in SECONDS (default ~1.1s = midfield
                # for drivers with no quali data); race pace stays position-scale.
                "quali": gaps.get(did, 1.1), "avg_grid": f["avg_grid"],
                "race": f["avg_fin"], "dnf": min(0.45, f["dnf"]),
            }
        return out
    return racing._cached(("f1_profiles",), 3 * 3600, build) or {}


def f1_remaining():
    """Remaining races as [{round, name, sprint, circuit, wet_prob, avg_wind}].
    Wet-race probability + wind come from the circuit's historical race-day
    climate (Open-Meteo archive)."""
    import datetime
    import race_weather
    def build():
        d = racing._get_json(f"{ERGAST}.json")
        today = datetime.date.today().isoformat()
        out = []
        for r in d["MRData"]["RaceTable"]["Races"]:
            if r.get("date", "") >= today:
                loc = (r.get("Circuit") or {}).get("Location") or {}
                clim = race_weather.climate(loc.get("lat"), loc.get("long"), r["date"]) or {}
                out.append({"round": int(r["round"]), "name": r["raceName"],
                            "sprint": "Sprint" in r,
                            "circuit": (r.get("Circuit") or {}).get("circuitName"),
                            "wet_prob": clim.get("wet_prob", 0.12),
                            "avg_wind": clim.get("avg_wind")})
        return out
    return racing._cached(("f1_remaining",), 6 * 3600, build) or []


def _sim_quali(drivers, rng):
    """Full Q1/Q2/Q3 knockout qualifying -> grid order (list of driver ids).

    Each segment is a FRESH flying lap (its own noise draw), and the slowest are
    knocked out: Q1 trims the field to 15, Q2 to 10, Q3 is the top-10 pole
    shootout. This is what makes pole odds realistic — a genuinely fast car gets
    three laps to avoid elimination and a clean shootout for P1, rather than a
    single draw where one bad lap buries it."""
    grid_pos = {}
    remaining = list(drivers)
    for cut in (15, 10, 0):                    # survivors after Q1, Q2; Q3 = final 10
        timed = sorted(remaining, key=lambda d: d["quali"] + rng.gauss(0, _SIGMA_Q))
        if cut and len(timed) > cut:
            for i, d in enumerate(timed[cut:]):    # eliminated -> grid (cut+1)+
                grid_pos[d["id"]] = cut + 1 + i
            remaining = timed[:cut]
        else:
            for i, d in enumerate(timed):          # final segment sets the rest
                grid_pos[d["id"]] = 1 + i
            break
    return [did for did, _ in sorted(grid_pos.items(), key=lambda kv: kv[1])]


def _apply_grid_penalties(grid, rng):
    """Engine/gearbox grid penalties: a driver who takes a penalty (or stalls on
    the formation lap) is sent to the back for the race start. Pole is still
    credited from qualifying — only the race START grid moves."""
    clean, penalized = [], []
    for did in grid:
        (penalized if rng.random() < _GRID_PEN else clean).append(did)
    return clean + penalized


def _sim_race(drivers, grid, rng, wet=False):
    """Finishing order (list of ids). Blends grid + race pace with variance, plus
    random events: DNFs (crash/mechanical + a flat incident chance) drop to the
    back, and time penalties cost positions. A WET race is far more chaotic — more
    spins/DNFs, wider variance, and tyre-call gambles (the wrong-tyre fiasco) that
    can drop a frontrunner or vault a midfielder."""
    sigma = _SIGMA_R * (1.8 if wet else 1.0)
    dnf_extra = 0.06 if wet else 0.0
    pos = {did: i + 1 for i, did in enumerate(grid)}
    by_id = {d["id"]: d for d in drivers}
    finishers, retired = [], []
    for did in grid:
        d = by_id[did]
        if rng.random() < min(0.6, d["dnf"] + _INCIDENT + dnf_extra):   # crash / mechanical / spin
            retired.append(did)
            continue
        pen = rng.randint(4, 9) if rng.random() < _TIME_PEN else 0
        gamble = 0
        if wet and rng.random() < 0.12:       # tyre-call gamble: usually wrong, sometimes genius
            gamble = rng.randint(3, 10) if rng.random() < 0.6 else -rng.randint(3, 8)
        score = _GRID_W * pos[did] + (1 - _GRID_W) * d["race"] + rng.gauss(0, sigma) + pen + gamble
        finishers.append((score, did))
    finishers.sort()
    rng.shuffle(retired)
    return [did for _, did in finishers] + retired


def _award(order, points_table):
    return {did: points_table[i] for i, did in enumerate(order) if i < len(points_table)}


def _sim_one_season(profiles, remaining, rng):
    """Play out the remaining schedule once. Returns final driver points, the
    per-driver season tallies, AND per-race (round, pole, winner) so the futures
    board can price each individual GP's pole + winner markets."""
    drivers = list(profiles.values())
    pts = {d["id"]: d["points"] for d in drivers}
    wins = defaultdict(int); poles = defaultdict(int); podiums = defaultdict(int)
    race_out = []
    for race in remaining:
        quali = _sim_quali(drivers, rng)
        pole_id = quali[0]                    # pole = fastest in qualifying
        poles[pole_id] += 1
        wet = rng.random() < race.get("wet_prob", 0.0)   # rain rolled from circuit climate
        if race["sprint"]:
            # Sprint: a second (noisier) shootout off the same quali pace.
            sgrid = _apply_grid_penalties(_sim_quali(drivers, rng), rng)
            sorder = _sim_race(drivers, sgrid, rng, wet=wet)
            for did, p in _award(sorder, SPRINT_POINTS).items():
                pts[did] += p
        grid = _apply_grid_penalties(quali, rng)   # engine penalties hit the start
        order = _sim_race(drivers, grid, rng, wet=wet)
        wins[order[0]] += 1
        for did in order[:3]:
            podiums[did] += 1
        for did, p in _award(order, F1_POINTS).items():
            pts[did] += p
        race_out.append((race["round"], pole_id, order[0]))
    return pts, wins, poles, podiums, race_out


def sim_f1(n=2000, seed=None):
    """Monte-Carlo the rest of the F1 season. Returns per-driver title odds,
    expected points/wins/poles/podiums, and the constructors' championship."""
    profiles = f1_profiles()
    remaining = f1_remaining()
    if not profiles:
        return None
    rng = random.Random(seed)
    con_of = {d["id"]: d["constructor"] for d in profiles.values()}
    name_of = {d["id"]: d["name"] for d in profiles.values()}
    team_of = {d["id"]: d["constructor"] for d in profiles.values()}
    champ = defaultdict(int); con_champ = defaultdict(int)
    pts_sum = defaultdict(float)
    w_sum = defaultdict(float); pole_sum = defaultdict(float); pod_sum = defaultdict(float)
    # Per-race winner/pole tallies, keyed by round.
    race_pole = defaultdict(lambda: defaultdict(int))
    race_win = defaultdict(lambda: defaultdict(int))

    def one(_):
        return _sim_one_season(profiles, remaining, random.Random(rng.random()))

    with ThreadPoolExecutor(max_workers=8) as ex:
        results = ex.map(one, range(n))
        for pts, wins, poles, podiums, race_out in results:
            champ[max(pts, key=pts.get)] += 1
            con_pts = defaultdict(float)
            for did, p in pts.items():
                con_pts[con_of[did]] += p
                pts_sum[did] += p
                w_sum[did] += wins.get(did, 0)
                pole_sum[did] += poles.get(did, 0)
                pod_sum[did] += podiums.get(did, 0)
            con_champ[max(con_pts, key=con_pts.get)] += 1
            for rnd, pole_id, win_id in race_out:
                race_pole[rnd][pole_id] += 1
                race_win[rnd][win_id] += 1

    def field(counter):
        return [{"name": name_of[did], "team": team_of[did], "pct": round(100 * c / n, 1)}
                for did, c in sorted(counter.items(), key=lambda kv: -kv[1]) if c][:14]
    races = [{"round": r["round"], "name": r["name"], "sprint": r["sprint"],
              "circuit": r.get("circuit"), "wet_prob": r.get("wet_prob"),
              "avg_wind": r.get("avg_wind"),
              "pole": field(race_pole[r["round"]]), "winner": field(race_win[r["round"]])}
             for r in remaining]

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
            "drivers": drivers, "constructors": constructors, "races": races}


# ---- NASCAR Cup: pace + points from weekend feeds, then the playoff bracket ---
NASCAR_BASE = "https://cf.nascar.com/cacher"
# Finish points: win 40, then 2nd=35 and -1 per spot. Stage points are folded in
# approximately via the real playoff-point totals we read from the feeds.
def _cup_points(pos):
    if pos <= 1:
        return 40
    return max(1, 37 - pos)


def nascar_state(year=None, series=1):
    """Current Cup standings + pace from this season's completed points races:
    {driver_id: {name, points, playoff_points, wins, race_pace, dnf, starts}}."""
    import datetime
    year = year or datetime.date.today().year

    def build():
        today = datetime.date.today().isoformat()
        rl = racing._get_json(f"{NASCAR_BASE}/{year}/{series}/race_list_basic.json")
        pts_races = sorted((r for r in rl if r.get("race_type_id") == 1),
                           key=lambda r: r.get("race_date", ""))
        done = [r for r in pts_races if (r.get("race_date") or "")[:10] < today]

        def feed(r):
            try:
                d = racing._get_json(
                    f"{NASCAR_BASE}/{year}/{series}/{r['race_id']}/weekend-feed.json")
                return d["weekend_race"][0]["results"]
            except Exception:
                return []
        with ThreadPoolExecutor(max_workers=6) as ex:
            all_res = list(ex.map(feed, done))
        st = {}
        for res in all_res:
            for x in res:
                did = x["driver_id"]
                s = st.setdefault(did, {"name": x.get("driver_fullname", ""), "points": 0,
                                        "playoff_points": 0, "wins": 0, "fins": [], "dnf": 0, "starts": 0})
                s["points"] += x.get("points_earned", 0)
                s["playoff_points"] += x.get("playoff_points_earned", 0)
                s["starts"] += 1
                fp = x.get("finishing_position", 35)
                if fp == 1:
                    s["wins"] += 1
                if (x.get("finishing_status") or "Running") == "Running":
                    s["fins"].append(fp)
                else:
                    s["dnf"] += 1
        out = {}
        prior, k = 20.0, 5.0      # regress pace toward midpack so a part-timer with
        for did, s in st.items():  # a couple of lucky finishes isn't a fake contender
            if s["starts"] < 2:
                continue
            fins = s["fins"]
            pace = (sum(fins) + k * prior) / (len(fins) + k)
            out[did] = {"id": did, "name": s["name"], "points": s["points"],
                        "playoff_points": s["playoff_points"], "wins": s["wins"],
                        "race_pace": pace,
                        "dnf": min(0.30, s["dnf"] / s["starts"]), "starts": s["starts"]}
        import race_weather
        remaining = []
        for r in pts_races[len(done):]:
            clim = race_weather.nascar_climate(r.get("track_name", ""),
                                               (r.get("race_date") or "")[:10]) or {}
            remaining.append({"name": r.get("race_name", ""), "track": r.get("track_name"),
                              "wet_prob": clim.get("wet_prob", 0.10),
                              "avg_wind": clim.get("avg_wind")})
        return {"drivers": out, "n_points_races": len(pts_races), "n_done": len(done),
                "remaining": remaining}
    return racing._cached(("nascar_state", year, series), 6 * 3600, build) or {}


_SIGMA_CUP = 7.5     # Cup field is flat (pole win rate low) -> high race variance


def _round_of(idx):
    """Playoff round for the i-th points race of a 36-race Cup season (1-indexed)."""
    if idx <= 26:
        return "regular"
    if idx <= 29:
        return "ro16"
    if idx <= 32:
        return "ro12"
    if idx <= 35:
        return "ro8"
    return "final"


def _sim_cup_race(drivers, rng, wet=False):
    """Finishing order (driver ids). Pace + large variance (flat field) plus
    NASCAR's random events: 'the big one' multi-car wreck, per-car incidents/
    mechanicals, and penalties. RAIN makes it wilder — a bigger wreck chance and
    a possible rain-shortened/called race (higher variance, track-position wins)."""
    sigma = _SIGMA_CUP * (1.35 if wet else 1.0)
    big_p = 0.20 if wet else 0.10
    big_one = set()
    if rng.random() < big_p:                   # multi-car wreck (more likely in the wet)
        big_one = set(rng.sample([d["id"] for d in drivers],
                                 min(len(drivers), rng.randint(3, 7))))
    dnf_extra = 0.05 if wet else 0.0
    fin, out = [], []
    for d in drivers:
        if d["id"] in big_one or rng.random() < min(0.45, d["dnf"] + _INCIDENT + dnf_extra):
            out.append(d["id"])
        else:
            pen = rng.randint(5, 14) if rng.random() < _TIME_PEN else 0   # penalty -> lose spots
            fin.append((d["race_pace"] + rng.gauss(0, sigma) + pen, d["id"]))
    fin.sort()
    rng.shuffle(out)
    return [did for _, did in fin] + out


def _sim_nascar_season(drivers, phases, schedule, rng):
    """One season forward through the remaining schedule + the playoff bracket.
    `schedule` is the ordered remaining race list ({name, wet_prob}), so per-race
    pole/winner are logged and rain events fire per the track's climate."""
    pts = {d["id"]: d["points"] for d in drivers}
    wins = {d["id"]: d["wins"] for d in drivers}
    ppts = {d["id"]: d["playoff_points"] for d in drivers}
    season_wins = defaultdict(int); top5 = defaultdict(int); top10 = defaultdict(int)
    poles = defaultdict(int)
    race_iter = iter(schedule)
    race_out = []

    def run_race():
        r = next(race_iter, None)
        wet = bool(r) and rng.random() < (r.get("wet_prob") or 0.0)
        # Single-lap qualifying sets the pole (NASCAR's flat field -> wide spread).
        pole = min(drivers, key=lambda d: d["race_pace"] + rng.gauss(0, _SIGMA_CUP * 0.7))
        poles[pole["id"]] += 1
        order = _sim_cup_race(drivers, rng, wet=wet)
        for pos, did in enumerate(order, 1):
            pts[did] += _cup_points(pos)
            if pos <= 5:
                top5[did] += 1
            if pos <= 10:
                top10[did] += 1
        season_wins[order[0]] += 1
        if r is not None:
            race_out.append((r["name"], pole["id"], order[0]))
        return order

    for _ in range(phases.get("regular", 0)):
        w = run_race()[0]
        wins[w] += 1; ppts[w] += 5            # a win = playoff lock + playoff points

    # Seed the 16-car playoff field: race winners first (by playoff pts), then fill
    # the rest on regular-season points.
    field = sorted((d for d in drivers if wins[d["id"]] > 0),
                   key=lambda d: (ppts[d["id"]], pts[d["id"]]), reverse=True)
    if len(field) < 16:
        rest = sorted((d for d in drivers if wins[d["id"]] == 0),
                      key=lambda d: pts[d["id"]], reverse=True)
        field += rest[:16 - len(field)]
    field = field[:16]
    made_playoffs = {d["id"] for d in field}

    survivors = field
    base = 2000
    for rnd, advance_to in (("ro16", 12), ("ro12", 8), ("ro8", 4)):
        if not phases.get(rnd) or not survivors:
            continue
        rpts = {d["id"]: base + ppts[d["id"]] for d in survivors}
        auto = set()
        for _ in range(phases[rnd]):
            order = run_race()
            for pos, did in enumerate(order, 1):
                if did in rpts:
                    rpts[did] += _cup_points(pos)
            if order[0] in rpts:
                auto.add(order[0])            # a round win auto-advances
        survivors = sorted(survivors, key=lambda d: (d["id"] in auto, rpts[d["id"]]),
                           reverse=True)[:advance_to]
        base += 1000

    if phases.get("final") and survivors:
        finalists = {d["id"] for d in survivors[:4]}
        order = run_race()
        champ = next((did for did in order if did in finalists), survivors[0]["id"])
    else:
        champ = max(survivors, key=lambda d: pts[d["id"]])["id"] if survivors \
            else max(pts, key=pts.get)
    return champ, made_playoffs, season_wins, top5, top10, pts, poles, race_out


def sim_nascar(n=2000, year=None, seed=None):
    """Monte-Carlo the rest of the Cup season through the playoff bracket."""
    state = nascar_state(year)
    drivers_map = (state or {}).get("drivers") or {}
    if not drivers_map:
        return None
    drivers = list(drivers_map.values())
    name_of = {d["id"]: d["name"] for d in drivers}
    n_done = state["n_done"]
    phases = defaultdict(int)
    for idx in range(n_done + 1, state["n_points_races"] + 1):
        phases[_round_of(idx)] += 1
    schedule = state.get("remaining") or []
    rng = random.Random(seed)

    champ = defaultdict(int); po = defaultdict(int)
    pts_sum = defaultdict(float); w_sum = defaultdict(float)
    t5 = defaultdict(float); t10 = defaultdict(float); pole_sum = defaultdict(float)
    race_pole = defaultdict(lambda: defaultdict(int))
    race_win = defaultdict(lambda: defaultdict(int))

    def one(_):
        return _sim_nascar_season(drivers, phases, schedule, random.Random(rng.random()))
    with ThreadPoolExecutor(max_workers=8) as ex:
        for c, made, sw, top5, top10, pts, poles, race_out in ex.map(one, range(n)):
            champ[c] += 1
            for did in made:
                po[did] += 1
            for d in drivers:
                did = d["id"]
                pts_sum[did] += pts[did]; w_sum[did] += sw.get(did, 0)
                t5[did] += top5.get(did, 0); t10[did] += top10.get(did, 0)
                pole_sum[did] += poles.get(did, 0)
            for nm, pole_id, win_id in race_out:
                race_pole[nm][pole_id] += 1
                race_win[nm][win_id] += 1

    out = []
    for d in drivers:
        did = d["id"]
        out.append({"id": did, "name": d["name"], "points_now": d["points"],
                    "wins_now": d["wins"], "title_pct": round(100 * champ[did] / n, 1),
                    "playoff_pct": round(100 * po[did] / n, 1),
                    "proj_points": round(pts_sum[did] / n), "exp_wins": round(w_sum[did] / n, 1),
                    "exp_top5": round(t5[did] / n, 1), "exp_top10": round(t10[did] / n, 1),
                    "exp_poles": round(pole_sum[did] / n, 1)})
    out.sort(key=lambda x: x["title_pct"], reverse=True)

    def field(counter):
        return [{"name": name_of[did], "pct": round(100 * c / n, 1)}
                for did, c in sorted(counter.items(), key=lambda kv: -kv[1]) if c][:16]
    races = [{"name": r["name"], "wet_prob": r.get("wet_prob"), "avg_wind": r.get("avg_wind"),
              "pole": field(race_pole[r["name"]]), "winner": field(race_win[r["name"]])}
             for r in schedule]
    return {"sport": "nascar", "n_sims": n,
            "races_left": sum(phases.values()), "drivers": out, "races": races}


