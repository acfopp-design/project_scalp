"""ind_test.py -- honest hit-rate test of every indicator and combination.

For each signal bar: does price reach +1.0% BEFORE it falls -1.0%, within the
next 60 bars (30 minutes)? Forward-only, no look-ahead beyond that horizon.
Compared against the BASE RATE of every eligible bar, so an indicator only
counts if it beats simply buying at random.
"""
import json, os, sys
from datetime import datetime, timezone, timedelta
IST = timezone(timedelta(hours=5, minutes=30))
HERE = os.path.dirname(os.path.abspath(__file__))
TGT, STP, HOR = 1.0, -1.0, 60

def ema(v,n):
    o,k,a=[],2.0/(n+1),None
    for x in v:
        a=x if a is None else x*k+a*(1-k); o.append(a)
    return o
def sma(v,n):
    return [sum(v[max(0,i-n+1):i+1])/len(v[max(0,i-n+1):i+1]) for i in range(len(v))]
def rsi_(c,n=14):
    g=[0.0]*len(c); l=[0.0]*len(c)
    for i in range(1,len(c)):
        d=c[i]-c[i-1]; g[i]=max(d,0.0); l[i]=max(-d,0.0)
    ag,al=ema(g,n),ema(l,n)
    return [100-100/(1+ag[i]/al[i]) if al[i] else 100.0 for i in range(len(c))]
def sar_(b):
    n=len(b); out=[None]*n
    if n<2: return out
    up=b[1]['c']>=b[0]['c']; af=0.02; ep=b[0]['h'] if up else b[0]['l']; s=b[0]['l'] if up else b[0]['h']
    for i in range(1,n):
        s=s+af*(ep-s); h,l=b[i]['h'],b[i]['l']
        if up:
            if l<s: up=False; s=ep; ep=l; af=0.02
            elif h>ep: ep=h; af=min(af+0.02,0.2)
        else:
            if h>s: up=True; s=ep; ep=h; af=0.02
            elif l<ep: ep=l; af=min(af+0.02,0.2)
        out[i]=s
    return out

def load(day, folder):
    d=os.path.join(HERE,'logs',folder,day); out={}
    if not os.path.isdir(d): return out
    for f in os.listdir(d):
        try: r=json.load(open(os.path.join(d,f),encoding='utf-8'))
        except Exception: continue
        t=r.get('t') or []; c=r.get('c') or []
        if len(t)<120 or len(c)!=len(t): continue
        o=r.get('o') or c; h=r.get('h') or c; l=r.get('l') or c; v=r.get('v') or [0]*len(c)
        bars=[]
        for i,tv in enumerate(t):
            hm=datetime.fromtimestamp(tv,IST).strftime('%H:%M:%S')
            if not ('09:15:00'<=hm<='15:30:00'): continue
            if not c[i]: continue
            bars.append({'o':o[i] or c[i],'h':h[i] or c[i],'l':l[i] or c[i],'c':c[i],'v':v[i] or 0,'hhmm':hm})
        if len(bars)>=120: out[f[:-5]]=bars
    return out

def outcome(b,i):
    entry=b[i]['c']
    for j in range(i+1,min(i+1+HOR,len(b))):
        if (b[j]['l']/entry-1)*100<=STP: return 0
        if (b[j]['h']/entry-1)*100>=TGT: return 1
    return 0

SIGNALS={}
def sig(name):
    def w(f): SIGNALS[name]=f; return f
    return w

@sig('EMA8/MA12 cross up')
def _(x,i): return x['e8'][i]>x['m12'][i] and x['e8'][i-1]<=x['m12'][i-1]
@sig('MACD cross up')
def _(x,i): return x['mac'][i]>x['sig'][i] and x['mac'][i-1]<=x['sig'][i-1]
@sig('MACD above zero')
def _(x,i): return x['mac'][i]>0
@sig('RSI > 50')
def _(x,i): return x['rsi'][i]>50
@sig('RSI cross above its SMA')
def _(x,i): return x['rsi'][i]>x['rsm'][i] and x['rsi'][i-1]<=x['rsm'][i-1]
@sig('Price ABOVE VWAP')
def _(x,i): return x['b'][i]['c']>x['vw'][i]
@sig('SAR below candle')
def _(x,i): return x['sar'][i] is not None and x['sar'][i]<x['b'][i]['l']
@sig('Volume expansion >=3x')
def _(x,i): return x['volx'][i]>=3.0
@sig('Volume expansion >=5x')
def _(x,i): return x['volx'][i]>=5.0
@sig('Price surge >=1% in 5 bars')
def _(x,i): return i>=5 and (x['b'][i]['c']/x['b'][i-5]['c']-1)*100>=1.0
@sig('New 20-bar high')
def _(x,i): return x['b'][i]['c']>=max(y['h'] for y in x['b'][max(0,i-20):i]) if i else False
@sig('Quiet base then burst >=5x')
def _(x,i): return x['volx'][i]>=5.0 and x['basevolx'][i]<=0.8
@sig('COMBO VWAP + vol>=3x')
def _(x,i): return x['b'][i]['c']>x['vw'][i] and x['volx'][i]>=3.0
@sig('COMBO VWAP + vol>=3x + MACD>0')
def _(x,i): return x['b'][i]['c']>x['vw'][i] and x['volx'][i]>=3.0 and x['mac'][i]>0
@sig('COMBO VWAP + vol>=3x + surge1%')
def _(x,i): return (x['b'][i]['c']>x['vw'][i] and x['volx'][i]>=3.0 and i>=5
                    and (x['b'][i]['c']/x['b'][i-5]['c']-1)*100>=1.0)
@sig('COMBO VWAP + vol + surge + SAR below')
def _(x,i): return (x['b'][i]['c']>x['vw'][i] and x['volx'][i]>=3.0 and i>=5
                    and (x['b'][i]['c']/x['b'][i-5]['c']-1)*100>=1.0
                    and x['sar'][i] is not None and x['sar'][i]<x['b'][i]['l'])
@sig('COMBO EMAcross + VWAP + vol>=3x')
def _(x,i): return (x['e8'][i]>x['m12'][i] and x['e8'][i-1]<=x['m12'][i-1]
                    and x['b'][i]['c']>x['vw'][i] and x['volx'][i]>=3.0)
@sig('COMBO EMAcross + VWAP + vol + MACD>0')
def _(x,i): return (x['e8'][i]>x['m12'][i] and x['e8'][i-1]<=x['m12'][i-1]
                    and x['b'][i]['c']>x['vw'][i] and x['volx'][i]>=3.0 and x['mac'][i]>0)
@sig('COMBO newhigh + vol>=3x + VWAP')
def _(x,i): return (x['b'][i]['c']>=max(y['h'] for y in x['b'][max(0,i-20):i] or [x['b'][i]])
                    and x['volx'][i]>=3.0 and x['b'][i]['c']>x['vw'][i])
@sig('SHEET11 stack (EMAx+MACD+RSI50+SAR)')
def _(x,i): return (x['e8'][i]>x['m12'][i] and x['mac'][i]>x['sig'][i] and x['rsi'][i]>50
                    and x['sar'][i] is not None and x['sar'][i]<x['b'][i]['l'])

def prep(b):
    c=[y['c'] for y in b]; v=[y['v'] for y in b]
    e8,m12=ema(c,8),sma(c,12)
    mac=[a-z for a,z in zip(ema(c,12),ema(c,26))]; sg=ema(mac,9)
    rsi=rsi_(c); rsm=sma(rsi,14); sar=sar_(b)
    cum=cv=0.0; vw=[]
    for i,y in enumerate(b):
        tp=(y['h']+y['l']+y['c'])/3.0; cum+=tp*v[i]; cv+=v[i]; vw.append(cum/cv if cv else c[i])
    volx=[]; basevolx=[]
    for i in range(len(b)):
        a=v[max(0,i-20):i]; av=sum(a)/len(a) if a else 0
        volx.append(v[i]/av if av else 0.0)
        pre=v[max(0,i-10):max(0,i-2)]; pv=sum(pre)/len(pre) if pre else 0
        basevolx.append(pv/av if av else 0.0)
    return {'b':b,'e8':e8,'m12':m12,'mac':mac,'sig':sg,'rsi':rsi,'rsm':rsm,
            'sar':sar,'vw':vw,'volx':volx,'basevolx':basevolx}

DAYS=[('20260902','tape'),('20260903','tape'),('20260904','tape'),
      ('20260907','tape_live'),('20260908','tape_live')]
hits={k:[0,0] for k in SIGNALS}; base=[0,0]
nsym=0
for day,folder in DAYS:
    data=load(day,folder)
    for symb,b in data.items():
        if len(b)<150: continue
        # only stocks worth trading: Rs 20+ and real turnover
        tov=sorted(y['c']*y['v']/1e5 for y in b)
        if b[0]['c']<20 or tov[len(tov)//2]<5.0: continue
        nsym+=1
        x=prep(b)
        for i in range(30,len(b)-HOR):
            if not ('09:16:00'<=b[i]['hhmm']<='14:30:00'): continue
            r=outcome(b,i); base[0]+=r; base[1]+=1
            for name,fn in SIGNALS.items():
                try:
                    if fn(x,i): hits[name][0]+=r; hits[name][1]+=1
                except Exception: pass
print(f"stock-days tested: {nsym} | eligible bars: {base[1]:,} | "
      f"BASE RATE (buy any bar): {base[0]*100.0/max(1,base[1]):.1f}%\n")
print(f"{'SIGNAL':<42}{'N':>9}{'HIT%':>8}{'vs BASE':>9}")
rows=[]
for name,(w,n) in hits.items():
    if n<40: rows.append((-999,name,n,0,0)); continue
    p=w*100.0/n; rows.append((p-base[0]*100.0/base[1],name,n,p,0))
for edge,name,n,p,_ in sorted(rows,reverse=True):
    if edge==-999: print(f"{name:<42}{n:>9}{'too few':>8}{'':>9}")
    else: print(f"{name:<42}{n:>9,}{p:>7.1f}%{edge:>+8.1f}")
