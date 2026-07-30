"""Does recent form / slump predict tennis results BEYOND the Elo rating?

The answer, measured, is NO -- and this file exists so that conclusion is
reproducible rather than folklore, because "fade the player in a slump" is an
intuition strong enough that someone will try to wire it in again.

Method: a point-in-time walk over the accumulated settled-match store (the same
store tennis_elo builds its ratings from -- ~19k matches, ATP/WTA/ITF). For every
match we use only what had already been played: each player's Elo and their
recent-form residual. Then we ask whether adding a form term beats plain Elo on a
HELD-OUT tail, having chosen the coefficient on the earlier portion.

Elo already absorbs these same results, so this is specifically a test of what Elo
UNDER-reacts to: K=24 moves a rating slowly, so a genuine level change (injury,
confidence collapse, a step up in class) should show as a run of results the
rating has not caught up to.

What the numbers say:
  * A linear form term:      no out-of-sample gain (best coefficient on the train
                             half makes the test half WORSE).
  * A streak term:           no gain, and the sign flips between halves -- noise.
  * Extremes-only threshold: +0.0004 logloss, i.e. nothing.
  * The in-sample bucket tables DO look convincing (players on +3/+4 win streaks
    underperform their rating by 5-6.5pp, n=425/232, beyond 2 standard errors).
    That effect is draw depth and rating immaturity, not momentum: it appears only
    mid-tournament, where a win streak means you have advanced to a harder
    opponent whose own thin rating understates them. At the START of an event the
    streak buckets are flat.
  * The regime is thin: only ~10% of stored matches have both players at 8+ prior
    matches and 1.5% of players ever reach 20. There is no stable baseline for a
    player to be "slumping" relative to.

Run: python3 tests/tennis_form_check.py
"""
import collections
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import deep_cache
import tennis_elo

K = tennis_elo._K
MIN_N = 8            # prior matches before a player is rateable
WIN = 10             # form window (matches)
HALFLIFE = 5.0


def load():
    store = deep_cache.load("tennis_elo_results")[0] or {}
    rows = []
    for date, win, los, g, tier in store.values():
        if date and win and los and win != los:
            rows.append((date, win, los, g, tier))
    rows.sort(key=lambda r: r[0])
    return rows


def elo_p(ra, rb):
    return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))


def form_delta(hist):
    if not hist:
        return 0.0, 0
    num = den = 0.0
    for i, (won, exp) in enumerate(reversed(hist[-WIN:])):
        w = 0.5 ** (i / HALFLIFE)
        num += w * (won - exp)
        den += w
    return (num / den if den else 0.0), len(hist[-WIN:])


def streak(hist):
    if not hist:
        return 0
    last = hist[-1][0]
    s = 0
    for won, _ in reversed(hist):
        if won != last:
            break
        s += 1
    return s if last else -s


def metrics(pairs):
    n = len(pairs)
    if not n:
        return {"n": 0, "logloss": 9.0, "brier": 9.0, "acc": 0.0}
    return {"n": n,
            "acc": round(sum(1 for p, y in pairs if (p >= .5) == bool(y)) / n, 4),
            "brier": round(sum((p - y) ** 2 for p, y in pairs) / n, 4),
            "logloss": round(-sum(math.log(max(1e-9, p if y else 1 - p))
                                  for p, y in pairs) / n, 4)}


def walk():
    """One point-in-time pass, emitting a record per scored match."""
    elo, n_m, hist = {}, collections.Counter(), collections.defaultdict(list)
    out = []
    for date, win, los, g, tier in load():
        for p in (win, los):
            if p not in elo:
                elo[p] = tier
        ra, rb = elo[win], elo[los]
        if n_m[win] >= MIN_N and n_m[los] >= MIN_N:
            # randomise sides so a winner-first store cannot leak
            flip = (hash(date + win) % 2 == 0)
            x, y = (win, los) if flip else (los, win)
            fx, _ = form_delta(hist[x])
            fy, _ = form_delta(hist[y])
            out.append({"p_elo": elo_p(elo[x], elo[y]), "won": 1 if x == win else 0,
                        "ex": elo[x], "ey": elo[y], "fx": fx, "fy": fy,
                        "sx": streak(hist[x]), "sy": streak(hist[y]),
                        "nx": n_m[x], "ny": n_m[y]})
        ew = elo_p(ra, rb)
        elo[win] = ra + K * (1.6 if n_m[win] < 10 else 1.0) * (1.0 - ew)
        elo[los] = rb - K * (1.6 if n_m[los] < 10 else 1.0) * (1.0 - ew)
        hist[win].append((1, ew)); hist[los].append((0, 1.0 - ew))
        n_m[win] += 1; n_m[los] += 1
    return out


def _oos(S, adjust, grid, label):
    """Fit `adjust(coef)` on the first 70%, score the last 30%. Returns the gain."""
    cut = int(len(S) * 0.7)

    def score(coef, rows):
        return metrics([(min(0.995, max(0.005, elo_p(s["ex"] + adjust(s, coef), s["ey"]))),
                         s["won"]) for s in rows])
    res = {c: (score(c, S[:cut]), score(c, S[cut:])) for c in grid}
    fit = min(res, key=lambda c: res[c][0]["logloss"])
    base = res[grid[0]][1]
    got = res[fit][1]
    gain = base["logloss"] - got["logloss"]
    print(f"\n  {label}")
    print(f"    {'coef':>8s} {'train ll':>10s} {'test ll':>10s} {'test acc':>9s}")
    for c in grid:
        tr, te = res[c]
        mark = "  <- fitted on train" if c == fit else ""
        print(f"    {c:8.2f} {tr['logloss']:10.4f} {te['logloss']:10.4f} "
              f"{te['acc']:9.4f}{mark}")
    print(f"    Elo alone test logloss {base['logloss']:.4f} -> fitted {got['logloss']:.4f}"
          f"   {'IMPROVES' if gain > 0.001 else 'NO GAIN'} ({gain:+.4f})")
    return gain


def main():
    rows = load()
    S = walk()
    print("=" * 74)
    print(f"store: {len(rows)} settled matches; {len(S)} scoreable "
          f"(both players {MIN_N}+ prior)")
    print("=" * 74)
    print(f"  Elo alone: {metrics([(s['p_elo'], s['won']) for s in S])}")

    # --- how thin is the regime? ------------------------------------------------
    cnt = collections.Counter()
    for _, w, l, _, _ in rows:
        cnt[w] += 1; cnt[l] += 1
    tot = len(cnt) or 1
    print(f"  players: {tot};  8+ matches: {sum(1 for v in cnt.values() if v >= 8)}"
          f" ({sum(1 for v in cnt.values() if v >= 8)/tot*100:.1f}%),"
          f"  20+: {sum(1 for v in cnt.values() if v >= 20)}"
          f" ({sum(1 for v in cnt.values() if v >= 20)/tot*100:.1f}%)")

    # --- the in-sample tables that look convincing -----------------------------
    print("\n" + "=" * 74)
    print("In-sample: streak vs Elo expectation (* = beyond 2 standard errors)")
    print("=" * 74)
    b = collections.defaultdict(lambda: {"n": 0, "exp": 0.0, "act": 0})
    for s in S:
        for side in (True, False):
            k = max(-4, min(5, s["sx"] if side else s["sy"]))
            d = b[k]
            d["n"] += 1
            d["exp"] += s["p_elo"] if side else 1 - s["p_elo"]
            d["act"] += s["won"] if side else 1 - s["won"]
    print(f"    {'streak':>8s} {'n':>6s} {'Elo exp':>9s} {'actual':>9s} {'resid':>9s}")
    for k in sorted(b):
        d = b[k]
        if d["n"] < 80:
            continue
        exp, act = d["exp"] / d["n"] * 100, d["act"] / d["n"] * 100
        se = math.sqrt(max(1e-9, (act / 100) * (1 - act / 100) / d["n"])) * 100
        print(f"    {k:+8d} {d['n']:6d} {exp:8.1f}% {act:8.1f}% {act-exp:+7.1f}pp"
              f"{'*' if abs(act-exp) > 2*se else ' '}")
    print("    ^ looks real, but see the module docstring: it is draw depth and")
    print("      rating immaturity, and it does not survive the tests below.")

    # --- out-of-sample: the only test that matters ------------------------------
    print("\n" + "=" * 74)
    print("OUT OF SAMPLE (coefficient chosen on the first 70%, scored on the last 30%)")
    print("=" * 74)
    gains = []
    gains.append(_oos(S, lambda s, c: c * (s["fx"] - s["fy"]),
                      [0, 20, 40, 60, 80, 100, 130, 160, 200, 250],
                      "linear form term (Elo points per unit of form gap)"))
    gains.append(_oos(S, lambda s, c: c * (s["sx"] - s["sy"]),
                      [0, -4, -8, -12, -16, -20, -25],
                      "streak term (Elo points per match of streak)"))

    def thresh(s, c):
        adj = 0.0
        for v, sign in ((s["fx"], 1), (s["fy"], -1)):
            if v <= -0.25:
                adj -= sign * c
            elif v >= 0.25:
                adj += sign * c
        return adj
    gains.append(_oos(S, thresh, [0, 20, 40, 60, 80],
                      "extremes-only form term (|form| >= 0.25)"))

    print("\n" + "=" * 74)
    best = max(gains)
    if best > 0.005:
        print(f"VERDICT: a form term DOES help out of sample (best {best:+.4f} logloss).")
        print("         Revisit tennis_elo.form -- it is currently reported, not modelled.")
    else:
        print(f"VERDICT: no form term helps out of sample (best {best:+.4f} logloss).")
        print("         tennis_elo.form stays REPORTED, not modelled. Do not wire it in")
        print("         on the strength of the in-sample table above.")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
