"""Joint tuner: one shared config must land every stock inside its own +/-5 band
and the portfolio inside +/-10. No stock names in the logic - only in the target map."""
import sys,itertools,numpy as np,pandas as pd
import tune_v6 as V6
from eye_targets import TARGETS

def run(files,grid=None,top=10):
    grid=grid or V6.GRID
    D={f:V6.prep(p) for f,p in files.items()}
    keys=list(grid); res=[]
    for combo in itertools.product(*[grid[k] for k in keys]):
        p=dict(zip(keys,combo)); row={}; ok=True; worst=0
        for f in files:
            t=V6.sim(D[f],p); net=sum(x['net'] for x in t)/1000
            row[f]=round(net,2); d=abs(net-TARGETS[f])
            worst=max(worst,d)
            if d>5: ok=False
        tot=sum(row.values()); tgt=sum(TARGETS[f] for f in files)
        res.append((worst,abs(tot-tgt),ok,row,round(tot,2),p))
    res.sort(key=lambda r:(not r[2],r[0]))
    inband=[r for r in res if r[2]]
    print(f"tested {len(res)} | stocks {list(files)} | ALL-IN-BAND {len(inband)}")
    for r in res[:top]:
        print(f"  worst{r[0]:6.2f} tot{r[4]:+8.2f}(tgt {round(sum(TARGETS[f] for f in files),2)}) {r[3]} "
              +" ".join(f"{k}={r[5][k]}" for k in keys if len(grid[k])>1))
    return res
if __name__=='__main__':
    files={s:f'backtest_data/intraday/{p}' for s,p in
           [a.split('=') for a in sys.argv[1:]]}
    run(files)
