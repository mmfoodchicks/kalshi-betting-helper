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
        for k in ("pa", "ab", "h", "2b", "3b", "hr", "bb", "k", "r", "rbi", "sb", "ph"):
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
        ("pa", "ab", "h", "2b", "3b", "hr", "bb", "k", "r", "rbi", "sb", "ph"), 0))
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
    """Per-player stat lines for one team: each player's SIMULATED rest-of-season
    line (mean over N deep seasons) alongside his REAL current-season numbers, plus
    injured players the sim leaves out — surfaced at the bottom with their real
    stats until they come off the IL and rejoin the active group up top."""
    n = agg.get("n") or 1
    prof = deep_data.team_profile(tid, season)
    meta = agg.get("meta", {}).get(tid, {})
    try:
        real = deep_data.roster_lines(tid, season)
    except Exception:
        real = {}

    # The deep run simulates the REMAINING schedule, so the per-player sim
    # totals are rest-of-season only. The futures view promises "where does he
    # finish the season" — the headline must be CURRENT + simulated remainder
    # (a projection can never sit below what's already banked: showing 47 Ks
    # against a real 66 reads as nonsense). The real line stays in `real` for
    # the parenthetical, untouched.
    def bat_line(p, role="starter"):
        s = agg["bat"].get(p["id"])
        if not s or s["pa"] < 1:
            return None
        r = real.get(p["id"], {})
        rb = r.get("bat") or {}
        def tot(key):
            return round((rb.get(key) or 0) + s.get(key, 0) / n)
        ab_t = (rb.get("ab") or 0) + s["ab"] / n
        h_t = (rb.get("h") or 0) + s["h"] / n
        # Rate stats are recomputed from the MERGED counting line, so they move
        # in BOTH directions: a hot sim stretch pulls season-end AVG/OPS above
        # current, a cold one drags it below. Merged OPS is built from TB +
        # walks only (the sim lines carry no HBP/SF), so it reads a few points
        # shy of the official OPS shown in the parenthetical.
        def _tb(d, div=1.0):
            h = d.get("h", 0) / div; d2 = d.get("2b", 0) / div
            d3 = d.get("3b", 0) / div; hr = d.get("hr", 0) / div
            return (h - d2 - d3 - hr) + 2 * d2 + 3 * d3 + 4 * hr
        tb_t = _tb(rb) + _tb(s, n)
        bb_t = (rb.get("bb") or 0) + s["bb"] / n
        obp_t = (h_t + bb_t) / (ab_t + bb_t) if (ab_t + bb_t) else 0
        slg_t = tb_t / ab_t if ab_t else 0
        return {"name": p["name"], "side": p["side"], "id": p["id"], "role": role,
                "il": bool(r.get("il")), "status": r.get("status"), "has_sim": True,
                "pa": tot("pa"), "ab": round(ab_t),
                "h": round(h_t), "hr": tot("hr"),
                "2b": tot("2b"), "3b": tot("3b"),
                "bb": round(bb_t), "k": tot("k"),
                "r": tot("r"), "rbi": tot("rbi"),
                "sb": tot("sb"),
                "ph_g": round(s.get("ph", 0) / n, 1),
                "avg": round(h_t / ab_t, 3) if ab_t else 0,
                "ops": round(obp_t + slg_t, 3),
                "real": r.get("bat")}

    def pit_line(p):
        s = agg["pit"].get(p["id"])
        if not s or s["outs"] < 1:
            return None
        r = real.get(p["id"], {})
        rp = r.get("pit") or {}
        # Real IP arrives as MLB's "101.2" (innings.outs) string.
        try:
            whole, _, thirds = str(rp.get("ip") or "0").partition(".")
            rip = float(whole) + (float(thirds) / 3 if thirds else 0)
        except Exception:
            rip = 0.0
        sim_ip = s["outs"] / 3 / n
        ip_t = rip + sim_ip
        # Season-end ERA: real earned runs (era*ip/9) + the sim's runs (the
        # sim already reports runs-as-earned; same approximation as before).
        rer = (rp.get("era") or 0) * rip / 9.0
        er_t = rer + s["r"] / n
        def tot(key):
            return round((rp.get(key) or 0) + s.get(key, 0) / n)
        # WHIP / FIP from the merged line — free to land above OR below the
        # current number depending on how the sim sees the rest of his season.
        # FIP constant 3.15 (league-typical); no HBP in the sim lines, so both
        # our season-end FIP and the real-side FIP shown beside it skip it.
        h_t = (rp.get("h") or 0) + s["h"] / n
        bb_t = (rp.get("bb") or 0) + s["bb"] / n
        k_t = (rp.get("k") or 0) + s["k"] / n
        hr_t = (rp.get("hr") or 0) + s["hr"] / n
        real_out = dict(rp) if rp else None
        if real_out and rip:
            if not real_out.get("whip"):
                real_out["whip"] = round(((rp.get("bb") or 0) + (rp.get("h") or 0)) / rip, 2)
            real_out["fip"] = round((13 * (rp.get("hr") or 0) + 3 * (rp.get("bb") or 0)
                                     - 2 * (rp.get("k") or 0)) / rip + 3.15, 2)
        return {"name": p["name"], "hand": p["hand"], "id": p["id"],
                "il": bool(r.get("il")), "status": r.get("status"), "has_sim": True,
                "ip": round(ip_t, 1), "k": tot("k"), "bb": tot("bb"),
                "h": tot("h"), "hr": tot("hr"), "r": tot("r"),
                "era": round(9 * er_t / ip_t, 2) if ip_t else None,
                "whip": round((bb_t + h_t) / ip_t, 2) if ip_t else None,
                "fip": round((13 * hr_t + 3 * bb_t - 2 * k_t) / ip_t + 3.15, 2) if ip_t else None,
                "role": "SP" if p in prof["rotation"] else "RP",
                "real": real_out}

    batting = [b for b in (bat_line(p) for p in prof["lineup"]) if b]
    batting += [b for b in (bat_line(p, "bench") for p in prof["bench"]) if b]
    pitching = [p for p in (pit_line(x) for x in prof["rotation"] + prof["bullpen"]) if p]

    # Injured players the sim dropped entirely (e.g. 60-day IL) — show them at the
    # bottom with their real stats only. When they're activated the next rerun puts
    # them back in the sim and they climb into the active group above.
    have = {r["id"] for r in batting} | {r["id"] for r in pitching}
    for pid, r in real.items():
        if pid in have or not r.get("il"):
            continue
        if r.get("bat") and r["bat"]["pa"] >= 1:
            batting.append({"name": r["name"], "id": pid, "il": True,
                            "status": r["status"], "has_sim": False, "real": r["bat"]})
        elif r.get("pit") and r["pit"]["ip"] not in (None, "0.0", "0"):
            pitching.append({"name": r["name"], "id": pid, "il": True, "status": r["status"],
                             "has_sim": False, "role": "P", "real": r["pit"]})

    # Active players first (by playing time), injured players sink to the bottom.
    batting.sort(key=lambda r: (r["il"], -(r.get("pa") or (r.get("real") or {}).get("pa", 0))))
    pitching.sort(key=lambda r: (r["il"], -_ip_num(r)))
    # The measurable pinch hitter: the bench bat the sim actually sends up the
    # most (simulated PH appearances per season).
    ph = max((b for b in batting if b.get("role") == "bench" and (b.get("ph_g") or 0) > 0.5),
             key=lambda b: b["ph_g"], default=None)
    return {"team": meta.get("name"), "n_sims": n,
            "ph_primary": ({"name": ph["name"], "ph_g": ph["ph_g"]} if ph else None),
            "batting": batting, "pitching": pitching}


def _ip_num(r):
    """Sort key for pitcher rows: simulated IP if present, else real IP (raw MLB
    'outs.thirds' string like '45.1')."""
    if r.get("has_sim"):
        return r.get("ip") or 0
    ip = (r.get("real") or {}).get("ip") or "0"
    try:
        whole, _, thirds = str(ip).partition(".")
        return float(whole) + (float(thirds) / 3 if thirds else 0)
    except Exception:
        return 0


# Live progress shared with the API while a run is in flight.
PROGRESS = {"running": False, "done": 0, "total": 0, "started": 0.0, "season": None}


def run_deep(season=None, n_seasons=600, workers=None):
    """Run the deep season Monte Carlo across processes; return aggregates keyed by
    team id (counts) and player id (summed season lines)."""
    season = season or str(__import__("datetime").date.today().year)
    # Flag running immediately so the loading bar appears during the (network)
    # roster/standings prep, not only once the sim chunks start.
    PROGRESS.update(running=True, done=0, total=n_seasons, started=time.time(), season=season)
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
    # Many small chunks (~20 per worker) so the loading bar advances smoothly over
    # a long 4,000-season run instead of jumping in a few big steps.
    per = max(1, n_seasons // (workers * 20) or 1)
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
    # Convert to plain dicts so the result is picklable for the disk cache
    # (defaultdicts with lambda factories — wins_hist/bat/pit — can't pickle).
    agg = _plainify(agg)
    agg["season"] = season
    agg["n_games_left"] = len(games)
    agg["meta"] = {tid: {"name": stand[tid]["name"], "division": stand[tid]["division"],
                         "league": stand[tid]["league"], "wins": stand[tid]["wins"],
                         "losses": stand[tid]["losses"]} for tid in tids}
    return agg
