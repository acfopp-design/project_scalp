"""v6 entry engine: 2 concurrent slots + opening-thrust path + anticipatory path.
No stock names anywhere - everything is derived from the stock's own bars."""
import sys,itertools,numpy as np,pandas as pd
import tune_dyn as TD, tune_v5 as V5
SLOT=250000

def prep(path):
    d=V5.prep(path)
    T,C,O,H,L,V,n=d['T'],d['C'],d['O'],d['H'],d['L'],d['V'],d['n']
    # a file may carry a warm-up tail of the PREVIOUS session so that EMA/SAR/MACD/RSI
    # are already seeded at 09:15, exactly as a continuous TradingView chart shows them.
    # the session boundary is wherever the clock jumps backwards.
    st=0
    for i in range(1,n):
        if T[i]<T[i-1]: st=i; break
    d['st']=st
    d['body']=np.abs(C-O)/C*100
    vm=np.array([np.median(V[st:i+1]) if i>st else max(V[st],1) for i in range(n)])
    d['volx']=V/np.where(vm<=0,1,vm)
    d['cum']=(C-O[st])/O[st]*100          # % from TODAY's open, causal
    # --- dynamic, per-stock entry bar -------------------------------------
    # instead of a fixed "EMA angle >= 20 degrees", ask for an angle that is
    # steep BY THIS STOCK'S OWN STANDARDS TODAY: the expanding quantile of its
    # own angle series, computed causally. A quiet stock qualifies on a gentler
    # slope, a violent one has to work harder.
    # --- causal keep-ratio: how much of the distance it travels does it KEEP? ---
    # Sri's own point: turnover can be huge while price goes nowhere. Over a rolling
    # window, net move / total distance travelled separates a trend from chop.
    W=40
    net_mv=np.abs(C-np.r_[np.full(W,C[0]),C[:-W]])
    dist=pd.Series(np.abs(np.diff(C,prepend=C[0]))).rolling(W,min_periods=5).sum().values
    d['keep']=np.where((dist>0)&~np.isnan(dist),net_mv/np.where(dist<=0,1e-9,dist),0.0)
    # --- causal percentile rank of this bar's volume and range, within the
    # stock's OWN session so far. Sri's 22 entries sit at the 76th percentile for
    # volume and the 92nd for range - but at only ~0.7-1.0x the RAW volume ratio.
    # Percentile inside the stock's own day is the right normalisation; a fixed
    # volume multiple is not, which is why the earlier volume gate failed.
    rngp=(H-L)/C*100
    d['volq']=np.zeros(n); d['rngq']=np.zeros(n)
    for i in range(st,n):
        w=slice(st,i+1)
        d['volq'][i]=(V[w]<=V[i]).mean()
        d['rngq'][i]=(rngp[w]<=rngp[i]).mean()
    # THIS STOCK'S OWN RHYTHM. TRANSRAILL 22-Sep stepped up in bursts with
    # 1-4 bar rests, and a fixed 8-bar dip tolerance fired inside the rest --
    # 14 legs for +3.11% where one held leg made +12.66%. Measure the rest
    # length from the stock's own first 40 bars and let the exit scale to it.
    runs=[];cur=0
    lim=min(n,st+40)
    for i in range(st+1,lim):
        if C[i]<C[i-1]: cur+=1
        else:
            if cur: runs.append(cur)
            cur=0
    if cur: runs.append(cur)
    d['rest75']=float(np.percentile(runs,75)) if runs else 2.0
    A=pd.Series(d['ANG'])
    for q in (60,75,85):
        up=A.expanding(min_periods=30).quantile(q/100).values
        dn=A.expanding(min_periods=30).quantile(1-q/100).values
        d[f'AQ{q}']=np.where(np.isnan(up),1e9,up)
        d[f'AQ{q}n']=np.where(np.isnan(dn),-1e9,dn)
    return d

def sim(d,p):
    (T,O,C,H,L,MID,e8,m12,HIST,RSI,ANG,BULL,HAT,cloud,R,n,body,volx,cum)=(d[k] for k in
     ['T','O','C','H','L','MID','e8','m12','HIST','RSI','ANG','BULL','HAT','cloud','R','n',
      'body','volx','cum'])
    def ride(i,side):
        ext=C[i];last=i;dips=[];stop=p['STOPR']*R[i]
        mode=p.get('EXITMODE','dip')
        # 'rhythm': dip tolerance scaled to how long THIS stock rests
        dipf=p['DIPF']
        if mode=='rhythm':
            dipf=max(4,int(round(d['rest75']*p.get('RESTMULT',3.0))))
        for j in range(i+1,n):
            if side==1:
                if H[j]>ext:
                    if j-last>1: dips.append(j-last)
                    ext=H[j];last=j
                ch=(C[j]-C[i])/C[i]*100
            else:
                if L[j]<ext:
                    if j-last>1: dips.append(j-last)
                    ext=L[j];last=j
                ch=(C[i]-C[j])/C[i]*100
            if mode=='trend':
                # STAY IN WHILE THE STRUCTURE HOLDS. No quiet-time exit at all:
                # only the stop, a close back under EMA8, or EMA8 losing SMA12.
                broke=(C[j]<e8[j]) if side==1 else (C[j]>e8[j])
                cross=(e8[j]<m12[j]) if side==1 else (e8[j]>m12[j])
                if broke or cross or ch<=-stop or T[j]>=p['EXITT'] or j==n-1: return j
                continue
            lim=max(dipf,int(p['DIPM']*max(dips))) if dips else dipf
            if (j-last)>=lim or ch<=-stop or T[j]>=p['EXITT'] or j==n-1: return j
        return n-1
    st=d['st']
    open_until=[]                     # bar index each busy slot frees up
    out=[]; last_in=-99
    for i in range(max(st,1),n-1):
        if T[i]>p['LASTT']: break
        open_until=[x for x in open_until if x>i]
        if len(open_until)>=p.get('SLOTS',1): continue
        if i-last_in<p['COOL']: continue
        if H[i]<=L[i]: continue          # circuit-frozen / no-range bar: never enter
        # ---- path A: opening thrust (no indicator history needed) ----
        openp = (i-st<=p['OPENB'] and C[i]>O[i] and body[i]>=p['OPENBODY']
                 and cum[i]>=p['OPENCUM'] and volx[i]>=p['OPENVOL'])
        # ---- path B: anticipatory continuation ----
        flipL = BULL[i] and not BULL[max(0,i-p['FLIPW'])]
        flipS = (not BULL[i]) and BULL[max(0,i-p['FLIPW'])]
        w=p['RW']; k=max(0,i-w)
        rising= HIST[i]>HIST[k]; rsiup=RSI[i]>RSI[k]
        hiA = d[f"AQ{p['ANGP']}"][i] if p['ANGMODE']=='pct' else p['ANG']
        loA = d[f"AQ{p['ANGP']}n"][i] if p['ANGMODE']=='pct' else -p['SANG']
        longok= (flipL or ANG[i]>=hiA) and e8[i]>m12[i] and rising and rsiup \
                and (HAT[i]==1 if p['USEHA'] else True) \
                and (cloud[i]>=0 if p['USECLOUD'] else True)
        # a short is only allowed once the stock has ALREADY run: every short in
        # Sri's book came after the name was up >6% on the day - he fades an
        # exhausted move, he does not short weakness.
        shortok= p['SHORTS'] and cum[i]>=p['SHORTCUM'] and (flipS or ANG[i]<=loA) and e8[i]<m12[i] \
                and (not rising) and (not rsiup) \
                and (HAT[i]==-1 if p['USEHA'] else True) \
                and (cloud[i]<=0 if p['USECLOUD'] else True)
        # the percentile is meaningless in the first few bars (nothing to rank
        # against yet), so the opening-thrust path is exempt from these gates.
        if i-st>p['OPENB']:
            if p['VOLQ']>0 and d['volq'][i]<p['VOLQ']: longok=False; shortok=False
            if p['RNGQ']>0 and d['rngq'][i]<p['RNGQ']: longok=False; shortok=False
        if p['KEEP']>0 and T[i]>=p['KEEPT'] and d['keep'][i] < p['KEEP']:
            longok=False; shortok=False
        # ---- quality gates, both measured against the stock's OWN scale ----
        # 1. the entry bar must be an expansion bar, not a drift bar
        if p['BODYX']>0 and body[i] < p['BODYX']*R[i]:
            longok=False; shortok=False
        # 2. and it must be taking out recent structure, not entering mid-range
        if p['NHB']>0:
            lo=max(st,i-p['NHB'])
            if longok  and C[i] < np.max(H[lo:i]): longok=False
            if shortok and C[i] > np.min(L[lo:i]): shortok=False
        side = 1 if (openp or longok) else (-1 if shortok else 0)
        if side==0: continue
        ex=ride(i,side)
        pin,pout=MID[i],MID[ex]; q=int(SLOT/pin)
        if q<1: continue
        vin,vout=(q*pin,q*pout) if side==1 else (q*pout,q*pin)
        g=(pout-pin)*q*side
        out.append(dict(side=side,ti=T[i],to=T[ex],i=i,j=ex,
                        pct=round((pout-pin)/pin*100*side,2),
                        net=round(g-TD.charges(vin,vout))))
        open_until.append(ex); last_in=i
        if len(out)>=p['MAXTR']: break
    return out

GRID=dict(SLOTS=[1,2],ANGMODE=['abs'],ANGP=[60],ANG=[5,10,20],SANG=[15],
          FLIPW=[1,2,3],STOPR=[2,3,4],DIPF=[8,12,16],DIPM=[1.0,1.5],
          SHORTS=[True],SHORTCUM=[0,6],USEHA=[False],USECLOUD=[False],OPENB=[4],
          OPENBODY=[0.3],OPENCUM=[0.5],OPENVOL=[1.0],RW=[3],COOL=[4],
          BODYX=[0],NHB=[0],KEEP=[0.08,0.12,0.2],KEEPT=['1015','1100'],
          LASTT=['1430','1520'],EXITT=['1515','1528'],MAXTR=[12,20,30])

def run(path,target,grid=GRID,top=8):
    d=prep(path); keys=list(grid); res=[]
    for combo in itertools.product(*[grid[k] for k in keys]):
        p=dict(zip(keys,combo))
        t=sim(d,p); net=sum(x['net'] for x in t)
        res.append((round(net/1000,2),len(t),p))
    band=[r for r in res if abs(r[0]-target)<=5]
    res.sort(key=lambda r:-r[0])
    print(f"tested {len(res)} | target {target} band[{target-5:.1f},{target+5:.1f}] | IN BAND {len(band)}")
    for r in res[:top]:
        p=r[2]
        print(f"  {r[0]:+7.2f}% {r[1]}tr "+" ".join(f"{k}={p[k]}" for k in keys))
    return res,band

if __name__=='__main__':
    run(sys.argv[1],float(sys.argv[2]))
