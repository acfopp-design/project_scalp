import dirlib as D, runner as R, entry_lab as E, live_shadow as L
UPTO="10:30:00"; GAP=5.0
def load_day(day):
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
def lists(tape,n=10):
    up,dn=[],[]
    for s,b in tape.items():
        o=b[0]["o"] or b[0]["c"]
        if not o: continue
        up.append(((max(x["h"] for x in b)/o-1)*100,s)); dn.append(((min(x["l"] for x in b)/o-1)*100,s))
    up.sort(reverse=True); dn.sort()
    return [s for _,s in up[:n]],[s for _,s in dn[:n]]
DAYS={}
for day,lab in (("20260907","07-Sep"),("20260904","04-Sep")):
    t=load_day(day); bu,be=lists(t); DAYS[lab]=(t,bu,be)
def evaluate(fn, mode):
    """long the bullish 10, short the bearish 10, one book each side combined"""
    res={}
    for lab,(tape,bu,be) in DAYS.items():
        B={s:tape[s] for s in bu}; S={s:tape[s] for s in be}
        db={s:fn(B[s]) for s in B}; ds={s:fn(S[s]) for s in S}
        tl,nl=R.book(B,db,longs=True,shorts=False,mode=mode,upto=UPTO)
        ts,ns=R.book(S,ds,longs=False,shorts=True,mode=mode,upto=UPTO)
        res[lab]=(nl+ns,len(tl)+len(ts),
                  len([x for x in tl+ts if x["net"]>0]))
    return res


def per_stock(fn, mode, upto=UPTO):
    """ONE STOCK, ONE BOOK. Full Rs 5,00,000 exposure on each name in turn, so
    three slots cannot decide the answer. This is the clean comparison of the
    INDICATOR; portfolio construction is a separate problem and mixing the two
    is how the native-mode test above ended up measuring alphabetical order.
    Returns {day: (avg % per stock, total trades, win rate)}."""
    import runner as R
    save = R.PER_SLOT
    R.PER_SLOT = R.CAPITAL * R.LEVERAGE          # all-in, one name at a time
    res = {}
    try:
        for lab, (tape, bu, be) in DAYS.items():
            pcts, ntr, nw = [], 0, 0
            for s in bu:
                d = fn(tape[s])
                t, n = R.book({s: tape[s]}, {s: d}, longs=True, shorts=False,
                              mode=mode, upto=upto)
                pcts.append(n / R.CAPITAL * 100); ntr += len(t)
                nw += len([x for x in t if x["net"] > 0])
            for s in be:
                d = fn(tape[s])
                t, n = R.book({s: tape[s]}, {s: d}, longs=False, shorts=True,
                              mode=mode, upto=upto)
                pcts.append(n / R.CAPITAL * 100); ntr += len(t)
                nw += len([x for x in t if x["net"] > 0])
            res[lab] = (sum(pcts) / len(pcts), ntr, (nw / ntr * 100) if ntr else 0)
    finally:
        R.PER_SLOT = save
    return res
