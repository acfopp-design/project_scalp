"""Is TRAIL_PCT=1.2 a real edge or fitted to 25-Sep? Sweep it across 4 days."""
import json, sys
sys.path.insert(0, ".")
import leverage as LV, live_shadow as LS, paper_live as PL, funnel as FN
import eye_strategy as ES

DAYS = sys.argv[1:] or ["20260925"]
PCTS = [0.8, 1.0, 1.2, 1.5, 2.0]
aud = json.load(open("logs/LEVERAGE_AUDIT.json", encoding="utf-8"))
LEVC = {s: LV.combine(r["api_lev"], None) for s, r in aud["rows"].items()}


def day_tape(day):
    json.dump(LEVC, open("logs/LEVERAGE_%s.json" % day, "w"))
    LV._load(day)
    warm = LS.load_warm(LS._prev_session_dir(day), want_day=None)
    pairs, _ = LS.build(day, warm)
    tape, d0 = {}, {}
    for s, (bars, n) in pairs.items():
        bb = [x for x in bars[:n] if x.get("c")]
        tb = [x for x in bars[n:] if x.get("c") and PL.OPEN_T <= x["hhmm"] <= "15:30:00"]
        if len(tb) < 3:
            continue
        tape[s], d0[s] = bb + tb, len(bb)
    avail, _, _ = FN.build(day, tape)
    pins = PL.nodip_watchlist(day)
    if pins:
        avail = {s: t for s, t in avail.items() if s in pins}
    fun = {s: tape[s] for s in avail if s in tape}
    return fun, {s: d0[s] for s in fun}, avail


BASE = PL.TRAIL_PCT
res = {}
for day in DAYS:
    try:
        fun, fd0, avail = day_tape(day)
    except Exception as e:
        print(day, "SKIP", type(e).__name__, e, flush=True)
        continue
    if not fun:
        print(day, "SKIP no tape", flush=True)
        continue
    res[day] = {}
    for p in PCTS:
        PL.TRAIL_PCT = p
        ES._cache.clear()
        closed, live = PL.run_book(fun, fd0, avail, "09:15:00", "15:30:00")
        allt = closed + live
        res[day][p] = (len(allt), sum(t.get("net", 0) or 0 for t in allt))
        print("  %s pct=%.1f -> %d trades, Rs %.0f" % (day, p, *res[day][p]), flush=True)
PL.TRAIL_PCT = BASE
ES._cache.clear()

print("\n%-10s %s" % ("day", "".join("%12s" % ("pct " + str(p)) for p in PCTS)), flush=True)
for day in res:
    best = max(res[day], key=lambda p: res[day][p][1])
    print("%-10s %s   best=%.1f" % (
        day, "".join("%12s" % "{:,.0f}".format(res[day][p][1]) for p in PCTS), best), flush=True)
print("%-10s %s" % ("TOTAL", "".join(
    "%12s" % "{:,.0f}".format(sum(res[d][p][1] for d in res)) for p in PCTS)), flush=True)
json.dump({d: {str(p): v for p, v in r.items()} for d, r in res.items()},
          open("logs/SWEEP_TRAIL_%s.json"%"_".join(DAYS), "w"), indent=1)
print("\nDONE", flush=True)
