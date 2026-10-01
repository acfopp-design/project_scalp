"""range_pos_test.py -- does entering lower in the recent range earn more?

READ-ONLY. Tests Sri's own diagnosis of his losing trades, 30-Sep:

    "Most common, I take trades when the ignition is high ... I buy at peak
     price, sellers take that opportunity and sell, price suddenly dips and
     my SL hits with loss."
    "Always prefer to take entries near crossing MA/EMA, MACD & RSI."

Measured on 30-Sep: the engine's MEDIAN entry sat at 83% of the stock's own
last-20-bar range, and 21 of 60 entries were in the top 15%. So the engine is
doing exactly what he says loses him money.

This is the cheap version of the order-flow question. Delta needs tick data we
do not have and cannot backtest. But "don't buy the top of the range" is the
same thesis and IS testable on the tape we already hold -- if capping entry
height helps, order flow is worth building; if it does nothing, the thesis is
wrong and weeks are saved.

MAX_POS = 100 is the engine as it stands. Lower values refuse an entry whose
price sits above that percentile of the last RANGE_LOOKBACK bars.
Runs with the CAUSAL eviction rule, so the look-ahead is not doing the work.
"""
import sys
sys.path.insert(0, ".")
import live_shadow as LS, paper_live as PL, funnel as FN, eye_strategy as ES

RANGE_LOOKBACK = 20


def tape_for(day, upto="15:30:00"):
    warm = LS.load_warm(LS._prev_session_dir(day))
    pairs, _ = LS.build(day, warm)
    tape, d0 = {}, {}
    for s, (bars, n) in pairs.items():
        bb = [x for x in bars[:n] if x.get("c")]
        tb = [x for x in bars[n:] if x.get("c") and PL.OPEN_T <= x["hhmm"] <= upto]
        if len(tb) < 3:
            continue
        tape[s] = bb + tb
        d0[s] = len(bb)
    avail, _, _ = FN.build(day, tape)
    pins = set(PL.nodip_watchlist(day))
    avail = {s: t for s, t in avail.items() if s in pins}
    fun = {s: tape[s] for s in avail if s in tape}
    return fun, {s: d0[s] for s in fun}, avail


def pos_in_range(bars, i, px):
    w = bars[max(0, i - RANGE_LOOKBACK):i + 1]
    hi = max((x.get("h") or x.get("c") or 0) for x in w)
    lo = min((x.get("l") or x.get("c") or 0) for x in w)
    if hi <= lo:
        return None
    return (px - lo) / (hi - lo) * 100


def run(day, start, cap):
    fun, fd0, avail = tape_for(day)
    PL.MAX_ENTRY_POS = cap
    ES._cache.clear()
    closed, live = PL.run_book(fun, fd0, avail, start, "15:30:00")
    net = sum(c.get("net") or 0 for c in closed)
    wins = sum(1 for c in closed if (c.get("net") or 0) > 0)
    return len(closed), wins, net


if __name__ == "__main__":
    day = sys.argv[1] if len(sys.argv) > 1 else "20260930"
    start = sys.argv[2] if len(sys.argv) > 2 else "10:03:17"
    PL.DISPLACE_MODE = "causal"
    print("  %s  entries capped by position in the last-%d-bar range" % (day, RANGE_LOOKBACK))
    print("  (100 = no cap, today's behaviour.  60 = refuse anything in the top 40%%)")
    print()
    print("  %-10s %7s %6s %7s %12s" % ("MAX POS", "TRADES", "WINS", "WIN%", "NET"))
    print("  " + "-" * 50)
    for cap in (100, 90, 80, 70, 60, 50):
        t, w, n = run(day, start, cap)
        print("  %-10s %7d %6d %6.0f%% %12s"
              % (cap, t, w, 100 * w / max(1, t), format(n, "+,.0f")))
