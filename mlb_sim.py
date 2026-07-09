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
lineup's simulated events (scaled so mean runs == the model's expected runs).
Each starter's strikeouts come from a pitch-count-aware staff simulation: he
throws until a sampled pitch limit (pulled earlier when the offense tags him),
then a random assortment of relievers (sampled ERA/WHIP/hand/K-rate from the
team's bullpen) finishes -- so Ks are capped by realistic workload and the
slip can report avg pitches before relief, IP, and the bullpen's Ks too.

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
    # Converge the rate multiplier until simulated mean runs sit within ~1.5% of
    # er. Two things made the old loop leave a systematic few-percent bias on the
    # TOTAL (as big as the edges we hunt): a loose 5% acceptance off noisy
    # 300-game samples, and a correction exponent assuming runs ~ mult^1.4 when
    # the true response is ~ mult^2.2 (more baserunners -> more PAs -> compound),
    # so every step overshot and the loop exited on oscillation peaks. We now
    # LEARN the local exponent from successive iterations (secant method).
    k_exp, prev = 0.45, None                 # 1/2.2 starting guess
    for _ in range(6):
        setup = _build_setup(rows, mult)
        mean = sum(_play_game(setup, rnd)[0] for _ in range(500)) / 500.0
        if mean <= 0.3:
            mult *= 1.5
            continue
        if abs(mean - er) <= 0.015 * er:     # close enough
            break
        # Learn the slope only from well-separated points (nearby mults give a
        # noise-dominated estimate that can send the next step wild), and keep
        # the exponent inside a sane band either way.
        if prev and prev[1] > 0.3 and abs(math.log(mult / prev[0])) > 0.03:
            k = math.log(mean / prev[1]) / math.log(mult / prev[0])
            if 0.8 < k < 4.0:                # sane local slope -> use it
                k_exp = max(0.3, min(0.7, 1.0 / k))
        prev = (mult, mean)
        # Clamp near 1.0 so matchup calibration nudges team runs to er without
        # badly distorting each hitter's true rate.
        mult = max(0.7, min(1.5, mult * (er / mean) ** k_exp))
    # Final centering pass: acceptance still fires on a noisy estimate, so one
    # more measurement plus a single corrective nudge (never re-checked, so it
    # can't oscillate) centers the residual around ~1%.
    setup = _build_setup(rows, mult)
    mean = sum(_play_game(setup, rnd)[0] for _ in range(700)) / 700.0
    if mean > 0.3:
        mult = max(0.7, min(1.5, mult * (er / mean) ** k_exp))
    return _build_setup(rows, mult)


_N_INNINGS = 9
# Calibrated effective first-inning scoring rate vs runs/9 (see props.RFI_K).
_RFI_K = 0.73


_DK_HIT = {1: 3, 2: 5, 3: 8, 4: 10}   # DraftKings hitter points by hit type


def _play_game(setup, rnd):
    """One full game for a lineup via base-out simulation, with speed-driven
    baserunning + stolen bases. Returns (runs, per-batter
    [hits, tb, hr, runs_scored, rbi, sb, dk_points]). dk_points is the batter's
    DraftKings fantasy total (1B+3 2B+5 3B+8 HR+10 R+2 RBI+2 BB+2 SB+5)."""
    L = len(setup)
    stats = [[0, 0, 0, 0, 0, 0, 0] for _ in range(L)]   # H,TB,HR,R,RBI,SB,DK
    runs = 0
    first_inning = 0                                     # runs scored in the 1st (for RFI)
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
        if _inn == 0:
            first_inning = runs
    return runs, stats, first_inning


_PA_PER_9 = 38.5   # plate appearances a staff faces over a 9-inning game


def _rel_kpa(bp_era, rnd):
    """K-per-PA for a fresh relief pitcher sampled around the bullpen's quality
    (relievers miss more bats; a better ERA implies a few more whiffs). Cheap --
    one draw, since it's hit several times per simulated game."""
    k9 = 9.2 + (rnd() + rnd() + rnd() - 1.5) * 2.4 - ((bp_era or 4.0) - 4.0) * 0.35
    return max(0.12, min(0.45, max(6.0, min(13.5, k9)) / _PA_PER_9))


# Average pitches per plate-appearance outcome (K / BB / hit / out-in-play). The
# per-outing pitch limit carries the variance, so per-PA counts are fixed -- much
# cheaper than a Gaussian draw on every pitch.
_PITCH = (4.7, 5.0, 3.4, 3.6)


def _sim_pitching(sp_k9, bp_era, bp_whip, opp_runs, rnd, bullpen=None, exp_ip=None,
                  er_opp=None, pen_out=0, budget=None, sp_bb_pa=None):
    """One game for a pitching staff against the opposing lineup.

    The starter throws until a sampled pitch limit (pulled earlier when he's
    being hit -- workload scales with the runs the offense actually scored this
    sim), then relievers (~1 inning apiece) finish. When the deep engine's named
    bullpen is supplied (`bullpen` = [{kpa, era}], best arm last) we cycle through
    the real relievers worst-first; otherwise we fall back to a generic K-rate
    draw off the bullpen's ERA. `pen_out` gassed arms (pitched back-to-back /
    heavy yesterday) are unavailable, thinning the BEST end of the pen. Returns
    the STARTER's (Ks, pitches, outs) and the bullpen's combined Ks."""
    sp_kpa = max(0.10, min(0.42, (sp_k9 or 8.0) / _PA_PER_9))
    # Correlate the starter's whiffs with THIS sim's game script: on a night the
    # opposing offense is quiet he misses more bats, on a night they're teeing
    # off he misses fewer. The tilt is mean-preserving (E[opp_runs] = er_opp) so
    # the K marginal stays calibrated -- it just gains the real K x opponent-runs
    # negative correlation (so "6+ Ks" x "opponent under" prices as correlated in
    # SGPs) and realistic overdispersion in the tails.
    if er_opp and er_opp > 0.5:
        # 0.17 lands the combined correlation (tilt + workload hook) in the
        # empirical -0.25..-0.35 band; 0.30 overshot to -0.42.
        tilt = 1.0 + 0.17 * (er_opp - opp_runs) / max(2.5, er_opp)
        sp_kpa = max(0.08, min(0.45, sp_kpa * max(0.80, min(1.22, tilt))))
    if pen_out and bullpen:
        bullpen = bullpen[:-int(pen_out)] or bullpen[:1]   # best arms sit tonight
    # More runs allowed => more traffic => more pitches and an earlier hook.
    hit_pa = max(0.16, min(0.34, 0.20 + (opp_runs - 4) * 0.012))
    # This starter's real walk rate: a wild arm issues more free passes, which
    # cost pitches and baserunners -- so he burns his budget and gets pulled
    # sooner (fewer innings, a lower strikeout ceiling). Falls back to league.
    bb_pa = sp_bb_pa if sp_bb_pa is not None else 0.078
    # This start's pitch-count cap: his stamina "budget" (walk-aware -- already
    # reflects that a wild arm won't be trusted as deep), else derived from his
    # expected innings at ~16.2 P/IP, else a league-ish 88.
    center = budget if budget else max(60.0, min(112.0, (exp_ip or 5.4) * 16.2))
    center = max(60.0, min(112.0, center))
    limit = max(52, min(120, random.gauss(center, 10)))
    pk, pbb, phit, pout = _PITCH
    sp_k = sp_outs = 0
    sp_pitches = 0.0
    sp_br = 0                                           # baserunners the starter allowed
    bull_k = 0
    outs = 0
    pa = 0
    starter_in = True
    rel_kpa = sp_kpa
    appr_outs = 0                                        # outs by the current reliever
    pen_i = [0]                                          # index into the named bullpen

    def next_reliever():
        # Named arms enter worst-first (closer held back); else a generic draw.
        if bullpen:
            arm = bullpen[min(pen_i[0], len(bullpen) - 1)]
            pen_i[0] += 1
            return max(0.12, min(0.45, arm["kpa"]))
        return _rel_kpa(bp_era, rnd)
    while outs < 27 and pa < 70:
        pa += 1
        kpa = sp_kpa if starter_in else rel_kpa
        u = rnd()
        if u < kpa:                                     # strikeout (an out)
            outs += 1; p = pk
            if starter_in:
                sp_k += 1; sp_outs += 1
            else:
                bull_k += 1; appr_outs += 1
        elif u < kpa + bb_pa:                           # walk
            p = pbb
            if starter_in:
                sp_br += 1
        elif u < kpa + bb_pa + hit_pa:                  # hit
            p = phit
            if starter_in:
                sp_br += 1
        else:                                           # out in play
            outs += 1; p = pout
            if starter_in:
                sp_outs += 1
            else:
                appr_outs += 1
        if starter_in:
            sp_pitches += p
            # Performance-aware hook ("rein him in or let him fly"): a starter
            # dealing a gem earns a longer leash (more Ks on the high lines), a
            # laboring one gets pulled sooner. Mirrors the deep engine.
            if sp_outs >= 15:                           # into the 6th+
                if sp_br == 0:                          # no-hitter/perfect — ride him
                    eff_limit, outs_cap = limit + 28, 27
                elif sp_br <= 3:                        # cruising
                    eff_limit, outs_cap = limit + 10, 24
                elif sp_br >= 9:                        # laboring — quicker hook
                    eff_limit, outs_cap = limit - 8, 19
                else:
                    eff_limit, outs_cap = limit, 21
            else:
                eff_limit, outs_cap = limit, 21
            if sp_pitches >= eff_limit or sp_outs >= outs_cap:
                starter_in = False; appr_outs = 0
                rel_kpa = next_reliever()
        elif appr_outs >= 3:                            # next reliever (~1 inning each)
            appr_outs = 0
            rel_kpa = next_reliever()
    return sp_k, int(round(sp_pitches)), sp_outs, bull_k


def simulate(g, n=5000):
    """Simulate game `g` n times via base-running. Returns shared per-sim arrays,
    including per-batter hits/total-bases/HR/runs/RBI for player props (HRR) and
    a full pitching sim (starter Ks/pitches/IP + bullpen Ks)."""
    props = g.get("props") or {}
    er_h = g.get("exp_runs_home") or 4.3
    er_a = g.get("exp_runs_away") or 4.3
    rnd = random.random
    setup_h = _team(props.get("batters_home"), er_h, rnd)
    setup_a = _team(props.get("batters_away"), er_a, rnd)
    lam_h = (props.get("ks_home") or {}).get("expected")   # home starter, faces away
    lam_a = (props.get("ks_away") or {}).get("expected")   # away starter, faces home

    # Pitching inputs: starter K/9 (fall back from expected Ks) + bullpen quality.
    # Each starter's expected innings ride along so his pitch limit is his own.
    hsp, asp = g.get("home_sp") or {}, g.get("away_sp") or {}
    ht, at = g.get("home_team") or {}, g.get("away_team") or {}
    ip_h = (props.get("ks_home") or {}).get("exp_ip") or 5.4
    ip_a = (props.get("ks_away") or {}).get("exp_ip") or 5.4
    home_k9 = hsp.get("k9") or (lam_h / ip_h * 9 if lam_h else None)
    away_k9 = asp.get("k9") or (lam_a / ip_a * 9 if lam_a else None)
    # Walk-aware workload: each starter's own pitch budget + real walk rate, so a
    # wild arm burns pitches faster and gets pulled sooner (fewer Ks).
    bud_h = hsp.get("est_pitches") or (props.get("ks_home") or {}).get("est_pitches")
    bud_a = asp.get("est_pitches") or (props.get("ks_away") or {}).get("est_pitches")
    bbpa_h, bbpa_a = hsp.get("bb_pa"), asp.get("bb_pa")
    # Gassed relievers (back-to-back days / heavy count yesterday) sit tonight:
    # thin the best end of each named pen in the pitching sim.
    pen_h = int((ht.get("bullpen_fatigue") or {}).get("count") or 0)
    pen_a = int((at.get("bullpen_fatigue") or {}).get("count") or 0)
    do_home_pitch = home_k9 is not None
    do_away_pitch = away_k9 is not None

    home_runs = [0] * n
    away_runs = [0] * n
    home_k = [0] * n
    away_k = [0] * n
    home_sp_pitch = [0] * n
    away_sp_pitch = [0] * n
    home_sp_outs = [0] * n
    away_sp_outs = [0] * n
    home_bull_k = [0] * n
    away_bull_k = [0] * n
    home_win = [False] * n
    rfi = [False] * n                       # a run scored in the 1st inning (either team)
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

    # First-inning scoring is bursty -- a base-out sim leading off with the top of
    # the order over-counts P(run), but the SIMULATED first frame carries the real
    # correlation with the rest of the game (a 1st-inning run and the Over cash
    # together). So we take the sim's own first-inning outcome and recalibrate its
    # marginal to the closed-form rate (_RFI_K, the empirical/market ~50%) by
    # thinning the yes's -- correlation preserved, marginal honest. A side with no
    # posted lineup falls back to an independent calibrated draw.
    p1a = 1 - math.exp(-_RFI_K * er_a / 9.0)
    p1h = 1 - math.exp(-_RFI_K * er_h / 9.0)
    p_target = 1 - (1 - p1a) * (1 - p1h)      # P(either team scores in the 1st)
    f1_raw = [False] * n                      # simulated 1st-inning run (either team)
    for i in range(n):
        if setup_a:
            ra, sa, f1a = _play_game(setup_a, rnd); store(sa, idx_a, i)
        else:
            ra = _poisson(er_a); f1a = 1 if rnd() < p1a else 0
        if setup_h:
            rh, sh, f1h = _play_game(setup_h, rnd); store(sh, idx_h, i)
        else:
            rh = _poisson(er_h); f1h = 1 if rnd() < p1h else 0
        home_runs[i] = rh
        away_runs[i] = ra
        f1_raw[i] = bool(f1a) or bool(f1h)
        if rh > ra:
            home_win[i] = True
        elif rh == ra:
            home_win[i] = rnd() < 0.52
        # Home staff faces the away offense (so its workload scales with away_runs).
        if do_home_pitch:
            sk, sp_p, sp_o, bk = _sim_pitching(home_k9, ht.get("bullpen_era"),
                                               ht.get("bullpen_whip"), ra, rnd,
                                               bullpen=ht.get("bp_arms"), exp_ip=ip_h,
                                               er_opp=er_a, pen_out=pen_h,
                                               budget=bud_h, sp_bb_pa=bbpa_h)
            home_k[i] = sk; home_sp_pitch[i] = sp_p; home_sp_outs[i] = sp_o
            home_bull_k[i] = bk
        if do_away_pitch:
            sk, sp_p, sp_o, bk = _sim_pitching(away_k9, at.get("bullpen_era"),
                                               at.get("bullpen_whip"), rh, rnd,
                                               bullpen=at.get("bp_arms"), exp_ip=ip_a,
                                               er_opp=er_h, pen_out=pen_a,
                                               budget=bud_a, sp_bb_pa=bbpa_a)
            away_k[i] = sk; away_sp_pitch[i] = sp_p; away_sp_outs[i] = sp_o
            away_bull_k[i] = bk

    # Recalibrate the simulated RFI marginal to the closed-form target: thin the
    # yes's when the sim runs hot (the usual case -- top of the order leads off),
    # or promote a few independent no's if it somehow runs cold. Thinning keeps
    # every retained yes tied to its simulated game, so RFI x Over / RFI x ML
    # correlations survive into the SGP masks.
    p_sim = sum(f1_raw) / n if n else 0.0
    if p_sim > p_target > 0:
        keep = p_target / p_sim
        for i in range(n):
            rfi[i] = f1_raw[i] and (rnd() < keep)
    elif p_sim < p_target:
        boost = (p_target - p_sim) / max(1e-9, 1.0 - p_sim)
        for i in range(n):
            rfi[i] = f1_raw[i] or (rnd() < boost)
    else:
        rfi = f1_raw

    return {"n": n, "home_runs": home_runs, "away_runs": away_runs,
            "home_k": home_k, "away_k": away_k, "home_win": home_win,
            "home_sp_pitch": home_sp_pitch, "away_sp_pitch": away_sp_pitch,
            "home_sp_outs": home_sp_outs, "away_sp_outs": away_sp_outs,
            "home_bull_k": home_bull_k, "away_bull_k": away_bull_k,
            "rfi": rfi, "bat": {"home": bat_h, "away": bat_a}}


def _ge_pct(arr, n, lines):
    """{line: % of sims with value >= line} for a per-sim integer array."""
    return {str(L): round(100 * sum(1 for x in arr if x >= L) / n, 1) for L in lines}


def _pitcher_line(name, k_arr, pitch_arr, outs_arr, bull_arr, n):
    """Simulated starter line: expected Ks, the K-threshold distribution (the
    '4+ K in X% of sims' the slip cares about), average pitches before relief,
    average IP, and the bullpen's combined Ks."""
    if not name:
        return None
    return {
        "name": name,
        "exp_k": round(sum(k_arr) / n, 1),
        "k_dist": _ge_pct(k_arr, n, (3, 4, 5, 6, 7, 8, 9, 10)),
        "avg_pitches": round(sum(pitch_arr) / n),
        "avg_ip": round(sum(outs_arr) / n / 3, 1),
        "bullpen_exp_k": round(sum(bull_arr) / n, 1),
    }


def _pitchers(g, sim):
    """Both starters' simulated lines (home starter faces away, and vice versa)."""
    n = sim["n"]
    props = g.get("props") or {}
    out = []
    for nm, kk, pp, oo, bb in (
        (props.get("home_sp_name"), "home_k", "home_sp_pitch", "home_sp_outs", "home_bull_k"),
        (props.get("away_sp_name"), "away_k", "away_sp_pitch", "away_sp_outs", "away_bull_k")):
        line = _pitcher_line(nm, sim[kk], sim[pp], sim[oo], sim[bb], n)
        if line:
            out.append(line)
    return out


def summary(sim, top=6, g=None):
    """Win %, total-runs distribution, per-player expected line, and (when the
    game `g` is given) the simulated starter lines -- for the game-sim UI."""
    n = sim["n"]
    hr_runs, ar_runs, hwin = sim["home_runs"], sim["away_runs"], sim["home_win"]
    totals = sorted(hr_runs[i] + ar_runs[i] for i in range(n))
    pct = lambda f: totals[min(n - 1, int(f * n))]
    home_w = sum(hwin) / n
    players = {}
    for side in ("home", "away"):
        rows = []
        for name, a in sim["bat"][side].items():
            rows.append({"name": name,
                         "hits": round(sum(a["hit"]) / n, 2),
                         "hr": round(sum(a["hr"]) / n, 2),
                         "tb": round(sum(a["tb"]) / n, 2),
                         "sb": round(sum(a["sb"]) / n, 2),
                         "dk": round(sum(a["dk"]) / n, 1)})
        rows.sort(key=lambda r: -r["dk"])
        players[side] = rows[:top]
    return {"home_win_pct": round(home_w * 100, 1),
            "away_win_pct": round((1 - home_w) * 100, 1),
            "median_total": pct(0.5), "p10_total": pct(0.1), "p90_total": pct(0.9),
            "players": players, "has_players": bool(players["home"] or players["away"]),
            "pitchers": _pitchers(g, sim) if g else []}


def deep_breakdown(g, sim, top_hitters=6):
    """Per-pitcher and per-hitter simulated distributions for one game -- the
    detail behind a same-game slip (every starter's K spread + avg pitches/IP +
    bullpen Ks, and each hitter's expected line + threshold odds)."""
    n = sim["n"]
    hitters = {}
    for side in ("home", "away"):
        rows = []
        for name, a in sim["bat"][side].items():
            rows.append({
                "name": name,
                "exp_hits": round(sum(a["hit"]) / n, 2),
                "exp_tb": round(sum(a["tb"]) / n, 2),
                "exp_hr": round(sum(a["hr"]) / n, 2),
                "exp_sb": round(sum(a["sb"]) / n, 2),
                "hits_dist": _ge_pct(a["hit"], n, (1, 2, 3)),
                "tb_dist": _ge_pct(a["tb"], n, (2, 3, 4)),
                "p_hr": round(100 * sum(1 for x in a["hr"] if x >= 1) / n, 1)})
        rows.sort(key=lambda r: -(r["exp_tb"] + r["exp_hr"]))
        hitters[side] = rows[:top_hitters]
    return {"n_sims": n, "pitchers": _pitchers(g, sim), "hitters": hitters}


def _mask(pred, n):
    m = 0
    for i in range(n):
        if pred(i):
            m |= (1 << i)
    return m


def build_candidates(g, sim, types=None):
    """Curated set of bettable legs for this game, each as a sim bitmask.

    Kept deliberately small (one leg per market, top hitters only) so the combo
    search stays fast while still spanning moneyline / run line / total / hitter
    props / starter strikeouts. `types` (a set of type names) restricts which prop
    kinds are produced.
    """
    n = sim["n"]
    hr_runs, ar_runs, hwin = sim["home_runs"], sim["away_runs"], sim["home_win"]
    ha = g.get("home_abbr") or g.get("home_name") or "Home"
    aa = g.get("away_abbr") or g.get("away_name") or "Away"
    props = g.get("props") or {}
    cands = []

    # Simulated per-game averages, so a combo leg can show "avg sim 9.1 runs".
    _mean = lambda arr: (sum(arr) / n) if n else 0.0
    mean_total = round(_mean([hr_runs[i] + ar_runs[i] for i in range(n)]), 1)
    mean_margin = round(_mean([hr_runs[i] - ar_runs[i] for i in range(n)]), 1)

    def add(typ, label, pred, group=None, model=None, kref=None, avg=None, unit=None):
        if types and typ not in types:
            return
        # `group` = the underlying market (a player, or ML/Total/Run line); a
        # parlay never stacks two legs from the same group. `model` is the closed-
        # form (exact-math) probability for player props, kept alongside the
        # simulated marginal so the UI can show both. `kref` is a structured key
        # used to look up this leg's live Kalshi price (see kalshi_mlb). `avg`/
        # `unit` are the simulated average for this market (runs, K, hits, …).
        m = _mask(pred, n)
        marg = _popcount(m) / n
        if 0.04 <= marg <= 0.97:
            cands.append({"type": typ, "label": label, "mask": m, "marg": marg,
                          "group": group or typ, "model_pct": model, "kref": kref,
                          "sim_avg": avg, "avg_unit": unit})

    # Moneyline (both sides; contradictory pairs are pruned in the search). The
    # closed-form win prob (g.p_home/p_away) rides along as the model number.
    ph, pa = g.get("p_home"), g.get("p_away")
    add("ML", f"{g.get('home_name', ha)} to win", lambda i: hwin[i],
        model=round(ph * 100, 1) if ph is not None else None,
        kref={"t": "ml", "team": ha}, avg=mean_margin, unit="run margin")
    add("ML", f"{g.get('away_name', aa)} to win", lambda i: not hwin[i],
        model=round(pa * 100, 1) if pa is not None else None,
        kref={"t": "ml", "team": aa}, avg=round(-mean_margin, 1), unit="run margin")
    # Run line -- Kalshi's adjustable "win by X+" for each side. analyze_slate has
    # trimmed the spread ladders to the margins Kalshi books, so iterate those (with
    # a sane 2/3 fallback for a bare game dict). The marginal filter below still
    # drops any margin too unlikely to be useful.
    rl = props.get("run_line") or {}
    home_by, away_by = rl.get("home_by") or {}, rl.get("away_by") or {}
    def _margins(by_map):
        ms = sorted(int(k) for k in by_map) if by_map else [2, 3]
        return ms
    for mgn in _margins(home_by):
        add("Run line", f"{ha} win by {mgn}+", lambda i, m=mgn: hr_runs[i] - ar_runs[i] >= m,
            model=home_by.get(str(mgn)), kref={"t": "spread", "team": ha, "by": mgn},
            avg=mean_margin, unit="run margin")
    for mgn in _margins(away_by):
        add("Run line", f"{aa} win by {mgn}+", lambda i, m=mgn: ar_runs[i] - hr_runs[i] >= m,
            model=away_by.get(str(mgn)), kref={"t": "spread", "team": aa, "by": mgn},
            avg=round(-mean_margin, 1), unit="run margin")
    # Game total -- iterate the totals ladder, which analyze_slate has already
    # trimmed to lines Kalshi actually books (no phantom 'Over 15.5' the sportsbook
    # won't quote). Each ladder entry carries the closed-form over/under %.
    ladder = {round(t["line"], 1): t for t in (props.get("totals_ladder") or [])}
    if not ladder:                               # bare game dict (no analyze_slate) -> sane window
        tot_mean = g.get("exp_total") or er(g)
        base = round(tot_mean)
        ladder = {n + 0.5: None for n in range(max(6, base - 3), base + 4)}
    for ln in sorted(ladder):
        t = ladder[ln]
        kn = int(ln + 0.5)                       # Kalshi total market suffix (Over ln)
        add("Total", f"Over {ln} runs", lambda i, ln=ln: (hr_runs[i] + ar_runs[i]) > ln,
            model=(t["over_pct"] if t else None), kref={"t": "total", "n": kn, "over": True},
            avg=mean_total, unit="runs")
        add("Total", f"Under {ln} runs", lambda i, ln=ln: (hr_runs[i] + ar_runs[i]) < ln,
            model=(t["under_pct"] if t else None), kref={"t": "total", "n": kn, "over": False},
            avg=mean_total, unit="runs")
    # RFI -- a run in the 1st inning (either team). Kalshi lists only the YES
    # side (you pick "yes there's a run"), so we don't offer a "No" leg. The
    # closed-form rfi_pct rides along as the model number.
    rfi = sim.get("rfi")
    rfi_pct = g.get("props", {}).get("rfi_pct") if isinstance(g.get("props"), dict) else None
    if rfi is not None:
        add("RFI", "Run in the 1st inning", lambda i: rfi[i], "RFI", rfi_pct, {"t": "rfi"})
    # Hitter props -- the WHOLE posted lineup (ranked best-first for display), so
    # combos and DK Pick 6 can reach a lower-order value bat, not just the top of
    # the order. The marginal filter below drops any line too unlikely to matter,
    # so the pool stays clean. Hits / total bases / HR / HRR (Hits+Runs+RBIs),
    # all from the same base-running sim so they're correctly correlated.
    for side, store, bp_list in (("home", sim["bat"]["home"], props.get("batters_home")),
                                 ("away", sim["bat"]["away"], props.get("batters_away"))):
        ranked = sorted((bp_list or []),
                        key=lambda bp: (bp.get("hr1", 0) + bp.get("tb2", 0)), reverse=True)[:9]
        for j, bp in enumerate(ranked):
            nm = bp.get("name")
            st = store.get(nm)
            if not st:
                continue
            hit, tb, hr, r, rbi = st["hit"], st["tb"], st["hr"], st["r"], st["rbi"]
            grp = f"bat:{side}:{nm}"
            a_hit, a_tb, a_hr = round(_mean(hit), 2), round(_mean(tb), 2), round(_mean(hr), 2)
            a_hrr = round(_mean([hit[i] + r[i] + rbi[i] for i in range(n)]), 2)
            # `bp` carries the closed-form model % for each line (hit1.., tb2..,
            # hr1..); pass it as `model` so legs show model vs simulated.
            for m in (1, 2):
                add("HR", f"{nm} {m}+ HR", lambda i, a=hr, m=m: a[i] >= m, grp,
                    bp.get(f"hr{m}"), {"t": "hr", "player": nm, "line": m}, avg=a_hr, unit="HR")
            for m in (2, 3, 4, 5, 6, 7):
                add("Bases", f"{nm} {m}+ total bases", lambda i, a=tb, m=m: a[i] >= m, grp,
                    bp.get(f"tb{m}"), {"t": "tb", "player": nm, "line": m}, avg=a_tb, unit="bases")
            for m in (1, 2, 3, 4):
                add("Hit", f"{nm} {m}+ hits", lambda i, a=hit, m=m: a[i] >= m, grp,
                    bp.get(f"hit{m}"), {"t": "hit", "player": nm, "line": m}, avg=a_hit, unit="hits")
            for m in (2, 3, 4, 5, 6):   # HRR is a combined market — no closed form
                add("HRR", f"{nm} {m}+ H+R+RBI",
                    lambda i, h=hit, rr=r, bb=rbi, m=m: h[i] + rr[i] + bb[i] >= m, grp,
                    None, {"t": "hrr", "player": nm, "line": m}, avg=a_hrr, unit="H+R+RBI")
    # Starter strikeouts -- full ladder per starter (the high lines are the long
    # odds); the marginal filter drops any that are too unlikely. The closed-form
    # Poisson % lives in the ks_* dict (string keys).
    hk, ak = sim["home_k"], sim["away_k"]
    ks_h, ks_a = props.get("ks_home") or {}, props.get("ks_away") or {}
    # Starter's season innings ride along so the edge finder can distrust a K
    # ladder built off a tiny sample (a rookie's K/9 is mostly noise early).
    hsp_ip = (g.get("home_sp") or {}).get("ip")
    asp_ip = (g.get("away_sp") or {}).get("ip")
    K_LINES = (4, 5, 6, 7, 8, 9, 10)
    mean_hk, mean_ak = round(_mean(hk), 1), round(_mean(ak), 1)
    if ks_h and props.get("home_sp_name"):
        for line in K_LINES:
            add("Ks", f"{props['home_sp_name']} {line}+ Ks",
                lambda i, L=line: hk[i] >= L, f"K:{props['home_sp_name']}", ks_h.get(str(line)),
                {"t": "ks", "player": props["home_sp_name"], "line": line, "sp_ip": hsp_ip},
                avg=mean_hk, unit="K")
    if ks_a and props.get("away_sp_name"):
        for line in K_LINES:
            add("Ks", f"{props['away_sp_name']} {line}+ Ks",
                lambda i, L=line: ak[i] >= L, f"K:{props['away_sp_name']}", ks_a.get(str(line)),
                {"t": "ks", "player": props["away_sp_name"], "line": line, "sp_ip": asp_ip},
                avg=mean_ak, unit="K")
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


# Two legs "counteract" when the game states that make one hit tend to make the
# other MISS -- e.g. "Wacha 5+ K" needs him dealing, but "Phillies (his
# opponent) win" and "Over 6.5 runs" both want him hit. We read that straight off
# the sim as the phi correlation between the two legs' hit-masks; a pair below
# _COUNTER_PHI is treated as fighting itself and kept out of a combo when we can.
_COUNTER_PHI = -0.12


def _phi(ma, mb, pa, pb, n):
    """phi (mean-square) correlation between two binary hit-masks, given each
    mask's popcount (pa, pb) and the sim count n. Range -1..1; <0 = counteracting."""
    n11 = _popcount(ma & mb)
    da, db = pa * (n - pa), pb * (n - pb)
    if da <= 0 or db <= 0:
        return 0.0                              # a leg that always/never hits can't conflict
    return (n * n11 - pa * pb) / (da * db) ** 0.5


def _corr_matrix(cands, n):
    """Pairwise phi for a pooled candidate list, keyed by (i, j) with i < j."""
    pcs = [_popcount(c["mask"]) for c in cands]
    phi = {}
    for i in range(len(cands)):
        for j in range(i + 1, len(cands)):
            phi[(i, j)] = _phi(cands[i]["mask"], cands[j]["mask"], pcs[i], pcs[j], n)
    return phi


def _worst_pair(idxs, phi):
    """Most-negative pairwise phi among a set of leg indices (0.0 if <2 legs)."""
    worst = 0.0
    for a in range(len(idxs)):
        for b in range(a + 1, len(idxs)):
            i, j = idxs[a], idxs[b]
            worst = min(worst, phi.get((i, j) if i < j else (j, i), 0.0))
    return worst


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


def best_same_game(cands, n, n_legs, target, target_payout, max_legs,
                   budget=400_000):
    """Search same-game parlays and return the best item, or None.

    Payout mode (target_payout > 1): among combos whose fair payout reaches the
    target, take the most likely; otherwise take the highest-payout combo found.
    Otherwise: take the most likely combo of n_legs legs (meeting the target
    confidence when possible)."""
    cands = _pool(cands)
    if len(cands) < 2:
        return None
    phi = _corr_matrix(cands, n)
    payout_mode = bool(target_payout and target_payout > 1)
    sizes = range(2, max_legs + 1) if payout_mode else [max(2, min(n_legs, max_legs))]

    # Track the best CLEAN combo (no counteracting pair) and, separately, the best
    # of any combo -- so if a slate genuinely can't field a clean parlay we still
    # return something (flagged) rather than nothing.
    best_clean = None  # (score, idxs, joint, worst_phi)
    best_any = None
    # `budget` = total combos across every size we try (keeps big-slip searches
    # responsive; the caller splits it across a multi-game slate).
    reached_at = None                   # first size where the payout target was hit
    for sz in sizes:
        if sz > len(cands) or budget < 5_000:
            break
        # In payout mode, once a combo reaches the target, a couple more sizes is
        # enough -- extra legs only lower the joint probability from there.
        if reached_at is not None and sz > reached_at + 2:
            break
        # Big slips would make C(pool, sz) explode (C(22,11) ~ 705k heavy-mask
        # combos). The pool is sorted best-first, so shrink to a prefix that keeps
        # the enumeration inside the budget -- large slips necessarily draw from
        # the top candidates anyway.
        k = len(cands)
        while k > sz and math.comb(k, sz) > min(150_000, budget):
            k -= 1
        budget -= math.comb(k, sz)
        for idxs in itertools.combinations(range(k), sz):
            combo = [cands[i] for i in idxs]
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
                    if reached_at is None:
                        reached_at = sz
                else:
                    score = payout                # not reached -> chase max payout
            else:
                score = (1000.0 + payout) if joint >= target else joint
            worst = _worst_pair(idxs, phi)
            if best_any is None or score > best_any[0]:
                best_any = (score, idxs, joint, worst)
            if worst >= _COUNTER_PHI and (best_clean is None or score > best_clean[0]):
                best_clean = (score, idxs, joint, worst)

    best = best_clean or best_any
    if not best:
        return None
    _, idxs, joint, worst_phi = best
    combo = [cands[i] for i in idxs]
    indep = 1.0
    for c in combo:
        indep *= c["marg"]
    return {
        "n_legs": len(combo),
        "legs": [{"pick": c["label"], "type": c["type"],
                  "prob_pct": round(c["marg"] * 100, 1),
                  "model_pct": c.get("model_pct"), "kref": c.get("kref"),
                  "sim_avg": c.get("sim_avg"), "avg_unit": c.get("avg_unit"),
                  "sims_hit": int(round(c["marg"] * n))} for c in combo],
        "combined_sims_hit": int(round(joint * n)),
        "combined_prob_pct": round(joint * 100, 1),
        "indep_prob_pct": round(indep * 100, 1),
        "corr_delta_pct": round((joint - indep) * 100, 1),
        "fair_payout_x": round(1.0 / joint, 2) if joint > 0 else None,
        "indep_payout_x": round(1.0 / indep, 2) if indep > 0 else None,
        "worst_pair_corr": round(worst_phi, 2),
        "counteracting": worst_phi < _COUNTER_PHI,
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
    phi = _corr_matrix(cs, n)
    bundles = []
    for sz in range(1, max_legs + 1):
        if sz > len(cs):
            break
        sized = []
        for idxs in itertools.combinations(range(len(cs)), sz):
            combo = [cs[i] for i in idxs]
            masks = [c["mask"] for c in combo]
            if sz > 1 and (_redundant(masks) or _market_conflict(combo)
                           or _worst_pair(idxs, phi) < _COUNTER_PHI):
                continue                          # keep counteracting legs out of a bundle
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
        entry = games_bundles[gi]
        mu = entry[0]
        suffix = entry[2] if len(entry) > 2 else None
        combined *= b["prob"]
        legs = []
        for c in b["legs"]:
            indep *= c["marg"]
            nlegs += 1
            legs.append({"pick": c["label"], "type": c["type"],
                         "prob_pct": round(c["marg"] * 100, 1),
                         "model_pct": c.get("model_pct"), "kref": c.get("kref"),
                         "sim_avg": c.get("sim_avg"), "avg_unit": c.get("avg_unit")})
        groups.append({"matchup": mu, "size": b["size"], "suffix": suffix,
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


def assemble_mixed(games_bundles, legs_target, payout_target,
                   legs_mode="prefer", payout_mode="off", conn="or",
                   max_total_legs=8):
    """Assemble one parlay across games under two optional, combinable targets:
    a leg count and a fair payout. Each target is "require" (hard), "prefer"
    (recommendation -- nudges the pick but never blocks), or "off". When both are
    "require", `conn` ('and'/'or') says whether both must hold or just one.

    Method: a DP gives the most-likely parlay at every total leg count (the
    frontier). We then pick the leg count whose parlay best satisfies the active
    targets, breaking ties toward the safest (most likely) parlay -- or, when a
    payout target isn't yet reached, toward the bigger payout."""
    if not games_bundles:
        return None
    # DP over selections keyed by (total legs, -log-prob bucket) so the frontier
    # spans BOTH leg counts and payout levels -- letting us reach a payout target
    # with riskier legs, not just by piling on safe ones.
    RES = 0.05
    dp = {(0, 0): (0.0, [])}                  # (legs, bucket) -> (-log prob, selection)
    for gi, (_mu, bundles, *_rest) in enumerate(games_bundles):
        nd = dict(dp)
        for (legs, _bk), (w, sel) in dp.items():
            for b in bundles:
                nl = legs + b["size"]
                if nl > max_total_legs:
                    continue
                nw = w - math.log(b["prob"])
                key = (nl, int(nw / RES))
                if key not in nd or nw < nd[key][0]:
                    nd[key] = (nw, sel + [(gi, b)])
        dp = nd
    states = []
    for (legs, _bk), (w, sel) in dp.items():
        if legs < 2 or not sel:
            continue
        prob = math.exp(-w)
        states.append({"legs": legs, "prob": prob,
                       "payout": (1.0 / prob if prob > 0 else None), "sel": sel})
    if not states:
        return None

    want_legs = legs_mode in ("require", "prefer")
    want_payout = payout_mode in ("require", "prefer") and bool(payout_target and payout_target > 1)
    X = max(2, min(legs_target or 2, max_total_legs))
    Y = payout_target or 0
    meets_legs = lambda s: s["legs"] == X
    meets_payout = lambda s: s["payout"] is not None and s["payout"] >= Y

    # Hard filter from "require" targets, combined by conn.
    reqs = []
    if legs_mode == "require":
        reqs.append(meets_legs)
    if payout_mode == "require" and want_payout:
        reqs.append(meets_payout)
    feasible, hard_ok = states, True
    if reqs:
        combine = all if conn == "and" else any
        feas = [s for s in states if combine(r(s) for r in reqs)]
        if feas:
            feasible = feas
        else:
            hard_ok = False                  # unsatisfiable -> best effort over all

    def rank(s):
        mp, ml = meets_payout(s), meets_legs(s)
        primary = (1 if want_payout and mp else 0) + (1 if want_legs and ml else 0)
        # Safest by default; if chasing an unmet payout, prefer the bigger payout.
        secondary = s["payout"] if (want_payout and not mp) else s["prob"]
        return (primary, secondary)

    best = max(feasible, key=rank)
    item = _mixed_item(best["sel"], games_bundles, Y if want_payout else None)
    item["legs_target"] = X if want_legs else None
    item["legs_met"] = meets_legs(best) if want_legs else None
    item["payout_reached"] = meets_payout(best) if want_payout else None
    item["hard_ok"] = hard_ok
    return item
