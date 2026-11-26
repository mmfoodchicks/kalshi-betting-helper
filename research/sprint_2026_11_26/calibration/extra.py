import os
import sys, json, csv, gzip, math, collections
import numpy as np
sys.path.insert(0, '/home/user/kalshi-betting-helper')
S = os.path.join(os.path.dirname(os.path.abspath(__file__)), '')  # data files beside the script (not committed)
import nfl_matchup as M, prop_dist as PD
coefs = M.load_coefs(); out = []; P = lambda *a: out.append(' '.join(str(x) for x in a))
ch = json.load(open('/home/user/kalshi-betting-helper/research/data/nfl_matchup_model/rush_cv/choose.json'))
def dist(pos, L, car, linecv=False):
    prm = M._disp(coefs, 'rush_yd', pos); cp = M._disp(coefs, 'car', pos)
    if prm is None or cp is None or L <= 0 or car <= 0: return None
    p0 = min(0.999, max(0.0, PD.NegBin(car, cp['alpha']).pmf(0))); mp = L / (1 - p0)
    cv = prm['cv']
    if linecv and pos in ch['line_cv']:
        c = ch['line_cv'][pos]; cv = min(4.0, max(0.25, c['cv_c'] * max(L, 1.0) ** c['cv_d']))
    return PD.Yards(p0, mp, cv * mp, M.yards_family(coefs, 'rush_yd'))
def cl(v, g, w=None):
    v = np.asarray(v, float); w = np.ones(len(v)) if w is None else np.asarray(w, float)
    gi = np.unique(g, return_inverse=True)[1]; mu = (w * v).sum() / w.sum(); s = np.bincount(gi, w * (v - mu)); G = len(s)
    return mu, math.sqrt(G / (G - 1) * (s ** 2).sum()) / w.sum(), G
# (a) 2023-2025 rows: the report's cell, with clustered SE
rows = [r for r in csv.DictReader(gzip.open('/home/user/kalshi-betting-helper/research/data/nfl_matchup_model/rush_cv/rows_2023_2025.csv.gz', 'rt'))]
P('## 3a. 2023-2025 revised rows (rush_cv/rows_2023_2025.csv.gz): hit minus model at fixed thresholds, game-clustered SE')
P('| pos | Sleeper line | seasons | n | k | hit | model (single CV) | gap (SE) | model (line CV) | gap (SE) |'); P('|---|---|---|---|---|---|---|---|---|---|')
for pos, lo, hi, ks in (('RB', 20, 30, (10, 15, 25, 40)), ('RB', 10, 20, (10, 15, 25, 40)), ('QB', 10, 20, (10, 15, 25, 40)), ('QB', 20, 30, (10, 25, 40))):
    for seas in (('2023', '2024'), ('2025',)):
        rr = [r for r in rows if r['pos'] == pos and r['season'] in seas and lo <= float(r['sl_rush_yd'] or 0) < hi and float(r['sl_rush_att'] or 0) > 0 and r['y_rush_yd'] != '']
        ds = [(dist(pos, float(r['sl_rush_yd']), float(r['sl_rush_att'])), dist(pos, float(r['sl_rush_yd']), float(r['sl_rush_att']), True)) for r in rr]
        for k in ks:
            y = np.array([float(r['y_rush_yd']) >= k for r in rr], float); p1 = np.array([d[0].sf_ge(k) for d in ds]); p2 = np.array([d[1].sf_ge(k) for d in ds])
            g = [r['game_id'] for r in rr]; a = cl(y - p1, g); b = cl(y - p2, g)
            P(f'| {pos} | {lo}-{hi} | {"+".join(seas)} | {len(rr)} | {k}+ | {y.mean():.3f} | {p1.mean():.3f} | {a[0]:+.3f} ({a[1]:.3f}) | {p2.mean():.3f} | {b[0]:+.3f} ({b[1]:.3f}) |')
# (b) the registered line-CV candidate on 2026 weeks 6-11 Kalshi rungs (descriptive; the registered weeks 8-10 judgement was skipped)
R = [r for r in csv.DictReader(open(S + 'rows_nfl_w06_w11.csv')) if r['stat'] == 'rush_yd' and r['pos'] in ('RB', 'QB')]
E_ = 1e-4; c = lambda p: min(1 - E_, max(E_, p)); L_ = lambda p, y: -(y * math.log(c(p)) + (1 - y) * math.log(1 - c(p)))
for r in R:
    d1 = dist(r['pos'], float(r['line']), float(r['car'])); d2 = dist(r['pos'], float(r['line']), float(r['car']), True)
    r['p1'] = d1.sf_ge(int(r['k'])); r['p2'] = d2.sf_ge(int(r['k'])); r['yy'] = int(r['y'])
    assert abs(r['p1'] - float(r['p_model'])) < 2e-4
P('\n## 3b. The registered line-dependent CV (fitted 2023-24, never shipped) on 2026 Kalshi-listed rushing rungs. DESCRIPTIVE ONLY: the registered weeks 8-10 judgement was skipped when the 2025 gate failed; this read is not that judgement and decides nothing.')
P('| rows | n | games | single CV LL | line CV LL | Kalshi LL | line - single (SE) | Kalshi - line (SE) |'); P('|---|---|---|---|---|---|---|---|')
for nm, f in (('RB+QB wk6-11', lambda r: True), ('RB+QB wk8-11', lambda r: int(r['week']) >= 8), ('RB wk6-11', lambda r: r['pos'] == 'RB'), ('QB wk6-11', lambda r: r['pos'] == 'QB'),
              ('RB, line < 30', lambda r: r['pos'] == 'RB' and float(r['line']) < 30), ('RB, line >= 30', lambda r: r['pos'] == 'RB' and float(r['line']) >= 30)):
    rr = [r for r in R if f(r)]; g = [r['game'] for r in rr]
    a = [L_(r['p1'], r['yy']) for r in rr]; b = [L_(r['p2'], r['yy']) for r in rr]; k = [L_(float(r['p_mkt']), r['yy']) for r in rr]
    d = cl(np.array(b) - np.array(a), g); e = cl(np.array(k) - np.array(b), g)
    P(f'| {nm} | {len(rr)} | {d[2]} | {np.mean(a):.4f} | {np.mean(b):.4f} | {np.mean(k):.4f} | {d[0]:+.4f} ({d[1]:.4f}) | {e[0]:+.4f} ({e[1]:.4f}) |')
# (c) live record aggregates: binned calibration slope (approximate)
P('\n## 1e. Live Matchups record (aggregates only): calibration slope from the ten published bins. APPROXIMATE: a logistic fit to bin means, SE = binomial SE x sqrt(n / n_eff) (ladder design effect), not a game-clustered SE.')
P('| sport | stat | graded | games | dates | slope b | approx SE | intercept a | top bins (p>=0.8): p / hit / n | bottom bin (p<0.1): p / hit / n |'); P('|---|---|---|---|---|---|---|---|---|---|')
for sp in ('nfl', 'nba'):
    d = json.load(open(S + f'mx-record-{sp}-latest.json'))
    for st, s in d['stats'].items():
        cal = s['calibration']; x = np.array([math.log(b['p'] / (1 - b['p'])) for b in cal]); n = np.array([b['n'] for b in cal], float); h = np.array([b['hit'] for b in cal])
        X = np.c_[np.ones(len(x)), x]; be = np.array([0.0, 1.0])
        for _ in range(50):
            q = 1 / (1 + np.exp(-X @ be)); H = (X * (n * q * (1 - q))[:, None]).T @ X; be = be + np.linalg.solve(H, X.T @ (n * (h - q)))
        de = s['first']['n'] / max(1, s['first']['n_eff']); se = np.sqrt(np.diag(np.linalg.inv(H)) * de)
        top = [b for b in cal if b['p'] >= 0.8]; tn = sum(b['n'] for b in top)
        tp = sum(b['p'] * b['n'] for b in top) / tn if tn else float('nan'); th = sum(b['hit'] * b['n'] for b in top) / tn if tn else float('nan')
        b0 = cal[0]
        P(f"| {sp} | {st} | {s['graded']} | {s['games']} | {s['days']} | {be[1]:.3f} | {se[1]:.3f} | {be[0]:+.3f} | {tp:.3f} / {th:.3f} / {tn} | {b0['p']:.3f} / {b0['hit']:.3f} / {b0['n']} |")
open(S + 'extra_tables.md', 'w').write('\n'.join(out) + '\n'); print('\n'.join(out))
