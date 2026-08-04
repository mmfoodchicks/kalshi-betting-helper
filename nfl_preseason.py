"""Preseason player usage, measured rather than projected.

THE PROBLEM. Sleeper has no preseason projections. The endpoint answers -- all
355 quarterbacks come back -- but every projection field is null, so the input the
entire NFL player-prop and DFS layer is built on simply does not exist in August.

THE FIX. Preseason usage is not random, it is INVERTED: starters play a series
and sit, and the snaps go to the roster bubble. That inversion is measurable, so
it was measured rather than assumed. ESPN keeps every preseason box score, Sleeper
keeps regular-season stats, and `espn_id` joins them -- which means both sides of
the fit come from the SAME season and nothing has to be assumed about a player
whose role changed.

    2025 preseason usage per game, by that player's 2025 REGULAR-season role
    (QB = pass attempts; everyone else = carries + targets)

      pos   regular-season role        n    preseason usage/g
      QB    starter, 20+ att/g        12          8.8
      QB    backup, 1-19              12         11.9      <- backups throw MORE
      QB    no regular role           16         10.3
      RB    starter, 10+ touches/g     7          3.2
      RB    rotational, 4-9            7          4.4
      RB    fringe, 0.1-3             13          5.4
      RB    no regular role           13          6.2      <- twice the starter
      WR    rotational                13          2.2
      WR    fringe                    30          2.5
      WR    no regular role           29          2.5
      TE    fringe                    14          2.0
      TE    no regular role           10          1.7

WHAT THIS SUPPORTS AND WHAT IT DOES NOT. Running back is the clean one: perfectly
monotonic, and a camp body gets roughly double a starter's touches. Quarterback is
inverted the same way, the backup throwing about a third more than the starter.
WIDE RECEIVER AND TIGHT END ARE FLAT -- 2.2 against 2.5 is nothing, and a target
share that thin does not separate. So this projects RB and QB usage and declines
to pretend it knows anything about WR/TE beyond a positional average.

THE SAMPLE IS SMALL: 179 players joined across the whole 2025 preseason, 7 to 30
per cell. The direction is consistent and matches the mechanism, but these are
ratios with wide error bars, not calibrated constants like the baseball work. They
are used as MULTIPLIERS on a positional baseline rather than as absolute
projections, so an error scales a number rather than inventing one.
"""

import clock

# Preseason usage per game by position, for a player with no regular-season role
# -- the baseline the multipliers below scale. Straight from the table above.
_BASE = {"QB": 10.3, "RB": 6.2, "WR": 2.5, "TE": 1.7}

# Multiplier on that baseline by the player's regular-season workload. Measured;
# the ordering is the whole point, and it runs the opposite way to the regular
# season. WR/TE are held flat because the measurement says they are.
_ROLE = {
    "QB": ((20.0, 8.8 / 10.3), (1.0, 11.9 / 10.3), (0.0, 1.0)),
    "RB": ((10.0, 3.2 / 6.2), (4.0, 4.4 / 6.2), (0.1, 5.4 / 6.2), (0.0, 1.0)),
    "WR": ((0.0, 1.0),),
    "TE": ((0.0, 1.0),),
}

# A preseason game is not a game. Starters who do play take a series or two, and
# the fourth quarter belongs to players who will be cut, so exhibitions score less
# than September does. Scales the drive engine's expected points when it simulates
# one. MEASURED off every completed 2025 game, because the first number written
# here was a guess wearing the word "measured":
#
#     2025 preseason   49 games   mean total 41.02
#     2025 regular    272 games   mean total 46.03   ->  0.891
PRESEASON_SCORING = 0.891


def role_factor(pos, reg_per_game):
    """Preseason usage multiplier for a player with `reg_per_game` regular-season
    workload (pass attempts for a QB, carries + targets otherwise)."""
    table = _ROLE.get(pos)
    if not table:
        return 1.0
    for threshold, mult in table:
        if (reg_per_game or 0.0) >= threshold:
            return mult
    return 1.0


def expected_usage(pos, reg_per_game):
    """Projected preseason touches (or pass attempts) per game."""
    base = _BASE.get(pos)
    if base is None:
        return None
    return round(base * role_factor(pos, reg_per_game), 2)


def is_preseason(date=None):
    """True when `date` (or today) falls in the NFL preseason window -- from the
    Hall of Fame game through the last exhibition, roughly all of August."""
    d = date or clock.today_et()
    return d.month == 8 or (d.month == 7 and d.day >= 25)


# The workload that separates a starter from a rotational player, per position.
# Read off the same table the multipliers come from, so the label and the number
# can never disagree -- comparing the MULTIPLIER against a fixed cutoff got this
# wrong, because .85 means "starter" for a quarterback and "rotational" for a
# running back.
_STARTER_AT = {"QB": 20.0, "RB": 10.0, "WR": 10.0, "TE": 10.0}
_ROTATIONAL_AT = {"QB": 1.0, "RB": 4.0, "WR": 4.0, "TE": 4.0}


def usage_note(pos, reg_per_game):
    """Why this player's preseason number is what it is, in one line, so a lineup
    can be argued with rather than just trusted."""
    if pos in ("WR", "TE"):
        return "preseason target share is flat across roles — no edge from depth"
    r = reg_per_game or 0.0
    if r >= _STARTER_AT.get(pos, 10.0):
        return "regular-season starter — sits early in exhibitions"
    if r >= _ROTATIONAL_AT.get(pos, 4.0):
        return "rotational — moderate exhibition workload"
    if r > 0:
        return "fringe — heavy exhibition workload"
    return "no regular-season role — the snaps starters give up"
