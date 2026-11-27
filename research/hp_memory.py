"""Measured memory of a classic tournament build, by stage, at scaled counts (2026-11-26).

The build profiles (pc_profiles.memory_gb) were sized from per-component ESTIMATES: deep ~29 GB, maximum ~58 GB
on a 500-player slate. The owner, before buying RAM: find out what the build really holds at once. This
harness runs the real `dfs_tourney.build_nfl_classic` on a synthetic slate (DraftKings, Sleeper and the depth
chart stubbed, as the guard files do; the pool handed over exactly as nfl_dfs_sim.player_pool hands the engine
model's: each player's worlds a Python list of floats rounded to 2 dp) in a fresh process per configuration,
and records the resident memory at every stage the build logs (a sampler thread reads /proc every 50 ms).

    python3 research/hp_memory.py --grid           # the grid below, one process each, JSON to stdout
    python3 research/hp_memory.py --grid 1,3 --compact   # the same points with the compact pool
    python3 research/hp_memory.py --one '{"games": 13, "n_sims": 40000, ...}'

Linux only (/proc). Single worker, so every byte is in one process (production's scoring workers map the
lineup matrices from shared memory; the pool and the make-it matrix are the parent's).
"""
import json
import os
import resource
import subprocess
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

_FIRST = ["Ace", "Bo", "Cal", "Dax", "Eli", "Finn", "Gus", "Hal", "Ike", "Jax", "Kip", "Lou", "Max", "Ned", "Oz",
          "Pip", "Quin", "Rex", "Sid", "Tad", "Uly", "Vic"]
_WORDS = ["Hawk", "Bear", "Lion", "Wolf", "Colt", "Ram", "Jet", "Owl", "Fox", "Elk", "Yak", "Cod", "Eel", "Bee",
          "Ant", "Asp", "Bat", "Cat", "Dog", "Emu", "Gnu", "Hen", "Jay", "Kit", "Pug", "Rat", "Sow", "Tit",
          "Wren", "Moth", "Mule", "Newt"]
# players per team, DraftKings-like depth (a 13-game main slate is ~550 players)
_ROSTER = (("QB", 3, 4800, 8200), ("RB", 5, 4000, 9000), ("WR", 8, 3000, 9200), ("TE", 4, 2500, 7000),
           ("DST", 1, 2200, 3800))


def _rss():
    with open("/proc/self/statm") as fh:
        return int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")


def slate(n_games, seed=42):
    import random
    g = random.Random(seed)
    teams = [f"T{chr(65 + i // 26)}{chr(65 + i % 26)}" for i in range(2 * n_games)]
    word = dict(zip(teams, _WORDS))
    rows, pool, gated, pid = [], {}, {}, 1000
    head = "Position,Name + ID,Name,ID,Roster Position,Salary,Game Info,TeamAbbrev,AvgPointsPerGame,Status"
    for gi in range(n_games):
        away, home = teams[2 * gi], teams[2 * gi + 1]
        game = f"{away}@{home} 11/01/2026 01:00PM ET"
        for tm in (away, home):
            k = 0
            for pos, n, lo, hi in _ROSTER:
                for j in range(n):
                    nm = f"{_FIRST[k]} {word[tm]}" if pos != "DST" else f"{word[tm]}s"
                    k += 1
                    sal = int(g.uniform(lo, hi) // 100 * 100)
                    deep = (pos in ("QB", "TE") and j >= 1) or (pos == "RB" and j >= 2) or (pos == "WR" and j >= 3)
                    proj = round(max(0.5, sal / 420 + g.uniform(-3, 3) - (4 if deep else 0)), 1)
                    rp = pos if pos in ("QB", "TE", "DST") else f"{pos}/FLEX"
                    rows.append(f"{pos},{nm} ({pid}),{nm},{pid},{rp},{sal},{game},{tm},{proj},ACTIVE")
                    pool[nm] = {"pos": pos, "team": tm, "proj": proj}
                    if deep:
                        keep = 1 if pos in ("QB", "TE") else 2 if pos == "RB" else 3
                        gated[nm] = f"{pos}{j + 1} by depth chart (keep {pos}1{'-' + str(keep) if keep > 1 else ''})"
                    pid += 1
    return head + "\n" + "\n".join(rows), pool, gated


def fake_pool(pool, n, seed=1234, compact=False):
    """The engine model's hand-over, its SHAPE exactly: per player a Python list of n floats rounded to 2 dp
    (nfl_dfs_sim: `_np.round(p["arr"], 2).tolist()`), team-correlated; with `compact`, the float32 row
    player_pool(compact=True) hands over instead (2026-11-26)."""
    import numpy as np
    rng = np.random.default_rng(seed)
    teams = sorted({p["team"] for p in pool.values()})
    lat = {tm: rng.standard_normal(n).astype(np.float64) for tm in teams}
    out = {}
    for nm, p in sorted(pool.items()):
        sd = 0.55 * p["proj"] + 1.5
        a = np.maximum(0.0, p["proj"] + sd * (0.6 * lat[p["team"]] + 0.8 * rng.standard_normal(n)))
        a = np.round(a / 0.02) * 0.02
        s = np.sort(a)
        out[nm] = {"pos": p["pos"], "team": p["team"], "proj": p["proj"], "ceiling": float(s[int(0.9 * (n - 1))]),
                   "floor": float(s[int(0.1 * (n - 1))]),
                   "arr": np.round(a, 2).astype(np.float32) if compact else np.round(a, 2).tolist(), "rec_tgt": 5.0,
                   "sim_mean": float(a.mean())}
        del a, s
    return out


def one(cfg):
    os.environ["VIGIL_NO_BG"] = "1"
    os.environ["VIGIL_POOL_CACHE_DIR"] = ""
    os.environ.pop("VIGIL_CKPT_DIR", None)
    sys.path.insert(0, _ROOT)
    import contextlib
    import dfs_tourney as T
    import dk
    import nfl_dfs as N
    import nfl_dfs_sim
    stage = ["imports"]
    peaks = {}
    order = []
    stop = [False]

    def sampler():
        while not stop[0]:
            r = _rss()
            s = stage[0]
            if s not in peaks:
                order.append(s)
            peaks[s] = max(peaks.get(s, 0), r)
            time.sleep(0.05)
    th = threading.Thread(target=sampler, daemon=True)
    th.start()
    rss0 = _rss()
    csv, pool, gated = slate(int(cfg["games"]))
    n_contests = int(cfg.get("contests", 1))
    detail = {"id": 1, "name": "Memory Millionaire", "prize_pool": 2.0e6, "max_entries": 150000, "entry_fee": 20.0,
              "first_prize": 1000000, "places_paid": 30000, "entered": 100000, "max_entries_per_user": 150,
              "payouts": [{"from": 1, "to": 1, "prize": 1000000.0}, {"from": 2, "to": 10, "prize": 10000.0},
                          {"from": 11, "to": 30000, "prize": 40.0}]}
    sl = {"starts": "2026-11-01T18:00:00.0000000Z", "draft_group_id": 999, "csv": csv,
          "contests": [{"id": i + 1, "prize_pool": 2.0e6, "entries": 100000} for i in range(n_contests)],
          "n_players": len(pool)}
    stage[0] = "pool"
    made = {}

    def player_pool(week, n=3000, **k):
        if n not in made:
            made[n] = fake_pool(pool, n, compact=bool(cfg.get("compact")))
        return made[n]
    rss_pool = None

    def log(msg, *a):
        m = str(msg)
        # the stage is the log line up to its first digit-free phrase: "[tourney] classic 999: pool ..." etc.
        stage[0] = m[:70]

    def depth(players, preseason, seconds_to_lock=None):
        kept, dx = [], []
        for p in players:
            if p["name"] in gated:
                dx.append({"name": p["name"], "pos": p["pos"], "team": p.get("team"), "why": gated[p["name"]]})
            else:
                p["depth"] = f"{p['pos']}1"
                kept.append(p)
        return kept, dx

    @contextlib.contextmanager
    def patched(*triples):
        old = [(o, n, getattr(o, n)) for o, n, _v in triples]
        try:
            for o, n, v in triples:
                setattr(o, n, v)
            yield
        finally:
            for o, n, v in reversed(old):
                setattr(o, n, v)
    kw = dict(n_sims=int(cfg["n_sims"]), opt_worlds=int(cfg["opt_worlds"]), field_n=int(cfg["field_n"]),
              cand_n=int(cfg.get("cand_n", 150000)), cal_n=int(cfg.get("cal_n", 20000)), week=8, preseason=False,
              workers=1, probes=[], model="legacy", log=log, top=40, k_port=int(cfg.get("k_port", 20)),
              candidates=int(cfg.get("candidates", 1200)), chunk=500, seed=7)
    player_pool(8, n=int(cfg["n_sims"]))
    rss_pool = _rss()
    t0 = time.time()
    with patched((dk, "slate_for", lambda sport, draft_group_id=None, exclude_games=None: dict(sl)),
                 (dk, "contest_detail", lambda cid: dict(detail, id=cid)),
                 (nfl_dfs_sim, "player_pool", player_pool), (N, "_apply_depth", depth),
                 (T, "status_sig", lambda names: (None, {})), (T, "_engine_report", lambda *a, **k: {})):
        board = T.build_nfl_classic(999, **kw)
    stage[0] = "done"
    time.sleep(0.2)
    stop[0] = True
    th.join()
    gb = 1024.0 ** 3
    return {"config": cfg, "players": len(pool), "ok": bool(board), "refusal": None if board else T.last_refusal(),
            "seconds": round(time.time() - t0, 1),
            "rss_start_gb": round(rss0 / gb, 3), "rss_after_pool_gb": round(rss_pool / gb, 3),
            "pool_gb": round((rss_pool - rss0) / gb, 3),
            "peak_gb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024 / gb, 3),
            "stages": [{"stage": s, "peak_gb": round(peaks[s] / gb, 3)} for s in order]}


GRID = [
    # the world-count term (players x worlds): pool lists, X, the scoring pass's per-world tables
    {"games": 13, "n_sims": 20000, "opt_worlds": 2000, "field_n": 20000, "cand_n": 20000, "candidates": 300},
    {"games": 13, "n_sims": 60000, "opt_worlds": 2000, "field_n": 20000, "cand_n": 20000, "candidates": 300},
    # the field term (lineups x players)
    {"games": 13, "n_sims": 20000, "opt_worlds": 2000, "field_n": 100000, "cand_n": 20000, "candidates": 300},
    # the portfolio term (contests x candidates x worlds)
    {"games": 13, "n_sims": 60000, "opt_worlds": 2000, "field_n": 20000, "cand_n": 20000, "candidates": 1200},
    {"games": 13, "n_sims": 60000, "opt_worlds": 2000, "field_n": 20000, "cand_n": 20000, "candidates": 1200,
     "contests": 3},
]


def main():
    if "--one" in sys.argv:
        print(json.dumps(one(json.loads(sys.argv[sys.argv.index("--one") + 1]))))
        return
    grid = GRID
    if "--grid" in sys.argv and len(sys.argv) > sys.argv.index("--grid") + 1 \
            and not sys.argv[sys.argv.index("--grid") + 1].startswith("--"):
        grid = [GRID[int(i)] for i in sys.argv[sys.argv.index("--grid") + 1].split(",")]
    if "--compact" in sys.argv:
        grid = [dict(c, compact=True) for c in grid]
    for cfg in grid:
        r = subprocess.run([sys.executable, os.path.abspath(__file__), "--one", json.dumps(cfg)],
                           capture_output=True, text=True, cwd=_ROOT)
        line = (r.stdout.strip().splitlines() or ["{}"])[-1]
        try:
            print(json.dumps(json.loads(line)), flush=True)
        except ValueError:
            print(json.dumps({"config": cfg, "error": (r.stderr or r.stdout)[-2000:]}), flush=True)


if __name__ == "__main__":
    main()
