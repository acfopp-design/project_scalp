import sys,os; sys.path.insert(0,os.getcwd())
import funnel as FN, live_shadow as LS, paper_live as PL, eye_strategy as EYE
import tune_v6 as V6
from eye_cfg import CFG
DAY="20260923"; UPTO="15:15:00"
HIS={"EKC":["09:17:00"],"CONFIPET":["09:17:00","10:30:00","11:02:30"],
 "ARCIL":["10:07:30","10:35:00"],"KANOHAR":["09:16:00","09:40:00(S)"],
 "WOCKPHARMA":["09:24:00","09:48:30","10:04:30"],"SSRETAIL":["11:22:00"],
 "OLAELEC":["09:16:00","09:47:00"]}
warm=LS.load_warm(LS._prev_session_dir(DAY)); pairs,_=LS.build(DAY,warm)
for sym in HIS:
    if sym not in pairs: print(sym,"no tape"); continue
    bars,n=pairs[sym]
    bb=[x for x in bars[:n] if x.get("c")]
    tb=[x for x in bars[n:] if x.get("c") and PL.OPEN_T<=x["hhmm"]<=UPTO]
    b=bb+tb
    EYE._cache.clear(); EYE.analyse(sym,b)
    try: d=V6.prep(str(EYE.SCRATCH/f"pl_{sym}.csv"))
    except Exception as e: print(sym,"prep fail",e); continue
    tr=V6.sim(d,dict(CFG,SLOTS=99,MAXTR=999,EXITMODE=EYE.EXITMODE))
    fired=[(t["ti"],t["to"],t["side"],t["pct"],t["j"]-t["i"]) for t in tr]
    print(f"\n{sym}  HIS: {', '.join(HIS[sym])}")
    print(f"   v6 fired {len(fired)} legs:")
    for ti,to,sd,pc,lg in fired[:10]:
        print(f"     {ti}->{to}  {'LONG ' if sd==1 else 'SHORT'} {pc:+6.2f}%  leg {lg}")
