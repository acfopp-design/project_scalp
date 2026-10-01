"""Tune FILATEX alone to see whether 20.33 is reachable at all."""
import itertools,numpy as np,pandas as pd,tune_v6 as V6
d=V6.prep('backtest_data/intraday/FILATEX_20260921.csv')
G=V6.GRID; keys=list(G); best=[]
for c in itertools.product(*[G[k] for k in keys]):
    p=dict(zip(keys,c)); t=V6.sim(d,p); net=sum(x['net'] for x in t)/1000
    best.append((round(net,2),len(t),p))
best.sort(key=lambda r:-r[0])
print('target 20.33  |  best %.2f  | in-band %d of %d'%(best[0][0],sum(1 for b in best if abs(b[0]-20.33)<=5),len(best)))
for b in best[:5]:
    print(' %+7.2f %2dtr '%(b[0],b[1])+' '.join(f'{k}={b[2][k]}' for k in keys if len(G[k])>1))
