"""Raw ESPN NBA game summaries for the matchup engine's research (RESEARCH ONLY).

The matchup engine needs more of each summary than nba_pbp keeps: the box
score (minutes, starters, positions, every counting stat), the full shot type
text ("Pullup Jump Shot", "Cutting Layup Shot", "Alley Oop Dunk Shot") and the
substitutions. Fitting will be iterated, so the raw summaries are kept ONCE,
gzipped, OUTSIDE the repo (never committed) and every parse re-reads them:

    <raw dir>/<season>/schedule.json      every event the scoreboards list
    <raw dir>/<season>/<event id>.json.gz one game's summary

Measured 2026-10-23: a summary is ~440 KB of JSON (~45 KB gzipped) and ~0.6 s
through the proxy; a regular season is 1,230 games. Six threads keep ESPN
answering (a 429/5xx backs off and retries; a game that still fails is left
for the next run, never written half).

  python3 research/nba_matchup_fetch.py <raw dir> --seasons 2022,2023,2024,2025,2026
"""
import argparse
import concurrent.futures as cf
import datetime
import gzip
import json
import os
import sys
import time
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import nba_pbp as P          # noqa: E402
import racing                # noqa: E402

_SITE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"


def _retry(fn, tries=4):
    for i in range(tries):
        try:
            return fn()
        except urllib.error.HTTPError as e:
            if e.code in (404,):
                return None
            time.sleep(1.5 * (i + 1) ** 2)
        except Exception:
            time.sleep(1.5 * (i + 1) ** 2)
    return None


def schedule(season, raw, today=None, threads=8):
    """Every event (regular season, play-in, postseason) of the season starting
    in `season`, walked one scoreboard per date, kept at <raw>/<season>/schedule.json.
    A completed season's file is reused; the current season is re-walked."""
    d = os.path.join(raw, str(season))
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "schedule.json")
    today = today or datetime.date.today()
    end = datetime.date(int(season) + 1, 6, 30)
    if os.path.exists(path) and end < today - datetime.timedelta(days=3):
        with open(path) as fh:
            return json.load(fh)
    days = []
    day = datetime.date(int(season), 10, 1)
    while day <= min(end, today + datetime.timedelta(days=2)):
        days.append(day)
        day += datetime.timedelta(days=1)
    out = {}

    def one(day):
        return _retry(lambda: P._day_events(day.strftime("%Y%m%d"))) or []

    with cf.ThreadPoolExecutor(threads) as ex:
        for evs in ex.map(one, days):
            for ev in evs:
                if ev["espn_year"] == int(season) + 1 and ev["st"] in (2, 3, 5):
                    out[ev["id"]] = ev
    evs = sorted(out.values(), key=lambda g: (g["date"] or "", g["id"]))
    with open(path + ".tmp", "w") as fh:
        json.dump(evs, fh)
    os.replace(path + ".tmp", path)
    return evs


def fetch_summaries(season, raw, events, threads=6, log=print, kinds=(2,)):
    d = os.path.join(raw, str(season))
    todo = [e for e in events if e.get("state") == "post" and e.get("st") in kinds
            and (e.get("st") != 2 or P.league_game(e))
            and not os.path.exists(os.path.join(d, f"{e['id']}.json.gz"))]
    log(f"[fetch] {season}: {len(events)} events, {len(todo)} summaries to fetch")
    t0 = time.time()
    done = failed = 0

    def one(e):
        js = _retry(lambda: racing._get_json(f"{_SITE}/summary?event={e['id']}", timeout=40))
        if not js or not js.get("plays") or not (js.get("boxscore") or {}).get("players"):
            return e["id"], False
        p = os.path.join(d, f"{e['id']}.json.gz")
        with gzip.open(p + ".tmp", "wt") as fh:
            json.dump(js, fh, separators=(",", ":"))
        os.replace(p + ".tmp", p)
        return e["id"], True

    with cf.ThreadPoolExecutor(threads) as ex:
        for _gid, ok in ex.map(one, todo):
            done += ok
            failed += not ok
            if (done + failed) % 200 == 0:
                log(f"[fetch] {season}: {done} fetched, {failed} failed, {time.time() - t0:.0f}s")
    log(f"[fetch] {season}: {done} fetched, {failed} failed in {time.time() - t0:.0f}s")
    return done, failed


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("raw")
    ap.add_argument("--seasons", default="2022,2023,2024,2025,2026")
    ap.add_argument("--threads", type=int, default=6)
    a = ap.parse_args(argv)
    for s in [int(x) for x in a.seasons.split(",")]:
        evs = schedule(s, a.raw)
        fetch_summaries(s, a.raw, evs, threads=a.threads)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
