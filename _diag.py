import signal_sim as G, sri_stack as S, entry_lab as E
G.VOL_MODE=True
t=E.load_tape_warm("20260904")
for sym in ["IFCI","LALITHAA"]:
    b,d0=t[sym]; st=S.compute(b,vwap_from=d0); v={"bars":b,"st":st,"d0":d0}
    print("==",sym)
    op=b[d0]["o"]
    for i in range(d0, d0+60):
        r=st[i]; c=b[i]["c"]
        if r["macd"] is None: reason="ind-none"
        else:
            volx=G._vol_stats(v,i)
            win=b[max(0,i-G.VOL_WIN+1):i+1]
            look=b[max(0,i-G.VOL_WIN-G.VOL_NEWHIGH_N+1):max(0,i-G.VOL_WIN+1)]
            hi=max(x["h"] for x in b[d0:i+1]); lo=min(x["l"] for x in b[d0:i+1])
            rng=(hi-lo)/c*100 if c else 0
            vw=r.get("vwap")
            reason=[]
            if volx<G.VOL_X: reason.append(f"vol{volx:.1f}")
            if look and max(x["h"] for x in win)<=max(x["h"] for x in look): reason.append("nonewhigh")
            if c<=win[0]["o"]: reason.append("blockdown")
            if not vw or c<vw: reason.append("belowVWAP")
            elif (c-vw)/vw*100>G.VOL_MAX_VWAP: reason.append("stretched")
            if rng>G.MAX_DAY_RANGE: reason.append(f"rng{rng:.1f}")
            wr=(max(x["h"] for x in win)-min(x["l"] for x in win))/c*100 if c else 0
            if wr<G.MIN_ATR_X*G.COST_PCT: reason.append(f"dead{wr:.2f}")
            frm=(c-op)/op*100 if op else 0
            if frm<G.MIN_FROM_OPEN: reason.append(f"fromopen{frm:.2f}")
            dr=(hi-lo)/lo*100 if lo else 0
            if dr<G.MIN_DAY_RANGE: reason.append(f"dayrng{dr:.2f}")
            reason=",".join(reason) or "*** PASS ***"
        print(b[i]["hhmm"], f"c={c:.2f}", reason)
