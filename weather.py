"""Game-time weather for a stadium, with a run-environment factor.

Tries NOAA (api.weather.gov, US-only, no key) first, then falls back to
Open-Meteo (global, no key). Returns temperature, wind speed/direction, and
precipitation chance closest to the game's start hour, plus a multiplicative
`run_factor` for how the conditions nudge run scoring:

  - Warmer air carries the ball -> more runs.
  - Wind blowing out to center -> more runs; blowing in -> fewer.
  - Heavy rain chance -> slightly fewer runs.

Weather mainly informs the expected TOTAL (over/under); its moneyline impact is
small since it affects both offenses. Retractable roofs get a PREDICTED state
(each park's real closure policy, measured from a season of boxscore roof
observations vs outdoor temperature -- see _ROOF_CLOSE); fixed domes are
neutral.
"""

import json
import math
import urllib.request
from datetime import datetime, timezone

_UA = "kalshi-betting-helper (contact: user@example.com)"
_cache = {}

_CARDINAL = {
    "N": 0, "NNE": 22.5, "NE": 45, "ENE": 67.5, "E": 90, "ESE": 112.5,
    "SE": 135, "SSE": 157.5, "S": 180, "SSW": 202.5, "SW": 225, "WSW": 247.5,
    "W": 270, "WNW": 292.5, "NW": 315, "NNW": 337.5,
}


def _get_json(url, timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _noaa(lat, lon, when_epoch):
    pts = _get_json(f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}")
    hourly_url = pts["properties"]["forecastHourly"]
    data = _get_json(hourly_url)
    periods = data["properties"]["periods"]
    best = min(periods, key=lambda p: abs(
        datetime.fromisoformat(p["startTime"]).timestamp() - when_epoch))
    wind_mph = float((best.get("windSpeed") or "0 mph").split()[0])
    wdir = _CARDINAL.get(best.get("windDirection", ""), None)
    return {
        "temp_f": best["temperature"],
        "wind_mph": wind_mph,
        "wind_from_deg": wdir,
        "wind_dir": best.get("windDirection"),
        "precip_pct": (best.get("probabilityOfPrecipitation") or {}).get("value") or 0,
        "humidity": (best.get("relativeHumidity") or {}).get("value"),
        "summary": best.get("shortForecast", ""),
        "source": "NOAA",
    }


def _open_meteo(lat, lon, when_epoch):
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat:.4f}&longitude={lon:.4f}"
           "&hourly=temperature_2m,wind_speed_10m,wind_direction_10m,precipitation_probability,relative_humidity_2m"
           "&temperature_unit=fahrenheit&wind_speed_unit=mph&forecast_days=3")
    data = _get_json(url)
    h = data["hourly"]
    times = [datetime.fromisoformat(t).replace(tzinfo=timezone.utc).timestamp() for t in h["time"]]
    i = min(range(len(times)), key=lambda k: abs(times[k] - when_epoch))
    deg = h["wind_direction_10m"][i]
    inv = {v: k for k, v in _CARDINAL.items()}
    return {
        "temp_f": round(h["temperature_2m"][i]),
        "wind_mph": round(h["wind_speed_10m"][i]),
        "wind_from_deg": deg,
        "wind_dir": min(inv, key=lambda d: abs(d - deg)) and inv[min(inv, key=lambda d: abs(d - deg))],
        "precip_pct": h["precipitation_probability"][i] or 0,
        "humidity": (h.get("relative_humidity_2m") or [None])[i] if h.get("relative_humidity_2m") else None,
        "summary": "",
        "source": "Open-Meteo",
    }


def get_weather(lat, lon, when_epoch):
    # Sweep-on-insert (ttlcache): this cache keys on the HOUR, so it mints new
    # keys forever -- the old read-only TTL check never dropped one.
    import ttlcache
    key = (round(lat, 2), round(lon, 2), int(when_epoch // 3600))

    def build():
        for fn in (_noaa, _open_meteo):
            try:
                wx = fn(lat, lon, when_epoch)
                if wx and wx.get("temp_f") is not None:
                    return wx
            except Exception:
                continue
        return None
    return ttlcache.cached(_cache, key, 1800, build)


# --- Retractable roofs: predicted state, not a shrug -------------------------
# MEASURED per-park closure policy, not folklore. Every 2026 home game at the
# seven retractable parks (453 games) carries its actual roof state in the MLB
# boxscore ("95 degrees, Roof Closed."); joined to the OUTDOOR game-time
# temperature from the weather archive (the boxscore temp is the indoor 72 when
# closed -- fitting on it would be circular), each park's real policy appears:
#   HOU  never opened once in 2026 (65/65 closed) -- a de facto dome.
#   TEX  92% closed at every temperature; open is the rare mild-evening treat.
#   MIA  92% closed; 100% above 82F.
#   ARI  the surprise: OPEN at 80-90% of games below 88F (desert evenings),
#        then 85% closed above 88F.
#   TOR/MIL  close for COLD, not heat: 100% closed under 55F, ~always open
#        above 82F -- the flat 0.5 weight was applying half of a cold-weather
#        run penalty to games played indoors at 72F.
#   SEA  an umbrella, not a seal: covered only in cold/rain (never above 65F),
#        and the park stays open-air underneath -- weather half-applies even
#        when covered.
# Rain closes any roof: raining 65-82F games at the cold parks ran 2/3 closed
# vs 14% dry.
# {team_id: ((temp_upper_bound_F, P(closed)), ...)} evaluated on outdoor temp.
_ROOF_CLOSE = {
    109: ((82, 0.18), (88, 0.12), (999, 0.85)),                        # ARI
    117: ((999, 1.00),),                                               # HOU
    140: ((65, 0.90), (75, 0.75), (999, 0.95)),                        # TEX
    146: ((82, 0.80), (999, 1.00)),                                    # MIA
    141: ((55, 1.00), (65, 0.70), (75, 0.30), (82, 0.10), (999, 0.02)),  # TOR
    158: ((55, 1.00), (65, 0.75), (75, 0.50), (82, 0.10), (999, 0.02)),  # MIL
    136: ((55, 0.45), (65, 0.12), (999, 0.02)),                        # SEA
}
_SEA = 136          # umbrella roof: open-air even when covered


def roof_closed_pct(home_id, wx):
    """P(roof closed) for a retractable park given the OUTDOOR forecast, as a
    0-100 percent -- None when the park isn't in the measured table."""
    curve = _ROOF_CLOSE.get(home_id)
    if not curve or not wx or wx.get("temp_f") is None:
        return None
    t = wx["temp_f"]
    p = curve[-1][1]
    for hi, pc in curve:
        if t < hi:
            p = pc
            break
    if (wx.get("precip_pct") or 0) >= 60:
        p = max(p, 0.85)                     # rain closes any roof
    return round(100 * p, 0)


def _roof_weight(roof, home_id, wx):
    """How much of the outdoor weather actually reaches the game."""
    if roof != "retractable":
        return 1.0
    p = roof_closed_pct(home_id, wx)
    if p is None:
        return 0.5                           # unknown park -> the old shrug
    # Closed and sealed = indoor at 72F, zero weather; Seattle's umbrella
    # leaves the park open-air underneath, so half the weather still applies.
    w_closed = 0.5 if home_id == _SEA else 0.0
    p /= 100.0
    return (1 - p) * 1.0 + p * w_closed


def run_factor(wx, cf_bearing_deg, roof, home_id=None):
    """Multiplicative run-scoring factor from the conditions (1.0 = neutral)."""
    if roof == "fixed" or not wx:
        return 1.0, 0.0
    weight = _roof_weight(roof, home_id, wx)

    temp = wx.get("temp_f")
    # MEASURED, not folklore: 1,460 outdoor 2026 games, temperature binned --
    # 8.22 runs under 60F rising monotonically to 10.30 above 90F. That is
    # ~0.55%/F, and the old 0.15%/F was underweighting the single biggest
    # weather effect in the sport by nearly 4x.
    temp_factor = 1.0 + (temp - 70) * 0.0055 if temp is not None else 1.0

    out_component = 0.0
    wind_factor = 1.0
    if wx.get("wind_from_deg") is not None and wx.get("wind_mph"):
        wind_to = (wx["wind_from_deg"] + 180) % 360  # direction wind blows toward
        angle = math.radians(wind_to - cf_bearing_deg)
        out_component = math.cos(angle) * wx["wind_mph"]  # +out / -in (toward CF)
        # Asymmetric, as measured (temp-residualized): wind IN at 8+ mph costs
        # ~6% of runs; wind OUT adds only ~3%. Blowing in beats the ball down
        # harder than blowing out carries it.
        slope = 0.006 if out_component < 0 else 0.003
        wind_factor = 1.0 + out_component * slope

    precip = wx.get("precip_pct") or 0
    precip_factor = 1.0 - (precip / 100.0) * 0.04

    # Humid air is less dense, so the ball carries slightly farther. Small, real
    # effect; centered at ~50% RH. (Altitude/air pressure is already captured by
    # the park factor, so we don't double-count it here.)
    hum = wx.get("humidity")
    humidity_factor = 1.0 + (hum - 50) * 0.0004 if hum is not None else 1.0

    raw = temp_factor * wind_factor * precip_factor * humidity_factor
    factor = 1.0 + (raw - 1.0) * weight
    return max(0.90, min(1.12, factor)), round(out_component, 1)


def hr_extra(wx, roof, home_id=None):
    """EXTRA home-run multiplier beyond the run factor. Homers feel weather
    harder than runs do: measured over the same 1,460 games, HR/game runs 1.65
    under 60F to 3.01 above 90F (~1.3%/F) against runs' 0.55%/F -- so after the
    run environment takes its share, HR ladders need the difference
    (~0.75%/F). Wind's HR signal is confounded at current sample sizes, so
    temperature carries this alone; wind reaches HR through the run factor."""
    if roof == "fixed" or not wx:
        return 1.0
    temp = wx.get("temp_f")
    if temp is None:
        return 1.0
    weight = _roof_weight(roof, home_id, wx)
    extra = 1.0 + (temp - 70) * 0.0075 * weight
    return max(0.85, min(1.20, extra))
