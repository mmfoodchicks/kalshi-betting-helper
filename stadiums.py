"""MLB stadium metadata: location (for weather geocoding), roof type, and the
compass bearing from home plate out to center field (for wind direction).

Keyed by MLB team id. roof:
  'open'        -> weather applies fully
  'retractable' -> weather weighted by the PREDICTED roof state (weather.py)
  'fixed'       -> indoor; weather neutral
cf_bearing_deg: heading from home plate toward center field (0=N, 90=E). Used to
decide whether wind blows out to center (more runs) or in.

PROVENANCE: every row is taken from MLB's own venues API
(statsapi /v1/venues?hydrate=location,fieldInfo) -- surveyed coordinates,
roofType, and location.azimuthAngle (the official home-plate-to-CF compass
heading, verified against known parks: Fenway 45, Tropicana ~0, Truist's
famous southeast orientation 145). The previous bearings were eyeballed and
FIFTEEN of thirty were wrong by more than 15 degrees -- Truist read 30 vs the
real 145 and Comerica 30 vs 150, so wind the model priced as "blowing out to
center" was actually blowing across or in; the Athletics' park sat four miles
from its real location. Regenerate against the API when a park changes.
"""

STADIUMS = {
    108: {"name": "Angel Stadium", "lat": 33.8002, "lon": -117.8824, "roof": "open", "cf_bearing_deg": 44},
    109: {"name": "Chase Field", "lat": 33.4453, "lon": -112.0667, "roof": "retractable", "cf_bearing_deg": 0},
    110: {"name": "Oriole Park at Camden Yards", "lat": 39.2838, "lon": -76.6217, "roof": "open", "cf_bearing_deg": 31},
    111: {"name": "Fenway Park", "lat": 42.3465, "lon": -71.0974, "roof": "open", "cf_bearing_deg": 45},
    112: {"name": "Wrigley Field", "lat": 41.9482, "lon": -87.6555, "roof": "open", "cf_bearing_deg": 37},
    113: {"name": "Great American Ball Park", "lat": 39.0974, "lon": -84.5066, "roof": "open", "cf_bearing_deg": 122},
    114: {"name": "Progressive Field", "lat": 41.4959, "lon": -81.6853, "roof": "open", "cf_bearing_deg": 0},
    115: {"name": "Coors Field", "lat": 39.7560, "lon": -104.9941, "roof": "open", "cf_bearing_deg": 4},
    116: {"name": "Comerica Park", "lat": 42.3391, "lon": -83.0487, "roof": "open", "cf_bearing_deg": 150},
    117: {"name": "Daikin Park", "lat": 29.7570, "lon": -95.3555, "roof": "retractable", "cf_bearing_deg": 343},
    118: {"name": "Kauffman Stadium", "lat": 39.0516, "lon": -94.4805, "roof": "open", "cf_bearing_deg": 46},
    119: {"name": "Dodger Stadium", "lat": 34.0737, "lon": -118.2405, "roof": "open", "cf_bearing_deg": 26},
    120: {"name": "Nationals Park", "lat": 38.8729, "lon": -77.0075, "roof": "open", "cf_bearing_deg": 28},
    121: {"name": "Citi Field", "lat": 40.7575, "lon": -73.8456, "roof": "open", "cf_bearing_deg": 13},
    133: {"name": "Sutter Health Park", "lat": 38.5799, "lon": -121.5125, "roof": "open", "cf_bearing_deg": 46},
    134: {"name": "PNC Park", "lat": 40.4469, "lon": -80.0058, "roof": "open", "cf_bearing_deg": 116},
    135: {"name": "Petco Park", "lat": 32.7079, "lon": -117.1573, "roof": "open", "cf_bearing_deg": 0},
    136: {"name": "T-Mobile Park", "lat": 47.5913, "lon": -122.3325, "roof": "retractable", "cf_bearing_deg": 49},
    137: {"name": "Oracle Park", "lat": 37.7784, "lon": -122.3894, "roof": "open", "cf_bearing_deg": 85},
    138: {"name": "Busch Stadium", "lat": 38.6226, "lon": -90.1929, "roof": "open", "cf_bearing_deg": 62},
    139: {"name": "Tropicana Field", "lat": 27.7678, "lon": -82.6525, "roof": "fixed", "cf_bearing_deg": 359},
    140: {"name": "Globe Life Field", "lat": 32.7473, "lon": -97.0818, "roof": "retractable", "cf_bearing_deg": 30},
    141: {"name": "Rogers Centre", "lat": 43.6416, "lon": -79.3892, "roof": "retractable", "cf_bearing_deg": 345},
    142: {"name": "Target Field", "lat": 44.9818, "lon": -93.2779, "roof": "open", "cf_bearing_deg": 129},
    143: {"name": "Citizens Bank Park", "lat": 39.9054, "lon": -75.1672, "roof": "open", "cf_bearing_deg": 9},
    144: {"name": "Truist Park", "lat": 33.8907, "lon": -84.4676, "roof": "open", "cf_bearing_deg": 145},
    145: {"name": "Rate Field", "lat": 41.8300, "lon": -87.6342, "roof": "open", "cf_bearing_deg": 127},
    146: {"name": "loanDepot park", "lat": 25.7780, "lon": -80.2195, "roof": "retractable", "cf_bearing_deg": 128},
    147: {"name": "Yankee Stadium", "lat": 40.8292, "lon": -73.9265, "roof": "open", "cf_bearing_deg": 75},
    158: {"name": "American Family Field", "lat": 43.0284, "lon": -87.9710, "roof": "retractable", "cf_bearing_deg": 129},
}
