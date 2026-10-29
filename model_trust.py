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


def _record(sport):
    """The measured record for a sport: this machine's deep store first, then
    the shared calibration file the server writes and the PC fetches
    (calibrate.compute_params' "trust"). The PC fits its own and nothing
    shipped the server's to it, so a PC whose store lacked a sport (its first
    cycle, a failed backtest, a wiped pc-deepcache) anchored the NBA/NHL
    headlines at the default 0.20 while the server's fallback build read
    0.066 -- the board's numbers depended on who built it (Fable's build
    review, 2026-10-29). None when neither has one."""
    rec = (load().get("weights") or {}).get(sport)
    if rec:
        return rec
    try:
        import calibrate
        rec = ((calibrate.file_params() or {}).get("trust") or {}).get(sport)
    except Exception as e:
        errlog.note("TRUST-file", e)
        rec = None
    return rec if isinstance(rec, dict) else None


def weight(sport, default=None):
    """The measured model weight for a sport, shrunk toward the cautious default
    by how much evidence backs it. Falls back to `default`/_DEFAULT when we've
    never measured that sport."""
    rec = _record(sport)
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


def anchor(p_model, home_cents, away_cents, sport):
    """A board's headline on its measured weight: w x model + (1 - w) x
    Kalshi's de-vigged price, w = weight(sport). (p, w, p_market) for the HOME
    side, or None when the book is not two-sided (the model's own figure then
    stands, as before).

    The NBA and NHL boards never read their weight (2026-10-29,
    research/reports/accuracy_gaps.md 13.1, agreed with a Fable agent): the
    backtests had measured both models well behind the closing price (nba log
    loss 0.591 vs the market's 0.551 on 304 games, nhl 0.691 vs 0.664 on 310),
    and live in 2026-27 the NBA headline read a coin flip (Brier 0.2504
    against Kalshi's 0.2200 on 106 games) while its card showed model-vs-ask
    edges on every game. College and UFC already apply theirs. The weight is
    only ever moved by refresh() -- never by hand."""
    try:
        h, a = float(home_cents), float(away_cents)
        p = float(p_model)
    except (TypeError, ValueError):
        return None
    if not (0.0 < h < 100.0 and 0.0 < a < 100.0):
        return None
    pm = h / (h + a)
    w = weight(sport)
    if _record(sport) is None:
        errlog.note("TRUST-default", msg=f"{sport}: no measured weight here or in the shared file; "
                                         f"the headline anchors at the default {w}")
    return w * p + (1.0 - w) * pm, w, pm


def rung(p_model, w, yes_cents, no_cents=None):
    """A spread or total rung on the same weight as its game's moneyline:
    w x model + (1 - w) x the rung's de-vigged YES price, yes / (yes + no) off
    its two asks (the moneyline's convention), in percent. A one-sided rung
    falls back to its YES ask, which is the more generous of the two to the
    model's edge by about half the spread -- and w = 1 (no anchor) is the
    model alone, as before."""
    fair = None
    try:
        y, nn = float(yes_cents), float(no_cents)
        if 0.0 < y < 100.0 and 0.0 < nn < 100.0:
            fair = 100.0 * y / (y + nn)
    except (TypeError, ValueError):
        fair = None
    if fair is None:
        fair = float(yes_cents)
    return w * float(p_model) * 100.0 + (1.0 - w) * fair


def record(sport, fitted_weight, n, source, extra=None):
    """Persist one backtest's verdict for a sport.

    A bigger RECENT sample outranks a smaller one: the PC runs the full
    backtest overnight (n≈300) and ships it through the deep sync, while the
    server's own nightly pass stays quick (n≈60) — and since weight() reads n
    as confidence, letting the quick pass clobber the full one would
    literally erase what we know. A stale full run (>48h) yields, so a dead
    PC can't pin outdated weights forever."""
    cur = load()
    old = (cur.get("weights") or {}).get(sport)
    if (old and (old.get("n") or 0) > (n or 0)
            and time.time() - (old.get("at") or 0) < 48 * 3600):
        return old
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
