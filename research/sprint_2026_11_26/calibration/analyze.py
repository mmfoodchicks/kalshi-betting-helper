import os
import csv, math, json, sys, collections
import numpy as np
S = os.path.join(os.path.dirname(os.path.abspath(__file__)), '')  # data files beside the script (not committed)
R = list(csv.DictReader(open(S + 'rows_nfl_w06_w11.csv')))
for r in R:
    for k in ('week', 'k', 'y'): r[k] = int(r[k])
    for k in ('p_model', 'p_mkt', 'line', 'actual', 'car'): r[k] = float(r[k] or 0)
EPS = 1e-4
A = lambda key: np.array([r[key] for r in R])
wk, y, pm, pk = A('week'), A('y').astype(float), np.clip(A('p_model'), EPS, 1 - EPS), np.clip(A('p_mkt'), EPS, 1 - EPS)
stat, pos, game, kk, line = A('stat'), A('pos'), A('game'), A('k'), A('line')
gid = np.unique(game, return_inverse=True)[1]
lad = np.unique(np.array([f"{r['game']}|{r['player']}|{r['stat']}" for r in R]), return_inverse=True)[1]
wl = 1.0 / np.bincount(lad)[lad]           # a ladder counts once (mx_record's weighting)
ll = lambda p: -(y * np.log(p) + (1 - y) * np.log(1 - p))
lg = lambda p: np.log(p / (1 - p))
def cmean(v, m, w=None):
    """mean of v over mask m and its game-clustered SE (ratio estimator)."""
    w = np.ones(len(v)) if w is None else w
    g = gid[m]; ww = w[m]; vv = v[m]
    mu = (ww * vv).sum() / ww.sum()
    s = np.bincount(g, ww * (vv - mu), minlength=gid.max() + 1); s = s[np.bincount(g, minlength=gid.max() + 1) > 0]
    G = len(s)
    return mu, math.sqrt(G / max(G - 1, 1) * (s ** 2).sum()) / ww.sum(), G
def slope(p, m, w=None):
    """logit(y) = a + b logit(p): weighted IRLS, game-clustered sandwich SE."""
    w = np.ones(len(p)) if w is None else w
    X = np.c_[np.ones(m.sum()), lg(p[m])]; yy = y[m]; ww = w[m]; b = np.array([0.0, 1.0])
    for _ in range(50):
        q = 1 / (1 + np.exp(-X @ b)); H = (X * (ww * q * (1 - q))[:, None]).T @ X
        st = np.linalg.solve(H, X.T @ (ww * (yy - q))); b += st
        if abs(st).max() < 1e-10: break
    q = 1 / (1 + np.exp(-X @ b)); U = X * (ww * (yy - q))[:, None]
    g = gid[m]; Ug = np.zeros((gid.max() + 1, 2)); np.add.at(Ug, g, U); Ug = Ug[np.bincount(g, minlength=gid.max() + 1) > 0]
    G = len(Ug); Hi = np.linalg.inv((X * (ww * q * (1 - q))[:, None]).T @ X)
    V = Hi @ (Ug.T @ Ug) @ Hi * G / (G - 1)
    return b, np.sqrt(np.diag(V)), G
out = []
P = lambda *a: out.append(' '.join(str(x) for x in a))
allm = np.ones(len(R), bool)
new = wk >= 7
P('# NFL rebuilt rows: n', len(R), 'games', gid.max() + 1, 'ladders', lad.max() + 1, '| weeks 7-11 n', new.sum())
m6 = wk == 6
P('check wk6 vs kalshi.json props_log_w06 (2875; 0.5051 / 0.4553): n', m6.sum(), 'model %.4f mkt %.4f' % (ll(pm)[m6].mean(), ll(pk)[m6].mean()))
P('\n## 1a. log loss, model vs Kalshi mid (per rung; diff = model - market, game-clustered SE)')
P('| scope | n | games | hit | model | Kalshi | diff | SE | z | ladder-weighted diff +/- SE |')
P('|---|---|---|---|---|---|---|---|---|---|')
d = ll(pm) - ll(pk)
scopes = [('all wk6-11', allm), ('all wk7-11', new)] + [(s, stat == s) for s in ('pass_yd', 'rec', 'rec_yd', 'rush_yd', 'td')] + \
    [(f'{s}/{p}', (stat == s) & (pos == p)) for s, p in (('rush_yd', 'RB'), ('rush_yd', 'QB'), ('rec', 'RB'), ('rec', 'WR'), ('rec', 'TE'), ('rec_yd', 'RB'), ('rec_yd', 'WR'), ('rec_yd', 'TE'), ('td', 'RB'), ('td', 'WR'), ('td', 'TE'), ('td', 'QB'))]
for nm, m in scopes:
    mu, se, G = cmean(d, m); muw, sew, _ = cmean(d, m, wl)
    P(f'| {nm} | {m.sum()} | {G} | {y[m].mean():.3f} | {ll(pm)[m].mean():.4f} | {ll(pk)[m].mean():.4f} | {mu:+.4f} | {se:.4f} | {mu/se:+.1f} | {muw:+.4f} +/- {sew:.4f} |')
P('\n## 1b. calibration slope: logit(y) = a + b logit(p), b<1 = overconfident (game-clustered SE)')
P('| scope | n | model a | model b (SE) | Kalshi a | Kalshi b (SE) |'); P('|---|---|---|---|---|---|')
bands = [('all', allm), ('long shots p<0.25 (own p)', None), ('middle 0.25-0.75', None), ('favourites p>0.75', None)]
for nm, m in [('all', allm)] + [(s, stat == s) for s in ('pass_yd', 'rec', 'rec_yd', 'rush_yd', 'td')] + [('rush_yd/RB', (stat == 'rush_yd') & (pos == 'RB')), ('rush_yd/QB', (stat == 'rush_yd') & (pos == 'QB'))]:
    bm, sm, _ = slope(pm, m); bk, sk, _ = slope(pk, m)
    P(f'| {nm} | {m.sum()} | {bm[0]:+.3f} ({sm[0]:.3f}) | {bm[1]:.3f} ({sm[1]:.3f}) | {bk[0]:+.3f} ({sk[0]:.3f}) | {bk[1]:.3f} ({sk[1]:.3f}) |')
P('\n## 1c. reliability by decile of each forecaster\'s own probability (mean p / hit rate / n; gap = hit - p with game-clustered SE)')
for nm, m in [('all stats', allm)] + [(s, stat == s) for s in ('pass_yd', 'rec', 'rec_yd', 'rush_yd', 'td')]:
    P(f'\n**{nm}**\n'); P('| decile | model p | model hit | n | gap (SE) | Kalshi p | Kalshi hit | n | gap (SE) |'); P('|---|---|---|---|---|---|---|---|---|')
    for b in range(10):
        cells = []
        for p in (pm, pk):
            mm = m & (np.minimum((p * 10).astype(int), 9) == b)
            if mm.sum() < 20: cells.append(f'insufficient | (n={mm.sum()}) | {mm.sum()} | -'); continue
            mu, se, _ = cmean(y - p, mm)
            cells.append(f'{p[mm].mean():.3f} | {y[mm].mean():.3f} | {mm.sum()} | {mu:+.3f} ({se:.3f})')
        P(f'| {b/10:.1f}-{(b+1)/10:.1f} | ' + ' | '.join(cells) + ' |')
P('\n## 1d. high-probability rungs vs long shots (bands of the MODEL\'s p; both forecasters scored on the same rows)')
P('| stat | band | n | model p | Kalshi p | hit | model gap (SE) | Kalshi gap (SE) | LL diff model-mkt (SE) |'); P('|---|---|---|---|---|---|---|---|---|')
for nm, m in [('all', allm)] + [(s, stat == s) for s in ('pass_yd', 'rec', 'rec_yd', 'rush_yd', 'td')]:
    for bn, lo, hi in (('long shot <0.20', 0, .2), ('0.20-0.50', .2, .5), ('0.50-0.80', .5, .8), ('high >=0.80', .8, 1.01)):
        mm = m & (pm >= lo) & (pm < hi)
        if mm.sum() < 30: P(f'| {nm} | {bn} | insufficient data (n={mm.sum()}) | | | | | | |'); continue
        a, sa, _ = cmean(y - pm, mm); b_, sb, _ = cmean(y - pk, mm); c, sc, _ = cmean(d, mm)
        P(f'| {nm} | {bn} | {mm.sum()} | {pm[mm].mean():.3f} | {pk[mm].mean():.3f} | {y[mm].mean():.3f} | {a:+.3f} ({sa:.3f}) | {b_:+.3f} ({sb:.3f}) | {c:+.4f} ({sc:.4f}) |')

# ---------- 2. why +EV loses
P('\n## 2a. Does the model add anything to the mid? logit(y) = a + b logit(p_mkt) + w [logit(p_model) - logit(p_mkt)]  (w=0: the disagreement is noise; w=1: the model is right)')
def enc(m, w=None):
    w_ = np.ones(len(y)) if w is None else w
    X = np.c_[np.ones(m.sum()), lg(pk[m]), lg(pm[m]) - lg(pk[m])]; yy = y[m]; ww = w_[m]; b = np.array([0, 1.0, 0])
    for _ in range(60):
        q = 1 / (1 + np.exp(-X @ b)); H = (X * (ww * q * (1 - q))[:, None]).T @ X; st = np.linalg.solve(H, X.T @ (ww * (yy - q))); b = b + st
        if abs(st).max() < 1e-10: break
    q = 1 / (1 + np.exp(-X @ b)); U = X * (ww * (yy - q))[:, None]; g = gid[m]
    Ug = np.zeros((gid.max() + 1, 3)); np.add.at(Ug, g, U); Ug = Ug[np.bincount(g, minlength=gid.max() + 1) > 0]; G = len(Ug)
    Hi = np.linalg.inv((X * (ww * q * (1 - q))[:, None]).T @ X); V = Hi @ Ug.T @ Ug @ Hi * G / (G - 1)
    return b, np.sqrt(np.diag(V))
P('| scope | n | b on market (SE) | w on model-minus-market (SE) | w ladder-weighted (SE) |'); P('|---|---|---|---|---|')
for nm, m in [('all', allm), ('wk7-11', new)] + [(s, stat == s) for s in ('pass_yd', 'rec', 'rec_yd', 'rush_yd', 'td')]:
    b, s = enc(m); b2, s2 = enc(m, wl)
    P(f'| {nm} | {m.sum()} | {b[1]:.3f} ({s[1]:.3f}) | {b[2]:+.3f} ({s[2]:.3f}) | {b2[2]:+.3f} ({s2[2]:.3f}) |')

# hypothetical bets
def fee_c(pc):   # Kalshi quadratic taker fee, one contract, rounded up to the cent
    return math.ceil(7 * pc / 100 * (1 - pc / 100) - 1e-9)
def bets(half):
    """one contract on the side the model favours; price = mid +/- half spread; taken only if fee-in EV > 0."""
    B = []
    for i in range(len(R)):
        for side in (1, 0):
            q = pm[i] if side else 1 - pm[i]; mid = pk[i] if side else 1 - pk[i]
            pc = min(99.0, max(1.0, round(mid * 100 + half))); f = fee_c(pc)
            if q * 100 - pc - f > 0:
                win = y[i] if side else 1 - y[i]
                B.append((i, side, q, mid, pc, f, win))
    return B
rng = np.random.default_rng(7)
def roi(B, cost, sel=None):
    """ROI and game-clustered bootstrap 95% for a cost function (pc, f)->stake."""
    if sel is not None: B = [b for b in B if sel(b)]
    if len(B) < 30: return None
    i = np.array([b[0] for b in B]); st = np.array([cost(b) for b in B]); pay = np.array([100.0 * b[6] for b in B])
    g = gid[i]; G = gid.max() + 1
    sg = np.bincount(g, st, minlength=G); pg = np.bincount(g, pay - st, minlength=G); ok = np.where(sg > 0)[0]
    dr = rng.integers(0, len(ok), (2000, len(ok))); r = pg[ok][dr].sum(1) / sg[ok][dr].sum(1)
    return len(B), pg.sum() / sg.sum(), np.percentile(r, 2.5), np.percentile(r, 97.5), float(np.mean(pay) / 100)
f3 = lambda t: 'insufficient (n<30)' if t is None else f'{t[1]*100:+.1f}% [{t[2]*100:+.1f}, {t[3]*100:+.1f}]'
P('\n## 2b. Hypothetical one-contract bets on the reconstructed rows (weeks 6-11). The log has mids only; the spread is ASSUMED (half-spread h cents), the fee is Kalshi\'s quadratic taker fee rounded up to the cent.')
P('Selection is fixed per row of the table (the bets that are fee-in +EV at mid + h); the same bets are then costed three ways. ROI with game-clustered bootstrap 95%.')
P('| half-spread h | bets | model\'s claimed ROI | ROI at the MID, no fee | ROI at mid + fee | ROI at mid + h + fee (executable) |'); P('|---|---|---|---|---|---|')
for h in (0, 1, 2, 3, 5):
    B = bets(h)
    claim = sum(b[2] * 100 - b[4] - b[5] for b in B) / sum(b[4] + b[5] for b in B)
    P(f'| {h}c | {len(B)} | {claim*100:+.1f}% | {f3(roi(B, lambda b: b[3]*100))} | {f3(roi(B, lambda b: b[3]*100 + b[5]))} | {f3(roi(B, lambda b: b[4] + b[5]))} |')
B = bets(2)
P('\n## 2c. By size of the disagreement (h = 2c selection; edge = model q - mid on the side bought)')
P('| claimed edge | bets | mean model q | mean mid | win rate | realised edge vs mid (SE) | share of claim realised | ROI at mid, no fee | ROI executable (h=2c, fee) |'); P('|---|---|---|---|---|---|---|---|---|')
for lo, hi in ((0, .05), (.05, .10), (.10, .15), (.15, .25), (.25, 1)):
    bb = [b for b in B if lo <= b[2] - b[3] < hi]
    if len(bb) < 30: P(f'| {lo:.2f}-{hi:.2f} | insufficient (n={len(bb)}) |||||||'); continue
    i = np.array([b[0] for b in bb]); q = np.array([b[2] for b in bb]); mid = np.array([b[3] for b in bb]); win = np.array([b[6] for b in bb])
    v = np.zeros(len(R)); mk = np.zeros(len(R), bool)
    # clustered SE of mean(win - mid)
    g = gid[i]; e = win - mid; mu = e.mean(); s = np.bincount(g, e - mu, minlength=gid.max() + 1); s = s[np.bincount(g, minlength=gid.max() + 1) > 0]
    se = math.sqrt(len(s) / (len(s) - 1) * (s ** 2).sum()) / len(e)
    P(f'| {lo:.2f}-{hi:.2f} | {len(bb)} | {q.mean():.3f} | {mid.mean():.3f} | {win.mean():.3f} | {mu:+.3f} ({se:.3f}) | {mu/(q-mid).mean()*100:+.0f}% | {f3(roi(bb, lambda b: b[3]*100))} | {f3(roi(bb, lambda b: b[4] + b[5]))} |')
P('\n## 2d. By stat and by side (h = 2c)')
P('| cut | bets | win rate | mean mid | ROI at mid, no fee | ROI executable |'); P('|---|---|---|---|---|---|')
for nm, sel in [(s, (lambda s: lambda b: stat[b[0]] == s)(s)) for s in ('pass_yd', 'rec', 'rec_yd', 'rush_yd', 'td')] + [('YES side', lambda b: b[1] == 1), ('NO side', lambda b: b[1] == 0),
        ('price < 20c', lambda b: b[4] < 20), ('price 20-80c', lambda b: 20 <= b[4] <= 80), ('price > 80c', lambda b: b[4] > 80), ('weeks 7-11 only', lambda b: wk[b[0]] >= 7)]:
    bb = [b for b in B if sel(b)]
    if len(bb) < 30: P(f'| {nm} | insufficient (n={len(bb)}) ||||'); continue
    P(f'| {nm} | {len(bb)} | {np.mean([b[6] for b in bb]):.3f} | {np.mean([b[3] for b in bb]):.3f} | {f3(roi(bb, lambda b: b[3]*100))} | {f3(roi(bb, lambda b: b[4] + b[5]))} |')
P('\n## 2e. Fee and spread as a share of stake (h = 2c bets): mean fee %.2fc on a mean price of %.1fc = %.1f%% of stake; a 2c half-spread = %.1f%% of stake' % (
    np.mean([b[5] for b in B]), np.mean([b[4] for b in B]), 100 * sum(b[5] for b in B) / sum(b[4] for b in B), 100 * 2 * len(B) / sum(b[4] for b in B)))

# ---------- 3. rushing tail
P('\n## 3. Rushing yards by Sleeper line and threshold, weeks 6-11 2026 (hit / model / Kalshi mid, n; rows are rungs Kalshi listed)')
P('| pos | Sleeper line | k band | n | player-games | hit | model | Kalshi | model gap (SE) | Kalshi gap (SE) |'); P('|---|---|---|---|---|---|---|---|---|---|')
pg = np.array([f"{r['game']}|{r['player']}" for r in R])
for p_, lines in (('RB', ((0, 20), (20, 30), (30, 50), (50, 200))), ('QB', ((0, 10), (10, 20), (20, 30), (30, 200)))):
    for lo, hi in lines:
        for klo, khi in ((1, 10), (11, 20), (21, 40), (41, 400)):
            mm = (stat == 'rush_yd') & (pos == p_) & (line >= lo) & (line < hi) & (kk >= klo) & (kk <= khi)
            if mm.sum() < 20: P(f'| {p_} | {lo}-{hi} | {klo}-{khi} | insufficient data (n={mm.sum()}) | | | | | | |'); continue
            a, sa, _ = cmean(y - pm, mm); b_, sb, _ = cmean(y - pk, mm)
            P(f'| {p_} | {lo}-{hi} | {klo}-{khi} | {mm.sum()} | {len(set(pg[mm]))} | {y[mm].mean():.3f} | {pm[mm].mean():.3f} | {pk[mm].mean():.3f} | {a:+.3f} ({sa:.3f}) | {b_:+.3f} ({sb:.3f}) |')
P('\nMean rushing yards vs Sleeper line (one row per player-game with a Kalshi rushing ladder):')
seen = {}
for r in R:
    if r['stat'] == 'rush_yd': seen[(r['game'], r['player'])] = r
for p_ in ('RB', 'QB'):
    for lo, hi in ((0, 20), (20, 30), (30, 50), (50, 200)):
        v = [(x['actual'], x['line'], float(x['kmean'] or 0)) for x in seen.values() if x['pos'] == p_ and lo <= x['line'] < hi]
        if len(v) < 10: P(f'- {p_} line {lo}-{hi}: insufficient data (n={len(v)})'); continue
        a = np.array(v); P(f'- {p_} line {lo}-{hi}: n={len(v)} Sleeper {a[:,1].mean():.1f}, Kalshi implied mean {a[:,2].mean():.1f}, actual {a[:,0].mean():.1f} (SD {a[:,0].std():.1f}; SE {a[:,0].std()/math.sqrt(len(v)):.1f}); zero-or-less {np.mean(a[:,0]<=0):.3f}')

# ---------- 4. out-of-sample Platt and blend, forward chained by week
P('\n## 4. Forward chaining by week on the rebuilt rows (fit on all earlier weeks, score the next; test weeks 8-11, fit starts with weeks 6-7). Ladder-weighted like mx_record; diff vs the comparator with game-clustered SE. Negative = better.')
def fitw(X, yy, ww, b0):
    b = np.array(b0, float)
    for _ in range(80):
        q = 1 / (1 + np.exp(-X @ b)); H = (X * (ww * q * (1 - q))[:, None]).T @ X + 1e-9 * np.eye(X.shape[1]); st = np.linalg.solve(H, X.T @ (ww * (yy - q))); b += st
        if abs(st).max() < 1e-10: break
    return b
P('| stat | test n | games | engine LL | Platt(engine) - engine (SE) | Kalshi - engine (SE) | blend - Kalshi (SE) | mean blend weight on engine | Platt(Kalshi) - Kalshi (SE) |'); P('|---|---|---|---|---|---|---|---|---|')
for nm, m in [('all (per-stat fits)', None)] + [(s, stat == s) for s in ('pass_yd', 'rec', 'rec_yd', 'rush_yd', 'td')]:
    pp = np.full(len(y), np.nan); bl = np.full(len(y), np.nan); pkp = np.full(len(y), np.nan); wts = []
    for s in (('pass_yd', 'rec', 'rec_yd', 'rush_yd', 'td') if m is None else (nm,)):
        ms = stat == s
        for t in (8, 9, 10, 11):
            tr = ms & (wk < t); te = ms & (wk == t)
            b = fitw(np.c_[np.ones(tr.sum()), lg(pm[tr])], y[tr], wl[tr], [0, 1]); pp[te] = 1 / (1 + np.exp(-(b[0] + b[1] * lg(pm[te]))))
            b = fitw(np.c_[np.ones(tr.sum()), lg(pk[tr])], y[tr], wl[tr], [0, 1]); pkp[te] = 1 / (1 + np.exp(-(b[0] + b[1] * lg(pk[te]))))
            b = fitw(np.c_[np.ones(tr.sum()), lg(pk[tr]), lg(pm[tr])], y[tr], wl[tr], [0, 1, 0]); bl[te] = 1 / (1 + np.exp(-(b[0] + b[1] * lg(pk[te]) + b[2] * lg(pm[te])))); wts.append(b[2] / (b[1] + b[2]))
    te = ~np.isnan(pp) if m is None else (m & ~np.isnan(pp))
    c = np.clip
    r1 = cmean(ll(c(pp, EPS, 1 - EPS)) - ll(pm), te, wl); r2 = cmean(ll(pk) - ll(pm), te, wl); r3 = cmean(ll(c(bl, EPS, 1 - EPS)) - ll(pk), te, wl); r4 = cmean(ll(c(pkp, EPS, 1 - EPS)) - ll(pk), te, wl)
    P(f'| {nm} | {te.sum()} | {r1[2]} | {cmean(ll(pm), te, wl)[0]:.4f} | {r1[0]:+.4f} ({r1[1]:.4f}) | {r2[0]:+.4f} ({r2[1]:.4f}) | {r3[0]:+.4f} ({r3[1]:.4f}) | {np.mean(wts):+.2f} | {r4[0]:+.4f} ({r4[1]:.4f}) |')
open(S + 'nfl_tables.md', 'w').write('\n'.join(out) + '\n')
print('\n'.join(out))
