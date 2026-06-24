"""Multicore deep season: play out the rest of the schedule AND the postseason
with the pitch-by-pitch engine, many times over, to get championship odds and
per-player season stat-line distributions.

Each simulated season plays all remaining regular-season games via deep_sim
(rotations cycle, regulars play — the "assumed rotations" path), tallies the
standings, then plays the real postseason bracket series-by-series with the same
engine. Across N seasons we aggregate division / playoff / pennant / World Series
counts and each player's mean season line (H/HR/K/BB/R, K/BB/IP for pitchers).

Heavy by design (~2,800 deep games/sec/core), so it runs across processes with a
progress counter and is cached; the futures board flips to engine="deep" once a
run completes.
"""

import multiprocessing as mp
import random
import time
from collections import defaultdict

import deep_data
import deep_sim
import season_sim

# Postseason series lengths (wins needed): WC bo3, DS bo5, LCS/WS bo7.
_WC, _DS, _LCS, _WS = 2, 3, 4, 4

_G = {}   # worker globals (set via initializer): profiles, schedule, standings, structure


def _profiles(season, tids):
    return {tid: deep_data.team_profile(tid, season) for tid in tids}


def _play_series(a, b, need, ridx, rng):
    """Best-of (2*need-1) between team ids a (higher seed, home edge) and b.
    Rotations cycle from each club's top arms. Returns the winning team id."""
    wins = {a: 0, b: 0}
    pa, pb = _G["profiles"][a], _G["profiles"][b]
    rot_a, rot_b = pa["rotation"] or [None], pb["rotation"] or [None]
    g = 0
    while wins[a] < need and wins[b] < need:
        # Higher seed hosts games 1,2,6,7 (2-3-2); home team bats last / has edge.
        home_is_a = g in (0, 1, 5, 6)
        sa = rot_a[(ridx[a] + g) % len(rot_a)]
        sb = rot_b[(ridx[b] + g) % len(rot_b)]
        if home_is_a:
            res = deep_sim.play_game(pa, pb, sa, sb, rng)
            winner = a if res["home_win"] else b
        else:
            res = deep_sim.play_game(pb, pa, sb, sa, rng)
            winner = b if res["home_win"] else a
        wins[winner] += 1
        _accum_box(res)
        g += 1
    ridx[a] += g
    ridx[b] += g
    return a if wins[a] > wins[b] else b


def _accum_box(res):
    """Fold one game's box score into this season's per-player running totals."""
    bt, pt = _G["season_bat"], _G["season_pit"]
    for pid, l in res["batting"].items():
        s = bt[pid]
        for k in ("pa", "ab", "h", "2b", "3b", "hr", "bb", "k", "r", "rbi"):
            s[k] += l[k]
    for pid, l in res["pitching"].items():
        s = pt[pid]
        for k in ("bf", "outs", "k", "bb", "h", "hr", "r"):
            s[k] += l[k]


def _sim_one_season(seed):
    rng = random.Random(seed)
    stand, games = _G["standings"], _G["schedule"]
    leagues = _G["leagues"]
    profiles = _G["profiles"]
    # Reset this season's per-player accumulators (kept on the worker between
    # seasons for the cross-season fold in run_deep's merge step).
    _G["season_bat"] = defaultdict(lambda: dict.fromkeys(
        ("pa", "ab", "h", "2b", "3b", "hr", "bb", "k", "r", "rbi"), 0))
    _G["season_pit"] = defaultdict(lambda: dict.fromkeys(
        ("bf", "outs", "k", "bb", "h", "hr", "r"), 0))

    wins = {tid: stand[tid]["wins"] for tid in stand}
    ridx = defaultdict(int)
    for home, away in games:
        ph, pa = profiles.get(home), profiles.get(away)
        if not ph or not pa:
            wins[home if rng.random() < 0.5 else away] += 1
            continue
        sh = ph["rotation"][ridx[home] % len(ph["rotation"])] if ph["rotation"] else None
        sa = pa["rotation"][ridx[away] % len(pa["rotation"])] if pa["rotation"] else None
        ridx[home] += 1; ridx[away] += 1
        res = deep_sim.play_game(ph, pa, sh, sa, rng)
        wins[(home if res["home_win"] else away)] += 1
        _accum_box(res)

    # Postseason bracket per league, then World Series.
    out = {"division": [], "playoffs": [], "pennant": [], "ws": None}
    pidx = defaultdict(int)
    champs = {}
    for lg_id, members in leagues.items():
        order = sorted(members, key=lambda t: (wins[t], rng.random()), reverse=True)
        div_winner = {}
        for tid in order:
            d = stand[tid]["division"]
            div_winner.setdefault(d, tid)
        dws = sorted(div_winner.values(), key=lambda t: (wins[t], rng.random()), reverse=True)
        wcs = [t for t in order if t not in set(dws)][:3]
        seeds = dws + wcs
        out["division"].extend(dws)
        out["playoffs"].extend(seeds)
        # WC (bo3): 3v6, 4v5; seeds 1-2 bye.
        w36 = _play_series(seeds[2], seeds[5], _WC, pidx, rng)
        w45 = _play_series(seeds[3], seeds[4], _WC, pidx, rng)
        ds_lo, ds_hi = (w36, w45) if seeds.index(w36) > seeds.index(w45) else (w45, w36)
        d1 = _play_series(seeds[0], ds_lo, _DS, pidx, rng)
        d2 = _play_series(seeds[1], ds_hi, _DS, pidx, rng)
        champ = _play_series(d1 if wins[d1] >= wins[d2] else d2,
                             d2 if wins[d1] >= wins[d2] else d1, _LCS, pidx, rng)
        champs[lg_id] = champ
        out["pennant"].append(champ)
    lg_ids = list(champs)
    if len(lg_ids) == 2:
        a, b = champs[lg_ids[0]], champs[lg_ids[1]]
        hi, lo = (a, b) if wins[a] >= wins[b] else (b, a)
        out["ws"] = _play_series(hi, lo, _WS, pidx, rng)
    out["wins"] = {tid: wins[tid] for tid in stand}
    out["bat"] = {pid: dict(s) for pid, s in _G["season_bat"].items()}
    out["pit"] = {pid: dict(s) for pid, s in _G["season_pit"].items()}
    return out


def _init_worker(shared):
    _G.update(shared)


def _chunk_seasons(args):
    """Run a chunk of seasons in a worker; return merged aggregates for the chunk.
    Plain dicts only — defaultdicts with lambda factories don't pickle back."""
    base_seed, k = args
    agg = _new_agg()
    for i in range(k):
        _merge_season(agg, _sim_one_season(base_seed + i))
    return _plainify(agg)


def _plainify(agg):
    out = {"n": agg["n"]}
    for key in ("division", "playoffs", "pennant", "ws", "wins_sum", "wins_sq"):
        out[key] = dict(agg[key])
    out["wins_hist"] = {tid: dict(h) for tid, h in agg["wins_hist"].items()}
    for grp in ("bat", "pit"):
        out[grp] = {pid: dict(line) for pid, line in agg[grp].items()}
    return out


def _new_agg():
    return {"n": 0, "division": defaultdict(int), "playoffs": defaultdict(int),
            "pennant": defaultdict(int), "ws": defaultdict(int),
            "wins_sum": defaultdict(int), "wins_sq": defaultdict(int),
            "wins_hist": defaultdict(lambda: defaultdict(int)),
            "bat": defaultdict(lambda: defaultdict(float)),
            "pit": defaultdict(lambda: defaultdict(float))}


def _merge_season(agg, s):
    agg["n"] += 1
    for tid in s["division"]:
        agg["division"][tid] += 1
    for tid in s["playoffs"]:
        agg["playoffs"][tid] += 1
    for tid in s["pennant"]:
        agg["pennant"][tid] += 1
    if s["ws"] is not None:
        agg["ws"][s["ws"]] += 1
    for tid, w in s["wins"].items():
        agg["wins_sum"][tid] += w
        agg["wins_sq"][tid] += w * w
        agg["wins_hist"][tid][w] += 1
    for pid, line in s["bat"].items():
        d = agg["bat"][pid]
        for k, v in line.items():
            d[k] += v
    for pid, line in s["pit"].items():
        d = agg["pit"][pid]
        for k, v in line.items():
            d[k] += v


def _merge_agg(a, b):
    a["n"] += b["n"]
    for key in ("division", "playoffs", "pennant", "ws", "wins_sum", "wins_sq"):
        for tid, v in b[key].items():
            a[key][tid] += v
    for tid, hist in b["wins_hist"].items():
        for w, c in hist.items():
            a["wins_hist"][tid][w] += c
    for grp in ("bat", "pit"):
        for pid, line in b[grp].items():
            d = a[grp][pid]
            for k, v in line.items():
                d[k] += v
    return a


def team_detail(agg, season, tid):
    """Per-player simulated SEASON stat lines for one team from a deep run —
    each player's mean line over the N simulated seasons."""
    n = agg.get("n") or 1
    prof = deep_data.team_profile(tid, season)
    meta = agg.get("meta", {}).get(tid, {})

    def bat_line(p):
        s = agg["bat"].get(p["id"])
        if not s or s["pa"] < 1:
            return None
        ab = s["ab"] / n
        return {"name": p["name"], "side": p["side"],
                "pa": round(s["pa"] / n), "ab": round(ab),
                "h": round(s["h"] / n), "hr": round(s["hr"] / n),
                "2b": round(s["2b"] / n), "3b": round(s["3b"] / n),
                "bb": round(s["bb"] / n), "k": round(s["k"] / n),
                "r": round(s["r"] / n), "rbi": round(s["rbi"] / n),
                "avg": round(s["h"] / s["ab"], 3) if s["ab"] else 0}

    def pit_line(p):
        s = agg["pit"].get(p["id"])
        if not s or s["outs"] < 1:
            return None
        ip = s["outs"] / 3 / n
        return {"name": p["name"], "hand": p["hand"],
                "ip": round(ip, 1), "k": round(s["k"] / n), "bb": round(s["bb"] / n),
                "h": round(s["h"] / n), "hr": round(s["hr"] / n), "r": round(s["r"] / n),
                "era": round(9 * (s["r"] / n) / ip, 2) if ip else None,
                "role": "SP" if p in prof["rotation"] else "RP"}

    batting = [b for b in (bat_line(p) for p in prof["lineup"] + prof["bench"]) if b]
    pitching = [p for p in (pit_line(x) for x in prof["rotation"] + prof["bullpen"]) if p]
    batting.sort(key=lambda r: r["pa"], reverse=True)
    pitching.sort(key=lambda r: r["ip"], reverse=True)
    return {"team": meta.get("name"), "n_sims": n,
            "batting": batting, "pitching": pitching}


# Live progress shared with the API while a run is in flight.
PROGRESS = {"running": False, "done": 0, "total": 0, "started": 0.0, "season": None}


def run_deep(season=None, n_seasons=600, workers=None):
    """Run the deep season Monte Carlo across processes; return aggregates keyed by
    team id (counts) and player id (summed season lines)."""
    season = season or str(__import__("datetime").date.today().year)
    stand = season_sim._standings(season)
    games = season_sim._remaining_games(season)
    games = [(h, a) for (h, a) in games if h in stand and a in stand]
    tids = list(stand)
    leagues = defaultdict(list)
    for tid in tids:
        leagues[stand[tid]["league"]].append(tid)
    shared = {"standings": stand, "schedule": games, "profiles": _profiles(season, tids),
              "leagues": dict(leagues)}

    workers = workers or min(mp.cpu_count(), 8)
    PROGRESS.update(running=True, done=0, total=n_seasons, started=time.time(), season=season)
    # Many small chunks (≈5 per worker) so the progress counter advances smoothly
    # as imap_unordered returns them, not in a few big jumps.
    per = max(1, n_seasons // (workers * 5) or 1)
    chunks, seed = [], random.randrange(1 << 30)
    assigned = 0
    while assigned < n_seasons:
        k = min(per, n_seasons - assigned)
        chunks.append((seed + assigned * 9973, k))
        assigned += k

    agg = _new_agg()
    try:
        if workers > 1:
            with mp.Pool(workers, initializer=_init_worker, initargs=(shared,)) as pool:
                for part in pool.imap_unordered(_chunk_seasons, chunks):
                    _merge_agg(agg, part)
                    PROGRESS["done"] = agg["n"]
        else:
            _init_worker(shared)
            for ch in chunks:
                _merge_agg(agg, _chunk_seasons(ch))
                PROGRESS["done"] = agg["n"]
    finally:
        PROGRESS["running"] = False
    agg["season"] = season
    agg["n_games_left"] = len(games)
    agg["meta"] = {tid: {"name": stand[tid]["name"], "division": stand[tid]["division"],
                         "league": stand[tid]["league"], "wins": stand[tid]["wins"],
                         "losses": stand[tid]["losses"]} for tid in tids}
    return agg
