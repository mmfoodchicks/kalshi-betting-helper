"""NFL game cross-reference replay (research/reports/game_xref.md). RESEARCH, read-only.

Replays the PRODUCTION drive sim (nfl_game_sim.simulate_game on nfl_data's
Sleeper team profiles, the shipped trims and home split) week by week over
2024-2026 and scores it beside every other pre-game number the app could read,
against nflverse's closing lines on the same games:

  p_sim / m_sim / t_sim   the drive sim's home win probability, mean margin, total
  pts_h / pts_a           Sleeper's implied team points (nfl_data.week_teams' sum)
  p_pd                    nfl_season._wp on the in-season points-differential
                          ratings (the deep validation's PD arm, from results
                          before the week only)
  p_fpi / m_fpi           ESPN's FPI predictor (gameProjection, teamPredPtDiff),
                          read from the core API's stored run for the game
  p_mkt / spread / total  nflverse closing moneyline (de-vigged), spread, total

Honesty rules of the replay:
  * the defence factor's points-allowed is computed from results BEFORE the
    week (production reads ESPN's standings at build time, the same thing);
    nothing the sim sees is from the game or after it;
  * Sleeper's historical projection for a week is the FINAL pre-kickoff one
    (Sleeper keeps no earlier snapshot), so the replay is the sim as it stood
    at kickoff, slightly better informed than a Tuesday build;
  * the sim runs with the trims SHIPPED today (_TD_CAL 0.992 / _FG_CAL 1.073,
    fitted on 2026). 2025's Sleeper ran hot, so 2025's totals read high here
    by design; the report says so where it matters;
  * the ESPN predictor payload is the LAST stored run for the game. For a
    played game that is the pre-kickoff run (the predictor is not updated
    in-game), but it is ESPN's own feed and cannot be re-verified here.

usage: VIGIL_NO_BG=1 python3 -m research.game_xref_nfl replay <raw dir> [--n 2400] [--out rows.json]
       VIGIL_NO_BG=1 python3 -m research.game_xref_nfl report <rows.json> [--raw <raw dir>] [--out report.md]
  <raw dir> holds games.csv (nflverse), sleeper/proj_{s}_w{w}.json, sleeper/projk_{s}_w{w}.json
  and nfl_pred/{espn id}.json.
"""
import csv
import json
import os
import sys
import time
import zlib
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("VIGIL_NO_BG", "1")

import racing                      # noqa: E402
import nfl_live                    # noqa: E402
import nfl_data                    # noqa: E402
import nfl_game_sim as G           # noqa: E402
import nfl_season                  # noqa: E402
from research import game_xref_common as C   # noqa: E402

SEASONS = (2024, 2025, 2026)
HOME_EDGE_PD = 2.1                 # nfl_season._wp's default, the validation's value


def canon(ab):
    return nfl_data._canon(ab)


def load_games(raw):
    out = defaultdict(list)
    for r in csv.DictReader(open(os.path.join(raw, "games.csv"))):
        if r["game_type"] != "REG" or int(r["season"]) not in SEASONS or r["result"] in ("", "NA"):
            continue
        out[(int(r["season"]), int(r["week"]))].append({
            "season": int(r["season"]), "week": int(r["week"]), "espn": r["espn"],
            "home": canon(r["home_team"]), "away": canon(r["away_team"]),
            "home_pts": int(float(r["home_score"])), "away_pts": int(float(r["away_score"])),
            "neutral": r["location"] == "Neutral", "gameday": r["gameday"], "gametime": r["gametime"],
            "spread": float(r["spread_line"]) if r["spread_line"] not in ("", "NA") else None,
            "total_line": float(r["total_line"]) if r["total_line"] not in ("", "NA") else None,
            "ml_home": float(r["home_moneyline"]) if r["home_moneyline"] not in ("", "NA") else None,
            "ml_away": float(r["away_moneyline"]) if r["away_moneyline"] not in ("", "NA") else None,
            "div_game": r["div_game"] == "1", "roof": r["roof"]})
    return out


def pa_to_date(games_by, season, week):
    """{abbr: {pa_pg, pf_pg, g, w, l}} from the season's games before `week`."""
    pf, pa, g, w, l = defaultdict(float), defaultdict(float), defaultdict(int), defaultdict(int), defaultdict(int)
    for (s, wk), gs in games_by.items():
        if s != season or wk >= week:
            continue
        for r in gs:
            h, a = r["home"], r["away"]
            pf[h] += r["home_pts"]; pa[h] += r["away_pts"]; g[h] += 1
            pf[a] += r["away_pts"]; pa[a] += r["home_pts"]; g[a] += 1
            if r["home_pts"] > r["away_pts"]:
                w[h] += 1; l[a] += 1
            elif r["home_pts"] < r["away_pts"]:
                w[a] += 1; l[h] += 1
    return {ab: {"pa_pg": round(pa[ab] / g[ab], 2), "pf_pg": round(pf[ab] / g[ab], 2), "g": g[ab],
                 "w": w[ab], "l": l[ab]} for ab in g}


def pd_ratings(rt):
    """nfl_season's in-season ratings from the record alone (the deep
    validation's _pd_rating): 8.5 blended toward 8.5 + 0.5 x PD/g at g/(g+6)."""
    out = {}
    for ab in nfl_season._DIV_OF:
        r = rt.get(ab)
        if r and r["g"]:
            live = 8.5 + (r["pf_pg"] - r["pa_pg"]) * nfl_season._WINS_PER_PD
            w = r["g"] / (r["g"] + 6.0)
            out[ab] = (1 - w) * 8.5 + w * live
        else:
            out[ab] = 8.5
    return out


def fpi(raw, espn_id):
    try:
        d = json.load(open(os.path.join(raw, "nfl_pred", f"{espn_id}.json")))
    except Exception:
        return None, None
    try:
        st = {s["name"]: s["value"] for s in d["homeTeam"]["statistics"]}
        return st.get("gameProjection"), st.get("teamPredPtDiff")
    except Exception:
        return None, None


def replay_week(raw, games_by, season, week, n):
    rows = []
    gs = games_by.get((season, week)) or []
    if not gs:
        return rows
    skill_path = os.path.join(raw, "sleeper", f"proj_{season}_w{week}.json")
    k_path = os.path.join(raw, "sleeper", f"projk_{season}_w{week}.json")
    if not (os.path.exists(skill_path) and os.path.exists(k_path)):
        return rows
    skill = json.load(open(skill_path))
    kick = json.load(open(k_path))
    rt = pa_to_date(games_by, season, week)

    # --- the production data layer, fed the week's archived inputs ----------
    racing._get_json = lambda url, timeout=12: (kick if "position%5B%5D=K" in url or "position[]=K" in url else skill)
    nfl_live.team_ratings = lambda s: {ab: dict(v) for ab, v in rt.items()}
    nfl_live.schedule = lambda wk, s, seasontype=2: [
        {"home": r["home"], "away": r["away"], "date": f"{r['gameday']}T{r['gametime'] or '13:00'}",
         "state": "pre", "home_name": r["home"], "away_name": r["away"]} for r in gs]
    pairs = nfl_data.week_games(str(season), week) or []
    by_pair = {(h["abbr"], a["abbr"]): (h, a) for h, a in pairs}
    rtg = pd_ratings(rt)
    for r in gs:
        key = (r["home"], r["away"])
        row = dict(r)
        row["y"] = 1.0 if r["home_pts"] > r["away_pts"] else 0.0 if r["home_pts"] < r["away_pts"] else 0.5
        row["margin"] = r["home_pts"] - r["away_pts"]
        row["total"] = r["home_pts"] + r["away_pts"]
        if r["ml_home"] is not None and r["ml_away"] is not None:
            row["p_mkt"] = C.devig(C.american_to_prob(r["ml_home"]), C.american_to_prob(r["ml_away"]))
        else:
            row["p_mkt"] = None
        row["p_pd"] = nfl_season._wp(rtg[r["home"]], rtg[r["away"]], 0.0 if r["neutral"] else HOME_EDGE_PD)
        row["p_fpi"], m_fpi = fpi(raw, r["espn"])
        if row["p_fpi"] is not None:
            row["p_fpi"] = row["p_fpi"] / 100.0
        row["m_fpi"] = m_fpi
        if key in by_pair:
            h, a = by_pair[key]
            if r["neutral"]:
                h = dict(h, home=None)          # the sim halves the edge on an unknown venue
            sim = G.simulate_game(h, a, n=n, seed=zlib.crc32(f"{season}|{week}|{a['abbr']}@{h['abbr']}".encode()))
            row.update({"p_sim": sim["p_home"], "m_sim": sim["mean_margin"], "t_sim": sim["exp_total"],
                        "eh_sim": sim["exp_home"], "ea_sim": sim["exp_away"],
                        "pts_h": h["points"], "pts_a": a["points"],
                        "def_h": G.def_factor(a.get("def_pa_pg"), h.get("lg_pa_pg")),
                        "def_a": G.def_factor(h.get("def_pa_pg"), a.get("lg_pa_pg"))})
        else:
            row.update({"p_sim": None, "m_sim": None, "t_sim": None})
        rows.append(row)
    return rows


# ---- the report stage ------------------------------------------------------------
def rejoin_fpi(rows, raw):
    for r in rows:
        p, m = fpi(raw, r["espn"])
        r["p_fpi"] = (p / 100.0) if p is not None else None
        r["m_fpi"] = m


def _clusters(rows):
    return [(r["season"], r["week"]) for r in rows]


def _ok(rows, keys):
    return [r for r in rows if all(r.get(k) is not None for k in keys)]


def _slp_p(r):
    """Sleeper's implied margin through the ratings' margin SD (no venue
    term: Sleeper's projections carry none, nfl_game_sim's _HFA_SCORE note)."""
    return C.phi((r["pts_h"] - r["pts_a"]) / 13.2)


WIN_MODELS = [("p_sim", "drive sim (raw)"), ("p_pd", "PD ratings (nfl_season._wp)"),
              ("p_fpi", "ESPN FPI"), ("p_slp", "Sleeper points -> Phi(m/13.2)"),
              ("p_mkt", "closing moneyline, de-vigged"), ("p_const", "constant home 0.54")]


def win_tables(rows):
    L = []
    for r in rows:
        r["p_slp"] = _slp_p(r)
        r["p_const"] = 0.54
    for title, sub in [("all three seasons", rows)] + [(str(s), [r for r in rows if r["season"] == s]) for s in SEASONS]:
        sub = _ok(sub, ["p_sim", "p_pd", "p_fpi", "p_mkt"])
        ys = [r["y"] for r in sub]
        cl = _clusters(sub)
        L.append(f"**{title}** (n = {len(sub)})")
        L.append("")
        hdr = ["model", "log loss", "Brier", "SU accuracy", "LL - market (+/- SE)", "acc - market (+/- SE)"]
        body = []
        pm = [r["p_mkt"] for r in sub]
        for key, name in WIN_MODELS:
            ps = [r[key] for r in sub]
            sc = C.score(ps, ys, cl)
            dl, sl = C.paired(ps, pm, ys, cl, "ll")
            da, sa = C.paired(ps, pm, ys, cl, "acc")
            body.append([name, sc["log_loss"], sc["brier"], f"{sc['accuracy']:.3f} (+/- {sc['acc_se']:.3f})",
                         f"{dl:+.4f} (+/- {sl:.4f})", f"{da:+.3f} (+/- {sa:.3f})"])
        L.append(C.md_table(hdr, body))
        L.append("")
    return "\n".join(L)


def disagreement_tables(rows):
    L = []
    sub = _ok(rows, ["p_sim", "p_pd", "p_fpi", "p_mkt"])
    ys = [r["y"] for r in sub]
    L.append("Games where a model picks the other side from the closing market favourite, and who was right (all seasons):")
    L.append("")
    body = []
    for key, name in WIN_MODELS[:4]:
        d = C.disagreements([r[key] for r in sub], [r["p_mkt"] for r in sub], ys)
        body.append([name, d["n_disagree"], d["a_right"], d["b_right"]])
    L.append(C.md_table(["model", "disagreements", "model right", "market right"], body))
    L.append("")
    # agreement tiers: how many of sim / pd / fpi side with the market favourite
    L.append("The market favourite's record by how many of the three models (sim, PD, FPI) agree with it:")
    L.append("")
    tiers = defaultdict(lambda: [0, 0, 0.0])
    for r in sub:
        fav_home = r["p_mkt"] >= 0.5
        k = sum(1 for key in ("p_sim", "p_pd", "p_fpi") if (r[key] >= 0.5) == fav_home)
        won = (r["y"] >= 0.5) == fav_home
        t = tiers[k]
        t[0] += 1; t[1] += won; t[2] += r["p_mkt"] if fav_home else 1 - r["p_mkt"]
    body = [[k, t[0], f"{t[1]/t[0]:.3f}", f"{t[2]/t[0]:.3f}"] for k, t in sorted(tiers.items())]
    L.append(C.md_table(["models agreeing", "games", "favourite won", "market's own mean p"], body))
    L.append("")
    return "\n".join(L)


def calibration_tables(rows):
    L = []
    sub = _ok(rows, ["p_sim", "p_mkt"])
    ys = [r["y"] for r in sub]
    for key, name in (("p_sim", "drive sim (raw)"), ("p_mkt", "closing moneyline")):
        L.append(f"Calibration, {name}, favourite's side, all seasons:")
        L.append("")
        L.append(C.md_table(["bin", "n", "mean predicted", "realized"], C.calibration([r[key] for r in sub], ys)))
        L.append("")
    return "\n".join(L)


def band_tables(rows):
    L = ["Straight-up accuracy / log loss by week band (all seasons):", ""]
    sub = _ok(rows, ["p_sim", "p_pd", "p_fpi", "p_mkt"])
    body = []
    for lo, hi in ((1, 4), (5, 9), (10, 18)):
        b = [r for r in sub if lo <= r["week"] <= hi]
        ys = [r["y"] for r in b]
        cells = [f"w{lo}-{hi}", len(b)]
        for key, _n in WIN_MODELS[:5]:
            sc = C.score([r[key] for r in b], ys)
            cells.append(f"{sc['accuracy']:.3f} / {sc['log_loss']:.4f}")
        body.append(cells)
    L.append(C.md_table(["weeks", "n", "sim", "PD", "FPI", "Sleeper pts", "market"], body))
    L.append("")
    return "\n".join(L)


def point_tables(rows):
    L = []
    for title, sub in [("all three seasons", rows)] + [(str(s), [r for r in rows if r["season"] == s]) for s in SEASONS]:
        sub = _ok(sub, ["m_sim", "spread", "m_fpi", "t_sim", "total_line"])
        cl = _clusters(sub)
        L.append(f"**{title}** (n = {len(sub)}). Margin = home - away; bias = predicted - actual; slope = actual on predicted.")
        L.append("")
        body = []
        act = [r["margin"] for r in sub]
        for key, name, fn in (("m_sim", "drive sim mean margin", lambda r: r["m_sim"]),
                              ("m_slp", "Sleeper points, home - away (no venue term)", lambda r: r["pts_h"] - r["pts_a"]),
                              ("m_fpi", "ESPN FPI margin", lambda r: r["m_fpi"]),
                              ("spread", "closing spread (home favoured = +)", lambda r: r["spread"])):
            pred = [fn(r) for r in sub]
            sc = C.point_scores(pred, act, cl)
            dm, se = C.paired_mae(pred, [r["spread"] for r in sub], act, cl)
            body.append([name, f"{sc['bias']:+.2f} (+/- {sc['bias_se']:.2f})", sc["mae"], sc["rmse"],
                         f"{sc['slope']:.2f} (+/- {sc['slope_se']:.2f})", sc["pred_sd"], f"{dm:+.2f} (+/- {se:.2f})"])
        L.append(C.md_table(["margin model", "bias", "MAE", "RMSE", "slope", "pred SD", "MAE - spread's"], body))
        L.append("")
        body = []
        act = [r["total"] for r in sub]
        for key, name, fn in (("t_sim", "drive sim total (shipped trims)", lambda r: r["t_sim"]),
                              ("t_slp", "Sleeper team points, summed (nfl_data)", lambda r: r["pts_h"] + r["pts_a"]),
                              ("total_line", "closing total", lambda r: r["total_line"])):
            pred = [fn(r) for r in sub]
            sc = C.point_scores(pred, act, cl)
            dm, se = C.paired_mae(pred, [r["total_line"] for r in sub], act, cl)
            body.append([name, f"{sc['bias']:+.2f} (+/- {sc['bias_se']:.2f})", sc["mae"], sc["rmse"],
                         f"{sc['slope']:.2f} (+/- {sc['slope_se']:.2f})", sc["pred_sd"], f"{dm:+.2f} (+/- {se:.2f})"])
        L.append(C.md_table(["total model", "bias", "MAE", "RMSE", "slope", "pred SD", "MAE - total line's"], body))
        L.append("")
    # team points
    sub = _ok(rows, ["eh_sim", "spread", "total_line"])
    L.append(f"Team points (both sides pooled, n = {2*len(sub)} team-games): the sim's expected points against the market's implied split (total/2 +/- spread/2).")
    L.append("")
    act = [r["home_pts"] for r in sub] + [r["away_pts"] for r in sub]
    cl = _clusters(sub) * 2
    body = []
    for name, pred in (("drive sim exp points", [r["eh_sim"] for r in sub] + [r["ea_sim"] for r in sub]),
                       ("Sleeper team points", [r["pts_h"] for r in sub] + [r["pts_a"] for r in sub]),
                       ("market implied", [r["total_line"] / 2 + r["spread"] / 2 for r in sub] + [r["total_line"] / 2 - r["spread"] / 2 for r in sub])):
        sc = C.point_scores(pred, act, cl, boot=200)
        body.append([name, f"{sc['bias']:+.2f}", sc["mae"], f"{sc['slope']:.2f}", sc["pred_sd"], sc["actual_sd"]])
    L.append(C.md_table(["team points", "bias", "MAE", "slope", "pred SD", "actual SD"], body))
    L.append("")
    return "\n".join(L)


def temperature_tables(rows):
    """The production calibrator's shape, replayed: a temperature on the sim
    fitted on the seasons before, scored on the next (walk-forward)."""
    L = ["The calibrator's job, walk-forward (temperature fitted on the earlier seasons, applied to the next):", ""]
    body = []
    for fit_s, test_s in (((2024,), 2025), ((2024, 2025), 2026)):
        fit = _ok([r for r in rows if r["season"] in fit_s], ["p_sim"])
        test = _ok([r for r in rows if r["season"] == test_s], ["p_sim", "p_mkt"])
        t = C.fit_temperature([r["p_sim"] for r in fit], [r["y"] for r in fit])
        raw = [r["p_sim"] for r in test]
        cal = [C.sigmoid(C.logit(p) / t) for p in raw]
        ys = [r["y"] for r in test]
        cl = _clusters(test)
        d, se = C.paired(cal, raw, ys, cl, "ll")
        body.append([f"{'+'.join(map(str, fit_s))} -> {test_s}", t, C.score(raw, ys)["log_loss"], C.score(cal, ys)["log_loss"],
                     f"{d:+.4f} (+/- {se:.4f})", C.score([r["p_mkt"] for r in test], ys)["log_loss"]])
    L.append(C.md_table(["fit -> test", "temperature t", "raw sim LL", "calibrated LL", "cal - raw (+/- SE)", "market LL"], body))
    L.append("")
    return "\n".join(L)


STACKS = {"S1 [sim]": ["sim"], "S2 [sim, pd]": ["sim", "pd"], "S3 [sim, pd, fpi, slp]": ["sim", "pd", "fpi", "slp"],
          "S4 [mkt]": ["mkt"], "S5 [mkt, sim]": ["mkt", "sim"], "S6 [mkt, sim, pd, fpi, slp]": ["mkt", "sim", "pd", "fpi", "slp"]}
FEAT = {"sim": lambda r: C.logit(r["p_sim"]), "pd": lambda r: C.logit(r["p_pd"]), "fpi": lambda r: C.logit(r["p_fpi"]),
        "slp": lambda r: C.logit(_slp_p(r)), "mkt": lambda r: C.logit(r["p_mkt"])}
LAM = 1.0


def stack_tables(rows):
    L = ["Pre-registered stacks (ridge logistic, lambda 1.0, intercept free), walk-forward. 'vs market' is the paired log-loss difference against the de-vigged close (negative = the stack is better), clustered SE by week.", ""]
    rows = _ok(rows, ["p_sim", "p_pd", "p_fpi", "p_mkt"])
    body = []
    for name, feats in STACKS.items():
        pooled_p, pooled_m, pooled_y, pooled_c, per = [], [], [], [], []
        weights = []
        for fit_s, test_s in (((2024,), 2025), ((2024, 2025), 2026)):
            fit = [r for r in rows if r["season"] in fit_s]
            test = [r for r in rows if r["season"] == test_s]
            X = [[FEAT[f](r) for f in feats] for r in fit]
            b0, w = C.ridge_logistic(X, [r["y"] for r in fit], LAM)
            ps = C.predict_logistic(b0, w, [[FEAT[f](r) for f in feats] for r in test])
            ys = [r["y"] for r in test]
            pm = [r["p_mkt"] for r in test]
            d, se = C.paired(ps, pm, ys, _clusters(test), "ll")
            per.append(f"{test_s}: {C.score(ps, ys)['log_loss']:.4f} ({d:+.4f})")
            weights.append(f"{test_s}: " + ", ".join(f"{f} {x:+.2f}" for f, x in zip(feats, w)) + f", b0 {b0:+.2f}")
            pooled_p += ps; pooled_m += pm; pooled_y += ys; pooled_c += _clusters(test)
        d, se = C.paired(pooled_p, pooled_m, pooled_y, pooled_c, "ll")
        da, sa = C.paired(pooled_p, pooled_m, pooled_y, pooled_c, "acc")
        verdict = "PASS" if (d < -2 * se and all("(-" in x for x in per)) else "no"
        body.append([name, C.score(pooled_p, pooled_y)["log_loss"], f"{d:+.4f} (+/- {se:.4f})", f"{da:+.3f} (+/- {sa:.3f})",
                     "; ".join(per), verdict])
        L.append(f"- {name} weights: " + " | ".join(weights))
    L.append("")
    L.append(C.md_table(["stack", "pooled test LL", "vs market LL", "vs market acc", "per test season (LL, vs market)", "2-SE pass"], body))
    L.append("")
    return "\n".join(L)


MSTACKS = {"M1 [m_sim]": ["m_sim"], "M2 [spread]": ["spread"], "M3 [spread, m_sim]": ["spread", "m_sim"],
           "M4 [spread, m_sim, m_fpi, m_slp]": ["spread", "m_sim", "m_fpi", "m_slp"]}
MFEAT = {"m_sim": lambda r: r["m_sim"], "spread": lambda r: r["spread"], "m_fpi": lambda r: r["m_fpi"],
         "m_slp": lambda r: r["pts_h"] - r["pts_a"]}
TSTACKS = {"T1 [t_sim]": ["t_sim"], "T2 [total]": ["total"], "T3 [total, t_sim, t_slp]": ["total", "t_sim", "t_slp"]}
TFEAT = {"t_sim": lambda r: r["t_sim"], "total": lambda r: r["total_line"], "t_slp": lambda r: r["pts_h"] + r["pts_a"]}


def linear_stack_tables(rows, stacks, feat, target, base_key, label):
    L = [f"Pre-registered {label} stacks (ridge linear, lambda 1.0), walk-forward; 'vs {base_key}' is the paired MAE difference against the closing line (negative = better), clustered SE.", ""]
    rows = _ok(rows, ["m_sim", "m_fpi", "spread", "t_sim", "total_line"])
    body = []
    for name, feats in stacks.items():
        pooled_p, pooled_b, pooled_y, pooled_c, per, weights = [], [], [], [], [], []
        for fit_s, test_s in (((2024,), 2025), ((2024, 2025), 2026)):
            fit = [r for r in rows if r["season"] in fit_s]
            test = [r for r in rows if r["season"] == test_s]
            b0, w = C.ridge_linear([[feat[f](r) for f in feats] for r in fit], [r[target] for r in fit], LAM)
            ps = C.predict_linear(b0, w, [[feat[f](r) for f in feats] for r in test])
            ys = [r[target] for r in test]
            base = [feat[base_key](r) for r in test]
            d, se = C.paired_mae(ps, base, ys, _clusters(test))
            sc = C.point_scores(ps, ys, None, boot=0)
            per.append(f"{test_s}: MAE {sc['mae']:.2f} ({d:+.2f})")
            weights.append(f"{test_s}: " + ", ".join(f"{f} {x:+.2f}" for f, x in zip(feats, w)) + f", b0 {b0:+.2f}")
            pooled_p += ps; pooled_b += base; pooled_y += ys; pooled_c += _clusters(test)
        d, se = C.paired_mae(pooled_p, pooled_b, pooled_y, pooled_c)
        sc = C.point_scores(pooled_p, pooled_y, None, boot=0)
        verdict = "PASS" if (d < -2 * se and all("(-" in x for x in per)) else "no"
        body.append([name, sc["mae"], f"{d:+.2f} (+/- {se:.2f})", "; ".join(per), verdict])
        L.append(f"- {name} weights: " + " | ".join(weights))
    L.append("")
    L.append(C.md_table(["stack", "pooled test MAE", f"vs {base_key} MAE", "per test season", "2-SE pass"], body))
    L.append("")
    return "\n".join(L)


def anchor_tables(rows):
    """The production knob the college board already has: the headline as
    w x model + (1 - w) x the de-vigged market price. w is chosen on the fit
    seasons by log loss from the grid and scored on the next season (rule in
    the report, section 0: pass = beats the raw sim by > 2 SE pooled and the
    same sign each season; the market itself is the reference beside it)."""
    L = ["Market anchor on the win probability, walk-forward: w chosen on the fit seasons from {0, 0.1, 0.2, 0.3, 0.5, 0.7, 1.0} by log loss; p = w x sim + (1 - w) x market.", ""]
    rows = _ok(rows, ["p_sim", "p_mkt"])
    grid = (0.0, 0.1, 0.2, 0.3, 0.5, 0.7, 1.0)
    body = []
    pooled = {w: ([], [], []) for w in grid}
    chosen = []
    for fit_s, test_s in (((2024,), 2025), ((2024, 2025), 2026)):
        fit = [r for r in rows if r["season"] in fit_s]
        test = [r for r in rows if r["season"] == test_s]
        yf = [r["y"] for r in fit]
        best = min(grid, key=lambda w: sum(C.ll_terms([w * r["p_sim"] + (1 - w) * r["p_mkt"] for r in fit], yf)))
        chosen.append((test_s, best))
        yt = [r["y"] for r in test]
        cl = _clusters(test)
        for w in grid:
            ps = [w * r["p_sim"] + (1 - w) * r["p_mkt"] for r in test]
            pooled[w][0].extend(ps); pooled[w][1].extend(yt); pooled[w][2].extend(cl)
            if w == best:
                d, se = C.paired(ps, [r["p_sim"] for r in test], yt, cl, "ll")
                dm, sem = C.paired(ps, [r["p_mkt"] for r in test], yt, cl, "ll")
                body.append([f"{'+'.join(map(str, fit_s))} -> {test_s}", best, C.score(ps, yt)["log_loss"],
                             f"{C.score(ps, yt)['accuracy']:.3f}", f"{d:+.4f} (+/- {se:.4f})", f"{dm:+.4f} (+/- {sem:.4f})"])
    L.append(C.md_table(["fit -> test", "chosen w", "test LL", "test SU acc", "vs raw sim LL", "vs market LL"], body))
    L.append("")
    L.append("Every w on the pooled test seasons (2025 + 2026), for the shape of the curve, with each test season's own difference against the raw sim:")
    L.append("")
    body = []
    base_sim = pooled[1.0]
    for w in grid:
        ps, ys, cl = pooled[w]
        d, se = C.paired(ps, base_sim[0], ys, cl, "ll")
        per = []
        for test_s in (2025, 2026):
            ii = [i for i, c in enumerate(cl) if c[0] == test_s]
            dd, ss = C.paired([ps[i] for i in ii], [base_sim[0][i] for i in ii], [ys[i] for i in ii], [cl[i] for i in ii], "ll")
            per.append(f"{test_s}: {dd:+.4f} (+/- {ss:.4f})")
        body.append([w, C.score(ps, ys)["log_loss"], f"{C.score(ps, ys)['accuracy']:.3f}", f"{d:+.4f} (+/- {se:.4f})", "; ".join(per)])
    L.append(C.md_table(["w (model share)", "pooled test LL", "SU acc", "vs raw sim LL", "per season"], body))
    L.append("")
    return "\n".join(L)


def scale_tables(rows):
    """Scale-only corrections, walk-forward: the sim's margin expanded by k
    (k = sum(m y)/sum(m^2) on the fit seasons, no intercept), and the sim's
    total shrunk toward the fit seasons' mean actual total by k_t (the slope
    of actual on predicted on the fit seasons)."""
    L = ["Scale-only corrections, walk-forward (no intercept; the fit seasons' slope applied to the next season):", ""]
    rows = _ok(rows, ["m_sim", "t_sim", "spread", "total_line"])
    body = []
    for fit_s, test_s in (((2024,), 2025), ((2024, 2025), 2026)):
        fit = [r for r in rows if r["season"] in fit_s]
        test = [r for r in rows if r["season"] == test_s]
        k = sum(r["m_sim"] * r["margin"] for r in fit) / sum(r["m_sim"] ** 2 for r in fit)
        pred = [k * r["m_sim"] for r in test]
        act = [r["margin"] for r in test]
        d, se = C.paired_mae(pred, [r["m_sim"] for r in test], act, _clusters(test))
        ds, ses = C.paired_mae(pred, [r["spread"] for r in test], act, _clusters(test))
        body.append([f"margin x k, {'+'.join(map(str, fit_s))} -> {test_s}", f"k = {k:.2f}", f"{C.point_scores(pred, act, None, boot=0)['mae']:.2f}",
                     f"{d:+.2f} (+/- {se:.2f})", f"{ds:+.2f} (+/- {ses:.2f})"])
        mt = sum(r["total"] for r in fit) / len(fit)
        ms = sum(r["t_sim"] for r in fit) / len(fit)
        dev = [(r["t_sim"] - ms) for r in fit]
        kt = sum(x * (r["total"] - mt) for x, r in zip(dev, fit)) / sum(x * x for x in dev)
        pred = [mt + kt * (r["t_sim"] - ms) for r in test]
        act = [r["total"] for r in test]
        d, se = C.paired_mae(pred, [r["t_sim"] for r in test], act, _clusters(test))
        ds, ses = C.paired_mae(pred, [r["total_line"] for r in test], act, _clusters(test))
        body.append([f"total shrunk, {'+'.join(map(str, fit_s))} -> {test_s}", f"k_t = {kt:.2f}, level {mt:.1f}",
                     f"{C.point_scores(pred, act, None, boot=0)['mae']:.2f}", f"{d:+.2f} (+/- {se:.2f})", f"{ds:+.2f} (+/- {ses:.2f})"])
    L.append(C.md_table(["correction", "fitted", "test MAE", "vs raw sim MAE", "vs closing line MAE"], body))
    L.append("")
    return "\n".join(L)


def report(rows_path, raw):
    with open(rows_path) as fh:
        d = json.load(fh)
    rows = d["rows"]
    if raw:
        rejoin_fpi(rows, raw)
    out = [f"NFL replay rows built {d['built']}, {d['n_sims']} sims a game, trims {d['trims']}; "
           f"{len(rows)} regular-season games (2024: {sum(1 for r in rows if r['season']==2024)}, "
           f"2025: {sum(1 for r in rows if r['season']==2025)}, 2026: {sum(1 for r in rows if r['season']==2026)}).", ""]
    out += ["### 1.1 Win probability", "", win_tables(rows), disagreement_tables(rows), band_tables(rows), calibration_tables(rows),
            temperature_tables(rows),
            "### 1.2 Margins, totals, team points", "", point_tables(rows),
            "### 2.1 Stacks (the pre-registered tests)", "", stack_tables(rows),
            linear_stack_tables(rows, MSTACKS, MFEAT, "margin", "spread", "margin"),
            linear_stack_tables(rows, TSTACKS, TFEAT, "total", "total", "total"),
            "### 2.2 The market anchor and scale-only corrections", "", anchor_tables(rows), scale_tables(rows)]
    return "\n".join(out)


def main(argv):
    if not argv or argv[0] not in ("replay", "report"):
        print(__doc__)
        return 2
    mode = argv[0]
    if mode == "report":
        rows_path = argv[1]
        raw = argv[argv.index("--raw") + 1] if "--raw" in argv else None
        out = argv[argv.index("--out") + 1] if "--out" in argv else None
        text = report(rows_path, raw)
        if out:
            with open(out, "w") as fh:
                fh.write(text + "\n")
        print(text)
        return 0
    raw = argv[1]
    n, out = 2400, os.path.join(ROOT, "research", "data", "game_xref_nfl_rows.json")
    i = 2
    while i < len(argv):
        if argv[i] == "--n":
            n = int(argv[i + 1]); i += 2
        elif argv[i] == "--out":
            out = argv[i + 1]; i += 2
        else:
            i += 1
    games_by = load_games(raw)
    rows = []
    t0 = time.time()
    for season in SEASONS:
        for week in range(1, 19):
            got = replay_week(raw, games_by, season, week, n)
            rows.extend(got)
            if got:
                sims = sum(1 for r in got if r.get("p_sim") is not None)
                print(f"[xref-nfl] {season} w{week}: {len(got)} games, {sims} simulated, {time.time()-t0:.0f}s", flush=True)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as fh:
        json.dump({"built": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "n_sims": n,
                   "trims": {"td": G._TD_CAL, "fg": G._FG_CAL, "proj_bias": G._PROJ_BIAS, "hfa": G._HFA_SCORE},
                   "rows": rows}, fh)
    print(f"[xref-nfl] wrote {len(rows)} rows to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
