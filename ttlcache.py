"""TTL cache with sweep-on-insert — SHARED, because this exact leak has now
been found four times.

The pattern: a module keeps `_cache = {}` and checks the TTL only on READ.
An entry nobody asks for again is never dropped. That is harmless for a
handful of fixed keys and fatal for growing ones — weather keys on
(lat, lon, HOUR), so every hour mints new keys forever; the velocity flag
keys on (pitcher, DATE); price drift keys on ticker and the tennis boards
rotate hundreds of tickers a week. Each worker holds its own copy, times
three. baseball._cached and racing._cached already fixed themselves with a
sweep-on-insert; this module is that same proven pattern extracted so the
NEXT cache doesn't re-grow the leak.

Usage:  ttlcache.cached(_cache, key, ttl_seconds, build_fn)
Entries store their own TTL; every 64th insert sweeps every expired entry
out of that cache, whether or not anyone reads it again. A build failure is
cached as None for the TTL (negative caching), matching the modules this
replaces.
"""

import time

_SWEEP_EVERY = 64
_puts = {}          # id(cache) -> insert count


def cached(cache, key, ttl, fn):
    now = time.time()
    hit = cache.get(key)
    if hit is not None and now - hit[0] < (hit[2] if len(hit) > 2 else ttl):
        return hit[1]
    try:
        val = fn()
    except Exception:
        val = None
    cache[key] = (now, val, ttl)
    n = _puts.get(id(cache), 0) + 1
    _puts[id(cache)] = n
    if n % _SWEEP_EVERY == 0:
        # list() first: worker threads insert while we walk.
        for k, v in list(cache.items()):
            if now - v[0] >= (v[2] if len(v) > 2 else ttl):
                cache.pop(k, None)
    return val
