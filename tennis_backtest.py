"""Point-in-time backtest for the tennis model.

Tennis is the cleanest sport to replay: the Match Charting Project gives every
charted match with a date and per-match serve/return counts, so a player's
rating "as of" any date is just the matches before it. We walk the archive in
date order, and for each match predict it from ONLY what had already been played
— then compare to who actually won.

No market benchmark exists here (ESPN publishes no tennis odds and Kalshi has no
history we can read back), so unlike the ball sports this can't answer "do we
beat the price." What it CAN do is the thing tennis most needs: produce hundreds
of graded (probability, outcome) pairs so calibrate.py can fit a real
temperature, instead of the model running uncalibrated forever. It also scores
against a naive baseline — pick whoever has the better career serve-minus-return
rating — because a model has to beat the obvious alternative to be worth having.

Incremental by construction: player accumulators are updated AFTER each match is
predicted, so the walk is O(matches), not a rebuild per match.
"""

import math
from collections import defaultdict

import tennis_data as td
import tennis_sim as ts

_MIN_MATCHES = 8          # a player needs some charted history before we rate him
_K = 60.0                 # points of shrinkage toward the tour average


def _load(tour):
    """[(date, winner, loser, surface, {per-player serve/return counts})] in date
    order, straight from the charting archive."""
    matches = td._rows(td._fetch_csv(f"charting-{tour}-matches.csv"))
    overview = td._rows(td._fetch_csv(f"charting-{tour}-stats-Overview.csv"))
    # Overview has one row per player per match ('Total' set), keyed by the
    # player's NAME (not a 1/2 slot), and carries return counts directly.
    stats = defaultdict(dict)
    for r in overview:
        if (r.get("set") or "").strip().lower() != "total":
            continue
        mid = r.get("match_id")
        nm = td._norm(r.get("player") or "")
        if not mid or not nm:
            continue
        try:
            stats[mid][nm] = {
                "sp": float(r.get("serve_pts") or 0),
                "spw": float(r.get("first_won") or 0) + float(r.get("second_won") or 0),
                "rp": float(r.get("return_pts") or 0),
                "rpw": float(r.get("return_pts_won") or 0),
            }
        except (TypeError, ValueError):
            continue
    out = []
    for m in matches:
        mid = m.get("match_id")
        st = stats.get(mid) or {}
        d = (m.get("Date") or "").strip()
        if len(d) != 8 or not d.isdigit():
            continue
        p1, p2 = td._norm(m.get("Player 1") or ""), td._norm(m.get("Player 2") or "")
        if not p1 or not p2 or p1 == p2 or p1 not in st or p2 not in st:
            continue
        out.append({"date": d, "w": p1, "l": p2,           # charting names the winner first
                    "surface": td._surface_of(m.get("Surface")),
                    "s1": st[p1], "s2": st[p2]})
    out.sort(key=lambda r: r["date"])
    return out


def _metrics(pairs):
    n = len(pairs)
    if not n:
        return {"n": 0}
    acc = sum(1 for p, y in pairs if (p >= 0.5) == bool(y)) / n
    brier = sum((p - y) ** 2 for p, y in pairs) / n
    ll = -sum(math.log(max(1e-9, p if y else 1 - p)) for p, y in pairs) / n
    return {"n": n, "acc": round(acc, 4), "brier": round(brier, 4),
            "logloss": round(ll, 4)}


def run(tour="m", since="20200101", limit=None, sims=600):
    """Replay the archive and score the model against actual results."""
    rows = _load(tour)
    rows = [r for r in rows if r["date"] >= since]
    if limit:
        rows = rows[:limit]
    if not rows:
        return {"error": "no matches"}
    # Running per-player totals from matches ALREADY played.
    sp, spw, rp, rpw, n_m = (defaultdict(float), defaultdict(float),
                             defaultdict(float), defaultdict(float), defaultdict(int))
    model, base = [], []
    graded = []
    lg_spw = 0.62
    for r in rows:
        a, b = r["w"], r["l"]
        if n_m[a] >= _MIN_MATCHES and n_m[b] >= _MIN_MATCHES:
            tot_sp = sum(sp.values()) or 1.0
            lg_spw = (sum(spw.values()) / tot_sp) or 0.62

            def rate(p):
                s = (spw[p] + _K * lg_spw) / (sp[p] + _K)
                rr = (rpw[p] + _K * (1 - lg_spw)) / (rp[p] + _K)
                return {"spw": s, "rpw": rr, "ace": 0.06, "df": 0.04}
            lg = {"spw": lg_spw, "rpw": 1 - lg_spw}
            # Randomise which side is "A" so a winner-first archive can't leak.
            flip = (hash(r["date"] + a) % 2 == 0)
            x, y_ = (a, b) if flip else (b, a)
            simres = ts.simulate(rate(x), rate(y_), lg, best_of=3, n=sims, seed=11)
            p_x = simres["p_a"] / 100.0
            won = 1 if x == a else 0
            model.append((p_x, won))
            graded.append((p_x, won))
            # Naive baseline: better career serve-minus-return differential.
            dx = (spw[x] / (sp[x] or 1)) - (rpw[y_] / (rp[y_] or 1))
            dy = (spw[y_] / (sp[y_] or 1)) - (rpw[x] / (rp[x] or 1))
            base.append((0.5 + max(-0.45, min(0.45, (dx - dy) * 1.5)), won))
        # fold the match in AFTER predicting it
        for who, s_self in ((a, r["s1"]), (b, r["s2"])):
            sp[who] += s_self["sp"]; spw[who] += s_self["spw"]
            rp[who] += s_self["rp"]; rpw[who] += s_self["rpw"]
            n_m[who] += 1
    return {"tour": tour, "since": since, "matches_scored": len(model),
            "model": _metrics(model), "baseline_serve_diff": _metrics(base),
            "verdict": ("model beats the naive baseline"
                        if (_metrics(model).get("logloss") or 9) <
                           (_metrics(base).get("logloss") or 9)
                        else "model does not beat the naive baseline"),
            "_graded": graded}


def graded_pairs(tour="m", since="20200101", limit=None):
    """[(prob, outcome)] for calibrate.py — lets tennis fit a real temperature
    from archive history instead of waiting on settled markets."""
    r = run(tour, since, limit)
    return r.get("_graded") or []
