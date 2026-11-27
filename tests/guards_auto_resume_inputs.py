"""Guards for a started build's FROZEN INPUTS (2026-11-27; build_ckpt inputs_*, dfs_tourney._BuildInputs,
nfl_dfs_sim.player_pool(pin=)), the deep probe v2 (research/profile_probe.py) and memprobe's two Windows fixes.

The PC's deep probe (deep-v1) killed a deep build at 40% of scoring and started it again: it resumed nothing
(`resumed: {}`, 7,129 s), because every input was fetched live again, had moved, and the checkpoint keys --
keys of the inputs, correctly -- named another build. The owner: "Resume existing build: reuse frozen inputs,
cached worlds and completed scoring chunks" and "Start fresh build: fetch current projections, injury
information, salaries and market data"; "The pre-lock refresh must always fetch current information."

Every build here runs the REAL player_pool engine path (Sleeper's games, the engine's week context and game
simulator, DraftKings and the depth gate stood in for), on the worlds cache, on DraftKings' 0.02 lattice, so
every equality is exact.

RI: checkpoints off is the old path; a build interrupted in scoring and restarted after Sleeper's line, a DK
status, a Kalshi-fed drive level, the depth chart, a status class, the roster and the contest fill ALL moved
resumes every stage on the frozen inputs, reads nothing live, and equals the uninterrupted build on the
original inputs; a generation interrupted mid-slate resumes its games on the frozen inputs; the cap, the
lock, the inactives, the pre-lock flag, an incomplete, damaged or foreign file and a missing worlds entry
each start fresh and say why; a picked build never reads a plain one's; the store's bounds; the worker.
MP: memprobe's process handle types and the machine reading's sanity check.
PV: the probe v2 -- its kill points read production's own log lines, C gets A1's frozen inputs and entropy
and nothing else, the comparison is refused across code versions, the drift report, the budget.

    python3 tests/guards_auto_resume_inputs.py      # exits 1 on any failed check
"""
import inspect
import os
import shutil
import sys
import tempfile
import time

os.environ.setdefault("VIGIL_NO_BG", "1")
_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "research"))


class _Stop(BaseException):
    """The batch file closed (a KeyboardInterrupt's shape: no handler in the build swallows it)."""


def run(ck):
    _memprobe_checks(ck)
    _probe_static_checks(ck)
    try:
        import numpy as np
    except ImportError:
        ck("(numpy absent: frozen inputs are the PC's, where the boards are built)", True)
        return
    import build_ckpt as BC
    tmp = tempfile.mkdtemp()
    keys = ("VIGIL_CKPT_DIR", "VIGIL_CKPT_GB", "VIGIL_POOL_CACHE_DIR")
    saved = {k: os.environ.get(k) for k in keys}
    every = BC.SAVE_EVERY_S
    try:
        os.environ.pop("VIGIL_CKPT_GB", None)
        os.environ.pop("VIGIL_CKPT_DIR", None)
        os.environ["VIGIL_POOL_CACHE_DIR"] = os.path.join(tmp, "pool")
        BC.SAVE_EVERY_S = 0.0
        with _World(np, tmp) as w:
            _off_checks(ck, np, BC, w)
            os.environ["VIGIL_CKPT_DIR"] = os.path.join(tmp, "ckpt")
            _resume_checks(ck, np, BC, w)
            _refusal_checks(ck, np, BC, w)
            _generation_checks(ck, np, BC, w)
            _store_checks(ck, BC, tmp)
        _zero_checks(ck, np)
        _worker_checks(ck)
    finally:
        BC.SAVE_EVERY_S = every
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(tmp, ignore_errors=True)


# ---- the stand-in world ----------------------------------------------------------------
class _World:
    """DraftKings, Sleeper, the engine's week context and game simulator, the depth gate, the roster and the
    status classes, each answering for the current `phase` ("orig" or "moved") and counting its calls."""

    def __init__(self, np, tmp):
        import guards_picker as GP
        self.np, self.tmp, self.GP = np, tmp, GP
        self.csv, self.pool_c, self.gated = GP._classic_slate()
        starts = time.strftime("%Y-%m-%dT%H:%M:%S.0000000Z", time.gmtime(time.time() + 3 * 86400))
        self.slate = {"starts": starts, "draft_group_id": 999, "n_players": 104,
                      "contests": [{"id": 1, "prize_pool": 2.0e6, "entries": 1000}]}
        self.detail = {"id": 1, "name": "Guard Millionaire", "prize_pool": 2.0e6, "max_entries": 40000,
                       "entry_fee": 20.0, "first_prize": 200000, "places_paid": 8000, "entered": 1000,
                       "max_entries_per_user": 150,
                       "payouts": [{"from": 1, "to": 1, "prize": 200000.0}, {"from": 2, "to": 10, "prize": 10000.0},
                                   {"from": 11, "to": 8000, "prize": 40.0}]}
        row = next(r for r in self.csv.split("\n") if ",Finn Hawk," in r)
        self.csv_moved = self.csv.replace(row, row[:-len("ACTIVE")] + "OUT")       # DK: a WR1 ruled OUT
        self.phase = "orig"
        self.calls = {}
        self.stop_sim_after = None
        self.kw = dict(n_sims=750, opt_worlds=250, field_n=3000, cand_n=2000, cal_n=1000, week=8, preseason=False,
                       workers=1, probes=[], model="engine", top=15, k_port=4, candidates=200, chunk=64)

    def _count(self, name, fn):
        def w(*a, **k):
            self.calls[name] = self.calls.get(name, 0) + 1
            return fn(*a, **k)
        return w

    def games(self, season, week):
        import nfl_dfs_sim as S
        by_game = {}
        for nm, p in sorted(self.pool_c.items()):
            if p["pos"] == "DST":
                continue
            by_game.setdefault(p["team"], []).append((nm, p))
        teams = sorted(by_game)
        out = {}
        for gi in range(0, len(teams), 2):
            a, h = teams[gi], teams[gi + 1]
            pl = []
            for tm, op in ((a, h), (h, a)):
                for nm, p in by_game[tm]:
                    proj = p["proj"] + (2.5 if (self.phase == "moved" and nm == "Cal Hawk") else 0.0)
                    pl.append({"name": nm, "pos": p["pos"], "team": tm, "opp": op, "proj_pts": round(proj, 2),
                               "means": {"rec_tgt": 5.0}, "recon": {"rec_tgt": 5.0}})
            out[f"g{gi // 2}"] = {"label": f"{a} @ {h}", "teams": [h, a], "home": h, "away": a, "players": pl}
        S.weekly_stamps[(str(season), int(week))] = {"usage": {"applied": True, "phase": self.phase},
                                                    "built_ts": 1000 if self.phase == "orig" else 2000}
        return out

    def dst(self, season, week):
        return {p["team"]: {"nickname": nm, "proj": p["proj"]} for nm, p in self.pool_c.items() if p["pos"] == "DST"}

    def context(self, season, week, workers=1):
        teams = sorted({p["team"] for p in self.pool_c.values()})
        drive = {t: 21.0 for t in teams}
        if self.phase == "moved":
            drive["AAA"] = 24.5                              # the market moved the drive model's level
        return {"T": {}, "profiles": {t: {"unit": t} for t in teams}, "drive": drive, "weight": 0.3, "workers": 1}

    def sim(self, game, n, rng, ctx):
        np = self.np
        if self.stop_sim_after is not None and self.calls.get("sim", 0) > self.stop_sim_after:
            raise _Stop()
        out = []
        for j, p in enumerate(game["players"]):
            arr = np.round(np.maximum(0.0, rng.normal(p["proj_pts"], 0.55 * p["proj_pts"] + 1.5, n)) / 0.02) * 0.02
            if j == 0:
                arr[0] = -0.0                    # the engine's arrays hold negative zeros (hp_memory_exact)
            out.append({"name": p["name"], "pos": p["pos"], "team": p["team"], "opp": p["opp"],
                        "proj_pts": p["proj_pts"], "ceiling": float(np.percentile(arr, 90)),
                        "floor": float(np.percentile(arr, 10)), "arr": arr, "sim_mean": float(arr.mean())})
        dst = {t: np.round(rng.normal(6, 4, n) / 0.02) * 0.02 for t in game["teams"]}
        for a in dst.values():
            a[1] = -0.0
        return {"label": game["label"], "teams": game["teams"], "players": out, "n_sims": n, "dst": dst, "k": {},
                "anchor": {"ok": game["label"]}, "team_def": {}}

    def depth(self, players, preseason, seconds_to_lock=None):
        gated = dict(self.gated)
        if self.phase == "moved":
            gated["Gus Hawk"] = "listed Doubtful"
        return self.GP._fake_depth(gated)(players, preseason, seconds_to_lock)

    def status(self, names):
        return {nm: ("q" if (self.phase == "moved" and nm == "Ace Hawk") else "in") for nm in names}

    def __enter__(self):
        import dfs_tourney as T
        import dk
        import nfl_dfs as N
        import nfl_dfs_engine as ENG
        import nfl_dfs_sim as S
        c = self._count
        real_steer = T.steer_field
        self.patches = [
            (dk, "slate_for", c("slate_for", lambda sport, draft_group_id=None, exclude_games=None: dict(
                self.slate, csv=self.csv_moved if self.phase == "moved" else self.csv))),
            (dk, "contest_detail", c("contest_detail", lambda cid: dict(
                self.detail, entered=9000 if self.phase == "moved" else 1000))),
            (S, "weekly_games", c("weekly_games", self.games)), (S, "dst_projections", c("dst", self.dst)),
            (S, "kicker_projections", c("kickers", lambda season, week: {})),
            (ENG, "week_context", c("week_context", self.context)), (ENG, "simulate_game", c("sim", self.sim)),
            (N, "_apply_depth", c("depth", self.depth)), (T, "status_classes", c("status", self.status)),
            (N, "roster_state", c("roster", lambda: {"source": "guard", "fetched_utc": self.phase, "age_s": 5.0})),
            (T, "steer_field", lambda draw, players, rng, beta, kappa, targets: real_steer(
                draw, players, rng, beta, kappa, targets, n=4000, iters=3)),
            (T, "OPT_FIELD_N", 4000)]
        self.old = [(o, n, getattr(o, n)) for o, n, _v in self.patches]
        for o, n, v in self.patches:
            setattr(o, n, v)
        return self

    def __exit__(self, *exc):
        import nfl_dfs_sim as S
        for o, n, v in reversed(self.old):
            setattr(o, n, v)
        S._cache.clear()
        return False

    def build(self, log=None, **kw):
        import dfs_tourney as T
        import nfl_dfs_sim as S
        S._cache.clear()
        lines = [] if log is None else log
        return T.build_nfl_classic(999, log=lambda *a: lines.append(" ".join(str(x) for x in a)),
                                   **dict(self.kw, **kw))

    def live_reads(self):
        return {k: v for k, v in self.calls.items() if k != "sim" and v}


def _interrupt_after(BC, k):
    real, n = BC.range_save, [0]

    def save(*a, **kw):
        out = real(*a, **kw)
        n[0] += 1
        if n[0] >= k:
            raise _Stop()
        return out
    return real, save


def _stopped(BC, w, k=1, **kw):
    """A build stopped at its k-th scoring save: (log lines, whether it stopped)."""
    real, save = _interrupt_after(BC, k)
    BC.range_save = save
    lines = []
    try:
        w.build(log=lines, **kw)
        stopped = False
    except _Stop:
        stopped = True
    finally:
        BC.range_save = real
    return lines, stopped


_CLOCKS = ("built_ts", "timings", "checkpoint", "eval_artifact")


def _strip(board):
    return {k: v for k, v in (board or {}).items() if k not in _CLOCKS}


# ---- RI ------------------------------------------------------------------------------
def _off_checks(ck, np, BC, w):
    import dfs_tourney as T
    import nfl_dfs_sim as S
    seen = {"pool": [], "status": []}
    real_pool, real_sig = S.player_pool, T.status_sig

    def spy_pool(*a, **k):
        seen["pool"].append(sorted(k))
        return real_pool(*a, **k)

    def spy_sig(*a, **k):
        seen["status"].append((len(a), sorted(k)))
        return real_sig(*a, **k)
    S.player_pool, T.status_sig = spy_pool, spy_sig
    try:
        off = w.build(seed=7)
    finally:
        S.player_pool, T.status_sig = real_pool, real_sig
    os.environ["VIGIL_CKPT_DIR"] = os.path.join(w.tmp, "ckpt_on")
    try:
        lines = []
        on = w.build(log=lines, seed=7)
    finally:
        os.environ.pop("VIGIL_CKPT_DIR", None)
    stamp = ((on or {}).get("checkpoint") or {}).get("inputs") or {}
    src = inspect.getsource(S.player_pool)
    ck("RI1 checkpoints off (the server, this suite, research) is the old path: no frozen inputs are opened, "
       "player_pool is called without pin/on_pin (so its in-process cache and its old key expression run), the "
       "status stamp is read live at the end with the names alone and the roster state live, nothing is written "
       "under any checkpoint directory and the board carries no checkpoint stamp; with checkpoints ON and the "
       "same seed the board is the same number for number -- freezing the inputs changes nothing a board says",
       off is not None and on is not None and "checkpoint" not in off and seen["pool"]
       and all("pin" not in k and "on_pin" not in k for k in seen["pool"]) and seen["status"] == [(1, [])]
       and T._BuildInputs.open("classic", (1,)) is None and not T._inputs_on("engine", False)
       and not os.path.exists(os.path.join(w.tmp, "ckpt"))
       and _strip(off) == _strip(on) and stamp.get("operation") == BC.FRESH and stamp.get("frozen") is True
       and "if pin is None:" in src and "if pin is not None:\n        return build()" in src
       and "dst=dst_projections(str(season), week),\n                            kickers=kicker_projections(str(season), week)))" in src,
       f"pool kwargs {seen['pool'][:2]}; status {seen['status']}; on {stamp.get('operation')}")


def _resume_checks(ck, np, BC, w):
    import dfs_tourney as T
    import nfl_dfs_sim as S
    import profile_probe as PP
    w.phase, w.calls = "orig", {}
    lines_a, stopped = _stopped(BC, w, k=4)              # unseeded, as production; four chunks of 64 scored
    key = T._CKPT[0].key if T._CKPT[0] is not None else None
    T._CKPT[0] = None
    rkey = next((m.group(3) for m in map(PP._OPLINE.search, lines_a) if m), None)
    snap, _why = BC.inputs_load(rkey) if rkey else (None, None)
    w._snap = snap
    # everything live moves overnight
    w.phase, w.calls = "moved", {}
    lines_b = []
    resumed = w.build(log=lines_b)
    reads_b, sims_b = w.live_reads(), w.calls.get("sim", 0)
    stamp = (resumed or {}).get("checkpoint") or {}
    inp_b = stamp.get("inputs") or {}
    # the uninterrupted build on the ORIGINAL inputs: a fresh checkpoint directory holding only A's entropy
    w.phase = "orig"
    ref_dir = os.path.join(w.tmp, "ckpt_ref")
    os.makedirs(os.path.join(ref_dir, "builds", str(key)), exist_ok=True)
    shutil.copy2(os.path.join(BC.root(), "builds", str(key), "rng.json"),
                 os.path.join(ref_dir, "builds", str(key), "rng.json"))
    os.environ["VIGIL_CKPT_DIR"] = ref_dir
    try:
        lines_r = []
        ref = w.build(log=lines_r)
    finally:
        os.environ["VIGIL_CKPT_DIR"] = os.path.join(w.tmp, "ckpt")
    inp_r = ((ref or {}).get("checkpoint") or {}).get("inputs") or {}
    ev = len(T.world_split(w.kw["n_sims"], w.kw["opt_worlds"])[1])
    res = stamp.get("resumed") or {}
    # the moved inputs WOULD have moved the board: another pool signature, another worlds key, another gate
    moved_in = S.engine_inputs(8, season="2026", teams={"AAA", "BBB"})
    w.phase = "moved"
    moved_mv = S.engine_inputs(8, season="2026", teams={"AAA", "BBB"})
    w.phase = "orig"
    dg_o, dg_m = T.input_digests({"pool": moved_in}), T.input_digests({"pool": moved_mv})
    ck("RI2 Resume existing build: a build stopped in its scoring pass and started again AFTER Sleeper's line, a "
       "DraftKings status, the market-fed drive level, the depth chart, a status class, the roster and the contest "
       "fill all moved reads NONE of them live (no DraftKings, Sleeper, engine-context, depth, status or roster "
       "call; no game simulated), takes the frozen inputs, the filed worlds, the generator's entropy, the best "
       "lineups and the 256 scored worlds from disk, and its board equals -- every figure but the clocks and the "
       "checkpoint stamp -- the uninterrupted build on the ORIGINAL inputs (a fresh checkpoint holding only the "
       "same entropy), whose own frozen inputs digest the same",
       stopped and resumed is not None and ref is not None and _strip(resumed) == _strip(ref)
       and reads_b == {} and sims_b == 0 and stamp.get("key") == key
       and inp_b.get("operation") == BC.RESUME and inp_b.get("pinned") is True and inp_b.get("key") == rkey
       and 0 <= inp_b.get("age_s", -1) < 600 and inp_r.get("operation") == BC.FRESH
       and res.get("optimal") == "from the checkpoint" and res.get("rng") and f"256 of {ev} worlds" in res.get("score", "")
       and inp_b.get("digests") == inp_r.get("digests") and inp_b["digests"].get("worlds_key")
       and resumed["pool_sig"] == T.pool_sig(w.csv) != T.pool_sig(w.csv_moved)
       and dg_o["games"] != dg_m["games"] and dg_o["drive"] != dg_m["drive"]
       and resumed["status"].get("Ace Hawk") == "in" and resumed["roster_source"]["fetched_utc"] == "orig"
       and resumed["contests"][0]["entered"] == 1000 and resumed["inputs"]["usage"]["phase"] == "orig"
       and not any(e["name"] == "Gus Hawk" for e in resumed["excluded"]),
       f"stopped {stopped}; live reads in the resume {reads_b}; sims {sims_b}; stamp {inp_b.get('operation')} "
       f"{inp_b.get('why')}; resumed {res}; equal {resumed is not None and ref is not None and _strip(resumed) == _strip(ref)}; "
       f"diff {PP._diff(_strip(resumed), _strip(ref))[:6] if resumed and ref else None}")
    # the probe reads these very lines
    m_ck = [PP._CKLINE.search(x) for x in lines_b]
    ck("RI3 the probe's parsers read production's own log: the operation line (which, why, the request key), the "
       "checkpoint line (the build key the board stamps), the best-lineup solves' progress, the scoring progress "
       "and the portfolio pass's start",
       any(m and m.group(1) == BC.RESUME and m.group(3) == rkey for m in map(PP._OPLINE.search, lines_b))
       and any(m and m.group(1) == key and m.group(2) == "found" for m in m_ck)
       and any(PP._SOLVED.search(x) for x in lines_r) and any(PP._PROGRESS.search(x) for x in lines_r)
       and any(PP._PORT_START.search(x) for x in lines_r)
       and not any(PP._PROGRESS.search(x) for x in lines_r if "solved" in x),
       f"{[x for x in lines_b if 'Resume' in x or 'checkpoint' in x][:3]}")


def _refusal_checks(ck, np, BC, w):
    import dfs_tourney as T
    import errlog
    import pool_cache as PC
    snap = getattr(w, "_snap", None) or {}
    now = time.time()
    lock = float(snap.get("lock_ts") or now + 86400)
    U = BC.inputs_usable
    ok_now = U(snap, now)
    old = U(dict(snap, as_of=now - BC.INPUTS_MAX_AGE_S - 60), now)
    edge = U(dict(snap, as_of=now - BC.INPUTS_MAX_AGE_S + 60), now)
    locked = U(dict(snap, as_of=lock - 600), lock + 1)
    pre_inact = U(dict(snap, as_of=lock - 2 * 3600), lock - 3600)
    post_inact = U(dict(snap, as_of=lock - 3600), lock - 1800)
    flag = U(snap, now, fresh=True)
    no_key = U(dict(snap, pool=dict(snap.get("pool") or {}, worlds_key=None)), now)
    no_part = U({k: v for k, v in snap.items() if k != "details"}, now)
    # a damaged file, a foreign file
    rk = "f" * 40
    BC.inputs_save(rk, dict(snap, key=rk))
    p = BC._inputs_path(rk)
    with open(p, "r+b") as f:
        f.seek(-9, 2)
        b = f.read(1)
        f.seek(-9, 2)
        f.write(bytes([b[0] ^ 1]))
    dmg, dmg_why = BC.inputs_load(rk)
    gone = not os.path.exists(p)
    BC.inputs_save(rk, dict(snap, key="e" * 40))
    foreign, foreign_why = BC.inputs_load(rk)
    gone2 = not os.path.exists(p)
    ck("RI4 frozen inputs are used only while they can stand for the build: fresh past the six-hour cap (and not "
       "before it), once the slate has locked, when they were frozen before the inactives and the inactives are out "
       "(not when frozen after them), whenever the pre-lock rebuild asks for fresh inputs, and when any part or "
       "the worlds key is missing; a file that fails its sha256, or that is another request's, is removed and "
       "read as absent -- each says why",
       ok_now == (True, None) and not old[0] and "h old" in old[1] and edge[0] and not locked[0]
       and "locked" in locked[1] and not pre_inact[0] and "inactives" in pre_inact[1] and post_inact[0]
       and not flag[0] and "pre-lock" in flag[1] and not no_key[0] and "incomplete" in no_key[1]
       and not no_part[0] and "details" in no_part[1] and dmg is None and "sha256" in dmg_why and gone
       and foreign is None and "could not be read" in foreign_why and gone2 and BC.INPUTS_MAX_AGE_S == 6 * 3600
       and BC.INACTIVES_S == 90 * 60,
       f"{ok_now} {old} {edge} {locked} {pre_inact} {post_inact} {flag} {no_key} {no_part} {dmg_why} {foreign_why}")
    # the worlds entry gone (pruned, or failed its sha256): the whole build starts again on current inputs
    noted = []
    real_note = errlog.note

    def note(eid, *a, **k):
        noted.append(eid)
        return real_note(eid, *a, **k)
    PC.clear()
    w.phase, w.calls = "moved", {}
    errlog.note = note
    try:
        lines_m, stopped_m = _stopped(BC, w, k=1)
    finally:
        errlog.note = real_note
    T._CKPT[0] = None
    reads_m = w.live_reads()
    ops = [x for x in lines_m if "-- " in x and "(request " in x]
    ck("RI5 never half-old, half-new: frozen inputs whose worlds are gone from the worlds cache (and no game of an "
       "interrupted generation is kept) are refused inside player_pool (PinMiss), ledgered CKPT-inputs, and the "
       "WHOLE build starts again fresh -- DraftKings, Sleeper, the context and the gate read live -- saying why",
       stopped_m and len(ops) == 2 and BC.RESUME in ops[0] and BC.FRESH in ops[1]
       and "worlds are no longer on disk" in ops[1] and "CKPT-inputs" in noted
       and all(reads_m.get(k) for k in ("slate_for", "contest_detail", "weekly_games", "week_context", "depth"))
       and w.calls.get("sim", 0) > 0, f"{ops}; {noted}; {reads_m}")
    # the pre-lock flag, a frozen set present: always live
    w.calls = {}
    lines_f, _s = _stopped(BC, w, k=1, fresh_inputs=True)
    T._CKPT[0] = None
    reads_f = w.live_reads()
    w.phase = "orig"
    plain_parts = (999, 1e6, None, 750)
    a = T._BuildInputs.open("classic", plain_parts + (None,))
    BC.inputs_save(a.key, dict(snap, key=a.key))
    again = T._BuildInputs.open("classic", plain_parts + (None,))
    picked = T._BuildInputs.open("classic", plain_parts + ({"force": [("Ace Hawk", "AAA", None)]},))
    still = BC.inputs_load(a.key)[0] is not None
    src = inspect.getsource(T.build_nfl_classic)
    ck("RI6 the pre-lock rebuild always fetches current information: with frozen inputs on disk, fresh_inputs "
       "starts fresh and reads DraftKings, Sleeper, the context and the gate live; a picked build's request is "
       "never a plain build's (the pick is in the request key), so it never reads the plain build's frozen inputs, "
       "which stay on disk",
       any(BC.FRESH in x and "pre-lock" in x for x in lines_f)
       and all(reads_f.get(k) for k in ("slate_for", "weekly_games", "week_context", "depth"))
       and again is not None and again.pinned and picked is not None and not picked.pinned
       and picked.key != a.key and still
       and "int(k_port), int(candidates), seed, model, pick), fresh=fresh_inputs" in src,
       f"{[x for x in lines_f if '(request' in x]}; {reads_f}")


def _generation_checks(ck, np, BC, w):
    """Interrupted at another stage: mid-generation (the worlds), restarted after the inputs moved."""
    import dfs_tourney as T
    import pool_cache as PC
    import profile_probe as PP
    PC.clear()
    shutil.rmtree(BC.root(), ignore_errors=True)
    w.phase, w.calls = "orig", {}
    w.stop_sim_after = 2                                  # two of four games played, then the batch file closes
    try:
        w.build(log=[])
        stopped = False
    except _Stop:
        stopped = True
    finally:
        w.stop_sim_after = None
    T._CKPT[0] = None
    kept = sum(len(fs) for _r, _d, fs in os.walk(os.path.join(BC.root(), "games")))
    w.phase, w.calls = "moved", {}
    lines, _s = _stopped(BC, w, k=1)
    T._CKPT[0] = None
    rkey = next((m.group(3) for m in map(PP._OPLINE.search, lines) if m), None)
    snap = BC.inputs_load(rkey)[0] if rkey else None
    wk = ((snap or {}).get("pool") or {}).get("worlds_key")
    entries = [os.path.basename(e[0]) for e in PC.entries()]
    ck("RI7 interrupted mid-generation (two of four games played) and restarted after the inputs moved: the "
       "restart is a Resume existing build on the frozen inputs, plays only the two games left (the kept games "
       "are under the frozen worlds key), files the pool under that key and freezes the depth gate it then reads",
       stopped and kept == 2 and any(BC.RESUME in x for x in lines) and w.calls.get("sim") == 2
       and wk and entries == [wk] and not w.calls.get("weekly_games") and not w.calls.get("week_context")
       and "depth" in (snap or {}),
       f"stopped {stopped}; kept {kept}; sims {w.calls.get('sim')}; entries {entries} vs {wk}")


def _store_checks(ck, BC, tmp):
    root = BC.root()
    shutil.rmtree(root, ignore_errors=True)
    for i in range(3):
        b = BC.open_build("guard", "store", i)
        os.utime(b.path, (2_000_000_000 + i, 2_000_000_000 + i))
    rks = []
    for i in range(BC.KEEP_INPUTS + 2):
        rk = f"{i:040d}"
        BC.inputs_save(rk, {"v": BC.INPUTS_VERSION, "key": rk, "i": i})
        os.utime(os.path.dirname(BC._inputs_path(rk)), (2_000_000_000 + 10 + i, 2_000_000_000 + 10 + i))
        rks.append(rk)
    BC.prune(max_age=1e12)
    inp_left = sorted(os.listdir(os.path.join(root, "inputs")))
    builds_left = len(os.listdir(os.path.join(root, "builds")))
    stale = rks[-1]
    os.utime(os.path.dirname(BC._inputs_path(stale)), (time.time() - BC.INPUTS_MAX_AGE_S - 60,) * 2)
    BC.prune(max_age=1e12)
    stale_gone = not os.path.exists(BC._inputs_path(stale))
    b = BC.open_build("guard", "landed")
    BC.inputs_save(rks[-2], {"v": BC.INPUTS_VERSION, "key": rks[-2]})
    landed = BC.landed({"checkpoint": {"key": b.key, "inputs": {"key": rks[-2]}}})
    ck("RI8 the store: frozen inputs count toward the checkpoint bytes but are held to their own count "
       "(KEEP_INPUTS), so they never push a build out of the six; past the six-hour cap they are pruned (nothing "
       "may read them); when a board lands its frozen inputs go with its checkpoint",
       inp_left == sorted(rks[-BC.KEEP_INPUTS:]) and builds_left == 3 and stale_gone and landed
       and not os.path.exists(BC._inputs_path(rks[-2])) and not os.path.isdir(b.path)
       and BC.total_bytes() > 0, f"{len(inp_left)} {builds_left} {stale_gone} {landed}")


def _zero_checks(ck, np):
    import build_ckpt as BC
    import dfs_tourney as T
    a = np.asarray([[1.0, -0.0, 0.0], [-0.0, 2.5, -0.0]], dtype=np.float32)
    saved = os.environ.get("VIGIL_CKPT_DIR")
    try:
        os.environ.pop("VIGIL_CKPT_DIR", None)
        off = T._ckpt_zero(a.copy())
        os.environ["VIGIL_CKPT_DIR"] = tempfile.mkdtemp()
        on = T._ckpt_zero(a.copy())
        shutil.rmtree(os.environ["VIGIL_CKPT_DIR"], ignore_errors=True)
    finally:
        if saved is None:
            os.environ.pop("VIGIL_CKPT_DIR", None)
        else:
            os.environ["VIGIL_CKPT_DIR"] = saved
    src = inspect.getsource(T.build_nfl_classic)
    ck("RI10 the worlds matrix keys alike from the cache and from memory: the worlds cache files int32 hundredths, "
       "which have no -0.0, so where checkpoints are on every -0.0 in X is made +0.0 before any key hashes it (the "
       "values equal, the bytes the cached pool's); off, X is untouched byte for byte -- RI2's simulator plants "
       "negative zeros, and without this its resume named another build",
       off.tobytes() == a.tobytes() and np.array_equal(on, a) and not np.signbit(on).any()
       and BC.sig("x", on) != BC.sig("x", a)
       and "X = _ckpt_zero(np.asarray([e[\"arr\"][:N] for e in ents], dtype=np.float32))" in src
       and src.index("_ckpt_zero(") < src.index("build_ckpt.open_build(")
       and "X = _ckpt_zero(X)" in inspect.getsource(T.build_nfl_showdown))


def _worker_checks(ck):
    import pc_worker as W
    src_c = inspect.getsource(W._task_classic_tourney)
    src_p = inspect.getsource(W._picked_builds)
    ck("RI9 the worker passes fresh_inputs to every classic build in the 85-40-minute pre-lock window (the plain "
       "board's two calls and the picked board's two) and nothing anywhere else, so every other call is "
       "production's unchanged",
       W._fresh_kw("2026-11-29 pregame") == {"fresh_inputs": True} and W._fresh_kw("2026-11-28 saturday night") == {}
       and W._fresh_kw(None) == {} and src_c.count("**fresh)") == 2 and "fresh = _fresh_kw(window)" in src_c
       and src_p.count("**_fresh_kw(pk_window))") == 2)


# ---- MP ------------------------------------------------------------------------------
def _memprobe_checks(ck):
    import ctypes
    import memprobe as M
    from ctypes import wintypes

    class Fn:
        def __init__(self, body):
            self.body, self.argtypes, self.restype, self.seen = body, None, None, None

        def __call__(self, *a):
            self.seen = (self.argtypes, self.restype)
            return self.body(*a)
    pseudo = 0xFFFFFFFFFFFFFFFF                            # GetCurrentProcess's (HANDLE)-1, 64 bits

    def gpmi(h, ref, cb):
        ok = h == pseudo and gpmi_fn.argtypes and gpmi_fn.argtypes[0] is wintypes.HANDLE
        ref._obj.WorkingSetSize = 123456789 if ok else 0
        return 1 if ok else 0
    k32 = type("K", (), {})()
    k32.GetCurrentProcess = Fn(lambda: pseudo)
    psapi = type("P", (), {})()
    gpmi_fn = Fn(gpmi)
    psapi.GetProcessMemoryInfo = gpmi_fn
    got = M._win_process_rss(k32, psapi)
    ck("MP1 memprobe's process figure on Windows: GetCurrentProcess returns the 64-bit pseudo-handle and is "
       "declared so (restype HANDLE), GetProcessMemoryInfo takes (HANDLE, the counters, DWORD) -> BOOL -- without "
       "them ctypes passed a truncated 32-bit int and every sample read None on the PC; the figure is this "
       "process only (the build's pools are child processes), so the machine's stays the headline",
       got == 123456789 and k32.GetCurrentProcess.seen[1] is wintypes.HANDLE
       and gpmi_fn.argtypes[0] is wintypes.HANDLE and gpmi_fn.argtypes[2] is wintypes.DWORD
       and gpmi_fn.restype is wintypes.BOOL and issubclass(gpmi_fn.argtypes[1], ctypes._Pointer)
       and "this process only" in M.Watch(every=0.05).stop()["process_scope"], str(got))
    G = 1024 ** 3
    good = lambda: (int(9.5 * G), int(31.78 * G))
    bad = lambda: (0, int(347.76 * G))                    # run C's reading on the PC
    drift = lambda: (int(5 * G), int(347.76 * G))
    near = lambda: (int(9.0 * G), int(31.9 * G))
    boom = lambda: (_ for _ in ()).throw(OSError("x"))
    M._reset()
    try:
        r1 = M.system_used([bad, good])
        r2 = M.system_used([near])
        r3 = M.system_used([drift, good])
        n0 = M.ANOMALIES["n"]
        r4 = M.system_used([drift, boom])
        n1 = M.ANOMALIES["n"]
        real = M._readers
        M._readers = lambda: (bad,)
        try:
            wt = M.Watch(every=0.02)
            time.sleep(0.1)
            res = wt.stop()
        finally:
            M._readers = real
    finally:
        M._reset()
    ck("MP2 the machine's memory is never reported from an impossible reading: 0 in use (run C's two hours) falls to "
       "the second reader (GetPerformanceInfo on Windows), a total within 2% of the process's first good reading "
       "is believed, one that jumps (347.76 GB against 31.78) is not, and when no reader is sane the sample is "
       "None and counted -- a Watch over such samples reports the machine's figures as None with the count",
       r1 == good() and r2 == near() and r3 == good() and r4 == (None, None) and n1 == n0 + 1
       and res["system_peak_used_gb"] is None and res["build_added_gb"] is None and res["system_unreadable_samples"] > 0
       and M.TOTAL_TOL == 0.02 and M._readers.__code__ is real.__code__,
       f"{r1} {r2} {r3} {r4} {res}")


# ---- PV ------------------------------------------------------------------------------
def _probe_static_checks(ck):
    import pc_worker as W
    import profile_probe as PP
    tmp = tempfile.mkdtemp()
    try:
        src = os.path.join(tmp, "a")
        for rel, body in (("inputs/abc/inputs.ck", b"frozen"), ("builds/k1/rng.json", b"{}"),
                          ("builds/k1/memo-optimal-x.ck", b"m"), ("builds/k1/score-x-0-1.ck", b"s"),
                          ("games/w/g.ck", b"g")):
            p = os.path.join(src, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as fh:
                fh.write(body)
        dst = os.path.join(tmp, "c")
        got = PP._copy_frozen(src, dst)
        files = sorted(os.path.relpath(os.path.join(r, f), dst) for r, _d, fs in os.walk(dst) for f in fs)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    v = {"commit": "a", "scoring_code": "s"}
    runs = [{"tag": "A1", "versions": v, "request_key": "r1"}, {"tag": "B", "versions": v},
            {"tag": "C", "versions": dict(v, commit="b")}]
    board = {"x": 1, "checkpoint": {"key": "k", "inputs": {"operation": "Resume existing build", "key": "r1"}}}
    split = PP._agreement(runs, board, board, {"rng": ["k"], "inputs": ["r1"]})
    runs[2]["versions"] = v
    same = PP._agreement(runs, board, dict(board, x=2), {"rng": ["k"], "inputs": ["r1"]})
    notc = PP._agreement(runs, board, {"x": 1, "checkpoint": {"key": "k", "inputs": {"operation": "Start fresh build",
                                                                                      "key": "r9", "why": "h old"}}},
                         {"rng": ["k"]})
    live = lambda ts, d, f: {"ts": ts, "digests": d, "games_fields": f}
    drift = PP._drift([{"tag": "A1", "live": live(100, {"games": "1", "drive": "a", "slate_csv": "x"},
                                                  {"proj_pts": {"p1": "a", "p2": "b"}, "means": {"p1": "m"}})},
                       {"tag": "A2", "live": live(700, {"games": "2", "drive": "a", "slate_csv": "y"},
                                                  {"proj_pts": {"p1": "a", "p2": "c", "p3": "d"}, "means": {"p1": "m"}})}])
    later = (drift.get("later") or [{}])[0]
    ck("PV1 the probe v2 tests production's path: one build killed in the solves, in scoring and in the portfolio "
       "pass and resumed each time, then C -- an empty checkpoint given A1's frozen inputs and generator entropy "
       "and nothing else (no stage memo, no scored range, no game); the comparison is REFUSED loudly when two runs "
       "ran different code or model versions or when C did not read A1's frozen inputs, and otherwise reports "
       "equal and up to 40 first differences; the drift names the inputs that moved, which Sleeper player fields "
       "for how many players, and the seconds since A1's read",
       PP.VERSION == 2 and [k for _t, k in PP.STAGES] == ["solve", "score", "portfolio", None]
       and files == ["builds/k1/rng.json", "inputs/abc/inputs.ck"] and got["rng"] == ["k1"]
       and split["comparable"] is False and "REFUSED" in split["refused"] and "commit" in split["refused"]
       and same["comparable"] is True and same["equal"] is False and same["first_differences"] == [".x"]
       and notc["comparable"] is False and "did not read A1's frozen inputs" in notc["refused"]
       and PP.MAX_DIFFS == 40 and set(PP._VOLATILE) == {"built_ts", "timings", "timing", "checkpoint",
                                                         "eval_artifact", "memory", "requested_by", "profile"}
       and later.get("moved") == ["games", "slate_csv"] and later.get("player_fields_moved") == {"proj_pts": 2}
       and later.get("players_added") == 1 and later.get("seconds_after_first") == 600,
       f"{files} {split.get('refused')} {same} {notc.get('refused')} {drift}")
    est_build = 7163.0                                     # v1's fresh deep build on the PC
    # nothing resumed: A1 and A2 at most v1's kill at 40% of scoring, A3 and B at most a whole build, C a build
    worst = 4280.0 + 4280.0 + 7129.0 + 7129.0 + 7163.0
    ck("PV2 the probe is re-run once (deep-v2) under the same scheduling rules, and its budget covers it: four "
       "deep builds at the profiles' 1.25 margin -- the worst case, where nothing resumed and each killed run "
       "repeats everything before its kill point, is at most ~4.2 builds of v1's measured times -- and the "
       "probe's cap holds it",
       W._PROBE_KEY == "deep-v2" and W._PROBE_BUILDS == 4.0 and W._PROBE_TIMEOUT_S >= worst
       and W._PROBE_BUILDS * 1.25 * est_build + 1800 >= worst and W._PROBE_CLEAR_S == 10_800,
       f"worst {worst:.0f} s")


if __name__ == "__main__":
    PASS, FAIL = [], []

    def ck(name, cond, detail=""):
        (PASS if cond else FAIL).append((name, detail))
        print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"   {detail}"))
    run(ck)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)
