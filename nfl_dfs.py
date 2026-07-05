"""DraftKings NFL Classic DFS: optimizer + correlated contest sim.

Roster: 1 QB, 2 RB, 3 WR, 1 TE, 1 FLEX (RB/WR/TE), 1 DST -- $50,000 cap.

Projections + per-player score arrays come from the Sleeper-seeded correlated
game sim (nfl_dfs_sim.player_pool): skill players carry the within-game
correlation (a QB and his WRs boom together), DSTs are sampled independently.
Salaries + the actual player pool come from the pasted DKSalaries.csv. Ceiling /
leverage objectives and the top-heavy contest sim mirror the MLB DFS builder;
the pure payout-curve helpers are reused straight from mlb_dfs.
"""

import math
import random

import simulate                     # parse_dk_csv
import nfl_dfs_sim
from mlb_dfs import _gpp_curve, _rank_grid, _ncdf, _npdf   # roster-agnostic helpers

ROSTER = ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "DST"]
FLEX_OK = {"RB", "WR", "TE"}
CAP = 50000


def _elig(pos):
    pos = "RB" if pos == "FB" else (pos or "").upper()
    slots = [pos] if pos in ("QB", "RB", "WR", "TE", "DST") else []
    if pos in FLEX_OK:
        slots.append("FLEX")
    return slots


def _value(p, objective):
    if objective == "ceiling":
        return p["ceiling"]
    if objective == "leverage":
        return p["ceiling"] * (1.0 - 0.007 * p.get("own", 8.0))
    return p["proj"]


def _by_pos(players):
    by = {pos: [] for pos in set(ROSTER)}
    for p in players:
        for slot in p["elig"]:
            if slot in by:
                by[slot].append(p)
    for pos in by:
        by[pos].sort(key=lambda p: -p["proj"])
    if any(len(by[pos]) < ROSTER.count(pos) for pos in set(ROSTER)):
        return None
    return by


def _build_one(by_pos, cap, objective, stack_team=None, stack_min=0, rng=random):
    """One greedy-randomized valid lineup. With a stack, WR/TE/FLEX slots seed from
    the QB's team so the QB and his pass-catchers boom together."""
    used, lineup, sal, stacked = set(), [], 0, 0
    for si in sorted(range(len(ROSTER)), key=lambda i: len(by_pos[ROSTER[i]])):
        pos = ROSTER[si]
        pool = [p for p in by_pos[pos] if p["name"] not in used and sal + p["salary"] <= cap]
        if not pool:
            return None
        cand = pool
        # A real NFL stack is QB + a pass-catcher (WR/TE) from his team -- that's
        # where the sim's correlation lives (a QB's day lifts his receivers).
        if stack_team and pos in ("WR", "TE", "FLEX") and stacked < stack_min:
            team_pool = [p for p in pool if p.get("team") == stack_team and p["pos"] in ("WR", "TE")]
            if team_pool:
                cand = team_pool
        top = cand[:14]
        weights = [max(0.1, _value(x, objective)) ** 2 for x in top]
        pick = rng.choices(top, weights=weights)[0]
        if stack_team and pick.get("team") == stack_team and pick["pos"] in ("WR", "TE"):
            stacked += 1
        used.add(pick["name"]); lineup.append(pick); sal += pick["salary"]
    if len(lineup) != len(ROSTER) or sal > cap or (stack_team and stacked < stack_min):
        return None
    return lineup, sal


def optimize(players, cap, objective, stack_min=0, restarts=8000):
    by_pos = _by_pos(players)
    if by_pos is None:
        return None
    teams = sorted({p.get("team") for p in players if p.get("pos") == "QB" and p.get("team")})
    best = None
    for _ in range(restarts):
        st = random.choice(teams) if (stack_min and teams) else None
        r = _build_one(by_pos, cap, objective, stack_team=st, stack_min=stack_min, rng=random)
        if not r:
            continue
        lineup, sal = r
        score = sum(_value(p, objective) for p in lineup)
        if best is None or score > best[0]:
            best = (score, lineup, sal)
    return best[1] if best else None


# ---- ownership model --------------------------------------------------------
def _set_ownership(players):
    """Rough field ownership from projection value (proj per $1k). Chalk = high
    value; used for the leverage objective and the contest field weighting."""
    for p in players:
        v = p["proj"] / max(1.0, p["salary"] / 1000.0)
        p["_vw"] = max(0.01, v) ** 3.2
    tot = sum(p["_vw"] for p in players) or 1.0
    # Ownership across all players sums to ~9 roster spots x 100%; chalk (high
    # value) gets the bulk. Capped so no single player looks impossibly owned.
    for p in players:
        p["own"] = round(min(45.0, 100.0 * len(ROSTER) * p["_vw"] / tot), 1)


# ---- contest sim (win% / cash% / ROI, any field size) ----------------------
def _field_lineup(by_pos, cap, own_w, rng, tries=8):
    for _ in range(tries):
        used, names, sal, ok = set(), [], 0, True
        for pos in sorted(ROSTER, key=lambda p: len(by_pos[p])):
            pool = [p for p in by_pos[pos] if p["name"] not in used and sal + p["salary"] <= cap]
            if not pool:
                ok = False
                break
            w = [own_w.get(p["name"], 1.0) for p in pool]
            pick = rng.choices(pool, weights=w)[0]
            used.add(pick["name"]); names.append(pick["name"]); sal += pick["salary"]
        if ok and len(names) == len(ROSTER):
            return names
    return None


def contest_sim(your_lineup, players, contest="gpp", entry_fee=1.0, contest_size=None,
                prize_pool=None, first_prize=None, sample_size=500, n_iter=400):
    by_pos = _by_pos(players)
    arr = {p["name"]: p.get("arr") for p in players if p.get("arr")}
    if by_pos is None or not arr:
        return None
    L = min(len(a) for a in arr.values())
    own_w = {p["name"]: max(0.1, p.get("own", 8.0)) for p in players}
    proj_of = {p["name"]: p.get("proj", 0.0) for p in players}
    rng = random.Random(12345)
    ref = sum(proj_of.get(p["name"], 0.0) for p in your_lineup)
    floor_q = 0.84 * ref
    field, attempts = [], 0
    while len(field) < sample_size and attempts < sample_size * 40:
        attempts += 1
        fl = _field_lineup(by_pos, CAP, own_w, rng)
        if fl and sum(proj_of.get(nm, 0.0) for nm in fl) >= floor_q:
            field.append(fl)
    if len(field) < 30:
        return None

    C = max(2, int(contest_size or (len(field) + 1)))
    pool = float(prize_pool) if prize_pool else entry_fee * C * 0.85
    if contest == "double_up":
        places = max(1, int(round(0.45 * C)))
        each = pool / places
        payout = lambda r: each if r <= places else 0.0
        first = each
    else:
        first = float(first_prize) if first_prize else 0.20 * pool
        payout, places = _gpp_curve(C, pool, first, entry_fee)
    grid = _rank_grid(places)

    import statistics
    def score(names, it):
        return sum((arr.get(nm) or [0])[it % L] for nm in names)

    ynames = [p["name"] for p in your_lineup]
    win = cash = ret = top1 = 0.0
    top1_line = max(1, int(0.01 * C))
    for it in range(n_iter):
        fs = [score(f, it) for f in field]
        mu = statistics.fmean(fs); sd = statistics.pstdev(fs) or 1.0
        ys = score(ynames, it)
        q = max(1e-12, min(1.0, 1.0 - _ncdf((ys - mu) / sd)))
        winp = math.exp((C - 1) * math.log(1.0 - q)) if q < 1.0 else 0.0
        win += winp
        mr = 1.0 + (C - 1) * q
        sr = math.sqrt(max(1e-9, (C - 1) * q * (1.0 - q)))
        cash += _ncdf((places - mr) / sr)
        top1 += _ncdf((top1_line - mr) / sr)
        if contest == "double_up":
            ret += each * _ncdf((places - mr) / sr)
        else:
            ev = first * winp
            for r, wd in grid:
                ev += payout(r) * _npdf((r - mr) / sr) / sr * wd
            ret += ev
    ret /= n_iter
    return {"win_pct": round(100 * win / n_iter, 4), "cash_pct": round(100 * cash / n_iter, 1),
            "top1_pct": round(100 * top1 / n_iter, 2), "roi_pct": round(100 * (ret - entry_fee) / entry_fee, 1),
            "avg_return": round(ret, 2), "sample_size": len(field), "entries": C,
            "contest": contest, "entry_fee": entry_fee, "prize_pool": round(pool),
            "first_prize": round(first), "places_paid": places}


# ---- public build -----------------------------------------------------------
def _pct(a, q):
    s = sorted(a)
    return s[min(len(s) - 1, int(q * len(s)))]


def build(csv_text, week=1, objective="projection", stack=True, contest=None,
          contest_size=None, entry_fee=1.0, prize_pool=None, first_prize=None):
    csv_players = simulate.parse_dk_csv(csv_text)
    if len(csv_players) < len(ROSTER):
        return {"error": f"need at least {len(ROSTER)} players in the CSV (got {len(csv_players)})"}
    pool = nfl_dfs_sim.player_pool(week)
    if not pool:
        return {"error": "projections not ready — the weekly sim is still building, retry shortly"}

    players, unmatched = [], []
    for c in csv_players:
        nm = c["name"]
        sim = pool.get(nm) or pool.get(c.get("team", ""))
        pos = (c.get("pos") or "").upper().split("/")[0]
        elig = _elig(pos)
        if not elig:
            continue
        if sim and sim.get("arr"):
            proj, ceiling, floor, samp = sim["proj"], sim["ceiling"], sim["floor"], sim["arr"]
        else:                                       # in the CSV but not projected -> soft fallback
            proj = c.get("proj") or 0.0
            samp = [max(0.0, random.gauss(proj, proj * 0.5 + 2)) for _ in range(1500)]
            ceiling, floor = round(_pct(samp, 0.9), 1), round(_pct(samp, 0.1), 1)
            unmatched.append(nm)
        players.append({"name": nm, "pos": pos, "team": c.get("team"), "salary": int(c["salary"]),
                        "proj": round(proj, 1), "ceiling": ceiling, "floor": floor,
                        "elig": elig, "arr": samp})
    if _by_pos(players) is None:
        return {"error": "the CSV doesn't cover every roster slot (need QB/RB/WR/TE/DST)"}

    _set_ownership(players)
    stack_min = 1 if (stack and objective in ("ceiling", "leverage")) else 0
    lineup = optimize(players, CAP, objective, stack_min=stack_min)
    if not lineup:
        return {"error": "no valid lineup under the cap"}

    # lineup distribution from the correlated arrays
    L = min(len(p["arr"]) for p in lineup)
    totals = [sum(p["arr"][i] for p in lineup) for i in range(L)]
    csim = None
    if contest:
        try:
            csim = contest_sim(lineup, players, contest=contest, entry_fee=entry_fee,
                               contest_size=contest_size, prize_pool=prize_pool, first_prize=first_prize)
        except Exception:
            csim = None
    slot_of, order = [], {"QB": 0, "RB": 1, "WR": 2, "TE": 3, "FLEX": 4, "DST": 5}
    # label FLEX: the extra RB/WR/TE beyond the required count
    need = {"RB": 2, "WR": 3, "TE": 1}
    seen = {"RB": 0, "WR": 0, "TE": 0}
    rows = []
    for p in sorted(lineup, key=lambda x: (order.get(x["pos"], 9), -x["proj"])):
        slot = p["pos"]
        if p["pos"] in need:
            seen[p["pos"]] += 1
            if seen[p["pos"]] > need[p["pos"]]:
                slot = "FLEX"
        rows.append({"slot": slot, "name": p["name"], "pos": p["pos"], "team": p["team"],
                     "salary": p["salary"], "proj": p["proj"], "ceiling": p["ceiling"],
                     "floor": p["floor"], "own": p.get("own")})
    return {"week": week, "objective": objective, "stack": bool(stack_min),
            "salary": sum(p["salary"] for p in lineup), "cap": CAP,
            "proj": round(sum(p["proj"] for p in lineup), 1),
            "floor": round(_pct(totals, 0.10), 1), "median": round(_pct(totals, 0.50), 1),
            "ceiling": round(_pct(totals, 0.90), 1), "max": round(max(totals), 1),
            "lineup": rows, "contest_sim": csim, "unmatched": unmatched[:20],
            "n_pool": len(players),
            "note": "Projections pinned to Sleeper; floor/ceiling + QB-WR correlation from the "
                    "game sim. Ownership is a model estimate of the field."}
