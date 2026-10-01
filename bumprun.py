import entry_lab as E, live_shadow as L
UPTO="15:15:00"; GAP=5.0
def load(day, upto=UPTO):
    if day=="20260907":
        warm=L.load_warm(L._prev_session_dir(day))
        today=L.load_today(day); today.update(L.load_live(day))
        src={s:(warm.get(s,[])+b,len(warm.get(s,[]))) for s,b in today.items()}
    else:
        src=E.load_tape_warm(day)
    tape,warms={},{}
    for s,(bars,d0) in src.items():
        b=[x for x in bars[d0:] if x["hhmm"]<=upto and x["c"]]
        if len(b)<40: continue
        pc=bars[d0-1]["c"] if d0 else None
        gap=((b[0]["o"]/pc-1)*100) if (pc and b[0]["o"]) else 0.0
        if abs(gap)>GAP: continue
        tape[s]=b; warms[s]=bars[:d0]
    return tape,warms
DAYS={}
for d,lab in (("20260902","02-Sep"),("20260903","03-Sep"),("20260904","04-Sep"),("20260907","07-Sep")):
    t,w=load(d)
    if len(t)>=20: DAYS[lab]=(t,w)
