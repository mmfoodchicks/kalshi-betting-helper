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

  2. SURFACE-SPLIT RATING -- NOW SHIPPED (tennis_elo.K_SURFACE = 50).
     Rejected twice on thinner data and accepted on the third look, which is the
     point of keeping the check rather than the conclusion. On the 11.6k-match
     charting archive, 69% of player-surface cells held under 5 matches and
     rolling origin gave -0.0010. On the 55k-match deep archive that falls to 49%
     and rolling origin gives +0.0035 with 5 of 5 folds positive, every fold
     independently choosing 50. The ratings it produces are recognisable: Nadal
     +164 clay over hard, Ruud +162, Musetti -113 on hard, Djokovic nearly flat.

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
    """Same archive, keeping the surface label, for the surface check."""
    try:
        import tennis_history
        return [(d, "m", w, l, s)
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
    """Per-surface Elo shrunk toward overall by K_SURF prior matches.
    K_SURF=None is the pooled null.

    Runs on the DEEP archive when it is reachable -- that is the data the shipped
    K_SURFACE was fitted on and the only sample big enough to establish it. Falls
    back to the charting archive, where the honest answer is still "not
    established"."""
    deep = load_deep_surface()
    rows = deep if deep else load_charting(with_surface=True)
    print("\n" + ("(deep archive)" if deep else "(charting archive -- thinner; "
                                                 "the shipped value was fitted on the deep one)"))
    print("\n" + "=" * 78)
    print(f"SURFACE-SPLIT RATING  ({len(rows)} charted matches with a surface)")
    print("=" * 78)
    cells = collections.Counter()
    for _, tour, p1, p2, s in rows:
        cells[(tour, p1, s)] += 1; cells[(tour, p2, s)] += 1
    thin = sum(1 for v in cells.values() if v < 5)
    print(f"  player-surface cells: {len(cells)};  under 5 matches: {thin} "
          f"({thin/len(cells)*100:.0f}%)")

    def w(K_SURF):
        ov = collections.defaultdict(lambda: 1500.0)
        sf, n_ov, n_sf, out = {}, collections.Counter(), collections.Counter(), []
        for date, tour, win, los, surf in rows:
            kw, kl = (tour, win), (tour, los)
            ksw, ksl = (tour, win, surf), (tour, los, surf)
            for k, ks in ((kw, ksw), (kl, ksl)):
                if ks not in sf:
                    sf[ks] = ov[k]

            def rate(k, ks):
                if K_SURF is None:
                    return ov[k]
                nn = n_sf[ks]
                return ((nn * sf[ks] + K_SURF * ov[k]) / (nn + K_SURF)
                        if nn + K_SURF > 0 else sf[ks])
            if n_ov[kw] >= MIN_N and n_ov[kl] >= MIN_N:
                flip = (hash(date + win) % 2 == 0)
                (x, kx, ksx), (y, ky, ksy) = (((win, kw, ksw), (los, kl, ksl)) if flip
                                              else ((los, kl, ksl), (win, kw, ksw)))
                out.append((min(0.995, max(0.005, elo_p(rate(kx, ksx), rate(ky, ksy)))),
                            1 if x == win else 0))
            # Mirror PRODUCTION exactly: the experience ramp, and the surface
            # rating stepped by its OWN match count. Using a flat K here made the
            # test recommend K_surf 120-200 while the shipped 50 was fitted under
            # the ramp -- the test was measuring a model that does not exist.
            ew = elo_p(ov[kw], ov[kl])
            ov[kw] += tennis_elo.k_for(n_ov[kw]) * (1.0 - ew)
            ov[kl] -= tennis_elo.k_for(n_ov[kl]) * (1.0 - ew)
            ews = elo_p(sf[ksw], sf[ksl])
            sf[ksw] += tennis_elo.k_for(n_sf[ksw]) * (1.0 - ews)
            sf[ksl] -= tennis_elo.k_for(n_sf[ksl]) * (1.0 - ews)
            n_ov[kw] += 1; n_ov[kl] += 1; n_sf[ksw] += 1; n_sf[ksl] += 1
        return out

    grid = [None, 200, 120, 80, 50, 30, 20, 12, 6, 3, 0]
    curves = {K: w(K) for K in grid}
    print(f"\n  {'K_surf':>8s} {'logloss':>9s} {'acc':>7s}   (K_surf=pooled is the null)")
    for K in grid:
        print(f"  {('pooled' if K is None else str(K)):>8s} "
              f"{ll(curves[K]):>9.4f} {acc(curves[K]):>7.4f}")
    # The in-sample curve is NOT the verdict. At the shipped K it dips ~0.001 below
    # pooled around K_surf=80, which is small enough to be selection noise -- so
    # decide it the way K itself was decided, by rolling origin.
    n = len(curves[None])
    folds = 6
    edges = [int(n * i / folds) for i in range(folds + 1)]
    print(f"\n  rolling origin -- choose K_surf on the past, score the next block")
    print(f"  {'fold':>5s} {'block':>7s} {'chosen':>8s} {'chosen ll':>10s} "
          f"{'pooled ll':>10s} {'gain':>8s}")
    gains, chosen = [], []
    for i in range(1, folds):
        lo, hi = edges[i], edges[i + 1]
        best = min(grid, key=lambda K: ll(curves[K][:lo]))
        g = ll(curves[None][lo:hi]) - ll(curves[best][lo:hi])
        gains.append(g); chosen.append(best)
        print(f"  {i:>5d} {hi-lo:>7d} {str(best):>8s} {ll(curves[best][lo:hi]):>10.4f} "
              f"{ll(curves[None][lo:hi]):>10.4f} {g:>+8.4f}")
    mean = sum(gains) / len(gains)
    pos = sum(1 for g in gains if g > 0)
    print(f"\n  chosen per fold: {chosen}")
    print(f"  mean gain vs pooled: {mean:+.4f} logloss  ({pos}/{len(gains)} folds positive)")
    established = mean > 0.002 and pos >= len(gains) - 1
    shipped = getattr(tennis_elo, "K_SURFACE", None)
    print(f"  -> {'SURFACE SPLIT HELPS' if established else 'not established on this data'}"
          f"   (shipped K_SURFACE = {shipped})")
    if established and shipped:
        near = [c for c in chosen if c and abs(c - shipped) <= 30]
        print(f"     folds agreeing with the shipped value: {len(near)}/{len(chosen)}")
    elif not established and shipped:
        print("     NOTE: a surface split IS shipped but this data does not support it.")
        print("     If this is the deep archive, reconsider K_SURFACE.")
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
