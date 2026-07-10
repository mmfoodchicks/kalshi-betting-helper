"""Derived MLB betting props from the expected-runs model.

Given each side's expected runs we model each team's runs as an independent
Poisson, which yields a full distribution and therefore:
  - run line (win by 2+, i.e. -1.5) for each side
  - game total runs over/under for several lines
  - team total hits over/under

Player hit props (1+ / 2+ hits) use a binomial over a batter's expected plate
appearances and a per-PA hit rate adjusted for the opposing pitching.
"""

import math

LEAGUE_HIT_RATE = 0.225          # ~ hits per plate appearance, league-ish
PA_BY_SLOT = [4.7, 4.6, 4.5, 4.3, 4.2, 4.1, 4.0, 3.9, 3.8]  # expected PA by lineup spot
_MAXR = 22                       # cap runs for the Poisson grid
# Effective first-inning scoring rate vs runs/9. Real innings are bursty, so a
# team scores in a given inning much less than Poisson(runs/9) implies; this
# calibrates a league-average game's RFI to the empirical/market ~50%.
RFI_K = 0.73


def _poisson_pmf(lam, kmax=_MAXR):
    pmf = []
    p = math.exp(-lam)
    for k in range(kmax + 1):
        pmf.append(p)
        p = p * lam / (k + 1)
    return pmf


_SPREAD_MARGINS = (2, 3, 4, 5, 6, 7, 8, 9, 10)


def _spread_ladder(margin):
    """Each side's P(win by >= m) -- Kalshi's adjustable spread ('wins by over
    1.5/2.5/.../8.5 runs'). The full ladder (not just +/-2) lets the combo maker
    pick a blowout line for longer odds. String keys for clean JSON; near-zero
    lines are dropped downstream by the variant filter."""
    home = {str(m): round(sum(p for d, p in margin.items() if d >= m) * 100, 1)
            for m in _SPREAD_MARGINS}
    away = {str(m): round(sum(p for d, p in margin.items() if d <= -m) * 100, 1)
            for m in _SPREAD_MARGINS}
    return home, away


def game_props(er_home, er_away, home_abbr, away_abbr):
    """Run line + game total over/unders from independent-Poisson run dists."""
    ph = _poisson_pmf(er_home)
    pa = _poisson_pmf(er_away)

    p_home_win = p_away_win = p_tie = 0.0
    p_home_by2 = p_away_by2 = 0.0
    total_pmf = [0.0] * (2 * _MAXR + 1)
    margin = {}                                    # home_runs - away_runs -> prob
    for i, phi in enumerate(ph):
        for j, paj in enumerate(pa):
            pr = phi * paj
            total_pmf[i + j] += pr
            margin[i - j] = margin.get(i - j, 0.0) + pr
            if i > j:
                p_home_win += pr
                if i - j >= 2:
                    p_home_by2 += pr
            elif j > i:
                p_away_win += pr
                if j - i >= 2:
                    p_away_by2 += pr
            else:
                p_tie += pr
    # Split ties (extra innings) with a small home edge.
    p_home_win += p_tie * 0.52
    p_away_win += p_tie * 0.48

    def over_under(line):
        # P(total > line). line is a .5 number so no pushes.
        under = sum(total_pmf[k] for k in range(len(total_pmf)) if k < line)
        return round((1 - under) * 100, 1), round(under * 100, 1)

    model_total = er_home + er_away
    lines = sorted({round(model_total) - 0.5, round(model_total) + 0.5, 8.5})
    totals = []
    for ln in lines:
        over, under = over_under(ln)
        totals.append({"line": ln, "over_pct": over, "under_pct": under})

    # Full half-run ladder (every alternate line, not just a +/-2 window) so the
    # combo maker can push the line well past the model total for longer odds.
    # Lines run from 0.5 up to comfortably beyond the model total; the combo
    # builder filters by usable probability, so far-out lines are available when
    # you want the payout. _MAXR caps the run grid, so cap the ladder to match.
    base = round(model_total)
    ladder = []
    for ln in [n + 0.5 for n in range(0, min(base + 10, 2 * _MAXR))]:
        over, under = over_under(ln)
        ladder.append({"line": ln, "over_pct": over, "under_pct": under})

    # RFI (a Run scored in the First Inning, either team). A naive Poisson on
    # runs/inning badly over-counts: real innings are bursty (zero-inflated), so
    # a team scores in a given inning far less often than Poisson(runs/9) implies.
    # RFI_K calibrates the effective rate so a league-average game lands near the
    # empirical ~50% (and Kalshi's ~50c price); it already nets the top-of-order
    # first-inning boost. YES = at least one team scores: 1 - P(no run each half).
    lam1_a = er_away / 9.0 * RFI_K
    lam1_h = er_home / 9.0 * RFI_K
    rfi_yes = 1 - math.exp(-lam1_a) * math.exp(-lam1_h)

    home_by, away_by = _spread_ladder(margin)
    return {
        "rfi_pct": round(rfi_yes * 100, 1),
        # Kalshi run line = "[team] wins by over X.5 runs" with an adjustable
        # line. home_by["2"] = P(win by 2+) = the 1.5 line, ["3"] = the 2.5 line,
        # etc. A 1-run game hits neither side's spread.
        "run_line": {
            "home": home_abbr, "home_by2_pct": round(p_home_by2 * 100, 1), "home_by": home_by,
            "away": away_abbr, "away_by2_pct": round(p_away_by2 * 100, 1), "away_by": away_by,
        },
        "model_total": round(model_total, 1),
        "totals": totals,
        "totals_ladder": ladder,
        "win_pct": {"home": round(p_home_win * 100, 1), "away": round(p_away_win * 100, 1)},
    }


def live_game_props(cur_home, cur_away, rem_home_mean, rem_away_mean,
                    home_abbr, away_abbr):
    """Run line + totals for a game IN PROGRESS: the final score is the current
    score plus remaining runs (independent Poisson). This keeps totals honest
    once a game is underway -- a 13-run 2nd inning makes 'Over 8.5' ~100%, not
    the stale pre-game number."""
    ph = _poisson_pmf(max(0.0001, rem_home_mean))
    pa = _poisson_pmf(max(0.0001, rem_away_mean))
    cur_total = cur_home + cur_away
    total_pmf = [0.0] * (2 * _MAXR + 1)   # over REMAINING runs
    p_home_by2 = p_away_by2 = 0.0          # final margin (current + remaining)
    margin = {}                            # final home - away margin -> prob
    for i, phi in enumerate(ph):
        for j, paj in enumerate(pa):
            pr = phi * paj
            total_pmf[i + j] += pr
            fh, fa = cur_home + i, cur_away + j
            margin[fh - fa] = margin.get(fh - fa, 0.0) + pr
            if fh - fa >= 2:
                p_home_by2 += pr
            elif fa - fh >= 2:
                p_away_by2 += pr

    def over_under(line):                  # line is on the FINAL total
        rem_line = line - cur_total
        under = sum(total_pmf[k] for k in range(len(total_pmf)) if k < rem_line)
        return round((1 - under) * 100, 1), round(under * 100, 1)

    exp_final = cur_total + rem_home_mean + rem_away_mean
    base = round(exp_final)
    ladder = []
    # Full half-run ladder from the current total upward (lines below the score
    # are already decided) so alternate totals can be pushed far out for odds.
    lo = max(0, int(cur_total))
    for ln in [n + 0.5 for n in range(lo, min(base + 10, 2 * _MAXR))]:
        over, under = over_under(ln)
        ladder.append({"line": ln, "over_pct": over, "under_pct": under})

    home_by, away_by = _spread_ladder(margin)
    return {
        # Kalshi run line ("[team] wins by over X.5 runs", adjustable line),
        # computed off the FINAL margin (current score + remaining runs).
        "run_line": {
            "home": home_abbr, "home_by2_pct": round(p_home_by2 * 100, 1), "home_by": home_by,
            "away": away_abbr, "away_by2_pct": round(p_away_by2 * 100, 1), "away_by": away_by,
        },
        "model_total": round(exp_final, 1),
        "totals_ladder": ladder,
    }


def in_game_win_prob(home_cur, away_cur, rem_home_mean, rem_away_mean):
    """P(home wins) given the current score and each side's expected remaining
    runs, modeling remaining runs as independent Poisson. Ties go to extra
    innings with a small home edge."""
    ph = _poisson_pmf(max(0.0001, rem_home_mean))
    pa = _poisson_pmf(max(0.0001, rem_away_mean))
    p_home = p_away = p_tie = 0.0
    for i, phi in enumerate(ph):
        for j, paj in enumerate(pa):
            pr = phi * paj
            h, a = home_cur + i, away_cur + j
            if h > a:
                p_home += pr
            elif a > h:
                p_away += pr
            else:
                p_tie += pr
    return p_home + p_tie * 0.52


def _speed_inputs(b, sprint):
    """Speed factor (0.8 slow .. 1.2 fast, 27 ft/s = 1.0) and steal-attempt rate
    per time on first, from sprint speed + season SB/CS."""
    spd = 1.0
    if sprint:
        spd = max(0.8, min(1.2, 1.0 + (sprint - 27.0) * 0.06))
    singles = max(0.0, (b.get("hits") or 0) - (b.get("doubles") or 0)
                  - (b.get("triples") or 0) - (b.get("hr") or 0))
    on1 = singles + (b.get("bb") or 0)
    sbr = min(0.40, (b.get("sb") or 0) / on1) if on1 > 0 else 0.0
    return round(spd, 3), round(sbr, 4)


def batter_props(b, slot, opp_hit_factor=1.0, contact_mult=1.0, power_mult=1.0, sprint=None,
                 hr_env=1.0):
    """Exact per-game prop probabilities for a hitter (the limit a simulation
    converges to). Returns 1+/2+ hits, 1+ HR, 2+ and 3+ total bases.

    Also returns the per-plate-appearance outcome rates (r1/r2/r3/rhr/rbb) and
    expected PA so the same-game simulator can draw this hitter from the exact
    same marginal distribution -- the closed-form props and the simulation stay
    consistent by construction.
    """
    spa = b.get("pa") or 0
    if spa <= 0:
        return None
    pa = PA_BY_SLOT[slot] if slot < 9 else 3.8
    k = max(1, int(round(pa)))
    # Substitution retention: a batter whose season PA-per-game runs below even a
    # full-retention #9 hitter (3.8) is getting pinch-hit for / lifted late, and
    # today's slot won't change that. Only that unambiguous deficit is penalized
    # (a low PA/G that could just mean "bats 9th" is NOT), shrunk toward 1.0 on
    # small samples. The game sim uses this to sub him out late; stars stay 1.0.
    g = b.get("g") or 0
    ret = 1.0
    if g >= 10 and spa / g < 3.55:
        ret = max(0.80, (spa / g) / 3.8)
        ret = 1.0 + (ret - 1.0) * min(1.0, g / 40.0)
    f = max(0.7, min(1.3, opp_hit_factor))
    # Statcast luck adjustment: power_mult scales extra-base rates toward xSLG,
    # contact_mult scales the overall hit rate toward xBA (true talent).
    pm = max(0.7, min(1.3, power_mult))
    cm = max(0.7, min(1.3, contact_mult))
    # Home runs are far more air-carry-sensitive than contact hits (a hot, thin-air
    # night lifts HR much more than singles), so HR gets an extra environment
    # multiplier on top of the hit-scale one already baked into `f`.
    he = max(0.85, min(1.25, hr_env))
    r_2b = (b.get("doubles") or 0) / spa * f * pm
    r_3b = (b.get("triples") or 0) / spa * f * pm
    r_hr = (b.get("hr") or 0) / spa * f * pm * he
    r_hit = (b.get("hits") or 0) / spa * f * cm
    r_1b = max(0.0, r_hit - r_2b - r_3b - r_hr)
    # Walks + HBP together: both put the batter on 1st the same way, and DK
    # scores them identically (+2), so HBP rides the walk outcome code.
    r_bb = ((b.get("bb") or 0) + (b.get("hbp") or 0)) / spa
    r_hit = min(0.95, r_1b + r_2b + r_3b + r_hr)

    # Exact hit-count distribution (binomial) -> 1+/2+/3+/4+ hits.
    q = 1 - r_hit
    p0 = q ** k
    p1 = k * r_hit * q ** (k - 1) if r_hit < 1 else 0
    p2 = (k * (k - 1) / 2) * r_hit ** 2 * q ** (k - 2) if (r_hit < 1 and k >= 2) else 0
    p3 = (k * (k - 1) * (k - 2) / 6) * r_hit ** 3 * q ** (k - 3) if (r_hit < 1 and k >= 3) else 0
    hit1 = 1 - p0
    hit2 = max(0.0, 1 - p0 - p1)
    hit3 = max(0.0, 1 - p0 - p1 - p2)
    hit4 = max(0.0, 1 - p0 - p1 - p2 - p3)
    # HR count (binomial) -> 1+/2+ HR (the multi-HR line is a real long-odds market).
    q_hr = 1 - min(0.6, r_hr)
    hr1 = 1 - q_hr ** k
    hr2 = max(0.0, 1 - q_hr ** k - k * min(0.6, r_hr) * q_hr ** (k - 1)) if k >= 2 else 0.0

    # Exact total-bases distribution by convolving the per-PA TB pmf k times.
    pmf = {0: 1 - (r_1b + r_2b + r_3b + r_hr), 1: r_1b, 2: r_2b, 3: r_3b, 4: r_hr}
    dist = {0: 1.0}
    for _ in range(k):
        nd = {}
        for tb, pr in dist.items():
            for add, qq in pmf.items():
                if qq > 0:
                    nd[tb + add] = nd.get(tb + add, 0.0) + pr * qq
        dist = nd
    tb2 = sum(p for tb, p in dist.items() if tb >= 2)
    tb3 = sum(p for tb, p in dist.items() if tb >= 3)
    tb4 = sum(p for tb, p in dist.items() if tb >= 4)
    tb5 = sum(p for tb, p in dist.items() if tb >= 5)
    tb6 = sum(p for tb, p in dist.items() if tb >= 6)
    tb7 = sum(p for tb, p in dist.items() if tb >= 7)
    spd, sbr = _speed_inputs(b, sprint)
    # Expected per-game counts (the number a simulation converges to) so combos
    # can show "avg sim X" under each leg.
    exp_hits = round(pa * r_hit, 2)
    exp_tb = round(pa * (r_1b + 2 * r_2b + 3 * r_3b + 4 * r_hr), 2)
    exp_hr = round(pa * min(0.6, r_hr), 2)
    return {"name": b.get("name"), "slot": slot,
            "exp_hits": exp_hits, "exp_tb": exp_tb, "exp_hr": exp_hr,
            "hit1": round(hit1 * 100, 1), "hit2": round(hit2 * 100, 1),
            "hit3": round(hit3 * 100, 1), "hit4": round(hit4 * 100, 1),
            "hr1": round(hr1 * 100, 1), "hr2": round(hr2 * 100, 1),
            "tb2": round(tb2 * 100, 1), "tb3": round(tb3 * 100, 1),
            "tb4": round(tb4 * 100, 1), "tb5": round(tb5 * 100, 1),
            "tb6": round(tb6 * 100, 1), "tb7": round(tb7 * 100, 1),
            "pa": pa, "r1": round(r_1b, 4), "r2": round(r_2b, 4),
            "r3": round(r_3b, 4), "rhr": round(r_hr, 4), "rbb": round(r_bb, 4),
            "spd": spd, "sbr": sbr, "ret": round(ret, 3)}


def pitcher_k_props(k9, exp_ip=5.3, est_pitches=None):
    """Strikeout props for a starter: P(K >= line) via Poisson(expected Ks).
    `exp_ip` is THIS pitcher's expected innings (walk-aware -- a wild arm burns
    his pitch budget faster and goes fewer innings), so a 6.3-IP workhorse and a
    3.5-IP opener get very different ladders. `est_pitches` rides along for display."""
    if not k9 or k9 <= 0:
        return None
    lam = k9 / 9.0 * exp_ip
    pmf = _poisson_pmf(lam, kmax=20)
    out = {}
    for line in (4, 5, 6, 7, 8, 9, 10, 11, 12):
        # Full ladder so high K lines (long odds) are available; string keys so
        # the dict has a single key type (mixing int + "expected" breaks Flask's
        # sort-keys JSON serialization).
        out[str(line)] = round(sum(pmf[k] for k in range(line, len(pmf))) * 100, 1)
    out["expected"] = round(lam, 1)
    out["exp_ip"] = round(exp_ip, 1)
    if est_pitches:
        out["est_pitches"] = int(est_pitches)
    return out


def _binom_hit_probs(pa, p):
    n = max(1, int(round(pa)))
    p = min(0.45, max(0.05, p))
    p0 = (1 - p) ** n
    p1 = n * p * (1 - p) ** (n - 1)
    return round((1 - p0) * 100, 1), round(max(0.0, 1 - p0 - p1) * 100, 1)


def hit_props(batters, opp_hit_factor, lg_hit_rate=LEAGUE_HIT_RATE, top=9):
    """Per-batter 1+/2+ hit odds (the whole posted lineup by default), plus a team
    total-hits over/under.

    batters: ordered list of dicts with 'name', 'hits', 'pa' (season), 'ab'.
    opp_hit_factor: >1 = opposing pitching allows more hits than average.
    """
    rows = []
    expected_team_hits = 0.0
    for i, b in enumerate(batters[:9]):
        pa_season = b.get("pa") or 0
        hits = b.get("hits") or 0
        raw_rate = hits / pa_season if pa_season > 0 else lg_hit_rate
        rel = pa_season / (pa_season + 50) if pa_season > 0 else 0.0
        rate = rel * raw_rate + (1 - rel) * lg_hit_rate
        rate *= opp_hit_factor
        exp_pa = PA_BY_SLOT[i] if i < 9 else 3.8
        p1, p2 = _binom_hit_probs(exp_pa, rate)
        exp_h = exp_pa * min(0.45, max(0.05, rate))
        expected_team_hits += exp_h
        rows.append({"name": b.get("name"), "slot": i + 1,
                     "hit1_pct": p1, "hit2_pct": p2, "exp_hits": round(exp_h, 2)})

    # Team total hits over/under from a Poisson on expected hits.
    hpmf = _poisson_pmf(expected_team_hits, kmax=25)
    line = round(expected_team_hits) + 0.5
    under = sum(hpmf[k] for k in range(len(hpmf)) if k < line)
    team_total = {"expected": round(expected_team_hits, 1), "line": line,
                  "over_pct": round((1 - under) * 100, 1),
                  "under_pct": round(under * 100, 1)}

    rows.sort(key=lambda r: r["hit1_pct"], reverse=True)
    return {"batters": rows[:top], "team_total_hits": team_total}
