import sys,itertools,numpy as np,pandas as pd
import ind_lib as I, tune_dyn as TD
SLOT=250000
def prep(path):
    T,O,H,L,C,V=TD.load(path)
    n=len(C)
    e8=pd.Series(C).ewm(span=8,adjust=False).mean().values
    m12=pd.Series(C).rolling(12,min_periods=1).mean().values
    e12=pd.Series(C).ewm(span=12,adjust=False).mean().values
    e26=pd.Series(C).ewm(span=26,adjust=False).mean().values
    MACD=e12-e26; SIG=pd.Series(MACD).ewm(span=9,adjust=False).mean().values
    HIST=MACD-SIG
    dd=np.diff(C,prepend=C[0]); up=np.where(dd>0,dd,0); dn=np.where(dd<0,-dd,0)
    ru=pd.Series(up).ewm(alpha=1/14,adjust=False).mean().values
    rd=pd.Series(dn).ewm(alpha=1/14,adjust=False).mean().values
    RSI=100-100/(1+np.divide(ru,np.where(rd==0,1e-9,rd)))
    A=I.atr(H,L,C,10); ANG=I.ema_angle(e8,A,3)
    SAR,BULL=I.psar_full(H,L)
    HAT,_=I.ha_supertrend(O,H,L,C)
    tk,kj,sa,sb=I.ichimoku(H,L,C)
    cloud=np.where(np.isnan(sa)|np.isnan(sb),0,np.where(C>np.maximum(sa,sb),1,np.where(C<np.minimum(sa,sb),-1,0)))
    R=pd.Series((H-L)/C*100).rolling(20,min_periods=3).median().bfill().values
    R=np.where(np.isnan(R)|(R<=0),0.15,R)
    MID=(H+L)/2.0
    return dict(T=T,O=O,H=H,L=L,C=C,V=V,MID=MID,e8=e8,m12=m12,MACD=MACD,SIG=SIG,HIST=HIST,
                RSI=RSI,ANG=ANG,SAR=SAR,BULL=BULL,HAT=HAT,cloud=cloud,R=R,n=n)
def sim(d,p):
    (T,C,H,L,MID,e8,m12,MACD,SIG,HIST,RSI,ANG,BULL,HAT,cloud,R,n)=(d[k] for k in
     ['T','C','H','L','MID','e8','m12','MACD','SIG','HIST','RSI','ANG','BULL','HAT','cloud','R','n'])
    out=[];used=0;i=2
    while i<n-1 and used<p['MAXTR']:
        if T[i]>'1430': break
        flipL = BULL[i] and not BULL[max(0,i-p['FLIPW'])]
        flipS = (not BULL[i]) and BULL[max(0,i-p['FLIPW'])]
        rising = HIST[i]>HIST[i-1]
        rsiup  = RSI[i]>RSI[i-1]
        longok = (flipL or ANG[i]>=p['ANG']) and e8[i]>m12[i] and rising and rsiup \
                 and (HAT[i]==1 if p['USEHA'] else True) and (cloud[i]>=0 if p['USECLOUD'] else True)
        shortok= p['SHORTS'] and (flipS or ANG[i]<=-p['ANG']) and e8[i]<m12[i] and (not rising) and (not rsiup) \
                 and (HAT[i]==-1 if p['USEHA'] else True) and (cloud[i]<=0 if p['USECLOUD'] else True)
        side = 1 if longok else (-1 if shortok else 0)
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
            lim=max(p['DIPF'],int(p['DIPM']*max(dips))) if dips else p['DIPF']
            if (j-last)>=lim or ch<=-stop or T[j]>='1515' or j==n-1: ex=j;break
        pin,pout=MID[i],MID[ex]; q=int(SLOT/pin)
        if q>=1:
            vin,vout=(q*pin,q*pout) if side==1 else (q*pout,q*pin)
            g=(pout-pin)*q*side
            out.append(dict(side=side,ti=T[i],to=T[ex],pct=round((pout-pin)/pin*100*side,2),
                            net=round(g-TD.charges(vin,vout))))
            used+=1
        i=ex+1
    return out
if __name__=='__main__':
    path,target=sys.argv[1],float(sys.argv[2])
    d=prep(path); res=[]
    for ANGt,FLIPW,STOPR,DIPF,DIPM,SHORTS,USEHA,USECLOUD in itertools.product(
        [5,10,15,20,30],[1,2,3,5],[2,3,4,6],[4,6,8,12],[1.0,1.5,2.0],[True,False],[True,False],[True,False]):
        p=dict(ANG=ANGt,FLIPW=FLIPW,STOPR=STOPR,DIPF=DIPF,DIPM=DIPM,SHORTS=SHORTS,
               USEHA=USEHA,USECLOUD=USECLOUD,MAXTR=10)
        t=sim(d,p); net=sum(x['net'] for x in t)
        res.append((round(net/1000,2),len(t),p))
    band=[r for r in res if abs(r[0]-target)<=5]
    res.sort(key=lambda r:-r[0])
    print(f"tested {len(res)} | target {target} band[{target-5:.1f},{target+5:.1f}] | IN BAND {len(band)}")
    for r in res[:6]:
        p=r[2];print(f"  {r[0]:+7.2f}% {r[1]}tr ANG{p['ANG']} FLIPW{p['FLIPW']} STOP{p['STOPR']} DIPF{p['DIPF']} DIPM{p['DIPM']} SH{int(p['SHORTS'])} HA{int(p['USEHA'])} CL{int(p['USECLOUD'])}")
