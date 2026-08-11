"""Derive each home-plate umpire's zone tendency from pitch-tracked calls.

umpires.py had the whole mechanism -- lookup, challenge-aware simulation, a run
effect -- and an EMPTY table, so every umpire was neutral and the entire feature
was a no-op. This builds the table from what actually happened.

METHOD. For every taken pitch (called strike or ball; swings carry no judgment)
compute the distance from the rulebook zone boundary, and keep the ones inside a
shell around the edge -- those are the calls where the umpire is deciding rather
than confirming. An umpire's raw bias is his called-strike rate on that shell
minus the league's. >0 is a big/pitcher-friendly zone.

TWO THINGS THIS GETS RIGHT THAT ARE EASY TO GET WRONG.

  * strikeZoneWidth from StatsAPI is INCHES (17.0), not feet. Reading it as feet
    puts the half-width at 8.5 FEET, every pitch lands "horizontally inside", and
    the shell collapses to a vertical band. The first measurement written here
    did exactly that and concluded umpire bias was indistinguishable from noise.
    With the units right the same sample showed a real signal.

  * The call codes in the feed are POST-REVIEW. An ABS challenge that overturns
    a call changes what the feed reports, so a tendency measured this way already
    has the challenge system netted out -- roughly 6% of borderline calls get
    challenged. No separate damping belongs on top of it; doing that would
    discount the corrections twice.

SHRINKAGE. Most of the spread between umpires at a season's sample size is
sampling noise. The observed spread and the binomial noise floor give the true
between-umpire variance directly (var_true = var_observed - var_noise), and each
umpire is then pulled toward league average by his own sample:

    shrunk = raw * n / (n + K),      K = p(1-p) / var_true

An umpire with few calls barely moves; nobody's zone is taken at face value.
"""

import json
import math
import os
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import baseball
import clock

STATS = "https://statsapi.mlb.com/api/v1"
SHELL_FT = 0.25          # how far either side of the edge still counts as a judgment call
BALL_R_FT = 0.121        # baseball radius (2.9" diameter)
MIN_CALLS = 120          # below this an umpire is not listed at all
_PATH = os.path.join(os.path.dirname(__file__), "ump_tendencies.json")


def _zone_dist(px, pz, top, bot, half_w):
    """Signed feet from the zone boundary: negative inside, positive outside."""
    dx = abs(px) - half_w
    dz = max(bot - pz, pz - top)
    if dx <= 0 and dz <= 0:
        return max(dx, dz)
    return math.hypot(max(dx, 0.0), max(dz, 0.0))


def game_calls(pk):
    """(game_summary, [(umpire, called_strike, dist_ft, was_challenged), ...]).

    The boxscore is fetched anyway for the officials, so the game's strikeout and
    run totals ride along free -- that is what lets the build MEASURE how much a
    zone is worth in Ks and runs instead of asserting a constant."""
    try:
        bx = baseball._get(f"{STATS}/game/{pk}/boxscore")
        ump = next(((o.get("official") or {}).get("fullName")
                    for o in (bx.get("officials") or [])
                    if o.get("officialType") == "Home Plate"), None)
        if not ump:
            return None, []
        tm = bx.get("teams") or {}
        ks = runs = 0
        for side in ("home", "away"):
            st = ((tm.get(side) or {}).get("teamStats") or {})
            ks += baseball._f((st.get("pitching") or {}).get("strikeOuts"))
            runs += baseball._f((st.get("batting") or {}).get("runs"))
        game = {"ump": ump, "k": ks, "r": runs}
        pbp = baseball._get(f"{STATS}/game/{pk}/playByPlay")
    except Exception:
        return None, []
    out = []
    for play in pbp.get("allPlays") or []:
        for e in play.get("playEvents") or []:
            if not e.get("isPitch"):
                continue
            det = e.get("details") or {}
            code = (det.get("call") or {}).get("code")
            if code not in ("C", "B"):
                continue
            pd = e.get("pitchData") or {}
            c = pd.get("coordinates") or {}
            px, pz = c.get("pX"), c.get("pZ")
            top, bot = pd.get("strikeZoneTop"), pd.get("strikeZoneBottom")
            if None in (px, pz, top, bot):
                continue
            w = pd.get("strikeZoneWidth")
            half_w = (w / 24.0) if w else 0.708      # INCHES -> feet, halved
            out.append((ump, code == "C",
                        _zone_dist(px, pz, top, bot, half_w + BALL_R_FT),
                        bool(det.get("hasReview"))))
    return game, out


def _final_pks(start, end):
    d = baseball._get(f"{STATS}/schedule?sportId=1&startDate={start}&endDate={end}"
                      "&fields=dates,games,gamePk,status,abstractGameState")
    return [g["gamePk"] for dd in d.get("dates", []) for g in dd.get("games", [])
            if (g.get("status") or {}).get("abstractGameState") == "Final"]


def build(start=None, end=None, workers=8, progress=None):
    """Walk finished games and return the tendency table plus what it was fitted on."""
    season = str(clock.today_et().year)
    start = start or f"{season}-03-01"
    end = end or clock.today_et().isoformat()
    pks = _final_pks(start, end)
    rows, games = [], []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, (gm, r) in enumerate(ex.map(game_calls, pks)):
            rows.extend(r)
            if gm:
                games.append(gm)
            if progress and i % 50 == 0:
                progress(i, len(pks))
    if not rows:
        return None

    border = [(u, cs, rv) for u, cs, d, rv in rows if abs(d) <= SHELL_FT]
    if len(border) < 5000:
        return None
    lg = sum(1 for _, cs, _ in border if cs) / len(border)
    by = defaultdict(list)
    for u, cs, _rv in border:
        by[u].append(cs)
    umps = {u: v for u, v in by.items() if len(v) >= MIN_CALLS}
    if len(umps) < 10:
        return None

    raw = {u: sum(v) / len(v) - lg for u, v in umps.items()}
    n_mean = sum(len(v) for v in umps.values()) / len(umps)
    var_obs = sum((b - sum(raw.values()) / len(raw)) ** 2 for b in raw.values()) / len(raw)
    var_noise = sum(lg * (1 - lg) / len(v) for v in umps.values()) / len(umps)
    var_true = max(var_obs - var_noise, 1e-6)
    K = lg * (1 - lg) / var_true          # calls needed before half the raw bias survives

    out = {}
    for u, v in umps.items():
        n = len(v)
        out[umpires_norm(u)] = {
            "name": u, "n": n,
            "raw": round(raw[u], 5),
            "bias": round(raw[u] * n / (n + K), 5),
        }
    # WHAT A ZONE IS WORTH, measured rather than assumed. Regress each finished
    # game's strikeout and run totals on its umpire's SHRUNK bias (the number the
    # model will actually apply). The slope is the effect per unit of bias, in
    # whole-game Ks and runs. Games whose umpire has too thin a sample to be
    # listed are dropped -- they carry no bias to regress on.
    # ...AND the standard error on each slope, because a single game's K and run
    # totals are enormously noisy (sd ~4 and ~4.5) against a bias that spans a
    # couple of percent. Without the error bar it is impossible to tell a
    # measured effect from a fitted coincidence -- and on this sample exactly one
    # of the two survives. Consumers gate on significance rather than trusting a
    # point estimate.
    shrunk = {u: raw[u] * len(v) / (len(v) + K) for u, v in umps.items()}
    obs = [(shrunk[g["ump"]], g["k"], g["r"]) for g in games if g["ump"] in shrunk]
    k_per_bias = r_per_bias = k_se = r_se = None
    lg_k = lg_r = None
    if len(obs) >= 300:
        n = len(obs)
        mx = sum(o[0] for o in obs) / n
        sxx = sum((o[0] - mx) ** 2 for o in obs)
        lg_k = sum(o[1] for o in obs) / n
        lg_r = sum(o[2] for o in obs) / n
        if sxx > 0:
            for idx, mean_y in ((1, lg_k), (2, lg_r)):
                slope = sum((o[0] - mx) * (o[idx] - mean_y) for o in obs) / sxx
                resid = sum((o[idx] - mean_y - slope * (o[0] - mx)) ** 2 for o in obs)
                se = math.sqrt(resid / (n - 2) / sxx)
                if idx == 1:
                    k_per_bias, k_se = slope, se
                else:
                    r_per_bias, r_se = slope, se

    meta = {
        "built": clock.now_et().isoformat(timespec="seconds"),
        "start": start, "end": end, "games": len(pks),
        "n_games_regressed": len(obs),
        "lg_k_per_game": round(lg_k, 2) if lg_k else None,
        "lg_r_per_game": round(lg_r, 2) if lg_r else None,
        # slope: whole-game Ks (both staffs) per 1.0 of bias. A bias of 0.02 with
        # a slope of 40 is +0.8 K on the game.
        "k_per_bias": round(k_per_bias, 2) if k_per_bias is not None else None,
        "r_per_bias": round(r_per_bias, 2) if r_per_bias is not None else None,
        "k_se": round(k_se, 2) if k_se is not None else None,
        "r_se": round(r_se, 2) if r_se is not None else None,
        "k_t": round(k_per_bias / k_se, 2) if k_se else None,
        "r_t": round(r_per_bias / r_se, 2) if r_se else None,
        "borderline_per_game": round(len(border) / max(1, len(games)), 1),
        "taken_pitches": len(rows), "borderline": len(border),
        "league_cs_rate": round(lg, 5),
        "challenged_pct": round(100 * sum(1 for _, _, rv in border if rv) / len(border), 2),
        "n_umps": len(umps), "mean_calls": round(n_mean),
        "sd_observed": round(math.sqrt(var_obs), 5),
        "sd_noise": round(math.sqrt(var_noise), 5),
        "sd_true": round(math.sqrt(var_true), 5),
        "shrink_k": round(K),
        "reliability_at_mean_n": round(n_mean / (n_mean + K), 3),
    }
    return {"meta": meta, "umps": out}


def umpires_norm(s):
    import umpires
    return umpires._norm(s)


def save(table, path=_PATH):
    with open(path, "w") as f:
        json.dump(table, f, indent=1, sort_keys=True)
    return path


def load(path=_PATH):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


if __name__ == "__main__":
    import sys
    a = sys.argv[1:]
    t = build(a[0] if a else None, a[1] if len(a) > 1 else None,
              progress=lambda i, n: print(f"  {i}/{n}", flush=True))
    if not t:
        raise SystemExit("not enough data")
    print(json.dumps(t["meta"], indent=1))
    save(t)
    rk = sorted(t["umps"].values(), key=lambda r: -r["bias"])
    print("\nbiggest zones:")
    for r in rk[:6]:
        print(f"   {r['name']:24s} raw {r['raw']:+.4f} -> {r['bias']:+.4f}  (n={r['n']})")
    print("tightest zones:")
    for r in rk[-6:]:
        print(f"   {r['name']:24s} raw {r['raw']:+.4f} -> {r['bias']:+.4f}  (n={r['n']})")
