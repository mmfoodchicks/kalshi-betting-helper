"""Fit and re-check the tennis Elo's hyperparameters, and the surface question.

This file backs three constants/decisions in tennis_elo and tennis_prices. Re-run
it as results accumulate -- the Kalshi store grows daily, and the right K can move
as the pool deepens.

  1. K FACTOR -- now a RAMP BY EXPERIENCE, not a constant
     (K_EARLY=100, K_LATE=22, K_TAU=10 in tennis_elo).
     A constant cannot serve this pool because the pool is a mixture: since the
     deep archive was wired in, ATP players carry hundreds of rated matches and
     want K~24, while the ITF players who make up most of a board carry a handful
     and want K~48. Fitted jointly over ~55k deep ATP matches and ~19k Kalshi
     results, the ramp matches the deep pool's best constant (0.6099 vs 0.6095)
     and beats the shallow pool's (0.6540 vs 0.6572).

  2. SURFACE-AWARE RATING -- SHIPPED, but as a DEVIATION, not a parallel chain
     (tennis_elo.K_DEV = 16, K_SURFACE = 50).
     Rejected twice on thinner data, accepted as a chain on the third look, and
     then caught: once the archive reached 436k matches the chain was measurably
     WORSE THAN POOLING on every rolling-origin fold, on tour and ITF alike. It
     seeded each surface from the player's overall rating and then hit it with a
     debut-sized K, and it went stale once the chains forked.
     Modelling the surface as a recentred deviation from the live overall rating
     fixes both: +0.0022 vs pooling and +0.0044 vs the chain, 5 of 5 folds
     positive on each, and positive on tour (+0.0025) and ITF (+0.0019)
     separately. The ratings stay recognisable -- Nadal well up on clay, Musetti
     down on hard, Djokovic nearly flat.
     The lesson worth keeping: a new model beating the null is not enough when
     the thing it replaces was never checked against the null itself.

  3. TIME DECAY / PROVISIONAL BOOST -- LEFT ALONE.
     Regressing an idle player's rating toward the pool mean hurt at every
     half-life from 90 to 720 days on both datasets. Refitting the 1.6x/10-match
     provisional boost to 2.0/20 also made the held-out tail worse.

Datasets, both walked point-in-time (a prediction only ever sees earlier matches):
  KALSHI    settled Kalshi results, ATP/WTA/ITF -- the population the live board
            actually runs on, but shallow (median ~4 matches per player).
  CHARTING  the Match Charting Project match index -- tour level, decades deep,
            and the only reachable results source carrying a surface label.

Run: python3 tests/tennis_elo_fit.py          (add --quick to skip the surface part)
"""
import collections
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import deep_cache
import tennis_data as td
import tennis_elo

MIN_N = 8
K_GRID = [16.0, 20.0, 24.0, 28.0, 32.0, 36.0, 40.0, 44.0, 48.0, 56.0, 64.0]
OLD_K = 24.0
SHIPPED_K = tennis_elo._K


def load_kalshi():
    # Rows gained a 6th element (tournament) when the surface Elo landed; older
    # rows are still 5 long, so slice rather than unpack.
    store = deep_cache.load("tennis_elo_results")[0] or {}
    rows = []
    for rec in store.values():
        d, w, l, g, t = rec[:5]
        if d and w and l and w != l:
            rows.append((d.replace("-", ""), g, w, l, t))
    rows.sort(key=lambda r: r[0])
    return rows


def load_charting(with_surface=False):
    rows = []
    for tour in ("m", "w"):
        for r in td._rows(td._fetch_csv(f"charting-{tour}-matches.csv")):
            d = (r.get("Date") or "").strip()
            if len(d) != 8 or not d.isdigit():
                continue
            p1, p2 = td._norm(r.get("Player 1") or ""), td._norm(r.get("Player 2") or "")
            if not p1 or not p2 or p1 == p2:
                continue
            # the charting index lists the WINNER first
            if with_surface:
                rows.append((d, tour, p1, p2, td._surface_of(r.get("Surface"))))
            else:
                rows.append((d, tour, p1, p2, 1500.0))
    rows.sort(key=lambda r: r[0])
    return rows


def elo_p(ra, rb):
    return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))


def ll(pairs):
    if not pairs:
        return None
    return -sum(math.log(max(1e-9, p if y else 1 - p)) for p, y in pairs) / len(pairs)


def acc(pairs):
    return sum(1 for p, y in pairs if (p >= .5) == bool(y)) / len(pairs) if pairs else None


def walk(rows, K):
    """K may be a number (constant, with the old 1.6x step) or the string
    "ramp", which uses the schedule tennis_elo actually ships."""
    ramp = (K == "ramp")
    elo, n_m, out = {}, collections.Counter(), []
    for date, pool, win, los, tier in rows:
        for p in (win, los):
            if p not in elo:
                elo[p] = tier
        ra, rb = elo[win], elo[los]
        if n_m[win] >= MIN_N and n_m[los] >= MIN_N:
            flip = (hash(date + win) % 2 == 0)     # store lists winner first
            x, y = (win, los) if flip else (los, win)
            out.append((min(0.995, max(0.005, elo_p(elo[x], elo[y]))),
                        1 if x == win else 0))
        ew = elo_p(ra, rb)
        if ramp:
            kw, kl = tennis_elo.k_for(n_m[win]), tennis_elo.k_for(n_m[los])
        else:
            kw = K * (1.6 if n_m[win] < 10 else 1.0)
            kl = K * (1.6 if n_m[los] < 10 else 1.0)
        elo[win] = ra + kw * (1.0 - ew)
        elo[los] = rb - kl * (1.0 - ew)
        n_m[win] += 1; n_m[los] += 1
    return out


def load_deep():
    """The deep ATP archive the ramp was fitted on (empty if unreachable)."""
    try:
        import tennis_history
        return [(d, "m", w, l, 1500.0)
                for d, tour, w, l, s, lv in tennis_history.results()]
    except Exception:
        return []


def load_deep_surface():
    """Same archive, keeping the surface label and the tier, for the surface
    check. Tier matters because tour and ITF are different populations and the
    surface question has to be answered on each rather than on their average."""
    try:
        import tennis_history
        return [(d, "m", w, l, s, not tennis_elo._is_low_tier(lv))
                for d, tour, w, l, s, lv in tennis_history.results() if s]
    except Exception:
        return []


def rolling(name, rows, folds=5):
    """Choose K on everything seen so far, score the NEXT block, walk forward.
    The shipped model is the RAMP, so it is scored alongside the constants rather
    than being looked up as one of them."""
    curves = {K: walk(rows, K) for K in K_GRID}
    curves["ramp"] = walk(rows, "ramp")
    n = len(curves[OLD_K])
    edges = [int(n * i / folds) for i in range(folds + 1)]
    print("\n" + "=" * 78)
    print(f"{name}  ({len(rows)} matches, {n} scoreable)")
    print("=" * 78)
    print(f"  {'fold':>5s} {'block':>7s} {'K chosen':>9s} {'fitted ll':>10s} "
          f"{'K=24 ll':>9s} {'gain':>8s}")
    gains, chosen = [], []
    for i in range(1, folds):
        lo, hi = edges[i], edges[i + 1]
        best = min(K_GRID, key=lambda K: ll(curves[K][:lo]))
        g = ll(curves[OLD_K][lo:hi]) - ll(curves[best][lo:hi])
        gains.append(g); chosen.append(best)
        print(f"  {i:>5d} {hi-lo:>7d} {best:>9.0f} {ll(curves[best][lo:hi]):>10.4f} "
              f"{ll(curves[OLD_K][lo:hi]):>9.4f} {g:>+8.4f}")
    print(f"  folds chose {[int(c) for c in chosen]};  "
          f"mean gain over K=24: {sum(gains)/len(gains):+.4f}")
    print(f"\n  {'K':>6s} {'logloss':>9s} {'acc':>7s}")
    lo_k = min(K_GRID, key=lambda k: ll(curves[k]))
    for K in K_GRID:
        tag = "  <-- best constant" if K == lo_k else ""
        print(f"  {K:>6.0f} {ll(curves[K]):>9.4f} {acc(curves[K]):>7.4f}{tag}")
    print(f"  {'ramp':>6s} {ll(curves['ramp']):>9.4f} {acc(curves['ramp']):>7.4f}"
          f"  <-- SHIPPED")
    return lo_k, ll(curves[OLD_K]), ll(curves["ramp"])


def surface_check():
    """Is a surface-aware rating better than pooling -- and which estimator?

    Three models, all walked point-in-time:
      POOLED  one rating per player, no surface at all. The null.
      CHAIN   the retired estimator: a parallel Elo per surface, seeded from the
              player's overall rating, shrunk back toward it by K_SURFACE.
      DEV     what ships now: overall + a recentred surface DEVIATION, shrunk by
              how much evidence that surface carries.

    CHAIN is kept in the comparison deliberately. It was shipped for a while and
    it is WORSE THAN NOT SPLITTING AT ALL, which is the kind of result that is
    easy to miss when a new model is only ever compared to the null. Two reasons:
    it hit a freshly seeded surface rating with a debut-sized K and destroyed the
    seed, and it went stale, since a player's improvement reached the overall
    rating but not their clay rating.

    Runs on the DEEP archive when reachable -- that is what the shipped constants
    were fitted on. Falls back to the charting archive, which is tour-only and
    far thinner, where the honest answer is "not established"."""
    deep = load_deep_surface()
    rows = deep if deep else [(d, t, a, b, s, True)
                              for d, t, a, b, s in load_charting(with_surface=True)]
    print("\n" + ("(deep archive)" if deep else "(charting archive -- thinner; "
                                               "the shipped values were fitted on the deep one)"))
    print("\n" + "=" * 78)
    print(f"SURFACE-AWARE RATING  ({len(rows)} matches with a surface)")
    print("=" * 78)
    cells = collections.Counter()
    tiers = collections.Counter()
    for _, tour, p1, p2, s, is_tour in rows:
        cells[(tour, p1, s)] += 1; cells[(tour, p2, s)] += 1
        tiers["tour" if is_tour else "ITF/chall"] += 1
    thin = sum(1 for v in cells.values() if v < 5)
    print(f"  player-surface cells: {len(cells)};  under 5 matches: {thin} "
          f"({thin/len(cells)*100:.0f}%)")
    print(f"  tier mix: {dict(tiers)}")

    def w(mode, p1=None, p2=None):
        """mode: 'pooled' | 'chain' (p1=K_SURFACE) | 'dev' (p1=K_DEV, p2=K_SHRINK).
        Emits (prob, outcome, is_tour) so the tail can be split by tier."""
        ov = collections.defaultdict(lambda: 1500.0)
        sf, dev = {}, collections.defaultdict(float)
        n_ov, n_sf = collections.Counter(), collections.Counter()
        surfs, out = collections.defaultdict(set), []

        def recentre(k):
            ss = surfs[k]
            tot = sum(n_sf[k + (s,)] for s in ss)
            if tot <= 0:
                return
            m = sum(n_sf[k + (s,)] * dev[k + (s,)] for s in ss) / tot
            for s in ss:
                dev[k + (s,)] -= m

        for date, tour, win, los, surf, is_tour in rows:
            kw, kl = (tour, win), (tour, los)
            ksw, ksl = kw + (surf,), kl + (surf,)
            if mode == "chain":
                for k, ks in ((kw, ksw), (kl, ksl)):
                    if ks not in sf:
                        sf[ks] = ov[k]

            def rate(k, ks):
                if mode == "pooled":
                    return ov[k]
                nn = n_sf[ks]
                if mode == "chain":
                    return (nn * sf[ks] + p1 * ov[k]) / (nn + p1)
                return ov[k] + dev[ks] * nn / (nn + p2)

            if n_ov[kw] >= MIN_N and n_ov[kl] >= MIN_N:
                flip = (hash(date + win) % 2 == 0)
                (x, kx, ksx), (y, ky, ksy) = (((win, kw, ksw), (los, kl, ksl)) if flip
                                              else ((los, kl, ksl), (win, kw, ksw)))
                out.append((min(0.995, max(0.005, elo_p(rate(kx, ksx), rate(ky, ksy)))),
                            1 if x == win else 0, is_tour))
            # Mirror PRODUCTION exactly, including the experience ramp on the
            # overall chain. Using a flat K here once made the test recommend
            # K_surf 120-200 while the shipped 50 was fitted under the ramp --
            # the test was measuring a model that does not exist.
            ew = elo_p(ov[kw], ov[kl])
            eb = elo_p(rate(kw, ksw), rate(kl, ksl))
            ov[kw] += tennis_elo.k_for(n_ov[kw]) * (1.0 - ew)
            ov[kl] -= tennis_elo.k_for(n_ov[kl]) * (1.0 - ew)
            if mode == "chain":
                ews = elo_p(sf[ksw], sf[ksl])
                sf[ksw] += tennis_elo.k_for(n_sf[ksw]) * (1.0 - ews)
                sf[ksl] -= tennis_elo.k_for(n_sf[ksl]) * (1.0 - ews)
            elif mode == "dev":
                dev[ksw] += p1 * (1.0 - eb)
                dev[ksl] -= p1 * (1.0 - eb)
            n_ov[kw] += 1; n_ov[kl] += 1; n_sf[ksw] += 1; n_sf[ksl] += 1
            if mode == "dev":
                surfs[kw].add(surf); surfs[kl].add(surf)
                recentre(kw); recentre(kl)
        return out

    def L(rows_):
        return ll([(p, y) for p, y, _ in rows_])

    GRID = [(kd, ks) for kd in (8, 16, 32) for ks in (20, 50, 100)]
    curves = {("dev",) + g: w("dev", *g) for g in GRID}
    curves[("pooled",)] = w("pooled")
    curves[("chain",)] = w("chain", tennis_elo.K_SURFACE)

    print(f"\n  {'model':>16s} {'logloss':>9s} {'acc':>7s}   (pooled is the null)")
    print(f"  {'pooled':>16s} {L(curves[('pooled',)]):>9.4f} "
          f"{acc([(p, y) for p, y, _ in curves[('pooled',)]]):>7.4f}")
    print(f"  {'chain (retired)':>16s} {L(curves[('chain',)]):>9.4f} "
          f"{acc([(p, y) for p, y, _ in curves[('chain',)]]):>7.4f}")
    for g in GRID:
        c = curves[("dev",) + g]
        print(f"  {f'dev {g[0]}/{g[1]}':>16s} {L(c):>9.4f} "
              f"{acc([(p, y) for p, y, _ in c]):>7.4f}")

    # The in-sample curve is NOT the verdict -- decide it the way K itself was
    # decided, by rolling origin.
    n = len(curves[("pooled",)])
    folds = 6
    edges = [int(n * i / folds) for i in range(folds + 1)]
    print("\n  rolling origin -- choose the constants on the past, score the next block")
    print(f"  {'fold':>5s} {'block':>7s} {'chosen':>10s} {'dev ll':>9s} "
          f"{'pooled':>9s} {'chain':>9s} {'v.pool':>8s} {'v.chain':>8s}")
    gains, vchain, chosen = [], [], []
    for i in range(1, folds):
        lo, hi = edges[i], edges[i + 1]
        g = min(GRID, key=lambda gg: L(curves[("dev",) + gg][:lo]))
        d = L(curves[("dev",) + g][lo:hi])
        p = L(curves[("pooled",)][lo:hi])
        c = L(curves[("chain",)][lo:hi])
        gains.append(p - d); vchain.append(c - d); chosen.append(g)
        print(f"  {i:>5d} {hi-lo:>7d} {str(g):>10s} {d:>9.4f} {p:>9.4f} {c:>9.4f} "
              f"{p-d:>+8.4f} {c-d:>+8.4f}")
    mean = sum(gains) / len(gains)
    pos = sum(1 for x in gains if x > 0)
    posc = sum(1 for x in vchain if x > 0)
    print(f"\n  chosen per fold: {chosen}")
    print(f"  mean gain vs pooled: {mean:+.4f} logloss  ({pos}/{len(gains)} folds positive)")
    print(f"  mean gain vs chain : {sum(vchain)/len(vchain):+.4f} logloss  "
          f"({posc}/{len(vchain)} folds positive)")

    # Tour and ITF are different populations; a gain that only exists on their
    # average is not a gain we can use, since most of a board is ITF.
    kd, ks = collections.Counter(chosen).most_common(1)[0][0]
    tail = slice(edges[1], None)
    tiers_ok = True
    for lbl, keep in (("tour", True), ("ITF/chall", False)):
        pr = [r for r in curves[("pooled",)][tail] if r[2] is keep]
        dr = [r for r in curves[("dev", kd, ks)][tail] if r[2] is keep]
        if pr:
            g = L(pr) - L(dr)
            tiers_ok = tiers_ok and g > 0
            print(f"  {lbl:>10s} n={len(pr):>6d}  pooled {L(pr):.4f}  "
                  f"dev {L(dr):.4f}   gain {g:+.4f}")

    # Significance on the PAIRED per-match difference, rather than a hand-picked
    # floor on the mean. The old rule was "mean > 0.002", which is arbitrary: it
    # reads a +0.0018 gain that is positive in every fold and in both tiers as a
    # failure, while a +0.0021 gain that is positive in three of five would pass.
    # Every model here scores the SAME matches, so the differences are paired and
    # a standard error is the honest test of whether the gain is real.
    pool_t, dev_t = curves[("pooled",)][tail], curves[("dev", kd, ks)][tail]
    d = [(-math.log(max(1e-9, p if y else 1 - p)))
         - (-math.log(max(1e-9, q if y else 1 - q)))
         for (p, y, _), (q, _, _) in zip(pool_t, dev_t)]
    nD = len(d)
    mu = sum(d) / nD
    var = sum((x - mu) ** 2 for x in d) / (nD - 1)
    se = math.sqrt(var / nD)
    t = mu / se if se else 0.0
    print(f"\n  paired difference (pooled - dev) over {nD} held-out matches:")
    print(f"    mean {mu:+.5f}  SE {se:.5f}  t = {t:+.1f}")

    # Three independent things must hold, and each catches a different failure:
    # the gain is real (t), it is not one lucky stretch (folds), and it is not an
    # average over one population helping while the other is hurt (tiers).
    established = t > 3.0 and pos == len(gains) and tiers_ok
    print(f"\n  -> {'SURFACE-AWARE RATING HELPS' if established else 'not established on this data'}"
          f"   (shipped K_DEV={tennis_elo.K_DEV}, K_SURFACE={tennis_elo.K_SURFACE})")
    print(f"     t>3: {t > 3.0}   every fold positive: {pos == len(gains)}   "
          f"both tiers positive: {tiers_ok}   beats retired chain: {posc}/{len(vchain)}")
    if established:
        # What matters is not whether a fold picked the shipped pair exactly, but
        # what picking it COSTS. The surface is a plateau -- K_SHRINK is flat from
        # 5 to 80 and the whole gain comes from having any deviation at all -- so
        # an exact-match count reads as total disagreement over a 0.0001 spread.
        ship = (tennis_elo.K_DEV, tennis_elo.K_SURFACE)
        exact = sum(1 for c in chosen if c == ship)
        if ("dev",) + ship in curves:
            costs = []
            for i in range(1, folds):
                lo, hi = edges[i], edges[i + 1]
                costs.append(L(curves[("dev",) + ship][lo:hi])
                             - L(curves[("dev",) + chosen[i - 1]][lo:hi]))
            worst = max(costs)
            print(f"     folds picking the shipped pair exactly: {exact}/{len(chosen)}; "
                  f"cost of shipping it anyway: {sum(costs)/len(costs):+.5f} mean, "
                  f"{worst:+.5f} worst")
            if worst > 0.001:
                print("     ^^ that is a real cost -- move K_DEV/K_SURFACE to the fold choice.")
    else:
        print("     NOTE: a surface split IS shipped but this data does not support it.")
        print("     If this is the deep archive, reconsider K_DEV / K_SURFACE.")
    return established


def _ramp_vs_constants(name, rows):
    """Does the shipped ramp beat the best constant on this dataset?"""
    if not rows:
        print(f"\n{name}: no data")
        return
    curves = {K: walk(rows, K) for K in K_GRID}
    curves["ramp"] = walk(rows, "ramp")
    print("\n" + "=" * 78)
    print(f"{name}: shipped RAMP vs the best constant  ({len(rows)} matches)")
    print("=" * 78)
    bestK = min(K_GRID, key=lambda k: ll(curves[k]))
    print(f"  best constant K={bestK:.0f}: logloss {ll(curves[bestK]):.4f} "
          f"acc {acc(curves[bestK]):.4f}")
    print(f"  shipped ramp       : logloss {ll(curves['ramp']):.4f} "
          f"acc {acc(curves['ramp']):.4f}")
    d = ll(curves[bestK]) - ll(curves["ramp"])
    print(f"  ramp vs best constant: {d:+.4f} "
          f"({'ramp wins' if d > 0 else 'within ' + format(-d, '.4f') + ' of it'})")


def main():
    quick = "--quick" in sys.argv
    k1, old1, new1 = rolling("KALSHI settled results (ITF-heavy -- the live board)",
                             load_kalshi())
    k2, old2, new2 = rolling("CHARTING match index (tour level, decades deep)",
                             load_charting())
    _ramp_vs_constants("KALSHI (shallow, ITF-heavy)", load_kalshi())
    if not quick:
        _ramp_vs_constants("DEEP ATP archive", load_deep())
    surf_rejected = True if quick else surface_check()

    print("\n" + "=" * 78)
    print("VERDICT")
    print("=" * 78)
    print(f"  shipped: ramp K_EARLY={tennis_elo.K_EARLY:.0f} K_LATE={tennis_elo.K_LATE:.0f} "
          f"K_TAU={tennis_elo.K_TAU:.0f}")
    print(f"  best CONSTANT found: KALSHI {k1:.0f}, CHARTING {k2:.0f} "
          f"(they disagree -- which is why the shipped model is a ramp)")
    print(f"  ramp vs flat K=24 -- KALSHI   {old1:.4f} -> {new1:.4f} ({old1-new1:+.4f})")
    print(f"                       CHARTING {old2:.4f} -> {new2:.4f} ({old2-new2:+.4f})")
    ok = (new1 <= old1 + 1e-4) and (new2 <= old2 + 1e-4)
    print(f"  shipped ramp beats the old flat default on both: {'YES' if ok else 'NO'}")
    if not quick:
        print(f"  surface split supported by the data: {'YES' if surf_rejected else 'NO'}")
    # Deliberately NOT a comparison of K_LATE against the best constant: those
    # should differ. K_LATE is where a well-established rating settles, while a
    # single constant is a compromise across a mixture of deep and shallow
    # players, so it always lands higher. The meaningful check is whether the ramp
    # still beats the best constant on the population the board actually runs on.
    kal = load_kalshi()
    if kal:
        cur = {K: walk(kal, K) for K in K_GRID}
        cur["ramp"] = walk(kal, "ramp")
        bc = min(K_GRID, key=lambda K: ll(cur[K]))
        margin = ll(cur[bc]) - ll(cur["ramp"])
        print(f"  ramp vs best constant on KALSHI: {margin:+.4f}")
        if margin < 0:
            print("  NOTE: a flat K now beats the ramp on the live population.")
            print("        The pool has changed shape -- re-fit K_EARLY/K_LATE/K_TAU.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
