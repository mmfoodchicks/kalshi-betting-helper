"""DraftKings LoL Classic DFS: optimizer + correlated contest sim.

Roster (7): CPT (1.5x, any player/team) + TOP + JNG + MID + ADC + SUP + TEAM,
under a $50,000 cap. Rules constraints: at most 4 entities from one LoL team
(the Team entity counts), the lineup must span >= 2 different games, and a player
can't be used at both CPT and a position.

Projections come from the pasted DKSalaries.csv's AvgPointsPerGame (DK's own
number, already in DK LoL fantasy points). The value we add is the optimizer plus
a correlated match sim: LoL scoring is hugely team-driven -- the winning team's
players all boom together (kills, win/first-blood/objective bonuses, and the big
series-sweep GNP bonus), the losers all bust. So we sample each game's outcome
once and move a whole team's players together, which is what ceiling and the
contest sim need. Player means stay pinned to the CSV projection.
"""

import math
import random

import simulate                     # parse_dk_csv
from mlb_dfs import _gpp_curve, _rank_grid, _ncdf, _npdf

SLOTS = ["CPT", "TOP", "JNG", "MID", "ADC", "SUP", "TEAM"]
POS_SLOTS = ["TOP", "JNG", "MID", "ADC", "SUP", "TEAM"]
CAP = 50000
CPT_MULT = 1.5
MAX_PER_TEAM = 4


def _parse(csv_text):
    """CPT pool + position pools + a per-name base projection, from the DK CSV."""
    rows = simulate.parse_dk_csv(csv_text)
    cpt, by_slot, base = [], {s: [] for s in POS_SLOTS}, {}
    for r in rows:
        role = (r.get("pos") or "").upper().strip()
        rp = (r.get("roster_pos") or "").upper().strip()
        role = "TEAM" if role == "TEAM" else role
        e = {"name": r["name"].strip(), "role": role, "salary": int(r["salary"]),
             "proj": float(r.get("proj") or 0.0), "team": (r.get("team") or "").strip(),
             "game": (r.get("game") or "").split(" ")[0] if r.get("game") else "",
             "is_team": role == "TEAM"}
        base.setdefault(e["name"], e["proj"])
        if rp == "CPT":
            cpt.append(e)
        elif role in by_slot:
            by_slot[role].append(e)
    for s in by_slot:
        by_slot[s].sort(key=lambda x: -x["proj"])
    return cpt, by_slot, base


# ---- correlated match sim (team wins -> its players boom together) ----------
def _arrays(base, entities, n=4000):
    """{name: [fantasy-point samples]} with teammates moving together and the two
    teams in a game anti-correlated. Means are pinned to the CSV projection."""
    # team -> its game, and each game's two teams + strength (sum of player projs)
    team_game, strength = {}, {}
    for e in entities:
        team_game[e["team"]] = e["game"]
        if not e["is_team"]:
            strength[e["team"]] = strength.get(e["team"], 0.0) + e["proj"]
    games = {}
    for t, g in team_game.items():
        games.setdefault(g, []).append(t)
    gauss = random.gauss
    samp = {nm: [] for nm in base}
    # map each name -> its team
    team_of = {e["name"]: e["team"] for e in entities}
    for _ in range(n):
        winners = {}
        for g, teams in games.items():
            if len(teams) >= 2:
                a, b = teams[0], teams[1]
                sa, sb = strength.get(a, 1.0), strength.get(b, 1.0)
                pa = sa / (sa + sb) if (sa + sb) else 0.5
                winners[g] = a if random.random() < pa else b
            elif teams:
                winners[g] = teams[0]
        for nm, proj in base.items():
            t = team_of.get(nm)
            won = winners.get(team_game.get(t)) == t
            f = gauss(1.42, 0.34) if won else gauss(0.62, 0.28)   # winners ~2x losers
            samp[nm].append(max(0.0, proj * f))
    # pin each name's mean back to its CSV projection
    for nm, arr in samp.items():
        m = sum(arr) / len(arr)
        if m > 0:
            f = base[nm] / m
            samp[nm] = [x * f for x in arr]
    return samp


def _pct(a, q):
    s = sorted(a)
    return s[min(len(s) - 1, int(q * len(s)))]


def _val(e, arrays, objective, own=8.0, cpt=False):
    mult = CPT_MULT if cpt else 1.0
    if objective == "projection":
        return e["proj"] * mult
    ceil = e.get("ceil", e["proj"]) * mult              # precomputed 90th pct (fast)
    if objective == "leverage":
        return ceil * (1.0 - 0.007 * e.get("own", own))
    return ceil


# ---- ownership --------------------------------------------------------------
def _ownership(cpt, by_slot):
    ents = list(cpt) + [e for s in by_slot.values() for e in s]
    for e in ents:
        v = e["proj"] / max(1.0, e["salary"] / 1000.0)
        e["_vw"] = max(0.01, v) ** 3.0
    tot = sum(e["_vw"] for e in ents) or 1.0
    for e in ents:
        e["own"] = round(min(50.0, 100.0 * len(SLOTS) * e["_vw"] / tot), 1)


# ---- optimizer --------------------------------------------------------------
def _fill(cpt_e, by_slot, arrays, objective, rng):
    """Greedy-randomized position fill around a fixed captain. Enforces the cap,
    the 4-per-team max, and no-duplicate-player; game-span is checked by caller."""
    used_names = {cpt_e["name"]}
    team_ct = {cpt_e["team"]: 1}
    sal = cpt_e["salary"]
    lineup = [("CPT", cpt_e)]
    for slot in POS_SLOTS:
        pool = [e for e in by_slot[slot]
                if e["name"] not in used_names
                and team_ct.get(e["team"], 0) < MAX_PER_TEAM
                and sal + e["salary"] <= CAP]
        if not pool:
            return None
        top = pool[:8]
        w = [max(0.05, _val(e, arrays, objective, e.get("own", 8.0))) ** 2 for e in top]
        pick = rng.choices(top, weights=w)[0]
        used_names.add(pick["name"])
        team_ct[pick["team"]] = team_ct.get(pick["team"], 0) + 1
        sal += pick["salary"]
        lineup.append((slot, pick))
    if len({e["game"] for _, e in lineup}) < 2:      # must span >= 2 games
        return None
    if max(team_ct.values()) > MAX_PER_TEAM:
        return None
    return lineup, sal


def optimize(cpt, by_slot, arrays, objective, restarts=4000):
    rng = random
    best = None
    for _ in range(restarts):
        c = rng.choice(cpt)
        r = _fill(c, by_slot, arrays, objective, rng)
        if not r:
            continue
        lineup, sal = r
        score = sum(_val(e, arrays, objective, e.get("own", 8.0), cpt=(slot == "CPT"))
                    for slot, e in lineup)
        if best is None or score > best[0]:
            best = (score, lineup, sal)
    return (best[1], best[2]) if best else (None, None)


# ---- contest sim ------------------------------------------------------------
def _field_lineup(cpt, by_slot, arrays, rng, tries=10):
    for _ in range(tries):
        c = rng.choices(cpt, weights=[max(0.1, e.get("own", 8.0)) for e in cpt])[0]
        r = _fill(c, by_slot, arrays, "projection", rng)
        if r:
            return r[0]
    return None


def contest_sim(lineup, cpt, by_slot, arrays, contest="gpp", entry_fee=1.0,
                contest_size=None, prize_pool=None, first_prize=None,
                sample_size=500, n_iter=400):
    L = min(len(a) for a in arrays.values()) if arrays else 0
    if not L:
        return None
    rng = random.Random(99)

    def score(lu, it):
        return sum((CPT_MULT if slot == "CPT" else 1.0) * arrays[e["name"]][it % L]
                   for slot, e in lu)

    ref = sum(e["proj"] * (CPT_MULT if slot == "CPT" else 1.0) for slot, e in lineup)
    field = []
    for _ in range(sample_size * 8):
        fl = _field_lineup(cpt, by_slot, arrays, rng)
        if fl and sum(e["proj"] * (CPT_MULT if s == "CPT" else 1.0) for s, e in fl) >= 0.82 * ref:
            field.append(fl)
        if len(field) >= sample_size:
            break
    if len(field) < 25:
        return None
    C = max(2, int(contest_size or (len(field) + 1)))
    pool = float(prize_pool) if prize_pool else entry_fee * C * 0.85
    if contest == "double_up":
        places = max(1, int(round(0.45 * C)))
        each = pool / places
        payout, first = (lambda r: each if r <= places else 0.0), each
    else:
        first = float(first_prize) if first_prize else 0.20 * pool
        payout, places = _gpp_curve(C, pool, first, entry_fee)
    grid = _rank_grid(places)
    import statistics
    win = cash = ret = top1 = 0.0
    top1_line = max(1, int(0.01 * C))
    for it in range(n_iter):
        fs = [score(f, it) for f in field]
        mu = statistics.fmean(fs); sd = statistics.pstdev(fs) or 1.0
        ys = score(lineup, it)
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
            "entries": C, "contest": contest, "entry_fee": entry_fee, "prize_pool": round(pool),
            "first_prize": round(first), "places_paid": places, "sample_size": len(field)}


# ---- public build -----------------------------------------------------------
def build(csv_text, objective="projection", contest=None, contest_size=None,
          entry_fee=1.0, prize_pool=None, first_prize=None):
    cpt, by_slot, base = _parse(csv_text)
    if not cpt or any(not by_slot[s] for s in POS_SLOTS):
        return {"error": "CSV must include a CPT row and every position (TOP/JNG/MID/ADC/SUP/TEAM)"}
    all_ents = list(cpt) + [e for s in by_slot.values() for e in s]
    arrays = _arrays(base, all_ents)
    for e in all_ents:                                  # precompute ceiling once (hot loop)
        e["ceil"] = _pct(arrays.get(e["name"], [e["proj"]]), 0.90)
    _ownership(cpt, by_slot)
    lineup, sal = optimize(cpt, by_slot, arrays, objective)
    if not lineup:
        return {"error": "no valid lineup under the cap (check the CSV covers both teams)"}

    L = min(len(a) for a in arrays.values())
    totals = [sum((CPT_MULT if s == "CPT" else 1.0) * arrays[e["name"]][i] for s, e in lineup)
              for i in range(L)]
    csim = None
    if contest:
        try:
            csim = contest_sim(lineup, cpt, by_slot, arrays, contest=contest, entry_fee=entry_fee,
                               contest_size=contest_size, prize_pool=prize_pool, first_prize=first_prize)
        except Exception:
            csim = None
    rows = []
    for slot, e in lineup:
        pts = round(e["proj"] * (CPT_MULT if slot == "CPT" else 1.0), 1)
        rows.append({"slot": slot, "name": e["name"], "role": e["role"], "team": e["team"],
                     "salary": e["salary"], "proj": pts, "base_proj": round(e["proj"], 1),
                     "own": e.get("own")})
    return {"objective": objective, "salary": sal, "cap": CAP,
            "proj": round(sum(r["proj"] for r in rows), 1),
            "floor": round(_pct(totals, 0.10), 1), "median": round(_pct(totals, 0.50), 1),
            "ceiling": round(_pct(totals, 0.90), 1), "max": round(max(totals), 1),
            "lineup": rows, "contest_sim": csim, "is_lol": True,
            "note": "Projections are DK's AvgPointsPerGame; the sim adds a team-correlated "
                    "ceiling (a winning team's players boom together, incl. the series-sweep "
                    "bonus). Captain scores 1.5x. Ownership is a model estimate."}
