"""Shared harness: top-10 bullish (long) and top-10 bearish (short), both days."""
import indi_lab as I, entry_lab as E, live_shadow as L
UPTO = "10:30:00"

def load_day(day):
    if day == "20260907":
        warm = L.load_warm(L._prev_session_dir(day))
        today = L.load_today(day); today.update(L.load_live(day))
        src = {s: (warm.get(s, []) + b, len(warm.get(s, []))) for s, b in today.items()}
    else:
        src = E.load_tape_warm(day)
    out = {}
    for s, (bars, d0) in src.items():
        b = [x for x in bars[d0:] if x["hhmm"] <= UPTO and x["c"]]
        if len(b) < 20:
            continue
        pc = bars[d0 - 1]["c"] if d0 else None
        gap = ((b[0]["o"] / pc - 1) * 100) if (pc and b[0]["o"]) else 0.0
        if abs(gap) > I.GAP_MAX:
            continue
        out[s] = b
    return out

def lists(tape, n=10):
    up, dn = [], []
    for s, b in tape.items():
        o = b[0]["o"] or b[0]["c"]
        if not o:
            continue
        up.append(((max(x["h"] for x in b) / o - 1) * 100, s))
        dn.append(((min(x["l"] for x in b) / o - 1) * 100, s))
    up.sort(reverse=True); dn.sort()
    return [s for _, s in up[:n]], [s for _, s in dn[:n]]

DAYS = {}
def prep():
    if DAYS:
        return DAYS
    for day, label in (("20260907", "07-Sep"), ("20260904", "04-Sep")):
        t = load_day(day)
        bull, bear = lists(t)
        DAYS[label] = (t, bull, bear)
    return DAYS

def score(long_sig, short_sig=None, n=10):
    """Long the bullish list, short the bearish list, separate books."""
    prep()
    res = {}
    for label, (tape, bull, bear) in DAYS.items():
        B = {s: tape[s] for s in bull}
        tl, nl = I.run_sided(B, long_sig, None, UPTO)
        ns, ts = 0, []
        if short_sig:
            S = {s: tape[s] for s in bear}
            ts, ns = I.run_sided(S, None, short_sig, UPTO)
        res[label] = (nl, ns, len(tl), len(ts),
                      len([x for x in tl if x["net"] > 0]),
                      len([x for x in ts if x["net"] > 0]))
    return res
