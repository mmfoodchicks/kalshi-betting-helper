"""MLB stadium metadata: location (for weather geocoding), roof type, and the
compass bearing from home plate out to center field (for wind direction).

Keyed by MLB team id. Coordinates are approximate (good to within a stadium --
plenty precise for a weather grid). roof:
  'open'        -> weather applies fully
  'retractable' -> weather applied at reduced weight (roof state unknown)
  'fixed'       -> indoor; weather neutral
cf_bearing_deg: heading from home plate toward center field (0=N, 90=E). Used to
decide whether wind blows out to center (more runs) or in.
"""

STADIUMS = {
    108: {"name": "Angel Stadium", "lat": 33.8003, "lon": -117.8827, "roof": "open", "cf_bearing_deg": 90},
    109: {"name": "Chase Field", "lat": 33.4455, "lon": -112.0667, "roof": "retractable", "cf_bearing_deg": 0},
    110: {"name": "Camden Yards", "lat": 39.2839, "lon": -76.6217, "roof": "open", "cf_bearing_deg": 60},
    111: {"name": "Fenway Park", "lat": 42.3467, "lon": -71.0972, "roof": "open", "cf_bearing_deg": 45},
    112: {"name": "Wrigley Field", "lat": 41.9484, "lon": -87.6553, "roof": "open", "cf_bearing_deg": 30},
    113: {"name": "Great American Ball Park", "lat": 39.0975, "lon": -84.5069, "roof": "open", "cf_bearing_deg": 100},
    114: {"name": "Progressive Field", "lat": 41.4962, "lon": -81.6852, "roof": "open", "cf_bearing_deg": 0},
    115: {"name": "Coors Field", "lat": 39.7559, "lon": -104.9942, "roof": "open", "cf_bearing_deg": 0},
    116: {"name": "Comerica Park", "lat": 42.3390, "lon": -83.0485, "roof": "open", "cf_bearing_deg": 30},
    117: {"name": "Minute Maid Park", "lat": 29.7572, "lon": -95.3556, "roof": "retractable", "cf_bearing_deg": 345},
    118: {"name": "Kauffman Stadium", "lat": 39.0517, "lon": -94.4803, "roof": "open", "cf_bearing_deg": 0},
    119: {"name": "Dodger Stadium", "lat": 34.0739, "lon": -118.2400, "roof": "open", "cf_bearing_deg": 25},
    120: {"name": "Nationals Park", "lat": 38.8730, "lon": -77.0074, "roof": "open", "cf_bearing_deg": 30},
    121: {"name": "Citi Field", "lat": 40.7571, "lon": -73.8458, "roof": "open", "cf_bearing_deg": 30},
    133: {"name": "Sutter Health Park", "lat": 38.6403, "lon": -121.5135, "roof": "open", "cf_bearing_deg": 30},
    134: {"name": "PNC Park", "lat": 40.4469, "lon": -80.0057, "roof": "open", "cf_bearing_deg": 120},
    135: {"name": "Petco Park", "lat": 32.7073, "lon": -117.1566, "roof": "open", "cf_bearing_deg": 0},
    136: {"name": "T-Mobile Park", "lat": 47.5914, "lon": -122.3325, "roof": "retractable", "cf_bearing_deg": 0},
    137: {"name": "Oracle Park", "lat": 37.7786, "lon": -122.3893, "roof": "open", "cf_bearing_deg": 60},
    138: {"name": "Busch Stadium", "lat": 38.6226, "lon": -90.1928, "roof": "open", "cf_bearing_deg": 0},
    139: {"name": "Tropicana Field", "lat": 27.7683, "lon": -82.6534, "roof": "fixed", "cf_bearing_deg": 0},
    140: {"name": "Globe Life Field", "lat": 32.7473, "lon": -97.0847, "roof": "retractable", "cf_bearing_deg": 0},
    141: {"name": "Rogers Centre", "lat": 43.6414, "lon": -79.3894, "roof": "retractable", "cf_bearing_deg": 0},
    142: {"name": "Target Field", "lat": 44.9817, "lon": -93.2776, "roof": "open", "cf_bearing_deg": 90},
    143: {"name": "Citizens Bank Park", "lat": 39.9061, "lon": -75.1665, "roof": "open", "cf_bearing_deg": 0},
    144: {"name": "Truist Park", "lat": 33.8908, "lon": -84.4678, "roof": "open", "cf_bearing_deg": 30},
    145: {"name": "Rate Field", "lat": 41.8299, "lon": -87.6338, "roof": "open", "cf_bearing_deg": 120},
    146: {"name": "loanDepot Park", "lat": 25.7781, "lon": -80.2197, "roof": "retractable", "cf_bearing_deg": 30},
    147: {"name": "Yankee Stadium", "lat": 40.8296, "lon": -73.9262, "roof": "open", "cf_bearing_deg": 30},
    158: {"name": "American Family Field", "lat": 43.0280, "lon": -87.9712, "roof": "retractable", "cf_bearing_deg": 0},
}
