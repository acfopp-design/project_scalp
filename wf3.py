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
CASES=[("baseline",0,0,0),("MAX_UP=6",6,0,0),("MAX_UP=8",8,0,0),("MAX_UP=10",10,0,0),
       ("OPEN_FREE=15",0,15,0),("OPEN_FREE=30",0,30,0),
       ("COOLDOWN=20",0,0,20),("COOLDOWN=45",0,0,45)]
for nm,mu,of,cd in CASES:
    EYE.MAX_UP=mu; EYE.OPEN_FREE=of; PL.COOLDOWN_MIN=cd; EYE._cache.clear()
    closed,_l=PL.run_book(fun,fd0,avail,PL.OPEN_T,UPTO)
    net=sum(c["net"] for c in closed); w=sum(1 for c in closed if c["net"]>0)
    print(f"{DAY} {nm:<14} n={len(closed):>3} net={net:>9,.0f} win={(round(w*100.0/len(closed)) if closed else 0):>3}%", flush=True)
