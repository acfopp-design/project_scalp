"""Realistic run: all stocks traded TOGETHER out of one account.
Rs 1,00,000 at 5x = Rs 5,00,000 buying power = 2 slots of Rs 2,50,000, shared
across every name. This is the number that matters - the per-stock backtests
each assume the whole account is free for that one stock."""
import sys,numpy as np,pandas as pd
import tune_v6 as V6, tune_dyn as TD
from eye_cfg import CFG
SLOT=250000
FILES={'AGI':'AGIGREENPAC_20260921.csv','PROTEAN':'PROTEAN_20260921.csv',
       'INDOMIM':'INDOMIM_20260921.csv','RAYMOND':'RAYMOND_20260921.csv',
       'FILATEX':'FILATEX_20260921.csv'}

def signals(sym,d,p):
    """every entry the rule would take on this stock, ignoring capital, each one
    scored by how exceptional its bar is BY THAT STOCK'S OWN STANDARDS."""
    q=dict(p); q['SLOTS']=99; q['MAXTR']=999
    T,st,n=d['T'],d['st'],d['n']
    idx={T[i]:i for i in range(n-1,st-1,-1)}
    out=[]
    for t in V6.sim(d,q):
        i=idx[t['ti']]
        R=globals().get('RANK','time')
        if   R=='flow':  score=d['volq'][i]+d['rngq'][i]+min(d['keep'][i],1.0)
        elif R=='mover': score=d['cum'][i]
        elif R=='angle': score=abs(d['ANG'][i])
        elif R=='early': score=-i
        else:            score=0.0
        out.append(dict(sym=sym,score=score,**t))
    return out

def main(syms,p):
    D={s:V6.prep('backtest_data/intraday/'+FILES[s]) for s in syms}
    cand=[]
    for s in syms: cand+=signals(s,D[s],p)
    RANK=globals().get('RANK','time')
    if RANK=='time':
        cand.sort(key=lambda r:(r['ti'],r['sym']))
        busy=[]; taken=[]
        for c in cand:
            busy=[b for b in busy if b>c['ti']]
            if len(busy)>=p['SLOTS']: continue
            taken.append(c); busy.append(c['to'])
    else:
        # walk the clock; when more than one name signals in the same 30s bar,
        # take the one whose bar is the most exceptional for that stock
        from collections import defaultdict
        bytime=defaultdict(list)
        for c in cand: bytime[c['ti']].append(c)
        busy=[]; taken=[]
        for tt in sorted(bytime):
            busy=[b for b in busy if b>tt]
            for c in sorted(bytime[tt],key=lambda r:-r['score']):
                if len(busy)>=p['SLOTS']: break
                taken.append(c); busy.append(c['to'])
        taken.sort(key=lambda r:r['ti'])
    T=pd.DataFrame(taken)
    n=T.net.sum()
    print(f"\n{len(T)} trades, {(T.net>0).sum()} wins, NET Rs {n:+,.0f} = {n/1000:+.2f}% on Rs 1,00,000")
    print('by stock:'); print((T.groupby('sym').net.agg(['size','sum'])/[1,1000]).round(2).to_string())
for RANK in ('time','flow','mover','angle','early'):
    globals()['RANK']=RANK
    print('='*70); print('slot contest resolved by:',RANK)
    main(list(FILES),CFG)
