import numpy as np, tune_dyn as TD, tune_v5 as V5
p='backtest_data/intraday/AGIGREENPAC_20260921.csv'
d=V5.prep(p)
T,C,H,L,MID,n=d['T'],d['C'],d['H'],d['L'],d['MID'],d['n']
ENT=['0915','0933','1013','1144','1220','1434','1452']
idx={}
for t in ENT:
    k=[i for i in range(n) if T[i].startswith(t)]
    idx[t]=k[0] if k else None
print('bars',n,'first',T[0],'last',T[-1])
# adaptive ride from each entry, long only
def ride(i,DIPF=8,DIPM=1.5,STOP=2.0):
    R=d['R'][i]; ext=C[i]; last=i; dips=[]
    for j in range(i+1,n):
        if H[j]>ext:
            if j-last>1: dips.append(j-last)
            ext=H[j]; last=j
        ch=(C[j]-C[i])/C[i]*100
        lim=max(DIPF,int(DIPM*max(dips))) if dips else DIPF
        if (j-last)>=lim or ch<=-STOP*R or T[j]>='1515' or j==n-1: return j
    return n-1
tot=0; totn=0
SLOT=250000
for t in ENT:
    i=idx[t]
    if i is None: print(t,'MISSING'); continue
    j=ride(i)
    pin,pout=MID[i],MID[j]; q=int(SLOT/pin)
    g=(pout-pin)*q; net=g-TD.charges(q*pin,q*pout)
    # oracle exit
    fut=H[i+1:]; mx=float(fut.max()) if len(fut) else C[i]
    tot+=(pout-pin)/pin*100; totn+=net
    print(f'{t} i={i} in={pin:.2f} out@{T[j]}={pout:.2f} {(pout-pin)/pin*100:+.2f}%  net={net:+.0f}  oracle_peak={(mx-pin)/pin*100:+.2f}%')
print(f'SUM gross% {tot:+.2f}   net on 1L = {totn/1000:+.2f}%')
