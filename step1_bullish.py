"""step1_bullish.py -- Step 1 of Live_Trading_Instructions_v1.0.

Sri, 08-Sep: "With the current logic, which stocks are bullish according to you
for today at that point of time... every 30 seconds starting from 9:16AM onwards
till 9:30... Just stock name, start time, end time."

Reproduces paper_live.py's decision path EXACTLY -- same warm-up, same funnel,
same combined(bars, pullback_alt), same WARM_MIN, same availability gate.
Today's bars are truncated at 09:30:00 so nothing later can leak in.
"""
import sys, json
from pathlib import Path
from datetime import datetime

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import combos2 as C2, funnel as FN, live_shadow as LS
import paper_live as PL

DAY   = "20260908"
MERGE_TAPE_LIVE = ("--merge" in sys.argv)
FROM  = "09:16:00"
UPTO  = "09:30:00"
OPEN_T = PL.OPEN_T
WARM_MIN = PL.WARM_MIN

# WARM-UP: logs/tape/20260907 holds only 159 files (A..GMRAIRPORT) -- the
# 07-Sep tape build stopped partway through the alphabet, so every stock after
# G has no history and cannot clear WARM_MIN before ~09:42. logs/tape_live/
# 20260907 has the same schema and covers A..Z, so it is merged in behind the
# primary tape. READ-ONLY: live_shadow.py is untouched.
from pathlib import Path as _P
warm = LS.load_warm(LS._prev_session_dir(DAY))
_n0 = len(warm)
if MERGE_TAPE_LIVE:
    extra = LS.load_warm(_P("logs/tape_live/20260907"))
    for k, v in extra.items():
        warm.setdefault(k, v)
    print(f"warm-up {_n0} -> {len(warm)} (merged tape_live/20260907)")
else:
    print(f"warm-up {_n0} (tape/20260907 only -- what the live engine used today)")
pairs, _today = LS.build(DAY, warm)

tape, d0 = {}, {}
for s, (bars, n) in pairs.items():
    bb = [x for x in bars[:n] if x.get("c")]
    tb = [x for x in bars[n:] if x.get("c") and OPEN_T <= x["hhmm"] <= UPTO]
    if len(tb) < 3:
        continue
    tape[s] = bb + tb
    d0[s] = len(bb)

avail, _a, _b = FN.build(DAY, tape)
fun  = {s: tape[s] for s in avail if s in tape}
fd0  = {s: d0[s]  for s in fun}

print(f"tape {len(tape)} symbols with bars | funnel {len(fun)} | "
      f"warm(>={WARM_MIN}) {sum(1 for s in fun if fd0[s] >= WARM_MIN)}")

# ---- 1. BULLISH INTERVALS -------------------------------------------------
# combined() is a STATE line: +1 while HOTT/LOTT is in uptrend and Pullback ALT
# has fired (it holds +1 while the trend holds). A contiguous run of +1 is one
# bullish interval. Gated by avail[s] -- a stock is not bullish before the
# board actually showed it -- and by WARM_MIN, same as an entry.
rows = []
for s, bars in fun.items():
    d = C2.combined(bars, C2.pullback_alt)
    first = avail.get(s, "99:99:99")
    run = None
    for i in range(fd0[s], len(bars)):
        t = bars[i]["hhmm"]
        if t < FROM or t > UPTO:
            continue
        ok = (d[i] == 1 and i >= WARM_MIN and t >= first)
        if ok and run is None:
            run = [t, t, bars[i]["c"], bars[i]["c"]]
        elif ok:
            run[1] = t; run[3] = bars[i]["c"]
        elif run is not None:
            rows.append((s, run[0], run[1], run[2], run[3], False)); run = None
    if run is not None:
        rows.append((s, run[0], run[1], run[2], run[3], True))

rows.sort(key=lambda r: (r[1], r[0]))
print(f"\n=== BULLISH per current logic, {FROM}-{UPTO} : {len(rows)} intervals, "
      f"{len({r[0] for r in rows})} distinct stocks ===")
print(f"{'STOCK':<14}{'START':<10}{'END':<10}{'PX_IN':>9}{'PX_END':>9}{'MOVE%':>8}  STATE")
for s, a, b, p0, p1, live in rows:
    mv = (p1 / p0 - 1) * 100 if p0 else 0
    print(f"{s:<14}{a:<10}{b:<10}{p0:>9.2f}{p1:>9.2f}{mv:>+8.2f}  "
          f"{'still bullish at 09:30' if live else 'ended'}")

# ---- 2. WHAT THE ENGINE WOULD ACTUALLY HAVE TAKEN (5 slots) ---------------
closed, open_ = PL.run_book(fun, fd0, avail, FROM, UPTO)
print(f"\n=== ENGINE, 5 slots x Rs {PL.BOOK/PL.SLOTS:,.0f}, {FROM}-{UPTO} ===")
print(f"{'STOCK':<14}{'START':<10}{'END':<10}{'IN':>9}{'OUT':>9}{'MOVE%':>8}{'NET Rs':>11}  WHY")
tot = 0.0
for c in sorted(closed, key=lambda x: x["in_t"]):
    mv = (c["out"] / c["in"] - 1) * 100
    tot += c["net"]
    print(f"{c['sym']:<14}{c['in_t']:<10}{c['out_t']:<10}{c['in']:>9.2f}{c['out']:>9.2f}"
          f"{mv:>+8.2f}{c['net']:>11,.0f}  {c['why']}")
for p in open_:
    mv = (p["last"] / p["in"] - 1) * 100
    print(f"{p['sym']:<14}{p['in_t']:<10}{'—':<10}{p['in']:>9.2f}{p['last']:>9.2f}"
          f"{mv:>+8.2f}{'':>11}  open at 09:30")
print(f"\nclosed {len(closed)} | open {len(open_)} | realised Rs {tot:,.0f}")
