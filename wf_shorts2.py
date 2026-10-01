"""Shorts re-tested under the CURRENT config: RANK=leg, MIN_LEG=10, 2 slots.
The original rejection was measured at MIN_LEG=4 with score ranking, which is
exactly the stale-baseline mistake the slots question exposed."""
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
PL.SLOTS=2; PL.RANK="leg"; EYE.MIN_LEG=10
for sh in (False,True):
    EYE.SHORTS=sh; EYE._cache.clear()
    closed,_l=PL.run_book(fun,fd0,avail,PL.OPEN_T,UPTO)
    net=sum(c["net"] for c in closed)
    sn=sum(c["net"] for c in closed if c.get("side",1)==-1)
    ns=sum(1 for c in closed if c.get("side",1)==-1)
    w=sum(1 for c in closed if c["net"]>0)
    print(f"{DAY} shorts={str(sh):<5} n={len(closed):>3} S={ns:>2} shortRs={sn:>8,.0f} "
          f"net={net:>9,.0f} win={(round(w*100.0/len(closed)) if closed else 0):>3}%", flush=True)
