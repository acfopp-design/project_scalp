"""
LEG HARVESTER v3 -- the human-eye strategy as executable logic.
Standalone. Touches NO board code. Built 21-Sep-2026 from 30 labelled trades.

Two things v2 could not do are fixed here:

 1. EXIT had no stable constant (16 configs, 1 positive, sign flipped by day).
    v3 uses NO global constant. A dip is judged against the dips THIS move has
    already produced: exit only when the quiet period is longer than anything
    the leg has survived so far. Self-calibrating per stock, per day.
    Floor of 6 bars comes from the measurement that fake dips average 4.8 bars.

 2. UNIVERSE was the whole badged list -> 66 trades/day vs his 2-7 per stock.
    v3 watches at most WATCH_MAX stocks, chosen causally in the order they
    qualify, mirroring how he actually works off the board.
"""
import json, os, collections
import numpy as np, pandas as pd

# ---- parameters, each traceable to a measurement (see LOGIC_V2_SPEC.md) ----
VOLX        = 2.5      # entry bar volume vs median so far
BODYX       = 1.0      # entry body vs median 20-bar range
STOP        = 1.2      # % adverse hard stop
DIP_FLOOR   = 6        # bars; fake dips measured at ~4.8 bars average
DIP_MULT    = 1.5      # quiet must exceed 1.5x the longest dip this leg survived
BASE_BARS   = 10       # bars of quiet required before a thrust counts
BASE_MAX    = 1.2      # that base must be <= 1.2x the stock's median bar range
MIN_PRICE   = 30.0
MIN_TOVER   = 200_000.0   # Rs median turnover per 30s bar (ABSOLUTE, not a ratio)
WATCH_MAX   = 5        # stocks watched per day (he traded 5 on the labelled day)
MAX_TR_SYM  = 8        # cap per stock per day (his max was 7)
SLOTS       = 2
SLOT        = 250_000
LAST_ENTRY  = '14:30:00'
SQUARE_OFF  = '15:15:00'
BOTH_SIDES  = True

def charges(vin, vout):
    br = min(20, vin*0.0003) + min(20, vout*0.0003)
    ex = (vin+vout)*0.0000322
    return br + vout*0.00025 + ex + vin*0.00003 + 0.18*(br+ex) + (vin+vout)*0.000001

def psar(h, l, af0=0.02, afm=0.2):
    n=len(h); s=np.zeros(n); bull=True; af=af0; s[0]=l[0]; ep=h[0]
    for i in range(1,n):
        s[i]=s[i-1]+af*(ep-s[i-1])
        if bull:
            if l[i]<s[i]: bull=False; s[i]=ep; ep=l[i]; af=af0
            elif h[i]>ep: ep=h[i]; af=min(af+af0,afm)
        else:
            if h[i]>s[i]: bull=True; s[i]=ep; ep=h[i]; af=af0
            elif l[i]<ep: ep=l[i]; af=min(af+af0,afm)
    return s

def prepare(bars, badge_start):
    """bars: list of dicts with hhmm,o,h,l,c,v sorted by time."""
    c=np.array([x['c'] for x in bars],float); h=np.array([x['h'] for x in bars],float)
    l=np.array([x['l'] for x in bars],float); o=np.array([x['o'] for x in bars],float)
    v=np.array([x['v'] for x in bars],float); hh=[x['hhmm'] for x in bars]
    if len(c)<60 or c.min()<MIN_PRICE: return None
    if np.median(c*v) < MIN_TOVER: return None
    e12=pd.Series(c).ewm(span=12,adjust=False).mean().values
    e26=pd.Series(c).ewm(span=26,adjust=False).mean().values
    macd=e12-e26; sig=pd.Series(macd).ewm(span=9,adjust=False).mean().values
    rng=pd.Series((h-l)/c*100).rolling(20,min_periods=3).median().bfill().fillna(0.2).values
    vmed=pd.Series(v).expanding(min_periods=1).median().values
    first=next((i for i,t in enumerate(hh) if t>=badge_start), len(c))
    return dict(c=c,h=h,l=l,o=o,v=v,hh=hh,macd=macd,sig=sig,rng=rng,
                vmed=vmed,sar=psar(h,l),first=first,n=len(c))

def side_break(a, i):
    """entry bar must take out the base it came from"""
    b0 = max(0, i-BASE_BARS)
    if a['c'][i] > a['o'][i]:
        return a['c'][i] >= a['h'][b0:i].max()
    return a['c'][i] <= a['l'][b0:i].min()

def entry_signals(a):
    out=[]
    for i in range(max(1,a['first']), a['n']):
        if a['hh'][i] > LAST_ENTRY: break
        vm = a['vmed'][i] or 1
        if a['v'][i] < VOLX*vm: continue
        # THE THRUST MUST START A MOVE, NOT SIT INSIDE CHOP.
        # Every labelled entry was the first push out of a quiet stretch --
        # APOLLO based 375.8-378.7 for 6 bars, RATNAVEER was flat till 10:30,
        # EMMVEE had 11 zero-volume bars. KPIGREEN is the exception: bar 2 of
        # the session, where no base can exist yet, so the first 3 bars pass.
        if i > 3:
            b0 = max(0, i-BASE_BARS)
            base = (a['h'][b0:i].max() - a['l'][b0:i].min()) / a['c'][i] * 100
            if base > BASE_MAX * a['rng'][i] * BASE_BARS / 4.0: continue
            if side_break(a, i) is False: continue
        body = (a['c'][i]-a['o'][i])/a['c'][i]*100
        if body>0 and body>=BODYX*a['rng'][i] and a['sar'][i]<a['c'][i] and a['macd'][i]>a['sig'][i]:
            out.append((i,1))
        elif BOTH_SIDES and body<0 and -body>=BODYX*a['rng'][i] and a['sar'][i]>a['c'][i] and a['macd'][i]<a['sig'][i]:
            out.append((i,-1))
    return out

def ride(a, i, side):
    """Adaptive leg exit: quiet must outlast every dip this leg already survived."""
    ext = a['c'][i]; last_new = i
    dips = []                      # durations of pullbacks already survived
    j = i+1
    while j < a['n']:
        if side==1:
            if a['h'][j] > ext:
                if j-last_new > 1: dips.append(j-last_new)
                ext = a['h'][j]; last_new = j
            ch = (a['c'][j]-a['c'][i])/a['c'][i]*100
        else:
            if a['l'][j] < ext:
                if j-last_new > 1: dips.append(j-last_new)
                ext = a['l'][j]; last_new = j
            ch = (a['c'][i]-a['c'][j])/a['c'][i]*100
        quiet = j - last_new
        limit = max(DIP_FLOOR, int(DIP_MULT * max(dips))) if dips else DIP_FLOOR
        if quiet >= limit or ch <= -STOP or a['hh'][j] >= SQUARE_OFF or j == a['n']-1:
            return j
        j += 1
    return a['n']-1

def run_day(universe):
    """universe: {sym: (bars, badge_start)}  -> list of taken trades"""
    prepped = {}
    for sym,(bars,bs) in universe.items():
        a = prepare(bars, bs)
        if a is not None and a['first'] < a['n']: prepped[sym]=a
    # WATCH: first WATCH_MAX stocks to produce a valid entry signal, by time
    firsts=[]
    for sym,a in prepped.items():
        sg = entry_signals(a)
        if sg: firsts.append((a['hh'][sg[0][0]], sym, sg))
    firsts.sort()
    watch = firsts[:WATCH_MAX]
    cand=[]
    for _,sym,sg in watch:
        a=prepped[sym]; used=0; block_until=-1
        for i,side in sg:
            if used>=MAX_TR_SYM or i<=block_until: continue
            j=ride(a,i,side); block_until=j
            ep,xp=a['c'][i],a['c'][j]; q=int(SLOT/ep)
            if q<1: continue
            vin,vout = (q*ep,q*xp) if side==1 else (q*xp,q*ep)
            g=(xp-ep)*q*side
            cand.append(dict(sym=sym,side=side,t_in=a['hh'][i],t_out=a['hh'][j],
                             p_in=round(ep,2),p_out=round(xp,2),qty=q,
                             pct=round((xp-ep)/ep*100*side,2),
                             net=round(g-charges(vin,vout))))
            used+=1
    cand.sort(key=lambda x:x['t_in'])
    free=['']*SLOTS; taken=[]
    for c0 in cand:
        for s in range(SLOTS):
            if c0['t_in']>=free[s]:
                free[s]=c0['t_out']; taken.append(c0); break
    return taken

def load_from_board_logs(day, logdir='logs/movers_board'):
    """Build the day's universe from badge_audit + bars30."""
    ba=json.load(open(f'{logdir}/badge_audit_{day}.json'))
    bs={}
    for w in ba.get('windows',[]):
        if w.get('badge')!='NO-DIP': continue
        s,t=w['sym'],w.get('badge_start')
        if t and (s not in bs or t<bs[s]): bs[s]=t
    S=collections.defaultdict(list)
    for line in open(f'{logdir}/bars30_{day}.jsonl'):
        r=json.loads(line)
        if '09:15:00'<=r['hhmm']<='15:30:00': S[r['sym']].append(r)
    uni={}
    for sym,b in S.items():
        if sym not in bs or len(b)<150: continue
        b.sort(key=lambda x:x['hhmm']); uni[sym]=(b,bs[sym])
    return uni

if __name__=='__main__':
    import sys
    for day in sys.argv[1:]:
        uni=load_from_board_logs(day)
        tk=run_day(uni)
        net=sum(t['net'] for t in tk)
        print(f"{day}: watched<= {WATCH_MAX}  trades {len(tk)}  wins {sum(1 for t in tk if t['net']>0)}"
              f"  net Rs {net:+,}  = {net/1000:+.2f}%")
        for t in tk: print('   ',t['sym'],t['side'],t['t_in'],'->',t['t_out'],
                           t['p_in'],'->',t['p_out'],f"{t['pct']:+.2f}%",f"net{t['net']:+}")
