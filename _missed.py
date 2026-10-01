"""Every qualified signal the engine REJECTED purely because both slots were
busy -- and what that leg actually went on to do. Returns are in %, so the
comparison is capital-neutral: it isolates SELECTION from CAPITAL."""
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
EYE.MIN_LEG=10; PL.SLOTS=2; PL.RANK="leg"; EYE._cache.clear()
eye={s:EYE.analyse(s,b) for s,b in fun.items()}
idx={s:{b["hhmm"]:i for i,b in enumerate(bars) if i>=fd0.get(s,0)} for s,bars in fun.items()}
closed,live=PL.run_book(fun,fd0,avail,PL.OPEN_T,UPTO)
taken={(c["sym"],c["in_t"]) for c in closed}
clock=sorted({bars[i]["hhmm"] for s,bars in fun.items()
              for i in range(fd0.get(s,0),len(bars))
              if PL.OPEN_T<=bars[i]["hhmm"]<=UPTO})
# rebuild occupancy from the closed book
occ=[]
for c in closed: occ.append((c["in_t"],c["out_t"]))
def busy(t): return sum(1 for a,b in occ if a<=t<b)
missed=[]
for t in clock:
    if busy(t)<PL.SLOTS: continue
    for s,bars in fun.items():
        if t<avail.get(s,"99:99:99"): continue
        i=idx[s].get(t)
        if i is None or i+1>=len(bars): continue
        ent,exi,sc=eye.get(s,([],[],[]))
        if i>=len(ent) or not ent[i]: continue
        if (s,bars[i+1]["hhmm"]) in taken: continue
        j=exi[i]
        if j is None or j>=len(bars): continue
        pin=bars[i+1]["o"] or bars[i+1]["c"]; pout=bars[j]["c"]
        if not pin: continue
        pct=(pout-pin)/pin*100 - 0.055     # minus round-trip charges
        missed.append((t,s,pct,(j-i)))
tk=[ (c["out"]/c["in"]-1)*100 - 0.055 for c in closed]
print(f"\n{DAY}")
print(f"  TAKEN   {len(tk):>4} trades | avg {sum(tk)/max(1,len(tk)):+6.2f}% | winners {sum(1 for x in tk if x>0)*100//max(1,len(tk))}%")
print(f"  MISSED  {len(missed):>4} signals (slots busy) | avg {sum(m[2] for m in missed)/max(1,len(missed)):+6.2f}% | winners {sum(1 for m in missed if m[2]>0)*100//max(1,len(missed))}%")
missed.sort(key=lambda m:-m[2])
print("  best 5 missed:", ", ".join(f"{m[1]} {m[0]} {m[2]:+.1f}%" for m in missed[:5]))

# --- does any entry-time feature PREDICT the big ones in the missed pool? ---
import numpy as _np
if missed:
    uniq={}
    for t,s,pct,lg in missed:          # collapse repeats of the same name/leg
        k=(s,lg)
        if k not in uniq or pct>uniq[k][0]: uniq[k]=(pct,lg)
    xs=_np.array([v[1] for v in uniq.values()],float)
    ys=_np.array([v[0] for v in uniq.values()],float)
    print(f"  distinct missed opportunities: {len(uniq)}")
    if len(xs)>3:
        print(f"  corr(leg length, realised %) = {_np.corrcoef(xs,ys)[0,1]:+.3f}")
    for lo,hi in ((10,14),(15,19),(20,29),(30,999)):
        m=(xs>=lo)&(xs<=hi)
        if m.sum(): print(f"    leg {lo:>3}-{hi if hi<999 else '+':<3} n={int(m.sum()):>3}  avg {ys[m].mean():+6.2f}%  best {ys[m].max():+6.2f}%")
