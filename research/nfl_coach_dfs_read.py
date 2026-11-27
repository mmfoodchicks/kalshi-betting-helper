"""The registered DFS read of the coaching engine: do DFS worlds whose games
are played by nfl_coach_sim score real DraftKings production better than
production's worlds? Independent of R1's win-probability criterion: a more
realistic end of half can move who scores when without moving who wins.
Registered in research/data/nfl_coach/prereg_dfs.json; NOT run yet.

  python3 -m research.nfl_coach_dfs_read <deep store dir> --table <player_weeks csv.gz with 2026 weeks 13-18>
  python3 -m research.nfl_coach_dfs_read <deep store dir> --dry          (2026 weeks 2-5: SEEN rows, mechanics only)

ARMS, on the same games, seeds, anchor targets, Sleeper usage deal and
allocation noise (research.nfl_dfs_engine_eval._week, run twice):
  engine   nfl_dfs_engine as shipped (ENV_SD, ATT_WEIGHT), games by nfl_deep_sim
  coach    the same, games by nfl_coach_sim (research.nfl_coach_dfs.coach_games)

DATA. Player-week DraftKings points for 2026 weeks 13-18, built by
research.hist_data.build from the Sleeper dumps and handed in with --table.
Those rows exist in no committed file and were read by no development run
(the committed 2026 table stops at week 5). Only weeks 13-18 of the table are
taken. R1's read uses the same GAMES for a different statistic (clock counts
and the winner); the candidate is frozen and pinned in both registrations, so
neither read can inform the other.

THE RULE (prereg_dfs.json; `verdict`):
  primary     offence CRPS, coach minus engine, paired by player-week, SE
              clustered by game: ESTABLISHED when t < -t(0.975, games - 1).
  guardrails  pair-correlation RMSE <= engine + 0.01; PIT KS <= engine + 0.01;
              defenses and kickers each not worse by more than 1 SE.
  PASS = primary and every guardrail. "NOT ESTABLISHED" = guardrails hold, the
  primary does not. "WORSE" = a guardrail fails. One look; no extension.
The role-pair table (QB-WR, stacks, cross-team), per-role CRPS and the world
summary are printed as secondary, non-gating figures. Late-game USAGE cannot
be scored: the deal sees a game's totals, not its plays (the audit's 2.6).

`--dry` runs the whole path on weeks 2-5 and writes nothing to the read's
output: it exists so the mechanics are proven on the PC before the date.
"""
import datetime
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from research import nfl_coach_reg as RG          # noqa: E402

PREREG = os.path.join(RG.REG_DIR, "prereg_dfs.json")
OUT = os.path.join(RG.REG_DIR, "read_dfs.json")
_EXTRA = {"rows": None}


def prereg():
    with open(PREREG) as fh:
        return json.load(fh)


def refusal(now_utc=None, dirty=None, reg=None):
    reg = reg or prereg()
    now = now_utc or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return RG.common_refusal(reg, now, reg["not_before_utc"], dirty, OUT)


def verdict(paired, sc, arm, games):
    """The registered rule on research.nfl_dfs_engine_eval's paired_crps
    (coach minus engine) and score outputs."""
    off = paired["offence"]
    crit = RG.t_ppf(0.975, max(1, games - 1))
    t = off["mean_diff"] / off["se_game_clustered"] if off["se_game_clustered"] else 0.0
    primary = t < -crit
    cal = sc["calibration"]
    g = {"pair_rmse": sc["pair_rmse"]["coach"] <= sc["pair_rmse"][arm] + 0.01,
         "pit_ks": cal["coach"]["_pooled"]["pit_ks"] <= cal[arm]["_pooled"]["pit_ks"] + 0.01}
    for kind in ("dst", "k"):
        p = paired.get(kind)
        g[kind] = p is None or p["mean_diff"] <= p["se_game_clustered"]
    guard = all(g.values())
    return {"outcome": "PASS" if primary and guard else "NOT ESTABLISHED" if guard else "WORSE",
            "primary": primary, "t": round(t, 3), "t_critical": round(crit, 3), "guardrails": g,
            "gain_upper_95": round(-off["mean_diff"] + RG.t_ppf(0.95, max(1, games - 1)) * off["se_game_clustered"], 5)}


def _rows_patch(table, weeks):
    """research.nfl_dfs_engine_eval._table_rows plus the sealed weeks of `table`."""
    from research import hist_data as H
    from research import nfl_dfs_engine_eval as EV
    if table:
        _EXTRA["rows"] = [r for r in H.read_csv_gz(table) if r["season"] == 2026 and r["week"] in weeks]
    if getattr(EV._table_rows, "coach_patch", False):
        return                                    # a forked worker inherits the parent's patch
    orig = EV._table_rows

    def rows():
        return orig() + (_EXTRA["rows"] or [])
    rows.coach_patch = True
    EV._table_rows = rows


def _init(table, weeks, *args):
    from research import nfl_dfs_engine_eval as EV
    _rows_patch(table, set(weeks))
    EV._init(*args)


def _both(w):
    """One week, both arms: the harness's own week runner, then again with the
    games played by the coaching engine; the second run's engine arm is filed
    as `coach` beside the first's."""
    from research import nfl_coach_dfs as CDFS
    from research import nfl_dfs_engine_eval as EV
    _w, rows = EV._week(w)
    with CDFS.coach_games():
        _w2, rows2 = EV._week(w)
    arm = EV.arm_key(*EV._W["arms"][0])
    by = {r["key"]: r for r in rows2}
    out = []
    for r in rows:
        r2 = by.get(r["key"])
        if r2 is None or arm not in r["models"] or arm not in r2["models"]:
            continue
        r["models"]["coach"] = r2["models"][arm]
        out.append(r)
    return w, out


def run(store_dir, table, weeks, workers=3, log=print):
    import multiprocessing as mp
    import nfl_dfs_engine as G
    from research import nfl_dfs_engine_eval as EV
    arm = (G.ENV_SD, G.ATT_WEIGHT)
    _rows_patch(table, set(weeks))
    res = []
    with mp.Pool(workers, initializer=_init,
                 initargs=(table, list(weeks), store_dir, 2026, [2023, 2024, 2025], EV.K_DEFAULT, [arm])) as pool:
        for w, rows in pool.imap_unordered(_both, list(weeks)):
            res.extend(rows)
            log(f"[coach dfs] week {w}: {len(rows)} games")
    res.sort(key=lambda r: (r["week"], r["key"][2]))
    key = EV.arm_key(*arm)
    sc = EV.score(res, ["legacy", "constrained", key, "coach"])
    paired = EV.paired_crps(res, "coach", key)
    return res, key, sc, paired


def main(argv):
    if not argv or argv[0].startswith("-"):
        print(__doc__)
        return 2
    dry = "--dry" in argv
    reg = prereg()
    if dry:
        weeks, table = [2, 3, 4, 5], None
    else:
        why = refusal(reg=reg)
        if why:
            print(f"REFUSED: {why}")
            return 1
        if "--table" not in argv:
            print("REFUSED: --table <player_weeks csv.gz holding 2026 weeks 13-18> is required")
            return 1
        weeks, table = list(reg["window"]["weeks"]), argv[argv.index("--table") + 1]
    res, key, sc, paired = run(argv[0], table, weeks)
    games = len(res)
    if not dry and games < reg["window"]["min_games"]:
        print(f"REFUSED: {games} games scored, the registration needs {reg['window']['min_games']} (nothing was written)")
        return 1
    v = verdict(paired, sc, key, games)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    out = {"registration": reg["block_sha256"], "commit": head, "dry_run": dry, "weeks": weeks, "games": games,
           "read_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "verdict": v, "paired_crps_coach_minus_engine": paired,
           "pair_rmse": sc["pair_rmse"], "opp_pair_rmse": sc.get("opp_pair_rmse"), "pairs": sc.get("pairs"),
           "pit_ks": {m: c["_pooled"]["pit_ks"] for m, c in sc["calibration"].items()},
           "crps_mean_over_roles": {m: c["_pooled"]["crps_mean_over_roles"] for m, c in sc["calibration"].items()},
           "sides": sc["sides"], "means": sc.get("means")}
    from research import nfl_dfs_engine_eval as EV
    out["worlds"] = {m: EV.world_summary(res, m) for m in (key, "coach")}
    if not dry:
        with open(OUT, "w") as fh:
            json.dump(out, fh, indent=1, sort_keys=True, default=float)
    print(json.dumps(out, indent=1, sort_keys=True, default=float))
    print("DFS:", ("DRY RUN (seen weeks; not a result) " if dry else "") + v["outcome"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
