"""Historical race-day weather per circuit, from Open-Meteo's archive.

For each track we pull ~5 years of daily precipitation + wind in a window around
the race's calendar date, which gives a realistic wet-race probability and a wind
profile (with the natural outliers baked in). The season sim turns that into
random wet-weather events: more chaos, more DNFs, and strategy gambles (the
wrong-tyre-call fiasco) that can scramble the order.
"""

import datetime
import urllib.request
import json

import racing

ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


# NASCAR Cup tracks -> (lat, lon). The NASCAR feed has no coordinates, so this
# curated map drives their race-day climate lookups.
NASCAR_TRACKS = {
    "sonoma": (38.16, -122.45), "chicagoland": (41.47, -88.06),
    "atlanta": (33.39, -84.31), "wilkesboro": (36.13, -81.00),
    "indianapolis": (39.79, -86.23), "iowa": (41.71, -93.03),
    "richmond": (37.59, -77.42), "new hampshire": (43.36, -71.46),
    "daytona": (29.18, -81.07), "darlington": (34.29, -79.91),
    "world wide technology": (38.65, -90.14), "gateway": (38.65, -90.14),
    "bristol": (36.52, -82.26), "kansas": (39.12, -94.83),
    "las vegas": (36.27, -115.01), "charlotte": (35.35, -80.68),
    "phoenix": (33.37, -112.31), "talladega": (33.57, -86.07),
    "martinsville": (36.63, -79.85), "homestead": (25.45, -80.41),
    "pocono": (41.05, -75.51), "michigan": (42.07, -84.24),
    "watkins glen": (42.34, -76.93), "nashville": (35.75, -86.41),
    "dover": (39.19, -75.53), "texas": (33.04, -97.28),
    "circuit of the americas": (30.13, -97.64), "cota": (30.13, -97.64),
}


def nascar_climate(track_name, race_date):
    """Wet-race probability + wind for a NASCAR track by name, or None if the
    track isn't in the coordinate map."""
    t = (track_name or "").lower()
    for key, (lat, lon) in NASCAR_TRACKS.items():
        if key in t:
            return climate(lat, lon, race_date)
    return None


def climate(lat, lon, race_date, window=7):
    """{wet_prob, avg_wind, max_wind} for the race date at (lat, lon).

    wet_prob = fraction of days (within +/-window of the race's calendar date,
    over recent years) with >=1mm rain, scaled to a per-RACE estimate (a rainy
    day isn't a wet 2-hour race). Cached per rounded location + month/day."""
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    md = race_date[5:]                                   # MM-DD
    key = (round(lat, 1), round(lon, 1), md)

    def build():
        end = datetime.date.today().year - 1
        url = (f"{ARCHIVE}?latitude={lat}&longitude={lon}"
               f"&start_date={end-5}-01-01&end_date={end}-12-31"
               "&daily=precipitation_sum,wind_speed_10m_max&timezone=auto")
        try:
            d = _get(url)["daily"]
        except Exception:
            return None
        try:
            tgt = datetime.date(2000, int(md[:2]), int(md[3:5]))
        except ValueError:
            return None
        rain = tot = 0
        winds = []
        for day, p, w in zip(d["time"], d["precipitation_sum"], d["wind_speed_10m_max"]):
            try:
                dd = datetime.date.fromisoformat(day)
            except ValueError:
                continue
            # circular distance in calendar days from the race date
            doy_diff = abs((datetime.date(2000, dd.month, min(dd.day, 28))) - tgt).days
            if doy_diff <= window and p is not None:
                tot += 1
                if p >= 1.0:
                    rain += 1
                if w is not None:
                    winds.append(w)
        if not tot:
            return None
        wet_day = rain / tot
        return {"wet_prob": round(min(0.6, wet_day * 0.62), 3),   # day -> race-window
                "avg_wind": round(sum(winds) / len(winds), 1) if winds else None,
                "max_wind": round(max(winds), 0) if winds else None}
    return racing._cached(("race_climate", key), 30 * 86400, build)
