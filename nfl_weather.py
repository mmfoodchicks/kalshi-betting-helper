"""Game-day weather for the NFL slate: each stadium's kickoff forecast,
fetched through the baseball board's own weather module (weather.get_weather:
NOAA's hourly forecast, Open-Meteo where NOAA has none) and shown on the DFS
tab as INFORMATION -- no number in the stack reads it.

research/nfl_weather_validation.py tested it first, by a rule written before
any number was computed (2026-10-18 13:30 UTC): wind above 10 mph, rain or
snow and cold, fitted on 2022-2024 against Sleeper's team lines and the
field-goal curve, tested on 2025. Nothing passed. The raw bands do lean the
expected way (team passing yards in 15+ mph wind ran about 13% under
Sleeper's projection, calm games about 2%), but the fitted adjustment did not
beat Sleeper's own lines on the held-out year for passing, rushing or kicking
(research/reports/nfl_weather.md). Sleeper and the market's total already move
for the weather; what is left is too small to find in ~130 weathered
team-games a season. So the card shows the forecast and says so, the same way
soccer shows form at weight 0.

The forecasts are fetched by the recorder's prop-log pass (nfl_props_log, the
owner worker, every half hour) for games inside FORECAST_AHEAD_S and frozen
from five minutes before kickoff, so the log also holds each game's last
pre-game forecast -- what a later test of forecasts (not gamebook
conditions) would need. Requests never fetch: /api/nfl/weather reads the log.
"""
import re

FORECAST_AHEAD_S = 72 * 3600     # Open-Meteo serves three days; beyond, it would answer for the wrong hour

# Stadiums by the venue name ESPN's scoreboard carries: (lat, lon, roof).
# roof: "outdoors"; "dome" (fixed roof or SoFi's canopy -- no weather on
# the field); "retractable" (closed in bad weather, so the rule counted it
# closed). Coordinates are the stadium's, to two decimals.
VENUES = {
    "arrowhead stadium": (39.05, -94.48, "outdoors"),
    "geha field at arrowhead stadium": (39.05, -94.48, "outdoors"),
    "highmark stadium": (42.77, -78.79, "outdoors"),
    "hard rock stadium": (25.96, -80.24, "outdoors"),
    "gillette stadium": (42.09, -71.26, "outdoors"),
    "metlife stadium": (40.81, -74.07, "outdoors"),
    "m t bank stadium": (39.28, -76.62, "outdoors"),
    "paycor stadium": (39.10, -84.52, "outdoors"),
    "huntington bank field": (41.51, -81.70, "outdoors"),
    "cleveland browns stadium": (41.51, -81.70, "outdoors"),
    "acrisure stadium": (40.45, -80.02, "outdoors"),
    "nrg stadium": (29.68, -95.41, "retractable"),
    "lucas oil stadium": (39.76, -86.16, "retractable"),
    "everbank stadium": (30.32, -81.64, "outdoors"),
    "nissan stadium": (36.17, -86.77, "outdoors"),
    "empower field at mile high": (39.74, -105.02, "outdoors"),
    "allegiant stadium": (36.09, -115.18, "dome"),
    "sofi stadium": (33.95, -118.34, "dome"),
    "at t stadium": (32.75, -97.09, "retractable"),
    "lincoln financial field": (39.90, -75.17, "outdoors"),
    "northwest stadium": (38.91, -76.86, "outdoors"),
    "soldier field": (41.86, -87.62, "outdoors"),
    "ford field": (42.34, -83.05, "dome"),
    "lambeau field": (44.50, -88.06, "outdoors"),
    "u s bank stadium": (44.97, -93.26, "dome"),
    "mercedes benz stadium": (33.76, -84.40, "retractable"),
    "bank of america stadium": (35.23, -80.85, "outdoors"),
    "caesars superdome": (29.95, -90.08, "dome"),
    "raymond james stadium": (27.98, -82.50, "outdoors"),
    "state farm stadium": (33.53, -112.26, "retractable"),
    "levi s stadium": (37.40, -121.97, "outdoors"),
    "lumen field": (47.60, -122.33, "outdoors"),
    # the international series
    "wembley stadium": (51.56, -0.28, "outdoors"),
    "tottenham hotspur stadium": (51.60, -0.07, "outdoors"),
    "allianz arena": (48.22, 11.62, "outdoors"),
    "deutsche bank park": (50.07, 8.65, "outdoors"),
    "olympiastadion": (52.51, 13.24, "outdoors"),
    "olympiastadion berlin": (52.51, 13.24, "outdoors"),
    "estadio azteca": (19.30, -99.15, "outdoors"),
    "estadio banorte": (19.30, -99.15, "outdoors"),
    "neo quimica arena": (-23.55, -46.47, "outdoors"),
    "arena corinthians": (-23.55, -46.47, "outdoors"),
    "santiago bernabeu": (40.45, -3.69, "retractable"),
    "estadio santiago bernabeu": (40.45, -3.69, "retractable"),
    "croke park": (53.36, -6.25, "outdoors"),
    "melbourne cricket ground": (-37.82, 144.98, "outdoors"),
    "maracana": (-22.91, -43.23, "outdoors"),
}


def _key(name):
    s = re.sub(r"[^a-z0-9]+", " ", (name or "").lower().replace("é", "e").replace("í", "i"))
    return " ".join(s.split())


def venue(name):
    """(lat, lon, roof) for an ESPN venue name, or None for one not tabled."""
    return VENUES.get(_key(name))


def forecast(game, fetch=None):
    """The kickoff forecast for one game of nfl_props_log.espn_games:
    {"venue", "roof", "wind_mph", "temp_f", "precip_pct", "summary",
    "source"} -- weather fields only for an open-air stadium; None for a venue
    not in the table."""
    v = venue(game.get("venue"))
    if v is None:
        return None
    lat, lon, roof = v
    out = {"venue": game.get("venue"), "roof": roof}
    if roof != "outdoors":
        return out
    if fetch is None:
        import weather
        fetch = weather.get_weather
    wx = fetch(lat, lon, game["kickoff"]) or {}
    for k in ("wind_mph", "temp_f", "precip_pct", "summary", "source", "wind_dir"):
        if wx.get(k) is not None:
            out[k] = wx[k]
    return out


def flags(wx):
    """The conditions the tested rule named, as words for the card: wind over
    10 mph, a 50%+ chance of rain or snow, under 40F."""
    if not wx or wx.get("roof") != "outdoors" or wx.get("wind_mph") is None:
        return []
    out = []
    if float(wx.get("wind_mph") or 0) > 10:
        out.append(f"wind {round(float(wx['wind_mph']))} mph")
    if float(wx.get("precip_pct") or 0) >= 50:
        out.append(f"{round(float(wx['precip_pct']))}% precipitation")
    if wx.get("temp_f") is not None and float(wx["temp_f"]) < 40:
        out.append(f"{round(float(wx['temp_f']))}F")
    return out


def board():
    """The latest logged week's games with their forecasts, soonest first
    (the route's payload). Reads the deep store only."""
    import deep_cache
    import nfl_props_log
    key, _ts = deep_cache.load(nfl_props_log.LATEST)
    if not key:
        return {"games": [], "why": "no forecast logged yet (the recorder fetches them every half hour)"}
    st, ts = deep_cache.load(key)
    rows = []
    for gid, w in ((st or {}).get("weather") or {}).items():
        rows.append(dict(w, id=gid, flags=flags(w.get("wx"))))
    rows.sort(key=lambda r: (r.get("kickoff") or 0, r.get("label") or ""))
    return {"season": (st or {}).get("season"), "week": (st or {}).get("week"), "games": rows, "ts": ts,
            "note": ("Information only: a held-out test (research/reports/nfl_weather.md) found no weather "
                     "adjustment that beat Sleeper's own lines for passing, rushing or field goals, so no "
                     "projection reads these.")}
