"""Game-time weather for a stadium, with a run-environment factor.

Tries NOAA (api.weather.gov, US-only, no key) first, then falls back to
Open-Meteo (global, no key). Returns temperature, wind speed/direction, and
precipitation chance closest to the game's start hour, plus a multiplicative
`run_factor` for how the conditions nudge run scoring:

  - Warmer air carries the ball -> more runs.
  - Wind blowing out to center -> more runs; blowing in -> fewer.
  - Heavy rain chance -> slightly fewer runs.

Weather mainly informs the expected TOTAL (over/under); its moneyline impact is
small since it affects both offenses. Retractable roofs get half weight (roof
state unknown); fixed domes are neutral.
"""

import json
import math
import time
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
    key = (round(lat, 2), round(lon, 2), int(when_epoch // 3600))
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < 1800:
        return hit[1]
    wx = None
    for fn in (_noaa, _open_meteo):
        try:
            wx = fn(lat, lon, when_epoch)
            if wx and wx.get("temp_f") is not None:
                break
        except Exception:
            wx = None
    _cache[key] = (time.time(), wx)
    return wx


def run_factor(wx, cf_bearing_deg, roof):
    """Multiplicative run-scoring factor from the conditions (1.0 = neutral)."""
    if roof == "fixed" or not wx:
        return 1.0, 0.0
    weight = 0.5 if roof == "retractable" else 1.0

    temp = wx.get("temp_f")
    temp_factor = 1.0 + (temp - 70) * 0.0015 if temp is not None else 1.0

    out_component = 0.0
    wind_factor = 1.0
    if wx.get("wind_from_deg") is not None and wx.get("wind_mph"):
        wind_to = (wx["wind_from_deg"] + 180) % 360  # direction wind blows toward
        angle = math.radians(wind_to - cf_bearing_deg)
        out_component = math.cos(angle) * wx["wind_mph"]  # +out / -in (toward CF)
        wind_factor = 1.0 + out_component * 0.004

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
