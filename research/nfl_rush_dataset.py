"""The rushing matchup layer measured and laid out: how reliable each rushing
signal is (split-half), how much of it carries into the next season, whether
the counts reconcile with the box score, and one row per RB / QB player-game
2023-2026 with every as-of rushing profile beside the actuals -- for the
modelling agent to fit on 2023-2024, choose on 2025, check (disclosed,
deciding nothing) on 2026 weeks 1-6, and test prospectively on 2026 weeks
8-11 (research/reports/nfl_rush_data.md).

RESEARCH. Numpy only (no pandas). Nothing here is read by the app.

    export DEEP_CACHE_DIR=<dir holding nfl_matchup_<season>.pkl and nfl_rush_aux_<season>.pkl>  VIGIL_NO_BG=1
    python3 -m research.nfl_rush_dataset measure          # reliability, persistence, sneak-rule agreement
    python3 -m research.nfl_rush_dataset checks --raw DIR # reconciliation, coverage, join rates
    python3 -m research.nfl_rush_dataset build --sleeper DIR   # the RB / QB player-game datasets
        [--out DIR] [--all-weeks | --max-week N]   # elsewhere; 2026 past week 7 (the default cap)

Outputs (research/data/nfl_rush/): reliability.json (nfl_rush_profiles reads
its "k"), persistence.json, sneak_rule.json, checks.json,
rb_games_2023_2026.csv.gz, qb_games_2023_2026.csv.gz, schema.json, meta.json.

RULES (written 2026-10-23 before any measurement or build was run; the file's
sha256 at the first run is in the report):
- k. Every shrunk quantity has its own metric name (the `metric` the profile
  stamps on the field). Its k is the Spearman-Brown k of its split-half
  reliability: odd weeks vs even weeks of one regular season, pooled over
  2018-2025, r the Pearson correlation of the two halves across units, n the
  mean denominator of a half, k = n (1 - r) / r. The quantity correlated is
  the field's RAW value, or, for a split (a field carrying `base`: direction,
  side, box, personnel, formation, scramble conditions, box by personnel /
  formation, gap), its departure from `base` -- the prior built from the
  unit's own raw overall figure and the league's split offset -- because that
  departure is what the shrinkage shrinks. r <= 0.02 (or fewer than 6 units)
  -> k None: the profile keeps the prior. A second table from 2018-2024 only
  (`k_fit_only`) serves a validation that must not have seen 2025.
- Units and gates. Teams: every team-season. Running backs: player-scope
  (all his teams), rushing metrics only when he has 25+ designed runs in each
  half, receiving metrics only with 15+ targets in each half, usage metrics
  with either. Quarterbacks: 100+ dropbacks in each half. A field enters only
  when both halves have n >= 8 (per-game fields n >= 4 games; observed-over-
  expected ratios n >= 0.5 expected events).
- Persistence. Pearson r across units of consecutive full regular seasons
  (2018->2019 ... 2024->2025): teams; RBs with 100+ designed runs in both;
  QBs with 200+ dropbacks in both; the same raw / departure value as above.
- Decay. The agreed rule (research/nfl_matchup_dataset.py) applied to the
  rushing TENDENCIES: the pooled persistence r of def_light_share,
  def_heavy_share, def_nickel_share, def_dime_share, def_dl3_share,
  rb_dir_share, rb_sg_share, qb_dsg_rate, qb_scr_rate, tm_neutral_run. Under
  0.3 -> a season back weighs 0.25; 0.3-0.6 -> 0.5; 0.6 or more -> 0.75.
- RB units (fixed 2026-10-23 after the reviews' B1, before the re-run): a
  "running back" unit is a player whose agreed-crosswalk position is RB or FB.
  The first run's units were every non-QB with a designed run -- 39% of the
  usage units and 54% of the receiving units were WRs / TEs. A WR's or TE's
  designed run (Taysom Hill is listed TE) still counts in the team's and the
  defense's run totals; it never enters a back's tally or an RB unit.
- Disclosure splits (decide nothing; the k stays odd vs even): the same
  measurement on weeks 1-9 vs 10-18 ("first_second") and on a fixed week set
  {1, 4, 5, 8, 10, 11, 14, 15, 17} vs the rest ("fixed_mix"), reported beside
  each k in `alt_splits` -- odd / even is optimistic for role quantities used
  forward in time (the Sonnet review's m7), and the OE k is poorly pinned (m10).
- Two passes. The defense's observed-over-expected figures shrink each facing
  QB's own rate by that rate's measured k, so `measure` runs twice: the first
  pass writes the table, the second reads it for those QB rates and replaces
  only the OE metrics (`def_*_oe`); every other k must come out identical in
  both passes (asserted: no other measured value depends on a k).
- Sneak rule. nfl_rush_profiles.sneak_rule is scored against FTN's
  is_qb_sneak on every 2022-2025 QB designed run FTN charted (precision,
  recall). It is used only where FTN has no row; nothing is tuned on the
  result.
- The dataset. One row per (game, team, player) for every RB / FB who had a
  designed run, target, aborted snap or snap (NGS on-field or PFR) and every
  Sleeper-projected back (rush or target projection > 0) of the team that
  week; one row per QB with a dropback or rush, and Sleeper's projected QB1.
  Every week's rows come from a snapshot FROZEN before any of that week's
  games enter it (the agreed week_rows discipline); the builder raises if
  the snapshot holds the week.
"""
import argparse
import collections
import csv
import gzip
import hashlib
import io
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.setdefault("VIGIL_NO_BG", "1")

import nfl_matchup_data as D            # noqa: E402
import nfl_matchup_profiles as P        # noqa: E402
import nfl_rush_data as R               # noqa: E402
import nfl_rush_profiles as RP          # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "nfl_rush")
PLAYER_WEEKS = os.path.join(HERE, "data", "player_weeks_2022_2025.csv.gz")
MEASURE_SEASONS = tuple(range(2018, 2026))
DATASET_SEASONS = (2023, 2024, 2025, 2026)
# the registered dataset's 2026 cap (its build stays reproducible: the default never moves). The rushing
# model's PROSPECTIVE dataset needs the window's weeks past it: `build --all-weeks` (every week the store
# holds) or `--max-week N` lifts it (Fable's B1: with no flag the decisive test's rows could never be built)
TEST_MAX_WEEK = {2026: 7}
MIN_CAR_HALF, MIN_TGT_HALF, MIN_DB_HALF = 25, 15, 100
MIN_CAR_SEASON, MIN_DB_SEASON = 100, 200
TENDENCY = ("def_light_share", "def_heavy_share", "def_nickel_share", "def_dime_share", "def_dl3_share", "rb_dir_share",
            "rb_sg_share", "qb_dsg_rate", "qb_scr_rate", "tm_neutral_run")
LICENSE = {"credit": "FTN Data via nflverse (CC BY-SA 4.0); nflverse-data (CC BY 4.0); Pro Football Reference advanced stats "
                     "and snap counts via nflverse (credit Pro Football Reference / Sports Reference); participation through 2022: "
                     "NFL NextGenStats via nflverse (for 2022 and earlier); NFL Next Gen Stats via "
                     "nflverse (NFL-owned data, governed by the NFL's terms); NFL injury reports via nflverse; DynastyProcess db_playerids",
           "share_alike": "tables derived from FTN charting are shared under CC BY-SA 4.0",
           "terms": "research/reports/rush_data_sources.md §12 and research/reports/matchup_data_sources.md"}
RUSH_RECEIVING = ("rb_catch", "rb_ypt", "rb_yac_rec", "rb_adot", "rb_screen_share", "rb_chk_share")


def log(msg):
    print(msg, flush=True)


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---- stores ------------------------------------------------------------------------------
def load_all(seasons=range(2018, 2027)):
    stores, auxes = {}, {}
    for s in seasons:
        st = D.load_season(s)
        if st:
            stores[s] = st
            ax = R.load_aux(s)
            if ax is not None and not R.aux_matches(ax, st):
                log(f"WARNING: nfl_rush_aux_{s} was built against another nfl_matchup_{s}: its row extras are ignored")
            auxes[s] = ax
    if not stores:
        raise SystemExit("no nfl_matchup_<season>.pkl in DEEP_CACHE_DIR")
    ids = D.load_ids()
    if not ids:
        raise SystemExit("no nfl_matchup_ids.pkl in DEEP_CACHE_DIR")
    return stores, auxes, P.positions_from_ids(ids), ids["ids"]


def pearson(a, b):
    import numpy as np
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 6 or a.std() == 0 or b.std() == 0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def _val(f):
    """The quantity a reliability / persistence measurement correlates: the raw
    value, or a split's departure from its raw-built prior."""
    raw = f.get("raw")
    if raw is None:
        return None
    if "base" in f:
        return None if f.get("base") is None else raw - f["base"]
    return raw


def _min_n(f):
    u = f.get("unit")
    if u in ("per_game", "yds_per_game"):
        return 4
    if u == "ratio":
        return 0.5
    return 8


# ---- unit profiles -----------------------------------------------------------------------
def unit_profiles(S, F=None, pos=None):
    """{(kind, unit): {path: field}} for every RB, QB and team in a snapshot,
    with the unit's volume for the gates. F: the snapshot supplying the QB
    rates for the defense's observed-over-expected (the full season when S is
    a half). An "rb" unit is a player whose crosswalk position (`pos`) is RB or
    FB -- never a WR / TE who carried (the reviews' B1); the tallies already
    keep those carries out of T.rb, and this filter holds it at the units too."""
    out = {}
    T = S.tallies[0][1]
    for team in sorted(set(T.dfn) | set(T.off)):
        prof = {"run": S.defense_run(team), "qb": S.defense_qb(team, rates_from=F)}
        out[("def", team)] = (RP.flatten(prof), {})
        prof = {"ol": S.offense_line(team), "ctx": S.team_context(team)}
        out[("off", team)] = (RP.flatten(prof), {})
    for pid, d in T.rb.items():
        if pos is None or pos.get(pid) not in ("RB", "FB"):
            continue
        car = P._agg(d, lambda k: True, RP.RV_W)[0]
        tgt = sum((r["players"].get(pid) or [0.0] * RP.PL_W)[RP.PL["tgt"]] for r in T.player_games.get(pid, []))
        out[("rb", pid)] = (RP.flatten(S.rb(pid)), {"car": car, "tgt": tgt})
    qbt = S._qb_totals()
    for pid, q in qbt.items():
        if q[RP.QL["db"]] >= 50 and (pos is None or pos.get(pid) == "QB"):
            out[("qb", pid)] = (RP.flatten(S.qb(pid)), {"db": q[RP.QL["db"]]})
    return out


def _gate(kind, metric, vol_a, vol_b, half=True):
    if kind == "rb":
        car_min, tgt_min = (MIN_CAR_HALF, MIN_TGT_HALF) if half else (MIN_CAR_SEASON, 2 * MIN_TGT_HALF)
        car_ok = vol_a.get("car", 0) >= car_min and vol_b.get("car", 0) >= car_min
        tgt_ok = vol_a.get("tgt", 0) >= tgt_min and vol_b.get("tgt", 0) >= tgt_min
        if metric.startswith(RUSH_RECEIVING):
            return tgt_ok
        if metric.startswith(("rb_carries", "rb_targets", "rb_carry", "rb_rb_carry", "rb_target_share", "rb_rz", "rb_gl",
                              "rb_early", "rb_third", "rb_two", "rb_sit", "rb_snap", "rb_tgt_per_db")):
            return car_ok or tgt_ok
        return car_ok
    if kind == "qb":
        m = MIN_DB_HALF if half else MIN_DB_SEASON
        return vol_a.get("db", 0) >= m and vol_b.get("db", 0) >= m
    return True


def _pairs(A, B, half=True):
    """{metric: [(a, nA, b, nB, unit, path)]} over units present in both."""
    acc = collections.defaultdict(list)
    for key in sorted(set(A) & set(B), key=str):
        fa, va = A[key]
        fb, vb = B[key]
        for path, f in fa.items():
            m = f.get("metric")
            g = fb.get(path)
            if not m or g is None or not _gate(key[0], m, va, vb, half):
                continue
            a, b = _val(f), _val(g)
            na, nb = f.get("n") or 0, g.get("n") or 0
            if a is None or b is None or na < _min_n(f) or nb < _min_n(g):
                continue
            acc[m].append((a, na, b, nb, str(key[1]), path))
    return acc


def _rel(rows):
    import numpy as np
    if len(rows) < 6:
        return {"r": None, "n_units": len(rows), "k": None}
    r = pearson([x[0] for x in rows], [x[2] for x in rows])
    nbar = float(np.mean([(x[1] + x[3]) / 2.0 for x in rows]))
    k = nbar * (1 - r) / r if (r is not None and r > 0.02) else None
    return {"r": None if r is None else round(r, 3), "n_units": len(rows), "mean_n_half": round(nbar, 2),
            "k": None if k is None else round(k, 2),
            "reliability_full_season": None if k is None else round(2 * nbar / (2 * nbar + k), 3)}


# ---- measurement: split-half reliability ------------------------------------------------------
FIXED_MIX = frozenset((1, 4, 5, 8, 10, 11, 14, 15, 17))
SPLITS = {"odd_even": lambda w: w % 2 == 1,          # half A = the odd weeks: the k rule
          "first_second": lambda w: w <= 9,         # disclosure only
          "fixed_mix": lambda w: w in FIXED_MIX}    # disclosure only


def half_tallies(st, aux, pos, split="odd_even"):
    """(A, B) tallies of one season split by SPLITS[split]: A the weeks the
    split's test keeps, B every other week -- disjoint, together the season."""
    a_of = SPLITS[split]
    A = RP.season_tallies(st, aux, pos, week_filter=a_of)
    B = RP.season_tallies(st, aux, pos, week_filter=lambda w: not a_of(w))
    return A, B


def measure_reliability(stores, auxes, pos, splits=("odd_even", "first_second", "fixed_mix")):
    acc = {sp: collections.defaultdict(list) for sp in splits}
    acc_fit = collections.defaultdict(list)
    by_season = {}
    for s in MEASURE_SEASONS:
        st = stores.get(s)
        if not st:
            continue
        t0 = time.time()
        aux = auxes.get(s)
        F = RP.RushSnapshot(s, 99, RP.season_tallies(st, aux, pos), [])
        for sp in splits:
            Ta, Tb = half_tallies(st, aux, pos, sp)
            pairs = _pairs(unit_profiles(RP.RushSnapshot(s, 99, Ta, []), F, pos),
                           unit_profiles(RP.RushSnapshot(s, 99, Tb, []), F, pos))
            for m, rows in pairs.items():
                acc[sp][m].extend(rows)
                if sp == "odd_even" and s <= 2024:
                    acc_fit[m].extend(rows)
            if sp == "odd_even":
                by_season[s] = {m: _rel(rows) for m, rows in pairs.items()}
        log(f"[measure] {s}: {len(by_season[s])} metrics, {time.time() - t0:.0f}s")
    pooled = {m: _rel(rows) for m, rows in sorted(acc["odd_even"].items())}
    fit = {m: _rel(rows) for m, rows in sorted(acc_fit.items())}
    alt = {sp: {m: _rel(rows) for m, rows in sorted(acc[sp].items())} for sp in splits if sp != "odd_even"}
    return {"method": "odd vs even weeks of one regular season, Pearson r across units of the raw value (a split: its "
                      "departure from its raw-built prior); k = n(1-r)/r with n the mean denominator of a half; r <= 0.02 -> k None",
            "units": "rb = crosswalk position RB/FB only; qb = position QB with 50+ dropbacks a half; every team",
            "seasons": [s for s in MEASURE_SEASONS if s in stores], "pooled": pooled, "by_season": by_season,
            "k": {m: b["k"] for m, b in pooled.items()},
            "k_fit_only": {"seasons": "2018-2024", "k": {m: b["k"] for m, b in fit.items()}, "pooled": fit},
            "alt_splits": {"note": "disclosure only; the k is the odd / even split's", "first_second": "weeks 1-9 vs 10-18",
                           "fixed_mix": "weeks " + ",".join(str(w) for w in sorted(FIXED_MIX)) + " vs the rest",
                           "pooled": alt},
            "unsupported": sorted(m for m, b in pooled.items() if b["k"] is None),
            "source": "research/nfl_rush_dataset.py measure (split-half, 2018-2025)"}


# ---- measurement: year-over-year persistence ------------------------------------------------------
def measure_persistence(stores, auxes, pos):
    import numpy as np
    full = {}
    for s in MEASURE_SEASONS:
        if s in stores:
            T = RP.season_tallies(stores[s], auxes.get(s), pos)
            S = RP.RushSnapshot(s, 99, T, [])
            full[s] = unit_profiles(S, S, pos)
    out = {"pairs": {}, "note": "Pearson r across units of consecutive full regular seasons (teams; RBs 100+ designed runs "
                                 "both seasons; QBs 200+ dropbacks both)"}
    pooled = collections.defaultdict(list)
    for s in sorted(full):
        if s + 1 not in full:
            continue
        pr = _pairs(full[s], full[s + 1], half=False)
        blk = {}
        for m, rows in pr.items():
            r = pearson([x[0] for x in rows], [x[2] for x in rows])
            blk[m] = {"r": None if r is None else round(r, 3), "n_units": len(rows)}
            if r is not None:
                pooled[m].append(r)
        out["pairs"][f"{s}->{s + 1}"] = blk
    out["pooled_r"] = {m: round(float(np.mean(v)), 3) for m, v in sorted(pooled.items())}
    tend = [out["pooled_r"][m] for m in TENDENCY if m in out["pooled_r"]]
    out["tendency_metrics"] = list(TENDENCY)
    out["tendency_pooled_r"] = round(float(np.mean(tend)), 3) if tend else None
    r = out["tendency_pooled_r"] or 0.0
    out["decay_rule"] = "0.25 if r < 0.3, 0.5 if 0.3 <= r < 0.6, 0.75 if r >= 0.6 (the agreed rule, written before running)"
    out["decay_chosen"] = decay_rule(r)
    return out


def decay_rule(r):
    """The agreed rule: a season back weighs 0.25 under a pooled tendency r of
    0.3, 0.5 from 0.3 to under 0.6, 0.75 from 0.6."""
    return 0.25 if r < 0.3 else 0.5 if r < 0.6 else 0.75


# ---- measurement: the sneak rule against FTN ---------------------------------------------------------
def measure_sneak_rule(stores, auxes, pos):
    out = {}
    for s in (2022, 2023, 2024, 2025):
        st, ax = stores.get(s), auxes.get(s)
        if not st:
            continue
        C = st["cols"]
        abt = (ax or {}).get("extras", {}).get("aborted") if ax and R.aux_matches(ax, st) else None
        tp = fp = fn = tn = 0
        for i in range(st["n"]):
            if C["kind"][i] != D.RUN or not C["rusher"][i] or pos.get(C["rusher"][i]) != "QB":
                continue
            if abt is not None and abt[i] == 1:
                continue
            if not C["ftn"][i] or C["sneak"][i] not in (0, 1):
                continue
            rule = RP.sneak_rule(1 if C["sg"][i] else 0, C["ytg"][i], C["rloc"][i], C["rgap"][i])
            ftn = C["sneak"][i] == 1
            tp += rule and ftn
            fp += rule and not ftn
            fn += ftn and not rule
            tn += (not rule) and (not ftn)
        out[s] = {"qb_designed_runs_charted": tp + fp + fn + tn, "ftn_sneaks": tp + fn, "rule_sneaks": tp + fp, "both": tp,
                  "precision": round(tp / (tp + fp), 3) if tp + fp else None, "recall": round(tp / (tp + fn), 3) if tp + fn else None}
    return out


# ---- checks: reconciliation, coverage, joins ------------------------------------------------------------
def _sleeper_box(seasons):
    out = {}
    with gzip.open(PLAYER_WEEKS, "rt", newline="") as fh:
        for row in csv.DictReader(fh):
            s = int(row["season"])
            if s not in seasons or row["has_act"] != "1":
                continue
            f = lambda k: float(row[k]) if row.get(k) not in (None, "") else 0.0
            out[(s, int(row["week"]), row["player_id"])] = {"pos": row["pos"], "gp": f("act_gp"), "rush_att": f("act_rush_att"),
                                                            "rush_yd": f("act_rush_yd"), "rush_td": f("act_rush_td")}
    return out


def check_sleeper(stores, auxes, pos, ids, seasons=(2023, 2024, 2025)):
    """pbp official rushing (designed + sneak + scramble + kneel + aborted; nflverse
    rushing_yards) per player-game against Sleeper's box, with and without kneels
    and aborted snaps, QBs and RBs apart. 2026 is not compared (test season)."""
    box = _sleeper_box(set(seasons))
    gs = {pid: r.get("sleeper") for pid, r in ids.items() if r.get("sleeper")}
    sl_to_g = {v: k for k, v in gs.items()}
    pbp = {}
    for s in seasons:
        st = stores[s]
        T = RP.season_tallies(st, auxes.get(s), pos)
        for pid, recs in T.player_games.items():
            for r in recs:
                pl = r["players"].get(pid)
                if pl is None:
                    continue
                key = (s, r["week"], pid)
                cur = pbp.setdefault(key, [0.0] * RP.PL_W)
                for i, v in enumerate(pl):
                    cur[i] += v
    res = {}
    for pos_ in ("QB", "RB"):
        stats = collections.Counter()
        diffs = collections.defaultdict(float)
        for (s, w, spid), b in box.items():
            if b["pos"] != pos_ or b["gp"] < 1:
                continue
            g = sl_to_g.get(spid)
            if not g:
                stats["no_gsis"] += 1
                continue
            pl = pbp.get((s, w, g)) or [0.0] * RP.PL_W
            att, yds = pl[RP.PL["rush"]], pl[RP.PL["rush_yds"]]
            kn, kny = pl[RP.PL["kneel"]], pl[RP.PL["kneel_yds"]]
            ab, aby = pl[RP.PL["abort"]], pl[RP.PL["abort_yds"]]
            stats["n"] += 1
            stats["att_exact"] += att == b["rush_att"]
            stats["yds_exact"] += abs(yds - b["rush_yd"]) < 0.5
            stats["both_exact"] += (att == b["rush_att"] and abs(yds - b["rush_yd"]) < 0.5)
            stats["att_exact_no_kneel"] += (att - kn) == b["rush_att"]
            stats["yds_exact_no_kneel"] += abs(yds - kny - b["rush_yd"]) < 0.5
            stats["att_exact_no_aborted"] += (att - ab) == b["rush_att"]
            stats["yds_exact_no_aborted"] += abs(yds - aby - b["rush_yd"]) < 0.5
            stats["att_exact_no_scramble"] += (att - pl[RP.PL["scr"]]) == b["rush_att"]
            stats["kneel_games"] += kn > 0
            stats["kneel_games_exact"] += (kn > 0 and att == b["rush_att"])
            stats["kneel_games_exact_without"] += (kn > 0 and (att - kn) == b["rush_att"])
            diffs["att"] += att - b["rush_att"]
            diffs["yds"] += yds - b["rush_yd"]
        n = stats["n"] or 1
        res[pos_] = {"player_games": stats["n"], "unmapped": stats["no_gsis"],
                     **{k: round(v / n, 4) for k, v in stats.items() if k not in ("n", "no_gsis", "kneel_games", "kneel_games_exact",
                                                                                  "kneel_games_exact_without")},
                     "kneel_games": stats["kneel_games"], "kneel_games_exact_with_kneels": stats["kneel_games_exact"],
                     "kneel_games_exact_without_kneels": stats["kneel_games_exact_without"],
                     "mean_att_diff_pbp_minus_sleeper": round(diffs["att"] / n, 4),
                     "mean_yds_diff_pbp_minus_sleeper": round(diffs["yds"] / n, 4)}
    res["definition"] = ("pbp official rushes = designed (incl. sneaks) + scrambles + kneels + aborted snaps with the player as "
                         "rusher; yards = nflverse rushing_yards where the row aligned (else yards_gained); sacks never")
    return res


def check_partition(stores, auxes, pos, seasons=(2023, 2024, 2025)):
    """Every QB rush in the store, counted independently, against the QB lines'
    designed + sneak + scramble + kneel + aborted; and no sack anywhere in a
    rushing figure."""
    out = {}
    for s in seasons:
        st, ax = stores[s], auxes.get(s)
        C = st["cols"]
        indep = collections.Counter()
        indep_y = collections.Counter()
        sacks_with_rusher = 0
        for i in range(st["n"]):
            k = C["kind"][i]
            rp = C["rusher"][i]
            if k == D.SACK and rp:
                sacks_with_rusher += 1
            if k in (D.RUN, D.SCRAMBLE, D.KNEEL) and rp and pos.get(rp) == "QB":
                indep[rp] += 1
                indep_y[rp] += C["yds"][i]
        T = RP.season_tallies(st, ax, pos)
        q = collections.Counter()
        qy = collections.Counter()
        for lst in T.team_games.values():
            for side, r in lst:
                if side != "off":
                    continue
                for pid, ql in r["qbs"].items():
                    q[pid] += sum(ql[RP.QL[x]] for x in ("dsg", "sneak", "scr", "kneel", "abort"))
                    qy[pid] += sum(ql[RP.QL[x]] for x in ("dsg_yds", "sneak_yds", "scr_yds", "kneel_yds", "abort_yds"))
        # a QB who never dropped back but rushed (a wildcat QB snap is impossible; a QB credited a rush on a
        # handoff-only game) still has a QB line: every rush creates one, so the two counts must agree
        mism = [pid for pid in set(indep) | set(q) if indep[pid] != q[pid]]
        mism_y = [pid for pid in set(indep_y) | set(qy) if abs(indep_y[pid] - qy[pid]) > 0.01]
        out[s] = {"qbs": len(set(indep) | set(q)), "rushes": sum(indep.values()), "partitioned": sum(q.values()),
                  "qbs_count_mismatch": len(mism), "qbs_yards_mismatch": len(mism_y), "sacks_with_a_rusher_id": sacks_with_rusher,
                  "classes": {x: sum(ql[RP.QL[x]] for lst in T.team_games.values() for side, r in lst if side == "off"
                                     for ql in r["qbs"].values()) for x in ("dsg", "sneak", "scr", "kneel", "abort", "rpo")},
                  "sneak_source": {"ftn": T.counts["sneak_ftn"], "rule": T.counts["sneak_rule"]},
                  "rpo_dropbacks_not_rushes": T.counts["rpo_pass"]}
    return out


def check_pfr_season(stores, auxes, pos, raw_dir, seasons=(2019, 2020, 2021, 2022, 2023, 2024, 2025)):
    """Scrambles per QB-season against PFR's season passing table (its own
    charting: `scrambles`, populated 2024+), and RPO runs per TEAM-season:
    PFR's rpo_rush_att (every rush on an RPO play the QB ran, handoffs
    included) summed over the team's single-team QB rows, against FTN's RPO
    plays that became designed runs (any rusher) and those the QB kept."""
    path = os.path.join(raw_dir, "advstats_season_pass.csv")
    if not os.path.exists(path):
        return {"error": "no advstats_season_pass.csv"}
    pm, _ = R.pfr_crosswalk(os.path.join(raw_dir, "players.csv"), os.path.join(raw_dir, "db_playerids.csv"))
    pfr, pfr_team = collections.defaultdict(dict), collections.defaultdict(lambda: collections.Counter())
    with open(path, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            s = R._i(row.get("season"), 0)
            if s not in seasons:
                continue
            g = pm.get((row.get("pfr_id") or "").strip())
            if g and R._f(row.get("scrambles")) is not None:
                pfr[s][g] = pfr[s].get(g, 0.0) + R._f(row.get("scrambles"))
            t = R.team_code(row.get("team"))
            if R._f(row.get("rpo_rush_att")) is not None and len(t) <= 3 and not t[0].isdigit():
                pfr_team[s][t] += R._f(row.get("rpo_rush_att"))
    import numpy as np
    out = {}
    for s in seasons:
        if s not in stores:
            continue
        T = RP.season_tallies(stores[s], auxes.get(s), pos)
        mine = collections.defaultdict(lambda: [0.0, 0.0])
        team = collections.defaultdict(lambda: [0.0, 0.0])
        for t, lst in T.team_games.items():
            for side, r in lst:
                if side != "off":
                    continue
                team[t][0] += r["team"][RP.TL["rpo_runs"]]
                team[t][1] += r["team"][RP.TL["rpo_qb_keeps"]]
                for pid, ql in r["qbs"].items():
                    mine[pid][0] += ql[RP.QL["scr"]]
                    mine[pid][1] += ql[RP.QL["db"]]
        blk = {}
        a = [(mine[g][0], v) for g, v in pfr[s].items() if g in mine and mine[g][1] >= 100]
        if a:
            a_, b_ = np.asarray([x[0] for x in a]), np.asarray([x[1] for x in a])
            blk["scrambles"] = {"qbs": len(a), "nflverse_total": int(a_.sum()), "pfr_total": int(b_.sum()),
                                "exact": round(float(np.mean(a_ == b_)), 3), "within_3": round(float(np.mean(np.abs(a_ - b_) <= 3)), 3),
                                "r": None if len(a) < 6 else round(float(np.corrcoef(a_, b_)[0, 1]), 3)}
        tt = [t for t in team if t in pfr_team[s]]
        if tt and s >= D.FTN_SEASONS.start:
            f_, k_, p_ = (np.asarray([team[t][0] for t in tt]), np.asarray([team[t][1] for t in tt]),
                          np.asarray([pfr_team[s][t] for t in tt]))
            blk["rpo_runs_team"] = {"teams": len(tt), "ftn_rpo_designed_runs": int(f_.sum()), "ftn_rpo_qb_keeps": int(k_.sum()),
                                    "pfr_rpo_rush_att": int(p_.sum()),
                                    "r_ftn_runs_vs_pfr": None if f_.std() == 0 or p_.std() == 0 else round(float(np.corrcoef(f_, p_)[0, 1]), 3)}
        out[s] = blk
    out["note"] = ("PFR's scrambles and RPO counts are PFR's charting, nflverse's scrambles are nflfastR's qb_scramble and "
                   "the RPO flag FTN's is_rpo; scrambles for QBs with 100+ dropbacks (PFR's column is blank before 2024); "
                   "RPO runs per team (QBs traded mid-season, '2TM' rows, are left out of PFR's side)")
    return out


def check_contact(stores, auxes, pos, seasons=(2023, 2024, 2025)):
    """PFR carries per player-game against nflverse official rushes, and how
    often the official rushing_yards differ from yards_gained on a rush."""
    out = {}
    for s in seasons:
        st, ax = stores[s], auxes.get(s)
        T = RP.season_tallies(st, ax, pos)
        n = exact = within1 = 0
        for pid, recs in T.player_games.items():
            for r in recs:
                pl = r["players"].get(pid)
                if not pl or pl[RP.PL["pfr_car"]] <= 0 and pl[RP.PL["rush"]] <= 0:
                    continue
                if pl[RP.PL["pfr_car"]] <= 0:
                    continue
                n += 1
                exact += pl[RP.PL["pfr_car"]] == pl[RP.PL["rush"]]
                within1 += abs(pl[RP.PL["pfr_car"]] - pl[RP.PL["rush"]]) <= 1
        C = st["cols"]
        ex = ax["extras"] if ax and ax.get("extras") and R.aux_matches(ax, st) else None
        diff = rushes = 0
        if ex:
            for i in range(st["n"]):
                if C["kind"][i] in (D.RUN, D.SCRAMBLE, D.KNEEL) and C["rusher"][i]:
                    rushes += 1
                    if ex["rush_yds"][i] != D.NA_H and ex["rush_yds"][i] != C["yds"][i]:
                        diff += 1
        out[s] = {"pfr_player_games": n, "pfr_carries_equal_official_rushes": round(exact / n, 4) if n else None,
                  "within_1": round(within1 / n, 4) if n else None, "official_rushes": rushes,
                  "rushing_yards_differ_from_yards_gained": diff}
    return out


def check_coverage(stores, auxes, pos):
    out = {"direction": {}, "joins": {}, "aux_2026": {}, "ngs_coverage": {}}
    for s in sorted(stores):
        st, ax = stores[s], auxes.get(s)
        C = st["cols"]
        abt = ax["extras"]["aborted"] if ax and ax.get("extras") and R.aux_matches(ax, st) else None
        n = blank_loc = blank_gap_side = middle = 0
        for i in range(st["n"]):
            if C["kind"][i] != D.RUN or not C["rusher"][i] or (abt is not None and abt[i] == 1):
                continue
            if pos.get(C["rusher"][i]) not in ("RB", "FB"):
                continue
            n += 1
            if C["rloc"][i] == 0:
                blank_loc += 1
            elif C["rloc"][i] == 2:
                middle += 1
            elif C["rgap"][i] == 0:
                blank_gap_side += 1
        out["direction"][s] = {"rb_designed_runs": n, "location_blank": round(blank_loc / n, 4) if n else None,
                               "middle (gap blank by convention)": round(middle / n, 4) if n else None,
                               "left/right with gap blank": round(blank_gap_side / n, 4) if n else None}
        if ax:
            j = dict(ax.get("joins") or {})
            if "pbp_extras" in j:
                j["pbp_extras"] = {k: v for k, v in j["pbp_extras"].items() if k != "games_count_mismatch"}
            out["joins"][s] = j
            if ax.get("ngs"):
                T = RP.season_tallies(st, ax, pos)
                att = des = 0.0
                games = ngs_games = 0
                for pid, recs in T.player_games.items():
                    for r in recs:
                        pl = r["players"].get(pid)
                        if pl and pl[RP.PL["pos_rb"]] > 0:
                            des += pl[RP.PL["des"]]
                            att += pl[RP.PL["ngs_att"]]
                            if pl[RP.PL["des"]] >= 10:
                                games += 1
                                ngs_games += pl[RP.PL["ngs_games"]] > 0
                out["ngs_coverage"][s] = {"rb_designed_runs": des, "ngs_attempts_joined": att,
                                          "share_of_rb_runs_with_ryoe": round(att / des, 4) if des else None,
                                          "rb_games_10plus_runs": games, "of_which_ngs_row": ngs_games}
    ax = auxes.get(2026)
    if ax:
        def weeks_of(d):
            return sorted({int(g.split("_")[1]) for g in d})
        out["aux_2026"] = {"pfr_rush_weeks": weeks_of(ax.get("pfr_rush") or {}), "pfr_def_weeks": weeks_of(ax.get("pfr_def") or {}),
                           "pfr_pass_weeks": weeks_of(ax.get("pfr_pass") or {}), "snap_weeks": weeks_of(ax.get("snaps") or {}),
                           "ngs_weeks": sorted(ax.get("ngs") or {}), "injury_weeks": sorted(ax.get("injuries") or {}),
                           "situational_on_field": "none (no 2026 participation file)", "missing": ax.get("missing"),
                           "store_weeks": sorted({g["week"] for g in stores[2026]["games"]})}
    return out


# ---- the dataset ------------------------------------------------------------------------------------
PROJ = ("pass_att", "rush_att", "rush_yd", "rush_td", "rec_tgt", "rec", "rec_yd", "rec_td", "fum_lost")
ACTS = (("act_rush", "rush", "official rushes: designed (incl. sneaks) + scrambles + kneels + aborted snaps; sacks never"),
        ("act_rush_yds", "rush_yds", "official rushing yards (nflverse rushing_yards; kneels negative; sacks never)"),
        ("act_rush_td", "rush_td", "rushing TDs"),
        ("act_des", "des", "designed runs (RUN, not an aborted snap; sneaks included)"),
        ("act_des_yds", "des_yds", "yards on designed runs (yards_gained)"),
        ("act_sneak", "sneak", "QB sneaks (FTN is_qb_sneak; rule where FTN has no row)"), ("act_sneak_yds", "sneak_yds", "sneak yards"),
        ("act_rpo_keep", "rpo", "QB designed runs on FTN RPO plays"), ("act_rpo_keep_yds", "rpo_yds", "their yards"),
        ("act_scr", "scr", "scrambles"), ("act_scr_yds", "scr_yds", "scramble yards"),
        ("act_kneel", "kneel", "kneel-downs"), ("act_kneel_yds", "kneel_yds", "kneel yards (negative)"),
        ("act_abort", "abort", "aborted snaps charged as his rush"), ("act_abort_yds", "abort_yds", "their yards"),
        ("act_des_rz", "des_rz", "designed runs inside the 20"), ("act_des_gl", "des_gl", "designed runs inside the 5"),
        ("act_tgt", "tgt", "targets"), ("act_rec", "rec", "receptions"), ("act_rec_yds", "rec_yds", "receiving yards"),
        ("act_rec_td", "rec_td", "receiving TDs"), ("act_fuml", "fuml", "rush fumbles lost"),
        ("act_pfr_snaps", "pfr_snaps", "PFR offensive snaps"), ("act_pfr_team_snaps", "pfr_team_snaps", "the team's PFR snaps (games PFR lists him)"),
        ("act_pfr_ybc", "pfr_ybc", "PFR yards before contact"), ("act_pfr_yac", "pfr_yac", "PFR yards after contact"),
        ("act_pfr_btk", "pfr_btk", "PFR rushing broken tackles"))
QACTS = (("act_db", "db", "dropbacks (attempts + sacks + scrambles)"), ("act_dsg", "dsg", "designed runs, not sneaks"),
         ("act_dsg_yds", "dsg_yds", "their yards"), ("act_expo", "expo", "exposure plays (team snaps x his dropback share)"),
         ("act_press", "press", "pressured dropbacks (2023+ participation)"), ("act_scr_press", "scr_press", "scrambles under pressure"))


def _fmt(x):
    if x is None:
        return ""
    if isinstance(x, bool):
        return "1" if x else "0"
    if isinstance(x, float):
        if math.isnan(x) or math.isinf(x):
            return ""
        # four significant figures at or above 1 (245.37 -> 245.4), four decimals below
        # (a share 0.0123 keeps its digits): the committed files stay under 10 MB
        a = abs(x)
        nd = 4 if a < 1 else max(0, 4 - (int(math.log10(a)) + 1))
        s = f"{x:.{nd}f}"
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return "0" if s in ("-0", "") else s
    return str(x)


def _flat_cols(prefix, prof):
    """[(column, value, field)] from a profile: the value of every field; its
    _raw (the unshrunk ratio) for every MEASURED field (one carrying a metric,
    shrunk or not -- a static column set); its _n (weighted denominator) for
    every field."""
    out = []
    _walk(prefix, prof, out)
    return out


_RATE_BLOCK = ("ypc", "success", "epa", "stuff", "expl10", "expl20")


def _p_cols(prof):
    """The rb() columns. The restricted display block `any_snap` (round 4) enters as its games count alone: its
    other figures are the usage columns over a PFR-inclusive game set, never a feature, and the full block
    would push the committed files past 10 MB."""
    return [(c, v, f) for c, v, f in _flat_cols("p_", prof) if not c.startswith("p_any_snap_") or c == "p_any_snap_games"]


def _walk(prefix, d, out):
    """A designed-run rate block (a dict with "carries") shares one denominator:
    it is written once as <block>_carries, not as six _n columns."""
    block = isinstance(d.get("carries"), (int, float))
    if block:
        out.append((prefix + "carries", d["carries"], {"basis": "designed runs (weighted) behind " + prefix.rstrip("_"),
                                                       "unit": "count", "kind": "observed",
                                                       "source": (d.get("ypc") or {}).get("source")}))
    for k, f in d.items():
        if not isinstance(f, dict):
            continue
        c = prefix + k
        if "unit" in f and "kind" in f and "value" in f:
            out.append((c, f.get("value"), f))
            if "metric" in f:
                out.append((c + "_raw", f.get("raw"), dict(f, basis="raw (unshrunk) " + (f.get("basis") or ""),
                                                           kind="proxy" if f.get("kind") == "proxy" else "observed")))
            if not (block and k in _RATE_BLOCK):
                out.append((c + "_n", f.get("n"), {"basis": "denominator (weighted) of " + c, "unit": "count", "kind": "observed",
                                                    "source": f.get("source")}))
        else:
            _walk(c + "_", f, out)


def _actual_long(st, gi, a, b, pid, aux_ex):
    """The player's longest official rush of the game (kneels and aborted snaps out)."""
    C = st["cols"]
    best = None
    for i in range(a, b):
        if C["rusher"][i] != pid or C["kind"][i] not in (D.RUN, D.SCRAMBLE):
            continue
        if aux_ex is not None and aux_ex["aborted"][i] == 1:
            continue
        best = C["yds"][i] if best is None else max(best, C["yds"][i])
    return best


def week_rows(S, st, aux, season, week, games, pos, ids, sl_by_team, schema_acc):
    """Every RB and QB row of one week from a snapshot FROZEN before any of the
    week's games (the caller adds them to the tallies only after this returns).
    Raises if the snapshot holds the week."""
    if S.contains(season, week):
        raise RuntimeError(f"snapshot for {season} week {week} holds the week's own games")
    rb_rows, qb_rows = [], []
    dcache, qcache, olcache, ctxcache = {}, {}, {}, {}
    ex = aux["extras"] if (aux and aux.get("extras") and R.aux_matches(aux, st)) else None
    for gi, a, b in games:
        g = st["games"][gi]
        tmp = RP.RushTallies(season)
        recs = RP.add_game(tmp, st, aux, gi, a, b, pos)
        for team, r in recs.items():
            opp = r["dfn"]
            if opp not in dcache:
                dcache[opp] = S.defense_run(opp)
                qcache[opp] = S.defense_qb(opp)
            if team not in olcache:
                olcache[team] = S.offense_line(team)
                ctxcache[team] = S.team_context(team)
            sl = sl_by_team.get(team, {})
            base_keys = {"season": season, "week": week, "game_id": g["id"], "date": g.get("date"), "team": team, "opp": opp,
                         "home": 1 if g["home"] == team else 0}
            # ---- backs
            backs = {pid for pid, pl in r["players"].items()
                     if pos.get(pid) in ("RB", "FB") and (pl[RP.PL["des"]] + pl[RP.PL["tgt"]] + pl[RP.PL["abort"]] + pl[RP.PL["scr"]]
                                                          + pl[RP.PL["of_snaps"]] + pl[RP.PL["pfr_snaps"]] > 0)}
            sl_backs = {pid for pid, rec in sl.items() if pos.get(pid) in ("RB", "FB") and rec.get("proj")
                        and ((rec["proj"].get("rush_att") or 0) > 0 or (rec["proj"].get("rec_tgt") or 0) > 0)}
            for pid in sorted(backs | sl_backs):
                row = dict(base_keys)
                row.update(_keys(pid, ids, sl.get(pid), played=pid in backs))
                pl = r["players"].get(pid) or [0.0] * RP.PL_W
                for c, nm, _m in ACTS:
                    row[c] = pl[RP.PL[nm]]
                row["act_any_snap"] = _any_snap(row, pl)
                row["aux_status"] = _aux_str(S)
                row["act_rush_long"] = _actual_long(st, gi, a, b, pid, ex)
                row["act_team_des"] = r["team"][RP.TL["des"]]
                row["act_team_db"] = r["team"][RP.TL["db"]]
                _sleeper(row, sl.get(pid))
                st_ = S.status(pid, team)
                row["status"] = st_["value"]
                row["practice"] = st_.get("practice", "")
                room = S.rb_room_out(team, exclude=pid)
                row["tm_rb_out_share"] = room["value"]
                prof = S.rb(pid, team)
                row["p_games_cur"], row["p_games_w"] = prof["games"]["current"], prof["games"]["weighted"]
                row["d_games_cur"], row["d_games_w"] = dcache[opp]["games"]["current"], dcache[opp]["games"]["weighted"]
                cols = []
                cols += _p_cols(prof)
                cols += _flat_cols("d_", dcache[opp])
                cols += [(c, v, f) for c, v, f in _flat_cols("dq_", qcache[opp]) if c.startswith(("dq_pressure", "dq_blitz", "dq_hit_sack"))]
                cols += _flat_cols("ol_", {k: v for k, v in olcache[team].items() if k != "starters"})
                st5 = olcache[team].get("starters") or {}
                for k in ("out_report", "missed_last_game", "continuity"):
                    if isinstance(st5.get(k), dict):
                        cols.append((f"ol_starters_{k}", st5[k].get("value"), st5[k]))
                cols += _flat_cols("tc_", ctxcache[team])
                for c, v, f in cols:
                    row[c] = v
                    _acc(schema_acc, ("rb", c), f, v)
                rb_rows.append(row)
            # ---- quarterbacks
            qbs = {pid for pid, ql in r["qbs"].items() if ql[RP.QL["db"]] + ql[RP.QL["dsg"]] + ql[RP.QL["sneak"]] + ql[RP.QL["scr"]]
                   + ql[RP.QL["kneel"]] + ql[RP.QL["abort"]] > 0}
            qb1 = {pid for pid, rec in sl.items() if rec.get("role") == "QB1"}
            for pid in sorted(qbs | qb1):
                row = dict(base_keys)
                row.update(_keys(pid, ids, sl.get(pid), played=pid in qbs))
                pl = r["players"].get(pid) or [0.0] * RP.PL_W
                ql = r["qbs"].get(pid) or [0.0] * RP.QL_W
                for c, nm, _m in ACTS:
                    row[c] = pl[RP.PL[nm]]
                for c, nm, _m in QACTS:
                    row[c] = ql[RP.QL[nm]]
                row["act_any_snap"] = _any_snap(row, pl)
                row["aux_status"] = _aux_str(S)
                row["act_rush_long"] = _actual_long(st, gi, a, b, pid, ex)
                row["act_team_des"] = r["team"][RP.TL["des"]]
                row["act_team_db"] = r["team"][RP.TL["db"]]
                row["sl_qb1"] = 1 if pid in qb1 else 0
                _sleeper(row, sl.get(pid))
                st_ = S.status(pid, team)
                row["status"] = st_["value"]
                row["practice"] = st_.get("practice", "")
                prof = S.qb(pid)
                row["q_games_cur"], row["q_games_w"] = prof["games"]["current"], prof["games"]["weighted"]
                row["d_games_cur"], row["d_games_w"] = dcache[opp]["games"]["current"], dcache[opp]["games"]["weighted"]
                cols = []
                cols += _flat_cols("q_", prof)
                cols += _flat_cols("dq_", qcache[opp])
                cols += [(c, v, f) for c, v, f in _flat_cols("d_", dcache[opp])
                         if not any(x in c for x in ("by_direction", "by_side", "by_box", "by_offense_personnel"))]
                cols += _flat_cols("tc_", ctxcache[team])
                for c, v, f in cols:
                    row[c] = v
                    _acc(schema_acc, ("qb", c), f, v)
                qb_rows.append(row)
    if S.contains(season, week):
        raise RuntimeError(f"snapshot for {season} week {week} took in the week's own games while its rows were written")
    return rb_rows, qb_rows


def _keys(pid, ids, sl, played):
    info = ids.get(pid) or {}
    return {"gsis": D.gsis_str(pid), "sleeper_id": info.get("sleeper") or "", "name": info.get("name") or (sl or {}).get("name") or "",
            "pos": info.get("pos") or "", "act_played": 1 if played else 0, "in_sleeper": 1 if (sl and sl.get("proj")) else 0}


def _any_snap(row, pl):
    """act_any_snap: an offensive OR special-teams snap (PFR st_snaps) -- Kalshi settles a player on "at least
    one snap"; act_played counts offensive participation only (the review's m2: 397 of 400 Sleeper-box backs
    with act_played 0 had special-teams snaps only)."""
    return 1 if (row.get("act_played") or pl[RP.PL["pfr_snaps"]] > 0 or pl[RP.PL["pfr_st_snaps"]] > 0) else 0


def _aux_str(S):
    return ";".join(f"{k}:{v}" for k, v in sorted(S.aux_status.items()))


def _acc(schema_acc, key, f, v):
    """Every column's schema entry reads the field DEFINITION across rows: the kinds seen (a shrunk field is
    "modelled" though a week-1 row has no prior -- the reviews' M4 / m5) and the first basis of a row whose
    value exists (a week-1 OL row's "no game this season" is not the column's meaning)."""
    e = schema_acc.get(key)
    if e is None:
        e = schema_acc[key] = {"field": f, "kinds": set(), "has_value": v is not None}
    e["kinds"].add(f.get("kind") or "observed")
    if not e["has_value"] and v is not None:
        e["field"], e["has_value"] = f, True


def _sleeper(row, sl):
    pj = (sl or {}).get("proj") or {}
    ac = (sl or {}).get("act") or {}
    for k in PROJ:
        row[f"sl_proj_{k}"] = pj.get(k)
    for k in ("rush_att", "rush_yd", "rush_td", "rec_tgt", "rec", "rec_yd", "gp"):
        row[f"sl_act_{k}"] = ac.get(k)


KEYS = ("season", "week", "game_id", "date", "team", "opp", "home", "gsis", "sleeper_id", "name", "pos", "act_played",
        "act_any_snap", "in_sleeper", "status", "practice", "aux_status")
KEY_COLS = ("season", "week", "game_id", "date", "team", "opp", "home", "gsis", "sleeper_id", "name", "pos")


def _template(stores, auxes, pos, ids, seasons=DATASET_SEASONS):
    """The column lists, from the profiles' static structure (every profile
    returns the same paths whatever the unit), read off one real snapshot."""
    s = max(x for x in seasons if x in stores)
    wk = min(5, max(g["week"] for g in stores[s]["games"]) + 1)
    S = RP.build(stores, auxes, s, wk, pos)
    T = S.tallies[0][1]
    rb = next(iter(T.rb))
    qb = next(iter(S._qb_totals()))
    team = next(iter(T.dfn))
    acc = {}
    for kind, prof_cols in (("rb", _p_cols(S.rb(rb, team)) + _flat_cols("d_", S.defense_run(team))
                             + [(c, v, f) for c, v, f in _flat_cols("dq_", S.defense_qb(team)) if c.startswith(("dq_pressure", "dq_blitz", "dq_hit_sack"))]
                             + _flat_cols("ol_", {k: v for k, v in S.offense_line(team).items() if k != "starters"})
                             + [(f"ol_starters_{k}", None, {"basis": "", "unit": "", "kind": "observed"}) for k in ("out_report", "missed_last_game", "continuity")]
                             + _flat_cols("tc_", S.team_context(team))),
                            ("qb", _flat_cols("q_", S.qb(qb)) + _flat_cols("dq_", S.defense_qb(team))
                             + [(c, v, f) for c, v, f in _flat_cols("d_", S.defense_run(team))
                                if not any(x in c for x in ("by_direction", "by_side", "by_box", "by_offense_personnel"))]
                             + _flat_cols("tc_", S.team_context(team)))):
        acc[kind] = [c for c, _v, _f in prof_cols]
    return acc


K_FRAMES = {2023: "fit_only", 2024: "fit_only", 2025: "fit_only", 2026: "full"}


def _k_frame_path(frame, tmpdir):
    """The k table a season's rows are shrunk with (the reviews' m3 / F7): 2023-2025 rows -- the fit and the
    choosing seasons -- read `k_fit_only` (pooled 2018-2024, never 2025); 2026 rows the full 2018-2025
    table. Written to a temp file so nfl_rush_profiles.k_for reads it like the shipped one."""
    path = os.path.join(OUT, "reliability.json")
    if frame == "full":
        return path
    with open(path, encoding="utf-8") as fh:
        rel = json.load(fh)
    tmp = os.path.join(tmpdir, "k_fit_only.json")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"k": rel["k_fit_only"]["k"], "source": "reliability.json k_fit_only (pooled 2018-2024)"}, fh)
    return tmp


def run_build(stores, auxes, pos, ids, sleeper, out_dir, seasons=DATASET_SEASONS, max_week=None, k_frames=None):
    """Write the two datasets into out_dir; returns (meta, schema). `seasons` /
    `max_week` ({season: last week}) default to the dataset's (the guard runs
    the same path on a fixture). `k_frames` {season: "fit_only" | "full"}
    defaults to K_FRAMES; False keeps whatever k table is loaded."""
    import tempfile
    max_week = TEST_MAX_WEEK if max_week is None else max_week
    k_frames = K_FRAMES if k_frames is None else k_frames
    saved_k = RP._k_path
    tmpdir = tempfile.mkdtemp()
    tmpl = _template(stores, auxes, pos, ids, seasons)
    rb_cols = list(KEYS) + [c for c, _n, _m in ACTS] + ["act_rush_long", "act_team_des", "act_team_db", "tm_rb_out_share",
                                                         "p_games_cur", "p_games_w", "d_games_cur", "d_games_w"] \
        + [f"sl_proj_{k}" for k in PROJ] + [f"sl_act_{k}" for k in ("rush_att", "rush_yd", "rush_td", "rec_tgt", "rec", "rec_yd", "gp")] \
        + tmpl["rb"]
    qb_cols = list(KEYS) + [c for c, _n, _m in ACTS] + [c for c, _n, _m in QACTS] + ["act_rush_long", "act_team_des", "act_team_db", "sl_qb1",
                                                                                     "q_games_cur", "q_games_w", "d_games_cur", "d_games_w"] \
        + [f"sl_proj_{k}" for k in PROJ] + [f"sl_act_{k}" for k in ("rush_att", "rush_yd", "rush_td", "rec_tgt", "rec", "rec_yd", "gp")] \
        + tmpl["qb"]
    idx = D.ids_index(ids)
    gsis_by_sleeper = idx["sleeper"]
    paths = {"rb": os.path.join(out_dir, "rb_games_2023_2026.csv.gz"), "qb": os.path.join(out_dir, "qb_games_2023_2026.csv.gz")}
    # mtime 0 in the gzip header: a rebuild of the same rows hashes the same (gzip.open stamps the clock)
    fh = {k: io.TextIOWrapper(gzip.GzipFile(filename=p, mode="wb", mtime=0), encoding="utf-8", newline="") for k, p in paths.items()}
    w = {k: csv.writer(fh[k]) for k in fh}
    w["rb"].writerow(rb_cols)
    w["qb"].writerow(qb_cols)
    rb_set, qb_set = set(rb_cols), set(qb_cols)
    schema_acc = {}
    meta = {"by_season": {}, "rows": {"rb": 0, "qb": 0}, "started": time.time(), "decay": RP.DECAY, "back": RP.BACK,
            "k_frames": {}, "sleeper_rows_mapped": 0, "sleeper_rows": 0}
    for season in seasons:
        st = stores.get(season)
        if not st:
            continue
        if k_frames:
            RP.reload_k(_k_frame_path(k_frames.get(season, "full"), tmpdir))
        meta["k_frames"][season] = RP.k_for("rb_ypc")[1]
        aux = auxes.get(season)
        priors = [RP.season_tallies(stores[s], auxes.get(s), pos) for s in range(season - RP.BACK, season) if s in stores]
        current = RP.RushTallies(season)
        by_week = collections.defaultdict(list)
        for gi, a, b in P.game_ranges(st):
            by_week[st["games"][gi]["week"]].append((gi, a, b))
        last_week = max_week.get(season, 18)
        t0 = time.time()
        n = {"rb": 0, "qb": 0}
        for week in sorted(by_week):
            if week > last_week:
                break
            S = RP.RushSnapshot(season, week, current, priors, aux=auxes)
            sl_by_team = collections.defaultdict(dict)
            for (s_, w_, spid), rec in sleeper.items():
                if s_ != season or w_ != week:
                    continue
                meta["sleeper_rows"] += 1
                g = gsis_by_sleeper.get(spid)
                if g is None:
                    continue
                meta["sleeper_rows_mapped"] += 1
                t = {"LAR": "LA"}.get(rec.get("team") or "", rec.get("team") or "")
                sl_by_team[t][g] = rec
            rbr, qbr = week_rows(S, st, aux, season, week, by_week[week], pos, ids, sl_by_team, schema_acc)
            for row in rbr:
                extra = set(row) - rb_set
                if extra:
                    raise RuntimeError(f"rb row has columns outside the template: {sorted(extra)[:5]}")
                w["rb"].writerow([_fmt(row.get(c)) for c in rb_cols])
            for row in qbr:
                extra = set(row) - qb_set
                if extra:
                    raise RuntimeError(f"qb row has columns outside the template: {sorted(extra)[:5]}")
                w["qb"].writerow([_fmt(row.get(c)) for c in qb_cols])
            n["rb"] += len(rbr)
            n["qb"] += len(qbr)
            for gi, a, b in by_week[week]:
                RP.add_game(current, st, aux, gi, a, b, pos)
            log(f"[dataset] {season} week {week}: rb {n['rb']} qb {n['qb']} ({time.time() - t0:.0f}s)")
        meta["by_season"][season] = {"rb_rows": n["rb"], "qb_rows": n["qb"], "weeks": sorted(k for k in by_week if k <= last_week),
                                     "last_week": last_week}
        meta["rows"]["rb"] += n["rb"]
        meta["rows"]["qb"] += n["qb"]
    for k in fh:
        fh[k].close()
    if k_frames:
        RP.reload_k(saved_k)
    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)
    meta["k_note"] = ("each row's shrunk `value` columns use the k table of its frame: 2023-2025 rows k_fit_only (pooled "
                      "2018-2024: a 2025 validation reads no 2025 reliability), 2026 rows the full 2018-2025 table; `_raw` "
                      "and `_n` sit beside every value for re-shrinking")
    meta["seconds"] = round(time.time() - meta.pop("started"), 1)
    meta["files"] = {k: {"path": os.path.relpath(p, os.path.join(HERE, "..")).replace(os.sep, "/"), "bytes": os.path.getsize(p), "sha256": sha(p),
                         "columns": len(rb_cols if k == "rb" else qb_cols)} for k, p in paths.items()}
    schema = {"license": LICENSE, "k_note": meta["k_note"],
              "kind_note": "kind is read from the field definition across every row: not_available > proxy > modelled > observed"}
    for kind, cols in (("rb", rb_cols), ("qb", qb_cols)):
        lst = []
        for c in cols:
            e = schema_acc.get((kind, c)) or {}
            f = e.get("field") or {}
            lst.append({"name": c, "meaning": _meaning(c, f), "unit": _unit(c, f), "kind": _kind(c, f, e.get("kinds") or set()),
                        "source": _source(c, f.get("source") or _source_tag(c)), "source_tag": f.get("source") or _source_tag(c)})
        schema[kind] = lst
    return meta, schema


def _meaning(c, f):
    for cc, _nm, m in ACTS + QACTS:
        if c == cc:
            return m
    fixed = {"act_rush_long": "longest designed run or scramble of the game", "act_team_des": "the team's designed runs in the game",
             "act_team_db": "the team's dropbacks in the game", "sl_qb1": "1 when Sleeper's projection makes him the team's QB1 (most projected pass attempts; Tuesday snapshot)",
             "tm_rb_out_share": "carry share (team's last 4 games) of the OTHER backs listed Out/Doubtful on this week's report (pre-kickoff)",
             "status": "game status on this week's injury report (pre-kickoff): active (listed without a status, or not listed) / questionable / doubtful / out",
             "practice": "his last practice status on this week's report",
             "act_played": "OUTCOME: 1 when he had an offensive snap (NGS on-field or PFR), rush, target or dropback in the game -- "
                           "offensive participation only",
             "act_any_snap": "OUTCOME: 1 when he took any snap, offense or special teams (PFR st_snaps) -- Kalshi settles on one snap",
             "in_sleeper": "1 when Sleeper's post-week snapshot projects him this week (the row universe for players who did not "
                           "play comes from it: an Out-listed player has no row, so status never reads out or doubtful)",
             "aux_status": "the aux store each season of the profile read: aligned / stale / missing",
             "ol_starters_out_report": "of the team's as-of starting five (most PFR snaps over its last four games this season), "
                                       "how many this week's injury report lists Out or Doubtful (pre-kickoff); blank before "
                                       "the team's first game or with no report for the week",
             "ol_starters_missed_last_game": "of the as-of starting five, how many took under half the team's snaps in its last "
                                             "game this season (catches injured reserve, which leaves the report)",
             "ol_starters_continuity": "share of the team's OL snaps over its last four games this season taken by those five",
             "p_games_cur": "his games for this team this season before the cutoff week -- games pbp or participation shows him in "
                            "(a rush, target or on-field snap); special-teams-only and PFR-only games are in p_any_snap_*",
             "p_games_w": "his games in the profile, weighted (DECAY), the same appearance rule",
             "q_games_cur": "his games this season before the cutoff week", "q_games_w": "his games in the profile, weighted (DECAY)",
             "d_games_cur": "the opponent defense's games this season before the cutoff", "d_games_w": "its games in the profile, weighted"}
    if c in fixed:
        return fixed[c]
    if c.startswith("sl_proj_"):
        return f"Sleeper's projected {c[8:]} (revised post-week snapshot for 2023-2026; NOT provably pre-kickoff)"
    if c.startswith("sl_act_"):
        return f"Sleeper's box score {c[7:]}"
    return f.get("basis") or c


def _kind(c, f, kinds=()):
    if c in KEY_COLS or c == "aux_status":
        return "key"
    if c.startswith("sl_proj_") or c in ("in_sleeper", "sl_qb1"):
        return "observed (Sleeper post-week snapshot; not provably pre-kickoff)"
    if c.startswith(("act_", "sl_act_")):
        return "actual (outcome)"
    if c in ("status", "practice", "tm_rb_out_share", "ol_ol_listed_out"):
        return "observed (pre-kickoff injury report)"
    kinds = set(kinds) or {f.get("kind") or "observed"}
    for k in ("not_available", "proxy", "modelled"):
        if k in kinds:
            return k
    return "observed"


def _unit(c, f):
    if f.get("unit"):
        return f["unit"]
    if c in ("tm_rb_out_share",):
        return "share"
    if c.endswith(("_games_cur", "_games_w")):
        return "games"
    if c.endswith(("_yds", "_yds_long")) or c == "act_rush_long" or c.startswith(("act_pfr_ybc", "act_pfr_yac")) or c.endswith("rush_yd") or c.endswith("rec_yd"):
        return "yds"
    if c.startswith(("act_", "sl_act_", "sl_proj_", "ol_starters_out", "ol_starters_missed")) or c in ("sl_qb1", "in_sleeper"):
        return "count"
    if c in ("status", "practice", "aux_status"):
        return "category"
    if c == "ol_starters_continuity":
        return "share"
    return ""


def _source_tag(c):
    """The machine source tag (nfl_rush_profiles.SOURCE_TAGS) of a column that is not a profile field; profile
    columns carry their field's own tag. `restricted` tags (pfr / pfr_snaps / ngs) are display-only by default."""
    if c in KEY_COLS or c == "aux_status":
        return "key"
    if c.startswith("sl_") or c in ("in_sleeper", "sl_qb1"):
        return "sleeper"
    if c in ("act_pfr_snaps", "act_pfr_team_snaps"):
        return "pfr_snaps"
    if c.startswith("act_pfr"):
        return "pfr"
    if c in ("act_played", "act_any_snap"):
        return "derived:participation+pbp+pfr_snaps"
    if c in ("act_press", "act_scr_press"):
        return "derived:participation+pbp"
    if c.startswith(("act_sneak", "act_rpo")):
        return "derived:ftn+pbp"
    if c in ("status", "practice"):
        return "injuries"
    if c == "tm_rb_out_share":
        return "derived:injuries+pbp"
    if c in ("p_games_cur", "p_games_w"):
        return "derived:participation+pbp"
    return "pbp"


_CREDIT = {"pbp": "nflverse pbp",
           "participation": "nflverse participation (NGS charting; none for 2026)",
           "ftn": "FTN Data via nflverse (2022+)", "injuries": "NFL injury report via nflverse (this week's, pre-kickoff)",
           "pfr": "Pro Football Reference advanced stats via nflverse", "pfr_snaps": "Pro Football Reference snap counts via nflverse",
           "ngs": "NFL Next Gen Stats via nflverse (weekly rows: games with 10+ rushes)", "sleeper": "Sleeper weekly API",
           "none": "not available (no free source)", "key": "key"}


def _source(c, tag):
    """The human-readable source of a column, written FROM its machine tag (the re-checks found three columns whose text
    still said pbp while the tag said otherwise), with the notes a tag cannot carry."""
    parts = tag[len("derived:"):].split("+") if tag.startswith("derived:") else [tag]
    txt = " + ".join(_CREDIT.get(x, x) for x in parts)
    if c.startswith(("d_box", "p_rushing_by_box", "d_vs_rb_by_box")):
        txt += "; box: NGS count 2016-25, FTN where none (all 2026); unrestricted (owner's participation decision)"
    if c.startswith("sl_proj_") or c in ("in_sleeper", "sl_qb1"):
        txt += " (revised post-week snapshot; not provably pre-kickoff)"
    if c == "ol_starters_out_report":
        txt += " (the report's listings of the as-of starters, who come from the snap counts)"
    return txt

def _week_cap(args):
    """run_build's `max_week` from the build flags: None (the dataset's default, TEST_MAX_WEEK), {} for
    --all-weeks (every week the store holds, through 18), {2026: N} for --max-week N."""
    if args.all_weeks:
        return {}
    if args.max_week is not None:
        return {2026: int(args.max_week)}
    return None


def _deep_inputs(stores, auxes):
    """{"deep:<key>": sha256 or None} of every store, aux and the ids pickle the build loaded, as the
    rushing model's frame names its inputs: its prospective test binds the dataset's to the frame's (the
    frame takes its OUTCOMES from these rows; Opus's F1b)."""
    import deep_cache
    keys = [f"nfl_matchup_{s}" for s in sorted(stores)] + [f"nfl_rush_aux_{s}" for s in sorted(auxes) if auxes[s]] \
        + ["nfl_matchup_ids"]
    out = {}
    for k in keys:
        p = os.path.join(deep_cache.CACHE_DIR, f"{k}.pkl")
        out[f"deep:{k}"] = sha(p) if os.path.exists(p) else None
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("measure", "checks", "build"))
    ap.add_argument("--raw", default=None, help="dir with the raw nflverse files (checks)")
    ap.add_argument("--sleeper", default=None, help="dir with proj_2026_<w>.json / stats_2026_<w>.json (build)")
    ap.add_argument("--out", default=None, help="build: write the dataset there instead of research/data/nfl_rush/ "
                                                "(the rushing model's prospective dataset: those files are registered)")
    cap = ap.add_mutually_exclusive_group()
    cap.add_argument("--all-weeks", action="store_true", help="build: every 2026 week the store holds (default: through "
                                                                "week 7, TEST_MAX_WEEK, the registered dataset's cap)")
    cap.add_argument("--max-week", type=int, default=None, help="build: the last 2026 week built")
    args = ap.parse_args(argv)
    os.makedirs(OUT, exist_ok=True)
    stores, auxes, pos, ids = load_all()
    inputs = _deep_inputs(stores, auxes)
    t0 = time.time()
    if args.cmd == "measure":
        log(f"[measure] script sha256 {sha(os.path.abspath(__file__))} at {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())} UTC")
        path = os.path.join(OUT, "reliability.json")
        empty = os.path.join(OUT, "_empty_k.json")          # pass 1: an EMPTY table (QB rates inside the OE unshrunk;
        with open(empty, "w", encoding="utf-8") as fh:      # a missing file would fall back to the code defaults)
            json.dump({"k": {}, "source": "empty (measure pass 1)"}, fh)
        RP.reload_k(empty)
        try:
            rel1 = measure_reliability(stores, auxes, pos)
        finally:
            os.remove(empty)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(rel1, fh, indent=1, sort_keys=True)
        RP.reload_k(path)                                   # pass 2: the QB rates inside the OE shrunk by pass 1's k
        rel = measure_reliability(stores, auxes, pos)
        same = [m for m in rel["k"] if not m.endswith("_oe") and rel["k"][m] != rel1["k"].get(m)]
        if same:
            raise SystemExit(f"non-OE k moved between passes: {same}")
        rel["passes"] = {"pass1_oe_k": {m: v for m, v in rel1["k"].items() if m.endswith("_oe")},
                         "note": "pass 2 = the shipped table; only the def_*_oe metrics differ between passes"}
        rel["license"] = LICENSE
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(rel, fh, indent=1, sort_keys=True)
        RP.reload_k(path)
        log(f"[measure] reliability: {len(rel['k'])} metrics, unsupported {rel['unsupported']} ({time.time() - t0:.0f}s)")
        pers = measure_persistence(stores, auxes, pos)
        pers["license"] = LICENSE
        with open(os.path.join(OUT, "persistence.json"), "w", encoding="utf-8") as fh:
            json.dump(pers, fh, indent=1, sort_keys=True)
        log(f"[measure] persistence: tendency r {pers['tendency_pooled_r']} -> decay {pers['decay_chosen']} ({time.time() - t0:.0f}s)")
        sn = measure_sneak_rule(stores, auxes, pos)
        with open(os.path.join(OUT, "sneak_rule.json"), "w", encoding="utf-8") as fh:
            json.dump({"rule": RP.sneak_rule.__doc__, "by_season": sn, "license": LICENSE}, fh, indent=1)
        log(f"[measure] sneak rule {sn}")
    elif args.cmd == "checks":
        out = {"sleeper": check_sleeper(stores, auxes, pos, ids), "partition": check_partition(stores, auxes, pos),
               "coverage": check_coverage(stores, auxes, pos), "contact": check_contact(stores, auxes, pos)}
        if args.raw:
            out["pfr_season"] = check_pfr_season(stores, auxes, pos, args.raw)
        out["license"] = LICENSE
        with open(os.path.join(OUT, "checks.json"), "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=1, default=str)
        log(json.dumps(out, default=str)[:4000])
    elif args.cmd == "build":
        import research.nfl_matchup_dataset as MD
        sleeper, _fields = MD.load_sleeper(args.sleeper, set(DATASET_SEASONS))
        out = args.out or OUT
        os.makedirs(out, exist_ok=True)
        meta, schema = run_build(stores, auxes, pos, ids, sleeper, out, max_week=_week_cap(args))
        meta["license"] = LICENSE
        meta["inputs"] = inputs
        # the code that built it, every repository module this process loaded, and the interpreter: the
        # rushing model's prospective test refuses a dataset not built by its registered bytes
        repo = os.path.abspath(os.path.join(HERE, ".."))
        mods = sorted({os.path.relpath(os.path.abspath(m.__file__), repo).replace(os.sep, "/")
                       for m in list(sys.modules.values()) if getattr(m, "__file__", None)
                       and os.path.abspath(m.__file__).startswith(repo + os.sep)})
        meta["code"] = {f: sha(os.path.join(repo, f)) for f in mods}
        import numpy as _np
        meta["versions"] = {"python": sys.version.split()[0], "numpy": _np.__version__}
        with open(os.path.join(out, "schema.json"), "w", encoding="utf-8") as fh:
            json.dump(schema, fh, indent=1)
        with open(os.path.join(out, "meta.json"), "w", encoding="utf-8") as fh:
            json.dump(meta, fh, indent=1, default=str)
        log(json.dumps(meta, default=str))


if __name__ == "__main__":
    main()
