"""Blind backtests of the DFS projection layer, to seed the look-back.

The look-back's correction is the ratio of actual to projected DraftKings
points per sport and position group. Waiting for five live events per sport
means weeks before the first correction; the completed events of a season
can say which way to lean now. Lineups cannot be rebuilt for past events
(DraftKings publishes no old salary pools), so this backtests PROJECTIONS,
every projectable player per event, against the real scoring dfslog already
grades with -- far more signal than six rostered players would give.

Blind means blind: an F1 round runs on form from the rounds BEFORE it (the
live model reads the whole season), with the real grid and none of the
this-weekend reads (practice, long runs, pit crews); a Cup race runs on the
season as of its date with the real starting grid; an NFL week runs on the
projections Sleeper published for that week (verified: past weeks are kept)
through the same correlated sim the live builder uses. MLB is not here: its
per-date projections come from game sims that skip finished games and read
season stats as of today, so an honest MLB backtest needs the sim to run as
of a past date -- a follow-up.

Output rows are what dfslog.seed_rows takes; write_seed() commits them to
seeds/dfs_backtest_seed.json, which the server ingests once on its next
recorder tick. Seeded events count half a live event and decay by recency
(dfslog), so what happens from today on takes over quickly.
"""

import json
import os
import time

import clock
import dfslog
import racing

_JOLPICA = "https://api.jolpi.ca/ergast/f1"
_NASCAR_BASE = "https://cf.nascar.com/cacher"


def _norm(s):
    return racing.norm_name(s or "")


def _retry(fn, tries=3, wait=4.0):
    """A feed read that times out once is not a missing event: try again,
    then give up on THIS event only (the first full run died on one Sleeper
    read timeout in week 4 with two sports' results still in memory)."""
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            last = e
            time.sleep(wait * (i + 1))
    raise last


def _match(actuals, name, prefix=""):
    nm = _norm(name)
    if prefix + nm in actuals:
        return actuals[prefix + nm]
    last = nm.split()[-1] if nm.split() else ""
    return actuals.get(f"last:{last}") if not prefix and last else None


# ---- F1 ---------------------------------------------------------------------
def f1_completed(season=None):
    """[(round, date, raceName, circuitName)] for rounds already run."""
    season = season or clock.today_et().year
    today = clock.today_et().isoformat()
    d = racing._get_json(f"{_JOLPICA}/{season}.json?limit=40")
    out = []
    for r in d.get("MRData", {}).get("RaceTable", {}).get("Races", []):
        if (r.get("date") or "") < today:
            out.append((int(r["round"]), r["date"], r.get("raceName", ""),
                        (r.get("Circuit") or {}).get("circuitName", "")))
    return out


def run_f1(season=None, rounds=None, n=1200, log=print):
    import racing_sim
    import race_weather
    season = season or clock.today_et().year
    done = f1_completed(season)
    if rounds:
        done = [x for x in done if x[0] in set(rounds)]
    rows = []
    for rnd, date, name, circuit in done:
        try:
            res = _retry(lambda: racing._get_json(f"{_JOLPICA}/{season}/{rnd}/results.json?limit=40"))
            results = res["MRData"]["RaceTable"]["Races"][0]["Results"]
        except Exception as e:
            log(f"f1 round {rnd}: no results ({e})")
            continue
        n_start = len(results)
        grid = {}
        for x in results:
            try:
                g = int(x.get("grid") or 0) or n_start
            except (TypeError, ValueError):
                g = n_start
            grid[_norm(f"{x['Driver'].get('givenName', '')} {x['Driver'].get('familyName', '')}")] = g
        try:
            clim = race_weather.climate(None, None, date) or {}
        except Exception:
            clim = {}
        race = {"round": rnd, "name": name, "type": racing_sim._f1_circuit_type(circuit or name),
                "wet_prob": clim.get("wet_prob", 0.0), "start": f"{date}T13:00:00Z"}
        try:
            sim = racing_sim.next_race_sim("f1", n=n, fixed_grid=grid, race=race, blind=True,
                                           as_of_round=rnd, seed=1_000_003)
        except Exception as e:
            log(f"f1 round {rnd}: sim failed ({e})")
            continue
        if not sim or not sim.get("drivers"):
            log(f"f1 round {rnd}: no sim")
            continue
        ek = f"f1:{date}:{rnd}"
        try:
            actuals = _retry(lambda: dfslog.actuals_f1(ek))
        except Exception as e:
            log(f"f1 round {rnd}: actuals failed ({e})")
            continue
        if not actuals:
            log(f"f1 round {rnd}: no actuals")
            continue
        players = []
        for nm, prof in sim["drivers"].items():
            if prof.get("dk_mean") is None:
                continue
            ac = _match(actuals, nm)
            if ac is None:
                continue
            players.append({"name": nm, "pos": "D", "proj": round(prof["dk_mean"], 1), "actual": round(ac, 2)})
        rows.append({"sport": "f1", "event_key": ek, "event_date": date, "players": players,
                     "note": f"blind: form as of round {rnd}, real grid, no weekend reads"})
        log(f"f1 round {rnd} {name}: {len(players)} drivers, proj {sum(p['proj'] for p in players):.0f} "
            f"actual {sum(p['actual'] for p in players):.0f}")
    return rows


# ---- NASCAR -----------------------------------------------------------------
def nascar_completed(year=None, series=1):
    year = year or clock.today_et().year
    today = clock.today_et().isoformat()
    rl = racing._get_json(f"{_NASCAR_BASE}/{year}/{series}/race_list_basic.json")
    out = [r for r in rl if r.get("race_type_id") == 1 and r.get("race_id")
           and (r.get("race_date") or "")[:10] < today]
    out.sort(key=lambda r: r.get("race_date") or "")
    return out


def run_nascar(year=None, n=1200, limit=None, log=print):
    import racing_sim
    import race_weather
    year = year or clock.today_et().year
    done = nascar_completed(year)
    if limit:
        done = done[-limit:]
    rows = []
    for r in done:
        date = (r.get("race_date") or "")[:10]
        try:
            feed = _retry(lambda: racing._get_json(f"{_NASCAR_BASE}/{year}/1/{r['race_id']}/weekend-feed.json"))
            results = feed["weekend_race"][0]["results"]
        except Exception as e:
            log(f"nascar {r.get('race_name')}: no results ({e})")
            continue
        grid = {}
        for x in results:
            try:
                sp = int(x.get("starting_position") or 0)
            except (TypeError, ValueError):
                sp = 0
            if sp > 0:
                grid[_norm(x.get("driver_fullname") or "")] = sp
        if len(grid) < 20:
            log(f"nascar {r.get('race_name')}: thin grid ({len(grid)})")
            continue
        try:
            clim = race_weather.nascar_climate(r.get("track_name", ""), date) or {}
        except Exception:
            clim = {}
        race = {"name": r.get("race_name", ""), "track": r.get("track_name"),
                "type": racing_sim._nascar_track_type(r.get("track_name", "")),
                "laps": r.get("scheduled_laps"), "wet_prob": clim.get("wet_prob", 0.10)}
        try:
            sim = racing_sim.next_race_sim("nascar", n=n, fixed_grid=grid, race=race, blind=True,
                                           as_of_date=date, seed=1_000_003)
        except Exception as e:
            log(f"nascar {r.get('race_name')}: sim failed ({e})")
            continue
        if not sim or not sim.get("drivers"):
            log(f"nascar {r.get('race_name')}: no sim")
            continue
        ek = f"nascar:{r['race_id']}"
        try:
            actuals = _retry(lambda: dfslog.actuals_nascar(ek))
        except Exception as e:
            log(f"nascar {r.get('race_name')}: actuals failed ({e})")
            continue
        if not actuals:
            log(f"nascar {r.get('race_name')}: no actuals")
            continue
        players = []
        for nm, prof in sim["drivers"].items():
            if prof.get("dk_mean") is None:
                continue
            ac = actuals.get(_norm(nm))
            if ac is None:
                continue
            players.append({"name": nm, "pos": "D", "proj": round(prof["dk_mean"], 1), "actual": round(ac, 2)})
        rows.append({"sport": "nascar", "event_key": ek, "event_date": date, "players": players,
                     "note": "blind: season as of race date, real grid, no practice; " + actuals.get("_partial", "")})
        log(f"nascar {r.get('race_name')}: {len(players)} drivers, proj {sum(p['proj'] for p in players):.0f} "
            f"actual {sum(p['actual'] for p in players):.0f}")
    return rows


# ---- NFL --------------------------------------------------------------------
def run_nfl(season, weeks=range(1, 19), n=800, log=print):
    import nfl_dfs_sim
    rows = []
    for wk in weeks:
        try:
            pool = _retry(lambda: nfl_dfs_sim.player_pool(wk, n=n, season=season))
        except Exception as e:
            log(f"nfl {season} w{wk}: pool failed ({e})")
            continue
        if not pool:
            log(f"nfl {season} w{wk}: no pool")
            continue
        ek = f"nfl:{season}:w{wk}"
        try:
            actuals = _retry(lambda: dfslog.actuals_nfl(ek))
        except Exception as e:
            log(f"nfl {season} w{wk}: actuals failed ({e})")
            continue
        if not actuals:
            log(f"nfl {season} w{wk}: no actuals")
            continue
        players = []
        for nm, p in pool.items():
            pos = (p.get("pos") or "").upper()
            if p.get("proj") is None:
                continue
            row = {"name": nm, "pos": "DST" if pos in ("DST", "DEF") else pos, "team": p.get("team")}
            ac = dfslog._lookup("nfl", actuals, row)
            if ac is None:
                continue
            players.append({"name": nm, "pos": row["pos"], "proj": round(float(p["proj"]), 1), "actual": round(ac, 2)})
        rows.append({"sport": "nfl", "event_key": ek, "event_date": None, "players": players,
                     "note": f"blind: Sleeper's week-{wk} projections through the live correlated sim"})
        log(f"nfl {season} w{wk}: {len(players)} players, proj {sum(p['proj'] for p in players):.0f} "
            f"actual {sum(p['actual'] for p in players):.0f}")
    return rows


def _nfl_week_dates(rows, season):
    """Stamp each NFL week with its Sunday so recency weighting can order it."""
    import datetime
    # week 1 Sunday: the first Sunday on or after September 5th
    d = datetime.date(int(season), 9, 5)
    while d.weekday() != 6:
        d += datetime.timedelta(days=1)
    for r in rows:
        if r["sport"] == "nfl" and not r.get("event_date"):
            wk = int(r["event_key"].split(":w")[-1])
            r["event_date"] = (d + datetime.timedelta(days=7 * (wk - 1))).isoformat()
    return rows


def write_seed(rows, path=None, source="backtest"):
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), dfslog.SEED_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump({"generated": int(time.time()), "source": source, "rows": rows}, fh, separators=(",", ":"))
    return path


def merge_seed(parts, path=None, source="backtest"):
    """Union of several part files by (sport, event), newest part wins, into
    the committed seed. Sports run in parallel processes and write parts, so
    a failure in one cannot lose another's events."""
    rows = {}
    for pth in parts:
        try:
            with open(pth) as fh:
                for r in json.load(fh).get("rows") or []:
                    rows[(r["sport"], r["event_key"])] = r
        except Exception as e:
            print(f"merge: skipped {pth} ({e})")
    out = sorted(rows.values(), key=lambda r: (r["sport"], r.get("event_date") or "", r["event_key"]))
    return write_seed(out, path, source), len(out)


# ---- the sample-noise measurement --------------------------------------------
_SAMPLES = (300, 600, 1200, 2500)
_SEEDS = (1, 2, 3, 4)
_ENTRIES = (1000, 20000, 150000)
NOISE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seeds/dfs_sample_noise.json")


def _probe_grid(samples=_SAMPLES, seeds=_SEEDS, entries=_ENTRIES):
    return [{"sample": sm, "seed": sd, "entries": en}
            for en in entries for sm in samples for sd in seeds]


def _fold(probe):
    """The probe rows folded to (entries, sample): mean and spread over seeds."""
    import statistics
    by = {}
    for r in probe or []:
        if r.get("error") or r.get("win_pct") is None:
            continue
        by.setdefault((r["entries"], r["sample"]), []).append(r)
    grid = []
    for (en, sm), rs in sorted(by.items()):
        def _ms(key):
            xs = [float(r.get(key) or 0.0) for r in rs]
            return (round(statistics.fmean(xs), 4),
                    round(statistics.pstdev(xs), 4) if len(xs) > 1 else 0.0)
        wm, ws = _ms("win_pct")
        cm, cs_ = _ms("cash_pct")
        rm, rsd = _ms("roi_pct")
        grid.append({"entries": en, "sample": sm, "n_seeds": len(rs),
                     "field": int(round(sum(r.get("field") or sm for r in rs) / len(rs))),
                     "win_mean": wm, "win_sd": ws, "cash_mean": cm, "cash_sd": cs_,
                     "roi_mean": rm, "roi_sd": rsd,
                     "distinct_fields": len({r.get("field_hash") for r in rs})})
    return grid


def run_sample(sport, sims=3000, samples=_SAMPLES, seeds=_SEEDS, entries=_ENTRIES,
               draft_group_id=None, entry_fee=20.0):
    """Measure the sample noise on the sport's live main DraftKings slate: one
    build, then the SAME top lineup against opponent fields of each size and
    seed at each entry count. Returns the row the seed file keeps."""
    import dk
    import simulate
    sl = dk.slate_for(sport, draft_group_id)
    if not sl or not sl.get("csv"):
        raise RuntimeError(f"{sport}: no DraftKings slate with a pool")
    probe = _probe_grid(samples, seeds, entries)
    today = clock.today_et().isoformat()
    t0 = time.time()
    if sport == "nfl":
        import nfl_dfs
        res = nfl_dfs.build(sl["csv"], week=1, objective="ceiling", stack=True, contest="gpp",
                            contest_size=entries[1] if len(entries) > 1 else entries[0],
                            entry_fee=entry_fee, mode="classic", contest_probe=probe)
    elif sport == "mlb":
        import mlb_dfs
        res = mlb_dfs.build(today, sl["csv"], cap=50000, objective="ceiling", n_sims=sims,
                            contest="gpp", field_size=600, entry_fee=entry_fee,
                            contest_size=entries[1] if len(entries) > 1 else entries[0],
                            contest_probe=probe)
    else:
        res = simulate.dfs_build(sl["csv"], roster=6, cap=50000, sport=sport, mode="classic",
                                 objective="ceiling", date=today, sims=sims, contest="gpp",
                                 contest_size=entries[1] if len(entries) > 1 else entries[0],
                                 entry_fee=entry_fee, contest_probe=probe)
    if not isinstance(res, dict) or res.get("error"):
        raise RuntimeError(f"{sport}: build failed: {(res or {}).get('error')}")
    grid = _fold(res.get("contest_probe"))
    if not grid:
        raise RuntimeError(f"{sport}: the probe returned nothing")
    return {"sport": sport, "ts": int(time.time()), "date": today, "sims": sims,
            "entry_fee": entry_fee, "seconds": round(time.time() - t0, 1),
            "event": {"draft_group_id": sl.get("draft_group_id"), "n_players": sl.get("n_players"),
                      "starts": ((sl.get("slate") or {}).get("starts") if isinstance(sl.get("slate"), dict) else None)},
            "grid": grid}


def write_noise(rows, path=None):
    """Merge per sport into the seed file (a sport's newest measurement wins)."""
    path = path or NOISE_PATH
    d = {"generated": None, "rows": []}
    if os.path.exists(path):
        try:
            with open(path) as fh:
                d = json.load(fh)
        except Exception:
            d = {"generated": None, "rows": []}
    keep = [r for r in d.get("rows") or [] if r.get("sport") not in {x["sport"] for x in rows}]
    d["rows"] = sorted(keep + list(rows), key=lambda r: r["sport"])
    d["generated"] = int(time.time())
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(d, fh, indent=1, sort_keys=True)
    return path


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    out_path = None
    if "--out" in args:
        i = args.index("--out")
        out_path = args[i + 1]
        args = args[:i] + args[i + 2:]
    if args and args[0] == "merge":
        p, n = merge_seed(args[1:])
        print("merged", p, "events", n)
        sys.exit(0)
    if args and args[0] == "sample":
        rows = []
        for sp in (args[1:] or ["f1", "nascar", "nfl", "mlb"]):
            try:
                row = run_sample(sp)
                rows.append(row)
                print(sp, "measured in", row["seconds"], "s:",
                      [(g["entries"], g["sample"], g["win_mean"], g["win_sd"], g["roi_sd"]) for g in row["grid"]])
            except Exception as e:
                print(f"{sp}: sample run failed ({e})")
        if rows:
            print("wrote", write_noise(rows, out_path))
        sys.exit(0)
    which = args or ["f1", "nascar", "nfl"]
    out = []
    for sp in which:
        try:
            if sp == "f1":
                out += run_f1()
            elif sp == "nascar":
                out += run_nascar()
            elif sp == "nfl":
                out += _nfl_week_dates(run_nfl(clock.today_et().year - 1), clock.today_et().year - 1)
        except Exception as e:
            print(f"{sp}: run failed ({e})")
    print("wrote", write_seed(out, out_path), "events", len(out))
