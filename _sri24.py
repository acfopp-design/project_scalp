"""Sri's 24-Sep human-eye book vs what the engine saw."""
import sys,os,json; sys.path.insert(0,os.getcwd())
import funnel as FN, live_shadow as LS, paper_live as PL, eye_strategy as EYE
import tune_v6 as V6
import numpy as np
DAY="20260924"; UPTO="15:15:00"
BOOK=[("SBILIFE","09:20:30",1722,"09:39:00",1782,1),
      ("SBILIFE","09:48:30",2274.8,"09:56:00",2316,1),
      ("ICICIGI","09:20:00",1494,"09:34:30",1538,1),
      ("ICICIGI","09:48:00",1540,"10:01:00",1553,1),
      ("MONQ50","10:27:30",187.45,"10:45:00",217.4,1)]
warm=LS.load_warm(LS._prev_session_dir(DAY)); pairs,_=LS.build(DAY,warm)
tape,d0={},{}
for s,(bars,n) in pairs.items():
    bb=[x for x in bars[:n] if x.get("c")]
    tb=[x for x in bars[n:] if x.get("c") and PL.OPEN_T<=x["hhmm"]<=UPTO]
    if len(tb)<3: continue
    tape[s]=bb+tb; d0[s]=len(bb)
avail,_a,_b=FN.build(DAY,tape)
print(f"{'stock':<10}{'his entry':>10}{'avail from':>12} | {'v6 bar':>9}{'score':>7}{'leg':>5}{'cum':>7} | blocked by")
for sym,ent,pin,ex,pout,side in BOOK:
    b=tape.get(sym)
    if not b:
        print(f"{sym:<10}{ent:>10}{str(avail.get(sym,'-')):>12} | NO TAPE -- never fetched, invisible to the engine"); continue
    EYE._cache.clear(); e,x,sc=EYE.analyse(sym,b)
    d=V6.prep(str(EYE.SCRATCH/f"pl_{sym}.csv"))
    n=len(b); st=d["st"]
    runhi=np.maximum.accumulate(np.where(np.arange(n)>=st,d["H"],-1e9))
    i0=None
    for k in range(d0[sym],n):
        if b[k]["hhmm"]>=ent: i0=k; break
    if i0 is None: continue
    best=None
    for i in range(max(d0[sym],i0-3),min(n,i0+4)):
        if sc[i]<=0: continue
        leg=(x[i]-i) if x[i] is not None else None
        if best is None or sc[i]>best[1]: best=(i,sc[i],leg)
    av=avail.get(sym)
    if best is None:
        print(f"{sym:<10}{ent:>10}{str(av):>12} | {'-- none --':>9}{'-':>7}{'-':>5}{'-':>7} | v6 entry rule never fired here"); continue
    i,s_,leg=best; cum=round(float(d["cum"][i]),2)
    off=round(float((d["C"][i]-runhi[i])/runhi[i]*100),2) if runhi[i]>0 else 0.0
    why=[]
    if av and av>b[i]["hhmm"]: why.append(f"AVAIL blocked till {av}")
    if s_<EYE.SELECT_MIN: why.append(f"score {s_:.2f}<{EYE.SELECT_MIN}")
    if cum<EYE.MIN_UP: why.append(f"cum {cum}<{EYE.MIN_UP}")
    if off<EYE.MAX_OFF: why.append(f"off-high {off}<{EYE.MAX_OFF}")
    if leg is not None and leg<EYE.MIN_LEG: why.append(f"leg {leg}<{EYE.MIN_LEG}")
    if not why: why.append("PASSED ALL GATES -> slots were full")
    print(f"{sym:<10}{ent:>10}{str(av):>12} | {b[i]['hhmm']:>9}{s_:>7.2f}{str(leg):>5}{cum:>7} | {'; '.join(why)}")
