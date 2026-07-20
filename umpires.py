"""Home-plate umpire zone tendency, made ABS-challenge-aware (2026 rules).

Starting 2026, MLB uses the ABS CHALLENGE system, not a full robot zone: the
human umpire still calls balls and strikes, but each team gets a few challenges
(kept when the challenge succeeds). A challenged pitch is re-judged by Hawk-Eye
against the rulebook zone and the call is corrected if the ump missed it. So an
umpire's personal zone only STICKS on borderline calls that go un-challenged or
survive review -- and the moment a team is out of challenges, that ump's tendency
applies in full again.

This module supplies two things:
  1. plate_umpire(game_pk) -- who is behind the plate (MLB StatsAPI), once posted.
  2. cs_bias(game_pk) -- that umpire's zone lean: net called-strike rate vs the
     league average on borderline pitches. >0 = a big / pitcher-friendly zone
     (more Ks, fewer walks, fewer runs); <0 = a tight / hitter-friendly zone.
     An unknown umpire is 0.0 (no effect), so nothing changes until a real
     tendency is supplied.

The SIMULATOR (deep_sim.play_game) consumes cs_bias with the FULL challenge
mechanic -- it tracks each team's remaining challenges, rolls a challenge on a
decisive borderline call, corrects it on a successful overturn (keeping the
challenge) and burns one on a failed challenge; once a side is out of challenges
the umpire's calls stand. The closed-form model applies the net, challenge-damped
run effect (umps move totals and Ks, and -- since both teams face the same plate
ump -- cancel out of the moneyline, like park/weather).

Per-umpire numbers come from public umpire scorecards; TENDENCIES is a small,
hand-maintained table (empty = every ump neutral until you fill it in). The
mechanism is complete and correct today; populating TENDENCIES makes it predictive.
"""

import json
import re
import time
import urllib.request

_UA = {"User-Agent": "vigil/1.0"}
_FEED = "https://statsapi.mlb.com/api/v1.1/game/{}/feed/live"

# norm_name -> cs_bias (called-strike-rate deviation from league average on
# borderline pitches; roughly [-0.05, 0.05] -- a couple of % is already a notable
# ump). Fill from umpire scorecards, e.g. {"doug eddings": 0.03, "pat hoberg": 0.0}.
# Empty by default so the model is unchanged until real tendencies are supplied.
TENDENCIES = {}

_cache = {}          # game_pk -> (ts, name)
_TTL = 3600          # the crew is set the morning of the game


def _norm(s):
    return re.sub(r"[^a-z ]", "", (s or "").lower()).strip()


def _get(url):
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def plate_umpire(game_pk):
    """Assigned home-plate umpire's full name for a game, or None if not posted
    yet. Cached an hour."""
    hit = _cache.get(game_pk)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    name = None
    try:
        d = _get(_FEED.format(game_pk))
        offs = (((d.get("liveData") or {}).get("boxscore") or {}).get("officials")) or []
        for o in offs:
            if (o.get("officialType") or "") == "Home Plate":
                name = (o.get("official") or {}).get("fullName")
                break
    except Exception:
        name = None
    _cache[game_pk] = (time.time(), name)
    return name


def cs_bias(game_pk):
    """The plate umpire's zone bias for a game: >0 big zone, <0 tight zone. 0.0
    when the umpire isn't posted yet or has no tendency on file (-> no effect)."""
    if not TENDENCIES:            # nothing to look up -> skip the fetch entirely
        return 0.0
    try:
        name = plate_umpire(game_pk)
    except Exception:
        return 0.0
    if not name:
        return 0.0
    return float(TENDENCIES.get(_norm(name), 0.0))
