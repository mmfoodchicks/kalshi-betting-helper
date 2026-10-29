"""Guards for the RB / QB rushing model (nfl_rush.py; design and draft rule in
research/reports/nfl_rush_model.md). Hermetic: synthetic kernels and a
synthetic coefficient file, no network, no deep store, about a second.

    python3 tests/guards_nfl_rush_model.py      # exits 1 on any failure
    from tests.guards_nfl_rush_model import run  # run(ck) inside the suite

Pinned by definition:
- The FFT is the DFT; the compound's pmf is the brute-force convolution of its
  parts; its analytic mean and variance are the grid's; the mass is 1.
- A kernel rescale keeps the mean exact and leaves a kneel kernel alone.
- At weight 0 every RB and QB mean IS Sleeper's line (carries, yards,
  receptions, attempts), opportunity terms move counts only and efficiency
  terms move yards per attempt only.
- Official scoring: kneels are QB rushing attempts at negative yards, and
  there is no sack component.
- No zero-width band: lo / hi are None where nothing fitted carries
  uncertainty, and a real band where something does.
- The breakdown's contract (label, value, unit, kind, basis, lo, hi), honest
  about what moves a probability.
- The longest-attempt distribution is the brute-force maximum.
- The production module never imports numpy.
"""
import cmath
import copy
import json
import math
import os
import subprocess
import sys
import tempfile
import time

os.environ["VIGIL_NO_BG"] = "1"
REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, REPO)

import nfl_rush as R                       # noqa: E402
import prop_dist as PD                     # noqa: E402


def _section(ck, name, fn, *args):
    """Runs one section of checks; a section that raises is a FAILED check (and the sections after it
    still run), never a pass."""
    try:
        fn(*args)
    except (Exception, SystemExit) as e:      # noqa: BLE001 -- a section that cannot run (a command's refusal
        ck(f"{name} ran to the end", False, repr(e))    # escaping included) is a failure, reported by name


def _norm(p):
    s = sum(p)
    return [x / s for x in p]


# synthetic kernels with the measured shapes' character: RB runs (losses, a long right tail), catches,
# designed QB runs (a third at or below 0), sneaks (1-2 yards), scrambles (never negative), kneels (-1)
KERN = {
    "rb_run": {"lo": -4, "p": _norm([1, 2, 4, 7, 9, 11, 12, 11, 9, 7, 6, 5, 4, 3, 2.5, 2, 1.5, 1.2, 1, .8, .6, .5, .4, .3,
                                     .2, .2, .1, .1, .1, .1, .05, .05, .05, .05])},
    "rb_catch": {"lo": -2, "p": _norm([1, 2, 4, 6, 8, 9, 9, 8, 7, 6, 5, 4, 3, 2.5, 2, 1.5, 1, .8, .6, .4, .3, .2, .1])},
    "qb_designed": {"lo": -3, "p": _norm([3, 5, 8, 12, 8, 7, 6, 5, 5, 4, 4, 3, 3, 2, 2, 1.5, 1, 1, .5, .5, .3, .2])},
    "qb_sneak": {"lo": 0, "p": _norm([18, 40, 30, 9, 3])},
    "qb_scramble": {"lo": 0, "p": _norm([.1, 3, 6, 8, 9, 9, 8, 7, 6, 6, 5, 4, 4, 3, 3, 2, 2, 1.5, 1, 1, .8, .5, .4, .3])},
    "kneel": {"lo": -3, "p": _norm([2, 10, 85, 3])},
}


def _coefs():
    return {"schema": R.SCHEMA, "kernels": copy.deepcopy(KERN),
            "alpha": {"rb_carries": {"a": 0.9, "b": -0.6, "cap": 1.0}, "rb_receptions": {"a": 0.2, "b": 0.0},
                      "qb_designed": {"a": 0.6, "b": 0.0}, "qb_sneak": {"a": 1.0, "b": 0.0},
                      "qb_scramble": {"a": 0.25, "b": 0.0}, "kneel": {"a": 0.6, "b": 0.0}},
            "kneels": {"by_margin": [[-10, 0.48], [-5, 0.50], [0, 0.90], [5, 0.97], [10, 1.06]], "mean": 0.78},
            "qb_prior": {"designed_share": 0.30, "sneak_share": 0.15, "scramble_share": 0.55},
            "correlations": [{"a": "qb_rush_att", "b": "rb1_rush_att", "rho": 0.0818, "n": 1087,
                              "basis": "actual minus Sleeper's revised line, 2023-24 team-games"}],
            "shipped": {}}


def _brute(components):
    """Exact pmf {value: p} of the sum of independent compounds, by direct convolution."""
    def conv(a, b):
        out = {}
        for x, p in a.items():
            for y, q in b.items():
                out[x + y] = out.get(x + y, 0.0) + p * q
        return out
    tot = {0: 1.0}
    for c in components:
        mu, a = c["count_mean"], c.get("alpha") or 0.0
        cnt = PD.NegBin(mu, a) if a > 0 else PD.Poisson(mu)
        k = {c["kernel"]["lo"] + i: q for i, q in enumerate(c["kernel"]["p"]) if q}
        acc, d = {}, {0: 1.0}
        for n in range(int(mu * 6 + 25)):
            if n:
                d = conv(d, k)
            pn = cnt.pmf(n)
            for v, q in d.items():
                acc[v] = acc.get(v, 0.0) + pn * q
        tot = conv(tot, acc)
    return tot


def run(ck):
    """The guards. A registered data file this checkout does not hold -- the two dataset CSVs are kept out of the
    public repository (their PFR- and NGS-derived columns; .gitignore), so CI never has them -- gets a stand-in for
    the run: every check that hashes REG_DATA works in a temporary records folder, and none reads the files'
    rows. A stand-in made here is removed here (Sonnet's R4-F1)."""
    made = []
    try:
        import research.nfl_rush_model_fit as F
        for f in F.REG_DATA:
            p_ = os.path.join(REPO, f)
            if not os.path.exists(p_):
                os.makedirs(os.path.dirname(p_), exist_ok=True)
                with open(p_, "wb") as fh:
                    fh.write(b"stand-in: this registered data file is not in this checkout\n")
                made.append(p_)
    except ImportError:          # numpy absent: the research guards inside skip themselves
        pass
    try:
        return _run_body(ck)
    finally:
        for p_ in made:
            os.remove(p_)


def _run_body(ck):
    # ---- the transform and the compound
    xs = [complex(math.sin(i * 1.7), math.cos(i * 0.3)) for i in range(16)]
    dft = [sum(x * cmath.exp(-2j * math.pi * k * j / 16) for j, x in enumerate(xs)) for k in range(16)]
    f = R.fft(xs)
    ck("FFT equals the DFT (16 points) and inverts", max(abs(a - b) for a, b in zip(f, dft)) < 1e-12
       and max(abs(a - b) for a, b in zip(R.fft(f, inverse=True), xs)) < 1e-12)
    comps = [{"role": "rb_run", "count_mean": 3.4, "alpha": 0.3, "kernel": R.rescale(KERN["rb_run"], 5.0)},
             {"role": "kneel", "count_mean": 0.9, "alpha": 0.0, "kernel": KERN["kneel"]}]
    C = R.Compound(comps)
    br = _brute(comps)
    diff = max(abs(C.pmf(v) - p) for v, p in br.items())
    ck("compound pmf = brute-force convolution of its parts (NegBin x kernel + Poisson x kneels)", diff < 1e-9, diff)
    ck("compound mass is 1 and nothing wrapped around the grid", abs(sum(C.p) - 1) < 1e-12 and abs(C.mass_raw - 1) < 1e-9,
       C.mass_raw)
    ck("compound: the analytic mean and variance are the grid's",
       abs(C.mean() - C.grid_mean()) < 1e-9 and abs(C.var() - sum((C.lo + i - C.grid_mean()) ** 2 * q
                                                                   for i, q in enumerate(C.p))) < 1e-6)
    ks = list(range(-5, 60, 5))
    lad = C.ladder(ks)
    ck("compound ladder monotone, P(S >= k) = 1 below the grid and 0 above it",
       all(lad[b] <= lad[a] + 1e-15 for a, b in zip(ks, ks[1:])) and C.sf_ge(R.GRID_LO - 5) == 1.0
       and C.sf_ge(R.GRID_LO + R.GRID_N + 5) == 0.0)
    ck("compound: the kneels put real mass below zero (official scoring: a kneel is a rush at about -1)",
       1.0 - C.sf_ge(0) > 0.01, 1.0 - C.sf_ge(0))
    # an independent part beside the compounds (receiving yards from any prop_dist family)
    rec = PD.Yards(0.2, 9.0, 7.0, "gamma")
    ex = {"role": "rec_yd", "pmf": R.pmf_of(rec, 0, 300), "mean": rec.mean(), "var": rec.var()}
    CX = R.Compound(comps[:1], extra=[ex])
    gm_ex = sum(v * q for v, q in zip(range(0, 301), ex["pmf"]["p"]))
    ck("compound + an independent part: the mean adds the part's, and the grid agrees",
       abs(CX.mean() - (comps[0]["count_mean"] * 5.0 + rec.mean())) < 1e-9
       and abs(CX.grid_mean() - (comps[0]["count_mean"] * 5.0 + gm_ex)) < 1e-6, (CX.mean(), CX.grid_mean(), gm_ex))

    # ---- kernels and dispersion
    k2 = R.rescale(KERN["rb_run"], 6.3)
    ck("rescale keeps the mean EXACT (mass split between the two integers around s x)",
       abs(R.kernel_mean(k2) - 6.3) < 1e-12 and abs(sum(k2["p"]) - 1) < 1e-12, R.kernel_mean(k2))
    ck("rescale leaves a kernel with a non-positive mean (kneels) unchanged", R.rescale(KERN["kneel"], 4.0) == KERN["kneel"])
    cur = {"a": 0.9, "b": -0.6, "cap": 1.0}
    ck("alpha(mean) falls with the line and is clamped (backups more dispersed than starters)",
       R.alpha_of(cur, 5.0) > R.alpha_of(cur, 15.0) > 0 and R.alpha_of(cur, 0.01) <= 1.0 and R.alpha_of(None, 9) == 0.0)

    # ---- running backs: weight 0 is Sleeper; opportunity vs efficiency
    co = _coefs()
    base = {"rush_att": 14.0, "rush_yd": 61.0, "rec": 2.6, "rec_yd": 19.0, "rec_tgt": 3.4, "rush_td": 0.42, "rec_td": 0.11}
    d0, p0 = R.rb_dists(base, co)
    ck("RB at weight 0: carries, rushing yards, receptions, receiving yards and TDs are Sleeper's means",
       abs(d0["rush_att"].mean() - 14.0) < 1e-12 and abs(d0["rush_yd"].mean() - 61.0) < 1e-9
       and abs(d0["rec"].mean() - 2.6) < 1e-12 and abs(d0["rec_yd"].mean() - 19.0) < 1e-9
       and abs(d0["rush_rec_yd"].mean() - 80.0) < 1e-9 and abs(d0["td"].mean() - 0.53) < 1e-12)
    d1, p1 = R.rb_dists(base, co, {"carries": 1.2})
    d2, p2 = R.rb_dists(base, co, {"ypc": 1.1})
    ck("RB: an opportunity term moves carries (and yards) but not yards per carry",
       abs(p1["rush"]["count_mean"] - 16.8) < 1e-12 and abs(p1["rush"]["ypc"] - 61.0 / 14.0) < 1e-12
       and abs(d1["rush_yd"].mean() - 61.0 * 1.2) < 1e-9)
    ck("RB: an efficiency term moves yards per carry (and yards) but not carries",
       abs(p2["rush"]["count_mean"] - 14.0) < 1e-12 and abs(d2["rush_yd"].mean() - 61.0 * 1.1) < 1e-9
       and abs(d2["rush_att"].mean() - 14.0) < 1e-12)
    d3, p3 = R.rb_dists(base, co, {"rec": 1.3, "ypr": 0.9})
    ck("RB receiving: a receptions term moves receptions, a per-catch term moves yards per catch",
       abs(d3["rec"].mean() - 2.6 * 1.3) < 1e-12 and abs(p3["rec"]["ypr"] - 19.0 / 2.6 * 0.9) < 1e-12
       and abs(d3["rec_yd"].mean() - 19.0 * 1.3 * 0.9) < 1e-9 and abs(d3["rush_yd"].mean() - 61.0) < 1e-9)
    ck("RB: carries' dispersion comes from the line-dependent curve (a 5-carry back more dispersed than a 14)",
       R.rb_parts(dict(base, rush_att=5.0, rush_yd=20.0), co)["rush"]["alpha"] > p0["rush"]["alpha"])
    ck("RB: no carries line -> no rushing markets", "rush_yd" not in R.rb_dists(dict(base, rush_att=0), co)[0])

    # ---- quarterbacks: four components, anchored to Sleeper
    qb = {"rush_att": 5.0, "rush_yd": 27.0, "pass_att": 33.0, "rush_td": 0.21}
    prof = {"designed_share": 0.35, "sneak_share": 0.10, "scramble_share": 0.55, "ypa_designed": 4.6, "ypa_scramble": 7.9}
    dq, pq = R.qb_dists(qb, prof, co, team_margin=4.0)
    roles = [c["role"] for c in pq["components"]]
    ck("QB rushing is four parts -- designed, sneak, scramble, kneel -- and never a sack",
       roles == ["qb_designed", "qb_sneak", "qb_scramble", "kneel"] and not any("sack" in r for r in roles))
    ck("QB anytime TD is rushing (+ receiving) TDs only -- Kalshi's KXNFLTD never counts passing TDs",
       abs(R.qb_dists(dict(qb, rec_td=0.02, pass_td=1.7), prof, co, 4.0)[0]["td"].mean() - 0.23) < 1e-12)
    ck("QB at weight 0: rushing attempts and rushing yards are Sleeper's means exactly (kneels included)",
       abs(dq["rush_att"].mean() - 5.0) < 1e-9 and abs(dq["rush_yd"].mean() - 27.0) < 1e-9 and pq["anchored"],
       (dq["rush_att"].mean(), dq["rush_yd"].mean()))
    kn = lambda m: R.expected_kneels(co, m)
    ck("kneels from the game script: a 7-point favourite kneels more than a 7-point underdog; linear between knots",
       kn(7.0) > kn(-7.0) and abs(kn(2.5) - 0.935) < 1e-12 and kn(30.0) == 1.06 and kn(None) == 0.90)
    ck("a kneel is one of his attempts: expected kneels capped at 0.8 x Sleeper's attempts",
       abs(R.qb_parts(dict(qb, rush_att=1.0), prof, co, team_margin=10.0)["kneels"] - 0.8) < 1e-12)
    dqs, pqs = R.qb_dists(qb, prof, co, team_margin=4.0, mult={"scramble": 1.3})
    sc = lambda p: next(c for c in p["components"] if c["role"] == "qb_scramble")
    ds = lambda p: next(c for c in p["components"] if c["role"] == "qb_designed")
    ck("QB: a scramble-rate term moves the scramble count only (designed runs and yards per scramble unchanged)",
       abs(sc(pqs)["count_mean"] - 1.3 * sc(pq)["count_mean"]) < 1e-12 and ds(pqs)["count_mean"] == ds(pq)["count_mean"]
       and abs(sc(pqs)["ypa"] - sc(pq)["ypa"]) < 1e-12)
    low = R.qb_parts(dict(qb, rush_yd=-2.0), prof, co, team_margin=8.0)
    ck("QB: a yards line below what sneaks and kneels carry is floored and SAYS so (anchored False)",
       low and not low["anchored"] and any(i["label"] == "Anchoring note" for i in R.qb_breakdown(low, qb)))
    ck("QB with no prior shares reads the position's (the shares are of his NON-kneel attempts)",
       abs(sum(R.qb_parts(qb, None, co, 0.0)["shares"].values()) - 1.0) < 1e-12)
    co_own = dict(co, qb_shares="own")
    ck("the shipped QB split is the POSITION's: his own shares never move the distribution unless a research arm says so",
       R.QB_SHARES == "position" and R.qb_parts(qb, prof, co, 4.0)["shares"] == R.qb_parts(qb, None, co, 4.0)["shares"]
       and abs(R.qb_parts(qb, prof, co_own, 4.0)["shares"]["scramble"] - 0.55) < 1e-12
       and R.qb_parts(qb, prof, co_own, 4.0)["shares"] != R.qb_parts(qb, None, co, 4.0)["shares"])
    bdo = R.qb_breakdown(pq, qb, own=prof)
    ownl = [i for i in bdo if i["label"].startswith("His own share")]
    ownc = {i["label"]: i for i in bdo if i["label"].startswith("His own split")}
    pos_d = next((i for i in bdo if i["label"] == "Expected designed runs (position split)"), None)
    nk = sum(c["count_mean"] for c in pq["components"] if c["role"] != "kneel")
    ck("the QB breakdown LABELS the split as the position's (the same for every QB) and shows HIS split -- shares "
       "and the counts it would give from the same attempts -- as information only",
       len(ownl) == 3 and all("information only" in i["basis"] and i["unit"] == "share" for i in ownl)
       and pos_d is not None and "the same for every QB" in pos_d["basis"] and "position's shares" in pos_d["basis"]
       and len(ownc) == 3 and all("information only" in i["basis"] for i in ownc.values())
       and abs(ownc["His own split of the same attempts: expected designed runs"]["value"]
               - round(prof["designed_share"] * nk, 3)) < 2e-3
       and not any(i["label"].startswith("His own") for i in R.qb_breakdown(pq, qb)))
    un = R.qb_dists(qb, {"designed_share": 2.0, "sneak_share": 1.0, "scramble_share": 1.0}, co_own, team_margin=4.0)
    ck("QB shares that do not sum to 1 are normalised: attempts and yards still Sleeper's, designed half the rest",
       abs(un[0]["rush_att"].mean() - 5.0) < 1e-9 and abs(un[0]["rush_yd"].mean() - 27.0) < 1e-9
       and abs(un[1]["shares"]["designed"] - 0.5) < 1e-12)
    ck("QB rushing yards carry real mass below 0 when kneels are expected (official scoring)",
       1.0 - R.qb_dists(dict(qb, rush_att=2.0, rush_yd=4.0), prof, co, team_margin=10.0)[0]["rush_yd"].sf_ge(0) > 0.05)

    # ---- the longest attempt
    comp1 = [{"role": "rb_run", "count_mean": 2.2, "alpha": 0.0, "kernel": KERN["rb_run"]}]
    L = R.Longest(comp1)
    cdf = lambda k: sum(q for i, q in enumerate(KERN["rb_run"]["p"]) if KERN["rb_run"]["lo"] + i < k)
    brute = lambda k: 1.0 - sum(PD.Poisson(2.2).pmf(n) * cdf(k) ** n for n in range(60))
    ck("longest attempt: P(longest >= k) = 1 - E[F(k-1)^N] (brute force), monotone, no attempt = a miss",
       all(abs(L.sf_ge(k) - brute(k)) < 1e-12 for k in (1, 5, 10, 20)) and L.sf_ge(10) <= L.sf_ge(5)
       and abs(L.sf_ge(1) - brute(1)) < 1e-12)

    # ---- bands, breakdown, payload hygiene
    pr0 = R.probs(lambda s: R.Compound([dict(comps[0], kernel=R.rescale(KERN["rb_run"], 5.0 * s))]), 0.0, [10, 20])
    ck("no shipped uncertainty -> p_lo = p_hi = None (never a zero-width band)",
       pr0 and all(v[1] is None and v[2] is None for v in pr0.values()))
    pr1 = R.probs(lambda s: R.Compound([dict(comps[0], kernel=R.rescale(KERN["rb_run"], 5.0 * s))]), 0.05, [10, 20])
    ck("a shipped coefficient's uncertainty gives a band that holds its point and has width",
       all(v[1] <= v[0] <= v[2] and v[2] - v[1] > 1e-4 for v in pr1.values()), pr1)
    units = {"per_game", "yds", "share", "none"}
    for nm, bd in (("RB", R.rb_breakdown(p0, base, {"ybc_per_att": 2.4, "yac_per_att": 1.9})), ("QB", R.qb_breakdown(pq, qb))):
        ck(f"{nm} breakdown: every item is {{label, value, unit, kind, basis, lo, hi}} with a known unit and an honest kind",
           bd and all(set(i) == {"label", "value", "unit", "kind", "basis", "lo", "hi"} and i["unit"] in units
                      and i["kind"] in ("modelled", "observed", "assumption") and i["basis"] for i in bd), bd[:2])
        ck(f"{nm} breakdown: no band where nothing shipped, and it says the split moves no probability",
           all(i["lo"] is None and i["hi"] is None for i in bd)
           and any("no rushing term" in i["basis"] or "does not move a probability" in i["basis"] for i in bd))
    qbd = {i["label"]: i for i in R.qb_breakdown(pq, qb)}
    ck("QB breakdown lists the four components, yards per attempt and the scramble rate per dropback",
       {"Expected designed runs (position split)", "Expected sneaks (position split)",
        "Expected scrambles (position split)", "Expected kneels", "Yards per scramble (position split)",
        "Scrambles per dropback"} <= set(qbd) and qbd["Scrambles per dropback"]["unit"] == "share")
    rbp = R.rb_breakdown(p1, base, None, status="provisional")
    ck("a provisional ship says PROVISIONAL in the breakdown", any("PROVISIONAL" in i["basis"] for i in rbp))
    s = json.dumps({"rb": R.rb_breakdown(p0, base), "qb": R.qb_breakdown(pq, qb), "d": dq["rush_yd"].to_dict(),
                    "c": R.correlations(co)})
    ck("payload pieces are JSON with no NaN / inf", "NaN" not in s and "Infinity" not in s)
    ck("correlations carry what they are (observed, basis, n)",
       R.correlations(co) and all(c["kind"] == "observed" and c["basis"] and c["n"] for c in R.correlations(co)))

    # ---- the committed model's contract
    with tempfile.TemporaryDirectory() as t:
        p = os.path.join(t, "c.json")
        with open(p, "w") as fh:
            json.dump(dict(co, schema=R.SCHEMA + 1), fh)
        bad = R.load_coefs(p)
        with open(p, "w") as fh:
            json.dump(co, fh)
        good = R.load_coefs(p)
        ck("load_coefs: another schema reads None (inert), its own reads", bad is None and good and good["schema"] == R.SCHEMA)
        ck("load_coefs: a missing file reads None (the board unchanged)", R.load_coefs(os.path.join(t, "none.json")) is None)
    shp = dict(co, shipped={"rush_yd": [{"group": "SHAPE", "status": "provisional"}, {"group": "OVD", "status": "shipped"}],
                            "rush_att": [{"group": "OVD", "status": "shipped"}, {"group": "SHAPE", "status": "reverted"}],
                            "rec": [{"group": "SPD", "status": None}], "rec_yd": [{"group": "FULL", "status": "shipped"}],
                            "td": [{"group": "SHAPE", "status": "Shipped"}, "junk"]})
    ck("ship status: provisional / shipped / 0, the compound only where SHAPE shipped, and the gate fails CLOSED "
       "(a reverted / None / mis-cased / malformed entry reads not shipped; FULL alone never switches the compound on)",
       R.ship_status(shp, "rush_yd") == "provisional" and R.ship_status(shp, "rush_att") == "shipped"
       and R.ship_status(shp, "rec") == "0" and R.ship_status(shp, "td") == "0" and R.ship_status(shp, "nope") == "0"
       and R.uses_compound(shp, "rush_yd") and not R.uses_compound(shp, "rush_att") and not R.uses_compound(shp, "rec_yd")
       and not R.uses_compound(shp, "td") and R.SHIP_STATUSES == ("shipped", "provisional"))

    # ---- the markets each position prices, and the longest rush beside the yards
    ck("thresholds by position: RB and QB rushing share the board's stat names (rush_att, rush_yd)",
       set(R.THRESHOLDS["RB"]) == set(R.RB_MARKETS) and set(R.THRESHOLDS["QB"]) == set(R.QB_MARKETS)
       and "rush_yd" in R.QB_MARKETS and "rush_att" in R.QB_MARKETS)
    lr = d0["long_rush"]
    ck("RB longest rush comes from the same carries and kernel as his yards (never more likely than the yards)",
       isinstance(lr, R.Longest) and all(lr.sf_ge(k) <= d0["rush_yd"].sf_ge(k) + 1e-12 for k in (10, 20, 30)))
    ck("QB longest rush counts every rushing part (designed, sneak, scramble, kneel)",
       [c["role"] for c in dq["long_rush"].components] == ["qb_designed", "qb_sneak", "qb_scramble", "kneel"])

    # ---- every Kalshi series that lists RBs / QBs maps to a market this module prices, with its strike rule
    ck("each RB / QB Kalshi series maps to a market priced for every position it lists",
       all(m in (R.THRESHOLDS[p]) for m, poss in R.SERIES.values() for p in poss) and R.SERIES["KXNFLRRYDS"][1] == ("RB",)
       and {"KXNFLRSHYDS", "KXNFLRSHATT", "KXNFLLONGRSH", "KXNFLTD", "KXNFLRRYDS"} <= set(R.SERIES))
    ck("no PROTECTED, ladder or escalator series is priced (their settlement is not P(stat >= k))",
       not any(t.endswith("PROT") or "LADDER" in t or "ESCALATOR" in t for t in R.SERIES))
    ck("an 'at least' rung's strike: 19.5 -> 20+, 20 -> 20+", (R.strike_k(19.5), R.strike_k(20)) == (20, 20))

    # ---- the hold: no research path reads 2025 or 2026 before the rule is hashed
    import importlib.util
    if importlib.util.find_spec("numpy") is None:
        ck("(numpy is not installed here -- the research hold guards run where it is)", True)
    else:
        import research.nfl_rush_model_fit as F
        import research.nfl_rush_model_explore as X
        import hashlib as _hl
        with tempfile.TemporaryDirectory() as tdr:
            old_out = F.MODEL_OUT
            F.MODEL_OUT = tdr
            try:
                def _held():
                    try:
                        F._guard([2024, 2025])
                        return False
                    except SystemExit:
                        return True
                unreg = _held()
                rec_ = {"sha256": _hl.sha256(F.rule_block().encode("utf-8")).hexdigest(),
                        "code": {f: F._sha(os.path.join(REPO, f)) for f in F.REG_CODE},
                        "data": {f: F._sha(os.path.join(REPO, f)) for f in F.REG_DATA}}
                json.dump(rec_, open(os.path.join(tdr, "prereg.json"), "w"))
                reg_ok = not _held() and F._guard([2023]) is None
                json.dump(dict(rec_, code=dict(rec_["code"], **{"nfl_rush.py": "0" * 64})),
                          open(os.path.join(tdr, "prereg.json"), "w"))
                tampered = _held() and "nfl_rush.py" in F.prereg_status()[1]
                json.dump(dict(rec_, sha256="0" * 64), open(os.path.join(tdr, "prereg.json"), "w"))
                rule_moved = _held()
                json.dump(dict(rec_, data={k: v for k, v in rec_["data"].items() if "player_weeks" not in k}),
                          open(os.path.join(tdr, "prereg.json"), "w"))
                set_moved = _held()
            finally:
                F.MODEL_OUT = old_out
        ck("held seasons (2025, 2026) are readable ONLY through a registration whose rule block and every registered "
           "file still hash: unregistered, a changed code file, a changed rule or a different registered set all refuse",
           unreg and reg_ok and tampered and rule_moved and set_moved and set(F.HELD) == {2025, 2026}
           and not hasattr(F, "FORBIDDEN"))
        ck("the design measurements read the fitting seasons only (2023-2024)", tuple(X.SEASONS) == (2023, 2024))
        import gzip as _gz
        import types
        with tempfile.TemporaryDirectory() as t:
            # an UNREGISTERED records folder: the repository's holds the real registration since 2026-10-29, and a
            # check of the hold or of the go must not read it (it held 2 guards red on the registered tree)
            unreg_out = os.path.join(t, "unregistered")
            os.makedirs(unreg_out)
            real_out = F.MODEL_OUT
            F.MODEL_OUT = unreg_out
            try:
                fp = os.path.join(t, "f.csv.gz")
                with _gz.open(fp, "wt") as fh:
                    fh.write("season,week,pos,game_id,f_x,y_rush_yd\n2024,3,RB,g1,0.1,55\n2025,3,RB,g2,0.2,61\n")
                try:
                    F.read_frame(fp, (2025,), outcomes=True)
                    refused = False
                except SystemExit:
                    refused = True
                plain = F.read_frame(fp, (2024, 2025), outcomes=False)
                ck("the frame reader refuses 2025 outcomes, and an audit read carries no outcome column",
                   refused and len(plain) == 2 and not any(k.startswith("y_") for r in plain for k in r))
                try:
                    F.cmd_register(types.SimpleNamespace(dry_run=False, go=None, frame=None, frame_val=None))
                    gated = False
                except SystemExit as e:
                    gated = "orchestrator's go" in str(e)        # refused BY the go check, not by a later one
                ck("registration refuses without the orchestrator's go, and writes nothing",
                   gated and not os.path.exists(os.path.join(F.MODEL_OUT, "prereg.json"))
                   and not os.path.exists(os.path.join(F.MODEL_OUT, "prereg_code")))
            finally:
                F.MODEL_OUT = real_out
            import research.nfl_rush_model_frame as _FRM
            cur = _FRM.code_shas()
            inp = _FRM.input_shas(["research/data/nfl_rush/schema.json"])
            ck("registration flags a frame built on code OR input data that has since changed, or with no build record",
               F.stale_frames({"val": {"code": cur, "participation": "prior", "inputs": inp}}) == {}
               and F.stale_frames({"val": {"code": dict(cur, **{"nfl_rush_profiles.py": "0" * 64}), "participation": "prior",
                                           "inputs": inp}}) == {"val": ["nfl_rush_profiles.py"]}
               and F.stale_frames({"val": {"code": cur, "participation": "prior",
                                           "inputs": {"research/data/nfl_rush/schema.json": "0" * 64}}})
               == {"val": ["research/data/nfl_rush/schema.json"]}
               and F.stale_frames({"test": {"code": None, "participation": None}}).get("test")
               and F.stale_frames({"test": {"code": cur, "participation": "prior", "inputs": {}}}).get("test")
               and set(_FRM.BUILD_CODE) == set(F.REG_CODE))
            bl = F.blocked_inputs({"val": {"paths_absent": [["dq", "hit_sack_rate"]]}, "test": {"paths_absent": None}})
            ck("registration is blocked while a fitted input is pending, absent from a frame, or unrecorded",
               bl.get("pending", []) == sorted(set(R.PENDING_PATHS) & _FRM.fitted_paths()) and bl.get("absent_val") == [("dq", "hit_sack_rate")]
               and bl.get("absent_test") and "absent_val" not in F.blocked_inputs({"val": {"paths_absent": []}}))
            with tempfile.TemporaryDirectory() as td2:
                fr = os.path.join(td2, "f.csv.gz")
                open(fr, "wb").write(b"x")
                json.dump({"rows": 1, "k": {}, "seasons": [2023], "participation": "prior", "code": _FRM.code_shas(),
                           "paths_absent": [["dq", "hit_sack_rate"]], "inputs": inp}, open(fr + ".meta.json", "w"))
                old_out = F.MODEL_OUT
                F.MODEL_OUT = td2
                try:
                    F.cmd_register(types.SimpleNamespace(dry_run=False, go=F.GO, frame=fr, frame_val=fr))
                    refused = False
                except SystemExit as e:
                    refused = "fitted inputs" in str(e)
                except Exception:      # noqa: BLE001 -- it went past the block: not refused
                    refused = False
                finally:
                    F.MODEL_OUT = old_out
                ck("registration WITH the go refuses frames missing a fitted input, and writes nothing",
                   refused and not os.path.exists(os.path.join(td2, "prereg.json"))
                   and not os.path.exists(os.path.join(td2, "prereg_code")))
        rep = open(os.path.join(REPO, "research", "reports", "nfl_rush_model.md"), encoding="utf-8").read()
        import research.nfl_rush_model_rule as RULE
        dead_today = {"RB": {"cfp_direction"}, "QB": {"cfp_dsg_edge", "spd_qb_td_oe"}}
        ck("the 2025 walk makes the decisions the rule states (35 group x market, SHAPE included, the dead groups "
           "left out), counted by the walk's own code",
           len(RULE.decisions(dead_today)) == 35 and "makes **35** group x market" in rep
           and ("QB", "td", "SPD") not in RULE.decisions(dead_today) and ("QB", "td", "SPD") in RULE.decisions({}))
        _section(ck, 'rule_checks', rule_checks, ck, F, RULE)
        _section(ck, 'leak_checks', leak_checks, ck, F, RULE)
        _section(ck, 'reg_checks', reg_checks, ck, F, RULE)
        _section(ck, 'prosp_checks', prosp_checks, ck, RULE)
        _section(ck, 'qbfit_checks', qbfit_checks, ck, F)
        _section(ck, 'plumb_checks', plumb_checks, ck, F, RULE)
        _section(ck, 'golden_checks', golden_checks, ck, F, RULE)
        _section(ck, 'core_checks', core_checks, ck, F, RULE)
        _section(ck, 'chain_checks', chain_checks, ck, F, RULE)
        _section(ck, 'tree_checks', tree_checks, ck, F, RULE)
        _section(ck, 'provenance_checks', provenance_checks, ck, F, RULE)
        _section(ck, 'stores_checks', stores_checks, ck, RULE)
        _section(ck, 'build_stores_checks', build_stores_checks, ck, F)
        _section(ck, 'selection_checks', selection_checks, ck, RULE)
        _section(ck, 'digest_checks', digest_checks, ck)
        _section(ck, 'provenance_io_checks', provenance_io_checks, ck, F, RULE)
        _section(ck, 'round4_checks', round4_checks, ck, F, RULE)
        _section(ck, 'dataset_cli_checks', dataset_cli_checks, ck)
        _section(ck, 'register_checks', register_checks, ck, F)

    # ---- the data-layer adapter (the one place the model reads nfl_rush_profiles)
    fld = lambda v, prior=None: {"value": v, "prior": prior, "unit": "share", "kind": "modelled"}
    blocks = {"d": {"vs_rb.ypc": fld(4.6, 4.3), "vs_rb.epa": fld(-0.02, -0.07), "per_game.rb_carries": fld(24.0),
                    "vs_rb.by_side.edge.ypc": fld(5.0, 4.6), "vs_rb.by_side.tackle.ypc": fld(4.4, 4.4),
                    "vs_rb.by_side.interior.ypc": fld(None, 4.1)},
              "dq": {"scrambles.oe_ratio": fld(1.25, 1.0), "hit_sack_rate.oe_ratio": fld(1.2, 1.0), "pressure": fld(0.9, 0.1),
                     "pressure_pfr": fld(0.7, 0.2)},
              "p": {"rushing.by_side.edge.share": fld(0.30, 0.21), "rushing.by_side.tackle.share": fld(0.25, 0.25),
                    "rushing.by_side.interior.share": fld(0.45, 0.54), "recent.carry_share": fld(0.55),
                    "usage.carry_share": fld(0.45)},
              "q": {"scramble.vs.pressure": fld(0.20, 0.17), "scramble.vs.no_pressure": fld(0.04, 0.05)}}
    ev = lambda n, lg=None, ext=None: R.evaluate(R.FEATURES[n], blocks, lg, ext)
    ck("adapter: dev = value - prior (the data layer's shrinkage target), log for an observed/expected ratio",
       abs(ev("spd_rb_ypc") - 0.3) < 1e-12 and abs(ev("spd_qb_scr_oe") - math.log(1.25)) < 1e-12)
    ck("adapter: an observed per-game count is centred on the league mean over every team at the cutoff",
       abs(ev("spd_rb_car_pg", lg={("d", "per_game.rb_carries"): 22.5}) - 1.5) < 1e-12 and ev("spd_rb_car_pg") is None)
    ck("adapter: direction = sum over sides of his share x the defense's departure; a side with no value drops out",
       ev("cfp_direction") is not None and abs(ev("cfp_direction") - (0.30 * 0.4 + 0.25 * 0.0)) < 1e-12)
    ck("adapter: a contrast (recent minus season share) and an interaction (his pressure split x the pressure dev)",
       abs(ev("dec_trend") - 0.10) < 1e-12 and abs(ev("cfp_scr_press") - (0.16 * math.log(1.2))) < 1e-12)
    ck("adapter: a missing input reads None (the caller's 0 = nothing known), never a crash",
       ev("ovd_pass_epa") is None and ev("spd_rbrec_ypt") is None and ev("dec_margin", ext={"margin7": 0.5}) == 0.5)
    allf = R.features(blocks, {}, {})
    ck("adapter: every feature of the table evaluates on any block set (no exception, value or None)",
       set(allf) == set(R.FEATURES) and all(v is None or isinstance(v, float) for v in allf.values()))
    ck("the ladder names only features the table defines, in the owner's order, each moving a count, an efficiency or a mean",
       all(f in R.FEATURES and acts in ("count", "eff", "mean") for fam in R.GROUPS.values() for g, fs in fam.items()
           for f, acts in fs) and all(list(fam) == [g for g in R.LADDER if g in fam] for fam in R.GROUPS.values()))
    tb = {"KC": {"d": {"per_game.rb_carries": fld(20.0), "per_game.rb_rush_yds": fld(90.0)}},
          "BUF": {"d": {"per_game.rb_carries": fld(26.0), "per_game.rb_rush_yds": fld(None)}}}
    lgm = R.league_means(tb)
    ck("league means over the teams, skipping a team without the value",
       lgm[("d", "per_game.rb_carries")] == 23.0 and lgm[("d", "per_game.rb_rush_yds")] == 90.0)
    qblk = {"q": {"designed.rate": fld(0.08), "sneak.rate": fld(0.01), "scramble.rate": fld(0.10),
                  "designed.ypc": fld(6.0), "scramble.ypa": fld(7.2)},
            "q_totals": {"exposure": 600.0, "dropbacks": 360.0}, "q_games": {"weighted": 10.0}}
    qp = R.qb_profile(qblk)
    # per game: designed 0.08 x 60 = 4.8, sneaks 0.6, scrambles 0.10 x 36 = 3.6 -> shares of 9.0
    ck("qb_profile: his own shares from the data layer's rates x his per-game exposure / dropbacks (the board's information)",
       qp and abs(qp["designed_share"] - 4.8 / 9.0) < 1e-12 and abs(qp["scramble_share"] - 3.6 / 9.0) < 1e-12
       and qp["ypa_designed"] == 6.0 and R.qb_profile({"q": {}}) is None)
    fb = {"p": {"usage.carry_share": {"value": 0.61, "unit": "share", "kind": "observed", "n": 300.0, "basis": "b",
                                      "reliability": 0.98}, "rushing.ypc": {"value": None, "unit": "yds_per_carry"},
                "usage.snap_share": {"value": 0.5}}}
    try:
        rf = R.facts(fb, R.ROLE_FACTS, "RB")
    except (KeyError, TypeError) as e:       # a fact without a unit must be dropped, never crash the board
        rf = {"error": repr(e)}
    ck("role facts pass the data layer's field through (value, unit, kind, n, basis) with a label; no unit -> no fact",
       set(rf) == {"carry_share"} and rf["carry_share"]["unit"] == "share" and rf["carry_share"]["value"] == 0.61
       and rf["carry_share"]["label"] and rf["carry_share"]["kind"] == "observed")
    ck("every fact table names a known block and a label; proxies say so in the label",
       all(b in ("p", "q", "d", "dq", "ol", "tc") and lab for t in (R.ROLE_FACTS, R.OPP_FACTS) for rows_ in t.values()
           for _k, b, _p, lab in rows_)
       and all("PROXY" in lab for t in (R.ROLE_FACTS, R.OPP_FACTS) for rows_ in t.values() for k, _b, _p, lab in rows_
               if k in ("edge_share", "tgt_per_db", "containment_proxy")))
    import array as _array
    import research.nfl_rush_model_frame as FRM
    reads = {(b, p_) for f in R.FEATURES for k, b, p_ in R.spec_leaves(R.FEATURES[f]) if k != "ext"}
    ck("pressure features read the pbp's sacks + hits rate, never participation's rate (stale live) or PFR's (licence)",
       ("dq", "pressure") not in reads and ("dq", "pressure_pfr") not in reads and ("dq", "hit_sack_rate.oe_ratio") in reads
       and ("dq", "hit_sack_rate.oe_ratio", "log") in FRM.VEC)

    # ---- the data licence default (F8): no PFR, PFR-snap or NGS input in anything fitted
    fitted = R.fitted_features()
    srcs = {f: R.feature_sources(f) for f in fitted}
    ck("every fitted feature is a feature the table defines, and every one of its inputs declares a source",
       set(fitted) <= set(R.FEATURES) and all("UNDECLARED" not in v for s_ in srcs.values() for v in s_.values()),
       {f: s_ for f, s_ in srcs.items() if any("UNDECLARED" in v for v in s_.values())})
    ck("every fitted input's source is pbp, participation, FTN, injuries or the pre-game line (or derived thereof)",
       all(set(v) <= R.FIT_SOURCES for s_ in srcs.values() for v in s_.values())
       and not ({"pfr", "ngs"} & R.FIT_SOURCES))
    DENY = ("pfr", "ngs", "ryoe", "snap", "contact.", "ybc", "rushing.back.", "broken_tackle", "missed_tackle",
            "pct8", "time_to_los", "starters.")
    fp = FRM.fitted_paths()
    ck("no fitted input (features or the comparables' vector) names a PFR / PFR-snap / NGS field",
       not [x for x in fp if any(d in x[1] for d in DENY)], [x for x in fp if any(d in x[1] for d in DENY)])
    ck("the comparables' vector inputs are declared and licence-clean too",
       all(set(R.PATH_SOURCE.get((b, p_), ("UNDECLARED",))) <= R.FIT_SOURCES for b, p_, _h in FRM.VEC))
    import nfl_rush_profiles as _RP
    KIND = {"p": "rb", "q": "qb", "d": "def", "dq": "dq", "ol": "ol", "tc": "tc"}
    _parts = lambda tag: set(tag[len("derived:"):].split("+")) if tag.startswith("derived:") else {tag}
    layer = {x: _RP.SOURCE_OF(KIND[x[0]], x[1]) for x in fp}
    ck("the data layer's own tags agree: no fitted input is restricted (RP.restricted), and every declaration here "
       "EQUALS the layer's SOURCE_OF", not [x for x, tag in layer.items() if _RP.restricted(tag)]
       and all(_parts(tag) == set(R.PATH_SOURCE.get(x, ("UNDECLARED",))) for x, tag in layer.items()),
       {x: (tag, R.PATH_SOURCE.get(x)) for x, tag in layer.items()
        if _RP.restricted(tag) or _parts(tag) != set(R.PATH_SOURCE.get(x, ("UNDECLARED",)))})
    ck("the layer's tags bite where they should: its contact, YBC, snap and PFR-pressure fields ARE restricted",
       all(_RP.restricted(_RP.SOURCE_OF(k_, p_)) for k_, p_ in (("def", "contact.missed_tackle_rate"), ("ol", "ybc"),
                                                                ("rb", "usage.snap_share"), ("rb", "rushing.back.yac"),
                                                                ("dq", "pressure_pfr"), ("ol", "starters.out_report"))))
    mut = dict(R.FEATURES, cfp_x=("prod", ("dev", "p", "rushing.back.yac"), ("dev", "d", "contact.missed_tackle_rate")))
    leaks = [(b, p_) for k, b, p_ in R.spec_leaves(mut["cfp_x"]) if (b, p_) not in R.PATH_SOURCE]
    ck("the check bites: a PFR contact term added to a group would have no declared source (and matches the deny list)",
       leaks and all(any(d in p_ for d in DENY) for _b, p_ in leaks))
    ck("pending inputs (announced by the data layer, not landed) are fitted inputs with a declared, licence-clean source",
       set(R.PENDING_PATHS) <= fp and all(set(R.PATH_SOURCE[x]) <= R.FIT_SOURCES for x in R.PENDING_PATHS))
    fb2 = {"p": {"usage.snap_share": {"value": 0.62, "unit": "share", "kind": "observed", "n": 5, "basis": "PFR snaps"},
                 "rushing.back.ryoe": {"value": 0.4, "unit": "yds_per_carry", "kind": "observed", "n": 50, "basis": "NGS"},
                 "usage.carry_share": {"value": 0.5, "unit": "share", "kind": "observed", "n": 80, "basis": "b"}}}
    rf2 = R.facts(fb2, R.ROLE_FACTS, "RB")
    ck("PFR / NGS facts are shown credited and say they do not move a probability; pbp facts carry their own credit",
       rf2["snap_share"]["credit"] == "Pro Football Reference via nflverse" and R.DISPLAY_ONLY in rf2["snap_share"]["basis"]
       and rf2["ryoe"]["credit"] == "NFL Next Gen Stats via nflverse" and R.DISPLAY_ONLY in rf2["ryoe"]["basis"]
       and rf2["carry_share"]["source"] == "pbp" and R.DISPLAY_ONLY not in rf2["carry_share"]["basis"]
       and set(R.FACT_SOURCE) == {k for t_ in (R.ROLE_FACTS, R.OPP_FACTS) for rows_ in t_.values() for k, _b, _p, _l in rows_})
    rbc = R.rb_breakdown(p0, base, {"ybc_per_att": 2.4, "yac_per_att": 1.9})
    ck("the RB breakdown's contact split is credited to PFR and says it does not move a probability",
       all("Pro Football Reference via nflverse" in i["basis"] and R.DISPLAY_ONLY in i["basis"]
           for i in rbc if "contact" in i["label"]) and sum("contact" in i["label"] for i in rbc) == 2)
    seen_ = {x for x in fp if x != ("dq", "hit_sack_rate.oe_ratio")}
    ck("the frame records a fitted input no row carried (a pending or renamed field), so it is never fitted",
       FRM.absent_paths(seen_) == [("dq", "hit_sack_rate.oe_ratio")] and FRM.absent_paths(fp) == [])
    st = {"built": 7, "n": 3, "cols": {"part": _array.array("B", [1, 0, 1]), "yds": _array.array("h", [4, 2, 9])},
          "onfield": {0: {"x": 1}}, "games": []}
    stp = {"built": 5, "n": 2, "cols": {"part": _array.array("B", [1, 1])}, "onfield": {0: {"y": 2}}}
    ax = {"store_built": 7, "store_n": 3, "sit": {"players": {1: 2}, "teams": {}, "plays": 9}, "extras": {"rush_yds": [1]}}
    axp = {"sit": {"players": {3: 4}, "teams": {}, "plays": 5}}
    vs, va = FRM.availability_view({2025: st, 2024: stp}, {2025: ax, 2024: axp}, 2025)
    import nfl_rush_data as _RD
    ck("the frame's availability view drops ONLY the season's own participation (flag, on-field, situational); priors whole",
       list(vs[2025]["cols"]["part"]) == [0, 0, 0] and vs[2025]["onfield"] == {} and va[2025]["sit"]["plays"] == 0
       and list(vs[2025]["cols"]["yds"]) == [4, 2, 9] and vs[2024] is stp and va[2024] is axp
       and list(st["cols"]["part"]) == [1, 0, 1] and st["onfield"] and ax["sit"]["plays"] == 9
       and _RD.aux_matches(va[2025], vs[2025]) and va[2025]["extras"] is ax["extras"])
    import ast as _ast
    src = open(os.path.join(REPO, "research", "nfl_rush_model_frame.py"), encoding="utf-8").read()
    rep_txt = open(os.path.join(REPO, "research", "reports", "nfl_rush_model.md"), encoding="utf-8").read()
    pre = rep_txt[rep_txt.find("<!-- RUSH-PREREG-START -->"):rep_txt.find("<!-- RUSH-PREREG-END -->")]
    ck("the rule's Q3a feature lists ARE the adapter's definitions (generated text, inside the registered block)",
       FRM.feature_lists_md() in pre)
    drow = {c: "1" for c in FRM.ID_COLS + FRM.BASELINE_COLS + FRM.OUTCOME_COLS}
    drow.update(pos="RB", act_played="1", in_sleeper="1", sl_qb1="1", sl_act_rush_yd="57", act_rush_yds="55")
    idn, yo = FRM.identity_of(drow), FRM.outcomes_of(drow)
    ck("the frame's identity carries no outcome or post-week Sleeper field; every outcome it writes is y_-prefixed",
       set(idn) == set(FRM.ID_COLS) | {"pos"} and all(k.startswith("y_") for k in yo)
       and yo["y_rush_yd"] == 57.0 and yo["y_played"] == "1" and yo["y_src"] == "sleeper_box"
       and FRM.outcomes_of(dict(drow, sl_act_gp="", act_played="1", act_rush="0", act_tgt="0"))["y_src"] == "did_not_play"
       and FRM.outcomes_of(dict(drow, sl_act_gp="", act_played="1", act_rush="0", act_tgt="0"))["y_cond_played"] == 0
       and FRM.outcomes_of(dict(drow, sl_act_gp="0", act_rush="2"))["y_cond_played"] == 1
       and FRM.outcomes_of(dict(drow, sl_act_gp="1", act_rush="0", act_tgt="0"))["y_cond_played"] == 1
       and FRM.outcomes_of({k: v for k, v in dict(drow, played="1").items() if k != "act_played"})["y_played"] == "1")
    tree = _ast.parse(open(os.path.join(REPO, "research", "nfl_rush_model_frame.py"), encoding="utf-8").read())
    allowed = set(FRM.ID_COLS) | set(FRM.BASELINE_COLS) | {"pos", "sleeper_id"}
    outcome_cols = set(FRM.OUTCOME_COLS) | set(FRM.PLAYED_COLS) | set(FRM.OPTIONAL_OUTCOME_COLS)
    bad = []
    for fn in [n for n in _ast.walk(tree) if isinstance(n, _ast.FunctionDef)]:
        for n in _ast.walk(fn):
            key = None
            if isinstance(n, _ast.Subscript) and isinstance(n.value, _ast.Name) and n.value.id == "r" \
                    and isinstance(n.slice, _ast.Constant):
                key = n.slice.value
            elif isinstance(n, _ast.Call) and isinstance(n.func, _ast.Attribute) and n.func.attr == "get" \
                    and isinstance(n.func.value, _ast.Name) and n.func.value.id == "r" and n.args \
                    and isinstance(n.args[0], _ast.Constant):
                key = n.args[0].value
            if key is None:
                continue
            if not (key in allowed or (key in outcome_cols and fn.name == "outcomes_of")):
                bad.append((fn.name, key))
    fsrc = open(os.path.join(REPO, "research", "nfl_rush_model_frame.py"), encoding="utf-8").read()
    ck("the frame reads the dataset by declared NAME only: identities and Sleeper's baseline anywhere, outcomes only in "
       "outcomes_of, in_sleeper / sl_qb1 nowhere", not bad and all(fsrc.count(f'"{c}"') == 1 for c in FRM.NEVER_READ), bad)
    with tempfile.TemporaryDirectory() as td_:
        pth = os.path.join(td_, "rb.csv.gz")
        import gzip as _gz
        with _gz.open(pth, "wt") as fh:
            fh.write(",".join(c for c in FRM.ID_COLS + FRM.BASELINE_COLS + FRM.OUTCOME_COLS) + "\n")   # no played column
        old_ds = FRM.DATASET
        FRM.DATASET = {"RB": pth}
        try:
            FRM.dataset_rows({2023})
            refused = False
        except SystemExit:
            refused = True
        finally:
            FRM.DATASET = old_ds
    ck("the frame refuses a dataset that lacks a column it reads by name (a rename fails loudly)", refused)
    _st = lambda b: {"built": b, "n": 1}
    _ax = lambda b: {"store_built": b, "store_n": 1, "complete": True}
    good_s = {y: _st(y) for y in range(2019, 2027)}
    good_a = {y: _ax(y) for y in range(2019, 2027)}

    def _refuses(st_, ax_):
        try:
            FRM.check_inputs(st_, ax_, (2023, 2024, 2025))
            return False
        except SystemExit:
            return True
    ck("the frame refuses a season without an aligned, COMPLETE rushing aux (an old-schema or short aux blanks sources silently)",
       not _refuses(good_s, good_a) and _refuses(good_s, {**good_a, 2024: None})
       and _refuses(good_s, {**good_a, 2022: _ax(99)}) and _refuses({k: v for k, v in good_s.items() if k != 2019}, good_a)
       and _refuses(good_s, {**good_a, 2025: dict(_ax(2025), complete=False)})
       and _refuses(good_s, {**good_a, 2021: {k: v for k, v in _ax(2021).items() if k != "complete"}}))
    ck("the frame builds every snapshot through the availability view (the season's own participation never enters)",
       "vs, va = availability_view(stores, auxes, s)" in src and "RP.build(vs, va, s, w, pos)" in src
       and src.count("RP.build(") == 1)
    keys = {n.value for n in _ast.walk(_ast.parse(src)) if isinstance(n, _ast.Constant) and isinstance(n.value, str)}
    ck("the frame's game script reads the PRE-GAME line (nflverse spread / total), never the final score",
       "spread" in keys and "total" in keys and not ({"hs", "as"} & keys))

    # ---- the board hook (nfl_matchup._board): inert without coefs, prices only what shipped
    _section(ck, 'hook_checks', hook_checks, ck, co)
    _section(ck, 'hook_more_checks', hook_more_checks, ck, co)
    _section(ck, 'hook_gate_checks', hook_gate_checks, ck, co)

    # ---- cost and purity
    t0 = time.time()
    R.qb_dists(qb, prof, co, team_margin=4.0)
    ck("a QB's two compounds build in well under a second (pure Python FFT)", time.time() - t0 < 0.5, time.time() - t0)
    out = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r); import nfl_rush; "
                          "print('numpy' in sys.modules)" % REPO], capture_output=True, text=True, timeout=60,
                         env=dict(os.environ, VIGIL_NO_BG="1"))
    ck("nfl_rush never imports numpy (the server has none)", out.stdout.strip() == "False", out.stdout + out.stderr[-300:])


def _leaf(v, prior=None, unit="share", kind="observed"):
    return {"value": v, "prior": prior, "unit": unit, "kind": kind, "n": 120.0, "basis": "fixture"}


class _FakeSnap:
    """A rushing snapshot for the hook fixture: the few fields its features and facts read."""

    def rb(self, pid, team):
        return {"usage": {"carry_share": _leaf(0.55, 0.45), "snap_share": _leaf(0.61)},
                "recent": {"carry_share": _leaf(0.60)},
                "rushing": {"ypc": _leaf(4.4, 4.2, "yds_per_carry"), "line": {"ybc": _leaf(2.1, None, "yds_per_carry")},
                            "back": {"yac": _leaf(2.3, 2.2, "yds_per_carry"), "ryoe": _leaf(0.3, None, "yds_per_carry")}}}

    def qb(self, pid, team):
        return {"designed": {"rate": _leaf(0.06), "ypc": _leaf(5.0, None, "yds_per_carry"),
                             "per_game": _leaf(3.6, None, "per_game")},
                "sneak": {"rate": _leaf(0.01)}, "scramble": {"rate": _leaf(0.07), "ypa": _leaf(7.4, None, "yds")},
                "hit_sack_rate": _leaf(0.09), "totals": {"exposure": 620.0, "dropbacks": 360.0}, "games": {"weighted": 10.0}}

    def rb_room_out(self, team, exclude=None):
        return {"value": 0.0}


def _hook_fixture():
    """(rows, kalshi index, the extra series, the rushing env) for one KC @ BUF game: an RB, a QB, a WR."""
    import kalshi_nfl as KN
    sfx = "26OCT25KCBUF"

    def mkt(series, name, stat_txt, strike, ya, na):
        return {"event_ticker": f"{series}-{sfx}", "title": f"{name}: {strike + 0.5:.0f}+ {stat_txt}", "floor_strike": strike,
                "yes_ask_dollars": ya, "no_ask_dollars": na, "yes_bid_dollars": "0.40", "no_bid_dollars": "0.52",
                "ticker": f"{series}-{sfx}-{int(strike)}", "close_time": "2026-10-25T17:00:00Z", "volume": 120,
                "open_interest": 300}
    idx, extra = {}, {}

    def ent_of(d):
        return lambda s: d.setdefault(s, {"players": {}, "names": {}, "q": {}, "tick": {}, "no": {"players": {}}})
    KN._add_props(ent_of(idx), [mkt("KXNFLRSHYDS", "Rex Runner", "rushing yards", 59.5, "0.47", "0.55"),
                                mkt("KXNFLREC", "Wade Wideout", "receptions", 4.5, "0.50", "0.52")], "rush_yd")
    idx[sfx]["pair"] = frozenset(("KC", "BUF"))
    KN._add_props(ent_of(extra), [mkt("KXNFLRSHATT", "Rex Runner", "rushing attempts", 14.5, "0.44", "0.58"),
                                  mkt("KXNFLRSHATT", "Quin Passer", "rushing attempts", 3.5, "0.52", "0.50")], "rush_att")

    def row(pid, first, last, pos, st):
        return {"player_id": pid, "team": "KC", "opponent": "BUF", "game_id": "77001", "date": "2026-10-25",
                "stats": dict(st, pts_ppr=12.0), "player": {"first_name": first, "last_name": last, "position": pos}}
    rows = [row("1001", "Rex", "Runner", "RB", {"rush_att": 15.0, "rush_yd": 66.0, "rush_td": 0.45, "rec_tgt": 3.2,
                                                 "rec": 2.5, "rec_yd": 18.0, "rec_td": 0.08}),
            row("1002", "Quin", "Passer", "QB", {"pass_att": 34.0, "pass_cmp": 22.0, "pass_yd": 250.0, "pass_td": 1.6,
                                                  "pass_int": 0.7, "rush_att": 4.5, "rush_yd": 24.0, "rush_td": 0.2}),
            row("1003", "Wade", "Wideout", "WR", {"rec_tgt": 7.0, "rec": 4.6, "rec_yd": 58.0, "rec_td": 0.35})]
    tb = {t: {"d": {"vs_rb.ypc": _leaf(4.6, 4.3, "yds_per_carry"), "vs_rb.epa": _leaf(0.01, -0.02, "epa_per_play")},
              "dq": {"hit_sack_rate.oe_ratio": _leaf(1.2, 1.0, "ratio"), "blitz": _leaf(0.28, 0.25)},
              "ol": {"ol_listed_out": _leaf(1.0, None, "count")}, "tc": {"neutral_run_rate": _leaf(0.42, 0.41)}}
          for t in ("KC", "BUF")}
    env = {"extra": extra, "S": _FakeSnap(), "tb": tb, "lg": {}, "sl2g": {"1001": 501, "1002": 502}, "seconds": 0.0}
    return rows, idx, extra, env


def _hook_coefs(co):
    """The fixture model: RB carries SPD + SHAPE provisional, RB rushing yards SHAPE shipped, a reverted
    RB receptions entry (must read not shipped), QB rushing yards OVD provisional in the mean form."""
    return dict(co, dispersion={"RB": {"rush_att": {"alpha": 0.12}, "rush_yd": {"cv": 0.63}, "rec": {"alpha": 0.17},
                                       "rec_yd": {"cv": 0.86}},
                                "QB": {"rush_att": {"alpha": 0.11}, "rush_yd": {"cv": 0.70}}},
                shipped={"RB:rush_att": [{"group": "SPD", "status": "provisional"}, {"group": "SHAPE", "status": "provisional"}],
                         "RB:rush_yd": [{"group": "SHAPE", "status": "shipped"}],
                         "RB:rec": [{"group": "SPD", "status": "reverted"}],
                         "QB:rush_yd": [{"group": "OVD", "status": "provisional"}]},
                models={"RB:carries": {"terms": [{"feature": "spd_rb_ypc", "b": 0.05, "mean": 0.0, "sd": 0.2}],
                                       "cov": [[0.0004]], "clamp": 1.0},
                        "RB:ypc": {"terms": [], "cov": None, "clamp": 1.0},
                        "QB:mean_rush_yd": {"terms": [{"feature": "ovd_pressure", "b": 0.03, "mean": 0.0, "sd": 0.1}],
                                            "cov": [[0.0001]], "clamp": 1.0}})


def hook_checks(ck, co):
    import nfl_matchup as M
    import nfl_rush_profiles as RP
    import kalshi_nfl as KN
    rows, idx, extra, env = _hook_fixture()
    saved = (M.production_ctx, R.COEF_PATH, dict(R._COEFS), KN.extra_props, RP.build)
    calls = []
    try:
        M.production_ctx = lambda season: None
        R.COEF_PATH = os.path.join(tempfile.gettempdir(), "no_such_nfl_rush_coefs.json")
        R._COEFS.update(mtime=None, data=None)
        KN.extra_props = lambda: calls.append("extra_props") or {}
        RP.build = lambda *a, **k: calls.append("RP.build")
        args = dict(rows=rows, kalshi_idx=idx, schedule=[], ctx=None, lines={}, with_market=True, now=1793000000.0)
        b_none = M.board(2026, 7, **args)
        b_off = M.board(2026, 7, rush_coefs=False, **args)
        s_none, s_off = json.dumps(b_none, sort_keys=True), json.dumps(b_off, sort_keys=True)
        ck("hook: with no committed rushing model the board is byte-identical to the hook switched off, and neither "
           "fetches Kalshi's extra series nor builds a rushing snapshot",
           s_none == s_off and not calls and len(b_none["players"]) == 3 and "error" not in b_none
           and not any("breakdown" in p for p in b_none["players"]) and not any("correlations" in g for g in b_none["games"])
           and "rushing" not in (b_none.get("model") or {}), (calls, b_none.get("error")))
        rc = _hook_coefs(co)
        b = M.board(2026, 7, rush_coefs=rc, rush_env=env, **args)
    finally:
        M.production_ctx, R.COEF_PATH, KN.extra_props, RP.build = saved[0], saved[1], saved[3], saved[4]
        R._COEFS.clear()
        R._COEFS.update(saved[2])
    pl = {p["pos"]: p for p in b["players"]}
    pl0 = {p["pos"]: p for p in b_none["players"]}
    mk = lambda p: {m["stat"]: m for m in p["markets"]}
    rb, qb, rb0, qb0 = mk(pl["RB"]), mk(pl["QB"]), mk(pl0["RB"]), mk(pl0["QB"])
    ck("hook: an RB's shipped markets come from the rushing model (compound carries and yards, the longest rush "
       "derived), with their statuses",
       rb["rush_att"].get("engine") == "rushing" and rb["rush_att"]["matchup_weight"] == "provisional"
       and rb["rush_att"]["dist"]["family"] == "negbin" and rb["rush_yd"]["matchup_weight"] == "shipped"
       and rb["rush_yd"]["dist"]["family"] == "compound" and rb["long_rush"]["dist"]["family"] == "longest_of_compound"
       and rb["long_rush"]["matchup_weight"] == "shipped", {k: (v.get("engine"), v["matchup_weight"]) for k, v in rb.items()})
    ck("hook: the carries factor is the committed model's (exp(0.05 x 1.5)) and the yards level follows it",
       abs(rb["rush_att"]["adjusted_mean"] - 15.0 * math.exp(0.075)) < 2e-3
       and abs(rb["rush_yd"]["adjusted_mean"] - 66.0 * math.exp(0.075)) < 0.05, (rb["rush_att"]["adjusted_mean"],
                                                                            rb["rush_yd"]["adjusted_mean"]))
    ck("hook: a market with no shipped group (RB receptions: a 'reverted' entry; receiving yards; TD) keeps the first "
       "model's block byte for byte, and the board says it is at rung 1",
       all(json.dumps(rb[m], sort_keys=True) == json.dumps(rb0[m], sort_keys=True) for m in ("rec", "rec_yd", "td"))
       and "engine" not in rb["rec"] and any("rung 1" in n and "rec" in n for n in pl["RB"]["notes"]))
    ck("hook: a derived market is priced only through shipped components (rush + rec needs receiving yards' SHAPE: "
       "absent here), and nothing new is priced for a QB whose compound did not ship",
       "rush_rec_yd" not in rb and "long_rush" not in qb and "rush_rec_yd" not in rb0 and "long_rush" not in rb0)
    ck("hook: a QB market shipped in the mean form uses the rule's own refit family (not the first model's) at "
       "Sleeper x the committed factor; his unshipped attempts keep the first model's block",
       qb["rush_yd"].get("engine") == "rushing" and qb["rush_yd"]["matchup_weight"] == "provisional"
       and abs(qb["rush_yd"]["adjusted_mean"] - 24.0 * math.exp(0.03 * math.log(1.2) / 0.1)) < 1e-3
       and json.dumps(qb["rush_att"], sort_keys=True) == json.dumps(qb0["rush_att"], sort_keys=True))
    ra = {ln["k"]: ln for ln in rb["rush_att"]["lines"]}
    qa = {ln["k"]: ln for ln in qb["rush_att"]["lines"]}
    ck("hook: Kalshi's extra rushing series reach the right player's rungs (his 15+ carries priced; the QB's 4+ "
       "attempts line not on the RB, and not on the QB, whose attempts market did not ship)",
       15 in ra and (ra[15]["market"] or {}).get("ticker", "").startswith("KXNFLRSHATT") and ra[15]["value"] is not None
       and all((ln.get("market") or {}).get("ticker", "").startswith("KXNFLRSHATT") is False for k, ln in qa.items()))
    ck("hook: bands only where a shipped coefficient carries uncertainty (the carries term's), none on the "
       "SE-free yards factor... but the yards compound inherits the carries SE",
       all(ln["p_lo"] is not None and ln["p_lo"] < ln["p_hi"] for ln in rb["rush_att"]["lines"])
       and all(ln["p_lo"] is not None for ln in rb["rush_yd"]["lines"]))
    bd = pl["RB"].get("breakdown") or []
    role = pl["RB"].get("role") or {}
    ck("hook: the RB carries the breakdown (expected carries, yards before / after contact credited to PFR, display "
       "only) and the role facts, a PFR fact credited and display-only",
       any(i["label"] == "Expected carries" for i in bd)
       and all("Pro Football Reference via nflverse" in i["basis"] and R.DISPLAY_ONLY in i["basis"] for i in bd
               if "contact" in i["label"])
       and role.get("snap_share", {}).get("credit") == "Pro Football Reference via nflverse"
       and R.DISPLAY_ONLY in role.get("snap_share", {}).get("basis", "")
       and role.get("ryoe", {}).get("credit") == "NFL Next Gen Stats via nflverse", (bd[:2], list(role)))
    ck("hook: the QB carries his four components and his own split as information; the game carries the "
       "correlations; the slate says what is provisional; the model block names the rushing statuses",
       any(i["label"] == "Expected kneels" for i in (pl["QB"].get("breakdown") or []))
       and any(i["label"].startswith("His own share") for i in (pl["QB"].get("breakdown") or []))
       and all(g.get("correlations") for g in b["games"]) and any("provisional" in n for n in b["notes"])
       and b["model"]["rushing"]["markets"]["RB:rec"] == "0" and b["model"]["rushing"]["markets"]["RB:rush_yd"] == "shipped")
    ck("hook: the WR is untouched by the rushing model (byte for byte)",
       json.dumps(pl["WR"], sort_keys=True) == json.dumps(pl0["WR"], sort_keys=True))
    s_ = json.dumps(b)
    ck("hook: the payload with the rushing model is JSON with no NaN / inf, every line monotone in k",
       "NaN" not in s_ and "Infinity" not in s_
       and all(all(a["p"] >= c["p"] - 1e-12 for a, c in zip(m["lines"], m["lines"][1:])) for p in b["players"]
               for m in p["markets"] if m.get("engine") == "rushing"))


def _synth_rows(seed=3):
    """A synthetic RB frame (2023-2026): carries move with spd_rb_car_pg (b 0.15 per unit), yards per
    carry with spd_rb_ypc, everything else noise; every outcome drawn from the generating model. No
    real outcome anywhere."""
    import random
    rnd = random.Random(seed)
    rows = []
    for s in (2023, 2024, 2025, 2026):
        for g in range(45):
            for j in range(3):
                c = rnd.uniform(6, 20)
                x, e = rnd.gauss(0, 1), rnd.gauss(0, 1)
                mu_c = c * math.exp(0.15 * x)
                ac = sum(1 for _ in range(int(mu_c * 3)) if rnd.random() < 1 / 3)
                yds = sum(rnd.choice((-1, 1, 2, 3, 4, 5, 6, 8, 12, 20)) * math.exp(0.05 * e) for _ in range(ac))
                r = {"season": str(s), "week": str(1 + g % 6), "game_id": f"{s}_{g}", "team": "T", "opp": "U",
                     "gsis": f"p{j}_{g % 9}", "pos": "RB", "margin": "0", "kick_ts": str(1.79e9 + g),
                     "sl_rush_att": f"{c:.3f}", "sl_rush_yd": f"{4.3 * c:.3f}", "sl_rush_td": "0.4", "sl_rec_tgt": "0",
                     "sl_rec": "0", "sl_rec_yd": "0", "sl_rec_td": "0", "sl_pass_att": "0",
                     "fz_src": "props" if s == 2026 else "", "y_gp": "1", "y_pbp_play": "1", "y_cond_played": "1",
                     "y_rush_att": str(ac), "y_rush_yd": f"{yds:.1f}", "y_rush_td": "0", "y_rec_td": "0"}
                for k, v in (("sl", "fz"),):
                    for f_ in ("rush_att", "rush_yd", "rush_td", "rec_tgt", "rec", "rec_yd", "rec_td", "pass_att"):
                        r[f"{v}_{f_}"] = r[f"{k}_{f_}"]
                for n in R.FEATURES:
                    r[f"f_{n}"] = "0"
                r["f_spd_rb_car_pg"], r["f_spd_rb_ypc"] = f"{x:.4f}", f"{e:.4f}"
                rows.append(r)
    return rows


def rule_checks(ck, F, RULE):
    import numpy as np
    # the statistic
    d, se = RULE.clustered([1.0, -1.0, 2.0, 0.0], ["a", "a", "b", "b"])
    ck("rule: the clustered SE is G/(G-1) x the clusters' CENTRED sums squared / n^2 (exactly 0.5 here; 0.354 "
       "without G/(G-1), 0.707 uncentred)", abs(d - 0.5) < 1e-12 and abs(se - 0.5) < 1e-12, (d, se))
    dd, sse, sg, sp = RULE.paired([1.0, -1.0, 2.0, 0.0, 1.5, -0.5], ["a", "a", "b", "b", "c", "c"],
                                  ["x", "x", "x", "y", "y", "y"])
    d3, se3, sg3, sp3 = RULE.paired([1.0, 1.0, -1.0, -1.0], ["a", "b", "a", "b"], ["x", "x", "y", "y"])
    ck("rule: the decision SE is the LARGER of the game- and player-clustered SEs (either way round: here the "
       "games' is 0 and the players' 1)",
       abs(sse - max(sg, sp)) < 1e-15 and sg != sp and abs(sg3) < 1e-15 and abs(se3 - 1.0) < 1e-12
       and abs(sp3 - 1.0) < 1e-12, (sg, sp, sg3, sp3, se3))
    a2 = RULE.accept_2025
    ck("rule: 2025 gate strict at -2 SE and at the floor (0.0005 x the number of thresholds); NaN rejects",
       not a2(-0.0100, 0.005, 4) and a2(-0.01001, 0.005, 4) and not a2(-0.0019, 0.0001, 4) and a2(-0.00201, 0.0001, 4)
       and not a2(float("nan"), 0.001, 4) and not a2(-0.5, float("nan"), 4) and RULE.GATE_SE == 2.0 and RULE.MIN_GAIN == 0.0005)
    ck("rule: 2026 keeps on d <= 0 (point estimate), drops on d > 0 or NaN",
       RULE.keep_2026(0.0) and RULE.keep_2026(-1e-9) and not RULE.keep_2026(1e-12) and not RULE.keep_2026(float("nan")))
    ck("rule: the prospective KEEP is d < -1 SE (strict, one-sided); NaN reverts",
       not RULE.keep_prospective(-0.01, 0.01) and RULE.keep_prospective(-0.0101, 0.01)
       and not RULE.keep_prospective(-0.0001, float("nan")) and RULE.KEEP_SE == 1.0)
    # the walk: sequential, each group against the groups already kept
    seen = []

    def ev(kept, g):
        seen.append((tuple(kept), g))
        val = {"A": -1.0, "B": (1.0 if "A" in kept else -1.0), "C": -1.0, "D": 1.0}[g]
        return val, 0.1, None
    kept, steps = RULE.walk(["A", "B", "C", "D"], ev, lambda d_, s_: d_ < -2 * s_)
    ck("rule: the walk is sequential in the given order, each group judged on top of the groups already kept, a "
       "dropped group never retried", kept == ["A", "C"] and seen == [((), "A"), (("A",), "B"), (("A",), "C"), (("A", "C"), "D")]
       and [s_["kept"] for s_ in steps] == [True, False, True, False])
    ck("rule: a record's groups are walked in the owner's ladder order whatever order the record lists",
       RULE._accepted_order(["FULL", "SHAPE", "OVD"]) == ["OVD", "SHAPE", "FULL"])
    # the window
    kicks = {f"g{i}": 1000.0 + i for i in range(63)}
    weeks = {f"g{i}": 7 + i // 9 for i in range(63)}            # weeks 7..13, 9 games each
    lw, games, note = RULE.window_games(kicks, 1005.5, weeks)
    ck("rule: the window counts games kicking off STRICTLY after registration through week 11 (39 here), extended a "
       "week at a time to reach 40 (week 12: 48 games)", lw == 12 and len(games) == 48 and "g5" not in games
       and "g6" in games and note is None, (lw, len(games)))
    lw2, games2, note2 = RULE.window_games({k: v for k, v in kicks.items() if weeks[k] <= 9}, 1005.5, weeks)
    ck("rule: still under 40 at week 13 -> the test runs on what exists and SAYS it is under the floor",
       lw2 == 13 and len(games2) == 21 and note2 and "under the 40" in note2, (lw2, len(games2)))
    # groups and terms
    dead = {"RB": {"cfp_direction"}, "QB": {"cfp_dsg_edge", "spd_qb_td_oe"}}
    ck("rule: SHAPE is visited only where the compound prices the market (never TD), dead features leave every fit, "
       "a group with no live term is not evaluated",
       "SHAPE" in RULE.market_groups("RB", "rush_yd", dead) and "SHAPE" not in RULE.market_groups("RB", "td", dead)
       and "cfp_direction" not in RULE.group_terms("RB", "rush_yd", "CFP", False, dead)
       and RULE.market_groups("QB", "td", dead) == ["FULL"] and RULE.market_groups("RB", "rush_att", dead)[0] == "SPD")
    ck("rule: a count market reads count terms; a yards market all of a group's terms before SHAPE and only its eff "
       "terms after", RULE.group_terms("RB", "rush_att", "SPD", False, dead) == ["spd_rb_car_pg"]
       and "spd_rb_car_pg" in RULE.group_terms("RB", "rush_yd", "SPD", False, dead)
       and "spd_rb_car_pg" not in RULE.group_terms("RB", "rush_yd", "SPD", True, dead)
       and set(R.QB_COMPONENT) >= {f for g, fs in R.GROUPS["qb_rush"].items() for f, _a in fs})
    # playing, records
    ck("rule: scored conditional on playing -- Sleeper's gp >= 1 or a pbp rush / target / dropback; act_played "
       "(PFR snaps) never decides", RULE.played({"y_gp": "1"}) and RULE.played({"y_gp": "0", "y_pbp_play": "2"})
       and not RULE.played({"y_gp": "0", "y_pbp_play": "0", "y_played": "1"}) and not RULE.played({}))
    with tempfile.TemporaryDirectory() as td:
        p1 = os.path.join(td, "r.json")
        s1 = RULE.write_once(p1, {"a": 1}, False)
        try:
            RULE.write_once(p1, {"a": 2}, False)
            again = True
        except SystemExit:
            again = False
        s2 = RULE.write_once(p1, {"a": 3}, True)
        rr = json.load(open(p1))
        ck("rule: a decision record is written once; a re-run needs --force and says so (with the previous sha)",
           s1 and not again and s2 != s1 and rr.get("forced_rerun") is True and rr.get("previous_sha256") == s1)
        old_out = F.MODEL_OUT
        F.MODEL_OUT = td
        try:
            RULE.require_registered(False)
            unreg = False
        except SystemExit:
            unreg = True
        finally:
            F.MODEL_OUT = old_out
        ck("rule: every command refuses without a valid registration (plumbing excepted: it reads no held outcome)",
           unreg and RULE.require_registered(True) == {"plumb": True})
    try:
        RULE.build_coefs(None, {}, "reverted", (2023,), {}, {})
        bad_status = False
    except SystemExit as e:
        bad_status = "not a shipping status" in str(e)
    except Exception:         # noqa: BLE001 -- anything but the status refusal is a failure of this check
        bad_status = False
    ck("rule: the export writes only a shipping status ('provisional' / 'shipped')", bad_status)
    # fit -> export -> board: the replay (the scored distribution IS the board's)
    rows = _synth_rows()
    Fr = RULE.Frame(rows, RULE.kind_test)
    parts = {k: v for k, v in _coefs().items() if k in ("schema", "kernels", "alpha", "kneels", "qb_prior")}
    disp = {"RB": {"rush_att": {"alpha": 0.1}, "rush_yd": {"cv": 0.7}, "rec": {"alpha": 0.1}, "rec_yd": {"cv": 0.8}},
            "QB": {"rush_att": {"alpha": 0.1}, "rush_yd": {"cv": 0.7}}}
    st_mean = {"rush_att": ["SPD"]}
    st_shape = {"rush_att": ["SPD", "SHAPE"], "rush_yd": ["SPD", "SHAPE"]}
    fr_m = RULE.fit_position(Fr, "RB", st_mean, (2023, 2024, 2025), parts, disp, dead)
    fr_s = RULE.fit_position(Fr, "RB", st_shape, (2023, 2024, 2025), parts, disp, dead)
    mm = fr_m["models"].get("RB:mean_rush_att") or {}
    car = fr_s["models"].get("RB:carries") or {}
    ypc = fr_s["models"].get("RB:ypc") or {}
    ck("rule: the fit has NO intercept, centres and scales by the fit rows, recovers the generating carries effect "
       "(0.15 per unit) and puts eff terms on yards per carry once SHAPE is in",
       mm and [t_["feature"] for t_ in mm["terms"]] == ["spd_rb_car_pg"] and "intercept" not in mm
       and 0.08 < mm["terms"][0]["b"] / mm["terms"][0]["sd"] < 0.22 and car and ypc
       and [t_["feature"] for t_ in ypc["terms"]] == ["spd_rb_ypc"], (mm, ypc.get("terms")))
    # the yards GLM's offset carries the carries market's fitted factor once SHAPE is in (Q3b)
    calls, real_ft = [], RULE.fit_target
    try:
        RULE.fit_target = lambda F_, idx_, terms_, y_, off_, seas_: (calls.append((list(idx_), list(terms_),
                                                                                  np.array(off_, float)))
                                                                    or real_ft(F_, idx_, terms_, y_, off_, seas_))
        fr_o = RULE.fit_position(Fr, "RB", st_shape, (2023, 2024, 2025), parts, disp, dead)
    finally:
        RULE.fit_target = real_ft
    iy = Fr.select("RB", "rush_yd", (2023, 2024, 2025))
    want = np.log(np.maximum([Fr.base(i)["rush_yd"] for i in iy], 1e-6)) + RULE.eta_of(fr_o["models"]["RB:carries"], Fr, iy)
    got_off = next((o_ for ix_, t_, o_ in calls if ix_ == iy and "spd_rb_ypc" in t_ and "spd_rb_car_pg" not in t_), None)
    ck("rule: from SHAPE on, the yards-per-carry GLM's offset is log(Sleeper's yards) + the carries model's fitted "
       "log factor, row by row", got_off is not None and np.allclose(got_off, want) and np.ptp(want - np.log(np.maximum(
           [Fr.base(i)["rush_yd"] for i in iy], 1e-6))) > 0.01)
    # dead features and the played-only selection
    cst = [dict(r, f_spd_rb_ypc="0.5") for r in rows[:30]]
    dd_ = RULE.dead_features([RULE.Frame(cst, RULE.kind_test)], {0: (2023, 2024, 2025, 2026)})
    ck("rule: a feature with zero SD over a position's training rows is dead", "spd_rb_ypc" in dd_["RB"])
    nplay = [dict(r, y_cond_played="0") if k % 2 else r for k, r in enumerate(rows)]
    Fn = RULE.Frame(nplay, RULE.kind_test)
    a_, b_ = Fn.select("RB", "rush_att", (2023,)), Fn.select("RB", "rush_att", (2023,), played_only=False)
    ck("rule: fitting and scoring select played rows only by default; the all-rows figure is the played_only=False "
       "selection", a_ and len(b_) > len(a_) and all(Fn.rows[i]["y_cond_played"] == "1" for i in a_))
    # the longest rush: no rush is a longest of 0, never a dropped row
    Fl = RULE.Frame([{"y_long": "", "y_rush_att": "0"}, {"y_long": "", "y_rush_att": "3"}, {"y_long": "17", "y_rush_att": "3"}],
                    RULE.kind_test)
    ck("rule: a back with no rush scores the longest rush as 0 (every rung a miss); a missing longest WITH rushes drops",
       RULE.outcome(Fl, 0, "long_rush") == 0.0 and RULE.outcome(Fl, 1, "long_rush") is None
       and RULE.outcome(Fl, 2, "long_rush") == 17.0)
    co_s = RULE.coefs_for(parts, disp, fr_s)
    i0 = RULE.Frame.select(Fr, "RB", "rush_yd", (2026,))[0]
    d_rule = RULE.market_dist(co_s, Fr, i0, "RB", "rush_yd")
    import nfl_matchup as M
    feats = Fr.feats(i0)
    saved = R.features
    try:
        R.features = lambda blocks, lg=None, ext=None, names=None: dict(feats)
        env = {"S": _FakeSnap(), "tb": {"T": _hook_fixture()[3]["tb"]["KC"], "U": _hook_fixture()[3]["tb"]["BUF"]},
               "lg": {}, "sl2g": {"9": 9}, "extra": {}}
        mk_ = [{"stat": "rush_yd", "lines": []}]
        base = Fr.base(i0)
        M._rush_player(co_s, env, 2026, 7, "RB", "9", "T", "U", base, {"margin": 0.0}, {}, {}, "x", mk_, None, None, 1.0)
    finally:
        R.features = saved
    bl = {ln["k"]: ln["p"] for ln in mk_[0]["lines"]}
    ck("rule: REPLAY -- the walk's scored distribution and the board's price for the same row and coefs agree at "
       "every threshold (fit -> export -> board)", bl and all(abs(bl[k] - d_rule.sf_ge(k)) < 1e-4 for k in bl), bl)
    # the export writes exactly the groups it is given, with their status and records
    savedp, savedd = RULE.parts_for, RULE.dispersion
    try:
        RULE.parts_for = lambda train, plumb=False, rec=None: parts
        RULE.dispersion = lambda F_, train: disp
        co_x = RULE.build_coefs(Fr, {"RB": st_shape, "QB": {}}, "provisional", (2023, 2024, 2025), dead,
                                {"prereg_sha256": "p", "validate_sha256": "v", "check2026_sha256": "c"})
    finally:
        RULE.parts_for, RULE.dispersion = savedp, savedd
    ck("rule: the export ships exactly the passed groups, each 'provisional', with its records; nothing for a market "
       "with no group", co_x["shipped"] == {"RB:rush_att": [{"group": "SPD", "status": "provisional"},
                                                             {"group": "SHAPE", "status": "provisional"}],
                                            "RB:rush_yd": [{"group": "SPD", "status": "provisional"},
                                                           {"group": "SHAPE", "status": "provisional"}]}
       and co_x["records"]["validate_sha256"] == "v" and co_x["prereg_sha256"] == "p" and R.uses_compound(co_x, "RB:rush_yd")
       and R.ship_status(co_x, "RB:rec") == "0")
    # a walk on the synthetic frame accepts the true effect and rejects noise (plumbing of the gate end to end)
    sel = lambda F_, p_, m_, played_only=True: [i for i in F_.select(p_, m_, (2026,), played_only) if p_ == "RB"]
    states, steps = RULE.walk_markets(Fr, (2023, 2024, 2025), Fr, sel, parts, disp, dead,
                                      lambda p_, m_: (["SPD", "DEC"] if (p_, m_) == ("RB", "rush_att") else []),
                                      RULE.gate_2025, "synthetic")
    sp_ = {s_["group"]: s_ for s_ in steps["RB"]["rush_att"]}
    ck("rule: end to end on synthetic data the walk accepts the generating term (SPD) and rejects pure noise (DEC)",
       states["RB"]["rush_att"] == ["SPD"] and sp_["SPD"]["d"] < 0 and np.isfinite(sp_["DEC"]["d"]), sp_)


def selection_checks(ck, RULE):
    """The row selection reads no restricted column (the Opus data confirmation's N2): a back gets a dataset row,
    and act_played 1, from PFR offensive snaps alone, so the universes read Sleeper's line and "played" reads
    Sleeper's gp or a pbp rush / target / dropback -- never act_played, act_any_snap or a PFR column."""
    import research.nfl_rush_model_frame as FRM
    import nfl_rush_profiles as RP
    import random as _rnd
    sch = json.load(open(os.path.join(REPO, "research", "data", "nfl_rush", "schema.json"), encoding="utf-8"))
    bad = {}
    for kind in ("rb", "qb"):
        tags = {e["name"]: e.get("source_tag") for e in sch[kind]}
        for c in FRM.SELECTION_COLS:
            t = tags.get(c)
            if t is None and c in FRM.OPTIONAL_OUTCOME_COLS and kind == "rb":
                continue                                     # act_db: QB rows only
            if t not in ("sleeper", "pbp") or RP.restricted(t):
                bad[(kind, c)] = t
    ck("every column that selects a row (SELECTION_COLS) carries an unrestricted Sleeper / pbp tag in the dataset's "
       "own schema, and the PFR-touched played columns are not among them",
       not bad and not set(FRM.SELECTION_COLS) & {"act_played", "act_any_snap", "played"}
       and all(RP.restricted(t) for t in {e.get("source_tag") for k in ("rb", "qb") for e in sch[k]
                                          if e["name"] in ("act_played", "act_any_snap")}), bad)
    base = {"sl_act_gp": "", "act_rush": "0", "act_tgt": "0", "act_db": "0"}
    pfr = {"act_played": "1", "act_any_snap": "1", "act_pfr_snaps": "41", "act_pfr_team_snaps": "66", "played": "1"}
    y0, y1 = FRM.outcomes_of(base), FRM.outcomes_of(dict(base, **pfr))
    y2 = FRM.outcomes_of(dict(base, act_tgt="1"))
    y3 = FRM.outcomes_of(dict(base, sl_act_gp="1", act_played="0"))
    ck("played (y_cond_played) moves with Sleeper's gp or a pbp touch only: PFR snaps, act_played and act_any_snap "
       "never make a row played",
       y0["y_cond_played"] == 0 and y1["y_cond_played"] == 0 and y2["y_cond_played"] == 1 and y3["y_cond_played"] == 1
       and y1["y_played"] == "1")
    ck("the rule's played() never reads y_played (act_played): it follows y_cond_played",
       not RULE.played({"y_played": "1", "y_cond_played": "0"}) and RULE.played({"y_played": "0", "y_cond_played": "1"}))
    # the frame's selection: the same rows whatever the PFR-touched columns say
    rows = []
    rng = _rnd.Random(5)
    for i in range(60):
        r = {"pos": "RB" if i % 3 else "QB", "season": "2024", "game_id": f"g{i}", "gsis": str(i),
             "sl_rush_att": str(rng.choice([0, 2, 4.9, 5, 9])), "sl_rush_yd": str(rng.choice([0, 20, 40])),
             "sl_rec_tgt": str(rng.choice([0, 1, 2, 3])), "sl_rec": str(rng.choice([0, 1.5])),
             "sl_rush_td": str(rng.choice([0, 0.1, 0.3])), "sl_rec_td": "0.05", "sl_pass_att": str(rng.choice([0, 25])),
             "y_cond_played": str(rng.choice([0, 1])), "y_played": str(rng.choice([0, 1]))}
        rows.append(r)
    flip = [dict(r, y_played=str(1 - int(r["y_played"]))) for r in rows]
    Fa, Fb = RULE.Frame(rows, RULE.kind_revised), RULE.Frame(flip, RULE.kind_revised)
    same = all(Fa.select(p_, m_, (2024,)) == Fb.select(p_, m_, (2024,)) for p_ in ("RB", "QB")
               for m_ in RULE.WALK[p_])
    proj = all(Fa.base(i)["rush_att"] > 0 or Fa.base(i)["rec_tgt"] > 0 for m_ in RULE.WALK["RB"]
               for i in Fa.select("RB", m_, (2024,), played_only=False))
    ck("the frame's selection is the same whatever act_played says, and every RB row it selects has projected carries "
       "or targets (what gives a back a dataset row whatever PFR says)", same and proj)
    ds = open(os.path.join(REPO, "research", "nfl_rush_dataset.py"), encoding="utf-8").read()
    ck("the dataset gives every back Sleeper projects carries or targets a row (week_rows' Sleeper set), so a universe "
       "row never exists through PFR alone",
       '((rec["proj"].get("rush_att") or 0) > 0 or (rec["proj"].get("rec_tgt") or 0) > 0)' in ds
       and "for pid in sorted(backs | sl_backs):" in ds
       and not R.in_universe("RB", "td", {"rush_td": 0.3, "rec_td": 0.1, "rush_att": 0, "rec_tgt": 0}))


def hook_more_checks(ck, co):
    """The hook's merge and its snapshot: the first model's facts are never overwritten, and the board's
    snapshot is built on the availability view (the frame's), once, with Kalshi's extra series."""
    import types
    import fnmatch
    import nfl_matchup as M
    import nfl_rush_data as RD
    import nfl_rush_profiles as RP
    import kalshi_nfl as KN
    import research.nfl_rush_model_frame as FRM
    rows, idx, extra, env = _hook_fixture()
    first = {"carry_share": {"value": 0.123, "unit": "share", "kind": "observed", "n": 9, "basis": "the first model's"}}
    saved = (M.production_ctx, M.role_block)
    try:
        M.production_ctx = lambda season: None
        M.role_block = lambda f, pos: copy.deepcopy(first) if pos == "RB" else {}
        b = M.board(2026, 7, rows=rows, kalshi_idx=idx, schedule=[], ctx=None, lines={}, with_market=True,
                    now=1793000000.0, rush_coefs=_hook_coefs(co), rush_env=env)
    finally:
        M.production_ctx, M.role_block = saved
    role = next(p_ for p_ in b["players"] if p_["pos"] == "RB")["role"]
    ck("hook: the rushing facts never overwrite a first-model fact of the same name (carry share kept), and add theirs",
       role.get("carry_share", {}).get("value") == 0.123 and "snap_share" in role, sorted(role))
    # the snapshot: availability view -> RP.build, once, with the extra series
    rec, notes = [], []
    keep = (RD.load_aux, RD.aux_matches, RD.aux_complete, R.availability_view, RP.build, R.team_blocks,
            R.league_means, M._ids, KN.extra_props)
    ctx = types.SimpleNamespace(stores={2025: {"teams": ["KC", "BUF"]}, 2026: {"teams": ["KC", "BUF"]}}, pos={},
                                save_history=False)
    try:
        RD.load_aux = lambda s_: {"season": s_}
        RD.aux_matches = lambda a_, st_: True
        RD.aux_complete = lambda a_: True
        R.availability_view = lambda st_, ax_, season: ("VIEW-STORES", "VIEW-AUX")
        RP.build = lambda vs, va, s_, w_, pos: rec.append((vs, va, s_, w_)) or "SNAP"
        R.team_blocks = lambda S_, teams: {t: {} for t in teams}
        R.league_means = lambda tb: {}
        M._ids = lambda: {"sleeper2gsis": {"1": 2}}
        KN.extra_props = lambda: {"from": "extra_props"}
        out = {"notes": notes}
        e1 = M._rush_env({"models": {}}, 2026, 7, ctx, True, out)
        e0 = M._rush_env({"models": {}}, 2026, 7, ctx, False, out)
        RD.aux_complete = lambda a_: False
        e_bad = M._rush_env({"models": {}}, 2026, 7, ctx, True, out)
    finally:
        (RD.load_aux, RD.aux_matches, RD.aux_complete, R.availability_view, RP.build, R.team_blocks,
         R.league_means, M._ids, KN.extra_props) = keep
    ck("hook: the board's rushing snapshot is built on the availability view (the season's own participation "
       "masked, as the frame), once a board, with Kalshi's extra series only when the market is read; an "
       "incomplete aux turns the hook off and says so",
       rec[:1] == [("VIEW-STORES", "VIEW-AUX", 2026, 7)] and e1["extra"] == {"from": "extra_props"} and e1["S"] == "SNAP"
       and e0["extra"] == {} and e_bad is None and any("rushing model off" in n_ for n_ in notes), (rec, notes))
    # the frozen clock and the capture globs
    ck("the frozen line must be strictly BEFORE kickoff (a capture at kickoff is not pre-game)",
       FRM.choose_frozen([(1000.0, {}, "s", "T", None)], 1000.0) is None
       and FRM.choose_frozen([(999.5, {}, "s", "T", None)], 1000.0) is not None)
    ck("the capture globs read any week number (two digits included)",
       all(fnmatch.fnmatch(os.path.basename(g_).replace("*", "11"), os.path.basename(g_)) and g_.endswith("proj_2026_*.json")
           for g_ in FRM.CAPTURE_GLOBS) and len(FRM.CAPTURE_GLOBS) >= 2)
    # no derived market without its components' SHAPE
    co_m = dict(co, shipped={"RB:rush_yd": [{"group": "SPD", "status": "provisional"}],
                             "RB:rec_yd": [{"group": "SHAPE", "status": "provisional"}]},
                models={"RB:mean_rush_yd": {"terms": [], "cov": None, "clamp": 1.0}})
    pm = R.plan(co_m, "RB", {"rush_att": 15.0, "rush_yd": 66.0, "rec_tgt": 3.0, "rec": 2.5, "rec_yd": 18.0}, {})
    ck("plan: with the rushing yards in the mean form (no SHAPE) neither the longest rush nor rush + rec is priced",
       pm["markets"]["long_rush"]["form"] == "none" and pm["markets"]["rush_rec_yd"]["form"] == "none"
       and pm["markets"]["rush_yd"]["form"] == "mean", {k: v["form"] for k, v in pm["markets"].items()})


# ---- the golden run: the four commands end to end on a synthetic frame pair ---------------------------
GOLD_REG_UTC = "2025-12-01T00:00:00Z"            # before every synthetic window game but a few week-7 ones
GOLD_PRE_REG_KICK = 1764000000.0                 # 2025-11-24: kicked off BEFORE the registration
GOLD_KICK0 = 1767225600.0                        # 2026-01-01: the window's games start here


# The golden figures, from this tree's first run (2026-10-24; seed 11): pinned so that a change to which
# frame, seasons, baseline, rows, groups or rule a command reads changes them.
_GA = {"RB": {"rush_att": ["SPD"], "rush_yd": ["SPD"], "rec": ["SPD", "SHAPE"], "rec_yd": [], "td": []},
       "QB": {"rush_att": [], "rush_yd": [], "td": []}}
_GP = {"RB": {"rush_att": ["SPD"], "rush_yd": ["SPD"], "rec": ["SHAPE"], "rec_yd": [], "td": []},
       "QB": {"rush_att": [], "rush_yd": [], "td": []}}
_GC = {f"RB:{m}": {"rows": 197, "games": 76} for m in ("rush_att", "rush_yd", "rec", "rec_yd", "td")}
_GC.update({f"QB:{m}": {"rows": 0, "games": 0} for m in ("rush_att", "rush_yd", "td")})
GOLDEN = {"accepted": _GA, "provisional": _GP, "d_spd": -0.27153781946978833, "se_spd": 0.10193944840034151,
          "window_games": 76, "counts": _GC,
          "kept": {"RB": {"rush_att": [], "rush_yd": [], "rec": ["SHAPE"], "rec_yd": [], "td": []},
                   "QB": {"rush_att": [], "rush_yd": [], "td": []}}}


def _gold_rows(seed=11):
    """Synthetic RB rows, no real outcome anywhere: val (2023-2025), test (2023-2026 weeks 1-6) and the
    prospective frame (2026 weeks 7-12). Carries move with spd_rb_car_pg (b 0.25 a unit) EXCEPT in the
    prospective weeks; yards per carry with spd_rb_ypc (b 0.15) everywhere; receptions with
    spd_rbrec_tgt (b 0.40), REVERSED in 2026 weeks 1-6 (so the 2026 check drops them); dec_margin is
    pure noise.
    Sleeper's REVISED line (sl_*) is the honest one for 2023-2025 and garbage (1.0: out of every
    universe) for 2026; the FROZEN line (fz_*) the reverse, so a command reading the wrong baseline
    scores no rows. 8% of rows did not play (y_cond_played 0, outcomes 0)."""
    import numpy as np
    rng = np.random.RandomState(seed)
    rng2 = np.random.RandomState(seed + 1)       # its own stream: the outcomes' draws stay as pinned
    val, test, prosp, sched = [], [], [], {}
    for s in (2023, 2024, 2025, 2026):
        weeks = range(1, 7) if s == 2026 else range(1, 6)
        if s == 2026:
            weeks = list(weeks) + list(range(7, 13))
        for w in weeks:
            for g in range(16 if (s == 2026 and w >= 7) else 9):
                gid = f"{s}_{w}_{g}"
                kick = (GOLD_PRE_REG_KICK + g if (s == 2026 and w == 7 and g < 3) else
                        GOLD_KICK0 + (w - 7) * 7 * 86400 + g * 3600 if (s == 2026 and w >= 7) else
                        1.6e9 + (s - 2023) * 3.2e7 + w * 6.05e5 + g * 3600)
                if s == 2026 and w >= 7:
                    sched[gid] = (w, kick)
                for j in range(3 if (s == 2026 and w >= 7) else 2):
                    cl, tl = rng.uniform(6, 18), rng.uniform(2, 6)
                    x, z, u, e_ = rng.normal(), rng.normal(), rng.normal(), rng.normal()
                    eff = 0.0 if (s == 2026 and w >= 7) else 0.25
                    mu_c = cl * math.exp(eff * x)
                    car = int(rng.poisson(rng.gamma(10.0, mu_c / 10.0)))
                    ypc = 4.3 * math.exp(0.15 * e_)
                    yds = round(ypc * car + rng.normal(0, 6 * math.sqrt(car)) if car else 0.0, 1)
                    zr = -0.4 if (s == 2026 and w < 7) else 0.4
                    rec = int(rng.poisson(0.75 * tl * math.exp(zr * z)))
                    ryd = round(7 * rec + rng.normal(0, 5 * math.sqrt(rec)) if rec else 0.0, 1)
                    played = rng.uniform() > 0.08
                    honest = {"rush_att": cl, "rush_yd": 4.3 * cl, "rush_td": 0.3, "rec_tgt": tl, "rec": 0.75 * tl,
                              "rec_yd": 5.2 * tl, "rec_td": 0.1, "pass_att": 0.0}
                    junk = {k: 1.0 for k in honest}
                    sl, fz = (junk, honest) if s == 2026 else (honest, junk)
                    r = {"season": str(s), "week": str(w), "game_id": gid, "team": f"T{g}", "opp": f"U{g}",
                         "gsis": f"p{(g * 2 + j + w) % 13}", "pos": "RB", "margin": "0", "kick_ts": f"{kick:.0f}",
                         "fz_src": "" if (s == 2026 and (w in (4, 5) or (w >= 7 and j == 1 and g == 15)
                                                         or (w == 11 and g == 14))) else
                         ("props" if s == 2026 else ""), "fz_lead_h": "2.0" if s == 2026 else ""}
                    for k_, v_ in sl.items():
                        r[f"sl_{k_}"] = f"{v_:.4f}"
                    for k_, v_ in fz.items():
                        r[f"fz_{k_}"] = f"{v_:.4f}"
                    for n in R.FEATURES:
                        r[f"f_{n}"] = "0"
                    r["f_spd_rb_car_pg"], r["f_spd_rbrec_tgt"], r["f_dec_margin"] = f"{x:.4f}", f"{z:.4f}", f"{u:.4f}"
                    r["f_spd_rb_ypc"] = f"{e_:.4f}"
                    r["f_cfp_ol_out"] = f"{int(rng2.poisson(1.0))}"       # noise; constant in the TEST frame's 2023-24
                    y = {"y_cond_played": "1" if played else "0", "y_gp": "1" if played else "0",
                         "y_pbp_play": "1" if played else "0", "y_rush_att": str(car if played else 0),
                         "y_rush_yd": str(yds if played else 0), "y_rec": str(rec if played else 0),
                         "y_rec_yd": str(ryd if played else 0), "y_rush_td": str(int(rng.poisson(0.3)) if played else 0),
                         "y_rec_td": "0", "y_long": str(min(99, int(2.5 * yds / car))) if (played and car) else ""}
                    r.update(y)
                    if s == 2026 and w >= 7:
                        prosp.append(r)
                        continue
                    t_ = dict(r)
                    if s < 2026:
                        t_["f_spd_rb_car_pg"] = f"{1.1 * x:.4f}"      # the test frame (pooled k) differs a little
                        if s < 2025:
                            t_["f_cfp_ol_out"] = "1"     # live in the test frame only through 2025: a dead-feature
                        val.append(r)                    # rule that read the test frame's 2023-24 would drop it
                    test.append(t_)
    return val, test, prosp, sched


def _gold_write(path, rows):
    import csv as _csv
    import gzip as _gz
    with _gz.open(path, "wt", newline="") as fh:
        w = _csv.DictWriter(fh, list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow(r)


def golden_checks(ck, F, RULE):
    """The four commands, end to end, on a synthetic frame pair (the re-checks' R1 / R-M1): which frame,
    seasons, baseline and rows each step reads, which groups each walks and keeps, what each record
    and coefs.json say, and the refusals around them. Pinned by definition and by the golden figures."""
    import types
    td = tempfile.mkdtemp()
    try:
        _golden(ck, F, RULE, td, types)
    finally:
        import shutil
        shutil.rmtree(td, ignore_errors=True)


def _golden(ck, F, RULE, td, types):
    val, test, prosp, sched = _gold_rows()
    pv, pt, pp = (os.path.join(td, n) for n in ("val.csv.gz", "test.csv.gz", "prosp.csv.gz"))
    _gold_write(pv, val)
    _gold_write(pt, test)
    _gold_write(pp, prosp)
    out = os.path.join(td, "model")
    os.makedirs(out)
    code = {f: F._sha(os.path.join(REPO, f)) for f in F.REG_CODE}
    rec = {"sha256": "S" * 64, "utc": GOLD_REG_UTC, "code": code, "frames": {"val": F._sha(pv), "test": F._sha(pt)},
           "frames_meta": {"test": {"inputs": {}}}}
    json.dump(rec, open(os.path.join(out, "prereg.json"), "w"))
    weeks = list(range(7, 13))
    meta = {"code": dict(code), "k": {"mode": "pooled"}, "seasons": [2026], "frozen_only": True,
            "built_utc": "2026-03-02T00:00:00Z", "dataset": "research/data/nfl_rush_model/prospective_dataset",
            "weeks": weeks, "inputs": {f"research/data/nfl_rush_model/inputs/nfl_props_2026_w{w:02d}.json": "x"
                                       for w in weeks if w != 8},
            "box_files": [{"week": w, "mtime_utc": "2026-03-01T00:00:00Z"} for w in weeks]}
    dsd = os.path.join(out, "prospective_dataset")
    os.makedirs(dsd)
    json.dump({"code": {f: code[f] for f in ("nfl_rush_data.py", "research/nfl_matchup_dataset.py")},
               "inputs": {"deep:nfl_matchup_2026": "m26", "deep:nfl_rush_aux_2026": "x26", "deep:nfl_matchup_ids": "ids",
                          "deep:nfl_matchup_2018": "only the dataset read it"},
               "by_season": {"2026": {"weeks": weeks}}},
              open(os.path.join(dsd, "meta.json"), "w"))
    stores_rec = {"prereg_sha256": rec["sha256"], "code": {"nfl_matchup_data.py": code["nfl_matchup_data.py"]},
                  "stores": {"deep:nfl_matchup_2026": "m26", "deep:nfl_rush_aux_2026": "x26", "deep:nfl_matchup_ids": "ids"}}
    meta.update(dataset_realpath=dsd, dataset_meta_sha256=F._sha(os.path.join(dsd, "meta.json")))
    meta["inputs"].update({"deep:nfl_rush_prospective_stores": "sv", "deep:nfl_matchup_2026": "m26",
                           "deep:nfl_rush_aux_2026": "x26", "deep:nfl_matchup_ids": "ids"})
    json.dump(meta, open(pp + ".meta.json", "w"))
    json.dump({"prereg_sha256": rec["sha256"],
               "weeks": {"8": {"utc": "2026-02-20T00:00:00Z", "note": "no props log for week 8"}}},
              open(os.path.join(out, "capture_missing.json"), "w"))
    parts = {k: v for k, v in _coefs().items() if k in ("schema", "kernels", "alpha", "kneels", "qb_prior")}
    calls = {"parts": [], "disp": [], "stores": [], "parts_rec": [], "wiring": {}}
    saved = (F.MODEL_OUT, F.prereg_status, F.in_registered_tree, RULE.parts_for, RULE.dispersion, RULE.verify_stores,
             RULE.schedule, RULE.load, RULE._provenance)
    real_disp, real_load = RULE.dispersion, RULE.load

    def tagged_load(path, seasons, kind_of, plumb=False, seed=7):
        Fr = real_load(path, seasons, kind_of, plumb=plumb, seed=seed)
        Fr.path = os.path.basename(path)
        return Fr
    try:
        F.MODEL_OUT = out
        F.prereg_status = lambda: (True, "registered")
        F.in_registered_tree = lambda: {"prereg_sha256": rec["sha256"]}
        RULE.parts_for = lambda train, plumb=False, rec=None: (calls["parts"].append(tuple(train)),
                                                               calls["parts_rec"].append(rec)) and parts
        RULE.dispersion = lambda F_, train: calls["disp"].append((getattr(F_, "path", "?"), tuple(train))) or \
            real_disp(F_, train)
        RULE.verify_stores = lambda rec_, seasons, plumb: calls["stores"].append(tuple(seasons))
        RULE.schedule = lambda season: dict(sched)
        RULE.load = tagged_load
        real_prov = RULE._provenance
        RULE._provenance = lambda m_, r_=None: real_prov(m_, r_)[:2] + (stores_rec, "sv", {})
        A = lambda **kw: types.SimpleNamespace(**dict(dict(plumb=False, frame=pt, frame_val=pv, frame_prosp=pp,
                                                             plumb_reg_ts=None, force=False), **kw))

        def refused(fn, a_, text):
            try:
                fn(a_)
            except SystemExit as e:
                return text in str(e)
            return False
        # out of order: check2026 before validate
        ooo = refused(RULE.cmd_check2026, A(), "does not exist yet")
        RULE.cmd_validate(A())
        v = json.load(open(os.path.join(out, "validate.json")))
        RULE.cmd_check2026(A())
        c = json.load(open(os.path.join(out, "check2026.json")))
        # a validate.json that no longer hashes to check2026's link: the chain is broken
        vp = os.path.join(out, "validate.json")
        vtxt = open(vp).read()
        open(vp, "w").write(vtxt + "\n")
        broken = refused(RULE.cmd_export, A(), "record chain is broken")
        open(vp, "w").write(vtxt)
        RULE.cmd_export(A())
        e = json.load(open(os.path.join(out, "coefs.json")))
        e_chain = R.record_chain(e, out)
        twice = refused(RULE.cmd_export, A(), "exists")
        # validate re-run on a frame that is not the registered validation frame
        val_frame = refused(RULE.cmd_validate, A(frame_val=pp), "not the registered one")
        # a prospective frame built by other code bytes
        pbad = os.path.join(td, "prosp_bad.csv.gz")
        _gold_write(pbad, prosp)
        json.dump(dict(meta, code=dict(meta["code"], **{"nfl_rush.py": "0" * 64})), open(pbad + ".meta.json", "w"))
        bad_build = refused(RULE.cmd_prospective, A(frame_prosp=pbad), "not built by the registered code")
        # stores the registered tree's build does not vouch for
        RULE._provenance = lambda m_, r_=None: real_prov(m_, r_)[:2] + (dict(stores_rec, prereg_sha256="Y" * 64), "sv", {})
        bad_prov = refused(RULE.cmd_prospective, A(), "not built by the registered code under this registration")
        RULE._provenance = lambda m_, r_=None: real_prov(m_, r_)[:2] + (stores_rec, "sv", {})
        # a coefs.json that is not this check2026's export
        json.dump(dict(e, records=dict(e["records"], check2026_sha256="0" * 64)), open(os.path.join(out, "coefs.json"), "w"))
        not_export = refused(RULE.cmd_prospective, A(), "is not this check2026's export")
        json.dump(e, open(os.path.join(out, "coefs.json"), "w"))
        # the store has played only weeks 7-9: an early run refuses
        RULE.schedule = lambda season: {k_: v_ for k_, v_ in sched.items() if v_[0] <= 9}
        early = refused(RULE.cmd_prospective, A(), "have not been played")
        RULE.schedule = lambda season: {k_: v_ for k_, v_ in sched.items() if v_[0] <= 11}
        open_wk = refused(RULE.cmd_prospective, A(), "not closed")
        RULE.schedule = lambda season: dict(sched)
        # a capture_missing.json recorded under another registration excuses no week
        cmp_ = os.path.join(out, "capture_missing.json")
        cm_good = json.load(open(cmp_))
        json.dump(dict(cm_good, prereg_sha256="Q" * 64), open(cmp_, "w"))
        cm_other = refused(RULE.cmd_prospective, A(), "capture_missing.json was not recorded under this registration")
        json.dump(cm_good, open(cmp_, "w"))
        # a week whose props log IS an input but which holds no frozen universe row (Fable's m5): refused
        pm5 = os.path.join(td, "prosp_m5.csv.gz")
        _gold_write(pm5, [dict(r, fz_src="") if r["week"] == "9" else r for r in prosp])
        json.dump(meta, open(pm5 + ".meta.json", "w"))
        no_frozen = refused(RULE.cmd_prospective, A(frame_prosp=pm5), "window week 9 holds no frozen universe row")
        # the window's last week reaches window_weeks as the window found it (an EXTENDED window included)
        real_wg, real_ww, real_pr, real_cr = RULE.window_games, RULE.window_weeks, RULE.provenance_refusal, RULE.coverage_refusal
        try:
            RULE.window_games = lambda kicks, reg_ts, week_of: (12, sorted(kicks), None)
            RULE.window_weeks = lambda sched_, reg_ts, lw: (calls["wiring"].setdefault("ext_lw", lw), [], None, "stop")[1:]
            ext = refused(RULE.cmd_prospective, A(), "stop")
        finally:
            RULE.window_games, RULE.window_weeks = real_wg, real_ww

        def ww_spy(sched_, reg_ts, lw):
            r_ = real_ww(sched_, reg_ts, lw)
            calls["wiring"].update(lw=lw, wk_exp=r_[0])
            return r_

        def pr_spy(*a_, **kw):
            calls["wiring"]["prov_weeks"] = kw.get("weeks")
            return real_pr(*a_, **kw)

        def cr_spy(*a_, **kw):
            calls["wiring"].update(now=a_[3], frozen_weeks=kw.get("frozen_weeks"), t=time.time())
            return real_cr(*a_, **kw)
        RULE.window_weeks, RULE.provenance_refusal, RULE.coverage_refusal = ww_spy, pr_spy, cr_spy
        try:
            RULE.cmd_prospective(A())
        finally:
            RULE.window_weeks, RULE.provenance_refusal, RULE.coverage_refusal = real_ww, real_pr, real_cr
        lk = json.load(open(os.path.join(out, "prospective_lock.json")))
        fin = json.load(open(os.path.join(out, "coefs.json")))
        fin_chain = R.record_chain(fin, out)
        after = refused(RULE.cmd_export, A(), "export cannot follow")
        again = refused(RULE.cmd_prospective, A(), "refused")
        fin2 = json.load(open(os.path.join(out, "coefs.json")))
        loaded = R.load_coefs(os.path.join(out, "coefs.json"))
        # the lock is written once: even with the export's coefs.json put back, a second run refuses at the lock
        json.dump(e, open(os.path.join(out, "coefs.json"), "w"))
        relock = refused(RULE.cmd_prospective, A(), "prospective_lock.json exists")
        # the prospective walk decides with keep_prospective (d < -1 SE), whatever it is: made to keep
        # nothing, a forced re-run keeps nothing
        keep_saved = RULE.keep_prospective
        RULE.keep_prospective = lambda d, se: False
        try:
            RULE.cmd_prospective(A(force=True))
        finally:
            RULE.keep_prospective = keep_saved
        lk_none = json.load(open(os.path.join(out, "prospective_lock.json")))
        F.in_registered_tree = lambda: None
        live = refused(RULE.cmd_prospective, A(force=True), "registered code's stored copies")
        # a validate.json written under another registration is not believed
        v_bad = dict(v, prereg_sha256="X" * 64)
        json.dump(v_bad, open(os.path.join(out, "validate.json"), "w"))
        forged = refused(RULE.cmd_check2026, A(force=True), "not written under this registration")
        json.dump(v, open(os.path.join(out, "validate.json"), "w"))
        other = refused(RULE.cmd_check2026, A(force=True, frame=pv), "not the registered one")
    finally:
        (F.MODEL_OUT, F.prereg_status, F.in_registered_tree, RULE.parts_for, RULE.dispersion, RULE.verify_stores,
         RULE.schedule, RULE.load, RULE._provenance) = saved
    acc, prov, kept = v["accepted"], c["provisional"], lk["kept"]
    n26 = sum(1 for r in test if r["season"] == "2026" and r["fz_src"] and r["y_cond_played"] == "1")
    c_step = c["steps"]["RB"]["rush_att"][0]
    step = {s_["group"]: s_ for s_ in v["steps"]["RB"]["rush_att"]}
    n25 = sum(1 for r in val if r["season"] == "2025" and r["y_cond_played"] == "1")
    gold = GOLDEN
    ck("golden: validate fits 2023-24 on the VALIDATION frame (parts, dispersion), scores 2025 on Sleeper's revised "
       "line, played rows only; the stores re-verified per step",
       calls["parts"][0] == (2023, 2024) and calls["disp"][0] == ("val.csv.gz", (2023, 2024))
       and v["fit"] == [2023, 2024] and v["eval"] == 2025 and step["SPD"]["n"] == n25
       and calls["stores"][1] == (2023, 2024) and set(calls["stores"][:1] + calls["stores"][2:]) == {(2023, 2024, 2025)},
       (calls, step["SPD"]["n"], n25))
    ck("golden: dead features read each frame on its own training seasons (cfp_ol_out, constant only in the test "
       "frame's 2023-24, stays live and its CFP group is walked)",
       "cfp_ol_out" not in v["dead"]["RB"] and "CFP" in [s_["group"] for s_ in v["steps"]["RB"]["rush_att"]],
       v["dead"]["RB"][:5])
    ck("golden: the 2025 walk accepts the planted carries and receptions terms, rejects the noise group, and its "
       "figures are the pinned ones", "SPD" in acc["RB"]["rush_att"] and "SPD" in acc["RB"]["rec"]
       and "DEC" not in acc["RB"]["rush_att"] and acc == gold.get("accepted")
       and abs(step["SPD"]["d"] - gold.get("d_spd", 9.0)) < 1e-6 and abs(step["SPD"]["se"] - gold.get("se_spd", 9.0)) < 1e-6
       and step["SPD"]["se_game"] != step["SPD"]["se_player"], (acc, step["SPD"]["d"], step["SPD"]["se"]))
    ck("golden: check2026 refits on 2023-25 (test frame), walks only the 2025-accepted groups on the FROZEN 2026 "
       "rows, keeps the pinned set and DROPS the receptions term the 2026 rows reverse",
       calls["parts"][1] == (2023, 2024, 2025) and calls["disp"][1] == ("test.csv.gz", (2023, 2024, 2025))
       and c["fit"] == [2023, 2024, 2025] and prov == gold.get("provisional") and "SPD" not in prov["RB"]["rec"]
       and c_step["n"] == n26 and c_step["group"] == "SPD"
       and all(set(prov[p_].get(m_) or []) <= set(acc[p_].get(m_) or []) for p_ in prov for m_ in prov[p_])
       and all(s_["group"] in (acc["RB"].get(m_) or []) for m_, st_ in c["steps"]["RB"].items() for s_ in st_), prov)
    ck("golden: export ships EXACTLY check2026's set as provisional, refit on 2023-25, written once, its records "
       "chaining to the decision records beside it",
       e["stage"] == "provisional" and e["fit_seasons"] == [2023, 2024, 2025] and calls["parts"][2] == (2023, 2024, 2025)
       and {k: [g["group"] for g in gs] for k, gs in e["shipped"].items()}
       == {f"RB:{m_}": gs for m_, gs in prov["RB"].items() if gs}
       and all(g["status"] == "provisional" for gs in e["shipped"].values() for g in gs) and e_chain is None and twice,
       (e["shipped"], e_chain))
    ck("golden: prospective counts the window from the registration (pre-registration week-7 games out), through "
       "week 11 (week 12 out), frozen rows only (a week-11 game with none is not counted), keeps on d < -1 SE, and "
       "ships only what it kept",
       lk["window_last_week"] == 11 and lk["window_games"] == gold.get("window_games") and lk["counts"] == gold.get("counts")
       and kept == gold.get("kept") and "SPD" not in (kept["RB"].get("rush_att") or [])
       and "SHAPE" in (kept["RB"].get("rec") or []) and calls["parts"][3] == (2023, 2024, 2025)
       and {k: [g["group"] for g in gs] for k, gs in fin["shipped"].items()}
       == {f"RB:{m_}": gs for m_, gs in kept["RB"].items() if gs}
       and all(g["status"] == "shipped" for gs in fin["shipped"].values() for g in gs) and fin["stage"] == "shipped"
       and fin_chain is None and loaded is not None, (lk["window_games"], lk["counts"], kept))
    ck("golden: the refusals around the chain -- out of order, a second export, an export after the lock (coefs.json "
       "untouched), a second prospective, a run outside the registered tree, a forged validate.json, the wrong frame, "
       "the wrong validation frame, a prospective frame from other code, a second lock, a store that has played only "
       "weeks 7-9, a week 11 not closed, a broken record chain, a coefs.json that is not this check2026's export, "
       "stores the registered build does not vouch for, a capture_missing.json of another registration",
       ooo and twice and after and again and fin2 == fin and live and forged and other and val_frame and bad_build
       and relock and early and open_wk and broken and not_export and bad_prov and cm_other,
       (ooo, twice, after, again, live, forged, other, val_frame, bad_build, relock, early, open_wk, broken, not_export,
        bad_prov, cm_other))
    ck("golden: the prospective walk keeps exactly what keep_prospective keeps (made to keep nothing, a forced re-run "
       "kept nothing, and says it was forced)",
       all(not gs for p_ in lk_none["kept"].values() for gs in p_.values()) and lk_none.get("forced_rerun") is True)
    wi = calls["wiring"]
    fz_weeks = {int(r["week"]) for r in prosp if r["fz_src"] and r["pos"] in RULE.WALK and any(
        R.in_universe(r["pos"], m_, {f: float(r.get(f"fz_{f}") or 0.0) for f in ("rush_att", "rush_yd", "rush_td",
                                                                                  "rec_tgt", "rec", "rec_yd", "rec_td",
                                                                                  "pass_att")})
        for m_ in RULE.WALK[r["pos"]])}
    ck("golden: prospective's wiring -- window_weeks gets the window's last week as window_games found it (an extended "
       "window's 12, never 11), provenance_refusal the window's weeks (the dataset must hold each: B1), and "
       "coverage_refusal this run's clock and the weeks holding a frozen universe row (m5)",
       ext and wi.get("ext_lw") == 12 and wi.get("lw") == lk["window_last_week"] and wi.get("wk_exp")
       and wi.get("prov_weeks") == wi.get("wk_exp") and abs((wi.get("now") or 0) - (wi.get("t") or 0)) < 5
       and wi.get("frozen_weeks") == fz_weeks and 9 in fz_weeks and no_frozen,
       {k_: v_ for k_, v_ in wi.items() if k_ != "t"} | {"fz_weeks": sorted(fz_weeks), "no_frozen": no_frozen, "ext": ext})
    ck("golden: every registered command reads the distribution parts with THIS registration's record (its "
       "positions, never the ids pickle in the folder)",
       len(calls["parts_rec"]) >= 4 and all((r_ or {}).get("sha256") == rec["sha256"] for r_ in calls["parts_rec"]),
       [None if r_ is None else (r_ or {}).get("sha256") for r_ in calls["parts_rec"]])


class _P:
    """A distribution whose P(X >= k) is fixed: for pinning the scoring core."""
    def __init__(self, p):
        self.p = p

    def sf_ge(self, k):
        return self.p


def core_checks(ck, F, RULE):
    """The scoring core and the fit, pinned to hand numbers (the re-checks' X05 / X06 / F01-F03 / F06 / G05 /
    WI2 / WI5 / F07 / D02 / F15 / B2 / H2 / PL3)."""
    import numpy as np
    one = RULE.clustered([1.0, -0.5, 2.0], ["a", "a", "a"])
    ck("a comparison with ONE cluster has no SE (NaN: the gates reject it), never SE 0",
       abs(one[0] - 2.5 / 3) < 1e-12 and not math.isfinite(one[1]) and not RULE.accept_2025(-1.0, one[1], 1)
       and not RULE.keep_prospective(-1.0, one[1]))
    ck("no fitted input is pending: the data layer landed every one (PENDING_PATHS empty; a pending one blocks the "
       "registration)", R.PENDING_PATHS == frozenset())
    ll = RULE.row_ll
    ck("row_ll: a hit is y >= k (y == k hits), probabilities clipped to [1e-6, 1 - 1e-6], no distribution -> None",
       abs(ll(_P(0.7), 10, (10,)) + math.log(0.7)) < 1e-12 and abs(ll(_P(0.7), 9, (10,)) + math.log(0.3)) < 1e-12
       and abs(ll(_P(1.0), 5, (10,)) - (-math.log(1e-6))) < 1e-9 and abs(ll(_P(0.0), 10, (10,)) - (-math.log(1e-6))) < 1e-9
       and abs(ll(_P(0.5), 0, (1, 2)) - 2 * math.log(2)) < 1e-12 and ll(None, 3, (1,)) is None and RULE.CLIP == 1e-6)
    big = {"terms": [{"feature": "spd_rb_ypc", "b": 5.0, "mean": 0.0, "sd": 1.0}], "cov": None, "clamp": 1.0}
    ck("the log factor is clamped to [-1, 1] (CLAMP 1)", RULE.CLAMP == 1.0 and R.linear(big, {"spd_rb_ypc": 9.0})[0] == 1.0
       and R.linear(big, {"spd_rb_ypc": -9.0})[0] == -1.0)
    rng = np.random.RandomState(7)
    Z = rng.normal(size=(40, 5))
    seas = np.array([2023] * 20 + [2024] * 20)
    off = np.log(rng.uniform(3, 9, size=40))
    y = np.round(np.exp(off + 0.08 * Z[:, 0]) * rng.gamma(2.0, 0.5, 40))
    y[0] = -2.0
    b, cov, lam = RULE.fit_ridge(Z, y, off, seas)
    want = [0.13717978949580675, 0.09793644448320765, -0.015601183228346364, 0.07537636472995482, -0.09819518827125147]
    ck("the ridge fit, pinned: every column penalised, lambda by leave-one-season-out deviance over the fitting "
       "seasons (100 here), outcomes clipped at 0, no intercept",
       lam == 100.0 and np.allclose(b, want, atol=1e-8) and cov.shape == (5, 5) and RULE.LAMBDAS[0] == 1.0
       and RULE.LAMBDAS[-1] == 1e6 and len(RULE.LAMBDAS) == 10, (b.tolist(), lam))
    g4, g8 = RULE.gate_2025("RB", "rush_att"), RULE.gate_2025("RB", "rush_yd")
    ck("the 2025 floor counts EVERY threshold of the market (4 for carries: 0.002; 8 for rushing yards: 0.004)",
       not g4(-0.0019, 1e-7) and g4(-0.00201, 1e-7) and not g8(-0.0039, 1e-7) and g8(-0.00401, 1e-7))
    kicks = {f"g{i}": 1000.0 + i for i in range(50)}
    wk = {f"g{i}": 7 + min(4, i // 8) for i in range(40)}
    wk.update({f"g{i}": 12 for i in range(40, 50)})
    lw, gm, nt = RULE.window_games(kicks, 999.0, wk)
    lw2, gm2, _n2 = RULE.window_games(kicks, 1000.0, wk)
    ck("the window: exactly 40 games by week 11 needs no extension; a game kicking off AT the registration time is "
       "out (strictly after); week 12 is outside; the constants are 11 / 13 / 40",
       lw == 11 and len(gm) == 40 and nt is None and "g0" not in gm2 and lw2 == 12 and len(gm2) == 49
       and RULE.LAST_WEEK == 11 and RULE.EXT_LAST_WEEK == 13 and RULE.MIN_WINDOW_GAMES == 40, (lw, len(gm), lw2, len(gm2)))
    mk = lambda s_, v: {"pos": "RB", "season": str(s_), "f_spd_rb_ypc": v[0], "f_spd_rb_stuff": v[1], "f_spd_rb_expl10": v[2]}
    fa = RULE.Frame([mk(2023, ("1", "1", "3")), mk(2024, ("1", "2", "4")), mk(2025, ("5", "3", "1"))], RULE.kind_revised)
    fb = RULE.Frame([mk(2023, ("1", "1", "2")), mk(2024, ("2", "1", "2")), mk(2025, ("3", "4", "2"))], RULE.kind_revised)
    dd = RULE.dead_features([fa, fb], {0: (2023, 2024), 1: (2023, 2024, 2025)})["RB"]
    ck("dead features read EITHER frame on its own training seasons: constant in the validation frame's 2023-24 -> "
       "dead; constant in the test frame's 2023-25 -> dead; constant only in the test frame's 2023-24 -> live",
       "spd_rb_ypc" in dd and "spd_rb_expl10" in dd and "spd_rb_stuff" not in dd, sorted(dd & {"spd_rb_ypc", "spd_rb_stuff", "spd_rb_expl10"}))
    co_d = dict(_coefs(), shipped={"RB:rush_att": [{"group": "DEC", "status": "provisional"}]},
                models={"RB:mean_rush_att": {"terms": [], "cov": None, "clamp": 1.0}})
    pdx = R.plan(co_d, "RB", {"rush_att": 15.0, "rush_yd": 66.0}, {})
    ck("plan: a market whose DEC shipped without SHAPE is priced in the mean form, never as the compound",
       pdx["markets"]["rush_att"]["form"] == "mean" and pdx["markets"]["rush_yd"]["form"] == "rung1")
    # rows one side cannot price drop out of a comparison (the derived market before its components' SHAPE)
    rows_l = [dict(r, y_long="12") for r in _synth_rows()]
    Fl = RULE.Frame(rows_l, RULE.kind_test)
    co_n = dict(_coefs(), dispersion={"RB": {"rush_att": {"alpha": 0.1}, "rush_yd": {"cv": 0.7}}}, shipped={}, models={})
    co_s = dict(co_n, shipped={"RB:rush_att": [{"group": "SHAPE", "status": "provisional"}],
                               "RB:rush_yd": [{"group": "SHAPE", "status": "provisional"}]})
    il = Fl.select("RB", "long_rush", (2023,))
    dl, sl_, ex_l = RULE.compare(co_n, co_s, Fl, il, "RB", "long_rush")
    ck("compare drops the rows only one side prices (the longest rush before SHAPE), counting them",
       ex_l["n"] == 0 and ex_l["n_dropped"] == len(il) and len(il) > 10 and not math.isfinite(dl), ex_l)
    # prereg_status reads the DATA files too
    with tempfile.TemporaryDirectory() as t2:
        import hashlib
        recp = {"sha256": hashlib.sha256(F.rule_block().encode("utf-8")).hexdigest(),
                "code": {f: F._sha(os.path.join(REPO, f)) for f in F.REG_CODE},
                "data": {f: F._sha(os.path.join(REPO, f)) for f in F.REG_DATA}}
        saved = F.MODEL_OUT
        try:
            F.MODEL_OUT = t2
            json.dump(recp, open(os.path.join(t2, "prereg.json"), "w"))
            ok1 = F.prereg_status()
            recp["data"][F.REG_DATA[0]] = "0" * 64
            json.dump(recp, open(os.path.join(t2, "prereg.json"), "w"))
            ok2 = F.prereg_status()
        finally:
            F.MODEL_OUT = saved
    ck("prereg_status holds with every file at its registered bytes and refuses a changed DATA file", ok1[0]
       and not ok2[0] and F.REG_DATA[0] in ok2[1], (ok1, ok2))
    # the plumbing donors replace a held row's WHOLE outcome block
    with tempfile.TemporaryDirectory() as t3:
        rows = [{"season": "2023", "pos": "RB", "game_id": f"a{i}", "gsis": f"p{i}", "y_rush_att": str(10 + i),
                 "y_cond_played": "1", "y_gp": "1"} for i in range(5)]
        rows += [{"season": "2025", "pos": "RB", "game_id": f"b{i}", "gsis": f"q{i}", "y_rush_att": "999",
                  "y_cond_played": "0", "y_gp": "999"} for i in range(5)]
        pth = os.path.join(t3, "f.csv.gz")
        _gold_write(pth, rows)
        Fp = RULE.load(pth, (2023, 2025), RULE.kind_revised, plumb=True)
    donors = {(r["y_rush_att"], r["y_cond_played"], r["y_gp"]) for r in rows[:5]}
    held = [r for r in Fp.rows if r["season"] == "2025"]
    ck("the plumbing run replaces a held row's whole outcome block (played included) with a 2023 donor's, never its own",
       len(held) == 5 and all((r["y_rush_att"], r["y_cond_played"], r["y_gp"]) in donors and r.get("plumb") == "1"
                              for r in held) and "999" not in json.dumps(Fp.rows))


def _fake_block(mo, src, F):
    """A stored rule block in `mo`/prereg_code (its sha256 returned: the registration's) and a live report in
    `src` whose block was edited after the hash."""
    import hashlib
    blk = f"{F.RULE_A}\nthe block as hashed\n{F.RULE_B}"
    open(os.path.join(mo, "prereg_code", "rule_block.md.txt"), "w", encoding="utf-8").write(blk)
    rp = os.path.join(src, "research", "reports", "nfl_rush_model.md")
    os.makedirs(os.path.dirname(rp), exist_ok=True)
    open(rp, "w", encoding="utf-8").write(f"# report\n\n{F.RULE_A}\nthe block EDITED after the hash\n{F.RULE_B}\n\n## after\n")
    return hashlib.sha256(blk.encode("utf-8")).hexdigest()


def chain_checks(ck, F, RULE):
    """coefs.json is believed only when its statuses trace to the decision records beside it (the re-check's R4);
    the registered tree holds the STORED code; the frame's gzip is reproducible; the coverage refusals."""
    import research.nfl_rush_model_frame as FRM
    with tempfile.TemporaryDirectory() as t:
        files = {n: os.path.join(t, n) for n in ("prereg.json", "validate.json", "check2026.json", "prospective_lock.json")}
        body = {"check2026.json": {"provisional": {"RB": {"rec": ["SHAPE"], "rush_att": []}, "QB": {}}},
                "prospective_lock.json": {"kept": {"RB": {"rec": ["SHAPE"]}, "QB": {"rush_yd": []}}}}
        for n, p_ in files.items():
            json.dump(body.get(n, {"n": n}), open(p_, "w"))
        sh = {n: F._sha(p_) for n, p_ in files.items()}
        recs = {"prereg_json_sha256": sh["prereg.json"], "validate_sha256": sh["validate.json"],
                "check2026_sha256": sh["check2026.json"]}
        prov = {"shipped": {"RB:rec": [{"group": "SHAPE", "status": "provisional"}]}, "records": dict(recs)}
        ship = {"shipped": {"RB:rec": [{"group": "SHAPE", "status": "shipped"}]},
                "records": dict(recs, prospective_sha256=sh["prospective_lock.json"])}
        os.rename(files["prospective_lock.json"], files["prospective_lock.json"] + ".away")   # the provisional stage
        a1 = R.record_chain(prov, t)
        a5 = R.record_chain({"shipped": {}}, t)
        # the confirmation's forged file: the genuine records' hashes, a group none of them kept
        forged = R.record_chain({"shipped": {"RB:rec": [{"group": "SHAPE", "status": "provisional"}],
                                             "RB:rush_att": [{"group": "SPD", "status": "provisional"}]},
                                 "records": dict(recs)}, t)
        json.dump(dict(_coefs(), **prov), open(os.path.join(t, "coefs.json"), "w"))
        l1 = R.load_coefs(os.path.join(t, "coefs.json"))
        os.rename(files["prospective_lock.json"] + ".away", files["prospective_lock.json"])     # the shipped stage
        a2 = R.record_chain(ship, t)
        a3 = R.record_chain(dict(ship, records=recs), t)
        a4 = R.record_chain({"shipped": {"RB:rec": [{"group": "SHAPE", "status": "provisional"}],
                                         "RB:rush_att": [{"group": "SPD", "status": "shipped"}]},
                             "records": dict(recs, prospective_sha256=sh["prospective_lock.json"])}, t)
        forged_s = R.record_chain({"shipped": {"RB:rec": [{"group": "SHAPE", "status": "shipped"},
                                                          {"group": "SPD", "status": "shipped"}]},
                                   "records": dict(recs, prospective_sha256=sh["prospective_lock.json"])}, t)
        beside = R.record_chain(prov, t)          # the provisional file restored beside the lock
        l3 = R.load_coefs(os.path.join(t, "coefs.json"))
        json.dump({"n": "tampered"}, open(files["check2026.json"], "w"))
        a6 = R.record_chain(prov, t)
        l2 = R.load_coefs(os.path.join(t, "coefs.json"))
    ck("the board believes coefs.json only when its records chain to the files beside it: provisional needs prereg / "
       "validate / check2026, shipped the lock too; a mix, a missing record or a changed file refuses (nothing at all)",
       a1 is None and a2 is None and a3 and a4 and "mixes" in a4 and a5 is None and l1 is not None and a6
       and l2 is None, (a1, a2, a3, a4, a5, a6))
    ck("a provisional coefs.json restored beside the prospective lock (one revert on the auto-deploying branch) is "
       "refused: the test may have reverted its groups", beside and "beside the prospective lock" in beside
       and l3 is None, beside)
    ck("the board believes coefs.json only when its shipped groups ARE the decision record's set: a file citing the "
       "genuine hashes but listing an unkept group refuses, provisional (check2026) and shipped (the lock) alike",
       forged and "decision record's set" in forged and forged_s and "decision record's set" in forged_s,
       (forged, forged_s))
    # the registered tree: the STORED copies, not the live ones
    with tempfile.TemporaryDirectory() as t:
        src, dest, mo = os.path.join(t, "src"), os.path.join(t, "dest"), os.path.join(t, "mo")
        os.makedirs(os.path.join(mo, "prereg_code"))
        os.makedirs(os.path.join(src, "research"))
        code = {}
        for f in F.REG_CODE:
            os.makedirs(os.path.dirname(os.path.join(src, f)), exist_ok=True)
            open(os.path.join(src, f), "w").write(f"live {f}\n")
            stp = os.path.join(mo, "prereg_code", f.replace("/", "__") + ".txt")
            open(stp, "w").write(f"stored {f}\n")
            code[f] = F._sha(stp)
        tsha = _fake_block(mo, src, F)
        json.dump({"sha256": tsha, "code": code}, open(os.path.join(mo, "prereg.json"), "w"))
        saved = F.MODEL_OUT
        try:
            F.MODEL_OUT = mo
            F.registered_tree(dest, src=src)
            got = all(open(os.path.join(dest, f)).read() == f"stored {f}\n" for f in F.REG_CODE)
            mark = json.load(open(os.path.join(dest, F.REGISTERED_MARK)))
            rep = open(os.path.join(dest, "research", "reports", "nfl_rush_model.md"), encoding="utf-8").read()
            block_restored = "the block as hashed" in rep and "EDITED" not in rep and rep.startswith("# report") \
                and rep.endswith("## after\n")
            c0 = os.path.join(mo, "prereg_code", F.REG_CODE[0].replace("/", "__") + ".txt")
            keep0 = open(c0).read()
            open(c0, "w").write("edited\n")
            try:
                F.registered_tree(os.path.join(t, "dest2"), src=src)
                tampered = False
            except SystemExit:
                tampered = True
            open(c0, "w").write(keep0)
            open(os.path.join(mo, "prereg_code", "rule_block.md.txt"), "a").write("an edit\n")
            try:
                F.registered_tree(os.path.join(t, "dest3"), src=src)
                block_tampered = None
            except SystemExit as e:
                block_tampered = str(e)
        finally:
            F.MODEL_OUT = saved
    ck("the registered tree holds the code bytes STORED at registration (never the live files), marks itself with "
       "the registration, and refuses a stored copy that no longer hashes", got and mark["prereg_sha256"] == tsha
       and tampered)
    ck("the registered tree's report carries the rule block STORED at registration, the rest of the report the live "
       "one (an edit inside the live block after the hash cannot stop the decisive run: Sonnet's F5e); a stored block "
       "that no longer hashes refuses", block_restored and block_tampered and "stored rule block" in block_tampered,
       block_tampered)
    # the frame's gzip is reproducible: no mtime, no file name in its header
    with tempfile.TemporaryDirectory() as t:
        rows = [{"season": "2023", "x": "1"}, {"season": "2024", "x": "2"}]
        FRM.write_frame(os.path.join(t, "a.csv.gz"), rows)
        time.sleep(1.1)
        FRM.write_frame(os.path.join(t, "bb.csv.gz"), rows)
        same = F._sha(os.path.join(t, "a.csv.gz")) == F._sha(os.path.join(t, "bb.csv.gz"))
        back = F.read_frame(os.path.join(t, "a.csv.gz"), (2023, 2024))
    ck("the frame's gzip has a zero mtime and no name, so a rebuild of the same rows has the same sha256", same
       and [r["x"] for r in back] == ["1", "2"])
    # the prospective coverage
    meta = {"weeks": [7, 8, 9], "inputs": {"research/data/nfl_rush_model/inputs/nfl_props_2026_w07.json": "x",
                                           "research/data/nfl_rush_model/inputs/nfl_props_2026_w09.json": "x"},
            "box_files": [{"week": w, "mtime_utc": "2026-12-10T00:00:00Z"} for w in (7, 8, 9)]}
    last = RULE._utc_ts("2026-12-01T00:00:00Z")
    now = RULE._utc_ts("2026-12-20T00:00:00Z")
    miss_ok = {"8": {"utc": "2026-12-05T00:00:00Z"}}
    cr = RULE.coverage_refusal
    ck("prospective coverage: every window week in the build, each with a props log or a 'capture missing' entry "
       "recorded BEFORE the box, a box file fetched 48 h after the last game, and the run itself 48 h after it",
       cr(meta, [7, 8, 9], last, now, miss_ok) is None and "omits" in (cr(meta, [7, 8, 9, 10], last, now, miss_ok) or "")
       and "no props log" in (cr(meta, [7, 8, 9], last, now, {}) or "")
       and "no props log" in (cr(meta, [7, 8, 9], last, now, {"8": {"utc": "2026-12-11T00:00:00Z"}}) or "")
       and "48 hours" in (cr(dict(meta, box_files=[{"week": w, "mtime_utc": "2026-12-02T00:00:00Z"} for w in (7, 8, 9)]),
                             [7, 8, 9], last, now, miss_ok) or "")
       and "no Sleeper box" in (cr(dict(meta, box_files=meta["box_files"][:2]), [7, 8, 9], last, now, miss_ok) or "")
       and "under 48 hours ago" in (cr(meta, [7, 8, 9], last, last + 3600, miss_ok) or ""))
    src = open(os.path.join(REPO, "research", "nfl_rush_dataset.py"), encoding="utf-8").read()
    ck("the prospective dataset is built to its OWN directory (--out), never over the registered research/data/nfl_rush/",
       'out = args.out or OUT' in src and 'run_build(stores, auxes, pos, ids, sleeper, out, max_week=_week_cap(args))' in src
       and 'os.path.join(out, "schema.json")' in src and 'os.path.join(out, "meta.json")' in src)


def tree_checks(ck, F, RULE):
    """The confirmation's C-M1 / C-M2 / C-m2: the window's weeks are a range that must be played and closed in
    the store; the registered tree's records live in ONE folder outside it; capture-missing is bound to the
    registration and runs in the tree; the index's and the extra series' stats never collide."""
    import types
    import kalshi_nfl as KN
    reg = 1000.0
    g = lambda weeks, extra=(): dict({f"g{w}_{i}": (w, reg + w * 100 + i) for w in weeks for i in range(4)},
                                     **{f"x{j}": v for j, v in enumerate(extra)})
    ww = RULE.window_weeks
    stop9 = ww(g(range(7, 10)), reg, 11)
    empty = ww({}, reg, 11)
    first11 = ww(dict(g(range(7, 11)), w11=(11, reg + 1100)), reg, 11)
    closed = ww(dict(g(range(7, 12)), w12=(12, reg + 1200)), reg, 11)
    pre = ww(dict(g(range(7, 12)), w12=(12, reg + 1200), early=(6, reg - 50.0)), reg, 11)
    ck("the window's weeks are EVERY week from the first after the registration through week 11, each played in "
       "the store and week 11 CLOSED (a week-12 game held): a store stopping at week 9, an empty one, and one holding "
       "week 11's first game only all refuse; the closed window passes with week 11's last kickoff",
       stop9[2] and "not been played" in stop9[2] and empty[2] and not empty[0] and first11[2]
       and "not closed" in first11[2] and closed[2] is None and closed[0] == [7, 8, 9, 10, 11]
       and closed[1] == reg + 1103 and pre[0] == [7, 8, 9, 10, 11] and pre[2] is None, (stop9, empty, first11, closed))
    ck("coverage refuses an empty window and an unknown last kickoff",
       "no week" in (RULE.coverage_refusal({}, [], 1.0, 2.0, {}) or "")
       and "unknown" in (RULE.coverage_refusal({"weeks": [7]}, [7], None, 2.0, {}) or ""))
    # the records' folder of a registered tree
    with tempfile.TemporaryDirectory() as t:
        repo, x = os.path.join(t, "tree"), os.path.join(t, "repo_records")
        os.makedirs(repo)
        os.makedirs(x)
        d0 = F.resolve_model_out(repo, None)
        json.dump({"model_out": x, "prereg_sha256": "Q" * 64}, open(os.path.join(repo, F.REGISTERED_MARK), "w"))
        d1, d2 = F.resolve_model_out(repo, None), F.resolve_model_out(repo, x)
        try:
            F.resolve_model_out(repo, os.path.join(t, "elsewhere"))
            other = False
        except SystemExit:
            other = True
        json.dump({"sha256": "Q" * 64}, open(os.path.join(x, "prereg.json"), "w"))
        inside = os.path.join(repo, "research", "data", "nfl_rush_model")
        os.makedirs(inside)
        json.dump({"sha256": "Q" * 64}, open(os.path.join(inside, "prereg.json"), "w"))
        ok_tree = F.in_registered_tree(repo, x)
        no_disagree = F.in_registered_tree(repo, inside)
        json.dump({"sha256": "R" * 64}, open(os.path.join(x, "prereg.json"), "w"))
        other_reg = F.in_registered_tree(repo, x)
        json.dump({"sha256": "Q" * 64}, open(os.path.join(x, "prereg.json"), "w"))
        json.dump({"model_out": inside, "prereg_sha256": "Q" * 64}, open(os.path.join(repo, F.REGISTERED_MARK), "w"))
        no_inside = F.in_registered_tree(repo, inside)
    ck("in a registered tree the records' folder is the marker's (outside the tree): no variable reads it, an agreeing "
       "one too, a disagreeing NFLR_MODEL_OUT refuses; the tree is accepted only with the marker's folder, never a "
       "folder inside the tree, and only when the marker names THIS registration",
       d0.endswith(os.path.join("tree", "research", "data", "nfl_rush_model")) and d1 == x and d2 == x and other
       and ok_tree and no_disagree is None and no_inside is None and other_reg is None,
       (d0, d1, ok_tree, no_disagree, no_inside, other_reg))
    # two trees share ONE lock
    with tempfile.TemporaryDirectory() as t:
        src, mo = os.path.join(t, "src"), os.path.join(t, "mo")
        os.makedirs(os.path.join(mo, "prereg_code"))
        code = {}
        for f in F.REG_CODE:
            os.makedirs(os.path.dirname(os.path.join(src, f)), exist_ok=True)
            open(os.path.join(src, f), "w").write("live\n")
            stp = os.path.join(mo, "prereg_code", f.replace("/", "__") + ".txt")
            open(stp, "w").write(f"stored {f}\n")
            code[f] = F._sha(stp)
        json.dump({"sha256": _fake_block(mo, src, F), "code": code}, open(os.path.join(mo, "prereg.json"), "w"))
        saved = F.MODEL_OUT
        try:
            F.MODEL_OUT = mo
            t1, t2 = F.registered_tree(os.path.join(t, "t1"), src=src), F.registered_tree(os.path.join(t, "t2"), src=src)
            r1, r2 = F.resolve_model_out(t1, None), F.resolve_model_out(t2, None)
            RULE.write_once(os.path.join(r1, "prospective_lock.json"), {"kept": {}}, False)
            try:
                RULE.write_once(os.path.join(r2, "prospective_lock.json"), {"kept": {}}, False)
                second = False
            except SystemExit:
                second = True
            import shutil as _sh
            nest = os.path.join(t, "t4")
            mo4 = os.path.join(nest, "research", "data", "nfl_rush_model")
            _sh.copytree(mo, mo4)
            F.MODEL_OUT = mo4
            try:
                F.registered_tree(nest, src=src)
                nested = False
            except SystemExit as e:
                nested = "inside the registered tree" in str(e)
        finally:
            F.MODEL_OUT = saved
    ck("two registered trees share ONE records folder, so a second tree's prospective finds the first's lock; a "
       "tree may not hold its own records folder", r1 == r2 == os.path.abspath(mo) and second and nested, (r1, r2))
    # capture-missing: in the tree only, bound to the registration
    with tempfile.TemporaryDirectory() as t:
        saved = (F.MODEL_OUT, F.prereg_status, F.in_registered_tree)
        try:
            F.MODEL_OUT = t
            json.dump({"sha256": "C" * 64}, open(os.path.join(t, "prereg.json"), "w"))
            F.prereg_status = lambda: (True, "registered")
            A = lambda w: types.SimpleNamespace(week=w, note="no props log")
            F.in_registered_tree = lambda: None
            try:
                RULE.cmd_capture_missing(A(9))
                out_tree = False
            except SystemExit:
                out_tree = True
            F.in_registered_tree = lambda: {"prereg_sha256": "C" * 64}
            RULE.cmd_capture_missing(A(9))
            got = json.load(open(os.path.join(t, "capture_missing.json")))
            try:
                RULE.cmd_capture_missing(A(9))
                twice = False
            except SystemExit:
                twice = True
            json.dump(dict(got, prereg_sha256="Z" * 64), open(os.path.join(t, "capture_missing.json"), "w"))
            try:
                RULE.cmd_capture_missing(A(10))
                foreign = False
            except SystemExit:
                foreign = True
        finally:
            F.MODEL_OUT, F.prereg_status, F.in_registered_tree = saved
    ck("capture-missing runs only in the registered tree, records a week once under THIS registration, and refuses a "
       "file from another registration", out_tree and got["prereg_sha256"] == "C" * 64 and "9" in got["weeks"]
       and twice and foreign)
    ck("the index's prop stats and the extra rushing series' never collide (the merge order cannot matter)",
       not set(KN.EXTRA_PROP_SERIES.values()) & set(KN._PROP_SERIES.values())
       and set(KN.EXTRA_PROP_SERIES.values()) == {"rush_att", "rush_rec_yd", "long_rush"})


def round4_checks(ck, F, RULE):
    """Rule round 4 (the Sonnet, Opus and Fable final confirmations): the window's edges, the tree's destination
    and printed procedure, a coefs.json shipping a subset, the module record, the grid-wrap read, the bands'
    scale, the rescale floor and Q6's stated power."""
    import numpy as np
    reg = 1000.0
    ww = RULE.window_weeks
    g = lambda weeks: {f"g{w}_{i}": (w, reg + w * 100 + i) for w in weeks for i in range(4)}
    late = ww(g(range(12, 15)), reg, 11)
    at = ww(dict(g(range(8, 13)), at_reg=(7, reg)), reg, 11)
    ck("window_weeks gives a REASON when the window holds no week (its first game after week lw), and a game AT the "
       "registration second is not in the window (strictly after)",
       late[0] == [] and late[2] and "no window week" in late[2] and at[0] == [8, 9, 10, 11] and at[2] is None,
       (late, at))
    # a window week whose every game kicked off BEFORE the registration is not played in the window (a postponed
    # earlier game after the registration starts it): the weeks played are counted from the games after the
    # registration only (Sonnet's WW12 counted the whole schedule)
    pre9 = ww(dict(g([8, 10, 11, 12]), w9a=(9, reg - 20.0), w9b=(9, reg - 10.0)), reg, 11)
    ck("window_weeks counts a window week as played only by a game AFTER the registration (week 9 played entirely "
       "before it, week 8's game after it: refused)", pre9[2] and "[9]" in pre9[2] and "not been played" in pre9[2],
       pre9)
    # the registered tree: a non-empty destination refuses; the printed procedure names ABSOLUTE paths
    with tempfile.TemporaryDirectory() as t:
        mo, src, dest = os.path.join(t, "mo"), os.path.join(t, "src"), os.path.join(t, "dest")
        os.makedirs(os.path.join(mo, "prereg_code"))
        os.makedirs(dest)
        open(os.path.join(dest, "stale.txt"), "w").write("left from another tree\n")
        json.dump({"sha256": "N" * 64, "code": {}}, open(os.path.join(mo, "prereg.json"), "w"))
        saved = F.MODEL_OUT
        try:
            F.MODEL_OUT = mo
            try:
                F.registered_tree(dest, src=src)
                nonempty = None
            except SystemExit as e:
                nonempty = str(e)
            proc = F.procedure(dest)
        finally:
            F.MODEL_OUT = saved
    ds_line = [ln for ln in proc if "research.nfl_rush_dataset build" in ln]
    ck("registered-tree refuses a destination that is not empty (stale files would survive the copy) and prints the "
       "procedure with the records' folder by ABSOLUTE path, the dataset built with --all-weeks",
       nonempty and "not empty" in nonempty and ds_line and "--all-weeks" in ds_line[0]
       and f"--out {os.path.join(os.path.abspath(mo), 'prospective_dataset')}" in ds_line[0]
       and any("build-stores" in ln for ln in proc), (nonempty, proc))
    # in_registered_tree: a records folder OTHER than the marker's (outside the tree, same registration) is refused
    with tempfile.TemporaryDirectory() as t:
        tree_, mine_, other_ = (os.path.join(t, n) for n in ("tree", "records", "other_records"))
        for d_ in (tree_, mine_, other_):
            os.makedirs(d_)
        json.dump({"model_out": mine_, "prereg_sha256": "M" * 64}, open(os.path.join(tree_, F.REGISTERED_MARK), "w"))
        for d_ in (mine_, other_):
            json.dump({"sha256": "M" * 64}, open(os.path.join(d_, "prereg.json"), "w"))
        it_mine, it_other = F.in_registered_tree(tree_, mine_), F.in_registered_tree(tree_, other_)
    ck("in_registered_tree accepts only the marker's records folder: another folder holding the same registration, "
       "outside the tree, is refused (Sonnet's IT2)", it_mine and it_other is None, (it_mine, it_other))
    # record_chain: a coefs.json shipping a SUBSET of the decision record's groups is refused too
    with tempfile.TemporaryDirectory() as t:
        files = {n: os.path.join(t, n) for n in ("prereg.json", "validate.json", "check2026.json")}
        for n, p_ in files.items():
            json.dump({"provisional": {"RB": {"rec": ["SHAPE", "SPD"]}, "QB": {}}} if n == "check2026.json" else {"n": n},
                      open(p_, "w"))
        recs = {"prereg_json_sha256": F._sha(files["prereg.json"]), "validate_sha256": F._sha(files["validate.json"]),
                "check2026_sha256": F._sha(files["check2026.json"])}
        sub = R.record_chain({"shipped": {"RB:rec": [{"group": "SHAPE", "status": "provisional"}]}, "records": recs}, t)
        full = R.record_chain({"shipped": {"RB:rec": [{"group": "SHAPE", "status": "provisional"},
                                                       {"group": "SPD", "status": "provisional"}]}, "records": recs}, t)
    ck("the board refuses a coefs.json that ships a SUBSET of the decision record's groups (a group dropped), and "
       "believes the exact set", sub and "decision record's set" in sub and full is None, (sub, full))
    # record_chain: a decision record that hashes to the coefs' records but is not JSON cannot be read, and
    # that refuses too (Opus's RC6 survived my round-4 run: a `return None` there believed the file)
    with tempfile.TemporaryDirectory() as t:
        files = {n: os.path.join(t, n) for n in ("prereg.json", "validate.json", "check2026.json")}
        for n, p_ in files.items():
            with open(p_, "w") as fh:
                fh.write("{not json" if n == "check2026.json" else json.dumps({"n": n}))
        recs = {"prereg_json_sha256": F._sha(files["prereg.json"]), "validate_sha256": F._sha(files["validate.json"]),
                "check2026_sha256": F._sha(files["check2026.json"])}
        unread = R.record_chain({"shipped": {"RB:rec": [{"group": "SHAPE", "status": "provisional"}]}, "records": recs}, t)
    ck("the board refuses a coefs.json whose decision record hashes to its records but cannot be read (not JSON) "
       "(Opus's RC6)", unread and "cannot be read" in unread, unread)
    mods = F.repo_modules()
    ck("the store build's module record covers every repository module the process loaded, research or not",
       {"nfl_rush.py", "nfl_matchup_data.py", "deep_cache.py", "prop_dist.py", "research/nfl_rush_model_fit.py"} <= set(mods),
       mods[:12])
    # the grid-wrap read (Fable's m1): the mean test's edge AND the direct read of the grid's top
    wf = R.wrap_flag
    ck("the grid-wrap flag: a grid mean off the analytic one by more than 1% of the mean (or 0.5 yards) refuses, "
       "under it does not; more than 1e-5 in the grid's top 32 values refuses, under it does not",
       wf(100.0, 101.2, 0.0) and not wf(100.0, 100.9, 0.0) and wf(0.0, 0.6, 0.0) and not wf(0.0, 0.4, 0.0)
       and wf(50.0, 50.0, 2e-5) and not wf(50.0, 50.0, 5e-6) and R.WRAP_TOP == 32 and R.WRAP_TAIL == 1e-5)
    kern = {"lo": -2, "p": [0.02, 0.05, 0.1, 0.13, 0.13, 0.11, 0.09, 0.07, 0.06, 0.05, 0.04, 0.03, 0.025, 0.02, 0.015, 0.012,
                           0.01, 0.008, 0.006, 0.005, 0.004, 0.003, 0.003, 0.002, 0.002, 0.002] + [0.001] * 10 + [0.0005] * 80
            + [0.002]}
    tot = sum(kern["p"])
    kern["p"] = [q / tot for q in kern["p"]]
    heavy = R.Compound([{"role": "rush", "count_mean": 25.0, "alpha": 0.05, "kernel": R.rescale(kern, 11.0)}])
    light = R.Compound([{"role": "rush", "count_mean": 18.0, "alpha": 0.05, "kernel": R.rescale(kern, 4.5)}])
    gm_ok = abs(heavy.grid_mean() - heavy.mean()) <= max(0.5, 0.01 * abs(heavy.mean()))
    ck("a heavy-tailed line whose tail reaches the grid's top is refused by the direct read even where the mean test "
       "passes it (25 carries at 11 yards a carry of a heavy kernel: 4.8e-4 in the top 32, the mean off by 1.0 of a "
       "2.75-yard allowance -- Fable's 30 carries at ypc x e is this case); a board line is not",
       heavy.wrapped and heavy.top_mass > R.WRAP_TAIL and gm_ok and not light.wrapped,
       (heavy.top_mass, heavy.grid_mean(), heavy.mean(), light.top_mass))
    # the bands: the penalised-information covariance, no dispersion factor (Q2, Fable's m4)
    rng = np.random.default_rng(11)
    Z = rng.normal(size=(300, 2))
    off = np.log(np.full(300, 6.0))
    y = rng.poisson(np.exp(off + Z @ np.array([0.2, -0.1]))).astype(float)
    seasons = np.array([2023] * 150 + [2024] * 150)
    b, cov, lam = RULE.fit_ridge(Z, y, off, seasons)
    mu = np.exp(np.clip(off + Z @ b, -30, 30))
    H = (Z.T * mu) @ Z + np.diag(np.full(2, lam) + 1e-8)
    ck("the bands' covariance is the inverse penalised information (Z'WZ + lambda), with no dispersion factor: Q2 says "
       "so", np.allclose(cov, np.linalg.inv(H), rtol=1e-6, atol=1e-12), (cov.tolist(), np.linalg.inv(H).tolist()))
    # the rescale floor (Fable's F07): a component's mean is matched exactly down to 5% of its kernel's
    m0 = R.kernel_mean(kern)
    lo_ = R.kernel_mean(R.rescale(kern, 0.1 * m0))
    floor_ = R.kernel_mean(R.rescale(kern, 0.01 * m0))
    ck("rescale matches the target mean exactly down to 5% of the kernel's (a low QB line's components), and floors "
       "the stretch there", abs(lo_ - 0.1 * m0) < 1e-9 and abs(floor_ - 0.05 * m0) < 1e-9, (lo_, floor_, m0))
    # the frame writer refuses an empty row list with its reason (B1: a capped dataset left none)
    with tempfile.TemporaryDirectory() as t:
        try:
            import research.nfl_rush_model_frame as FRM
            FRM.write_frame(os.path.join(t, "empty.csv.gz"), [])
            empty = None
        except SystemExit as e:
            empty = str(e)
        except Exception as e:      # noqa: BLE001 -- the old IndexError is the failure this pins
            empty = f"raised {type(e).__name__}"
    ck("the frame writer refuses no rows with a reason (never an IndexError on rows[0])",
       empty and "no rows" in empty and "--all-weeks" in empty, empty)
    # the distribution fit reads the positions it is HANDED (the registration's), never the ids pickle
    seen_pos = []

    class _Stop(Exception):
        pass

    saved_fd = (F.positions, F.season_plays)
    try:
        F.positions = lambda: ({1: "WR"}, {})
        F.season_plays = lambda season, pos: (seen_pos.append(dict(pos)), (_ for _ in ()).throw(_Stop()))
        try:
            F.fit_distributions((2023,), pos_sl=({1: "RB"}, {1: "s1"}))
        except _Stop:
            pass
    finally:
        F.positions, F.season_plays = saved_fd
    ck("fit_distributions reads the positions it is handed (pos_sl), never the ids pickle's",
       seen_pos == [{1: "RB"}], seen_pos)
    # the producers' wiring: build hands provenance_fields its own frozen_only (Sonnet's FR5: with False a prospective
    # frame would not record the stores record, and provenance would refuse every legitimate frame)
    fsrc = open(os.path.join(REPO, "research", "nfl_rush_model_frame.py"), encoding="utf-8").read()
    ck("the frame build hands provenance_fields its own frozen_only (one call, the build's argument)",
       fsrc.count("provenance_fields(") == 2 and "prov, prov_inputs = provenance_fields(ds_dir, frozen_only)" in fsrc)
    # ... and the CLI hands build_stores its --older seasons (Sonnet's FT27: the archive-lost path)
    seen_bs = []
    saved_bs = F.build_stores
    try:
        F.build_stores = lambda season, older=(): seen_bs.append((season, list(older))) or {"ok": True}
        F.main(["build-stores", "--older", "2019", "2024"])
        F.main(["build-stores"])
    finally:
        F.build_stores = saved_bs
    ck("build-stores' CLI hands --older to build_stores (and nothing without it)",
       seen_bs == [(2026, [2019, 2024]), (2026, [])], seen_bs)
    # Q6's stated power: the figures are keep_power()'s, at the games the block states
    blk = " ".join(F.rule_block().split())
    want = [f"{RULE.keep_power(272, 56, m):.2f}" for m in (1.0, 1.5, 2.0)] + \
           [f"{RULE.keep_power(272, 42, m):.2f}" for m in (1.0, 1.5, 2.0)]
    phi = lambda z: 0.5 * math.erfc(-z / math.sqrt(2.0))
    ck("Q6's power figures are keep_power() of the block's own game counts (272 in 2025; 56 and 42 in the window), and "
       "keep_power is P(d < -1 SE) for a true d of -2 SE x the multiple, the SE scaled by the square root of the games",
       all(w in blk for w in want) and "272" in blk
       and abs(RULE.keep_power(100, 25, 1.0) - phi(2.0 / 2.0 - 1.0)) < 1e-12
       and abs(RULE.keep_power(100, 100, 2.0) - phi(4.0 - 1.0)) < 1e-12, want)


def dataset_cli_checks(ck):
    """Fable's B1 (= the data review's D1): the dataset build's CLI stops at 2026 week 7 by default (the registered
    dataset's cap) and, asked, builds every week the store holds; its meta records the modules it ran (every
    repository module, research or not) and the store, aux and ids pickles it read (Opus's F1b)."""
    try:
        import guards_nfl_rush_data as GD
    except ImportError:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import guards_nfl_rush_data as GD
    import deep_cache
    import research.nfl_rush_dataset as DS
    import research.nfl_matchup_dataset as MD
    got, metas = {}, {}
    saved = (DS.load_all, MD.load_sleeper, deep_cache.CACHE_DIR)
    with tempfile.TemporaryDirectory() as d:
        st, ax, _p = GD._build(d, season=2026)
        for gm in st["games"]:
            gm["week"] += 3                                   # the fixture's weeks 1-6 become 4-9: past the cap
        wk = st["cols"]["week"]
        for i in range(len(wk)):
            wk[i] += 3
        deep = os.path.join(d, "deep")
        os.makedirs(deep)
        try:
            deep_cache.CACHE_DIR = deep
            deep_cache.save("nfl_matchup_2026", st)
            deep_cache.save("nfl_rush_aux_2026", ax)
            deep_cache.save("nfl_matchup_ids", {"ids": GD.IDS})
            DS.load_all = lambda seasons=None: ({2026: st}, {2026: ax}, GD.POS, GD.IDS)
            MD.load_sleeper = lambda path, seasons: ({}, None)
            for name, flags in (("default", []), ("all", ["--all-weeks"]), ("w8", ["--max-week", "8"])):
                out = os.path.join(d, name)
                DS.main(["build", "--sleeper", d, "--out", out] + flags)
                metas[name] = json.load(open(os.path.join(out, "meta.json")))
                got[name] = metas[name]["by_season"]["2026"]["weeks"]
            shas = {f"deep:{k}": DS.sha(os.path.join(deep, f"{k}.pkl"))
                    for k in ("nfl_matchup_2026", "nfl_rush_aux_2026", "nfl_matchup_ids")}
        finally:
            DS.load_all, MD.load_sleeper, deep_cache.CACHE_DIR = saved
    ck("B1: the dataset build stops at 2026 week 7 by default (the registered dataset's cap, TEST_MAX_WEEK) and builds "
       "every week the store holds with --all-weeks, or through --max-week N",
       got == {"default": [4, 5, 6, 7], "all": [4, 5, 6, 7, 8, 9], "w8": [4, 5, 6, 7, 8]} and DS.TEST_MAX_WEEK == {2026: 7},
       got)
    repo = os.path.abspath(REPO)
    loaded = {os.path.relpath(os.path.abspath(m.__file__), repo).replace(os.sep, "/")
              for m in list(sys.modules.values()) if getattr(m, "__file__", None)
              and os.path.abspath(m.__file__).startswith(repo + os.sep)}
    code = set(metas["all"].get("code") or {})
    ck("the dataset build's meta records the sha256 of EVERY repository module it ran -- research or not, the ledger "
       "and the store loader included -- and the versions",
       {"research/nfl_rush_dataset.py", "research/nfl_matchup_dataset.py", "nfl_rush_data.py", "nfl_rush_profiles.py",
        "nfl_matchup_data.py", "deep_cache.py", "errlog.py"} <= code <= loaded
       and metas["all"].get("versions", {}).get("python"), sorted(code)[:10])
    ck("the dataset build's meta records the store, aux and ids pickles it read, by sha256 (Opus's F1b: the frame's "
       "outcomes are these rows)", metas["all"].get("inputs") == shas, metas["all"].get("inputs"))


def register_checks(ck, F):
    """A registration records every deep pickle the frames read by sha256 AND content digest, the positions the
    distribution fit reads from the ids pickle, and writes the manifest of the 19 files to archive (Opus's F2,
    Sonnet's F1, Fable's m7)."""
    import types
    import deep_cache
    import research.nfl_rush_model_frame as FRM
    out = {}
    saved = (F.MODEL_OUT, deep_cache.CACHE_DIR)
    with tempfile.TemporaryDirectory() as t:
        deep, mo = os.path.join(t, "deep"), os.path.join(t, "model")
        os.makedirs(deep)
        os.makedirs(mo)
        try:
            deep_cache.CACHE_DIR = deep
            F.MODEL_OUT = mo
            deep_cache.save("nfl_matchup_2025", _fixture_store(2025))
            deep_cache.save("nfl_rush_aux_2025", {"schema": 2, "season": 2025, "built": 1.0, "store_built": 1.0,
                                                  "extras": {"aborted": [0]}})
            ids = {"schema": 2, "built": 3.0, "ids": {7: {"pos": "RB", "sleeper": "77"}, 8: {"pos": "QB", "sleeper": ""}},
                   "sources": {}}
            deep_cache.save("nfl_matchup_ids", ids)
            keys = ["deep:nfl_matchup_2025", "deep:nfl_rush_aux_2025", "deep:nfl_matchup_ids",
                    "research/data/nfl_rush/schema.json"]
            frames = {}
            for which in ("val", "test"):
                p_ = os.path.join(t, f"rush_frame_{which}.csv.gz")
                _gold_write(p_, [{"season": "2023", "week": "1", "pos": "RB", "game_id": "g", "f_spd_rb_car_pg": "0.1"},
                                 {"season": "2024", "week": "1", "pos": "RB", "game_id": "h", "f_spd_rb_car_pg": "0.2"}])
                json.dump({"rows": 2, "k": {}, "seasons": [2023, 2024], "participation": "prior",
                           "code": FRM.code_shas(), "paths_absent": [], "inputs": FRM.input_shas(keys)},
                          open(p_ + ".meta.json", "w"))
                frames[which] = p_
            F.cmd_register(types.SimpleNamespace(dry_run=False, go=F.GO, frame=frames["test"], frame_val=frames["val"]))
            rec = json.load(open(os.path.join(mo, "prereg.json")))
            man = json.load(open(os.path.join(mo, "prereg_manifest.json")))
            lines = [ln.split("  ") for ln in open(os.path.join(mo, "prereg_manifest.sha256")).read().splitlines()]
            out["stores"] = set(rec.get("stores") or {}) == set(keys[:3]) and all(
                rec["stores"][k]["sha256"] == FRM.input_shas([k])[k]
                and rec["stores"][k]["digest"] == F.deep_digest(k[5:])["digest"] for k in keys[:3])
            out["positions"] = (rec.get("ids_positions") or {}).get("map") == {"7": ["RB", "77"], "8": ["QB", None]} \
                and F.registered_positions(rec) == ({7: "RB", 8: "QB"}, {7: "77"})
            srcs = {n: v["from"] for n, v in man["files"].items()}
            out["manifest"] = (set(man["files"]) == {"rush_frame_val.csv.gz", "rush_frame_test.csv.gz",
                                                     "rush_frame_val.csv.gz.meta.json", "rush_frame_test.csv.gz.meta.json",
                                                     "nfl_matchup_2025.pkl", "nfl_rush_aux_2025.pkl", "nfl_matchup_ids.pkl"}
                               and man["prereg_sha256"] == rec["sha256"]
                               and all(F._sha(srcs[n]) == h for h, n in lines) and len(lines) == 7
                               and man["files"]["nfl_matchup_2025.pkl"]["digest"] == rec["stores"]["deep:nfl_matchup_2025"]["digest"]
                               and sum(1 for v in man["files"].values() if v["verified"]) == 5)
        finally:
            F.MODEL_OUT, deep_cache.CACHE_DIR = saved
    ck("the registration records every deep pickle the frames read by sha256 AND content digest, and the ids "
       "pickle's positions and Sleeper ids (what the distribution fit reads)", out.get("stores") and out.get("positions"),
       out)
    ck("the registration writes the manifest of the files to archive -- the frames, their build records, every store, "
       "aux and ids pickle -- each sha256 checkable where it lies (`sha256sum -c`)", out.get("manifest"), out)


def plumb_checks(ck, F, RULE):
    """The plumbing run never reads a held season's pbp or box: its distribution parts fit the open seasons. A
    registered command's parts read the positions the REGISTRATION recorded (Fable's m7), never the ids pickle."""
    seen, pos_seen, saved = [], [], F.fit_distributions
    rec = {"ids_positions": {"map": {"100": ["RB", "s1"], "200": ["QB", None], "300": ["", None]}}}
    refused = None
    try:
        F.fit_distributions = lambda seasons, by_type=False, pos_sl=None: (seen.append(tuple(seasons)),
                                                                           pos_seen.append(pos_sl)) and {
            "schema": 0, "kernels": {}, "alpha": {}, "kneels": {}, "qb_prior": {}}
        RULE.parts_for((2023, 2024, 2025), True)
        RULE.parts_for((2023, 2024, 2025))
        RULE.parts_for((2023, 2024, 2025), False, rec)
        try:
            RULE.parts_for((2023, 2024, 2025), False, {"sha256": "x"})
        except SystemExit as e:
            refused = str(e)
    finally:
        F.fit_distributions = saved
    ck("the plumbing run fits the distribution parts on the open seasons only (2025 held); the registered run on all",
       seen[:3] == [(2023, 2024), (2023, 2024, 2025), (2023, 2024, 2025)], seen)
    ck("a registered command's distribution parts read the registration's positions and Sleeper ids (prereg.json "
       "ids_positions), never a later ids pickle; a registration without them refuses; plumbing and research read the "
       "ids pickle", pos_seen[:2] == [None, None]
       and pos_seen[2] == ({100: "RB", 200: "QB", 300: ""}, {100: "s1"}) and refused and "ids positions" in refused,
       (pos_seen, refused))
    # the map reproduces positions() exactly on the ids it was taken from
    ids = {"ids": {7: {"pos": "RB", "sleeper": "77"}, 8: {"pos": None, "sleeper": ""}, 9: {"pos": "QB"}}}
    savedl = F.D.load_ids
    try:
        F.D.load_ids = lambda: ids
        direct = F.positions()
    finally:
        F.D.load_ids = savedl
    ck("the registration's positions map, read back, IS positions() on the ids pickle it was taken from",
       F.registered_positions({"ids_positions": {"map": json.loads(json.dumps(F.positions_map(ids)))}}) == direct,
       (direct, F.positions_map(ids)))


def qbfit_checks(ck, F):
    """The QB shares and component curves keep a QB-game with no rush (zeros), never select on the outcome."""
    saved = (F.box_rows, F.qb_components, F.team_margin)
    box = [{"season": "2023", "week": str(w), "player_id": str(pid), "team": "KC", "proj_pass_att": "30",
            "proj_rush_att": "3", "act_gp": "1"} for w in range(1, 7) for pid in (1, 2)]
    comp = {(2023, w, "1"): [1, 1, 2, 0] for w in range(1, 7)}           # QB "2" never rushed
    try:
        F.box_rows = lambda seasons, pos_set: list(box)
        F.qb_components = lambda stores, P, pos, sl: comp
        F.team_margin = lambda stores: {(2023, w, "KC"): 0.0 for w in range(1, 7)}
        prior, curves = F.fit_qb([], [], {}, {}, {"by_margin": [[-10, 0.5], [0, 0.5], [10, 0.5]], "mean": 0.5}, (2023,))
    finally:
        F.box_rows, F.qb_components, F.team_margin = saved
    ck("the QB shares and curves keep a QB-game with no rush as zeros (12 rows here, 6 of them rushless), never "
       "dropping it on its outcome", prior["rows"] == 12 and abs(prior["scramble_share"] - 0.5) < 1e-12, prior)


def hook_gate_checks(ck, co):
    """The hook's survivors (the Sonnet re-check's H01 / H04 / H06 / H07 / H10, Opus's m2): rush_coefs=False never
    reads coefs.json, only RBs and QBs enter, the universes are checked, the pre-game margin reaches the kneels,
    an unmapped player keeps the first model's row, and a failing snapshot keeps the whole board."""
    import nfl_matchup as M
    rows, idx, extra, env = _hook_fixture()
    rows = rows + [dict(rows[0], player_id="1004", player={"first_name": "Bench", "last_name": "Back", "position": "RB"},
                        stats={"rush_att": 3.0, "rush_yd": 11.0, "rush_td": 0.05, "rec_tgt": 0.5, "rec": 0.4,
                               "rec_yd": 3.0, "rec_td": 0.01, "pts_ppr": 2.0})]
    env = dict(env, sl2g=dict(env["sl2g"], **{"1004": 504}))
    rc = _hook_coefs(co)
    seen = []
    saved = (M.production_ctx, R.load_coefs, M._rush_player, M._rush_env)
    args = dict(rows=rows, kalshi_idx=idx, schedule=[], ctx=None, with_market=True, now=1793000000.0)

    def boom(*a, **k):
        raise AssertionError("load_coefs read with rush_coefs=False")
    try:
        M.production_ctx = lambda season: None
        R.load_coefs = boom
        b_off = M.board(2026, 7, rush_coefs=False, lines={}, **args)
        R.load_coefs = saved[1]
        real_rp = saved[2]
        M._rush_player = lambda rc_, env_, season, week, pos, *a: seen.append(pos) or real_rp(rc_, env_, season, week,
                                                                                               pos, *a)
        b_on = M.board(2026, 7, rush_coefs=rc, rush_env=env, lines={"KC": {"margin": 10.0, "total": 44.0}}, **args)
        b_dn = M.board(2026, 7, rush_coefs=rc, rush_env=env, lines={"KC": {"margin": -10.0, "total": 44.0}}, **args)
        env_nomap = dict(env, sl2g={"1002": 502})
        b_nm = M.board(2026, 7, rush_coefs=rc, rush_env=env_nomap, lines={}, **args)
        M._rush_player = real_rp

        def env_boom(*a, **k):
            raise RuntimeError("the snapshot failed")
        M._rush_env = env_boom
        b_fail = M.board(2026, 7, rush_coefs=rc, lines={}, **args)
    finally:
        M.production_ctx, R.load_coefs, M._rush_player, M._rush_env = saved
    pl = lambda b_, pid: next(p_ for p_ in b_["players"] if p_["player_id"] == pid)
    mk = lambda p_: {m_["stat"]: m_ for m_ in p_["markets"]}
    kne = lambda b_: next((i["value"] for i in (pl(b_, "1002").get("breakdown") or []) if i["label"] == "Expected kneels"), None)
    ck("hook: rush_coefs=False never reads coefs.json", "error" not in b_off and len(b_off["players"]) == 4)
    ck("hook: only RBs and QBs enter the rushing model (the WR never does)", seen and set(seen) <= {"RB", "QB"}
       and "WR" not in seen, seen)
    bench_on, bench_off = mk(pl(b_on, "1004")), mk(pl(b_off, "1004"))
    ck("hook: a back outside the rushing universe (3 projected carries) keeps the first model's rushing markets byte "
       "for byte", all(json.dumps(bench_on.get(m_), sort_keys=True) == json.dumps(bench_off.get(m_), sort_keys=True)
                       for m_ in ("rush_att", "rush_yd")) and "long_rush" not in bench_on)
    ck("hook: the pre-game margin reaches the QB's kneels (favoured by 10 vs an underdog by 10)",
       kne(b_on) is not None and kne(b_dn) is not None and kne(b_on) > kne(b_dn), (kne(b_on), kne(b_dn)))
    ck("hook: a player with no Sleeper-to-gsis mapping keeps the first model's row (no breakdown, no rushing market)",
       "breakdown" not in pl(b_nm, "1001")
       and not any(m_.get("engine") == "rushing" for m_ in pl(b_nm, "1001")["markets"])
       and "breakdown" in pl(b_nm, "1002"))
    ck("hook: a failing rushing snapshot keeps the WHOLE board (every row the first model's, a note, no blank board)",
       "error" not in b_fail and len(b_fail["players"]) == 4 and any("RUSH-board" in n_ for n_ in b_fail["notes"])
       and all(json.dumps(p_["markets"], sort_keys=True) == json.dumps(pl(b_off, p_["player_id"])["markets"], sort_keys=True)
               for p_ in b_fail["players"]))


def _fixture_store(season, yds=(3, 7, -2, 15), built=1.0):
    """A small compact store payload shaped like nfl_matchup_data's (arrays, games, nested dicts) with its
    save-time fields set."""
    import array
    return {"schema": 2, "season": season, "built": built, "seconds": 0.5,
            "sources": {"pbp": {"url": "u", "bytes": 10, "cached": True}},
            "teams": ["KC", "BUF"], "games": [{"id": f"{season}_01_KC_BUF", "week": 1, "spread": 3.5}],
            "n": len(yds), "cols": {"yds": array.array("h", list(yds)), "kind": array.array("B", [0, 1, 0, 1][:len(yds)])},
            "onfield": {0: {"KC": [1, 2], "BUF": [3]}}, "matched": {"participation": len(yds)},
            "vocab": {"kind": ("run", "pass"), "columns": ["yds", "kind"]}, "unknown": {}}


def stores_checks(ck, RULE):
    """The commands re-verify the season stores the distribution parts read, against the REGISTRATION's record:
    by sha256, or -- re-saved, or rebuilt after the archive was lost -- by content digest (the durability round);
    a store with one changed play refuses, naming what differs."""
    import copy as _cp
    import deep_cache
    import research.nfl_rush_model_fit as F
    out = {}
    saved = deep_cache.CACHE_DIR
    with tempfile.TemporaryDirectory() as t:
        deep_cache.CACHE_DIR = t
        try:
            for s_ in (2023, 2024):
                deep_cache.save(f"nfl_matchup_{s_}", _fixture_store(s_))
            rec = {"stores": {f"deep:nfl_matchup_{s_}": F.deep_digest(f"nfl_matchup_{s_}") for s_ in (2023, 2024)}}

            def vs(seasons, plumb=False):
                try:
                    RULE.verify_stores(rec, seasons, plumb)
                    return None
                except SystemExit as e:
                    return str(e)
            out["same"] = vs((2023, 2024))
            # re-saved: another `built`, `seconds`, download bookkeeping and save time; the same data
            deep_cache.save("nfl_matchup_2023", dict(_fixture_store(2023, built=99.0), seconds=7.0,
                                                     sources={"pbp": {"cached": False}}))
            out["resaved_sha_moved"] = F.deep_digest("nfl_matchup_2023")["sha256"] != rec["stores"]["deep:nfl_matchup_2023"]["sha256"]
            out["resaved"] = vs((2023, 2024))
            ch = _cp.deepcopy(_fixture_store(2023))
            ch["cols"]["yds"][2] = -3                                  # one play's yards
            deep_cache.save("nfl_matchup_2023", ch)
            out["changed"] = vs((2023, 2024))
            out["unrecorded"] = vs((2023, 2024, 2025))
            out["plumb"] = vs((2023, 2024, 2025), True)
        finally:
            deep_cache.CACHE_DIR = saved
    ck("the commands re-verify the season stores they read against the registration: the registered pickle and a "
       "RE-SAVED one holding the same data (another sha256) pass; a store with one changed play refuses naming the "
       "part (cols.yds); an unrecorded season refuses; plumbing is exempt",
       out.get("same") is None and out.get("resaved_sha_moved") and out.get("resaved") is None
       and "cols.yds" in (out.get("changed") or "") and "nfl_matchup_2023" in (out.get("changed") or "")
       and "no record" in (out.get("unrecorded") or "") and out.get("plumb") is None, out)


def digest_checks(ck):
    """The content digest (the durability round): canonical over everything but the save-time fields, so a
    rebuild of the same data digests the same whatever order its dicts and sets were filled in, and any
    changed value -- one play, a float's last bit, a type -- does not; an unknown type refuses to digest."""
    import array
    import research.nfl_rush_model_fit as F
    a = _fixture_store(2023)
    b = {k: a[k] for k in reversed(list(a))}                                        # insertion order reversed
    b["onfield"] = {0: {"BUF": [3], "KC": [1, 2]}}
    b["built"], b["seconds"], b["sources"], b["store_built"], b["joins"] = 5.0, 9.0, {"x": 1}, 7.0, {"pfr_crosswalk": 3}
    da, db = F.content_digest(a), F.content_digest(b)
    st = {"tags": {"b", "a", "c"}}
    st2 = {"tags": set(reversed(sorted({"a", "b", "c"})))}
    flt = dict(a, games=[dict(a["games"][0], spread=3.5000000000000004)])
    typ = dict(a, n=float(a["n"]))
    arr = dict(a, cols=dict(a["cols"], kind=array.array("H", list(a["cols"]["kind"]))))
    try:
        F.content_digest(dict(a, odd=object()))
        unknown = False
    except TypeError:
        unknown = True
    ck("the content digest leaves out exactly what no registered computation reads (the save clocks built, seconds, "
       "store_built; the download bookkeeping sources; the join diagnostics joins) and is canonical: dict order, set "
       "order and the save clock move nothing; a float's last bit, an int read as a float and an array's type code "
       "each move it; an unknown type refuses to digest",
       F.NOT_CONTENT == ("built", "seconds", "store_built", "sources", "joins") and da == db
       and F.content_digest(st)["digest"] == F.content_digest(st2)["digest"]
       and F.content_digest(flt)["digest"] != da["digest"] and F.content_digest(typ)["digest"] != da["digest"]
       and F.content_digest(arr)["digest"] != da["digest"] and unknown, (da["digest"], db["digest"]))
    one = dict(a, cols=dict(a["cols"], yds=array.array("h", [3, 7, -3, 15])))
    two = dict(a, games=[dict(a["games"][0], week=2)])
    ck("a digest mismatch names the finest part recorded: cols.yds for one changed play, games for a changed game "
       "list; agreeing digests name nothing",
       F.digest_diff(da, F.content_digest(one)) == ["cols.yds"] and F.digest_diff(da, F.content_digest(two)) == ["games"]
       and F.digest_diff(da, db) == [], (F.digest_diff(da, F.content_digest(one)), F.digest_diff(da, F.content_digest(two))))


def build_stores_checks(ck, F):
    """build_stores runs only in the registered tree, vouches only for what its OWN refresh built (Opus's F1a), and
    records the stores it built, the code that ran (refusing an unregistered module) and the registration; older
    seasons rebuilt beside it must match the registration's content digest (the durability round)."""
    import copy as _cp
    import deep_cache
    import nfl_matchup_data as D
    import nfl_rush_data as RD
    import research.nfl_rush_model_frame as FRM
    saved = (F.in_registered_tree, F.MODEL_OUT, D.refresh, RD.refresh, deep_cache.save, FRM.input_shas, F.repo_modules,
             deep_cache.CACHE_DIR)
    real_save = deep_cache.save
    got = {}
    mode = {"d": None, "r": None, "ids": True, "older_store": None}

    def d_refresh(seasons=None, **k):
        got.setdefault("refreshed", []).append(("D", tuple(seasons or ())))
        for s_ in seasons or ():
            if mode["older_store"] is not None and s_ < 2026:
                real_save(f"nfl_matchup_{s_}", mode["older_store"])          # the rebuild writes its own pickle
        sts = {s_: (mode["d"] if mode["d"] is not None else {"n": 10, "games": 1}) for s_ in seasons or ()}
        return {"seasons": sts, "ids": {"n": 5, "sleeper": 2} if mode["ids"] else None}

    def r_refresh(seasons=None, **k):
        got.setdefault("refreshed", []).append(("RD", tuple(seasons or ())))
        for s_ in seasons or ():
            if s_ < 2026:
                real_save(f"nfl_rush_aux_{s_}", {"schema": 2, "season": s_, "built": 5.0, "store_built": 6.0,
                                                 "extras": {"aborted": [0, 1]}})
        return {"seasons": {s_: (mode["r"] if mode["r"] is not None else {"built": True, "complete": True})
                            for s_ in seasons or ()}}

    def build(**kw):
        mode.update({"d": None, "r": None, "ids": True, "older_store": None}, **kw.pop("mode", {}))
        try:
            return F.build_stores(2026, **kw), None
        except SystemExit as e:
            return None, str(e)
    with tempfile.TemporaryDirectory() as t:
        deep = os.path.join(t, "deep")
        os.makedirs(deep)
        try:
            deep_cache.CACHE_DIR = deep
            real_save("nfl_matchup_2024", _fixture_store(2024))
            real_save("nfl_rush_aux_2024", {"schema": 2, "season": 2024, "built": 1.0, "store_built": 2.0,
                                            "extras": {"aborted": [0, 1]}})
            rec = {"sha256": "B" * 64, "code": {"nfl_matchup_data.py": F._sha(os.path.join(REPO, "nfl_matchup_data.py"))},
                   "stores": {f"deep:{k}": F.deep_digest(k) for k in ("nfl_matchup_2024", "nfl_rush_aux_2024")}}
            json.dump(rec, open(os.path.join(t, "prereg.json"), "w"))
            F.MODEL_OUT = t
            # stubbed FIRST: outside the tree nothing is downloaded and the refusal is the tree's own, even with
            # the registered code loaded (a mutant dropping the tree check was once refused by the code check)
            D.refresh, RD.refresh = d_refresh, r_refresh
            deep_cache.save = lambda key, obj: got.update(saved_key=key, saved=obj)
            FRM.input_shas = lambda keys: {k: "h_" + k for k in keys}
            F.repo_modules = lambda: ["nfl_matchup_data.py", "errlog.py"]
            F.in_registered_tree = lambda: None
            _r, why = build()
            got["outside"] = bool(why) and "registered tree" in why and "refreshed" not in got and "saved_key" not in got
            F.in_registered_tree = lambda: {"prereg_sha256": "B" * 64}
            real_save("nfl_matchup_2026", {"a store": "left by something else"})
            rec_, why_ok = build()
            got["moved_aside"] = (os.path.exists(os.path.join(deep, "nfl_matchup_2026.pkl.replaced"))
                                  and not os.path.exists(os.path.join(deep, "nfl_matchup_2026.pkl")))
            got["store_skipped"] = build(mode={"d": {"skipped": "no source changed and the store is current"}})[1]
            got["store_error"] = build(mode={"d": {"error": "no play-by-play"}})[1]
            got["aux_skipped"] = build(mode={"r": {"skipped": "no source changed"}})[1]
            got["aux_error"] = build(mode={"r": {"error": "no play-by-play file"}})[1]
            got["no_ids"] = build(mode={"ids": False})[1]
            FRM.input_shas = lambda keys: {k: (None if "aux" in k else "h_" + k) for k in keys}
            got["missing_aux"] = build()[1]
            FRM.input_shas = lambda keys: {k: "h_" + k for k in keys}
            older_ok, why_older = build(older=[2024], mode={"older_store": dict(_fixture_store(2024), built=77.0)})
            ch = _cp.deepcopy(_fixture_store(2024))
            ch["cols"]["yds"][0] = 4
            got["older_changed"] = build(older=[2024], mode={"older_store": ch})[1]
            got["older_unknown"] = build(older=[2018])[1]
            F.repo_modules = lambda: ["nfl_matchup_data.py", "clock.py"]
            got["unreg"] = build()[1]
        finally:
            (F.in_registered_tree, F.MODEL_OUT, D.refresh, RD.refresh, deep_cache.save, FRM.input_shas, F.repo_modules,
             deep_cache.CACHE_DIR) = saved
    ref = lambda k_, txt: bool(got.get(k_)) and txt in got[k_]
    ck("build_stores runs only in the registered tree, refreshes the 2026 store and THEN its aux, records the 2026 "
       "store, aux and ids (all three) with the code that ran and the registration, and refuses a run that loaded an "
       "unregistered module or found a pickle missing",
       got.get("outside") and why_ok is None and rec_ and got.get("saved_key") == F.STORES_KEY
       and rec_["prereg_sha256"] == "B" * 64 and set(rec_["code"]) == {"nfl_matchup_data.py", "errlog.py"}
       and set(rec_["stores"]) == {"deep:nfl_matchup_2026", "deep:nfl_rush_aux_2026", "deep:nfl_matchup_ids"}
       and got.get("refreshed", [])[0:2] == [("D", (2026,)), ("RD", (2026,))] and ref("unreg", "registered code")
       and ref("missing_aux", "missing"), {k_: v_ for k_, v_ in got.items() if k_ != "saved"})
    ck("build_stores moves the season's store and aux already in the folder aside (<key>.pkl.replaced) before its "
       "refresh, so the refresh BUILDS them (a re-run after a refusal would otherwise be refused for good)",
       got.get("moved_aside"), got.get("moved_aside"))
    ck("build_stores vouches only for what its own refresh BUILT (Opus's F1a): a store or aux the refresh kept "
       "(skipped: unchanged upstream) or could not fetch (error), or ids it did not rebuild, refuses",
       all(ref(k_, "did not BUILD") for k_ in ("store_skipped", "store_error", "aux_skipped", "aux_error"))
       and ref("no_ids", "ids"), {k_: got.get(k_) for k_ in ("store_skipped", "store_error", "aux_skipped", "aux_error",
                                                             "no_ids")})
    ck("build_stores --older rebuilds a registered older season beside 2026: the same data under a new save clock "
       "passes and is recorded; one changed play refuses naming the part; a season the frames never read refuses",
       why_older is None and older_ok and set(older_ok["older"]) == {"deep:nfl_matchup_2024", "deep:nfl_rush_aux_2024"}
       and ref("older_changed", "cols.yds") and ref("older_unknown", "not older seasons"),
       (why_older, got.get("older_changed"), got.get("older_unknown")))
    src = open(os.path.join(REPO, "research", "nfl_rush_dataset.py"), encoding="utf-8").read()
    ck("the dataset build records the sha256 of every repository module it loaded, and the versions, in its meta",
       'meta["code"] = {f: sha(os.path.join(repo, f)) for f in mods}' in src and 'meta["versions"] =' in src)


def provenance_checks(ck, F, RULE):
    """The prospective frame's DATA was made by the registered bytes (the confirmation's C2): the dataset by
    realpath, by its recorded code, by the stores it read (Opus's F1b) and the weeks it holds (Fable's B1); the
    2026 store, aux and ids by the registered tree's build record; the older stores by the registered frames'
    record, by sha256 or content digest. Each case is refused for ITS reason."""
    import shutil as _sh
    rec = {"sha256": "P" * 64, "code": {"nfl_rush_data.py": "c1", "research/nfl_matchup_dataset.py": "c2"},
           "frames_meta": {"test": {"inputs": {"deep:nfl_matchup_2025": "s25", "deep:nfl_rush_aux_2025": "a25"}}}}
    with tempfile.TemporaryDirectory() as t:
        mo = os.path.join(t, "repo", "research", "data", "nfl_rush_model")
        ds = os.path.join(mo, "prospective_dataset")
        reg_ds = os.path.join(t, "repo", "research", "data", "nfl_rush")
        os.makedirs(ds)
        os.makedirs(reg_ds)
        link = os.path.join(t, "looks_separate")
        os.symlink(reg_ds, link)
        right = os.path.join(t, "right_by_a_link")
        os.symlink(ds, right)
        ins = {"deep:nfl_rush_prospective_stores": "sv", "deep:nfl_matchup_2026": "m26", "deep:nfl_rush_aux_2026": "x26",
               "deep:nfl_matchup_ids": "ids", "deep:nfl_matchup_2025": "s25", "deep:nfl_rush_aux_2025": "a25"}
        dsi = {"deep:nfl_matchup_2026": "m26", "deep:nfl_rush_aux_2026": "x26", "deep:nfl_matchup_ids": "ids",
               "deep:nfl_matchup_2025": "s25", "deep:nfl_matchup_2018": "only the dataset read it"}
        ds_meta = {"code": {"nfl_rush_data.py": "c1", "research/nfl_matchup_dataset.py": "c2", "errlog.py": "zz"},
                   "inputs": dsi, "by_season": {"2026": {"weeks": list(range(1, 12))}}}
        meta = {"dataset_realpath": ds, "dataset_meta_sha256": "dm", "inputs": ins}
        st = {"prereg_sha256": "P" * 64, "code": {"nfl_rush_data.py": "c1", "deep_cache.py": "c3"} | {},
              "stores": {"deep:nfl_matchup_2026": "m26", "deep:nfl_rush_aux_2026": "x26", "deep:nfl_matchup_ids": "ids"}}
        rec_ok = dict(rec, code=dict(rec["code"], **{"deep_cache.py": "c3"}))
        pr = lambda m=meta, r=rec_ok, dm=ds_meta, dsha="dm", sr=st, ssha="sv", older=None, weeks=None: \
            RULE.provenance_refusal(m, r, mo, dm, dsha, sr, ssha, older, weeks=weeks)
        both = lambda k_, v_: {"m": dict(meta, inputs=dict(ins, **{k_: v_})), "dm": dict(ds_meta, inputs=dict(dsi, **{k_: v_}))}
        cases = {   # case: (refusal, the words it must give)
            "the registered folder through a link": (pr(m=dict(meta, dataset_realpath=link)), "by realpath"),
            "the registered folder by path": (pr(m=dict(meta, dataset_realpath=reg_ds)), "by realpath"),
            "a dataset meta that changed": (pr(dsha="other"), "not the one the frame read"),
            "a dataset built by an unregistered file": (
                pr(dm=dict(ds_meta, code=dict(ds_meta["code"], **{"clock.py": "q"}))), "not built by the registered code"),
            "a dataset built by a registered file at other bytes": (
                pr(dm=dict(ds_meta, code=dict(ds_meta["code"], **{"nfl_rush_data.py": "x"}))), "not built by the registered code"),
            "a dataset with no code record": (pr(dm={k_: v_ for k_, v_ in ds_meta.items() if k_ != "code"}),
                                              "not built by the registered code"),
            "a dataset that records no stores": (pr(dm={k_: v_ for k_, v_ in ds_meta.items() if k_ != "inputs"}),
                                                 "does not record the stores it read"),
            "a dataset that records no ids": (pr(dm=dict(ds_meta, inputs={k_: v_ for k_, v_ in dsi.items()
                                                                         if k_ != "deep:nfl_matchup_ids"})),
                                              "does not record the stores it read"),
            "a dataset built on another 2026 store than the frame's": (
                pr(dm=dict(ds_meta, inputs=dict(dsi, **{"deep:nfl_matchup_2026": "live"}))), "read other stores"),
            "a dataset built on another ids than the frame's": (
                pr(dm=dict(ds_meta, inputs=dict(dsi, **{"deep:nfl_matchup_ids": "live"}))), "read other stores"),
            "a dataset built on another OLDER store than the frame's": (
                pr(dm=dict(ds_meta, inputs=dict(dsi, **{"deep:nfl_matchup_2025": "s25-other"}))), "read other stores"),
            "a dataset short of a window week": (pr(weeks=[8, 9, 10, 11, 12]), "window week(s) [12]"),
            # the frame's own record must name the vouched three (Opus's PV6d: a frame meta without the ids passed)
            "a frame that records no ids": (pr(m=dict(meta, inputs={k_: v_ for k_, v_ in ins.items()
                                                                    if k_ != "deep:nfl_matchup_ids"})),
                                            "nfl_matchup_ids is not the store"),
            # an older store the registered frames never read is not theirs (Opus's PV7e passed it)
            "an older store the registered frames never read": (
                pr(m=dict(meta, inputs=dict(ins, **{"deep:nfl_matchup_2017": "s17"}))), "not the registered frames'"),
            "no stores record": (pr(sr=None), "build record is missing"),
            "a stores record that changed": (pr(ssha="other"), "build record is missing"),
            "stores of another registration": (pr(sr=dict(st, prereg_sha256="Q" * 64)), "under this registration"),
            "stores built by other code": (pr(sr=dict(st, code={"nfl_rush_data.py": "x"})), "under this registration"),
            "a 2026 store the build did not make": (pr(**both("deep:nfl_matchup_2026", "m26b")),
                                                    "nfl_matchup_2026 is not the store"),
            "a 2026 aux the build did not make": (pr(**both("deep:nfl_rush_aux_2026", "x26b")),
                                                  "nfl_rush_aux_2026 is not the store"),
            "ids the build did not make": (pr(**both("deep:nfl_matchup_ids", "idsb")), "nfl_matchup_ids is not the store"),
            "an older aux that is not the registered one": (
                pr(m=dict(meta, inputs=dict(ins, **{"deep:nfl_rush_aux_2025": "a25b"}))), "not the registered frames'"),
            "an older matchup store that is not the registered one": (
                pr(**both("deep:nfl_matchup_2025", "s25b")), "not the registered frames'"),
            "an older store whose CONTENT differs": (
                pr(m=dict(meta, inputs=dict(ins, **{"deep:nfl_rush_aux_2025": "a25b"})),
                   older={"deep:nfl_rush_aux_2025": "nfl_rush_aux_2025 is not the registration's: its content differs in "
                                                    "['sit']"}), "differs in ['sit']"),
        }
        good = pr()
        good_weeks = pr(weeks=[8, 9, 10, 11])
        good_link = pr(m=dict(meta, dataset_realpath=right))
        good_resaved = pr(m=dict(meta, inputs=dict(ins, **{"deep:nfl_rush_aux_2025": "a25b"})),
                          older={"deep:nfl_rush_aux_2025": None})
        _sh.rmtree(ds)
    wrong = {k_: v_[0] for k_, v_ in cases.items() if not (v_[0] and v_[1] in v_[0])}
    ck("the prospective frame's data must be the registered bytes': the dataset by REALPATH (the registered folder by "
       "a link or by path refuses; the right folder through a link passes), by its recorded code, by the stores it "
       "read (every one it shares with the frame the frame's, the 2026 three recorded) and the window weeks it holds; "
       "the 2026 store / aux / ids by the registered tree's build record under this registration; the older stores by "
       "the registered frames' record or, re-saved, by content -- each refused for its own reason",
       good is None and good_weeks is None and good_link is None and good_resaved is None and not wrong,
       wrong or (good, good_weeks, good_link, good_resaved))


def provenance_io_checks(ck, F, RULE):
    """_provenance reads what is on disk NOW (the dataset's meta.json, the stores record) and checks an older store
    the frame read, re-saved since the registration, by content digest; the frame records the realpath of the
    dataset it read, its meta.json's sha256 and (prospective only) the stores record (Sonnet's FM1-FM3, Opus's
    FM1-FM3, PV9a-b)."""
    import deep_cache
    import research.nfl_rush_model_frame as FRM
    saved = deep_cache.CACHE_DIR
    got = {}
    with tempfile.TemporaryDirectory() as t:
        deep = os.path.join(t, "deep")
        ds = os.path.join(t, "prospective_dataset")
        os.makedirs(deep)
        os.makedirs(ds)
        link = os.path.join(t, "ds_link")
        os.symlink(ds, link)
        json.dump({"code": {"a.py": "1"}}, open(os.path.join(ds, "meta.json"), "w"))
        try:
            deep_cache.CACHE_DIR = deep
            deep_cache.save(F.STORES_KEY, {"prereg_sha256": "P", "stores": {}})
            deep_cache.save("nfl_matchup_2025", _fixture_store(2025))
            # the registration's pickle held the same data under another save clock: another sha256, the same digest
            rec = {"stores": {"deep:nfl_matchup_2025": dict(F.deep_digest("nfl_matchup_2025"), sha256="registered sha")},
                   "frames_meta": {"test": {"inputs": {"deep:nfl_matchup_2025": "registered sha"}}}}
            pf, extra = FRM.provenance_fields(link, True)
            pf0, extra0 = FRM.provenance_fields(ds, False)
            got["fields"] = (pf["dataset_realpath"] == os.path.realpath(ds) and pf["dataset_meta_sha256"] == F._sha(
                os.path.join(ds, "meta.json")) and extra == {f"deep:{F.STORES_KEY}"} and extra0 == set()
                and pf0["dataset_realpath"] == os.path.realpath(ds))
            now_sha = F.deep_digest("nfl_matchup_2025")["sha256"]
            meta = {"dataset_realpath": pf["dataset_realpath"], "dataset_meta_sha256": pf["dataset_meta_sha256"],
                    "inputs": {f"deep:{F.STORES_KEY}": "the frame's", "deep:nfl_matchup_2025": now_sha}}
            json.dump({"code": {"a.py": "2"}}, open(os.path.join(ds, "meta.json"), "w"))       # edited after the frame
            dm, dsha, srec, ssha, older = RULE._provenance(meta, rec)
            got["now"] = (dm == {"code": {"a.py": "2"}} and dsha == F._sha(os.path.join(ds, "meta.json"))
                          and dsha != meta["dataset_meta_sha256"] and srec == {"prereg_sha256": "P", "stores": {}}
                          and ssha == F._sha(os.path.join(deep, f"{F.STORES_KEY}.pkl")) and ssha != "the frame's")
            got["older_same"] = older == {"deep:nfl_matchup_2025": None}
            deep_cache.save("nfl_matchup_2025", dict(_fixture_store(2025), built=42.0))     # re-saved after the frame
            got["older_moved"] = "not the file the frame read" in (RULE._provenance(meta, rec)[4].get(
                "deep:nfl_matchup_2025") or "")
        finally:
            deep_cache.CACHE_DIR = saved
    ck("the frame records the REALPATH of the dataset it read (a link resolved), that meta.json's sha256, and -- a "
       "prospective frame only -- the 2026 stores' build record as an input", got.get("fields"), got)
    ck("_provenance reads the dataset's meta.json and the stores record as they are NOW (never the frame's own "
       "record of them), and checks an older store the frame read by content: the same data passes, a file replaced "
       "since the frame read it does not", got.get("now") and got.get("older_same") and got.get("older_moved"), got)


def prosp_checks(ck, RULE):
    """The prospective frame the rule may read (Q1 / Q6): every registered file at its registered bytes,
    the registered build, after the registration."""
    rec = {"utc": "2026-10-25T12:00:00Z", "code": {"a.py": "h1", "b.py": "h2"}}
    good = {"code": {"a.py": "h1", "b.py": "h2", "c.py": "x"}, "k": {"mode": "pooled"}, "seasons": [2026],
            "frozen_only": True, "built_utc": "2027-01-20T09:00:00Z",
            "dataset": "research/data/nfl_rush_model/prospective_dataset"}
    bad = {"a registered file missing from the build": dict(good, code={"a.py": "h1"}),
           "a registered file at other bytes": dict(good, code={"a.py": "h1", "b.py": "zz"}),
           "the fit-only k": dict(good, k={"mode": "fit_only"}), "more than 2026": dict(good, seasons=[2025, 2026]),
           "not frozen-only": dict(good, frozen_only=False), "built before the registration": dict(
               good, built_utc="2026-10-25T11:59:59Z"), "no build time": dict(good, built_utc=None),
           }
    got = {k: RULE.prospective_frame_refusal(m, rec) for k, m in bad.items()}
    ck("the prospective frame is refused unless built by EVERY registered file at its registered bytes, on the pooled "
       "k, for 2026 only, frozen rows only, after the registration",
       RULE.prospective_frame_refusal(good, rec) is None and all(got.values()), got)


def leak_checks(ck, F, RULE):
    """The leakage-relevant definitions and the rule's numbers, each pinned (the reviews' survivors)."""
    import research.nfl_rush_model_frame as FRM
    import nfl_rush_profiles as RP
    import datetime
    # the k each frame reads
    rel = json.load(open(os.path.join(REPO, "research", "data", "nfl_rush", "reliability.json")))
    met = "rb_carry_share"
    with tempfile.TemporaryDirectory() as td:
        ki = FRM.use_k("fit_only", td)
        k_fit = RP.k_for(met)[0]
        kp = FRM.use_k("pooled", td)
        k_pool = RP.k_for(met)[0]
    src = open(os.path.join(REPO, "research", "nfl_rush_model_frame.py"), encoding="utf-8").read()
    ck("the validation frame shrinks with the 2018-2024 k (k_fit_only), the test frame with the pooled k, and the "
       "first model's deviations follow (NFLM_K set before nfl_matchup is imported; its K_MODE asserted)",
       ki["mode"] == "fit_only" and abs(k_fit - rel["k_fit_only"]["k"][met]) < 1e-9 and kp["mode"] == "pooled"
       and abs(k_pool - rel["k"][met]) < 1e-9 and k_fit != k_pool
       and src.index('os.environ["NFLM_K"] = "fit_only"') < src.index("    import nfl_matchup as M")
       and "M.K_MODE != (" in src, (k_fit, k_pool))
    # comparables read strictly earlier games: DISTINCT opponent vectors week by week, so the similarity
    # weights differ from the recency weights and the feature is not identically 0 (the re-checks' O3 / O4:
    # with every vector equal the guard could not fail)
    nv = len(R.VEC)
    hist = {(s_, w, "OPP"): [((w + 3 * j) % 5 - 2) * 0.4 * (1 if s_ == 2025 else -1) for j in range(nv)]
            for s_ in (2024, 2025) for w in range(1, 10)}
    me = [((3 + 3 * j) % 5 - 2) * 0.4 for j in range(nv)]
    logs = {7: {(2025, 3): {"opp": "OPP", "rush_yd": 40.0, "rec_yd": 0.0}, (2025, 4): {"opp": "OPP", "rush_yd": 95.0, "rec_yd": 0.0},
                (2025, 5): {"opp": "OPP", "rush_yd": 80.0, "rec_yd": 0.0}, (2025, 6): {"opp": "OPP", "rush_yd": 70.0, "rec_yd": 0.0},
                (2024, 9): {"opp": "OPP", "rush_yd": 50.0, "rec_yd": 0.0}}}
    a = R.comparables(hist, logs, 7, 2025, 5, me, "rush_yd")
    poison = {7: {**logs[7], (2025, 5): {"opp": "OPP", "rush_yd": 9999.0, "rec_yd": 0.0},
                  (2025, 6): {"opp": "OPP", "rush_yd": 9999.0, "rec_yd": 0.0}}}
    b = R.comparables(hist, poison, 7, 2025, 5, me, "rush_yd")
    earlier = {7: {**logs[7], (2025, 3): {"opp": "OPP", "rush_yd": 400.0, "rec_yd": 0.0}}}
    c_ = R.comparables(hist, earlier, 7, 2025, 5, me, "rush_yd")
    ck("comparables read STRICTLY earlier games: with distinct opponent vectors the feature is not 0, poisoning the "
       "row's own week and later weeks moves nothing, and poisoning an earlier week does",
       a == b and a[1] > 0 and abs(a[0]) > 1e-4 and abs(c_[0] - a[0]) > 1e-4, (a, b, c_))
    # the kickoff clock and the margin's sign
    et = lambda s_: datetime.datetime.strptime(s_, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc).timestamp()
    ck("the store's kickoff is EASTERN time (DST-aware): 13:00 on 10/25/26 is 17:00 UTC, on 11/08/26 18:00 UTC",
       FRM.kick_ts({"kick": "10/25/26, 13:00:00"}) == et("2026-10-25T17:00:00Z")
       and FRM.kick_ts({"kick": "11/08/26, 13:00:00"}) == et("2026-11-08T18:00:00Z") and FRM.kick_ts({}) is None)
    ck("the team-side pre-game margin: nflverse's spread_line is + for a favoured HOME team",
       FRM.team_margin({"spread": "7", "home": "KC"}, "KC") == 7.0 and FRM.team_margin({"spread": "7", "home": "KC"}, "BUF") == -7.0
       and FRM.team_margin({"home": "KC"}, "KC") is None)
    # the Q1 universes, at their edges
    u = R.in_universe
    ck("the Q1 universes: RB rushing 5+ projected carries with a yards line; RB receiving 2+ targets and a receptions "
       "line; RB TD 0.15+ with projected carries or targets; QB 20+ pass attempts and 2+ rushing attempts",
       u("RB", "rush_yd", {"rush_att": 5, "rush_yd": 1}) and not u("RB", "rush_yd", {"rush_att": 4.99, "rush_yd": 30})
       and not u("RB", "rush_att", {"rush_att": 9, "rush_yd": 0}) and u("RB", "rec", {"rec_tgt": 2, "rec": 0.1})
       and not u("RB", "rec", {"rec_tgt": 1.99, "rec": 2}) and not u("RB", "rec_yd", {"rec_tgt": 3, "rec": 0})
       and u("RB", "td", {"rush_td": 0.1, "rec_td": 0.05, "rush_att": 3}) and u("RB", "td", {"rush_td": 0.15, "rec_tgt": 1})
       and not u("RB", "td", {"rush_td": 0.1, "rec_td": 0.049, "rush_att": 9}) and not u("RB", "td", {"rush_td": 0.4})
       and u("QB", "rush_yd", {"pass_att": 20, "rush_att": 2}) and not u("QB", "rush_yd", {"pass_att": 19.9, "rush_att": 5})
       and not u("QB", "long_rush", {"pass_att": 30, "rush_att": 1.99}) and not u("WR", "rush_yd", {"rush_att": 9, "rush_yd": 9}))
    rep_txt = open(os.path.join(REPO, "research", "reports", "nfl_rush_model.md"), encoding="utf-8").read()
    pre = rep_txt[rep_txt.find("<!-- RUSH-PREREG-START -->"):rep_txt.find("<!-- RUSH-PREREG-END -->")]
    ck("the Q4 thresholds are the code's (generated table inside the registered block), the longest rush included",
       FRM.thresholds_md() in pre and R.THRESHOLDS["RB"]["long_rush"] == (10, 15, 20, 25, 30, 40)
       and R.THRESHOLDS["RB"]["rush_att"] == (5, 10, 15, 20) and R.THRESHOLDS["QB"]["rush_yd"] == (10, 15, 25, 40, 50, 60))
    yo = FRM.outcomes_of({"act_rush_td": "2", "sl_act_gp": "1"})
    ck("the outcome TDs are the pbp's rushing + receiving TDs, a missing one 0 (never 1)",
       yo["y_rush_td"] == 2.0 and yo["y_rec_td"] == 0.0 and FRM.outcomes_of({})["y_rush_td"] == 0.0)
    # the pending-input block, with an explicit expectation
    saved = R.PENDING_PATHS
    try:
        R.PENDING_PATHS = frozenset({("dq", "hit_sack_rate.oe_ratio")})
        bl = F.blocked_inputs({"val": {"paths_absent": []}, "test": {"paths_absent": []}})
    finally:
        R.PENDING_PATHS = saved
    ck("a PENDING fitted input blocks registration (explicit expectation)", bl == {"pending": [("dq", "hit_sack_rate.oe_ratio")]})
    # the grid wrap
    co = _coefs()
    big = R.rb_dists({"rush_att": 14.0, "rush_yd": 500.0}, co)[0]["rush_yd"]
    ok_ = R.rb_dists({"rush_att": 14.0, "rush_yd": 61.0}, co)[0]["rush_yd"]
    pl_ = R.plan(dict(co, shipped={"RB:rush_att": [{"group": "SHAPE", "status": "provisional"}],
                                   "RB:rush_yd": [{"group": "SHAPE", "status": "provisional"}]}, models={}),
                 "RB", {"rush_att": 14.0, "rush_yd": 500.0}, {})
    ck("a compound whose grid wrapped is detected (analytic vs grid mean) and never priced",
       big.wrapped and not ok_.wrapped and pl_["markets"]["rush_yd"]["dist_fn"](1.0) is None
       and abs(big.mass_raw - 1.0) < 1e-6)
    # the week-agnostic frozen reader (B2): a back first seen after week 7 is kept
    with tempfile.TemporaryDirectory() as td:
        def cap(w, pid, first, last, team, ts):
            return {"player_id": pid, "week": w, "team": team, "opponent": "MIA", "updated_at": ts * 1000,
                    "player": {"first_name": first, "last_name": last, "position": "RB"},
                    "stats": {"rush_att": 9.0, "rush_yd": 38.0}}
        json.dump([cap(10, "9001", "Late", "Back", "NYJ", 1.8e9)], open(os.path.join(td, "proj_2026_10.json"), "w"))
        json.dump([cap(11, "9002", "Eleven", "Week", "NYJ", 1.8e9)], open(os.path.join(td, "proj_2026_11.json"), "w"))
        lg = {"week": 10, "sleeper": {"late back": {"team": "NYJ", "pos": "RB", "ts": 1.8e9 + 60,
                                                     "stats": {"rush_att": 11.0, "rush_yd": 47.0}},
                                       "roster only": {"team": "NYJ", "pos": "RB", "ts": 1.8e9 + 60,
                                                       "stats": {"rush_att": 6.0, "rush_yd": 25.0}}}}
        lp = os.path.join(td, "nfl_props_2026_w10.json")
        json.dump(lg, open(lp, "w"))
        fz = FRM.frozen_lines([lp], td, [{"name": "Roster Only", "team": "NYJ", "sleeper_id": "9003"}])
    ck("the frozen reader is week-agnostic: a week-10 back first seen after week 7 is kept (props log mapped by that "
       "week's capture), a roster-only back too, and week 11's capture is read (two-digit weeks)",
       (10, "9001") in fz and any("props log" in c[2] for c in fz[(10, "9001")]) and (10, "9003") in fz
       and (11, "9002") in fz, sorted(fz))
    # the licence default under the round-4 tags: dec_trend's inputs, and a special-teams-only PFR line
    ck("dec_trend's inputs are unrestricted under the data layer's own tags",
       not RP.restricted(RP.SOURCE_OF("rb", "recent.carry_share")) and not RP.restricted(RP.SOURCE_OF("rb", "usage.carry_share"))
       and RP.restricted(RP.SOURCE_OF("rb", "any_snap.carry_share")))
    st_only(ck)


def reg_checks(ck, F, RULE):
    """The registered block's own text and records (the reviews' R3 / R7-R9), the missing-count fill,
    the computed error rates and the capture clock, each pinned to its definition."""
    import calendar
    import csv as _csv
    import gzip as _gz
    import re
    import research.nfl_rush_model_frame as FRM
    blk = F.rule_block()
    flat = " ".join(blk.split())
    ck("the hashed block runs from its START marker through its END marker",
       blk.startswith(F.RULE_A) and blk.endswith(F.RULE_B))
    miss = [f for f in F.REG_CODE + F.REG_DATA if f"`{f}`" not in blk]
    ck("Q9 names every registered code file and every registered data file (REG_CODE / REG_DATA)", not miss, miss)
    # and the other way round: a file Q9 lists as registered that REG_CODE drops (the dataset build's Sleeper parser,
    # the Opus confirmation's C2a) would refuse every genuine prospective dataset; the neutral pair is Q9's exactly
    q9 = blk[blk.find("The registered code (`REG_CODE`):"):blk.find("The registered data (`REG_DATA`):")]
    listed = set(re.findall(r"`([\w/]+\.py)`", q9[:q9.find("Audited by")]))
    neutral = set(re.findall(r"`([\w/]+\.py)`", q9[q9.find("Audited by"):]))
    ck("Q9's registered code list IS REG_CODE (both ways), and the modules it calls neutral ARE NEUTRAL",
       listed == set(F.REG_CODE) and neutral == set(F.NEUTRAL) and len(F.REG_CODE) == 17,
       (sorted(listed ^ set(F.REG_CODE)), sorted(neutral ^ set(F.NEUTRAL))))
    # the computed error rates, recomputed from the block's own decision count and the gate constants
    m = re.search(r"makes \*\*(\d+)\*\* group x market", flat)
    n_dec = int(m.group(1)) if m else 0
    er, s05, s13 = RULE.error_rates(n_dec), RULE.error_rates(n_dec, 0.005), RULE.error_rates(n_dec, 0.013)
    phi = lambda z: 0.5 * math.erfc(-z / math.sqrt(2.0))
    want = [f"That is {100 * er['provisional']:.2f}% a decision on the normal figure: {er['provisional_expected']:.2f} "
            f"expected across {n_dec} decisions, and {100 * er['provisional_any']:.1f}% for at least one",
            f"That is {100 * er['permanent']:.2f}% a decision on the normal figure: {er['permanent_expected']:.3f} "
            f"expected and **{100 * er['permanent_any']:.1f}%** for at least one across the {n_dec}",
            f"{100 * s05['provisional_any']:.1f}% (at 0.5%) to {100 * s13['provisional_any']:.1f}% (at 1.3%)",
            f"{100 * s05['permanent_any']:.1f}% (at 0.5%) to {100 * s13['permanent_any']:.1f}% (at 1.3%)",
            f"about {100 * er['gate']:.3f}% of the time", f"(d < -1 SE, {100 * phi(-RULE.KEEP_SE):.2f}% for a null)"]
    absent = [w for w in want if w not in flat]
    ck("Q3b's false-ship figures are error_rates() of the block's own decision count, and error_rates reads the "
       "gates' own constants (2 SE, 1 SE)",
       n_dec > 0 and not absent and abs(er["gate"] - phi(-2.0)) < 1e-12 and RULE.GATE_SE == 2.0
       and RULE.KEEP_SE == 1.0 and RULE.accept_2025(-0.21, 0.1, 1) and not RULE.accept_2025(-0.19, 0.1, 1)
       and RULE.keep_prospective(-0.11, 0.1) and not RULE.keep_prospective(-0.09, 0.1), (n_dec, absent))
    # an outcome-free read drops EVERY y_* column, whatever its name
    with tempfile.TemporaryDirectory() as td:
        p_ = os.path.join(td, "f.csv.gz")
        cols = ["season", "pos", "game_id", "y_played", "y_src", "y_long", "y_des", "y_cond_played", "f_spd_rb_ypc"]
        with _gz.open(p_, "wt", newline="") as fh:
            w = _csv.DictWriter(fh, cols)
            w.writeheader()
            w.writerow({c: ("2025" if c == "season" else "1") for c in cols})
        got = F.read_frame(p_, (2025,), outcomes=False)
    ck("an outcome-free frame read (the audit's, the dry run's) drops every y_* column (y_played, y_src, y_long, "
       "y_des included) and keeps the features",
       len(got) == 1 and not [k for k in got[0] if k.startswith("y_")] and "f_spd_rb_ypc" in got[0])
    # the missing-count fill: train and serve agree
    terms = [{"feature": "cfp_ol_out", "b": 0.5, "mean": 1.25, "sd": 0.8},
             {"feature": "spd_rb_ypc", "b": 0.3, "mean": 0.1, "sd": 0.5}]
    z = R.design_row(terms, {})
    rows = [{"pos": "RB", "season": "2023", "f_cfp_ol_out": v, "f_spd_rb_ypc": u_}
            for v, u_ in (("1", "0.2"), ("", ""), ("3", "0.4"))]
    Fr = RULE.Frame(rows, RULE.kind_revised)
    c1, c2 = RULE.fill_column(Fr, [0, 1, 2], "cfp_ol_out"), RULE.fill_column(Fr, [0, 1, 2], "spd_rb_ypc")
    ck("a missing non-negative count (MEAN_FILL) reads its training mean on the board and in the fit (no effect); a "
       "missing deviation reads 0 before centring in both",
       R.MEAN_FILL == frozenset({"cfp_ol_out", "dec_room_out"}) and z[0] == 0.0 and abs(z[1] - (0.0 - 0.1) / 0.5) < 1e-12
       and list(c1) == [1.0, 2.0, 3.0] and list(c2) == [0.2, 0.0, 0.4], (z, list(c1), list(c2)))
    # the capture clock: content time no earlier than the recorded fetch
    with tempfile.TemporaryDirectory() as td:
        sub = os.path.join(td, "cap")
        os.makedirs(sub)
        row = {"player_id": "9100", "week": 7, "team": "NYJ", "opponent": "MIA", "updated_at": 1.8e9 * 1000,
               "player": {"first_name": "Fetched", "last_name": "Late", "position": "RB"},
               "stats": {"rush_att": 9.0, "rush_yd": 38.0}}
        json.dump([row], open(os.path.join(sub, "proj_2026_7.json"), "w"))
        a_ = FRM.frozen_lines([], sub, [])[(7, "9100")]
        json.dump({"captured_utc": "2027-01-15 09:00:00 UTC"}, open(os.path.join(sub, "capture.json"), "w"))
        b_ = FRM.frozen_lines([], sub, [])[(7, "9100")]
    kick = 1.8e9 + 1800
    ck("a capture counts only when its Sleeper updated_at AND its recorded fetch precede kickoff (fetched after "
       "kickoff, it is never the frozen line)",
       FRM.choose_frozen(a_, kick) is not None and FRM.choose_frozen(b_, kick) is None
       and b_[0][0] == float(calendar.timegm((2027, 1, 15, 9, 0, 0))), (a_[0][0], b_[0][0]))


def st_only(ck):
    """A special-teams-only PFR snap line must move NO fitted input (the review's M8): the data layer's own
    fixture store, its special-teams-only back's PFR line removed, every fitted path of every team and back
    compared."""
    try:
        import guards_nfl_rush_data as GD
    except ImportError:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import guards_nfl_rush_data as GD
    import copy as _cp
    import research.nfl_rush_model_frame as FRM
    import nfl_rush_profiles as RP
    with tempfile.TemporaryDirectory() as d:
        store, aux, _paths = GD._build(d)
        aux2 = _cp.deepcopy(aux)
        removed = 0
        for gid, teams in (aux2.get("snaps") or {}).items():
            for tm, sn in teams.items():
                if 100010 in (sn.get("skill") or {}):
                    sn["skill"].pop(100010)
                    removed += 1
        fp = FRM.fitted_paths()

        def fitted(a_):
            S, _T = GD._snap(store, a_, 6)
            out = {}
            for tm in store["teams"]:
                for b, blk in (("d", S.defense_run(tm)), ("dq", S.defense_qb(tm)), ("ol", S.offense_line(tm)),
                               ("tc", S.team_context(tm))):
                    fl = RP.flatten(blk)
                    out.update({(tm, b, p_): (fl.get(p_) or {}).get("value") for bb, p_ in fp if bb == b})
            for pid in (100004, 100006, 100010):
                fl = RP.flatten(S.rb(pid, "KC"))
                out.update({(pid, "p", p_): (fl.get(p_) or {}).get("value") for bb, p_ in fp if bb == "p"})
            return out
        a_, b_ = fitted(aux), fitted(aux2)
    moved = [k for k in a_ if a_[k] != b_.get(k)]
    ck("licence: removing a special-teams-only PFR snap line moves no fitted input (teams' blocks and every back's)",
       removed >= 1 and not moved, (removed, moved[:5]))


def main():
    fails = []
    n = [0]

    def ck(name, cond, detail=""):
        n[0] += 1
        if not cond:
            fails.append((name, detail))
            print(f"FAIL {name} {str(detail)[:300]}")
    try:
        run(ck)
    except (Exception, SystemExit) as e:      # noqa: BLE001 -- a guard file that cannot run is a failure, never a pass
        fails.append(("guards ran to the end", repr(e)))
        print(f"FAIL guards ran to the end {e!r}")
    print(f"guards_nfl_rush_model: {n[0]} checks, {len(fails)} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
