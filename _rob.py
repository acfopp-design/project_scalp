import numpy as np,pandas as pd,tune_v6 as V6
res,band=V6.run('backtest_data/intraday/AGIGREENPAC_20260921.csv',29.38,top=0)
A=pd.DataFrame([dict(pct=r[0],tr=r[1],**r[2]) for r in res])
print('all configs: median %.2f  p25 %.2f  p75 %.2f  max %.2f'%(A.pct.median(),A.pct.quantile(.25),A.pct.quantile(.75),A.pct.max()))
keys=[k for k in V6.GRID if len(V6.GRID[k])>1]
for k in keys:
    g=A.groupby(k).pct.agg(['median','mean','max']).round(2)
    print('\n--',k); print(g.to_string())
