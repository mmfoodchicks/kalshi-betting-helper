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


def _roster_stats(team_id, season, group):
    """{player_id: (person, stat)} for one stat group, hydrated in one call."""
    def fetch():
        url = (f"{STATS}/teams/{team_id}/roster?rosterType=active"
               f"&hydrate=person(stats(type=season,group={group},season={season}))")
        d = baseball._get(url)
        out = {}
        for r in d.get("roster", []):
            per = r.get("person", {})
            st = None
            for s in (per.get("stats") or []):
                sp = s.get("splits") or []
                if sp:
                    st = sp[0].get("stat")
                    break
            out[per["id"]] = (per, r.get("position", {}), st)
        return out
    return baseball._cached(("deep_roster", team_id, season, group), 21600, fetch)


def _batter(per, st):
    pa = _f(st.get("plateAppearances"))
    if pa < 25:                       # thin sample -> league-average bat
        rates = dict(LG)
    else:
        h = _f(st.get("hits")); d2 = _f(st.get("doubles")); t3 = _f(st.get("triples"))
        hr = _f(st.get("homeRuns")); singles = max(0.0, h - d2 - t3 - hr)
        rates = {"k": _f(st.get("strikeOuts")) / pa, "bb": _f(st.get("baseOnBalls")) / pa,
                 "hbp": _f(st.get("hitByPitch")) / pa, "hr": hr / pa,
                 "1b": singles / pa, "2b": d2 / pa, "3b": t3 / pa}
    return {"id": per["id"], "name": per.get("boxscoreName") or per["fullName"],
            "side": per.get("batSide", {}).get("code", "R"), "pa": pa, "rates": rates}


def _pitcher(per, st):
    ip = _f(st.get("inningsPitched"))
    k9, bb9 = _f(st.get("strikeoutsPer9Inn")), _f(st.get("walksPer9Inn"))
    hr9 = _f(st.get("homeRunsPer9")) or (_f(st.get("homeRuns")) * 9 / ip if ip else 0)
    # Per-batter rates; regress thin samples toward league average.
    if ip < 10:
        kpa, bbpa, hrpa = LG["k"], LG["bb"], LG["hr"]
    else:
        kpa = min(0.45, k9 / PA_PER_9) if k9 else LG["k"]
        bbpa = min(0.20, bb9 / PA_PER_9) if bb9 else LG["bb"]
        hrpa = min(0.08, hr9 / PA_PER_9) if hr9 else LG["hr"]
    return {"id": per["id"], "name": per.get("boxscoreName") or per["fullName"],
            "hand": per.get("pitchHand", {}).get("code", "R"),
            "ip": ip, "gs": _f(st.get("gamesStarted")), "g": _f(st.get("gamesPitched")),
            "sv": _f(st.get("saves")) + _f(st.get("holds")),
            "era": _f(st.get("era"), 4.3),
            "kpa": kpa, "bbpa": bbpa, "hrpa": hrpa}


def team_profile(team_id, season=None):
    """{rotation, bullpen, lineup, bench} of player dicts for one club."""
    season = season or str(__import__("datetime").date.today().year)

    def build():
        hit = _roster_stats(team_id, season, "hitting")
        pit = _roster_stats(team_id, season, "pitching")
        batters, pitchers = [], []
        for pid, (per, pos, st) in {**hit, **pit}.items():
            abbr = (pos or {}).get("abbreviation", "")
            if abbr == "P":
                s = pit.get(pid, (None, None, None))[2]
                if s:
                    pitchers.append(_pitcher(per, s))
            else:
                s = hit.get(pid, (None, None, None))[2]
                if s:
                    batters.append(_batter(per, s))
        # Rotation = top starters by games started; bullpen = the rest with innings.
        starters = sorted((p for p in pitchers if p["gs"] >= 3),
                          key=lambda p: (p["gs"], p["ip"]), reverse=True)[:6]
        sid = {p["id"] for p in starters}
        # Quality score: more Ks, fewer walks, lower ERA = better. Bullpen is ranked
        # WORST-first so the best arm (closer) is held back for late innings.
        relievers = [p for p in pitchers if p["id"] not in sid and p["ip"] > 0]
        relievers.sort(key=lambda p: (p["kpa"] - p["bbpa"]) - p["era"] / 20.0)
        # Lineup = nine regulars by plate appearances; bench = remaining bats.
        batters.sort(key=lambda b: b["pa"], reverse=True)
        return {"rotation": starters or pitchers[:1],
                "bullpen": relievers, "lineup": batters[:9], "bench": batters[9:]}
    return baseball._cached(("deep_profile", team_id, season), 21600, build)
