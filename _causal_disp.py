"""Displacement, tested CAUSALLY. The walk-forward harness hands run_book the
whole day, so a candidate's predicted leg is its TRUE leg -- knowledge the live
engine cannot have at 10:27. Displacement compares predicted legs directly, so
it is the most hindsight-sensitive thing we have built. This replays in steps and
locks each trade in when it actually completed."""
import sys,os; sys.path.insert(0,os.getcwd())
import funnel as FN, live_shadow as LS, paper_live as PL, eye_strategy as EYE
DAY=sys.argv[1]
warm=LS.load_warm(LS._prev_session_dir(DAY)); pairs,_=LS.build(DAY,warm)
def book(upto,disp):
    tape,d0={},{}
    for s,(bars,n) in pairs.items():
        bb=[x for x in bars[:n] if x.get("c")]
        tb=[x for x in bars[n:] if x.get("c") and PL.OPEN_T<=x["hhmm"]<=upto]
        if len(tb)<3: continue
        tape[s]=bb+tb; d0[s]=len(bb)
    avail,_a,_b=FN.build(DAY,tape)
    fun={s:tape[s] for s in avail if s in tape}; fd0={s:d0[s] for s in fun}
    PL.SLOTS=2; PL.RANK="leg"; PL.DISPLACE=disp
    EYE.MIN_LEG=10; EYE.SHORTS=True; EYE.LAST_ENTRY="1130"; EYE._cache.clear()
    return PL.run_book(fun,fd0,avail,PL.OPEN_T,upto)[0]
CUTS=["10:00:00","10:45:00","11:30:00","12:15:00","15:15:00"]
for disp in (0,2.0):
    seen={}
    for cut in CUTS:
        for c in book(cut,disp):
            k=(c["sym"],c["in_t"])
            if k not in seen and str(c["out_t"])<=cut:
                seen[k]=c["net"]
    one=book("15:15:00",disp)
    print(f"{DAY} DISPLACE={disp:<4} | CAUSAL {len(seen):>3} trades Rs {sum(seen.values()):>9,.0f}"
          f"   | whole-day-visible {len(one):>3} trades Rs {sum(c['net'] for c in one):>9,.0f}", flush=True)
