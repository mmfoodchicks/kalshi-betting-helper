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
