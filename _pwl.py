"""Continuous long/short on one stock, AlphaTrend, no stop and no trail --
always in the market, reverse on every flip. This is how the indicator is drawn
on Sri's chart: BUY, then SELL, then BUY, with no gaps in between."""
import indi_lab as I, live_shadow as L, paper_engine as PE

SYM, UPTO = "PWL", "13:30:00"
CAP, LEV = 100_000.0, 5.0

warm = L.load_warm(L._prev_session_dir("20260907"))
today = L.load_today("20260907"); today.update(L.load_live("20260907"))
b = [x for x in today[SYM] if "09:15:00" <= x["hhmm"] <= UPTO and x["c"]]
w = warm.get(SYM) or []
bars = w + b
d0 = len(w)
print(f"{SYM}: {len(b)} candles 09:15-{UPTO[:5]}, warm-up {len(w)}")
print(f"   open {b[0]['o']}  high {max(x['h'] for x in b)}  low {min(x['l'] for x in b)}  {UPTO[:5]} {b[-1]['c']}")

at = I._alphatrend_line(bars, 1.0, 14)
# flips, causal, only today, only from 09:16
flips = []
for i in range(d0 + 1, len(bars)):
    if bars[i]["hhmm"] < "09:16:00":
        continue
    up_now = bars[i]["c"] > at[i]
    up_prev = bars[i - 1]["c"] > at[i - 1]
    if up_now != up_prev:
        flips.append((i, 1 if up_now else -1))
print(f"   AlphaTrend flips: {len(flips)}")

trades, pos = [], None
for i, side in flips:
    if i + 1 >= len(bars):
        break
    px, t = bars[i + 1]["o"] or bars[i + 1]["c"], bars[i + 1]["hhmm"]
    if pos and pos["side"] != side:
        trades.append({**pos, "out": px, "out_t": t})
        pos = None
    if pos is None:
        qty = int(CAP * LEV / px)
        pos = {"side": side, "in": px, "in_t": t, "qty": qty}
if pos:
    trades.append({**pos, "out": bars[-1]["c"], "out_t": UPTO})

tot = 0
print(f"\n{'in':10s}{'out':10s}{'side':7s}{'buy':>9}{'sell':>9}{'qty':>7}{'move':>8}{'net':>10}")
for x in trades:
    bv, sv = x["qty"] * x["in"], x["qty"] * x["out"]
    ch = PE.charges(bv, sv)["total"]
    net = (sv - bv - ch) if x["side"] > 0 else (bv - sv - ch)
    mv = ((x["out"] / x["in"] - 1) * 100) * (1 if x["side"] > 0 else -1)
    tot += net
    print(f"{x['in_t']:10s}{x['out_t']:10s}{'LONG' if x['side']>0 else 'SHORT':7s}"
          f"{x['in']:>9.2f}{x['out']:>9.2f}{x['qty']:>7,}{mv:>7.2f}%{net:>10,.0f}")
w_ = [t for t in trades if ((t['qty']*t['out']-t['qty']*t['in']) if t['side']>0 else (t['qty']*t['in']-t['qty']*t['out'])) - PE.charges(t['qty']*t['in'], t['qty']*t['out'])['total'] > 0]
print(f"\n{len(trades)} trades, {len(w_)} wins, NET Rs {tot:,.0f}  ({tot/CAP*100:.2f}% of capital)")
