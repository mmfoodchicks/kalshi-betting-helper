"""Walk-forward backtest for the UFC model — earn the model's weight from
history instead of waiting months for live results to accrue.

The idea: replay past cards as if they were upcoming. For every historical bout
we rebuild BOTH fighters' ratings as of the day before the fight (ufc_data's
`as_of`, which uses only bouts that had already happened), run the same win
model the live board uses, and compare the prediction to what actually happened
— and to what the market thought, using ESPN's closing moneyline.

Why point-in-time matters: rating a fighter with his full career would include
the very fight being predicted, plus everything after it. A backtest with that
leak looks brilliant and means nothing. Every rating here is built from a strict
date cutoff, so the model only ever sees what it could have known.

What it produces:
  * accuracy and calibration (Brier / log-loss) of the model's raw probability
  * the same for the market's de-vigged closing price, as the benchmark
  * the blend weight w in  w*model + (1-w)*market  that minimises log-loss —
    i.e. how much the model has actually EARNED against a sharp price
  * graded (prob, outcome) pairs, which feed calibrate.py exactly like live
    results do

This is a batch job: each bout needs two point-in-time ratings and each rating
is many ESPN calls, so it caches hard and is meant to run in the background over
a bounded sample rather than on request.

Measured so far (walk-forward, market = the de-vigged ESPN close):
  * the box-score model, 2025 (59 bouts): logloss 0.685 vs market 0.615 ->
    fitted blend weight 0.05 (persisted via model_trust).
  * an Elo over the FULL 2019-2025 results graph (K fit on 2024 only, then
    385 test bouts in 2025, both fighters with 2+ prior bouts): logloss
    0.665, accuracy 61.3% -- vs the market's 0.587 / 69.4% on the same
    bouts. Best blend weight 0.00. Measured 2026-08-29. A rating model
    does not rescue this: MMA closing lines are simply sharp, and the
    honest posture is market-first with the model as a small, earned voice.
"""

import concurrent.futures as _cf
import math
import re

import racing
import ufc_data

CORE = "http://sports.core.api.espn.com/v2/sports/mma/leagues/ufc"


def _american_to_prob(ml):
    """American moneyline -> implied probability (with vig)."""
    try:
        ml = float(ml)
    except (TypeError, ValueError):
        return None
    if ml == 0:
        return None
    return (-ml) / ((-ml) + 100.0) if ml < 0 else 100.0 / (ml + 100.0)


def _athlete_id(ref):
    m = re.search(r"/athletes/(\d+)", ref or "")
    return m.group(1) if m else None


def bouts(year):
    """[{date, a_id, b_id, a_name, b_name, winner_id, mkt_a, mkt_b}] for a season.
    mkt_* are de-vigged closing probabilities; None when ESPN has no odds."""
    def build():
        try:
            d = racing._get_json(f"{CORE}/events?limit=1000&dates={year}")
        except Exception:
            return None
        refs = [i["$ref"] for i in (d.get("items") or []) if i.get("$ref")]

        def one_event(ref):
            out = []
            try:
                ev = racing._get_json(ref)
            except Exception:
                return out
            for c in ev.get("competitions") or []:
                date = (c.get("date") or "")[:10]
                cs = c.get("competitors") or []
                if len(cs) != 2 or not date:
                    continue
                ids, names, win = [], [], None
                for x in cs:
                    aid = _athlete_id((x.get("athlete") or {}).get("$ref"))
                    ids.append(aid)
                    names.append(None)
                    if x.get("winner"):
                        win = aid
                if not all(ids) or not win:
                    continue
                mkt = {}
                try:
                    o = racing._get_json(c["$ref"].split("?")[0] + "/odds")
                    items = o.get("items") or []
                    if items:
                        rec = racing._get_json(items[0]["$ref"])
                        for side in ("homeAthleteOdds", "awayAthleteOdds"):
                            s = rec.get(side) or {}
                            aid = _athlete_id((s.get("athlete") or {}).get("$ref"))
                            p = _american_to_prob(s.get("moneyLine"))
                            if aid and p:
                                mkt[aid] = p
                except Exception:
                    pass
                ma, mb = mkt.get(ids[0]), mkt.get(ids[1])
                if ma and mb and (ma + mb) > 0:       # de-vig
                    ma, mb = ma / (ma + mb), mb / (ma + mb)
                else:
                    ma = mb = None
                out.append({"date": date, "a_id": ids[0], "b_id": ids[1],
                            "winner_id": win, "mkt_a": ma, "mkt_b": mb})
            return out

        rows = []
        with _cf.ThreadPoolExecutor(max_workers=6) as ex:
            for got in ex.map(one_event, refs):
                rows.extend(got)
        rows.sort(key=lambda r: r["date"])
        return rows
    return racing._cached(("ufc_bt_bouts", year), 30 * 86400, build) or []


def predict(bout):
    """Point-in-time model probability that fighter A wins, or None."""
    import ufc_sim
    as_of = bout["date"]                     # strictly-before filter inside
    ra = ufc_data.fighter_rating(bout["a_id"], as_of=as_of)
    rb = ufc_data.fighter_rating(bout["b_id"], as_of=as_of)
    if not ra or not rb:
        return None
    # Skip bouts where neither fighter has usable history at that date — the
    # model has nothing to say and would just echo its prior.
    if (ra.get("fights", 0) + rb.get("fights", 0)) < 2:
        return None
    try:
        return float(ufc_sim.win_prob(ra, rb))
    except Exception:
        return None


def _metrics(pairs):
    """(n, accuracy, brier, logloss) for [(prob_of_A, A_won)]."""
    n = len(pairs)
    if not n:
        return 0, None, None, None
    acc = sum(1 for p, y in pairs if (p >= 0.5) == bool(y)) / n
    brier = sum((p - y) ** 2 for p, y in pairs) / n
    ll = -sum(math.log(max(1e-9, p if y else 1 - p)) for p, y in pairs) / n
    return n, round(acc, 4), round(brier, 4), round(ll, 4)


def run(years=(2025,), limit=None, with_market_only=True):
    """Replay `years` and score the model against reality and the market."""
    rows = []
    for y in years:
        rows.extend(bouts(y))
    if with_market_only:
        rows = [r for r in rows if r["mkt_a"] is not None]
    if limit:
        rows = rows[:limit]
    model, market, both = [], [], []
    for r in rows:
        p = predict(r)
        if p is None:
            continue
        y = 1 if r["winner_id"] == r["a_id"] else 0
        model.append((p, y))
        if r["mkt_a"] is not None:
            market.append((r["mkt_a"], y))
            both.append((p, r["mkt_a"], y))
    out = {"bouts_scored": len(model),
           "model": dict(zip(("n", "acc", "brier", "logloss"), _metrics(model))),
           "market": dict(zip(("n", "acc", "brier", "logloss"), _metrics(market)))}
    if both:
        best_w, best_ll = 0.0, None
        for i in range(21):
            w = i / 20.0
            blend = [(max(1e-6, min(1 - 1e-6, w * m + (1 - w) * k)), y) for m, k, y in both]
            _n, _a, _b, ll = _metrics(blend)
            if best_ll is None or ll < best_ll:
                best_w, best_ll = w, ll
        out["best_blend_weight"] = best_w
        out["best_blend_logloss"] = best_ll
        out["verdict"] = (
            "model adds nothing over the market" if best_w <= 0.05 else
            "model adds a little" if best_w <= 0.35 else
            "model carries real independent signal")
    return out


def graded_pairs(years=(2025,), limit=None):
    """[(prob, outcome)] for calibrate.py — the same shape live grading produces,
    so a backtest can seed the calibration instead of waiting for settled markets."""
    out = []
    for y in years:
        for r in (bouts(y)[:limit] if limit else bouts(y)):
            p = predict(r)
            if p is None:
                continue
            out.append((p, 1 if r["winner_id"] == r["a_id"] else 0))
    return out
