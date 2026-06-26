"""Hierarchical tennis match simulator.

From two players' per-point serve/return rates (tennis_data) we derive each
player's probability of winning a point on serve in THIS matchup, then build the
match up the natural hierarchy: point -> game -> set -> match. Game-hold is the
exact analytic formula; sets and the match are Monte-Carlo'd game-by-game (with
proper tiebreaks and serve rotation) so every correlated market falls out of the
same simulation and stays mutually consistent:

  - match winner
  - total games (over/under)
  - total sets / straight-sets / set-sweep
  - individual set winners
  - aces (from each server's ace rate x their expected service points)

This is what lets the combo maker price a parlay like "Player A wins + over 22.5
games + match goes 3 sets" off one coherent model instead of three guesses.
"""

import random


def _hold(p):
    """Probability the server wins a game, given per-point serve-win prob p
    (deuce solved in closed form)."""
    p = min(0.999, max(0.001, p))
    q = 1 - p
    # win to love / 15 / 30, plus reaching deuce (3-3) then winning out
    pre = p**4 + 4 * p**4 * q + 10 * p**4 * q**2
    deuce = 20 * p**3 * q**3
    d_win = p * p / (p * p + q * q)
    return pre + deuce * d_win


def point_probs(ra, rb, lg):
    """(pa, pb): A's and B's per-point serve-win prob in the matchup. A's serve
    strength above tour average, minus B's return strength above tour average,
    anchored at the tour serve level."""
    spw, rpw = lg["spw"], lg["rpw"]
    pa = spw + (ra["spw"] - spw) - (rb["rpw"] - rpw)
    pb = spw + (rb["spw"] - spw) - (ra["rpw"] - rpw)
    return min(0.90, max(0.50, pa)), min(0.90, max(0.50, pb))


def _tiebreak(rng, pa, pb, a_first):
    """Simulate a 7-point tiebreak point-by-point with correct serve rotation
    (1 serve, then 2 each). Returns True if A wins."""
    a = b = 0
    pt = 0
    while True:
        server_a = a_first if ((pt + 1) // 2) % 2 == 0 else (not a_first)
        if rng.random() < (pa if server_a else (1 - pb)):
            a += 1
        else:
            b += 1
        if (a >= 7 or b >= 7) and abs(a - b) >= 2:
            return a > b
        pt += 1


def _set(rng, holdA, holdB, pa, pb, a_serving):
    """One set, game-by-game. Returns (a_won, gamesA, gamesB, a_serving_next,
    a_serve_games, b_serve_games)."""
    gA = gB = 0
    asg = bsg = 0
    while True:
        if a_serving:
            asg += 1
            a_won_game = rng.random() < holdA
        else:
            bsg += 1
            a_won_game = rng.random() >= holdB     # A breaks B's serve
        if a_won_game:
            gA += 1
        else:
            gB += 1
        a_serving = not a_serving
        if (gA >= 6 or gB >= 6) and abs(gA - gB) >= 2:
            return gA > gB, gA, gB, a_serving, asg, bsg
        if gA == 6 and gB == 6:
            a_first = a_serving
            if _tiebreak(rng, pa, pb, a_first):
                gA += 1
            else:
                gB += 1
            # the player who did NOT serve first in the breaker serves the next set
            return gA > gB, gA, gB, (not a_first), asg, bsg


def simulate(rates_a, rates_b, lg, best_of=3, n=12000, seed=None, fatigue=None):
    """Monte-Carlo the match. rates_* are {spw,rpw,ace,df}. `fatigue` is an optional
    (tired_a, tired_b) pair of recent-load scores: the more-fatigued player serves
    slightly worse (capped ~2.5% of a point), which compounds through the hold and
    deciding-set math the way tired legs actually cost matches. Returns a board of
    coherent market probabilities for both players."""
    rng = random.Random(seed)
    pa, pb = point_probs(rates_a, rates_b, lg)
    if fatigue:
        # differential only -- if both are equally tired it washes out
        shift = max(-0.025, min(0.025, (fatigue[0] - fatigue[1]) * 0.004))
        pa = min(0.90, max(0.50, pa - shift))
        pb = min(0.90, max(0.50, pb + shift))
    holdA, holdB = _hold(pa), _hold(pb)
    need = best_of // 2 + 1
    max_sets = best_of

    a_match = 0
    games_arr = []
    sets_count = {}
    set_win = [0] * max_sets        # P(A wins set k)
    set_played = [0] * max_sets
    a_straight = 0                  # A wins and drops no set
    a_sweep_possible = 0
    a_serve_games_tot = b_serve_games_tot = 0.0

    PPG = 6.3                       # avg points per service game (for ace expectation)
    for _ in range(n):
        sa = sb = 0
        tg = 0
        a_serving = (rng.random() < 0.5)
        k = 0
        a_dropped = False
        while sa < need and sb < need:
            a_won, gA, gB, a_serving, asg, bsg = _set(rng, holdA, holdB, pa, pb, a_serving)
            tg += gA + gB
            a_serve_games_tot += asg
            b_serve_games_tot += bsg
            if k < max_sets:
                set_played[k] += 1
                if a_won:
                    set_win[k] += 1
            if a_won:
                sa += 1
            else:
                sb += 1
                a_dropped = True
            k += 1
        a_won_match = sa >= need
        if a_won_match:
            a_match += 1
            if not a_dropped:
                a_straight += 1
        games_arr.append(tg)
        ts = sa + sb
        sets_count[ts] = sets_count.get(ts, 0) + 1

    games_arr.sort()
    inv = 1.0 / n

    def p_over(line):                # P(total games > line); line is x.5
        import bisect
        return round(100.0 * (n - bisect.bisect_right(games_arr, line)) / n, 1)

    mean_games = sum(games_arr) / n
    # ace expectation: each player's service games x points/game x ace rate
    aces_a = (a_serve_games_tot / n) * PPG * rates_a["ace"]
    aces_b = (b_serve_games_tot / n) * PPG * rates_b["ace"]

    # build a games over/under ladder around the mean (half-point lines)
    lo = int(mean_games) - 6
    games_ladder = {str(x + 0.5): p_over(x + 0.5) for x in range(max(12, lo), lo + 13)}

    setk = {}
    for k in range(max_sets):
        if set_played[k]:
            setk[f"set{k+1}_a"] = round(100.0 * set_win[k] / set_played[k], 1)
            setk[f"set{k+1}_played"] = round(100.0 * set_played[k] * inv, 1)

    return {
        "p_a": round(100.0 * a_match * inv, 1),
        "p_b": round(100.0 * (n - a_match) * inv, 1),
        "pa_serve": round(pa, 3), "pb_serve": round(pb, 3),
        "holdA": round(100 * holdA, 1), "holdB": round(100 * holdB, 1),
        "mean_games": round(mean_games, 1),
        "games_ladder": games_ladder,
        "p_over": p_over,                       # callable for arbitrary lines
        "total_sets": {str(k): round(100.0 * v * inv, 1) for k, v in sorted(sets_count.items())},
        "a_straight": round(100.0 * a_straight * inv, 1),    # A wins in straight sets
        "best_of": best_of,
        **setk,
        "aces_a": round(aces_a, 1), "aces_b": round(aces_b, 1),
        "aces_total": round(aces_a + aces_b, 1),
    }


def quick_winprob(rates_a, rates_b, lg, best_of=3):
    """Fast analytic-ish match win prob (small Monte Carlo) for ranking many
    matchups cheaply."""
    return simulate(rates_a, rates_b, lg, best_of=best_of, n=2500)["p_a"]
