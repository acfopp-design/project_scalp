"""What did the winners have at ENTRY that the losers did not?"""
import sys, json; from pathlib import Path
HERE=Path(__file__).parent; sys.path.insert(0,str(HERE))
import funnel as FN, live_shadow as LS, paper_live as PL, eye_strategy as EYE
import tune_v6 as V6
from eye_cfg import CFG
DAY="20260923"
d=json.load(open('logs/paper_live_20260923.json'))
tr=d['trades']
warm=LS.load_warm(LS._prev_session_dir(DAY)); pairs,_=LS.build(DAY,warm)
rows=[]
for t in tr:
    s=t['sym']
    if s not in pairs: continue
    bars,n0=pairs[s]
    # rebuild eye features exactly as the live engine saw them
    ent,exi,sc=EYE.analyse(s,bars)
    # locate the signal bar by its hhmm (sig_hms)
    sig=t.get('sig_hms') or t['in_hms']
    i=None
    for k in range(n0,len(bars)):
        if bars[k]['hhmm']==sig: i=k; break
    if i is None: continue
    try:
        f=EYE.SCRATCH/f"pl_{s}.csv"; dd=V6.prep(str(f))
    except Exception: continue
    if i>=len(dd['C']): continue
    leg = (exi[i]-i) if (i<len(exi) and exi[i] is not None) else None
    rows.append(dict(sym=s, net=t['net'], pct=(t['out']/t['in']-1)*100,
        held=t['held'], score=sc[i] if i<len(sc) else None,
        cum=round(float(dd['cum'][i]),2), ang=round(float(dd['ANG'][i]),1),
        volq=round(float(dd['volq'][i]),2), rngq=round(float(dd['rngq'][i]),2),
        keep=round(float(dd['keep'][i]),2), leg=leg, t=sig))
rows.sort(key=lambda r:-r['net'])
print(f"{'sym':<11}{'net':>8}{'pct':>7}{'held':>5}{'score':>6}{'cum':>7}{'ang':>7}{'volq':>6}{'rngq':>6}{'keep':>6}{'leg':>5}  {'sig'}")
for r in rows:
    print(f"{r['sym']:<11}{r['net']:>8,.0f}{r['pct']:>7.2f}{r['held']:>5}"
          f"{(r['score'] or 0):>6.2f}{r['cum']:>7.2f}{r['ang']:>7.1f}{r['volq']:>6.2f}"
          f"{r['rngq']:>6.2f}{r['keep']:>6.2f}{str(r['leg']):>5}  {r['t']}")
W=[r for r in rows if r['net']>0]; L=[r for r in rows if r['net']<=0]
def avg(a,k): return sum(x[k] or 0 for x in a)/max(1,len(a))
print()
for k in ('score','cum','ang','volq','rngq','keep','leg'):
    print(f"  {k:<6} winners {avg(W,k):>7.2f}   losers {avg(L,k):>7.2f}")
