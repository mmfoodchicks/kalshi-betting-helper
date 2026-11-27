"""The research-only COACHING variant of the DFS worlds: the same
nfl_dfs_engine (anchor, Sleeper usage deal, allocation noise, defenses and
kickers), with every game played by nfl_coach_sim instead of nfl_deep_sim.

nfl_dfs_engine.py is NOT edited and has no hook: it calls
nfl_deep_sim.play_game by name, and `coach_games()` swaps that one function
for the length of a `with` block, in the calling process only. Outside the
block (and in every process that never enters one) production is untouched; a
guard holds nfl_dfs_engine.py's sha256 and plays the same worlds before and
after a block. No board, PC task or cache key ever enters the block.

What the swapped game is: nfl_coach_sim.play_game on the CALLER's base tables
(the tables the anchor measured on: nfl_dfs_engine passes them) with the
coaching engine's clock tables on top, at production's own form tilt and home
edge, so the two arms differ in the snap loop and nothing else. The anchor
re-levels each arm on its own engine, as it does for any table set.
"""
import contextlib
import copy

import nfl_coach_sim as C
import nfl_deep_sim as D

_WRAP = {}


def coach_tables(base):
    """nfl_coach_sim's tables with `base` (an nfl_deep_sim.Tables) as the
    shared part. Cached per base object."""
    key = id(base)
    hit = _WRAP.get(key)
    if hit is None or hit[0] is not base:
        ct = copy.copy(C.tables())
        ct.base = base
        ct.call_base = {k: D._logit(v) for k, v in base.call_marg.items()}
        ct.call_lg = D._logit(base.league["pass_rate"])
        _WRAP[key] = hit = (base, ct)
    return hit[1]


def coach_play_game(home, away, rng, T=None, neutral=False, playoff=False, form_sd=None, env_sd=None, log=None):
    """nfl_deep_sim.play_game's signature, played by the coaching engine."""
    return C.play_game(home, away, rng, coach_tables(T or D.tables()), neutral=neutral, playoff=playoff,
                       form_sd=D._FORM_SD if form_sd is None else form_sd, env_sd=env_sd, log=log,
                       hfa=D._HFA_YDS)


@contextlib.contextmanager
def coach_games():
    """Inside the block, nfl_deep_sim.play_game IS the coaching engine (this
    process only). Always restored."""
    orig = D.play_game
    D.play_game = coach_play_game
    try:
        yield
    finally:
        D.play_game = orig
