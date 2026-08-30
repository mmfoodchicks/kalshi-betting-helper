"""Slip-level grading: the correlation claim, finally scored.

Every per-leg number in this app is now measured against a graded record --
and when measured, most shrank (the blend fitter flattened our per-leg
disagreements to 2% weight). But a slip's EV doesn't come from the legs: it
comes from the JOINT probability, which the sim lifts above the independent
product via same-game correlation, and until this module nothing on the site
ever checked whether slips claiming 10% actually cash 10% of the time.

So: every parlay the maker builds is logged at first sight (pre-game, all
legs ticketed, first-write-wins on the leg set), then graded as a unit off
Kalshi settlement -- won only if every leg settled the bought way. The report
in store.slip_report compares three numbers over the same slips: expected
wins under the claimed joints, expected wins treating the legs as
independent, and actual wins. Where actual lands is the verdict on the
correlation premium.
"""

import hashlib
import json
import time

import errlog
import store


def log_from_item(item, sport="mlb", date=None, tag=None):
    """Record one built slip, or None when it isn't cleanly gradable.

    All-or-nothing on purpose: a slip with one unticketed or live leg can't be
    settled as the unit whose joint probability was claimed, and logging the
    gradable subset would grade a DIFFERENT slip than the one shown."""
    if not item or not item.get("groups"):
        return None
    legs, disp = [], []
    for grp in item["groups"]:
        if "🔴" in (grp.get("matchup") or ""):
            return None                  # a live joint is a different quantity
        for l in grp.get("legs") or []:
            if l.get("live"):
                return None
            tk = l.get("ticker")
            if not tk:
                return None
            legs.append({"tk": tk,
                         "no": 1 if l.get("side") == "no" else 0,
                         "close": l.get("close_time")})
            # Display text rides along: `legs` above is tickers (enough to
            # GRADE, useless to SHOW), and a winning slip on the wall should
            # read like the slip did the day it was built.
            disp.append({"pick": l.get("pick") or tk,
                         "side": l.get("side", "yes"),
                         "matchup": grp.get("matchup"),
                         "cents": l.get("market_cents")})
    if len(legs) < 2:
        return None
    prob = (item.get("combined_prob_pct") or 0) / 100.0
    indep = (item.get("indep_prob_pct") or 0) / 100.0
    if not (0.0 < prob < 1.0):
        return None
    key = hashlib.sha1("|".join(
        sorted(f"{l['tk']}:{l['no']}" for l in legs)).encode()).hexdigest()
    closes = [l["close"] for l in legs if l.get("close")]
    store.log_slip(sport, date, key, len(legs), item.get("n_games"),
                   prob, indep if 0 < indep < 1 else None,
                   item.get("kalshi_payout_net_x"), item.get("ev_pct"),
                   item.get("objective"), json.dumps(legs),
                   max(closes) if closes else None, tag=tag,
                   legs_disp=json.dumps(disp))
    return key


# A leg is probed no earlier than build time + this: rows are logged pre-game
# on slate day, so log + a full game's length means the games have been played.
# Same reasoning as predlog's early probe, without its close-time backstop
# complications -- an unsettled market just reports itself open and is retried.
_PLAYED_S = 6 * 3600


def grade_due(limit=40):
    """Grade slips whose games should be over, off Kalshi settlement.

    A slip grades only when EVERY leg has a decided result -- one decided loss
    does settle the outcome early, but waiting for the full set keeps legs_hit
    exact and costs only hours. Any leg that settles void, or whose market
    Kalshi no longer serves, voids the whole slip (graded=2): a voided leg
    changes the claimed joint, so the slip that remains is not the slip that
    was logged."""
    import urllib.error
    import kalshi
    rows = store.ungraded_slips(int(time.time()) - _PLAYED_S, limit=limit)
    if not rows:
        return 0
    memo = {}                            # ticker -> ("yes"/"no"/"void"/"open")

    def _result(tk):
        if tk in memo:
            return memo[tk]
        try:
            m = kalshi.get_market(tk)
        except urllib.error.HTTPError as e:
            memo[tk] = "void" if getattr(e, "code", None) == 404 else "open"
            return memo[tk]
        except Exception:
            memo[tk] = "open"            # transient -> retry next pass
            return memo[tk]
        res = (m.get("result") or "").lower()
        if res in ("yes", "no"):
            memo[tk] = res
        elif (m.get("status") or "").lower() in ("finalized", "settled",
                                                 "determined"):
            memo[tk] = "void"            # settled with no side = scratched
        else:
            memo[tk] = "open"
        return memo[tk]

    graded = 0
    for r in rows:
        try:
            legs = json.loads(r["legs"] or "[]")
        except ValueError as e:
            errlog.note("SLIP-legs-json", e)
            store.set_slip_grade(r["id"], 2)
            continue
        results = [(_result(l["tk"]), l.get("no")) for l in legs]
        if any(res == "void" for res, _n in results):
            store.set_slip_grade(r["id"], 2)
            graded += 1
            continue
        if any(res == "open" for res, _n in results):
            continue                     # still settling -> next pass
        hits = sum(1 for res, no in results
                   if res == ("no" if no else "yes"))
        store.set_slip_grade(r["id"], 1, won=1 if hits == len(results) else 0,
                             legs_hit=hits)
        graded += 1
    return graded
