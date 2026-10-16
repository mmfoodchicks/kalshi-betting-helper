"""Team unit profiles for the NFL deep engine -- the football twin of deep_data.

A profile is what nfl_deep_sim plays with: the offence's and the defence's
rates in league units (pass rate, completion %, yards per completion, sack and
interception rates, yards per carry, fumbles, penalties, pace), the kicker,
the punt and kick-return units, and a LEVEL multiplier that anchors the
team's overall strength.

WHERE THE RATES COME FROM. Every finished game of the season, from the
play-by-play (nfl_pbp.team_units), shrunk toward a prior with the K the fit
measured for each stat -- the number of games of evidence it takes before a
team's own rate counts as much as the prior (yards per play 7.3, completions
63, sacks 15.5, interceptions 14, yards per carry 6.8, penalties 12, pass rate
4.1; research/nfl_deep_fit.py). The prior is the league mean plus the part of
LAST season's deviation that survives a year, also measured (the persistence
slopes: an offence keeps ~0.42 of its yards-per-play edge, a defence ~0.16;
interceptions, fumbles, punting and returns keep nothing, so their prior is
the league). Before week 1 the profile IS that prior; by mid-season the games
have taken over for the fast stats and the slow ones still lean on the prior.

THE LEVEL. Unit rates say how a team moves the ball; they do not know what
the market knows about the roster. The anchor is the one nfl_season already
trusts -- Kalshi's win-total ladder (or the roster-aware ratings when the
ladder is thin), as points of margin against an average team -- blended with
the engine's own read at the weight nfl_season._ratings gives the prior
against live results, 6 / (6 + games). The difference becomes a yardage
multiplier through the measured points-per-yard elasticity (pts ~ ypp^1.45),
half on the offence and half on the defence. The season job reports each
team's realized margin so the anchor's residual is visible, never assumed.

Rosters this week: Sleeper's team projection for the week against the team's
own season average is the only roster signal the codebase has for football
(a quarterback out moves it directly). It is applied to the current week's
games only, at half weight -- a judgment, stated as one.
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
_ANCHOR_PRIOR_GAMES = 6.0        # nfl_season._ratings' weight on the prior
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
            apply_anchor(profs, season, T)
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


def market_margins(season):
    """{abbr: points of margin vs average} from Kalshi's win totals, else the
    roster-aware ratings nfl_season already uses."""
    import nfl_season
    mkt = nfl_season._market_wins()
    src = "kalshi_win_totals"
    if len(mkt) < 24:
        mkt = nfl_season._ratings(str(season))
        src = "nfl_season_ratings"
    return {ab: (w - 8.5) * nfl_season._PTS_PER_WIN for ab, w in mkt.items()}, src


def apply_anchor(profs, season, T):
    """Set each profile's `level` so the team's margin blends the market's read
    with the engine's at 6 / (6 + games) on the market."""
    L = _lg(T)
    elast = (T.raw.get("pts_vs_ypp") or [0, 1.45])[1]
    pts = L["pts_pg"]
    mm, src = market_margins(season)
    for ab, prof in profs.items():
        own = engine_margin(prof, T)
        if ab not in mm:
            prof["anchor"] = {"source": None, "engine_margin": round(own, 2)}
            continue
        g = prof.get("g") or 0
        w = _ANCHOR_PRIOR_GAMES / (_ANCHOR_PRIOR_GAMES + g)
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
    offence's level (the defence's level is left alone: Sleeper projects
    offences)."""
    out = {}
    for ab, p in profs.items():
        q = dict(p)
        f = factors.get(ab)
        if f:
            q = dict(p)
            q["off"] = dict(p["off"])
            q["roster_factor"] = f
            # the level multiplies yards; a roster factor on points maps through
            # the same elasticity
            q["level"] = round((p.get("level") or 1.0) * (f ** (1.0 / 1.45)), 4)
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
