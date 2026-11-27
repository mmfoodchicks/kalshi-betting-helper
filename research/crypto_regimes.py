"""Crypto terminal v2, stage 3 (2026-11-27): more models for the FINAL reversal, and execution delay. The owner:
"Continue crypto Final Reversal Detection research. Test realistic execution delays of 5, 15, 30 and 60 seconds
where recorded data supports them." Predicting another dip and trading on it profitably are kept SEPARATE: R3 is
forecasting only, R4 is money only.

RULE (written and committed before any figure below was computed):

  R3. FINAL-REVERSAL MODELS on crypto_hybrid.R1's looks (same windows, looks, labels, development 2026-10-16..11-12,
      held-out 11-13..26; later bars are labels only). Compared on the held-out fortnight, per label (final low,
      final high, touch of the opening level):
        null        the walk's analytic answer (R1);
        challenger  R1's committed logistic (research/data/crypto/hybrid.json weights, not refitted);
        regime      the null with sigma scaled by a factor per volatility regime (terciles of crypto_moves'
                    vol_reg, cut points from development), each factor chosen on development from {0.5, 0.6, ...,
                    1.5} by log loss -- a regime-switching volatility;
        pathsim     an empirical path simulation: 400 paths of the window's remaining minutes, each minute's return
                    and its intra-minute high/low excursions resampled together from the trailing 300 complete
                    minutes (as of the look), the label read off each path exactly as R1 reads the real one;
                    seeded per look (crc32 of coin and time), so it is reproducible. No fitted parameters.
      Each is scored against the null and against the challenger: it beats one only if the held-out log-loss gain
      exceeds 2 SE (SE over days) and 0.001 nats and holds in at least 4 of the 5 coins.

  R4. DELAY, money only. (a) On v1's minute books (crypto_hybrid.R2, its frozen choices: take profit 15c, theta
      4c, the p_hat weights) every strategy is re-scored with a ZERO delay -- the fill at the decision candle's
      own quote -- beside R2's one-minute delay, as an optimistic bracket: minute candles cannot resolve 5, 15
      or 30 seconds. (b) `rescore_delays` scores the same frozen strategies at 5, 15, 30 and 60 s on the book
      snapshots crypto_terminal records every ~5 s (table cx_book): a decision at a minute look uses the latest
      snapshot at or before it, and fills at the first snapshot at or after decision + delay. It is read ONCE,
      after 14 full days of snapshots, over every recorded window (no parameter is fitted on them); a strategy
      is "profitable" there only if its mean per window exceeds 2 SE.

    python3 research/crypto_regimes.py            # R3 and R4(a) -> research/data/crypto/regimes.json
"""
import json
import math
import os
import sys
import zlib

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("VIGIL_NO_BG", "1")
import crypto_moves as M  # noqa: E402
import crypto_hybrid as HY  # noqa: E402
import crypto_terminal as CT  # noqa: E402

OUT = os.path.join(_ROOT, "research", "data", "crypto", "regimes.json")
LABELS = ("final_low", "final_high", "touch")
SCALES = tuple(round(0.5 + 0.1 * i, 1) for i in range(11))
DELAYS = (5, 15, 30, 60)


def null_scaled(r, k):
    """R1's null with sigma x k (the look's own x, L, H, K and tau are in the row)."""
    s = r["sig"] * k * math.sqrt(r["tau"])
    x, lo, hi, kk = r["x"], r["lo"], r["hi"], r["k"]
    return {"final_low": max(0.0, 2 * HY._phi(math.log(x / lo) / s) - 1) if x >= lo else 0.0,
            "final_high": max(0.0, 2 * HY._phi(math.log(hi / x) / s) - 1) if hi >= x else 0.0,
            "touch": min(1.0, 2 * (1 - HY._phi(abs(math.log(x / kk)) / s)))}


def pathsim(np, r, hist, n=400):
    """P(label) from paths built out of the trailing minutes' own (return, up-excursion, down-excursion)."""
    rng = np.random.default_rng(zlib.crc32(f"{r['coin']}:{r['t']}".encode()))
    m = int(round(r["tau"] / 60))
    ix = rng.integers(0, len(hist), size=(n, m))
    ret, up, dn = hist[ix, 0], hist[ix, 1], hist[ix, 2]
    lc = np.log(r["x"]) + np.cumsum(ret, axis=1)
    prev = np.concatenate([np.full((n, 1), np.log(r["x"])), lc[:, :-1]], axis=1)
    hi = np.maximum(prev, lc) + up
    lo = np.minimum(prev, lc) - dn
    lk = np.log(r["k"])
    return {"final_low": float(np.mean(lo.min(axis=1) >= np.log(r["lo"]))),
            "final_high": float(np.mean(hi.max(axis=1) <= np.log(r["hi"]))),
            "touch": float(np.mean(((lo <= lk) & (hi >= lk)).any(axis=1)))}


def r3_rows(np, coin):
    """R1's looks with what the extra models need: the look's state and its trailing 300-minute history."""
    rows = HY.r1_rows(coin)
    raw = json.load(open(os.path.join(M.RAW, f"cbx_{coin}.json")))
    g = M.grid(raw, M.START - 6 * 3600, M.END + 3600)
    idx = {b[0]: i for i, b in enumerate(g)}
    for r in rows:
        j = idx[r["t"] - 60]                              # the bar ending at the look
        T = r["t"] + r["tau"]
        w0 = idx[T - 900]
        r["x"] = g[j][4]
        r["lo"] = min(b[3] for b in g[w0:j + 1])
        r["hi"] = max(b[2] for b in g[w0:j + 1])
        r["k"] = g[w0 - 1][4]
        r["sig"] = 0.9 * r["f"]["sig"] / math.sqrt(60.0)
        h = g[j - 299:j + 1]
        r["hist"] = np.array([[math.log(b[4] / a[4]), math.log(b[2] / max(a[4], b[4])), math.log(min(a[4], b[4]) / b[3])]
                              for a, b in zip(g[j - 300:j], h)])
    return rows


def _ll(p, y):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def r3():
    import numpy as np
    hyb = json.load(open(os.path.join(_ROOT, "research", "data", "crypto", "hybrid.json")))["r1_final_reversal"]
    rows = [r for c in M.COINS for r in r3_rows(np, c)]
    dev = [r for r in rows if r["t"] < M.SPLIT]
    test = [r for r in rows if r["t"] >= M.SPLIT]
    vr = sorted(r["f"]["vol_reg"] for r in dev)
    cuts = (vr[len(vr) // 3], vr[2 * len(vr) // 3])
    reg = lambda r: 0 if r["f"]["vol_reg"] < cuts[0] else (1 if r["f"]["vol_reg"] < cuts[1] else 2)   # noqa: E731
    scale = {}
    for lab in LABELS:
        scale[lab] = []
        for g in range(3):
            sub = [r for r in dev if reg(r) == g]
            scale[lab].append(min(SCALES, key=lambda k: sum(_ll(null_scaled(r, k)[lab], r["y"][lab]) for r in sub)))
    day = lambda t: (t - M.START) // 86400                                    # noqa: E731
    preds = {m: {lab: [] for lab in LABELS} for m in ("null", "challenger", "regime", "pathsim")}
    for r in test:
        ps = pathsim(np, r, r["hist"])
        for lab in LABELS:
            d = hyb[lab]
            w = d["weights"]
            z = w[0] + w[1] * HY._logit(r["p"][lab]) + sum(wi * r["f"][n] for wi, n in zip(w[2:], d["weight_names"][2:]))
            preds["null"][lab].append(r["p"][lab])
            preds["challenger"][lab].append(1 / (1 + math.exp(-z)))
            preds["regime"][lab].append(null_scaled(r, scale[lab][reg(r)])[lab])
            preds["pathsim"][lab].append(min(max(ps[lab], 0.5 / 400), 1 - 0.5 / 400))
    out = {"regime_cuts_dev": [round(c, 4) for c in cuts], "regime_scales_dev": scale, "heldout_rows": len(test), "labels": {}}
    for lab in LABELS:
        y = [r["y"][lab] for r in test]
        ll = {m: [_ll(p, yy) for p, yy in zip(preds[m][lab], y)] for m in preds}
        res = {m: round(sum(v) / len(v), 5) for m, v in ll.items()}
        comp = {}
        for m in ("regime", "pathsim", "challenger"):
            for base in ("null", "challenger"):
                if m == base:
                    continue
                d = [a - b for a, b in zip(ll[m], ll[base])]
                days = {}
                for r, v in zip(test, d):
                    days.setdefault(day(r["t"]), []).append(v)
                dm = [sum(v) / len(v) for v in days.values()]
                mu = sum(dm) / len(dm)
                se = math.sqrt(sum((x - mu) ** 2 for x in dm) / (len(dm) - 1) / len(dm))
                coins = {}
                for r, v in zip(test, d):
                    coins.setdefault(r["coin"], []).append(v)
                cw = sum(1 for v in coins.values() if sum(v) / len(v) < 0)
                gain = -sum(d) / len(d)
                comp[f"{m}_vs_{base}"] = {"gain": round(gain, 6), "se_days": round(se, 6), "coins_better": cw,
                                          "beats": bool(gain > 2 * se and gain > 0.001 and cw >= 4)}
        out["labels"][lab] = {"logloss": res, "compare": comp}
    return out


def r4_zero_delay():
    """R2's frozen strategies with the fill at the decision candle's own quote (an optimistic bracket)."""
    hyb = json.load(open(os.path.join(_ROOT, "research", "data", "crypto", "hybrid.json")))["r2_hybrid"]
    ch = hyb["chosen_on_dev"]
    w = ch["p_hat_weights"]
    mk = HY.r2_markets()
    for m in mk:
        for lk in m["looks"]:
            lk["fill"] = lk["q"]                      # zero delay: the quote the decision saw
    out = {}
    for name, mks in (("dev", [m for m in mk if m["T"] < HY.H2_SPLIT]), ("heldout", [m for m in mk if m["T"] >= HY.H2_SPLIT])):
        per = {}
        for s, par in (("S1", None), ("S2", None), ("S3", ch["take_profit_c"]), ("S4", ch["theta_c"])):
            rr = [HY.run_strategy(m, s, par, w) for m in mks]
            per[s] = HY.summarize([r[0] for r in rr], sum(r[1] for r in rr), sum(len(r[2]) for r in rr))
        out[name] = per
    return {"zero_delay": out, "one_minute_delay_heldout": {s: hyb["heldout"][s] for s in ("S1", "S2", "S3", "S4")}}


def fill_at(snaps, t, delay):
    """The first snapshot at or after t + delay (snaps sorted by time): never one before it."""
    for s in snaps:
        if s[0] >= t + delay:
            return s
    return None


def quote_at(snaps, t):
    """The latest snapshot at or before t."""
    best = None
    for s in snaps:
        if s[0] > t:
            break
        best = s
    return best


def rescore_delays(db_path, level_closes, min_days=14):
    """R4(b), read once after 14 full days of snapshots. `level_closes`: {coin: {minute_start: close}} from
    Coinbase over the recorded span (the reader fetches them). Each window's rule and result come from the live
    record's graded rows (cx_forecasts); the model's figure is v1's p_yes at the look's level and trailing EWMA
    volatility; the frozen R2 strategies are run with the decision quote at or before each look and the fill at
    the first snapshot at or after look + delay."""
    import sqlite3
    hyb = json.load(open(os.path.join(_ROOT, "research", "data", "crypto", "hybrid.json")))["r2_hybrid"]
    ch = hyb["chosen_on_dev"]
    w = ch["p_hat_weights"]
    c = sqlite3.connect(db_path)
    lo, hi = c.execute("SELECT MIN(ts), MAX(ts) FROM cx_book").fetchone()
    if lo is None or hi - lo < min_days * 86400:
        return {"ready": False, "span_days": round(((hi or 0) - (lo or 0)) / 86400, 2)}
    ok = lambda b: b is not None and 0 < b[1] < b[2] < 100                    # noqa: E731
    res = {}
    for d in DELAYS:
        runs = {s: [] for s in ("S1", "S2", "S3", "S4")}
        for ci, coin in enumerate(CT.COINS):
            closes = level_closes.get(coin, {})
            wins = list(c.execute("""SELECT close_ts, MAX(result), MAX(strike), MAX(op) FROM cx_forecasts
                                     WHERE coin = ? AND kind = 'm15' AND result IN (0, 1) GROUP BY close_ts""", (coin,)))
            for T, y, K, op in wins:
                T = int(T)
                snaps = [(ts, yb / 10.0, ya / 10.0) for ts, yb, ya in c.execute(
                    "SELECT ts, yb, ya FROM cx_book WHERE coin = ? AND close_ts = ? ORDER BY ts", (ci, T))]
                looks = []
                for kk in range(1, 15):
                    t = T - 900 + 60 * kk
                    lv = closes.get(t - 60)
                    hist = [closes.get(t - 60 * (i + 1)) for i in range(299, -1, -1)]
                    sig = CT.ewma_sigma([x for x in hist if x])
                    q, f = quote_at(snaps, t), fill_at(snaps, t, d)
                    if not lv or not sig:
                        continue
                    looks.append({"t": t, "tau": T - t, "p": CT.p_yes({"op": op, "strike": K}, lv, sig, T - t),
                                  "q": q if ok(q) else None, "fill": f if ok(f) else None})
                m = {"T": T, "y": int(y), "looks": looks}
                for s, par in (("S1", None), ("S2", None), ("S3", ch["take_profit_c"]), ("S4", ch["theta_c"])):
                    runs[s].append(HY.run_strategy(m, s, par, w))
        res[str(d)] = {s: HY.summarize([r[0] for r in rr], sum(r[1] for r in rr), sum(len(r[2]) for r in rr))
                       for s, rr in runs.items()}
    return {"ready": True, "by_delay_s": res, "frozen": ch}


if __name__ == "__main__":
    out = {"r3_final_reversal_models": r3(), "r4_delay": r4_zero_delay(),
           "r4b_recorded": "pending: needs 14 days of cx_book snapshots (crypto_terminal's book recorder)"}
    json.dump(out, open(OUT, "w"), indent=1, sort_keys=True)
    print(json.dumps(out, indent=1, sort_keys=True)[:4000])
