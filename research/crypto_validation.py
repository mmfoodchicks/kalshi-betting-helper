"""The crypto terminal's model against held-out history (2026-11-27).

RULE (written before any held-out figure was computed; committed with this
script before the scoring run):

  Data. Every settled KX<COIN>15M market of BTC, ETH, SOL, XRP and DOGE whose
  close falls in [2026-11-13 00:00, 2026-11-27 00:00) UTC: Kalshi's own
  result, expiration_value (the official sixty-second CF Benchmarks average)
  and floor_strike (the same average before the open; custom_strike's figure
  where it is more precise); Kalshi's one-minute candlesticks for each
  market's book; Coinbase's public one-minute candles for the level.

  Looks. 600, 300, 120 and 60 s before the close. At a look t:
    level  = the close of the Coinbase candle that starts at t - 60 (its last
             trade before t) -- nothing at or after t;
    sigma  = crypto_terminal.ewma_sigma over the 300 one-minute closes ending
             at t (the production function);
    book   = yes_bid / yes_ask closes of the latest Kalshi candlestick whose
             end_period_ts <= t, used only if it ended within 120 s of t and
             is two-sided (0 < bid < ask < 100).
  The model's figure is crypto_terminal.p_yes on crypto_terminal.parse_rule's
  rule -- the production path, Coinbase standing in for the four-exchange
  median (the other three exchanges' minute history is not kept; the live
  record measures the production proxy).

  Split. Development = closes 2026-11-13..19; held-out = 2026-11-20..26.

  Chosen on development only, by pooled model log loss at the 300 s look over
  every market: half-life in {15, 30, 60, 120, 240} minutes x volatility
  multiplier in {0.8, 0.9, 1.0, 1.1, 1.25}. PROXY_SD = the median over coins
  of the RMS of log(expiration_value / Coinbase typical price (O+H+L+C)/4 of
  the minute before the close) on development. Then frozen.

  Held-out, reported per look, per coin and pooled: model and Kalshi log loss
  and Brier on the rows priced both ways; the paired difference (model minus
  Kalshi, one row per market per look) and its SE; reliability; the paper
  rule (one contract at the ask on the side whose fee-in edge is 3c or more
  at the 300 s look, Kalshi's quadratic taker fee) with its mean P/L a bet
  and SE.

  Decision. The terminal may call the model's edges anything but "unproven"
  only if the pooled held-out difference at 300 s favours the model by more
  than 2 SE. The paper rule is "profitable" only if its held-out mean exceeds
  2 SE. Otherwise Kalshi is the better forecaster on this history, the edges
  stay labelled unproven, and nothing more is claimed. A week of 15-minute
  markets is ~670 a coin: a small sample for any edge, flagged as such.

    python3 research/crypto_validation.py fetch   # raw data into $CRYPTO_RAW (default ~/.cache/vigil-crypto)
    python3 research/crypto_validation.py frame   # -> research/data/crypto/frame_15m.csv.gz (committed)
    python3 research/crypto_validation.py score   # the dev choice, then the held-out figures (JSON)
"""
import csv
import gzip
import io
import json
import math
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _ROOT)
os.environ.setdefault("VIGIL_NO_BG", "1")
import crypto_terminal as CT  # noqa: E402
import kalshi  # noqa: E402

# Kalshi's quadratic taker curve; every crypto series read on 2026-11-27 has
# fee_type quadratic, multiplier 1 (series_info), so no lookup per row.
_fee = kalshi.taker_fee_cents

K = "https://api.elections.kalshi.com/trade-api/v2"
RAW = os.environ.get("CRYPTO_RAW") or os.path.expanduser("~/.cache/vigil-crypto")
FRAME = os.path.join(_ROOT, "research", "data", "crypto", "frame_15m.csv.gz")
START = int(datetime(2026, 11, 13, tzinfo=timezone.utc).timestamp())
SPLIT = int(datetime(2026, 11, 20, tzinfo=timezone.utc).timestamp())
END = int(datetime(2026, 11, 27, tzinfo=timezone.utc).timestamp())
LOOKS = (600, 300, 120, 60)
HALFLIVES = (15, 30, 60, 120, 240)
MULTS = (0.8, 0.9, 1.0, 1.1, 1.25)
EDGE_C = 3.0
COLS = ("coin", "ticker", "close_ts", "look", "result", "level", "bid", "ask", "settle", "typ_close",
        "rules_primary", "rules_secondary", "strike_type", "floor_strike", "custom_strike") + \
    tuple(f"s{h}" for h in HALFLIVES)


def _get(url):
    for attempt in range(6):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "research", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 5:
                raise
            time.sleep(2.0 * (attempt + 1))


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch():
    os.makedirs(RAW, exist_ok=True)
    for coin, c in CT.COINS.items():
        mp = os.path.join(RAW, f"m15_{coin}.json")
        if not os.path.exists(mp):
            out, cursor = [], ""
            while True:
                d = _get(f"{K}/markets?series_ticker={c['m15']}&status=settled&limit=1000"
                         f"&min_close_ts={START}&max_close_ts={END}" + (f"&cursor={cursor}" if cursor else ""))
                keep = ("ticker", "close_time", "open_time", "result", "expiration_value", "floor_strike",
                        "custom_strike", "strike_type", "rules_primary", "rules_secondary")
                out += [{k: m.get(k) for k in keep} for m in d.get("markets") or []]
                cursor = d.get("cursor") or ""
                time.sleep(0.4)
                if not cursor or not d.get("markets"):
                    break
            out = [m for m in out if START <= CT._ts(m["close_time"]) < END]
            json.dump(out, open(mp, "w"))
        ms = json.load(open(mp))
        kp = os.path.join(RAW, f"book_{coin}.json")
        book = json.load(open(kp)) if os.path.exists(kp) else {}
        todo = [m for m in ms if m["ticker"] not in book]
        for i in range(0, len(todo), 20):
            part = todo[i:i + 20]
            s = min(int(CT._ts(m["open_time"])) for m in part) - 60
            e = max(int(CT._ts(m["close_time"])) for m in part)
            d = _get(f"{K}/markets/candlesticks?market_tickers={','.join(m['ticker'] for m in part)}"
                     f"&start_ts={s}&end_ts={e}&period_interval=1")
            for x in d.get("markets") or []:
                book[x["market_ticker"]] = [[k["end_period_ts"], (k.get("yes_bid") or {}).get("close_dollars"),
                                             (k.get("yes_ask") or {}).get("close_dollars")]
                                            for k in x.get("candlesticks") or []]
            for m in part:
                book.setdefault(m["ticker"], [])
            time.sleep(0.4)
            if i % 400 == 0:
                json.dump(book, open(kp, "w"))
        json.dump(book, open(kp, "w"))
        cp = os.path.join(RAW, f"cb_{coin}.json")
        have = {int(r[0]): r for r in json.load(open(cp))} if os.path.exists(cp) else {}
        t = START - 6 * 3600
        while t < END:
            t2 = min(END, t + 300 * 60)
            if not all(x in have for x in range(t, t2, 60)):
                for r in _get(f"https://api.exchange.coinbase.com/products/{c['coinbase']}/candles"
                              f"?granularity=60&start={_iso(t)}&end={_iso(t2 - 60)}"):
                    have[int(r[0])] = r
                time.sleep(0.15)
            t = t2
        json.dump(sorted(have.values()), open(cp, "w"))
        print(coin, len(ms), "markets", len(book), "books", len(have), "candles", flush=True)


def frame():
    rows = []
    for coin in CT.COINS:
        ms = json.load(open(os.path.join(RAW, f"m15_{coin}.json")))
        book = json.load(open(os.path.join(RAW, f"book_{coin}.json")))
        cb = {int(r[0]): r for r in json.load(open(os.path.join(RAW, f"cb_{coin}.json")))}
        for m in ms:
            if str(m.get("result")).lower() not in ("yes", "no"):
                continue
            close = int(CT._ts(m["close_time"]))
            last = cb.get(close - 60)
            typ = (last[1] + last[2] + last[3] + last[4]) / 4.0 if last else None
            bk = book.get(m["ticker"]) or []
            for look in LOOKS:
                t = close - look
                lv = cb.get(t - 60)
                closes = [cb[x][4] for x in range(t - 300 * 60, t, 60) if x in cb]
                if not lv or len(closes) < 250:
                    continue
                b = [k for k in bk if k[0] <= t]
                bid = ask = None
                if b and t - b[-1][0] <= 120:
                    bid, ask = CT._cents(b[-1][1]), CT._cents(b[-1][2])
                    if bid is None or ask is None or not ask > bid:
                        bid = ask = None
                sig = [CT.ewma_sigma(closes, h, 1.0) for h in HALFLIVES]
                rows.append([coin, m["ticker"], close, look, 1 if m["result"].lower() == "yes" else 0, lv[4],
                             bid, ask, CT._f(m.get("expiration_value")), typ,
                             m.get("rules_primary"), m.get("rules_secondary"), m.get("strike_type"),
                             m.get("floor_strike"), json.dumps(m.get("custom_strike") or {})] +
                            [f"{s:.6g}" if s else "" for s in sig])
    os.makedirs(os.path.dirname(FRAME), exist_ok=True)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(COLS)
    w.writerows(rows)
    with gzip.open(FRAME, "wt", encoding="utf-8", compresslevel=9) as f:
        f.write(buf.getvalue())
    print(len(rows), "rows ->", FRAME, os.path.getsize(FRAME), "bytes")


def load():
    out = []
    with gzip.open(FRAME, "rt", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            m = {"rules_primary": r["rules_primary"], "rules_secondary": r["rules_secondary"],
                 "strike_type": r["strike_type"], "floor_strike": r["floor_strike"] or None,
                 "custom_strike": json.loads(r["custom_strike"] or "{}"), "close_time": _iso(int(r["close_ts"]))}
            out.append({"coin": r["coin"], "ticker": r["ticker"], "close_ts": int(r["close_ts"]),
                        "look": int(r["look"]), "y": int(r["result"]), "level": float(r["level"]),
                        "bid": float(r["bid"]) if r["bid"] else None, "ask": float(r["ask"]) if r["ask"] else None,
                        "settle": float(r["settle"]) if r["settle"] else None,
                        "typ": float(r["typ_close"]) if r["typ_close"] else None,
                        "rule": CT.parse_rule(m, r["coin"]),
                        "sig": {h: float(r[f"s{h}"]) if r[f"s{h}"] else None for h in HALFLIVES}})
    return out


def _ll(p, y):
    p = min(max(p, 0.001), 0.999)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def proxy_sd(rows):
    per = {}
    seen = set()
    for r in rows:
        if r["close_ts"] < SPLIT and r["settle"] and r["typ"] and r["ticker"] not in seen:
            seen.add(r["ticker"])
            per.setdefault(r["coin"], []).append(math.log(r["settle"] / r["typ"]) ** 2)
    rms = {c: math.sqrt(sum(v) / len(v)) for c, v in per.items()}
    vals = sorted(rms.values())
    return vals[len(vals) // 2], rms


def model_p(r, h, k, psd):
    s = r["sig"].get(h)
    if not r["rule"] or not s:
        return None
    return CT.p_yes(r["rule"], r["level"], s * k, r["look"], proxy_sd=psd)


def choose(rows, psd):
    dev = [r for r in rows if r["close_ts"] < SPLIT and r["look"] == 300]
    grid = {}
    for h in HALFLIVES:
        for k in MULTS:
            ll = [_ll(p, r["y"]) for r in dev for p in [model_p(r, h, k, psd)] if p is not None]
            grid[(h, k)] = sum(ll) / len(ll)
    best = min(grid, key=grid.get)
    return best, grid, len(dev)


def summary(sub, h, k, psd):
    """Model vs Kalshi on the rows priced both ways; the paired difference's
    SE over rows (one per market at one look)."""
    d, lm, lk, bm, bk = [], [], [], [], []
    yes = 0
    for r in sub:
        p = model_p(r, h, k, psd)
        if p is None or r["bid"] is None:
            continue
        q = (r["bid"] + r["ask"]) / 200.0
        a, b = _ll(p, r["y"]), _ll(q, r["y"])
        lm.append(a)
        lk.append(b)
        d.append(a - b)
        bm.append((p - r["y"]) ** 2)
        bk.append((q - r["y"]) ** 2)
        yes += r["y"]
    n = len(d)
    if not n:
        return {"n": 0}
    md = sum(d) / n
    se = math.sqrt(max(0.0, sum(x * x for x in d) / n - md * md) / n) if n > 1 else None
    return {"n": n, "yes_rate": round(yes / n, 3), "model_ll": round(sum(lm) / n, 4), "kalshi_ll": round(sum(lk) / n, 4),
            "model_brier": round(sum(bm) / n, 4), "kalshi_brier": round(sum(bk) / n, 4),
            "diff": round(md, 4), "se": round(se, 4) if se else None,
            "z": round(md / se, 2) if se else None}


def reliability(sub, h, k, psd):
    bins = {}
    for r in sub:
        p = model_p(r, h, k, psd)
        if p is None:
            continue
        b = min(int(p * 10), 9)
        x = bins.setdefault(b, [0, 0.0, 0])
        x[0] += 1
        x[1] += p
        x[2] += r["y"]
    return [{"bin": b, "n": x[0], "said": round(x[1] / x[0], 3), "hit": round(x[2] / x[0], 3)}
            for b, x in sorted(bins.items())]


def paper(sub, h, k, psd):
    pnl = []
    for r in sub:
        p = model_p(r, h, k, psd)
        if p is None or r["bid"] is None:
            continue
        ya, na = r["ask"], 100.0 - r["bid"]
        ey = 100.0 * p - ya - _fee(ya)
        en = 100.0 * (1 - p) - na - _fee(na)
        if ey >= EDGE_C and ey >= en:
            pnl.append((100.0 if r["y"] == 1 else 0.0) - ya - _fee(ya))
        elif en >= EDGE_C:
            pnl.append((100.0 if r["y"] == 0 else 0.0) - na - _fee(na))
    n = len(pnl)
    if not n:
        return {"bets": 0}
    m = sum(pnl) / n
    se = math.sqrt(max(0.0, sum(x * x for x in pnl) / n - m * m) / n) if n > 1 else None
    return {"bets": n, "per_bet_c": round(m, 2), "se_c": round(se, 2) if se else None,
            "total_c": round(sum(pnl), 1), "win_rate": round(sum(1 for x in pnl if x > 0) / n, 3)}


def score(rows=None):
    rows = rows if rows is not None else load()
    psd, psd_by = proxy_sd(rows)
    (h, k), grid, n_dev = choose(rows, psd)
    test = [r for r in rows if r["close_ts"] >= SPLIT]
    out = {"proxy_sd": round(psd, 6), "proxy_sd_by_coin": {c: round(v, 6) for c, v in psd_by.items()},
           "chosen": {"halflife_min": h, "mult": k}, "dev_rows_300": n_dev,
           "dev_grid_top": sorted([round(v, 5), hh, kk] for (hh, kk), v in grid.items())[:5],
           "unparsed_rules": sum(1 for r in rows if not r["rule"]), "rows": len(rows),
           "markets": len({r["ticker"] for r in rows}), "heldout": {}}
    for look in LOOKS:
        sub = [r for r in test if r["look"] == look]
        out["heldout"][str(look)] = {"pooled": summary(sub, h, k, psd),
                                     "by_coin": {c: summary([r for r in sub if r["coin"] == c], h, k, psd)
                                                 for c in CT.COINS}}
    sub300 = [r for r in test if r["look"] == 300]
    out["heldout_reliability_300"] = reliability(sub300, h, k, psd)
    out["heldout_paper_300"] = paper(sub300, h, k, psd)
    out["dev_paper_300"] = paper([r for r in rows if r["close_ts"] < SPLIT and r["look"] == 300], h, k, psd)
    p = out["heldout"]["300"]["pooled"]
    pp = out["heldout_paper_300"]
    out["decision"] = {
        "edges": "model" if p.get("se") and p["diff"] < -2 * p["se"] else "unproven (Kalshi the better forecaster)"
        if p.get("se") and p["diff"] > 2 * p["se"] else "unproven (no difference past 2 SE)",
        "paper": "profitable" if pp.get("se_c") and pp["per_bet_c"] > 2 * pp["se_c"] else "not profitable past 2 SE"}
    return out


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "score"
    if cmd == "fetch":
        fetch()
    elif cmd == "frame":
        frame()
    else:
        print(json.dumps(score(), indent=1))
