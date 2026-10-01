"""Slots re-tested at MIN_LEG=10. The original slot study ran at MIN_LEG=4,
where every extra slot bought junk. With the junk gone the economics may flip."""
import sys,os; sys.path.insert(0,os.getcwd())
import funnel as FN, live_shadow as LS, paper_live as PL, eye_strategy as EYE
DAY=sys.argv[1]; UPTO="15:15:00"
warm=LS.load_warm(LS._prev_session_dir(DAY)); pairs,_=LS.build(DAY,warm)
tape,d0={},{}
for s,(bars,n) in pairs.items():
    bb=[x for x in bars[:n] if x.get("c")]
    tb=[x for x in bars[n:] if x.get("c") and PL.OPEN_T<=x["hhmm"]<=UPTO]
    if len(tb)<3: continue
    tape[s]=bb+tb; d0[s]=len(bb)
avail,_a,_b=FN.build(DAY,tape)
fun={s:tape[s] for s in avail if s in tape}; fd0={s:d0[s] for s in fun}
EYE.MIN_LEG=10
for sl in (2,3,4,5,6,8):
    EYE._cache.clear(); PL.SLOTS=sl
    closed,_l=PL.run_book(fun,fd0,avail,PL.OPEN_T,UPTO)
    net=sum(c["net"] for c in closed); w=sum(1 for c in closed if c["net"]>0)
    print(f"{DAY} MIN_LEG=10 slots={sl:<2} n={len(closed):>3} net={net:>9,.0f} "
          f"win={(round(w*100.0/len(closed)) if closed else 0):>3}% pct={net/PL.CAPITAL*100:>7.2f}", flush=True)
