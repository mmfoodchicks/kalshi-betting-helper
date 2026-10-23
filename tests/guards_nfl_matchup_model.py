"""Guards for the NFL matchup model (nfl_matchup.py; research in
research/nfl_matchup_fit.py). Hermetic: the data foundation's synthetic
season (tests/guards_nfl_matchup_data.py's fixture, built through the real
nfl_matchup_data.build_season), a synthetic coefficient file, no network,
no deep store, a few seconds.

    python3 tests/guards_nfl_matchup_model.py      # exits 1 on any failure
    from tests.guards_nfl_matchup_model import run  # run(ck) inside the suite

Pinned by definition, not by number alone:
- NO LEAKAGE: every feature as of (season, week) is unchanged when the games of
  that week and later are removed or altered; the participation-derived split
  rates read the PRIOR seasons only (a current season without participation,
  like 2026, never enters numerator or denominator); comparables read only
  games before the cutoff; a frozen Sleeper capture stamped at or after
  kickoff is never chosen as the pre-kickoff baseline.
- The split rates' numerators are the player's targets ON the plays the
  denominators count, across a team change (hand-computed on the fixture).
- Every market's P(stat >= k) is monotone in k and the band holds the point.
- apply() preserves every team total of every component, moves proj_pts by
  the PPR change, never raises on bad input, and is a no-op at weight 0.
- Factor effects sum EXACTLY to the applied change in every market.
- DraftKings scoring on a hand-computed line with both yardage bonuses, and
  the constants equal dk_scoring.NFL_OFF.
- The board payload: no NaN / inf, a unit on every role and opponent fact,
  monotone ladders, effects that equal what was applied.
- The 2026-10-23 review fixes, each guard failing on the code before it:
  the yards family is stored where the JSON keeps it and production draws it
  (F1); a NaN row no longer decides the family (F1, numpy); no band where no
  shipped coefficient carries uncertainty, a real one where one does (F2); the
  low-threshold rushing note (F3) and its pre-registered rule's hashes; the
  no-ladder fallback marked (N3); the dead DK-scale fit and the identity code
  map gone (N2, N5).
- The review's surviving mutants (F7): participation fields from a CHARTED
  current season, proe as a matchup term, the split's reliability side, the
  matchup_weight label, the 300-yard and 100-yard bonuses at exactly the
  line, and a replay of the pre-registered rule (accept() and the frozen
  walk) over the committed validate / test JSONs that must give coefs.json's
  shipped set.
"""
import ast
import copy
import hashlib
import json
import math
import os
import sys
import tempfile

os.environ["VIGIL_NO_BG"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import nfl_matchup as M                  # noqa: E402
import nfl_matchup_data as D             # noqa: E402
import prop_dist as PD                   # noqa: E402
from tests import guards_nfl_matchup_data as FX   # noqa: E402  (the shared synthetic season)

SEASON = 2030
REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
MODEL_DIR = os.path.join(REPO, "research", "data", "nfl_matchup_model")
REPORT = os.path.join(REPO, "research", "reports", "nfl_matchup_model.md")


def _load(name):
    with open(os.path.join(MODEL_DIR, name), encoding="utf-8") as fh:
        return json.load(fh)


def _block(start, end):
    """The text between two markers, markers included (how both rules hash)."""
    with open(REPORT, encoding="utf-8") as fh:
        txt = fh.read()
    a, b = txt.find(start), txt.find(end)
    return txt[a:b + len(end)] if 0 <= a < b and txt.count(start) == 1 and txt.count(end) == 1 else None


def _fn_node(path, name):
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    return next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name), None)


def _stores(tmp):
    """{2029: a charted prior season (the fixture with the review extras: a
    traded receiver), 2030: the same games uncharted -- no participation, the
    2026 shape}, plus the 2030 store with week 2 removed."""
    pbp, part, ftn, _pl, _dp = FX._write(tmp, extra=True)
    prior = dict(D.build_season(SEASON, pbp, part, ftn, positions=FX.POS), season=SEASON - 1)
    cur = D.build_season(SEASON, pbp, None, ftn, positions=FX.POS)
    t2 = os.path.join(tmp, "w1only")
    os.makedirs(t2)
    pbp2, _part2, ftn2, _pl2, _dp2 = FX._write(t2, extra=True, drop_games=(FX.G2, FX.G2B, FX.G2C))
    cur_w1 = D.build_season(SEASON, pbp2, None, ftn2, positions=FX.POS)
    return prior, cur, cur_w1


def _coefs():
    """A synthetic shipped model: a WR intercept and two features on targets,
    one on receiving yards, nothing elsewhere; dispersion for every market."""
    disp = {m: {p: {"alpha": 0.1, "n": 100} for p in ("WR", "TE", "RB", "QB")} for m in ("tgt", "rec", "car", "patt",
                                                                                      "pcmp", "ptd", "int")}
    disp["rec_yd"] = {p: {"cv": 0.7, "n": 100} for p in ("WR", "TE", "RB")}
    disp["rec_yd"]["family"] = "gamma"
    disp["rush_yd"] = {p: {"cv": 0.65, "n": 100} for p in ("RB", "QB", "WR")}
    disp["rush_yd"]["family"] = "lognormal"
    disp["pass_yd"] = {"QB": {"sd_c": 80.0, "sd_d": 0.0, "n": 100}}
    names = ["int[WR]", "cov_mz", "pass_epa"]
    models = {m: {"terms": [], "names": [], "b": [], "cov": []} for m in M.MARKETS}
    models["tgt"] = {"terms": [["int", "WR"], ["f", "cov_mz"], ["f", "pass_epa"]], "names": names,
                     "b": [0.05, 2.0, -1.5], "cov": [[1e-4, 0, 0], [0, 1e-2, 0], [0, 0, 1e-2]]}
    models["rec_yd"] = {"terms": [["f", "pass_epa"]], "names": ["pass_epa"], "b": [-1.0], "cov": [[1e-2]]}
    return {"schema": M.SCHEMA, "models": models, "dispersion": disp, "dk_scale": {p: 1.2 for p in M.POS},
            "fp_shift": 3.0, "shipped": {"tgt": ["R", "D2", "D5"], "rec_yd": ["D2"]},
            "dfs_hook": {"ships": True, "team_totals_free": False}}


def _coefs_pass():
    c = _coefs()
    c["models"]["patt"] = {"terms": [["f", "margin"]], "names": ["margin"], "b": [0.1], "cov": [[1e-4]]}
    return c


def run(ck):
    with tempfile.TemporaryDirectory() as tmp:
        prior, cur, cur_w1 = _stores(tmp)
    ctx = M.Ctx({SEASON - 1: prior, SEASON: cur}, FX.POS)
    ctx_w1 = M.Ctx({SEASON - 1: prior, SEASON: cur_w1}, FX.POS)
    ctx.save_history = ctx_w1.save_history = False

    # ---- leakage: features as of week 2 do not see week 2
    S = ctx.snap(SEASON, 2)
    ck("as-of snapshot holds nothing from the cutoff week", not S.contains(SEASON, 2) and S.contains(SEASON, 1))
    f_a = M.features(ctx, SEASON, 2, 100002, "KC", "BUF", "WR", margin=3.0, total=47.0, qb=100001)
    f_b = M.features(ctx_w1, SEASON, 2, 100002, "KC", "BUF", "WR", margin=3.0, total=47.0, qb=100001)
    keys = [k for k in f_a if not k.startswith("_")]
    ck("features as of week 2 identical with week 2's games removed (no leakage)",
       all(abs(f_a[k] - f_b[k]) < 1e-12 for k in keys), [k for k in keys if abs(f_a[k] - f_b[k]) >= 1e-12])
    d_a = M.defense_block(ctx, SEASON, 2, "BUF")
    d_b = M.defense_block(ctx_w1, SEASON, 2, "BUF")
    ck("defense block as of week 2 identical with week 2 removed",
       all(abs((d_a[k]["x"] or 0) - (d_b[k]["x"] or 0)) < 1e-12 for k in d_a if isinstance(d_a[k], dict) and "x" in d_a[k]))
    # prior-only participation fields: the uncharted current season adds nothing
    ck("coverage share reads the prior season only (src label)", d_a["man"]["src"].startswith("prior seasons only"))
    sp = M.player_splits(ctx, SEASON, 100002)
    sp_w1 = M.player_splits(ctx_w1, SEASON, 100002)
    ck("split rates identical whatever the current season holds (prior seasons only)", sp == sp_w1, (sp, sp_w1))
    ctx_charted = M.Ctx({SEASON - 1: prior, SEASON: dict(prior, season=SEASON)}, FX.POS)
    ck("a CHARTED current season never enters the split rates (production never has it in season)",
       M.player_splits(ctx_charted, SEASON, 100002) == sp, M.player_splits(ctx_charted, SEASON, 100002))
    ctx_none = M.Ctx({SEASON: cur}, FX.POS)
    sp0 = M.player_splits(ctx_none, SEASON, 100002)
    ck("split rates with no prior season are empty (the current season never enters)",
       all(v == 0.0 for v in sp0.values()), sp0)
    # same-play numerators across a team change (hand-computed on the fixture's prior season: he is on the field
    # for KC in G1, G2 and G2B and for BUF in G2C; dropbacks vs man: plays 1, 2, 33; vs zone: 3, 11, 31, 34; his
    # targets: 1 and 33 vs man, 3 and 11 vs zone; every count x0.5, the prior-season weight)
    ck("split numerators are his targets on the counted plays: 2 of 3 vs man, 2 of 4 vs zone (x0.5 prior weight)",
       abs(sp["t_man"] - 1.0) < 1e-9 and abs(sp["db_man"] - 1.5) < 1e-9 and abs(sp["t_zone"] - 1.0) < 1e-9
       and abs(sp["db_zone"] - 2.0) < 1e-9, sp)
    ck("shell split on the same plays: Cover 1 / 3 targets 4 of 7 single-high dropbacks (x0.5)",
       abs(sp["t_sh"] - 2.0) < 1e-9 and abs(sp["db_sh"] - 3.5) < 1e-9 and sp["t_th"] == 0.0, sp)
    ck("split rates never exceed one target per dropback", sp["t_man"] <= sp["db_man"] and sp["t_zone"] <= sp["db_zone"])
    hist = M.history(ctx, SEASON, 2, 100002, None, {})
    ck("history holds only games before the cutoff",
       all((r["season"], r["week"]) < (SEASON, 2) for _w, _s, r, _l, _v in hist) and len(hist) >= 1)
    try:
        import research.nfl_matchup_frame as FR
        kick = 1_000_000
        cands = [(kick - 3600.0, {"rec": 3.0}, "early", "KC", "BUF"), (kick - 60.0, {"rec": 4.0}, "late", "KC", "BUF"),
                 (kick + 1.0, {"rec": 9.0}, "after", "KC", "BUF"), (kick, {"rec": 8.0}, "at", "KC", "BUF")]
        c = FR.choose_frozen(cands, kick)
        ck("frozen baseline: the latest capture strictly before kickoff, never one at or after it", c and c[2] == "late", c)
        ck("frozen baseline: nothing before kickoff -> None", FR.choose_frozen(cands[2:], kick) is None)
    except ImportError as e:
        ck("research.nfl_matchup_frame importable for the frozen-baseline guard", False, str(e))

    # ---- shrinkage and the split feature
    ck("shrink: no evidence moves nothing", M.shrink(None, 10, 0.5, 5) == (0.0, 0.0) and M.shrink(0.6, 0, 0.5, 5) == (0.0, 0.0))
    x, rel = M.shrink(0.6, 15, 0.5, 5)
    ck("shrink: n/(n+k) x deviation", abs(rel - 0.75) < 1e-12 and abs(x - 0.075) < 1e-12)
    x, rel, _d = M._split_feature(10, 50, 10, 50, 0.9, 100.9)
    ck("split feature: no split -> 0 whatever the defense", abs(x) < 1e-12)
    x1, _r1, _ = M._split_feature(20, 50, 5, 50, 0.7, 100.9)
    x2, _r2, _ = M._split_feature(20, 50, 5, 50, 0.3, 100.9)
    ck("split feature: a man-heavy receiver gains vs a man-heavy defense, loses vs a zone-heavy one", x1 > 0 > x2, (x1, x2))
    # review F7 (d): the split is believed by its SMALLER side's dropbacks (10 of 10 / 200 -> 10 / 30)
    _x, rel_s, _d = M._split_feature(4, 10, 30, 200, 0.5, 20)
    ck("split reliability reads the smaller side: n / (n + k) with n = min(dropbacks) = 10, k 20 -> 1/3",
       abs(rel_s - 10.0 / 30.0) < 1e-12, rel_s)
    # review F7 (a): pressure / man / single-high read the PRIOR seasons by VALUE. The label guard above
    # survived a mutant that read the current season, because no current season in the fixture was
    # charted; production never has one in season, so a charted one must change nothing
    d_ch = M.defense_block(ctx_charted, SEASON, 2, "BUF")
    ck("pressure, man share and single-high share ignore a CHARTED current season (the values, not the labels)",
       all(d_ch[k]["v"] == d_a[k]["v"] and d_ch[k]["n"] == d_a[k]["n"] for k in ("pressure", "man", "sh_frac")),
       {k: (d_ch[k]["v"], d_a[k]["v"]) for k in ("pressure", "man", "sh_frac")})

    # ---- DraftKings scoring
    qb = {"pass_yd": 310, "pass_td": 2, "int": 1, "rush_yd": 25, "rush_td": 0}
    ck("DK: QB 310-2-1 + 25 rushing = 12.4 + 8 - 1 + 3 (300 bonus) + 2.5 = 24.9", abs(M.dk_points(qb) - 24.9) < 1e-9, M.dk_points(qb))
    wr = {"rec": 8, "rec_yd": 112, "rec_td": 1, "rush_yd": 104, "rush_td": 1, "fum_lost": 1, "two_pt": 1}
    ck("DK: 8-112-1 receiving + 104-1 rushing + fumble + 2pt = 8+11.2+6+3 + 10.4+6+3 - 1 + 2 = 48.6",
       abs(M.dk_points(wr) - 48.6) < 1e-9, M.dk_points(wr))
    ck("DK: 99 receiving yards has no bonus, 100 has", abs(M.dk_points({"rec_yd": 100}) - M.dk_points({"rec_yd": 99}) - 3.1) < 1e-9)
    ck("DK: the passing bonus at EXACTLY 300 (299 -> 300 adds 0.04 + 3) and the rushing one at exactly 100",
       abs(M.dk_points({"pass_yd": 300}) - M.dk_points({"pass_yd": 299}) - 3.04) < 1e-9
       and abs(M.dk_points({"rush_yd": 100}) - M.dk_points({"rush_yd": 99}) - 3.1) < 1e-9,
       (M.dk_points({"pass_yd": 300}), M.dk_points({"pass_yd": 299})))
    try:
        import dk_scoring
        o = dk_scoring.NFL_OFF
        ck("DK constants equal dk_scoring.NFL_OFF (one rulebook)",
           (M.DK["pass_yd"], M.DK["pass_td"], M.DK["int"], M.DK["pass_300"], M.DK["rush_yd"], M.DK["rush_td"], M.DK["rush_100"],
            M.DK["rec"], M.DK["rec_yd"], M.DK["rec_td"], M.DK["rec_100"], M.DK["fum_lost"], M.DK["two_pt"])
           == (o["pass_yd"], o["pass_td"], o["int"], o["pass_300"], o["rush_yd"], o["rush_td"], o["rush_100"], o["rec"],
               o["rec_yd"], o["rec_td"], o["rec_100"], o["fumble_lost"], o["two_point"]))
    except ImportError as e:
        ck("dk_scoring importable", False, str(e))

    # ---- distributions: monotone, band holds the point
    coefs = _coefs()
    base = {"rec_tgt": 7.0, "rec": 4.6, "rec_yd": 58.0, "rec_td": 0.4, "rush_att": 12.0, "rush_yd": 55.0, "rush_td": 0.35,
            "pass_att": 34.0, "pass_cmp": 22.0, "pass_yd": 250.0, "pass_td": 1.6, "pass_int": 0.7, "fum_lost": 0.1}
    ok_mono, ok_band = True, True
    for m, pos in (("tgt", "WR"), ("rec", "WR"), ("rec_yd", "WR"), ("car", "RB"), ("rush_yd", "RB"), ("pass_yd", "QB"),
                   ("patt", "QB"), ("ptd", "QB"), ("int", "QB")):
        mu = base[M.SLEEPER_FIELD[m]]
        ks = list(range(0, int(mu * 3) + 2, max(1, int(mu // 6) or 1)))
        lad = M.probs_with_band(lambda x, m=m, pos=pos: M.market_dist(coefs, m, x, pos, base), mu, 0.1, ks)
        ps = [lad[k][0] for k in sorted(lad)]
        ok_mono &= all(b <= a + 1e-12 for a, b in zip(ps, ps[1:]))
        ok_band &= all(lad[k][1] is not None and lad[k][2] is not None
                       and lad[k][1] - 1e-12 <= lad[k][0] <= lad[k][2] + 1e-12 for k in lad)
    ck("every market's P(stat >= k) is monotone in k", ok_mono)
    ck("every band holds its point (p_lo <= p <= p_hi)", ok_band)
    # review F2: with no shipped coefficient's uncertainty (se 0) there is no band at all -- never lo = hi = p,
    # which read as "robust" on every +EV line of the 2026-10-25 board
    lad0 = M.probs_with_band(lambda x: M.market_dist(coefs, "rec", x, "WR", base), 4.6, 0.0, [2, 3, 4])
    ck("no shipped uncertainty (se 0) -> p_lo = p_hi = None, p kept",
       lad0 and all(v[1] is None and v[2] is None and 0 < v[0] < 1 for v in lad0.values()), lad0)
    # review F1: the family is read from where the JSON keeps it ("family"), and production draws it
    ck("yards family: production draws the family the file names (rush_yd lognormal, rec_yd gamma)",
       M.market_dist(coefs, "rush_yd", 55.0, "RB", base).pos_family == "lognormal"
       and M.market_dist(coefs, "rec_yd", 58.0, "WR", base).pos_family == "gamma",
       (M.market_dist(coefs, "rush_yd", 55.0, "RB", base).pos_family,))
    c_nofam = copy.deepcopy(coefs)
    c_nofam["dispersion"]["rush_yd"].pop("family")
    c_bad = copy.deepcopy(coefs)
    c_bad["dispersion"]["rush_yd"]["family"] = "weibull"
    yf = getattr(M, "yards_family", None)
    ck("yards family: a file without the key reads gamma (the tested family); an unknown name reads gamma, never a crash",
       yf is not None and yf(c_nofam, "rush_yd") == "gamma"
       and M.market_dist(c_bad, "rush_yd", 55.0, "RB", base).pos_family == "gamma")
    committed = _load("coefs.json")
    fams = {m: (committed["dispersion"].get(m) or {}).get("family") for m in ("rec_yd", "rush_yd")}
    ck("committed coefs.json names each yards market's family (gamma: what the test scored)",
       fams == {"rec_yd": "gamma", "rush_yd": "gamma"}, fams)
    ck("production draws the committed family on both yards markets",
       all(M.market_dist(committed, m, mu_, pos_, base).pos_family == fams[m]
           for m, mu_, pos_ in (("rec_yd", 58.0, "WR"), ("rush_yd", 55.0, "RB"))))
    d70 = M.market_dist(coefs, "rec_yd", 70.0, "WR", base)
    p0 = PD.NegBin(4.6 * 70.0 / 58.0, 0.1).pmf(0)
    ck("yards family: point mass = P(0 receptions) at Sleeper's count line x the yards factor (70 / 58)",
       abs(d70.p0 - p0) < 1e-12 and abs(d70.mean() - 70.0) < 1e-9, (d70.p0, p0, d70.mean()))
    d = M.market_dist(coefs, "rec_yd", 58.0, "WR", base)
    ok_mean = True
    for m, pos in (("tgt", "WR"), ("rec", "WR"), ("rec_yd", "WR"), ("car", "RB"), ("rush_yd", "RB"), ("patt", "QB"),
                   ("ptd", "QB"), ("int", "QB")):
        for mu in (0.7, 3.0, 47.0):
            dd = M.market_dist(coefs, m, mu, pos, base)
            ok_mean &= dd is not None and abs(dd.mean() - mu) < 1e-9 * max(1.0, mu)
    ck("every count / yards family's mean IS the adjusted mean", ok_mean)
    dk = M.dk_dist(coefs, {"rec": 4.6, "rec_yd": 58.0, "rec_td": 0.4}, {"rec": PD.NegBin(4.6, 0.1), "rec_yd": d}, "WR")
    ck("DK points mean = scoring of the means + 3 x P(100+)", abs(dk.mean() - (4.6 + 5.8 + 2.4 + 3.0 * d.sf_ge(100))) < 1e-9)
    lad = dk.ladder([5, 10, 15, 20, 25, 30])
    ck("DK points ladder monotone", all(lad[b] <= lad[a] + 1e-12 for a, b in zip((5, 10, 15, 20, 25), (10, 15, 20, 25, 30))))

    # ---- factor effects sum to the applied adjustment
    f = dict(f_a)
    mk = M.player_markets(coefs, f, "WR", base)
    ok = all(abs(sum(b["effects"].values()) - (b["adjusted"] - b["baseline"])) < 1e-9 for m, b in mk.items() if b.get("effects"))
    ck("factor effects sum exactly to adjusted - baseline in every market", ok)
    rows = M.factor_rows(f, mk, "WR", coefs)
    tot = {}
    for r in rows:
        for k, v in r["effects"].items():
            tot[k] = tot.get(k, 0.0) + v
    ck("the matrix rows' effects add up to the applied change (targets)",
       abs(tot.get("tgt", 0.0) - (mk["tgt"]["adjusted"] - mk["tgt"]["baseline"])) < 5e-3, (tot, mk["tgt"]))
    ck("an unshipped market moves nothing (receptions factor 1)", abs(mk["rec"]["adjusted"] - base["rec"]) < 1e-12)
    ck("a feature outside the shipped terms prints effect 0",
       all(r["effect"]["delta_mean"] == 0.0 for r in rows if r["factor"].startswith("Location")))
    c2 = copy.deepcopy(coefs)
    c2["models"]["car"] = {"terms": [["f", "run_ypc"]], "names": ["run_ypc"], "b": [5.0], "cov": [[1e-2]]}
    f2 = dict(f, run_ypc=0.05)
    ck("a position outside a market's fit leaves it at Sleeper's line (TE carries), inside it moves (RB)",
       M.player_markets(c2, f2, "TE", dict(base, rush_att=1.0)).get("car", {}).get("adj", {}).get("factor") == 1.0
       and abs(M.player_markets(c2, f2, "RB", base)["car"]["adj"]["factor"] - math.exp(0.25)) < 1e-12)
    ck("a backup QB (under 10 projected attempts) keeps Sleeper's passing lines",
       M.player_markets(_coefs_pass(), f, "QB", dict(base, pass_att=6.0))["patt"]["adj"]["factor"] == 1.0
       and M.player_markets(_coefs_pass(), f, "QB", base)["patt"]["adj"]["factor"] != 1.0)

    # ---- strong_fit is side-aware and reads only the kept matchup terms
    adj = {"contrib": [("int[WR]", 0.30), ("margin", 0.20), ("cov_mz", -0.09), ("pass_epaxTE", 0.0)]}
    ck("matchup shift ignores Sleeper's level (R) and game script (V)", abs(M.matchup_shift(adj) - (math.exp(-0.09) - 1)) < 1e-12)
    # review F7 (c): each of R / V's terms ALONE moves nothing -- proe is the shipped V term's third part
    ck("matchup shift: proe, margin, total_c and a position intercept each alone give 0 (never strong_fit)",
       all(M.matchup_shift({"contrib": [(nm, 0.3)]}) == 0.0 for nm in ("proe", "margin", "total_c", "int[QB]", "proexmargin"))
       and abs(M.matchup_shift({"contrib": [("pass_epa", 0.1)]}) - (math.exp(0.1) - 1)) < 1e-12)
    mkt = {"ticker": "T", "yes_bid": 40.0, "yes_ask": 43.0, "no_bid": 57.0, "no_ask": 60.0, "volume": 10.0, "oi": 5.0}
    _v, lab, side = M.line_labels(0.40, 0.38, 0.42, mkt, -0.08, 30.0)
    ck("an unfavourable 8% matchup puts strong_fit on the NO side, never YES", "strong_fit" in lab and side == "no", (lab, side))
    _v, lab, side = M.line_labels(0.40, 0.38, 0.42, mkt, 0.08, 30.0)
    ck("a favourable 8% matchup puts strong_fit on the YES side", "strong_fit" in lab and side == "yes", (lab, side))
    _v, lab, side = M.line_labels(0.40, 0.38, 0.42, None, 0.03, 30.0)
    ck("a 3% matchup is not a strong fit (and carries no side)", "strong_fit" not in lab and side is None, (lab, side))

    # ---- apply(): team totals preserved, proj_pts moved, never raises
    import nfl_matchup as MM
    real = MM.factors_for_pairs

    def fake(pairs, season, week, coefs, ctx=None, qb_of=None, lines=None):
        return {i: {"tgt": 1.0 + 0.1 * (i % 3), "rec": 1.0 + 0.07 * (i % 2), "rec_yd": 1.0 - 0.05 * (i % 2)}
                for i in range(len(pairs))}
    MM.factors_for_pairs = fake
    try:
        pairs = []
        for i, (team, pos) in enumerate((("KC", "WR"), ("KC", "WR"), ("KC", "TE"), ("BUF", "WR"), ("BUF", "RB"))):
            means = {"rec_tgt": 5.0 + i, "rec": 3.0 + i * 0.5, "rec_yd": 40.0 + 5 * i, "rec_td": 0.3, "rush_att": 2.0 * (pos == "RB"),
                     "rush_yd": 9.0 * (pos == "RB"), "rush_td": 0.1, "pass_att": 0.0, "pass_cmp": 0.0, "pass_yd": 0.0,
                     "pass_td": 0.0, "int": 0.0, "fum": 0.05}
            pairs.append((str(1000 + i), {"name": f"p{i}", "pos": pos, "team": team, "opp": "X", "means": means,
                                          "proj_pts": 12.0}))
        before = copy.deepcopy(pairs)
        st = M.apply(pairs, SEASON, 2, coefs=coefs)
        ok = True
        for team in ("KC", "BUF"):
            for k in M._MEANS_OF:
                a = sum(p["means"][k] for _i, p in before if p["team"] == team)
                b = sum(p["means"][k] for _i, p in pairs if p["team"] == team)
                ok &= abs(a - b) < 1e-9
        ck("apply(): every team total of every component unchanged", ok)
        moved = any(abs(p["means"]["rec_tgt"] - q["means"]["rec_tgt"]) > 1e-9 for (_a, p), (_b, q) in zip(pairs, before))
        ck("apply(): players moved within team", st["applied"] and st["players_moved"] > 0 and moved, st)
        dpp = [p["proj_pts"] - q["proj_pts"] for (_a, p), (_b, q) in zip(pairs, before)]
        # Sleeper's default PPR (the scoring proj_pts is), pinned here rather than read back from the module
        ppr = {"rec": 1.0, "rec_yd": 0.1, "rec_td": 6.0, "rush_yd": 0.1, "rush_td": 6.0, "pass_yd": 0.04, "pass_td": 4.0,
               "int": -1.0}
        dppr = [sum(ppr.get(k, 0.0) * (p["means"][k] - q["means"][k]) for k in M._MEANS_OF) for (_a, p), (_b, q) in zip(pairs, before)]
        ck("apply(): proj_pts moves by the PPR change", all(abs(a - round(b, 2)) < 0.011 for a, b in zip(dpp, dppr)), (dpp, dppr))
        for bad in (None, [], [(None, None)], [("1", {"means": None})], [("1", {"team": "KC", "pos": "WR", "means": {"rec_tgt": "x"}})],
                    [("1", {"team": "KC", "pos": "WR", "means": {"rec_tgt": float("nan"), "rec": 2.0}})]):
            try:
                out = M.apply(copy.deepcopy(bad), SEASON, 2, coefs=coefs)
                ck(f"apply() never raises on bad input {str(bad)[:40]}", isinstance(out, dict))
            except Exception as e:      # noqa: BLE001 -- the guard is the point
                ck(f"apply() never raises on bad input {str(bad)[:40]}", False, repr(e))
        nan_pair = [("1", {"team": "KC", "pos": "WR", "means": {"rec_tgt": float("nan"), "rec": 2.0}})]
        M.apply(nan_pair, SEASON, 2, coefs=coefs)
        ck("apply(): a non-finite component is never written back", math.isnan(nan_pair[0][1]["means"]["rec_tgt"]))
        zero = dict(coefs, dfs_hook={"ships": False})
        p2 = copy.deepcopy(before)
        st0 = M.apply(p2, SEASON, 2, coefs=zero)
        ck("apply(): weight 0 (hook not shipped) leaves every line untouched",
           (not st0["applied"]) and json.dumps(p2, sort_keys=True) == json.dumps(before, sort_keys=True))
        st1 = M.apply(copy.deepcopy(before), SEASON, 2, coefs={})
        ck("apply(): no committed model -> untouched with a reason", not st1["applied"] and st1.get("reason"))
    finally:
        MM.factors_for_pairs = real

    # ---- coefficients file contract
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "c.json")
        with open(p, "w") as fh:
            json.dump(dict(coefs, schema=M.SCHEMA + 99), fh)
        ck("load_coefs refuses another schema", M.load_coefs(p) is None)
        with open(p, "w") as fh:
            json.dump(coefs, fh)
        ck("load_coefs reads its own schema", (M.load_coefs(p) or {}).get("schema") == M.SCHEMA)

    # ---- the board payload
    rows_sl = []
    for pid, (first, last, pos, team, opp, st_) in {
            "4046": ("Pat", "Qb", "QB", "KC", "BUF", {"pass_att": 34, "pass_cmp": 22, "pass_yd": 250, "pass_td": 1.6, "pass_int": 0.7,
                                                       "rush_att": 3, "rush_yd": 15, "pts_ppr": 18}),
            "9002": ("Wide", "Out", "WR", "KC", "BUF", {"rec_tgt": 7, "rec": 4.6, "rec_yd": 58, "rec_td": 0.4, "pts_ppr": 13}),
            "9003": ("Tight", "End", "TE", "KC", "BUF", {"rec_tgt": 4, "rec": 3, "rec_yd": 30, "rec_td": 0.2, "pts_ppr": 7}),
            "9004": ("Run", "Back", "RB", "KC", "BUF", {"rush_att": 14, "rush_yd": 60, "rush_td": 0.4, "rec_tgt": 3, "rec": 2.4,
                                                         "rec_yd": 17, "pts_ppr": 13})}.items():
        rows_sl.append({"player_id": pid, "team": team, "opponent": opp, "stats": st_, "game_id": "203009150",
                        "player": {"first_name": first, "last_name": last, "position": pos, "injury_status": None}})
    old_ids = M._IDS["data"]
    M._IDS.update(data={"ids": {}, "sleeper2gsis": {"4046": 100001, "9002": 100002, "9003": 100003, "9004": 100004},
                        "positions": FX.POS}, ts=1e18)
    try:
        idx = {"30SEP15KCBUF": {"pair": frozenset(("KC", "BUF")), "players": {("rec_yd", "wide out", 49.5): 40},
                                "q": {("prop", "rec_yd", "wide out", 49.5, False): {"ask": 47.0, "bid": 44.0, "mid": 45.5, "vol": 100.0, "oi": 50.0},
                                      ("prop", "rec_yd", "wide out", 49.5, True): {"ask": 56.0, "bid": 53.0, "mid": 54.5, "vol": 100.0, "oi": 50.0}},
                                "tick": {("prop", "rec_yd", "wide out", 49.5): ("KXNFLRECYDS-30SEP15KCBUF-X-50", None)}}}
        sched = [{"home": "BUF", "away": "KC", "date": "2030-09-15T17:00Z", "state": "pre"}]
        b = M.board(SEASON, 2, rows=rows_sl, kalshi_idx=idx, schedule=sched,
                    ctx=ctx, coefs=coefs, lines={"KC": {"margin": 3.0, "total": 47.0}}, with_market=True, now=1.9e9)
        # review N3: a game with no believable ladder reads margin 0 / total 44 -- said on the slate and the row
        cp = _coefs_pass()
        b_fb = M.board(SEASON, 2, rows=rows_sl, kalshi_idx={}, schedule=sched, ctx=ctx, coefs=cp, lines={},
                       with_market=False, now=1.9e9)
        b_ok = M.board(SEASON, 2, rows=rows_sl, kalshi_idx={}, schedule=sched, ctx=ctx, coefs=cp,
                       lines={"KC": {"margin": 3.0, "total": 47.0}, "BUF": {"margin": -3.0, "total": 47.0}},
                       with_market=False, now=1.9e9)
    finally:
        M._IDS["data"] = old_ids
    s = json.dumps(b)
    ck("board builds without error", not b.get("error") and len(b["players"]) == 4, b.get("error"))
    ck("board payload has no NaN / inf", "NaN" not in s and "Infinity" not in s)
    ck("board game ids are digits (the matchups route's contract)",
       all(str(g["game_id"]).isdigit() for g in b["games"]) and all(str(p["game_id"]).isdigit() for p in b["players"]))
    facts = [v for pl in b["players"] for blk in ("role", "opponent_profile") for v in (pl.get(blk) or {}).values()]
    ck("every role / opponent fact carries a unit", facts and all(isinstance(v, dict) and v.get("unit") for v in facts),
       [v for v in facts if not (isinstance(v, dict) and v.get("unit"))][:2])
    mono = all(all(b2["p"] <= a2["p"] + 1e-12 for a2, b2 in zip(mk_["lines"], mk_["lines"][1:]))
               for pl in b["players"] for mk_ in pl["markets"])
    ck("board ladders monotone in k", mono)
    wr_ = next(pl for pl in b["players"] if pl["pos"] == "WR")
    ry = next(mk_ for mk_ in wr_["markets"] if mk_["stat"] == "rec_yd")
    l50 = next(l for l in ry["lines"] if l["k"] == 50)
    ck("a Kalshi-listed line carries its market and a prop_value block", l50["market"] and l50["value"] and
       abs(l50["value"]["p"] - l50["p"]) < 1e-3, l50)
    eff = {}
    for fr in wr_["factors"]:
        for k, v in fr["effects"].items():
            eff[k] = eff.get(k, 0.0) + v
    tg = next(mk_ for mk_ in wr_["markets"] if mk_["stat"] == "tgt")
    ck("board: the WR's factor effects equal the applied targets change",
       abs(eff.get("tgt", 0.0) - (tg["adjusted_mean"] - tg["baseline_mean"])) < 0.01, (eff, tg["adjusted_mean"], tg["baseline_mean"]))
    ck("board: every strong_fit line carries its side",
       all(ln.get("fit_side") in ("yes", "no") for pl in b["players"] for mk_ in pl["markets"] for ln in mk_["lines"]
           if "strong_fit" in (ln.get("labels") or [])))
    ck("board: every player's game_id is one of the games", {p["game_id"] for p in b["players"]} <= {g["game_id"] for g in b["games"]})
    ck("board: top-level notes say what ships", isinstance(b.get("notes"), list) and b["notes"])
    nb = M.board(SEASON, 2, rows=rows_sl, kalshi_idx={}, schedule=[], ctx=ctx, coefs={}, lines={}, with_market=False)
    ck("board without a model: no players, and the reason is in the slate notes",
       not nb["players"] and nb.get("error") and any("could not be built" in n for n in nb.get("notes") or []), nb.get("notes"))
    ck("board: comparables list carries similarity, weight and why",
       all({"similarity", "weight", "why", "stats"} <= set(c) for pl in b["players"] for c in pl["comparables"]))

    # ---- review F2: no band where nothing fitted carries uncertainty, a real one where a term ships
    shipped_terms = {m for m, mod in coefs["models"].items() if mod.get("terms")}
    stat_of = {v: k for k, v in M.STAT_NAME.items()}
    unbanded = [(pl["pos"], mk_["stat"], ln["k"]) for pl in b["players"] for mk_ in pl["markets"] for ln in mk_["lines"]
                if stat_of.get(mk_["stat"]) not in shipped_terms and (ln["p_lo"] is not None or ln["p_hi"] is not None)]
    ck("board: every line of a market with no shipped term (and anytime TD, DK points) has p_lo = p_hi = None",
       not unbanded, unbanded[:4])
    tgt_wr = [ln for ln in tg["lines"]]
    ck("board: a market WITH shipped terms keeps a band that holds its point and has width somewhere (WR targets)",
       tgt_wr and all(ln["p_lo"] is not None and ln["p_lo"] - 1e-9 <= ln["p"] <= ln["p_hi"] + 1e-9 for ln in tgt_wr)
       and max(ln["p_hi"] - ln["p_lo"] for ln in tgt_wr) > 1e-3, [(ln["k"], ln["p_lo"], ln["p"], ln["p_hi"]) for ln in tgt_wr])
    ck("board: no line without a band is labelled positive_ev_robust",
       not any("positive_ev_robust" in (ln.get("labels") or []) for pl in b["players"] for mk_ in pl["markets"]
               for ln in mk_["lines"] if ln["p_lo"] is None))
    # review F7 (f): the matchup_weight label is "shipped" exactly where the model carries terms
    wl = {(pl["pos"], mk_["stat"]): mk_["matchup_weight"] for pl in b["players"] for mk_ in pl["markets"]}
    want = {k: ("shipped" if stat_of.get(k[1]) in shipped_terms else "0") for k in wl}
    ck("board: matchup_weight reads 'shipped' only on the markets carrying terms (targets, receiving yards here)",
       wl == want and wl.get(("WR", "rec")) == "0" and wl.get(("WR", "tgt")) == "shipped",
       {k: v for k, v in wl.items() if want[k] != v})
    # review N3: the fallback is marked
    fb_notes = [n for n in b_fb.get("notes") or [] if "no believable Kalshi" in n]
    ck("board: a game with no ladder says so on the slate when a shipped term reads it (KC @ BUF, margin 0 / total 44)",
       len(fb_notes) == 1 and "KC @ BUF" in fb_notes[0] and "pass_att" in fb_notes[0], b_fb.get("notes"))
    qb_rows = next(pl for pl in b_fb["players"] if pl["pos"] == "QB")["factors"]
    gs = [r for r in qb_rows if r["factor"].startswith("Game script")]
    ck("board: the QB's game-script factor row marks the FALLBACK and its evidence kind",
       gs and "FALLBACK" in gs[0]["opponent"] and gs[0]["evidence"]["kind"] == "fallback", gs[:1])
    ck("board: a game WITH both teams' lines carries no fallback note or mark",
       not [n for n in b_ok.get("notes") or [] if "no believable Kalshi" in n]
       and not any("FALLBACK" in r["opponent"] for pl in b_ok["players"] for r in pl["factors"]))

    # ---- review F3: the slate says low rushing-yard thresholds run high, while the spread is the single-CV form
    sn = M.slate_notes(committed)
    ck("slate notes (committed model): the low-threshold rushing-yard warning with its measured figures",
       any("LOW thresholds" in n and "0.86" in n and "0.67" in n for n in sn), sn)
    c_line = copy.deepcopy(committed)
    for prm in c_line["dispersion"]["rush_yd"].values():
        if isinstance(prm, dict):
            prm.update(cv_c=3.7, cv_d=-0.43)
    ck("slate notes: the warning goes once a line-dependent rushing spread is the committed form",
       not any("LOW thresholds" in n for n in M.slate_notes(c_line)))

    for part in (lambda: _rules(ck, committed), lambda: _source_nits(ck), lambda: _numpy_guards(ck)):
        try:
            part()
        except Exception as e:      # noqa: BLE001 -- a guard that cannot run is a failed guard, never a dead suite
            ck("nfl matchup model guards ran to the end", False, repr(e))


def _rules(ck, committed):
    """Both pre-registered rules: their blocks still hash to the records, and the
    committed decisions are what the stated rules give on the committed numbers
    (review F7: a guard that pins the rule's APPLICATION, not just a number)."""
    pre = _load("prereg.json")
    blk = _block("<!-- PREREG-START -->", "<!-- PREREG-END -->")
    ck("main rule: the PREREG block hashes to prereg.json, and coefs.json carries that hash",
       blk is not None and hashlib.sha256(blk.encode("utf-8")).hexdigest() == pre["sha256"] == committed["prereg_sha256"])
    val, tst = _load("validate.json"), _load("test.json")
    # R3, stated here rather than imported: accept iff d < -2 SE and d < -0.0005 x the market's thresholds
    bad, dec = [], {}
    for m, lad in val["ladder"].items():
        acc = []
        for r in lad["rungs"]:
            if r["rung"] == "1" or r.get("skipped"):
                continue
            rule = r["d_ll"] < -2.0 * r["se"] and r["d_ll"] < -0.0005 * len(M.THRESHOLDS[m])
            if bool(r.get("accepted")) != rule:
                bad.append((m, r["rung"]))
            if rule:
                acc.append(r["rung"])
        dec[m] = acc
    ck("replay R3 over validate.json's rungs: every accepted flag is the rule's, and the decisions follow",
       not bad and dec == val["decisions"] == committed["validation_accepted"], (bad, dec))
    sens = val.get("k_pooled_sensitivity") or {}
    bad_s = [(m, r["rung"]) for m, rs in (sens.get("rungs") or {}).items() for r in rs
             if r.get("d_ll") is not None and r.get("se") is not None
             and bool(r.get("accepted")) != (r["d_ll"] < -2.0 * r["se"] and r["d_ll"] < -0.0005 * len(M.THRESHOLDS[m]))]
    ck("replay R3 over the k-sensitivity rungs: same rule, and the report's 'same two decisions' holds",
       sens.get("rungs") and not bad_s and sens.get("decisions") == val["decisions"], bad_s)
    # R4: the frozen walk keeps a group iff d < 0, over exactly the validation's groups, in order
    bad, ship = [], {}
    for m, mk in tst["markets"].items():
        walk = mk.get("shipping_walk_frozen") or []
        if [w["group"] for w in walk] != dec.get(m, []):
            bad.append((m, "groups"))
        if any(bool(w["ships"]) != (w["d_ll"] < 0.0) for w in walk):
            bad.append((m, "ships"))
        ship[m] = [w["group"] for w in walk if w["d_ll"] < 0.0]
    ck("replay R4's frozen walk over test.json: coefs.json ships exactly what the rule keeps (passing TDs: V)",
       not bad and ship == tst["shipped"] == committed["shipped"] and ship.get("ptd") == ["V"], (bad, ship))
    ck("coefs.json carries terms exactly on the shipped markets",
       {m for m, mod in committed["models"].items() if mod.get("terms")} == {m for m, g in ship.items() if g})
    vr, fz = val["dfs_rule_2025"], tst["dk_fp"]["fz"]
    ck("replay the DFS hook rule: ships iff 2025 said so AND the frozen difference is <= 0",
       committed["dfs_hook"]["ships"] == bool(vr["hook_ships"] and fz["SUxM_team_vs_SU"]["d_ll"] <= 0.0))
    # the rushing-spread rule (report section 9): its own block, hash, stored bytes and gate
    rc = os.path.join(MODEL_DIR, "rush_cv")
    try:
        with open(os.path.join(rc, "prereg.json")) as fh:
            rr = json.load(fh)
        rblk = _block("<!-- RUSHCV-PREREG-START -->", "<!-- RUSHCV-PREREG-END -->")
        with open(os.path.join(rc, "code", "nfl_matchup_rushcv.py.txt"), "rb") as fh:
            stored = fh.read()
        with open(os.path.join(rc, "code", "rule_block.md.txt"), encoding="utf-8") as fh:
            stored_blk = fh.read()
        with open(os.path.join(REPO, "research", "nfl_matchup_rushcv.py"), "rb") as fh:
            live = fh.read()
        with open(os.path.join(rc, "rows_2023_2025.csv.gz"), "rb") as fh:
            ext = fh.read()
        ck("rushing-spread rule: its block hashes to its record, the stored block and code bytes are the registered ones",
           rblk is not None and hashlib.sha256(rblk.encode("utf-8")).hexdigest() == rr["sha256"] and stored_blk == rblk
           and hashlib.sha256(stored).hexdigest() == rr["code"]["research/nfl_matchup_rushcv.py"] == hashlib.sha256(live).hexdigest()
           and hashlib.sha256(ext).hexdigest() == rr["extract_sha256"])
        with open(os.path.join(rc, "choose.json")) as fh:
            ch = json.load(fh)
        g = ch["gate_2025"]
        ck("rushing-spread rule: the 2025 gate's verdict is the stated rule on its own numbers (d < -2 SE and < -0.004)",
           ch["prereg"] == rr["sha256"] and g["passes"] == bool(g["d_ll"] < -2.0 * g["se"] and g["d_ll"] < -0.0005 * 8))
    except (OSError, ValueError, KeyError) as e:
        ck("rushing-spread rule: registered, with its stored code and gate (research/data/nfl_matchup_model/rush_cv)", False, repr(e))


def _source_nits(ck):
    """Review N2 / N5: two dead lines, pinned where they lived."""
    fn = _fn_node(os.path.join(REPO, "research", "nfl_matchup_fit.py"), "cmd_validate")
    n_cpos = sum(1 for n in ast.walk(fn) if isinstance(n, ast.Assign)
                 for t in n.targets if isinstance(t, ast.Name) and t.id == "cpos") if fn else -1
    ck("cmd_validate fits the DK scale once (a 2023-only fit was computed and thrown away; N2)", n_cpos == 1, n_cpos)
    fn = _fn_node(os.path.join(REPO, "research", "nfl_matchup_kalshi.py"), "kick_map")
    ident = [ast.dump(n)[:60] for n in ast.walk(fn) if isinstance(n, ast.Dict) and n.keys
             and all(isinstance(k, ast.Constant) and isinstance(v, ast.Constant) and k.value == v.value
                     for k, v in zip(n.keys, n.values))] if fn else ["no kick_map"]
    ck("kick_map holds no identity code map (N5)", not ident, ident)
    try:
        sys.path.insert(0, os.path.join(REPO, "research"))
        import nfl_matchup_kalshi as KH
        km = KH.kick_map({0: {"games": [{"kick": "10/18/26, 13:00:00", "away": "LA", "home": "WAS", "date": "2026-10-18"}]}})
        ck("kick_map: nflverse LA reads as Kalshi's LAR too, WAS as WAS, at the gamebook's Eastern time",
           set(km) == {("2026-10-18", "LAWAS"), ("2026-10-18", "LARWAS")} and len(set(km.values())) == 1
           and list(km.values())[0] == 1792342800, km)
    except ImportError as e:
        ck("research/nfl_matchup_kalshi importable", False, str(e))


def _numpy_guards(ck):
    """The research fit's own rule code (numpy): R3's accept() and the family choice."""
    import importlib.util
    if importlib.util.find_spec("numpy") is None:     # CI and the server install none
        ck("(numpy is not installed here -- the research fit's accept() and family guards run where it is)", True)
        return
    import gzip
    import csv
    import random
    import research.nfl_matchup_fit as R
    val = _load("validate.json")
    ok = all(R.accept(r["d_ll"], r["se"], m) == bool(r.get("accepted")) for m, lad in val["ladder"].items()
             for r in lad["rungs"] if r["rung"] != "1" and not r.get("skipped"))
    edge = (not R.accept(-0.02, 0.01, "ptd") and R.accept(-0.0201, 0.01, "ptd")
            and not R.accept(-0.0014, 0.0001, "ptd") and R.accept(-0.0016, 0.0001, "ptd"))
    ck("research accept() is R3 as written: on every committed rung, at exactly 2 SE, and at the 0.0005 x thresholds floor",
       ok and edge and R.MIN_GAIN == 0.0005)
    cf = getattr(R, "choose_family", None)
    best, _means, n = cf({"gamma": [1.0, float("nan"), 2.0], "lognormal": [0.9, 0.5, 2.0]}) if cf else (None, None, None)
    ck("choose_family compares on rows every family scores (a NaN row cannot decide it)", best == "lognormal" and n == 2)
    # a frame whose yards outcomes are lognormal, plus 15 rows with a yards line and no carry line (they
    # score NaN): the first version's np.mean was NaN for both and kept gamma by loop order, under a key
    # strip() dropped from every JSON
    fields = ("rec_tgt", "rec", "rec_yd", "rush_att", "rush_yd", "pass_att", "pass_cmp", "pass_yd", "pass_td", "pass_int")
    cols = ["season", "week", "game_id", "pos"] + [f"sl_{f}" for f in fields] + [f"y_{f}" for f in fields]
    rnd = random.Random(3)

    def pois(lam):
        lim, k, pr = math.exp(-lam), 0, 1.0
        while True:
            pr *= rnd.random()
            if pr <= lim:
                return k
            k += 1
    rows = []
    s2 = math.log1p(1.2 ** 2)
    for pos, lo, hi in (("RB", 4.0, 18.0), ("QB", 2.0, 8.0), ("WR", 0.6, 3.0)):
        for i in range(700):
            c = rnd.uniform(lo, hi)
            car = pois(c)
            y = 0.0 if car == 0 else float(round(math.exp(rnd.gauss(math.log(4.5 * c) - 0.5 * s2, math.sqrt(s2)))))
            r = dict.fromkeys(cols, "")
            r.update(season=2023, week=1 + i % 17, game_id=f"g{i % 150}", pos=pos, sl_rush_att=round(c, 2),
                     sl_rush_yd=round(4.5 * c, 2), y_rush_att=car, y_rush_yd=y)
            rows.append(r)
    for i in range(15):
        r = dict.fromkeys(cols, "")
        r.update(season=2023, week=1, game_id=f"g{i}", pos="WR", sl_rush_yd=5.0, y_rush_yd=0.0)
        rows.append(r)
    with tempfile.TemporaryDirectory() as t:
        path = os.path.join(t, "frame.csv.gz")
        with gzip.open(path, "wt", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
        F = R.Frame(path, {2023})
        disp = R.fit_dispersion(F, {m: R.fit_idx(F, m, (2023,)) for m in R.SPEC})
        kept = json.loads(json.dumps(R.strip(disp)))
        ck("fit_dispersion: lognormal-shaped yards with NaN rows -> lognormal, stored under a key the JSON keeps",
           kept.get("rush_yd", {}).get("family") == "lognormal", kept.get("rush_yd"))
        try:
            carried = R.fit_dispersion(F, {m: R.fit_idx(F, m, (2023,)) for m in R.SPEC},
                                       family={"rec_yd": "gamma", "rush_yd": "gamma"})
        except TypeError as e:
            carried = {"rush_yd": {"error": repr(e)}}
        ck("fit_dispersion carries a family it is handed (the amended R2: the test and export carry the validation's)",
           carried["rush_yd"].get("family") == "gamma", carried["rush_yd"])


def main():
    fails = []

    def ck(name, cond, detail=""):
        if not cond:
            fails.append((name, detail))
            print(f"FAIL {name} {str(detail)[:300]}")
    run(ck)
    print(f"guards_nfl_matchup_model: {len(fails)} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
