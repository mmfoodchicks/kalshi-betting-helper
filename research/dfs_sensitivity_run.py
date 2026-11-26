"""The projection-sensitivity run (dfs_sensitivity; research/reports/dfs_sensitivity.md).

On the PC, on a real board (its evaluator artifact and served payload must be the
same build):

    cd /d "D:\\Games\\Kalshi Betting\\kalshi-betting-helper"
    python research\\dfs_sensitivity_run.py --dg 141234 --dry-run      # the scenarios and the time it will take
    python research\\dfs_sensitivity_run.py --dg 141234 --workers 12    # the run (add --gpu where CuPy is installed)

Run it in a second Command Prompt, never in the worker's window (CLAUDE.md, "The PC
offload"). It reads the board's evaluator artifact (pc-simcache/poolcache/evalart),
the board the PC built (boardshare's copy on disk), simulates every scenario's
worlds through nfl_dfs_sim.player_pool on a seed of its own, files them in ITS OWN
worlds cache (pc-simcache/sensitivity/poolcache -- never the boards' cache, whose
24 entries a run would otherwise evict), and writes

    pc-simcache/sensitivity/<dg>[_pick]/<artifact>-<UTC stamp>/
        summary.json   every figure (per scenario and lineup), the receipts, no per-world arrays
        share.json     the compact digest meant for the data branch (no entrant data exists in a run:
                       the field is the board's model sample, the lineups the board's own and the owner's)
        blocks.npz     the block sums, for any re-analysis without re-simulating
        digest.md      the tables, readable

Nothing here is wired into pc_worker: what the orchestrator should wire is in the
report's section 7.

Without a board (`--synthetic`), the same pipeline runs end to end on a synthetic
slate through the REAL engine (league-average deep profiles, made-up lines and
salaries, the production field sampler): the mechanics demonstration the report's
section 6 quotes. Its numbers describe the method, never a real slate.
"""
import argparse
import contextlib
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("VIGIL_NO_BG", "1")

# The run's own worlds cache: its scenarios are many per-game entries, and the boards' cache keeps 24.
SENS_CACHE_SUBDIR = os.path.join("sensitivity", "poolcache")
SENS_KEEP_ENTRIES = 800
SENS_MAX_BYTES = 20 * 1024 ** 3


def _sim_root():
    return os.environ.get("VIGIL_SIM_CACHE_DIR") or os.path.join(_ROOT, "pc-simcache")


def _art_root():
    """Where the PC's evaluator artifacts are (dfs_evaluate.root() under the worker's settings)."""
    return os.path.join(_sim_root(), "poolcache", "evalart")


def use_own_cache(path=None):
    """Point pool_cache at the run's own directory, with room for a run's
    entries. Process-local: the worker's cache and its bounds are untouched."""
    import pool_cache
    d = path or os.path.join(_sim_root(), SENS_CACHE_SUBDIR)
    os.makedirs(d, exist_ok=True)
    os.environ["VIGIL_POOL_CACHE_DIR"] = d
    pool_cache.KEEP_ENTRIES = SENS_KEEP_ENTRIES
    pool_cache.MAX_BYTES = SENS_MAX_BYTES
    return d


def load_artifact(dg, picked=False, art_id=None, art_root=None):
    """(path, meta) of the slate's newest evaluator artifact (or the named one)."""
    root = art_root or _art_root()
    d = os.path.join(root, f"{int(dg)}{'_pick' if picked else ''}")
    if not os.path.isdir(d):
        return None
    names = [art_id] if art_id else sorted((p for p in os.listdir(d) if ".tmp-" not in p),
                                           key=lambda p: os.path.getmtime(os.path.join(d, p)), reverse=True)
    for n in names:
        p = os.path.join(d, n)
        try:
            with open(os.path.join(p, "meta.json")) as f:
                return p, json.load(f)
        except (OSError, ValueError):
            continue
    return None


def load_field(path):
    import numpy as np
    idx = np.load(os.path.join(path, "field_idx.npy")).astype(np.int64)
    wp = os.path.join(path, "field_w.npy")
    return idx, (np.load(wp) if os.path.isfile(wp) else None)


def board_lineups(board, per_list=20):
    """The board's own lineups: the top lists of the first contest (classic)
    or the board (showdown), the portfolio and the chalk, each once, with
    every list it came from."""
    if not board:
        return []
    if board.get("kind") == "classic":
        res = board.get("results") or {}
        src = next(iter(res.values()), {}) if res else {}
    else:
        src = board
    out, seen = [], {}

    def add(row, label):
        lu = row.get("lineup") or []
        names = [p.get("name") for p in lu]
        cap = next((p.get("name") for p in lu if p.get("slot") == "CPT"), None)
        key = (frozenset(names), cap)
        if key in seen:
            out[seen[key]]["sources"].append(label)
            return
        seen[key] = len(out)
        out.append({"label": f"Vigil {label}", "names": names, "captain": cap, "source": f"optimizer:{label}",
                    "sources": [label]})
    for lst in ("top_top1", "top_top01", "top_money", "top_win", "top_ev"):
        for i, r in enumerate((src.get(lst) or [])[:per_list]):
            add(r, f"{lst}#{i + 1}")
    port = src.get("portfolio") or {}
    if isinstance(port, dict) and "entries" in port:
        for i, r in enumerate(port.get("entries") or []):
            add(r, f"portfolio#{i + 1}")
    elif isinstance(port, dict):
        for k, v in sorted(port.items(), key=lambda kv: -int(kv[0]) if str(kv[0]).isdigit() else 0)[:1]:
            for i, r in enumerate((v or {}).get("entries") or []):
                add(r, f"portfolio{k}#{i + 1}")
    ch = src.get("chalk") or (board.get("field_model") or {}).get("chalk")
    if ch:
        add(ch, "chalk")
    return out


# ---- the synthetic slate (the demonstration and the guards) ------------------------------------
_COMP = ("pass_yd", "pass_td", "int", "rush_yd", "rush_td", "rec", "rec_yd", "rec_td", "fum", "pass_att",
         "pass_cmp", "rec_tgt", "rush_att")


def _line(**kw):
    m = {k: 0.0 for k in _COMP}
    m.update({k: float(v) for k, v in kw.items()})
    return m


def synthetic_slate(n_games=4, seed=0, recon=True):
    """A made-up slate the real engine can play: weekly_games' shape (every
    player with `means` and, when `recon`, a slightly different reconciled
    line -- the two must move together), an engine context on league-average
    deep profiles with drive levels that make favourites, Sleeper-shaped
    defense and kicker projections, and the board's players in an artifact's
    shape with DraftKings-like salaries."""
    import random
    import nfl_deep_sim as E
    import nfl_dfs_sim
    rng = random.Random(f"vigil-sens-synthetic|{seed}")
    T = E.tables()
    abbrs = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH", "III", "JJJ", "KKK", "LLL"][:2 * n_games]
    games, drive, profiles, dst, kick, players = {}, {}, {}, {}, {}, []
    for gi in range(n_games):
        home, away = abbrs[2 * gi], abbrs[2 * gi + 1]
        gp = []
        for team, opp in ((home, away), (away, home)):
            st = rng.uniform(0.85, 1.15)
            drive[team] = round(21.5 * st + (1.5 if team == home else 0.0), 2)
            profiles[team] = E.league_profile(T, team)
            att = 33.0 * rng.uniform(0.92, 1.08)
            rows = [("QB", f"{team} Quarterback", _line(pass_att=att, pass_cmp=0.65 * att, pass_yd=7.1 * att,
                                                        pass_td=1.55 * st, int=0.8, rush_att=3.5, rush_yd=16,
                                                        rush_td=0.15, fum=0.2))]
            for k, (car, tgt) in enumerate(((15.0, 3.5), (7.0, 2.0))):
                car *= rng.uniform(0.8, 1.2)
                rows.append(("RB", f"{team} Back{k + 1}", _line(rush_att=car, rush_yd=4.3 * car, rush_td=0.03 * car * st,
                                                                 rec_tgt=tgt, rec=0.78 * tgt, rec_yd=6.0 * tgt,
                                                                 rec_td=0.03 * tgt, fum=0.05)))
            for k, tgt in enumerate((8.0, 6.3, 4.4)):
                tgt *= rng.uniform(0.85, 1.15)
                rows.append(("WR", f"{team} Receiver{k + 1}", _line(rec_tgt=tgt, rec=0.64 * tgt, rec_yd=8.3 * tgt,
                                                                     rec_td=0.055 * tgt * st)))
            tgt = 5.5 * rng.uniform(0.8, 1.2)
            rows.append(("TE", f"{team} Tightend1", _line(rec_tgt=tgt, rec=0.7 * tgt, rec_yd=7.4 * tgt,
                                                           rec_td=0.05 * tgt * st)))
            for pos, name, m in rows:
                p = {"name": name, "pos": pos, "team": team, "opp": opp, "means": m,
                     "proj_pts": round(nfl_dfs_sim.dk_points(m), 2)}
                if recon:
                    # a reconciled line a few percent off the mean, as nfl_recon leaves one
                    p["recon"] = {k: v * rng.uniform(0.96, 1.04) for k, v in m.items()}
                gp.append(p)
            dst[team] = {"nickname": f"{team} Defense", "proj": round(6.5 + 2.0 * (1.0 - st), 2)}
            kick[team] = {"name": f"{team} Kicker", "pts": 8.0, "fg": [], "xpm": 2.0, "xpa": 2.0}
        games[f"g{gi + 1}"] = {"label": f"{away} @ {home}", "teams": [home, away], "home": home, "away": away,
                               "neutral": False, "players": gp}
        for p in gp:
            sal_raw = (3000 + 200 * p["proj_pts"]) if p["pos"] == "QB" else (1500 + 330 * p["proj_pts"])
            players.append({"name": p["name"], "pos": p["pos"], "team": p["team"], "opp": p["opp"],
                            "game": f"{away}@{home}", "salary": int(min(9800, max(2500, round(sal_raw / 100) * 100))),
                            "proj": float(p["proj_pts"]), "field_only": False})
        for team, opp in ((home, away), (away, home)):
            pr = dst[team]["proj"]
            players.append({"name": dst[team]["nickname"], "pos": "DST", "team": team, "opp": opp,
                            "game": f"{away}@{home}", "salary": int(min(4500, max(2000, round((2000 + 150 * pr) / 100) * 100))),
                            "proj": pr, "field_only": False})
    for p in players:
        p["cpt_salary"] = int(round(1.5 * p["salary"]))
    ctx = {"T": T, "profiles": profiles, "drive": drive, "weight": 0.30, "workers": 1}
    return {"games": games, "ctx": ctx, "dst": dst, "kickers": kick, "players": players,
            "teams": sorted(abbrs)}


def synthetic_contest(capacity=20000, fee=20.0):
    pay = [(1, 1, 10000.0), (2, 2, 5000.0), (3, 3, 3000.0), (4, 5, 1500.0), (6, 10, 750.0), (11, 20, 400.0),
           (21, 50, 200.0), (51, 100, 120.0), (101, 200, 80.0), (201, 500, 60.0), (501, 1000, 50.0),
           (1001, 2000, 40.0), (2001, 4000, 30.0)]
    return {"id": 1, "name": "Synthetic GPP", "entry_fee": fee, "places_paid": 4000, "capacity": capacity,
            "payouts": [{"from": a, "to": b, "prize": p} for a, b, p in pay]}


@contextlib.contextmanager
def synthetic_inputs(slate):
    """Sleeper's defense and kicker projections swapped for the slate's (the
    lines and the context go in through EngineWorlds' own arguments)."""
    import nfl_dfs_sim as S
    saved = (S.dst_projections, S.kicker_projections)
    S.dst_projections = lambda season, week: slate["dst"]
    S.kicker_projections = lambda season, week: slate["kickers"]
    try:
        yield
    finally:
        S.dst_projections, S.kicker_projections = saved


def demo(out_dir, n_games=4, n_worlds=8000, n_gen=4000, field_n=20000, n_cand=1500, workers=2, anchor_n=300,
         log=print):
    """The synthetic board, chosen on generation worlds and stress-tested on
    independent ones. Returns (summary, raw)."""
    import numpy as np
    import dfs_sensitivity as DS
    import dfs_tourney as DT
    t_all = time.time()
    slate = synthetic_slate(n_games=n_games, seed=0)
    slate["ctx"]["anchor_n"], slate["ctx"]["anchor_iters"] = anchor_n, 2
    meta = {"id": "synthetic-demo", "kind": "classic", "dg": 0, "week": 12, "preseason": False, "model": "engine",
            "teams": slate["teams"], "players": slate["players"], "contests": [synthetic_contest()]}
    use_own_cache(os.path.join(out_dir, "poolcache"))
    with synthetic_inputs(slate):
        # the field: the production sampler, calibrated on these players (the casual field)
        log("[demo] drawing the field (calibrated softmax sampler)...")
        fidx, frec = DS.casual_field(meta["players"], field_n, seed=11, cal_n=4000, workers=workers)
        # the "board": candidates drawn by the rule-abiding sampler, chosen on GENERATION worlds
        gen = DS.EngineWorlds(12, slate["teams"], n_gen, seed=101, season="2026", workers=workers,
                              games=slate["games"], ctx=slate["ctx"], log=log)
        draw = DT.FieldSampler([dict(p) for p in meta["players"]], workers)
        try:
            cidx = draw(n_cand * 2, np.random.default_rng(5), frec["beta"] * 1.6, frec["kappa"], rules=True)
        finally:
            draw.close()
        _u, first = np.unique(np.sort(cidx, axis=1), axis=0, return_index=True)
        cidx = cidx[np.sort(first)][:n_cand]
        cands = [{"label": f"cand{i}", "names": [meta["players"][j]["name"] for j in row], "source": "candidate"}
                 for i, row in enumerate(cidx.tolist())]
        log(f"[demo] {len(cands)} candidates; scoring them on {n_gen:,} generation worlds...")
        rg = DS.Runner(meta, (fidx, None), cands, gen, log=log, workers=workers).run([DS.scenario("base")])
        sg = DS.summarize(rg, metrics=("top1", "cash"))
        m1 = np.asarray(sg["metrics"]["top1"]["by_scenario"]["base"]["mean"])
        mc = np.asarray(sg["metrics"]["cash"]["by_scenario"]["base"]["mean"])
        pick = list(dict.fromkeys(np.argsort(-m1)[:25].tolist() + np.argsort(-mc)[:15].tolist()))
        lineups = [dict(cands[i], label=f"board#{k + 1}", source="optimizer:demo") for k, i in enumerate(pick)]
        rows = np.sort(fidx, axis=1)
        u, cnt = np.unique(rows, axis=0, return_counts=True)
        lineups.append({"label": "chalk", "names": [meta["players"][j]["name"] for j in u[int(np.argmax(cnt))]],
                        "source": "field:chalk"})
        # the sensitivity run proper, on worlds of its own seed
        ev = DS.EngineWorlds(12, slate["teams"], n_worlds, seed=DS.sens_seed(meta["id"]), season="2026",
                             workers=workers, games=slate["games"], ctx=slate["ctx"], log=log)
        runner = DS.Runner(meta, (fidx, None), lineups, ev, log=log, workers=workers)
        scen = DS.preset(meta, runner.lus, kind="quick")
        log(f"[demo] {len(scen)} scenarios on {n_worlds:,} worlds...")
        raw = runner.run(scen)
    raw["artifact"] = meta["id"]
    summary = DS.summarize(raw)
    summary["field"] = frec
    summary["timing_s"] = round(time.time() - t_all, 1)
    return summary, raw


# ---- the real board ------------------------------------------------------------------------------
def run_board(dg, picked=False, art_id=None, worlds=40000, workers=12, gpu=False, kind="standard",
              lineups_file=None, scenarios_file=None, out_root=None, season=None, per_list=20, dry_run=False,
              art_root=None, log=print):
    import dfs_sensitivity as DS
    got = load_artifact(dg, picked, art_id, art_root)
    if not got:
        raise SystemExit(f"no evaluator artifact for slate {dg} under {art_root or _art_root()}: rebuild the board "
                         "on the PC first (boards built before 2026-11-26 have none)")
    path, meta = got
    import boardshare
    k = "sd" if meta["kind"] == "showdown" else "cl"
    board, _age = boardshare.get(f"{k}_tourney_nfl_{int(dg)}{'_pick' if picked else ''}", None)
    if board and board.get("eval_artifact") not in (None, meta["id"]):
        log(f"[sens] the board on disk is another build ({board.get('eval_artifact')}): its lineups are not this "
            "artifact's field's; only custom lineups are scored")
        board = None
    lineups = board_lineups(board, per_list=per_list)
    if lineups_file:
        with open(lineups_file) as f:
            lineups += [dict(lu, source=lu.get("source") or "custom") for lu in json.load(f)]
    if not lineups:
        raise SystemExit("no lineups: the board is missing or another build, and no --lineups file was given")
    if gpu:
        os.environ["VIGIL_GPU"] = "1"
    cache = use_own_cache()
    fidx, fw = load_field(path)
    ew = DS.EngineWorlds(meta["week"], meta["teams"], worlds, DS.sens_seed(meta["id"]), season=season,
                         workers=workers, log=log)
    runner = DS.Runner(meta, (fidx, fw), lineups, ew, log=log, workers=workers, board=board)
    scen = DS.preset(meta, runner.lus, kind=kind)
    if scenarios_file:
        with open(scenarios_file) as f:
            scen += [DS.normalize(s) for s in json.load(f)]
    n_games = max(1, len({p.get("game") for p in meta["players"] if p.get("game")}))
    secs, parts = DS.estimate(scen, worlds, n_games, len(fidx), workers=workers, gpu=gpu)
    log(f"[sens] slate {dg} ({meta['kind']}, week {meta['week']}): {len(runner.lus)} lineups "
        f"({len(runner.bad)} illegal skipped), {len(scen)} scenarios x {worlds:,} worlds, field {len(fidx):,}; "
        f"estimate {secs / 60:.0f} min {parts}; worlds cache {cache}")
    for s in scen:
        log(f"[sens]   {s['name']}: {s.get('note') or ''}")
    if dry_run:
        return None
    t0 = time.time()
    raw = runner.run(scen)
    raw["artifact"] = meta["id"]
    summary = DS.summarize(raw)
    out = write_outputs(raw, summary, meta, dg, picked, out_root=out_root,
                        extra={"estimate_s": round(secs), "seconds": round(time.time() - t0, 1),
                               "worlds": worlds, "seed": DS.sens_seed(meta["id"]), "season": ew.season,
                               "gpu": bool(gpu), "workers": int(workers)})
    log(f"[sens] done in {(time.time() - t0) / 60:.1f} min: {out}")
    return out


def _jsonable(o):
    import numpy as np
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, np.ndarray):
        return _jsonable(o.tolist())
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, float) and o != o:
        return None
    return o


def share_digest(summary, raw, top=20):
    """The compact digest for the data branch: per scenario the counts of
    lineups that moved beyond 2 and 3 SE, the rank correlations beside their
    noise floor, and the most robust lineups by name. No per-world data; no
    entrant data exists in a run."""
    out = {"artifact": raw.get("artifact"), "contest": summary["contest"], "worlds": summary["worlds"],
           "empty_scenarios": summary.get("empty_scenarios"), "scenarios": [], "lineups": []}
    m = summary["metrics"].get("top1") or {}
    for s in raw["scenarios"]:
        v = (m.get("by_scenario") or {}).get(s["name"])
        if v:
            out["scenarios"].append({"name": s["name"], "note": s.get("note"), "n_z2": v["n_z2"], "n_z3": v["n_z3"],
                                     "spearman": v["spearman"], "kendall": v["kendall"],
                                     "split_noise_floor": v["split_noise_floor"], "split_cross": v["split_cross"],
                                     "worlds": v["worlds"]})
    for r in sorted(m.get("by_lineup") or [], key=lambda r: r["rank_by_mean"])[:top]:
        lu = raw["lineups"][r["lineup"]]
        out["lineups"].append(dict(r, label=lu["label"], names=lu["names"], captain=lu.get("captain"),
                                   sources=lu.get("sources")))
    out["worlds_needed_top1"] = m.get("worlds_needed")
    return out


def write_outputs(raw, summary, meta, dg, picked, out_root=None, extra=None):
    import numpy as np
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    root = out_root or os.path.join(_sim_root(), "sensitivity")
    d = os.path.join(root, f"{int(dg)}{'_pick' if picked else ''}", f"{meta['id']}-{stamp}")
    os.makedirs(d, exist_ok=True)
    import dfs_sensitivity as DS
    full = {"version": DS.VERSION, "artifact": meta["id"], "dg": int(dg), "kind": meta["kind"], "week": meta["week"],
            "run": extra or {}, "scenarios": raw["scenarios"], "lineups": raw["lineups"],
            "illegal_lineups": raw["illegal_lineups"], "contests": raw["contests"], "receipts": raw["receipts"],
            "summary": summary}
    with open(os.path.join(d, "summary.json"), "w") as f:
        json.dump(_jsonable(full), f)
    with open(os.path.join(d, "share.json"), "w") as f:
        json.dump(_jsonable(dict(share_digest(summary, raw), run=extra or {})), f)
    np.savez_compressed(os.path.join(d, "blocks.npz"), counts=np.asarray(raw["counts"]),
                        **{k.replace(":", "_"): np.asarray(v) for k, v in raw["sums"].items()},
                        keys=np.asarray(sorted(raw["sums"]), dtype="<U32"))
    with open(os.path.join(d, "digest.md"), "w") as f:
        f.write(DS.render_markdown(summary, raw))
    return d


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dg", type=int, help="the slate's draft group id")
    ap.add_argument("--picked", action="store_true", help="the picked board (_pick)")
    ap.add_argument("--art-id", help="a named evaluator artifact (default: the newest)")
    ap.add_argument("--art-root", help="where the evaluator artifacts are (default pc-simcache/poolcache/evalart)")
    ap.add_argument("--worlds", type=int, default=40000)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--gpu", action="store_true", help="the field's thresholds on the GPU (verified per job)")
    ap.add_argument("--preset", default="standard", choices=("standard", "quick"))
    ap.add_argument("--lineups", help="a JSON list of custom lineups: [{label, names, captain?}]")
    ap.add_argument("--scenarios", help="a JSON list of extra scenarios (dfs_sensitivity.normalize's shape)")
    ap.add_argument("--per-list", type=int, default=20)
    ap.add_argument("--season")
    ap.add_argument("--out")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--synthetic", action="store_true", help="the mechanics demonstration on a made-up slate")
    a = ap.parse_args(argv)
    if a.synthetic:
        out = a.out or os.path.join(_sim_root(), "sensitivity", "synthetic")
        summary, raw = demo(out, workers=max(1, min(a.workers, 4)), n_worlds=min(a.worlds, 8000))
        d = write_outputs(raw, summary, {"id": "synthetic-demo", "kind": "classic", "week": 12}, 0, False,
                          out_root=out, extra={"synthetic": True, "timing_s": summary.get("timing_s")})
        print(f"[demo] written to {d}")
        return 0
    if not a.dg:
        ap.error("--dg is required (or --synthetic)")
    run_board(a.dg, a.picked, a.art_id, a.worlds, a.workers, a.gpu, a.preset, a.lineups, a.scenarios, a.out,
              a.season, a.per_list, a.dry_run, a.art_root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
