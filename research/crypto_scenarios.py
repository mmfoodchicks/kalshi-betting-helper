"""Up, sideways or down: do the crypto terminal's scenario probabilities beat the random walk? And when is a
direction lean distinguishable from a coin flip? (UI v3, 2026-11-28.) The owner asked for upward / sideways /
downward scenarios with calibrated probabilities, "Direction uncertain" wherever no validated edge separates a
lean from 50%, and random-walk figures kept apart from predictive ones everywhere.

RULE (written and committed before any held-out figure was computed):

  Data and split: research/crypto_moves.py's own -- Coinbase's one-minute candles for BTC, ETH, SOL, XRP and DOGE
  on a contiguous minute grid, development 2026-10-16..11-12 (28 days), held-out 2026-11-13..11-26 (14 days), its
  causal features (`crypto_moves.features`, guard CXM-LEAK). The held-out fortnight was already read ONCE for the
  direction test (moves.json); the scenario question is a different one, scored once here, and the reuse is stated
  in the report.

  A. SCENARIOS. At minute t (the level = close[t]), horizons H = 3 and 5 minutes and CLOSE (the minutes left to
     the next 15-minute boundary, 1..14, pooled -- the 15-minute market's own window), the move
     r = ln(close[t+H] / close[t]). With the meaningful move d = 2 x the EWMA one-minute SD at t (the unit the
     terminal already uses for swings and dips, so "sideways" means no meaningful move): UP if r >= d, DOWN if
     r <= -d, SIDEWAYS otherwise.
     Null: the random walk at production's scale, P(UP) = P(DOWN) = 1 - Phi(d / (VOL_MULT x sd x sqrt H)) --
     which, d being 2 sd, depends on H alone.
     Challenger: a three-class softmax whose logits are ln(null class probability) + beta_c . x for UP and DOWN
     (SIDEWAYS the reference), x = the twelve direction features standardized on development (plus ln H for
     CLOSE), L2 lambda in {1, 10, 100, 1000} chosen on development by forward-chained log loss (fit days 1-14
     score 15-21, fit 1-21 score 22-28, pooled), refitted on all of development, scored ONCE on the held-out
     fortnight against the null.
     VALIDATED only if its held-out log loss beats the null's by more than 2 SE (the 14 held-out days' paired
     daily means), by more than 0.0005 nats a row, and in at least 4 of the 5 coins. A horizon that fails shows
     the null's figures on the page, labelled random walk. Reported on held-out for both models: per class, five
     bins of the said probability, said against hit. "A recovery or a decline is supported by evidence" (the
     page's words) at H only where the challenger is validated there.
     Scenario PATHS are not drawn: probabilities only (the report says why).

  B. DIRECTION UNCERTAIN. For each direction horizon that passed (1, 3, 5 minutes; moves.json's chosen spec):
     the threshold on |p_up - 0.5| at and above which the page shows a lean, chosen on DEVELOPMENT's
     forward-chained out-of-sample predictions (the same folds and spec): bins of |p - 0.5| with edges 0, 0.005,
     0.01, 0.02, 0.03, 0.05, 0.1; the threshold is the lowest edge from which EVERY non-empty bin's hit rate of
     the favoured side exceeds 0.5 by more than 2 SE (clustered by day). No such edge: the page always says
     "Direction uncertain". The held-out hit rates above and below the threshold are reported, never used.

  C. 30 SECONDS stays untested: no sub-minute history is held (moves.json's own note). The page shows
     "Direction uncertain (untested)" there, never a lean.

    python3 research/crypto_moves.py fetch       # the candles, into $CRYPTO_RAW
    python3 research/crypto_scenarios.py         # every figure -> research/data/crypto/scenarios.json
"""
import json
import math
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, ".."))
import crypto_moves as CM  # noqa: E402

OUT = os.path.join(_HERE, "data", "crypto", "scenarios.json")
VOL_MULT = 0.9                 # crypto_terminal.VOL_MULT (guard CXS pins the equality)
HZ = ("3", "5", "close")
LAMS = (1.0, 10.0, 100.0, 1000.0)
EDGES = (0.0, 0.005, 0.01, 0.02, 0.03, 0.05, 0.1)


def _phi(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def null_probs(h):
    """(UP, SIDEWAYS, DOWN) under the walk for a horizon of h minutes: d = 2 sd, so H alone sets it."""
    pu = 1 - _phi(2.0 / (VOL_MULT * math.sqrt(h)))
    return pu, 1 - 2 * pu, pu


def rows(coin):
    g = CM.grid(json.load(open(os.path.join(CM.RAW, f"cbx_{coin}.json"))), CM.START - 6 * 3600, CM.END + 3600)
    f = CM.features(g)
    out = []
    for i, x in enumerate(f):
        if x is None or not CM.START <= g[i][0] < CM.END or not x.get("sig"):
            continue
        c, d, t = g[i][4], 2 * x["sig"], g[i][0]
        ys = {}
        for key in HZ:
            h = (900 - (t + 60) % 900) // 60 if key == "close" else int(key)
            if h < 1 or h > 14 or i + h >= len(g):
                continue
            r = math.log(g[i + h][4] / c)
            ys[key] = (h, 0 if r >= d else (2 if r <= -d else 1))
        out.append((t, coin, x, ys))
    return out


def _design(np, rs, key, mu, sd):
    keep = [r for r in rs if key in r[3]]
    X = np.array([[(r[2][k] - mu[k]) / sd[k] for k in CM.FEATS] for r in keep])
    h = np.array([r[3][key][0] for r in keep], dtype=float)
    if key == "close":
        X = np.column_stack([X, np.log(h)])
    X = np.column_stack([np.ones(len(keep)), X])
    y = np.array([r[3][key][1] for r in keep])
    nb = np.array([null_probs(v) for v in h])
    return keep, X, y, np.log(nb)


def _probs(np, B, X, off):
    z = off.copy()
    z[:, 0] += X @ B[0]
    z[:, 2] += X @ B[1]
    z -= z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return np.clip(e / e.sum(axis=1, keepdims=True), 1e-9, 1.0)


def _fit(np, X, y, off, lam):
    """Newton on the two free classes' coefficients (UP, DOWN), L2 on all but the intercepts."""
    k = X.shape[1]
    B = np.zeros((2, k))
    Y = np.zeros((len(y), 3))
    Y[np.arange(len(y)), y] = 1.0
    pen = np.full(k, lam)
    pen[0] = 0.0
    for _ in range(30):
        P = _probs(np, B, X, off)
        g = np.concatenate([X.T @ (P[:, 0] - Y[:, 0]) + pen * B[0], X.T @ (P[:, 2] - Y[:, 2]) + pen * B[1]])
        H = np.zeros((2 * k, 2 * k))
        cls = (0, 2)
        for a in range(2):
            for b in range(2):
                w = P[:, cls[a]] * ((1.0 if a == b else 0.0) - P[:, cls[b]])
                H[a * k:(a + 1) * k, b * k:(b + 1) * k] = (X * w[:, None]).T @ X
        H += np.diag(np.concatenate([pen, pen]))
        step = np.linalg.solve(H, g)
        B -= step.reshape(2, k)
        if np.max(np.abs(step)) < 1e-8:
            break
    return B


def _ll(np, P, y):
    return -np.log(P[np.arange(len(y)), y])


def _day(t):
    return (t - CM.START) // 86400


def scenarios(np, dev, test):
    mu = {k: float(np.mean([r[2][k] for r in dev])) for k in CM.FEATS}
    sd = {k: float(np.std([r[2][k] for r in dev])) or 1.0 for k in CM.FEATS}
    folds = [(lambda r: _day(r[0]) < 14, lambda r: 14 <= _day(r[0]) < 21),
             (lambda r: _day(r[0]) < 21, lambda r: 21 <= _day(r[0]) < 28)]
    res = {"mu": mu, "sd": sd}
    for key in HZ:
        cv = {}
        for lam in LAMS:
            tot = n = 0.0
            for tr, te in folds:
                _, Xa, ya, oa = _design(np, [r for r in dev if tr(r)], key, mu, sd)
                _, Xb, yb, ob = _design(np, [r for r in dev if te(r)], key, mu, sd)
                B = _fit(np, Xa, ya, oa, lam)
                tot += float(_ll(np, _probs(np, B, Xb, ob), yb).sum())
                n += len(yb)
            cv[str(int(lam))] = round(tot / n, 6)
        lam = float(min(cv, key=cv.get))
        _, Xa, ya, oa = _design(np, dev, key, mu, sd)
        B = _fit(np, Xa, ya, oa, lam)
        kb, Xb, yb, ob = _design(np, test, key, mu, sd)
        Pc, Pn = _probs(np, B, Xb, ob), np.exp(ob)
        d = _ll(np, Pc, yb) - _ll(np, Pn, yb)
        days = np.array([_day(r[0]) for r in kb])
        dm = np.array([d[days == k].mean() for k in sorted(set(days.tolist()))])
        se = float(dm.std(ddof=1) / math.sqrt(len(dm)))
        coin = np.array([r[1] for r in kb])
        by = {c: round(float(d[coin == c].mean()), 6) for c in CM.COINS}
        gain = -float(d.mean())
        passed = gain > 2 * se and gain > 0.0005 and sum(1 for v in by.values() if v < 0) >= 4
        calib = {}
        for name, P in (("null", Pn), ("challenger", Pc)):
            calib[name] = {}
            for ci, cname in enumerate(("up", "sideways", "down")):
                q = np.quantile(P[:, ci], [0.2, 0.4, 0.6, 0.8])
                b = np.searchsorted(q, P[:, ci])
                calib[name][cname] = [{"n": int((b == j).sum()), "said": round(float(P[b == j, ci].mean()), 4),
                                       "hit": round(float((yb[b == j] == ci).mean()), 4)} for j in range(5) if (b == j).any()]
        res[key] = {"cv_dev": cv, "lambda": lam, "heldout_rows": int(len(yb)),
                    "heldout_rates": [round(float((yb == c).mean()), 4) for c in range(3)],
                    "null_rates_mean": [round(float(Pn[:, c].mean()), 4) for c in range(3)],
                    "gain_vs_null_nats": round(gain, 6), "se_days": round(se, 6), "by_coin_diff_vs_null": by,
                    "passed": bool(passed), "weights_up": [round(float(v), 6) for v in B[0]],
                    "weights_down": [round(float(v), 6) for v in B[1]],
                    "weight_names": ["intercept"] + list(CM.FEATS) + (["ln_h"] if key == "close" else []),
                    "calibration": calib}
    return res


def _cluster_se(np, hit, days):
    m = float(hit.mean())
    s = 0.0
    for k in set(days.tolist()):
        sel = days == k
        s += (sel.sum() * (hit[sel].mean() - m)) ** 2
    return math.sqrt(s) / len(hit)


def uncertain(np, dev_by_coin, test_by_coin, moves):
    """B: the |p - 0.5| threshold for a lean, on development's forward-chained predictions."""
    specs = {CM._name(s): s for s in CM._specs()}
    dev = [r for c in CM.COINS for r in dev_by_coin[c]]
    allt = [r for c in CM.COINS for r in test_by_coin[c]]
    folds = [(lambda r: _day(r[0]) < 14, lambda r: 14 <= _day(r[0]) < 21),
             (lambda r: _day(r[0]) < 21, lambda r: 21 <= _day(r[0]) < 28)]
    out = {}
    for hz, d in moves.items():
        if not d.get("passed"):
            continue
        h, spec = int(hz), specs[d["chosen"]]
        P, Y, D = [], [], []
        for tr, te in folds:
            kb, p, y, _ = CM._fit_score(np, [r for r in dev if tr(r)], [r for r in dev if te(r)], h, spec)
            P.append(p)
            Y.append(y)
            D.append(np.array([_day(r[0]) for r in kb]))
        P, Y, D = np.concatenate(P), np.concatenate(Y), np.concatenate(D)
        gap, hit = np.abs(P - 0.5), np.where(P >= 0.5, Y, 1 - Y)
        bins = []
        for a, b in zip(EDGES, EDGES[1:] + (1.0,)):
            sel = (gap >= a) & (gap < b)
            if sel.sum() < 50:
                continue
            m = float(hit[sel].mean())
            bins.append({"from": a, "to": b, "n": int(sel.sum()), "hit": round(m, 4),
                         "se": round(_cluster_se(np, hit[sel], D[sel]), 4)})
        thr = None
        for e in EDGES:
            above = [x for x in bins if x["from"] >= e]
            if above and all(x["hit"] - 0.5 > 2 * x["se"] for x in above):
                thr = e
                break
        kb, p, y, _ = CM._fit_score(np, dev, allt, h, spec)
        gh, hh = np.abs(p - 0.5), np.where(p >= 0.5, y, 1 - y)
        rep = {}
        if thr is not None:
            for lab, sel in (("at_or_above", gh >= thr), ("below", gh < thr)):
                if sel.any():
                    rep[lab] = {"n": int(sel.sum()), "hit": round(float(hh[sel].mean()), 4),
                                "share": round(float(sel.mean()), 4)}
        out[hz] = {"dev_bins": bins, "threshold": thr, "heldout": rep}
    return out


def main():
    import numpy as np
    data = {c: rows(c) for c in CM.COINS}
    dev = [r for c in CM.COINS for r in data[c] if r[0] < CM.SPLIT]
    test = [r for c in CM.COINS for r in data[c] if r[0] >= CM.SPLIT]
    moves = json.load(open(os.path.join(_HERE, "data", "crypto", "moves.json")))["direction"]
    mdata = {c: CM.rows_for(c) for c in CM.COINS}
    out = {"rule": "research/crypto_scenarios.py docstring, committed before scoring",
           "band": "meaningful move = 2 x the EWMA one-minute SD at t", "vol_mult": VOL_MULT,
           "null_by_h": {str(h): [round(v, 4) for v in null_probs(h)] for h in (1, 3, 5, 10, 14)},
           "scenarios": scenarios(np, dev, test),
           "uncertain": uncertain(np, {c: [r for r in mdata[c] if r[0] < CM.SPLIT] for c in CM.COINS},
                                  {c: [r for r in mdata[c] if r[0] >= CM.SPLIT] for c in CM.COINS}, moves),
           "thirty_seconds": "untested: no sub-minute history held"}
    json.dump(out, open(OUT, "w"), indent=1, sort_keys=True)
    s = out["scenarios"]
    print({k: (s[k]["passed"], s[k]["gain_vs_null_nats"], s[k]["se_days"]) for k in HZ},
          {k: v["threshold"] for k, v in out["uncertain"].items()})


def decompose():
    """DESCRIPTIVE, written after the verdict above (2026-11-28) and deciding nothing the rule decided: how much of
    each validated scenario model's held-out gain is HOW FAR the price moves (a meaningful move or sideways) and how
    much is WHICH WAY (up given a meaningful move). The rule calls a recovery or a decline "supported" wherever the
    model passed; the page words that evidence by this split, so a gain that is all volatility is never shown as a
    directional call. Same fits (each horizon's chosen lambda on all of development), same held-out rows."""
    import numpy as np
    data = {c: rows(c) for c in CM.COINS}
    dev = [r for c in CM.COINS for r in data[c] if r[0] < CM.SPLIT]
    test = [r for c in CM.COINS for r in data[c] if r[0] >= CM.SPLIT]
    out = json.load(open(OUT))
    S = out["scenarios"]
    mu, sd = S["mu"], S["sd"]
    dec = {}
    for key in HZ:
        _, Xa, ya, oa = _design(np, dev, key, mu, sd)
        B = _fit(np, Xa, ya, oa, S[key]["lambda"])
        kb, Xb, yb, ob = _design(np, test, key, mu, sd)
        Pc, Pn = _probs(np, B, Xb, ob), np.exp(ob)
        mc, mn = Pc[:, 0] + Pc[:, 2], Pn[:, 0] + Pn[:, 2]
        moved = yb != 1
        mag = np.where(moved, -np.log(mc) + np.log(mn), -np.log(1 - mc) + np.log(1 - mn))
        upc = Pc[:, 0] / mc
        way = np.where(moved, -np.log(np.where(yb == 0, upc, 1 - upc)) + np.log(0.5), 0.0)
        days = np.array([_day(r[0]) for r in kb])
        part = {}
        for name, d in (("how_far", mag), ("which_way", way)):
            dm = np.array([d[days == k].mean() for k in sorted(set(days.tolist()))])
            part[name] = {"gain_nats": round(-float(d.mean()), 6), "se_days": round(float(dm.std(ddof=1) / math.sqrt(len(dm))), 6)}
        part["which_way_supported"] = part["which_way"]["gain_nats"] > 2 * part["which_way"]["se_days"]
        dec[key] = part
    out["decomposition"] = dec
    json.dump(out, open(OUT, "w"), indent=1, sort_keys=True)
    print(dec)


if __name__ == "__main__":
    decompose() if sys.argv[1:] == ["decompose"] else main()
