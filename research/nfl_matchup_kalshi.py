"""Genuine pre-kickoff Kalshi NFL player-prop prices for the matchup model's
market comparison (RESEARCH ONLY; nothing in production reads this).

Kalshi keeps markets settled before its historical cutoff (2026-08-23 when
read on 2026-10-23: /historical/cutoff) in the historical tier
(/historical/markets, /historical/markets/{ticker}/candlesticks) -- that is
the 2025 season and its playoffs. Markets settled after the cutoff (2026
weeks 1-6) are in the live tier (/markets?status=settled,
/series/{series}/markets/{ticker}/candlesticks).

PRE-KICKOFF ONLY: a market's price is the last hourly candle that ENDED at or
before the game's kickoff (the nflverse store's `kick`, the gamebook's start
time in US Eastern): its yes bid and yes ask closes. An in-game candle is never
read (CLAUDE.md: in-game information must not touch a number that claims to be
pre-game). The pull is slow on purpose (the API answers 429 with no
Retry-After): one request every ~1.5 s, backing off on 429, every response
cached under <raw>/kalshi_nfl_hist/ so a re-run resumes. Raw prices are never
committed (Kalshi's developer terms limit redistribution); only the summary
statistics the report quotes are.

    python3 research/nfl_matchup_kalshi.py list <raw>
    python3 research/nfl_matchup_kalshi.py candles <raw> --n 1200 --seed 20261023
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

BASE = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = ("KXNFLRECYDS", "KXNFLREC", "KXNFLRSHYDS", "KXNFLPASSYDS", "KXNFLTD")
STAT_OF = {"KXNFLRECYDS": "rec_yd", "KXNFLREC": "rec", "KXNFLRSHYDS": "rush_yd",
           "KXNFLPASSYDS": "pass_yd", "KXNFLTD": "td"}
KEEP = ("ticker", "event_ticker", "title", "yes_sub_title", "floor_strike", "strike_type", "result",
        "occurrence_datetime", "open_time", "close_time", "volume_fp", "custom_strike",
        "settlement_value_dollars", "status")
_last = [0.0]
GAP_S = 1.5


def _get(url, tries=8):
    for i in range(tries):
        wait = GAP_S - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(5.0 * (i + 1))
                continue
            if e.code == 404:
                return None
            time.sleep(2.0 * (i + 1))
        except (urllib.error.URLError, OSError, ValueError):
            time.sleep(2.0 * (i + 1))
    return None


def _page_all(url0):
    out, cur = [], None
    while True:
        js = _get(url0 + (f"&cursor={cur}" if cur else ""))
        if not js:
            break
        for m in js.get("markets") or []:
            out.append({k: m.get(k) for k in KEEP})
        cur = js.get("cursor")
        if not cur or not js.get("markets"):
            break
    return out


def list_series(raw, series):
    d = os.path.join(raw, "kalshi_nfl_hist")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{series}_markets.json")
    if os.path.exists(path):
        with open(path) as fh:
            return json.load(fh)
    hist = _page_all(f"{BASE}/historical/markets?series_ticker={series}&limit=1000")
    for m in hist:
        m["tier"] = "historical"
    live = _page_all(f"{BASE}/markets?series_ticker={series}&status=settled&limit=1000")
    for m in live:
        m["tier"] = "live"
    out = hist + live
    with open(path + ".tmp", "w") as fh:
        json.dump(out, fh)
    os.replace(path + ".tmp", path)
    print(f"[kalshi] {series}: {len(hist)} historical + {len(live)} live settled markets", flush=True)
    return out


_MON = {m: i + 1 for i, m in enumerate(("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"))}


def event_key(event_ticker):
    """KXNFLREC-25SEP14CHIDET -> ("2025-09-14", "CHIDET") (the US date and the
    two team codes run together, away then home)."""
    tail = event_ticker.split("-", 1)[1]
    yy, mon, dd, teams = tail[:2], tail[2:5], tail[5:7], tail[7:]
    return f"20{yy}-{_MON[mon]:02d}-{dd}", teams


def candles(raw, kick_of, n_per_series=1200, seed=20261023):
    """A seeded sample of PLAYER-GAMES per series (all of a sampled
    player-game's rungs), regular-season games the store knows (kick_of:
    {(date, teams string): kickoff epoch}), markets that traded and settled
    yes / no."""
    d = os.path.join(raw, "kalshi_nfl_hist", "candles")
    os.makedirs(d, exist_ok=True)
    rng = random.Random(seed)
    for s in SERIES:
        groups = {}
        for m in list_series(raw, s):
            if float(m.get("volume_fp") or 0) <= 0 or m.get("result") not in ("yes", "no"):
                continue
            try:
                key = event_key(m["event_ticker"])
            except (KeyError, ValueError, IndexError):
                continue
            kick = kick_of.get(key)
            if kick is None:
                continue
            m["kick_ts"] = kick
            groups.setdefault(m["ticker"].rsplit("-", 1)[0], []).append(m)
        keys = sorted(groups)
        rng.shuffle(keys)
        n = 0
        for k in keys:
            if n >= n_per_series:
                break
            for m in groups[k]:
                p = os.path.join(d, m["ticker"] + ".json")
                n += 1
                if os.path.exists(p):
                    continue
                kick = m["kick_ts"]
                if m.get("tier") == "historical":
                    url = f"{BASE}/historical/markets/{m['ticker']}/candlesticks"
                else:
                    url = f"{BASE}/series/{s}/markets/{m['ticker']}/candlesticks"
                js = _get(f"{url}?start_ts={kick - 4 * 86400}&end_ts={kick}&period_interval=60")
                with open(p + ".tmp", "w") as fh:
                    json.dump({"market": m, "candles": (js or {}).get("candlesticks"), "ok": js is not None}, fh)
                os.replace(p + ".tmp", p)
        print(f"[kalshi] {s}: {n} markets sampled", flush=True)


def prekick(m, cands):
    """(yes_bid, yes_ask, end_ts, volume to then) of the last hourly candle
    that ended at or before kickoff, or None. Prices in cents."""
    kick = m["kick_ts"]
    best, vol = None, 0.0
    for c in cands or []:
        if c["end_period_ts"] > kick:
            break
        vol += float(c.get("volume") or c.get("volume_fp") or 0)
        b, a = _cents((c.get("yes_bid") or {})), _cents((c.get("yes_ask") or {}))
        if b is None or a is None:
            continue
        best = (b, a, c["end_period_ts"], vol)
    return best


def game_lists(seasons=(2025, 2026)):
    """{season: {"games": [...]}} straight from the deep store's pickles: only
    the schedule (id, date, home, away, kick) is read, so a store-schema bump
    in nfl_matchup_data never blocks the price pull."""
    import pickle
    d = os.environ.get("DEEP_CACHE_DIR") or ""
    out = {}
    for s in seasons:
        p = os.path.join(d, f"nfl_matchup_{s}.pkl")
        if os.path.exists(p):
            with open(p, "rb") as fh:
                rec = pickle.load(fh)
            out[s] = {"games": (rec.get("payload") or {}).get("games") or []}
    return out


def _cents(blk):
    """A candle side's close in cents. Both tiers answer in DOLLARS as strings
    (historical "close": "0.4900", live "close_dollars": "0.0400", read
    2026-10-23); a bare number is the older cents field."""
    v = blk.get("close_dollars")
    if v is None:
        v = blk.get("close")
    if v is None:
        return None
    if isinstance(v, str):
        try:
            return round(100.0 * float(v), 4)
        except ValueError:
            return None
    return float(v)


def market_name(m):
    """The player's name: the part of yes_sub_title ("Saquon Barkley: 90+")
    or of the title ("Darnell Washington: 6+ receptions") before the colon;
    historical titles read "Saquon Barkley records 90+ rushing yards"."""
    for t in (m.get("yes_sub_title"), m.get("title")):
        if t and ":" in t:
            return t.split(":", 1)[0].strip()
    t = m.get("title") or ""
    return t.split(" records ", 1)[0].strip() if " records " in t else t


def kick_map(stores):
    """{(US date, AWAYHOME in Kalshi codes): kickoff epoch} from the nflverse
    stores' games (the gamebook start time, US Eastern)."""
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    out = {}
    for st in stores.values():
        for g in st["games"]:
            kick = g.get("kick")
            if not kick:
                continue
            try:
                dt = datetime.datetime.strptime(kick, "%m/%d/%y, %H:%M:%S").replace(tzinfo=et)
            except ValueError:
                continue
            ts = int(dt.timestamp())
            # nflverse's LA is Kalshi's LAR; every other code is the same on both sides
            # (WAS included: 3,219 Kalshi markets carry it -- an identity map stood here, review N5)
            for a in (g["away"], {"LA": "LAR"}.get(g["away"], g["away"])):
                for h in (g["home"], {"LA": "LAR"}.get(g["home"], g["home"])):
                    out[(g["date"], a + h)] = ts
    return out


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd")
    ap.add_argument("raw")
    ap.add_argument("--n", type=int, default=1200)
    ap.add_argument("--seed", type=int, default=20261023)
    a = ap.parse_args(argv)
    if a.cmd == "list":
        for s in SERIES:
            list_series(a.raw, s)
    elif a.cmd == "candles":
        candles(a.raw, kick_map(game_lists()), a.n, a.seed)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
