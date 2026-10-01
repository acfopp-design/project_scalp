"""25-Sep ONLY. Does arming the trail on 'ever been in profit' beat the legacy
'still in profit' guard? Also sweeps TRAIL_PCT so we can see if the win is the
arming rule or just a tighter trail."""
import json, sys
sys.path.insert(0, ".")
DAY = "20260925"
import leverage as LV, live_shadow as LS, paper_live as PL, funnel as FN
import eye_strategy as ES

aud = json.load(open("logs/LEVERAGE_AUDIT.json", encoding="utf-8"))
json.dump({s: LV.combine(r["api_lev"], None) for s, r in aud["rows"].items()},
          open("logs/LEVERAGE_%s.json" % DAY, "w"))
LV._load(DAY)
warm = LS.load_warm(LS._prev_session_dir(DAY), want_day=None)
pairs, _ = LS.build(DAY, warm)
tape, d0 = {}, {}
for s, (bars, n) in pairs.items():
    bb = [x for x in bars[:n] if x.get("c")]
    tb = [x for x in bars[n:] if x.get("c") and PL.OPEN_T <= x["hhmm"] <= "15:30:00"]
    if len(tb) < 3:
        continue
    tape[s], d0[s] = bb + tb, len(bb)
avail, _, _ = FN.build(DAY, tape)
pins = PL.nodip_watchlist(DAY)
if pins:
    avail = {s: t for s, t in avail.items() if s in pins}
fun = {s: tape[s] for s in avail if s in tape}
fd0 = {s: d0[s] for s in fun}

B_ARM, B_PCT = PL.TRAIL_ARM, PL.TRAIL_PCT
ref = None
print("%-7s %-6s %7s %8s %9s %8s %8s %9s" %
      ("arm", "pct", "trades", "net Rs", "delta", "trail n", "winners", "worst"))
for arm in ("close", "peak"):
    for pct in (0.8, 1.0, 1.2, 1.5, 2.0):
        PL.TRAIL_ARM, PL.TRAIL_PCT = arm, pct
        ES._cache.clear()
        closed, live = PL.run_book(fun, fd0, avail, "09:15:00", "15:30:00")
        allt = closed + live
        net = sum(t.get("net", 0) or 0 for t in allt)
        if ref is None:
            ref = net
        ntr = sum(1 for t in allt if t["why"] == "trail")
        win = sum(1 for t in allt if (t.get("net") or 0) > 0)
        worst = min((t.get("net") or 0) for t in allt) if allt else 0
        print("%-7s %-6s %7d %8s %9s %8d %8s %9s" % (
            arm, pct, len(allt), "{:,.0f}".format(net), "{:+,.0f}".format(net - ref),
            ntr, "%d/%d" % (win, len(allt)), "{:,.0f}".format(worst)))
        if arm == "peak" and pct == 1.2:
            json.dump(allt, open("logs/BT_TRAILPEAK_%s.json" % DAY, "w"), indent=1, default=str)
PL.TRAIL_ARM, PL.TRAIL_PCT = B_ARM, B_PCT
ES._cache.clear()
print("\nrestored:", PL.TRAIL_ARM, PL.TRAIL_PCT)
