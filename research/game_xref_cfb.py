"""College game cross-reference replay (research/reports/game_xref.md). RESEARCH, read-only.

Replays the PRODUCTION college model week by week over 2024-2026:

  * the Massey ratings exactly as `cfb.inseason_ratings` fits them (the
    function here, `massey`, is checked against the module's own result at
    the shipped constants), from the season's completed games BEFORE the
    week, on both divisions' pools with the preseason prior `cfb.ratings`;
  * the board's raw number `p_raw` = Phi(model margin / 18) (`cfb_board`'s
    p_home_raw) and the drive engine's own game (`cfb._rates_for` +
    `cfb._play`) for the win probability, expected points and total the
    cards show (`p_eng`, `eh`, `ea`, `t_eng`);
  * the board's PRICED headline: the engine run with the level blended 25%
    model / 75% market margin (`cfb_board._ANCHOR_W_DEFAULT`; model_trust
    has no college weight), `p_blend`, with ESPN BET's close standing in for
    Kalshi's spread midpoint;

beside ESPN BET's close (spread as the home line, total, moneylines from the
core API's stored odds record), ESPN's FPI predictor (gameProjection,
teamPredPtDiff), and the actual score. Every row carries the inputs an
offence/defence rating needs (the report stage fits that candidate by the
pre-registered rule in the report).

usage:
  VIGIL_NO_BG=1 python3 -m research.game_xref_cfb replay <raw dir> [--n 1000] [--out rows.json]
    <raw dir> holds cfb/sb_{season}_w{week}_g{80|81}.json scoreboards and
    cfb_core/odds/{id}.json, cfb_core/pred/{id}.json; ESPN standings are
    fetched once and cached under <raw dir>/espn_cache.
"""
import hashlib
import json
import os
import random
import sys
import time
import zlib
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("VIGIL_NO_BG", "1")

import racing                      # noqa: E402
import cfb                         # noqa: E402
from research import game_xref_common as C   # noqa: E402

SEASONS = (2024, 2025, 2026)
ANCHOR_W = 0.25                    # cfb_board._ANCHOR_W_DEFAULT
MARGIN_SD = 18.0                   # cfb_board._MARGIN_SD


# ---- a disk cache in front of ESPN, so the standings are fetched once ---------
def install_cache(raw):
    cdir = os.path.join(raw, "espn_cache")
    os.makedirs(cdir, exist_ok=True)
    orig = racing._get_json

    def get(url, timeout=12):
        fn = os.path.join(cdir, hashlib.sha1(url.encode()).hexdigest() + ".json")
        if os.path.exists(fn):
            with open(fn) as fh:
                return json.load(fh)
        d = orig(url, timeout=timeout)
        with open(fn, "w") as fh:
            json.dump(d, fh)
        time.sleep(0.8)
        return d
    racing._get_json = get
    return get


def prior_points(season, div, get):
    """{team_id: (pf_pg, pa_pg)} from last season's standings (the same call
    cfb.teams makes; first stat block = the overall record, as cfb.teams)."""
    d = get(f"{cfb._WEB}/standings?season={season - 1}&{cfb._DIV_URL[div]}", timeout=25)
    out = {}
    for conf in d.get("children", []):
        for sub in (conf.get("children") or [conf]):
            for e in sub.get("standings", {}).get("entries", []):
                st = {}
                for s in e.get("stats", []):
                    st.setdefault(s["name"], s.get("value"))
                gp = st.get("gamesPlayed") or 0
                try:
                    gp = int(gp)
                except (TypeError, ValueError):
                    gp = 0
                if gp < 6:
                    gp = 12
                pf, pa = st.get("pointsFor"), st.get("pointsAgainst")
                if pf is None or pa is None:
                    continue
                out[str((e.get("team") or {}).get("id"))] = (pf / gp, pa / gp)
    return out


# ---- the scoreboards -------------------------------------------------------------
def load_games(raw, season):
    seen = {}
    for w in range(1, 17):
        for grp in (80, 81):
            fn = os.path.join(raw, "cfb", f"sb_{season}_w{w}_g{grp}.json")
            if not os.path.exists(fn):
                continue
            with open(fn) as fh:
                d = json.load(fh)
            for e in d.get("events", []):
                comp = (e.get("competitions") or [{}])[0]
                stype = ((comp.get("status") or {}).get("type")) or {}
                if stype.get("state") != "post" or not stype.get("completed"):
                    continue
                g = seen.get(e["id"])
                if g:
                    g["groups"].add(grp)
                    continue
                sides = {}
                for c in comp.get("competitors", []):
                    tm = c.get("team") or {}
                    try:
                        sc = float(c.get("score"))
                    except (TypeError, ValueError):
                        sc = None
                    sides[c.get("homeAway")] = {"id": str(tm.get("id") or c.get("id")), "abbr": tm.get("abbreviation"),
                                                "name": tm.get("displayName"), "score": sc}
                h, a = sides.get("home"), sides.get("away")
                if not h or not a or h["score"] is None or a["score"] is None:
                    continue
                seen[e["id"]] = {"id": e["id"], "season": season, "week": int((e.get("week") or {}).get("number") or w),
                                 "date": e.get("date"), "neutral": bool(comp.get("neutralSite")),
                                 "home": h, "away": a, "groups": {grp}}
    return sorted(seen.values(), key=lambda g: (g["week"], g["date"] or ""))


# ---- the production rating, replayed ------------------------------------------
def massey(prior, played, k=cfb._PRIOR_K, cap=cfb._MARGIN_CAP, hfa=cfb._HFA_MARGIN, iters=25):
    """cfb.inseason_ratings' fixed point, parametrised."""
    R = dict(prior)
    for _ in range(iters):
        acc = {t: 0.0 for t in R}
        cnt = {t: 0 for t in R}
        for g in played:
            h, a = g["home"], g["away"]
            m = max(-cap, min(cap, g["margin"])) - (0.0 if g.get("neutral") else hfa)
            acc[h] += m + R[a]; cnt[h] += 1
            acc[a] += -m + R[h]; cnt[a] += 1
        R = {t: (k * prior[t] + acc[t]) / (k + cnt[t]) for t in R}
    return R


def engine(rh, ra, neutral, n, seed):
    """The drive engine as the board runs it: (p_home, mean margin, mean total, exp home, exp away)."""
    rng = random.Random(seed)
    r_h, r_a = cfb._rates_for(rh, ra, 0.0 if neutral else 1.0)
    hw = sm = st = ph_s = 0.0
    for _ in range(n):
        ph, pa = cfb._play(r_h, r_a, rng)
        hw += ph > pa
        sm += ph - pa
        st += ph + pa
        ph_s += ph
    return hw / n, sm / n, st / n, ph_s / n, (st - ph_s) / n


def _amer(x):
    try:
        return float(str(x).replace("EVEN", "100"))
    except (TypeError, ValueError):
        return None


def odds(raw, eid):
    """ESPN BET's stored record: (home spread, total, ml home, ml away, source)
    -- the close where it carries one, else the current/top-level line."""
    fn = os.path.join(raw, "cfb_core", "odds", f"{eid}.json")
    if not os.path.exists(fn):
        return None
    try:
        d = json.load(open(fn))
    except Exception:
        return None
    # ESPN BET (58) through 2025; DraftKings (100) is the stored book for 2026.
    # Never a "Live Odds" provider: those are in-game numbers.
    items = [it for it in d.get("items", []) if "live" not in ((it.get("provider") or {}).get("name") or "").lower()]
    pref = [it for it in items if str((it.get("provider") or {}).get("id")) == "58"] or \
           [it for it in items if str((it.get("provider") or {}).get("id")) == "100"] or items
    if not pref:
        return None
    it = pref[0]
    ho, ao = it.get("homeTeamOdds") or {}, it.get("awayTeamOdds") or {}
    src = "close"
    sp = _amer((((ho.get("close") or {}).get("pointSpread")) or {}).get("american"))
    if sp is None:
        sp, src = it.get("spread"), "current"
    tot = _amer((((it.get("close") or {}).get("total")) or {}).get("american"))
    if tot is None:
        tot = it.get("overUnder")
    mlh = _amer((((ho.get("close") or {}).get("moneyLine")) or {}).get("american"))
    mla = _amer((((ao.get("close") or {}).get("moneyLine")) or {}).get("american"))
    if mlh is None or mla is None:
        mlh, mla = ho.get("moneyLine"), ao.get("moneyLine")
    return {"spread": sp, "total": tot, "ml_home": mlh, "ml_away": mla, "src": src,
            "book": (it.get("provider") or {}).get("name")}


def predictor(raw, eid):
    fn = os.path.join(raw, "cfb_core", "pred", f"{eid}.json")
    if not os.path.exists(fn):
        return None, None
    try:
        d = json.load(open(fn))
        st = {s["name"]: s["value"] for s in d["homeTeam"]["statistics"]}
        return st.get("gameProjection"), st.get("teamPredPtDiff")
    except Exception:
        return None, None


def replay_season(raw, season, n, get, log=print):
    games = load_games(raw, season)
    meta = cfb.teams(season, "all")
    divs = cfb.divisions(season)
    prior = cfb.ratings(season, "all")
    pts_prior = {}
    for div in ("fbs", "fcs"):
        for tid, (pf, pa) in prior_points(season, div, get).items():
            pts_prior[tid] = {"pf": round(pf, 2), "pa": round(pa, 2), "div": div}
    rated = [g for g in games if g["home"]["id"] in prior and g["away"]["id"] in prior]
    log(f"[xref-cfb] {season}: {len(games)} completed games, {len(rated)} with both sides rated, "
        f"{len(meta)} teams")
    # self-check: the replayed solver reproduces the module's own fixed point
    sub = [{"home": g["home"]["id"], "away": g["away"]["id"], "margin": g["home"]["score"] - g["away"]["score"],
            "neutral": g["neutral"], "final": True} for g in rated if g["week"] < 6]
    saved = cfb.schedule
    try:
        cfb.schedule = lambda s=None, div="fbs": sub
        R_mod = cfb.inseason_ratings(season, "all")
    finally:
        cfb.schedule = saved
    R_me = massey(prior, sub)
    gap = max(abs(R_mod[t] - R_me[t]) for t in R_me)
    log(f"[xref-cfb] {season}: solver self-check max |diff| = {gap:.2e} (weeks 1-5)")
    rows = []
    by_week = defaultdict(list)
    for g in rated:
        by_week[g["week"]].append(g)
    played = []
    t0 = time.time()
    for w in sorted(by_week):
        R = massey(prior, played)
        for g in by_week[w]:
            h, a = g["home"], g["away"]
            dh, da = divs.get(h["id"], "fbs"), divs.get(a["id"], "fbs")
            host = 0.0 if g["neutral"] else 1.0
            mm = (R[h["id"]] - R[a["id"]]) + cfb._HFA_MARGIN * host
            seed = zlib.crc32(f"{season}|{g['id']}".encode())
            p_eng, m_eng, t_eng, eh, ea = engine(R[h["id"]], R[a["id"]], g["neutral"], n, seed)
            od = odds(raw, g["id"]) or {}
            p_fpi, m_fpi = predictor(raw, g["id"])
            row = {"id": g["id"], "season": season, "week": w, "date": g["date"], "neutral": g["neutral"],
                   "home": h["abbr"], "away": a["abbr"], "home_id": h["id"], "away_id": a["id"],
                   "home_name": h["name"], "away_name": a["name"],
                   "div_home": dh, "div_away": da, "division": dh if dh == da else "cross",
                   "home_pts": h["score"], "away_pts": a["score"],
                   "y": 1.0 if h["score"] > a["score"] else 0.0,
                   "margin": h["score"] - a["score"], "total": h["score"] + a["score"],
                   "r_home": round(R[h["id"]], 3), "r_away": round(R[a["id"]], 3),
                   "m_model": round(mm, 3), "p_raw": C.phi(mm / MARGIN_SD),
                   "p_eng": p_eng, "m_eng": round(m_eng, 2), "t_eng": round(t_eng, 2),
                   "eh": round(eh, 2), "ea": round(ea, 2),
                   "spread": od.get("spread"), "total_line": od.get("total"),
                   "ml_home": od.get("ml_home"), "ml_away": od.get("ml_away"), "odds_src": od.get("src"),
                   "book": od.get("book"), "p_fpi": (p_fpi / 100.0) if p_fpi is not None else None, "m_fpi": m_fpi,
                   "pf_prior_h": (pts_prior.get(h["id"]) or {}).get("pf"), "pa_prior_h": (pts_prior.get(h["id"]) or {}).get("pa"),
                   "pf_prior_a": (pts_prior.get(a["id"]) or {}).get("pf"), "pa_prior_a": (pts_prior.get(a["id"]) or {}).get("pa")}
            row["p_mkt"] = (C.devig(C.american_to_prob(od["ml_home"]), C.american_to_prob(od["ml_away"]))
                            if od.get("ml_home") is not None and od.get("ml_away") is not None else None)
            if od.get("spread") is not None:
                # the board's priced headline: level 25% model / 75% market
                mkt_margin = -float(od["spread"])
                fair = ANCHOR_W * mm + (1 - ANCHOR_W) * mkt_margin
                shift = fair - mm
                p_b, m_b, t_b, _eh, _ea = engine(R[h["id"]] + shift / 2.0, R[a["id"]] - shift / 2.0,
                                                 g["neutral"], n, seed)
                row.update({"m_mkt": mkt_margin, "p_blend": p_b, "m_blend": round(m_b, 2), "t_blend": round(t_b, 2)})
            else:
                row.update({"m_mkt": None, "p_blend": None, "m_blend": None, "t_blend": None})
            rows.append(row)
        played.extend({"home": g["home"]["id"], "away": g["away"]["id"],
                       "margin": g["home"]["score"] - g["away"]["score"], "neutral": g["neutral"]}
                      for g in by_week[w])
        log(f"[xref-cfb] {season} w{w}: {len(by_week[w])} games ({time.time()-t0:.0f}s)")
    return rows, {"n_teams": len(meta), "prior_mean_fbs": None}


def join(rows_path, raw, n):
    """Re-read the odds and predictor files for every row (a pull that landed
    after the replay) and recompute the market fields and the priced
    headline; the ratings and the model-only engine figures are kept."""
    with open(rows_path) as fh:
        d = json.load(fh)
    changed = 0
    for row in d["rows"]:
        od = odds(raw, row["id"]) or {}
        p_fpi, m_fpi = predictor(raw, row["id"])
        new = {"spread": od.get("spread"), "total_line": od.get("total"), "ml_home": od.get("ml_home"),
               "ml_away": od.get("ml_away"), "odds_src": od.get("src"), "book": od.get("book"),
               "p_fpi": (p_fpi / 100.0) if p_fpi is not None else None, "m_fpi": m_fpi}
        new["p_mkt"] = (C.devig(C.american_to_prob(od["ml_home"]), C.american_to_prob(od["ml_away"]))
                        if od.get("ml_home") is not None and od.get("ml_away") is not None else None)
        if all(row.get(k) == v for k, v in new.items()):
            continue
        changed += 1
        row.update(new)
        if od.get("spread") is not None:
            mm = row["m_model"]
            mkt_margin = -float(od["spread"])
            fair = ANCHOR_W * mm + (1 - ANCHOR_W) * mkt_margin
            shift = fair - mm
            seed = zlib.crc32(f"{row['season']}|{row['id']}".encode())
            p_b, m_b, t_b, _eh, _ea = engine(row["r_home"] + shift / 2.0, row["r_away"] - shift / 2.0,
                                             row["neutral"], n, seed)
            row.update({"m_mkt": mkt_margin, "p_blend": p_b, "m_blend": round(m_b, 2), "t_blend": round(t_b, 2)})
        else:
            row.update({"m_mkt": None, "p_blend": None, "m_blend": None, "t_blend": None})
    d["joined"] = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    with open(rows_path, "w") as fh:
        json.dump(d, fh)
    print(f"[xref-cfb] join: {changed} rows updated of {len(d['rows'])}")
    return 0


# ---- the report stage ------------------------------------------------------------
import numpy as np   # noqa: E402

DIVS = ("fbs", "cross", "fcs")


def _clusters(rows):
    return [(r["season"], r["week"]) for r in rows]


def _ok(rows, keys):
    return [r for r in rows if all(r.get(k) is not None for k in keys)]


def _by(rows, season=None, div=None):
    out = rows
    if season is not None:
        out = [r for r in out if r["season"] == season]
    if div is not None:
        out = [r for r in out if r["division"] == div]
    return out


def win_tables(rows):
    L = []
    for r in rows:
        r["p_const"] = 0.56
    models_all = [("p_raw", "ratings Phi(m/18) (p_home_raw)"), ("p_eng", "drive engine, model level"),
                  ("p_fpi", "ESPN FPI"), ("p_const", "constant home 0.56")]
    models_priced = [("p_raw", "ratings Phi(m/18) (p_home_raw)"), ("p_eng", "drive engine, model level"),
                     ("p_blend", "board headline (25% model / 75% market level)"), ("p_fpi", "ESPN FPI"),
                     ("p_mkt", "closing moneyline, de-vigged"), ("p_const", "constant home 0.56")]
    L.append("**Priced games** (ESPN BET close spread and moneyline present), by season and division. 'vs market' = paired log-loss difference against the de-vigged close (negative = better), clustered SE by week.")
    L.append("")
    for season in (None,) + SEASONS:
        for div in (None, "fbs", "cross", "fcs"):
            sub = _ok(_by(rows, season, div), ["p_raw", "p_eng", "p_blend", "p_fpi", "p_mkt"])
            if len(sub) < 40:
                continue
            ys = [r["y"] for r in sub]
            cl = _clusters(sub)
            pm = [r["p_mkt"] for r in sub]
            L.append(f"*{season or 'all seasons'}, {div or 'all divisions'}* (n = {len(sub)})")
            L.append("")
            body = []
            for key, name in models_priced:
                ps = [r[key] for r in sub]
                sc = C.score(ps, ys, cl)
                dl, sl = C.paired(ps, pm, ys, cl, "ll")
                da, sa = C.paired(ps, pm, ys, cl, "acc")
                body.append([name, sc["log_loss"], sc["brier"], f"{sc['accuracy']:.3f} (+/- {sc['acc_se']:.3f})",
                             f"{dl:+.4f} (+/- {sl:.4f})", f"{da:+.3f} (+/- {sa:.3f})"])
            L.append(C.md_table(["model", "log loss", "Brier", "SU accuracy", "LL - market", "acc - market"], body))
            L.append("")
    L.append("**All rated games** (no market needed), by division: the raw ratings against the engine and FPI where FPI exists.")
    L.append("")
    for div in (None, "fbs", "cross", "fcs"):
        sub = _ok(_by(rows, None, div), ["p_raw", "p_eng"])
        ys = [r["y"] for r in sub]
        cl = _clusters(sub)
        body = []
        for key, name in models_all:
            ok = _ok(sub, [key])
            if len(ok) < 40:
                continue
            sc = C.score([r[key] for r in ok], [r["y"] for r in ok], _clusters(ok))
            body.append([name, len(ok), sc["log_loss"], sc["brier"], f"{sc['accuracy']:.3f} (+/- {sc['acc_se']:.3f})"])
        L.append(f"*{div or 'all divisions'}*")
        L.append("")
        L.append(C.md_table(["model", "n", "log loss", "Brier", "SU accuracy"], body))
        L.append("")
    return "\n".join(L)


def disagreement_tables(rows):
    L = []
    sub = _ok(rows, ["p_raw", "p_blend", "p_fpi", "p_mkt"])
    ys = [r["y"] for r in sub]
    L.append("Priced games where a model picks the other side from the market favourite (all seasons):")
    L.append("")
    body = []
    for key, name in (("p_raw", "raw ratings"), ("p_blend", "board headline"), ("p_fpi", "ESPN FPI")):
        d = C.disagreements([r[key] for r in sub], [r["p_mkt"] for r in sub], ys)
        body.append([name, d["n_disagree"], d["a_right"], d["b_right"]])
    L.append(C.md_table(["model", "disagreements", "model right", "market right"], body))
    L.append("")
    L.append("The market favourite's record by whether the raw ratings and FPI agree with it:")
    L.append("")
    tiers = defaultdict(lambda: [0, 0, 0.0])
    for r in sub:
        fav_home = r["p_mkt"] >= 0.5
        k = sum(1 for key in ("p_raw", "p_fpi") if (r[key] >= 0.5) == fav_home)
        t = tiers[k]
        t[0] += 1; t[1] += (r["y"] >= 0.5) == fav_home; t[2] += r["p_mkt"] if fav_home else 1 - r["p_mkt"]
    L.append(C.md_table(["models agreeing (of 2)", "games", "favourite won", "market's mean p"],
                        [[k, t[0], f"{t[1]/t[0]:.3f}", f"{t[2]/t[0]:.3f}"] for k, t in sorted(tiers.items())]))
    L.append("")
    # the quick combo's tiers: how the raw number's 80/90/95 read against reality and the market
    L.append("The quick-combo tiers on the raw ratings (the LOWER of model and market sets the tier; here each number's own bins), priced games:")
    L.append("")
    body = []
    for lo, hi in ((0.80, 0.90), (0.90, 0.95), (0.95, 1.01)):
        for key, name in (("p_raw", "raw ratings"), ("p_mkt", "market")):
            b = [(r[key] if r[key] >= 0.5 else 1 - r[key], (r["y"] >= 0.5) == (r[key] >= 0.5)) for r in sub]
            b = [(p, w) for p, w in b if lo <= p < hi]
            if b:
                body.append([f"{lo:.2f}-{min(hi, 1.0):.2f}", name, len(b), f"{sum(p for p, _ in b)/len(b):.3f}", f"{sum(w for _, w in b)/len(b):.3f}"])
    L.append(C.md_table(["tier", "number", "n", "mean predicted", "realized"], body))
    L.append("")
    return "\n".join(L)


def calibration_tables(rows):
    L = []
    sub = _ok(rows, ["p_raw", "p_blend", "p_mkt"])
    ys = [r["y"] for r in sub]
    for key, name in (("p_raw", "raw ratings Phi(m/18)"), ("p_blend", "board headline"), ("p_mkt", "closing moneyline")):
        L.append(f"Calibration, {name}, favourite's side, priced games, all seasons:")
        L.append("")
        L.append(C.md_table(["bin", "n", "mean predicted", "realized"], C.calibration([r[key] for r in sub], ys)))
        L.append("")
    return "\n".join(L)


def band_tables(rows):
    L = ["Straight-up accuracy / log loss by week band, priced games, all seasons:", ""]
    sub = _ok(rows, ["p_raw", "p_blend", "p_fpi", "p_mkt"])
    body = []
    for lo, hi in ((1, 3), (4, 8), (9, 16)):
        b = [r for r in sub if lo <= r["week"] <= hi]
        ys = [r["y"] for r in b]
        cells = [f"w{lo}-{hi}", len(b)]
        for key in ("p_raw", "p_blend", "p_fpi", "p_mkt"):
            sc = C.score([r[key] for r in b], ys)
            cells.append(f"{sc['accuracy']:.3f} / {sc['log_loss']:.4f}")
        body.append(cells)
    L.append(C.md_table(["weeks", "n", "raw ratings", "headline", "FPI", "market"], body))
    L.append("")
    return "\n".join(L)


def point_tables(rows, extra=None):
    """Margins, totals and team points against the actual score; `extra`
    adds fitted candidates {name: {row id: (total, home pts, away pts)}}."""
    L = []
    extra = extra or {}
    for season in (None,) + SEASONS:
        for div in (None, "fbs", "cross", "fcs"):
            sub = _ok(_by(rows, season, div), ["m_model", "m_fpi", "spread", "total_line", "t_eng"])
            if len(sub) < 40:
                continue
            cl = _clusters(sub)
            L.append(f"*{season or 'all seasons'}, {div or 'all divisions'}* (n = {len(sub)} priced games). Margin = home - away; bias = predicted - actual; slope = actual on predicted; pred SD = spread of the predictions across games.")
            L.append("")
            act = [r["margin"] for r in sub]
            body = []
            for name, fn in (("ratings margin (model_margin)", lambda r: r["m_model"]),
                             ("drive engine mean margin", lambda r: r["m_eng"]),
                             ("board fair margin (25/75)", lambda r: r["m_blend"]),
                             ("ESPN FPI margin", lambda r: r["m_fpi"]),
                             ("closing spread (home favoured = +)", lambda r: r["m_mkt"])):
                pred = [fn(r) for r in sub]
                sc = C.point_scores(pred, act, cl, boot=200)
                dm, se = C.paired_mae(pred, [r["m_mkt"] for r in sub], act, cl)
                body.append([name, f"{sc['bias']:+.2f} (+/- {sc['bias_se']:.2f})", sc["mae"], f"{sc['slope']:.2f} (+/- {sc['slope_se']:.2f})",
                             sc["pred_sd"], f"{dm:+.2f} (+/- {se:.2f})"])
            L.append(C.md_table(["margin", "bias", "MAE", "slope", "pred SD", "MAE - spread's"], body))
            L.append("")
            act = [r["total"] for r in sub]
            body = []
            cands = [("drive engine total (the card's exp_total)", lambda r: r["t_eng"]),
                     ("engine total at the blended level", lambda r: r["t_blend"]),
                     ("closing total", lambda r: r["total_line"])]
            for name, m in extra.items():
                cands.append((name, (lambda r, m=m: m[r["id"]][0])))
            for name, fn in cands:
                try:
                    pred = [fn(r) for r in sub]
                except KeyError:
                    continue
                sc = C.point_scores(pred, act, cl, boot=200)
                dm, se = C.paired_mae(pred, [r["total_line"] for r in sub], act, cl)
                de, see = C.paired_mae(pred, [r["t_eng"] for r in sub], act, cl)
                body.append([name, f"{sc['bias']:+.2f} (+/- {sc['bias_se']:.2f})", sc["mae"], f"{sc['slope']:.2f} (+/- {sc['slope_se']:.2f})",
                             sc["pred_sd"], sc["actual_sd"], f"{dm:+.2f} (+/- {se:.2f})", f"{de:+.2f} (+/- {see:.2f})"])
            L.append(C.md_table(["total", "bias", "MAE", "slope", "pred SD", "actual SD", "MAE - total line's", "MAE - engine's"], body))
            L.append("")
    sub = _ok(rows, ["eh", "spread", "total_line"])
    L.append(f"Team points, all priced games pooled (n = {2*len(sub)} team-games): the card's expected points against the market's implied split (total/2 -/+ spread/2).")
    L.append("")
    act = [r["home_pts"] for r in sub] + [r["away_pts"] for r in sub]
    cl = _clusters(sub) * 2
    body = []
    cands = [("card exp points (engine, model level)", [r["eh"] for r in sub] + [r["ea"] for r in sub]),
             ("market implied split", [r["total_line"] / 2 - r["spread"] / 2 for r in sub] + [r["total_line"] / 2 + r["spread"] / 2 for r in sub])]
    for name, m in extra.items():
        try:
            cands.append((name, [m[r["id"]][1] for r in sub] + [m[r["id"]][2] for r in sub]))
        except KeyError:
            pass
    for name, pred in cands:
        sc = C.point_scores(pred, act, cl, boot=100)
        body.append([name, f"{sc['bias']:+.2f}", sc["mae"], f"{sc['slope']:.2f}", sc["pred_sd"], sc["actual_sd"]])
    L.append(C.md_table(["team points", "bias", "MAE", "slope", "pred SD", "actual SD"], body))
    L.append("")
    return "\n".join(L)


# ---- the offence/defence rating candidate (pre-registered, report section 0) ----
def _season_games(rows, season):
    return sorted([r for r in rows if r["season"] == season], key=lambda r: (r["week"], r["date"] or ""))


def _priors(rows, season):
    """{team id: (pf_pg, pa_pg, div)} from the rows' carried standings figures;
    plus the FBS and FCS means of pf (the level each prior is centred on)."""
    pri = {}
    for r in rows:
        if r["season"] != season:
            continue
        for side in ("h", "a"):
            tid = r["home_id" if side == "h" else "away_id"]
            pf, pa = r.get(f"pf_prior_{side}"), r.get(f"pa_prior_{side}")
            div = r["div_home" if side == "h" else "div_away"]
            if pf is not None and pa is not None:
                pri[tid] = (pf, pa, div)
    means = {}
    for div in ("fbs", "fcs"):
        v = [pf for pf, _pa, d in pri.values() if d == div]
        means[div] = sum(v) / len(v) if v else 28.0
    return pri, means


def od_fit(prior_O, prior_D, mu, games, k, h, iters=30):
    """Fixed point for pts_for(i at j) = mu + O_i + D_j +/- h.
    games: list of (home id, away id, home pts, away pts, neutral)."""
    O, D = dict(prior_O), dict(prior_D)
    for _ in range(iters):
        accO = defaultdict(float); accD = defaultdict(float); n = defaultdict(int)
        for hid, aid, hp, ap, neu in games:
            hh = 0.0 if neu else h
            accO[hid] += hp - mu - D[aid] - hh; accD[aid] += hp - mu - O[hid] - hh
            accO[aid] += ap - mu - D[hid] + hh; accD[hid] += ap - mu - O[aid] + hh
            n[hid] += 1; n[aid] += 1
        O = {t: (k * prior_O[t] + accO[t]) / (k + n[t]) for t in O}
        D = {t: (k * prior_D[t] + accD[t]) / (k + n[t]) for t in D}
    return O, D


def od_replay(rows, season, k, h=None):
    """{row id: (pred total, pred home pts, pred away pts, pred margin)} for the
    season's rows, each week from the games before it."""
    h = cfb._HFA_MARGIN / 2.0 if h is None else h
    pri, means = _priors(rows, season)
    mu = means["fbs"]
    teams = set()
    for r in rows:
        if r["season"] == season:
            teams.add(r["home_id"]); teams.add(r["away_id"])
    prior_O, prior_D = {}, {}
    for t in teams:
        pf, pa, div = pri.get(t, (None, None, None))
        if pf is None:
            div = next((r["div_home"] for r in rows if r["season"] == season and r["home_id"] == t), None) or \
                  next((r["div_away"] for r in rows if r["season"] == season and r["away_id"] == t), "fbs")
            pf, pa = means[div], means[div]
        o = (pf - means[div]) * (1 - cfb._REG)
        d = (pa - means[div]) * (1 - cfb._REG)
        if div == "fcs":
            o -= cfb._DIV_GAP / 2.0
            d += cfb._DIV_GAP / 2.0
        prior_O[t], prior_D[t] = o, d
    out = {}
    by_week = defaultdict(list)
    for r in _season_games(rows, season):
        by_week[r["week"]].append(r)
    played = []
    for w in sorted(by_week):
        O, D = od_fit(prior_O, prior_D, mu, played, k, h)
        for r in by_week[w]:
            hh = 0.0 if r["neutral"] else h
            ph = mu + O[r["home_id"]] + D[r["away_id"]] + hh
            pa = mu + O[r["away_id"]] + D[r["home_id"]] - hh
            out[r["id"]] = (ph + pa, ph, pa, ph - pa)
        played.extend((r["home_id"], r["away_id"], r["home_pts"], r["away_pts"], r["neutral"]) for r in by_week[w])
    return out


def od_tables(rows):
    L = ["The offence/defence rating (rule in section 0): K chosen on 2024 by total MAE over every rated game, then tested on 2025 and 2026 against the engine's total and the closing total.", ""]
    grid = (1.0, 1.5, 2.0, 3.0, 4.0, 6.0)
    fit_rows = [r for r in rows if r["season"] == 2024]
    body = []
    best, best_mae = None, None
    for k in grid:
        pred = od_replay(rows, 2024, k)
        mae = sum(abs(pred[r["id"]][0] - r["total"]) for r in fit_rows) / len(fit_rows)
        mm = sum(abs(pred[r["id"]][3] - r["margin"]) for r in fit_rows) / len(fit_rows)
        body.append([k, f"{mae:.3f}", f"{mm:.3f}"])
        if best_mae is None or mae < best_mae:
            best, best_mae = k, mae
    L.append(C.md_table(["K (prior games)", "2024 total MAE (all rated games)", "2024 margin MAE (OD margin, descriptive)"], body))
    L.append("")
    L.append(f"Chosen K = {best}.")
    L.append("")
    preds = {}
    for season in SEASONS:
        preds.update(od_replay(rows, season, best))
    body = []
    for season in (2025, 2026):
        for div in (None, "fbs", "cross", "fcs"):
            sub = _ok(_by(rows, season, div), ["t_eng", "total_line"])
            if len(sub) < 40:
                continue
            act = [r["total"] for r in sub]
            cl = _clusters(sub)
            pred = [preds[r["id"]][0] for r in sub]
            sc = C.point_scores(pred, act, cl, boot=200)
            de, see = C.paired_mae(pred, [r["t_eng"] for r in sub], act, cl)
            dm, sem = C.paired_mae(pred, [r["total_line"] for r in sub], act, cl)
            blend = [0.25 * p + 0.75 * r["total_line"] for p, r in zip(pred, sub)]
            db, seb = C.paired_mae(blend, [r["total_line"] for r in sub], act, cl)
            body.append([season, div or "all", len(sub), f"{sc['bias']:+.2f}", sc["mae"], f"{sc['slope']:.2f} (+/- {sc['slope_se']:.2f})",
                         f"{de:+.2f} (+/- {see:.2f})", f"{dm:+.2f} (+/- {sem:.2f})", f"{db:+.2f} (+/- {seb:.2f})"])
    L.append(C.md_table(["test season", "division", "n", "bias", "MAE", "slope", "MAE - engine's", "MAE - total line's", "25/75 blend MAE - line's"], body))
    L.append("")
    # unpriced games too: the OD total against the engine's where no line exists
    body = []
    for season in (2025, 2026):
        sub = [r for r in _by(rows, season) if r.get("total_line") is None]
        if len(sub) >= 40:
            act = [r["total"] for r in sub]
            cl = _clusters(sub)
            pred = [preds[r["id"]][0] for r in sub]
            de, see = C.paired_mae(pred, [r["t_eng"] for r in sub], act, cl)
            body.append([season, len(sub), f"{C.point_scores(pred, act, None, boot=0)['mae']:.2f}",
                         f"{C.point_scores([r['t_eng'] for r in sub], act, None, boot=0)['mae']:.2f}", f"{de:+.2f} (+/- {see:.2f})"])
    if body:
        L.append("Unpriced games (no ESPN BET line stored; mostly FCS):")
        L.append("")
        L.append(C.md_table(["season", "n", "OD total MAE", "engine total MAE", "difference"], body))
        L.append("")
    return "\n".join(L), preds, best


# ---- the margin constants, re-measured ------------------------------------------
def _index(rows, season):
    teams = sorted({t for r in rows if r["season"] == season for t in (r["home_id"], r["away_id"])})
    return {t: i for i, t in enumerate(teams)}


def massey_np(prior, games, k, cap, hfa, iters=25):
    """cfb.inseason_ratings' fixed point in numpy. games: arrays (h idx, a idx, margin, neutral)."""
    R = prior.copy()
    if len(games[0]) == 0:
        return R
    h, a, m, neu = games
    adj = np.clip(m, -cap, cap) - np.where(neu, 0.0, hfa)
    n = np.zeros_like(R)
    np.add.at(n, h, 1); np.add.at(n, a, 1)
    for _ in range(iters):
        acc = np.zeros_like(R)
        np.add.at(acc, h, adj + R[a])
        np.add.at(acc, a, -adj + R[h])
        R = (k * prior + acc) / (k + n)
    return R


def constants_tables(rows):
    L = ["The margin constants re-measured (grid on 2024 by margin MAE over every rated game, tested on 2025 and 2026 against the shipped K 1.5 / cap 50 / HFA 3.0):", ""]
    grid = [(k, cap, hfa) for k in (1.0, 1.5, 2.0, 3.0) for cap in (28.0, 50.0, 100.0) for hfa in (2.0, 3.0, 4.0)]
    ship = (cfb._PRIOR_K, cfb._MARGIN_CAP, cfb._HFA_MARGIN)

    def replay(season, k, cap, hfa):
        idx = _index(rows, season)
        prior = np.zeros(len(idx))
        for r in rows:
            if r["season"] != season:
                continue
            # the preseason prior is the week-1 rating of each team (no games yet)
            if r["week"] == min(x["week"] for x in rows if x["season"] == season):
                pass
        # rebuild the prior from the module (cheap, cached in-process)
        pr = cfb.ratings(season, "all")
        for t, i in idx.items():
            prior[i] = pr.get(t, 0.0)
        by_week = defaultdict(list)
        for r in _season_games(rows, season):
            by_week[r["week"]].append(r)
        H, A, M, N = [], [], [], []
        out = {}
        for w in sorted(by_week):
            R = massey_np(prior, (np.array(H, int), np.array(A, int), np.array(M, float), np.array(N, bool)), k, cap, hfa)
            for r in by_week[w]:
                out[r["id"]] = R[idx[r["home_id"]]] - R[idx[r["away_id"]]] + (0.0 if r["neutral"] else hfa)
            for r in by_week[w]:
                H.append(idx[r["home_id"]]); A.append(idx[r["away_id"]]); M.append(r["margin"]); N.append(r["neutral"])
        return out

    # self-check against the replay's own margins at the shipped constants
    chk = replay(2024, *ship)
    rows24 = [r for r in rows if r["season"] == 2024]
    gap = max(abs(chk[r["id"]] - r["m_model"]) for r in rows24)
    L.append(f"(numpy solver self-check against the replay's margins, 2024: max |diff| = {gap:.2e})")
    L.append("")
    body = []
    best, best_mae = None, None
    for k, cap, hfa in grid:
        pred = replay(2024, k, cap, hfa)
        mae = sum(abs(pred[r["id"]] - r["margin"]) for r in rows24) / len(rows24)
        if best_mae is None or mae < best_mae:
            best, best_mae = (k, cap, hfa), mae
        if (k, cap, hfa) == ship or hfa == 3.0 and cap == 50.0 or k == 1.5 and hfa == 3.0 or k == 1.5 and cap == 50.0:
            body.append([k, cap, hfa, f"{mae:.3f}"])
    L.append(C.md_table(["K", "cap", "HFA", "2024 margin MAE"], body))
    L.append("")
    L.append(f"Best on 2024: K {best[0]}, cap {best[1]}, HFA {best[2]} (MAE {best_mae:.3f}) against the shipped {ship} (MAE {[b for b in body if (b[0], b[1], b[2]) == ship][0][3]}).")
    L.append("")
    body = []
    for season in (2025, 2026):
        sub = [r for r in rows if r["season"] == season]
        act = [r["margin"] for r in sub]
        cl = _clusters(sub)
        pb = replay(season, *best)
        d, se = C.paired_mae([pb[r["id"]] for r in sub], [r["m_model"] for r in sub], act, cl)
        body.append([season, len(sub), f"{C.point_scores([r['m_model'] for r in sub], act, None, boot=0)['mae']:.3f}",
                     f"{C.point_scores([pb[r['id']] for r in sub], act, None, boot=0)['mae']:.3f}", f"{d:+.3f} (+/- {se:.3f})"])
    L.append(C.md_table(["test season", "n", "shipped constants MAE", "2024-best constants MAE", "difference (+/- SE)"], body))
    L.append("")
    return "\n".join(L)


# ---- stacks ---------------------------------------------------------------------------
CSTACKS = {"C1 [raw]": ["raw"], "C2 [raw, fpi]": ["raw", "fpi"], "C3 [mkt]": ["mkt"], "C4 [mkt, raw]": ["mkt", "raw"],
           "C5 [mkt, raw, fpi]": ["mkt", "raw", "fpi"]}
CFEAT = {"raw": lambda r: C.logit(r["p_raw"]), "fpi": lambda r: C.logit(r["p_fpi"]), "mkt": lambda r: C.logit(r["p_mkt"])}
LAM = 1.0


def stack_tables(rows):
    L = ["Pre-registered win-probability stacks (ridge logistic, lambda 1.0), walk-forward, priced games with FPI; 'vs market' = paired log loss against the de-vigged close, clustered SE.", ""]
    rows = _ok(rows, ["p_raw", "p_fpi", "p_mkt"])
    body = []
    for name, feats in CSTACKS.items():
        pooled_p, pooled_m, pooled_y, pooled_c, per, weights = [], [], [], [], [], []
        for fit_s, test_s in (((2024,), 2025), ((2024, 2025), 2026)):
            fit = [r for r in rows if r["season"] in fit_s]
            test = [r for r in rows if r["season"] == test_s]
            if len(test) < 40:
                per.append(f"{test_s}: no priced test games")
                continue
            b0, w = C.ridge_logistic([[CFEAT[f](r) for f in feats] for r in fit], [r["y"] for r in fit], LAM)
            ps = C.predict_logistic(b0, w, [[CFEAT[f](r) for f in feats] for r in test])
            ys = [r["y"] for r in test]
            pm = [r["p_mkt"] for r in test]
            d, se = C.paired(ps, pm, ys, _clusters(test), "ll")
            per.append(f"{test_s}: {C.score(ps, ys)['log_loss']:.4f} ({d:+.4f})")
            weights.append(f"{test_s}: " + ", ".join(f"{f} {x:+.2f}" for f, x in zip(feats, w)) + f", b0 {b0:+.2f}")
            pooled_p += ps; pooled_m += pm; pooled_y += ys; pooled_c += _clusters(test)
        d, se = C.paired(pooled_p, pooled_m, pooled_y, pooled_c, "ll")
        da, sa = C.paired(pooled_p, pooled_m, pooled_y, pooled_c, "acc")
        folds = [x for x in per if "no priced" not in x]
        verdict = "PASS" if (d < -2 * se and folds and all("(-" in x for x in folds)) else "no"
        if len(folds) < 2:
            verdict += " (one fold only)"
        body.append([name, C.score(pooled_p, pooled_y)["log_loss"], f"{d:+.4f} (+/- {se:.4f})", f"{da:+.3f} (+/- {sa:.3f})", "; ".join(per), verdict])
        L.append(f"- {name} weights: " + " | ".join(weights))
    L.append("")
    L.append(C.md_table(["stack", "pooled test LL", "vs market LL", "vs market acc", "per test season", "2-SE pass"], body))
    L.append("")
    # the anchor weight on the margin, the board's own knob
    L.append("The board's anchor weight (model share of the LEVEL), walk-forward on the win probability through the engine's own Phi(margin/18): w chosen on the fit seasons from {0, 0.1, 0.25, 0.5, 0.75, 1}.")
    L.append("")
    grid = (0.0, 0.1, 0.25, 0.5, 0.75, 1.0)
    sub = _ok(rows, ["m_model", "m_mkt", "p_mkt"])
    body = []
    for fit_s, test_s in (((2024,), 2025), ((2024, 2025), 2026)):
        fit = [r for r in sub if r["season"] in fit_s]
        test = [r for r in sub if r["season"] == test_s]
        if len(test) < 40:
            body.append([f"{'+'.join(map(str, fit_s))} -> {test_s}", "-", "no priced test games", "", "", ""])
            continue
        yf = [r["y"] for r in fit]

        def pw(w, rs):
            return [C.phi((w * r["m_model"] + (1 - w) * r["m_mkt"]) / MARGIN_SD) for r in rs]
        best = min(grid, key=lambda w: sum(C.ll_terms(pw(w, fit), yf)))
        yt = [r["y"] for r in test]
        cl = _clusters(test)
        cells = [f"{'+'.join(map(str, fit_s))} -> {test_s}", best]
        for w in (0.0, 0.25, best, 1.0):
            ps = pw(w, test)
            d, se = C.paired(ps, [r["p_mkt"] for r in test], yt, cl, "ll")
            cells.append(f"w {w}: LL {C.score(ps, yt)['log_loss']:.4f}, acc {C.score(ps, yt)['accuracy']:.3f}, vs mkt {d:+.4f} (+/- {se:.4f})")
        body.append(cells)
    L.append(C.md_table(["fit -> test", "chosen w", "market level (w 0)", "shipped (w 0.25)", "chosen", "model only (w 1)"], body))
    L.append("")
    return "\n".join(L)


def headline_tables(rows):
    """The engine step's cost on the priced headline, and what the compressed
    margin and the near-constant total do to the ATS and total picks.
    `p_fair` = Phi((0.25 x model margin + 0.75 x market margin) / 18): the
    shipped blend and the shipped sigma, no engine in between (no fit)."""
    L = ["**The engine step on the headline.** `p_fair` is Phi(fair margin / 18) at the shipped 25/75 blend, the number the board would show if the engine's win frequency were not used for the headline. Paired log loss, clustered SE.", ""]
    sub = _ok(rows, ["p_blend", "p_mkt", "m_model", "m_mkt"])
    for r in sub:
        r["p_fair"] = C.phi((ANCHOR_W * r["m_model"] + (1 - ANCHOR_W) * r["m_mkt"]) / MARGIN_SD)
    body = []
    for season in (None,) + SEASONS:
        b = _by(sub, season)
        ys = [r["y"] for r in b]
        cl = _clusters(b)
        d1, s1 = C.paired([r["p_fair"] for r in b], [r["p_blend"] for r in b], ys, cl, "ll")
        d2, s2 = C.paired([r["p_fair"] for r in b], [r["p_mkt"] for r in b], ys, cl, "ll")
        d3, s3 = C.paired([r["p_blend"] for r in b], [r["p_mkt"] for r in b], ys, cl, "ll")
        body.append([season or "all", len(b), C.score([r["p_blend"] for r in b], ys)["log_loss"], C.score([r["p_fair"] for r in b], ys)["log_loss"],
                     C.score([r["p_mkt"] for r in b], ys)["log_loss"], f"{d1:+.4f} (+/- {s1:.4f})", f"{d2:+.4f} (+/- {s2:.4f})", f"{d3:+.4f} (+/- {s3:.4f})",
                     f"{C.score([r['p_blend'] for r in b], ys)['accuracy']:.3f} / {C.score([r['p_fair'] for r in b], ys)['accuracy']:.3f} / {C.score([r['p_mkt'] for r in b], ys)['accuracy']:.3f}"])
    L.append(C.md_table(["season", "n", "engine headline LL", "p_fair LL", "market LL", "p_fair - engine", "p_fair - market", "engine - market", "SU acc engine / p_fair / market"], body))
    L.append("")
    L.append("Calibration of p_fair (favourite's side, all priced games):")
    L.append("")
    L.append(C.md_table(["bin", "n", "mean predicted", "realized"], C.calibration([r["p_fair"] for r in sub], [r["y"] for r in sub])))
    L.append("")
    # the engine's compression of the blended margin, and the ATS pick it implies
    L.append("**The engine's margin against the margin it was given.** `fair_in` is the blended input margin; `m_blend` the engine's mean margin at that level. The ATS pick reads the simulated margins at Kalshi's line, so a mean below the line is a pick on the underdog.")
    L.append("")
    body = []
    for div in (None, "fbs", "cross"):
        b = _by(sub, None, div)
        gap = [r["m_blend"] - (ANCHOR_W * r["m_model"] + (1 - ANCHOR_W) * r["m_mkt"]) for r in b]
        fav = [r for r in b if abs(r["m_mkt"]) >= 3]
        dog_pick = [r for r in fav if (r["m_blend"] < r["m_mkt"]) == (r["m_mkt"] > 0)]      # engine sits on the dog's side of the line
        dog_cover = [r for r in dog_pick if (r["margin"] - r["m_mkt"]) * (1 if r["m_mkt"] > 0 else -1) < 0]
        fav_cover_all = [r for r in fav if (r["margin"] - r["m_mkt"]) * (1 if r["m_mkt"] > 0 else -1) > 0]
        import statistics as S
        body.append([div or "all", len(b), f"{S.mean(gap):+.2f}", f"{S.mean(g for g, r in zip(gap, b) if abs(r['m_mkt']) >= 14):+.2f}" if any(abs(r['m_mkt']) >= 14 for r in b) else "-",
                     len(fav), f"{100*len(dog_pick)/len(fav):.1f}%", f"{100*len(dog_cover)/max(1,len(dog_pick)):.1f}%",
                     f"{100*len(fav_cover_all)/len(fav):.1f}%"])
    L.append(C.md_table(["division", "n", "mean (m_blend - fair_in)", "same, lines of 14+", "games with |line| >= 3", "engine on the dog's side", "dog covered when picked", "favourite covered, all"], body))
    L.append("")
    # the total lean
    L.append("**The total lean.** The card leans under when the engine's total sits below Kalshi's line. With the engine's total near-constant, the lean is a function of the line alone.")
    L.append("")
    body = []
    tsub = _ok(rows, ["t_blend", "total_line"])
    for div in (None, "fbs", "cross"):
        b = _by(tsub, None, div)
        under = [r for r in b if r["t_blend"] < r["total_line"]]
        over = [r for r in b if r["t_blend"] >= r["total_line"]]
        body.append([div or "all", len(b), f"{100*len(under)/len(b):.1f}%",
                     f"{100*sum(1 for r in under if r['total'] < r['total_line'])/max(1,len(under)):.1f}%",
                     f"{100*sum(1 for r in over if r['total'] > r['total_line'])/max(1,len(over)):.1f}%",
                     f"{S.mean(r['total_line'] for r in under):.1f} / {S.mean(r['total_line'] for r in over):.1f}" if under and over else "-"])
    L.append(C.md_table(["division", "n", "leans under", "under hit when leaned under", "over hit when leaned over", "mean line, under-leans / over-leans"], body))
    L.append("")
    return "\n".join(L)


def engine_constancy(rows):
    """How much the card's total actually varies with the matchup."""
    sub = _ok(rows, ["t_eng", "total_line"])
    te = np.array([r["t_eng"] for r in sub]); tl = np.array([r["total_line"] for r in sub]); ta = np.array([r["total"] for r in sub])
    c_el = float(np.corrcoef(te, tl)[0, 1]); c_ea = float(np.corrcoef(te, ta)[0, 1]); c_la = float(np.corrcoef(tl, ta)[0, 1])
    return (f"The card's total across {len(sub)} priced games: mean {te.mean():.1f}, SD {te.std():.2f} (the closing total's SD {tl.std():.2f}, "
            f"the actual total's {ta.std():.2f}); correlation with the closing total {c_el:.2f}, with the actual total {c_ea:.2f} "
            f"(the closing total's with the actual {c_la:.2f}).")


def report(rows_path):
    with open(rows_path) as fh:
        d = json.load(fh)
    rows = d["rows"]
    counts = {s: len([r for r in rows if r["season"] == s]) for s in SEASONS}
    priced = {s: len(_ok([r for r in rows if r["season"] == s], ["spread", "ml_home", "ml_away"])) for s in SEASONS}
    divs = {dv: len([r for r in rows if r["division"] == dv]) for dv in DIVS}
    out = [f"College replay rows built {d['built']} (joined {d.get('joined', '-')}), {d['n_sims']} engine games a matchup, constants {d['constants']}; "
           f"{len(rows)} completed games with both sides rated (2024: {counts[2024]}, 2025: {counts[2025]}, 2026: {counts[2026]}); "
           f"priced (ESPN BET spread + moneyline stored): {priced}; by division {divs}.", ""]
    out += ["### 3.1 Win probability", "", win_tables(rows), disagreement_tables(rows), band_tables(rows), calibration_tables(rows),
            headline_tables(rows)]
    od_text, preds, best_k = od_tables(rows)
    out += ["### 3.2 Margins, totals, team points", "", engine_constancy(rows), "",
            point_tables(rows, extra={f"offence/defence rating total (K {best_k})": preds}),
            "### 4.1 The total candidate (pre-registered)", "", od_text,
            "### 4.2 The margin constants", "", constants_tables(rows),
            "### 4.3 Stacks and the anchor weight (pre-registered)", "", stack_tables(rows)]
    return "\n".join(out)


def main(argv):
    if argv and argv[0] == "report":
        text = report(argv[1])
        if "--out" in argv:
            with open(argv[argv.index("--out") + 1], "w") as fh:
                fh.write(text + "\n")
        print(text)
        return 0
    if argv and argv[0] == "join":
        n = int(argv[argv.index("--n") + 1]) if "--n" in argv else 1000
        return join(argv[1], argv[2], n)
    if not argv or argv[0] != "replay":
        print(__doc__)
        return 2
    raw = argv[1]
    n, out = 1000, os.path.join(os.path.dirname(raw.rstrip("/")), "cfb_rows.json")
    i = 2
    while i < len(argv):
        if argv[i] == "--n":
            n = int(argv[i + 1]); i += 2
        elif argv[i] == "--out":
            out = argv[i + 1]; i += 2
        else:
            i += 1
    get = install_cache(raw)
    rows = []
    for season in SEASONS:
        r, _m = replay_season(raw, season, n, get)
        rows.extend(r)
    with open(out, "w") as fh:
        json.dump({"built": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "n_sims": n,
                   "constants": {"prior_k": cfb._PRIOR_K, "cap": cfb._MARGIN_CAP, "hfa": cfb._HFA_MARGIN,
                                 "sigma": cfb._SIGMA, "reg": cfb._REG, "div_gap": cfb._DIV_GAP,
                                 "base_ppg": cfb._BASE_PPG, "anchor_w": ANCHOR_W},
                   "rows": rows}, fh)
    print(f"[xref-cfb] wrote {len(rows)} rows to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
