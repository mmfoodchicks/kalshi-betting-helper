import json,subprocess,sys
R='/home/user/kalshi-betting-helper'
log=subprocess.check_output(['git','-C',R,'log','--reverse','--format=%H %cI','origin/sim-history','--','errors/sharp-latest.json'],text=True).split('\n')
out=[]
for l in log:
    if not l: continue
    h,d=l.split()
    try: j=json.loads(subprocess.check_output(['git','-C',R,'show',h+':errors/sharp-latest.json'],text=True))
    except Exception as e: print('bad',h,e); continue
    m=j.get('models',j)
    row={'d':d}
    for b in ('nfl','nfl_deep','nfl_book'):
        v=(m.get(b) or {}); vm=v.get('vs_market') or {}
        row[b]={'graded':v.get('graded'),'logged':v.get('logged'),**{k:vm.get(k) for k in ('n','model_logloss','market_logloss','model_brier','market_brier')}}
    out.append(row)
json.dump(out,open(sys.argv[1],'w'),indent=0)
prev=None
for r in out:
    key=tuple((r[b]['n'],r[b]['model_logloss']) for b in ('nfl','nfl_deep','nfl_book'))
    if key!=prev:
        print(r['d'][:16],*[f"{b}: g{r[b]['graded']} n{r[b]['n']} m{r[b]['model_logloss']} k{r[b]['market_logloss']} bm{r[b]['model_brier']} bk{r[b]['market_brier']} |" for b in ('nfl','nfl_deep','nfl_book')])
    prev=key
