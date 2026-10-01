"""wf_slots.py <day> -- walk-forward one day through the LIVE engine.
Sweeps slot count x shorts-on/off. No fitting, no peeking: each day is scored
with the config as it stands, on that day's own tape."""
import sys; from pathlib import Path
HERE=Path(__file__).parent; sys.path.insert(0,str(HERE))
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
for shorts in (False,True):
    for slots in (1,2,3,5):
        EYE.SHORTS=shorts; EYE._cache.clear(); PL.SLOTS=slots
        closed,_live=PL.run_book(fun,fd0,avail,PL.OPEN_T,UPTO)
        net=sum(c["net"] for c in closed)
        sn=sum(c["net"] for c in closed if c.get("side",1)==-1)
        ns=sum(1 for c in closed if c.get("side",1)==-1)
        w=sum(1 for c in closed if c["net"]>0)
        print(f"{DAY} shorts={str(shorts):<5} slots={slots} n={len(closed):>3} "
              f"S={ns:>2} shortRs={sn:>8,.0f} net={net:>9,.0f} "
              f"win={(round(w*100.0/len(closed)) if closed else 0):>3}% "
              f"pct={net/PL.CAPITAL*100:>6.2f}", flush=True)
