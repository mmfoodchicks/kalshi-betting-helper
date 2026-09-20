"""Week-2 execution harness around the FROZEN slot-allocation v1 scorer.
RESEARCH ONLY. Pre-registered 2026-09-20 before the 17:00 UTC lock of draft
group 153428 (the NFL $3M Fantasy Football Millionaire, contest 195648007).

What this module never does: edit research/cl_slot.py, the frozen file
research/data/cl_slot_frozen.json, dfs_tourney.classic_sample, the frozen
parameters, the objectives, or production behaviour. The candidate was
defined by the exact candidate code and the production sampler's hash at the
freeze; this harness REFUSES to score when any of them has changed, at
execution time, not only in the guard suite (the reviewer's requirement,
2026-09-20). The scorer's advertised standings-hash argument was not enforced
by research.cl_slot.score (its dispatch dropped it); the wrapper requires and
verifies it here, then calls the frozen scorer unchanged.

Commands (python3 -m research.cl_week2 ...):
  verify                       every frozen identity check; exit non-zero on any difference
  candidates                   every served-board snapshot of dg 153428 on the data branch, with its verdict
  select [--final] [--offline] the primary input by ACTUAL timestamps (build stamp and data-branch
                               commit both before lock); --final writes week2_input_receipt.json ONCE
                               and pins the board bytes; without it, a rehearsal file
  reconstruct <stamp>          the fallback pool from a pre-lock prospective capture, compared to the
                               served primary; exercised before lock so the prose path is a code path
  preflight <board json> [n]   the frozen candidate on a pool without the field: completion, acceptance,
                               legality, the FLEX draw obeyed, determinism; no ownership is read
  archive                      every pre-lock input worth having later, hashed; future-study material only
  postgame <export> --sha256 <expected> [--contest 195648007]
                               the one command after the field: pin by hash -> verify contest and draft
                               group -> reconcile rows -> verify every frozen hash -> primary seed ->
                               secondary seeds -> timing arm -> report from the template. No live fetch.

Input selection (deterministic): the primary is the served-board snapshot of
draft group 153428 on the sim-history branch whose board build stamp AND
whose data-branch commit (the fetch's upper bound) both precede
2026-09-20 17:00:00 UTC, latest build first, earliest commit on a tie. A cron
scheduled before lock but committed after lock is archival only. With no
valid snapshot the primary is the pool reconstructed from the latest pre-lock
prospective capture by production's own pool assembly on the pinned feeds.
The early capture (2026-09-20 00:47 UTC, 16.2 h before lock) is the
input-timing-sensitivity arm whatever the primary is; it never decides.

Stochastic sensitivity (pre-registered): the primary result is the frozen
REAL_SEED 20260913 through research.cl_slot.score itself. Four secondary
seeds, 20260914-20260917, run both arms through the same frozen functions
and are reported as mean / SD / range of the candidate-minus-current deltas
on both co-primaries. They are sensitivity only, never a validation and
never a way to choose a seed.

Support holes (pre-registered): the co-primaries condition on FULLY
REPRESENTABLE entries, the frozen real_from_export's definition (a lineup
with any player outside the served pool is omitted). The report states the
total rows, the non-blank active entries, the representable count, the
excluded count and share, the unsupported players behind the exclusions and
the roster-slot mass outside support. Materially worse coverage than week 1
weakens the interpretation; no projection is invented for a missing player.

Interpretation rules: in research/reports/cl_week2_template.md, committed
with blank cells before the field existed; the harness fills the template's
cells and nothing else (render() refuses a key the template does not carry).
"""
import collections
import datetime
import gzip
import hashlib
import inspect
import json
import os
import re
import subprocess
import sys
import time
import zipfile

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from research import cl_field as C  # noqa: E402
from research import cl_sampler as S  # noqa: E402
from research import cl_slot as V  # noqa: E402
from research import s6_capture  # noqa: E402

DG = 153428
WEEK = 2
CONTEST = 195648007            # the $20 $3M Millionaire, the owner's entry
CONTESTS = (195648007, 195648008)
LOCK_UTC = "2026-09-20 17:00:00 UTC"
DATA_BRANCH = "sim-history"
SNAPSHOT_PATH = "errors/tourney-153428.json"
FREEZE_COMMIT = "00e2943"
# The frozen candidate, pinned by bytes: the frozen file, and research/cl_slot.py
# as it stood when this harness was committed (assembly-only code was added to
# that module after the freeze at 1166c1a, so whole-file identity with the freeze
# commit is not the contract; the producing functions' AST identity with
# 00e2943 is, and any later change to the file must re-pin this constant in
# the open, where a diff shows it).
FROZEN_SHA256 = "62b83debdd0c9458831327b3fbc5e1479aea7d9ddedaf0ea902a87ece79f2080"
CL_SLOT_FILE_SHA256 = "a8e3eba302fed269dc65bc55cc68d6fc29a32c6dc1379d39505f79ba44844cd3"
PARAMS = {"beta": 0.15, "eta": 0.0, "delta": 3.05371, "flex_probs_rb_wr_te": [0.4251, 0.3613, 0.2136]}
PRODUCING = ("slot_sampler", "sample", "real_slot_shares", "model_slot_shares", "slot_cross_entropy", "quantiles", "wasserstein1",
             "real_from_export", "grade_field", "_objective", "_grid_pass", "_delta_bisect", "fit", "score")
SECONDARY_SEEDS = (20260914, 20260915, 20260916, 20260917)
PREFLIGHT_N = 100000
FLEX_DRAW_CHECK_N = 5000
RECON_SIMS = 200               # arrays are not used by the field sampler; proj is Sleeper's number regardless
OUT = os.path.join(C.DATA, "cl_week2")
RECEIPT = os.path.join(OUT, "week2_input_receipt.json")
# NOT under research/data/dk_classic: that directory's manifest is one of the
# second artifact's IDENTITY FILES, which cl_sampler.content_identity requires
# to be byte-identical to its content at the seed partials' commit. Writing a
# week-2 entry there would refuse that assembly and turn its guard red, so the
# week-2 board is pinned here and the receipt is its manifest.
BOARD_COPY = os.path.join(OUT, f"served_board_{DG}.json")
PRO = os.path.join(C.DATA, "prospective", str(DG))
STANDINGS_DIR = os.path.join(OUT, "standings")      # gitignored: the plain CSV the frozen loader reads
TEMPLATE = os.path.join(ROOT, "research", "reports", "cl_week2_template.md")
REPORT = os.path.join(ROOT, "research", "reports", "cl_week2.md")
GROUPS = ("QB", "RB", "WR", "TE", "FLEX", "DST")
SLOT_POS = {0: ("QB",), 1: ("RB",), 2: ("RB",), 3: ("WR",), 4: ("WR",), 5: ("WR",), 6: ("TE",), 7: ("RB", "WR", "TE"), 8: ("DST",)}
VERDICTS = {
    (True, True): "the slot-allocation and spend mechanisms transferred on this slate",
    (True, False): "the allocation transferred, the spend penalty did not",
    (False, True): "the spend behaviour transferred, the week-1 slot allocation did not",
    (False, False): "the candidate failed to transfer",
}


def _sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _utc(ts=None):
    return datetime.datetime.fromtimestamp(ts if ts is not None else time.time(), datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _ts(s):
    """'YYYY-mm-dd HH:MM:SS UTC' or ISO-8601 with offset -> epoch seconds."""
    s = s.strip()
    if s.endswith(" UTC"):
        return datetime.datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=datetime.timezone.utc).timestamp()
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


LOCK_TS = _ts(LOCK_UTC)


def _jsonable(o):
    """JSON-safe copy. The Kalshi ladder index nests dicts keyed by tuples
    (market, side), which json.dump refuses; archival material must not be
    lost to that, so tuple keys become "a|b" and tuples become lists."""
    if isinstance(o, dict):
        return {(k if isinstance(k, (str, int, float, bool)) or k is None else "|".join(str(x) for x in k) if isinstance(k, tuple) else str(k)): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, (str, int, float, bool)) or o is None:
        return o
    return str(o)


def _git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, stderr=subprocess.DEVNULL)


def _stamp():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# ---- 1. the frozen identity, enforced at execution time ----------------------------------
def verify(log=print):
    """Every identity the candidate's meaning rests on. Refuses (SystemExit)
    on any difference; the report of what was checked is returned."""
    import dfs_tourney as T
    fails = []
    frozen_sha = _sha(V.FROZEN)
    if frozen_sha != FROZEN_SHA256:
        fails.append(f"frozen file sha256 {frozen_sha[:12]} is not the pinned {FROZEN_SHA256[:12]}")
    frozen = json.load(open(V.FROZEN))
    if frozen.get("parameters") != PARAMS:
        fails.append(f"frozen parameters {frozen.get('parameters')} are not the pinned {PARAMS}")
    src_path = os.path.join(ROOT, "research", "cl_slot.py")
    src_now = open(src_path).read()
    src_sha = hashlib.sha256(src_now.encode()).hexdigest()
    if src_sha != CL_SLOT_FILE_SHA256:
        fails.append(f"research/cl_slot.py sha256 {src_sha[:12]} is not the pinned {CL_SLOT_FILE_SHA256[:12]}: the candidate's module changed after the harness pinned it")
    then = S._git_show(FREEZE_COMMIT, "research/cl_slot.py")
    if then is None:
        fails.append(f"research/cl_slot.py at the freeze commit {FREEZE_COMMIT} is not in this repository's history")
        d_then = {}
    else:
        d_then = S._defs(then.decode())
    d_now = S._defs(src_now)
    for name in PRODUCING:
        if name not in d_then or name not in d_now or d_then[name] != d_now[name]:
            fails.append(f"producing function {name} differs from the freeze commit {FREEZE_COMMIT} by the AST")
    consts = {"BETA_GRID": (0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50), "ETA_GRID": (0.0, 0.5, 1.0, 1.5, 2.0, 3.0), "FIT_SEED": 20260920,
              "REAL_SEED": 20260913, "MAX_RAW_FACTOR": 20, "CHUNK": 100000, "FIT_N": 40000, "CAP": 50000, "DELTA_BRACKET": (0.0, 8.0), "DELTA_STEPS": 12}
    for k, want in consts.items():
        if getattr(V, k, None) != want:
            fails.append(f"research.cl_slot.{k} is {getattr(V, k, None)}, not {want}")
    subs = [{"from": o, "to": n} for o, n in V.SUBSTITUTIONS]
    if frozen.get("sampler", {}).get("substitutions") != subs:
        fails.append("the module's five source substitutions are not the frozen file's")
    _fn, rc = V.slot_sampler()
    if rc["base_sha256"] != frozen["sampler"]["base_sha256"]:
        fails.append("production's classic_sample no longer hashes to the base the candidate was built on (a later change to the production sampler changes the candidate's meaning)")
    if rc["variant_sha256"] != frozen["sampler"]["variant_sha256"]:
        fails.append("the generated slot-sampler variant no longer hashes to the frozen variant")
    prod_sha = hashlib.sha256(inspect.getsource(T.classic_sample).encode()).hexdigest()
    if prod_sha != frozen["sampler"]["base_sha256"]:
        fails.append("dfs_tourney.classic_sample's source hash is not the frozen base hash")
    if frozen.get("provenance", {}).get("commit") != FREEZE_COMMIT or frozen.get("provenance", {}).get("dirty") is not False:
        fails.append("the frozen file is not stamped clean at the pre-registration commit")
    rep = {"checked_utc": _utc(), "passed": not fails, "failures": fails,
           "frozen_file_sha256": frozen_sha, "cl_slot_file_sha256": src_sha, "cl_slot_file_sha256_at_freeze": frozen.get("source_sha256"),
           "producing_functions_identical_to": FREEZE_COMMIT, "producing_functions": list(PRODUCING),
           "sampler_base_sha256": rc["base_sha256"], "sampler_variant_sha256": rc["variant_sha256"], "classic_sample_sha256": prod_sha,
           "parameters": PARAMS}
    if fails:
        raise SystemExit("REFUSED: the frozen candidate's identity does not hold:\n  " + "\n  ".join(fails))
    log(f"[W2] verify: frozen {frozen_sha[:12]}, cl_slot.py {src_sha[:12]}, {len(PRODUCING)} producing functions identical to {FREEZE_COMMIT}, "
        f"classic_sample {prod_sha[:12]} = frozen base; passed")
    return rep


# ---- 2. the input: every served snapshot, judged by actual timestamps ------------------
def snapshots(fetch=True, log=print):
    """Every data-branch commit of the served board of dg 153428, oldest
    first, each with its build stamp, its commit time (the fetch's upper
    bound: the workflow commits seconds after it fetches) and a verdict."""
    if fetch:
        subprocess.run(["git", "fetch", "-q", "origin", DATA_BRANCH], cwd=ROOT, check=True)
    lines = _git("log", f"origin/{DATA_BRANCH}", "--format=%H|%cI", "--", SNAPSHOT_PATH).decode().split()
    out = []
    for line in lines:
        h, ct = line.split("|")
        raw = _git("show", f"{h}:{SNAPSHOT_PATH}")
        d = json.loads(raw)
        committed = _ts(ct)
        built = float(d.get("built_ts") or 0)
        why = []
        if d.get("draft_group_id") != DG:
            why.append("not this draft group")
        if d.get("kind") != "classic":
            why.append("not a classic board")
        if not built:
            why.append("no build stamp")
        elif built >= LOCK_TS:
            why.append("built at or after lock")
        if committed >= LOCK_TS:
            why.append("fetched (committed to the data branch) at or after lock: archival only")
        out.append({"commit": h[:7], "commit_full": h, "committed_utc": _utc(committed), "committed_ts": committed,
                    "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw), "built_ts": built, "built_utc": _utc(built) if built else None,
                    "minutes_before_lock": round((LOCK_TS - built) / 60.0, 1) if built else None,
                    "draft_group_id": d.get("draft_group_id"), "kind": d.get("kind"), "players": len(d.get("players") or []),
                    "field_only": sum(1 for p in d.get("players") or [] if p.get("field_only")),
                    "pool_sig": d.get("pool_sig"), "status_sig": d.get("status_sig"), "valid": not why, "why_not": why})
    out.sort(key=lambda s: (s["built_ts"], s["committed_ts"]))
    for s in out:
        log(f"[W2] snapshot {s['commit']} committed {s['committed_utc']}: built {s['built_utc']} ({s['minutes_before_lock']} min before lock), "
            f"{s['players']} players, sha {s['sha256'][:12]} -> {'VALID' if s['valid'] else 'invalid: ' + '; '.join(s['why_not'])}")
    return out


def _captures():
    """Every prospective capture of the draft group, oldest first, pre-lock
    or not (the flag travels with each)."""
    out = []
    for st in sorted(os.listdir(PRO)) if os.path.isdir(PRO) else []:
        p = os.path.join(PRO, st, "capture.json")
        if os.path.exists(p):
            out.append((st, os.path.join(PRO, st), json.load(open(p))))
    return out


def _identity(caps):
    for _st, _dir, info in reversed(caps):
        if info.get("slate_identity"):
            return info["slate_identity"]
    return None


def select(final=False, fetch=True, log=print):
    """The primary input by the rule in the module docstring. --final writes
    the receipt once and pins the board bytes; otherwise a rehearsal file."""
    from research import provenance
    ver = verify(log)
    snaps = snapshots(fetch=fetch, log=log)
    valid = [s for s in snaps if s["valid"]]
    caps = _captures()
    pre = [c for c in caps if c[2].get("pre_lock")]
    now = time.time()
    if valid:
        prim = max(valid, key=lambda s: (s["built_ts"], -s["committed_ts"]))
        raw = _git("show", f"{prim['commit_full']}:{SNAPSHOT_PATH}")
        board = json.loads(raw)
        kind = "served_snapshot"
        source = {"branch": DATA_BRANCH, "commit": prim["commit_full"], "path": SNAPSHOT_PATH, "workflow": ".github/workflows/error-log.yml"}
        fetched_utc, built_ts = prim["committed_utc"], prim["built_ts"]
    else:
        if not pre:
            raise SystemExit("no valid served snapshot and no pre-lock prospective capture: nothing to select")
        st, _dir, info = pre[-1]
        board = reconstruct(st, log=log)
        raw = json.dumps(board, indent=1, sort_keys=True).encode()
        kind = "reconstructed_from_prospective_capture"
        source = {"capture_stamp": st, "path": os.path.relpath(_dir, ROOT)}
        fetched_utc, built_ts = info["captured_utc"], float(_ts(info["captured_utc"]))
        prim = None
    ents = S.served_pool(board)
    sha = hashlib.sha256(raw).hexdigest()
    losers = []
    for s in snaps:
        if prim is not None and s["commit_full"] == prim["commit_full"]:
            continue
        if not s["valid"]:
            losers.append({"commit": s["commit"], "built_utc": s["built_utc"], "committed_utc": s["committed_utc"], "lost_because": "; ".join(s["why_not"])})
        elif prim is not None:
            losers.append({"commit": s["commit"], "built_utc": s["built_utc"], "committed_utc": s["committed_utc"],
                           "lost_because": f"built earlier than the selected board ({s['built_utc']} against {prim['built_utc']})" if s["built_ts"] < prim["built_ts"]
                           else "same build, committed later (the earliest commit of a build is its fetch)"})
    timing = pre[0] if pre else None
    rec = {"meta": {"stage": "week-2 input receipt: the primary pool for the frozen protocol, selected by actual timestamps before the field exists",
                    "selected_utc": _utc(now), "selected_before_lock": now < LOCK_TS, "final": bool(final),
                    "selection_rule": ("the served-board snapshot of dg 153428 on sim-history whose build stamp and data-branch commit both precede lock, "
                                       "latest build first, earliest commit on a tie; a post-lock commit is archival only; with no valid snapshot, the pool "
                                       "reconstructed from the latest pre-lock prospective capture"),
                    "provenance": provenance.stamp(worlds=None, model="input selection; nothing fitted", seed="UNSEEDED")},
           "selected": {"kind": kind, "path": os.path.relpath(BOARD_COPY, ROOT) if final else None, "sha256": sha, "bytes": len(raw), "source": source,
                        "fetched_utc": fetched_utc, "built_ts": built_ts, "built_utc": _utc(built_ts), "minutes_before_lock": round((LOCK_TS - built_ts) / 60.0, 1),
                        "draft_group": DG, "slate_identity": _identity(caps), "pool_sig": board.get("pool_sig"),
                        "players": len(board.get("players") or []), "pool_after_served_pool": len(ents),
                        "field_only": sum(1 for e in ents if e["_field_only"]), "lock_utc": LOCK_UTC},
           "frozen_file_sha256": ver["frozen_file_sha256"], "candidate_source_sha256": ver["cl_slot_file_sha256"],
           "candidate_source_sha256_at_freeze": ver["cl_slot_file_sha256_at_freeze"], "production_classic_sample_sha256": ver["classic_sample_sha256"],
           "sampler_variant_sha256": ver["sampler_variant_sha256"],
           "candidates": snaps, "why_the_selected_input_beat_each_other_candidate": losers,
           "timing_sensitivity_arm": ({"capture_stamp": timing[0], "path": os.path.relpath(timing[1], ROOT), "captured_utc": timing[2]["captured_utc"],
                                       "hours_to_lock": timing[2]["hours_to_lock"], "files_sha256": timing[2]["files"],
                                       "role": "input-timing sensitivity only; never decides"} if timing else None),
           "prospective_captures": [{"stamp": st, "captured_utc": i["captured_utc"], "hours_to_lock": i["hours_to_lock"], "pre_lock": i["pre_lock"]} for st, _d, i in caps]}
    os.makedirs(OUT, exist_ok=True)
    if final:
        if os.path.exists(RECEIPT):
            raise SystemExit(f"{RECEIPT} exists: the receipt is written once and never rewritten")
        if os.path.exists(BOARD_COPY) and _sha(BOARD_COPY) != sha:
            raise SystemExit(f"{BOARD_COPY} exists with different bytes; refusing to overwrite a pinned board")
        with open(BOARD_COPY, "wb") as fh:
            fh.write(raw)
        rec["selected"]["pinned_source"] = (
            f"the app's served classic board pulled by .github/workflows/error-log.yml into {DATA_BRANCH} as {SNAPSHOT_PATH} "
            f"(commit {source.get('commit', '')[:7]}, committed {fetched_utc}), copied byte for byte" if kind == "served_snapshot"
            else f"reconstructed from the prospective capture {source.get('capture_stamp')} by research.cl_week2.reconstruct")
        path = RECEIPT
    else:
        os.makedirs(os.path.join(OUT, "rehearsal"), exist_ok=True)
        bp = os.path.join(OUT, "rehearsal", f"board_{sha[:12]}.json")
        with open(bp, "wb") as fh:
            fh.write(raw)
        rec["selected"]["path"] = os.path.relpath(bp, ROOT)
        path = os.path.join(OUT, "rehearsal", f"select_{_stamp()}.json")
    with open(path, "w") as fh:
        json.dump(rec, fh, indent=1, sort_keys=True)
    log(f"[W2] selected {kind}: built {rec['selected']['built_utc']} ({rec['selected']['minutes_before_lock']} min before lock), fetched {fetched_utc}, "
        f"sha {sha[:12]}, {len(ents)} players; {len(losers)} other candidates; {'FINAL receipt' if final else 'rehearsal'} -> {os.path.relpath(path, ROOT)}")
    return rec


# ---- 3. the fallback: production's pool assembly on a pinned capture -------------------
def reconstruct(stamp, n_sims=RECON_SIMS, log=print, kalshi_index=None):
    """A board-shaped pool from a prospective capture, by production's own
    steps (build_nfl_classic's pool section) on the pinned Sleeper feeds and
    roster: DK's playable rows, matched to the projection pool, the
    depth-chart gate, the field-only extras. `proj` is Sleeper's number as
    production carries it; the simulated arrays are not used by any field
    sampler, so n_sims is small. No live fetch: the feeds are patched to the
    capture and the Kalshi ladder to `kalshi_index` (or nothing)."""
    from research import sd_board
    import kalshi_nfl
    import nfl_dfs
    import nfl_dfs_sim
    import simulate
    import dfs_tourney as T
    capdir = os.path.join(PRO, stamp)
    info = json.load(open(os.path.join(capdir, "capture.json")))
    for fn, want in info["files"].items():
        if fn != "capture.json" and _sha(os.path.join(capdir, fn)) != want:
            raise SystemExit(f"{capdir}/{fn} does not hash to the capture's manifest")
    sd_board.feeds(capdir, week=WEEK)
    kalshi_nfl.index = lambda *a, **k: dict(kalshi_index or {})
    nfl_dfs_sim._cache.clear()
    slate, _details = s6_capture.replay(DG, capdir=capdir)
    csv_players = [c for c in simulate.parse_dk_csv(slate["csv"]) if nfl_dfs._playable(c)]
    t0 = time.time()
    pool = nfl_dfs_sim.player_pool(WEEK, n=int(n_sims), preseason=False, model="legacy") or {}
    if not pool:
        raise SystemExit("the projection pool is empty on the pinned feeds")
    nidx, norm = nfl_dfs._norm_index(pool)
    ents, excluded = [], []
    for c in csv_players:
        pos = (c.get("pos") or "").upper().split("/")[0]
        if pos not in T._CL_POS:
            continue
        sim = nfl_dfs._pool_match(pool, c["name"], pos, c.get("team"), nidx, norm)
        if not (sim and sim.get("arr")):
            excluded.append({"name": c["name"], "pos": pos, "team": c.get("team"), "why": "no projection this week"})
            continue
        ents.append({"name": c["name"], "pos": pos, "team": c.get("team"), "opp": nfl_dfs._opp_of(c.get("team"), c.get("game")),
                     "game": nfl_dfs.game_key(c.get("game")), "salary": int(c["salary"]), "proj": round(float(sim["proj"]), 1),
                     "ceiling": sim.get("ceiling"), "floor": sim.get("floor"), "rec_tgt": float(sim.get("rec_tgt") or 0.0)})
    by_name = {e["name"]: e for e in ents}
    ents, dx = nfl_dfs._apply_depth(ents, False, nfl_dfs.seconds_to_lock(slate.get("starts")))
    extra = []
    for d in dx:
        e = by_name.get(d["name"])
        if e is not None and "depth chart" in (d.get("why") or "") and (e.get("proj") or 0) >= T._FIELD_EXTRA_MIN_PROJ:
            e["_field_only"] = True
            e["depth"] = (d["why"].split(" ") or [""])[0]
            extra.append(e)
    extra.sort(key=lambda e: -e["proj"])
    extra = extra[:T._CL_FIELD_KEEP]
    field_only = {e["name"] for e in extra}
    ents = ents + extra
    players = [{"name": e["name"], "pos": e["pos"], "team": e["team"], "opp": e.get("opp"), "game": e.get("game"), "salary": e["salary"],
                "depth": e.get("depth"), "field_only": bool(e.get("_field_only")), "proj": e["proj"], "floor": e.get("floor"), "ceiling": e.get("ceiling")}
               for e in ents]
    board = {"kind": "classic", "sport": "nfl", "draft_group_id": DG, "reconstructed": True, "capture_stamp": stamp,
             "built_ts": int(_ts(info["captured_utc"])), "built_utc": info["captured_utc"], "hours_to_lock_at_capture": info["hours_to_lock"],
             "method": ("build_nfl_classic's pool section on the capture's DK slate, Sleeper projection feeds and roster (research.sd_board.feeds pins them; "
                        "nfl_adp answers 'pinned'); the Kalshi ladder patched to the archived index or nothing; the game arrays simulated at n_sims for the "
                        "pool match only and dropped"),
             "n_sims": int(n_sims), "seconds": round(time.time() - t0, 1), "roster_source": nfl_dfs.roster_state(),
             "slate": {"n_players": slate.get("n_players"), "dropped": slate.get("dropped"), "games": len({(e["team"], e.get("opp")) for e in ents}) // 2,
                       "week": WEEK, "field_only": sorted(field_only)},
             "excluded": excluded + dx, "players": players, "inputs": {"draftkings": s6_capture.dk_hashes(DG, capdir=capdir), "sleeper_feeds": s6_capture.sleeper_hashes(capdir)}}
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, f"reconstructed_board_{stamp}.json")
    with open(path, "w") as fh:
        json.dump(board, fh, indent=1, sort_keys=True)
    log(f"[W2] reconstructed from {stamp}: {len(players)} players ({len(field_only)} field-only), {len(excluded) + len(dx)} excluded, "
        f"{board['seconds']}s -> {os.path.relpath(path, ROOT)}")
    return board


def compare_pools(a, b, label_a="served", label_b="reconstructed"):
    """Player identities, salaries, teams, opponents, games, depth tags,
    field-only flags and projections, one pool against another."""
    A = {p["name"]: p for p in a["players"]}
    B = {p["name"]: p for p in b["players"]}
    common = sorted(set(A) & set(B))
    diffs = {k: [n for n in common if (A[n].get(k) or None) != (B[n].get(k) or None)] for k in ("salary", "team", "opp", "game", "pos")}
    depth = [n for n in common if (A[n].get("depth") or None) != (B[n].get("depth") or None)]
    fo = [n for n in common if bool(A[n].get("field_only")) != bool(B[n].get("field_only"))]
    pd = [(n, float(A[n]["proj"]), float(B[n]["proj"])) for n in common]
    pdiff = [abs(x - y) for _n, x, y in pd]
    return {"players": {label_a: len(A), label_b: len(B), "common": len(common)},
            f"only_in_{label_a}": sorted(set(A) - set(B)), f"only_in_{label_b}": sorted(set(B) - set(A)),
            "mismatches": diffs, "depth_tag_differs": depth, "field_only_differs": fo,
            "projection": {"players_compared": len(pd), "identical": sum(1 for d in pdiff if d == 0), "max_abs_diff": round(max(pdiff), 2) if pdiff else None,
                           "mean_abs_diff": round(float(np.mean(pdiff)), 3) if pdiff else None,
                           "abs_diff_ge_1": [{"name": n, label_a: x, label_b: y} for n, x, y in pd if abs(x - y) >= 1.0]},
            "identical_pools": not (set(A) ^ set(B)) and not any(diffs.values()) and not depth and not fo and all(d == 0 for d in pdiff)}


# ---- 4. the preflight: the frozen candidate on a pool without the field ------------------
def preflight(board_path, n=PREFLIGHT_N, log=print):
    """Completion, acceptance against the frozen computational contract, DK
    legality, the FLEX draw obeyed, determinism under the frozen seed. Reads
    no ownership (the real field's or the model's)."""
    import dfs_tourney as T
    ver = verify(log)
    board = json.load(open(board_path))
    ents = S.served_pool(board)
    sampler, _rc = V.slot_sampler()
    p = PARAMS
    t0 = time.time()
    idx1, rec1 = V.sample(ents, n, np.random.default_rng(V.REAL_SEED), p["beta"], p["eta"], p["delta"], p["flex_probs_rb_wr_te"], sampler=sampler)
    t1 = time.time()
    idx2, rec2 = V.sample(ents, n, np.random.default_rng(V.REAL_SEED), p["beta"], p["eta"], p["delta"], p["flex_probs_rb_wr_te"], sampler=sampler)
    idx3, _rec3 = V.sample(ents, min(n, 20000), np.random.default_rng(V.REAL_SEED + 1), p["beta"], p["eta"], p["delta"], p["flex_probs_rb_wr_te"], sampler=sampler)
    pc = np.asarray([T._CL_CODE[e["pos"]] for e in ents])
    sal = np.asarray([int(e["salary"]) for e in ents], dtype=np.int64)
    game = np.asarray([e["game"] or "" for e in ents])
    m = len(idx1)
    slots_ok = all(np.isin(pc[idx1[:, k]], [T._CL_CODE[q] for q in SLOT_POS[k]]).all() for k in range(9)) if m else False
    distinct = bool((np.sort(idx1, axis=1)[:, 1:] != np.sort(idx1, axis=1)[:, :-1]).all()) if m else False
    used = sal[idx1].sum(axis=1) if m else np.zeros(0)
    two_games = bool(np.asarray([len(set(game[row])) >= 2 for row in idx1]).all()) if m else False
    flex = {}
    for code, kind in ((1, "RB"), (2, "WR"), (3, "TE")):
        rep = {}
        out = sampler(ents, FLEX_DRAW_CHECK_N, np.random.default_rng(V.REAL_SEED + 10 + code), p["beta"], 0.0, report=rep, flex_kind=np.full(FLEX_DRAW_CHECK_N, code))
        flex[kind] = {"drawn": FLEX_DRAW_CHECK_N, "completed": int(rep.get("completed") or 0), "rejected": int(rep.get("rejected") or 0),
                      "flex_is_drawn_position": bool((pc[out[:, 7]] == code).all()) if len(out) else False}
    boundary = 1.0 / V.MAX_RAW_FACTOR
    acc = rec1["acceptance_rate"] or 0.0
    checks = {"no_incomplete_rows": rec1["incomplete_rows"] == 0, "not_short": rec1["short"] is False and rec1["accepted"] == n,
              "acceptance_above_contract_boundary": acc > boundary, "acceptance_headroom_ratio_ge_1_5": acc / boundary >= 1.5,
              "slots_hold_legal_positions": slots_ok, "nine_distinct_players": distinct, "salary_never_over_cap": bool((used <= V.CAP).all()) if m else False,
              "players_from_at_least_two_games": two_games, "flex_obeys_drawn_position": all(v["flex_is_drawn_position"] for v in flex.values()),
              "deterministic_under_frozen_seed": bool(np.array_equal(idx1, idx2)) and rec1 == rec2, "different_seed_differs": not (len(idx3) == len(idx1[:len(idx3)]) and np.array_equal(idx3, idx1[:len(idx3)]))}
    flex_mix = {k: round(100.0 * float((pc[idx1[:, 7]] == c).mean()), 2) for k, c in (("RB", 1), ("WR", 2), ("TE", 3))} if m else {}
    punts = np.bincount(np.minimum(((sal[idx1] < 3000) & (pc[idx1] != 4)).sum(axis=1), 2), minlength=3) / m if m else np.zeros(3)
    out = {"meta": {"stage": "week-2 preflight of the frozen candidate on a pre-lock pool: structural checks only, no ownership read", "checked_utc": _utc(),
                    "board": {"path": os.path.relpath(board_path, ROOT), "sha256": _sha(board_path), "built_ts": board.get("built_ts"), "players": len(ents),
                              "reconstructed": bool(board.get("reconstructed"))},
                    "n": int(n), "seed": V.REAL_SEED, "verify": ver, "seconds_first_sample": round(t1 - t0, 1)},
           "receipt": rec1, "contract": {"max_raw_factor": V.MAX_RAW_FACTOR, "acceptance_boundary": boundary, "acceptance_rate": acc,
                                         "headroom_ratio": round(acc / boundary, 3) if boundary else None, "raw_draws_used_of_allowed": round(rec1["raw_draws"] / (V.MAX_RAW_FACTOR * n), 4),
                                         "week1_acceptance_for_comparison": 0.1159},
           "flex_draw_check": flex, "realised": {"flex_position_pct": flex_mix, "drawn_flex_shares_pct": [round(100 * x, 2) for x in p["flex_probs_rb_wr_te"]],
                                                 "salary": {"mean": round(float(used.mean()), 1), "share_at_cap_pct": round(100.0 * float((used == V.CAP).mean()), 2),
                                                            "share_ge_49500_pct": round(100.0 * float((used >= 49500).mean()), 2), "share_le_48000_pct": round(100.0 * float((used <= 48000).mean()), 2)} if m else None,
                                                 "punts_under_3000_pct": [round(100.0 * float(x), 2) for x in punts]},
           "sample_sha256": hashlib.sha256(np.ascontiguousarray(idx1).tobytes()).hexdigest() if m else None,
           "checks": checks, "passed": all(checks.values())}
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, f"preflight_{out['meta']['board']['sha256'][:12]}_{_stamp()}.json")
    with open(path, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    log(f"[W2] preflight on {out['meta']['board']['sha256'][:12]} ({len(ents)} players): accepted {rec1['accepted']:,} of {rec1['raw_draws']:,} raw "
        f"(acceptance {acc:.4f}, boundary {boundary}, headroom x{acc / boundary:.2f}); incomplete {rec1['incomplete_rows']}; "
        f"checks {'ALL PASS' if out['passed'] else 'FAILED: ' + ', '.join(k for k, v in checks.items() if not v)} -> {os.path.relpath(path, ROOT)}")
    if not out["passed"]:
        log("[W2] the frozen candidate could not meet its own computational contract on this pool; nothing is tuned, this is reported as a prospective failure")
    return out


# ---- 5. the archive: every pre-lock input worth having later --------------------------
def archive(log=print):
    """Future-study material only; none of it enters slot-allocation v1."""
    import kalshi_nfl
    from research import provenance
    caps = _captures()
    snaps = snapshots(fetch=False, log=lambda *_a, **_k: None)
    valid = [s for s in snaps if s["valid"]]
    latest = max(valid, key=lambda s: (s["built_ts"], -s["committed_ts"])) if valid else None
    board = json.loads(_git("show", f"{latest['commit_full']}:{SNAPSHOT_PATH}")) if latest else None
    try:
        kidx = kalshi_nfl.index()
        kal = {"fetched_utc": _utc(), "records": len(kidx) if hasattr(kidx, "__len__") else None, "index": _jsonable(kidx),
               "note": "tuple keys inside the ladder are joined with | so the archive is JSON; the live object is not reshaped"}
    except Exception as e:  # archival: a failed fetch is recorded, never raised
        kal = {"fetched_utc": _utc(), "error": f"{type(e).__name__}: {e}"}
    cap_rows = []
    for st, d, info in caps:
        slate = json.load(open(os.path.join(d, f"slate_{DG}.json")))
        header = slate["csv"].splitlines()[0] if slate.get("csv") else None
        cap_rows.append({"stamp": st, "path": os.path.relpath(d, ROOT), "captured_utc": info["captured_utc"], "hours_to_lock": info["hours_to_lock"], "pre_lock": info["pre_lock"],
                         "files_sha256": info["files"], "dk_slate": {"file": f"slate_{DG}.json", "csv_header": header, "n_players": slate.get("n_players"),
                                                                      "avg_points_per_game_column": bool(header and "AvgPointsPerGame" in header), "game_info_column": bool(header and "Game Info" in header)},
                         "sleeper_projections": [f for f in info["files"] if f.startswith("proj_")], "roster": f"players_2026_{WEEK}.json",
                         "contest_lobby_detail": {"file": f"contests_{DG}.json", "contests_detailed": info.get("contests_detailed")}})
    out = {"meta": {"stage": "week-2 pre-lock archive: every input the classic board consumed or a later study may want, hashed; not an input to slot-allocation v1",
                    "archived_utc": _utc(), "before_lock": time.time() < LOCK_TS, "provenance": provenance.stamp(worlds=None, model="archive; nothing computed", seed="UNSEEDED")},
           "prospective_captures": cap_rows, "served_snapshots": snaps,
           "latest_valid_served_board": ({"commit": latest["commit"], "sha256": latest["sha256"], "built_utc": latest["built_utc"], "committed_utc": latest["committed_utc"],
                                          "field_model": board.get("field_model"), "production_ownership_map_field_pct": {p["name"]: p.get("field_pct") for p in board["players"]},
                                          "players": len(board["players"]), "slate": board.get("slate"), "roster_source": board.get("roster_source"), "simulator": board.get("simulator"),
                                          "contests": board.get("contests"), "rules": board.get("rules"), "candidate_draws": board.get("candidate_draws"), "seed": board.get("seed"),
                                          "model": board.get("model"), "sims": board.get("sims")} if latest else None),
           "game_line_feed": {"what_the_classic_sim_consumes": "kalshi_nfl.index() (Kalshi's game ladders), read by nfl_dfs_sim's game model for totals; the field's proj is Sleeper's number and does not read it",
                              "kalshi_index_at_archive": kal}}
    os.makedirs(OUT, exist_ok=True)
    # gzipped: the Kalshi ladder alone is 2.6 MB of JSON, against a 7.2 MB pack
    # that Render clones on every deploy. Nothing is dropped to save the space.
    path = os.path.join(OUT, f"archive_{_stamp()}.json.gz")
    with gzip.open(path, "wt", compresslevel=9) as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    log(f"[W2] archived {len(cap_rows)} capture(s), {len(snaps)} snapshot(s), the latest valid board's field model and ownership map, the Kalshi index "
        f"({'ok' if 'index' in kal else kal.get('error')}) -> {os.path.relpath(path, ROOT)}")
    return out


# ---- 6. after the field: the one command --------------------------------------------
def pin_export(export, expected_sha, cid=CONTEST, log=print):
    """The owner's export, frozen by hash beside the other standings; the
    caller's expected sha256 must equal the ZIP's or the CSV's."""
    if export.lower().endswith(".zip"):
        zsha = _sha(export)
        with zipfile.ZipFile(export) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".csv")]
            if len(names) != 1:
                raise SystemExit(f"{export}: expected one CSV inside, found {names}")
            raw = z.read(names[0])
            mtime = "%04d-%02d-%02d %02d:%02d:%02d" % z.getinfo(names[0]).date_time
    else:
        zsha, raw, mtime = None, open(export, "rb").read(), None
    csha = hashlib.sha256(raw).hexdigest()
    if expected_sha.lower() not in (zsha, csha):
        raise SystemExit(f"REFUSED: the export's hashes (zip {zsha}, csv {csha}) do not include the expected {expected_sha}")
    man_path = os.path.join(C.DATA, "dk_standings", "manifest.json")
    man = json.load(open(man_path))
    key = f"contest-standings-{cid}.csv.gz"
    rows = raw.count(b"\n") - 1
    if key in man["files"]:
        if man["files"][key]["csv_sha256"] != csha:
            raise SystemExit(f"{key} is already pinned with a different csv sha256; a frozen export is never replaced")
        ent = man["files"][key]
    else:
        caps = _captures()
        detail = None
        for _st, d, _i in reversed(caps):
            _slate, details = s6_capture.replay(DG, capdir=d)
            if str(cid) in details:
                detail = details[str(cid)]
                break
        target = os.path.join(C.DATA, "dk_standings", key)
        if os.path.exists(target):
            raise SystemExit(f"{target} exists without a manifest entry; refusing to guess")
        with gzip.open(target, "wb", compresslevel=9) as fh:
            fh.write(raw)
        gz_bytes = os.path.getsize(target)
        # 3 MB: the three standings already committed are 1.6-2.3 MB gzipped and the
        # whole pack is 7.2 MB, which Render clones on every deploy. A bigger export
        # stays with the owner and is pinned by hash, as week 1's 28 MB one is.
        commit = gz_bytes <= 3 * 1024 * 1024
        ent = {"contest_id": int(cid), "kind": "classic", "committed": commit, "prospective": True, "frozen_utc": _utc(), "csv_sha256": csha, "csv_bytes": len(raw),
               "gz_sha256": _sha(target), "gz_bytes": gz_bytes, "zip_sha256": zsha, "csv_mtime_in_zip": mtime, "rows_excluding_header": rows,
               "contest_detail_at_freeze": ({k: detail.get(k) for k in ("draft_group_id", "entry_fee", "first_prize", "max_entries", "name", "places_paid", "prize_pool", "starts", "state")} if detail else None),
               "note": "week-2 held-out contest for slot-allocation v1: frozen after the game, scored by research.cl_week2 postgame against the pre-lock input receipt"}
        if not commit:
            ent["why_not_committed"] = f"{gz_bytes / 1e6:.1f} MB gzipped against a small repository Render clones on every deploy; the bytes stay with the owner's zip and are pinned here by hash"
            os.remove(target)
        man["files"][key] = ent
        with open(man_path, "w") as fh:
            json.dump(man, fh, indent=1, sort_keys=True)
    os.makedirs(STANDINGS_DIR, exist_ok=True)
    csv_path = os.path.join(STANDINGS_DIR, f"contest-standings-{cid}.csv")
    with open(csv_path, "wb") as fh:
        fh.write(raw)
    log(f"[W2] export pinned: {rows:,} rows, csv sha {csha[:12]}, zip sha {(zsha or '-')[:12]}; plain CSV at {os.path.relpath(csv_path, ROOT)} (gitignored)")
    return ent, csv_path


def support_report(std, ents):
    """Rows, blanks, malformed, active, fully representable, exclusions and
    the unsupported players behind them, roster-slot mass outside support."""
    pool = {e["name"] for e in ents}
    total = len(std["entries"])
    blank = sum(1 for e in std["entries"] if e["blank"])
    active = [e for e in std["entries"] if e["lineup"]]
    rep = [e for e in active if all(nm in pool for _s, nm in e["lineup"])]
    exc = [e for e in active if any(nm not in pool for _s, nm in e["lineup"])]
    causes = collections.Counter(nm for e in exc for _s, nm in e["lineup"] if nm not in pool)
    slot_out = collections.Counter(s for e in active for s, nm in e["lineup"] if nm not in pool)
    slot_all = collections.Counter(s for e in active for s, _nm in e["lineup"])
    return {"definition": "conditional on fully representable entries: a lineup with any player outside the served pool is omitted (the frozen real_from_export)",
            "contest_rows_excluding_header": total, "blank_entries": blank, "malformed_lineups": std["malformed"], "nonblank_active_entries": len(active),
            "fully_representable_entries": len(rep), "excluded_entries": len(exc), "excluded_share_of_active_pct": round(100.0 * len(exc) / len(active), 3) if active else None,
            "representable_share_of_active_pct": round(100.0 * len(rep) / len(active), 3) if active else None,
            "unsupported_players_by_exclusions": [{"name": n, "excluded_entries": c, "share_of_active_pct": round(100.0 * c / len(active), 3)} for n, c in causes.most_common(25)],
            "roster_slot_mass_outside_support_pct": {s: round(100.0 * slot_out.get(s, 0) / slot_all[s], 3) for s in sorted(slot_all)},
            "week1_representable_share_for_comparison_pct": 99.733}


def arms(ents, real, pool_g, seed, n):
    """Both arms exactly as research.cl_slot.score runs them, on `seed`. The
    primary result is score() itself; this exists for the secondary seeds
    and the timing arm, and is cross-checked against score() on REAL_SEED."""
    import dfs_tourney as T
    p = PARAMS
    sampler, _rc = V.slot_sampler()
    idx_c, rec_c = V.sample(ents, n, np.random.default_rng(seed), p["beta"], p["eta"], p["delta"], p["flex_probs_rb_wr_te"], sampler=sampler)
    rng = np.random.default_rng(seed)
    beta0, kappa0, _o, _m, _c, _t = T.calibrate_field(ents, rng, n=S.CAL_N)
    parts, done = [], 0
    while done < n:
        k = min(V.CHUNK, n - done)
        rep = {}
        idx = T.classic_sample(ents, k, rng, beta0, kappa0, report=rep)
        parts.append(idx)
        done += len(idx)
    idx_0 = np.concatenate(parts, axis=0)[:n]
    return {"seed": int(seed), "n": int(n),
            "current_family": {"beta": round(float(beta0), 6), "kappa": round(float(kappa0), 6), "graded": V.grade_field(idx_0, ents, real, pool_g)},
            "candidate": {"receipt": rec_c, "graded": V.grade_field(idx_c, ents, real, pool_g)}}


def _cp(g):
    c = g["graded"]["co_primary"]
    return {"ce": c["slot_cross_entropy"]["total"]["cross_entropy"], "kl": c["slot_cross_entropy"]["total"]["kl"], "w1": c["salary_wasserstein1"], "salary_mean": c["salary_mean"]}


def real_empirical(std, ents):
    """The real field's own structure on the same pool, by the first
    artifact's function, called exactly as week 1 called it (every row,
    blanks included, and the per-entry user counts as a list), so the Real
    column is computed identically on both slates."""
    return C.empirical([e["lineup"] for e in std["entries"]], S.grading_pool(ents), n_all=len(std["entries"]),
                       user_entries=[e["user_entries"] for e in std["entries"]])


def _stats(xs):
    xs = [float(x) for x in xs]
    return {"mean": round(float(np.mean(xs)), 5), "sd": round(float(np.std(xs, ddof=1)), 5) if len(xs) > 1 else None, "min": round(min(xs), 5), "max": round(max(xs), 5), "n": len(xs)}


def postgame(export, expected_sha, cid=CONTEST, log=print):
    from research import provenance
    t0 = time.time()
    ver = verify(log)
    if not os.path.exists(RECEIPT):
        raise SystemExit("no final input receipt: run `select --final` (before lock) first")
    rec = json.load(open(RECEIPT))
    sel = rec["selected"]
    board_path = os.path.join(ROOT, sel["path"])
    if _sha(board_path) != sel["sha256"]:
        raise SystemExit(f"{sel['path']} does not hash to the receipt's {sel['sha256'][:12]}; the pinned input moved")
    for k, want in (("frozen_file_sha256", ver["frozen_file_sha256"]), ("candidate_source_sha256", ver["cl_slot_file_sha256"]), ("production_classic_sample_sha256", ver["classic_sample_sha256"])):
        if rec[k] != want:
            raise SystemExit(f"the receipt's {k} {rec[k][:12]} is not the tree's {want[:12]}: refused")
    if float(sel["built_ts"]) >= LOCK_TS or _ts(sel["fetched_utc"]) >= LOCK_TS:
        raise SystemExit("the receipt's input was built or fetched at or after lock: refused")
    ent, csv_path = pin_export(export, expected_sha, cid, log)
    det = ent.get("contest_detail_at_freeze") or {}
    if det and int(det.get("draft_group_id") or 0) != DG:
        raise SystemExit(f"contest {cid} belongs to draft group {det.get('draft_group_id')}, not {DG}: refused")
    board = json.load(open(board_path))
    ents = S.served_pool(board)
    pool_g = S.grading_pool(ents)
    pool_slate = {e["name"]: {"salary": e["salary"]} for e in ents}
    std = C.load_standings(csv_path, ent["csv_sha256"])
    support = support_report(std, ents)
    log(f"[W2] support: {support['contest_rows_excluding_header']:,} rows, {support['nonblank_active_entries']:,} active, "
        f"{support['fully_representable_entries']:,} representable ({support['representable_share_of_active_pct']}%)")
    # the primary: the frozen scorer itself
    primary = V.score(board_path, sel["fetched_utc"], csv_path, DG, LOCK_UTC, log=log)
    real = V.real_from_export(csv_path, ents, pool_slate)
    n = real["active_lineups"]
    if n != primary["real"]["active_lineups"] or n != support["fully_representable_entries"]:
        raise SystemExit("the representable count disagrees between the frozen loader, the scorer and the support report")
    # the cross-check and the secondary seeds
    cross = arms(ents, real, pool_g, V.REAL_SEED, n)
    cross_ok = (_cp(cross["candidate"]) == _cp(primary["candidate"]) and _cp(cross["current_family"]) == _cp(primary["current_family"]))
    if not cross_ok:
        raise SystemExit("the wrapper's arms on the primary seed do not reproduce research.cl_slot.score's co-primaries exactly: refused")
    secondary = []
    for sd in SECONDARY_SEEDS:
        a = arms(ents, real, pool_g, sd, n)
        secondary.append({"seed": sd, "current_family": _cp(a["current_family"]), "candidate": _cp(a["candidate"]),
                          "candidate_receipt": a["candidate"]["receipt"], "current_calibration": {"beta": a["current_family"]["beta"], "kappa": a["current_family"]["kappa"]}})
        log(f"[W2] seed {sd}: CE {secondary[-1]['current_family']['ce']} -> {secondary[-1]['candidate']['ce']}; W1 ${secondary[-1]['current_family']['w1']} -> ${secondary[-1]['candidate']['w1']}")
    d_ce = [s["candidate"]["ce"] - s["current_family"]["ce"] for s in secondary]
    d_w1 = [s["candidate"]["w1"] - s["current_family"]["w1"] for s in secondary]
    # the timing arm: the early capture, reconstructed offline
    timing = None
    ta = rec.get("timing_sensitivity_arm")
    if ta:
        arch = sorted(f for f in os.listdir(OUT) if f.startswith("archive_") and f.endswith(".json.gz"))
        kidx = None
        if arch:
            with gzip.open(os.path.join(OUT, arch[-1]), "rt") as fh:
                kidx = (json.load(fh).get("game_line_feed") or {}).get("kalshi_index_at_archive", {}).get("index")
        board_t = reconstruct(ta["capture_stamp"], log=log, kalshi_index=kidx)
        ents_t = S.served_pool(board_t)
        real_t = V.real_from_export(csv_path, ents_t, {e["name"]: {"salary": e["salary"]} for e in ents_t})
        a_t = arms(ents_t, real_t, S.grading_pool(ents_t), V.REAL_SEED, real_t["active_lineups"])
        timing = {"role": "input-timing sensitivity only; never decides", "capture_stamp": ta["capture_stamp"], "captured_utc": ta["captured_utc"], "hours_to_lock": ta["hours_to_lock"],
                  "pool": {"players": len(ents_t), "reconstructed": True}, "support": support_report(std, ents_t), "pool_differences_vs_primary": compare_pools(board, board_t, "primary", "early_capture"),
                  "current_family": _cp(a_t["current_family"]), "candidate": _cp(a_t["candidate"]), "candidate_receipt": a_t["candidate"]["receipt"],
                  "note": "the co-primaries here condition on entries representable on THIS pool, which is not the primary's support when the pools differ; the difference is stamped, not hidden"}
    cp0, cp1 = _cp(primary["current_family"]), _cp(primary["candidate"])
    verdict = VERDICTS[(cp1["ce"] < cp0["ce"], cp1["w1"] < cp0["w1"])]
    remp = real_empirical(std, ents)
    out = {"meta": {"stage": "week 2 post-game: slot-allocation v1 against the untouched current family, the frozen protocol, one command", "finished_utc": _utc(),
                    "seconds": round(time.time() - t0, 1), "contest": int(cid), "draft_group": DG, "lock_utc": LOCK_UTC, "verify": ver, "receipt": os.path.relpath(RECEIPT, ROOT),
                    "input": sel, "export": ent, "no_live_fetch": True, "provenance": provenance.stamp(worlds=n, model="frozen candidate and current family; nothing fitted", seed={"primary": V.REAL_SEED, "secondary": list(SECONDARY_SEEDS)})},
           "verdict": {"text": verdict, "rule": "mechanical from the primary seed's two co-primaries; an exact tie counts as not lower; no combined score; secondary seeds and the timing arm never decide",
                       "candidate_lower_ce": cp1["ce"] < cp0["ce"], "candidate_lower_w1": cp1["w1"] < cp0["w1"]},
           "primary": {"seed": V.REAL_SEED, "current_family": cp0, "candidate": cp1, "delta_candidate_minus_current": {"ce": round(cp1["ce"] - cp0["ce"], 5), "w1": round(cp1["w1"] - cp0["w1"], 1)},
                       "artifact": f"research/data/cl_slot_week2_{DG}.json", "cross_check_wrapper_reproduces_score": cross_ok},
           "secondary_seeds": {"seeds": list(SECONDARY_SEEDS), "rows": secondary, "delta_ce": _stats(d_ce), "delta_w1": _stats(d_w1),
                               "reads": "stochastic sensitivity of the comparison; not five validations, not a seed choice"},
           "timing_sensitivity": timing, "support": support, "real_empirical": remp}
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "postgame.json")
    if os.path.exists(path):
        raise SystemExit(f"{path} exists; a scored run is never overwritten")
    with open(path, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    text = render(context(out, primary))
    with open(REPORT, "w") as fh:
        fh.write(text)
    log(f"[W2] VERDICT: {verdict}. CE {cp0['ce']} -> {cp1['ce']}; W1 ${cp0['w1']} -> ${cp1['w1']}; secondary dCE {out['secondary_seeds']['delta_ce']}; "
        f"written {os.path.relpath(path, ROOT)} and {os.path.relpath(REPORT, ROOT)} in {out['meta']['seconds']}s")
    return out


# ---- 7. the report: the template's cells and nothing else ------------------------------
def template_keys(text=None):
    text = text if text is not None else open(TEMPLATE).read()
    return set(re.findall(r"\{\{([a-z0-9_]+)\}\}", text))


def render(ctx, text=None):
    text = text if text is not None else open(TEMPLATE).read()
    keys = template_keys(text)
    if set(ctx) != keys:
        raise SystemExit(f"render refused: the context's keys are not the template's (missing {sorted(keys - set(ctx))}, extra {sorted(set(ctx) - keys)})")
    return re.sub(r"\{\{([a-z0-9_]+)\}\}", lambda m: str(ctx[m.group(1)]), text)


def _f(x, nd=3):
    return "n/a" if x is None else (f"{x:,.{nd}f}" if isinstance(x, float) else f"{x:,}" if isinstance(x, int) else str(x))


def context(out, primary):
    g0, g1 = primary["current_family"]["graded"], primary["candidate"]["graded"]
    e0, e1, er = g0["out_of_objective"]["empirical"], g1["out_of_objective"]["empirical"], out["real_empirical"]
    v0, v1 = g0["out_of_objective"]["vs_real"], g1["out_of_objective"]["vs_real"]
    dg0, dg1 = g0["out_of_objective"]["diagnostics"], g1["out_of_objective"]["diagnostics"]
    ce0, ce1 = g0["co_primary"]["slot_cross_entropy"], g1["co_primary"]["slot_cross_entropy"]
    sel, sup, sec, tim = out["meta"]["input"], out["support"], out["secondary_seeds"], out["timing_sensitivity"]
    slot_rows = "\n".join(f"| {gname} | {_f(ce0[gname]['cross_entropy'], 5)} (KL {_f(ce0[gname]['kl'], 5)}) | {_f(ce1[gname]['cross_entropy'], 5)} (KL {_f(ce1[gname]['kl'], 5)}) | "
                          f"{_f(ce0[gname]['real_entropy'], 5)} | {ce0[gname]['players_with_real_mass']} |" for gname in GROUPS)
    seed_rows = "\n".join(f"| {r['seed']} | {_f(r['current_family']['ce'], 5)} | {_f(r['candidate']['ce'], 5)} | {_f(r['candidate']['ce'] - r['current_family']['ce'], 5)} | "
                          f"${_f(r['current_family']['w1'], 1)} | ${_f(r['candidate']['w1'], 1)} | ${_f(r['candidate']['w1'] - r['current_family']['w1'], 1)} |" for r in sec["rows"])
    real_own = out["real_empirical"]["ownership_any_slot_pct"]
    o0, o1 = g0["ownership_any_slot_pct"], g1["ownership_any_slot_pct"]
    ents = {}
    board = json.load(open(os.path.join(ROOT, sel["path"])))
    for p in board["players"]:
        ents[p["name"]] = p
    top = sorted(real_own.items(), key=lambda kv: -kv[1])[:50]
    top50_rows = "\n".join(f"| {i + 1} | {nm} | {ents.get(nm, {}).get('pos', '?')} | ${ents.get(nm, {}).get('salary', 0):,} | {_f(pct, 2)} | {_f(o0.get(nm, 0.0), 2)} | {_f(o1.get(nm, 0.0), 2)} |"
                           for i, (nm, pct) in enumerate(top))

    def sal(e):
        s = e["salary_used"]
        return f"${_f(s['mean'], 1)}", " / ".join(f"${int(q):,}" for q in [s["p10_p25_p75_p90"][0], s["p10_p25_p75_p90"][1], s["median"], s["p10_p25_p75_p90"][2], s["p10_p25_p75_p90"][3]]), \
            f"{_f(s['share_at_cap_pct'], 2)}% / {_f(s['share_ge_49500_pct'], 2)}% / {_f(s['share_le_48000_pct'], 2)}%"

    s0, s1, sr = sal(e0), sal(e1), sal(er)

    def stk(e):
        return " / ".join(_f(x, 2) for x in e["stack_pct"]["0_1_2_3plus"])

    def cap(d, k):
        c = d["real_mass_captured_by_samplers_top"][k]
        return f"{_f(c['capture_ratio'], 4)} ({c['overlap_players']} overlap)"

    def dup(e):
        return " / ".join(f"{_f(e['duplicates'][b]['entry_share_pct'], 2)}%" for b in ("1-1", "2-2", "3-5", "6-10", "11-50", "51-plus"))

    def hhi(e):
        c = e["collision"]
        return f"{c['sum_p2']:.3e}; {_f(c['effective_lineups'], 1)}"

    ctx = {"verdict": out["verdict"]["text"],
           "cur_ce": _f(ce0["total"]["cross_entropy"], 5), "cur_kl": _f(ce0["total"]["kl"], 5), "cand_ce": _f(ce1["total"]["cross_entropy"], 5), "cand_kl": _f(ce1["total"]["kl"], 5),
           "real_entropy": _f(ce0["total"]["real_entropy"], 5), "cur_w1": _f(g0["co_primary"]["salary_wasserstein1"], 1), "cand_w1": _f(g1["co_primary"]["salary_wasserstein1"], 1),
           "d_ce": _f(out["primary"]["delta_candidate_minus_current"]["ce"], 5), "d_w1": _f(out["primary"]["delta_candidate_minus_current"]["w1"], 1),
           "slot_rows": slot_rows,
           "cur_sal_mean": s0[0], "cand_sal_mean": s1[0], "real_sal_mean": sr[0], "cur_sal_q": s0[1], "cand_sal_q": s1[1], "real_sal_q": sr[1],
           "cur_sal_shares": s0[2], "cand_sal_shares": s1[2], "real_sal_shares": sr[2],
           "rows_total": _f(sup["contest_rows_excluding_header"]), "rows_blank": _f(sup["blank_entries"]), "rows_malformed": _f(sup["malformed_lineups"]),
           "rows_active": _f(sup["nonblank_active_entries"]), "rows_representable": _f(sup["fully_representable_entries"]), "rows_excluded": _f(sup["excluded_entries"]),
           "rows_excluded_pct": _f(sup["excluded_share_of_active_pct"], 3), "week1_representable_pct": _f(sup["week1_representable_share_for_comparison_pct"], 3),
           "unsupported_players": "; ".join(f"{r['name']} ({r['excluded_entries']:,}, {_f(r['share_of_active_pct'], 2)}%)" for r in sup["unsupported_players_by_exclusions"][:10]) or "none",
           "slot_mass_outside": "; ".join(f"{s} {_f(v, 3)}%" for s, v in sup["roster_slot_mass_outside_support_pct"].items()),
           "primary_kind": sel["kind"], "built_utc": sel["built_utc"], "fetched_utc": sel["fetched_utc"], "minutes_before_lock": _f(sel["minutes_before_lock"], 1),
           "board_sha": sel["sha256"], "board_commit": (sel["source"].get("commit") or sel["source"].get("capture_stamp") or "")[:12], "pool_n": _f(sel["pool_after_served_pool"]),
           "frozen_sha": out["meta"]["verify"]["frozen_file_sha256"][:12], "cl_slot_sha": out["meta"]["verify"]["cl_slot_file_sha256"][:12], "classic_sample_sha": out["meta"]["verify"]["classic_sample_sha256"][:12],
           "selection_rule": json.load(open(RECEIPT))["meta"]["selection_rule"],
           "seed_rows": seed_rows, "dce_stats": f"{_f(sec['delta_ce']['mean'], 5)} / {_f(sec['delta_ce']['sd'], 5)} / [{_f(sec['delta_ce']['min'], 5)}, {_f(sec['delta_ce']['max'], 5)}]",
           "dw1_stats": f"${_f(sec['delta_w1']['mean'], 1)} / ${_f(sec['delta_w1']['sd'], 1)} / [${_f(sec['delta_w1']['min'], 1)}, ${_f(sec['delta_w1']['max'], 1)}]",
           "crosscheck": "yes" if out["primary"]["cross_check_wrapper_reproduces_score"] else "NO",
           "t_cur_ce": _f(tim["current_family"]["ce"], 5) if tim else "n/a", "t_cand_ce": _f(tim["candidate"]["ce"], 5) if tim else "n/a",
           "t_cur_w1": _f(tim["current_family"]["w1"], 1) if tim else "n/a", "t_cand_w1": _f(tim["candidate"]["w1"], 1) if tim else "n/a",
           "timing_pool_note": (f"reconstructed from the capture of {tim['captured_utc']} ({tim['hours_to_lock']} h before lock), {tim['pool']['players']} players" if tim else "no timing arm"),
           "timing_support_diff": ((lambda d: f"only in the primary {len(d['only_in_primary'])}, only in the early capture {len(d['only_in_early_capture'])}, salary mismatches {len(d['mismatches']['salary'])}, "
                                              f"projections differing by 1+ points {len(d['projection']['abs_diff_ge_1'])} of {d['projection']['players_compared']} (max {d['projection']['max_abs_diff']}), "
                                              f"depth tags differing {len(d['depth_tag_differs'])}, field-only flags differing {len(d['field_only_differs'])}; identical pools: {d['identical_pools']}")
                                   (tim["pool_differences_vs_primary"]) if tim else "n/a"),
           "timing_representable": (f"{tim['support']['fully_representable_entries']:,} ({_f(tim['support']['representable_share_of_active_pct'], 3)}%) against the primary's {sup['fully_representable_entries']:,}" if tim else "n/a"),
           "top20": f"{_f(v0['top20_by_real']['mae_pp'], 3)} / {_f(v0['top20_by_real']['bias_pp'], 3)} | {_f(v1['top20_by_real']['mae_pp'], 3)} / {_f(v1['top20_by_real']['bias_pp'], 3)}",
           "top50_rho": f"{_f(v0['top50_by_real']['spearman'], 4)} | {_f(v1['top50_by_real']['spearman'], 4)}",
           "overlap": f"{dg0['real_mass_captured_by_samplers_top']['top10']['overlap_players']} / {dg0['real_mass_captured_by_samplers_top']['top20']['overlap_players']} / {dg0['real_mass_captured_by_samplers_top']['top50']['overlap_players']} | "
                      f"{dg1['real_mass_captured_by_samplers_top']['top10']['overlap_players']} / {dg1['real_mass_captured_by_samplers_top']['top20']['overlap_players']} / {dg1['real_mass_captured_by_samplers_top']['top50']['overlap_players']}",
           "mass": f"{cap(dg0, 'top10')} / {cap(dg0, 'top20')} / {cap(dg0, 'top50')} | {cap(dg1, 'top10')} / {cap(dg1, 'top20')} / {cap(dg1, 'top50')}",
           "flex": " | ".join(" / ".join(_f(e["flex_position_pct"].get(k, 0.0), 2) for k in ("RB", "WR", "TE")) for e in (e0, e1, er)),
           "punts": " | ".join(" / ".join(_f(e["punts_under_3000_pct"].get(k, 0.0), 2) for k in ("0", "1", "2")) for e in (e0, e1, er)),
           "stacks": " | ".join(stk(e) for e in (e0, e1, er)),
           "bring": " | ".join(_f(e["bring_back_pct"]["all_entries"], 2) for e in (e0, e1, er)),
           "dst": " | ".join(f"{_f(e['dst_vs_own_qb_pct']['real'], 3)} / {_f(e['dst_vs_own_rb_pct'], 3)}" for e in (e0, e1, er)),
           "unique": " | ".join(f"{_f(100.0 * e['distinct_lineups'] / e['active_entries'], 2)}%; {e['max_lineup_copies']}" for e in (e0, e1, er)),
           "collision": " | ".join(f"{e['collision']['distinct_pairs']:.3e}" for e in (e0, e1, er)),
           "hhi": " | ".join(hhi(e) for e in (e0, e1, er)),
           "dups": " | ".join(dup(e) for e in (e0, e1, er)),
           "top50_rows": top50_rows}
    return ctx


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "verify":
        verify()
    elif a and a[0] == "candidates":
        snapshots(fetch="--offline" not in a)
    elif a and a[0] == "select":
        select(final="--final" in a, fetch="--offline" not in a)
    elif a and a[0] == "reconstruct" and len(a) >= 2:
        b = reconstruct(a[1])
        if "--compare" in a:
            other = json.load(open(a[a.index("--compare") + 1]))
            cmp_ = compare_pools(other, b)
            path = os.path.join(OUT, f"reconstruction_vs_served_{a[1]}.json")
            with open(path, "w") as fh:
                json.dump({"served": {"path": os.path.relpath(a[a.index('--compare') + 1], ROOT), "sha256": _sha(a[a.index("--compare") + 1])},
                           "reconstructed": {"capture_stamp": a[1]}, "comparison": cmp_, "compared_utc": _utc()}, fh, indent=1, sort_keys=True)
            print(f"[W2] comparison: {json.dumps({k: v for k, v in cmp_.items() if k != 'projection'}, sort_keys=True)[:600]}; projection {json.dumps({k: v for k, v in cmp_['projection'].items() if k != 'abs_diff_ge_1'})}; -> {os.path.relpath(path, ROOT)}")
    elif a and a[0] == "preflight" and len(a) >= 2:
        out = preflight(a[1], n=int(a[2]) if len(a) > 2 and not a[2].startswith("--") else PREFLIGHT_N)
        sys.exit(0 if out["passed"] else 1)
    elif a and a[0] == "archive":
        archive()
    elif a and a[0] == "postgame" and len(a) >= 4 and "--sha256" in a:
        cid = int(a[a.index("--contest") + 1]) if "--contest" in a else CONTEST
        postgame(a[1], a[a.index("--sha256") + 1], cid=cid)
    else:
        raise SystemExit(__doc__.split("Input selection")[0])
