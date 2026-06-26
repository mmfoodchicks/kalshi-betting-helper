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
    """Knuth Poisson sampler (fine for the small means in a baseball game)."""
    L = math.exp(-min(lam, 30))
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
                proj = float(r.get("AvgPointsPerGame") or r.get("Projection")
                             or r.get("proj") or r.get("AvgPoints") or 0)
            except ValueError:
                continue
            if name and salary > 0:
                out.append({"name": name, "salary": salary, "proj": proj,
                            "pos": (r.get("Position") or r.get("Roster Position") or "").strip(),
                            "game": (r.get("Game Info") or r.get("GameInfo") or "").strip()})
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
        if name and salary and salary > 0:
            out.append({"name": name, "salary": salary, "proj": proj or 0.0,
                        "pos": pos, "game": game})
    return out


def apply_grid(players, sport, date=None):
    """For racing DFS, fetch the qualifying grid and adjust each driver's
    projection for an atypically good/bad starting spot via place differential.

    DK racing scoring is +1 per position gained, -1 per position lost. A driver's
    season FPPR already bakes in their *typical* place differential, so we only
    correct for THIS race's start being better/worse than the car deserves
    (deserved spot proxied by salary rank). A pole-sitter in a mid car has almost
    no upside and big downside -> his projection drops; a fast car buried deep
    gets a boost. Returns a status dict; leaves projections untouched on failure.
    """
    import racing
    race_name = next((p.get("game") for p in players if p.get("game")), None)
    grid = racing.get_grid(sport, race_name=race_name, date=date)
    if not grid:
        return {"available": False,
                "reason": "no qualifying grid posted yet (check back after qualifying)"}
    n = len(players)
    field = grid["field"]
    # Deserved finishing spot. Primary source = our race SIMULATOR's expected
    # finish for this driver (it simulates qualifying + the race with car pace,
    # track-type form, DNFs and wet chaos). Fallback when a driver isn't in the
    # sim: salary rank (DK prices by car quality), sharpened with recent form.
    sim_prof = _sim_profile(sport)              # {normalized_name: {...}} or {}
    sim_fin = {nm: s["avg_finish"] for nm, s in sim_prof.items()}
    order = sorted(range(n), key=lambda i: players[i]["salary"], reverse=True)
    deserved = [0.0] * n
    for rank, i in enumerate(order):
        deserved[i] = min(field, max(1.0, (rank + 0.5) / n * field))
    form = {}
    try:
        if sport == "nascar":
            form = racing.get_nascar_form((date or "")[:4] or None,
                                          date or "", series=grid.get("series_id") or 1) or {}
        elif sport == "f1":
            form = racing.get_f1_form() or {}
    except Exception:
        form = {}
    lam = 0.55                                  # partial mean-reversion of the grid
    matched, unmatched, form_hits, sim_hits = 0, [], 0, 0
    for i, p in enumerate(players):
        start = racing.lookup(grid, p["name"])
        if start is None:
            unmatched.append(p["name"])
            continue
        matched += 1
        sv = sim_fin.get(_norm_name(p["name"]))
        if sv is not None:                      # the simulator knows this driver
            deserved[i] = min(field, max(1.0, sv))
            sim_hits += 1
        else:
            fv = racing._match_prob(form, None, p["name"]) if form else None
            if fv is not None:                  # blend salary-deserved with form
                deserved[i] = min(field, max(1.0, 0.5 * deserved[i] + 0.5 * fv))
                form_hits += 1
        # delta < 0: starting better than deserved -> expected to lose spots.
        delta = start - deserved[i]
        adj = max(-15.0, min(15.0, lam * delta))
        p["start"] = start
        p["exp_finish"] = round(deserved[i], 1)
        p["pd_adj"] = round(adj, 1)
        p["base_proj"] = p["proj"]
        p["proj"] = max(0.0, p["proj"] + adj)
        # Per-driver GPP ceiling from the sim's finish distribution (so GPP tilts to
        # high-variance drivers rather than mirroring cash). cv=0.5 fallback for
        # drivers the sim doesn't cover.
        p["ceil_proj"] = round(_driver_ceiling(p["proj"], sim_prof.get(_norm_name(p["name"])), 0.5), 1)
    return {"available": True, "race": grid["race"], "series": grid["series"],
            "field": field, "matched": matched, "unmatched": unmatched[:25],
            "form_used": form_hits > 0, "sim_used": sim_hits > 0, "sim_drivers": sim_hits}


def _norm_name(s):
    import unicodedata
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return "".join(c for c in s.lower() if c.isalnum() or c == " ").strip()


def _sim_profile(sport):
    """{normalized_name: {avg_finish, p_win, p_top5, p_top10, p_top20}} from the race
    simulator's next-race profile, keyed by full AND last name for DK spellings."""
    if sport not in ("f1", "nascar"):
        return {}
    try:
        import racing_sim
        prof = racing_sim.next_race_profile(sport)
    except Exception:
        prof = None
    if not prof or not prof.get("drivers"):
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
            su = int(round(pl["salary"] / 100))
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
    'ceiling' (GPP) rewards upside.

    Critically, GPP must use each player's OWN ceiling -- a flat multiple of proj
    (proj*(1+cv)) scales every player identically, so the knapsack picks the exact
    same lineup and GPP looks no different from cash. UFC fighters carry a real
    per-fighter simulated ceiling (`ceil_proj`, the 90th-pct DK night), so a
    finisher with knockout power outranks a steady decision-grinder of equal mean.
    Without a per-player ceiling we fall back to the flat boost (e.g. racing, where
    we don't model per-driver variance), which leaves cash and GPP equivalent."""
    for p in players:
        if objective == "ceiling":
            p["value"] = p.get("ceil_proj") or p["proj"] * (1 + cv)
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


def dfs_sim(lineup, n=20000, cv=0.55):
    """Monte Carlo a lineup's total DK points (per-player variance ~ cv)."""
    totals = []
    for _ in range(n):
        t = 0.0
        for p in lineup:
            t += max(0.0, random.gauss(p["proj"], p["proj"] * cv))
        totals.append(t)
    totals.sort()
    def pct(q): return round(totals[min(n - 1, int(q * n))], 1)
    return {"floor": pct(0.1), "median": pct(0.5), "ceiling": pct(0.9),
            "max": round(totals[-1], 1), "n": n}


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


def dfs_build(text, roster=6, cap=50000, sport="ufc", mode="classic",
              objective="projection", date=None, sims=20000):
    players = parse_dk_csv(text)
    if len(players) < roster:
        return {"error": f"need at least {roster} players in the CSV (got {len(players)})"}
    # Racing: qualifying-grid place differential. UFC: our fight-sim projections.
    grid_status = None
    ufc_status = None
    if sport in ("nascar", "f1"):
        try:
            grid_status = apply_grid(players, sport, date)
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
    if mode == "showdown":
        lineup = dfs_showdown(players, cap, objective, cv,
                              flex_count=max(1, roster - 1), exclusive_group=exclusive)
    else:
        _set_values(players, objective, cv)
        lineup = dfs_optimize(players, roster, cap, exclusive_group=exclusive)
    if not lineup:
        return {"error": "no valid lineup fits the salary cap"}
    sim = dfs_sim(lineup, n=sims, cv=cv)
    return {
        "lineup": [{"name": p["name"], "salary": int(p["salary"]), "proj": round(p["proj"], 1),
                    "captain": p.get("captain", False), "start": p.get("start"),
                    "pd_adj": p.get("pd_adj"), "base_proj": round(p["base_proj"], 1)
                    if p.get("base_proj") is not None else None,
                    "ceil_proj": round(p["ceil_proj"], 1) if p.get("ceil_proj") is not None else None,
                    "win_pct": p.get("win_pct"), "rating": p.get("rating"),
                    "record": p.get("record"), "career_record": p.get("career_record"),
                    "fights": p.get("fights"), "thin": p.get("thin"),
                    "defaulted": p.get("defaulted"), "debut": p.get("debut")}
                   for p in lineup],
        "total_salary": int(sum(p["salary"] for p in lineup)),
        "total_proj": round(sum(p["proj"] for p in lineup), 1),
        "cap": cap, "roster": roster, "sim": sim, "pool": len(players),
        "mode": mode, "objective": objective, "grid": grid_status,
        "ufc": ufc_status,
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
