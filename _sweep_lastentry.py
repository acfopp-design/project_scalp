"""25-Sep ONLY. Does trading past 14:00 help? Sri's eye says yes (4 trades, +3.2% avg).

NOTE the cache trap: eye_strategy._cache is keyed (sym, nbars) and is NOT aware
of module-level knobs. It MUST be cleared between values or every run after the
first silently replays the first run's decisions.
"""
import json, sys
sys.path.insert(0, ".")
DAY = "20260925"

import leverage as LV, live_shadow as LS, paper_live as PL, funnel as FN
import eye_strategy as ES

aud = json.load(open("logs/LEVERAGE_AUDIT.json", encoding="utf-8"))
cache = {s: LV.combine(r["api_lev"], None) for s, r in aud["rows"].items()}
json.dump(cache, open("logs/LEVERAGE_%s.json" % DAY, "w"))
LV._load(DAY)

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

BASE = ES.LAST_ENTRY
print("baseline LAST_ENTRY =", BASE, "| square-off", PL.SQUARE_OFF)
print("%-10s %7s %10s %10s %9s %8s" % ("LAST_ENTRY", "trades", "net Rs", "delta", "late trd", "late Rs"))
ref = None
for val in ("1400", "1415", "1430", "1445", "1500"):
    ES.LAST_ENTRY = val
    ES._cache.clear()                      # <-- the trap. Do not remove.
    closed, live = PL.run_book(fun, fd0, avail, "09:15:00", "15:30:00")
    allt = closed + live
    net = sum(t.get("net", 0) or 0 for t in allt)
    late = [t for t in allt if t["in_t"] >= "14:00:00"]
    if ref is None:
        ref = net
    print("%-10s %7d %10s %10s %9d %8s" % (
        val, len(allt), "{:,.0f}".format(net), "{:+,.0f}".format(net - ref),
        len(late), "{:+,.0f}".format(sum(t.get("net", 0) or 0 for t in late))))
    json.dump(allt, open("logs/BT_LE%s_%s.json" % (val, DAY), "w"), indent=1, default=str)
ES.LAST_ENTRY = BASE
ES._cache.clear()
print("\nrestored LAST_ENTRY =", ES.LAST_ENTRY)
