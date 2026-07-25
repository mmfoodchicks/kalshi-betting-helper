"""Monte Carlo simulators (read-only — they touch no stored data).

Price simulator: projects a coin/commodity forward with many random GBM paths
and reports the distribution of where it lands — median, best/worst case, the
5–95% range, the chance it's up, and the chance it crosses a threshold. This is
the "run it for an hour and see the range of outcomes" view.
"""

import csv
import io
import math
import random

import odds


def _pois(lam):
    """Knuth Poisson sampler (fine for the small means in a baseball game).

    Normal approx above lam=30 keeps it correct if ever handed a large mean; the
    old min(lam, 30) cap silently sampled ~Poisson(30) there. Baseball run counts
    never approach 30, so this branch doesn't change any current output."""
    if lam > 30:
        return max(0, int(round(random.gauss(lam, math.sqrt(lam)))))
    L = math.exp(-lam)
    k, p = 0, 1.0
    while True:
        k += 1
        p *= random.random()
        if p <= L:
            return k - 1


def game_sim(er_home, er_away, n=20000):
    """Simulate a baseball game many times from each side's expected runs."""
    hw = aw = tie = blow = shut = 0
    totals = []
    scores = {}
    for _ in range(n):
        h, a = _pois(er_home), _pois(er_away)
        totals.append(h + a)
        if h > a: hw += 1
        elif a > h: aw += 1
        else: tie += 1
        if abs(h - a) >= 5: blow += 1
        if h == 0 or a == 0: shut += 1
        scores[(a, h)] = scores.get((a, h), 0) + 1
    totals.sort()
    def pct(p): return totals[min(n - 1, int(p * n))]
    top = sorted(scores.items(), key=lambda kv: -kv[1])[:5]
    return {
        "home_win_pct": round((hw + tie * 0.52) / n * 100, 1),
        "away_win_pct": round((aw + tie * 0.48) / n * 100, 1),
        "median_total": pct(0.5), "p10_total": pct(0.1), "p90_total": pct(0.9),
        "blowout_pct": round(blow / n * 100, 1), "shutout_pct": round(shut / n * 100, 1),
        "top_scores": [{"away": k[0], "home": k[1], "pct": round(v / n * 100, 1)} for k, v in top],
        "n": n,
    }


def temp_sim(mean, sigma, n=20000, threshold=None, direction=None):
    """Simulate the daily high from the weather model's mean +/- sigma."""
    draws = sorted(random.gauss(mean, sigma) for _ in range(n))
    def pct(p): return round(draws[min(n - 1, int(p * n))], 1)
    res = {"median": pct(0.5), "p10": pct(0.1), "p90": pct(0.9),
           "low": pct(0.02), "high": pct(0.98), "n": n}
    if threshold is not None:
        if direction == "below":
            res["prob_threshold"] = round(sum(1 for x in draws if x <= threshold) / n * 100, 1)
        else:
            res["prob_threshold"] = round(sum(1 for x in draws if x >= threshold) / n * 100, 1)
    return res


# ---- DFS (DraftKings) lineup optimizer + simulator ------------------------
def parse_dk_csv(text):
    """Parse a DraftKings DKSalaries.csv -> [{name, salary, proj, pos}].

    Works with OR without the header row. DraftKings' standard export layout is:
        Position, Name+ID, Name, ID, Roster Position, Salary, Game Info,
        TeamAbbrev, AvgPointsPerGame
    so if no header is present we read those columns positionally.
    """
    text = (text or "").strip()
    if not text:
        return []
    lines = [ln for ln in text.splitlines() if ln.strip()]
    first = lines[0].lower()
    out = []
    if "salary" in first and "name" in first:        # header present
        for r in csv.DictReader(io.StringIO("\n".join(lines))):
            name = (r.get("Name") or r.get("name") or "").strip()
            try:
                salary = float((r.get("Salary") or r.get("salary") or 0))
            except ValueError:
                continue
            # A debut/returning player has no DK average — the cell is "-" or
            # blank. That's not a reason to drop him from the pool (our own sim
            # projects him); treat an unparseable average as 0.
            try:
                proj = float(r.get("AvgPointsPerGame") or r.get("Projection")
                             or r.get("proj") or r.get("AvgPoints") or 0)
            except ValueError:
                proj = 0.0
            if name and salary > 0:
                out.append({"name": name, "salary": salary, "proj": proj,
                            "pos": (r.get("Position") or r.get("Roster Position") or "").strip(),
                            "roster_pos": (r.get("Roster Position") or "").strip().upper(),
                            "game": (r.get("Game Info") or r.get("GameInfo") or "").strip(),
                            "team": (r.get("TeamAbbrev") or r.get("Team") or "").strip()})
        if out:
            return out

    # No usable header -> parse positionally with the standard DK layout.
    for parts in csv.reader(io.StringIO("\n".join(lines))):
        if len(parts) < 6:
            continue
        name = (parts[2] if len(parts) > 2 and parts[2].strip() else parts[1]).strip()
        pos = parts[0].strip()
        # Salary: column 5 in the standard layout, else the first big integer.
        salary = None
        try:
            salary = float(parts[5].replace(",", ""))
        except (ValueError, IndexError):
            pass
        if not salary or salary <= 0:
            for p in parts:
                try:
                    v = float(p.replace(",", ""))
                except ValueError:
                    continue
                if v >= 1000:
                    salary = v
                    break
        # Projection: column 8 (AvgPointsPerGame), else the last small numeric.
        proj = None
        try:
            proj = float(parts[8])
        except (ValueError, IndexError):
            for p in reversed(parts):
                try:
                    v = float(p)
                except ValueError:
                    continue
                if v != salary:          # skip the salary field
                    proj = v
                    break
        game = parts[6].strip() if len(parts) > 6 else ""
        # Roster Position (col 4) carries CPT / D / CNSTR for captain-mode slates.
        rpos = parts[4].strip().upper() if len(parts) > 4 else ""
        if name and salary and salary > 0:
            out.append({"name": name, "salary": salary, "proj": proj or 0.0,
                        "pos": pos, "roster_pos": rpos, "game": game,
                        "team": parts[7].strip() if len(parts) > 7 else ""})
    return out


def apply_grid(players, sport, date=None, grid_text=None):
    """For racing DFS, fetch the qualifying grid and adjust each driver's
    projection for an atypically good/bad starting spot via place differential.

    DK racing scoring is +1 per position gained, -1 per position lost. A driver's
    season FPPR already bakes in their *typical* place differential, so we only
    correct for THIS race's start being better/worse than the car deserves
    (deserved spot proxied by salary rank). A pole-sitter in a mid car has almost
    no upside and big downside -> his projection drops; a fast car buried deep
    gets a boost. Returns a status dict; leaves projections untouched on failure.

    `grid_text`: a hand-pasted starting grid. The feed only carries the last
    INGESTED race's qualifying, which lags a round (and is quali order, not the
    penalty-adjusted starting grid), so a pasted grid overrides it -- this is the
    authoritative starting order for the race you're actually building.
    """
    import racing
    race_name = next((p.get("game") for p in players if p.get("game")), None)
    manual = racing.parse_grid_text(grid_text, sport) if grid_text else None
    # The grid may not exist yet (qualifying hasn't run). That must NOT disable
    # the race simulator — pre-qualifying we still project every driver from
    # thousands of simulated races (finish points + dominators + wrecks/DNFs),
    # just without the place-differential term, and note it honestly.
    grid = manual or racing.get_grid(sport, race_name=race_name, date=date)
    # The feed hands back whichever race in ANY series has a grid — on a Cup
    # weekend where only the Trucks race has qualified, a Cup slate "matches"
    # a handful of crossover drivers and everyone else gets treated as
    # withdrawn (losing their sim projections). A grid that doesn't cover
    # most of the slate is the wrong race: ignore it and let the race
    # simulator carry the projections, same as pre-qualifying.
    if grid and not manual:
        hits = sum(1 for p in players
                   if racing.lookup(grid, p["name"]) is not None)
        if hits < max(3, len(players) * 0.5):
            grid = None
    n = len(players)
    field = grid["field"] if grid else n
    # Deserved finishing spot. Primary source = our race SIMULATOR's expected
    # finish for this driver (it simulates qualifying + the race with car pace,
    # track-type form, DNFs and wet chaos). wait=True: a cold cache computes the
    # sim synchronously rather than silently building from raw CSV numbers.
    _sim_raw_profile(sport, wait=True)
    sim_prof = _sim_profile(sport)              # {normalized_name: {...}} or {}
    order = sorted(range(n), key=lambda i: players[i]["salary"], reverse=True)
    deserved = [0.0] * n
    for rank, i in enumerate(order):
        deserved[i] = min(field, max(1.0, (rank + 0.5) / n * field))
    form = {}
    try:
        if sport == "nascar" and grid:
            form = racing.get_nascar_form((date or "")[:4] or None,
                                          date or "", series=grid.get("series_id") or 1) or {}
        elif sport == "f1":
            form = racing.get_f1_form() or {}
    except Exception:
        form = {}
    # F1: real qualifying is done, so RE-SIMULATE the race thousands of times
    # FROM THE ACTUAL GRID. Expected finish is conditioned on where each driver
    # actually starts, and place differential is computed exactly per simulated
    # race inside the DK samples (F1 leans hard on the grid — conditioning beats
    # any linear PD adjustment). Falls back to the marginal profile on failure.
    f1_prof, cond_meta = {}, {}
    if sport == "f1" and grid:
        try:
            import racing_sim
            # Fixed seed so rerunning the DFS on the SAME grid reproduces the same
            # projections (and lineups) instead of drifting on fresh Monte Carlo
            # noise each build; output still changes when the grid or form does.
            cond = racing_sim.next_race_sim("f1", n=1500, fixed_grid=grid["grid"], seed=1_000_003)
            if cond and cond.get("grid_conditioned"):
                for nm2, s2 in (cond.get("drivers") or {}).items():
                    key = _norm_name(nm2)
                    f1_prof[key] = s2
                    if key.split():
                        f1_prof.setdefault(key.split()[-1], s2)
                cond_meta = {k: cond.get(k) for k in
                             ("laps", "dominator_pool", "grid_conditioned", "n_sims")}
        except Exception:
            f1_prof = {}

    lam = 0.55                                  # partial mean-reversion of the grid
    matched, unmatched, form_hits, sim_hits = 0, [], 0, 0
    dk_hits = 0
    for i, p in enumerate(players):
        start = racing.lookup(grid, p["name"]) if grid else None
        if grid and start is None:
            unmatched.append(p["name"])         # in the CSV but not the grid: withdrew
            continue
        if start is not None:
            matched += 1
        # F1 grid-conditioned DK projection (PD already inside the samples).
        if f1_prof:
            nm = _norm_name(p["name"])
            s2 = f1_prof.get(nm) or (f1_prof.get(nm.split()[-1]) if nm.split() else None)
            if s2 and s2.get("dk_mean") is not None:
                p["base_proj"] = p["proj"]
                p["start"] = start
                csv_anchor = p["proj"] if p["proj"] > 1 else s2["dk_mean"]
                p["proj"] = max(0.0, 0.75 * s2["dk_mean"] + 0.25 * csv_anchor)
                p["ceil_proj"] = round(max(p["proj"], s2["dk_q90"]), 1)
                p["exp_finish"] = round(s2["avg_finish"], 1)
                p["pd_adj"] = round(start - s2["avg_finish"], 1)   # display; PD is in-sim
                if s2.get("dk_arr"):
                    p["arr"] = s2["dk_arr"]     # joint samples: PD already inside
                sim_hits += 1; dk_hits += 1
                continue
        s = sim_prof.get(_norm_name(p["name"]))
        p["base_proj"] = p["proj"]
        p["start"] = start
        # Full sim DK components: the projection is built FROM SCRATCH the way
        # DraftKings scores the sport — expected finish points + dominator points
        # + place differential off the real starting spot (0 until qualifying
        # runs). The CSV's season FPPG stays as a 25% sanity anchor, and the
        # per-race joint samples ride along so lineups are scored on the SAME
        # simulated races (crashes, DNFs and all), not independent bell curves.
        if s and s.get("dk_mean") is not None:
            pd = (start - s["avg_finish"]) if start is not None else 0.0
            ours = s["dk_mean"] + pd
            csv_anchor = p["proj"] if p["proj"] > 1 else ours
            p["proj"] = max(0.0, 0.75 * ours + 0.25 * csv_anchor)
            p["ceil_proj"] = round(max(p["proj"], s["dk_q90"] + pd), 1)
            p["exp_finish"] = round(s["avg_finish"], 1)
            p["pd_adj"] = round(pd, 1)
            if s.get("dk_arr"):
                p["arr"] = [x + pd for x in s["dk_arr"]]
            sim_hits += 1; dk_hits += 1
            continue
        if start is None:
            continue                            # no grid AND no sim: leave the CSV proj
        # Fallback (a driver the sim doesn't know): season-FPPG + partial
        # place-differential correction vs the deserved spot.
        if s is not None:
            deserved[i] = min(field, max(1.0, s["avg_finish"]))
            sim_hits += 1
        else:
            fv = racing._match_prob(form, None, p["name"]) if form else None
            if fv is not None:                  # blend salary-deserved with form
                deserved[i] = min(field, max(1.0, 0.5 * deserved[i] + 0.5 * fv))
                form_hits += 1
        # delta < 0: starting better than deserved -> expected to lose spots.
        delta = start - deserved[i]
        adj = max(-15.0, min(15.0, lam * delta))
        p["exp_finish"] = round(deserved[i], 1)
        p["pd_adj"] = round(adj, 1)
        p["proj"] = max(0.0, p["proj"] + adj)
        # Per-driver GPP ceiling from the sim's finish distribution (so GPP tilts to
        # high-variance drivers rather than mirroring cash). cv=0.5 fallback for
        # drivers the sim doesn't cover.
        p["ceil_proj"] = round(_driver_ceiling(p["proj"], s, 0.5), 1)
    # F1 constructors: DK scores the team off both cars, so project each CNSTR
    # row from OUR two driver projections for THIS race. The absolute scale is
    # anchored to the CSV's own constructor averages (DK knows its table; we
    # know the race-specific ordering and spread), so no scoring table is
    # invented — we supply the shape, DK supplies the scale.
    if sport == "f1":
        by_team = {}
        for p in players:
            if p.get("roster_pos") == "D" and p.get("team"):
                by_team.setdefault(p["team"], []).append(p)
        cons = [p for p in players if p.get("roster_pos") == "CNSTR" and p.get("team")]
        pairs = [(c, by_team.get(c["team"])) for c in cons]
        vals = [(c["proj"], sum(x["proj"] for x in tp)) for c, tp in pairs
                if tp and c["proj"] > 1]
        if vals:
            csv_mean = sum(v for v, _ in vals) / len(vals)
            ours_mean = sum(s for _, s in vals) / len(vals) or 1.0
            k = max(0.2, min(1.5, csv_mean / ours_mean))
        else:
            k = 0.45
        for c, tp in pairs:
            if not tp:
                continue
            c["base_proj"] = c["proj"]
            ours = k * sum(x["proj"] for x in tp)
            anchor = c["base_proj"] if c["base_proj"] > 1 else ours
            c["proj"] = round(0.7 * ours + 0.3 * anchor, 1)
            c["ceil_proj"] = round(max(c["proj"], k * sum(x.get("ceil_proj") or x["proj"]
                                                          for x in tp)), 1)

    meta = _sim_meta(sport)
    if cond_meta:
        meta.update(cond_meta)
    prof_race = _sim_raw_profile(sport).get("race")
    return {"available": bool(grid), "manual": bool(manual),
            "reason": None if grid else
            ("no qualifying grid yet — projections come from the race simulator "
             "(place differential applies after qualifying)" if dk_hits else
             "no qualifying grid posted yet (check back after qualifying)"),
            "race": ("Pasted grid" if manual else grid["race"]) if grid else prof_race,
            "series": grid["series"] if grid else None,
            "field": field, "matched": matched, "unmatched": unmatched[:25],
            "form_used": form_hits > 0, "sim_used": sim_hits > 0, "sim_drivers": sim_hits,
            "dk_scored": dk_hits, "track_type": meta.get("track_type"),
            "laps": meta.get("laps"), "dominator_pool": meta.get("dominator_pool"),
            "grid_conditioned": bool(cond_meta.get("grid_conditioned"))}


def _norm_name(s):
    import unicodedata
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return "".join(c for c in s.lower() if c.isalnum() or c == " ").strip()


def _sim_raw_profile(sport, wait=False):
    """The race simulator's raw next-race profile dict (or {}).

    next_race_profile is non-blocking (background warm), which used to mean a
    cold start silently built lineups from raw CSV numbers with no sim at all.
    With `wait=True` we compute a bounded synchronous profile instead — the DFS
    build takes longer once, then the cache serves everyone."""
    if sport not in ("f1", "nascar"):
        return {}
    try:
        import racing_sim
        prof = racing_sim.next_race_profile(sport)
        if not prof and wait:
            import time as _t
            import racing as _r
            prof = racing_sim.next_race_sim(sport, n=800)
            if prof:
                _r._form_cache[("next_race", sport)] = (_t.time(), prof)
        return prof or {}
    except Exception:
        return {}


def _sim_meta(sport):
    """Race-level meta from the sim profile: track_type / laps / dominator_pool."""
    prof = _sim_raw_profile(sport)
    return {k: prof.get(k) for k in ("track_type", "laps", "dominator_pool")}


def _sim_profile(sport):
    """{normalized_name: {avg_finish, p_win, p_top5, p_top10, p_top20, dk_mean,
    dk_q90}} from the race simulator's next-race profile, keyed by full AND last
    name for DK spellings."""
    prof = _sim_raw_profile(sport)
    if not prof.get("drivers"):
        return {}
    out = {}
    for name, s in prof["drivers"].items():
        nm = _norm_name(name)
        out[nm] = s
        out.setdefault(nm.split()[-1], s)
    return out


def _driver_ceiling(proj, s, cv):
    """A per-driver GPP ceiling. Two drivers with the same expected finish can have
    very different upside -- a midpack car that occasionally wins is a tournament
    play; a steady points-finisher is not. We read the boom-side finish from the
    sim's win/top-N probabilities and scale the projection by how much better that
    boom is than the mean, so GPP tilts toward high-variance drivers (cash doesn't)."""
    if not s:
        return proj * (1 + cv)
    if s.get("p_win", 0) >= 0.08:
        boom = 1.5
    elif s.get("p_top5", 0) >= 0.12:
        boom = 3.5
    elif s.get("p_top10", 0) >= 0.15:
        boom = 8.0
    elif s.get("p_top20", 0) >= 0.20:
        boom = 16.0
    else:
        boom = max(1.0, s.get("avg_finish", 20.0))
    avg = max(1.0, s.get("avg_finish", 20.0))
    upside = min(1.2, max(0.0, 0.6 * (avg / boom - 1)))
    return proj * (1 + upside)


def _sim_expected_finish(sport):
    """{normalized_name: avg_finish}, derived from the full sim profile."""
    out = {}
    for nm, s in _sim_profile(sport).items():
        out[nm] = s["avg_finish"]
    return out


def dfs_optimize(players, roster, cap, key="value", exclusive_group=None):
    """Max-`key` lineup of exactly `roster` players under the salary cap
    (0/1 knapsack with a cardinality constraint; salary in $100 units).

    `exclusive_group(player) -> id` marks players that can't coexist on a lineup:
    at most ONE player per group is ever chosen. For UFC that's the bout — you must
    never roster both fighters in a fight, since one is guaranteed to lose. Players
    with no group (id falsy) are each their own singleton, so they're unconstrained.

    Each DP cell carries the actual selected indices so reconstruction can never
    reuse a player (shared back-pointers can otherwise rebuild an invalid path)."""
    U = int(cap // 100)
    NEG = float("-inf")
    # Bucket players into mutually-exclusive groups (singletons when ungrouped).
    if exclusive_group is not None:
        groups = {}
        for idx in range(len(players)):
            gid = exclusive_group(players[idx])
            groups.setdefault(gid if gid else ("_solo", idx), []).append(idx)
        group_list = list(groups.values())
    else:
        group_list = [[idx] for idx in range(len(players))]

    # dp[k][s] = (best value, tuple of chosen indices) using exactly k players.
    dp = [[(NEG, None)] * (U + 1) for _ in range(roster + 1)]
    dp[0][0] = (0.0, ())
    for members in group_list:
        # Group knapsack: each member is evaluated against the state from BEFORE
        # this group (dp), writing into new_dp, so two members of the same group can
        # never both be selected. new_dp seeded with "take nobody from this group".
        new_dp = [row[:] for row in dp]
        for idx in members:
            pl = players[idx]
            # Ceil, never round: rounding $4,740 to 47 units understates the
            # real cost and can return a lineup a few dollars OVER the cap
            # (DK rejects it). Real DK salaries are even $100s, so exact there.
            su = -(-int(pl["salary"]) // 100)
            if su > U or su <= 0:
                continue
            pr = pl.get(key, pl["proj"])
            for k in range(roster - 1, -1, -1):
                row = dp[k]
                nrow = new_dp[k + 1]
                for s in range(U - su, -1, -1):
                    val, sel = row[s]
                    if val == NEG:
                        continue
                    if val + pr > nrow[s + su][0]:
                        nrow[s + su] = (val + pr, sel + (idx,))
        dp = new_dp
    best, bsel = NEG, None
    for s in range(U + 1):
        val, sel = dp[roster][s]
        if val > best:
            best, bsel = val, sel
    if bsel is None:
        return None
    return [players[i] for i in bsel]


def _set_values(players, objective, cv):
    """Set each player's optimizer value. 'projection' (cash) is the plain mean;
    'ceiling' (GPP) rewards upside; 'leverage' rewards upside the FIELD ignores.

    GPP must use each player's OWN ceiling -- a flat multiple of proj
    (proj*(1+cv)) scales every player identically, so the knapsack picks the exact
    same lineup and GPP looks no different from cash. UFC fighters and NASCAR
    drivers carry real per-player simulated ceilings (`ceil_proj`, the 90th-pct DK
    night), so a finisher with knockout power / a dominator candidate outranks a
    steady points-scorer of equal mean.

    'leverage' is the large-field GPP play: the same ceiling, discounted by
    projected ownership (~-0.7% of value per ownership point). Beating a
    100k-entry field requires being DIFFERENT and right -- a 25%-owned chalk
    ceiling play wins you a shared prize; the 5%-owned one wins you the top."""
    for p in players:
        ceil = p.get("ceil_proj") or p["proj"] * (1 + cv)
        if objective == "leverage":
            own = p.get("own") if p.get("own") is not None else 15.0
            p["value"] = ceil * (1.0 - 0.007 * own)
        elif objective == "ceiling":
            p["value"] = ceil
        else:
            p["value"] = p["proj"]


def dfs_showdown(players, cap, objective, cv, flex_count=5, exclusive_group=None):
    """Captain (1.5x salary + 1.5x points) + flex lineup. Tries each player as
    captain and knapsacks the rest. `exclusive_group` forbids co-rostering players
    in the same group (e.g. both fighters of a UFC bout) -- including the captain."""
    seen = {}
    for p in players:  # dedupe by name (showdown CSVs list CPT + FLEX); keep base salary
        if p["name"] not in seen or p["salary"] < seen[p["name"]]["salary"]:
            seen[p["name"]] = p
    pool = list(seen.values())
    _set_values(pool, objective, cv)
    best = None
    for i, capt in enumerate(pool):
        cap_sal = capt["salary"] * 1.5
        rem = cap - cap_sal
        if rem < 0:
            continue
        rest = pool[:i] + pool[i + 1:]
        if exclusive_group is not None:        # drop the captain's group-mates from the flex
            cg = exclusive_group(capt)
            if cg:
                rest = [p for p in rest if exclusive_group(p) != cg]
        flex = dfs_optimize(rest, flex_count, int(rem), exclusive_group=exclusive_group)
        if not flex:
            continue
        score = capt["value"] * 1.5 + sum(p["value"] for p in flex)
        if best is None or score > best[0]:
            best = (score, capt, flex)
    if not best:
        return None
    _, capt, flex = best
    cap_row = {"name": capt["name"], "salary": capt["salary"] * 1.5,
               "proj": capt["proj"] * 1.5, "captain": True}
    return [cap_row] + [{**p, "captain": False} for p in flex]


def _f1_showdown(players, cap, objective, cv):
    """DraftKings F1 Captain Mode: exactly 1 captain (a DRIVER at 1.5x cost & 1.5x
    points), 4 more drivers, and 1 constructor, under the cap. The CSV lists each
    driver twice -- a CPT row (already 1.5x salary) and a D row -- plus CNSTR rows;
    the generic knapsack ignored all that and just grabbed the six highest-value
    slots (which were constructors). This builds the real roster shape."""
    cpt = [p for p in players if p.get("roster_pos") == "CPT"]
    drv = [p for p in players if p.get("roster_pos") == "D"]
    con = [p for p in players if p.get("roster_pos") == "CNSTR"]
    if not cpt or len(drv) < 4 or not con:
        return None
    _set_values(players, objective, cv)
    best = None
    for c in cpt:
        if c["salary"] > cap:
            continue
        cval = c["value"] * 1.5                       # captain scores 1.5x
        pool = [d for d in drv if d["name"] != c["name"]]
        for k in con:
            rem = cap - c["salary"] - k["salary"]
            if rem < 0:
                continue
            # DK F1: a single team can contribute at most 2 roster spots, and you
            # can't pair both of a team's drivers WITH that team's constructor
            # (that's 3). So when we take constructor k, the driver pool may hold at
            # most one driver from k's team once the captain is counted.
            kteam = k.get("team")
            kpool = pool
            if kteam:
                allow = 0 if c.get("team") == kteam else 1
                kt = sorted((d for d in pool if d.get("team") == kteam),
                            key=lambda d: -d["value"])
                if len(kt) > allow:
                    drop = {id(d) for d in kt[allow:]}
                    kpool = [d for d in pool if id(d) not in drop]
            four = dfs_optimize(kpool, 4, int(rem))   # best 4 drivers under what's left
            if not four:
                continue
            score = cval + k["value"] + sum(d["value"] for d in four)
            if best is None or score > best[0]:
                best = (score, c, k, four)
    if not best:
        return None
    _, c, k, four = best
    cap_row = {**c, "captain": True, "proj": c["proj"] * 1.5}
    return [cap_row] + [{**d, "captain": False} for d in four] + [{**k, "captain": False}]


def dfs_sim(lineup, n=20000, cv=0.55):
    """Monte Carlo a lineup's total DK points.

    When players carry `arr` (per-sim DK samples from the race/fight simulator,
    index-aligned so entry i is the SAME simulated race/card), the lineup is
    scored by replaying those JOINT outcomes: only one driver wins any given
    race, the big one wrecks several of your cars at once, and exactly one
    fighter per bout banks the win bonus. Six players can no longer all hit
    their 90th percentile in the same world — the old independent-gaussian draw
    let them, which inflated every ceiling and win%. Players without samples
    (e.g. a driver the sim doesn't know) keep an independent gaussian."""
    arrs = [(p.get("arr"), p) for p in lineup]
    have = [a for a, _ in arrs if a]
    totals = []
    if have:
        L = min(len(a) for a in have)
        iters = min(n, max(L, 1) * 4)             # resample the joint worlds
        for _ in range(iters):
            i = random.randrange(L)
            t = 0.0
            for a, p in arrs:
                if a:
                    t += a[i % len(a)]
                else:
                    t += max(0.0, random.gauss(p["proj"], p["proj"] * cv))
            totals.append(t)
    else:
        for _ in range(n):
            t = 0.0
            for _a, p in arrs:
                t += max(0.0, random.gauss(p["proj"], p["proj"] * cv))
            totals.append(t)
    totals.sort()
    m = len(totals)
    def pct(q): return round(totals[min(m - 1, int(q * m))], 1)
    return {"floor": pct(0.1), "median": pct(0.5), "ceiling": pct(0.9),
            "max": round(totals[-1], 1), "n": m,
            "correlated": bool(have)}


# ---- Projected ownership + portfolio + large-field contest sim (generic) ----
def _estimate_ownership(players, roster, sport=None):
    """Rough projected FIELD ownership (% of lineups that roster a player) from
    points-per-$1k value: chalk (high value) gets rostered a lot. Softmax over
    value, scaled so total ownership ~ roster*100 (each lineup holds `roster`
    players). Sport-aware: the NASCAR field chases obvious place-differential
    plays (fast car buried deep), the UFC field over-rosters favorites — the
    exact behaviors leverage lineups fade. Not an external feed, but a sound
    model of what the field will do."""
    for p in players:
        p["_val"] = (p.get("proj") or 0.0) / max(1.0, p["salary"] / 1000.0)
        if sport in ("nascar", "f1"):
            pd = p.get("pd_adj") or 0.0
            if pd > 3:                      # visible PD play -> the field piles in
                p["_val"] *= 1.0 + min(0.35, 0.03 * pd)
        elif sport == "ufc":
            wp = p.get("win_pct")
            if wp is not None:              # favorites get over-rostered
                p["_val"] *= 0.70 + 0.006 * wp
    mx = max((p["_val"] for p in players), default=1.0) or 1.0
    raw = {p["name"]: math.exp(2.4 * (p["_val"] / mx - 1.0)) for p in players}
    tot = sum(raw.values()) or 1.0
    for p in players:
        p["own"] = round(max(0.3, min(75.0, 100.0 * roster * raw[p["name"]] / tot)), 1)


def _field_lineup(players, roster, cap, rng, exclusive_group=None, tries=12):
    """One ownership-weighted valid field lineup (list of player dicts) or None."""
    w = [max(0.1, p.get("own", 1.0)) for p in players]
    idxs = list(range(len(players)))
    for _ in range(tries):
        chosen, groups, sal, avail = [], set(), 0.0, list(idxs)
        while len(chosen) < roster and avail:
            i = rng.choices(avail, weights=[w[j] for j in avail])[0]
            avail.remove(i)
            p = players[i]
            g = exclusive_group(p) if exclusive_group else None
            if sal + p["salary"] <= cap and (not g or g not in groups):
                chosen.append(p); sal += p["salary"]
                if g:
                    groups.add(g)
        if len(chosen) == roster:
            return chosen
    return None


def _leverage_lineup(players, roster, cap, cv, exclusive_group):
    """The large-field GPP build: generate a spread of near-ceiling lineups
    (value-jittered knapsack restarts), then pick the one with the best
    ceiling x ownership-leverage tradeoff AT THE LINEUP LEVEL. A per-player
    value discount often just re-picks the chalk (the chalk IS the ceiling);
    choosing between whole builds is how you actually end up different-and-right
    in a 100k field. Falls back to the straight ceiling lineup if nothing
    lower-owned comes close."""
    cands = _portfolio(players, roster, cap, "ceiling", cv,
                       n_lineups=20, max_exposure=100.0, min_uniq=1,
                       exclusive_group=exclusive_group)
    if not cands:
        return None

    def score(ln):
        ceil = sum(p.get("ceil_proj") or p["proj"] * (1 + cv) for p in ln)
        avg_own = sum(p.get("own", 15.0) or 15.0 for p in ln) / max(1, len(ln))
        return ceil * (1.0 - 0.009 * avg_own)
    return max(cands, key=score)


def _portfolio(players, roster, cap, objective, cv, n_lineups, max_exposure,
               min_uniq, exclusive_group):
    """Build up to `n_lineups` diverse lineups: value-perturbed knapsack restarts,
    an exposure cap (no player in more than max_exposure% of lineups) and a
    uniqueness floor (each lineup differs from the others by >= min_uniq)."""
    _set_values(players, objective, cv)
    base = {p["name"]: p["value"] for p in players}
    max_count = max(1, int(round(max_exposure / 100.0 * n_lineups)))
    chosen, exposure = [], {}
    for attempt in range(n_lineups * 25):
        if len(chosen) >= n_lineups:
            break
        pool = [p for p in players if exposure.get(p["name"], 0) < max_count]
        for p in pool:                       # perturb values to force diversity
            jitter = 1.0 if attempt == 0 else (0.80 + 0.40 * random.random())
            p["value"] = base[p["name"]] * jitter
        lu = dfs_optimize(pool, roster, cap, exclusive_group=exclusive_group)
        if not lu:
            break
        key = frozenset(p["name"] for p in lu)
        if any(len(key & frozenset(p["name"] for p in c)) > roster - min_uniq for c in chosen):
            continue
        chosen.append(lu)
        for p in lu:
            exposure[p["name"]] = exposure.get(p["name"], 0) + 1
    for p in players:                        # restore clean values
        p["value"] = base[p["name"]]
    return chosen


def _contest_sim(your_lineups, players, roster, cap, cv, contest="gpp", entry_fee=1.0,
                 contest_size=None, prize_pool=None, first_prize=None,
                 exclusive_group=None, n_iter=400, field_n=500):
    """Win% / cash% / ROI for lineups in a contest of ANY size. Ownership-weighted
    sample field + analytic extrapolation to the real entry count with a top-heavy
    payout curve -- the same approach as the MLB contest sim, generalized to
    projection-based sports (players scored independently, no stacks)."""
    import statistics
    import mlb_dfs
    rng = random.Random(1234)
    # A real GPP field isn't dart throwers: most entrants optimize against the
    # same public projections, so field lineups cluster near the optimum. Keep
    # only sampled lineups projecting >= ~84% of YOUR best build — without this
    # floor the model "beats" 14,000 random rosters and prints fantasy ROI.
    ref = max(sum(p["proj"] for p in ln) for ln in your_lineups)
    floor_q = 0.84 * ref
    field, attempts = [], 0
    while len(field) < field_n and attempts < field_n * 40:
        attempts += 1
        fl = _field_lineup(players, roster, cap, rng, exclusive_group)
        if fl and sum(p["proj"] for p in fl) >= floor_q:
            field.append([p["name"] for p in fl])
    if len(field) < 20:                      # thin pool -> relax the sharpness floor
        for _ in range(field_n * 5):
            fl = _field_lineup(players, roster, cap, rng, exclusive_group)
            if fl:
                field.append([p["name"] for p in fl])
            if len(field) >= field_n:
                break
    if len(field) < 20:
        return None
    by = {p["name"]: p for p in players}
    C = max(2, int(contest_size or (len(field) + 1)))
    pool = float(prize_pool) if prize_pool else entry_fee * C * 0.85
    if contest == "double_up":
        places = max(1, int(round(0.45 * C)))
        du = pool / places
        payout, first = (lambda r: du if r <= places else 0.0), du
    else:
        first = float(first_prize) if first_prize else 0.20 * pool
        payout, places = mlb_dfs._gpp_curve(C, pool, first, entry_fee)
    grid = mlb_dfs._rank_grid(places)
    ncdf, npdf = mlb_dfs._ncdf, mlb_dfs._npdf
    your = [[p["name"] for p in ln] for ln in your_lineups]
    stats = [{"win": 0.0, "cash": 0.0, "ret": 0.0} for _ in your]
    # Shared joint-sample length (players with per-sim arrays from the race/fight
    # simulator). Each contest iteration picks ONE simulated world and scores
    # your lineups AND the whole field in it — the same crashes, the same winner.
    arr_lens = [len(p["arr"]) for p in by.values() if p.get("arr")]
    L = min(arr_lens) if arr_lens else 0
    for _ in range(n_iter):
        i = rng.randrange(L) if L else 0
        sc = {}
        for nm, p in by.items():
            a = p.get("arr")
            if a:
                sc[nm] = a[i % len(a)]
            else:
                sc[nm] = max(0.0, rng.gauss(p["proj"], (p["proj"] or 1.0) * cv))
        fs = [sum(sc.get(nm, 0.0) for nm in fl) for fl in field]
        mu = statistics.fmean(fs)
        sd = statistics.pstdev(fs) or 1.0
        for li, names in enumerate(your):
            ys = sum(sc.get(nm, 0.0) for nm in names)
            q = max(1e-12, min(1.0, 1.0 - ncdf((ys - mu) / sd)))
            st = stats[li]
            st["win"] += math.exp((C - 1) * math.log(1.0 - q)) if q < 1.0 else 0.0
            mr = 1.0 + (C - 1) * q
            sr = math.sqrt(max(1e-9, (C - 1) * q * (1.0 - q)))
            st["cash"] += ncdf((places - mr) / sr)
            if contest == "double_up":
                st["ret"] += du * ncdf((places - mr) / sr)
            else:
                ev = first * (math.exp((C - 1) * math.log(1.0 - q)) if q < 1.0 else 0.0)
                for r, wd in grid:
                    ev += payout(r) * npdf((r - mr) / sr) / sr * wd
                st["ret"] += ev
    out = []
    for st in stats:
        ret = st["ret"] / n_iter
        out.append({"win_pct": round(100 * st["win"] / n_iter, 4),
                    "cash_pct": round(100 * st["cash"] / n_iter, 1),
                    "roi_pct": round(100 * (ret - entry_fee) / entry_fee, 1),
                    "avg_return": round(ret, 2)})
    best = max(range(len(out)), key=lambda i: out[i]["roi_pct"]) if out else None
    return {"entries": C, "iterations": n_iter, "sample_size": len(field),
            "contest": contest, "entry_fee": entry_fee, "prize_pool": round(pool),
            "first_prize": round(first), "places_paid": places,
            "lineups": out, "best_lineup_index": best}


def apply_ufc(players):
    """UFC DFS: replace each fighter's CSV projection with OUR fight-simulator
    projection (win prob + method/round -> DraftKings points). Non-blocking — the
    board computes in the background on a cold start, falling back to the CSV
    numbers until it's ready, then upgrading on the next build."""
    try:
        import ufc_sim
        board = ufc_sim.board()
    except Exception as e:
        return {"available": False, "reason": f"fight sim failed: {e}"}
    if not board:
        return {"available": False,
                "reason": "fighter sim warming up (rating every fighter from history) — using CSV projections; rerun in ~1 min"}
    idx = {}
    for bt in board.get("bouts", []):
        for side in ("a", "b"):
            f = bt[side]
            nm = _norm_name(f["name"])
            idx[nm] = f
            idx.setdefault(nm.split()[-1], f)
    matched = 0
    for p in players:
        f = idx.get(_norm_name(p["name"])) or idx.get(_norm_name(p["name"]).split()[-1])
        if f:
            p["base_proj"] = p["proj"]
            p["proj"] = f["proj"]
            p["ceil_proj"] = f["ceil"]
            if f.get("dk_arr"):
                p["arr"] = f["dk_arr"]      # joint bout samples (winner/loser aligned)
            p["win_pct"] = f["win_pct"]
            p["rating"] = f.get("rating")
            p["record"] = f.get("record")
            p["career_record"] = f.get("career_record")
            p["fights"] = f.get("fights")
            p["thin"] = f.get("thin")
            p["defaulted"] = f.get("defaulted")
            p["debut"] = f.get("debut")
            matched += 1
    return {"available": True, "event": board.get("event"), "matched": matched,
            "sim_used": matched > 0, "fighters": sum(len(b) for b in [board.get("bouts", [])]) * 2}


def _lineup_player(p):
    return {"name": p["name"], "salary": int(p["salary"]), "proj": round(p["proj"], 1),
            "captain": p.get("captain", False), "start": p.get("start"),
            "own": p.get("own"),
            "pd_adj": p.get("pd_adj"), "base_proj": round(p["base_proj"], 1)
            if p.get("base_proj") is not None else None,
            "ceil_proj": round(p["ceil_proj"], 1) if p.get("ceil_proj") is not None else None,
            "win_pct": p.get("win_pct"), "rating": p.get("rating"),
            "record": p.get("record"), "career_record": p.get("career_record"),
            "fights": p.get("fights"), "thin": p.get("thin"),
            "defaulted": p.get("defaulted"), "debut": p.get("debut")}


def dfs_build(text, roster=6, cap=50000, sport="ufc", mode="classic",
              objective="projection", date=None, sims=20000,
              n_lineups=1, max_exposure=60.0, min_uniq=1, contest=None,
              contest_size=None, entry_fee=1.0, prize_pool=None, first_prize=None,
              grid_text=None):
    players = parse_dk_csv(text)
    if len(players) < roster:
        return {"error": f"need at least {roster} players in the CSV (got {len(players)})"}
    # Racing: qualifying-grid place differential. UFC: our fight-sim projections.
    grid_status = None
    ufc_status = None
    if sport in ("nascar", "f1"):
        try:
            grid_status = apply_grid(players, sport, date, grid_text=grid_text)
        except Exception as e:
            grid_status = {"available": False, "reason": f"grid fetch failed: {e}"}
    elif sport == "ufc":
        try:
            ufc_status = apply_ufc(players)
        except Exception as e:
            ufc_status = {"available": False, "reason": f"fight sim failed: {e}"}
    cv = {"nascar": 0.5, "f1": 0.5, "ufc": 0.6}.get(sport, 0.55)
    # UFC: never roster both fighters of a bout (one is guaranteed to lose). The DK
    # "Game Info" column is identical for both fighters in a fight, so it's the bout key.
    exclusive = (lambda p: p.get("game")) if sport == "ufc" else None
    n_lineups = max(1, min(150, int(n_lineups)))
    captain_mode = sport in ("f1", "nascar") and any(p.get("roster_pos") == "CNSTR" for p in players)
    # Projected field ownership BEFORE building: the leverage objective and the
    # contest sim both need it.
    _estimate_ownership(players, roster, sport)

    # ---- Build lineup(s) ----
    if captain_mode:
        lu = _f1_showdown(players, cap, objective, cv)
        lineups = [lu] if lu else []
    elif mode == "showdown":
        lu = dfs_showdown(players, cap, objective, cv,
                          flex_count=max(1, roster - 1), exclusive_group=exclusive)
        lineups = [lu] if lu else []
    elif n_lineups > 1:
        lineups = _portfolio(players, roster, cap, objective, cv, n_lineups,
                             max_exposure, min_uniq, exclusive)
    elif objective == "leverage":
        # Leverage is a LINEUP-level choice among near-ceiling builds, not a
        # per-player discount (which usually just re-picks the chalk).
        lu = _leverage_lineup(players, roster, cap, cv, exclusive)
        lineups = [lu] if lu else []
    else:
        _set_values(players, objective, cv)
        lu = dfs_optimize(players, roster, cap, exclusive_group=exclusive)
        lineups = [lu] if lu else []
    if not lineups or not lineups[0]:
        return {"error": "no valid lineup fits the salary cap"}

    # ---- Contest simulation (win% / cash% / ROI at the real field size) ----
    contest_sim = None
    if contest in ("gpp", "double_up"):
        try:
            # The opponent-field sample scales with the user's sims knob (it was
            # a fixed 500, which read as the "sims" setting being ignored —
            # they're different things: `sims` scores YOUR lineups, this builds
            # the field you're up against).
            contest_sim = _contest_sim(lineups, players, roster, cap, cv, contest=contest,
                                       entry_fee=entry_fee, contest_size=contest_size,
                                       prize_pool=prize_pool, first_prize=first_prize,
                                       exclusive_group=exclusive,
                                       n_iter=min(1200, max(300, sims // 40)),
                                       field_n=min(1500, max(400, sims // 30)))
        except Exception as e:
            contest_sim = {"error": f"contest sim failed: {e}"}

    per_sims = min(sims, 4000) if len(lineups) > 1 else sims
    lineups_out = [{
        "lineup": [_lineup_player(p) for p in ln],
        "total_salary": int(sum(p["salary"] for p in ln)),
        "total_proj": round(sum(p["proj"] for p in ln), 1),
        "own_sum": round(sum(p.get("own", 0) or 0 for p in ln), 1),
        "sim": dfs_sim(ln, n=per_sims, cv=cv),
    } for ln in lineups]

    # Exposure report across the portfolio.
    exp = {}
    for ln in lineups:
        for p in ln:
            exp[p["name"]] = exp.get(p["name"], 0) + 1
    exposure = sorted(({"name": nm, "lineups": c, "pct": round(100 * c / len(lineups), 1)}
                       for nm, c in exp.items()), key=lambda x: -x["lineups"])[:25]

    first_out = lineups_out[0]
    return {
        # Back-compat single-lineup fields (existing UI path).
        "lineup": first_out["lineup"], "total_salary": first_out["total_salary"],
        "total_proj": first_out["total_proj"], "sim": first_out["sim"],
        "cap": cap, "roster": roster, "pool": len(players),
        "mode": mode, "objective": objective, "grid": grid_status, "ufc": ufc_status,
        # Portfolio + contest.
        "n_lineups": len(lineups_out), "lineups": lineups_out,
        "exposure": exposure if len(lineups_out) > 1 else [],
        "contest_sim": contest_sim,
    }


def price_sim(spot, candles, horizon, n=20000, threshold=None, direction=None):
    """Many random GBM paths over `horizon` (in the candles' time unit).

    Returns distribution stats + a histogram. `horizon` is in minutes for 1-min
    crypto candles, or in days for daily commodity candles (units just have to
    match the candles, which is how the odds engine already works).
    """
    mu, sigma, k = odds.estimate_params(candles)
    if sigma <= 0 or spot <= 0:
        return {"error": "not enough recent data to simulate"}
    drift = (mu - 0.5 * sigma * sigma) * horizon
    vol = sigma * math.sqrt(horizon)
    finals = sorted(spot * math.exp(drift + vol * random.gauss(0, 1)) for _ in range(n))

    def pct(p):
        return finals[min(n - 1, int(p * n))]

    up = sum(1 for f in finals if f > spot) / n
    res = {
        "spot": round(spot, 4), "n": n, "horizon": horizon,
        "median": round(pct(0.50), 4),
        "p1": round(pct(0.01), 4), "p5": round(pct(0.05), 4),
        "p25": round(pct(0.25), 4), "p75": round(pct(0.75), 4),
        "p95": round(pct(0.95), 4), "p99": round(pct(0.99), 4),
        "best": round(finals[-1], 4), "worst": round(finals[0], 4),
        "prob_up": round(up * 100, 1),
        "median_move_pct": round((pct(0.50) / spot - 1) * 100, 2),
        "best_case_move_pct": round((pct(0.95) / spot - 1) * 100, 2),
        "worst_case_move_pct": round((pct(0.05) / spot - 1) * 100, 2),
    }
    if threshold:
        if direction == "below":
            res["prob_threshold"] = round(sum(1 for f in finals if f <= threshold) / n * 100, 1)
        else:
            res["prob_threshold"] = round(sum(1 for f in finals if f >= threshold) / n * 100, 1)
        res["threshold"] = threshold
        res["direction"] = direction or "above"

    lo, hi = pct(0.02), pct(0.98)
    bins = 24
    width = (hi - lo) / bins if hi > lo else 1
    counts = [0] * bins
    for f in finals:
        if lo <= f <= hi:
            counts[min(bins - 1, int((f - lo) / width))] += 1
    res["hist"] = {"lo": round(lo, 4), "hi": round(hi, 4), "counts": counts}
    return res
