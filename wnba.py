"""WNBA — thin shim over the league-parameterized basketball core (basket.py).

The full pipeline (ESPN data, possession engine, Kalshi 3-series pricing, SGPs,
predlog/calibration) lives in basket.py so the WNBA and NBA share ONE engine.
This module keeps the original public surface working: wnba.board(),
wnba.ratings(), wnba.players(), wnba.schedule(), wnba.game_markets(), etc.
"""
import basket


def teams():
    return basket.teams("wnba")


def ratings():
    return basket.ratings("wnba")


def players():
    return basket.players("wnba")


def schedule(date=None):
    return basket.schedule("wnba", date)


def game_markets(home_ab, away_ab):
    return basket.game_markets("wnba", home_ab, away_ab)


def simulate_game(rh, ra, ph_list, pa_list, n=3000, seed=None):
    return basket.simulate_game("wnba", rh, ra, ph_list, pa_list, n=n, seed=seed)


def same_game_parlay(sim, n_legs=3, target=0.45):
    return basket.same_game_parlay(sim, n_legs=n_legs, target=target)


def board(date=None):
    return basket.board("wnba", date)


def _build_board(date, n=3000):
    return basket._build_board("wnba", date, n=n)
