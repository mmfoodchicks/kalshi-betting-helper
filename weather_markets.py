"""Weather edge: Kalshi daily high-temperature markets vs the NOAA forecast.

Kalshi runs daily "high temperature" markets for several cities (a ladder of
strikes: high >= X, high <= X, or high in [X, Y]). These resolve on the actual
observed high. We already pull NOAA forecasts, which carry a well-understood
error (~a couple degrees a day out), so we can model the high as a normal
distribution around the forecast, compute a fair probability for each strike,
and flag where Kalshi's price disagrees -- a genuine edge from a sharper source.

Note: each city resolves at a specific official station; our coordinates are
close approximations, so treat the temperatures as forecasts, not gospel.
"""

import math
import datetime

import weather
import kalshi

# Kalshi high-temp series -> city label + approx station coordinates.
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


def _ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _parse_date(ticker):
    """KXHIGHTSFO-26JUN20-T72 -> date(2026, 6, 20)."""
    try:
        part = ticker.split("-")[1]            # '26JUN20'
        yy = int(part[:2]); mon = _MON[part[2:5]]; dd = int(part[5:7])
        return datetime.date(2000 + yy, mon, dd)
    except Exception:
        return None


def _forecast_highs(lat, lon):
    """{date_iso: forecast_high_F} from NOAA's daily forecast (cached)."""
    key = (round(lat, 2), round(lon, 2))
    hit = _cache.get(key)
    import time as _t
    if hit and _t.time() - hit[0] < 1800:
        return hit[1]
    highs = {}
    try:
        pts = weather._get_json(f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}")
        daily = weather._get_json(pts["properties"]["forecast"])
        for p in daily["properties"]["periods"]:
            if p.get("isDaytime") and p.get("temperature") is not None:
                highs[p["startTime"][:10]] = p["temperature"]
    except Exception:
        highs = {}
    _cache[key] = (_t.time(), highs)
    return highs


def _sigma(days_out):
    # Forecast error grows with lead time; ~2F same-day, ~3F next-day, etc.
    return min(6.0, 2.0 + 1.2 * max(0, days_out))


def _fair(strike_type, floor, cap, mean, sigma):
    st = (strike_type or "").lower()
    if st in ("greater", "greater_or_equal"):     # "F+1 or above" -> high > floor
        return 1 - _ncdf((floor + 0.5 - mean) / sigma)
    if st in ("less", "less_or_equal"):           # "cap-1 or below" -> high < cap
        return _ncdf((cap - 0.5 - mean) / sigma)
    if st == "between" and floor is not None and cap is not None:
        return max(0.0, _ncdf((cap + 0.5 - mean) / sigma) - _ncdf((floor - 0.5 - mean) / sigma))
    return None


def get_city(city_key, min_edge=7.0):
    cfg = CITIES.get(city_key)
    if not cfg:
        raise ValueError(f"unknown city '{city_key}'")
    data = kalshi._get_json(
        f"{kalshi.BASE}/markets?series_ticker={cfg['series']}&status=open&limit=100")
    markets = data.get("markets", [])
    if not markets:
        return {"city": cfg["label"], "series": cfg["series"], "events": []}

    highs = _forecast_highs(cfg["lat"], cfg["lon"])
    today = datetime.datetime.utcnow().date()
    events = {}
    for m in markets:
        d = _parse_date(m.get("ticker", ""))
        if not d:
            continue
        fc = highs.get(d.isoformat())
        ev = events.setdefault(d.isoformat(), {"date": d.isoformat(), "forecast_high": fc,
                                               "outcomes": []})
        yes_ask = kalshi._cents(m.get("yes_ask_dollars"))
        fair = None
        edge = None
        if fc is not None:
            sigma = _sigma((d - today).days)
            p = _fair(m.get("strike_type"), kalshi._f(m.get("floor_strike")),
                      kalshi._f(m.get("cap_strike")), fc, sigma)
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
        # Best edge in the event, for highlighting.
        edges = [o["edge_cents"] for o in ev["outcomes"] if o["edge_cents"] is not None]
        ev["best_edge"] = max(edges) if edges else None
        out.append(ev)
    out.sort(key=lambda e: e["date"])
    return {"city": cfg["label"], "series": cfg["series"], "min_edge": min_edge, "events": out}
