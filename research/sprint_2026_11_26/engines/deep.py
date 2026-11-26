import sys,math,time,statistics as st
sys.path.insert(0,'/home/user/kalshi-betting-helper')
import nfl_deep_sim as E, nfl_deep_data as D
T=E.tables()
def P(ab,level):
    p=E.league_profile(T,abbr=ab); p["level"]=level; return p
def lvl(margin_vs_avg):
    pts=T.league["pts_pg"]; return (1+(margin_vs_avg/2)/pts)**(1/D._ENGINE_PTS_ELASTICITY)
t0=time.time()
for mh,ma,n in ((0,0,12000),(1.5,-1.5,6000),(-1.5,1.5,6000),(3,-3,6000),(-3,3,6000),(5,-5,6000)):
    r=E.simulate(P("H",lvl(mh)),P("A",lvl(ma)),n=n,seed=7)
    z=st.NormalDist().inv_cdf(r["p_home"]) if abs(r["p_home"]-.5)>.02 else None
    print("DEEP home margin-vs-avg",mh,"away",ma,{k:r[k] for k in ("p_home","p_tie","mean_margin","sd_margin","exp_total","sd_total")},"impl_sd",round(r["mean_margin"]/z,2) if z else None,round(time.time()-t0),flush=True)
