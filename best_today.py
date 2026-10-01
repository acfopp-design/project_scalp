"""best_today.py -- build the best strategy for today's no-dip watchlist.

Evidence it is built on (all measured today, 09-Sep):
  * The board's no-dip badge picks the stocks. Sri's eye beat every mechanical
    selector I tried, and the badge is what his eye uses.
  * The money is in ONE leg per stock, early. GRAPHITE +10.21%, ASTEC +12.09%,
    NOVARTIND +15.69%, AEGISLOG +5.99% -- all before ~10:05.
  * The loss is ALL in the exit. On GRAPHITE, holding the leg was Rs 9,837;
    trading it with oscillator exits was Rs 1,767.
  * Fake dips inside a leg last 1-5 bars and recover; the real top does not
    make a new high again. DURATION separates them, depth and volume do not.
"""
import sys, json
sys.path.insert(0, '.')
import live_shadow as LS, paper_engine as PE

DAY = '20260909'
CAPITAL, LEV = 100000.0, 5.0

def ema(v, n):
    o, k, a = [], 2.0/(n+1), None
    for x in v:
        a = x if a is None else x*k + a*(1-k); o.append(a)
    return o

def watchlist(day):
    s = set()
    for line in open('logs/movers_board/board_%s.jsonl' % day, encoding='utf-8', errors='replace'):
        if '"pinned": true' not in line and '"pinned":true' not in line:
            continue
        try: d = json.loads(line)
        except Exception: continue
        for rows in (d.get('panels') or {}).values():
            for r in rows or []:
                if r.get('pinned') and r.get('sym'): s.add(r['sym'])
    return s

def prep(b):
    c = [x['c'] for x in b]; v = [x['v'] or 0 for x in b]
    mac = [a-x for a, x in zip(ema(c, 12), ema(c, 26))]; sig = ema(mac, 9)
    cum = cv = 0.0; vw = []
    for i, x in enumerate(b):
        tp = (x['h']+x['l']+x['c'])/3.0; cum += tp*v[i]; cv += v[i]
        vw.append(cum/cv if cv else c[i])
    return mac, sig, vw

def trades(b, STALE, GIVEBACK, STOP=-1.0, upto='15:15:00'):
    """Enter on strength; hold through any dip that keeps making new highs.
    Exit only when the move has STOPPED making highs for STALE bars, or has
    handed back GIVEBACK% of its peak, or the stop is hit."""
    c = [x['c'] for x in b]; mac, sig, vw = prep(b)
    out = []; pos = None
    for i in range(20, len(b)-1):
        t = b[i]['hhmm']
        if pos:
            if b[i]['h'] > pos['peak']:
                pos['peak'] = b[i]['h']; pos['peak_i'] = i
            if b[i]['l'] <= pos['in']*(1+STOP/100):
                out.append((pos, round(pos['in']*(1+STOP/100), 2), b[i]['hhmm'], 'stop')); pos = None; continue
            stale = i - pos['peak_i'] >= STALE
            give = (pos['peak']-c[i])/pos['peak']*100 >= GIVEBACK
            if (stale and give) or t >= upto:
                out.append((pos, c[i], b[i]['hhmm'], 'trend over')); pos = None
            continue
        if t < '09:16:00' or t > '14:30:00':
            continue
        hi20 = max(x['h'] for x in b[max(0, i-20):i])
        if c[i] > vw[i] and c[i] >= hi20 and mac[i] > sig[i] and c[i] > (b[i]['o'] or c[i]):
            e = b[i+1]['o'] or c[i]
            if e: pos = {'in': e, 'in_t': b[i+1]['hhmm'], 'i': i+1,
                         'peak': b[i+1]['h'] or e, 'peak_i': i+1}
    return out
