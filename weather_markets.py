"""Weather edge: Kalshi daily high-temperature markets vs a live-aware forecast.

Kalshi runs daily "high temperature" markets for several cities (a ladder of
strikes: high >= X, high <= X, or high in [X, Y]) that resolve on the actual
observed high. We model the high as a distribution and price each strike, then
flag where Kalshi disagrees.

Depth note: dew point, wind and pressure are already baked into the professional
forecast, so we don't hand-roll meteorology on top. Instead we sharpen the
forecast with *live* signal, which is where the real edge is:
  - today's HIGH SO FAR is a hard floor (the high can't end below it),
  - if the current temp is already running hot/cold vs what the forecast expected
    for this hour, we nudge the projected high in that direction (the traders'
    "heat spike" intuition, quantified),
  - uncertainty (sigma) shrinks as the day progresses toward the afternoon peak.
Current conditions (temp, dew point, wind, pressure) are shown so you can apply
your own read on top.
"""

import math
import datetime
import json
import urllib.request
import time as _time

CITIES = {
    "nyc":  {"label": "New York City", "series": "KXHIGHNY",   "lat": 40.7790, "lon": -73.9693},
    "dc":   {"label": "Washington DC",  "series": "KXHIGHTDC",  "lat": 38.8512, "lon": -77.0402},
    "chi":  {"label": "Chicago",        "series": "KXHIGHCHI",  "lat": 41.9742, "lon": -87.9073},
    "mia":  {"label": "Miami",          "series": "KXHIGHMIA",  "lat": 25.7959, "lon": -80.2870},
    "aus":  {"label": "Austin",         "series": "KXHIGHAUS",  "lat": 30.1975, "lon": -97.6664},
    "den":  {"label": "Denver",         "series": "KXHIGHDEN",  "lat": 39.8467, "lon": -104.6562},
    "sfo":  {"label": "San Francisco",  "series": "KXHIGHTSFO", "lat": 37.6213, "lon": -122.3790},
    "lax":  {"label": "Los Angeles",    "series": "KXHIGHLAX",  "lat": 33.9416, "lon": -118.4085},
    "phil": {"label": "Philadelphia",   "series": "KXHIGHPHIL", "lat": 39.8729, "lon": -75.2407},
    "hou":  {"label": "Houston",        "series": "KXHIGHTHOU", "lat": 29.9902, "lon": -95.3368},
    "lv":   {"label": "Las Vegas",      "series": "KXHIGHTLV",  "lat": 36.0840, "lon": -115.1537},
}

_MON = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
        "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}

_cache = {}

import kalshi  # for BASE + _cents + _f


def _ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _parse_date(ticker):
    try:
        part = ticker.split("-")[1]
        yy = int(part[:2]); mon = _MON[part[2:5]]; dd = int(part[5:7])
        return datetime.date(2000 + yy, mon, dd)
    except Exception:
        return None


def _om_data(lat, lon):
    """Live + forecast bundle from Open-Meteo (cached ~10 min)."""
    key = (round(lat, 2), round(lon, 2))
    hit = _cache.get(key)
    if hit and _time.time() - hit[0] < 600:
        return hit[1]
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat:.4f}&longitude={lon:.4f}"
           "&current=temperature_2m,dew_point_2m,relative_humidity_2m,wind_speed_10m,surface_pressure"
           "&hourly=temperature_2m&daily=temperature_2m_max"
           "&temperature_unit=fahrenheit&wind_speed_unit=mph&timezone=auto&past_days=1&forecast_days=7")
    out = None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "kalshi-betting-helper/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read())
        cur = d.get("current", {})
        now = cur.get("time", "")
        daily = d.get("daily", {})
        highs = dict(zip(daily.get("time", []), daily.get("temperature_2m_max", [])))
        h = d.get("hourly", {})
        today = now[:10]
        todays = [(t, v) for t, v in zip(h.get("time", []), h.get("temperature_2m", []))
                  if t[:10] == today and v is not None]
        high_so_far = max((v for t, v in todays if t <= now), default=None)
        forecast_now = next((v for t, v in zip(h.get("time", []), h.get("temperature_2m", []))
                             if t[:13] == now[:13]), None)
        out = {
            "today": today, "cur_hour": int(now[11:13]) if len(now) >= 13 else 12,
            "current": cur, "daily_highs": highs,
            "high_so_far": high_so_far, "forecast_now": forecast_now,
        }
    except Exception:
        out = None
    _cache[key] = (_time.time(), out)
    return out


def _model_high(om, target_date):
    """Return {mean, sigma, forecast_high, high_so_far, running_delta} or None."""
    fc = om["daily_highs"].get(target_date)
    if fc is None:
        return None
    days_out = (datetime.date.fromisoformat(target_date)
                - datetime.date.fromisoformat(om["today"])).days
    if days_out > 0:                       # future day: forecast only
        return {"mean": round(fc, 1), "sigma": min(6.0, 2.0 + 1.2 * days_out),
                "forecast_high": fc, "high_so_far": None, "running_delta": None}
    if days_out < 0:
        return None

    # Today: sharpen with live signal.
    cur_temp = om["current"].get("temperature_2m")
    running = (cur_temp - om["forecast_now"]) if (cur_temp is not None and om["forecast_now"] is not None) else 0.0
    running_c = max(-5.0, min(5.0, running))
    mean = fc + 0.6 * running_c
    hsf = om["high_so_far"]
    if hsf is not None:
        mean = max(mean, hsf)             # high can't end below what's happened
    hours_to_peak = max(0, 15 - om["cur_hour"])
    sigma = max(1.0, min(4.0, 1.0 + 0.45 * hours_to_peak))
    if hsf is not None and hsf >= fc:     # already at/over forecast -> nearly locked
        sigma = max(0.8, sigma * 0.6)
    return {"mean": round(mean, 1), "sigma": round(sigma, 2), "forecast_high": fc,
            "high_so_far": hsf, "running_delta": round(running, 1)}


def _fair(strike_type, floor, cap, mean, sigma):
    st = (strike_type or "").lower()
    if st in ("greater", "greater_or_equal"):
        return 1 - _ncdf((floor + 0.5 - mean) / sigma)
    if st in ("less", "less_or_equal"):
        return _ncdf((cap - 0.5 - mean) / sigma)
    if st == "between" and floor is not None and cap is not None:
        return max(0.0, _ncdf((cap + 0.5 - mean) / sigma) - _ncdf((floor - 0.5 - mean) / sigma))
    return None


def get_city(city_key):
    cfg = CITIES.get(city_key)
    if not cfg:
        raise ValueError(f"unknown city '{city_key}'")
    data = kalshi._get_json(
        f"{kalshi.BASE}/markets?series_ticker={cfg['series']}&status=open&limit=100")
    markets = data.get("markets", [])
    om = _om_data(cfg["lat"], cfg["lon"])
    cur = (om or {}).get("current", {})
    current = {
        "temp_f": cur.get("temperature_2m"), "dew_point_f": cur.get("dew_point_2m"),
        "humidity_pct": cur.get("relative_humidity_2m"), "wind_mph": cur.get("wind_speed_10m"),
        "pressure_hpa": cur.get("surface_pressure"),
        "high_so_far_f": (om or {}).get("high_so_far"),
    } if om else None

    events = {}
    for m in markets:
        d = _parse_date(m.get("ticker", ""))
        if not d:
            continue
        model = _model_high(om, d.isoformat()) if om else None
        ev = events.setdefault(d.isoformat(), {"date": d.isoformat(), "model": model,
                                               "outcomes": []})
        yes_ask = kalshi._cents(m.get("yes_ask_dollars"))
        fair = edge = None
        if model:
            p = _fair(m.get("strike_type"), kalshi._f(m.get("floor_strike")),
                      kalshi._f(m.get("cap_strike")), model["mean"], model["sigma"])
            if p is not None:
                fair = round(p * 100, 1)
                if yes_ask is not None:
                    edge = round(fair - yes_ask, 1)
        ev["outcomes"].append({
            "name": m.get("yes_sub_title") or m.get("subtitle"),
            "ticker": m.get("ticker"), "yes_ask": yes_ask,
            "fair_pct": fair, "edge_cents": edge,
        })

    out = []
    for ev in events.values():
        ev["outcomes"].sort(key=lambda o: (o["fair_pct"] is None, -(o["fair_pct"] or 0)))
        edges = [o["edge_cents"] for o in ev["outcomes"] if o["edge_cents"] is not None]
        ev["best_edge"] = max(edges) if edges else None
        out.append(ev)
    out.sort(key=lambda e: e["date"])
    return {"city": cfg["label"], "series": cfg["series"], "current": current, "events": out}
