"""Multi-core runner for the season Monte Carlos.

A season sim runs N independent simulated seasons and reports each team's title /
playoff / win-total rates. Independent means embarrassingly parallel: split the N
seasons across worker processes, run each chunk as its OWN smaller sim, and merge
the resulting boards. K estimates of N/K seasons combine into exactly the same
board a single N-season run would produce — percentages are averaged (each chunk
is an unbiased estimate), win-total histograms are summed.

This is a thin wrapper around the sim functions themselves (pro_sim.project,
nfl_season.run_season, cfb.run_season): each already takes (n, seed), so a worker
just calls it with a slice of the seasons and workers=1. Nothing about the sim
internals changes. Falls back to a single in-process run on any multiprocessing
failure (restricted hosts, spawn quirks), so it is always safe to call.

Worker count defaults to the machine's logical processors (env DEEP_WORKERS
overrides). On fork platforms (Linux/macOS) the parent warms the network caches
once before the pool forks, so workers inherit them instead of each re-fetching.
"""

import multiprocessing as mp
import os
import random


def n_workers(cap=16):
    """Logical processors, capped; DEEP_WORKERS env var overrides."""
    try:
        env = int(os.environ.get("DEEP_WORKERS", "0") or 0)
    except ValueError:
        env = 0
    if env > 0:
        return env
    return max(1, min(mp.cpu_count() or 1, cap))


def _invoke(spec):
    """Top-level (picklable) worker entry: import the module and call the sim.
    spec = (module_name, func_name, kwargs)."""
    import importlib
    mod, fn, kwargs = spec
    return getattr(importlib.import_module(mod), fn)(**kwargs)


def _merge_boards(boards, team_key, avg_fields, sum_fields):
    """Combine per-chunk boards into one. Scalar rate fields are averaged
    weighted by each chunk's sim count; count histograms are summed."""
    total_n = sum(b.get("n_sims") or 0 for b in boards) or 1
    out = dict(boards[0])                       # metadata from the first chunk
    out["n_sims"] = total_n
    merged = {}
    order = []
    for b in boards:
        w = b.get("n_sims") or 0
        for t in b.get("teams", []):
            k = t.get(team_key)
            if k not in merged:
                merged[k] = {"base": dict(t), "wsum": {}, "hist": {}}
                order.append(k)
            m = merged[k]
            for f in avg_fields:
                v = t.get(f)
                if isinstance(v, (int, float)):
                    m["wsum"][f] = m["wsum"].get(f, 0.0) + v * w
            for f in sum_fields:
                d = t.get(f) or {}
                acc = m["hist"].setdefault(f, {})
                for key, c in d.items():
                    acc[key] = acc.get(key, 0) + c
    teams = []
    for k in order:
        m = merged[k]
        t = dict(m["base"])
        for f in avg_fields:
            if f in m["wsum"]:
                t[f] = round(m["wsum"][f] / total_n, 2)
        for f in sum_fields:
            if f in m["hist"]:
                t[f] = m["hist"][f]
        teams.append(t)
    out["teams"] = teams
    return out


def run(module, func, base_kwargs, n, seed, team_key, avg_fields, sum_fields,
        workers=None, min_n=400):
    """Run `func(**base_kwargs, n, seed)` across processes and merge the boards.
    Returns the merged board, or None to signal the caller to run single-process
    (worker count is 1, n is small, or multiprocessing was unavailable)."""
    workers = workers or n_workers()
    if workers <= 1 or n < min_n:
        return None
    rng = random.Random(seed)
    base = n // workers
    specs = []
    for i in range(workers):
        ni = base + (1 if i < n - base * workers else 0)
        if ni <= 0:
            continue
        kw = dict(base_kwargs)
        kw.update(n=ni, seed=rng.randrange(1 << 30), workers=1)
        specs.append((module, func, kw))
    if len(specs) <= 1:
        return None
    # Warm the parent's network caches once so forked workers inherit them. The
    # probe also tells us there's something to simulate: a None here (e.g. an
    # offseason league with no schedule yet) means skip the pool entirely.
    try:
        warm = dict(base_kwargs)
        warm.update(n=1, seed=0, workers=1)
        if _invoke((module, func, warm)) is None:
            return None
    except Exception:
        pass
    try:
        with mp.Pool(len(specs)) as pool:
            boards = [b for b in pool.map(_invoke, specs) if b]
    except Exception:
        return None                              # caller falls back to single run
    if not boards:
        return None
    return _merge_boards(boards, team_key, avg_fields, sum_fields)
