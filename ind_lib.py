import numpy as np, pandas as pd
def psar_full(h,l,af0=0.02,afm=0.2):
    n=len(h);s=np.zeros(n);bull=np.zeros(n,bool);b=True;af=af0;s[0]=l[0];ep=h[0]
    for i in range(1,n):
        s[i]=s[i-1]+af*(ep-s[i-1])
        if b:
            if l[i]<s[i]: b=False;s[i]=ep;ep=l[i];af=af0
            elif h[i]>ep: ep=h[i];af=min(af+af0,afm)
        else:
            if h[i]>s[i]: b=True;s[i]=ep;ep=h[i];af=af0
            elif l[i]<ep: ep=l[i];af=min(af+af0,afm)
        bull[i]=b
    return s,bull
def atr(h,l,c,p=10):
    pc=np.roll(c,1); pc[0]=c[0]
    tr=np.maximum(h-l,np.maximum(abs(h-pc),abs(l-pc)))
    return pd.Series(tr).ewm(alpha=1/p,adjust=False).mean().values
def heikin(o,h,l,c):
    hc=(o+h+l+c)/4.0; ho=np.zeros(len(c)); ho[0]=(o[0]+c[0])/2
    for i in range(1,len(c)): ho[i]=(ho[i-1]+hc[i-1])/2
    hh=np.maximum.reduce([h,ho,hc]); hl=np.minimum.reduce([l,ho,hc])
    return ho,hh,hl,hc
def supertrend(h,l,c,p=10,m=3.0):
    a=atr(h,l,c,p); hl2=(h+l)/2.0
    ub=hl2+m*a; lb=hl2-m*a
    n=len(c); fu=np.copy(ub); fl=np.copy(lb); tr=np.ones(n)
    for i in range(1,n):
        fu[i]=ub[i] if (ub[i]<fu[i-1] or c[i-1]>fu[i-1]) else fu[i-1]
        fl[i]=lb[i] if (lb[i]>fl[i-1] or c[i-1]<fl[i-1]) else fl[i-1]
        if tr[i-1]==1: tr[i]=-1 if c[i]<fl[i] else 1
        else: tr[i]=1 if c[i]>fu[i] else -1
    return tr, np.where(tr==1,fl,fu)
def ha_supertrend(o,h,l,c,p=10,m=3.0):
    ho,hh,hl,hc=heikin(o,h,l,c)
    return supertrend(hh,hl,hc,p,m)
def ichimoku(h,l,c,t=9,k=26,b=52):
    hi=lambda p: pd.Series(h).rolling(p,min_periods=1).max().values
    lo=lambda p: pd.Series(l).rolling(p,min_periods=1).min().values
    tenkan=(hi(t)+lo(t))/2; kijun=(hi(k)+lo(k))/2
    spanA=np.roll((tenkan+kijun)/2,k); spanB=np.roll((hi(b)+lo(b))/2,k)
    spanA[:k]=np.nan; spanB[:k]=np.nan
    return tenkan,kijun,spanA,spanB
def ema_angle(ema,atr_,k=3):
    """slope over k bars expressed in ATR units -> degrees. Scale-free."""
    sl=np.zeros(len(ema))
    for i in range(k,len(ema)):
        rise=(ema[i]-ema[i-k])/max(atr_[i],1e-9)
        sl[i]=np.degrees(np.arctan2(rise,k))
    return sl
