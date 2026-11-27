"""Crypto terminal v2, stage 2: the FINAL reversal and the hybrid hold / cash-out / switch strategies
(2026-11-27). The owner trades Kalshi's 15-minute contracts with $10 Quick Orders, cashes out, may buy the
other side in the same window, and near the close may hold the side likely to win. "The central research
question is whether Vigil can predict profitable changes in Kalshi's YES and NO prices before expiration."

RULE (written and committed before any held-out figure was computed):

  R1. FINAL REVERSAL. Every quarter-hour window [T-900, T) of BTC, ETH, SOL, XRP and DOGE on Coinbase's
      one-minute candles (crypto_moves' grid), development 2026-10-16..11-12, held-out 2026-11-13..26. Looks
      at 2..13 minutes into the window, decision time t; only bars that END by t are used (crypto_moves'
      causal features; guard CXH-LEAK). x = the close of the bar ending at t; L / H = the window's lowest low
      / highest high so far; K = the window's opening level (the close of the bar ending at T-900, the stand-
      in for the opening sixty-second average); sigma = the board's own per-second volatility (0.9 x the
      60-minute EWMA of one-minute returns / sqrt 60); tau = T - t.
      Labels (TRAINING LABELS ONLY, never inputs): final_low = no later bar in the window trades below L;
      final_high = none above H; touch = some later bar's range contains K.
      The random walk is the NULL and answers each analytically: P(final low) = 2 Phi(ln(x/L) / (sigma sqrt
      tau)) - 1, P(final high) likewise from H, P(touch) = 2 (1 - Phi(|ln(x/K)| / (sigma sqrt tau))).
      Its calibration (log loss, ten bins) is reported on both periods. A challenger -- a logistic on the
      null's logit and crypto_moves' momentum, RSI, MACD, EMA, volatility-regime and range features, L2 100 --
      is fitted on development and REPLACES the null on the panel only if, on the held-out fortnight, it
      beats the null's log loss by more than 2 SE (SE over days) and 0.001 nats, in at least 4 of 5 coins,
      for that label. Swings: with delta = 2 one-minute SDs, the walk expects sigma^2 tau / delta^2 remaining
      zigzag legs; the realized count over the rest of the window is reported beside it (descriptive).

  R2. HYBRID STRATEGIES on Kalshi's real books (v1's raw cache: every settled 15-minute market closing
      2026-11-13..26, its one-minute candlesticks; development 11-13..19, held-out 11-20..26, v1's split).
      $10 a position: contracts = the most the $10 buys at the ask after Kalshi's per-order fee rounded up to
      the cent. A decision at minute t uses the book of the candle ending at or before t and the level of the
      bar ending at t; it FILLS at the book of the next candle ending in (t, t+60] (a delay of up to a minute;
      no such candle, no fill). A sale is at that candle's bid for the side held (NO bid = 100 - YES ask), fee
      in; a position held to the close is paid 100 or 0 with no fee.
        S0 no trade.
        S1 settlement only: at 10 minutes left, buy the side whose fee-in edge (v1's model at the ask) is
           3c+, hold.
        S2 trading only: S1's entry, sold at 5 minutes left.
        S3 take profit: S1's entry, sold at the first minute the bid reaches entry + TP cents, TP in
           {5, 10, 15, 20} chosen on development, else held.
        S4 hybrid, time-aware: a settlement probability p_hat = logistic(a + b logit(model) + c logit(Kalshi
           mid) + d (tau/900) logit(model) + e (tau/900) logit(mid)) fitted on development looks -- how much to
           trust the model against Kalshi changes with time left, learned, no phases. Each minute from 14 to
           1 minute left: flat -> buy the side with 100 p_hat - ask - fee >= theta; holding -> sell when the
           bid after the fee exceeds 100 p_hat of the side held, and may then buy the other side by the same
           entry rule (a switch is two independent decisions); at most three entries a window. theta in
           {2, 4, 6, 8} chosen on development.
      Reported per strategy: net dollars over the period, per window with its SE over windows, worst
      drawdown, entries, turnover, fees; S4's entries by time left. A strategy is "profitable" only if its
      held-out mean per window exceeds 2 SE; S4 "beats" a baseline only if the paired per-window difference
      exceeds 2 SE.

    python3 research/crypto_hybrid.py     # needs crypto_moves' and crypto_validation's raw caches in $CRYPTO_RAW
"""
import json
import math
import os
import sys
from datetime import datetime, timezone

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("VIGIL_NO_BG", "1")
import crypto_moves as M  # noqa: E402
import crypto_terminal as CT  # noqa: E402

OUT = os.path.join(_ROOT, "research", "data", "crypto", "hybrid.json")
H2_SPLIT = int(datetime(2026, 11, 20, tzinfo=timezone.utc).timestamp())
H2_START = int(datetime(2026, 11, 13, tzinfo=timezone.utc).timestamp())
FEATS = ("m1", "m5", "m15", "rsi", "macd", "ema_gap", "vol_reg", "range_pos")


def _phi(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def null_probs(x, low, high, k, sig_s, tau):
    """The random walk's answers (the panel's baseline, crypto_terminal.final_reversal uses the same)."""
    s = sig_s * math.sqrt(tau)
    if s <= 0:
        return None
    return {"final_low": max(0.0, 2 * _phi(math.log(x / low) / s) - 1) if x >= low else 0.0,
            "final_high": max(0.0, 2 * _phi(math.log(high / x) / s) - 1) if high >= x else 0.0,
            "touch": min(1.0, 2 * (1 - _phi(abs(math.log(x / k)) / s)))}


def zig_legs(closes, delta):
    """Turning points of a zigzag with log threshold delta on a series of closes (minute closes undercount a
    continuous path's)."""
    legs, d, hi, lo = 0, 0, closes[0], closes[0]
    for c in closes[1:]:
        hi, lo = max(hi, c), min(lo, c)
        if d >= 0 and math.log(hi / c) >= delta:
            legs, d, lo, hi = legs + (1 if d else 0), -1, c, c
        elif d <= 0 and math.log(c / lo) >= delta:
            legs, d, hi, lo = legs + (1 if d else 0), 1, c, c
    return legs


def r1_rows(coin):
    rows = json.load(open(os.path.join(M.RAW, f"cbx_{coin}.json")))
    g = M.grid(rows, M.START - 6 * 3600, M.END + 3600)
    f = M.features(g)
    idx = {b[0]: i for i, b in enumerate(g)}
    out = []
    for T in range(M.START + 900, M.END + 1, 900):
        i0 = idx.get(T - 900)                       # the window's first bar starts at T-900
        if i0 is None or idx.get(T - 60) is None or i0 < 1:
            continue
        k_open = g[i0 - 1][4]
        for kk in range(2, 14):
            j = i0 + kk - 1                          # the bar ending at t = T-900+60kk
            t = g[j][0] + 60
            if f[j] is None:
                continue
            lo = min(b[3] for b in g[i0:j + 1])
            hi = max(b[2] for b in g[i0:j + 1])
            fut = g[j + 1:i0 + 15]
            if len(fut) != 15 - kk:
                continue
            x = g[j][4]
            sig = 0.9 * f[j]["sig"] / math.sqrt(60.0)
            p = null_probs(x, lo, hi, k_open, sig, T - t)
            if not p:
                continue
            delta = 2 * f[j]["sig"]
            legs = zig_legs([x] + [b[4] for b in fut], delta)
            lab = {"final_low": int(min(b[3] for b in fut) >= lo), "final_high": int(max(b[2] for b in fut) <= hi),
                   "touch": int(any(b[3] <= k_open <= b[2] for b in fut))}
            out.append({"t": t, "coin": coin, "p": p, "y": lab, "f": f[j], "tau": T - t,
                        "swings_null": (sig ** 2) * (T - t) / (delta ** 2), "swings_real": legs})
    return out


def _ll(p, y):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def _logit(p):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def r1():
    import numpy as np
    rows = [r for c in M.COINS for r in r1_rows(c)]
    day = lambda t: (t - M.START) // 86400                                  # noqa: E731
    res = {}
    for lab in ("final_low", "final_high", "touch"):
        dev = [r for r in rows if r["t"] < M.SPLIT]
        test = [r for r in rows if r["t"] >= M.SPLIT]

        def X(rs):
            return np.array([[_logit(r["p"][lab])] + [r["f"][k] for k in FEATS] for r in rs])
        w = M._fit(np, X(dev), np.array([r["y"][lab] for r in dev], float), 100.0)
        pt = M._pred(np, w, X(test))
        y = np.array([r["y"][lab] for r in test], float)
        pn = np.array([min(max(r["p"][lab], 1e-4), 1 - 1e-4) for r in test])
        d = M._ll(np, pt, y) - M._ll(np, pn, y)
        days = np.array([day(r["t"]) for r in test])
        dm = np.array([d[days == k].mean() for k in sorted(set(days.tolist()))])
        se = float(dm.std(ddof=1) / math.sqrt(len(dm)))
        coins = {c: round(float(d[np.array([r["coin"] == c for r in test])].mean()), 6) for c in M.COINS}
        gain = -float(d.mean())
        passed = gain > 2 * se and gain > 0.001 and sum(1 for v in coins.values() if v < 0) >= 4

        def calib(rs):
            bins = {}
            for r in rs:
                b = min(int(r["p"][lab] * 10), 9)
                x = bins.setdefault(b, [0, 0.0, 0])
                x[0] += 1
                x[1] += r["p"][lab]
                x[2] += r["y"][lab]
            return [{"bin": b, "n": v[0], "said": round(v[1] / v[0], 4), "hit": round(v[2] / v[0], 4)}
                    for b, v in sorted(bins.items())]
        res[lab] = {"null_logloss_dev": round(sum(_ll(r["p"][lab], r["y"][lab]) for r in dev) / len(dev), 5),
                    "null_logloss_heldout": round(float(M._ll(np, pn, y).mean()), 5),
                    "null_calibration_heldout": calib(test), "base_rate_heldout": round(float(y.mean()), 4),
                    "challenger_gain_vs_null": round(gain, 6), "se_days": round(se, 6), "by_coin_diff": coins,
                    "passed": bool(passed), "weights": [round(float(v), 5) for v in w],
                    "weight_names": ["intercept", "logit_null"] + list(FEATS), "heldout_rows": int(len(y))}
    test = [r for r in rows if r["t"] >= M.SPLIT]
    sn = sum(r["swings_null"] for r in test)
    sr = sum(r["swings_real"] for r in test)
    res["swings"] = {"delta": "2 one-minute SDs", "predicted_mean": round(sn / len(test), 3),
                     "realized_mean": round(sr / len(test), 3), "rows": len(test)}
    return res


# ---- R2 -----------------------------------------------------------------------------------------------
def r2_markets():
    out = []
    for coin in M.COINS:
        ms = json.load(open(os.path.join(M.RAW, f"m15_{coin}.json")))
        book = json.load(open(os.path.join(M.RAW, f"book_{coin}.json")))
        g = M.grid(json.load(open(os.path.join(M.RAW, f"cbx_{coin}.json"))), M.START - 6 * 3600, M.END + 3600)
        f = M.features(g)
        idx = {b[0]: i for i, b in enumerate(g)}
        for m in ms:
            res = str(m.get("result") or "").lower()
            rule = CT.parse_rule(m, coin)
            T = CT._ts(m["close_time"])
            if res not in ("yes", "no") or not rule or T is None:
                continue
            T = int(T)
            bk = [(int(k[0]), CT._cents(k[1]), CT._cents(k[2])) for k in book.get(m["ticker"]) or []]
            looks = []
            for kk in range(1, 15):
                t = T - 900 + 60 * kk
                j = idx.get(t - 60)
                if j is None or f[j] is None:
                    continue
                now = [b for b in bk if b[0] <= t]
                nxt = [b for b in bk if t < b[0] <= t + 60]
                q = now[-1] if now and t - now[-1][0] <= 120 else None
                fill = nxt[0] if nxt else None
                ok = lambda b: b and b[1] is not None and b[2] is not None and 0 < b[1] < b[2] < 100   # noqa: E731
                sig = CT.VOL_MULT * f[j]["sig"] / math.sqrt(60.0)
                p = CT.p_yes(rule, g[j][4], sig, T - t)
                looks.append({"t": t, "tau": T - t, "p": p, "q": q if ok(q) else None, "fill": fill if ok(fill) else None})
            out.append({"coin": coin, "T": T, "y": 1 if res == "yes" else 0, "looks": looks})
    return out


def _fit_phat(np, mk):
    X, y = [], []
    for m in mk:
        for lk in m["looks"]:
            if lk["q"] and lk["p"] is not None:
                a, b = _logit(lk["p"]), _logit((lk["q"][1] + lk["q"][2]) / 200.0)
                s = lk["tau"] / 900.0
                X.append([a, b, s * a, s * b])
                y.append(m["y"])
    return M._fit(np, np.array(X), np.array(y, float), 1.0)


def _phat(w, lk):
    a, b = _logit(lk["p"]), _logit((lk["q"][1] + lk["q"][2]) / 200.0)
    s = lk["tau"] / 900.0
    z = w[0] + w[1] * a + w[2] * b + w[3] * s * a + w[4] * s * b
    return 1 / (1 + math.exp(-z))


def _side_px(fill, side, buy):
    yb, ya = fill[1], fill[2]
    if side == "yes":
        return ya if buy else yb
    return (100 - yb) if buy else (100 - ya)


def run_strategy(m, kind, par=None, w=None):
    """One window's net cents for a $10 strategy, and its entries [(tau, side)] and fees."""
    pnl, fees, entries, pos = 0.0, 0.0, [], None       # pos = (side, n, cost)
    looks = {lk["tau"]: lk for lk in m["looks"]}

    def buy(lk, side):
        nonlocal pnl, fees, pos
        px = _side_px(lk["fill"], side, True)
        n = CT.contracts_for_dollars(1000, px)
        if n <= 0:
            return
        fee = CT.order_fee_c(px, n)
        pnl -= n * px + fee
        fees += fee
        pos = (side, n)
        entries.append((lk["tau"], side))

    def sell(lk):
        nonlocal pnl, fees, pos
        side, n = pos
        px = _side_px(lk["fill"], side, False)
        fee = CT.order_fee_c(px, n)
        pnl += n * px - fee
        fees += fee
        pos = None

    def edge_side(lk):
        p, q = lk["p"], lk["q"]
        ey = 100 * p - q[2] - CT.kalshi_fee_c(q[2])
        en = 100 * (1 - p) - (100 - q[1]) - CT.kalshi_fee_c(100 - q[1])
        if ey >= 3 and ey >= en:
            return "yes"
        return "no" if en >= 3 else None
    if kind in ("S1", "S2", "S3"):
        lk = looks.get(600)
        if lk and lk["q"] and lk["fill"] and lk["p"] is not None:
            s = edge_side(lk)
            if s:
                buy(lk, s)
                entry_px = _side_px(lk["fill"], s, True)
                for tau in range(540, 0, -60):
                    l2 = looks.get(tau)
                    if not pos or not l2 or not l2["fill"]:
                        continue
                    if kind == "S2" and tau == 300:
                        sell(l2)
                    elif kind == "S3" and _side_px(l2["fill"], s, False) >= entry_px + par:
                        sell(l2)
    elif kind == "S4":
        for tau in range(840, 0, -60):
            lk = looks.get(tau)
            if not lk or not lk["q"] or not lk["fill"] or lk["p"] is None:
                continue
            ph = _phat(w, lk)
            if pos:
                side, n = pos
                bid = _side_px(lk["fill"], side, False)
                hold = 100 * (ph if side == "yes" else 1 - ph)
                if bid - CT.order_fee_c(bid, n) / max(n, 1) > hold:
                    sell(lk)
            if not pos and len(entries) < 3:
                q = lk["q"]
                ey = 100 * ph - q[2] - CT.kalshi_fee_c(q[2])
                en = 100 * (1 - ph) - (100 - q[1]) - CT.kalshi_fee_c(100 - q[1])
                if ey >= par and ey >= en:
                    buy(lk, "yes")
                elif en >= par:
                    buy(lk, "no")
    if pos:
        side, n = pos
        pnl += n * 100 * (m["y"] if side == "yes" else 1 - m["y"])
    return pnl, fees, entries


def summarize(vals, fees, ents):
    n = len(vals)
    m = sum(vals) / n
    se = math.sqrt(max(0.0, sum(v * v for v in vals) / n - m * m) / n)
    cum = peak = dd = 0.0
    for v in vals:
        cum += v
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return {"windows": n, "net_dollars": round(sum(vals) / 100, 2), "per_window_c": round(m, 3), "se_c": round(se, 3),
            "max_drawdown_dollars": round(dd / 100, 2), "entries": ents, "fees_dollars": round(fees / 100, 2),
            "profitable": bool(m > 2 * se)}


def r2():
    import numpy as np
    mk = r2_markets()
    dev = [m for m in mk if m["T"] < H2_SPLIT]
    test = [m for m in mk if m["T"] >= H2_SPLIT]
    w = list(_fit_phat(np, dev))
    choose = {}
    tp = max((5, 10, 15, 20), key=lambda v: sum(run_strategy(m, "S3", v)[0] for m in dev))
    th = max((2, 4, 6, 8), key=lambda v: sum(run_strategy(m, "S4", v, w)[0] for m in dev))
    choose = {"take_profit_c": tp, "theta_c": th, "p_hat_weights": [round(float(x), 5) for x in w],
              "p_hat_names": ["intercept", "logit_model", "logit_kalshi_mid", "tau_x_logit_model", "tau_x_logit_mid"]}
    out = {"chosen_on_dev": choose}
    for name, mks in (("dev", dev), ("heldout", test)):
        per = {}
        runs = {}
        for s, par in (("S0", None), ("S1", None), ("S2", None), ("S3", tp), ("S4", th)):
            rr = [(0.0, 0.0, []) if s == "S0" else run_strategy(m, s, par, w) for m in mks]
            runs[s] = [r[0] for r in rr]
            per[s] = summarize(runs[s], sum(r[1] for r in rr), sum(len(r[2]) for r in rr))
            if s == "S4":
                by = {}
                for r in rr:
                    for tau, _side in r[2]:
                        b = "10-14 min" if tau > 600 else "5-10 min" if tau > 300 else "under 5 min"
                        by[b] = by.get(b, 0) + 1
                per[s]["entries_by_time_left"] = by
        for base in ("S0", "S1", "S2", "S3"):
            d = [a - b for a, b in zip(runs["S4"], runs[base])]
            n = len(d)
            mm = sum(d) / n
            se = math.sqrt(max(0.0, sum(v * v for v in d) / n - mm * mm) / n)
            per[f"S4_minus_{base}"] = {"per_window_c": round(mm, 3), "se_c": round(se, 3), "beats": bool(mm > 2 * se)}
        out[name] = per
    return out


if __name__ == "__main__":
    res = {"r1_final_reversal": r1(), "r2_hybrid": r2()}
    json.dump(res, open(OUT, "w"), indent=1, sort_keys=True)
    print(json.dumps(res, indent=1, sort_keys=True)[:5000])
