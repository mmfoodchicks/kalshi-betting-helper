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

import math

import clock
import racing

# ---- What a preseason team-game actually looks like --------------------------
# Sleeper publishes no preseason PROJECTIONS (every field is null, which is what
# sent this file looking for a measurement in the first place) but it does
# publish preseason STATS, at season_type=pre. That closes the loop: the same
# feed the regular-season model runs on carries the exhibition ground truth.
#
# Measured over all 96 team-games of the 2025 preseason:
#
#     pass_att 32.0   pass_yd 206.7   pass_td 1.21   pass_int 0.80
#     rush_att 26.9   rush_yd 109.0   rush_td 0.83
#     rec_tgt  30.8   rec      20.1   rec_yd 206.0   rec_td 1.20
#     fum_lost 0.43                        offensive TDs 2.04, 59.2% of them passing
#
# 20.5 points a side, which is the same 41.02 total PRESEASON_SCORING was fitted
# on -- two independent slices of the same season agreeing is the check that this
# is one coherent picture rather than two numbers that happen to be nearby.
PRE_TEAM = {"pass_att": 32.02, "pass_yd": 206.68, "pass_td": 1.21,
            "pass_int": 0.80, "rush_att": 26.85, "rush_yd": 109.00,
            "rush_td": 0.83, "rec_tgt": 30.82, "rec": 20.06, "rec_yd": 205.97,
            "rec_td": 1.20, "fum_lost": 0.43, "points": 20.51}

# Field goals per touchdown. The engine's regular-season 0.55 is wrong for
# exhibitions by half: preseason drives stall in field-goal range far more often
# (2.04 TD against 1.67 FG a side), and inverting points at 0.55 hands the sim
# ~10% too many touchdowns -- which lands directly on every anytime-TD leg.
TD_FG = 0.82

# Per-attempt efficiency, same 96 team-games. Preseason is LESS efficient than
# September across the board, and the one number that goes the other way is the
# interception rate -- which is the backups-are-playing signature again:
#
#                       preseason   regular (wk 1-5)
#     pass yards/att       6.45         7.00
#     pass TD/att        0.0377       0.0478
#     INT/att            0.0250       0.0204   <- the only one that rises
#     RB yards/carry       3.96         4.30
#     WR yards/target      6.93         7.89
Y_ATT, PTD_ATT, INT_ATT = 6.45, 0.0377, 0.0250
RUSH_SHARE = {"QB": 0.131, "RB": 0.846, "WR": 0.023, "TE": 0.000}
TGT_SHARE = {"QB": 0.003, "RB": 0.160, "WR": 0.659, "TE": 0.179}
Y_CAR = {"QB": 4.41, "RB": 3.96, "WR": 5.90, "TE": 3.96}
RTD_CAR = {"QB": 0.0444, "RB": 0.0293, "WR": 0.0169, "TE": 0.0293}
Y_TGT = {"QB": 8.12, "RB": 5.34, "WR": 6.93, "TE": 6.93}
CATCH = {"QB": 1.000, "RB": 0.755, "WR": 0.606, "TE": 0.716}
RTD_TGT = {"QB": 0.0000, "RB": 0.0211, "WR": 0.0431, "TE": 0.0378}

# How many at each position actually record a snap in an exhibition, from the
# team budget divided by the measured per-player usage: 32 attempts at 12 a
# quarterback is under three of them, 20.3 WR targets at 2.5 apiece is eight.
_DEPTH = {"QB": 3, "RB": 5, "WR": 8, "TE": 3}

# --- the two units that never leave the field --------------------------------
# A kicker takes every extra point and field goal; a defense plays every
# defensive snap. Nobody else on a preseason roster does, and the scoring follows
# directly. DK points per game across three exhibition seasons (Sleeper,
# season_type=pre):
#
#     K     n=132  mean 5.83  sd 3.16      DST   n= 96  mean 7.07  sd 3.44
#     QB    n=216  mean 5.04                WR   n=574  mean 3.96
#     RB    n=347  mean 4.67                TE   n=262  mean 2.63
#
# The two positions a regular-season optimizer treats as salary relief are the
# top two scorers in August, by mean AND median, in every season measured. That
# is not a preference, it is what limited playing time does to everyone else.
#
# Stored as a decile ladder rather than a mean and SD because the shape matters:
# a kicker's floor is a real 0.5 (one extra point) and a defense's is NEGATIVE
# (points allowed with no takeaways), while both carry a long right tail off a
# return touchdown. A Normal fitted to the mean would invent a symmetric spread
# neither of them has.
_SPECIAL_Q = {
    "K":   (0.5, 2.0, 3.33, 4.0, 4.67, 5.5, 6.33, 7.0, 8.0, 10.33, 16.0),
    "DST": (-1.5, 2.67, 4.33, 5.33, 6.33, 7.0, 7.33, 9.0, 10.33, 12.0, 16.0),
}
SPECIAL_MEAN = {"K": 5.83, "DST": 7.07}


# How hard a defense's score tracks what the other offense did to it. Measured
# over 96 exhibition team-seasons (Sleeper pts_allow against DK defensive points):
#
#     allowed  0-14/g  n=16   10.90        r     = -0.694
#             14-20    n=40    7.83        slope = -0.427 per point allowed
#             20-24    n=28    5.40        fit at 30 allowed = 2.23
#             24-30    n= 9    4.33        fit at 18 allowed = 7.35
#             30+      n= 3    0.56
#
# Carolina allowed 30 and scored 1; Arizona allowed 33 and scored 2. The model
# had them at 7.1 and 7.3 -- the unconditional average of an 18.7-points-allowed
# game -- because the two defenses were drawn INDEPENDENTLY of the game they were
# playing in and of each other. That is what made rostering both look like
# diversification when it is the most concentrated bet on the board: in a
# shootout they lose together, which is exactly what happened.
DST_R = -0.694
DST_SD = 3.44


def dst_from_offense(opp_fp, n, rng, mean=None, sd=None, r=DST_R):
    """Defensive DK scores correlated with the opposing offense, iteration by
    iteration.

    `opp_fp` is that offense's per-iteration fantasy output from the game sim --
    a proxy for the points this defense conceded, and the only per-iteration
    handle on game script available. Standardised and mapped through a bivariate
    normal at the measured correlation, so the marginal mean and spread stay the
    measured ones while the ORDERING follows the game: heavy offensive iterations
    are the ones where this defense scores badly.

    The correlation is measured against literal points allowed, not against
    fantasy output, so using the proxy attenuates it -- the realised figure will
    be somewhat weaker than -0.694. Weaker and correctly signed beats zero."""
    mean = SPECIAL_MEAN["DST"] if mean is None else mean
    sd = DST_SD if sd is None else sd
    if not opp_fp:
        return None
    m = sum(opp_fp) / len(opp_fp)
    var = sum((x - m) ** 2 for x in opp_fp) / max(1, len(opp_fp))
    s = var ** 0.5
    resid = sd * (1.0 - r * r) ** 0.5
    out = []
    for i in range(n):
        z = ((opp_fp[i % len(opp_fp)] - m) / s) if s > 0 else 0.0
        out.append(round(mean + r * sd * z + rng.gauss(0.0, resid), 2))
    return out


def special_samples(pos, n, rng, scale=1.0):
    """n sampled DK scores for a kicker or a defense in an exhibition.

    Inverse-CDF off the measured decile ladder with uniform interpolation inside
    each decile, so the draw reproduces the real shape -- floor, median and tail
    -- instead of a Normal's idea of it. `scale` tilts it for a game the market
    prices high or low, since both units score off the same drives everyone else
    does."""
    q = _SPECIAL_Q.get((pos or "").upper())
    if not q:
        return None
    last = len(q) - 1
    out = []
    for _ in range(n):
        u = rng.random() * last
        i = int(u)
        if i >= last:
            i = last - 1
        lo, hi = q[i], q[i + 1]
        out.append(round((lo + (hi - lo) * (u - i)) * scale, 2))
    return out

_POS = ("QB", "RB", "WR", "TE")
_PROJ = "https://api.sleeper.com/projections/nfl/{season}/1"
_STATS = "https://api.sleeper.com/stats/nfl/{season}"

# Preseason usage per game by position, for a player with no regular-season role
# -- the baseline the multipliers below scale. Straight from the table above.
_BASE = {"QB": 10.3, "RB": 6.2, "WR": 2.5, "TE": 1.7}

# Multiplier on that baseline by the player's regular-season workload. Measured;
# the ordering is the whole point, and it runs the opposite way to the regular
# season.
#
# WR was wrong and TE was right, and it took two seasons of data to tell them
# apart. Both were a single flat (0.0, 1.0) entry under the note "held flat
# because the measurement says they are". Measured over Sleeper's exhibition feed
# joined to the PRIOR regular season on player_id, 2025->2024 and 2024->2023
# pooled, preseason touches per game by prior regular-season workload:
#
#     WR   n=676                        TE   n=358
#       >=4/g   n= 92   1.58              >=4/g   n= 29   1.13
#       0.1-4   n=204   2.45              <0.1    n=191   1.25
#       <0.1    n=380   2.28
#       Welch t = -5.01, r = -0.147       Welch t = -0.49, r = -0.039
#
# WR separates hard: an established receiver takes ~31% fewer exhibition targets
# than a camp body, at t = -5.01 over 676 players. Flat meant every receiver on a
# team got an identical projection -- on the Panthers-Cardinals showdown slate all
# 26 came out at 3.38 or 3.00, so Marvin Harrison Jr. and a camp body were the
# same bet and the optimizer had nothing to choose between them.
#
# TE does not separate. A single season suggested it did (0.65 against 1.12,
# ratio 0.58) and that was noise -- the tiers behind it were n=8 and n=8. Pooling
# a second season and widening the tier to >=4 puts n=29 against n=191 and the
# effect collapses to t = -0.49, r = -0.04. So TE stays flat, which is what the
# original note said, and the note was right for the position it was right about.
#
# Ratios are against the no-role tier, matching how the QB and RB rows are
# written. The 0.1-4 WR tier sits slightly ABOVE the no-role one, which is not
# noise the same way: a fringe player is on the roster bubble and plays the whole
# exhibition, while a complete unknown is as likely to be cut at half-time.
_ROLE = {
    "QB": ((20.0, 8.8 / 10.3), (1.0, 11.9 / 10.3), (0.0, 1.0)),
    "RB": ((10.0, 3.2 / 6.2), (4.0, 4.4 / 6.2), (0.1, 5.4 / 6.2), (0.0, 1.0)),
    "WR": ((4.0, 1.58 / 2.28), (0.1, 2.45 / 2.28), (0.0, 1.0)),
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

# The shape of a preseason RESULT. Needed because Kalshi opens the moneyline on
# a game long before it opens a spread ladder -- every game of preseason week 2
# was moneyline-only two days before week 1 kicked off -- so a win probability
# has to be convertible into points or those games cannot be simulated at all.
#
# First fitted on ONE preseason (49 games): margin SD 15.40, home edge +0.78.
# Three seasons (2023-25, n=147 preseason / 801 regular) say the single year
# was noise on both counts:
#
#     margin SD        13.70  against 14.37 in the regular season
#     home advantage   -0.19 +/- 1.1  against +2.13 in the regular season
#
# So preseason margins do NOT scatter wider in raw points -- they scatter wider
# RELATIVE to a much lower scoring level (38.6 total against 45.2) -- and the
# home edge really is nothing: nobody's crowd or travel matters when the
# players on the field in the fourth quarter will be cut on Tuesday.
MARGIN_SD = 13.70
HFA_PTS = 0.0


def margin_from_prob(p_home):
    """Expected home margin implied by a home win probability.

    Inverts P(margin > 0) = Phi(mu / sigma) at the measured preseason sigma. A
    62c home moneyline is a 4.2-point favourite: the wider the outcome
    distribution, the MORE points it takes to buy the same win probability,
    because a wider distribution means more of the underdog's mass sits on the
    winning side of any given spread."""
    p = min(0.97, max(0.03, float(p_home or 0.5)))
    return MARGIN_SD * _phi_inv(p)


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
        return "preseason target share is flat across roles - no edge from depth"
    r = reg_per_game or 0.0
    if r >= _STARTER_AT.get(pos, 10.0):
        return "regular-season starter - sits early in exhibitions"
    if r >= _ROTATIONAL_AT.get(pos, 4.0):
        return "rotational - moderate exhibition workload"
    if r > 0:
        return "fringe - heavy exhibition workload"
    return "no regular-season role - the snaps starters give up"


# ---- Roster -> preseason stat lines ------------------------------------------
def rosters(season):
    """{abbr: [{name, pos, reg_per_game, exp}]} for every team, in Sleeper's own
    depth order.

    The roster comes off the PRESEASON PROJECTIONS endpoint -- the one with no
    projections in it. Every row is null, but each row still carries the player's
    CURRENT team and position, which is the one thing that has to be right in
    August and that last season's stat feed cannot tell you: a player who changed
    teams is filed under the old one there. So the useless half of the feed
    supplies the roster and the season stat feed supplies the role, joined on
    player_id.

    Each row also carries Sleeper's `search_rank`, which is what says whether a
    name is a real NFL player or a camp body -- the rows come back in NO
    meaningful order (with every projection null there is nothing to order them
    by), so the ranking has to be fetched rather than assumed."""
    def build():
        q = "&".join(f"position[]={p}" for p in _POS)
        try:
            ros = racing._get_json(
                f"{_PROJ.format(season=season)}?season_type=pre&{q}&order_by=pts_ppr",
                timeout=60) or []
            st = racing._get_json(
                f"{_STATS.format(season=int(season) - 1)}?season_type=regular&{q}"
                "&order_by=pts_ppr", timeout=60) or []
        except Exception:
            return None
        try:
            import nfl_adp
            rank = nfl_adp.consensus() or {}
            norm = nfl_adp._norm
        except Exception:
            rank, norm = {}, (lambda s: s)
        prev = {r.get("player_id"): (r.get("stats") or {}) for r in st}
        out = {}
        for r in ros:
            ab = r.get("team")
            p = r.get("player") or {}
            pos = p.get("position")
            if not ab or pos not in _POS:
                continue                       # free agents carry no team
            s = prev.get(r.get("player_id")) or {}
            gp = s.get("gp") or 0
            if pos == "QB":
                work = (s.get("pass_att") or 0.0) / gp if gp else 0.0
            else:
                work = ((s.get("rush_att") or 0.0)
                        + (s.get("rec_tgt") or 0.0)) / gp if gp else 0.0
            nm = (p.get("first_name", "") + " " + p.get("last_name", "")).strip()
            sr = (rank.get(norm(nm)) or {}).get("rank")
            out.setdefault(ab, []).append(
                {"name": nm, "pos": pos, "reg_per_game": round(work, 2),
                 "rank": sr if isinstance(sr, (int, float)) else None,
                 "exp": p.get("years_exp")})
        return out or None
    return racing._cached(("nfl_pre_roster", str(season)), 6 * 3600, build)


_UNRANKED = 10 ** 9


def _keep(players, force=()):
    """The players who will actually take a snap, in position groups.

    Two different questions, answered by two different sources, which is the
    whole reason this is not one sort. WHO dresses is Sleeper's search_rank --
    the signal for "is this a real NFL player" -- broken by last season's
    workload inside Sleeper's flat 999 tier, where most of a roster sits. HOW
    MUCH each plays is the usage model, which runs the OTHER WAY. Ranking by
    projected preseason usage alone would keep the eight most anonymous
    receivers on the roster and cut the starter.

    `force` (normalized names) can never be cut. Kalshi booking a market on a
    player is the strongest possible evidence he is playing -- stronger than any
    ranking -- and the four players priced for the Panthers game are exactly the
    backups and rookies a rank-ordered cut is most likely to drop."""
    force = set(force or ())
    groups = {}
    for p in sorted(players, key=lambda x: ((x.get("rank") or _UNRANKED),
                                            -(x.get("reg_per_game") or 0.0))):
        g = groups.setdefault(p["pos"], [])
        if len(g) < _DEPTH.get(p["pos"], 4) or _key(p["name"]) in force:
            g.append(p)
    return groups


def _key(name):
    try:
        import nfl_adp
        return nfl_adp._norm(name)
    except Exception:
        return (name or "").lower()


def stat_lines(players, scale=1.0, force=()):
    """[{name, pos, pass_yd, rush_yd, rec, rec_yd, pass_td, rush_td, rec_td}] --
    the per-player expected line the game engine deals its simulated team totals
    out by, summing to the measured team budget.

    `scale` multiplies the team budget, so a game the market prices at 35 points
    gets proportionally smaller lines than one priced at 48. Shares within a
    position come from the INVERTED usage model, so the third quarterback throws
    more than the first."""
    groups = _keep(players, force)
    rows = []
    for pos, ps in groups.items():
        w = [max(0.01, expected_usage(pos, p.get("reg_per_game")) or 0.01) for p in ps]
        tot = sum(w) or 1.0
        for p, wi in zip(ps, w):
            f = wi / tot                        # this player's share of his group
            r = {"name": p["name"], "pos": pos,
                 "pass_yd": 0.0, "pass_td": 0.0, "pass_int": 0.0,
                 "rush_yd": 0.0, "rush_td": 0.0,
                 "rec": 0.0, "rec_yd": 0.0, "rec_td": 0.0}
            if pos == "QB":
                att = PRE_TEAM["pass_att"] * f * scale
                r["pass_yd"] = att * Y_ATT
                r["pass_td"] = att * PTD_ATT
                r["pass_int"] = att * INT_ATT
            car = PRE_TEAM["rush_att"] * RUSH_SHARE[pos] * f * scale
            tgt = PRE_TEAM["rec_tgt"] * TGT_SHARE[pos] * f * scale
            r["rush_yd"] = car * Y_CAR[pos]
            r["rush_td"] = car * RTD_CAR[pos]
            r["rec"] = tgt * CATCH[pos]
            r["rec_yd"] = tgt * Y_TGT[pos]
            r["rec_td"] = tgt * RTD_TGT[pos]
            r["carries"] = round(car, 2)
            r["targets"] = round(tgt, 2)
            r["note"] = usage_note(pos, p.get("reg_per_game"))
            r["reg_per_game"] = p.get("reg_per_game")
            rows.append({k: (round(v, 3) if isinstance(v, float) else v)
                         for k, v in r.items()})
    rows.sort(key=lambda x: -(x["pass_yd"] + x["rush_yd"] + x["rec_yd"]))
    return rows


# ---- What the market says a player will do -----------------------------------
# Kalshi's own ladder is a better read on a specific preseason workload than any
# positional model can be, because it prices THIS player in THIS exhibition.
# The model still supplies the shape and every correlation; the market supplies
# the level -- the same division of labour the game total already runs on.
#
# Yardage is fitted LOGNORMAL, which makes the fit a straight line rather than an
# optimizer: if ln(X) is normal then ln(line) = mu + sigma*z at every rung, where
# z is the standard normal quantile of the miss probability. Regressing ln(line)
# on z across a player's rungs recovers mu and sigma in closed form, and the mean
# is exp(mu + sigma^2/2). Counting stats (touchdowns) are fitted Poisson instead.
_LN_SD = 0.62               # fallback shape when a player has ONE rung to fit

# How much a preseason player's line MOVES game to game. Measured within-player
# across the 2025 exhibitions (players with 3+ games, log scale), which is the
# only way to separate "these two receivers are different" from "this receiver is
# different week to week" -- and it is the second that a prop line has to price:
#
#     pos  stat       players   log-SD
#     QB   pass_yd       53      0.635
#     RB   rush_yd       68      0.784
#     WR   rec_yd        71      0.725
#     TE   rec_yd        22      0.666
#                       ----     -----
#     pooled           250       0.722
#
# The estimator matters here and the first pass got it wrong: with three or four
# games a player, the POPULATION standard deviation is biased low by about 15%,
# and reading 0.58 off it would have left the model a sixth too confident on every
# prop. These are sample SDs.
#
# The cross-check is that Kalshi's own ladders imply the same shape -- fitting a
# lognormal through Carson Beck's four rungs gives sigma 0.58 and Kenny Pickett's
# two give 0.76. A measurement and a market arriving at the same dispersion from
# completely different directions is the reason to believe it.
#
# The regular-season engine runs at roughly half this, because a September role is
# stable and an August one is a coaching decision made at halftime -- and running
# the tight number against these ladders had the model calling Carson Beck 90% to
# clear 74.5 yards where the market said 57%.
_TARGET_LOGSD = 0.722
# What the shock has to be set to for the ENGINE to realize that, since a player's
# line already inherits dispersion from team volume, script and drive count:
#
#     shock   0.45   0.53   0.58   0.69
#     got     0.50   0.57   0.62   0.72
PLAYER_LOGSD = 0.69


def _phi_inv(p):
    """Standard normal quantile (Acklam's rational approximation, ~1e-9)."""
    if p <= 0.0:
        return -8.0
    if p >= 1.0:
        return 8.0
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00)
    lo, hi = 0.02425, 1 - 0.02425
    if p < lo:
        q = (-2.0 * math.log(p)) ** 0.5
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > hi:
        q = (-2.0 * math.log(1 - p)) ** 0.5
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5]) * q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def implied_mean(stat, rungs):
    """Expected value a Kalshi prop ladder implies, or None.

    `rungs` = [(line, p_over), ...] as kalshi_nfl.prop_ladders returns them."""
    pts = [(float(x), min(0.98, max(0.02, float(p)))) for x, p in (rungs or [])
           if x is not None and p is not None and float(x) > 0]
    if not pts:
        return None
    if stat == "td" or stat == "rec":
        # Counting stat: fit the Poisson rate that best matches every rung's
        # exceedance. A touchdown ladder is two points at most, so a one-parameter
        # family is all the data can support.
        best, err = None, None
        lam = 0.02
        while lam <= 4.0:
            e = 0.0
            for x, p in pts:
                k = int(math.ceil(x))            # "1+ TD" is floor_strike 0.5
                surv, term, acc = 1.0, math.exp(-lam), 0.0
                for i in range(k):
                    acc += term
                    term *= lam / (i + 1)
                surv = max(1e-9, 1.0 - acc)
                e += (surv - p) ** 2
            if err is None or e < err:
                best, err = lam, e
            lam += 0.02
        return round(best, 3)
    xs = [(math.log(x), _phi_inv(1.0 - p)) for x, p in pts]
    if len(xs) == 1:
        mu, sd = xs[0][0] - _LN_SD * xs[0][1], _LN_SD
    else:
        mz = sum(z for _, z in xs) / len(xs)
        ml = sum(l for l, _ in xs) / len(xs)
        szz = sum((z - mz) ** 2 for _, z in xs)
        if szz <= 1e-9:
            return None                          # every rung at the same quantile
        sd = sum((z - mz) * (l - ml) for l, z in xs) / szz
        sd = max(0.15, min(1.6, sd))
        mu = ml - sd * mz
    return round(math.exp(mu + sd * sd / 2.0), 2)


def team_exp(scale=1.0):
    """Team expectation for the engine, from the same measured budget the player
    lines are cut from -- so the players always sum to the team."""
    return {"pass_yd": PRE_TEAM["pass_yd"] * scale,
            "rush_yd": PRE_TEAM["rush_yd"] * scale,
            "rec_yd": PRE_TEAM["rec_yd"] * scale,
            "rec": PRE_TEAM["rec"] * scale,
            "pass_td": PRE_TEAM["pass_td"] * scale,
            "rush_td": PRE_TEAM["rush_td"] * scale,
            "pass_int": PRE_TEAM["pass_int"] * scale,
            "fum_lost": PRE_TEAM["fum_lost"] * scale}
