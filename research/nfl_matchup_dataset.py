"""The NFL matchup engine's data foundation, measured and laid out: how
reliable each kind of charted signal is, how much of a defense's identity
carries from one season to the next, and one row per player-game with every
pre-game profile beside Sleeper's line and the box score -- for the modelling
agent to fit on 2023-2024, validate on 2025 and test ONCE on 2026 (weeks 1-7;
the stores hold weeks 1-6, every 2026 game nflverse had published by
2026-10-22 15:03 UTC).

RESEARCH. Numpy only (no pandas). Nothing here is read by the app.

    export DEEP_CACHE_DIR=<dir holding nfl_matchup_<season>.pkl>  VIGIL_NO_BG=1
    python3 -m research.nfl_matchup_dataset measure            # persistence, reliability, 2026 scheme flags
    python3 -m research.nfl_matchup_dataset build --sleeper DIR  # the dataset (DIR: proj_2026_<w>.json, stats_2026_<w>.json)
    python3 -m research.nfl_matchup_dataset check              # pbp actuals vs Sleeper's box, 2023-2025 only

Outputs (research/data/nfl_matchup/): persistence.json, reliability.json
(nfl_matchup_profiles reads its "k" table), scheme_change_2026.json,
charting_agreement.json, dataset_2023_2026.csv.gz + dataset_schema.json +
dataset_meta.json. The raw nflverse files are never committed; the stores are
rebuilt by nfl_matchup_data.refresh.

What the measurements decide (written before they were run, 2026-10-23):
- The prior-season weight (nfl_matchup_profiles.DECAY) is read off the
  year-over-year Pearson r of the defensive tendencies: a weight near r is
  the regression-to-the-mean a last-season figure deserves. If the pooled r
  of the shell/man/blitz/personnel tendencies is under 0.3 the prior season
  carries weight 0.25; 0.3-0.6 weight 0.5; above 0.6 weight 0.75.
- Each shrinkage k is the Spearman-Brown k of the split-half (odd vs even
  weeks) reliability of that signal kind, pooled over the seasons measured:
  with r the correlation of the two halves and n the mean denominator of a
  half, k = n (1 - r) / r, so that a unit with n denominator units is
  believed n / (n + k). A signal whose r is not above zero gets k = None and
  the profile keeps the prior (eb = prior) -- the data does not support it.
- A 2026 defense is flagged as a possible scheme change when its weeks 1-6
  FTN blitz rate or box figure, MINUS the league's change over the same
  window, sits more than two standard deviations of the (centred) 2025-vs-2024
  same-window changes away from its 2025 figure. The centring was added after
  the first run showed a league-wide box drift flagging 22 of 128 cells
  (report §5.4 discloses it); the decay and k rules above did not change.

Fixes after the review (research/reviews/nfl_matchup_data.md, 2026-10-23):
every week's rows are built from a Snapshot frozen BEFORE any of that week's
games enter the tallies (week_rows; F3); the q_* quarterback is labelled as
Sleeper's Tuesday-snapshot QB1 and a pre-kickoff pick (frozen capture, else
the last starter as of the cutoff) travels beside it as qp_* (F6); a
Sleeper-only row is dropped when the player has a played row that week (N7);
p_tgt_per_db_proxy gives 2026 a same-season per-dropback rate from PFR snap
shares (F7); the k table carries one entry per shrunk quantity (F4) and a
2018-2024-only table for a validation that must not have seen 2025 (O2).
"""
import argparse
import collections
import csv
import gzip
import hashlib
import json
import math
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.setdefault("VIGIL_NO_BG", "1")

import nfl_matchup_data as D            # noqa: E402
import nfl_matchup_profiles as P        # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "nfl_matchup")
PLAYER_WEEKS = os.path.join(HERE, "data", "player_weeks_2022_2025.csv.gz")
DATASET_SEASONS = (2023, 2024, 2025, 2026)
TEST_MAX_WEEK = {2026: 7}
SLEEPER_TEAM = {"LA": "LAR"}            # nflverse -> Sleeper; every other code agrees (checked 2026-10-23)
MIN_TGT_HALF = 15                       # a player's split-half row needs this many targets in each half
MIN_CAR_HALF = 25
MIN_DB_HALF = 100


def log(msg):
    print(msg, flush=True)


# ---- stores ----------------------------------------------------------------------
LICENSE = {"credit": "FTN Data via nflverse (CC BY-SA 4.0); nflverse-data (CC BY 4.0); DynastyProcess db_playerids",
           "share_alike": "tables derived from FTN charting are shared under CC BY-SA 4.0"}


def load_stores(seasons=range(2018, 2027)):
    stores = {}
    for s in seasons:
        st = D.load_season(s)
        if st:
            stores[s] = st
    if not stores:
        raise SystemExit("no nfl_matchup_<season>.pkl in DEEP_CACHE_DIR -- run nfl_matchup_data.refresh first")
    return stores


def positions():
    ids = D.load_ids()
    if not ids:
        raise SystemExit("no nfl_matchup_ids.pkl in DEEP_CACHE_DIR")
    return P.positions_from_ids(ids), ids["ids"]


def snap(T, season):
    """A profile reader over one tallies object with no cutoff in its way."""
    return P.Snapshot(season, 99, T, [])


def v(x):
    return None if x is None else x.get("v")


def eb(x):
    return None if x is None else x.get("eb")


def pearson(a, b):
    import numpy as np
    a, b = np.asarray(a, float), np.asarray(b, float)
    ok = ~(np.isnan(a) | np.isnan(b))
    if ok.sum() < 4:
        return None, int(ok.sum())
    a, b = a[ok], b[ok]
    if a.std() == 0 or b.std() == 0:
        return None, int(ok.sum())
    return float(np.corrcoef(a, b)[0, 1]), int(ok.sum())


def spearman(a, b):
    import numpy as np
    a, b = np.asarray(a, float), np.asarray(b, float)
    ok = ~(np.isnan(a) | np.isnan(b))
    if ok.sum() < 4:
        return None
    ra, rb = a[ok].argsort().argsort(), b[ok].argsort().argsort()
    return float(np.corrcoef(ra, rb)[0, 1])


# ---- unit metrics read off profiles ---------------------------------------------
def def_metrics(S, team):
    d = S.defense(team)
    c, p, a, f, pr, b = d["coverage"], d["personnel"], d["allowed"], d["fantasy_allowed"], d["pressure"], d["box"]
    m = {"man": (v(c["man"]), c["man"]["n"]), "two_high": (v(c["two_high"]), c["two_high"]["n"]),
         "single_high": (v(c["single_high"]), c["single_high"]["n"]), "zero": (v(c["zero"]), c["zero"]["n"]),
         "blitz": (v(pr["blitz"]), pr["blitz"]["n"]), "pressure": (v(pr["pressure"]), pr["pressure"]["n"]),
         "sack": (v(pr["sack"]), pr["sack"]["n"]), "ttt": (v(pr["ttt"]), pr["ttt"]["n"]),
         "nickel": (v(p["nickel"]), p["nickel"]["n"]), "dime": (v(p["dime"]), p["dime"]["n"]),
         "base": (v(p["base"]), p["base"]["n"]), "light_box": (v(b["light"]), b["light"]["n"]),
         "heavy_box": (v(b["heavy"]), b["heavy"]["n"]), "box_mean": (v(b["mean"]), b["mean"]["n"]),
         "run_ypc": (v(d["run"]["ypc"]), d["run"]["carries"]), "run_success": (v(d["run"]["success"]), d["run"]["carries"]),
         "epa_db": (v(pr["epa_db"]), pr["epa_db"]["n"]), "scramble": (v(pr["scramble"]), pr["scramble"]["n"])}
    for cov in ("COVER_1", "COVER_2", "COVER_3", "COVER_4", "COVER_6", "2_MAN", "COVER_0"):
        m[f"fam_{cov}"] = (v(c["families"][cov]), c["families"][cov]["n"])
        blk = a["per_coverage"][cov]
        m[f"epa_t_{cov}"] = (v(blk["epa_t"]), blk["targets"])
        m[f"ypt_{cov}"] = (v(blk["ypt"]), blk["targets"])
    for mz in ("man", "zone"):
        blk = a["per_mz"][mz]
        m[f"epa_t_{mz}"] = (v(blk["epa_t"]), blk["targets"])
        m[f"ypt_{mz}"] = (v(blk["ypt"]), blk["targets"])
    for pos in ("WR", "TE", "RB"):
        blk = a["per_pos"][pos]
        m[f"ypt_{pos}"] = (v(blk["ypt"]), blk["targets"])
        m[f"epa_t_{pos}"] = (v(blk["epa_t"]), blk["targets"])
        m[f"catch_{pos}"] = (v(blk["catch"]), blk["targets"])
        m[f"tgt_per_db_{pos}"] = (v(blk["tgt_per_db"]), blk["tgt_per_db"]["n"])
        pg = f[pos]["per_game"]
        m[f"ppr_pg_{pos}"] = (v(pg["ppr"]), pg["ppr"]["n"])
        m[f"tgt_pg_{pos}"] = (v(pg["targets"]), pg["targets"]["n"])
        adj = f[pos].get("opp_adjusted") or {}
        if adj:
            m[f"adj_ppr_{pos}"] = (v(adj["ppr"]), adj["ppr"]["n"])
    pg = f["QB"]["per_game"]
    m["pass_yds_pg_QB"] = (v(pg["pass_yds"]), pg["pass_yds"]["n"])
    m["ppr_pg_QB"] = (v(pg["ppr"]), pg["ppr"]["n"])
    return m


def off_metrics(S, team):
    o = S.offense(team)
    return {"pass_rate": (v(o["pass_rate"]), o["pass_rate"]["n"]), "proe": (v(o["proe"]), o["proe"]["n"]),
            "neutral_pass_rate": (v(o["neutral_pass_rate"]), o["neutral_pass_rate"]["n"]),
            "play_action": (v(o["play_action"]), o["play_action"]["n"]), "motion": (v(o["motion"]), o["motion"]["n"]),
            "shotgun": (v(o["shotgun"]), o["shotgun"]["n"]), "p11": (o["personnel"].get("11", {}).get("share"), o["snaps"]),
            "deep_rate": (v(o["targets"]["deep_rate"]), o["targets"]["deep_rate"]["n"])}


def player_metrics(S, pid):
    p = S.player(pid)
    u, r = p["usage"], p["receiving"]
    m = {"tgt_per_db": (v(u["tgt_per_db"]), u["tgt_per_db"]["n"]), "target_share": (v(u["target_share"]), u["target_share"]["n"]),
         "air_share": (v(u["air_share"]), u["air_share"]["n"]), "catch": (v(r["catch"]), r["targets"]), "ypt": (v(r["ypt"]), r["targets"]),
         "epa_t": (v(r["epa_t"]), r["targets"]), "adot": (v(r["adot"]), r["adot"]["n"]), "yac_rec": (v(r["yac_rec"]), r["yac_rec"]["n"]),
         "contested": (v(r["contested"]), r["contested"]["n"]), "ppr_pg": (v(p["fantasy"]["ppr"]), p["fantasy"]["games"]),
         "mid_share_proxy": (v(p["alignment_proxy"]["value"]), p["alignment_proxy"]["value"]["n"]),
         "route_vertical": (v(r["route_mix"]["vertical"]), r["route_mix"]["vertical"]["n"]),
         "ypc": (v(p["rushing"]["ypc"]), p["rushing"]["carries"]), "rush_success": (v(p["rushing"]["success"]), p["rushing"]["carries"])}
    man, zone = u["tgt_per_db_vs_man"], u["tgt_per_db_vs_zone"]
    if v(man) is not None and v(zone) is not None:
        m["mz_tgt_rate_split"] = (v(man) - v(zone), min(man["n"], zone["n"]))
    rm, rz = r["vs_man"], r["vs_zone"]
    if v(rm["ypt"]) is not None and v(rz["ypt"]) is not None:
        m["mz_ypt_split"] = (v(rm["ypt"]) - v(rz["ypt"]), min(rm["targets"], rz["targets"]))
        m["mz_epa_split"] = (v(rm["epa_t"]) - v(rz["epa_t"]), min(rm["targets"], rz["targets"]))
    m["ypt_man"] = (v(rm["ypt"]), rm["targets"])
    m["ypt_zone"] = (v(rz["ypt"]), rz["targets"])
    pc = r["per_coverage"]
    for cov in ("COVER_1", "COVER_3", "COVER_2", "COVER_4"):
        blk = pc.get(cov)
        if blk:
            m[f"epa_t_{cov}"] = (v(blk["epa_t"]), blk["targets"])
            m[f"tgt_per_db_{cov}"] = (v(blk["tgt_per_db"]), blk["tgt_per_db"]["n"])
    return m


def qb_metrics(S, pid):
    q = S.qb(pid)
    m = {"epa_db": (v(q["epa_db"]), q["dropbacks"]), "ttt": (v(q["ttt"]), q["ttt"]["n"]), "deep_rate": (v(q["deep_rate"]), q["deep_rate"]["n"]),
         "sack": (v(q["sack"]), q["dropbacks"])}
    for d in ("short", "intermediate", "deep"):
        blk = q["by_depth"].get(d)
        if blk:
            m[f"cpoe_{d}"] = (v(blk["cpoe"]), blk["cpoe"]["n"])
    for name in ("man", "zone", "pressure", "no_pressure", "blitz", "no_blitz"):
        blk = q["vs"].get(name)
        if blk:
            m[f"epa_db_{name}"] = (v(blk["epa_db"]), blk["dropbacks"])
    if "man" in q["vs"] and "zone" in q["vs"]:
        m["mz_epa_split"] = (v(q["vs"]["man"]["epa_db"]) - v(q["vs"]["zone"]["epa_db"]), min(q["vs"]["man"]["dropbacks"], q["vs"]["zone"]["dropbacks"]))
    return m


# ---- measurement 1: year-over-year persistence -----------------------------------
def measure_persistence(stores, pos):
    import numpy as np
    out = {"pairs": {}, "note": "Pearson r and Spearman rho across the teams of consecutive full regular seasons; "
                                 "league means both years show the charting level shift"}
    snaps = {}
    for s, st in stores.items():
        if s == 2026:
            continue
        snaps[s] = snap(P.season_tallies(st, pos), s)
    for s in sorted(snaps):
        if s + 1 not in snaps:
            continue
        A, B = snaps[s], snaps[s + 1]
        teams = sorted(set(A.tallies[0][1].dfn) & set(B.tallies[0][1].dfn))
        ma = {t: def_metrics(A, t) for t in teams}
        mb = {t: def_metrics(B, t) for t in teams}
        oa = {t: off_metrics(A, t) for t in teams}
        ob = {t: off_metrics(B, t) for t in teams}
        pair = {}
        for name in ma[teams[0]]:
            a = [ma[t].get(name, (None,))[0] for t in teams]
            b = [mb[t].get(name, (None,))[0] for t in teams]
            a = [np.nan if x is None else x for x in a]
            b = [np.nan if x is None else x for x in b]
            r, n = pearson(a, b)
            pair[f"def.{name}"] = {"r": None if r is None else round(r, 3), "rho": None if spearman(a, b) is None else round(spearman(a, b), 3),
                                   "n_teams": n, "mean_y1": round(float(np.nanmean(a)), 4), "mean_y2": round(float(np.nanmean(b)), 4),
                                   "sd_y1": round(float(np.nanstd(a)), 4), "sd_y2": round(float(np.nanstd(b)), 4),
                                   "mean_n_y1": round(float(np.nanmean([ma[t].get(name, (None, np.nan))[1] or np.nan for t in teams])), 1)}
        for name in oa[teams[0]]:
            a = [np.nan if oa[t][name][0] is None else oa[t][name][0] for t in teams]
            b = [np.nan if ob[t][name][0] is None else ob[t][name][0] for t in teams]
            r, n = pearson(a, b)
            pair[f"off.{name}"] = {"r": None if r is None else round(r, 3), "rho": None if spearman(a, b) is None else round(spearman(a, b), 3),
                                   "n_teams": n, "mean_y1": round(float(np.nanmean(a)), 4), "mean_y2": round(float(np.nanmean(b)), 4)}
        out["pairs"][f"{s}->{s + 1}"] = pair
    # pooled r per metric (mean over pairs, weighted equally -- each pair is 32 teams)
    pooled = {}
    for pk, pair in out["pairs"].items():
        for name, rec in pair.items():
            if rec["r"] is not None:
                pooled.setdefault(name, []).append(rec["r"])
    out["pooled_r"] = {k: round(float(np.mean(vs)), 3) for k, vs in pooled.items()}
    tend = [out["pooled_r"][k] for k in ("def.man", "def.two_high", "def.single_high", "def.blitz", "def.nickel", "def.dime",
                                         "def.fam_COVER_1", "def.fam_COVER_3", "def.fam_COVER_2", "def.fam_COVER_4") if k in out["pooled_r"]]
    out["tendency_pooled_r"] = round(float(np.mean(tend)), 3) if tend else None
    r = out["tendency_pooled_r"] or 0.0
    out["decay_rule"] = "0.25 if r < 0.3, 0.5 if 0.3 <= r < 0.6, 0.75 if r >= 0.6 (written before running)"
    out["decay_chosen"] = 0.25 if r < 0.3 else 0.5 if r < 0.6 else 0.75
    return out


# ---- measurement 2: split-half reliability -------------------------------------
def _half_tallies(st, pos):
    odd = P.Tallies(st["season"])
    even = P.Tallies(st["season"])
    for gi, a, b in P.game_ranges(st):
        w = st["games"][gi]["week"]
        P.add_game(odd if w % 2 == 1 else even, st, gi, a, b, pos)
    return odd, even


def _rel_block(rows):
    """rows: [(value_half1, n1, value_half2, n2)] -> r, mean n, k."""
    import numpy as np
    rows = [r for r in rows if r[0] is not None and r[2] is not None]
    if len(rows) < 6:
        return {"r": None, "n_units": len(rows)}
    a = [r[0] for r in rows]
    b = [r[2] for r in rows]
    r, n = pearson(a, b)
    nbar = float(np.mean([(x[1] + x[3]) / 2.0 for x in rows]))
    k = None
    if r is not None and r > 0.02:
        k = nbar * (1.0 - r) / r
    return {"r": None if r is None else round(r, 3), "n_units": n, "mean_n_half": round(nbar, 1),
            "k": None if k is None else round(k, 1),
            "reliability_full_season": None if k is None else round(2 * nbar / (2 * nbar + k), 3)}


def measure_reliability(stores, pos):
    import numpy as np
    out = {"method": "odd weeks vs even weeks of one regular season; Pearson r across units; k = n(1-r)/r (Spearman-Brown) "
                     "with n the mean denominator of a half", "by_season": {}, "pooled": {}}
    acc = collections.defaultdict(list)
    acc_fit = collections.defaultdict(list)
    for s, st in sorted(stores.items()):
        if s == 2026:
            continue
        odd, even = _half_tallies(st, pos)
        A, B = snap(odd, s), snap(even, s)
        rows = collections.defaultdict(list)
        teams = sorted(set(odd.dfn) & set(even.dfn))
        for t in teams:
            ma, mb = def_metrics(A, t), def_metrics(B, t)
            for name, (va, na) in ma.items():
                vb, nb = mb.get(name, (None, 0))
                rows[f"def.{name}"].append((va, na or 0, vb, nb or 0))
            oa, ob = off_metrics(A, t), off_metrics(B, t)
            for name, (va, na) in oa.items():
                vb, nb = ob.get(name, (None, 0))
                rows[f"off.{name}"].append((va, na or 0, vb, nb or 0))
        # players with enough targets in both halves
        for pid in set(odd.pl) & set(even.pl):
            ta = P._agg(odd.pl[pid]["rec"], lambda k: True, 15)[0]
            tb = P._agg(even.pl[pid]["rec"], lambda k: True, 15)[0]
            ca = P._agg(odd.pl[pid]["rush"], lambda k: True, 7)[0]
            cb = P._agg(even.pl[pid]["rush"], lambda k: True, 7)[0]
            if (ta >= MIN_TGT_HALF and tb >= MIN_TGT_HALF) or (ca >= MIN_CAR_HALF and cb >= MIN_CAR_HALF):
                ma, mb = player_metrics(A, pid), player_metrics(B, pid)
                for name, (va, na) in ma.items():
                    if name in ("ypc", "rush_success") and not (ca >= MIN_CAR_HALF and cb >= MIN_CAR_HALF):
                        continue
                    if name not in ("ypc", "rush_success") and not (ta >= MIN_TGT_HALF and tb >= MIN_TGT_HALF):
                        continue
                    vb, nb = mb.get(name, (None, 0))
                    if name.startswith(("epa_t_COVER", "tgt_per_db_COVER", "mz_")) and (min(na or 0, nb or 0) < 8):
                        continue
                    rows[f"pl.{name}"].append((va, na or 0, vb, nb or 0))
        for pid in set(odd.qb) & set(even.qb):
            if odd.qb[pid]["tot"][0] >= MIN_DB_HALF and even.qb[pid]["tot"][0] >= MIN_DB_HALF:
                ma, mb = qb_metrics(A, pid), qb_metrics(B, pid)
                for name, (va, na) in ma.items():
                    vb, nb = mb.get(name, (None, 0))
                    rows[f"qb.{name}"].append((va, na or 0, vb, nb or 0))
        out["by_season"][s] = {name: _rel_block(rs) for name, rs in rows.items()}
        for name, rs in rows.items():
            acc[name].extend(rs)
            if s <= 2024:
                acc_fit[name].extend(rs)
    out["pooled"] = {name: _rel_block(rs) for name, rs in acc.items()}
    pooled_fit = {name: _rel_block(rs) for name, rs in acc_fit.items()}

    # the k table nfl_matchup_profiles reads: one entry per shrunk quantity, each the
    # mean k of the measured signal(s) of that kind (F4 of the review: the first
    # version reused the blitz k for pressure, sack, TTT and EPA per dropback)
    def kk(pool, *names):
        ks = [pool[n]["k"] for n in names if n in pool and pool[n].get("k")]
        return round(float(np.mean(ks)), 1) if ks else None

    def table(pool):
        return {"def_cov_share": kk(pool, "def.man", "def.two_high", "def.fam_COVER_1", "def.fam_COVER_3"),
                "def_blitz": kk(pool, "def.blitz"), "def_pressure": kk(pool, "def.pressure"),
                "def_sack": kk(pool, "def.sack"), "def_ttt": kk(pool, "def.ttt"), "def_epa_db": kk(pool, "def.epa_db"),
                "def_scramble": kk(pool, "def.scramble"),
                "def_box": kk(pool, "def.light_box", "def.heavy_box"), "def_pers": kk(pool, "def.nickel", "def.dime"),
                "def_allow_target": kk(pool, "def.ypt_WR", "def.ypt_TE", "def.ypt_RB", "def.epa_t_WR", "def.epa_t_man", "def.epa_t_zone"),
                "def_allow_game": kk(pool, "def.ppr_pg_WR", "def.ppr_pg_TE", "def.ppr_pg_RB", "def.ppr_pg_QB"),
                "def_run": kk(pool, "def.run_ypc", "def.run_success"),
                "off_rate": kk(pool, "off.pass_rate", "off.proe", "off.play_action", "off.motion"),
                "qb_rate": kk(pool, "qb.epa_db", "qb.sack"), "qb_depth": kk(pool, "qb.cpoe_short", "qb.cpoe_intermediate", "qb.cpoe_deep"),
                "qb_ttt": kk(pool, "qb.ttt"), "qb_deep": kk(pool, "qb.deep_rate"),
                "pl_target": kk(pool, "pl.catch", "pl.ypt", "pl.epa_t"), "pl_snap": kk(pool, "pl.tgt_per_db"),
                "pl_adot": kk(pool, "pl.adot"), "pl_yac": kk(pool, "pl.yac_rec"),
                "pl_game": kk(pool, "pl.ppr_pg"), "pl_share": kk(pool, "pl.target_share"), "pl_carry": kk(pool, "pl.ypc", "pl.rush_success")}
    out["k"] = table(out["pooled"])
    # the same table from 2018-2024 only, for a model validation on 2025 that must not have seen it (O2)
    out["k_fit_only"] = {"seasons": "2018-2024", "k": table(pooled_fit)}
    out["unsupported"] = sorted(n for n, b in out["pooled"].items() if b.get("r") is not None and b["r"] <= 0.02)
    out["source"] = "research/nfl_matchup_dataset.py measure (split-half, " + ",".join(str(s) for s in out["by_season"]) + ")"
    return out


# ---- measurement 3: FTN vs NGS agreement, 2026 scheme flags ----------------------
def measure_agreement(stores):
    out = {}
    for s, st in sorted(stores.items()):
        C = st["cols"]
        both_box = eq_box = both_rush = eq_rush = both_blitz = eq_blitz = 0
        for i in range(st["n"]):
            if C["part"][i] and C["ftn"][i]:
                if C["box"][i] > 0 and C["fbox"][i] > 0:
                    both_box += 1
                    eq_box += C["box"][i] == C["fbox"][i]
                if C["db"][i] and C["rushers"][i] > 0 and C["frush"][i] > 0:
                    both_rush += 1
                    eq_rush += C["rushers"][i] == C["frush"][i]
                    both_blitz += 1
                    eq_blitz += (C["rushers"][i] >= 5) == (C["frush"][i] >= 5)
        if both_box:
            out[s] = {"box_plays": both_box, "box_equal": round(eq_box / both_box, 4),
                      "rushers_plays": both_rush, "rushers_equal": round(eq_rush / both_rush, 4) if both_rush else None,
                      "blitz_flag_equal": round(eq_blitz / both_blitz, 4) if both_blitz else None}
    return out


def _window_metrics(st, pos, max_week=None):
    T = P.season_tallies(st, pos, max_week=max_week)
    S = snap(T, st["season"])
    out = {}
    for t in sorted(T.dfn):
        d = S.defense(t)
        out[t] = {"blitz": v(d["pressure"]["blitz"]), "blitz_n": d["pressure"]["blitz"]["n"],
                  "box_mean": v(d["box"]["mean"]), "box_n": d["box"]["mean"]["n"],
                  "light_box": v(d["box"]["light"]), "heavy_box": v(d["box"]["heavy"]),
                  "motion_faced": None}
    return out


def measure_scheme_change(stores, pos):
    """2026 weeks 1-6 (FTN) against 2025 full; the null spread from 2025 weeks
    1-6 against 2024 full, the same comparison a year earlier."""
    import numpy as np
    if 2026 not in stores or 2025 not in stores or 2024 not in stores:
        return {"error": "needs 2024, 2025 and 2026 stores"}
    w26 = max(g["week"] for g in stores[2026]["games"]) + 1
    cur = _window_metrics(stores[2026], pos)
    prev = _window_metrics(stores[2025], pos)
    null_cur = _window_metrics(stores[2025], pos, max_week=w26)
    null_prev = _window_metrics(stores[2024], pos)
    out = {"window": f"2026 weeks 1-{w26 - 1} vs 2025 full season; null = 2025 weeks 1-{w26 - 1} vs 2024 full", "metrics": {}, "flags": []}
    out["note"] = ("differences are centred on the league-wide change of the same window, so a charting or league-wide "
                   "drift (every box count up a quarter of a man) is not a scheme change; |z| > 2 flags")
    for name in ("blitz", "box_mean", "light_box", "heavy_box"):
        nd = [null_cur[t][name] - null_prev[t][name] for t in null_cur if t in null_prev and null_cur[t][name] is not None and null_prev[t][name] is not None]
        nd = np.asarray(nd, float)
        sd = float(np.std(nd - nd.mean())) if len(nd) else None
        diffs = {t: cur[t][name] - prev[t][name] for t in cur if t in prev and cur[t][name] is not None and prev[t][name] is not None}
        league = float(np.mean(list(diffs.values()))) if diffs else 0.0
        rows = {}
        for t, diff in diffs.items():
            z = (diff - league) / sd if sd else None
            rows[t] = {"now": round(cur[t][name], 4), "last": round(prev[t][name], 4), "diff": round(diff, 4),
                       "z": None if z is None else round(z, 2)}
            if z is not None and abs(z) > 2:
                out["flags"].append({"team": t, "metric": name, "now": round(cur[t][name], 4), "last": round(prev[t][name], 4),
                                     "diff_vs_league": round(diff - league, 4), "z": round(z, 2)})
        out["metrics"][name] = {"null_sd": None if sd is None else round(sd, 4), "league_change": round(league, 4), "teams": rows}
    return out


# ---- Sleeper's weekly lines ---------------------------------------------------------
PROJ_FIELDS = ("pass_att", "pass_cmp", "pass_yd", "pass_td", "pass_int", "rush_att", "rush_yd", "rush_td",
               "rec_tgt", "rec", "rec_yd", "rec_td", "fum_lost")
ACT_FIELDS = ("pass_att", "pass_yd", "pass_td", "pass_int", "rush_att", "rush_yd", "rush_td", "rec_tgt", "rec", "rec_yd",
              "rec_td", "fum_lost", "pts_ppr", "gp", "off_snp")


def load_sleeper(sleeper_dir, seasons):
    """{(season, week, sleeper id): {"team", "opp", "pos", "name", "role", "proj": {...} | None,
    "act": {...} | None}} from the repo's player-week table (2022-2025) and the
    raw weekly feeds for 2026 (proj_2026_<w>.json / stats_2026_<w>.json)."""
    out = {}
    fields_seen = {"proj": collections.Counter(), "act": collections.Counter()}
    with gzip.open(PLAYER_WEEKS, "rt", newline="") as fh:
        for row in csv.DictReader(fh):
            s = int(row["season"])
            if s not in seasons:
                continue
            key = (s, int(row["week"]), row["player_id"])
            proj = {k: float(row[f"proj_{k}"]) for k in PROJ_FIELDS if row.get(f"proj_{k}") not in (None, "")} if row["has_proj"] == "1" else None
            act = {k: float(row[f"act_{k}"]) for k in ACT_FIELDS if row.get(f"act_{k}") not in (None, "")} if row["has_act"] == "1" else None
            out[key] = {"team": row["team"], "opp": row["opp"], "pos": row["pos"], "name": row["name"], "role": row["role"],
                        "proj": proj, "act": act, "source": "research/data/player_weeks_2022_2025.csv.gz (Sleeper, fetched after the season)"}
            if proj:
                fields_seen["proj"].update(proj.keys())
            if act:
                fields_seen["act"].update(act.keys())
    if sleeper_dir and 2026 in seasons:
        for w in range(1, 19):
            pp = os.path.join(sleeper_dir, f"proj_2026_{w}.json")
            sp = os.path.join(sleeper_dir, f"stats_2026_{w}.json")
            if not os.path.exists(pp):
                continue
            with open(pp, encoding="utf-8") as fh:
                proj_rows = json.load(fh)
            stats_rows = []
            if os.path.exists(sp):
                with open(sp, encoding="utf-8") as fh:
                    stats_rows = json.load(fh)
            by_pid = {}
            for r in proj_rows:
                pl = r.get("player") or {}
                st = r.get("stats") or {}
                pid = str(r.get("player_id") or "")
                if not pid or (pl.get("position") or "") not in P.POS:
                    continue
                proj = {k: float(st[k]) for k in PROJ_FIELDS if st.get(k) is not None}
                if not proj:
                    continue
                fields_seen["proj"].update(proj.keys())
                by_pid[pid] = {"team": r.get("team"), "opp": r.get("opponent"), "pos": pl.get("position"),
                               "name": f"{pl.get('first_name', '')} {pl.get('last_name', '')}".strip(), "role": "",
                               "proj": proj, "act": None, "updated_at": r.get("updated_at"),
                               "source": f"api.sleeper.com projections 2026 week {w}, fetched 2026-10-23 (post-week snapshot)"}
            for r in stats_rows:
                pid = str(r.get("player_id") or "")
                st = r.get("stats") or {}
                pl = r.get("player") or {}
                act = {k: float(st[k]) for k in ACT_FIELDS if st.get(k) is not None}
                if not act:
                    continue
                fields_seen["act"].update(act.keys())
                rec = by_pid.get(pid)
                if rec is None:
                    if (pl.get("position") or "") not in P.POS:
                        continue
                    rec = by_pid[pid] = {"team": r.get("team"), "opp": r.get("opponent"), "pos": pl.get("position"),
                                         "name": f"{pl.get('first_name', '')} {pl.get('last_name', '')}".strip(), "role": "",
                                         "proj": None, "act": None, "source": f"api.sleeper.com stats 2026 week {w}"}
                rec["act"] = act
            # QB1 by projected pass attempts, hist_data's rule
            best = {}
            for pid, rec in by_pid.items():
                if rec["pos"] == "QB" and rec["proj"]:
                    att = rec["proj"].get("pass_att") or 0.0
                    if att > best.get(rec["team"], (0.0, None))[0]:
                        best[rec["team"]] = (att, pid)
            for team, (att, pid) in best.items():
                by_pid[pid]["role"] = "QB1"
            for pid, rec in by_pid.items():
                out[(2026, w, pid)] = rec
    return out, fields_seen


# ---- the dataset ----------------------------------------------------------------
def _col(name, meaning, source, kind):
    return {"name": name, "meaning": meaning, "source": source, "kind": kind}


PBP = "nflverse pbp"
NGS = "nflverse participation (NGS post-snap charting, 2021-2025)"
FTN = "FTN Data via nflverse (2022-2026)"
SLP = "Sleeper weekly API (post-week snapshot; see report)"
PROF = "nfl_matchup_profiles as of (season, week): prior weeks of the season + prior seasons at DECAY weights"


def schema():
    """The dataset's columns, in order, with meaning / source / kind."""
    cols = [
        _col("season", "season", PBP, "key"), _col("week", "week", PBP, "key"),
        _col("game_id", "nflverse game id", PBP, "key"), _col("date", "game date", PBP, "key"),
        _col("team", "player's team (nflverse code; LA = Rams)", PBP, "key"), _col("opp", "opponent", PBP, "key"),
        _col("home", "1 when the player's team is home", PBP, "observed"),
        _col("gsis", "nflverse gsis id", PBP, "key"), _col("sleeper_id", "Sleeper id (DynastyProcess crosswalk)", "DynastyProcess", "key"),
        _col("name", "display name", "nflverse players.csv", "key"), _col("norm_name", "kalshi_nfl._norm of the name", "derived", "key"),
        _col("pos", "QB/RB/WR/TE (FB -> RB)", "nflverse players.csv", "key"),
        _col("played", "1 when the player had a snap, target, carry or pass attempt in pbp/participation", PBP, "observed"),
        _col("in_sleeper", "1 when Sleeper lists a projection for this player-week", SLP, "observed"),
        _col("sl_role", "Sleeper-projection role (QB1/RB1/WR1...; 2026: QB1 only), from the Tuesday snapshot", SLP, "derived"),
    ]
    for k in PROJ_FIELDS:
        cols.append(_col(f"sl_proj_{k}", f"Sleeper projected {k}", SLP, "observed (not provably pre-kickoff)"))
    cols.append(_col("sl_proj_ppr", "PPR points of the Sleeper line under the owner's convention", "derived from Sleeper", "derived"))
    for k in ACT_FIELDS:
        cols.append(_col(f"sl_act_{k}", f"Sleeper's box score {k}", SLP, "observed"))
    acts = [("act_tgt", "targets"), ("act_rec", "receptions"), ("act_rec_yds", "receiving yards"), ("act_rec_td", "receiving TD"),
            ("act_air", "air yards on targets"), ("act_car", "carries (designed runs + scrambles)"), ("act_rush_yds", "rushing yards"),
            ("act_rush_td", "rushing TD"), ("act_pass_att", "pass attempts (no sacks)"), ("act_pass_cmp", "completions"),
            ("act_pass_yds", "passing yards"), ("act_pass_td", "passing TD"), ("act_int", "interceptions thrown"),
            ("act_fuml", "fumbles lost"), ("act_rz_tgt", "red-zone targets"), ("act_rz_car", "red-zone carries"),
            ("act_std", "standard fantasy points"), ("act_half", "half-PPR points"), ("act_ppr", "PPR points"),
            ("act_snaps", "offensive snaps on the field"), ("act_db_snaps", "dropbacks on the field"),
            ("act_snap_share", "snaps / team snaps"),
            ("team_snaps", "team offensive snaps"), ("team_db", "team dropbacks"), ("team_tgt", "team targets"), ("team_car", "team carries")]
    for n, m in acts:
        src = PBP if not n.endswith(("snaps", "snap_share")) else NGS + " (2026: nflverse snap_counts, PFR, by name)"
        cols.append(_col(n, m, src, "observed"))
    cols.append(_col("snaps_source", "ngs | pfr | none", "derived", "key"))
    # player profile
    p = [("p_games", "games in the profile (weighted)", "observed"), ("p_games_cur", "games this season before the cutoff", "observed"),
         ("p_snap_share", "snap share", "observed"), ("p_tgt_share", "target share", "observed"), ("p_air_share", "air-yard share", "observed"),
         ("p_car_share", "carry share", "observed"), ("p_rz_tgt_share", "red-zone target share", "observed"),
         ("p_rz_car_share", "red-zone carry share", "observed"), ("p_tgt_pg", "targets per game", "observed"), ("p_car_pg", "carries per game", "observed"),
         ("p_tgt_per_db", "targets per on-field dropback, numerator and denominator from the same charted plays, team-scoped", "proxy (targets per route run)"),
         ("p_tgt_per_db_man", "... vs man coverage (same plays, team-scoped)", "proxy"), ("p_tgt_per_db_zone", "... vs zone", "proxy"),
         ("p_tgt_per_db_n", "on-field dropbacks (denominator, weighted)", "observed"),
         ("p_tgt_per_db_n_current", "on-field dropbacks from THIS season (0 on every 2026 row: no participation file)", "observed"),
         ("p_tgt_per_db_proxy", "targets / (team dropbacks x PFR offensive snap share), this season's games for this team as of the cutoff -- the one same-season per-dropback rate 2026 has; unshrunk: gate on p_tgt_per_db_proxy_n (>= 20 removes every value above 1) or shrink toward the league rate; do not cap", "proxy"),
         ("p_tgt_per_db_proxy_n", "sum of team dropbacks x snap share behind the proxy", "derived"),
         ("p_catch", "catch rate", "observed"), ("p_catch_eb", "catch rate, shrunk", "modelled (EB)"),
         ("p_ypt", "yards per target", "observed"), ("p_ypt_eb", "yards per target, shrunk", "modelled (EB)"),
         ("p_epa_t", "EPA per target", "observed"), ("p_epa_t_eb", "EPA per target, shrunk", "modelled (EB)"),
         ("p_adot", "average depth of target", "observed"), ("p_yac_rec", "YAC per reception", "observed"),
         ("p_expl20", "20+ yard plays per target", "observed"), ("p_contested", "contested-target share (FTN)", "observed"),
         ("p_catchable", "catchable-target share (FTN)", "observed"), ("p_targets", "targets in the profile (weighted)", "observed"),
         ("p_depth_behind", "share of targets behind the line", "observed"), ("p_depth_short", "0-9 air yards", "observed"),
         ("p_depth_int", "10-19", "observed"), ("p_depth_deep", "20+", "observed"),
         ("p_loc_left", "share of targets to the left", "observed"), ("p_loc_mid", "middle", "observed"), ("p_loc_right", "right", "observed"),
         ("p_route_quick", "route-family share (charted routes on targets)", "observed"), ("p_route_screen", "", "observed"),
         ("p_route_int", "", "observed"), ("p_route_vert", "", "observed"),
         ("p_man_tgt", "targets vs man", "observed"), ("p_zone_tgt", "targets vs zone", "observed"),
         ("p_man_ypt", "yards per target vs man", "observed"), ("p_zone_ypt", "vs zone", "observed"),
         ("p_man_ypt_eb", "shrunk toward his overall", "modelled (EB)"), ("p_zone_ypt_eb", "", "modelled (EB)"),
         ("p_man_epa_t", "EPA per target vs man", "observed"), ("p_zone_epa_t", "vs zone", "observed"),
         ("p_man_catch", "catch rate vs man", "observed"), ("p_zone_catch", "vs zone", "observed"),
         ("p_sh_tgt_per_db", "targets per on-field dropback vs single-high (C1+C3), same plays, team-scoped", "proxy"),
         ("p_th_tgt_per_db", "vs two-high (C2+C4+C6+2-Man)", "proxy"),
         ("p_sh_epa_t", "EPA per target vs single-high (player-scoped: all his teams)", "observed"), ("p_th_epa_t", "vs two-high", "observed"),
         ("p_ypc", "yards per carry", "observed"), ("p_ypc_eb", "", "modelled (EB)"), ("p_rush_success", "rush success rate", "observed"),
         ("p_rush_epa", "EPA per carry", "observed"), ("p_stuff", "carries for <= 0", "observed"), ("p_expl10", "10+ yard carries", "observed"),
         ("p_carries", "carries in the profile", "observed"), ("p_ypc_light", "YPC vs light box (<= 6)", "observed"),
         ("p_ypc_heavy", "YPC vs heavy box (8+)", "observed"),
         ("p_fp_std_pg", "standard points per game", "observed"), ("p_fp_half_pg", "half-PPR", "observed"), ("p_fp_ppr_pg", "PPR", "observed"),
         ("p_rec_tgt_share", "last-4 target share", "observed"), ("p_rec_car_share", "last-4 carry share", "observed"),
         ("p_rec_snap_share", "last-4 snap share", "observed"), ("p_rec_tgt_pg", "last-4 targets per game", "observed"),
         ("p_rec_fp_ppr_pg", "last-4 PPR per game", "observed"), ("p_rec_games", "games in the recent window", "observed"),
         ("p_align_mid_proxy", "middle-of-field target share (alignment PROXY, not charted alignment)", "proxy")]
    for n, m, k in p:
        cols.append(_col(n, m or n, PROF, k))
    d = [("d_games", "defense games in the profile (weighted)", "observed"), ("d_games_cur", "this season", "observed"),
         ("d_cov_n", "charted dropbacks behind the coverage figures (2026: prior seasons only)", "observed"),
         ("d_nickel", "5-DB share", "observed"), ("d_dime", "6+-DB share", "observed"), ("d_base", "<= 4-DB share", "observed"),
         ("d_most_played_share", "share of the most-used DL-LB-DB grouping", "observed"), ("d_base_43", "1 = inferred 4-3 base, 0 = 3-4", "derived"),
         ("d_nickel_vs_11", "nickel share against 11 personnel", "observed"), ("d_dime_vs_11", "dime share against 11", "observed"),
         ("d_base_vs_12", "<= 4-DB share against 12 personnel", "observed"), ("d_dime_3rd_long", "dime share on 3rd & 7+", "observed"),
         ("d_light_box", "<= 6 in the box", "observed"), ("d_heavy_box", "8+ in the box", "observed"), ("d_box_mean", "mean box", "observed"),
         ("d_man", "man share of charted dropbacks", "observed"), ("d_man_eb", "shrunk", "modelled (EB)"), ("d_zone", "zone share", "observed"),
         ("d_single_high", "C1 + C3 share", "observed"), ("d_two_high", "C2 + C4 + C6 + 2-Man", "observed"), ("d_zero", "Cover 0", "observed"),
         ("d_fam_c0", "COVER_0 share", "observed"), ("d_fam_c1", "COVER_1", "observed"), ("d_fam_c2", "COVER_2", "observed"), ("d_fam_c3", "COVER_3", "observed"),
         ("d_fam_c4", "COVER_4", "observed"), ("d_fam_c6", "COVER_6", "observed"), ("d_fam_c9", "COVER_9", "observed"), ("d_fam_2man", "2_MAN", "observed"),
         ("d_fam_combo", "COMBO", "observed"),
         ("d_blitz", "5+ pass rushers per dropback", "observed"), ("d_blitz_eb", "", "modelled (EB)"), ("d_pressure", "pressure per dropback (NGS)", "observed"),
         ("d_sack", "sacks per dropback", "observed"), ("d_ttt", "time to throw allowed (s)", "observed"), ("d_rushers", "mean pass rushers", "observed"),
         ("d_epa_db", "EPA per dropback allowed", "observed"),
         ("d_ypt", "yards per target allowed", "observed"), ("d_epa_t", "EPA per target allowed", "observed"), ("d_catch", "catch rate allowed", "observed"),
         ("d_expl20", "20+ per target allowed", "observed"), ("d_targets", "targets faced (weighted)", "observed"),
         ("d_man_ypt", "YPT allowed in man", "observed"), ("d_zone_ypt", "in zone", "observed"), ("d_man_epa_t", "", "observed"), ("d_zone_epa_t", "", "observed"),
         ("d_sh_epa_t", "EPA/target allowed in single-high", "observed"), ("d_th_epa_t", "in two-high", "observed"),
         ("d_depth_behind_ypt", "YPT allowed by target depth", "observed"), ("d_depth_short_ypt", "", "observed"), ("d_depth_int_ypt", "", "observed"), ("d_depth_deep_ypt", "", "observed"),
         ("d_loc_left_ypt", "YPT allowed by pass location", "observed"), ("d_loc_mid_ypt", "", "observed"), ("d_loc_right_ypt", "", "observed"),
         ("d_route_quick_epa", "EPA/target allowed by route family", "observed"), ("d_route_screen_epa", "", "observed"), ("d_route_int_epa", "", "observed"), ("d_route_vert_epa", "", "observed"),
         ("d_run_ypc", "YPC allowed", "observed"), ("d_run_success", "rush success allowed", "observed"), ("d_run_epa", "EPA per carry allowed", "observed"),
         ("d_run_stuff", "stuff rate", "observed"), ("d_run_expl10", "10+ carries allowed", "observed"), ("d_run_ypc_light", "YPC allowed with a light box", "observed"),
         ("d_run_ypc_heavy", "with a heavy box", "observed")]
    for pos in ("WR", "TE", "RB"):
        d += [(f"d_{pos}_tgt_per_db", f"{pos} targets per dropback faced", "observed"), (f"d_{pos}_ypt", f"YPT allowed to {pos}", "observed"),
              (f"d_{pos}_ypt_eb", "", "modelled (EB)"), (f"d_{pos}_epa_t", f"EPA/target allowed to {pos}", "observed"), (f"d_{pos}_catch", "", "observed"),
              (f"d_{pos}_man_ypt", f"YPT allowed to {pos} in man", "observed"), (f"d_{pos}_zone_ypt", "in zone", "observed"),
              (f"d_{pos}_tgt_pg", f"{pos} targets allowed per game", "observed"), (f"d_{pos}_rec_yds_pg", f"{pos} receiving yards allowed per game", "observed"),
              (f"d_{pos}_std_pg", f"{pos} standard points allowed per game", "observed"), (f"d_{pos}_half_pg", "half-PPR", "observed"), (f"d_{pos}_ppr_pg", "PPR", "observed"),
              (f"d_{pos}_ppr_pg_eb", "", "modelled (EB)"), (f"d_{pos}_adj_ppr", f"{pos} PPR allowed minus the offences' averages elsewhere", "derived (opponent-adjusted)"),
              (f"d_{pos}_adj_tgt", "", "derived (opponent-adjusted)"), (f"d_{pos}_adj_rec_yds", "", "derived (opponent-adjusted)"),
              (f"d_{pos}_rec_ppr_pg", f"last-4 {pos} PPR allowed per game", "observed")]
    d += [("d_RB_car_pg", "RB carries allowed per game", "observed"), ("d_RB_rush_yds_pg", "RB rushing yards allowed per game", "observed"),
          ("d_RB_adj_rush_yds", "", "derived (opponent-adjusted)"),
          ("d_QB_pass_yds_pg", "QB passing yards allowed per game", "observed"), ("d_QB_ppr_pg", "QB fantasy points allowed per game", "observed"),
          ("d_QB_adj_pass_yds", "", "derived (opponent-adjusted)"), ("d_QB_adj_ppr", "", "derived (opponent-adjusted)"),
          ("d_rec_games", "games in the defense's recent window", "observed")]
    for n, m, k in d:
        cols.append(_col(n, m or n, PROF, k))
    o = [("o_games", "offense games (weighted)", "observed"), ("o_pass_rate", "dropbacks per snap", "observed"), ("o_proe", "pass rate over nflfastR xpass", "observed"),
         ("o_neutral_pass_rate", "pass rate, |score diff| <= 7 in Q1-Q3", "observed"), ("o_early_pass_rate", "1st/2nd-down pass rate", "observed"),
         ("o_shotgun", "", "observed"), ("o_no_huddle", "", "observed"), ("o_play_action", "of dropbacks (FTN)", "observed"), ("o_screen", "of dropbacks (FTN)", "observed"),
         ("o_motion", "of snaps (FTN)", "observed"), ("o_rpo", "of snaps (FTN)", "observed"), ("o_rz_pass_rate", "", "observed"), ("o_plays_pg", "snaps per game", "observed"),
         ("o_p11", "11 personnel share", "observed"), ("o_p12", "", "observed"), ("o_p21", "", "observed"), ("o_p13", "", "observed"),
         ("o_tgt_WR", "target share to WR", "observed"), ("o_tgt_TE", "", "observed"), ("o_tgt_RB", "", "observed"), ("o_deep_rate", "20+ air yards per attempt", "observed"),
         ("o_pressure_allowed", "", "observed"), ("o_sack", "", "observed"), ("o_blitz_faced", "", "observed"), ("o_ttt", "", "observed")]
    for n, m, k in o:
        cols.append(_col(n, m or n, PROF, k))
    q = [("q_gsis", "the quarterback the q_ features describe: Sleeper's QB1 by projected pass attempts from the TUESDAY post-week snapshot (2023-25: player_weeks role; 2026: the fetch of 2026-10-23) -- not provably pre-kickoff", "derived (not provably pre-kickoff)"),
         ("q_source", "sleeper_qb1_tuesday | last_starter | none", "derived"),
         ("q_games", "", "observed"), ("q_dropbacks", "", "observed"), ("q_epa_db", "EPA per dropback", "observed"), ("q_epa_db_eb", "", "modelled (EB)"),
         ("q_comp", "", "observed"), ("q_ypa", "", "observed"), ("q_sack", "", "observed"), ("q_ttt", "", "observed"), ("q_deep_rate", "", "observed"),
         ("q_cpoe_short", "CPOE on 0-9 air yards", "observed"), ("q_cpoe_int", "10-19", "observed"), ("q_cpoe_deep", "20+", "observed"),
         ("q_epa_man", "EPA/dropback vs man", "observed"), ("q_epa_zone", "", "observed"), ("q_epa_press", "under pressure", "observed"), ("q_epa_nopress", "", "observed"),
         ("q_epa_blitz", "vs blitz", "observed"), ("q_epa_noblitz", "", "observed"), ("q_tgt_share_to_player", "share of this QB's targets to this player", "observed"),
         ("q_is_player", "1 when the row's player IS the quarterback", "derived")]
    for n, m, k in q:
        cols.append(_col(n, m or n, PROF, k))
    qp = [("qp_gsis", "the PRE-KICKOFF quarterback pick: the app's frozen pre-kickoff Sleeper capture when one dated before the game exists (research/data/feeds, 2026 weeks 1-2, Sunday/Monday games), else the team's leading passer in its last game as of the cutoff", "derived (pre-kickoff by construction)"),
          ("qp_source", "frozen_capture | last_starter | none", "derived"), ("qp_same_as_q", "1 when qp_gsis == q_gsis", "derived"),
          ("qp_games", "", "observed"), ("qp_dropbacks", "", "observed"), ("qp_epa_db", "EPA per dropback", "observed"), ("qp_epa_db_eb", "", "modelled (EB)"),
          ("qp_ttt", "", "observed"), ("qp_cpoe_short", "", "observed"), ("qp_epa_man", "", "observed"), ("qp_epa_zone", "", "observed"),
          ("qp_epa_press", "", "observed"), ("qp_epa_blitz", "", "observed"), ("qp_tgt_share_to_player", "share of this QB's targets to this player", "observed")]
    for n, m, k in qp:
        cols.append(_col(n, m or n, PROF, k))
    x = [("x_exp_tgt_per_db", "p_tgt_per_db_man x d_man + p_tgt_per_db_zone x d_zone (falls back to p_tgt_per_db); bounded in [0, 1] because every input is", "derived"),
         ("x_exp_ypt_mz", "p_man_ypt_eb x d_man + p_zone_ypt_eb x d_zone", "derived"),
         ("x_exp_tgt_per_db_shell", "p_sh_tgt_per_db x d_single_high + p_th_tgt_per_db x d_two_high (+ p_tgt_per_db x the rest)", "derived"),
         ("x_depth_ypt", "sum over depth of the player's target share x the defense's YPT allowed at that depth", "derived"),
         ("x_route_epa", "sum over route family of the player's share x the defense's EPA/target allowed", "derived"),
         ("x_pos_ypt_gap", "d_<pos>_ypt_eb minus the league YPT allowed to the position", "derived"),
         ("x_pos_adj_ppr", "d_<pos>_adj_ppr for the row's position", "derived"),
         ("x_box_ypc", "p_ypc_light x d_light_box + p_ypc_heavy x d_heavy_box + p_ypc x the rest", "derived")]
    for n, m, k in x:
        cols.append(_col(n, m, "derived from the profiles", k))
    return cols


def _f(x, nd=4):
    if x is None:
        return ""
    if isinstance(x, float):
        if math.isnan(x):
            return ""
        return f"{x:.{nd}f}".rstrip("0").rstrip(".") if nd else str(x)
    return str(x)


def load_frozen_qb(feeds_dir, gsis_by_sleeper):
    """{(season, week, team): (gsis, capture date)} from the app's frozen
    pre-kickoff Sleeper captures (research/data/feeds/proj_<season>_<w>.json,
    skill positions; the _def / _k files are not read): the QB with the most
    projected pass attempts per team, and the UTC date of the capture (the
    newest updated_at in the file). A capture is pre-kickoff for a game only
    when its date is before the game's date -- the week-1 capture of
    2026-09-12 is after Thursday's game and before Sunday's."""
    import datetime as dt
    out = {}
    if not feeds_dir or not os.path.isdir(feeds_dir):
        return out
    for name in sorted(os.listdir(feeds_dir)):
        m = re.match(r"^proj_(\d{4})_(\d{1,2})\.json$", name)
        if not m:
            continue
        season, week = int(m.group(1)), int(m.group(2))
        with open(os.path.join(feeds_dir, name), encoding="utf-8") as fh:
            rows = json.load(fh)
        stamp = max((r.get("updated_at") or 0) for r in rows) if rows else 0
        if not stamp:
            continue
        cap_date = dt.datetime.utcfromtimestamp(stamp / 1000).strftime("%Y-%m-%d")
        best = {}
        for r in rows:
            pl = r.get("player") or {}
            if (pl.get("position") or "") != "QB":
                continue
            att = float((r.get("stats") or {}).get("pass_att") or 0.0)
            team = r.get("team") or ""
            team = {"LAR": "LA"}.get(team, team)
            g = gsis_by_sleeper.get(str(r.get("player_id") or ""))
            if g and team and att > best.get(team, (0.0, None))[0]:
                best[team] = (att, g)
        for team, (att, g) in best.items():
            out[(season, week, team)] = (g, cap_date)
    return out


def last_starter(S, team):
    """The team's leading passer in its most recent game inside the snapshot --
    strictly before the cutoff, so pre-kickoff by construction."""
    best = None
    for w, T in S.tallies:
        for side, r in T.team_games.get(team) or []:
            if side != "off":
                continue
            key = (r["season"], r["week"])
            if best is None or key > best[0]:
                best = (key, r)
    if best is None:
        return None
    cand = max(best[1]["players"].items(), key=lambda kv: kv[1][P.G_PATT], default=None)
    return cand[0] if cand and cand[1][P.G_PATT] > 0 else None


def week_rows(S, st, season, week, games, pos_map, ids, sl_by_team, snaps_pfr, frozen_qb, meta):
    """Every row of one week from a Snapshot FROZEN before any of the week's
    games: the caller adds the games to the tallies only after this returns,
    so Thursday's game never informs Sunday's rows (the review's F3: the first
    version fed each game into the live tallies, and the opponent-adjusted
    columns of later games in the week read earlier ones). A Sleeper-only row
    (projected, no snap) is dropped when the same player has a played row in
    the week on another team (N7). Returns (rows, counts)."""
    L = S.league()
    qb_src = meta.setdefault("qb_source", collections.Counter())
    qp_src = meta.setdefault("qp_source", collections.Counter())
    meta.setdefault("snaps_source", collections.Counter())
    dcache, ocache, qcache = {}, {}, {}
    rows, deferred, played = [], [], set()

    def qprof(pid):
        if pid not in qcache:
            qcache[pid] = S.qb(pid) if pid else None
        return qcache[pid]
    for gi, a, b in games:
        g = st["games"][gi]
        tmp = P.Tallies(season)
        recs = P.add_game(tmp, st, gi, a, b, pos_map)
        for team, r in recs.items():
            opp = r["dfn"]
            if opp not in dcache:
                dcache[opp] = S.defense(opp)
            if team not in ocache:
                ocache[team] = S.offense(team)
            d, o = dcache[opp], ocache[team]
            # q_: Sleeper's QB1 from the Tuesday snapshot (not provably pre-kickoff)
            q_g, q_src = None, "none"
            for gg, rec in sl_by_team.get(team, {}).items():
                if rec.get("role") == "QB1":
                    q_g, q_src = gg, "sleeper_qb1_tuesday"
            starter = last_starter(S, team)
            if q_g is None and starter:
                q_g, q_src = starter, "last_starter"
            qb_src[q_src] += 1
            # qp_: pre-kickoff by construction -- a frozen capture dated before the game, else the last starter
            qp_g, qp_s = None, "none"
            fz = frozen_qb.get((season, week, team))
            if fz and fz[1] < (g.get("date") or ""):
                qp_g, qp_s = fz[0], "frozen_capture"
            elif starter:
                qp_g, qp_s = starter, "last_starter"
            qp_src[qp_s] += 1
            universe, sleeper_only = set(), set()
            for pid, line in r["players"].items():
                if P._pos(pos_map, pid) in P.POS and (line[P.G_TGT] + line[P.G_CAR] + line[P.G_PATT] + line[P.G_SNAPS] > 0):
                    universe.add(pid)
            for gg, rec in sl_by_team.get(team, {}).items():
                if rec.get("proj") and P._pos(pos_map, gg) in P.POS and gg not in universe:
                    sleeper_only.add(gg)
            for pid in universe | sleeper_only:
                row = _row(S, L, season, week, g, team, opp, pid, r, d, o, qprof(q_g), q_g, q_src, qprof(qp_g), qp_g, qp_s,
                           pos_map, ids, sl_by_team.get(team, {}).get(pid), snaps_pfr, meta)
                if pid in universe:
                    rows.append(row)
                    played.add(pid)
                else:
                    deferred.append((pid, row))
    kept = [row for pid, row in deferred if pid not in played]
    rows.extend(kept)
    if S.contains(season, week):
        raise RuntimeError(f"snapshot for {season} week {week} holds the week's own games")
    return rows, {"played": len(rows) - len(kept), "sleeper_only": len(kept), "dropped_duplicates": len(deferred) - len(kept)}


def build_dataset(stores, pos_map, ids, sleeper, out_path):
    cols = [c["name"] for c in schema()]
    idx = D.ids_index(ids)
    gsis_by_sleeper = idx["sleeper"]
    meta = {"rows": 0, "by_season": {}, "sleeper": {"rows": 0, "mapped_to_gsis": 0, "unmapped_examples": []},
            "qb_source": collections.Counter(), "qp_source": collections.Counter(), "snaps_source": collections.Counter(),
            "sleeper_only_dropped_as_duplicates": 0, "started": time.time()}
    snaps_pfr = {}
    raw_dir = os.environ.get("NFL_MATCHUP_RAW")
    for season in DATASET_SEASONS:
        path = os.path.join(raw_dir or "", f"snap_counts_{season}.csv")
        if raw_dir and os.path.exists(path):
            snaps_pfr.update(D.read_snap_counts(path))
    meta["snap_count_rows"] = len(snaps_pfr)
    frozen_qb = load_frozen_qb(os.path.join(HERE, "data", "feeds"), gsis_by_sleeper)
    meta["frozen_qb_captures"] = sorted({f"{k[0]} w{k[1]} ({v[1]})" for k, v in frozen_qb.items()})
    fh = gzip.open(out_path, "wt", newline="")
    w = csv.writer(fh)
    w.writerow(cols)
    for season in DATASET_SEASONS:
        st = stores.get(season)
        if not st:
            log(f"no store for {season}")
            continue
        priors = [P.season_tallies(stores[s], pos_map) for s in (season - 2, season - 1) if s in stores]
        current = P.Tallies(season)
        by_week = collections.defaultdict(list)
        for gi, a, b in P.game_ranges(st):
            by_week[st["games"][gi]["week"]].append((gi, a, b))
        max_week = TEST_MAX_WEEK.get(season, 18)
        n_rows = 0
        t0 = time.time()
        for week in sorted(by_week):
            if week > max_week:
                break
            # the snapshot is frozen here; the week's games join `current` only after its rows are written
            S = P.Snapshot(season, week, current, priors)
            sl_week = {k[2]: rec for k, rec in sleeper.items() if k[0] == season and k[1] == week}
            sl_by_team = collections.defaultdict(dict)
            for spid, rec in sl_week.items():
                g = gsis_by_sleeper.get(spid)
                meta["sleeper"]["rows"] += 1
                if g is None:
                    if len(meta["sleeper"]["unmapped_examples"]) < 12 and (rec.get("proj") or {}).get("rec_tgt", 0) > 2:
                        meta["sleeper"]["unmapped_examples"].append(f"{season} w{week} {rec['name']} ({rec['pos']}, {rec['team']})")
                    continue
                meta["sleeper"]["mapped_to_gsis"] += 1
                team = rec.get("team") or ""
                team = {"LAR": "LA"}.get(team, team)
                sl_by_team[team][g] = rec
            rows, counts = week_rows(S, st, season, week, by_week[week], pos_map, ids, sl_by_team, snaps_pfr, frozen_qb, meta)
            meta["sleeper_only_dropped_as_duplicates"] += counts["dropped_duplicates"]
            for row in rows:
                w.writerow([_f(row.get(c)) for c in cols])
            n_rows += len(rows)
            for gi, a, b in by_week[week]:
                P.add_game(current, st, gi, a, b, pos_map)
            log(f"[dataset] {season} week {week}: {n_rows} rows so far, {time.time() - t0:.0f}s")
        meta["by_season"][season] = {"rows": n_rows, "weeks": sorted(k for k in by_week if k <= max_week), "games": sum(len(v) for k, v in by_week.items() if k <= max_week)}
        meta["rows"] += n_rows
    fh.close()
    meta["qb_source"] = dict(meta["qb_source"])
    meta["qp_source"] = dict(meta["qp_source"])
    meta["snaps_source"] = dict(meta["snaps_source"])
    meta["seconds"] = round(time.time() - meta.pop("started"), 1)
    meta["bytes"] = os.path.getsize(out_path)
    meta["sha256"] = _sha(out_path)
    return meta


def _proxy_rate(S, pid, team, info, snaps_pfr):
    """targets / (team dropbacks x PFR offensive snap share) over the player's
    games for `team` in the CURRENT season of the snapshot (weight 1.0 tallies),
    games without a PFR share skipped on both sides. (value, n) or (None, 0).
    UNSHRUNK: a reader gates on n (>= 20 removes every value above 1 in the
    2023-2026 dataset; 11 rows exceed 1, all with n < 2) or shrinks toward the
    league rate; never cap it at 1, which hides the noise instead of removing it."""
    num = den = 0.0
    norm = info.get("norm") or ""
    for w, T in S.tallies:
        if w != 1.0:
            continue
        for r in T.player_games.get(pid) or []:
            if r["off"] != team or r["season"] != S.season:
                continue
            hit = snaps_pfr.get((r["id"], norm, team))
            if not hit or not hit[1]:
                continue
            line = r["players"].get(pid)
            num += line[P.G_TGT] if line else 0.0
            den += r["team"][P.T_DB] * hit[1]
    return ((num / den) if den else None), round(den, 2)


def _row(S, L, season, week, g, team, opp, pid, r, d, o, q, q_g, q_src, qp, qp_g, qp_s, pos_map, ids, sl, snaps_pfr, meta):
    line = r["players"].get(pid) or [0.0] * P.G_W
    tm = r["team"]
    info = ids.get(pid) or {}
    pos = P._pos(pos_map, pid)
    row = {"season": season, "week": week, "game_id": g["id"], "date": g["date"], "team": team, "opp": opp,
           "home": 1 if g["home"] == team else 0, "gsis": D.gsis_str(pid), "sleeper_id": info.get("sleeper") or "",
           "name": info.get("name") or (sl or {}).get("name") or "", "norm_name": info.get("norm") or "", "pos": pos,
           "played": 1 if (line[P.G_TGT] + line[P.G_CAR] + line[P.G_PATT] + line[P.G_SNAPS]) > 0 else 0,
           "in_sleeper": 1 if sl and sl.get("proj") else 0, "sl_role": (sl or {}).get("role") or ""}
    if sl and sl.get("proj"):
        for k in PROJ_FIELDS:
            row[f"sl_proj_{k}"] = sl["proj"].get(k)
        pj = sl["proj"]
        row["sl_proj_ppr"] = P.fantasy_points({"rec": pj.get("rec"), "rec_yds": pj.get("rec_yd"), "rec_td": pj.get("rec_td"),
                                               "rush_yds": pj.get("rush_yd"), "rush_td": pj.get("rush_td"), "pass_yds": pj.get("pass_yd"),
                                               "pass_td": pj.get("pass_td"), "intc": pj.get("pass_int"), "fuml": pj.get("fum_lost")}, "ppr")
    if sl and sl.get("act"):
        for k in ACT_FIELDS:
            row[f"sl_act_{k}"] = sl["act"].get(k)
    row.update({"act_tgt": line[P.G_TGT], "act_rec": line[P.G_REC], "act_rec_yds": line[P.G_RECY], "act_rec_td": line[P.G_RECTD],
                "act_air": line[P.G_AIR], "act_car": line[P.G_CAR], "act_rush_yds": line[P.G_RUSHY], "act_rush_td": line[P.G_RUSHTD],
                "act_pass_att": line[P.G_PATT], "act_pass_cmp": line[P.G_PCMP], "act_pass_yds": line[P.G_PASSY], "act_pass_td": line[P.G_PASSTD],
                "act_int": line[P.G_INT], "act_fuml": line[P.G_FUML], "act_rz_tgt": line[P.G_RZT], "act_rz_car": line[P.G_RZC],
                "act_std": line[P.G_STD], "act_half": line[P.G_HALF], "act_ppr": line[P.G_PPR],
                "team_snaps": tm[P.T_SNAPS], "team_db": tm[P.T_DB], "team_tgt": tm[P.T_TGT], "team_car": tm[P.T_CAR]})
    if line[P.G_SNAPS] > 0:
        row.update({"act_snaps": line[P.G_SNAPS], "act_db_snaps": line[P.G_DB],
                    "act_snap_share": line[P.G_SNAPS] / tm[P.T_SNAPS] if tm[P.T_SNAPS] else None, "snaps_source": "ngs"})
    else:
        hit = snaps_pfr.get((g["id"], info.get("norm") or "", team))
        if hit:
            row.update({"act_snaps": hit[0], "act_snap_share": hit[1], "snaps_source": "pfr"})
        else:
            row["snaps_source"] = "none"
    meta["snaps_source"][row["snaps_source"]] += 1
    # ---- player profile
    p = S.player(pid, team=team)
    u, rc, ru = p["usage"], p["receiving"], p["rushing"]
    pc = rc["per_coverage"]

    def fam_epa(fams):
        t = sum(pc[c]["targets"] for c in fams if c in pc)
        e = sum(pc[c]["epa_t"]["v"] * pc[c]["targets"] for c in fams if c in pc and pc[c]["epa_t"]["v"] is not None)
        return (e / t) if t else None
    SH, TH = ("COVER_1", "COVER_3"), ("COVER_2", "COVER_4", "COVER_6", "2_MAN")
    proxy_v, proxy_n = _proxy_rate(S, pid, team, info, snaps_pfr)
    row.update({"p_games": p["games"]["weighted"], "p_games_cur": p["games"]["current"],
                "p_snap_share": v(u["snap_share"]), "p_tgt_share": v(u["target_share"]), "p_air_share": v(u["air_share"]),
                "p_car_share": v(u["carry_share"]), "p_rz_tgt_share": v(u["rz_target_share"]), "p_rz_car_share": v(u["rz_carry_share"]),
                "p_tgt_pg": v(u["targets_pg"]), "p_car_pg": v(u["carries_pg"]), "p_tgt_per_db": v(u["tgt_per_db"]),
                "p_tgt_per_db_man": v(u["tgt_per_db_vs_man"]), "p_tgt_per_db_zone": v(u["tgt_per_db_vs_zone"]), "p_tgt_per_db_n": u["tgt_per_db"]["n"],
                "p_tgt_per_db_n_current": u["tgt_per_db"]["n_current"], "p_tgt_per_db_proxy": proxy_v, "p_tgt_per_db_proxy_n": proxy_n,
                "p_catch": v(rc["catch"]), "p_catch_eb": eb(rc["catch"]), "p_ypt": v(rc["ypt"]), "p_ypt_eb": eb(rc["ypt"]),
                "p_epa_t": v(rc["epa_t"]), "p_epa_t_eb": eb(rc["epa_t"]), "p_adot": v(rc["adot"]), "p_yac_rec": v(rc["yac_rec"]),
                "p_expl20": v(rc["expl20"]), "p_contested": v(rc["contested"]), "p_catchable": v(rc["catchable"]), "p_targets": rc["targets"],
                "p_depth_behind": v(rc["depth_mix"]["behind"]), "p_depth_short": v(rc["depth_mix"]["short"]),
                "p_depth_int": v(rc["depth_mix"]["intermediate"]), "p_depth_deep": v(rc["depth_mix"]["deep"]),
                "p_loc_left": v(rc["location_mix"]["left"]), "p_loc_mid": v(rc["location_mix"]["middle"]), "p_loc_right": v(rc["location_mix"]["right"]),
                "p_route_quick": v(rc["route_mix"]["quick"]), "p_route_screen": v(rc["route_mix"]["screen"]),
                "p_route_int": v(rc["route_mix"]["intermediate"]), "p_route_vert": v(rc["route_mix"]["vertical"]),
                "p_man_tgt": rc["vs_man"]["targets"], "p_zone_tgt": rc["vs_zone"]["targets"],
                "p_man_ypt": v(rc["vs_man"]["ypt"]), "p_zone_ypt": v(rc["vs_zone"]["ypt"]),
                "p_man_ypt_eb": eb(rc["vs_man"]["ypt"]), "p_zone_ypt_eb": eb(rc["vs_zone"]["ypt"]),
                "p_man_epa_t": v(rc["vs_man"]["epa_t"]), "p_zone_epa_t": v(rc["vs_zone"]["epa_t"]),
                "p_man_catch": v(rc["vs_man"]["catch"]), "p_zone_catch": v(rc["vs_zone"]["catch"]),
                "p_sh_tgt_per_db": v(u["tgt_per_db_by_shell"]["single_high"]), "p_th_tgt_per_db": v(u["tgt_per_db_by_shell"]["two_high"]),
                "p_sh_epa_t": fam_epa(SH), "p_th_epa_t": fam_epa(TH),
                "p_ypc": v(ru["ypc"]), "p_ypc_eb": eb(ru["ypc"]), "p_rush_success": v(ru["success"]), "p_rush_epa": v(ru["epa_c"]),
                "p_stuff": v(ru["stuff"]), "p_expl10": v(ru["expl10"]), "p_carries": ru["carries"],
                "p_ypc_light": v(ru["by_box"]["light"]["ypc"]), "p_ypc_heavy": v(ru["by_box"]["heavy"]["ypc"]),
                "p_fp_std_pg": v(p["fantasy"]["std"]), "p_fp_half_pg": v(p["fantasy"]["half"]), "p_fp_ppr_pg": v(p["fantasy"]["ppr"]),
                "p_rec_tgt_share": v(p["recent"]["target_share"]), "p_rec_car_share": v(p["recent"]["carry_share"]),
                "p_rec_snap_share": v(p["recent"]["snap_share"]), "p_rec_tgt_pg": v(p["recent"]["targets_pg"]),
                "p_rec_fp_ppr_pg": v(p["recent"]["fantasy"]["ppr"]), "p_rec_games": p["recent"]["games"],
                "p_align_mid_proxy": v(p["alignment_proxy"]["value"])})
    # ---- defense
    c, pe, al, fa, pr, bx = d["coverage"], d["personnel"], d["allowed"], d["fantasy_allowed"], d["pressure"], d["box"]
    row.update({"d_games": d["games"]["weighted"], "d_games_cur": d["games"]["current"], "d_cov_n": c["dropbacks_charted"],
                "d_nickel": v(pe["nickel"]), "d_dime": v(pe["dime"]), "d_base": v(pe["base"]),
                "d_most_played_share": v(pe["most_played_share"]) if pe["most_played_share"] else None,
                "d_base_43": None if pe["base_front"] is None else (1 if pe["base_front"] == "4-3" else 0),
                "d_nickel_vs_11": v((pe["by_off_personnel"].get("11") or {}).get("nickel")), "d_dime_vs_11": v((pe["by_off_personnel"].get("11") or {}).get("dime")),
                "d_base_vs_12": v((pe["by_off_personnel"].get("12") or {}).get("base")), "d_dime_3rd_long": v((pe["by_down_distance"].get("3rd_long") or {}).get("dime")),
                "d_light_box": v(bx["light"]), "d_heavy_box": v(bx["heavy"]), "d_box_mean": v(bx["mean"]),
                "d_man": v(c["man"]), "d_man_eb": eb(c["man"]), "d_zone": v(c["zone"]), "d_single_high": v(c["single_high"]), "d_two_high": v(c["two_high"]),
                "d_zero": v(c["zero"]), "d_fam_c0": v(c["families"]["COVER_0"]), "d_fam_c1": v(c["families"]["COVER_1"]), "d_fam_c2": v(c["families"]["COVER_2"]),
                "d_fam_c3": v(c["families"]["COVER_3"]), "d_fam_c4": v(c["families"]["COVER_4"]), "d_fam_c6": v(c["families"]["COVER_6"]),
                "d_fam_c9": v(c["families"]["COVER_9"]), "d_fam_2man": v(c["families"]["2_MAN"]), "d_fam_combo": v(c["families"]["COMBO"]),
                "d_blitz": v(pr["blitz"]), "d_blitz_eb": eb(pr["blitz"]), "d_pressure": v(pr["pressure"]), "d_sack": v(pr["sack"]), "d_ttt": v(pr["ttt"]),
                "d_rushers": v(pr["rushers"]), "d_epa_db": v(pr["epa_db"]),
                "d_ypt": v(al["all"]["ypt"]), "d_epa_t": v(al["all"]["epa_t"]), "d_catch": v(al["all"]["catch"]), "d_expl20": v(al["all"]["expl20"]),
                "d_targets": al["all"]["targets"], "d_man_ypt": v(al["per_mz"]["man"]["ypt"]), "d_zone_ypt": v(al["per_mz"]["zone"]["ypt"]),
                "d_man_epa_t": v(al["per_mz"]["man"]["epa_t"]), "d_zone_epa_t": v(al["per_mz"]["zone"]["epa_t"]),
                "d_depth_behind_ypt": v(al["per_depth"]["behind"]["ypt"]), "d_depth_short_ypt": v(al["per_depth"]["short"]["ypt"]),
                "d_depth_int_ypt": v(al["per_depth"]["intermediate"]["ypt"]), "d_depth_deep_ypt": v(al["per_depth"]["deep"]["ypt"]),
                "d_loc_left_ypt": v(al["per_location"]["left"]["ypt"]), "d_loc_mid_ypt": v(al["per_location"]["middle"]["ypt"]),
                "d_loc_right_ypt": v(al["per_location"]["right"]["ypt"]),
                "d_route_quick_epa": v(al["per_route"]["quick"]["epa_t"]), "d_route_screen_epa": v(al["per_route"]["screen"]["epa_t"]),
                "d_route_int_epa": v(al["per_route"]["intermediate"]["epa_t"]), "d_route_vert_epa": v(al["per_route"]["vertical"]["epa_t"]),
                "d_run_ypc": v(d["run"]["ypc"]), "d_run_success": v(d["run"]["success"]), "d_run_epa": v(d["run"]["epa_c"]),
                "d_run_stuff": v(d["run"]["stuff"]), "d_run_expl10": v(d["run"]["expl10"]),
                "d_run_ypc_light": v(bx["vs_run"]["light"]["ypc"]), "d_run_ypc_heavy": v(bx["vs_run"]["heavy"]["ypc"]),
                "d_rec_games": d["recent"]["games"]})

    def fam_epa_def(fams):
        t = sum(al["per_coverage"][c]["targets"] for c in fams)
        e = sum(al["per_coverage"][c]["epa_t"]["v"] * al["per_coverage"][c]["targets"] for c in fams if al["per_coverage"][c]["epa_t"]["v"] is not None)
        return (e / t) if t else None
    row["d_sh_epa_t"], row["d_th_epa_t"] = fam_epa_def(SH), fam_epa_def(TH)
    for ps in ("WR", "TE", "RB"):
        blk, fp = al["per_pos"][ps], fa[ps]
        adj = fp.get("opp_adjusted") or {}
        row.update({f"d_{ps}_tgt_per_db": v(blk["tgt_per_db"]), f"d_{ps}_ypt": v(blk["ypt"]), f"d_{ps}_ypt_eb": eb(blk["ypt"]),
                    f"d_{ps}_epa_t": v(blk["epa_t"]), f"d_{ps}_catch": v(blk["catch"]),
                    f"d_{ps}_man_ypt": v(blk["vs_man"]["ypt"]), f"d_{ps}_zone_ypt": v(blk["vs_zone"]["ypt"]),
                    f"d_{ps}_tgt_pg": v(fp["per_game"]["targets"]), f"d_{ps}_rec_yds_pg": v(fp["per_game"]["rec_yds"]),
                    f"d_{ps}_std_pg": v(fp["per_game"]["std"]), f"d_{ps}_half_pg": v(fp["per_game"]["half"]), f"d_{ps}_ppr_pg": v(fp["per_game"]["ppr"]),
                    f"d_{ps}_ppr_pg_eb": eb(fp["per_game"]["ppr"]), f"d_{ps}_adj_ppr": v(adj.get("ppr")), f"d_{ps}_adj_tgt": v(adj.get("tgt")),
                    f"d_{ps}_adj_rec_yds": v(adj.get("rec_yds")), f"d_{ps}_rec_ppr_pg": v(d["recent"]["fantasy_allowed"][ps]["per_game"]["ppr"])})
    rb, qb = fa["RB"], fa["QB"]
    row.update({"d_RB_car_pg": v(rb["per_game"]["carries"]), "d_RB_rush_yds_pg": v(rb["per_game"]["rush_yds"]),
                "d_RB_adj_rush_yds": v((rb.get("opp_adjusted") or {}).get("rush_yds")),
                "d_QB_pass_yds_pg": v(qb["per_game"]["pass_yds"]), "d_QB_ppr_pg": v(qb["per_game"]["ppr"]),
                "d_QB_adj_pass_yds": v((qb.get("opp_adjusted") or {}).get("pass_yds")), "d_QB_adj_ppr": v((qb.get("opp_adjusted") or {}).get("ppr"))})
    # ---- offense
    row.update({"o_games": o["games"]["weighted"], "o_pass_rate": v(o["pass_rate"]), "o_proe": v(o["proe"]), "o_neutral_pass_rate": v(o["neutral_pass_rate"]),
                "o_early_pass_rate": v(o["early_down_pass_rate"]), "o_shotgun": v(o["shotgun"]), "o_no_huddle": v(o["no_huddle"]),
                "o_play_action": v(o["play_action"]), "o_screen": v(o["screen"]), "o_motion": v(o["motion"]), "o_rpo": v(o["rpo"]),
                "o_rz_pass_rate": v(o["red_zone_pass_rate"]), "o_plays_pg": v(o["plays_per_game"]),
                "o_p11": (o["personnel"].get("11") or {}).get("share"), "o_p12": (o["personnel"].get("12") or {}).get("share"),
                "o_p21": (o["personnel"].get("21") or {}).get("share"), "o_p13": (o["personnel"].get("13") or {}).get("share"),
                "o_tgt_WR": v(o["targets"]["by_pos"]["WR"]), "o_tgt_TE": v(o["targets"]["by_pos"]["TE"]), "o_tgt_RB": v(o["targets"]["by_pos"]["RB"]),
                "o_deep_rate": v(o["targets"]["deep_rate"]), "o_pressure_allowed": v(o["protection"]["pressure_allowed"]),
                "o_sack": v(o["protection"]["sack"]), "o_blitz_faced": v(o["protection"]["blitz_faced"]), "o_ttt": v(o["protection"]["ttt"])})
    # ---- quarterbacks: q_ (Sleeper's Tuesday QB1) and qp_ (pre-kickoff by construction)
    row.update({"q_gsis": D.gsis_str(q_g) if q_g else "", "q_source": q_src, "q_is_player": 1 if q_g == pid else 0})
    if q:
        bd, vs = q["by_depth"], q["vs"]
        row.update({"q_games": q["games"], "q_dropbacks": q["dropbacks"], "q_epa_db": v(q["epa_db"]), "q_epa_db_eb": eb(q["epa_db"]),
                    "q_comp": v(q["comp"]), "q_ypa": v(q["ypa"]), "q_sack": v(q["sack"]), "q_ttt": v(q["ttt"]), "q_deep_rate": v(q["deep_rate"]),
                    "q_cpoe_short": v((bd.get("short") or {}).get("cpoe")), "q_cpoe_int": v((bd.get("intermediate") or {}).get("cpoe")),
                    "q_cpoe_deep": v((bd.get("deep") or {}).get("cpoe")),
                    "q_epa_man": v((vs.get("man") or {}).get("epa_db")), "q_epa_zone": v((vs.get("zone") or {}).get("epa_db")),
                    "q_epa_press": v((vs.get("pressure") or {}).get("epa_db")), "q_epa_nopress": v((vs.get("no_pressure") or {}).get("epa_db")),
                    "q_epa_blitz": v((vs.get("blitz") or {}).get("epa_db")), "q_epa_noblitz": v((vs.get("no_blitz") or {}).get("epa_db")),
                    "q_tgt_share_to_player": v(S.qb_target_share(q_g, pid)) if q_g != pid else None})
    row.update({"qp_gsis": D.gsis_str(qp_g) if qp_g else "", "qp_source": qp_s,
                "qp_same_as_q": None if not (qp_g and q_g) else (1 if qp_g == q_g else 0)})
    if qp:
        bd, vs = qp["by_depth"], qp["vs"]
        row.update({"qp_games": qp["games"], "qp_dropbacks": qp["dropbacks"], "qp_epa_db": v(qp["epa_db"]), "qp_epa_db_eb": eb(qp["epa_db"]),
                    "qp_ttt": v(qp["ttt"]), "qp_cpoe_short": v((bd.get("short") or {}).get("cpoe")),
                    "qp_epa_man": v((vs.get("man") or {}).get("epa_db")), "qp_epa_zone": v((vs.get("zone") or {}).get("epa_db")),
                    "qp_epa_press": v((vs.get("pressure") or {}).get("epa_db")), "qp_epa_blitz": v((vs.get("blitz") or {}).get("epa_db")),
                    "qp_tgt_share_to_player": v(S.qb_target_share(qp_g, pid)) if qp_g != pid else None})
    # ---- interactions
    dm, dz = row["d_man"], row["d_zone"]
    if dm is not None and dz is not None and row["p_tgt_per_db_man"] is not None and row["p_tgt_per_db_zone"] is not None:
        row["x_exp_tgt_per_db"] = row["p_tgt_per_db_man"] * dm + row["p_tgt_per_db_zone"] * dz
    else:
        row["x_exp_tgt_per_db"] = row["p_tgt_per_db"]
    if dm is not None and dz is not None and row["p_man_ypt_eb"] is not None and row["p_zone_ypt_eb"] is not None:
        row["x_exp_ypt_mz"] = row["p_man_ypt_eb"] * dm + row["p_zone_ypt_eb"] * dz
    sh, th = row["d_single_high"], row["d_two_high"]
    if sh is not None and th is not None and row["p_tgt_per_db"] is not None:
        rest = max(0.0, 1.0 - sh - th)
        a = row["p_sh_tgt_per_db"] if row["p_sh_tgt_per_db"] is not None else row["p_tgt_per_db"]
        b = row["p_th_tgt_per_db"] if row["p_th_tgt_per_db"] is not None else row["p_tgt_per_db"]
        row["x_exp_tgt_per_db_shell"] = a * sh + b * th + row["p_tgt_per_db"] * rest
    depth_pairs = (("p_depth_behind", "d_depth_behind_ypt"), ("p_depth_short", "d_depth_short_ypt"),
                   ("p_depth_int", "d_depth_int_ypt"), ("p_depth_deep", "d_depth_deep_ypt"))
    if all(row.get(a) is not None and row.get(b) is not None for a, b in depth_pairs):
        row["x_depth_ypt"] = sum(row[a] * row[b] for a, b in depth_pairs)
    route_pairs = (("p_route_quick", "d_route_quick_epa"), ("p_route_screen", "d_route_screen_epa"),
                   ("p_route_int", "d_route_int_epa"), ("p_route_vert", "d_route_vert_epa"))
    if all(row.get(a) is not None and row.get(b) is not None for a, b in route_pairs):
        row["x_route_epa"] = sum(row[a] * row[b] for a, b in route_pairs)
    if pos in ("WR", "TE", "RB"):
        lp = L["pos"][pos].get("ypt")
        if row.get(f"d_{pos}_ypt_eb") is not None and lp is not None:
            row["x_pos_ypt_gap"] = row[f"d_{pos}_ypt_eb"] - lp
        row["x_pos_adj_ppr"] = row.get(f"d_{pos}_adj_ppr")
    else:
        row["x_pos_adj_ppr"] = row.get("d_QB_adj_ppr")
    lb, hb = row["d_light_box"], row["d_heavy_box"]
    if lb is not None and hb is not None and row["p_ypc"] is not None:
        a = row["p_ypc_light"] if row["p_ypc_light"] is not None else row["p_ypc"]
        b = row["p_ypc_heavy"] if row["p_ypc_heavy"] is not None else row["p_ypc"]
        row["x_box_ypc"] = a * lb + b * hb + row["p_ypc"] * max(0.0, 1.0 - lb - hb)
    return row


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---- the pbp-vs-Sleeper cross-check (2023-2025 only; 2026 is the test split) -----
def check(dataset_path):
    import numpy as np
    pairs = (("act_rec_yds", "sl_act_rec_yd"), ("act_rec", "sl_act_rec"), ("act_tgt", "sl_act_rec_tgt"), ("act_rush_yds", "sl_act_rush_yd"),
             ("act_car", "sl_act_rush_att"), ("act_pass_yds", "sl_act_pass_yd"), ("act_ppr", "sl_act_pts_ppr"))
    acc = {a: [] for a, b in pairs}
    n = 0
    with gzip.open(dataset_path, "rt", newline="") as fh:
        for row in csv.DictReader(fh):
            if int(row["season"]) >= 2026 or row["played"] != "1" or not row.get("sl_act_gp"):
                continue
            n += 1
            for a, b in pairs:
                if row.get(a) not in ("", None) and row.get(b) not in ("", None):
                    acc[a].append((float(row[a]), float(row[b])))
    out = {"rows_compared": n, "seasons": "2023-2025 player-games with a Sleeper box (gp)", "stats": {}}
    for a, b in pairs:
        arr = np.asarray(acc[a], float)
        if len(arr) == 0:
            continue
        diff = arr[:, 0] - arr[:, 1]
        out["stats"][a] = {"n": int(len(arr)), "exact": round(float(np.mean(np.abs(diff) < 1e-6)), 4),
                           "within_1": round(float(np.mean(np.abs(diff) <= 1.0)), 4), "mean_abs_diff": round(float(np.mean(np.abs(diff))), 3),
                           "mean_diff_pbp_minus_sleeper": round(float(np.mean(diff)), 3), "sleeper_col": b}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("measure", "build", "check", "schema"))
    ap.add_argument("--sleeper", default=None, help="dir with proj_2026_<w>.json / stats_2026_<w>.json")
    ap.add_argument("--out", default=os.path.join(OUT, "dataset_2023_2026.csv.gz"))
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    if args.cmd == "schema":
        with open(os.path.join(OUT, "dataset_schema.json"), "w", encoding="utf-8") as fh:
            json.dump(schema(), fh, indent=1)
        log(f"{len(schema())} columns")
        return
    stores = load_stores()
    pos_map, ids = positions()
    if args.cmd == "measure":
        t0 = time.time()
        pers = measure_persistence(stores, pos_map)
        pers["license"] = LICENSE
        with open(os.path.join(OUT, "persistence.json"), "w", encoding="utf-8") as fh:
            json.dump(pers, fh, indent=1)
        log(f"persistence: tendency pooled r {pers['tendency_pooled_r']} -> decay {pers['decay_chosen']} ({time.time() - t0:.0f}s)")
        rel = measure_reliability(stores, pos_map)
        rel["license"] = LICENSE
        with open(os.path.join(OUT, "reliability.json"), "w", encoding="utf-8") as fh:
            json.dump(rel, fh, indent=1)
        log(f"reliability k: {rel['k']} ({time.time() - t0:.0f}s)")
        agree = measure_agreement(stores)
        agree["license"] = LICENSE
        with open(os.path.join(OUT, "charting_agreement.json"), "w", encoding="utf-8") as fh:
            json.dump(agree, fh, indent=1)
        sc = measure_scheme_change(stores, pos_map)
        sc["license"] = LICENSE
        with open(os.path.join(OUT, "scheme_change_2026.json"), "w", encoding="utf-8") as fh:
            json.dump(sc, fh, indent=1)
        log(f"scheme flags: {sc.get('flags')} ({time.time() - t0:.0f}s)")
    elif args.cmd == "build":
        sleeper, fields = load_sleeper(args.sleeper, set(DATASET_SEASONS))
        log(f"sleeper rows {len(sleeper)}; proj fields {dict(fields['proj'])}; act fields {dict(fields['act'])}")
        meta = build_dataset(stores, pos_map, ids, sleeper, args.out)
        meta["sleeper_fields"] = {"proj": dict(fields["proj"]), "act": dict(fields["act"])}
        meta["columns"] = len(schema())
        meta["license"] = LICENSE
        with open(os.path.join(OUT, "dataset_schema.json"), "w", encoding="utf-8") as fh:
            json.dump(schema(), fh, indent=1)
        with open(os.path.join(OUT, "dataset_meta.json"), "w", encoding="utf-8") as fh:
            json.dump(meta, fh, indent=1, default=str)
        log(json.dumps({k: v for k, v in meta.items() if k not in ("sleeper_fields",)}, default=str))
    elif args.cmd == "check":
        out = check(args.out)
        out["license"] = LICENSE
        with open(os.path.join(OUT, "sleeper_crosscheck.json"), "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=1)
        log(json.dumps(out))


if __name__ == "__main__":
    main()
