import sys,math,random,statistics as st
sys.path.insert(0,'/home/user/kalshi-betting-helper')
import nfl_game_sim as G, nfl_deep_sim as E
def prof(tds,fgm,to=1.25,home=True):
    return {"abbr":"X","home":home,"exp":{"pass_td":tds*0.62,"rush_td":tds*0.38,"fgm":fgm,"pass_int":to*0.6,"fum_lost":to*0.4}}
def drive(th,ta,n=40000,seed=1,fh=1.7,fa=1.7):
    rng=random.Random(seed)
    rh=G._rates(prof(th,fh),True); ra=G._rates(prof(ta,fa,home=False),False)
    m=[];t=[];hw=0
    for _ in range(n):
        s=G._play_game(rh,ra,rng); a,b=s[0]["pts"],s[1]["pts"]; m.append(a-b); t.append(a+b); hw+=a>b
    return dict(p_home=round(hw/n,4),mean_m=round(st.mean(m),2),sd_m=round(st.pstdev(m),2),med_m=st.median(m),mean_t=round(st.mean(t),2),sd_t=round(st.pstdev(t),2),
                impl_sd=round(st.mean(m)/st.NormalDist().inv_cdf(hw/n),2) if hw/n not in (0,1) and abs(hw/n-.5)>.02 else None)
print("DRIVE equal teams (2.35 TD, 1.7 FG each)",drive(2.35,2.35))
print("DRIVE no-tilt check: form 0"); 
import copy
f=G._FORM_SD; pass
for th,ta in ((2.45,2.15),(2.55,2.05),(2.6,2.0),(2.7,1.9),(2.15,2.45),(2.0,2.6),(1.9,2.7)):
    print("DRIVE home TD",th,"away TD",ta,drive(th,ta,n=30000))
# no-tilt: pass form_sd=0 explicitly
rng=random.Random(3); rh=G._rates(prof(2.35,1.7),True); ra=G._rates(prof(2.35,1.7,home=False),False)
m=[];t=[]
for _ in range(30000):
    s=G._play_game(rh,ra,rng,form_sd=0.0); m.append(s[0]["pts"]-s[1]["pts"]); t.append(s[0]["pts"]+s[1]["pts"])
print("DRIVE no tilt: sd_m",round(st.pstdev(m),2),"sd_t",round(st.pstdev(t),2))
