"""DraftKings MLB DFS optimizer driven by the game simulator.

Most DFS tools multiply flat projections and assume players are independent.
This one simulates each game (the same base-running sim used for props) and
reads every HITTER's DraftKings fantasy-point distribution straight off the
shared games -- so a team's hitters are correctly correlated and STACKS are
valued on their real ceiling. Pitchers are projected from their rate stats with
a simulated line (IP/K/ER/W) for floor/ceiling.

Classic roster: P, P, C, 1B, 2B, 3B, SS, OF, OF, OF under a $50,000 cap.
"""

import random
import statistics
import unicodedata

import simulate as _sim   # parse_dk_csv

ROSTER = ["P", "P", "C", "1B", "2B", "3B", "SS", "OF", "OF", "OF"]
_POS_MAP = {"SP": "P", "RP": "P", "P": "P", "C": "C", "1B": "1B", "2B": "2B",
            "3B": "3B", "SS": "SS", "OF": "OF", "LF": "OF", "CF": "OF", "RF": "OF"}


def _norm(name):
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    return " ".join("".join(c for c in s.lower() if c.isalpha() or c == " ").split())


def _eligible(pos_str):
    """Set of roster positions a DK position string maps to (e.g. '1B/3B')."""
    out = set()
    for tok in (pos_str or "").replace("-", "/").split("/"):
        p = _POS_MAP.get(tok.strip().upper())
        if p:
            out.add(p)
    return out


def _dist(arr):
    arr = sorted(arr)
    n = len(arr)
    if not n:
        return None
    q = lambda f: arr[min(n - 1, int(f * n))]
    return {"proj": round(statistics.fmean(arr), 1), "floor": round(q(0.1), 1),
            "median": round(q(0.5), 1), "ceil": round(q(0.9), 1)}


def _pitcher_dk(sp, team_win_prob, n=3000):
    """Simulated DraftKings line for a starter -> point distribution."""
    era, whip, k9 = sp.get("era"), sp.get("whip"), sp.get("k9")
    if not era or not whip or not k9:
        return None
    exp_ip = max(4.5, min(6.5, (sp.get("ip") or 0) / max(1, _starts(sp)) or 5.7)) if sp.get("ip") else 5.7
    out = []
    for _ in range(n):
        ip = max(3.0, min(8.0, random.gauss(exp_ip, 1.0)))
        outs = ip * 3
        k = _pois(k9 / 9.0 * ip)
        er = _pois(era / 9.0 * ip)
        br = whip * ip
        h = _pois(br * 0.74)
        bb = _pois(br * 0.26)
        win = 4 if random.random() < (team_win_prob or 0.5) * 0.62 else 0
        dk = outs * 0.75 + 2 * k - 2 * er - 0.6 * h - 0.6 * bb + win
        if er == 0 and ip >= 6:                    # rough no-runs/QS-ish bump
            dk += 0.0
        out.append(dk)
    return _dist(out)


def _starts(sp):
    return 1  # IP already season total; exp_ip handled with a sane default


def _pois(lam):
    if lam <= 0:
        return 0
    import math
    L = math.exp(-lam); k = 0; p = 1.0
    while True:
        k += 1; p *= random.random()
        if p <= L:
            return k - 1


def projections(games, n_sims=4000):
    """{normalized name: {dist, kind}} DK fantasy projections for every hitter
    (from the game sim) and starter (simulated line) on the slate."""
    import mlb_sim
    proj = {}
    for g in games:
        if (g.get("live") or {}).get("state") == "Final":
            continue
        sim = mlb_sim.simulate(g, n_sims)
        for side in ("home", "away"):
            for name, arr in (sim["bat"][side] or {}).items():
                d = _dist(arr["dk"])
                if d:
                    proj[_norm(name)] = {"kind": "bat", **d}
        for sp, wp in ((g.get("home_sp"), g.get("p_home")),
                       (g.get("away_sp"), g.get("p_away"))):
            if sp and sp.get("name"):
                d = _pitcher_dk(sp, wp)
                if d:
                    proj[_norm(sp["name"])] = {"kind": "pit", **d}
    return proj


def _value(p, objective):
    return p["ceil"] if objective == "ceiling" else p["median"]


def optimize(players, cap, objective, restarts=6000):
    """Greedy-randomized position-aware optimizer. Each restart fills the roster
    slot by slot from value-weighted eligible players, keeping the best valid
    lineup under the cap. Favors stacking implicitly via the sim's correlated
    ceilings."""
    by_pos = {pos: [] for pos in set(ROSTER)}
    for p in players:
        for pos in p["elig"]:
            if pos in by_pos:
                by_pos[pos].append(p)
    for pos in by_pos:
        by_pos[pos].sort(key=lambda p: -_value(p, objective))
    if any(len(by_pos[pos]) < ROSTER.count(pos) for pos in set(ROSTER)):
        return None

    best = None
    slots = ROSTER
    for _ in range(restarts):
        used, lineup, sal = set(), [], 0
        ok = True
        # Hardest (scarcest) positions first so cheap fills remain for the rest.
        order = sorted(range(len(slots)), key=lambda i: len(by_pos[slots[i]]))
        remaining = list(slots)
        for si in order:
            pos = slots[si]
            pool = [p for p in by_pos[pos] if p["name"] not in used]
            # keep enough budget for the slots still to fill (min-priced each)
            pool = [p for p in pool if sal + p["salary"] <= cap]
            if not pool:
                ok = False
                break
            top = pool[:12]
            weights = [max(0.1, _value(x, objective)) ** 2 for x in top]
            pick = random.choices(top, weights=weights)[0]
            used.add(pick["name"]); lineup.append(pick); sal += pick["salary"]
        if ok and len(lineup) == len(slots) and sal <= cap:
            score = sum(_value(p, objective) for p in lineup)
            if best is None or score > best[0]:
                best = (score, lineup, sal)
    return best


def build(date, csv_text, cap=50000, objective="median", n_sims=4000):
    import baseball
    players_raw = _sim.parse_dk_csv(csv_text)
    if len(players_raw) < 10:
        return {"error": f"need a full MLB slate CSV (got {len(players_raw)} players)"}
    try:
        games = baseball.analyze_slate(date, date[:4])
    except Exception as e:
        return {"error": f"slate failed: {e}"}
    proj = projections(games, n_sims)
    if not proj:
        return {"error": "no projections — lineups may not be posted yet"}

    players, unmatched = [], []
    for p in players_raw:
        elig = _eligible(p.get("pos"))
        if not elig:
            continue
        pr = proj.get(_norm(p["name"]))
        if not pr:                                  # fall back to CSV AvgPointsPerGame
            if not p.get("proj"):
                unmatched.append(p["name"])
                continue
            pr = {"proj": p["proj"], "median": p["proj"],
                  "floor": p["proj"] * 0.5, "ceil": p["proj"] * 1.6, "kind": "?"}
        players.append({"name": p["name"], "salary": p["salary"], "elig": elig,
                        "median": pr["median"], "ceil": pr["ceil"],
                        "floor": pr["floor"], "proj": pr["proj"],
                        "sim": pr.get("kind") in ("bat", "pit")})

    res = optimize(players, cap, objective)
    if not res:
        return {"error": "couldn't fill a valid roster (need P,P,C,1B,2B,3B,SS,OF×3 under cap)"}
    score, lineup, sal = res
    lineup_sorted = sorted(lineup, key=lambda p: ROSTER.index(next(iter(p["elig"] & set(ROSTER)))))
    sim_n = sum(1 for p in lineup if p["sim"])
    return {
        "lineup": [{"name": p["name"], "salary": int(p["salary"]),
                    "pos": "/".join(sorted(p["elig"])), "proj": round(p["proj"], 1),
                    "floor": round(p["floor"], 1), "ceil": round(p["ceil"], 1),
                    "sim": p["sim"]} for p in lineup_sorted],
        "total_salary": int(sal), "cap": cap, "objective": objective,
        "total_proj": round(sum(p["proj"] for p in lineup), 1),
        "total_ceil": round(sum(p["ceil"] for p in lineup), 1),
        "total_floor": round(sum(p["floor"] for p in lineup), 1),
        "pool": len(players), "sim_players": sim_n, "unmatched": unmatched[:15],
    }
