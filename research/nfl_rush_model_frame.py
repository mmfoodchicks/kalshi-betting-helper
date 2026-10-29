"""The RB / QB rushing model's frame: one row per RB- or QB-game of the
rushing data layer's dataset (research/data/nfl_rush/{rb,qb}_games_2023_2026.csv.gz),
with every feature computed by PRODUCTION's adapter (nfl_rush.snapshot_blocks /
features / qb_profile) on the data layer's own as-of snapshot
(nfl_rush_profiles.build as of the row's week). Also: Sleeper's lines (revised
for 2023-2025; the FROZEN pre-kickoff capture for 2026), the official
outcomes, the QB's component outcomes, the pre-game lines and comparables.
RESEARCH (numpy-free; nothing in production reads the frame).

    export DEEP_CACHE_DIR=<nfl_matchup_<season>.pkl + nfl_rush_aux_<season>.pkl 2019-2026> VIGIL_NO_BG=1
    python3 -m research.nfl_rush_model_frame --k fit_only --out <work>/rush_frame_val.csv.gz      # 2023-2025, 2018-2024 k
    python3 -m research.nfl_rush_model_frame --k pooled --out <work>/rush_frame_test.csv.gz \\
        --sleeper <dir with proj_2026_<w>.json> --props props_w06.json ...                        # 2023-2026, 2018-2025 k

Building the frame reads no outcome to make a feature, except the
comparables, which read the player's own PAST games (strictly earlier weeks)
exactly as production will. The outcome columns are written beside the
features for the rule's scoring; nothing here scores them.

Definitions (the draft rule's Q1):
- the pre-game line is nflverse's closing spread_line / total_line (game
  script and the kneels; never the realised margin);
- the outcome is Sleeper's box where the player has one (Kalshi settles on
  the official line; the data layer matched it on 100% of 2023-25 QB-game
  attempts), else the pbp's official rushes (designed + sneaks + scrambles +
  kneels + aborted snaps; sacks are not rushes);
- a frozen 2026 line is the latest capture whose content time is before
  kickoff (research/nfl_matchup_frame.choose_frozen, the first model's).
"""
import argparse
import collections
import csv
import datetime
import gzip
import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
os.environ.setdefault("VIGIL_NO_BG", "1")
import nfl_rush as NR  # noqa: E402  (after the path and VIGIL_NO_BG: production's adapter)

RUSH_DATA = os.path.join(HERE, "data", "nfl_rush")
DATASET = {"RB": os.path.join(RUSH_DATA, "rb_games_2023_2026.csv.gz"),
           "QB": os.path.join(RUSH_DATA, "qb_games_2023_2026.csv.gz")}
RELIABILITY = os.path.join(RUSH_DATA, "reliability.json")
STORE_SEASONS = range(2019, 2027)          # 2023's priors reach 2021; its comparables' vectors 2021
VECTOR_SEASONS = range(2021, 2027)
SL_FIELDS = ("rush_att", "rush_yd", "rush_td", "rec_tgt", "rec", "rec_yd", "rec_td", "pass_att")
# The dataset columns the frame reads, by NAME and by role (the one place to change when the data
# layer renames one). No feature reads the dataset: every feature comes from the snapshot API.
ID_COLS = ("season", "week", "game_id", "team", "opp", "home", "gsis", "sleeper_id", "name", "status")
BASELINE_COLS = tuple(f"sl_proj_{f}" for f in SL_FIELDS)      # Sleeper's REVISED line: the baseline, never a feature
OUTCOME_COLS = ("sl_act_gp", "sl_act_rush_att", "sl_act_rush_yd", "sl_act_rec_tgt", "sl_act_rec", "sl_act_rec_yd",
                "act_rush", "act_rush_yds", "act_tgt", "act_rec", "act_rec_yds", "act_rush_td", "act_rec_td",
                "act_rush_long") + tuple(f"act_{c}{x}" for c in ("des", "sneak", "scr", "kneel", "abort")
                                         for x in ("", "_yds"))      # QB rows also carry act_db, read where present
OPTIONAL_OUTCOME_COLS = ("act_db",)          # QB rows only: his dropbacks (a pbp appearance)
PLAYED_COLS = ("act_played", "played")       # an outcome (renamed act_played by the rebuild); written as y_played only
# The ONLY dataset columns that decide whether a row is fitted or scored (Q1): Sleeper's line (the
# universes, nfl_rush.in_universe) and Sleeper's box gp or a pbp rush / target / dropback (played,
# y_cond_played). act_played and act_any_snap carry PFR snaps (a back gets a row, and act_played 1,
# from PFR offensive snaps alone: 48 of 618 RB rows in 2026), so neither ever selects a row; a guard
# holds every column here to an unrestricted tag in the dataset's schema.
SELECTION_COLS = BASELINE_COLS + ("sl_act_gp", "act_rush", "act_tgt") + OPTIONAL_OUTCOME_COLS
NEVER_READ = ("in_sleeper", "sl_qb1")        # post-week Sleeper fields: read by nothing here
# the comparables and the participation view are PRODUCTION's (nfl_rush): one definition, so the
# board computes exactly what this frame measured
VEC, COMP_C, COMP_K = NR.VEC, NR.COMP_C, NR.COMP_K
_vec, standardize, comparables, game_logs = NR.vec_of, NR.standardize, NR.comparables, NR.game_logs


def log(msg):
    print(msg, flush=True)


def _f(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def team_margin(g, team):
    """The team-side PRE-GAME expected margin: nflverse's closing spread_line is positive when the
    HOME team is favoured, so the home side reads +spread and the away side -spread; None without a
    line. Never the realised score (C2)."""
    sp = _f(g.get("spread"))
    if sp is None:
        return None
    return sp if g.get("home") == team else -sp


def kick_ts(g):
    """The scheduled kickoff as epoch seconds: the store's "kick" string is EASTERN time with no zone
    (America/New_York, DST-aware); reading it as UTC would admit captures 4-5 hours after kickoff."""
    from zoneinfo import ZoneInfo
    try:
        dt = datetime.datetime.strptime(g.get("kick") or "", "%m/%d/%y, %H:%M:%S")
    except ValueError:
        return None
    return int(dt.replace(tzinfo=ZoneInfo("America/New_York")).timestamp())


def use_k(mode, scratch):
    """Point the data layer at the k table the frame reads: 'pooled' = the
    shipped 2018-2025 table, 'fit_only' = its 2018-2024 block (no k the 2025
    validation reads has seen 2025)."""
    import nfl_rush_profiles as RP
    if mode == "pooled":
        RP.reload_k(RELIABILITY)
        return {"mode": "pooled", "source": "research/data/nfl_rush/reliability.json k (pooled 2018-2025)",
                "file": "research/data/nfl_rush/reliability.json"}
    with open(RELIABILITY, encoding="utf-8") as fh:
        rel = json.load(fh)
    p = os.path.join(scratch, "k_fit_only_rush.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump({"k": rel["k_fit_only"]["k"], "source": "reliability.json k_fit_only (2018-2024)"}, fh)
    RP.reload_k(p)
    return {"mode": "fit_only", "source": "research/data/nfl_rush/reliability.json k_fit_only (2018-2024)", "file": p}


def dataset_rows(seasons):
    """The dataset's rows for `seasons` (DATASET: the registered dataset, or the prospective one a
    `--dataset` directory names), refusing a dataset that lacks a column the frame reads by name (a
    rename must fail loudly, never read blank)."""
    out = []
    for pos, path in DATASET.items():
        with gzip.open(path, "rt", newline="") as fh:
            rd = csv.DictReader(fh)
            have = set(rd.fieldnames or ())
            lack = [c for c in ID_COLS + BASELINE_COLS + OUTCOME_COLS if c not in have]
            if lack or not (set(PLAYED_COLS) & have):
                raise SystemExit(f"[frame] {os.path.basename(path)} lacks {lack or list(PLAYED_COLS)}")
            for r in rd:
                if int(r["season"]) in seasons:
                    r["pos"] = pos
                    out.append(r)
    return out


def availability_view(stores, auxes, season):
    """(stores, auxes) as production could have read them during `season`: the
    season's own participation removed (nfl_rush.availability_view, whose
    docstring says why; the board reads the same view)."""
    return NR.availability_view(stores, auxes, season)


# the code a frame's numbers come from: hashed at build time into its meta, so registration can
# refuse a frame built on code that has since changed (the data layer was rebuilt under one)
BUILD_CODE = ("nfl_rush.py", "prop_dist.py", "research/nfl_rush_model_frame.py", "research/nfl_rush_model_fit.py",
              "research/nfl_rush_model_rule.py", "nfl_rush_data.py", "nfl_rush_profiles.py", "research/nfl_rush_dataset.py",
              "nfl_matchup_data.py", "nfl_matchup_profiles.py", "nfl_matchup.py", "research/nfl_matchup_frame.py",
              "research/nfl_matchup_fit.py", "kalshi_nfl.py", "deep_cache.py",
              "kalshi.py", "research/nfl_matchup_dataset.py")      # = research.nfl_rush_model_fit.REG_CODE (a guard)


def code_shas():
    import hashlib
    out = {}
    for f in BUILD_CODE:
        with open(os.path.join(REPO, f), "rb") as fh:
            out[f] = hashlib.sha256(fh.read()).hexdigest()
    return out


def input_shas(names):
    """{name: sha256 or None} for a frame's inputs: a repo-relative path, an absolute path, or
    "deep:<key>" (a deep-store pickle, DEEP_CACHE_DIR)."""
    import hashlib
    import deep_cache
    out = {}
    for n in names:
        path = deep_cache._path(n[5:]) if n.startswith("deep:") else (n if os.path.isabs(n) else os.path.join(REPO, n))
        try:
            with open(path, "rb") as fh:
                out[n] = hashlib.sha256(fh.read()).hexdigest()
        except OSError:
            out[n] = None
    return out


def check_inputs(stores, auxes, seasons):
    """Refuse (SystemExit) unless every season a snapshot reads -- the
    comparables' first vector season less the layer's BACK, through the last
    season built -- has its matchup store AND an aux built against that very
    store (nfl_rush_data.aux_matches) and COMPLETE (aux_complete). Without the aux the layer still
    answers, with PFR, injuries, snaps and NGS silently blank: the data
    layer's rebuild bumped the aux SCHEMA, and a frame on the old aux read
    pressure, OL starters out and contact as missing on every row."""
    import nfl_rush_data as RD
    import nfl_rush_profiles as RP
    need = range(min(VECTOR_SEASONS) - RP.BACK, max(int(x) for x in seasons) + 1)
    bad = [s for s in need if s not in stores or not RD.aux_matches(auxes.get(s), stores[s])]
    if bad:
        raise SystemExit(f"[frame] no matchup store or no aligned rushing aux (schema {RD.SCHEMA}) for {bad}")
    # an aux built without a source its season should have (RD.aux_complete: a failed or dropped
    # download) answers with that source blank -- the same silent failure as no aux at all
    short = [s for s in need if not RD.aux_complete(auxes.get(s))]
    if short:
        raise SystemExit(f"[frame] the rushing aux is INCOMPLETE for {short} (nfl_rush_data.aux_complete)")
    return list(need)


def fitted_paths():
    """{(block, path)} every fitted feature and the comparables' vector read."""
    import nfl_rush as NR
    out = {(b, p) for f in NR.fitted_features() for k, b, p in NR.spec_leaves(NR.FEATURES[f]) if k != "ext"}
    return out | {(b, p) for b, p, _how in VEC}


def absent_paths(seen):
    """The fitted inputs no row's block carried as a key (`seen`: {(block, path)} present
    on some row): an input the data layer has not landed, or has renamed. A frame with one
    is never registered (the feature would fit on a column of zeros)."""
    return sorted(fitted_paths() - set(seen))


CAPTURE_GLOBS = ("research/data/feeds/proj_2026_*.json", "research/data/prospective/*/*/proj_2026_*.json")


def capture_fetched(path):
    """The UTC epoch a DraftKings-slate capture says it was fetched (`capture.json` `captured_utc`
    beside it, "YYYY-MM-DD HH:MM:SS UTC"), or None when the directory holds no such record."""
    import calendar
    meta = os.path.join(os.path.dirname(path), "capture.json")
    if not os.path.exists(meta):
        return None
    try:
        with open(meta, encoding="utf-8") as fh:
            got = (json.load(fh) or {}).get("captured_utc")
        return float(calendar.timegm(time.strptime(got, "%Y-%m-%d %H:%M:%S UTC"))) if got else None
    except (OSError, ValueError, TypeError) as e:
        log(f"[frame] capture record {meta}: {e!r}")
        return None


def frozen_lines(props_paths, sleeper_dir, roster_rows=(), seen=None):
    """{(week, sleeper id): [(content ts, {field: v}, source, team, opp)]} -- WEEK-AGNOSTIC (the
    review's B2 / M5): every capture `proj_2026_*.json` (any number of digits) in the repo's feeds,
    the prospective captures and `sleeper_dir`; every props-log file given (`nfl_props_2026_wNN`),
    each record's normalised name mapped to a Sleeper id through THAT week's own captures, else
    the dataset's roster (name, team -> sleeper_id), else any week's captures. A back first seen
    after week 7 is therefore kept. The first model's reader stopped its name map at week 7 and
    its glob at one digit."""
    import glob
    import kalshi_nfl
    out = collections.defaultdict(list)
    caps = sorted({p for g in CAPTURE_GLOBS for p in glob.glob(os.path.join(REPO, g))}
                  | set(glob.glob(os.path.join(sleeper_dir or "/nonexistent", "proj_2026_*.json"))))
    name_week, name_any = collections.defaultdict(dict), {}
    for p in caps:
        if seen is not None:                 # every capture read, and its fetch record, is a frame input
            seen.append(p)
            if os.path.exists(os.path.join(os.path.dirname(p), "capture.json")):
                seen.append(os.path.join(os.path.dirname(p), "capture.json"))
        try:
            with open(p, encoding="utf-8") as fh:
                rows = json.load(fh)
        except (OSError, ValueError) as e:
            log(f"[frame] capture {p}: {e!r}")
            continue
        rel = os.path.relpath(p, REPO) if p.startswith(REPO) else os.path.basename(p)
        fetched = capture_fetched(p)
        for cr in rows:                    # a Sleeper capture row (not a dataset row)
            pl = cr.get("player") or {}
            if (pl.get("position") or "") not in ("QB", "RB", "FB", "WR", "TE"):
                continue
            pid, wk = str(cr.get("player_id")), int(cr.get("week") or 0)
            nm = kalshi_nfl._norm(f"{pl.get('first_name', '')} {pl.get('last_name', '')}")
            name_week[wk][(nm, cr.get("team"))] = pid
            name_any[(nm, cr.get("team"))] = pid
            st = cr.get("stats") or {}
            proj = {k: float(st[k]) for k in SL_FIELDS if st.get(k) is not None}
            if proj and cr.get("updated_at"):
                # the content time: Sleeper's updated_at, and no earlier than the capture's own fetch
                # where the capture says when it was fetched -- a row counts only when BOTH precede
                # kickoff (the rule review's m7)
                ts = max(cr["updated_at"] / 1000.0, fetched or 0.0)
                out[(wk, pid)].append((ts, proj, rel, cr.get("team"), cr.get("opponent")))
    roster = {(kalshi_nfl._norm(dr["name"] or ""), dr["team"]): dr["sleeper_id"] for dr in roster_rows
              if dr.get("sleeper_id")}
    for p in props_paths:
        with open(p, encoding="utf-8") as fh:
            lg = json.load(fh)
        wk = int(lg["week"])
        for nm, rec in (lg.get("sleeper") or {}).items():
            st = rec.get("stats") or {}
            key = (nm, rec.get("team"))
            pid = name_week[wk].get(key) or roster.get(key) or name_any.get(key)
            if not pid or not st or rec.get("pos") not in ("QB", "RB", "FB", "WR", "TE"):
                continue
            proj = {k: float(st[k]) for k in SL_FIELDS if st.get(k) is not None}
            if proj:
                out[(wk, str(pid))].append((float(rec.get("ts") or 0.0), proj, os.path.basename(p) + " (props log)",
                                            rec.get("team"), None))
    return out


def choose_frozen(cands, kick):
    """The latest capture whose CONTENT time is strictly before kickoff, or None."""
    ok = [c for c in cands if kick and c[0] < kick]
    return max(ok, key=lambda c: c[0]) if ok else None


def identity_of(r):
    """The row's identity: who, which game, his pre-game injury-report status.
    Never an outcome, a post-week Sleeper field or a dataset feature."""
    return {k: r[k] for k in ID_COLS + ("pos",)}


def outcomes_of(r):
    """The row's outcomes, every one y_-prefixed (the frame reader drops
    them for any season the hold forbids). Sleeper's box where the player has
    one, else the pbp's official rushes; a player with neither scores 0."""
    box = r.get("sl_act_gp") not in ("", None)
    played = next((r[c] for c in PLAYED_COLS if c in r), None)
    gp = _f(r.get("sl_act_gp"))
    pbp_play = (_f(r.get("act_rush")) or 0.0) + (_f(r.get("act_tgt")) or 0.0) + \
        sum((_f(r.get(c)) or 0.0) for c in OPTIONAL_OUTCOME_COLS)
    y = {"y_played": played,
         "y_src": "sleeper_box" if box else ("pbp" if pbp_play > 0 else "did_not_play"),
         # Q1: scored CONDITIONAL ON PLAYING -- Sleeper's box shows gp >= 1, or the pbp credits him a
         # rush, a target or a dropback (never PFR snaps: act_played reads them)
         "y_gp": gp, "y_pbp_play": pbp_play,
         "y_cond_played": 1 if ((gp is not None and gp >= 1) or pbp_play > 0) else 0}
    pick = lambda sl, ac: _f(r.get(sl)) if box and r.get(sl) not in ("", None) else (_f(r.get(ac)) or 0.0)
    y["y_rush_att"] = pick("sl_act_rush_att", "act_rush")
    y["y_rush_yd"] = pick("sl_act_rush_yd", "act_rush_yds")
    y["y_rec_tgt"] = pick("sl_act_rec_tgt", "act_tgt")
    y["y_rec"] = pick("sl_act_rec", "act_rec")
    y["y_rec_yd"] = pick("sl_act_rec_yd", "act_rec_yds")
    y["y_rush_td"] = _f(r.get("act_rush_td")) or 0.0
    y["y_rec_td"] = _f(r.get("act_rec_td")) or 0.0
    y["y_long"] = _f(r.get("act_rush_long"))
    for c_ in ("des", "sneak", "scr", "kneel", "abort"):
        y[f"y_{c_}"] = _f(r.get(f"act_{c_}")) or 0.0
        y[f"y_{c_}_yds"] = _f(r.get(f"act_{c_}_yds")) or 0.0
    return y


def rel(path):
    """A frame input's name: repo-relative when it lives in the repository (the registration and the
    reviewers re-derive it there), absolute only when it does not."""
    ap_ = os.path.abspath(path)
    return os.path.relpath(ap_, REPO).replace(os.sep, "/") if ap_.startswith(REPO + os.sep) else ap_


def box_files(sleeper_dir, weeks):
    """Sleeper's 2026 box files (stats_2026_<w>.json) the prospective dataset reads, with their sha256
    and modification time: `prospective` requires each window week's to postdate the window's last
    game by 48 hours (Q1)."""
    out = []
    for w in sorted(set(int(x) for x in (weeks or ()))):
        p = os.path.join(sleeper_dir or "/nonexistent", f"stats_2026_{w}.json")
        if os.path.exists(p):
            out.append({"week": w, "path": rel(p), "sha256": input_shas([os.path.abspath(p)])[os.path.abspath(p)],
                        "mtime_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(os.path.getmtime(p)))})
    return out


def provenance_fields(dataset_dir, frozen_only):
    """({dataset, dataset_realpath, dataset_meta_sha256}, the extra input keys) a frame records about its
    data's provenance: the dataset directory it read, by repo path and by REALPATH (a link resolved), and
    its meta.json's sha256; a prospective (--frozen-only) frame also reads the record of the 2026 store
    and aux's build (`prospective` holds all of them to the registration: provenance_refusal)."""
    import research.nfl_rush_model_fit as FIT
    ds_meta = os.path.abspath(os.path.join(dataset_dir, "meta.json"))
    return ({"dataset": rel(dataset_dir), "dataset_realpath": os.path.realpath(dataset_dir),
             "dataset_meta_sha256": input_shas([ds_meta])[ds_meta]},
            {f"deep:{FIT.STORES_KEY}"} if frozen_only else set())


def write_frame(out_path, rows):
    """The frame as gzip with a ZERO mtime and no file name in its header, so a rebuild of the same rows
    has the same sha256 (the re-check rebuilt the validation frame byte for byte except gzip's mtime).
    No rows refuses with a reason (Fable's B1: a dataset capped short of the window's weeks left none, and
    `rows[0]` raised an IndexError instead of saying why)."""
    import io
    if not rows:
        raise SystemExit(f"[frame] no rows to write to {out_path}: the dataset holds none for these seasons / weeks "
                         "(a prospective dataset must be built with --all-weeks; its meta.json lists the weeks it holds)")
    cols = list(rows[0].keys())
    with open(out_path, "wb") as raw:
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as gz:
            with io.TextIOWrapper(gz, encoding="utf-8", newline="") as fh:
                w_ = csv.DictWriter(fh, fieldnames=cols)
                w_.writeheader()
                for rec in rows:
                    w_.writerow({k: ("" if v is None else v) for k, v in rec.items()})


def build(out_path, kmode, seasons, sleeper_dir=None, props=(), scratch="/tmp", weeks=None, frozen_only=False,
          dataset_dir=None):
    global DATASET
    import nfl_rush as NR
    if dataset_dir:
        # the prospective dataset, built OUTSIDE the registered research/data/nfl_rush/ (Q1): a rebuild in
        # place would change registered bytes and every command would refuse
        DATASET = {"RB": os.path.join(dataset_dir, "rb_games_2023_2026.csv.gz"),
                   "QB": os.path.join(dataset_dir, "qb_games_2023_2026.csv.gz")}
    cap_files = []
    import nfl_matchup_data as D
    import nfl_rush_data as RD
    import nfl_rush_profiles as RP
    import nfl_matchup_profiles as P
    shas = code_shas()          # the modules are imported above and below; nothing reloads them during the build
    kinfo = use_k(kmode, scratch)
    if kmode == "fit_only":
        os.environ["NFLM_K"] = "fit_only"       # the first model's defense deviations on its 2018-2024 k too
    import nfl_matchup as M
    if M.K_MODE != ("fit_only" if kmode == "fit_only" else "pooled"):
        # nfl_matchup reads NFLM_K at import: a process that imported it earlier would silently keep
        # the pooled k for the first model's defense deviations (review m6)
        raise SystemExit(f"[frame] nfl_matchup.K_MODE is {M.K_MODE!r} for a {kmode} frame: run the build in a fresh process")
    t0 = time.time()
    stores = {s: D.load_season(s) for s in STORE_SEASONS}
    stores = {s: st for s, st in stores.items() if st}
    auxes = {s: RD.load_aux(s) for s in stores}
    check_inputs(stores, auxes, seasons)
    pos = P.positions_from_ids(D.load_ids())
    m1 = M.Ctx(stores, pos)
    m1.save_history = False
    logs = game_logs({s: st for s, st in stores.items() if s in VECTOR_SEASONS})
    rows = dataset_rows(set(seasons))
    if weeks:
        rows = [r for r in rows if int(r["week"]) in set(int(w) for w in weeks)]
    by_week = collections.defaultdict(list)
    for r in rows:
        by_week[(int(r["season"]), int(r["week"]))].append(r)
    frozen = {}
    if any(int(s) == 2026 for s in seasons):
        frozen = frozen_lines(list(props), sleeper_dir, dataset_rows({2026}), seen=cap_files)
    log(f"[frame] k {kinfo['mode']}; {len(rows)} rows; stores {sorted(stores)}; {time.time() - t0:.0f}s")
    hist = {}                 # (season, week, team) -> standardized vector as of that week (comparables)
    out, missing = [], collections.Counter()
    seen = set()              # (block, path) keys some row's blocks carried
    weeks_all = [(s, w) for s in VECTOR_SEASONS if s in stores for w in sorted({g["week"] for g in stores[s]["games"]})]
    for (s, w) in weeks_all:
        if s > max(seasons):
            break
        vs, va = availability_view(stores, auxes, s)        # the season's own participation never enters
        S = RP.build(vs, va, s, w, pos)
        teams = stores[s]["teams"]
        tb = NR.team_blocks(S, teams)
        lg = NR.league_means(tb)
        z = standardize({t: _vec(b) for t, b in tb.items()})
        for t, v in z.items():
            hist[(s, w, t)] = v
        wk_rows = by_week.get((s, w)) or []
        if not wk_rows:
            continue
        games = {g["id"]: g for g in stores[s]["games"]}
        m1def = {}
        for r in wk_rows:
            pid, team, opp, p_ = D.gsis_int(r["gsis"]), r["team"], r["opp"], r["pos"]
            blocks = dict(d=tb[opp]["d"], dq=tb[opp]["dq"], ol=tb[team]["ol"], tc=tb[team]["tc"])
            if p_ == "RB":
                blocks["p"] = RP.flatten(S.rb(pid, team))
                ro = S.rb_room_out(team, exclude=pid) or {}
            else:
                q = S.qb(pid, team)
                blocks["q"], blocks["q_totals"], blocks["q_games"] = RP.flatten(q), q.get("totals") or {}, q.get("games") or {}
                ro = {}
            g = games.get(r["game_id"]) or {}
            margin, tot = team_margin(g, team), _f(g.get("total"))
            if opp not in m1def:
                try:
                    m1def[opp] = M.defense_block(m1, s, w, opp)
                except (KeyError, TypeError, ValueError, ZeroDivisionError) as e:
                    log(f"[frame] first-model defense block {s} wk{w} {opp}: {e!r}")
                    m1def[opp] = {}
            db = m1def[opp]
            ext = {"margin7": None if margin is None else margin / 7.0,
                   "total_c": None if tot is None else (tot - 44.0) / 10.0,
                   "room_out": _f(ro.get("value")) if p_ == "RB" else None,
                   "m1_pass_epa": _f((db.get("pass_epa") or {}).get("x")), "m1_catch_all": _f((db.get("catch_all") or {}).get("x")),
                   "m1_man": _f((db.get("man") or {}).get("x")), "m1_sh_frac": _f((db.get("sh_frac") or {}).get("x"))}
            ov = hist.get((s, w, opp))
            if p_ == "RB":
                ext["comp_rush_yd"], n1 = comparables(hist, logs, pid, s, w, ov, "rush_yd")
                ext["comp_rec_yd"], n2 = comparables(hist, logs, pid, s, w, ov, "rec_yd")
            else:
                ext["comp_qb_rush_yd"], n1 = comparables(hist, logs, pid, s, w, ov, "rush_yd")
                n2 = None
            for b_, blk_ in blocks.items():
                if isinstance(blk_, dict):
                    seen.update((b_, k_) for k_ in blk_)
            feats = NR.features(blocks, lg, ext)
            rec = identity_of(r)
            rec["kick_ts"] = kick_ts(g)
            rec["margin"], rec["total"] = margin, tot
            rec["k_mode"] = kinfo["mode"]
            rec["features_through"] = f"{s} week {w - 1}" if w > 1 else f"{s - 1} season"
            for fld, col in zip(SL_FIELDS, BASELINE_COLS):
                rec[f"sl_{fld}"] = _f(r.get(col))
            # 2026: the frozen pre-kickoff capture (never the revised line)
            rec["fz_src"], rec["fz_lead_h"] = "", None
            for fld in SL_FIELDS:
                rec[f"fz_{fld}"] = None
            if s == 2026 and frozen:
                c = choose_frozen(frozen.get((w, r.get("sleeper_id") or ""), []), rec["kick_ts"])
                if c:
                    rec["fz_src"] = c[2]
                    rec["fz_lead_h"] = round((rec["kick_ts"] - c[0]) / 3600.0, 2)
                    for fld in SL_FIELDS:
                        rec[f"fz_{fld}"] = c[1].get(fld)
            rec.update(outcomes_of(r))        # written, never scored here
            # the QB's own shares (the data layer's decomposition)
            qp = NR.qb_profile(blocks) if p_ == "QB" else None
            for kk in ("designed_share", "sneak_share", "scramble_share", "ypa_designed", "ypa_scramble"):
                rec[f"qp_{kk}"] = (qp or {}).get(kk)
            # research only (traincheck_frame's QB-type kernel arm): 1.5+ designed runs a game as of the week
            dpg = NR._num_or_none(((blocks.get("q") or {}).get("designed.per_game") or {}).get("value"))
            rec["qp_runner"] = None if p_ != "QB" else bool(dpg is not None and dpg >= 1.5)
            rec["n_comp1"], rec["n_comp2"] = n1, n2
            for n, v in feats.items():
                rec[f"f_{n}"] = v
                if v is None:
                    missing[(s, p_, n)] += 1
            rec["live_lag"] = ""        # Q1 (C1): the live board's provenance stamp; no stamp is logged yet
            if frozen_only and not rec["fz_src"]:
                continue
            out.append(rec)
        log(f"[frame] {s} week {w}: {len(wk_rows)} rows ({time.time() - t0:.0f}s)")
    write_frame(out_path, out)
    ds_dir = os.path.dirname(DATASET["RB"])
    ds_meta = os.path.join(ds_dir, "meta.json")
    import research.nfl_rush_model_fit as FIT
    prov, prov_inputs = provenance_fields(ds_dir, frozen_only)
    meta = dict({"rows": len(out), "k": {k_: v_ for k_, v_ in kinfo.items() if k_ != "file"}, "seasons": list(seasons),
                 "seconds": round(time.time() - t0, 1),
                 "participation": "prior seasons only (availability_view: the season's own masked)", "code": shas,
                 "inputs": input_shas(sorted({rel(DATASET[p_]) for p_ in DATASET} | {rel(ds_meta)}
                                             | {"research/data/nfl_rush/reliability.json", "research/data/nfl_rush/schema.json",
                                                "research/data/nfl_matchup/reliability.json",
                                                "research/data/nfl_matchup_model/k_fit_only.json"}
                                             | {f"deep:nfl_matchup_{s_}" for s_ in stores}
                                             | {f"deep:nfl_rush_aux_{s_}" for s_ in stores} | {"deep:nfl_matchup_ids"}
                                             | {rel(p_) for p_ in props} | {rel(p_) for p_ in cap_files} | prov_inputs))},
                **prov)
    meta.update({
            "box_files": box_files(sleeper_dir, weeks),
            "versions": FIT.versions(),
            "weeks": list(weeks) if weeks else None, "frozen_only": bool(frozen_only),
            "built_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "paths_absent": [list(x) for x in absent_paths(seen)],
            "missing": {f"{s}|{p}|{n}": c for (s, p, n), c in sorted(missing.items())}})
    with open(out_path + ".meta.json", "w") as fh:
        json.dump(meta, fh, indent=1)
    log(f"[frame] {len(out)} rows -> {out_path} ({time.time() - t0:.0f}s); fitted inputs absent: "
        f"{meta['paths_absent'] or 'none'}")
    return meta


# ---- the rule's Q3a, generated (the report's block must equal this text; a guard checks) -----------
FAMILY_LABEL = {"rb_rush": "RB rushing (carries, rushing yards; rush + rec and longest rush derived)",
                "qb_rush": "QB rushing (attempts, rushing yards; longest rush derived)",
                "rb_rec": "RB receiving (receptions, receiving yards)", "rb_td": "RB anytime TD",
                "qb_td": "QB anytime TD (rushing + receiving)"}


def spec_text(spec):
    k = spec[0]
    if k in ("dev", "log", "lg", "val"):
        f = {"dev": "{b}.{p} (value - prior)", "log": "log {b}.{p}", "lg": "{b}.{p} - league mean", "val": "{b}.{p}"}[k]
        return f.format(b=spec[1], p=spec[2])
    if k == "ext":
        return f"[{spec[1]}]"
    if k == "diff":
        return f"({spec_text(spec[1])} - {spec_text(spec[2])})"
    if k == "prod":
        return f"{spec_text(spec[1])} x {spec_text(spec[2])}"
    if k == "sum":
        return "sum: " + " + ".join(spec_text(x) for x in spec[1])
    raise ValueError(k)


def feature_lists_md():
    """The rule's Q3a block from nfl_rush.FEATURES / GROUPS (what the frame
    computes is what the rule says, by construction)."""
    import nfl_rush as NR
    lines = ["#### Q3a. The feature lists (frozen at hashing; generated by `research.nfl_rush_model_frame.feature_lists_md`"
             " from `nfl_rush.FEATURES` / `GROUPS`)", "",
             "Frame column = `f_<feature>`. Blocks: `p` rb(pid, team), `q` qb(pid, team), `d` defense_run(opp), "
             "`dq` defense_qb(opp), `ol` offense_line(team), `tc` team_context(team) (`research/reports/nfl_rush_data.md` "
             "§0.3 paths), each from the data layer's snapshot as of the week through the frame's `availability_view` "
             "(the season's own participation never enters: Q1). `[m1_*]` are the first model's "
             "`nfl_matchup.defense_block` deviations (`x`) for the opponent, prior seasons for coverage; `[margin7]` the "
             "team-side PRE-GAME expected margin / 7 and `[total_c]` (total - 44) / 10 (research: nflverse closing "
             "spread_line / total_line; production: the board's pre-game Kalshi line); `[room_out]` "
             "`rb_room_out(team, exclude=pid)`; `[comp_*]` the comparables (frame docstring). `dq.hit_sack_rate` is the "
             "pbp's sacks + QB hits per opponent dropback, in season, observed over what the QBs faced do elsewhere (a "
             "pressure proxy), and `ol.ol_listed_out` the team's offensive linemen listed Out / Doubtful on the week's "
             "report (injuries; None without a report). Every input's source is declared "
             "(`nfl_rush.PATH_SOURCE` / `EXT_SOURCE`) and is pbp, participation, FTN, injuries or the pre-game line: no "
             "PFR or PFR-snap field and no NGS field (Q10). Participation keeps the shipped first model's status: "
             "nflverse took its 2022-and-earlier records from NGS, so the 2023 and 2024 rows' prior-season reads are "
             "NGS-originated (Q10). A missing input makes the feature missing (a `sum` drops a missing part and is "
             "missing only when every part is), and a missing feature reads 0, the deviation's \"nothing known\"; the "
             "two non-negative counts (`cfp_ol_out`, `dec_room_out`, `nfl_rush.MEAN_FILL`) read their training mean "
             "instead. \"moves\": `count` the opportunity (carries / designed runs / scrambles / receptions), `eff` "
             "yards per attempt, `mean` the market's mean; QB terms also name their component (`nfl_rush.QB_COMPONENT`); "
             "before SHAPE is accepted every term acts on its market's mean (Q3b).", "",
             "**No feature reads a dataset column.** The dataset (`research/data/nfl_rush/{rb,qb}_games_2023_2026.csv.gz`) "
             "supplies, by name (`research.nfl_rush_model_frame.ID_COLS` / `BASELINE_COLS` / `OUTCOME_COLS` / "
             "`PLAYED_COLS`), only the row's identity and pre-game injury-report status, Sleeper's line (`sl_proj_*`: the "
             "BASELINE, never a feature) and the outcomes (`act_*`, `sl_act_*`, `act_played`; written to the frame as "
             "`y_*`, which the frame reader drops for every season the hold forbids). `in_sleeper` and `sl_qb1` "
             "(post-week Sleeper fields) are read by nothing. Every feature below is computed from the snapshot API.", ""]
    for fam, groups in NR.GROUPS.items():
        lines.append(f"- **{FAMILY_LABEL[fam]}**")
        for g, fs in groups.items():
            if g == "SHAPE":
                lines.append("  - SHAPE: no term -- the compound distribution in place of the rung-1 family, the terms "
                             "already accepted re-expressed on components (Q3b)")
                continue
            lab = (lambda f, a: f"{a}, {NR.QB_COMPONENT[f]}") if fam == "qb_rush" else (lambda f, a: a)
            lines.append(f"  - {g}: " + "; ".join(f"`{f}` ({lab(f, a)}) = {spec_text(NR.FEATURES[f])}" for f, a in fs))
    lines.append("")
    return "\n".join(lines)


def thresholds_md():
    """The rule's Q4 threshold table, generated from nfl_rush.THRESHOLDS (a guard checks the report holds
    exactly this text)."""
    import nfl_rush as NR
    label = {"rush_att": "carries / QB rushing attempts", "rush_yd": "rushing yards", "rec": "receptions",
             "rec_yd": "receiving yards", "rush_rec_yd": "rush + rec yards (derived)", "td": "anytime TD",
             "long_rush": "longest rush (derived)"}
    rows = ["| market | RB thresholds | QB thresholds |", "|---|---|---|"]
    for m in ("rush_att", "rush_yd", "rec", "rec_yd", "rush_rec_yd", "td", "long_rush"):
        rb = " / ".join(str(k) for k in NR.THRESHOLDS["RB"].get(m, ())) or "-"
        qb = " / ".join(str(k) for k in NR.THRESHOLDS["QB"].get(m, ())) or "-"
        rows.append(f"| {label[m]} | {rb} | {qb} |")
    return "\n".join(rows) + "\n"


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--k", choices=("pooled", "fit_only"), required=True)
    ap.add_argument("--seasons", nargs="*", type=int, default=[2023, 2024, 2025, 2026])
    ap.add_argument("--sleeper", default=None)
    ap.add_argument("--props", nargs="*", default=[])
    ap.add_argument("--weeks", nargs="*", type=int, default=None, help="the prospective frame: the window's weeks")
    ap.add_argument("--frozen-only", action="store_true", help="the prospective frame: rows with a frozen capture only")
    ap.add_argument("--dataset", default=None,
                    help="the prospective frame: the directory the prospective dataset was built to (never "
                         "research/data/nfl_rush/, whose files are registered)")
    a = ap.parse_args(argv)
    build(a.out, a.k, tuple(a.seasons), a.sleeper, a.props, scratch=os.path.dirname(os.path.abspath(a.out)),
          weeks=a.weeks, frozen_only=a.frozen_only, dataset_dir=a.dataset)


if __name__ == "__main__":
    main(sys.argv[1:])
