"""Team unit profiles for the NFL deep engine -- the football twin of deep_data.

A profile is what nfl_deep_sim plays with: the offence's and the defence's
rates in league units (pass rate, completion %, yards per completion, sack and
interception rates, yards per carry, fumbles, penalties, pace), the kicker,
the punt and kick-return units, and a LEVEL multiplier that anchors the
team's overall strength.

WHERE THE RATES COME FROM. Every finished game of the season, from the
play-by-play (nfl_pbp.team_units), shrunk toward a prior with the K the fit
measured for each stat -- the number of games of evidence it takes before a
team's own rate counts as much as the prior, the median over the three full
seasons 2023-2025 (yards per play 5.9, completions 11.5, sacks 7.9,
interceptions 26, yards per carry 22, pass rate 8.7, penalties 251 -- capped at
60 here; research/nfl_deep_fit.py, every season's value in the tables). The prior is the league mean plus the part of
LAST season's deviation that survives a year, also measured (the persistence
slopes: an offence keeps ~0.42 of its yards-per-play edge, a defence ~0.16;
interceptions, fumbles, punting and returns keep nothing, so their prior is
the league). Before week 1 the profile IS that prior; by mid-season the games
have taken over for the fast stats and the slow ones still lean on the prior.

THE LEVEL. Unit rates say how a team moves the ball; they do not know what
the market knows about the roster. The anchor is the one nfl_season already
trusts -- Kalshi's win-total ladder (or the roster-aware ratings when the
ladder is thin) -- converted to the market's REST-OF-SEASON strength
(nfl_season.market_pace: the ladder prices the whole season, banked wins
included) and then to points of margin against an average team, blended with
the engine's own read at the weight the validation supports -- the whole level
(see _ANCHOR_PRIOR_GAMES). The difference becomes a yardage multiplier through the
ENGINE's measured points elasticity (_ENGINE_PTS_ELASTICITY), half on the
offence and half on the defence. The team view carries the anchor record
(source, engine read, market read, weight, target) so the residual is visible.

Rosters this week: Sleeper's team projection for the week against the team's
own season average is the only roster signal the codebase has for football
(a quarterback out moves it directly). It is applied to the current week's
games only, at half weight, to the OFFENCE's level alone -- a judgment, stated
as one.
"""
import math

import errlog
import nfl_pbp as P
import nfl_deep_sim as E

# Shrinkage, in games, for the stats the fit did not measure a K for.
_K_FALLBACK = {"sec_per_snap": 4.0}
# Kicker, punter, returners: shrinkage in kicks. Year-over-year persistence
# of punting and returns measured ~0 (slopes -0.08 / -0.05), so their prior
# is the league and the season's own kicks have to earn the deviation.
_K_FG_ATT = 30.0
_K_PUNTS = 40.0
_K_KO = 40.0
# Last season counts this much of a full season for the kicker (judgment:
# kickers persist, but a 60-kick season is a small sample either way).
_PREV_FG_WEIGHT = 0.5
# The market's weight in the level: None = the WHOLE level (weight 1), a
# number k = k / (k + games). Full weight is what the out-of-sample walk-forward
# supports (research/reports/nfl_deep_validation.md, 2024-25, 512 games, the
# points-differential ratings standing in for Kalshi): anchored at weight 1 the
# engine's log loss is 0.6317 against 0.6418 at 6/(6+g) (paired -0.0101 +/-
# 0.0073; better in each season on its own, 2024 0.6216 v 0.6327, 2025 0.6418 v
# 0.6509) and level with the PD ratings themselves (0.6331; -0.0014 +/- 0.0045),
# with calibration closer and a margin slope of 0.88 against 1.39; the unit
# model's own level read is the weakest part of the engine (alone 0.660,
# margins compressed: slope 1.87). So
# the market sets the level and the engine supplies what a rating cannot -- both
# teams' score shapes, totals, spreads and the matchup-dependent spread of
# outcomes. (The first version used nfl_season's 6/(6+g), a prior-vs-live blend,
# on a market that is already a live posterior.)
_ANCHOR_PRIOR_GAMES = None
# The engine's own d ln(points) / d ln(level), measured on the engine with the
# unbiased yardage rounding (league-average teams, 12,000 neutral games a point,
# review_opus/r4b_level_slopes.py): 1.42 / 1.41 for the side whose yards move,
# against the 1.45 measured on real team-games (pts ~ ypp^1.45) -- the engine and
# the real game agree. With the old int(round()) rounding the same level moved
# the margin by 9-72% of this.
_ENGINE_PTS_ELASTICITY = 1.42
_ROSTER_WEIGHT = 0.5
_ROSTER_CLAMP = (0.85, 1.12)
_LEVEL_CLAMP = (0.80, 1.25)


def _lg(T):
    return T.league


def _persist(T, side, stat):
    rec = (T.raw.get("persistence") or {}).get(f"{side}.{stat}") or {}
    s = rec.get("slope")
    if s is None:
        return 0.0
    return min(0.6, max(0.0, s))


def _k(T, stat):
    m = (T.raw.get("matchup") or {}).get(stat) or {}
    k = m.get("K_games")
    if k is None:
        k = _K_FALLBACK.get(stat, 10.0)
    return min(60.0, max(1.0, float(k)))     # a K of 147,000 (fumbles) means "league"


def _unit_profile(T, side, cur, prev, g):
    """Shrunk rates for one unit. `cur`/`prev` are unit count dicts (or None)."""
    L = _lg(T)
    lg_key = {"ypcomp": "ypc_pass"}
    rc = P.unit_rates(cur) if cur else {}
    rp = P.unit_rates(prev) if prev else {}
    out = {}
    for stat in E.PROFILE_KEYS:
        lgv = L[lg_key.get(stat, stat)]
        prior = lgv
        pv = rp.get(stat)
        if pv is not None:
            prior = lgv + _persist(T, side, stat) * (pv - lgv)
        cv = rc.get(stat)
        if cv is None or g <= 0:
            out[stat] = prior
        else:
            k = _k(T, stat)
            if stat == "fum":
                out[stat] = lgv              # nothing carries; the fit's K is 147,000 games
            else:
                out[stat] = (g * cv + k * prior) / (g + k)
    return out


def _fg_shift(T, cur, prev):
    """Kicker quality as a log-odds shift against the league curve, from the
    team's own attempts by distance (this season, plus half-weight last)."""
    bins = {}
    for src, w in ((cur, 1.0), (prev, _PREV_FG_WEIGHT)):
        for kd, (made, att) in ((src or {}).get("fg") or {}).items():
            cell = bins.setdefault(int(kd), [0.0, 0.0])
            cell[0] += made * w
            cell[1] += att * w
    exp_made = made_t = att_t = 0.0
    for kd, (made, att) in bins.items():
        p = E._sig(T.fg_a + T.fg_b * (kd + 2.5))
        exp_made += p * att
        made_t += made
        att_t += att
    if att_t <= 0:
        return 0.0
    eps = 0.5
    obs = math.log((made_t + eps) / (att_t - made_t + eps))
    exp = math.log((exp_made + eps) / (att_t - exp_made + eps))
    return round((obs - exp) * att_t / (att_t + _K_FG_ATT), 4)


def _shift(cur_pair, league_mean, k):
    """(n, sum) -> shrunk deviation of the mean from the league."""
    n, s = cur_pair or (0, 0)
    if not n or league_mean is None:
        return 0.0
    return round((s / n - league_mean) * n / (n + k), 3)


def build_profiles(season, store=None, T=None, anchor=True, log=None):
    """{abbr: profile} for all 32 teams. `store` is an nfl_pbp season store
    (fetched when None). Returns (profiles, quality)."""
    import nfl_season
    T = T or E.tables()
    L = _lg(T)
    if store is None:
        store = P.season_records(int(season), log=log)
    cur = P.team_units(store, seasontype=2)
    prior_rec = T.raw.get("team_prior") or {}
    prev = prior_rec.get("units") if prior_rec.get("season") == int(season) - 1 else None
    if prev is None and log:
        log(f"[nfl-deep] no prior-season units for {season} (tables carry {prior_rec.get('season')})")
    profs = {}
    with_games = 0
    for ab in nfl_season._DIV_OF:
        c = cur.get(ab) or {}
        pv = (prev or {}).get(ab) or {}
        g = int(c.get("g") or 0)
        with_games += g > 0
        prof = {"abbr": ab, "name": nfl_season._team_name(ab), "g": g,
                "pf": c.get("pf", 0), "pa": c.get("pa", 0),
                "off": _unit_profile(T, "off", c.get("off"), pv.get("off"), g),
                "def": _unit_profile(T, "def", c.get("def"), pv.get("def"), g),
                "st": {"fg_shift": _fg_shift(T, c.get("off"), pv.get("off")),
                       "xp": T.xp,
                       "punt_shift": _shift((c.get("off") or {}).get("punt"), L.get("punt_net_ytg"), _K_PUNTS),
                       # the engine subtracts: positive = the return unit leaves less to go
                       "ko_shift": -_shift((c.get("off") or {}).get("ko_ret"), L.get("ko_ret_ytg"), _K_KO)},
                "level": 1.0, "anchor": None}
        profs[ab] = prof
    done, held = P.coverage(store)
    quality = {"teams": len(profs), "teams_with_games": with_games, "pbp_games_finished": done,
               "pbp_games_held": held, "coverage": round(held / done, 3) if done else 0.0,
               "prior_season": bool(prev), "season": int(season)}
    if anchor:
        try:
            apply_anchor(profs, season, T, recs=P.records(store, set(nfl_season._DIV_OF)))
            quality["anchor"] = "applied"
        except Exception as e:
            errlog.note("NFLDD-anchor", e)
            quality["anchor"] = f"failed: {type(e).__name__}"
    return profs, quality


def engine_ypp(unit):
    """Approximate yards per play implied by a unit's rates (sack yards at the
    league's -6.2)."""
    pr = unit["pass_rate"]
    return pr * ((1 - unit["sack"]) * unit["comp"] * unit["ypcomp"] - unit["sack"] * 6.2) + (1 - pr) * unit["ypc"]


def engine_margin(prof, T):
    """The engine's own read of a team's margin against an average team, from
    its units through the measured points-yards elasticity. Approximate (it
    leaves turnovers to the league rate); the season job reports the realized
    figure beside it."""
    L = _lg(T)
    elast = (T.raw.get("pts_vs_ypp") or [0, 1.45])[1]
    lg_unit = E.league_profile(T)["off"]
    ypp_lg = engine_ypp(lg_unit)
    pts = L["pts_pg"]
    off = pts * ((engine_ypp(prof["off"]) / ypp_lg) ** elast - 1.0)
    dfn = pts * ((engine_ypp(prof["def"]) / ypp_lg) ** elast - 1.0)
    return off - dfn


def market_margins(season, recs=None):
    """{abbr: points of margin vs average, for the games LEFT} from Kalshi's win
    totals converted to rest-of-season pace with the banked records `recs`
    ({abbr: (w, l, t, games_left)}), else the roster-aware ratings nfl_season
    already uses (which carry the same conversion)."""
    import nfl_season
    mkt = nfl_season._market_wins()
    src = "kalshi_win_totals_rest_of_season"
    if len(mkt) >= 24:
        mkt = nfl_season.market_pace(mkt, recs or {})
    else:
        mkt = nfl_season._ratings(str(season))
        src = "nfl_season_ratings"
    return {ab: (w - 8.5) * nfl_season._PTS_PER_WIN for ab, w in mkt.items()}, src


def apply_anchor(profs, season, T, recs=None):
    """Set each profile's `level` so the team's margin is the market's
    rest-of-season read (blended with the engine's own when _ANCHOR_PRIOR_GAMES
    is a number)."""
    L = _lg(T)
    elast = _ENGINE_PTS_ELASTICITY
    pts = L["pts_pg"]
    mm, src = market_margins(season, recs)
    for ab, prof in profs.items():
        own = engine_margin(prof, T)
        if ab not in mm:
            prof["anchor"] = {"source": None, "engine_margin": round(own, 2)}
            continue
        g = prof.get("g") or 0
        w = 1.0 if _ANCHOR_PRIOR_GAMES is None else _ANCHOR_PRIOR_GAMES / (_ANCHOR_PRIOR_GAMES + g)
        target = w * mm[ab] + (1 - w) * own
        delta = target - own
        lvl = (1.0 + (delta / 2.0) / pts) ** (1.0 / elast) if pts > 0 else 1.0
        lvl = min(_LEVEL_CLAMP[1], max(_LEVEL_CLAMP[0], lvl))
        prof["level"] = round(lvl, 4)
        prof["anchor"] = {"source": src, "market_margin": round(mm[ab], 2),
                          "engine_margin": round(own, 2), "weight_market": round(w, 3),
                          "target_margin": round(target, 2)}


def roster_factors(season, week):
    """{abbr: this week's Sleeper team projection / the team's season-average
    projection}, shrunk by _ROSTER_WEIGHT and clamped. Empty when Sleeper is
    dark."""
    import nfl_data
    out = {}
    try:
        this = nfl_data.week_teams(str(season), int(week)) or {}
    except Exception as e:
        errlog.note("NFLDD-roster-week", e)
        return out
    if not this:
        return out
    sums, cnts = {}, {}
    for w in range(1, 19):
        try:
            wk = nfl_data.week_teams(str(season), w) or {}
        except Exception as e:
            errlog.note("NFLDD-roster-season", e)
            continue
        for ab, t in wk.items():
            if (t.get("points") or 0) > 5:
                sums[ab] = sums.get(ab, 0.0) + t["points"]
                cnts[ab] = cnts.get(ab, 0) + 1
    for ab, t in this.items():
        if cnts.get(ab, 0) >= 4 and (t.get("points") or 0) > 5:
            ratio = t["points"] / (sums[ab] / cnts[ab])
            f = 1.0 + _ROSTER_WEIGHT * (ratio - 1.0)
            out[ab] = round(min(_ROSTER_CLAMP[1], max(_ROSTER_CLAMP[0], f)), 4)
    return out


def with_roster(profs, factors):
    """Copies of the profiles with this week's roster factor folded into the
    OFFENCE's level only (Sleeper projects offences). It used to multiply the
    team's single `level`, which the engine applies to both sides: a 0.88
    factor (a starter out) cut the team's points 12.1% as intended AND raised
    its opponent's 10.9% (review_opus/r4_level.py), doubling the effect."""
    out = {}
    for ab, p in profs.items():
        q = dict(p)
        f = factors.get(ab)
        if f:
            base = p.get("level") or 1.0
            q["roster_factor"] = f
            q["level_def"] = p.get("level_def") or base
            q["level_off"] = round((p.get("level_off") or base) * (f ** (1.0 / _ENGINE_PTS_ELASTICITY)), 4)
        out[ab] = q
    return out


def summary(prof, T=None):
    """One team's units beside the league, for the team view."""
    T = T or E.tables()
    L = _lg(T)
    lg_key = {"ypcomp": "ypc_pass"}
    rows = []
    for stat in E.PROFILE_KEYS:
        lgv = L[lg_key.get(stat, stat)]
        rows.append({"stat": stat, "off": round(prof["off"][stat], 4), "def": round(prof["def"][stat], 4),
                     "league": round(lgv, 4)})
    return {"abbr": prof["abbr"], "name": prof.get("name"), "games": prof.get("g"),
            "level": prof.get("level"), "anchor": prof.get("anchor"), "st": prof.get("st"),
            "units": rows, "engine_margin": round(engine_margin(prof, T), 2)}
