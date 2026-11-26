"""The custom lineup evaluator (2026-11-26; research/reports/dfs_cache_evaluator.md,
stages 2-4). The owner: "Build any lineup manually and evaluate it against
Vigil's existing simulated games." The review: "The evaluator should allow an
arbitrary legal lineup to be scored without resimulating football games. A
manually constructed lineup must be distinguishable from an
optimizer-selected lineup."

Three pieces, all on the PC (numpy; the large arrays never reach Render):

1. The ARTIFACT a tournament build leaves (`save_artifact`, called from
   dfs_tourney): the board's players in its own order (name, position, team,
   salary, captain salary, projection, game, field-only), the contests with
   their full payout tables (the board export drops them), and the field the
   board scored against -- the classic field sample (rows of player indices)
   or the showdown universe and its weights. The board's random field cannot
   be drawn again (production boards are unseeded), so it is kept.

2. The FINAL worlds (`prepare`): FINAL_N worlds simulated for the slate on a
   seed of their own (`final_seed`), through nfl_dfs_sim.player_pool and so
   through the worlds cache (pool_cache): nothing on the board was chosen on
   them. The field is scored on them once, into each world's threshold score
   (0.01-point buckets, the board's own bucketing) at every rank boundary that
   matters: each payout tier's last place, the top 0.1% and 1%, the cash line,
   and a ladder of finish fractions. Kept as final.npz beside the artifact.

3. EVALUATE (`evaluate`): a lineup's score in every final world (the sum of
   its players' worlds, a captain at 1.5x) against those thresholds:
   - its score distribution (mean, SD, P10 ... P99);
   - P(top 0.1%), P(top 1%), P(cash), P(paid more than the fee) and the
     finish-fraction ladder, per contest;
   - payout and ROI by the tier its score reaches. Ties are NOT split (a
     lineup level with field mass takes the better tier), and the rank is
     the field's mass above times (entries - 1) -- so both are labelled
     experimental, as the board's own money columns are;
   - the per-player correlation matrix on the same worlds;
   - legality by DraftKings' rules (slots, cap, two games / both teams) and
     whether it also passes Vigil's house rules (a field-only player is legal
     on DraftKings but outside them).
   Every lineup carries its `source`: "custom" for one the owner built,
   "optimizer:<list>" for the board's own -- scored on the same worlds, so
   the comparison is out of sample for both.

`looks` counts how many evaluation requests a slate's final worlds have
answered. A lineup changed in response to them is development data, and the
result says so past the first look (the review: "Do not allow users or the
optimizer to repeatedly select based on the final evaluation partition
without relabeling it as development data").
"""
import json
import os
import shutil
import time
import zlib

import errlog

try:
    import numpy as np
except ImportError:                       # the server: evaluation is the PC's
    np = None

FINAL_N = 40000
KEEP_PER_SLATE = 3
FINISH_LADDER = (0.001, 0.01, 0.05, 0.10, 0.20, 0.50)
_CAP = 50000
_CL_SLOTS = {"QB": (1, 1), "RB": (2, 3), "WR": (3, 4), "TE": (1, 2), "DST": (1, 1)}
_SD_POS = ("QB", "RB", "WR", "TE", "K", "DST")


def root():
    import pool_cache
    d = pool_cache.cache_dir()
    return os.path.join(d, "evalart") if d else None


def _slate_dir(dg, picked):
    r = root()
    return os.path.join(r, f"{int(dg)}{'_pick' if picked else ''}") if r else None


def _atomic_dir(final, write):
    tmp = f"{final}.tmp-{os.getpid()}-{int(time.time() * 1000)}"
    os.makedirs(tmp)
    try:
        write(tmp)
        os.replace(tmp, final)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


# ---- 1. the artifact ------------------------------------------------------------
def save_artifact(kind, dg, picked, ents, field_idx, field_w, contests, week, preseason, model,
                  n_sims, teams, built_ts):
    """Write the build's evaluation artifact; returns its id, or None where
    there is no cache directory (the server) or numpy. Never raises: an
    artifact must not cost a board."""
    if np is None or root() is None:
        return None
    try:
        art_id = f"{int(dg)}{'_pick' if picked else ''}-{int(built_ts)}"
        d = _slate_dir(dg, picked)
        os.makedirs(d, exist_ok=True)
        final = os.path.join(d, art_id)
        if os.path.isdir(final):
            return art_id
        players = [{"name": e["name"], "pos": e["pos"], "team": e.get("team"), "opp": e.get("opp"),
                    "game": e.get("game"), "salary": int(e["salary"]),
                    "cpt_salary": int(e.get("cpt_salary") or round(1.5 * int(e["salary"]))),
                    "proj": float(e.get("proj") or 0.0), "field_only": bool(e.get("_field_only"))} for e in ents]
        cons = [{"id": c.get("id"), "name": c.get("name"), "entry_fee": float(c.get("entry_fee") or 0.0),
                 "places_paid": int(c.get("places_paid") or 0), "capacity": int(_capacity(c)),
                 "payouts": [{"from": int(r["from"]), "to": int(r["to"]), "prize": float(r["prize"])}
                             for r in (c.get("payouts") or [])]} for c in contests]
        meta = {"id": art_id, "kind": kind, "dg": int(dg), "picked": bool(picked), "week": int(week),
                "preseason": bool(preseason), "model": model, "n_sims": int(n_sims),
                "teams": sorted(str(t).upper() for t in teams), "built_ts": float(built_ts),
                "players": players, "contests": cons, "field_rows": int(len(field_idx)),
                "field_width": int(np.asarray(field_idx).shape[1]), "looks": 0}

        def write(tmp):
            np.save(os.path.join(tmp, "field_idx.npy"), np.asarray(field_idx, dtype=np.int16), allow_pickle=False)
            if field_w is not None:
                np.save(os.path.join(tmp, "field_w.npy"), np.asarray(field_w, dtype=np.float64), allow_pickle=False)
            with open(os.path.join(tmp, "meta.json"), "w") as f:
                json.dump(meta, f)
        _atomic_dir(final, write)
        # retention: the newest few builds of a slate
        olds = sorted((p for p in os.listdir(d) if ".tmp-" not in p), key=lambda p: os.path.getmtime(os.path.join(d, p)))
        for p in olds[:-KEEP_PER_SLATE]:
            shutil.rmtree(os.path.join(d, p), ignore_errors=True)
        return art_id
    except Exception as e:
        errlog.note("EVAL-artifact", e, path=str(dg))
        return None


def _capacity(c):
    try:
        import dfs_tourney
        return dfs_tourney.contest_capacity(c)
    except Exception:
        return int(c.get("max_entries") or c.get("entered") or 0)


def load_artifact(dg, picked=False, art_id=None):
    """(dir, meta) of the newest artifact for the slate (or the named one)."""
    d = _slate_dir(dg, picked)
    if not d or not os.path.isdir(d):
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


# ---- 2. the final worlds ------------------------------------------------------------
def final_seed(art_id):
    return zlib.crc32(f"vigil-final|{art_id}".encode())


def tier_fractions(contest):
    """[(fraction, prize)] per payout tier, best rank first: a lineup whose
    field mass above is at most `fraction` reaches that tier's last place.
    Rank = 1 + (entries - 1) x mass above."""
    C = max(2, int(contest["capacity"]))
    out = []
    for r in sorted(contest["payouts"], key=lambda r: r["from"]):
        if r["prize"] <= 0:
            continue
        out.append(((r["to"] - 1) / (C - 1), r["prize"]))
    return out


def _fractions(meta):
    """Every rank fraction a threshold is kept for, sorted, and the index of
    each named one."""
    qs = set(FINISH_LADDER) | {0.0}
    for c in meta["contests"]:
        C = max(2, int(c["capacity"]))
        qs |= {q for q, _p in tier_fractions(c)}
        qs.add((max(1, c["places_paid"]) - 1) / (C - 1))
    return sorted(qs)


def _match_worlds(meta, pool):
    import nfl_dfs
    nidx, norm = nfl_dfs._norm_index(pool)
    rows, missing = [], []
    for p in meta["players"]:
        sim = nfl_dfs._pool_match(pool, p["name"], p["pos"], p.get("team"), nidx, norm)
        if not (sim and sim.get("arr")):
            missing.append(p["name"])
            rows.append(None)
        else:
            rows.append(sim["arr"])
    return rows, missing


# ---- the field's per-world thresholds, on the CPU or the PC's GPU ------------------------------------
# The owner's RTX 4070 SUPER measured this kernel 7.8x the CPU (research/gpu_bench.py, 20,000 worlds x
# 300,000 lineups, 2026-11-26: 102.5 s against 11.6 s + 1.5 s start-up, every bucket and threshold equal):
# the score products and the bucketing are 12-18x on the GPU; the histograms only gain once many worlds
# share one bincount (world j's buckets offset by j x _NB), because one world a call is ~10 tiny kernels
# and the syncs cupy.bincount makes to size its output. Opt-in ("gpu": true in vigil-pc.cfg sets
# VIGIL_GPU=1 in the evaluator's lane); the first chunk is recomputed on the CPU and compared cell by cell,
# and any mismatch or error sends the whole job back to the CPU, ledgered. The server never has CuPy.
GPU_HIST_BATCH = 50
# What computed the latest prepare()'s thresholds and how long it took; the PC's evaluator lane puts it on
# the result the tab shows, so the GPU's real-workload timing is visible from the owner's phone.
LAST_COMPUTE = {}
# Worlds per bincount. The owner's bench (2026-11-26, synthetic 20,000 x 300,000): 50 a call 9.0 s of
# histograms, 250 a call 1.3 s (whole kernel 20.1x the CPU, 2.3 GB of VRAM, every cell equal). 250 is the
# candidate; the default stays 50 until the real workload is timed ("gpu_hist_batch" in vigil-pc.cfg sets
# VIGIL_GPU_HIST_BATCH in the evaluator lane).


def _gpu_hist_batch():
    try:
        v = int(os.environ.get("VIGIL_GPU_HIST_BATCH") or GPU_HIST_BATCH)
    except ValueError:
        v = GPU_HIST_BATCH
    return max(1, min(v, 1000))


def _thresholds_cpu(B, w, qs):
    """Production's form, one world a call: the lowest bucket whose field mass strictly above is at most
    each fraction (that mass never rises with the bucket)."""
    import dfs_tourney
    T = np.zeros((B.shape[0], len(qs)), dtype=np.int32)
    for j in range(B.shape[0]):
        hist = np.bincount(B[j], weights=w, minlength=dfs_tourney._NB)
        above = np.cumsum(hist[::-1])[::-1] - hist            # field mass strictly above each bucket
        T[j] = np.searchsorted(-above, -(qs + 1e-12), side="left")
    return T


def _cpu_chunks(X, WfT, w, qs, chunk, upto=None):
    import dfs_tourney
    W = X.shape[1] if upto is None else min(upto, X.shape[1])
    T = np.zeros((W, len(qs)), dtype=np.int32)
    for a in range(0, W, chunk):
        Xc = np.ascontiguousarray(X[:, a:min(a + chunk, W)].T)
        T[a:a + Xc.shape[0]] = _thresholds_cpu(dfs_tourney._buckets(Xc @ WfT), w, qs)   # the board's bucketing
    return T


def _gpu():
    """CuPy on the first device when the evaluator lane asked for it, else None."""
    if os.environ.get("VIGIL_GPU") != "1":
        return None
    try:
        try:
            import cupy                          # PC-only extra, never in requirements.txt
        except ImportError:
            errlog.note("EVAL-gpu-init", msg="gpu requested but CuPy is not installed; CPU used")
            return None
        cupy.cuda.Device(0).use()
        return cupy
    except Exception as e:                       # a broken install, no device, no driver: the CPU answers
        errlog.note("EVAL-gpu-init", e)
        return None


def _thresholds_gpu(cp, X, WfT, w, qs, chunk):
    """The same thresholds on the GPU: dfs_tourney._buckets in place, then GPU_HIST_BATCH worlds a
    bincount and, per fraction, the count of buckets whose mass above exceeds it -- searchsorted's left
    point on a row that never rises."""
    import dfs_tourney
    NB, RES = dfs_tourney._NB, dfs_tourney._RES
    W, M = X.shape[1], WfT.shape[1]
    Xd, WfTd = cp.asarray(X), cp.asarray(WfT)
    hb = max(1, min(_gpu_hist_batch(), chunk))
    wt = cp.tile(cp.asarray(w, dtype=cp.float64), hb)
    qh = [float(q) + 1e-12 for q in qs]
    T = cp.zeros((W, len(qs)), dtype=cp.int32)
    for a in range(0, W, chunk):
        S = cp.ascontiguousarray(Xd[:, a:a + chunk].T) @ WfTd
        cp.multiply(S, RES, out=S)
        S += 0.5
        cp.clip(S, 0, NB - 1, out=S)
        B = S.astype(cp.int32)
        del S
        for u in range(0, B.shape[0], hb):
            n = min(hb, B.shape[0] - u)
            off = (cp.arange(n, dtype=cp.int32) * NB)[:, None]
            hist = cp.bincount((B[u:u + n] + off).ravel(), weights=wt[:n * M], minlength=n * NB).reshape(n, NB)
            above = cp.cumsum(hist[:, ::-1], axis=1)[:, ::-1] - hist
            for k, q in enumerate(qh):
                T[a + u:a + u + n, k] = cp.count_nonzero(above > q, axis=1)
        del B
    out = cp.asnumpy(T)
    del Xd, WfTd, wt, T
    cp.get_default_memory_pool().free_all_blocks()
    return out


def field_thresholds(X, WfT, w, qs, chunk, xp=None):
    """(T, device): every final world's threshold buckets. The GPU when asked and verified on the first
    chunk against the CPU exactly, else the CPU. `xp` lets a test hand in the array module."""
    cp = xp if xp is not None else _gpu()
    if cp is not None:
        try:
            T = _thresholds_gpu(cp, X, WfT, w, qs, chunk)
            ref = _cpu_chunks(X, WfT, w, qs, chunk, upto=chunk)
            bad = int((T[:len(ref)] != ref).sum())
            if bad == 0:
                return T, f"GPU (batch {max(1, min(_gpu_hist_batch(), chunk))}; first chunk verified on the CPU)"
            errlog.note("EVAL-gpu-mismatch", msg=f"{bad} of {ref.size} first-chunk thresholds differ; CPU used")
        except Exception as e:
            errlog.note("EVAL-gpu", e)
    return _cpu_chunks(X, WfT, w, qs, chunk), "CPU"


def prepare(path, meta, n=FINAL_N, workers=None, log=print, pool=None):
    """Simulate the slate's final worlds (through the worlds cache) and score
    the board's field on them once. Idempotent: an existing final.npz is
    reused. `pool` lets a test pass the worlds in."""
    out = os.path.join(path, "final.npz")
    if os.path.isfile(out):
        LAST_COMPUTE.clear()
        LAST_COMPUTE.update(device="saved thresholds reused (computed on this slate's first evaluation)", seconds=0.0)
        return out
    import dfs_tourney
    if pool is None:
        import nfl_dfs_sim
        t0 = time.time()
        pool = nfl_dfs_sim.player_pool(meta["week"], n=int(n), preseason=meta["preseason"], model=meta["model"],
                                       seed=final_seed(meta["id"]), teams=set(meta["teams"]),
                                       workers=workers or dfs_tourney._workers(None)) or {}
        log(f"[eval] {meta['id']}: {n:,} final worlds simulated ({time.time() - t0:.0f}s)")
    rows, missing = _match_worlds(meta, pool)
    W = min((len(r) for r in rows if r is not None), default=0)
    if not W:
        raise RuntimeError("no final worlds for this slate's players")
    P = len(rows)
    X = np.zeros((P, W), dtype=np.float32)
    for i, r in enumerate(rows):
        if r is not None:
            X[i] = np.asarray(r[:W], dtype=np.float32)
    idx = np.load(os.path.join(path, "field_idx.npy")).astype(np.int64)
    wpath = os.path.join(path, "field_w.npy")
    w = np.load(wpath) if os.path.isfile(wpath) else np.full(len(idx), 1.0 / len(idx))
    cpt = 1.5 if meta["kind"] == "showdown" else None
    Wf = dfs_tourney.lineup_matrix(idx, P, cpt_mult=cpt)
    qs = np.asarray(_fractions(meta), dtype=np.float64)
    t0 = time.time()
    chunk = max(16, int(dfs_tourney._CHUNK_ELEMS // max(1, len(idx))))
    WfT = np.ascontiguousarray(Wf.T)
    T, device = field_thresholds(X, WfT, w, qs, chunk)
    LAST_COMPUTE.clear()
    LAST_COMPUTE.update(device=device, seconds=round(time.time() - t0, 1), worlds=int(W), field=int(len(idx)))
    log(f"[eval] {meta['id']}: field of {len(idx):,} scored on {W:,} final worlds on the {device} "
        f"({time.time() - t0:.0f}s)")
    def write(tmp):
        # float32, exactly the matrix the board itself scores (dfs_tourney builds X as float32)
        np.savez(os.path.join(tmp, "final.npz"), X=X, T=T, qs=qs,
                 missing=np.asarray(missing if missing else [], dtype="<U80"))
    tmpdir = f"{path}/.final-{os.getpid()}"
    os.makedirs(tmpdir, exist_ok=True)
    try:
        write(tmpdir)
        os.replace(os.path.join(tmpdir, "final.npz"), out)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return out


def _load_final(path):
    z = np.load(os.path.join(path, "final.npz"), allow_pickle=False)
    return z["X"], z["T"], z["qs"], [str(m) for m in z["missing"]]


# ---- 3. evaluate -------------------------------------------------------------------
def check_lineup(meta, names, captain=None):
    """(player indices, problems, house-rule notes). DraftKings' rules only
    decide legality; Vigil's own (the depth gate's field-only players) are
    noted, never enforced on the owner's lineup."""
    at = {p["name"]: i for i, p in enumerate(meta["players"])}
    probs, notes = [], []
    idx = []
    for nm in names:
        if nm not in at:
            probs.append(f"{nm} is not in this board's simulated pool (no projection, ruled out or not on the slate)")
        else:
            idx.append(at[nm])
    if len(set(names)) != len(names):
        probs.append("a player is listed twice")
    pl = [meta["players"][i] for i in idx]
    if meta["kind"] == "showdown":
        if len(names) != 6:
            probs.append(f"a showdown lineup is a captain and five flex players (got {len(names)})")
        if captain not in names:
            probs.append("name the captain (one of the six)")
        sal = sum(p["cpt_salary"] if p["name"] == captain else p["salary"] for p in pl)
        if len({p["team"] for p in pl}) < 2 and len(pl) == 6:
            probs.append("DraftKings requires players from both teams")
    else:
        if len(names) != 9:
            probs.append(f"a classic lineup is nine players (got {len(names)})")
        cnt = {}
        for p in pl:
            cnt[p["pos"]] = cnt.get(p["pos"], 0) + 1
        for pos, (lo, hi) in _CL_SLOTS.items():
            if not lo <= cnt.get(pos, 0) <= hi:
                probs.append(f"{pos}: {cnt.get(pos, 0)} (DraftKings allows {lo}-{hi} with the FLEX)")
        if sum(cnt.get(p, 0) for p in ("RB", "WR", "TE")) != 7 and len(pl) == 9:
            probs.append("the FLEX must be a RB, WR or TE")
        if len({p.get("game") for p in pl}) < 2 and len(pl) == 9:
            probs.append("DraftKings requires players from at least two games")
        sal = sum(p["salary"] for p in pl)
    if sal > _CAP:
        probs.append(f"salary ${sal:,} is over the ${_CAP:,} cap")
    fo = [p["name"] for p in pl if p.get("field_only")]
    if fo:
        notes.append(f"outside Vigil's depth-chart gate (legal on DraftKings): {', '.join(fo)}")
    return idx, probs, notes, sal


def _pct(a, q):
    return round(float(np.percentile(a, q)), 2)


def score_lineup(X, T, qs, meta, idx, captain_i=None):
    """The per-world figures for one lineup on the final worlds."""
    s = X[idx].sum(axis=0, dtype=np.float32)
    if captain_i is not None:
        s = s + np.float32(0.5) * X[captain_i]
    import dfs_tourney
    # the board's own bucketing (dfs_tourney._buckets): 0.01 points, rounded, clipped to the grid
    b = np.clip(np.floor(s.astype(np.float64) * dfs_tourney._RES + 0.5), 0, dfs_tourney._NB - 1).astype(np.int64)
    qi = {round(float(q), 12): i for i, q in enumerate(qs)}

    def reach(q):
        return float(np.mean(b >= T[:, qi[round(float(q), 12)]]))
    out = {"mean": round(float(s.mean()), 2), "sd": round(float(s.std()), 2),
           "p10": _pct(s, 10), "p25": _pct(s, 25), "p50": _pct(s, 50), "p75": _pct(s, 75),
           "p90": _pct(s, 90), "p95": _pct(s, 95), "p99": _pct(s, 99),
           "finish": {f"top_{q:g}": round(100.0 * reach(q), 3) for q in FINISH_LADDER}}
    per = []
    for c in meta["contests"]:
        tiers = tier_fractions(c)
        prize = np.zeros(len(b), dtype=np.float64)
        got = np.zeros(len(b), dtype=bool)
        for q, p in tiers:                         # best tier first: the first one reached pays
            hit = (~got) & (b >= T[:, qi[round(float(q), 12)]])
            prize[hit] = p
            got |= hit
        C = max(2, int(c["capacity"]))
        fee = c["entry_fee"]
        cash_q = (max(1, c["places_paid"]) - 1) / (C - 1)
        money_q = max((q for q, p in tiers if p > fee), default=None)
        ev = float(prize.mean())
        per.append({"contest_id": c["id"], "name": c["name"], "entry_fee": fee,
                    "top01_pct": round(100.0 * reach(0.001), 3), "top1_pct": round(100.0 * reach(0.01), 3),
                    "cash_pct": round(100.0 * reach(cash_q), 2),
                    "money_pct": round(100.0 * reach(money_q), 2) if money_q is not None else None,
                    "first_pct_experimental": round(100.0 * reach(0.0), 4),
                    "ev_experimental": round(ev - fee, 2),
                    "roi_pct_experimental": round(100.0 * (ev - fee) / fee, 1) if fee else None})
    out["contests"] = per
    return out


def evaluate(path, meta, lineups, board=None, top=3):
    """Score `lineups` -- [{"label", "names", "captain"?, "source"?}] -- and,
    beside them, the board's own top lineups, all on the slate's final worlds."""
    X, T, qs, missing = _load_final(path)
    res = {"artifact": meta["id"], "kind": meta["kind"], "dg": meta["dg"], "worlds": int(X.shape[1]),
           "worlds_note": f"{X.shape[1]:,} final worlds simulated on their own seed: no lineup on the board was "
                          "chosen on them, so a custom lineup and Vigil's are compared out of sample. Payouts do "
                          "not split ties and read rank off the field's mass, so EV and first place are experimental.",
           "missing_from_final": missing, "lineups": []}
    meta["looks"] = int(meta.get("looks") or 0) + 1
    try:
        with open(os.path.join(path, "meta.json"), "w") as f:
            json.dump(meta, f)
    except OSError as e:
        errlog.note("EVAL-looks", e)
    res["looks"] = meta["looks"]
    if meta["looks"] > 1:
        res["development_note"] = (f"these final worlds have now answered {meta['looks']} evaluation requests: a "
                                   "lineup changed in response to them is fitted to them, so treat repeat comparisons "
                                   "as development data, not a held-out read")
    at = {p["name"]: i for i, p in enumerate(meta["players"])}
    todo = [dict(lu, source=lu.get("source") or "custom") for lu in (lineups or [])]
    for lst in ("top_top1", "top_money", "top_win"):
        for r in _board_rows(board, lst)[:top]:
            names = [p.get("name") for p in r.get("lineup") or []]
            cap = next((p.get("name") for p in r.get("lineup") or [] if p.get("slot") == "CPT"), None)
            todo.append({"label": f"Vigil {lst} #{r.get('_rank', 0) + 1}", "names": names, "captain": cap,
                         "source": f"optimizer:{lst}"})
    seen = set()
    for lu in todo:
        names = [str(n) for n in lu.get("names") or []]
        key = (frozenset(names), lu.get("captain"))
        if not lu["source"].startswith("custom"):
            if key in seen:                  # a lineup on two of the board's lists shows once
                continue
            seen.add(key)
        idx, probs, notes, sal = check_lineup(meta, names, lu.get("captain"))
        gone = [n for n in names if n in missing]
        if gone:
            probs.append(f"no final worlds for {', '.join(gone)} (dropped from the simulation since the build)")
        row = {"label": lu.get("label") or "custom lineup", "source": lu["source"], "names": names,
               "captain": lu.get("captain"), "salary": sal, "legal": not probs, "problems": probs, "notes": notes}
        if not probs:
            cap_i = at.get(lu.get("captain")) if meta["kind"] == "showdown" else None
            row.update(score_lineup(X, T, qs, meta, idx, cap_i))
            if len(idx) > 1:
                C = np.corrcoef(X[idx])
                row["corr"] = [[round(float(v), 3) for v in r_] for r_ in C]
            row["proj"] = round(sum(meta["players"][i]["proj"] for i in idx)
                                + (0.5 * meta["players"][cap_i]["proj"] if cap_i is not None else 0.0), 2)
        res["lineups"].append(row)
    return res


def _board_rows(board, lst):
    """A list's rows from a board payload (classic: the first contest's)."""
    if not board:
        return []
    if board.get("kind") == "classic":
        res = board.get("results") or {}
        first = next(iter(res.values()), {}) if res else {}
        rows = first.get(lst) or []
    else:
        rows = board.get(lst) or []
    return [dict(r, _rank=i) for i, r in enumerate(rows)]
