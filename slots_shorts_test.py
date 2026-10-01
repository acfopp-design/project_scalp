"""slots_shorts_test.py -- 23-Sep. Two questions on 22-Sep's real tape:
   1. do the new shorts actually earn their keep?
   2. is 2 slots still right now that both directions are available?
Drives paper_live.run_book itself, so this is the live engine, not a model of it.
"""
import sys, json
from pathlib import Path
HERE = Path(__file__).parent; sys.path.insert(0, str(HERE))
import funnel as FN, live_shadow as LS
import paper_live as PL, eye_strategy as EYE

DAY, UPTO = "20260922", "15:15:00"
warm = LS.load_warm(LS._prev_session_dir(DAY))
pairs, _t = LS.build(DAY, warm)
tape, d0 = {}, {}
for s, (bars, n) in pairs.items():
    bb = [x for x in bars[:n] if x.get("c")]
    tb = [x for x in bars[n:] if x.get("c") and PL.OPEN_T <= x["hhmm"] <= UPTO]
    if len(tb) < 3: continue
    tape[s] = bb + tb; d0[s] = len(bb)
avail, _a, _b = FN.build(DAY, tape)
fun = {s: tape[s] for s in avail if s in tape}
fd0 = {s: d0[s] for s in fun}
print(f"warm {len(warm)} | tape {len(tape)} | funnel {len(fun)}")

def run(slots, shorts):
    EYE.SHORTS = shorts; EYE._cache.clear()
    PL.SLOTS = slots
    closed, live = PL.run_book(fun, fd0, avail, PL.OPEN_T, UPTO)
    allp = closed + [dict(p, out=p["last"], out_t=UPTO, why="open",
                          chg=0.0, net=0.0) for p in live]
    net = 0.0; ns = nl = 0; sn = 0.0
    for c in closed:
        net += c["net"]
        if c.get("side", 1) == -1: ns += 1; sn += c["net"]
        else: nl += 1
    wins = sum(1 for c in closed if c["net"] > 0)
    return dict(slots=slots, shorts=shorts, n=len(closed), longs=nl, shorts_n=ns,
                short_net=sn, net=net, wins=wins,
                win=round(wins*100.0/len(closed)) if closed else 0,
                pct=round(net/PL.CAPITAL*100, 2))

print(f"{'slots':>5} {'shorts':>7} {'trades':>7} {'L':>4} {'S':>4} "
      f"{'shortRs':>9} {'netRs':>9} {'win%':>5} {'%cap':>7}")
for shorts in (False, True):
    for slots in (1, 2, 3, 4, 5, 6):
        r = run(slots, shorts)
        print(f"{r['slots']:>5} {str(r['shorts']):>7} {r['n']:>7} {r['longs']:>4} "
              f"{r['shorts_n']:>4} {r['short_net']:>9,.0f} {r['net']:>9,.0f} "
              f"{r['win']:>5} {r['pct']:>7}")
