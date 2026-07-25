"""Deep full-season simulator for F1 (and NASCAR — see racing_sim_nascar pieces).

Mirrors the MLB deep engine for motorsport: we estimate each driver's qualifying
pace, race pace and reliability from this season's results, then for every
REMAINING weekend simulate qualifying (to set the grid / pole), the race (and the
sprint where the weekend has one), award championship points, and roll the season
forward. Over many simulated seasons we get each driver's title odds, expected
wins / poles / podiums, and the constructors' championship — to compare against
Kalshi and Polymarket futures.
"""

import math
import clock
import random
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import racing

ERGAST = "https://api.jolpi.ca/ergast/f1/current"


def _et_wallclock_to_utc(iso):
    """NASCAR's feed gives race times as an ET wall-clock ISO with no zone
    ('2026-07-20T18:00:00'). Convert to a UTC ISO (…Z) so the frontend renders
    the right local + ET times. Returns None if there's no usable time."""
    if not iso or len(iso) <= 10:
        return None
    try:
        import datetime as _dt
        from zoneinfo import ZoneInfo
        d = _dt.datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")
        u = d.replace(tzinfo=ZoneInfo("America/New_York")).astimezone(_dt.timezone.utc)
        return u.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return None
F1_POINTS = [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]       # race top 10
SPRINT_POINTS = [8, 7, 6, 5, 4, 3, 2, 1]              # sprint top 8

# Field-spread knobs. Tuned so the pole sits on a realistic share of wins and the
# title race isn't a coin flip.
_SIGMA_Q = 0.20     # qualifying variance, in SECONDS of lap time (quali is a
                    # gap-to-pole now); ~one-session spread for the same car
# Track position is worth a LOT in modern F1 -- passing is hard, so the grid
# largely locks the order. Calibrated (_GRID_W / _SIGMA_R) so the pole-sitter
# wins ~40% and podiums ~70% of dry races, matching reality (the old 0.40 / 3.1
# let fast cars slice through the field and gave the pole only a ~26% win rate).
_SIGMA_R = 2.0      # race variance, in finishing-position units
_GRID_W = 0.72      # how much the race result leans on grid vs raw race pace
# Random-event rates (per driver, per race).
_GRID_PEN = 0.045   # engine/gearbox grid penalty -> start from the back
_TIME_PEN = 0.05    # in-race time penalty -> drop ~4-9 places
_INCIDENT = 0.015   # flat crash/incident DNF on top of each driver's reliability


def _f1_circuit_type(name):
    """Bucket an F1 circuit by character. Street circuits punish mistakes and
    reward precision (qualifying-locked, hard to pass); power tracks reward the
    car's top end. Some drivers are demonstrably stronger on one kind, so we rate
    a per-type finish delta (heavily regressed — the season is young)."""
    t = (name or "").lower()
    if any(k in t for k in ("monaco", "marina bay", "singapore", "baku", "azerbaijan",
                            "jeddah", "miami", "las vegas", "vegas", "albert park", "melbourne")):
        return "street"
    if any(k in t for k in ("monza", "spa", "silverstone", "gilles villeneuve",
                            "montreal", "red bull ring", "austria")):
        return "power"
    return "standard"


# How locked a race is to the grid, by how hard the circuit is to OVERTAKE on --
# a different axis from circuit "type" above. Baku and Vegas are street circuits
# but overtake-heavy (long straights + DRS), while Monaco and Hungary are the truly
# grid-locked ones. Track position is worth far more at the processional tracks, so
# the pole-sitter/front row should convert much more often there than at pass-fests.
_F1_HARD_PASS = ("monaco", "monte carlo", "singapore", "marina bay",
                 "hungar", "zandvoort", "dutch", "imola", "emilia")
_F1_EASY_PASS = ("monza", "italian", "spa", "belgian", "baku", "azerbaijan",
                 "jeddah", "saudi", "las vegas", "vegas", "shanghai", "chinese",
                 "china", "bahrain", "sakhir", "red bull ring", "austria",
                 "spielberg", "interlagos", "brazil", "sao paulo", "americas",
                 "cota", "austin", "mexico", "hermanos")


def _f1_grid_weight(name):
    """Per-track grid dominance: ~0.84 where passing is near-impossible (Monaco),
    ~0.60 at the pass-fests (Monza/Baku), the calibrated 0.72 baseline elsewhere."""
    t = (name or "").lower()
    if any(k in t for k in _F1_HARD_PASS):
        return 0.84
    if any(k in t for k in _F1_EASY_PASS):
        return 0.60
    return _GRID_W


# Safety-car likelihood by circuit. Street/tight tracks (walls close, no run-off)
# throw a full SC far more often than fast, wide permanent circuits with gravel and
# tarmac run-off. A SC bunches the field and swings pit strategy -> a big shuffle,
# and it's one of the largest sources of real F1 variance and underdog results.
_F1_SC_HIGH = ("monaco", "monte carlo", "singapore", "marina bay", "baku",
               "azerbaijan", "jeddah", "saudi", "melbourne", "albert park",
               "las vegas", "vegas", "zandvoort", "dutch", "miami")
_F1_SC_LOW = ("bahrain", "sakhir", "barcelona", "catalunya", "spanish",
              "paul ricard", "france", "suzuka", "japan", "silverstone",
              "british", "americas", "cota", "austin", "mugello", "portimao")


def _f1_sc_prob(name):
    """Per-race probability of a full safety car. Street circuits ~0.72, fast open
    tracks ~0.22, ~0.42 elsewhere."""
    t = (name or "").lower()
    if any(k in t for k in _F1_SC_HIGH):
        return 0.72
    if any(k in t for k in _F1_SC_LOW):
        return 0.22
    return 0.42


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
        t_acc, t_w, t_n = defaultdict(lambda: defaultdict(float)), \
            defaultdict(lambda: defaultdict(float)), defaultdict(lambda: defaultdict(int))
        for i, r in enumerate(races):
            w = i + 1
            ctype = _f1_circuit_type((r.get("Circuit") or {}).get("circuitName")
                                     or r.get("raceName"))
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
                        p = int(res["position"])
                        fin_acc[did] += w * p; fin_w[did] += w
                        t_acc[did][ctype] += w * p; t_w[did][ctype] += w
                        t_n[did][ctype] += 1
                    except (ValueError, KeyError):
                        pass
                else:
                    dnf[did] += 1                                # reliability, handled separately
        # Early in a season (or for part-timers) a couple of results are pure
        # noise, so we shrink toward stable priors by sample size:
        #  - race pace -> avg grid (where you start strongly predicts the finish),
        #  - DNF rate  -> a field-typical ~9%.
        # A rookie's two lucky podiums no longer read as "finishes 1st on average."
        _FIN_K, _DNF_K, _TYPE_K = 6.0, 5.0, 2.5   # _TYPE_K light: circuit-type skill persists
        out = {}
        for did in starts:
            g = grid[did] or [13]
            avg_grid = sum(g) / len(g)
            raw_fin = fin_acc[did] / fin_w[did] if fin_w[did] else avg_grid
            avg_fin = (fin_w[did] * raw_fin + _FIN_K * avg_grid) / (fin_w[did] + _FIN_K)
            dnf_rate = (dnf[did] + _DNF_K * 0.09) / (starts[did] + _DNF_K)
            # Per-circuit-type pace: shrink the driver's in-type finish toward his
            # OWN race pace as an absolute estimate (not toward "no effect" added
            # onto the regressed base — that double-washes a specialist). Stored as
            # a delta the sim adds to avg_fin.
            by_type = {}
            for ct, acc in t_acc[did].items():
                raw_t = acc / t_w[did][ct]
                nt = t_n[did][ct]
                type_pace = (nt * raw_t + _TYPE_K * avg_fin) / (nt + _TYPE_K)
                by_type[ct] = round(type_pace - avg_fin, 2)
            # dnf is a flat-prior fallback; dnf_n/starts let f1_profiles re-pool
            # reliability by constructor (failures are a shared-car property).
            out[did] = {"avg_grid": avg_grid, "avg_fin": avg_fin, "race_by_type": by_type,
                        "dnf": dnf_rate, "dnf_n": dnf[did], "starts": starts[did]}
        return out
    return racing._cached(("f1_results",), 6 * 3600, build) or {}


def f1_type_skill(year=None, n_back=2):
    """Per-driver per-circuit-type skill delta, car-free, across recent F1 seasons.

    Same delta-of-deltas as NASCAR: for each season, (in-type avg finish − that
    season's overall avg finish), recency-weighted across seasons and regressed by
    in-type sample. The delta-of-deltas strips out the car a driver had each year,
    so a street/wet specialist keeps that skill even through a team change — and it
    gives a real sample where the current 5-race season has almost none."""
    import datetime
    year = year or clock.today_et().year
    base = ERGAST.rsplit("/", 1)[0]                  # .../ergast/f1

    def build():
        seasons = list(range(year - n_back, year + 1))
        per = defaultdict(lambda: defaultdict(lambda: {"type": defaultdict(list), "all": []}))
        for yr in seasons:
            races = []
            for off in (0, 100, 200, 300, 400):
                try:
                    d = racing._get_json(f"{base}/{yr}/results.json?limit=100&offset={off}")
                except Exception:
                    break
                rs_ = d.get("MRData", {}).get("RaceTable", {}).get("Races", [])
                races += rs_
                if len(rs_) < 100:
                    break
            for r in races:
                ct = _f1_circuit_type((r.get("Circuit") or {}).get("circuitName") or r.get("raceName"))
                for res in r.get("Results", []):
                    st = res.get("status", "")
                    if not (st == "Finished" or st.startswith("+")):
                        continue
                    try:
                        fp = int(res["position"])
                    except (ValueError, KeyError):
                        continue
                    did = res["Driver"]["driverId"]
                    per[did][yr]["type"][ct].append(fp)
                    per[did][yr]["all"].append(fp)
        sw = {yr: i + 1 for i, yr in enumerate(seasons)}
        _SK = 3.0
        out = {}
        for did, byseason in per.items():
            acc = defaultdict(float); wsum = defaultdict(float); nsum = defaultdict(int)
            for yr, data in byseason.items():
                allf = data["all"]
                if len(allf) < 3:
                    continue
                overall = sum(allf) / len(allf)
                for ct, fins in data["type"].items():
                    type_avg = sum(fins) / len(fins)
                    w = sw[yr] * len(fins)
                    acc[ct] += w * (type_avg - overall)
                    wsum[ct] += w
                    nsum[ct] += len(fins)
            out[did] = {ct: round((acc[ct] / wsum[ct]) * nsum[ct] / (nsum[ct] + _SK), 2)
                        for ct in acc if wsum[ct]}
        return out
    return racing._cached(("f1_type_skill", year, n_back), 7 * 86400, build) or {}


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
    """Per-driver RAW qualifying pace as {driver_id: {gap, n}}: a recency-weighted
    average gap to pole (seconds) and the number of sessions behind it. Far finer
    than grid position — it knows whether a P2 was 0.05s or 0.5s off pole — and the
    sample count lets f1_profiles pool small samples toward the car's pace."""
    def build():
        races = []
        for off in (0, 100):
            d = racing._get_json(f"{ERGAST}/qualifying.json?limit=100&offset={off}")
            rs = d.get("MRData", {}).get("RaceTable", {}).get("Races", [])
            races += rs
            if len(rs) < 5:
                break
        acc, wsum, cnt = defaultdict(float), defaultdict(float), defaultdict(int)
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
                cnt[did] += 1
        # Raw recency-weighted gap + session count; shrinkage happens in
        # f1_profiles, where the constructor (teammate) signal is available.
        return {did: {"gap": acc[did] / wsum[did], "n": cnt[did]} for did in acc}
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
        skill = f1_type_skill()                       # multi-year, car-free circuit-type skill
        standings = lst[0]["DriverStandings"]

        # Teammate pooling: a driver's car is shared evidence of pace, so each
        # constructor's "car pace" is the sample-weighted mean of its drivers' raw
        # quali gaps. A noisy driver then shrinks toward their CAR's pace (what the
        # teammate proves the car can do), not a blind midfield guess.
        car_acc, car_w = defaultdict(float), defaultdict(float)
        # Reliability is also a shared-car property (engine/gearbox failures hit
        # both cars), so pool DNFs by constructor too: tally team failures/starts.
        car_dnf, car_starts = defaultdict(int), defaultdict(int)
        for x in standings:
            did = x["Driver"]["driverId"]
            cid = x["Constructors"][-1]["constructorId"]
            g = gaps.get(did)
            if g:
                car_acc[cid] += g["gap"] * g["n"]; car_w[cid] += g["n"]
            f = form.get(did)
            if f:
                car_dnf[cid] += f.get("dnf_n", 0); car_starts[cid] += f.get("starts", 0)
        car_pace = {cid: car_acc[cid] / car_w[cid] for cid in car_acc}
        # Team DNF rate, itself shrunk toward the field-typical ~9%.
        car_rel = {cid: (car_dnf[cid] + 4 * 0.09) / (car_starts[cid] + 4)
                   for cid in car_starts}

        _QK = 4.0          # quali pseudo-sessions to shrink by
        _RELK = 6.0        # reliability pseudo-starts: pull a driver's DNF toward the car's
        out = {}
        for x in standings:
            dr = x["Driver"]; did = dr["driverId"]
            con = x["Constructors"][-1]; cid = con["constructorId"]
            f = form.get(did, {"avg_grid": 13.0, "avg_fin": 13.0, "dnf": 0.08,
                               "starts": 0, "race_by_type": {}})
            g = gaps.get(did, {"gap": 1.1, "n": 0})
            # prior = mostly the car's pace, with a dash of field-midfield so a
            # one-car team or a brand-new lineup can't run away on no evidence.
            prior = 0.7 * car_pace.get(cid, 0.9) + 0.3 * 0.9
            quali = (g["n"] * g["gap"] + _QK * prior) / (g["n"] + _QK)
            # Pool the driver's own failures toward their CAR's reliability, so one
            # mechanical DNF doesn't brand a driver fragile when his teammate has
            # been bulletproof (and a flaky engine taints both cars correctly).
            team_rel = car_rel.get(cid, 0.09)
            dnf = min(0.45, (f.get("dnf_n", 0) + _RELK * team_rel) / (f.get("starts", 0) + _RELK))
            out[did] = {
                "id": did, "name": f"{dr.get('givenName','')} {dr.get('familyName','')}".strip(),
                "code": dr.get("code") or dr.get("familyName", "")[:3].upper(),
                "constructor": con["name"], "constructor_id": cid,
                "points": float(x["points"]), "wins": int(x["wins"]),
                "position": int(x["position"]),
                # quali pace is a teammate-pooled gap-to-pole in SECONDS; race pace
                # stays position-scale (regressed toward grid in _f1_results).
                "quali": quali, "avg_grid": f["avg_grid"],
                "race": f["avg_fin"],
                # multi-year circuit-type skill primary, this-season as the fallback
                "race_by_type": {**f.get("race_by_type", {}), **skill.get(did, {})},
                "dnf": dnf,
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
        today = clock.today_et().isoformat()
        out = []
        for r in d["MRData"]["RaceTable"]["Races"]:
            if r.get("date", "") >= today:
                loc = (r.get("Circuit") or {}).get("Location") or {}
                clim = race_weather.climate(loc.get("lat"), loc.get("long"), r["date"]) or {}
                cir = (r.get("Circuit") or {}).get("circuitName")
                out.append({"round": int(r["round"]), "name": r["raceName"],
                            "sprint": "Sprint" in r, "circuit": cir,
                            "type": _f1_circuit_type(cir or r["raceName"]),
                            # Ergast gives race date + optional UTC start time.
                            "start": (f"{r['date']}T{r['time']}" if r.get("time") else None),
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


def _sim_race(drivers, grid, rng, wet=False, ctype="standard", grid_w=_GRID_W, sc_prob=0.0):
    """Finishing order (list of ids). Blends grid + race pace with variance, plus
    random events: DNFs (crash/mechanical + a flat incident chance) drop to the
    back, and time penalties cost positions. Circuit type nudges each driver by
    their per-type form (a street-circuit specialist gains on street tracks). A WET
    race is far more chaotic — more spins/DNFs, wider variance, and tyre-call
    gambles (the wrong-tyre fiasco) that can drop a frontrunner or vault a
    midfielder."""
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
        race_pace = d["race"] + d.get("race_by_type", {}).get(ctype, 0.0)
        score = grid_w * pos[did] + (1 - grid_w) * race_pace + rng.gauss(0, sigma) + pen + gamble
        finishers.append((score, did))
    # Safety car: the field bunches up (gaps erased) and pit-strategy luck under the
    # SC shuffles track position — a lucky window vaults a midfielder, an unlucky
    # just-pitted leader drops back. Widens the result: the front row keeps a smaller
    # edge and underdogs get a real shot, matching how street-circuit races behave.
    if finishers and rng.random() < sc_prob:
        sm = sum(s for s, _ in finishers) / len(finishers)
        shuffled = []
        for s, did in finishers:
            s = sm + 0.62 * (s - sm)               # bunched behind the safety car
            s += rng.gauss(0, sigma * 0.9)          # restart chaos
            if rng.random() < 0.35:                 # pit-window luck (good or bad)
                s += rng.uniform(-7, 7)
            shuffled.append((s, did))
        finishers = shuffled
    finishers.sort()
    rng.shuffle(retired)
    return [did for _, did in finishers] + retired


def _award(order, points_table):
    return {did: points_table[i] for i, did in enumerate(order) if i < len(points_table)}


def _resolve_grid_order(fixed_grid, drivers):
    """Resolve a real {normalized name: start pos} grid to a driver-id start
    order (unmatched drivers start at the back). None if too few match to trust."""
    if not fixed_grid:
        return None
    id_by_norm = {}
    for d in drivers:
        nm = racing.norm_name(d["name"])
        id_by_norm[nm] = d["id"]
        last = nm.split()[-1] if nm.split() else nm
        id_by_norm.setdefault(last, d["id"])
    pairs = []
    for nm, pos in fixed_grid.items():
        did = id_by_norm.get(nm) or (id_by_norm.get(nm.split()[-1]) if nm.split() else None)
        if did is not None:
            pairs.append((pos, did))
    if len(pairs) < 8:
        return None
    pairs.sort()
    seen, order = set(), []
    for _pos, did in pairs:
        if did not in seen:
            order.append(did); seen.add(did)
    for d in drivers:
        if d["id"] not in seen:
            order.append(d["id"])
    return order


def _sim_one_season(profiles, remaining, rng, first_grid=None):
    """Play out the remaining schedule once. Returns final driver points, the
    per-driver season tallies, AND per-race (round, pole, winner) so the futures
    board can price each individual GP's pole + winner markets. When `first_grid`
    (a real driver-id start order) is given, the NEXT race starts from it instead
    of a simulated qualifying -- real quali is done, so its pole is decided."""
    drivers = list(profiles.values())
    pts = {d["id"]: d["points"] for d in drivers}
    wins = defaultdict(int); poles = defaultdict(int); podiums = defaultdict(int)
    race_out = []
    for idx, race in enumerate(remaining):
        wet = rng.random() < race.get("wet_prob", 0.0)   # rain rolled from circuit climate
        ctype = race.get("type", "standard")
        gw = _f1_grid_weight(race.get("name", ""))        # how grid-locked this track is
        sc_p = _f1_sc_prob(race.get("name", ""))          # safety-car likelihood
        if idx == 0 and first_grid:
            grid = list(first_grid); pole_id = grid[0]   # real qualifying result
        else:
            quali = _sim_quali(drivers, rng)
            pole_id = quali[0]                            # pole = fastest in qualifying
            grid = _apply_grid_penalties(quali, rng)      # engine penalties hit the start
        poles[pole_id] += 1
        if race["sprint"]:
            # Sprint: a second (noisier) shootout off the same quali pace.
            sgrid = _apply_grid_penalties(_sim_quali(drivers, rng), rng)
            sorder = _sim_race(drivers, sgrid, rng, wet=wet, ctype=ctype, grid_w=gw, sc_prob=sc_p)
            for did, p in _award(sorder, SPRINT_POINTS).items():
                pts[did] += p
        order = _sim_race(drivers, grid, rng, wet=wet, ctype=ctype, grid_w=gw, sc_prob=sc_p)
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

    # Condition the NEXT race on the REAL grid when qualifying is already done
    # (the last-quali race matches remaining[0]); otherwise re-sim qualifying.
    first_grid = None
    try:
        if remaining:
            g = racing.get_f1_grid()
            if g and racing.norm_name(g.get("race", "")) == racing.norm_name(remaining[0]["name"]):
                first_grid = _resolve_grid_order(g.get("grid"), list(profiles.values()))
    except Exception:
        first_grid = None
    grid_conditioned = bool(first_grid)

    def one(_):
        return _sim_one_season(profiles, remaining, random.Random(rng.random()),
                               first_grid=first_grid)

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
              "avg_wind": r.get("avg_wind"), "start": r.get("start"),
              # The first remaining race is conditioned on the real grid when
              # qualifying is already done (its pole is then the actual pole).
              "grid_set": bool(first_grid) and i == 0,
              "pole": field(race_pole[r["round"]]), "winner": field(race_win[r["round"]])}
             for i, r in enumerate(remaining)]

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
            "next_grid_conditioned": grid_conditioned,
            "drivers": drivers, "constructors": constructors, "races": races}


# ---- NASCAR Cup: pace + points from weekend feeds, then the playoff bracket ---
NASCAR_BASE = "https://cf.nascar.com/cacher"


# Canonical classifier lives in racing.py (the win model + DFS grid share it).
_nascar_track_type = racing.nascar_track_type


def nascar_type_skill(year=None, series=1, n_back=2):
    """Per-driver per-track-type SKILL delta, isolated from the car, across recent
    seasons (current + n_back prior).

    Track-type skill (a road ace's road craft) is persistent, but a single season
    has too few road races to measure it — so we look back. The trap is that a raw
    finish from two years ago reflects the CAR the driver had then, not just skill.
    We avoid it with a delta-of-deltas: for each season we take (in-type avg finish
    − that season's overall avg finish) — how much better than the driver's OWN
    baseline on that track type — then recency-weight across seasons and regress by
    the total in-type sample. The result is applied to the CURRENT base pace."""
    import datetime
    year = year or clock.today_et().year

    def build():
        seasons = list(range(year - n_back, year + 1))
        today = clock.today_et().isoformat()
        # per[did][season] = {"type": {tt: [finishes]}, "all": [finishes]}
        per = defaultdict(lambda: defaultdict(lambda: {"type": defaultdict(list), "all": []}))
        # led[did][season][tt] = [race laps-led shares] — real dominator history
        led = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
        for yr in seasons:
            try:
                rl = racing._get_json(f"{NASCAR_BASE}/{yr}/{series}/race_list_basic.json")
            except Exception:
                continue
            pts = sorted((r for r in rl if r.get("race_type_id") == 1),
                         key=lambda r: r.get("race_date", ""))
            done = [r for r in pts if (r.get("race_date") or "")[:10] < today]

            def feed(r):
                try:
                    d = racing._get_json(
                        f"{NASCAR_BASE}/{yr}/{series}/{r['race_id']}/weekend-feed.json")
                    return r, d["weekend_race"][0]["results"]
                except Exception:
                    return r, []
            with ThreadPoolExecutor(max_workers=6) as ex:
                for r, res in ex.map(feed, done):
                    tt = _nascar_track_type(r.get("track_name", ""))
                    # Laps-led share of THIS race (counts DNFs too — leading early
                    # then crashing is still dominating): the raw dominator signal
                    # the DFS cares about, straight from the timing data.
                    race_led = sum((x.get("laps_led") or 0) for x in res) or 1
                    for x in res:
                        frac = (x.get("laps_led") or 0) / race_led
                        led[x["driver_id"]][yr][tt].append(frac)
                        if (x.get("finishing_status") or "Running") != "Running":
                            continue                       # DNFs handled by the base/reliability
                        fp = x.get("finishing_position", 35)
                        per[x["driver_id"]][yr]["type"][tt].append(fp)
                        per[x["driver_id"]][yr]["all"].append(fp)
        sw = {yr: i + 1 for i, yr in enumerate(seasons)}   # recency weight, current highest
        # Light regression: an elite specialist's delta is HUGE and consistent
        # (SVG runs ~15 spots better on road courses); 2.5 pseudo-races shaved a
        # third off it and made the master a mid-packer. 1.2 keeps small samples
        # honest while letting a proven specialist keep his edge.
        _SK = 1.2
        out = {}
        for did, byseason in per.items():
            acc = defaultdict(float); wsum = defaultdict(float); nsum = defaultdict(int)
            for yr, data in byseason.items():
                allf = data["all"]
                if len(allf) < 3:
                    continue
                overall = sum(allf) / len(allf)
                for tt, fins in data["type"].items():
                    type_avg = sum(fins) / len(fins)
                    w = sw[yr] * len(fins)                  # recency * sample
                    acc[tt] += w * (type_avg - overall)     # delta-of-deltas (car-free)
                    wsum[tt] += w
                    nsum[tt] += len(fins)
            delta = {tt: round((acc[tt] / wsum[tt]) * nsum[tt] / (nsum[tt] + _SK), 2)
                     for tt in acc if wsum[tt]}
            # Real dominator propensity: recency-weighted average share of a
            # race's laps this driver led, per track type, lightly regressed
            # toward "leads nothing" by sample (3 pseudo-races).
            lacc = defaultdict(float); lw = defaultdict(float); ln = defaultdict(int)
            for yr, by_tt in led[did].items():
                for tt, fracs in by_tt.items():
                    w = sw[yr]
                    for f in fracs:
                        lacc[tt] += w * f; lw[tt] += w; ln[tt] += 1
            led_share = {tt: round((lacc[tt] / lw[tt]) * ln[tt] / (ln[tt] + 3.0), 3)
                         for tt in lacc if lw[tt]}
            out[did] = {"delta": delta, "led": led_share}
        return out
    return racing._cached(("nascar_type_skill", year, series, n_back, 2), 7 * 86400, build) or {}


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
    year = year or clock.today_et().year

    def build():
        today = clock.today_et().isoformat()
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
        for i, (r, res) in enumerate(zip(done, all_res)):   # chronological, race-paired
            w = i + 1                              # linear recency weight: catch form/upgrades
            tt = _nascar_track_type(r.get("track_name", ""))
            for x in res:
                did = x["driver_id"]
                s = st.setdefault(did, {"name": x.get("driver_fullname", ""), "points": 0,
                                        "playoff_points": 0, "wins": 0, "n_fin": 0,
                                        "fin_acc": 0.0, "fin_w": 0.0, "dnf": 0, "starts": 0,
                                        "t_acc": {}, "t_w": {}, "t_n": {}})
                s["points"] += x.get("points_earned", 0)
                s["playoff_points"] += x.get("playoff_points_earned", 0)
                s["starts"] += 1
                fp = x.get("finishing_position", 35)
                if fp == 1:
                    s["wins"] += 1
                if (x.get("finishing_status") or "Running") == "Running":
                    s["n_fin"] += 1
                    s["fin_acc"] += w * fp; s["fin_w"] += w
                    s["t_acc"][tt] = s["t_acc"].get(tt, 0.0) + w * fp
                    s["t_w"][tt] = s["t_w"].get(tt, 0.0) + w
                    s["t_n"][tt] = s["t_n"].get(tt, 0) + 1
                else:
                    s["dnf"] += 1
        # Per-track-type SKILL deltas from multiple recent seasons (current + 2
        # prior), car-free (delta-of-deltas). This gives a road ace a real road
        # sample instead of this season's two races, so a specialist isn't washed
        # out. Current-season t_acc is the fallback when multi-year is unavailable.
        skill = nascar_type_skill(year, series)
        out = {}
        prior, k = 20.0, 5.0      # regress pace toward midpack so a part-timer with
        _TK = 2.0                 # per-category pseudo-races for the single-season fallback
        for did, s in st.items():  # a couple of lucky finishes isn't a fake contender
            if s["starts"] < 2:
                continue
            # recency-weighted average finish, then blend toward midpack by how
            # many races we actually have (k pseudo-races), keeping regression in
            # race-count units even though weights sum large.
            raw = s["fin_acc"] / s["fin_w"] if s["fin_w"] else prior
            n_eff = s["n_fin"]
            pace = (n_eff * raw + k * prior) / (n_eff + k)
            sk = skill.get(did) or {}
            by_type = dict(sk.get("delta") or {})  # multi-year skill (delta to add to pace)
            for tt, acc in s["t_acc"].items():     # fill any gap from this season only
                if tt in by_type:
                    continue
                raw_t = acc / s["t_w"][tt]
                n_t = s["t_n"][tt]
                type_pace = (n_t * raw_t + _TK * pace) / (n_t + _TK)
                by_type[tt] = round(type_pace - pace, 2)
            out[did] = {"id": did, "name": s["name"], "points": s["points"],
                        "playoff_points": s["playoff_points"], "wins": s["wins"],
                        "race_pace": pace, "pace_by_type": by_type,
                        "led_by_type": sk.get("led") or {},
                        "dnf": min(0.30, s["dnf"] / s["starts"]), "starts": s["starts"]}
        import race_weather
        remaining = []
        for r in pts_races[len(done):]:
            clim = race_weather.nascar_climate(r.get("track_name", ""),
                                               (r.get("race_date") or "")[:10]) or {}
            remaining.append({"name": r.get("race_name", ""), "track": r.get("track_name"),
                              "type": _nascar_track_type(r.get("track_name", "")),
                              "laps": r.get("scheduled_laps"),
                              "start": _et_wallclock_to_utc(r.get("race_date")),
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


def _sim_cup_race(drivers, rng, wet=False, ttype="intermediate"):
    """Finishing order (driver ids). Pace + large variance (flat field) plus
    NASCAR's random events: 'the big one' multi-car wreck, per-car incidents/
    mechanicals, and penalties. Track TYPE reshapes it: a superspeedway is a
    drafting lottery (everyone bunched, huge wreck risk, anyone can win), road
    courses are more skill-deterministic. RAIN makes it wilder — a bigger wreck
    chance and a possible rain-shortened/called race (track-position wins)."""
    sigma = _SIGMA_CUP * (1.35 if wet else 1.0)
    big_p = 0.20 if wet else 0.10
    compress = 1.0                              # how much pace separates the field
    if ttype == "superspeedway":
        sigma *= 1.6; big_p = max(big_p, 0.32); compress = 0.45   # pack-racing lottery
    elif ttype == "road":
        sigma *= 0.72                           # skill shows through, fewer wrecks
    elif ttype == "short":
        sigma *= 0.92
    big_one = set()
    # 'The big one' — a superspeedway can have several in one race (Daytona/Talladega
    # routinely see 2-3 multi-car wrecks), so roll it multiple times there; elsewhere
    # a wreck is a single event. Each collects a random pack of cars.
    n_wrecks = rng.choice((1, 1, 2, 2, 3)) if ttype == "superspeedway" else 1
    ids = [d["id"] for d in drivers]
    for _ in range(n_wrecks):
        if rng.random() < big_p:               # multi-car wreck (more likely in the wet)
            big_one |= set(rng.sample(ids, min(len(ids), rng.randint(3, 7))))
    dnf_extra = 0.05 if wet else 0.0
    mean_pace = sum(d["race_pace"] for d in drivers) / len(drivers)
    fin, out = [], []
    for d in drivers:
        if d["id"] in big_one or rng.random() < min(0.45, d["dnf"] + _INCIDENT + dnf_extra):
            out.append(d["id"])
        else:
            pen = rng.randint(5, 14) if rng.random() < _TIME_PEN else 0   # penalty -> lose spots
            tdel = d.get("pace_by_type", {}).get(ttype, 0.0)
            pace = d["race_pace"] + tdel
            if compress < 1.0:                  # superspeedway: bunch the field toward the mean
                pace = mean_pace + compress * (pace - mean_pace)
            # A true type specialist isn't just faster — he's CONSISTENT on the
            # surface he masters (fewer mistakes to make up). Trim his race noise
            # so a dominant road ace converts pace into wins at a realistic rate.
            sig_d = sigma * (0.85 if tdel <= -5.0 else 1.0)
            fin.append((pace + rng.gauss(0, sig_d) + pen, d["id"]))
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
        ttype = (r or {}).get("type", "intermediate")
        wet = bool(r) and rng.random() < (r.get("wet_prob") or 0.0)
        # Single-lap qualifying sets the pole (NASCAR's flat field -> wide spread);
        # superspeedway qualifying is its own lottery, so the spread is even wider.
        psig = _SIGMA_CUP * 0.7 * (1.5 if ttype == "superspeedway" else 1.0)
        pole = min(drivers, key=lambda d: d["race_pace"]
                   + d.get("pace_by_type", {}).get(ttype, 0.0) + rng.gauss(0, psig))
        poles[pole["id"]] += 1
        order = _sim_cup_race(drivers, rng, wet=wet, ttype=ttype)
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
              "start": r.get("start"),
              "pole": field(race_pole[r["name"]]), "winner": field(race_win[r["name"]])}
             for r in schedule]
    return {"sport": "nascar", "n_sims": n,
            "races_left": sum(phases.values()), "drivers": out, "races": races}




# ---- Single-race finish distribution for DFS ------------------------------
import threading as _threading
import time as _time
_nr_inflight = set()


def next_race_profile(sport):
    """Cached next-race finish profile for the DFS feed. NON-BLOCKING: returns the
    cached profile if it's fresh, otherwise kicks a background compute and returns
    None for now (the DFS falls back to its salary/form proxy and upgrades to the
    sim automatically on the next build). Recomputed every few hours."""
    key = ("next_race", sport)
    hit = racing._form_cache.get(key)
    if hit and (_time.time() - hit[0]) < 3 * 3600 and hit[1] is not None:
        return hit[1]
    if sport not in _nr_inflight:
        _nr_inflight.add(sport)

        def _bg():
            try:
                racing._cached(key, 3 * 3600, lambda: next_race_sim(sport, 2000))
            finally:
                _nr_inflight.discard(sport)
        _threading.Thread(target=_bg, daemon=True).start()
    return None


def _f1_practice_gaps():
    """{normalized driver name: gap-to-fastest in seconds} from the LATEST
    completed practice session of the CURRENT GP weekend (OpenF1). Practice pace
    at this circuit is the best pre-qualifying signal there is. None when the
    freshest practice is stale (>4 days — i.e. from a finished weekend)."""
    import datetime as _dt

    def build():
        year = clock.today_et().year
        sessions = racing._get_json(f"https://api.openf1.org/v1/sessions?year={year}")
        now = _dt.datetime.now(_dt.timezone.utc)
        done = []
        for s in sessions:
            if s.get("session_type") != "Practice":
                continue
            try:
                t = _dt.datetime.fromisoformat(s["date_start"].replace("Z", "+00:00"))
            except Exception:
                continue
            if t < now:
                done.append((t, s))
        if not done:
            return None
        done.sort()
        t, s = done[-1]
        if (now - t).total_seconds() > 4 * 86400:
            return None                        # last practice was a past weekend
        key = s["session_key"]
        drivers = racing._get_json(f"https://api.openf1.org/v1/drivers?session_key={key}")
        name_of = {d["driver_number"]: d.get("full_name") or d.get("broadcast_name")
                   for d in drivers}
        laps = racing._get_json(f"https://api.openf1.org/v1/laps?session_key={key}")
        best = {}
        for l in laps:
            dur = l.get("lap_duration")
            dn = l.get("driver_number")
            if dur and dn in name_of:
                best[dn] = min(best.get(dn, 1e9), dur)
        if len(best) < 10:
            return None
        fastest = min(best.values())
        out = {}
        for dn, t in best.items():
            nm = racing.norm_name(name_of[dn])
            out[nm] = round(t - fastest, 3)
            if nm.split():
                out.setdefault(nm.split()[-1], out[nm])   # last-name alias
        return out
    return racing._cached(("f1_practice",), 1800, build)


def _longrun_from_session(key):
    """{normalized name: race-pace gap-to-fastest (s)} from ONE practice session's
    long runs. For each driver we take their best sustained stint (>=5 laps on one
    set of tyres), drop the out-lap/warm-up lap and traffic-spoiled laps (>4% off
    the run median), and average the rest — the closest public read on real race
    pace, which single-lap qualifying pace completely misses. None if too sparse."""
    import statistics
    try:
        drivers = racing._get_json(f"https://api.openf1.org/v1/drivers?session_key={key}")
        stints = racing._get_json(f"https://api.openf1.org/v1/stints?session_key={key}")
        laps = racing._get_json(f"https://api.openf1.org/v1/laps?session_key={key}")
    except Exception:
        return None
    name_of = {d["driver_number"]: (d.get("full_name") or d.get("broadcast_name"))
               for d in drivers}
    lap_t = {}
    for l in laps:
        if l.get("lap_duration") and not l.get("is_pit_out_lap"):
            lap_t[(l.get("driver_number"), l.get("lap_number"))] = l["lap_duration"]
    best = {}
    for s in stints:
        dn, a, b = s.get("driver_number"), s.get("lap_start"), s.get("lap_end")
        if not (dn and a and b) or b - a + 1 < 5:
            continue
        ts = [lap_t[(dn, ln)] for ln in range(a + 1, b + 1) if (dn, ln) in lap_t]  # skip warm-up
        if len(ts) < 4:
            continue
        med = statistics.median(ts)
        clean = [t for t in ts if t <= med * 1.04]        # drop traffic/lift laps
        if len(clean) < 4:
            continue
        m = sum(clean) / len(clean)
        if dn not in best or m < best[dn]:
            best[dn] = m
    if len(best) < 10:
        return None
    fastest = min(best.values())
    out = {}
    for dn, v in best.items():
        nm = racing.norm_name(name_of.get(dn, ""))
        if nm:
            out[nm] = round(v - fastest, 3)
            if nm.split():
                out.setdefault(nm.split()[-1], out[nm])
    return out


def _f1_longrun_pace():
    """This weekend's RACE pace from practice long runs (OpenF1). Prefers FP2 (the
    traditional race-sim session), then the latest completed practice of the
    current weekend; None once the freshest is stale (a finished weekend)."""
    import datetime as _dt

    def build():
        now = _dt.datetime.now(_dt.timezone.utc)
        year = clock.today_et().year
        try:
            sessions = racing._get_json(f"https://api.openf1.org/v1/sessions?year={year}")
        except Exception:
            return None
        cand = []
        for s in sessions:
            if s.get("session_type") != "Practice":
                continue
            try:
                t = _dt.datetime.fromisoformat(s["date_start"].replace("Z", "+00:00"))
            except Exception:
                continue
            if t < now and (now - t).total_seconds() <= 4 * 86400:
                cand.append((s.get("session_name"), t, s))
        if not cand:
            return None
        # FP2 first (longest runs), then the most recent practice.
        cand.sort(key=lambda c: (0 if c[0] == "Practice 2" else 1, -c[1].timestamp()))
        for _nm, _t, s in cand:
            res = _longrun_from_session(s["session_key"])
            if res:
                return res
        return None
    return racing._cached(("f1_longrun",), 1800, build)


# DraftKings F1 scoring: finishing points (1st 43, 2nd 40, 3rd 38, then 41−pos),
# ±1 place differential, fastest lap +5, laps led +0.1, defeated teammate +5.
# F1's dominator pool is tiny (57 laps x 0.1 ≈ 5.7 + the 5-pt fastest lap), so
# unlike NASCAR the GPP question is finish position + place differential — which
# is why we re-simulate the race FROM THE REAL GRID once qualifying is done.
def _dk_f1_fin(pos):
    if pos <= 1:
        return 43.0
    if pos == 2:
        return 40.0
    return max(1.0, 41.0 - pos)


_F1_LAPS = 57                      # typical race distance; 0.1/lap keeps this minor


def _f1_dk_race(order, laps, rng):
    """Laps-led + fastest-lap DK points for ONE simulated F1 race. The winner
    leads most laps (modern F1 is front-locked); the fastest lap often goes to a
    top car that pits late for softs, so it spreads over the top ~8."""
    dom = {}
    k = 1 + (1 if rng.random() < 0.55 else 0) + (1 if rng.random() < 0.25 else 0)
    leaders = order[:max(3, k)]
    weights = [math.exp(-i / 1.1) * (0.6 + rng.random()) for i in range(len(leaders))]
    picks = sorted(range(len(leaders)), key=lambda i: -weights[i])[:k]
    shares = sorted((rng.expovariate(1.0) for _ in picks), reverse=True)
    tot = sum(shares) or 1.0
    for idx, sh in zip(picks, shares):
        dom[leaders[idx]] = 0.1 * laps * sh / tot
    fl_pool = order[:8]
    fw = [math.exp(-i / 2.5) for i in range(len(fl_pool))]
    fl = rng.choices(fl_pool, weights=fw)[0]
    dom[fl] = dom.get(fl, 0.0) + 5.0
    return dom


# DraftKings NASCAR Classic scoring: finishing points (1st=45, then 44−pos),
# ±1 per place gained/lost, +0.25 per lap led, +0.45 per fastest lap. Dominator
# points scale with race LENGTH — a 400-lap short track carries a 280-point
# dominator pool, a 90-lap road course only ~63 — which is why "who leads laps"
# is the whole GPP question at ovals and nearly irrelevant on streets.
def _dk_nascar_fin(pos):
    return 45.0 if pos <= 1 else max(1.0, 44.0 - pos)


_DEFAULT_LAPS = {"road": 90, "superspeedway": 170, "short": 400, "intermediate": 300}
# How many drivers realistically split the laps at each track type (front-biased):
# short ovals are 2-3 car shows; superspeedways shuffle the lead constantly.
_DOM_N = {"road": (2, 4), "superspeedway": (5, 8), "short": (2, 3), "intermediate": (3, 5)}


def _alloc_dominators(order, laps, ttype, rng, led_prop=None):
    """Split a race's laps-led + fastest-laps among the finishing order for ONE
    simulated race -> {driver_id: dominator DK points}. Leaders come mostly from
    the front of the finishing order (you finish near where you dominated), and —
    when `led_prop` carries each driver's REAL historical laps-led share at this
    track type (straight from the timing data) — proven lap-hoarders dominate at
    their actual rates instead of a position guess (SVG led 67% of Sonoma; a
    steady top-5 points racer often leads almost nothing)."""
    lo, hi = _DOM_N.get(ttype, (3, 5))
    k = rng.randint(lo, hi)
    runners = order[:max(k + 6, 12)]
    # Front-of-field bias with noise; a mid-pack finisher occasionally led early.
    weights = [math.exp(-i / 2.6) * (0.5 + rng.random()) for i in range(len(runners))]
    if led_prop:
        # 0.35 floor keeps unknowns alive; ~4x scale means a 0.40-share hoarder
        # carries ~5x the pull of a never-leads car at the same finishing spot.
        weights = [w * (0.35 + 4.0 * (led_prop.get(runners[i]) or 0.0))
                   for i, w in enumerate(weights)]
    picks = sorted(range(len(runners)), key=lambda i: -weights[i])[:k]
    shares = sorted((rng.expovariate(1.0) for _ in picks), reverse=True)
    tot = sum(shares) or 1.0
    led = {}
    for idx, sh in zip(picks, shares):
        led[runners[idx]] = laps * sh / tot
    # Fastest laps: ~65% tracks laps led (clean air), the rest spreads over the
    # top ~12 finishers (fast cars in traffic still ring fastest laps).
    dom = {}
    spread = runners[:12]
    for did, L in led.items():
        dom[did] = 0.25 * L + 0.45 * (L * 0.65)
    for did in spread:
        dom[did] = dom.get(did, 0.0) + 0.45 * (laps * 0.35 / len(spread))
    return dom


def next_race_sim(sport, n=2500, seed=None, fixed_grid=None):
    """Simulate the NEXT race many times and return each driver's finish profile
    {name: {avg_finish, p_win, p_top5, p_top10, p_top20, ...}} -- the signal the
    DFS optimizer feeds on -- plus REAL DraftKings scoring components per driver
    (dk_mean / dk_q90 for true per-player means and GPP ceilings).

    NASCAR: finish points + a dominator model (laps led x0.25 + fastest x0.45)
    sized to THIS race's length and track type. Grid-independent by design (Cup
    start position barely predicts the finish); place differential is applied
    by the DFS layer off the real grid.

    F1: finish points + laps led x0.1 + the 5-pt fastest lap + the defeated-
    teammate +5. When `fixed_grid` ({normalized name: start pos}) is given --
    i.e. real qualifying is done -- every simulated race STARTS FROM THAT GRID,
    so expected finish is conditioned on the actual start and place differential
    is computed exactly, per sim, inside the DK samples (F1 races lean hard on
    the grid, so conditioning beats any linear PD adjustment)."""
    rng = random.Random(seed)
    fin = defaultdict(float); win = defaultdict(int)
    t5 = defaultdict(int); t10 = defaultdict(int); t20 = defaultdict(int)
    name_of = {}
    dk_samples = defaultdict(list)          # per-sim DK points (scoring components)
    laps = None; ttype = None
    grid_conditioned = False
    longrun_used = False

    if sport == "f1":
        profs = f1_profiles(); rem = f1_remaining()
        if not profs or not rem:
            return None
        drivers = list(profs.values())
        name_of = {d["id"]: d["name"] for d in drivers}
        race = rem[0]
        ctype = race.get("type", "standard"); wet_p = race.get("wet_prob", 0.0)
        laps = _F1_LAPS
        # Fresh practice pace from THIS weekend sharpens the simulated qualifying
        # (pre-quali only — once the real grid is fixed, practice is moot).
        if not fixed_grid:
            try:
                pg = _f1_practice_gaps()
            except Exception:
                pg = None
            if pg:
                blended, hits = [], 0
                for d in drivers:
                    nm = racing.norm_name(d["name"])
                    g = pg.get(nm) or (pg.get(nm.split()[-1]) if nm.split() else None)
                    if g is not None:
                        d = dict(d, quali=0.65 * d["quali"] + 0.35 * min(3.0, g))
                        hits += 1
                    blended.append(d)
                if hits >= 12:
                    drivers = blended
        # THIS weekend's long-run (race) pace sharpens each driver's race pace at
        # THIS circuit — the strongest race-pace read there is, and one the
        # season-average finish misses. Grid-independent, so it applies whether or
        # not the real grid is set. Blended as a pace RANK into the race score.
        try:
            lr = _f1_longrun_pace()
        except Exception:
            lr = None
        if lr:
            gaps = {}
            for d in drivers:
                nm = racing.norm_name(d["name"])
                g = lr.get(nm) or (lr.get(nm.split()[-1]) if nm.split() else None)
                if g is not None:
                    gaps[d["id"]] = g
            if len(gaps) >= 12:
                ranked = sorted(gaps, key=lambda did: gaps[did])
                rank = {did: i + 1 for i, did in enumerate(ranked)}
                _LRW = 0.35                          # weight on this-weekend race pace
                drivers = [dict(d, race=(1 - _LRW) * d["race"] + _LRW * rank[d["id"]])
                           if d["id"] in rank else d for d in drivers]
                longrun_used = True
        # Teammate pairs for the defeated-teammate bonus.
        by_con = defaultdict(list)
        for d in drivers:
            by_con[d.get("constructor_id") or d.get("constructor")].append(d["id"])
        mate_of = {}
        for ids in by_con.values():
            if len(ids) == 2:
                mate_of[ids[0]], mate_of[ids[1]] = ids[1], ids[0]
        # Resolve the real grid (normalized names) to a driver-id start order.
        fg_order = None
        if fixed_grid:
            id_by_norm = {}
            for d in drivers:
                nm = racing.norm_name(d["name"])
                id_by_norm[nm] = d["id"]
                last = nm.split()[-1] if nm.split() else nm
                id_by_norm.setdefault(last, d["id"])
            pairs = []
            for nm, pos in fixed_grid.items():
                did = id_by_norm.get(nm)
                if did is None and nm.split():
                    did = id_by_norm.get(nm.split()[-1])
                if did is not None:
                    pairs.append((pos, did))
            # 8+ real matches = a legitimate grid (standings can carry 21-22
            # entries with mid-season replacements, so don't gate on field size).
            if len(pairs) >= 8:
                pairs.sort()
                seen, fg = set(), []
                for _pos, did in pairs:
                    if did not in seen:
                        fg.append(did); seen.add(did)
                for d in drivers:                      # anyone unmatched starts last
                    if d["id"] not in seen:
                        fg.append(d["id"])
                fg_order = fg
                grid_conditioned = True
        for _ in range(n):
            grid = list(fg_order) if fg_order else \
                _apply_grid_penalties(_sim_quali(drivers, rng), rng)
            start_of = {did: i + 1 for i, did in enumerate(grid)}
            order = _sim_race(drivers, grid, rng, wet=rng.random() < wet_p, ctype=ctype)
            _tally_finish(order, fin, win, t5, t10, t20)
            dom = _f1_dk_race(order, laps, rng)
            pos_of = {did: i + 1 for i, did in enumerate(order)}
            for pos, did in enumerate(order, 1):
                pts = _dk_f1_fin(pos) + dom.get(did, 0.0)
                mate = mate_of.get(did)
                if mate is not None and pos < pos_of.get(mate, 99):
                    pts += 5.0                          # defeated teammate
                if fg_order:
                    pts += start_of.get(did, len(order)) - pos   # exact in-sim PD
                dk_samples[did].append(pts)
    elif sport == "nascar":
        state = nascar_state()
        dmap = (state or {}).get("drivers") or {}
        rem = (state or {}).get("remaining") or []
        if not dmap or not rem:
            return None
        drivers = list(dmap.values())
        name_of = {d["id"]: d["name"] for d in drivers}
        race = rem[0]
        ttype = race.get("type", "intermediate"); wet_p = race.get("wet_prob", 0.0)
        laps = int(race.get("laps") or _DEFAULT_LAPS.get(ttype, 300))
        # Final-practice speed rank at THIS track nudges race pace: a car that
        # unloaded fast usually races fast; one buried in practice rarely finds
        # 15 spots by Sunday. Modest weight (trim levels vary), name-matched.
        try:
            pr = racing.get_nascar_practice(race.get("name"))
        except Exception:
            pr = None
        if pr:
            by_full, by_last = racing._index(pr)
            ranked = sorted(drivers, key=lambda d: d["race_pace"]
                            + (d.get("pace_by_type") or {}).get(ttype, 0.0))
            mrank = {d["id"]: i + 1 for i, d in enumerate(ranked)}
            adj, hits = [], 0
            for d in drivers:
                nm = racing.norm_name(d["name"])
                p = by_full.get(nm) or (by_last.get(nm.split()[-1]) if nm.split() else None)
                if p is not None:
                    delta = max(-4.0, min(4.0, 0.30 * (p - mrank[d["id"]])))
                    d = dict(d, race_pace=d["race_pace"] + delta)
                    hits += 1
                adj.append(d)
            if hits >= 15:
                drivers = adj
        # Real historical laps-led share per driver AT THIS TRACK TYPE.
        led_prop = {d["id"]: (d.get("led_by_type") or {}).get(ttype, 0.0) for d in drivers}
        for _ in range(n):
            order = _sim_cup_race(drivers, rng, wet=rng.random() < wet_p, ttype=ttype)
            _tally_finish(order, fin, win, t5, t10, t20)
            dom = _alloc_dominators(order, laps, ttype, rng, led_prop)
            for pos, did in enumerate(order, 1):
                dk_samples[did].append(_dk_nascar_fin(pos) + dom.get(did, 0.0))
    else:
        return None

    out = {}
    # Keep an index-ALIGNED subsample of every driver's per-race DK points: entry
    # i of every array is the SAME simulated race, so lineup scoring can replay
    # joint outcomes (one winner per race, the big one wrecking several cars at
    # once) instead of drawing independent bell curves per driver.
    step = max(1, n // 1000)
    keep = list(range(0, n, step))
    for did, nm in name_of.items():
        row = {"avg_finish": round(fin[did] / n, 2),
               "p_win": round(win[did] / n, 3), "p_top5": round(t5[did] / n, 3),
               "p_top10": round(t10[did] / n, 3), "p_top20": round(t20[did] / n, 3)}
        smp = dk_samples.get(did)
        if smp:
            row["dk_arr"] = [round(smp[i], 1) for i in keep if i < len(smp)]
            srt = sorted(smp)
            m = len(srt)
            row["dk_mean"] = round(sum(srt) / m, 1)           # fin pts + dominator
            row["dk_q90"] = round(srt[min(m - 1, int(0.9 * m))], 1)
        out[nm] = row
    meta = {"sport": sport, "race": race.get("name"), "n_sims": n, "drivers": out}
    if laps is not None:
        meta["laps"] = laps
        meta["track_type"] = ttype if sport == "nascar" else race.get("type")
        # NASCAR: 0.25+0.45 per lap. F1: 0.1 per lap + the single 5-pt fastest lap.
        meta["dominator_pool"] = round(0.7 * laps) if sport == "nascar" \
            else round(0.1 * laps + 5)
        meta["grid_conditioned"] = grid_conditioned
        if sport == "f1":
            meta["longrun_used"] = longrun_used
    return meta


def _tally_finish(order, fin, win, t5, t10, t20):
    for pos, did in enumerate(order, 1):
        fin[did] += pos
        if pos == 1:
            win[did] += 1
        if pos <= 5:
            t5[did] += 1
        if pos <= 10:
            t10[did] += 1
        if pos <= 20:
            t20[did] += 1
