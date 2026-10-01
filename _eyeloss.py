"""Human-eye read on every losing trade: what the chart actually looked like
at the entry bar, and what happened to the leg v6 predicted."""
import sys,os,json; sys.path.insert(0,os.getcwd())
import funnel as FN, live_shadow as LS, paper_live as PL, eye_strategy as EYE
import tune_v6 as V6
import numpy as np
DAY="20260924"
d=json.load(open('logs/paper_live_%s.json'%DAY))
L=[t for t in d['trades'] if t['net']<0]
warm=LS.load_warm(LS._prev_session_dir(DAY)); pairs,_=LS.build(DAY,warm)
for t in sorted(L,key=lambda x:x['net']):
    sym=t['sym']
    if sym not in pairs: print(sym,'no tape'); continue
    b,n0=pairs[sym]
    EYE._cache.clear(); e,x,sc=EYE.analyse(sym,b)
    dd=V6.prep(str(EYE.SCRATCH/f"pl_{sym}.csv"))
    N=len(b); st=dd['st']
    runhi=np.maximum.accumulate(np.where(np.arange(N)>=st,dd['H'],-1e9))
    i=None
    for k in range(n0,N):
        if b[k]['hhmm']==t['sig_hms']: i=k; break
    if i is None:
        for k in range(n0,N):
            if b[k]['hhmm']>=t['in_hms']: i=k-1; break
    pred = (x[i]-i) if (i<len(x) and x[i] is not None) else None
    print(f"\n===== {sym}  net Rs {t['net']:,.0f}  ({t['in_hms']} -> {t['out_hms']}) =====")
    print(f"  at the signal bar {b[i]['hhmm']}: score {sc[i]:.2f} | cum {dd['cum'][i]:+.2f}% | "
          f"EMA angle {dd['ANG'][i]:.0f} | off high {(dd['C'][i]-runhi[i])/runhi[i]*100:+.2f}%")
    print(f"  v6 predicted a {pred}-bar leg; the trade actually lasted {t['held']//30} bars")
    print(f"  {'bar':<10}{'open':>10}{'high':>10}{'low':>10}{'close':>10}{'vol':>9}")
    for k in range(max(n0,i-4), min(N, i+16)):
        mark = ' <- signal' if k==i else (' <- ENTRY' if b[k]['hhmm']==t['in_hms'] else
               (' <- EXIT' if b[k]['hhmm']==str(t['out_hms']) else ''))
        print(f"  {b[k]['hhmm']:<10}{b[k]['o']:>10.2f}{b[k]['h']:>10.2f}{b[k]['l']:>10.2f}{b[k]['c']:>10.2f}{int(b[k]['v'] or 0):>9}{mark}")
