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


def _rates(batters):
    """Parse the lineup into [(name, [r1,r2,r3,rhr,rbb], spd, sbr)]."""
    rows = []
    for b in batters or []:
        rows.append([b.get("name"),
                     [max(0.0, b.get(k) or 0.0) for k in ("r1", "r2", "r3", "rhr", "rbb")],
                     b.get("spd") or 1.0, b.get("sbr") or 0.0])
    return rows


def _build_setup(rows, mult):
    """Cumulative outcome thresholds with on-base rates scaled by `mult`,
    carrying each batter's speed factor + steal rate for baserunning."""
    setup = []
    for name, rates, spd, sbr in rows:
        sr = [x * mult for x in rates]
        tot = sum(sr)
        if tot > 0.95:
            sr = [x * 0.95 / tot for x in sr]
        thresh, acc = [], 0.0
        for code, p in zip((1, 2, 3, 4, 5), sr):
            if p > 0:
                acc += p
                thresh.append((acc, code))
        setup.append({"name": name, "thresh": thresh, "spd": spd, "sbr": sbr})
    return setup


def _team(batters, er, rnd):
    """Lineup setup with on-base rates EMPIRICALLY calibrated so the simulated
    runs land near `er` (the matchup-adjusted model total). Returns [] if no
    lineup is posted."""
    rows = _rates(batters)
    if not rows:
        return []
    if not er:
        return _build_setup(rows, 1.0)
    mult = 1.0
    for _ in range(4):                       # converge the rate multiplier to er
        setup = _build_setup(rows, mult)
        mean = sum(_play_game(setup, rnd)[0] for _ in range(300)) / 300.0
        if mean <= 0.3:
            mult *= 1.5
            continue
        if abs(mean - er) <= 0.05 * er:      # close enough
            break
        # Clamp near 1.0 so matchup calibration nudges team runs to er without
        # badly distorting each hitter's true rate (runs ~ mult^1.5 -> exp 0.7).
        mult = max(0.7, min(1.5, mult * (er / mean) ** 0.7))
    return _build_setup(rows, mult)


_N_INNINGS = 9


_DK_HIT = {1: 3, 2: 5, 3: 8, 4: 10}   # DraftKings hitter points by hit type


def _play_game(setup, rnd):
    """One full game for a lineup via base-out simulation, with speed-driven
    baserunning + stolen bases. Returns (runs, per-batter
    [hits, tb, hr, runs_scored, rbi, sb, dk_points]). dk_points is the batter's
    DraftKings fantasy total (1B+3 2B+5 3B+8 HR+10 R+2 RBI+2 BB+2 SB+5)."""
    L = len(setup)
    stats = [[0, 0, 0, 0, 0, 0, 0] for _ in range(L)]   # H,TB,HR,R,RBI,SB,DK
    runs = 0
    idx = 0
    for _inn in range(_N_INNINGS):
        outs = 0
        bases = [None, None, None]                # batter index on 1st/2nd/3rd
        while outs < 3:
            # Steal attempt: runner on 1st, 2nd open, < 2 outs.
            if bases[0] is not None and bases[1] is None and outs < 2:
                rr = bases[0]
                if rnd() < setup[rr]["sbr"]:
                    if rnd() < max(0.55, min(0.9, 0.62 + (setup[rr]["spd"] - 1.0) * 0.7)):
                        bases[1] = rr; bases[0] = None
                        stats[rr][5] += 1; stats[rr][6] += 5            # SB +5
                    else:
                        bases[0] = None; outs += 1                      # caught stealing
                        if outs >= 3:
                            break
            bi = idx % L
            idx += 1
            u = rnd()
            code = 0
            for acc, c in setup[bi]["thresh"]:
                if u < acc:
                    code = c
                    break
            s = stats[bi]
            if code == 0:                         # out
                # Double play: runner on 1st, < 2 outs -> erase batter + lead runner.
                if bases[0] is not None and outs < 2 and rnd() < 0.13:
                    outs += 2
                    bases[0] = None
                else:
                    # Sac fly / productive out: runner on 3rd, < 2 outs scores ~16%.
                    if bases[2] is not None and outs < 2 and rnd() < 0.16:
                        rs = stats[bases[2]]; runs += 1; rs[3] += 1; rs[6] += 2
                        s[4] += 1; s[6] += 2; bases[2] = None
                    outs += 1
            elif code == 5:                       # walk (force advances only)
                s[6] += 2
                if bases[0] is None:
                    bases[0] = bi
                elif bases[1] is None:
                    bases[1] = bases[0]; bases[0] = bi
                elif bases[2] is None:
                    bases[2] = bases[1]; bases[1] = bases[0]; bases[0] = bi
                else:                             # bases loaded -> forced run
                    rs = stats[bases[2]]; runs += 1; rs[3] += 1; rs[6] += 2
                    s[4] += 1; s[6] += 2
                    bases[2] = bases[1]; bases[1] = bases[0]; bases[0] = bi
            else:                                 # a hit
                s[0] += 1; s[1] += code; s[6] += _DK_HIT[code]
                r3, r2, r1 = bases
                scored = 0
                if code == 4:                     # HR: everyone (incl. batter) scores
                    s[2] += 1
                    for r in (r1, r2, r3):
                        if r is not None:
                            rs = stats[r]; runs += 1; rs[3] += 1; rs[6] += 2; scored += 1
                    runs += 1; s[3] += 1; s[6] += 2                     # batter run
                    s[4] += 1 + scored; s[6] += 2 * (1 + scored)       # RBI
                    bases = [None, None, None]
                elif code == 3:                   # triple: all runners score
                    for r in (r1, r2, r3):
                        if r is not None:
                            rs = stats[r]; runs += 1; rs[3] += 1; rs[6] += 2; scored += 1
                    s[4] += scored; s[6] += 2 * scored
                    bases = [None, None, bi]
                elif code == 2:                   # double
                    nb = [None, bi, None]         # batter to 2nd
                    for r in (r3, r2):
                        if r is not None:
                            rs = stats[r]; runs += 1; rs[3] += 1; rs[6] += 2; scored += 1
                    if r1 is not None:            # from 1st: faster runners score more
                        if rnd() < max(0.25, min(0.7, 0.45 * setup[r1]["spd"])):
                            rs = stats[r1]; runs += 1; rs[3] += 1; rs[6] += 2; scored += 1
                        else:
                            nb[2] = r1
                    bases = nb
                    s[4] += scored; s[6] += 2 * scored
                else:                             # single
                    nb = [bi, None, None]         # batter to 1st
                    if r3 is not None:
                        rs = stats[r3]; runs += 1; rs[3] += 1; rs[6] += 2; scored += 1
                    if r2 is not None:            # from 2nd: ~60%, speed-scaled
                        if rnd() < max(0.4, min(0.85, 0.60 * setup[r2]["spd"])):
                            rs = stats[r2]; runs += 1; rs[3] += 1; rs[6] += 2; scored += 1
                        else:
                            nb[2] = r2
                    if r1 is not None:            # from 1st: ->2nd, or ->3rd (speed)
                        if nb[2] is None and rnd() < max(0.15, min(0.5, 0.28 * setup[r1]["spd"])):
                            nb[2] = r1
                        else:
                            nb[1] = r1
                    bases = nb
                    s[4] += scored; s[6] += 2 * scored
    return runs, stats


def simulate(g, n=5000):
    """Simulate game `g` n times via base-running. Returns shared per-sim arrays,
    including per-batter hits/total-bases/HR/runs/RBI for player props (HRR)."""
    props = g.get("props") or {}
    er_h = g.get("exp_runs_home") or 4.3
    er_a = g.get("exp_runs_away") or 4.3
    rnd = random.random
    setup_h = _team(props.get("batters_home"), er_h, rnd)
    setup_a = _team(props.get("batters_away"), er_a, rnd)
    lam_h = (props.get("ks_home") or {}).get("expected")   # home starter, faces away
    lam_a = (props.get("ks_away") or {}).get("expected")   # away starter, faces home

    home_runs = [0] * n
    away_runs = [0] * n
    home_k = [0] * n
    away_k = [0] * n
    home_win = [False] * n
    keys = ("hit", "tb", "hr", "r", "rbi", "sb", "dk")
    bat_h = {b["name"]: {k: [0] * n for k in keys} for b in setup_h}
    bat_a = {b["name"]: {k: [0] * n for k in keys} for b in setup_a}
    idx_h = [(b["name"], bat_h[b["name"]]) for b in setup_h]
    idx_a = [(b["name"], bat_a[b["name"]]) for b in setup_a]

    def store(stats, idx_map, i):
        for (name, arr), st in zip(idx_map, stats):
            arr["hit"][i] = st[0]; arr["tb"][i] = st[1]; arr["hr"][i] = st[2]
            arr["r"][i] = st[3]; arr["rbi"][i] = st[4]; arr["sb"][i] = st[5]
            arr["dk"][i] = st[6]

    for i in range(n):
        if setup_a:
            ra, sa = _play_game(setup_a, rnd); store(sa, idx_a, i)
        else:
            ra = _poisson(er_a)
        if setup_h:
            rh, sh = _play_game(setup_h, rnd); store(sh, idx_h, i)
        else:
            rh = _poisson(er_h)
        home_runs[i] = rh
        away_runs[i] = ra
        if rh > ra:
            home_win[i] = True
        elif rh == ra:
            home_win[i] = rnd() < 0.52
        if lam_h is not None:
            home_k[i] = _poisson(lam_h)
        if lam_a is not None:
            away_k[i] = _poisson(lam_a)

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

    def add(typ, label, pred, group=None):
        # `group` = the underlying market (a player, or ML/Total/Run line); a
        # parlay never stacks two legs from the same group.
        m = _mask(pred, n)
        marg = _popcount(m) / n
        if 0.04 <= marg <= 0.97:
            cands.append({"type": typ, "label": label, "mask": m, "marg": marg,
                          "group": group or typ})

    # Moneyline (both sides; contradictory pairs are pruned in the search).
    add("ML", f"{g.get('home_name', ha)} to win", lambda i: hwin[i])
    add("ML", f"{g.get('away_name', aa)} to win", lambda i: not hwin[i])
    # Run line -- Kalshi's adjustable "win by X+" for each side.
    for mgn in (2, 3):
        add("Run line", f"{ha} win by {mgn}+", lambda i, m=mgn: hr_runs[i] - ar_runs[i] >= m)
        add("Run line", f"{aa} win by {mgn}+", lambda i, m=mgn: ar_runs[i] - hr_runs[i] >= m)
    # Game total (a few lines around the model total).
    tot_mean = g.get("exp_total") or (er(g))
    base = round(tot_mean)
    for ln in (base - 0.5, base + 0.5, 8.5):
        if ln < 3.5:
            continue
        add("Total", f"Over {ln} runs", lambda i, ln=ln: (hr_runs[i] + ar_runs[i]) > ln)
        add("Total", f"Under {ln} runs", lambda i, ln=ln: (hr_runs[i] + ar_runs[i]) < ln)
    # Hitter props -- top 3 hitters per side. Hits / total bases / HR / HRR
    # (Hits+Runs+RBIs, Kalshi's combined player market), all from the same
    # base-running sim so they're correctly correlated with each other and runs.
    for side, store, bp_list in (("home", sim["bat"]["home"], props.get("batters_home")),
                                 ("away", sim["bat"]["away"], props.get("batters_away"))):
        ranked = sorted((bp_list or []),
                        key=lambda bp: (bp.get("hr1", 0) + bp.get("tb2", 0)), reverse=True)[:3]
        for j, bp in enumerate(ranked):
            nm = bp.get("name")
            st = store.get(nm)
            if not st:
                continue
            hit, tb, hr, r, rbi = st["hit"], st["tb"], st["hr"], st["r"], st["rbi"]
            grp = f"bat:{side}:{nm}"
            add("HR", f"{nm} 1+ HR", lambda i, a=hr: a[i] >= 1, grp)
            for m in (2, 3, 4):
                add("Bases", f"{nm} {m}+ total bases", lambda i, a=tb, m=m: a[i] >= m, grp)
            for m in (1, 2, 3):
                add("Hit", f"{nm} {m}+ hits", lambda i, a=hit, m=m: a[i] >= m, grp)
            for m in (2, 3, 4):
                add("HRR", f"{nm} {m}+ H+R+RBI",
                    lambda i, h=hit, rr=r, bb=rbi, m=m: h[i] + rr[i] + bb[i] >= m, grp)
    # Starter strikeouts -- best couple of lines per starter.
    hk, ak = sim["home_k"], sim["away_k"]
    if props.get("ks_home") and props.get("home_sp_name"):
        for line in (5, 6, 7):
            add("Ks", f"{props['home_sp_name']} {line}+ Ks",
                lambda i, L=line: hk[i] >= L, f"K:{props['home_sp_name']}")
    if props.get("ks_away") and props.get("away_sp_name"):
        for line in (5, 6, 7):
            add("Ks", f"{props['away_sp_name']} {line}+ Ks",
                lambda i, L=line: ak[i] >= L, f"K:{props['away_sp_name']}")
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


def _market_conflict(combo):
    """True if a parlay stacks two legs from the same market group -- two game
    totals, two run-line margins, two moneylines, or two props on the SAME
    player/pitcher (those are one correlated market, not independent picks)."""
    seen = set()
    for c in combo:
        g = c.get("group", c["type"])
        if g in seen:
            return True
        seen.add(g)
    return False


def _pool(cands, k=22):
    """Trim the candidate set for the combinatorial search: at most two lines per
    group (a safe one + an aggressive one), capped to k, so the search stays fast
    while still spanning safe favorites and longer-shot payouts."""
    by_group = {}
    for c in sorted(cands, key=lambda x: -x["marg"]):
        by_group.setdefault(c.get("group", c["type"]), []).append(c)
    pool = []
    for cs in by_group.values():
        pool.append(cs[0])
        if len(cs) > 1:
            pool.append(cs[-1])
    return sorted(pool, key=lambda x: -x["marg"])[:k]


def best_same_game(cands, n, n_legs, target, target_payout, max_legs):
    """Search same-game parlays and return the best item, or None.

    Payout mode (target_payout > 1): among combos whose fair payout reaches the
    target, take the most likely; otherwise take the highest-payout combo found.
    Otherwise: take the most likely combo of n_legs legs (meeting the target
    confidence when possible)."""
    cands = _pool(cands)
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
            if _redundant(masks) or _market_conflict(combo):
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


# --- Mixed multi-game parlays ------------------------------------------------
# A parlay can take several legs from one game (correlated -> simulated joint)
# AND single legs from other games (independent -> multiply across games). Each
# game contributes at most one "bundle" of 1..k legs with its simulated joint
# probability; the overall parlay probability is the product of the bundles.

def game_bundles(cands, n, max_legs=3, per_size=6):
    """Non-redundant leg bundles (size 1..max_legs) for one game, each with its
    simulated joint probability. Trimmed to the most useful per size: the safest
    few (high prob) and the longest-shot few (high payout, to reach a target)."""
    cs = _pool(cands, 14)
    bundles = []
    for sz in range(1, max_legs + 1):
        if sz > len(cs):
            break
        sized = []
        for combo in itertools.combinations(cs, sz):
            masks = [c["mask"] for c in combo]
            if sz > 1 and (_redundant(masks) or _market_conflict(combo)):
                continue
            jm = masks[0]
            for m in masks[1:]:
                jm &= m
            joint = _popcount(jm) / n
            if joint <= 0.005:
                continue
            sized.append((joint, combo))
        sized.sort(key=lambda x: x[0], reverse=True)
        keep = sized[:per_size] + sized[-per_size:]
        seen = set()
        for joint, combo in keep:
            key = tuple(sorted(c["label"] for c in combo))
            if key in seen:
                continue
            seen.add(key)
            bundles.append({"size": sz, "prob": joint, "legs": combo})
    return bundles


def _mixed_item(sel, games_bundles, target_payout=None):
    groups = []
    combined = indep = 1.0
    nlegs = 0
    for gi, b in sel:
        mu = games_bundles[gi][0]
        combined *= b["prob"]
        legs = []
        for c in b["legs"]:
            indep *= c["marg"]
            nlegs += 1
            legs.append({"pick": c["label"], "type": c["type"],
                         "prob_pct": round(c["marg"] * 100, 1)})
        groups.append({"matchup": mu, "size": b["size"],
                       "joint_pct": round(b["prob"] * 100, 1),
                       "same_game": b["size"] > 1, "legs": legs})
    groups.sort(key=lambda g: g["size"], reverse=True)
    return {
        "n_legs": nlegs, "n_games": len(groups), "groups": groups,
        "combined_prob_pct": round(combined * 100, 1),
        "indep_prob_pct": round(indep * 100, 1),
        "corr_delta_pct": round((combined - indep) * 100, 1),
        "fair_payout_x": round(1.0 / combined, 2) if combined > 0 else None,
        "indep_payout_x": round(1.0 / indep, 2) if indep > 0 else None,
        "target_payout_x": target_payout,
        "payout_reached": (target_payout is None) or
                          (combined > 0 and 1.0 / combined >= target_payout),
    }


def assemble_mixed(games_bundles, mode, n_legs, target_payout, max_total_legs=8):
    """Assemble one parlay across games, picking at most one bundle per game.

    'legs' mode: the most likely parlay with ~n_legs total legs.
    'payout' mode: the most likely parlay whose fair payout reaches the target
    (a covering knapsack on -log(prob) >= log(payout))."""
    if not games_bundles:
        return None

    if mode == "payout" and target_payout and target_payout > 1:
        threshold = math.log(target_payout)
        res = 0.04
        dp = {0: (0.0, [])}  # weight-bucket -> (actual -log(prob), selection)
        for gi, (_mu, bundles) in enumerate(games_bundles):
            nd = dict(dp)  # skip-this-game option
            for w, sel in dp.values():
                for b in bundles:
                    nw = w - math.log(b["prob"])
                    if nw > threshold + 2.0:           # don't overshoot wildly
                        continue
                    if sum(x.get("size", 1) for _g, x in sel) + b["size"] > max_total_legs:
                        continue
                    bk = int(nw / res)
                    if bk not in nd or nw < nd[bk][0]:
                        nd[bk] = (nw, sel + [(gi, b)])
            dp = nd
        best = None
        for w, sel in dp.values():
            if sum(b["size"] for _g, b in sel) < 2:
                continue
            reached = w >= threshold - 1e-9
            # Prefer parlays that reach the payout (then safest = min weight);
            # if none reach, take the highest payout (max weight) we can build.
            key = (1, -w) if reached else (0, w)
            if best is None or key > best[0]:
                best = (key, sel)
        return _mixed_item(best[1], games_bundles, target_payout) if best else None

    # legs mode: DP over total leg count, maximise probability (min -log).
    target = max(2, min(n_legs, max_total_legs))
    dp = {0: (0.0, [])}  # total legs -> (actual -log(prob), selection)
    for gi, (_mu, bundles) in enumerate(games_bundles):
        nd = dict(dp)
        for legs, (w, sel) in dp.items():
            for b in bundles:
                nl = legs + b["size"]
                if nl > max_total_legs:
                    continue
                nw = w - math.log(b["prob"])
                if nl not in nd or nw < nd[nl][0]:
                    nd[nl] = (nw, sel + [(gi, b)])
        dp = nd
    pick = [k for k in dp if k >= 2 and dp[k][1]]
    if not pick:
        return None
    k = min(pick, key=lambda x: (abs(x - target), x))
    return _mixed_item(dp[k][1], games_bundles)
