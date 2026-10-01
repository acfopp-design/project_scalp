"""replay0709.py -- forward-only replay, 07-Sep, 09:16 to 10:30.

Sri: "assume you don't have the history... replay as if you don't have history."

So NO top-10 list. Every stock that was trading is a candidate, and the only
thing that decides which one gets the money is what had already happened by that
candle. The three systems under test are indicators, not selectors -- across 276
stocks they say "up" on dozens at once -- so a tie-break is unavoidable and it
must itself be causal. Two are used, and both are reported, because the choice
of tie-break turns out to matter more than the indicator:

    FIRST     whichever fires first gets the slot (the naive default)
    STRONGEST among everything firing on the same candle, the one whose volume
              has expanded most out of its own quiet base -- the bump measure

One position at a time, all Rs 5,00,000 in it. -1% stop, 1.2% trail.
Everything is squared off at 10:30.
"""
import paper_engine as PE

CAPITAL, LEVERAGE = 100_000.0, 5.0
BOOK = CAPITAL * LEVERAGE
FROM, UPTO = "09:16:00", "10:30:00"
STOP_PCT, TRAIL_PCT = -1.0, 1.2


def volx(bars, i, win=6, base=20):
    a = bars[max(0, i - win + 1):i + 1]
    b = bars[max(0, i - win - base + 1):max(0, i - win + 1)]
    if not a or not b:
        return 0.0
    ca = sum((x["v"] or 0) for x in a) / len(a)
    cb = sum((x["v"] or 0) for x in b) / len(b)
    return ca / cb if cb else 0.0


def replay(tape, dirfn, tiebreak="first", min_volx=0.0):
    dirs = {s: dirfn(b) for s, b in tape.items()}
    idx = {s: {b["hhmm"]: i for i, b in enumerate(bars)}
           for s, bars in tape.items() for bars in [tape[s]]}
    clock = sorted({b["hhmm"] for bars in tape.values() for b in bars
                    if FROM <= b["hhmm"] <= UPTO})
    pos, out = None, []
    for t in clock:
        # ---- manage the open position first
        if pos:
            bars = tape[pos["sym"]]
            i = idx[pos["sym"]].get(t)
            if i is not None:
                lo, hi, c = bars[i]["l"], bars[i]["h"], bars[i]["c"]
                why = px = None
                if lo and lo <= pos["in"] * (1 + STOP_PCT / 100):
                    px, why = pos["in"] * (1 + STOP_PCT / 100), "stop"
                else:
                    pos["peak"] = max(pos["peak"], hi or c)
                    if (pos["peak"] - c) / pos["peak"] * 100 >= TRAIL_PCT and c > pos["in"]:
                        px, why = c, "trail"
                if why:
                    bv, sv = pos["qty"] * pos["in"], pos["qty"] * px
                    ch = PE.charges(bv, sv)["total"]
                    out.append({**pos, "out": px, "out_t": t, "why": why,
                                "net": sv - bv - ch})
                    pos = None
        if pos:
            continue
        # ---- who is signalling ON THIS CANDLE
        cands = []
        for s, bars in tape.items():
            i = idx[s].get(t)
            if i is None or i < 1 or i + 1 >= len(bars):
                continue
            d = dirs[s]
            if d[i] == 1 and d[i - 1] != 1:
                v = volx(bars, i)
                if v >= min_volx:
                    cands.append((v, s, i))
        if not cands:
            continue
        if tiebreak == "strongest":
            cands.sort(key=lambda x: -x[0])
        else:
            cands.sort(key=lambda x: x[1])
        v, s, i = cands[0]
        bars = tape[s]
        entry = bars[i + 1]["o"] or bars[i + 1]["c"]
        if not entry:
            continue
        qty = int(BOOK / entry)
        if qty <= 0:
            continue
        pos = {"sym": s, "in": entry, "in_t": bars[i + 1]["hhmm"],
               "qty": qty, "peak": entry, "str": v}
    if pos:
        bars = tape[pos["sym"]]
        px = bars[-1]["c"]
        bv, sv = pos["qty"] * pos["in"], pos["qty"] * px
        ch = PE.charges(bv, sv)["total"]
        out.append({**pos, "out": px, "out_t": UPTO, "why": "square-off",
                    "net": sv - bv - ch})
    return out, sum(x["net"] for x in out)
