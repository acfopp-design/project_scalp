"""dynsize.py -- size by conviction instead of by a fixed number of slots.

Sri, 07-Sep: "who told to take 3 positions?" -- nobody. I invented three when I
built the human-eye benchmark and it silently became law. Then: "can you make it
dynamic based on the momentum of the stock?"

Yes. The book has ONE real constraint: Rs 5,00,000 of buying power. How it is
divided is free. Here a signal asks for a share of the book in proportion to how
hard the stock is moving, so a strong signal can take the whole book and a weak
one takes a sliver -- instead of every signal getting the same arbitrary third.

    weight = clamp(strength / REF, MIN_W, 1.0)
    size   = min(weight * BOOK, whatever exposure is still free)

STRENGTH is measured at the signal bar, from data available then:
    "vol"  volume of the last 6 candles vs the 20 before them
    "mom"  % the stock has moved over the last 20 candles
    "both" the two multiplied
"""
import paper_engine as PE

CAPITAL, LEVERAGE = 100_000.0, 5.0
BOOK = CAPITAL * LEVERAGE
FROM = "09:16:00"
STOP_PCT, TRAIL_PCT = -1.0, 1.2
MIN_TICKET = 25_000.0


def _volx(bars, i, win=6, base=20):
    a = bars[max(0, i - win + 1):i + 1]
    b = bars[max(0, i - win - base + 1):max(0, i - win + 1)]
    if not a or not b:
        return 0.0
    ca = sum((x["v"] or 0) for x in a) / len(a)
    cb = sum((x["v"] or 0) for x in b) / len(b)
    return ca / cb if cb else 0.0


def _mom(bars, i, n=20):
    j = max(0, i - n)
    p = bars[j]["c"]
    return abs((bars[i]["c"] - p) / p * 100.0) if p else 0.0


def strength(bars, i, kind):
    if kind == "vol":
        return _volx(bars, i)
    if kind == "mom":
        return _mom(bars, i)
    if kind == "both":
        return _volx(bars, i) * _mom(bars, i)
    return 1.0


def _exit(bars, i, entry, side):
    if side > 0:
        peak = entry
        for j in range(i, len(bars)):
            lo, hi, c = bars[j]["l"], bars[j]["h"], bars[j]["c"]
            if lo and lo <= entry * (1 + STOP_PCT / 100):
                return entry * (1 + STOP_PCT / 100), bars[j]["hhmm"], "stop"
            peak = max(peak, hi or c)
            if peak and (peak - c) / peak * 100 >= TRAIL_PCT and c > entry:
                return c, bars[j]["hhmm"], "trail"
    else:
        tr = entry
        for j in range(i, len(bars)):
            lo, hi, c = bars[j]["l"], bars[j]["h"], bars[j]["c"]
            if hi and hi >= entry * (1 - STOP_PCT / 100):
                return entry * (1 - STOP_PCT / 100), bars[j]["hhmm"], "stop"
            tr = min(tr, lo or c)
            if tr and (c - tr) / tr * 100 >= TRAIL_PCT and c < entry:
                return c, bars[j]["hhmm"], "trail"
    return None, None, None


def run(tape, dirs, kind="vol", ref=6.0, min_w=0.2, longs=True, shorts=False,
        upto="15:15:00", fixed=None):
    """fixed=N reproduces the old N-equal-slots behaviour, for comparison."""
    ev = []
    for sym, bars in tape.items():
        d = dirs[sym]
        for i in range(1, len(bars)):
            if bars[i]["hhmm"] < FROM:
                continue
            if longs and d[i] == 1 and d[i - 1] != 1 and i + 1 < len(bars):
                ev.append((bars[i + 1]["hhmm"], sym, i + 1, +1, strength(bars, i, kind)))
            if shorts and d[i] == -1 and d[i - 1] != -1 and i + 1 < len(bars):
                ev.append((bars[i + 1]["hhmm"], sym, i + 1, -1, strength(bars, i, kind)))
    ev.sort()
    out, open_pos = [], []          # open_pos: (release_time, exposure)
    for t, sym, i, side, st in ev:
        open_pos = [p for p in open_pos if p[0] > t]
        used = sum(p[1] for p in open_pos)
        free = BOOK - used
        if free < MIN_TICKET:
            continue
        if fixed:
            want = BOOK / fixed
        else:
            w = max(min_w, min(1.0, st / ref)) if ref else 1.0
            want = BOOK * w
        val = min(want, free)
        if val < MIN_TICKET:
            continue
        bars = tape[sym]
        entry = bars[i]["o"] or bars[i]["c"]
        if not entry:
            continue
        qty = int(val / entry)
        if qty <= 0:
            continue
        px, ot, why = _exit(bars, i, entry, side)
        if px is None:
            px, ot, why = bars[-1]["c"], upto, "square-off"
        bv, sv = qty * entry, qty * px
        ch = PE.charges(bv, sv)["total"]
        net = (sv - bv - ch) if side > 0 else (bv - sv - ch)
        out.append({"sym": sym, "side": side, "in": entry, "in_t": t, "out": px,
                    "out_t": ot, "qty": qty, "why": why, "net": net,
                    "val": bv, "str": st})
        open_pos.append((ot, bv))
    return out, sum(x["net"] for x in out)
