"""Statcast (Baseball Savant) data — the real quality-of-contact and speed
numbers behind a hitter's true talent. These are the actual stats MLB The Show's
ratings are derived from, used directly instead of someone's 0-99 guess:

  - expected stats (xBA / xSLG / xwOBA): results with luck stripped out, for
    regressing a hitter toward what he's genuinely hitting (like FIP for pitchers).
  - sprint speed (ft/sec): drives baserunning + stolen bases in the game sim.

Everything is cached for hours and degrades to {} on any failure, so the model
falls straight back to plain MLB-API stats if Savant is unreachable.
"""

import csv
import io
import time
import urllib.request

_UA = "kalshi-betting-helper/1.0"
_cache = {}


def _get_csv(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        text = r.read().decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def _cached(key, ttl, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    try:
        val = fn()
    except Exception:
        val = None
    _cache[key] = (time.time(), val)
    return val


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def expected_stats(season):
    """{player_id: {ba, xba, slg, xslg, woba, xwoba, pa}} for the season."""
    def build():
        url = ("https://baseballsavant.mlb.com/leaderboard/expected_statistics"
               f"?type=batter&year={season}&filterType=bip&min=50&csv=true")
        out = {}
        for r in _get_csv(url):
            pid = r.get("player_id")
            if not pid:
                continue
            out[str(pid)] = {
                "ba": _f(r.get("ba")), "xba": _f(r.get("est_ba")),
                "slg": _f(r.get("slg")), "xslg": _f(r.get("est_slg")),
                "woba": _f(r.get("woba")), "xwoba": _f(r.get("est_woba")),
                "pa": _f(r.get("pa")),
            }
        return out
    return _cached(("xstats", season), 6 * 3600, build) or {}


def team_defense(season):
    """{team_id: outs_above_average} for the season -- team fielding OAA from
    Statcast. Positive = the defense turns more balls in play into outs than
    average (fewer hits/runs allowed); negative = a leaky glove."""
    def build():
        url = ("https://baseballsavant.mlb.com/leaderboard/outs_above_average"
               f"?type=Fielding_Team&startYear={season}&endYear={season}&split=no"
               "&team=&range=year&min=q&pos=&roles=&viz=hide&csv=true")
        out = {}
        for r in _get_csv(url):
            tid = r.get("team_id")
            oaa = _f(r.get("outs_above_average"))
            if tid and oaa is not None:
                out[str(tid)] = oaa
        return out
    return _cached(("team_def", season), 6 * 3600, build) or {}


def sprint_speed(season):
    """{player_id: sprint_speed_ft_per_sec} for the season."""
    def build():
        url = ("https://baseballsavant.mlb.com/leaderboard/sprint_speed"
               f"?year={season}&min=5&csv=true")
        out = {}
        for r in _get_csv(url):
            pid = r.get("player_id")
            ss = _f(r.get("sprint_speed"))
            if pid and ss:
                out[str(pid)] = ss
        return out
    return _cached(("speed", season), 6 * 3600, build) or {}


def quality_mults(xs):
    """Contact + power multipliers (dampened, clamped) that nudge a hitter's
    rates toward his expected stats -- crediting hard-hit bad luck and fading
    lucky results. (1.0, 1.0) = no adjustment / no data."""
    if not xs:
        return 1.0, 1.0
    ba, xba = xs.get("ba"), xs.get("xba")
    slg, xslg = xs.get("slg"), xs.get("xslg")
    contact = power = 1.0
    if ba and xba and ba > 0:
        contact = max(0.88, min(1.12, 1 + 0.5 * ((xba - ba) / ba)))
    if slg and xslg and slg > 0:
        power = max(0.85, min(1.15, 1 + 0.5 * ((xslg - slg) / slg)))
    return contact, power


def pitch_arsenals(season):
    """Arsenal matchup data from the Statcast pitch-arsenal leaderboards:

      {"pit": {pid: {pitch_type: usage_fraction}},
       "bat": {pid: {pitch_type: {"k": whiff_mult, "w": woba_mult}}}}

    Batter multipliers are RELATIVE TO HIS OWN overall line (usage-weighted
    across every pitch he saw), shrunk by pitches seen — so vs a league-average
    mix the expected multiplier is ~1 and the season stays calibrated, while a
    breaking-ball-blind hitter facing a slider-heavy starter finally reads as
    one. Everything degrades to {} (no adjustment) on failure."""
    def build():
        base = ("https://baseballsavant.mlb.com/leaderboard/pitch-arsenal-stats"
                "?type={t}&pitchType=&year={y}&team=&min=5&csv=true")
        pit_rows = _get_csv(base.format(t="pitcher", y=season))
        bat_rows = _get_csv(base.format(t="batter", y=season))

        pit = {}
        for r in pit_rows:
            pid, pt = r.get("player_id"), r.get("pitch_type")
            u = _f(r.get("pitch_usage"))
            if pid and pt and u:
                pit.setdefault(pid, {})[pt] = u / 100.0

        # Batter: per-pitch whiff% and wOBA vs his own usage-weighted overall.
        raw = {}
        for r in bat_rows:
            pid, pt = r.get("player_id"), r.get("pitch_type")
            n = _f(r.get("pitches"))
            wh, wo = _f(r.get("whiff_percent")), _f(r.get("woba"))
            if pid and pt and n and wh is not None and wo is not None:
                raw.setdefault(pid, []).append((pt, n, wh, wo))
        bat = {}
        _KN, _WN = 250.0, 350.0            # shrinkage in pitches seen (whiff / woba)
        for pid, rows in raw.items():
            tot = sum(n for _, n, _, _ in rows) or 1.0
            o_wh = sum(n * wh for _, n, wh, _ in rows) / tot
            o_wo = sum(n * wo for _, n, _, wo in rows) / tot
            if o_wh <= 0 or o_wo <= 0:
                continue
            m = {}
            for pt, n, wh, wo in rows:
                km = (n * (wh / o_wh) + _KN) / (n + _KN)
                wm = (n * (wo / o_wo) + _WN) / (n + _WN)
                m[pt] = {"k": round(max(0.80, min(1.25, km)), 3),
                         "w": round(max(0.85, min(1.18, wm)), 3)}
            bat[pid] = m
        return {"pit": pit, "bat": bat}
    return _cached(("arsenal", season), 12 * 3600, build) or {"pit": {}, "bat": {}}

def handed_hr_factors(year=None):
    """{club_display_name: {"L": residual, "R": residual}} -- how much MORE a
    park rewards home runs from one batter side than its own average.

    Statcast publishes park HR factors split by batter side (the Yankee Stadium
    short porch is a lefty effect, not a park-wide one). The park's OVERALL
    level is already inside the model's run-environment factor, so shipping the
    raw index would double-count it; each side's index is divided by the park's
    two-side mean, leaving only the handedness RESIDUAL (Yankee LHB ~1.1, RHB
    ~0.9). Best-effort: any failure returns {} and nothing downstream changes.
    """
    import clock
    year = year or (clock.today_et().year - 1)     # last FULL season's factors

    def build():
        import json as _json
        import re as _re
        import urllib.request as _rq
        out = {}
        sides = {}
        for side in ("L", "R"):
            url = ("https://baseballsavant.mlb.com/leaderboard/statcast-park-factors"
                   f"?type=year&year={year}&batSide={side}&stat=index_hr"
                   "&condition=All&rolling=")
            req = _rq.Request(url, headers={"User-Agent": _UA})
            body = _rq.urlopen(req, timeout=25).read().decode()
            m = _re.search(r"var data = (\[.*?\]);", body, _re.S)
            if not m:
                return {}
            for r in _json.loads(m.group(1)):
                club = r.get("name_display_club")
                idx = _f(r.get("index_hr"))
                if club and idx:
                    sides.setdefault(club, {})[side] = idx
        for club, d in sides.items():
            if "L" in d and "R" in d:
                mean = (d["L"] + d["R"]) / 2.0
                if mean > 0:
                    out[club] = {"L": round(d["L"] / mean, 3),
                                 "R": round(d["R"] / mean, 3)}
        return out
    return _cached(("park_hand_hr", year), 24 * 3600, build) or {}
