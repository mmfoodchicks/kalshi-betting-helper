"""Measured model-vs-market trust, learned from backtests — the self-building
version of a hand-set constant.

Every board that departs from a market price is implicitly claiming "our model
knows better than this price." That claim should be measured, not assumed. The
backtests (ufc_backtest, team_backtest, racing_backtest) each fit the blend
weight w in

    w * model + (1 - w) * market

that minimises log-loss over point-in-time history. This module persists those
fitted weights and serves them to the boards, so the amount a sport is allowed
to disagree with the price is whatever the evidence supports.

An important subtlety: the right input is the FITTED WEIGHT, not the sample
count. It's tempting to say "more graded history -> trust the model more," but
that gets the direction wrong — UFC has plenty of history and the fit says 0.05,
because the model is worse than the close. Sample size only controls CONFIDENCE
in the fitted weight, so a thin backtest is pulled toward the cautious default
rather than taken at face value.

Sports with no measurement fall back to `_DEFAULT`, which is deliberately
market-leaning: an unvalidated model should not be fading a liquid price.
"""

import time

import deep_cache
import errlog

_KEY = "model_trust"
# The docstring above says the no-measurement default is "deliberately
# market-leaning" -- and then this constant sat at 0.50, which is not leaning
# anywhere. The mismatch had a real cost: UFC's backtest FIT 0.05 on 59 graded
# bouts (the model demonstrably loses to the close, logloss 0.685 vs 0.615),
# and the shrink toward 0.50 served an effective weight of 0.37 -- the less
# evidence, the harder we faded a market that beats us. The prior for an
# unproven model against real-money prices belongs near the market, and the
# asymmetry seals it: overweighting a losing model realises losses, while
# underweighting a winning one only forgoes edge until the sample grows.
_DEFAULT = 0.20
_PRIOR_N = 150           # backtest samples needed before a fit is taken at face value
_FLOOR, _CEIL = 0.05, 0.85


def _blank():
    return {"weights": {}, "updated": 0.0}


def load():
    payload, _ts = deep_cache.load(_KEY)
    return payload if isinstance(payload, dict) and "weights" in payload else _blank()


def weight(sport, default=None):
    """The measured model weight for a sport, shrunk toward the cautious default
    by how much evidence backs it. Falls back to `default`/_DEFAULT when we've
    never measured that sport."""
    rec = (load().get("weights") or {}).get(sport)
    base = _DEFAULT if default is None else default
    if not rec:
        return base
    w = rec.get("weight")
    n = rec.get("n") or 0
    if w is None:
        return base
    # Shrink the fit toward the default by sample size: a 60-bout read is a
    # direction, not a number.
    conf = n / float(n + _PRIOR_N)
    out = conf * float(w) + (1 - conf) * base
    return round(max(_FLOOR, min(_CEIL, out)), 3)


def record(sport, fitted_weight, n, source, extra=None):
    """Persist one backtest's verdict for a sport."""
    cur = load()
    cur["weights"][sport] = {"weight": fitted_weight, "n": n, "source": source,
                             "at": time.time(), **(extra or {})}
    cur["updated"] = time.time()
    deep_cache.save(_KEY, cur)
    return cur["weights"][sport]


def report():
    """Everything measured so far, for the UI / an audit."""
    rec = load()
    out = {}
    for sport, r in (rec.get("weights") or {}).items():
        out[sport] = {**r, "effective_weight": weight(sport)}
    return {"weights": out, "updated": rec.get("updated"),
            "default": _DEFAULT,
            "note": "weight = how much the board may follow OUR model instead of "
                    "the market price; fitted on point-in-time backtests and "
                    "shrunk toward the default until the sample earns it."}


# ---- The nightly refresh ----------------------------------------------------
# Every sport team_backtest can score, plus UFC. NFL and WNBA were missing from
# this tuple, so they were never measured and silently kept the cautious default
# forever -- which is how a preseason NFL model came to print double-digit edges
# on a Super Bowl market. A sport that can be measured belongs here.
def refresh(sports=("ufc", "nhl", "nba", "mlb", "nfl"), quick=True):
    """Re-run the backtests and store each fitted weight. Designed for the nightly
    scheduler: every sport it can measure, it measures. Best-effort per sport, so
    one bad feed can't stop the rest."""
    done = {}
    if "ufc" in sports:
        try:
            import ufc_backtest
            r = ufc_backtest.run(years=(2025,), limit=(60 if quick else 300))
            if r.get("best_blend_weight") is not None:
                done["ufc"] = record("ufc", r["best_blend_weight"],
                                     r.get("bouts_scored", 0), "ufc_backtest",
                                     {"model_logloss": (r.get("model") or {}).get("logloss"),
                                      "market_logloss": (r.get("market") or {}).get("logloss")})
        except Exception as _e:
            errlog.note("MT-refresh", _e)
    import team_backtest
    for lg in [s for s in sports if s in team_backtest.LEAGUES]:
        try:
            r = team_backtest.run(lg, 2025, eval_n=(120 if quick else 300))
            if r.get("best_blend_weight") is not None:
                done[lg] = record(lg, r["best_blend_weight"], r.get("games_scored", 0),
                                  "team_backtest",
                                  {"model_logloss": (r.get("model") or {}).get("logloss"),
                                   "market_logloss": (r.get("market") or {}).get("logloss")})
        except Exception:
            continue
    return done
