"""S7, prospective validation of the FROZEN projection+salary field family.

The retrospective S7 baseline is frozen (research/s7_salary.py, commit
bc7a52c): one linear salary-left coefficient improved held-out likelihood on
three historical Showdown fields by 0.23 to 1.01 nats per entry, an order of
magnitude more than retuning beta, and left the concentration and duplication
failures visible. Those three contests have now been asked many questions;
choosing the next residual feature on the same boards would quietly turn the
process into iterative training on three contests. So the next stage is
prospective: freeze one parameter set BEFORE seeing another final field,
capture the next Showdown contests before lock, score the frozen model after
the game, and fit nothing on the new counts.

What this module does, and refuses to do:

  freeze     copies the pooled all-three fits out of research/data/s7_salary.json
             into research/data/s7_frozen_field.json with the source hash. The
             primary parameter set is the contest-balanced projection+salary
             fit (beta 0.112, gamma 1.045); the entry-weighted fit (0.116,
             0.995) is the secondary sensitivity arm; the contest-balanced
             projection-only beta (0.351) is the research baseline; the
             contest-balanced salary-only gamma (1.251) is a diagnostic arm.
             The reviewer's choice: the target is cross-slate generalisation,
             and a larger contest must not define the universal research
             parameter by its size.
  upcoming   lists DraftKings' posted one-game NFL draft groups.
  capture    pins, into research/data/prospective/<dg>/<stamp>/, the pool and
             contest lobby (s6_capture), the week's Sleeper projection feeds
             and the roster (sd_board), and stamps the capture time against
             the earliest contest start: hours_to_lock and pre_lock are on the
             record before anyone knows the field.
  standings  freezes the owner's post-game "Export full standings" file by
             hash into research/data/dk_standings and its manifest.
  score      builds the lifted universe from the PRE-LOCK capture (it refuses a
             capture stamped after lock), scores the frozen arms on the
             representable support, and writes research/data/s7_prospective_<cid>.json
             plus a row in the ledger research/data/s7_prospective.json.
             Nothing is fitted: this module never calls a fitter, and the
             production-field arm is scored without the diagnostic MLE the
             retrospective stage carried.

Arms scored on every prospective contest:
  1. the served production field, untouched: the placeholder on its own gated
     universe with the 0.2% rule, injury statuses as captured, on its own
     support (its likelihood is not comparable to the lifted-support arms;
     its shape metrics are)
  2. projection only, frozen contest-balanced beta, on the lifted support
  3. projection + salary, frozen primary (contest-balanced)
  4. projection + salary, frozen secondary (entry-weighted)
  5. salary only, frozen contest-balanced gamma (diagnostic)

Principal prospective criterion: per-entry NLL of arm 3 minus arm 2 on the
representable support; negative = salary helps; the user-cluster bootstrap
interval is conditional on the frozen parameters, which is now the honest
label because the parameters were fitted before the contest existed.

Graded without fitting: support coverage, exact-lineup likelihood, chalk rank,
top-10/100/1,000 observed mass, the salary-left distribution, captain mix,
5-1 / 4-2 / 3-3, K/DST usage, effective lineups, top-lineup share,
heavy-duplication mass.

Rules: no refit of beta or gamma per slate; no new parameter after seeing a
contest; score before any of its counts changes anything; at least three
genuinely pre-lock captured contests before another feature-selection round.
If prospective contests reproduce the retrospective residual (salary mean and
tail roughly right, a consistent unexplained spike at exactly $0), the next
single feature is delta * I[salary_left == 0]; the three retrospective boards
disagree on its sign (NE @ SEA and DEN @ KC under, DET @ BUF over), which is
exactly why it is not fitted now. Realised duplication is the outcome, never
a feature. A user-level mixture of lineup-generation strategies is recorded
as a later model-family hypothesis and not built.

    python3 -m research.s7_prospective freeze
    python3 -m research.s7_prospective upcoming
    python3 -m research.s7_prospective capture <week> <dg> [dg ...] [--contest <cid> ...] [--note "..."]
    python3 -m research.s7_prospective standings <cid> <export.zip|export.csv>
    python3 -m research.s7_prospective score <cid> <dg> <week> [--stamp <capture stamp>]
"""
import datetime
import gzip
import hashlib
import json
import os
import sys
import time
import zipfile

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from research import s6_capture, sd_board     # noqa: E402
from research import s7_beta as SB           # noqa: E402
from research import s7_field as F           # noqa: E402
from research import s7_salary as SS         # noqa: E402

DATA = F.DATA
PRO = os.path.join(DATA, "prospective")
FROZEN = os.path.join(DATA, "s7_frozen_field.json")
LEDGER = os.path.join(DATA, "s7_prospective.json")
SEED = F.SEED
MIN_CONTESTS_BEFORE_NEXT_FEATURE = 3
ARMS = (
    {"name": "production_field", "what": "the served placeholder on its own gated universe (depth gate applied, injury statuses as captured, "
                                         "beta from the 0.2% rule), scored on its own support; likelihood not comparable to the lifted-support arms"},
    {"name": "projection_only_frozen", "arm": "projection_only", "source": "projection_only_baseline"},
    {"name": "projection_plus_salary_frozen_primary", "arm": "projection_plus_salary", "source": "primary"},
    {"name": "projection_plus_salary_frozen_secondary", "arm": "projection_plus_salary", "source": "secondary"},
    {"name": "salary_only_diagnostic", "arm": "salary_only", "source": "salary_only_diagnostic"},
)
PRINCIPAL = ("per-entry NLL of projection+salary (frozen primary) minus projection-only (frozen baseline) on the representable "
             "support of a contest captured before lock; negative = salary helps")
GRADED = ("support coverage", "exact-lineup likelihood", "chalk rank", "top-10/100/1000 observed mass", "salary-left distribution",
          "captain mix", "5-1 / 4-2 / 3-3", "K/DST usage", "effective lineups", "top-lineup share", "heavy-duplication mass")
RULES = ("no refit of beta or gamma per slate", "no new parameter after seeing a contest",
         "score a contest before any of its counts changes anything",
         f"at least {MIN_CONTESTS_BEFORE_NEXT_FEATURE} genuinely pre-lock captured contests before another feature-selection round",
         "the scorer refuses a capture stamped after the earliest contest start")


def _sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _utc(ts=None):
    return datetime.datetime.fromtimestamp(ts if ts is not None else time.time(), datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


# ---- freeze ------------------------------------------------------------------
def freeze(log=print):
    from research import provenance
    src = os.path.join(DATA, "s7_salary.json")
    sal = json.load(open(src))
    P = sal["fits"]
    pick = lambda arm, wk: [float(v) for v in P[arm]["pooled_all"][wk]["theta"]]
    out = {"frozen_utc": _utc(),
           "source_artifact": {"file": "research/data/s7_salary.json", "sha256": _sha(src), "provenance": sal["meta"]["provenance"]},
           "model": sal["meta"]["model"],
           "primary": {"arm": "projection_plus_salary", "pooling": "contest_balanced", "theta": pick("projection_plus_salary", "contest_balanced"),
                       "why": "the target is cross-slate generalisation; a larger contest must not define the universal research parameter by its size"},
           "secondary": {"arm": "projection_plus_salary", "pooling": "entry_weighted", "theta": pick("projection_plus_salary", "entry_weighted"),
                         "why": "sensitivity arm; the two poolings differ by under 0.05 in gamma"},
           "projection_only_baseline": {"arm": "projection_only", "pooling": "contest_balanced", "theta": pick("projection_only", "contest_balanced")},
           "salary_only_diagnostic": {"arm": "salary_only", "pooling": "contest_balanced", "theta": pick("salary_only", "contest_balanced")},
           "arms": list(ARMS), "principal_criterion": PRINCIPAL, "graded_without_fitting": list(GRADED), "rules": list(RULES),
           "candidate_next_feature_if_the_residual_repeats": {
               "feature": "delta * I[salary_left == 0]",
               "condition": "prospective contests reproduce the retrospective residual: salary mean roughly right, salary tail roughly right, a consistent unexplained spike at exactly $0",
               "warning": "the three retrospective boards disagree on its sign (model share at $0: NE @ SEA 7.69% against 13.92%, DEN @ KC 4.68% against 6.67%, DET @ BUF 15.00% against 13.24%), so it is not fitted now",
               "checksums_if_fitted": ["mean projection", "mean salary left", "P(salary left = 0)"],
               "test_if_fitted": "held-out NLL and the rest of the salary distribution"},
           "not_a_feature": "realised duplication: it is the outcome the field model is asked to predict",
           "recorded_hypothesis_not_built": "the field is a mixture of user-level lineup-generation strategies rather than IID entries from one universal softmax (a model-family change, for after prospective validation)",
           "production": {"changed": False, "SD_MONEY_FIELD_CALIBRATED": False, "note": "what a winning arm earns is preferred research family status for prospective validation, not production calibration; S5 stays deferred"},
           "provenance": provenance.stamp(worlds=None, model="frozen parameters copied from s7_salary.json; nothing fitted", seed="UNSEEDED")}
    with open(FROZEN, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    log(f"[S7p] frozen: primary {out['primary']['theta']}, secondary {out['secondary']['theta']}, "
        f"projection-only {out['projection_only_baseline']['theta']}, salary-only {out['salary_only_diagnostic']['theta']} -> {os.path.relpath(FROZEN, ROOT)}")
    return out


# ---- capture --------------------------------------------------------------------
def upcoming(log=print):
    import dk
    rows = [s for s in (dk.slates("nfl") or []) if (s.get("games") or 0) == 1]
    for s in rows:
        log(f"  dg {s['draft_group_id']}  starts {s.get('starts')}  tag {s.get('tag')}  type {s.get('contest_type')}")
    if not rows:
        log("  no one-game NFL draft groups posted")
    return rows


def capture(dgs, week, contest_ids=(), note="", log=print):
    """One directory per draft group and capture time; nothing is overwritten,
    so a second capture nearer lock sits beside the first."""
    import nfl_dfs
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = []
    for dg in dgs:
        capdir = os.path.join(PRO, str(int(dg)), stamp)
        os.makedirs(capdir)
        s6_capture.capture((int(dg),), capdir=capdir, log=log, contest_ids=tuple(int(c) for c in contest_ids))
        sd_board.capture_feeds(capdir, week=int(week), log=log)
        sd_board.capture_roster(capdir, (int(dg),), week=int(week), log=log, capdir=capdir)
        slate, details = s6_capture.replay(int(dg), capdir=capdir)
        starts = [nfl_dfs.iso_ts(d.get("starts")) for d in details.values() if d.get("starts")]
        starts = [s for s in starts if s]
        lock = min(starts) if starts else None
        now = time.time()
        gpp = max(details.values(), key=lambda d: float(d.get("prize_pool") or 0)) if details else None
        info = {"draft_group": int(dg), "week": int(week), "capture_stamp": stamp, "captured_utc": _utc(now),
                "lock_utc": _utc(lock) if lock else None, "hours_to_lock": round((lock - now) / 3600.0, 3) if lock else None,
                "pre_lock": bool(lock and lock > now),
                "contests_detailed": sorted(details), "biggest_gpp_detailed": ({"id": gpp.get("id"), "name": gpp.get("name"), "max_entries": gpp.get("max_entries"),
                                                                             "prize_pool": gpp.get("prize_pool")} if gpp else None),
                "note": note, "files": {fn: _sha(os.path.join(capdir, fn)) for fn in sorted(os.listdir(capdir))}}
        with open(os.path.join(capdir, "capture.json"), "w") as fh:
            json.dump(info, fh, indent=1, sort_keys=True)
        log(f"[S7p] dg {dg} captured at {info['captured_utc']}: {info['hours_to_lock']} h before the earliest contest start "
            f"({'PRE-LOCK' if info['pre_lock'] else 'AFTER LOCK: not usable for prospective scoring'}); "
            f"{len(details)} contests detailed; biggest {info['biggest_gpp_detailed']}")
        out.append(info)
    return out


def standings(cid, path, log=print):
    """Freeze the owner's export by hash, beside the retrospective ones."""
    cid = int(cid)
    man_path = os.path.join(F.STANDINGS, "manifest.json")
    man = json.load(open(man_path))
    if path.lower().endswith(".zip"):
        zsha = _sha(path)
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".csv")]
            assert len(names) == 1, names
            raw = z.read(names[0])
            mtime = "%04d-%02d-%02d %02d:%02d:%02d" % z.getinfo(names[0]).date_time
    else:
        zsha, raw, mtime = None, open(path, "rb").read(), None
    target = os.path.join(F.STANDINGS, f"contest-standings-{cid}.csv.gz")
    assert not os.path.exists(target), f"{target} exists; a frozen export is never overwritten"
    with gzip.open(target, "wb", compresslevel=9) as fh:
        fh.write(raw)
    rows = raw.count(b"\n") - 1
    ent = {"contest_id": cid, "kind": "showdown", "committed": True, "prospective": True, "frozen_utc": _utc(),
           "csv_sha256": hashlib.sha256(raw).hexdigest(), "csv_bytes": len(raw), "gz_sha256": _sha(target),
           "zip_sha256": zsha, "csv_mtime_in_zip": mtime, "rows_excluding_header": rows,
           "note": "prospective contest: frozen after the game, scored by research.s7_prospective against the pre-lock capture"}
    man["files"][os.path.basename(target)] = ent
    with open(man_path, "w") as fh:
        json.dump(man, fh, indent=1, sort_keys=True)
    log(f"[S7p] standings {cid}: {rows:,} rows, csv sha256 {ent['csv_sha256'][:12]} -> {os.path.relpath(target, ROOT)}")
    return ent


# ---- score ------------------------------------------------------------------------
def latest_pre_lock_capture(dg, stamp=None):
    base = os.path.join(PRO, str(int(dg)))
    stamps = sorted(os.listdir(base)) if os.path.isdir(base) else []
    if stamp:
        assert stamp in stamps, f"no capture {stamp} for draft group {dg}"
        stamps = [stamp]
    for st in reversed(stamps):
        info = json.load(open(os.path.join(base, st, "capture.json")))
        if info.get("pre_lock"):
            return os.path.join(base, st), info
    raise SystemExit(f"draft group {dg}: no capture stamped before lock; prospective scoring refuses a post-lock capture")


def score(cid, dg, week, stamp=None, log=print):
    from research import provenance
    cid, dg, week = int(cid), int(dg), int(week)
    frozen = json.load(open(FROZEN))
    capdir, info = latest_pre_lock_capture(dg, stamp)
    slate, details = s6_capture.replay(dg, capdir=capdir)
    detail = details.get(str(cid))
    detail_source = "pre-lock capture"
    if detail is None:
        # payout metadata only (name, capacity, prizes); no field-model input comes from it
        import dk
        detail = dk.contest_detail(cid)
        detail_source = f"fetched at scoring time ({_utc()}): payout metadata only, no field-model input"
        details[str(cid)] = detail
        with open(os.path.join(capdir, f"detail_{cid}_at_scoring.json"), "w") as fh:
            json.dump(detail, fh, indent=1, sort_keys=True)
    spec = {"dg": dg, "week": week, "roster": None, "label": detail.get("name") or str(cid),
            "captured": f"pre-lock capture {info['captured_utc']}, {info['hours_to_lock']} h before the earliest contest start"}
    # s6_capture.replay inside build() reads the capture's manifest; the detail
    # patched above is only for the artifact's contest block
    bd = SS.build(cid, spec, log, capdir=capdir, feed_dir=capdir)
    bd["detail"] = detail
    B = bd["B2"]
    thetas = {a["name"]: (np.asarray(frozen[a["source"]]["theta"], dtype=np.float64), a["arm"]) for a in ARMS if "source" in a}
    graded = {}
    for name, (th, arm) in thetas.items():
        graded[name] = SS.grade(B, th, arm, bd["players"], bd["idx"], bd["pool"], bd["std"], full=True)
        graded[name]["rank_bins_by_this_utility"] = SS.rank_bins(B, th)
        graded[name]["frozen_theta"] = [float(v) for v in th]
    prod = SB.production_on_own_universe(bd, with_mle=False, feed_dir=capdir, clear_injuries=False)
    th_ps, th_p = thetas["projection_plus_salary_frozen_primary"][0], thetas["projection_only_frozen"][0]
    delta = (B.logp(th_ps) - B.logp(th_p))[bd["entry_rows"]]
    boot = SS.cluster_bootstrap(delta, bd["entry_users"], np.random.default_rng(SEED))
    boot["conditional_on"] = ("the frozen parameters, fitted on the three retrospective contests before this one existed; "
                              "only this contest's users are resampled")
    nll = {name: graded[name]["likelihood"]["nll_per_entry_nats"] for name in graded}
    principal = {"criterion": PRINCIPAL,
                 "delta_nll_primary_minus_projection_only": round(nll["projection_plus_salary_frozen_primary"] - nll["projection_only_frozen"], 5),
                 "delta_nll_secondary_minus_projection_only": round(nll["projection_plus_salary_frozen_secondary"] - nll["projection_only_frozen"], 5),
                 "delta_nll_salary_only_minus_projection_only": round(nll["salary_only_diagnostic"] - nll["projection_only_frozen"], 5),
                 "user_cluster_bootstrap_primary": {**boot, "sign": "positive = projection+salary assigns higher probability to this contest's entries"}}
    out = {"meta": {"stage": "S7 prospective: the frozen field family scored on a contest captured before lock; nothing fitted",
                    "contest": cid, "label": spec["label"], "draft_group": dg, "week": week,
                    "capture": {**info, "directory": os.path.relpath(capdir, ROOT)}, "contest_detail_source": detail_source,
                    "frozen": {"file": os.path.relpath(FROZEN, ROOT), "sha256": _sha(FROZEN), "frozen_utc": frozen["frozen_utc"]},
                    "fit_free": True, "fits_called": 0, "rules": list(RULES),
                    "provenance": provenance.stamp(worlds=None, model="frozen parameters; nothing fitted", seed=SEED)},
           "contest": {k: detail.get(k) for k in ("id", "name", "entry_fee", "max_entries", "prize_pool", "first_prize", "places_paid", "draft_group_id", "starts")},
           "support": bd["support"], "excluded_entries_salary_profile": bd["excluded_salary"],
           "universe": {"players": len(bd["players"]), "legal_lineups": int(len(bd["idx"])), "players_without_projection": bd["dropped"],
                        "salary_left_universe_mean_dollars": round(1000 * float(bd["s"].mean()), 1), "salary_left_observed_mean_dollars": round(1000 * B.sbar, 1),
                        "roster": {**sd_board.roster_stamp(capdir, week=week), "injury_statuses": bd["roster_stamp"]["injury_statuses"]}},
           "saturated_nll_per_entry_nats": round(B.saturated(), 5), "uniform_nll_per_entry_nats": round(float(np.log(len(bd["idx"]))), 5),
           "production_field": prod, "arms": graded, "principal": principal,
           "inputs": {"draftkings": s6_capture.dk_hashes(dg, capdir=capdir), "sleeper_feeds": s6_capture.sleeper_hashes(capdir),
                      "standings": json.load(open(os.path.join(F.STANDINGS, "manifest.json")))["files"].get(f"contest-standings-{cid}.csv.gz")}}
    path = os.path.join(DATA, f"s7_prospective_{cid}.json")
    with open(path, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    row = summary_row(out)
    ledger = json.load(open(LEDGER)) if os.path.exists(LEDGER) else {"meta": {"stage": "S7 prospective ledger: one row per contest scored with the frozen field family",
                                                                              "frozen": os.path.relpath(FROZEN, ROOT), "rules": list(RULES)}, "contests": []}
    ledger["contests"] = [r for r in ledger["contests"] if r["contest"] != cid] + [row]
    ledger["contests"].sort(key=lambda r: r["contest"])
    ledger["meta"]["pre_lock_contests_scored"] = len(ledger["contests"])
    ledger["meta"]["feature_selection_round_allowed"] = len(ledger["contests"]) >= MIN_CONTESTS_BEFORE_NEXT_FEATURE
    with open(LEDGER, "w") as fh:
        json.dump(ledger, fh, indent=1, sort_keys=True)
    log(f"[S7p] {cid} {spec['label']}: representable {bd['support']['representable_pct']}%; NLL projection-only {nll['projection_only_frozen']}, "
        f"primary {nll['projection_plus_salary_frozen_primary']}, salary-only {nll['salary_only_diagnostic']}; principal delta "
        f"{principal['delta_nll_primary_minus_projection_only']} (95% {boot['ci95_user_cluster']}); chalk rank primary "
        f"{graded['projection_plus_salary_frozen_primary']['ordering']['real_chalk_model_rank']}")
    return out


def summary_row(out):
    g = out["arms"]; p = out["principal"]
    pri = g["projection_plus_salary_frozen_primary"]; po = g["projection_only_frozen"]
    return {"contest": out["meta"]["contest"], "label": out["meta"]["label"], "draft_group": out["meta"]["draft_group"], "week": out["meta"]["week"],
            "capture_stamp": out["meta"]["capture"]["capture_stamp"], "hours_to_lock": out["meta"]["capture"]["hours_to_lock"],
            "active_entries": out["support"]["active_entries"], "representable_pct": out["support"]["representable_pct"],
            "nll_per_entry": {name: g[name]["likelihood"]["nll_per_entry_nats"] for name in g},
            "production_field_on_own_support": {"support_pct": out["production_field"]["support_pct_of_active"], "nll": out["production_field"]["nll_per_entry_nats_on_its_support"]},
            "principal_delta": p["delta_nll_primary_minus_projection_only"], "principal_ci95_user_cluster": p["user_cluster_bootstrap_primary"]["ci95_user_cluster"],
            "secondary_delta": p["delta_nll_secondary_minus_projection_only"], "salary_only_delta": p["delta_nll_salary_only_minus_projection_only"],
            "real_chalk_rank": {"projection_only": po["ordering"]["real_chalk_model_rank"], "primary": pri["ordering"]["real_chalk_model_rank"]},
            "observed_mass_in_top_1000_pct": {"projection_only": po["ordering"]["observed_mass_in_model_top_k_pct"]["1000"], "primary": pri["ordering"]["observed_mass_in_model_top_k_pct"]["1000"]},
            "top_share_pct": {"observed": pri["shape"]["max_share_pct"]["observed"], "primary": pri["shape"]["max_share_pct"]["model"]},
            "effective_lineups": {"observed": pri["shape"]["effective_lineups_inverse_sum_p2"]["observed"], "primary": pri["shape"]["effective_lineups_inverse_sum_p2"]["model"]},
            "entries_in_51plus_copy_lineups_pct": {"observed": pri["shape"]["duplication"]["51-plus"]["observed_entry_share_pct"], "primary": pri["shape"]["duplication"]["51-plus"]["model_entry_share_pct"]},
            "salary_left": {"share_ge_1000_pct": pri["out_of_objective"]["salary_left_share_ge_1000_pct"],
                            "share_exactly_0_pct": {"observed": pri["salary_left"]["observed"]["exactly_0"], "primary": pri["salary_left"]["model"]["exactly_0"]}},
            "cpt_position_mix_l1_pp": {"projection_only": po["out_of_objective"]["cpt_position_mix_pct"]["l1_pp"], "primary": pri["out_of_objective"]["cpt_position_mix_pct"]["l1_pp"]},
            "five_one_pct": {"observed": pri["out_of_objective"]["structure_pct"]["observed"].get("5-1", 0.0), "primary": pri["out_of_objective"]["structure_pct"]["model"].get("5-1", 0.0)}}


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "freeze":
        freeze()
    elif a and a[0] == "upcoming":
        upcoming()
    elif a and a[0] == "capture" and len(a) >= 3:
        rest = a[2:]
        cids, note, dgs = [], "", []
        i = 0
        while i < len(rest):
            if rest[i] == "--contest":
                cids.append(int(rest[i + 1])); i += 2
            elif rest[i] == "--note":
                note = rest[i + 1]; i += 2
            else:
                dgs.append(int(rest[i])); i += 1
        capture(dgs, int(a[1]), contest_ids=cids, note=note)
    elif a and a[0] == "standings" and len(a) == 3:
        standings(int(a[1]), a[2])
    elif a and a[0] == "score" and len(a) >= 4:
        stamp = a[a.index("--stamp") + 1] if "--stamp" in a else None
        score(int(a[1]), int(a[2]), int(a[3]), stamp=stamp)
    else:
        print(__doc__.split("    python3")[0].rstrip())
        print("    python3 -m research.s7_prospective freeze | upcoming | capture <week> <dg> [dg ...] [--contest <cid>] [--note ...] "
              "| standings <cid> <export.zip|.csv> | score <cid> <dg> <week> [--stamp <stamp>]")
