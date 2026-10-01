"""next_bar_test.py -- does Sri's visible geometry predict the NEXT candle?

READ-ONLY. Sri, 30-Sep:
    "exit I made is based on the prediction that blue and orange lines
     crossover or the candles are crossing blue line ... And in case of entry,
     candles are way above blue line and slope continues. That's some
     prediction at least to next candle only (as a human)."

Every feature here is computed from bars up to i ONLY. The target is the return
of bar i+1 (and i+1..i+2). No future leaks into the features.

    above  = (close - EMA8) / EMA8 * 100        candle's distance above the blue line
    slope  = EMA8 3-bar rate of change, %       is the blue line still rising
    gap    = (EMA8 - MA12) / MA12 * 100         blue vs orange separation
    widen  = gap[i] - gap[i-1]                  separating or converging
"""
import json, glob, os, sys
import numpy as np

day = sys.argv[1] if len(sys.argv) > 1 else "20260930"


def ema(x, n):
    k = 2 / (n + 1); out = [x[0]]
    for p in x[1:]:
        out.append(p * k + out[-1] * (1 - k))
    return np.array(out)


def sma(x, n):
    return np.array([np.mean(x[max(0, i - n + 1):i + 1]) for i in range(len(x))])


rows = []
for f in glob.glob("logs/tape_live/%s/*.json" % day):
    try:
        r = json.load(open(f, encoding="utf-8"))
        c = np.array([z for z in (r.get("c") or []) if z], float)
    except Exception:
        continue
    if len(c) < 60:
        continue
    e8, m12 = ema(c, 8), sma(c, 12)
    for i in range(30, len(c) - 21):
        if not (c[i] and e8[i] and m12[i] and e8[i - 3]):
            continue
        above = (c[i] - e8[i]) / e8[i] * 100
        slope = (e8[i] - e8[i - 3]) / e8[i - 3] * 100
        gap = (e8[i] - m12[i]) / m12[i] * 100
        gapp = (e8[i - 1] - m12[i - 1]) / m12[i - 1] * 100
        widen = gap - gapp
        H = (1, 5, 10, 20)
        if i + max(H) >= len(c):
            continue
        rows.append((above, slope, gap, widen)
                    + tuple((c[i + k] / c[i] - 1) * 100 for k in H))

a = np.array(rows)
print("  %s -- %d bar-observations across the day's tape" % (day, len(a)))
print()
print("  baseline   1 bar %+.4f%%   5 bars %+.4f%%   10 bars %+.4f%%   20 bars %+.4f%%"
      % tuple(a[:, 4 + j].mean() for j in range(4)))
print()


def show(name, mask):
    s = a[mask]
    if len(s) < 50:
        print("  %-38s (too few: %d)" % (name, len(s)))
        return
    print("  %-38s n=%-6d 1bar %+.4f%%  5bar %+.4f%%  10bar %+.4f%%  20bar %+.4f%%"
          % ((name, len(s)) + tuple(s[:, 4 + j].mean() for j in range(4))))


print("  HIS ENTRY GEOMETRY -- candle above a rising blue line, gap widening")
show("above>0, slope>0", (a[:, 0] > 0) & (a[:, 1] > 0))
show("above>0, slope>0, widening", (a[:, 0] > 0) & (a[:, 1] > 0) & (a[:, 3] > 0))
show("above>0.3, slope>0.1, widening", (a[:, 0] > 0.3) & (a[:, 1] > 0.1) & (a[:, 3] > 0))
show("above>0.5, slope>0.2, widening", (a[:, 0] > 0.5) & (a[:, 1] > 0.2) & (a[:, 3] > 0))
print()
print("  HIS EXIT GEOMETRY -- candle through the blue line, or lines converging")
show("below the blue line (above<0)", a[:, 0] < 0)
show("above<0 and slope turning down", (a[:, 0] < 0) & (a[:, 1] < 0))
show("converging (widen<0) while above", (a[:, 0] > 0) & (a[:, 3] < 0))
show("blue crossed under orange (gap<0)", a[:, 2] < 0)
print()
print("  THE CONTRAST HE DESCRIBES")
show("ENTRY state", (a[:, 0] > 0.3) & (a[:, 1] > 0.1) & (a[:, 3] > 0))
show("EXIT state", (a[:, 0] < 0) & (a[:, 1] < 0))
