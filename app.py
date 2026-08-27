"""Kalshi crypto betting helper -- Flask backend.

Endpoints
  GET  /                      -> the live web UI
  GET  /api/coins            -> supported coins
  GET  /api/quote            -> live spot + signal for ad-hoc params (no save)
  POST /api/markets          -> create/track a market (saves a model snapshot)
  GET  /api/markets          -> list tracked markets with live signals; auto-resolves
  DELETE /api/markets/<id>   -> stop tracking a market
  GET  /api/stats            -> running accuracy + Brier score

This is a decision-support tool. The odds are model estimates, not guarantees.
"""

import os
import clock
import datetime
import hmac
import hashlib
import json
import time
import threading

from flask import Flask, g, jsonify, request, render_template, Response
from werkzeug.middleware.proxy_fix import ProxyFix

import prices
import odds
import store
import kalshi
import baseball
import tiers
import errlog

app = Flask(__name__)
# Respect the X-Forwarded-* headers from the hosting platform's reverse proxy so
# request.is_secure / scheme are correct (needed for HSTS + secure cookies).
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
# Bound request bodies BEFORE they buffer: without this Flask reads any POST
# fully into memory before a handler can size-check it. The artifact door's own
# cap is 64 MB after decompression; this is the transport-level backstop.
app.config["MAX_CONTENT_LENGTH"] = 96_000_000
# Used by Flask for signing if we add sessions later; pulled from the env in prod.
app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(32)
# Changes every server start so the browser re-fetches CSS/JS after an update.
_ASSET_VERSION = str(int(time.time()))
_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def _asset_version():
    """Cache-busting token = the newest mtime of the front-end files. Changes the
    moment app.js/style.css change on disk (e.g. after `git pull`), so a plain
    page reload fetches the new UI without needing a server restart."""
    try:
        latest = max(os.path.getmtime(os.path.join(_STATIC_DIR, f))
                     for f in ("app.js", "style.css", "sw.js"))
        return str(int(latest))
    except OSError:
        return _ASSET_VERSION
# Don't sort JSON keys: it's wasted work and crashes on any dict with mixed
# key types (e.g. integer prop lines alongside string keys).
app.json.sort_keys = False
store.init_db()


# ---- central error capture --------------------------------------------------
# The guarded failure points all note() themselves with stable IDs; these two
# hooks catch what nothing guarded. Together: nothing fails silently anymore.
@app.errorhandler(Exception)
def _unhandled(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e                          # 404s and friends are not failures
    code = f"HTTP-{request.endpoint or request.path}"
    errlog.note(code, e, path=request.path)
    return jsonify({"error": f"{type(e).__name__}: {e}", "error_id": code}), 500


def _thread_hook(args):
    """Uncaught exception in ANY background thread -- the recorders, the
    warmer, the scheduler, a build worker. These died in total silence."""
    try:
        name = getattr(args.thread, "name", None) or "unnamed"
        errlog.note(f"THREAD-{name}", args.exc_value)
    except Exception as e:
        # Meta-errors can't go to the ledger (that's what just failed), but
        # they must not vanish either -- stdout reaches the platform's logs.
        print(f"[errlog] thread hook could not record: {e!r}", flush=True)
    _orig_thread_hook(args)


_orig_thread_hook = threading.excepthook
if getattr(_orig_thread_hook, "_vigil_hook", False):
    # This module was RELOADED (tests do; a future dev server might): the
    # current hook is our own previous incarnation. Chaining to it recurses
    # forever, so fall back to the interpreter's pristine hook instead.
    _orig_thread_hook = threading.__excepthook__
_thread_hook._vigil_hook = True
threading.excepthook = _thread_hook


# ---- one worker owns the background work -----------------------------------
# The server ran a SINGLE gunicorn worker specifically so the recorders would
# start once. That is what made the platform's health check fail: /healthz is a
# static 200, but a static 200 still needs a thread, and Python's GIL means any
# CPU-bound work anywhere in the process -- a DFS optimize at 2,500 restarts, a
# contest sim, a combo build -- starves every other thread in it. The probe
# times out at five seconds, the platform calls the instance dead and restarts
# it. Nothing was actually broken; the box was just busy doing what you asked.
#
# The fix is more than one worker PROCESS, so a health check lands on a worker
# that isn't holding a GIL. That reintroduces the original problem -- N workers
# would each start the recorders and each grade the prediction log -- so the
# background jobs are claimed by exactly one of them via an exclusive file lock.
# Whoever wins holds the descriptor for the life of the process; the rest serve
# requests only. If the owner dies, the kernel drops the lock and the next
# worker to boot takes it over.
_BG_LOCK_FD = None


def _own_background_jobs():
    """True in exactly one worker process. Falls back to True where flock is
    unavailable (a single-process dev run has nothing to contend with)."""
    global _BG_LOCK_FD
    if _BG_LOCK_FD is not None:
        return True
    try:
        import fcntl
        path = os.path.join(os.environ.get("VIGIL_RUN_DIR") or "/tmp",
                            "vigil-background.lock")
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False              # another worker already owns them
        os.write(fd, str(os.getpid()).encode())
        _BG_LOCK_FD = fd              # held for the process lifetime, never closed
        return True
    except Exception:
        return True

# Optional: auto-pull the branch + restart when new commits land, so a push goes
# live with no manual step (set VIGIL_SELFUPDATE=1). No-ops on Render / anywhere
# without a git checkout — that path uses the platform's own autoDeploy.
# These two run at import time, which means once per WORKER -- so both are
# claimed by the background owner. A second self-updater would race the first
# into a git reset, and a second grader would double-write the prediction log.
_BG_OWNER = _own_background_jobs()

try:
    import selfupdate
    if _BG_OWNER:
        selfupdate.start()
except Exception as _e:
    errlog.note("APP-mod", _e)

# Prediction logger: capture each sim's Kalshi predictions and grade them when the
# market settles, so tennis/UFC (and more as they're wired) accrue the graded
# history the calibrator needs. Background, cheap (boards are cached).
try:
    import predlog
    if _BG_OWNER:
        predlog.start()
except Exception as _e:
    errlog.note("APP-mod-2", _e)


@app.after_request
def _security_headers(resp):
    """Baseline hardening headers for a public deployment."""
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    # Only assert HSTS once we're actually being served over HTTPS.
    if request.is_secure:
        resp.headers.setdefault("Strict-Transport-Security",
                                "max-age=31536000; includeSubDomains")
    return resp


# ---- slow-request log ------------------------------------------------------
# When the arbiter kills a worker for outliving --timeout, NOTHING says which
# request did it: the platform reports a failed health check and the app's own
# logs are silent, so the instance looks like it crashed at random. Time every
# request, name the slow ones, and keep the worst few in memory for /api/diag/slow.
# A request nearing the worker timeout is logged as the danger it is -- that is
# the one that takes the health check down with it.
# Background combo builds, keyed by the progress token the client already mints.
# A build is minutes of simulation and a request must never be, so the two are
# decoupled: the request starts a job and returns 202, the client polls.
_combo_jobs = {}
_combo_lock = threading.Lock()

# When this worker process started. A health-check alert has two very different
# causes that look identical from outside: the instance was RESTARTED (a deploy,
# a platform move) or it was genuinely STALLED. Uptime separates them -- if the
# worker answering is only seconds old, the probe that failed was aimed at a
# process that was being replaced, and nothing was wrong with the app.
_PROC_START = time.time()

_KILL_TIMEOUT = float(os.environ.get("VIGIL_WORKER_TIMEOUT") or 120)
_SLOW_LOG_S = float(os.environ.get("VIGIL_SLOW_LOG_S") or 10)
_slow_recent = []
_slow_lock = threading.Lock()


@app.before_request
def _slow_start():
    g._t0 = time.time()


@app.after_request
def _slow_finish(resp):
    t0 = getattr(g, "_t0", None)
    if t0 is None or request.path in _PROBE_PATHS:
        return resp
    dt = time.time() - t0
    if dt >= _SLOW_LOG_S:
        risky = dt >= _KILL_TIMEOUT * 0.5
        print(f"[slow] {dt:7.1f}s  {request.method} {request.full_path}"
              + ("   <-- over half the worker timeout; this is what kills a worker "
                 "and fails the health check" if risky else ""), flush=True)
        with _slow_lock:
            _slow_recent.append({"path": request.full_path, "seconds": round(dt, 1),
                                 "at": int(time.time()), "risky": risky})
            _slow_recent.sort(key=lambda r: -r["seconds"])
            del _slow_recent[12:]
    return resp


@app.route("/api/warm")
def _api_warm():
    """Is the app ready to build instantly, or is it still simulating?

    The answer used to be invisible: you opened the app, pressed Build, and
    only then discovered the board was cold. This reports the same counts the
    warmer is working through, so the page can say so up front."""
    date = request.args.get("date") or clock.today_et().isoformat()
    season = request.args.get("season") or date[:4]
    # The date being POLLED is the date being LOOKED AT -- tell the warmer, so
    # a user parked on tomorrow's slate gets tomorrow warmed, not today's.
    try:
        _note_slate_use(date, season)
    except Exception as _e:
        errlog.note("APP-api_warm", _e)
    try:
        games = baseball.analyze_slate(date, season, cached_only=True)
    except Exception:
        games = None
    slate_fresh = games is not None
    if games is None:
        # The board's 5-minute cache expiring is ROUTINE, but this endpoint
        # treated it like a cold start: the counts crashed to 0/0 mid-refresh
        # while seconds earlier they read 4/13, and the sims those counts
        # measure were still sitting on disk the whole time. The stale board
        # names the same games, and sims are keyed by game_pk -- so count from
        # it and say "refreshing", not "nothing exists".
        try:
            games, _stale_age = baseball.stale_slate(date, season,
                                                     max_age=6 * 3600)
        except Exception:
            games = None

    # What the warmer says it is doing, readable from EVERY worker. `at` only
    # counts when the warmer is on this same date; a heartbeat is only "alive"
    # while it is younger than the longest legitimately silent stretch (a board
    # build can hold the thread ~600s), and a recent error is worth showing.
    st = _warm_json_read(_WARM_STATUS)
    now = time.time()
    beat_s = round(now - st["ts"], 1) if st.get("ts") else None
    same_date = st.get("date") == date
    # `at` is only a live claim while the warmer is IN the sim phase: a worker
    # recycled mid-sim freezes the file with a matchup name inside, and showing
    # that beside a "building the board" note put two contradictory truths on
    # one bar.
    phase = st.get("phase") if same_date else None
    at = st.get("at") if (same_date and phase == "sim") else None
    err = (st.get("err")
           if st.get("err") and now - (st.get("err_ts") or 0) < 1800 else None)
    # No status file at all is normal for a young instance (the warmer's first
    # tick is a minute out) -- only call it stalled once this process is old
    # enough that a working warmer would certainly have written something.
    alive = (beat_s < 900 if beat_s is not None
             else now - _PROC_START < 900)

    if games is None:
        # Same shape as the ready branch -- a cold start must not answer with
        # fewer fields than a warm one, or the client special-cases it.
        return jsonify({"ready": False, "slate_ready": False, "total": 0,
                        "warm": 0, "at": at, "phase": phase,
                        "always_warm": bool(_WARM_ALWAYS),
                        "stalled": not alive, "warm_err": err,
                        "beat_s": beat_s,
                        "note": "building today's board…"})
    # PRE-GAME games only -- the same set a Build actually simulates. Counting
    # live games here (13) while the maker's bar counted pregame ones (9) put
    # two different denominators for the same work on one screen. A game under
    # way is priced by the live-resume path, not this cache; it neither needs
    # warming nor belongs in the count.
    todo = [g for g in games
            if (g.get("live") or {}).get("state") not in ("Final", "Live")]
    warm = sum(1 for g in todo if baseball._game_sim_cached(g))
    ready = bool(todo) and warm >= len(todo)
    return jsonify({"ready": ready,
                    "slate_ready": slate_fresh, "total": len(todo), "warm": warm,
                    "at": at, "phase": phase,
                    "always_warm": bool(_WARM_ALWAYS),
                    # Cold with no live warmer is the state that used to be
                    # invisible: the count sat at 0/N and nothing said why.
                    "stalled": (not ready and bool(todo) and not alive),
                    "warm_err": err, "beat_s": beat_s,
                    "note": ("ready" if ready
                             else "no games today" if not todo
                             else f"simulating {warm}/{len(todo)} games"
                             if slate_fresh
                             else "refreshing today's board…")})


@app.route("/api/progress")
def _api_progress():
    """How far a long build has actually got. The combo maker is one request, so
    the browser cannot observe it directly; the builder counts games as it
    finishes simulating each one and this reports that count."""
    tok = (request.args.get("token") or "")[:64]
    p = baseball.progress_get(tok) if tok else None
    if not p:
        return jsonify({"known": False})
    return jsonify({"known": True, "done": p["done"], "at": p.get("at", 0),
                    "total": p["total"], "cached": p["cached"], "phase": p["phase"],
                    "pass": p.get("pass", 1), "passes": p.get("passes", 1),
                    "elapsed_s": round(time.time() - p["started"], 1)})


# A background build must be able to DIE without freezing its bar forever.
# Job files live on the persistent disk, so a build killed mid-flight (deploy
# swap, --max-requests worker recycle, OOM) left status='running' there for
# good: every poll answered 202 'building', the claim was unwinnable (O_EXCL,
# first winner forever), and the owner watched 'simulated 1/5' sit frozen for
# 20 minutes. The builder now BEATS its job file from a side thread; a poll
# that finds a running job untouched for _JOB_DEAD_S takes it over and
# rebuilds -- riding the disk-cached sims the dead build already paid for.
_JOB_DEAD_S = 90                # beat is 20s; ~4 missed beats means dead


def _run_job(ptok, core, errcode):
    """Run a build in a daemon thread under its job token, heartbeating the
    job file so a killed worker is detectable instead of eternal."""
    def _bg():
        stop = threading.Event()

        def _beat():
            while not stop.wait(20):
                baseball.job_heartbeat(ptok)
        threading.Thread(target=_beat, daemon=True).start()
        try:
            res = core()
            baseball.job_finish(ptok, "done", result=res)
        except Exception as e:
            print(f"[job] build failed ({ptok}): {e!r}", flush=True)
            errlog.note(errcode, e, path=ptok)
            baseball.job_finish(ptok, "error", error=str(e))
        finally:
            stop.set()
    threading.Thread(target=_bg, daemon=True).start()


@app.route("/api/dfs/scoring")
def _api_dfs_scoring():
    """The DraftKings scoring card for a sport, rendered from the SAME constants
    the simulator scores with -- so what the card says and what the projection
    was built from cannot disagree."""
    import dk_scoring
    sport = (request.args.get("sport") or "ufc").lower()
    return jsonify({"sport": sport, "groups": dk_scoring.card(sport)})


@app.route("/api/diag/slow")
def _api_diag_slow():
    """The slowest requests this worker has served, worst first."""
    with _slow_lock:
        rows = list(_slow_recent)
    up = time.time() - _PROC_START
    # The state of the sim cache and its disk, because "warming stuck at 0/N"
    # has looked exactly like "the data disk is full" -- sims completing and
    # landing nowhere. One screenshot of this answers which it is.
    disk = {}
    try:
        import shutil
        d = getattr(baseball, "_SIM_DISK", None)
        if d and os.path.isdir(d):
            u = shutil.disk_usage(d)
            names = os.listdir(d)
            newest = max((os.stat(os.path.join(d, n)).st_mtime
                          for n in names), default=None)
            disk = {"sim_cache_dir": d,
                    "disk_free_mb": round(u.free / 1e6, 1),
                    "disk_total_mb": round(u.total / 1e6, 1),
                    "sim_files": len(names),
                    "newest_sim_age_s": (round(time.time() - newest, 1)
                                         if newest else None)}
        elif d:
            disk = {"sim_cache_dir": d, "note": "not created yet"}
    except Exception as e:
        disk = {"error": f"{type(e).__name__}: {e}"}
    return jsonify({"worker_timeout_s": _KILL_TIMEOUT, "logged_over_s": _SLOW_LOG_S,
                    "pid": os.getpid(), "owns_background": bool(_BG_OWNER),
                    # The top of the error ledger, so this one screenshot also
                    # answers "did anything actually break".
                    "errors_24h": errlog.summary(hours=24)[:8],
                    "warm_status": _warm_json_read(_WARM_STATUS),
                    "sim_disk": disk,
                    "sim_disk_err": getattr(baseball, "sim_disk_health",
                                            lambda: None)(),
                    "uptime_s": round(up, 1),
                    "uptime_human": (f"{up/3600:.1f}h" if up >= 3600
                                     else f"{up/60:.1f}m" if up >= 60 else f"{up:.0f}s"),
                    # The question every health-check alert raises. A worker only
                    # minutes old means the probe hit an instance being replaced
                    # (a deploy), not an app that hung.
                    "recently_restarted": up < 300,
                    "slowest": rows})


# ---- Fanfare: the owner's teams' latest finals ------------------------------
@app.route("/api/fanfare")
def api_fanfare():
    """The freshest FINAL result for the owner's teams (Angels + Steelers),
    so the app can throw a few seconds of emojis on the first open after a
    game ends. Only finals from the last ~36h count -- an old result must
    never trigger. The client remembers which game ids it has already
    celebrated (localStorage), so each final fires exactly once per device."""
    def build():
        out = {}
        try:
            today = clock.today_et()
            d = baseball._get(
                f"{baseball.STATS_BASE}/schedule?sportId=1&teamId=108"
                f"&startDate={(today - datetime.timedelta(days=1)).isoformat()}"
                f"&endDate={today.isoformat()}")
            fin = [g for day in d.get("dates", []) for g in day.get("games", [])
                   if g.get("status", {}).get("abstractGameState") == "Final"]
            if fin:
                g = fin[-1]
                home, away = g["teams"]["home"], g["teams"]["away"]
                us, them = (home, away) if home["team"]["id"] == 108 else (away, home)
                if us.get("isWinner") is not None:
                    out["angels"] = {
                        "id": g["gamePk"], "won": bool(us.get("isWinner")),
                        "score": f'{us.get("score")}-{them.get("score")}',
                        "opp": them["team"].get("name"),
                        "label": "ANGELS"}
        except Exception as e:
            errlog.note("FAN-laa", e)
        try:
            import racing
            sb = racing._get_json(
                "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
                timeout=12)
            for ev in sb.get("events") or []:
                comp = (ev.get("competitions") or [{}])[0]
                if (comp.get("status") or {}).get("type", {}).get("state") != "post":
                    continue
                try:
                    age = time.time() - datetime.datetime.fromisoformat(
                        ev["date"].replace("Z", "+00:00")).timestamp()
                except (KeyError, ValueError):
                    age = 0
                if age > 36 * 3600:
                    continue
                cs = comp.get("competitors") or []
                pit = next((c for c in cs
                            if (c.get("team") or {}).get("abbreviation") == "PIT"), None)
                if not pit:
                    continue
                them = next((c for c in cs if c is not pit), {})
                out["steelers"] = {
                    "id": ev.get("id"), "won": bool(pit.get("winner")),
                    "score": f'{pit.get("score")}-{them.get("score")}',
                    "opp": (them.get("team") or {}).get("displayName"),
                    "label": "STEELERS"}
                break
        except Exception as e:
            errlog.note("FAN-pit", e)
        return out
    import racing
    return jsonify(racing._cached(("fanfare",), 300, build) or {})


# ---- PC compute worker: adopt externally-computed sims ----------------------
# The user's desktop runs pc_worker.py on a loop: it simulates the slate with
# the SAME baseball._game_sim the warm loop uses (~32s a game there vs 200s+
# on this shared CPU) and uploads each finished pickle here. Everything
# downstream -- the combo maker, the warm loop, the boards -- adopts whatever
# is freshest on the sim disk, so the PC can only ever make things faster; if
# it is off or stale, this server computes for itself exactly as before.
def _pc_auth_ok():
    """The upload door REQUIRES the shared secret even when the app has no
    password set -- writes must never be open."""
    tok = request.headers.get("X-Sim-Token") or ""
    return bool(_SIM_TOKEN and tok and hmac.compare_digest(tok, _SIM_TOKEN))


@app.route("/api/art/have")
def api_art_have():
    """{name: age_s} for one artifact store (gamesim | boards | deep), plus the
    contract schema — the PC worker's inventory question, generalized."""
    if not _pc_auth_ok():
        return jsonify({"error": "auth"}), 403
    import artifacts
    kind = request.args.get("kind") or ""
    if artifacts.dir_for(kind) is None:
        return jsonify({"error": "bad kind"}), 400
    return jsonify({"have": artifacts.ages(kind), "schema": artifacts.SCHEMA})


@app.route("/api/art/upload", methods=["POST"])
def api_art_upload():
    """Adopt one externally-computed artifact into any of the three stores.
    Same trust model and gates as the game-sim door (see api_sim_upload):
    token required even with no app password, schema-versioned so a stale
    checkout is ignored, flat sanitized filenames only, size-capped."""
    if not _pc_auth_ok():
        return jsonify({"error": "auth"}), 403
    import artifacts
    kind = request.args.get("kind") or ""
    name = request.args.get("name") or ""
    try:
        schema = int(request.args.get("schema") or 0)
    except ValueError:
        return jsonify({"error": "bad params"}), 400
    if schema != artifacts.SCHEMA:
        errlog.note("PCUP-art-schema",
                    msg=f"{kind}/{name}: schema {schema} vs {artifacts.SCHEMA}")
        return jsonify({"error": "schema mismatch", "adopted": False,
                        "want": artifacts.SCHEMA}), 409
    if artifacts.dir_for(kind) is None or not artifacts.valid_name(name):
        return jsonify({"error": "bad kind/name"}), 400
    data = request.get_data(cache=False)
    if request.headers.get("Content-Encoding") == "gzip":
        import gzip as _gz
        try:
            data = _gz.decompress(data)
        except OSError:
            return jsonify({"error": "bad gzip"}), 400
    if not data or len(data) > 64_000_000:
        return jsonify({"error": "bad size"}), 400
    try:
        artifacts.write_raw(kind, name, data)
    except Exception as e:
        errlog.note("PCUP-art-write", e)
        return jsonify({"error": f"write failed: {type(e).__name__}",
                        "adopted": False}), 500
    return jsonify({"ok": True, "adopted": True, "kind": kind, "name": name,
                    "bytes": len(data)})


@app.route("/api/sim/have")
def api_sim_have():
    """{pk: age_s} of fresh sims on this server, plus the schema and commit it
    expects -- the PC worker's 'what do you still need' question."""
    if not _pc_auth_ok():
        return jsonify({"error": "auth"}), 403
    return jsonify({"have": baseball.sim_disk_ages(),
                    "schema": baseball.GAME_SIM_SCHEMA,
                    "commit": os.environ.get("RENDER_GIT_COMMIT", "")[:12]})


@app.route("/api/sim/upload", methods=["POST"])
def api_sim_upload():
    """Adopt one game sim computed on the PC. Gated three ways: the shared
    token, the schema version (a stale checkout gets rejected, never adopted),
    and a numeric game_pk (no path games). The body is the pickled sim, gzip
    optional. NOTE the trust model, stated plainly: a pickle is code-execution-
    equivalent when loaded, so this door is only as safe as SIM_TOKEN -- which
    is the app owner's own secret on the app owner's own machine, the same
    trust as the server trusting its own disk. Single-user by design."""
    if not _pc_auth_ok():
        return jsonify({"error": "auth"}), 403
    try:
        schema = int(request.args.get("schema") or 0)
        pk = int(request.args.get("pk") or 0)
    except ValueError:
        return jsonify({"error": "bad params"}), 400
    if schema != baseball.GAME_SIM_SCHEMA:
        errlog.note("PCUP-schema", msg=f"pk={pk}: schema {schema} vs "
                                       f"{baseball.GAME_SIM_SCHEMA} - PC checkout stale?")
        return jsonify({"error": "schema mismatch", "adopted": False,
                        "want": baseball.GAME_SIM_SCHEMA}), 409
    if pk <= 0:
        return jsonify({"error": "bad pk"}), 400
    data = request.get_data(cache=False)
    if request.headers.get("Content-Encoding") == "gzip":
        import gzip as _gz
        try:
            data = _gz.decompress(data)
        except OSError:
            return jsonify({"error": "bad gzip"}), 400
    if not data or len(data) > 16_000_000:
        return jsonify({"error": "bad size"}), 400
    try:
        baseball.sim_disk_write_raw(pk, data)
    except Exception as e:
        errlog.note("PCUP-write", e)
        return jsonify({"error": f"write failed: {type(e).__name__}", "adopted": False}), 500
    return jsonify({"ok": True, "adopted": True, "pk": pk,
                    "bytes": len(data)})


def _cgroup_mem():
    """(used_bytes, limit_bytes) from the container's cgroup, (None, None)
    where unavailable. v2 first, v1 fallback; a v1 'unlimited' sentinel
    (huge number) reads as no limit."""
    def _read(path):
        with open(path) as f:
            return f.read().strip()
    used = limit = None
    for u_path, l_path in (("/sys/fs/cgroup/memory.current",
                            "/sys/fs/cgroup/memory.max"),
                           ("/sys/fs/cgroup/memory/memory.usage_in_bytes",
                            "/sys/fs/cgroup/memory/memory.limit_in_bytes")):
        try:
            used = int(_read(u_path))
            raw = _read(l_path)
            limit = None if raw == "max" else int(raw)
            if limit is not None and limit > 1 << 48:
                limit = None                     # v1 "unlimited" sentinel
            break
        except (OSError, ValueError):
            used = limit = None
    return used, limit


def _proc_tree_mb(top=None):
    """Per-process RSS for EVERYTHING in this container, fattest first --
    workers, sim children, the slate builder. An instance-level 'exceeded
    memory' kill is about their SUM, which no single worker's own RSS can
    answer."""
    procs = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            rss = 0
            with open(f"/proc/{pid}/status") as f:
                for ln in f:
                    if ln.startswith("VmRSS"):
                        rss = int(ln.split()[1])
                        break
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmd = f.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
            procs.append({"pid": int(pid), "rss_mb": round(rss / 1024, 1),
                          "cmd": cmd[:120] or "?"})
        except OSError:
            continue                     # raced a process that just exited
    procs.sort(key=lambda p: p["rss_mb"], reverse=True)
    return procs[:top] if top else procs


@app.route("/api/diag/mem")
def _api_diag_mem():
    """The INSTANCE's memory picture: every process's RSS plus the cgroup
    total against its limit, and the entry count of each in-process cache big
    enough to matter (those are per-worker -- refresh a few times, gunicorn
    rotates workers across requests). Built after an instance-level 'exceeded
    memory' kill: the first question is which process and which cache."""
    caches = {"baseball": len(getattr(baseball, "_cache", {}) or {})}
    import importlib
    for mod, attr, label in (("racing", "_form_cache", "racing_form"),
                             ("savant", "_cache", "savant"),
                             ("value", "_cache", "value"),
                             ("weather", "_cache", "weather"),
                             ("kalshi", "_move_cache", "kalshi_moves")):
        try:
            caches[label] = len(getattr(importlib.import_module(mod), attr))
        except Exception as e:
            caches[label] = f"err {type(e).__name__}"
    try:
        procs = _proc_tree_mb(top=12)
    except Exception as e:
        procs = f"err {type(e).__name__}"      # diag must still answer
    used, limit = (None, None)
    try:
        used, limit = _cgroup_mem()
    except Exception as _e:
        errlog.note("APP-diag-cgroup", _e)
    return jsonify({"pid": os.getpid(),
                    "uptime_s": round(time.time() - _PROC_START, 1),
                    "cgroup_used_mb": round(used / 1048576, 1) if used else None,
                    "cgroup_limit_mb": round(limit / 1048576, 1) if limit else None,
                    "procs": procs,
                    "cache_entries": caches})


# The watchdog turns the NEXT kill from guesswork into data: sampled minutes
# before a death, the ledger (on the persistent disk) holds which processes
# were fat. Render's own email says only "exceeded memory".
_MEM_WARN_FRAC = float(os.environ.get("VIGIL_MEM_WARN_FRAC") or 0.80)
_MEM_WATCH_S = 60


def _start_mem_watchdog():
    def _loop():
        last = 0.0
        while True:
            time.sleep(_MEM_WATCH_S)
            try:
                used, limit = _cgroup_mem()
                if not used or not limit or used / limit < _MEM_WARN_FRAC:
                    continue
                if time.time() - last < 600:
                    continue                   # one note per surge, not sixty
                last = time.time()
                top = "; ".join(f"{p['rss_mb']}MB pid{p['pid']} {p['cmd'][:48]}"
                                for p in _proc_tree_mb(top=6))
                errlog.note("MEM-high",
                            msg=f"{used >> 20}/{limit >> 20}MB: {top}")
            except Exception as _e:
                errlog.note("APP-memwatch", _e)
    threading.Thread(target=_loop, daemon=True).start()


@app.route("/api/errors")
def _api_errors():
    """The error ledger, readable from the phone: per-code rollup plus the
    newest raw entries. ?hours=24 ?code=SLATE-build ?limit=50 filter it."""
    hours = request.args.get("hours", type=float) or 24
    return jsonify({"summary": errlog.summary(hours=hours),
                    "recent": errlog.recent(
                        limit=request.args.get("limit", type=int) or 50,
                        code=request.args.get("code"), hours=hours)})


@app.route("/api/errors/export")
def _api_errors_export():
    """The whole recent ledger as JSON, for the scheduled GitHub Action to
    commit under errors/ on the sim-history branch. Same PULL direction as the
    history snapshot: the app holds no GitHub credentials."""
    try:
        return jsonify(errlog.export_bundle(days=7))
    except Exception as e:
        return jsonify({"error": f"export failed: {e}"}), 502


# Browser-side errors were completely invisible: a JS exception broke a page
# feature and the server never heard about it. The front end reports its own
# uncaught errors here (rate-limited client-side, deduped server-side).
_CLIENT_ERR_MAX = 2000


@app.route("/api/errors/client", methods=["POST"])
def _api_errors_client():
    try:
        d = request.get_json(silent=True) or {}
        code = str(d.get("code") or "JS-error")[:40]
        if not code.startswith("JS-"):
            code = "JS-" + code
        msg = str(d.get("msg") or "")[:_CLIENT_ERR_MAX]
        src = str(d.get("src") or "")[:300]
        errlog.note(code, msg=f"{msg} @ {src}" if src else msg,
                    path=str(d.get("page") or "")[:200])
    except Exception as e:
        errlog.note("APP-client-err-intake", e)
    return jsonify({"ok": True})


@app.route("/healthz")
def _healthz():
    return Response("ok", mimetype="text/plain")


@app.route("/api/version")
def _api_version():
    """Current build token so an open page can detect a deploy and offer a
    one-tap refresh. Changes whenever the front-end files change on disk (after a
    pull/redeploy)."""
    return jsonify({"v": _asset_version()})


@app.route("/robots.txt")
def _robots():
    # Keep the app out of search indexes while it's pre-launch / private.
    return Response("User-agent: *\nDisallow: /\n", mimetype="text/plain")

# Optional password protection (recommended when exposing it over a tunnel).
# Set APP_PASSWORD (and optionally APP_USER) in the environment to turn it on.
APP_USER = os.environ.get("APP_USER", "kalshi")
APP_PASSWORD = os.environ.get("APP_PASSWORD")


# ---- staying logged in ------------------------------------------------------
# Basic auth alone means the phone re-prompts constantly: browsers drop the
# cached credentials whenever the tab is evicted, which on a phone is every time
# you switch apps for a while. So a successful login also mints a signed cookie
# and that carries you afterwards.
#
# The cookie is an HMAC over its own expiry, keyed by the server secret -- there
# is nothing in it to forge and nothing to steal a password from. HttpOnly keeps
# script away from it, Secure keeps it off plaintext, SameSite=Lax keeps it off
# cross-site requests. Rotating SECRET_KEY invalidates every outstanding cookie,
# which is the revoke button if a device is ever lost.
_REMEMBER_COOKIE = "vigil_auth"
_REMEMBER_DAYS = int(os.environ.get("VIGIL_REMEMBER_DAYS") or 90)


def _auth_key():
    """Signing key EVERY WORKER agrees on.

    app.secret_key falls back to os.urandom when SECRET_KEY is unset, and with
    three workers that is three different keys -- a cookie minted by one would
    be rejected by the other two and the login would appear to 'not stick'.
    Deriving from the password instead keeps every worker in agreement."""
    k = os.environ.get("SECRET_KEY")
    if k:
        return k.encode()
    return hashlib.sha256(("vigil-remember:" + (APP_PASSWORD or "")).encode()).digest()


def _mint_remember(days=None):
    exp = int(time.time() + (days or _REMEMBER_DAYS) * 86400)
    sig = hmac.new(_auth_key(), str(exp).encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def _remember_ok(tok):
    try:
        exp_s, sig = (tok or "").split(".", 1)
        exp = int(exp_s)
    except (ValueError, AttributeError):
        return False
    if exp < time.time():
        return False
    want = hmac.new(_auth_key(), str(exp).encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, want)


def _trusted_ip(addr):
    """True for an address inside VIGIL_TRUSTED_IPS (comma-separated IPs or
    CIDRs, e.g. "203.0.113.4,192.168.1.0/24").

    Sensible for a home connection with a stable address. NOT sensible for a
    phone: carrier IPs rotate constantly and sit behind carrier-grade NAT, so
    the allowlist would both break often and hand access to everyone sharing
    that pool. Off unless explicitly set.

    SECURITY: remote_addr comes through ProxyFix, which trusts ONE
    X-Forwarded-For hop. Behind Render or Caddy that header is set by the
    proxy and this is sound. Exposed DIRECTLY to the internet (no proxy),
    any client can send X-Forwarded-For: <your-ip> and walk straight in --
    so only enable this behind a real proxy. The cookie path does not have
    this problem."""
    nets = (os.environ.get("VIGIL_TRUSTED_IPS") or "").strip()
    if not nets or not addr:
        return False
    import ipaddress
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    for part in nets.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if ip in ipaddress.ip_network(part, strict=False):
                return True
        except ValueError:
            continue
    return False


# Shared secret for the GitHub Actions workflows (history snapshot, error-log
# snapshot, sim trigger). The nightly workflow has SENT an X-Sim-Token header
# since it was written -- but nothing ever CHECKED it, so once APP_PASSWORD was
# set every workflow fetch got a silent 401 and the snapshots quietly stopped.
# Set the same value as a Render env var (SIM_TOKEN) and a GitHub Actions
# secret (SIM_TOKEN) and the automation authenticates itself.
_SIM_TOKEN = os.environ.get("SIM_TOKEN") or ""


@app.before_request
def _auth():
    if not APP_PASSWORD:
        return
    if request.path in ("/healthz", "/robots.txt"):   # platform probes, no creds
        return
    tok = request.headers.get("X-Sim-Token") or ""
    if _SIM_TOKEN and tok and hmac.compare_digest(tok, _SIM_TOKEN):
        return                                        # the workflows' door
    if _trusted_ip(request.remote_addr or ""):
        return
    if _remember_ok(request.cookies.get(_REMEMBER_COOKIE)):
        return
    a = request.authorization
    # Constant-time compare so a wrong password can't be teased out by timing.
    ok = a and hmac.compare_digest(str(a.username or ""), APP_USER) \
           and hmac.compare_digest(str(a.password or ""), APP_PASSWORD)
    if not ok:
        return Response("Login required", 401,
                        {"WWW-Authenticate": 'Basic realm="Vigil"'})
    g._mint_remember = True        # logged in properly -> remember this device


@app.after_request
def _set_remember(resp):
    if getattr(g, "_mint_remember", False):
        resp.set_cookie(_REMEMBER_COOKIE, _mint_remember(),
                        max_age=_REMEMBER_DAYS * 86400, httponly=True,
                        secure=bool(request.is_secure), samesite="Lax")
    return resp


@app.route("/logout")
def _logout():
    """Forget this device. The password still works; this just drops the cookie."""
    resp = Response("Signed out of this device.", mimetype="text/plain")
    resp.delete_cookie(_REMEMBER_COOKIE)
    return resp


def _tier():
    """The subscription tier for this request. Gating is OFF by default, so this
    returns the unlimited 'owner' tier until TIERS_ENFORCED is set."""
    return tiers.resolve(request.cookies.get("tier"), request.cookies.get("owner_key"))


def _locked(feature):
    """If the current tier can't use `feature`, return a 402 JSON response to
    return from the endpoint; otherwise None."""
    tier = _tier()
    if tiers.has_feature(tier, feature):
        return None
    need = tiers.feature_tier(feature)
    return jsonify({"error": "upgrade_required", "feature": feature,
                    "required_tier": need, "current_tier": tier,
                    "message": f"{feature.replace('_', ' ').title()} needs the "
                               f"{tiers.TIERS[need]['label']} tier."}), 402


@app.route("/api/tiers")
def api_tiers():
    return jsonify(tiers.public(_tier()))

# Start the background Kalshi recorder exactly once, on the first request. This
# works the same under the dev server, gunicorn, or any host (the __main__ block
# is not run by gunicorn, so we can't rely on it).
_rec_lock = threading.Lock()
_rec_started = False


def _ensure_recorder():
    global _rec_started
    if _rec_started:
        return
    with _rec_lock:
        if _rec_started:
            return
        _rec_started = True
        try:
            # EVERY worker registers the deep jobs and loads the deep board --
            # the rerun button, the status poll and the board itself land on
            # whichever worker gunicorn picks. Only the RUNNING stays singular.
            _register_deep_sims()
        except Exception as _e:
            errlog.note("APP-ensure_recorder", _e)
        if not _BG_OWNER:
            return          # a sibling worker owns the recorders and the sims
        try:
            import recorder
            recorder.start_background()
        except Exception as _e:
            errlog.note("APP-ensure_recorder-2", _e)
        try:
            import mlb_recorder
            mlb_recorder.start_background()
        except Exception as _e:
            errlog.note("APP-ensure_recorder-3", _e)
        try:
            _init_deep_sims()
        except Exception as _e:
            errlog.note("APP-ensure_recorder-4", _e)
        try:
            _start_slate_warmer()
        except Exception as _e:
            errlog.note("APP-ensure_recorder-5", _e)
        try:
            _start_mem_watchdog()
        except Exception as _e:
            errlog.note("APP-ensure_recorder-6", _e)


# An instance that sees only health probes must STILL run its nightlies. The
# bootstrap above fires on the first real request precisely so probes never pay
# for it -- but gunicorn recycles workers every few hundred requests, probes
# included, so overnight the background owner was replaced by a fresh worker
# that then waited for a human. Nobody browses at midnight; the scheduler was
# simply not running when the nightly came due, and the deep board quietly aged
# 32 hours. A delayed thread bootstraps each worker shortly after it starts:
# probes still never pay, and the heavy jobs keep their own startup grace.
def _bootstrap_unprompted():
    time.sleep(90)                 # well past boot and the first health probes
    try:
        _ensure_recorder()
    except Exception as _e:
        errlog.note("APP-bootstrap_unprompted", _e)


# Only in a real SERVER process. Anything that merely imports app -- the test
# suites, a one-off script, a REPL -- must not sprout recorders and schedulers
# ninety seconds in (the guard suite caught exactly that: background threads
# churning the caches mid-assertion). gunicorn's arbiter stamps SERVER_SOFTWARE
# into the workers' environment; the dev server still bootstraps on its first
# real request like always.
if ("gunicorn" in (os.environ.get("SERVER_SOFTWARE") or "")
        and (os.environ.get("VIGIL_AUTOBOOT") or "1") != "0"):
    threading.Thread(target=_bootstrap_unprompted, daemon=True).start()


# ---- keeping the board warm while you're actually using it -----------------
# The instance never sleeps (Render Starter is always-on), but the SLATE does:
# its cache lives 300s, which is shorter than a trip to the exchange. So the
# process stayed hot and the data still went cold, and every return paid for a
# rebuild. Stale-while-revalidate hides that; this removes it.
#
# Warming only while the app is IN USE is the whole point. Rebuilding every five
# minutes around the clock would spawn a ~175 MB child forever on a 512 MB box
# for nobody's benefit; a 30-minute window means an evening of place-a-bet /
# come-back stays instant and the box idles overnight. The build takes
# deep_cache.HEAVY_BUILD, so it can never race the nightly season sim -- it
# waits its turn instead of pushing the instance into an OOM.
_WARM_WINDOW = int(os.environ.get("VIGIL_WARM_WINDOW") or 1800)   # 0 disables
# KEEP IT WARM ALL THE TIME, not just for half an hour after someone looks.
# The activity window meant that picking the phone up after a break landed on a
# completely cold cache and a wait measured in minutes -- the warmer had stopped
# long before. The instance is always-on and paid for by the month either way;
# the only real cost is CPU it would otherwise spend idling, and games that have
# finished are skipped, so an empty overnight slate costs nothing.
_WARM_ALWAYS = (os.environ.get("VIGIL_WARM_ALWAYS") or "1") != "0"

# EVERYTHING THE WARMER REPORTS LIVES ON DISK, like everything else that has to
# cross workers. The status used to sit in each worker's memory, so the worker
# doing the warming knew which game it was on and whether anything had failed --
# and the worker answering /api/warm knew none of it. The bar showed a count
# (disk-derived, honest) and nothing else, and when warming stalled the count
# just sat there with no way to tell "still simulating game one" from "the
# warmer died an hour ago". Same fix as the sims, the board and the jobs:
# publish it where every worker can read it. /tmp deliberately -- it is shared
# by the workers, wiped with the instance, and immune to the data disk filling.
_WARM_STATUS = os.path.join(os.environ.get("VIGIL_RUN_DIR") or "/tmp",
                            "vigil-warm-status.json")
_WARM_VIEWED = os.path.join(os.environ.get("VIGIL_RUN_DIR") or "/tmp",
                            "vigil-warm-viewed.json")


def _warm_json_read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh) or {}
    except Exception:
        return {}


def _warm_json_write(path, data):
    try:
        import tempfile
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".",
                                   suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
    except Exception as _e:
        errlog.note("APP-warm_json_write", _e)


def _warm_status(**kw):
    """Merge-update the shared warm status. Every write refreshes the heartbeat,
    so 'how stale is ts' answers 'is the warmer alive'."""
    st = _warm_json_read(_WARM_STATUS)
    st.update(kw)
    st["ts"] = time.time()
    st["pid"] = os.getpid()
    _warm_json_write(_WARM_STATUS, st)


def _note_slate_use(date, season):
    """Record which board is being LOOKED AT, for the warmer to follow.

    On disk, not in memory: the request lands on whichever worker gunicorn
    picks, and the warmer runs in exactly one. Kept in memory this only ever
    reached the warmer on a ~1-in-workers coincidence -- a user watching
    tomorrow's slate was warming today's."""
    v = _warm_json_read(_WARM_VIEWED)
    if v.get("date") == date and time.time() - (v.get("ts") or 0) < 120:
        return                      # polled every 5s; don't rewrite a fresh note
    _warm_json_write(_WARM_VIEWED, {"date": date, "season": str(season),
                                    "ts": time.time()})


def _warm_pick_key():
    """(date, season) the warmer should work on, or None to idle.

    The board someone is viewing wins while the note is fresh; always-warm
    falls back to today so the first visitor finds it ready. Only today or the
    near future is honoured -- a stale picker left on last week warms nothing
    (every game is Final) but would still pay to build that dead board."""
    v = _warm_json_read(_WARM_VIEWED)
    today = clock.today_et().isoformat()
    horizon = (clock.today_et() + datetime.timedelta(days=7)).isoformat()
    fresh = v and time.time() - (v.get("ts") or 0) <= max(_WARM_WINDOW, 1800)
    if fresh and today <= (v.get("date") or "") <= horizon:
        return v["date"], (v.get("season") or v["date"][:4])
    if _WARM_ALWAYS:
        return today, today[:4]
    if fresh:                       # window mode: recent look at today
        return today, today[:4]
    return None                     # window mode, nobody's looking


def _warm_game_sims(date, season):
    """Pre-run the per-game simulations the COMBO MAKER needs.

    The slate board and the combo maker use different engines: the board's
    per-game work is a few seconds, the maker's is a full 4,000-run
    correlated simulation. Measured, that is ~32s a game on a fast desktop and
    over 200s on a single shared cloud CPU -- so the first Build after opening
    the app paid minutes of simulation while the user watched a progress bar,
    even on a one-game slate.

    Nothing about that work depends on the user's settings: the floors, the leg
    count and the payout target all filter candidates the simulation has already
    produced. So it does not belong on the click. Warming it here moves the wait
    off the button entirely -- by the time anyone presses Build the sims are
    cached and the answer is about a second.

    Deliberately serial and unhurried: one game at a time, checking between each
    that somebody is still using the app. Returns True when a board existed to
    warm from, so the caller knows a build would help."""
    try:
        games = baseball.analyze_slate(date, season, cached_only=True)
    except Exception:
        games = None
    if games is None:
        return False
    # Pre-game only: a game under way is priced by the live-resume path, so
    # warming its pregame sim buys nothing and costs ~200s of the shared CPU
    # in the evening window that can least afford it.
    todo = [g for g in games
            if (g.get("live") or {}).get("state") not in ("Final", "Live")]
    warm = sum(1 for g in todo if baseball._game_sim_cached(g))
    _warm_status(phase="sim", date=date, total=len(todo), warm=warm, at=None)
    for gm in todo:
        if not _WARM_ALWAYS and _warm_pick_key() is None:
            _warm_status(phase="idle", at=None)
            return True                 # nobody's looking any more
        try:
            if baseball._game_sim_cached(gm):
                continue
            _warm_status(phase="sim", at=gm.get("matchup") or gm.get("game_pk"))
            baseball._game_sim(gm)       # the expensive bit, done off the click
            warm = sum(1 for x in todo if baseball._game_sim_cached(x))
            _warm_status(phase="sim", warm=warm, at=None)
        except Exception as e:
            # The old bare `continue` is how a warmer that failed every game
            # looked exactly like one that was working: 0/9 forever with the
            # errors thrown away. Still continue -- one broken game must not
            # strand the other eight -- but the LAST error travels to the bar.
            _warm_status(err=f"{gm.get('matchup') or gm.get('game_pk')}: "
                             f"{type(e).__name__}: {e}"[:300],
                         err_ts=time.time())
            errlog.note("WARM-game-sim", e,
                        path=str(gm.get("matchup") or gm.get("game_pk")))
            continue
    # Sim writes that failed (a full data disk, most likely) surface the same
    # way; without this the sims complete, land nowhere, and the count reads 0.
    dh = getattr(baseball, "sim_disk_health", lambda: None)()
    if dh:
        _warm_status(err=f"sim cache write failed: {dh}"[:300],
                     err_ts=time.time())
    _warm_status(phase="idle", at=None)
    return True


def _warm_tick():
    """One pass of the warmer: sims first, board freshness second.

    The old order rebuilt the slate before warming, in the same thread -- so
    whenever the board build ran long (it waits on the one-heavy-build gate,
    which the nightly season sim can hold for the better part of an hour) the
    expensive game sims silently queued behind it and the bar froze at 0/N
    with no explanation. Sims need only SOME board naming today's games, not a
    fresh one, so they go first; the rebuild still happens, labelled, after."""
    key = _warm_pick_key()
    if key is None:
        return                        # window mode and nobody's looking
    date, season = key
    warmed = _warm_game_sims(date, season)
    _, age = baseball.stale_slate(date, season, max_age=None)
    # Rebuild just BEFORE it expires, so a user who returns at any moment
    # finds a board that is fresh rather than one that is merely recent.
    if age is None or age >= baseball._SLATE_TTL - 90:
        _warm_status(phase="board", date=date, at=None)
        kicked = False
        with _slate_lock:
            if key not in _slate_inflight:
                _slate_inflight.add(key)
                kicked = True
        if kicked:
            try:
                baseball.analyze_slate(date, season)
            except Exception as e:
                _warm_status(err=f"board build: {type(e).__name__}: {e}"[:300],
                             err_ts=time.time())
                errlog.note("WARM-board-build", e)
            finally:
                with _slate_lock:
                    _slate_inflight.discard(key)
        if not warmed:                # cold boot: board just arrived, use it
            _warm_game_sims(date, season)
    _warm_status(phase="idle", at=None)


def _start_slate_warmer():
    if _WARM_WINDOW <= 0:
        return

    def _loop():
        while True:
            time.sleep(60)
            try:
                _warm_tick()
            except Exception as e:
                # A warmer must never take the app down -- but a warmer that
                # dies every tick must not be indistinguishable from one that
                # is working. Record, then keep going.
                try:
                    _warm_status(err=f"tick: {type(e).__name__}: {e}"[:300],
                                 err_ts=time.time())
                except Exception as _e:
                    errlog.note("APP-loop", _e)

    threading.Thread(target=_loop, daemon=True).start()


# A healthy build sits near 0.96 career coverage. Below this the roster
# hydration came back partial and the projection is tracking record, not talent.
_MIN_CAREER_FRAC = float(os.environ.get("VIGIL_MIN_CAREER_FRAC") or 0.70)


def _register_deep_sims():
    """Register the heavy season sims and load the persisted MLB result.

    Runs in EVERY worker, not just the background owner. Registration only
    fills a dict with closures -- the expensive part is running them, and that
    stays with the owner's scheduler. What registration buys the other workers
    is correctness: the rerun button, the status poll and the deep board all
    land on whichever worker gunicorn picks, and a worker with an empty job
    table answered them with "started: false", "running: false" and a 409 --
    which is how "I hit rerun now and nothing happens" happened 2 clicks in 3."""
    import deep_cache
    if "mlb_deep" in deep_cache._jobs:      # already registered in this worker;
        _deep_refresh()                     # re-registering would blank a live
        return                              # job's running flag

    def run_mlb():
        import deep_season
        season = str(clock.today_et().year)
        # Capture the profiles this run actually used, so the day's history is
        # diffed against the same rosters the numbers came from rather than a
        # second fetch that may have moved underneath us.
        profs = {}
        agg = deep_season.run_deep(season, n_seasons=4000, ret_profiles=profs)
        # Refuse to publish a run built on incomplete data. The roster call
        # hydrates season AND career stats together; career is what regresses a
        # player toward true talent. When that hydration comes back partial --
        # observed live, where the same code produced a materially different
        # board on the deployed host than on a workstation -- every rate is taken
        # at face value and the projection tracks each team's RECORD instead of
        # its talent. The board still renders, fully confident and wrong, which is
        # the worst way for this to fail. Returning None leaves the previous good
        # run in the cache rather than overwriting it with this one.
        q = agg.get("quality") or {}
        if q.get("career_frac", 0) < _MIN_CAREER_FRAC:
            app.logger.warning(
                "deep run REJECTED: career coverage %.0f%% (need %.0f%%), "
                "%d/%d teams ok - keeping the previous board",
                100 * q.get("career_frac", 0), 100 * _MIN_CAREER_FRAC,
                q.get("teams_ok", 0), q.get("teams", 0))
            return None
        _deep["agg"] = agg
        _deep["season"] = season
        # Snapshot + attribution. Best-effort by design: the deep run is the
        # product, and a history failure must never cost the night's numbers.
        try:
            import deep_history
            deep_history.build_day(agg, season, profs)
        except Exception as _e:
            errlog.note("APP-run_mlb", _e)
        # Futures snapshot + market-coherence flags: write down what the model
        # AND both venues said tonight, so October grades against a record
        # instead of a memory. Same best-effort rule.
        try:
            import coherence
            coherence.snapshot(season)
        except Exception as _e:
            errlog.note("APP-run_mlb-2", _e)
        # Refresh the umpire zone table off the season's finished games. Same
        # best-effort rule; a stale table is a slightly old tendency, a failed
        # deep run is the night's numbers.
        try:
            import ump_build
            t = ump_build.build()
            if t:
                ump_build.save(t)
        except Exception as _e:
            errlog.note("APP-run_mlb-3", _e)
        return {"agg": agg, "season": season}

    def run_f1():
        import racing_sim
        return racing_sim.sim_f1(3000)

    def run_nascar():
        import racing_sim
        return racing_sim.sim_nascar(3000)

    def run_pro(league):
        def run():
            import pro_sim
            return pro_sim.project(league, 4000)
        return run

    def run_nfl_season():
        import nfl_season
        return nfl_season.run_season(n=4000)

    def run_cfb():
        import cfb
        return cfb.run_season(n=4000)

    def run_model_trust():
        """Re-measure how far each sport's model may depart from the market, by
        replaying point-in-time history. Stores the fitted blend weights."""
        import model_trust
        model_trust.refresh()
        return model_trust.load()

    deep_cache.register("mlb_deep", run_mlb)

    def run_nightly_extras():
        """The light nightly work that must run on the SERVER even when the PC
        worker delivered the heavy deep run (a fresh uploaded mlb_deep.pkl
        makes the daily scheduler skip run_mlb entirely): the futures
        coherence snapshot and the umpire table write into the repo directory
        and the GitHub history flow, which the PC's artifact sync doesn't
        carry. Cheap (fetch + write), idempotent per date, so running it on a
        night run_mlb ALSO ran costs nothing."""
        season = str(clock.today_et().year)
        try:
            import coherence
            coherence.snapshot(season)
        except Exception as _e:
            errlog.note("APP-extras-coherence", _e)
        try:
            import ump_build
            t = ump_build.build()
            if t:
                ump_build.save(t)
        except Exception as _e:
            errlog.note("APP-extras-ump", _e)
        return {"at": time.time()}
    deep_cache.register("mlb_nightly_extras", run_nightly_extras)
    deep_cache.register("f1", run_f1)
    deep_cache.register("nascar", run_nascar)
    deep_cache.register("nfl_season", run_nfl_season)
    deep_cache.register("cfb", run_cfb)
    deep_cache.register("model_trust", run_model_trust)
    # Every pro-league season is played out game by game with its own engine
    # (drive-level football, possession basketball, shot-event hockey). Each run
    # returns None outside its own season window (pro_sim.SEASON_WINDOW) before
    # fetching anything, so an out-of-season league costs a date comparison
    # rather than a schedule pull and 4,000 simulated seasons.
    for _lg in ("nfl", "nba", "nhl"):
        deep_cache.register(_lg, run_pro(_lg))
    # Restore the MLB deep run from disk so a restart doesn't lose it.
    _deep_refresh()


def _deep_refresh():
    """Adopt a newer on-disk deep run into this worker's memory.

    The nightly saves in whichever worker ran it; every other worker's copy
    would serve yesterday's board forever without this. One os.stat when
    nothing changed; the multi-MB unpickle only when the file really moved."""
    import deep_cache
    try:
        m = os.stat(deep_cache._path("mlb_deep")).st_mtime
    except OSError:
        return
    if _deep.get("mtime") == m and _deep.get("agg"):
        return
    payload, _ts = deep_cache.load("mlb_deep")
    if payload:
        _deep["agg"] = payload.get("agg")
        _deep["season"] = payload.get("season")
        _deep["mtime"] = m


def _init_deep_sims():
    """Owner-only: restore the run history and start the nightly scheduler.
    (Job registration happens in every worker via _register_deep_sims.)"""
    import deep_cache
    _register_deep_sims()
    # This host has no persistent disk, so the deep cache starts empty after every
    # restart and redeploy. Pull the repo's copy of the run history back before
    # the scheduler starts, so a restart doesn't silently empty the calendar.
    # Best-effort: a failure here leaves the app exactly as it was.
    def _restore_history():
        try:
            import deep_history
            deep_history.restore_from_github()
        except Exception as _e:
            errlog.note("APP-restore_history", _e)
    threading.Thread(target=_restore_history, daemon=True).start()
    # The scheduler alone decides when heavy work runs, and it runs it one job at
    # a time after a startup grace period. There used to be an eager warm-up loop
    # here that fired four uncached season sims immediately -- on a fresh instance
    # every job is stale, so it raced the scheduler and stacked sims on top of a
    # container that was still being health-checked. The scheduler already picks
    # these up; it just does it without taking the instance down.
    deep_cache.start_scheduler()


_PROBE_PATHS = ("/healthz", "/robots.txt")


@app.before_request
def _bootstrap_recorder():
    # Platform probes must NOT trigger the bootstrap. The health check is the
    # first request a fresh instance ever sees, so hanging the recorders and the
    # sim scheduler off it means the probe pays for all of it -- and if that
    # exceeds the platform's timeout the instance is killed and restarted before
    # it can finish, forever. Probes stay a static 200; the first REAL request
    # starts the background work.
    if request.path in _PROBE_PATHS:
        return
    _ensure_recorder()


def _minutes_to_close(close_time):
    return max(0.0, (close_time - time.time()) / 60.0)


def _signal_for(coin, threshold, direction, close_time, yes_price_cents):
    spot = prices.get_spot(coin)
    candles = prices.get_candles(coin, granularity=60)
    mins = _minutes_to_close(close_time)
    sig = odds.compute_signal(spot, candles, threshold, direction, mins, yes_price_cents)
    return sig


def _auto_resolve(market):
    """Resolve a market if its window has closed and it isn't resolved yet."""
    if market["resolved"]:
        return market
    if time.time() < market["close_time"]:
        return market
    try:
        price = prices.get_price_at(market["coin"], market["close_time"])
    except Exception:
        return market
    return store.resolve_market(market["id"], price) or market


@app.route("/")
def index():
    # Cache-bust static assets by their on-disk mtime, so a `git pull` alone
    # busts the cache (no server restart needed), and tell the browser not to
    # cache the page itself, so a reload always pulls the latest UI.
    resp = Response(render_template("index.html", v=_asset_version()))
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


@app.route("/sw.js")
def service_worker():
    # Served from root so the service worker's scope covers the whole app. Never
    # cache the SW script itself, so a new version is picked up on next load.
    resp = app.send_static_file("sw.js")
    resp.headers["Content-Type"] = "application/javascript"
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


@app.route("/api/coins")
def api_coins():
    return jsonify(sorted(prices.SUPPORTED_COINS.keys()))


@app.route("/api/quote")
def api_quote():
    """Ad-hoc signal without saving — for the live preview as you type."""
    try:
        coin = request.args.get("coin", "BTC").upper()
        threshold = float(request.args["threshold"])
        direction = request.args.get("direction", "above")
        close_time = float(request.args["close_time"])
        yp = request.args.get("yes_price_cents")
        yes_price = float(yp) if yp not in (None, "", "null") else None
    except (KeyError, ValueError) as e:
        return jsonify({"error": f"bad params: {e}"}), 400
    try:
        sig = _signal_for(coin, threshold, direction, close_time, yes_price)
    except Exception as e:
        return jsonify({"error": str(e)}), 502
    sig["coin"] = coin
    sig["close_time"] = close_time
    return jsonify(sig)


@app.route("/api/markets", methods=["POST"])
def api_create_market():
    data = request.get_json(force=True, silent=True) or {}
    try:
        coin = str(data["coin"]).upper()
        threshold = float(data["threshold"])
        direction = data.get("direction", "above")
        close_time = float(data["close_time"])
        yp = data.get("yes_price_cents")
        yes_price = float(yp) if yp not in (None, "", "null") else None
    except (KeyError, ValueError, TypeError) as e:
        return jsonify({"error": f"bad params: {e}"}), 400
    if direction not in ("above", "below"):
        return jsonify({"error": "direction must be 'above' or 'below'"}), 400

    ticker = data.get("kalshi_ticker") or None
    side = data.get("position_side")
    side = side.upper() if side in ("yes", "no", "YES", "NO") else None
    ec = data.get("entry_cost_cents")
    entry_cost = float(ec) if ec not in (None, "", "null") else None

    try:
        sig = _signal_for(coin, threshold, direction, close_time, yes_price)
    except Exception as e:
        return jsonify({"error": str(e)}), 502

    market_id = store.add_market(
        coin, threshold, direction, close_time, yes_price,
        snap_prob_yes=sig["prob_yes"],
        snap_recommendation=sig["recommendation"],
        snap_spot=sig["spot"],
        kalshi_ticker=ticker, position_side=side, entry_cost_cents=entry_cost,
    )
    return jsonify({"id": market_id}), 201


@app.route("/api/markets/<int:market_id>/position", methods=["POST"])
def api_set_position(market_id):
    """Record (or clear) a held position so the app can advise when to sell."""
    data = request.get_json(force=True, silent=True) or {}
    side = data.get("position_side")
    side = side if side in ("YES", "NO", "yes", "no") else None
    ec = data.get("entry_cost_cents")
    try:
        entry_cost = float(ec) if ec not in (None, "", "null") else None
    except (ValueError, TypeError):
        return jsonify({"error": "bad entry_cost_cents"}), 400
    store.set_position(market_id, side, entry_cost)
    return jsonify({"ok": True})


@app.route("/api/markets")
def api_list_markets():
    out = []
    for m in store.list_markets():
        m = _auto_resolve(m)
        item = dict(m)
        if not m["resolved"]:
            try:
                sig = _signal_for(m["coin"], m["threshold"], m["direction"],
                                  m["close_time"], m["yes_price_cents"])
                item["signal"] = sig
            except Exception as e:
                item["signal_error"] = str(e)
                sig = None

            # Live Kalshi bid/ask if this market came from the scanner.
            live = None
            if m.get("kalshi_ticker"):
                try:
                    live = kalshi.get_market(m["kalshi_ticker"])
                    item["kalshi_live"] = live
                except Exception as _e:
                    errlog.note("APP-api_list_markets", _e)

            # Sell guidance for a held position.
            if sig and m.get("position_side") and m.get("entry_cost_cents") is not None:
                item["position"] = odds.sell_guidance(
                    m["position_side"], m["entry_cost_cents"],
                    sig["fair_yes_cents"], sig["fair_no_cents"],
                    yes_bid=(live or {}).get("yes_bid"),
                    no_bid=(live or {}).get("no_bid"),
                    yes_ask=(live or {}).get("yes_ask"),
                    no_ask=(live or {}).get("no_ask"),
                    minutes_to_close=sig.get("minutes_to_close"),
                )
        out.append(item)
    return jsonify(out)


@app.route("/api/markets/<int:market_id>", methods=["DELETE"])
def api_delete_market(market_id):
    store.delete_market(market_id)
    return jsonify({"ok": True})


@app.route("/api/kalshi/meta")
def api_kalshi_meta():
    return jsonify({"coins": kalshi.SCANNABLE_COINS, "timeframes": kalshi.TIMEFRAMES})


@app.route("/api/kalshi/scan")
def api_kalshi_scan():
    """Pull live open Kalshi contracts for a coin+timeframe and run the model
    on each, returning them ranked by edge (best opportunities first)."""
    coin = request.args.get("coin", "BTC").upper()
    timeframe = request.args.get("timeframe", "hourly")
    try:
        markets = kalshi.get_open_markets(coin, timeframe)
    except Exception as e:
        return jsonify({"error": f"kalshi fetch failed: {e}"}), 502
    if not markets:
        return jsonify({"coin": coin, "timeframe": timeframe, "markets": []})

    try:
        spot = prices.get_spot(coin)
        candles = prices.get_candles(coin, granularity=60)
    except Exception as e:
        return jsonify({"error": f"price feed failed: {e}"}), 502

    enriched = []
    for m in markets:
        mins = _minutes_to_close(m["close_time"]) if m["close_time"] else 0.0
        sig = odds.kalshi_signal(spot, candles, m, mins, calibrated=True)
        item = dict(m)
        item["minutes_to_close"] = round(mins, 2)
        item["signal"] = sig
        # Best available edge on either side, for ranking.
        edges = [e for e in (sig["edge_yes_cents"], sig["edge_no_cents"]) if e is not None]
        item["best_edge"] = max(edges) if edges else None
        enriched.append(item)

    enriched.sort(key=lambda x: (x["best_edge"] is None, -(x["best_edge"] or 0)))

    # Volatility edge: what move is the strike ladder pricing vs realized? (Pro+)
    vol = None
    tier = _tier()
    if markets and tiers.has_feature(tier, "vol_edge"):
        close = max((m["close_time"] for m in markets if m["close_time"]), default=None)
        mins = _minutes_to_close(close) if close else 0.0
        vol = odds.vol_edge(spot, candles, markets, mins)
        # Cross-check against Deribit's DVOL (the sharp options market, BTC/ETH).
        if vol and tiers.has_feature(tier, "deribit"):
            import deribit
            dvol = deribit.get_dvol(coin)
            if dvol:
                vol["deribit_dvol_pct"] = dvol
                ki = vol.get("implied_annual_pct")
                src = "Kalshi-implied" if ki else "recent realized"
                if not ki:
                    ki = vol.get("realized_annual_pct")
                if ki:
                    r = ki / dvol
                    vol["deribit_ratio"] = round(r, 2)
                    if r >= 1.15:
                        vol["deribit_note"] = (f"{src} vol ({ki}%) is RICHER than Deribit ({dvol}%) "
                                               f"- favorites/near-money look cheap on Kalshi.")
                    elif r <= 0.87:
                        vol["deribit_note"] = (f"{src} vol ({ki}%) is CHEAPER than Deribit ({dvol}%) "
                                               f"- the wings/longshots look cheap on Kalshi.")
                    else:
                        vol["deribit_note"] = f"{src} vol ({ki}%) is in line with Deribit ({dvol}%)."

    return jsonify({"coin": coin, "timeframe": timeframe, "spot": round(spot, 2),
                    "markets": enriched, "vol": vol})


@app.route("/api/simulate/price")
def api_simulate_price():
    """Monte Carlo a coin or commodity forward; return the outcome distribution."""
    import simulate
    kind = request.args.get("kind", "crypto")
    key = request.args.get("key", "BTC")
    try:
        horizon = float(request.args.get("horizon", 60))
        th = request.args.get("threshold")
        threshold = float(th) if th not in (None, "", "null") else None
        direction = request.args.get("direction", "above")
    except ValueError:
        return jsonify({"error": "bad params"}), 400
    try:
        if kind == "commodity":
            import commodities
            spot = commodities.get_spot(key)
            candles = commodities.get_candles(key)  # daily -> horizon in days
        else:
            spot = prices.get_spot(key.upper())
            candles = prices.get_candles(key.upper(), granularity=60)  # horizon in minutes
    except Exception as e:
        return jsonify({"error": f"feed failed: {e}"}), 502
    n = tiers.cap_sims(_tier(), request.args.get("sims", 20000))
    res = simulate.price_sim(spot, candles, horizon, n=n,
                             threshold=threshold, direction=direction)
    res["kind"] = kind
    res["key"] = key
    return jsonify(res)


@app.route("/api/simulate/game")
def api_simulate_game():
    """Simulate a baseball game many times -> win %, totals, blowout/shutout."""
    import simulate
    date = request.args.get("date") or clock.today_et().isoformat()
    try:
        game_pk = int(request.args["game_pk"])
    except (KeyError, ValueError):
        return jsonify({"error": "game_pk required"}), 400
    try:
        games = baseball.analyze_slate(date, date[:4])
    except Exception as e:
        return jsonify({"error": str(e)}), 502
    g = next((x for x in games if x["game_pk"] == game_pk), None)
    if not g:
        return jsonify({"error": "game not found"}), 404
    n = tiers.cap_sims(_tier(), request.args.get("sims", 20000))
    res = simulate.game_sim(g["exp_runs_home"], g["exp_runs_away"], n=n)
    res.update(matchup=g["matchup"], home=g["home_name"], away=g["away_name"])
    # When lineups are posted, also run the full player-level base-running sim
    # (per-batter hits/HR/SB/DK points, speed + steals) and attach it.
    if (g.get("props") or {}).get("batters_home") or (g.get("props") or {}).get("batters_away"):
        try:
            import mlb_sim
            res["player_sim"] = mlb_sim.summary(mlb_sim.simulate(g, min(8000, n)), g=g)
        except Exception as _e:
            errlog.note("APP-api_simulate_game", _e)
    return jsonify(res)


@app.route("/api/simulate/weather")
def api_simulate_weather():
    import simulate, weather_markets
    city = request.args.get("city", "nyc")
    want_date = request.args.get("date")
    try:
        data = weather_markets.get_city(city)
    except Exception as e:
        return jsonify({"error": str(e)}), 502
    evs = data.get("events") or []
    ev = next((e for e in evs if e.get("date") == want_date), None) if want_date else None
    if ev is None:
        ev = evs[0] if evs else None
    if not ev or not ev.get("model"):
        return jsonify({"error": "no forecast available"}), 404
    m = ev["model"]
    th = request.args.get("threshold")
    threshold = float(th) if th not in (None, "", "null") else None
    n = tiers.cap_sims(_tier(), request.args.get("sims", 20000))
    res = simulate.temp_sim(m["mean"], m["sigma"], n=n, threshold=threshold,
                            direction=request.args.get("direction", "above"))
    res.update(city=data["city"], date=ev["date"], forecast_high=m["forecast_high"])
    return jsonify(res)


@app.route("/api/dfs/slate")
def api_dfs_slate():
    """Tonight's DraftKings slate for a sport, straight from DK's public lobby —
    salaries, roster slots and the game/bout key, plus the posted contests (entry
    fee, field size, prize pool) so the contest sim's parameters match the contest
    you're entering. Removes the hand-pasted CSV step entirely.

    ?sport=ufc|mlb|nfl|nba|nhl|golf|nascar|f1|soccer|lol
    ?dg=<draft_group_id>   pick a specific slate (default: the soonest, biggest)
    ?exclude=Dulatov vs Turman,...  drop a postponed game/bout DK hasn't pulled"""
    import dk
    sport = (request.args.get("sport") or "ufc").lower()
    if sport not in dk.SPORTS:
        return jsonify({"error": f"unknown sport (have: {', '.join(sorted(dk.SPORTS))})"}), 400
    dg = request.args.get("dg")
    excl = [s.strip() for s in (request.args.get("exclude") or "").split(",") if s.strip()]
    try:
        s = dk.slate_for(sport, draft_group_id=(int(dg) if dg else None), exclude_games=excl)
    except Exception as e:
        return jsonify({"error": f"dk slate failed: {e}"}), 502
    if not s:
        return jsonify({"error": f"no DraftKings slate posted for {sport} right now"}), 404
    return jsonify(s)


@app.route("/api/simulate/dfs", methods=["POST"])
def api_simulate_dfs():
    """Optimize + simulate a DraftKings DFS lineup from a pasted DKSalaries.csv."""
    import simulate
    locked = _locked("dfs")
    if locked:
        return locked
    d = request.get_json(force=True, silent=True) or {}
    text = d.get("csv", "")
    # No CSV pasted? Pull the live slate from DK's public lobby instead, so the
    # builder works straight from the sport (and auto-drops anyone DK has marked
    # unavailable — a scratched player or a postponed fight).
    auto_slate = None
    if not text.strip():
        try:
            import dk
            sp = (d.get("sport") or "ufc").lower()
            if sp in dk.SPORTS:
                excl = d.get("exclude") or []
                if isinstance(excl, str):
                    excl = [x.strip() for x in excl.split(",") if x.strip()]
                got = dk.slate_for(sp, draft_group_id=d.get("draft_group_id"),
                                   exclude_games=excl)
                if got:
                    text = got["csv"]
                    auto_slate = {k: got[k] for k in
                                  ("draft_group_id", "n_players", "n_dropped", "dropped")}
        except Exception:
            auto_slate = None
    if not text.strip():
        return jsonify({"error": "paste your DraftKings salaries CSV "
                                 "(or pass a sport to auto-load tonight's slate)"}), 400
    try:
        roster = int(d.get("roster", 6))
        cap = int(d.get("cap", 50000))
    except (ValueError, TypeError):
        return jsonify({"error": "bad roster/cap"}), 400
    n = tiers.cap_sims(_tier(), d.get("sims", 20000))
    if d.get("sport") == "lol":
        # LoL Classic DFS: captain + role optimizer with team-correlated ceiling.
        import lol_dfs

        def _li(key, default, cast=float):
            try:
                return cast(d.get(key, default))
            except (ValueError, TypeError):
                return default
        obj = d.get("objective")
        obj = obj if obj in ("projection", "ceiling", "leverage") else "projection"
        contest = d.get("contest") if d.get("contest") in ("gpp", "double_up") else None
        try:
            res = lol_dfs.build(
                text, objective=obj, contest=contest,
                contest_size=(int(_li("contest_size", 0, int)) or None),
                entry_fee=max(0.01, _li("entry_fee", 1.0)),
                prize_pool=(_li("prize_pool", 0.0) or None),
                first_prize=(_li("first_prize", 0.0) or None))
            if auto_slate and isinstance(res, dict):
                res["dk_slate"] = auto_slate
            return jsonify(res)
        except Exception as e:
            return jsonify({"error": f"lol dfs failed: {e}"}), 502
    if d.get("sport") == "nfl":
        # NFL Classic DFS: positional optimizer + Sleeper-seeded correlated contest sim.
        import nfl_dfs

        def _ni(key, default, cast=float):
            try:
                return cast(d.get(key, default))
            except (ValueError, TypeError):
                return default
        obj = d.get("objective")
        obj = obj if obj in ("projection", "ceiling", "leverage") else "projection"
        contest = d.get("contest") if d.get("contest") in ("gpp", "double_up") else None
        try:
            res = nfl_dfs.build(
                text, week=max(1, min(18, int(_ni("week", 1, int)))), objective=obj,
                stack=d.get("stack", True) is not False, contest=contest,
                contest_size=(int(_ni("contest_size", 0, int)) or None),
                entry_fee=max(0.01, _ni("entry_fee", 1.0)),
                prize_pool=(_ni("prize_pool", 0.0) or None),
                first_prize=(_ni("first_prize", 0.0) or None),
                preseason=_nfl_pre_flag(d),
                field_size=int(_ni("field_size", 0, int)) or None,
                n_lineups=max(1, min(20, int(_ni("lineups", 1, int)))),
                uniq=max(1, min(6, int(_ni("uniq", 0, int) or _ni("min_uniq", 2, int)))),
                # auto reads it off the CSV (a showdown export carries CPT rows);
                # the explicit values are the override for when DK posts a
                # one-game CLASSIC slate, which auto cannot tell apart by size.
                # `nfl_mode`, not `mode` -- the DFS payload already carries a
                # `mode` for the racing/UFC builders and reusing it would let a
                # NASCAR setting decide an NFL roster.
                mode=(d.get("nfl_mode") if d.get("nfl_mode") in ("showdown", "classic")
                      else "auto"))
            if auto_slate and isinstance(res, dict):
                res["dk_slate"] = auto_slate
            return jsonify(res)
        except Exception as e:
            return jsonify({"error": f"nfl dfs failed: {e}"}), 502
    if d.get("sport") == "mlb":
        # Baseball DFS is driven by the game simulator (correlated hitter ceilings
        # for stacking) -- a different path from the projection-based racing/UFC.
        import mlb_dfs
        date = d.get("date") or clock.today_et().isoformat()
        objective = "ceiling" if d.get("objective") == "ceiling" else "median"

        def _i(key, default, lo, hi):
            try:
                return max(lo, min(hi, int(d.get(key, default))))
            except (ValueError, TypeError):
                return default

        def _f(key, default, lo, hi):
            try:
                return max(lo, min(hi, float(d.get(key, default))))
            except (ValueError, TypeError):
                return default

        n_lineups = _i("lineups", 1, 1, 150)
        contest = d.get("contest") if d.get("contest") in ("gpp", "double_up") else None
        res = mlb_dfs.build(
            date, text, cap=cap, objective=objective, n_sims=min(6000, n),
            n_lineups=n_lineups, max_exposure=_f("max_exposure", 60, 5, 100),
            min_uniq=_i("min_uniq", 2, 1, 6), stack_min=_i("stack_min", 4, 0, 5),
            contest=contest, field_size=_i("field_size", 600, 100, 1200),
            contest_iters=_i("contest_iters", 500, 50, 800),
            entry_fee=_f("entry_fee", 1.0, 0.01, 100000),
            contest_size=_i("contest_size", 0, 0, 5000000) or None,
            prize_pool=(_f("prize_pool", 0, 0, 1e9) or None),
            first_prize=(_f("first_prize", 0, 0, 1e9) or None),
            include_unconfirmed=bool(d.get("include_unconfirmed")),
            # DK's opener/bulk pitcher badges ride the auto-loaded slate (the
            # CSV format can't carry them) so the card can flag a "PO" trap.
            roles=(auto_slate or {}).get("roles"))
        if auto_slate and isinstance(res, dict):
            res["dk_slate"] = auto_slate
        return jsonify(res)
    # UFC / F1 / NASCAR: single or multi-lineup portfolio + large-field contest sim.
    def _num(key, default, cast=float):
        try:
            return cast(d.get(key, default))
        except (ValueError, TypeError):
            return default
    contest = d.get("contest") if d.get("contest") in ("gpp", "double_up") else None
    built = simulate.dfs_build(
        text, roster=roster, cap=cap, sport=d.get("sport", "ufc"),
        mode=d.get("mode", "classic"), objective=d.get("objective", "projection"),
        date=d.get("date") or clock.today_et().isoformat(), sims=n,
        n_lineups=max(1, min(150, _num("lineups", 1, int))),
        max_exposure=max(5.0, min(100.0, _num("max_exposure", 60.0))),
        min_uniq=max(1, min(6, _num("min_uniq", 1, int))),
        contest=contest,
        contest_size=(int(_num("contest_size", 0, int)) or None),
        entry_fee=max(0.01, _num("entry_fee", 1.0)),
        prize_pool=(_num("prize_pool", 0.0) or None),
        first_prize=(_num("first_prize", 0.0) or None),
        grid_text=(d.get("grid") or None))
    if auto_slate and isinstance(built, dict):
        built["dk_slate"] = auto_slate          # what was auto-loaded, and who DK dropped
    return jsonify(built)


def _prop_types():
    """Optional prop-type filter from ?types=ML,Total,Hit,... (None = all types)."""
    raw = request.args.get("types")
    if not raw:
        return None
    got = {t.strip() for t in raw.split(",") if t.strip()}
    return got or None


@app.route("/api/calibration")
def api_calibration():
    """Site-wide calibration audit: per-model temperature, sample size, and
    direction (reined in / sharpened / well-calibrated / accruing / no data)."""
    import calibrate
    try:
        return jsonify(calibrate.report())
    except Exception as e:
        return jsonify({"error": f"calibration report failed: {e}"}), 502


# The MLB slate is the heaviest board in the app: a cold build simulates every
# game and takes ~54s on four fast cores, which on a half-core instance is several
# MINUTES. Blocking a request for that long does not merely feel slow -- gunicorn
# kills the worker at its timeout, the browser gets a 502 instead of JSON, and the
# page shows "Failed to load slate" with nothing in the app's own logs to explain
# it. Every other heavy board here (NBA, NHL, tennis) already answers 202 and
# builds in the background; this one was the exception.
_slate_inflight = set()
_slate_lock = threading.Lock()


def _slate_ready(date, season):
    """The slate if it is already built and cached, else None. Never blocks."""
    try:
        return baseball.analyze_slate(date, season, cached_only=True)
    except TypeError:
        # Older signature without the flag: fall back to the cache directly so a
        # partial deploy degrades to "slow" rather than "broken".
        hit = baseball._cache.get(("slate", date, season))
        return hit[1] if hit else None
    except Exception:
        return None


@app.route("/api/baseball/today")
def api_baseball_today():
    """Model predictions for a day's MLB slate plus parlay combo suggestions.

    Non-blocking: 202 while the board builds, the frontend polls."""
    date = request.args.get("date") or clock.today_et().isoformat()
    season = request.args.get("season") or date[:4]
    key = (date, season)
    _note_slate_use(date, season)   # keep this board warm while it's in use
    games = _slate_ready(date, season)
    if games is None:
        with _slate_lock:
            starting = key not in _slate_inflight
            if starting:
                _slate_inflight.add(key)

        if starting:
            def _bg():
                try:
                    baseball.analyze_slate(date, season)
                except Exception as _e:
                    errlog.note("APP-bg", _e)
                finally:
                    with _slate_lock:
                        _slate_inflight.discard(key)
            threading.Thread(target=_bg, daemon=True).start()
        # Stale-while-revalidate: if we have a recent-but-expired board, answer
        # with it NOW rather than making the user watch a rebuild. The five
        # minute TTL is shorter than a trip to the exchange, so "come back to
        # the app" and "the cache just expired" are the same event; 202 turned
        # every return into a one-minute wait for numbers we already had.
        stale, age = baseball.stale_slate(date, season)
        if stale:
            try:
                combos = baseball.combo_context(stale, allow_live=_allow_live())
            except Exception:
                combos = {}
            return jsonify({"date": date, "games": stale, "combos": combos,
                            "stale": True, "stale_age_s": int(age or 0),
                            "refreshing": True})
        return jsonify({"status": "computing", "date": date,
                        "message": "simulating every game on the slate…"}), 202
    try:
        games = baseball.analyze_slate(date, season)
    except Exception as e:
        return jsonify({"error": f"baseball data failed: {e}"}), 502
    # Record the model's PRE-GAME picks so we can grade the real track record.
    # Preview only -- "not Final" also let games in progress through, and a
    # live game poisons the ledger twice over: a first-seen-live game records
    # an in-game price as its "entry", and every board poll during play
    # refreshed close_price with the score's opinion, which turned the CLV
    # stat into a mirror of the win rate (+14.8c of pure hindsight).
    for gm in games:
        if (gm.get("live") or {}).get("state") in (None, "", "Preview"):
            side = "home" if gm["pick_is_home"] else "away"
            store.record_mlb_pick(gm["game_pk"], date, side,
                                  gm["pick"], gm["pick_prob"], gm.get("pick_price_cents"),
                                  pred_total=gm.get("exp_total"),
                                  p_home_model=gm.get("p_home_model"),
                                  p_home_deep=gm.get("p_home_deep"),
                                  prob_raw=gm.get("pick_prob_raw"))
            # Track the latest pre-game price of our side for closing-line
            # value -- same-side only, so a pick that flips across 50% between
            # builds freezes at its last honest close instead of adopting the
            # other team's price.
            store.update_mlb_close(gm["game_pk"], gm.get("pick_price_cents"),
                                   pick_side=side)
    # The auto-built suggestion slips (safest / best value / mixed / live) are no
    # longer produced on every slate load: they cost ~26 MB and seconds of work
    # per request whether or not anyone looked at them, on an instance with no
    # memory to spare. The interactive combo MAKER is untouched and still builds
    # on demand via /api/baseball/parlay; it only needs these two cheap facts.
    combos = baseball.combo_context(games, allow_live=_allow_live())
    # What the factor/deep blend is running on, so a changed weight is visible on
    # the board rather than silently re-shaping every number (the deep engine
    # once sat at 0% for weeks and nothing said so).
    try:
        blend = baseball.deep_blend_info()
    except Exception:
        blend = None
    return jsonify({"date": date, "games": games, "combos": combos,
                    "deep_blend": blend})


@app.route("/api/backtest")
def api_backtest():
    """Replay history to measure how well the crypto model predicts reality."""
    locked = _locked("backtest")
    if locked:
        return locked
    import backtest
    coin = request.args.get("coin", "BTC").upper()
    try:
        horizon = int(request.args.get("horizon", 15))
    except ValueError:
        horizon = 15
    horizon = max(1, min(120, horizon))
    try:
        result = backtest.run(coin, horizon_min=horizon)
    except Exception as e:
        return jsonify({"error": str(e)}), 502
    return jsonify(result)


@app.route("/api/recorder/status")
def api_recorder_status():
    import recorder
    return jsonify(recorder.status())


@app.route("/api/recorder/backtest")
def api_recorder_backtest():
    """Realized P&L of the edge strategy at real recorded Kalshi prices."""
    import recorder
    coin = request.args.get("coin") or None
    timeframe = request.args.get("timeframe") or None
    try:
        return jsonify(recorder.backtest(coin=coin, timeframe=timeframe))
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/bets", methods=["GET"])
def api_list_bets():
    return jsonify(store.list_bets())


@app.route("/api/bets", methods=["POST"])
def api_add_bet():
    d = request.get_json(force=True, silent=True) or {}
    try:
        stake = float(d["stake"])
        price = float(d["price_cents"]) if d.get("price_cents") not in (None, "", "null") else None
    except (KeyError, ValueError, TypeError) as e:
        return jsonify({"error": f"bad params: {e}"}), 400
    kind = d.get("kind", "other")
    bid = store.add_bet(kind, d.get("description", ""), d.get("side"),
                        stake, price, d.get("notes"))
    return jsonify({"id": bid}), 201


@app.route("/api/bets/<int:bet_id>/settle", methods=["POST"])
def api_settle_bet(bet_id):
    d = request.get_json(force=True, silent=True) or {}
    m = store.settle_bet(bet_id, d.get("status", ""))
    if m is None:
        return jsonify({"error": "bad status or bet not found"}), 400
    return jsonify(m)


@app.route("/api/bets/<int:bet_id>", methods=["DELETE"])
def api_delete_bet(bet_id):
    store.delete_bet(bet_id)
    return jsonify({"ok": True})


@app.route("/api/sports/meta")
def api_sports_meta():
    import sports
    return jsonify({k: v["label"] for k, v in sports.SPORTS.items()})


@app.route("/api/sports/<sport_key>")
def api_sports(sport_key):
    import sports
    try:
        events = sports.get_events(sport_key)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"kalshi fetch failed: {e}"}), 502
    grid = None
    # Racing: overlay an independent grid-based win model to surface real edge
    # (Pro+). Falls back silently to the de-vig favorite for free users.
    if sport_key in ("nascar", "f1") and tiers.has_feature(_tier(), "racing_picks"):
        try:
            import racing
            events, grid = racing.race_board(sport_key, events,
                                             date=clock.today_et().isoformat())
        except Exception:
            grid = None
    return jsonify({"sport": sport_key, "events": events, "grid": grid,
                    "racing_locked": (sport_key in ("nascar", "f1")
                                      and not tiers.has_feature(_tier(), "racing_picks"))})


@app.route("/api/racing/dfs")
def api_racing_dfs():
    """Scenario-coherent DK lineups for the next F1/NASCAR race: correlated
    finish-order sims -> real DK scoring -> per-scenario optimal builds, with
    duplication estimates (small pools duplicate; uniqueness is equity)."""
    kind = (request.args.get("kind") or "f1").lower()
    if kind not in ("f1", "nascar"):
        return jsonify({"error": "kind must be f1 or nascar"}), 400
    try:
        import racing_dfs
        data = racing_dfs.board(kind)
    except Exception as e:
        return jsonify({"error": f"racing dfs failed: {e}"}), 502
    if data is None:
        return jsonify({"error": "simulating the race in the background - retry shortly"}), 502
    return jsonify(data)


_live_cache = {"ts": 0.0, "data": None}


@app.route("/api/sports/live")
def api_sports_live():
    """Every game we track that's being played RIGHT NOW, confirmed from each
    league's live scoreboard (not market-timing guesses). MLB carries its richer
    inning/base-out feed; the other tracked leagues come from ESPN scoreboards and
    surface the moment a game flips to in-progress."""
    import time as _t
    now = _t.time()
    if _live_cache["data"] and now - _live_cache["ts"] < 30:
        return jsonify(_live_cache["data"])
    out = []
    # MLB: richer dedicated feed (inning + score).
    try:
        today = clock.today_et().isoformat()
        for gm in baseball._schedule(today, today[:4]):
            lv = gm.get("live") or {}
            if lv.get("state") == "Live":
                out.append({
                    "sport": "⚾ MLB", "confirmed": True,
                    "title": f"{gm['away_name']} @ {gm['home_name']}",
                    "detail": f"{lv.get('inning_state', '')} {lv.get('inning', '')}".strip(),
                    "score": f"{lv.get('away_runs', 0)}–{lv.get('home_runs', 0)}",
                    "nav": {"tab": "baseball", "pk": gm["game_pk"]},
                })
    except Exception as _e:
        errlog.note("APP-api_sports_live", _e)
    # Every other tracked team sport, confirmed live from its ESPN scoreboard.
    try:
        import live as _live
        out.extend(_live.confirmed_live())
    except Exception as _e:
        errlog.note("APP-api_sports_live-2", _e)
    data = {"games": out, "confirmed_count": sum(1 for g in out if g["confirmed"])}
    _live_cache.update(ts=now, data=data)
    return jsonify(data)


@app.route("/api/weather/meta")
def api_weather_meta():
    import weather_markets
    return jsonify({k: v["label"] for k, v in weather_markets.CITIES.items()})


@app.route("/api/weather/<city>")
def api_weather_city(city):
    import weather_markets
    try:
        return jsonify(weather_markets.get_city(city))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"fetch failed: {e}"}), 502


def _allow_live():
    """Whether the caller opted in to betting games already under way. Off unless
    explicitly ticked -- a live board is priced from a snapshot that is seconds
    old, so it must never be the silent default."""
    v = (request.args.get("live") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


@app.route("/api/futures/modeled")
def api_futures_modeled():
    """Long-dated markets our season simulators actually model, ranked by
    expected return to settlement.

    Unlike /api/futures (market-only, no edge claim), these rows carry a model
    number, so expected return can be genuinely positive. It is blended with the
    market at the weight that sport's model has EARNED on graded results."""
    import mfutures as _mf
    def num(name, default=None, lo=None, hi=None):
        v = request.args.get(name)
        if v in (None, ""):
            return default
        try:
            x = float(v)
        except ValueError:
            return default
        if lo is not None:
            x = max(lo, x)
        if hi is not None:
            x = min(hi, x)
        return x
    def flag(name, default=False):
        v = request.args.get(name)
        if v in (None, ""):
            return default
        return v.strip().lower() in ("1", "true", "yes", "on")
    try:
        data = _mf.board(
            q=request.args.get("q", ""),
            sort=request.args.get("sort", "best"),
            sports=[x for x in (request.args.get("sports", "") or "").split(",") if x],
            markets=[x for x in (request.args.get("markets", "") or "").split(",") if x],
            min_prob=num("min_prob", 0, 0, 100),
            max_days=num("max_days", None, 1, 40000),
            limit=int(num("limit", 60, 1, 400)),
            include_suspect=flag("include_suspect", False),
            positive_only=flag("positive_only", True),
            in_season_only=flag("in_season", False),
        )
        if data.get("building"):
            return jsonify({"building": True,
                            "error": "running the season simulations - retry shortly"}), 202
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": f"modeled futures failed: {e}"}), 502


@app.route("/api/futures")
def api_futures():
    """Long-dated Kalshi contracts, ranked by what they pay per year.

    Deliberately market-only: no model, no edge claim. The board answers "given
    how likely this is and how long my money is tied up, what does it pay?" and
    shows the loss chance next to every yield."""
    import futures as _fut
    q = request.args.get("q", "")
    sort = request.args.get("sort", "best")

    def num(name, default=None, lo=None, hi=None):
        v = request.args.get(name)
        if v in (None, ""):
            return default
        try:
            x = float(v)
        except ValueError:
            return default
        if lo is not None:
            x = max(lo, x)
        if hi is not None:
            x = min(hi, x)
        return x
    try:
        data = _fut.board(
            q=q, sort=sort,
            min_prob=num("min_prob", _fut.DEFAULT_MIN_PROB, 1, 100),
            max_days=num("max_days", None, 1, 40000),
            min_days=num("min_days", _fut.MIN_DAYS, 1, 40000),
            min_volume=num("min_volume", None, 0, 1e9),
            limit=int(num("limit", 60, 1, 500)),
        )
        if data.get("building"):
            # First hit after a cold start: the sweep is ~40 pages and runs on a
            # background thread rather than holding the request open.
            return jsonify({"building": True,
                            "error": "scanning Kalshi's long-dated markets - retry shortly"}), 202
        data["summary"] = _fut.summary()
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": f"futures load failed: {e}"}), 502


@app.route("/api/combine/meta")
def api_combine_meta():
    import combine
    return jsonify({"categories": combine.CATEGORIES, "types": combine.CATEGORY_TYPES})


@app.route("/api/combine")
def api_combine():
    """Cross-category parlay (MLB + daily crypto + UFC/tennis/golf),
    tuned to a target per-leg confidence."""
    import combine
    date = request.args.get("date") or clock.today_et().isoformat()
    season = date[:4]
    cats = [c for c in (request.args.get("cats", "") or "").split(",") if c]
    if not cats:
        cats = list(combine.CATEGORIES.keys())
    try:
        legs = tiers.cap_legs(_tier(), request.args.get("legs", 3))
        target = float(request.args.get("target", 65))
        payout = request.args.get("payout")
        payout = float(payout) if payout not in (None, "", "0") else None
    except ValueError:
        return jsonify({"error": "bad legs/target"}), 400
    # Each target (legs / payout) is "require" (hard) / "prefer" (recommend) /
    # "off", combined by conn ('and'/'or') -- same controls as the baseball maker.
    modes = ("require", "prefer", "off")
    legs_mode = request.args.get("legs_mode", "prefer")
    payout_mode = request.args.get("payout_mode")
    conn = "and" if request.args.get("conn") == "and" else "or"
    if legs_mode not in modes:
        legs_mode = "prefer"
    if payout_mode is not None and payout_mode not in modes:
        payout_mode = None
    # Per-sport leg-type filter (the maker's type chips). Present-but-empty means
    # every catalogued type is turned off; absent means no filtering.
    types = ([t for t in request.args.get("types", "").split(",") if t]
             if "types" in request.args else None)
    # Per-sport leg budget: "mlb:0,tennis:2" -> {mlb: 0 (all), tennis: 2}. When
    # present, the combo is built from these counts per sport instead of one floor.
    per_cat = {}
    for pair in (request.args.get("per_cat", "") or "").split(","):
        if ":" in pair:
            k, _, v = pair.partition(":")
            try:
                if k.strip():
                    per_cat[k.strip()] = max(0, min(40, int(v)))
            except ValueError:
                continue
    try:
        return jsonify(combine.build(cats, legs, target, date, season, target_payout=payout,
                                     max_legs=tiers.cap_legs(_tier(), 30),
                                     legs_mode=legs_mode, payout_mode=payout_mode, conn=conn,
                                     types=types, per_cat=(per_cat or None),
                                     allow_live=_allow_live()))
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/combine/recommended")
def api_combine_recommended():
    """Auto-built safest / best-value / best parlays from the checked sports."""
    import combine
    date = request.args.get("date") or clock.today_et().isoformat()
    season = date[:4]
    cats = [c for c in (request.args.get("cats", "") or "").split(",") if c]
    if not cats:
        return jsonify({"error": "no_cats", "counts": {}})
    types = ([t for t in request.args.get("types", "").split(",") if t]
             if "types" in request.args else None)
    try:
        return jsonify(combine.recommended(cats, date, season,
                                           max_legs=tiers.cap_legs(_tier(), 30),
                                           types=types, allow_live=_allow_live()))
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/baseball/parlay")
def api_baseball_parlay():
    """Build an N-leg parlay tuned to a target per-leg confidence, picking the
    optimal line (hits 1+/2+, run total, moneyline/spread) for each leg."""
    date = request.args.get("date") or clock.today_et().isoformat()
    season = request.args.get("season") or date[:4]
    try:
        legs = tiers.cap_legs(_tier(), request.args.get("legs", 3))
        target = float(request.args.get("target", 65))
        payout = request.args.get("payout")
        payout = float(payout) if payout not in (None, "", "0") else None
    except ValueError:
        return jsonify({"error": "bad legs/target"}), 400
    try:
        games = baseball.analyze_slate(date, season)
    except Exception as e:
        return jsonify({"error": f"baseball data failed: {e}"}), 502
    combo = baseball.build_target_parlay(games, legs, target, target_payout=payout,
                                         max_legs=tiers.cap_legs(_tier(), 30), types=_prop_types(),
                                         allow_live=_allow_live())
    return jsonify({"combo": combo})


@app.route("/api/baseball/rotations")
def api_baseball_rotations():
    """Projected starting pitchers for the coming days, per team: announced
    probables where MLB has named them (89% at D+1, zero at D+5), rest-cadence
    order-cycling where it hasn't. Point-in-time backtested: the pure projection
    beats a naive repeat-the-order baseline at every horizon, and live use only
    improves on that because announcements anchor the front."""
    import rotation
    try:
        horizon = max(2, min(14, int(request.args.get("days", 8))))
    except ValueError:
        return jsonify({"error": "bad params"}), 400
    try:
        proj = rotation.project(horizon=horizon)
    except Exception as e:
        return jsonify({"error": f"rotation projection failed: {e}"}), 502
    abbr = {}
    try:
        abbr = baseball._abbr_map(str(clock.today_et().year))
    except Exception as _e:
        errlog.note("APP-api_baseball_rotations", _e)
    return jsonify({"days": horizon,
                    "teams": {str(tid): {"abbr": abbr.get(tid), "starts": rows}
                              for tid, rows in proj.items()}})


@app.route("/api/baseball/season")
def api_baseball_season():
    """Our season Monte Carlo (division/playoff/pennant/WS odds + win totals) and
    the resulting edges vs Kalshi futures markets."""
    import season_sim
    season = request.args.get("season") or str(clock.today_et().year)
    try:
        sims = tiers.cap_sims(_tier(), request.args.get("sims", 4000))
    except ValueError:
        return jsonify({"error": "bad params"}), 400
    try:
        sim = season_sim.cached(season, n=sims)
        fut = season_sim.futures_edges(season, sim=sim)
    except Exception as e:
        return jsonify({"error": f"season sim failed: {e}"}), 502
    teams = [{k: v for k, v in t.items() if k != "_wins_sample"} for t in sim["teams"]]
    return jsonify({"season": season, "n_sims": sim["n_sims"],
                    "n_games_left": sim["n_games_left"], "teams": teams,
                    "futures": fut["edges"], "futures_summary": fut["summary"],
                    "n_liquid": fut.get("n_liquid")})


@app.route("/api/baseball/futures")
def api_baseball_futures():
    """Futures board: pick a market (World Series / pennant / division / playoffs /
    win-total line) and get the full ranked team list — how many of the N sims
    each team won it, our model %, Kalshi and Polymarket. Built for the dropdown +
    search UI."""
    import season_sim
    season = request.args.get("season") or str(clock.today_et().year)
    try:
        sims = tiers.cap_sims(_tier(), request.args.get("sims", 4000))
    except ValueError:
        return jsonify({"error": "bad params"}), 400
    # The deep 4,000-season pitch-by-pitch run is the headline: serve it by
    # default whenever a completed run is cached. Only ?engine=fast forces the
    # instant Pythagorean board (the cold-start fallback before the first deep run).
    if request.args.get("engine") != "fast":
        _deep_refresh()             # adopt a run a sibling worker finished
        agg = _deep.get("agg")
        if agg and _deep.get("season") == season:
            try:
                import deep_cache
                board = season_sim.deep_board(agg, season)
                board["age_sec"] = deep_cache.age("mlb_deep")
                return jsonify(board)
            except Exception as e:
                return jsonify({"error": f"deep board failed: {e}"}), 502
    try:
        board = season_sim.board_cached(season, n=sims)
    except Exception as e:
        return jsonify({"error": f"season sim failed: {e}"}), 502
    return jsonify(board)


@app.route("/api/racing/<sport>")
def api_racing_season(sport):
    """Deep full-season motorsport sim -> championship odds. F1 simulates
    qualifying + race (+ sprints) over the remaining calendar; NASCAR runs the
    playoff bracket. Best-effort Polymarket champion prices attached."""
    import deep_cache
    import time as _t
    sport = (sport or "").lower()
    if sport not in ("f1", "nascar"):
        return jsonify({"error": "unknown sport"}), 404
    # Served from the weekly disk cache (shared by everyone); computed inline on a
    # cold miss since the racing sims are cheap. The scheduler refreshes weekly.
    try:
        data, ts = deep_cache.load(sport)
        if data is None:
            data, ts = deep_cache.run_sync(sport)
    except Exception as e:
        return jsonify({"error": f"sim failed: {e}"}), 502
    if not data:
        return jsonify({"error": "no season data available"}), 502
    data = dict(data)
    data["generated_at"] = ts
    data["age_sec"] = (_t.time() - ts) if ts else None
    # Live Kalshi prices on the championship + upcoming-race markets (model stays
    # weekly-cached; prices fetched fresh per request).
    try:
        import racing_prices
        racing_prices.attach(data)
    except Exception as _e:
        errlog.note("APP-api_racing_season", _e)
    return jsonify(data)


@app.route("/api/pro/<league>")
def api_pro_league(league):
    """Roster-aware preseason futures projection (NFL now; NBA/NHL when their
    schedules publish). Served from the weekly cache; live Kalshi prices + edges
    attached per request. Returns a 'computing' status while a cold run warms up."""
    import deep_cache
    import time as _t
    league = (league or "").lower()
    if league not in ("nfl", "nba", "nhl"):
        return jsonify({"error": "unknown league"}), 404
    data, ts = deep_cache.load(league)
    if data is None:
        started = deep_cache.run_job(league, force=True)
        st = deep_cache.status(league)
        return jsonify({"status": "computing" if (started or st["running"]) else "unavailable",
                        "league": league, "running": st["running"]}), 202
    data = dict(data)
    data["generated_at"] = ts
    data["age_sec"] = (_t.time() - ts) if ts else None
    try:
        import pro_prices
        pro_prices.attach(league, data)
    except Exception as _e:
        errlog.note("APP-api_pro_league", _e)
    return jsonify(data)


@app.route("/api/nfl/futures")
def api_nfl_futures():
    """NFL futures board (Featured tab): Super Bowl / conference / division /
    playoffs / win-total lines from a 4,000-season Monte Carlo, priced vs the
    live Kalshi futures series. Mirrors /api/baseball/futures exactly."""
    try:
        import nfl_season
        b = nfl_season.futures_board(n=tiers.cap_sims(_tier(), request.args.get("sims", 4000)))
    except Exception as e:
        return jsonify({"error": f"nfl season sim failed: {e}"}), 502
    if not b:
        return jsonify({"error": "NFL schedule not available yet"}), 502
    return jsonify(b)


@app.route("/api/cfb/futures")
def api_cfb_futures():
    """College Football Playoff board: national-championship + make-the-Playoff
    odds from a 4,000-season drive-engine Monte Carlo with the 12-team CFP,
    priced vs Kalshi's college futures. Served from the nightly deep-season
    cache; a cold hit computes a smaller run in-line."""
    try:
        import cfb
        b = cfb.futures_board()
    except Exception as e:
        return jsonify({"error": f"cfb season sim failed: {e}"}), 502
    if not b:
        return jsonify({"error": "CFB schedule not available yet"}), 502
    return jsonify(b)


@app.route("/api/nfl/team")
def api_nfl_team():
    """Per-player projected season stat lines for one NFL team — Sleeper's
    projections preseason, blending in real weekly stats once games are played.
    Needs ?abbr=KC."""
    abbr = (request.args.get("abbr") or "").upper()
    if not abbr:
        return jsonify({"error": "abbr required"}), 400
    try:
        import nfl_season
        return jsonify(nfl_season.team_detail(abbr))
    except Exception as e:
        return jsonify({"error": f"team detail failed: {e}"}), 502


# /api/nfl/teams lived here: it persisted the best-ball drafter's saved teams.
# The Draft tab was removed, and the removal took _TEAMS_PATH with it while
# leaving the endpoint behind, so every call raised NameError -- silently on GET
# (the bare `except` returned an empty team list) and as a 500 on POST. Nothing in
# the frontend referenced it. Deleted rather than repaired: there is no drafter
# left to save teams for.


@app.route("/api/ufc")
def api_ufc():
    """UFC card simulator: per-bout win odds, method/round and each fighter's DK
    projection, with live Kalshi prices + edges. The board (model) is cached; a
    cold start computes in the background and returns 202 until ready."""
    try:
        import ufc_sim
        board = ufc_sim.board()
    except Exception as e:
        return jsonify({"error": f"ufc sim failed: {e}"}), 502
    if not board:
        return jsonify({"status": "computing",
                        "message": "rating every fighter from their fight history…"}), 202
    try:
        import ufc_prices
        ufc_prices.attach(board)
    except Exception as _e:
        errlog.note("APP-api_ufc", _e)
    return jsonify(board)


@app.route("/api/lol")
def api_lol():
    """League of Legends esports board: the pro slate with each team's roster,
    per-player projections (from Leaguepedia per-map history), and a DK Pick 6
    board of More/Less kill/assist/CS picks. Cold build is slow (paced, rate-
    limited wiki API); cached 15 min."""
    try:
        import lol
        data = lol.board(max_matches=6)
    except Exception as e:
        return jsonify({"error": f"lol data failed: {e}"}), 502
    if not data:
        return jsonify({"error": "no LoL data available yet (loading / rate-limited - retry shortly)"}), 502
    return jsonify(data)


@app.route("/api/lol/futures")
def api_lol_futures():
    """Championship odds (who wins the split / MSI) for one tournament, from an
    Elo + bracket Monte Carlo. ?page=<OverviewPage>."""
    page = request.args.get("page")
    if not page:
        return jsonify({"error": "missing tournament page"}), 400
    try:
        import lol
        data = lol.sim_tournament(page)
    except Exception as e:
        return jsonify({"error": f"lol futures failed: {e}"}), 502
    if not data:
        return jsonify({"error": "no data for that tournament yet (loading / rate-limited)"}), 502
    return jsonify(data)


@app.route("/api/nfl/week")
def api_nfl_week():
    """NFL week board: modeled score, win%, yards/TDs + key players per game.
    Non-blocking (heavy ESPN fan-out) -- returns 502 while the board builds; the
    frontend polls. ?week=1 (regular season; preseason is skipped)."""
    try:
        week = int(request.args.get("week", 1))
    except ValueError:
        week = 1
    week = max(1, min(18, week))
    try:
        import nfl_live
        data = nfl_live.board(week=week)
    except Exception as e:
        return jsonify({"error": f"nfl data failed: {e}"}), 502
    if not data:
        return jsonify({"error": "no NFL data yet (building in the background - retry shortly)"}), 502
    return jsonify(data)


@app.route("/api/nba/slate")
def api_nba_slate():
    """NBA daily slate (possession engine; lights up when the season tips off)."""
    date = request.args.get("date") or None
    try:
        import basket
        data = basket.board("nba", date)
    except Exception as e:
        return jsonify({"error": f"nba slate failed: {e}"}), 502
    if not data:
        return jsonify({"error": "simulating the slate in the background - retry shortly"}), 502
    return jsonify(data)


@app.route("/api/nhl/slate")
def api_nhl_slate():
    """NHL daily slate (shot-event engine; lights up when the season starts)."""
    date = request.args.get("date") or None
    try:
        import hockey
        data = hockey.board(date)
    except Exception as e:
        return jsonify({"error": f"nhl slate failed: {e}"}), 502
    if not data:
        return jsonify({"error": "simulating the slate in the background - retry shortly"}), 502
    return jsonify(data)


@app.route("/api/nfl/slate")
def api_nfl_slate():
    """Drive-engine NFL slate: per-game win probs vs live Kalshi moneylines
    (edges), score/total/spread distributions, correlated player props and a
    default same-game parlay per game. Non-blocking; the frontend polls.
    week=0 (or absent) means AUTO: the first week with games still to play."""
    try:
        week = int(request.args.get("week", 0))
    except ValueError:
        week = 0
    pre = _nfl_preseason()
    try:
        import nfl_game_sim
        if week < 1:
            week = nfl_game_sim.current_week(pre)
        week = max(1, min(18, week))
        data = nfl_game_sim.board(week=week, preseason=pre)
    except Exception as e:
        return jsonify({"error": f"nfl slate failed: {e}"}), 502
    if not data:
        return jsonify({"error": "simulating the slate in the background - retry shortly"}), 502
    # A week that genuinely has no games is an ANSWER, not something to retry
    # forever. Preseason week 1 has a single Hall of Fame game that is played and
    # gone by mid-August, which is exactly when someone opens the tab.
    if data.get("empty"):
        return jsonify({"error": data.get("note") or "No games for this week.",
                        "week": week, "preseason": bool(pre), "games": []})
    # Record pre-game picks + same-side closes for the NFL track record --
    # exactly the MLB flow, with the same pre-game-only gate.
    try:
        import nfl_track
        nfl_track.record_from_board(data)
    except Exception as _e:
        errlog.note("APP-nfl-slate-record", _e)
    return jsonify(data)


@app.route("/api/nfl/record")
def api_nfl_record():
    """The NFL model's graded track record (regular + preseason buckets)."""
    try:
        import nfl_track
        nfl_track.grade_due()
    except Exception as _e:
        errlog.note("APP-api_nfl_record", _e)
    return jsonify(store.nfl_record())


def _nfl_pre_flag(payload):
    """Preseason flag from a POSTed DFS body -- same rule as the query-string
    version, so the lineup builder and the boards agree."""
    v = (payload or {}).get("preseason")
    if v is None:
        try:
            import nfl_preseason
            return nfl_preseason.is_preseason()
        except Exception:
            return False
    return v is True or str(v).lower() in ("1", "true", "yes")


def _nfl_preseason():
    """Whether this request wants exhibitions. `?preseason=1|0` is explicit;
    with no parameter the calendar decides, so the tab is right in August without
    the user having to know which season type ESPN files a game under."""
    raw = request.args.get("preseason")
    if raw in ("1", "true", "yes"):
        return True
    if raw in ("0", "false", "no"):
        return False
    try:
        import nfl_preseason
        return nfl_preseason.is_preseason()
    except Exception:
        return False


@app.route("/api/nfl/parlay")
def api_nfl_parlay():
    """One NFL parlay across the week's games, priced against Kalshi — the
    football twin of /api/baseball/mixed, on the same combo engine.

    `cap` turns the confidence floor into a BAND and the builder walks the spread,
    total and player ladders to the lines that land inside it."""
    locked = _locked("mixed_parlay")
    if locked:
        return locked
    try:
        week = max(1, min(18, int(request.args.get("week", 1))))
        legs = tiers.cap_legs(_tier(), request.args.get("legs", 4))
        target = float(request.args.get("target", 55))
        payout = request.args.get("payout")
        payout = float(payout) if payout not in (None, "", "0") else 0
        sims = tiers.cap_sims(_tier(), request.args.get("sims", 3000))
        max_total = tiers.cap_legs(_tier(), 30)
    except ValueError:
        return jsonify({"error": "bad week/legs/payout"}), 400
    cap = request.args.get("cap")
    try:
        cap = float(cap) if cap not in (None, "") else None
    except ValueError:
        cap = None
    if cap is not None and not (0 < cap <= 100):
        cap = None
    same_game = request.args.get("same_game", "1") != "0"
    modes = ("require", "prefer", "off")
    legs_mode = request.args.get("legs_mode", "prefer")
    payout_mode = request.args.get("payout_mode", "off")
    if legs_mode not in modes:
        legs_mode = "prefer"
    if payout_mode not in modes:
        payout_mode = "off"
    conn = "and" if request.args.get("conn") == "and" else "or"
    import combo_engine
    objective = request.args.get("objective", "balanced")
    if objective not in combo_engine.OBJECTIVES:
        objective = "balanced"
    sel = {t.strip() for t in (request.args.get("sel") or "").split(",") if t.strip()}
    max_bet = request.args.get("max_bet") == "1"
    # Optimal mode, exactly as baseball has it: ONE input (the payout target).
    # The leg count, the per-leg confidence and the game mix are all outputs --
    # legs_mode off, payout required, balanced objective, per-leg floor swept.
    optimal = request.args.get("optimal") == "1"
    if optimal and not (payout and payout > 1):
        return jsonify({"error": "optimal mode needs a payout target above 1x"}), 400
    # The build may run in a background thread where `request` does not exist,
    # so EVERY request-dependent value is read here, on the request thread --
    # baseball learned this the hard way (_prop_types reads the request).
    ptok = (request.args.get("ptok") or "")[:64] or None
    pre_flag = _nfl_preseason()
    prop_types = _prop_types()
    try:
        import nfl_game_sim

        def _build(target_pct, _mb=False, _opt=False):
            return nfl_game_sim.build_parlay(
                week=week, preseason=pre_flag, n_legs=legs,
                target_pct=target_pct,
                cap_pct=None if (_mb or _opt) else cap,
                target_payout=0 if _mb else payout,
                n_sims=sims,
                max_legs_per_game=max_total if same_game else 1,
                max_total_legs=max_total,
                legs_mode="off" if _opt else legs_mode,
                payout_mode="require" if _opt else payout_mode,
                conn=conn,
                objective="balanced" if _opt else objective,
                types=prop_types, game_sel=sel or None, max_bet=_mb)

        def _core():
            """The whole build as a plain dict, runnable off-request -- the
            phone only WATCHES a build, it never carries one."""
            if optimal:
                tgt = min(payout, combo_engine.MAX_PAYOUT_X)
                capped = payout > combo_engine.MAX_PAYOUT_X
                item = combo_engine.best_target(lambda f: _build(f, _opt=True))
                if item:
                    item["objective"] = "optimal"
                    item["target_payout_x"] = tgt
                    item["target_capped"] = capped
                    return {"parlay": item}
                return {"parlay": None, "hint": "optimal_unbuildable",
                        "target_payout_x": tgt}
            if max_bet:
                # A max bet has to be free to go deep and to use unlikely legs --
                # that is what the MARKET payout cap costs. Holding it to the
                # maker's band would guarantee the answer "can't be done", so the
                # ceiling and the payout target are dropped and the floor swept.
                item = combo_engine.best_max_bet(lambda f: _build(f, _mb=True))
                if not item:
                    return {"parlay": None, "hint": "max_bet_unreachable",
                            "cap_x": combo_engine.MAX_PAYOUT_X}
            else:
                item = _build(target)
            if isinstance(item, dict) and item.get("error_hint"):
                return {"parlay": None, "hint": item["error_hint"],
                        "n_games_available": item.get("n_games_available"),
                        "n_started": item.get("n_started")}
            return {"parlay": item}

        if ptok:
            # Baseball's job pattern, verbatim: the result is stored under the
            # click's token and served idempotently, so a phone that suspends
            # the tab mid-build collects the finished slip whenever it returns.
            job = baseball.job_read(ptok)
            if job and job.get("status") == "done":
                return jsonify(job.get("result") or {})
            if job and job.get("status") == "error":
                return jsonify({"error": job.get("error") or "build failed"}), 502
            if (job and job.get("status") == "running"
                    and baseball.job_takeover(ptok, _JOB_DEAD_S)):
                errlog.note("NFL-COMBO-dead-job",
                            RuntimeError("build heartbeat stopped; rebuilding"),
                            path=ptok)
            if not baseball.job_claim(ptok):
                return jsonify({"status": "building", "token": ptok}), 202
            _run_job(ptok, _core, "NFL-COMBO-build")
            return jsonify({"status": "building", "token": ptok}), 202
        return jsonify(_core())
    except Exception as e:
        return jsonify({"error": f"nfl parlay failed: {e}"}), 502


@app.route("/api/nfl/sim")
def api_nfl_sim():
    """Correlated per-game Monte Carlo seeded by Sleeper weekly projections:
    per-player floor/median/ceiling/boom (sim + best-ball), QB->WR stacks, and
    same-game-correlated prop over/unders (Pick 6). Non-blocking; polls."""
    try:
        week = int(request.args.get("week", 1))
    except ValueError:
        week = 1
    week = max(1, min(18, week))
    try:
        import nfl_dfs_sim
        data = nfl_dfs_sim.board(week=week, preseason=_nfl_preseason())
    except Exception as e:
        return jsonify({"error": f"nfl sim failed: {e}"}), 502
    if not data:
        return jsonify({"error": "simulating the week in the background - retry shortly"}), 502
    return jsonify(data)


@app.route("/api/tennis")
def api_tennis():
    """Tennis match board: per-match model win% vs the de-vig Kalshi price (with a
    confidence blend), the coherent derived markets (total games / sets / aces),
    and plain-English angles. Non-blocking: 202 while the first board computes."""
    try:
        import tennis_prices
        board = tennis_prices.board()
    except Exception as e:
        return jsonify({"error": f"tennis sim failed: {e}"}), 502
    if not board:
        return jsonify({"status": "computing",
                        "message": "rating players from their charted matches…"}), 202
    # Live merge happens per-request (60s ESPN snapshot), NOT inside the 20-min
    # board cache: scores move fast and the upset radar is only useful live.
    try:
        import tennis_live
        board = tennis_live.attach(board)
    except Exception as _e:
        errlog.note("APP-api_tennis", _e)
    # ESPN covers atp and wta only, and ITF is over 90% of this board. The trade
    # tape fills that in -- see tennis_tape -- so an in-progress ITF match is
    # marked live everywhere instead of nowhere.
    try:
        import tennis_tape
        board = tennis_tape.attach(board)
    except Exception as _e:
        errlog.note("APP-api_tennis-2", _e)
    # Alarms and dips run LAST, after both live sources, so a match the tape
    # found is judged the same way as one ESPN found.
    try:
        board = tennis_live.mark_price_upsets(board)
        board = tennis_live.mark_dips(board)
    except Exception as _e:
        errlog.note("APP-api_tennis-3", _e)
    return jsonify(board)


@app.route("/api/tennis/parlay")
def api_tennis_parlay():
    """A tennis-only parlay, on the same builder the cross-sport combine uses.

    Tennis has no bitmask sim to stack correlated legs the way baseball and
    football do -- two matches are genuinely independent -- so this runs through
    combine's assembler with the category pinned to tennis rather than through
    mlb_sim's same-game machinery. Everything the user sees is the same: a per-leg
    confidence band, legs/payout targets, and Kalshi pricing.

    Which matches are eligible is Kalshi's call, per match, read off its
    multivariate event collections (kalshi.combo_events) -- ITF very much
    included."""
    import combine
    date = request.args.get("date") or clock.today_et().isoformat()
    try:
        legs = tiers.cap_legs(_tier(), request.args.get("legs", 3))
        target = float(request.args.get("target", 60))
        payout = request.args.get("payout")
        payout = float(payout) if payout not in (None, "", "0") else None
    except ValueError:
        return jsonify({"error": "bad legs/target"}), 400
    modes = ("require", "prefer", "off")
    legs_mode = request.args.get("legs_mode", "prefer")
    payout_mode = request.args.get("payout_mode")
    conn = "and" if request.args.get("conn") == "and" else "or"
    if legs_mode not in modes:
        legs_mode = "prefer"
    if payout_mode is not None and payout_mode not in modes:
        payout_mode = None
    types = ([t for t in request.args.get("types", "").split(",") if t]
             if "types" in request.args else None)
    cap = request.args.get("cap")
    try:
        cap = float(cap) if cap not in (None, "") else None
    except ValueError:
        cap = None
    if cap is not None and not (0 < cap <= 100):
        cap = None
    # Start-time window. "today" is enforceable for every match (the day is in
    # the Kalshi event ticker); the tighter ones need a published start time and
    # so can only ever admit ATP/WTA.
    window = request.args.get("window")
    if window not in ("today", "3h", "1h"):
        window = None
    max_bet = request.args.get("max_bet") == "1"
    try:
        import combo_engine

        def _build(target_pct, _mb=False):
            return combine.build(
                ["tennis"], legs, target_pct, date, date[:4],
                target_payout=None if _mb else payout,
                cap_pct=None if _mb else cap,
                tennis_window=window,
                max_legs=tiers.cap_legs(_tier(), 30),
                legs_mode=legs_mode, payout_mode=payout_mode,
                conn=conn, types=None if _mb else types,
                allow_live=_allow_live(), max_bet=_mb)

        if max_bet:
            # combine.build returns {combo, counts}; the sweep compares slips, so
            # it is handed the combo and the wrapper is rebuilt around the winner.
            last = {}

            def _combo(f):
                last.clear()
                last.update(_build(f, _mb=True) or {})
                return last.get("combo")

            best = combo_engine.best_max_bet(_combo)
            out = dict(last)
            out["combo"] = best
            if not best:
                out["hint"] = out.get("hint") or "max_bet_unreachable"
                out["cap_x"] = combo_engine.MAX_PAYOUT_X
        else:
            out = _build(target)
    except Exception as e:
        return jsonify({"error": f"tennis parlay failed: {e}"}), 502
    # How many matches could have contributed, so an empty build can say whether
    # the slate was thin or the targets were.
    try:
        import tennis_prices
        b = tennis_prices.board() or {}
        out["n_combo_matches"] = b.get("n_combo")
        out["window"] = window
        out["window_counts"] = combine.window_counts(allow_live=_allow_live())
    except Exception as _e:
        errlog.note("APP-api_tennis_parlay", _e)
    return jsonify(out)


@app.route("/api/golf")
def api_golf():
    """Golf tournament simulator: win / top-5/10/20 / make-the-cut and every
    head-to-head from a strokes model (season scoring average + course length +
    wind/temp/altitude), simulating the remaining rounds through the cut and
    priced vs Kalshi (make-cut, H2H). Non-blocking: 202 while the board warms."""
    try:
        import golf
        b = golf.board(request.args.get("tour", "pga"))
    except Exception as e:
        return jsonify({"error": f"golf sim failed: {e}"}), 502
    if not b:
        return jsonify({"status": "computing",
                        "message": "simulating the field through the cut…"}), 202
    return jsonify(b)


@app.route("/api/model-trust")
def api_model_trust():
    """How far each sport's model is allowed to depart from the market price, and
    why — the blend weight fitted on point-in-time backtests, the sample behind
    it, and our model's log-loss against the market's. Sports with no measurement
    show the cautious default."""
    try:
        import model_trust
        return jsonify(model_trust.report())
    except Exception as e:
        return jsonify({"error": f"model trust unavailable: {e}"}), 502


@app.route("/api/sim/status")
def api_sim_status():
    """Freshness + run state of each cached season sim."""
    import deep_cache
    return jsonify({k: deep_cache.status(k) for k in ("mlb_deep", "f1", "nascar")})


@app.route("/api/sim/rerun", methods=["POST"])
def api_sim_rerun():
    """Force a fresh run of one cached season sim (manual weekly-style refresh).

    The run itself belongs to the scheduler-owning worker, but THIS request
    lands on whichever worker gunicorn picks -- so anything but the owner
    queues the request through the shared file and the owner starts it within
    seconds. run_job directly on a non-owner returned {"started": false}: the
    literal "I hit rerun now and nothing happens"."""
    import deep_cache
    key = {"mlb": "mlb_deep", "f1": "f1", "nascar": "nascar",
           "nfl": "nfl", "nba": "nba", "nhl": "nhl"}.get(
        (request.args.get("sport") or "").lower())
    if not key:
        return jsonify({"error": "unknown sport"}), 400
    if deep_cache.running_anywhere(key):
        return jsonify({"started": False, "already_running": True,
                        "status": deep_cache.status(key)})
    started = bool(_BG_OWNER and key in deep_cache._jobs
                   and deep_cache.run_job(key, force=True))
    queued = False if started else deep_cache.request_rerun(key)
    return jsonify({"started": started or queued, "queued": queued,
                    "status": deep_cache.status(key)})


# Latest completed deep-season run, kept in-process. {agg, season}.
_deep = {"agg": None, "season": None}


@app.route("/api/baseball/futures/deep", methods=["POST"])
def api_baseball_deep_start():
    """Kick off the deep multicore season run in the background (it takes minutes).
    Poll /deep/status; when ready, GET /futures?engine=deep for the deep board."""
    import deep_season
    import deep_cache
    if (deep_season.progress_read().get("running")
            or deep_cache.running_anywhere("mlb_deep")):
        return jsonify({"started": False, "already_running": True,
                        "progress": deep_season.progress_read()})
    # Force a fresh run; result is persisted to disk and reused for everyone.
    # Same worker-lottery rule as /api/sim/rerun: the owner starts it directly,
    # anyone else queues it for the owner to pick up within seconds.
    started = bool(_BG_OWNER and "mlb_deep" in deep_cache._jobs
                   and deep_cache.run_job("mlb_deep", force=True))
    queued = False if started else deep_cache.request_rerun("mlb_deep")
    return jsonify({"started": started or queued, "queued": queued,
                    "season": str(clock.today_et().year)})


@app.route("/api/baseball/live/<int:game_pk>")
def api_baseball_live(game_pk):
    """Live-game feedback: pitcher pitch counts + lines, per-hitter AB results, and
    our model's hit odds (incl. conditional 2nd-hit + live remaining-AB odds)."""
    try:
        return jsonify(baseball.live_game_feedback(game_pk))
    except Exception as e:
        return jsonify({"error": f"live feed failed: {e}"}), 502


@app.route("/api/baseball/team")
def api_baseball_team():
    """Per-player simulated season stat lines for one team from the latest deep
    run (hits/HR/BB/K/R for bats, IP/K/BB/ERA for arms). Needs ?abbr=NYY."""
    import deep_season
    season = request.args.get("season") or str(clock.today_et().year)
    abbr = (request.args.get("abbr") or "").upper()
    _deep_refresh()                 # adopt a run a sibling worker finished
    agg = _deep.get("agg")
    if not agg or _deep.get("season") != season:
        return jsonify({"error": "Run the deep simulation first to get player lines."}), 409
    try:
        amap = baseball._abbr_map(season)            # tid -> abbr
        tid = next((t for t, a in amap.items() if a == abbr), None)
        if tid is None:
            return jsonify({"error": f"unknown team {abbr}"}), 404
        return jsonify(deep_season.team_detail(agg, season, tid))
    except Exception as e:
        return jsonify({"error": f"team detail failed: {e}"}), 502


@app.route("/api/baseball/futures/deep/status")
def api_baseball_deep_status():
    import deep_season
    season = request.args.get("season") or str(clock.today_et().year)
    # progress_read, not PROGRESS: the run happens in ONE worker and this poll
    # lands on any of them -- read from memory, two of three polls denied a run
    # was in flight and the loading bar flickered or never appeared.
    p = deep_season.progress_read()
    if p.get("running") and p.get("started"):
        import time as _t
        done, total = p.get("done", 0), p.get("total", 1) or 1
        elapsed = _t.time() - p["started"]
        p["pct"] = round(100 * done / total, 1)
        p["eta_sec"] = round(elapsed / done * (total - done)) if done else None
    import deep_cache
    _deep_refresh()
    p["ready"] = bool(_deep.get("agg") and _deep.get("season") == season)
    p["age_sec"] = deep_cache.age("mlb_deep")
    # Queued-but-not-started (a rerun clicked during the startup grace, or in
    # the seconds before the owner picks it up) and how the LAST attempt ended
    # (done / empty / error+why) -- so "nothing happens" and "it ran and was
    # rejected" and "it crashed" finally look different from the phone.
    p["queued"] = "mlb_deep" in deep_cache._json_read(deep_cache._RERUN_REQ)
    p["last"] = deep_cache.run_state("mlb_deep")
    import deep_history
    p["history_dates"] = deep_history.dates()[:60]
    return jsonify(p)


@app.route("/api/baseball/coherence")
def api_baseball_coherence():
    """Is Kalshi's futures book coherent with ITSELF? Model-free checks:
    P(win WS | win pennant) per team, the league split of WS probability, and
    parent/child price ordering. A flag says two of the venue's own prices
    cannot both be right — no model required. ?date=YYYY-MM-DD serves a stored
    nightly snapshot instead of a live check."""
    import coherence
    date = request.args.get("date")
    if date:
        rec = coherence.load_day(date)
        if not rec:
            return jsonify({"error": f"no snapshot for {date}",
                            "dates": coherence.history_dates()[-30:]}), 404
        return jsonify(rec)
    try:
        out = coherence.check()
    except Exception as e:
        return jsonify({"error": f"coherence check failed: {e}"}), 502
    out["dates"] = coherence.history_dates()[-30:]
    return jsonify(out)


@app.route("/api/baseball/futures/deep/history")
def api_baseball_deep_history():
    """What changed between two nightly deep runs, and what each change was worth.

    ?date=YYYY-MM-DD picks a day (default: the newest run). Every day describes
    the move from the PREVIOUS stored run to itself, so the current day is
    answerable as soon as its run lands."""
    import deep_history
    date = request.args.get("date") or None
    # A range collapses every run in the window into ONE box. Reading a week as
    # seven separate boxes is how a +3 on Monday and a -4 on Thursday get read as
    # two moves instead of a -1 week.
    d_from = request.args.get("from") or None
    d_to = request.args.get("to") or None
    try:
        if d_from or d_to:
            rep = deep_history.report_range(d_from, d_to or date)
        else:
            rep = deep_history.report(date)
    except Exception as e:
        return jsonify({"error": f"history failed: {e}"}), 502
    if not rep:
        return jsonify({"empty": True, "dates": deep_history.dates(),
                        "message": "No deep-run history yet. It starts building "
                                   "from the next nightly run."})
    return jsonify(rep)


@app.route("/api/baseball/futures/deep/history/export")
def api_baseball_deep_history_export():
    """The whole stored history as plain JSON, for the nightly Action to commit
    to the repo.

    This is a PULL: the app holds no GitHub credentials and never writes there.
    The scheduled workflow fetches this, writes it under history/, and commits
    with its own token -- the same direction the weekly sim-rerun workflow
    already runs in."""
    import deep_history
    try:
        return jsonify(deep_history.export_bundle())
    except Exception as e:
        return jsonify({"error": f"export failed: {e}"}), 502


@app.route("/api/baseball/futures/deep/history/restore", methods=["POST"])
def api_baseball_deep_history_restore():
    """Pull the repo copy back into this host's cache. Runs automatically on boot;
    this endpoint is for forcing it after a manual snapshot."""
    import deep_history
    try:
        return jsonify(deep_history.restore_from_github(
            overwrite=request.args.get("overwrite") in ("1", "true", "yes")))
    except Exception as e:
        return jsonify({"error": f"restore failed: {e}"}), 502


@app.route("/api/commodities/meta")
def api_commodities_meta():
    """Commodity list for the SIMULATOR tab's price sim. The Commodities scanner
    tab is gone, but the sim still offers commodities alongside crypto, and this
    is what fills that dropdown."""
    import commodities
    return jsonify({k: v["label"] for k, v in commodities.COMMODITIES.items()})


@app.route("/api/bestbets")
def api_bestbets():
    """Cross-sport Best Bets: every positive net-of-fee edge across all our
    models (MLB slate/props, season futures, UFC, tennis, crypto, arbitrage),
    ranked by the edge you'd actually bank. Sources degrade independently."""
    import bestbets
    date = request.args.get("date") or clock.today_et().isoformat()
    try:
        sims = tiers.cap_sims(_tier(), request.args.get("sims", 2500))
    except ValueError:
        return jsonify({"error": "bad params"}), 400
    try:
        return jsonify(bestbets.board(date, date[:4], sims=sims))
    except Exception as e:
        return jsonify({"error": f"best bets failed: {e}"}), 502


@app.route("/api/baseball/edges")
def api_baseball_edges():
    """Rank the biggest model/sim-vs-Kalshi disparities across the day's slate.
    For each leg we can price live, edge = our simulated probability minus
    Kalshi's implied price. A disagreement finder, flagged by confidence."""
    date = request.args.get("date") or clock.today_et().isoformat()
    season = request.args.get("season") or date[:4]
    try:
        min_edge = float(request.args.get("min_edge", 4))
        sims = tiers.cap_sims(_tier(), request.args.get("sims", 4000))
    except ValueError:
        return jsonify({"error": "bad params"}), 400
    try:
        games = baseball.analyze_slate(date, season)
    except Exception as e:
        return jsonify({"error": f"baseball data failed: {e}"}), 502
    res = baseball.find_edges(games, n_sims=sims, min_edge=min_edge, types=_prop_types())
    res["date"] = date
    return jsonify(res)


@app.route("/api/baseball/pick6")
def api_baseball_pick6():
    """DraftKings Pick 6 board: our player-prop projections framed as More/Less at
    DK-style lines, from the same shared game sim the combos use."""
    date = request.args.get("date") or clock.today_et().isoformat()
    season = request.args.get("season") or date[:4]
    try:
        games = baseball.analyze_slate(date, season)
    except Exception as e:
        return jsonify({"error": f"baseball data failed: {e}"}), 502
    return jsonify(baseball.pick6_board(games))


@app.route("/api/baseball/pick6/sheet")
def api_baseball_pick6_sheet():
    """Pick 6 game browser: one game's full per-player simulated stat sheet
    (avg + More% at every line) plus the slate's game list. ?date=&pk=."""
    date = request.args.get("date") or clock.today_et().isoformat()
    season = request.args.get("season") or date[:4]
    try:
        pk = int(request.args.get("pk", 0)) or None
    except ValueError:
        pk = None
    try:
        games = baseball.analyze_slate(date, season)
    except Exception as e:
        return jsonify({"error": f"baseball data failed: {e}"}), 502
    return jsonify(baseball.pick6_game_sheet(games, pk))


@app.route("/api/baseball/pick6/eval", methods=["POST"])
def api_baseball_pick6_eval():
    """Exact joint odds for a hand-built same-game Pick 6 slip (masks ANDed on
    the shared sim, not an independence product)."""
    d = request.get_json(force=True, silent=True) or {}
    date = d.get("date") or clock.today_et().isoformat()
    legs = d.get("legs") or []
    try:
        pk = int(d.get("pk", 0))
    except (ValueError, TypeError):
        return jsonify({"error": "bad pk"}), 400
    if not (1 <= len(legs) <= 6):
        return jsonify({"error": "1-6 legs"}), 400
    try:
        games = baseball.analyze_slate(date, date[:4])
    except Exception as e:
        return jsonify({"error": f"baseball data failed: {e}"}), 502
    return jsonify(baseball.pick6_eval(games, pk, legs))


@app.route("/api/baseball/sgp")
def api_baseball_sgp():
    """Same-game parlays with correlation-aware (simulated) joint odds. Legs from
    one game are correlated, so these are read off a full game simulation rather
    than multiplying independent marginals."""
    locked = _locked("same_game_parlay")
    if locked:
        return locked
    date = request.args.get("date") or clock.today_et().isoformat()
    season = request.args.get("season") or date[:4]
    try:
        legs = tiers.cap_legs(_tier(), request.args.get("legs", 3))
        target = float(request.args.get("target", 55))
        payout = request.args.get("payout")
        payout = float(payout) if payout not in (None, "", "0") else 0
        sims = tiers.cap_sims(_tier(), request.args.get("sims", 5000))
    except ValueError:
        return jsonify({"error": "bad legs/target"}), 400
    try:
        games = baseball.analyze_slate(date, season)
    except Exception as e:
        return jsonify({"error": f"baseball data failed: {e}"}), 502
    res = baseball.build_same_game_parlays(games, n_legs=legs, target_pct=target, types=_prop_types(),
                                           target_payout=payout, n_sims=sims,
                                           max_legs=tiers.cap_legs(_tier(), 30),
                                           allow_live=_allow_live())
    return jsonify(res)


@app.route("/api/baseball/mixed")
def api_baseball_mixed():
    """One parlay across multiple games that may stack correlated legs in a game
    and add single legs from others. Within a game -> simulated joint odds;
    across games -> independent product (measured: see combo_engine).

    `objective` picks which point on the price/probability frontier to return —
    safe (likeliest), value (best EV), or balanced (likeliest that isn't -EV)."""
    locked = _locked("mixed_parlay")
    if locked:
        return locked
    date = request.args.get("date") or clock.today_et().isoformat()
    season = request.args.get("season") or date[:4]
    try:
        legs = tiers.cap_legs(_tier(), request.args.get("legs", 4))
        target = float(request.args.get("target", 55))
        payout = request.args.get("payout")
        payout = float(payout) if payout not in (None, "", "0") else 0
        sims = tiers.cap_sims(_tier(), request.args.get("sims", 5000))
        max_total = tiers.cap_legs(_tier(), 30)   # tier ceiling is the only cap
    except ValueError:
        return jsonify({"error": "bad legs/payout"}), 400
    # same_game off -> one leg per game (a plain cross-game combo, still simulated
    # so every leg shows model vs sim); on -> may stack correlated legs in a game.
    same_game = request.args.get("same_game", "1") != "0"
    # The leg-count and payout targets are each "require" (hard) / "prefer"
    # (recommendation) / "off", combined by conn ('and'/'or').
    modes = ("require", "prefer", "off")
    legs_mode = request.args.get("legs_mode", "prefer")
    payout_mode = request.args.get("payout_mode", "off")
    conn = "and" if request.args.get("conn") == "and" else "or"
    if legs_mode not in modes:
        legs_mode = "prefer"
    if payout_mode not in modes:
        payout_mode = "off"
    include_live = request.args.get("include_live") == "1"
    import combo_engine
    objective = request.args.get("objective", "balanced")
    if objective not in combo_engine.OBJECTIVES:
        objective = "balanced"
    # Optional game grid: "sel" = comma list of "pk" (whole game) or "pk:Team"
    # (only that team's legs). Empty -> all games.
    sel = {}
    for tok in (request.args.get("sel") or "").split(","):
        tok = tok.strip()
        if not tok:
            continue
        pk, _, team = tok.partition(":")
        try:
            sel[int(pk)] = team or True
        except ValueError:
            pass
    # Optional confidence CEILING -> the floor becomes a band and the builder
    # walks each ladder to the line that lands inside it.
    cap = request.args.get("cap")
    try:
        cap = float(cap) if cap not in (None, "") else None
    except ValueError:
        cap = None
    if cap is not None and not (0 < cap <= 100):
        cap = None
    max_bet = request.args.get("max_bet") == "1"
    # Optimal mode: ONE input (the payout target). The leg count, the per-leg
    # confidence and the game mix are all outputs -- legs_mode off, payout
    # required, "balanced" objective (likeliest slip that isn't -EV and is
    # actually fillable), per-leg floor swept. See combo_engine.best_target.
    optimal = request.args.get("optimal") == "1"
    if optimal and not (payout and payout > 1):
        return jsonify({"error": "optimal mode needs a payout target above 1x"}), 400
    # Real progress: the client mints a token, we count games as they finish
    # simulating, and /api/progress reports it. One build = one HTTP request, so
    # this is the only way the browser can see inside it.
    ptok = (request.args.get("ptok") or "")[:64] or None

    # EVERY request-dependent value has to be read here, on the request thread.
    # The build runs in a background thread where `request` does not exist, and
    # _prop_types() reads it -- called from in there it raises "Working outside
    # of request context" and the build dies instantly instead of running.
    prop_types = _prop_types()
    # YES-only / NO-only. A confidence target picks a PROBABILITY, not a
    # direction, so it cannot express "I want the home runs to happen": a home
    # run is a ~12% event, and a maker asked for the likeliest slip will always
    # answer with three fades. This is the control that says which side.
    sides_pref = (request.args.get("sides") or "both").lower()
    sides = ({"yes"} if sides_pref == "yes"
             else {"no"} if sides_pref == "no" else None)
    # EDGE MODE: only legs whose pre-blend model number beats the ask by at
    # least this many cents. Blank = off; 0 is a real floor ("any non-negative
    # model edge"). Bounded to the range that can exist at all.
    min_edge = request.args.get("min_edge")
    try:
        min_edge = float(min_edge) if min_edge not in (None, "") else None
    except ValueError:
        min_edge = None
    if min_edge is not None:
        min_edge = max(-20.0, min(30.0, min_edge))

    def _slip_log_safe(item):
        """File the built slip in the slip ledger (best-effort bookkeeping --
        a build must never fail because a log write did not work). Live builds
        are a different joint quantity and are never logged."""
        if not item or include_live:
            return
        try:
            import sliplog
            sliplog.log_from_item(item, sport="mlb", date=date)
        except Exception as _e:
            errlog.note("SLIP-log", _e)

    def _core():
        """The build itself, as a plain dict. Split out so it can run in the
        background instead of holding an HTTP request open."""
        nonlocal payout
        # The board fetch runs IN the job, not on the request thread: an
        # expired slate cache used to block the HTTP request for the whole
        # board rebuild while the bar read a blind time estimate. The job
        # phase names the wait instead.
        if ptok:
            baseball.job_update(ptok, phase="building today's board…")
        try:
            games = baseball.analyze_slate(date, season)
        except Exception as e:
            return {"error": f"baseball data failed: {e}"}

        def _build(target_pct, _mb=False, _opt=False):
            return baseball.build_mixed_parlay(
                games, n_legs=legs, target_pct=target_pct, sides=sides,
                cap_pct=None if (_mb or _opt) else cap,
                target_payout=0 if _mb else payout, n_sims=sims,
                max_legs_per_game=max_total if same_game else 1,
                max_total_legs=max_total,
                legs_mode="off" if _opt else legs_mode,
                payout_mode="require" if _opt else payout_mode,
                conn=conn, game_sel=sel or None,
                include_live=include_live, types=prop_types,
                objective="balanced" if _opt else objective, max_bet=_mb,
                progress_token=ptok, min_edge_c=min_edge)

        # ONE Kalshi book for the whole build (see kalshi_mlb.pinned): every
        # pass prices against the same snapshot, and the index can never
        # expire mid-build and charge the user a full refetch per pass.
        import kalshi_mlb
        with kalshi_mlb.pinned():
            if optimal:
                # A target past the exchange ceiling is not reachable in real money;
                # clamp and say so rather than optimizing toward a payout Kalshi caps.
                capped = payout > combo_engine.MAX_PAYOUT_X
                payout = min(payout, combo_engine.MAX_PAYOUT_X)
                # The floor sweep is N builds on ONE token: declare N up front so
                # the bar divides by it instead of growing its total mid-flight.
                baseball.progress_declare(ptok, len(combo_engine.OPTIMAL_FLOORS))
                item = combo_engine.best_target(lambda f: _build(f, _opt=True))
                if item:
                    item["objective"] = "optimal"
                    item["target_payout_x"] = payout
                    item["target_capped"] = capped
                    _slip_log_safe(item)
                    return {"parlay": item}
                pre = [g for g in games if (g.get("live") or {}).get("state") == "Preview"]
                if pre and not any(g.get("pick_price_cents") for g in pre):
                    return {"parlay": None, "hint": "kalshi_unpriced",
                            "n_pregame": len(pre)}
                return {"parlay": None, "hint": "optimal_unbuildable",
                        "target_payout_x": payout}
            if max_bet:
                # The band and the payout target are dropped and the per-leg floor is
                # swept: reaching the MARKET payout cap needs room the maker's own
                # settings would not give it. See combo_engine.MAX_BET_FLOORS.
                baseball.progress_declare(ptok, len(combo_engine.MAX_BET_FLOORS))
                item = combo_engine.best_max_bet(lambda f: _build(f, _mb=True))
                if not item:
                    return {"parlay": None, "hint": "max_bet_unreachable",
                            "cap_x": combo_engine.MAX_PAYOUT_X}
            else:
                item = _build(target)
            if not item:
                # Say WHY nothing built. "No eligible games for that selection" sends
                # the user to loosen filters that were never the problem: the common
                # cause is that the slate carries no Kalshi prices yet (lines post
                # near game time, or the exchange is unreachable), and the maker only
                # builds legs you can actually place.
                pre = [g for g in games if (g.get("live") or {}).get("state") == "Preview"]
                if pre and not any(g.get("pick_price_cents") for g in pre):
                    return {"parlay": None, "hint": "kalshi_unpriced",
                            "n_pregame": len(pre)}
                # A one-sided pool and a confidence floor fight each other: YES legs
                # for rare events (home runs, steals) live at 5-20%, so a 55% floor
                # empties the pool completely. Name that instead of blaming the
                # game selection.
                if min_edge is not None:
                    return {"parlay": None, "hint": "edge_empty",
                            "min_edge_c": min_edge, "target_pct": target}
                if sides is not None:
                    return {"parlay": None, "hint": "sides_empty",
                            "sides": sides_pref, "target_pct": target}
            _slip_log_safe(item)
            return {"parlay": item}

    # ---- run it in the BACKGROUND when the client gave us a token ----------
    # A full slate is 14 games and one game's 4,000-run simulation costs ~32s on
    # a fast desktop and well over 100s on a single shared cloud CPU. That is
    # minutes of work, against a 120s gunicorn worker timeout -- so the request
    # was killed every time, and killing a worker is exactly what fails the
    # platform's 5-second health probe. The build could not have completed and
    # took the instance down trying.
    #
    # So the build no longer runs inside the request. The token the progress bar
    # already mints keys a background job; the request returns immediately and
    # the client polls /api/progress, which it was doing anyway.
    if ptok:
        # The job lives on SHARED STORAGE, not in this worker's memory. With
        # three workers the browser's poll round-robins, so a poll landing on a
        # worker that had never seen the token used to start a SECOND build --
        # the progress bar climbed to "game 10 of 11" and then dropped back to
        # "game 1 of 11" because it was reading a different worker's duplicate,
        # and three simulations of the same slate fought over one CPU.
        job = baseball.job_read(ptok)
        # Serving a finished result must be IDEMPOTENT. It used to job_drop
        # before the response was known to have reached the phone; if that one
        # response was lost (a backgrounded tab, a dropped socket -- the normal
        # weather of mobile), the next poll found no job, WON a fresh claim,
        # and silently started the whole build over. From the phone that looks
        # like the bar finishing and then snapping back to "building" -- and
        # the service worker's transparent retry could trigger it all by
        # itself. Tokens are unique per click and job files are swept after an
        # hour, so the stored result can safely be served as many times as it
        # is asked for.
        if job and job.get("status") == "done":
            return jsonify(job.get("result") or {})
        if job and job.get("status") == "error":
            return jsonify({"error": job.get("error") or "build failed"}), 502
        # A running job whose heartbeat stopped is DEAD, not busy -- rebuild.
        if (job and job.get("status") == "running"
                and baseball.job_takeover(ptok, _JOB_DEAD_S)):
            errlog.note("COMBO-dead-job",
                        RuntimeError("build heartbeat stopped; rebuilding"),
                        path=ptok)
        # Exactly one worker wins the claim; everyone else just reports 202.
        if not baseball.job_claim(ptok):
            return jsonify({"status": "building", "token": ptok}), 202
        _run_job(ptok, _core, "COMBO-build")
        return jsonify({"status": "building", "token": ptok}), 202
    return jsonify(_core())


@app.route("/api/baseball/value")
def api_baseball_value():
    """Kalshi player-prop prices vs each batter's recent game-log rate -> value plays."""
    locked = _locked("racing_picks")   # gated with the other edge-finder tools (Pro)
    if locked:
        return locked
    import value
    season = request.args.get("season") or clock.today_et().isoformat()[:4]
    try:
        n = max(5, min(40, int(request.args.get("games", 15))))
        edge = max(3.0, float(request.args.get("edge", 10)))
    except ValueError:
        return jsonify({"error": "bad params"}), 400
    try:
        return jsonify(value.find_value(season=season, n_games=n, min_edge=edge))
    except Exception as e:
        return jsonify({"error": f"value scan failed: {e}"}), 502


@app.route("/api/baseball/record")
def api_baseball_record():
    try:
        baseball.grade_picks()
    except Exception as _e:
        errlog.note("APP-api_baseball_record", _e)
    return jsonify(store.mlb_record())


@app.route("/api/baseball/proplog")
def api_baseball_proplog():
    """Aggregate accuracy of the prop model, recent form, and Kalshi's own price,
    from the background recorder's logged + graded batter props."""
    import mlb_recorder
    try:
        edge = max(3.0, float(request.args.get("edge", 8)))
    except ValueError:
        return jsonify({"error": "bad params"}), 400
    try:
        mlb_recorder.grade_due()   # grade any games that just went final
    except Exception as _e:
        errlog.note("APP-api_baseball_proplog", _e)
    report = store.prop_report(min_edge=edge)
    report["recorder"] = mlb_recorder.status()
    return jsonify(report)


@app.route("/api/baseball/sliplog")
def api_baseball_sliplog():
    """Slip-level calibration: claimed joint probability vs realized wins over
    every parlay the maker built -- the correlation premium's track record."""
    try:
        import sliplog
        sliplog.grade_due()
    except Exception as _e:
        errlog.note("APP-api_baseball_sliplog", _e)
    return jsonify(store.slip_report())


@app.route("/api/baseball/hits")
def api_baseball_hits():
    """Predicted Hits + Risky Hits: from the recorder's graded props, the ones the
    model liked that cashed, and the longshots that would've paid big."""
    import mlb_recorder
    date = request.args.get("date")
    if date == "today":
        date = clock.today_et().isoformat()
    try:
        mlb_recorder.grade_due()   # grade any games that just went final
    except Exception as _e:
        errlog.note("APP-api_baseball_hits", _e)
    res = store.prop_hit_combos(date=date)
    res["recorder"] = mlb_recorder.status()
    return jsonify(res)


@app.route("/api/stats")
def api_stats():
    return jsonify(store.stats())


if __name__ == "__main__":
    import os
    import argparse
    # Works the same in any shell:  python app.py --debug --port 8080
    parser = argparse.ArgumentParser(description="Vigil server")
    parser.add_argument("--debug", action="store_true",
                        help="auto-reload on file changes (no manual restart needed)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 5000)))
    args = parser.parse_args()
    # --debug flag OR DEBUG=1 env var both enable auto-reload.
    debug = args.debug or os.environ.get("DEBUG") == "1"
    # Start the recorder now (skip in the reloader's watcher parent to avoid two).
    if not debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        _ensure_recorder()
    app.run(host="0.0.0.0", port=args.port, debug=debug, use_reloader=debug)
