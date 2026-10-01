"""Sri's own 23-Sep book vs what the engine saw at HIS entry bar."""
import sys,os; sys.path.insert(0,os.getcwd())
import funnel as FN, live_shadow as LS, paper_live as PL, eye_strategy as EYE
import tune_v6 as V6
DAY="20260923"; UPTO="15:15:00"
BOOK=[("EKC","09:17:00","09:23:00",1),("CONFIPET","09:17:00","09:28:30",1),
 ("CONFIPET","10:30:00","10:54:00",1),("CONFIPET","11:02:30","11:07:30",1),
 ("ARCIL","10:07:30","10:16:30",1),("ARCIL","10:35:00","10:44:30",1),
 ("KANOHAR","09:16:00","09:33:00",1),("KANOHAR","09:40:00","09:55:00",-1),
 ("WOCKPHARMA","09:24:00","09:45:00",1),("WOCKPHARMA","09:48:30","09:56:00",1),
 ("WOCKPHARMA","10:04:30","10:16:30",1),("SSRETAIL","11:22:00","12:11:30",1),
 ("OLAELEC","09:16:00","09:25:00",1),("OLAELEC","09:47:00","09:51:30",1)]
warm=LS.load_warm(LS._prev_session_dir(DAY)); pairs,_=LS.build(DAY,warm)
tape,d0={},{}
for s,(bars,n) in pairs.items():
    bb=[x for x in bars[:n] if x.get("c")]
    tb=[x for x in bars[n:] if x.get("c") and PL.OPEN_T<=x["hhmm"]<=UPTO]
    if len(tb)<3: continue
    tape[s]=bb+tb; d0[s]=len(bb)
avail,_a,_b=FN.build(DAY,tape)
print(f"{'stock':<11}{'his entry':>10} | {'bars?':<6}{'avail from':>11} | {'sig':>4}{'score':>7}{'cum':>7}{'leg':>5} | verdict")
for sym,ent,ex,side in BOOK:
    if sym not in tape:
        print(f"{sym:<11}{ent:>10} | {'NO':<6}{str(avail.get(sym,'-')):>11} | {'-':>4}{'-':>7}{'-':>7}{'-':>5} | NO BARS FETCHED -- invisible to the engine")
        continue
    bars=tape[sym]; av=avail.get(sym)
    e,x,sc=EYE.analyse(sym,bars)
    i=None
    for k in range(d0[sym],len(bars)):
        if bars[k]["hhmm"]>=ent: i=k; break
    if i is None: print(f"{sym:<11}{ent:>10} | bar not found"); continue
    try:
        d=V6.prep(str(EYE.SCRATCH/f"pl_{sym}.csv"))
        cum=round(float(d["cum"][i]),2)
    except Exception: cum=None
    leg = (x[i]-i) if (i<len(x) and x[i] is not None) else None
    sig = e[i] if i<len(e) else 0
    # was it even allowed to trade then?
    late = (av is None or av>ent)
    v=[]
    if late: v.append(f"BLOCKED till {av}")
    if not sig:
        if sc[i]<EYE.SELECT_MIN: v.append(f"score {sc[i]:.2f}<{EYE.SELECT_MIN}")
        elif leg is not None and leg<EYE.MIN_LEG: v.append(f"leg {leg}<{EYE.MIN_LEG}")
        elif cum is not None and cum<EYE.MIN_UP: v.append(f"cum {cum}<{EYE.MIN_UP}")
        else: v.append("v6 gave no signal on this bar")
    else: v.append("SIGNAL PRESENT")
    print(f"{sym:<11}{ent:>10} | {'yes':<6}{str(av):>11} | {str(sig):>4}{sc[i]:>7.2f}{str(cum):>7}{str(leg):>5} | {'; '.join(v)}")
