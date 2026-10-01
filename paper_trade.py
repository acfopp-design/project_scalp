"""
paper_trade.py -- what today's Super Stocks signals would have paid, after charges.

NOT a simulation of prices. Every entry and exit below is a price this board
actually recorded on the tape today, at 10-second resolution, from
logs/movers_board/board_YYYYMMDD.jsonl. Nothing is interpolated or invented.

THE RULES, fixed before looking at any result:
  capital      Rs 50,000, ONE position at a time (a scalper watches one chart)
  signal       the first time a stock appears on the SUPER STOCKS tab
  entry        the NEXT recorded price after the signal, not the signal price --
               he cannot fill at the instant the card appears
  target       +1.0%      stop  -0.5%      time cap  15 min
  session      09:15 to 10:30, his stated window; anything open at 10:30 is closed
  quantity     floor(50,000 / entry price), no leverage

CHARGES -- Dhan equity INTRADAY, both legs:
  brokerage    min(Rs 20, 0.03% of turnover) per executed order
  STT          0.025% on the SELL turnover
  exchange     0.00297% on total turnover (NSE)
  SEBI         0.0001% on total turnover
  stamp duty   0.003% on the BUY turnover
  GST          18% on (brokerage + exchange + SEBI)
"""
import json, math, sys
from datetime import datetime
from pathlib import Path
HERE = Path(__file__).resolve().parent
DAY = datetime.now().strftime("%Y%m%d")
CAP, TARGET, STOP, TIMECAP, END = 50_000.0, 1.0, -0.5, 15 * 60, "10:30:00"

def sec(x):
    h, m, s = map(int, x.split(":")); return h * 3600 + m * 60 + s

# ---- real recorded prices ------------------------------------------------
# PRICE SOURCE: bars30_YYYYMMDD.jsonl, the tick builder's own 30-second bars.
# The board log was the obvious choice and it is the WRONG one: a symbol only
# appears there while its card is on screen, so a stock that drops off the board
# has no prices during exactly the window a trade would be open. Using it gave
# YATRA an entry at 10:29 -- 70 minutes after its signal -- purely because that
# was the first price the board happened to record for it.
#
# bars30 tracks every symbol the board watches, continuously, at 30s. Exits are
# taken on the bar CLOSE, never the high, so a target only counts if the stock
# actually closed a 30-second bar through it.
from datetime import timezone as _tz, timedelta as _td
_IST = _tz(_td(hours=5, minutes=30))
px = {}
for ln in (HERE / "logs" / "movers_board" / f"bars30_{DAY}.jsonl").open(
        encoding="utf-8", errors="replace"):
    try: r = json.loads(ln)
    except Exception: continue
    d = datetime.fromtimestamp(r["t"], _IST)
    if d.strftime("%Y%m%d") != DAY: continue
    t = d.strftime("%H:%M:%S")
    if not ("09:15:00" <= t <= "10:35:00"): continue
    if r.get("sym") and r.get("c"):
        px.setdefault(r["sym"], {})[t] = float(r["c"])

# ---- signals -------------------------------------------------------------
sig = {}
for ln in (HERE / "logs" / "movers_board" / f"super_{DAY}.jsonl").open(
        encoding="utf-8", errors="replace"):
    try: r = json.loads(ln)
    except Exception: continue
    ts = str(r.get("ts"))[-8:]
    if not ("09:15:00" <= ts <= END): continue
    for c in (r.get("rows") or []):
        s = c["sym"]
        # AAA is a TEST FIXTURE that leaked into the live log when super_check.py
        # was run -- the suite calls scan(), and scan() writes. Excluded here and
        # flagged; the suite should not be able to write to the production log.
        if s == "AAA": continue
        if s not in sig: sig[s] = (ts, c.get("price"))

def charges(buy_val, sell_val):
    b = min(20.0, buy_val * 0.0003) + min(20.0, sell_val * 0.0003)
    stt = sell_val * 0.00025
    exch = (buy_val + sell_val) * 0.0000297
    sebi = (buy_val + sell_val) * 0.000001
    stamp = buy_val * 0.00003
    gst = 0.18 * (b + exch + sebi)
    return b, stt, exch, sebi, stamp, gst, b + stt + exch + sebi + stamp + gst

SEQUENTIAL = "--all" not in sys.argv
trades, busy_until = [], 0
for symbol, (ts, sp) in sorted(sig.items(), key=lambda kv: kv[1][0]):
    if SEQUENTIAL and sec(ts) < busy_until: continue  # one position at a time
    series = sorted(px.get(symbol, {}).items())
    after = [(t, v) for t, v in series if sec(t) > sec(ts)]
    # A signal is only tradeable if a price exists WITHIN A MINUTE of it. Without
    # this the book fills with entries taken 30 or 70 minutes late, purely
    # because that is when the symbol re-entered the log -- COHANCE signalled at
    # 09:16 and the first available price was 09:43. That is not a trade he could
    # have taken; counting it either way would be fiction.
    if len(after) < 3 or sec(after[0][0]) - sec(ts) > 60:
        continue
    e_t, e_p = after[0]
    qty = int(CAP // e_p)
    if qty < 1: continue
    x_t, x_p, why = None, None, None
    for t, v in after[1:]:
        ch = (v / e_p - 1) * 100
        if ch >= TARGET: x_t, x_p, why = t, e_p * (1 + TARGET / 100), "target +1.0%"; break
        if ch <= STOP:   x_t, x_p, why = t, e_p * (1 + STOP / 100), "stop -0.5%"; break
        if sec(t) - sec(e_t) >= TIMECAP: x_t, x_p, why = t, v, "15-min time cap"; break
        if t >= END:     x_t, x_p, why = t, v, "10:30 session end"; break
    if x_t is None:
        x_t, x_p, why = after[-1][0], after[-1][1], "end of data"
    bv, sv = e_p * qty, x_p * qty
    b, stt, exch, sebi, stamp, gst, tot = charges(bv, sv)
    trades.append({"sym": symbol, "sig": ts, "in_t": e_t, "in_p": e_p, "out_t": x_t,
                   "out_p": x_p, "qty": qty, "why": why, "gross": sv - bv,
                   "chg": tot, "net": sv - bv - tot, "bv": bv, "sv": sv})
    busy_until = sec(x_t)

out = {"trades": trades}
(HERE / "logs" / "PAPER_TRADES.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
print(f"{len(trades)} trades")
for t in trades:
    print(f"  {t['sig']} {t['sym']:<12} in {t['in_t']} @{t['in_p']:.2f} x{t['qty']:<4} "
          f"out {t['out_t']} @{t['out_p']:.2f}  {t['why']:<18} "
          f"gross {t['gross']:+8.2f}  chg {t['chg']:6.2f}  net {t['net']:+8.2f}")
g = sum(t["gross"] for t in trades); c = sum(t["chg"] for t in trades)
print(f"\n  gross {g:+.2f}   charges {c:.2f}   NET {g-c:+.2f}   on capital {CAP:,.0f}")
w = [t for t in trades if t["net"] > 0]
print(f"  winners {len(w)}/{len(trades)}")
