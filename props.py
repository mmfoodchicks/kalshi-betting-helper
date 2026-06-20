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


def _poisson_pmf(lam, kmax=_MAXR):
    pmf = []
    p = math.exp(-lam)
    for k in range(kmax + 1):
        pmf.append(p)
        p = p * lam / (k + 1)
    return pmf


def game_props(er_home, er_away, home_abbr, away_abbr):
    """Run line + game total over/unders from independent-Poisson run dists."""
    ph = _poisson_pmf(er_home)
    pa = _poisson_pmf(er_away)

    p_home_win = p_away_win = p_tie = 0.0
    p_home_by2 = p_away_by2 = 0.0
    total_pmf = [0.0] * (2 * _MAXR + 1)
    for i, phi in enumerate(ph):
        for j, paj in enumerate(pa):
            pr = phi * paj
            total_pmf[i + j] += pr
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

    # Fuller ladder so the combo maker can tune the line up/down for confidence.
    base = round(model_total)
    ladder = []
    for ln in [base - 4.5 + i for i in range(9)]:
        if ln < 0.5:
            continue
        over, under = over_under(ln)
        ladder.append({"line": ln, "over_pct": over, "under_pct": under})

    fav_is_home = er_home >= er_away
    return {
        "run_line": {
            "favorite": home_abbr if fav_is_home else away_abbr,
            "fav_by2_pct": round((p_home_by2 if fav_is_home else p_away_by2) * 100, 1),
            "underdog": away_abbr if fav_is_home else home_abbr,
            "dog_plus15_pct": round((1 - (p_home_by2 if fav_is_home else p_away_by2)) * 100, 1),
        },
        "model_total": round(model_total, 1),
        "totals": totals,
        "totals_ladder": ladder,
        "win_pct": {"home": round(p_home_win * 100, 1), "away": round(p_away_win * 100, 1)},
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


def _binom_hit_probs(pa, p):
    n = max(1, int(round(pa)))
    p = min(0.45, max(0.05, p))
    p0 = (1 - p) ** n
    p1 = n * p * (1 - p) ** (n - 1)
    return round((1 - p0) * 100, 1), round(max(0.0, 1 - p0 - p1) * 100, 1)


def hit_props(batters, opp_hit_factor, lg_hit_rate=LEAGUE_HIT_RATE, top=5):
    """Per-batter 1+/2+ hit odds, plus a team total-hits over/under.

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
        expected_team_hits += exp_pa * min(0.45, max(0.05, rate))
        rows.append({"name": b.get("name"), "slot": i + 1,
                     "hit1_pct": p1, "hit2_pct": p2})

    # Team total hits over/under from a Poisson on expected hits.
    hpmf = _poisson_pmf(expected_team_hits, kmax=25)
    line = round(expected_team_hits) + 0.5
    under = sum(hpmf[k] for k in range(len(hpmf)) if k < line)
    team_total = {"expected": round(expected_team_hits, 1), "line": line,
                  "over_pct": round((1 - under) * 100, 1),
                  "under_pct": round(under * 100, 1)}

    rows.sort(key=lambda r: r["hit1_pct"], reverse=True)
    return {"batters": rows[:top], "team_total_hits": team_total}
