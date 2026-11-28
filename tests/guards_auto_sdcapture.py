"""Guards for the showdown captures, the eligibility selector and the rare-outcome counts (2026-11-27; sd_capture.py,
research/reports/dfs_eligibility.md section 10).

    python3 tests/guards_auto_sdcapture.py      # exits 1 on any failed check

SC2 is the exact-replay test: a probe build that reads the network (one URL twice, a 429 then a 200), a deep-store
file and the clock, writes the deep store and draws seeded worlds is captured by sd_capture's own child, the live
store and the network are then changed / taken away, and the replay must reproduce the board bit for bit.
Hermetic: the network is a stub, every store a scratch directory.
"""
import inspect
import json
import os
import subprocess
import sys
import tempfile
import time

os.environ.setdefault("VIGIL_NO_BG", "1")
_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "research"))

_PROBE = r'''
import gzip, io, json, os, pickle, sys, time, urllib.error, urllib.request
sys.path.insert(0, ROOT)
os.environ["DEEP_CACHE_DIR"] = REAL_DEEP
import sd_capture
import numpy as np

def probe(dg, out):
    """A stand-in for the recorder: every kind of input a board build reads."""
    import deep_cache
    os.makedirs(out, exist_ok=True)
    def get(u):
        for _ in range(3):
            try:
                with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "probe"})) as r:
                    return json.loads(r.read().decode())
            except urllib.error.HTTPError:
                continue
        raise RuntimeError("no answer")
    proj = get("https://example.invalid/projections")
    dk = get("https://example.invalid/draftables")
    prior, _ts = deep_cache.load("probe_prior")
    deep_cache.save("probe_written", {"k": prior["level"] * 2})
    again, _ts = deep_cache.load("probe_written")
    clock_ok = time.time() < LOCK
    rng = np.random.default_rng(int(dg) + int(proj["seed_bump"]))
    arr = (rng.standard_normal(500) * prior["level"]).astype(np.float32)
    board = {"players": dk["players"], "level": again["k"], "clock_ok": clock_ok, "built_ts": time.time(),
             "excluded": [], "slate": {"week": 12}}
    json.dump(board, open(os.path.join(out, "board.json"), "w"))
    json.dump(dk, open(os.path.join(out, "dk_players.json"), "w"))
    json.dump({"id": 1}, open(os.path.join(out, "contest.json"), "w"))
    json.dump({"ts": time.time(), "rows": proj}, gzip.open(os.path.join(out, "sleeper_proj.json.gz"), "wt"))
    json.dump({"ts": time.time(), "hist": {"1": prior}}, gzip.open(os.path.join(out, "usage_hist.json.gz"), "wt"))
    pickle.dump({"P1": {"proj": 5.0, "arr": arr}}, open(os.path.join(out, "pool_engine.pkl"), "wb"))
    return {"ok": True}

NR = sd_capture._netreplay()
if MODE == "capture":
    seq = {"https://example.invalid/projections": [429, 200], "https://example.invalid/draftables": [200]}
    class _R(io.BytesIO):
        status = 200
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *a): return False
    def fake(req, *a, **k):
        u = req.full_url
        code = seq[u].pop(0) if seq[u] else 200
        if code != 200:
            raise urllib.error.HTTPError(u, code, "slow down", {}, io.BytesIO(b""))
        body = {"https://example.invalid/projections": {"seed_bump": 7, "p": [1, 2, 3]},
                "https://example.invalid/draftables": {"players": ["A", "B"]}}[u]
        return _R(json.dumps(body).encode())
    NR._REAL = fake
    sd_capture.child_capture(157000, SNAP, LOCK, builder=probe)
else:
    sd_capture.child_replay(157000, SNAP, builder=probe)
'''


def _run(mode, snap, real_deep, lock):
    code = (f"ROOT = {_ROOT!r}\nREAL_DEEP = {real_deep!r}\nSNAP = {snap!r}\nLOCK = {lock!r}\nMODE = {mode!r}\n"
            + _PROBE)
    env = dict(os.environ, VIGIL_NO_BG="1")
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300, env=env)


def run(ck):
    import deep_cache
    import nfl_dfs
    import sd_capture as S
    # ---- SC1: when a capture is owed ----
    now = 1_800_000_000.0

    def sl(dg, hours, games=1, kind=96):
        import datetime
        t = datetime.datetime.fromtimestamp(now + hours * 3600, datetime.timezone.utc)
        return {"draft_group_id": dg, "games": games, "contest_type": kind,
                "starts": t.strftime("%Y-%m-%dT%H:%M:%S.0000000Z")}
    slates = [sl(1, 7), sl(2, 5), sl(3, 0.5), sl(4, 0.1), sl(5, 5, games=10, kind=21), sl(6, 3)]
    got = S.due(slates, now, done={(6, "lead")})
    ck("SC1 a capture is owed only inside its windows: the lead look 6 h to 2 h before lock, the close look 50 to 15 "
       "minutes before (after the inactives), never within 10 minutes of lock, never for a classic slate, never "
       "twice under one label",
       [(d, lab) for d, lab, _ in got] == [(3, "close"), (2, "lead")] and S.MIN_LEAD_S == 600
       and S.LEAD_S == 6 * 3600 and S.CLOSE_S == 50 * 60 and nfl_dfs.iso_ts(slates[0]["starts"]) == now + 7 * 3600,
       str(got))
    # ---- SC2: exact replay ----
    with tempfile.TemporaryDirectory() as d:
        real = os.path.join(d, "real_deep")
        snap = os.path.join(d, "snap")
        os.makedirs(real)
        os.makedirs(snap)
        saved = deep_cache.CACHE_DIR
        try:
            deep_cache.CACHE_DIR = real
            deep_cache.save("probe_prior", {"level": 3.0})
        finally:
            deep_cache.CACHE_DIR = saved
        lock = time.time() + 3600
        c1 = _run("capture", snap, real, lock)
        # the live store moves and the network is gone: the replay may read neither
        deep_cache.CACHE_DIR = real
        try:
            deep_cache.save("probe_prior", {"level": 99.0})
        finally:
            deep_cache.CACHE_DIR = saved
        c2 = _run("replay", snap, real, lock)
        verdict = S.compare(snap) if c1.returncode == 0 and c2.returncode == 0 else None
        rep = json.load(open(os.path.join(snap, "replay.json"))) if os.path.exists(os.path.join(snap, "replay.json")) else {}
        cap = json.load(open(os.path.join(snap, "capture.json"))) if os.path.exists(os.path.join(snap, "capture.json")) else {}
        b = json.load(open(os.path.join(snap, "replay", "board.json"))) if os.path.exists(os.path.join(snap, "replay", "board.json")) else {}
        ck("SC2 exact replay: a build captured by sd_capture's child (the network recorded in order, a 429 then a 200; "
           "the deep file it read copied as first read; its write kept in the snapshot) replays with the network "
           "forbidden, the live deep store changed and the clock moved, and comes back bit for bit (the board, every "
           "world): no request missed, the snapshot's deep file read, not the live one, the capture's clock",
           verdict == "exact" and (rep.get("counts") or {}).get("miss") == 0 and b.get("level") == 6.0
           and b.get("clock_ok") is True and cap.get("net_requests") == 3
           and os.path.exists(os.path.join(snap, "deep_orig", "probe_prior.pkl"))
           and not os.path.exists(os.path.join(real, "probe_written.pkl")),
           f"{verdict} {rep} {cap.get('net_requests')} {c1.stderr[-400:]} {c2.stderr[-400:]}")
        man = S.manifest_files(snap)
        ck("SC2b the manifest lists every file of the snapshot by sha256 (the recording, the deep files, the board)",
           any(k.startswith("net/") for k in man) and "rec/board.json" in man and "deep_orig/probe_prior.pkl" in man
           and all(len(v) == 64 for v in man.values()), str(list(man)[:6]))
    ck("SC3 a snapshot is refused when it ended, or holds a network answer that arrived, at or after lock (in-game "
       "information must never reach a pre-game number); the recorder itself refuses a locked slate",
       S.post_lock({"ended_ts": 100.0, "net_last_ts": 90.0}, 100.0) and S.post_lock({"ended_ts": 50.0,
                                                                                     "net_last_ts": 120.0}, 100.0)
       and not S.post_lock({"ended_ts": 99.0, "net_last_ts": 98.0}, 100.0)
       and "the slate has locked" in open(os.path.join(_ROOT, "research", "dfs_eligibility_record.py")).read()
       and "too close to lock" in inspect.getsource(S.child_capture))
    with tempfile.TemporaryDirectory() as d:
        for i in range(4):
            p = os.path.join(d, f"s{i}")
            os.makedirs(p)
            open(os.path.join(p, "x"), "wb").write(b"0" * 1000)
            os.utime(p, (1000 + i, 1000 + i))
        gone = S.prune(d, keep=2, max_gb=1.0)
        ck("SC4 the store is bounded (MAX_KEEP snapshots, MAX_GB): the oldest go first", sorted(gone) == ["s0", "s1"]
           and sorted(os.listdir(d)) == ["s2", "s3"] and S.MAX_KEEP == 40 and S.MAX_GB == 8.0, str(gone))
    pw = open(os.path.join(_ROOT, "pc_worker.py")).read()
    app = open(os.path.join(_ROOT, "app.py")).read()
    wf = open(os.path.join(_ROOT, ".github", "workflows", "error-log.yml")).read()
    ck("SC5 the PC runs the captures in its cycle between builds (first, and again after the classic board), can be "
       "switched off, sends a short summary through the token door, the server keeps it and the error-log workflow "
       "pulls it to sim-history; the snapshots live under pc-simcache (gitignored)",
       '("showdown captures", lambda: _task_sd_capture(url, tok))' in pw
       and '("showdown captures (2)", lambda: _task_sd_capture(url, tok))' in pw
       and '_cfg_value("sd_capture") is False' in pw and '"/api/pc/captures"' in pw
       and '@app.route("/api/pc/captures", methods=["GET", "POST"])' in app and "sd-captures:/api/pc/captures" in wf
       and os.path.basename(S.root()) == "sd-captures")
    # ---- SC6: the selector (the owner approved it; conservative stays the default) ----
    import pc_worker as P
    p1 = nfl_dfs.pick_parse({"mode": "adaptive"})
    try:
        nfl_dfs.pick_parse({"mode": "loose"})
        bad = False
    except ValueError:
        bad = True
    m, rest = P._pick_mode(p1)
    m2, rest2 = P._pick_mode(nfl_dfs.pick_parse({"mode": "unrestricted", "exclude": [{"name": "A B", "team": "X"}]}))
    sched = pw.replace(inspect.getsource(P._picked_builds), "")
    js = open(os.path.join(_ROOT, "static", "dfspick.js")).read()
    ck("SC6 the eligibility selector: a mode rides the pick, so a Build with one is always the picked board and never "
       "a scheduled one; conservative (or no mode) is the absence of the key and parses as before; a typo is refused; "
       "the PC passes a mode ONLY on the picked path (no scheduled build passes one); the sheet refuses a mode; the "
       "tab labels both experimental modes and the production default",
       p1 == {"exclude": [], "force": [], "captain": None, "mode": "adaptive"}
       and nfl_dfs.pick_parse({"mode": "conservative"}) is None and nfl_dfs.pick_parse({}) is None and bad
       and nfl_dfs.pick_dump(p1).get("mode") == "adaptive" and (m, rest) == ("adaptive", None)
       and m2 == "unrestricted" and rest2 and rest2.get("exclude") and "mode" not in rest2
       and inspect.getsource(P._picked_builds).count("eligibility=elig") == 3 and "eligibility=" not in sched
       and "an eligibility mode is for Build on the PC" in app
       and '["", "Conservative (production default)"], ["adaptive", "Adaptive (experimental)"]' in js
       and '["unrestricted", "Unrestricted (experimental)"]' in js and 'const mode = ctx === "pc" && s.mode' in js
       and "🧪 experimental" in js, f"{p1} {m} {rest}")
    # ---- SC7: the rare-outcome counts, and the scoring pass untouched ----
    import dfs_tourney as T
    cp = 100 * (1 - 0.025 ** (1 / 60000))
    rc = T.rare_counts(0.0, 0.0, 0.002, 60000, True, best_worlds=0, best_n=60000, best_what="x")
    rc2 = T.rare_counts(0.0004, 0.0006, 0.01, 30000, False, best_worlds=37, best_n=20000, best_what="y")
    src_sd, src_cl = inspect.getsource(T.build_nfl_showdown), inspect.getsource(T.build_nfl_classic)
    appjs = open(os.path.join(_ROOT, "static", "app.js")).read()
    seg = appjs[appjs.index("function dfsRareTag(r, kind)"):appjs.index("function dfsEligTag(p)")]
    ck("SC7 rare figures carry their simulation, two things kept apart (the owner: \"distinguish between the number "
       "of simulated games where a lineup was best and its duplication-adjusted probability of winning outright\"): "
       "`best` is an integer COUNT of worlds where the lineup scored the most (showdown: of every legal lineup, the "
       "field's whole support; classic: the best possible lineup on the generation worlds) with its own worlds; "
       "`win` is the board's duplication-adjusted outright first (win_pct), its worlds' worth a sum of probabilities "
       "with and without ties; top 0.1% beside; the exact Clopper-Pearson 95% upper bound 1 - 0.025^(1/n) where "
       "either is 0; both formats' rows carry it and the tab prints both, labelled and escaped",
       rc["best"]["count"] == 0 and abs(rc["best"]["cp95_upper_pct"] - round(cp, 4)) < 1e-9
       and rc["win"]["worlds_worth"] == 0.0 and abs(rc["win"]["cp95_upper_pct"] - round(cp, 4)) < 1e-9
       and rc["top01"]["worlds_worth"] == 120.0 and "cp95_upper_pct" not in rc["top01"]
       and "weighted field" in rc["win"]["what"] and "duplication-adjusted" in rc["win"]["what"]
       and rc2["best"] == {"count": 37, "of_worlds": 20000, "pct": 0.185, "what": "y"}
       and rc2["win"]["worlds_worth"] == 12.0 and rc2["win"]["with_ties_worlds_worth"] == 18.0
       and rc2["win"]["pct"] == 0.04 and "cp95_upper_pct" not in rc2["win"]
       and 'best_worlds=res["opt"][i], best_n=res["n_worlds"]' in src_sd
       and 'rare_counts(res["win_sole"][i], res["win"][i], res["top01"][i], res["n_worlds"], True,' in src_sd
       and 'best_worlds=is_opt[i], best_n=n_opt' in src_cl
       and 'rare_counts(r["win_sole"][i], r["win"][i], r["top01"][i], _n, False,' in src_cl
       and appjs.count('dfsRareTag(r, "first")') == 1 and appjs.count('dfsRareTag(r, "top01")') == 1
       and "outright, dup-adjusted" in seg and "best in " in seg and "escapeHtml(" in seg and "onclick" not in seg,
       f"{rc} {rc2}")
    # ---- SC9: the primary snapshot is named in advance ----
    import dfs_eligibility_read as ER
    regs = [json.load(open(os.path.join(_ROOT, "research", "data", d, "prereg.json")))
            for d in ("dfs_eligibility", "dfs_house_rules")]
    meta = {"label": "lead", "replay": "exact", "recorded_before_lock": True, "recorded_ts": 2e9, "lock_ts": 2e9 + 3600,
            "dg": 1}
    ok_lead = all(ER.eligible({"meta": meta}, r) for r in regs)
    no_close = not any(ER.eligible({"meta": dict(meta, label="close")}, r) for r in regs)
    no_inexact = not any(ER.eligible({"meta": dict(meta, replay="board.json differs")}, r) for r in regs)
    ck("SC9 the registrations name the primary snapshot BEFORE any slate is read (the owner: \"We shouldn't "
       "retrospectively choose whichever snapshot produced better results\"): the lead look, replayed exact; the "
       "reads refuse the close look and any inexact one; every capture stamps its label and replay verdict on the "
       "recording itself",
       all(r.get("primary_snapshot") == "lead" and "never replaced by its close look" in r.get("primary_snapshot_rule", "")
           for r in regs) and ok_lead and no_close and no_inexact
       and 'm.update(label=label, snapshot=os.path.basename(snap), replay=row.get("replay"))' in inspect.getsource(S.capture))
    ck("SC8 the scoring pass is untouched until Saturday's GPU validation (the owner: \"Preserve Saturday's GPU scoring "
       "validation before modifying the scoring pass\"): dfs_tourney.score_path_sig is the validated b0bf761df43c7f49",
       T.score_path_sig() == "b0bf761df43c7f49", T.score_path_sig())


if __name__ == "__main__":
    PASS, FAIL = [], []

    def ck(name, cond, detail=""):
        (PASS if cond else FAIL).append((name, detail))
        print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"   {detail}"))
    run(ck)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)
