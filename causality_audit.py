"""Does analyse() decide entries from PAST bars only?

Truncates the tape bar by bar and records when each entry signal FIRST becomes
visible. A lag of 0 or 1 bar is correct -- the engine signals on bar i's close
and buys at bar i+1's open. Anything larger means the signal needed the future,
and any P&L built on it is unachievable.

    python causality_audit.py 20260928

Run this after ANY change to eye_strategy.analyse(). It is the guard that the
28-Sep look-ahead defect went undetected for weeks without.
"""
import json, sys
sys.path.insert(0, ".")
DAY = sys.argv[1] if len(sys.argv) > 1 else "20260928"
LOOK = 25          # how many bars ahead to search before giving up

import multi_capital_backtest as MB
import eye_strategy as ES

fun, fd0, avail, tape = MB.day_tape(DAY)
print("MIN_LEG = %s   (must be 0; anything else gates entry on the exit bar)" % ES.MIN_LEG)
res = {}
for sym in sorted(fun):
    b, n = fun[sym], fd0[sym]
    ES._cache.clear()
    ent, _eo, _sc = ES.analyse(sym, b)
    sig = [i for i in range(n, min(len(ent), len(b))) if ent[i]]
    if not sig:
        continue
    i = sig[0]
    lag = None
    for cut in range(i + 1, min(i + LOOK, len(b)) + 1):
        ES._cache.clear()
        e2, _, _ = ES.analyse(sym, b[:cut])
        if i < len(e2) and e2[i]:
            lag = cut - 1 - i
            break
    res[sym] = lag

good = sorted(s for s, l in res.items() if l is not None and l <= 1)
bad = sorted(s for s, l in res.items() if l is None or l > 1)
print("names with a signal : %d" % len(res))
print("CAUSAL (lag <= 1)   : %d  (%.0f%%)" % (len(good), len(good) * 100.0 / max(1, len(res))))
print("LOOKS AHEAD         : %d  (%.0f%%)" % (len(bad), len(bad) * 100.0 / max(1, len(res))))
if bad:
    print("  worst offenders:", {s: res[s] for s in bad[:10]})
json.dump({"day": DAY, "good": good, "bad": bad, "lags": res},
          open("logs/CAUSALITY_%s.json" % DAY, "w"), indent=1)
print("wrote logs/CAUSALITY_%s.json" % DAY)
raise SystemExit(0 if not bad else 1)
