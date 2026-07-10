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
SHORT_IL = {"D7": 0.93, "D10": 0.88, "D15": 0.82}


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
    k9, bb9 = _f(st.get("strikeoutsPer9Inn")), _f(st.get("walksPer9Inn"))
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
    engine calibration is untouched in expectation."""
    from concurrent.futures import ThreadPoolExecutor

    def one(b):
        sp = _platoon_one(b["id"], season)
        if not sp or "vl" not in sp or "vr" not in sp:
            return
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
                mults[hand] = (side["pa"] * raw + k_shrink) / (side["pa"] + k_shrink)
            norm = _LHP_EXPOSURE * mults["L"] + (1 - _LHP_EXPOSURE) * mults["R"]
            for hand in ("L", "R"):
                plat[hand][comp] = round(max(0.75, min(1.30, mults[hand] / (norm or 1.0))), 3)
        b["plat"] = plat

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(one, batters))


def team_profile(team_id, season=None):
    """{rotation, bullpen, lineup, bench} of player dicts for one club."""
    season = season or str(__import__("datetime").date.today().year)

    def build():
        hit = _roster_stats(team_id, season, "hitting")
        pit = _roster_stats(team_id, season, "pitching")
        # Statcast xStats so the deep run uses true-talent batter rates (the same
        # refinement the combo sim applies). Best-effort: falls back to raw rates.
        xstats = {}
        try:
            import savant
            xstats = savant.expected_stats(season) or {}
        except Exception:
            pass
        batters, pitchers, depth = [], [], []
        for pid in set(hit) | set(pit):
            h, p = hit.get(pid), pit.get(pid)
            per, pos, code = (p or h)[0], (p or h)[1], (p or h)[4]
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
            if is_pitcher_pos and pst:
                arm = _pitcher(per, pst, pcar, avail)
                (depth if is_depth else pitchers).append(arm)
            # Batting side: position players + two-way (never pure pitchers, never
            # taxi-squad depth — those only matter as call-up arms).
            if (not is_pitcher_pos or two_way) and hst and not is_depth:
                mults = (1.0, 1.0)
                try:
                    import savant
                    mults = savant.quality_mults(xstats.get(pid))
                except Exception:
                    pass
                batters.append(_batter(per, hst, hcar, avail, mults))
        # Rotation = top starters by games started; bullpen = the rest with innings.
        starters = sorted((p for p in pitchers if p["gs"] >= 3),
                          key=lambda p: (p["gs"], p["ip"]), reverse=True)[:6]
        sid = {p["id"] for p in starters}
        # Quality score: more Ks, fewer walks, lower ERA = better. Bullpen is ranked
        # WORST-first so the best arm (closer) is held back for late innings.
        def quality(p):
            return (p["kpa"] - p["bbpa"]) - p["era"] / 20.0
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
        except Exception:
            pass
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
        except Exception:
            pass
        return {"rotation": starters or pitchers[:1], "bullpen": relievers,
                "depth": depth[:6], "lineup": batters[:9], "bench": batters[9:]}
    return baseball._cached(("deep_profile3", team_id, season), 21600, build)


def _bat_real(st):
    """Real current-season counting line for a hitter (None-safe)."""
    ab = _f(st.get("atBats"))
    return {"pa": round(_f(st.get("plateAppearances"))), "ab": round(ab),
            "h": round(_f(st.get("hits"))), "hr": round(_f(st.get("homeRuns"))),
            "2b": round(_f(st.get("doubles"))), "3b": round(_f(st.get("triples"))),
            "bb": round(_f(st.get("baseOnBalls"))), "k": round(_f(st.get("strikeOuts"))),
            "r": round(_f(st.get("runs"))), "rbi": round(_f(st.get("rbi"))),
            "sb": round(_f(st.get("stolenBases"))),
            "avg": _f(st.get("avg")) or (_f(st.get("hits")) / ab if ab else 0.0)}


def _pit_real(st):
    """Real current-season line for a pitcher (IP kept as the raw MLB string)."""
    return {"ip": st.get("inningsPitched") or "0.0",
            "k": round(_f(st.get("strikeOuts"))), "bb": round(_f(st.get("baseOnBalls"))),
            "h": round(_f(st.get("hits"))), "hr": round(_f(st.get("homeRuns"))),
            "r": round(_f(st.get("runs"))), "era": _f(st.get("era")),
            "gs": round(_f(st.get("gamesStarted")))}


def roster_lines(team_id, season=None):
    """Real current-season stat lines + IL status for the full 40-man, keyed by
    player id: {pid: {name, pos, status, il, bat?, pit?}}. Lets the team view show
    each player's actual numbers beside the simulated ones, and surfaces injured
    players the season sim leaves out entirely (they sit at the bottom until active)."""
    season = season or str(__import__("datetime").date.today().year)

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
