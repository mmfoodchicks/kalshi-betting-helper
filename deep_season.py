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
import os

import clock
import random
import time
from collections import defaultdict

import deep_data
import deep_sim
import season_sim
import errlog

# Postseason series lengths (wins needed): WC bo3, DS bo5, LCS/WS bo7.
_WC, _DS, _LCS, _WS = 2, 3, 4, 4

_G = {}   # worker globals (set via initializer): profiles, schedule, standings, structure

# Measured peak PSS -- the figure a container's memory limit actually enforces,
# unlike summed RSS, which double-counts the copy-on-write pages forked workers
# share and overstates a pool by several times.
#   app idle 35 MB | workers=1 336 MB | workers=2 613 MB | workers=4 562 MB
# So each forked worker costs roughly this much on top of the app.
_MB_PER_WORKER = 140
_MB_RESERVE = 220          # app, request threads, and the parent's own copy


def _cgroup_limit(v2_path, v1_path, parse):
    for p in (v2_path, v1_path):
        try:
            with open(p) as f:
                v = parse(f.read().strip())
            if v:
                return v
        except Exception:
            continue
    return None


def default_workers():
    """How many workers actually FIT, rather than how many cores the machine has.

    multiprocessing.cpu_count() reports the HOST's cores, not the cgroup's quota.
    On a small container that means eight workers started on half a core and
    against a 512 MB cap, which is how a deep run gets OOM-killed instead of
    merely being slow -- and it fails confusingly, because the web app survives
    and only the sim dies. So read the container's real CPU and memory limits and
    size the pool to whichever binds first.

    VIGIL_SIM_WORKERS overrides everything, including on hosts that expose no
    limits at all."""
    env = os.environ.get("VIGIL_SIM_WORKERS")
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            pass
    cap = mp.cpu_count()

    def _cpu(s):
        # cgroup v2 "max 100000" (unlimited) or "50000 100000" (half a core)
        parts = s.split()
        if parts and parts[0] not in ("max", "-1"):
            try:
                quota = float(parts[0])
                period = float(parts[1]) if len(parts) > 1 else 100000.0
                return max(1, int(quota / period)) if quota > 0 else None
            except Exception:
                return None
        return None

    cpu = _cgroup_limit("/sys/fs/cgroup/cpu.max",
                        "/sys/fs/cgroup/cpu/cpu.cfs_quota_us", _cpu)
    if cpu:
        cap = min(cap, cpu)

    def _mem(s):
        if s in ("max", "-1"):
            return None
        try:
            b = int(s)
        except ValueError:
            return None
        # cgroup v1 reports a huge sentinel when unlimited
        return b if 0 < b < (1 << 62) else None

    mem = _cgroup_limit("/sys/fs/cgroup/memory.max",
                        "/sys/fs/cgroup/memory/memory.limit_in_bytes", _mem)
    if mem:
        room = (mem / (1024 * 1024)) - _MB_RESERVE
        cap = min(cap, max(1, int(room // _MB_PER_WORKER)))
    return max(1, min(cap, 8))


def _profiles(season, tids):
    return {tid: deep_data.team_profile(tid, season) for tid in tids}


def profile_quality(profiles):
    """How complete the data behind these profiles is, 0-1 on career coverage.

    The roster call hydrates season AND career stats; career is what regresses a
    player toward true talent. A partial hydration silently drops it, every rate
    is taken at face value, and the projection tracks each team's RECORD rather
    than its talent -- while still rendering as a confident board. Measured on a
    healthy build this sits near 0.96."""
    players = career = 0
    teams_ok = 0
    for prof in profiles.values():
        q = (prof or {}).get("_quality") or {}
        players += q.get("players", 0)
        career += q.get("with_career", 0)
        if q.get("players") and q.get("with_career", 0) / q["players"] >= 0.5:
            teams_ok += 1
    return {"players": players, "with_career": career,
            "career_frac": round(career / players, 3) if players else 0.0,
            "teams_ok": teams_ok, "teams": len(profiles)}


# How many starters a club actually uses in a series of each length (keyed by
# wins needed). Nobody's fifth starter throws a playoff inning. The Wild Card is
# three games on three consecutive days, so it needs three arms -- the ace cannot
# come back for Game 3 on zero rest. A best-of-five has an off day either side of
# the middle games and is covered by three; a best-of-seven by four, with the ace
# back for Game 5 on normal rest. The regular-season list runs six deep, so
# cycling it through October handed real playoff starts to arms that in reality
# watch from the bullpen -- and it cost the deep staffs most, because a club
# four aces deep is exactly the one whose #5 and #6 drag it furthest down.
_PO_ARMS = {2: 3, 3: 3, 4: 4}


def _po_rotation(tid, need):
    """The arms team `tid` actually starts in a series of this length, best first."""
    return _G["po_rot"][tid][need]


def _play_series(a, b, need, rng, seed=None, tag=""):
    """Best-of (2*need-1) between team ids a (higher seed, home edge) and b.
    Each club opens with its ace and works down its playoff rotation. Returns
    the winning team id.

    With `seed`, the series draws from its OWN stream keyed by (seed, round,
    both teams). A series is the one part of the sim that consumes a VARIABLE
    number of draws -- a sweep uses far fewer than a game seven -- so on a shared
    stream one short series shifts every later draw in the bracket, including the
    other league's. That desynchronisation is what made paired counterfactual
    runs drift on teams nobody touched. Keyed per series, the same two clubs in
    the same round play the same way unless something about THEM changed."""
    wins = {a: 0, b: 0}
    pa, pb = _G["profiles"][a], _G["profiles"][b]
    rot_a, rot_b = _po_rotation(a, need), _po_rotation(b, need)
    # Which games the higher seed hosts, by MLB's real format: the Wild Card is
    # played entirely at the higher seed's park, the Division Series is 2-2-1 and
    # the LCS/World Series 2-3-2. One flat 2-3-2 pattern (the old behaviour) gave
    # the top seed 2 of 3 in the WC instead of 3, and handed away Game 5 of the DS.
    homes = season_sim._HOME_GAMES.get(need, ())
    if seed is not None:
        rng = random.Random(f"{seed}|{tag}|{a}|{b}")
    g = 0
    while wins[a] < need and wins[b] < need:
        home_is_a = (g + 1) in homes           # home team bats last / has the edge
        # Each series resets to the ace: rotations line up on the off days between
        # rounds, they do not carry an index over from August.
        sa, sb = rot_a[g % len(rot_a)], rot_b[g % len(rot_b)]
        if home_is_a:
            res = deep_sim.play_game(pa, pb, sa, sb, rng)
            winner = a if res["home_win"] else b
        else:
            res = deep_sim.play_game(pb, pa, sb, sa, rng)
            winner = b if res["home_win"] else a
        wins[winner] += 1
        _accum_box(res)
        g += 1
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
    # Rotation PHASE from reality. ridx used to start at 0 for all 30 clubs, so
    # every simulated remaining season opened with every team's ACE on the
    # mound -- a bias for exactly the near-term games that series odds and the
    # calendar's next few days care about. rotation.next_starter_ids() names
    # each club's actual next arm (announced probable, else rest-cadence
    # projection) and the cycle starts there. Shared read-only across workers.
    ridx = defaultdict(int)
    for tid, phase in (_G.get("rot_phase") or {}).items():
        ridx[tid] = phase
    # Each game draws from its OWN stream, seeded from (season seed, game index).
    #
    # This is what makes deep_history's attribution possible. With one shared
    # stream, changing a single player's availability shifts every subsequent
    # draw in the season, so two runs on the same seed diverge everywhere and
    # teams nobody touched drift by up to 3pp -- larger than the effect being
    # measured. Per-game streams keep every game a team plays against unchanged
    # opponents bit-identical between the two runs, so the difference that
    # survives is the change itself. Measured: untouched-team drift falls from a
    # 3.00pp tail to 0.
    #
    # The schedule is fixed and identical across runs, so the game index is a
    # stable key. Re-seeding one Random is cheaper than constructing one per game.
    grng = random.Random()
    for gi, (home, away) in enumerate(games):
        grng.seed(seed * 7919 + gi)
        ph, pa = profiles.get(home), profiles.get(away)
        if not ph or not pa:
            wins[home if grng.random() < 0.5 else away] += 1
            continue
        sh = ph["rotation"][ridx[home] % len(ph["rotation"])] if ph["rotation"] else None
        sa = pa["rotation"][ridx[away] % len(pa["rotation"])] if pa["rotation"] else None
        ridx[home] += 1; ridx[away] += 1
        res = deep_sim.play_game(ph, pa, sh, sa, grng)
        wins[(home if res["home_win"] else away)] += 1
        _accum_box(res)

    # Postseason bracket per league, then World Series.
    out = {"division": [], "playoffs": [], "pennant": [], "ws": None}
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
        w36 = _play_series(seeds[2], seeds[5], _WC, rng, seed, "wc36")
        w45 = _play_series(seeds[3], seeds[4], _WC, rng, seed, "wc45")
        ds_lo, ds_hi = (w36, w45) if seeds.index(w36) > seeds.index(w45) else (w45, w36)
        d1 = _play_series(seeds[0], ds_lo, _DS, rng, seed, "ds1")
        d2 = _play_series(seeds[1], ds_hi, _DS, rng, seed, "ds2")
        champ = _play_series(d1 if wins[d1] >= wins[d2] else d2,
                             d2 if wins[d1] >= wins[d2] else d1, _LCS, rng,
                             seed, "lcs")
        champs[lg_id] = champ
        out["pennant"].append(champ)
    lg_ids = list(champs)
    if len(lg_ids) == 2:
        a, b = champs[lg_ids[0]], champs[lg_ids[1]]
        hi, lo = (a, b) if wins[a] >= wins[b] else (b, a)
        out["ws"] = _play_series(hi, lo, _WS, rng, seed, "ws")
    out["wins"] = {tid: wins[tid] for tid in stand}
    out["bat"] = {pid: dict(s) for pid, s in _G["season_bat"].items()}
    out["pit"] = {pid: dict(s) for pid, s in _G["season_pit"].items()}
    return out


def _build_po_rot(profiles):
    """{team_id: {wins_needed: [starter, ...]}} — each club's October rotation.

    Built once per worker rather than per series: sorting six arms 4,000 seasons
    x 13 series deep would cost more than the games themselves. Ranked by
    deep_data.arm_quality, the same scale the bullpen order uses, so "best arm"
    means one thing across the app.
    """
    out = {}
    for tid, prof in profiles.items():
        rot = (prof or {}).get("rotation") or []
        if not rot:
            out[tid] = {need: [None] for need in _PO_ARMS}
            continue
        best = sorted(rot, key=deep_data.arm_quality, reverse=True)
        out[tid] = {need: best[:min(len(best), k)] for need, k in _PO_ARMS.items()}
    return out


def _init_worker(shared):
    # Same reasoning as the slate child: a 4,000-season run saturates every CPU
    # it is given for the better part of an hour. Niced, it yields instantly to
    # the web worker, so the nightly job cannot starve the health probe into a
    # five-second timeout and get the instance restarted mid-run -- which also
    # means the run actually finishes.
    try:
        os.nice(10)
    except Exception as _e:
        errlog.note("DS-init_worker", _e)
    _G.update(shared)
    _G["po_rot"] = _build_po_rot(_G["profiles"])


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
    # Taxi-squad bats (optioned to the minors) appear only when the sim actually
    # called them up — bat_line returns None for anyone with no simulated PA.
    batting += [b for b in (bat_line(p, "taxi") for p in prof.get("depth_bats", [])) if b]
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

# ...and mirrored to a file, because "the API" is three workers and the run
# happens in one of them. Read from memory, two of every three status polls
# reported no run in flight while 4,000 seasons were simulating -- the loading
# bar flickered or never appeared, and a just-clicked rerun looked like a dead
# button. Same disk-not-memory cure as everything else that crosses workers.
_PROG_DISK = os.path.join(os.environ.get("VIGIL_RUN_DIR") or "/tmp",
                          "vigil-deep-progress.json")
_PROG_FLUSHED = [0.0]


def _prog_flush(final=False):
    now = time.time()
    if not final and now - _PROG_FLUSHED[0] < 1.0:
        return                       # a chunk lands every few seconds; that's enough
    _PROG_FLUSHED[0] = now
    try:
        import json
        import tempfile
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(_PROG_DISK) or ".",
                                   suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(PROGRESS, fh)
        os.replace(tmp, _PROG_DISK)
    except Exception as _e:
        errlog.note("DS-prog_flush", _e)


def progress_read():
    """The freshest progress from ANY worker: this process's if it is the one
    running, else the mirror -- ignoring a mirror stale enough to be a dead
    run's leftover (a live run updates it every few seconds)."""
    if PROGRESS.get("running"):
        return dict(PROGRESS)
    try:
        if time.time() - os.stat(_PROG_DISK).st_mtime < 300:
            import json
            with open(_PROG_DISK, encoding="utf-8") as fh:
                disk = json.load(fh) or {}
            if disk.get("running"):
                return disk
    except OSError:
        pass                # no mirror yet (fresh boot) -- the normal cold case
    except Exception as _e:
        errlog.note("DS-progress_read", _e)
    return dict(PROGRESS)


def run_deep(season=None, n_seasons=600, workers=None, seed=None, profiles=None,
             track_progress=True, ret_profiles=None, isolate=True):
    """Run the deep season Monte Carlo across processes; return aggregates keyed by
    team id (counts) and player id (summed season lines).

    `isolate` (default) runs even a ONE-worker sim in a child process. Inline,
    the working set settles into the caller -- on the server that is a gunicorn
    worker, which then also wears the os.nice(10) meant for a sim child for the
    rest of its life. Measured 2026-09-05 (08:56 ET): Render's one-core quota
    sizes the pool to one worker, the button took the inline path inside the
    web worker beside a slate child, and the instance was gone inside a minute.
    A child's memory dies with it and its nice touches nothing else.

    `seed` pins the season seeds. Two runs sharing a seed play the SAME simulated
    seasons, so differencing them isolates whatever input was changed between them
    and cancels almost all the Monte Carlo noise -- common random numbers, which is
    what makes deep_history's per-player attribution affordable. Leave it None for
    the nightly run, where independent draws are what you want.

    `profiles` overrides the built team profiles ({team_id: profile}), which is how
    a counterfactual reverts one player. `ret_profiles`, if a dict is passed, is
    filled with the profiles actually used, so a caller can snapshot them without
    paying for the roster fetch twice.

    `track_progress=False` keeps a background counterfactual from clobbering the
    loading bar that belongs to the user-facing run."""
    season = season or str(clock.today_et().year)

    def _prog(**kw):
        if track_progress:
            PROGRESS.update(**kw)
            _prog_flush()
    # Flag running immediately so the loading bar appears during the (network)
    # roster/standings prep, not only once the sim chunks start.
    _prog(running=True, done=0, total=n_seasons, started=time.time(), season=season)
    stand = season_sim._standings(season)
    games = season_sim._remaining_games(season)
    games = [(h, a) for (h, a) in games if h in stand and a in stand]
    tids = list(stand)
    leagues = defaultdict(list)
    for tid in tids:
        leagues[stand[tid]["league"]].append(tid)
    profs = _profiles(season, tids) if profiles is None else profiles
    if ret_profiles is not None:
        ret_profiles.update(profs)
    # Each club's rotation phase: the index (in its profile's rotation list) of
    # the arm actually lined up to start next, from announced probables backed
    # by rest-cadence projection. Best-effort: an empty map reproduces the old
    # ace-first behaviour rather than failing the run.
    rot_phase = {}
    try:
        import rotation
        nxt = rotation.next_starter_ids()
        for tid, prof in profs.items():
            pid = nxt.get(tid)
            rot = prof.get("rotation") or []
            for i, arm in enumerate(rot):
                if arm.get("id") == pid:
                    rot_phase[tid] = i
                    break
    except Exception:
        rot_phase = {}

    shared = {"standings": stand, "schedule": games, "profiles": profs,
              "leagues": dict(leagues), "rot_phase": rot_phase}

    workers = workers or default_workers()
    _prog(running=True, done=0, total=n_seasons, started=time.time(), season=season)
    # Many small chunks (~20 per worker) so the loading bar advances smoothly over
    # a long 4,000-season run instead of jumping in a few big steps.
    per = max(1, n_seasons // (workers * 20) or 1)
    chunks = []
    seed = random.randrange(1 << 30) if seed is None else int(seed)
    assigned = 0
    while assigned < n_seasons:
        k = min(per, n_seasons - assigned)
        chunks.append((seed + assigned * 9973, k))
        assigned += k

    agg = _new_agg()
    try:
        if workers > 1 or isolate:
            with mp.Pool(max(1, workers), initializer=_init_worker,
                         initargs=(shared,)) as pool:
                for part in pool.imap_unordered(_chunk_seasons, chunks):
                    _merge_agg(agg, part)
                    _prog(done=agg["n"])
        else:
            _init_worker(shared)
            for ch in chunks:
                _merge_agg(agg, _chunk_seasons(ch))
                _prog(done=agg["n"])
    finally:
        if track_progress:
            PROGRESS["running"] = False
            _prog_flush(final=True)
    # Convert to plain dicts so the result is picklable for the disk cache
    # (defaultdicts with lambda factories — wins_hist/bat/pit — can't pickle).
    agg = _plainify(agg)
    agg["season"] = season
    agg["n_games_left"] = len(games)
    agg["meta"] = {tid: {"name": stand[tid]["name"], "division": stand[tid]["division"],
                         "league": stand[tid]["league"], "wins": stand[tid]["wins"],
                         "losses": stand[tid]["losses"]} for tid in tids}
    agg["quality"] = profile_quality(profs)
    return agg
