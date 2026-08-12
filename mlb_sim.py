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

import calibrate as _calibrate
import props as _props

# Candidate type -> the predlog bucket its graded forecasts are filed under
# (mirrors baseball._PREDLOG_TYPES for the batter markets). This is what lets a
# market graduate from the pooled prop temperature to its own measured one.
_PREDLOG_BUCKET = {"Hit": "mlb_hit", "Bases": "mlb_bases", "HR": "mlb_hr",
                   "HRR": "mlb_hrr", "SB": "mlb_sb", "RBI": "mlb_rbi"}

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
    """Parse the lineup into [(name, [r1,r2,r3,rhr,rbb], spd, sbr, ret, mix)].

    `mix` is (strikeout, ground, air) share of this hitter's outs. Defaults to
    the league split so a bare game dict, a test fixture or an older cached
    slate still simulates."""
    rows = []
    for b in batters or []:
        mix = (b.get("ok"), b.get("og"), b.get("of"))
        if not all(isinstance(v, (int, float)) and v >= 0 for v in mix) \
                or sum(mix) <= 0:
            mix = _LG_OUT_MIX
        else:
            t = float(sum(mix))
            mix = tuple(v / t for v in mix)
        rows.append([b.get("name"),
                     [max(0.0, b.get(k) or 0.0) for k in ("r1", "r2", "r3", "rhr", "rbb")],
                     b.get("spd") or 1.0, b.get("sbr") or 0.0, b.get("ret") or 1.0,
                     mix])
    return rows


# `sbr` is a runner's season steals per time-on-first, but the engine only rolls
# an attempt when the gate is open (next base free, < 2 outs) -- so raw rates
# under-produce steals. This measured factor recenters simulated SB/game on each
# runner's real rate (see the engine calibration test).
_SBR_ADJ = 1.85


# Expected PA for the SLOT (starter + whoever inherits it) -- the right
# number for sizing pinch-hit losses, measured over the same 190 games as
# props.PA_BY_SLOT (which carries the STARTER's own, lower, figures).
_PA_SLOT = (4.63, 4.51, 4.40, 4.30, 4.19, 4.09, 3.97, 3.85, 3.73)

# TIMES THROUGH THE ORDER. A starter gets worse each time he faces a lineup --
# hitters have seen his stuff, his sequencing and his velocity that day. It is one
# of the best-established effects in baseball and this engine did not have it
# (deep_sim did), so every prop, total and combo leg was priced off a pitcher who
# never tired within a game.
#
# The size is FITTED so the engine reproduces the real per-inning curve, rather
# than copied from that curve directly. Part of the observed shape is lineup
# position -- innings 1 and 2 face the top and bottom of the order -- and this
# engine already models lineup position correctly. Reading the raw curve into a
# pitcher term would double-count it, the same mistake as the one-sided home
# multiplier. So the fit targets the runs-per-half-inning ratio between innings
# 1-3 and 4-6, with the step swept under COMMON RANDOM NUMBERS (every step value
# replays the identical draws, so the differences are signal, not sampling noise
# -- an earlier independent-sample sweep was non-monotonic and useless):
#
#     step     sim t2/t1    level err
#     1.0000     0.9976       -0.83%     <- lineup position alone
#     1.0150     1.0359       -1.27%
#     1.0300     1.0775       -0.86%     <- real MLB is 1.0794
#     1.0450     1.1226       -1.14%
#
# The step is small because runs respond superlinearly to baserunners (more men on
# -> more PAs -> compounding; _team measures the local exponent near 2.2), and
# because innings 1-3 and 4-6 each straddle roughly 1.2 turns rather than one.
#
# REFITTED once the bullpen handoff below existed, because the two interact: the
# pen now enters partway through innings 5-6, which cuts how much penalty those
# frames carry. Same sweep with the handoff active and _PEN_MULT at 1.00:
#
#     step    t2/t1     inn78      <- targets 1.0794 and 0.9754
#     1.031   1.0482    0.9747
#     1.040   1.0745    0.9603
#     1.048   1.0806    0.9559
#
# The two targets pull against each other through the normalisation -- innings 7-8
# are measured relative to the game mean, so making 4-6 hotter deflates them.
# 1.040 is the best joint fit. t2/t1 is weighted the more heavily of the two: by
# the 7th, pinch hitters and defensive substitutions have changed the lineups, so
# that window is a noisier read on pitching than innings 1-6 are.
#
# Innings 7-9 are excluded from the fit: a leading home team never bats in the
# 9th, so those half-innings are a biased sample of game states rather than a
# clean read on the pitcher.
#
# Applied to on-base rates, compounding per turn and HELD FLAT from the third turn
# on. By then the starter is usually gone, and this engine has no pitcher identity
# on the offense side -- it cannot tell a fresh reliever from a tiring starter, so
# claiming a fourth-turn penalty would be inventing detail it does not have.
#
# The level takes care of itself, and the sweep confirms it: _team() calibrates the
# rate multiplier until simulated runs hit `er`, so the shape change is absorbed
# there and the team total stays within ~1% of where the run model put it at every
# step tried. This moves WHEN runs score, not HOW MANY.
#
# VERIFIED at the shipped step (16 real lineups x 1500 games, CRN):
#
#     turn2/turn1   sim 1.0841   real 1.0794      <- the fitted target
#     level         -1.17% vs er                  <- inside _team's tolerance
#     inn 1  sim/avg 0.997 vs real 0.987
#     inn 2          0.879        0.891
#     inn 6          1.075        1.058
#
# WHAT IS STILL MISSING, stated plainly: innings 3-5 come out low (sim 0.954 /
# 0.982 / 1.011 against real 1.090 / 1.062 / 1.084) and 7-9 high. Real scoring
# rises through the starter's second pass and then FALLS once the bullpen takes
# over; this curve rises and holds, because the penalty compounds to a plateau and
# nothing here knows when the starter was pulled. Modelling the handoff needs
# pitcher identity on the offense side, which this engine does not have -- the
# staff is simulated separately, only for the strikeout line.
#
# The real innings 7-9 are also a poor target to fit even if it could: a leading
# home team never bats in the 9th, so those half-innings are a filtered sample of
# game states, and the 9th's 0.877 is substantially that artifact rather than the
# bullpen. Innings 1-6 carry the clean signal and the fit uses only them.
_TTO_STEP = 1.040
_TTO_MAX_TURN = 2                    # 0-indexed: 1st, 2nd, 3rd-and-later

# THE BULLPEN HANDOFF. The penalty above belongs to the STARTER, and it must stop
# when he does -- a reliever entering in the 7th has faced nobody. Without this the
# curve rose and plateaued, leaving innings 7-9 too hot (sim 1.028/1.034/1.041
# against real 0.967/0.983/0.877).
#
# _PEN_MULT is the on-base multiplier once the pen is in, relative to the hitter's
# own baseline. Fitted against innings 7-8 -- NOT the 9th, which is a filtered
# sample of game states (a leading home team never bats) rather than a read on
# relief pitching:
#
#     pen    inn7/avg  inn8/avg   mean78     <- real target 0.9754
#     1.00     0.970     0.979    0.9747
#     0.97     0.924     0.939    0.9312
#     0.94     0.887     0.889    0.8881
#
# 1.00 lands it, and that is the interesting result: THE BULLPEN RESETS THE HITTER
# TO HIS BASELINE, no better and no worse. The late-inning dip in real baseball is
# not relievers being superior -- it is the starter's accumulated penalty simply
# ending. A fresh reliever facing a lineup for the first time is, to this engine,
# the same thing as a fresh starter facing it for the first time, which is exactly
# what the baseline ladder is.
#
# The exit inning is SAMPLED per game, not fixed at the mean. A hard threshold at
# 5.4 innings would put a cliff in the middle of every game's 6th and leave a
# visible kink in the run curve; real starters exit across a spread, and averaging
# over that spread is what smooths it.
_PEN_MULT = 1.00
_PEN_EXIT_SD = 1.15                  # innings of spread around a starter's expected exit


def _tto_mult(turn):
    """On-base multiplier for the `turn`-th time through the order (0-based)."""
    return _TTO_STEP ** min(max(0, turn), _TTO_MAX_TURN)


def _sample_exit(exp_ip, rnd):
    """Inning (0-based) at which this game's starter hands off.

    Sampled around his own expected innings so the handoff is spread across the
    slate instead of every game turning over in the same frame. Clamped to a
    plausible band: nobody is pulled before the 2nd, and nobody in this engine
    goes past the 8th."""
    if not exp_ip or exp_ip <= 0:
        return 99                                    # unknown -> never hand off
    # Triangular-ish spread from two uniforms: cheap, bounded, no math import.
    jitter = (rnd() + rnd() - 1.0) * _PEN_EXIT_SD * 1.5
    return max(2, min(8, int(round(exp_ip + jitter))))


def _build_setup(rows, mult):
    """Cumulative outcome thresholds with on-base rates scaled by `mult`,
    carrying each batter's speed factor, steal rate, and late-sub probability
    (`psub`: chance per late-inning PA of being lifted for a pinch hitter, sized
    so his expected lost PAs match his measured substitution retention).

    `thresh_tto[t]` is the same ladder rebuilt for the t-th time through the
    order; `thresh` stays as the first-turn ladder so any caller that has not been
    taught about TTO keeps its old behaviour instead of breaking."""
    setup = []
    for i, (name, rates, spd, sbr, ret, mix) in enumerate(rows):
        by_turn = []
        # One extra ladder on the end: index _TTO_MAX_TURN+1 is the BULLPEN, used
        # once the starter hands off, so a reliever is not charged the tiring
        # starter's accumulated penalty.
        for t in list(range(_TTO_MAX_TURN + 1)) + ["pen"]:
            f = _PEN_MULT if t == "pen" else _tto_mult(t)
            sr = [x * mult * f for x in rates]
            tot = sum(sr)
            if tot > 0.95:
                sr = [x * 0.95 / tot for x in sr]
            thresh, acc = [], 0.0
            for code, p in zip((1, 2, 3, 4, 5), sr):
                if p > 0:
                    acc += p
                    thresh.append((acc, code))
            by_turn.append(thresh)
        slot_pa = _PA_SLOT[i] if i < 9 else 3.8
        lost = slot_pa * (1.0 - min(1.0, ret))
        setup.append({"name": name, "thresh": by_turn[0], "thresh_tto": by_turn,
                      # Slugging-shaped danger, for the intentional-walk call:
                      # who a manager refuses to pitch to is about extra-base
                      # threat, not on-base skill.
                      "dang": rates[0] + 2 * rates[1] + 3 * rates[2] + 4 * rates[3],
                      "spd": spd, "sbr": min(0.6, sbr * _SBR_ADJ),
                      # Cumulative so one draw picks the out type: k, then
                      # ground, then air by remainder.
                      "okg": (mix[0], mix[0] + mix[1]),
                      "psub": min(0.45, lost / 1.75) if lost > 0 else 0.0})
    return setup


# How far the calibration is allowed to move a lineup's rates. This is the LAST
# line of defence, not the first: it exists to stop a bad input from producing an
# absurd game, and it should essentially never bind on real data.
#
# It used to be [0.7, 1.5] and it DID bind, silently. One lineup in sixteen came
# out of calibration 39% above the runs the model asked for, because a call-up
# with 4.6 plate appearances carried a .9193 home-run rate and the floor of 0.7
# could not pull nine hitters like that back down to 3.69 runs. The clamp was not
# the bug -- the unregressed rate was, and props._reg now fixes it at source --
# but a guard that fails by quietly returning the wrong answer is the wrong
# guard. Widened so a genuinely extreme MATCHUP still calibrates, and the engine
# now says so when it cannot.
# HOW FAR TO TRUST er OVER THE RATES. `mult` is the multiplier that would land a
# lineup exactly on its er; this is the power it is raised to, so 0 keeps the
# rates untouched and 1 forces the total onto er. It used to be 1, implicitly.
#
# THE RATES ALREADY CARRY THE MATCHUP. props.batter_props multiplies every
# component by opp_hit_factor before the simulator sees a thing, so er and the
# rates are two estimates of the same quantity, not a target and a raw input.
# Forcing one onto the other throws away half the information, and it always
# comes out of the HITS, because er pins the runs and nothing pins the hits.
#
# Swept over 16 real lineups, 112,000 team-games a setting, against the real
# league and against the model's OWN expected hits:
#
#     power   hits/g  runs/g     H/R   rho(er)  slope   vs real
#      0.00    8.348   4.510  1.8511    0.764   1.068   H +1.08%  R +1.42%
#      0.25    8.281   4.462  1.8558    0.848   1.020   H +0.27%  R +0.35%
#      0.35    8.266   4.438  1.8627    0.867   1.014   H +0.09%  R -0.21%
#      0.50    8.232   4.413  1.8652    0.911   1.005   H -0.33%  R -0.75%
#      1.00    8.120   4.323  1.8785    0.941   0.908   H -1.68%  R -2.80%
#     real     8.259   4.447  1.8572        -   1.000
#
# Full calibration was the worst setting on every level number, and the reason is
# in the SLOPE column: at power 1 a lineup's realized runs move only 0.908 per
# unit of er. The step meant to impose the matchup was COMPRESSING it, because
# the convergence loop systematically undershoots the high-scoring lineups. At
# 0.35 the slope is 1.014 -- the matchup comes through at full strength.
#
# There is a real trade-off and it is worth naming: rank correlation with er
# falls from .941 to .867, so some matchup ORDERING is given up for a level that
# is right. 0.25 through 0.50 is a plateau on the level numbers; 0.35 is chosen
# inside it as the setting that keeps the most er ordering.
#
# Shipped, with _CAL_HOME/_CAL_AWAY refit underneath it (they ride inside the
# target, so the shrink diluted them too):
#
#                       hits/g   runs/g      H/R    away/er   home/er
#     before             8.183    4.374   1.8709     1.000     0.999
#     after              8.210    4.399   1.8663     1.004     1.003
#     real               8.259    4.447   1.8572     1.000     1.000
#
# Every level number moves toward real and both sides still land on their own er.
# The gain is modest. The reason to take it is the slope: the engine no longer
# flattens the difference between a good matchup and a bad one.
_CAL_SHRINK = 0.35


_MULT_LO, _MULT_HI = 0.55, 1.8


def _clamp_mult(m):
    return max(_MULT_LO, min(_MULT_HI, m))


def calibration_error(setup, er, rnd, opp_sp_ip=None, n=900):
    """How far a calibrated lineup actually lands from its target, as a signed
    fraction. Exposed so callers and tests can catch a lineup the calibration
    could not reach instead of pricing a game off it."""
    if not setup or not er:
        return 0.0
    mean = sum(_play_game(setup, rnd, opp_sp_ip)[0] for _ in range(n)) / float(n)
    return mean / er - 1.0


def _team(batters, er, rnd, opp_sp_ip=None):
    """Lineup setup with on-base rates EMPIRICALLY calibrated so the simulated
    runs land near `er` (the matchup-adjusted model total). Returns [] if no
    lineup is posted.

    `opp_sp_ip` (the opposing starter's expected innings) is passed straight to
    the calibration games so the multiplier is fitted against the same TTO and
    bullpen shape the real simulation will use."""
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
        mean = sum(_play_game(setup, rnd, opp_sp_ip)[0] for _ in range(500)) / 500.0
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
        mult = _clamp_mult(mult * (er / mean) ** k_exp)
    # Final centering pass: acceptance still fires on a noisy estimate, so one
    # more measurement plus a single corrective nudge (never re-checked, so it
    # can't oscillate) centers the residual around ~1%.
    setup = _build_setup(rows, mult)
    mean = sum(_play_game(setup, rnd, opp_sp_ip)[0] for _ in range(700)) / 700.0
    if mean > 0.3:
        mult = _clamp_mult(mult * (er / mean) ** k_exp)
    # PARTIALLY. See _CAL_SHRINK: `mult` is the multiplier that would land the
    # lineup exactly on er, and going all the way there is measurably worse than
    # going most of the way back toward the rates it was handed.
    return _build_setup(rows, _clamp_mult(mult ** _CAL_SHRINK))


_N_INNINGS = 9


_DK_HIT = {1: 3, 2: 5, 3: 8, 4: 10}   # DraftKings hitter points by hit type


# Steals of 3rd run at this fraction of a runner's steal-of-2nd attempt rate.
# Measured at .119 of all completed steals over the same 300 games (53 of 445),
# so the standing 0.12 was right and stays.
_SB3_FRAC = 0.12

# STEAL SUCCESS. Measured over those 300 games (600 team-games):
#
#     stealing 2nd    391 SB / 89 CS   .8146   attempts .800 per team-game
#     stealing 3rd     53 SB / 24 CS   .6883   attempts .128
#
# The engine had 0.62 and 0.72 -- both wrong, and in opposite directions. It was
# throwing out 38% of runners going to second against a real 19%, which cost
# steals and, worse, manufactured outs that never happened. And it had stealing
# THIRD as the easier of the two when it is measurably the harder one: the throw
# is shorter and the runner's lead counts for less, so a play that gets attempted
# only in hand-picked spots still succeeds less often than the ordinary one.
#
# Fixing the success rate is also what fixes the steal LEVEL. The engine ran .547
# steals per team-game against a real .708; at the correct success rate the same
# attempt rate lands at about .72, so `_SBR_ADJ` needs no touching. Raising the
# attempt rate instead would have hit the total while leaving twice as many
# runners thrown out as really are.
_SB2_OK = 0.815
_SB3_OK = 0.688
_SB_SPD = 0.7                        # sprint-speed sensitivity (unfitted, kept)
# Intentional walks. The gate (late, 1st open, RISP, 1+ out, danger ratio) picks
# the situations; _IBB_P then fires often enough to land near the real ~0.3
# intentional passes per team-game on league-shaped lineups.
# The gate: he must out-threaten the man on deck (ratio) AND be a real power
# threat in absolute terms (floor -- league-average slugging proxy sits near
# 0.34, so 0.42 marks a genuine middle-order bat). A 1.35 ratio was
# unreachable: adjacent hitters in a real order are never 35% apart.
#
# CALIBRATED TO THE UNIVERSAL-DH ERA, not to memory. Intentional walks
# collapsed when the pitcher spot vanished: the modern league issues ~0.10
# per team-game, a third of the number the old rule-of-thumb carries. The
# classic spot (late, first open, runner in scoring position, slugger up
# over a weaker on-deck man) arises ~0.13 times a team-game in the sim, and
# managers who face it point to first most of the time -- so the honest
# shape is a HIGH fire rate on a RARE gate: 0.72 x 0.13 lands ~0.10.
_IBB_RATIO = 1.10
_IBB_DANG = 0.42
_IBB_P = 0.72
_IBB_N = [0]                         # counted for the calibration probe
# Structural calibration for the real-rules matchup engine (measured): a lineup
# calibrated to er over a solo 9-inning game realizes less as the HOME side
# (bottom-9 skip + walk-off truncation, minus the extras bump) and a touch more
# as the AWAY side (extras bump only).
#
# REFIT over 16 real lineups x 12,000 matchups a side, each calibrated to its own
# er with no correction applied, so the ratio measured IS the whole effect:
#
#     away realizes 1.0305 of its solo mean  -> 0.9704
#     home realizes 0.9796                   -> 1.0208
#
# The away figure had drifted: 0.962 was leaving every away side about 0.9% under
# the runs the model actually asked for. It was already off before baserunner
# advancement was touched (the same measurement against the previous engine says
# 0.9756), so this is a standing error being corrected rather than fallout. The
# home figure was already right and is left alone.
#
# One constant per side is an approximation either way -- how often the home team
# skips the ninth depends on the matchup, and across the 16 pairs the home ratio
# ranges 0.946-1.012 against an away range of 1.008-1.047. The mean is the right
# single number, and the home spread is the wider one precisely because the
# bottom-of-the-ninth skip is a function of who is winning.
# REFIT AGAIN once _CAL_SHRINK existed, because these ride INSIDE the target and
# the shrink dilutes them along with everything else. At shrink 0.35 the old
# 0.970 left the away side realizing 1.0370 of its er; home was already exact at
# 0.9987. These are now the factor applied to er to FORM THE TARGET, not the
# realized correction -- they have absorbed the dilution, which is why the away
# number looks so far from 1. What matters is the measurement below them.
_CAL_HOME = 1.024
_CAL_AWAY = 0.875


# --- BASERUNNER ADVANCEMENT ---------------------------------------------------
# THE BUG THIS REPLACED. `bases` is [1st, 2nd, 3rd] everywhere in this engine --
# the walk branch, the steal gates, the double-play branch, the live-game resume
# in state_from_snapshot. The hit branch unpacked it BACKWARDS:
#
#     r3, r2, r1 = bases          # r3 got the runner on FIRST
#
# so on every single the man on first scored and the man on third went back to
# second, and on every double the man on first scored while the man on third was
# rolled at 45%. Nothing caught it because _team calibrates the run LEVEL: with
# each hit worth far too much, the calibrator simply dialled every lineup's rates
# down until the total came back to `er`. Feeding the engine the league's own
# measured per-PA rates with NO calibration is what exposed it --
#
#     league-average rates, uncalibrated, real-rules games
#       before   5.197 runs/game    8.549 hits  ->  1.645 hits per run
#       after    4.493              8.645       ->  1.924
#       real     4.447              8.259       ->  1.857
#
# -- and it is the reason the engine looked like it needed "an eighth fewer hits
# than real baseball": the runs were being forced right, so the hits had to come
# out wrong.
#
# That flat construction is a smoke alarm, not a precision instrument -- nine
# identical hitters is not a team, and it draws a few more plate appearances a
# game than a real lineup does, which is most of the residual above. The precise
# measurement is over 16 real lineups through the production path, and there:
#
#              hits/game   runs/game   hits per run
#       before    7.577       4.500        1.684
#       after     8.316       4.483        1.855
#       real      8.259       4.447        1.857
#
# The no-hitter rate roughly halved with it, 1 in 831 -> 1 in 1,391 against a
# real 1 in 2,433. That real figure is 4 events in 9,732 team-games, so its own
# confidence interval runs from about 1 in 900 to 1 in 6,500: the engine now sits
# inside it instead of clearly outside, which is as much as four events can say.
# The shoulder is the sample worth trusting, and it agrees -- games of 2 hits or
# fewer come out at .0186 against a real .0208.
#
# WHAT IS STILL MISSING, stated plainly: a batter can only reach here on a hit or
# a walk. Real hitters also reach on errors and catcher's interference, .61% of
# plate appearances, and runners advance on wild pitches and passed balls. Those
# are baserunners -- and runs -- that arrive without a hit. They are not modelled
# because the per-PA rates this engine is fed are a hitter's own H/BB/HBP;
# reach-on-error is a property of the defence behind him, and inventing a
# hitter-level rate for it would be worse than leaving the gap stated.
#
# Every rate below is measured from 300 real 2025 games of StatsAPI play-by-play
# (22,782 plate appearances), with the base-out state reconstructed before each
# play so the rate is stated over exactly the situation this engine rolls it in.
#
#     SINGLE                                          real
#       runner on 3rd scores                          .994   -> automatic here
#       runner on 2nd scores           by outs        .389 / .536 / .751
#       runner on 2nd thrown out                      .032
#       runner on 1st reaches 3rd+     by outs        .305 / .271 / .468
#       runner on 1st thrown out                      .018
#         of those reaching 3rd+, scores              .131
#     DOUBLE
#       runner on 3rd scores                          .990   -> automatic
#       runner on 2nd scores                          .968   -> automatic
#       runner on 1st scores           by outs        .293 / .289 / .475
#       runner on 1st thrown out                      .053
#     TRIPLE (n=54), HOME RUN                        1.000   -> automatic
#     ANY OUT, fewer than 2 down -- and "any out" is right, because code 0 in
#     this engine is every PA that is not a hit and not a walk, strikeouts
#     included. Measuring these over balls in play only would overstate them by
#     half (a strikeout scores the man on third .026 of the time; a ball in play
#     scores him .547):
#       double play          | runner on 1st          .155
#       runner on 3rd scores | no double play         .402
#       runner on 2nd to 3rd | 3rd open               .339
#       runner on 1st to 2nd | 2nd open               .204
#       runner on 3rd scores | double play            .125
#
# THE OUT COUNT IS THE BIGGEST SINGLE DRIVER and the old code ignored it. With
# two down the runner leaves on contact; with nobody out he has to wait and see
# the ball land. Scoring from second on a single nearly doubles across that
# span, .389 to .751, and no fixed constant can sit in both places.
_S_2B_SCORE = (0.389, 0.536, 0.751)   # single: runner on 2nd scores, by outs
_S_1B_THIRD = (0.305, 0.271, 0.468)   # single: runner on 1st reaches 3rd+
_S_1B_HOME = 0.131                    # ... and of those, comes all the way round
_D_1B_SCORE = (0.293, 0.289, 0.475)   # double: runner on 1st scores, by outs
# Thrown out trying. These cost the runner AND an out, which is why the engine
# needed them: without a price on advancing, taking the extra base was free.
_TOOB_S_2B = 0.032
_TOOB_S_1B = 0.018
_TOOB_D_1B = 0.053
# --- WHAT KIND OF OUT WAS IT ---------------------------------------------------
# The engine used to have ONE out. A strikeout, a fly ball and a ground ball are
# not one event -- they are three, and they do almost opposite things to the men
# on base. Measured over the same 300 games (the row is what happens to that
# runner, given fewer than two down):
#
#                              STRIKEOUT   GROUND    FLY      old single number
#     double play (1st on)        .045      .424     .018          .155
#     runner on 3rd scores        .026      .484     .596          .402
#     runner on 2nd -> 3rd        .056      .685     .230          .339
#     runner on 1st -> 2nd        .146      .534     .077          .204
#
# The double-play rate varies TWENTY-FIVE FOLD between a ground ball and a fly
# ball. Blending it meant a contact hitter and a three-true-outcomes slugger ran
# the bases identically, when strikeouts are 43% of Aaron Judge's outs against a
# league 32%. The mix is per hitter, from the groundOuts / airOuts / strikeOuts
# counters already in every stat line the app pulls -- league .318 / .358 / .324,
# which independently reproduces the .316 / .364 / .320 read off the play-by-play
# descriptions.
#
# THE STRIKEOUT COLUMN IS DELIBERATELY NOT THE MEASURED ONE. A runner reaching
# second on a strikeout is almost always a STOLEN BASE, and this engine already
# rolls steals separately, before the batter -- taking .146 here would count them
# twice. Same for the .045 "double play" on a strikeout, which is a strikeout
# plus a caught stealing the engine also already has. Only the runner scoring
# from third survives, because that one is a wild pitch, which nothing else here
# models.
_LG_OUT_MIX = (0.3241, 0.3181, 0.3577)   # strikeout, ground, air -- 2025 league
_DP_BY_TYPE = (0.000, 0.424, 0.018)      # runner on 1st, <2 out, third EMPTY
_DP_BY_TYPE_R3 = (0.000, 0.373, 0.012)   # ... and with a runner on third
_SCORE_3B_BY_TYPE = (0.026, 0.484, 0.596)
_ADV_2B_BY_TYPE = (0.000, 0.685, 0.230)
_ADV_1B_BY_TYPE = (0.000, 0.534, 0.077)
#
# THE DOUBLE PLAY ALSO DEPENDS ON WHETHER THIRD IS OCCUPIED. Within ground balls,
# .424 with third empty against .373 with a runner there -- fielders concede the
# second out to check him or go home with it instead. That was 2.9 sigma when
# measured across all out types (.160 vs .112) and it survives the split.
#
# The runner on third does NOT need conditioning on the force, which is worth
# recording because the opposite is the intuitive guess. With the bases loaded
# the fielder has a force at the plate and takes it, so the run should score less
# often -- and it does, but not measurably:
#
#     third scores, first base empty      .382 +/- .029
#     third scores, first on, not loaded  .421 +/- .031
#     third scores, BASES LOADED          .369 +/- .035
#
# All three sit inside about one and a half standard errors of each other. The
# force only bites on a ground ball, and strikeouts and fly balls -- where it is
# irrelevant -- are most of the denominator.
#
# AND NOTHING IS THROWN OUT TAGGING UP. Runner on third, ball caught in the air,
# fewer than two down: he scored 161 times, held 109 times, and was thrown out
# ZERO times in 270 chances. Going to third off a fly is 3 in 413. So an
# outfielder's arm does not belong in a "can he make the throw" branch -- that
# branch would never fire. Its whole effect is DETERRENCE, and deterrence is
# already inside the .596: the runner holds four times in ten precisely because
# somebody with a real arm is standing out there. Modelling the arm would mean
# modelling the go/no-go decision, which needs the ball's depth and hang time,
# and this engine has no batted-ball location -- only its type.
# A RUN ON A DOUBLE PLAY, and this one was wrong by a factor of six.
#
#     3rd scores | NOBODY out before     34 / 42    .8095
#     3rd scores | one out before         3 / 129   .0233
#     3rd scores | blended               37 / 171   .2164   <- what I measured
#
# The engine only rolls this when the double play came from nobody out -- with
# one down the DP is the second and third outs and no run can count, which the
# `outs < 3` gate already handles. So the right denominator is the first row.
# The blended figure is dominated by the cases where scoring is PHYSICALLY
# IMPOSSIBLE, and using it meant a bases-loaded ground ball with nobody out
# almost never brought the run home. That is the single event that produced the
# stranding: bases loaded and nobody out, the engine came away with nothing 18.1%
# of the time against a real 11.5%.
_DP_SCORE_3B = 0.810
# And the runner on second takes third on most of them -- a 6-4-3 leaves him
# ninety feet further along, where the engine used to leave him standing.
_DP_ADV_2B = 0.790

# --- THE EVENT INVENTORY, AND A NEGATIVE RESULT --------------------------------
# Every event real baseball produces was listed by rate per team-game and checked
# one at a time against what this engine can express. Most were already modelled,
# or folded into something with the identical base-out consequence (a hit by
# pitch is a walk; a force play leaves the same men aboard as the batter being
# retired), or under a rate worth any code at all -- balks .018 a team-game,
# catcher's interference .023, triple plays .003, defensive indifference .058.
#
# Two came out clearly on top of what was genuinely missing:
#
#     wild pitch / passed ball / balk    .0202 per PA with a runner aboard
#                                        (.055 runs a team-game score on one)
#     batter reaches on an error         .0061 per PA  (.208 a team-game)
#
# BOTH WERE BUILT, MEASURED, AND TAKEN BACK OUT. They are real, the rates above
# are right, and adding them made the simulator fit real baseball WORSE on every
# hit-based number:
#
#                          hits/g   runs/g   hits per run   no-hitters
#     without (shipped)     8.375    4.508      1.8580      1 in 1,803
#     with, as measured     7.983    4.362      1.8299      1 in 1,049
#     REAL                  8.259    4.447      1.8570      1 in 2,433
#
# The mechanism is not mysterious: _team calibrates RUNS to er, so any run this
# engine manufactures without a hit is a run it then takes away from the hitters,
# and the hits fall. For that to come out right, a wild pitch would have to be
# worth the same here as it is in a real game, and it is worth more -- adding
# .53 baserunner-events a game cost .39 hits a game, which is too many.
#
# So somewhere the engine still converts a baserunner into a run slightly too
# readily, and today the missing wild pitches and errors are cancelling that
# error out. Two wrongs, and removing one of them is worse than leaving both.
# That is not a satisfying place to stop, but shipping a change that degrades
# every hit prop in order to be more detailed is the wrong trade, and the honest
# version of "more realistic" is realistic ABOUT ITS OWN FIT.
#
# THE SAME THING HAPPENED TO A STRAIGHT MEASUREMENT ERROR OF MINE, which is what
# makes this a diagnosis rather than a shrug. The runner on third scoring on a
# GROUND BALL was measured at .484 over all ground outs -- including double
# plays, where he cannot score. The engine only rolls it on the non-double-play
# branch, so the right denominator gives .596. That correction is unambiguously
# right about baseball, and applying it moved the engine the same way the wild
# pitch did: hits/run 1.8580 -> 1.8666 and no-hitters 1 in 1,803 -> 1 in 1,196.
#
# Three separate corrections, each independently correct, each adding a way for a
# run to score without a hit, and all three degrade the fit. That is not three
# coincidences. The engine converts a baserunner into a run roughly 3-4% too
# readily, and the constant it has been hiding behind is the one thing that was
# fitted rather than measured -- .484 is doing the work of a fudge factor, and
# every honest number I put in its place exposes it.
#
# So the whole attempt is reverted, including my own correction, and the engine
# ships at its best measured fit.
#
# --- RE24: WHAT THE RUN EXPECTANCY TABLE SAYS ----------------------------------
# Expected runs from each base-out state to the end of the inning, measured over
# 15,907 complete half-innings of 2025 play-by-play (truncated halves dropped --
# a walk-off, or a home ninth never batted, would drag every estimate down),
# against the same table read straight off _half_inning, which already takes
# start_outs and start_bases and so needs no instrumentation at all.
#
# REAL_RE24 below is that measurement. The comparison carries sampling error on
# BOTH sides -- the engine's own table moves about a point between seeds, which
# is not something to leave out of the arithmetic:
#
#     ALL 24 STATES, frequency weighted     -0.67%   (-1.1 sd)
#
# THE ENGINE CONVERTS A BASERUNNER INTO A RUN CORRECTLY. That is the headline,
# and it CORRECTS what this note used to say: "3-4% too readily" was inferred
# from the wild-pitch experiment, and the table does not support it.
#
# What the table does show is a TRAFFIC gap. With one man aboard the engine is
# inside 2% of real at every out count. With two or three aboard and nobody out
# it is short by a tenth or more:
#
#     12_ 0 out  -13.3%     1_3 0 out  -10.3%
#     123 0 out  -11.2%     _23 0 out   -9.5%
#
# THE PROBE WAS AUDITED BEFORE THE ENGINE, because two of the errors this file
# documents were mine and not the model's. The first table was read off nine
# IDENTICAL hitters drawn uniformly across the order, where a real lineup runs
# .3423 OBP at the top against .2878 at the bottom, and where the batter due up
# depends on the state. Rebuilt with per-slot rates and the slot drawn from the
# real state-conditional distribution:
#
#     flat lineup, uniform slot     all 24 +0.06%   0 out -2.99%   2 out +5.37%
#     graded lineup, uniform slot          -0.01%         -2.55%         +5.26%
#     graded lineup, REAL slot mix         +0.85%         -1.36%         +5.21%
#
# The confound is real, it is worth about a point and a half at nobody out, and
# it runs the OPPOSITE way to the guess that motivated the check -- loaded states
# select WORSE hitters (mean slot 5.06 against an overall 4.83), not better. The
# traffic gap does not move at all. It is the engine.
#
# HOW IT GOES WRONG IS STRANDING, NOT THE TAIL. Runs scored from bases loaded
# and nobody out:
#
#     ZERO runs    sim 18.1%   real 11.5%      <- the engine comes away empty
#     3+ runs      sim 37.9%   real 41.8%         57% more often than reality
#
# and from a lone runner on first, the control, it matches to a point.
#
# THAT LOCATED A FOURTH MEASUREMENT ERROR OF MINE, worth a factor of six:
#
#     3rd scores on a DP | NOBODY out before    34 / 42    .8095
#                        | one out before        3 / 129   .0233
#                        | blended              37 / 171   .2164  <- I used .125
#
# The engine only rolls this when the DP came from nobody out; with one down the
# DP is the second and third outs and no run can count, which the `outs < 3` gate
# already handles. So the blended figure was dominated by cases where scoring is
# PHYSICALLY IMPOSSIBLE. Fixed, along with the runner on second taking third on
# .79 of double plays where the engine used to leave him standing. Bases loaded
# and first-and-third improved by 2.5 and 3.0 points; first-and-second and
# second-and-third barely moved, which is right -- second-and-third has nobody on
# first, so no double play was ever possible there.
#
# TWO EXPLANATIONS TESTED AND REJECTED, so they do not get re-proposed:
#
#   * The double play RATE. Switching it off entirely still leaves
#     first-and-second at -3% and second-and-third at -7%, and it breaks the one
#     state where the rate is demonstrably right: a lone runner on first goes
#     -2% -> +10%. The rate was fine; the run it failed to score was not.
#   * Pitcher-day variance -- one rate multiplier drawn per half-inning instead
#     of identical rates every PA, on the theory that traffic is itself evidence
#     the pitcher is off today. A multiplicative spread lifts EVERY row convexly
#     (0 out +1.95%, 1 out +5.84%, 2 out +9.93% at sigma .18). It does not tilt
#     them. Whatever the rest of the mechanism is, it is not that.
#
# --- AND THE THING ALL OF THIS KEEPS RUNNING INTO -----------------------------
# Every correct fix to baserunning in this file has cost the engine hits, and it
# is always the same mechanism: _team SCALES A LINEUP'S RATES UNTIL ITS RUNS
# EQUAL er. Runs are pinned to a target. Hits are whatever falls out. So a run
# manufactured anywhere else -- a wild pitch, a run on a double play, an error --
# is a run subtracted from the hitters, and the hit level absorbs every
# improvement made to the baserunning.
#
#     wild pitch + reach on error   hits/g 8.375 -> 7.983
#     run on a double play          hits/g 8.375 -> 8.183   (real 8.259)
#
# The second is a smaller move toward a better number, which is why it stays and
# the first did not. But the pattern is the point: HITS ARE A FREE VARIABLE HERE
# AND NOTHING PINS THEM DOWN. Feed the engine the league's own per-PA rates with
# no calibration at all and it produces about the right runs on its own, which
# says the rates and the baserunning are broadly sound and the scaling step is
# what is throwing the hits around.
#
# So the next question is not another event and not another advancement rate. It
# is whether _team should be scaling every rate to hit er at all, or whether it
# should trust the rates it was given and let the total land where it lands.
#
# WHAT THIS MEANS FOR BETTING, stated plainly. Totals and moneylines read the
# frequency-weighted number, which is right. The tilt lands on the tails -- team
# total OVERS want the big innings the engine is 9-14% short of, and RBI props
# settle disproportionately in exactly those loaded states. Those are the legs to
# treat as the least trustworthy until this is closed.
REAL_RE24 = {
    "___": (0.4999, 0.2690, 0.1042),
    "1__": (0.8861, 0.5124, 0.2295),
    "_2_": (1.1008, 0.6166, 0.3054),
    "12_": (1.6490, 0.9384, 0.4419),
    "__3": (1.3535, 0.8217, 0.3426),
    "1_3": (1.9050, 1.2389, 0.4594),
    "_23": (2.0865, 1.4055, 0.6322),
    "123": (2.5594, 1.5918, 0.7697),
}
_RE24_N = {                          # PAs behind each real cell, for weighting
    "___": (16685, 12011, 9502), "1__": (3994, 4861, 5063),
    "_2_": (863, 1557, 1978), "12_": (1000, 1738, 2030),
    "__3": (99, 488, 867), "1_3": (337, 720, 1084),
    "_23": (208, 439, 503), "123": (261, 610, 747),
}


def run_expectancy(setup, rnd, n=8000, pen_frac=0.4):
    """The engine's own RE24, in the same shape as REAL_RE24.

    Drops the lineup into each base-out state and averages the runs it scores
    before the third out. `idx` cycles across three turns through the order so
    the table is not read off one lineup spot."""
    out = {}
    L = len(setup)
    slots = {"1__": (0, None, None), "_2_": (None, 0, None), "12_": (0, 1, None),
             "__3": (None, None, 0), "1_3": (0, None, 1), "_23": (None, 0, 1),
             "123": (0, 1, 2), "___": (None, None, None)}
    for st, offs in slots.items():
        row = []
        for o in (0, 1, 2):
            tot = 0.0
            for i in range(n):
                bases = [None if x is None else (i - 1 - x) % L for x in offs]
                tot += _half_inning(setup, [[0] * 7 for _ in range(L)], i % 27,
                                    rnd, start_outs=o, start_bases=bases,
                                    pen=((i % 10) < pen_frac * 10))[0]
            row.append(tot / float(n))
        out[st] = tuple(row)
    return out
# How hard a runner's own speed swings an advancement roll. `spd` is sprint
# speed centred on the league average and clamped to [0.8, 1.2] upstream, so
# this is a +/-20% relative swing on any base-taking chance -- the fastest and
# slowest regulars really do sit about that far apart on first-to-third.
_SPD_ADV = 1.0


def _adv(p, spd):
    """An advancement chance scaled by the runner's speed, centred so that a
    league-average runner (spd == 1.0) sits exactly on the measured rate.

    The bounds are proportional rather than the fixed floors the old code used.
    Those floors were written against a single constant and quietly became
    wrong once the rate moved with the out count: a `max(0.4, ...)` floor under
    a nobody-out rate of .389 pinned even the slowest runner ABOVE the league
    average, which is the opposite of what a floor is for."""
    return max(0.02, min(0.95, p * (1.0 + (spd - 1.0) * _SPD_ADV)))


def _half_inning(setup, stats, idx, rnd, ghost=False, lead_target=None, base_runs=0,
                 late=False, subbed=None, start_outs=0, start_bases=None, pen=False):
    """One half-inning of base-out simulation for a lineup. Returns
    (runs, next_batter_idx, walkoff).

    ghost: extra-innings placed runner -- the previous batter starts on 2nd.
    lead_target/base_runs: the walk-off rule for the home half of the 9th and
    extras -- the half ENDS the moment base_runs+runs exceeds lead_target. Per
    MLB rules only the winning run scores on a non-HR (trailing runners don't),
    while a walk-off HR counts in full.
    late/subbed: the pinch-hitter model. In late innings a batter with a measured
    substitution deficit can be lifted (added to `subbed`); a PHANTOM bench bat
    (base index -1) takes his PAs from then on -- same outcome rates so team
    offense stays calibrated, but nothing credits to the starter's stat line.
    start_outs/start_bases: resume a half already in progress (live games), with
    the outs already recorded and the runners already aboard."""
    L = len(setup)
    outs, runs = start_outs, 0
    bases = [None, None, None]                # batter index on 1st/2nd/3rd (-1 = bench)
    if start_bases:
        bases = list(start_bases)
    if ghost:
        gi = (idx - 1) % L
        bases[1] = -1 if (subbed and gi in subbed) else gi
    wo = lead_target is not None

    def won():
        return wo and base_runs + runs > lead_target

    def credit_run(r):                        # a runner crosses the plate
        nonlocal runs
        runs += 1
        if r >= 0:
            rs = stats[r]; rs[3] += 1; rs[6] += 2

    while outs < 3:
        # Steal of 2nd: runner on 1st, 2nd open, < 2 outs (bench runners don't run).
        if bases[0] is not None and bases[0] >= 0 and bases[1] is None and outs < 2:
            rr = bases[0]
            if rnd() < setup[rr]["sbr"]:
                # The catcher finally has an arm: sb_adj is the opposing club's
                # stolen-base-percentage-allowed vs league (set in simulate()),
                # so running on a cannon costs real success probability and a
                # turnstile pays it back. Same clamp as always.
                if rnd() < max(0.60, min(0.93,
                                         _SB2_OK + (setup[rr]["spd"] - 1.0) * _SB_SPD
                                         + setup[rr].get("sb_adj", 0.0))):
                    bases[1] = rr; bases[0] = None
                    stats[rr][5] += 1; stats[rr][6] += 5            # SB +5
                else:
                    bases[0] = None; outs += 1                      # caught stealing
                    if outs >= 3:
                        break
        # Steal of 3rd: runner on 2nd, 3rd open, < 2 outs (rarer, higher success).
        elif bases[1] is not None and bases[1] >= 0 and bases[2] is None and outs < 2:
            rr = bases[1]
            if rnd() < setup[rr]["sbr"] * _SB3_FRAC:
                if rnd() < max(0.50, min(0.88,
                                         _SB3_OK + (setup[rr]["spd"] - 1.0) * _SB_SPD
                                         + setup[rr].get("sb_adj", 0.0))):
                    bases[2] = rr; bases[1] = None
                    stats[rr][5] += 1; stats[rr][6] += 5
                else:
                    bases[1] = None; outs += 1
                    if outs >= 3:
                        break
        bi = idx % L
        # Which time through the order this is. `idx` runs continuously across
        # innings (that is why it is threaded back out of every half-inning), so
        # idx // L is exactly the number of complete passes the pitcher has made.
        turn = idx // L
        idx += 1
        phantom = False
        if late and subbed is not None:
            if bi in subbed:
                phantom = True
            elif setup[bi]["psub"] and rnd() < setup[bi]["psub"]:
                subbed.add(bi); phantom = True                     # lifted right now
        # THE INTENTIONAL WALK. Late, first base open, a runner in scoring
        # position, at least one out, and the man at the plate is a far bigger
        # extra-base threat than the man on deck: the manager points to first.
        # _IBB_P is calibrated so league-shaped lineups produce roughly the
        # real ~0.10 intentional passes per team-game of the universal-DH era
        # (see the calibration probe); the danger gate keeps it pointed at
        # actual sluggers rather than making late innings a free-pass parade.
        if (late and outs >= 1 and bases[0] is None
                and (bases[1] is not None or bases[2] is not None)
                and not phantom
                and setup[bi]["dang"] >= _IBB_DANG
                and setup[bi]["dang"] > _IBB_RATIO * setup[(bi + 1) % L]["dang"]
                and rnd() < _IBB_P):
            code = 5                              # a walk, without the at-bat
            u = -1.0
            _IBB_N[0] += 1                        # calibration/diagnostics counter
        else:
            u = rnd()
            code = 0
        _tt = setup[bi].get("thresh_tto")
        if _tt:
            # Last slot is the bullpen ladder; otherwise the turn's own, capped.
            _row = _tt[-1] if pen else _tt[min(turn, len(_tt) - 2)]
        else:
            _row = setup[bi]["thresh"]
        if u >= 0.0:                          # an IBB (u = -1) skips the at-bat
            for acc, c in _row:
                if u < acc:
                    code = c
                    break
        s = [0, 0, 0, 0, 0, 0, 0] if phantom else stats[bi]        # discard row for PH
        onb = -1 if phantom else bi                                # what goes on base
        if code == 0:                         # out
            # WHICH out. One draw off this hitter's own strikeout / ground / air
            # mix, and everything below reads from that column instead of a
            # league blend.
            _kg = setup[bi].get("okg") or (_LG_OUT_MIX[0],
                                           _LG_OUT_MIX[0] + _LG_OUT_MIX[1])
            _u = rnd()
            _t = 0 if _u < _kg[0] else (1 if _u < _kg[1] else 2)
            # Double play: runner on 1st, < 2 outs -> erase batter + lead runner.
            # Much less likely with a runner on third, where the fielder is
            # checking him or going home instead of turning two.
            _dp = (_DP_BY_TYPE_R3 if bases[2] is not None else _DP_BY_TYPE)[_t]
            if bases[0] is not None and outs < 2 and rnd() < _dp:
                outs += 2
                bases[0] = None
                # A run can still come home on a double play -- but only if the
                # DP was not the third out, since no run scores when the inning
                # ends on a force. No RBI: the rules do not award one to a
                # batter who grounds into one.
                if outs < 3 and bases[2] is not None and rnd() < _DP_SCORE_3B:
                    credit_run(bases[2])
                    bases[2] = None
                    if won():
                        return runs, idx, True
                if outs < 3 and bases[1] is not None and bases[2] is None \
                        and rnd() < _DP_ADV_2B:
                    bases[2] = bases[1]; bases[1] = None
            else:
                # Resolved LEAD RUNNER FIRST so a vacated base is available to
                # the man behind him: third scores, second takes third, first
                # takes second. The engine used to move nobody but the man on
                # third, which made every out a dead end -- real outs advance a
                # runner about a fifth of the time.
                if bases[2] is not None and outs < 2 \
                        and rnd() < _SCORE_3B_BY_TYPE[_t]:
                    credit_run(bases[2])
                    s[4] += 1; s[6] += 2; bases[2] = None   # sac fly: RBI stands
                    if won():
                        return runs, idx, True
                if bases[1] is not None and bases[2] is None and outs < 2 \
                        and rnd() < _ADV_2B_BY_TYPE[_t]:
                    bases[2] = bases[1]; bases[1] = None
                if bases[0] is not None and bases[1] is None and outs < 2 \
                        and rnd() < _ADV_1B_BY_TYPE[_t]:
                    bases[1] = bases[0]; bases[0] = None
                outs += 1
        elif code == 5:                       # walk (force advances only)
            s[6] += 2
            if bases[0] is None:
                bases[0] = onb
            elif bases[1] is None:
                bases[1] = bases[0]; bases[0] = onb
            elif bases[2] is None:
                bases[2] = bases[1]; bases[1] = bases[0]; bases[0] = onb
            else:                             # bases loaded -> forced run
                credit_run(bases[2])
                s[4] += 1; s[6] += 2
                bases[2] = bases[1]; bases[1] = bases[0]; bases[0] = onb
                if won():
                    return runs, idx, True
        else:                                 # a hit
            s[0] += 1; s[1] += code; s[6] += _DK_HIT[code]
            # bases is [1st, 2nd, 3rd] everywhere in this engine -- the walk
            # branch above, the steal gates, the live-game resume in
            # state_from_snapshot -- so this unpack has to run in that order.
            r1, r2, r3 = bases
            scored = 0
            spd1 = setup[r1]["spd"] if (r1 is not None and r1 >= 0) else 1.0
            spd2 = setup[r2]["spd"] if (r2 is not None and r2 >= 0) else 1.0
            # Outs as they stood when the ball was hit. A runner gunned down on
            # this same play must not change how aggressive the man behind him
            # was, and the real rates are conditioned the same way.
            o0 = min(outs, 2)
            if code == 4:                     # HR: everyone (incl. batter) scores
                s[2] += 1
                for r in (r1, r2, r3):
                    if r is not None:
                        credit_run(r); scored += 1
                runs += 1; s[3] += 1; s[6] += 2                     # batter run
                s[4] += 1 + scored; s[6] += 2 * (1 + scored)       # RBI
                bases = [None, None, None]
                if won():                     # walk-off HR counts in full
                    return runs, idx, True
            elif code == 3:                   # triple: all runners score
                for r in (r3, r2, r1):        # lead runner crosses first
                    if r is not None:
                        credit_run(r); scored += 1
                        if won():             # winning run ends it; rest don't score
                            s[4] += scored; s[6] += 2 * scored
                            return runs, idx, True
                s[4] += scored; s[6] += 2 * scored
                bases = [None, None, onb]
            elif code == 2:                   # double
                nb = [None, onb, None]        # batter to 2nd
                # From 3rd (.990 real) and from 2nd (.968) -- close enough to
                # automatic that modelling the hold would only put a runner on a
                # base the man behind him needs.
                for r in (r3, r2):            # lead runner crosses first
                    if r is not None:
                        credit_run(r); scored += 1
                        if won():
                            s[4] += scored; s[6] += 2 * scored
                            return runs, idx, True
                if r1 is not None:            # from 1st: score, get gunned, or 3rd
                    u1 = rnd()
                    p_sc = _adv(_D_1B_SCORE[o0], spd1)
                    if u1 < p_sc:
                        credit_run(r1); scored += 1
                        if won():
                            s[4] += scored; s[6] += 2 * scored
                            return runs, idx, True
                    elif u1 < p_sc + _TOOB_D_1B:
                        outs += 1             # thrown out at the plate
                    else:
                        nb[2] = r1
                bases = nb
                s[4] += scored; s[6] += 2 * scored
            else:                             # single
                nb = [onb, None, None]        # batter to 1st
                if r3 is not None:            # from 3rd: .994 real -> automatic
                    credit_run(r3); scored += 1
                    if won():
                        s[4] += scored; s[6] += 2 * scored
                        return runs, idx, True
                if r2 is not None:            # from 2nd: score, gunned, or 3rd
                    u2 = rnd()
                    p_sc = _adv(_S_2B_SCORE[o0], spd2)
                    if u2 < p_sc:
                        credit_run(r2); scored += 1
                        if won():
                            s[4] += scored; s[6] += 2 * scored
                            return runs, idx, True
                    elif u2 < p_sc + _TOOB_S_2B:
                        outs += 1             # thrown out at the plate
                    else:
                        nb[2] = r2
                # If the man ahead was gunned down for the third out, nothing
                # behind him scores -- the inning is already over.
                if r1 is not None and outs < 3:   # from 1st: 3rd+, gunned, or 2nd
                    u1 = rnd()
                    p_adv = _adv(_S_1B_THIRD[o0], spd1)
                    if u1 < p_adv:
                        if rnd() < _S_1B_HOME:        # all the way around
                            credit_run(r1); scored += 1
                            if won():
                                s[4] += scored; s[6] += 2 * scored
                                return runs, idx, True
                        elif nb[2] is None:
                            nb[2] = r1
                        else:                 # third is taken -- hold at second
                            nb[1] = r1
                    elif u1 < p_adv + _TOOB_S_1B:
                        outs += 1             # thrown out stretching
                    else:
                        nb[1] = r1
                bases = nb
                s[4] += scored; s[6] += 2 * scored
    return runs, idx, False


def _play_game(setup, rnd, opp_sp_ip=None):
    """One SOLO 9-inning game for a lineup (no opponent interaction) -- the
    calibration reference `_team` converges against. Returns (runs, per-batter
    [hits, tb, hr, runs_scored, rbi, sb, dk_points]). dk_points is the batter's
    DraftKings fantasy total (1B+3 2B+5 3B+8 HR+10 R+2 RBI+2 BB+2 SB+5).

    `opp_sp_ip` is the OPPOSING starter's expected innings; past his sampled exit
    the lineup faces the bullpen and the times-through-order penalty stops. It
    matters here and not only in the real-rules matchup because this is the
    function `_team` calibrates against -- if the two disagreed about when the pen
    enters, the rate multiplier would be tuned against a game that is never
    played."""
    L = len(setup)
    stats = [[0, 0, 0, 0, 0, 0, 0] for _ in range(L)]   # H,TB,HR,R,RBI,SB,DK
    runs, idx, first_inning = 0, 0, 0
    subbed = set()
    exit_inn = _sample_exit(opp_sp_ip, rnd)
    for _inn in range(_N_INNINGS):
        r, idx, _ = _half_inning(setup, stats, idx, rnd,
                                 late=_inn >= 6, subbed=subbed,
                                 pen=_inn >= exit_inn)
        runs += r
        if _inn == 0:
            first_inning = r
    return runs, stats, first_inning


# --- THE RUN DISTRIBUTION IS TOO NARROW, AND WIDENING IT IS THE WRONG FIX ------
# The engine draws every plate appearance from fixed rates, so the only spread in
# a game total is binomial noise. Real starters have good days and bad ones.
# Measured INSIDE A FIXED MATCHUP, which is the comparison that matters -- the
# league's pooled spread also mixes over matchups, and the 16 test pairs mix over
# a wider range of expected totals than the league does, so pooled numbers
# understate this:
#
#     within-matchup sd of a game total    sim 4.220    real 4.557   (-7.4%)
#
# It prices out at about a cent and a half on the tails, one-directional: every
# high line looks less likely than it is, every low line more. On team totals,
# P(over 7.5) reads .147 against a real .162.
#
# THE OBVIOUS FIX WAS BUILT AND MEASURED AND DOES NOT WORK. A rate multiplier
# drawn once per game per side, seven quantile points, divided back out so the
# run mean is preserved:
#
#                          hits/g   runs/g      H/R   no-hitter  within-sd  o7.5
#     shipped, no shock     8.214    4.398   1.8676   1 in 1,260    4.220   .1473
#     sd .05, mean-held     8.188    4.389   1.8657   1 in 1,081    4.250   .1475
#     real                  8.259    4.447   1.8572   1 in 2,433    4.557   .1619
#
# It buys .03 of standard deviation and two hundredths of a cent, and it costs
# 14% on the no-hitter rate. An uncentred version looks much better -- sd .09
# reaches .157 on the line -- but that gain is the mean drifting up 1.7%, not
# dispersion, and re-centring gives it all back.
#
# THE REASON IS A SHAPE PROBLEM, NOT A SCALE ONE, and it is worth stating because
# it kills a whole family of would-be fixes. Scaling rates widens BOTH tails. The
# engine's low tail is ALREADY too fat -- no-hitters run twice as often as real
# baseball before anything is touched. Real baseball's distribution is wider than
# this engine's at the top while being THINNER at the bottom. No symmetric
# widening can produce that; it makes the bad end worse faster than it helps the
# good end.
#
# So the dispersion gap stays open, deliberately, and the thing to fix first is
# the LOW tail -- the same defect the no-hitter rate has been reporting all
# along. Widening the distribution is not the tool for it.


def _play_matchup(setup_a, setup_h, rnd, state=None, ip_h=None, ip_a=None):
    """A full REAL-RULES game between two lineups: interleaved half-innings, the
    home team skips the bottom of the 9th when already leading, a walk-off ends
    the game the moment home takes the lead in the bottom 9th or extras, and
    ties go to ghost-runner extra innings. Returns
    (away_runs, home_runs, stats_a, stats_h, first_inning_run, extra_innings).

    `state` resumes a game already under way: the score, the half and base-out
    situation to pick up from, each side's spot in the order, and the stat lines
    already banked. Everything downstream then reads a full-game total (what has
    happened plus what is simulated), which is what a prop settles on."""
    stats_a = [[0, 0, 0, 0, 0, 0, 0] for _ in range(len(setup_a))]
    stats_h = [[0, 0, 0, 0, 0, 0, 0] for _ in range(len(setup_h))]
    ia = ih = ra = rh = f1 = 0
    sub_a, sub_h = set(), set()
    # Each lineup hands off when the OPPOSING starter does: the away bats face the
    # home starter (ip_h) and vice versa. Sampled once per game so both halves of
    # a frame agree on who is pitching.
    exit_a = _sample_exit(ip_h, rnd)      # away lineup's opponent
    exit_h = _sample_exit(ip_a, rnd)      # home lineup's opponent
    start_inn, start_top, s_outs, s_bases = 0, True, 0, None
    if state:
        ia, ih = state.get("ia", 0), state.get("ih", 0)
        ra, rh = state.get("ra", 0), state.get("rh", 0)
        start_inn = max(0, (state.get("inning") or 1) - 1)
        start_top = state.get("is_top", True)
        s_outs = state.get("outs", 0)
        s_bases = state.get("bases")
        for dst, src in ((stats_a, state.get("stats_a")), (stats_h, state.get("stats_h"))):
            for i, row in enumerate(src or []):
                if i < len(dst):
                    dst[i] = list(row)
    for inn in range(start_inn, 9):
        late = inn >= 6
        first = inn == start_inn                # the half we resume into
        if not (first and not start_top):       # already past the top of this inning?
            r, ia, _ = _half_inning(setup_a, stats_a, ia, rnd, late=late, subbed=sub_a,
                                    pen=inn >= exit_a,
                                    start_outs=s_outs if (first and start_top) else 0,
                                    start_bases=s_bases if (first and start_top) else None)
            ra += r
            if inn == 0:
                f1 += r
        resume_bot = first and not start_top
        if inn < 8:
            r, ih, _ = _half_inning(setup_h, stats_h, ih, rnd, late=late, subbed=sub_h,
                                    pen=inn >= exit_h,
                                    start_outs=s_outs if resume_bot else 0,
                                    start_bases=s_bases if resume_bot else None)
            rh += r
            if inn == 0:
                f1 += r
        elif rh <= ra:                      # bottom 9 only if home isn't ahead
            r, ih, _ = _half_inning(setup_h, stats_h, ih, rnd,
                                    lead_target=ra, base_runs=rh,
                                    late=True, subbed=sub_h, pen=inn >= exit_h,
                                    start_outs=s_outs if resume_bot else 0,
                                    start_bases=s_bases if resume_bot else None)
            rh += r
    extra = 0
    while ra == rh and extra < 12:          # ghost-runner extras until decided
        extra += 1
        # Extras are always relief on both sides — no starter reaches the 10th.
        r, ia, _ = _half_inning(setup_a, stats_a, ia, rnd, ghost=True, pen=True,
                                late=True, subbed=sub_a)
        ra += r
        r, ih, _ = _half_inning(setup_h, stats_h, ih, rnd, ghost=True, pen=True,
                                lead_target=ra, base_runs=rh,
                                late=True, subbed=sub_h)
        rh += r
    if ra == rh:                            # 21-inning marathon failsafe (~never)
        if rnd() < 0.52:
            rh += 1
        else:
            ra += 1
    return ra, rh, stats_a, stats_h, f1, extra


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
                  er_opp=None, pen_out=0, budget=None, sp_bb_pa=None, resume=None,
                  pen_out_ids=None):
    """One game for a pitching staff against the opposing lineup.

    The starter throws until a sampled pitch limit (pulled earlier when he's
    being hit -- workload scales with the runs the offense actually scored this
    sim), then relievers (~1 inning apiece) finish. When the deep engine's named
    bullpen is supplied (`bullpen` = [{kpa, era}], best arm last) we cycle through
    the real relievers worst-first; otherwise we fall back to a generic K-rate
    draw off the bullpen's ERA.

    Tired arms sit. `pen_out_ids` names WHICH relievers are unavailable and is
    the path that should normally run: the pen loses exactly those pitchers,
    wherever they sit in the pecking order. `pen_out` is the old count-only
    fallback and thins the BEST end, which is only correct when the tired arms
    really were the good ones -- it benched a closer over a mop-up man's
    back-to-back, so it is used only when no ids are available.

    Returns the STARTER's (Ks, pitches, outs) and the bullpen's combined Ks."""
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
    if bullpen and pen_out_ids:
        # Sit the arms that are actually gassed. Keeps the pecking order intact,
        # so a tired mop-up man costs the pen a mop-up man and a tired closer
        # costs it the closer.
        # A None id must never match a reliever whose id is simply missing, or a
        # single unidentified arm would empty the whole pen.
        _out = {i for i in pen_out_ids if i is not None}
        kept = [r for r in bullpen
                if r.get("id") is None or r.get("id") not in _out]
        bullpen = kept or bullpen[:1]
    elif pen_out and bullpen:
        bullpen = bullpen[:-int(pen_out)] or bullpen[:1]   # no ids: old fallback
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
    if resume:
        # Pick the staff up where it stands: the starter's line is already in the
        # book, and if he has been pulled his K total is FINAL -- only the pen
        # adds from here. Seeding sp_pitches/sp_br means the same hook logic that
        # governs a simulated start decides how much longer this real one lasts.
        sp_k = resume.get("sp_k", 0) or 0
        sp_outs = resume.get("sp_outs", 0) or 0
        sp_pitches = float(resume.get("sp_pitches", 0) or 0)
        sp_br = resume.get("sp_br", 0) or 0
        bull_k = resume.get("bull_k", 0) or 0
        outs = resume.get("outs", 0) or 0
        starter_in = bool(resume.get("sp_in", True))
        if not starter_in:
            rel_kpa = next_reliever()
        elif sp_pitches >= limit:
            # He is past the budget we projected and the manager still has him out
            # there -- that is information the pre-game estimate didn't have. Give
            # him the leash his manager is visibly giving him instead of pulling
            # him on the very next batter.
            limit = sp_pitches + max(0.0, random.gauss(14, 7))
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


def _live_state(snap, setup_a, setup_h):
    """Translate a live snapshot into the resume state `_play_matchup` takes.

    The snapshot speaks in player NAMES (from the boxscore); the engine speaks in
    lineup indices. Anyone it can't place -- a pinch runner off the bench, say --
    goes on base as a phantom (-1), so he still occupies the bag and can score
    without crediting a run to a batter who isn't there.
    """
    idx_of = ({b["name"]: i for i, b in enumerate(setup_a)},
              {b["name"]: i for i, b in enumerate(setup_h)})

    def rows(setup, banked):
        # [hits, tb, hr, runs, rbi, sb, dk] per lineup spot, already in the book.
        out = []
        for b in setup:
            k = banked.get(b["name"]) or {}
            out.append([k.get("hit", 0), k.get("tb", 0), k.get("hr", 0),
                        k.get("r", 0), k.get("rbi", 0), k.get("sb", 0), 0])
        return out

    bat_idx = idx_of[0] if snap["is_top"] else idx_of[1]
    bases = [None, None, None]
    for i, nm in enumerate(snap.get("bases") or []):
        if nm:
            bases[i] = bat_idx.get(nm, -1)
    # Resume with the man actually due up. His order slot is the reliable signal;
    # the name is used when the posted lineup and the live one line up, which
    # keeps things right through a substitution that shifts the order.
    def due(side, setup, ids):
        i = snap["order_idx"][side]
        lu = snap["lineup"][side]
        if i < len(lu):
            nm = lu[i][1]
            if nm in ids:
                return ids[nm]
        return i
    return {
        "ia": due("away", setup_a, idx_of[0]),
        "ih": due("home", setup_h, idx_of[1]),
        "ra": snap["away_runs"], "rh": snap["home_runs"],
        "inning": snap["inning"], "is_top": snap["is_top"],
        "outs": snap["outs"], "bases": bases,
        "stats_a": rows(setup_a, snap["banked"]["away"]),
        "stats_h": rows(setup_h, snap["banked"]["home"]),
    }


def simulate(g, n=5000, live=None):
    """Simulate game `g` n times via base-running. Returns shared per-sim arrays,
    including per-batter hits/total-bases/HR/runs/RBI for player props (HRR) and
    a full pitching sim (starter Ks/pitches/IP + bullpen Ks).

    `live` (an mlb_live snapshot) resumes an in-progress game from its current
    base-out state instead of starting at 0-0: every batter begins with what he
    has already banked and every staff with the line it has already thrown, so
    the arrays still describe FULL-GAME totals -- the thing a prop settles on --
    while only the unplayed remainder is actually random.
    """
    props = g.get("props") or {}
    # TALENT-ONLY expected runs when the slate provides them. The home-field
    # multiplier is a crutch for the closed-form win model, which cannot see that
    # the home team bats last; this engine plays that rule directly and produces
    # the home edge on its own (53.35% with identical inputs, vs a real 52.89%).
    # Taking the tilted runs here would count home-field twice. Falls back to the
    # plain fields so a bare game dict, a test fixture or an older cached slate
    # still simulates.
    er_h = g.get("exp_runs_home_talent") or g.get("exp_runs_home") or 4.3
    er_a = g.get("exp_runs_away_talent") or g.get("exp_runs_away") or 4.3
    rnd = random.random
    # `_team` calibrates a lineup to `er` over a SOLO 9-inning game, but the
    # real-rules matchup shifts realized scoring: the home team loses the bottom
    # of the 9th when leading (and gets walk-off-truncated), while extras add a
    # little to both sides. These measured factors recenter each side's realized
    # matchup mean back onto its er (see the engine calibration test).
    # Starter workloads are read FIRST: _team calibrates against _play_game, and
    # that game now hands off to the bullpen, so the multiplier has to be fitted
    # against the same shape the matchup will actually play.
    ip_h = (props.get("ks_home") or {}).get("exp_ip") or 5.4
    ip_a = (props.get("ks_away") or {}).get("exp_ip") or 5.4
    # Each lineup's handoff is governed by the OPPOSING starter.
    setup_h = _team(props.get("batters_home"), er_h * _CAL_HOME, rnd, opp_sp_ip=ip_a)
    setup_a = _team(props.get("batters_away"), er_a * _CAL_AWAY, rnd, opp_sp_ip=ip_h)
    lam_h = (props.get("ks_home") or {}).get("expected")   # home starter, faces away
    lam_a = (props.get("ks_away") or {}).get("expected")   # away starter, faces home

    # Pitching inputs: starter K/9 (fall back from expected Ks) + bullpen quality.
    # Each starter's expected innings ride along so his pitch limit is his own.
    hsp, asp = g.get("home_sp") or {}, g.get("away_sp") or {}
    ht, at = g.get("home_team") or {}, g.get("away_team") or {}
    home_k9 = hsp.get("k9") or (lam_h / ip_h * 9 if lam_h else None)
    away_k9 = asp.get("k9") or (lam_a / ip_a * 9 if lam_a else None)
    # Walk-aware workload: each starter's own pitch budget + real walk rate, so a
    # wild arm burns pitches faster and gets pulled sooner (fewer Ks).
    bud_h = hsp.get("est_pitches") or (props.get("ks_home") or {}).get("est_pitches")
    bud_a = asp.get("est_pitches") or (props.get("ks_away") or {}).get("est_pitches")
    bbpa_h, bbpa_a = hsp.get("bb_pa"), asp.get("bb_pa")
    # STEAL DEFENSE. Each offense's steal-success shift is the OPPOSING club's
    # stolen-base-percentage-allowed vs league (catchers' arms + staff hold
    # games, measured by what actually happens on their watch). Clamped small:
    # the spread between the best and worst clubs is ~12-15 points of success.
    def _sb_adj(off_setup, def_team):
        pct = (def_team or {}).get("sb_allow_pct")
        lg = (def_team or {}).get("sb_lg_pct")
        if pct is None or lg is None:
            return
        adj = max(-0.12, min(0.12, float(pct) - float(lg)))
        for row in off_setup:
            row["sb_adj"] = adj
    _sb_adj(setup_h, at)
    _sb_adj(setup_a, ht)

    # Gassed relievers sit tonight. Prefer the id list, which names the arms that
    # are actually tired; the count is only a fallback for when identity is
    # missing, and it thins the good end of the pen whether or not the good arms
    # were the tired ones.
    _fat_h = ht.get("bullpen_fatigue") or {}
    _fat_a = at.get("bullpen_fatigue") or {}
    pen_h = int(_fat_h.get("count") or 0)
    pen_a = int(_fat_a.get("count") or 0)
    pen_ids_h = _fat_h.get("out_ids") or None
    pen_ids_a = _fat_a.get("out_ids") or None
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
    # marginal to the closed-form rate by thinning the yes's -- correlation
    # preserved, marginal honest. A side with no posted lineup falls back to an
    # independent calibrated draw.
    #
    # That closed-form target comes from props, not a second copy of the formula.
    # This module used to keep its own _RFI_K = 0.73 with a Poisson tail, so when
    # props moved to a negative binomial and a measured first-inning share the two
    # silently disagreed -- the engine would have thinned toward a 63% rate while
    # the board displayed 48%.
    p1a = 1 - _props._runs_pmf(er_a / 9.0 * _props.RFI_K, kmax=0)[0]
    p1h = 1 - _props._runs_pmf(er_h / 9.0 * _props.RFI_K, kmax=0)[0]
    p_target = 1 - (1 - p1a) * (1 - p1h)      # P(either team scores in the 1st)
    f1_raw = [False] * n                      # simulated 1st-inning run (either team)
    # A live game resumes from its own base-out state, and its staffs from the
    # lines they have already thrown.
    state = None
    res_h = res_a = None
    if live and setup_a and setup_h:
        state = _live_state(live, setup_a, setup_h)
        res_h = (live.get("pitching") or {}).get("home")
        res_a = (live.get("pitching") or {}).get("away")
    for i in range(n):
        if setup_a and setup_h:
            # Real-rules matchup: bottom-9 skip, walk-off, ghost-runner extras.
            ra, rh, sa, sh, f1, _x = _play_matchup(setup_a, setup_h, rnd, state=state,
                                                   ip_h=ip_h, ip_a=ip_a)
            store(sa, idx_a, i); store(sh, idx_h, i)
            home_runs[i] = rh
            away_runs[i] = ra
            f1_raw[i] = f1 > 0
            home_win[i] = rh > ra
        else:
            # Legacy independent fallback when a lineup isn't posted yet.
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
                                               pen_out_ids=pen_ids_h,
                                               budget=bud_h, sp_bb_pa=bbpa_h,
                                               resume=res_h)
            home_k[i] = sk; home_sp_pitch[i] = sp_p; home_sp_outs[i] = sp_o
            home_bull_k[i] = bk
        if do_away_pitch:
            sk, sp_p, sp_o, bk = _sim_pitching(away_k9, at.get("bullpen_era"),
                                               at.get("bullpen_whip"), rh, rnd,
                                               bullpen=at.get("bp_arms"), exp_ip=ip_a,
                                               er_opp=er_h, pen_out=pen_a,
                                               pen_out_ids=pen_ids_a,
                                               budget=bud_a, sp_bb_pa=bbpa_a,
                                               resume=res_a)
            away_k[i] = sk; away_sp_pitch[i] = sp_p; away_sp_outs[i] = sp_o
            away_bull_k[i] = bk

    # Recalibrate the simulated RFI marginal to the closed-form target: thin the
    # yes's when the sim runs hot (the usual case -- top of the order leads off),
    # or promote a few independent no's if it somehow runs cold. Thinning keeps
    # every retained yes tied to its simulated game, so RFI x Over / RFI x ML
    # correlations survive into the SGP masks.
    if live and live.get("rfi_settled"):
        # The 1st is already in the book -- there is nothing left to simulate and
        # nothing to calibrate. The market is decided at 0% or 100%.
        done = bool(live.get("rfi_runs"))
        return _pack(n, home_runs, away_runs, home_k, away_k, home_win, live,
                     home_sp_pitch, away_sp_pitch, home_sp_outs, away_sp_outs,
                     home_bull_k, away_bull_k, [done] * n, bat_h, bat_a)
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

    return _pack(n, home_runs, away_runs, home_k, away_k, home_win, live,
                 home_sp_pitch, away_sp_pitch, home_sp_outs, away_sp_outs,
                 home_bull_k, away_bull_k, rfi, bat_h, bat_a)


def _pack(n, home_runs, away_runs, home_k, away_k, home_win, live, home_sp_pitch,
          away_sp_pitch, home_sp_outs, away_sp_outs, home_bull_k, away_bull_k,
          rfi, bat_h, bat_a):
    """The shared per-sim arrays every consumer reads."""
    return {"n": n, "home_runs": home_runs, "away_runs": away_runs,
            "home_k": home_k, "away_k": away_k, "home_win": home_win,
            "home_sp_pitch": home_sp_pitch, "away_sp_pitch": away_sp_pitch,
            "home_sp_outs": home_sp_outs, "away_sp_outs": away_sp_outs,
            "home_bull_k": home_bull_k, "away_bull_k": away_bull_k,
            # Whether this run RESUMED a live game. build_candidates keys on it:
            # a live sim's win frequency has the actual score in it and is the
            # right moneyline; a pregame sim's should defer to the board's
            # official p_home instead of re-deriving a second opinion.
            "live": bool(live),
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

    def add(typ, label, pred, group=None, model=None, kref=None, avg=None, unit=None,
            marg_override=None):
        if types is not None and typ not in types:
            return
        # `group` = the underlying market (a player, or ML/Total/Run line); a
        # parlay never stacks two legs from the same group. `model` is the closed-
        # form (exact-math) probability for player props, kept alongside the
        # simulated marginal so the UI can show both. `kref` is a structured key
        # used to look up this leg's live Kalshi price (see kalshi_mlb). `avg`/
        # `unit` are the simulated average for this market (runs, K, hits, …).
        m = _mask(pred, n)
        marg = _popcount(m) / n
        # Reality-calibrate the leg's marginal against our graded track record —
        # the win model and batter props both run overconfident on the high end,
        # so temperature scaling reins the tails toward what actually happens
        # (self-tuning; a no-op until enough games have graded). Totals / run line
        # / RFI / starter Ks are left as the raw sim marginal.
        if marg_override is not None:
            marg = marg_override
        elif typ == "ML":
            marg = _calibrate.win_prob(marg)
        elif typ in ("Hit", "Bases", "HR", "HRR", "SB", "RBI"):
            # Per-market correction once that market has EARNED one on its own
            # graded predlog bucket (row + day floors); the pooled batter-prop
            # temperature until then. The pool averages markets that disagree
            # in sign, which is exactly what the per-market split is for.
            marg = _calibrate.prop_market(marg, _PREDLOG_BUCKET[typ])
        if 0.04 <= marg <= 0.97:
            cands.append({"type": typ, "label": label, "mask": m, "marg": marg,
                          "group": group or typ, "model_pct": model, "kref": kref,
                          "sim_avg": avg, "avg_unit": unit, "side": "yes"})

    # Moneyline (both sides; contradictory pairs are pruned in the search).
    #
    # PREGAME, THE BOARD'S p_home IS THE MONEYLINE — not a re-derived one. The
    # shipped pick probability already carries everything the win model knows:
    # calibration AND the deep-season engine's blend, which the sim never sees.
    # Letting the sim's raw win frequency ride as the marginal meant the combo
    # maker and the game card could disagree on the SAME game by 11pp whenever
    # the deep engine had a strong opinion (PIT@MIA: card 43.0%, combo leg
    # 54.5%). The mask keeps doing its real job — correlations with totals, run
    # lines and props — exactly as RFI already recalibrates its marginal to the
    # closed form; the bundle machinery rescales joints onto marginals anyway.
    # LIVE, the sim's own frequency is the right number (the score is in it) and
    # the pregame p_home is stale, so the override only applies pregame.
    ph, pa = g.get("p_home"), g.get("p_away")
    _pre = not sim.get("live")
    add("ML", f"{g.get('home_name', ha)} to win", lambda i: hwin[i],
        model=round(ph * 100, 1) if ph is not None else None,
        kref={"t": "ml", "team": ha}, avg=mean_margin, unit="run margin",
        marg_override=ph if (_pre and ph) else None)
    add("ML", f"{g.get('away_name', aa)} to win", lambda i: not hwin[i],
        model=round(pa * 100, 1) if pa is not None else None,
        kref={"t": "ml", "team": aa}, avg=round(-mean_margin, 1), unit="run margin",
        marg_override=pa if (_pre and pa) else None)
    # Run line -- Kalshi's adjustable "win by X+" for each side. analyze_slate has
    # trimmed the spread ladders to the margins Kalshi books, so iterate those (with
    # a sane 2/3 fallback for a bare game dict). The marginal filter below still
    # drops any margin too unlikely to be useful.
    rl = props.get("run_line") or {}
    home_by, away_by = rl.get("home_by") or {}, rl.get("away_by") or {}
    def _margins(by_map):
        ms = sorted(int(k) for k in by_map) if by_map else [2, 3]
        return ms
    import combo_engine as _ce
    for mgn in _margins(home_by):
        add("Run line", _ce.spread_label(ha, mgn, "runs"),
            lambda i, m=mgn: hr_runs[i] - ar_runs[i] >= m,
            model=home_by.get(str(mgn)), kref={"t": "spread", "team": ha, "by": mgn},
            avg=mean_margin, unit="run margin")
    for mgn in _margins(away_by):
        add("Run line", _ce.spread_label(aa, mgn, "runs"),
            lambda i, m=mgn: ar_runs[i] - hr_runs[i] >= m,
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
    # RFI -- a run in the 1st inning (either team). Kalshi quotes BOTH sides
    # (verified against the live rules text), so the NO leg comes from
    # _no_candidates like every other prop; only the YES is added here. The
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
            # RBI -- Kalshi books it (KXMLBRBI, "Name: N+") and the sim has
            # tracked per-batter RBI all along because HRR needs it; the market
            # was simply never offered. No closed form, like HRR/SB.
            a_rbi = round(_mean(rbi), 2)
            for m in (1, 2, 3):
                add("RBI", f"{nm} {m}+ RBIs", lambda i, a=rbi, m=m: a[i] >= m, grp,
                    None, {"t": "rbi", "player": nm, "line": m}, avg=a_rbi, unit="RBI")
            # Stolen bases -- the base-out engine attempts steals off each runner's
            # real speed/steal rate, so SB props read straight off the sim. The
            # marginal filter naturally keeps this to players who actually run.
            sb = st["sb"]
            a_sb = round(_mean(sb), 2)
            for m in (1, 2):
                add("SB", f"{nm} {m}+ stolen bases", lambda i, a=sb, m=m: a[i] >= m, grp,
                    None, {"t": "sb", "player": nm, "line": m}, avg=a_sb, unit="SB")
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
    cands.extend(_no_candidates(cands, n))
    return cands


# Markets that already carry their own other side, so a NO leg would just
# duplicate one: the moneyline pairs both teams, and Under IS the NO of Over.
#
# RFI used to sit here too, on the stated grounds that "Kalshi lists RFI as a
# YES-only market". That was simply not true: every open RFI market quotes both
# sides, our own index has been storing the no ask for all 38 of them, and
# price_leg has always been able to resolve it. So "no run in the 1st" was a
# fully priced, two-sided leg the maker refused to offer on every game of every
# slate. It is a wide market (a ~23c spread), which the blend already handles by
# leaning on the market -- that is a reason to price it carefully, not to hide it.
_NO_SKIP_TYPES = {"ML", "Total"}

# A NO leg is only worth offering where it is a real position. The YES floor goes
# down to 4% because a longshot is a legitimate payout play, but the mirror of one
# ("NO 6+ H+R+RBI", 96%) is padding: it can't lose, so it adds no edge and only
# inflates a slip's headline confidence. Keep NO to a band either side of a coin
# flip -- a genuine fade, or a genuine longshot fade.
_NO_MIN, _NO_MAX = 0.10, 0.90


def _no_candidates(cands, n):
    """The NO side of each eligible leg — betting a player DOESN'T get there.

    The mask is the exact complement, so a NO leg's correlation with every other
    leg falls straight out of the same simulation (fading a bat and taking the
    under really are correlated, and this gets that for free instead of assuming
    independence). The marginal is 1 minus the CALIBRATED yes marginal, not the
    raw complement, so a leg and its negation always sum to 1.
    """
    full = (1 << n) - 1
    out = []
    for c in cands:
        if c["type"] in _NO_SKIP_TYPES:
            continue
        marg = 1.0 - c["marg"]
        if not (_NO_MIN <= marg <= _NO_MAX):
            continue
        kref = c.get("kref")
        kref = dict(kref, no=True) if kref else None
        model = c.get("model_pct")
        out.append({**c, "label": f"NO - {c['label']}", "mask": (~c["mask"]) & full,
                    "marg": marg, "side": "no", "kref": kref,
                    "model_pct": round(100.0 - model, 1) if model is not None else None})
    return out


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


def _ladder_dir(c):
    """Which way a ladder leg points -- the team for a moneyline or spread,
    over/under for a total, with the NO side inverted. None for anything that is
    not a ladder leg (player props, and anything built without a Kalshi ref)."""
    k = c.get("kref") or {}
    t, no = k.get("t"), bool(k.get("no"))
    if t == "total":
        return "over" if bool(k.get("over")) != no else "under"
    if t in ("ml", "spread"):
        return ("not:" if no else "") + str(k.get("team") or "").upper()
    return None


def _market_conflict(combo):
    """True if a parlay stacks two legs from the same market group -- two game
    totals on the same side, two run-line margins, two moneylines, or two props
    on the SAME player/pitcher (those are one correlated market, not independent
    picks).

    OPPOSITE ENDS OF ONE LADDER ARE THE EXCEPTION. "Over 18.5 and Under 45.5" is
    a band on where the total lands, which is a real two-leg bet and not two
    bites at one pick -- and on a board whose entire slate is three team markets
    (an NFL preseason game has no player props at all) it is the only way a
    same-game stack reaches three legs. Same-side pairs stay barred, and the pair
    is still put through _redundant (nested lines) and the counteract filter (a
    band tight enough that its legs fight each other), so what survives is a wide
    band and nothing else."""
    seen = {}
    for c in combo:
        # dict.get evaluates its default EAGERLY, so c.get("group", c["type"])
        # raised KeyError on any candidate without a "type" even when it had a
        # perfectly good "group". Every candidate build_candidates emits happens
        # to carry both, so this never fired -- but it made the conflict check
        # crash-prone for anything constructed anywhere else.
        g = c.get("group") or c.get("type")
        d = _ladder_dir(c)
        prev = seen.get(g)
        if prev is None:
            seen[g] = [d]
            continue
        if d is None or any(p is None or p == d for p in prev):
            return True
        prev.append(d)
    return False


def _span(items, m):
    """m entries spanning the probability range, not just the m likeliest."""
    xs = sorted(items, key=lambda x: -x["marg"])
    if m <= 0 or not xs:
        return []
    if m >= len(xs):
        return xs
    step = (len(xs) - 1) / (m - 1) if m > 1 else 0
    picked, seen = [], set()
    for i in range(m):
        j = int(round(i * step))
        if j not in seen:
            seen.add(j)
            picked.append(xs[j])
    return picked


def _pool(cands, k=22):
    """Trim the candidate set for the combinatorial search: at most two lines per
    market SIDE (a safe one + an aggressive one), capped to k, so the search stays
    fast while still spanning safe favorites and longer-shot payouts.

    YES and NO bucket separately even though they share a `group` (which still
    stops a parlay taking both). Pooled together, a player's likeliest leg is
    always the NO of his longest shot — "NO 4+ hits" would evict "1+ hits" from
    the board. NO also gets a reserved share of k, chosen across the probability
    range: ranking the whole pool by probability would keep only the near-certain
    NOs and drop the contrarian fades that are the reason to bet NO at all.

    Two per bucket is the right trim when there are DOZENS of buckets, which is
    baseball's shape -- a group per player per stat. It starves a board whose
    whole slate is three team markets: an NFL preseason game has no player props,
    so ML/Spread/Total is everything, and a flat two-per-bucket trim handed the
    search a pool of three legs to build a parlay out of. The per-bucket
    allowance therefore scales to how many buckets there are, which leaves a wide
    board exactly as it was and gives a narrow one the ladder width a band needs.
    """
    by_bucket = {}
    for c in sorted(cands, key=lambda x: -x["marg"]):
        # `c.get("group", c["type"])` evaluates the default EAGERLY, so a
        # candidate without a "type" raised KeyError even when it had a perfectly
        # good "group". Same bug as _market_conflict, which was fixed; this copy
        # was missed.
        by_bucket.setdefault((c.get("group") or c.get("type"),
                              c.get("side", "yes")), []).append(c)
    per = max(2, -(-k // max(1, len(by_bucket))))
    picks = {"yes": [], "no": []}
    for (_grp, side), cs in by_bucket.items():
        p = picks.get(side)
        if p is None:
            p = picks.setdefault(side, [])
        # _span, not the top few: on a ladder the useful width runs across the
        # LINES, and probability-ranking a total ladder keeps only one end of it.
        p.extend(_span(cs, min(per, len(cs))))
    no_slots = min(len(picks["no"]), max(2, k // 3)) if picks["no"] else 0
    take_no = _span(picks["no"], no_slots)
    take_yes = sorted(picks["yes"], key=lambda x: -x["marg"])[:max(0, k - len(take_no))]
    return sorted(take_yes + take_no, key=lambda x: -x["marg"])


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
    # build_candidates CALIBRATES each leg's marginal against our graded record
    # (temperature scaling on the win model and batter props), but a joint read
    # off the sim masks is the RAW frequency. Mixing the two made a slip's
    # headline probability disagree with its own legs by ~3pp -- always in the
    # optimistic direction -- and left corr_delta (joint - indep) reporting the
    # calibration gap as if it were correlation, sometimes with the wrong sign:
    # legs that were genuinely +2pp correlated displayed as -0.5pp.
    # Rescaling the joint by the product of the per-leg calibration ratios keeps
    # the sim's dependence structure intact while putting the joint on the same
    # scale as the marginals it is quoted beside.
    margs = [c["marg"] for c in cands]
    ratios = []
    for c in cands:
        raw = _popcount(c["mask"]) / n
        ratios.append((c["marg"] / raw) if raw > 0 else 1.0)
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
            # Onto the calibrated scale, capped by the smallest marginal (a joint
            # can never beat its least likely leg). Done here, not at the end, so
            # the confidence/payout target is judged on the number we report.
            cal, cap = 1.0, 1.0
            for i in idxs:
                cal *= ratios[i]
                if margs[i] < cap:
                    cap = margs[i]
            joint = min(joint * cal, cap)
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
                  "side": c.get("side", "yes"),
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

# How many leg combinations one game may enumerate. The bundle search is a
# subset scan, so DEPTH is the exponent and the pool is not: at depth 4 a pool of
# 26 costs fewer combinations than a pool of 14 explored to the bottom. That is
# the whole trade this budget exists to make -- spend it on WIDTH, which is what
# finds a correlating pair, rather than on 12-leg same-game stacks nobody bets.
#
#     pool 14, every depth   16,383      <- what this used to do
#     pool 20, depth 5       21,699
#     pool 26, depth 4       17,902
#     pool 30, depth 3        4,525
#
# Measured at roughly 12us a combination (the cost is popcounting 5,000-bit masks),
# so 25,000 is about 0.3s per game against a full 15-game build of ~27s.
_STACK_BUDGET = 25000
_POOL_MIN, _POOL_MAX = 14, 30


def _pool_for(depth):
    """Widest candidate pool that can be searched to `depth` inside the budget.

    Deep stacks force a narrow pool and shallow ones buy a wide one, automatically
    -- so a thin slate that genuinely needs an 8-leg single-game bundle still gets
    it, at the old width, while the ordinary 3-5 leg ask gets twice the legs to
    correlate across."""
    best = _POOL_MIN
    for k in range(_POOL_MIN, _POOL_MAX + 1):
        tot = sum(math.comb(k, sz) for sz in range(1, min(k, depth) + 1))
        if tot > _STACK_BUDGET:
            break
        best = k
    return best


def game_bundles(cands, n, max_legs=3, per_size=6):
    """Non-redundant leg bundles (size 1..max_legs) for one game, each with its
    simulated joint probability. Trimmed to the most useful per size: the safest
    few (high prob) and the longest-shot few (high payout, to reach a target)."""
    cs = _pool(cands, _pool_for(max_legs))
    phi = _corr_matrix(cs, n)
    # Same calibrated-marginal / raw-joint mismatch as best_same_game -- and it
    # compounds here, because a mixed parlay multiplies one bundle per game. Even
    # a SIZE-1 bundle needs this: its raw mask frequency is not the calibrated
    # marginal the leg is displayed with.
    margs = [c["marg"] for c in cs]
    ratios = []
    for c in cs:
        raw = _popcount(c["mask"]) / n
        ratios.append((c["marg"] / raw) if raw > 0 else 1.0)
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
            cal, cap = 1.0, 1.0
            for i in idxs:
                cal *= ratios[i]
                if margs[i] < cap:
                    cap = margs[i]
            joint = min(joint * cal, cap)
            if joint <= 0.005:
                continue
            # How much the stack beats treating its legs as independent. This is
            # the whole point of a same-game bundle, and it is NOT what sorting by
            # joint probability finds -- the safest bundles are usually the ones
            # whose legs barely interact.
            ind = 1.0
            for i in idxs:
                ind *= margs[i]
            sized.append((joint, combo, joint - ind))
        sized.sort(key=lambda x: x[0], reverse=True)
        # Safest few, longest-shot few, and -- added because widening the pool did
        # nothing without it -- the few that CORRELATE best. A bigger pool finds
        # more complementary pairs and then a probability-ranked trim throws every
        # one of them away.
        keep = sized[:per_size] + sized[-per_size:]
        if sz > 1:
            keep += sorted(sized, key=lambda x: x[2], reverse=True)[:per_size]
        seen = set()
        for joint, combo, _dlt in keep:
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
                         "sim_avg": c.get("sim_avg"), "avg_unit": c.get("avg_unit"),
                         "side": c.get("side", "yes"),
                         # Pre-blend sim number and how much of the final
                         # probability is still ours, so a leg where the market
                         # overruled the model is visible rather than silent.
                         "sim_pct": (round(c["marg_model"] * 100, 1)
                                     if c.get("marg_model") is not None else None),
                         "model_weight": c.get("model_weight"),
                         "market_quality": c.get("market_quality"),
                         # The leg's live Kalshi ask, and whether it can actually
                         # be filled. The slip has always SAID "a ¢ price means
                         # it's a live Kalshi market" and never carried one --
                         # the candidate had price_cents throughout and the item
                         # dropped it, so every leg rendered priceless in both
                         # sports. It matters most where a slate is half-quoted:
                         # NFL preseason week 2 lists moneylines only, so a slip
                         # mixes placeable legs with model-only ones and nothing
                         # on screen said which was which.
                         "market_cents": c.get("price_cents"),
                         "fillable": c.get("fillable")})
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
