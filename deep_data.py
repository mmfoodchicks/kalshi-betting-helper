"""Per-team player profiles for the deep pitch-by-pitch season engine.

Pulls each club's active roster with season stat lines (two hydrated calls per
team, cached) and turns them into the inputs the engine actually needs:

  - rotation:  starters (per-PA K / BB / HR / hit rates, hand)
  - bullpen:   relievers, ranked best-arm-last so the closer is held back
  - lineup:    the nine regulars (top position players by plate appearances)
  - bench:     everyone else available to pinch-hit / pinch-run

All rates are per plate appearance so the engine can sample an at-bat directly.
Future games have no posted lineup, so the engine assumes the rotation cycles
and the regulars play — the "assumed rotations" path the design calls for.
"""

import baseball
import clock
import errlog

STATS = "https://statsapi.mlb.com/api/v1"
# League-average per-PA outcome rates — fallback for thin samples + the baseline
# the log5 combination regresses pitcher/batter toward.
LG = {"k": 0.225, "bb": 0.085, "hbp": 0.011, "hr": 0.032,
      "1b": 0.142, "2b": 0.046, "3b": 0.004}
PA_PER_9 = 38.0   # ~ batters faced per 9 innings, to convert /9 rates to per-PA


def _f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


# Short-IL availability: fraction of the rest of the season a player on each IL
# is expected to be available (they miss ~the next couple of weeks, then return).
# 60-day is treated as out for the season; the nightly rerun re-checks all of this.
SHORT_IL = {"D7": 0.93, "D10": 0.88, "D15": 0.82,
            # Not injuries at all, but they land here for the same reason: the
            # player is briefly unavailable and then back. Paternity is capped at
            # 3 days by rule and bereavement at 7, so they cost almost nothing
            # over a rest-of-season horizon. They were being treated like a
            # 60-day IL and dropped for the year.
            "PL": 0.99, "BRV": 0.98}


def _is_il(code):
    """True if a roster status code is any flavor of injured list (D7/D10/D15/D60,
    DL)."""
    if not code:
        return False
    if code in ("DL",):
        return True
    return code[0] == "D" and code[1:].isdigit()   # D7 / D10 / D15 / D60


def _roster_stats(team_id, season, group):
    """{player_id: (person, position, season_stat, career_stat, status_code)} for
    one stat group. Pulls the 40-man so we can see IL + minor-league status, and
    hydrates BOTH this season and career so the engine can regress small samples
    toward a real talent baseline."""
    def fetch():
        url = (f"{STATS}/teams/{team_id}/roster?rosterType=40Man"
               f"&hydrate=person(stats(type=[season,career],group=[{group}],season={season}))")
        d = baseball._get(url)
        out = {}
        for r in d.get("roster", []):
            per = r.get("person", {})
            season_st = career_st = None
            for s in (per.get("stats") or []):
                sp = s.get("splits") or []
                if not sp:
                    continue
                tp = (s.get("type") or {}).get("displayName")
                if tp == "career":
                    career_st = sp[0].get("stat")
                elif tp == "season" or season_st is None:
                    season_st = sp[0].get("stat")
            out[per["id"]] = (per, r.get("position", {}), season_st, career_st,
                              (r.get("status") or {}).get("code", "A"))
        return out
    return baseball._cached(("deep_roster40c", team_id, season, group), 21600, fetch)


# --- Minor-league translation -------------------------------------------------
# A player optioned to the minors ("RM") is the club's call-up pool, and most of
# that pool has no MLB line to project from. Measured on 8 real 40-man rosters,
# only 29% of optioned HITTERS and 49% of optioned pitchers carried MLB season
# stats -- the other 60%/44% had none at all, and a further 11%/7% had a career
# book but no current season. All of them were dropped, so the taxi squad the
# engine reaches for was a third of its real size and skewed toward shuttle
# veterans over actual prospects.
#
# Career-only players need no translation: _batter/_pitcher already shrink an
# empty season toward the career prior, so they just had to stop being filtered
# out. Players with ONLY minor-league stats need their rates translated, because
# Triple-A production is not major-league production.
#
# MEASURED, not assumed. 127 hitters logged 80+ PA in BOTH MLB and Triple-A in
# 2025 -- which is exactly the shuttle population this pool models, so it is the
# right sample rather than a biased one:
#
#     stat      MLB/PA    AAA/PA    MLB/AAA
#     hits      .2021     .2394      0.844
#     HR        .0237     .0345      0.687
#     BB        .0764     .1159      0.659
#     K         .2510     .2083      1.205   <- strikeouts go UP
#     OPS       .6465     .8393      0.770
#
# Levels below Triple-A are progressively harsher. Those are stepped down from the
# measured AAA figures rather than measured directly: the same-season two-level
# sample thins out fast below AAA (few players yo-yo between Double-A and the
# majors in one year), so a fitted number there would be noise wearing a decimal
# point.
_MLE = {
    11: {"hit": 0.844, "hr": 0.687, "bb": 0.659, "k": 1.205},      # Triple-A
    12: {"hit": 0.800, "hr": 0.590, "bb": 0.620, "k": 1.320},      # Double-A
    13: {"hit": 0.760, "hr": 0.520, "bb": 0.590, "k": 1.420},      # High-A
    14: {"hit": 0.720, "hr": 0.460, "bb": 0.560, "k": 1.520},      # Single-A
}
_MLE_LEVELS = (11, 12, 13, 14)

# The same translation for ARMS, measured the same way: 154 pitchers threw 20+ IP
# in both MLB and Triple-A in 2025.
#
#     stat      MLB/BF    AAA/BF    MLB/AAA
#     K         .2119     .2495      0.849    <- strikeouts DROP
#     BB        .0881     .0936      0.942
#     H         .2245     .2130      1.054
#     HR        .0339     .0233      1.453    <- homers jump
#     ERA       4.751     3.950      1.203
#
# It mirrors the hitting side and tells the same story from the mound: a promoted
# arm misses fewer bats, gives up more contact and far more of it over the fence,
# and his ERA rises about 20%. Walks going slightly DOWN is the one surprise —
# most likely selection, since the arms clubs promote are the ones who throw
# strikes — but it is measured, so it stays.
_MLE_PIT = {
    11: {"k": 0.849, "bb": 0.942, "hr": 1.453, "h": 1.054, "era": 1.203},   # AAA
    12: {"k": 0.800, "bb": 1.020, "hr": 1.600, "h": 1.090, "era": 1.330},   # AA
    13: {"k": 0.760, "bb": 1.090, "hr": 1.720, "h": 1.120, "era": 1.440},   # A+
    14: {"k": 0.720, "bb": 1.160, "hr": 1.840, "h": 1.150, "era": 1.550},   # A
}
# A call-up is a fringe major-leaguer, not a league-average one. Even after
# translation, cap the pool so a hot Triple-A line cannot promote someone into a
# better hitter than the regulars he is covering for.
_MLE_CAP = 1.00

# How much an MLE plate appearance is worth against a real major-league one.
#
# The translation is a fitted average, so a translated PA carries the level's
# noise ON TOP of the sampling noise a real PA has. Counting it at full weight
# would let a big Triple-A season outvote a genuine MLB book; counting it at
# zero is what the code used to do, and that is what this fixes. A half-weight
# is the conventional treatment of minor-league equivalencies and sits between
# the two failure modes. This is a judgment call, not a measurement: pinning it
# properly needs a same-season predictive test on dual-level players, which the
# translation sample (127 hitters / 154 arms) is too thin to support cleanly.
_MLE_PA_WEIGHT = 0.50

# Counting stats that carry the sample. Summing these merges two stat lines, and
# because the MLE line has already been rescaled to major-league equivalence the
# sum is in one consistent unit.
_MERGE_BAT = ("plateAppearances", "atBats", "hits", "doubles", "triples",
              "homeRuns", "baseOnBalls", "hitByPitch", "strikeOuts")


def _merge_bat(mlb_st, milb_st, weight=_MLE_PA_WEIGHT):
    """One hitting line from an MLB line plus a translated minor-league line.

    Both sides are counts, so they simply add, with the minor-league side scaled
    down by `weight` to reflect that a translated PA is weaker evidence. Scaling
    numerator and denominator together leaves the RATES untouched and moves only
    the sample size, which is exactly what the shrinkage downstream reads."""
    if not milb_st:
        return dict(mlb_st or {}), 0.0
    out = dict(mlb_st or {})
    for key in _MERGE_BAT:
        base = _f((mlb_st or {}).get(key))
        add = _f(milb_st.get(key)) * weight
        if (mlb_st or {}).get(key) is not None or milb_st.get(key) is not None:
            out[key] = base + add
    eff = _f(milb_st.get("plateAppearances")) * weight
    tot = _f(out.get("plateAppearances"))
    return out, (eff / tot if tot > 0 else 0.0)


def _merge_pit(mlb_st, milb_st, weight=_MLE_PA_WEIGHT):
    """Same merge for arms. Innings carry the sample here, and the per-9 fields
    have to be recomputed from the merged counts: _pit_rates_from PREFERS the
    derived rates, so leaving them at the MLB line's values would silently
    discard everything the minor-league line just contributed."""
    if not milb_st:
        return dict(mlb_st or {}), 0.0
    out = dict(mlb_st or {})
    mlb_ip = _f((mlb_st or {}).get("inningsPitched"))
    milb_ip = _f(milb_st.get("inningsPitched")) * weight
    tot_ip = mlb_ip + milb_ip
    for key in ("strikeOuts", "baseOnBalls", "homeRuns", "hits", "earnedRuns",
                "battersFaced"):
        base = _f((mlb_st or {}).get(key))
        add = _f(milb_st.get(key)) * weight
        if (mlb_st or {}).get(key) is not None or milb_st.get(key) is not None:
            out[key] = base + add
    out["inningsPitched"] = tot_ip
    if tot_ip > 0:
        # Recompute the derived fields the reader actually prefers.
        for key, cnt in (("strikeoutsPer9Inn", "strikeOuts"),
                         ("walksPer9Inn", "baseOnBalls"),
                         ("homeRunsPer9", "homeRuns")):
            if out.get(cnt) is not None:
                out[key] = _f(out[cnt]) * 9.0 / tot_ip
        if out.get("earnedRuns") is not None:
            out["era"] = _f(out["earnedRuns"]) * 9.0 / tot_ip
    return out, (milb_ip / tot_ip if tot_ip > 0 else 0.0)


def milb_hitting(season):
    """{player_id: (translated stat dict, level_sport_id)} for every minor-league
    hitter, best level first. One request per level (~800 players each), cached
    like everything else here -- the per-player endpoint would be hundreds of
    calls for a single slate."""
    def fetch():
        out = {}
        for sid in _MLE_LEVELS:
            try:
                d = baseball._get(f"{STATS}/stats?stats=season&group=hitting"
                                  f"&season={season}&sportId={sid}&limit=3000"
                                  f"&playerPool=All")
            except Exception:
                continue
            for sp in (d.get("stats") or [{}])[0].get("splits") or []:
                pid = (sp.get("player") or {}).get("id")
                st = sp.get("stat") or {}
                if not pid or pid in out:
                    continue          # already have him at a higher level
                if _f(st.get("plateAppearances")) < 40:
                    continue          # too thin to translate
                out[pid] = (_translate(st, _MLE[sid]), sid)
        return out
    try:
        return baseball._cached(("milb_hit", season), 6 * 3600, fetch)
    except Exception:
        return {}


def milb_pitching(season):
    """{player_id: (translated stat dict, level)} for minor-league arms, best
    level first. Same shape and cost as milb_hitting."""
    def fetch():
        out = {}
        for sid in _MLE_LEVELS:
            try:
                d = baseball._get(f"{STATS}/stats?stats=season&group=pitching"
                                  f"&season={season}&sportId={sid}&limit=3000"
                                  f"&playerPool=All")
            except Exception:
                continue
            for sp in (d.get("stats") or [{}])[0].get("splits") or []:
                pid = (sp.get("player") or {}).get("id")
                st = sp.get("stat") or {}
                if not pid or pid in out:
                    continue
                if _ip_outs(st.get("inningsPitched")) < 15:
                    continue                  # too thin to translate
                out[pid] = (_translate_pit(st, _MLE_PIT[sid]), sid)
        return out
    try:
        return baseball._cached(("milb_pit", season), 6 * 3600, fetch)
    except Exception:
        return {}


def _ip_outs(v):
    """MLB writes innings as 5.1 / 5.2 meaning 5 1/3 / 5 2/3, not decimal."""
    try:
        w, _, fr = str(v).partition(".")
        return int(w) + (int(fr[0]) / 3.0 if fr else 0.0)
    except (TypeError, ValueError):
        return 0.0


def _translate_pit(st, f):
    """A minor-league pitching line rescaled to major-league equivalence.

    Innings are NOT scaled, for the same reason plate appearances are not on the
    hitting side: the workload is real evidence about the arm even though the
    level is not, and it is what the shrinkage weighs."""
    out = dict(st)
    # BOTH the counts and the per-9 rates. _pit_rates_from reads the DERIVED
    # strikeoutsPer9Inn / walksPer9Inn fields, not the counts, so scaling only the
    # counts left the translation a silent no-op for K and BB -- the arm came out
    # with its untouched Triple-A strikeout rate. Every feed here carries both, so
    # both have to move together or the two disagree.
    for key, mult in (("strikeOuts", f["k"]), ("strikeoutsPer9Inn", f["k"]),
                      ("baseOnBalls", f["bb"]), ("walksPer9Inn", f["bb"]),
                      ("homeRuns", f["hr"]), ("homeRunsPer9", f["hr"]),
                      ("hits", f["h"]), ("era", f["era"])):
        if st.get(key) is not None:
            out[key] = _f(st.get(key)) * mult
    return out


def _translate(st, f):
    """A minor-league stat line rescaled to major-league equivalence. Counting
    stats are scaled in place so the downstream per-PA maths is unchanged; PA
    itself is NOT scaled, because the sample size is real even if the level is
    not -- that is what the shrinkage should see."""
    out = dict(st)
    for key, mult in (("hits", f["hit"]), ("doubles", f["hit"]), ("triples", f["hit"]),
                      ("homeRuns", f["hr"]), ("baseOnBalls", f["bb"]),
                      ("hitByPitch", f["bb"]), ("strikeOuts", f["k"])):
        if st.get(key) is not None:
            out[key] = _f(st.get(key)) * mult
    return out


def _shrink(obs, prior, n, k):
    """Sample-weighted blend toward a prior: little data -> mostly prior, lots of
    data -> mostly observed. k is the stabilization point (in the same units as n)."""
    return (n * obs + k * prior) / (n + k) if (n + k) else prior


# Stabilization points (Bayesian shrinkage k): how much data it takes for an
# observed rate to be trusted half-and-half vs the prior. Batting in PA, pitching
# in batters faced. HR/power are noisiest (largest k); K is the most stable.
_BAT_K = {"k": 60, "bb": 120, "hit": 320, "pow": 200, "hbp": 80}
_PIT_K = {"k": 70, "bb": 170, "hr": 500, "era": 320}


def _bat_rates_from(st, pa):
    """Per-PA component rates from a hitting stat line (None-safe)."""
    if not st or pa <= 0:
        return None
    return {"k": _f(st.get("strikeOuts")) / pa, "bb": _f(st.get("baseOnBalls")) / pa,
            "hbp": _f(st.get("hitByPitch")) / pa, "hr": _f(st.get("homeRuns")) / pa,
            "2b": _f(st.get("doubles")) / pa, "3b": _f(st.get("triples")) / pa,
            "hit": _f(st.get("hits")) / pa}


def _batter(per, st, career=None, avail=1.0, mults=(1.0, 1.0)):
    """Batter profile with Bayesian shrinkage: blend this season toward a career
    prior (or league average if no career book), weighted by plate appearances, so
    a 40-PA hot start doesn't read as true talent. Statcast xBA/xSLG then nudges
    the regressed contact/power toward deserved quality."""
    pa = _f(st.get("plateAppearances"))
    obs = _bat_rates_from(st, pa) or {}
    cpa = _f((career or {}).get("plateAppearances"))
    cprior = _bat_rates_from(career, cpa) if cpa >= 50 else None
    lg_hit = LG["1b"] + LG["2b"] + LG["3b"] + LG["hr"]
    pri = {"k": LG["k"], "bb": LG["bb"], "hbp": LG["hbp"], "hr": LG["hr"],
           "2b": LG["2b"], "3b": LG["3b"], "hit": lg_hit}
    if cprior:                                   # career book available -> better prior
        pri.update(cprior)
    g = lambda c: obs.get(c, pri[c])
    k = _shrink(g("k"), pri["k"], pa, _BAT_K["k"])
    bb = _shrink(g("bb"), pri["bb"], pa, _BAT_K["bb"])
    hbp = _shrink(g("hbp"), pri["hbp"], pa, _BAT_K["hbp"])
    hit = _shrink(g("hit"), pri["hit"], pa, _BAT_K["hit"])
    d2 = _shrink(g("2b"), pri["2b"], pa, _BAT_K["pow"])
    t3 = _shrink(g("3b"), pri["3b"], pa, _BAT_K["pow"])
    hr = _shrink(g("hr"), pri["hr"], pa, _BAT_K["pow"])
    # Statcast true-talent nudge on the already-regressed rates.
    contact, power = mults
    d2 *= power; t3 *= power; hr *= power; hit *= contact
    singles = max(0.0, hit - d2 - t3 - hr)
    rates = {"k": k, "bb": bb, "hbp": hbp, "hr": hr, "1b": singles, "2b": d2, "3b": t3}
    # Stolen bases: attempt rate per time-on-first + real success rate, so the
    # season engine can run the bases with each player's actual tendencies.
    sb = _f(st.get("stolenBases"))
    cs = _f(st.get("caughtStealing"))
    on1 = (_f(st.get("hits")) - _f(st.get("doubles")) - _f(st.get("triples"))
           - _f(st.get("homeRuns")) + _f(st.get("baseOnBalls")) + _f(st.get("hitByPitch")))
    sbr = min(0.4, sb / on1) if on1 > 0 else 0.0
    sbs = max(0.55, min(0.92, sb / (sb + cs))) if (sb + cs) >= 3 else 0.72
    return {"id": per["id"], "name": per.get("boxscoreName") or per["fullName"],
            "side": per.get("batSide", {}).get("code", "R"), "pa": pa, "rates": rates,
            "avail": avail, "sbr": round(sbr, 4), "sbs": round(sbs, 3)}


def _pit_rates_from(st, ip):
    """Per-batter-faced K/BB/HR from a pitching stat line (None-safe)."""
    if not st or ip <= 0:
        return None
    # Prefer the derived per-9 fields, but fall back to the RAW COUNTS before the
    # league average. HR already did this; K and BB dropped straight to the league
    # mean, so a line carrying real counts but no derived rate projected as a
    # perfectly average arm. Every current feed supplies the derived fields, so
    # this is a guard rather than a fix for something observed live -- but it is
    # the difference between degrading gracefully and degrading invisibly.
    k9 = _f(st.get("strikeoutsPer9Inn")) or (_f(st.get("strikeOuts")) * 9 / ip)
    bb9 = _f(st.get("walksPer9Inn")) or (_f(st.get("baseOnBalls")) * 9 / ip)
    hr9 = _f(st.get("homeRunsPer9")) or (_f(st.get("homeRuns")) * 9 / ip)
    return {"k": min(0.50, k9 / PA_PER_9) if k9 else LG["k"],
            "bb": min(0.22, bb9 / PA_PER_9) if bb9 else LG["bb"],
            "hr": min(0.09, hr9 / PA_PER_9) if hr9 else LG["hr"]}


def _pitcher(per, st, career=None, avail=1.0):
    """Pitcher profile with Bayesian shrinkage: regress this season's K/BB/HR/ERA
    toward a career prior (or league average) weighted by batters faced. A 20-IP
    1.50-ERA hot streak no longer projects as an immortal ace over 180 innings."""
    ip = _f(st.get("inningsPitched"))
    bf = ip * PA_PER_9 / 9.0                      # ~ batters faced, the sample size
    obs = _pit_rates_from(st, ip) or {"k": LG["k"], "bb": LG["bb"], "hr": LG["hr"]}
    cip = _f((career or {}).get("inningsPitched"))
    cprior = _pit_rates_from(career, cip) if cip >= 20 else None
    pri = {"k": LG["k"], "bb": LG["bb"], "hr": LG["hr"]}
    if cprior:
        pri.update(cprior)
    kpa = _shrink(obs["k"], pri["k"], bf, _PIT_K["k"])
    bbpa = _shrink(obs["bb"], pri["bb"], bf, _PIT_K["bb"])
    hrpa = _shrink(obs["hr"], pri["hr"], bf, _PIT_K["hr"])
    # ERA (run-prevention proxy used for in-play hit suppression): shrink toward
    # career ERA, or league ~4.30 if no career book.
    era_prior = _f((career or {}).get("era"), 4.30) if cip >= 20 else 4.30
    era = round(_shrink(_f(st.get("era"), era_prior), era_prior, bf, _PIT_K["era"]), 2)
    return {"id": per["id"], "name": per.get("boxscoreName") or per["fullName"],
            "hand": per.get("pitchHand", {}).get("code", "R"),
            "ip": ip, "gs": _f(st.get("gamesStarted")), "g": _f(st.get("gamesPitched")),
            "sv": _f(st.get("saves")) + _f(st.get("holds")),
            "era": era, "kpa": kpa, "bbpa": bbpa, "hrpa": hrpa, "avail": avail}


def _platoon_one(pid, season):
    """Per-side (vs LHP / vs RHP) hitting line for one batter, or None."""
    def fetch():
        try:
            d = baseball._get(f"{STATS}/people/{pid}/stats?stats=statSplits"
                              f"&sitCodes=vl,vr&group=hitting&season={season}")
        except Exception:
            return None
        out = {}
        for s in d.get("stats", []):
            for sp in s.get("splits", []):
                code = (sp.get("split") or {}).get("code")
                st = sp.get("stat") or {}
                pa = _f(st.get("plateAppearances"))
                if code in ("vl", "vr") and pa > 0:
                    out[code] = {"pa": pa, "k": _f(st.get("strikeOuts")),
                                 "hit": _f(st.get("hits")), "hr": _f(st.get("homeRuns"))}
        return out or None
    return baseball._cached(("platoon", pid, season), 6 * 3600, fetch)


# League share of plate appearances that come against left-handed pitching —
# the exposure the season-long rates are already averaged over.
_LHP_EXPOSURE = 0.28
# Shrinkage (in side-PA): platoon splits are notoriously noisy; K rates
# stabilize fastest, power slowest.
_PLAT_K = {"k": 90.0, "hit": 130.0, "hr": 200.0}


def _attach_platoon(batters, season):
    """Attach per-batter platoon multipliers: bat["plat"] = {"L": {k,hit,hr},
    "R": {...}} — how each rate shifts against that pitcher hand vs his overall
    line. Multipliers are shrunk by side sample and NORMALIZED so the exposure-
    weighted mean is exactly 1.0 (0.28 L / 0.72 R): a lefty-masher gets hot vs
    southpaws and correspondingly cooler vs righties, and the season-long
    engine calibration is untouched in expectation.

    The HIT and HR components blend the raw-outcome split evenly with the
    batter's Statcast x-SPLIT (xwOBA by pitcher hand): the x-version judges
    each PA by contact quality instead of outcome, so it stabilizes roughly
    twice as fast — which is why its shrink constant is HALF the raw one. HR
    gets the x-ratio amplified (^1.8: power splits swing harder than overall
    wOBA splits). K stays raw-only — strikeouts are inside xwOBA's value, not
    separable from it, and the raw K split stabilizes fastest anyway. Both
    reads flow through the same normalization, so calibration holds no matter
    the mix; no x data at all reproduces the raw-only behavior exactly."""
    from concurrent.futures import ThreadPoolExecutor
    import errlog
    import savant

    def one(b):
        sp = _platoon_one(b["id"], season)
        if not sp or "vl" not in sp or "vr" not in sp:
            return
        xs = None
        try:
            xs = savant.batter_x_splits(b["id"], season)
        except Exception as e:
            errlog.note("SAV-xsplit", e)
        x_ratio = {}
        if xs and xs.get("L") and xs.get("R"):
            xtot = sum(v["xwoba"] * v["pa"] for v in xs.values())
            xpa = sum(v["pa"] for v in xs.values())
            x_over = xtot / xpa if xpa else 0.0
            if x_over > 0:
                x_ratio = {h: xs[h]["xwoba"] / x_over for h in ("L", "R")}
        plat = {"L": {}, "R": {}}
        for comp in ("k", "hit", "hr"):
            tot_pa = sp["vl"]["pa"] + sp["vr"]["pa"]
            tot_c = sp["vl"][comp] + sp["vr"][comp]
            overall = tot_c / tot_pa if tot_pa else 0.0
            if overall <= 0:
                plat["L"][comp] = plat["R"][comp] = 1.0
                continue
            k_shrink = _PLAT_K[comp]
            mults = {}
            for code, hand in (("vl", "L"), ("vr", "R")):
                side = sp[code]
                raw = (side[comp] / side["pa"]) / overall if side["pa"] else 1.0
                m = (side["pa"] * raw + k_shrink) / (side["pa"] + k_shrink)
                if comp in ("hit", "hr") and hand in x_ratio:
                    xr = x_ratio[hand] ** (1.8 if comp == "hr" else 1.0)
                    xpa_h = xs[hand]["pa"]
                    kx = k_shrink / 2.0            # x stabilizes ~2x faster
                    xm = (xpa_h * xr + kx) / (xpa_h + kx)
                    m = 0.5 * m + 0.5 * xm
                mults[hand] = m
            norm = _LHP_EXPOSURE * mults["L"] + (1 - _LHP_EXPOSURE) * mults["R"]
            for hand in ("L", "R"):
                plat[hand][comp] = round(max(0.75, min(1.30, mults[hand] / (norm or 1.0))), 3)
        b["plat"] = plat

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(one, batters))


def arm_quality(p):
    """How good a pitcher is, on one scale: more Ks, fewer walks, lower ERA.

    Shared so every consumer ranks arms the same way — the bullpen order inside
    a profile, and the playoff rotation deep_season starts in October.
    """
    return (p["kpa"] - p["bbpa"]) - p["era"] / 20.0


def team_profile(team_id, season=None):
    """{rotation, bullpen, lineup, bench} of player dicts for one club."""
    season = season or str(clock.today_et().year)

    def build():
        hit = _roster_stats(team_id, season, "hitting")
        pit = _roster_stats(team_id, season, "pitching")
        # Statcast xStats so the deep run uses true-talent batter rates (the same
        # refinement the combo sim applies). Best-effort: falls back to raw rates.
        xstats = {}
        try:
            import savant
            xstats = savant.expected_stats(season) or {}
        except Exception as _e:
            errlog.note("DD-team_profile-5", _e)
        # How much of what we asked for actually came back. The roster call
        # hydrates SEASON and CAREER stats together; career is what regresses a
        # player toward his true talent. When the hydration comes back partial --
        # seen live on a constrained host -- career silently goes missing, every
        # rate is taken at face value, and the projection tracks a team's RECORD
        # instead of its talent. The board still renders, fully confident and
        # meaningfully wrong, which is the worst way for this to fail. Counted
        # here so the run can refuse to publish on top of a good one.
        seen_n = career_n = 0
        batters, pitchers, depth, depth_bats = [], [], [], []
        milb = milb_hitting(season) or {}
        milb_p = milb_pitching(season) or {}
        for pid in set(hit) | set(pit):
            h, p = hit.get(pid), pit.get(pid)
            per, pos, code = (p or h)[0], (p or h)[1], (p or h)[4]
            seen_n += 1
            if (h and h[3]) or (p and p[3]):
                career_n += 1
            # Active + short-IL play; RM (reassigned to minors) become emergency
            # taxi-squad depth; 60-day IL / suspended / paternity -> out.
            active = code == "A" or code in SHORT_IL
            is_depth = code == "RM"
            if not (active or is_depth):
                continue
            avail = SHORT_IL.get(code, 1.0)
            abbr = (pos or {}).get("abbreviation", "")
            two_way = abbr == "TWP"
            is_pitcher_pos = abbr in ("P", "TWP")
            pst, pcar = (p[2], p[3]) if p else (None, None)
            hst, hcar = (h[2], h[3]) if h else (None, None)
            # Pitching side: pure pitchers + two-way players. A two-way ace lands in
            # BOTH pools, so his elite innings reach the rotation instead of being
            # dropped and his bat reallocated to the bullpen bottom.
            # A career book with no current season is still a projection: _pitcher
            # shrinks an empty season toward the career prior, so passing {} gives
            # exactly the career rates. These used to be dropped outright.
            # MERGED, not gated. This used to fire only for an optioned player
            # with NO major-league record at all, which meant any career book --
            # 8 innings from two years ago -- blocked a full current Triple-A
            # season from being seen. Measured on the real 40-mans: the fallback
            # reached 25 of 281 optioned players, while 34 more sat on a career
            # book too thin to clear _pitcher's own 20-IP prior bar AND a real
            # minor-league season that was thrown away. They came out as league
            # average. It also dropped ACTIVE players outright when their only
            # line was a minor-league one, because the gate asked for is_depth.
            #
            # Weighting by sample instead of gating on absence handles every one
            # of those cases without a special branch: a regular's rehab start
            # adds a few effective innings to a full season and changes nothing,
            # a prospect's whole Triple-A year is most of what we know about him.
            pst_eff, pmilb_lvl = pst, None
            got = milb_p.get(pid)
            if got:
                merged, share = _merge_pit(pst, got[0])
                pst_eff = merged
                if share >= 0.5:          # the translated line is the main source
                    pmilb_lvl = got[1]
            if is_pitcher_pos and (pst_eff or pcar):
                arm = _pitcher(per, pst_eff or {}, pcar, avail)
                if pmilb_lvl:
                    arm["milb_level"] = pmilb_lvl
                (depth if is_depth else pitchers).append(arm)
            # Batting side: position players + two-way. RM (optioned) bats become
            # the position-player taxi squad — the call-up pool the engine reaches
            # for when injuries drain the MLB bench, mirroring the depth arms.
            # Same for bats, plus the minor-league fallback: an optioned player
            # with no MLB record at all gets his translated Triple-A (or lower)
            # line, which is the difference between a call-up pool of shuttle
            # veterans and one that contains the actual prospects.
            hst_eff, milb_lvl = hst, None
            got = milb.get(pid)          # merged by sample, see the arms above
            if got:
                merged, share = _merge_bat(hst, got[0])
                hst_eff = merged
                if share >= 0.5:
                    milb_lvl = got[1]
            if (not is_pitcher_pos or two_way) and (hst_eff or hcar):
                mults = (1.0, 1.0)
                try:
                    import savant
                    mults = savant.quality_mults(xstats.get(pid))
                except Exception as _e:
                    errlog.note("DD-team_profile-4", _e)
                if milb_lvl:
                    # Statcast has no minor-league book for him, and a translated
                    # line is already an estimate -- do not stack a second one.
                    mults = (min(_MLE_CAP, mults[0]), min(_MLE_CAP, mults[1]))
                b = _batter(per, hst_eff or {}, hcar, avail, mults)
                if milb_lvl:
                    b["milb_level"] = milb_lvl
                (depth_bats if is_depth else batters).append(b)
        # Rotation = top starters by games started; bullpen = the rest with innings.
        starters = sorted((p for p in pitchers if p["gs"] >= 3),
                          key=lambda p: (p["gs"], p["ip"]), reverse=True)[:6]
        sid = {p["id"] for p in starters}
        # Bullpen is ranked WORST-first so the best arm (closer) is held back for
        # late innings.
        quality = arm_quality
        relievers = [p for p in pitchers if p["id"] not in sid and p["ip"] > 0]
        relievers.sort(key=quality)
        depth.sort(key=quality)                       # worst-first; real org arms
        # Lineup = nine regulars by plate appearances; bench = remaining bats.
        batters.sort(key=lambda b: b["pa"], reverse=True)
        # Per-batter platoon splits (vs LHP / vs RHP) for the bats that actually
        # play — the engine knows every pitcher's hand, so a lefty-masher facing
        # a southpaw starter finally reads as one. Best-effort.
        try:
            _attach_platoon(batters[:13], season)
        except Exception as _e:
            errlog.note("DD-team_profile-3", _e)
        # Statcast pitch arsenals: each pitcher's mix + each batter's per-pitch
        # strengths/weaknesses, so a breaking-ball-blind hitter facing a
        # slider-heavy starter reads as the mismatch he is. Best-effort.
        try:
            import savant
            ars = savant.pitch_arsenals(season)
            for b in batters:
                a = ars["bat"].get(str(b["id"]))
                if a:
                    b["ars"] = a
            for p in pitchers + depth:
                m = ars["pit"].get(str(p["id"]))
                if m:
                    p["mix"] = m
        except Exception as _e:
            errlog.note("DD-team_profile-2", _e)
        # Batter danger: how hard each hitter punishes a mistake over the heart of
        # the plate (MLB hot/cold zone slugging). Drives the sim's pitch-around
        # logic. Lineup only (the bats that'll actually hit), fetched concurrently.
        try:
            import zones
            from concurrent.futures import ThreadPoolExecutor
            lineup9 = batters[:9]
            with ThreadPoolExecutor(max_workers=6) as ex:
                dv = list(ex.map(lambda b: zones.batter_danger(b["id"], season), lineup9))
            for b, d in zip(lineup9, dv):
                if d is not None:
                    b["danger"] = d
        except Exception as _e:
            errlog.note("DD-team_profile", _e)
        # Taxi bats: BEST ready bat first — when a club loses a starter, the
        # call-up is their best available bat, not the shuttle guy (the depth
        # ARMS pop worst-first because that's how bullpen call-ups work).
        def bat_q(b):
            r = b["rates"]
            return (r["1b"] + 2 * r["2b"] + 3 * r["3b"] + 4 * r["hr"]
                    + 0.7 * (r["bb"] + r["hbp"]))
        depth_bats.sort(key=bat_q, reverse=True)
        lineup, bench = batters[:9], batters[9:]
        taxi = depth_bats[:6]
        # A chronically thin bench (long-IL absences shrank the active group)
        # gets topped back up from the taxi squad — real clubs carry 26, they
        # don't play a man short for months.
        while len(bench) < 4 and taxi:
            bench.append(taxi.pop(0))
        return {"rotation": starters or pitchers[:1], "bullpen": relievers,
                "depth": depth[:6], "lineup": lineup, "bench": bench,
                "depth_bats": taxi,
                "_quality": {"players": seen_n, "with_career": career_n,
                             "xstats": len(xstats)}}

    def disk_or_build():
        got = _profile_disk_get(team_id, season)
        if got is not None:
            return got
        val = build()
        _profile_disk_put(team_id, season, val)
        return val
    return baseball._cached(("deep_profile4", team_id, season), 21600, disk_or_build)


# The in-process cache above dies with its process -- and the slate builds in a
# FRESH niced child every few minutes, so each child re-hydrated every slate
# team from zero: two roster calls, the xStats CSV, and (since the platoon
# x-splits landed) THIRTEEN Statcast searches per club. Caught by the memory
# watchdog on its first night as an 892 MB slate child. Profiles now persist
# in the deep artifact store, so a child reads what a sibling (or the PC
# worker, which syncs this store uphill) already hydrated; at most one process
# per TTL pays the full cost. Plain per-team pickles named by team+season --
# additive to the store, so no artifact-schema bump: an older server ignores
# them and an older PC simply doesn't send them.
_PROFILE_DISK_TTL = 21600     # same 6h as the in-process cache


def _profile_path(team_id, season):
    import os
    import deep_cache
    return os.path.join(deep_cache.CACHE_DIR, f"profile_{team_id}_{season}.pkl")


def _profile_disk_get(team_id, season):
    import os
    import time
    try:
        path = _profile_path(team_id, season)
        if time.time() - os.stat(path).st_mtime > _PROFILE_DISK_TTL:
            return None
        import pickle
        with open(path, "rb") as fh:
            return pickle.load(fh)
    except Exception:
        return None               # no disk copy is a slow build, never a broken one


def _profile_disk_put(team_id, season, val):
    try:
        import os
        import pickle
        import tempfile
        import deep_cache
        os.makedirs(deep_cache.CACHE_DIR, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=deep_cache.CACHE_DIR, suffix=".tmp")
        with os.fdopen(fd, "wb") as fh:
            pickle.dump(val, fh, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, _profile_path(team_id, season))
    except Exception as _e:
        import errlog
        errlog.note("DD-profile_disk", _e)


def _bat_real(st):
    """Real current-season counting line for a hitter (None-safe)."""
    ab = _f(st.get("atBats"))
    return {"pa": round(_f(st.get("plateAppearances"))), "ab": round(ab),
            "h": round(_f(st.get("hits"))), "hr": round(_f(st.get("homeRuns"))),
            "2b": round(_f(st.get("doubles"))), "3b": round(_f(st.get("triples"))),
            "bb": round(_f(st.get("baseOnBalls"))), "k": round(_f(st.get("strikeOuts"))),
            "r": round(_f(st.get("runs"))), "rbi": round(_f(st.get("rbi"))),
            "sb": round(_f(st.get("stolenBases"))),
            "avg": _f(st.get("avg")) or (_f(st.get("hits")) / ab if ab else 0.0),
            "ops": _f(st.get("ops"))}


def _pit_real(st):
    """Real current-season line for a pitcher (IP kept as the raw MLB string)."""
    return {"ip": st.get("inningsPitched") or "0.0",
            "k": round(_f(st.get("strikeOuts"))), "bb": round(_f(st.get("baseOnBalls"))),
            "h": round(_f(st.get("hits"))), "hr": round(_f(st.get("homeRuns"))),
            "r": round(_f(st.get("runs"))), "era": _f(st.get("era")),
            "whip": _f(st.get("whip")),
            "gs": round(_f(st.get("gamesStarted")))}


def roster_lines(team_id, season=None):
    """Real current-season stat lines + IL status for the full 40-man, keyed by
    player id: {pid: {name, pos, status, il, bat?, pit?}}. Lets the team view show
    each player's actual numbers beside the simulated ones, and surfaces injured
    players the season sim leaves out entirely (they sit at the bottom until active)."""
    season = season or str(clock.today_et().year)

    def build():
        hit = _roster_stats(team_id, season, "hitting")
        pit = _roster_stats(team_id, season, "pitching")
        out = {}
        for pid in set(hit) | set(pit):
            h, p = hit.get(pid), pit.get(pid)
            per, pos, code = (h or p)[0], (h or p)[1], (h or p)[4]
            rec = {"id": pid, "name": per.get("boxscoreName") or per.get("fullName"),
                   "pos": (pos or {}).get("abbreviation", ""),
                   "status": code, "il": _is_il(code)}
            if h and h[2]:
                rec["bat"] = _bat_real(h[2])
            if p and p[2]:
                rec["pit"] = _pit_real(p[2])
            out[pid] = rec
        return out
    return baseball._cached(("deep_roster_lines", team_id, season), 21600, build)
