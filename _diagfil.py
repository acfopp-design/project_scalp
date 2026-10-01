import tune_v6 as V6, tune_dyn as TD, pandas as pd, numpy as np
d=V6.prep('backtest_data/intraday/FILATEX_20260921.csv')
T,C,O,H,L,MID,st=d['T'],d['C'],d['O'],d['H'],d['L'],d['MID'],d['st']
SLOT=250000
# Sri's 3 trades replayed with engine's adaptive exit
for tt,side in [('0916',1),('0941',-1),('1108',1)]:
    k=[i for i in range(st,d['n']) if T[i]==tt][0]
    print(tt,'idx',k,'MID',round(MID[k],2),'ANG %.1f'%d['ANG'][k],'HAT',d['HAT'][k],
          'SAR<C',bool(d['SAR'][k]<C[k]),'e8>m12',bool(d['e8'][k]>d['m12'][k]),
          'hist_rise',bool(d['HIST'][k]>d['HIST'][k-1]),'rsi_up',bool(d['RSI'][k]>d['RSI'][k-1]),
          'volx %.2f'%d['volx'][k],'cum %.2f'%d['cum'][k])
cfg=dict(SLOTS=1,ANG=20,FLIPW=2,STOPR=3,DIPF=16,DIPM=1.5,SHORTS=False,USEHA=False,USECLOUD=False,
         OPENB=4,OPENBODY=0.3,OPENCUM=0.5,OPENVOL=1.0,COOL=4,LASTT='1520',EXITT='1515',MAXTR=12)
print(pd.DataFrame(V6.sim(d,cfg)).to_string(index=False))
