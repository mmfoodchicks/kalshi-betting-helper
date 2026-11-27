"""Does any technical indicator forecast short crypto moves? The crypto terminal v2's movement research
(2026-11-27). The owner: "I also want to know when it's gonna change direction so I can sell the over and
buy the under". Nothing here is assumed to work; everything is tested against benchmarks.

RULE (written and committed before any held-out figure was computed):

  A. DIRECTION at 1, 3 and 5 minutes.
     Data: Coinbase's public one-minute candles for BTC, ETH, SOL, XRP and DOGE, 2026-10-16 00:00 to
     2026-11-27 00:00 UTC, on a contiguous minute grid (a minute with no trade carries the previous close,
     volume 0). Development = 2026-10-16..11-12 (28 days); held-out = 2026-11-13..11-26 (14 days).
     Target at minute t: 1 if close[t+h] > close[t], 0 if below, the row dropped if equal.
     Features at t use candles up to and including t only (`features`, one causal pass; guard CXM-LEAK
     perturbs every later candle and requires every earlier feature unchanged), after 300 minutes of warm-up:
     momentum r1/r3/r5/r15 (z-scored by the EWMA one-minute volatility), acceleration, RSI(14), MACD
     histogram (12/26/9), EMA 5-21 gap, price-EMA9 gap, the volatility regime (EWMA vol, 15-minute half-life
     over 240-minute), volume against its 60-minute mean, and the position in the 60-minute range (the
     support/resistance proxy).
     Models: M0 the random walk (0.5); M1 persistence (a logistic on the sign of the trailing h-minute move);
     one logistic per indicator family; M3 every feature (L2, lambda in {1, 10, 100, 1000}). Each model's
     inputs are the features above per coin pooled. Chosen per horizon on development only, by forward-
     chained log loss (fit days 1-14 score 15-21, fit 1-21 score 22-28, pooled). The chosen model is then
     refitted on all 28 development days and scored ONCE on the held-out fortnight against M0 and M1.
     Passes only if its held-out log loss beats M0 by more than 2 SE (SE over the 14 held-out days of the
     daily paired mean -- minutes overlap, days roughly do not) AND by more than 0.0005 nats a row AND in at
     least 4 of the 5 coins. A failed horizon shows no forecast past the random walk on the panel.
     REVERSAL at h = the chosen direction model's P(the next h minutes go against the trailing 5-minute
     move); its calibration on the held-out fortnight is reported (ten bins).
     Untested and said so: the 30-second horizon (no sub-minute history is held), order-book imbalance and
     cross-exchange confirmation (no history of either is held).

  B. SETTLEMENT probability with a drift. v1's model (research/crypto_validation.py) shifted by beta x the
     trailing 5-minute log return, beta in {-0.5, -0.25, -0.1, 0, 0.1, 0.25, 0.5}, chosen on v1's
     development week (15-minute markets closing 2026-11-13..19) at the 300 s look; scored on v1's held-out
     week (11-20..26), which v1 scored once and no v2 candidate has touched, against v1 (beta 0) and Kalshi's
     mid. Ships only if beta != 0 and it beats v1 there by more than 2 SE (rows).

  C. INTRAWINDOW paper trading on v1's frame, with Kalshi's real quotes at the looks: enter the side whose
     fee-in edge at the ask is 3c or more at the 600 s look; exit at the 300 s look at that side's bid
     (NO bid = 100 - YES ask), fee on both legs. Mean P/L a trade, its SE, drawdown and turnover,
     development and held-out week. Profitable only if the held-out mean exceeds 2 SE.

  D. THE PROXY, descriptive (nothing changes automatically): the official sixty-second average (each 15-
     minute market's floor_strike and expiration_value) against Coinbase's typical price (O+H+L+C)/4, its
     (O+C)/2, its close, Bitstamp's typical price and the mean of the two exchanges, over the same minute,
     RMS in bp per coin, both weeks.

    python3 research/crypto_moves.py fetch    # candles into $CRYPTO_RAW (default ~/.cache/vigil-crypto)
    python3 research/crypto_moves.py score    # every figure -> research/data/crypto/moves.json
"""
import json
import math
import os
import sys
import time
import urllib.request
from collections import deque
from datetime import datetime, timezone

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("VIGIL_NO_BG", "1")

RAW = os.environ.get("CRYPTO_RAW") or os.path.expanduser("~/.cache/vigil-crypto")
OUT = os.path.join(_ROOT, "research", "data", "crypto", "moves.json")
COINS = ("BTC", "ETH", "SOL", "XRP", "DOGE")
START = int(datetime(2026, 10, 16, tzinfo=timezone.utc).timestamp())
SPLIT = int(datetime(2026, 11, 13, tzinfo=timezone.utc).timestamp())
END = int(datetime(2026, 11, 27, tzinfo=timezone.utc).timestamp())
WARM = 300
HORIZONS = (1, 3, 5)
FEATS = ("m1", "m3", "m5", "m15", "acc", "rsi", "macd", "ema_gap", "px_ema9", "vol_reg", "vol_z", "range_pos")
FAMILIES = {"momentum": ("m1", "m3", "m5", "m15"), "acceleration": ("acc",), "rsi": ("rsi",), "macd": ("macd",),
            "ema": ("ema_gap", "px_ema9"), "vol_regime": ("vol_reg", "m1"), "volume": ("vol_z", "m1"),
            "range": ("range_pos",)}


def _get(url):
    for k in range(6):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "research", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code != 429 or k == 5:
                raise
            time.sleep(2.0 * (k + 1))


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch():
    os.makedirs(RAW, exist_ok=True)
    import crypto_terminal as CT
    for coin in COINS:
        p = os.path.join(RAW, f"cbx_{coin}.json")
        have = {int(r[0]): r for r in json.load(open(p))} if os.path.exists(p) else {}
        t = START - 6 * 3600
        while t < END:
            t2 = min(END, t + 300 * 60)
            if not any(x in have for x in range(t, t2, 60)):
                for r in _get(f"https://api.exchange.coinbase.com/products/{CT.COINS[coin]['coinbase']}/candles"
                              f"?granularity=60&start={_iso(t)}&end={_iso(t2 - 60)}"):
                    have[int(r[0])] = r
                time.sleep(0.15)
            t = t2
        json.dump(sorted(have.values()), open(p, "w"))
        b = os.path.join(RAW, f"bs_{coin}.json")
        bs = {int(r[0]): r for r in json.load(open(b))} if os.path.exists(b) else {}
        t = SPLIT - 3600
        while t < END:
            if t not in bs:
                d = _get(f"https://www.bitstamp.net/api/v2/ohlc/{CT.COINS[coin]['bitstamp']}/?step=60&limit=1000&start={t}")
                for r in (d.get("data") or {}).get("ohlc") or []:
                    bs[int(r["timestamp"])] = [int(r["timestamp"]), float(r["open"]), float(r["high"]), float(r["low"]),
                                               float(r["close"]), float(r["volume"])]
                time.sleep(0.3)
            t += 1000 * 60
        json.dump(sorted(bs.values()), open(b, "w"))
        print(coin, len(have), "coinbase minutes", len(bs), "bitstamp minutes", flush=True)


def grid(rows, t0, t1):
    """Coinbase rows [t, low, high, open, close, vol] -> contiguous (t, o, h, l, c, v) from t0 to t1; a
    missing minute carries the previous close, volume 0."""
    by = {int(r[0]): r for r in rows}
    out, last = [], None
    for t in range(t0, t1, 60):
        r = by.get(t)
        if r:
            last = float(r[4])
            out.append((t, float(r[3]), float(r[2]), float(r[1]), last, float(r[5])))
        elif last is not None:
            out.append((t, last, last, last, last, 0.0))
    return out


def features(g):
    """One causal pass over a minute grid: the feature dict at each index uses candles 0..i only (None
    during the warm-up). Every recursion is a running EMA or a bounded window, so this is also how an
    incremental indicator computes it."""
    a5, a9, a12, a21, a26 = (2 / 6.0, 2 / 10.0, 2 / 13.0, 2 / 22.0, 2 / 27.0)
    l60, l15, l240 = (1 - 0.5 ** (1 / 60.0), 1 - 0.5 ** (1 / 15.0), 1 - 0.5 ** (1 / 240.0))
    out = []
    e5 = e9 = e12 = e21 = e26 = sig = None
    v60 = v15 = v240 = 0.0
    ag = al = 0.0
    prev_r1 = 0.0
    closes = deque(maxlen=16)
    hi, lo = deque(), deque()
    vols, vsum = deque(), 0.0
    for i, (t, o, h, low, c, v) in enumerate(g):
        if e5 is None:
            e5 = e9 = e12 = e21 = e26 = c
            sig = 0.0
        e5 += a5 * (c - e5)
        e9 += a9 * (c - e9)
        e12 += a12 * (c - e12)
        e21 += a21 * (c - e21)
        e26 += a26 * (c - e26)
        macd = e12 - e26
        sig = macd if i == 0 else sig + a9 * (macd - sig)
        r1 = math.log(c / closes[-1]) if closes else 0.0
        v60 += l60 * (r1 * r1 - v60)
        v15 += l15 * (r1 * r1 - v15)
        v240 += l240 * (r1 * r1 - v240)
        ag += (max(r1, 0.0) - ag) / 14.0
        al += (max(-r1, 0.0) - al) / 14.0
        closes.append(c)
        while hi and hi[-1][1] <= h:
            hi.pop()
        hi.append((i, h))
        while lo and lo[-1][1] >= low:
            lo.pop()
        lo.append((i, low))
        while hi[0][0] <= i - 60:
            hi.popleft()
        while lo[0][0] <= i - 60:
            lo.popleft()
        vols.append(v)
        vsum += v
        if len(vols) > 60:
            vsum -= vols.popleft()
        if i < WARM or v60 <= 0:
            out.append(None)
            prev_r1 = r1
            continue
        s = math.sqrt(v60)
        k = lambda n: math.log(c / closes[-1 - n]) / (s * math.sqrt(n))      # noqa: E731
        rng = hi[0][1] - lo[0][1]
        out.append({"m1": k(1), "m3": k(3), "m5": k(5), "m15": k(15), "acc": (r1 - prev_r1) / s,
                    "rsi": (ag / (ag + al) - 0.5) * 2 if ag + al > 0 else 0.0,
                    "macd": (macd - sig) / (c * s), "ema_gap": (e5 - e21) / (c * s), "px_ema9": (c - e9) / (c * s),
                    "vol_reg": 0.5 * math.log(max(v15, 1e-18) / max(v240, 1e-18)),
                    "vol_z": math.log((v + 1e-9) / (vsum / len(vols) + 1e-9)),
                    "range_pos": ((c - lo[0][1]) / rng - 0.5) if rng > 0 else 0.0, "sig": s})
        prev_r1 = r1
    return out


def rows_for(coin, raw=None):
    rows = json.load(open(raw or os.path.join(RAW, f"cbx_{coin}.json")))
    g = grid(rows, START - 6 * 3600, END + 3600)
    f = features(g)
    out = []
    for i, x in enumerate(f):
        if x is None or not START <= g[i][0] < END:
            continue
        c = g[i][4]
        ys = {}
        for hh in HORIZONS:
            if i + hh < len(g) and g[i + hh][4] != c:
                ys[hh] = 1 if g[i + hh][4] > c else 0
        past = {hh: (c > g[i - hh][4]) - (c < g[i - hh][4]) for hh in HORIZONS}
        out.append((g[i][0], x, ys, past, (c > g[i - 5][4]) - (c < g[i - 5][4])))
    return out


def _x(r, cols):
    return [r[1][k] for k in cols]


def _fit(np, X, y, lam):
    """L2 logistic by Newton steps (the intercept unpenalized)."""
    X = np.column_stack([np.ones(len(X)), X])
    w = np.zeros(X.shape[1])
    P = np.eye(X.shape[1]) * lam
    P[0, 0] = 0.0
    for _ in range(25):
        p = 1 / (1 + np.exp(-X @ w))
        g = X.T @ (p - y) + P @ w
        H = (X * (p * (1 - p))[:, None]).T @ X + P
        step = np.linalg.solve(H, g)
        w -= step
        if np.max(np.abs(step)) < 1e-9:
            break
    return w


def _pred(np, w, X):
    X = np.column_stack([np.ones(len(X)), X])
    return np.clip(1 / (1 + np.exp(-X @ w)), 1e-6, 1 - 1e-6)


def _ll(np, p, y):
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def _design(np, rows, h, spec):
    """spec: ("rw",), ("persist",), ("feat", cols, lam)."""
    keep = [r for r in rows if h in r[2]]
    y = np.array([r[2][h] for r in keep], dtype=float)
    if spec[0] == "persist":
        X = np.array([[float(r[3][h])] for r in keep])
    elif spec[0] == "feat":
        X = np.array([_x(r, spec[1]) for r in keep])
    else:
        X = np.zeros((len(keep), 0))
    return keep, X, y


def _fit_score(np, train, test, h, spec):
    _, Xa, ya = _design(np, train, h, spec)
    kb, Xb, yb = _design(np, test, h, spec)
    if spec[0] == "rw":
        p = np.full(len(yb), 0.5)
        w = None
    else:
        w = _fit(np, Xa, ya, spec[2] if spec[0] == "feat" else 1.0)
        p = _pred(np, w, Xb)
    return kb, p, yb, w


def _specs():
    out = [("rw",), ("persist",)]
    for fam, cols in FAMILIES.items():
        out.append(("feat", cols, 1.0, fam))
    for lam in (1.0, 10.0, 100.0, 1000.0):
        out.append(("feat", FEATS, lam, f"all_l2_{int(lam)}"))
    return out


def _name(s):
    return s[3] if s[0] == "feat" else ("random_walk" if s[0] == "rw" else "persistence")


def direction(data):
    import numpy as np
    day = lambda t: (t - START) // 86400                                          # noqa: E731
    dev = [r for c in COINS for r in data[c] if r[0] < SPLIT]
    test = {c: [r for r in data[c] if r[0] >= SPLIT] for c in COINS}
    folds = [(lambda r: day(r[0]) < 14, lambda r: 14 <= day(r[0]) < 21),
             (lambda r: day(r[0]) < 21, lambda r: 21 <= day(r[0]) < 28)]
    res = {}
    for h in HORIZONS:
        cv = {}
        for s in _specs():
            tot = n = 0.0
            for tr, te in folds:
                _, p, y, _ = _fit_score(np, [r for r in dev if tr(r)], [r for r in dev if te(r)], h, s)
                tot += float(_ll(np, p, y).sum())
                n += len(y)
            cv[_name(s)] = tot / n
        best = min((s for s in _specs() if s[0] != "rw"), key=lambda s: cv[_name(s)])
        allt = [r for c in COINS for r in test[c]]
        kb, p, y, w = _fit_score(np, dev, allt, h, best)
        _, p1, _, _ = _fit_score(np, dev, allt, h, ("persist",))
        d = _ll(np, p, y) - _ll(np, np.full(len(y), 0.5), y)
        d1 = _ll(np, p1, y) - _ll(np, np.full(len(y), 0.5), y)
        days = np.array([day(r[0]) for r in kb])
        dm = np.array([d[days == k].mean() for k in sorted(set(days.tolist()))])
        se = float(dm.std(ddof=1) / math.sqrt(len(dm)))
        coins = {}
        for c in COINS:
            _, pc, yc, _ = _fit_score(np, dev, test[c], h, best)
            coins[c] = round(float((_ll(np, pc, yc) - _ll(np, np.full(len(yc), 0.5), yc)).mean()), 6)
        gain = -float(d.mean())
        passed = gain > 2 * se and gain > 0.0005 and sum(1 for v in coins.values() if v < 0) >= 4
        # reversal: P(next h minutes against the trailing 5-minute move)
        trail = np.array([r[4] for r in kb])
        rev_y = np.where(trail > 0, 1 - y, np.where(trail < 0, y, np.nan))
        rev_p = np.where(trail > 0, 1 - p, np.where(trail < 0, p, np.nan))
        ok = ~np.isnan(rev_y)
        bins = []
        for b in range(10):
            m = ok & (np.minimum((rev_p * 10).astype(int), 9) == b)
            if m.sum():
                bins.append({"bin": b, "n": int(m.sum()), "said": round(float(rev_p[m].mean()), 4),
                             "hit": round(float(rev_y[m].mean()), 4)})
        res[str(h)] = {"cv_dev": {k: round(v, 6) for k, v in sorted(cv.items(), key=lambda kv: kv[1])},
                       "chosen": _name(best), "heldout_rows": int(len(y)), "heldout_up_rate": round(float(y.mean()), 4),
                       "gain_vs_rw_nats": round(gain, 6), "se_days": round(se, 6),
                       "persistence_gain_vs_rw": round(-float(d1.mean()), 6),
                       "by_coin_diff_vs_rw": coins, "passed": bool(passed),
                       "weights": [round(float(x), 6) for x in w] if w is not None else None,
                       "weight_names": ["intercept"] + list(best[1]) if best[0] == "feat" else ["intercept", "sign"],
                       "reversal_rate_heldout": round(float(np.nanmean(rev_y)), 4), "reversal_bins": bins}
    return res


def settlement(data):
    """B and C on v1's frame (research/data/crypto/frame_15m.csv.gz)."""
    import crypto_terminal as CT
    import crypto_validation as V
    import kalshi
    rows = V.load()
    grids = {}
    for c in COINS:
        g = grid(json.load(open(os.path.join(RAW, f"cbx_{c}.json"))), START - 6 * 3600, END + 3600)
        grids[c] = {x[0]: x[4] for x in g}
    psd = CT.PROXY_SD
    dev_end = V.SPLIT

    def r5(r):
        t = r["close_ts"] - r["look"]
        a, b = grids[r["coin"]].get(t - 60), grids[r["coin"]].get(t - 360)
        return math.log(a / b) if a and b else None

    def p_of(r, beta):
        s = r["sig"][CT.VOL_HALFLIFE_MIN]
        m = r5(r)
        if not r["rule"] or not s or m is None:
            return None
        lv = r["level"] * math.exp(beta * m)
        return CT.p_yes(r["rule"], lv, s * CT.VOL_MULT, r["look"], proxy_sd=psd)

    def ll(p, y):
        p = min(max(p, 0.001), 0.999)
        return -(y * math.log(p) + (1 - y) * math.log(1 - p))
    betas = (-0.5, -0.25, -0.1, 0.0, 0.1, 0.25, 0.5)
    dev = [r for r in rows if r["close_ts"] < dev_end and r["look"] == 300]
    test = [r for r in rows if r["close_ts"] >= dev_end and r["look"] == 300]
    cvb = {}
    for b in betas:
        v = [ll(p, r["y"]) for r in dev for p in [p_of(r, b)] if p is not None]
        cvb[b] = sum(v) / len(v)
    beta = min(cvb, key=cvb.get)
    d, dk = [], []
    for r in test:
        p, p0 = p_of(r, beta), p_of(r, 0.0)
        if p is None or p0 is None or r["bid"] is None:
            continue
        q = (r["bid"] + r["ask"]) / 200.0
        d.append(ll(p, r["y"]) - ll(p0, r["y"]))
        dk.append(ll(p, r["y"]) - ll(q, r["y"]))

    def ms(v):
        n = len(v)
        m = sum(v) / n
        return round(m, 6), round(math.sqrt(max(0.0, sum(x * x for x in v) / n - m * m) / n), 6), n
    vs_v1, vs_k = ms(d), ms(dk)
    B = {"dev_logloss_by_beta": {str(k): round(v, 6) for k, v in cvb.items()}, "chosen_beta": beta,
         "heldout_vs_v1": {"diff": vs_v1[0], "se": vs_v1[1], "n": vs_v1[2]},
         "heldout_vs_kalshi": {"diff": vs_k[0], "se": vs_k[1], "n": vs_k[2]},
         "ships": bool(beta != 0.0 and vs_v1[0] < -2 * vs_v1[1])}
    # C: intrawindow -- enter at 600 (ask, fee), exit at 300 (the same side's bid, fee)
    by = {}
    for r in rows:
        by.setdefault(r["ticker"], {})[r["look"]] = r

    def trades(lo, hi):
        out = []
        for t, lk in by.items():
            a, b = lk.get(600), lk.get(300)
            if not a or not b or not lo <= a["close_ts"] < hi or a["bid"] is None or b["bid"] is None:
                continue
            p = p_of(a, 0.0)
            if p is None:
                continue
            ya, na = a["ask"], 100.0 - a["bid"]
            ey = 100 * p - ya - kalshi.taker_fee_cents(ya)
            en = 100 * (1 - p) - na - kalshi.taker_fee_cents(na)
            if ey >= 3.0 and ey >= en:
                ex = b["bid"]
                out.append((a["close_ts"], ex - kalshi.taker_fee_cents(ex) - ya - kalshi.taker_fee_cents(ya)))
            elif en >= 3.0:
                ex = 100.0 - b["ask"]
                out.append((a["close_ts"], ex - kalshi.taker_fee_cents(ex) - na - kalshi.taker_fee_cents(na)))
        out.sort()
        if not out:
            return {"trades": 0}
        v = [x for _, x in out]
        m, se, n = ms(v)
        cum = peak = dd = 0.0
        for x in v:
            cum += x
            peak = max(peak, cum)
            dd = max(dd, peak - cum)
        return {"trades": n, "per_trade_c": round(m, 3), "se_c": se, "total_c": round(cum, 1),
                "max_drawdown_c": round(dd, 1), "turnover_c": round(sum(abs(x) for x in v), 1)}
    C = {"dev": trades(V.START, dev_end), "heldout": trades(dev_end, V.END)}
    C["profitable"] = bool(C["heldout"].get("trades") and C["heldout"]["per_trade_c"] > 2 * C["heldout"]["se_c"])
    return B, C


def proxy():
    """D: the official minute average against minute prices from the two exchanges."""
    out = {}
    for coin in COINS:
        cb = {int(r[0]): r for r in json.load(open(os.path.join(RAW, f"cbx_{coin}.json")))}
        bs = {int(r[0]): r for r in json.load(open(os.path.join(RAW, f"bs_{coin}.json")))}
        ms = json.load(open(os.path.join(RAW, f"m15_{coin}.json")))
        acc = {}
        for m in ms:
            close = int(datetime.fromisoformat(m["close_time"].replace("Z", "+00:00")).timestamp())
            for t_end, val in ((close, m.get("expiration_value")), (close - 900, m.get("floor_strike"))):
                try:
                    off = float(val)
                except (TypeError, ValueError):
                    continue
                a, b = cb.get(t_end - 60), bs.get(t_end - 60)
                wk = "dev" if close < int(datetime(2026, 11, 20, tzinfo=timezone.utc).timestamp()) else "heldout"
                cand = {}
                if a:
                    cand["cb_typical"] = (a[1] + a[2] + a[3] + a[4]) / 4
                    cand["cb_open_close"] = (a[3] + a[4]) / 2
                    cand["cb_close"] = a[4]
                if b:
                    cand["bs_typical"] = (b[1] + b[2] + b[3] + b[4]) / 4
                if a and b:
                    cand["mean_cb_bs"] = (cand["cb_typical"] + cand["bs_typical"]) / 2
                for k, v in cand.items():
                    s = acc.setdefault(wk, {}).setdefault(k, [0.0, 0])
                    s[0] += (1e4 * math.log(v / off)) ** 2
                    s[1] += 1
        out[coin] = {wk: {k: {"rms_bp": round(math.sqrt(s[0] / s[1]), 2), "n": s[1]} for k, s in d.items()}
                     for wk, d in acc.items()}
    return out


def score():
    data = {c: rows_for(c) for c in COINS}
    A = direction(data)
    B, C = settlement(data)
    out = {"direction": A, "settlement_drift": B, "intrawindow": C, "proxy": proxy(),
           "untested": ["30-second horizon: no sub-minute history held", "order-book imbalance: no history held",
                        "cross-exchange confirmation: no multi-exchange minute history held for the direction rows"]}
    json.dump(out, open(OUT, "w"), indent=1, sort_keys=True)
    return out


if __name__ == "__main__":
    if (sys.argv[1:] or ["score"])[0] == "fetch":
        fetch()
    else:
        print(json.dumps(score(), indent=1, sort_keys=True)[:6000])
