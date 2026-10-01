import tune_v6 as V6, pandas as pd
from eye_cfg import CFG
d=V6.prep('backtest_data/intraday/AGIGREENPAC_20260921.csv')
t=V6.sim(d,CFG); T=pd.DataFrame(t)
print(T.to_string(index=False))
print('trades %d  wins %d  NET %+d = %+.2f%% on 1L'%(len(T),(T.net>0).sum(),T.net.sum(),T.net.sum()/1000))
