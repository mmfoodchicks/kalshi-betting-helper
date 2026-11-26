import os
"""Rebuild row-level NFL rungs for 2026 weeks 6-11: Kalshi's frozen de-vigged mid (props log,
sim-history), the Matchups engine's figure (nfl_matchup.player_markets on the frozen Sleeper line,
no shipped term touches a Kalshi stat), and the outcome from Sleeper's official box."""
import sys, json, math, csv, collections
sys.path.insert(0, '/home/user/kalshi-betting-helper')
S = os.path.join(os.path.dirname(os.path.abspath(__file__)), '')  # data files beside the script (not committed)
import nfl_matchup as M, kalshi_nfl
coefs = M.load_coefs()
rows = []; skip = collections.Counter()
for w in range(6, 12):
    lg = json.load(open(S + f'props/w{w:02d}.json'))
    st = json.load(open(S + f'sleeper/stats_w{w}.json'))
    box = collections.defaultdict(list)
    for r in st:
        p = r.get('player') or {}
        nm = kalshi_nfl._norm(f"{p.get('first_name','')} {p.get('last_name','')}")
        box[nm].append(r)
    slp = lg['sleeper']
    for gk, g in lg['games'].items():
        for nm, pl in g['players'].items():
            sl = slp.get(nm)
            if not sl or not sl.get('stats'):
                skip['no_sleeper_line'] += len(pl['stats']); continue
            b = [r for r in box.get(nm, []) if (r.get('team') or '') in gk[-6:] or True]
            b = [r for r in b if r['player'].get('position') == sl['pos']] or b
            if len(b) != 1:
                skip['box_missing_or_dup'] += len(pl['stats']); continue
            s = b[0]['stats']
            if not (s.get('gms_active') or 0) or not ((s.get('off_snp') or 0) > 0):
                skip['void_did_not_play'] += len(pl['stats']); continue
            pos = sl['pos']; base = dict(sl['stats'])
            mk = M.player_markets(coefs, None, pos, base)
            y = {'rec': s.get('rec', 0), 'rec_yd': s.get('rec_yd', 0), 'rush_yd': s.get('rush_yd', 0),
                 'pass_yd': s.get('pass_yd', 0), 'td': (s.get('rec_td', 0) or 0) + (s.get('rush_td', 0) or 0)}
            for stat, blk in pl['stats'].items():
                if stat not in y: skip['other_stat'] += 1; continue
                d = (mk.get(stat) or {}).get('dist')
                if d is None: skip['no_dist:' + stat] += 1; continue
                for line, pm in blk.get('rungs') or []:
                    k = int(math.ceil(float(line)))
                    rows.append(dict(week=w, game=gk, player=nm, pos=pos, stat=stat, k=k, line=base.get(
                        {'td': 'rush_td'}.get(stat, stat)) if stat != 'td' else (base.get('rush_td', 0) or 0) + (base.get('rec_td', 0) or 0),
                        car=base.get('rush_att', 0), p_model=round(float(d.sf_ge(k)), 5), p_mkt=float(pm),
                        y=int((y[stat] or 0) >= k), actual=y[stat] or 0, kmean=blk.get('mean')))
print(len(rows), dict(skip))
print(collections.Counter((r['week']) for r in rows), collections.Counter(r['stat'] for r in rows), len({r['game'] for r in rows}))
with open(S + 'rows_nfl_w06_w11.csv', 'w', newline='') as fh:
    wr = csv.DictWriter(fh, fieldnames=list(rows[0])); wr.writeheader(); wr.writerows(rows)
