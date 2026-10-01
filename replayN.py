"""replayN.py -- forward-only replay with N positions instead of one.

Sri: "I am assuming you are taking 1 trade at a time. Can you split into 5
different trades... 1 lakh one trade as I have 5x margin."

Rs 1,00,000 at 5x = Rs 5,00,000 of buying power. N=1 puts all of it in one name;
N=5 runs five parallel positions of Rs 1,00,000 each. Everything else is
identical, so the only variable is concentration.

`available` gates each stock to the moment it entered Sri's funnel -- a stock the
Dhan shocker panels had not yet surfaced cannot be traded, however good it looks
in hindsight.
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


def replay(tape, dirfn, slots=1, available=None, upto=UPTO, tiebreak="strongest"):
    dirs = {s: dirfn(b) for s, b in tape.items()}
    idx = {s: {b["hhmm"]: i for i, b in enumerate(bars)} for s, bars in tape.items()}
    clock = sorted({b["hhmm"] for bars in tape.values() for b in bars
                    if FROM <= b["hhmm"] <= upto})
    per = BOOK / slots
    open_pos, out = [], []
    for t in clock:
        # ---- exits
        still = []
        for p in open_pos:
            bars = tape[p["sym"]]
            i = idx[p["sym"]].get(t)
            if i is None:
                still.append(p); continue
            lo, hi, c = bars[i]["l"], bars[i]["h"], bars[i]["c"]
            why = px = None
            if lo and lo <= p["in"] * (1 + STOP_PCT / 100):
                px, why = p["in"] * (1 + STOP_PCT / 100), "stop"
            else:
                p["peak"] = max(p["peak"], hi or c)
                if (p["peak"] - c) / p["peak"] * 100 >= TRAIL_PCT and c > p["in"]:
                    px, why = c, "trail"
            if why:
                bv, sv = p["qty"] * p["in"], p["qty"] * px
                ch = PE.charges(bv, sv)["total"]
                out.append({**p, "out": px, "out_t": t, "why": why, "net": sv - bv - ch})
            else:
                still.append(p)
        open_pos = still
        if len(open_pos) >= slots:
            continue
        held = {p["sym"] for p in open_pos}
        cands = []
        for s, bars in tape.items():
            if s in held:
                continue
            if available is not None and t < available.get(s, "99:99:99"):
                continue                       # not in the funnel yet
            i = idx[s].get(t)
            if i is None or i < 1 or i + 1 >= len(bars):
                continue
            d = dirs[s]
            if d[i] == 1 and d[i - 1] != 1:
                cands.append((volx(bars, i), s, i))
        if not cands:
            continue
        cands.sort(key=(lambda x: -x[0]) if tiebreak == "strongest" else (lambda x: x[1]))
        for v, s, i in cands:
            if len(open_pos) >= slots:
                break
            bars = tape[s]
            entry = bars[i + 1]["o"] or bars[i + 1]["c"]
            if not entry:
                continue
            qty = int(per / entry)
            if qty <= 0:
                continue
            open_pos.append({"sym": s, "in": entry, "in_t": bars[i + 1]["hhmm"],
                             "qty": qty, "peak": entry, "str": v})
    for p in open_pos:
        px = tape[p["sym"]][-1]["c"]
        bv, sv = p["qty"] * p["in"], p["qty"] * px
        ch = PE.charges(bv, sv)["total"]
        out.append({**p, "out": px, "out_t": upto, "why": "square-off", "net": sv - bv - ch})
    return out, sum(x["net"] for x in out)
