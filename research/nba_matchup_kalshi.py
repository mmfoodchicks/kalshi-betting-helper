"""Genuine historical Kalshi NBA player-prop prices for the matchup engine's
market comparison (RESEARCH ONLY; nothing in production reads this).

Kalshi's public API keeps settled markets in a historical tier:
/historical/markets (by series), /historical/markets/{ticker}/candlesticks
(hourly here). The player-prop series launched 2025-11-14 (KXNBAPTS's
history: close times 2025-11-19 .. 2026-06-14, 23,562 markets), so only the
TEST season 2025-26 has prices; nothing earlier exists.

PRE-TIP ONLY: a market's price is the last hourly candle that ENDED at or
before the game's scheduled start (`occurrence_datetime`): its yes bid and
yes ask closes. In-game candles are never read (the CLAUDE.md rule: in-game
information must not touch a number that claims to be pre-game).

The pull is slow on purpose (the API answers 429 with no Retry-After when
pressed; four of twelve quick requests were refused on 2026-10-23): one
request about every 1.1 s, backing off on 429, every response cached under
<raw>/kalshi_hist/ so a re-run resumes.

  python3 research/nba_matchup_kalshi.py list <raw>
  python3 research/nba_matchup_kalshi.py candles <raw> --n 1500 --seed 20261023
"""
import argparse
import datetime
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = ("KXNBAPTS", "KXNBAREB", "KXNBAAST", "KXNBA3PT", "KXNBAPRA")
STAT_OF = {"KXNBAPTS": "pts", "KXNBAREB": "reb", "KXNBAAST": "ast", "KXNBA3PT": "tpm", "KXNBAPRA": "pra"}
# the 2025-26 regular season ended 2026-04-12 (ESPN's schedule); props after
# are the play-in / playoffs, which the test season's rows do not include
REG_END = "2026-04-13"
_last = [0.0]


def _get(url, tries=8):
    for i in range(tries):
        wait = 1.1 - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(4.0 * (i + 1))
                continue
            if e.code == 404:
                return None
            time.sleep(2.0 * (i + 1))
        except Exception:
            time.sleep(2.0 * (i + 1))
    return None


def list_series(raw, series):
    d = os.path.join(raw, "kalshi_hist")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{series}_markets.json")
    if os.path.exists(path):
        with open(path) as fh:
            return json.load(fh)
    out, cur = [], None
    while True:
        js = _get(f"{BASE}/historical/markets?series_ticker={series}&limit=1000" + (f"&cursor={cur}" if cur else ""))
        if not js:
            break
        for m in js.get("markets") or []:
            out.append({k: m.get(k) for k in ("ticker", "event_ticker", "title", "yes_sub_title", "floor_strike",
                                               "strike_type", "result", "occurrence_datetime", "open_time",
                                               "close_time", "volume_fp", "custom_strike", "settlement_value_dollars")})
        cur = js.get("cursor")
        if not cur or not js.get("markets"):
            break
    with open(path + ".tmp", "w") as fh:
        json.dump(out, fh)
    os.replace(path + ".tmp", path)
    print(f"[kalshi] {series}: {len(out)} markets", flush=True)
    return out


def _ts(iso):
    return int(datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


def pretip(m, candles):
    """(yes_bid, yes_ask, end_ts, volume to then) of the last hourly candle
    ending at or before the scheduled start, or None."""
    tip = m.get("tip_ts") or _ts(m["occurrence_datetime"])
    best = None
    vol = 0.0
    for c in candles or []:
        if c["end_period_ts"] > tip:
            break
        vol += float(c.get("volume") or 0)
        b, a = (c.get("yes_bid") or {}).get("close"), (c.get("yes_ask") or {}).get("close")
        if b is None or a is None:
            continue
        best = (float(b), float(a), c["end_period_ts"], vol)
    return best


# Kalshi's team codes where ESPN's differ
K2E = {"GSW": "GS", "NOP": "NO", "NYK": "NY", "SAS": "SA", "UTA": "UTAH", "WAS": "WSH"}
_MON = {m: i + 1 for i, m in enumerate(("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"))}


def event_key(event_ticker):
    """KXNBAPTS-25NOV24DALMIA -> ("2025-11-24", away "DAL", home "MIA") in ESPN codes."""
    tail = event_ticker.split("-", 1)[1]
    yy, mon, dd, teams = tail[:2], tail[2:5], tail[5:7], tail[7:]
    date = f"20{yy}-{_MON[mon]:02d}-{dd}"
    away, home = teams[:3], teams[3:6]
    return date, K2E.get(away, away), K2E.get(home, home)


def tip_map(espn_raw, season=2025):
    """{(ET date, away, home): tip epoch} from ESPN's schedule (the raw
    summaries' schedule.json): the start time historical markets before
    2026-04 do not carry."""
    with open(os.path.join(espn_raw, str(season), "schedule.json")) as fh:
        evs = json.load(fh)
    out = {}
    for e in evs:
        if not e.get("date"):
            continue
        t = _ts(e["date"].replace("Z", ":00Z") if len(e["date"]) == 17 else e["date"])
        for shift in (5, 4):        # the ticker's date is the game's US date (EST / EDT)
            day = datetime.datetime.utcfromtimestamp(t - shift * 3600).date().isoformat()
            out.setdefault((day, e["away"], e["home"]), t)
    return out


def candles(raw, n_per_series=1500, seed=20261023, espn_raw=None):
    """A seeded random sample of PLAYER-GAMES per series (all of a sampled
    player-game's thresholds), regular season 2025-26, markets that traded
    and settled yes/no (a 'scalar' settlement is a player who never entered)."""
    d = os.path.join(raw, "kalshi_hist", "candles")
    os.makedirs(d, exist_ok=True)
    rng = random.Random(seed)
    tips = tip_map(espn_raw or os.path.join(raw, "espn_nba_summary"))
    for s in SERIES:
        ms = []
        for m in list_series(raw, s):
            if float(m.get("volume_fp") or 0) <= 0 or m.get("result") not in ("yes", "no"):
                continue
            try:
                key = event_key(m["event_ticker"])
            except (KeyError, ValueError, IndexError):
                continue
            tip = tips.get(key)
            if tip is None or key[0] >= REG_END:
                continue
            m["tip_ts"] = tip
            m["espn_key"] = list(key)
            ms.append(m)
        groups = {}
        for m in ms:
            groups.setdefault(m["ticker"].rsplit("-", 1)[0], []).append(m)
        keys = sorted(groups)
        rng.shuffle(keys)
        n = 0
        for k in keys:
            for m in groups[k]:
                if n >= n_per_series:
                    break
                p = os.path.join(d, m["ticker"] + ".json")
                n += 1
                if os.path.exists(p):
                    continue
                tip = m["tip_ts"]
                js = _get(f"{BASE}/historical/markets/{m['ticker']}/candlesticks?start_ts={tip - 4 * 86400}"
                          f"&end_ts={tip}&period_interval=60")
                with open(p + ".tmp", "w") as fh:
                    json.dump({"market": m, "candles": (js or {}).get("candlesticks"), "ok": js is not None}, fh)
                os.replace(p + ".tmp", p)
            if n >= n_per_series:
                break
        print(f"[kalshi] {s}: {n} markets sampled", flush=True)


def _norm(n):
    sys.path.insert(0, ROOT)
    import kalshi_nba_props as KP
    return KP.norm_name(n)


def compare(raw, preds_path, store_dir, out_path, n_boot=1000, seed=20261023):
    """The pre-registered market comparison (research/reports/nba_matchup.md):
    the shipped model's P(stat >= k) on each sampled market's player-game
    against the pre-tip mid, and the hypothetical one-contract bets."""
    import pickle
    import math
    sys.path.insert(0, ROOT)
    import prop_value as PV
    with open(preds_path, "rb") as fh:
        preds = pickle.load(fh)          # {(gid, pid): {stat: [sf...], "base": {stat: [sf...]}}}
    with open(os.path.join(store_dir, "nba_matchup_2025.pkl"), "rb") as fh:
        st = pickle.load(fh)["payload"]
    with open(os.path.join(raw, "espn_nba_summary", "2025", "schedule.json")) as fh:
        sched = json.load(fh)
    by_key = {}
    for e in sched:
        t = _ts(e["date"].replace("Z", ":00Z") if len(e["date"]) == 17 else e["date"])
        for shift in (5, 4):
            day = datetime.datetime.utcfromtimestamp(t - shift * 3600).date().isoformat()
            by_key.setdefault((day, e["away"], e["home"]), e["id"])
    names = {}
    for gid, g in st["games"].items():
        for pid, nm in (g.get("names") or {}).items():
            names[(gid, _norm(nm))] = pid
    d = os.path.join(raw, "kalshi_hist", "candles")
    recs = []
    unmatched = {"game": 0, "player": 0, "pred": 0, "quote": 0}
    mism = 0
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(d, fn)) as fh:
            js = json.load(fh)
        m = js["market"]
        stat = STAT_OF[m["ticker"].split("-")[0]]
        key = tuple(m["espn_key"])
        gid = by_key.get(key)
        if gid is None:
            unmatched["game"] += 1
            continue
        # "Name: 25+ points" now; "Name records 25+ points" on the early markets
        nm = (m.get("title") or "").split(":")[0].split(" records ")[0].strip()
        pid = names.get((gid, _norm(nm)))
        if pid is None:
            unmatched["player"] += 1
            continue
        pr = preds.get((gid, pid))
        if pr is None or stat not in pr:
            unmatched["pred"] += 1
            continue
        q = pretip(m, js.get("candles"))
        if q is None:
            unmatched["quote"] += 1
            continue
        bid, ask = q[0] * 100.0, q[1] * 100.0
        if not (bid > 0 and ask < 100 and ask > bid):
            unmatched["quote"] += 1
            continue
        k = int(round(float(m["floor_strike"]) + 0.5))
        sf = pr[stat]
        if k >= len(sf):
            continue
        y = 1 if m["result"] == "yes" else 0
        if (pr["y"][stat] >= k) != bool(y):
            mism += 1
        recs.append({"gid": gid, "pid": pid, "stat": stat, "k": k, "p": sf[k], "p_base": pr["base"][stat][k],
                     "bid": bid, "ask": ask, "mid": (bid + ask) / 200.0, "spread": ask - bid, "y": y,
                     "vol_pre": q[3], "ticker": m["ticker"]})

    def ll(p, y):
        p = min(1 - 1e-4, max(1e-4, p))
        return -(y * math.log(p) + (1 - y) * math.log(1 - p))

    def summary(rs):
        if not rs:
            return {"n": 0}
        out = {"n": len(rs), "games": len({r["gid"] for r in rs})}
        for arm in ("p", "p_base", "mid"):
            out[f"ll_{arm}"] = round(sum(ll(r[arm], r["y"]) for r in rs) / len(rs), 5)
            out[f"brier_{arm}"] = round(sum((r[arm] - r["y"]) ** 2 for r in rs) / len(rs), 5)
        bets = []
        for r in rs:
            yes_fee = PV.fee_order_c(r["ask"], 1)
            no_ask = 100.0 - r["bid"]
            no_fee = PV.fee_order_c(no_ask, 1)
            e_yes = r["p"] - (r["ask"] + yes_fee) / 100.0
            e_no = (1 - r["p"]) - (no_ask + no_fee) / 100.0
            if e_yes >= 0.03 and e_yes >= e_no:
                cost = r["ask"] + yes_fee
                bets.append((r["gid"], cost, 100.0 * r["y"] - cost, "yes"))
            elif e_no >= 0.03:
                cost = no_ask + no_fee
                bets.append((r["gid"], cost, 100.0 * (1 - r["y"]) - cost, "no"))
        if bets:
            import random as _r
            rng = _r.Random(seed)
            gids = sorted({b[0] for b in bets})
            byg = {}
            for b in bets:
                byg.setdefault(b[0], []).append(b)
            boots = []
            for _ in range(n_boot):
                pick = [rng.choice(gids) for _ in gids]
                c = sum(b[1] for gg in pick for b in byg[gg])
                p_ = sum(b[2] for gg in pick for b in byg[gg])
                boots.append(p_ / c if c else 0.0)
            boots.sort()
            out["bets"] = {"n": len(bets), "yes": sum(1 for b in bets if b[3] == "yes"),
                           "roi": round(sum(b[2] for b in bets) / sum(b[1] for b in bets), 4),
                           "roi_ci90": [round(boots[int(0.05 * n_boot)], 4), round(boots[int(0.95 * n_boot)], 4)],
                           "profit_c": round(sum(b[2] for b in bets), 1)}
        else:
            out["bets"] = {"n": 0}
        return out

    res = {"unmatched": unmatched, "outcome_mismatch": mism, "all": summary(recs),
           "tight": summary([r for r in recs if r["spread"] <= 10]),
           "by_stat": {s: summary([r for r in recs if r["stat"] == s]) for s in sorted({r["stat"] for r in recs})}}
    with open(out_path, "w") as fh:
        json.dump(res, fh, indent=1)
    return res


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd")
    ap.add_argument("raw")
    ap.add_argument("--n", type=int, default=1500)
    ap.add_argument("--seed", type=int, default=20261023)
    ap.add_argument("--preds")
    ap.add_argument("--store")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        for s in SERIES:
            list_series(a.raw, s)
    elif a.cmd == "candles":
        candles(a.raw, a.n, a.seed)
    elif a.cmd == "compare":
        r = compare(a.raw, a.preds, a.store, a.out)
        print(json.dumps(r, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
