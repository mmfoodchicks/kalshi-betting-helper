"""Pre-registered (report section 9, the RUSHCV block): a line-dependent
spread for rushing yards, judged ONCE on 2026 weeks 8-10. RESEARCH; nothing in
production reads it, and a PASS ships nothing by itself.

Why: one positive-part CV per position is too thin for small lines (review
F3, 2026-10-23): on Sleeper lines of 20-30 yards a running back's 10+ reads
0.857 where 0.671 hit (2023-25, n 487). The candidate is the passing-yards
decile template applied to the rushing-yards CV: cv(line) = c x line^d per
position, fitted on the training seasons' decile bins.

    python3 -m research.nfl_matchup_rushcv extract --frame <frame with 2023-2025 rows>   (before register only)
    python3 -m research.nfl_matchup_rushcv plumbing --out <scratch dir>                  (scrambled outcomes)
    python3 -m research.nfl_matchup_rushcv register
    python3 -m research.nfl_matchup_rushcv choose                                        (fit 2023-24, gate 2025)
    python3 -m research.nfl_matchup_rushcv judge --props <props logs> --sleeper <dir>    (ONCE, weeks 8-10)

Self-contained on purpose (its own numerics; inputs a committed extract of
the frame's 2023-2025 Sleeper lines and box scores, the committed coefs and
validate JSONs, the sim-history props logs and Sleeper's stats files), so a
later edit to the model's modules cannot change what the registered code
computes. `register` stores this file's bytes beside its prereg record; every
later command refuses when the file or the rule block no longer hashes to
what was registered.
"""
import argparse
import csv
import gzip
import hashlib
import json
import math
import os
import shutil
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
os.environ.setdefault("VIGIL_NO_BG", "1")

MODEL_DIR = os.path.join(REPO, "research", "data", "nfl_matchup_model")
OUT = os.path.join(MODEL_DIR, "rush_cv")
EXTRACT = os.path.join(OUT, "rows_2023_2025.csv.gz")
PREREG = os.path.join(OUT, "prereg.json")
CODE_COPY = os.path.join(OUT, "code")
REPORT = os.path.join(REPO, "research", "reports", "nfl_matchup_model.md")
RULE_A, RULE_B = "<!-- RUSHCV-PREREG-START -->", "<!-- RUSHCV-PREREG-END -->"
SELF = "research/nfl_matchup_rushcv.py"
DEPS = ("kalshi_nfl.py",)        # name / team keys for the 2026 matching: hashed, reported when they drift

# ---- the rule's constants (the RUSHCV block states each) ------------------------------
KS = (10, 15, 25, 40, 50, 60, 75, 100)     # the market's thresholds plus 10+ (Kalshi lists it)
EPS = 1e-6
MIN_GAIN = 0.0005                          # nats per threshold (R3's floor)
FIT_POS = ("RB", "QB", "WR")               # the rushing markets' fitting positions
CHANGED = ("RB", "QB")                     # the scored positions; WR keeps its single CV
CV_CLAMP = (0.25, 4.0)                     # never binds inside 2023-24's bins (0.48-3.1)
MIN_BIN_POS = 10                           # positive outcomes a decile bin needs
FIT_A, FIT_B, VAL = (2023, 2024), (2023, 2024, 2025), 2025
JUDGE_WEEKS, EXTEND_TO, MIN_GAMES = (8, 9, 10), 12, 30
MAX_UNMATCHED = 0.10
COLS = ("season", "week", "game_id", "pos", "sl_rush_att", "sl_rush_yd", "y_rush_att", "y_rush_yd")


def log(msg):
    print(msg, flush=True)


def sha_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha_file(path):
    with open(path, "rb") as fh:
        return sha_bytes(fh.read())


# ---- numerics (copies of research/nfl_matchup_fit's, so later edits there change nothing here) ----
def nb_p0(mu, alpha):
    mu = np.maximum(np.asarray(mu, float), 1e-12)
    if alpha < 1e-6:
        return np.exp(-mu)
    r = 1.0 / alpha
    return np.exp(r * np.log(r / (r + mu)))


def gammaq(a, x):
    """Regularised upper incomplete gamma Q(a, x), vectorised in both."""
    a, x = np.broadcast_arrays(np.asarray(a, float), np.asarray(x, float))
    a, x = a.astype(float).copy(), x.astype(float).copy()
    out = np.ones_like(x)
    pos = x > 0
    lg = np.zeros_like(x)
    lg[pos] = -x[pos] + a[pos] * np.log(x[pos]) - np.array([math.lgamma(v) for v in a[pos]])
    ser = pos & (x < a + 1)
    if ser.any():
        aa, xx = a[ser], x[ser]
        ap, term = aa.copy(), 1.0 / aa
        tot = term.copy()
        for _ in range(500):
            ap += 1.0
            term *= xx / ap
            tot += term
            if np.all(np.abs(term) < np.abs(tot) * 1e-15):
                break
        out[ser] = 1.0 - tot * np.exp(lg[ser])
    cf = pos & ~(x < a + 1)
    if cf.any():
        aa, xx = a[cf], x[cf]
        b = xx + 1.0 - aa
        c = np.full_like(b, 1.0 / 1e-300)
        d = 1.0 / b
        h = d.copy()
        for i in range(1, 500):
            an = -i * (i - aa)
            b = b + 2.0
            d = an * d + b
            d = np.where(np.abs(d) < 1e-300, 1e-300, d)
            c = b + an / c
            c = np.where(np.abs(c) < 1e-300, 1e-300, c)
            d = 1.0 / d
            dl = d * c
            h *= dl
            if np.all(np.abs(dl - 1.0) < 1e-15):
                break
        out[cf] = np.exp(lg[cf]) * h
    return np.clip(out, 0.0, 1.0)


def p_ge(p0, mu, cv, k):
    """P(Y >= k): point mass p0 at 0, gamma positive part with mean mu / (1 - p0)
    and coefficient of variation cv, whole yards (k - 0.5)."""
    p0 = np.clip(p0, 0.0, 0.999)
    m_pos = np.maximum(mu, 1e-6) / (1.0 - p0)
    if k <= 0:
        return np.ones_like(m_pos)
    cv = np.broadcast_to(np.asarray(cv, float), m_pos.shape)
    return (1.0 - p0) * gammaq(1.0 / (cv * cv), (k - 0.5) / (m_pos * cv * cv))


def row_ll(p0, mu, cv, y):
    tot = np.zeros(len(y))
    for k in KS:
        p = np.clip(p_ge(p0, mu, cv, k), EPS, 1.0 - EPS)
        hit = (y >= k).astype(float)
        tot -= hit * np.log(p) + (1.0 - hit) * np.log(1.0 - p)
    return tot


def clustered(delta, games):
    """(mean, SE) of a per-row difference clustered by game (G/(G-1) factor)."""
    n = len(delta)
    if n == 0:
        return float("nan"), float("nan")
    mean = float(np.mean(delta))
    ug, inv = np.unique(np.asarray(games, dtype=object).astype(str), return_inverse=True)
    sums = np.bincount(inv, weights=delta - mean)
    g = len(ug)
    return mean, float(math.sqrt((g / max(1, g - 1)) * np.sum(sums ** 2) / (n * n)))


# ---- the fits ------------------------------------------------------------------------
def read_rows(path=EXTRACT):
    with gzip.open(path, "rt", newline="") as fh:
        rows = list(csv.DictReader(fh))
    out = {"pos": np.array([r["pos"] for r in rows], dtype=object),
           "game_id": np.array([r["game_id"] for r in rows], dtype=object)}
    for c in ("season", "week", "sl_rush_att", "sl_rush_yd", "y_rush_att", "y_rush_yd"):
        out[c] = np.array([float(r[c]) if r[c] not in ("", None) else np.nan for r in rows])
    return out


def fit_alpha(R, seasons):
    """Carries' NegBin alpha by moments per position (fit_dispersion's)."""
    out = {}
    s = np.isin(R["season"].astype(int), seasons) & np.isin(R["pos"], FIT_POS) & (R["sl_rush_att"] > 0)
    for p in FIT_POS:
        sel = s & (R["pos"] == p)
        if sel.sum() < 30:
            continue
        y, m = np.nan_to_num(R["y_rush_att"][sel]), R["sl_rush_att"][sel]
        out[p] = max(0.0, float(np.sum((y - m) ** 2 - m) / np.sum(m * m)))
    return out


def _cv(y, m_pos):
    ok = y > 0
    return float(math.sqrt(np.sum((y[ok] - m_pos[ok]) ** 2) / np.sum(m_pos[ok] ** 2)))


def _fit_rows(R, seasons, p, alpha):
    s = (np.isin(R["season"].astype(int), seasons) & (R["pos"] == p) & (R["sl_rush_yd"] > 0)
         & (R["sl_rush_att"] > 0))
    y, mu, cnt = np.nan_to_num(R["y_rush_yd"][s]), R["sl_rush_yd"][s], R["sl_rush_att"][s]
    p0 = nb_p0(cnt, alpha)
    return y, mu, mu / (1.0 - np.clip(p0, 0.0, 0.999))


def fit_single(R, seasons, alpha):
    """A: the shipped form, one positive-part CV per position."""
    out = {}
    for p in FIT_POS:
        if p not in alpha:
            continue
        y, _mu, m_pos = _fit_rows(R, seasons, p, alpha[p])
        if len(y) >= 30:
            out[p] = _cv(y, m_pos)
    return out


def fit_line(R, seasons, alpha):
    """B: cv = c x line^d per changed position -- deciles of Sleeper's yards
    line, each bin's moment CV of the positive part (bins with >= 10 positive
    outcomes), least squares of log CV on log(mean line in the bin)."""
    out = {}
    for p in CHANGED:
        y, mu, m_pos = _fit_rows(R, seasons, p, alpha[p])
        q = np.quantile(mu, np.linspace(0, 1, 11))
        xs, ys, bins = [], [], []
        for lo, hi in zip(q[:-1], q[1:]):
            b = (mu >= lo) & (mu <= hi)
            if (b & (y > 0)).sum() >= MIN_BIN_POS:
                cvb = _cv(y[b], m_pos[b])
                xs.append(math.log(float(np.mean(mu[b]))))
                ys.append(math.log(cvb))
                bins.append({"lo": float(lo), "hi": float(hi), "n": int(b.sum()), "cv": cvb})
        d, lc = np.polyfit(xs, ys, 1)
        out[p] = {"cv_c": float(math.exp(lc)), "cv_d": float(d), "bins": bins}
    return out


def cv_of(pos, mu, single, line=None):
    """Per-row CV: the candidate's c x max(line, 1)^d for the changed positions
    (clamped), else the single CV."""
    cv = np.array([single.get(p, np.nan) for p in pos], float)
    if line:
        for p, prm in line.items():
            s = pos == p
            cv[s] = np.clip(prm["cv_c"] * np.maximum(mu[s], 1.0) ** prm["cv_d"], *CV_CLAMP)
    return cv


def priced(pos, cnt, mu):
    """R3's priced rushing universe: RB with 5+ projected carries, QB with 2+, a yards line."""
    return (((pos == "RB") & (cnt >= 5)) | ((pos == "QB") & (cnt >= 2))) & (mu > 0)


def compare(pos, cnt, mu, y, games, alpha, single, line):
    a = np.array([alpha[p] for p in pos])
    p0 = np.array([float(nb_p0(c, ai)) for c, ai in zip(cnt, a)])
    ll_a = row_ll(p0, mu, cv_of(pos, mu, single), y)
    ll_b = row_ll(p0, mu, cv_of(pos, mu, single, line), y)
    d, se = clustered(ll_b - ll_a, games)
    out = {"n": int(len(y)), "games": int(len(set(games.tolist()))), "d_ll": d, "se": se,
           "ll_single": float(np.mean(ll_a)), "ll_line": float(np.mean(ll_b)), "by_pos": {}, "calibration": {}}
    for p in CHANGED:
        s = pos == p
        if s.any():
            dp, sp = clustered((ll_b - ll_a)[s], games[s])
            out["by_pos"][p] = {"n": int(s.sum()), "d_ll": dp, "se": sp}
    for p, lo, hi in (("RB", 20, 30), ("QB", 10, 20), ("QB", 20, 30), ("RB", 50, 1e9)):
        s = (pos == p) & (mu >= lo) & (mu < hi)
        if not s.any():
            continue
        cell = {"n": int(s.sum())}
        for k in (10, 15, 25, 40):
            cell[f"{k}+"] = {"hit": float(np.mean(y[s] >= k)),
                             "single": float(np.mean(p_ge(p0[s], mu[s], cv_of(pos[s], mu[s], single), k))),
                             "line": float(np.mean(p_ge(p0[s], mu[s], cv_of(pos[s], mu[s], single, line), k)))}
        out["calibration"][f"{p} lines {lo}-{hi if hi < 1e8 else '+'}"] = cell
    return out


# ---- registration ----------------------------------------------------------------------
def rule_block():
    with open(REPORT, encoding="utf-8") as fh:
        txt = fh.read()
    a, b = txt.find(RULE_A), txt.find(RULE_B)
    if a < 0 or b <= a or txt.count(RULE_A) != 1 or txt.count(RULE_B) != 1:
        raise SystemExit("the report has no single RUSHCV block")
    return txt[a:b + len(RULE_B)]


def check_registered():
    if not os.path.exists(PREREG):
        raise SystemExit("not registered: run `register` after writing the RUSHCV block")
    with open(PREREG) as fh:
        rec = json.load(fh)
    h = sha_bytes(rule_block().encode("utf-8"))
    if h != rec["sha256"]:
        raise SystemExit(f"the RUSHCV block changed since registration ({h} != {rec['sha256']})")
    if sha_file(os.path.join(REPO, SELF)) != rec["code"][SELF]:
        raise SystemExit(f"{SELF} changed since registration: run the stored bytes ({CODE_COPY}) instead")
    if sha_file(EXTRACT) != rec["extract_sha256"]:
        raise SystemExit("the 2023-2025 extract changed since registration")
    drift = {f: sha_file(os.path.join(REPO, f)) for f in DEPS if sha_file(os.path.join(REPO, f)) != rec["deps"].get(f)}
    return rec, drift


def cmd_register(a):
    if os.path.exists(PREREG) and not a.amend:
        raise SystemExit("already registered; --amend re-registers and keeps the old hash (the report must say why)")
    old = None
    if os.path.exists(PREREG):
        with open(PREREG) as fh:
            old = json.load(fh)
    block = rule_block()
    rec = {"sha256": sha_bytes(block.encode("utf-8")), "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "code": {SELF: sha_file(os.path.join(REPO, SELF))},
           "deps": {f: sha_file(os.path.join(REPO, f)) for f in DEPS},
           "extract_sha256": sha_file(EXTRACT), "superseded": (old or {}).get("superseded", []) + ([old] if old else [])}
    os.makedirs(CODE_COPY, exist_ok=True)
    # the bytes themselves, beside the record (review F6: the main rule's registered
    # nfl_matchup.py was not preserved). .txt so no tool imports or lints the copy
    shutil.copyfile(os.path.join(REPO, SELF), os.path.join(CODE_COPY, os.path.basename(SELF) + ".txt"))
    with open(os.path.join(CODE_COPY, "rule_block.md.txt"), "w", encoding="utf-8") as fh:
        fh.write(block)
    write_json(PREREG, rec)
    log(f"[rushcv] registered {rec['sha256']} at {rec['utc']}")


def write_json(path, obj):
    def clean(o):
        if isinstance(o, dict):
            return {k: clean(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [clean(v) for v in o]
        if isinstance(o, (np.floating, float)):
            return float(o) if math.isfinite(float(o)) else None
        if isinstance(o, np.integer):
            return int(o)
        return o
    with open(path + ".tmp", "w") as fh:
        json.dump(clean(obj), fh, indent=1)
    os.replace(path + ".tmp", path)


# ---- commands ----------------------------------------------------------------------
def cmd_extract(a):
    if os.path.exists(PREREG):
        raise SystemExit("registered: the extract is frozen")
    with gzip.open(a.frame, "rt", newline="") as fh:
        rows = [r for r in csv.DictReader(fh) if int(r["season"]) in FIT_B and r["pos"] in FIT_POS
                and (float(r["sl_rush_att"] or 0) > 0 or float(r["sl_rush_yd"] or 0) > 0)]
    os.makedirs(OUT, exist_ok=True)
    import io
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(COLS)
    for r in rows:
        w.writerow([r[c] for c in COLS])
    with open(EXTRACT, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as fh:
        fh.write(buf.getvalue().encode("utf-8"))      # mtime 0: the same rows give the same bytes
    log(f"[rushcv] {len(rows)} rows -> {EXTRACT} ({sha_file(EXTRACT)})")


def _baseline_check(alpha, single, disp):
    """The harness's single-CV fit must reproduce the committed one exactly."""
    bad = []
    for p in FIT_POS:
        if abs(alpha[p] - disp["car"][p]["alpha"]) > 1e-9 or abs(single[p] - disp["rush_yd"][p]["cv"]) > 1e-9:
            bad.append((p, alpha[p], disp["car"][p]["alpha"], single[p], disp["rush_yd"][p]["cv"]))
    if bad:
        raise SystemExit(f"the harness does not reproduce the committed dispersion: {bad}")


def gate(R, scramble_seed=None):
    alpha = fit_alpha(R, FIT_A)
    single = fit_single(R, FIT_A, alpha)
    line = fit_line(R, FIT_A, alpha)
    s = (R["season"].astype(int) == VAL) & priced(R["pos"], R["sl_rush_att"], R["sl_rush_yd"])
    y = np.nan_to_num(R["y_rush_yd"][s])
    if scramble_seed is not None:      # plumbing: 2025 outcomes replaced by 2023 draws of the same position
        rng = np.random.default_rng(scramble_seed)
        for p in CHANGED:
            src = np.nan_to_num(R["y_rush_yd"][(R["season"].astype(int) == 2023) & (R["pos"] == p)])
            k = R["pos"][s] == p
            y[k] = rng.choice(src, size=int(k.sum()))
    res = compare(R["pos"][s], R["sl_rush_att"][s], R["sl_rush_yd"][s], y, R["game_id"][s], alpha, single, line)
    res["passes"] = bool(res["d_ll"] < -2.0 * res["se"] and res["d_ll"] < -MIN_GAIN * len(KS))
    return {"fit_seasons": list(FIT_A), "alpha": alpha, "single_cv": single, "line_cv": line, "gate_2025": res}


def cmd_plumbing(a):
    R = read_rows()
    out = gate(R, scramble_seed=7)
    os.makedirs(a.out, exist_ok=True)
    write_json(os.path.join(a.out, "rushcv_plumbing.json"), out)
    log("[rushcv] plumbing (scrambled 2025): " + json.dumps({k: out["gate_2025"][k] for k in ("n", "d_ll", "se", "passes")}))


def cmd_choose(a):
    rec, drift = check_registered()
    R = read_rows()
    out = gate(R)
    with open(os.path.join(MODEL_DIR, "validate.json")) as fh:
        _baseline_check(out["alpha"], out["single_cv"], json.load(fh)["dispersion"])
    out.update(prereg=rec["sha256"], dep_drift=drift, utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    write_json(os.path.join(OUT, "choose.json"), out)
    g = out["gate_2025"]
    log(f"[rushcv] 2025 gate: d {g['d_ll']:+.5f} se {g['se']:.5f} n {g['n']} -> {'PASS' if g['passes'] else 'FAIL'}")


# ---- 2026: frozen props-log captures and Sleeper's box -------------------------------------
def kickoffs(lg, canon):
    """{team: (kickoff epoch, "AWAY-HOME" game key)} from the props log's
    weather block (home, away, kickoff) and games block (the Kalshi event
    suffix's two codes, its kickoff). A team given two kickoffs more than an
    hour apart is ambiguous and drops out."""
    seen, teams = {}, set()
    for rec in (lg.get("sleeper") or {}).values():
        teams.add(canon(rec.get("team")))
    pairs = []
    for w in (lg.get("weather") or {}).values():
        if w.get("home") and w.get("away") and w.get("kickoff"):
            pairs.append((canon(w["away"]), canon(w["home"]), int(w["kickoff"])))
    for sfx, g in (lg.get("games") or {}).items():
        code = sfx[7:]
        for i in range(2, len(code) - 1):
            a, h = canon(code[:i]), canon(code[i:])
            if a in teams and h in teams and g.get("kickoff"):
                pairs.append((a, h, int(g["kickoff"])))
    for a, h, k in pairs:
        key = "-".join(sorted((a, h)))
        for t in (a, h):
            seen.setdefault(t, []).append((k, key))
    out = {}
    for t, v in seen.items():
        ks = [k for k, _key in v]
        if max(ks) - min(ks) <= 3600 and len({key for _k, key in v}) == 1:
            out[t] = (min(ks), v[0][1])
    return out


def rows_2026(props_paths, sleeper_dir, weeks):
    import kalshi_nfl
    canon, norm = kalshi_nfl._canon, kalshi_nfl._norm
    out = {k: [] for k in ("pos", "sl_rush_att", "sl_rush_yd", "y_rush_yd", "game_id", "week")}
    audit = {"weeks": {}}
    for p in props_paths:
        with open(p, encoding="utf-8") as fh:
            lg = json.load(fh)
        w = int(lg["week"])
        if int(lg.get("season") or 0) != 2026 or w not in weeks:
            continue
        kick = kickoffs(lg, canon)
        sp = os.path.join(sleeper_dir, f"stats_2026_{w}.json")
        with open(sp, encoding="utf-8") as fh:
            box = json.load(fh)
        by_key, box_teams = {}, set()
        for r in box:
            pl = r.get("player") or {}
            t = canon(r.get("team") or pl.get("team"))
            box_teams.add(t)
            by_key[(norm(f"{pl.get('first_name', '')} {pl.get('last_name', '')}"), t)] = r.get("stats") or {}
        a = {"captured": 0, "after_kickoff": 0, "no_kickoff": 0, "team_not_in_box": 0, "scored": 0, "no_box_row": 0}
        for nm, rec in (lg.get("sleeper") or {}).items():
            pos, st = rec.get("pos"), rec.get("stats") or {}
            if pos not in CHANGED:
                continue
            cnt, mu = float(st.get("rush_att") or 0.0), float(st.get("rush_yd") or 0.0)
            if not priced(np.array([pos]), np.array([cnt]), np.array([mu]))[0]:
                continue
            a["captured"] += 1
            t = canon(rec.get("team"))
            k, gkey = kick.get(t) or (None, None)
            if k is None:
                a["no_kickoff"] += 1
                continue
            if not float(rec.get("ts") or 0.0) < k:       # frozen: content time strictly before kickoff
                a["after_kickoff"] += 1
                continue
            if t not in box_teams:
                a["team_not_in_box"] += 1
                continue
            b = by_key.get((nm, t))
            if b is None:
                a["no_box_row"] += 1            # projected, no box line: did not play -> 0 yards
            out["pos"].append(pos)
            out["sl_rush_att"].append(cnt)
            out["sl_rush_yd"].append(mu)
            out["y_rush_yd"].append(float((b or {}).get("rush_yd") or 0.0))
            out["game_id"].append(f"{w}:{gkey}")
            out["week"].append(w)
            a["scored"] += 1
        audit["weeks"][w] = a
    R = {k: np.array(v, dtype=object if k in ("pos", "game_id") else float) for k, v in out.items()}
    return R, audit


def window(props_paths, first_weeks):
    """The judgement's weeks, fixed by COUNTING games before any outcome is read:
    8-10, extended a week at a time to 12 until 30+ games carry a frozen row."""
    import kalshi_nfl
    games = {}
    for p in props_paths:
        with open(p, encoding="utf-8") as fh:
            lg = json.load(fh)
        w = int(lg["week"])
        kick = kickoffs(lg, kalshi_nfl._canon)
        g = set()
        for rec in (lg.get("sleeper") or {}).values():
            st = rec.get("stats") or {}
            t = kalshi_nfl._canon(rec.get("team"))
            if rec.get("pos") in CHANGED and t in kick and float(rec.get("ts") or 0) < kick[t][0] and priced(
                    np.array([rec["pos"]]), np.array([float(st.get("rush_att") or 0)]),
                    np.array([float(st.get("rush_yd") or 0)]))[0]:
                g.add(kick[t][1])
        games[w] = len(g)
    weeks = list(first_weeks)
    while sum(games.get(w, 0) for w in weeks) < MIN_GAMES and weeks[-1] < EXTEND_TO:
        weeks.append(weeks[-1] + 1)
    return tuple(weeks), games


def cmd_judge(a):
    lock = os.path.join(OUT, "judge_lock.json")
    if not a.plumbing:
        rec, drift = check_registered()
        if a.weeks:
            raise SystemExit("the judgement's weeks are the rule's (8-10, extended by count only); --weeks is for plumbing")
        if os.path.exists(lock) and not a.force:
            raise SystemExit("the 2026 judgement has run once (judge_lock.json); --force re-runs it and the report must say so")
        if not os.path.exists(os.path.join(OUT, "choose.json")):
            raise SystemExit("no choose.json: the 2025 gate runs first (`choose`)")
        with open(os.path.join(OUT, "choose.json")) as fh:
            ch = json.load(fh)
        if not ch["gate_2025"]["passes"]:
            write_json(os.path.join(OUT, "judge.json"), {"skipped": "the 2025 gate failed: nothing to judge",
                                                         "prereg": rec["sha256"]})
            log("[rushcv] the 2025 gate failed: nothing to judge")
            return
    else:
        rec, drift = {"sha256": None}, {}
    weeks, counts = window(a.props, a.weeks or JUDGE_WEEKS)
    R = read_rows()
    alpha = fit_alpha(R, FIT_B)
    single = fit_single(R, FIT_B, alpha)
    with open(os.path.join(MODEL_DIR, "coefs.json")) as fh:
        _baseline_check(alpha, single, json.load(fh)["dispersion"])   # A IS production's spread
    line = fit_line(R, FIT_B, alpha)
    T, audit = rows_2026(a.props, a.sleeper, weeks)
    if a.plumbing:                      # code check: outcomes replaced by 2025 draws of the same position
        rng = np.random.default_rng(11)
        for p in CHANGED:
            src = np.nan_to_num(R["y_rush_yd"][(R["season"].astype(int) == 2025) & (R["pos"] == p)])
            k = T["pos"] == p
            T["y_rush_yd"][k] = rng.choice(src, size=int(k.sum()))
    n_rows = int(len(T["pos"]))
    unmatched = sum(w["no_box_row"] for w in audit["weeks"].values())
    out = {"prereg": rec["sha256"], "dep_drift": drift, "weeks": list(weeks), "game_counts": counts, "audit": audit,
           "alpha": alpha, "single_cv": single, "line_cv": line, "plumbing": bool(a.plumbing)}
    if n_rows == 0 or unmatched > MAX_UNMATCHED * n_rows:
        out["refused"] = f"{unmatched} of {n_rows} scored rows have no box line (> {MAX_UNMATCHED:.0%}): a data problem"
    else:
        res = compare(T["pos"], T["sl_rush_att"], T["sl_rush_yd"], T["y_rush_yd"], T["game_id"], alpha, single, line)
        res["passes"] = bool(res["d_ll"] < 0.0)
        out["judge_2026"] = res
    dest = os.path.join(a.out, "rushcv_judge_plumbing.json") if a.plumbing else os.path.join(OUT, "judge.json")
    if a.plumbing:
        os.makedirs(a.out, exist_ok=True)
    write_json(dest, out)
    if not a.plumbing:
        write_json(lock, {"ran_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "forced": bool(a.force)})
    log("[rushcv] judge: " + json.dumps({k: (out.get("judge_2026") or {}).get(k) for k in ("n", "games", "d_ll", "se", "passes")})
        + (" REFUSED " + out["refused"] if out.get("refused") else ""))


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("extract", "plumbing", "register", "choose", "judge"))
    ap.add_argument("--frame")
    ap.add_argument("--out", default=os.environ.get("NFLM_WORK", "/tmp"))
    ap.add_argument("--amend", action="store_true")
    ap.add_argument("--props", nargs="*", default=[])
    ap.add_argument("--sleeper")
    ap.add_argument("--weeks", nargs="*", type=int)
    ap.add_argument("--plumbing", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    {"extract": cmd_extract, "plumbing": cmd_plumbing, "register": cmd_register, "choose": cmd_choose,
     "judge": cmd_judge}[a.cmd](a)


if __name__ == "__main__":
    main(sys.argv[1:])
