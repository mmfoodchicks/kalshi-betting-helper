"""Integrated same-game simulation -> honest same-game-parlay (SGP) odds.

Single legs are computed in closed form (exact). But two legs from the SAME
game are correlated, and multiplying their independent marginals lies:

  - a home run lifts the hitter's team runs, which moves the moneyline, the
    run line AND the game total all at once;
  - a starter's strikeouts come out of the opposing lineup's outs, so more Ks
    means fewer opponent runs (the K prop and the opponent's under move
    together);
  - a hitter's own props (1+ hit / 2+ total bases / 1+ HR) are the same plate
    appearances viewed three ways.

So for a same-game parlay we simulate the whole game ONCE and read the joint
hit-rate straight off the shared random outcomes. Runs are built from the
lineup's simulated events (scaled so mean runs == the model's expected runs),
and the starters' Ks are tied to the opposing lineup's simulated outs -- the
correlation is generated, not assumed.

Each candidate leg is stored as a bitmask over the N simulated games (bit i set
when the leg cashes in sim i). The joint probability of a parlay is then a
single bitwise-AND + popcount -- fast enough to search thousands of combos.
"""

import itertools
import math
import random

# Linear-weight run values per offensive event (relative to an out). The
# lineup's raw run-units are rescaled each game so the mean matches the model's
# expected runs, which keeps the run marginal calibrated to the rest of the app.
_LW = {1: 0.46, 2: 0.80, 3: 1.10, 4: 1.45}
_LW_BB = 0.30

try:
    (0).bit_count  # Python 3.10+
    def _popcount(m):
        return m.bit_count()
except AttributeError:  # pragma: no cover
    def _popcount(m):
        return bin(m).count("1")


def _poisson(lam):
    """Draw from Poisson(lam). Normal approximation in the (rare) large tail."""
    if lam <= 0:
        return 0
    if lam > 30:
        return max(0, int(round(random.gauss(lam, math.sqrt(lam)))))
    target = math.exp(-lam)
    k = 0
    p = 1.0
    while True:
        k += 1
        p *= random.random()
        if p <= target:
            return k - 1


def _team(batters):
    """Per-batter draw setup plus expected run-units and outs for calibration.

    Returns (setup, e_raw, e_outs) where setup is a list of
    {name, k (PA), thresh (cumulative outcome thresholds)}.
    """
    setup = []
    e_raw = e_outs = 0.0
    for b in batters or []:
        r1 = max(0.0, b.get("r1") or 0.0)
        r2 = max(0.0, b.get("r2") or 0.0)
        r3 = max(0.0, b.get("r3") or 0.0)
        rhr = max(0.0, b.get("rhr") or 0.0)
        rbb = max(0.0, b.get("rbb") or 0.0)
        tot = r1 + r2 + r3 + rhr + rbb
        if tot > 0.95:  # always leave room for an out
            s = 0.95 / tot
            r1, r2, r3, rhr, rbb = r1 * s, r2 * s, r3 * s, rhr * s, rbb * s
            tot = 0.95
        k = max(1, int(round(b.get("pa") or 4.0)))
        thresh = []
        acc = 0.0
        for code, p in ((1, r1), (2, r2), (3, r3), (4, rhr), (5, rbb)):
            if p > 0:
                acc += p
                thresh.append((acc, code))
        setup.append({"name": b.get("name"), "k": k, "thresh": thresh})
        e_raw += k * (r1 * _LW[1] + r2 * _LW[2] + r3 * _LW[3] + rhr * _LW[4] + rbb * _LW_BB)
        e_outs += k * (1 - tot)
    return setup, e_raw, e_outs


def _bat_team(setup, scale, er, store, i, rnd):
    """Simulate one team's batting for sim i. Records each tracked hitter's
    hits/total-bases/HR into `store` and returns (runs, outs).

    With no posted lineup we fall back to runs ~ Poisson(er) (outs unknown)."""
    if not setup:
        return _poisson(er), None
    raw = 0.0
    outs = 0
    for b in setup:
        h = tb = hr = 0
        for _ in range(b["k"]):
            u = rnd()
            code = 0
            for acc, c in b["thresh"]:
                if u < acc:
                    code = c
                    break
            if code == 0:
                outs += 1
            elif code == 5:        # walk
                raw += _LW_BB
            else:                  # 1B/2B/3B/HR
                raw += _LW[code]
                h += 1
                tb += code
                if code == 4:
                    hr += 1
        st = store.get(b["name"])
        if st is not None:
            st["hit"][i] = h
            st["tb"][i] = tb
            st["hr"][i] = hr
    # Poisson around the lineup-driven mean: keeps E[runs] == er while letting a
    # big offensive game (more HRs) push that team's runs up -> real correlation.
    return _poisson(raw * scale if scale else er), outs


def simulate(g, n=5000):
    """Simulate game `g` n times. Returns shared per-sim outcome arrays."""
    props = g.get("props") or {}
    er_h = g.get("exp_runs_home") or 4.3
    er_a = g.get("exp_runs_away") or 4.3
    setup_h, eh_raw, eh_outs = _team(props.get("batters_home"))
    setup_a, ea_raw, ea_outs = _team(props.get("batters_away"))
    sh = (er_h / eh_raw) if eh_raw > 0 else None
    sa = (er_a / ea_raw) if ea_raw > 0 else None
    lam_h = (props.get("ks_home") or {}).get("expected")   # home starter, faces away
    lam_a = (props.get("ks_away") or {}).get("expected")   # away starter, faces home

    home_runs = [0] * n
    away_runs = [0] * n
    home_k = [0] * n
    away_k = [0] * n
    home_win = [False] * n
    bat_h = {b["name"]: {"hit": [0] * n, "tb": [0] * n, "hr": [0] * n} for b in setup_h}
    bat_a = {b["name"]: {"hit": [0] * n, "tb": [0] * n, "hr": [0] * n} for b in setup_a}
    rnd = random.random

    for i in range(n):
        ra, oa = _bat_team(setup_a, sa, er_a, bat_a, i, rnd)
        rh, oh = _bat_team(setup_h, sh, er_h, bat_h, i, rnd)
        home_runs[i] = rh
        away_runs[i] = ra
        if rh > ra:
            home_win[i] = True
        elif rh == ra:                       # extra innings, slight home edge
            home_win[i] = rnd() < 0.52
        if lam_h is not None:
            adj = lam_h * (oa / ea_outs) if (oa is not None and ea_outs > 0) else lam_h
            home_k[i] = _poisson(min(15.0, adj))
        if lam_a is not None:
            adj = lam_a * (oh / eh_outs) if (oh is not None and eh_outs > 0) else lam_a
            away_k[i] = _poisson(min(15.0, adj))

    return {"n": n, "home_runs": home_runs, "away_runs": away_runs,
            "home_k": home_k, "away_k": away_k, "home_win": home_win,
            "bat": {"home": bat_h, "away": bat_a}}


def _mask(pred, n):
    m = 0
    for i in range(n):
        if pred(i):
            m |= (1 << i)
    return m


def build_candidates(g, sim):
    """Curated set of bettable legs for this game, each as a sim bitmask.

    Kept deliberately small (one leg per market, top hitters only) so the combo
    search stays fast while still spanning moneyline / run line / total / hitter
    props / starter strikeouts.
    """
    n = sim["n"]
    hr_runs, ar_runs, hwin = sim["home_runs"], sim["away_runs"], sim["home_win"]
    ha = g.get("home_abbr") or g.get("home_name") or "Home"
    aa = g.get("away_abbr") or g.get("away_name") or "Away"
    props = g.get("props") or {}
    cands = []

    def add(typ, label, pred):
        m = _mask(pred, n)
        marg = _popcount(m) / n
        if 0.04 <= marg <= 0.97:
            cands.append({"type": typ, "label": label, "mask": m, "marg": marg})

    # Moneyline (both sides; contradictory pairs are pruned in the search).
    add("ML", f"{g.get('home_name', ha)} to win", lambda i: hwin[i])
    add("ML", f"{g.get('away_name', aa)} to win", lambda i: not hwin[i])
    # Run line.
    add("Run line", f"{ha} −1.5 (win by 2+)", lambda i: hr_runs[i] - ar_runs[i] >= 2)
    add("Run line", f"{aa} −1.5 (win by 2+)", lambda i: ar_runs[i] - hr_runs[i] >= 2)
    # Game total (a few lines around the model total).
    tot_mean = g.get("exp_total") or (er(g))
    base = round(tot_mean)
    for ln in (base - 0.5, base + 0.5, 8.5):
        if ln < 3.5:
            continue
        add("Total", f"Over {ln} runs", lambda i, ln=ln: (hr_runs[i] + ar_runs[i]) > ln)
        add("Total", f"Under {ln} runs", lambda i, ln=ln: (hr_runs[i] + ar_runs[i]) < ln)
    # Hitter props -- top 2 hitters per side by HR+TB upside.
    for side, store, bp_list in (("home", sim["bat"]["home"], props.get("batters_home")),
                                 ("away", sim["bat"]["away"], props.get("batters_away"))):
        ranked = sorted((bp_list or []),
                        key=lambda bp: (bp.get("hr1", 0) + bp.get("tb2", 0)), reverse=True)[:2]
        for j, bp in enumerate(ranked):
            nm = bp.get("name")
            st = store.get(nm)
            if not st:
                continue
            hit, tb, hrr = st["hit"], st["tb"], st["hr"]
            add("HR", f"{nm} 1+ HR", lambda i, a=hrr: a[i] >= 1)
            add("Bases", f"{nm} 2+ total bases", lambda i, a=tb: a[i] >= 2)
            if j == 0:
                add("Hit", f"{nm} 1+ hit", lambda i, a=hit: a[i] >= 1)
    # Starter strikeouts -- best couple of lines per starter.
    hk, ak = sim["home_k"], sim["away_k"]
    if props.get("ks_home") and props.get("home_sp_name"):
        for line in (5, 6, 7):
            add("Ks", f"{props['home_sp_name']} {line}+ Ks", lambda i, L=line: hk[i] >= L)
    if props.get("ks_away") and props.get("away_sp_name"):
        for line in (5, 6, 7):
            add("Ks", f"{props['away_sp_name']} {line}+ Ks", lambda i, L=line: ak[i] >= L)
    return cands


def er(g):
    return (g.get("exp_runs_home") or 4.3) + (g.get("exp_runs_away") or 4.3)


def _redundant(masks):
    """True if any leg's outcome set is a subset of another's (one leg implies
    the other -- e.g. '2+ hits' implies '1+ hit'). Such a leg adds no real risk
    and books usually void it, so we never build it into an SGP."""
    for a in range(len(masks)):
        for b in range(a + 1, len(masks)):
            inter = masks[a] & masks[b]
            if inter == masks[a] or inter == masks[b]:
                return True
    return False


def best_same_game(cands, n, n_legs, target, target_payout, max_legs):
    """Search same-game parlays and return the best item, or None.

    Payout mode (target_payout > 1): among combos whose fair payout reaches the
    target, take the most likely; otherwise take the highest-payout combo found.
    Otherwise: take the most likely combo of n_legs legs (meeting the target
    confidence when possible)."""
    if len(cands) < 2:
        return None
    payout_mode = bool(target_payout and target_payout > 1)
    sizes = range(2, max_legs + 1) if payout_mode else [max(2, min(n_legs, max_legs))]

    best = None  # (score, combo, joint)
    for sz in sizes:
        if sz > len(cands):
            break
        for combo in itertools.combinations(cands, sz):
            masks = [c["mask"] for c in combo]
            if _redundant(masks):
                continue
            jm = masks[0]
            for m in masks[1:]:
                jm &= m
            joint = _popcount(jm) / n
            if joint <= 0:
                continue
            payout = 1.0 / joint
            if payout_mode:
                if payout >= target_payout:
                    score = 1000.0 + joint        # reached -> safest that still pays
                else:
                    score = payout                # not reached -> chase max payout
            else:
                score = (1000.0 + payout) if joint >= target else joint
            if best is None or score > best[0]:
                best = (score, combo, joint)

    if not best:
        return None
    _, combo, joint = best
    indep = 1.0
    for c in combo:
        indep *= c["marg"]
    return {
        "n_legs": len(combo),
        "legs": [{"pick": c["label"], "type": c["type"],
                  "prob_pct": round(c["marg"] * 100, 1)} for c in combo],
        "combined_prob_pct": round(joint * 100, 1),
        "indep_prob_pct": round(indep * 100, 1),
        "corr_delta_pct": round((joint - indep) * 100, 1),
        "fair_payout_x": round(1.0 / joint, 2) if joint > 0 else None,
        "indep_payout_x": round(1.0 / indep, 2) if indep > 0 else None,
        "n_sims": n,
    }
