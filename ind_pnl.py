"""ind_pnl.py -- actual % profit per signal, with real exits and real charges."""
import sys, os
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
import ind_test as IT
import paper_engine as PE

PER = 100000.0     # one slot
def run(x, fire):
    b=x['b']; out=[]; pos=None
    for i in range(30,len(b)-1):
        t=b[i]['hhmm']
        if pos:
            lo=b[i]['l']
            if lo<=pos['in']*0.99:
                out.append((pos['in'],round(pos['in']*0.99,2))); pos=None; continue
            rsi_roll = x['rsi'][i]<x['rsm'][i] and x['rsi'][i-1]<x['rsm'][i-1] and x['rsi'][i]<x['rsi'][i-1]
            if i-pos['i']>=3 and (rsi_roll or t>='15:15:00'):
                out.append((pos['in'],b[i]['c'])); pos=None
            continue
        if t<'09:16:00' or t>'14:30:00': continue
        try:
            if fire(x,i): pos={'in':b[i+1]['o'] or b[i]['c'],'i':i}
        except Exception: pass
    if pos: out.append((pos['in'],b[-1]['c']))
    return out

DAYS=IT.DAYS
res={k:[0,0,0.0,0.0] for k in IT.SIGNALS}   # trades, wins, sum%, net Rs
nsym=0
for day,folder in DAYS:
    data=IT.load(day,folder)
    for symb,b in data.items():
        if len(b)<150: continue
        tov=sorted(y['c']*y['v']/1e5 for y in b)
        if b[0]['c']<20 or tov[len(tov)//2]<5.0: continue
        nsym+=1
        x=IT.prep(b)
        for name,fn in IT.SIGNALS.items():
            for e,o in run(x,fn):
                if not e: continue
                pct=(o/e-1)*100
                q=int(PER/e); bv,sv=q*e,q*o
                net=sv-bv-PE.charges(bv,sv)['total']
                r=res[name]; r[0]+=1; r[1]+= 1 if pct>0 else 0; r[2]+=pct; r[3]+=net
print(f"{nsym} stock-days | one slot of Rs {PER:,.0f} | exits: RSI-vs-smoothing, -1% stop, 15:15\n")
print(f"{'SIGNAL':<42}{'TRADES':>8}{'WIN%':>7}{'AVG%':>8}{'TOTAL%':>9}{'NET Rs':>12}")
rows=sorted(res.items(), key=lambda kv:-kv[1][3])
for name,(n,w,s,net) in rows:
    if n<20: continue
    print(f"{name:<42}{n:>8,}{w*100.0/n:>6.0f}%{s/n:>+8.2f}{s:>+9.0f}{net:>12,.0f}")
