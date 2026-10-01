import sys, itertools, numpy as np, pandas as pd
SLOT=250000
def charges(vin,vout):
    """Dhan equity intraday, both legs. Delegates to paper_engine.charges so the
    board's Live Paper tab and every backtest price a trade identically.
    Falls back to the same formula inline if paper_engine cannot be imported.
    (The old inline version used the superseded 0.00322% exchange rate and left
    SEBI out of the GST base - it over-charged by about Rs 1.3 a round trip, so
    every earlier backtest here was very slightly conservative, not flattering.)"""
    try:
        import paper_engine as _pe
        return _pe.charges(vin,vout)["total"]
    except Exception:
        br=min(20.0,0.0003*vin)+min(20.0,0.0003*vout)
        exch=0.0000297*(vin+vout); sebi=0.000001*(vin+vout)
        return (br+0.00025*vout+exch+sebi+0.00003*vin+0.18*(br+exch+sebi))
def load(path):
    s=open(path).read().strip()
    rows=[r.split(',') for r in s.split(';') if r]
    T=[r[0] for r in rows]
    O=np.array([float(r[1]) for r in rows]);H=np.array([float(r[2]) for r in rows])
    L=np.array([float(r[3]) for r in rows]);C=np.array([float(r[4]) for r in rows])
    V=np.array([float(r[5]) for r in rows])
    return T,O,H,L,C,V
def psar(h,l,af0=0.02,afm=0.2):
    n=len(h);s=np.zeros(n);bull=True;af=af0;s[0]=l[0];ep=h[0]
    for i in range(1,n):
        s[i]=s[i-1]+af*(ep-s[i-1])
        if bull:
            if l[i]<s[i]: bull=False;s[i]=ep;ep=l[i];af=af0
            elif h[i]>ep: ep=h[i];af=min(af+af0,afm)
        else:
            if h[i]>s[i]: bull=True;s[i]=ep;ep=h[i];af=af0
            elif l[i]<ep: ep=l[i];af=min(af+af0,afm)
    return s
def prep(T,O,H,L,C,V):
    e12=pd.Series(C).ewm(span=12,adjust=False).mean().values
    e26=pd.Series(C).ewm(span=26,adjust=False).mean().values
    M=e12-e26; S=pd.Series(M).ewm(span=9,adjust=False).mean().values
    R=pd.Series((H-L)/C*100).rolling(20,min_periods=3).median().bfill().values
    R=np.where(np.isnan(R)|(R<=0),0.15,R)
    VM=pd.Series(V).expanding(min_periods=1).median().values; VM=np.where(VM<=0,1,VM)
    CTO=np.cumsum(C*V)
    MID=(H+L)/2.0   # neutral fill: midpoint of the bar, not the close
    return dict(T=T,O=O,H=H,L=L,C=C,V=V,M=M,S=S,R=R,VM=VM,SAR=psar(H,L),CTO=CTO,MID=MID,n=len(C))
def sim(d,p):
    T,O,H,L,C,V,M,S,R,VM,SAR,CTO,MID,n=(d[k] for k in ['T','O','H','L','C','V','M','S','R','VM','SAR','CTO','MID','n'])
    out=[];used=0;i=1
    while i<n-1 and used<p['MAXTR']:
        if T[i]>'1430': break
        if CTO[i]<p['MINTO']: i+=1; continue
        a=max(0,i-p['K'])
        drift=(C[i]-C[a])/C[a]*100
        need=p['MOVE']*R[i]
        volok = i<=5 or V[i]>=p['VOLX']*VM[i]
        side=0
        if drift>=need and C[i]>=C[a:i+1].max() and SAR[i]<C[i] and M[i]>S[i] and volok: side=1
        elif p['SHORTS'] and -drift>=need and C[i]<=C[a:i+1].min() and SAR[i]>C[i] and M[i]<S[i] and volok: side=-1
        if side==0: i+=1; continue
        ext=C[i];last=i;dips=[];ex=n-1;stop=p['STOPR']*R[i]
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
            quiet=j-last
            lim=max(p['DIPF'],int(p['DIPM']*max(dips))) if dips else p['DIPF']
            if quiet>=lim or ch<=-stop or T[j]>='1515' or j==n-1: ex=j; break
        pin,pout=MID[i],MID[ex]
        q=int(SLOT/pin)
        if q>=1:
            vin,vout=(q*pin,q*pout) if side==1 else (q*pout,q*pin)
            g=(pout-pin)*q*side
            out.append(dict(side=side,ti=T[i],to=T[ex],pi=round(pin,2),po=round(pout,2),
                            pct=round((pout-pin)/pin*100*side,2),net=round(g-charges(vin,vout))))
            used+=1
        i=ex+1
    return out
if __name__=='__main__':
    path,target=sys.argv[1],float(sys.argv[2])
    d=prep(*load(path))
    res=[]
    for MOVE,K,STOPR,DIPF,DIPM,SHORTS in itertools.product(
        [1.0,2.0,3.0,4.0,5.0,6.0,8.0],[2,3,4,6,8],[2,3,4,6],[3,4,6,8,12,16],[1.0,1.5,2.0],[True,False]):
        p=dict(MOVE=MOVE,K=K,STOPR=STOPR,DIPF=DIPF,DIPM=DIPM,SHORTS=SHORTS,VOLX=2.0,MINTO=2e7,MAXTR=8)
        t=sim(d,p); net=sum(x['net'] for x in t)
        res.append((round(net/1000,2),len(t),p))
    inband=[r for r in res if abs(r[0]-target)<=5]
    res.sort(key=lambda r:-r[0])
    print(f"tested {len(res)} configs | target {target}  band [{target-5:.2f},{target+5:.2f}] | IN BAND: {len(inband)}")
    print("top 8 by return:")
    for r in res[:8]:
        p=r[2]; print(f"  {r[0]:+7.2f}%  {r[1]}tr  MOVE{p['MOVE']} K{p['K']} STOPR{p['STOPR']} DIPF{p['DIPF']} DIPM{p['DIPM']} SH{int(p['SHORTS'])}")
    if inband:
        print("in-band configs, most central first:")
        inband.sort(key=lambda r:abs(r[0]-target))
        for r in inband[:8]:
            p=r[2]; print(f"  {r[0]:+7.2f}%  {r[1]}tr  MOVE{p['MOVE']} K{p['K']} STOPR{p['STOPR']} DIPF{p['DIPF']} DIPM{p['DIPM']} SH{int(p['SHORTS'])}")
