"""Task 8, second artifact: the field the classic sampler ACTUALLY PRODUCES,
beside the field that actually entered. FIT-FREE.

The first artifact (research/cl_field.py) put the sampler's six hand-set
constants beside the week-1 $3.5M Millionaire's 831,028 active entries. The
constants are input targets; the sampler's rejection and legality mechanics
can move its realised output away from them, so the question the reviewer
asked is the one that matters: with the constants and mechanics we actually
serve, what field does the sampler produce, and where does it differ from
the real 831k-entry field? Two realised fields answer it:

  served   production's own pre-lock build of draft group 151307: built
           2026-09-13 16:27:24 UTC, 33 minutes before the 17:00 lock, by
           dfs_tourney.build_nfl_classic (unseeded; 300,000 field draws
           after a calibration on 20,000). The tab served it; the error-log
           workflow pulled it into the sim-history branch at 20:47 UTC that
           day; it is copied here byte for byte and pinned by hash
           (research/data/dk_classic/served_board_151307.json, refused if
           the hash differs or the build stamp is after lock). Its pool, the
           240 players the roster gate kept (QB1, RB1-2, WR1-3, TE1-2 and
           the defenses, plus 24 depth-gated players the public still
           rosters), with Sleeper's lines and DraftKings' salaries and
           availability as they stood at 16:27, its calibration (beta 0.194,
           kappa -1.984) and its per-player any-slot ownership are the
           production numbers, not a reconstruction. Every one of the 240
           resolves to the draft group captured after the game with the same
           salary and team.
  re-run   the untouched sampler (dfs_tourney.calibrate_field, then
           classic_sample; the constants exactly as served) on exactly that
           pool at the real field's size, 831,028 draws, under five fixed
           seeds. Each seed calibrates afresh on its own 20,000-draw sample
           the way a build does, so the calibration's own noise is inside
           the spread; the field is then drawn in chunks from that seed's one
           generator (rows are independent, so chunking changes nothing but
           memory) and every chunk must complete (check_completion).

Nothing is fitted to the real field. calibrate_field aims at CL_FIELD_MAX_OWN
and CL_SALARY_USED: that is production's mechanism run with production's
targets, and its outcome on this pool (the two cannot both be met; the
least-combined-miss compromise is what a build ships) is part of what is
measured. Every table says which column is a TARGET (an input constant),
which is REALISED (what a sampled field actually did) and which is REAL (the
export). The real field's statistics are the first artifact's, produced by
research.cl_field.empirical, and the sampled fields go through the same
function with the same slot labels, so no definition can differ between the
two sides; production's own receipts function (dfs_tourney.field_stats) and
its own collision statistic (_collision) are run on the re-run as well, so
the re-run can be checked against the served board's stamps.

    python3 -m research.cl_sampler seed <seed> <scratch dir>   # one seed, its partial file
    python3 -m research.cl_sampler assemble <scratch dir>      # the artifact from the partials
"""
import collections
import datetime
import hashlib
import json
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from research import cl_field as C  # noqa: E402

DATA = C.DATA
CAPDIR = C.CAPDIR
DG = C.DG
CONTEST = C.CONTEST
SERVED_FILE = "served_board_151307.json"
SEEDS = (20260913, 20260914, 20260915, 20260916, 20260917)
FIELD_N = 831028      # the real field's active entries (the first artifact's reconciliation)
CAL_N = 20000         # build_nfl_classic's cal_n default, which pc_worker does not override
CHUNK = 100000
TOP_TABLE = 50


def _sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def served_board():
    """The served board, verified against its pinned hash, refused after lock."""
    man = json.load(open(os.path.join(CAPDIR, "manifest.json")))["draft_groups"][str(DG)]["served_board"]
    path = os.path.join(CAPDIR, man["file"])
    sha = _sha(path)
    if sha != man["sha256"]:
        raise SystemExit(f"{path}: sha256 {sha} is not the pinned {man['sha256']}; refusing to read an unpinned board")
    d = json.load(open(path))
    if d.get("draft_group_id") != DG or d.get("kind") != "classic":
        raise SystemExit("the pinned board is not the classic board for draft group 151307")
    from research import s6_capture
    _slate, details = s6_capture.replay(DG, capdir=CAPDIR)
    starts = details[str(CONTEST)]["starts"]
    lock = datetime.datetime.strptime(starts[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc).timestamp()
    if float(d["built_ts"]) >= lock:
        raise SystemExit(f"the pinned board was built at {d['built_ts']}, on or after lock {starts}; refused")
    return d, dict(man, minutes_before_lock=round((lock - d["built_ts"]) / 60.0, 1))


def served_pool(d):
    """The 240 entrants exactly as production sampled from them."""
    return [{"name": p["name"], "pos": p["pos"], "team": p["team"], "opp": p["opp"], "game": p["game"],
             "salary": int(p["salary"]), "proj": float(p["proj"]), "depth": p.get("depth"),
             "_field_only": bool(p.get("field_only"))} for p in d["players"]]


def grading_pool(ents):
    return {e["name"]: {"pos": e["pos"], "team": e["team"], "opp": e["opp"], "salary": e["salary"], "game": e["game"]} for e in ents}


def reconcile_pool(ents, slate_pool):
    """Every served entrant against the draft group captured after the game."""
    missing = [e["name"] for e in ents if e["name"] not in slate_pool]
    sal = [e["name"] for e in ents if e["name"] in slate_pool and slate_pool[e["name"]]["salary"] != e["salary"]]
    team = [e["name"] for e in ents if e["name"] in slate_pool and slate_pool[e["name"]]["team"] != e["team"]]
    opp = [e["name"] for e in ents if e["name"] in slate_pool and slate_pool[e["name"]]["opp"] != e["opp"]]
    per_team = collections.Counter((e["team"], e["pos"]) for e in ents if not e["_field_only"])
    return {"served_players": len(ents), "field_only": sum(1 for e in ents if e["_field_only"]),
            "gate_kept": len(ents) - sum(1 for e in ents if e["_field_only"]),
            "teams": len({e["team"] for e in ents}),
            "kept_per_team_position_max": {pos: max(v for (t, p), v in per_team.items() if p == pos) for pos in ("QB", "RB", "WR", "TE", "DST")},
            "depth_tags": dict(collections.Counter(e["depth"] for e in ents).most_common()),
            "unresolved_to_draft_group": missing, "salary_mismatch": sal, "team_mismatch": team, "opp_mismatch": opp,
            "all_checks_pass": not (missing or sal or team or opp)}


def lineups_from_idx(idx, ents):
    names = [e["name"] for e in ents]
    return [[(s, names[i]) for s, i in zip(C.SLOTS, row)] for row in idx.tolist()]


def sample_field(ents, seed, n=FIELD_N, cal_n=CAL_N, chunk=CHUNK, log=print):
    """Production's two calls, in production's order, from one generator."""
    import dfs_tourney as T
    rng = np.random.default_rng(int(seed))
    t0 = time.time()
    beta, kappa, own, mean, coll, top = T.calibrate_field(ents, rng, n=int(cal_n))
    t1 = time.time()
    log(f"[CLS] seed {seed}: calibrated beta {beta:.4f} kappa {kappa:.4f} on {cal_n:,} draws "
        f"(final draw: top own {100 * own:.2f}%, mean salary ${mean:,.0f}, collision {coll:.2e}) {t1 - t0:.0f}s; sampling {n:,}...")
    parts, done = [], 0
    while done < n:
        k = min(int(chunk), n - done)
        rep = {}
        idx = T.classic_sample(ents, k, rng, beta, kappa, report=rep)
        T.check_completion(rep, f"re-run seed {seed} chunk at {done:,}")
        parts.append(idx)
        done += len(idx)
    idx = np.concatenate(parts, axis=0)
    assert len(idx) == n, (len(idx), n)
    t2 = time.time()
    log(f"[CLS] seed {seed}: {len(idx):,} rows in {t2 - t1:.0f}s")
    return {"seed": int(seed), "beta": round(float(beta), 6), "kappa": round(float(kappa), 6),
            "calibration": {"n": int(cal_n), "targets": {"max_own": T.CL_FIELD_MAX_OWN, "salary_used": T.CL_SALARY_USED},
                            "final_draw": {"max_own": float(own), "mean_salary": float(mean), "collision": float(coll), "top_share": float(top)},
                            "seconds": round(t1 - t0, 1)},
            "sample": {"n": int(len(idx)), "chunk": int(chunk), "seconds": round(t2 - t1, 1)}}, idx


def _rankdata(a):
    a = np.asarray(a, dtype=np.float64)
    order = np.argsort(a, kind="mergesort")
    ranks = np.empty(len(a), dtype=np.float64)
    sa = a[order]
    i, n = 0, len(a)
    while i < n:
        j = i
        while j + 1 < n and sa[j + 1] == sa[i]:
            j += 1
        ranks[order[i:j + 1]] = 0.5 * (i + j) + 1.0
        i = j + 1
    return ranks


def compare_ownership(samp_pct, real_pct, ents, top=TOP_TABLE):
    """Sampled any-slot ownership (pct of sampled lineups) against real any-slot
    ownership (pct of active real lineups), over the sampler's own pool, plus
    what the pool cannot reach at all."""
    names = [e["name"] for e in ents]
    s = np.asarray([float(samp_pct.get(nm, 0.0)) for nm in names])
    r = np.asarray([float(real_pct.get(nm, 0.0)) for nm in names])
    d = s - r
    order_r = np.argsort(-r)
    top_i = order_r[:top]
    outside = sorted(((nm, v) for nm, v in real_pct.items() if nm not in set(names)), key=lambda kv: -kv[1])
    return {"players_in_pool": len(names),
            "mae_pp": round(float(np.abs(d).mean()), 3), "rmse_pp": round(float(np.sqrt((d ** 2).mean())), 3),
            "bias_pp": round(float(d.mean()), 3),
            "pearson": round(float(np.corrcoef(s, r)[0, 1]), 4), "spearman": round(float(np.corrcoef(_rankdata(s), _rankdata(r))[0, 1]), 4),
            f"top{top}_by_real": {"mae_pp": round(float(np.abs(d[top_i]).mean()), 3), "rmse_pp": round(float(np.sqrt((d[top_i] ** 2).mean())), 3),
                                  "bias_pp": round(float(d[top_i].mean()), 3),
                                  "spearman": round(float(np.corrcoef(_rankdata(s[top_i]), _rankdata(r[top_i]))[0, 1]), 4)},
            "top20_by_real": {"mae_pp": round(float(np.abs(d[order_r[:20]]).mean()), 3), "bias_pp": round(float(d[order_r[:20]].mean()), 3)},
            "coverage": {"real_slots_on_pool_players_pct": round(float(r.sum()) / 9.0, 3),
                         "real_players_outside_pool": len(outside),
                         "real_slots_outside_pool_pct": round(sum(v for _, v in outside) / 9.0, 3),
                         "largest_outside_pool": [{"name": nm, "real_pct": round(v, 2)} for nm, v in outside[:8]]},
            "largest_over": [{"name": names[i], "sampled_pct": round(float(s[i]), 2), "real_pct": round(float(r[i]), 2), "diff_pp": round(float(d[i]), 2)}
                             for i in np.argsort(-d)[:8]],
            "largest_under": [{"name": names[i], "sampled_pct": round(float(s[i]), 2), "real_pct": round(float(r[i]), 2), "diff_pp": round(float(d[i]), 2)}
                              for i in np.argsort(d)[:8]]}


def grade(idx, ents, pool_g):
    """The realised field through the first artifact's definitions and through
    production's own receipts."""
    import dfs_tourney as T
    emp = C.empirical(lineups_from_idx(idx, ents), pool_g)
    P = len(ents)
    own = np.bincount(idx.ravel(), minlength=P) / float(len(idx))
    receipts = {"field_stats": T.field_stats(idx, ents), "collision_distinct_pairs": float(T._collision(idx)),
                "top_share": float(T._dup_share(idx))}
    return emp, {e["name"]: round(100.0 * float(own[i]), 4) for i, e in enumerate(ents)}, receipts


def real_side():
    """The first artifact's real field, refused if stale or unpinned."""
    art = json.load(open(os.path.join(DATA, "cl_field.json")))
    man = json.load(open(os.path.join(DATA, "dk_standings", "manifest.json")))["files"][f"contest-standings-{CONTEST}.csv.gz"]
    if art["meta"]["standings_file"]["sha256"] != man["csv_sha256"]:
        raise SystemExit("cl_field.json was not built on the pinned export")
    if "distinct_pairs" not in art["empirical"]["collision"] or "ownership_any_slot_pct" not in art["empirical"]:
        raise SystemExit("cl_field.json predates the collision correction; rebuild the first artifact first")
    if art["empirical"]["active_entries"] != FIELD_N:
        raise SystemExit(f"the real field has {art['empirical']['active_entries']} active entries, not {FIELD_N}")
    return art


def run_seed(seed, outdir, log=print):
    from research import provenance
    d, man = served_board()
    ents = served_pool(d)
    pool_g = grading_pool(ents)
    meta, idx = sample_field(ents, seed, log=log)
    emp, own, receipts = grade(idx, ents, pool_g)
    real = real_side()
    served_pct = {p["name"]: float(p["field_pct"]) for p in d["players"]}
    out = dict(meta, empirical=emp, ownership_any_slot_pct=own, production_receipts=receipts,
               vs_real=compare_ownership(own, real["empirical"]["ownership_any_slot_pct"], ents),
               vs_served=compare_ownership(own, served_pct, ents),
               provenance=provenance.stamp(worlds=FIELD_N, model="dfs_tourney.calibrate_field + classic_sample, the constants as served, on the served pre-lock pool",
                                           seed=int(seed)))
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, f"cl_sampler_seed_{seed}.json")
    with open(path, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    log(f"[CLS] seed {seed}: top own {emp['max_ownership']['player']} {emp['max_ownership']['any_slot_pct']}%, mean salary ${emp['salary_used']['mean']:,.0f}, "
        f"pairs {emp['collision']['distinct_pairs']:.2e}, MAE vs real {out['vs_real']['mae_pp']} pp, Spearman {out['vs_real']['spearman']}; written {path}")
    return out


def _agg(vals):
    v = [float(x) for x in vals if x is not None]
    if not v:
        return None
    return {"mean": round(float(np.mean(v)), 4), "min": round(min(v), 4), "max": round(max(v), 4)}


def _rows(target, served, per_seed, real, log=print):
    """The structure table: one row per quantity, every column labelled by kind."""
    import dfs_tourney as T
    ach = served["field_model"]["achieved"]
    rows = []

    def row(q, tgt, srv, key, rl, note=""):
        vals = [key(s) for s in per_seed]
        rows.append({"quantity": q, "target_input": tgt, "served_realised": srv, "rerun_realised": _agg(vals), "real": rl, "note": note})

    E = lambda s: s["empirical"]  # noqa: E731
    row("most-owned player, any slot (% of lineups)", 100 * T.CL_FIELD_MAX_OWN, round(100 * ach["max_own"], 2),
        lambda s: E(s)["max_ownership"]["any_slot_pct"], real["max_ownership"]["any_slot_pct"],
        "the calibration's first target; the served build landed 1.4 pts above it because the two targets cannot both be met on this pool")
    row("mean salary used ($)", T.CL_SALARY_USED, round(ach["mean_salary"], 1), lambda s: E(s)["salary_used"]["mean"], real["salary_used"]["mean"],
        "the calibration's second target")
    row("median salary used ($)", None, None, lambda s: E(s)["salary_used"]["median"], real["salary_used"]["median"])
    row("share at exactly $50,000 (%)", None, None, lambda s: E(s)["salary_used"]["share_at_cap_pct"], real["salary_used"]["share_at_cap_pct"])
    row("share at $49,500 or more (%)", None, None, lambda s: E(s)["salary_used"]["share_ge_49500_pct"], real["salary_used"]["share_ge_49500_pct"])
    row("share at $48,000 or less (%)", None, None, lambda s: E(s)["salary_used"]["share_le_48000_pct"], real["salary_used"]["share_le_48000_pct"])
    for i, q in enumerate(("p10", "p25", "p75", "p90")):
        row(f"salary used {q} ($)", None, None, lambda s, i=i: E(s)["salary_used"]["p10_p25_p75_p90"][i], real["salary_used"]["p10_p25_p75_p90"][i])
    for k, lab in enumerate(("naked QB", "one WR/TE teammate", "two", "three or more")):
        row(f"stack: {lab} (%)", round(100 * T.CL_STACK_DIST[k], 1), round(100 * ach["stack_dist"][k], 2),
            lambda s, k=k: E(s)["stack_pct"]["0_1_2_3plus"][k], real["stack_pct"]["0_1_2_3plus"][k],
            "drawn from CL_STACK_DIST; a back does not count" if k == 0 else "")
    row("bring-back, all entries (%)", 100 * T.CL_BRING_BACK, round(100 * ach["bring_back"], 2), lambda s: E(s)["bring_back_pct"]["all_entries"],
        real["bring_back_pct"]["all_entries"], "drawn with CL_BRING_BACK for every lineup")
    row("bring-back among stacked entries (%)", None, None, lambda s: E(s)["bring_back_pct"]["among_stacked"], real["bring_back_pct"]["among_stacked"])
    row("defense against own QB (%)", 100 * T.CL_DST_VS_OWN_QB, round(100 * ach["dst_vs_own_qb"], 3), lambda s: E(s)["dst_vs_own_qb_pct"]["real"],
        real["dst_vs_own_qb_pct"]["real"], "the constant is the share NOT steering round it; the realised holding share is that times the pick rate, so target and realised are not 1:1")
    row("defense against one of the lineup's backs (%)", None, None, lambda s: E(s)["dst_vs_own_rb_pct"], real["dst_vs_own_rb_pct"])
    for pos in ("RB", "WR", "TE"):
        row(f"FLEX is a {pos} (%)", None, None, lambda s, pos=pos: E(s)["flex_position_pct"].get(pos, 0.0), real["flex_position_pct"].get(pos, 0.0))
    for k in ("2", "3", "4"):
        row(f"players from one team (non-DST), max {k}{'+' if k == '4' else ''} (%)", None, None,
            lambda s, k=k: sum(v for kk, v in E(s)["players_from_one_team_max_pct"].items() if (int(kk) >= 4 if k == "4" else kk == k)),
            sum(v for kk, v in real["players_from_one_team_max_pct"].items() if (int(kk) >= 4 if k == "4" else kk == k)),
            "our own team cap is 3; the field sampler has none" if k == "4" else "")
    for k in ("3", "4", "5"):
        row(f"players from one game, max {k}{'+' if k == '5' else ''} (%)", None, None,
            lambda s, k=k: sum(v for kk, v in E(s)["players_from_one_game_max_pct"].items() if (int(kk) >= 5 if k == "5" else kk == k)),
            sum(v for kk, v in real["players_from_one_game_max_pct"].items() if (int(kk) >= 5 if k == "5" else kk == k)))
    for k in ("0", "1", "2"):
        row(f"punts under $3,000: {k}{'+' if k == '2' else ''} (%)", None, None,
            lambda s, k=k: sum(v for kk, v in E(s)["punts_under_3000_pct"].items() if (int(kk) >= 2 if k == "2" else kk == k)),
            sum(v for kk, v in real["punts_under_3000_pct"].items() if (int(kk) >= 2 if k == "2" else kk == k)))
    row("players used", None, None, lambda s: E(s)["players_used"], real["players_used"], "of 240 in the sampler's pool; of 746 in the draft group")
    row("distinct lineups", None, None, lambda s: E(s)["distinct_lineups"], real["distinct_lineups"], "both fields have 831,028 lineups, so these are comparable as they stand")
    row("most-copied lineup (copies)", None, None, lambda s: E(s)["max_lineup_copies"], real["max_lineup_copies"])
    row("most-copied lineup share (%)", None, round(served["field_model"]["top_share_pct"], 4), lambda s: E(s)["max_lineup_share_pct"], real["max_lineup_share_pct"],
        "the served value is on its 300,000-draw sample")
    for b in ("1-1", "2-2", "3-5", "6-10", "11-50", "51-plus"):
        row(f"entries in lineups with {b} copies (%)", None, None, lambda s, b=b: E(s)["duplicates"][b]["entry_share_pct"], real["duplicates"][b]["entry_share_pct"])
    row("plug-in HHI (sum of squared lineup shares)", None, None, lambda s: E(s)["collision"]["sum_p2"], real["collision"]["sum_p2"],
        "descriptive; floor 1/N = 1.20e-6 at this size on both sides")
    row("effective lineups (1 / HHI)", None, None, lambda s: E(s)["collision"]["effective_lineups"], real["collision"]["effective_lineups"])
    row("distinct-entry collision (pairs holding the same lineup / all pairs)", T.CL_FIELD_COLLISION, served["field_model"]["collision"],
        lambda s: E(s)["collision"]["distinct_pairs"], real["collision"]["distinct_pairs"],
        "the constant's definition; the served value is dfs_tourney._collision on its 300,000 draws, the re-run's the same statistic on 831,028")
    row("effective lineups by pairs (1 / collision)", round(1.0 / T.CL_FIELD_COLLISION), round(1.0 / served["field_model"]["collision"]),
        lambda s: E(s)["collision"]["effective_lineups_pairs"], real["collision"]["effective_lineups_pairs"])
    return rows


def _top_players(served, per_seed, real, ents, top=TOP_TABLE):
    real_pct = real["ownership_any_slot_pct"]
    served_pct = {p["name"]: float(p["field_pct"]) for p in served["players"]}
    info = {e["name"]: e for e in ents}
    order = sorted(real_pct.items(), key=lambda kv: -kv[1])[:top]
    out = []
    for nm, r in order:
        vals = [s["ownership_any_slot_pct"].get(nm) for s in per_seed] if nm in info else []
        e = info.get(nm)
        out.append({"name": nm, "in_pool": nm in info, "pos": e["pos"] if e else None, "team": e["team"] if e else None,
                    "salary": e["salary"] if e else None, "proj": e["proj"] if e else None, "depth": e["depth"] if e else None,
                    "real_pct": round(r, 2), "served_pct": served_pct.get(nm), "rerun_pct": _agg(vals),
                    "rerun_minus_real_pp": (round(_agg(vals)["mean"] - r, 2) if vals else None)})
    return out


def assemble(outdir, log=print):
    from research import provenance, s6_capture
    d, man = served_board()
    ents = served_pool(d)
    slate, _details = s6_capture.replay(DG, capdir=CAPDIR)
    rec = reconcile_pool(ents, C.pool_rows(slate))
    if not rec["all_checks_pass"]:
        raise SystemExit(f"the served pool does not reconcile with the pinned draft group: {rec}")
    real_art = real_side()
    real = real_art["empirical"]
    per_seed = []
    for seed in SEEDS:
        path = os.path.join(outdir, f"cl_sampler_seed_{seed}.json")
        if not os.path.exists(path):
            raise SystemExit(f"missing {path}: run `python3 -m research.cl_sampler seed {seed} {outdir}` first")
        per_seed.append(json.load(open(path)))
    commits = {s["provenance"]["commit"] for s in per_seed}
    dirty = any(s["provenance"].get("dirty") for s in per_seed)
    here = provenance.stamp(worlds=FIELD_N, model="dfs_tourney.calibrate_field + classic_sample, the constants as served, on the served pre-lock pool",
                            seed=list(SEEDS))
    if len(commits) != 1 or dirty or here["commit"] not in commits:
        raise SystemExit(f"the seed runs were not all made on this clean commit: {commits}, dirty {dirty}, here {here['commit']}")
    served_pct = {p["name"]: float(p["field_pct"]) for p in d["players"]}
    served_cmp = compare_ownership(served_pct, real["ownership_any_slot_pct"], ents)
    import dfs_tourney as T
    out = {"meta": {"stage": "Task 8, second artifact: the field the classic sampler actually produces (production's served pre-lock build, and the untouched sampler re-run at the real field's size under fixed seeds) beside the real week-1 Millionaire field, fit-free",
                    "contest": real_art["meta"]["contest"], "standings_file": real_art["meta"]["standings_file"],
                    "inputs": {"draftkings": s6_capture.dk_hashes(DG, capdir=CAPDIR), "served_board": man, "first_artifact": "research/data/cl_field.json"},
                    "fit_free": True,
                    "what_is_fitted": "nothing to the real field; calibrate_field aims at CL_FIELD_MAX_OWN and CL_SALARY_USED on the sampler's own draws, "
                                      "which is production's mechanism with production's targets, and its compromise on this pool is part of the measurement",
                    "constants_as_served": {"CL_FIELD_MAX_OWN": T.CL_FIELD_MAX_OWN, "CL_SALARY_USED": T.CL_SALARY_USED, "CL_STACK_DIST": list(T.CL_STACK_DIST),
                                            "CL_BRING_BACK": T.CL_BRING_BACK, "CL_DST_VS_OWN_QB": T.CL_DST_VS_OWN_QB, "CL_FIELD_COLLISION": T.CL_FIELD_COLLISION,
                                            "unchanged_from_the_served_board": d["field_model"]["targets"] == {"max_own": T.CL_FIELD_MAX_OWN, "mean_salary": T.CL_SALARY_USED,
                                                                                                                 "bring_back": T.CL_BRING_BACK, "dst_vs_own_qb_not_avoiding": T.CL_DST_VS_OWN_QB}
                                            and d["field_model"]["stack_dist"] == list(T.CL_STACK_DIST)},
                    "seeds": list(SEEDS), "field_n": FIELD_N, "cal_n": CAL_N, "chunk": CHUNK,
                    "columns": {"target_input": "the constant as an input", "served_realised": "what production's pre-lock build actually produced (300,000 draws; only the receipts it stamped)",
                                "rerun_realised": "what the untouched sampler produced at 831,028 draws, mean and range over the seeds", "real": "the export"},
                    "provenance": here},
           "served": {"built_utc": man["built_utc"], "minutes_before_lock": man["minutes_before_lock"], "n": d["field_model"]["n"],
                      "beta": d["field_model"]["beta"], "kappa": d["field_model"]["kappa"], "seed": d.get("seed"),
                      "achieved": d["field_model"]["achieved"], "collision_distinct_pairs": d["field_model"]["collision"],
                      "top_share_pct": d["field_model"]["top_share_pct"], "completion": d["field_model"]["completion"],
                      "pool": rec, "slate_at_build": d["slate"], "excluded_no_projection": len(d["excluded"]),
                      "ownership_any_slot_pct": served_pct, "vs_real": served_cmp},
           "rerun": {"per_seed": per_seed,
                     "summary": {"beta": _agg([s["beta"] for s in per_seed]), "kappa": _agg([s["kappa"] for s in per_seed]),
                                 "vs_real": {k: _agg([s["vs_real"][k] for s in per_seed]) for k in ("mae_pp", "rmse_pp", "bias_pp", "pearson", "spearman")},
                                 "vs_real_top50": {k: _agg([s["vs_real"]["top50_by_real"][k] for s in per_seed]) for k in ("mae_pp", "rmse_pp", "bias_pp", "spearman")},
                                 "vs_served": {k: _agg([s["vs_served"][k] for s in per_seed]) for k in ("mae_pp", "rmse_pp", "bias_pp", "pearson", "spearman")},
                                 "production_receipts": {k: _agg([s["production_receipts"]["field_stats"][k] for s in per_seed]) for k in ("max_own", "mean_salary", "bring_back", "dst_vs_own_qb")},
                                 "collision_distinct_pairs": _agg([s["production_receipts"]["collision_distinct_pairs"] for s in per_seed]),
                                 "seconds": {"calibration": _agg([s["calibration"]["seconds"] for s in per_seed]), "sample": _agg([s["sample"]["seconds"] for s in per_seed])}}},
           "structure": _rows(None, d, per_seed, real, log=log),
           "players_top50_by_real": _top_players(d, per_seed, real, ents),
           "coverage": served_cmp["coverage"]}
    path = os.path.join(DATA, "cl_sampler.json")
    with open(path, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    log(f"[CLS] written {path}: served MAE {served_cmp['mae_pp']} pp / Spearman {served_cmp['spearman']}; re-run MAE {out['rerun']['summary']['vs_real']['mae_pp']}, "
        f"coverage {served_cmp['coverage']['real_slots_on_pool_players_pct']}% of real slots")
    return out


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "seed":
        run_seed(int(sys.argv[2]), sys.argv[3])
    elif len(sys.argv) >= 3 and sys.argv[1] == "assemble":
        assemble(sys.argv[2])
    else:
        raise SystemExit("usage: python3 -m research.cl_sampler seed <seed> <scratch dir> | assemble <scratch dir>")
