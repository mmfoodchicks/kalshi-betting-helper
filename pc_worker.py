"""Vigil PC compute worker — one cycle: simulate today's slate, upload the sims.

Runs on the app owner's desktop (Windows/anything with Python), NOT on the
server. The bootstrap (vigil-pc.bat) git-pulls before every cycle, so this file
always runs the same code the server deployed; a version mismatch is caught by
the server's schema gate and simply skipped until the next pull.

What one cycle does:
  1. Ask the server which game sims it already has fresh (/api/sim/have).
  2. Build the slate locally (the same baseball.analyze_slate the server runs)
     and simulate every game the server still needs (~32s a game on a desktop
     vs 200s+ on the server's shared CPU -- the whole point).
  3. Upload each finished sim (/api/sim/upload). The server adopts it
     atomically; the combo maker's next Build reads it in about a second.

Config: vigil-pc.cfg (JSON) next to this file, or environment variables:
  {"app_url": "https://vigil-xxxx.onrender.com", "sim_token": "..."}

If ANYTHING here fails, nothing breaks: the server keeps computing for itself
exactly as it does when this worker doesn't exist. Exit code 0 = cycle done.
"""

import gzip
import json
import os
import sys
import time
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)


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


def _api(url, tok, path, data=None, headers=None):
    req = urllib.request.Request(url + path, data=data, headers={
        "X-Sim-Token": tok, "User-Agent": "vigil-pc-worker",
        **(headers or {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def main():
    url, tok = _config()
    # Local sim cache lives next to this checkout (Windows has no /tmp).
    os.environ.setdefault("VIGIL_SIM_CACHE_DIR", os.path.join(_HERE, "pc-simcache"))
    os.environ.setdefault("DEEP_CACHE_DIR", os.path.join(_HERE, "pc-deepcache"))

    import clock
    import baseball

    have = _api(url, tok, "/api/sim/have")
    if have.get("schema") != baseball.GAME_SIM_SCHEMA:
        print(f"[vigil-pc] schema skew (server {have.get('schema')} vs local "
              f"{baseball.GAME_SIM_SCHEMA}) - a deploy is mid-flight; next "
              "cycle's git pull will converge. Nothing to do.")
        return
    server_has = {int(k) for k, age in (have.get("have") or {}).items()
                  if age < 1800}          # fresh enough that re-upload is waste

    date = clock.today_et().isoformat()
    season = date[:4]
    print(f"[vigil-pc] slate {date} - building (server already fresh on "
          f"{len(server_has)} games)")
    games = baseball.analyze_slate(date, season)
    todo = [g for g in games
            if (g.get("live") or {}).get("state") != "Final"
            and g.get("game_pk") and g["game_pk"] not in server_has]
    print(f"[vigil-pc] {len(todo)} game(s) to simulate")
    for i, gm in enumerate(todo, 1):
        pk = gm["game_pk"]
        t0 = time.time()
        try:
            baseball._game_sim(gm)          # writes {pk}.pkl into the local cache
        except Exception as e:
            print(f"[vigil-pc]  {i}/{len(todo)} {gm.get('matchup')}: sim failed "
                  f"({type(e).__name__}: {e}) - skipping")
            continue
        path = os.path.join(baseball._SIM_DISK, f"{pk}.pkl")
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
        except OSError as e:
            print(f"[vigil-pc]  {i}/{len(todo)} {gm.get('matchup')}: no local "
                  f"pickle ({e}) - skipping")
            continue
        body = gzip.compress(raw)
        try:
            res = _api(url, tok,
                       f"/api/sim/upload?pk={pk}&schema={baseball.GAME_SIM_SCHEMA}",
                       data=body, headers={"Content-Encoding": "gzip",
                                           "Content-Type": "application/octet-stream"})
            print(f"[vigil-pc]  {i}/{len(todo)} {gm.get('matchup')}: simmed + "
                  f"uploaded {len(body)//1024}KB in {time.time()-t0:.0f}s "
                  f"(adopted={res.get('adopted')})")
        except Exception as e:
            print(f"[vigil-pc]  {i}/{len(todo)} {gm.get('matchup')}: upload "
                  f"failed ({type(e).__name__}: {e}) - server will self-compute")
    print("[vigil-pc] cycle done")


if __name__ == "__main__":
    main()
