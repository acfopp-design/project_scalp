import dirlib as D, entry_lab as E, live_shadow as L
UPTO="10:30:00"; GAP=5.0
def load(day):
    if day=="20260907":
        warm=L.load_warm(L._prev_session_dir(day))
        today=L.load_today(day); today.update(L.load_live(day))
        src={s:(warm.get(s,[])+b,len(warm.get(s,[]))) for s,b in today.items()}
    else:
        src=E.load_tape_warm(day)
    out={}
    for s,(bars,d0) in src.items():
        b=[x for x in bars[d0:] if x["hhmm"]<=UPTO and x["c"]]
        if len(b)<20: continue
        pc=bars[d0-1]["c"] if d0 else None
        gap=((b[0]["o"]/pc-1)*100) if (pc and b[0]["o"]) else 0.0
        if abs(gap)>GAP: continue
        out[s]=b
    return out
def top(tape,n=10):
    up,dn=[],[]
    for s,b in tape.items():
        o=b[0]["o"] or b[0]["c"]
        if not o: continue
        up.append(((max(x["h"] for x in b)/o-1)*100,s)); dn.append(((min(x["l"] for x in b)/o-1)*100,s))
    up.sort(reverse=True); dn.sort()
    return [s for _,s in up[:n]],[s for _,s in dn[:n]]
DAYS={}
for d,lab in (("20260902","02-Sep"),("20260903","03-Sep"),("20260904","04-Sep"),("20260907","07-Sep")):
    try:
        t=load(d)
        if len(t)<20: print(f"{lab}: only {len(t)} stocks -- skipped"); continue
        bu,be=top(t); DAYS[lab]=(t,bu,be)
    except Exception as e:
        print(f"{lab}: {type(e).__name__} {e}")
