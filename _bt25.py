"""True replay of 25-Sep through the CURRENT engine, leverage rule included."""
import json, sys, importlib
sys.path.insert(0, ".")
DAY = sys.argv[1] if len(sys.argv) > 1 else "20260925"

import leverage as LV
import live_shadow as LS
import paper_live as PL
import funnel as FN

# seed the day's leverage from the audit, through the shipped rule
aud = json.load(open("logs/LEVERAGE_AUDIT.json", encoding="utf-8"))
cache = {s: LV.combine(r["api_lev"], None) for s, r in aud["rows"].items()}
json.dump(cache, open(f"logs/LEVERAGE_{DAY}.json", "w"))
LV._load(DAY)
print(f"leverage cache: {len(cache)} names, "
      f"{sum(1 for v in cache.values() if v <= 1.0)} at 1x")

warm = LS.load_warm(LS._prev_session_dir(DAY), want_day=None)
pairs, _ = LS.build(DAY, warm)
tape, d0 = {}, {}
for s, (bars, n) in pairs.items():
    bb = [x for x in bars[:n] if x.get("c")]
    tb = [x for x in bars[n:] if x.get("c") and PL.OPEN_T <= x["hhmm"] <= "15:30:00"]
    if len(tb) < 3:
        continue
    tape[s] = bb + tb
    d0[s] = len(bb)
avail, _, _ = FN.build(DAY, tape)
pins = PL.nodip_watchlist(DAY)
if pins:
    avail = {s: t for s, t in avail.items() if s in pins}
fun = {s: tape[s] for s in avail if s in tape}
fd0 = {s: d0[s] for s in fun}
print(f"universe: {len(tape)} with bars | {len(pins)} no-dip | {len(fun)} in funnel")

closed, live = PL.run_book(fun, fd0, avail, "09:15:00", "15:30:00")
allt = closed + live
net = sum(t.get("net", 0) or 0 for t in allt)
print(f"\nREPLAY: {len(allt)} trades | net Rs {net:,.2f}")
json.dump(allt, open(f"logs/BT_{DAY}.json", "w"), indent=1, default=str)
print("wrote", f"logs/BT_{DAY}.json")
