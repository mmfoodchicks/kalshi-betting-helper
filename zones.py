"""Per-batter damage-by-zone -> a DANGER score.

The MLB StatsAPI hot/cold grid (the 13-zone chart ESPN shows) gives a hitter's
slugging in each part of the strike zone. Zones 01-09 are the in-zone 3x3; the
"heart" is the central cross (02/04/05/06/08) -- the meatball region a pitcher
must not leave one in. We turn a hitter's heart-of-the-plate slugging into a
danger score: ~1.0 = league-average, >1 = a masher who punishes mistakes
(Schwarber, Judge ~1.4-1.6), <1 = easy to challenge.

The deep sim uses it to pitch AROUND dangerous hitters -- nobody grooves a
fastball to Schwarber with a base open and a weaker bat on deck -- so they draw
more walks and see far fewer meatballs (fewer HR). Free, keyless, cached 12h.
"""

import json
import time
import urllib.request

_UA = {"User-Agent": "vigil/1.0"}
_URL = ("https://statsapi.mlb.com/api/v1/people/{pid}/stats"
        "?stats=hotColdZones&group=hitting&season={season}")
_HEART = {"02", "04", "05", "06", "08"}      # the meatball zones (central cross)
_LG_HEART_SLG = 0.520                          # league-ish slugging over the heart

_cache = {}
_TTL = 12 * 3600


def _get(url):
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def batter_danger(pid, season):
    """How hard a hitter punishes a pitch over the heart of the plate: ~1.0 =
    league average, >1 dangerous. None when the grid isn't available (the sim then
    treats him as neutral)."""
    key = (pid, str(season))
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    danger = None
    try:
        d = _get(_URL.format(pid=pid, season=season))
        zones = None
        for st in d.get("stats", []):
            for sp in st.get("splits", []):
                if (sp.get("stat", {}) or {}).get("name") == "sluggingPercentage":
                    zones = (sp.get("stat", {}) or {}).get("zones")
        if zones:
            vals = [_f(z.get("value")) for z in zones if z.get("zone") in _HEART]
            vals = [v for v in vals if v is not None]
            if vals:
                heart = sum(vals) / len(vals)
                danger = max(0.6, min(1.9, heart / _LG_HEART_SLG))
    except Exception:
        danger = None
    _cache[key] = (time.time(), danger)
    return danger
