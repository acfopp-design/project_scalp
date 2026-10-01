"""bump.py -- the two questions that actually matter.

Sri, 07-Sep: "Because you know the history, you are easily identifying the top
bullish stocks. In real-time, what's your strategy? ... And if you're already
all-in and another stock starts bumping, how would you trade it?"

Nothing here may look forward. A stock qualifies at candle i using only candles
up to i, and the previous session. Three detectors, all causal:

  VOL   volume of the last 6 candles vs the 20 before them (what survived the
        four-day no-hindsight test)
  RVOL  volume so far today vs the SAME CLOCK MINUTES of the previous session --
        "is this stock busier than it normally is by now"
  BOTH  must pass both

and on top, price must be making a new high of the day and be above VWAP, so we
are buying a bump, not a bounce.
"""
import paper_engine as PE

CAPITAL, LEVERAGE = 100_000.0, 5.0
BOOK = CAPITAL * LEVERAGE
FROM = "09:16:00"
STOP_PCT, TRAIL_PCT = -1.0, 1.2
MIN_TICKET = 25_000.0


def vwap(bars):
    out, pv, vv = [], 0.0, 0.0
    for x in bars:
        tp = (x["h"] + x["l"] + x["c"]) / 3.0
        v = x["v"] or 0
        pv += tp * v; vv += v
        out.append(pv / vv if vv else x["c"])
    return out


def signals(bars, warm, kind="vol", volx=3.0, rvolx=2.0, newhigh=10):
    """Return [(i, strength)] -- every candle where a bump is detected."""
    vw = vwap(bars)
    out = []
    cum = 0.0
    wcum = []                       # warm-up cumulative volume by candle index
    if warm:
        s = 0.0
        for x in warm:
            s += x["v"] or 0
            wcum.append(s)
    for i in range(len(bars)):
        cum += bars[i]["v"] or 0
        if bars[i]["hhmm"] < FROM or i < 26:
            continue
        a = bars[max(0, i - 5):i + 1]
        b = bars[max(0, i - 25):max(0, i - 5)]
        if not a or not b:
            continue
        ca = sum((x["v"] or 0) for x in a) / len(a)
        cb = sum((x["v"] or 0) for x in b) / len(b)
        v_ratio = (ca / cb) if cb else 0.0
        r_ratio = 0.0
        if wcum and i < len(wcum):
            r_ratio = cum / wcum[i] if wcum[i] else 0.0
        ok = (v_ratio >= volx) if kind == "vol" else \
             (r_ratio >= rvolx) if kind == "rvol" else \
             (v_ratio >= volx and r_ratio >= rvolx)
        if not ok:
            continue
        look = bars[max(0, i - 5 - newhigh):max(0, i - 5)]
        if look and max(x["h"] for x in a) <= max(x["h"] for x in look):
            continue                                   # not a new high
        if bars[i]["c"] <= a[0]["o"]:
            continue                                   # the block is not up
        if bars[i]["c"] < vw[i]:
            continue                                   # buyers not in control
        out.append((i, v_ratio * max(r_ratio, 1.0)))
    return out


def _exit(bars, i, entry):
    peak = entry
    for j in range(i, len(bars)):
        lo, hi, c = bars[j]["l"], bars[j]["h"], bars[j]["c"]
        if lo and lo <= entry * (1 + STOP_PCT / 100):
            return entry * (1 + STOP_PCT / 100), bars[j]["hhmm"], "stop", j
        peak = max(peak, hi or c)
        if peak and (peak - c) / peak * 100 >= TRAIL_PCT and c > entry:
            return c, bars[j]["hhmm"], "trail", j
    return bars[-1]["c"], bars[-1]["hhmm"], "square-off", len(bars) - 1


def trade(tape, warms, kind="vol", reserve=0.0, rotate_x=0.0, rotate_max=0.5,
          upto="15:15:00", **kw):
    """ONE main position with (1 - reserve) of the book, plus an optional
    reserve pot for a second, stronger bump found mid-trade.

    rotate_x > 0 also allows the main position to be REPLACED when a new bump
    is that many times stronger and the current one is under rotate_max %."""
    ev = []
    for sym, bars in tape.items():
        for i, st in signals(bars, warms.get(sym), kind=kind, **kw):
            if i + 1 < len(bars):
                ev.append((bars[i + 1]["hhmm"], sym, i + 1, st))
    ev.sort()
    main_free, res_free = "00:00:00", "00:00:00"
    main = None
    out = []

    def book_it(sym, i, val, tag):
        bars = tape[sym]
        entry = bars[i]["o"] or bars[i]["c"]
        if not entry:
            return None
        qty = int(val / entry)
        if qty <= 0:
            return None
        px, ot, why, j = _exit(bars, i, entry)
        bv, sv = qty * entry, qty * px
        ch = PE.charges(bv, sv)["total"]
        return {"sym": sym, "in": entry, "in_t": bars[i]["hhmm"], "out": px,
                "out_t": ot, "qty": qty, "why": why, "net": sv - bv - ch,
                "pot": tag}

    for t, sym, i, st in ev:
        if main and t >= main_free:
            main = None
        # ROTATE: the position is still open, but a much stronger bump has just
        # appeared somewhere else. Cut the current one at the market and move.
        if (main and rotate_x and t < main_free and sym != main["sym"]
                and st >= main["str"] * rotate_x):
            hb = tape[main["sym"]]
            k = next((j for j in range(len(hb)) if hb[j]["hhmm"] >= t), None)
            if k is not None:
                px = hb[k]["o"] or hb[k]["c"]
                gain = (px / main["entry"] - 1) * 100.0
                if gain <= rotate_max:
                    for r in reversed(out):
                        if r["sym"] == main["sym"] and r["in_t"] == main["in_t"]:
                            bv = r["qty"] * r["in"]; sv = r["qty"] * px
                            r.update(out=px, out_t=t, why="rotated out",
                                     net=sv - bv - PE.charges(bv, sv)["total"])
                            break
                    main, main_free = None, t
        if main is None and t >= main_free:
            r = book_it(sym, i, BOOK * (1 - reserve), "main")
            if r:
                out.append(r); main_free = r["out_t"]
                main = {"sym": sym, "str": st, "in_t": r["in_t"],
                        "entry": r["in"]}
            continue
        if reserve > 0 and t >= res_free:
            r = book_it(sym, i, BOOK * reserve, "reserve")
            if r:
                out.append(r); res_free = r["out_t"]
            continue
    return out, sum(x["net"] for x in out)
