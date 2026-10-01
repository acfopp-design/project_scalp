"""live_eye.py -- the human-eye logic (tune_v6 + eye_cfg) on TODAY's live tape.

SHADOW ONLY. No orders, no order path, no contact with Movers_app.py or
live_paper.py. It reads what the Board already writes and logs what the logic
would have done. Nothing here can place a trade.

DATA
  today    logs/tape_live/<today>/<SYM>.json -- the COMPLETE 30-second series,
           pulled from Dhan by live_shadow.fetch_live. Verified bar-for-bar
           identical to the TradingView chart on 21-Sep (price AND volume).
           NOT bars30_*.jsonl: that is the Board's membership log and carries
           only 4-16% of the session's bars per name, starting after the move.
  warm-up  logs/tape_live/<prev>/<SYM>.json        -- last 60 bars of the
           previous session, so EMA/SAR/MACD/RSI are alive at 09:15:00 instead
           of 09:40. This was worth +5.7 points on AGI in backtest.
  universe logs/movers_board/board_<today>.jsonl   -- a name is tradeable only
           from the moment the Board carded it. No look-ahead.
  depth    the same board rows carry {tot_buy, tot_sell, imb_pct}. RECORDED AT
           EACH ENTRY FOR LATER ANALYSIS, NOT USED IN ANY DECISION TODAY.

CAPITAL  Rs 1,00,000 at 5x = 2 slots of Rs 2,50,000 shared across all names.
         Contested slots go to the earliest signal (the only tie-break that beat
         first-come in two separate studies).

OUTPUT   logs/EYE_SHADOW_<today>.json   full book, rewritten each cycle
         logs/EYE_SHADOW_<today>.log    one line per new trade
"""
import json, os, sys, time
from datetime import datetime
from pathlib import Path
HERE=Path(__file__).parent; sys.path.insert(0,str(HERE))
import numpy as np, pandas as pd
import tune_v6 as V6, tune_dyn as TD
from eye_cfg import CFG

SLOT=250000; SLOTS=2; REFRESH=20
OPEN_T,CLOSE_T='0915','1530'
WARM=60
SCRATCH=HERE/'logs'/'_eye_scratch'; SCRATCH.mkdir(parents=True,exist_ok=True)

def today(): return datetime.now().strftime('%Y%m%d')
def prev_tape_day(d):
    base=HERE/'logs'/'tape_live'
    days=sorted(p.name for p in base.iterdir() if p.is_dir() and p.name<d) if base.exists() else []
    return days[-1] if days else None

from datetime import timezone, timedelta
IST=timezone(timedelta(hours=5,minutes=30))

def _series(day,sym):
    """(hhmm4,o,h,l,c,v) rows from the complete tape. Timestamps are epoch -> IST."""
    f=HERE/'logs'/'tape_live'/day/f'{sym}.json'
    if not f.exists(): return []
    try: d=json.load(f.open(encoding='utf-8'))
    except Exception: return []
    ts=d.get('t') or []; c=d.get('c') or []
    if not ts or len(c)!=len(ts): return []
    o=d.get('o') or c; h=d.get('h') or c; l=d.get('l') or c; v=d.get('v') or [0]*len(c)
    rows=[]
    for i,tv in enumerate(ts):
        hh=datetime.fromtimestamp(tv,IST).strftime('%H%M')
        rows.append((hh,o[i],h[i],l[i],c[i],v[i] or 0))
    return rows

def load_bars(day):
    d=HERE/'logs'/'tape_live'/day
    if not d.exists(): return {}
    out={}
    for f in d.glob('*.json'):
        sym=f.stem
        rows=[r for r in _series(day,sym) if OPEN_T<=r[0]<=CLOSE_T]
        if rows: out[sym]=rows
    return out

def load_warm(day,sym):
    return _series(day,sym)[-WARM:]

def preopen_universe(day):
    """names frozen BEFORE the open, so using them from 09:15 is not look-ahead.
    The Board's carding is deliberately NOT used as the gate: it carded EMMVEE at
    09:28, eight minutes after the ignition leg the logic is built to catch."""
    syms=[]
    f=HERE/'logs'/'movers_board'/f'premarket_calls_{day}.json'
    if f.exists():
        try:
            d=json.load(f.open(encoding='utf-8'))
            for r in (d.get('calls') or []):
                s=r.get('sym') if isinstance(r,dict) else r
                if s: syms.append(s)
        except Exception: pass
    b=HERE/'logs'/f'bullish_0909_{day}.json'
    _fb=None
    if b.exists():
        try:
            for r in json.loads(b.read_text(encoding='utf-8')).get('rows',[]):
                if r.get('sym'): syms.append(r['sym'])
        except Exception: pass
    if not syms:
        # the Board writes premarket_calls at ~09:14:45. If it has not yet, fall
        # back to the names that had tape YESTERDAY - known before today's open,
        # so still no look-ahead - rather than sit idle through the open.
        pv=prev_tape_day(day)
        if pv:
            d=HERE/'logs'/'tape_live'/pv
            if d.exists(): syms=[f.stem for f in d.glob('*.json')]
    return list(dict.fromkeys(syms))

def carded(day):
    """first board timestamp at which each symbol appeared, plus its latest depth"""
    f=HERE/'logs'/'movers_board'/f'board_{day}.jsonl'
    first={}; depth={}
    if not f.exists(): return first,depth
    for ln in f.open(encoding='utf-8'):
        try: d=json.loads(ln)
        except Exception: continue
        hh=str(d.get('ts',''))[11:16].replace(':','')
        if not hh: continue
        for p in (d.get('panels') or {}).values():
            if not isinstance(p,list): continue
            for r in p:
                s=r.get('sym')
                if not s: continue
                if s not in first or hh<first[s]: first[s]=hh
                dp=r.get('depth')
                if isinstance(dp,dict): depth.setdefault(s,[]).append((hh,dp))
    return first,depth

def build(sym,warm,bars):
    """write the same CSV shape the backtest uses, then reuse prep() unchanged"""
    rows=warm+bars
    txt=';'.join(f'{t},{o:g},{h:g},{l:g},{c:g},{int(v)}' for t,o,h,l,c,v in rows)
    p=SCRATCH/f'{sym}.csv'; p.write_text(txt)
    return V6.prep(str(p))

def depth_at(dp,hh):
    best=None
    for t,d in dp: 
        if t<=hh: best=d
    return best

def run_once(day,prev):
    bars=load_bars(day); _,depth=carded(day)
    uni=set(preopen_universe(day))
    cand=[]
    for sym,b in bars.items():
        if len(b)<3: continue
        if uni and sym not in uni: continue           # pre-open list only
        warm=load_warm(prev,sym) if prev else []
        try: d=build(sym,warm,b)
        except Exception: continue
        if d['st']>=d['n']-2: continue
        try: trades=V6.sim(d,dict(CFG,SLOTS=99,MAXTR=999))
        except Exception: continue
        for t in trades:
            cand.append(dict(sym=sym,**t))
    cand.sort(key=lambda r:(r['ti'],r['sym']))
    busy=[]; book=[]
    for c in cand:
        busy=[x for x in busy if x>c['ti']]
        if len(busy)>=SLOTS: continue
        c['depth_at_entry']=depth_at(depth.get(c['sym'],[]),c['ti'])
        book.append(c); busy.append(c['to'])
    return book

def fetch(day,syms):
    """pull the complete 30s tape for the pre-open list. Reuses live_shadow's
    fetch so there is exactly one piece of code talking to Dhan."""
    try:
        import live_shadow as LS
        return LS.fetch_live(day,syms,lambda m:print('  '+m,flush=True))
    except Exception as e:
        print('  fetch unavailable:',e,flush=True); return 0

def main():
    day=today(); prev=prev_tape_day(day)
    jf=HERE/'logs'/f'EYE_SHADOW_{day}.json'; lf=HERE/'logs'/f'EYE_SHADOW_{day}.log'
    seen=set(); uni=preopen_universe(day)[:400]
    print(f'live_eye SHADOW | {day} | warm-up {prev} | {len(uni)} pre-open names',flush=True)
    print('NO ORDERS. Nothing here can place a trade. Board and Live Paper tab untouched.',flush=True)
    while True:
        now=datetime.now().strftime('%H%M')
        if now<OPEN_T:
            print(f'{now}  waiting for 09:15 ...',flush=True); time.sleep(20); continue
        if now>CLOSE_T: print('session over',flush=True); break
        if not uni:
            uni=preopen_universe(day)[:400]
            print(f'  pre-open list now {len(uni)} names',flush=True)
        print(f'{now}  fetching tape ...',flush=True); fetch(day,uni)
        try: book=run_once(day,prev)
        except Exception as e:
            print('cycle error',e,flush=True); time.sleep(REFRESH); continue
        net=sum(t['net'] for t in book)
        jf.write_text(json.dumps(dict(day=day,clock=now,slots=SLOTS,
            net=round(net),pct_on_1L=round(net/1000,2),trades=book),indent=1))
        with lf.open('a',encoding='utf-8') as fh:
            for t in book:
                k=(t['sym'],t['ti'])
                if k in seen: continue
                seen.add(k)
                imb=(t.get('depth_at_entry') or {}).get('imb_pct')
                fh.write(f"{t['ti']} {t['sym']:<12} {'LONG ' if t['side']==1 else 'SHORT'} "
                         f"exit {t['to']} {t['pct']:+6.2f}% net {t['net']:+7d} "
                         f"imb {imb if imb is not None else '-'}\n")
        print(f"{now}  {len(book)} trades  net {net:+,.0f} = {net/1000:+.2f}% on 1L",flush=True)
        time.sleep(REFRESH)

if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--once':
        d=sys.argv[2] if len(sys.argv)>2 else today()
        b=run_once(d,prev_tape_day(d))
        print(pd.DataFrame(b).to_string(index=False) if b else 'no trades yet')
    else: main()
