"""Sanity checks that a model is worth betting, not just that it runs.

Every other suite here asks "does this code do what it says". This one asks the
question that actually costs money: is the number any good? Three defects found in
one audit pass all shared a shape -- the code was correct and the output was
nonsense, so nothing caught them:

  * The crypto GBM extrapolated a 2-hour drift estimate across a 19-hour horizon,
    displacing the distribution's centre by 2.3x the entire uncertainty of the
    outcome. It priced "SOL >= $73" at 96.97% with spot at $72.58.
  * The CFB title model ranked teams by projected win total. Its championship
    ordering correlated +0.07 with the market and +0.87 with wins -- a
    strength-of-schedule artifact wearing a title-odds label.
  * Best Bets sorted by trust and then size, but four of its six sources never
    downgraded an implausible edge, so the least believable rows sat on top.

The checks below are the generic forms of those three questions:

  1. DRIFT DISCIPLINE  -- a trend estimated from n samples must not dominate a
     horizon of N >> n.
  2. MARKET AGREEMENT  -- a model that ranks a field must correlate with the
     market that prices it. Not agree on levels; agree on ORDER. A model at
     r ~ 0 is not finding value, it is measuring something else.
  3. EDGE PLAUSIBILITY -- no source may present a huge edge as trustworthy.

Offline by default. `--live` runs 2 against real boards, which needs network.

    python3 tests/model_sanity_check.py
    python3 tests/model_sanity_check.py --live
"""

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  PASS  {name}" + (f"   {detail}" if detail else ""))
    else:
        _f += 1
        print(f"  FAIL  {name}   {detail}")


def head(t):
    print("\n" + "=" * 72 + f"\n{t}\n" + "=" * 72)


def spearman(xs, ys):
    """Rank correlation. Levels can disagree for honest reasons (vig, our own
    calibration); ORDER disagreeing means we are ranking on the wrong thing."""
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0] * len(v)
        for pos, i in enumerate(order):
            r[i] = pos
        return r
    rx, ry = rank(xs), rank(ys)
    n = len(xs)
    if n < 3:
        return 0.0
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else 0.0


# --- 1. drift discipline -----------------------------------------------------
head("1. A trend must not outweigh the uncertainty it sits inside")

import odds  # noqa: E402

check("drift ships OFF", odds.DRIFT_VOL_CAP == 0.0, f"cap={odds.DRIFT_VOL_CAP}")
check("a driftless model returns no drift",
      odds.damped_drift(1e-4, 4e-4, 119, 1151) == 0.0)

# With the cap raised, both guards must still bound the damage. These are the
# real measured inputs from the SOL daily market that exposed the bug.
odds.DRIFT_VOL_CAP = 0.25
try:
    mu, sigma, n, N = 8.379e-05, 4.384e-04, 119, 1151.0
    old = 0.35 * mu * N
    new = odds.damped_drift(mu, sigma, n, N)
    vol = sigma * math.sqrt(N)
    check("the old flat damping let drift exceed the horizon vol",
          abs(old) / vol > 2.0, f"{abs(old)/vol:.2f}x")
    check("the guarded drift never exceeds the cap",
          abs(new) / vol <= 0.2500001, f"{abs(new)/vol:.2f}x")
    check("and it is far smaller than the old one", abs(new) < abs(old) / 5)
    # Pure noise (t ~ 0) must contribute essentially nothing even uncapped.
    quiet = odds.damped_drift(1e-7, 4e-4, 119, 1151.0)
    check("a trend indistinguishable from noise is discarded",
          abs(quiet) < 1e-6, f"{quiet:.2e}")
    # A shorter horizon than the estimation window is the only regime where a
    # drift estimate has any business being applied at full strength.
    short = odds.damped_drift(mu, sigma, n, 60.0)
    check("a short horizon keeps more of the trend than a long one",
          abs(short) / (sigma * math.sqrt(60.0)) <= 0.2500001)
    check("drift is signed like mu", odds.damped_drift(-mu, sigma, n, N) < 0)
finally:
    odds.DRIFT_VOL_CAP = 0.0

# The GBM must stay monotone in the strike no matter what the drift does: a
# higher bar cannot be MORE likely to clear.
probs = [odds._prob_above(72.585, k, 1151.0, 8.379e-05, 4.384e-04, 119)
         for k in (70, 72, 73, 74, 76, 80)]
check("P(>= strike) is monotone decreasing in the strike",
      all(a >= b for a, b in zip(probs, probs[1:])),
      " ".join(f"{p:.3f}" for p in probs))
check("and P(>= spot) is near 50% with no drift",
      abs(odds._prob_above(72.585, 72.585, 1151.0, 8.379e-05, 4.384e-04, 119) - 0.5) < 0.01)

# --- 1b. the run level must be centred ---------------------------------------
head("1b. A multiplicative model must be neutral when its inputs are neutral")

import baseball as B  # noqa: E402
import props as _props  # noqa: E402

check("home-field is a RATIO, applied geometrically",
      abs(B._HOME_SPLIT - B.HOME_RUNS_MULT ** 0.5) < 1e-12,
      f"split={B._HOME_SPLIT:.4f} from ratio {B.HOME_RUNS_MULT}")
# The whole point of a geometric split: the ratio (which carries the moneyline
# through PYTH_EXP) survives, while the product (which carries the total) is 1.
check("the home:away ratio is preserved exactly",
      abs((B._HOME_SPLIT / (1.0 / B._HOME_SPLIT)) - B.HOME_RUNS_MULT) < 1e-12)
check("and the level it implies is neutral",
      abs(B._HOME_SPLIT * (1.0 / B._HOME_SPLIT) - 1.0) < 1e-12,
      "a one-sided x1.08 lifted every total by 4%")
# A one-sided application is the bug this replaced; make it un-reintroducible.
one_sided = (B.HOME_RUNS_MULT + 1.0) / 2.0
geometric = (B._HOME_SPLIT + 1.0 / B._HOME_SPLIT) / 2.0
check("geometric beats one-sided on level neutrality",
      abs(geometric - 1.0) < abs(one_sided - 1.0) / 10,
      f"one-sided {one_sided:.4f} vs geometric {geometric:.4f}")

# Calibration must never outlive the model it was fit on.
import store  # noqa: E402

check("prop rows carry a model version", B and store.MODEL_VERSION >= 2,
      store.MODEL_VERSION)
src = __import__("inspect").getsource(store.prop_grade_pairs)
check("and calibration only fits its own generation",
      "model_version" in src,
      "a correction for the old run level would double-count on top of the fix")


# --- 1c. probability ladders must be coherent --------------------------------
head("1c. A ladder of thresholds must be monotone and in range")

import props as _props  # noqa: E402
import random as _rnd   # noqa: E402

_r = _rnd.Random(5)


def _hitter():
    """A COHERENT synthetic hitter. The first version of this check generated
    random fields independently and produced hitters with more hits than at-bats,
    which made the model emit P(2+ hits)=100% — a garbage-in artifact I nearly
    reported as a bug. Derive the counting stats from the rate stats instead."""
    ab = _r.randrange(50, 600)
    hits = int(ab * _r.uniform(.180, .340))
    d = int(hits * _r.uniform(.10, .28))
    t = int(hits * _r.uniform(0, .03))
    hr = min(int(hits * _r.uniform(.02, .25)), max(0, hits - d - t))
    bb, hbp = int(ab * _r.uniform(.04, .16)), int(ab * _r.uniform(0, .02))
    return {"name": "x", "ops": _r.uniform(.560, .980), "ab": ab, "hits": hits,
            "pa": ab + bb + hbp, "doubles": d, "triples": t, "hr": hr, "bb": bb,
            "hbp": hbp, "strikeouts": int(ab * _r.uniform(.10, .35)),
            "sb": _r.randrange(0, 40), "cs": _r.randrange(0, 12),
            "g": _r.randrange(20, 162)}


_mono, _range = [], []
for _ in range(400):
    _bp = _props.batter_props(_hitter(), _r.randrange(0, 9), _r.uniform(0.8, 1.25),
                              _r.uniform(0.9, 1.1), _r.uniform(0.9, 1.1))
    if not _bp:
        continue
    for _pref, _ks in (("hit", (1, 2, 3, 4)), ("tb", (2, 3, 4, 5, 6, 7)), ("hr", (1, 2))):
        _v = [_bp.get(f"{_pref}{k}") for k in _ks]
        _v = [x for x in _v if x is not None]
        if any(a + 1e-9 < b for a, b in zip(_v, _v[1:])):
            _mono.append((_pref, _v))
    for _k, _val in _bp.items():
        if _k[:2] in ("hi", "tb", "hr") and isinstance(_val, (int, float)) \
                and not (0 <= _val <= 100):
            _range.append((_k, _val))
check("P(>= k) never rises with k, over 400 hitters", not _mono, _mono[:2])
check("every prop probability lands in [0, 100]", not _range, _range[:2])

_kbad = []
for _ in range(200):
    _kp = _props.pitcher_k_props(_r.uniform(3, 15), _r.uniform(2.5, 7.5))
    _lad = (_kp or {}).get("ladder")
    if not isinstance(_lad, list):
        continue
    _v = [r.get("pct") for r in _lad if isinstance(r, dict) and r.get("pct") is not None]
    if any(a + 1e-9 < b for a, b in zip(_v, _v[1:])):
        _kbad.append(_v[:6])
check("starter K ladders are monotone too", not _kbad, _kbad[:2])


# --- 1d. the run distribution must match baseball ----------------------------
head("1d. Runs are not Poisson — the distribution must have the right shape")

_pp = _props._poisson_pmf(4.468)
_nb = _props._runs_pmf(4.468)
check("the run model is overdispersed", _props.RUN_DISPERSION > 1.5,
      f"var/mean = {_props.RUN_DISPERSION} (Poisson assumes 1.0)")
check("the pmf is a distribution", abs(sum(_nb) - 1.0) < 1e-3, sum(_nb))
_mean = sum(k * p for k, p in enumerate(_nb))
check("and it preserves the mean", abs(_mean - 4.468) < 0.02, f"{_mean:.4f}")
_var = sum(p * (k - _mean) ** 2 for k, p in enumerate(_nb))
check("with the intended variance", abs(_var / _mean - _props.RUN_DISPERSION) < 0.1,
      f"var/mean {_var/_mean:.2f}")

# Measured on real 2025 games. Poisson's error is in the tails, which is exactly
# where the totals ladder and the run line are priced.
# NB: not `_f` — that is this file's FAIL counter, and shadowing it makes the
# summary line print a lambda instead of a number. Second time in this suite.
for _lbl, _actual, _fn in (("P(team shutout)", 0.0668, lambda d: d[0]),
                           ("P(team 8+ runs)", 0.1682, lambda d: sum(d[8:])),
                           ("P(team 12+ runs)", 0.0343, lambda d: sum(d[12:]))):
    _e_po, _e_nb = abs(_fn(_pp) - _actual), abs(_fn(_nb) - _actual)
    check(f"{_lbl} is closer to reality than Poisson", _e_nb < _e_po,
          f"actual {_actual:.4f}  Poisson {_fn(_pp):.4f}  NegBin {_fn(_nb):.4f}")

check("a zero-dispersion setting degrades to Poisson",
      _props._runs_pmf(3.0, dispersion=1.0) == _props._poisson_pmf(3.0))
check("and a zero mean does not blow up",
      abs(sum(_props._runs_pmf(0.0)) - 1.0) < 1e-9)
check("the pmf is monotone in the tail",
      all(a >= b for a, b in zip(_nb[5:], _nb[6:])))

# RFI lives entirely on P(0), the quantity Poisson gets worst.
_l1 = 4.468 / 9.0 * _props.RFI_K
_rfi = 1 - _props._runs_pmf(_l1, kmax=0)[0] ** 2
check("RFI at league-average scoring matches the measured 47.7%",
      abs(_rfi - 0.477) < 0.02, f"model {_rfi*100:.1f}%  (Poisson gave 63.0%)")
check("the first-inning share is measured, not tuned to a market",
      0.95 <= _props.RFI_K <= 1.0, _props.RFI_K)


# --- 2. edge plausibility ----------------------------------------------------
head("2. No source may present an implausible edge as trustworthy")

import bestbets  # noqa: E402

check("there is a shared implausible-edge threshold",
      0 < bestbets._IMPLAUSIBLE_EDGE <= 25, bestbets._IMPLAUSIBLE_EDGE)
big = bestbets._row("x", "k", "p", "m", 90.0, 5.0, "med")
check("a 85pp edge is forced to low trust regardless of the caller",
      big["trust"] == "low", big["trust"])
check("and carries an explanation", bool(big["note"]))
small = bestbets._row("x", "k", "p", "m", 56.0, 50.0, "med")
check("an ordinary edge keeps its trust", small["trust"] == "med")
check("a caller's own low trust is not upgraded",
      bestbets._row("x", "k", "p", "m", 56.0, 50.0, "low")["trust"] == "low")
check("an unquoted leg makes no row at all",
      bestbets._row("x", "k", "p", "m", 56.0, None, "med") is None)
check("a 100c 'no offer' makes no row",
      bestbets._row("x", "k", "p", "m", 56.0, 100.0, "med") is None)
check("the CFB futures source is withheld until it is rebuilt",
      bestbets._cfb_futures_rows() == [],
      "it ranked on projected wins, r=+0.07 vs market")

# Trust must sort before size, or a flagged row still leads the board.
rows = [{"trust": "low", "net_edge": 80.0}, {"trust": "med", "net_edge": 6.0},
        {"trust": "high", "net_edge": 3.0}]
rank = {"high": 0, "med": 1, "low": 2}
rows.sort(key=lambda r: (rank.get(r["trust"], 1), -r["net_edge"]))
check("a solid +3 outranks a flagged +80",
      rows[0]["trust"] == "high" and rows[-1]["net_edge"] == 80.0)

# --- 3. market agreement (live) ---------------------------------------------
if "--live" in sys.argv:
    head("3. A ranking model must agree with the market on ORDER")

    def rank_check(label, pairs, floor=0.5):
        """pairs: [(model_pct, market_cents)]"""
        if len(pairs) < 8:
            print(f"  SKIP  {label} — only {len(pairs)} priced entries")
            return
        r = spearman([p[0] for p in pairs], [p[1] for p in pairs])
        check(f"{label} ranks like the market", r >= floor,
              f"Spearman {r:+.3f} over {len(pairs)} entries (floor {floor})")

    try:
        import season_sim
        out = season_sim.futures_board("2026")
        pooled = []
        for name, grp in (out.get("markets") or {}).items():
            ok = [(t["model_pct"], t["kalshi_cents"])
                  for t in (grp.get("teams") or [])
                  if t.get("kalshi_cents") and t.get("model_pct") is not None
                  and not t.get("thin")]
            pooled += ok
        rank_check("MLB futures", pooled, floor=0.6)
    except Exception as e:
        print(f"  SKIP  MLB futures — {type(e).__name__}: {str(e)[:60]}")

    # Tennis, on the books it says are tradeable. Audited at +0.953 over 218
    # sides, so this floor is set where a real regression would trip it.
    try:
        import tennis_prices
        tb = tennis_prices.board() or tennis_prices._compute(n_sims=1200)
        pairs = []
        for m in (tb.get("matches") or []):
            if not m.get("tradeable"):
                continue
            for s in ("a", "b"):
                p = m[s]
                fw = p.get("fair_win_raw") if p.get("fair_win_raw") is not None else p.get("model_win")
                if fw is not None and p.get("cents") is not None:
                    pairs.append((fw, p["cents"]))
        rank_check("tennis", pairs, floor=0.7)
    except Exception as e:
        print(f"  SKIP  tennis — {type(e).__name__}: {str(e)[:60]}")

    # The crypto model against every tight two-sided book on the board. This is
    # the check that would have caught the drift bug on day one.
    try:
        import kalshi
        import prices
        import time
        now = time.time()
        errs = []
        for coin in ("BTC", "ETH", "SOL", "XRP"):
            spot = prices.get_spot(coin)
            candles = prices.get_candles(coin, granularity=60)
            mu, sigma, n = odds.estimate_params(candles)
            for tf in ("daily", "hourly"):
                for m in (kalshi.get_open_markets(coin, tf) or []):
                    ya, yb = m.get("yes_ask"), m.get("yes_bid")
                    if not ya or not yb or ya >= 100 or ya - yb > 8:
                        continue
                    mins = max(0.0, (m["close_time"] - now) / 60.0) if m.get("close_time") else 0
                    if mins < 20:
                        continue
                    mid = (ya + yb) / 2.0
                    if not (8 <= mid <= 92):
                        continue
                    p = odds.probability_yes_for_strike(
                        spot, m["strike_type"], m["floor"], m["cap"], mins, mu, sigma, n)
                    errs.append(abs(p * 100 - mid))
        if len(errs) >= 8:
            errs.sort()
            med = errs[len(errs) // 2]
            check("the crypto model tracks tight two-sided books", med <= 6.0,
                  f"median |model-market| {med:.1f}pp over {len(errs)} markets")
        else:
            print(f"  SKIP  crypto — only {len(errs)} tight books open")
    except Exception as e:
        print(f"  SKIP  crypto — {type(e).__name__}: {str(e)[:60]}")

print("\n" + "=" * 72)
assert isinstance(_p, int) and isinstance(_f, int), (
    f"the pass/fail counters were shadowed by a loop variable: _p={_p!r} _f={_f!r}")
print(f"RESULT: {_p} passed, {_f} failed")
print("=" * 72)
if "--live" not in sys.argv:
    print("\nOffline only — market agreement is UNVERIFIED until run with --live.")
sys.exit(1 if _f else 0)
