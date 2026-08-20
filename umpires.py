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
the umpire's calls stand. The closed-form model applies the net run effect and
moves the STRIKEOUT ladder (umps move totals and Ks, and -- since both teams face
the same plate ump -- cancel out of the moneyline, like park/weather).

WHERE THE NUMBERS COME FROM. ump_build.py derives them from pitch-tracked calls:
per umpire, the called-strike rate on pitches within a shell of the rulebook zone
edge, against the league's rate on the same shell. That table is what this module
loads. TENDENCIES stays empty and exists only as a hand override.

TWO PROPERTIES OF THAT TABLE WORTH KNOWING HERE. It is SHRUNK -- most of the
raw spread between umpires is sampling noise (measured 2026: observed sd 0.023,
noise floor 0.017, true sd 0.015), so each man is pulled toward league average by
his own sample and the extremes roughly halve. And it is already NET OF
CHALLENGES, because StatsAPI reports post-review call codes: an overturned call
is counted the way it finally stood. Roughly 6% of borderline calls get
challenged. Applying a further challenge discount on top would take the
corrections twice.
"""

import json
import re
import time
import urllib.request

_UA = {"User-Agent": "vigil/1.0"}
_BOX = "https://statsapi.mlb.com/api/v1/game/{}/boxscore"

# norm_name -> cs_bias, for a hand-set override. Normally EMPTY: the real table
# is derived from pitch-tracked calls by ump_build.py and loaded below. Anything
# put here wins, which is the escape hatch for a mid-season note the data hasn't
# caught up with yet.
TENDENCIES = {}

_cache = {}          # game_pk -> (ts, name)
_TTL = 3600          # the crew is set the morning of the game
_TABLE = {"t": 0.0, "v": None}
_TABLE_TTL = 6 * 3600


def _norm(s):
    return re.sub(r"[^a-z ]", "", (s or "").lower()).strip()


def table():
    """The built tendency table, or None. Re-read from disk every few hours so a
    nightly rebuild reaches a long-running process without a restart."""
    if _TABLE["v"] is not None and time.time() - _TABLE["t"] < _TABLE_TTL:
        return _TABLE["v"]
    try:
        import ump_build
        t = ump_build.load()
    except Exception:
        t = None
    _TABLE["t"], _TABLE["v"] = time.time(), t
    return t


def meta():
    """What the table was fitted on -- sample, spread, shrinkage, and the measured
    Ks/runs per unit of bias. The card and the guards both read this."""
    return (table() or {}).get("meta") or {}


MIN_T = 2.0          # a slope inside its own error bar does not get to move a price


def slope(which):
    """The measured whole-game slope for 'k' or 'r', or None when the regression
    cannot resolve it.

    Both slopes come out of the same fit, and on 2026 to date only one survives:
    strikeouts at +20.2 +/- 9.85 (t = 2.05) and runs at -1.06 +/- 10.73
    (t = -0.10, an interval spanning both signs by a wide margin). A game's run
    total is swamped by everything else that happens in a baseball game; its
    strikeout total is not, quite.

    Note how MARGINAL the surviving one is -- 2.05 against a bar of 2.0. That is
    deliberate rather than embarrassing: the gate is re-evaluated every time the
    table rebuilds, so if another month of games pulls it under the bar the K
    effect switches itself off without anyone editing code, and if it firms up it
    stays. What must never happen is a point estimate being priced while its own
    error bar contains zero."""
    m = meta()
    v, t = m.get(f"{which}_per_bias"), m.get(f"{which}_t")
    if v is None or t is None or abs(t) < MIN_T:
        return None
    return float(v)


def profile(name):
    """{name, bias, raw, n} for an umpire, or None when he isn't in the table."""
    if not name:
        return None
    key = _norm(name)
    if key in TENDENCIES:                       # hand override wins
        return {"name": name, "bias": float(TENDENCIES[key]), "raw": None,
                "n": None, "source": "override"}
    rec = ((table() or {}).get("umps") or {}).get(key)
    if not rec:
        return None
    return {"name": rec.get("name") or name, "bias": rec["bias"],
            "raw": rec.get("raw"), "n": rec.get("n"), "source": "measured"}


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
        # BOXSCORE, not feed/live. Both carry `officials`, but the live feed is
        # the whole game -- every pitch, every play -- and this is called once per
        # game on every slate build. The boxscore is a fraction of the bytes and
        # answers the same question.
        d = _get(_BOX.format(game_pk))
        for o in d.get("officials") or []:
            if (o.get("officialType") or "") == "Home Plate":
                name = (o.get("official") or {}).get("fullName")
                break
    except Exception:
        name = None
    _cache[game_pk] = (time.time(), name)
    return name


def cs_bias(game_pk):
    """The plate umpire's zone bias for a game: >0 big zone, <0 tight zone. 0.0
    when the umpire isn't posted yet or has no tendency on file (-> no effect).

    Already SHRUNK toward league average by his own sample size, and already net
    of ABS challenges -- the call codes the table is built from are post-review,
    so overturned calls are counted the way they finally stood. Applying a
    separate challenge discount on top of this would take the corrections twice.
    """
    if not TENDENCIES and not table():        # nothing to look up -> skip the fetch
        return 0.0
    try:
        p = profile(plate_umpire(game_pk))
    except Exception:
        return 0.0
    return float(p["bias"]) if p else 0.0


def game_profile(game_pk):
    """The umpire behind the plate tonight and what his zone is worth, for the
    card: name, shrunk bias, his raw rate, the sample, and the effect in whole-
    game Ks and runs off the measured slopes. None when nobody is posted yet."""
    try:
        name = plate_umpire(game_pk)
    except Exception:
        return None
    if not name:
        return None
    p = profile(name)
    if not p:
        return {"name": name, "bias": 0.0, "known": False}
    out = {"name": p["name"], "bias": p["bias"], "raw": p.get("raw"),
           "n": p.get("n"), "known": True, "source": p.get("source")}
    kb, rb = slope("k"), slope("r")
    if kb is not None:
        out["k_effect"] = round(kb * p["bias"], 2)      # whole-game Ks, both staffs
    if rb is not None:
        out["r_effect"] = round(rb * p["bias"], 2)      # whole-game runs
    return out
