"""Does restricting v6 to the few BEST-LOOKING names fix the -4.37%?
Selection score is causal and per-stock: volume percentile + range percentile +
keep-ratio, all measured inside that stock's own session so far. Sri's 22 entries
sat at the 76th / 92nd / 0.28 of these."""
import numpy as np, pandas as pd, tune_v6 as V6, tune_dyn as TD
from eye_cfg import CFG
import eye_signals as ES
DAY,PREV='20260921','20260918'
SLOT=250000
uni=set(ES._universe(DAY))
tdir=ES.HERE/'logs'/'tape_live'/DAY
D={}
for f in sorted(tdir.glob('*.json')):
    sym=f.stem
    if uni and sym not in uni: continue
    today=[r for r in ES._series(DAY,sym) if '0915'<=r[0]<='1530']
    if len(today)<50: continue
    rows=(ES._series(PREV,sym)[-60:])+today
    p=ES.HERE/'logs'/'_eye_scratch'/f'{sym}.csv'
    p.write_text(';'.join(f'{t},{o:g},{h:g},{l:g},{c:g},{int(v)}' for t,o,h,l,c,v in rows))
    try: D[sym]=V6.prep(str(p))
    except Exception: pass
print('names with usable tape:',len(D))
cand=[]
for sym,d in D.items():
    T,st,n=d['T'],d['st'],d['n']
    idx={T[i]:i for i in range(n-1,st-1,-1)}
    for t in V6.sim(d,dict(CFG,SLOTS=99,MAXTR=999)):
        i=idx[t['ti']]
        cand.append(dict(sym=sym,score=d['volq'][i]+d['rngq'][i]+min(d['keep'][i],1.0),**t))
print('total candidate legs:',len(cand))
def book(cands,slots=2,topn=None,minscore=None,persym=1):
    from collections import defaultdict
    bytime=defaultdict(list)
    for c in cands: bytime[c['ti']].append(c)
    busy=[]; held=defaultdict(int); taken=[]
    for tt in sorted(bytime):
        busy=[b for b in busy if b[0]>tt]
        held=defaultdict(int)
        for b in busy: held[b[1]]+=1
        pool=sorted(bytime[tt],key=lambda r:-r['score'])
        if minscore is not None: pool=[c for c in pool if c['score']>=minscore]
        if topn is not None: pool=pool[:topn]
        for c in pool:
            if len(busy)>=slots: break
            if held[c['sym']]>=persym: continue
            taken.append(c); busy.append((c['to'],c['sym'])); held[c['sym']]+=1
    return pd.DataFrame(taken)
def book2(cands,slots,slot_rs,topn=1,minscore=1.5,persym=1):
    from collections import defaultdict
    import tune_dyn as TD
    bytime=defaultdict(list)
    for c in cands: bytime[c['ti']].append(c)
    busy=[]; taken=[]
    for tt in sorted(bytime):
        busy=[b for b in busy if b[0]>tt]
        held=defaultdict(int)
        for b in busy: held[b[1]]+=1
        pool=[c for c in sorted(bytime[tt],key=lambda r:-r['score']) if c['score']>=minscore][:topn]
        for c in pool:
            if len(busy)>=slots: break
            if held[c['sym']]>=persym: continue
            d=D[c['sym']]; i={d['T'][k]:k for k in range(d['n']-1,d['st']-1,-1)}
            pin=d['MID'][i[c['ti']]]; pout=d['MID'][i[c['to']]]
            q=int(slot_rs/pin)
            if q<1: continue
            g=(pout-pin)*q*c['side']
            vin,vout=(q*pin,q*pout) if c['side']==1 else (q*pout,q*pin)
            taken.append(dict(sym=c['sym'],net=g-TD.charges(vin,vout)))
            busy.append((c['to'],c['sym'])); held[c['sym']]+=1
    import pandas as pd
    return pd.DataFrame(taken)
print()
print('%-42s %6s %5s %9s'%('capital model','trades','wins','net %'))
for lab,sl,rs in [('2 slots x Rs 2,50,000  (what I measured)',2,250000),
                  ('6 units x Rs 83,333    (the tab today)',6,500000/6),
                  ('3 slots x Rs 1,66,667',3,500000/3),
                  ('4 slots x Rs 1,25,000',4,125000)]:
    T=book2(cand,sl,rs)
    print('%-42s %6d %5d %+8.2f%%'%(lab,len(T),(T.net>0).sum(),T.net.sum()/1000))
import sys; sys.exit()
print('\n%-34s %5s %5s %9s'%('rule','trades','wins','net %'))
for lab,kw in [('all candidates, earliest first',dict()),
               ('one leg per name at a time',dict(persym=1)),
               ('score >= 1.2',dict(minscore=1.2)),
               ('score >= 1.5',dict(minscore=1.5)),
               ('score >= 1.8',dict(minscore=1.8)),
               ('score >= 2.0',dict(minscore=2.0)),
               ('best 1 name in the bar',dict(topn=1)),
               ('best 1 + score >= 1.5',dict(topn=1,minscore=1.5)),
               ('best 1 + score >= 1.8',dict(topn=1,minscore=1.8))]:
    T=book(cand,**kw)
    if len(T)==0: print('%-34s %5d %5d %9s'%(lab,0,0,'-')); continue
    print('%-34s %5d %5d %+8.2f%%'%(lab,len(T),(T.net>0).sum(),T.net.sum()/1000))

