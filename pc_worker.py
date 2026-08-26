"""Vigil PC compute worker — one cycle: build every simulator's artifacts
locally, upload whatever is fresher than the server's copy.

Runs on the app owner's desktop, not the server. pc_loop.py keeps this file
current (git check every minute) and runs a cycle every 10 minutes or right
after an update. The server adopts uploads atomically and computes for itself
whenever this worker is off — the PC can only add speed or be ignored.

What one cycle covers (each task independently guarded — one broken sport
never strands the rest):

  1. MLB game sims — every non-Final game the server doesn't have fresh
     (the combo maker's cost center: ~4s a game here vs 200s+ there).
  2. Every boardshare board: NFL game board + DFS board + parlay slate sims,
     the Kalshi NFL index, golf, LoL, NBA/WNBA, NHL, tennis, UFC, futures.
     Builders are the SAME functions the server runs; they write into the
     local boards store and re-invoking them is cheap once fresh.
  3. The nightly deep run (4,000 seasons + quality gate + day history) when
     the server's copy is older than 19h — uploading mlb_deep.pkl makes the
     server's own daily scheduler see "already ran today" and skip its
     multi-hour rebuild. Coherence/ump extras stay server-side (they feed
     the GitHub history flow, not the artifact stores).
  4. Sync: upload every local artifact meaningfully fresher than the
     server's copy, gzip'd, through the schema-gated door.
"""

import gzip
import json
import os
import sys
import time
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

# Local stores live next to the checkout (Windows has no /tmp). MUST be set
# before any model module import resolves its store path.
os.environ.setdefault("VIGIL_SIM_CACHE_DIR", os.path.join(_HERE, "pc-simcache"))
os.environ.setdefault("DEEP_CACHE_DIR", os.path.join(_HERE, "pc-deepcache"))


def _config():
    cfg = {}
    path = os.path.join(_HERE, "vigil-pc.cfg")
    if os.path.exists(path):
        with open(path) as fh:
            cfg = json.load(fh)
    url = (os.environ.get("VIGIL_APP_URL") or cfg.get("app_url") or "").rstrip("/")
    tok = os.environ.get("SIM_TOKEN") or cfg.get("sim_token") or ""
    if not url or not tok:
        print("[vigil-pc] missing config: set app_url + sim_token in "
              "vigil-pc.cfg (see vigil-pc.cfg.example)")
        sys.exit(1)
    return url, tok


def _api(url, tok, path, data=None, headers=None, timeout=90):
    req = urllib.request.Request(url + path, data=data, headers={
        "X-Sim-Token": tok, "User-Agent": "vigil-pc-worker",
        **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _sync_kind(url, tok, kind, min_fresher_s=60):
    """Upload every local artifact of `kind` that is fresher than the server's
    copy by a meaningful margin. Returns how many were adopted."""
    import artifacts
    have = _api(url, tok, f"/api/art/have?kind={kind}")
    if have.get("schema") != artifacts.SCHEMA:
        print(f"[vigil-pc] {kind}: schema skew (server {have.get('schema')} vs "
              f"local {artifacts.SCHEMA}) - next git pull converges; skipping")
        return 0
    server = have.get("have") or {}
    local_dir = artifacts.dir_for(kind)
    sent = 0
    for name, l_age in sorted(artifacts.ages(kind).items()):
        s_age = server.get(name)
        if s_age is not None and (s_age - l_age) < min_fresher_s:
            continue                        # server copy as fresh or fresher
        try:
            with open(os.path.join(local_dir, name), "rb") as fh:
                raw = fh.read()
        except OSError:
            continue
        body = gzip.compress(raw)
        try:
            res = _api(url, tok,
                       f"/api/art/upload?kind={kind}&name={name}&schema={artifacts.SCHEMA}",
                       data=body, headers={"Content-Encoding": "gzip",
                                           "Content-Type": "application/octet-stream"})
            if res.get("adopted"):
                sent += 1
                print(f"[vigil-pc]   ↑ {kind}/{name} ({len(body)//1024}KB)")
        except Exception as e:
            print(f"[vigil-pc]   {kind}/{name}: upload failed "
                  f"({type(e).__name__}: {e}) - server will self-compute")
    if sent == 0:
        print(f"[vigil-pc] {kind}: server already current")
    return sent


def _task_mlb_sims(url, tok):
    import baseball
    import clock
    have = _api(url, tok, "/api/art/have?kind=gamesim").get("have") or {}
    server_has = {n for n, age in have.items() if age < 1800}
    date = clock.today_et().isoformat()
    games = baseball.analyze_slate(date, date[:4])
    # Pre-game only: a live game is priced by the server's live-resume path,
    # never by this pregame cache -- simming it here is upload noise.
    todo = [g for g in games
            if (g.get("live") or {}).get("state") not in ("Final", "Live")
            and g.get("game_pk") and f"{g['game_pk']}.pkl" not in server_has]
    print(f"[vigil-pc] MLB: {len(todo)} game sim(s) needed")
    for i, gm in enumerate(todo, 1):
        pk = gm.get("game_pk")
        # The server asked because ITS copy is over 30 minutes old. Answering
        # from our own aging local pickle takes 1s and re-stamps old work as
        # fresh at upload (the mtime restarts at the door) -- the "simmed in
        # 1s" lines that made a whole cycle a no-op. If the local copy is
        # nearly that old too, drop it so _game_sim really re-simulates.
        try:
            path = os.path.join(baseball._SIM_DISK, f"{pk}.pkl")
            if time.time() - os.stat(path).st_mtime > 1500:
                os.remove(path)
                baseball._cache.pop(("game_sim", pk), None)
        except OSError:
            pass
        t0 = time.time()
        try:
            baseball._game_sim(gm)
            print(f"[vigil-pc]   {i}/{len(todo)} {gm.get('matchup')}: "
                  f"simmed in {time.time()-t0:.0f}s")
        except Exception as e:
            print(f"[vigil-pc]   {i}/{len(todo)} {gm.get('matchup')}: "
                  f"failed ({type(e).__name__}: {e})")


def _task_boards():
    """Invoke every board builder the server serves. boardshare's nonblocking
    pattern kicks a background build and returns stale-or-None meanwhile, so
    each builder is re-invoked over a wait window until its board is fresh
    (a fresh board returns instantly — re-invoking is then free)."""
    import nfl_preseason

    def _nfl_tasks():
        import nfl_game_sim
        import nfl_dfs_sim
        import kalshi_nfl
        pre = nfl_preseason.is_preseason()
        wk = nfl_game_sim.current_week(preseason=pre)
        kalshi_nfl.index()
        nfl_game_sim.board(wk, pre)
        nfl_dfs_sim.board(wk, pre)
        nfl_game_sim._slate_sims(wk, pre, 4000)
    builders = [("nfl", _nfl_tasks)]

    def _add(label, fn):
        builders.append((label, fn))
    import clock
    date = clock.today_et().isoformat()
    _add("golf", lambda: __import__("golf").board("pga"))
    _add("tennis", lambda: __import__("tennis_prices").board())
    _add("ufc", lambda: __import__("ufc_sim").board())
    _add("lol", lambda: __import__("lol").board(6))
    _add("nba", lambda: __import__("basket").board("nba", date))
    _add("wnba", lambda: __import__("basket").board("wnba", date))
    _add("nhl", lambda: __import__("hockey").board(date))
    _add("futures", lambda: __import__("mfutures").rows())
    # Racing DFS boards: correlated race sims + DK lineup search. Cheap on a
    # desktop, and the server then serves the uploaded board instead of
    # simulating on the shared CPU.
    _add("dfs-f1", lambda: __import__("racing_dfs").board("f1"))
    _add("dfs-nascar", lambda: __import__("racing_dfs").board("nascar"))

    deadline = time.time() + 300
    pending = dict(builders)
    while pending and time.time() < deadline:
        for label, fn in list(pending.items()):
            try:
                out = fn()
                # A builder that hands back a payload is done; None/(None, age)
                # means its background build is still running.
                val = out[0] if isinstance(out, tuple) else out
                if val is not None:
                    print(f"[vigil-pc] board {label}: fresh")
                    pending.pop(label, None)
            except Exception as e:
                print(f"[vigil-pc] board {label}: failed "
                      f"({type(e).__name__}: {e}) - skipping")
                pending.pop(label, None)
        if pending:
            time.sleep(12)
    for label in pending:
        print(f"[vigil-pc] board {label}: still building at deadline - "
              "will finish next cycle")


_MIN_CAREER_FRAC = float(os.environ.get("VIGIL_MIN_CAREER_FRAC") or 0.70)


def _task_deep(url, tok):
    """Every registered season sim the server runs nightly, PC edition. Each
    job saves under the same deep_cache key with the SAME payload shape the
    server's runner returns, so the server's daily scheduler sees "already
    ran today" from the uploaded file and skips its own rebuild. model_trust
    stays server-side (it replays the server's own prediction-log DB), as do
    the coherence/ump extras (they feed the GitHub history flow)."""
    have = _api(url, tok, "/api/art/have?kind=deep").get("have") or {}
    import deep_cache

    def _due(key, hours=19):
        age = have.get(f"{key}.pkl")
        if age is not None and age < hours * 3600:
            print(f"[vigil-pc] deep {key}: server copy {age/3600:.1f}h old - not due")
            return False
        return True

    def _mlb():
        """Same pipeline as the server's run_mlb: run, QUALITY GATE (a run
        built on partial roster hydration tracks record, not talent -- refuse
        to ship it), save the same {"agg", "season"} wrapper, day history."""
        import clock
        import deep_season
        season = str(clock.today_et().year)
        print("[vigil-pc] deep mlb: 4,000-season sim (the big one)...")
        t0 = time.time()
        profs = {}
        agg = deep_season.run_deep(season, n_seasons=4000, ret_profiles=profs)
        q = (agg or {}).get("quality") or {}
        if not agg or q.get("career_frac", 0) < _MIN_CAREER_FRAC:
            print(f"[vigil-pc] deep mlb: REJECTED by the quality gate "
                  f"(career coverage {100*q.get('career_frac', 0):.0f}%) - "
                  "not shipping; the server keeps its previous board")
            return
        deep_cache.save("mlb_deep", {"agg": agg, "season": season})
        try:
            import deep_history
            deep_history.build_day(agg, season, profs)
        except Exception as e:
            print(f"[vigil-pc] deep mlb: history step failed "
                  f"({type(e).__name__}: {e}) - the run itself still ships")
        print(f"[vigil-pc] deep mlb: done in {(time.time()-t0)/60:.1f} min "
              f"({q.get('teams_ok', '?')}/{q.get('teams', '?')} teams clean)")

    def _plain(key, fn):
        ret = fn()
        if ret is not None:
            deep_cache.save(key, ret)
            print(f"[vigil-pc] deep {key}: done")
        else:
            print(f"[vigil-pc] deep {key}: nothing to run (out of season?)")

    jobs = [
        ("mlb_deep", _mlb),
        ("f1", lambda: _plain("f1", lambda: __import__("racing_sim").sim_f1(3000))),
        ("nascar", lambda: _plain("nascar", lambda: __import__("racing_sim").sim_nascar(3000))),
        ("nfl_season", lambda: _plain("nfl_season", lambda: __import__("nfl_season").run_season(n=4000))),
        ("cfb", lambda: _plain("cfb", lambda: __import__("cfb").run_season(n=4000))),
        ("nfl", lambda: _plain("nfl", lambda: __import__("pro_sim").project("nfl", 4000))),
        ("nba", lambda: _plain("nba", lambda: __import__("pro_sim").project("nba", 4000))),
        ("nhl", lambda: _plain("nhl", lambda: __import__("pro_sim").project("nhl", 4000))),
    ]
    for key, fn in jobs:
        if not _due(key):
            continue
        try:
            fn()
        except Exception as e:
            print(f"[vigil-pc] deep {key}: failed ({type(e).__name__}: {e}) - "
                  "the server's own nightly still covers it")


def main():
    url, tok = _config()
    total = 0
    try:
        _task_mlb_sims(url, tok)
    except Exception as e:
        print(f"[vigil-pc] task MLB sims failed ({type(e).__name__}: {e}) - "
              "moving on")
    # Ship the game sims NOW. The boards task can sit in its wait loop for
    # five minutes, and the whole point of these sims is beating the server's
    # own 200s-per-game rebuild to the punch -- a user who pressed Build two
    # minutes after this cycle started was watching the server re-simulate
    # games this machine had already finished.
    try:
        total += _sync_kind(url, tok, "gamesim")
    except Exception as e:
        print(f"[vigil-pc] sync gamesim failed ({type(e).__name__}: {e})")
    for label, fn in (("boards", _task_boards),
                      ("deep nightly", lambda: _task_deep(url, tok))):
        try:
            fn()
        except Exception as e:
            print(f"[vigil-pc] task {label} failed ({type(e).__name__}: {e}) - "
                  "moving on")
    for kind in ("gamesim", "boards", "deep"):
        try:
            total += _sync_kind(url, tok, kind)
        except Exception as e:
            print(f"[vigil-pc] sync {kind} failed ({type(e).__name__}: {e})")
    print(f"[vigil-pc] cycle done - {total} artifact(s) shipped")


if __name__ == "__main__":
    main()
