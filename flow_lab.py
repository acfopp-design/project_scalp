"""Bar-level order-flow PROXIES from 30s OHLCV, plus volume analysis.
No depth or tape exists in this project, so aggressor side is inferred from where
each bar closed inside its own range. Everything here is causal."""
import numpy as np, pandas as pd, tune_v6 as V6
pd.set_option('display.width',250)
F={'AGI':'AGIGREENPAC','PROTEAN':'PROTEAN','INDOMIM':'INDOMIM',
   'RAYMOND':'RAYMOND','FILATEX':'FILATEX','EMMVEE':'EMMVEE'}

def feats(d):
    C,O,H,L,V,st,n=d['C'],d['O'],d['H'],d['L'],d['V'],d['st'],d['n']
    rng=H-L
    # 1. close location value: +1 = closed on the high (buyers took it), -1 = on the low
    clv=np.where(rng>0,((C-L)-(H-C))/np.where(rng>0,rng,1),0.0)
    # 2. signed volume and cumulative volume delta (the CVD proxy)
    sv=V*clv
    cvd=np.cumsum(np.where(np.arange(n)>=st,sv,0))
    # 3. volume vs the stock's OWN expanding median (causal)
    vm=np.array([np.median(V[st:i+1]) if i>st else max(V[st],1) for i in range(n)])
    volx=V/np.where(vm<=0,1,vm)
    # 4. EFFORT vs RESULT: how much price movement per share traded, 20 bars, causal
    eff=(pd.Series(np.abs(np.diff(C,prepend=C[0]))/C*100).rolling(20,min_periods=5).sum().values /
         pd.Series(V).rolling(20,min_periods=5).sum().values.clip(1))
    # 5. CVD slope over 10 bars, scaled by typical signed volume
    sv_sc=pd.Series(np.abs(sv)).rolling(40,min_periods=5).mean().values.clip(1)
    cvds=(cvd-np.r_[np.full(10,cvd[0]),cvd[:-10]])/(sv_sc*10)
    # 6. does CVD agree with price? divergence = effort without result
    ret10=(C-np.r_[np.full(10,C[0]),C[:-10]])/C*100
    return dict(clv=clv,volx=volx,eff=eff,cvd=cvd,cvds=cvds,ret10=ret10,
                keep=d['keep'],rng=rng/C*100)

ALL=[]
for s,f in F.items():
    d=V6.prep(f'backtest_data/intraday/{f}_20260921.csv'); x=feats(d)
    st,n,C=d['st'],d['n'],d['C']
    fwd=np.array([ (C[min(i+20,n-1)]-C[i])/C[i]*100 for i in range(n)])
    for i in range(st+30,n-25):
        ALL.append(dict(sym=s,t=d['T'][i],clv=x['clv'][i],volx=x['volx'][i],
            eff=x['eff'][i]*1e4,cvds=x['cvds'][i],ret10=x['ret10'][i],
            keep=x['keep'][i],rng=x['rng'][i],fwd=fwd[i]))
A=pd.DataFrame(ALL)
A['state']=np.where(A.fwd>0.5,'UP-LEG',np.where(A.fwd<-0.5,'DOWN-LEG',
            np.where(A.fwd.abs()<=0.2,'FLAT','drift')))
print('bars per state:'); print(A.state.value_counts().to_string())
cols=['clv','volx','eff','cvds','ret10','keep','rng']
print('\n=== MEDIAN of each causal feature, by what the NEXT 10 minutes did ===')
print(A.groupby('state')[cols].median().round(3).to_string())
A.to_csv('_flow.csv',index=False)
