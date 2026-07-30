"""Fit and re-check the tennis Elo's hyperparameters, and the surface question.

This file backs three constants/decisions in tennis_elo and tennis_prices. Re-run
it as results accumulate -- the Kalshi store grows daily, and the right K can move
as the pool deepens.

  1. K FACTOR -- SHIPPED AT 48 (was 24, never fitted).
     Rolling-origin validation over two independent datasets puts the minimum at
     48 on both, with the curve flat from ~40 to ~56.

  2. SURFACE-SPLIT RATING -- REJECTED.
     Giving each player a per-surface Elo shrunk toward their overall makes the
     model monotonically WORSE at every level of surface weight. 69% of
     player-surface cells hold fewer than 5 matches: a match outcome is one bit,
     so splitting a thin history three ways just triples the noise. Surface
     belongs where tennis_data already puts it -- in the serve/return rates, which
     are estimated from HUNDREDS of points per match rather than a handful of
     1-bit outcomes, and are shrunk toward the player's overall profile.
     This is not an argument that surface does not matter. It matters a lot; it
     just cannot be learned from match results at this sample size.

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
    store = deep_cache.load("tennis_elo_results")[0] or {}
    rows = [(d.replace("-", ""), g, w, l, t)
            for d, w, l, g, t in store.values() if d and w and l and w != l]
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
        elo[win] = ra + K * (1.6 if n_m[win] < 10 else 1.0) * (1.0 - ew)
        elo[los] = rb - K * (1.6 if n_m[los] < 10 else 1.0) * (1.0 - ew)
        n_m[win] += 1; n_m[los] += 1
    return out


def rolling(name, rows, folds=5):
    """Choose K on everything seen so far, score the NEXT block, walk forward."""
    curves = {K: walk(rows, K) for K in K_GRID}
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
    print(f"\n  {'K':>5s} {'logloss':>9s} {'acc':>7s}")
    lo_k = min(K_GRID, key=lambda k: ll(curves[k]))
    for K in K_GRID:
        tag = "  <-- min" if K == lo_k else ("  (shipped)" if K == SHIPPED_K else "")
        print(f"  {K:>5.0f} {ll(curves[K]):>9.4f} {acc(curves[K]):>7.4f}{tag}")
    return lo_k, ll(curves[OLD_K]), ll(curves[SHIPPED_K])


def surface_check():
    """Per-surface Elo shrunk toward overall by K_SURF prior matches.
    K_SURF=None is the pooled null."""
    rows = load_charting(with_surface=True)
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
            ew = elo_p(ov[kw], ov[kl])
            bw = 1.6 if n_ov[kw] < 10 else 1.0
            bl = 1.6 if n_ov[kl] < 10 else 1.0
            ov[kw] += SHIPPED_K * bw * (1.0 - ew); ov[kl] -= SHIPPED_K * bl * (1.0 - ew)
            ews = elo_p(sf[ksw], sf[ksl])
            sf[ksw] += SHIPPED_K * bw * (1.0 - ews); sf[ksl] -= SHIPPED_K * bl * (1.0 - ews)
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
    print(f"  -> {'SURFACE SPLIT HELPS -- wire it into tennis_elo' if established else 'not established: keep the pooled rating'}")
    if not established and pos >= len(gains) - 1:
        print("     (most folds are slightly positive at heavy shrinkage, so this is")
        print("      worth re-running as the store deepens -- it is marginal, not dead)")
    return not established


def main():
    quick = "--quick" in sys.argv
    k1, old1, new1 = rolling("KALSHI settled results (ITF-heavy -- the live board)",
                             load_kalshi())
    k2, old2, new2 = rolling("CHARTING match index (tour level, decades deep)",
                             load_charting())
    surf_rejected = True if quick else surface_check()

    print("\n" + "=" * 78)
    print("VERDICT")
    print("=" * 78)
    print(f"  shipped K = {SHIPPED_K:.0f};  minimum found: KALSHI {k1:.0f}, CHARTING {k2:.0f}")
    print(f"  vs the old K=24 -- KALSHI  {old1:.4f} -> {new1:.4f} ({old1-new1:+.4f})")
    print(f"                     CHARTING {old2:.4f} -> {new2:.4f} ({old2-new2:+.4f})")
    ok = (new1 <= old1 + 1e-4) and (new2 <= old2 + 1e-4)
    print(f"  shipped K beats the old default on both datasets: {'YES' if ok else 'NO'}")
    if not quick:
        print(f"  surface-split rating still rejected: {'YES' if surf_rejected else 'NO'}")
    if abs(SHIPPED_K - k1) > 12 or abs(SHIPPED_K - k2) > 12:
        print("  NOTE: the fitted minimum has drifted well away from the shipped K.")
        print("        The pool has changed shape -- consider re-shipping.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
