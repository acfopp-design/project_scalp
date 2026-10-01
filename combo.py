"""combo.py -- the bump detector picks the stock, an indicator times the exit.

Sri, 07-Sep: "Why is SSL Channel failing? It looks like a good indicator."

It is. It failed as a WHOLE SYSTEM because across 276 stocks it says "up" on
hundreds at once -- with one position you end up holding whichever happened to
fire first, which is not a decision. The bump detector has the opposite problem
and the opposite strength: it fires rarely (57 stocks on 07-Sep) and says WHICH
stock, but it has no opinion about when to leave.

So: bump chooses, indicator holds. Exit when the indicator turns down, with the
-1% stop still underneath as a floor.
"""
import paper_engine as PE
import bump as U

CAPITAL, LEVERAGE = 100_000.0, 5.0
BOOK = CAPITAL * LEVERAGE
STOP_PCT = -1.0


def trade(tape, warms, dirfn, volx=3.0, rvolx=1.5, upto="15:15:00",
          need_up=True, trail=None):
    """dirfn(bars) -> direction line. Entry: a bump alert (optionally only when
    the indicator already points up). Exit: the indicator turns down, or -1%."""
    dirs = {s: dirfn(b) for s, b in tape.items()}
    ev = []
    for s, bars in tape.items():
        d = dirs[s]
        for i, st in U.signals(bars, warms.get(s), kind="both",
                               volx=volx, rvolx=rvolx):
            if i + 1 >= len(bars):
                continue
            if need_up and d[i] != 1:
                continue
            ev.append((bars[i + 1]["hhmm"], s, i + 1, st))
    ev.sort()
    free = "00:00:00"
    out = []
    for t, sym, i, st in ev:
        if t < free:
            continue
        bars, d = tape[sym], dirs[sym]
        entry = bars[i]["o"] or bars[i]["c"]
        if not entry:
            continue
        qty = int(BOOK / entry)
        if qty <= 0:
            continue
        px = ot = why = None
        peak = entry
        for j in range(i, len(bars)):
            lo, hi, c = bars[j]["l"], bars[j]["h"], bars[j]["c"]
            if lo and lo <= entry * (1 + STOP_PCT / 100):
                px, ot, why = entry * (1 + STOP_PCT / 100), bars[j]["hhmm"], "stop"
                break
            peak = max(peak, hi or c)
            if trail and peak and (peak - c) / peak * 100 >= trail and c > entry:
                px, ot, why = c, bars[j]["hhmm"], "trail"
                break
            if j > i and d[j] == -1:
                px, ot, why = c, bars[j]["hhmm"], "indicator turned"
                break
        if px is None:
            px, ot, why = bars[-1]["c"], upto, "square-off"
        bv, sv = qty * entry, qty * px
        ch = PE.charges(bv, sv)["total"]
        out.append({"sym": sym, "in": entry, "in_t": bars[i]["hhmm"], "out": px,
                    "out_t": ot, "qty": qty, "why": why,
                    "net": sv - bv - ch, "str": st})
        free = ot
    return out, sum(x["net"] for x in out)
