"""The goals model against the closing market: does the model know anything
the market does not, and does leaning on the market make the board's figure
more accurate? Chosen on 2024, tested on 2025, confirmed on 2026. RESEARCH.

Nothing in the soccer stack had been tested against a market. Kalshi keeps
no price history, so the market here is the sharpest public one, Pinnacle's
CLOSING three-way price, from football-data.co.uk (results and closing odds
published free for this use; 27 leagues the model also rates, 98% of their 2024-26 matches joined to ESPN's). The live
board compares the model with Kalshi's PRE-GAME price, a thinner and earlier
market than Pinnacle at the close; what this test says about the market's
weight is therefore an upper bound for Kalshi's.

THE RULE (written before any number below was computed)
  The model is the one soccer.py ships when this runs (soccer.fit with XI,
  KAPPA, RHO and soccer.SHOTS_W; the weekly walk-forward in production's
  window, research/soccer_shots_validation.py's construction), predicting
  every match of the year.
  The market: Pinnacle's closing price (PSCH/PSCD/PSCA), de-vigged by
  normalising the implied probabilities; the average closing price
  (AvgCH/D/A) where Pinnacle's is missing; a match with neither is dropped.
  A football-data match is joined to ESPN's on the mapped league, the date
  +-1 day and the same 90-minute score; two candidates are told apart by
  the team names; no candidate, no row (the join rate is reported).
  The blend is the logarithmic pool p ~ p_model^a * p_market^(1-a),
  renormalised. Grid a in {0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.7, 1}.
  1. SELECT a on the joined 2024 matches: the lowest mean three-way log loss.
  2. TEST on 2025, a frozen:
     B1 (does the model add to the market?): the blend at a must beat the
        market alone (a = 0) by more than two standard errors of the
        per-match log-loss difference.
     B2 (does the market add to the model?): the blend at a must beat the
        model alone (a = 1) by more than two standard errors.
  3. CONFIRM on 2026 (1 January to the data's last match): a test that
     passed but goes the other way on 2026 by more than two standard errors
     is void. 2026 cannot promote anything.
  What ships: B2 passing makes the board's headline the pool of the model
  and Kalshi's de-vigged pre-game price at the chosen a (the raw model kept
  beside it, on the record and for the quick picks' disagreement rule); B2
  failing leaves the headline the model's. B1 decides what the board says:
  passing, that the model adds information the closing market lacks;
  failing, that it adds none measurable, so its edges against Kalshi are the
  gap between Kalshi and the sharp market at best.

Usage: python3 -m research.soccer_market_validation --rows DIR --fd DIR [--jobs N]
  --rows as research/soccer_shots_validation.py's; --fd holds football-data
  files: {DIV}_{yyyy}.csv (2223, 2324, 2425, 2526, 2627) and new_{CODE}.csv.
Writes research/data/soccer_market_validation.json.
"""
import argparse
import csv
import datetime
import json
import math
import multiprocessing as mp
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import soccer  # noqa: E402
from research import soccer_shots_validation as SV  # noqa: E402

FD_MAIN = {"E0": "eng.1", "E1": "eng.2", "E2": "eng.3", "SC0": "sco.1", "D1": "ger.1", "D2": "ger.2",
           "I1": "ita.1", "I2": "ita.2", "SP1": "esp.1", "SP2": "esp.2", "F1": "fra.1", "F2": "fra.2",
           "N1": "ned.1", "B1": "bel.1", "P1": "por.1", "T1": "tur.1", "G1": "gre.1"}
FD_NEW = {"ARG": "arg.1", "AUT": "aut.1", "BRA": "bra.1", "CHN": "chn.1", "DNK": "den.1", "JPN": "jpn.1",
          "MEX": "mex.1", "NOR": "nor.1", "SWE": "swe.1", "USA": "usa.1"}
GRID = (0.0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.7, 1.0)
SELECT, TEST, CONFIRM = 2024, 2025, 2026
DATA, COEF, TARGET = [], [], {}


def _date(s):
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            pass
    return None


def _devig(row, keys):
    try:
        odds = [float(row[k]) for k in keys]
    except (KeyError, TypeError, ValueError):
        return None
    if any(o <= 1.0 for o in odds):
        return None
    inv = [1.0 / o for o in odds]
    s = sum(inv)
    return [x / s for x in inv]


def fd_matches(fd_dir):
    """[(date, slug, home name, away name, hg, ag, (pH, pD, pA), source)]"""
    out = []
    for name in sorted(os.listdir(fd_dir)):
        m = re.match(r"^([A-Z0-9]+)_(\d{4})\.csv$", name)
        n = re.match(r"^new_([A-Z]+)\.csv$", name)
        if m and m.group(1) in FD_MAIN:
            slug, hk, ak, hgk, agk = FD_MAIN[m.group(1)], "HomeTeam", "AwayTeam", "FTHG", "FTAG"
        elif n and n.group(1) in FD_NEW:
            slug, hk, ak, hgk, agk = FD_NEW[n.group(1)], "Home", "Away", "HG", "AG"
        else:
            continue
        with open(os.path.join(fd_dir, name), encoding="utf-8-sig", errors="replace") as fh:
            for row in csv.DictReader(fh):
                d = _date(row.get("Date") or "")
                if not d or d.year < 2024:
                    continue
                try:
                    hg, ag = int(row[hgk]), int(row[agk])
                except (KeyError, TypeError, ValueError):
                    continue
                p, src = _devig(row, ("PSCH", "PSCD", "PSCA")), "pinnacle"
                if p is None:
                    p, src = _devig(row, ("AvgCH", "AvgCD", "AvgCA")), "average"
                if p is None:
                    continue
                out.append((d, slug, row.get(hk) or "", row.get(ak) or "", hg, ag, tuple(p), src))
    return out


def _toks(s):
    return set(soccer._tokens(s or ""))


def join(fd, data, names):
    """{espn row: (market probs, source)} and the join tally."""
    by = {}
    for row, _sh in data:
        by.setdefault((row[1], row[0], row[4], row[5]), []).append(row)
    out, tally = {}, {"fd": len(fd), "joined": 0, "none": 0, "ambiguous": 0}
    for d, slug, hn, an, hg, ag, p, src in fd:
        cands = []
        for dd in (0, -1, 1):
            cands += by.get((slug, (d + datetime.timedelta(days=dd)).toordinal(), hg, ag), [])
        if len(cands) > 1:
            th, ta = _toks(hn), _toks(an)
            sc = sorted(((len(th & _toks(names.get(r[2]))) + len(ta & _toks(names.get(r[3]))), r) for r in cands),
                        key=lambda t: -t[0])
            if sc[0][0] == 0 or (len(sc) > 1 and sc[0][0] == sc[1][0]):
                tally["ambiguous"] += 1
                continue
            cands = [sc[0][1]]
        if not cands:
            tally["none"] += 1
            continue
        out[cands[0]] = (p, src)
        tally["joined"] += 1
    return out, tally


def one_week(args):
    start, end = args
    year = datetime.date.fromordinal(start).year
    lo = datetime.date(year - (soccer._FIT_YEARS - 1), 1, 1).toordinal()
    train = [(r, s) for r, s in DATA if lo <= r[0] < start]
    test = [r for r, _s in DATA if start <= r[0] < end and r in TARGET]
    if not train or not test:
        return []
    w = getattr(soccer, "SHOTS_W", 0.0)
    m = soccer.fit([SV.pseudo(r, s, w) for r, s in train], start)
    out = []
    for r in test:
        lh, la = soccer.rates(m, r[2], r[3], r[1], r[6])
        p = soccer.outcome(soccer.grid(lh, la, soccer.RHO))
        k = 0 if r[4] > r[5] else 1 if r[4] == r[5] else 2
        out.append({"y": year, "k": k, "model": list(p), "mkt": list(TARGET[r][0]), "src": TARGET[r][1],
                    "comp": r[1]})
    return out


def _init(data, coef, target):
    DATA[:] = data
    COEF[:] = coef
    SV.COEF[:] = coef
    TARGET.clear()
    TARGET.update(target)


def pool(pm, pk, a):
    z = [math.exp(a * math.log(max(1e-12, x)) + (1 - a) * math.log(max(1e-12, y))) for x, y in zip(pm, pk)]
    s = sum(z)
    return [v / s for v in z]


def ll(rows, a):
    return [-math.log(max(1e-12, pool(r["model"], r["mkt"], a)[r["k"]])) for r in rows]


def cmp(rows, a, b):
    """mean and SE of log loss(a) - log loss(b), per match"""
    la, lb = ll(rows, a), ll(rows, b)
    d = [x - y for x, y in zip(la, lb)]
    n = len(d)
    m = sum(d) / n
    sd = (sum((x - m) ** 2 for x in d) / max(1, n - 1)) ** 0.5
    return {"n": n, "mean": round(m, 5), "se": round(sd / n ** 0.5, 5)}


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", required=True)
    ap.add_argument("--fd", required=True)
    ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args(argv)
    data = SV.load(a.rows)
    names = {}
    for f in os.listdir(a.rows):
        names.update(json.load(open(os.path.join(a.rows, f))).get("names") or {})
    coef = SV.conversion(data)
    fd = fd_matches(a.fd)
    target, tally = join(fd, data, names)
    last = datetime.date.fromordinal(max(r[0] for r, _s in data))
    wk = list(SV.weeks(SELECT)) + list(SV.weeks(TEST)) + list(SV.weeks(CONFIRM, last_day=last + datetime.timedelta(days=1)))
    t0 = time.time()
    with mp.get_context("fork").Pool(a.jobs, initializer=_init, initargs=(data, coef, target)) as pool_:
        rows = [r for part in pool_.map(one_week, wk, chunksize=2) for r in part]
    res = {"rule": __doc__.split("Usage:")[0], "data_last_day": last.isoformat(), "join": tally,
           "shots_w": getattr(soccer, "SHOTS_W", 0.0), "seconds": round(time.time() - t0)}
    for y in (SELECT, TEST, CONFIRM):
        ys = [r for r in rows if r["y"] == y]
        res[str(y)] = {"n": len(ys), "pinnacle": sum(1 for r in ys if r["src"] == "pinnacle"),
                       "logloss": {str(g): round(sum(ll(ys, g)) / len(ys), 5) for g in GRID},
                       "model_minus_market": cmp(ys, 1.0, 0.0)}
    sel = res[str(SELECT)]["logloss"]
    chosen = min(GRID, key=lambda g: sel[str(g)])
    t = [r for r in rows if r["y"] == TEST]
    c = [r for r in rows if r["y"] == CONFIRM]
    b1t, b2t = cmp(t, chosen, 0.0), cmp(t, chosen, 1.0)
    b1c, b2c = cmp(c, chosen, 0.0), cmp(c, chosen, 1.0)
    dec = {"chosen_a": chosen,
           "B1_test": b1t, "B1_passes": chosen > 0 and b1t["mean"] < -2 * b1t["se"],
           "B1_confirm": b1c, "B1_void": b1c["mean"] > 2 * b1c["se"],
           "B2_test": b2t, "B2_passes": chosen < 1 and b2t["mean"] < -2 * b2t["se"],
           "B2_confirm": b2c, "B2_void": b2c["mean"] > 2 * b2c["se"]}
    dec["B1"] = bool(dec["B1_passes"] and not dec["B1_void"])
    dec["B2"] = bool(dec["B2_passes"] and not dec["B2_void"])
    dec["ship_market_weight"] = round(1 - chosen, 3) if dec["B2"] else 0.0
    res["decision"] = dec
    # the per-competition view of the model against the market, 2025 and 2026 pooled
    comps = {}
    for r in t + c:
        comps.setdefault(r["comp"], []).append(r)
    res["by_comp"] = {k: dict(cmp(v, 1.0, 0.0), n=len(v)) for k, v in sorted(comps.items()) if len(v) >= 150}
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "soccer_market_validation.json")
    with open(out, "w") as fh:
        json.dump(res, fh, indent=1)
    print(json.dumps({k: res[k] for k in ("join", "shots_w", "decision", "seconds")}, indent=1))
    for y in (SELECT, TEST, CONFIRM):
        print(y, res[str(y)]["n"], res[str(y)]["logloss"], res[str(y)]["model_minus_market"])


if __name__ == "__main__":
    main(sys.argv[1:])
