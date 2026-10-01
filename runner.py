"""runner.py -- one book, three ways of trading a direction line.

  MODE "signal"  : enter on the turn, exit on -1% stop or 1.2% off the peak
  MODE "native"  : always in the market, reverse on every turn (how a flip
                   indicator is actually drawn on a chart), square off at the end
Long trades come from turns to +1, shorts from turns to -1 -- exact mirrors.
"""
import paper_engine as PE

CAPITAL, LEVERAGE, SLOTS = 100_000.0, 5.0, 3
PER_SLOT = CAPITAL * LEVERAGE / SLOTS
FROM = "09:16:00"
STOP_PCT, TRAIL_PCT = -1.0, 1.2


def turns(bars, d, side):
    """Bars where the direction line turns to `side`, after FROM."""
    return [i for i in range(1, len(bars))
            if d[i] == side and d[i - 1] != side and bars[i]["hhmm"] >= FROM]


def _exit_signal(bars, i, entry, side):
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
        trough = entry
        for j in range(i, len(bars)):
            lo, hi, c = bars[j]["l"], bars[j]["h"], bars[j]["c"]
            if hi and hi >= entry * (1 - STOP_PCT / 100):
                return entry * (1 - STOP_PCT / 100), bars[j]["hhmm"], "stop"
            trough = min(trough, lo or c)
            if trough and (c - trough) / trough * 100 >= TRAIL_PCT and c < entry:
                return c, bars[j]["hhmm"], "trail"
    return None, None, None


def book(tape, dirs, longs=True, shorts=False, mode="signal", upto="15:15:00"):
    """tape {sym: bars}, dirs {sym: direction line}."""
    ev = []
    for sym, bars in tape.items():
        d = dirs[sym]
        # NATIVE MODE IS "ALWAYS IN THE MARKET". Sri's chart shows a BUY at
        # 09:16 on PWL because the line was already pointing up when the session
        # opened -- there is no flip there, the position simply exists. Waiting
        # for the first turn skipped that whole leg, so the run is seeded with
        # whatever direction the line already holds at FROM.
        if mode == "native":
            k = next((j for j in range(len(bars)) if bars[j]["hhmm"] >= FROM), None)
            if k is not None and ((d[k] > 0 and longs) or (d[k] < 0 and shorts)):
                ev.append((bars[k]["hhmm"], sym, k, d[k]))
        if longs:
            ev += [(bars[i + 1]["hhmm"], sym, i + 1, +1)
                   for i in turns(bars, d, +1) if i + 1 < len(bars)]
        if shorts:
            ev += [(bars[i + 1]["hhmm"], sym, i + 1, -1)
                   for i in turns(bars, d, -1) if i + 1 < len(bars)]
    ev.sort()
    free = ["00:00:00"] * SLOTS
    held = {}                       # native mode: sym -> position (holds a slot)
    slot_of = {}                    # native mode: sym -> which slot it holds
    out = []
    for t, sym, i, side in ev:
        bars = tape[sym]
        if mode == "native" and sym in held:
            p = held[sym]
            if p["side"] == side:
                continue                        # same direction, nothing to do
            px = bars[i]["o"] or bars[i]["c"]
            out.append({**p, "out": px, "out_t": t, "why": "reverse"})
            del held[sym]
            slot = slot_of.pop(sym)             # REVERSE INSIDE THE SAME SLOT --
            qty = int(PER_SLOT / px) if px else 0   # the earlier version tried to
            if qty > 0:                         # find the slot by a timestamp tag
                held[sym] = {"sym": sym, "side": side, "in": px,   # and silently
                             "in_t": t, "qty": qty}                # lost it, so
                slot_of[sym] = slot             # everything after the second flip
            else:                               # was dropped.
                free[slot] = t
            continue
        slot = next((s for s in range(SLOTS) if free[s] <= t), None)
        if slot is None:
            continue
        entry = bars[i]["o"] or bars[i]["c"]
        if not entry:
            continue
        qty = int(PER_SLOT / entry)
        if qty <= 0:
            continue
        if mode == "signal":
            px, ot, why = _exit_signal(bars, i, entry, side)
            if px is None:
                px, ot, why = bars[-1]["c"], upto, "square-off"
            out.append({"sym": sym, "side": side, "in": entry, "in_t": t,
                        "out": px, "out_t": ot, "qty": qty, "why": why})
            free[slot] = ot
        else:
            held[sym] = {"sym": sym, "side": side, "in": entry, "in_t": t,
                         "qty": qty}
            slot_of[sym] = slot
            free[slot] = "99:99:99"
    for sym, p in held.items():
        out.append({**p, "out": tape[sym][-1]["c"], "out_t": upto, "why": "square-off"})
    tot = 0.0
    for x in out:
        bv, sv = x["qty"] * x["in"], x["qty"] * x["out"]
        ch = PE.charges(bv, sv)["total"]
        x["chg"] = ch
        x["net"] = (sv - bv - ch) if x["side"] > 0 else (bv - sv - ch)
        tot += x["net"]
    return out, tot
