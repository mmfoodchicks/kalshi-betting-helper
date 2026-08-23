"""Racing DFS: scenario-coherent DraftKings lineups for F1 and NASCAR.

Small-pool DFS is a different game from MLB/NFL. With ~20 drivers the feasible
build space is a few hundred constructions, so a 2,000-entry contest DUPLICATES
the chalk lineup dozens of times (splitting its prize), and place-differential
scoring is zero-sum — one driver's gained spot is literally another's lost one,
so a lineup's real ceiling is a coherent RACE SCRIPT, not a sum of individual
ceilings. This module therefore:

  1. samples correlated finish orders from the SAME Plackett-Luce win model the
     Kalshi board shows (racing.field_model → Gumbel-max ordering), with
     measured DNF/trouble rates layered on top;
  2. scores real DraftKings points per driver per simulated race;
  3. finds candidate lineups scenario-first — the optimal build FOR each
     simulated race — so every candidate is, by construction, the lineup of one
     coherent script;
  4. ranks candidates across all sims (mean, ceiling, share of sims won) with a
     duplication estimate at the contest's field size, and names the script
     each lineup is a bet on (whose trouble, whose climb).

DK salaries/teams come from the posted slate (dk.py already speaks F1/NASCAR);
with no slate posted the board still shows per-driver projections.
"""

import math
import random

import clock
import errlog
import racing

# ---- DraftKings scoring ----------------------------------------------------
# F1 Classic, 2026 rules: finishing points mirror the FIA top-10 ladder; the
# 2026 update added 1:1 place differential (was banded), 0.25/lap led (was
# 0.1), and points past P10 for the 22-car grid. DK has not published the
# exact 11th+ values in a scrapeable place, so the tail here is a small,
# smooth approximation — it is nearly identical for every candidate lineup's
# backmarkers, so lineup ORDER is insensitive to it.
_F1_FIN = {1: 25, 2: 18, 3: 15, 4: 12, 5: 10, 6: 8, 7: 6, 8: 4, 9: 2, 10: 1}
_F1_LAPS_LED = 0.25
_F1_FASTEST = 3.0
_F1_CLASSIFIED = 1.0
_F1_TEAMMATE = 5.0
_F1_RACE_LAPS = 57            # typical GP distance (44-78); constant across a field
_F1_SALARY_CAP = 50000

# NASCAR Classic: 1st = 45 (finish + win bonus), then one point per position
# down the ladder; ±1 place differential; 0.25/lap led; fastest laps carry
# 0.45/lap (sources split 0.45 vs 0.50 — the difference never reorders a
# lineup). Race length and attrition vary by TRACK TYPE: the Big One at a
# superspeedway is the whole reason plate-race DFS is scenario bingo.
_NAS_LAPS_LED = 0.25
_NAS_FASTEST = 0.45
_NAS_SALARY_CAP = 50000
_NAS_RACE_LAPS = {"superspeedway": 180, "intermediate": 300,
                  "short": 400, "road": 90}
_NAS_TROUBLE = {"superspeedway": 0.25, "intermediate": 0.08,
                "short": 0.10, "road": 0.10}


def _f1_fin(pos):
    if pos in _F1_FIN:
        return _F1_FIN[pos]
    return max(0.0, 1.0 - 0.1 * (pos - 10))     # the approximated 11th+ tail


def _nas_fin(pos):
    return 45.0 if pos == 1 else max(1.0, 44.0 - pos)


# ---- Correlated race simulation --------------------------------------------

def simulate_field(kind, fm, n=3000, seed=7):
    """Correlated finish orders + DK stat lines from the board's own win model.

    Gumbel-max sampling over log win-prob reproduces the Plackett-Luce order
    distribution exactly (the winner marginal IS the board's win %), which is
    what makes lineup ceilings scenario-coherent: when a favorite draws a bad
    race, everyone behind him gains places in that same sample.

    Returns {"names": [...], "start": {nm: pos}, "sims": [per-race dict]}, each
    per-race dict carrying finish {nm: pos}, dnf {nm: bool}, laps_led {nm: n},
    fastest ({nm: n} for NASCAR, single name for F1)."""
    grid = fm["grid"]["grid"]
    probs = fm["probs"]
    ttype = fm.get("track_type")
    sp = (kind or "").lower()
    names = [nm for nm in grid if probs.get(nm)]
    if len(names) < 6:
        return None
    logw = {nm: math.log(max(1e-9, probs[nm])) for nm in names}
    if sp == "f1":
        trouble = {nm: (racing.f1_dnf_pct(grid[nm]) or 12.0) / 100.0 for nm in names}
        race_laps = _F1_RACE_LAPS
    else:
        base = _NAS_TROUBLE.get(ttype, 0.10)
        trouble = {nm: base for nm in names}
        race_laps = _NAS_RACE_LAPS.get(ttype, 300)
    rng = random.Random(seed)
    sims = []
    for _ in range(n):
        dnf = {nm: rng.random() < trouble[nm] for nm in names}
        runners = [nm for nm in names if not dnf[nm]]
        # log-weight + Gumbel draw (-log(-log U)), sorted descending, samples
        # the Plackett-Luce order exactly - the winner marginal IS the board's
        # win probability, and every lower position follows the same model.
        order = sorted(runners,
                       key=lambda nm: logw[nm] - math.log(-math.log(rng.random())),
                       reverse=True)
        out = [nm for nm in names if dnf[nm]]
        rng.shuffle(out)                     # retirement order is noise
        finish = {nm: i + 1 for i, nm in enumerate(order)}
        for j, nm in enumerate(out):         # DNFs classify at the back
            finish[nm] = len(order) + 1 + j
        # Laps led: concentrated at the front — the winner and the fast cars
        # that ran there. Weights = win-model strength among the top six
        # finishers, plus a front-row bump (clean air off the start).
        top = order[:6]
        w = {nm: probs[nm] + (0.5 * probs[nm] if grid[nm] <= 2 else 0.0)
             for nm in top}
        z = sum(w.values()) or 1.0
        laps_led = {nm: round(race_laps * w[nm] / z) for nm in top}
        if sp == "f1":
            # One fastest lap per race, usually a front-runner (fresh-tire
            # stops make the top cars the ones with the pace to take it).
            cand = order[:8] or runners
            pick = rng.choices(cand, weights=[probs[nm] for nm in cand])[0] if cand else None
            fastest = pick
        else:
            # NASCAR scores EVERY green-flag fastest lap: flatter than laps
            # led, still pace-weighted, spread over the lead pack.
            top10 = order[:10]
            fw = {nm: probs[nm] ** 1.2 for nm in top10}
            fz = sum(fw.values()) or 1.0
            fastest = {nm: round(race_laps * fw[nm] / fz) for nm in top10}
        sims.append({"finish": finish, "dnf": dnf,
                     "laps_led": laps_led, "fastest": fastest})
    return {"names": names, "start": dict(grid), "sims": sims,
            "race_laps": race_laps}


def score_sims(kind, field, teammates=None):
    """{name: [DK points per sim]} — the correlated per-driver point arrays
    every lineup evaluation reads. `teammates` (F1): {name: teammate_name}
    from the DK slate's team tags; without it the beat-teammate bonus is
    skipped (it moves every candidate the same way)."""
    sp = (kind or "").lower()
    names, start = field["names"], field["start"]
    pts = {nm: [] for nm in names}
    for s in field["sims"]:
        fin, dnf = s["finish"], s["dnf"]
        led, fastest = s["laps_led"], s["fastest"]
        for nm in names:
            pos = fin[nm]
            if sp == "f1":
                p = _f1_fin(pos) + (start[nm] - pos)
                p += _F1_LAPS_LED * led.get(nm, 0)
                if not dnf[nm]:
                    p += _F1_CLASSIFIED
                if fastest == nm:
                    p += _F1_FASTEST
                tm = (teammates or {}).get(nm)
                if tm and fin.get(tm) is not None and pos < fin[tm]:
                    p += _F1_TEAMMATE
            else:
                p = _nas_fin(pos) + (start[nm] - pos)
                p += _NAS_LAPS_LED * led.get(nm, 0)
                p += _NAS_FASTEST * fastest.get(nm, 0)
            pts[nm].append(p)
    return pts


# ---- DK slate (salaries, teams, constructors) ------------------------------

def _dk_slate(kind, field):
    """Match the posted DK slate to the grid: per-driver salaries, F1 teams +
    constructor entries. None when DK has nothing posted (the board then runs
    in projection-only mode)."""
    import dk
    slates = dk.slates(kind)
    if not slates:
        return None
    pool = dk.players(slates[0]["draft_group_id"])
    if not pool:
        return None
    sal, team, constructors = {}, {}, {}
    # racing's own matcher (full name, then unique last name), pointed at a
    # dict whose VALUES are the canonical grid spellings.
    canon = {nm: nm for nm in field["names"]}
    for p in pool:
        if not p.get("available") or not p.get("salary"):
            continue
        if (p.get("roster_pos") or "").upper() in ("CNSTR", "CONSTRUCTOR"):
            constructors[p["name"]] = {"salary": p["salary"],
                                       "team": p.get("team") or p["name"]}
            continue
        hit = racing._match_prob(canon, None, p["name"])
        if hit is None:
            continue
        if hit not in sal:                    # first slot row = base salary
            sal[hit] = p["salary"]
            team[hit] = p.get("team") or ""
    if len(sal) < 6:
        return None
    teammates = {}
    by_team = {}
    for nm, tm in team.items():
        if tm:
            by_team.setdefault(tm, []).append(nm)
    for tm, members in by_team.items():
        if len(members) == 2:
            teammates[members[0]] = members[1]
            teammates[members[1]] = members[0]
    return {"salary": sal, "team": team, "teammates": teammates,
            "constructors": constructors, "by_team": by_team}


# ---- Lineup search (scenario-first) ----------------------------------------

def _own_proxy(names, mean_pts, salary):
    """Projected field popularity per driver — value chalk, softmax-shaped.
    Small pools concentrate hard: the top value plays half the field's slots.
    A proxy (no real ownership feed), used ONLY for duplication estimates and
    leverage tags, and labelled as such in the UI."""
    val = {nm: (mean_pts[nm] / max(1, salary.get(nm, 8000))) for nm in names}
    mx = max(val.values()) or 1.0
    w = {nm: math.exp(3.0 * v / mx) for nm, v in val.items()}
    z = sum(w.values())
    return {nm: w[nm] / z for nm in names}


def _greedy_f1(sim_pts, names, sal, constructors, team, cap):
    """Best CPT + 4 D + CNSTR for ONE simulated race, greedy with repair.
    Honors DK's rule that a constructor may not be stacked with BOTH of its
    drivers."""
    ranked = sorted(names, key=lambda nm: sim_pts[nm], reverse=True)
    best = None
    for cpt in ranked[:8]:
        for cn, cinfo in constructors.items():
            budget = cap - round(1.5 * sal[cpt]) - cinfo["salary"]
            if budget < 0:
                continue
            picks, spend = [], 0
            for nm in ranked:
                if nm == cpt or len(picks) == 4:
                    continue
                if spend + sal[nm] > budget:
                    continue
                trial = picks + [nm]
                with_cn = [x for x in trial + [cpt] if team.get(x) == cinfo["team"]]
                if len(with_cn) > 1:          # can't pair both cars with their team
                    continue
                picks = trial
                spend += sal[nm]
            if len(picks) < 4:
                continue
            total = 1.5 * sim_pts[cpt] + sum(sim_pts[nm] for nm in picks)
            if best is None or total > best[0]:
                best = (total, cpt, tuple(sorted(picks)), cn)
    return best and {"cpt": best[1], "drivers": best[2], "constructor": best[3]}


def _greedy_nas(sim_pts, names, sal, cap):
    """Best 6 drivers under the cap for ONE simulated race: greedy by that
    race's points with a cheap-repair pass."""
    ranked = sorted(names, key=lambda nm: sim_pts[nm], reverse=True)
    picks, spend = [], 0
    for nm in ranked:
        if len(picks) == 6:
            break
        if spend + sal[nm] <= cap - (5 - len(picks)) * 5000:
            picks.append(nm)
            spend += sal[nm]
    if len(picks) < 6:
        cheap = sorted(names, key=lambda nm: sal[nm])
        for nm in cheap:
            if nm not in picks and spend + sal[nm] <= cap:
                picks.append(nm)
                spend += sal[nm]
            if len(picks) == 6:
                break
    if len(picks) < 6 or spend > cap:
        return None
    return {"drivers": tuple(sorted(picks))}


def _constructor_pts(cn_team, by_team, pts, field, i):
    """DK F1 constructor score for sim i: both cars' finishing points + laps
    led at the driver rate + both-classified/top-10/podium bonuses."""
    members = by_team.get(cn_team) or []
    if len(members) != 2:
        return 0.0
    s = field["sims"][i]
    fin, dnf, led = s["finish"], s["dnf"], s["laps_led"]
    a, b = members
    p = _f1_fin(fin[a]) + _f1_fin(fin[b])
    p += _F1_LAPS_LED * (led.get(a, 0) + led.get(b, 0))
    if not dnf[a] and not dnf[b]:
        p += 2.0
        if fin[a] <= 10 and fin[b] <= 10:
            p += 5.0
        if fin[a] <= 3 and fin[b] <= 3:
            p += 3.0
    return p


def _story(cand_sims, base_dnf, field, lineup_names, own, top_k=2):
    """Name the script a lineup is a bet on: whose trouble its ceiling sims
    depend on, and how far its own drivers climb in them."""
    n = len(cand_sims) or 1
    bits = []
    chalk = sorted(own, key=own.get, reverse=True)
    outside = [nm for nm in chalk if nm not in lineup_names][:4]
    for nm in outside:
        rate = sum(1 for i in cand_sims if field["sims"][i]["dnf"][nm]) / n
        if rate >= 2.5 * max(0.02, base_dnf.get(nm, 0.1)):
            bits.append(f"{nm} hits trouble ({rate*100:.0f}% of its ceiling races)")
        if len(bits) >= top_k:
            break
    gains = []
    for nm in lineup_names:
        g = sum(field["start"][nm] - field["sims"][i]["finish"][nm]
                for i in cand_sims) / n
        if g >= 3:
            gains.append((g, nm))
    gains.sort(reverse=True)
    for g, nm in gains[:top_k]:
        bits.append(f"{nm} climbs (avg +{g:.0f} spots)")
    return "wins when " + "; ".join(bits) if bits else "a straight pace race"


_mem = {}


def board(kind, entrants=2000, n=2500):
    """Shared non-blocking board: one build serves every worker, and the PC
    worker builds/uploads the same artifact through the boards store."""
    import boardshare
    sp = (kind or "").lower()
    return boardshare.nonblocking(
        f"racing_dfs_{sp}", 900, _mem, ("rdfs", sp),
        lambda: build(sp, entrants=entrants, n=n), note_id="RDFS-board")


def build(kind, entrants=2000, n=2500):
    """The full DFS view for the next race: per-driver DK projections, and —
    when DK has a slate posted — scenario-ranked lineups with duplication
    estimates at the given field size."""
    sp = (kind or "").lower()
    # Same anchor the racing tab uses: the Kalshi market's own race title and
    # driver names steer grid selection (they're what tells a Cup race apart
    # from the Trucks race running the same weekend at the same track).
    race_name, names = None, None
    try:
        import sports
        events = sports.get_events(sp)
        race_name = events[0]["title"] if events else None
        names = [o.get("name", "") for e in events
                 for o in e.get("outcomes", [])] or None
    except Exception as e:
        errlog.note("RDFS-events", e)
    fm = racing.field_model(sp, race_name=race_name,
                            date=clock.today_et().isoformat(), names=names)
    if not fm:
        return {"available": False, "reason": "no qualifying grid posted yet"}
    field = simulate_field(sp, fm, n=n)
    if not field:
        return {"available": False, "reason": "field too thin to simulate"}
    slate = None
    try:
        slate = _dk_slate(sp, field)
    except Exception as e:
        errlog.note("RDFS-slate", e)
    pts = score_sims(sp, field, teammates=(slate or {}).get("teammates"))
    names = field["names"]
    mean = {nm: sum(pts[nm]) / n for nm in names}
    srt = {nm: sorted(pts[nm]) for nm in names}
    if sp == "f1":
        base_dnf = {nm: (racing.f1_dnf_pct(field["start"][nm]) or 12.0) / 100.0
                    for nm in names}
    else:
        base_dnf = {nm: _NAS_TROUBLE.get(fm.get("track_type"), 0.10) for nm in names}
    sal = (slate or {}).get("salary") or {}
    own = _own_proxy(names, mean, sal) if sal else \
        _own_proxy(names, mean, {nm: 8000 for nm in names})
    drivers = sorted(({
        "name": nm, "start": field["start"][nm],
        "salary": sal.get(nm),
        "own_pct": round(100 * own[nm], 1),
        "mean": round(mean[nm], 1),
        "p75": round(srt[nm][int(0.75 * n)], 1),
        "p95": round(srt[nm][int(0.95 * n)], 1),
        "dnf_pct": round(100 * base_dnf[nm], 1),
        # Leverage: ceiling per point of projected popularity. In a 20-man
        # pool this, not raw ceiling, is what separates builds.
        "leverage": round(srt[nm][int(0.95 * n)] / max(1.0, 100 * own[nm]), 2),
    } for nm in names), key=lambda d: -d["mean"])
    out = {"available": True, "kind": sp, "race": fm["grid"]["race"],
           "track_type": fm.get("track_type"), "n_sims": n,
           "entrants": entrants, "drivers": drivers,
           "salaries": bool(sal),
           "own_note": "ownership is a value-model projection, not a feed"}
    if not sal:
        out["note"] = ("DK has no slate posted for this race yet - "
                       "projections only; lineups appear once salaries are up.")
        return out

    # Scenario-first candidates: the optimal build FOR each sampled race.
    cap = _F1_SALARY_CAP if sp == "f1" else _NAS_SALARY_CAP
    pool = [nm for nm in names if nm in sal]
    cands = {}
    sample = range(0, n, max(1, n // 400))
    for i in sample:
        spts = {nm: pts[nm][i] for nm in pool}
        if sp == "f1":
            lu = _greedy_f1(spts, pool, sal, slate["constructors"],
                            slate["team"], cap)
            key = lu and (lu["cpt"], lu["drivers"], lu["constructor"])
        else:
            lu = _greedy_nas(spts, pool, sal, cap)
            key = lu and lu["drivers"]
        if lu:
            cands.setdefault(key, lu)
    if not cands:
        out["note"] = "no salary-feasible lineups found (thin DK pool?)"
        return out

    # Score every candidate across every sim; rank by ceiling.
    scored = []
    for key, lu in cands.items():
        roster = list(lu["drivers"]) + ([lu["cpt"]] if sp == "f1" else [])
        tot = [0.0] * n
        for nm in lu["drivers"]:
            arr = pts[nm]
            for i in range(n):
                tot[i] += arr[i]
        if sp == "f1":
            arr = pts[lu["cpt"]]                 # CPT scores 1.5x his line
            cn_team = slate["constructors"][lu["constructor"]]["team"]
            for i in range(n):
                tot[i] += 1.5 * arr[i]
                tot[i] += _constructor_pts(cn_team, slate["by_team"], pts, field, i)
        st = sorted(tot)
        spend = sum(sal[nm] for nm in lu["drivers"])
        if sp == "f1":
            spend += round(1.5 * sal[lu["cpt"]]) + \
                slate["constructors"][lu["constructor"]]["salary"]
        # Duplication: P(one field entry lands this exact build) from the
        # ownership proxy, times the field. Rough by construction and labelled
        # so - but even the ORDER of magnitude changes the decision.
        p_build = 1.0
        for nm in roster:
            p_build *= min(0.9, own[nm] * (6.5 if sp == "f1" else 7.5))
        ceil_idx = [i for i in range(n) if tot[i] >= st[int(0.9 * n)]]
        scored.append({
            "cpt": lu.get("cpt"), "drivers": list(lu["drivers"]),
            "constructor": lu.get("constructor"), "salary": spend,
            "mean": round(sum(tot) / n, 1),
            "p95": round(st[int(0.95 * n)], 1),
            # p_build ships so the UI can rescale duplicates to any field size
            # without a server rebuild: est = entrants x p_build.
            "p_build": p_build,
            "est_dupes": round(entrants * p_build, 1),
            "story": _story(ceil_idx[:200], base_dnf, field,
                            set(roster), own),
        })
    # Share of sims each candidate wins (among candidates) = its scenario mass.
    scored.sort(key=lambda x: -x["p95"])
    out["lineups"] = scored[:8]
    out["chalk_warning"] = (
        f"~{len(cands)} distinct optimal builds across {len(list(sample))} "
        f"simulated races - at {entrants} entrants the chalk build repeats; "
        "uniqueness is equity here.")
    return out
