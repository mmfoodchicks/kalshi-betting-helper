import json,math
s=json.load(open('series.json'))
def pick(prefix):
    c=[r for r in s if r['d'].startswith(prefix)]
    return c[-1]
# week boundaries: last snapshot before the next Thursday's games
marks=[('base 10-29 (wk<=8)','2026-10-29T15'),('wk9','2026-11-03'),('wk10','2026-11-10'),('wk11','2026-11-17'),('wk12','2026-11-24'),('late','2026-11-26')]
snaps=[(n,pick(p)) for n,p in marks]
def S(r,b,k): return r[b]['n']*r[b][k]
print("cluster rows | drive LL  deep LL  book LL  kalshi LL(nfl) kalshi(deep) | drive-deep | Brier drive deep kalshi")
tot={}
rows=[]
for (n0,a),(n1,b) in zip(snaps,snaps[1:]):
    dn=b['nfl']['n']-a['nfl']['n']; dd=b['nfl_deep']['n']-a['nfl_deep']['n']
    f=lambda bk,k:(S(b,bk,k)-S(a,bk,k))
    bookn=(b['nfl_book']['n'] or 0)-(a['nfl_book']['n'] or 0)
    d=dict(n=dn,nd=dd,drive=f('nfl','model_logloss'),deep=f('nfl_deep','model_logloss'),k1=f('nfl','market_logloss'),k2=f('nfl_deep','market_logloss'),
           bd=f('nfl','model_brier'),bdp=f('nfl_deep','model_brier'),bk=f('nfl','market_brier'))
    rows.append((n1,d))
    print(n1,dn,dd,*(round(d[k]/dn,4) for k in ('drive','deep','k1','k2')),'| diff',round((d['drive']-d['deep'])/dn,4),'| brier',*(round(d[k]/dn,4) for k in ('bd','bdp','bk')))
N=sum(d['n'] for _,d in rows)
for k in ('drive','deep','k1','k2','bd','bdp','bk'):
    print(k, round(sum(d[k] for _,d in rows)/N,4))
print('rows',N,'games',N/2)
# cluster SE of drive-deep per-row mean difference, ratio estimator over 4 week clusters (merge 'late' into wk12)
cl=[rows[0][1],rows[1][1],rows[2][1],{k:rows[3][1][k]+rows[4][1][k] for k in rows[3][1]}]
for name,fa,fb in (('drive-deep','drive','deep'),('drive-kalshi','drive','k1'),('deep-kalshi','deep','k1'),('blend bound(0.3*(deep-drive))','deep','drive')):
    D=[c[fa]-c[fb] for c in cl]; n=[c['n'] for c in cl]; G=len(cl); m=sum(D)/sum(n)
    var=G/(G-1)*sum((d-m*ni)**2 for d,ni in zip(D,n))/sum(n)**2
    print(name,'mean/row',round(m,4),'cluster SE (4 weeks)',round(math.sqrt(var),4),'per-week',[round(d/ni,4) for d,ni in zip(D,n)])
# base
b=snaps[0][1]
print('base: nfl 146 rows drive',b['nfl']['model_logloss'],'kalshi',b['nfl']['market_logloss'],'; deep 54 rows',b['nfl_deep']['model_logloss'],'kalshi',b['nfl_deep']['market_logloss'])
# what the drive needs on the first 54 for the blend-vs-drive read
last=snaps[-1][1]
deep_all=S(last,'nfl_deep','model_logloss'); drive_114=sum(d['drive'] for _,d in rows)
print('deep sum all 168',round(deep_all,2),'drive sum on the 114',round(drive_114,2),'deep sum on 114',round(sum(d['deep'] for _,d in rows),2))
print('drive would need LL on first 54 rows below',round((deep_all-drive_114)/54,4),'to tie deep over all 168')
