"""At 09:15:00, what was on the table and how were the 2 slots allocated?"""
import sys,os; sys.path.insert(0,os.getcwd())
import funnel as FN, live_shadow as LS, paper_live as PL, eye_strategy as EYE
DAY="20260923"; UPTO="15:15:00"
warm=LS.load_warm(LS._prev_session_dir(DAY)); pairs,_=LS.build(DAY,warm)
tape,d0={},{}
for s,(bars,n) in pairs.items():
    bb=[x for x in bars[:n] if x.get("c")]
    tb=[x for x in bars[n:] if x.get("c") and PL.OPEN_T<=x["hhmm"]<=UPTO]
    if len(tb)<3: continue
    tape[s]=bb+tb; d0[s]=len(bb)
avail,_a,_b=FN.build(DAY,tape)
fun={s:tape[s] for s in avail if s in tape}; fd0={s:d0[s] for s in fun}
EYE.MIN_LEG=10; EYE._cache.clear()
eye={s:EYE.analyse(s,b) for s,b in fun.items()}
idx={s:{b["hhmm"]:i for i,b in enumerate(bars) if i>=fd0.get(s,0)} for s,bars in fun.items()}
for T in ("09:15:00","09:15:30","09:16:00"):
    cands=[]
    for s,bars in fun.items():
        if T < avail.get(s,"99:99:99"): continue
        i=idx[s].get(T)
        if i is None or i+1>=len(bars): continue
        ent,exi,sc=eye.get(s,([],[],[]))
        if i>=len(ent) or not ent[i]: continue
        cands.append((sc[i],s,(exi[i]-i) if exi[i] is not None else None))
    cands.sort(key=lambda x:-x[0])
    print(f"\n=== clock {T}: {len(cands)} qualified candidates, 2 slots ===")
    for sc_,s,leg in cands[:8]:
        print(f"   score {sc_:>5.2f}  leg {str(leg):>4}  {s}")
