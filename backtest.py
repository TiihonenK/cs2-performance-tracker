"""Aito out-of-sample -testi: jokaiselle testijakson kartalle ennustetaan pelaajan
tapot VAIN sitä ennen olevalla datalla, ja verrataan toteutuneeseen."""
import sqlite3, numpy as np, pandas as pd
from match_simulator import simulate_match_context

HALF_LIFE=60; SHRINK=200; OVERDISP=1.25
rng=np.random.default_rng(0)

conn=sqlite3.connect('hltv_data.db')
ps=pd.read_sql_query("""SELECT s.match_id,s.player_id,s.kills,s.deaths,
 m.match_date,m.score_team1,m.score_team2,m.team1_id,m.team2_id,p.team_id
 FROM player_stats s JOIN matches m ON m.id=s.match_id JOIN players p ON p.id=s.player_id""",conn)
conn.close()
ps['date']=pd.to_datetime(ps.match_date,format='%Y-%m-%d',errors='coerce')
ps=ps.dropna(subset=['date'])
ps['tot']=ps.score_team1+ps.score_team2
ps=ps[ps.tot>=13].sort_values('date')

cut=ps.date.max()-pd.Timedelta(days=75)
train_end=cut
test=ps[ps.date>cut]
print(f"treeni: {ps.date.min().date()} - {cut.date()}   testi: {cut.date()} - {ps.date.max().date()}")
print(f"testirivejä: {len(test)}")

pit=[]; errs=[]; act_l=[]; pred_l=[]
for mid, grp in test.groupby('match_id'):
    d0=grp.date.iloc[0]
    hist=ps[ps.date<d0]
    if hist.empty: continue
    # joukkueen keskimääräinen KPR shrinkagea varten
    for _,row in grp.iterrows():
        h=hist[hist.player_id==row.player_id]
        if len(h)<10: continue
        w=0.5**((d0-h.date).dt.days/HALF_LIFE)
        kpr=(h.kills*w).sum()/((h.tot*w).sum())
        # shrinkage kohti kaikkien pelaajien keskiarvoa (0.658)
        n_eff=(h.tot*w).sum()
        kpr=(kpr*n_eff+0.658*SHRINK)/(n_eff+SHRINK)
        ctx=simulate_match_context(0.5,2000,seed=int(row.player_id)%9999)
        mult=(1.825+2.937*ctx['share_t1'])/3.2935
        exp_k=np.maximum(ctx['rounds']*kpr*mult,1e-6)
        sim=rng.negative_binomial(np.maximum(exp_k/(OVERDISP-1),1e-6),1/OVERDISP)
        a=row.kills
        pit.append((np.sum(sim<a)+0.5*np.sum(sim==a))/len(sim))
        errs.append(sim.mean()-a); act_l.append(a); pred_l.append(sim.mean())

pit=np.array(pit); errs=np.array(errs)
print(f"\nn = {len(pit)} pelaaja-karttaa")
print(f"harha (ennuste - toteutunut): {errs.mean():+.3f} tappoa   MAE {np.abs(errs).mean():.2f}")
print(f"korrelaatio ennuste vs toteutunut: {np.corrcoef(pred_l,act_l)[0,1]:.3f}")
print("\nPIT-kalibrointi (pitäisi olla 10 % joka koriin jos jakauma on oikein):")
h,_=np.histogram(pit,bins=np.linspace(0,1,11))
for i,v in enumerate(h): print(f"  {i*10:3d}-{(i+1)*10:3d} %: {v/len(pit)*100:5.1f} %  {'#'*int(v/len(pit)*200)}")
print(f"\nkeskihajonta toteutuneissa {np.std(act_l):.2f} vs ennusteiden keskihajonta {np.std(pred_l):.2f}")
