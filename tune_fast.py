"""Same engine as tune_v6, but the exit is precomputed once per (exit-params, bar).
ride() depends only on the bar and the exit parameters, never on the entry rule,
so the whole grid reuses one table instead of re-walking the leg every time."""
import sys,itertools,pickle,numpy as np
import tune_v6 as V6, tune_dyn as TD
from eye_targets import TARGETS
SLOT=250000
FILES={'AGI':'AGIGREENPAC_20260921.csv','PROTEAN':'PROTEAN_20260921.csv',
       'INDOMIM':'INDOMIM_20260921.csv','RAYMOND':'RAYMOND_20260921.csv',
       'FILATEX':'FILATEX_20260921.csv'}
EXITK=['STOPR','DIPF','DIPM','EXITT']
ENTK =['SLOTS','ANGMODE','ANGP','ANG','SANG','FLIPW','SHORTS','SHORTCUM','USEHA','USECLOUD','OPENB','OPENBODY',
       'OPENCUM','OPENVOL','RW','COOL','BODYX','NHB','KEEP','KEEPT','LASTT','MAXTR']

def ride_table(d,ep,side):
    T,C,H,L,R,n=d['T'],d['C'],d['H'],d['L'],d['R'],d['n']
    ex=np.zeros(n,dtype=np.int32)
    for i in range(n-1):
        stop=ep['STOPR']*R[i]; extv=C[i]; last=i; mx=0; out=n-1
        for j in range(i+1,n):
            if side==1:
                if H[j]>extv:
                    if j-last>1 and j-last>mx: mx=j-last
                    extv=H[j]; last=j
                ch=(C[j]-C[i])/C[i]*100
            else:
                if L[j]<extv:
                    if j-last>1 and j-last>mx: mx=j-last
                    extv=L[j]; last=j
                ch=(C[i]-C[j])/C[i]*100
            lim=max(ep['DIPF'],int(ep['DIPM']*mx)) if mx else ep['DIPF']
            if (j-last)>=lim or ch<=-stop or T[j]>=ep['EXITT'] or j==n-1:
                out=j; break
        ex[i]=out
    ex[n-1]=n-1
    return ex

def entry_flags(d,p):
    """boolean arrays: can this bar open a long / a short under entry params p"""
    T,O,C,H,L,e8,m12,HIST,RSI,ANG,BULL,HAT,cloud,n,body,volx,cum,st=(d[k] for k in
     ['T','O','C','H','L','e8','m12','HIST','RSI','ANG','BULL','HAT','cloud','n','body','volx','cum','st'])
    idx=np.arange(n)
    w=p['FLIPW']; prev=np.maximum(0,idx-w)
    flipL=BULL&(~BULL[prev]); flipS=(~BULL)&BULL[prev]
    w2=p['RW']; k=np.maximum(0,idx-w2)
    rising=HIST>HIST[k]; rsiup=RSI>RSI[k]
    if p['ANGMODE']=='pct':
        hiA=d[f"AQ{p['ANGP']}"]; loA=d[f"AQ{p['ANGP']}n"]
    else:
        hiA=np.full(n,p['ANG'],float); loA=np.full(n,-p['SANG'],float)
    longok=(flipL|(ANG>=hiA))&(e8>m12)&rising&rsiup
    if p['USEHA']:    longok&=(HAT==1)
    if p['USECLOUD']: longok&=(cloud>=0)
    shortok=np.zeros(n,bool)
    if p['SHORTS']:
        shortok=(flipS|(ANG<=loA))&(e8<m12)&(~rising)&(~rsiup)&(cum>=p['SHORTCUM'])
        if p['USEHA']:    shortok&=(HAT==-1)
        if p['USECLOUD']: shortok&=(cloud<=0)
    openp=(idx-st<=p['OPENB'])&(C>O)&(body>=p['OPENBODY'])&(cum>=p['OPENCUM'])&(volx>=p['OPENVOL'])
    if p['KEEP']>0:
        Tarr=np.array(d['T']); okk=(d['keep']>=p['KEEP'])|(Tarr<p['KEEPT'])
        longok&=okk; shortok&=okk
    if p['BODYX']>0:
        okbar=body>=p['BODYX']*d['R']; longok&=okbar; shortok&=okbar
    if p['NHB']>0:
        nb=p['NHB']
        hmax=np.array([H[max(st,i-nb):i].max() if i>max(st,0) else np.inf for i in range(n)])
        lmin=np.array([L[max(st,i-nb):i].min() if i>max(st,0) else -np.inf for i in range(n)])
        longok&=(C>=hmax); shortok&=(C<=lmin)
    live=(H>L)
    return (longok|openp)&live, shortok&live

def run_sym(d,exL,exS,p,okL,okS):
    T,C,MID,n,st=d['T'],d['C'],d['MID'],d['n'],d['st']
    open_until=[]; net=0.0; ntr=0; last_in=-99
    for i in range(max(st,1),n-1):
        if T[i]>p['LASTT']: break
        open_until=[x for x in open_until if x>i]
        if len(open_until)>=p['SLOTS']: continue
        if i-last_in<p['COOL']: continue
        if okL[i]: side=1; ex=exL[i]
        elif okS[i]: side=-1; ex=exS[i]
        else: continue
        pin,pout=MID[i],MID[ex]; q=int(SLOT/pin)
        if q<1: continue
        vin,vout=(q*pin,q*pout) if side==1 else (q*pout,q*pin)
        net+=(pout-pin)*q*side-TD.charges(vin,vout)
        open_until.append(ex); last_in=i; ntr+=1
        if ntr>=p['MAXTR']: break
    return net/1000,ntr

def main():
    syms=sys.argv[1].split(',')
    sh,nsh=(int(sys.argv[2]),int(sys.argv[3])) if len(sys.argv)>3 else (0,1)
    D={s:V6.prep('backtest_data/intraday/'+FILES[s]) for s in syms}
    G=V6.GRID
    exit_combos=[c for n,c in enumerate(itertools.product(*[G[k] for k in EXITK])) if n%nsh==sh]
    ent_combos =list(itertools.product(*[G[k] for k in ENTK]))
    print(f'exit combos {len(exit_combos)}  entry combos {len(ent_combos)}  '
          f'= {len(exit_combos)*len(ent_combos)} configs x {len(syms)} stocks',flush=True)
    TAB={s:{} for s in syms}
    for ec in exit_combos:
        ep=dict(zip(EXITK,ec))
        for s in syms: TAB[s][ec]=(ride_table(D[s],ep,1),ride_table(D[s],ep,-1))
    print('exit tables built',flush=True)
    res=[]
    for en in ent_combos:
        pe=dict(zip(ENTK,en))
        FL={s:entry_flags(D[s],pe) for s in syms}
        for ec in exit_combos:
            p=dict(pe); p.update(dict(zip(EXITK,ec)))
            row={}; worst=0
            for s in syms:
                okL,okS=FL[s]; exL,exS=TAB[s][ec]
                v,_=run_sym(D[s],exL,exS,p,okL,okS)
                row[s]=round(v,2); worst=max(worst,abs(v-TARGETS[s]))
            tot=sum(row.values())
            res.append((round(worst,2),round(abs(tot-sum(TARGETS[s] for s in syms)),2),row,p))
    res.sort(key=lambda r:(max(0,r[0]-5),max(0,r[1]-10),r[0]+r[1]))
    import collections
    per=collections.defaultdict(float)
    for r in res:
        for k,v in r[2].items(): per[k]=max(per[k],v)
    print('per-stock BEST achievable in this shard:',{k:round(v,2) for k,v in per.items()},flush=True)
    ok4=[r for r in res if sum(1 for k in r[2] if abs(r[2][k]-TARGETS[k])<=5)>=4]
    print('configs with >=4 in band:',len(ok4),flush=True)
    if ok4:
        ok4.sort(key=lambda r:-min(r[2][k]-TARGETS[k]+5 for k in r[2]))
        for r in ok4[:3]:
            print('  ',{k:round(v,2) for k,v in r[2].items()})
            print('     '+' '.join(f'{k}={r[3][k]}' for k in G if len(G[k])>1),flush=True)
    pickle.dump(res[:300],open(f'_best{sh}.pkl','wb'))
    full=[r for r in res if r[0]<=5 and r[1]<=10]
    print(f'PER-STOCK-IN-BAND {sum(1 for r in res if r[0]<=5)}  FULL PASS (also portfolio +/-10): {len(full)} of {len(res)}')
    for r in res[:5]:
        print(f'  worst{r[0]:6.2f} tot-diff{r[1]:6.2f} {r[2]}')
        print('     '+' '.join(f'{k}={r[3][k]}' for k in G if len(G[k])>1))
main()
